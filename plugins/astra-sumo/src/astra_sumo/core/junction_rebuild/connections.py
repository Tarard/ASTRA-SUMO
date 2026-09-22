"""Build teacher connection plans and bounded connection-repair candidates."""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from pathlib import Path
from .artifacts import _failure, _stage_file
from .geometry import _translate_shape
from .network import (
    TLS_CONNECTION_REPAIR_ATTRS,
    _approach_edges,
    _candidate_lane_counts,
    _connection_edges_are_adjacent,
    _connection_key_record,
    _connection_lane_indices_valid,
    _connection_link_indices,
    _controlled_tls_connection_count,
    _edge_file_lane_counts,
    _net_lane_counts,
    _plain_crossing_node_id,
    _plain_edge_endpoints,
    _split,
    _unique_connections_by_key,
)
from .tls import (
    _add_green_phases_for_links,
    _capacity_gap_records,
    _connection_link_indices_fit,
    _connection_max_link_index,
    _copy_referenced_tllogics,
    _pad_tllogic_state_lengths,
    _record_linkindex_capacity_gap,
    _tllogic_min_state_length_by_id,
)


def build_tls_connection_repair_variant(
    *,
    source_net_file: Path,
    candidate_net_file: Path,
    output_dir: Path,
    prefix: str = "tls_connection_repair",
    tls_id_map: dict[str, str] | None = None,
    copy_unmapped_tls: bool = True,
    require_target_link_index_capacity: bool = False,
    pad_mapped_tllogic_capacity: bool = False,
    add_green_phases_for_padded_links: bool = False,
    add_yellow_phases_for_generated_green: bool = False,
) -> dict[str, object]:
    if not source_net_file.exists():
        return _failure(f"source net file does not exist: {source_net_file}")
    if not candidate_net_file.exists():
        return _failure(f"candidate net file does not exist: {candidate_net_file}")

    source_net_file = source_net_file.resolve()
    candidate_net_file = candidate_net_file.resolve()
    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    variant_file = _stage_file(output_dir, prefix, "tls_connection_repaired.net.xml")
    summary_file = _stage_file(output_dir, prefix, "tls_connection_repair.json")

    source_root = ET.parse(source_net_file).getroot()
    candidate_tree = ET.parse(candidate_net_file)
    candidate_root = candidate_tree.getroot()
    tls_id_map = tls_id_map or {}
    candidate_tllogic_ids = {
        tl_logic.attrib["id"]
        for tl_logic in candidate_root.findall("tlLogic")
        if tl_logic.attrib.get("id")
    }
    target_tllogic_capacities = _tllogic_min_state_length_by_id(candidate_root)
    source_unique, source_duplicate_keys = _unique_connections_by_key(source_root)
    candidate_unique, candidate_duplicate_keys = _unique_connections_by_key(candidate_root)
    source_controlled = _controlled_tls_connection_count(source_root)
    candidate_controlled_before = _controlled_tls_connection_count(candidate_root)
    matched_connections = 0
    updated_connections = 0
    missing_candidate_connections = 0
    ambiguous_connections = 0
    skipped_unmapped_tls_connections = 0
    skipped_missing_mapped_tllogic_connections = 0
    skipped_invalid_mapped_linkindex_connections = 0
    invalid_mapped_linkindex_capacity_gaps: dict[str, dict[str, object]] = {}
    required_tllogic_lengths: dict[str, int] = {}
    padded_tllogic_link_indices: dict[str, set[int]] = {}
    copied_tls_ids: set[str] = set()
    updated_keys: list[dict[str, str]] = []

    for key, source_connection in sorted(source_unique.items()):
        tls_id = source_connection.attrib.get("tl", "")
        if not tls_id or not source_connection.attrib.get("linkIndex"):
            continue
        target_tls_id = tls_id_map.get(tls_id, tls_id)
        if tls_id not in tls_id_map and not copy_unmapped_tls:
            skipped_unmapped_tls_connections += 1
            continue
        if tls_id in tls_id_map and target_tls_id not in candidate_tllogic_ids:
            skipped_missing_mapped_tllogic_connections += 1
            continue
        if (
            require_target_link_index_capacity
            and tls_id in tls_id_map
            and not _connection_link_indices_fit(
                source_connection,
                target_tllogic_capacities.get(target_tls_id),
            )
        ):
            _record_linkindex_capacity_gap(
                invalid_mapped_linkindex_capacity_gaps,
                source_connection=source_connection,
                source_tls_id=tls_id,
                target_tls_id=target_tls_id,
                capacity=target_tllogic_capacities.get(target_tls_id),
            )
            capacity = target_tllogic_capacities.get(target_tls_id)
            if not pad_mapped_tllogic_capacity or capacity is None:
                skipped_invalid_mapped_linkindex_connections += 1
                continue
            required_tllogic_lengths[target_tls_id] = max(
                required_tllogic_lengths.get(target_tls_id, 0),
                _connection_max_link_index(source_connection) + 1,
            )
            padded_tllogic_link_indices.setdefault(target_tls_id, set()).update(_connection_link_indices(source_connection))
        if key in source_duplicate_keys or key in candidate_duplicate_keys:
            ambiguous_connections += 1
            continue
        candidate_connection = candidate_unique.get(key)
        if candidate_connection is None:
            missing_candidate_connections += 1
            continue
        matched_connections += 1
        before = dict(candidate_connection.attrib)
        for attr in TLS_CONNECTION_REPAIR_ATTRS:
            if attr in source_connection.attrib:
                candidate_connection.set(attr, source_connection.attrib[attr])
            else:
                candidate_connection.attrib.pop(attr, None)
        candidate_connection.set("tl", target_tls_id)
        candidate_connection.attrib.pop("uncontrolled", None)
        if target_tls_id == tls_id:
            copied_tls_ids.add(tls_id)
        if dict(candidate_connection.attrib) != before:
            updated_connections += 1
            updated_keys.append(_connection_key_record(key))

    tl_logic_report = _copy_referenced_tllogics(source_root, candidate_root, copied_tls_ids)
    padding_report = _pad_tllogic_state_lengths(candidate_root, required_tllogic_lengths)
    green_phase_report = (
        _add_green_phases_for_links(
            candidate_root,
            padded_tllogic_link_indices,
            add_yellow_phases=add_yellow_phases_for_generated_green,
        )
        if add_green_phases_for_padded_links
        else {
            "added_green_phase_count": 0,
            "added_green_phase_tllogic_count": 0,
            "added_green_phase_tllogics": [],
            "added_yellow_phase_count": 0,
            "added_yellow_phase_tllogic_count": 0,
            "added_yellow_phase_tllogics": [],
        }
    )
    candidate_controlled_after = _controlled_tls_connection_count(candidate_root)
    ET.indent(candidate_root, space="    ")
    candidate_tree.write(variant_file, encoding="utf-8", xml_declaration=True)

    report: dict[str, object] = {
        "status": "pass",
        "claim_status": "diagnostic-demo",
        "source_net_file": str(source_net_file),
        "candidate_net_file": str(candidate_net_file),
        "variant_file": str(variant_file),
        "summary_file": str(summary_file),
        "source_tls_controlled_connection_count": source_controlled,
        "candidate_tls_controlled_connection_count_before": candidate_controlled_before,
        "candidate_tls_controlled_connection_count_after": candidate_controlled_after,
        "matched_connection_count": matched_connections,
        "updated_connection_count": updated_connections,
        "missing_candidate_connection_count": missing_candidate_connections,
        "skipped_ambiguous_connection_count": ambiguous_connections,
        "skipped_unmapped_tls_connection_count": skipped_unmapped_tls_connections,
        "skipped_missing_mapped_tllogic_connection_count": skipped_missing_mapped_tllogic_connections,
        "skipped_invalid_mapped_linkindex_connection_count": skipped_invalid_mapped_linkindex_connections,
        "invalid_mapped_linkindex_capacity_gaps": _capacity_gap_records(invalid_mapped_linkindex_capacity_gaps),
        "source_duplicate_connection_key_count": len(source_duplicate_keys),
        "candidate_duplicate_connection_key_count": len(candidate_duplicate_keys),
        "tls_id_map_count": len(tls_id_map),
        "copy_unmapped_tls": copy_unmapped_tls,
        "require_target_link_index_capacity": require_target_link_index_capacity,
        "pad_mapped_tllogic_capacity": pad_mapped_tllogic_capacity,
        "add_green_phases_for_padded_links": add_green_phases_for_padded_links,
        "updated_connection_keys": updated_keys,
        **tl_logic_report,
        **padding_report,
        **green_phase_report,
        "review_policy": (
            "diagnostic variant only: run SUMO load and NetEdit connection-mode review before adoption; "
            "this repair copies TLS control attributes without changing edge, junction, via, or shape geometry"
        ),
    }
    summary_file.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    return report


