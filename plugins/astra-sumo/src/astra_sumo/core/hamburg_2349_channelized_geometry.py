from __future__ import annotations

from dataclasses import dataclass
import math
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence
import xml.etree.ElementTree as ET

from .artifact_io import copy_file_atomic, write_json_atomic, write_text_atomic
from .candidate_contracts import file_sha256
from .command_runner import run_command
from .hamburg_official_corridor_geometry import _lane_endpoints, _reanchor_connection_shapes
from .junction_aggregation import (
    audit_join_collapse_residuals,
    audit_join_output_presence,
    audit_junction_aggregation_preservation,
)
from .junction_join_definition import build_junction_join_definition
from .osm_workflow import export_plain_net_for_teacher_guided_repair


SCHEMA = "torii.hamburg-2349-channelized-geometry/v1"
JOIN_GROUP = ("2761334249", "610506352", "cluster_739654528_759714704")
JOINED_JUNCTION_ID = "cluster_2761334249_610506352_cluster_739654528_759714704"
ABSORBED_EDGE_IDS = frozenset({"61649647#1", "61649647#2"})
INCOMING_EDGE_ID = "61649647#0"
TARGET_TLS_ID = "HH_2349"
LEGACY_TLS_ID = "2761334249"


@dataclass(frozen=True)
class ChannelizedMovement:
    movement_id: str
    through_lane: int
    to_edge: str
    to_lane: int
    link_index: int

    @property
    def key(self) -> tuple[str, str, str, str]:
        return (INCOMING_EDGE_ID, str(self.through_lane), self.to_edge, str(self.to_lane))


MOVEMENTS = (
    ChannelizedMovement("C5", 0, "59990286", 0, 2),
    ChannelizedMovement("C6", 0, "61649649#0", 0, 5),
    ChannelizedMovement("C4", 1, "59990286", 1, 2),
)


class Hamburg2349ChannelizedGeometryError(ValueError):
    """Raised when the accepted W1 source no longer matches the bounded 2349 edit."""


