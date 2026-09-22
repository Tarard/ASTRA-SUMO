"""Rebuild and restore scoped traffic-signal definitions and connection bindings."""

from __future__ import annotations

import copy
from collections import Counter
import xml.etree.ElementTree as ET
from pathlib import Path
from .artifacts import _failure
from .network import TLS_CONNECTION_REPAIR_ATTRS
from .scope import _local_junction_context_summary


def _remove_teacher_non_tls_tllogics(
    *,
    teacher_net_file: Path,
    target_file: Path,
) -> dict[str, object]:
    if not teacher_net_file.exists():
        return _failure(f"teacher net file does not exist: {teacher_net_file}")
    if not target_file.exists():
        return _failure(f"target net file does not exist: {target_file}")

    teacher_root = ET.parse(teacher_net_file).getroot()
    target_tree = ET.parse(target_file)
    target_root = target_tree.getroot()
    teacher_tl_logic_ids = {
        tl_logic.attrib.get("id", "")
        for tl_logic in teacher_root.findall("tlLogic")
        if tl_logic.attrib.get("id")
    }
    teacher_non_tls_types = {
        junction.attrib.get("id", ""): junction.attrib.get("type", "")
        for junction in teacher_root.findall("junction")
        if junction.attrib.get("id")
        and not junction.attrib.get("id", "").startswith(":")
        and junction.attrib.get("id", "") not in teacher_tl_logic_ids
        and junction.attrib.get("type") not in {"", "traffic_light"}
    }
    removed_ids = []
    for tl_logic in list(target_root.findall("tlLogic")):
        tls_id = tl_logic.attrib.get("id", "")
        if tls_id not in teacher_non_tls_types:
            continue
        target_root.remove(tl_logic)
        removed_ids.append(tls_id)

    uncontrolled_connections = []
    removed_id_set = set(removed_ids)
    for connection in target_root.findall("connection"):
        if connection.attrib.get("tl") not in removed_id_set:
            continue
        uncontrolled_connections.append(dict(connection.attrib))
        for attr in ("tl", "linkIndex", "linkIndex2"):
            connection.attrib.pop(attr, None)
        connection.set("uncontrolled", "true")

    restored_junction_ids = []
    for junction_id in removed_ids:
        junction = target_root.find(f"junction[@id='{junction_id}']")
        if junction is None:
            continue
        teacher_type = teacher_non_tls_types[junction_id]
        if junction.attrib.get("type") == teacher_type:
            continue
        junction.set("type", teacher_type)
        restored_junction_ids.append(junction_id)

    if removed_ids or uncontrolled_connections or restored_junction_ids:
        ET.indent(target_root, space="    ")
        target_tree.write(target_file, encoding="utf-8", xml_declaration=True)

    return {
        "status": "pass",
        "claim_status": "diagnostic-demo",
        "teacher_net_file": str(teacher_net_file),
        "target_file": str(target_file),
        "removed_teacher_non_tls_tllogic_count": len(removed_ids),
        "removed_teacher_non_tls_tllogic_ids": removed_ids,
        "uncontrolled_teacher_non_tls_connection_count": len(uncontrolled_connections),
        "uncontrolled_teacher_non_tls_connections": uncontrolled_connections,
        "restored_teacher_non_tls_junction_type_count": len(restored_junction_ids),
        "restored_teacher_non_tls_junction_type_ids": restored_junction_ids,
    }


