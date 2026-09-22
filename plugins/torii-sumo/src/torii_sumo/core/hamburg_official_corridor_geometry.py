"""Stitch official HH-SIB road axes to official local MAP intersection cells."""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence
import xml.etree.ElementTree as ET

from pyproj import Transformer

from .artifact_io import write_json_atomic, write_text_atomic
from .candidate_contracts import file_sha256
from .command_runner import run_command
from .connection_mode_audit import audit_network_connection_mode
from .sumo_commands import run_sumo_load_audit
from .surface_overlap_audit import audit_sumo_lane_junction_surface_overlaps


CORRIDOR_GEOMETRY_SCHEMA = "torii.hamburg-official-corridor-geometry/v1"
_MOTOR_VCLASSES = "passenger taxi bus coach delivery truck motorcycle emergency"
_HAMBURG_PROJECTION = "+proj=utm +zone=32 +ellps=GRS80 +units=m +no_defs"


class HamburgOfficialCorridorGeometryError(ValueError):
    """Raised when the official corridor geometry cannot be stitched safely."""


def materialize_hamburg_official_corridor_geometry(
    *,
    hh_sib_nodes_file: Path,
    hh_sib_edges_file: Path,
    hh_sib_types_file: Path,
    intersection_sources: Mapping[str, Path],
    output_dir: Path,
    lsa_identity_manifest: Path | None = None,
    netconvert_binary: str = "netconvert",
    sumo_binary: str = "sumo",
    timeout_seconds: float = 240.0,
    command_runner: Callable[..., object] = run_command,
) -> dict[str, Any]:
    """Build a separate 2349/2394/2403 geometry candidate.

    The two available official MAP/OCIT cells provide movement topology for
    2349 and 2394.  2403 is retained as an HH-SIB geometry/control boundary;
    this function intentionally does not invent its missing MAP/OCIT signal
    asset.  ``intersection_sources`` must contain exactly ``2349`` and
    ``2394`` directories with the frozen ``hamburg-official-<id>`` PlainXML.
    """

    sources = _resolve_sources(hh_sib_nodes_file, hh_sib_edges_file, hh_sib_types_file, intersection_sources)
    destination = Path(output_dir).expanduser().resolve()
    destination.mkdir(parents=True, exist_ok=True)
    if any(destination.iterdir()):
        raise HamburgOfficialCorridorGeometryError("output_dir must be empty; choose a new versioned run")

    hh_nodes = ET.parse(sources["hh_nodes"]).getroot()
    hh_edges = ET.parse(sources["hh_edges"]).getroot()
    hh_types = ET.parse(sources["hh_types"]).getroot()
    local = {
        node_id: _load_local(
            node_id,
            {key: sources[f"{node_id}_{key}"] for key in ("nod", "edg", "con", "tll", "typ")},
        )
        for node_id in ("2349", "2394")
    }

    nodes = _build_nodes(hh_nodes, hh_edges, local)
    official_lsa_control_boundary = _official_lsa_control_boundary_evidence(
        nodes,
        lsa_identity_manifest=lsa_identity_manifest,
    )
    edges = _build_edges(hh_edges, local)
    _synthesize_signal_node_shapes(nodes, edges)
    # Re-anchor each official MAP movement curve to the lane endpoints of the
    # stitched network.  The MAP curve is still the geometry evidence, but its
    # original first/last points belong to the replaced local MAP edges.
    connections = _build_connections(local, edges=edges)
    map_fanout_evidence = _official_map_target_fanout_evidence(local)
    tllogics = _build_tllogics(local)
    types = _build_types(hh_types, local)

    plain = {
        "nodes": destination / "hamburg_official_corridor.nod.xml",
        "edges": destination / "hamburg_official_corridor.edg.xml",
        "connections": destination / "hamburg_official_corridor.con.xml",
        "tllogics": destination / "hamburg_official_corridor.tll.xml",
        "types": destination / "hamburg_official_corridor.typ.xml",
    }
    _write_xml(plain["nodes"], "nodes", nodes)
    _write_xml(plain["edges"], "edges", edges)
    _write_xml(plain["connections"], "connections", connections)
    _write_xml(plain["tllogics"], "tlLogics", tllogics)
    _write_xml(plain["types"], "types", types)

    output_net = destination / "hamburg_official_corridor.net.xml"
    command = [
        str(netconvert_binary),
        "--node-files", str(plain["nodes"]),
        "--edge-files", str(plain["edges"]),
        "--connection-files", str(plain["connections"]),
        "--tllogic-files", str(plain["tllogics"]),
        "--type-files", str(plain["types"]),
        "--no-turnarounds",
        # Build the junction polygon from the actual edge endpoints.  The
        # official HH-SIB axis and local MAP cells can meet at a node whose
        # centerline is not the centroid of the generated polygon; the SUMO
        # default then reports a large shape/position mismatch and can make
        # the GUI render a false-looking overlap.  This is deterministic and
        # keeps the geometry evidence in PlainXML rather than requiring a
        # manual NetEdit correction.
        "--junctions.endpoint-shape", "true",
        "--offset.disable-normalization", "true",
        "--output-file", str(output_net),
    ]
    netconvert_passes = [_as_dict(command_runner(command, cwd=destination, timeout_seconds=timeout_seconds))]
    # netconvert trims external lane shapes at the generated junction polygon.
    # A curve anchored to the PlainXML lane endpoint can therefore still miss
    # the actual compiled lane by several metres.  Feed those compiled
    # endpoints back once, then compile the same evidence again.  This keeps
    # the process deterministic and removes the old GUI-only repair step.
    # A generated junction polygon can trim the lane endpoint again on the
    # next compile.  Iterate a small, deterministic number of times until the
    # connection curves and compiled lane endpoints reach a fixed point.
    for _ in range(3):
        if netconvert_passes[-1].get("status") != "pass" or not output_net.is_file():
            break
        compiled_endpoints = _lane_endpoints(ET.parse(output_net).getroot().findall("edge"))
        if not _reanchor_connection_shapes(connections, compiled_endpoints):
            break
        _write_xml(plain["connections"], "connections", connections)
        netconvert_passes.append(_as_dict(command_runner(command, cwd=destination, timeout_seconds=timeout_seconds)))
    netconvert = dict(netconvert_passes[-1])
    netconvert["passes"] = netconvert_passes
    compiled = netconvert.get("status") == "pass" and output_net.is_file()
    projection_metadata = (
        _ensure_network_projection_metadata(output_net)
        if compiled
        else {"status": "not_run", "projection": "EPSG:25832"}
    )
    overlap = (
        audit_sumo_lane_junction_surface_overlaps(
            output_net,
            report_file=destination / "surface_overlap" / "corridor_surface_overlap.json",
        )
        if compiled else {"status": "not_run"}
    )
    load = (
        run_sumo_load_audit(
            net_file=output_net,
            output_dir=destination / "sumo_load",
            sumo_binary=sumo_binary,
            timeout_seconds=timeout_seconds,
            command_runner=command_runner,
        )
        if compiled else {"status": "not_run"}
    )
    connection_mode = (
        audit_network_connection_mode(
            ET.parse(output_net).getroot(),
            junction_ids=("hh-map-2349-core", "hh-map-2394-core", "hh_sib.n.242500071"),
            evidence_justified_target_fanouts=map_fanout_evidence,
        )
        if compiled else {"status": "not_run"}
    )
    structural_connection_ok = (
        compiled
        and overlap.get("status") == "pass"
        and load.get("status") == "pass"
        and connection_mode.get("structural_failure_count", 1) == 0
    )
    source_hashes = {key: file_sha256(path) for key, path in sources.items()}
    source_manifest = {
        key: {"path": str(path), "sha256": source_hashes[key]}
        for key, path in sources.items()
    }
    if lsa_identity_manifest is not None:
        identity_path = Path(lsa_identity_manifest).resolve(strict=True)
        source_manifest["lsa_identity_manifest"] = {
            "path": str(identity_path),
            "sha256": file_sha256(identity_path),
        }
    status = "review_ready" if compiled and overlap.get("status") == "pass" and load.get("status") == "pass" else "blocked"
    manifest_file = destination / "hamburg_official_corridor_geometry.manifest.json"
    manifest: dict[str, Any] = {
        "schema": CORRIDOR_GEOMETRY_SCHEMA,
        "status": status,
        "execution_gate": "pass" if structural_connection_ok else "blocked",
        "execution_gate_reason": (
            "compiled_surface_load_and_structural_connection_checks_pass;"
            " review findings remain non-promoting"
            if structural_connection_ok
            else "compiled structural prerequisites are not all passing"
        ),
        "claim_status": "official-road-and-local-map-geometry-review-candidate",
        "automatic_promotion_gate": "blocked",
        "source": source_manifest,
        "official_lsa_control_boundary": official_lsa_control_boundary,
        "stitch_policy": {
            "removed_hh_sib_names": ["Am Sandtorkai", "Großer Grasbrook"],
            "local_cells": ["2349", "2394"],
            "control_boundary": "2403 remains the official HH-SIB node 242500071 without invented MAP/OCIT",
            "derived_edges": [
                "corridor_2349_2394_east_upstream",
                "corridor_2349_2394_east_downstream",
                "corridor_2349_2394_west_upstream",
                "corridor_2349_2394_west_downstream",
                "bridge_2349_grasbrook_out",
                "bridge_2349_grasbrook_in",
                "bridge_2394_sandtorpark_out",
                "bridge_2394_sandtorpark_in",
                "corridor_2394_2403_east",
                "corridor_2394_2403_west",
            ],
            "lane_transition_nodes": ["corridor_2349_2394_lane_transition"],
            "evidence_policy": "official HH-SIB lane axis plus official local MAP cell; derived boundary edges remain review_required",
        },
        "plainxml": {key: {"path": str(path), "sha256": file_sha256(path)} for key, path in plain.items()},
        "network": {
            "path": str(output_net),
            "sha256": file_sha256(output_net) if output_net.is_file() else None,
            "projection": projection_metadata,
        },
        "netconvert": {"command": command, "result": netconvert},
        "surface_overlap_audit": overlap,
        "sumo_load_audit": load,
        "connection_mode_audit": connection_mode,
        "routeability": {"status": "not_run", "reason": "W4 requires complete signal and detector-stage inputs"},
        "gates": {
            "official_source_hashes": "pass",
            "plainxml": "pass",
            "netconvert": "pass" if compiled else "blocked",
            "surface_overlap": overlap.get("status", "blocked"),
            "sumo_load": load.get("status", "blocked"),
            "connection_mode": connection_mode.get("status", "blocked"),
            "connection_mode_structural_failures": connection_mode.get("structural_failure_count", 0),
            "official_2403_lsa_identity": official_lsa_control_boundary.get("status", "blocked"),
            "2403_signal_assets": "blocked_pending_official_map_ocit",
            "automatic_promotion": "blocked",
        },
        "artifacts": {"network": str(output_net), "manifest": str(manifest_file)},
        "claim_boundary": {
            "proves": [
                "the two available official MAP cells are structurally stitched to the official road axis",
                "the 2403 road/control boundary is retained without invented signal topology",
                "the official 2403 LSA point is compared with, but not used to move, the HH-SIB road boundary node",
                "compiled network loading and surface-overlap evidence for this candidate",
            ],
            "does_not_prove": [
                "2403 lane movements or signal timing",
                "historical replay or detector-fit demand",
                "automatic promotion while any derived bridge or official asset gate is blocked",
            ],
        },
    }
    write_json_atomic(manifest_file, manifest, sort_keys=True)
    return {**manifest, "manifest_file": str(manifest_file)}