def materialize_hamburg_2349_channelized_geometry(
    *,
    source_net_file: Path,
    expected_source_sha256: str,
    output_dir: Path,
    netconvert_binary: str = "netconvert",
    timeout_seconds: float = 240.0,
    command_runner: Callable[..., Any] = run_command,
) -> dict[str, Any]:
    """Replace two external micro-edges with one channelized 2349 junction.

    The source must be the hash-bound accepted W1 network (including later
    2403 work).  Only ``61649647#1`` and ``61649647#2`` may disappear.  Their
    existing OSM lane and compiled-via geometry is concatenated into exactly
    three direct movement curves from the official stop-line edge.  The old
    upstream physical owner joins the official ``HH_2349`` logical cell; all
    semantics outside this three-node cell must remain exact.
    """

    source = source_net_file.resolve(strict=True)
    destination = output_dir.resolve()
    _require_sha256(expected_source_sha256)
    source_hash_before = file_sha256(source)
    if source_hash_before.lower() != expected_source_sha256.lower():
        raise Hamburg2349ChannelizedGeometryError(
            "accepted W1 source hash mismatch: "
            f"expected {expected_source_sha256.lower()}, got {source_hash_before}"
        )
    if destination == source.parent or _is_within(source, destination):
        raise Hamburg2349ChannelizedGeometryError(
            "output_dir must not contain or overwrite the accepted source network"
        )

    source_root = ET.parse(source).getroot()
    movement_shapes = _validate_source_and_build_shapes(source_root)
    joined_cell_shape = _joined_cell_shape(source_root)
    destination.mkdir(parents=True, exist_ok=True)
    manifest_file = destination / "hamburg_2349_channelized_geometry.manifest.json"
    candidate_net = destination / "hamburg_2349_channelized_geometry.net.xml"
    joined_net = destination / "hamburg_2349_joined_topology.net.xml"
    joined_junctions = destination / "hamburg_2349_channelized_geometry.joined.xml"
    join_command_file = destination / "hamburg_2349_join.netconvert.cmd.txt"
    command_file = destination / "hamburg_2349_channelized_geometry.netconvert.cmd.txt"

    source_export = export_plain_net_for_teacher_guided_repair(
        net_file=source,
        output_dir=destination / "plain_export",
        prefix="hamburg_2349_source",
        netconvert_binary=netconvert_binary,
        timeout_seconds=timeout_seconds,
        command_runner=command_runner,
    )
    if source_export.get("status") != "pass":
        return _blocked_manifest(
            manifest_file,
            reason="plain_export_failed",
            source=source,
            source_hash=source_hash_before,
            export=source_export,
        )
    source_raw_edges = _required_export_path(source_export, "raw_edge_file")
    source_raw_tllogic = _required_export_path(source_export, "raw_tllogic_file")
    _validate_plain_absorbed_edges(source_raw_edges)

    join_definition = build_junction_join_definition(
        [{
            "source": "reference_matched",
            "candidate_id": "hamburg-2349-c4-c5-c6-channelized-core",
            "decision": "join",
            "confidence": "confirmed",
            "review_status": "confirmed",
            "node_ids": list(JOIN_GROUP),
            "reason": "official KML stop-line and MAP/OCIT C4/C5/C6 evidence",
        }],
        output_dir=destination / "join_definition",
        prefix="hamburg_2349_channelized",
    )
    _validate_join_definition(join_definition)

    join_command = [
        netconvert_binary,
        "--sumo-net-file", str(source),
        "--node-files", str(join_definition["nodes_patch_file"]),
        "--offset.disable-normalization", "true",
        "--junctions.join-output", str(joined_junctions),
        "--output-file", str(joined_net),
    ]
    write_text_atomic(join_command_file, " ".join(join_command) + "\n")
    join_netconvert = _result_dict(
        command_runner(join_command, cwd=destination, timeout_seconds=timeout_seconds)
    )
    if not _netconvert_passed(join_netconvert, joined_net):
        return _blocked_manifest(
            manifest_file, reason="join_netconvert_failed", source=source,
            source_hash=source_hash_before, export=source_export,
            details={"join_netconvert": join_netconvert},
        )
    join_preservation = audit_junction_aggregation_preservation(
        source, joined_net, join_groups=[JOIN_GROUP]
    )
    join_collapse = audit_join_collapse_residuals(joined_net, [JOIN_GROUP])
    join_presence = audit_join_output_presence(joined_net, [JOIN_GROUP])
    if (
        join_preservation.get("unexpected_removed_normal_edge_count") != 0
        or set(join_preservation.get("absorbed_join_edge_ids", [])) != ABSORBED_EDGE_IDS
        or join_preservation.get("new_dangling_shared_normal_edge_count") != 0
        or join_preservation.get("boundary_movement_preservation", {}).get("status") != "pass"
        or join_collapse.get("status") != "pass"
        or join_presence.get("status") != "pass"
    ):
        return _blocked_manifest(
            manifest_file, reason="joined_topology_failed_preservation", source=source,
            source_hash=source_hash_before, export=source_export,
            details={
                "join_netconvert": join_netconvert,
                "join_preservation": join_preservation,
                "join_collapse": join_collapse,
                "join_presence": join_presence,
            },
        )

    joined_export = export_plain_net_for_teacher_guided_repair(
        net_file=joined_net,
        output_dir=destination / "joined_plain_export",
        prefix="hamburg_2349_joined",
        netconvert_binary=netconvert_binary,
        timeout_seconds=timeout_seconds,
        command_runner=command_runner,
    )
    if joined_export.get("status") != "pass":
        return _blocked_manifest(
            manifest_file, reason="joined_plain_export_failed", source=source,
            source_hash=source_hash_before, export=source_export,
            details={"joined_plain_export": joined_export},
        )
    raw_nodes = _required_export_path(joined_export, "raw_node_file")
    raw_edges = _required_export_path(joined_export, "raw_edge_file")
    raw_connections = _required_export_path(joined_export, "raw_connection_file")
    raw_types = _optional_export_path(joined_export, "raw_type_file")

    staged = destination / "staged_plain"
    staged.mkdir(parents=True, exist_ok=True)
    nodes_file = staged / "hamburg_2349_channelized.nod.xml"
    edges_file = staged / "hamburg_2349_channelized.edg.xml"
    connections_file = staged / "hamburg_2349_channelized.con.xml"
    tllogic_file = staged / "hamburg_2349_channelized.tll.xml"
    types_file = staged / "hamburg_2349_channelized.typ.xml"
    copy_file_atomic(raw_edges, edges_file)
    if raw_types is not None:
        copy_file_atomic(raw_types, types_file)
    node_stage = _stage_nodes(raw_nodes, nodes_file, joined_cell_shape)
    direct_connections = _stage_connections(raw_connections, connections_file, movement_shapes)
    tls_stage = _stage_tllogic(source_raw_tllogic, tllogic_file)

    command = [
        netconvert_binary,
        "--node-files",
        str(nodes_file),
        "--edge-files",
        str(edges_file),
        "--connection-files",
        str(connections_file),
        "--tllogic-files",
        str(tllogic_file),
    ]
    if raw_types is not None:
        command.extend(["--type-files", str(types_file)])
    command.extend(
        [
            "--no-turnarounds",
            "--offset.disable-normalization",
            "true",
            "--output-file",
            str(candidate_net),
        ]
    )
    write_text_atomic(command_file, " ".join(command) + "\n")
    netconvert_passes: list[dict[str, Any]] = []
    for _ in range(3):
        result = _result_dict(
            command_runner(command, cwd=destination, timeout_seconds=timeout_seconds)
        )
        netconvert_passes.append(result)
        if not _netconvert_passed(result, candidate_net):
            break
        endpoints = _lane_endpoints(ET.parse(candidate_net).getroot().findall("edge"))
        if not _reanchor_connection_shapes(direct_connections, endpoints):
            break
        _persist_reanchored_connections(connections_file, direct_connections)

    netconvert = dict(netconvert_passes[-1])
    netconvert["passes"] = netconvert_passes
    compiled = _netconvert_passed(netconvert, candidate_net)
    source_hash_after = file_sha256(source)
    if compiled:
        candidate_root = ET.parse(candidate_net).getroot()
        collapse = audit_join_collapse_residuals(candidate_net, [JOIN_GROUP])
        presence = audit_join_output_presence(candidate_net, [JOIN_GROUP])
        preservation = audit_junction_aggregation_preservation(
            source,
            candidate_net,
            join_groups=[JOIN_GROUP],
        )
        edge_scope = _audit_edge_scope(source_root, candidate_root)
        movement_scope = _audit_direct_movements(candidate_root)
        channel_shapes = _audit_channelized_shapes(candidate_root, direct_connections)
        off_cell = _audit_off_cell_semantics(source_root, candidate_root)
        outer_geometry = _audit_outer_geometry(source_root, candidate_root)
        logical_cell = _audit_logical_cell(candidate_root)
    else:
        collapse = presence = preservation = edge_scope = movement_scope = {"status": "not_run"}
        channel_shapes = off_cell = outer_geometry = logical_cell = {"status": "not_run"}

    gates = {
        "source_immutable": "pass" if source_hash_after == source_hash_before else "fail",
        "plain_export": "pass",
        "join_definition": "pass",
        "netconvert": "pass" if compiled else "fail",
        "join_collapse": str(collapse.get("status", "fail")),
        "join_output_presence": str(presence.get("status", "fail")),
        "preservation": str(preservation.get("status", "fail")),
        "exact_absorbed_edge_scope": str(edge_scope.get("status", "fail")),
        "exact_direct_movements": str(movement_scope.get("status", "fail")),
        "channelized_connection_shapes": str(channel_shapes.get("status", "fail")),
        "off_cell_semantics": str(off_cell.get("status", "fail")),
        "outer_geometry": str(outer_geometry.get("status", "fail")),
        "official_logical_cell": str(logical_cell.get("status", "fail")),
    }
    machine_pass = all(value == "pass" for value in gates.values())
    manifest: dict[str, Any] = {
        "schema": SCHEMA,
        "status": "review_ready" if machine_pass else "blocked",
        "claim_status": "local_channelized_geometry_and_binding_candidate",
        "automatic_promotion_gate": "blocked",
        "reason": (
            "local_machine_gates_pass_visual_and_full_corridor_review_still_required"
            if machine_pass
            else "one_or_more_local_geometry_gates_failed"
        ),
        "source": {
            "path": str(source),
            "expected_sha256": expected_source_sha256.lower(),
            "sha256_before": source_hash_before,
            "sha256_after": source_hash_after,
            "immutable": source_hash_after == source_hash_before,
        },
        "edit_scope": {
            "join_group": list(JOIN_GROUP),
            "joined_junction_id": JOINED_JUNCTION_ID,
            "absorbed_edge_ids": sorted(ABSORBED_EDGE_IDS),
            "direct_movements": [_movement_record(item) for item in MOVEMENTS],
            "official_tls_id": TARGET_TLS_ID,
            "retired_legacy_tls_program_id": LEGACY_TLS_ID,
        },
        "staging": {
            "nodes": node_stage,
            "connections": {"status": "pass", "direct_connection_count": len(direct_connections)},
            "tllogic": tls_stage,
        },
        "gates": gates,
        "audits": {
            "join_collapse": collapse,
            "join_output_presence": presence,
            "preservation": preservation,
            "edge_scope": edge_scope,
            "direct_movements": movement_scope,
            "channelized_connection_shapes": channel_shapes,
            "off_cell_semantics": off_cell,
            "outer_geometry": outer_geometry,
            "official_logical_cell": logical_cell,
        },
        "plain_export": source_export,
        "joined_plain_export": joined_export,
        "join_definition": join_definition,
        "join_netconvert": join_netconvert,
        "netconvert": netconvert,
        "artifacts": {
            "candidate_net": _artifact(candidate_net),
            "joined_topology_net": _artifact(joined_net),
            "manifest": str(manifest_file),
            "command": _artifact(command_file),
            "join_command": _artifact(join_command_file),
            "joined_junctions": _artifact(joined_junctions),
            "staged_nodes": _artifact(nodes_file),
            "staged_edges": _artifact(edges_file),
            "staged_connections": _artifact(connections_file),
            "staged_tllogic": _artifact(tllogic_file),
            "staged_types": _artifact(types_file),
        },
    }
    write_json_atomic(manifest_file, manifest, sort_keys=True)
    return manifest


