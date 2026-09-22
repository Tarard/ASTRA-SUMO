"""Compatibility tail extracted from ``osm_workflow``.

This module carries the dependent helper layers of the legacy OSM
cleanup workflow.  The original module re-exports these names so
existing callers and tests keep working while the facade shrinks.
"""

from __future__ import annotations

import json
import shutil
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Callable, Mapping

from .command_runner import run_command
from .junction_rebuild.restoration import _restore_replayed_geometry_attrs
from .network_plan import NETWORK_PLAN_QUESTION
from .osm_area import osm_preview_url
from .road_connectivity_teacher_model import (
    write_internal_movement_owner_layered_teacher_replay_candidate,
    write_road_connection_topology_replay_candidate,
    write_road_connectivity_split_root_alias_repair_candidate,
)
from .tls_aggregation import build_tls_aggregation_variant
from .topology_audit import compare_topology_canonical_cells
from .osm_workflow_helpers import (
    _candidate_fields,
    _command_path_for_cwd,
    _command_result_report,
    _controlled_tls_connection_count_from_net_file,
    _delta_count_score,
    _delta_failed_fields_by_junction,
    _gate_value,
    _has_tls_incompatibility_warning,
    _int_field,
    _intish,
    _junction_aggregation_summary,
    _osm_highway_classes,
    _osm_tag_values,
    _plain_output_prefix,
    _prune_stale_plain_tllogics,
    _reference_join_audit_can_seed_teacher_guided_queue,
    _restore_false_traffic_light_plain_node_types,
    _road_connectivity_gate_status,
    _road_connectivity_seed_geometry_owner_ids,
    _road_connectivity_split_root_aliases,
    _road_level_scope_fields,
    _run_road_connectivity_seed_probe,
    _safe_path_part,
    _synthesize_missing_plain_edge_types,
    _teacher_owner_ids,
)

PARTIAL_MAIN_COMPONENT_RATIO = 0.98



def _reference_visual_source_osm_path(
    build_report: Mapping[str, Any],
    source_osm_path: Path | None,
    required_highways: set[str],
    required_modal_way_tags: Mapping[str, set[str]] | None = None,
) -> Path | None:
    source_osm_value = build_report.get("source_osm_file") or source_osm_path
    if not source_osm_value:
        return None
    source = Path(str(source_osm_value))
    source_highways = _osm_highway_classes(source)
    if source_highways and not required_highways <= source_highways:
        return None
    for key, required_values in (required_modal_way_tags or {}).items():
        source_values = _osm_tag_values(source, key)
        if source_values is not None and not set(required_values) <= source_values:
            return None
    return source
def _blocked_place_report(
    place_name: str,
    output_dir: Path,
    place_report: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "status": "blocked",
        "claim_status": "blocked",
        "area_input": place_name,
        "area_resolution_status": "needs_user_confirmation",
        **_candidate_fields(place_report),
        "osm_preview_url": str(
            place_report.get("osm_preview_url", osm_preview_url(place_name))
            if place_report is not None
            else osm_preview_url(place_name)
        ),
        "user_confirmed_area": "no",
        "output_dir": str(output_dir),
        "gate_status": {
            "area_confirmation": "blocked",
            "road_level_scope": "not_started",
            "network_build": "not_started",
            "tls_reality_audit": "not_started",
            "connectivity": "not_started",
            "routeability_audit": "not_started",
            "netedit": "not_started",
            "sumo_gui": "not_started",
        },
        "warnings": list(place_report.get("warnings", []) if place_report is not None else [])
        + ["place-name input requires OSM preview and user confirmation before network construction"],
    }
def _blocked_road_level_scope_report(
    *,
    area_input: str,
    area_status: str,
    place_report: Mapping[str, Any] | None,
    cleaned_place_name: str,
    bbox: str,
    network_plan: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "status": "blocked",
        "claim_status": "blocked",
        "area_input": area_input,
        "area_resolution_status": area_status,
        **(
            _candidate_fields(place_report)
            if place_report is not None
            else {**_candidate_fields(None), "candidate_bbox": bbox}
        ),
        "osm_preview_url": (
            str(place_report.get("osm_preview_url", osm_preview_url(cleaned_place_name)))
            if place_report is not None
            else (osm_preview_url(cleaned_place_name) if cleaned_place_name else "")
        ),
        "user_confirmed_area": "yes" if area_status == "confirmed_by_user" else "confirmed_by_input",
        "road_level_scope_status": "needs_user_confirmation",
        "network_plan_status": str(network_plan.get("network_plan_status", "needs_user_confirmation")),
        **_road_level_scope_fields(),
        "traffic_layer_options": list(network_plan.get("traffic_layer_options", [])),
        "network_detail_options": list(network_plan.get("network_detail_options", [])),
        "recommended_network_detail": str(network_plan.get("recommended_network_detail", "")),
        "missing_blockers": list(network_plan.get("missing_blockers", ["network_plan"])),
        "next_question": str(network_plan.get("next_question", NETWORK_PLAN_QUESTION)),
        "gate_status": {
            "area_confirmation": "pass",
            "road_level_scope": "blocked",
            "network_build": "not_started",
            "tls_reality_audit": "not_started",
            "connectivity": "not_started",
            "routeability_audit": "not_started",
            "netedit": "not_started",
            "sumo_gui": "not_started",
        },
        "warnings": ["road level scope must be confirmed before OSM network construction"],
    }
def _teacher_guided_movement_gap_stats(report: Mapping[str, Any] | None) -> tuple[int, int, int, list[dict[str, Any]]]:
    if report is None:
        return 0, 0, 0, []
    gaps = []
    missing_plan_total = 0
    for candidate in report.get("repair_candidates", []) or []:
        if not isinstance(candidate, Mapping):
            continue
        gap = _int_field(candidate, "vehicle_movement_matrix_missing_count")
        missing_plan = candidate.get("missing_teacher_movement_plan", []) or []
        missing_plan_count = _int_field(candidate, "missing_teacher_movement_plan_count")
        if missing_plan_count <= 0 and isinstance(missing_plan, list):
            missing_plan_count = len(missing_plan)
        if gap <= 0 and missing_plan_count <= 0:
            continue
        missing_plan_total += missing_plan_count
        first_missing = (
            missing_plan[0]
            if isinstance(missing_plan, list) and missing_plan and isinstance(missing_plan[0], Mapping)
            else {}
        )
        gap_summary = {
            "reference_id": str(candidate.get("reference_id", "")),
            "junction_id": str(candidate.get("junction_id", "")),
            "candidate_status": str(candidate.get("candidate_status", "")),
            "vehicle_movement_matrix_missing_count": gap,
            "missing_teacher_movement_plan_count": missing_plan_count,
            "netedit_review_actions": [str(item) for item in candidate.get("netedit_review_actions", []) or []],
        }
        if first_missing:
            gap_summary["first_missing_teacher_movement"] = dict(first_missing)
        gaps.append(gap_summary)
    gaps.sort(key=lambda item: (-int(item["vehicle_movement_matrix_missing_count"]), item["reference_id"]))
    return len(gaps), int(gaps[0]["vehicle_movement_matrix_missing_count"]) if gaps else 0, missing_plan_total, gaps[:5]
def _junction_pattern_residual_stats(report: Mapping[str, Any] | None) -> dict[str, Any]:
    if report is None:
        return {
            "case_count": 0,
            "failed_case_count": 0,
            "mismatch_field_counts": {},
            "internal_function_count_deficits": {},
            "top_junction_pattern_mismatches": [],
        }
    comparisons = report.get("junction_pattern_comparisons", []) or []
    if not isinstance(comparisons, list):
        comparisons = []
    field_counts = report.get("junction_pattern_mismatch_field_counts", {}) or {}
    mismatch_field_counts = (
        {str(key): _intish(value) for key, value in field_counts.items()} if isinstance(field_counts, Mapping) else {}
    )
    internal_deficits: dict[str, int] = {}
    failed_examples: list[dict[str, Any]] = []
    for comparison in comparisons:
        if not isinstance(comparison, Mapping) or comparison.get("status") != "fail":
            continue
        fields = comparison.get("mismatch_fields", []) or []
        mismatch_fields = (
            [field for field in fields.split(";") if field]
            if isinstance(fields, str)
            else [str(field) for field in fields]
        )
        if not mismatch_field_counts:
            for field in mismatch_fields:
                mismatch_field_counts[field] = mismatch_field_counts.get(field, 0) + 1

        teacher = comparison.get("teacher", {})
        candidate = comparison.get("candidate", {})
        teacher_counts = teacher.get("internal_function_counts", {}) if isinstance(teacher, Mapping) else {}
        candidate_counts = candidate.get("internal_function_counts", {}) if isinstance(candidate, Mapping) else {}
        if not isinstance(teacher_counts, Mapping):
            teacher_counts = {}
        if not isinstance(candidate_counts, Mapping):
            candidate_counts = {}
        example_deficits = {}
        for function_name, teacher_count in teacher_counts.items():
            deficit = _intish(teacher_count) - _intish(candidate_counts.get(function_name, 0))
            if deficit <= 0:
                continue
            key = str(function_name)
            example_deficits[key] = deficit
            internal_deficits[key] = internal_deficits.get(key, 0) + deficit

        failed_examples.append(
            {
                "junction_id": str(comparison.get("junction_id", "")),
                "mismatch_fields": mismatch_fields,
                "teacher_control_type": teacher.get("control_type", "") if isinstance(teacher, Mapping) else "",
                "candidate_control_type": candidate.get("control_type", "") if isinstance(candidate, Mapping) else "",
                "teacher_has_tls": teacher.get("has_tls", "") if isinstance(teacher, Mapping) else "",
                "candidate_has_tls": candidate.get("has_tls", "") if isinstance(candidate, Mapping) else "",
                "internal_function_count_deficits": example_deficits,
            }
        )

    failed_case_count = _int_field(report, "junction_pattern_mismatch_count")
    return {
        "case_count": len(comparisons),
        "failed_case_count": failed_case_count if failed_case_count > 0 else len(failed_examples),
        "mismatch_field_counts": mismatch_field_counts,
        "internal_function_count_deficits": internal_deficits,
        "top_junction_pattern_mismatches": failed_examples[:5],
    }