def _restore_false_traffic_light_junction_types(
    *,
    source_file: Path,
    target_file: Path,
    fallback_node_file: Path | None = None,
    exclude_junction_ids: set[str] | None = None,
) -> dict[str, object]:
    if not source_file.exists():
        return _failure(f"source net file does not exist: {source_file}")
    if not target_file.exists():
        return _failure(f"target net file does not exist: {target_file}")
    if fallback_node_file is not None and not fallback_node_file.exists():
        return _failure(f"fallback node file does not exist: {fallback_node_file}")

    source_root = ET.parse(source_file).getroot()
    target_tree = ET.parse(target_file)
    target_root = target_tree.getroot()
    source_types = {
        junction.attrib.get("id", ""): junction.attrib.get("type", "")
        for junction in source_root.findall("junction")
        if junction.attrib.get("id") and not junction.attrib.get("id", "").startswith(":")
    }
    fallback_types = {}
    if fallback_node_file is not None:
        fallback_types = {
            node.attrib.get("id", ""): node.attrib.get("type", "")
            for node in ET.parse(fallback_node_file).getroot().findall("node")
            if node.attrib.get("id")
        }
    tl_logic_ids = {tl.attrib.get("id", "") for tl in target_root.findall("tlLogic") if tl.attrib.get("id")}
    controlled_tls_ids = {
        connection.attrib.get("tl", "")
        for connection in target_root.findall("connection")
        if connection.attrib.get("tl")
    }
    exclude_junction_ids = exclude_junction_ids or set()
    restored_ids = []
    for junction in target_root.findall("junction"):
        junction_id = junction.attrib.get("id", "")
        source_type = source_types.get(junction_id, "")
        if source_type in {"", "traffic_light"}:
            source_type = fallback_types.get(junction_id, source_type)
        if (
            not junction_id
            or junction_id.startswith(":")
            or junction_id in exclude_junction_ids
            or junction.attrib.get("type") != "traffic_light"
            or source_type in {"", "traffic_light"}
            or junction_id in tl_logic_ids
            or junction_id in controlled_tls_ids
        ):
            continue
        junction.set("type", source_type)
        restored_ids.append(junction_id)

    if restored_ids:
        ET.indent(target_root, space="    ")
        target_tree.write(target_file, encoding="utf-8", xml_declaration=True)
    return {
        "status": "pass",
        "claim_status": "diagnostic-demo",
        "source_file": str(source_file),
        "target_file": str(target_file),
        "fallback_node_file": str(fallback_node_file) if fallback_node_file is not None else "",
        "restored_false_traffic_light_junction_type_count": len(restored_ids),
        "restored_false_traffic_light_junction_ids": restored_ids,
    }


def _tllogic_min_state_length_by_id(root: ET.Element) -> dict[str, int]:
    lengths_by_id = {}
    for tl_logic in root.findall("tlLogic"):
        tls_id = tl_logic.attrib.get("id", "")
        lengths = [
            len(phase.attrib.get("state", ""))
            for phase in tl_logic.findall("phase")
            if phase.attrib.get("state")
        ]
        if tls_id and lengths:
            lengths_by_id[tls_id] = min(lengths)
    return lengths_by_id


def _connection_link_indices_fit(connection: ET.Element, capacity: int | None) -> bool:
    if capacity is None:
        return False
    for attr in ("linkIndex", "linkIndex2"):
        value = connection.attrib.get(attr, "")
        if not value:
            continue
        try:
            if int(value) >= capacity:
                return False
        except ValueError:
            return False
    return True


def _record_linkindex_capacity_gap(
    gaps: dict[str, dict[str, object]],
    *,
    source_connection: ET.Element,
    source_tls_id: str,
    target_tls_id: str,
    capacity: int | None,
) -> None:
    record = gaps.setdefault(
        target_tls_id,
        {
            "target_tls": target_tls_id,
            "target_capacity": capacity if capacity is not None else 0,
            "max_required_link_index": 0,
            "skipped_connection_count": 0,
            "source_tls_ids": set(),
        },
    )
    record["skipped_connection_count"] = int(record["skipped_connection_count"]) + 1
    record["max_required_link_index"] = max(
        int(record["max_required_link_index"]),
        _connection_max_link_index(source_connection),
    )
    source_ids = record["source_tls_ids"]
    if isinstance(source_ids, set):
        source_ids.add(source_tls_id)


def _connection_max_link_index(connection: ET.Element) -> int:
    values = []
    for attr in ("linkIndex", "linkIndex2"):
        value = connection.attrib.get(attr, "")
        if value:
            try:
                values.append(int(value))
            except ValueError:
                pass
    return max(values) if values else 0


def _capacity_gap_records(gaps: dict[str, dict[str, object]]) -> list[dict[str, object]]:
    records = []
    for record in gaps.values():
        max_required = int(record["max_required_link_index"])
        source_ids = record["source_tls_ids"]
        records.append(
            {
                "target_tls": str(record["target_tls"]),
                "target_capacity": int(record["target_capacity"]),
                "required_state_length": max_required + 1,
                "max_required_link_index": max_required,
                "skipped_connection_count": int(record["skipped_connection_count"]),
                "source_tls_ids": sorted(source_ids) if isinstance(source_ids, set) else [],
            }
        )
    return sorted(records, key=lambda item: (-int(item["skipped_connection_count"]), str(item["target_tls"])))


