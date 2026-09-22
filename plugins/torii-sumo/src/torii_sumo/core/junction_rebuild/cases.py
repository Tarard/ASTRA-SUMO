"""Classify teacher differences and attach applicable junction-pattern evidence."""

from __future__ import annotations

import json
from typing import Any
import xml.etree.ElementTree as ET
from pathlib import Path
from ..junction_teacher_model import _extract_teacher_junction_model
from .edge_mapping import (
    _candidate_edge_by_exact_or_unsplit_id,
    _candidate_nodes_from_exact_teacher_approach_edges,
)
from .network import (
    _approach_edges,
    _real_junction_ids,
    _root_vehicle_outgoing_by_lane,
    _signed_edge_family_id,
    _vehicle_outgoing_by_lane,
)
from .parity import _teacher_parity_summary
from .scope import _sumo_joined_cluster_id


def _same_id_tls_matches_teacher(
    teacher_root: ET.Element,
    candidate_root: ET.Element,
    teacher_net_file: Path,
    candidate_net_file: Path,
    junction_id: str,
    candidate_junction: ET.Element,
) -> bool:
    if not _teacher_junction_has_tls(candidate_root, junction_id, candidate_junction):
        return False
    try:
        teacher_model = _extract_teacher_junction_model(teacher_root, teacher_net_file, junction_id)
        candidate_model = _extract_teacher_junction_model(candidate_root, candidate_net_file, junction_id)
    except (ET.ParseError, OSError, KeyError, TypeError, ValueError):
        return False
    teacher = _teacher_parity_summary(teacher_model)
    candidate = _teacher_parity_summary(candidate_model)
    fields = (
        "incoming_vehicle_edge_count",
        "outgoing_vehicle_edge_count",
        "vehicle_connection_count",
        "controlled_vehicle_link_count",
        "controlled_link_index_count",
        "controlled_duplicate_link_index_count",
    )
    return all(teacher.get(field) == candidate.get(field) for field in fields)


def _same_id_tls_mismatch_cases(
    matched_cases: list[dict[str, Any]],
    teacher_root: ET.Element,
    candidate_root: ET.Element,
    teacher_net_file: Path,
    candidate_net_file: Path,
) -> list[dict[str, Any]]:
    covered_ids = {key for case in matched_cases for key in _junction_pattern_delta_keys(case)}
    candidate_junctions = {
        junction.attrib["id"]: junction
        for junction in candidate_root.findall("junction")
        if junction.attrib.get("id") and not junction.attrib["id"].startswith(":")
    }
    cases = []
    for junction in teacher_root.findall("junction"):
        reference_id = junction.attrib.get("id", "")
        candidate_junction = candidate_junctions.get(reference_id)
        if (
            not reference_id
            or reference_id.startswith(":")
            or reference_id in covered_ids
            or candidate_junction is None
            or not _teacher_junction_has_tls(teacher_root, reference_id, junction)
        ):
            continue
        if _same_id_tls_matches_teacher(
            teacher_root,
            candidate_root,
            teacher_net_file,
            candidate_net_file,
            reference_id,
            candidate_junction,
        ):
            continue
        cases.append(
            {
                "reference_id": reference_id,
                "reference_joined_source_nodes": [],
                "matched_reference_source_node_ids": [],
                "matched_candidate_node_ids": [reference_id],
                "learned_rule_basis": "same_id_tls_semantic_mismatch",
                "learned_rule": "tum_like_same_id_tls_candidate",
            }
        )
    return cases


def _topology_fragmented_tls_cases(
    matched_cases: list[dict[str, Any]],
    teacher_root: ET.Element,
    candidate_root: ET.Element,
    teacher_net_file: Path,
    candidate_edges_by_id: dict[str, ET.Element],
) -> list[dict[str, Any]]:
    covered_ids = {key for case in matched_cases for key in _junction_pattern_delta_keys(case)}
    candidate_junction_ids = _real_junction_ids(candidate_root)
    cases = []
    for junction in teacher_root.findall("junction"):
        reference_id = junction.attrib.get("id", "")
        if (
            not reference_id
            or reference_id.startswith(":")
            or reference_id in covered_ids
            or reference_id in candidate_junction_ids
            or not _teacher_junction_has_tls(teacher_root, reference_id, junction)
        ):
            continue
        try:
            teacher_model = _extract_teacher_junction_model(teacher_root, teacher_net_file, reference_id)
        except (ET.ParseError, OSError, KeyError, TypeError, ValueError):
            continue
        candidate_node_ids, edge_map = _candidate_nodes_from_exact_teacher_approach_edges(
            teacher_model,
            candidate_edges_by_id,
            candidate_junction_ids,
        )
        if len(candidate_node_ids) < 2:
            continue
        cases.append(
            {
                "reference_id": reference_id,
                "matched_candidate_node_ids": candidate_node_ids,
                "join_all_candidate_node_ids": True,
                "edge_map": edge_map,
                "learned_rule_basis": "topology_fragmented_tls_approach_edges",
                "learned_rule": "tum_like_topology_fragmented_tls_candidate",
            }
        )
    return cases


