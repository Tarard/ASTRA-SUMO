"""Materialize the hash-bound static topology candidate for Hamburg 2394.

The topology compiler in :mod:`hamburg_2394_tls_topology` deliberately stops
before writing a network.  This module is the next, still review-gated, step:
it applies the already-proven ``delete five / add three`` channelization patch,
marks the three signal-bearing owners with one shared ``HH_2394`` controller,
and emits an all-red placeholder program.  It never claims that the
placeholder is the historical Saturday signal plan.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
import xml.etree.ElementTree as ET

from .artifact_io import copy_file_atomic, write_json_atomic, write_text_atomic
from .candidate_contracts import file_sha256
from .command_runner import run_command
from .hamburg_2394_tls_topology import (
    CONTROLLER_ID,
    PASSIVE_OWNER_IDS,
    ROUTING_REPAIRS,
    ROUTING_REMOVALS,
    SIGNAL_OWNER_IDS,
    build_hamburg_2394_tls_topology_plan,
)
from .sumo_commands import run_sumo_load_audit
from .surface_overlap_audit import (
    audit_sumo_lane_junction_surface_overlaps,
    compare_sumo_surface_overlap_reports,
)


MATERIALIZER_SCHEMA = "torii.hamburg-2394-tls-topology-materializer/v1"
PLAIN_FILENAMES = {
    "nodes": "hamburg_2394_tls_candidate.nod.xml",
    "edges": "hamburg_2394_tls_candidate.edg.xml",
    "connections": "hamburg_2394_tls_candidate.con.xml",
    "tllogic": "hamburg_2394_tls_candidate.tll.xml",
    "types": "hamburg_2394_tls_candidate.typ.xml",
}


class Hamburg2394TlsMaterializationError(ValueError):
    """Raised when a topology candidate cannot be emitted without guessing."""


def materialize_hamburg_2394_tls_topology_candidate(
    *,
    source_net_file: Path,
    map_file: Path,
    ocit_file: Path,
    classification_file: Path,
    accepted_classification_id: str,
    expected_source_sha256: str,
    expected_map_sha256: str,
    expected_ocit_sha256: str,
    expected_classification_sha256: str,
    plain_source_dir: Path,
    output_dir: Path,
    netconvert_binary: str = "netconvert",
    sumo_binary: str = "sumo",
    timeout_seconds: float = 240.0,
    command_runner: Callable[..., object] = run_command,
) -> dict[str, object]:
    """Emit a review-only 2394 topology candidate from frozen PlainXML.

    The source network, official MAP/OCIT assets, classification artifact and
    all five PlainXML inputs are immutable evidence.  The output is separate
    from every source and is accepted only when netconvert/SUMO loading,
    owner/controller parity, and the bounded surface audit pass.
    """

    source = Path(source_net_file).resolve(strict=True)
    map_path = Path(map_file).resolve(strict=True)
    ocit_path = Path(ocit_file).resolve(strict=True)
    classification = Path(classification_file).resolve(strict=True)
    plain_dir = Path(plain_source_dir).resolve(strict=True)
    destination = Path(output_dir).resolve()
    destination.mkdir(parents=True, exist_ok=True)
    manifest_file = destination / "hamburg_2394_tls_topology_candidate.manifest.json"
    source_hash_before = file_sha256(source)
    evidence = {
        "source_net": source,
        "map": map_path,
        "ocit": ocit_path,
        "classification": classification,
    }
    plain_inputs = _resolve_plain_inputs(plain_dir)
    if destination in {path.parent.resolve() for path in (*evidence.values(), *plain_inputs.values())}:
        raise Hamburg2394TlsMaterializationError(
            "output_dir must be separate from every evidence-file directory"
        )
    if source in plain_inputs.values():
        raise Hamburg2394TlsMaterializationError("source network cannot be a PlainXML input")

    plan = build_hamburg_2394_tls_topology_plan(
        source_net_file=source,
        map_file=map_path,
        ocit_file=ocit_path,
        classification_file=classification,
        accepted_classification_id=accepted_classification_id,
        expected_source_sha256=expected_source_sha256,
        expected_map_sha256=expected_map_sha256,
        expected_ocit_sha256=expected_ocit_sha256,
        expected_classification_sha256=expected_classification_sha256,
    )
    if plan.get("status") != "topology_binding_ready":
        raise Hamburg2394TlsMaterializationError("topology plan did not reach ready status")
    acceptance = plan.get("hard_acceptance", {})
    if acceptance != {
        "vehicle_movement_count": 8,
        "control_expression_count": 6,
        "controlled_physical_link_count": 8,
        "signal_owner_count": 3,
        "controller_count": 1,
        "status": "pass",
    }:
        raise Hamburg2394TlsMaterializationError(
            f"unexpected topology acceptance: {acceptance!r}"
        )

    output_plain = destination / "plain"
    output_plain.mkdir(parents=True, exist_ok=True)
    staged = {
        role: output_plain / filename for role, filename in PLAIN_FILENAMES.items()
    }
    for role, source_plain in plain_inputs.items():
        copy_file_atomic(source_plain, staged[role])
    _patch_nodes(staged["nodes"])
    connection_patch = _patch_connections(
        staged["connections"],
        repairs=ROUTING_REPAIRS,
        removals=ROUTING_REMOVALS,
    )
    _patch_tllogic(
        staged["tllogic"],
        movement_bindings=plan["movement_bindings"],
    )

    output_net = destination / "hamburg_2394_tls_topology_candidate.net.xml"
    command = [
        str(netconvert_binary),
        "--node-files",
        str(staged["nodes"]),
        "--edge-files",
        str(staged["edges"]),
        "--connection-files",
        str(staged["connections"]),
        "--tllogic-files",
        str(staged["tllogic"]),
        "--type-files",
        str(staged["types"]),
        "--no-turnarounds",
        "--offset.disable-normalization",
        "true",
        "--output-file",
        str(output_net),
    ]
    result = command_runner(command, cwd=destination, timeout_seconds=timeout_seconds)
    command_report = result.to_dict() if hasattr(result, "to_dict") else dict(result)  # type: ignore[arg-type]
    if command_report.get("status") != "pass" or command_report.get("returncode") != 0:
        raise Hamburg2394TlsMaterializationError(
            "netconvert failed: "
            + str(command_report.get("stderr") or command_report.get("error") or command_report)
        )
    if not output_net.is_file():
        raise Hamburg2394TlsMaterializationError("netconvert reported success without output net")
    _canonicalize_net_file(output_net)

    network_audit = _audit_materialized_network(output_net, plan)
    if network_audit["status"] != "pass":
        raise Hamburg2394TlsMaterializationError(
            "materialized network parity failed: " + "; ".join(network_audit["errors"])
        )
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
    bounded_surface_comparison = compare_sumo_surface_overlap_reports(
        baseline_surface_audit,
        surface_audit,
        focus_junction_ids=[*SIGNAL_OWNER_IDS, *PASSIVE_OWNER_IDS],
        report_file=destination / "surface_overlap" / "bounded_surface_overlap_comparison.json",
    )
    source_immutable = file_sha256(source) == source_hash_before
    status = (
        "review_ready"
        if load_audit.get("status") == "pass"
        and bounded_surface_comparison.get("status") == "pass"
        and network_audit["status"] == "pass"
        and source_immutable
        else "blocked"
    )
    manifest: dict[str, object] = {
        "schema_id": MATERIALIZER_SCHEMA,
        "status": status,
        "claim_status": "official-static-topology-candidate",
        "automatic_promotion_gate": "blocked",
        "source": {
            "path": str(source),
            "sha256_before": source_hash_before,
            "sha256_after": file_sha256(source),
            "immutable": source_immutable,
        },
        "evidence": {
            role: {"path": str(path), "sha256": file_sha256(path)}
            for role, path in evidence.items()
        },
        "plain_inputs": {
            role: {"path": str(path), "sha256": file_sha256(path)}
            for role, path in plain_inputs.items()
        },
        "plain_outputs": {
            role: {"path": str(path), "sha256": file_sha256(path)}
            for role, path in staged.items()
        },
        "topology_plan": plan,
        "materialization": {
            "status": status,
            "controller_id": CONTROLLER_ID,
            "signal_owner_ids": list(SIGNAL_OWNER_IDS),
            "passive_priority_owner_ids": list(PASSIVE_OWNER_IDS),
            "all_red_placeholder": True,
            "historical_two_hour_replay": "not_run",
            "operational_signal_timing": "blocked",
            "routing_patch": {
                "removed_count": connection_patch["removed_count"],
                "added_count": connection_patch["added_count"],
            },
        },
        "netconvert": {"command": command, "result": command_report},
        "network_audit": network_audit,
        "sumo_load_audit": load_audit,
        "surface_overlap_audit": surface_audit,
        "surface_overlap_comparison": bounded_surface_comparison,
        "artifacts": {
            "manifest": str(manifest_file),
            "network": str(output_net),
        },
    }
    write_json_atomic(manifest_file, manifest, sort_keys=True)
    return manifest


def _resolve_plain_inputs(plain_dir: Path) -> dict[str, Path]:
    required = {
        "nodes": next(plain_dir.glob("*.nod.xml"), None),
        "edges": next(plain_dir.glob("*.edg.xml"), None),
        "connections": next(plain_dir.glob("*.con.xml"), None),
        "tllogic": next(plain_dir.glob("*.tll.xml"), None),
        "types": next(plain_dir.glob("*.typ.xml"), None),
    }
    missing = sorted(role for role, path in required.items() if path is None)
    if missing:
        raise Hamburg2394TlsMaterializationError(
            f"plain_source_dir is missing required files: {missing}"
        )
    resolved = {role: path.resolve(strict=True) for role, path in required.items() if path is not None}
    nodes = ET.parse(resolved["nodes"]).getroot()
    node_ids = {node.attrib.get("id", "") for node in nodes.findall("node")}
    missing_signal_owners = sorted(set(SIGNAL_OWNER_IDS) - node_ids)
    if missing_signal_owners:
        raise Hamburg2394TlsMaterializationError(
            "plain_source_dir must be exported from the joined V10 network; "
            f"missing post-join signal owners: {missing_signal_owners}"
        )
    if nodes.findall("join"):
        raise Hamburg2394TlsMaterializationError(
            "plain_source_dir must already contain the joined V10 junction ids"
        )
    return resolved


def _canonicalize_net_file(path: Path) -> None:
    """Remove netconvert's timestamp/path comment so candidate hashes are portable."""

    ET.register_namespace("xsi", "http://www.w3.org/2001/XMLSchema-instance")
    tree = ET.parse(path)
    root = tree.getroot()
    ET.indent(tree, space="    ")
    text = '<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(
        root,
        encoding="unicode",
    ) + "\n"
    write_text_atomic(path, text)