def _add_green_phases_for_links(
    root: ET.Element,
    indices_by_tls: dict[str, set[int]],
    *,
    add_yellow_phases: bool = False,
) -> dict[str, object]:
    added_by_tls = []
    added_count = 0
    added_yellow_by_tls = []
    added_yellow_count = 0
    for tl_logic in root.findall("tlLogic"):
        tls_id = tl_logic.attrib.get("id", "")
        indices = sorted(indices_by_tls.get(tls_id, set()))
        if not indices:
            continue
        phases = tl_logic.findall("phase")
        state_length = max((len(phase.attrib.get("state", "")) for phase in phases), default=0)
        added_for_tls = 0
        added_yellow_for_tls = 0
        for index in indices:
            if index < 0 or index >= state_length or _phase_has_green_for_index(phases, index):
                continue
            state = "r" * index + "G" + "r" * (state_length - index - 1)
            ET.SubElement(tl_logic, "phase", {"duration": "4", "state": state})
            added_for_tls += 1
            if add_yellow_phases:
                yellow_state = "r" * index + "y" + "r" * (state_length - index - 1)
                ET.SubElement(tl_logic, "phase", {"duration": "3", "state": yellow_state})
                added_yellow_for_tls += 1
        if added_for_tls:
            added_by_tls.append({"tls": tls_id, "added_green_phase_count": added_for_tls})
            added_count += added_for_tls
        if added_yellow_for_tls:
            added_yellow_by_tls.append({"tls": tls_id, "added_yellow_phase_count": added_yellow_for_tls})
            added_yellow_count += added_yellow_for_tls
    return {
        "added_green_phase_count": added_count,
        "added_green_phase_tllogic_count": len(added_by_tls),
        "added_green_phase_tllogics": added_by_tls,
        "added_yellow_phase_count": added_yellow_count,
        "added_yellow_phase_tllogic_count": len(added_yellow_by_tls),
        "added_yellow_phase_tllogics": added_yellow_by_tls,
    }


def _phase_has_green_for_index(phases: list[ET.Element], index: int) -> bool:
    return any(
        len(phase.attrib.get("state", "")) > index and phase.attrib.get("state", "")[index] in {"G", "g"}
        for phase in phases
    )


def _pad_tllogic_state_lengths(root: ET.Element, required_lengths: dict[str, int]) -> dict[str, object]:
    padded_tls = []
    padded_phases = 0
    if not required_lengths:
        return {"padded_tllogic_count": 0, "padded_tllogic_phase_count": 0, "padded_tllogics": []}
    for tl_logic in root.findall("tlLogic"):
        tls_id = tl_logic.attrib.get("id", "")
        required_length = required_lengths.get(tls_id, 0)
        if required_length <= 0:
            continue
        phase_count = 0
        for phase in tl_logic.findall("phase"):
            state = phase.attrib.get("state", "")
            if state and len(state) < required_length:
                phase.set("state", state + ("r" * (required_length - len(state))))
                phase_count += 1
        if phase_count:
            padded_tls.append({"tls": tls_id, "required_state_length": required_length, "padded_phase_count": phase_count})
            padded_phases += phase_count
    return {
        "padded_tllogic_count": len(padded_tls),
        "padded_tllogic_phase_count": padded_phases,
        "padded_tllogics": padded_tls,
    }


def _copy_referenced_tllogics(
    source_root: ET.Element,
    candidate_root: ET.Element,
    tls_ids: set[str],
) -> dict[str, object]:
    source_by_id = {
        tl_logic.attrib["id"]: tl_logic
        for tl_logic in source_root.findall("tlLogic")
        if tl_logic.attrib.get("id")
    }
    copied = 0
    replaced = 0
    missing: list[str] = []
    insert_index = _tl_logic_insert_index(candidate_root)
    for tls_id in sorted(tls_ids):
        source = source_by_id.get(tls_id)
        if source is None:
            missing.append(tls_id)
            continue
        replacement = copy.deepcopy(source)
        target = next(
            (tl_logic for tl_logic in candidate_root.findall("tlLogic") if tl_logic.attrib.get("id") == tls_id),
            None,
        )
        if target is None:
            candidate_root.insert(insert_index, replacement)
            insert_index += 1
            copied += 1
            continue
        target_index = list(candidate_root).index(target)
        candidate_root.remove(target)
        candidate_root.insert(target_index, replacement)
        replaced += 1
    return {
        "copied_tllogic_count": copied,
        "replaced_tllogic_count": replaced,
        "missing_source_tllogic_count": len(missing),
        "missing_source_tllogic_ids": missing,
    }