def _validate_source_and_build_shapes(root: ET.Element) -> dict[str, str]:
    edges = {
        item.attrib.get("id", ""): item
        for item in root.findall("edge")
        if item.attrib.get("id", "") and not item.attrib.get("id", "").startswith(":")
    }
    for edge_id, endpoints in {
        "61649647#1": (JOIN_GROUP[0], JOIN_GROUP[1]),
        "61649647#2": (JOIN_GROUP[1], JOIN_GROUP[2]),
    }.items():
        edge = edges.get(edge_id)
        if edge is None or (edge.attrib.get("from"), edge.attrib.get("to")) != endpoints:
            raise Hamburg2349ChannelizedGeometryError(
                f"{edge_id} is not the exact directed edge inside the accepted join group"
            )
    required = {INCOMING_EDGE_ID, *ABSORBED_EDGE_IDS, *(item.to_edge for item in MOVEMENTS)}
    if missing := sorted(required - set(edges)):
        raise Hamburg2349ChannelizedGeometryError(f"source is missing required 2349 edges: {missing}")

    connections = [item for item in root.findall("connection") if item.attrib.get("from")]
    expected_touching = {
        (INCOMING_EDGE_ID, "0", "61649647#1", "0"),
        (INCOMING_EDGE_ID, "1", "61649647#1", "1"),
        ("61649647#1", "0", "61649647#2", "0"),
        ("61649647#1", "1", "61649647#2", "1"),
        ("61649647#2", "0", "59990286", "0"),
        ("61649647#2", "0", "61649649#0", "0"),
        ("61649647#2", "1", "59990286", "1"),
    }
    actual_touching = {
        _connection_key(item)
        for item in connections
        if not item.attrib.get("from", "").startswith(":")
        and not item.attrib.get("to", "").startswith(":")
        and (
            item.attrib.get("from") in ABSORBED_EDGE_IDS
            or item.attrib.get("to") in ABSORBED_EDGE_IDS
        )
    }
    if actual_touching != expected_touching:
        raise Hamburg2349ChannelizedGeometryError(
            "source movement chain touching the absorbed edges changed: "
            f"expected {sorted(expected_touching)}, got {sorted(actual_touching)}"
        )

    lane_shapes = {
        lane.attrib.get("id", ""): lane.attrib.get("shape", "")
        for edge in root.findall("edge")
        for lane in edge.findall("lane")
        if lane.attrib.get("id", "") and lane.attrib.get("shape", "")
    }
    shapes: dict[str, str] = {}
    for movement in MOVEMENTS:
        keys = (
            (INCOMING_EDGE_ID, str(movement.through_lane), "61649647#1", str(movement.through_lane)),
            ("61649647#1", str(movement.through_lane), "61649647#2", str(movement.through_lane)),
            ("61649647#2", str(movement.through_lane), movement.to_edge, str(movement.to_lane)),
        )
        chain = [_one_connection(connections, key) for key in keys]
        first = chain[0]
        if (first.attrib.get("tl"), first.attrib.get("linkIndex")) != (
            LEGACY_TLS_ID,
            str(movement.through_lane),
        ):
            raise Hamburg2349ChannelizedGeometryError(
                f"source stop-line owner for {movement.movement_id} changed"
            )
        official = [item for item in chain[1:] if item.attrib.get("tl") == TARGET_TLS_ID]
        if len(official) != 1 or official[0].attrib.get("linkIndex") != str(movement.link_index):
            raise Hamburg2349ChannelizedGeometryError(
                f"source official signal-group evidence for {movement.movement_id} changed"
            )
        via_ids = [item.attrib.get("via", "") for item in chain]
        pieces = [
            lane_shapes.get(via_ids[0], ""),
            lane_shapes.get(f"61649647#1_{movement.through_lane}", ""),
            lane_shapes.get(via_ids[1], ""),
            lane_shapes.get(f"61649647#2_{movement.through_lane}", ""),
            lane_shapes.get(via_ids[2], ""),
        ]
        if not all(via_ids) or not all(pieces):
            raise Hamburg2349ChannelizedGeometryError(
                f"source movement {movement.movement_id} lacks complete OSM/via geometry"
            )
        shapes[movement.movement_id] = _shape_text(
            _join_points([_parse_shape(piece) for piece in pieces])
        )
    return shapes