def _resolve_sources(
    hh_nodes: Path,
    hh_edges: Path,
    hh_types: Path,
    intersection_sources: Mapping[str, Path],
) -> dict[str, Path]:
    if set(intersection_sources) != {"2349", "2394"}:
        raise HamburgOfficialCorridorGeometryError("intersection_sources must contain exactly 2349 and 2394")
    result = {"hh_nodes": Path(hh_nodes).resolve(strict=True), "hh_edges": Path(hh_edges).resolve(strict=True), "hh_types": Path(hh_types).resolve(strict=True)}
    for node_id, raw_dir in intersection_sources.items():
        directory = Path(raw_dir).resolve(strict=True)
        for suffix in ("nod.xml", "edg.xml", "con.xml", "tll.xml", "typ.xml"):
            result[f"{node_id}_{suffix.split('.')[0]}"] = (directory / f"hamburg-official-{node_id}.{suffix}").resolve(strict=True)
    return result


def _load_local(node_id: str, sources: Mapping[str, Path]) -> dict[str, ET.Element]:
    del node_id
    return {key: ET.parse(sources[key]).getroot() for key in ("nod", "edg", "con", "tll", "typ")}


def _official_lsa_control_boundary_evidence(
    nodes: Sequence[ET.Element],
    *,
    lsa_identity_manifest: Path | None,
) -> dict[str, Any]:
    """Compare the official 2403 signal point with the HH-SIB road node.

    Hamburg's LSA location dataset identifies a signal controller by a point,
    while HH-SIB identifies the road-network junction by the endpoint of its
    road links.  These are related but are not interchangeable coordinates.
    The comparison is therefore evidence-only: it records the offset and
    deliberately never moves the road node or creates a signal movement.
    """

    boundary_node_id = "hh_sib.n.242500071"
    boundary_node = next(
        (node for node in nodes if node.attrib.get("id") == boundary_node_id),
        None,
    )
    if boundary_node is None:
        return {
            "status": "blocked",
            "node_id": "2403",
            "reason": f"official HH-SIB 2403 boundary node {boundary_node_id!r} is missing",
            "geometry_action": "no_boundary_evidence_available",
        }
    try:
        boundary_x = float(boundary_node.attrib["x"])
        boundary_y = float(boundary_node.attrib["y"])
    except (KeyError, TypeError, ValueError):
        return {
            "status": "blocked",
            "node_id": "2403",
            "reason": f"official HH-SIB 2403 boundary node {boundary_node_id!r} has invalid coordinates",
            "geometry_action": "no_boundary_evidence_available",
        }

    base: dict[str, Any] = {
        "node_id": "2403",
        "official_name": "Am Sandtorkai/Osakaallee",
        "hh_sib_boundary_node_id": boundary_node_id,
        "hh_sib_boundary_point": {
            "crs": "EPSG:25832",
            "x": boundary_x,
            "y": boundary_y,
        },
        "geometry_action": "retain_hh_sib_road_boundary_without_signal_point_snap",
    }
    if lsa_identity_manifest is None:
        return {
            **base,
            "status": "not_provided",
            "reason": "official_lsa_identity_manifest_not_supplied",
        }

    identity_path = Path(lsa_identity_manifest).expanduser().resolve(strict=True)
    try:
        payload = json.loads(identity_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise HamburgOfficialCorridorGeometryError(
            f"official LSA identity manifest is not valid JSON: {identity_path}"
        ) from exc
    if not isinstance(payload, Mapping) or payload.get("schema") != "torii.hamburg-lsa-node-identity-evidence/v1":
        raise HamburgOfficialCorridorGeometryError(
            "official LSA identity manifest has an unsupported schema"
        )
    if payload.get("decision") != "pass":
        raise HamburgOfficialCorridorGeometryError(
            "official LSA identity manifest must have decision=pass"
        )
    selections = payload.get("selections")
    if not isinstance(selections, list):
        raise HamburgOfficialCorridorGeometryError(
            "official LSA identity manifest selections must be a list"
        )
    matches = [
        item
        for item in selections
        if isinstance(item, Mapping) and item.get("expected_node_id") == "2403"
    ]
    if len(matches) != 1:
        raise HamburgOfficialCorridorGeometryError(
            "official LSA identity manifest must contain exactly one 2403 selection"
        )
    selection = matches[0]
    selected = selection.get("selected_node")
    if not isinstance(selected, Mapping) or selected.get("node_id") != "2403":
        raise HamburgOfficialCorridorGeometryError(
            "official LSA identity manifest 2403 selection is not uniquely selected"
        )
    official_name = selected.get("official_name")
    point = selected.get("point_geometry")
    coordinates = point.get("coordinates") if isinstance(point, Mapping) else None
    if (
        not isinstance(official_name, str)
        or not isinstance(coordinates, list)
        or len(coordinates) != 2
    ):
        raise HamburgOfficialCorridorGeometryError(
            "official LSA identity manifest 2403 point geometry is incomplete"
        )
    try:
        longitude, latitude = (float(coordinates[0]), float(coordinates[1]))
    except (TypeError, ValueError) as exc:
        raise HamburgOfficialCorridorGeometryError(
            "official LSA identity manifest 2403 point coordinates are not numeric"
        ) from exc
    if not (
        math.isfinite(longitude)
        and math.isfinite(latitude)
        and -180.0 <= longitude <= 180.0
        and -90.0 <= latitude <= 90.0
    ):
        raise HamburgOfficialCorridorGeometryError(
            "official LSA identity manifest 2403 point coordinates are invalid"
        )
    try:
        transformer = Transformer.from_crs("EPSG:4326", "EPSG:25832", always_xy=True)
        official_x, official_y = transformer.transform(longitude, latitude)
    except Exception as exc:  # noqa: BLE001 - pyproj exposes CRS-specific exceptions.
        raise HamburgOfficialCorridorGeometryError(
            "could not project official LSA 2403 point to EPSG:25832"
        ) from exc
    distance_m = math.hypot(official_x - boundary_x, official_y - boundary_y)
    return {
        **base,
        "status": "pass",
        "reason": "official_lsa_identity_compared_without_topology_snap",
        "official_name": official_name,
        "official_lsa_point": {
            "crs": "OGC:CRS84",
            "longitude": longitude,
            "latitude": latitude,
        },
        "official_lsa_point_projected": {
            "crs": "EPSG:25832",
            "x": official_x,
            "y": official_y,
        },
        "distance_m": distance_m,
        "source_manifest": {
            "path": str(identity_path),
            "sha256": file_sha256(identity_path),
        },
    }


def _build_nodes(hh_nodes: ET.Element, hh_edges: ET.Element, local: Mapping[str, Mapping[str, ET.Element]]) -> list[ET.Element]:
    kept_names = {"Osakaallee", "Am Sandtorpark", "Singapurstraße", "Tokiostraße", "Brooktorkai"}
    used = {edge.attrib.get("from") for edge in hh_edges.findall("edge") if _edge_name(edge) in kept_names} | {edge.attrib.get("to") for edge in hh_edges.findall("edge") if _edge_name(edge) in kept_names}
    used.update({"hh_sib.n.242514481", "hh_sib.n.242500071"})
    nodes = [ET.fromstring(ET.tostring(node)) for node in hh_nodes.findall("node") if node.attrib.get("id") in used]
    for node_id in ("2349", "2394"):
        for node in local[node_id]["nod"].findall("node"):
            if node.attrib.get("id") == f"hh-map-{node_id}-a3-boundary" or node.attrib.get("type") == "traffic_light":
                item = ET.fromstring(ET.tostring(node))
                if item.attrib.get("type") == "traffic_light":
                    # The local MAP node shape may include a stale boundary
                    # vertex (2349/2394 currently do).  Keep the node
                    # position and MAP movement evidence, but let SUMO derive
                    # the physical junction polygon from the corrected lane
                    # endpoints instead of reusing that malformed polygon.
                    item.attrib.pop("shape", None)
                nodes.append(item)
    nodes.append(
        ET.Element(
            "node",
            {
                "id": "corridor_2349_2394_lane_transition",
                "x": "565884.00",
                "y": "5933181.00",
                "type": "priority",
            },
        )
    )
    # The official 2403 node is already present in the retained HH-SIB arms.
    return _unique_elements(nodes)


def _build_edges(hh_edges: ET.Element, local: Mapping[str, Mapping[str, ET.Element]]) -> list[ET.Element]:
    kept_names = {"Osakaallee", "Am Sandtorpark", "Singapurstraße", "Tokiostraße", "Brooktorkai"}
    edges = [
        ET.fromstring(ET.tostring(edge))
        for edge in hh_edges.findall("edge")
        if _edge_name(edge) in kept_names
        and not (_edge_name(edge) == "Am Sandtorpark" and ".0_99." in edge.attrib.get("id", ""))
    ]
    for node_id in ("2349", "2394"):
        removed = {f"hh-map-{node_id}-a{arm}-{direction}" for arm in ("1", "2", "3", "4") for direction in ("in", "out")}
        if node_id == "2349":
            removed -= {"hh-map-2349-a3-in", "hh-map-2349-a3-out"}
        for edge in local[node_id]["edg"].findall("edge"):
            if edge.attrib.get("id") not in removed:
                edges.append(ET.fromstring(ET.tostring(edge)))
    # A local MAP cell can carry a truncated lane curve whose endpoint is
    # tens of metres away from the traffic-light node named by the edge
    # topology (2349/a3 is the observed case).  Anchor only that endpoint to
    # the node while preserving the curve and the remote boundary endpoint;
    # otherwise netconvert expands the junction polygon over the side arm.
    _anchor_local_junction_edge_shapes(edges, local)
    edges.extend(_derived_edges(local))
    return _unique_elements(edges)


def _synthesize_signal_node_shapes(nodes: Sequence[ET.Element], edges: Sequence[ET.Element]) -> None:
    """Give rebuilt signal nodes a valid boundary from corrected lane endpoints.

    SUMO's endpoint-shape mode can return a self-intersecting polygon when a
    compound intersection has duplicate or differently ordered MAP endpoint
    samples.  The convex hull of the actual adjacent lane endpoints is a
    deterministic, simple boundary and does not invent movement geometry.
    """

    endpoints_by_node: dict[str, list[tuple[float, float]]] = {}
    for edge in edges:
        lanes = [lane for lane in edge.findall("lane") if lane.attrib.get("shape")]
        if not lanes:
            continue
        for endpoint_attribute, endpoint_index in (("from", 0), ("to", -1)):
            node_id = edge.attrib.get(endpoint_attribute)
            if not node_id:
                continue
            for lane in lanes:
                try:
                    points = _parse_shape(lane.attrib["shape"])
                except (KeyError, ValueError):
                    continue
                if len(points) >= 2:
                    endpoints_by_node.setdefault(node_id, []).append(points[endpoint_index])
    for node in nodes:
        if node.attrib.get("type") != "traffic_light":
            continue
        node_id = node.attrib.get("id", "")
        hull = _convex_hull(endpoints_by_node.get(node_id, ()))
        if len(hull) >= 3:
            node.set("shape", _shape(hull))


def _convex_hull(points: Sequence[tuple[float, float]]) -> list[tuple[float, float]]:
    """Return the monotonic-chain convex hull in counter-clockwise order."""

    unique = sorted(set(points))
    if len(unique) <= 1:
        return unique

    def cross(
        origin: tuple[float, float],
        a: tuple[float, float],
        b: tuple[float, float],
    ) -> float:
        return (a[0] - origin[0]) * (b[1] - origin[1]) - (a[1] - origin[1]) * (b[0] - origin[0])

    lower: list[tuple[float, float]] = []
    for point in unique:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], point) <= 0:
            lower.pop()
        lower.append(point)
    upper: list[tuple[float, float]] = []
    for point in reversed(unique):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], point) <= 0:
            upper.pop()
        upper.append(point)
    return lower[:-1] + upper[:-1]