def _topology_fragmented_non_tls_cases(
    matched_cases: list[dict[str, Any]],
    teacher_root: ET.Element,
    candidate_root: ET.Element,
    teacher_net_file: Path,
    candidate_edges_by_id: dict[str, ET.Element],
) -> list[dict[str, Any]]:
    covered_ids = {key for case in matched_cases for key in _junction_pattern_delta_keys(case)}
    candidate_junction_ids = _real_junction_ids(candidate_root)
    cases = []
    for junction in teacher_root.findall("junction"):
        reference_id = junction.attrib.get("id", "")
        if (
            not reference_id
            or not reference_id.startswith("cluster_")
            or reference_id in covered_ids
            or reference_id in candidate_junction_ids
            or _teacher_junction_has_tls(teacher_root, reference_id, junction)
        ):
            continue
        try:
            teacher_model = _extract_teacher_junction_model(teacher_root, teacher_net_file, reference_id)
        except (ET.ParseError, OSError, KeyError, TypeError, ValueError):
            continue
        candidate_node_ids, edge_map = _candidate_nodes_from_exact_teacher_approach_edges(
            teacher_model,
            candidate_edges_by_id,
            candidate_junction_ids,
        )
        if len(candidate_node_ids) < 2:
            continue
        cases.append(
            {
                "reference_id": reference_id,
                "matched_candidate_node_ids": candidate_node_ids,
                "join_all_candidate_node_ids": True,
                "edge_map": edge_map,
                "learned_rule_basis": "topology_fragmented_non_tls_approach_edges",
                "learned_rule": "tum_like_topology_fragmented_cluster_candidate",
            }
        )
    return cases


def _teacher_guided_case_sort_key(
    case: dict[str, Any],
    pattern_records: dict[str, dict[str, Any]] | None = None,
    pattern_templates: dict[str, dict[str, Any]] | None = None,
) -> tuple[int, int, int, str]:
    template_count = _teacher_template_count_for_case(case, pattern_records or {}, pattern_templates or {})
    candidate_nodes = case.get("matched_candidate_node_ids")
    candidate_node_count = len(candidate_nodes) if isinstance(candidate_nodes, list) else 1_000_000
    reference_id = str(case.get("reference_id", ""))
    reference_node_count = len(reference_id.removeprefix("cluster_").split("_")) if reference_id else 1_000_000
    return (-template_count, candidate_node_count, reference_node_count, reference_id)


def _tls_repair_candidates(reference_join_audit_report: dict[str, Any]) -> list[dict[str, object]]:
    candidates = []
    for entry in reference_join_audit_report.get("tls_control_review_queue", []) or []:
        if not isinstance(entry, dict):
            continue
        repair_category = str(entry.get("repair_category", "tls_controller_cardinality_repair"))
        candidates.append(
            {
                **entry,
                "candidate_status": "needs_tls_semantic_repair",
                "repair_category": repair_category,
                "netedit_review_actions": _tls_repair_actions(repair_category),
                "tls_review_index": len(candidates),
            }
        )
    return candidates