def _stage_nodes(
    raw_nodes: Path,
    output_file: Path,
    joined_cell_shape: str,
) -> dict[str, Any]:
    tree = ET.parse(raw_nodes)
    root = tree.getroot()
    nodes = {item.attrib.get("id", ""): item for item in root.findall("node")}
    if JOINED_JUNCTION_ID not in nodes or set(JOIN_GROUP) & set(nodes):
        raise Hamburg2349ChannelizedGeometryError(
            "joined PlainXML does not contain exactly the expected 2349 logical owner"
        )
    node = nodes[JOINED_JUNCTION_ID]
    node.set("type", "traffic_light")
    node.set("tl", TARGET_TLS_ID)
    node.set("shape", joined_cell_shape)
    node.attrib.pop("controlledInner", None)
    _write_tree(output_file, tree)
    return {
        "status": "pass",
        "official_logical_cell_nodes": list(JOIN_GROUP),
        "joined_junction_id": JOINED_JUNCTION_ID,
        "shape": joined_cell_shape,
        "official_tls_id": TARGET_TLS_ID,
    }


def _stage_connections(
    raw_connections: Path,
    output_file: Path,
    shapes: Mapping[str, str],
) -> list[ET.Element]:
    tree = ET.parse(raw_connections)
    root = tree.getroot()
    expected = {movement.key for movement in MOVEMENTS}
    direct = [
        item
        for item in root.findall("connection")
        if item.attrib.get("from") == INCOMING_EDGE_ID
        and item.attrib.get("to") in {movement.to_edge for movement in MOVEMENTS}
    ]
    if {_connection_key(item) for item in direct} != expected or len(direct) != len(expected):
        raise Hamburg2349ChannelizedGeometryError(
            "joined PlainXML did not produce exactly the three proven direct movements"
        )
    by_key = {_connection_key(item): item for item in direct}
    for movement in MOVEMENTS:
        item = by_key[movement.key]
        item.set("shape", shapes[movement.movement_id])
        item.attrib.pop("uncontrolled", None)
    _write_tree(output_file, tree)
    return [by_key[movement.key] for movement in MOVEMENTS]


