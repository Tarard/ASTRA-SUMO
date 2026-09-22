"""Build and prioritize explicit teacher-guided reconstruction candidates."""

from __future__ import annotations

import copy
import json
from collections import Counter
from pathlib import Path
from typing import Any
import xml.etree.ElementTree as ET
from ..junction_connection_audit import build_connection_signature, write_connection_signature
from ..junction_movement_model import audit_movement_graph, build_movement_graph, write_movement_review
from ..junction_teacher_model import _extract_teacher_junction_model, extract_junction_pattern_exemplar, materialize_exemplar_movement_signatures, slot_edge_map_from_exemplar
from .artifacts import _failure, _write_teacher_guided_queue_csv
from .cases import (
    _attach_junction_pattern_delta,
    _attach_teacher_pattern_template,
    _candidate_junction_id_candidates,
    _junction_pattern_delta_by_id,
    _junction_pattern_record_by_id,
    _junction_pattern_template_by_key,
    _limit_ready_repair_candidates,
    _same_id_pattern_cases,
    _same_id_tls_mismatch_cases,
    _teacher_approach_edge_ids,
    _teacher_guided_candidate_sort_key,
    _teacher_guided_case_sort_key,
    _teacher_pattern_metric_is_positive,
    _tls_repair_candidates,
    _topology_fragmented_non_tls_cases,
    _topology_fragmented_tls_cases,
    _turnaround_only_lane_cases,
    _turnaround_only_lane_gaps,
)
from .edge_mapping import (
    _approach_endpoint_rebuild_plan,
    _case_boundary_edge_map,
    _copyable_missing_teacher_edge_ids,
    _missing_teacher_edge_endpoint_ids,
    _missing_teacher_movement_plan,
    _prefer_existing_exact_edge_ids,
    _stale_case_edge_map_entries,
    _teacher_candidate_edge_map,
    _valid_edge_map,
)
from .network import (
    _approach_edges,
    _plain_edge_endpoints,
    _report_used_unrestored_normalized_replay,
    _should_emit,
    _string_list,
    _write_connections,
)
from .parity import _teacher_parity_summary
from .scope import _conservative_join_node_ids, _expanded_rebuild_scope