def _tl_logic_insert_index(root: ET.Element) -> int:
    children = list(root)
    for index, child in enumerate(children):
        if child.tag == "tlLogic":
            return index
    for index, child in enumerate(children):
        if child.tag == "connection":
            return index
    return len(children)


def write_teacher_tllogic_net(
    *,
    candidate_net_file: Path,
    output_file: Path,
    junction_id: str,
    teacher_model: dict[str, object],
) -> dict[str, object]:
    output_file.parent.mkdir(parents=True, exist_ok=True)
    tree = ET.parse(candidate_net_file)
    root = tree.getroot()
    traffic_light = teacher_model.get("traffic_light", {})
    if not isinstance(traffic_light, dict):
        return _failure("teacher_model.traffic_light is missing")
    attributes = traffic_light.get("attributes", {})
    phases = traffic_light.get("phases", [])
    if not isinstance(attributes, dict) or not isinstance(phases, list):
        return _failure("teacher_model.traffic_light is invalid")
    target_tl = next((tl for tl in root.findall("tlLogic") if tl.attrib.get("id") == junction_id), None)
    if not attributes and not phases:
        if target_tl is not None:
            root.remove(target_tl)
        valid_tls_ids = {tl.attrib["id"] for tl in root.findall("tlLogic") if tl.attrib.get("id")}
        uncontrolled_count = 0
        for connection in root.findall("connection"):
            if connection.attrib.get("tl") == junction_id or (
                connection.attrib.get("tl") and connection.attrib.get("tl") not in valid_tls_ids
            ):
                connection.attrib.pop("tl", None)
                connection.attrib.pop("linkIndex", None)
                connection.set("uncontrolled", "true")
                uncontrolled_count += 1
        ET.indent(root, space="    ")
        tree.write(output_file, encoding="utf-8", xml_declaration=True)
        return {
            "status": "pass",
            "claim_status": "diagnostic-demo",
            "net_file": str(output_file),
            "tl_phase_count": 0,
            "tl_phase_state_lengths": [],
            "controlled_link_count": 0,
            "removed_controlled_link_count": uncontrolled_count,
            "tls_replay_status": "not_applicable_no_teacher_tllogic",
        }
    root_children = list(root)
    candidate_indexes = [
        idx for idx, child in enumerate(root_children) if child.tag == "tlLogic" or child.tag in {"junction", "connection"}
    ]
    index = min(candidate_indexes) if candidate_indexes else len(root_children)
    if target_tl is not None:
        root.remove(target_tl)
    replacement = ET.Element("tlLogic", {str(key): str(value) for key, value in attributes.items()})
    replacement.set("id", junction_id)
    for phase in phases:
        if isinstance(phase, dict):
            ET.SubElement(replacement, "phase", {str(key): str(value) for key, value in phase.items()})
    root.insert(index, replacement)

    ET.indent(root, space="    ")
    tree.write(output_file, encoding="utf-8", xml_declaration=True)
    controlled_links = [
        connection
        for connection in root.findall("connection")
        if connection.attrib.get("tl") == junction_id and connection.attrib.get("linkIndex")
    ]
    return {
        "status": "pass",
        "claim_status": "diagnostic-demo",
        "net_file": str(output_file),
        "tl_phase_count": len(replacement.findall("phase")),
        "tl_phase_state_lengths": sorted({len(phase.attrib.get("state", "")) for phase in replacement.findall("phase")}),
        "controlled_link_count": len(controlled_links),
    }