def _derived_edges(local: Mapping[str, Mapping[str, ET.Element]]) -> list[ET.Element]:
    edge_specs = (
        ("corridor_2349_2394_east_upstream", "hh-map-2349-core", "corridor_2349_2394_lane_transition", 2, _axis("east_1_upstream"), "Am Sandtorkai"),
        ("corridor_2349_2394_east_downstream", "corridor_2349_2394_lane_transition", "hh-map-2394-core", 3, _axis("east_1_downstream"), "Am Sandtorkai"),
        ("corridor_2349_2394_west_upstream", "hh-map-2394-core", "corridor_2349_2394_lane_transition", 1, _axis("west_1_upstream"), "Am Sandtorkai"),
        ("corridor_2349_2394_west_downstream", "corridor_2349_2394_lane_transition", "hh-map-2349-core", 2, _axis("west_1_downstream"), "Am Sandtorkai"),
        ("bridge_2349_grasbrook_out", "hh-map-2349-core", "hh_sib.n.242514481", 1, _axis("grasbrook_out"), "Großer Grasbrook"),
        ("bridge_2349_grasbrook_in", "hh_sib.n.242514481", "hh-map-2349-core", 2, _axis("grasbrook_in"), "Großer Grasbrook"),
        ("bridge_2394_sandtorpark_out", "hh-map-2394-core", "hh_sib.s.717050612f6d38b4.99", 1, _axis("sandtorpark_out"), "Am Sandtorpark"),
        ("bridge_2394_sandtorpark_in", "hh_sib.s.717050612f6d38b4.99", "hh-map-2394-core", 2, _axis("sandtorpark_in"), "Am Sandtorpark"),
        ("corridor_2394_2403_east", "hh-map-2394-core", "hh_sib.n.242500071", 2, _axis("east_2"), "Am Sandtorkai"),
        ("corridor_2394_2403_west", "hh_sib.n.242500071", "hh-map-2394-core", 2, _axis("west_2"), "Am Sandtorkai"),
    )
    return [_make_edge(*spec) for spec in edge_specs]