def build_teacher_guided_repair_queue(
    *,
    teacher_net_file: Path,
    candidate_net_file: Path,
    reference_join_audit_report: dict[str, Any],
    output_dir: Path,
    prefix: str = "teacher_guided_repair",
    max_ready_candidates: int | None = None,
) -> dict[str, object]:
    if not teacher_net_file.exists():
        return _failure(f"teacher net file does not exist: {teacher_net_file}")
    if not candidate_net_file.exists():
        return _failure(f"candidate net file does not exist: {candidate_net_file}")
    teacher_net_file = teacher_net_file.resolve()
    candidate_net_file = candidate_net_file.resolve()
    output_dir = output_dir.resolve()

    output_dir.mkdir(parents=True, exist_ok=True)
    teacher_root = ET.parse(teacher_net_file).getroot()
    candidate_root = ET.parse(candidate_net_file).getroot()
    teacher_edges = {edge.attrib["id"]: edge for edge in teacher_root.findall("edge") if edge.attrib.get("id")}
    candidate_edges_by_id = {edge.attrib["id"]: edge for edge in candidate_root.findall("edge") if edge.attrib.get("id")}
    candidate_edge_ids = set(candidate_edges_by_id)
    matched_cases = [
        case
        for case in reference_join_audit_report.get("matched_cases", []) or []
        if isinstance(case, dict)
    ]
    pattern_deltas = _junction_pattern_delta_by_id(reference_join_audit_report)
    pattern_records = _junction_pattern_record_by_id(reference_join_audit_report)
    pattern_templates = _junction_pattern_template_by_key(reference_join_audit_report)
    same_id_pattern_cases = _same_id_pattern_cases(
        pattern_deltas,
        matched_cases,
        teacher_root,
        candidate_root,
    )
    same_id_tls_cases = _same_id_tls_mismatch_cases(
        [*matched_cases, *same_id_pattern_cases],
        teacher_root,
        candidate_root,
        teacher_net_file,
        candidate_net_file,
    )
    matched_cases = [*matched_cases, *same_id_pattern_cases, *same_id_tls_cases]
    topology_fragmented_tls_cases = _topology_fragmented_tls_cases(
        matched_cases,
        teacher_root,
        candidate_root,
        teacher_net_file,
        candidate_edges_by_id,
    )
    matched_cases = [*matched_cases, *topology_fragmented_tls_cases]
    topology_fragmented_non_tls_cases = _topology_fragmented_non_tls_cases(
        matched_cases,
        teacher_root,
        candidate_root,
        teacher_net_file,
        candidate_edges_by_id,
    )
    matched_cases = [*matched_cases, *topology_fragmented_non_tls_cases]
    turnaround_only_lane_cases = _turnaround_only_lane_cases(matched_cases, teacher_root, candidate_root)
    matched_cases = [*matched_cases, *turnaround_only_lane_cases]
    matched_cases.sort(
        key=lambda case: _teacher_guided_case_sort_key(case, pattern_records, pattern_templates)
    )
    repair_candidates = []
    tls_alignment_by_reference_id = {
        str(pair.get("reference_tl_id", "")): pair
        for pair in (
            (reference_join_audit_report.get("tls_controller_alignment", {}) or {}).get("pairs", [])
            if isinstance(reference_join_audit_report.get("tls_controller_alignment", {}), dict)
            else []
        )
        if isinstance(pair, dict) and str(pair.get("reference_tl_id", ""))
    }
    for case in matched_cases:
        candidate = _attach_teacher_pattern_template(
            _attach_junction_pattern_delta(
                _teacher_guided_repair_candidate(
                    case=case,
                    teacher_net_file=teacher_net_file,
                    candidate_net_file=candidate_net_file,
                    teacher_root=teacher_root,
                    candidate_root=candidate_root,
                    teacher_edges=teacher_edges,
                    candidate_edges_by_id=candidate_edges_by_id,
                    candidate_edge_ids=candidate_edge_ids,
                ),
                pattern_deltas,
            ),
            pattern_records,
            pattern_templates,
        )
        tls_alignment = tls_alignment_by_reference_id.get(str(candidate.get("reference_id", "")))
        if isinstance(tls_alignment, dict):
            candidate = {
                **candidate,
                "tls_reference_tl_id": str(tls_alignment.get("reference_tl_id", "")),
                "tls_candidate_tl_id": str(tls_alignment.get("candidate_tl_id", "")),
                "tls_candidate_junction_ids": [
                    str(item) for item in tls_alignment.get("candidate_junction_ids", []) or [] if str(item)
                ],
                "tls_approach_pairs": [
                    dict(item)
                    for item in tls_alignment.get("approach_pairs", []) or []
                    if isinstance(item, dict)
                ],
                "tls_reference_controlled_connection_count": int(
                    tls_alignment.get("reference_controlled_connection_count", 0) or 0
                ),
                "tls_candidate_controlled_connection_count": int(
                    tls_alignment.get("candidate_controlled_connection_count", 0) or 0
                ),
            }
        repair_candidates.append(candidate)
    repair_candidates.sort(key=_teacher_guided_candidate_sort_key)
    tls_repair_candidates = _tls_repair_candidates(reference_join_audit_report)
    if max_ready_candidates is not None and max_ready_candidates > 0:
        repair_candidates = _limit_ready_repair_candidates(repair_candidates, max_ready_candidates)
    ready_count = sum(
        1 for candidate in repair_candidates if candidate["candidate_status"] == "ready_for_teacher_guided_variant"
    )
    expanded_scope_count = sum(
        1 for candidate in repair_candidates if candidate["candidate_status"] == "needs_expanded_rebuild_scope"
    )
    queue_file = output_dir / f"{prefix}_queue.json"
    queue_csv_file = output_dir / f"{prefix}_queue.csv"
    report = {
        "status": "pass",
        "claim_status": "diagnostic-demo",
        "teacher_net_file": str(teacher_net_file),
        "candidate_net_file": str(candidate_net_file),
        "matched_case_count": len(matched_cases),
        "same_id_pattern_candidate_count": len(same_id_pattern_cases),
        "same_id_tls_candidate_count": len(same_id_tls_cases),
        "topology_fragmented_tls_candidate_count": len(topology_fragmented_tls_cases),
        "topology_fragmented_non_tls_candidate_count": len(topology_fragmented_non_tls_cases),
        "turnaround_only_lane_candidate_count": len(turnaround_only_lane_cases),
        "queued_case_count": len(repair_candidates),
        "queue_truncated": len(repair_candidates) < len(matched_cases),
        "queue_order_policy": "ready_then_same_id_tls_low_gap_then_largest_vehicle_movement_gap_then_highest_teacher_template_count",
        "max_ready_candidates": max_ready_candidates if max_ready_candidates is not None else "",
        "repair_candidate_count": len(repair_candidates),
        "ready_candidate_count": ready_count,
        "expanded_scope_candidate_count": expanded_scope_count,
        "blocked_candidate_count": len(repair_candidates) - ready_count - expanded_scope_count,
        "tls_repair_candidate_count": len(tls_repair_candidates),
        "tls_repair_category_counts": dict(
            sorted(Counter(str(candidate.get("repair_category", "")) for candidate in tls_repair_candidates).items())
        ),
        "tls_repair_candidates": tls_repair_candidates,
        "junction_pattern_mismatch_field_counts": reference_join_audit_report.get(
            "junction_pattern_mismatch_field_counts", {}
        ),
        "queue_file": str(queue_file),
        "queue_csv_file": str(queue_csv_file),
        "repair_candidates": repair_candidates,
        "review_policy": "queue only; run teacher-guided variants and inspect NetEdit connection mode before adoption",
    }
    queue_file.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    _write_teacher_guided_queue_csv(queue_csv_file, repair_candidates)
    return report