def write_teacher_connection_plan(
    *,
    raw_connection_file: Path,
    output_file: Path,
    junction_id: str,
    teacher_model: dict[str, object],
    candidate_model: dict[str, object],
    edge_map: dict[str, str],
    crossing_edge_overrides: dict[str, str | list[str]] | None = None,
    candidate_edge_file: Path | None = None,
    crossing_node_ids: set[str] | None = None,
    emit_crossings: bool = True,
    teacher_internal_scope_id: str | None = None,
) -> dict[str, object]:
    crossing_edge_overrides = crossing_edge_overrides or {}
    crossing_node_ids = crossing_node_ids or set()
    output_file.parent.mkdir(parents=True, exist_ok=True)

    incoming = _approach_edges(candidate_model, "incoming")
    outgoing = _approach_edges(candidate_model, "outgoing")
    target_incoming_edges = set(incoming)
    target_outgoing_edges = set(outgoing)
    candidate_lane_counts = _candidate_lane_counts(candidate_model)
    present_candidate_edges: set[str] | None = None
    if candidate_edge_file is not None:
        patched_lane_counts = _edge_file_lane_counts(candidate_edge_file)
        candidate_lane_counts.update(patched_lane_counts)
        present_candidate_edges = set(patched_lane_counts)
        incoming = [edge for edge in incoming if edge in present_candidate_edges]
        outgoing = [edge for edge in outgoing if edge in present_candidate_edges]
    patched_edge_endpoints = _plain_edge_endpoints(candidate_edge_file) if candidate_edge_file is not None else {}

    root = ET.Element("connections")
    kept = 0
    removed = 0
    removed_invalid_lane_connections = []
    removed_nonadjacent_connections = []
    for child in ET.parse(raw_connection_file).getroot():
        if (
            child.tag == "connection"
            and present_candidate_edges is not None
            and (
                child.attrib.get("from", "") not in present_candidate_edges
                or child.attrib.get("to", "") not in present_candidate_edges
            )
        ):
            removed += 1
            continue
        if (
            child.tag == "connection"
            and present_candidate_edges is not None
            and not _connection_lane_indices_valid(child, candidate_lane_counts)
        ):
            removed_invalid_lane_connections.append(dict(child.attrib))
            removed += 1
            continue
        if (
            child.tag == "connection"
            and present_candidate_edges is not None
            and not _connection_edges_are_adjacent(child, patched_edge_endpoints)
        ):
            removed_nonadjacent_connections.append(dict(child.attrib))
            removed += 1
            continue
        if child.tag == "connection" and (
            child.attrib.get("from", "") in target_incoming_edges
            and child.attrib.get("to", "") in target_outgoing_edges
        ):
            removed += 1
            continue
        if child.tag == "crossing" and present_candidate_edges is not None:
            crossing_edges = set(_split(child.attrib.get("edges", "")))
            if crossing_edges and not crossing_edges <= present_candidate_edges:
                removed += 1
                continue
        if child.tag == "crossing" and child.attrib.get("node") == junction_id:
            removed += 1
            continue
        root.append(child)
        kept += 1

    emitted_connections = 0
    emitted_uncontrolled_connections = 0
    allowed_pairs: set[tuple[str, str]] = set()
    skipped_off_scope_pairs: set[tuple[str, str]] = set()
    seen_connections: set[tuple[str, str, str, str]] = set()
    lane_clamps = []
    skipped_off_scope_internal_connections = []
    teacher_internal_scope_prefix = f":{teacher_internal_scope_id}_" if teacher_internal_scope_id else ""
    for connection in teacher_model.get("vehicle_connections", []) or []:
        if not isinstance(connection, dict):
            continue
        source = edge_map.get(str(connection.get("from", "")))
        target = edge_map.get(str(connection.get("to", "")))
        if not source or not target:
            continue
        via = str(connection.get("via", ""))
        if teacher_internal_scope_prefix and via.startswith(":") and not via.startswith(teacher_internal_scope_prefix):
            skipped_off_scope_pairs.add((source, target))
            skipped_off_scope_internal_connections.append(dict(connection))
            continue
        if present_candidate_edges is not None and (source not in present_candidate_edges or target not in present_candidate_edges):
            continue
        original_from_lane = int(connection.get("fromLane") or 0)
        original_to_lane = int(connection.get("toLane") or 0)
        from_lane = min(original_from_lane, candidate_lane_counts.get(source, 1) - 1)
        to_lane = min(original_to_lane, candidate_lane_counts.get(target, 1) - 1)
        if (from_lane, to_lane) != (original_from_lane, original_to_lane):
            lane_clamps.append(
                {
                    "candidate_from": source,
                    "candidate_to": target,
                    "fromLane": original_from_lane,
                    "toLane": original_to_lane,
                    "clamped_fromLane": from_lane,
                    "clamped_toLane": to_lane,
                }
            )
        key = (source, target, str(from_lane), str(to_lane))
        allowed_pairs.add((source, target))
        if key in seen_connections:
            continue
        seen_connections.add(key)
        attributes = {"from": source, "to": target, "fromLane": str(from_lane), "toLane": str(to_lane)}
        if not str(connection.get("tl", "")):
            attributes["uncontrolled"] = "true"
            emitted_uncontrolled_connections += 1
        ET.SubElement(root, "connection", attributes)
        emitted_connections += 1

    emitted_deletes = 0
    for source in sorted(incoming):
        for target in sorted(outgoing):
            if (source, target) in allowed_pairs:
                continue
            if (source, target) in skipped_off_scope_pairs:
                continue
            ET.SubElement(root, "delete", {"from": source, "to": target})
            emitted_deletes += 1

    emitted_crossings = 0
    skipped_crossings = []
    crossing_node_rewrites = []
    if emit_crossings:
        for crossing in teacher_model.get("crossings", []) or []:
            if not isinstance(crossing, dict):
                continue
            crossing_id = str(crossing.get("edge_id", ""))
            crossing_edges = crossing_edge_overrides.get(crossing_id)
            if crossing_edges is None:
                crossing_edges = [edge_map.get(str(edge), "") for edge in crossing.get("crossingEdges", []) or []]
            if isinstance(crossing_edges, str):
                crossing_edges = [crossing_edges]
            crossing_edges = [edge for edge in crossing_edges if edge]
            if present_candidate_edges is not None:
                crossing_edges = [edge for edge in crossing_edges if edge in present_candidate_edges]
            if not crossing_edges:
                skipped_crossings.append(crossing_id)
                continue
            crossing_node_id = _plain_crossing_node_id(
                junction_id,
                crossing_edges,
                patched_edge_endpoints,
                crossing_node_ids,
            )
            if crossing_node_id != junction_id:
                crossing_node_rewrites.append(
                    {
                        "crossing_id": crossing_id,
                        "from": junction_id,
                        "to": crossing_node_id,
                        "edges": crossing_edges,
                    }
                )
            ET.SubElement(
                root,
                "crossing",
                {"node": crossing_node_id, "edges": " ".join(crossing_edges), "priority": "1", "width": "4.00"},
            )
            emitted_crossings += 1

    ET.indent(root, space="    ")
    ET.ElementTree(root).write(output_file, encoding="utf-8", xml_declaration=True)
    return {
        "status": "pass",
        "claim_status": "diagnostic-demo",
        "connection_file": str(output_file),
        "kept_non_target_children": kept,
        "removed_target_children": removed,
        "removed_invalid_lane_connection_count": len(removed_invalid_lane_connections),
        "removed_invalid_lane_connections": removed_invalid_lane_connections,
        "removed_nonadjacent_connection_count": len(removed_nonadjacent_connections),
        "removed_nonadjacent_connections": removed_nonadjacent_connections,
        "emitted_connection_count": emitted_connections,
        "emitted_uncontrolled_connection_count": emitted_uncontrolled_connections,
        "emitted_delete_count": emitted_deletes,
        "emitted_crossing_count": emitted_crossings,
        "emit_crossings": emit_crossings,
        "skipped_crossings": skipped_crossings,
        "crossing_node_rewrite_count": len(crossing_node_rewrites),
        "crossing_node_rewrites": crossing_node_rewrites,
        "lane_clamp_count": len(lane_clamps),
        "lane_clamps": lane_clamps,
        "skipped_off_scope_internal_connection_count": len(skipped_off_scope_internal_connections),
        "skipped_off_scope_internal_connections": skipped_off_scope_internal_connections,
    }