def _axis(name: str) -> list[tuple[float, float]]:
    axes = {
        "east_1_upstream": [(565820.59, 5933162.89), (565870.76, 5933175.52), (565884.00, 5933181.00)],
        "east_1_downstream": [(565884.00, 5933181.00), (565915.00, 5933188.50), (565945.10, 5933194.10)],
        "west_1_upstream": [(565945.10, 5933194.10), (565915.00, 5933188.50), (565884.00, 5933181.00)],
        "west_1_downstream": [(565884.00, 5933181.00), (565870.76, 5933175.52), (565820.59, 5933162.89)],
        "east_2": [(565945.10, 5933194.10), (565956.45, 5933196.39), (566117.41, 5933264.86)],
        "west_2": [(566117.41, 5933264.86), (565956.45, 5933196.39), (565945.10, 5933194.10)],
        "grasbrook_out": [(565820.59, 5933162.89), (565816.80, 5933160.61), (565839.40, 5932992.98)],
        "grasbrook_in": [(565839.40, 5932992.98), (565816.80, 5933160.61), (565820.59, 5933162.89)],
        "sandtorpark_out": [(565945.10, 5933194.10), (565978.84, 5933091.31)],
        "sandtorpark_in": [(565978.84, 5933091.31), (565945.10, 5933194.10)],
    }
    return axes[name]