def build_rebuild_candidate(
    *,
    net_file: Path,
    junction_id: str,
    output_dir: Path,
    prefix: str = "junction_movement_rebuild",
    movement_exemplar: dict[str, Any] | None = None,
    slot_edge_map: dict[str, str] | None = None,
    teacher_edge_map: dict[str, str] | None = None,
) -> dict[str, object]:
    if not net_file.exists():
        return _failure(f"net file does not exist: {net_file}")
    output_dir.mkdir(parents=True, exist_ok=True)

    graph = build_movement_graph(net_file, junction_id)
    audit = audit_movement_graph(graph)
    review = write_movement_review(graph, audit, output_dir, prefix)
    signature = build_connection_signature(net_file, junction_id)
    signature_report = write_connection_signature(signature, output_dir, prefix)
    connections_file = output_dir / f"{prefix}.con.xml"
    summary_file = output_dir / f"{prefix}_rebuild_candidate.json"
    command_file = output_dir / f"{prefix}_netconvert.cmd.txt"
    variant_file = output_dir / f"{prefix}_rebuilt.net.xml"

    if movement_exemplar is not None and slot_edge_map is None and teacher_edge_map is not None:
        slot_edge_map = slot_edge_map_from_exemplar(movement_exemplar, teacher_edge_map)
    if movement_exemplar is not None and slot_edge_map is not None:
        emitted = materialize_exemplar_movement_signatures(movement_exemplar, slot_edge_map)
        emitted_pairs = {
            (str(movement.get("from_edge_id", "")), str(movement.get("to_edge_id", ""))) for movement in emitted
        }
        skipped = [
            movement
            for movement in graph.get("movements", []) or []
            if (str(movement.get("source_edge_id", "")), str(movement.get("target_edge_id", ""))) not in emitted_pairs
        ]
        movement_source = "exemplar_signatures"
    else:
        emitted = [movement for movement in graph.get("movements", []) or [] if _should_emit(movement)]
        skipped = [movement for movement in graph.get("movements", []) or [] if not _should_emit(movement)]
        movement_source = "movement_graph"
    _write_connections(connections_file, emitted)
    command = [
        "netconvert",
        "--sumo-net-file",
        str(net_file),
        "--connection-files",
        str(connections_file),
        "--output-file",
        str(variant_file),
    ]
    command_file.write_text(" ".join(command) + "\n", encoding="utf-8")

    report = {
        "status": "pass",
        "claim_status": "diagnostic-demo",
        "junction_id": junction_id,
        "net_file": str(net_file),
        "connections_file": str(connections_file),
        "variant_file": str(variant_file),
        "netconvert_command_file": str(command_file),
        "movement_review": review,
        "connection_signature": signature_report,
        "movement_audit_status": audit["status"],
        "movement_source": movement_source,
        "slot_edge_map": slot_edge_map or {},
        "emitted_connection_count": len(emitted),
        "skipped_movement_count": len(skipped),
        "review_policy": "run netconvert and inspect NetEdit connection mode before adoption",
    }
    summary_file.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    report["summary_file"] = str(summary_file)
    return report