def _patch_nodes(path: Path) -> dict[str, object]:
    tree = ET.parse(path)
    root = tree.getroot()
    nodes = {node.attrib.get("id", ""): node for node in root.findall("node")}
    missing = sorted(set(SIGNAL_OWNER_IDS) - set(nodes))
    if missing:
        raise Hamburg2394TlsMaterializationError(f"PlainXML nodes missing signal owners: {missing}")
    for node_id in SIGNAL_OWNER_IDS:
        node = nodes[node_id]
        if node.attrib.get("tl") not in {None, ""}:
            raise Hamburg2394TlsMaterializationError(
                f"signal owner {node_id} already has a different tl binding"
            )
        node.set("type", "traffic_light")
        node.set("tl", CONTROLLER_ID)
    for node_id in PASSIVE_OWNER_IDS:
        node = nodes[node_id]
        if node.attrib.get("type") != "priority" or node.attrib.get("tl", ""):
            raise Hamburg2394TlsMaterializationError(
                f"passive owner {node_id} is not an unbound priority node"
            )
    ET.indent(tree, space="    ")
    write_text_atomic(path, ET.tostring(root, encoding="unicode") + "\n")
    return {"status": "pass", "signal_owner_count": len(SIGNAL_OWNER_IDS)}


def _patch_connections(
    path: Path,
    *,
    repairs: Sequence[object],
    removals: Sequence[object],
) -> dict[str, object]:
    tree = ET.parse(path)
    root = tree.getroot()

    def key(element: ET.Element) -> tuple[str, int, str, int]:
        return (
            element.attrib.get("from", ""),
            int(element.attrib.get("fromLane", "-1")),
            element.attrib.get("to", ""),
            int(element.attrib.get("toLane", "-1")),
        )

    removal_keys = {item.key for item in removals}  # type: ignore[attr-defined]
    repair_keys = {item.key for item in repairs}  # type: ignore[attr-defined]
    existing = [element for element in root.findall("connection") if key(element) in removal_keys]
    if len(existing) != len(removal_keys):
        raise Hamburg2394TlsMaterializationError(
            f"expected {len(removal_keys)} routing removals, found {len(existing)}"
        )
    for element in list(root.findall("connection")):
        if key(element) in removal_keys:
            root.remove(element)
    remaining = {key(element) for element in root.findall("connection")}
    if remaining & repair_keys:
        raise Hamburg2394TlsMaterializationError("a routing repair already exists in PlainXML")
    for item in sorted(repairs, key=lambda value: value.key):  # type: ignore[attr-defined]
        root.append(
            ET.Element(
                "connection",
                {
                    "from": item.from_edge,  # type: ignore[attr-defined]
                    "to": item.to_edge,  # type: ignore[attr-defined]
                    "fromLane": str(item.from_lane),  # type: ignore[attr-defined]
                    "toLane": str(item.to_lane),  # type: ignore[attr-defined]
                },
            )
        )
    ET.indent(tree, space="    ")
    write_text_atomic(path, ET.tostring(root, encoding="unicode") + "\n")
    return {
        "status": "pass",
        "removed_count": len(removal_keys),
        "added_count": len(repair_keys),
    }