def restore_teacher_tls_connection_semantics_after_normalize(
    *,
    source_net_file: Path,
    target_net_file: Path,
    junction_id: str,
) -> dict[str, object]:
    """Restore teacher TLS attributes after netconvert rebuilds internal links.

    netconvert is authoritative for legal internal geometry and may recompute
    ``dir``/``state`` while assigning a different internal ``via`` suffix.  A
    scoped teacher replay needs the teacher control semantics on the resulting
    candidate connection, keyed by the stable ``linkIndex``.  This function
    refuses partial rebinding and reports every rewrite; it never creates a
    missing connection or changes the candidate's endpoints/lanes/via path.
    """

    if not source_net_file.exists() or not target_net_file.exists():
        return _failure("source or target normalized net file is missing")
    try:
        source_root = ET.parse(source_net_file).getroot()
        target_tree = ET.parse(target_net_file)
        target_root = target_tree.getroot()
    except (ET.ParseError, OSError, ValueError) as exc:
        return _failure(f"TLS semantic restore parse failed: {type(exc).__name__}: {exc}")

    source_connections = [
        connection
        for connection in source_root.findall("connection")
        if connection.attrib.get("tl") == junction_id and connection.attrib.get("linkIndex")
    ]
    target_connections = [
        connection
        for connection in target_root.findall("connection")
        if connection.attrib.get("tl") == junction_id and connection.attrib.get("linkIndex")
    ]

    def connection_semantic_key(connection: ET.Element) -> tuple[str, str, str, str, str]:
        """Identify a controlled movement without depending on the internal via id."""

        attrs = connection.attrib
        return (
            attrs.get("from", ""),
            attrs.get("to", ""),
            attrs.get("fromLane", ""),
            attrs.get("toLane", ""),
            attrs.get("linkIndex", ""),
        )

    source_semantic_key_counts = Counter(connection_semantic_key(connection) for connection in source_connections)
    retained_semantic_key_counts: Counter[tuple[str, str, str, str, str]] = Counter()
    removed_stale_target_connections: list[dict[str, str]] = []
    retained_target_connections: list[ET.Element] = []
    for target_connection in target_connections:
        semantic_key = connection_semantic_key(target_connection)
        if retained_semantic_key_counts[semantic_key] >= source_semantic_key_counts.get(semantic_key, 0):
            target_root.remove(target_connection)
            removed_stale_target_connections.append(
                {
                    "from": target_connection.attrib.get("from", ""),
                    "to": target_connection.attrib.get("to", ""),
                    "fromLane": target_connection.attrib.get("fromLane", ""),
                    "toLane": target_connection.attrib.get("toLane", ""),
                    "linkIndex": target_connection.attrib.get("linkIndex", ""),
                }
            )
            continue
        retained_semantic_key_counts[semantic_key] += 1
        retained_target_connections.append(target_connection)
    target_connections = retained_target_connections

    source_by_link_index: dict[str, list[ET.Element]] = {}
    target_by_link_index: dict[str, list[ET.Element]] = {}
    for connection in source_connections:
        source_by_link_index.setdefault(connection.attrib["linkIndex"], []).append(connection)
    for connection in target_connections:
        target_by_link_index.setdefault(connection.attrib["linkIndex"], []).append(connection)

    rewritten: list[dict[str, str]] = []
    missing_source_link_indices: list[str] = []
    extra_target_link_indices: list[str] = []
    used_source_counts: dict[str, int] = {}
    for link_index, target_items in sorted(target_by_link_index.items()):
        source_items = source_by_link_index.get(link_index, [])
        if len(target_items) > len(source_items):
            extra_target_link_indices.extend([link_index] * (len(target_items) - len(source_items)))
        for position, target_connection in enumerate(target_items):
            if position >= len(source_items):
                continue
            source_connection = source_items[position]
            before = dict(target_connection.attrib)
            for attr in TLS_CONNECTION_REPAIR_ATTRS:
                if attr in source_connection.attrib:
                    target_connection.set(attr, source_connection.attrib[attr])
                else:
                    target_connection.attrib.pop(attr, None)
            target_connection.set("tl", junction_id)
            target_connection.set("linkIndex", link_index)
            used_source_counts[link_index] = used_source_counts.get(link_index, 0) + 1
            if target_connection.attrib != before:
                rewritten.append(
                    {
                        "linkIndex": link_index,
                        "from": target_connection.attrib.get("from", ""),
                        "to": target_connection.attrib.get("to", ""),
                    }
                )
    for link_index, source_items in sorted(source_by_link_index.items()):
        if used_source_counts.get(link_index, 0) < len(source_items):
            missing_source_link_indices.extend(
                [link_index] * (len(source_items) - used_source_counts.get(link_index, 0))
            )

    source_tl_logic = next(
        (tl_logic for tl_logic in source_root.findall("tlLogic") if tl_logic.attrib.get("id") == junction_id),
        None,
    )
    target_tl_logic = next(
        (tl_logic for tl_logic in target_root.findall("tlLogic") if tl_logic.attrib.get("id") == junction_id),
        None,
    )
    tl_logic_status = "skipped"
    restored_tl_logic_phase_count = 0
    phase_state_length_mismatches: list[dict[str, int]] = []
    phase_state_extra_slot_warnings: list[dict[str, int]] = []
    if source_tl_logic is not None:
        if target_tl_logic is None:
            tl_logic_status = "blocked"
        else:
            source_phases = list(source_tl_logic.findall("phase"))
            numeric_link_indices = [
                int(connection.attrib["linkIndex"])
                for connection in source_connections
                if connection.attrib.get("linkIndex", "").isdigit()
            ]
            expected_phase_state_length = max(numeric_link_indices, default=-1) + 1
            for phase in source_phases:
                state = phase.attrib.get("state")
                if state is not None and len(state) != expected_phase_state_length:
                    evidence = {
                        "source_state_length": len(state),
                        "expected_phase_state_length": expected_phase_state_length,
                        "target_controlled_connection_count": len(target_connections),
                    }
                    if len(state) < expected_phase_state_length:
                        phase_state_length_mismatches.append(evidence)
                    else:
                        phase_state_extra_slot_warnings.append(evidence)
            for attr in ("type", "programID", "offset"):
                if attr in source_tl_logic.attrib:
                    target_tl_logic.set(attr, source_tl_logic.attrib[attr])
                else:
                    target_tl_logic.attrib.pop(attr, None)
            for phase in list(target_tl_logic.findall("phase")):
                target_tl_logic.remove(phase)
            for phase in source_phases:
                target_tl_logic.append(copy.deepcopy(phase))
            restored_tl_logic_phase_count = len(source_phases)
            tl_logic_status = "pass" if not phase_state_length_mismatches else "blocked"

    status = (
        "pass"
        if not missing_source_link_indices
        and not extra_target_link_indices
        and tl_logic_status != "blocked"
        else "blocked"
    )
    if rewritten or removed_stale_target_connections or (source_tl_logic is not None and target_tl_logic is not None):
        ET.indent(target_root, space="    ")
        target_tree.write(target_net_file, encoding="utf-8", xml_declaration=True)
    return {
        "status": status,
        "claim_status": "diagnostic-demo",
        "source_net_file": str(source_net_file),
        "target_net_file": str(target_net_file),
        "junction_id": junction_id,
        "source_controlled_connection_count": len(source_connections),
        "target_controlled_connection_count": len(target_connections),
        "removed_stale_target_connection_count": len(removed_stale_target_connections),
        "removed_stale_target_connections": removed_stale_target_connections,
        "rewritten_connection_count": len(rewritten),
        "rewritten_connections": rewritten,
        "missing_source_link_indices": missing_source_link_indices,
        "extra_target_link_indices": extra_target_link_indices,
        "tl_logic_status": tl_logic_status,
        "restored_tl_logic": source_tl_logic is not None and target_tl_logic is not None,
        "restored_tl_logic_phase_count": restored_tl_logic_phase_count,
        "phase_state_length_mismatches": phase_state_length_mismatches,
        "phase_state_extra_slot_warnings": phase_state_extra_slot_warnings,
        "policy": "restore teacher TLS attrs by linkIndex; preserve candidate endpoints/lanes/via",
    }