def _expanded_scope_skip_entry(
    *,
    index: int,
    junction_id: str,
    candidate_status: str,
    scope_report: dict[str, Any],
    replay_edge_map: dict[str, str],
) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "index": index,
        "junction_id": junction_id,
        "candidate_status": candidate_status,
    }
    missing_joined = _string_list(scope_report.get("blocking_missing_joined_scope_junction_ids"))
    missing_nodes = _string_list(scope_report.get("blocking_missing_node_ids"))
    missing_blocked = _string_list(scope_report.get("blocking_missing_blocked_edge_ids"))
    if missing_joined:
        entry["skip_reason"] = "scope_insufficient_joined_junction_missing"
        entry["blocking_missing_joined_scope_junction_ids"] = missing_joined
    elif missing_nodes:
        entry["skip_reason"] = "scope_insufficient_missing_nodes"
        entry["blocking_missing_node_ids"] = missing_nodes
    elif missing_blocked:
        entry["skip_reason"] = "missing_blocked_edges_uncopyable"
        entry["blocking_missing_blocked_edge_ids"] = missing_blocked
    elif not replay_edge_map:
        entry["skip_reason"] = "edge_map_derivation_gap"
    else:
        entry["skip_reason"] = "expanded_scope_review"
    return entry


def _accepted_target_internal_replay_entry(
    report: dict[str, Any],
    *,
    junction_id: str,
    teacher_junction_id: str,
) -> dict[str, object] | None:
    replay = report.get("target_internal_replay", {})
    if not isinstance(replay, dict) or replay.get("status") != "pass":
        return None
    edge_map = _valid_edge_map(replay.get("effective_edge_map", {}))
    if not edge_map:
        return None
    return {
        "junction_id": junction_id,
        "teacher_junction_id": teacher_junction_id,
        "edge_map": edge_map,
        "prefer_clean_replay_base": _report_used_unrestored_normalized_replay(report),
    }


def _candidate_requests_target_internal_replay(candidate: dict[str, Any]) -> bool:
    learned_rule = str(candidate.get("learned_rule", ""))
    if learned_rule in {
        "tum_like_topology_fragmented_tls_candidate",
        "tum_like_topology_fragmented_cluster_candidate",
    }:
        return True
    if learned_rule == "tum_like_join_candidate":
        teacher_pattern_key = str(candidate.get("teacher_pattern_key", ""))
        return "control=traffic_light" in teacher_pattern_key or any(
            _teacher_pattern_metric_is_positive(teacher_pattern_key, metric)
            for metric in ("tls", "ped", "internal", "requests")
        )
    if learned_rule == "tum_like_same_id_pattern_candidate":
        mismatch_fields = {str(item) for item in candidate.get("junction_pattern_mismatch_fields", []) or []}
        return bool(mismatch_fields & {"internal_function_counts", "request_signatures", "junction_signature"})
    if learned_rule == "tum_like_same_id_tls_candidate":
        teacher_pattern_key = str(candidate.get("teacher_pattern_key", ""))
        return any(
            _teacher_pattern_metric_is_positive(teacher_pattern_key, metric)
            for metric in ("internal", "requests")
        )
    if learned_rule == "tum_like_turnaround_only_lane_candidate":
        teacher_pattern_key = str(candidate.get("teacher_pattern_key", ""))
        return any(
            _teacher_pattern_metric_is_positive(teacher_pattern_key, metric)
            for metric in ("internal", "requests")
        )
    return False