def _same_id_pattern_cases(
    pattern_deltas: dict[str, dict[str, Any]],
    matched_cases: list[dict[str, Any]],
    teacher_root: ET.Element,
    candidate_root: ET.Element,
) -> list[dict[str, Any]]:
    covered_ids = {
        key
        for case in matched_cases
        for key in _junction_pattern_delta_keys(case)
    }
    teacher_junction_ids = _real_junction_ids(teacher_root)
    candidate_junction_ids = _real_junction_ids(candidate_root)
    cases = []
    for junction_id, delta in sorted(pattern_deltas.items()):
        if delta.get("status") == "pass" or junction_id in covered_ids:
            continue
        if junction_id not in teacher_junction_ids or junction_id not in candidate_junction_ids:
            continue
        cases.append(
            {
                "reference_id": junction_id,
                "reference_joined_source_nodes": [],
                "matched_reference_source_node_ids": [],
                "matched_candidate_node_ids": [junction_id],
                "learned_rule_basis": "same_id_junction_pattern",
                "learned_rule": "tum_like_same_id_pattern_candidate",
            }
        )
    return cases


def _attach_junction_pattern_delta(
    candidate: dict[str, object],
    deltas: dict[str, dict[str, Any]],
) -> dict[str, object]:
    matches = [deltas[key] for key in _junction_pattern_delta_keys(candidate) if key in deltas]
    if not matches:
        return candidate
    mismatch_fields = list(
        dict.fromkeys(field for delta in matches for field in delta.get("mismatch_fields", []))
    )
    review_actions = list(
        dict.fromkeys(
            [str(item) for item in candidate.get("netedit_review_actions", []) or []]
            + _netedit_review_actions(mismatch_fields)
        )
    )
    return {
        **candidate,
        "junction_pattern_delta_count": len(matches),
        "junction_pattern_deltas": matches,
        "junction_pattern_mismatch_fields": mismatch_fields,
        "netedit_review_actions": review_actions,
        "review_priority": "high" if review_actions else str(candidate.get("review_priority", "normal") or "normal"),
    }


def _candidate_junction_id_candidates(reference_id: str, node_ids: list[str]) -> list[str]:
    candidates = [reference_id] if reference_id else []
    joined_id = _sumo_joined_cluster_id(node_ids)
    if joined_id and joined_id not in candidates:
        candidates.append(joined_id)
    return candidates


def _teacher_approach_edge_ids(teacher_model: dict[str, object]) -> list[str]:
    return sorted(dict.fromkeys(_approach_edges(teacher_model, "incoming") + _approach_edges(teacher_model, "outgoing")))


def _turnaround_only_lane_cases(
    matched_cases: list[dict[str, Any]],
    teacher_root: ET.Element,
    candidate_root: ET.Element,
) -> list[dict[str, Any]]:
    covered_ids = {key for case in matched_cases for key in _junction_pattern_delta_keys(case)}
    teacher_junction_ids = _real_junction_ids(teacher_root)
    candidate_junction_ids = _real_junction_ids(candidate_root)
    teacher_by_lane = _root_vehicle_outgoing_by_lane(teacher_root)
    candidate_by_lane = _root_vehicle_outgoing_by_lane(candidate_root)
    candidate_edges = {
        edge.attrib["id"]: edge
        for edge in candidate_root.findall("edge")
        if edge.attrib.get("id") and not edge.attrib["id"].startswith(":")
    }
    teacher_edges = {
        edge.attrib["id"]: edge
        for edge in teacher_root.findall("edge")
        if edge.attrib.get("id") and not edge.attrib["id"].startswith(":")
    }
    teacher_lane_keys_by_family: dict[tuple[str, str], list[tuple[str, str]]] = {}
    for edge_id, from_lane in teacher_by_lane:
        teacher_lane_keys_by_family.setdefault((_signed_edge_family_id(edge_id), from_lane), []).append(
            (edge_id, from_lane)
        )
    cases: dict[str, dict[str, object]] = {}
    for (edge_id, from_lane), candidate_stats in candidate_by_lane.items():
        if candidate_stats["non_turnaround_count"] or not candidate_stats["turnaround_count"]:
            continue
        candidate_edge = candidate_edges.get(edge_id)
        junction_id = candidate_edge.attrib.get("to", "") if candidate_edge is not None else ""
        if (
            not junction_id
            or junction_id in covered_ids
            or junction_id not in candidate_junction_ids
        ):
            continue
        teacher_lane_keys = [(edge_id, from_lane)] if (edge_id, from_lane) in teacher_by_lane else []
        teacher_lane_keys.extend(
            key
            for key in teacher_lane_keys_by_family.get((_signed_edge_family_id(edge_id), from_lane), [])
            if key not in teacher_lane_keys
        )
        matched_teacher_edge_id = ""
        matched_teacher_stats: dict[str, object] | None = None
        for teacher_edge_id, teacher_from_lane in teacher_lane_keys:
            teacher_stats = teacher_by_lane.get((teacher_edge_id, teacher_from_lane))
            if not teacher_stats or not teacher_stats["non_turnaround_count"]:
                continue
            teacher_edge = teacher_edges.get(teacher_edge_id)
            if (
                teacher_edge is None
                or teacher_edge.attrib.get("to") != junction_id
                or teacher_edge.attrib.get("to") not in teacher_junction_ids
            ):
                continue
            matched_teacher_edge_id = teacher_edge_id
            matched_teacher_stats = teacher_stats
            break
        if not matched_teacher_edge_id or matched_teacher_stats is None:
            continue
        case = cases.setdefault(junction_id, {"source_lanes": set(), "edge_map": {}})
        source_lanes = case["source_lanes"]
        if isinstance(source_lanes, set):
            source_lanes.add(f"{edge_id}_{from_lane}")
        edge_map = case["edge_map"]
        if isinstance(edge_map, dict):
            edge_map[matched_teacher_edge_id] = edge_id
            for teacher_target in sorted(matched_teacher_stats["non_turnaround_targets"]):
                candidate_target_id, _candidate_target = _candidate_edge_by_exact_or_unsplit_id(
                    str(teacher_target),
                    candidate_edges,
                )
                if candidate_target_id:
                    edge_map[str(teacher_target)] = candidate_target_id
    return [
        {
            "reference_id": junction_id,
            "reference_joined_source_nodes": [],
            "matched_reference_source_node_ids": [],
            "matched_candidate_node_ids": [junction_id],
            "edge_map": dict(sorted(case_data["edge_map"].items()))
            if isinstance(case_data.get("edge_map"), dict)
            else {},
            "turnaround_only_source_lanes": sorted(case_data["source_lanes"])
            if isinstance(case_data.get("source_lanes"), set)
            else [],
            "learned_rule_basis": "turnaround_only_lane_gap",
            "learned_rule": "tum_like_turnaround_only_lane_candidate",
        }
        for junction_id, case_data in sorted(cases.items())
    ]


