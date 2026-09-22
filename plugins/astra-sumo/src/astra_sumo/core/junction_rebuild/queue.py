"""Run teacher-guided reconstruction queues and comparison matrices."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
import xml.etree.ElementTree as ET
from ..command_runner import run_command
from ..junction_teacher_model import extract_teacher_junction_model
from .artifacts import (
    _command_path,
    _command_report,
    _failure,
    _queue_candidate_dir,
    _queue_path,
    _safe_stage_name,
    _stage_file,
    _variant_exception_report,
    _write_teacher_guided_promotion_gate,
)
from .cases import _attach_candidate_template_context, _teacher_pattern_contexts
from .edge_mapping import (
    _augment_candidate_edge_map_from_tls_approach_pairs,
    _edge_map_from_approach_endpoint_rebuild_plan,
    _teacher_boundary_edge_has_target_junction,
    _teacher_boundary_edge_ids_touching_internal_subgraph,
    _teacher_candidate_edge_map,
    _valid_edge_map,
)
from .lane_inputs import write_missing_edge_type_patch
from .network import _edge_file_ids, _edge_is_pedestrian_only, _net_contains_normal_junctions, _plain_node_ids
from .parity import (
    _approach_integrity_failure_counts,
    _approach_integrity_status,
    _final_composite_parity_gate,
    _final_context_parity_gate,
    _road_continuity_probe_summary,
    _semantic_failure_counts,
    _semantic_layer_gate_counts,
)
from .planning import (
    _accepted_target_internal_replay_entry,
    _candidate_requests_target_internal_replay,
    _expanded_scope_followup_candidate_for_unsafe_internal_replay,
    _expanded_scope_skip_entry,
    _teacher_guided_repair_candidate,
)
from .restoration import _restore_non_target_internal_artifacts, _restore_replayed_geometry_attrs
from .scope import (
    _blocking_sequential_overlap_edge_ids,
    _candidate_connection_mode_scope_ids,
    _expand_fragmented_tls_join_scope_candidate,
    _join_internal_self_loop_drop_has_witness,
    _join_patch_joined_node_ids,
    _joined_endpoint_self_loop_edge_ids,
    _load_teacher_join_groups_by_cluster,
)
from .scope_inputs import (
    _prune_plain_node_controlled_inner_edges,
    _write_join_scope_connection_file,
    _write_join_scope_tllogic_file,
    _write_joined_endpoint_connection_file,
    _write_joined_endpoint_edge_file,
    _write_replay_node_file,
    write_expanded_scope_plain_inputs,
)
from .target_replay import write_teacher_target_internal_replay_net
from .tls import (
    _demote_teacher_absent_context_tls,
    _prune_plain_tls_against_teacher,
    _remove_teacher_non_tls_tllogics,
    _restore_false_traffic_light_junction_types,
)
from .variant import build_teacher_guided_junction_variant


def run_teacher_guided_repair_queue(
    *,
    queue_report: dict[str, Any],
    raw_node_file: Path,
    raw_edge_file: Path,
    raw_connection_file: Path,
    output_dir: Path,
    prefix: str = "teacher_guided_repair",
    queue_base_dir: Path | None = None,
    raw_type_file: Path | None = None,
    raw_tllogic_file: Path | None = None,
    teacher_join_definition_file: Path | None = None,
    teacher_join_groups_by_cluster: dict[str, list[str]] | None = None,
    crossing_edge_overrides_by_junction: dict[str, dict[str, str | list[str]]] | None = None,
    replay_target_internal_subgraph: bool = False,
    max_ready_candidates: int | None = None,
    netconvert_binary: str = "netconvert",
    sumo_binary: str = "sumo",
    timeout_seconds: float = 240.0,
    command_runner: Any = run_command,
    variant_builder: Any = build_teacher_guided_junction_variant,
    connection_mode_regression_builder: Any | None = None,
    expand_fragmented_tls_join_scope: bool = False,
    sequential_accept_passed_variants: bool = False,
    plain_exporter: Any | None = None,
    final_internal_replay_writer: Any = write_teacher_target_internal_replay_net,
) -> dict[str, object]:
    teacher_net_value = queue_report.get("teacher_net_file")
    candidate_net_value = queue_report.get("candidate_net_file")
    missing_fields = [
        field
        for field, value in (
            ("teacher_net_file", teacher_net_value),
            ("candidate_net_file", candidate_net_value),
        )
        if not value
    ]
    if missing_fields:
        return _failure(f"queue report missing field(s): {', '.join(missing_fields)}")

    teacher_net_file = _queue_path(teacher_net_value, queue_base_dir)
    candidate_net_file = _queue_path(candidate_net_value, queue_base_dir)
    missing = [
        str(path)
        for path in (raw_node_file, raw_edge_file, raw_connection_file, teacher_net_file, candidate_net_file)
        if not path.exists()
    ]
    if raw_type_file is not None and not raw_type_file.exists():
        missing.append(str(raw_type_file))
    if raw_tllogic_file is not None and not raw_tllogic_file.exists():
        missing.append(str(raw_tllogic_file))
    if missing:
        return _failure(f"missing input file(s): {', '.join(missing)}")

    teacher_net_file = teacher_net_file.resolve()
    candidate_net_file = candidate_net_file.resolve()
    raw_node_file = raw_node_file.resolve()
    raw_edge_file = raw_edge_file.resolve()
    raw_connection_file = raw_connection_file.resolve()
    raw_type_file = raw_type_file.resolve() if raw_type_file is not None else None
    raw_tllogic_file = raw_tllogic_file.resolve() if raw_tllogic_file is not None else None
    output_dir = output_dir.resolve()

    candidates = queue_report.get("repair_candidates", []) or []
    if not isinstance(candidates, list):
        return _failure("queue report repair_candidates must be a list")
    candidates = list(candidates)

    if teacher_join_groups_by_cluster is None:
        teacher_join_definition_value = teacher_join_definition_file or queue_report.get(
            "teacher_join_definition_file", ""
        )
        teacher_join_groups_by_cluster = _load_teacher_join_groups_by_cluster(
            teacher_join_definition_value,
            queue_base_dir=queue_base_dir,
        )
    else:
        teacher_join_groups_by_cluster = {
            str(cluster_id): sorted(
                {str(node_id) for node_id in node_ids if str(node_id)}
            )
            for cluster_id, node_ids in teacher_join_groups_by_cluster.items()
            if str(cluster_id) and isinstance(node_ids, (list, tuple, set))
        }

    output_dir.mkdir(parents=True, exist_ok=True)
    crossing_edge_overrides_by_junction = crossing_edge_overrides_by_junction or {}
    variant_reports = []
    expanded_scope_reports = []
    expanded_scope_followup_candidates = []
    skipped_candidates = []
    sequential_plain_export_reports = []
    current_raw_node_file = raw_node_file
    current_raw_edge_file = raw_edge_file
    current_raw_connection_file = raw_connection_file
    current_raw_type_file = raw_type_file
    current_raw_tllogic_file = raw_tllogic_file
    current_candidate_net_file = candidate_net_file
    composite_applied_candidate_count = 0
    composite_net_file = ""
    applied_candidate_edge_ids: set[str] = set()
    applied_candidate_node_ids: set[str] = set()
    sequential_blocked_reason = ""
    attempted_ready_count = 0
    accepted_internal_replays: list[dict[str, object]] = []
    refresh_teacher_root: ET.Element | None = None
    refresh_candidate_root: ET.Element | None = None
    refresh_candidate_net_file: Path | None = None
    for index, candidate in enumerate(candidates):
        if not isinstance(candidate, dict):
            skipped_candidates.append({"index": index, "candidate_status": "invalid_candidate"})
            continue
        if (
            sequential_accept_passed_variants
            and composite_applied_candidate_count > 0
            and current_candidate_net_file != candidate_net_file
            and current_candidate_net_file.exists()
        ):
            try:
                if refresh_teacher_root is None:
                    refresh_teacher_root = ET.parse(teacher_net_file).getroot()
                if refresh_candidate_root is None or refresh_candidate_net_file != current_candidate_net_file:
                    refresh_candidate_root = ET.parse(current_candidate_net_file).getroot()
                    refresh_candidate_net_file = current_candidate_net_file
                refresh_teacher_edges = {
                    edge.attrib["id"]: edge
                    for edge in refresh_teacher_root.findall("edge")
                    if edge.attrib.get("id")
                }
                refresh_candidate_edges = {
                    edge.attrib["id"]: edge
                    for edge in refresh_candidate_root.findall("edge")
                    if edge.attrib.get("id")
                }
                refreshed_candidate = _teacher_guided_repair_candidate(
                    case=candidate,
                    teacher_net_file=teacher_net_file,
                    candidate_net_file=current_candidate_net_file,
                    teacher_root=refresh_teacher_root,
                    candidate_root=refresh_candidate_root,
                    teacher_edges=refresh_teacher_edges,
                    candidate_edges_by_id=refresh_candidate_edges,
                    candidate_edge_ids=set(refresh_candidate_edges),
                )
                if refreshed_candidate.get("candidate_status") in {
                    "ready_for_teacher_guided_variant",
                    "needs_expanded_rebuild_scope",
                }:
                    original_expanded_scope = candidate.get("expanded_rebuild_scope", {})
                    original_scope_ids = {
                        str(item)
                        for item in (
                            original_expanded_scope.get("junction_ids", [])
                            if isinstance(original_expanded_scope, dict)
                            else []
                        )
                        if str(item)
                    }
                    original_cluster_scope_ids = {
                        item for item in original_scope_ids if item.startswith("cluster_")
                    }
                    refreshed_expanded_scope = refreshed_candidate.get("expanded_rebuild_scope", {})
                    # A sequential refresh can make the core junction look
                    # individually repairable after an earlier candidate was
                    # applied.  Preserve the teacher's explicit split-cluster
                    # context in that case; otherwise the next replay silently
                    # downgrades a context repair into a single-node repair and
                    # the final composite regresses to the split cluster.
                    if original_cluster_scope_ids:
                        merged_scope = dict(
                            refreshed_expanded_scope
                            if isinstance(refreshed_expanded_scope, dict)
                            else {}
                        )
                        merged_scope.update(
                            {
                                key: value
                                for key, value in original_expanded_scope.items()
                                if key in {
                                    "core_junction_id",
                                    "blocked_teacher_edge_ids",
                                    "missing_desired_endpoint_ids",
                                    "reason",
                                }
                            }
                        )
                        merged_scope["junction_ids"] = sorted(
                            original_scope_ids
                            | {
                                str(item)
                                for item in merged_scope.get("junction_ids", []) or []
                                if str(item)
                            }
                        )
                        original_join_ids = {
                            str(item)
                            for item in original_expanded_scope.get("join_junction_ids", []) or []
                            if str(item)
                        }
                        refreshed_join_ids = {
                            str(item)
                            for item in merged_scope.get("join_junction_ids", []) or []
                            if str(item)
                        }
                        merged_scope["join_junction_ids"] = sorted(original_join_ids | refreshed_join_ids)
                        merged_scope["status"] = "review"
                        merged_scope["recommended_action"] = "rebuild_plain_xml_scope"
                        merged_scope["sequential_cluster_context_preserved"] = True
                        merged_scope["sequential_cluster_context_ids"] = sorted(original_cluster_scope_ids)
                        refreshed_candidate = {
                            **refreshed_candidate,
                            "candidate_status": "needs_expanded_rebuild_scope",
                            "expanded_rebuild_scope": merged_scope,
                            "sequential_cluster_context_preserved": True,
                        }
                    candidate = {
                        **candidate,
                        **refreshed_candidate,
                        "sequential_refreshed_candidate": True,
                        "sequential_refresh_source_net_file": str(current_candidate_net_file),
                    }
                else:
                    candidate = {
                        **candidate,
                        "sequential_refresh_status": "skipped_unusable",
                        "sequential_refresh_candidate_status": str(refreshed_candidate.get("candidate_status", "")),
                    }
            except (ET.ParseError, OSError, KeyError, TypeError, ValueError) as exc:
                candidate = {
                    **candidate,
                    "sequential_refresh_status": "fail",
                    "sequential_refresh_error": f"{type(exc).__name__}: {exc}",
                }
        is_followup_candidate = bool(candidate.get("followup_reason"))
        if (
            expand_fragmented_tls_join_scope
            and candidate.get("candidate_status") == "needs_expanded_rebuild_scope"
        ):
            candidate = _expand_fragmented_tls_join_scope_candidate(
                candidate,
                current_raw_node_file,
            )
            tls_scope_expansion = candidate.get("tls_join_scope_expansion", {})
            if isinstance(tls_scope_expansion, dict) and tls_scope_expansion.get(
                "automatic_expansion_applied"
            ):
                candidate = _augment_candidate_edge_map_from_tls_approach_pairs(candidate)
        junction_id = str(candidate.get("junction_id") or candidate.get("reference_id") or "")
        teacher_junction_id = str(candidate.get("reference_id") or junction_id)
        edge_map = _valid_edge_map(candidate.get("edge_map", {}))
        candidate_replay_target_internal_subgraph = (
            replay_target_internal_subgraph or _candidate_requests_target_internal_replay(candidate)
        )
        candidate_node_ids = {str(item) for item in candidate.get("matched_candidate_node_ids", []) or [] if str(item)}
        if junction_id:
            candidate_node_ids.add(junction_id)
        candidate_edge_ids = {str(item) for item in edge_map.values() if str(item)}
        overlap_edge_ids = candidate_edge_ids & applied_candidate_edge_ids
        overlap_node_ids = candidate_node_ids & applied_candidate_node_ids
        blocking_overlap_edge_ids = _blocking_sequential_overlap_edge_ids(
            overlap_edge_ids,
            current_raw_edge_file,
            candidate_node_ids,
            applied_candidate_node_ids,
        )
        allowed_boundary_overlap_edge_ids = sorted(overlap_edge_ids - set(blocking_overlap_edge_ids))
        if allowed_boundary_overlap_edge_ids:
            candidate = {
                **candidate,
                "sequential_allowed_boundary_overlap_edge_ids": allowed_boundary_overlap_edge_ids,
            }
        if sequential_accept_passed_variants and sequential_blocked_reason:
            skipped_candidates.append(
                {
                    "index": index,
                    "junction_id": junction_id,
                    "candidate_status": "sequential_plain_export_failed",
                    "reason": sequential_blocked_reason,
                }
            )
            continue
        if (
            sequential_accept_passed_variants
            and is_followup_candidate
            and (blocking_overlap_edge_ids or overlap_node_ids)
        ):
            candidate = {
                **candidate,
                "sequential_followup_overlap_replay": True,
                "sequential_followup_overlap_edge_ids": blocking_overlap_edge_ids,
                "sequential_followup_overlap_node_ids": sorted(overlap_node_ids),
            }
        if sequential_accept_passed_variants and not is_followup_candidate and (blocking_overlap_edge_ids or overlap_node_ids):
            skipped_candidates.append(
                {
                    "index": index,
                    "junction_id": junction_id,
                    "candidate_status": "sequential_candidate_overlap",
                    "overlap_edge_ids": blocking_overlap_edge_ids,
                    "allowed_boundary_overlap_edge_ids": allowed_boundary_overlap_edge_ids,
                    "overlap_node_ids": sorted(overlap_node_ids),
                }
            )
            continue
        if candidate.get("candidate_status") == "needs_expanded_rebuild_scope" and junction_id:
            if (
                max_ready_candidates is not None
                and max_ready_candidates > 0
                and attempted_ready_count >= max_ready_candidates
                and not is_followup_candidate
            ):
                skipped_candidates.append(
                    {"index": index, "junction_id": junction_id, "candidate_status": "max_ready_candidates_reached"}
                )
                continue
            safe_junction_id = _queue_candidate_dir(index, junction_id)
            scope_report = _attach_candidate_template_context(
                write_expanded_scope_plain_inputs(
                    raw_node_file=current_raw_node_file,
                    raw_edge_file=current_raw_edge_file,
                    raw_connection_file=current_raw_connection_file,
                    output_dir=output_dir / safe_junction_id,
                    expanded_rebuild_scope=candidate.get("expanded_rebuild_scope", {}),
                    approach_endpoint_rebuild_plan=candidate.get("approach_endpoint_rebuild_plan", {}),
                    teacher_join_groups_by_cluster=teacher_join_groups_by_cluster,
                    netconvert_binary=netconvert_binary,
                    sumo_binary=sumo_binary,
                    timeout_seconds=timeout_seconds,
                    command_runner=command_runner,
                ),
                candidate,
            )
            expanded_scope_reports.append(scope_report)
            joined_scope_junction_id = str(scope_report.get("joined_scope_junction_id", ""))
            rewrite_joined_endpoints = bool(scope_report.get("auto_cluster_join_count", 0))
            joined_scope_junction_ids = [
                str(item)
                for item in scope_report.get("joined_scope_junction_ids", []) or []
                if str(item)
            ] or ([joined_scope_junction_id] if joined_scope_junction_id else [])
            replay_edge_map = edge_map
            if not replay_edge_map and joined_scope_junction_id:
                try:
                    teacher_model = extract_teacher_junction_model(teacher_net_file, teacher_junction_id)
                    replay_edge_map = _teacher_candidate_edge_map(
                        teacher_model,
                        extract_teacher_junction_model(Path(str(scope_report.get("net_file", ""))), joined_scope_junction_id),
                        teacher_junction_id=teacher_junction_id,
                        candidate_junction_id=joined_scope_junction_id,
                        drop_endpoint_mismatches=False,
                        max_bearing_delta=45.0,
                    )
                    if not replay_edge_map:
                        replay_edge_map = _edge_map_from_approach_endpoint_rebuild_plan(
                            teacher_model,
                            candidate.get("approach_endpoint_rebuild_plan", {}),
                            teacher_junction_id=teacher_junction_id,
                            candidate_junction_id=joined_scope_junction_id,
                            plan_junction_id=junction_id,
                        )
                    scope_report["derived_edge_map"] = replay_edge_map
                except (ET.ParseError, OSError, KeyError, TypeError, ValueError):
                    replay_edge_map = {}
            missing_node_ids = {str(item) for item in scope_report.get("missing_node_ids", []) or [] if str(item)}
            skipped_endpoint_missing_ids = {
                str(node_id)
                for item in scope_report.get("skipped_endpoint_rewrites", []) or []
                if isinstance(item, dict)
                for node_id in item.get("missing_endpoint_ids", []) or []
                if str(node_id)
            }
            use_full_network_replay = (
                not str(scope_report.get("join_nodes_patch_file", ""))
                and (not missing_node_ids or missing_node_ids <= skipped_endpoint_missing_ids)
                and not scope_report.get("blocking_missing_node_ids")
                and not scope_report.get("missing_blocked_edge_ids")
            )
            join_patch_file = Path(str(scope_report.get("join_nodes_patch_file", "")))
            if replay_edge_map:
                missing_blocked_edge_ids = [
                    str(edge_id) for edge_id in scope_report.get("missing_blocked_edge_ids", []) or [] if str(edge_id)
                ]
                resolved_missing_blocked_edge_ids = [
                    edge_id for edge_id in missing_blocked_edge_ids if edge_id in replay_edge_map
                ]
                unresolved_missing_blocked_edge_ids = [
                    edge_id for edge_id in missing_blocked_edge_ids if edge_id not in replay_edge_map
                ]
                copyable_missing_blocked_edge_ids: list[str] = []
                if unresolved_missing_blocked_edge_ids:
                    try:
                        teacher_root = ET.parse(teacher_net_file).getroot()
                        teacher_edges = {
                            edge.attrib["id"]: edge for edge in teacher_root.findall("edge") if edge.attrib.get("id")
                        }
                        teacher_boundary_edge_ids = set(
                            _teacher_boundary_edge_ids_touching_internal_subgraph(
                                teacher_root.findall("connection"),
                                teacher_edges,
                                teacher_junction_id,
                            )
                        )
                        teacher_boundary_edge_ids.update(
                            edge_id
                            for edge_id, edge in teacher_edges.items()
                            if teacher_junction_id in (edge.attrib.get("from"), edge.attrib.get("to"))
                            and not _edge_is_pedestrian_only(edge)
                        )
                        copyable_missing_blocked_edge_ids = [
                            edge_id
                            for edge_id in unresolved_missing_blocked_edge_ids
                            if edge_id in teacher_boundary_edge_ids
                        ]
                    except (ET.ParseError, OSError, KeyError, TypeError, ValueError):
                        copyable_missing_blocked_edge_ids = []
                blocking_missing_blocked_edge_ids = [
                    edge_id
                    for edge_id in unresolved_missing_blocked_edge_ids
                    if edge_id not in set(copyable_missing_blocked_edge_ids)
                ]
                scope_report["resolved_missing_blocked_edge_ids"] = resolved_missing_blocked_edge_ids
                scope_report["unresolved_missing_blocked_edge_ids"] = unresolved_missing_blocked_edge_ids
                scope_report["copyable_missing_blocked_edge_ids"] = copyable_missing_blocked_edge_ids
                scope_report["blocking_missing_blocked_edge_ids"] = blocking_missing_blocked_edge_ids
                if copyable_missing_blocked_edge_ids:
                    replay_edge_map = {
                        **replay_edge_map,
                        **{edge_id: edge_id for edge_id in copyable_missing_blocked_edge_ids},
                    }
                    scope_report["derived_edge_map"] = replay_edge_map
                if (
                    scope_report.get("status") == "review"
                    and missing_blocked_edge_ids
                    and not blocking_missing_blocked_edge_ids
                    and not scope_report.get("blocking_missing_node_ids")
                    and not scope_report.get("blocking_missing_joined_scope_junction_ids")
                ):
                    scope_report["status"] = "pass"
                    scope_report["missing_blocked_edge_resolution"] = (
                        "copyable_by_teacher_replay"
                        if copyable_missing_blocked_edge_ids
                        else "mapped_by_replay_edge_map"
                    )
            join_self_loop_edge_ids, join_blocking_self_loop_edge_ids = _joined_endpoint_self_loop_edge_ids(
                current_raw_edge_file,
                join_patch_file,
                joined_scope_junction_ids,
            )
            expanded_rebuild_scope = candidate.get("expanded_rebuild_scope", {})
            blocked_teacher_edge_ids = (
                expanded_rebuild_scope.get("blocked_teacher_edge_ids", [])
                if isinstance(expanded_rebuild_scope, dict)
                else []
            )
            protected_self_loop_edge_ids = {
                *{str(edge_id) for edge_id in replay_edge_map.values() if str(edge_id)},
                *{
                    str(edge_id)
                    for edge_id in blocked_teacher_edge_ids
                    if str(edge_id)
                },
            }
            if join_self_loop_edge_ids:
                scope_report["full_network_join_self_loop_edge_drop_candidates"] = join_self_loop_edge_ids
            if join_blocking_self_loop_edge_ids:
                scope_report["full_network_join_blocking_self_loop_edge_drop_candidates"] = (
                    join_blocking_self_loop_edge_ids
                )
            full_network_join_edge_file = current_raw_edge_file
            full_network_join_dropped_self_loop_edges: list[str] = []
            full_network_join_absorbed_self_loop_edge_ids: list[str] = []
            full_network_join_blocking_self_loop_edge_ids = list(join_blocking_self_loop_edge_ids)
            if join_patch_file.is_file() and join_self_loop_edge_ids:
                (
                    candidate_full_network_join_edge_file,
                    _full_network_join_edge_endpoint_rewrite_count,
                    candidate_full_network_join_dropped_self_loop_edges,
                    candidate_full_network_join_blocking_self_loop_edge_ids,
                ) = _write_joined_endpoint_edge_file(
                    current_raw_edge_file,
                    join_patch_file,
                    joined_scope_junction_ids,
                    output_dir / safe_junction_id / "full_network_join_replay.edg.xml",
                )
                surviving_edge_ids = _edge_file_ids(candidate_full_network_join_edge_file)
                absorbable_self_loop_edge_ids = [
                    edge_id
                    for edge_id in candidate_full_network_join_blocking_self_loop_edge_ids
                    if (
                        edge_id not in protected_self_loop_edge_ids
                        or _join_internal_self_loop_drop_has_witness(
                            edge_id,
                            candidate_full_network_join_dropped_self_loop_edges,
                            surviving_edge_ids,
                        )
                        or _teacher_boundary_edge_has_target_junction(
                            teacher_net_file,
                            teacher_junction_id,
                            edge_id,
                        )
                    )
                ]
                absorbable_self_loop_edge_id_set = set(absorbable_self_loop_edge_ids)
                full_network_join_blocking_self_loop_edge_ids = [
                    edge_id
                    for edge_id in candidate_full_network_join_blocking_self_loop_edge_ids
                    if edge_id not in absorbable_self_loop_edge_id_set
                ]
                if not full_network_join_blocking_self_loop_edge_ids:
                    full_network_join_edge_file = candidate_full_network_join_edge_file
                    full_network_join_dropped_self_loop_edges = candidate_full_network_join_dropped_self_loop_edges
                    full_network_join_absorbed_self_loop_edge_ids = list(
                        dict.fromkeys(candidate_full_network_join_dropped_self_loop_edges)
                    )
            use_full_network_join_patch_replay = (
                join_patch_file.is_file()
                and (
                    not scope_report.get("rewritten_endpoint_count")
                    or rewrite_joined_endpoints
                )
                and not full_network_join_blocking_self_loop_edge_ids
                and not scope_report.get("blocking_missing_node_ids")
                and not scope_report.get("blocking_missing_blocked_edge_ids")
                and not scope_report.get("blocking_missing_joined_scope_junction_ids")
            )
            join_patch_joined_node_ids = _join_patch_joined_node_ids(join_patch_file)
            can_replace_stale_joined_node = (
                joined_scope_junction_id == junction_id
                and joined_scope_junction_id in join_patch_joined_node_ids
            )
            if (
                use_full_network_join_patch_replay
                and joined_scope_junction_id in _plain_node_ids(current_raw_node_file)
                and joined_scope_junction_id in join_patch_joined_node_ids
                and not can_replace_stale_joined_node
            ):
                skipped_candidates.append(
                    {
                        "index": index,
                        "junction_id": junction_id,
                        "candidate_status": "sequential_candidate_overlap",
                        "overlap_edge_ids": [],
                        "overlap_node_ids": [joined_scope_junction_id],
                    }
                )
                continue
            replaced_stale_joined_node_ids = sorted(
                join_patch_joined_node_ids & _plain_node_ids(current_raw_node_file)
            )
            if can_replace_stale_joined_node and replaced_stale_joined_node_ids:
                scope_report["full_network_join_replaced_stale_joined_node_ids"] = replaced_stale_joined_node_ids
            if (
                scope_report.get("status") == "pass"
                and (scope_report.get("netconvert") or {}).get("status") == "pass"
                and (scope_report.get("sumo_load") or {}).get("status") == "pass"
                and joined_scope_junction_id
                and replay_edge_map
                and not (
                    max_ready_candidates is not None
                    and max_ready_candidates > 0
                    and attempted_ready_count >= max_ready_candidates
                    and not is_followup_candidate
                )
            ):
                variant_prefix = f"{_safe_stage_name(prefix, max_len=12)}_{index + 1:03d}"
                replay_tllogic_file = current_raw_tllogic_file
                if use_full_network_replay:
                    replay_node_file = current_raw_node_file
                    replay_edge_file = current_raw_edge_file
                    replay_connection_file = current_raw_connection_file
                    replay_candidate_net_file = current_candidate_net_file
                    replay_blocking_self_loop_edge_drops = []
                    replay_dropped_self_loop_edges = []
                    preabsorbed_join_internal_edge_ids = []
                    replay_edge_endpoint_rewrite_count = 0
                    scope_report["replay_scope"] = "full_network"
                elif use_full_network_join_patch_replay:
                    replay_node_file = _write_replay_node_file(
                        current_raw_node_file,
                        join_patch_file,
                        output_dir / safe_junction_id / "full_network_join_replay.nod.xml",
                    )
                    controlled_inner_prune = _prune_plain_node_controlled_inner_edges(
                        replay_node_file,
                        set(full_network_join_dropped_self_loop_edges),
                    )
                    scope_report["full_network_join_controlled_inner_prune"] = (
                        controlled_inner_prune
                    )
                    replay_edge_file = full_network_join_edge_file
                    replay_connection_file, dead_end_drop_count, dead_end_drop_edge_ids = _write_join_scope_connection_file(
                        replay_edge_file,
                        current_raw_connection_file,
                        {str(node_id) for node_id in scope_report.get("join_node_ids", []) or [] if str(node_id)},
                        output_dir / safe_junction_id / "full_network_join_replay.con.xml",
                        drop_edge_ids=set(full_network_join_dropped_self_loop_edges),
                    )
                    rewritten_replay_edge_file, rewritten_endpoint_count, rewritten_self_loop_edges, rewritten_blocking_self_loops = (
                        _write_joined_endpoint_edge_file(
                            replay_edge_file,
                            join_patch_file,
                            joined_scope_junction_ids,
                            output_dir / safe_junction_id / "full_network_join_replay_rewritten.edg.xml",
                            rewrite_endpoints=rewrite_joined_endpoints,
                        )
                    )
                    if rewrite_joined_endpoints:
                        rewritten_replay_connection_file, crossing_rewrite_count = _write_joined_endpoint_connection_file(
                            replay_connection_file,
                            join_patch_file,
                            output_dir / safe_junction_id / "full_network_join_replay_rewritten.con.xml",
                        )
                    else:
                        rewritten_replay_connection_file, crossing_rewrite_count = replay_connection_file, 0
                    if rewritten_replay_edge_file != replay_edge_file:
                        replay_edge_file = rewritten_replay_edge_file
                    if rewritten_replay_connection_file != replay_connection_file:
                        replay_connection_file = rewritten_replay_connection_file
                    scope_report["full_network_join_endpoint_rewrite_count"] = rewritten_endpoint_count
                    scope_report["full_network_join_crossing_node_rewrite_count"] = crossing_rewrite_count
                    scope_report["full_network_join_rewritten_self_loop_edges"] = rewritten_self_loop_edges
                    scope_report["full_network_join_rewritten_blocking_self_loop_edges"] = rewritten_blocking_self_loops
                    scope_report["full_network_join_dead_end_connection_drop_count"] = dead_end_drop_count
                    scope_report["full_network_join_dead_end_connection_drop_edge_ids"] = dead_end_drop_edge_ids
                    if current_raw_tllogic_file is not None:
                        (
                            replay_tllogic_file,
                            tllogic_drop_count,
                            tllogic_drop_edge_ids,
                        ) = _write_join_scope_tllogic_file(
                            current_raw_tllogic_file,
                            set(full_network_join_dropped_self_loop_edges),
                            output_dir / safe_junction_id / "full_network_join_replay.tll.xml",
                        )
                        scope_report["full_network_join_tllogic_connection_drop_count"] = (
                            tllogic_drop_count
                        )
                        scope_report["full_network_join_tllogic_connection_drop_edge_ids"] = (
                            tllogic_drop_edge_ids
                        )
                    replay_edge_endpoint_rewrite_count = 0
                    replay_dropped_self_loop_edges = full_network_join_dropped_self_loop_edges
                    replay_blocking_self_loop_edge_drops = full_network_join_blocking_self_loop_edge_ids
                    preabsorbed_join_internal_edge_ids = full_network_join_absorbed_self_loop_edge_ids
                    replay_candidate_net_file = output_dir / safe_junction_id / "full_network_join_replay.net.xml"
                    replay_type_file = current_raw_type_file
                    if current_raw_type_file is not None:
                        replay_type_report = write_missing_edge_type_patch(
                            raw_type_file=current_raw_type_file,
                            edge_file=replay_edge_file,
                            output_file=output_dir
                            / safe_junction_id
                            / "full_network_join_replay.typ.xml",
                        )
                        scope_report["full_network_join_type_patch"] = replay_type_report
                        if replay_type_report.get("status") != "pass":
                            scope_report["status"] = "review"
                            skipped_candidates.append(
                                {
                                    "index": index,
                                    "junction_id": junction_id,
                                    "candidate_status": "full_network_join_type_patch_failed",
                                }
                            )
                            continue
                        replay_type_value = str(replay_type_report.get("type_file", ""))
                        replay_type_file = Path(replay_type_value) if replay_type_value else None
                    seed_command = [
                        netconvert_binary,
                        "--node-files",
                        _command_path(replay_node_file, output_dir / safe_junction_id),
                        "--edge-files",
                        _command_path(replay_edge_file, output_dir / safe_junction_id),
                        "--connection-files",
                        _command_path(replay_connection_file, output_dir / safe_junction_id),
                        "--output-file",
                        replay_candidate_net_file.name,
                        "--walkingareas",
                        "true",
                        "--tls.ignore-internal-junction-jam",
                    ]
                    if replay_type_file is not None:
                        seed_command[5:5] = [
                            "--type-files",
                            _command_path(replay_type_file, output_dir / safe_junction_id),
                        ]
                    if replay_tllogic_file is not None:
                        seed_command[5:5] = [
                            "--tllogic-files",
                            _command_path(replay_tllogic_file, output_dir / safe_junction_id),
                        ]
                    seed_report = _command_report(
                        command_runner(seed_command, cwd=output_dir / safe_junction_id, timeout_seconds=timeout_seconds)
                    )
                    scope_report["full_network_join_seed_netconvert"] = seed_report
                    if seed_report.get("status") != "pass":
                        scope_report["status"] = "review"
                        skipped_candidates.append(
                            {
                                "index": index,
                                "junction_id": junction_id,
                                "candidate_status": "full_network_join_seed_failed",
                            }
                        )
                        continue
                    try:
                        refreshed_edge_map = _teacher_candidate_edge_map(
                            extract_teacher_junction_model(teacher_net_file, teacher_junction_id),
                            extract_teacher_junction_model(replay_candidate_net_file, joined_scope_junction_id),
                            teacher_junction_id=teacher_junction_id,
                            candidate_junction_id=joined_scope_junction_id,
                            drop_endpoint_mismatches=False,
                            max_bearing_delta=45.0,
                        )
                    except (ET.ParseError, OSError, KeyError, TypeError, ValueError) as exc:
                        refreshed_edge_map = {}
                        scope_report["full_network_join_refreshed_edge_map_error"] = f"{type(exc).__name__}: {exc}"
                    if refreshed_edge_map:
                        replacements = {
                            teacher_edge_id: {
                                "old": replay_edge_map.get(teacher_edge_id, ""),
                                "new": candidate_edge_id,
                            }
                            for teacher_edge_id, candidate_edge_id in refreshed_edge_map.items()
                            if replay_edge_map.get(teacher_edge_id) != candidate_edge_id
                        }
                        replay_edge_map = dict(sorted({**replay_edge_map, **refreshed_edge_map}.items()))
                        scope_report["full_network_join_refreshed_edge_map"] = refreshed_edge_map
                        scope_report["full_network_join_refreshed_edge_map_replacements"] = replacements
                    scope_report["replay_scope"] = "full_network_join_patch"
                else:
                    replay_node_file = _write_replay_node_file(
                        Path(str(scope_report.get("node_file", ""))),
                        join_patch_file,
                        output_dir / safe_junction_id / "expanded_scope_replay.nod.xml",
                    )
                    (
                        replay_edge_file,
                        replay_edge_endpoint_rewrite_count,
                        replay_dropped_self_loop_edges,
                        replay_blocking_self_loop_edge_drops,
                    ) = _write_joined_endpoint_edge_file(
                        Path(str(scope_report.get("edge_file", ""))),
                        join_patch_file,
                        joined_scope_junction_ids,
                        output_dir / safe_junction_id / "expanded_scope_replay.edg.xml",
                        rewrite_endpoints=rewrite_joined_endpoints,
                    )
                    if rewrite_joined_endpoints:
                        replay_connection_file, crossing_rewrite_count = _write_joined_endpoint_connection_file(
                            Path(str(scope_report.get("connection_file", ""))),
                            join_patch_file,
                            output_dir / safe_junction_id / "expanded_scope_replay.con.xml",
                        )
                    else:
                        replay_connection_file, crossing_rewrite_count = (
                            Path(str(scope_report.get("connection_file", ""))),
                            0,
                        )
                    scope_report["expanded_scope_crossing_node_rewrite_count"] = crossing_rewrite_count
                    replay_candidate_net_file = Path(str(scope_report.get("net_file", "")))
                    preabsorbed_join_internal_edge_ids = []
                    scope_report["replay_scope"] = "expanded_scope"
                replay_absorbed_join_internal_edge_ids = list(
                    dict.fromkeys(
                        [
                            *preabsorbed_join_internal_edge_ids,
                            *[
                                edge_id
                                for edge_id in replay_blocking_self_loop_edge_drops
                                if edge_id not in protected_self_loop_edge_ids
                            ],
                        ]
                    )
                )
                replay_blocking_self_loop_edge_drops = [
                    edge_id
                    for edge_id in replay_blocking_self_loop_edge_drops
                    if edge_id in protected_self_loop_edge_ids
                ]
                if candidate_replay_target_internal_subgraph and replay_blocking_self_loop_edge_drops:
                    surviving_edge_ids = _edge_file_ids(replay_edge_file)
                    deferred_self_loop_edge_ids = [
                        edge_id
                        for edge_id in replay_blocking_self_loop_edge_drops
                        if (
                            _join_internal_self_loop_drop_has_witness(
                                edge_id,
                                replay_dropped_self_loop_edges,
                                surviving_edge_ids,
                            )
                            or _teacher_boundary_edge_has_target_junction(
                                teacher_net_file,
                                teacher_junction_id,
                                edge_id,
                            )
                        )
                    ]
                    if deferred_self_loop_edge_ids:
                        replay_absorbed_join_internal_edge_ids = [
                            *replay_absorbed_join_internal_edge_ids,
                            *deferred_self_loop_edge_ids,
                        ]
                        deferred_self_loop_edge_id_set = set(deferred_self_loop_edge_ids)
                        replay_blocking_self_loop_edge_drops = [
                            edge_id
                            for edge_id in replay_blocking_self_loop_edge_drops
                            if edge_id not in deferred_self_loop_edge_id_set
                        ]
                scope_report["replay_node_file"] = str(replay_node_file)
                scope_report["replay_edge_file"] = str(replay_edge_file)
                scope_report["replay_edge_endpoint_rewrite_count"] = replay_edge_endpoint_rewrite_count
                scope_report["replay_self_loop_edge_drop_count"] = len(replay_dropped_self_loop_edges)
                scope_report["replay_dropped_self_loop_edges"] = replay_dropped_self_loop_edges
                scope_report["replay_absorbed_join_internal_edge_ids"] = replay_absorbed_join_internal_edge_ids
                scope_report["replay_blocking_self_loop_edge_drops"] = replay_blocking_self_loop_edge_drops
                if replay_blocking_self_loop_edge_drops:
                    scope_report["status"] = "review"
                    skipped_candidates.append(
                        {
                            "index": index,
                            "junction_id": junction_id,
                            "candidate_status": "unsafe_replay_self_loop_edge_drop",
                            "skip_reason": "singleton_or_no_witness_self_loop_drop"
                            if candidate_replay_target_internal_subgraph
                            else "protected_self_loop_edge_drop",
                            "replay_blocking_self_loop_edge_drops": replay_blocking_self_loop_edge_drops,
                        }
                    )
                    continue
                attempted_ready_count += 1
                try:
                    variant_report = variant_builder(
                        raw_node_file=replay_node_file,
                        raw_edge_file=replay_edge_file,
                        raw_connection_file=replay_connection_file,
                        raw_type_file=current_raw_type_file,
                        raw_tllogic_file=replay_tllogic_file,
                        teacher_net_file=teacher_net_file,
                        candidate_net_file=replay_candidate_net_file,
                        junction_id=joined_scope_junction_id,
                        output_dir=output_dir / safe_junction_id / "teacher_replay",
                        edge_map=replay_edge_map,
                        prefix=variant_prefix,
                        teacher_junction_id=teacher_junction_id,
                        crossing_edge_overrides=crossing_edge_overrides_by_junction.get(joined_scope_junction_id)
                        or crossing_edge_overrides_by_junction.get(junction_id)
                        or crossing_edge_overrides_by_junction.get(teacher_junction_id),
                        approach_endpoint_rebuild_plan=candidate.get("approach_endpoint_rebuild_plan", {}),
                        replay_target_internal_subgraph=candidate_replay_target_internal_subgraph,
                        preserve_teacher_lane_shapes=not use_full_network_join_patch_replay,
                        emit_teacher_crossings=not use_full_network_join_patch_replay,
                        netconvert_binary=netconvert_binary,
                        sumo_binary=sumo_binary,
                        timeout_seconds=timeout_seconds,
                    )
                except Exception as exc:  # noqa: BLE001 - isolate one injected variant builder failure.
                    variant_report = _variant_exception_report(exc, joined_scope_junction_id)
                attached_report = _attach_candidate_template_context(variant_report, candidate)
                attached_report.setdefault("teacher_junction_id", teacher_junction_id)
                if scope_report.get("replay_scope") == "expanded_scope":
                    attached_report["candidate_scope_status"] = "local_scope"
                    attached_report["global_candidate_eligible"] = False
                    attached_report["candidate_scope_reason"] = "expanded_scope_replay_uses_local_plain_net"
                else:
                    attached_report["candidate_scope_status"] = "full_network"
                    attached_report["global_candidate_eligible"] = True
                variant_reports.append(attached_report)
                followup_candidate = _expanded_scope_followup_candidate_for_unsafe_internal_replay(
                    candidate,
                    attached_report,
                    replay_edge_file,
                    junction_id=junction_id,
                )
                if followup_candidate is not None:
                    attached_report["expanded_scope_followup_emitted"] = True
                    expanded_scope_followup_candidates.append(followup_candidate)
                    candidates.append(followup_candidate)
                final_net_file = Path(str(attached_report.get("final_net_file", "")))
                if (
                    (use_full_network_replay or use_full_network_join_patch_replay)
                    and sequential_accept_passed_variants
                    and attached_report.get("status") == "pass"
                    and attached_report.get("parity_gate_status") == "pass"
                    and final_net_file.exists()
                ):
                    attached_report["composite_applied"] = True
                    composite_applied_candidate_count += 1
                    composite_net_file = str(final_net_file)
                    replay_entry = _accepted_target_internal_replay_entry(
                        attached_report,
                        junction_id=joined_scope_junction_id,
                        teacher_junction_id=teacher_junction_id,
                    )
                    if replay_entry is not None:
                        accepted_internal_replays.append(replay_entry)
                    applied_candidate_edge_ids.update(candidate_edge_ids)
                    applied_candidate_node_ids.update(candidate_node_ids)
                    current_candidate_net_file = final_net_file
                    if plain_exporter is not None and index < len(candidates) - 1:
                        export_report = plain_exporter(
                            net_file=final_net_file,
                            output_dir=output_dir / safe_junction_id / "sequential_plain",
                            prefix=f"{variant_prefix}_sequential",
                            netconvert_binary=netconvert_binary,
                            timeout_seconds=timeout_seconds,
                        )
                        sequential_plain_export_reports.append(export_report)
                        if export_report.get("status") == "pass":
                            export_report["teacher_plain_tls_cleanup"] = _prune_plain_tls_against_teacher(
                                teacher_net_file=teacher_net_file,
                                node_file=Path(str(export_report["raw_node_file"])),
                                connection_file=Path(str(export_report["raw_connection_file"])),
                                tllogic_file=(
                                    Path(str(export_report["raw_tllogic_file"]))
                                    if export_report.get("raw_tllogic_file")
                                    else None
                                ),
                            )
                            current_raw_node_file = Path(str(export_report["raw_node_file"]))
                            current_raw_edge_file = Path(str(export_report["raw_edge_file"]))
                            current_raw_connection_file = Path(str(export_report["raw_connection_file"]))
                            raw_type_value = str(export_report.get("raw_type_file", ""))
                            current_raw_type_file = Path(raw_type_value) if raw_type_value else None
                            raw_tllogic_value = str(export_report.get("raw_tllogic_file", ""))
                            current_raw_tllogic_file = Path(raw_tllogic_value) if raw_tllogic_value else None
                        else:
                            sequential_blocked_reason = str(
                                export_report.get("error", "plain export failed after accepted variant")
                            )
            else:
                skipped_candidates.append(
                    _expanded_scope_skip_entry(
                        index=index,
                        junction_id=junction_id,
                        candidate_status=str(candidate.get("candidate_status", "skipped")),
                        scope_report=scope_report,
                        replay_edge_map=replay_edge_map,
                    )
                )
            continue
        if candidate.get("candidate_status") != "ready_for_teacher_guided_variant" or not junction_id:
            candidate_status = str(candidate.get("candidate_status", "skipped"))
            skipped_candidates.append(
                {
                    "index": index,
                    "junction_id": junction_id,
                    "candidate_status": candidate_status,
                    **({"skip_reason": candidate_status} if candidate_status == "no_vehicle_reference_context" else {}),
                }
            )
            continue
        if not edge_map:
            skipped_candidates.append(
                {"index": index, "junction_id": junction_id, "candidate_status": "invalid_edge_map"}
            )
            continue
        if (
            max_ready_candidates is not None
            and max_ready_candidates > 0
            and attempted_ready_count >= max_ready_candidates
            and not is_followup_candidate
        ):
            skipped_candidates.append(
                {"index": index, "junction_id": junction_id, "candidate_status": "max_ready_candidates_reached"}
            )
            continue

        safe_junction_id = _queue_candidate_dir(index, junction_id)
        variant_prefix = f"{_safe_stage_name(prefix, max_len=12)}_{index + 1:03d}"
        attempted_ready_count += 1
        try:
            variant_report = variant_builder(
                raw_node_file=current_raw_node_file,
                raw_edge_file=current_raw_edge_file,
                raw_connection_file=current_raw_connection_file,
                raw_type_file=current_raw_type_file,
                raw_tllogic_file=current_raw_tllogic_file,
                teacher_net_file=teacher_net_file,
                candidate_net_file=current_candidate_net_file,
                junction_id=junction_id,
                output_dir=output_dir / safe_junction_id,
                edge_map=edge_map,
                prefix=variant_prefix,
                teacher_junction_id=teacher_junction_id,
                crossing_edge_overrides=crossing_edge_overrides_by_junction.get(junction_id)
                or crossing_edge_overrides_by_junction.get(teacher_junction_id),
                replay_target_internal_subgraph=candidate_replay_target_internal_subgraph,
                netconvert_binary=netconvert_binary,
                sumo_binary=sumo_binary,
                timeout_seconds=timeout_seconds,
            )
        except Exception as exc:  # noqa: BLE001 - isolate one injected variant builder failure.
            variant_report = _variant_exception_report(exc, junction_id)
        attached_report = _attach_candidate_template_context(variant_report, candidate)
        attached_report.setdefault("teacher_junction_id", teacher_junction_id)
        variant_reports.append(attached_report)
        followup_candidate = _expanded_scope_followup_candidate_for_unsafe_internal_replay(
            candidate,
            attached_report,
            current_raw_edge_file,
            junction_id=junction_id,
        )
        if followup_candidate is not None:
            attached_report["expanded_scope_followup_emitted"] = True
            expanded_scope_followup_candidates.append(followup_candidate)
            candidates.append(followup_candidate)
        final_net_file = Path(str(attached_report.get("final_net_file", "")))
        if (
            sequential_accept_passed_variants
            and attached_report.get("status") == "pass"
            and attached_report.get("parity_gate_status") == "pass"
            and final_net_file.exists()
        ):
            attached_report["composite_applied"] = True
            composite_applied_candidate_count += 1
            composite_net_file = str(final_net_file)
            replay_entry = _accepted_target_internal_replay_entry(
                attached_report,
                junction_id=junction_id,
                teacher_junction_id=teacher_junction_id,
            )
            if replay_entry is not None:
                accepted_internal_replays.append(replay_entry)
            applied_candidate_edge_ids.update(candidate_edge_ids)
            applied_candidate_node_ids.update(candidate_node_ids)
            current_candidate_net_file = final_net_file
            if plain_exporter is not None and index < len(candidates) - 1:
                export_report = plain_exporter(
                    net_file=final_net_file,
                    output_dir=output_dir / safe_junction_id / "sequential_plain",
                    prefix=f"{variant_prefix}_sequential",
                    netconvert_binary=netconvert_binary,
                    timeout_seconds=timeout_seconds,
                )
                sequential_plain_export_reports.append(export_report)
                if export_report.get("status") == "pass":
                    export_report["teacher_plain_tls_cleanup"] = _prune_plain_tls_against_teacher(
                        teacher_net_file=teacher_net_file,
                        node_file=Path(str(export_report["raw_node_file"])),
                        connection_file=Path(str(export_report["raw_connection_file"])),
                        tllogic_file=(
                            Path(str(export_report["raw_tllogic_file"]))
                            if export_report.get("raw_tllogic_file")
                            else None
                        ),
                    )
                    current_raw_node_file = Path(str(export_report["raw_node_file"]))
                    current_raw_edge_file = Path(str(export_report["raw_edge_file"]))
                    current_raw_connection_file = Path(str(export_report["raw_connection_file"]))
                    raw_type_value = str(export_report.get("raw_type_file", ""))
                    current_raw_type_file = Path(raw_type_value) if raw_type_value else None
                    raw_tllogic_value = str(export_report.get("raw_tllogic_file", ""))
                    current_raw_tllogic_file = Path(raw_tllogic_value) if raw_tllogic_value else None
                else:
                    sequential_blocked_reason = str(
                        export_report.get("error", "plain export failed after accepted variant")
                    )

    connection_mode_regression_reports: list[dict[str, object]] = []
    if connection_mode_regression_builder is not None:
        for regression_index, variant_report in enumerate(variant_reports, start=1):
            if not bool(variant_report.get("global_candidate_eligible", True)):
                variant_report["connection_mode_regression"] = {
                    "status": "skipped",
                    "automatic_promotion_gate": "not_applicable",
                    "reason": "local-scope candidate cannot be compared with the full source network",
                }
                continue
            final_net_file = Path(str(variant_report.get("final_net_file", "")))
            if not final_net_file.is_file():
                if variant_report.get("status") != "pass":
                    variant_report["connection_mode_regression"] = {
                        "status": "skipped",
                        "automatic_promotion_gate": "not_applicable",
                        "reason": "failed variant did not produce a candidate network",
                    }
                    continue
                regression_report: dict[str, object] = {
                    "status": "fail",
                    "claim_status": "construction-invalid",
                    "automatic_promotion_gate": "blocked",
                    "error": f"candidate network missing: {final_net_file}",
                }
            else:
                source_scope_ids, candidate_scope_ids = _candidate_connection_mode_scope_ids(
                    variant_report
                )
                try:
                    regression_report = dict(
                        connection_mode_regression_builder(
                            source_net_file=candidate_net_file,
                            candidate_net_file=final_net_file,
                            output_dir=final_net_file.parent / "connection_mode_regression",
                            prefix=f"{_safe_stage_name(prefix, max_len=24)}_{regression_index:03d}",
                            target_source_junction_ids=source_scope_ids,
                            target_candidate_junction_ids=candidate_scope_ids,
                        )
                    )
                except (ET.ParseError, OSError, RuntimeError, TypeError, ValueError) as exc:
                    regression_report = {
                        "status": "fail",
                        "claim_status": "construction-invalid",
                        "automatic_promotion_gate": "blocked",
                        "error": f"{type(exc).__name__}: {exc}",
                    }
            variant_report["connection_mode_regression"] = regression_report
            connection_mode_regression_reports.append(regression_report)
    if connection_mode_regression_builder is None:
        connection_mode_regression_status = "skipped"
    elif not connection_mode_regression_reports:
        connection_mode_regression_status = "not_run"
    elif all(report.get("status") == "pass" for report in connection_mode_regression_reports):
        connection_mode_regression_status = "pass"
    else:
        connection_mode_regression_status = "fail"
    connection_mode_regression_failed = connection_mode_regression_status == "fail"

    attempted_count = len(variant_reports)
    pass_count = sum(1 for report in variant_reports if report.get("status") == "pass")
    failed_count = attempted_count - pass_count
    parity_pass_count = sum(1 for report in variant_reports if report.get("parity_gate_status") == "pass")
    local_scope_candidate_count = sum(
        1 for report in variant_reports if report.get("candidate_scope_status") == "local_scope"
    )
    global_candidate_eligible_count = sum(
        1 for report in variant_reports if report.get("global_candidate_eligible", True)
    )
    semantic_failure_counts = _semantic_failure_counts(variant_reports)
    semantic_layer_gate_counts = _semantic_layer_gate_counts(variant_reports)
    approach_integrity_failure_counts = _approach_integrity_failure_counts(semantic_failure_counts)
    expanded_scope_pass_count = sum(1 for report in expanded_scope_reports if report.get("status") == "pass")
    best_expanded_scope_net_file = ""
    for expanded_report in expanded_scope_reports:
        if expanded_report.get("status") != "pass":
            continue
        net_file = Path(str(expanded_report.get("net_file", "")))
        if net_file.exists():
            best_expanded_scope_net_file = str(net_file)
            break
    final_internal_replay_reports = []
    final_internal_replay_status = "skipped"
    final_internal_replay_normalize_report = None
    final_internal_replay_normalized_net_file = ""
    if (
        sequential_accept_passed_variants
        and composite_applied_candidate_count > 0
        and composite_net_file
        and Path(composite_net_file).exists()
        and accepted_internal_replays
    ):
        final_internal_replay_status = "pass"
        replay_junction_ids = {str(entry["junction_id"]) for entry in accepted_internal_replays}
        use_clean_replay_base = (
            any(entry.get("prefer_clean_replay_base") for entry in accepted_internal_replays)
            and _net_contains_normal_junctions(candidate_net_file, replay_junction_ids)
        )
        current_composite_net_file = (
            candidate_net_file
            if use_clean_replay_base
            else Path(composite_net_file)
        )
        restore_dir = output_dir / "final_internal_replay"
        restore_dir.mkdir(parents=True, exist_ok=True)
        for restore_index, replay_entry in enumerate(accepted_internal_replays, start=1):
            restore_junction_id = str(replay_entry["junction_id"])
            restore_file = (
                restore_dir
                / f"{restore_index:03d}_{_safe_stage_name(restore_junction_id, max_len=32)}_target_internal_replay.net.xml"
            )
            replay_report = final_internal_replay_writer(
                candidate_net_file=current_composite_net_file,
                teacher_net_file=teacher_net_file,
                output_file=restore_file,
                junction_id=restore_junction_id,
                teacher_junction_id=str(replay_entry["teacher_junction_id"]),
                edge_map=dict(replay_entry["edge_map"]),
            )
            final_internal_replay_reports.append(replay_report)
            restored_net_file = Path(str(replay_report.get("net_file", "")))
            if replay_report.get("status") != "pass" or not restored_net_file.exists():
                final_internal_replay_status = "fail"
                break
            current_composite_net_file = restored_net_file
        if final_internal_replay_status == "pass":
            normalized_composite_net_file = _stage_file(
                restore_dir,
                prefix,
                "final_internal_replay_normalized.net.xml",
            )
            final_internal_replay_normalize_command = [
                netconvert_binary,
                "--sumo-net-file",
                _command_path(current_composite_net_file, restore_dir),
                "--output-file",
                _command_path(normalized_composite_net_file, restore_dir),
            ]
            final_internal_replay_normalize_report = _command_report(
                command_runner(
                    final_internal_replay_normalize_command,
                    cwd=restore_dir,
                    timeout_seconds=timeout_seconds,
                )
            )
            final_internal_replay_normalize_report["output_file"] = str(normalized_composite_net_file)
            geometry_restore_reports = []
            if (
                final_internal_replay_normalize_report.get("status") == "pass"
                and normalized_composite_net_file.exists()
            ):
                excluded_replay_junction_ids = {
                    str(replay_entry["junction_id"]) for replay_entry in accepted_internal_replays
                }
                final_internal_replay_normalize_report["non_target_internal_restore"] = (
                    _restore_non_target_internal_artifacts(
                        source_file=current_composite_net_file,
                        target_file=normalized_composite_net_file,
                        exclude_junction_ids=excluded_replay_junction_ids,
                    )
                )
                final_internal_replay_normalize_report["teacher_non_tls_tllogic_cleanup"] = (
                    _remove_teacher_non_tls_tllogics(
                        teacher_net_file=teacher_net_file,
                        target_file=normalized_composite_net_file,
                    )
                )
                final_internal_replay_normalize_report["false_traffic_light_type_restore"] = (
                    _restore_false_traffic_light_junction_types(
                        source_file=current_composite_net_file,
                        target_file=normalized_composite_net_file,
                        fallback_node_file=current_raw_node_file,
                        exclude_junction_ids=excluded_replay_junction_ids,
                    )
                )
                final_internal_replay_normalize_report["teacher_absent_context_tls_cleanup"] = (
                    _demote_teacher_absent_context_tls(
                        teacher_net_file=teacher_net_file,
                        target_file=normalized_composite_net_file,
                        accepted_internal_replays=accepted_internal_replays,
                    )
                )
                for replay_entry, replay_report in zip(accepted_internal_replays, final_internal_replay_reports):
                    restored_net_file = Path(str(replay_report.get("net_file", "")))
                    if replay_report.get("status") != "pass" or not restored_net_file.exists():
                        continue
                    geometry_restore_report = _restore_replayed_geometry_attrs(
                        source_file=restored_net_file,
                        target_file=normalized_composite_net_file,
                        junction_id=str(replay_entry["junction_id"]),
                    )
                    geometry_restore_reports.append(geometry_restore_report)
                    if geometry_restore_report.get("status") != "pass":
                        break
                final_internal_replay_normalize_report["geometry_restore"] = geometry_restore_reports
                if all(report.get("status") == "pass" for report in geometry_restore_reports):
                    canonical_composite_net_file = _stage_file(
                        restore_dir,
                        prefix,
                        "final_internal_replay_canonical.net.xml",
                    )
                    final_internal_replay_canonical_command = [
                        netconvert_binary,
                        "--sumo-net-file",
                        _command_path(normalized_composite_net_file, restore_dir),
                        "--output-file",
                        _command_path(canonical_composite_net_file, restore_dir),
                    ]
                    final_internal_replay_canonical_report = _command_report(
                        command_runner(
                            final_internal_replay_canonical_command,
                            cwd=restore_dir,
                            timeout_seconds=timeout_seconds,
                        )
                    )
                    final_internal_replay_canonical_report["output_file"] = str(canonical_composite_net_file)
                    final_internal_replay_normalize_report["canonicalize"] = final_internal_replay_canonical_report
                    if (
                        final_internal_replay_canonical_report.get("status") == "pass"
                        and canonical_composite_net_file.exists()
                    ):
                        canonical_geometry_restore_reports = []
                        for replay_entry, replay_report in zip(accepted_internal_replays, final_internal_replay_reports):
                            restored_net_file = Path(str(replay_report.get("net_file", "")))
                            if replay_report.get("status") != "pass" or not restored_net_file.exists():
                                continue
                            canonical_geometry_restore_report = _restore_replayed_geometry_attrs(
                                source_file=restored_net_file,
                                target_file=canonical_composite_net_file,
                                junction_id=str(replay_entry["junction_id"]),
                            )
                            canonical_geometry_restore_reports.append(canonical_geometry_restore_report)
                            if canonical_geometry_restore_report.get("status") != "pass":
                                break
                        final_internal_replay_canonical_report["geometry_restore"] = canonical_geometry_restore_reports
                        final_internal_replay_canonical_report["teacher_absent_context_tls_cleanup"] = (
                            _demote_teacher_absent_context_tls(
                                teacher_net_file=teacher_net_file,
                                target_file=canonical_composite_net_file,
                                accepted_internal_replays=accepted_internal_replays,
                            )
                        )
                        if all(report.get("status") == "pass" for report in canonical_geometry_restore_reports):
                            final_internal_replay_normalized_net_file = str(canonical_composite_net_file)
                            composite_net_file = final_internal_replay_normalized_net_file
                        else:
                            final_internal_replay_status = "fail"
                            final_internal_replay_normalize_report["status"] = "fail"
                            final_internal_replay_normalize_report["error"] = (
                                "geometry restore failed after canonicalization"
                            )
                    else:
                        final_internal_replay_status = "fail"
                        final_internal_replay_normalize_report["status"] = "fail"
                        final_internal_replay_normalize_report["error"] = "canonicalize failed after geometry restore"
                else:
                    final_internal_replay_status = "fail"
                    final_internal_replay_normalize_report["status"] = "fail"
                    final_internal_replay_normalize_report["error"] = "geometry restore failed after normalization"
            else:
                final_internal_replay_status = "fail"
                if final_internal_replay_normalize_report.get("status") == "pass":
                    final_internal_replay_normalize_report["status"] = "fail"
                    final_internal_replay_normalize_report["error"] = (
                        f"normalized output missing: {normalized_composite_net_file}"
                    )
    final_composite_parity = _final_composite_parity_gate(
        teacher_net_file=teacher_net_file,
        composite_net_file=Path(composite_net_file) if composite_net_file else None,
        accepted_internal_replays=accepted_internal_replays,
        enabled=sequential_accept_passed_variants and final_internal_replay_status != "fail",
    )
    final_composite_parity_failed = final_composite_parity.get("status") == "fail"
    final_context_parity = _final_context_parity_gate(
        teacher_net_file=teacher_net_file,
        composite_net_file=Path(composite_net_file) if composite_net_file else None,
        accepted_internal_replays=accepted_internal_replays,
        teacher_join_groups_by_cluster=teacher_join_groups_by_cluster,
        enabled=sequential_accept_passed_variants and final_internal_replay_status != "fail",
    )
    final_context_parity_failed = final_context_parity.get("status") == "fail"
    context_gate_status = str(final_context_parity.get("status", "skipped"))
    sequential_composite_ready = (
        sequential_accept_passed_variants
        and composite_applied_candidate_count > 0
        and bool(composite_net_file)
        and Path(composite_net_file).exists()
        and final_internal_replay_status != "fail"
        and not final_composite_parity_failed
        and not final_context_parity_failed
    )
    if attempted_count == 0:
        status = "blocked"
        claim_status = "blocked"
        parity_gate_status = "blocked"
    elif final_composite_parity_failed:
        status = "fail"
        claim_status = "construction-invalid"
        parity_gate_status = "fail"
    elif final_context_parity_failed:
        status = "fail"
        claim_status = "construction-invalid"
        parity_gate_status = "pass" if parity_pass_count == attempted_count else "fail"
    elif connection_mode_regression_failed:
        status = "fail"
        claim_status = "construction-invalid"
        parity_gate_status = "pass" if parity_pass_count == attempted_count else "fail"
    elif sequential_composite_ready and failed_count == 0:
        status = "pass"
        claim_status = "diagnostic-demo"
        parity_gate_status = "pass"
    else:
        status = "pass" if failed_count == 0 and parity_pass_count == attempted_count else "fail"
        claim_status = "construction-invalid" if failed_count else "diagnostic-demo"
        parity_gate_status = "pass" if parity_pass_count == attempted_count else "fail"

    approach_integrity_status = _approach_integrity_status(
        parity_gate_status=parity_gate_status,
        attempted_count=attempted_count,
        semantic_failure_counts=semantic_failure_counts,
        approach_failure_counts=approach_integrity_failure_counts,
    )
    promotion_gate_file = output_dir / f"{prefix}_promotion_gate.json"
    promotion_gate = _write_teacher_guided_promotion_gate(
        output_file=promotion_gate_file,
        status=status,
        claim_status=claim_status,
        parity_gate_status=parity_gate_status,
        context_gate_status=context_gate_status,
        connection_mode_regression_status=connection_mode_regression_status,
        approach_integrity_status=approach_integrity_status,
        variant_reports=variant_reports,
    )

    run_report_file = output_dir / f"{prefix}_run_report.json"
    report = {
        "status": status,
        "claim_status": claim_status,
        "parity_gate_status": parity_gate_status,
        "teacher_net_file": str(teacher_net_file),
        "candidate_net_file": str(candidate_net_file),
        "raw_node_file": str(raw_node_file),
        "raw_edge_file": str(raw_edge_file),
        "raw_connection_file": str(raw_connection_file),
        "raw_type_file": str(raw_type_file) if raw_type_file is not None else "",
        "raw_tllogic_file": str(raw_tllogic_file) if raw_tllogic_file is not None else "",
        "candidate_count": len(candidates),
        "max_ready_candidates": max_ready_candidates if max_ready_candidates is not None else "",
        "attempted_candidate_count": attempted_count,
        "skipped_candidate_count": len(skipped_candidates),
        "pass_candidate_count": pass_count,
        "failed_candidate_count": failed_count,
        "parity_pass_candidate_count": parity_pass_count,
        "local_scope_candidate_count": local_scope_candidate_count,
        "global_candidate_eligible_count": global_candidate_eligible_count,
        "semantic_failure_counts": semantic_failure_counts,
        "semantic_layer_gate_counts": semantic_layer_gate_counts,
        "approach_integrity_status": approach_integrity_status,
        "approach_integrity_failure_counts": approach_integrity_failure_counts,
        "context_gate_status": context_gate_status,
        "connection_mode_regression_status": connection_mode_regression_status,
        "connection_mode_regression_reports": connection_mode_regression_reports,
        "promotion_gate_status": promotion_gate["status"],
        "promotion_gate_file": str(promotion_gate_file),
        "expanded_scope_candidate_count": len(expanded_scope_reports),
        "expanded_scope_pass_candidate_count": expanded_scope_pass_count,
        "best_expanded_scope_net_file": best_expanded_scope_net_file,
        "expanded_scope_followup_candidate_count": len(expanded_scope_followup_candidates),
        "expanded_scope_followup_candidates": expanded_scope_followup_candidates,
        "expand_fragmented_tls_join_scope": expand_fragmented_tls_join_scope,
        "sequential_accept_passed_variants": sequential_accept_passed_variants,
        "sequential_plain_export_status": "skipped"
        if not sequential_accept_passed_variants or not sequential_plain_export_reports
        else ("pass" if all(report.get("status") == "pass" for report in sequential_plain_export_reports) else "fail"),
        "sequential_plain_export_reports": sequential_plain_export_reports,
        "composite_applied_candidate_count": composite_applied_candidate_count,
        "composite_net_file": composite_net_file,
        "final_internal_replay_status": final_internal_replay_status,
        "final_internal_replay_normalize": final_internal_replay_normalize_report,
        "final_internal_replay_normalized_net_file": final_internal_replay_normalized_net_file,
        "final_composite_parity": final_composite_parity,
        "final_context_parity": final_context_parity,
        "final_internal_replay_restored_count": sum(
            1 for report in final_internal_replay_reports if report.get("status") == "pass"
        ),
        "final_internal_replay_reports": final_internal_replay_reports,
        "expanded_scope_reports": expanded_scope_reports,
        "run_report_file": str(run_report_file),
        "variant_reports": variant_reports,
        "teacher_pattern_contexts": _teacher_pattern_contexts(variant_reports + expanded_scope_reports),
        "skipped_candidates": skipped_candidates,
        "review_policy": (
            "code-native Connection Mode regression must pass; use NetEdit only as optional visual review"
        ),
    }
    run_report_file.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    return report


def run_teacher_guided_repair_matrix(
    *,
    queue_report: dict[str, Any],
    target_junction_ids: list[str],
    raw_node_file: Path,
    raw_edge_file: Path,
    raw_connection_file: Path,
    output_dir: Path,
    prefix: str = "teacher_guided_probe_matrix",
    queue_base_dir: Path | None = None,
    raw_type_file: Path | None = None,
    raw_tllogic_file: Path | None = None,
    crossing_edge_overrides_by_junction: dict[str, dict[str, str | list[str]]] | None = None,
    replay_target_internal_subgraph: bool = True,
    netconvert_binary: str = "netconvert",
    sumo_binary: str = "sumo",
    timeout_seconds: float = 240.0,
    command_runner: Any = run_command,
    repair_queue_runner: Any = run_teacher_guided_repair_queue,
    sequential_accept_passed_variants: bool = False,
) -> dict[str, object]:
    output_dir.mkdir(parents=True, exist_ok=True)
    candidates = queue_report.get("repair_candidates", []) or []
    if not isinstance(candidates, list):
        return _failure("queue report repair_candidates must be a list")

    candidates_by_id: dict[str, dict[str, Any]] = {}
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        for key in (str(candidate.get("reference_id", "")), str(candidate.get("junction_id", ""))):
            if key and key not in candidates_by_id:
                candidates_by_id[key] = candidate

    probes = []
    missing_junction_ids = []
    for index, junction_id in enumerate(target_junction_ids):
        candidate = candidates_by_id.get(str(junction_id))
        if candidate is None:
            missing_junction_ids.append(str(junction_id))
            continue
        probe_dir = output_dir / _queue_candidate_dir(index, str(junction_id))
        single_queue = dict(queue_report)
        single_queue["repair_candidates"] = [candidate]
        single_queue["repair_candidate_count"] = 1
        candidate_status = candidate.get("candidate_status")
        single_queue["ready_candidate_count"] = 1 if candidate_status == "ready_for_teacher_guided_variant" else 0
        single_queue["expanded_scope_candidate_count"] = (
            1 if candidate_status == "needs_expanded_rebuild_scope" else 0
        )
        single_queue["blocked_candidate_count"] = (
            0 if single_queue["ready_candidate_count"] or single_queue["expanded_scope_candidate_count"] else 1
        )
        single_queue_file = probe_dir / "single_queue.json"
        probe_dir.mkdir(parents=True, exist_ok=True)
        single_queue["queue_file"] = str(single_queue_file)
        single_queue_file.write_text(json.dumps(single_queue, indent=2, ensure_ascii=False), encoding="utf-8")
        run_report = repair_queue_runner(
            queue_report=single_queue,
            raw_node_file=raw_node_file,
            raw_edge_file=raw_edge_file,
            raw_connection_file=raw_connection_file,
            raw_type_file=raw_type_file,
            raw_tllogic_file=raw_tllogic_file,
            crossing_edge_overrides_by_junction=crossing_edge_overrides_by_junction,
            output_dir=probe_dir,
            prefix=f"{prefix}_{index + 1:03d}",
            queue_base_dir=queue_base_dir,
            replay_target_internal_subgraph=replay_target_internal_subgraph,
            max_ready_candidates=1,
            netconvert_binary=netconvert_binary,
            sumo_binary=sumo_binary,
            timeout_seconds=timeout_seconds,
            command_runner=command_runner,
            sequential_accept_passed_variants=sequential_accept_passed_variants,
        )
        road_continuity_summary = _road_continuity_probe_summary(run_report)
        probes.append(
            {
                "junction_id": str(junction_id),
                "status": str(run_report.get("status", "")),
                "parity_gate_status": str(run_report.get("parity_gate_status", "")),
                "promotion_gate_status": str(run_report.get("promotion_gate_status", "")),
                "approach_integrity_status": str(run_report.get("approach_integrity_status", "")),
                "context_gate_status": str(run_report.get("context_gate_status", "")),
                **road_continuity_summary,
                "semantic_failure_counts": run_report.get("semantic_failure_counts", {})
                if isinstance(run_report.get("semantic_failure_counts"), dict)
                else {},
                "semantic_layer_gate_counts": run_report.get("semantic_layer_gate_counts", {})
                if isinstance(run_report.get("semantic_layer_gate_counts"), dict)
                else {},
                "best_expanded_scope_net_file": str(run_report.get("best_expanded_scope_net_file", "")),
                "composite_applied_candidate_count": run_report.get("composite_applied_candidate_count", 0),
                "composite_net_file": str(run_report.get("composite_net_file", "")),
                "run_report_file": str(run_report.get("run_report_file", "")),
                "single_queue_file": str(single_queue_file),
            }
        )

    all_parity_pass = bool(probes) and all(probe["parity_gate_status"] == "pass" for probe in probes)
    all_promotion_pass = bool(probes) and all(probe["promotion_gate_status"] == "pass" for probe in probes)
    all_context_pass = bool(probes) and all(probe["context_gate_status"] != "fail" for probe in probes)
    all_road_continuity_pass = bool(probes) and all(
        probe["road_continuity_gate_status"] == "pass" for probe in probes
    )
    status = (
        "pass"
        if all_parity_pass
        and all_promotion_pass
        and all_context_pass
        and all_road_continuity_pass
        and not missing_junction_ids
        else "fail"
    )
    matrix_file = output_dir / f"{prefix}.json"
    report = {
        "status": status,
        "claim_status": "diagnostic-demo",
        "requested_junction_count": len(target_junction_ids),
        "probe_count": len(probes),
        "missing_junction_ids": missing_junction_ids,
        "all_parity_gate_pass": all_parity_pass,
        "all_promotion_gate_pass": all_promotion_pass,
        "all_context_gate_pass": all_context_pass,
        "all_road_continuity_gate_pass": all_road_continuity_pass,
        "matrix_file": str(matrix_file),
        "probes": probes,
        "review_policy": "probe matrix only; promote to workflow evidence after full OSM workflow replay uses the same gate",
    }
    matrix_file.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    return report