def _expanded_scope_followup_candidate_for_unsafe_internal_replay(
    candidate: dict[str, Any],
    variant_report: dict[str, object],
    raw_edge_file: Path,
    *,
    junction_id: str,
) -> dict[str, object] | None:
    followup_depth = int(candidate.get("followup_depth", 0) or 0)
    if followup_depth >= 1:
        return None
    replay = variant_report.get("target_internal_replay")
    if not isinstance(replay, dict):
        return None
    removed_count = int(replay.get("removed_stale_replaced_edge_connection_count", 0) or 0)
    if not removed_count:
        return None
    removed_connections = [
        connection
        for connection in replay.get("removed_stale_replaced_edge_connections", []) or []
        if isinstance(connection, dict)
    ]
    removed_edge_ids = sorted(
        {
            str(connection.get(field, ""))
            for connection in removed_connections
            for field in ("from", "to")
            if str(connection.get(field, "")) and not str(connection.get(field, "")).startswith(":")
        }
    )
    endpoints_by_edge = _plain_edge_endpoints(raw_edge_file)
    raw_endpoint_ids = {endpoint for endpoints in endpoints_by_edge.values() for endpoint in endpoints if endpoint}
    matched_candidate_node_ids = {
        str(item)
        for item in candidate.get("matched_candidate_node_ids", []) or []
        if str(item)
    }
    junction_ids = set(matched_candidate_node_ids)
    join_junction_ids = set(matched_candidate_node_ids)
    existing_scope = candidate.get("expanded_rebuild_scope", {})
    if isinstance(existing_scope, dict):
        junction_ids.update(str(item) for item in existing_scope.get("junction_ids", []) or [] if str(item))
        join_junction_ids.update(str(item) for item in existing_scope.get("join_junction_ids", []) or [] if str(item))
        missing_desired_endpoint_ids = sorted(
            {str(item) for item in existing_scope.get("missing_desired_endpoint_ids", []) or [] if str(item)}
        )
    else:
        missing_desired_endpoint_ids = []
    if junction_id and (not raw_endpoint_ids or junction_id in raw_endpoint_ids):
        junction_ids.add(junction_id)
        join_junction_ids.add(junction_id)
    for edge_id in removed_edge_ids:
        junction_ids.update(endpoint for endpoint in endpoints_by_edge.get(edge_id, ()) if endpoint)
    edge_map = _valid_edge_map(candidate.get("edge_map", {}))
    blocked_teacher_edge_ids = sorted(
        teacher_edge_id for teacher_edge_id, candidate_edge_id in edge_map.items() if candidate_edge_id in removed_edge_ids
    )
    followup = copy.deepcopy(candidate)
    followup.update(
        {
            "candidate_status": "needs_expanded_rebuild_scope",
            "followup_reason": "target_internal_replay_removed_non_target_connections",
            "followup_depth": followup_depth + 1,
            "unsafe_removed_connection_count": removed_count,
            "unsafe_removed_connections": removed_connections,
            "unsafe_removed_edge_ids": removed_edge_ids,
            "expanded_rebuild_scope": {
                "status": "review",
                "recommended_action": "rebuild_plain_xml_scope",
                "core_junction_id": junction_id,
                "junction_ids": sorted(junction_ids),
                "join_junction_ids": sorted(join_junction_ids),
                "blocked_teacher_edge_ids": blocked_teacher_edge_ids,
                "missing_desired_endpoint_ids": missing_desired_endpoint_ids,
                "reason": "target internal replay removed non-target boundary connections; rebuild expanded scope before movement replay",
            },
        }
    )
    return followup


