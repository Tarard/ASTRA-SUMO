"""Materialize a geometry-preserving Hamburg three-node TLS candidate.

The old native-teacher replay rewrote several OSM edge chains into direct
connections.  That made the graph look routeable while the rendered lane
surfaces overlapped.  This materializer keeps the accepted V10 geometry,
demotes inherited OSM signal heads, and binds the official movement paths to
the closest *existing* source-network junction transition.  A shared SUMO
controller may span several OSM sub-junctions; no lane or edge is warped.

The output is deliberately a static topology candidate.  Each controller gets
an explicit all-red structural program.  Historical Saturday states are a
separate replay stage and are never inferred here.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
import json
import math
from pathlib import Path
import xml.etree.ElementTree as ET

from .artifact_io import write_json_atomic, write_text_atomic
from .candidate_contracts import file_sha256
from .command_runner import run_command
from torii_sumo.intersection.road_sumo_materialization_gate import (
    gate_road_sumo_connection_intents_for_materialization,
)
from .sumo_commands import run_sumo_load_audit
from .surface_overlap_audit import (
    audit_sumo_lane_junction_surface_overlaps,
    compare_sumo_surface_overlap_reports,
)


CORRIDOR_TLS_MATERIALIZER_SCHEMA = "torii.hamburg-sandtorkai-corridor-tls-materializer/v1"
CONTROLLER_BY_NODE = {"228": "HH_0228", "2421": "HH_2421"}
CONTROLLED_NODE_IDS = ("228", "2421")
REQUIRED_2394_CONTROLLER = "HH_2394"
PATH_NODE_ALIASES = {"0228": "228", "228": "228", "2421": "2421"}


class HamburgCorridorTlsMaterializationError(ValueError):
    """Raised when the corridor candidate cannot be emitted fail-closed."""


def materialize_hamburg_sandtorkai_corridor_tls_candidate(
    *,
    source_net_file: Path,
    movement_paths_file: Path,
    movement_endpoints_file: Path,
    expected_source_sha256: str,
    expected_movement_paths_sha256: str,
    expected_movement_endpoints_sha256: str,
    output_dir: Path,
    road_sumo_binding_file: Path | None = None,
    expected_road_sumo_binding_sha256: str | None = None,
    netconvert_binary: str = "netconvert",
    sumo_binary: str = "sumo",
    timeout_seconds: float = 240.0,
    selection_radius_m: float = 80.0,
    command_runner: Callable[..., object] = run_command,
) -> dict[str, object]:
    """Build the geometry-preserving three-controller corridor candidate.

    ``movement_paths_file`` is the official MAP/OCIT-derived lane-path
    evidence.  ``movement_endpoints_file`` supplies the already audited
    official link-index grouping.  ``road_sumo_binding_file`` is the frozen
    road-arm intent artifact for the exact source SUMO snapshot.  The latter
    is a mandatory pre-materialization gate: every planned lane connection
    must map to exactly one ready intent and retain its MAP/OCIT/owner evidence.
    """

    source = Path(source_net_file).resolve(strict=True)
    paths_file = Path(movement_paths_file).resolve(strict=True)
    endpoints_file = Path(movement_endpoints_file).resolve(strict=True)
    if road_sumo_binding_file is None or not expected_road_sumo_binding_sha256:
        raise HamburgCorridorTlsMaterializationError(
            "road_sumo_binding_file and expected_road_sumo_binding_sha256 are required before "
            "any corridor lane connection is materialized"
        )
    binding_file = Path(road_sumo_binding_file).resolve(strict=True)
    destination = Path(output_dir).resolve()
    destination.mkdir(parents=True, exist_ok=True)
    if not math.isfinite(selection_radius_m) or selection_radius_m <= 0:
        raise HamburgCorridorTlsMaterializationError("selection_radius_m must be positive")
    _require_hash(source, expected_source_sha256, "source network")
    _require_hash(paths_file, expected_movement_paths_sha256, "movement paths")
    _require_hash(endpoints_file, expected_movement_endpoints_sha256, "movement endpoints")
    _require_hash(binding_file, expected_road_sumo_binding_sha256, "road SUMO binding")
    if destination in {source.parent, paths_file.parent, endpoints_file.parent, binding_file.parent}:
        raise HamburgCorridorTlsMaterializationError(
            "output_dir must be separate from every evidence-file directory"
        )

    paths = _load_json(paths_file, "movement paths")
    endpoints = _load_json(endpoints_file, "movement endpoints")
    road_sumo_binding = _load_json(binding_file, "road SUMO binding")
    if not isinstance(paths, list) or not isinstance(endpoints, Mapping):
        raise HamburgCorridorTlsMaterializationError("movement evidence has an unexpected shape")
    if not isinstance(road_sumo_binding, Mapping):
        raise HamburgCorridorTlsMaterializationError("road SUMO binding has an unexpected shape")
    endpoint_rows = endpoints.get("movements")
    if not isinstance(endpoint_rows, list):
        raise HamburgCorridorTlsMaterializationError("movement endpoints has no movements list")
    endpoint_by_key: dict[tuple[str, str], object] = {}
    for row in endpoint_rows:
        if not isinstance(row, Mapping):
            continue
        raw_node = str(row.get("node_id", ""))
        canonical_node = PATH_NODE_ALIASES.get(raw_node, raw_node)
        endpoint_by_key[(raw_node, str(row.get("connection_id", "")))] = row
        endpoint_by_key[(canonical_node, str(row.get("connection_id", "")))] = row

    source_plain = destination / "source_plain"
    source_plain.mkdir(parents=True, exist_ok=True)
    export_prefix = source_plain / "source"
    export_command = [
        str(netconvert_binary),
        "--sumo-net-file",
        str(source),
        "--plain-output-prefix",
        str(export_prefix),
    ]
    export_result = command_runner(export_command, cwd=source_plain, timeout_seconds=timeout_seconds)
    export_report = _result_dict(export_result)
    if export_report.get("status") != "pass" or export_report.get("returncode") != 0:
        raise HamburgCorridorTlsMaterializationError(
            "source plain export failed: "
            + str(export_report.get("stderr") or export_report.get("error") or export_report)
        )
    plain = _resolve_plain(source_plain)

    selection = _select_official_transitions(
        plain["nodes"],
        plain["edges"],
        plain["connections"],
        paths,
        endpoint_by_key,
        selection_radius_m=selection_radius_m,
    )
    road_sumo_materialization_gate = _road_sumo_materialization_gate(
        road_sumo_binding=road_sumo_binding,
        source_sumo_sha256=file_sha256(source),
        selection=selection,
        movement_paths_sha256=file_sha256(paths_file),
        movement_endpoints_sha256=file_sha256(endpoints_file),
    )
    if road_sumo_materialization_gate["status"] != "pass":
        raise HamburgCorridorTlsMaterializationError(
            "road SUMO connection-intent gate blocked materialization: "
            + "; ".join(
                str(reason)
                for reason in road_sumo_materialization_gate.get("blocking_reasons", ())
            )
        )
    patch_report = _patch_plain(
        nodes_path=plain["nodes"],
        connections_path=plain["connections"],
        tllogic_path=plain["tllogic"],
        selection=selection,
    )

    output_net = destination / "hamburg_sandtorkai_corridor_tls_candidate.net.xml"
    command = [
        str(netconvert_binary),
        "--node-files",
        str(plain["nodes"]),
        "--edge-files",
        str(plain["edges"]),
        "--connection-files",
        str(plain["connections"]),
        "--tllogic-files",
        str(plain["tllogic"]),
        "--type-files",
        str(plain["types"]),
        "--no-turnarounds",
        "--offset.disable-normalization",
        "true",
        "--output-file",
        str(output_net),
    ]
    result = command_runner(command, cwd=destination, timeout_seconds=timeout_seconds)
    command_report = _result_dict(result)
    if command_report.get("status") != "pass" or command_report.get("returncode") != 0:
        raise HamburgCorridorTlsMaterializationError(
            "corridor netconvert failed: "
            + str(command_report.get("stderr") or command_report.get("error") or command_report)
        )
    if not output_net.is_file():
        raise HamburgCorridorTlsMaterializationError("netconvert reported success without output net")
    _canonicalize_net_file(output_net)

    network_audit = _audit_network(output_net, selection)
    load_audit = run_sumo_load_audit(
        net_file=output_net,
        output_dir=destination / "sumo_load",
        sumo_binary=sumo_binary,
        timeout_seconds=timeout_seconds,
        command_runner=command_runner,
    )
    surface_audit = audit_sumo_lane_junction_surface_overlaps(
        output_net,
        report_file=destination / "surface_overlap" / "candidate_surface_overlap_audit.json",
    )
    baseline_surface_audit = audit_sumo_lane_junction_surface_overlaps(
        source,
        report_file=destination / "surface_overlap" / "baseline_surface_overlap_audit.json",
    )
    focus_ids = sorted(
        set(CONTROLLED_NODE_IDS)
        | {
            str(row["owner_id"])
            for row in selection["bindings"]
            if row.get("owner_id")
        }
        | {str(row) for row in selection["existing_2394_owner_ids"]}
    )
    surface_comparison = compare_sumo_surface_overlap_reports(
        baseline_surface_audit,
        surface_audit,
        focus_junction_ids=focus_ids,
        report_file=destination / "surface_overlap" / "bounded_surface_overlap_comparison.json",
    )
    source_immutable = file_sha256(source) == expected_source_sha256
    status = (
        "review_ready"
        if network_audit["status"] == "pass"
        and load_audit.get("status") == "pass"
        and surface_comparison.get("status") == "pass"
        and source_immutable
        else "blocked"
    )
    manifest_file = destination / "hamburg_sandtorkai_corridor_tls_candidate.manifest.json"
    manifest: dict[str, object] = {
        "schema_id": CORRIDOR_TLS_MATERIALIZER_SCHEMA,
        "status": status,
        "claim_status": "official-static-topology-candidate",
        "automatic_promotion_gate": "blocked",
        "source": {
            "path": str(source),
            "sha256_expected": expected_source_sha256,
            "sha256_after": file_sha256(source),
            "immutable": source_immutable,
        },
        "evidence": {
            "movement_paths": {"path": str(paths_file), "sha256": file_sha256(paths_file)},
            "movement_endpoints": {"path": str(endpoints_file), "sha256": file_sha256(endpoints_file)},
            "road_sumo_binding": {"path": str(binding_file), "sha256": file_sha256(binding_file)},
        },
        "road_sumo_materialization_gate": road_sumo_materialization_gate,
        "source_plain_export": {"command": export_command, "result": export_report},
        "selection": selection,
        "plain_patch": patch_report,
        "netconvert": {"command": command, "result": command_report},
        "network_audit": network_audit,
        "sumo_load_audit": load_audit,
        "surface_overlap_audit": surface_audit,
        "surface_overlap_comparison": surface_comparison,
        "materialization": {
            "status": status,
            "controller_ids": ["HH_0228", "HH_2421", "HH_2394"],
            "legacy_tls_policy": "demote every inherited source TLS connection; install only the three shared official controllers",
            "geometry_policy": "preserve source V10 edge/lane geometry; no edge or lane warp; shared controllers span source OSM sub-junctions",
            "historical_two_hour_replay": "not_run",
            "operational_signal_timing": "blocked",
        },
        "artifacts": {"manifest": str(manifest_file), "network": str(output_net)},
    }
    write_json_atomic(manifest_file, manifest, sort_keys=True)
    return manifest


def _require_hash(path: Path, expected: str, label: str) -> None:
    if not expected or file_sha256(path).lower() != expected.lower():
        raise HamburgCorridorTlsMaterializationError(
            f"{label} SHA-256 mismatch: expected {expected}, actual {file_sha256(path)}"
        )


def _load_json(path: Path, label: str) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise HamburgCorridorTlsMaterializationError(f"cannot read {label}: {exc}") from exc


def _result_dict(result: object) -> dict[str, object]:
    if hasattr(result, "to_dict"):
        return result.to_dict()  # type: ignore[no-any-return]
    return dict(result)  # type: ignore[arg-type]


def _resolve_plain(directory: Path) -> dict[str, Path]:
    result = {
        "nodes": next(directory.glob("*.nod.xml"), None),
        "edges": next(directory.glob("*.edg.xml"), None),
        "connections": next(directory.glob("*.con.xml"), None),
        "tllogic": next(directory.glob("*.tll.xml"), None),
        "types": next(directory.glob("*.typ.xml"), None),
    }
    missing = sorted(key for key, path in result.items() if path is None)
    if missing:
        raise HamburgCorridorTlsMaterializationError(f"plain export missing: {missing}")
    return {key: path.resolve(strict=True) for key, path in result.items() if path is not None}


def _select_official_transitions(
    nodes_path: Path,
    edges_path: Path,
    connections_path: Path,
    paths: list[object],
    endpoint_by_key: Mapping[tuple[str, str], object],
    *,
    selection_radius_m: float,
) -> dict[str, object]:
    nodes_root = ET.parse(nodes_path).getroot()
    edges_root = ET.parse(edges_path).getroot()
    connections_root = ET.parse(connections_path).getroot()
    coords = {
        str(node.attrib.get("id", "")): (
            float(node.attrib["x"]),
            float(node.attrib["y"]),
        )
        for node in nodes_root.findall("node")
        if node.attrib.get("x") is not None and node.attrib.get("y") is not None
    }
    edge_nodes = {
        str(edge.attrib.get("id", "")): (
            str(edge.attrib.get("from", "")),
            str(edge.attrib.get("to", "")),
        )
        for edge in edges_root.findall("edge")
        if edge.attrib.get("function") != "internal"
    }
    source_connections = {
        _connection_key(element): element
        for element in connections_root.findall("connection")
    }
    centers = _read_net_centers(nodes_root)
    rows: list[dict[str, object]] = []
    missing_rows: list[dict[str, object]] = []
    owner_ids: set[str] = set()
    for item in paths:
        if not isinstance(item, Mapping):
            continue
        raw_node = str(item.get("node_id", ""))
        node = PATH_NODE_ALIASES.get(raw_node, raw_node)
        if node not in CONTROLLER_BY_NODE:
            continue
        key = (raw_node, str(item.get("connection_id", "")))
        endpoint = endpoint_by_key.get(key)
        if not isinstance(endpoint, Mapping):
            raise HamburgCorridorTlsMaterializationError(f"missing movement endpoint: {key}")
        link_index = endpoint.get("sumo_link_index")
        if link_index is None:
            raise HamburgCorridorTlsMaterializationError(f"movement has no link index: {key}")
        lane_ids = item.get("lane_ids")
        if not isinstance(lane_ids, list) or len(lane_ids) < 2:
            raise HamburgCorridorTlsMaterializationError(f"movement has no path transitions: {key}")
        candidates: list[tuple[float, tuple[str, int, str, int], str | None, bool]] = []
        for left, right in zip(lane_ids, lane_ids[1:]):
            left_edge, left_lane = _split_lane_id(str(left))
            right_edge, right_lane = _split_lane_id(str(right))
            transition_key = (left_edge, left_lane, right_edge, right_lane)
            element = source_connections.get(transition_key)
            owner = _owner_from_connection(element) if element is not None else None
            if owner is None:
                left_nodes = edge_nodes.get(left_edge)
                right_nodes = edge_nodes.get(right_edge)
                if left_nodes and right_nodes and left_nodes[1] == right_nodes[0]:
                    owner = left_nodes[1]
            distance = _distance_to_center(owner, coords, centers.get(node))
            candidates.append((distance, transition_key, owner, element is None))
        candidates.sort(key=lambda value: (value[0], value[1]))
        distance, transition_key, owner, missing = candidates[0]
        if owner is None or not math.isfinite(distance) or distance > selection_radius_m:
            raise HamburgCorridorTlsMaterializationError(
                f"movement {key} has no source transition within {selection_radius_m:g}m"
            )
        row = {
            "node_id": raw_node,
            "controller_id": CONTROLLER_BY_NODE[node],
            "connection_id": str(item.get("connection_id", "")),
            "link_index": int(link_index),
            "from_edge": transition_key[0],
            "from_lane": transition_key[1],
            "to_edge": transition_key[2],
            "to_lane": transition_key[3],
            "owner_id": owner,
            "distance_to_official_center_m": round(distance, 6),
            "source_transition_missing": missing,
            "selection_policy": "nearest source junction transition to official preset center",
        }
        rows.append(row)
        owner_ids.add(owner)
        if missing:
            missing_rows.append(row)
    if len(rows) != 25:
        raise HamburgCorridorTlsMaterializationError(
            f"expected 25 official 0228/2421 movement bindings, got {len(rows)}"
        )
    for controller in CONTROLLER_BY_NODE.values():
        links = {int(row["link_index"]) for row in rows if row["controller_id"] == controller}
        expected = set(range(9 if controller == "HH_0228" else 7))
        if links != expected:
            raise HamburgCorridorTlsMaterializationError(
                f"{controller} link indices are {sorted(links)}, expected {sorted(expected)}"
            )
    existing_2394_owner_ids = {
        str(element.attrib.get("via", "")).split(":", 1)[1].rsplit("_", 2)[0]
        for element in connections_root.findall("connection")
        if element.attrib.get("tl") == REQUIRED_2394_CONTROLLER
        and element.attrib.get("via", "").startswith(":")
    }
    return {
        "status": "pass",
        "bindings": rows,
        "missing_transition_count": len(missing_rows),
        "missing_transitions": missing_rows,
        "controlled_owner_count": len(owner_ids),
        "controlled_owner_ids": sorted(owner_ids),
        "existing_2394_owner_ids": sorted(existing_2394_owner_ids),
    }


def _road_sumo_materialization_gate(
    *,
    road_sumo_binding: Mapping[str, object],
    source_sumo_sha256: str,
    selection: Mapping[str, object],
    movement_paths_sha256: str,
    movement_endpoints_sha256: str,
) -> dict[str, object]:
    """Turn the already selected official lane paths into gate input rows.

    The materializer does not derive a lane choice here: ``selection`` was
    produced from the frozen MAP/OCIT lane-path and endpoint inputs above.
    This helper merely makes the evidence categories explicit and verifies that
    each directed source edge pair belongs to one semantic road-arm intent.
    """

    planned: list[dict[str, object]] = []
    bindings = selection.get("bindings")
    if not isinstance(bindings, list):
        raise HamburgCorridorTlsMaterializationError("official transition selection has no bindings")
    for row in bindings:
        if not isinstance(row, Mapping):
            raise HamburgCorridorTlsMaterializationError("official transition selection contains an invalid row")
        node_id = str(row.get("node_id", "")).strip()
        connection_id = str(row.get("connection_id", "")).strip()
        owner_id = str(row.get("owner_id", "")).strip()
        if not node_id or not connection_id or not owner_id:
            raise HamburgCorridorTlsMaterializationError(
                "official transition selection lacks node, connection, or owner identity"
            )
        link_index = row.get("link_index")
        if isinstance(link_index, bool):
            raise HamburgCorridorTlsMaterializationError(
                f"official transition {node_id}/{connection_id} has an invalid link index"
            )
        try:
            normalized_link_index = int(link_index)
        except (TypeError, ValueError) as exc:
            raise HamburgCorridorTlsMaterializationError(
                f"official transition {node_id}/{connection_id} has an invalid link index"
            ) from exc
        planned.append(
            {
                "planned_connection_id": f"official:{node_id}:{connection_id}",
                "from_edge": str(row.get("from_edge", "")),
                "from_lane": row.get("from_lane"),
                "to_edge": str(row.get("to_edge", "")),
                "to_lane": row.get("to_lane"),
                "evidence": {
                    "junction_facing_lane_and_stop_line_binding": [
                        f"movement-path:{movement_paths_sha256}:{node_id}:{connection_id}"
                    ],
                    "physical_connector_geometry_or_map_evidence": [
                        f"movement-path:{movement_paths_sha256}:{node_id}:{connection_id}"
                    ],
                    "legal_turn_or_signal_control_evidence": [
                        f"movement-endpoint:{movement_endpoints_sha256}:{node_id}:{connection_id}"
                    ],
                    "SUMO_owner_and_link_index_decision": [
                        f"sumo-owner:{source_sumo_sha256}:{owner_id}:link-index:{normalized_link_index}"
                    ],
                },
            }
        )
    try:
        return gate_road_sumo_connection_intents_for_materialization(
            road_sumo_binding,
            source_sumo_sha256=source_sumo_sha256,
            planned_lane_connections=planned,
        )
    except ValueError as exc:
        raise HamburgCorridorTlsMaterializationError(
            f"road SUMO connection-intent gate input is invalid: {exc}"
        ) from exc


def _read_net_centers(nodes_root: ET.Element) -> dict[str, tuple[float, float]]:
    centers: dict[str, tuple[float, float]] = {}
    for node in nodes_root.findall("node"):
        node_id = str(node.attrib.get("id", ""))
        if node_id in {"228", "2421", "2394"}:
            centers[node_id] = (float(node.attrib["x"]), float(node.attrib["y"]))
    # PlainXML retains the actual source OSM owners, while the official preset
    # centers are stored in the network's projected frame.  The materializer
    # uses the closest source owner as the selection anchor.  When an official
    # node id is not present, the center is reconstructed from the known
    # nearest legacy owner written by the V10 source export.
    fallback = {"228": (268.72000208846293, 571.050125842914), "2421": (469.01999904960394, 400.6501220250502)}
    for node, xy in fallback.items():
        centers.setdefault(node, xy)
    return centers


def _distance_to_center(
    owner: str | None,
    coords: Mapping[str, tuple[float, float]],
    center: tuple[float, float] | None,
) -> float:
    if owner is None or center is None or owner not in coords:
        return math.inf
    return math.hypot(coords[owner][0] - center[0], coords[owner][1] - center[1])


def _split_lane_id(value: str) -> tuple[str, int]:
    try:
        edge, lane = value.rsplit("_", 1)
        return edge, int(lane)
    except ValueError as exc:
        raise HamburgCorridorTlsMaterializationError(f"invalid lane id: {value}") from exc


def _connection_key(element: ET.Element) -> tuple[str, int, str, int]:
    return (
        str(element.attrib.get("from", "")),
        int(element.attrib.get("fromLane", "-1")),
        str(element.attrib.get("to", "")),
        int(element.attrib.get("toLane", "-1")),
    )


def _owner_from_connection(element: ET.Element | None) -> str | None:
    if element is None:
        return None
    via = element.attrib.get("via", "")
    if not via.startswith(":"):
        return None
    return via[1:].rsplit("_", 2)[0]


def _patch_plain(
    *,
    nodes_path: Path,
    connections_path: Path,
    tllogic_path: Path,
    selection: Mapping[str, object],
) -> dict[str, object]:
    bindings = [row for row in selection["bindings"] if isinstance(row, Mapping)]  # type: ignore[index]
    by_key = {
        (
            str(row["from_edge"]),
            int(row["from_lane"]),
            str(row["to_edge"]),
            int(row["to_lane"]),
        ): row
        for row in bindings
    }
    node_tree = ET.parse(nodes_path)
    node_root = node_tree.getroot()
    signal_owners = {str(row["owner_id"]) for row in bindings}
    existing_2394_owners = {str(owner) for owner in selection["existing_2394_owner_ids"]}  # type: ignore[index]
    existing_2394_owners.update(
        str(node.attrib["id"])
        for node in node_root.findall("node")
        if node.attrib.get("tl") == REQUIRED_2394_CONTROLLER
    )
    for node in node_root.findall("node"):
        if node.attrib.get("type") == "traffic_light" or node.attrib.get("tl"):
            node.set("type", "priority")
            node.attrib.pop("tl", None)
        node_id = str(node.attrib.get("id", ""))
        if node_id in existing_2394_owners:
            node.set("type", "traffic_light")
            node.set("tl", REQUIRED_2394_CONTROLLER)
        elif node_id in signal_owners:
            node.set("type", "traffic_light")
            node.set(
                "tl",
                next(
                    str(row["controller_id"])
                    for row in bindings
                    if str(row["owner_id"]) == node_id
                ),
            )
    ET.indent(node_tree, space="    ")
    write_text_atomic(nodes_path, ET.tostring(node_root, encoding="unicode") + "\n")

    connection_tree = ET.parse(connections_path)
    connection_root = connection_tree.getroot()
    existing = {_connection_key(element): element for element in connection_root.findall("connection")}
    demoted = 0
    assigned = 0
    added = 0
    # SUMO's plain export stores controlled connection declarations in the
    # .tll.xml file, not in .con.xml.  Keep .con.xml as the geometric/base
    # connection graph and add only the four MAP/OCIT transitions absent from
    # the old OSM graph.
    for key, row in sorted(by_key.items()):
        if key not in existing:
            element = ET.Element(
                "connection",
                {
                    "from": key[0],
                    "to": key[2],
                    "fromLane": str(key[1]),
                    "toLane": str(key[3]),
                },
            )
            connection_root.append(element)
            existing[key] = element
            added += 1
        assigned += 1
    ET.indent(connection_tree, space="    ")
    write_text_atomic(connections_path, ET.tostring(connection_root, encoding="unicode") + "\n")

    tllogic_tree = ET.parse(tllogic_path)
    tllogic_root = tllogic_tree.getroot()
    retained_connections = [
        element
        for element in tllogic_root.findall("connection")
        if element.attrib.get("tl") == REQUIRED_2394_CONTROLLER
    ]
    for element in list(tllogic_root.findall("connection")):
        tllogic_root.remove(element)
        if element.attrib.get("tl") != REQUIRED_2394_CONTROLLER:
            demoted += 1
    for logic in list(tllogic_root.findall("tlLogic")):
        if logic.attrib.get("id") != REQUIRED_2394_CONTROLLER:
            tllogic_root.remove(logic)
    for controller, width in (("HH_0228", 9), ("HH_2421", 7)):
        ET.SubElement(
            tllogic_root,
            "tlLogic",
            {
                "id": controller,
                "type": "static",
                "programID": "official-static-structure-placeholder",
                "offset": "0",
            },
        )
        logic = tllogic_root[-1]
        ET.SubElement(logic, "phase", {"duration": "1", "state": "r" * width})
    for row in sorted(bindings, key=lambda item: (str(item["controller_id"]), int(item["link_index"]), str(item["connection_id"]))):
        tllogic_root.append(
            ET.Element(
                "connection",
                {
                    "from": str(row["from_edge"]),
                    "to": str(row["to_edge"]),
                    "fromLane": str(int(row["from_lane"])),
                    "toLane": str(int(row["to_lane"])),
                    "tl": str(row["controller_id"]),
                    "linkIndex": str(int(row["link_index"])),
                },
            )
        )
    for element in retained_connections:
        tllogic_root.append(element)
    ET.indent(tllogic_tree, space="    ")
    write_text_atomic(tllogic_path, ET.tostring(tllogic_root, encoding="unicode") + "\n")
    return {
        "status": "pass",
        "demoted_legacy_connection_count": demoted,
        "assigned_official_connection_count": assigned,
        "added_official_connection_count": added,
        "retained_2394_connection_count": len(retained_connections),
    }


def _audit_network(path: Path, selection: Mapping[str, object]) -> dict[str, object]:
    root = ET.parse(path).getroot()
    errors: list[str] = []
    controllers = {"HH_0228": 9, "HH_2421": 7, "HH_2394": 6}
    counts: dict[str, int] = {}
    indices: dict[str, set[int]] = {}
    for element in root.findall("connection"):
        controller = element.attrib.get("tl")
        if not controller:
            continue
        counts[controller] = counts.get(controller, 0) + 1
        indices.setdefault(controller, set()).add(int(element.attrib.get("linkIndex", "-1")))
    for controller, width in controllers.items():
        if controller not in counts:
            errors.append(f"missing controller {controller}")
        elif not indices.get(controller, set()) == set(range(width)):
            errors.append(f"{controller} indices={sorted(indices[controller])}, expected 0..{width - 1}")
    expected_bindings = {
        (
            str(row["controller_id"]),
            str(row["from_edge"]),
            int(row["from_lane"]),
            str(row["to_edge"]),
            int(row["to_lane"]),
            int(row["link_index"]),
        )
        for row in selection["bindings"]  # type: ignore[index]
        if isinstance(row, Mapping)
    }
    actual_bindings = {
        (
            str(element.attrib.get("tl", "")),
            str(element.attrib.get("from", "")),
            int(element.attrib.get("fromLane", "-1")),
            str(element.attrib.get("to", "")),
            int(element.attrib.get("toLane", "-1")),
            int(element.attrib.get("linkIndex", "-1")),
        )
        for element in root.findall("connection")
        if element.attrib.get("tl") in {"HH_0228", "HH_2421"}
    }
    if not expected_bindings <= actual_bindings:
        errors.append(
            f"official movement bindings missing: expected={len(expected_bindings)}, actual={len(actual_bindings)}"
        )
    tllogic_ids = {logic.attrib.get("id") for logic in root.findall("tlLogic")}
    if not {"HH_0228", "HH_2421", "HH_2394"} <= tllogic_ids:
        errors.append(f"controller programs={sorted(tllogic_ids)}")
    return {
        "status": "pass" if not errors else "fail",
        "errors": errors,
        "controller_connection_counts": counts,
        "controller_link_indices": {key: sorted(value) for key, value in indices.items()},
        "controller_count": len({key for key in counts if key in controllers}),
        "official_movement_binding_count": len(expected_bindings),
    }


def _canonicalize_net_file(path: Path) -> None:
    tree = ET.parse(path)
    root = tree.getroot()
    ET.indent(tree, space="    ")
    write_text_atomic(
        path,
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        + ET.tostring(root, encoding="unicode")
        + "\n",
    )