def _patch_tllogic(path: Path, *, movement_bindings: Sequence[Mapping[str, object]]) -> dict[str, object]:
    tree = ET.parse(path)
    root = tree.getroot()
    if any(element.attrib.get("id") == CONTROLLER_ID for element in root.findall("tlLogic")):
        raise Hamburg2394TlsMaterializationError(f"PlainXML already contains {CONTROLLER_ID}")
    logic = ET.Element(
        "tlLogic",
        {
            "id": CONTROLLER_ID,
            "type": "static",
            "programID": "official-static-structure-placeholder",
            "offset": "0",
        },
    )
    ET.SubElement(logic, "phase", {"duration": "1", "state": "rrrrrr"})
    root.append(logic)
    expected = {int(row["link_index"]) for row in movement_bindings}
    if expected != set(range(6)):
        raise Hamburg2394TlsMaterializationError(
            f"movement link indices must cover 0..5, got {sorted(expected)}"
        )
    seen: set[tuple[str, int, str, int]] = set()
    for row in sorted(movement_bindings, key=lambda item: str(item["connection_id"])):
        link = row["controlled_stopline_link"]
        if not isinstance(link, Mapping):
            raise Hamburg2394TlsMaterializationError("movement stopline link is not an object")
        signature = (
            str(link["from_edge"]),
            int(link["from_lane"]),
            str(link["to_edge"]),
            int(link["to_lane"]),
        )
        if signature in seen:
            raise Hamburg2394TlsMaterializationError(f"duplicate stopline binding: {signature}")
        seen.add(signature)
        root.append(
            ET.Element(
                "connection",
                {
                    "from": signature[0],
                    "to": signature[2],
                    "fromLane": str(signature[1]),
                    "toLane": str(signature[3]),
                    "tl": CONTROLLER_ID,
                    "linkIndex": str(int(row["link_index"])),
                },
            )
        )
    if len(seen) != 8:
        raise Hamburg2394TlsMaterializationError(f"expected eight physical tl links, got {len(seen)}")
    ET.indent(tree, space="    ")
    write_text_atomic(path, ET.tostring(root, encoding="unicode") + "\n")
    return {"status": "pass", "controller_id": CONTROLLER_ID, "link_count": len(seen)}