def _make_edge(edge_id: str, from_node: str, to_node: str, lanes: int, centerline: Sequence[tuple[float, float]], name: str) -> ET.Element:
    edge = ET.Element("edge", {"id": edge_id, "from": from_node, "to": to_node, "type": "official-map-vehicle-structural", "numLanes": str(lanes), "spreadType": "center", "allow": _MOTOR_VCLASSES})
    for index in range(lanes):
        points = _offset_line(centerline, index, lanes)
        ET.SubElement(edge, "lane", {"index": str(index), "allow": _MOTOR_VCLASSES, "speed": "13.890", "width": "3.200", "shape": _shape(points), "type": "official-map-vehicle-structural"})
    ET.SubElement(edge, "param", {"key": "name", "value": name})
    ET.SubElement(edge, "param", {"key": "torii:geometry_source", "value": "official_hh_sib_axis_boundary_stitch"})
    ET.SubElement(edge, "param", {"key": "torii:automatic_promotion", "value": "blocked_review_required"})
    return edge


def _build_connections(
    local: Mapping[str, Mapping[str, ET.Element]],
    *,
    edges: Sequence[ET.Element] | None = None,
) -> list[ET.Element]:
    replacements = {
        "hh-map-2349-a1-in": "corridor_2349_2394_west_downstream", "hh-map-2349-a1-out": "corridor_2349_2394_east_upstream",
        "hh-map-2349-a2-in": "bridge_2349_grasbrook_in", "hh-map-2349-a2-out": "bridge_2349_grasbrook_out",
        "hh-map-2394-a4-in": "corridor_2349_2394_east_downstream", "hh-map-2394-a4-out": "corridor_2349_2394_west_upstream",
        "hh-map-2394-a2-in": "corridor_2394_2403_west", "hh-map-2394-a2-out": "corridor_2394_2403_east",
        "hh-map-2394-a3-in": "bridge_2394_sandtorpark_in", "hh-map-2394-a3-out": "bridge_2394_sandtorpark_out",
    }
    lane_counts = _lane_counts()
    lane_endpoints = _lane_endpoints(edges) if edges is not None else {}
    result: list[ET.Element] = []
    for node_id in ("2349", "2394"):
        for connection in local[node_id]["con"].findall("connection"):
            item = ET.fromstring(ET.tostring(connection))
            old_from, old_to = item.attrib.get("from"), item.attrib.get("to")
            if old_from in replacements:
                item.set("from", replacements[old_from])
            if old_to in replacements:
                item.set("to", replacements[old_to])
            # Preserve the official MAP connector curve after rebinding its
            # endpoint edge IDs.  Compound approaches such as 2349/2394 use
            # two distinct curves for one-to-many lane splits; dropping them
            # makes netconvert synthesize overlapping internal paths.
            if item.attrib.get("from") in lane_counts and item.attrib.get("fromLane") is not None:
                item.set("fromLane", str(min(int(item.attrib["fromLane"]), lane_counts[item.attrib["from"]] - 1)))
            if item.attrib.get("to") in lane_counts and item.attrib.get("toLane") is not None:
                item.set("toLane", str(min(int(item.attrib["toLane"]), lane_counts[item.attrib["to"]] - 1)))
            if lane_endpoints and item.attrib.get("shape"):
                points = _parse_shape(item.attrib["shape"])
                source = lane_endpoints.get((item.attrib.get("from", ""), int(item.attrib.get("fromLane", "0"))))
                target = lane_endpoints.get((item.attrib.get("to", ""), int(item.attrib.get("toLane", "0"))))
                if len(points) >= 2 and source is not None and target is not None:
                    item.set("shape", _shape(_reanchor_shape(points, source[1], target[0])))
            result.append(item)
    result.extend(
        ET.Element("connection", {"from": "corridor_2349_2394_east_upstream", "to": "corridor_2349_2394_east_downstream", "fromLane": str(lane), "toLane": str(target)})
        for lane, target in ((0, 0), (1, 1), (1, 2))
    )
    result.extend(
        ET.Element("connection", {"from": "corridor_2349_2394_west_upstream", "to": "corridor_2349_2394_west_downstream", "fromLane": "0", "toLane": str(target)})
        for target in (0, 1)
    )
    return result


