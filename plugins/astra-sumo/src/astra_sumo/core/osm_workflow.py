from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Callable, Literal, Mapping

from .osm_area import osm_map_url_bbox, osm_preview_url, resolve_osm_place
from .connectivity import extract_largest_passenger_component_core, summarize_passenger_connectivity
from .connection_mode_audit import build_network_connection_mode_audit
from .corridor_edit_ledger import build_corridor_edit_ledger
from .corridor_simplification import build_corridor_geometry_simplification_variant
from .command_runner import run_command
from .junction_aggregation import build_junction_aggregation_variant
from .junction_rebuild_candidate import (
    _stage_file,
    build_shared_teacher_tls_controller_replay_plan,
    build_scoped_teacher_tls_cell_replay_plan,
    build_teacher_guided_repair_queue,
    build_tls_connection_repair_variant,
    _restore_false_traffic_light_junction_types,
    _restore_replayed_geometry_attrs,
    restore_off_scope_netconvert_artifacts,
    run_teacher_guided_repair_matrix,
    restore_teacher_tls_connection_semantics_after_normalize,
    restore_scoped_pedestrian_internal_semantics_after_normalize,
    run_teacher_guided_repair_queue,
    write_scoped_teacher_tls_cell_replay_net,
    write_shared_teacher_tls_controller_replay_net,
    write_teacher_target_internal_replay_net,
    write_teacher_tllogic_net,
)
from .junction_teacher_model import extract_teacher_junction_model
from .netedit import launch_netedit
from .network_permissions import apply_service_passenger_permissions
from .network_plan import NETWORK_PLAN_QUESTION, derive_network_plan  # noqa: F401
from .osm_network import audit_tls, build_osm_network, build_routeability_probe, regional_map_baseline_for_bbox
from .reference_bbox import derive_reference_net_bbox
from .reference_hierarchy import audit_reference_hierarchy, build_reference_hierarchy_type_repair_variant
from .junction_connection_audit import (
    compare_shared_tls_via_path_semantics,
    compare_tls_via_path_semantics,
)
from .reference_join_audit import audit_reference_join_patterns
from .tls_gap_mapping import (
    audit_tls_gap_variant_semantics,
    build_tls_gap_destination_mapping,
    build_tls_gap_repair_variant,
    build_tls_repair_decision_report,
)
from .road_connectivity_teacher_model import audit_road_connectivity_parity
from .reference_scope import (
    audit_reference_scope,
    build_reference_bbox_variant,
    build_scope_pruning_variant,
)
from .routeability_audit import run_routeability_audit
from .road_scope import (  # noqa: F401 - compatibility re-export
    ROAD_LEVEL_SCOPE_OPTIONS,
    RECOMMENDED_ROAD_LEVEL_SCOPE,
)
from .standard_nema_binding import build_standard_nema_phase_binding
from .sumo_gui import launch_sumo_gui
from .tls_aggregation import (
    build_tls_aggregation_variant,
    build_tls_low_vehicle_control_variant,
    build_tls_non_controller_junction_demotion_variant,
    build_tls_signal_grouping_variant,
)
from .topology_audit import audit_topology_fragmentation
from .workflow_review_html import build_workflow_review_html

from .osm_workflow_helpers import (  # noqa: F401 - compatibility re-export
    _candidate_fields,
    _class_set,
    _command_path_for_cwd,
    _command_result_report,
    _connection_mode_gate_value,
    _controlled_tls_connection_count_from_net_file,
    _delta_count_score,
    _delta_failed_fields_by_junction,
    _filter_teacher_guided_queue_to_mismatch_fields,
    _followup_reference_delta_structural_only,
    _gate_value,
    _has_tls_incompatibility_warning,
    _hash_scoped_tls_batch_artifacts,
    _int_field,
    _intish,
    _junction_aggregation_summary,
    _list_field_count,
    _load_review_decisions_file,
    _low_vehicle_control_candidate_limits,
    _osm_highway_classes,
    _osm_tag_values,
    _plain_output_prefix,
    _prune_stale_plain_tllogics,
    _queue_path_value,
    _reference_bbox_fields,
    _reference_join_audit_can_seed_teacher_guided_queue,
    _reference_visual_tls_guess_signal_distances,
    _restore_false_traffic_light_plain_node_types,
    _road_connectivity_gate_status,
    _road_connectivity_seed_geometry_owner_ids,
    _road_connectivity_split_root_aliases,
    _road_level_scope_fields,
    _run_road_connectivity_seed_probe,
    _safe_path_part,
    _same_path_value,
    _scoped_tls_batch_artifact_paths,
    _supports_keyword,
    _synthesize_missing_plain_edge_types,
    _teacher_guided_direct_replay_needed,
    _teacher_guided_equivalent_approach_edge_map,
    _teacher_guided_exemplar_ready_stats,
    _teacher_owner_ids,
    _teacher_tls_has_multiple_internal_owners,
    _tls_aggregation_preserves_controlled_connections,
    _tls_control_review_category_counts,
    _tls_guess_signal_distance_label,
    _tls_representative_id_map,
    _tls_review_summary,
)

from .osm_workflow_tail import (  # noqa: F401 - compatibility re-export
    _blocked_place_report,
    _blocked_road_level_scope_report,
    _connectivity_quality,
    _controlled_tls_connection_count_from_delta,
    _corridor_geometry_simplification_promotion_decision,
    _effective_tls_controlled_connection_preservation,
    _first_teacher_owner_id,
    _junction_pattern_index_gate,
    _junction_pattern_residual_stats,
    _junction_semantic_gate,
    _movement_rebuild_mismatch_score,
    _movement_rebuild_reference_delta_promotion_decision,
    _normalize_sumo_net,
    _reference_bbox_scope_gate,
    _reference_delta_promotion_decision,
    _reference_hierarchy_gate,
    _reference_hierarchy_type_repair_promotion_decision,
    _reference_join_aggregation_gate,
    _reference_join_gate,
    _reference_scope_gate,
    _reference_scope_pruning_gate,
    _reference_topology_parity_gate,
    _reference_visual_source_osm_path,
    _restore_followup_internal_regressions,
    _road_connectivity_best_variant_file,
    _road_connectivity_gate_counts,
    _road_connectivity_owner_ids,
    _road_connectivity_parity_gate_status,
    _road_connectivity_promoted_variant_file,
    _road_connectivity_replay_batch_report,
    _road_connectivity_seed_delta_score,
    _road_connectivity_seed_probe_improved,
    _routeability_scale_profile,
    _run_owner_road_connectivity_replay,
    _run_road_connection_topology_replay,
    _run_road_connectivity_replay_sequence,
    _run_road_connectivity_split_root_alias_repair,
    _run_teacher_guided_queue_replay,
    _run_tls_movement_routeability_smoke,
    _scope_pruning_promotion_decision,
    _should_run_tls_aggregation,
    _structural_delta_key_count,
    _sumo_load_net,
    _teacher_guided_application_stats,
    _teacher_guided_best_variant_file,
    _teacher_guided_junction_parity_gate,
    _teacher_guided_movement_gap_stats,
    _teacher_guided_parity_gate,
    _teacher_guided_queue_has_replay_candidates,
    _teacher_guided_seed_candidate,
    _tls_connection_repair_promotion_decision,
    _tls_gate_value,
    _tls_semantic_delta_score,
    _topology_metric,
    _total_structural_delta_score,
    export_plain_net_for_teacher_guided_repair,
)


PARTIAL_MAIN_COMPONENT_RATIO = 0.98
TLS_SEMANTIC_DELTA_KEYS = {
    "tl_logic_count",
    "traffic_light_junction_count",
    "tls_controlled_connection_count",
    "multi_junction_tl_logic_count",
    "traffic_light_junction_without_tls_connection_count",
    "tls_shared_linkindex_group_count",
    "tls_sparse_linkindex_tl_logic_count",
}


def _run_direct_local_teacher_replay(
    *,
    queue_report: dict[str, Any] | None,
    source_net_file: Path,
    output_dir: Path,
    prefix: str,
    netconvert_binary: str,
    sumo_binary: str,
    timeout_seconds: float,
    command_runner: Callable[..., Any],
    shared_controller_source_net_file: Path | None = None,
) -> dict[str, Any]:
    if not _teacher_guided_queue_has_replay_candidates(queue_report):
        return {"status": "skipped", "reason": "no_replay_candidates", "variant_reports": []}
    teacher_net_file = _queue_path_value(queue_report, "teacher_net_file")
    if teacher_net_file is None or not teacher_net_file.exists():
        return {"status": "blocked", "reason": "teacher_net_file_missing", "variant_reports": []}
    if not source_net_file.exists():
        return {"status": "blocked", "reason": "source_net_file_missing", "variant_reports": []}

    output_dir.mkdir(parents=True, exist_ok=True)
    candidates = [
        candidate for candidate in queue_report.get("repair_candidates", []) or [] if isinstance(candidate, Mapping)
    ]
    variant_reports: list[dict[str, Any]] = []
    for index, candidate in enumerate(candidates, start=1):
        tls_candidate_junction_id = str(candidate.get("tls_candidate_tl_id", "")).strip()
        tls_candidate_junction_ids = {
            str(item)
            for item in candidate.get("tls_candidate_junction_ids", []) or []
            if str(item)
        }
        scoped_candidate = bool(tls_candidate_junction_id and len(tls_candidate_junction_ids) > 1)
        candidate_status = str(candidate.get("candidate_status", ""))
        if candidate_status != "ready_for_teacher_guided_variant" and not (
            scoped_candidate and candidate_status == "needs_expanded_rebuild_scope"
        ):
            continue
        junction_id = str(
            tls_candidate_junction_id
            or candidate.get("junction_id")
            or candidate.get("candidate_junction_id")
            or candidate.get("candidate_id")
            or ""
        ).strip()
        teacher_junction_id = str(
            candidate.get("tls_reference_tl_id")
            or candidate.get("reference_id")
            or candidate.get("teacher_junction_id")
            or ""
        ).strip()
        teacher_junction_id = teacher_junction_id or junction_id
        edge_map_value = candidate.get("edge_map", {})
        edge_map = {str(key): str(value) for key, value in edge_map_value.items()} if isinstance(edge_map_value, Mapping) else {}
        scoped_plan: dict[str, Any] | None = None
        shared_controller_plan: dict[str, Any] | None = None
        shared_controller_candidate = False
        candidate_source_net_file = source_net_file
        candidate_shared_source = str(candidate.get("shared_controller_candidate_net_file", "")).strip()
        candidate_has_shared_controller = _teacher_tls_has_multiple_internal_owners(
            teacher_net_file=teacher_net_file,
            teacher_junction_id=teacher_junction_id,
        )
        if candidate_shared_source and candidate_has_shared_controller:
            candidate_shared_source_file = Path(candidate_shared_source)
            if candidate_shared_source_file.exists():
                candidate_source_net_file = candidate_shared_source_file
        elif (
            candidate_status == "needs_expanded_rebuild_scope"
            and candidate_has_shared_controller
            and shared_controller_source_net_file is not None
            and shared_controller_source_net_file.exists()
        ):
            candidate_source_net_file = shared_controller_source_net_file
        collapse_junction_ids: set[str] = set()
        junction_map: dict[str, str] = {}
        if scoped_candidate:
            approach_pairs = [
                dict(item)
                for item in candidate.get("tls_approach_pairs", []) or []
                if isinstance(item, Mapping)
            ]
            if (
                candidate_source_net_file != source_net_file
                and candidate_status == "needs_expanded_rebuild_scope"
            ):
                shared_controller_plan = build_shared_teacher_tls_controller_replay_plan(
                    candidate_net_file=candidate_source_net_file,
                    teacher_net_file=teacher_net_file,
                    teacher_controller_id=teacher_junction_id,
                    candidate_controller_id=junction_id,
                    candidate_junction_ids=tls_candidate_junction_ids,
                    approach_pairs=approach_pairs,
                    collapse_junction_ids=tls_candidate_junction_ids,
                )
                if shared_controller_plan.get("status") == "pass":
                    shared_controller_candidate = True
                    edge_map = {
                        str(key): str(value)
                        for key, value in (shared_controller_plan.get("edge_map", {}) or {}).items()
                        if str(key) and str(value)
                    }
                    collapse_junction_ids = {
                        str(item)
                        for item in shared_controller_plan.get("candidate_junction_ids", []) or []
                        if str(item)
                    }
                    junction_map = {
                        str(key): str(value)
                        for key, value in (shared_controller_plan.get("junction_map", {}) or {}).items()
                        if str(key) and str(value)
                    }
                else:
                    variant_report = {
                        "candidate_index": index,
                        "junction_id": junction_id,
                        "teacher_junction_id": teacher_junction_id,
                        "shared_controller_replay_plan": shared_controller_plan,
                        "status": "blocked",
                        "reason": "shared_controller_replay_plan_not_pass",
                    }
                    variant_reports.append(variant_report)
                    continue
            else:
                scoped_plan = build_scoped_teacher_tls_cell_replay_plan(
                    candidate_net_file=candidate_source_net_file,
                    teacher_net_file=teacher_net_file,
                    teacher_junction_id=teacher_junction_id,
                    candidate_junction_id=junction_id,
                    candidate_junction_ids=tls_candidate_junction_ids,
                    approach_pairs=approach_pairs,
                )
                if scoped_plan.get("status") == "pass":
                    edge_map = {
                        str(key): str(value)
                        for key, value in (scoped_plan.get("edge_map", {}) or {}).items()
                        if str(key) and str(value)
                    }
                    collapse_junction_ids = {
                        str(item)
                        for item in scoped_plan.get("candidate_junction_ids", []) or []
                        if str(item)
                    }
                    junction_map = {
                        str(key): str(value)
                        for key, value in (scoped_plan.get("junction_map", {}) or {}).items()
                        if str(key) and str(value)
                    }
                elif candidate_status == "needs_expanded_rebuild_scope":
                    variant_report = {
                        "candidate_index": index,
                        "junction_id": junction_id,
                        "teacher_junction_id": teacher_junction_id,
                        "scoped_tls_cell_plan": scoped_plan,
                        "status": "blocked",
                        "reason": "scoped_tls_cell_plan_not_pass",
                    }
                    variant_reports.append(variant_report)
                    continue
            if candidate_status == "needs_expanded_rebuild_scope" and not shared_controller_candidate and scoped_plan is None:
                variant_report = {
                    "candidate_index": index,
                    "junction_id": junction_id,
                    "teacher_junction_id": teacher_junction_id,
                    "shared_controller_candidate_source_net_file": str(candidate_source_net_file),
                    "status": "blocked",
                    "reason": "shared_controller_candidate_source_missing",
                }
                variant_reports.append(variant_report)
                continue
        if not junction_id:
            variant_report = {
                "candidate_index": index,
                "junction_id": junction_id,
                "teacher_junction_id": teacher_junction_id,
                "status": "blocked",
                "reason": "junction_id_missing",
            }
            variant_reports.append(variant_report)
            continue
        variant_dir = output_dir / f"candidate_{index:03d}_{_safe_path_part(junction_id)}"
        variant_report: dict[str, Any] = {
            "candidate_index": index,
            "junction_id": junction_id,
            "teacher_junction_id": teacher_junction_id,
            "scoped_tls_cell": scoped_candidate,
            "candidate_source_net_file": str(candidate_source_net_file),
            "shared_controller_candidate": shared_controller_candidate,
        }
        if scoped_plan is not None:
            variant_report["scoped_tls_cell_plan"] = scoped_plan
        if shared_controller_plan is not None:
            variant_report["shared_controller_replay_plan"] = shared_controller_plan
        if not edge_map:
            variant_report.update({"status": "blocked", "reason": "edge_map_missing"})
            variant_reports.append(variant_report)
            continue

        replay_prefix = f"{prefix}_candidate_{index:03d}"
        replay_file = _stage_file(variant_dir, replay_prefix, "target_internal_replay.net.xml")
        if shared_controller_candidate and shared_controller_plan is not None:
            replay_report = write_shared_teacher_tls_controller_replay_net(
                candidate_net_file=candidate_source_net_file,
                teacher_net_file=teacher_net_file,
                output_file=replay_file,
                candidate_controller_id=junction_id,
                teacher_controller_id=teacher_junction_id,
                owner_map={
                    str(key): str(value)
                    for key, value in (shared_controller_plan.get("owner_map", {}) or {}).items()
                    if str(key) and str(value)
                },
                edge_map=edge_map,
                junction_map=junction_map,
                collapse_junction_ids=collapse_junction_ids,
            )
        elif scoped_candidate and scoped_plan is not None and scoped_plan.get("status") == "pass":
            replay_report = write_scoped_teacher_tls_cell_replay_net(
                candidate_net_file=candidate_source_net_file,
                teacher_net_file=teacher_net_file,
                output_file=replay_file,
                junction_id=junction_id,
                teacher_junction_id=teacher_junction_id,
                edge_map=edge_map,
                collapse_junction_ids=collapse_junction_ids,
                junction_map=junction_map,
            )
        else:
            replay_report = write_teacher_target_internal_replay_net(
                candidate_net_file=candidate_source_net_file,
                teacher_net_file=teacher_net_file,
                output_file=replay_file,
                junction_id=junction_id,
                teacher_junction_id=teacher_junction_id,
                edge_map=edge_map,
            )
        variant_report["target_internal_replay"] = replay_report
        if replay_report.get("status") != "pass":
            variant_report.update({"status": "blocked", "reason": "target_internal_replay_not_pass"})
            variant_reports.append(variant_report)
            continue
        if scoped_candidate:
            base_replay_report = replay_report.get("base_replay_report", {})
            ignored_off_scope_tls_connections = (
                int(base_replay_report.get("ignored_off_scope_tls_connection_count", 0) or 0)
                if isinstance(base_replay_report, Mapping)
                else 0
            )
            shared_controlled_connection_count = (
                int(replay_report.get("teacher_controlled_connection_count", 0) or 0)
                if shared_controller_candidate
                else 0
            )
            variant_report["scoped_tls_replay_coverage"] = {
                "status": "pass"
                if ignored_off_scope_tls_connections == 0
                and (not shared_controller_candidate or shared_controlled_connection_count > 0)
                else "blocked",
                "ignored_off_scope_tls_connection_count": ignored_off_scope_tls_connections,
                "shared_controller_teacher_controlled_connection_count": shared_controlled_connection_count,
                "ignored_off_scope_tls_connections": list(
                    base_replay_report.get("ignored_off_scope_tls_connections", []) or []
                )
                if isinstance(base_replay_report, Mapping)
                else [],
                "policy": "all teacher-controlled movements must remain in the scoped candidate before TLS restore",
            }
            if ignored_off_scope_tls_connections or (
                shared_controller_candidate and shared_controlled_connection_count == 0
            ):
                variant_report.update(
                    {
                        "status": "blocked",
                        "reason": "scoped_tls_teacher_connections_off_scope",
                    }
                )
                variant_reports.append(variant_report)
                continue

        teacher_model = extract_teacher_junction_model(teacher_net_file, teacher_junction_id)
        tllogic_file = _stage_file(variant_dir, replay_prefix, "teacher_tllogic.net.xml")
        tllogic_report = write_teacher_tllogic_net(
            candidate_net_file=replay_file,
            output_file=tllogic_file,
            junction_id=junction_id,
            teacher_model=teacher_model,
        )
        variant_report["tl_logic"] = tllogic_report
        if tllogic_report.get("status") != "pass":
            variant_report.update({"status": "blocked", "reason": "teacher_tllogic_not_pass"})
            variant_reports.append(variant_report)
            continue

        tllogic_sumo_load = _sumo_load_net(
            tllogic_file,
            output_dir=variant_dir / "sumo_load_tllogic",
            sumo_binary=sumo_binary,
            timeout_seconds=timeout_seconds,
            command_runner=command_runner,
        )
        variant_report["tllogic_sumo_load"] = tllogic_sumo_load

        normalized_file = _stage_file(variant_dir, replay_prefix, "normalized.net.xml")
        normalize_report = _normalize_sumo_net(
            net_file=tllogic_file,
            output_file=normalized_file,
            output_dir=variant_dir,
            netconvert_binary=netconvert_binary,
            timeout_seconds=timeout_seconds,
            command_runner=command_runner,
        )
        variant_report["normalize"] = normalize_report
        if normalize_report.get("status") == "pass" and normalized_file.exists():
            effective_replay_edge_map = {
                str(key): str(value)
                for key, value in (
                    replay_report.get("effective_edge_map", edge_map)
                    if isinstance(replay_report, Mapping)
                    else edge_map
                ).items()
                if str(key) and str(value)
            }
            restore_owner_ids = [junction_id]
            if shared_controller_candidate and shared_controller_plan is not None:
                restore_owner_ids = sorted(
                    {
                        str(value)
                        for value in (shared_controller_plan.get("owner_map", {}) or {}).values()
                        if str(value)
                    }
                )
            off_scope_restore_report = restore_off_scope_netconvert_artifacts(
                source_file=tllogic_file,
                target_file=normalized_file,
                mutable_junction_ids={
                    *restore_owner_ids,
                    *(
                        str(value)
                        for value in (
                            replay_report.get("collapse_junction_ids", ())
                            if isinstance(replay_report, Mapping)
                            else ()
                        )
                        if str(value)
                    ),
                },
                mutable_edge_ids=set(effective_replay_edge_map.values()),
            )
            normalize_report["off_scope_netconvert_restore"] = off_scope_restore_report
            if off_scope_restore_report.get("status") != "pass":
                variant_report.update(
                    {
                        "status": "blocked",
                        "reason": "off_scope_netconvert_restore_not_pass",
                    }
                )
                variant_reports.append(variant_report)
                continue
            if scoped_candidate:
                pedestrian_semantic_restore_reports: dict[str, Any] = {}
                pedestrian_restore_failed = False
                for restore_owner_id in restore_owner_ids:
                    pedestrian_semantic_restore_report = restore_scoped_pedestrian_internal_semantics_after_normalize(
                        source_net_file=tllogic_file,
                        target_net_file=normalized_file,
                        junction_id=restore_owner_id,
                        edge_map=effective_replay_edge_map,
                    )
                    pedestrian_semantic_restore_reports[restore_owner_id] = pedestrian_semantic_restore_report
                    if pedestrian_semantic_restore_report.get("status") != "pass":
                        pedestrian_restore_failed = True
                normalize_report["pedestrian_semantic_restore"] = (
                    pedestrian_semantic_restore_reports
                    if shared_controller_candidate
                    else pedestrian_semantic_restore_reports.get(junction_id, {})
                )
                if pedestrian_restore_failed:
                    variant_report.update(
                        {
                            "status": "blocked",
                            "reason": "pedestrian_semantic_restore_not_pass",
                        }
                    )
                    variant_reports.append(variant_report)
                    continue
                tls_semantic_restore_report = restore_teacher_tls_connection_semantics_after_normalize(
                    source_net_file=tllogic_file,
                    target_net_file=normalized_file,
                    junction_id=junction_id,
                )
                normalize_report["teacher_tls_semantic_restore"] = tls_semantic_restore_report
                if tls_semantic_restore_report.get("status") != "pass":
                    variant_report.update(
                        {
                            "status": "blocked",
                            "reason": "teacher_tls_semantic_restore_not_pass",
                        }
                    )
                    variant_reports.append(variant_report)
                    continue
            normalize_report["false_traffic_light_type_restore"] = _restore_false_traffic_light_junction_types(
                source_file=tllogic_file,
                target_file=normalized_file,
                exclude_junction_ids=(
                    {
                        str(value)
                        for value in (shared_controller_plan.get("owner_map", {}) or {}).values()
                        if str(value)
                    }
                    if shared_controller_candidate and shared_controller_plan is not None
                    else {junction_id}
                ),
            )
            if shared_controller_candidate and shared_controller_plan is not None:
                geometry_restore_reports: dict[str, Any] = {}
                for restore_owner_id in sorted(
                    {
                        str(value)
                        for value in (shared_controller_plan.get("owner_map", {}) or {}).values()
                        if str(value)
                    }
                ):
                    geometry_restore_reports[restore_owner_id] = _restore_replayed_geometry_attrs(
                        source_file=tllogic_file,
                        target_file=normalized_file,
                        junction_id=restore_owner_id,
                    )
                normalize_report["geometry_restore"] = geometry_restore_reports
                shared_tls_restore_after_geometry = restore_teacher_tls_connection_semantics_after_normalize(
                    source_net_file=tllogic_file,
                    target_net_file=normalized_file,
                    junction_id=junction_id,
                )
                normalize_report["shared_controller_tls_semantic_restore_after_geometry"] = (
                    shared_tls_restore_after_geometry
                )
                if shared_tls_restore_after_geometry.get("status") != "pass":
                    variant_report.update(
                        {
                            "status": "blocked",
                            "reason": "shared_controller_tls_semantic_restore_not_pass",
                        }
                    )
                    variant_reports.append(variant_report)
                    continue
            else:
                normalize_report["geometry_restore"] = _restore_replayed_geometry_attrs(
                    source_file=tllogic_file,
                    target_file=normalized_file,
                    junction_id=junction_id,
                )
            normalized_sumo_load = _sumo_load_net(
                normalized_file,
                output_dir=variant_dir / "sumo_load_normalized",
                sumo_binary=sumo_binary,
                timeout_seconds=timeout_seconds,
                command_runner=command_runner,
            )
            variant_report["sumo_load"] = normalized_sumo_load
            if normalized_sumo_load.get("status") == "pass":
                routeability_smoke = _run_tls_movement_routeability_smoke(
                    net_file=normalized_file,
                    controller_id=junction_id,
                    output_dir=variant_dir / "routeability_smoke",
                    prefix=replay_prefix,
                    sumo_binary=sumo_binary,
                    timeout_seconds=timeout_seconds,
                    command_runner=command_runner,
                )
                variant_report["routeability_smoke"] = routeability_smoke
                if routeability_smoke.get("status") != "pass":
                    variant_report.update(
                        {
                            "status": "blocked",
                            "reason": "routeability_smoke_not_pass",
                        }
                    )
                    variant_reports.append(variant_report)
                    continue
                if scoped_candidate:
                    if shared_controller_candidate and shared_controller_plan is not None:
                        tls_via_path_semantics = compare_shared_tls_via_path_semantics(
                            teacher_net_file,
                            normalized_file,
                            teacher_junction_id,
                            junction_id,
                            owner_map={
                                str(key): str(value)
                                for key, value in (shared_controller_plan.get("owner_map", {}) or {}).items()
                                if str(key) and str(value)
                            },
                            teacher_edge_map=effective_replay_edge_map,
                        )
                    else:
                        tls_via_path_semantics = compare_tls_via_path_semantics(
                            teacher_net_file,
                            normalized_file,
                            teacher_junction_id,
                            junction_id,
                            teacher_edge_map=effective_replay_edge_map,
                            teacher_internal_scope_id=teacher_junction_id,
                            candidate_internal_scope_id=junction_id,
                        )
                    variant_report["tls_via_path_semantics"] = tls_via_path_semantics
                    if tls_via_path_semantics.get("status") != "pass":
                        variant_report.update(
                            {
                                "status": "blocked",
                                "reason": "tls_via_path_semantics_not_pass",
                            }
                        )
                        variant_reports.append(variant_report)
                        continue
                variant_report.update(
                    {
                        "status": "pass",
                        "final_net_file": str(normalized_file),
                        "variant_file": str(normalized_file),
                    }
                )
                variant_reports.append(variant_report)
                return {
                    "status": "pass",
                    "claim_status": "diagnostic-demo",
                    "variant_file": str(normalized_file),
                    "final_net_file": str(normalized_file),
                    "candidate_index": index,
                    "junction_id": junction_id,
                    "teacher_junction_id": teacher_junction_id,
                    "sumo_load": normalized_sumo_load,
                    "variant_reports": variant_reports,
                }

        if scoped_candidate and normalize_report.get("status") != "pass":
            variant_report.update(
                {
                    "status": "blocked",
                    "reason": "scoped_tls_normalize_not_pass",
                }
            )
            variant_reports.append(variant_report)
            continue
        if tllogic_sumo_load.get("status") == "pass":
            variant_report.update(
                {
                    "status": "pass",
                    "final_net_file": str(tllogic_file),
                    "variant_file": str(tllogic_file),
                }
            )
            variant_reports.append(variant_report)
            return {
                "status": "pass",
                "claim_status": "diagnostic-demo",
                "variant_file": str(tllogic_file),
                "final_net_file": str(tllogic_file),
                "candidate_index": index,
                "junction_id": junction_id,
                "teacher_junction_id": teacher_junction_id,
                "sumo_load": tllogic_sumo_load,
                "variant_reports": variant_reports,
            }
        variant_report.update({"status": "blocked", "reason": "sumo_load_not_pass"})
        variant_reports.append(variant_report)
        continue
    return {
        "status": "blocked",
        "reason": "no_direct_local_replay_candidate_passed",
        "variant_reports": variant_reports,
    }


def run_scoped_teacher_tls_cell_batch(
    *,
    queue_report: Mapping[str, Any] | None,
    source_net_file: Path,
    output_dir: Path,
    prefix: str,
    netconvert_binary: str,
    sumo_binary: str,
    timeout_seconds: float,
    command_runner: Callable[..., Any] = run_command,
    shared_controller_source_net_file: Path | None = None,
) -> dict[str, Any]:
    """Run every enriched split-TLS candidate as an independent gated variant.

    Each cell is evaluated against the same untouched source network.  The
    variants are deliberately not merged here: a cell can pass local
    construction/TLS gates while the global reference topology still needs a
    separate promotion decision.
    """

    if not queue_report:
        return {"status": "skipped", "reason": "queue_missing", "cell_reports": []}
    candidates = []
    seen: set[tuple[str, str, tuple[str, ...]]] = set()
    for candidate in queue_report.get("repair_candidates", []) or []:
        if not isinstance(candidate, Mapping):
            continue
        candidate_tl_id = str(candidate.get("tls_candidate_tl_id", "")).strip()
        member_ids = tuple(sorted(str(item) for item in candidate.get("tls_candidate_junction_ids", []) or [] if str(item)))
        reference_tl_id = str(candidate.get("tls_reference_tl_id", "")).strip()
        if not candidate_tl_id or len(member_ids) <= 1 or not reference_tl_id:
            continue
        key = (reference_tl_id, candidate_tl_id, member_ids)
        if key in seen:
            continue
        seen.add(key)
        candidates.append(candidate)
    if not candidates:
        return {"status": "skipped", "reason": "no_scoped_tls_candidates", "cell_reports": []}
    if not source_net_file.exists():
        return {"status": "blocked", "reason": "source_net_file_missing", "cell_reports": []}

    output_dir.mkdir(parents=True, exist_ok=True)
    cell_reports: list[dict[str, Any]] = []
    for index, candidate in enumerate(candidates, start=1):
        candidate_tl_id = str(candidate.get("tls_candidate_tl_id", "")).strip()
        cell_dir = output_dir / f"cell_{index:03d}_{_safe_path_part(candidate_tl_id)}"
        trial_queue = dict(queue_report)
        trial_queue["repair_candidates"] = [dict(candidate)]
        trial_queue["repair_candidate_count"] = 1
        trial_queue["ready_candidate_count"] = 0
        trial_queue["expanded_scope_candidate_count"] = 1
        direct_report = _run_direct_local_teacher_replay(
            queue_report=trial_queue,
            source_net_file=source_net_file,
            output_dir=cell_dir,
            prefix=f"{prefix}_cell_{index:03d}",
            netconvert_binary=netconvert_binary,
            sumo_binary=sumo_binary,
            timeout_seconds=timeout_seconds,
            command_runner=command_runner,
            shared_controller_source_net_file=shared_controller_source_net_file,
        )
        cell_report = {
            "cell_index": index,
            "reference_tl_id": str(candidate.get("tls_reference_tl_id", "")),
            "candidate_tl_id": candidate_tl_id,
            "candidate_junction_ids": list(candidate.get("tls_candidate_junction_ids", []) or []),
            "status": str(direct_report.get("status", "fail")),
            "direct_replay": direct_report,
        }
        cell_reports.append(cell_report)

    report = {
        "status": "pass" if all(item["status"] == "pass" for item in cell_reports) else "fail",
        "claim_status": "diagnostic-demo",
        "source_net_file": str(source_net_file),
        "cell_count": len(cell_reports),
        "pass_count": sum(item["status"] == "pass" for item in cell_reports),
        "blocked_count": sum(item["status"] == "blocked" for item in cell_reports),
        "fail_count": sum(item["status"] == "fail" for item in cell_reports),
        "cell_reports": cell_reports,
        "policy": "independent scoped variants; no main-network merge without global promotion gates",
    }
    report_file = output_dir / f"{prefix}_scoped_tls_cell_batch.json"
    report["report_file"] = str(report_file)
    manifest_file = output_dir / f"{prefix}_scoped_tls_cell_batch_manifest.json"
    artifact_paths = _scoped_tls_batch_artifact_paths(
        report=report,
        source_net_file=source_net_file,
        output_dir=output_dir,
    )
    artifact_hashes, artifact_hash_gate = _hash_scoped_tls_batch_artifacts(
        artifact_paths,
        base_dir=output_dir,
    )
    report["artifact_manifest_file"] = str(manifest_file)
    report["artifact_hashes"] = artifact_hashes
    report["artifact_hash_gate"] = artifact_hash_gate
    report["artifact_manifest_status"] = (
        "pass" if report["status"] == "pass" and artifact_hash_gate["status"] == "pass" else "fail"
    )
    report_file.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    report_hashes, report_hash_gate = _hash_scoped_tls_batch_artifacts(
        {"batch_report": report_file},
        base_dir=output_dir,
    )
    manifest_hashes = {**artifact_hashes, **report_hashes}
    manifest_gate = {
        "status": (
            "pass"
            if artifact_hash_gate["status"] == "pass"
            and report_hash_gate["status"] == "pass"
            and report["status"] == "pass"
            else "fail"
        ),
        "algorithm": "sha256",
        "hashed_artifact_count": len([item for item in manifest_hashes.values() if item.get("status") == "pass"]),
        "missing_artifacts": sorted(
            [key for key, item in manifest_hashes.items() if item.get("status") == "missing"]
        ),
        "unreadable_artifacts": sorted(
            [key for key, item in manifest_hashes.items() if item.get("status") == "unreadable"]
        ),
        "batch_status": report["status"],
    }
    manifest = {
        "schema_version": 1,
        "artifact_manifest_kind": "scoped_tls_cell_batch",
        "status": manifest_gate["status"],
        "claim_status": "diagnostic-demo",
        "source_net_file": str(source_net_file),
        "report_file": str(report_file),
        "cell_count": report["cell_count"],
        "pass_count": report["pass_count"],
        "blocked_count": report["blocked_count"],
        "fail_count": report["fail_count"],
        "netconvert_binary": netconvert_binary,
        "sumo_binary": sumo_binary,
        "policy": "hash every source/report/variant artifact; no main-network merge is implied",
        "artifact_hashes": manifest_hashes,
        "artifact_hash_gate": manifest_gate,
        "rebuild_inputs": {
            "prefix": prefix,
            "timeout_seconds": timeout_seconds,
            "source_net_sha256": manifest_hashes.get("source_net_file", {}).get("sha256", ""),
        },
    }
    manifest_file.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    return report


def run_final_direct_replay_candidates(
    queue_report: Mapping[str, Any],
    *,
    source_net_file: Path,
    baseline_delta_report: Mapping[str, Any],
    iteration_label: str,
    output_dir: Path,
    prefix: str,
    netconvert_binary: str,
    sumo_binary: str,
    timeout_seconds: float,
    command_runner: Callable[..., Any],
    teacher_guided_direct_replay_func: Callable[..., Any],
    reference_join_audit_func: Callable[..., Any],
    reference_join_audit_structural_only: bool,
    reference_net_file: Path | None,
    topology_cluster_radius_m: float,
    topology_min_cluster_nodes: int,
    teacher_guided_seed_report: Mapping[str, Any] | None,
    teacher_guided_repair_requires_reference_promotion: bool,
) -> tuple[Path | None, dict[str, Any] | None, dict[str, Any], dict[str, Any] | None]:
    final_direct_candidates = [
        candidate
        for candidate in queue_report.get("repair_candidates", []) or []
        if isinstance(candidate, Mapping)
        and candidate.get("candidate_status") == "ready_for_teacher_guided_variant"
    ]
    selected_variant_file: Path | None = None
    selected_delta_report: dict[str, Any] | None = None
    selected_replay_report: dict[str, Any] | None = None
    selected_promotion_report: dict[str, Any] = {
        "status": "skipped",
        "reason": "no_replay_candidates",
    }
    selected_rank: tuple[int, int, int, int, int] | None = None
    for direct_index, candidate in enumerate(final_direct_candidates, start=1):
        trial_queue_report = dict(queue_report)
        trial_queue_report["repair_candidates"] = [dict(candidate)]
        trial_queue_report["repair_candidate_count"] = 1
        trial_queue_report["ready_candidate_count"] = 1
        trial_queue_report["expanded_scope_candidate_count"] = 0
        replay_output_dir = output_dir / "final_movement_direct_replay" / f"attempt_{direct_index:03d}"
        delta_output_dir = (
            output_dir / "final_movement_direct_replay_reference_delta" / f"attempt_{direct_index:03d}"
        )
        delta_prefix = f"{prefix}_final_movement_direct_replay_reference_delta_{direct_index:03d}"
        if iteration_label != "iteration_001":
            replay_output_dir = (
                output_dir
                / "final_movement_direct_replay"
                / iteration_label
                / f"attempt_{direct_index:03d}"
            )
            delta_output_dir = (
                output_dir
                / "final_movement_direct_replay_reference_delta"
                / iteration_label
                / f"attempt_{direct_index:03d}"
            )
            delta_prefix = (
                f"{prefix}_final_movement_direct_replay_{iteration_label}_"
                f"reference_delta_{direct_index:03d}"
            )
        replay_report = teacher_guided_direct_replay_func(
            queue_report=trial_queue_report,
            source_net_file=source_net_file,
            output_dir=replay_output_dir,
            prefix=f"{prefix}_final_movement_rebuild_direct_replay",
            netconvert_binary=netconvert_binary,
            sumo_binary=sumo_binary,
            timeout_seconds=timeout_seconds,
            command_runner=command_runner,
        )
        last_replay_report = replay_report
        direct_variant_value = str(replay_report.get("variant_file", ""))
        direct_variant_file = Path(direct_variant_value) if direct_variant_value else None
        if direct_variant_file is None or not direct_variant_file.exists():
            if selected_replay_report is None:
                selected_replay_report = last_replay_report
            continue
        delta_report = reference_join_audit_func(
            reference_net_file=reference_net_file,
            candidate_net_file=direct_variant_file,
            output_dir=delta_output_dir,
            prefix=delta_prefix,
            candidate_cluster_radius_m=topology_cluster_radius_m,
            candidate_min_cluster_nodes=topology_min_cluster_nodes,
            structural_only=_followup_reference_delta_structural_only(
                baseline_delta_report,
                default=reference_join_audit_structural_only,
            ),
        )
        promotion_report = _movement_rebuild_reference_delta_promotion_decision(
            candidate_delta_report=delta_report,
            baseline_delta_report=baseline_delta_report,
            structural_guard_delta_report=(
                teacher_guided_seed_report if teacher_guided_repair_requires_reference_promotion else None
            ),
            reason="final_direct_local_teacher_replay_promoted_by_reference_delta",
        )
        if promotion_report.get("status") != "pass":
            if selected_rank is None:
                selected_delta_report = delta_report
                selected_promotion_report = promotion_report
                selected_replay_report = last_replay_report
            continue
        candidate_rank = (
            _movement_rebuild_mismatch_score(delta_report),
            _int_field(delta_report, "junction_pattern_mismatch_count"),
            _total_structural_delta_score(delta_report),
            _tls_semantic_delta_score(delta_report),
            direct_index,
        )
        if selected_rank is None or candidate_rank < selected_rank:
            selected_rank = candidate_rank
            selected_variant_file = direct_variant_file
            selected_delta_report = delta_report
            selected_promotion_report = promotion_report
            selected_replay_report = last_replay_report
    return (
        selected_variant_file,
        selected_delta_report,
        selected_promotion_report,
        selected_replay_report,
    )


_WORKFLOW_UNSET = object()


def _workflow_tls_aggregation_section(
    *,
    command_runner: Callable[..., Any],
    map_target_date: str | None,
    map_temporal_scope: str,
    network_plan: Any,
    osm_file: Any,
    output_dir: Path,
    prefix: str,
    reference_join_audit_func: Callable[..., dict[str, Any]],
    reference_net_file: Path | None,
    reference_visual_detail_net_file: Path | None,
    reference_visual_detail_tls_aggregation_candidates: list[dict[str, Any]],
    reference_visual_detail_tls_low_vehicle_control_candidates: list[dict[str, Any]],
    run_tls_aggregation_after_build: bool,
    sumo_binary: str,
    timeout_seconds: float,
    tls_aggregation_func: Callable[..., dict[str, Any]],
    tls_audit_func: Callable[..., dict[str, Any]],
    tls_connection_repair_func: Callable[..., dict[str, Any]],
    tls_low_vehicle_control_func: Callable[..., dict[str, Any]],
    tls_signal_grouping_func: Callable[..., dict[str, Any]],
    topology_cluster_radius_m: float,
    topology_min_cluster_nodes: int,
    reference_visual_detail_comparison_net_file: Path | None,
    reference_visual_detail_raw_reference_delta_report: dict[str, Any] | None,
    reference_visual_detail_tls_aggregation_reference_delta_report: dict[str, Any] | None,
    reference_visual_detail_tls_aggregation_reference_promotion_report: dict[str, Any],
    reference_visual_detail_tls_aggregation_report: dict[str, Any] | None,
    reference_visual_detail_tls_connection_repair_promotion_report: dict[str, Any],
    reference_visual_detail_tls_connection_repair_reference_delta_report: dict[str, Any] | None,
    reference_visual_detail_tls_connection_repair_report: dict[str, Any] | None,
    reference_visual_detail_tls_connection_repair_sumo_load_report: dict[str, Any] | None,
    reference_visual_detail_tls_low_vehicle_control_reference_promotion_report: dict[str, Any],
    reference_visual_detail_tls_report: dict[str, Any] | None,
    reference_visual_detail_tls_signal_grouping_reference_delta_report: dict[str, Any] | None,
    reference_visual_detail_tls_signal_grouping_reference_promotion_report: dict[str, Any],
    reference_visual_detail_tls_signal_grouping_report: dict[str, Any] | None,
    reference_visual_detail_tls_signal_grouping_sumo_load_report: dict[str, Any] | None,
    tls_aggregation_report: dict[str, Any] | None,
) -> dict[str, Any]:
    low_vehicle_candidate_record = _WORKFLOW_UNSET
    low_vehicle_delta_report = _WORKFLOW_UNSET
    low_vehicle_label = _WORKFLOW_UNSET
    low_vehicle_limit = _WORKFLOW_UNSET
    low_vehicle_output_dir = _WORKFLOW_UNSET
    low_vehicle_promotion_report = _WORKFLOW_UNSET
    low_vehicle_report = _WORKFLOW_UNSET
    low_vehicle_score = _WORKFLOW_UNSET
    low_vehicle_sumo_load_report = _WORKFLOW_UNSET
    low_vehicle_variant_file = _WORKFLOW_UNSET
    low_vehicle_variant_value = _WORKFLOW_UNSET
    missing_shared_groups = _WORKFLOW_UNSET
    reference_visual_detail_comparison_selection_reason = _WORKFLOW_UNSET
    reference_visual_detail_tls_low_vehicle_control_reference_delta_report = _WORKFLOW_UNSET
    reference_visual_detail_tls_low_vehicle_control_report = _WORKFLOW_UNSET
    reference_visual_detail_tls_low_vehicle_control_sumo_load_report = _WORKFLOW_UNSET
    repair_variant_file = _WORKFLOW_UNSET
    repair_variant_value = _WORKFLOW_UNSET
    selected_low_vehicle_candidate = _WORKFLOW_UNSET
    signal_grouping_variant_file = _WORKFLOW_UNSET
    signal_grouping_variant_value = _WORKFLOW_UNSET
    if reference_visual_detail_net_file is not None and run_tls_aggregation_after_build:
        reference_visual_detail_tls_report = tls_audit_func(
            net_file=reference_visual_detail_net_file,
            output_dir=output_dir / "reference_visual_detail_tls_audit",
            prefix=f"{prefix}_reference_visual_detail_tls_audit",
            osm_file=osm_file,
            google_maps_temporal_scope=map_temporal_scope,
            google_maps_target_date=map_target_date,
        )
        if _should_run_tls_aggregation(reference_visual_detail_tls_report, tls_aggregation_func):
            reference_matched_tls_delta = (
                reference_net_file is not None and str(network_plan.get("network_profile", "")) == "reference_matched"
            )
            selected_tls_candidate: tuple[
                dict[str, Any],
                dict[str, Any] | None,
                dict[str, Any],
                Path,
            ] | None = None
            best_scored_tls_candidate: tuple[
                int,
                dict[str, Any],
                dict[str, Any],
                dict[str, Any],
                Path,
            ] | None = None
            first_tls_aggregation_report: dict[str, Any] | None = None
            rejected_controlled_connection_variant_file: Path | None = None
            tls_guess_signal_distances = _reference_visual_tls_guess_signal_distances(
                reference_net_file=reference_net_file,
                network_profile=str(network_plan.get("network_profile", "")),
            )
            for index, tls_guess_signals_dist_m in enumerate(tls_guess_signal_distances):
                candidate_label = _tls_guess_signal_distance_label(tls_guess_signals_dist_m)
                primary_candidate = index == 0
                aggregation_output_dir = (
                    output_dir / "reference_visual_detail_tls_aggregation"
                    if primary_candidate
                    else output_dir / f"reference_visual_detail_tls_aggregation_{candidate_label}"
                )
                aggregation_prefix = (
                    f"{prefix}_reference_visual_detail_tls_aggregation"
                    if primary_candidate
                    else f"{prefix}_reference_visual_detail_tls_aggregation_{candidate_label}"
                )
                tls_aggregation_report = tls_aggregation_func(
                    net_file=reference_visual_detail_net_file,
                    tls_audit_report=reference_visual_detail_tls_report,
                    output_dir=aggregation_output_dir,
                    prefix=aggregation_prefix,
                    timeout_seconds=timeout_seconds,
                    tls_guess_signals_dist_m=tls_guess_signals_dist_m,
                )
                if first_tls_aggregation_report is None:
                    first_tls_aggregation_report = tls_aggregation_report
                visual_tls_variant_value = tls_aggregation_report.get("tls_aggregation_variant_file", "")
                candidate_record: dict[str, Any] = {
                    "tls_guess_signals_dist_m": tls_guess_signals_dist_m,
                    "candidate_label": candidate_label,
                    "status": tls_aggregation_report.get("status", "fail"),
                    "tls_aggregation_status": tls_aggregation_report.get("tls_aggregation_status", "fail"),
                    "tls_aggregation_variant_file": visual_tls_variant_value,
                }
                netconvert_report = tls_aggregation_report.get("tls_aggregation_netconvert", {})
                if isinstance(netconvert_report, Mapping):
                    candidate_record["netconvert_returncode"] = netconvert_report.get("returncode")
                candidate_visual_tls_net_file = Path(str(visual_tls_variant_value)) if visual_tls_variant_value else None
                if (
                    tls_aggregation_report.get("status") == "pass"
                    and candidate_visual_tls_net_file is not None
                    and candidate_visual_tls_net_file.exists()
                ):
                    if reference_matched_tls_delta:
                        if reference_visual_detail_raw_reference_delta_report is None:
                            reference_visual_detail_raw_reference_delta_report = reference_join_audit_func(
                                reference_net_file=reference_net_file,
                                candidate_net_file=reference_visual_detail_net_file,
                                output_dir=output_dir / "reference_visual_detail_raw_reference_delta",
                                prefix=f"{prefix}_reference_visual_detail_raw_reference_delta",
                                candidate_cluster_radius_m=topology_cluster_radius_m,
                                candidate_min_cluster_nodes=topology_min_cluster_nodes,
                                structural_only=True,
                            )
                        delta_output_dir = (
                            output_dir / "reference_visual_detail_tls_aggregation_reference_delta"
                            if primary_candidate
                            else output_dir / f"reference_visual_detail_tls_aggregation_reference_delta_{candidate_label}"
                        )
                        delta_prefix = (
                            f"{prefix}_reference_visual_detail_tls_aggregation_reference_delta"
                            if primary_candidate
                            else f"{prefix}_reference_visual_detail_tls_aggregation_reference_delta_{candidate_label}"
                        )
                        tls_aggregation_delta_report = reference_join_audit_func(
                            reference_net_file=reference_net_file,
                            candidate_net_file=candidate_visual_tls_net_file,
                            output_dir=delta_output_dir,
                            prefix=delta_prefix,
                            candidate_cluster_radius_m=topology_cluster_radius_m,
                            candidate_min_cluster_nodes=topology_min_cluster_nodes,
                            structural_only=True,
                        )
                        tls_aggregation_promotion_report = _reference_delta_promotion_decision(
                            candidate_delta_report=tls_aggregation_delta_report,
                            baseline_delta_report=reference_visual_detail_raw_reference_delta_report,
                            reason="tls_aggregation_promoted_by_reference_delta",
                        )
                        candidate_score = _tls_semantic_delta_score(tls_aggregation_delta_report)
                        candidate_record.update(
                            {
                                "reference_tls_semantic_delta_score": candidate_score,
                                "reference_delta_file": tls_aggregation_delta_report.get("summary_file", ""),
                                "reference_promotion_status": tls_aggregation_promotion_report.get("status", ""),
                            }
                        )
                        if best_scored_tls_candidate is None or candidate_score < best_scored_tls_candidate[0]:
                            best_scored_tls_candidate = (
                                candidate_score,
                                tls_aggregation_report,
                                tls_aggregation_delta_report,
                                tls_aggregation_promotion_report,
                                candidate_visual_tls_net_file,
                            )
                        if tls_aggregation_promotion_report.get("status") == "pass" and (
                            selected_tls_candidate is None
                            or candidate_score
                            < _tls_semantic_delta_score(selected_tls_candidate[1])
                        ):
                            selected_tls_candidate = (
                                tls_aggregation_report,
                                tls_aggregation_delta_report,
                                tls_aggregation_promotion_report,
                                candidate_visual_tls_net_file,
                            )
                    elif _tls_aggregation_preserves_controlled_connections(tls_aggregation_report):
                        selected_tls_candidate = (
                            tls_aggregation_report,
                            None,
                            {
                                "status": "pass",
                                "reason": "tls_aggregation_preserved_controlled_connections",
                            },
                            candidate_visual_tls_net_file,
                        )
                        candidate_record["reference_promotion_status"] = "not_applicable"
                        reference_visual_detail_tls_aggregation_candidates.append(candidate_record)
                        break
                    else:
                        rejected_controlled_connection_variant_file = candidate_visual_tls_net_file
                reference_visual_detail_tls_aggregation_candidates.append(candidate_record)

            if selected_tls_candidate is not None:
                (
                    reference_visual_detail_tls_aggregation_report,
                    reference_visual_detail_tls_aggregation_reference_delta_report,
                    reference_visual_detail_tls_aggregation_reference_promotion_report,
                    candidate_visual_tls_net_file,
                ) = selected_tls_candidate
                reference_visual_detail_comparison_net_file = candidate_visual_tls_net_file
                reference_visual_detail_comparison_selection_reason = str(
                    reference_visual_detail_tls_aggregation_reference_promotion_report.get("reason", "")
                )
            elif best_scored_tls_candidate is not None:
                (
                    _best_tls_score,
                    reference_visual_detail_tls_aggregation_report,
                    reference_visual_detail_tls_aggregation_reference_delta_report,
                    reference_visual_detail_tls_aggregation_reference_promotion_report,
                    candidate_visual_tls_net_file,
                ) = best_scored_tls_candidate
                reference_visual_detail_comparison_net_file = candidate_visual_tls_net_file
                reference_visual_detail_comparison_selection_reason = (
                    "tls_aggregation_rejected_controlled_connection_regression"
                )
            else:
                reference_visual_detail_tls_aggregation_report = first_tls_aggregation_report
                candidate_visual_tls_net_file = rejected_controlled_connection_variant_file
                if rejected_controlled_connection_variant_file is not None:
                    reference_visual_detail_comparison_selection_reason = (
                        "tls_aggregation_rejected_controlled_connection_regression"
                    )

            if (
                selected_tls_candidate is not None
                and reference_visual_detail_tls_aggregation_reference_delta_report is not None
            ):
                missing_counts = reference_visual_detail_tls_aggregation_reference_delta_report.get(
                    "network_structural_missing_counts", {}
                )
                missing_shared_groups = int(missing_counts.get("tls_shared_linkindex_group_count", 0) or 0)
                if missing_shared_groups > 0:
                    reference_visual_detail_tls_signal_grouping_report = tls_signal_grouping_func(
                        source_net_file=candidate_visual_tls_net_file,
                        output_dir=output_dir / "reference_visual_detail_tls_signal_grouping",
                        prefix=f"{prefix}_reference_visual_detail_tls_signal_grouping",
                        max_shared_linkindex_groups=missing_shared_groups,
                    )
                    signal_grouping_variant_value = reference_visual_detail_tls_signal_grouping_report.get(
                        "tls_signal_grouping_variant_file", ""
                    )
                    signal_grouping_variant_file = (
                        Path(str(signal_grouping_variant_value)) if signal_grouping_variant_value else None
                    )
                    if signal_grouping_variant_file is not None and signal_grouping_variant_file.exists():
                        reference_visual_detail_tls_signal_grouping_sumo_load_report = _sumo_load_net(
                            signal_grouping_variant_file,
                            output_dir=output_dir / "reference_visual_detail_tls_signal_grouping",
                            sumo_binary=sumo_binary,
                            timeout_seconds=timeout_seconds,
                            command_runner=command_runner,
                        )
                        if reference_visual_detail_tls_signal_grouping_sumo_load_report.get("status") == "pass":
                            reference_visual_detail_tls_signal_grouping_reference_delta_report = (
                                reference_join_audit_func(
                                    reference_net_file=reference_net_file,
                                    candidate_net_file=signal_grouping_variant_file,
                                    output_dir=output_dir
                                    / "reference_visual_detail_tls_signal_grouping_reference_delta",
                                    prefix=f"{prefix}_reference_visual_detail_tls_signal_grouping_reference_delta",
                                    candidate_cluster_radius_m=topology_cluster_radius_m,
                                    candidate_min_cluster_nodes=topology_min_cluster_nodes,
                                    structural_only=True,
                                )
                            )
                            reference_visual_detail_tls_signal_grouping_reference_promotion_report = (
                                _reference_delta_promotion_decision(
                                    candidate_delta_report=reference_visual_detail_tls_signal_grouping_reference_delta_report,
                                    baseline_delta_report=reference_visual_detail_tls_aggregation_reference_delta_report,
                                    reason="tls_signal_grouping_promoted_by_reference_delta",
                                )
                            )
                        else:
                            reference_visual_detail_tls_signal_grouping_reference_promotion_report = {
                                "status": "blocked",
                                "reason": "sumo_load_not_pass",
                            }
                        if (
                            reference_visual_detail_tls_signal_grouping_reference_promotion_report.get("status")
                            == "pass"
                        ):
                            reference_visual_detail_comparison_net_file = signal_grouping_variant_file
                            reference_visual_detail_comparison_selection_reason = str(
                                reference_visual_detail_tls_signal_grouping_reference_promotion_report.get(
                                    "reason", ""
                                )
                            )
            low_vehicle_baseline_delta_report = (
                reference_visual_detail_tls_signal_grouping_reference_delta_report
                if reference_visual_detail_tls_signal_grouping_reference_promotion_report.get("status") == "pass"
                else reference_visual_detail_tls_aggregation_reference_delta_report
            )
            low_vehicle_source_net_file = reference_visual_detail_comparison_net_file
            if (
                low_vehicle_source_net_file is not None
                and low_vehicle_baseline_delta_report is not None
                and reference_matched_tls_delta
            ):
                low_vehicle_queue = low_vehicle_baseline_delta_report.get("tls_control_review_queue", [])
                selected_low_vehicle_candidate: tuple[
                    int,
                    dict[str, Any],
                    dict[str, Any],
                    dict[str, Any],
                    dict[str, Any],
                    Path,
                ] | None = None
                for low_vehicle_limit in _low_vehicle_control_candidate_limits(low_vehicle_baseline_delta_report):
                    low_vehicle_label = str(low_vehicle_limit["label"])
                    low_vehicle_output_dir = output_dir / f"reference_visual_detail_tls_low_vehicle_control_{low_vehicle_label}"
                    low_vehicle_report = tls_low_vehicle_control_func(
                        source_net_file=low_vehicle_source_net_file,
                        tls_control_review_queue=low_vehicle_queue,
                        output_dir=low_vehicle_output_dir,
                        prefix=f"{prefix}_reference_visual_detail_tls_low_vehicle_control_{low_vehicle_label}",
                        max_removed_controlled_connections=low_vehicle_limit["max_removed_controlled_connections"],
                        max_selected_tllogic_count=low_vehicle_limit["max_selected_tllogic_count"],
                    )
                    low_vehicle_candidate_record = {
                        "candidate_label": low_vehicle_label,
                        "status": low_vehicle_report.get("status", "fail"),
                        "max_removed_controlled_connections": low_vehicle_limit[
                            "max_removed_controlled_connections"
                        ],
                        "max_selected_tllogic_count": low_vehicle_limit["max_selected_tllogic_count"],
                        "selected_tllogic_count": low_vehicle_report.get(
                            "tls_low_vehicle_control_selected_tllogic_count", 0
                        ),
                        "removed_connection_count": low_vehicle_report.get(
                            "tls_low_vehicle_control_removed_connection_count", 0
                        ),
                    }
                    low_vehicle_variant_value = low_vehicle_report.get("tls_low_vehicle_control_variant_file", "")
                    low_vehicle_variant_file = Path(str(low_vehicle_variant_value)) if low_vehicle_variant_value else None
                    if low_vehicle_variant_file is not None and low_vehicle_variant_file.exists():
                        low_vehicle_sumo_load_report = _sumo_load_net(
                            low_vehicle_variant_file,
                            output_dir=low_vehicle_output_dir,
                            sumo_binary=sumo_binary,
                            timeout_seconds=timeout_seconds,
                            command_runner=command_runner,
                        )
                        low_vehicle_candidate_record["sumo_load_status"] = low_vehicle_sumo_load_report.get(
                            "status", "fail"
                        )
                        if low_vehicle_sumo_load_report.get("status") == "pass":
                            low_vehicle_delta_report = reference_join_audit_func(
                                reference_net_file=reference_net_file,
                                candidate_net_file=low_vehicle_variant_file,
                                output_dir=output_dir
                                / f"reference_visual_detail_tls_low_vehicle_control_reference_delta_{low_vehicle_label}",
                                prefix=f"{prefix}_reference_visual_detail_tls_low_vehicle_control_reference_delta_{low_vehicle_label}",
                                candidate_cluster_radius_m=topology_cluster_radius_m,
                                candidate_min_cluster_nodes=topology_min_cluster_nodes,
                                structural_only=True,
                            )
                            low_vehicle_promotion_report = _reference_delta_promotion_decision(
                                candidate_delta_report=low_vehicle_delta_report,
                                baseline_delta_report=low_vehicle_baseline_delta_report,
                                reason="tls_low_vehicle_control_promoted_by_reference_delta",
                            )
                            low_vehicle_score = _tls_semantic_delta_score(low_vehicle_delta_report)
                            low_vehicle_candidate_record.update(
                                {
                                    "reference_tls_semantic_delta_score": low_vehicle_score,
                                    "reference_delta_file": low_vehicle_delta_report.get("summary_file", ""),
                                    "reference_promotion_status": low_vehicle_promotion_report.get("status", ""),
                                }
                            )
                            if low_vehicle_promotion_report.get("status") == "pass" and (
                                selected_low_vehicle_candidate is None
                                or low_vehicle_score < selected_low_vehicle_candidate[0]
                            ):
                                selected_low_vehicle_candidate = (
                                    low_vehicle_score,
                                    low_vehicle_report,
                                    low_vehicle_sumo_load_report,
                                    low_vehicle_delta_report,
                                    low_vehicle_promotion_report,
                                    low_vehicle_variant_file,
                                )
                        else:
                            low_vehicle_candidate_record["reference_promotion_status"] = "blocked"
                    reference_visual_detail_tls_low_vehicle_control_candidates.append(low_vehicle_candidate_record)
                if selected_low_vehicle_candidate is not None:
                    (
                        _low_vehicle_score,
                        reference_visual_detail_tls_low_vehicle_control_report,
                        reference_visual_detail_tls_low_vehicle_control_sumo_load_report,
                        reference_visual_detail_tls_low_vehicle_control_reference_delta_report,
                        reference_visual_detail_tls_low_vehicle_control_reference_promotion_report,
                        low_vehicle_variant_file,
                    ) = selected_low_vehicle_candidate
                    reference_visual_detail_comparison_net_file = low_vehicle_variant_file
                    reference_visual_detail_comparison_selection_reason = str(
                        reference_visual_detail_tls_low_vehicle_control_reference_promotion_report.get("reason", "")
                    )
            if candidate_visual_tls_net_file is not None and reference_matched_tls_delta:
                tls_id_map = (
                    {}
                    if reference_visual_detail_tls_aggregation_reference_promotion_report.get("status") == "pass"
                    else _tls_representative_id_map(reference_visual_detail_tls_aggregation_report or {})
                )
                if tls_id_map:
                    reference_visual_detail_tls_connection_repair_report = tls_connection_repair_func(
                        source_net_file=reference_visual_detail_net_file,
                        candidate_net_file=candidate_visual_tls_net_file,
                        output_dir=output_dir / "reference_visual_detail_tls_connection_repair",
                        prefix=f"{prefix}_reference_visual_detail_tls_connection_repair",
                        tls_id_map=tls_id_map,
                        copy_unmapped_tls=False,
                        require_target_link_index_capacity=True,
                        pad_mapped_tllogic_capacity=True,
                        add_green_phases_for_padded_links=True,
                        add_yellow_phases_for_generated_green=True,
                    )
                    repair_variant_value = reference_visual_detail_tls_connection_repair_report.get(
                        "variant_file", ""
                    )
                    repair_variant_file = Path(str(repair_variant_value)) if repair_variant_value else None
                    if repair_variant_file is not None and repair_variant_file.exists():
                        reference_visual_detail_tls_connection_repair_sumo_load_report = _sumo_load_net(
                            repair_variant_file,
                            output_dir=output_dir / "reference_visual_detail_tls_connection_repair",
                            sumo_binary=sumo_binary,
                            timeout_seconds=timeout_seconds,
                            command_runner=command_runner,
                        )
                        reference_visual_detail_tls_connection_repair_reference_delta_report = (
                            reference_join_audit_func(
                                reference_net_file=reference_net_file,
                                candidate_net_file=repair_variant_file,
                                output_dir=output_dir
                                / "reference_visual_detail_tls_connection_repair_reference_delta",
                                prefix=f"{prefix}_reference_visual_detail_tls_connection_repair_reference_delta",
                                candidate_cluster_radius_m=topology_cluster_radius_m,
                                candidate_min_cluster_nodes=topology_min_cluster_nodes,
                                structural_only=True,
                            )
                        )
                    reference_visual_detail_tls_connection_repair_promotion_report = (
                        _tls_connection_repair_promotion_decision(
                            repair_report=reference_visual_detail_tls_connection_repair_report,
                            sumo_load_report=reference_visual_detail_tls_connection_repair_sumo_load_report,
                            repair_delta_report=reference_visual_detail_tls_connection_repair_reference_delta_report,
                            rejected_delta_report=reference_visual_detail_tls_aggregation_reference_delta_report,
                        )
                    )
                    if (
                        reference_visual_detail_tls_connection_repair_promotion_report.get("status") == "pass"
                        and repair_variant_file is not None
                    ):
                        reference_visual_detail_comparison_net_file = repair_variant_file
                        reference_visual_detail_comparison_selection_reason = str(
                            reference_visual_detail_tls_connection_repair_promotion_report.get("reason", "")
                        )
    return {
        'low_vehicle_candidate_record': low_vehicle_candidate_record,
        'low_vehicle_delta_report': low_vehicle_delta_report,
        'low_vehicle_label': low_vehicle_label,
        'low_vehicle_limit': low_vehicle_limit,
        'low_vehicle_output_dir': low_vehicle_output_dir,
        'low_vehicle_promotion_report': low_vehicle_promotion_report,
        'low_vehicle_report': low_vehicle_report,
        'low_vehicle_score': low_vehicle_score,
        'low_vehicle_sumo_load_report': low_vehicle_sumo_load_report,
        'low_vehicle_variant_file': low_vehicle_variant_file,
        'low_vehicle_variant_value': low_vehicle_variant_value,
        'missing_shared_groups': missing_shared_groups,
        'reference_visual_detail_comparison_net_file': reference_visual_detail_comparison_net_file,
        'reference_visual_detail_comparison_selection_reason': reference_visual_detail_comparison_selection_reason,
        'reference_visual_detail_raw_reference_delta_report': reference_visual_detail_raw_reference_delta_report,
        'reference_visual_detail_tls_aggregation_reference_delta_report': reference_visual_detail_tls_aggregation_reference_delta_report,
        'reference_visual_detail_tls_aggregation_reference_promotion_report': reference_visual_detail_tls_aggregation_reference_promotion_report,
        'reference_visual_detail_tls_aggregation_report': reference_visual_detail_tls_aggregation_report,
        'reference_visual_detail_tls_connection_repair_promotion_report': reference_visual_detail_tls_connection_repair_promotion_report,
        'reference_visual_detail_tls_connection_repair_reference_delta_report': reference_visual_detail_tls_connection_repair_reference_delta_report,
        'reference_visual_detail_tls_connection_repair_report': reference_visual_detail_tls_connection_repair_report,
        'reference_visual_detail_tls_connection_repair_sumo_load_report': reference_visual_detail_tls_connection_repair_sumo_load_report,
        'reference_visual_detail_tls_low_vehicle_control_reference_delta_report': reference_visual_detail_tls_low_vehicle_control_reference_delta_report,
        'reference_visual_detail_tls_low_vehicle_control_reference_promotion_report': reference_visual_detail_tls_low_vehicle_control_reference_promotion_report,
        'reference_visual_detail_tls_low_vehicle_control_report': reference_visual_detail_tls_low_vehicle_control_report,
        'reference_visual_detail_tls_low_vehicle_control_sumo_load_report': reference_visual_detail_tls_low_vehicle_control_sumo_load_report,
        'reference_visual_detail_tls_report': reference_visual_detail_tls_report,
        'reference_visual_detail_tls_signal_grouping_reference_delta_report': reference_visual_detail_tls_signal_grouping_reference_delta_report,
        'reference_visual_detail_tls_signal_grouping_reference_promotion_report': reference_visual_detail_tls_signal_grouping_reference_promotion_report,
        'reference_visual_detail_tls_signal_grouping_report': reference_visual_detail_tls_signal_grouping_report,
        'reference_visual_detail_tls_signal_grouping_sumo_load_report': reference_visual_detail_tls_signal_grouping_sumo_load_report,
        'repair_variant_file': repair_variant_file,
        'repair_variant_value': repair_variant_value,
        'selected_low_vehicle_candidate': selected_low_vehicle_candidate,
        'signal_grouping_variant_file': signal_grouping_variant_file,
        'signal_grouping_variant_value': signal_grouping_variant_value,
        'tls_aggregation_report': tls_aggregation_report,
    }


def _workflow_reference_matched_section(
    *,
    command_runner: Callable[..., Any],
    net_file: Any,
    netconvert_binary: str,
    network_plan: Any,
    output_dir: Path,
    post_teacher_tls_low_vehicle_control_candidates: list[dict[str, Any]],
    prefix: str,
    reference_join_aggregation_func: Callable[..., dict[str, Any]],
    reference_join_audit_func: Callable[..., dict[str, Any]],
    reference_join_audit_structural_only: bool,
    reference_net_file: Path | None,
    reference_visual_detail_net_file: Path | None,
    reference_visual_detail_raw_reference_delta_report: dict[str, Any] | None,
    reference_visual_detail_tls_aggregation_reference_delta_report: dict[str, Any] | None,
    reference_visual_detail_tls_connection_repair_reference_delta_report: dict[str, Any] | None,
    reference_visual_detail_tls_low_vehicle_control_reference_delta_report: dict[str, Any] | None,
    reference_visual_detail_tls_signal_grouping_reference_delta_report: dict[str, Any] | None,
    road_connection_topology_replay_func: Callable[..., dict[str, Any]],
    road_connectivity_probe_edge_ids: list[str] | None,
    road_connectivity_replay_func: Callable[..., dict[str, Any]],
    road_connectivity_replay_max_owners: int | None,
    road_connectivity_seed_probe_func: Callable[..., dict[str, Any]],
    run_reference_join_aggregation_after_build: bool,
    run_reference_join_audit_after_build: bool,
    run_teacher_guided_repair_after_build: bool,
    run_tls_aggregation_after_build: bool,
    sumo_binary: str,
    teacher_guided_direct_replay_func: Callable[..., dict[str, Any]],
    teacher_guided_plain_export_func: Callable[..., dict[str, Any]],
    teacher_guided_probe_matrix_func: Callable[..., dict[str, Any]],
    teacher_guided_probe_matrix_junction_ids: list[str] | None,
    teacher_guided_repair_max_ready_candidates: int | None,
    teacher_guided_repair_queue_func: Callable[..., dict[str, Any]],
    teacher_guided_repair_run_func: Callable[..., dict[str, Any]],
    timeout_seconds: float,
    tls_connection_repair_func: Callable[..., dict[str, Any]],
    tls_low_vehicle_control_func: Callable[..., dict[str, Any]],
    tls_non_controller_junction_demotion_func: Callable[..., dict[str, Any]],
    tls_signal_grouping_func: Callable[..., dict[str, Any]],
    topology_audit_report: Any,
    topology_cluster_radius_m: float,
    topology_min_cluster_nodes: int,
    post_teacher_tls_connection_repair_movement_rebuild_best_variant_file: Path | None,
    post_teacher_tls_connection_repair_movement_rebuild_queue_report: dict[str, Any] | None,
    post_teacher_tls_connection_repair_movement_rebuild_run_report: dict[str, Any] | None,
    post_teacher_tls_connection_repair_reference_delta_report: dict[str, Any] | None,
    post_teacher_tls_connection_repair_reference_promotion_report: dict[str, Any],
    post_teacher_tls_connection_repair_report: dict[str, Any] | None,
    post_teacher_tls_connection_repair_sumo_load_report: dict[str, Any] | None,
    post_teacher_tls_low_vehicle_control_reference_delta_report: dict[str, Any] | None,
    post_teacher_tls_low_vehicle_control_reference_promotion_report: dict[str, Any],
    post_teacher_tls_non_controller_junction_demotion_reference_delta_report: dict[str, Any] | None,
    post_teacher_tls_non_controller_junction_demotion_reference_promotion_report: dict[str, Any],
    post_teacher_tls_non_controller_junction_demotion_report: dict[str, Any] | None,
    post_teacher_tls_non_controller_junction_demotion_sumo_load_report: dict[str, Any] | None,
    post_teacher_tls_signal_grouping_reference_delta_report: dict[str, Any] | None,
    post_teacher_tls_signal_grouping_reference_promotion_report: dict[str, Any],
    post_teacher_tls_signal_grouping_report: dict[str, Any] | None,
    post_teacher_tls_signal_grouping_sumo_load_report: dict[str, Any] | None,
    reference_join_aggregation_report: dict[str, Any] | None,
    reference_join_audit_candidate_net_file: Path | None,
    reference_join_audit_report: dict[str, Any] | None,
    reference_join_post_teacher_audit_report: dict[str, Any] | None,
    reference_visual_detail_comparison_net_file: Path | None,
    road_connection_topology_replay_report: dict[str, Any] | None,
    road_connectivity_replay_report: dict[str, Any] | None,
    road_connectivity_seed_probe_report: dict[str, Any] | None,
    road_connectivity_split_root_alias_repair_report: dict[str, Any] | None,
    teacher_guided_direct_replay_reference_delta_report: dict[str, Any] | None,
    teacher_guided_direct_replay_reference_promotion_report: dict[str, Any],
    teacher_guided_direct_replay_report: dict[str, Any] | None,
    teacher_guided_plain_export_report: dict[str, Any] | None,
    teacher_guided_repair_best_variant_file: Path | None,
    teacher_guided_repair_queue_report: dict[str, Any] | None,
    teacher_guided_repair_reference_promotion_report: dict[str, Any],
    teacher_guided_repair_requires_reference_promotion: Any,
    teacher_guided_repair_run_report: dict[str, Any] | None,
    teacher_guided_scoped_tls_cell_batch_report: dict[str, Any] | None,
    teacher_guided_seed_report: dict[str, Any] | None,
    tls_gap_destination_mapping_report: dict[str, Any] | None,
    tls_repair_variant_reference_audit_report: dict[str, Any] | None,
    tls_repair_variant_report: dict[str, Any] | None,
    tls_repair_variant_semantic_report: dict[str, Any] | None,
    tls_repair_variant_sumo_load_report: dict[str, Any] | None,
) -> dict[str, Any]:
    direct_variant_file = _WORKFLOW_UNSET
    post_teacher_tls_connection_repair_movement_rebuild_plain_export_report = _WORKFLOW_UNSET
    post_teacher_tls_low_vehicle_control_report = _WORKFLOW_UNSET
    post_teacher_tls_low_vehicle_control_sumo_load_report = _WORKFLOW_UNSET
    reference_join_audit_candidate_layer = _WORKFLOW_UNSET
    reference_visual_detail_comparison_selection_reason = _WORKFLOW_UNSET
    teacher_guided_direct_replay_best_variant_file = _WORKFLOW_UNSET
    teacher_guided_probe_matrix_report = _WORKFLOW_UNSET
    teacher_guided_repair_best_expanded_scope_net_file = _WORKFLOW_UNSET
    teacher_guided_repair_seed_source = _WORKFLOW_UNSET
    tls_repair_decision_report = _WORKFLOW_UNSET
    candidate = _WORKFLOW_UNSET
    candidate_joined_net_file = _WORKFLOW_UNSET
    candidate_teacher_guided_best_variant_file = _WORKFLOW_UNSET
    connection_repair_baseline_report = _WORKFLOW_UNSET
    connection_repair_missing_counts = _WORKFLOW_UNSET
    connection_repair_output_dir = _WORKFLOW_UNSET
    connection_repair_source_net_file = _WORKFLOW_UNSET
    connection_repair_variant_file = _WORKFLOW_UNSET
    connection_repair_variant_value = _WORKFLOW_UNSET
    direct_index = _WORKFLOW_UNSET
    direct_replay_candidates = _WORKFLOW_UNSET
    direct_replay_source_net_file = _WORKFLOW_UNSET
    direct_variant_value = _WORKFLOW_UNSET
    expanded_scope_file = _WORKFLOW_UNSET
    expanded_scope_value = _WORKFLOW_UNSET
    extra_traffic_light_junctions = _WORKFLOW_UNSET
    followup_best_variant_file = _WORKFLOW_UNSET
    followup_delta_report = _WORKFLOW_UNSET
    followup_demotion_file = _WORKFLOW_UNSET
    followup_demotion_report = _WORKFLOW_UNSET
    followup_demotion_value = _WORKFLOW_UNSET
    followup_edge_map = _WORKFLOW_UNSET
    followup_promotion_report = _WORKFLOW_UNSET
    followup_queue_report = _WORKFLOW_UNSET
    followup_run_report = _WORKFLOW_UNSET
    followup_sumo_load_report = _WORKFLOW_UNSET
    internal_regression_restore_report = _WORKFLOW_UNSET
    join_output_status = _WORKFLOW_UNSET
    joined_value = _WORKFLOW_UNSET
    low_vehicle_candidate_record = _WORKFLOW_UNSET
    low_vehicle_delta_report = _WORKFLOW_UNSET
    low_vehicle_label = _WORKFLOW_UNSET
    low_vehicle_limit = _WORKFLOW_UNSET
    low_vehicle_output_dir = _WORKFLOW_UNSET
    low_vehicle_promotion_report = _WORKFLOW_UNSET
    low_vehicle_report = _WORKFLOW_UNSET
    low_vehicle_score = _WORKFLOW_UNSET
    low_vehicle_sumo_load_report = _WORKFLOW_UNSET
    low_vehicle_variant_file = _WORKFLOW_UNSET
    low_vehicle_variant_value = _WORKFLOW_UNSET
    missing_controlled_connections = _WORKFLOW_UNSET
    missing_shared_groups = _WORKFLOW_UNSET
    missing_sparse_tllogics = _WORKFLOW_UNSET
    non_controller_base_edge_map = _WORKFLOW_UNSET
    non_controller_demotion_baseline_report = _WORKFLOW_UNSET
    non_controller_demotion_extra_counts = _WORKFLOW_UNSET
    non_controller_demotion_output_dir = _WORKFLOW_UNSET
    non_controller_demotion_source_net_file = _WORKFLOW_UNSET
    non_controller_demotion_variant_file = _WORKFLOW_UNSET
    non_controller_demotion_variant_value = _WORKFLOW_UNSET
    preservation_status = _WORKFLOW_UNSET
    probe_matrix_junction_ids = _WORKFLOW_UNSET
    queue_file_value = _WORKFLOW_UNSET
    raw_tllogic_value = _WORKFLOW_UNSET
    raw_type_value = _WORKFLOW_UNSET
    reference_join_audit_is_structural_only = _WORKFLOW_UNSET
    repair_variant_file = _WORKFLOW_UNSET
    repair_variant_value = _WORKFLOW_UNSET
    restore_promotion_report = _WORKFLOW_UNSET
    restored_delta_report = _WORKFLOW_UNSET
    restored_followup_file = _WORKFLOW_UNSET
    restored_sumo_load_report = _WORKFLOW_UNSET
    road_connection_topology_variant_file = _WORKFLOW_UNSET
    road_connectivity_seed_edge_ids = _WORKFLOW_UNSET
    road_connectivity_split_alias_variant_file = _WORKFLOW_UNSET
    selected_low_vehicle_candidate = _WORKFLOW_UNSET
    shared_candidate_value = _WORKFLOW_UNSET
    shared_controller_candidate_net_file = _WORKFLOW_UNSET
    signal_grouping_baseline_report = _WORKFLOW_UNSET
    signal_grouping_missing_counts = _WORKFLOW_UNSET
    signal_grouping_output_dir = _WORKFLOW_UNSET
    signal_grouping_source_net_file = _WORKFLOW_UNSET
    signal_grouping_variant_file = _WORKFLOW_UNSET
    signal_grouping_variant_value = _WORKFLOW_UNSET
    teacher_guided_queue_needed = _WORKFLOW_UNSET
    teacher_guided_replay_source_net_file = _WORKFLOW_UNSET
    teacher_guided_scoped_tls_batch_pass_candidate_ids = _WORKFLOW_UNSET
    teacher_guided_seed_structural_only = _WORKFLOW_UNSET
    trial_queue_report = _WORKFLOW_UNSET
    if (
        str(network_plan.get("network_profile", "")) == "reference_matched"
        and reference_net_file is not None
        and run_reference_join_audit_after_build
    ):
        reference_join_audit_candidate_net_file = reference_visual_detail_comparison_net_file or reference_visual_detail_net_file or net_file
        reference_join_audit_candidate_layer = (
            "reference_visual_detail" if reference_visual_detail_comparison_net_file is not None or reference_visual_detail_net_file is not None else "vehicle_core"
        )
        road_connectivity_seed_edge_ids = [
            str(edge_id).strip()
            for edge_id in (road_connectivity_probe_edge_ids or [])
            if str(edge_id).strip()
        ]
        if road_connectivity_seed_edge_ids:
            road_connectivity_seed_probe_report = road_connectivity_seed_probe_func(
                teacher_net_file=reference_net_file,
                candidate_net_file=reference_join_audit_candidate_net_file,
                seed_edge_ids=road_connectivity_seed_edge_ids,
                output_dir=output_dir / "road_connectivity_seed_probe",
                prefix=f"{prefix}_road_connectivity_seed_probe",
            )
        reference_join_audit_report = reference_join_audit_func(
            reference_net_file=reference_net_file,
            candidate_net_file=reference_join_audit_candidate_net_file,
            output_dir=output_dir / "reference_join_audit",
            prefix=f"{prefix}_reference_join_audit",
            candidate_cluster_radius_m=topology_cluster_radius_m,
            candidate_min_cluster_nodes=topology_min_cluster_nodes,
            structural_only=reference_join_audit_structural_only,
        )
        if (
            reference_join_audit_report.get("status") in {"pass", "blocked"}
            and reference_join_audit_candidate_net_file.exists()
            and reference_join_audit_report.get("tls_controller_alignment")
        ):
            tls_gap_destination_mapping_report = build_tls_gap_destination_mapping(
                reference_net_file=reference_net_file,
                candidate_net_file=reference_join_audit_candidate_net_file,
                alignment_report=reference_join_audit_report,
                output_dir=output_dir / "tls_gap_destination_mapping",
                prefix=f"{prefix}_tls_gap_destination_mapping",
            )
            tls_repair_variant_report = build_tls_gap_repair_variant(
                mapping_report=tls_gap_destination_mapping_report,
                candidate_net_file=reference_join_audit_candidate_net_file,
                output_dir=output_dir / "tls_gap_repair_variant",
                prefix=f"{prefix}_tls_gap_repair",
                netconvert_binary=netconvert_binary,
                timeout_seconds=timeout_seconds,
                command_runner=command_runner,
            )
            repair_variant_value = tls_repair_variant_report.get("variant_file", "")
            repair_variant_file = Path(str(repair_variant_value)) if repair_variant_value else None
            if repair_variant_file is not None and repair_variant_file.exists():
                tls_repair_variant_semantic_report = audit_tls_gap_variant_semantics(
                    mapping_report=tls_gap_destination_mapping_report,
                    variant_report=tls_repair_variant_report,
                    candidate_net_file=reference_join_audit_candidate_net_file,
                    variant_net_file=repair_variant_file,
                    output_dir=output_dir / "tls_gap_repair_variant",
                    prefix=f"{prefix}_tls_gap_variant_semantic_parity",
                )
                tls_repair_variant_sumo_load_report = _sumo_load_net(
                    repair_variant_file,
                    output_dir=output_dir / "tls_gap_repair_variant",
                    sumo_binary=sumo_binary,
                    timeout_seconds=timeout_seconds,
                    command_runner=command_runner,
                )
                if tls_repair_variant_sumo_load_report.get("status") == "pass":
                    tls_repair_variant_reference_audit_report = reference_join_audit_func(
                        reference_net_file=reference_net_file,
                        candidate_net_file=repair_variant_file,
                        output_dir=output_dir / "tls_gap_repair_variant_reference_audit",
                        prefix=f"{prefix}_tls_gap_repair_variant_reference_audit",
                        candidate_cluster_radius_m=topology_cluster_radius_m,
                        candidate_min_cluster_nodes=topology_min_cluster_nodes,
                        structural_only=True,
                    )
            tls_repair_decision_report = build_tls_repair_decision_report(
                mapping_report=tls_gap_destination_mapping_report,
                variant_report=tls_repair_variant_report,
                sumo_load_report=tls_repair_variant_sumo_load_report,
                semantic_report=tls_repair_variant_reference_audit_report,
                tls_variant_semantic_report=tls_repair_variant_semantic_report,
                output_dir=output_dir / "tls_gap_destination_mapping",
                prefix=f"{prefix}_tls_repair_decision",
            )
        reference_join_audit_is_structural_only = reference_join_audit_report.get("audit_mode") == "structural_only"
        if run_reference_join_aggregation_after_build and not reference_join_audit_is_structural_only:
            reference_join_aggregation_report = reference_join_aggregation_func(
                net_file=reference_join_audit_candidate_net_file,
                output_dir=output_dir / "reference_join_aggregation",
                prefix=f"{prefix}_reference_join_aggregation",
                topology_audit_report=topology_audit_report,
                reference_join_audit_report=reference_join_audit_report,
                join_dist_m=topology_cluster_radius_m,
                timeout_seconds=timeout_seconds,
            )
            joined_value = reference_join_aggregation_report.get("junction_aggregation_variant_file", "")
            preservation_status = str(
                reference_join_aggregation_report.get("junction_aggregation_preservation_status", "pass")
            )
            join_output_status = str(
                reference_join_aggregation_report.get("junction_aggregation_join_output_audit_status", "pass")
            )
            if (
                reference_join_aggregation_report.get("status") == "pass"
                and joined_value
                and preservation_status == "pass"
                and join_output_status == "pass"
            ):
                candidate_joined_net_file = Path(str(joined_value))
                if candidate_joined_net_file.exists():
                    reference_visual_detail_comparison_net_file = candidate_joined_net_file
        (
            teacher_guided_seed_report,
            teacher_guided_seed_structural_only,
            teacher_guided_repair_requires_reference_promotion,
            teacher_guided_repair_seed_source,
        ) = _teacher_guided_seed_candidate(
            reference_join_audit_report,
            primary_structural_only=reference_join_audit_is_structural_only,
            fallback_reports=[
                (
                    "reference_visual_detail_tls_connection_repair_reference_delta",
                    reference_visual_detail_tls_connection_repair_reference_delta_report,
                ),
                (
                    "reference_visual_detail_tls_low_vehicle_control_reference_delta",
                    reference_visual_detail_tls_low_vehicle_control_reference_delta_report,
                ),
                (
                    "reference_visual_detail_tls_signal_grouping_reference_delta",
                    reference_visual_detail_tls_signal_grouping_reference_delta_report,
                ),
                (
                    "reference_visual_detail_tls_aggregation_reference_delta",
                    reference_visual_detail_tls_aggregation_reference_delta_report,
                ),
                ("reference_visual_detail_raw_reference_delta", reference_visual_detail_raw_reference_delta_report),
            ],
        )
        teacher_guided_queue_needed = (
            run_teacher_guided_repair_after_build
            or road_connectivity_replay_max_owners is None
            or road_connectivity_replay_max_owners > 0
            or bool(road_connectivity_seed_edge_ids)
            or bool(teacher_guided_probe_matrix_junction_ids)
        )
        if teacher_guided_queue_needed and _reference_join_audit_can_seed_teacher_guided_queue(
            teacher_guided_seed_report,
            structural_only=teacher_guided_seed_structural_only,
        ):
            teacher_guided_repair_queue_report = teacher_guided_repair_queue_func(
                teacher_net_file=reference_net_file,
                candidate_net_file=reference_visual_detail_comparison_net_file or reference_join_audit_candidate_net_file,
                reference_join_audit_report=dict(teacher_guided_seed_report),
                output_dir=output_dir / "teacher_guided_repair_queue",
                prefix=f"{prefix}_teacher_guided_repair",
                max_ready_candidates=teacher_guided_repair_max_ready_candidates,
            )
            teacher_guided_repair_queue_report = _filter_teacher_guided_queue_to_mismatch_fields(
                teacher_guided_repair_queue_report,
                teacher_guided_seed_report,
                {"movement_signature_counts", "internal_function_counts"},
                output_dir=output_dir / "teacher_guided_repair_queue",
                prefix=f"{prefix}_teacher_guided_repair_movement_mismatches",
            )
            shared_controller_candidate_net_file: Path | None = None
            if reference_join_aggregation_report is not None:
                shared_candidate_value = str(
                    reference_join_aggregation_report.get("junction_aggregation_variant_file", "")
                ).strip()
                if shared_candidate_value and Path(shared_candidate_value).exists():
                    shared_controller_candidate_net_file = Path(shared_candidate_value)
            teacher_guided_scoped_tls_cell_batch_report = run_scoped_teacher_tls_cell_batch(
                queue_report=teacher_guided_repair_queue_report,
                source_net_file=reference_visual_detail_comparison_net_file
                or reference_join_audit_candidate_net_file,
                output_dir=output_dir / "teacher_guided_scoped_tls_cell_batch",
                prefix=f"{prefix}_teacher_guided",
                netconvert_binary=netconvert_binary,
                sumo_binary=sumo_binary,
                timeout_seconds=timeout_seconds,
                command_runner=command_runner,
                shared_controller_source_net_file=shared_controller_candidate_net_file,
            )
            teacher_guided_scoped_tls_batch_pass_candidate_ids = {
                str(item.get("candidate_tl_id", "")).strip()
                for item in teacher_guided_scoped_tls_cell_batch_report.get("cell_reports", []) or []
                if isinstance(item, Mapping) and item.get("status") == "pass" and str(item.get("candidate_tl_id", "")).strip()
            }
            (
                road_connectivity_replay_report,
                road_connectivity_seed_probe_report,
                road_connectivity_split_root_alias_repair_report,
                road_connection_topology_replay_report,
            ) = _run_road_connectivity_replay_sequence(
                teacher_net_file=reference_net_file,
                candidate_net_file=reference_visual_detail_comparison_net_file or reference_join_audit_candidate_net_file,
                queue_report=teacher_guided_repair_queue_report,
                seed_probe_report=road_connectivity_seed_probe_report,
                seed_edge_ids=road_connectivity_seed_edge_ids,
                output_dir=output_dir,
                prefix=prefix,
                max_owner_count=road_connectivity_replay_max_owners,
                sumo_binary=sumo_binary,
                timeout_seconds=timeout_seconds,
                command_runner=command_runner,
                road_connectivity_replay_func=road_connectivity_replay_func,
                road_connectivity_seed_probe_func=road_connectivity_seed_probe_func,
                road_connection_topology_replay_func=road_connection_topology_replay_func,
            )
            if run_teacher_guided_repair_after_build and _teacher_guided_queue_has_replay_candidates(
                teacher_guided_repair_queue_report
            ):
                road_connectivity_split_alias_variant_file = (
                    Path(str(road_connectivity_split_root_alias_repair_report["output_file"]))
                    if road_connectivity_split_root_alias_repair_report is not None
                    and road_connectivity_split_root_alias_repair_report.get("status") == "pass"
                    and road_connectivity_split_root_alias_repair_report.get("output_file")
                    else None
                )
                road_connection_topology_variant_file = (
                    Path(str(road_connection_topology_replay_report["output_file"]))
                    if road_connection_topology_replay_report is not None
                    and road_connection_topology_replay_report.get("status") == "pass"
                    and road_connection_topology_replay_report.get("output_file")
                    else None
                )
                teacher_guided_replay_source_net_file = (
                    road_connection_topology_variant_file
                    or road_connectivity_split_alias_variant_file
                    or _road_connectivity_best_variant_file(road_connectivity_replay_report)
                    or reference_visual_detail_comparison_net_file
                    or reference_join_audit_candidate_net_file
                )
                teacher_guided_plain_export_report = teacher_guided_plain_export_func(
                    net_file=teacher_guided_replay_source_net_file,
                    output_dir=output_dir / "teacher_guided_repair_plain",
                    prefix=f"{prefix}_teacher_guided_repair",
                    netconvert_binary=netconvert_binary,
                    timeout_seconds=timeout_seconds,
                )
                if teacher_guided_plain_export_report.get("status") == "pass":
                    raw_type_value = str(teacher_guided_plain_export_report.get("raw_type_file", ""))
                    raw_tllogic_value = str(teacher_guided_plain_export_report.get("raw_tllogic_file", ""))
                    queue_file_value = str(teacher_guided_repair_queue_report.get("queue_file", ""))
                    teacher_guided_repair_run_report = teacher_guided_repair_run_func(
                        queue_report=teacher_guided_repair_queue_report,
                        raw_node_file=Path(str(teacher_guided_plain_export_report["raw_node_file"])),
                        raw_edge_file=Path(str(teacher_guided_plain_export_report["raw_edge_file"])),
                        raw_connection_file=Path(str(teacher_guided_plain_export_report["raw_connection_file"])),
                        raw_type_file=Path(raw_type_value) if raw_type_value else None,
                        raw_tllogic_file=Path(raw_tllogic_value) if raw_tllogic_value else None,
                        output_dir=output_dir / "teacher_guided_repair_execution",
                        prefix=f"{prefix}_teacher_guided_repair",
                        queue_base_dir=Path(queue_file_value).resolve().parent if queue_file_value else None,
                        replay_target_internal_subgraph=True,
                        max_ready_candidates=teacher_guided_repair_max_ready_candidates,
                        netconvert_binary=netconvert_binary,
                        sumo_binary=sumo_binary,
                        timeout_seconds=timeout_seconds,
                        sequential_accept_passed_variants=True,
                        plain_exporter=teacher_guided_plain_export_func,
                    )
                    probe_matrix_junction_ids = [
                        str(junction_id).strip()
                        for junction_id in (teacher_guided_probe_matrix_junction_ids or [])
                        if str(junction_id).strip()
                    ]
                    if probe_matrix_junction_ids:
                        teacher_guided_probe_matrix_report = teacher_guided_probe_matrix_func(
                            queue_report=teacher_guided_repair_queue_report,
                            target_junction_ids=probe_matrix_junction_ids,
                            raw_node_file=Path(str(teacher_guided_plain_export_report["raw_node_file"])),
                            raw_edge_file=Path(str(teacher_guided_plain_export_report["raw_edge_file"])),
                            raw_connection_file=Path(str(teacher_guided_plain_export_report["raw_connection_file"])),
                            raw_type_file=Path(raw_type_value) if raw_type_value else None,
                            raw_tllogic_file=Path(raw_tllogic_value) if raw_tllogic_value else None,
                            output_dir=output_dir / "teacher_guided_probe_matrix",
                            prefix=f"{prefix}_teacher_guided_probe_matrix",
                            queue_base_dir=Path(queue_file_value).resolve().parent if queue_file_value else None,
                            replay_target_internal_subgraph=True,
                            netconvert_binary=netconvert_binary,
                            sumo_binary=sumo_binary,
                            timeout_seconds=timeout_seconds,
                            command_runner=command_runner,
                            sequential_accept_passed_variants=True,
                        )
                    candidate_teacher_guided_best_variant_file = _teacher_guided_best_variant_file(
                        teacher_guided_repair_run_report
                    )
                    expanded_scope_value = str(teacher_guided_repair_run_report.get("best_expanded_scope_net_file", ""))
                    if expanded_scope_value:
                        expanded_scope_file = Path(expanded_scope_value)
                        if expanded_scope_file.exists():
                            teacher_guided_repair_best_expanded_scope_net_file = expanded_scope_file
                    if candidate_teacher_guided_best_variant_file is not None:
                        reference_join_post_teacher_audit_report = reference_join_audit_func(
                            reference_net_file=reference_net_file,
                            candidate_net_file=candidate_teacher_guided_best_variant_file,
                            output_dir=output_dir / "post_teacher_reference_join_audit",
                            prefix=f"{prefix}_post_teacher_reference_join_audit",
                            candidate_cluster_radius_m=topology_cluster_radius_m,
                            candidate_min_cluster_nodes=topology_min_cluster_nodes,
                            structural_only=teacher_guided_seed_structural_only,
                        )
                        if teacher_guided_repair_requires_reference_promotion:
                            teacher_guided_repair_reference_promotion_report = _reference_delta_promotion_decision(
                                candidate_delta_report=reference_join_post_teacher_audit_report,
                                baseline_delta_report=teacher_guided_seed_report,
                                reason="structural_teacher_guided_promoted_by_reference_delta",
                            )
                        else:
                            teacher_guided_repair_reference_promotion_report = {
                                "status": "pass",
                                "reason": "full_reference_join_teacher_guided_promoted_by_parity",
                            }
                        if teacher_guided_repair_reference_promotion_report.get("status") == "pass":
                            teacher_guided_repair_best_variant_file = candidate_teacher_guided_best_variant_file
                            reference_visual_detail_comparison_net_file = teacher_guided_repair_best_variant_file
                        if (
                            teacher_guided_repair_reference_promotion_report.get("status") == "pass"
                            and run_tls_aggregation_after_build
                        ):
                            selected_low_vehicle_candidate: tuple[
                                int,
                                dict[str, Any],
                                dict[str, Any],
                                dict[str, Any],
                                dict[str, Any],
                                Path,
                            ] | None = None
                            for low_vehicle_limit in _low_vehicle_control_candidate_limits(
                                reference_join_post_teacher_audit_report
                            ):
                                low_vehicle_label = str(low_vehicle_limit["label"])
                                low_vehicle_output_dir = (
                                    output_dir / f"post_teacher_tls_low_vehicle_control_{low_vehicle_label}"
                                )
                                low_vehicle_report = tls_low_vehicle_control_func(
                                    source_net_file=teacher_guided_repair_best_variant_file,
                                    tls_control_review_queue=reference_join_post_teacher_audit_report.get(
                                        "tls_control_review_queue", []
                                    ),
                                    output_dir=low_vehicle_output_dir,
                                    prefix=f"{prefix}_post_teacher_tls_low_vehicle_control_{low_vehicle_label}",
                                    max_removed_controlled_connections=low_vehicle_limit[
                                        "max_removed_controlled_connections"
                                    ],
                                    max_selected_tllogic_count=low_vehicle_limit["max_selected_tllogic_count"],
                                )
                                low_vehicle_candidate_record = {
                                    "candidate_label": low_vehicle_label,
                                    "status": low_vehicle_report.get("status", "fail"),
                                    "max_removed_controlled_connections": low_vehicle_limit[
                                        "max_removed_controlled_connections"
                                    ],
                                    "max_selected_tllogic_count": low_vehicle_limit["max_selected_tllogic_count"],
                                    "selected_tllogic_count": low_vehicle_report.get(
                                        "tls_low_vehicle_control_selected_tllogic_count", 0
                                    ),
                                    "removed_connection_count": low_vehicle_report.get(
                                        "tls_low_vehicle_control_removed_connection_count", 0
                                    ),
                                }
                                low_vehicle_variant_value = low_vehicle_report.get(
                                    "tls_low_vehicle_control_variant_file", ""
                                )
                                low_vehicle_variant_file = (
                                    Path(str(low_vehicle_variant_value)) if low_vehicle_variant_value else None
                                )
                                if low_vehicle_variant_file is not None and low_vehicle_variant_file.exists():
                                    low_vehicle_sumo_load_report = _sumo_load_net(
                                        low_vehicle_variant_file,
                                        output_dir=low_vehicle_output_dir,
                                        sumo_binary=sumo_binary,
                                        timeout_seconds=timeout_seconds,
                                        command_runner=command_runner,
                                    )
                                    low_vehicle_candidate_record["sumo_load_status"] = (
                                        low_vehicle_sumo_load_report.get("status", "fail")
                                    )
                                    if low_vehicle_sumo_load_report.get("status") == "pass":
                                        low_vehicle_delta_report = reference_join_audit_func(
                                            reference_net_file=reference_net_file,
                                            candidate_net_file=low_vehicle_variant_file,
                                            output_dir=output_dir
                                            / f"post_teacher_tls_low_vehicle_control_reference_delta_{low_vehicle_label}",
                                            prefix=(
                                                f"{prefix}_post_teacher_tls_low_vehicle_control_reference_delta_"
                                                f"{low_vehicle_label}"
                                            ),
                                            candidate_cluster_radius_m=topology_cluster_radius_m,
                                            candidate_min_cluster_nodes=topology_min_cluster_nodes,
                                            structural_only=True,
                                        )
                                        low_vehicle_promotion_report = _reference_delta_promotion_decision(
                                            candidate_delta_report=low_vehicle_delta_report,
                                            baseline_delta_report=reference_join_post_teacher_audit_report,
                                            reason="post_teacher_tls_low_vehicle_promoted_by_reference_delta",
                                        )
                                        low_vehicle_score = _tls_semantic_delta_score(low_vehicle_delta_report)
                                        low_vehicle_candidate_record.update(
                                            {
                                                "reference_tls_semantic_delta_score": low_vehicle_score,
                                                "reference_delta_file": low_vehicle_delta_report.get(
                                                    "summary_file", ""
                                                ),
                                                "reference_promotion_status": low_vehicle_promotion_report.get(
                                                    "status", ""
                                                ),
                                            }
                                        )
                                        if low_vehicle_promotion_report.get("status") == "pass" and (
                                            selected_low_vehicle_candidate is None
                                            or low_vehicle_score < selected_low_vehicle_candidate[0]
                                        ):
                                            selected_low_vehicle_candidate = (
                                                low_vehicle_score,
                                                low_vehicle_report,
                                                low_vehicle_sumo_load_report,
                                                low_vehicle_delta_report,
                                                low_vehicle_promotion_report,
                                                low_vehicle_variant_file,
                                            )
                                    else:
                                        low_vehicle_candidate_record["reference_promotion_status"] = "blocked"
                                post_teacher_tls_low_vehicle_control_candidates.append(low_vehicle_candidate_record)
                            if selected_low_vehicle_candidate is not None:
                                (
                                    _low_vehicle_score,
                                    post_teacher_tls_low_vehicle_control_report,
                                    post_teacher_tls_low_vehicle_control_sumo_load_report,
                                    post_teacher_tls_low_vehicle_control_reference_delta_report,
                                    post_teacher_tls_low_vehicle_control_reference_promotion_report,
                                    low_vehicle_variant_file,
                                ) = selected_low_vehicle_candidate
                                reference_visual_detail_comparison_net_file = low_vehicle_variant_file
                                reference_visual_detail_comparison_selection_reason = str(
                                    post_teacher_tls_low_vehicle_control_reference_promotion_report.get("reason", "")
                                )
                            signal_grouping_baseline_report = (
                                post_teacher_tls_low_vehicle_control_reference_delta_report
                                or reference_join_post_teacher_audit_report
                            )
                            signal_grouping_source_net_file = (
                                reference_visual_detail_comparison_net_file or teacher_guided_repair_best_variant_file
                            )
                            signal_grouping_missing_counts = signal_grouping_baseline_report.get(
                                "network_structural_missing_counts", {}
                            )
                            if not isinstance(signal_grouping_missing_counts, Mapping):
                                signal_grouping_missing_counts = {}
                            missing_shared_groups = int(
                                signal_grouping_missing_counts.get("tls_shared_linkindex_group_count", 0) or 0
                            )
                            if missing_shared_groups > 0 and signal_grouping_source_net_file is not None:
                                signal_grouping_output_dir = output_dir / "post_teacher_tls_signal_grouping"
                                post_teacher_tls_signal_grouping_report = tls_signal_grouping_func(
                                    source_net_file=signal_grouping_source_net_file,
                                    output_dir=signal_grouping_output_dir,
                                    prefix=f"{prefix}_post_teacher_tls_signal_grouping",
                                    max_shared_linkindex_groups=missing_shared_groups,
                                )
                                signal_grouping_variant_value = post_teacher_tls_signal_grouping_report.get(
                                    "tls_signal_grouping_variant_file", ""
                                )
                                signal_grouping_variant_file = (
                                    Path(str(signal_grouping_variant_value)) if signal_grouping_variant_value else None
                                )
                                if signal_grouping_variant_file is not None and signal_grouping_variant_file.exists():
                                    post_teacher_tls_signal_grouping_sumo_load_report = _sumo_load_net(
                                        signal_grouping_variant_file,
                                        output_dir=signal_grouping_output_dir,
                                        sumo_binary=sumo_binary,
                                        timeout_seconds=timeout_seconds,
                                        command_runner=command_runner,
                                    )
                                    if post_teacher_tls_signal_grouping_sumo_load_report.get("status") == "pass":
                                        post_teacher_tls_signal_grouping_reference_delta_report = reference_join_audit_func(
                                            reference_net_file=reference_net_file,
                                            candidate_net_file=signal_grouping_variant_file,
                                            output_dir=output_dir
                                            / "post_teacher_tls_signal_grouping_reference_delta",
                                            prefix=f"{prefix}_post_teacher_tls_signal_grouping_reference_delta",
                                            candidate_cluster_radius_m=topology_cluster_radius_m,
                                            candidate_min_cluster_nodes=topology_min_cluster_nodes,
                                            structural_only=True,
                                        )
                                        post_teacher_tls_signal_grouping_reference_promotion_report = (
                                            _reference_delta_promotion_decision(
                                                candidate_delta_report=post_teacher_tls_signal_grouping_reference_delta_report,
                                                baseline_delta_report=signal_grouping_baseline_report,
                                                reason="post_teacher_tls_signal_grouping_promoted_by_reference_delta",
                                            )
                                        )
                                        if (
                                            post_teacher_tls_signal_grouping_reference_promotion_report.get("status")
                                            == "pass"
                                        ):
                                            reference_visual_detail_comparison_net_file = signal_grouping_variant_file
                                            reference_visual_detail_comparison_selection_reason = str(
                                                post_teacher_tls_signal_grouping_reference_promotion_report.get(
                                                    "reason", ""
                                                )
                                            )
                                    else:
                                        post_teacher_tls_signal_grouping_reference_promotion_report = {
                                            "status": "blocked",
                                            "reason": "sumo_load_not_pass",
                                        }
                            connection_repair_baseline_report = (
                                post_teacher_tls_signal_grouping_reference_delta_report
                                or post_teacher_tls_low_vehicle_control_reference_delta_report
                                or reference_join_post_teacher_audit_report
                            )
                            connection_repair_source_net_file = (
                                reference_visual_detail_comparison_net_file or teacher_guided_repair_best_variant_file
                            )
                            connection_repair_missing_counts = connection_repair_baseline_report.get(
                                "network_structural_missing_counts", {}
                            )
                            if not isinstance(connection_repair_missing_counts, Mapping):
                                connection_repair_missing_counts = {}
                            missing_controlled_connections = int(
                                connection_repair_missing_counts.get("tls_controlled_connection_count", 0) or 0
                            )
                            missing_sparse_tllogics = int(
                                connection_repair_missing_counts.get("tls_sparse_linkindex_tl_logic_count", 0) or 0
                            )
                            if (
                                reference_net_file is not None
                                and connection_repair_source_net_file is not None
                                and (missing_controlled_connections > 0 or missing_sparse_tllogics > 0)
                            ):
                                connection_repair_output_dir = output_dir / "post_teacher_tls_connection_repair"
                                post_teacher_tls_connection_repair_report = tls_connection_repair_func(
                                    source_net_file=reference_net_file,
                                    candidate_net_file=connection_repair_source_net_file,
                                    output_dir=connection_repair_output_dir,
                                    prefix=f"{prefix}_post_teacher_tls_connection_repair",
                                    copy_unmapped_tls=True,
                                    require_target_link_index_capacity=False,
                                )
                                connection_repair_variant_value = post_teacher_tls_connection_repair_report.get(
                                    "variant_file", ""
                                )
                                connection_repair_variant_file = (
                                    Path(str(connection_repair_variant_value))
                                    if connection_repair_variant_value
                                    else None
                                )
                                if (
                                    connection_repair_variant_file is not None
                                    and connection_repair_variant_file.exists()
                                ):
                                    post_teacher_tls_connection_repair_sumo_load_report = _sumo_load_net(
                                        connection_repair_variant_file,
                                        output_dir=connection_repair_output_dir,
                                        sumo_binary=sumo_binary,
                                        timeout_seconds=timeout_seconds,
                                        command_runner=command_runner,
                                    )
                                    if post_teacher_tls_connection_repair_sumo_load_report.get("status") == "pass":
                                        post_teacher_tls_connection_repair_reference_delta_report = (
                                            reference_join_audit_func(
                                                reference_net_file=reference_net_file,
                                                candidate_net_file=connection_repair_variant_file,
                                                output_dir=output_dir
                                                / "post_teacher_tls_connection_repair_reference_delta",
                                                prefix=f"{prefix}_post_teacher_tls_connection_repair_reference_delta",
                                                candidate_cluster_radius_m=topology_cluster_radius_m,
                                                candidate_min_cluster_nodes=topology_min_cluster_nodes,
                                                structural_only=True,
                                            )
                                        )
                                        post_teacher_tls_connection_repair_reference_promotion_report = (
                                            _reference_delta_promotion_decision(
                                                candidate_delta_report=post_teacher_tls_connection_repair_reference_delta_report,
                                                baseline_delta_report=connection_repair_baseline_report,
                                                reason="post_teacher_tls_connection_repair_promoted_by_reference_delta",
                                            )
                                        )
                                        if (
                                            _int_field(
                                                post_teacher_tls_connection_repair_reference_delta_report,
                                                "junction_pattern_mismatch_count",
                                            )
                                            > 0
                                        ):
                                            post_teacher_tls_connection_repair_movement_rebuild_queue_report = (
                                                teacher_guided_repair_queue_func(
                                                    teacher_net_file=reference_net_file,
                                                    candidate_net_file=connection_repair_variant_file,
                                                    reference_join_audit_report=(
                                                        post_teacher_tls_connection_repair_reference_delta_report
                                                    ),
                                                    output_dir=output_dir
                                                    / "post_teacher_tls_connection_repair_movement_rebuild_queue",
                                                    prefix=(
                                                        f"{prefix}_post_teacher_tls_connection_repair_"
                                                        "movement_rebuild"
                                                    ),
                                                    max_ready_candidates=teacher_guided_repair_max_ready_candidates,
                                                )
                                            )
                                        if (
                                            post_teacher_tls_connection_repair_reference_promotion_report.get("status")
                                            == "pass"
                                        ):
                                            reference_visual_detail_comparison_net_file = connection_repair_variant_file
                                            reference_visual_detail_comparison_selection_reason = str(
                                                post_teacher_tls_connection_repair_reference_promotion_report.get(
                                                    "reason", ""
                                                )
                                            )
                                        if (
                                            post_teacher_tls_connection_repair_movement_rebuild_run_report is None
                                            and _teacher_guided_queue_has_replay_candidates(
                                                post_teacher_tls_connection_repair_movement_rebuild_queue_report
                                            )
                                        ):
                                            (
                                                post_teacher_tls_connection_repair_movement_rebuild_plain_export_report,
                                                post_teacher_tls_connection_repair_movement_rebuild_run_report,
                                                post_teacher_tls_connection_repair_movement_rebuild_best_variant_file,
                                            ) = _run_teacher_guided_queue_replay(
                                                queue_report=post_teacher_tls_connection_repair_movement_rebuild_queue_report,
                                                source_net_file=connection_repair_variant_file,
                                                plain_output_dir=output_dir
                                                / "post_teacher_tls_connection_repair_movement_rebuild_plain",
                                                run_output_dir=output_dir
                                                / "post_teacher_tls_connection_repair_movement_rebuild_execution",
                                                prefix=(
                                                    f"{prefix}_post_teacher_tls_connection_repair_"
                                                    "movement_rebuild"
                                                ),
                                                netconvert_binary=netconvert_binary,
                                                sumo_binary=sumo_binary,
                                                timeout_seconds=timeout_seconds,
                                                max_ready_candidates=teacher_guided_repair_max_ready_candidates,
                                                plain_export_func=teacher_guided_plain_export_func,
                                                repair_run_func=teacher_guided_repair_run_func,
                                            )
                                        if (
                                            post_teacher_tls_connection_repair_movement_rebuild_best_variant_file
                                            is not None
                                        ):
                                            reference_visual_detail_comparison_net_file = (
                                                post_teacher_tls_connection_repair_movement_rebuild_best_variant_file
                                            )
                                            reference_visual_detail_comparison_selection_reason = (
                                                "post_teacher_tls_connection_repair_movement_rebuild_promoted"
                                            )
                                            reference_join_post_teacher_audit_report = reference_join_audit_func(
                                                reference_net_file=reference_net_file,
                                                candidate_net_file=post_teacher_tls_connection_repair_movement_rebuild_best_variant_file,
                                                output_dir=output_dir
                                                / "post_teacher_tls_connection_repair_movement_rebuild_reference_delta",
                                                prefix=(
                                                    f"{prefix}_post_teacher_tls_connection_repair_"
                                                    "movement_rebuild_reference_delta"
                                                ),
                                                candidate_cluster_radius_m=topology_cluster_radius_m,
                                                candidate_min_cluster_nodes=topology_min_cluster_nodes,
                                                structural_only=True,
                                                equivalent_approach_edge_map=_teacher_guided_equivalent_approach_edge_map(
                                                    post_teacher_tls_connection_repair_movement_rebuild_run_report
                                                ),
                                            )
                                    else:
                                        post_teacher_tls_connection_repair_reference_promotion_report = {
                                            "status": "blocked",
                                            "reason": "sumo_load_not_pass",
                                        }
                            non_controller_demotion_baseline_report = reference_join_post_teacher_audit_report
                            non_controller_demotion_source_net_file = (
                                reference_visual_detail_comparison_net_file or teacher_guided_repair_best_variant_file
                            )
                            non_controller_demotion_extra_counts = (
                                non_controller_demotion_baseline_report.get("network_structural_extra_counts", {})
                                if non_controller_demotion_baseline_report is not None
                                else {}
                            )
                            if not isinstance(non_controller_demotion_extra_counts, Mapping):
                                non_controller_demotion_extra_counts = {}
                            extra_traffic_light_junctions = int(
                                non_controller_demotion_extra_counts.get("traffic_light_junction_count", 0) or 0
                            )
                            if (
                                reference_net_file is not None
                                and non_controller_demotion_baseline_report is not None
                                and non_controller_demotion_source_net_file is not None
                                and extra_traffic_light_junctions > 0
                            ):
                                non_controller_demotion_output_dir = (
                                    output_dir / "post_teacher_tls_non_controller_junction_demotion"
                                )
                                post_teacher_tls_non_controller_junction_demotion_report = (
                                    tls_non_controller_junction_demotion_func(
                                        source_net_file=non_controller_demotion_source_net_file,
                                        output_dir=non_controller_demotion_output_dir,
                                        prefix=f"{prefix}_post_teacher_tls_non_controller_junction_demotion",
                                    )
                                )
                                non_controller_demotion_variant_value = (
                                    post_teacher_tls_non_controller_junction_demotion_report.get(
                                        "tls_non_controller_junction_demotion_variant_file", ""
                                    )
                                )
                                non_controller_demotion_variant_file = (
                                    Path(str(non_controller_demotion_variant_value))
                                    if non_controller_demotion_variant_value
                                    else None
                                )
                                if (
                                    non_controller_demotion_variant_file is not None
                                    and non_controller_demotion_variant_file.exists()
                                ):
                                    post_teacher_tls_non_controller_junction_demotion_sumo_load_report = _sumo_load_net(
                                        non_controller_demotion_variant_file,
                                        output_dir=non_controller_demotion_output_dir,
                                        sumo_binary=sumo_binary,
                                        timeout_seconds=timeout_seconds,
                                        command_runner=command_runner,
                                    )
                                    if (
                                        post_teacher_tls_non_controller_junction_demotion_sumo_load_report.get(
                                            "status"
                                        )
                                        == "pass"
                                    ):
                                        non_controller_base_edge_map = _teacher_guided_equivalent_approach_edge_map(
                                            post_teacher_tls_connection_repair_movement_rebuild_run_report
                                            or teacher_guided_repair_run_report
                                        )
                                        post_teacher_tls_non_controller_junction_demotion_reference_delta_report = (
                                            reference_join_audit_func(
                                                reference_net_file=reference_net_file,
                                                candidate_net_file=non_controller_demotion_variant_file,
                                                output_dir=output_dir
                                                / "post_teacher_tls_non_controller_junction_demotion_reference_delta",
                                                prefix=(
                                                    f"{prefix}_post_teacher_tls_non_controller_junction_demotion_"
                                                    "reference_delta"
                                                ),
                                                candidate_cluster_radius_m=topology_cluster_radius_m,
                                                candidate_min_cluster_nodes=topology_min_cluster_nodes,
                                                structural_only=True,
                                                equivalent_approach_edge_map=non_controller_base_edge_map,
                                            )
                                        )
                                        post_teacher_tls_non_controller_junction_demotion_reference_promotion_report = (
                                            _reference_delta_promotion_decision(
                                                candidate_delta_report=(
                                                    post_teacher_tls_non_controller_junction_demotion_reference_delta_report
                                                ),
                                                baseline_delta_report=non_controller_demotion_baseline_report,
                                                reason=(
                                                    "post_teacher_tls_non_controller_junction_demotion_"
                                                    "promoted_by_reference_delta"
                                                ),
                                            )
                                        )
                                        if (
                                            post_teacher_tls_non_controller_junction_demotion_reference_promotion_report.get(
                                                "status"
                                            )
                                            == "pass"
                                        ):
                                            reference_visual_detail_comparison_net_file = (
                                                non_controller_demotion_variant_file
                                            )
                                            reference_visual_detail_comparison_selection_reason = str(
                                                post_teacher_tls_non_controller_junction_demotion_reference_promotion_report.get(
                                                    "reason", ""
                                                )
                                            )
                                            reference_join_post_teacher_audit_report = (
                                                post_teacher_tls_non_controller_junction_demotion_reference_delta_report
                                            )
                                            if (
                                                _int_field(
                                                    post_teacher_tls_non_controller_junction_demotion_reference_delta_report,
                                                    "junction_pattern_mismatch_count",
                                                )
                                                > 0
                                            ):
                                                followup_queue_report = teacher_guided_repair_queue_func(
                                                    teacher_net_file=reference_net_file,
                                                    candidate_net_file=non_controller_demotion_variant_file,
                                                    reference_join_audit_report=(
                                                        post_teacher_tls_non_controller_junction_demotion_reference_delta_report
                                                    ),
                                                    output_dir=output_dir
                                                    / "post_teacher_tls_non_controller_junction_demotion_movement_rebuild_queue",
                                                    prefix=(
                                                        f"{prefix}_post_teacher_tls_non_controller_junction_demotion_"
                                                        "movement_rebuild"
                                                    ),
                                                    max_ready_candidates=teacher_guided_repair_max_ready_candidates,
                                                )
                                                (
                                                    _followup_plain_export_report,
                                                    followup_run_report,
                                                    followup_best_variant_file,
                                                ) = _run_teacher_guided_queue_replay(
                                                    queue_report=followup_queue_report,
                                                    source_net_file=non_controller_demotion_variant_file,
                                                    plain_output_dir=output_dir
                                                    / "post_teacher_tls_non_controller_junction_demotion_movement_rebuild_plain",
                                                    run_output_dir=output_dir
                                                    / "post_teacher_tls_non_controller_junction_demotion_movement_rebuild_execution",
                                                    prefix=(
                                                        f"{prefix}_post_teacher_tls_non_controller_junction_demotion_"
                                                        "movement_rebuild"
                                                    ),
                                                    netconvert_binary=netconvert_binary,
                                                    sumo_binary=sumo_binary,
                                                    timeout_seconds=timeout_seconds,
                                                    max_ready_candidates=teacher_guided_repair_max_ready_candidates,
                                                    plain_export_func=teacher_guided_plain_export_func,
                                                    repair_run_func=teacher_guided_repair_run_func,
                                                )
                                                if followup_best_variant_file is not None:
                                                    followup_demotion_report = tls_non_controller_junction_demotion_func(
                                                        source_net_file=followup_best_variant_file,
                                                        output_dir=output_dir
                                                        / "post_teacher_tls_non_controller_junction_demotion_movement_rebuild_demote",
                                                        prefix=(
                                                            f"{prefix}_post_teacher_tls_non_controller_junction_demotion_"
                                                            "movement_rebuild_demote"
                                                        ),
                                                    )
                                                    followup_demotion_value = followup_demotion_report.get(
                                                        "tls_non_controller_junction_demotion_variant_file", ""
                                                    )
                                                    followup_demotion_file = (
                                                        Path(str(followup_demotion_value))
                                                        if followup_demotion_value
                                                        else None
                                                    )
                                                    if (
                                                        followup_demotion_file is not None
                                                        and followup_demotion_file.exists()
                                                    ):
                                                        followup_sumo_load_report = _sumo_load_net(
                                                            followup_demotion_file,
                                                            output_dir=output_dir
                                                            / "post_teacher_tls_non_controller_junction_demotion_movement_rebuild_demote_sumo_load",
                                                            sumo_binary=sumo_binary,
                                                            timeout_seconds=timeout_seconds,
                                                            command_runner=command_runner,
                                                        )
                                                        if followup_sumo_load_report.get("status") == "pass":
                                                            followup_edge_map = {
                                                                **non_controller_base_edge_map,
                                                                **_teacher_guided_equivalent_approach_edge_map(
                                                                    followup_run_report
                                                                ),
                                                            }
                                                            followup_delta_report = reference_join_audit_func(
                                                                reference_net_file=reference_net_file,
                                                                candidate_net_file=followup_demotion_file,
                                                                output_dir=output_dir
                                                                / "post_teacher_tls_non_controller_junction_demotion_movement_rebuild_reference_delta",
                                                                prefix=(
                                                                    f"{prefix}_post_teacher_tls_non_controller_"
                                                                    "junction_demotion_movement_rebuild_reference_delta"
                                                                ),
                                                                candidate_cluster_radius_m=topology_cluster_radius_m,
                                                                candidate_min_cluster_nodes=topology_min_cluster_nodes,
                                                                structural_only=True,
                                                                equivalent_approach_edge_map=followup_edge_map,
                                                            )
                                                            internal_regression_restore_report = (
                                                                _restore_followup_internal_regressions(
                                                                    baseline_delta_report=(
                                                                        post_teacher_tls_non_controller_junction_demotion_reference_delta_report
                                                                    ),
                                                                    followup_delta_report=followup_delta_report,
                                                                    baseline_net_file=non_controller_demotion_variant_file,
                                                                    followup_net_file=followup_demotion_file,
                                                                    output_dir=output_dir
                                                                    / "post_teacher_tls_non_controller_junction_demotion_movement_rebuild_internal_regression_restore",
                                                                    prefix=(
                                                                        f"{prefix}_post_teacher_tls_non_controller_"
                                                                        "junction_demotion_movement_rebuild"
                                                                    ),
                                                                )
                                                            )
                                                            if internal_regression_restore_report.get("status") == "pass":
                                                                restored_followup_file = Path(
                                                                    str(
                                                                        internal_regression_restore_report.get(
                                                                            "restored_net_file", ""
                                                                        )
                                                                    )
                                                                )
                                                                restored_sumo_load_report = _sumo_load_net(
                                                                    restored_followup_file,
                                                                    output_dir=output_dir
                                                                    / "post_teacher_tls_non_controller_junction_demotion_movement_rebuild_internal_regression_restore_sumo_load",
                                                                    sumo_binary=sumo_binary,
                                                                    timeout_seconds=timeout_seconds,
                                                                    command_runner=command_runner,
                                                                )
                                                                if restored_sumo_load_report.get("status") == "pass":
                                                                    restored_delta_report = reference_join_audit_func(
                                                                        reference_net_file=reference_net_file,
                                                                        candidate_net_file=restored_followup_file,
                                                                        output_dir=output_dir
                                                                        / "post_teacher_tls_non_controller_junction_demotion_movement_rebuild_internal_regression_restore_reference_delta",
                                                                        prefix=(
                                                                            f"{prefix}_post_teacher_tls_non_controller_"
                                                                            "junction_demotion_movement_rebuild_internal_regression_restore_reference_delta"
                                                                        ),
                                                                        candidate_cluster_radius_m=topology_cluster_radius_m,
                                                                        candidate_min_cluster_nodes=topology_min_cluster_nodes,
                                                                        structural_only=True,
                                                                        equivalent_approach_edge_map=followup_edge_map,
                                                                    )
                                                                    restore_promotion_report = _movement_rebuild_reference_delta_promotion_decision(
                                                                        candidate_delta_report=restored_delta_report,
                                                                        baseline_delta_report=followup_delta_report,
                                                                        reason=(
                                                                            "post_teacher_tls_non_controller_junction_demotion_"
                                                                            "movement_rebuild_internal_regressions_restored"
                                                                        ),
                                                                    )
                                                                    if restore_promotion_report.get("status") == "pass":
                                                                        followup_demotion_report = {
                                                                            **followup_demotion_report,
                                                                            "tls_non_controller_junction_demotion_variant_file": str(
                                                                                restored_followup_file
                                                                            ),
                                                                            "internal_regression_restore": (
                                                                                internal_regression_restore_report
                                                                            ),
                                                                            "internal_regression_restore_sumo_load": (
                                                                                restored_sumo_load_report
                                                                            ),
                                                                            "internal_regression_restore_reference_delta": (
                                                                                restored_delta_report
                                                                            ),
                                                                            "internal_regression_restore_promotion": (
                                                                                restore_promotion_report
                                                                            ),
                                                                        }
                                                                        followup_demotion_file = restored_followup_file
                                                                        followup_sumo_load_report = restored_sumo_load_report
                                                                        followup_delta_report = restored_delta_report
                                                            followup_promotion_report = _movement_rebuild_reference_delta_promotion_decision(
                                                                candidate_delta_report=followup_delta_report,
                                                                baseline_delta_report=(
                                                                    post_teacher_tls_non_controller_junction_demotion_reference_delta_report
                                                                ),
                                                                reason=(
                                                                    "post_teacher_tls_non_controller_junction_demotion_"
                                                                    "movement_rebuild_promoted_by_reference_delta"
                                                                ),
                                                            )
                                                            if followup_promotion_report.get("status") == "pass":
                                                                post_teacher_tls_non_controller_junction_demotion_report = (
                                                                    followup_demotion_report
                                                                )
                                                                post_teacher_tls_non_controller_junction_demotion_sumo_load_report = (
                                                                    followup_sumo_load_report
                                                                )
                                                                post_teacher_tls_non_controller_junction_demotion_reference_delta_report = (
                                                                    followup_delta_report
                                                                )
                                                                post_teacher_tls_non_controller_junction_demotion_reference_promotion_report = (
                                                                    followup_promotion_report
                                                                )
                                                                reference_visual_detail_comparison_net_file = (
                                                                    followup_demotion_file
                                                                )
                                                                reference_visual_detail_comparison_selection_reason = str(
                                                                    followup_promotion_report.get("reason", "")
                                                                )
                                                                reference_join_post_teacher_audit_report = (
                                                                    followup_delta_report
                                                                )
                                    else:
                                        post_teacher_tls_non_controller_junction_demotion_reference_promotion_report = {
                                            "status": "blocked",
                                            "reason": "sumo_load_not_pass",
                                        }
            if (
                run_teacher_guided_repair_after_build
                and _teacher_guided_direct_replay_needed(
                    repair_promotion_report=teacher_guided_repair_reference_promotion_report,
                    repair_run_report=teacher_guided_repair_run_report,
                )
                and _teacher_guided_queue_has_replay_candidates(teacher_guided_repair_queue_report)
            ):
                direct_replay_source_net_file = (
                    teacher_guided_replay_source_net_file
                    or reference_visual_detail_comparison_net_file
                    or reference_join_audit_candidate_net_file
                )
                if direct_replay_source_net_file is not None:
                    direct_replay_candidates = [
                        dict(candidate)
                        for candidate in teacher_guided_repair_queue_report.get("repair_candidates", []) or []
                        if isinstance(candidate, Mapping)
                        and candidate.get("candidate_status") == "ready_for_teacher_guided_variant"
                        and str(candidate.get("tls_candidate_tl_id", "")).strip()
                        not in teacher_guided_scoped_tls_batch_pass_candidate_ids
                    ]
                    if not direct_replay_candidates:
                        direct_replay_candidates = [
                            dict(candidate)
                            for candidate in teacher_guided_repair_queue_report.get("repair_candidates", []) or []
                            if isinstance(candidate, Mapping)
                            and str(candidate.get("tls_candidate_tl_id", "")).strip()
                            not in teacher_guided_scoped_tls_batch_pass_candidate_ids
                        ]
                    for direct_index, candidate in enumerate(direct_replay_candidates, start=1):
                        trial_queue_report = dict(teacher_guided_repair_queue_report)
                        trial_queue_report["repair_candidates"] = [candidate]
                        trial_queue_report["repair_candidate_count"] = 1
                        trial_queue_report["ready_candidate_count"] = (
                            1 if candidate.get("candidate_status") == "ready_for_teacher_guided_variant" else 0
                        )
                        trial_queue_report["expanded_scope_candidate_count"] = (
                            1 if candidate.get("candidate_status") == "needs_expanded_rebuild_scope" else 0
                        )
                        teacher_guided_direct_replay_report = teacher_guided_direct_replay_func(
                            queue_report=trial_queue_report,
                            source_net_file=direct_replay_source_net_file,
                            output_dir=output_dir / "teacher_guided_direct_replay" / f"attempt_{direct_index:03d}",
                            prefix=f"{prefix}_teacher_guided_direct_replay_{direct_index:03d}",
                            netconvert_binary=netconvert_binary,
                            sumo_binary=sumo_binary,
                            timeout_seconds=timeout_seconds,
                            command_runner=command_runner,
                        )
                        direct_variant_value = str(teacher_guided_direct_replay_report.get("variant_file", ""))
                        direct_variant_file = Path(direct_variant_value) if direct_variant_value else None
                        if direct_variant_file is None or not direct_variant_file.exists():
                            continue
                        teacher_guided_direct_replay_reference_delta_report = reference_join_audit_func(
                            reference_net_file=reference_net_file,
                            candidate_net_file=direct_variant_file,
                            output_dir=(
                                output_dir
                                / "teacher_guided_direct_replay_reference_delta"
                                / f"attempt_{direct_index:03d}"
                            ),
                            prefix=f"{prefix}_teacher_guided_direct_replay_reference_delta_{direct_index:03d}",
                            candidate_cluster_radius_m=topology_cluster_radius_m,
                            candidate_min_cluster_nodes=topology_min_cluster_nodes,
                            structural_only=teacher_guided_seed_structural_only,
                        )
                        teacher_guided_direct_replay_reference_promotion_report = (
                            _movement_rebuild_reference_delta_promotion_decision(
                                candidate_delta_report=teacher_guided_direct_replay_reference_delta_report,
                                baseline_delta_report=teacher_guided_seed_report,
                                structural_guard_delta_report=teacher_guided_seed_report,
                                reason="direct_local_teacher_replay_promoted_by_reference_delta",
                            )
                        )
                        if teacher_guided_direct_replay_reference_promotion_report.get("status") == "pass":
                            teacher_guided_direct_replay_best_variant_file = direct_variant_file
                            reference_visual_detail_comparison_net_file = direct_variant_file
                            reference_visual_detail_comparison_selection_reason = str(
                                teacher_guided_direct_replay_reference_promotion_report.get("reason", "")
                            )
                            reference_join_post_teacher_audit_report = (
                                teacher_guided_direct_replay_reference_delta_report
                            )
                            break
        if road_connectivity_replay_report is None and road_connectivity_seed_edge_ids:
            (
                road_connectivity_replay_report,
                road_connectivity_seed_probe_report,
                road_connectivity_split_root_alias_repair_report,
                road_connection_topology_replay_report,
            ) = _run_road_connectivity_replay_sequence(
                teacher_net_file=reference_net_file,
                candidate_net_file=reference_visual_detail_comparison_net_file or reference_join_audit_candidate_net_file,
                queue_report=None,
                seed_probe_report=road_connectivity_seed_probe_report,
                seed_edge_ids=road_connectivity_seed_edge_ids,
                output_dir=output_dir,
                prefix=prefix,
                max_owner_count=road_connectivity_replay_max_owners,
                sumo_binary=sumo_binary,
                timeout_seconds=timeout_seconds,
                command_runner=command_runner,
                road_connectivity_replay_func=road_connectivity_replay_func,
                road_connectivity_seed_probe_func=road_connectivity_seed_probe_func,
                road_connection_topology_replay_func=road_connection_topology_replay_func,
            )
    return {
        'direct_variant_file': direct_variant_file,
        'post_teacher_tls_connection_repair_movement_rebuild_best_variant_file': post_teacher_tls_connection_repair_movement_rebuild_best_variant_file,
        'post_teacher_tls_connection_repair_movement_rebuild_plain_export_report': post_teacher_tls_connection_repair_movement_rebuild_plain_export_report,
        'post_teacher_tls_connection_repair_movement_rebuild_queue_report': post_teacher_tls_connection_repair_movement_rebuild_queue_report,
        'post_teacher_tls_connection_repair_movement_rebuild_run_report': post_teacher_tls_connection_repair_movement_rebuild_run_report,
        'post_teacher_tls_connection_repair_reference_delta_report': post_teacher_tls_connection_repair_reference_delta_report,
        'post_teacher_tls_connection_repair_reference_promotion_report': post_teacher_tls_connection_repair_reference_promotion_report,
        'post_teacher_tls_connection_repair_report': post_teacher_tls_connection_repair_report,
        'post_teacher_tls_connection_repair_sumo_load_report': post_teacher_tls_connection_repair_sumo_load_report,
        'post_teacher_tls_low_vehicle_control_reference_delta_report': post_teacher_tls_low_vehicle_control_reference_delta_report,
        'post_teacher_tls_low_vehicle_control_reference_promotion_report': post_teacher_tls_low_vehicle_control_reference_promotion_report,
        'post_teacher_tls_low_vehicle_control_report': post_teacher_tls_low_vehicle_control_report,
        'post_teacher_tls_low_vehicle_control_sumo_load_report': post_teacher_tls_low_vehicle_control_sumo_load_report,
        'post_teacher_tls_non_controller_junction_demotion_reference_delta_report': post_teacher_tls_non_controller_junction_demotion_reference_delta_report,
        'post_teacher_tls_non_controller_junction_demotion_reference_promotion_report': post_teacher_tls_non_controller_junction_demotion_reference_promotion_report,
        'post_teacher_tls_non_controller_junction_demotion_report': post_teacher_tls_non_controller_junction_demotion_report,
        'post_teacher_tls_non_controller_junction_demotion_sumo_load_report': post_teacher_tls_non_controller_junction_demotion_sumo_load_report,
        'post_teacher_tls_signal_grouping_reference_delta_report': post_teacher_tls_signal_grouping_reference_delta_report,
        'post_teacher_tls_signal_grouping_reference_promotion_report': post_teacher_tls_signal_grouping_reference_promotion_report,
        'post_teacher_tls_signal_grouping_report': post_teacher_tls_signal_grouping_report,
        'post_teacher_tls_signal_grouping_sumo_load_report': post_teacher_tls_signal_grouping_sumo_load_report,
        'reference_join_aggregation_report': reference_join_aggregation_report,
        'reference_join_audit_candidate_layer': reference_join_audit_candidate_layer,
        'reference_join_audit_candidate_net_file': reference_join_audit_candidate_net_file,
        'reference_join_audit_report': reference_join_audit_report,
        'reference_join_post_teacher_audit_report': reference_join_post_teacher_audit_report,
        'reference_visual_detail_comparison_net_file': reference_visual_detail_comparison_net_file,
        'reference_visual_detail_comparison_selection_reason': reference_visual_detail_comparison_selection_reason,
        'road_connection_topology_replay_report': road_connection_topology_replay_report,
        'road_connectivity_replay_report': road_connectivity_replay_report,
        'road_connectivity_seed_probe_report': road_connectivity_seed_probe_report,
        'road_connectivity_split_root_alias_repair_report': road_connectivity_split_root_alias_repair_report,
        'teacher_guided_direct_replay_best_variant_file': teacher_guided_direct_replay_best_variant_file,
        'teacher_guided_direct_replay_reference_delta_report': teacher_guided_direct_replay_reference_delta_report,
        'teacher_guided_direct_replay_reference_promotion_report': teacher_guided_direct_replay_reference_promotion_report,
        'teacher_guided_direct_replay_report': teacher_guided_direct_replay_report,
        'teacher_guided_plain_export_report': teacher_guided_plain_export_report,
        'teacher_guided_probe_matrix_report': teacher_guided_probe_matrix_report,
        'teacher_guided_repair_best_expanded_scope_net_file': teacher_guided_repair_best_expanded_scope_net_file,
        'teacher_guided_repair_best_variant_file': teacher_guided_repair_best_variant_file,
        'teacher_guided_repair_queue_report': teacher_guided_repair_queue_report,
        'teacher_guided_repair_reference_promotion_report': teacher_guided_repair_reference_promotion_report,
        'teacher_guided_repair_requires_reference_promotion': teacher_guided_repair_requires_reference_promotion,
        'teacher_guided_repair_run_report': teacher_guided_repair_run_report,
        'teacher_guided_repair_seed_source': teacher_guided_repair_seed_source,
        'teacher_guided_scoped_tls_cell_batch_report': teacher_guided_scoped_tls_cell_batch_report,
        'teacher_guided_seed_report': teacher_guided_seed_report,
        'tls_gap_destination_mapping_report': tls_gap_destination_mapping_report,
        'tls_repair_decision_report': tls_repair_decision_report,
        'tls_repair_variant_reference_audit_report': tls_repair_variant_reference_audit_report,
        'tls_repair_variant_report': tls_repair_variant_report,
        'tls_repair_variant_semantic_report': tls_repair_variant_semantic_report,
        'tls_repair_variant_sumo_load_report': tls_repair_variant_sumo_load_report,
    }


def _workflow_teacher_guided_repair_section(
    *,
    command_runner: Callable[..., Any],
    netconvert_binary: str,
    output_dir: Path,
    prefix: str,
    reference_join_audit_func: Callable[..., dict[str, Any]],
    reference_join_audit_structural_only: bool,
    reference_net_file: Path | None,
    run_teacher_guided_repair_after_build: bool,
    sumo_binary: str,
    teacher_guided_direct_replay_func: Callable[..., dict[str, Any]],
    teacher_guided_plain_export_func: Callable[..., dict[str, Any]],
    teacher_guided_repair_max_ready_candidates: int | None,
    teacher_guided_repair_queue_func: Callable[..., dict[str, Any]],
    teacher_guided_repair_requires_reference_promotion: Any,
    teacher_guided_repair_run_func: Callable[..., dict[str, Any]],
    teacher_guided_seed_report: dict[str, Any] | None,
    timeout_seconds: float,
    topology_cluster_radius_m: float,
    topology_min_cluster_nodes: int,
    final_movement_rebuild_best_variant_file: Path | None,
    final_movement_rebuild_internal_regression_restore_promotion_report: dict[str, Any] | None,
    final_movement_rebuild_internal_regression_restore_reference_delta_report: dict[str, Any] | None,
    final_movement_rebuild_internal_regression_restore_report: dict[str, Any] | None,
    final_movement_rebuild_internal_regression_restore_sumo_load_report: dict[str, Any] | None,
    final_movement_rebuild_queue_report: dict[str, Any] | None,
    final_movement_rebuild_reference_delta_report: dict[str, Any] | None,
    final_movement_rebuild_reference_promotion_report: dict[str, Any],
    final_movement_rebuild_run_report: dict[str, Any] | None,
    final_movement_rebuild_sumo_load_report: dict[str, Any] | None,
    reference_join_post_teacher_audit_report: dict[str, Any] | None,
    reference_visual_detail_comparison_net_file: Path | None,
) -> dict[str, Any]:
    final_movement_direct_replay_best_variant_file = _WORKFLOW_UNSET
    final_movement_direct_replay_last_queue_report = _WORKFLOW_UNSET
    final_movement_direct_replay_reference_delta_report = _WORKFLOW_UNSET
    final_movement_direct_replay_reference_promotion_report = _WORKFLOW_UNSET
    final_movement_direct_replay_report = _WORKFLOW_UNSET
    final_movement_rebuild_plain_export_report = _WORKFLOW_UNSET
    reference_visual_detail_comparison_selection_reason = _WORKFLOW_UNSET
    current_direct_baseline_report = _WORKFLOW_UNSET
    current_direct_queue_report = _WORKFLOW_UNSET
    current_direct_source_net_file = _WORKFLOW_UNSET
    direct_delta_report = _WORKFLOW_UNSET
    direct_promotion_report = _WORKFLOW_UNSET
    direct_replay_report = _WORKFLOW_UNSET
    direct_variant_file = _WORKFLOW_UNSET
    final_movement_baseline_report = _WORKFLOW_UNSET
    final_movement_candidate_delta_report = _WORKFLOW_UNSET
    final_movement_candidate_file = _WORKFLOW_UNSET
    final_movement_edge_map = _WORKFLOW_UNSET
    final_movement_source_net_file = _WORKFLOW_UNSET
    iteration_label = _WORKFLOW_UNSET
    iteration_number = _WORKFLOW_UNSET
    max_final_direct_replay_iterations = _WORKFLOW_UNSET
    next_iteration_number = _WORKFLOW_UNSET
    restored_final_movement_file = _WORKFLOW_UNSET
    if (
        run_teacher_guided_repair_after_build
        and reference_net_file is not None
        and reference_visual_detail_comparison_net_file is not None
        and reference_join_post_teacher_audit_report is not None
        and _movement_rebuild_mismatch_score(reference_join_post_teacher_audit_report) > 0
    ):
        final_movement_source_net_file = reference_visual_detail_comparison_net_file
        final_movement_baseline_report = reference_join_post_teacher_audit_report
        final_movement_rebuild_queue_report = teacher_guided_repair_queue_func(
            teacher_net_file=reference_net_file,
            candidate_net_file=final_movement_source_net_file,
            reference_join_audit_report=final_movement_baseline_report,
            output_dir=output_dir / "final_movement_rebuild_queue",
            prefix=f"{prefix}_final_movement_rebuild",
            max_ready_candidates=teacher_guided_repair_max_ready_candidates,
        )
        final_movement_rebuild_queue_report = _filter_teacher_guided_queue_to_mismatch_fields(
            final_movement_rebuild_queue_report,
            final_movement_baseline_report,
            {"movement_signature_counts", "internal_function_counts"},
            output_dir=output_dir / "final_movement_rebuild_queue",
            prefix=f"{prefix}_final_movement_rebuild_movement_mismatches",
        )
        if _teacher_guided_queue_has_replay_candidates(final_movement_rebuild_queue_report):
            (
                final_movement_rebuild_plain_export_report,
                final_movement_rebuild_run_report,
                final_movement_rebuild_best_variant_file,
            ) = _run_teacher_guided_queue_replay(
                queue_report=final_movement_rebuild_queue_report,
                source_net_file=final_movement_source_net_file,
                plain_output_dir=output_dir / "final_movement_rebuild_plain",
                run_output_dir=output_dir / "final_movement_rebuild_execution",
                prefix=f"{prefix}_final_movement_rebuild",
                netconvert_binary=netconvert_binary,
                sumo_binary=sumo_binary,
                timeout_seconds=timeout_seconds,
                max_ready_candidates=teacher_guided_repair_max_ready_candidates,
                plain_export_func=teacher_guided_plain_export_func,
                repair_run_func=teacher_guided_repair_run_func,
            )
        if final_movement_rebuild_best_variant_file is not None:
            final_movement_rebuild_sumo_load_report = _sumo_load_net(
                final_movement_rebuild_best_variant_file,
                output_dir=output_dir / "final_movement_rebuild_sumo_load",
                sumo_binary=sumo_binary,
                timeout_seconds=timeout_seconds,
                command_runner=command_runner,
            )
            if final_movement_rebuild_sumo_load_report.get("status") == "pass":
                final_movement_edge_map = _teacher_guided_equivalent_approach_edge_map(
                    final_movement_rebuild_run_report
                )
                final_movement_rebuild_reference_delta_report = reference_join_audit_func(
                    reference_net_file=reference_net_file,
                    candidate_net_file=final_movement_rebuild_best_variant_file,
                    output_dir=output_dir / "final_movement_rebuild_reference_delta",
                    prefix=f"{prefix}_final_movement_rebuild_reference_delta",
                    candidate_cluster_radius_m=topology_cluster_radius_m,
                    candidate_min_cluster_nodes=topology_min_cluster_nodes,
                    structural_only=_followup_reference_delta_structural_only(
                        final_movement_baseline_report,
                        default=reference_join_audit_structural_only,
                    ),
                    equivalent_approach_edge_map=final_movement_edge_map,
                )
                final_movement_candidate_file = final_movement_rebuild_best_variant_file
                final_movement_candidate_delta_report = final_movement_rebuild_reference_delta_report
                final_movement_rebuild_internal_regression_restore_report = _restore_followup_internal_regressions(
                    baseline_delta_report=final_movement_baseline_report,
                    followup_delta_report=final_movement_rebuild_reference_delta_report,
                    baseline_net_file=final_movement_source_net_file,
                    followup_net_file=final_movement_rebuild_best_variant_file,
                    output_dir=output_dir / "final_movement_rebuild_internal_regression_restore",
                    prefix=f"{prefix}_final_movement_rebuild",
                )
                if final_movement_rebuild_internal_regression_restore_report.get("status") == "pass":
                    restored_final_movement_file = Path(
                        str(final_movement_rebuild_internal_regression_restore_report.get("restored_net_file", ""))
                    )
                    final_movement_rebuild_internal_regression_restore_sumo_load_report = _sumo_load_net(
                        restored_final_movement_file,
                        output_dir=output_dir / "final_movement_rebuild_internal_regression_restore_sumo_load",
                        sumo_binary=sumo_binary,
                        timeout_seconds=timeout_seconds,
                        command_runner=command_runner,
                    )
                    if final_movement_rebuild_internal_regression_restore_sumo_load_report.get("status") == "pass":
                        final_movement_rebuild_internal_regression_restore_reference_delta_report = (
                            reference_join_audit_func(
                                reference_net_file=reference_net_file,
                                candidate_net_file=restored_final_movement_file,
                                output_dir=output_dir
                                / "final_movement_rebuild_internal_regression_restore_reference_delta",
                                prefix=(
                                    f"{prefix}_final_movement_rebuild_internal_regression_restore_"
                                    "reference_delta"
                                ),
                                candidate_cluster_radius_m=topology_cluster_radius_m,
                                candidate_min_cluster_nodes=topology_min_cluster_nodes,
                                structural_only=_followup_reference_delta_structural_only(
                                    final_movement_rebuild_reference_delta_report,
                                    default=reference_join_audit_structural_only,
                                ),
                                equivalent_approach_edge_map=final_movement_edge_map,
                            )
                        )
                        final_movement_rebuild_internal_regression_restore_promotion_report = (
                            _movement_rebuild_reference_delta_promotion_decision(
                                candidate_delta_report=(
                                    final_movement_rebuild_internal_regression_restore_reference_delta_report
                                ),
                                baseline_delta_report=final_movement_rebuild_reference_delta_report,
                                reason="final_movement_rebuild_internal_regressions_restored",
                            )
                        )
                        if final_movement_rebuild_internal_regression_restore_promotion_report.get("status") == "pass":
                            final_movement_candidate_file = restored_final_movement_file
                            final_movement_candidate_delta_report = (
                                final_movement_rebuild_internal_regression_restore_reference_delta_report
                            )
                final_movement_rebuild_reference_promotion_report = (
                    _movement_rebuild_reference_delta_promotion_decision(
                        candidate_delta_report=final_movement_candidate_delta_report,
                        baseline_delta_report=final_movement_baseline_report,
                        structural_guard_delta_report=(
                            teacher_guided_seed_report if teacher_guided_repair_requires_reference_promotion else None
                        ),
                        reason="final_movement_rebuild_promoted_by_reference_delta",
                    )
                )
                if final_movement_rebuild_reference_promotion_report.get("status") == "pass":
                    reference_visual_detail_comparison_net_file = final_movement_candidate_file
                    reference_visual_detail_comparison_selection_reason = str(
                        final_movement_rebuild_reference_promotion_report.get("reason", "")
                    )
                    reference_join_post_teacher_audit_report = final_movement_candidate_delta_report
            else:
                final_movement_rebuild_reference_promotion_report = {
                    "status": "blocked",
                    "reason": "sumo_load_not_pass",
                }

        if (
            final_movement_rebuild_reference_promotion_report.get("status") != "pass"
            and _teacher_guided_queue_has_replay_candidates(final_movement_rebuild_queue_report)
        ):
            current_direct_queue_report = final_movement_rebuild_queue_report
            final_movement_direct_replay_last_queue_report = current_direct_queue_report
            current_direct_source_net_file = final_movement_source_net_file
            current_direct_baseline_report = final_movement_baseline_report
            max_final_direct_replay_iterations = 4
            for iteration_number in range(1, max_final_direct_replay_iterations + 1):
                if not _teacher_guided_queue_has_replay_candidates(current_direct_queue_report):
                    break
                iteration_label = f"iteration_{iteration_number:03d}"
                (
                    direct_variant_file,
                    direct_delta_report,
                    direct_promotion_report,
                    direct_replay_report,
                ) = run_final_direct_replay_candidates(
                    current_direct_queue_report,
                    source_net_file=current_direct_source_net_file,
                    baseline_delta_report=current_direct_baseline_report,
                    iteration_label=iteration_label,
                    output_dir=output_dir,
                    prefix=prefix,
                    netconvert_binary=netconvert_binary,
                    sumo_binary=sumo_binary,
                    timeout_seconds=timeout_seconds,
                    command_runner=command_runner,
                    teacher_guided_direct_replay_func=teacher_guided_direct_replay_func,
                    reference_join_audit_func=reference_join_audit_func,
                    reference_join_audit_structural_only=reference_join_audit_structural_only,
                    reference_net_file=reference_net_file,
                    topology_cluster_radius_m=topology_cluster_radius_m,
                    topology_min_cluster_nodes=topology_min_cluster_nodes,
                    teacher_guided_seed_report=teacher_guided_seed_report,
                    teacher_guided_repair_requires_reference_promotion=teacher_guided_repair_requires_reference_promotion,
                )
                if direct_replay_report is not None:
                    final_movement_direct_replay_report = direct_replay_report
                if direct_delta_report is not None:
                    final_movement_direct_replay_reference_delta_report = direct_delta_report
                final_movement_direct_replay_reference_promotion_report = direct_promotion_report
                if direct_variant_file is None or direct_promotion_report.get("status") != "pass":
                    break
                final_movement_direct_replay_best_variant_file = direct_variant_file
                final_movement_rebuild_best_variant_file = direct_variant_file
                final_movement_rebuild_reference_delta_report = direct_delta_report
                final_movement_rebuild_reference_promotion_report = direct_promotion_report
                reference_visual_detail_comparison_net_file = direct_variant_file
                reference_visual_detail_comparison_selection_reason = str(direct_promotion_report.get("reason", ""))
                reference_join_post_teacher_audit_report = direct_delta_report
                if direct_delta_report is None or _movement_rebuild_mismatch_score(direct_delta_report) <= 0:
                    break
                next_iteration_number = iteration_number + 1
                current_direct_queue_report = teacher_guided_repair_queue_func(
                    teacher_net_file=reference_net_file,
                    candidate_net_file=direct_variant_file,
                    reference_join_audit_report=direct_delta_report,
                    output_dir=output_dir / f"final_movement_rebuild_queue_iteration_{next_iteration_number:03d}",
                    prefix=f"{prefix}_final_movement_rebuild_iteration_{next_iteration_number:03d}",
                    max_ready_candidates=teacher_guided_repair_max_ready_candidates,
                )
                current_direct_queue_report = _filter_teacher_guided_queue_to_mismatch_fields(
                    current_direct_queue_report,
                    direct_delta_report,
                    {"movement_signature_counts", "internal_function_counts"},
                    output_dir=output_dir / f"final_movement_rebuild_queue_iteration_{next_iteration_number:03d}",
                    prefix=(
                        f"{prefix}_final_movement_rebuild_iteration_{next_iteration_number:03d}_"
                        "movement_mismatches"
                    ),
                )
                final_movement_direct_replay_last_queue_report = current_direct_queue_report
                current_direct_source_net_file = direct_variant_file
                current_direct_baseline_report = direct_delta_report
    return {
        'final_movement_direct_replay_best_variant_file': final_movement_direct_replay_best_variant_file,
        'final_movement_direct_replay_last_queue_report': final_movement_direct_replay_last_queue_report,
        'final_movement_direct_replay_reference_delta_report': final_movement_direct_replay_reference_delta_report,
        'final_movement_direct_replay_reference_promotion_report': final_movement_direct_replay_reference_promotion_report,
        'final_movement_direct_replay_report': final_movement_direct_replay_report,
        'final_movement_rebuild_best_variant_file': final_movement_rebuild_best_variant_file,
        'final_movement_rebuild_internal_regression_restore_promotion_report': final_movement_rebuild_internal_regression_restore_promotion_report,
        'final_movement_rebuild_internal_regression_restore_reference_delta_report': final_movement_rebuild_internal_regression_restore_reference_delta_report,
        'final_movement_rebuild_internal_regression_restore_report': final_movement_rebuild_internal_regression_restore_report,
        'final_movement_rebuild_internal_regression_restore_sumo_load_report': final_movement_rebuild_internal_regression_restore_sumo_load_report,
        'final_movement_rebuild_plain_export_report': final_movement_rebuild_plain_export_report,
        'final_movement_rebuild_queue_report': final_movement_rebuild_queue_report,
        'final_movement_rebuild_reference_delta_report': final_movement_rebuild_reference_delta_report,
        'final_movement_rebuild_reference_promotion_report': final_movement_rebuild_reference_promotion_report,
        'final_movement_rebuild_run_report': final_movement_rebuild_run_report,
        'final_movement_rebuild_sumo_load_report': final_movement_rebuild_sumo_load_report,
        'reference_join_post_teacher_audit_report': reference_join_post_teacher_audit_report,
        'reference_visual_detail_comparison_net_file': reference_visual_detail_comparison_net_file,
        'reference_visual_detail_comparison_selection_reason': reference_visual_detail_comparison_selection_reason,
    }


def _workflow_reference_comparison_section(
    *,
    command_runner: Callable[..., Any],
    corridor_geometry_simplification_func: Callable[..., dict[str, Any]],
    network_plan: Any,
    osm_file: Any,
    output_dir: Path,
    prefix: str,
    reference_hierarchy_audit_func: Callable[..., dict[str, Any]],
    reference_hierarchy_type_repair_func: Callable[..., dict[str, Any]],
    reference_join_audit_func: Callable[..., dict[str, Any]],
    reference_join_post_teacher_audit_report: dict[str, Any] | None,
    reference_net_file: Path | None,
    reference_scope_audit_func: Callable[..., dict[str, Any]],
    reference_visual_detail_raw_reference_delta_report: dict[str, Any] | None,
    reference_visual_detail_tls_connection_repair_reference_delta_report: dict[str, Any] | None,
    run_corridor_geometry_simplification_after_build: bool,
    run_reference_hierarchy_audit_after_build: bool,
    run_reference_hierarchy_type_repair_after_build: bool,
    run_topology_audit_after_build: bool,
    sumo_binary: str,
    timeout_seconds: float,
    topology_audit_func: Callable[..., dict[str, Any]],
    topology_cluster_radius_m: float,
    topology_min_cluster_nodes: int,
    corridor_geometry_simplification_promotion_report: dict[str, Any],
    corridor_geometry_simplification_reference_delta_report: dict[str, Any] | None,
    corridor_geometry_simplification_report: dict[str, Any] | None,
    corridor_geometry_simplification_sumo_load_report: dict[str, Any] | None,
    corridor_geometry_simplification_topology_report: dict[str, Any] | None,
    reference_hierarchy_audit_candidate_net_file: Path | None,
    reference_hierarchy_audit_report: dict[str, Any] | None,
    reference_hierarchy_type_repair_audit_report: dict[str, Any] | None,
    reference_hierarchy_type_repair_promotion_report: dict[str, Any],
    reference_hierarchy_type_repair_report: dict[str, Any] | None,
    reference_hierarchy_type_repair_sumo_load_report: dict[str, Any] | None,
    reference_visual_detail_comparison_net_file: Path | None,
    topology_audit_report: Any,
) -> dict[str, Any]:
    reference_hierarchy_audit_candidate_layer = _WORKFLOW_UNSET
    reference_scope_audit_report = _WORKFLOW_UNSET
    reference_scope_candidate_net_file = _WORKFLOW_UNSET
    reference_topology_audit_report = _WORKFLOW_UNSET
    reference_visual_detail_comparison_selection_reason = _WORKFLOW_UNSET
    corridor_baseline_delta_report = _WORKFLOW_UNSET
    corridor_variant_file = _WORKFLOW_UNSET
    corridor_variant_value = _WORKFLOW_UNSET
    type_repair_variant_file = _WORKFLOW_UNSET
    type_repair_variant_value = _WORKFLOW_UNSET
    if reference_visual_detail_comparison_net_file is not None and reference_visual_detail_comparison_net_file.exists():
        if (
            run_topology_audit_after_build
            and str(network_plan.get("network_profile", "")) == "reference_matched"
            and reference_net_file is not None
        ):
            reference_topology_audit_report = topology_audit_func(
                net_file=reference_net_file,
                output_dir=output_dir / "reference_topology_audit",
                prefix=f"{prefix}_reference_topology_audit",
                cluster_radius_m=topology_cluster_radius_m,
                min_cluster_nodes=topology_min_cluster_nodes,
                osm_file=osm_file,
            )
        if run_topology_audit_after_build and not _same_path_value(
            None if topology_audit_report is None else topology_audit_report.get("net_file", ""),
            reference_visual_detail_comparison_net_file,
        ):
            topology_audit_report = topology_audit_func(
                net_file=reference_visual_detail_comparison_net_file,
                output_dir=output_dir / "final_topology_audit",
                prefix=f"{prefix}_final_topology_audit",
                cluster_radius_m=topology_cluster_radius_m,
                min_cluster_nodes=topology_min_cluster_nodes,
                osm_file=osm_file,
            )
        if (
            str(network_plan.get("network_profile", "")) == "reference_matched"
            and reference_net_file is not None
            and run_reference_hierarchy_audit_after_build
            and not _same_path_value(reference_hierarchy_audit_candidate_net_file, reference_visual_detail_comparison_net_file)
        ):
            reference_hierarchy_audit_candidate_net_file = reference_visual_detail_comparison_net_file
            reference_hierarchy_audit_candidate_layer = "reference_visual_detail"
            reference_hierarchy_audit_report = reference_hierarchy_audit_func(
                reference_net_file=reference_net_file,
                candidate_net_file=reference_hierarchy_audit_candidate_net_file,
                output_dir=output_dir / "final_reference_hierarchy_audit",
                prefix=f"{prefix}_final_reference_hierarchy_audit",
                resolve_equivalent_fragmentation=True,
            )
        if (
            str(network_plan.get("network_profile", "")) == "reference_matched"
            and reference_net_file is not None
            and run_reference_hierarchy_audit_after_build
            and run_reference_hierarchy_type_repair_after_build
            and reference_hierarchy_audit_report is not None
            and _int_field(reference_hierarchy_audit_report, "high_hierarchy_issue_count") > 0
        ):
            reference_hierarchy_type_repair_report = reference_hierarchy_type_repair_func(
                candidate_net_file=reference_visual_detail_comparison_net_file,
                reference_hierarchy_report=reference_hierarchy_audit_report,
                output_dir=output_dir / "reference_hierarchy_type_repair",
                prefix=f"{prefix}_reference_hierarchy_type_repair",
            )
            type_repair_variant_value = reference_hierarchy_type_repair_report.get(
                "reference_hierarchy_type_repair_variant_file", ""
            )
            type_repair_variant_file = Path(str(type_repair_variant_value)) if type_repair_variant_value else None
            if (
                reference_hierarchy_type_repair_report.get("status") == "pass"
                and type_repair_variant_file is not None
                and type_repair_variant_file.exists()
            ):
                reference_hierarchy_type_repair_sumo_load_report = _sumo_load_net(
                    type_repair_variant_file,
                    output_dir=output_dir / "reference_hierarchy_type_repair_sumo_load",
                    sumo_binary=sumo_binary,
                    timeout_seconds=timeout_seconds,
                    command_runner=command_runner,
                )
                if reference_hierarchy_type_repair_sumo_load_report.get("status") == "pass":
                    reference_hierarchy_type_repair_audit_report = reference_hierarchy_audit_func(
                        reference_net_file=reference_net_file,
                        candidate_net_file=type_repair_variant_file,
                        output_dir=output_dir / "reference_hierarchy_type_repair_audit",
                        prefix=f"{prefix}_reference_hierarchy_type_repair_audit",
                        resolve_equivalent_fragmentation=True,
                    )
                    reference_hierarchy_type_repair_promotion_report = (
                        _reference_hierarchy_type_repair_promotion_decision(
                            baseline_audit_report=reference_hierarchy_audit_report,
                            candidate_audit_report=reference_hierarchy_type_repair_audit_report,
                            sumo_load_report=reference_hierarchy_type_repair_sumo_load_report,
                        )
                    )
                    if reference_hierarchy_type_repair_promotion_report.get("status") == "pass":
                        reference_visual_detail_comparison_net_file = type_repair_variant_file
                        reference_visual_detail_comparison_selection_reason = "reference_hierarchy_type_repair_promoted"
                        reference_hierarchy_audit_report = reference_hierarchy_type_repair_audit_report
                        reference_hierarchy_audit_candidate_net_file = type_repair_variant_file
                        reference_hierarchy_audit_candidate_layer = "reference_visual_detail"
                        if run_topology_audit_after_build:
                            topology_audit_report = topology_audit_func(
                                net_file=reference_visual_detail_comparison_net_file,
                                output_dir=output_dir / "reference_hierarchy_type_repair_topology_audit",
                                prefix=f"{prefix}_reference_hierarchy_type_repair_topology_audit",
                                cluster_radius_m=topology_cluster_radius_m,
                                min_cluster_nodes=topology_min_cluster_nodes,
                                osm_file=osm_file,
                            )
                else:
                    reference_hierarchy_type_repair_promotion_report = {
                        "status": "blocked",
                        "reason": "sumo_load_not_pass",
                    }
            elif reference_hierarchy_type_repair_report.get("reference_hierarchy_type_repair_status") == "not_needed":
                reference_hierarchy_type_repair_promotion_report = {
                    "status": "skipped",
                    "reason": "not_needed",
                }
            else:
                reference_hierarchy_type_repair_promotion_report = {
                    "status": "blocked",
                    "reason": "type_repair_variant_not_created",
                }

        if (
            run_corridor_geometry_simplification_after_build
            and reference_visual_detail_comparison_net_file is not None
        ):
            corridor_geometry_simplification_report = corridor_geometry_simplification_func(
                net_file=reference_visual_detail_comparison_net_file,
                reference_net_file=reference_net_file,
                output_dir=output_dir / "corridor_geometry_simplification",
                prefix=f"{prefix}_corridor_geometry_simplification",
                timeout_seconds=timeout_seconds,
            )
            corridor_variant_value = corridor_geometry_simplification_report.get("variant_file", "")
            corridor_variant_file = Path(str(corridor_variant_value)) if corridor_variant_value else None
            if (
                corridor_geometry_simplification_report.get("status") == "pass"
                and corridor_variant_file is not None
                and corridor_variant_file.exists()
            ):
                corridor_geometry_simplification_sumo_load_report = _sumo_load_net(
                    corridor_variant_file,
                    output_dir=output_dir / "corridor_geometry_simplification_sumo_load",
                    sumo_binary=sumo_binary,
                    timeout_seconds=timeout_seconds,
                    command_runner=command_runner,
                )
                corridor_geometry_simplification_reference_delta_report = reference_join_audit_func(
                    reference_net_file=reference_net_file,
                    candidate_net_file=corridor_variant_file,
                    output_dir=output_dir / "corridor_geometry_simplification_reference_delta",
                    prefix=f"{prefix}_corridor_geometry_simplification_reference_delta",
                    candidate_cluster_radius_m=topology_cluster_radius_m,
                    candidate_min_cluster_nodes=topology_min_cluster_nodes,
                    structural_only=True,
                )
                corridor_geometry_simplification_topology_report = topology_audit_func(
                    net_file=corridor_variant_file,
                    output_dir=output_dir / "corridor_geometry_simplification_topology",
                    prefix=f"{prefix}_corridor_geometry_simplification_topology",
                    cluster_radius_m=topology_cluster_radius_m,
                    min_cluster_nodes=topology_min_cluster_nodes,
                    osm_file=osm_file,
                )
                corridor_baseline_delta_report = (
                    reference_join_post_teacher_audit_report
                    or reference_visual_detail_tls_connection_repair_reference_delta_report
                    or reference_visual_detail_raw_reference_delta_report
                )
                corridor_geometry_simplification_promotion_report = (
                    _corridor_geometry_simplification_promotion_decision(
                        variant_report=corridor_geometry_simplification_report,
                        sumo_load_report=corridor_geometry_simplification_sumo_load_report,
                        baseline_delta_report=corridor_baseline_delta_report,
                        candidate_delta_report=corridor_geometry_simplification_reference_delta_report,
                        baseline_topology_report=topology_audit_report,
                        candidate_topology_report=corridor_geometry_simplification_topology_report,
                    )
                )
                if corridor_geometry_simplification_promotion_report.get("status") == "pass":
                    reference_visual_detail_comparison_net_file = corridor_variant_file
                    reference_visual_detail_comparison_selection_reason = str(
                        corridor_geometry_simplification_promotion_report.get("reason", "")
                    )
                    topology_audit_report = corridor_geometry_simplification_topology_report
                    reference_hierarchy_audit_report = reference_hierarchy_audit_func(
                        reference_net_file=reference_net_file,
                        candidate_net_file=corridor_variant_file,
                        output_dir=output_dir / "corridor_geometry_simplification_hierarchy_audit",
                        prefix=f"{prefix}_corridor_geometry_simplification_hierarchy_audit",
                        resolve_equivalent_fragmentation=True,
                    )
                    reference_hierarchy_audit_candidate_net_file = corridor_variant_file
                    reference_scope_audit_report = reference_scope_audit_func(
                        reference_net_file=reference_net_file,
                        candidate_net_file=corridor_variant_file,
                        output_dir=output_dir / "corridor_geometry_simplification_scope_audit",
                        prefix=f"{prefix}_corridor_geometry_simplification_scope_audit",
                    )
                    reference_scope_candidate_net_file = corridor_variant_file
            elif corridor_geometry_simplification_report.get(
                "corridor_geometry_simplification_status"
            ) == "not_needed":
                corridor_geometry_simplification_promotion_report = {
                    "status": "skipped",
                    "reason": "not_needed",
                }
    return {
        'corridor_geometry_simplification_promotion_report': corridor_geometry_simplification_promotion_report,
        'corridor_geometry_simplification_reference_delta_report': corridor_geometry_simplification_reference_delta_report,
        'corridor_geometry_simplification_report': corridor_geometry_simplification_report,
        'corridor_geometry_simplification_sumo_load_report': corridor_geometry_simplification_sumo_load_report,
        'corridor_geometry_simplification_topology_report': corridor_geometry_simplification_topology_report,
        'reference_hierarchy_audit_candidate_layer': reference_hierarchy_audit_candidate_layer,
        'reference_hierarchy_audit_candidate_net_file': reference_hierarchy_audit_candidate_net_file,
        'reference_hierarchy_audit_report': reference_hierarchy_audit_report,
        'reference_hierarchy_type_repair_audit_report': reference_hierarchy_type_repair_audit_report,
        'reference_hierarchy_type_repair_promotion_report': reference_hierarchy_type_repair_promotion_report,
        'reference_hierarchy_type_repair_report': reference_hierarchy_type_repair_report,
        'reference_hierarchy_type_repair_sumo_load_report': reference_hierarchy_type_repair_sumo_load_report,
        'reference_scope_audit_report': reference_scope_audit_report,
        'reference_scope_candidate_net_file': reference_scope_candidate_net_file,
        'reference_topology_audit_report': reference_topology_audit_report,
        'reference_visual_detail_comparison_net_file': reference_visual_detail_comparison_net_file,
        'reference_visual_detail_comparison_selection_reason': reference_visual_detail_comparison_selection_reason,
        'topology_audit_report': topology_audit_report,
    }


def _workflow_scope_pruning_section(
    *,
    command_runner: Callable[..., Any],
    network_plan: Any,
    osm_file: Any,
    output_dir: Path,
    prefix: str,
    reference_hierarchy_audit_func: Callable[..., dict[str, Any]],
    reference_net_file: Path | None,
    reference_scope_audit_func: Callable[..., dict[str, Any]],
    run_scope_pruning_after_build: bool,
    run_topology_audit_after_build: bool,
    scope_pruning_func: Callable[..., dict[str, Any]],
    sumo_binary: str,
    timeout_seconds: float,
    topology_audit_func: Callable[..., dict[str, Any]],
    topology_cluster_radius_m: float,
    topology_min_cluster_nodes: int,
    reference_scope_candidate_net_file: Path | None,
    reference_scope_final_audit_report: dict[str, Any] | None,
    reference_scope_final_post_prune_audit_report: dict[str, Any] | None,
    reference_scope_final_promotion_report: dict[str, Any],
    reference_scope_final_pruning_report: dict[str, Any] | None,
    reference_scope_final_sumo_load_report: dict[str, Any] | None,
    reference_scope_pruning_report: dict[str, Any] | None,
    reference_visual_detail_comparison_net_file: Path | None,
) -> dict[str, Any]:
    reference_hierarchy_audit_candidate_net_file = _WORKFLOW_UNSET
    reference_hierarchy_audit_report = _WORKFLOW_UNSET
    reference_scope_audit_report = _WORKFLOW_UNSET
    reference_scope_candidate_layer = _WORKFLOW_UNSET
    reference_scope_pruning_promotion_report = _WORKFLOW_UNSET
    reference_visual_detail_comparison_selection_reason = _WORKFLOW_UNSET
    topology_audit_report = _WORKFLOW_UNSET
    final_hierarchy_report = _WORKFLOW_UNSET
    final_scope_variant_file = _WORKFLOW_UNSET
    final_scope_variant_value = _WORKFLOW_UNSET
    if (
        run_scope_pruning_after_build
        and str(network_plan.get("network_profile", "")) == "reference_matched"
        and reference_net_file is not None
        and reference_visual_detail_comparison_net_file is not None
        and reference_visual_detail_comparison_net_file.exists()
    ):
        reference_scope_final_audit_report = reference_scope_audit_func(
            reference_net_file=reference_net_file,
            candidate_net_file=reference_visual_detail_comparison_net_file,
            output_dir=output_dir / "final_reference_scope_audit",
            prefix=f"{prefix}_final_reference_scope_audit",
        )
        if _int_field(reference_scope_final_audit_report, "prune_candidate_count") > 0:
            reference_scope_final_pruning_report = scope_pruning_func(
                net_file=reference_visual_detail_comparison_net_file,
                reference_scope_report=reference_scope_final_audit_report,
                output_dir=output_dir / "final_reference_scope_pruning",
                prefix=f"{prefix}_final_reference_scope_pruning",
                timeout_seconds=timeout_seconds,
            )
            final_scope_variant_value = str(
                reference_scope_final_pruning_report.get("scope_pruning_variant_file", "")
            )
            final_scope_variant_file = Path(final_scope_variant_value) if final_scope_variant_value else None
            if final_scope_variant_file is not None and final_scope_variant_file.exists():
                reference_scope_final_sumo_load_report = _sumo_load_net(
                    final_scope_variant_file,
                    output_dir=output_dir / "final_reference_scope_pruning_sumo_load",
                    sumo_binary=sumo_binary,
                    timeout_seconds=timeout_seconds,
                    command_runner=command_runner,
                )
                reference_scope_final_post_prune_audit_report = reference_scope_audit_func(
                    reference_net_file=reference_net_file,
                    candidate_net_file=final_scope_variant_file,
                    output_dir=output_dir / "final_reference_scope_post_prune_audit",
                    prefix=f"{prefix}_final_reference_scope_post_prune_audit",
                )
                reference_scope_final_promotion_report = _scope_pruning_promotion_decision(
                    pruning_report=reference_scope_final_pruning_report,
                    post_scope_report=reference_scope_final_post_prune_audit_report,
                    sumo_load_report=reference_scope_final_sumo_load_report,
                    source_net_file=reference_scope_candidate_net_file,
                    variant_net_file=final_scope_variant_file,
                )
                if reference_scope_final_promotion_report.get("status") == "pass":
                    final_hierarchy_report = reference_hierarchy_audit_func(
                        reference_net_file=reference_net_file,
                        candidate_net_file=final_scope_variant_file,
                        output_dir=output_dir / "final_reference_scope_hierarchy_audit",
                        prefix=f"{prefix}_final_reference_scope_hierarchy_audit",
                        resolve_equivalent_fragmentation=True,
                    )
                    if _gate_value(final_hierarchy_report) == "pass":
                        reference_visual_detail_comparison_net_file = final_scope_variant_file
                        reference_visual_detail_comparison_selection_reason = (
                            "reference_scope_pruning_promoted_final"
                        )
                        reference_scope_audit_report = reference_scope_final_post_prune_audit_report
                        reference_scope_candidate_net_file = final_scope_variant_file
                        reference_scope_candidate_layer = "reference_visual_detail"
                        reference_scope_pruning_report = reference_scope_final_pruning_report
                        reference_scope_pruning_report["scope_pruning_promotion_status"] = str(
                            reference_scope_final_promotion_report.get("status", "blocked")
                        )
                        reference_scope_pruning_report["scope_pruning_promotion_checks"] = (
                            reference_scope_final_promotion_report.get("checks", {})
                        )
                        reference_scope_pruning_promotion_report = reference_scope_final_promotion_report
                        reference_hierarchy_audit_report = final_hierarchy_report
                        reference_hierarchy_audit_candidate_net_file = final_scope_variant_file
                        if run_topology_audit_after_build:
                            topology_audit_report = topology_audit_func(
                                net_file=final_scope_variant_file,
                                output_dir=output_dir / "final_reference_scope_topology_audit",
                                prefix=f"{prefix}_final_reference_scope_topology_audit",
                                cluster_radius_m=topology_cluster_radius_m,
                                min_cluster_nodes=topology_min_cluster_nodes,
                                osm_file=osm_file,
                            )
                    else:
                        reference_scope_final_promotion_report = {
                            **reference_scope_final_promotion_report,
                            "status": "blocked",
                            "reason": "final_scope_hierarchy_audit_not_pass",
                            "checks": {
                                **dict(reference_scope_final_promotion_report.get("checks", {})),
                                "final_hierarchy_audit": _gate_value(final_hierarchy_report),
                            },
                        }
    return {
        'reference_hierarchy_audit_candidate_net_file': reference_hierarchy_audit_candidate_net_file,
        'reference_hierarchy_audit_report': reference_hierarchy_audit_report,
        'reference_scope_audit_report': reference_scope_audit_report,
        'reference_scope_candidate_layer': reference_scope_candidate_layer,
        'reference_scope_candidate_net_file': reference_scope_candidate_net_file,
        'reference_scope_final_audit_report': reference_scope_final_audit_report,
        'reference_scope_final_post_prune_audit_report': reference_scope_final_post_prune_audit_report,
        'reference_scope_final_promotion_report': reference_scope_final_promotion_report,
        'reference_scope_final_pruning_report': reference_scope_final_pruning_report,
        'reference_scope_final_sumo_load_report': reference_scope_final_sumo_load_report,
        'reference_scope_pruning_promotion_report': reference_scope_pruning_promotion_report,
        'reference_scope_pruning_report': reference_scope_pruning_report,
        'reference_visual_detail_comparison_net_file': reference_visual_detail_comparison_net_file,
        'reference_visual_detail_comparison_selection_reason': reference_visual_detail_comparison_selection_reason,
        'topology_audit_report': topology_audit_report,
    }


def _workflow_reference_visual_detail_section(
    *,
    area_status: Any,
    bbox: str | None,
    build_func: Callable[..., dict[str, Any]],
    build_report: Any,
    cleaned_place_name: Any,
    clip_source_ways_to_bbox: bool,
    historical_date: str | None,
    max_retries: int,
    max_tile_area_km2: float,
    netconvert_binary: str,
    network_plan: Any,
    output_dir: Path,
    overpass_url: str,
    place_report: Any,
    prefix: str,
    reference_source_way_scope: Any,
    reference_visual_detail_highway_classes: Any,
    reference_visual_detail_modal_way_tags: Any,
    retry_pause_seconds: float,
    service_permission_func: Callable[..., dict[str, Any]],
    service_permission_report: Any,
    should_build_reference_visual_detail: Any,
    source_osm_path: Path | None,
    timeout_seconds: float,
    vehicle_core_highway_classes: Any,
    reference_visual_detail_build_report: dict[str, Any],
    reference_visual_detail_net_file: Path | None,
    reference_visual_detail_service_permission_report: dict[str, Any],
    sumo_binary: str = "sumo",
) -> dict[str, Any]:
    reference_visual_detail_comparison_net_file = _WORKFLOW_UNSET
    reference_visual_detail_comparison_selection_reason = _WORKFLOW_UNSET
    reference_visual_detail_status = _WORKFLOW_UNSET
    visual_build_kwargs = _WORKFLOW_UNSET
    visual_source_osm_path = _WORKFLOW_UNSET
    visual_source_osm_value = _WORKFLOW_UNSET
    if should_build_reference_visual_detail:
        visual_source_osm_path = _reference_visual_source_osm_path(
            build_report,
            source_osm_path,
            reference_visual_detail_highway_classes,
            reference_visual_detail_modal_way_tags,
        )
        visual_source_osm_value = str(visual_source_osm_path) if visual_source_osm_path is not None else None
        if not visual_source_osm_value:
            visual_source_osm_value = None
        visual_build_kwargs: dict[str, Any] = {
            "bbox": bbox,
            "output_dir": output_dir,
            "prefix": f"{prefix}_reference_visual_detail",
            "source_osm_path": Path(str(visual_source_osm_value)) if visual_source_osm_value else None,
            "allowed_highways": reference_visual_detail_highway_classes,
            "allowed_way_ids": reference_source_way_scope,
            "historical_date": historical_date,
            "overpass_url": overpass_url,
            "timeout_seconds": timeout_seconds,
            "max_tile_area_km2": max_tile_area_km2,
            "max_retries": max_retries,
            "retry_pause_seconds": retry_pause_seconds,
            "netconvert_profile": "reference_visual_detail",
        }
        if _supports_keyword(build_func, "include_railway"):
            visual_build_kwargs["include_railway"] = bool(
                reference_visual_detail_modal_way_tags.get("railway")
            )
        if _supports_keyword(build_func, "allowed_railways"):
            visual_build_kwargs["allowed_railways"] = set(
                reference_visual_detail_modal_way_tags.get("railway", set())
            ) or None
        if _supports_keyword(build_func, "netconvert_binary"):
            visual_build_kwargs["netconvert_binary"] = netconvert_binary
        if _supports_keyword(build_func, "clip_source_ways_to_bbox"):
            visual_build_kwargs["clip_source_ways_to_bbox"] = clip_source_ways_to_bbox
        reference_visual_detail_build_report = build_func(
            **visual_build_kwargs,
        )
        if reference_visual_detail_build_report.get("status") != "pass":
            return {
                "status": "fail",
                "claim_status": "construction-invalid",
                "area_input": cleaned_place_name or bbox,
                "area_resolution_status": area_status,
                **_candidate_fields(place_report),
                "user_confirmed_area": "yes" if area_status == "confirmed_by_user" else "confirmed_by_input",
                "network_plan_status": network_plan.get("network_plan_status", "confirmed"),
                "network_profile": network_plan.get("network_profile", ""),
                "reference_target": network_plan.get("reference_target", ""),
                "reference_net_file": network_plan.get("reference_net_file", ""),
                "network_detail_target": network_plan.get("network_detail_target", ""),
                "selected_highway_classes": network_plan.get("highway_classes", []),
                "vehicle_core_highway_classes": sorted(vehicle_core_highway_classes),
                "reference_visual_detail_highway_classes": sorted(reference_visual_detail_highway_classes),
                "reference_visual_detail_status": "failed",
                "network_plan": network_plan,
                "reference_policy": network_plan.get("reference_policy", {}),
                "build": build_report,
                "reference_visual_detail_build": reference_visual_detail_build_report,
                "service_passenger_permissions": service_permission_report,
                "gate_status": {
                    "area_confirmation": "pass",
                    "road_level_scope": "pass",
                    "network_build": _gate_value(build_report),
                    "reference_visual_detail": _gate_value(reference_visual_detail_build_report),
                    "tls_reality_audit": "not_started",
                    "connectivity": "not_started",
                    "routeability_audit": "not_started",
                    "netedit": "not_started",
                    "sumo_gui": "not_started",
                },
                "warnings": list(build_report.get("warnings", []))
                + list(reference_visual_detail_build_report.get("warnings", [])),
            }
        reference_visual_detail_net_file = Path(str(reference_visual_detail_build_report["net_file"]))
        reference_visual_detail_service_permission_report = service_permission_func(
            reference_visual_detail_net_file,
            policy=str(network_plan.get("service_passenger_policy", "sumo_default")),
            output_dir=output_dir / "reference_visual_detail_service_permissions",
            netconvert_binary=netconvert_binary, sumo_binary=sumo_binary, timeout_seconds=timeout_seconds,
        )
        if reference_visual_detail_service_permission_report.get("status") != "pass":
            return {
                "status": "fail",
                "claim_status": "construction-invalid",
                "area_input": cleaned_place_name or bbox,
                "area_resolution_status": area_status,
                **_candidate_fields(place_report),
                "user_confirmed_area": "yes" if area_status == "confirmed_by_user" else "confirmed_by_input",
                "network_plan_status": network_plan.get("network_plan_status", "confirmed"),
                "network_profile": network_plan.get("network_profile", ""),
                "reference_target": network_plan.get("reference_target", ""),
                "reference_net_file": network_plan.get("reference_net_file", ""),
                "network_detail_target": network_plan.get("network_detail_target", ""),
                "selected_highway_classes": network_plan.get("highway_classes", []),
                "vehicle_core_highway_classes": sorted(vehicle_core_highway_classes),
                "reference_visual_detail_highway_classes": sorted(reference_visual_detail_highway_classes),
                "reference_visual_detail_status": "failed",
                "network_plan": network_plan,
                "reference_policy": network_plan.get("reference_policy", {}),
                "build": build_report,
                "reference_visual_detail_build": reference_visual_detail_build_report,
                "service_passenger_permissions": service_permission_report,
                "reference_visual_detail_service_passenger_permissions": reference_visual_detail_service_permission_report,
                "gate_status": {
                    "area_confirmation": "pass",
                    "road_level_scope": "pass",
                    "network_build": _gate_value(build_report),
                    "reference_visual_detail": "fail",
                    "tls_reality_audit": "not_started",
                    "connectivity": "not_started",
                    "routeability_audit": "not_started",
                    "netedit": "not_started",
                    "sumo_gui": "not_started",
                },
                "warnings": list(build_report.get("warnings", []))
                + list(reference_visual_detail_build_report.get("warnings", []))
                + list(reference_visual_detail_service_permission_report.get("warnings", [])),
            }
        reference_visual_detail_net_file = Path(str(reference_visual_detail_service_permission_report.get("net_file") or reference_visual_detail_net_file))
        reference_visual_detail_status = "built"
        reference_visual_detail_comparison_net_file = reference_visual_detail_net_file
        reference_visual_detail_comparison_selection_reason = "raw_visual_detail"
    return {
        'reference_visual_detail_build_report': reference_visual_detail_build_report,
        'reference_visual_detail_comparison_net_file': reference_visual_detail_comparison_net_file,
        'reference_visual_detail_comparison_selection_reason': reference_visual_detail_comparison_selection_reason,
        'reference_visual_detail_net_file': reference_visual_detail_net_file,
        'reference_visual_detail_service_permission_report': reference_visual_detail_service_permission_report,
        'reference_visual_detail_status': reference_visual_detail_status,
    }


def _workflow_reference_matched_gate_section(
    *,
    command_runner: Callable[..., Any],
    net_file: Any,
    network_plan: Any,
    output_dir: Path,
    prefix: str,
    reference_net_file: Path | None,
    reference_scope_audit_func: Callable[..., dict[str, Any]],
    reference_visual_detail_net_file: Path | None,
    run_reference_scope_audit_after_build: bool,
    run_scope_pruning_after_build: bool,
    scope_pruning_func: Callable[..., dict[str, Any]],
    sumo_binary: str,
    timeout_seconds: float,
    reference_scope_audit_report: dict[str, Any] | None,
    reference_scope_candidate_net_file: Path | None,
    reference_scope_post_prune_audit_report: dict[str, Any] | None,
    reference_scope_pruning_promotion_report: dict[str, Any],
    reference_scope_pruning_report: dict[str, Any] | None,
    reference_scope_pruning_sumo_load_report: dict[str, Any] | None,
    reference_visual_detail_comparison_net_file: Path | None,
) -> dict[str, Any]:
    reference_scope_candidate_layer = _WORKFLOW_UNSET
    reference_visual_detail_comparison_selection_reason = _WORKFLOW_UNSET
    scope_variant_file = _WORKFLOW_UNSET
    scope_variant_value = _WORKFLOW_UNSET
    if (
        str(network_plan.get("network_profile", "")) == "reference_matched"
        and reference_net_file is not None
        and run_reference_scope_audit_after_build
    ):
        reference_scope_candidate_net_file = reference_visual_detail_comparison_net_file or reference_visual_detail_net_file or net_file
        reference_scope_candidate_layer = (
            "reference_visual_detail"
            if reference_visual_detail_comparison_net_file is not None or reference_visual_detail_net_file is not None
            else "vehicle_core"
        )
        reference_scope_audit_report = reference_scope_audit_func(
            reference_net_file=reference_net_file,
            candidate_net_file=reference_scope_candidate_net_file,
            output_dir=output_dir / "reference_scope_audit",
            prefix=f"{prefix}_reference_scope_audit",
        )
        if run_scope_pruning_after_build and _int_field(reference_scope_audit_report, "prune_candidate_count") > 0:
            reference_scope_pruning_report = scope_pruning_func(
                net_file=reference_scope_candidate_net_file,
                reference_scope_report=reference_scope_audit_report,
                output_dir=output_dir / "reference_scope_pruning",
                prefix=f"{prefix}_reference_scope_pruning",
                timeout_seconds=timeout_seconds,
            )
            scope_variant_value = str(
                reference_scope_pruning_report.get("scope_pruning_variant_file", "")
            )
            scope_variant_file = Path(scope_variant_value) if scope_variant_value else None
            if (
                reference_scope_pruning_report.get("status") == "pass"
                and scope_variant_file is not None
                and scope_variant_file.exists()
            ):
                reference_scope_pruning_sumo_load_report = _sumo_load_net(
                    scope_variant_file,
                    output_dir=output_dir / "reference_scope_pruning_sumo_load",
                    sumo_binary=sumo_binary,
                    timeout_seconds=timeout_seconds,
                    command_runner=command_runner,
                )
                reference_scope_post_prune_audit_report = reference_scope_audit_func(
                    reference_net_file=reference_net_file,
                    candidate_net_file=scope_variant_file,
                    output_dir=output_dir / "reference_scope_post_prune_audit",
                    prefix=f"{prefix}_reference_scope_post_prune_audit",
                )
                reference_scope_pruning_promotion_report = _scope_pruning_promotion_decision(
                    pruning_report=reference_scope_pruning_report,
                    post_scope_report=reference_scope_post_prune_audit_report,
                    sumo_load_report=reference_scope_pruning_sumo_load_report,
                    source_net_file=reference_scope_candidate_net_file,
                    variant_net_file=scope_variant_file,
                )
                reference_scope_pruning_report["scope_pruning_promotion_status"] = str(
                    reference_scope_pruning_promotion_report.get("status", "blocked")
                )
                reference_scope_pruning_report["scope_pruning_promotion_checks"] = reference_scope_pruning_promotion_report.get(
                    "checks", {}
                )
                if reference_scope_pruning_promotion_report.get("status") == "pass":
                    reference_visual_detail_comparison_net_file = scope_variant_file
                    reference_visual_detail_comparison_selection_reason = "reference_scope_pruning_promoted"
                    reference_scope_candidate_net_file = scope_variant_file
                    reference_scope_candidate_layer = "reference_visual_detail"
                    reference_scope_audit_report = reference_scope_post_prune_audit_report
    return {
        'reference_scope_audit_report': reference_scope_audit_report,
        'reference_scope_candidate_layer': reference_scope_candidate_layer,
        'reference_scope_candidate_net_file': reference_scope_candidate_net_file,
        'reference_scope_post_prune_audit_report': reference_scope_post_prune_audit_report,
        'reference_scope_pruning_promotion_report': reference_scope_pruning_promotion_report,
        'reference_scope_pruning_report': reference_scope_pruning_report,
        'reference_scope_pruning_sumo_load_report': reference_scope_pruning_sumo_load_report,
        'reference_visual_detail_comparison_net_file': reference_visual_detail_comparison_net_file,
        'reference_visual_detail_comparison_selection_reason': reference_visual_detail_comparison_selection_reason,
    }


def run_osm_cleanup_workflow(
    *,
    output_dir: Path,
    bbox: str,
    profile: Literal["standard", "reference_matched"] = "standard",
    source_osm_path: Path | None = None,
    traffic_layers: str | set[str] | None = None,
    reference_net_file: Path | None = None,
    timeout_seconds: float = 240.0,
) -> dict[str, Any]:
    if profile not in {"standard", "reference_matched"}:
        raise ValueError("profile must be 'standard' or 'reference_matched'")
    if profile == "standard":
        if not traffic_layers:
            raise ValueError("traffic_layers is required for profile='standard'")
        if reference_net_file is not None:
            raise ValueError("reference_net_file is not allowed for profile='standard'")
    else:
        if reference_net_file is None:
            raise ValueError("reference_net_file is required for profile='reference_matched'")
        if traffic_layers:
            raise ValueError("traffic_layers is not allowed for profile='reference_matched'")

    is_reference_matched = profile == "reference_matched"
    place_name = None
    confirmed_area = True
    prefix = "sumo_osm_cleanup"
    clip_source_ways_to_bbox = True
    highway_classes = None
    network_profile = profile
    reference_policy_report = None
    service_passenger_policy = None
    historical_date = None
    overpass_url = "https://overpass-api.de/api/interpreter"
    netconvert_binary = "netconvert"
    sumo_binary = "sumo"
    max_tile_area_km2 = 2500.0
    max_retries = 2
    retry_pause_seconds = 5.0
    map_temporal_scope = "current"
    map_target_date = None
    review_decisions_file = None
    launch_netedit_after_build = False
    launch_netedit_review_after_build = False
    launch_sumo_gui_after_build = False
    run_topology_audit_after_build = True
    topology_cluster_radius_m = 30.0
    topology_min_cluster_nodes = 3
    run_routeability_audit_after_build = True
    run_connection_mode_audit_after_build = True
    run_standard_nema_scan_after_build = True
    routeability_vehicle_count = None
    routeability_initial_end = None
    routeability_max_end = None
    run_tls_aggregation_after_build = not is_reference_matched
    run_junction_aggregation_after_build = False
    run_reference_join_audit_after_build = is_reference_matched
    reference_join_audit_structural_only = False
    run_reference_join_aggregation_after_build = False
    run_reference_hierarchy_audit_after_build = is_reference_matched
    run_reference_hierarchy_type_repair_after_build = False
    run_reference_scope_audit_after_build = is_reference_matched
    run_reference_bbox_scope_after_build = is_reference_matched
    run_road_connectivity_parity_audit_after_build = is_reference_matched
    run_scope_pruning_after_build = False
    run_corridor_geometry_simplification_after_build = False
    run_corridor_edit_ledger_after_build = False
    teacher_guided_repair_max_ready_candidates = 80
    run_teacher_guided_repair_after_build = False
    teacher_guided_probe_matrix_junction_ids = None
    road_connectivity_replay_max_owners = 4
    road_connectivity_probe_edge_ids = None
    key_edge_queries = None

    cleaned_place_name = (place_name or "").strip()
    bbox_input = (bbox or "").strip()
    bbox_from_url = osm_map_url_bbox(bbox_input)
    if bbox_from_url:
        cleaned_place_name = bbox_input
        bbox = bbox_from_url
    else:
        bbox_from_url = osm_map_url_bbox(cleaned_place_name)
        if bbox_from_url:
            bbox = bbox_from_url
    place_report = None
    reference_bbox_report: dict[str, Any] | None = None
    if not bbox and source_osm_path is None and reference_net_file is not None:
        reference_bbox_report = derive_reference_net_bbox(reference_net_file)
        derived_bbox = str(reference_bbox_report.get("reference_bbox", "")).strip()
        if reference_bbox_report.get("status") == "pass" and derived_bbox:
            bbox = derived_bbox
    if cleaned_place_name and not bbox and source_osm_path is None:
        place_report = resolve_osm_place(cleaned_place_name)
        if not confirmed_area:
            return _blocked_place_report(cleaned_place_name, output_dir, place_report)
        resolved_bbox = str(place_report.get("candidate_bbox", ""))
        if place_report.get("status") != "pass" or not resolved_bbox:
            return {
                "status": "fail",
                "claim_status": "construction-invalid",
                "area_input": cleaned_place_name,
                "area_resolution_status": str(place_report.get("area_resolution_status", "blocked")),
                **_candidate_fields(place_report),
                "osm_preview_url": str(place_report.get("osm_preview_url", osm_preview_url(cleaned_place_name))),
                "user_confirmed_area": "yes",
                "gate_status": {
                    "area_confirmation": "fail",
                    "road_level_scope": "not_started",
                    "network_build": "not_started",
                    "tls_reality_audit": "not_started",
                    "connectivity": "not_started",
                    "routeability_audit": "not_started",
                    "netedit": "not_started",
                    "sumo_gui": "not_started",
                },
                "warnings": list(place_report.get("warnings", [])) + ["confirmed place_name could not be resolved to a bbox"],
            }
        bbox = resolved_bbox
    if not bbox:
        reference_bbox_status = (
            str(reference_bbox_report.get("reference_bbox_status", "blocked"))
            if reference_bbox_report is not None
            else "blocked"
        )
        reference_bbox_warnings = list(reference_bbox_report.get("warnings", [])) if reference_bbox_report else []
        return {
            "status": "fail",
            "claim_status": "construction-invalid",
            "area_input": cleaned_place_name,
            "area_resolution_status": reference_bbox_status,
            **_candidate_fields(place_report),
            **_reference_bbox_fields(reference_bbox_report),
            "gate_status": {
                "area_confirmation": "fail",
                "road_level_scope": "not_started",
                "network_build": "not_started",
                "tls_reality_audit": "not_started",
                "connectivity": "not_started",
                "routeability_audit": "not_started",
                "netedit": "not_started",
                "sumo_gui": "not_started",
            },
            "warnings": reference_bbox_warnings + ["bbox is required for OSM network construction"],
        }
    area_status = "confirmed_by_user" if cleaned_place_name and confirmed_area else "confirmed_by_input"
    network_plan = derive_network_plan(
        highway_classes=highway_classes,
        traffic_layers=traffic_layers,
        network_profile=network_profile,
        reference_net_file=reference_net_file,
        reference_policy_report=reference_policy_report,
        service_passenger_policy=service_passenger_policy,
    )
    if network_plan.get("status") == "blocked":
        return _blocked_road_level_scope_report(
            area_input=cleaned_place_name or bbox,
            area_status=area_status,
            place_report=place_report,
            cleaned_place_name=cleaned_place_name,
            bbox=bbox,
            network_plan=network_plan,
        )
    if network_plan.get("status") != "pass":
        return {
            "status": "fail",
            "claim_status": "construction-invalid",
            "area_input": cleaned_place_name or bbox,
            "area_resolution_status": area_status,
            **(_candidate_fields(place_report) if place_report is not None else {**_candidate_fields(None), "candidate_bbox": bbox}),
            "user_confirmed_area": "yes" if area_status == "confirmed_by_user" else "confirmed_by_input",
            "network_plan_status": network_plan.get("network_plan_status", "failed"),
            "network_profile": network_plan.get("network_profile", ""),
            "reference_target": network_plan.get("reference_target", ""),
            "reference_net_file": network_plan.get("reference_net_file", ""),
            "network_detail_target": network_plan.get("network_detail_target", ""),
            "movement_layers": network_plan.get("movement_layers", []),
            "selected_highway_classes": network_plan.get("highway_classes", []),
            "service_passenger_policy": network_plan.get("service_passenger_policy", "sumo_default"),
            "network_plan": network_plan,
            "gate_status": {
                "area_confirmation": "pass",
                "road_level_scope": "fail",
                "network_build": "not_started",
                "tls_reality_audit": "not_started",
                "connectivity": "not_started",
                "routeability_audit": "not_started",
                "netedit": "not_started",
                "sumo_gui": "not_started",
            },
            "warnings": list(network_plan.get("warnings", [])),
        }
    reference_source_net_file = reference_net_file
    reference_bbox_scope_report: dict[str, Any] | None = None
    if (
        run_reference_bbox_scope_after_build
        and str(network_plan.get("network_profile", "")) == "reference_matched"
        and reference_net_file is not None
    ):
        reference_bbox_scope_report = build_reference_bbox_variant(
            reference_net_file=reference_net_file,
            bbox=bbox,
            output_dir=output_dir / "reference_bbox_scope",
            prefix=f"{prefix}_reference_bbox_scope",
            netconvert_binary=netconvert_binary,
            timeout_seconds=timeout_seconds,
            command_runner=run_command,
        )
        scoped_reference_value = reference_bbox_scope_report.get("variant_file", "")
        scoped_reference_file = Path(str(scoped_reference_value)) if scoped_reference_value else None
        if (
            reference_bbox_scope_report.get("status") == "pass"
            and scoped_reference_file is not None
            and scoped_reference_file.exists()
        ):
            # From this point on, all teacher-guided parity and replay stages
            # use the same geographic scope as the candidate OSM build.  Keep
            # the original teacher path separately for provenance and source
            # way-scope planning.
            reference_net_file = scoped_reference_file
    selected_highway_classes = set(network_plan.get("highway_classes", []))
    reference_source_way_ids = {
        str(item)
        for item in network_plan.get("reference_source_way_ids", [])
        if str(item).strip()
    }
    reference_source_way_scope = reference_source_way_ids or None
    build_kwargs: dict[str, Any] = {
        "bbox": bbox,
        "output_dir": output_dir,
        "prefix": prefix,
        "source_osm_path": source_osm_path,
        "allowed_highways": selected_highway_classes,
        "allowed_way_ids": reference_source_way_scope,
        "historical_date": historical_date,
        "overpass_url": overpass_url,
        "timeout_seconds": timeout_seconds,
        "max_tile_area_km2": max_tile_area_km2,
        "max_retries": max_retries,
        "retry_pause_seconds": retry_pause_seconds,
        "netconvert_profile": "vehicle_core",
    }
    if _supports_keyword(build_osm_network, "netconvert_binary"):
        build_kwargs["netconvert_binary"] = netconvert_binary
    if _supports_keyword(build_osm_network, "clip_source_ways_to_bbox"):
        build_kwargs["clip_source_ways_to_bbox"] = clip_source_ways_to_bbox
    build_report = build_osm_network(**build_kwargs)
    if build_report.get("status") != "pass":
        return {
            "status": "fail",
            "claim_status": "construction-invalid",
            "area_input": cleaned_place_name or bbox,
            "area_resolution_status": area_status,
            **_candidate_fields(place_report),
            "user_confirmed_area": "yes" if area_status == "confirmed_by_user" else "confirmed_by_input",
            "network_plan_status": network_plan.get("network_plan_status", "confirmed"),
            "network_profile": network_plan.get("network_profile", ""),
            "reference_target": network_plan.get("reference_target", ""),
            "reference_net_file": network_plan.get("reference_net_file", ""),
            "network_detail_target": network_plan.get("network_detail_target", ""),
            "primary_network_layer": network_plan.get("primary_network_layer", ""),
            "auxiliary_modal_layers": network_plan.get("auxiliary_modal_layers", []),
            "movement_layers": network_plan.get("movement_layers", []),
            "selected_highway_classes": network_plan.get("highway_classes", []),
            "service_passenger_policy": network_plan.get("service_passenger_policy", "sumo_default"),
            "reference_policy": network_plan.get("reference_policy", {}),
            "build": build_report,
            "gate_status": {
                "area_confirmation": "pass",
                "road_level_scope": "pass",
                "network_build": _gate_value(build_report),
                "tls_reality_audit": "not_started",
                "connectivity": "not_started",
                "routeability_audit": "not_started",
                "netedit": "not_started",
                "sumo_gui": "not_started",
            },
            "warnings": list(build_report.get("warnings", [])),
        }
    raw_net_file = Path(str(build_report["net_file"]))
    service_permission_report = apply_service_passenger_permissions(
        raw_net_file,
        policy=str(network_plan.get("service_passenger_policy", "sumo_default")),
        output_dir=output_dir / "service_permissions",
        netconvert_binary=netconvert_binary, sumo_binary=sumo_binary, timeout_seconds=timeout_seconds,
    )
    if service_permission_report.get("status") != "pass":
        return {
            "status": "fail",
            "claim_status": "construction-invalid",
            "area_input": cleaned_place_name or bbox,
            "area_resolution_status": area_status,
            **_candidate_fields(place_report),
            "user_confirmed_area": "yes" if area_status == "confirmed_by_user" else "confirmed_by_input",
            "network_plan_status": network_plan.get("network_plan_status", "confirmed"),
            "network_profile": network_plan.get("network_profile", ""),
            "reference_target": network_plan.get("reference_target", ""),
            "reference_net_file": network_plan.get("reference_net_file", ""),
            "network_detail_target": network_plan.get("network_detail_target", ""),
            "primary_network_layer": network_plan.get("primary_network_layer", ""),
            "auxiliary_modal_layers": network_plan.get("auxiliary_modal_layers", []),
            "movement_layers": network_plan.get("movement_layers", []),
            "selected_highway_classes": network_plan.get("highway_classes", []),
            "service_passenger_policy": network_plan.get("service_passenger_policy", "sumo_default"),
            "reference_policy": network_plan.get("reference_policy", {}),
            "build": build_report,
            "service_passenger_permissions": service_permission_report,
            "gate_status": {
                "area_confirmation": "pass",
                "road_level_scope": "pass",
                "network_build": _gate_value(build_report),
                "tls_reality_audit": "not_started",
                "connectivity": "not_started",
                "routeability_audit": "not_started",
                "netedit": "not_started",
                "sumo_gui": "not_started",
            },
            "warnings": list(build_report.get("warnings", [])) + list(service_permission_report.get("warnings", [])),
        }
    net_file = Path(str(service_permission_report.get("net_file") or raw_net_file))
    reference_visual_detail_status = "not_applicable"
    reference_visual_detail_net_file: Path | None = None
    reference_visual_detail_comparison_net_file: Path | None = None
    reference_visual_detail_comparison_selection_reason = "not_applicable"
    reference_visual_detail_build_report: dict[str, Any] = {}
    reference_visual_detail_service_permission_report: dict[str, Any] = {}
    reference_visual_detail_netedit_report: dict[str, Any] = {}
    reference_visual_detail_tls_report: dict[str, Any] | None = None
    reference_visual_detail_tls_aggregation_report: dict[str, Any] | None = None
    reference_visual_detail_tls_aggregation_candidates: list[dict[str, Any]] = []
    reference_visual_detail_tls_signal_grouping_report: dict[str, Any] | None = None
    reference_visual_detail_tls_low_vehicle_control_report: dict[str, Any] | None = None
    reference_visual_detail_tls_low_vehicle_control_candidates: list[dict[str, Any]] = []
    reference_visual_detail_tls_connection_repair_report: dict[str, Any] | None = None
    reference_visual_detail_raw_reference_delta_report: dict[str, Any] | None = None
    reference_visual_detail_tls_aggregation_reference_delta_report: dict[str, Any] | None = None
    reference_visual_detail_tls_aggregation_reference_promotion_report: dict[str, Any] = {
        "status": "skipped",
        "reason": "not_run",
    }
    reference_visual_detail_tls_signal_grouping_reference_delta_report: dict[str, Any] | None = None
    reference_visual_detail_tls_signal_grouping_reference_promotion_report: dict[str, Any] = {
        "status": "skipped",
        "reason": "not_run",
    }
    reference_visual_detail_tls_low_vehicle_control_sumo_load_report: dict[str, Any] | None = None
    reference_visual_detail_tls_low_vehicle_control_reference_delta_report: dict[str, Any] | None = None
    reference_visual_detail_tls_low_vehicle_control_reference_promotion_report: dict[str, Any] = {
        "status": "skipped",
        "reason": "not_run",
    }
    reference_visual_detail_tls_signal_grouping_sumo_load_report: dict[str, Any] | None = None
    reference_visual_detail_tls_connection_repair_reference_delta_report: dict[str, Any] | None = None
    reference_visual_detail_tls_connection_repair_sumo_load_report: dict[str, Any] | None = None
    reference_visual_detail_tls_connection_repair_promotion_report: dict[str, Any] = {
        "status": "skipped",
        "reason": "not_run",
    }
    junction_aggregation_report: dict[str, Any] | None = None
    reference_join_audit_report: dict[str, Any] | None = None
    tls_gap_destination_mapping_report: dict[str, Any] | None = None
    tls_repair_variant_report: dict[str, Any] | None = None
    tls_repair_variant_sumo_load_report: dict[str, Any] | None = None
    tls_repair_variant_semantic_report: dict[str, Any] | None = None
    tls_repair_variant_reference_audit_report: dict[str, Any] | None = None
    tls_repair_decision_report: dict[str, Any] | None = None
    reference_join_post_teacher_audit_report: dict[str, Any] | None = None
    post_teacher_tls_low_vehicle_control_report: dict[str, Any] | None = None
    post_teacher_tls_low_vehicle_control_candidates: list[dict[str, Any]] = []
    post_teacher_tls_low_vehicle_control_sumo_load_report: dict[str, Any] | None = None
    post_teacher_tls_low_vehicle_control_reference_delta_report: dict[str, Any] | None = None
    post_teacher_tls_low_vehicle_control_reference_promotion_report: dict[str, Any] = {
        "status": "skipped",
        "reason": "not_run",
    }
    post_teacher_tls_signal_grouping_report: dict[str, Any] | None = None
    post_teacher_tls_signal_grouping_sumo_load_report: dict[str, Any] | None = None
    post_teacher_tls_signal_grouping_reference_delta_report: dict[str, Any] | None = None
    post_teacher_tls_signal_grouping_reference_promotion_report: dict[str, Any] = {
        "status": "skipped",
        "reason": "not_run",
    }
    post_teacher_tls_non_controller_junction_demotion_report: dict[str, Any] | None = None
    post_teacher_tls_non_controller_junction_demotion_sumo_load_report: dict[str, Any] | None = None
    post_teacher_tls_non_controller_junction_demotion_reference_delta_report: dict[str, Any] | None = None
    post_teacher_tls_non_controller_junction_demotion_reference_promotion_report: dict[str, Any] = {
        "status": "skipped",
        "reason": "not_run",
    }
    post_teacher_tls_connection_repair_report: dict[str, Any] | None = None
    post_teacher_tls_connection_repair_sumo_load_report: dict[str, Any] | None = None
    post_teacher_tls_connection_repair_reference_delta_report: dict[str, Any] | None = None
    post_teacher_tls_connection_repair_reference_promotion_report: dict[str, Any] = {
        "status": "skipped",
        "reason": "not_run",
    }
    post_teacher_tls_connection_repair_movement_rebuild_queue_report: dict[str, Any] | None = None
    post_teacher_tls_connection_repair_movement_rebuild_plain_export_report: dict[str, Any] | None = None
    post_teacher_tls_connection_repair_movement_rebuild_run_report: dict[str, Any] | None = None
    post_teacher_tls_connection_repair_movement_rebuild_best_variant_file: Path | None = None
    final_movement_rebuild_queue_report: dict[str, Any] | None = None
    final_movement_rebuild_plain_export_report: dict[str, Any] | None = None
    final_movement_rebuild_run_report: dict[str, Any] | None = None
    final_movement_rebuild_best_variant_file: Path | None = None
    final_movement_rebuild_sumo_load_report: dict[str, Any] | None = None
    final_movement_rebuild_reference_delta_report: dict[str, Any] | None = None
    final_movement_rebuild_reference_promotion_report: dict[str, Any] = {
        "status": "skipped",
        "reason": "not_run",
    }
    final_movement_direct_replay_report: dict[str, Any] | None = None
    final_movement_direct_replay_reference_delta_report: dict[str, Any] | None = None
    final_movement_direct_replay_reference_promotion_report: dict[str, Any] = {
        "status": "skipped",
        "reason": "not_run",
    }
    final_movement_direct_replay_best_variant_file: Path | None = None
    final_movement_direct_replay_last_queue_report: dict[str, Any] | None = None
    final_movement_rebuild_internal_regression_restore_report: dict[str, Any] | None = None
    final_movement_rebuild_internal_regression_restore_sumo_load_report: dict[str, Any] | None = None
    final_movement_rebuild_internal_regression_restore_reference_delta_report: dict[str, Any] | None = None
    final_movement_rebuild_internal_regression_restore_promotion_report: dict[str, Any] | None = None
    reference_join_aggregation_report: dict[str, Any] | None = None
    teacher_guided_repair_queue_report: dict[str, Any] | None = None
    teacher_guided_scoped_tls_cell_batch_report: dict[str, Any] | None = None
    teacher_guided_plain_export_report: dict[str, Any] | None = None
    teacher_guided_repair_run_report: dict[str, Any] | None = None
    teacher_guided_probe_matrix_report: dict[str, Any] | None = None
    road_connectivity_replay_report: dict[str, Any] | None = None
    road_connectivity_parity_audit_report: dict[str, Any] | None = None
    road_connectivity_seed_probe_report: dict[str, Any] | None = None
    road_connectivity_split_root_alias_repair_report: dict[str, Any] | None = None
    road_connection_topology_replay_report: dict[str, Any] | None = None
    teacher_guided_repair_best_variant_file: Path | None = None
    teacher_guided_direct_replay_report: dict[str, Any] | None = None
    teacher_guided_direct_replay_reference_delta_report: dict[str, Any] | None = None
    teacher_guided_direct_replay_reference_promotion_report: dict[str, Any] = {
        "status": "skipped",
        "reason": "not_run",
    }
    teacher_guided_direct_replay_best_variant_file: Path | None = None
    teacher_guided_repair_best_expanded_scope_net_file: Path | None = None
    teacher_guided_seed_report: dict[str, Any] | None = None
    teacher_guided_repair_seed_source = "skipped"
    teacher_guided_repair_requires_reference_promotion = False
    teacher_guided_repair_reference_promotion_report: dict[str, Any] = {
        "status": "skipped",
        "reason": "not_run",
    }
    reference_hierarchy_audit_report: dict[str, Any] | None = None
    reference_hierarchy_audit_candidate_layer = "not_applicable"
    reference_hierarchy_audit_candidate_net_file: Path | None = None
    reference_hierarchy_type_repair_report: dict[str, Any] | None = None
    reference_hierarchy_type_repair_sumo_load_report: dict[str, Any] | None = None
    reference_hierarchy_type_repair_audit_report: dict[str, Any] | None = None
    reference_hierarchy_type_repair_promotion_report: dict[str, Any] = {
        "status": "skipped",
        "reason": "not_run",
    }
    corridor_geometry_simplification_report: dict[str, Any] | None = None
    corridor_geometry_simplification_sumo_load_report: dict[str, Any] | None = None
    corridor_geometry_simplification_reference_delta_report: dict[str, Any] | None = None
    corridor_geometry_simplification_topology_report: dict[str, Any] | None = None
    corridor_geometry_simplification_promotion_report: dict[str, Any] = {
        "status": "skipped",
        "reason": "not_run",
    }
    corridor_edit_ledger_report: dict[str, Any] | None = None
    reference_scope_audit_report: dict[str, Any] | None = None
    reference_scope_pruning_report: dict[str, Any] | None = None
    reference_scope_post_prune_audit_report: dict[str, Any] | None = None
    reference_scope_pruning_sumo_load_report: dict[str, Any] | None = None
    reference_scope_pruning_promotion_report: dict[str, Any] = {}
    reference_scope_final_audit_report: dict[str, Any] | None = None
    reference_scope_final_pruning_report: dict[str, Any] | None = None
    reference_scope_final_post_prune_audit_report: dict[str, Any] | None = None
    reference_scope_final_sumo_load_report: dict[str, Any] | None = None
    reference_scope_final_promotion_report: dict[str, Any] = {
        "status": "skipped",
        "reason": "not_run",
    }
    reference_scope_candidate_layer = "not_applicable"
    reference_scope_candidate_net_file: Path | None = None
    reference_join_audit_candidate_layer = "not_applicable"
    reference_join_audit_candidate_net_file: Path | None = None
    supplied_review_decisions: dict[str, Any] | None = None
    review_decisions_source_status = "not_supplied"
    review_decisions_source_error = ""
    tls_aggregation_report: dict[str, Any] | None = None
    vehicle_core_highway_classes = _class_set(
        network_plan.get("vehicle_core_highway_classes", network_plan.get("highway_classes", []))
    )
    reference_visual_detail_highway_classes = _class_set(
        network_plan.get("reference_visual_detail_highway_classes", [])
    )
    reference_visual_detail_modal_way_tags = {
        str(key): {
            str(value)
            for value in values
            if str(value).strip()
        }
        for key, values in dict(
            network_plan.get("reference_visual_detail_modal_way_tags", {})
        ).items()
        if isinstance(values, (list, tuple, set, frozenset)) and values
    }
    should_build_reference_visual_detail = (
        str(network_plan.get("network_profile", "")) == "reference_matched"
        and (
            bool(reference_visual_detail_highway_classes)
            and reference_visual_detail_highway_classes != vehicle_core_highway_classes
            or bool(reference_visual_detail_modal_way_tags)
        )
    )
    if str(network_plan.get("network_profile", "")) == "reference_matched":
        reference_visual_detail_status = "same_as_vehicle_core"
    _reference_visual_detail_section_result = _workflow_reference_visual_detail_section(
        area_status=area_status,
        bbox=bbox,
        build_func=build_osm_network,
        build_report=build_report,
        cleaned_place_name=cleaned_place_name,
        clip_source_ways_to_bbox=clip_source_ways_to_bbox,
        historical_date=historical_date,
        max_retries=max_retries,
        max_tile_area_km2=max_tile_area_km2,
        netconvert_binary=netconvert_binary,
        network_plan=network_plan,
        output_dir=output_dir,
        overpass_url=overpass_url,
        place_report=place_report,
        prefix=prefix,
        reference_source_way_scope=reference_source_way_scope,
        reference_visual_detail_highway_classes=reference_visual_detail_highway_classes,
        reference_visual_detail_modal_way_tags=reference_visual_detail_modal_way_tags,
        retry_pause_seconds=retry_pause_seconds,
        service_permission_func=apply_service_passenger_permissions,
        service_permission_report=service_permission_report,
        should_build_reference_visual_detail=should_build_reference_visual_detail,
        source_osm_path=source_osm_path,
        timeout_seconds=timeout_seconds,
        vehicle_core_highway_classes=vehicle_core_highway_classes,
        reference_visual_detail_build_report=reference_visual_detail_build_report,
        reference_visual_detail_net_file=reference_visual_detail_net_file,
        reference_visual_detail_service_permission_report=reference_visual_detail_service_permission_report,
        sumo_binary=sumo_binary,
    )
    if _reference_visual_detail_section_result.get("status") == "fail":
        return _reference_visual_detail_section_result
    reference_visual_detail_build_report = _reference_visual_detail_section_result['reference_visual_detail_build_report']
    reference_visual_detail_net_file = _reference_visual_detail_section_result['reference_visual_detail_net_file']
    reference_visual_detail_service_permission_report = _reference_visual_detail_section_result['reference_visual_detail_service_permission_report']
    if _reference_visual_detail_section_result['reference_visual_detail_comparison_net_file'] is not _WORKFLOW_UNSET:
        reference_visual_detail_comparison_net_file = _reference_visual_detail_section_result['reference_visual_detail_comparison_net_file']
    if _reference_visual_detail_section_result['reference_visual_detail_comparison_selection_reason'] is not _WORKFLOW_UNSET:
        reference_visual_detail_comparison_selection_reason = _reference_visual_detail_section_result['reference_visual_detail_comparison_selection_reason']
    if _reference_visual_detail_section_result['reference_visual_detail_status'] is not _WORKFLOW_UNSET:
        reference_visual_detail_status = _reference_visual_detail_section_result['reference_visual_detail_status']
    filtered_osm_value = build_report.get("filtered_osm_file") or build_report.get("source_osm_file")
    osm_file = Path(str(filtered_osm_value)) if filtered_osm_value else None
    tls_report = audit_tls(
        net_file=net_file,
        output_dir=output_dir / "tls_audit",
        prefix=f"{prefix}_tls_audit",
        osm_file=osm_file,
        google_maps_temporal_scope=map_temporal_scope,
        google_maps_target_date=map_target_date,
    )
    if run_tls_aggregation_after_build and _should_run_tls_aggregation(tls_report, build_tls_aggregation_variant):
        tls_aggregation_report = build_tls_aggregation_variant(
            net_file=net_file,
            tls_audit_report=tls_report,
            output_dir=output_dir / "tls_aggregation",
            prefix=f"{prefix}_tls_aggregation",
            timeout_seconds=timeout_seconds,
        )
        tls_variant_value = tls_aggregation_report.get("tls_aggregation_variant_file", "") if tls_aggregation_report else ""
        if (
            tls_aggregation_report.get("status") == "pass"
            and tls_variant_value
            and _tls_aggregation_preserves_controlled_connections(tls_aggregation_report)
        ):
            candidate_tls_net_file = Path(str(tls_variant_value))
            if candidate_tls_net_file.exists():
                net_file = candidate_tls_net_file
    _tls_section_result = _workflow_tls_aggregation_section(
        command_runner=run_command,
        map_target_date=map_target_date,
        map_temporal_scope=map_temporal_scope,
        network_plan=network_plan,
        osm_file=osm_file,
        output_dir=output_dir,
        prefix=prefix,
        reference_join_audit_func=audit_reference_join_patterns,
        reference_net_file=reference_net_file,
        reference_visual_detail_net_file=reference_visual_detail_net_file,
        reference_visual_detail_tls_aggregation_candidates=reference_visual_detail_tls_aggregation_candidates,
        reference_visual_detail_tls_low_vehicle_control_candidates=reference_visual_detail_tls_low_vehicle_control_candidates,
        run_tls_aggregation_after_build=run_tls_aggregation_after_build,
        sumo_binary=sumo_binary,
        timeout_seconds=timeout_seconds,
        tls_aggregation_func=build_tls_aggregation_variant,
        tls_audit_func=audit_tls,
        tls_connection_repair_func=build_tls_connection_repair_variant,
        tls_low_vehicle_control_func=build_tls_low_vehicle_control_variant,
        tls_signal_grouping_func=build_tls_signal_grouping_variant,
        topology_cluster_radius_m=topology_cluster_radius_m,
        topology_min_cluster_nodes=topology_min_cluster_nodes,
        reference_visual_detail_comparison_net_file=reference_visual_detail_comparison_net_file,
        reference_visual_detail_raw_reference_delta_report=reference_visual_detail_raw_reference_delta_report,
        reference_visual_detail_tls_aggregation_reference_delta_report=reference_visual_detail_tls_aggregation_reference_delta_report,
        reference_visual_detail_tls_aggregation_reference_promotion_report=reference_visual_detail_tls_aggregation_reference_promotion_report,
        reference_visual_detail_tls_aggregation_report=reference_visual_detail_tls_aggregation_report,
        reference_visual_detail_tls_connection_repair_promotion_report=reference_visual_detail_tls_connection_repair_promotion_report,
        reference_visual_detail_tls_connection_repair_reference_delta_report=reference_visual_detail_tls_connection_repair_reference_delta_report,
        reference_visual_detail_tls_connection_repair_report=reference_visual_detail_tls_connection_repair_report,
        reference_visual_detail_tls_connection_repair_sumo_load_report=reference_visual_detail_tls_connection_repair_sumo_load_report,
        reference_visual_detail_tls_low_vehicle_control_reference_promotion_report=reference_visual_detail_tls_low_vehicle_control_reference_promotion_report,
        reference_visual_detail_tls_report=reference_visual_detail_tls_report,
        reference_visual_detail_tls_signal_grouping_reference_delta_report=reference_visual_detail_tls_signal_grouping_reference_delta_report,
        reference_visual_detail_tls_signal_grouping_reference_promotion_report=reference_visual_detail_tls_signal_grouping_reference_promotion_report,
        reference_visual_detail_tls_signal_grouping_report=reference_visual_detail_tls_signal_grouping_report,
        reference_visual_detail_tls_signal_grouping_sumo_load_report=reference_visual_detail_tls_signal_grouping_sumo_load_report,
        tls_aggregation_report=tls_aggregation_report,
    )
    reference_visual_detail_comparison_net_file = _tls_section_result['reference_visual_detail_comparison_net_file']
    reference_visual_detail_raw_reference_delta_report = _tls_section_result['reference_visual_detail_raw_reference_delta_report']
    reference_visual_detail_tls_aggregation_reference_delta_report = _tls_section_result['reference_visual_detail_tls_aggregation_reference_delta_report']
    reference_visual_detail_tls_aggregation_reference_promotion_report = _tls_section_result['reference_visual_detail_tls_aggregation_reference_promotion_report']
    reference_visual_detail_tls_aggregation_report = _tls_section_result['reference_visual_detail_tls_aggregation_report']
    reference_visual_detail_tls_connection_repair_promotion_report = _tls_section_result['reference_visual_detail_tls_connection_repair_promotion_report']
    reference_visual_detail_tls_connection_repair_reference_delta_report = _tls_section_result['reference_visual_detail_tls_connection_repair_reference_delta_report']
    reference_visual_detail_tls_connection_repair_report = _tls_section_result['reference_visual_detail_tls_connection_repair_report']
    reference_visual_detail_tls_connection_repair_sumo_load_report = _tls_section_result['reference_visual_detail_tls_connection_repair_sumo_load_report']
    reference_visual_detail_tls_low_vehicle_control_reference_promotion_report = _tls_section_result['reference_visual_detail_tls_low_vehicle_control_reference_promotion_report']
    reference_visual_detail_tls_report = _tls_section_result['reference_visual_detail_tls_report']
    reference_visual_detail_tls_signal_grouping_reference_delta_report = _tls_section_result['reference_visual_detail_tls_signal_grouping_reference_delta_report']
    reference_visual_detail_tls_signal_grouping_reference_promotion_report = _tls_section_result['reference_visual_detail_tls_signal_grouping_reference_promotion_report']
    reference_visual_detail_tls_signal_grouping_report = _tls_section_result['reference_visual_detail_tls_signal_grouping_report']
    reference_visual_detail_tls_signal_grouping_sumo_load_report = _tls_section_result['reference_visual_detail_tls_signal_grouping_sumo_load_report']
    tls_aggregation_report = _tls_section_result['tls_aggregation_report']
    if _tls_section_result['reference_visual_detail_comparison_selection_reason'] is not _WORKFLOW_UNSET:
        reference_visual_detail_comparison_selection_reason = _tls_section_result['reference_visual_detail_comparison_selection_reason']
    if _tls_section_result['reference_visual_detail_tls_low_vehicle_control_reference_delta_report'] is not _WORKFLOW_UNSET:
        reference_visual_detail_tls_low_vehicle_control_reference_delta_report = _tls_section_result['reference_visual_detail_tls_low_vehicle_control_reference_delta_report']
    if _tls_section_result['reference_visual_detail_tls_low_vehicle_control_report'] is not _WORKFLOW_UNSET:
        reference_visual_detail_tls_low_vehicle_control_report = _tls_section_result['reference_visual_detail_tls_low_vehicle_control_report']
    if _tls_section_result['reference_visual_detail_tls_low_vehicle_control_sumo_load_report'] is not _WORKFLOW_UNSET:
        reference_visual_detail_tls_low_vehicle_control_sumo_load_report = _tls_section_result['reference_visual_detail_tls_low_vehicle_control_sumo_load_report']
    raw_connectivity_report = summarize_passenger_connectivity(net_file)
    connectivity_report = raw_connectivity_report
    connectivity_quality = _connectivity_quality(connectivity_report)
    connected_core_report = None
    connected_core_connectivity_report = None
    if connectivity_quality["strict_connectivity_status"] != "pass":
        connected_core_report = extract_largest_passenger_component_core(
            net_file,
            output_dir=output_dir / "connected_core",
            prefix=prefix,
            timeout_seconds=timeout_seconds,
        )
        core_file_value = connected_core_report.get("connected_core_file", "") if connected_core_report else ""
        if connected_core_report.get("status") == "pass" and core_file_value:
            candidate_core_file = Path(str(core_file_value))
            connected_core_connectivity_report = summarize_passenger_connectivity(candidate_core_file)
            connected_core_quality = _connectivity_quality(connected_core_connectivity_report)
            if connected_core_quality["strict_connectivity_status"] == "pass":
                net_file = candidate_core_file
                connectivity_report = connected_core_connectivity_report
                connectivity_quality = dict(connected_core_quality)
                connectivity_quality["network_quality"] = "connected-core"
    topology_audit_report = None
    reference_topology_audit_report: dict[str, Any] | None = None
    if run_topology_audit_after_build:
        topology_audit_report = audit_topology_fragmentation(
            net_file=net_file,
            output_dir=output_dir / "topology_audit",
            prefix=f"{prefix}_topology_audit",
            cluster_radius_m=topology_cluster_radius_m,
            min_cluster_nodes=topology_min_cluster_nodes,
            osm_file=osm_file,
        )
    if (
        topology_audit_report is not None
        and run_junction_aggregation_after_build
        and str(network_plan.get("network_profile", "")) != "reference_matched"
        and _junction_aggregation_summary(topology_audit_report)["junction_aggregation_candidate_count"] > 0
    ):
        junction_aggregation_report = build_junction_aggregation_variant(
            net_file=net_file,
            output_dir=output_dir / "junction_aggregation",
            prefix=f"{prefix}_junction_aggregation",
            topology_audit_report=topology_audit_report,
            reference_join_audit_report=None,
            join_dist_m=topology_cluster_radius_m,
            timeout_seconds=timeout_seconds,
        )
    if (
        str(network_plan.get("network_profile", "")) == "reference_matched"
        and reference_net_file is not None
        and run_reference_hierarchy_audit_after_build
    ):
        reference_hierarchy_audit_candidate_net_file = reference_visual_detail_comparison_net_file or reference_visual_detail_net_file or net_file
        reference_hierarchy_audit_candidate_layer = (
            "reference_visual_detail"
            if reference_visual_detail_comparison_net_file is not None or reference_visual_detail_net_file is not None
            else "vehicle_core"
        )
        reference_hierarchy_audit_report = audit_reference_hierarchy(
            reference_net_file=reference_net_file,
            candidate_net_file=reference_hierarchy_audit_candidate_net_file,
            output_dir=output_dir / "reference_hierarchy_audit",
            prefix=f"{prefix}_reference_hierarchy_audit",
            resolve_equivalent_fragmentation=True,
        )
    _reference_matched_gate_section_result = _workflow_reference_matched_gate_section(
        command_runner=run_command,
        net_file=net_file,
        network_plan=network_plan,
        output_dir=output_dir,
        prefix=prefix,
        reference_net_file=reference_net_file,
        reference_scope_audit_func=audit_reference_scope,
        reference_visual_detail_net_file=reference_visual_detail_net_file,
        run_reference_scope_audit_after_build=run_reference_scope_audit_after_build,
        run_scope_pruning_after_build=run_scope_pruning_after_build,
        scope_pruning_func=build_scope_pruning_variant,
        sumo_binary=sumo_binary,
        timeout_seconds=timeout_seconds,
        reference_scope_audit_report=reference_scope_audit_report,
        reference_scope_candidate_net_file=reference_scope_candidate_net_file,
        reference_scope_post_prune_audit_report=reference_scope_post_prune_audit_report,
        reference_scope_pruning_promotion_report=reference_scope_pruning_promotion_report,
        reference_scope_pruning_report=reference_scope_pruning_report,
        reference_scope_pruning_sumo_load_report=reference_scope_pruning_sumo_load_report,
        reference_visual_detail_comparison_net_file=reference_visual_detail_comparison_net_file,
    )
    reference_scope_audit_report = _reference_matched_gate_section_result['reference_scope_audit_report']
    reference_scope_candidate_net_file = _reference_matched_gate_section_result['reference_scope_candidate_net_file']
    reference_scope_post_prune_audit_report = _reference_matched_gate_section_result['reference_scope_post_prune_audit_report']
    reference_scope_pruning_promotion_report = _reference_matched_gate_section_result['reference_scope_pruning_promotion_report']
    reference_scope_pruning_report = _reference_matched_gate_section_result['reference_scope_pruning_report']
    reference_scope_pruning_sumo_load_report = _reference_matched_gate_section_result['reference_scope_pruning_sumo_load_report']
    reference_visual_detail_comparison_net_file = _reference_matched_gate_section_result['reference_visual_detail_comparison_net_file']
    if _reference_matched_gate_section_result['reference_scope_candidate_layer'] is not _WORKFLOW_UNSET:
        reference_scope_candidate_layer = _reference_matched_gate_section_result['reference_scope_candidate_layer']
    if _reference_matched_gate_section_result['reference_visual_detail_comparison_selection_reason'] is not _WORKFLOW_UNSET:
        reference_visual_detail_comparison_selection_reason = _reference_matched_gate_section_result['reference_visual_detail_comparison_selection_reason']
    _reference_matched_section_result = _workflow_reference_matched_section(
        command_runner=run_command,
        net_file=net_file,
        netconvert_binary=netconvert_binary,
        network_plan=network_plan,
        output_dir=output_dir,
        post_teacher_tls_low_vehicle_control_candidates=post_teacher_tls_low_vehicle_control_candidates,
        prefix=prefix,
        reference_join_aggregation_func=build_junction_aggregation_variant,
        reference_join_audit_func=audit_reference_join_patterns,
        reference_join_audit_structural_only=reference_join_audit_structural_only,
        reference_net_file=reference_net_file,
        reference_visual_detail_net_file=reference_visual_detail_net_file,
        reference_visual_detail_raw_reference_delta_report=reference_visual_detail_raw_reference_delta_report,
        reference_visual_detail_tls_aggregation_reference_delta_report=reference_visual_detail_tls_aggregation_reference_delta_report,
        reference_visual_detail_tls_connection_repair_reference_delta_report=reference_visual_detail_tls_connection_repair_reference_delta_report,
        reference_visual_detail_tls_low_vehicle_control_reference_delta_report=reference_visual_detail_tls_low_vehicle_control_reference_delta_report,
        reference_visual_detail_tls_signal_grouping_reference_delta_report=reference_visual_detail_tls_signal_grouping_reference_delta_report,
        road_connection_topology_replay_func=_run_road_connection_topology_replay,
        road_connectivity_probe_edge_ids=road_connectivity_probe_edge_ids,
        road_connectivity_replay_func=_run_owner_road_connectivity_replay,
        road_connectivity_replay_max_owners=road_connectivity_replay_max_owners,
        road_connectivity_seed_probe_func=_run_road_connectivity_seed_probe,
        run_reference_join_aggregation_after_build=run_reference_join_aggregation_after_build,
        run_reference_join_audit_after_build=run_reference_join_audit_after_build,
        run_teacher_guided_repair_after_build=run_teacher_guided_repair_after_build,
        run_tls_aggregation_after_build=run_tls_aggregation_after_build,
        sumo_binary=sumo_binary,
        teacher_guided_direct_replay_func=_run_direct_local_teacher_replay,
        teacher_guided_plain_export_func=export_plain_net_for_teacher_guided_repair,
        teacher_guided_probe_matrix_func=run_teacher_guided_repair_matrix,
        teacher_guided_probe_matrix_junction_ids=teacher_guided_probe_matrix_junction_ids,
        teacher_guided_repair_max_ready_candidates=teacher_guided_repair_max_ready_candidates,
        teacher_guided_repair_queue_func=build_teacher_guided_repair_queue,
        teacher_guided_repair_run_func=run_teacher_guided_repair_queue,
        timeout_seconds=timeout_seconds,
        tls_connection_repair_func=build_tls_connection_repair_variant,
        tls_low_vehicle_control_func=build_tls_low_vehicle_control_variant,
        tls_non_controller_junction_demotion_func=build_tls_non_controller_junction_demotion_variant,
        tls_signal_grouping_func=build_tls_signal_grouping_variant,
        topology_audit_report=topology_audit_report,
        topology_cluster_radius_m=topology_cluster_radius_m,
        topology_min_cluster_nodes=topology_min_cluster_nodes,
        post_teacher_tls_connection_repair_movement_rebuild_best_variant_file=post_teacher_tls_connection_repair_movement_rebuild_best_variant_file,
        post_teacher_tls_connection_repair_movement_rebuild_queue_report=post_teacher_tls_connection_repair_movement_rebuild_queue_report,
        post_teacher_tls_connection_repair_movement_rebuild_run_report=post_teacher_tls_connection_repair_movement_rebuild_run_report,
        post_teacher_tls_connection_repair_reference_delta_report=post_teacher_tls_connection_repair_reference_delta_report,
        post_teacher_tls_connection_repair_reference_promotion_report=post_teacher_tls_connection_repair_reference_promotion_report,
        post_teacher_tls_connection_repair_report=post_teacher_tls_connection_repair_report,
        post_teacher_tls_connection_repair_sumo_load_report=post_teacher_tls_connection_repair_sumo_load_report,
        post_teacher_tls_low_vehicle_control_reference_delta_report=post_teacher_tls_low_vehicle_control_reference_delta_report,
        post_teacher_tls_low_vehicle_control_reference_promotion_report=post_teacher_tls_low_vehicle_control_reference_promotion_report,
        post_teacher_tls_non_controller_junction_demotion_reference_delta_report=post_teacher_tls_non_controller_junction_demotion_reference_delta_report,
        post_teacher_tls_non_controller_junction_demotion_reference_promotion_report=post_teacher_tls_non_controller_junction_demotion_reference_promotion_report,
        post_teacher_tls_non_controller_junction_demotion_report=post_teacher_tls_non_controller_junction_demotion_report,
        post_teacher_tls_non_controller_junction_demotion_sumo_load_report=post_teacher_tls_non_controller_junction_demotion_sumo_load_report,
        post_teacher_tls_signal_grouping_reference_delta_report=post_teacher_tls_signal_grouping_reference_delta_report,
        post_teacher_tls_signal_grouping_reference_promotion_report=post_teacher_tls_signal_grouping_reference_promotion_report,
        post_teacher_tls_signal_grouping_report=post_teacher_tls_signal_grouping_report,
        post_teacher_tls_signal_grouping_sumo_load_report=post_teacher_tls_signal_grouping_sumo_load_report,
        reference_join_aggregation_report=reference_join_aggregation_report,
        reference_join_audit_candidate_net_file=reference_join_audit_candidate_net_file,
        reference_join_audit_report=reference_join_audit_report,
        reference_join_post_teacher_audit_report=reference_join_post_teacher_audit_report,
        reference_visual_detail_comparison_net_file=reference_visual_detail_comparison_net_file,
        road_connection_topology_replay_report=road_connection_topology_replay_report,
        road_connectivity_replay_report=road_connectivity_replay_report,
        road_connectivity_seed_probe_report=road_connectivity_seed_probe_report,
        road_connectivity_split_root_alias_repair_report=road_connectivity_split_root_alias_repair_report,
        teacher_guided_direct_replay_reference_delta_report=teacher_guided_direct_replay_reference_delta_report,
        teacher_guided_direct_replay_reference_promotion_report=teacher_guided_direct_replay_reference_promotion_report,
        teacher_guided_direct_replay_report=teacher_guided_direct_replay_report,
        teacher_guided_plain_export_report=teacher_guided_plain_export_report,
        teacher_guided_repair_best_variant_file=teacher_guided_repair_best_variant_file,
        teacher_guided_repair_queue_report=teacher_guided_repair_queue_report,
        teacher_guided_repair_reference_promotion_report=teacher_guided_repair_reference_promotion_report,
        teacher_guided_repair_requires_reference_promotion=teacher_guided_repair_requires_reference_promotion,
        teacher_guided_repair_run_report=teacher_guided_repair_run_report,
        teacher_guided_scoped_tls_cell_batch_report=teacher_guided_scoped_tls_cell_batch_report,
        teacher_guided_seed_report=teacher_guided_seed_report,
        tls_gap_destination_mapping_report=tls_gap_destination_mapping_report,
        tls_repair_variant_reference_audit_report=tls_repair_variant_reference_audit_report,
        tls_repair_variant_report=tls_repair_variant_report,
        tls_repair_variant_semantic_report=tls_repair_variant_semantic_report,
        tls_repair_variant_sumo_load_report=tls_repair_variant_sumo_load_report,
    )
    post_teacher_tls_connection_repair_movement_rebuild_best_variant_file = _reference_matched_section_result['post_teacher_tls_connection_repair_movement_rebuild_best_variant_file']
    post_teacher_tls_connection_repair_movement_rebuild_queue_report = _reference_matched_section_result['post_teacher_tls_connection_repair_movement_rebuild_queue_report']
    post_teacher_tls_connection_repair_movement_rebuild_run_report = _reference_matched_section_result['post_teacher_tls_connection_repair_movement_rebuild_run_report']
    post_teacher_tls_connection_repair_reference_delta_report = _reference_matched_section_result['post_teacher_tls_connection_repair_reference_delta_report']
    post_teacher_tls_connection_repair_reference_promotion_report = _reference_matched_section_result['post_teacher_tls_connection_repair_reference_promotion_report']
    post_teacher_tls_connection_repair_report = _reference_matched_section_result['post_teacher_tls_connection_repair_report']
    post_teacher_tls_connection_repair_sumo_load_report = _reference_matched_section_result['post_teacher_tls_connection_repair_sumo_load_report']
    post_teacher_tls_low_vehicle_control_reference_delta_report = _reference_matched_section_result['post_teacher_tls_low_vehicle_control_reference_delta_report']
    post_teacher_tls_low_vehicle_control_reference_promotion_report = _reference_matched_section_result['post_teacher_tls_low_vehicle_control_reference_promotion_report']
    post_teacher_tls_non_controller_junction_demotion_reference_delta_report = _reference_matched_section_result['post_teacher_tls_non_controller_junction_demotion_reference_delta_report']
    post_teacher_tls_non_controller_junction_demotion_reference_promotion_report = _reference_matched_section_result['post_teacher_tls_non_controller_junction_demotion_reference_promotion_report']
    post_teacher_tls_non_controller_junction_demotion_report = _reference_matched_section_result['post_teacher_tls_non_controller_junction_demotion_report']
    post_teacher_tls_non_controller_junction_demotion_sumo_load_report = _reference_matched_section_result['post_teacher_tls_non_controller_junction_demotion_sumo_load_report']
    post_teacher_tls_signal_grouping_reference_delta_report = _reference_matched_section_result['post_teacher_tls_signal_grouping_reference_delta_report']
    post_teacher_tls_signal_grouping_reference_promotion_report = _reference_matched_section_result['post_teacher_tls_signal_grouping_reference_promotion_report']
    post_teacher_tls_signal_grouping_report = _reference_matched_section_result['post_teacher_tls_signal_grouping_report']
    post_teacher_tls_signal_grouping_sumo_load_report = _reference_matched_section_result['post_teacher_tls_signal_grouping_sumo_load_report']
    reference_join_aggregation_report = _reference_matched_section_result['reference_join_aggregation_report']
    reference_join_audit_candidate_net_file = _reference_matched_section_result['reference_join_audit_candidate_net_file']
    reference_join_audit_report = _reference_matched_section_result['reference_join_audit_report']
    reference_join_post_teacher_audit_report = _reference_matched_section_result['reference_join_post_teacher_audit_report']
    reference_visual_detail_comparison_net_file = _reference_matched_section_result['reference_visual_detail_comparison_net_file']
    road_connection_topology_replay_report = _reference_matched_section_result['road_connection_topology_replay_report']
    road_connectivity_replay_report = _reference_matched_section_result['road_connectivity_replay_report']
    road_connectivity_seed_probe_report = _reference_matched_section_result['road_connectivity_seed_probe_report']
    road_connectivity_split_root_alias_repair_report = _reference_matched_section_result['road_connectivity_split_root_alias_repair_report']
    teacher_guided_direct_replay_reference_delta_report = _reference_matched_section_result['teacher_guided_direct_replay_reference_delta_report']
    teacher_guided_direct_replay_reference_promotion_report = _reference_matched_section_result['teacher_guided_direct_replay_reference_promotion_report']
    teacher_guided_direct_replay_report = _reference_matched_section_result['teacher_guided_direct_replay_report']
    teacher_guided_plain_export_report = _reference_matched_section_result['teacher_guided_plain_export_report']
    teacher_guided_repair_best_variant_file = _reference_matched_section_result['teacher_guided_repair_best_variant_file']
    teacher_guided_repair_queue_report = _reference_matched_section_result['teacher_guided_repair_queue_report']
    teacher_guided_repair_reference_promotion_report = _reference_matched_section_result['teacher_guided_repair_reference_promotion_report']
    teacher_guided_repair_requires_reference_promotion = _reference_matched_section_result['teacher_guided_repair_requires_reference_promotion']
    teacher_guided_repair_run_report = _reference_matched_section_result['teacher_guided_repair_run_report']
    teacher_guided_scoped_tls_cell_batch_report = _reference_matched_section_result['teacher_guided_scoped_tls_cell_batch_report']
    teacher_guided_seed_report = _reference_matched_section_result['teacher_guided_seed_report']
    tls_gap_destination_mapping_report = _reference_matched_section_result['tls_gap_destination_mapping_report']
    tls_repair_variant_reference_audit_report = _reference_matched_section_result['tls_repair_variant_reference_audit_report']
    tls_repair_variant_report = _reference_matched_section_result['tls_repair_variant_report']
    tls_repair_variant_semantic_report = _reference_matched_section_result['tls_repair_variant_semantic_report']
    tls_repair_variant_sumo_load_report = _reference_matched_section_result['tls_repair_variant_sumo_load_report']
    if _reference_matched_section_result['post_teacher_tls_connection_repair_movement_rebuild_plain_export_report'] is not _WORKFLOW_UNSET:
        post_teacher_tls_connection_repair_movement_rebuild_plain_export_report = _reference_matched_section_result['post_teacher_tls_connection_repair_movement_rebuild_plain_export_report']
    if _reference_matched_section_result['post_teacher_tls_low_vehicle_control_report'] is not _WORKFLOW_UNSET:
        post_teacher_tls_low_vehicle_control_report = _reference_matched_section_result['post_teacher_tls_low_vehicle_control_report']
    if _reference_matched_section_result['post_teacher_tls_low_vehicle_control_sumo_load_report'] is not _WORKFLOW_UNSET:
        post_teacher_tls_low_vehicle_control_sumo_load_report = _reference_matched_section_result['post_teacher_tls_low_vehicle_control_sumo_load_report']
    if _reference_matched_section_result['reference_join_audit_candidate_layer'] is not _WORKFLOW_UNSET:
        reference_join_audit_candidate_layer = _reference_matched_section_result['reference_join_audit_candidate_layer']
    if _reference_matched_section_result['reference_visual_detail_comparison_selection_reason'] is not _WORKFLOW_UNSET:
        reference_visual_detail_comparison_selection_reason = _reference_matched_section_result['reference_visual_detail_comparison_selection_reason']
    if _reference_matched_section_result['teacher_guided_direct_replay_best_variant_file'] is not _WORKFLOW_UNSET:
        teacher_guided_direct_replay_best_variant_file = _reference_matched_section_result['teacher_guided_direct_replay_best_variant_file']
    if _reference_matched_section_result['teacher_guided_probe_matrix_report'] is not _WORKFLOW_UNSET:
        teacher_guided_probe_matrix_report = _reference_matched_section_result['teacher_guided_probe_matrix_report']
    if _reference_matched_section_result['teacher_guided_repair_best_expanded_scope_net_file'] is not _WORKFLOW_UNSET:
        teacher_guided_repair_best_expanded_scope_net_file = _reference_matched_section_result['teacher_guided_repair_best_expanded_scope_net_file']
    if _reference_matched_section_result['teacher_guided_repair_seed_source'] is not _WORKFLOW_UNSET:
        teacher_guided_repair_seed_source = _reference_matched_section_result['teacher_guided_repair_seed_source']
    if _reference_matched_section_result['tls_repair_decision_report'] is not _WORKFLOW_UNSET:
        tls_repair_decision_report = _reference_matched_section_result['tls_repair_decision_report']
    _teacher_guided_repair_section_result = _workflow_teacher_guided_repair_section(
        command_runner=run_command,
        netconvert_binary=netconvert_binary,
        output_dir=output_dir,
        prefix=prefix,
        reference_join_audit_func=audit_reference_join_patterns,
        reference_join_audit_structural_only=reference_join_audit_structural_only,
        reference_net_file=reference_net_file,
        run_teacher_guided_repair_after_build=run_teacher_guided_repair_after_build,
        sumo_binary=sumo_binary,
        teacher_guided_direct_replay_func=_run_direct_local_teacher_replay,
        teacher_guided_plain_export_func=export_plain_net_for_teacher_guided_repair,
        teacher_guided_repair_max_ready_candidates=teacher_guided_repair_max_ready_candidates,
        teacher_guided_repair_queue_func=build_teacher_guided_repair_queue,
        teacher_guided_repair_requires_reference_promotion=teacher_guided_repair_requires_reference_promotion,
        teacher_guided_repair_run_func=run_teacher_guided_repair_queue,
        teacher_guided_seed_report=teacher_guided_seed_report,
        timeout_seconds=timeout_seconds,
        topology_cluster_radius_m=topology_cluster_radius_m,
        topology_min_cluster_nodes=topology_min_cluster_nodes,
        final_movement_rebuild_best_variant_file=final_movement_rebuild_best_variant_file,
        final_movement_rebuild_internal_regression_restore_promotion_report=final_movement_rebuild_internal_regression_restore_promotion_report,
        final_movement_rebuild_internal_regression_restore_reference_delta_report=final_movement_rebuild_internal_regression_restore_reference_delta_report,
        final_movement_rebuild_internal_regression_restore_report=final_movement_rebuild_internal_regression_restore_report,
        final_movement_rebuild_internal_regression_restore_sumo_load_report=final_movement_rebuild_internal_regression_restore_sumo_load_report,
        final_movement_rebuild_queue_report=final_movement_rebuild_queue_report,
        final_movement_rebuild_reference_delta_report=final_movement_rebuild_reference_delta_report,
        final_movement_rebuild_reference_promotion_report=final_movement_rebuild_reference_promotion_report,
        final_movement_rebuild_run_report=final_movement_rebuild_run_report,
        final_movement_rebuild_sumo_load_report=final_movement_rebuild_sumo_load_report,
        reference_join_post_teacher_audit_report=reference_join_post_teacher_audit_report,
        reference_visual_detail_comparison_net_file=reference_visual_detail_comparison_net_file,
    )
    final_movement_rebuild_best_variant_file = _teacher_guided_repair_section_result['final_movement_rebuild_best_variant_file']
    final_movement_rebuild_internal_regression_restore_promotion_report = _teacher_guided_repair_section_result['final_movement_rebuild_internal_regression_restore_promotion_report']
    final_movement_rebuild_internal_regression_restore_reference_delta_report = _teacher_guided_repair_section_result['final_movement_rebuild_internal_regression_restore_reference_delta_report']
    final_movement_rebuild_internal_regression_restore_report = _teacher_guided_repair_section_result['final_movement_rebuild_internal_regression_restore_report']
    final_movement_rebuild_internal_regression_restore_sumo_load_report = _teacher_guided_repair_section_result['final_movement_rebuild_internal_regression_restore_sumo_load_report']
    final_movement_rebuild_queue_report = _teacher_guided_repair_section_result['final_movement_rebuild_queue_report']
    final_movement_rebuild_reference_delta_report = _teacher_guided_repair_section_result['final_movement_rebuild_reference_delta_report']
    final_movement_rebuild_reference_promotion_report = _teacher_guided_repair_section_result['final_movement_rebuild_reference_promotion_report']
    final_movement_rebuild_run_report = _teacher_guided_repair_section_result['final_movement_rebuild_run_report']
    final_movement_rebuild_sumo_load_report = _teacher_guided_repair_section_result['final_movement_rebuild_sumo_load_report']
    reference_join_post_teacher_audit_report = _teacher_guided_repair_section_result['reference_join_post_teacher_audit_report']
    reference_visual_detail_comparison_net_file = _teacher_guided_repair_section_result['reference_visual_detail_comparison_net_file']
    if _teacher_guided_repair_section_result['final_movement_direct_replay_best_variant_file'] is not _WORKFLOW_UNSET:
        final_movement_direct_replay_best_variant_file = _teacher_guided_repair_section_result['final_movement_direct_replay_best_variant_file']
    if _teacher_guided_repair_section_result['final_movement_direct_replay_last_queue_report'] is not _WORKFLOW_UNSET:
        final_movement_direct_replay_last_queue_report = _teacher_guided_repair_section_result['final_movement_direct_replay_last_queue_report']
    if _teacher_guided_repair_section_result['final_movement_direct_replay_reference_delta_report'] is not _WORKFLOW_UNSET:
        final_movement_direct_replay_reference_delta_report = _teacher_guided_repair_section_result['final_movement_direct_replay_reference_delta_report']
    if _teacher_guided_repair_section_result['final_movement_direct_replay_reference_promotion_report'] is not _WORKFLOW_UNSET:
        final_movement_direct_replay_reference_promotion_report = _teacher_guided_repair_section_result['final_movement_direct_replay_reference_promotion_report']
    if _teacher_guided_repair_section_result['final_movement_direct_replay_report'] is not _WORKFLOW_UNSET:
        final_movement_direct_replay_report = _teacher_guided_repair_section_result['final_movement_direct_replay_report']
    if _teacher_guided_repair_section_result['final_movement_rebuild_plain_export_report'] is not _WORKFLOW_UNSET:
        final_movement_rebuild_plain_export_report = _teacher_guided_repair_section_result['final_movement_rebuild_plain_export_report']
    if _teacher_guided_repair_section_result['reference_visual_detail_comparison_selection_reason'] is not _WORKFLOW_UNSET:
        reference_visual_detail_comparison_selection_reason = _teacher_guided_repair_section_result['reference_visual_detail_comparison_selection_reason']
    _reference_comparison_section_result = _workflow_reference_comparison_section(
        command_runner=run_command,
        corridor_geometry_simplification_func=build_corridor_geometry_simplification_variant,
        network_plan=network_plan,
        osm_file=osm_file,
        output_dir=output_dir,
        prefix=prefix,
        reference_hierarchy_audit_func=audit_reference_hierarchy,
        reference_hierarchy_type_repair_func=build_reference_hierarchy_type_repair_variant,
        reference_join_audit_func=audit_reference_join_patterns,
        reference_join_post_teacher_audit_report=reference_join_post_teacher_audit_report,
        reference_net_file=reference_net_file,
        reference_scope_audit_func=audit_reference_scope,
        reference_visual_detail_raw_reference_delta_report=reference_visual_detail_raw_reference_delta_report,
        reference_visual_detail_tls_connection_repair_reference_delta_report=reference_visual_detail_tls_connection_repair_reference_delta_report,
        run_corridor_geometry_simplification_after_build=run_corridor_geometry_simplification_after_build,
        run_reference_hierarchy_audit_after_build=run_reference_hierarchy_audit_after_build,
        run_reference_hierarchy_type_repair_after_build=run_reference_hierarchy_type_repair_after_build,
        run_topology_audit_after_build=run_topology_audit_after_build,
        sumo_binary=sumo_binary,
        timeout_seconds=timeout_seconds,
        topology_audit_func=audit_topology_fragmentation,
        topology_cluster_radius_m=topology_cluster_radius_m,
        topology_min_cluster_nodes=topology_min_cluster_nodes,
        corridor_geometry_simplification_promotion_report=corridor_geometry_simplification_promotion_report,
        corridor_geometry_simplification_reference_delta_report=corridor_geometry_simplification_reference_delta_report,
        corridor_geometry_simplification_report=corridor_geometry_simplification_report,
        corridor_geometry_simplification_sumo_load_report=corridor_geometry_simplification_sumo_load_report,
        corridor_geometry_simplification_topology_report=corridor_geometry_simplification_topology_report,
        reference_hierarchy_audit_candidate_net_file=reference_hierarchy_audit_candidate_net_file,
        reference_hierarchy_audit_report=reference_hierarchy_audit_report,
        reference_hierarchy_type_repair_audit_report=reference_hierarchy_type_repair_audit_report,
        reference_hierarchy_type_repair_promotion_report=reference_hierarchy_type_repair_promotion_report,
        reference_hierarchy_type_repair_report=reference_hierarchy_type_repair_report,
        reference_hierarchy_type_repair_sumo_load_report=reference_hierarchy_type_repair_sumo_load_report,
        reference_visual_detail_comparison_net_file=reference_visual_detail_comparison_net_file,
        topology_audit_report=topology_audit_report,
    )
    corridor_geometry_simplification_promotion_report = _reference_comparison_section_result['corridor_geometry_simplification_promotion_report']
    corridor_geometry_simplification_reference_delta_report = _reference_comparison_section_result['corridor_geometry_simplification_reference_delta_report']
    corridor_geometry_simplification_report = _reference_comparison_section_result['corridor_geometry_simplification_report']
    corridor_geometry_simplification_sumo_load_report = _reference_comparison_section_result['corridor_geometry_simplification_sumo_load_report']
    corridor_geometry_simplification_topology_report = _reference_comparison_section_result['corridor_geometry_simplification_topology_report']
    reference_hierarchy_audit_candidate_net_file = _reference_comparison_section_result['reference_hierarchy_audit_candidate_net_file']
    reference_hierarchy_audit_report = _reference_comparison_section_result['reference_hierarchy_audit_report']
    reference_hierarchy_type_repair_audit_report = _reference_comparison_section_result['reference_hierarchy_type_repair_audit_report']
    reference_hierarchy_type_repair_promotion_report = _reference_comparison_section_result['reference_hierarchy_type_repair_promotion_report']
    reference_hierarchy_type_repair_report = _reference_comparison_section_result['reference_hierarchy_type_repair_report']
    reference_hierarchy_type_repair_sumo_load_report = _reference_comparison_section_result['reference_hierarchy_type_repair_sumo_load_report']
    reference_visual_detail_comparison_net_file = _reference_comparison_section_result['reference_visual_detail_comparison_net_file']
    topology_audit_report = _reference_comparison_section_result['topology_audit_report']
    if _reference_comparison_section_result['reference_hierarchy_audit_candidate_layer'] is not _WORKFLOW_UNSET:
        reference_hierarchy_audit_candidate_layer = _reference_comparison_section_result['reference_hierarchy_audit_candidate_layer']
    if _reference_comparison_section_result['reference_scope_audit_report'] is not _WORKFLOW_UNSET:
        reference_scope_audit_report = _reference_comparison_section_result['reference_scope_audit_report']
    if _reference_comparison_section_result['reference_scope_candidate_net_file'] is not _WORKFLOW_UNSET:
        reference_scope_candidate_net_file = _reference_comparison_section_result['reference_scope_candidate_net_file']
    if _reference_comparison_section_result['reference_topology_audit_report'] is not _WORKFLOW_UNSET:
        reference_topology_audit_report = _reference_comparison_section_result['reference_topology_audit_report']
    if _reference_comparison_section_result['reference_visual_detail_comparison_selection_reason'] is not _WORKFLOW_UNSET:
        reference_visual_detail_comparison_selection_reason = _reference_comparison_section_result['reference_visual_detail_comparison_selection_reason']
    _scope_pruning_section_result = _workflow_scope_pruning_section(
        command_runner=run_command,
        network_plan=network_plan,
        osm_file=osm_file,
        output_dir=output_dir,
        prefix=prefix,
        reference_hierarchy_audit_func=audit_reference_hierarchy,
        reference_net_file=reference_net_file,
        reference_scope_audit_func=audit_reference_scope,
        run_scope_pruning_after_build=run_scope_pruning_after_build,
        run_topology_audit_after_build=run_topology_audit_after_build,
        scope_pruning_func=build_scope_pruning_variant,
        sumo_binary=sumo_binary,
        timeout_seconds=timeout_seconds,
        topology_audit_func=audit_topology_fragmentation,
        topology_cluster_radius_m=topology_cluster_radius_m,
        topology_min_cluster_nodes=topology_min_cluster_nodes,
        reference_scope_candidate_net_file=reference_scope_candidate_net_file,
        reference_scope_final_audit_report=reference_scope_final_audit_report,
        reference_scope_final_post_prune_audit_report=reference_scope_final_post_prune_audit_report,
        reference_scope_final_promotion_report=reference_scope_final_promotion_report,
        reference_scope_final_pruning_report=reference_scope_final_pruning_report,
        reference_scope_final_sumo_load_report=reference_scope_final_sumo_load_report,
        reference_scope_pruning_report=reference_scope_pruning_report,
        reference_visual_detail_comparison_net_file=reference_visual_detail_comparison_net_file,
    )
    reference_scope_candidate_net_file = _scope_pruning_section_result['reference_scope_candidate_net_file']
    reference_scope_final_audit_report = _scope_pruning_section_result['reference_scope_final_audit_report']
    reference_scope_final_post_prune_audit_report = _scope_pruning_section_result['reference_scope_final_post_prune_audit_report']
    reference_scope_final_promotion_report = _scope_pruning_section_result['reference_scope_final_promotion_report']
    reference_scope_final_pruning_report = _scope_pruning_section_result['reference_scope_final_pruning_report']
    reference_scope_final_sumo_load_report = _scope_pruning_section_result['reference_scope_final_sumo_load_report']
    reference_scope_pruning_report = _scope_pruning_section_result['reference_scope_pruning_report']
    reference_visual_detail_comparison_net_file = _scope_pruning_section_result['reference_visual_detail_comparison_net_file']
    if _scope_pruning_section_result['reference_hierarchy_audit_candidate_net_file'] is not _WORKFLOW_UNSET:
        reference_hierarchy_audit_candidate_net_file = _scope_pruning_section_result['reference_hierarchy_audit_candidate_net_file']
    if _scope_pruning_section_result['reference_hierarchy_audit_report'] is not _WORKFLOW_UNSET:
        reference_hierarchy_audit_report = _scope_pruning_section_result['reference_hierarchy_audit_report']
    if _scope_pruning_section_result['reference_scope_audit_report'] is not _WORKFLOW_UNSET:
        reference_scope_audit_report = _scope_pruning_section_result['reference_scope_audit_report']
    if _scope_pruning_section_result['reference_scope_candidate_layer'] is not _WORKFLOW_UNSET:
        reference_scope_candidate_layer = _scope_pruning_section_result['reference_scope_candidate_layer']
    if _scope_pruning_section_result['reference_scope_pruning_promotion_report'] is not _WORKFLOW_UNSET:
        reference_scope_pruning_promotion_report = _scope_pruning_section_result['reference_scope_pruning_promotion_report']
    if _scope_pruning_section_result['reference_visual_detail_comparison_selection_reason'] is not _WORKFLOW_UNSET:
        reference_visual_detail_comparison_selection_reason = _scope_pruning_section_result['reference_visual_detail_comparison_selection_reason']
    if _scope_pruning_section_result['topology_audit_report'] is not _WORKFLOW_UNSET:
        topology_audit_report = _scope_pruning_section_result['topology_audit_report']
    if (
        run_road_connectivity_parity_audit_after_build
        and str(network_plan.get("network_profile", "")) == "reference_matched"
        and reference_net_file is not None
        and _reference_bbox_scope_gate(reference_bbox_scope_report) == "pass"
    ):
        road_parity_candidate_net_file = (
            reference_visual_detail_comparison_net_file
            or reference_visual_detail_net_file
            or net_file
        )
        if road_parity_candidate_net_file is not None and road_parity_candidate_net_file.exists():
            road_parity_kwargs: dict[str, Any] = {
                "teacher_net_file": reference_net_file,
                "candidate_net_file": road_parity_candidate_net_file,
                "output_dir": output_dir / "road_connectivity_parity_audit",
                "prefix": f"{prefix}_road_connectivity_parity",
                "max_examples": 20,
            }
            visual_source_value = (
                reference_visual_detail_build_report.get("filtered_osm_file")
                or reference_visual_detail_build_report.get("source_osm_file")
                if isinstance(reference_visual_detail_build_report, Mapping)
                else ""
            )
            visual_source_path = Path(str(visual_source_value)) if visual_source_value else None
            if visual_source_path is not None and visual_source_path.exists() and _supports_keyword(
                audit_road_connectivity_parity,
                "source_osm_file",
            ):
                road_parity_kwargs["source_osm_file"] = visual_source_path
            road_connectivity_parity_audit_report = audit_road_connectivity_parity(**road_parity_kwargs)
        else:
            road_connectivity_parity_audit_report = {
                "status": "blocked",
                "claim_status": "reference-audit",
                "blocking_reason": "road_connectivity_parity_candidate_missing",
                "teacher_net_file": str(reference_net_file),
                "candidate_net_file": "",
                "warnings": [],
            }
    routeability_report = None
    if key_edge_queries:
        routeability_report = build_routeability_probe(
            net_file=net_file,
            output_dir=output_dir / "routeability",
            prefix=f"{prefix}_routeability",
            key_edge_queries=key_edge_queries,
        )
    routeability_audit_report = None
    routeability_profile = _routeability_scale_profile(
        connectivity_report,
        requested_vehicle_count=routeability_vehicle_count,
        requested_initial_end=routeability_initial_end,
        requested_max_end=routeability_max_end,
    )
    if run_routeability_audit_after_build:
        routeability_audit_report = run_routeability_audit(
            net_file=net_file,
            output_dir=output_dir / "routeability_audit",
            prefix=f"{prefix}_routeability_audit",
            vehicle_count=routeability_profile["routeability_audit_vehicle_count"],
            initial_end=routeability_profile["routeability_audit_initial_end"],
            max_end=routeability_profile["routeability_audit_max_end"],
            timeout_seconds=timeout_seconds,
        )
    connection_mode_audit_report = None
    if run_connection_mode_audit_after_build:
        connection_mode_audit_report = build_network_connection_mode_audit(
            net_file,
            output_dir=output_dir / "connection_mode_audit",
            prefix=f"{prefix}_connection_mode",
        )
    standard_nema_scan_report = None
    if run_standard_nema_scan_after_build:
        standard_nema_scan_report = build_standard_nema_phase_binding(
            net_file,
            output_dir=output_dir / "standard_nema_review",
            prefix=f"{prefix}_standard_nema",
            junction_id=None,
            run_runtime_checks=False,
            run_routeability=False,
        )
    if launch_netedit_after_build:
        netedit_report = launch_netedit(net_file)
    else:
        netedit_report = {
            "status": "blocked",
            "claim_status": "diagnostic-demo",
            "netedit_status": "skipped",
            "netedit_network_file": str(net_file),
            "warnings": ["netedit launch disabled by caller"],
        }
    if reference_visual_detail_comparison_net_file is not None:
        if launch_netedit_after_build:
            reference_visual_detail_netedit_report = launch_netedit(reference_visual_detail_comparison_net_file)
        else:
            reference_visual_detail_netedit_report = {
                "status": "blocked",
                "claim_status": "diagnostic-demo",
                "netedit_status": "skipped",
                "netedit_network_file": str(reference_visual_detail_comparison_net_file),
                "warnings": ["reference visual-detail netedit launch disabled by caller"],
            }
    if launch_sumo_gui_after_build:
        sumo_gui_report = launch_sumo_gui(
            net_file,
            output_dir=output_dir / "sumo_gui",
            prefix=f"{prefix}_sumo_gui",
        )
    else:
        sumo_gui_report = {
            "status": "blocked",
            "claim_status": "diagnostic-demo",
            "sumo_gui_status": "skipped",
            "sumo_gui_binary": None,
            "sumo_gui_process_id": None,
            "sumo_gui_config_file": "",
            "sumo_gui_network_file": str(net_file),
            "warnings": ["sumo-gui launch disabled by caller"],
        }
    supplied_review_decisions, review_decisions_source_status, review_decisions_source_error = (
        _load_review_decisions_file(review_decisions_file)
    )
    tls_summary = _tls_review_summary(tls_report, review_decisions=supplied_review_decisions)
    junction_aggregation_summary = _junction_aggregation_summary(topology_audit_report)
    topology_reference_parity_report = (
        _reference_topology_parity_gate(topology_audit_report, reference_topology_audit_report)
        if str(network_plan.get("network_profile", "")) == "reference_matched"
        else {"status": "skipped", "reason": "not_reference_matched", "metrics": {}}
    )
    warnings = []
    for child in (
        reference_bbox_report or {},
        build_report,
        service_permission_report,
        reference_visual_detail_build_report,
        reference_visual_detail_service_permission_report,
        tls_report,
        tls_aggregation_report or {},
        reference_visual_detail_tls_report or {},
        reference_visual_detail_tls_aggregation_report or {},
        reference_visual_detail_tls_signal_grouping_report or {},
        reference_visual_detail_tls_low_vehicle_control_report or {},
        post_teacher_tls_low_vehicle_control_report or {},
        post_teacher_tls_signal_grouping_report or {},
        post_teacher_tls_non_controller_junction_demotion_report or {},
        post_teacher_tls_connection_repair_report or {},
        reference_visual_detail_tls_connection_repair_report or {},
        reference_visual_detail_raw_reference_delta_report or {},
        reference_visual_detail_tls_signal_grouping_sumo_load_report or {},
        reference_visual_detail_tls_low_vehicle_control_sumo_load_report or {},
        post_teacher_tls_low_vehicle_control_sumo_load_report or {},
        post_teacher_tls_signal_grouping_sumo_load_report or {},
        post_teacher_tls_non_controller_junction_demotion_sumo_load_report or {},
        post_teacher_tls_connection_repair_sumo_load_report or {},
        reference_visual_detail_tls_connection_repair_sumo_load_report or {},
        reference_visual_detail_tls_low_vehicle_control_reference_delta_report or {},
        post_teacher_tls_low_vehicle_control_reference_delta_report or {},
        post_teacher_tls_signal_grouping_reference_delta_report or {},
        post_teacher_tls_non_controller_junction_demotion_reference_delta_report or {},
        post_teacher_tls_connection_repair_reference_delta_report or {},
        post_teacher_tls_connection_repair_movement_rebuild_queue_report or {},
        post_teacher_tls_connection_repair_movement_rebuild_plain_export_report or {},
        post_teacher_tls_connection_repair_movement_rebuild_run_report or {},
        reference_visual_detail_tls_connection_repair_reference_delta_report or {},
        raw_connectivity_report,
        connected_core_report or {},
        connected_core_connectivity_report or {},
        connectivity_report,
        topology_audit_report or {},
        reference_topology_audit_report or {},
        junction_aggregation_report or {},
        reference_hierarchy_audit_report or {},
        reference_scope_audit_report or {},
        reference_scope_pruning_report or {},
        reference_join_audit_report or {},
        reference_join_aggregation_report or {},
        reference_hierarchy_type_repair_report or {},
        reference_hierarchy_type_repair_sumo_load_report or {},
        reference_hierarchy_type_repair_audit_report or {},
        reference_hierarchy_type_repair_promotion_report,
        corridor_geometry_simplification_report or {},
        corridor_geometry_simplification_sumo_load_report or {},
        corridor_geometry_simplification_reference_delta_report or {},
        corridor_geometry_simplification_topology_report or {},
        corridor_geometry_simplification_promotion_report,
        routeability_audit_report or {},
        connection_mode_audit_report or {},
        standard_nema_scan_report or {},
        netedit_report,
        reference_visual_detail_netedit_report,
        sumo_gui_report,
        teacher_guided_scoped_tls_cell_batch_report or {},
    ):
        warnings.extend(str(item) for item in child.get("warnings", []))
    if (
        teacher_guided_scoped_tls_cell_batch_report is not None
        and teacher_guided_scoped_tls_cell_batch_report.get("status") != "pass"
    ):
        warnings.append(
            "scoped TLS cell batch contains blocked cells; keep them as review variants until pedestrian/internal "
            "link semantics and global reference gates pass"
        )
    if tls_summary["tls_review_complete"] == "no":
        warnings.append("TLS reality review still requires human Google Maps/current-or-user-targeted map inspection")
    if tls_aggregation_report is not None and tls_aggregation_report.get("tls_aggregation_status") == "variant_created_for_review":
        warnings.append(
            "TLS aggregation created a separate review variant; compare physical TLS clusters in Netedit and Google Maps "
            "before adopting it as the clean signal network"
        )
    if (
        reference_visual_detail_tls_aggregation_report is not None
        and reference_visual_detail_tls_aggregation_report.get("tls_aggregation_status") == "variant_created_for_review"
    ):
        warnings.append(
            "reference visual-detail TLS aggregation created a separate comparison variant; use it for TUM/manual-reference "
            "Netedit comparison before adopting signal cleanup"
        )
    if reference_visual_detail_tls_connection_repair_report is not None:
        warnings.append(
            "reference visual-detail TLS connection repair created a diagnostic variant; use SUMO load, reference audit, "
            "and Netedit connection mode before adopting any copied signal semantics"
        )
    if post_teacher_tls_connection_repair_report is not None:
        warnings.append(
            "post-teacher TLS connection repair copied reference signal-control semantics into a diagnostic variant; "
            "use SUMO load, reference audit, and Netedit connection mode before treating it as clean"
        )
    if connectivity_quality["quality_warning"]:
        warnings.append(str(connectivity_quality["quality_warning"]))
    if topology_audit_report is not None and topology_audit_report.get("topology_fragmentation_status") == "needs_review":
        if (
            str(network_plan.get("network_profile", "")) == "reference_matched"
            and topology_reference_parity_report.get("status") == "pass"
        ):
            warnings.append(
                "topology fragmentation remains diagnostic; candidate topology is not more fragmented than the reference"
            )
        else:
            warnings.append("topology fragmentation audit needs human review before treating the network as clean")
    if (
        reference_hierarchy_audit_report is not None
        and _int_field(reference_hierarchy_audit_report, "high_hierarchy_issue_count") > 0
    ):
        warnings.append(
            "reference hierarchy audit found high-road review cases; inspect over-split corridors, out-of-scope roads, "
            "hierarchy mismatches, and link/slip-lane cases before pruning or merging high-level roads"
        )
    if junction_aggregation_summary["junction_aggregation_candidate_count"]:
        warnings.append(
            "junction aggregation audit identified "
            f"{junction_aggregation_summary['junction_aggregation_candidate_count']} possible physical-intersection "
            "aggregation candidate(s); inspect the candidate CSV and map-review links before destructive joining"
        )
    if junction_aggregation_report is not None and junction_aggregation_report.get(
        "junction_aggregation_status"
    ) == "variant_created_for_review":
        warnings.append(
            "junction aggregation created a separate plain-nodes join patch review variant; inspect it in Netedit "
            "and map context before adopting any physical-intersection join"
        )
    if reference_join_aggregation_report is not None and reference_join_aggregation_report.get(
        "junction_aggregation_status"
    ) == "variant_created_for_review":
        warnings.append(
            "reference join aggregation created a separate review variant; compare it in Netedit and Google Maps "
            "before adopting it as the clean network"
        )
    if reference_scope_audit_report is not None and _int_field(reference_scope_audit_report, "prune_candidate_count") > 0:
        warnings.append(
            "reference scope audit found over-included short detail fragments; inspect the candidate CSV and "
            "the map context before adopting any pruning"
        )
    if (
        reference_scope_pruning_report is not None
        and reference_scope_pruning_report.get("scope_pruning_status") == "variant_created_for_review"
        and reference_scope_pruning_promotion_report.get("status") != "pass"
    ):
        warnings.append(
            "reference scope pruning created a separate review variant; compare it in Netedit and map imagery "
            "before adopting it as the clean network"
        )
    warnings = list(dict.fromkeys(warnings))
    gate_status = {
        "area_confirmation": "pass",
        "road_level_scope": "pass",
        "network_build": _gate_value(build_report),
        "tls_reality_audit": _tls_gate_value(tls_report, tls_summary),
        "connectivity": str(connectivity_quality["connectivity_gate"]),
        "netedit": _gate_value(netedit_report),
        "sumo_gui": _gate_value(sumo_gui_report),
    }
    if tls_aggregation_report is not None:
        gate_status["tls_aggregation"] = "blocked" if tls_aggregation_report.get("status") == "pass" else _gate_value(tls_aggregation_report)
    gate_status["tls_scoped_cell_batch"] = (
        "skipped"
        if teacher_guided_scoped_tls_cell_batch_report is None
        else _gate_value(teacher_guided_scoped_tls_cell_batch_report)
    )
    if str(network_plan.get("network_profile", "")) == "reference_matched":
        gate_status["reference_visual_detail"] = "pass" if reference_visual_detail_status in {"built", "same_as_vehicle_core"} else "fail"
        gate_status["reference_bbox_scope"] = _reference_bbox_scope_gate(reference_bbox_scope_report)
        gate_status["reference_hierarchy_audit"] = _reference_hierarchy_gate(reference_hierarchy_audit_report)
        gate_status["reference_scope_audit"] = _reference_scope_gate(reference_scope_audit_report)
        gate_status["reference_scope_pruning"] = _reference_scope_pruning_gate(reference_scope_pruning_report)
        if corridor_geometry_simplification_report is None:
            gate_status["corridor_geometry_simplification"] = "skipped"
        elif corridor_geometry_simplification_promotion_report.get("status") == "pass":
            gate_status["corridor_geometry_simplification"] = "pass"
        elif corridor_geometry_simplification_promotion_report.get("reason") == "not_needed":
            gate_status["corridor_geometry_simplification"] = "skipped"
        else:
            gate_status["corridor_geometry_simplification"] = "blocked"
        gate_status["reference_join_audit"] = _reference_join_gate(reference_join_audit_report)
        gate_status["junction_pattern_index"] = _junction_pattern_index_gate(reference_join_audit_report)
        gate_status["road_connectivity_parity"] = _road_connectivity_parity_gate_status(
            road_connectivity_parity_audit_report,
            road_connectivity_replay_report,
        )
        gate_status["road_connectivity_parity_audit"] = (
            "skipped"
            if road_connectivity_parity_audit_report is None
            else str(road_connectivity_parity_audit_report.get("status", "blocked"))
        )
        alignment_report = (
            road_connectivity_parity_audit_report.get("reference_road_alignment", {})
            if isinstance(road_connectivity_parity_audit_report, Mapping)
            else {}
        )
        gate_status["reference_road_alignment"] = (
            "skipped"
            if not isinstance(alignment_report, Mapping) or not alignment_report
            else str(alignment_report.get("status", "blocked"))
        )
        gate_status["road_connectivity_seed_parity"] = (
            "skipped"
            if road_connectivity_seed_probe_report is None
            else str(road_connectivity_seed_probe_report.get("status", "fail"))
        )
        semantic_parity_report = reference_join_post_teacher_audit_report or reference_join_audit_report
        gate_status["connection_semantics_parity"] = _junction_semantic_gate(
            semantic_parity_report,
            {"approach_edge_ids", "movement_signature_counts", "request_bit_lengths_ok"},
        )
        gate_status["tls_semantics_parity"] = _junction_semantic_gate(
            semantic_parity_report,
            {"control_type", "has_tls", "request_bit_lengths_ok"},
        )
        gate_status["internal_junction_parity"] = _junction_semantic_gate(
            semantic_parity_report,
            {"internal_function_counts"},
        )
        reference_join_aggregation_gate = _reference_join_aggregation_gate(reference_join_aggregation_report)
        if reference_join_aggregation_gate in {"blocked", "fail"} and all(
            gate_status[key] == "pass"
            for key in ("connection_semantics_parity", "tls_semantics_parity", "internal_junction_parity")
        ):
            reference_join_aggregation_gate = "skipped"
        gate_status["reference_join_aggregation"] = reference_join_aggregation_gate
        gate_status["teacher_guided_junction_parity"] = _teacher_guided_junction_parity_gate(
            teacher_guided_repair_run_report or teacher_guided_plain_export_report or teacher_guided_repair_queue_report,
            semantic_parity_report,
        )
    if topology_audit_report is not None:
        gate_status["topology_audit"] = (
            str(topology_reference_parity_report.get("status", "fail"))
            if str(network_plan.get("network_profile", "")) == "reference_matched"
            else _gate_value(topology_audit_report)
        )
    if junction_aggregation_report is not None:
        gate_status["junction_aggregation"] = (
            "blocked" if junction_aggregation_report.get("status") == "pass" else _gate_value(junction_aggregation_report)
        )
    if routeability_audit_report is not None:
        gate_status["routeability_audit"] = _gate_value(routeability_audit_report)
    gate_status["connection_mode_audit"] = _connection_mode_gate_value(
        connection_mode_audit_report
    )
    if str(network_plan.get("network_profile", "")) == "reference_matched":
        # Deprecated compatibility alias. The value now comes from the
        # code-native gate; launching NetEdit is never required to compute it.
        gate_status["netedit_connection_mode_review"] = gate_status[
            "connection_mode_audit"
        ]
    gate_status["standard_nema_scan"] = (
        "skipped" if standard_nema_scan_report is None else _gate_value(standard_nema_scan_report)
    )
    workflow_ok = (
        gate_status["network_build"] == "pass"
        and gate_status["tls_reality_audit"] == "pass"
        and gate_status["connectivity"] in {"pass", "partial"}
        and gate_status.get("topology_audit", "skipped") in {"pass", "skipped"}
        and gate_status.get("routeability_audit", "skipped") in {"pass", "blocked", "skipped"}
        and gate_status.get("connection_mode_audit", "skipped")
        in {"pass", "review_required", "skipped"}
        and gate_status.get("standard_nema_scan", "skipped") in {"pass", "skipped"}
        and gate_status.get("reference_visual_detail", "skipped") in {"pass", "skipped"}
        and gate_status.get("reference_bbox_scope", "skipped") in {"pass", "skipped"}
        and gate_status.get("reference_hierarchy_audit", "skipped") in {"pass", "skipped"}
        and gate_status.get("reference_scope_audit", "skipped") in {"pass", "skipped"}
        and gate_status.get("reference_scope_pruning", "skipped") in {"pass", "skipped"}
        and gate_status.get("corridor_geometry_simplification", "skipped") in {"pass", "skipped"}
        and gate_status.get("reference_join_audit", "skipped") in {"pass", "skipped"}
        and gate_status.get("tls_scoped_cell_batch", "skipped") in {"pass", "skipped"}
        and gate_status.get("reference_join_aggregation", "skipped") in {"pass", "skipped"}
        and gate_status.get("road_connectivity_parity", "skipped") in {"pass", "skipped"}
        and gate_status.get("road_connectivity_seed_parity", "skipped") in {"pass", "skipped"}
        and gate_status.get("teacher_guided_junction_parity", "skipped") in {"pass", "blocked", "skipped"}
        and gate_status["netedit"] in {"pass", "blocked"}
        and gate_status["sumo_gui"] in {"pass", "blocked"}
    )
    bbox_regional_map_baseline = regional_map_baseline_for_bbox(bbox, label=cleaned_place_name or "SUMO network area")
    tls_regional_map_baseline = dict(tls_summary.get("regional_map_baseline") or {})
    provider_counts = tls_regional_map_baseline.get("regional_map_provider_counts")
    has_tls_regional_rows = not isinstance(provider_counts, dict) or any(int(count) > 0 for count in provider_counts.values())
    regional_map_baseline = tls_regional_map_baseline if tls_regional_map_baseline and has_tls_regional_rows else bbox_regional_map_baseline
    (
        teacher_guided_exemplar_ready_candidate_count,
        teacher_guided_exemplar_movement_signature_count,
    ) = _teacher_guided_exemplar_ready_stats(teacher_guided_repair_queue_report)
    (
        teacher_guided_movement_gap_candidate_count,
        teacher_guided_max_vehicle_movement_matrix_missing_count,
        teacher_guided_missing_movement_plan_count,
        teacher_guided_top_movement_gaps,
    ) = _teacher_guided_movement_gap_stats(teacher_guided_repair_queue_report)
    post_teacher_tls_connection_repair_residual_stats = _junction_pattern_residual_stats(
        post_teacher_tls_connection_repair_reference_delta_report
    )
    final_movement_rebuild_residual_stats = _junction_pattern_residual_stats(
        final_movement_rebuild_reference_delta_report
    )
    (
        post_teacher_tls_connection_repair_movement_gap_candidate_count,
        post_teacher_tls_connection_repair_movement_max_gap_count,
        _post_teacher_tls_connection_repair_missing_movement_plan_count,
        _post_teacher_tls_connection_repair_top_movement_gaps,
    ) = _teacher_guided_movement_gap_stats(post_teacher_tls_connection_repair_movement_rebuild_queue_report)
    (
        final_movement_rebuild_movement_gap_candidate_count,
        final_movement_rebuild_movement_max_gap_count,
        _final_movement_rebuild_missing_movement_plan_count,
        _final_movement_rebuild_top_movement_gaps,
    ) = _teacher_guided_movement_gap_stats(final_movement_rebuild_queue_report)
    (
        final_movement_direct_replay_last_queue_movement_gap_candidate_count,
        final_movement_direct_replay_last_queue_max_gap_count,
        final_movement_direct_replay_last_queue_missing_movement_plan_count,
        final_movement_direct_replay_last_queue_top_movement_gaps,
    ) = _teacher_guided_movement_gap_stats(final_movement_direct_replay_last_queue_report)
    road_connectivity_promoted_variant_file = None
    road_connectivity_promoted_variant_reason = ""
    if (
        road_connectivity_seed_probe_report is not None
        and road_connectivity_seed_probe_report.get("status") == "pass"
        and (
            road_connectivity_parity_audit_report is None
            or road_connectivity_parity_audit_report.get("status") == "pass"
        )
    ):
        road_connectivity_promoted_variant_file = _road_connectivity_promoted_variant_file(
            road_connectivity_replay_report,
            road_connectivity_split_root_alias_repair_report,
            road_connection_topology_replay_report,
        )
        if road_connectivity_promoted_variant_file is not None:
            road_connectivity_promoted_variant_reason = "seed_probe_pass"
    elif road_connectivity_parity_audit_report is not None:
        road_connectivity_promoted_variant_reason = "global_road_parity_not_pass"
    if run_corridor_edit_ledger_after_build:
        ledger_source_net_file = reference_visual_detail_comparison_net_file or net_file
        if ledger_source_net_file.exists():
            try:
                corridor_edit_ledger_report = build_corridor_edit_ledger(
                    net_file=ledger_source_net_file,
                    output_dir=output_dir / "corridor_edit_ledger",
                    reference_net_file=reference_net_file,
                    osm_file=osm_file,
                    prefix=f"{prefix}_corridor_edit_ledger",
                    include_auto_proposals=True,
                )
            except (OSError, ET.ParseError, TypeError, ValueError) as exc:
                corridor_edit_ledger_report = {
                    "status": "fail",
                    "claim_status": "construction-invalid",
                    "corridor_edit_ledger_status": "failed",
                    "error": f"{type(exc).__name__}: {exc}",
                    "warnings": ["corridor edit ledger was requested but could not be built"],
                }
        else:
            corridor_edit_ledger_report = {
                "status": "fail",
                "claim_status": "construction-invalid",
                "corridor_edit_ledger_status": "failed",
                "error": f"ledger source network does not exist: {ledger_source_net_file}",
                "warnings": ["corridor edit ledger was requested but no source network was available"],
            }
    effective_visual_tls_preservation = _effective_tls_controlled_connection_preservation(
        aggregation_report=reference_visual_detail_tls_aggregation_report,
        repair_report=reference_visual_detail_tls_connection_repair_report,
        repair_promotion_report=reference_visual_detail_tls_connection_repair_promotion_report,
        effective_net_file=reference_visual_detail_comparison_net_file,
    )
    report = {
        "status": "pass" if workflow_ok else "fail",
        "claim_status": "diagnostic-demo" if workflow_ok else "construction-invalid",
        "area_input": cleaned_place_name or bbox,
        "area_resolution_status": area_status,
        **(_candidate_fields(place_report) if place_report is not None else {**_candidate_fields(None), "candidate_bbox": bbox}),
        **_reference_bbox_fields(reference_bbox_report),
        "osm_preview_url": str(place_report.get("osm_preview_url", osm_preview_url(cleaned_place_name))) if place_report is not None else (osm_preview_url(cleaned_place_name) if cleaned_place_name else ""),
        "user_confirmed_area": "yes" if area_status == "confirmed_by_user" else "confirmed_by_input",
        "road_level_scope_status": "confirmed",
        "network_plan_status": network_plan.get("network_plan_status", "confirmed"),
        "network_profile": network_plan.get("network_profile", ""),
        "reference_target": network_plan.get("reference_target", ""),
        "reference_net_file": network_plan.get("reference_net_file", ""),
        "reference_source_net_file": str(reference_source_net_file or ""),
        "reference_validation_net_file": str(reference_net_file or ""),
        "network_detail_target": network_plan.get("network_detail_target", ""),
        "primary_network_layer": network_plan.get("primary_network_layer", ""),
        "default_routeability_layer": network_plan.get("default_routeability_layer", ""),
        "default_netedit_comparison_layer": network_plan.get("default_netedit_comparison_layer", ""),
        "comparison_scope_mode": network_plan.get("comparison_scope_mode", ""),
        "auxiliary_modal_layers": network_plan.get("auxiliary_modal_layers", []),
        "auxiliary_modal_highway_classes": network_plan.get("auxiliary_modal_highway_classes", {}),
        "movement_layers": network_plan.get("movement_layers", []),
        "selected_highway_classes": network_plan.get("highway_classes", []),
        "vehicle_core_highway_classes": network_plan.get("vehicle_core_highway_classes", network_plan.get("highway_classes", [])),
        "reference_visual_detail_highway_classes": network_plan.get("reference_visual_detail_highway_classes", []),
        "reference_visual_detail_only_highway_classes": network_plan.get("reference_visual_detail_only_highway_classes", []),
        "reference_source_way_scope": "reference_source_way_ids"
        if reference_source_way_scope is not None
        else "not_applied",
        "reference_source_way_id_count": len(reference_source_way_ids),
        "service_passenger_policy": network_plan.get("service_passenger_policy", "sumo_default"),
        "network_plan": network_plan,
        "reference_policy": network_plan.get("reference_policy", {}),
        **_road_level_scope_fields(),
        "map_baseline_source": regional_map_baseline["regional_map_provider"],
        "regional_map_baseline": regional_map_baseline,
        "map_temporal_scope": map_temporal_scope,
        "map_target_date": map_target_date or "",
        **tls_summary,
        "tls_physical_cluster_count": tls_aggregation_report.get("tls_physical_cluster_count", tls_summary["tls_cluster_count"])
        if tls_aggregation_report is not None
        else tls_summary["tls_cluster_count"],
        "tls_aggregation_status": "skipped"
        if tls_aggregation_report is None
        else tls_aggregation_report.get("tls_aggregation_status", tls_aggregation_report.get("status", "fail")),
        "tls_aggregation_variant_file": ""
        if tls_aggregation_report is None
        else str(tls_aggregation_report.get("tls_aggregation_variant_file", "")),
        "tls_aggregation_plan_file": ""
        if tls_aggregation_report is None
        else str(tls_aggregation_report.get("tls_aggregation_plan_file", "")),
        "tls_aggregation_representatives_file": ""
        if tls_aggregation_report is None
        else str(tls_aggregation_report.get("tls_aggregation_representatives_file", "")),
        "tls_aggregated_traffic_light_junction_count": ""
        if tls_aggregation_report is None
        else tls_aggregation_report.get("tls_aggregated_traffic_light_junction_count", ""),
        "tls_aggregated_tl_logic_count": ""
        if tls_aggregation_report is None
        else tls_aggregation_report.get("tls_aggregated_tl_logic_count", ""),
        "tls_aggregated_controlled_connection_count": ""
        if tls_aggregation_report is None
        else tls_aggregation_report.get("tls_aggregated_controlled_connection_count", ""),
        "tls_aggregated_tl_connection_missing_linkindex_count": ""
        if tls_aggregation_report is None
        else tls_aggregation_report.get("tls_aggregated_tl_connection_missing_linkindex_count", ""),
        "tls_controlled_connection_preservation_status": "skipped"
        if tls_aggregation_report is None
        else str(tls_aggregation_report.get("tls_controlled_connection_preservation_status", "pass")),
        "tls_controlled_connection_regression_count": 0
        if tls_aggregation_report is None
        else tls_aggregation_report.get("tls_controlled_connection_regression_count", 0),
        "connectivity_status": connectivity_report.get("connectivity_status", connectivity_report.get("status", "fail")),
        "raw_connectivity_status": raw_connectivity_report.get("connectivity_status", raw_connectivity_report.get("status", "fail")),
        "strict_connectivity_status": connectivity_quality["strict_connectivity_status"],
        "connectivity_main_component_ratio": connectivity_quality["connectivity_main_component_ratio"],
        "network_quality": connectivity_quality["network_quality"],
        "experiment_readiness": "no",
        "passenger_edge_count": connectivity_report.get("passenger_edge_count", 0),
        "passenger_component_count": connectivity_report.get("passenger_component_count", 0),
        "largest_component_edge_count": connectivity_report.get("largest_component_edge_count", 0),
        "small_component_count": connectivity_report.get("small_component_count", 0),
        "isolated_passenger_edge_count": connectivity_report.get("isolated_passenger_edge_count", 0),
        "raw_passenger_edge_count": raw_connectivity_report.get("passenger_edge_count", 0),
        "raw_passenger_component_count": raw_connectivity_report.get("passenger_component_count", 0),
        "raw_largest_component_edge_count": raw_connectivity_report.get("largest_component_edge_count", 0),
        "raw_isolated_passenger_edge_count": raw_connectivity_report.get("isolated_passenger_edge_count", 0),
        "topology_fragmentation_status": "skipped" if topology_audit_report is None else topology_audit_report.get("topology_fragmentation_status", topology_audit_report.get("status", "fail")),
        "suspicious_topology_cluster_count": 0 if topology_audit_report is None else topology_audit_report.get("suspicious_cluster_count", 0),
        "max_topology_cluster_node_count": 0 if topology_audit_report is None else topology_audit_report.get("max_cluster_node_count", 0),
        "topology_audit_clusters_file": "" if topology_audit_report is None else str(topology_audit_report.get("clusters_file", "")),
        "reference_topology_fragmentation_status": "skipped"
        if reference_topology_audit_report is None
        else reference_topology_audit_report.get(
            "topology_fragmentation_status", reference_topology_audit_report.get("status", "fail")
        ),
        "reference_topology_suspicious_cluster_count": 0
        if reference_topology_audit_report is None
        else reference_topology_audit_report.get("suspicious_cluster_count", 0),
        "reference_topology_junction_aggregation_candidate_count": 0
        if reference_topology_audit_report is None
        else _topology_metric(reference_topology_audit_report, "junction_aggregation_candidate_count"),
        "reference_topology_physical_intersection_candidate_count": 0
        if reference_topology_audit_report is None
        else reference_topology_audit_report.get("physical_intersection_candidate_count", 0),
        "reference_topology_audit_clusters_file": ""
        if reference_topology_audit_report is None
        else str(reference_topology_audit_report.get("clusters_file", "")),
        "topology_reference_parity_status": str(topology_reference_parity_report.get("status", "skipped")),
        "topology_reference_parity_reason": str(topology_reference_parity_report.get("reason", "")),
        "topology_reference_parity_metrics": topology_reference_parity_report.get("metrics", {}),
        "topology_reference_parity_details": topology_reference_parity_report,
        **junction_aggregation_summary,
        "junction_aggregation_variant_status": "skipped"
        if junction_aggregation_report is None
        else junction_aggregation_report.get(
            "junction_aggregation_status", junction_aggregation_report.get("status", "fail")
        ),
        "junction_aggregation_variant_file": ""
        if junction_aggregation_report is None
        else str(junction_aggregation_report.get("junction_aggregation_variant_file", "")),
        "junction_aggregation_plan_file": ""
        if junction_aggregation_report is None
        else str(junction_aggregation_report.get("junction_aggregation_plan_file", "")),
        "junction_aggregation_variant_candidates_file": ""
        if junction_aggregation_report is None
        else str(junction_aggregation_report.get("junction_aggregation_candidates_file", "")),
        "junction_join_nodes_patch_file": ""
        if junction_aggregation_report is None
        else str(junction_aggregation_report.get("junction_join_nodes_patch_file", "")),
        "junction_join_definition_file": ""
        if junction_aggregation_report is None
        else str(junction_aggregation_report.get("junction_join_definition_file", "")),
        "junction_join_definition_csv": ""
        if junction_aggregation_report is None
        else str(junction_aggregation_report.get("junction_join_definition_csv", "")),
        "junction_join_explicit_join_count": 0
        if junction_aggregation_report is None
        else junction_aggregation_report.get("junction_join_explicit_join_count", 0),
        "junction_join_exclude_count": 0
        if junction_aggregation_report is None
        else junction_aggregation_report.get("junction_join_exclude_count", 0),
        "junction_join_needs_map_review_count": 0
        if junction_aggregation_report is None
        else junction_aggregation_report.get("junction_join_needs_map_review_count", 0),
        "reference_join_audit_status": "skipped"
        if reference_join_audit_report is None
        else reference_join_audit_report.get("status", "fail"),
        "reference_join_audit_mode": "skipped"
        if reference_join_audit_report is None
        else str(reference_join_audit_report.get("audit_mode", "full")),
        "reference_join_audit_candidate_layer": reference_join_audit_candidate_layer,
        "reference_join_audit_candidate_net_file": ""
        if reference_join_audit_candidate_net_file is None
        else str(reference_join_audit_candidate_net_file),
        "reference_join_reference_case_count": 0
        if reference_join_audit_report is None
        else reference_join_audit_report.get("reference_case_count", 0),
        "reference_join_matched_case_count": 0
        if reference_join_audit_report is None
        else reference_join_audit_report.get("matched_case_count", 0),
        "reference_join_unmatched_case_count": 0
        if reference_join_audit_report is None
        else reference_join_audit_report.get("unmatched_case_count", 0),
        "reference_join_audit_report_file": ""
        if reference_join_audit_report is None
        else str(reference_join_audit_report.get("summary_file", "")),
        "reference_join_audit_cases_file": ""
        if reference_join_audit_report is None
        else str(reference_join_audit_report.get("cases_file", "")),
        "reference_join_junction_teacher_delta_file": ""
        if reference_join_audit_report is None
        else str(reference_join_audit_report.get("junction_teacher_delta_file", "")),
        "reference_join_junction_pattern_comparisons_file": ""
        if reference_join_audit_report is None
        else str(reference_join_audit_report.get("junction_pattern_comparisons_file", "")),
        "reference_join_junction_pattern_templates_file": ""
        if reference_join_audit_report is None
        else str(reference_join_audit_report.get("junction_pattern_templates_file", "")),
        "reference_join_junction_pattern_comparison_status": "skipped"
        if reference_join_audit_report is None
        else str(reference_join_audit_report.get("junction_pattern_comparison_status", "skipped")),
        "reference_join_junction_pattern_mismatch_count": 0
        if reference_join_audit_report is None
        else _int_field(reference_join_audit_report, "junction_pattern_mismatch_count"),
        "reference_join_junction_pattern_comparison_sample_count": _list_field_count(
            reference_join_audit_report,
            "junction_pattern_comparisons",
        ),
        "reference_join_junction_pattern_mismatch_field_counts": {}
        if reference_join_audit_report is None
        else reference_join_audit_report.get("junction_pattern_mismatch_field_counts", {}),
        "reference_join_structural_signature_status": "skipped"
        if reference_join_audit_report is None
        else str(reference_join_audit_report.get("junction_structural_signature_status", "skipped")),
        "reference_join_structural_signature_missing_counts": {}
        if reference_join_audit_report is None
        else reference_join_audit_report.get("junction_structural_signature_missing_counts", {}),
        "reference_join_reference_structural_signature_summary": {}
        if reference_join_audit_report is None
        else reference_join_audit_report.get("reference_structural_signature_summary", {}),
        "reference_join_candidate_structural_signature_summary": {}
        if reference_join_audit_report is None
        else reference_join_audit_report.get("candidate_structural_signature_summary", {}),
        "reference_join_network_structural_delta_status": "skipped"
        if reference_join_audit_report is None
        else str(reference_join_audit_report.get("network_structural_delta_status", "skipped")),
        "reference_join_network_structural_missing_counts": {}
        if reference_join_audit_report is None
        else reference_join_audit_report.get("network_structural_missing_counts", {}),
        "reference_join_network_structural_extra_counts": {}
        if reference_join_audit_report is None
        else reference_join_audit_report.get("network_structural_extra_counts", {}),
        "tls_gap_destination_mapping_status": "skipped"
        if tls_gap_destination_mapping_report is None
        else str(tls_gap_destination_mapping_report.get("status", "fail")),
        "tls_gap_destination_mapping_report_file": ""
        if tls_gap_destination_mapping_report is None
        else str(tls_gap_destination_mapping_report.get("report_file", "")),
        "tls_gap_destination_mapping_missing_connection_count": 0
        if tls_gap_destination_mapping_report is None
        else int(tls_gap_destination_mapping_report.get("missing_connection_count", 0) or 0),
        "tls_gap_destination_mapping_endpoint_mapped_count": 0
        if tls_gap_destination_mapping_report is None
        else int(
            tls_gap_destination_mapping_report.get("destination_edge_and_endpoint_mapped_count", 0) or 0
        ),
        "tls_gap_destination_mapping_unmapped_count": 0
        if tls_gap_destination_mapping_report is None
        else int(tls_gap_destination_mapping_report.get("unmapped_destination_edge_count", 0) or 0),
        "tls_gap_destination_mapping_repair_safe": False
        if tls_gap_destination_mapping_report is None
        else bool(tls_gap_destination_mapping_report.get("repair_safe", False)),
        "tls_repair_variant_status": "skipped"
        if tls_repair_variant_report is None
        else str(tls_repair_variant_report.get("repair_variant_status", "not_created")),
        "tls_repair_variant_file": ""
        if tls_repair_variant_report is None
        else str(tls_repair_variant_report.get("variant_file", "")),
        "tls_repair_variant_report_file": ""
        if tls_repair_variant_report is None
        else str(tls_repair_variant_report.get("report_file", "")),
        "tls_repair_variant_netconvert_status": "skipped"
        if tls_repair_variant_report is None
        else str(tls_repair_variant_report.get("status", "fail")),
        "tls_repair_variant_sumo_load_status": "skipped"
        if tls_repair_variant_sumo_load_report is None
        else str(tls_repair_variant_sumo_load_report.get("status", "fail")),
        "tls_repair_variant_semantic_status": "skipped"
        if tls_repair_variant_semantic_report is None
        else str(tls_repair_variant_semantic_report.get("status", "fail")),
        "tls_repair_variant_semantic_report_file": ""
        if tls_repair_variant_semantic_report is None
        else str(tls_repair_variant_semantic_report.get("report_file", "")),
        "tls_repair_variant_reference_audit_status": "skipped"
        if tls_repair_variant_reference_audit_report is None
        else str(tls_repair_variant_reference_audit_report.get("status", "fail")),
        "tls_repair_variant_reference_parity_status": "skipped"
        if tls_repair_decision_report is None
        else str(tls_repair_decision_report.get("repair_variant_reference_parity_status", "skipped")),
        "tls_repair_variant_reference_audit_report_file": ""
        if tls_repair_variant_reference_audit_report is None
        else str(tls_repair_variant_reference_audit_report.get("summary_file", "")),
        "tls_repair_decision_status": "skipped"
        if tls_repair_decision_report is None
        else str(tls_repair_decision_report.get("status", "fail")),
        "tls_repair_decision_report_file": ""
        if tls_repair_decision_report is None
        else str(tls_repair_decision_report.get("report_file", "")),
        "reference_join_post_teacher_audit_status": "skipped"
        if reference_join_post_teacher_audit_report is None
        else reference_join_post_teacher_audit_report.get("status", "fail"),
        "reference_join_post_teacher_audit_report_file": ""
        if reference_join_post_teacher_audit_report is None
        else str(reference_join_post_teacher_audit_report.get("summary_file", "")),
        "reference_join_post_teacher_junction_pattern_mismatch_count": 0
        if reference_join_post_teacher_audit_report is None
        else _int_field(reference_join_post_teacher_audit_report, "junction_pattern_mismatch_count"),
        "reference_join_post_teacher_junction_pattern_mismatch_field_counts": {}
        if reference_join_post_teacher_audit_report is None
        else reference_join_post_teacher_audit_report.get("junction_pattern_mismatch_field_counts", {}),
        "reference_join_post_teacher_network_structural_missing_counts": {}
        if reference_join_post_teacher_audit_report is None
        else reference_join_post_teacher_audit_report.get("network_structural_missing_counts", {}),
        "reference_join_post_teacher_network_structural_extra_counts": {}
        if reference_join_post_teacher_audit_report is None
        else reference_join_post_teacher_audit_report.get("network_structural_extra_counts", {}),
        "post_teacher_tls_low_vehicle_control_status": "skipped"
        if post_teacher_tls_low_vehicle_control_report is None
        else str(post_teacher_tls_low_vehicle_control_report.get("status", "fail")),
        "post_teacher_tls_low_vehicle_control_variant_file": ""
        if post_teacher_tls_low_vehicle_control_report is None
        else str(post_teacher_tls_low_vehicle_control_report.get("tls_low_vehicle_control_variant_file", "")),
        "post_teacher_tls_low_vehicle_control_selected_tllogic_count": 0
        if post_teacher_tls_low_vehicle_control_report is None
        else post_teacher_tls_low_vehicle_control_report.get("tls_low_vehicle_control_selected_tllogic_count", 0),
        "post_teacher_tls_low_vehicle_control_removed_connection_count": 0
        if post_teacher_tls_low_vehicle_control_report is None
        else post_teacher_tls_low_vehicle_control_report.get("tls_low_vehicle_control_removed_connection_count", 0),
        "post_teacher_tls_low_vehicle_control_candidate_count": len(post_teacher_tls_low_vehicle_control_candidates),
        "post_teacher_tls_low_vehicle_control_candidates": post_teacher_tls_low_vehicle_control_candidates,
        "post_teacher_tls_low_vehicle_control_sumo_load_status": "skipped"
        if post_teacher_tls_low_vehicle_control_sumo_load_report is None
        else str(post_teacher_tls_low_vehicle_control_sumo_load_report.get("status", "fail")),
        "post_teacher_tls_low_vehicle_control_reference_tls_semantic_delta_score": _tls_semantic_delta_score(
            post_teacher_tls_low_vehicle_control_reference_delta_report
        ),
        "post_teacher_tls_low_vehicle_control_reference_delta_file": ""
        if post_teacher_tls_low_vehicle_control_reference_delta_report is None
        else str(post_teacher_tls_low_vehicle_control_reference_delta_report.get("summary_file", "")),
        "post_teacher_tls_low_vehicle_control_reference_promotion_status": str(
            post_teacher_tls_low_vehicle_control_reference_promotion_report.get("status", "skipped")
        ),
        "post_teacher_tls_low_vehicle_control_reference_promotion_reason": str(
            post_teacher_tls_low_vehicle_control_reference_promotion_report.get("reason", "")
        ),
        "post_teacher_tls_signal_grouping_status": "skipped"
        if post_teacher_tls_signal_grouping_report is None
        else str(post_teacher_tls_signal_grouping_report.get("status", "fail")),
        "post_teacher_tls_signal_grouping_variant_file": ""
        if post_teacher_tls_signal_grouping_report is None
        else str(post_teacher_tls_signal_grouping_report.get("tls_signal_grouping_variant_file", "")),
        "post_teacher_tls_signal_grouping_merged_group_count": 0
        if post_teacher_tls_signal_grouping_report is None
        else post_teacher_tls_signal_grouping_report.get("tls_signal_grouping_merged_group_count", 0),
        "post_teacher_tls_signal_grouping_remapped_connection_count": 0
        if post_teacher_tls_signal_grouping_report is None
        else post_teacher_tls_signal_grouping_report.get("tls_signal_grouping_remapped_connection_count", 0),
        "post_teacher_tls_signal_grouping_sumo_load_status": "skipped"
        if post_teacher_tls_signal_grouping_sumo_load_report is None
        else str(post_teacher_tls_signal_grouping_sumo_load_report.get("status", "fail")),
        "post_teacher_tls_signal_grouping_reference_tls_semantic_delta_score": _tls_semantic_delta_score(
            post_teacher_tls_signal_grouping_reference_delta_report
        ),
        "post_teacher_tls_signal_grouping_reference_delta_file": ""
        if post_teacher_tls_signal_grouping_reference_delta_report is None
        else str(post_teacher_tls_signal_grouping_reference_delta_report.get("summary_file", "")),
        "post_teacher_tls_signal_grouping_reference_promotion_status": str(
            post_teacher_tls_signal_grouping_reference_promotion_report.get("status", "skipped")
        ),
        "post_teacher_tls_signal_grouping_reference_promotion_reason": str(
            post_teacher_tls_signal_grouping_reference_promotion_report.get("reason", "")
        ),
        "post_teacher_tls_non_controller_junction_demotion_status": "skipped"
        if post_teacher_tls_non_controller_junction_demotion_report is None
        else str(post_teacher_tls_non_controller_junction_demotion_report.get("status", "fail")),
        "post_teacher_tls_non_controller_junction_demotion_variant_file": ""
        if post_teacher_tls_non_controller_junction_demotion_report is None
        else str(
            post_teacher_tls_non_controller_junction_demotion_report.get(
                "tls_non_controller_junction_demotion_variant_file", ""
            )
        ),
        "post_teacher_tls_non_controller_traffic_light_junction_demoted_count": 0
        if post_teacher_tls_non_controller_junction_demotion_report is None
        else post_teacher_tls_non_controller_junction_demotion_report.get(
            "tls_non_controller_traffic_light_junction_demoted_count", 0
        ),
        "post_teacher_tls_non_controller_junction_demotion_sumo_load_status": "skipped"
        if post_teacher_tls_non_controller_junction_demotion_sumo_load_report is None
        else str(post_teacher_tls_non_controller_junction_demotion_sumo_load_report.get("status", "fail")),
        "post_teacher_tls_non_controller_junction_demotion_reference_tls_semantic_delta_score": _tls_semantic_delta_score(
            post_teacher_tls_non_controller_junction_demotion_reference_delta_report
        ),
        "post_teacher_tls_non_controller_junction_demotion_reference_delta_file": ""
        if post_teacher_tls_non_controller_junction_demotion_reference_delta_report is None
        else str(post_teacher_tls_non_controller_junction_demotion_reference_delta_report.get("summary_file", "")),
        "post_teacher_tls_non_controller_junction_demotion_reference_promotion_status": str(
            post_teacher_tls_non_controller_junction_demotion_reference_promotion_report.get("status", "skipped")
        ),
        "post_teacher_tls_non_controller_junction_demotion_reference_promotion_reason": str(
            post_teacher_tls_non_controller_junction_demotion_reference_promotion_report.get("reason", "")
        ),
        "post_teacher_tls_connection_repair_status": "skipped"
        if post_teacher_tls_connection_repair_report is None
        else str(post_teacher_tls_connection_repair_report.get("status", "fail")),
        "post_teacher_tls_connection_repair_variant_file": ""
        if post_teacher_tls_connection_repair_report is None
        else str(post_teacher_tls_connection_repair_report.get("variant_file", "")),
        "post_teacher_tls_connection_repair_controlled_connection_count_before": ""
        if post_teacher_tls_connection_repair_report is None
        else post_teacher_tls_connection_repair_report.get(
            "candidate_tls_controlled_connection_count_before", ""
        ),
        "post_teacher_tls_connection_repair_controlled_connection_count_after": ""
        if post_teacher_tls_connection_repair_report is None
        else post_teacher_tls_connection_repair_report.get("candidate_tls_controlled_connection_count_after", ""),
        "post_teacher_tls_connection_repair_updated_connection_count": 0
        if post_teacher_tls_connection_repair_report is None
        else post_teacher_tls_connection_repair_report.get("updated_connection_count", 0),
        "post_teacher_tls_connection_repair_skipped_invalid_mapped_linkindex_count": 0
        if post_teacher_tls_connection_repair_report is None
        else post_teacher_tls_connection_repair_report.get(
            "skipped_invalid_mapped_linkindex_connection_count", 0
        ),
        "post_teacher_tls_connection_repair_sumo_load_status": "skipped"
        if post_teacher_tls_connection_repair_sumo_load_report is None
        else str(post_teacher_tls_connection_repair_sumo_load_report.get("status", "fail")),
        "post_teacher_tls_connection_repair_reference_tls_semantic_delta_score": _tls_semantic_delta_score(
            post_teacher_tls_connection_repair_reference_delta_report
        ),
        "post_teacher_tls_connection_repair_reference_delta_file": ""
        if post_teacher_tls_connection_repair_reference_delta_report is None
        else str(post_teacher_tls_connection_repair_reference_delta_report.get("summary_file", "")),
        "post_teacher_tls_connection_repair_junction_pattern_case_count": post_teacher_tls_connection_repair_residual_stats[
            "case_count"
        ],
        "post_teacher_tls_connection_repair_junction_pattern_mismatch_count": post_teacher_tls_connection_repair_residual_stats[
            "failed_case_count"
        ],
        "post_teacher_tls_connection_repair_junction_pattern_mismatch_field_counts": post_teacher_tls_connection_repair_residual_stats[
            "mismatch_field_counts"
        ],
        "post_teacher_tls_connection_repair_internal_function_count_deficits": post_teacher_tls_connection_repair_residual_stats[
            "internal_function_count_deficits"
        ],
        "post_teacher_tls_connection_repair_top_junction_pattern_mismatches": post_teacher_tls_connection_repair_residual_stats[
            "top_junction_pattern_mismatches"
        ],
        "post_teacher_tls_connection_repair_movement_rebuild_queue_status": "skipped"
        if post_teacher_tls_connection_repair_movement_rebuild_queue_report is None
        else str(post_teacher_tls_connection_repair_movement_rebuild_queue_report.get("status", "fail")),
        "post_teacher_tls_connection_repair_movement_rebuild_candidate_count": 0
        if post_teacher_tls_connection_repair_movement_rebuild_queue_report is None
        else post_teacher_tls_connection_repair_movement_rebuild_queue_report.get("repair_candidate_count", 0),
        "post_teacher_tls_connection_repair_movement_rebuild_ready_candidate_count": 0
        if post_teacher_tls_connection_repair_movement_rebuild_queue_report is None
        else post_teacher_tls_connection_repair_movement_rebuild_queue_report.get("ready_candidate_count", 0),
        "post_teacher_tls_connection_repair_movement_rebuild_expanded_scope_candidate_count": 0
        if post_teacher_tls_connection_repair_movement_rebuild_queue_report is None
        else post_teacher_tls_connection_repair_movement_rebuild_queue_report.get("expanded_scope_candidate_count", 0),
        "post_teacher_tls_connection_repair_movement_rebuild_gap_candidate_count": post_teacher_tls_connection_repair_movement_gap_candidate_count,
        "post_teacher_tls_connection_repair_movement_rebuild_max_gap_count": post_teacher_tls_connection_repair_movement_max_gap_count,
        "post_teacher_tls_connection_repair_movement_rebuild_queue_file": ""
        if post_teacher_tls_connection_repair_movement_rebuild_queue_report is None
        else str(post_teacher_tls_connection_repair_movement_rebuild_queue_report.get("queue_file", "")),
        "post_teacher_tls_connection_repair_movement_rebuild_plain_export_status": "skipped"
        if post_teacher_tls_connection_repair_movement_rebuild_plain_export_report is None
        else str(post_teacher_tls_connection_repair_movement_rebuild_plain_export_report.get("status", "fail")),
        "post_teacher_tls_connection_repair_movement_rebuild_run_status": "skipped"
        if post_teacher_tls_connection_repair_movement_rebuild_run_report is None
        else str(post_teacher_tls_connection_repair_movement_rebuild_run_report.get("status", "fail")),
        "post_teacher_tls_connection_repair_movement_rebuild_parity_gate_status": "skipped"
        if post_teacher_tls_connection_repair_movement_rebuild_run_report is None
        else str(post_teacher_tls_connection_repair_movement_rebuild_run_report.get("parity_gate_status", "fail")),
        "post_teacher_tls_connection_repair_movement_rebuild_semantic_layer_gate_counts": {}
        if post_teacher_tls_connection_repair_movement_rebuild_run_report is None
        else post_teacher_tls_connection_repair_movement_rebuild_run_report.get("semantic_layer_gate_counts", {}),
        "post_teacher_tls_connection_repair_movement_rebuild_best_variant_file": ""
        if post_teacher_tls_connection_repair_movement_rebuild_best_variant_file is None
        else str(post_teacher_tls_connection_repair_movement_rebuild_best_variant_file),
        "post_teacher_tls_connection_repair_movement_rebuild_applied_candidate_count": 0
        if post_teacher_tls_connection_repair_movement_rebuild_run_report is None
        else post_teacher_tls_connection_repair_movement_rebuild_run_report.get("composite_applied_candidate_count", 0),
        "final_movement_rebuild_queue_status": "skipped"
        if final_movement_rebuild_queue_report is None
        else str(final_movement_rebuild_queue_report.get("status", "fail")),
        "final_movement_rebuild_candidate_count": 0
        if final_movement_rebuild_queue_report is None
        else final_movement_rebuild_queue_report.get("repair_candidate_count", 0),
        "final_movement_rebuild_ready_candidate_count": 0
        if final_movement_rebuild_queue_report is None
        else final_movement_rebuild_queue_report.get("ready_candidate_count", 0),
        "final_movement_rebuild_expanded_scope_candidate_count": 0
        if final_movement_rebuild_queue_report is None
        else final_movement_rebuild_queue_report.get("expanded_scope_candidate_count", 0),
        "final_movement_rebuild_gap_candidate_count": final_movement_rebuild_movement_gap_candidate_count,
        "final_movement_rebuild_max_gap_count": final_movement_rebuild_movement_max_gap_count,
        "final_movement_rebuild_queue_file": ""
        if final_movement_rebuild_queue_report is None
        else str(final_movement_rebuild_queue_report.get("queue_file", "")),
        "final_movement_rebuild_plain_export_status": "skipped"
        if final_movement_rebuild_plain_export_report is None
        else str(final_movement_rebuild_plain_export_report.get("status", "fail")),
        "final_movement_rebuild_run_status": "skipped"
        if final_movement_rebuild_run_report is None
        else str(final_movement_rebuild_run_report.get("status", "fail")),
        "final_movement_rebuild_parity_gate_status": "skipped"
        if final_movement_rebuild_run_report is None
        else str(final_movement_rebuild_run_report.get("parity_gate_status", "fail")),
        "final_movement_rebuild_semantic_layer_gate_counts": {}
        if final_movement_rebuild_run_report is None
        else final_movement_rebuild_run_report.get("semantic_layer_gate_counts", {}),
        "final_movement_rebuild_best_variant_file": ""
        if final_movement_rebuild_best_variant_file is None
        else str(final_movement_rebuild_best_variant_file),
        "final_movement_rebuild_applied_candidate_count": 0
        if final_movement_rebuild_run_report is None
        else final_movement_rebuild_run_report.get("composite_applied_candidate_count", 0),
        "final_movement_rebuild_sumo_load_status": "skipped"
        if final_movement_rebuild_sumo_load_report is None
        else str(final_movement_rebuild_sumo_load_report.get("status", "fail")),
        "final_movement_rebuild_reference_delta_file": ""
        if final_movement_rebuild_reference_delta_report is None
        else str(final_movement_rebuild_reference_delta_report.get("summary_file", "")),
        "final_movement_rebuild_junction_pattern_case_count": final_movement_rebuild_residual_stats[
            "case_count"
        ],
        "final_movement_rebuild_junction_pattern_mismatch_count": final_movement_rebuild_residual_stats[
            "failed_case_count"
        ],
        "final_movement_rebuild_junction_pattern_mismatch_field_counts": final_movement_rebuild_residual_stats[
            "mismatch_field_counts"
        ],
        "final_movement_rebuild_internal_function_count_deficits": final_movement_rebuild_residual_stats[
            "internal_function_count_deficits"
        ],
        "final_movement_rebuild_top_junction_pattern_mismatches": final_movement_rebuild_residual_stats[
            "top_junction_pattern_mismatches"
        ],
        "final_movement_rebuild_reference_promotion_status": str(
            final_movement_rebuild_reference_promotion_report.get("status", "skipped")
        ),
        "final_movement_rebuild_reference_promotion_reason": str(
            final_movement_rebuild_reference_promotion_report.get("reason", "")
        ),
        "final_movement_direct_replay_status": "skipped"
        if final_movement_direct_replay_report is None
        else str(final_movement_direct_replay_report.get("status", "fail")),
        "final_movement_direct_replay_variant_file": ""
        if final_movement_direct_replay_best_variant_file is None
        else str(final_movement_direct_replay_best_variant_file),
        "final_movement_direct_replay_reference_delta_file": ""
        if final_movement_direct_replay_reference_delta_report is None
        else str(final_movement_direct_replay_reference_delta_report.get("summary_file", "")),
        "final_movement_direct_replay_reference_promotion_status": str(
            final_movement_direct_replay_reference_promotion_report.get("status", "skipped")
        ),
        "final_movement_direct_replay_reference_promotion_reason": str(
            final_movement_direct_replay_reference_promotion_report.get("reason", "")
        ),
        "final_movement_direct_replay_last_queue_status": "skipped"
        if final_movement_direct_replay_last_queue_report is None
        else str(final_movement_direct_replay_last_queue_report.get("status", "fail")),
        "final_movement_direct_replay_last_queue_candidate_count": 0
        if final_movement_direct_replay_last_queue_report is None
        else _int_field(final_movement_direct_replay_last_queue_report, "repair_candidate_count"),
        "final_movement_direct_replay_last_queue_ready_candidate_count": 0
        if final_movement_direct_replay_last_queue_report is None
        else _int_field(final_movement_direct_replay_last_queue_report, "ready_candidate_count"),
        "final_movement_direct_replay_last_queue_expanded_scope_candidate_count": 0
        if final_movement_direct_replay_last_queue_report is None
        else _int_field(final_movement_direct_replay_last_queue_report, "expanded_scope_candidate_count"),
        "final_movement_direct_replay_last_queue_blocked_candidate_count": 0
        if final_movement_direct_replay_last_queue_report is None
        else _int_field(final_movement_direct_replay_last_queue_report, "blocked_candidate_count"),
        "final_movement_direct_replay_last_queue_movement_gap_candidate_count": (
            final_movement_direct_replay_last_queue_movement_gap_candidate_count
        ),
        "final_movement_direct_replay_last_queue_max_vehicle_movement_matrix_missing_count": (
            final_movement_direct_replay_last_queue_max_gap_count
        ),
        "final_movement_direct_replay_last_queue_missing_movement_plan_count": (
            final_movement_direct_replay_last_queue_missing_movement_plan_count
        ),
        "final_movement_direct_replay_last_queue_top_movement_gaps": (
            final_movement_direct_replay_last_queue_top_movement_gaps
        ),
        "final_movement_direct_replay_last_queue_file": ""
        if final_movement_direct_replay_last_queue_report is None
        else str(final_movement_direct_replay_last_queue_report.get("queue_file", "")),
        "final_movement_rebuild_internal_regression_restore_status": "skipped"
        if final_movement_rebuild_internal_regression_restore_report is None
        else str(final_movement_rebuild_internal_regression_restore_report.get("status", "fail")),
        "final_movement_rebuild_internal_regression_restore_sumo_load_status": "skipped"
        if final_movement_rebuild_internal_regression_restore_sumo_load_report is None
        else str(final_movement_rebuild_internal_regression_restore_sumo_load_report.get("status", "fail")),
        "final_movement_rebuild_internal_regression_restore_reference_delta_file": ""
        if final_movement_rebuild_internal_regression_restore_reference_delta_report is None
        else str(final_movement_rebuild_internal_regression_restore_reference_delta_report.get("summary_file", "")),
        "final_movement_rebuild_internal_regression_restore_promotion_status": "skipped"
        if final_movement_rebuild_internal_regression_restore_promotion_report is None
        else str(final_movement_rebuild_internal_regression_restore_promotion_report.get("status", "fail")),
        "post_teacher_tls_connection_repair_reference_promotion_status": str(
            post_teacher_tls_connection_repair_reference_promotion_report.get("status", "skipped")
        ),
        "post_teacher_tls_connection_repair_reference_promotion_reason": str(
            post_teacher_tls_connection_repair_reference_promotion_report.get("reason", "")
        ),
        "reference_join_tls_semantic_delta_score": _tls_semantic_delta_score(reference_join_audit_report),
        "reference_join_tls_control_review_status": "skipped"
        if reference_join_audit_report is None
        else str(reference_join_audit_report.get("tls_control_review_status", "skipped")),
        "reference_join_tls_control_review_queue_count": 0
        if reference_join_audit_report is None
        else int(reference_join_audit_report.get("tls_control_review_queue_count", 0) or 0),
        "reference_join_tls_control_review_category_counts": _tls_control_review_category_counts(
            reference_join_audit_report
        ),
        "reference_join_tls_controller_alignment": {}
        if reference_join_audit_report is None
        else reference_join_audit_report.get("tls_controller_alignment", {}),
        "reference_join_tls_controller_alignment_status": "skipped"
        if reference_join_audit_report is None
        else str(
            (reference_join_audit_report.get("tls_controller_alignment", {}) or {}).get(
                "status", "skipped"
            )
        ),
        "reference_join_tls_controller_alignment_pair_count": 0
        if reference_join_audit_report is None
        else int(
            (reference_join_audit_report.get("tls_controller_alignment", {}) or {}).get(
                "pair_count", 0
            )
            or 0
        ),
        "reference_join_tls_controller_possible_split_count": 0
        if reference_join_audit_report is None
        else int(
            (reference_join_audit_report.get("tls_controller_alignment", {}) or {}).get(
                "possible_candidate_split_reference_count", 0
            )
            or 0
        ),
        "reference_join_tls_controller_possible_merge_count": 0
        if reference_join_audit_report is None
        else int(
            (reference_join_audit_report.get("tls_controller_alignment", {}) or {}).get(
                "possible_candidate_merge_controller_count", 0
            )
            or 0
        ),
        "reference_join_tls_high_confidence_movement_gap_candidate_count": 0
        if reference_join_audit_report is None
        else int(
            (reference_join_audit_report.get("tls_controller_alignment", {}) or {}).get(
                "high_confidence_movement_gap_candidate_count", 0
            )
            or 0
        ),
        "reference_join_tls_high_confidence_missing_direction_instance_count": 0
        if reference_join_audit_report is None
        else int(
            (reference_join_audit_report.get("tls_controller_alignment", {}) or {}).get(
                "high_confidence_missing_direction_instance_count", 0
            )
            or 0
        ),
        "reference_join_tls_controller_alignment_repair_safe": False
        if reference_join_audit_report is None
        else bool(
            (reference_join_audit_report.get("tls_controller_alignment", {}) or {}).get(
                "repair_safe", False
            )
        ),
        "reference_join_network_structural_junction_type_missing_counts": {}
        if reference_join_audit_report is None
        else reference_join_audit_report.get("network_structural_junction_type_missing_counts", {}),
        "reference_join_network_structural_junction_type_extra_counts": {}
        if reference_join_audit_report is None
        else reference_join_audit_report.get("network_structural_junction_type_extra_counts", {}),
        "reference_join_reference_network_structural_summary": {}
        if reference_join_audit_report is None
        else reference_join_audit_report.get("reference_network_structural_summary", {}),
        "reference_join_candidate_network_structural_summary": {}
        if reference_join_audit_report is None
        else reference_join_audit_report.get("candidate_network_structural_summary", {}),
        "reference_join_aggregation_status": "skipped"
        if reference_join_aggregation_report is None
        else reference_join_aggregation_report.get("junction_aggregation_status", reference_join_aggregation_report.get("status", "fail")),
        "reference_join_aggregation_candidate_count": 0
        if reference_join_aggregation_report is None
        else reference_join_aggregation_report.get("junction_aggregation_candidate_count", 0),
        "reference_join_aggregation_plan_file": ""
        if reference_join_aggregation_report is None
        else str(reference_join_aggregation_report.get("junction_aggregation_plan_file", "")),
        "reference_join_aggregation_candidates_file": ""
        if reference_join_aggregation_report is None
        else str(reference_join_aggregation_report.get("junction_aggregation_candidates_file", "")),
        "reference_join_aggregation_variant_file": ""
        if reference_join_aggregation_report is None
        else str(reference_join_aggregation_report.get("junction_aggregation_variant_file", "")),
        "reference_join_aggregation_preservation_status": "skipped"
        if reference_join_aggregation_report is None
        else str(reference_join_aggregation_report.get("junction_aggregation_preservation_status", "not_run")),
        "reference_join_aggregation_preservation_audit_file": ""
        if reference_join_aggregation_report is None
        else str(reference_join_aggregation_report.get("junction_aggregation_preservation_audit_file", "")),
        "reference_join_aggregation_removed_normal_edge_count": 0
        if reference_join_aggregation_report is None
        else reference_join_aggregation_report.get("junction_aggregation_removed_normal_edge_count", 0),
        "reference_join_aggregation_removed_normal_edge_type_counts": {}
        if reference_join_aggregation_report is None
        else reference_join_aggregation_report.get("junction_aggregation_removed_normal_edge_type_counts", {}),
        "reference_join_aggregation_removed_normal_edge_mode_counts": {}
        if reference_join_aggregation_report is None
        else reference_join_aggregation_report.get("junction_aggregation_removed_normal_edge_mode_counts", {}),
        "reference_join_aggregation_lost_shared_connection_count": 0
        if reference_join_aggregation_report is None
        else reference_join_aggregation_report.get("junction_aggregation_lost_shared_connection_count", 0),
        "reference_join_aggregation_new_dangling_shared_normal_edge_count": 0
        if reference_join_aggregation_report is None
        else reference_join_aggregation_report.get("junction_aggregation_new_dangling_shared_normal_edge_count", 0),
        "teacher_guided_repair_queue_status": "skipped"
        if teacher_guided_repair_queue_report is None
        else teacher_guided_repair_queue_report.get("status", "fail"),
        "teacher_guided_scoped_tls_cell_batch_status": "skipped"
        if teacher_guided_scoped_tls_cell_batch_report is None
        else teacher_guided_scoped_tls_cell_batch_report.get("status", "fail"),
        "teacher_guided_scoped_tls_cell_batch_count": 0
        if teacher_guided_scoped_tls_cell_batch_report is None
        else teacher_guided_scoped_tls_cell_batch_report.get("cell_count", 0),
        "teacher_guided_scoped_tls_cell_batch_pass_count": 0
        if teacher_guided_scoped_tls_cell_batch_report is None
        else teacher_guided_scoped_tls_cell_batch_report.get("pass_count", 0),
        "teacher_guided_scoped_tls_cell_batch_blocked_count": 0
        if teacher_guided_scoped_tls_cell_batch_report is None
        else teacher_guided_scoped_tls_cell_batch_report.get("blocked_count", 0),
        "teacher_guided_scoped_tls_cell_batch_report_file": ""
        if teacher_guided_scoped_tls_cell_batch_report is None
        else str(teacher_guided_scoped_tls_cell_batch_report.get("report_file", "")),
        "teacher_guided_scoped_tls_cell_batch_manifest_file": ""
        if teacher_guided_scoped_tls_cell_batch_report is None
        else str(teacher_guided_scoped_tls_cell_batch_report.get("artifact_manifest_file", "")),
        "teacher_guided_scoped_tls_cell_batch_manifest_status": "skipped"
        if teacher_guided_scoped_tls_cell_batch_report is None
        else str(teacher_guided_scoped_tls_cell_batch_report.get("artifact_manifest_status", "fail")),
        "teacher_guided_scoped_tls_cell_batch_artifact_hash_gate": {}
        if teacher_guided_scoped_tls_cell_batch_report is None
        else teacher_guided_scoped_tls_cell_batch_report.get("artifact_hash_gate", {}),
        "run_teacher_guided_repair_after_build": run_teacher_guided_repair_after_build,
        "teacher_guided_repair_candidate_count": 0
        if teacher_guided_repair_queue_report is None
        else teacher_guided_repair_queue_report.get("repair_candidate_count", 0),
        "teacher_guided_repair_ready_candidate_count": 0
        if teacher_guided_repair_queue_report is None
        else teacher_guided_repair_queue_report.get("ready_candidate_count", 0),
        "teacher_guided_repair_expanded_scope_candidate_count": 0
        if teacher_guided_repair_run_report is None
        else teacher_guided_repair_run_report.get("expanded_scope_candidate_count", 0),
        "teacher_guided_repair_expanded_scope_pass_candidate_count": 0
        if teacher_guided_repair_run_report is None
        else teacher_guided_repair_run_report.get("expanded_scope_pass_candidate_count", 0),
        "teacher_guided_repair_exemplar_ready_candidate_count": teacher_guided_exemplar_ready_candidate_count,
        "teacher_guided_repair_exemplar_movement_signature_count": teacher_guided_exemplar_movement_signature_count,
        "teacher_guided_repair_movement_gap_candidate_count": teacher_guided_movement_gap_candidate_count,
        "teacher_guided_repair_max_vehicle_movement_matrix_missing_count": teacher_guided_max_vehicle_movement_matrix_missing_count,
        "teacher_guided_repair_missing_movement_plan_count": teacher_guided_missing_movement_plan_count,
        "teacher_guided_repair_top_movement_gaps": teacher_guided_top_movement_gaps,
        "teacher_guided_repair_queued_case_count": 0
        if teacher_guided_repair_queue_report is None
        else teacher_guided_repair_queue_report.get("queued_case_count", 0),
        "teacher_guided_repair_tls_candidate_count": 0
        if teacher_guided_repair_queue_report is None
        else teacher_guided_repair_queue_report.get("tls_repair_candidate_count", 0),
        "teacher_guided_repair_tls_category_counts": {}
        if teacher_guided_repair_queue_report is None
        else teacher_guided_repair_queue_report.get("tls_repair_category_counts", {}),
        "teacher_guided_repair_queue_truncated": False
        if teacher_guided_repair_queue_report is None
        else bool(teacher_guided_repair_queue_report.get("queue_truncated", False)),
        "teacher_guided_repair_max_ready_candidates": ""
        if teacher_guided_repair_queue_report is None
        else teacher_guided_repair_queue_report.get("max_ready_candidates", ""),
        "teacher_guided_repair_queue_file": ""
        if teacher_guided_repair_queue_report is None
        else str(teacher_guided_repair_queue_report.get("queue_file", "")),
        "teacher_guided_repair_queue_csv_file": ""
        if teacher_guided_repair_queue_report is None
        else str(teacher_guided_repair_queue_report.get("queue_csv_file", "")),
        "teacher_guided_repair_seed_source": teacher_guided_repair_seed_source,
        "teacher_guided_repair_requires_reference_promotion": teacher_guided_repair_requires_reference_promotion,
        "teacher_guided_repair_reference_promotion_status": str(
            teacher_guided_repair_reference_promotion_report.get("status", "skipped")
        ),
        "teacher_guided_repair_reference_promotion_reason": str(
            teacher_guided_repair_reference_promotion_report.get("reason", "")
        ),
        "teacher_guided_repair_plain_export_status": "skipped"
        if teacher_guided_plain_export_report is None
        else teacher_guided_plain_export_report.get("status", "fail"),
        "teacher_guided_repair_raw_node_file": ""
        if teacher_guided_plain_export_report is None
        else str(teacher_guided_plain_export_report.get("raw_node_file", "")),
        "teacher_guided_repair_raw_edge_file": ""
        if teacher_guided_plain_export_report is None
        else str(teacher_guided_plain_export_report.get("raw_edge_file", "")),
        "teacher_guided_repair_raw_connection_file": ""
        if teacher_guided_plain_export_report is None
        else str(teacher_guided_plain_export_report.get("raw_connection_file", "")),
        "teacher_guided_repair_raw_type_file": ""
        if teacher_guided_plain_export_report is None
        else str(teacher_guided_plain_export_report.get("raw_type_file", "")),
        "teacher_guided_repair_run_status": "skipped"
        if teacher_guided_repair_run_report is None
        else teacher_guided_repair_run_report.get("status", "fail"),
        "teacher_guided_repair_parity_gate_status": "skipped"
        if teacher_guided_repair_run_report is None
        else teacher_guided_repair_run_report.get("parity_gate_status", "fail"),
        "teacher_guided_repair_promotion_gate_status": "skipped"
        if teacher_guided_repair_run_report is None
        else teacher_guided_repair_run_report.get("promotion_gate_status", "fail"),
        "teacher_guided_repair_promotion_gate_file": ""
        if teacher_guided_repair_run_report is None
        else str(teacher_guided_repair_run_report.get("promotion_gate_file", "")),
        "teacher_guided_repair_semantic_failure_counts": {}
        if teacher_guided_repair_run_report is None
        else teacher_guided_repair_run_report.get("semantic_failure_counts", {}),
        "teacher_guided_repair_semantic_layer_gate_counts": {}
        if teacher_guided_repair_run_report is None
        else teacher_guided_repair_run_report.get("semantic_layer_gate_counts", {}),
        "teacher_guided_repair_approach_integrity_status": "skipped"
        if teacher_guided_repair_run_report is None
        else teacher_guided_repair_run_report.get("approach_integrity_status", "blocked"),
        "teacher_guided_repair_approach_integrity_failure_counts": {}
        if teacher_guided_repair_run_report is None
        else teacher_guided_repair_run_report.get("approach_integrity_failure_counts", {}),
        "teacher_guided_repair_template_contexts": []
        if teacher_guided_repair_run_report is None
        else teacher_guided_repair_run_report.get("teacher_pattern_contexts", []),
        "teacher_guided_repair_attempted_candidate_count": 0
        if teacher_guided_repair_run_report is None
        else teacher_guided_repair_run_report.get("attempted_candidate_count", 0),
        "teacher_guided_repair_pass_candidate_count": 0
        if teacher_guided_repair_run_report is None
        else teacher_guided_repair_run_report.get("pass_candidate_count", 0),
        **_teacher_guided_application_stats(
            teacher_guided_repair_run_report, teacher_guided_repair_best_variant_file
        ),
        "teacher_guided_repair_run_report_file": ""
        if teacher_guided_repair_run_report is None
        else str(teacher_guided_repair_run_report.get("run_report_file", "")),
        "teacher_guided_probe_matrix_status": "skipped"
        if teacher_guided_probe_matrix_report is None
        else str(teacher_guided_probe_matrix_report.get("status", "fail")),
        "teacher_guided_probe_matrix_file": ""
        if teacher_guided_probe_matrix_report is None
        else str(teacher_guided_probe_matrix_report.get("matrix_file", "")),
        "teacher_guided_probe_matrix_probe_count": 0
        if teacher_guided_probe_matrix_report is None
        else teacher_guided_probe_matrix_report.get("probe_count", 0),
        "teacher_guided_probe_matrix_all_parity_gate_pass": False
        if teacher_guided_probe_matrix_report is None
        else bool(teacher_guided_probe_matrix_report.get("all_parity_gate_pass", False)),
        "teacher_guided_probe_matrix_all_promotion_gate_pass": False
        if teacher_guided_probe_matrix_report is None
        else bool(teacher_guided_probe_matrix_report.get("all_promotion_gate_pass", False)),
        "teacher_guided_probe_matrix_all_road_continuity_gate_pass": False
        if teacher_guided_probe_matrix_report is None
        else bool(teacher_guided_probe_matrix_report.get("all_road_continuity_gate_pass", False)),
        "teacher_guided_probe_matrix_missing_junction_ids": []
        if teacher_guided_probe_matrix_report is None
        else teacher_guided_probe_matrix_report.get("missing_junction_ids", []),
        "teacher_guided_repair_best_variant_file": ""
        if teacher_guided_repair_best_variant_file is None
        else str(teacher_guided_repair_best_variant_file),
        "road_connectivity_parity_audit_status": "skipped"
        if road_connectivity_parity_audit_report is None
        else str(road_connectivity_parity_audit_report.get("status", "blocked")),
        "road_connectivity_parity_audit_report_file": ""
        if road_connectivity_parity_audit_report is None
        else str(road_connectivity_parity_audit_report.get("report_file", "")),
        "road_connectivity_parity_audit_road_template_report_file": ""
        if road_connectivity_parity_audit_report is None
        else str(road_connectivity_parity_audit_report.get("road_template_report_file", "")),
        "road_connectivity_parity_audit_connection_topology_report_file": ""
        if road_connectivity_parity_audit_report is None
        else str(road_connectivity_parity_audit_report.get("connection_topology_report_file", "")),
        "road_connectivity_parity_audit_internal_movement_report_file": ""
        if road_connectivity_parity_audit_report is None
        else str(road_connectivity_parity_audit_report.get("internal_movement_report_file", "")),
        "reference_road_alignment_status": "skipped"
        if road_connectivity_parity_audit_report is None
        else str(
            road_connectivity_parity_audit_report.get("reference_road_alignment", {}).get(
                "status", "blocked"
            )
            if isinstance(road_connectivity_parity_audit_report.get("reference_road_alignment", {}), Mapping)
            else "blocked"
        ),
        "reference_road_alignment_report_file": ""
        if road_connectivity_parity_audit_report is None
        else str(road_connectivity_parity_audit_report.get("reference_road_alignment_report_file", "")),
        "reference_road_alignment_additional_file": ""
        if road_connectivity_parity_audit_report is None
        else str(road_connectivity_parity_audit_report.get("reference_road_alignment_additional_file", "")),
        "road_connectivity_replay_status": "skipped"
        if road_connectivity_replay_report is None
        else str(road_connectivity_replay_report.get("status", "fail")),
        "road_connectivity_replay_gate_status": "skipped"
        if road_connectivity_replay_report is None
        else _road_connectivity_gate_status(road_connectivity_replay_report),
        "road_connectivity_replay_sumo_load_status": "skipped"
        if road_connectivity_replay_report is None
        else str(road_connectivity_replay_report.get("sumo_load_status", "")),
        "road_connectivity_replay_best_variant_file": ""
        if road_connectivity_replay_report is None
        else str(road_connectivity_replay_report.get("output_file", "")),
        "road_connectivity_promoted_variant_file": ""
        if road_connectivity_promoted_variant_file is None
        else str(road_connectivity_promoted_variant_file),
        "road_connectivity_promoted_variant_reason": road_connectivity_promoted_variant_reason,
        "road_connectivity_replay_run_report_file": ""
        if road_connectivity_replay_report is None
        else str(road_connectivity_replay_report.get("run_report_file", "")),
        "road_connectivity_replay_gate_counts": _road_connectivity_gate_counts(road_connectivity_replay_report),
        "road_connectivity_split_root_alias_repair_status": "skipped"
        if road_connectivity_split_root_alias_repair_report is None
        else str(road_connectivity_split_root_alias_repair_report.get("status", "fail")),
        "road_connectivity_split_root_alias_repair_file": ""
        if road_connectivity_split_root_alias_repair_report is None
        else str(road_connectivity_split_root_alias_repair_report.get("output_file", "")),
        "road_connectivity_split_root_alias_repair_report_file": ""
        if road_connectivity_split_root_alias_repair_report is None
        else str(road_connectivity_split_root_alias_repair_report.get("report_file", "")),
        "road_connection_topology_replay_status": "skipped"
        if road_connection_topology_replay_report is None
        else str(road_connection_topology_replay_report.get("status", "fail")),
        "road_connection_topology_replay_file": ""
        if road_connection_topology_replay_report is None
        else str(road_connection_topology_replay_report.get("output_file", "")),
        "road_connection_topology_replay_report_file": ""
        if road_connection_topology_replay_report is None
        else str(road_connection_topology_replay_report.get("report_file", "")),
        "road_connectivity_seed_probe_status": "skipped"
        if road_connectivity_seed_probe_report is None
        else str(road_connectivity_seed_probe_report.get("status", "fail")),
        "road_connectivity_seed_probe_file": ""
        if road_connectivity_seed_probe_report is None
        else str(road_connectivity_seed_probe_report.get("report_file", "")),
        "road_connectivity_seed_probe_edge_delta_count": 0
        if road_connectivity_seed_probe_report is None
        else road_connectivity_seed_probe_report.get("edge_delta_count", 0),
        "road_connectivity_seed_probe_connection_delta_count": 0
        if road_connectivity_seed_probe_report is None
        else road_connectivity_seed_probe_report.get("connection_delta_count", 0),
        "road_connectivity_seed_probe_candidate_missing_seed_edge_ids": []
        if road_connectivity_seed_probe_report is None
        else road_connectivity_seed_probe_report.get("candidate_missing_seed_edge_ids", []),
        "teacher_guided_direct_replay_status": "skipped"
        if teacher_guided_direct_replay_report is None
        else str(teacher_guided_direct_replay_report.get("status", "fail")),
        "teacher_guided_direct_replay_variant_file": ""
        if teacher_guided_direct_replay_best_variant_file is None
        else str(teacher_guided_direct_replay_best_variant_file),
        "teacher_guided_direct_replay_candidate_index": ""
        if teacher_guided_direct_replay_report is None
        else teacher_guided_direct_replay_report.get("candidate_index", ""),
        "teacher_guided_direct_replay_junction_id": ""
        if teacher_guided_direct_replay_report is None
        else str(teacher_guided_direct_replay_report.get("junction_id", "")),
        "teacher_guided_direct_replay_reference_delta_file": ""
        if teacher_guided_direct_replay_reference_delta_report is None
        else str(teacher_guided_direct_replay_reference_delta_report.get("summary_file", "")),
        "teacher_guided_direct_replay_reference_promotion_status": str(
            teacher_guided_direct_replay_reference_promotion_report.get("status", "skipped")
        ),
        "teacher_guided_direct_replay_reference_promotion_reason": str(
            teacher_guided_direct_replay_reference_promotion_report.get("reason", "")
        ),
        "teacher_guided_repair_best_expanded_scope_net_file": ""
        if teacher_guided_repair_best_expanded_scope_net_file is None
        else str(teacher_guided_repair_best_expanded_scope_net_file),
        "reference_hierarchy_status": "skipped"
        if reference_hierarchy_audit_report is None
        else reference_hierarchy_audit_report.get(
            "reference_hierarchy_status", reference_hierarchy_audit_report.get("status", "fail")
        ),
        "reference_hierarchy_audit_candidate_layer": reference_hierarchy_audit_candidate_layer,
        "reference_hierarchy_audit_candidate_net_file": ""
        if reference_hierarchy_audit_candidate_net_file is None
        else str(reference_hierarchy_audit_candidate_net_file),
        "reference_hierarchy_issue_count": 0
        if reference_hierarchy_audit_report is None
        else reference_hierarchy_audit_report.get("high_hierarchy_issue_count", 0),
        "reference_hierarchy_decision_counts": {}
        if reference_hierarchy_audit_report is None
        else reference_hierarchy_audit_report.get("decision_counts", {}),
        "reference_hierarchy_corridor_match_basis_counts": {}
        if reference_hierarchy_audit_report is None
        else reference_hierarchy_audit_report.get("corridor_match_basis_counts", {}),
        "reference_hierarchy_same_name_match_status_counts": {}
        if reference_hierarchy_audit_report is None
        else reference_hierarchy_audit_report.get("same_name_match_status_counts", {}),
        "reference_hierarchy_cases_file": ""
        if reference_hierarchy_audit_report is None
        else str(reference_hierarchy_audit_report.get("cases_file", "")),
        "reference_hierarchy_type_comparison_file": ""
        if reference_hierarchy_audit_report is None
        else str(reference_hierarchy_audit_report.get("type_comparison_file", "")),
        "reference_hierarchy_audit_report_file": ""
        if reference_hierarchy_audit_report is None
        else str(reference_hierarchy_audit_report.get("summary_file", "")),
        "reference_hierarchy_type_repair_status": "skipped"
        if reference_hierarchy_type_repair_report is None
        else str(
            reference_hierarchy_type_repair_report.get(
                "reference_hierarchy_type_repair_status",
                reference_hierarchy_type_repair_report.get("status", "fail"),
            )
        ),
        "reference_hierarchy_type_repair_count": 0
        if reference_hierarchy_type_repair_report is None
        else reference_hierarchy_type_repair_report.get("reference_hierarchy_type_repair_count", 0),
        "reference_hierarchy_type_repair_variant_file": ""
        if reference_hierarchy_type_repair_report is None
        else str(reference_hierarchy_type_repair_report.get("reference_hierarchy_type_repair_variant_file", "")),
        "reference_hierarchy_type_repair_plan_file": ""
        if reference_hierarchy_type_repair_report is None
        else str(reference_hierarchy_type_repair_report.get("reference_hierarchy_type_repair_plan_file", "")),
        "reference_hierarchy_type_repair_repairs_file": ""
        if reference_hierarchy_type_repair_report is None
        else str(reference_hierarchy_type_repair_report.get("reference_hierarchy_type_repair_repairs_file", "")),
        "reference_hierarchy_type_repair_sumo_load_status": "skipped"
        if reference_hierarchy_type_repair_sumo_load_report is None
        else str(reference_hierarchy_type_repair_sumo_load_report.get("status", "fail")),
        "reference_hierarchy_type_repair_audit_status": "skipped"
        if reference_hierarchy_type_repair_audit_report is None
        else str(
            reference_hierarchy_type_repair_audit_report.get(
                "reference_hierarchy_status", reference_hierarchy_type_repair_audit_report.get("status", "fail")
            )
        ),
        "reference_hierarchy_type_repair_issue_count": 0
        if reference_hierarchy_type_repair_audit_report is None
        else reference_hierarchy_type_repair_audit_report.get("high_hierarchy_issue_count", 0),
        "reference_hierarchy_type_repair_promotion_status": str(
            reference_hierarchy_type_repair_promotion_report.get("status", "skipped")
        ),
        "reference_hierarchy_type_repair_promotion_reason": str(
            reference_hierarchy_type_repair_promotion_report.get("reason", "")
        ),
        "corridor_edit_ledger_status": "skipped"
        if corridor_edit_ledger_report is None
        else str(
            corridor_edit_ledger_report.get(
                "corridor_edit_ledger_status",
                corridor_edit_ledger_report.get("status", "fail"),
            )
        ),
        "corridor_edit_ledger_file": ""
        if corridor_edit_ledger_report is None
        else str(corridor_edit_ledger_report.get("ledger_file", "")),
        "corridor_edit_ledger_manifest_file": ""
        if corridor_edit_ledger_report is None
        else str(corridor_edit_ledger_report.get("manifest_file", "")),
        "corridor_geometry_simplification_status": "skipped"
        if corridor_geometry_simplification_report is None
        else str(
            corridor_geometry_simplification_report.get(
                "corridor_geometry_simplification_status",
                corridor_geometry_simplification_report.get("status", "fail"),
            )
        ),
        "corridor_geometry_simplification_candidate_node_count": 0
        if corridor_geometry_simplification_report is None
        else corridor_geometry_simplification_report.get("candidate_node_count", 0),
        "corridor_geometry_simplification_removed_node_count": 0
        if corridor_geometry_simplification_report is None
        else corridor_geometry_simplification_report.get("removed_node_count", 0),
        "corridor_geometry_simplification_semantic_preservation_status": "skipped"
        if corridor_geometry_simplification_report is None
        else str(corridor_geometry_simplification_report.get("semantic_preservation_status", "fail")),
        "corridor_geometry_simplification_normal_connection_missing_count": 0
        if corridor_geometry_simplification_report is None
        else int(
            (corridor_geometry_simplification_report.get("alias_normalized_connection_audit", {}) or {}).get(
                "normal_missing_count", 0
            )
        ),
        "corridor_geometry_simplification_normal_connection_extra_count": 0
        if corridor_geometry_simplification_report is None
        else int(
            (corridor_geometry_simplification_report.get("alias_normalized_connection_audit", {}) or {}).get(
                "normal_extra_count", 0
            )
        ),
        "corridor_geometry_simplification_sumo_load_status": "skipped"
        if corridor_geometry_simplification_sumo_load_report is None
        else str(corridor_geometry_simplification_sumo_load_report.get("status", "fail")),
        "corridor_geometry_simplification_reference_tls_semantic_delta_score": _tls_semantic_delta_score(
            corridor_geometry_simplification_reference_delta_report
        ),
        "corridor_geometry_simplification_promotion_status": str(
            corridor_geometry_simplification_promotion_report.get("status", "skipped")
        ),
        "corridor_geometry_simplification_promotion_reason": str(
            corridor_geometry_simplification_promotion_report.get("reason", "")
        ),
        "corridor_geometry_simplification_variant_file": ""
        if corridor_geometry_simplification_report is None
        else str(corridor_geometry_simplification_report.get("variant_file", "")),
        "reference_scope_status": "skipped"
        if reference_scope_audit_report is None
        else reference_scope_audit_report.get("reference_scope_status", reference_scope_audit_report.get("status", "fail")),
        "reference_scope_audit_candidate_layer": reference_scope_candidate_layer,
        "reference_scope_audit_candidate_net_file": ""
        if reference_scope_candidate_net_file is None
        else str(reference_scope_candidate_net_file),
        "reference_scope_prune_candidate_count": 0
        if reference_scope_audit_report is None
        else reference_scope_audit_report.get("prune_candidate_count", 0),
        "reference_scope_audit_report_file": ""
        if reference_scope_audit_report is None
        else str(reference_scope_audit_report.get("report_file", "")),
        "reference_scope_prune_candidates_file": ""
        if reference_scope_audit_report is None
        else str(reference_scope_audit_report.get("prune_candidates_file", "")),
        "reference_scope_pruning_status": "skipped"
        if reference_scope_pruning_report is None
        else reference_scope_pruning_report.get("scope_pruning_status", reference_scope_pruning_report.get("status", "fail")),
        "reference_scope_pruning_removed_edge_count": 0
        if reference_scope_pruning_report is None
        else reference_scope_pruning_report.get("scope_pruning_removed_edge_count", 0),
        "reference_scope_pruning_variant_file": ""
        if reference_scope_pruning_report is None
        else str(reference_scope_pruning_report.get("scope_pruning_variant_file", "")),
        "reference_scope_pruning_plan_file": ""
        if reference_scope_pruning_report is None
        else str(reference_scope_pruning_report.get("scope_pruning_plan_file", "")),
        "reference_scope_post_prune_audit_status": "skipped"
        if reference_scope_post_prune_audit_report is None
        else reference_scope_post_prune_audit_report.get(
            "reference_scope_status", reference_scope_post_prune_audit_report.get("status", "fail")
        ),
        "reference_scope_post_prune_audit_report_file": ""
        if reference_scope_post_prune_audit_report is None
        else str(reference_scope_post_prune_audit_report.get("report_file", "")),
        "reference_scope_pruning_sumo_load_status": "skipped"
        if reference_scope_pruning_sumo_load_report is None
        else str(reference_scope_pruning_sumo_load_report.get("status", "fail")),
        "reference_scope_pruning_promotion_status": str(
            reference_scope_pruning_promotion_report.get("status", "skipped")
        ),
        "reference_scope_pruning_promotion_reason": str(
            reference_scope_pruning_promotion_report.get("reason", "")
        ),
        "reference_scope_pruning_promotion_checks": reference_scope_pruning_promotion_report.get("checks", {}),
        "reference_scope_final_audit_status": "skipped"
        if reference_scope_final_audit_report is None
        else reference_scope_final_audit_report.get(
            "reference_scope_status", reference_scope_final_audit_report.get("status", "fail")
        ),
        "reference_scope_final_audit_report_file": ""
        if reference_scope_final_audit_report is None
        else str(reference_scope_final_audit_report.get("report_file", "")),
        "reference_scope_final_pruning_status": "skipped"
        if reference_scope_final_pruning_report is None
        else reference_scope_final_pruning_report.get(
            "scope_pruning_status", reference_scope_final_pruning_report.get("status", "fail")
        ),
        "reference_scope_final_pruning_variant_file": ""
        if reference_scope_final_pruning_report is None
        else str(reference_scope_final_pruning_report.get("scope_pruning_variant_file", "")),
        "reference_scope_final_pruning_plan_file": ""
        if reference_scope_final_pruning_report is None
        else str(reference_scope_final_pruning_report.get("scope_pruning_plan_file", "")),
        "reference_scope_final_post_prune_audit_status": "skipped"
        if reference_scope_final_post_prune_audit_report is None
        else reference_scope_final_post_prune_audit_report.get(
            "reference_scope_status", reference_scope_final_post_prune_audit_report.get("status", "fail")
        ),
        "reference_scope_final_post_prune_audit_report_file": ""
        if reference_scope_final_post_prune_audit_report is None
        else str(reference_scope_final_post_prune_audit_report.get("report_file", "")),
        "reference_scope_final_sumo_load_status": "skipped"
        if reference_scope_final_sumo_load_report is None
        else str(reference_scope_final_sumo_load_report.get("status", "fail")),
        "reference_scope_final_promotion_status": str(
            reference_scope_final_promotion_report.get("status", "skipped")
        ),
        "reference_scope_final_promotion_reason": str(
            reference_scope_final_promotion_report.get("reason", "")
        ),
        "reference_scope_final_promotion_checks": reference_scope_final_promotion_report.get("checks", {}),
        "routeability_probe_file": "" if routeability_report is None else str(routeability_report.get("sumocfg_file", "")),
        "missing_key_edges": [] if routeability_report is None else routeability_report.get("missing_key_edges", []),
        "routeability_probe_status": "skipped" if routeability_report is None else routeability_report.get("status", "fail"),
        **routeability_profile,
        "routeability_audit_status": "skipped" if routeability_audit_report is None else routeability_audit_report.get("routeability_status", routeability_audit_report.get("status", "fail")),
        "routeability_audit_report_file": "" if routeability_audit_report is None else str(routeability_audit_report.get("report_file", "")),
        "connection_mode_audit_status": "skipped"
        if connection_mode_audit_report is None
        else str(connection_mode_audit_report.get("status", "fail")),
        "connection_mode_audit_pass_count": 0
        if connection_mode_audit_report is None
        else int(connection_mode_audit_report.get("pass_count", 0)),
        "connection_mode_audit_review_required_count": 0
        if connection_mode_audit_report is None
        else int(connection_mode_audit_report.get("review_required_count", 0)),
        "connection_mode_audit_fail_count": 0
        if connection_mode_audit_report is None
        else int(connection_mode_audit_report.get("fail_count", 0)),
        "connection_mode_audit_report_file": ""
        if connection_mode_audit_report is None
        else str(connection_mode_audit_report.get("report_file", "")),
        "connection_mode_review_overlay_file": ""
        if connection_mode_audit_report is None
        else str(connection_mode_audit_report.get("review_overlay_file", "")),
        "connection_mode_manifest_file": ""
        if connection_mode_audit_report is None
        else str(connection_mode_audit_report.get("manifest_file", "")),
        "standard_nema_scan_status": "skipped"
        if standard_nema_scan_report is None
        else str(standard_nema_scan_report.get("nema_binding_status", standard_nema_scan_report.get("status", "fail"))),
        "standard_nema_eligible_count": 0
        if standard_nema_scan_report is None
        else int((standard_nema_scan_report.get("scan_counts") or {}).get("eligible_count", 0)),
        "standard_nema_review_required_count": 0
        if standard_nema_scan_report is None
        else int((standard_nema_scan_report.get("scan_counts") or {}).get("review_required_count", 0)),
        "standard_nema_report_file": ""
        if standard_nema_scan_report is None
        else str(standard_nema_scan_report.get("report_file", "")),
        "standard_nema_connection_mode_report_file": ""
        if standard_nema_scan_report is None
        else str(standard_nema_scan_report.get("connection_mode_report_file", "")),
        "standard_nema_review_overlay_file": ""
        if standard_nema_scan_report is None
        else str(standard_nema_scan_report.get("review_overlay_file", "")),
        "standard_nema_review_html_file": ""
        if standard_nema_scan_report is None
        else str(standard_nema_scan_report.get("review_html_file", "")),
        "netedit_status": netedit_report.get("netedit_status", "failed"),
        "netedit_binary": netedit_report.get("netedit_binary"),
        "netedit_process_id": netedit_report.get("netedit_process_id"),
        "netedit_window_title": netedit_report.get("netedit_window_title", ""),
        "netedit_network_file": netedit_report.get("netedit_network_file", str(net_file)),
        "reference_visual_detail_status": reference_visual_detail_status,
        "reference_visual_detail_net_file": "" if reference_visual_detail_net_file is None else str(reference_visual_detail_net_file),
        "reference_visual_detail_comparison_net_file": ""
        if reference_visual_detail_comparison_net_file is None
        else str(reference_visual_detail_comparison_net_file),
        "reference_visual_detail_comparison_selection_reason": reference_visual_detail_comparison_selection_reason,
        "reference_visual_detail_tls_candidate_count": ""
        if reference_visual_detail_tls_report is None
        else reference_visual_detail_tls_report.get("tls_candidate_count", ""),
        "reference_visual_detail_tls_cluster_count": ""
        if reference_visual_detail_tls_report is None
        else reference_visual_detail_tls_report.get("tls_cluster_count", ""),
        "reference_visual_detail_tls_aggregation_status": "skipped"
        if reference_visual_detail_tls_aggregation_report is None
        else reference_visual_detail_tls_aggregation_report.get(
            "tls_aggregation_status", reference_visual_detail_tls_aggregation_report.get("status", "fail")
        ),
        "reference_visual_detail_tls_aggregation_variant_file": ""
        if reference_visual_detail_tls_aggregation_report is None
        else str(reference_visual_detail_tls_aggregation_report.get("tls_aggregation_variant_file", "")),
        "reference_visual_detail_tls_aggregation_candidate_count": len(
            reference_visual_detail_tls_aggregation_candidates
        ),
        "reference_visual_detail_tls_aggregation_candidates": reference_visual_detail_tls_aggregation_candidates,
        "reference_visual_detail_tls_aggregated_traffic_light_junction_count": ""
        if reference_visual_detail_tls_aggregation_report is None
        else reference_visual_detail_tls_aggregation_report.get("tls_aggregated_traffic_light_junction_count", ""),
        "reference_visual_detail_tls_aggregated_tl_logic_count": ""
        if reference_visual_detail_tls_aggregation_report is None
        else reference_visual_detail_tls_aggregation_report.get("tls_aggregated_tl_logic_count", ""),
        "reference_visual_detail_tls_aggregated_controlled_connection_count": ""
        if reference_visual_detail_tls_aggregation_report is None
        else reference_visual_detail_tls_aggregation_report.get("tls_aggregated_controlled_connection_count", ""),
        "reference_visual_detail_tls_aggregated_tl_connection_missing_linkindex_count": ""
        if reference_visual_detail_tls_aggregation_report is None
        else reference_visual_detail_tls_aggregation_report.get("tls_aggregated_tl_connection_missing_linkindex_count", ""),
        "reference_visual_detail_tls_controlled_connection_preservation_status": "skipped"
        if reference_visual_detail_tls_aggregation_report is None
        else str(
            reference_visual_detail_tls_aggregation_report.get("tls_controlled_connection_preservation_status", "pass")
        ),
        "reference_visual_detail_tls_controlled_connection_regression_count": 0
        if reference_visual_detail_tls_aggregation_report is None
        else reference_visual_detail_tls_aggregation_report.get("tls_controlled_connection_regression_count", 0),
        "reference_visual_detail_tls_aggregation_reference_delta_status": "skipped"
        if reference_visual_detail_tls_aggregation_reference_delta_report is None
        else reference_visual_detail_tls_aggregation_reference_delta_report.get("network_structural_delta_status", "fail"),
        "reference_visual_detail_tls_aggregation_reference_tls_semantic_delta_score": _tls_semantic_delta_score(
            reference_visual_detail_tls_aggregation_reference_delta_report
        ),
        "reference_visual_detail_tls_aggregation_reference_delta_missing_counts": {}
        if reference_visual_detail_tls_aggregation_reference_delta_report is None
        else reference_visual_detail_tls_aggregation_reference_delta_report.get("network_structural_missing_counts", {}),
        "reference_visual_detail_tls_aggregation_reference_delta_extra_counts": {}
        if reference_visual_detail_tls_aggregation_reference_delta_report is None
        else reference_visual_detail_tls_aggregation_reference_delta_report.get("network_structural_extra_counts", {}),
        "reference_visual_detail_tls_aggregation_reference_delta_file": ""
        if reference_visual_detail_tls_aggregation_reference_delta_report is None
        else str(reference_visual_detail_tls_aggregation_reference_delta_report.get("summary_file", "")),
        "reference_visual_detail_raw_reference_tls_semantic_delta_score": _tls_semantic_delta_score(
            reference_visual_detail_raw_reference_delta_report
        ),
        "reference_visual_detail_raw_reference_delta_file": ""
        if reference_visual_detail_raw_reference_delta_report is None
        else str(reference_visual_detail_raw_reference_delta_report.get("summary_file", "")),
        "reference_visual_detail_tls_aggregation_reference_promotion_status": str(
            reference_visual_detail_tls_aggregation_reference_promotion_report.get("status", "skipped")
        ),
        "reference_visual_detail_tls_aggregation_reference_promotion_reason": str(
            reference_visual_detail_tls_aggregation_reference_promotion_report.get("reason", "")
        ),
        "reference_visual_detail_tls_signal_grouping_status": "skipped"
        if reference_visual_detail_tls_signal_grouping_report is None
        else str(reference_visual_detail_tls_signal_grouping_report.get("tls_signal_grouping_status", "failed")),
        "reference_visual_detail_tls_signal_grouping_reference_tls_semantic_delta_score": _tls_semantic_delta_score(
            reference_visual_detail_tls_signal_grouping_reference_delta_report
        ),
        "reference_visual_detail_tls_signal_grouping_reference_delta_file": ""
        if reference_visual_detail_tls_signal_grouping_reference_delta_report is None
        else str(reference_visual_detail_tls_signal_grouping_reference_delta_report.get("summary_file", "")),
        "reference_visual_detail_tls_signal_grouping_sumo_load_status": "skipped"
        if reference_visual_detail_tls_signal_grouping_sumo_load_report is None
        else str(reference_visual_detail_tls_signal_grouping_sumo_load_report.get("status", "fail")),
        "reference_visual_detail_tls_signal_grouping_reference_promotion_status": str(
            reference_visual_detail_tls_signal_grouping_reference_promotion_report.get("status", "skipped")
        ),
        "reference_visual_detail_tls_signal_grouping_reference_promotion_reason": str(
            reference_visual_detail_tls_signal_grouping_reference_promotion_report.get("reason", "")
        ),
        "reference_visual_detail_tls_low_vehicle_control_status": "skipped"
        if reference_visual_detail_tls_low_vehicle_control_report is None
        else str(
            reference_visual_detail_tls_low_vehicle_control_report.get(
                "tls_low_vehicle_control_status",
                reference_visual_detail_tls_low_vehicle_control_report.get("status", "fail"),
            )
        ),
        "reference_visual_detail_tls_low_vehicle_control_variant_file": ""
        if reference_visual_detail_tls_low_vehicle_control_report is None
        else str(reference_visual_detail_tls_low_vehicle_control_report.get("tls_low_vehicle_control_variant_file", "")),
        "reference_visual_detail_tls_low_vehicle_control_selected_tllogic_count": 0
        if reference_visual_detail_tls_low_vehicle_control_report is None
        else reference_visual_detail_tls_low_vehicle_control_report.get(
            "tls_low_vehicle_control_selected_tllogic_count", 0
        ),
        "reference_visual_detail_tls_low_vehicle_control_removed_connection_count": 0
        if reference_visual_detail_tls_low_vehicle_control_report is None
        else reference_visual_detail_tls_low_vehicle_control_report.get(
            "tls_low_vehicle_control_removed_connection_count", 0
        ),
        "reference_visual_detail_tls_low_vehicle_control_candidate_count": len(
            reference_visual_detail_tls_low_vehicle_control_candidates
        ),
        "reference_visual_detail_tls_low_vehicle_control_candidates": reference_visual_detail_tls_low_vehicle_control_candidates,
        "reference_visual_detail_tls_low_vehicle_control_sumo_load_status": "skipped"
        if reference_visual_detail_tls_low_vehicle_control_sumo_load_report is None
        else str(reference_visual_detail_tls_low_vehicle_control_sumo_load_report.get("status", "fail")),
        "reference_visual_detail_tls_low_vehicle_control_reference_tls_semantic_delta_score": _tls_semantic_delta_score(
            reference_visual_detail_tls_low_vehicle_control_reference_delta_report
        ),
        "reference_visual_detail_tls_low_vehicle_control_reference_delta_file": ""
        if reference_visual_detail_tls_low_vehicle_control_reference_delta_report is None
        else str(reference_visual_detail_tls_low_vehicle_control_reference_delta_report.get("summary_file", "")),
        "reference_visual_detail_tls_low_vehicle_control_reference_promotion_status": str(
            reference_visual_detail_tls_low_vehicle_control_reference_promotion_report.get("status", "skipped")
        ),
        "reference_visual_detail_tls_low_vehicle_control_reference_promotion_reason": str(
            reference_visual_detail_tls_low_vehicle_control_reference_promotion_report.get("reason", "")
        ),
        "reference_visual_detail_tls_connection_repair_status": "skipped"
        if reference_visual_detail_tls_connection_repair_report is None
        else str(reference_visual_detail_tls_connection_repair_report.get("status", "fail")),
        "reference_visual_detail_tls_connection_repair_variant_file": ""
        if reference_visual_detail_tls_connection_repair_report is None
        else str(reference_visual_detail_tls_connection_repair_report.get("variant_file", "")),
        "reference_visual_detail_tls_connection_repair_controlled_connection_count_before": ""
        if reference_visual_detail_tls_connection_repair_report is None
        else reference_visual_detail_tls_connection_repair_report.get(
            "candidate_tls_controlled_connection_count_before", ""
        ),
        "reference_visual_detail_tls_connection_repair_controlled_connection_count_after": ""
        if reference_visual_detail_tls_connection_repair_report is None
        else reference_visual_detail_tls_connection_repair_report.get(
            "candidate_tls_controlled_connection_count_after", ""
        ),
        "reference_visual_detail_tls_connection_repair_updated_connection_count": 0
        if reference_visual_detail_tls_connection_repair_report is None
        else reference_visual_detail_tls_connection_repair_report.get("updated_connection_count", 0),
        "reference_visual_detail_tls_connection_repair_skipped_invalid_mapped_linkindex_count": 0
        if reference_visual_detail_tls_connection_repair_report is None
        else reference_visual_detail_tls_connection_repair_report.get(
            "skipped_invalid_mapped_linkindex_connection_count", 0
        ),
        "reference_visual_detail_tls_connection_repair_promotion_status": str(
            reference_visual_detail_tls_connection_repair_promotion_report.get("status", "skipped")
        ),
        "reference_visual_detail_tls_connection_repair_promotion_reason": str(
            reference_visual_detail_tls_connection_repair_promotion_report.get("reason", "")
        ),
        "reference_visual_detail_tls_connection_repair_sumo_load_status": "skipped"
        if reference_visual_detail_tls_connection_repair_sumo_load_report is None
        else str(reference_visual_detail_tls_connection_repair_sumo_load_report.get("status", "fail")),
        "reference_visual_detail_tls_connection_repair_reference_delta_status": "skipped"
        if reference_visual_detail_tls_connection_repair_reference_delta_report is None
        else reference_visual_detail_tls_connection_repair_reference_delta_report.get(
            "network_structural_delta_status", "fail"
        ),
        "reference_visual_detail_tls_connection_repair_reference_tls_semantic_delta_score": _tls_semantic_delta_score(
            reference_visual_detail_tls_connection_repair_reference_delta_report
        ),
        "reference_visual_detail_tls_connection_repair_reference_delta_missing_counts": {}
        if reference_visual_detail_tls_connection_repair_reference_delta_report is None
        else reference_visual_detail_tls_connection_repair_reference_delta_report.get(
            "network_structural_missing_counts", {}
        ),
        "reference_visual_detail_tls_connection_repair_reference_delta_extra_counts": {}
        if reference_visual_detail_tls_connection_repair_reference_delta_report is None
        else reference_visual_detail_tls_connection_repair_reference_delta_report.get(
            "network_structural_extra_counts", {}
        ),
        "reference_visual_detail_tls_connection_repair_reference_delta_file": ""
        if reference_visual_detail_tls_connection_repair_reference_delta_report is None
        else str(reference_visual_detail_tls_connection_repair_reference_delta_report.get("summary_file", "")),
        "reference_visual_detail_tls_connection_repair_summary_file": ""
        if reference_visual_detail_tls_connection_repair_report is None
        else str(reference_visual_detail_tls_connection_repair_report.get("summary_file", "")),
        "reference_visual_detail_tls_effective_source": effective_visual_tls_preservation["source"],
        "reference_visual_detail_tls_effective_network_file": effective_visual_tls_preservation["network_file"],
        "reference_visual_detail_tls_effective_source_controlled_connection_count": effective_visual_tls_preservation[
            "source_controlled_connection_count"
        ],
        "reference_visual_detail_tls_effective_controlled_connection_count": effective_visual_tls_preservation[
            "controlled_connection_count"
        ],
        "reference_visual_detail_tls_effective_controlled_connection_preservation_status": effective_visual_tls_preservation[
            "controlled_connection_preservation_status"
        ],
        "reference_visual_detail_tls_effective_controlled_connection_regression_count": effective_visual_tls_preservation[
            "controlled_connection_regression_count"
        ],
        "reference_visual_detail_netedit_status": reference_visual_detail_netedit_report.get("netedit_status", "not_started"),
        "reference_visual_detail_netedit_network_file": reference_visual_detail_netedit_report.get("netedit_network_file", ""),
        "sumo_gui_status": sumo_gui_report.get("sumo_gui_status", "failed"),
        "sumo_gui_binary": sumo_gui_report.get("sumo_gui_binary"),
        "sumo_gui_process_id": sumo_gui_report.get("sumo_gui_process_id"),
        "sumo_gui_config_file": sumo_gui_report.get("sumo_gui_config_file", ""),
        "sumo_gui_network_file": sumo_gui_report.get("sumo_gui_network_file", str(net_file)),
        "net_file": str(net_file),
        "raw_net_file": str(raw_net_file),
        "connected_core_file": "" if connected_core_report is None else str(connected_core_report.get("connected_core_file", "")),
        "connected_core_discarded_components_review_file": ""
        if connected_core_report is None
        else str(connected_core_report.get("discarded_components_review_file", "")),
        "filtered_osm_file": str(filtered_osm_value) if filtered_osm_value else "",
        "build": build_report,
        "reference_visual_detail_build": reference_visual_detail_build_report,
        "service_passenger_permissions": service_permission_report,
        "reference_visual_detail_service_passenger_permissions": reference_visual_detail_service_permission_report,
        "tls_audit": tls_report,
        "tls_aggregation": tls_aggregation_report or {},
        "reference_visual_detail_tls_audit": reference_visual_detail_tls_report or {},
        "reference_visual_detail_tls_aggregation": reference_visual_detail_tls_aggregation_report or {},
        "reference_visual_detail_tls_connection_repair": reference_visual_detail_tls_connection_repair_report or {},
        "reference_visual_detail_tls_signal_grouping": reference_visual_detail_tls_signal_grouping_report or {},
        "reference_visual_detail_raw_reference_delta": reference_visual_detail_raw_reference_delta_report or {},
        "reference_visual_detail_tls_aggregation_reference_delta": reference_visual_detail_tls_aggregation_reference_delta_report
        or {},
        "reference_visual_detail_tls_aggregation_reference_promotion": reference_visual_detail_tls_aggregation_reference_promotion_report,
        "reference_visual_detail_tls_signal_grouping_reference_delta": reference_visual_detail_tls_signal_grouping_reference_delta_report
        or {},
        "reference_visual_detail_tls_signal_grouping_sumo_load": reference_visual_detail_tls_signal_grouping_sumo_load_report
        or {},
        "reference_visual_detail_tls_signal_grouping_reference_promotion": reference_visual_detail_tls_signal_grouping_reference_promotion_report,
        "reference_visual_detail_tls_low_vehicle_control": reference_visual_detail_tls_low_vehicle_control_report
        or {},
        "reference_visual_detail_tls_low_vehicle_control_sumo_load": reference_visual_detail_tls_low_vehicle_control_sumo_load_report
        or {},
        "reference_visual_detail_tls_low_vehicle_control_reference_delta": reference_visual_detail_tls_low_vehicle_control_reference_delta_report
        or {},
        "reference_visual_detail_tls_low_vehicle_control_reference_promotion": reference_visual_detail_tls_low_vehicle_control_reference_promotion_report,
        "post_teacher_tls_low_vehicle_control": post_teacher_tls_low_vehicle_control_report or {},
        "post_teacher_tls_low_vehicle_control_sumo_load": post_teacher_tls_low_vehicle_control_sumo_load_report or {},
        "post_teacher_tls_low_vehicle_control_reference_delta": post_teacher_tls_low_vehicle_control_reference_delta_report
        or {},
        "post_teacher_tls_low_vehicle_control_reference_promotion": post_teacher_tls_low_vehicle_control_reference_promotion_report,
        "post_teacher_tls_signal_grouping": post_teacher_tls_signal_grouping_report or {},
        "post_teacher_tls_signal_grouping_sumo_load": post_teacher_tls_signal_grouping_sumo_load_report or {},
        "post_teacher_tls_signal_grouping_reference_delta": post_teacher_tls_signal_grouping_reference_delta_report
        or {},
        "post_teacher_tls_signal_grouping_reference_promotion": post_teacher_tls_signal_grouping_reference_promotion_report,
        "post_teacher_tls_non_controller_junction_demotion": (
            post_teacher_tls_non_controller_junction_demotion_report or {}
        ),
        "post_teacher_tls_non_controller_junction_demotion_sumo_load": (
            post_teacher_tls_non_controller_junction_demotion_sumo_load_report or {}
        ),
        "post_teacher_tls_non_controller_junction_demotion_reference_delta": (
            post_teacher_tls_non_controller_junction_demotion_reference_delta_report or {}
        ),
        "post_teacher_tls_non_controller_junction_demotion_reference_promotion": (
            post_teacher_tls_non_controller_junction_demotion_reference_promotion_report
        ),
        "post_teacher_tls_connection_repair": post_teacher_tls_connection_repair_report or {},
        "post_teacher_tls_connection_repair_sumo_load": post_teacher_tls_connection_repair_sumo_load_report or {},
        "post_teacher_tls_connection_repair_reference_delta": post_teacher_tls_connection_repair_reference_delta_report
        or {},
        "post_teacher_tls_connection_repair_reference_promotion": post_teacher_tls_connection_repair_reference_promotion_report,
        "post_teacher_tls_connection_repair_movement_rebuild_queue": (
            post_teacher_tls_connection_repair_movement_rebuild_queue_report or {}
        ),
        "post_teacher_tls_connection_repair_movement_rebuild_plain_export": (
            post_teacher_tls_connection_repair_movement_rebuild_plain_export_report or {}
        ),
        "post_teacher_tls_connection_repair_movement_rebuild_run": (
            post_teacher_tls_connection_repair_movement_rebuild_run_report or {}
        ),
        "final_movement_rebuild_queue": final_movement_rebuild_queue_report or {},
        "final_movement_rebuild_plain_export": final_movement_rebuild_plain_export_report or {},
        "final_movement_rebuild_run": final_movement_rebuild_run_report or {},
        "final_movement_rebuild_sumo_load": final_movement_rebuild_sumo_load_report or {},
        "final_movement_rebuild_reference_delta": final_movement_rebuild_reference_delta_report or {},
        "final_movement_rebuild_reference_promotion": final_movement_rebuild_reference_promotion_report,
        "final_movement_direct_replay": final_movement_direct_replay_report or {},
        "final_movement_direct_replay_reference_delta": final_movement_direct_replay_reference_delta_report or {},
        "final_movement_direct_replay_reference_promotion": final_movement_direct_replay_reference_promotion_report,
        "final_movement_rebuild_internal_regression_restore": (
            final_movement_rebuild_internal_regression_restore_report or {}
        ),
        "final_movement_rebuild_internal_regression_restore_sumo_load": (
            final_movement_rebuild_internal_regression_restore_sumo_load_report or {}
        ),
        "final_movement_rebuild_internal_regression_restore_reference_delta": (
            final_movement_rebuild_internal_regression_restore_reference_delta_report or {}
        ),
        "final_movement_rebuild_internal_regression_restore_promotion": (
            final_movement_rebuild_internal_regression_restore_promotion_report or {}
        ),
        "reference_visual_detail_tls_connection_repair_sumo_load": reference_visual_detail_tls_connection_repair_sumo_load_report
        or {},
        "reference_visual_detail_tls_connection_repair_reference_delta": reference_visual_detail_tls_connection_repair_reference_delta_report
        or {},
        "reference_visual_detail_tls_connection_repair_promotion": reference_visual_detail_tls_connection_repair_promotion_report,
        "raw_connectivity": raw_connectivity_report,
        "connected_core": connected_core_report or {},
        "connected_core_connectivity": connected_core_connectivity_report or {},
        "connectivity": connectivity_report,
        "topology_audit": topology_audit_report or {},
        "reference_topology_audit": reference_topology_audit_report or {},
        "junction_aggregation": junction_aggregation_report or {},
        "reference_hierarchy_audit": reference_hierarchy_audit_report or {},
        "reference_hierarchy_type_repair": reference_hierarchy_type_repair_report or {},
        "reference_hierarchy_type_repair_sumo_load": reference_hierarchy_type_repair_sumo_load_report or {},
        "reference_hierarchy_type_repair_audit": reference_hierarchy_type_repair_audit_report or {},
        "reference_hierarchy_type_repair_promotion": reference_hierarchy_type_repair_promotion_report,
        "reference_bbox_scope": reference_bbox_scope_report or {},
        "corridor_edit_ledger": corridor_edit_ledger_report or {},
        "corridor_geometry_simplification": corridor_geometry_simplification_report or {},
        "corridor_geometry_simplification_sumo_load": corridor_geometry_simplification_sumo_load_report or {},
        "corridor_geometry_simplification_reference_delta": corridor_geometry_simplification_reference_delta_report or {},
        "corridor_geometry_simplification_topology": corridor_geometry_simplification_topology_report or {},
        "corridor_geometry_simplification_promotion": corridor_geometry_simplification_promotion_report,
        "reference_scope_audit": reference_scope_audit_report or {},
        "reference_scope_pruning": reference_scope_pruning_report or {},
        "reference_join_audit": reference_join_audit_report or {},
        "tls_gap_destination_mapping": tls_gap_destination_mapping_report or {},
        "tls_repair_variant": tls_repair_variant_report or {},
        "tls_repair_variant_sumo_load": tls_repair_variant_sumo_load_report or {},
        "tls_repair_variant_semantic": tls_repair_variant_semantic_report or {},
        "tls_repair_variant_reference_audit": tls_repair_variant_reference_audit_report or {},
        "tls_repair_decision": tls_repair_decision_report or {},
        "reference_join_post_teacher_audit": reference_join_post_teacher_audit_report or {},
        "reference_join_aggregation": reference_join_aggregation_report or {},
        "teacher_guided_repair_queue": teacher_guided_repair_queue_report or {},
        "teacher_guided_scoped_tls_cell_batch": teacher_guided_scoped_tls_cell_batch_report or {},
        "teacher_guided_repair_plain_export": teacher_guided_plain_export_report or {},
        "teacher_guided_repair_run": teacher_guided_repair_run_report or {},
        "teacher_guided_probe_matrix": teacher_guided_probe_matrix_report or {},
        "road_connectivity_replay": road_connectivity_replay_report or {},
        "road_connectivity_parity_audit": road_connectivity_parity_audit_report or {},
        "road_connectivity_split_root_alias_repair": road_connectivity_split_root_alias_repair_report or {},
        "road_connection_topology_replay": road_connection_topology_replay_report or {},
        "road_connectivity_seed_probe": road_connectivity_seed_probe_report or {},
        "teacher_guided_direct_replay": teacher_guided_direct_replay_report or {},
        "teacher_guided_direct_replay_reference_delta": teacher_guided_direct_replay_reference_delta_report or {},
        "teacher_guided_direct_replay_reference_promotion": teacher_guided_direct_replay_reference_promotion_report,
        "routeability_audit": routeability_audit_report or {},
        "connection_mode_audit": connection_mode_audit_report or {},
        "standard_nema_scan": standard_nema_scan_report or {},
        "netedit": netedit_report,
        "reference_visual_detail_netedit": reference_visual_detail_netedit_report,
        "sumo_gui": sumo_gui_report,
        "gate_status": gate_status,
        "warnings": warnings,
    }
    report["review_decisions_source_file"] = str(review_decisions_file) if review_decisions_file else ""
    report["review_decisions_source_status"] = review_decisions_source_status
    report["review_decisions_source_error"] = review_decisions_source_error
    if supplied_review_decisions is not None:
        report["review_decisions"] = supplied_review_decisions
    workflow_review_html_report = build_workflow_review_html(
        output_dir=output_dir / "review",
        prefix=f"{prefix}_workflow_review",
        title="SUMO Network Review",
        claim_status=str(report["claim_status"]),
        summary=report,
        net_file=reference_visual_detail_comparison_net_file or report.get("net_file"),
        raw_net_file=report.get("raw_net_file"),
        connected_core_file=report.get("connected_core_file"),
        reference_net_file=report.get("reference_validation_net_file") or report.get("reference_net_file"),
        tls_review_file=report.get("tls_review_file"),
        topology_audit_report=topology_audit_report,
        topology_audit_report_file=report.get("topology_audit_clusters_file"),
        junction_aggregation_report=junction_aggregation_report,
        junction_aggregation_report_file=report.get("junction_aggregation_plan_file"),
        routeability_audit_report=routeability_audit_report,
        routeability_audit_report_file=report.get("routeability_audit_report_file"),
        gate_status=gate_status,
        warnings=warnings,
    )
    netedit_review_sumocfg = workflow_review_html_report.get("netedit_review_sumocfg_file", "")
    netedit_review_selection_files = list(workflow_review_html_report.get("netedit_review_selection_files", []) or [])
    netedit_review_viewsettings_files = list(workflow_review_html_report.get("netedit_review_viewsettings_files", []) or [])
    should_launch_netedit_review = (
        launch_netedit_after_build
        if launch_netedit_review_after_build is None
        else launch_netedit_review_after_build
    )
    review_launcher = launch_netedit
    if should_launch_netedit_review and netedit_review_sumocfg:
        review_launch_kwargs: dict[str, Any] = {}
        if netedit_review_selection_files and _supports_keyword(review_launcher, "selection_file"):
            review_launch_kwargs["selection_file"] = Path(str(netedit_review_selection_files[0]))
        if netedit_review_viewsettings_files and _supports_keyword(review_launcher, "gui_settings_file"):
            review_launch_kwargs["gui_settings_file"] = Path(str(netedit_review_viewsettings_files[0]))
        netedit_review_launch_report = review_launcher(Path(str(netedit_review_sumocfg)), **review_launch_kwargs)
    elif not netedit_review_sumocfg:
        netedit_review_launch_report = {
            "status": "blocked",
            "claim_status": "diagnostic-demo",
            "netedit_status": "skipped",
            "netedit_input_file": "",
            "netedit_open_mode": "sumocfg",
            "warnings": ["netedit review config was not generated"],
        }
    else:
        netedit_review_launch_report = {
            "status": "blocked",
            "claim_status": "diagnostic-demo",
            "netedit_status": "skipped",
            "netedit_input_file": str(netedit_review_sumocfg),
            "netedit_open_mode": "sumocfg",
            "warnings": ["netedit review launch disabled by caller"],
        }
    report.update(
        {
            "workflow_review_html_status": workflow_review_html_report.get("workflow_review_html_status", "fail"),
            "workflow_review_html_file": workflow_review_html_report.get("workflow_review_html_file", ""),
            "workflow_review_net_file": workflow_review_html_report.get("workflow_review_net_file", ""),
            "workflow_report_file": workflow_review_html_report.get("workflow_report_file", ""),
            "review_manifest_file": workflow_review_html_report.get("review_manifest_file", ""),
            "artifact_hash_gate": workflow_review_html_report.get("artifact_hash_gate", {}),
            "artifact_hash_gate_status": workflow_review_html_report.get("artifact_hash_gate_status", "fail"),
            "artifact_hashes": workflow_review_html_report.get("artifact_hashes", {}),
            "network_overview_png": workflow_review_html_report.get("network_overview_png", ""),
            "problem_overlay_png": workflow_review_html_report.get("problem_overlay_png", ""),
            "reference_comparison_png": workflow_review_html_report.get("reference_comparison_png", ""),
            "cluster_zoom_pngs": workflow_review_html_report.get("cluster_zoom_pngs", []),
            "netedit_review_additional_file": workflow_review_html_report.get("netedit_review_additional_file", ""),
            "netedit_review_sumocfg_file": workflow_review_html_report.get("netedit_review_sumocfg_file", ""),
            "netedit_review_command": workflow_review_html_report.get("netedit_review_command", ""),
            "netedit_review_selection_files": netedit_review_selection_files,
            "netedit_review_viewsettings_files": netedit_review_viewsettings_files,
            "claim_tiers": workflow_review_html_report.get("claim_tiers", {}),
            "review_decisions_file": workflow_review_html_report.get("review_decisions_file", ""),
            "review_decisions_status": workflow_review_html_report.get("review_decisions_status", "pending"),
            "review_overlay_location_count": workflow_review_html_report.get("review_overlay_location_count", 0),
            "review_overlay_category_counts": workflow_review_html_report.get(
                "review_overlay_category_counts", {}
            ),
            "netedit_review_launch_status": netedit_review_launch_report.get("netedit_status", "not_started"),
            "netedit_review_launch_process_id": netedit_review_launch_report.get("netedit_process_id"),
            "netedit_review_launch_file": netedit_review_launch_report.get("netedit_input_file", ""),
            "netedit_review_launch": netedit_review_launch_report,
            "human_review_required_count": workflow_review_html_report.get("human_review_required_count", 0),
            "workflow_review_html": workflow_review_html_report,
        }
    )
    return report