def _demote_teacher_absent_context_tls(
    *,
    teacher_net_file: Path,
    target_file: Path,
    accepted_internal_replays: list[dict[str, object]],
    radius_m: float = 100.0,
) -> dict[str, object]:
    if not teacher_net_file.exists():
        return _failure(f"teacher net file does not exist: {teacher_net_file}")
    if not target_file.exists():
        return _failure(f"target net file does not exist: {target_file}")
    if not accepted_internal_replays:
        return {"status": "pass", "claim_status": "diagnostic-demo", "demoted_context_tls_count": 0}
    try:
        teacher_root = ET.parse(teacher_net_file).getroot()
        target_tree = ET.parse(target_file)
    except (OSError, ET.ParseError) as exc:
        return _failure(f"parse_error: {exc}")
    target_root = target_tree.getroot()
    protected_candidate_tls_ids = {
        str(replay.get("junction_id", "")) for replay in accepted_internal_replays if str(replay.get("junction_id", ""))
    }

    candidate_tls_to_demote: set[str] = set()
    skipped_contexts: list[dict[str, object]] = []
    for replay in accepted_internal_replays:
        teacher_junction_id = str(replay.get("teacher_junction_id", ""))
        junction_id = str(replay.get("junction_id", ""))
        if not teacher_junction_id or not junction_id:
            continue
        teacher_context = _local_junction_context_summary(
            teacher_root,
            teacher_junction_id,
            radius_m=radius_m,
        )
        candidate_context = _local_junction_context_summary(
            target_root,
            junction_id,
            radius_m=radius_m,
        )
        if teacher_context.get("status") != "pass" or candidate_context.get("status") != "pass":
            skipped_contexts.append(
                {
                    "junction_id": junction_id,
                    "teacher_junction_id": teacher_junction_id,
                    "reason": "context_extract_failed",
                }
            )
            continue
        teacher_extra_tls_ids = (
            set(str(item) for item in teacher_context.get("traffic_light_junction_ids", []) if str(item))
            | set(str(item) for item in teacher_context.get("tl_logic_ids", []) if str(item))
        ) - {teacher_junction_id}
        if teacher_extra_tls_ids:
            skipped_contexts.append(
                {
                    "junction_id": junction_id,
                    "teacher_junction_id": teacher_junction_id,
                    "reason": "teacher_has_extra_context_tls",
                    "teacher_extra_tls_ids": sorted(teacher_extra_tls_ids),
                }
            )
            continue
        candidate_tls_to_demote.update(
            (
                set(str(item) for item in candidate_context.get("traffic_light_junction_ids", []) if str(item))
                | set(str(item) for item in candidate_context.get("tl_logic_ids", []) if str(item))
            )
            - protected_candidate_tls_ids
        )

    demoted_junction_ids = []
    for junction in target_root.findall("junction"):
        junction_id = str(junction.attrib.get("id", ""))
        if junction_id in candidate_tls_to_demote and junction.attrib.get("type") == "traffic_light":
            junction.set("type", "priority")
            demoted_junction_ids.append(junction_id)

    removed_tllogic_ids = []
    for tllogic in list(target_root.findall("tlLogic")):
        tls_id = str(tllogic.attrib.get("id", ""))
        if tls_id in candidate_tls_to_demote:
            target_root.remove(tllogic)
            removed_tllogic_ids.append(tls_id)

    uncontrolled_connections = []
    for connection in target_root.findall("connection"):
        if str(connection.attrib.get("tl", "")) not in candidate_tls_to_demote:
            continue
        uncontrolled_connections.append(dict(connection.attrib))
        for attr in ("tl", "linkIndex", "linkIndex2"):
            connection.attrib.pop(attr, None)
        connection.set("uncontrolled", "true")

    if demoted_junction_ids or removed_tllogic_ids or uncontrolled_connections:
        ET.indent(target_root, space="    ")
        target_tree.write(target_file, encoding="utf-8", xml_declaration=True)

    return {
        "status": "pass",
        "claim_status": "diagnostic-demo",
        "radius_m": radius_m,
        "candidate_context_tls_ids": sorted(candidate_tls_to_demote),
        "demoted_context_tls_count": len(candidate_tls_to_demote),
        "demoted_context_tls_junction_count": len(demoted_junction_ids),
        "demoted_context_tls_junction_ids": sorted(demoted_junction_ids),
        "removed_context_tllogic_count": len(removed_tllogic_ids),
        "removed_context_tllogic_ids": sorted(removed_tllogic_ids),
        "uncontrolled_context_tls_connection_count": len(uncontrolled_connections),
        "uncontrolled_context_tls_connections": uncontrolled_connections,
        "skipped_contexts": skipped_contexts,
    }