def _turnaround_only_lane_gaps(
    teacher_model: dict[str, object],
    candidate_model: dict[str, object],
    *,
    edge_map: dict[str, str],
) -> list[dict[str, object]]:
    teacher_by_lane = _vehicle_outgoing_by_lane(teacher_model)
    candidate_by_lane = _vehicle_outgoing_by_lane(candidate_model)
    teacher_by_candidate_edge = {candidate: teacher for teacher, candidate in edge_map.items()}
    gaps = []
    for (candidate_edge_id, from_lane), candidate_stats in sorted(candidate_by_lane.items()):
        if candidate_stats["non_turnaround_count"]:
            continue
        if not candidate_stats["turnaround_count"]:
            continue
        teacher_edge_id = teacher_by_candidate_edge.get(candidate_edge_id)
        if not teacher_edge_id and (candidate_edge_id, from_lane) in teacher_by_lane:
            teacher_edge_id = candidate_edge_id
        if not teacher_edge_id:
            continue
        teacher_stats = teacher_by_lane.get((teacher_edge_id, from_lane))
        if not teacher_stats or not teacher_stats["non_turnaround_count"]:
            continue
        gaps.append(
            {
                "teacher_from_edge_id": teacher_edge_id,
                "from_edge_id": candidate_edge_id,
                "fromLane": from_lane,
                "candidate_turnaround_outgoing_count": candidate_stats["turnaround_count"],
                "candidate_non_turnaround_outgoing_count": candidate_stats["non_turnaround_count"],
                "teacher_turnaround_outgoing_count": teacher_stats["turnaround_count"],
                "teacher_non_turnaround_outgoing_count": teacher_stats["non_turnaround_count"],
                "teacher_non_turnaround_targets": sorted(teacher_stats["non_turnaround_targets"]),
                "match_status": "candidate_turnaround_only_teacher_has_normal_vehicle_movement",
            }
        )
    return gaps


def _teacher_pattern_metric_is_positive(pattern_key: str, metric: str) -> bool:
    prefix = f"{metric}="
    for part in pattern_key.split("|"):
        if not part.startswith(prefix):
            continue
        for token in part[len(prefix) :].replace("/", ":").split(":"):
            try:
                if int(token) > 0:
                    return True
            except ValueError:
                continue
    return False