def _official_map_target_fanout_evidence(
    local: Mapping[str, Mapping[str, ET.Element]],
) -> dict[str, dict[str, Any]]:
    """Derive exact one-to-many lane evidence from the frozen local MAP cells."""

    replacements = {
        "hh-map-2349-a1-in": "corridor_2349_2394_west_downstream",
        "hh-map-2349-a1-out": "corridor_2349_2394_east_upstream",
        "hh-map-2349-a2-in": "bridge_2349_grasbrook_in",
        "hh-map-2349-a2-out": "bridge_2349_grasbrook_out",
        "hh-map-2394-a4-in": "corridor_2349_2394_east_downstream",
        "hh-map-2394-a4-out": "corridor_2349_2394_west_upstream",
        "hh-map-2394-a2-in": "corridor_2394_2403_west",
        "hh-map-2394-a2-out": "corridor_2394_2403_east",
        "hh-map-2394-a3-in": "bridge_2394_sandtorpark_in",
        "hh-map-2394-a3-out": "bridge_2394_sandtorpark_out",
    }
    lane_counts = _lane_counts()
    grouped: dict[str, dict[str, Any]] = {}
    for node_id in ("2349", "2394"):
        for connection in local[node_id]["con"].findall("connection"):
            source = replacements.get(connection.attrib.get("from", ""), connection.attrib.get("from", ""))
            target = replacements.get(connection.attrib.get("to", ""), connection.attrib.get("to", ""))
            source_lane = min(int(connection.attrib.get("fromLane", "0")), lane_counts.get(source, 1) - 1)
            target_lane = min(int(connection.attrib.get("toLane", "0")), lane_counts.get(target, 1) - 1)
            key = f"{source}|{source_lane}|{target}"
            row = grouped.setdefault(
                key,
                {"target_lanes": set(), "source_cell": node_id},
            )
            row["target_lanes"].add(str(target_lane))
    return {
        key: {
            "basis": "official_map_connection_curve",
            "source_cell": value["source_cell"],
            "target_lanes": sorted(value["target_lanes"]),
        }
        for key, value in grouped.items()
        if len(value["target_lanes"]) > 1
    }