def _connectivity_quality(connectivity_report: Mapping[str, Any]) -> dict[str, Any]:
    passenger_count = _int_field(connectivity_report, "passenger_edge_count")
    largest_count = _int_field(connectivity_report, "largest_component_edge_count")
    ratio = round(largest_count / passenger_count, 3) if passenger_count else 0.0
    strict_status = str(connectivity_report.get("connectivity_status", connectivity_report.get("status", "fail")))

    if strict_status == "pass":
        return {
            "connectivity_gate": "pass",
            "network_quality": "strict-connected",
            "strict_connectivity_status": "pass",
            "connectivity_main_component_ratio": ratio,
            "quality_warning": "",
        }
    if passenger_count and ratio >= PARTIAL_MAIN_COMPONENT_RATIO:
        return {
            "connectivity_gate": "partial",
            "network_quality": "partial-main-component",
            "strict_connectivity_status": "fail",
            "connectivity_main_component_ratio": ratio,
            "quality_warning": (
                f"strict connectivity failed; largest passenger component covers {ratio:.2%}; "
                "demote to diagnostic-demo and do not treat as experiment-ready"
            ),
        }
    return {
        "connectivity_gate": "fail",
        "network_quality": "construction-invalid",
        "strict_connectivity_status": "fail",
        "connectivity_main_component_ratio": ratio,
        "quality_warning": "",
    }
def _tls_gate_value(tls_report: Mapping[str, Any], tls_summary: Mapping[str, Any]) -> str:
    base_gate = _gate_value(tls_report)
    if base_gate != "pass":
        return base_gate
    if tls_summary.get("tls_reality_review_status") != "pass":
        return "blocked"
    return "pass"
def _routeability_scale_profile(
    connectivity_report: Mapping[str, Any],
    *,
    requested_vehicle_count: int | None,
    requested_initial_end: int | None,
    requested_max_end: int | None,
) -> dict[str, Any]:
    passenger_edge_count = _int_field(connectivity_report, "passenger_edge_count")
    if passenger_edge_count <= 1500:
        profile = "small"
        floor_vehicle_count = 50
        floor_initial_end = 180
        floor_max_end = 1200
    elif passenger_edge_count <= 6000:
        profile = "medium"
        floor_vehicle_count = 100
        floor_initial_end = 300
        floor_max_end = 2400
    elif passenger_edge_count <= 15000:
        profile = "large"
        floor_vehicle_count = 200
        floor_initial_end = 600
        floor_max_end = 3600
    else:
        profile = "metro"
        floor_vehicle_count = 300
        floor_initial_end = 900
        floor_max_end = 5400

    vehicle_count = max(requested_vehicle_count or 0, floor_vehicle_count)
    initial_end = max(requested_initial_end or 0, floor_initial_end)
    max_end = max(requested_max_end or 0, floor_max_end)
    requested = {
        "vehicle_count": requested_vehicle_count,
        "initial_end": requested_initial_end,
        "max_end": requested_max_end,
    }
    floor_applied = (
        requested_vehicle_count is not None
        and requested_vehicle_count < floor_vehicle_count
        or requested_initial_end is not None
        and requested_initial_end < floor_initial_end
        or requested_max_end is not None
        and requested_max_end < floor_max_end
    )
    if all(value is None for value in requested.values()):
        profile_status = "scale_profile_selected"
    elif floor_applied:
        profile_status = "scale_floor_applied"
    else:
        profile_status = "caller_values_confirmed"
    return {
        "routeability_audit_profile": profile,
        "routeability_audit_profile_status": profile_status,
        "routeability_audit_scale_basis": f"passenger_edge_count={passenger_edge_count}",
        "routeability_audit_vehicle_count": vehicle_count,
        "routeability_audit_initial_end": initial_end,
        "routeability_audit_max_end": max_end,
        "routeability_audit_floor_vehicle_count": floor_vehicle_count,
        "routeability_audit_floor_initial_end": floor_initial_end,
        "routeability_audit_floor_max_end": floor_max_end,
        "routeability_audit_requested_vehicle_count": requested_vehicle_count if requested_vehicle_count is not None else "",
        "routeability_audit_requested_initial_end": requested_initial_end if requested_initial_end is not None else "",
        "routeability_audit_requested_max_end": requested_max_end if requested_max_end is not None else "",
    }
def _reference_join_gate(report: Mapping[str, Any] | None) -> str:
    if report is None:
        return "skipped"
    if _int_field(report, "reference_case_count") == 0:
        return "skipped"
    return _gate_value(report)
def _junction_pattern_index_gate(report: Mapping[str, Any] | None) -> str:
    if report is None:
        return "skipped"
    if report.get("status") != "pass":
        return _gate_value(report)
    if report.get("junction_pattern_index"):
        return "pass"
    if _int_field(report, "reference_case_count") == 0:
        return "skipped"
    return "blocked"
def _teacher_guided_parity_gate(report: Mapping[str, Any] | None) -> str:
    if report is None:
        return "skipped"
    if report.get("status") != "pass":
        return _gate_value(report)
    if report.get("parity_gate_status") == "pass":
        return "pass"
    if _int_field(report, "repair_candidate_count") == 0:
        return "skipped"
    return "blocked"
def _teacher_guided_queue_has_replay_candidates(report: Mapping[str, Any] | None) -> bool:
    if report is None:
        return False
    return _int_field(report, "ready_candidate_count") > 0 or _int_field(report, "expanded_scope_candidate_count") > 0
def _first_teacher_owner_id(report: Mapping[str, Any] | None) -> str:
    owner_ids = _teacher_owner_ids(report, max_owner_count=1)
    return owner_ids[0] if owner_ids else ""
def _road_connectivity_owner_ids(
    queue_report: Mapping[str, Any] | None,
    seed_probe_report: Mapping[str, Any] | None,
    *,
    teacher_net_file: Path,
    max_owner_count: int | None = None,
) -> list[str]:
    if max_owner_count is not None and max_owner_count <= 0:
        return []
    owner_ids = _teacher_owner_ids(queue_report, max_owner_count=max_owner_count)
    seen = set(owner_ids)
    for owner_id in _road_connectivity_seed_geometry_owner_ids(seed_probe_report, teacher_net_file):
        if owner_id in seen:
            continue
        owner_ids.append(owner_id)
        seen.add(owner_id)
        if max_owner_count is not None and len(owner_ids) >= max_owner_count:
            break
    return owner_ids
def _road_connectivity_seed_delta_score(report: Mapping[str, Any] | None) -> int:
    if report is None:
        return 10**9
    return _int_field(report, "edge_delta_count") + _int_field(report, "connection_delta_count")
def _road_connectivity_gate_counts(report: Mapping[str, Any] | None) -> dict[str, dict[str, int]]:
    if report is None:
        return {}
    owner_reports = [item for item in report.get("owner_reports", []) or [] if isinstance(item, Mapping)]
    if owner_reports:
        pass_count = sum(1 for item in owner_reports if _road_connectivity_gate_status(item) == "pass")
        failure_count = sum(
            _int_field(item.get("owner_road_connectivity_audit", {}).get("gate", {}), "lane_delta_count")
            for item in owner_reports
            if isinstance(item.get("owner_road_connectivity_audit", {}), Mapping)
            and isinstance(item.get("owner_road_connectivity_audit", {}).get("gate", {}), Mapping)
        )
        return {
            "owner_road_connectivity": {
                "pass": pass_count,
                "fail": len(owner_reports) - pass_count,
                "failure_count": failure_count,
            }
        }
    audit = report.get("owner_road_connectivity_audit", {})
    audit = audit if isinstance(audit, Mapping) else {}
    gate = audit.get("gate", {})
    gate = gate if isinstance(gate, Mapping) else {}
    status = _road_connectivity_gate_status(report)
    return {
        "owner_road_connectivity": {
            "pass": 1 if status == "pass" else 0,
            "fail": 0 if status == "pass" else 1,
            "failure_count": _int_field(gate, "lane_delta_count"),
        }
    }
def _road_connectivity_parity_gate_status(
    parity_report: Mapping[str, Any] | None,
    replay_report: Mapping[str, Any] | None,
) -> str:
    """Prefer the complete road-layer audit over a local owner replay result."""

    if parity_report is not None:
        return str(parity_report.get("status", "blocked"))
    return _road_connectivity_gate_status(replay_report)
def _road_connectivity_best_variant_file(report: Mapping[str, Any] | None) -> Path | None:
    if report is None:
        return None
    output_value = str(report.get("output_file", "")).strip()
    if not output_value:
        return None
    output_file = Path(output_value)
    if not output_file.exists():
        return None
    if (
        str(report.get("status", "fail")) == "pass"
        and str(report.get("sumo_load_status", "fail")) == "pass"
        and _road_connectivity_gate_status(report) == "pass"
    ):
        return output_file
    for owner_report in report.get("owner_reports", []) or []:
        if not isinstance(owner_report, Mapping):
            continue
        if (
            str(owner_report.get("output_file", "")) == output_value
            and str(owner_report.get("status", "fail")) == "pass"
            and str(owner_report.get("sumo_load_status", "fail")) == "pass"
            and _road_connectivity_gate_status(owner_report) == "pass"
        ):
            return output_file
    return None