def _teacher_pattern_contexts(variant_reports: list[dict[str, object]]) -> list[dict[str, object]]:
    contexts = []
    seen_keys = set()
    for report in variant_reports:
        pattern_key = str(report.get("teacher_pattern_key", ""))
        if not pattern_key or pattern_key in seen_keys:
            continue
        seen_keys.add(pattern_key)
        try:
            template_count = int(report.get("teacher_pattern_template_count", 0) or 0)
        except (TypeError, ValueError):
            template_count = 0
        examples = report.get("teacher_pattern_template_examples", [])
        contexts.append(
            {
                "teacher_pattern_key": pattern_key,
                "teacher_pattern_family": str(report.get("teacher_pattern_family", "")),
                "teacher_pattern_template_count": template_count,
                "teacher_pattern_template_examples": [str(item) for item in examples]
                if isinstance(examples, list)
                else [],
            }
        )
    return contexts


def _attach_candidate_template_context(
    report: dict[str, object],
    candidate: dict[str, Any],
) -> dict[str, object]:
    context = {
        key: candidate[key]
        for key in (
            "teacher_pattern_key",
            "teacher_pattern_family",
            "teacher_pattern_template_count",
            "teacher_pattern_template_examples",
            "matched_candidate_node_ids",
            "expanded_rebuild_scope",
            "tls_join_scope_expansion",
            "tls_approach_edge_map_evidence",
            "sequential_refreshed_candidate",
            "sequential_refresh_source_net_file",
            "sequential_refresh_status",
            "sequential_refresh_error",
            "sequential_allowed_boundary_overlap_edge_ids",
        )
        if key in candidate
    }
    candidate_original_junction_id = str(candidate.get("junction_id", ""))
    if candidate_original_junction_id:
        context["candidate_original_junction_id"] = candidate_original_junction_id
    if not context:
        return report
    merged = {**report, **context}
    report_file = str(report.get("report_file", ""))
    if report_file:
        try:
            path = Path(report_file)
            existing = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(existing, dict):
                path.write_text(json.dumps({**existing, **context}, indent=2, ensure_ascii=False), encoding="utf-8")
        except (OSError, json.JSONDecodeError):
            pass
    return merged


def _teacher_guided_candidate_sort_key(candidate: dict[str, object]) -> tuple[int, int, int, int, int, str]:
    movement_gap = int(candidate.get("vehicle_movement_matrix_missing_count", 0) or 0)
    template_count = int(candidate.get("teacher_pattern_template_count", 0) or 0)
    candidate_nodes = candidate.get("matched_candidate_node_ids")
    candidate_node_count = len(candidate_nodes) if isinstance(candidate_nodes, list) else 1_000_000
    status_rank = 0 if candidate.get("candidate_status") in {"ready_for_teacher_guided_variant", "needs_expanded_rebuild_scope"} else 1
    is_same_id_tls = candidate.get("learned_rule") == "tum_like_same_id_tls_candidate"
    semantic_rank = 0 if is_same_id_tls else 1
    movement_rank = movement_gap if is_same_id_tls else -movement_gap
    return (status_rank, semantic_rank, movement_rank, -template_count, candidate_node_count, str(candidate.get("reference_id", "")))


def _limit_ready_repair_candidates(candidates: list[dict[str, object]], max_ready_candidates: int) -> list[dict[str, object]]:
    ready = [
        candidate
        for candidate in candidates
        if candidate.get("candidate_status") == "ready_for_teacher_guided_variant"
    ][:max_ready_candidates]
    if len(ready) >= max_ready_candidates:
        return ready
    ready_ids = {id(candidate) for candidate in ready}
    selected = list(ready)
    for candidate in candidates:
        if id(candidate) in ready_ids:
            continue
        selected.append(candidate)
    return selected


def _tls_repair_actions(repair_category: str) -> list[str]:
    if repair_category == "tls_linkindex_phase_repair":
        return ["inspect_tls_linkindex_phase"]
    return ["inspect_tls_control"]


def _junction_pattern_record_by_id(report: dict[str, Any]) -> dict[str, dict[str, Any]]:
    records = {}
    for record in report.get("junction_pattern_index", []) or []:
        if not isinstance(record, dict):
            continue
        junction_id = str(record.get("junction_id", ""))
        if junction_id:
            records[junction_id] = record
    return records