def _build_tllogics(local: Mapping[str, Mapping[str, ET.Element]]) -> list[ET.Element]:
    result: list[ET.Element] = []
    replacements = _edge_replacements()
    lane_counts = _lane_counts()
    for node_id in ("2349", "2394"):
        result.extend(ET.fromstring(ET.tostring(item)) for item in local[node_id]["tll"].findall("tlLogic"))
        for connection in local[node_id]["tll"].findall("connection"):
            item = ET.fromstring(ET.tostring(connection))
            for key in ("from", "to"):
                old = item.attrib.get(key)
                if old in replacements:
                    item.set(key, replacements[old])
            item.attrib.pop("shape", None)
            for key in ("from", "to"):
                lane_key = f"{key}Lane"
                edge_id = item.attrib.get(key)
                if edge_id in lane_counts and item.attrib.get(lane_key) is not None:
                    item.set(lane_key, str(min(int(item.attrib[lane_key]), lane_counts[edge_id] - 1)))
            result.append(item)
    return result


def _build_types(hh_types: ET.Element, local: Mapping[str, Mapping[str, ET.Element]]) -> list[ET.Element]:
    items = [ET.fromstring(ET.tostring(item)) for item in hh_types.findall("type")]
    items.extend(ET.fromstring(ET.tostring(item)) for item in local["2349"]["typ"].findall("type") if item.attrib.get("id") not in {x.attrib.get("id") for x in items})
    return items


def _edge_replacements() -> dict[str, str]:
    return {
        "hh-map-2349-a1-in": "corridor_2349_2394_west_downstream", "hh-map-2349-a1-out": "corridor_2349_2394_east_upstream",
        "hh-map-2349-a2-in": "bridge_2349_grasbrook_in", "hh-map-2349-a2-out": "bridge_2349_grasbrook_out",
        "hh-map-2394-a4-in": "corridor_2349_2394_east_downstream", "hh-map-2394-a4-out": "corridor_2349_2394_west_upstream",
        "hh-map-2394-a2-in": "corridor_2394_2403_west", "hh-map-2394-a2-out": "corridor_2394_2403_east",
        "hh-map-2394-a3-in": "bridge_2394_sandtorpark_in", "hh-map-2394-a3-out": "bridge_2394_sandtorpark_out",
    }


def _lane_counts() -> dict[str, int]:
    return {
        "corridor_2349_2394_east_upstream": 2, "corridor_2349_2394_east_downstream": 3,
        "corridor_2349_2394_west_upstream": 1, "corridor_2349_2394_west_downstream": 2,
        "bridge_2349_grasbrook_in": 2, "bridge_2349_grasbrook_out": 1,
        "corridor_2394_2403_east": 2, "corridor_2394_2403_west": 2,
        "bridge_2394_sandtorpark_in": 2, "bridge_2394_sandtorpark_out": 1,
    }


def _offset_line(points: Sequence[tuple[float, float]], index: int, lanes: int) -> list[tuple[float, float]]:
    if lanes == 1:
        return list(points)
    # SUMO spreadType=center: deterministic lane offsets are enough for this
    # derived bridge; local MAP lane geometry remains the movement evidence.
    offset = (index - (lanes - 1) / 2.0) * 3.2
    result: list[tuple[float, float]] = []
    for i, (x, y) in enumerate(points):
        if i == 0:
            dx, dy = points[1][0] - x, points[1][1] - y
        else:
            dx, dy = x - points[i - 1][0], y - points[i - 1][1]
        length = math.hypot(dx, dy) or 1.0
        result.append((x - dy / length * offset, y + dx / length * offset))
    return result


def _anchor_local_junction_edge_shapes(
    edges: Sequence[ET.Element],
    local: Mapping[str, Mapping[str, ET.Element]],
) -> None:
    """Move malformed local MAP edge endpoints onto their traffic-light node.

    The correction is applied as a linear endpoint blend, so the far end of
    a boundary arm and all intermediate MAP control points remain unchanged
    in the limit.  Lane separation is preserved by translating every lane by
    the same centreline correction.
    """

    junction_positions: dict[str, tuple[float, float]] = {}
    for cell in local.values():
        for node in cell["nod"].findall("node"):
            if node.attrib.get("type") != "traffic_light":
                continue
            try:
                junction_positions[node.attrib["id"]] = (float(node.attrib["x"]), float(node.attrib["y"]))
            except (KeyError, TypeError, ValueError):
                continue
    for edge in edges:
        lanes = [lane for lane in edge.findall("lane") if lane.attrib.get("shape")]
        if not lanes:
            continue
        for endpoint_attribute, endpoint_index in (("from", 0), ("to", -1)):
            node_id = edge.attrib.get(endpoint_attribute)
            node_position = junction_positions.get(node_id or "")
            if node_position is None:
                continue
            lane_points: list[list[tuple[float, float]]] = []
            for lane in lanes:
                try:
                    points = _parse_shape(lane.attrib["shape"])
                except (KeyError, ValueError):
                    continue
                if len(points) >= 2:
                    lane_points.append(points)
            if not lane_points:
                continue
            endpoint_points = [points[endpoint_index] for points in lane_points]
            centre = (
                sum(point[0] for point in endpoint_points) / len(endpoint_points),
                sum(point[1] for point in endpoint_points) / len(endpoint_points),
            )
            correction = (node_position[0] - centre[0], node_position[1] - centre[1])
            for lane in lanes:
                try:
                    points = _parse_shape(lane.attrib["shape"])
                except (KeyError, ValueError):
                    continue
                if len(points) < 2:
                    continue
                target = points[endpoint_index]
                target = (target[0] + correction[0], target[1] + correction[1])
                if endpoint_index == 0:
                    adjusted = _reanchor_shape(points, target, points[-1])
                else:
                    adjusted = _reanchor_shape(points, points[0], target)
                lane.set("shape", _shape(adjusted))