def _road_connectivity_replay_batch_report(
    owner_reports: list[Mapping[str, Any]], *, output_dir: Path, prefix: str
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    reports = [dict(report) for report in owner_reports]
    gate_statuses = [_road_connectivity_gate_status(report) for report in reports]
    sumo_load_statuses = [str(report.get("sumo_load_status", "fail")) for report in reports]
    status = (
        "pass"
        if reports
        and all(str(report.get("status", "fail")) == "pass" for report in reports)
        and all(status == "pass" for status in gate_statuses)
        and all(status == "pass" for status in sumo_load_statuses)
        else "fail"
    )
    lane_delta_count = sum(
        _int_field(report.get("owner_road_connectivity_audit", {}).get("gate", {}), "lane_delta_count")
        for report in reports
        if isinstance(report.get("owner_road_connectivity_audit", {}), Mapping)
        and isinstance(report.get("owner_road_connectivity_audit", {}).get("gate", {}), Mapping)
    )
    best_output = next(
        (
            str(report.get("output_file", ""))
            for report in reversed(reports)
            if report.get("output_file")
            and str(report.get("status", "fail")) == "pass"
            and str(report.get("sumo_load_status", "fail")) == "pass"
            and (
                _road_connectivity_gate_status(report) == "pass"
                or bool(report.get("road_connectivity_seed_probe_improved"))
            )
        ),
        "",
    )
    batch_report = {
        "status": status,
        "claim_status": "diagnostic-demo",
        "owner_count": len(reports),
        "pass_owner_count": sum(1 for item in gate_statuses if item == "pass"),
        "output_file": best_output,
        "sumo_load_status": "pass" if reports and all(item == "pass" for item in sumo_load_statuses) else "fail",
        "owner_road_connectivity_audit": {
            "status": "pass" if reports and all(item == "pass" for item in gate_statuses) else "fail",
            "gate": {"lane_delta_count": lane_delta_count},
        },
        "owner_reports": reports,
    }
    run_report_file = output_dir / f"{prefix}_batch.json"
    batch_report["run_report_file"] = str(run_report_file)
    run_report_file.write_text(json.dumps(batch_report, indent=2, ensure_ascii=False), encoding="utf-8")
    return batch_report
def _teacher_guided_seed_candidate(
    primary_report: Mapping[str, Any],
    *,
    primary_structural_only: bool,
    fallback_reports: list[tuple[str, Mapping[str, Any] | None]],
) -> tuple[Mapping[str, Any], bool, bool, str]:
    if _reference_join_audit_can_seed_teacher_guided_queue(
        primary_report,
        structural_only=primary_structural_only,
    ):
        return primary_report, primary_structural_only, False, "reference_join_audit"
    for source, report in fallback_reports:
        if report is None:
            continue
        if _reference_join_audit_can_seed_teacher_guided_queue(report, structural_only=True):
            return report, True, True, source
    return primary_report, primary_structural_only, False, "reference_join_audit"
def _teacher_guided_best_variant_file(report: Mapping[str, Any] | None) -> Path | None:
    if report is None:
        return None
    composite_net_file = str(report.get("composite_net_file", ""))
    has_accepted_composite = _int_field(report, "composite_applied_candidate_count") > 0 or (
        report.get("status") == "pass" and report.get("parity_gate_status") == "pass" and bool(composite_net_file)
    )
    if has_accepted_composite and Path(composite_net_file).exists():
        return Path(composite_net_file)
    return None
def _normalize_sumo_net(
    *,
    net_file: Path,
    output_file: Path,
    output_dir: Path,
    netconvert_binary: str,
    timeout_seconds: float,
    command_runner: Callable[..., Any],
) -> dict[str, Any]:
    output_file.parent.mkdir(parents=True, exist_ok=True)
    command = [
        netconvert_binary,
        "--sumo-net-file",
        _command_path_for_cwd(net_file, output_dir),
        "--output-file",
        _command_path_for_cwd(output_file, output_dir),
    ]
    report = _command_result_report(command_runner(command, cwd=output_dir, timeout_seconds=timeout_seconds))
    report["source_net_file"] = str(net_file)
    report["output_file"] = str(output_file)
    return report
def _run_tls_movement_routeability_smoke(
    *,
    net_file: Path,
    controller_id: str,
    output_dir: Path,
    prefix: str,
    sumo_binary: str,
    timeout_seconds: float,
    command_runner: Callable[..., Any],
) -> dict[str, Any]:
    """Run one explicit route through every controlled movement in a cell."""

    if not net_file.exists():
        return {"status": "blocked", "reason": "routeability_net_file_missing", "net_file": str(net_file)}
    try:
        root = ET.parse(net_file).getroot()
    except (ET.ParseError, OSError, ValueError) as exc:
        return {"status": "blocked", "reason": f"routeability_net_parse_failed:{type(exc).__name__}"}
    all_controlled_connections = sorted(
        [
            connection
            for connection in root.findall("connection")
            if connection.attrib.get("tl") == controller_id and connection.attrib.get("linkIndex") is not None
        ],
        key=lambda connection: (
            int(connection.attrib["linkIndex"])
            if connection.attrib.get("linkIndex", "").isdigit()
            else connection.attrib.get("linkIndex", ""),
            connection.attrib.get("from", ""),
            connection.attrib.get("to", ""),
        ),
    )
    if not all_controlled_connections:
        return {
            "status": "blocked",
            "reason": "routeability_no_controlled_connections",
            "controller_id": controller_id,
        }
    edges_by_id = {
        edge.attrib.get("id", ""): edge
        for edge in root.findall("edge")
        if edge.attrib.get("id")
    }
    non_vehicle_functions = {"internal", "crossing", "walkingarea"}

    def lane_allows_passenger(edge_id: str, lane_index: str) -> bool:
        edge = edges_by_id.get(edge_id)
        if edge is None:
            return False
        lane = next(
            (item for item in edge.findall("lane") if item.attrib.get("index") == lane_index),
            None,
        )
        if lane is None:
            return False
        allow = set(lane.attrib.get("allow", "").split())
        disallow = set(lane.attrib.get("disallow", "").split())
        if allow and not ("passenger" in allow or "all" in allow or "private" in allow):
            return False
        return not bool({"passenger", "all"} & disallow)

    controlled_connections = [
        connection
        for connection in all_controlled_connections
        if all(
            edges_by_id.get(connection.attrib.get(attr, "")) is not None
            and edges_by_id[connection.attrib.get(attr, "")].attrib.get("function") not in non_vehicle_functions
            and lane_allows_passenger(
                connection.attrib.get(attr, ""),
                connection.attrib.get("fromLane" if attr == "from" else "toLane", ""),
            )
            for attr in ("from", "to")
        )
    ]
    excluded_non_vehicle_connection_count = len(all_controlled_connections) - len(controlled_connections)
    if not controlled_connections:
        return {
            "status": "pass",
            "claim_status": "diagnostic-demo",
            "reason": "no_vehicle_controlled_connections",
            "controller_id": controller_id,
            "controlled_connection_count": 0,
            "excluded_non_vehicle_connection_count": excluded_non_vehicle_connection_count,
        }
    # This is a smoke check for the cell, not a replacement for the full
    # connection/TLS parity audit.  One passenger movement avoids creating a
    # synthetic traffic jam when several controller links share the same
    # approach edge; the semantic gate below still checks every controlled
    # movement and every linkIndex.
    smoke_connections = controlled_connections[:1]
    edge_ids = set(edges_by_id)
    invalid_routes = [
        dict(connection.attrib)
        for connection in controlled_connections
        if connection.attrib.get("from") not in edge_ids or connection.attrib.get("to") not in edge_ids
    ]
    if invalid_routes:
        return {
            "status": "blocked",
            "reason": "routeability_controlled_edge_missing",
            "controller_id": controller_id,
            "controlled_connection_count": len(controlled_connections),
            "invalid_routes": invalid_routes,
        }

    output_dir.mkdir(parents=True, exist_ok=True)
    # This directory is already unique per candidate.  Keep filenames short
    # because Windows resolves the full path before SUMO opens the outputs.
    route_file = output_dir / "m.rou.xml"
    summary_file = output_dir / "m.sum.xml"
    tripinfo_file = output_dir / "m.ti.xml"
    summary_file.unlink(missing_ok=True)
    tripinfo_file.unlink(missing_ok=True)
    route_root = ET.Element("routes")
    ET.SubElement(route_root, "vType", {"id": "torii_smoke_passenger", "vClass": "passenger", "maxSpeed": "13.9"})
    for position, connection in enumerate(smoke_connections):
        vehicle = ET.SubElement(
            route_root,
            "vehicle",
            {
                "id": f"{prefix}_movement_{position}_{connection.attrib.get('linkIndex', position)}",
                "type": "torii_smoke_passenger",
                "depart": str(position * 2),
            },
        )
        ET.SubElement(
            vehicle,
            "route",
            {
                "edges": f"{connection.attrib.get('from', '')} {connection.attrib.get('to', '')}",
            },
        )
    ET.indent(route_root, space="    ")
    route_file.parent.mkdir(parents=True, exist_ok=True)
    route_file.write_text(
        ET.tostring(route_root, encoding="unicode"),
        encoding="utf-8",
    )
    end_time = max(300, len(smoke_connections) * 2 + 180)
    command = [
        sumo_binary,
        "--net-file",
        str(net_file.resolve()),
        "--route-files",
        str(route_file.resolve()),
        "--begin",
        "0",
        "--end",
        str(end_time),
        "--summary-output",
        str(summary_file.resolve()),
        "--tripinfo-output",
        str(tripinfo_file.resolve()),
        "--duration-log.disable",
        "true",
    ]
    command_report = _command_result_report(
        command_runner(command, cwd=output_dir.resolve(), timeout_seconds=timeout_seconds)
    )
    last_step: dict[str, str] = {}
    if summary_file.exists():
        try:
            summary_root = ET.parse(summary_file).getroot()
            steps = summary_root.findall("step")
            if steps:
                last_step = dict(steps[-1].attrib)
        except (ET.ParseError, OSError, ValueError):
            last_step = {}
    expected_count = len(smoke_connections)
    loaded = int(last_step.get("loaded", "-1")) if last_step.get("loaded", "").isdigit() else -1
    arrived = int(last_step.get("arrived", "-1")) if last_step.get("arrived", "").isdigit() else -1
    inserted = int(last_step.get("inserted", "-1")) if last_step.get("inserted", "").isdigit() else -1
    teleports = int(last_step.get("teleports", "0")) if last_step.get("teleports", "0").isdigit() else 0
    collisions = int(last_step.get("collisions", "0")) if last_step.get("collisions", "0").isdigit() else 0
    status = "pass" if (
        command_report.get("status") == "pass"
        and loaded == expected_count
        and inserted == expected_count
        and arrived == expected_count
        and teleports == 0
        and collisions == 0
    ) else "blocked"
    return {
        "status": status,
        "claim_status": "diagnostic-demo" if status == "pass" else "construction-invalid",
        "controller_id": controller_id,
        "net_file": str(net_file),
        "route_file": str(route_file),
        "summary_file": str(summary_file),
        "tripinfo_file": str(tripinfo_file),
        "controlled_connection_count": len(controlled_connections),
        "tested_connection_count": expected_count,
        "all_controlled_connection_count": len(all_controlled_connections),
        "excluded_non_vehicle_connection_count": excluded_non_vehicle_connection_count,
        "loaded": loaded,
        "inserted": inserted,
        "arrived": arrived,
        "teleports": teleports,
        "collisions": collisions,
        "end_time": end_time,
        "command": command_report,
        "last_summary_step": last_step,
    }
def _teacher_guided_application_stats(
    report: Mapping[str, Any] | None, best_variant_file: Path | None
) -> dict[str, str | int]:
    pass_count = 0 if report is None else _int_field(report, "pass_candidate_count")
    composite_applied_count = 0 if report is None else _int_field(report, "composite_applied_candidate_count")
    applied_count = composite_applied_count if composite_applied_count and best_variant_file is not None else 0
    if not applied_count and best_variant_file is not None:
        applied_count = 1
    if report is None:
        scope = "skipped"
    elif composite_applied_count > 1 and best_variant_file is not None:
        scope = "sequential_composite"
    elif applied_count:
        scope = "single_best_variant"
    else:
        scope = "none"
    return {
        "teacher_guided_repair_application_scope": scope,
        "teacher_guided_repair_applied_candidate_count": applied_count,
        "teacher_guided_repair_unapplied_pass_candidate_count": max(0, pass_count - applied_count),
    }
def export_plain_net_for_teacher_guided_repair(
    *,
    net_file: Path,
    output_dir: Path,
    prefix: str,
    netconvert_binary: str = "netconvert",
    timeout_seconds: float = 240.0,
    command_runner: Callable[..., Any] = run_command,
) -> dict[str, Any]:
    net_file = net_file.resolve()
    output_dir = output_dir.resolve()
    if not net_file.exists():
        return {
            "status": "fail",
            "claim_status": "construction-invalid",
            "error": f"net file does not exist: {net_file}",
        }

    output_dir.mkdir(parents=True, exist_ok=True)
    plain_prefix, plain_prefix_shortened, plain_prefix_digest = _plain_output_prefix(output_dir, prefix)
    source_copy = output_dir / "plain_source.net.xml"
    try:
        if source_copy.resolve() != net_file.resolve():
            shutil.copyfile(net_file, source_copy)
    except OSError as exc:
        return {
            "status": "fail",
            "claim_status": "construction-invalid",
            "error": f"{type(exc).__name__}: {exc}",
        }
    command = [
        netconvert_binary,
        "--sumo-net-file",
        _command_path_for_cwd(source_copy, output_dir),
        "--plain-output-prefix",
        str(plain_prefix),
    ]
    result = command_runner(command, cwd=output_dir, timeout_seconds=timeout_seconds)
    if hasattr(result, "to_dict"):
        netconvert_report = result.to_dict()
    elif isinstance(result, dict):
        netconvert_report = dict(result)
    else:
        netconvert_report = {
            "status": getattr(result, "status", "fail"),
            "returncode": getattr(result, "returncode", None),
        }
    if "status" not in netconvert_report:
        netconvert_report["status"] = "pass" if netconvert_report.get("returncode") == 0 else "fail"

    raw_node_file = Path(f"{plain_prefix}.nod.xml")
    raw_edge_file = Path(f"{plain_prefix}.edg.xml")
    raw_connection_file = Path(f"{plain_prefix}.con.xml")
    raw_type_file = Path(f"{plain_prefix}.typ.xml")
    raw_tllogic_file = Path(f"{plain_prefix}.tll.xml")
    synthesized_edge_type_ids = _synthesize_missing_plain_edge_types(raw_edge_file, raw_type_file)
    false_tls_plain_node_restore_report = _restore_false_traffic_light_plain_node_types(
        source_net_file=net_file,
        node_file=raw_node_file,
    )
    stale_plain_tllogic_prune_report = _prune_stale_plain_tllogics(
        node_file=raw_node_file,
        tllogic_file=raw_tllogic_file,
    )
    missing_required = [
        str(path)
        for path in (raw_node_file, raw_edge_file, raw_connection_file)
        if not path.exists()
    ]
    status = (
        "pass"
        if netconvert_report.get("status") == "pass"
        and not missing_required
        and false_tls_plain_node_restore_report.get("status") == "pass"
        and stale_plain_tllogic_prune_report.get("status") in {"pass", "skipped"}
        else "fail"
    )
    return {
        "status": status,
        "claim_status": "diagnostic-demo" if status == "pass" else "construction-invalid",
        "net_file": str(net_file),
        "plain_source_net_file": str(source_copy),
        "plain_output_prefix": str(plain_prefix),
        "requested_plain_output_prefix": str(output_dir / prefix),
        "plain_output_prefix_shortened": plain_prefix_shortened,
        "plain_output_prefix_digest": plain_prefix_digest,
        "raw_node_file": str(raw_node_file),
        "raw_edge_file": str(raw_edge_file),
        "raw_connection_file": str(raw_connection_file),
        "raw_type_file": str(raw_type_file) if raw_type_file.exists() else "",
        "raw_tllogic_file": str(raw_tllogic_file) if raw_tllogic_file.exists() else "",
        "synthesized_edge_type_count": len(synthesized_edge_type_ids),
        "synthesized_edge_type_ids": synthesized_edge_type_ids,
        "false_traffic_light_plain_node_restore": false_tls_plain_node_restore_report,
        "stale_plain_tllogic_prune": stale_plain_tllogic_prune_report,
        "restored_false_traffic_light_plain_node_count": false_tls_plain_node_restore_report.get(
            "restored_false_traffic_light_plain_node_count", 0
        ),
        "restored_false_traffic_light_plain_node_ids": false_tls_plain_node_restore_report.get(
            "restored_false_traffic_light_plain_node_ids", []
        ),
        "removed_stale_plain_tllogic_count": stale_plain_tllogic_prune_report.get(
            "removed_stale_plain_tllogic_count", 0
        ),
        "removed_stale_plain_tllogic_ids": stale_plain_tllogic_prune_report.get(
            "removed_stale_plain_tllogic_ids", []
        ),
        "removed_stale_plain_tllogic_connection_count": stale_plain_tllogic_prune_report.get(
            "removed_stale_plain_tllogic_connection_count", 0
        ),
        "missing_required_plain_files": missing_required,
        "netconvert": netconvert_report,
    }
def _reference_join_aggregation_gate(report: Mapping[str, Any] | None) -> str:
    if report is None:
        return "skipped"
    if report.get("junction_aggregation_status") == "not_needed":
        return "skipped"
    if report.get("status") != "pass":
        return _gate_value(report)
    if _int_field(report, "junction_aggregation_candidate_count") > 0:
        return "blocked"
    return "pass"
def _reference_scope_gate(report: Mapping[str, Any] | None) -> str:
    if report is None:
        return "skipped"
    return _gate_value(report)
def _reference_bbox_scope_gate(report: Mapping[str, Any] | None) -> str:
    if report is None:
        return "skipped"
    # Tiny synthetic/reference fixtures and legacy hand-authored nets may not
    # carry SUMO <location> projection metadata.  They cannot prove a
    # geographic bbox match, but they should remain usable for non-geographic
    # unit/workflow tests.  The claim-tier evaluator still treats skipped as
    # insufficient for reference_aligned claims.
    if (
        report.get("status") == "blocked"
        and "projection metadata" in str(report.get("error", ""))
    ):
        return "skipped"
    return _gate_value(report)
def _reference_scope_pruning_gate(report: Mapping[str, Any] | None) -> str:
    if report is None:
        return "skipped"
    if report.get("scope_pruning_status") == "not_needed":
        return "skipped"
    if report.get("status") != "pass":
        return _gate_value(report)
    if report.get("scope_pruning_promotion_status") == "pass":
        return "pass"
    if report.get("scope_pruning_status") == "variant_created_for_review":
        return "blocked"
    return "pass"
def _scope_pruning_promotion_decision(
    *,
    pruning_report: Mapping[str, Any] | None,
    post_scope_report: Mapping[str, Any] | None,
    sumo_load_report: Mapping[str, Any] | None,
    source_net_file: Path | None = None,
    variant_net_file: Path | None = None,
) -> dict[str, Any]:
    """Promote only a scope variant that is safe for the visual-detail layer."""

    if pruning_report is None:
        return {"status": "skipped", "reason": "not_run"}
    checks = {
        "variant_netconvert": pruning_report.get("scope_pruning_netconvert", {}).get("status")
        if isinstance(pruning_report.get("scope_pruning_netconvert"), Mapping)
        else "fail",
        "sumo_load": sumo_load_report.get("status") if isinstance(sumo_load_report, Mapping) else "fail",
        "post_scope_audit": post_scope_report.get("status") if isinstance(post_scope_report, Mapping) else "fail",
        "modal_only": pruning_report.get("scope_pruning_modal_only_status", "blocked"),
        "modal_leaf_continuity": pruning_report.get("scope_pruning_modal_leaf_continuity_status", "blocked"),
        "vehicle_core_impact": pruning_report.get("scope_pruning_vehicle_core_impact_status", "blocked"),
    }
    source_controlled = _controlled_tls_connection_count_from_net_file(source_net_file)
    variant_controlled = _controlled_tls_connection_count_from_net_file(variant_net_file)
    controlled_tls_check = (
        "pass"
        if source_controlled is not None
        and variant_controlled is not None
        and variant_controlled >= source_controlled
        else "blocked"
    )
    checks["controlled_tls_connection_preservation"] = controlled_tls_check
    status = "pass" if all(value == "pass" for value in checks.values()) else "blocked"
    return {
        "status": status,
        "scope_pruning_promotion_status": status,
        "reason": (
            "scope_variant_promoted_after_netconvert_sumo_load_post_scope_audit_and_modal_safety"
            if status == "pass"
            else "scope_variant_not_safe_for_promotion"
        ),
        "checks": checks,
        "post_scope_audit_file": str(post_scope_report.get("report_file", ""))
        if isinstance(post_scope_report, Mapping)
        else "",
        "sumo_load_status": sumo_load_report.get("status", "fail")
        if isinstance(sumo_load_report, Mapping)
        else "fail",
        "source_controlled_connection_count": source_controlled,
        "variant_controlled_connection_count": variant_controlled,
        "controlled_connection_regression_count": (
            max(source_controlled - variant_controlled, 0)
            if source_controlled is not None and variant_controlled is not None
            else None
        ),
    }
def _reference_hierarchy_gate(report: Mapping[str, Any] | None) -> str:
    if report is None:
        return "skipped"
    return _gate_value(report)
def _should_run_tls_aggregation(
    tls_report: Mapping[str, Any],
    tls_aggregation_func: Callable[..., dict[str, Any]],
) -> bool:
    if _int_field(tls_report, "tls_cluster_count") <= 0:
        return False
    clusters_file = Path(str(tls_report.get("clusters_file", "")))
    return clusters_file.exists() or tls_aggregation_func is not build_tls_aggregation_variant
def _controlled_tls_connection_count_from_delta(report: Mapping[str, Any] | None) -> int | None:
    if report is None:
        return None
    return _controlled_tls_connection_count_from_net_file(report.get("candidate_net_file"))
def _tls_semantic_delta_score(report: Mapping[str, Any] | None) -> int:
    if report is None:
        return 0
    return _delta_count_score(report.get("network_structural_missing_counts", {})) + _delta_count_score(
        report.get("network_structural_extra_counts", {})
    )
def _total_structural_delta_score(report: Mapping[str, Any] | None) -> int:
    if report is None:
        return 0
    total = 0
    for field in ("network_structural_missing_counts", "network_structural_extra_counts"):
        counts = report.get(field, {})
        if isinstance(counts, Mapping):
            total += sum(_intish(value) for value in counts.values())
    return total
def _sumo_load_net(
    net_file: Path,
    *,
    output_dir: Path,
    sumo_binary: str,
    timeout_seconds: float,
    command_runner: Callable[..., Any],
) -> dict[str, Any]:
    if not net_file.exists():
        return {"status": "fail", "error": f"net file does not exist: {net_file}"}
    output_dir.mkdir(parents=True, exist_ok=True)
    load_net_file = output_dir / "sumo_load_candidate.net.xml"
    try:
        if load_net_file.resolve() != net_file.resolve():
            shutil.copyfile(net_file, load_net_file)
    except OSError as exc:
        return {"status": "fail", "error": f"{type(exc).__name__}: {exc}"}
    command = [
        sumo_binary,
        "-n",
        _command_path_for_cwd(load_net_file, output_dir),
        "--no-step-log",
        "true",
        "--duration-log.disable",
        "true",
        "--begin",
        "0",
        "--end",
        "1",
    ]
    report = _command_result_report(command_runner(command, cwd=output_dir, timeout_seconds=timeout_seconds))
    report["source_net_file"] = str(net_file)
    report["load_net_file"] = str(load_net_file)
    if report.get("status") == "pass":
        return report
    sumo_path = Path(sumo_binary)
    netconvert_binary = sumo_path.with_name("netconvert.exe" if sumo_path.suffix.lower() == ".exe" else "netconvert")
    if not netconvert_binary.exists():
        resolved_netconvert = shutil.which(str(netconvert_binary)) or shutil.which(netconvert_binary.name)
        if not resolved_netconvert:
            return report
        netconvert_binary = Path(resolved_netconvert)
    normalized_net_file = output_dir / "sumo_load_candidate_normalized.net.xml"
    netconvert_command = [
        str(netconvert_binary),
        "--sumo-net-file",
        _command_path_for_cwd(load_net_file, output_dir),
        "--output-file",
        _command_path_for_cwd(normalized_net_file, output_dir),
    ]
    netconvert_report = _command_result_report(
        command_runner(netconvert_command, cwd=output_dir, timeout_seconds=timeout_seconds)
    )
    if netconvert_report.get("status") != "pass" or not normalized_net_file.exists():
        report["normalization_netconvert"] = netconvert_report
        return report
    retry_command = [
        sumo_binary,
        "-n",
        _command_path_for_cwd(normalized_net_file, output_dir),
        "--no-step-log",
        "true",
        "--duration-log.disable",
        "true",
        "--begin",
        "0",
        "--end",
        "1",
    ]
    retry_report = _command_result_report(
        command_runner(retry_command, cwd=output_dir, timeout_seconds=timeout_seconds)
    )
    retry_report["source_net_file"] = str(net_file)
    retry_report["load_net_file"] = str(normalized_net_file)
    retry_report["direct_sumo_load"] = report
    retry_report["normalization_netconvert"] = netconvert_report
    return retry_report
def _effective_tls_controlled_connection_preservation(
    *,
    aggregation_report: Mapping[str, Any] | None,
    repair_report: Mapping[str, Any] | None,
    repair_promotion_report: Mapping[str, Any] | None,
    effective_net_file: Path | None,
) -> dict[str, Any]:
    if (
        repair_report is not None
        and repair_report.get("status") == "pass"
        and repair_promotion_report is not None
        and repair_promotion_report.get("status") == "pass"
    ):
        controlled_after = _int_field(repair_report, "candidate_tls_controlled_connection_count_after")
        effective_count = _controlled_tls_connection_count_from_net_file(effective_net_file)
        if effective_count is not None:
            controlled_after = effective_count
        source_controlled = _int_field(repair_report, "source_tls_controlled_connection_count")
        if source_controlled == 0:
            source_controlled = controlled_after
        regression_count = max(source_controlled - controlled_after, 0)
        return {
            "source": "promoted_tls_connection_repair",
            "network_file": str(effective_net_file or repair_report.get("variant_file", "")),
            "source_controlled_connection_count": source_controlled,
            "controlled_connection_count": controlled_after,
            "controlled_connection_preservation_status": "pass" if regression_count == 0 else "fail",
            "controlled_connection_regression_count": regression_count,
        }
    if aggregation_report is not None:
        return {
            "source": "tls_aggregation_variant",
            "network_file": str(effective_net_file or aggregation_report.get("tls_aggregation_variant_file", "")),
            "source_controlled_connection_count": _int_field(
                aggregation_report, "source_tls_controlled_connection_count"
            ),
            "controlled_connection_count": _int_field(
                aggregation_report, "tls_aggregated_controlled_connection_count"
            ),
            "controlled_connection_preservation_status": str(
                aggregation_report.get("tls_controlled_connection_preservation_status", "pass")
            ),
            "controlled_connection_regression_count": _int_field(
                aggregation_report, "tls_controlled_connection_regression_count"
            ),
        }
    return {
        "source": "not_run",
        "network_file": str(effective_net_file or ""),
        "source_controlled_connection_count": 0,
        "controlled_connection_count": 0,
        "controlled_connection_preservation_status": "skipped",
        "controlled_connection_regression_count": 0,
    }
def _movement_rebuild_mismatch_score(report: Mapping[str, Any] | None) -> int:
    if report is None:
        return 0
    field_counts = report.get("junction_pattern_mismatch_field_counts", {})
    if isinstance(field_counts, Mapping) and field_counts:
        return sum(
            _intish(field_counts.get(field, 0))
            for field in ("movement_signature_counts", "internal_function_counts")
        )
    return _int_field(report, "junction_pattern_mismatch_count")
def _structural_delta_key_count(report: Mapping[str, Any] | None, key: str) -> int:
    if report is None:
        return 0
    total = 0
    for field in ("network_structural_missing_counts", "network_structural_extra_counts"):
        counts = report.get(field, {})
        if isinstance(counts, Mapping):
            total += _intish(counts.get(key, 0))
    return total
def _restore_followup_internal_regressions(
    *,
    baseline_delta_report: Mapping[str, Any] | None,
    followup_delta_report: Mapping[str, Any] | None,
    baseline_net_file: Path,
    followup_net_file: Path,
    output_dir: Path,
    prefix: str,
) -> dict[str, Any]:
    if followup_delta_report is None or followup_delta_report.get("status", "pass") != "pass":
        return {"status": "skipped", "reason": "followup_delta_not_pass"}
    baseline_failed = _delta_failed_fields_by_junction(baseline_delta_report)
    restore_ids = [
        junction_id
        for junction_id, fields in _delta_failed_fields_by_junction(followup_delta_report).items()
        if "internal_function_counts" in fields and "internal_function_counts" not in baseline_failed.get(junction_id, set())
    ]
    if not restore_ids:
        return {"status": "skipped", "reason": "no_internal_regressions", "restored_junction_ids": []}
    output_dir.mkdir(parents=True, exist_ok=True)
    restored_net_file = output_dir / f"{prefix}_internal_regressions_restored.net.xml"
    shutil.copyfile(followup_net_file, restored_net_file)
    restore_reports = []
    for junction_id in restore_ids:
        report = _restore_replayed_geometry_attrs(
            source_file=baseline_net_file,
            target_file=restored_net_file,
            junction_id=junction_id,
        )
        restore_reports.append({"junction_id": junction_id, **report})
        if report.get("status") != "pass":
            return {
                "status": "fail",
                "reason": "internal_regression_restore_failed",
                "restored_net_file": str(restored_net_file),
                "restored_junction_ids": restore_ids,
                "restore_reports": restore_reports,
            }
    return {
        "status": "pass",
        "reason": "internal_regressions_restored",
        "restored_net_file": str(restored_net_file),
        "restored_junction_ids": restore_ids,
        "restore_reports": restore_reports,
    }
def _topology_metric(report: Mapping[str, Any], key: str) -> int:
    if key == "junction_aggregation_candidate_count" and key not in report:
        return int(_junction_aggregation_summary(report)["junction_aggregation_candidate_count"])
    return _int_field(report, key)


def _junction_semantic_gate(report: Mapping[str, Any] | None, fields: set[str]) -> str:
    if report is None:
        return "skipped"
    if report.get("status") != "pass":
        return _gate_value(report)
    comparisons = report.get("junction_pattern_comparisons", []) or []
    has_comparison_evidence = isinstance(comparisons, list) and len(comparisons) > 0
    if _int_field(report, "matched_case_count") == 0 and not has_comparison_evidence:
        return "skipped"
    stats = _junction_pattern_residual_stats(report)
    field_counts = stats["mismatch_field_counts"]
    if _int_field(report, "junction_pattern_mismatch_count") > 0 and not field_counts:
        return "blocked"
    return "blocked" if any(_intish(field_counts.get(field, 0)) > 0 for field in fields) else "pass"
def _run_owner_road_connectivity_replay(
    *,
    teacher_net_file: Path,
    candidate_net_file: Path,
    output_dir: Path,
    prefix: str,
    owner_id: str,
    sumo_binary: str,
    timeout_seconds: float,
    command_runner: Callable[..., Any],
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    safe_owner = _safe_path_part(owner_id)
    output_file = output_dir / f"{prefix}_{safe_owner}.net.xml"
    report = write_internal_movement_owner_layered_teacher_replay_candidate(
        teacher_net_file,
        candidate_net_file,
        output_file,
        owner_id=owner_id,
        copy_tls=True,
        max_ready_spans=2,
        pre_repair_ready_road_spans=True,
        replay_blocked_road_span_endpoint_owners=True,
    )
    best_variant_file = Path(str(report.get("output_file", output_file)))
    sumo_load = _sumo_load_net(
        best_variant_file,
        output_dir=output_dir / "sumo_load",
        sumo_binary=sumo_binary,
        timeout_seconds=timeout_seconds,
        command_runner=command_runner,
    )
    report = dict(report)
    report["sumo_load"] = sumo_load
    report["sumo_load_status"] = str(sumo_load.get("status", "fail"))
    run_report_file = output_dir / f"{prefix}_{safe_owner}.json"
    report["run_report_file"] = str(run_report_file)
    run_report_file.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    return report
def _tls_connection_repair_promotion_decision(
    *,
    repair_report: Mapping[str, Any] | None,
    sumo_load_report: Mapping[str, Any] | None,
    repair_delta_report: Mapping[str, Any] | None,
    rejected_delta_report: Mapping[str, Any] | None,
) -> dict[str, Any]:
    if repair_report is None or repair_report.get("status") != "pass":
        return {"status": "blocked", "reason": "repair_not_pass"}
    variant_file = Path(str(repair_report.get("variant_file", "")))
    if not variant_file.exists():
        return {"status": "blocked", "reason": "repair_variant_missing"}
    if _int_field(repair_report, "skipped_invalid_mapped_linkindex_connection_count") > 0:
        return {"status": "blocked", "reason": "invalid_mapped_linkindex_skipped"}
    if sumo_load_report is None or sumo_load_report.get("status") != "pass":
        return {"status": "blocked", "reason": "sumo_load_not_pass"}
    if _has_tls_incompatibility_warning(sumo_load_report):
        return {"status": "blocked", "reason": "sumo_load_tls_incompatible"}
    if repair_delta_report is None or repair_delta_report.get("status") != "pass":
        return {"status": "blocked", "reason": "repair_reference_delta_not_pass"}
    repair_score = _tls_semantic_delta_score(repair_delta_report)
    rejected_score = _tls_semantic_delta_score(rejected_delta_report)
    if repair_score > rejected_score:
        return {
            "status": "blocked",
            "reason": "reference_tls_semantic_delta_regressed",
            "repair_tls_semantic_delta_score": repair_score,
            "rejected_tls_semantic_delta_score": rejected_score,
        }
    return {
        "status": "pass",
        "reason": "tls_connection_repair_promoted_after_sumo_load_and_reference_delta",
        "repair_tls_semantic_delta_score": repair_score,
        "rejected_tls_semantic_delta_score": rejected_score,
    }
def _reference_hierarchy_type_repair_promotion_decision(
    *,
    baseline_audit_report: Mapping[str, Any] | None,
    candidate_audit_report: Mapping[str, Any] | None,
    sumo_load_report: Mapping[str, Any] | None,
) -> dict[str, Any]:
    if sumo_load_report is None or sumo_load_report.get("status") != "pass":
        return {"status": "blocked", "reason": "sumo_load_not_pass"}
    if candidate_audit_report is None:
        return {"status": "blocked", "reason": "reference_hierarchy_type_repair_audit_missing"}
    if _reference_hierarchy_gate(candidate_audit_report) == "pass":
        return {
            "status": "pass",
            "reason": "reference_hierarchy_type_repair_promoted_after_sumo_load_and_audit",
        }
    if baseline_audit_report is None:
        return {"status": "blocked", "reason": "reference_hierarchy_baseline_audit_missing"}

    baseline_issues = _int_field(baseline_audit_report, "high_hierarchy_issue_count")
    candidate_issues = _int_field(candidate_audit_report, "high_hierarchy_issue_count")
    baseline_counts = baseline_audit_report.get("decision_counts", {})
    candidate_counts = candidate_audit_report.get("decision_counts", {})
    if not isinstance(baseline_counts, Mapping) or not isinstance(candidate_counts, Mapping):
        return {"status": "blocked", "reason": "reference_hierarchy_decision_counts_missing"}

    baseline_type_mismatches = int(baseline_counts.get("type_hierarchy_mismatch", 0) or 0)
    candidate_type_mismatches = int(candidate_counts.get("type_hierarchy_mismatch", 0) or 0)
    risk_regressions = {
        decision: {
            "baseline": int(baseline_counts.get(decision, 0) or 0),
            "candidate": int(candidate_counts.get(decision, 0) or 0),
        }
        for decision in ("out_of_reference_scope", "link_or_slip_lane")
        if int(candidate_counts.get(decision, 0) or 0) > int(baseline_counts.get(decision, 0) or 0)
    }
    aligned_regressed = int(candidate_counts.get("aligned", 0) or 0) < int(
        baseline_counts.get("aligned", 0) or 0
    )
    if (
        candidate_issues < baseline_issues
        and candidate_type_mismatches < baseline_type_mismatches
        and not risk_regressions
        and not aligned_regressed
    ):
        return {
            "status": "pass",
            "reason": "reference_hierarchy_type_repair_promoted_by_strict_improvement",
            "baseline_issue_count": baseline_issues,
            "candidate_issue_count": candidate_issues,
            "baseline_type_mismatch_count": baseline_type_mismatches,
            "candidate_type_mismatch_count": candidate_type_mismatches,
            "risk_regressions": {},
        }
    return {
        "status": "blocked",
        "reason": "reference_hierarchy_type_repair_not_strictly_better",
        "baseline_issue_count": baseline_issues,
        "candidate_issue_count": candidate_issues,
        "baseline_type_mismatch_count": baseline_type_mismatches,
        "candidate_type_mismatch_count": candidate_type_mismatches,
        "risk_regressions": risk_regressions,
        "aligned_regressed": aligned_regressed,
    }
def _corridor_geometry_simplification_promotion_decision(
    *,
    variant_report: Mapping[str, Any] | None,
    sumo_load_report: Mapping[str, Any] | None,
    baseline_delta_report: Mapping[str, Any] | None,
    candidate_delta_report: Mapping[str, Any] | None,
    baseline_topology_report: Mapping[str, Any] | None,
    candidate_topology_report: Mapping[str, Any] | None,
) -> dict[str, Any]:
    if variant_report is None or variant_report.get("status") != "pass":
        return {"status": "blocked", "reason": "corridor_variant_not_pass"}
    if variant_report.get("semantic_preservation_status") != "pass":
        return {"status": "blocked", "reason": "corridor_semantic_preservation_not_pass"}
    connection_audit = variant_report.get("alias_normalized_connection_audit", {})
    if not isinstance(connection_audit, Mapping) or any(
        int(connection_audit.get(field, 0) or 0) > 0
        for field in ("normal_missing_count", "normal_extra_count", "controlled_missing_count", "controlled_extra_count")
    ):
        return {"status": "blocked", "reason": "corridor_alias_normalized_connections_regressed"}
    if sumo_load_report is None or sumo_load_report.get("status") != "pass":
        return {"status": "blocked", "reason": "corridor_sumo_load_not_pass"}
    if candidate_delta_report is None or candidate_delta_report.get("status") != "pass":
        return {"status": "blocked", "reason": "corridor_reference_delta_not_pass"}
    if baseline_delta_report is None or baseline_delta_report.get("status") != "pass":
        return {"status": "blocked", "reason": "corridor_baseline_reference_delta_not_pass"}
    baseline_score = _tls_semantic_delta_score(baseline_delta_report)
    candidate_score = _tls_semantic_delta_score(candidate_delta_report)
    if candidate_score > baseline_score:
        return {
            "status": "blocked",
            "reason": "corridor_reference_tls_semantic_delta_regressed",
            "baseline_tls_semantic_delta_score": baseline_score,
            "candidate_tls_semantic_delta_score": candidate_score,
        }
    if baseline_topology_report is None or candidate_topology_report is None:
        return {"status": "blocked", "reason": "corridor_topology_audit_missing"}
    topology_metrics = (
        "suspicious_cluster_count",
        "junction_aggregation_candidate_count",
        "physical_intersection_candidate_count",
        "topology_connection_cell_candidate_count",
        "max_cluster_node_count",
    )
    topology_regressions = {
        metric: {
            "baseline": _int_field(baseline_topology_report, metric),
            "candidate": _int_field(candidate_topology_report, metric),
        }
        for metric in topology_metrics
        if _int_field(candidate_topology_report, metric) > _int_field(baseline_topology_report, metric)
    }
    if topology_regressions:
        return {
            "status": "blocked",
            "reason": "corridor_topology_regressed",
            "topology_regressions": topology_regressions,
        }
    return {
        "status": "pass",
        "reason": "corridor_geometry_simplification_promoted_by_semantic_and_topology_gates",
        "baseline_tls_semantic_delta_score": baseline_score,
        "candidate_tls_semantic_delta_score": candidate_score,
        "topology_regressions": {},
    }
def _reference_topology_parity_gate(
    candidate_report: Mapping[str, Any] | None,
    reference_report: Mapping[str, Any] | None,
) -> dict[str, Any]:
    if candidate_report is None:
        return {"status": "skipped", "reason": "candidate_topology_audit_not_run", "metrics": {}}

    candidate_gate = _gate_value(candidate_report)
    if candidate_gate == "pass":
        return {"status": "pass", "reason": "candidate_topology_audit_passed", "metrics": {}}
    if candidate_gate == "fail":
        return {"status": "fail", "reason": "candidate_topology_audit_failed", "metrics": {}}
    if reference_report is None:
        return {
            "status": candidate_gate,
            "reason": "reference_topology_audit_not_available",
            "metrics": {},
        }
    if _gate_value(reference_report) == "fail":
        return {
            "status": candidate_gate,
            "reason": "reference_topology_audit_failed",
            "metrics": {},
        }

    if (
        "topology_canonical_cell_records" in candidate_report
        and "topology_canonical_cell_records" in reference_report
    ):
        return compare_topology_canonical_cells(candidate_report, reference_report)

    metric_keys = (
        "suspicious_cluster_count",
        "junction_aggregation_candidate_count",
        "physical_intersection_candidate_count",
    )
    metrics = {
        key: {
            "candidate": _topology_metric(candidate_report, key),
            "reference": _topology_metric(reference_report, key),
        }
        for key in metric_keys
    }
    exceeded = {
        key: value
        for key, value in metrics.items()
        if int(value["candidate"]) > int(value["reference"])
    }
    if exceeded:
        return {
            "status": "blocked",
            "reason": "candidate_topology_exceeds_reference",
            "metrics": metrics,
            "exceeded_metrics": exceeded,
        }
    return {
        "status": "pass",
        "reason": "candidate_topology_not_more_fragmented_than_reference",
        "metrics": metrics,
        "exceeded_metrics": {},
    }


def _teacher_guided_junction_parity_gate(
    report: Mapping[str, Any] | None,
    semantic_parity_report: Mapping[str, Any] | None,
) -> str:
    if (
        _junction_semantic_gate(
            semantic_parity_report,
            {
                "approach_edge_ids",
                "control_type",
                "has_tls",
                "internal_function_counts",
                "movement_signature_counts",
                "request_bit_lengths_ok",
            },
        )
        == "pass"
    ):
        return "pass"
    return _teacher_guided_parity_gate(report)
def _road_connectivity_seed_probe_improved(
    before: Mapping[str, Any] | None,
    after: Mapping[str, Any] | None,
) -> bool:
    return _road_connectivity_seed_delta_score(after) < _road_connectivity_seed_delta_score(before)
def _run_teacher_guided_queue_replay(
    *,
    queue_report: dict[str, Any] | None,
    source_net_file: Path,
    plain_output_dir: Path,
    run_output_dir: Path,
    prefix: str,
    netconvert_binary: str,
    sumo_binary: str,
    timeout_seconds: float,
    max_ready_candidates: int | None,
    plain_export_func: Callable[..., dict[str, Any]],
    repair_run_func: Callable[..., dict[str, Any]],
) -> tuple[dict[str, Any] | None, dict[str, Any] | None, Path | None]:
    if not _teacher_guided_queue_has_replay_candidates(queue_report):
        return None, None, None

    plain_export_report = plain_export_func(
        net_file=source_net_file,
        output_dir=plain_output_dir,
        prefix=prefix,
        netconvert_binary=netconvert_binary,
        timeout_seconds=timeout_seconds,
    )
    if plain_export_report.get("status") != "pass":
        return plain_export_report, None, None

    raw_type_value = str(plain_export_report.get("raw_type_file", ""))
    raw_tllogic_value = str(plain_export_report.get("raw_tllogic_file", ""))
    queue_file_value = str(queue_report.get("queue_file", "")) if queue_report is not None else ""
    run_report = repair_run_func(
        queue_report=queue_report,
        raw_node_file=Path(str(plain_export_report["raw_node_file"])),
        raw_edge_file=Path(str(plain_export_report["raw_edge_file"])),
        raw_connection_file=Path(str(plain_export_report["raw_connection_file"])),
        raw_type_file=Path(raw_type_value) if raw_type_value else None,
        raw_tllogic_file=Path(raw_tllogic_value) if raw_tllogic_value else None,
        output_dir=run_output_dir,
        prefix=prefix,
        queue_base_dir=Path(queue_file_value).resolve().parent if queue_file_value else None,
        replay_target_internal_subgraph=True,
        max_ready_candidates=max_ready_candidates,
        netconvert_binary=netconvert_binary,
        sumo_binary=sumo_binary,
        timeout_seconds=timeout_seconds,
        sequential_accept_passed_variants=True,
        plain_exporter=plain_export_func,
    )
    return plain_export_report, run_report, _teacher_guided_best_variant_file(run_report)
def _road_connectivity_promoted_variant_file(
    replay_report: Mapping[str, Any] | None,
    split_root_alias_report: Mapping[str, Any] | None,
    topology_replay_report: Mapping[str, Any] | None,
) -> Path | None:
    for report in (topology_replay_report, split_root_alias_report):
        if report is None or report.get("status") != "pass":
            continue
        output_value = str(report.get("output_file", "")).strip()
        if output_value and Path(output_value).exists():
            return Path(output_value)
    return _road_connectivity_best_variant_file(replay_report)
def _reference_delta_promotion_decision(
    *,
    candidate_delta_report: Mapping[str, Any] | None,
    baseline_delta_report: Mapping[str, Any] | None,
    reason: str,
) -> dict[str, Any]:
    if candidate_delta_report is None or candidate_delta_report.get("status") != "pass":
        return {"status": "blocked", "reason": "candidate_reference_delta_not_pass"}
    if baseline_delta_report is None or baseline_delta_report.get("status") != "pass":
        return {"status": "blocked", "reason": "baseline_reference_delta_not_pass"}
    candidate_score = _tls_semantic_delta_score(candidate_delta_report)
    baseline_score = _tls_semantic_delta_score(baseline_delta_report)
    if candidate_score > baseline_score:
        return {
            "status": "blocked",
            "reason": "reference_tls_semantic_delta_regressed",
            "candidate_tls_semantic_delta_score": candidate_score,
            "baseline_tls_semantic_delta_score": baseline_score,
        }
    return {
        "status": "pass",
        "reason": reason,
        "candidate_tls_semantic_delta_score": candidate_score,
        "baseline_tls_semantic_delta_score": baseline_score,
    }


def _run_road_connectivity_split_root_alias_repair(
    *,
    teacher_net_file: Path,
    candidate_net_file: Path,
    seed_probe_report: Mapping[str, Any] | None,
    seed_edge_ids: list[str],
    output_dir: Path,
    prefix: str,
    sumo_binary: str,
    timeout_seconds: float,
    command_runner: Callable[..., Any],
) -> dict[str, Any]:
    split_root_aliases = _road_connectivity_split_root_aliases(seed_probe_report)
    if not split_root_aliases:
        return {
            "status": "skipped",
            "claim_status": "diagnostic-demo",
            "reason": "no_split_root_aliases",
        }
    output_dir.mkdir(parents=True, exist_ok=True)
    output_file = output_dir / f"{prefix}_split_root_alias_repaired.net.xml"
    repair = write_road_connectivity_split_root_alias_repair_candidate(
        candidate_net_file,
        output_file,
        split_root_aliases,
    )
    sumo_load = _sumo_load_net(
        output_file,
        output_dir=output_dir / "sumo_load",
        sumo_binary=sumo_binary,
        timeout_seconds=timeout_seconds,
        command_runner=command_runner,
    )
    seed_probe = None
    improved = False
    if str(sumo_load.get("status", "fail")) == "pass":
        seed_probe = _run_road_connectivity_seed_probe(
            teacher_net_file=teacher_net_file,
            candidate_net_file=output_file,
            seed_edge_ids=seed_edge_ids,
            output_dir=output_dir / "seed_probe",
            prefix=f"{prefix}_split_root_alias_seed_probe",
        )
        improved = _road_connectivity_seed_probe_improved(seed_probe_report, seed_probe)
    report = {
        "status": "pass" if improved else "fail",
        "claim_status": "diagnostic-demo",
        "candidate_net_file": str(candidate_net_file),
        "output_file": str(output_file),
        "selected_alias_count": len(split_root_aliases),
        "repair": repair,
        "sumo_load": sumo_load,
        "sumo_load_status": str(sumo_load.get("status", "fail")),
        "seed_probe": seed_probe or {},
        "seed_probe_improved": improved,
    }
    report_file = output_dir / f"{prefix}_split_root_alias_repair.json"
    report["report_file"] = str(report_file)
    report_file.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    return report
def _run_road_connection_topology_replay(
    *,
    teacher_net_file: Path,
    candidate_net_file: Path,
    seed_probe_report: Mapping[str, Any] | None,
    seed_edge_ids: list[str],
    output_dir: Path,
    prefix: str,
    sumo_binary: str,
    timeout_seconds: float,
    command_runner: Callable[..., Any],
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    output_file = output_dir / f"{prefix}_road_connection_topology_replayed.net.xml"
    replay = write_road_connection_topology_replay_candidate(
        teacher_net_file,
        candidate_net_file,
        output_file,
    )
    sumo_load = _sumo_load_net(
        output_file,
        output_dir=output_dir / "sumo_load",
        sumo_binary=sumo_binary,
        timeout_seconds=timeout_seconds,
        command_runner=command_runner,
    )
    seed_probe = None
    improved = False
    if str(sumo_load.get("status", "fail")) == "pass":
        seed_probe = _run_road_connectivity_seed_probe(
            teacher_net_file=teacher_net_file,
            candidate_net_file=output_file,
            seed_edge_ids=seed_edge_ids,
            output_dir=output_dir / "seed_probe",
            prefix=f"{prefix}_road_connection_topology_seed_probe",
        )
        improved = _road_connectivity_seed_probe_improved(seed_probe_report, seed_probe)
    report = {
        "status": "pass" if improved else "fail",
        "claim_status": "diagnostic-demo",
        "candidate_net_file": str(candidate_net_file),
        "output_file": str(output_file),
        "replay": replay,
        "sumo_load": sumo_load,
        "sumo_load_status": str(sumo_load.get("status", "fail")),
        "seed_probe": seed_probe or {},
        "seed_probe_improved": improved,
    }
    report_file = output_dir / f"{prefix}_road_connection_topology_replay.json"
    report["report_file"] = str(report_file)
    report_file.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    return report


def _run_road_connectivity_replay_sequence(
    *,
    teacher_net_file: Path,
    candidate_net_file: Path,
    queue_report: Mapping[str, Any] | None,
    seed_probe_report: Mapping[str, Any] | None,
    seed_edge_ids: list[str],
    output_dir: Path,
    prefix: str,
    max_owner_count: int | None,
    sumo_binary: str,
    timeout_seconds: float,
    command_runner: Callable[..., Any],
    road_connectivity_replay_func: Callable[..., dict[str, Any]],
    road_connectivity_seed_probe_func: Callable[..., dict[str, Any]],
    road_connection_topology_replay_func: Callable[..., dict[str, Any]],
) -> tuple[dict[str, Any] | None, dict[str, Any] | None, dict[str, Any] | None, dict[str, Any] | None]:
    owner_ids = _road_connectivity_owner_ids(
        queue_report,
        seed_probe_report,
        teacher_net_file=teacher_net_file,
        max_owner_count=max_owner_count,
    )
    if not owner_ids:
        return None, seed_probe_report, None, None

    owner_reports = []
    current_seed_probe_report = seed_probe_report
    road_connectivity_candidate_net_file = candidate_net_file
    for owner_id in owner_ids:
        owner_report = dict(
            road_connectivity_replay_func(
                teacher_net_file=teacher_net_file,
                candidate_net_file=road_connectivity_candidate_net_file,
                output_dir=output_dir / "road_connectivity_replay",
                prefix=f"{prefix}_road_connectivity",
                owner_id=owner_id,
                sumo_binary=sumo_binary,
                timeout_seconds=timeout_seconds,
                command_runner=command_runner,
            )
        )
        owner_report.setdefault("owner_id", owner_id)
        owner_seed_probe_report = None
        owner_seed_probe_improved = False
        if (
            seed_edge_ids
            and owner_report.get("output_file")
            and str(owner_report.get("status", "fail")) == "pass"
            and str(owner_report.get("sumo_load_status", "fail")) == "pass"
            and _road_connectivity_gate_status(owner_report) != "pass"
        ):
            owner_seed_probe_report = road_connectivity_seed_probe_func(
                teacher_net_file=teacher_net_file,
                candidate_net_file=Path(str(owner_report["output_file"])),
                seed_edge_ids=seed_edge_ids,
                output_dir=output_dir / "road_connectivity_seed_probe",
                prefix=f"{prefix}_road_connectivity_seed_probe_after_{_safe_path_part(owner_id)}",
            )
            owner_seed_probe_improved = _road_connectivity_seed_probe_improved(
                current_seed_probe_report,
                owner_seed_probe_report,
            )
            owner_report["road_connectivity_seed_probe"] = owner_seed_probe_report
            owner_report["road_connectivity_seed_probe_improved"] = owner_seed_probe_improved
            if owner_report.get("run_report_file"):
                Path(str(owner_report["run_report_file"])).write_text(
                    json.dumps(owner_report, indent=2, ensure_ascii=False),
                    encoding="utf-8",
                )
        owner_reports.append(owner_report)
        if (
            owner_report.get("output_file")
            and str(owner_report.get("status", "fail")) == "pass"
            and str(owner_report.get("sumo_load_status", "fail")) == "pass"
            and (_road_connectivity_gate_status(owner_report) == "pass" or owner_seed_probe_improved)
        ):
            road_connectivity_candidate_net_file = Path(str(owner_report["output_file"]))
            if owner_seed_probe_improved and owner_seed_probe_report is not None:
                current_seed_probe_report = owner_seed_probe_report

    replay_report = (
        owner_reports[0]
        if len(owner_reports) == 1
        else _road_connectivity_replay_batch_report(
            owner_reports,
            output_dir=output_dir / "road_connectivity_replay",
            prefix=f"{prefix}_road_connectivity",
        )
    )
    replay_variant_file = _road_connectivity_best_variant_file(replay_report)
    split_root_alias_repair_report = None
    if seed_edge_ids and replay_variant_file is not None and _road_connectivity_split_root_aliases(
        current_seed_probe_report
    ):
        split_root_alias_repair_report = _run_road_connectivity_split_root_alias_repair(
            teacher_net_file=teacher_net_file,
            candidate_net_file=replay_variant_file,
            seed_probe_report=current_seed_probe_report,
            seed_edge_ids=seed_edge_ids,
            output_dir=output_dir / "road_connectivity_split_root_alias_repair",
            prefix=f"{prefix}_road_connectivity",
            sumo_binary=sumo_binary,
            timeout_seconds=timeout_seconds,
            command_runner=command_runner,
        )
        if split_root_alias_repair_report.get("status") == "pass":
            current_seed_probe_report = dict(split_root_alias_repair_report.get("seed_probe", {}))

    topology_source_net_file = (
        Path(str(split_root_alias_repair_report["output_file"]))
        if split_root_alias_repair_report is not None
        and split_root_alias_repair_report.get("status") == "pass"
        and split_root_alias_repair_report.get("output_file")
        else replay_variant_file
    )
    topology_replay_report = None
    if (
        seed_edge_ids
        and topology_source_net_file is not None
        and current_seed_probe_report is not None
        and str(current_seed_probe_report.get("status", "fail")) != "pass"
    ):
        topology_replay_report = road_connection_topology_replay_func(
            teacher_net_file=teacher_net_file,
            candidate_net_file=topology_source_net_file,
            seed_probe_report=current_seed_probe_report,
            seed_edge_ids=seed_edge_ids,
            output_dir=output_dir / "road_connection_topology_replay",
            prefix=f"{prefix}_road_connection_topology",
            sumo_binary=sumo_binary,
            timeout_seconds=timeout_seconds,
            command_runner=command_runner,
        )
        if topology_replay_report.get("status") == "pass":
            current_seed_probe_report = dict(topology_replay_report.get("seed_probe", {}))

    return replay_report, current_seed_probe_report, split_root_alias_repair_report, topology_replay_report
def _movement_rebuild_reference_delta_promotion_decision(
    *,
    candidate_delta_report: Mapping[str, Any] | None,
    baseline_delta_report: Mapping[str, Any] | None,
    structural_guard_delta_report: Mapping[str, Any] | None = None,
    reason: str,
) -> dict[str, Any]:
    decision = _reference_delta_promotion_decision(
        candidate_delta_report=candidate_delta_report,
        baseline_delta_report=baseline_delta_report,
        reason=reason,
    )

    candidate_movement_score = _movement_rebuild_mismatch_score(candidate_delta_report)
    baseline_movement_score = _movement_rebuild_mismatch_score(baseline_delta_report)
    candidate_tls_junction_count = _structural_delta_key_count(candidate_delta_report, "traffic_light_junction_count")
    baseline_tls_junction_count = _structural_delta_key_count(baseline_delta_report, "traffic_light_junction_count")
    candidate_structural_score = _total_structural_delta_score(candidate_delta_report)
    baseline_structural_score = _total_structural_delta_score(baseline_delta_report)
    candidate_controlled_connection_count = _controlled_tls_connection_count_from_delta(candidate_delta_report)
    baseline_controlled_connection_count = _controlled_tls_connection_count_from_delta(baseline_delta_report)
    controlled_connection_regression_count = 0
    if candidate_controlled_connection_count is not None and baseline_controlled_connection_count is not None:
        controlled_connection_regression_count = max(
            baseline_controlled_connection_count - candidate_controlled_connection_count,
            0,
        )
    guard_structural_score = (
        _total_structural_delta_score(structural_guard_delta_report)
        if structural_guard_delta_report is not None
        else baseline_structural_score
    )
    movement_fields = {
        "candidate_movement_rebuild_mismatch_score": candidate_movement_score,
        "baseline_movement_rebuild_mismatch_score": baseline_movement_score,
        "candidate_traffic_light_junction_delta_count": candidate_tls_junction_count,
        "baseline_traffic_light_junction_delta_count": baseline_tls_junction_count,
        "candidate_total_structural_delta_score": candidate_structural_score,
        "baseline_total_structural_delta_score": baseline_structural_score,
        "guard_total_structural_delta_score": guard_structural_score,
        "candidate_controlled_connection_count": candidate_controlled_connection_count,
        "baseline_controlled_connection_count": baseline_controlled_connection_count,
        "controlled_connection_regression_count": controlled_connection_regression_count,
    }
    if controlled_connection_regression_count > 0:
        return {
            **decision,
            **movement_fields,
            "status": "blocked",
            "reason": "controlled_tls_connection_regressed",
        }
    structural_regression_allowance = 100
    if candidate_structural_score > guard_structural_score + structural_regression_allowance:
        return {
            **decision,
            **movement_fields,
            "structural_delta_regression_allowance": structural_regression_allowance,
            "status": "blocked",
            "reason": "reference_structural_delta_regressed",
        }
    candidate_tls_score = _tls_semantic_delta_score(candidate_delta_report)
    baseline_tls_score = _tls_semantic_delta_score(baseline_delta_report)
    if (
        candidate_movement_score >= baseline_movement_score
        and candidate_structural_score >= baseline_structural_score
        and candidate_tls_score >= baseline_tls_score
    ):
        return {
            **decision,
            **movement_fields,
            "status": "blocked",
            "reason": "movement_rebuild_no_reference_delta_improvement",
        }
    if decision.get("reason") != "reference_tls_semantic_delta_regressed":
        return {**decision, **movement_fields}
    if (
        candidate_movement_score < baseline_movement_score
        and candidate_tls_junction_count <= baseline_tls_junction_count
        and candidate_structural_score <= guard_structural_score + structural_regression_allowance
    ):
        return {
            **decision,
            **movement_fields,
            "status": "pass",
            "reason": reason,
        }
    return {**decision, **movement_fields}