def write_teacher_vehicle_connection_attrs_net(
    *,
    candidate_net_file: Path,
    output_file: Path,
    junction_id: str,
    teacher_model: dict[str, object],
    edge_map: dict[str, str],
) -> dict[str, object]:
    output_file.parent.mkdir(parents=True, exist_ok=True)
    tree = ET.parse(candidate_net_file)
    root = tree.getroot()
    lane_counts = _net_lane_counts(root)
    shape_delta = None
    teacher_junction = teacher_model.get("junction", {}) if isinstance(teacher_model.get("junction"), dict) else {}
    candidate_junction = next((item for item in root.findall("junction") if item.attrib.get("id") == junction_id), None)
    if candidate_junction is not None:
        try:
            shape_delta = (
                float(candidate_junction.attrib.get("x", "")) - float(teacher_junction.get("x", "")),
                float(candidate_junction.attrib.get("y", "")) - float(teacher_junction.get("y", "")),
            )
        except (TypeError, ValueError):
            shape_delta = None

    connections_by_key: dict[tuple[str, str, str, str], list[ET.Element]] = {}
    for connection in root.findall("connection"):
        key = (
            connection.attrib.get("from", ""),
            connection.attrib.get("to", ""),
            connection.attrib.get("fromLane", "0"),
            connection.attrib.get("toLane", "0"),
        )
        connections_by_key.setdefault(key, []).append(connection)

    updated = 0
    skipped = []
    for teacher_connection in teacher_model.get("vehicle_connections", []) or []:
        if not isinstance(teacher_connection, dict):
            continue
        source = edge_map.get(str(teacher_connection.get("from", "")))
        target = edge_map.get(str(teacher_connection.get("to", "")))
        if not source or not target:
            skipped.append({"reason": "unmapped_edge", "connection": teacher_connection})
            continue
        from_lane = min(int(teacher_connection.get("fromLane") or 0), lane_counts.get(source, 1) - 1)
        to_lane = min(int(teacher_connection.get("toLane") or 0), lane_counts.get(target, 1) - 1)
        matches = connections_by_key.get((source, target, str(from_lane), str(to_lane)), [])
        if not matches:
            skipped.append({"reason": "missing_candidate_connection", "connection": teacher_connection})
            continue
        for connection in matches:
            for attr in ("dir", "state"):
                if teacher_connection.get(attr):
                    connection.set(attr, str(teacher_connection[attr]))
            for attr in ("linkIndex2", "pass", "allow", "disallow", "keepClear", "contPos"):
                if teacher_connection.get(attr):
                    connection.set(attr, str(teacher_connection[attr]))
                else:
                    connection.attrib.pop(attr, None)
            if teacher_connection.get("shape") and shape_delta is not None:
                connection.set("shape", _translate_shape(str(teacher_connection["shape"]), shape_delta[0], shape_delta[1]))
            if teacher_connection.get("tl"):
                connection.set("tl", junction_id)
                connection.set("linkIndex", str(teacher_connection.get("linkIndex", "")))
                if teacher_connection.get("uncontrolled"):
                    connection.set("uncontrolled", str(teacher_connection["uncontrolled"]))
                else:
                    connection.attrib.pop("uncontrolled", None)
            else:
                connection.attrib.pop("tl", None)
                connection.attrib.pop("linkIndex", None)
                connection.set("uncontrolled", "true")
            updated += 1

    ET.indent(root, space="    ")
    tree.write(output_file, encoding="utf-8", xml_declaration=True)
    return {
        "status": "pass",
        "claim_status": "diagnostic-demo",
        "net_file": str(output_file),
        "updated_vehicle_connection_count": updated,
        "skipped_vehicle_connection_count": len(skipped),
        "skipped_vehicle_connections": skipped,
    }