def _stage_tllogic(raw_tllogic: Path, output_file: Path) -> dict[str, Any]:
    tree = ET.parse(raw_tllogic)
    root = tree.getroot()
    programs = {item.attrib.get("id", ""): item for item in root.findall("tlLogic")}
    if TARGET_TLS_ID not in programs or LEGACY_TLS_ID not in programs:
        raise Hamburg2349ChannelizedGeometryError("plain TLS export lost a required source program")
    root.remove(programs[LEGACY_TLS_ID])
    removed = 0
    for item in list(root.findall("connection")):
        if item.attrib.get("from") in ABSORBED_EDGE_IDS or item.attrib.get("to") in ABSORBED_EDGE_IDS:
            root.remove(item)
            removed += 1
    if removed != 5:
        raise Hamburg2349ChannelizedGeometryError(
            f"plain TLS export must contain exactly five absorbed-edge bindings, got {removed}"
        )
    for movement in MOVEMENTS:
        ET.SubElement(
            root,
            "connection",
            {
                "from": INCOMING_EDGE_ID,
                "to": movement.to_edge,
                "fromLane": str(movement.through_lane),
                "toLane": str(movement.to_lane),
                "tl": TARGET_TLS_ID,
                "linkIndex": str(movement.link_index),
            },
        )
    _write_tree(output_file, tree)
    return {
        "status": "pass",
        "removed_absorbed_edge_binding_count": removed,
        "direct_binding_count": len(MOVEMENTS),
        "official_tls_id": TARGET_TLS_ID,
        "retired_legacy_program_id": LEGACY_TLS_ID,
    }


def _audit_edge_scope(source: ET.Element, candidate: ET.Element) -> dict[str, Any]:
    removed = _normal_edge_ids(source) - _normal_edge_ids(candidate)
    added = _normal_edge_ids(candidate) - _normal_edge_ids(source)
    return {
        "status": "pass" if removed == ABSORBED_EDGE_IDS and not added else "fail",
        "expected_removed_edge_ids": sorted(ABSORBED_EDGE_IDS),
        "actual_removed_edge_ids": sorted(removed),
        "unexpected_added_edge_ids": sorted(added),
    }


def _audit_direct_movements(root: ET.Element) -> dict[str, Any]:
    targets = {item.to_edge for item in MOVEMENTS}
    rows = [
        item
        for item in root.findall("connection")
        if item.attrib.get("from") == INCOMING_EDGE_ID and item.attrib.get("to") in targets
    ]
    actual = {
        _connection_key(item): (item.attrib.get("tl", ""), item.attrib.get("linkIndex", ""))
        for item in rows
    }
    expected = {item.key: (TARGET_TLS_ID, str(item.link_index)) for item in MOVEMENTS}
    return {
        "status": "pass" if actual == expected and len(rows) == len(expected) else "fail",
        "expected": [{**_movement_record(item), "key": list(item.key)} for item in MOVEMENTS],
        "actual": [
            {"key": list(key), "tl": value[0], "link_index": value[1]}
            for key, value in sorted(actual.items())
        ],
    }