def _audit_materialized_network(path: Path, plan: Mapping[str, object]) -> dict[str, object]:
    root = ET.parse(path).getroot()
    errors: list[str] = []
    junctions = {element.attrib.get("id", ""): element for element in root.findall("junction")}
    controller_connections = [
        element for element in root.findall("connection") if element.attrib.get("tl") == CONTROLLER_ID
    ]
    signal_owners = []
    for node_id in SIGNAL_OWNER_IDS:
        node = junctions.get(node_id)
        owner_prefix = f":{node_id}_"
        if (
            node is not None
            and node.attrib.get("type") == "traffic_light"
            and any(element.attrib.get("via", "").startswith(owner_prefix) for element in controller_connections)
        ):
            signal_owners.append(node_id)
    if len(signal_owners) != 3:
        errors.append(f"signal owners={signal_owners!r}")
    for node_id in PASSIVE_OWNER_IDS:
        node = junctions.get(node_id)
        owner_prefix = f":{node_id}_"
        if (
            node is None
            or node.attrib.get("type") != "priority"
            or any(element.attrib.get("via", "").startswith(owner_prefix) for element in controller_connections)
        ):
            errors.append(f"passive owner {node_id} changed")
    logic = [element for element in root.findall("tlLogic") if element.attrib.get("id") == CONTROLLER_ID]
    if len(logic) != 1:
        errors.append(f"HH_2394 tlLogic count={len(logic)}")
    elif logic[0].find("phase") is None or logic[0].find("phase").attrib.get("state") != "rrrrrr":
        errors.append("HH_2394 is not the explicit six-link all-red placeholder")
    bindings = controller_connections
    expected_rows = plan.get("movement_bindings", [])
    expected = {
        (
            str(row["controlled_stopline_link"]["from_edge"]),
            int(row["controlled_stopline_link"]["from_lane"]),
            str(row["controlled_stopline_link"]["to_edge"]),
            int(row["controlled_stopline_link"]["to_lane"]),
            int(row["link_index"]),
        )
        for row in expected_rows  # type: ignore[union-attr]
    }
    actual = {
        (
            element.attrib.get("from", ""),
            int(element.attrib.get("fromLane", "-1")),
            element.attrib.get("to", ""),
            int(element.attrib.get("toLane", "-1")),
            int(element.attrib.get("linkIndex", "-1")),
        )
        for element in bindings
    }
    if actual != expected:
        errors.append(f"controlled physical links differ: expected={len(expected)}, actual={len(actual)}")
    return {
        "status": "pass" if not errors else "fail",
        "errors": errors,
        "signal_owner_ids": signal_owners,
        "controller_count": len(logic),
        "controlled_link_count": len(bindings),
        "controlled_link_indices": sorted({item[4] for item in actual}),
    }