def _shape(points: Sequence[tuple[float, float]]) -> str:
    return " ".join(f"{x:.3f},{y:.3f}" for x, y in points)


def _parse_shape(value: str) -> list[tuple[float, float]]:
    points: list[tuple[float, float]] = []
    for token in value.split():
        x, y = token.split(",", 1)
        points.append((float(x), float(y)))
    return points


def _lane_endpoints(edges: Sequence[ET.Element]) -> dict[tuple[str, int], tuple[tuple[float, float], tuple[float, float]]]:
    result: dict[tuple[str, int], tuple[tuple[float, float], tuple[float, float]]] = {}
    for edge in edges:
        edge_id = edge.attrib.get("id")
        if not edge_id:
            continue
        for lane in edge.findall("lane"):
            shape = lane.attrib.get("shape", "")
            if not shape:
                continue
            points = _parse_shape(shape)
            if len(points) >= 2:
                result[(edge_id, int(lane.attrib.get("index", "0")))] = (points[0], points[-1])
    return result


def _reanchor_shape(
    points: Sequence[tuple[float, float]],
    source_endpoint: tuple[float, float],
    target_endpoint: tuple[float, float],
) -> list[tuple[float, float]]:
    """Move a MAP curve's endpoints onto the stitched lane axes smoothly.

    A plain endpoint replacement creates a sharp artificial kink.  A linear
    blend of the source and target corrections keeps every intermediate MAP
    control point while making netconvert's custom shape exactly coincident
    with the new incoming/outgoing lane endpoints.
    """

    old_source, old_target = points[0], points[-1]
    count = len(points) - 1
    result: list[tuple[float, float]] = []
    for index, (x, y) in enumerate(points):
        fraction = index / count
        correction_x = (1.0 - fraction) * (source_endpoint[0] - old_source[0]) + fraction * (target_endpoint[0] - old_target[0])
        correction_y = (1.0 - fraction) * (source_endpoint[1] - old_source[1]) + fraction * (target_endpoint[1] - old_target[1])
        result.append((x + correction_x, y + correction_y))
    return result


def _reanchor_connection_shapes(
    connections: Sequence[ET.Element],
    lane_endpoints: Mapping[tuple[str, int], tuple[tuple[float, float], tuple[float, float]]],
) -> bool:
    changed = False
    for item in connections:
        shape = item.attrib.get("shape", "")
        if not shape:
            continue
        points = _parse_shape(shape)
        try:
            from_lane = int(item.attrib.get("fromLane", "0"))
            to_lane = int(item.attrib.get("toLane", "0"))
        except ValueError:
            continue
        source = lane_endpoints.get((item.attrib.get("from", ""), from_lane))
        target = lane_endpoints.get((item.attrib.get("to", ""), to_lane))
        if len(points) < 2 or source is None or target is None:
            continue
        rewritten = _shape(_reanchor_shape(points, source[1], target[0]))
        if rewritten != shape:
            item.set("shape", rewritten)
            changed = True
    return changed


def _edge_name(edge: ET.Element) -> str:
    return next(
        (param.attrib.get("value", "") for param in edge.findall("param") if param.attrib.get("key") == "name"),
        edge.attrib.get("name", ""),
    )


def _unique_elements(elements: Sequence[ET.Element]) -> list[ET.Element]:
    seen: set[str] = set()
    result: list[ET.Element] = []
    for item in elements:
        item_id = item.attrib.get("id", "")
        if item_id and item_id in seen:
            continue
        if item_id:
            seen.add(item_id)
        result.append(item)
    return result


def _write_xml(path: Path, root_name: str, children: Sequence[ET.Element]) -> None:
    root = ET.Element(root_name)
    for child in children:
        root.append(child)
    ET.indent(root, space="    ")
    write_text_atomic(path, '<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(root, encoding="unicode") + "\n")


def _ensure_network_projection_metadata(path: Path) -> dict[str, Any]:
    """Declare Hamburg's metric CRS on a network whose coordinates are already projected.

    The stitched PlainXML uses ETRS89 / UTM 32N metres.  Without a location
    projection SUMO writes ``projParameter=\"!\"`` and downstream WGS84 sensor
    or MAP binding cannot convert coordinates.  Updating metadata does not
    move geometry; it only makes the already-used coordinate reference explicit.
    """

    root = ET.parse(path).getroot()
    location = root.find("location")
    if location is None:
        return {"status": "blocked", "projection": "EPSG:25832", "reason": "network_location_missing"}
    previous = location.attrib.get("projParameter", "")
    location.set("projParameter", _HAMBURG_PROJECTION)
    ET.indent(root, space="    ")
    write_text_atomic(path, '<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(root, encoding="unicode") + "\n")
    return {
        "status": "pass",
        "projection": "EPSG:25832",
        "proj_parameter": _HAMBURG_PROJECTION,
        "previous_proj_parameter": previous,
        "metadata_only": True,
    }


def _as_dict(result: object) -> dict[str, Any]:
    return result.to_dict() if hasattr(result, "to_dict") else dict(result)  # type: ignore[arg-type]


__all__ = ["CORRIDOR_GEOMETRY_SCHEMA", "HamburgOfficialCorridorGeometryError", "materialize_hamburg_official_corridor_geometry"]