def _audit_channelized_shapes(
    candidate: ET.Element,
    staged_connections: Sequence[ET.Element],
) -> dict[str, Any]:
    expected = {_connection_key(item): _parse_shape(item.attrib.get("shape", "")) for item in staged_connections}
    lanes = {
        lane.attrib.get("id", ""): _parse_shape(lane.attrib.get("shape", ""))
        for edge in candidate.findall("edge")
        for lane in edge.findall("lane")
        if lane.attrib.get("id", "") and lane.attrib.get("shape", "")
    }
    rows = []
    for movement in MOVEMENTS:
        matches = [item for item in candidate.findall("connection") if _connection_key(item) == movement.key]
        via = matches[0].attrib.get("via", "") if len(matches) == 1 else ""
        distance = _symmetric_polyline_distance(expected.get(movement.key, []), lanes.get(via, []))
        rows.append(
            {
                "movement_id": movement.movement_id,
                "via_lane_id": via,
                "max_symmetric_distance_m": distance,
                "status": "pass" if distance is not None and distance <= 0.25 else "fail",
            }
        )
    return {
        "status": "pass" if all(item["status"] == "pass" for item in rows) else "fail",
        "tolerance_m": 0.25,
        "movements": rows,
    }


def _audit_off_cell_semantics(source: ET.Element, candidate: ET.Element) -> dict[str, Any]:
    incident = {
        edge.attrib.get("id", "")
        for edge in source.findall("edge")
        if edge.attrib.get("id", "")
        and not edge.attrib.get("id", "").startswith(":")
        and (edge.attrib.get("from", "") in JOIN_GROUP or edge.attrib.get("to", "") in JOIN_GROUP)
    }

    def connection_signatures(root: ET.Element) -> set[tuple[str, ...]]:
        return {
            (
                item.attrib.get("from", ""),
                item.attrib.get("fromLane", ""),
                item.attrib.get("to", ""),
                item.attrib.get("toLane", ""),
                item.attrib.get("tl", ""),
                item.attrib.get("linkIndex", ""),
                item.attrib.get("linkIndex2", ""),
                item.attrib.get("dir", ""),
                item.attrib.get("state", ""),
            )
            for item in root.findall("connection")
            if item.attrib.get("from", "") not in incident
            and item.attrib.get("to", "") not in incident
            and not item.attrib.get("from", "").startswith(":")
        }

    source_connections = connection_signatures(source)
    candidate_connections = connection_signatures(candidate)
    excluded_tls = {TARGET_TLS_ID, LEGACY_TLS_ID}
    source_tls = _tls_program_signatures(source, exclude=excluded_tls)
    candidate_tls = _tls_program_signatures(candidate, exclude=excluded_tls)
    return {
        "status": "pass" if source_connections == candidate_connections and source_tls == candidate_tls else "fail",
        "missing_connection_signatures": [list(item) for item in sorted(source_connections - candidate_connections)],
        "unexpected_connection_signatures": [list(item) for item in sorted(candidate_connections - source_connections)],
        "missing_tls_program_signatures": sorted(source_tls - candidate_tls),
        "unexpected_tls_program_signatures": sorted(candidate_tls - source_tls),
    }


def _audit_outer_geometry(source: ET.Element, candidate: ET.Element) -> dict[str, Any]:
    source_lanes = _lane_shapes_by_key(source)
    candidate_lanes = _lane_shapes_by_key(candidate)
    requested = {(INCOMING_EDGE_ID, item.through_lane, "remote_start") for item in MOVEMENTS}
    requested |= {(item.to_edge, item.to_lane, "remote_end") for item in MOVEMENTS}
    rows = []
    for edge_id, lane_index, endpoint in sorted(requested):
        source_points = source_lanes.get((edge_id, lane_index), [])
        candidate_points = candidate_lanes.get((edge_id, lane_index), [])
        distance = None
        if source_points and candidate_points:
            source_point = source_points[0] if endpoint == "remote_start" else source_points[-1]
            candidate_point = candidate_points[0] if endpoint == "remote_start" else candidate_points[-1]
            distance = math.dist(source_point, candidate_point)
        rows.append(
            {
                "edge_id": edge_id,
                "lane_index": lane_index,
                "endpoint": endpoint,
                "distance_m": distance,
                "status": "pass" if distance is not None and distance <= 0.05 else "fail",
            }
        )
    return {
        "status": "pass" if all(item["status"] == "pass" for item in rows) else "fail",
        "tolerance_m": 0.05,
        "checks": rows,
    }


def _audit_logical_cell(root: ET.Element) -> dict[str, Any]:
    junction_ids = {item.attrib.get("id", "") for item in root.findall("junction")}
    tls_ids = {item.attrib.get("id", "") for item in root.findall("tlLogic")}
    stale_bindings = [
        list(_connection_key(item))
        for item in root.findall("connection")
        if item.attrib.get("tl") == LEGACY_TLS_ID
    ]
    return {
        "status": (
            "pass"
            if JOINED_JUNCTION_ID in junction_ids
            and TARGET_TLS_ID in tls_ids
            and LEGACY_TLS_ID not in tls_ids
            and not stale_bindings
            else "fail"
        ),
        "joined_junction_id": JOINED_JUNCTION_ID,
        "official_tls_id": TARGET_TLS_ID,
        "legacy_tls_program_present": LEGACY_TLS_ID in tls_ids,
        "legacy_tls_bindings": stale_bindings,
    }