def _teacher_guided_repair_candidate(
    *,
    case: dict[str, Any],
    teacher_net_file: Path,
    candidate_net_file: Path,
    teacher_root: ET.Element,
    candidate_root: ET.Element,
    teacher_edges: dict[str, ET.Element],
    candidate_edges_by_id: dict[str, ET.Element],
    candidate_edge_ids: set[str],
) -> dict[str, object]:
    reference_id = str(case.get("reference_id", ""))
    reference_source_node_ids = [str(item) for item in case.get("reference_joined_source_nodes") or []]
    matched_reference_source_node_ids = [str(item) for item in case.get("matched_reference_source_node_ids") or []]
    candidate_node_ids = [str(item) for item in case.get("matched_candidate_node_ids") or case.get("candidate_node_ids") or []]
    candidate_junction_ids = _candidate_junction_id_candidates(reference_id, candidate_node_ids)
    matched_source_node_set = set(matched_reference_source_node_ids or reference_source_node_ids)
    case_edge_map = {
        **_case_boundary_edge_map(case, candidate_edges_by_id),
        **_valid_edge_map(case.get("edge_map", {})),
    }
    case_edge_map = _prefer_existing_exact_edge_ids(case_edge_map, candidate_edges_by_id)
    scope_node_ids = [node_id for node_id in candidate_node_ids if node_id in matched_source_node_set]
    if len(scope_node_ids) < 2:
        scope_node_ids = candidate_node_ids
    unique_candidate_node_ids = list(dict.fromkeys(candidate_node_ids))
    join_all_candidate_node_ids = bool(case.get("join_all_candidate_node_ids")) or (
        str(case.get("learned_rule_basis", "")) == "spatial_cluster" and len(unique_candidate_node_ids) > 2
    )
    join_node_ids = (
        unique_candidate_node_ids
        if join_all_candidate_node_ids
        else _conservative_join_node_ids(candidate_node_ids, matched_source_node_set)
    )
    base = {
        "reference_id": reference_id,
        "junction_id": candidate_junction_ids[0] if candidate_junction_ids else reference_id,
        "reference_joined_source_nodes": reference_source_node_ids,
        "matched_reference_source_node_ids": matched_reference_source_node_ids,
        "matched_candidate_node_ids": candidate_node_ids,
        "learned_rule_basis": str(case.get("learned_rule_basis", "")),
        "learned_rule": str(case.get("learned_rule", "")),
    }
    if not reference_id:
        return {**base, "candidate_status": "invalid_reference_id", "edge_map": {}, "missing_teacher_edge_ids": []}

    try:
        teacher_model = _extract_teacher_junction_model(teacher_root, teacher_net_file, reference_id)
    except (ET.ParseError, OSError, KeyError, TypeError, ValueError) as exc:
        return {
            **base,
            "candidate_status": "teacher_model_failed",
            "edge_map": {},
            "missing_teacher_edge_ids": [],
            "error": f"{type(exc).__name__}: {exc}",
        }
    teacher_summary = teacher_model.get("summary", {}) if isinstance(teacher_model.get("summary"), dict) else {}
    if not any(
        int(teacher_summary.get(field, 0) or 0)
        for field in ("incoming_vehicle_edge_count", "outgoing_vehicle_edge_count", "vehicle_connection_count")
    ):
        return {
            **base,
            "candidate_status": "no_vehicle_reference_context",
            "edge_map": {},
            "missing_teacher_edge_ids": [],
            "pedestrian_connection_count": int(teacher_summary.get("pedestrian_connection_count", 0) or 0),
            "walkingarea_count": int(teacher_summary.get("walkingarea_count", 0) or 0),
        }
    candidate_model = None
    candidate_error = None
    candidate_junction_id = ""
    for candidate_id in candidate_junction_ids:
        try:
            candidate_model = _extract_teacher_junction_model(candidate_root, candidate_net_file, candidate_id)
            candidate_junction_id = candidate_id
            break
        except (ET.ParseError, OSError, KeyError, TypeError, ValueError) as exc:
            candidate_error = exc
    if candidate_model is None:
        missing_teacher_edge_ids = [
            edge_id for edge_id in _teacher_approach_edge_ids(teacher_model) if edge_id not in case_edge_map
        ]
        missing_endpoint_ids = (
            _missing_teacher_edge_endpoint_ids(teacher_edges, missing_teacher_edge_ids, reference_id)
            if case.get("reference_approach_edge_ids") or case.get("matched_reference_source_boundary_edge_ids")
            else []
        )
        if candidate_node_ids:
            return {
                **base,
                "candidate_status": "needs_expanded_rebuild_scope",
                "edge_map": case_edge_map,
                "missing_teacher_edge_ids": missing_teacher_edge_ids,
                "copyable_missing_teacher_edge_ids": [],
                "uncopyable_missing_teacher_edge_ids": missing_teacher_edge_ids,
                "approach_endpoint_rebuild_plan": {"status": "review", "edge_rebuilds": []},
                "expanded_rebuild_scope": {
                    "status": "review",
                    "recommended_action": "rebuild_plain_xml_scope",
                    "core_junction_id": base["junction_id"],
                    "junction_ids": sorted(dict.fromkeys([*scope_node_ids, *missing_endpoint_ids])),
                    "join_junction_ids": list(dict.fromkeys(join_node_ids)),
                    "blocked_teacher_edge_ids": missing_teacher_edge_ids,
                    "missing_desired_endpoint_ids": missing_endpoint_ids,
                    "reason": "candidate joined junction not found; rebuild from matched candidate source nodes",
                },
                "error": f"{type(candidate_error).__name__}: {candidate_error}",
            }
        return {
            **base,
            "candidate_status": "needs_joined_candidate_junction",
            "edge_map": case_edge_map,
            "missing_teacher_edge_ids": missing_teacher_edge_ids,
            "error": f"{type(candidate_error).__name__}: {candidate_error}",
        }

    provisional_edge_map = _teacher_candidate_edge_map(teacher_model, candidate_model, drop_endpoint_mismatches=False)
    approach_endpoint_rebuild_plan = _approach_endpoint_rebuild_plan(
        teacher_model,
        candidate_model,
        edge_map=provisional_edge_map,
        teacher_junction_id=reference_id,
        candidate_junction_id=candidate_junction_id,
        candidate_junction_ids={
            junction.attrib["id"]
            for junction in candidate_root.findall("junction")
            if junction.attrib.get("id")
        },
    )
    stale_case_edge_map = _stale_case_edge_map_entries(
        case_edge_map,
        teacher_model,
        candidate_model,
        provisional_edge_map,
    )
    edge_map = _teacher_candidate_edge_map(
        teacher_model,
        candidate_model,
        teacher_junction_id=reference_id,
        candidate_junction_id=candidate_junction_id,
    )
    edge_map = dict(sorted({**case_edge_map, **edge_map}.items()))
    missing = [edge_id for edge_id in _teacher_approach_edge_ids(teacher_model) if edge_id not in edge_map]
    copyable_missing = _copyable_missing_teacher_edge_ids(
        teacher_root.findall("connection"),
        teacher_edges,
        candidate_edges_by_id,
        teacher_junction_id=reference_id,
        candidate_junction_id=candidate_junction_id,
        edge_map=edge_map,
    )
    uncopyable_missing = [edge_id for edge_id in missing if edge_id not in set(copyable_missing)]
    movement_exemplar = extract_junction_pattern_exemplar(teacher_net_file, reference_id)
    expanded_rebuild_scope = _expanded_rebuild_scope(
        candidate_junction_id,
        approach_endpoint_rebuild_plan,
        blocked_teacher_edge_ids=uncopyable_missing,
        fallback_junction_ids=scope_node_ids,
    )
    if stale_case_edge_map:
        stale_endpoint_ids = sorted(
            {
                endpoint
                for edge_id in stale_case_edge_map.values()
                if (edge := candidate_edges_by_id.get(edge_id)) is not None
                for endpoint in (edge.attrib.get("from", ""), edge.attrib.get("to", ""))
                if endpoint and endpoint != candidate_junction_id
            }
        )
        existing_scope = case.get("expanded_rebuild_scope", {})
        if isinstance(existing_scope, dict) and existing_scope:
            existing_junction_ids = [
                str(item) for item in existing_scope.get("junction_ids", []) or [] if str(item)
            ]
            existing_join_junction_ids = [
                str(item) for item in existing_scope.get("join_junction_ids", []) or [] if str(item)
            ]
            expanded_rebuild_scope = {
                **existing_scope,
                "status": "review",
                "junction_ids": sorted(dict.fromkeys([candidate_junction_id, *existing_junction_ids])),
                "join_junction_ids": sorted(dict.fromkeys([candidate_junction_id, *existing_join_junction_ids])),
                "reason": "case edge map points outside candidate junction approaches",
                "stale_case_edge_map_ids": stale_case_edge_map,
            }
        else:
            expanded_rebuild_scope = {
                "status": "review",
                "recommended_action": "rebuild_plain_xml_scope",
                "core_junction_id": candidate_junction_id,
                "junction_ids": sorted(dict.fromkeys([candidate_junction_id, *scope_node_ids, *stale_endpoint_ids])),
                "join_junction_ids": list(dict.fromkeys(join_node_ids or [candidate_junction_id])),
                "blocked_teacher_edge_ids": sorted(stale_case_edge_map),
                "missing_desired_endpoint_ids": [],
                "reason": "case edge map points outside candidate junction approaches",
                "stale_case_edge_map_ids": stale_case_edge_map,
            }
    if str(case.get("learned_rule", "")) == "tum_like_turnaround_only_lane_candidate" and missing:
        missing_endpoint_ids = sorted(
            {
                endpoint
                for edge_id in missing
                if (edge := teacher_edges.get(edge_id)) is not None
                for endpoint in (edge.attrib.get("from", ""), edge.attrib.get("to", ""))
                if endpoint and endpoint != reference_id
            }
        )
        expanded_rebuild_scope = {
            "status": "review",
            "recommended_action": "rebuild_plain_xml_scope",
            "core_junction_id": candidate_junction_id,
            "junction_ids": sorted({candidate_junction_id, *missing_endpoint_ids}) if candidate_junction_id else missing_endpoint_ids,
            "join_junction_ids": [candidate_junction_id] if candidate_junction_id else [],
            "blocked_teacher_edge_ids": missing,
            "missing_desired_endpoint_ids": missing_endpoint_ids,
            "reason": "turnaround-only lane is missing a normal teacher movement target edge",
        }
    candidate_status = "ready_for_teacher_guided_variant"
    if expanded_rebuild_scope["status"] == "review":
        candidate_status = "needs_expanded_rebuild_scope"
    elif uncopyable_missing:
        candidate_status = "edge_map_incomplete"
    teacher_parity = _teacher_parity_summary(teacher_model)
    candidate_parity = _teacher_parity_summary(candidate_model)
    missing_teacher_movement_plan = _missing_teacher_movement_plan(
        teacher_model,
        candidate_model,
        edge_map=edge_map,
        teacher_junction_id=reference_id,
        candidate_junction_id=candidate_junction_id,
    )
    turnaround_only_lane_gaps = _turnaround_only_lane_gaps(
        teacher_model,
        candidate_model,
        edge_map=edge_map,
    )
    movement_matrix_missing_count = max(
        0,
        int(candidate_parity.get("vehicle_movement_matrix_missing_count", 0) or 0)
        - int(teacher_parity.get("vehicle_movement_matrix_missing_count", 0) or 0),
        len(missing_teacher_movement_plan),
        len(turnaround_only_lane_gaps),
    )
    review_actions = ["rebuild_vehicle_movement_matrix"] if movement_matrix_missing_count else []
    return {
        **base,
        "junction_id": candidate_junction_id,
        "candidate_status": candidate_status,
        "edge_map": edge_map,
        "slot_edge_map": slot_edge_map_from_exemplar(movement_exemplar, edge_map),
        "movement_exemplar": movement_exemplar,
        "approach_endpoint_rebuild_plan": approach_endpoint_rebuild_plan,
        "expanded_rebuild_scope": expanded_rebuild_scope,
        "stale_case_edge_map_ids": stale_case_edge_map,
        "missing_teacher_edge_ids": missing,
        "copyable_missing_teacher_edge_ids": copyable_missing,
        "uncopyable_missing_teacher_edge_ids": uncopyable_missing,
        "vehicle_movement_matrix_missing_count": movement_matrix_missing_count,
        "missing_teacher_movement_plan_count": len(missing_teacher_movement_plan),
        "missing_teacher_movement_plan": missing_teacher_movement_plan,
        "turnaround_only_lane_gap_count": len(turnaround_only_lane_gaps),
        "turnaround_only_lane_gaps": turnaround_only_lane_gaps,
        "netedit_review_actions": review_actions,
        "review_priority": "high" if review_actions else "normal",
        "teacher_incoming_edge_count": len(_approach_edges(teacher_model, "incoming")),
        "teacher_outgoing_edge_count": len(_approach_edges(teacher_model, "outgoing")),
        "candidate_incoming_edge_count": len(_approach_edges(candidate_model, "incoming")),
        "candidate_outgoing_edge_count": len(_approach_edges(candidate_model, "outgoing")),
    }