def _junction_pattern_template_by_key(report: dict[str, Any]) -> dict[str, dict[str, Any]]:
    templates = {}
    for template in report.get("junction_pattern_templates", []) or []:
        if not isinstance(template, dict):
            continue
        pattern_key = str(template.get("pattern_key", ""))
        if pattern_key:
            templates[pattern_key] = template
    return templates


def _teacher_template_count_for_case(
    case: dict[str, Any],
    pattern_records: dict[str, dict[str, Any]],
    pattern_templates: dict[str, dict[str, Any]],
) -> int:
    reference_id = str(case.get("reference_id", ""))
    pattern_key = str(pattern_records.get(reference_id, {}).get("pattern_key", ""))
    if not pattern_key:
        return 0
    return int(pattern_templates.get(pattern_key, {}).get("count", 0) or 0)


def _junction_pattern_delta_by_id(report: dict[str, Any]) -> dict[str, dict[str, Any]]:
    deltas = {}
    for comparison in report.get("junction_pattern_comparisons", []) or []:
        if not isinstance(comparison, dict):
            continue
        junction_id = str(comparison.get("junction_id", ""))
        if not junction_id:
            continue
        deltas[junction_id] = {
            "junction_id": junction_id,
            "status": str(comparison.get("status", "")),
            "mismatch_fields": [str(field) for field in comparison.get("mismatch_fields", []) or []],
            "teacher": comparison.get("teacher", {}) if isinstance(comparison.get("teacher"), dict) else {},
            "candidate": comparison.get("candidate", {}) if isinstance(comparison.get("candidate"), dict) else {},
        }
    return deltas


def _teacher_junction_has_tls(
    root: ET.Element,
    junction_id: str,
    junction: ET.Element,
) -> bool:
    return (
        junction.attrib.get("type") == "traffic_light"
        or any(tl.attrib.get("id") == junction_id for tl in root.findall("tlLogic"))
        or any(connection.attrib.get("tl") == junction_id for connection in root.findall("connection"))
    )


def _attach_teacher_pattern_template(
    candidate: dict[str, object],
    pattern_records: dict[str, dict[str, Any]],
    pattern_templates: dict[str, dict[str, Any]],
) -> dict[str, object]:
    reference_id = str(candidate.get("reference_id", ""))
    record = pattern_records.get(reference_id, {})
    movement_exemplar = candidate.get("movement_exemplar", {})
    exemplar_pattern_key = (
        str(movement_exemplar.get("pattern_key", ""))
        if isinstance(movement_exemplar, dict)
        else ""
    )
    pattern_key = str(record.get("pattern_key", "")) or exemplar_pattern_key
    if not pattern_key:
        return candidate
    template = pattern_templates.get(pattern_key, {})
    return {
        **candidate,
        "teacher_pattern_key": pattern_key,
        "teacher_pattern_family": str(
            template.get("pattern_family", record.get("pattern_family", ""))
        ),
        "teacher_pattern_template_count": int(template.get("count", 0) or 0),
        "teacher_pattern_template_examples": [
            str(item) for item in template.get("example_junction_ids", []) or []
        ],
    }


def _netedit_review_actions(mismatch_fields: list[str]) -> list[str]:
    action_by_field = {
        "internal_function_counts": "inspect_internal_edges_crossings_walkingareas",
        "approach_edge_ids": "verify_approach_membership",
        "control_type": "inspect_tls_control",
        "has_tls": "inspect_tls_control",
        "movement_signature_counts": "rebuild_vehicle_movement_matrix",
        "request_bit_lengths_ok": "inspect_request_foes_response",
    }
    return list(
        dict.fromkeys(
            action_by_field.get(field, "inspect_junction_pattern_delta") for field in mismatch_fields
        )
    )


def _junction_pattern_delta_keys(candidate: dict[str, object]) -> list[str]:
    keys = [str(candidate.get("reference_id", "")), str(candidate.get("junction_id", ""))]
    for field in ("reference_joined_source_nodes", "matched_reference_source_node_ids", "matched_candidate_node_ids"):
        keys.extend(str(item) for item in candidate.get(field, []) or [])
    return [key for key in dict.fromkeys(keys) if key]