def _validate_plain_absorbed_edges(path: Path) -> None:
    root = ET.parse(path).getroot()
    edges = {item.attrib.get("id", ""): item for item in root.findall("edge")}
    expected = {
        "61649647#1": (JOIN_GROUP[0], JOIN_GROUP[1]),
        "61649647#2": (JOIN_GROUP[1], JOIN_GROUP[2]),
    }
    for edge_id, endpoints in expected.items():
        item = edges.get(edge_id)
        if item is None or (item.attrib.get("from"), item.attrib.get("to")) != endpoints:
            raise Hamburg2349ChannelizedGeometryError("plain export changed an absorbed edge")


def _validate_join_definition(report: Mapping[str, Any]) -> None:
    joins = [
        item
        for item in report.get("records", [])
        if isinstance(item, Mapping) and item.get("action") == "join"
    ]
    if report.get("status") != "pass" or len(joins) != 1:
        raise Hamburg2349ChannelizedGeometryError("join helper did not emit exactly one join")
    if set(joins[0].get("node_ids", [])) != set(JOIN_GROUP):
        raise Hamburg2349ChannelizedGeometryError("join helper changed the exact 2349 group")


def _one_connection(
    connections: Sequence[ET.Element],
    key: tuple[str, str, str, str],
) -> ET.Element:
    matches = [item for item in connections if _connection_key(item) == key]
    if len(matches) != 1:
        raise Hamburg2349ChannelizedGeometryError(
            f"expected one compiled connection for {key}, got {len(matches)}"
        )
    return matches[0]


def _connection_key(item: ET.Element) -> tuple[str, str, str, str]:
    return (
        item.attrib.get("from", ""),
        item.attrib.get("fromLane", ""),
        item.attrib.get("to", ""),
        item.attrib.get("toLane", ""),
    )


def _normal_edge_ids(root: ET.Element) -> set[str]:
    return {
        item.attrib.get("id", "")
        for item in root.findall("edge")
        if item.attrib.get("id", "")
        and not item.attrib.get("id", "").startswith(":")
        and item.attrib.get("function", "") not in {"internal", "crossing", "walkingarea"}
    }


def _lane_shapes_by_key(root: ET.Element) -> dict[tuple[str, int], list[tuple[float, float]]]:
    result = {}
    for edge in root.findall("edge"):
        for lane in edge.findall("lane"):
            try:
                index = int(lane.attrib.get("index", "0"))
            except ValueError:
                continue
            result[(edge.attrib.get("id", ""), index)] = _parse_shape(lane.attrib.get("shape", ""))
    return result


def _tls_program_signatures(root: ET.Element, *, exclude: set[str]) -> set[str]:
    result = set()
    for logic in root.findall("tlLogic"):
        if logic.attrib.get("id", "") in exclude:
            continue
        header = ",".join(f"{key}={value}" for key, value in sorted(logic.attrib.items()))
        phases = ";".join(
            ",".join(f"{key}={value}" for key, value in sorted(phase.attrib.items()))
            for phase in logic.findall("phase")
        )
        result.add(f"{header}|{phases}")
    return result


def _joined_cell_shape(root: ET.Element) -> str:
    points = []
    for junction in root.findall("junction"):
        if junction.attrib.get("id", "") in JOIN_GROUP:
            points.extend(_parse_shape(junction.attrib.get("shape", "")))
    hull = _convex_hull(points)
    if len(hull) < 3:
        raise Hamburg2349ChannelizedGeometryError(
            "source junction polygons cannot define the joined 2349 cell"
        )
    return _shape_text(hull)


def _convex_hull(points: Sequence[tuple[float, float]]) -> list[tuple[float, float]]:
    ordered = sorted(set(points))
    if len(ordered) <= 1:
        return ordered

    def cross(
        origin: tuple[float, float],
        first: tuple[float, float],
        second: tuple[float, float],
    ) -> float:
        return (first[0] - origin[0]) * (second[1] - origin[1]) - (
            first[1] - origin[1]
        ) * (second[0] - origin[0])

    lower: list[tuple[float, float]] = []
    for point in ordered:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], point) <= 0:
            lower.pop()
        lower.append(point)
    upper: list[tuple[float, float]] = []
    for point in reversed(ordered):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], point) <= 0:
            upper.pop()
        upper.append(point)
    return lower[:-1] + upper[:-1]


def _join_points(parts: Sequence[Sequence[tuple[float, float]]]) -> list[tuple[float, float]]:
    result: list[tuple[float, float]] = []
    for part in parts:
        for point in part:
            if not result or math.dist(result[-1], point) > 1e-6:
                result.append(point)
    return result


def _parse_shape(value: str) -> list[tuple[float, float]]:
    result = []
    for token in value.split():
        try:
            x, y = token.split(",", 1)
            result.append((float(x), float(y)))
        except (TypeError, ValueError):
            return []
    return result