def _prune_plain_tls_against_teacher(
    *,
    teacher_net_file: Path,
    node_file: Path,
    connection_file: Path,
    tllogic_file: Path | None,
) -> dict[str, object]:
    """Keep sequential plain inputs from reviving TLS absent in the teacher.

    netconvert plain export can materialize a stale traffic-light program from
    an intermediate candidate.  That stale program later makes a valid join
    fail because the joined source junction is no longer a controller.  The
    teacher reference is authoritative for this cleanup; the main candidate
    network is not edited here.
    """
    if not all(path.exists() for path in (teacher_net_file, node_file, connection_file)):
        return {"status": "skipped", "reason": "plain_input_missing", "demoted_junction_count": 0}
    try:
        teacher_root = ET.parse(teacher_net_file).getroot()
        node_tree = ET.parse(node_file)
        connection_tree = ET.parse(connection_file)
        tllogic_tree = ET.parse(tllogic_file) if tllogic_file is not None and tllogic_file.exists() else None
    except (OSError, ET.ParseError) as exc:
        return {"status": "fail", "reason": f"parse_error: {exc}", "demoted_junction_count": 0}

    teacher_junction_types = {
        str(junction.attrib.get("id", "")): str(junction.attrib.get("type", ""))
        for junction in teacher_root.findall("junction")
        if junction.attrib.get("id")
    }
    teacher_tls_ids = {
        junction_id
        for junction_id, junction_type in teacher_junction_types.items()
        if junction_type.startswith("traffic_light")
    }
    for junction in teacher_root.findall("junction"):
        if not str(junction.attrib.get("type", "")).startswith("traffic_light"):
            continue
        teacher_tls_ids.update(str(item) for item in junction.attrib.get("tl", "").split() if str(item))
    teacher_tls_ids.update(
        str(logic.attrib.get("id", ""))
        for logic in teacher_root.findall("tlLogic")
        if logic.attrib.get("id")
    )

    demoted_junction_ids: list[str] = []
    invalid_tls_ids: set[str] = set()
    for node in node_tree.getroot().findall("node"):
        node_id = str(node.attrib.get("id", ""))
        if not node_id or not str(node.attrib.get("type", "")).startswith("traffic_light"):
            continue
        node_tls_ids = {node_id, *[str(item) for item in node.attrib.get("tl", "").split() if str(item)]}
        teacher_type = teacher_junction_types.get(node_id, "")
        valid = teacher_type.startswith("traffic_light") and bool(node_tls_ids & teacher_tls_ids)
        if valid:
            continue
        invalid_tls_ids.update(node_tls_ids)
        node.set("type", teacher_type or "priority")
        node.attrib.pop("tl", None)
        node.attrib.pop("tlType", None)
        demoted_junction_ids.append(node_id)

    removed_tllogic_ids: list[str] = []
    removed_tllogic_connection_count = 0
    if tllogic_tree is not None:
        tllogic_root = tllogic_tree.getroot()
        for child in list(tllogic_root):
            child_tls_id = str(child.attrib.get("id", child.attrib.get("tl", "")))
            if child_tls_id and child_tls_id in invalid_tls_ids:
                tllogic_root.remove(child)
                if child.tag == "tlLogic":
                    removed_tllogic_ids.append(child_tls_id)
                else:
                    removed_tllogic_connection_count += 1

    uncontrolled_connection_count = 0
    for connection in connection_tree.getroot().findall("connection"):
        tls_id = str(connection.attrib.get("tl", ""))
        if not tls_id or tls_id not in invalid_tls_ids:
            continue
        for attr in ("tl", "linkIndex", "linkIndex2"):
            connection.attrib.pop(attr, None)
        connection.set("uncontrolled", "true")
        uncontrolled_connection_count += 1

    if demoted_junction_ids:
        ET.indent(node_tree.getroot(), space="    ")
        node_tree.write(node_file, encoding="utf-8", xml_declaration=True)
    if tllogic_tree is not None and (removed_tllogic_ids or removed_tllogic_connection_count):
        ET.indent(tllogic_tree.getroot(), space="    ")
        tllogic_tree.write(tllogic_file, encoding="utf-8", xml_declaration=True)
    if uncontrolled_connection_count:
        ET.indent(connection_tree.getroot(), space="    ")
        connection_tree.write(connection_file, encoding="utf-8", xml_declaration=True)
    return {
        "status": "pass",
        "demoted_junction_count": len(demoted_junction_ids),
        "demoted_junction_ids": sorted(demoted_junction_ids),
        "removed_tllogic_count": len(removed_tllogic_ids),
        "removed_tllogic_ids": sorted(set(removed_tllogic_ids)),
        "removed_tllogic_connection_count": removed_tllogic_connection_count,
        "uncontrolled_connection_count": uncontrolled_connection_count,
    }