def _shape_text(points: Sequence[tuple[float, float]]) -> str:
    return " ".join(f"{x:.6f},{y:.6f}" for x, y in points)


def _symmetric_polyline_distance(
    first: Sequence[tuple[float, float]],
    second: Sequence[tuple[float, float]],
) -> float | None:
    if len(first) < 2 or len(second) < 2:
        return None
    return max(
        max(_point_polyline_distance(point, second) for point in first),
        max(_point_polyline_distance(point, first) for point in second),
    )


def _point_polyline_distance(
    point: tuple[float, float],
    line: Sequence[tuple[float, float]],
) -> float:
    return min(_point_segment_distance(point, start, end) for start, end in zip(line, line[1:]))


def _point_segment_distance(
    point: tuple[float, float],
    start: tuple[float, float],
    end: tuple[float, float],
) -> float:
    dx, dy = end[0] - start[0], end[1] - start[1]
    if dx == 0 and dy == 0:
        return math.dist(point, start)
    fraction = max(
        0.0,
        min(1.0, ((point[0] - start[0]) * dx + (point[1] - start[1]) * dy) / (dx * dx + dy * dy)),
    )
    return math.dist(point, (start[0] + fraction * dx, start[1] + fraction * dy))


def _movement_record(item: ChannelizedMovement) -> dict[str, Any]:
    return {
        "movement_id": item.movement_id,
        "from_edge": INCOMING_EDGE_ID,
        "from_lane": item.through_lane,
        "to_edge": item.to_edge,
        "to_lane": item.to_lane,
        "tls_id": TARGET_TLS_ID,
        "link_index": item.link_index,
    }


def _persist_reanchored_connections(path: Path, direct: Sequence[ET.Element]) -> None:
    tree = ET.parse(path)
    replacements = {_connection_key(item): item.attrib.get("shape", "") for item in direct}
    found = set()
    for item in tree.getroot().findall("connection"):
        key = _connection_key(item)
        if key in replacements:
            item.set("shape", replacements[key])
            found.add(key)
    if found != set(replacements):
        raise Hamburg2349ChannelizedGeometryError(
            "staged direct connection set changed before geometry re-anchoring"
        )
    _write_tree(path, tree)


def _required_export_path(export: Mapping[str, Any], field: str) -> Path:
    value = str(export.get(field, ""))
    path = Path(value).resolve() if value else Path()
    if not value or not path.is_file():
        raise Hamburg2349ChannelizedGeometryError(f"plain export did not produce {field}")
    return path


def _optional_export_path(export: Mapping[str, Any], field: str) -> Path | None:
    value = str(export.get(field, ""))
    if not value:
        return None
    path = Path(value).resolve()
    if not path.is_file():
        raise Hamburg2349ChannelizedGeometryError(f"plain export did not produce {field}")
    return path


def _write_tree(path: Path, tree: ET.ElementTree) -> None:
    ET.indent(tree, space="    ")
    path.parent.mkdir(parents=True, exist_ok=True)
    tree.write(path, encoding="utf-8", xml_declaration=True)


def _result_dict(result: Any) -> dict[str, Any]:
    if hasattr(result, "to_dict"):
        value = dict(result.to_dict())
    elif isinstance(result, Mapping):
        value = dict(result)
    else:
        value = {"status": getattr(result, "status", "fail"), "returncode": getattr(result, "returncode", None)}
    if "status" not in value:
        value["status"] = "pass" if value.get("returncode") == 0 else "fail"
    return value


def _netconvert_passed(result: Mapping[str, Any], candidate: Path) -> bool:
    return result.get("status") == "pass" and result.get("returncode") in {0, None} and candidate.is_file()


def _artifact(path: Path) -> dict[str, Any]:
    return {"path": str(path), "exists": path.is_file(), "sha256": file_sha256(path) if path.is_file() else None}


def _require_sha256(value: str) -> None:
    if len(value) != 64 or any(character not in "0123456789abcdefABCDEF" for character in value):
        raise Hamburg2349ChannelizedGeometryError("expected_source_sha256 must be 64 hex characters")


def _is_within(path: Path, directory: Path) -> bool:
    try:
        path.resolve().relative_to(directory.resolve())
        return True
    except ValueError:
        return False


def _blocked_manifest(
    manifest_file: Path,
    *,
    reason: str,
    source: Path,
    source_hash: str,
    export: Mapping[str, Any],
    details: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    manifest = {
        "schema": SCHEMA,
        "status": "blocked",
        "claim_status": "construction_invalid",
        "automatic_promotion_gate": "blocked",
        "reason": reason,
        "source": {
            "path": str(source),
            "sha256_before": source_hash,
            "sha256_after": file_sha256(source),
        },
        "plain_export": dict(export),
        "details": dict(details or {}),
        "artifacts": {"manifest": str(manifest_file)},
    }
    write_json_atomic(manifest_file, manifest, sort_keys=True)
    return manifest
