"""Check teacher parity, protected semantics and final context preservation."""

from __future__ import annotations

from pathlib import Path
from typing import Any
import xml.etree.ElementTree as ET
from ..junction_teacher_model import _extract_teacher_junction_model
from .edge_mapping import _valid_edge_map
from .network import (
    ROAD_CONTINUITY_COUNT_FIELDS,
    ROAD_CONTINUITY_FAILURE_FIELDS,
    TURNAROUND_DIR,
    _connection_touches_any_edge,
    _connection_touches_walkingarea_internal,
    _dict_mismatch_count,
    _int_count,
)
from .scope import _context_count_delta, _local_junction_context_summary, _split_cluster_member_residuals
from .signatures import (
    _approach_edge_signatures,
    _approach_endpoint_signatures,
    _controlled_link_count,
    _controlled_link_index_stats,
    _controlled_pedestrian_link_signatures,
    _controlled_vehicle_link_signatures,
    _crossing_geometry_signatures,
    _crossing_signatures,
    _internal_connection_signatures,
    _internal_edge_signatures,
    _internal_junction_signatures,
    _junction_signature,
    _request_signatures,
    _tl_phase_signatures,
    _uncontrolled_pedestrian_connection_signatures,
    _walking_area_signatures,
)


def _non_target_internal_restore_changed(report: dict[str, object]) -> bool:
    changed = any(
        int(report.get(key, 0) or 0)
        for key in (
            "removed_non_target_internal_edge_count",
            "restored_non_target_internal_edge_count",
            "removed_non_target_internal_junction_count",
            "restored_non_target_internal_junction_count",
            "removed_non_target_internal_connection_count",
            "restored_non_target_internal_connection_count",
            "restored_non_target_normal_junction_attr_count",
            "restored_non_target_request_count",
        )
    )
    if (
        changed
        or int(report.get("restored_external_lane_count", 0) or 0)
        or int(report.get("restored_external_edge_centerline_count", 0) or 0)
    ):
        return True
    internal_report = report.get("internal_artifact_restore")
    return isinstance(internal_report, dict) and _non_target_internal_restore_changed(internal_report)


def _compare_teacher_models(
    teacher_model: dict[str, Any],
    candidate_model: dict[str, Any],
    *,
    edge_map: dict[str, str] | None = None,
    teacher_junction_id: str = "",
    candidate_junction_id: str = "",
) -> dict[str, object]:
    teacher_summary = _teacher_parity_summary(teacher_model)
    candidate_summary = _teacher_parity_summary(candidate_model)
    keys = sorted(set(teacher_summary) | set(candidate_summary))
    delta: dict[str, int] = {}
    for key in keys:
        candidate_value = candidate_summary.get(key, 0)
        teacher_value = teacher_summary.get(key, 0)
        if isinstance(candidate_value, int) and isinstance(teacher_value, int):
            delta[key] = candidate_value - teacher_value
        elif candidate_value != teacher_value:
            delta[f"{key}_mismatch_count"] = 1
    if edge_map is not None:
        teacher_signatures = _controlled_vehicle_link_signatures(
            teacher_model,
            edge_map=edge_map,
            source_junction_id=teacher_junction_id or str(teacher_model.get("junction_id", "")),
            target_junction_id=candidate_junction_id or str(candidate_model.get("junction_id", "")),
        )
        candidate_signatures = _controlled_vehicle_link_signatures(candidate_model)
        mismatch_count = _dict_mismatch_count(teacher_signatures, candidate_signatures)
        teacher_summary["controlled_vehicle_link_signatures"] = teacher_signatures
        candidate_summary["controlled_vehicle_link_signatures"] = candidate_signatures
        if mismatch_count:
            delta["controlled_vehicle_link_signature_mismatch_count"] = mismatch_count
        teacher_pedestrian_signatures = _controlled_pedestrian_link_signatures(
            teacher_model,
            edge_map=edge_map,
            source_junction_id=teacher_junction_id or str(teacher_model.get("junction_id", "")),
            target_junction_id=candidate_junction_id or str(candidate_model.get("junction_id", "")),
        )
        candidate_pedestrian_signatures = _controlled_pedestrian_link_signatures(candidate_model)
        pedestrian_mismatch_count = _dict_mismatch_count(teacher_pedestrian_signatures, candidate_pedestrian_signatures)
        teacher_summary["controlled_pedestrian_link_signatures"] = teacher_pedestrian_signatures
        candidate_summary["controlled_pedestrian_link_signatures"] = candidate_pedestrian_signatures
        if pedestrian_mismatch_count:
            delta["controlled_pedestrian_link_signature_mismatch_count"] = pedestrian_mismatch_count
        teacher_pedestrian_ring_signatures = _uncontrolled_pedestrian_connection_signatures(
            teacher_model,
            edge_map=edge_map,
            source_junction_id=teacher_junction_id or str(teacher_model.get("junction_id", "")),
            target_junction_id=candidate_junction_id or str(candidate_model.get("junction_id", "")),
        )
        candidate_pedestrian_ring_signatures = _uncontrolled_pedestrian_connection_signatures(candidate_model)
        pedestrian_ring_mismatch_count = _dict_mismatch_count(
            teacher_pedestrian_ring_signatures, candidate_pedestrian_ring_signatures
        )
        teacher_summary["uncontrolled_pedestrian_connection_signatures"] = teacher_pedestrian_ring_signatures
        candidate_summary["uncontrolled_pedestrian_connection_signatures"] = candidate_pedestrian_ring_signatures
        if pedestrian_ring_mismatch_count:
            delta["uncontrolled_pedestrian_connection_signature_mismatch_count"] = pedestrian_ring_mismatch_count
        teacher_junction_signature = _junction_signature(
            teacher_model,
            edge_map=edge_map,
            source_junction_id=teacher_junction_id or str(teacher_model.get("junction_id", "")),
            target_junction_id=candidate_junction_id or str(candidate_model.get("junction_id", "")),
        )
        candidate_junction_signature = _junction_signature(candidate_model)
        teacher_summary["junction_signature"] = teacher_junction_signature
        candidate_summary["junction_signature"] = candidate_junction_signature
        if teacher_junction_signature != candidate_junction_signature:
            delta["junction_signature_mismatch_count"] = 1
        teacher_approach_signatures = _approach_edge_signatures(
            teacher_model,
            edge_map=edge_map,
            source_junction_id=teacher_junction_id or str(teacher_model.get("junction_id", "")),
            target_junction_id=candidate_junction_id or str(candidate_model.get("junction_id", "")),
        )
        candidate_approach_signatures = _approach_edge_signatures(candidate_model)
        approach_mismatch_count = _dict_mismatch_count(teacher_approach_signatures, candidate_approach_signatures)
        teacher_summary["approach_edge_signatures"] = teacher_approach_signatures
        candidate_summary["approach_edge_signatures"] = candidate_approach_signatures
        if approach_mismatch_count:
            delta["approach_edge_signature_mismatch_count"] = approach_mismatch_count
        teacher_approach_endpoint_signatures = _approach_endpoint_signatures(
            teacher_model,
            edge_map=edge_map,
            source_junction_id=teacher_junction_id or str(teacher_model.get("junction_id", "")),
            target_junction_id=candidate_junction_id or str(candidate_model.get("junction_id", "")),
        )
        candidate_approach_endpoint_signatures = _approach_endpoint_signatures(candidate_model)
        approach_endpoint_mismatch_count = _dict_mismatch_count(
            teacher_approach_endpoint_signatures, candidate_approach_endpoint_signatures
        )
        teacher_summary["approach_endpoint_signatures"] = teacher_approach_endpoint_signatures
        candidate_summary["approach_endpoint_signatures"] = candidate_approach_endpoint_signatures
        if approach_endpoint_mismatch_count:
            delta["approach_endpoint_signature_mismatch_count"] = approach_endpoint_mismatch_count
        teacher_crossing_signatures = _crossing_signatures(
            teacher_model,
            edge_map=edge_map,
            source_junction_id=teacher_junction_id or str(teacher_model.get("junction_id", "")),
            target_junction_id=candidate_junction_id or str(candidate_model.get("junction_id", "")),
        )
        candidate_crossing_signatures = _crossing_signatures(candidate_model)
        crossing_mismatch_count = _dict_mismatch_count(teacher_crossing_signatures, candidate_crossing_signatures)
        teacher_summary["crossing_signatures"] = teacher_crossing_signatures
        candidate_summary["crossing_signatures"] = candidate_crossing_signatures
        if crossing_mismatch_count:
            delta["crossing_signature_mismatch_count"] = crossing_mismatch_count
        teacher_crossing_geometry_signatures = _crossing_geometry_signatures(
            teacher_model,
            source_junction_id=teacher_junction_id or str(teacher_model.get("junction_id", "")),
            target_junction_id=candidate_junction_id or str(candidate_model.get("junction_id", "")),
        )
        candidate_crossing_geometry_signatures = _crossing_geometry_signatures(candidate_model)
        crossing_geometry_mismatch_count = _dict_mismatch_count(
            teacher_crossing_geometry_signatures, candidate_crossing_geometry_signatures
        )
        teacher_summary["crossing_geometry_signatures"] = teacher_crossing_geometry_signatures
        candidate_summary["crossing_geometry_signatures"] = candidate_crossing_geometry_signatures
        if crossing_geometry_mismatch_count:
            delta["crossing_geometry_signature_mismatch_count"] = crossing_geometry_mismatch_count
        teacher_walking_area_signatures = _walking_area_signatures(
            teacher_model,
            source_junction_id=teacher_junction_id or str(teacher_model.get("junction_id", "")),
            target_junction_id=candidate_junction_id or str(candidate_model.get("junction_id", "")),
        )
        candidate_walking_area_signatures = _walking_area_signatures(candidate_model)
        walking_area_mismatch_count = _dict_mismatch_count(
            teacher_walking_area_signatures, candidate_walking_area_signatures
        )
        teacher_summary["walking_area_signatures"] = teacher_walking_area_signatures
        candidate_summary["walking_area_signatures"] = candidate_walking_area_signatures
        if walking_area_mismatch_count:
            delta["walking_area_signature_mismatch_count"] = walking_area_mismatch_count
        teacher_internal_edge_signatures = _internal_edge_signatures(
            teacher_model,
            source_junction_id=teacher_junction_id or str(teacher_model.get("junction_id", "")),
            target_junction_id=candidate_junction_id or str(candidate_model.get("junction_id", "")),
        )
        candidate_internal_edge_signatures = _internal_edge_signatures(candidate_model)
        internal_edge_mismatch_count = _dict_mismatch_count(
            teacher_internal_edge_signatures, candidate_internal_edge_signatures
        )
        teacher_summary["internal_edge_signatures"] = teacher_internal_edge_signatures
        candidate_summary["internal_edge_signatures"] = candidate_internal_edge_signatures
        if internal_edge_mismatch_count:
            delta["internal_edge_signature_mismatch_count"] = internal_edge_mismatch_count
        teacher_internal_junction_signatures = _internal_junction_signatures(
            teacher_model,
            edge_map=edge_map,
            source_junction_id=teacher_junction_id or str(teacher_model.get("junction_id", "")),
            target_junction_id=candidate_junction_id or str(candidate_model.get("junction_id", "")),
        )
        candidate_internal_junction_signatures = _internal_junction_signatures(candidate_model)
        internal_junction_mismatch_count = _dict_mismatch_count(
            teacher_internal_junction_signatures, candidate_internal_junction_signatures
        )
        teacher_summary["internal_junction_signatures"] = teacher_internal_junction_signatures
        candidate_summary["internal_junction_signatures"] = candidate_internal_junction_signatures
        if internal_junction_mismatch_count:
            delta["internal_junction_signature_mismatch_count"] = internal_junction_mismatch_count
        teacher_internal_connection_signatures = _internal_connection_signatures(
            teacher_model,
            edge_map=edge_map,
            source_junction_id=teacher_junction_id or str(teacher_model.get("junction_id", "")),
            target_junction_id=candidate_junction_id or str(candidate_model.get("junction_id", "")),
        )
        candidate_internal_connection_signatures = _internal_connection_signatures(candidate_model)
        internal_connection_mismatch_count = _dict_mismatch_count(
            teacher_internal_connection_signatures, candidate_internal_connection_signatures
        )
        teacher_summary["internal_connection_signatures"] = teacher_internal_connection_signatures
        candidate_summary["internal_connection_signatures"] = candidate_internal_connection_signatures
        if internal_connection_mismatch_count:
            delta["internal_connection_signature_mismatch_count"] = internal_connection_mismatch_count
    return {
        "teacher": teacher_summary,
        "candidate": candidate_summary,
        "delta": delta,
    }


def _hybrid_osm_approach_authority_policy(
    raw_semantic_gate: dict[str, Any],
    *,
    replay_target_internal_subgraph: bool,
    preserve_teacher_lane_shapes: bool,
    edge_map: dict[str, str],
    lane_patch: dict[str, Any],
    target_internal_replay: dict[str, Any],
    tls_movement_parity: dict[str, Any],
    pedestrian_crossing_parity: dict[str, Any],
) -> dict[str, Any]:
    """Allow a deliberate OSM-approach/official-core authority split.

    Strict teacher parity remains the default.  The hybrid policy is active
    only when the caller explicitly preserves OSM lane shapes while replaying
    the complete teacher internal subgraph.  It may waive only the expected
    approach-edge and remote-endpoint signature deltas; every lane count,
    movement, internal/via path, TLS/linkIndex, pedestrian crossing, and mapped
    boundary invariant must still match.
    """

    active = replay_target_internal_subgraph and not preserve_teacher_lane_shapes
    if not active:
        return {
            "schema": "torii.hybrid_osm_approach_authority_policy.v1",
            "status": "not_applied",
            "policy": "strict_teacher_parity",
            "effective_semantic_gate": dict(raw_semantic_gate),
            "waived_raw_failures": [],
            "invariant_failures": [],
        }

    mapped_pairs = {
        str(teacher_edge): str(candidate_edge)
        for teacher_edge, candidate_edge in edge_map.items()
        if str(teacher_edge) and str(candidate_edge)
    }
    mapped_candidate_edges = set(mapped_pairs.values())
    invariant_failures: list[str] = []
    if not mapped_pairs:
        invariant_failures.append("edge_map_empty")
    if len(mapped_candidate_edges) != len(mapped_pairs):
        invariant_failures.append("edge_map_not_bijective")

    patched_pairs = {
        (str(item.get("teacher_edge_id", "")), str(item.get("candidate_edge_id", "")))
        for item in lane_patch.get("patched_edges", [])
        if isinstance(item, dict)
    }
    if lane_patch.get("status") != "pass":
        invariant_failures.append("lane_patch_not_pass")
    if patched_pairs != set(mapped_pairs.items()):
        invariant_failures.append("lane_patch_edge_map_mismatch")
    for field in (
        "added_missing_mapped_edge_count",
        "rebased_missing_mapped_edge_count",
        "endpoint_rewritten_missing_mapped_edge_count",
        "skipped_rebased_self_loop_edge_count",
        "pruned_boundary_edge_count",
    ):
        if int(lane_patch.get(field, 0) or 0):
            invariant_failures.append(f"lane_patch_{field}")
    if lane_patch.get("preserve_lane_shapes") is not False:
        invariant_failures.append("lane_patch_did_not_preserve_osm_shape_policy")

    preserved_endpoint_edges = {
        str(item.get("candidate_edge_id", ""))
        for item in target_internal_replay.get("preserved_mapped_boundary_endpoints", [])
        if isinstance(item, dict) and str(item.get("candidate_edge_id", ""))
    }
    blended_edges = {
        str(edge_id)
        for edge_id in target_internal_replay.get("blended_geometry_anchor_edge_ids", [])
        if str(edge_id)
    }
    if target_internal_replay.get("status") != "pass":
        invariant_failures.append("target_internal_replay_not_pass")
    if int(target_internal_replay.get("skipped_connection_count", 0) or 0):
        invariant_failures.append("target_internal_replay_skipped_connections")
    if target_internal_replay.get("copy_unmapped_boundary_edges") is not False:
        invariant_failures.append("target_internal_replay_copied_unmapped_boundary_edges")
    if target_internal_replay.get("preserve_mapped_boundary_endpoints") is not True:
        invariant_failures.append("mapped_boundary_endpoints_not_preserved")
    if target_internal_replay.get("blend_geometry_anchor_at_target") is not True:
        invariant_failures.append("local_target_geometry_blend_not_applied")
    if preserved_endpoint_edges != mapped_candidate_edges:
        invariant_failures.append("preserved_boundary_endpoint_set_mismatch")
    if blended_edges != mapped_candidate_edges:
        invariant_failures.append("blended_geometry_anchor_set_mismatch")
    if int(target_internal_replay.get("copied_boundary_edge_count", 0) or 0) != len(
        mapped_pairs
    ):
        invariant_failures.append("copied_boundary_edge_count_mismatch")
    if tls_movement_parity.get("status") != "pass":
        invariant_failures.append("tls_movement_parity_not_pass")
    if pedestrian_crossing_parity.get("status") != "pass":
        invariant_failures.append("pedestrian_crossing_parity_not_pass")

    allowed_fields = {
        "approach_edge_signature_mismatch_count",
        "approach_endpoint_signature_mismatch_count",
    }
    waived_raw_failures: list[dict[str, Any]] = []
    retained_raw_failures: list[dict[str, Any]] = []
    for failure in raw_semantic_gate.get("failures", []):
        if not isinstance(failure, dict):
            retained_raw_failures.append(
                {"report": "semantic_replay_gate", "field": "malformed_failure", "count": 1}
            )
            continue
        field = str(failure.get("field", ""))
        count = int(failure.get("count", 0) or 0)
        if (
            failure.get("report") == "parity"
            and field in allowed_fields
            and count == len(mapped_pairs)
        ):
            waived_raw_failures.append(dict(failure))
        else:
            retained_raw_failures.append(dict(failure))

    effective_failures = [
        *retained_raw_failures,
        *(
            {
                "report": "hybrid_osm_approach_authority_policy",
                "field": failure,
                "count": 1,
            }
            for failure in invariant_failures
        ),
    ]
    effective_gate = {
        "status": "fail" if effective_failures else "pass",
        "failures": effective_failures,
    }
    return {
        "schema": "torii.hybrid_osm_approach_authority_policy.v1",
        "status": "pass" if not effective_failures else "fail",
        "policy": "osm_remote_approaches_official_internal_core",
        "mapped_approach_edge_count": len(mapped_pairs),
        "mapped_candidate_edge_ids": sorted(mapped_candidate_edges),
        "waived_raw_failures": waived_raw_failures,
        "retained_raw_failures": retained_raw_failures,
        "invariant_failures": invariant_failures,
        "effective_semantic_gate": effective_gate,
    }


def _teacher_guided_semantics_gate(parity: dict[str, Any], **reports: dict[str, Any] | None) -> dict[str, object]:
    failures: list[dict[str, object]] = []
    for field, count in (parity.get("delta", {}) if isinstance(parity.get("delta"), dict) else {}).items():
        if isinstance(count, int) and count != 0:
            failures.append({"report": "parity", "field": str(field), "count": count})

    target_internal_replay = reports.get("target_internal_replay")
    internal_replay_complete = (
        isinstance(target_internal_replay, dict)
        and target_internal_replay.get("status") == "pass"
        and int(target_internal_replay.get("skipped_connection_count", 0) or 0) == 0
    )
    for report_name, report in reports.items():
        if not isinstance(report, dict):
            continue
        for field in (
            "skipped_pedestrian_connection_count",
            "skipped_vehicle_connection_count",
            "skipped_connection_count",
            "removed_stale_replaced_edge_connection_count",
        ):
            if internal_replay_complete and report_name in {"pedestrian_ring", "vehicle_connection_attrs"}:
                continue
            count = (
                _blocking_removed_stale_connection_count(report)
                if field == "removed_stale_replaced_edge_connection_count"
                else int(report.get(field, 0) or 0)
            )
            if count:
                failures.append({"report": report_name, "field": field, "count": count})

    return {"status": "fail" if failures else "pass", "failures": failures}


def _blocking_removed_stale_connection_count(report: dict[str, Any]) -> int:
    count = int(report.get("removed_stale_replaced_edge_connection_count", 0) or 0)
    removed = report.get("removed_stale_replaced_edge_connections", [])
    if not isinstance(removed, list):
        return count
    copied_boundary_edges = {
        str(item)
        for field in ("copied_boundary_edges", "copied_boundary_candidate_edges")
        for item in report.get(field, []) or []
        if str(item)
    }
    blocking = [
        connection
        for connection in removed
        if isinstance(connection, dict)
        and not _connection_touches_walkingarea_internal(connection)
        and not _connection_touches_any_edge(connection, copied_boundary_edges)
    ]
    return len(blocking)


def _blocking_removed_stale_boundary_connection_count(report: dict[str, Any]) -> int:
    count = int(report.get("removed_stale_boundary_edge_connection_count", 0) or 0)
    removed = report.get("removed_stale_boundary_edge_connections", [])
    if not isinstance(removed, list):
        return count
    return sum(
        1
        for connection in removed
        if isinstance(connection, dict) and str(connection.get("dir", "")).lower() != TURNAROUND_DIR
    )


def _semantic_layer_gates(
    semantic_gate: dict[str, Any],
    tls_movement_parity: dict[str, Any],
    pedestrian_crossing_parity: dict[str, Any] | None = None,
) -> dict[str, dict[str, Any]]:
    layers: dict[str, dict[str, Any]] = {
        name: {"status": "pass", "failure_count": 0, "failures": []}
        for name in ("topology", "movement_tls", "pedestrian_bike", "internal", "uncategorized")
    }
    for failure in semantic_gate.get("failures", []) if isinstance(semantic_gate, dict) else []:
        if not isinstance(failure, dict):
            continue
        layers[_semantic_layer_for_field(str(failure.get("field", "")))]["failures"].append(dict(failure))
    if isinstance(tls_movement_parity, dict) and tls_movement_parity.get("status") != "pass":
        layers["movement_tls"]["failures"].append(
            {"report": "tls_movement_parity", "field": "status", "count": 1}
        )
    if isinstance(pedestrian_crossing_parity, dict) and pedestrian_crossing_parity.get("status") != "pass":
        layers["pedestrian_bike"]["failures"].append(
            {
                "report": "pedestrian_crossing_parity",
                "field": "status",
                "count": _pedestrian_crossing_delta_count(pedestrian_crossing_parity),
            }
        )
    for layer in layers.values():
        layer["failure_count"] = len(layer["failures"])
        layer["status"] = "fail" if layer["failure_count"] else "pass"
    return layers


def _semantic_layer_for_field(field: str) -> str:
    if field.startswith(("crossing", "walking")) or "pedestrian" in field:
        return "pedestrian_bike"
    if field.startswith(("internal", "request")):
        return "internal"
    if field.startswith(("tl_", "controlled_", "vehicle_connection", "vehicle_movement")):
        return "movement_tls"
    if field.startswith("removed_stale_replaced_edge_connection"):
        return "topology"
    if field.startswith(("approach", "junction", "incoming_vehicle_edge", "outgoing_vehicle_edge")):
        return "topology"
    return "uncategorized"


def _pedestrian_crossing_delta_count(report: dict[str, Any]) -> int:
    count = 0
    for field in (
        "teacher_only_normalized_edge_signatures",
        "candidate_only_normalized_edge_signatures",
        "teacher_only_normalized_connection_signatures",
        "candidate_only_normalized_connection_signatures",
    ):
        values = report.get(field, [])
        count += len(values) if isinstance(values, list) else 0
    return max(1, count)


def _teacher_parity_summary(model: dict[str, Any]) -> dict[str, object]:
    summary = dict(model.get("summary", {}) if isinstance(model.get("summary"), dict) else {})
    traffic_light = model.get("traffic_light", {})
    attributes = traffic_light.get("attributes", {}) if isinstance(traffic_light, dict) else {}
    phases = traffic_light.get("phases", []) if isinstance(traffic_light, dict) else []
    phase_states = [str(phase.get("state", "")) for phase in phases if isinstance(phase, dict)]
    requests = model.get("requests", []) if isinstance(model.get("requests"), list) else []
    vehicle_connections = model.get("vehicle_connections", []) if isinstance(model.get("vehicle_connections"), list) else []
    pedestrian_connections = model.get("pedestrian_connections", []) if isinstance(model.get("pedestrian_connections"), list) else []
    incoming_count = int(summary.get("incoming_vehicle_edge_count", 0) or 0)
    outgoing_count = int(summary.get("outgoing_vehicle_edge_count", 0) or 0)
    vehicle_connection_count = int(summary.get("vehicle_connection_count", 0) or 0)
    expected_vehicle_movements = incoming_count * outgoing_count
    summary["vehicle_movement_matrix_expected_count"] = expected_vehicle_movements
    summary["vehicle_movement_matrix_missing_count"] = max(0, expected_vehicle_movements - vehicle_connection_count)
    target_tls_id = str(attributes.get("id", "") or model.get("junction_id", ""))
    summary["tl_type"] = str(attributes.get("type", "")) if isinstance(attributes, dict) else ""
    summary["tl_programID"] = str(attributes.get("programID", "")) if isinstance(attributes, dict) else ""
    summary["tl_offset"] = str(attributes.get("offset", "")) if isinstance(attributes, dict) else ""
    summary["tl_phase_state_lengths"] = sorted({len(state) for state in phase_states})
    summary["tl_phase_signatures"] = _tl_phase_signatures(phases)
    summary["request_signatures"] = _request_signatures(requests)
    summary["controlled_vehicle_link_count"] = _controlled_link_count(vehicle_connections, target_tls_id)
    summary["controlled_pedestrian_link_count"] = _controlled_link_count(pedestrian_connections, target_tls_id)
    summary["controlled_link_count"] = summary["controlled_vehicle_link_count"] + summary["controlled_pedestrian_link_count"]
    summary.update(_controlled_link_index_stats(vehicle_connections + pedestrian_connections, target_tls_id))
    return summary


def _final_composite_parity_gate(
    *,
    teacher_net_file: Path,
    composite_net_file: Path | None,
    accepted_internal_replays: list[dict[str, object]],
    enabled: bool,
) -> dict[str, object]:
    if not enabled:
        return {"status": "skipped", "reason": "disabled"}
    if composite_net_file is None or not composite_net_file.exists():
        return {"status": "skipped", "reason": "missing_composite_net_file"}
    if not accepted_internal_replays:
        return {"status": "skipped", "reason": "no_accepted_internal_replays"}
    try:
        teacher_root = ET.parse(teacher_net_file).getroot()
        composite_root = ET.parse(composite_net_file).getroot()
    except (OSError, ET.ParseError) as exc:
        return {"status": "fail", "reason": f"parse_error: {exc}", "reports": []}

    reports: list[dict[str, object]] = []
    for replay in accepted_internal_replays:
        junction_id = str(replay.get("junction_id", ""))
        teacher_junction_id = str(replay.get("teacher_junction_id", ""))
        edge_map = _valid_edge_map(replay.get("edge_map", {}))
        if not junction_id or not teacher_junction_id or not edge_map:
            reports.append(
                {
                    "status": "fail",
                    "junction_id": junction_id,
                    "teacher_junction_id": teacher_junction_id,
                    "reason": "missing_final_parity_inputs",
                }
            )
            continue
        try:
            teacher_model = _extract_teacher_junction_model(teacher_root, teacher_net_file, teacher_junction_id)
            candidate_model = _extract_teacher_junction_model(composite_root, composite_net_file, junction_id)
            parity = _compare_teacher_models(
                teacher_model,
                candidate_model,
                edge_map=edge_map,
                teacher_junction_id=teacher_junction_id,
                candidate_junction_id=junction_id,
            )
            semantic_replay_gate = _teacher_guided_semantics_gate(parity)
            reports.append(
                {
                    "status": semantic_replay_gate["status"],
                    "junction_id": junction_id,
                    "teacher_junction_id": teacher_junction_id,
                    "semantic_replay_gate": semantic_replay_gate,
                    "parity": parity,
                }
            )
        except (ET.ParseError, OSError, KeyError, TypeError, ValueError) as exc:
            reports.append(
                {
                    "status": "fail",
                    "junction_id": junction_id,
                    "teacher_junction_id": teacher_junction_id,
                    "reason": f"extract_error: {exc}",
                }
            )
    return {
        "status": "pass" if reports and all(report.get("status") == "pass" for report in reports) else "fail",
        "checked_junction_count": len(reports),
        "reports": reports,
    }


def _road_continuity_probe_summary(run_report: dict[str, Any]) -> dict[str, object]:
    counts: dict[str, int] = {}
    failure_counts: dict[str, int] = {}
    replay_count = 0
    for variant in run_report.get("variant_reports", []) or []:
        if not isinstance(variant, dict) or variant.get("expanded_scope_followup_emitted"):
            continue
        replay = variant.get("target_internal_replay")
        if not isinstance(replay, dict):
            continue
        replay_count += 1
        if replay.get("status") != "pass":
            failure_counts["status_not_pass"] = failure_counts.get("status_not_pass", 0) + 1
        for field in ROAD_CONTINUITY_COUNT_FIELDS:
            value = _int_count(replay.get(field, 0))
            if value:
                counts[field] = counts.get(field, 0) + value
        for field in ROAD_CONTINUITY_FAILURE_FIELDS:
            value = (
                _blocking_removed_stale_connection_count(replay)
                if field == "removed_stale_replaced_edge_connection_count"
                else _blocking_removed_stale_boundary_connection_count(replay)
                if field == "removed_stale_boundary_edge_connection_count"
                else _int_count(replay.get(field, 0))
            )
            if value:
                failure_counts[field] = failure_counts.get(field, 0) + value
    if not replay_count:
        status = "skipped"
    elif failure_counts:
        status = "fail"
    else:
        status = "pass"
    return {
        "road_continuity_gate_status": status,
        "road_continuity_replay_count": replay_count,
        "road_continuity_counts": dict(sorted(counts.items())),
        "road_continuity_failure_counts": dict(sorted(failure_counts.items())),
    }


def _final_context_parity_gate(
    *,
    teacher_net_file: Path,
    composite_net_file: Path | None,
    accepted_internal_replays: list[dict[str, object]],
    teacher_join_groups_by_cluster: dict[str, list[str]] | None = None,
    enabled: bool,
    radius_m: float = 100.0,
) -> dict[str, object]:
    if not enabled:
        return {"status": "skipped", "reason": "disabled"}
    if composite_net_file is None or not composite_net_file.exists():
        return {"status": "skipped", "reason": "missing_composite_net_file"}
    if not accepted_internal_replays:
        return {"status": "skipped", "reason": "no_accepted_internal_replays"}
    try:
        teacher_root = ET.parse(teacher_net_file).getroot()
        composite_root = ET.parse(composite_net_file).getroot()
    except (OSError, ET.ParseError) as exc:
        return {"status": "fail", "reason": f"parse_error: {exc}", "reports": []}

    reports: list[dict[str, object]] = []
    split_cluster_repair_seeds: list[dict[str, object]] = []
    seen_split_cluster_repair_seed_keys: set[tuple[str, tuple[str, ...]]] = set()
    for replay in accepted_internal_replays:
        junction_id = str(replay.get("junction_id", ""))
        teacher_junction_id = str(replay.get("teacher_junction_id", ""))
        if not junction_id or not teacher_junction_id:
            reports.append(
                {
                    "status": "fail",
                    "junction_id": junction_id,
                    "teacher_junction_id": teacher_junction_id,
                    "reason": "missing_context_inputs",
                }
            )
            continue
        teacher_context = _local_junction_context_summary(
            teacher_root,
            teacher_junction_id,
            radius_m=radius_m,
        )
        candidate_context = _local_junction_context_summary(
            composite_root,
            junction_id,
            radius_m=radius_m,
        )
        if teacher_context.get("status") != "pass" or candidate_context.get("status") != "pass":
            reports.append(
                {
                    "status": "fail",
                    "junction_id": junction_id,
                    "teacher_junction_id": teacher_junction_id,
                    "reason": "context_extract_failed",
                    "teacher_context": teacher_context,
                    "candidate_context": candidate_context,
                }
            )
            continue
        delta = _context_count_delta(teacher_context, candidate_context)
        split_cluster_residuals = _split_cluster_member_residuals(
            teacher_context,
            candidate_context,
            teacher_join_groups_by_cluster=teacher_join_groups_by_cluster,
        )
        report_repair_seeds = []
        for residual in split_cluster_residuals:
            reference_id = str(residual.get("teacher_cluster_junction_id", ""))
            member_ids = [
                str(member_id)
                for member_id in residual.get("candidate_member_junction_ids", []) or []
                if str(member_id)
            ]
            seed_key = (reference_id, tuple(member_ids))
            if not reference_id or seed_key in seen_split_cluster_repair_seed_keys:
                continue
            seen_split_cluster_repair_seed_keys.add(seed_key)
            seed = {
                "reference_id": reference_id,
                "candidate_member_junction_ids": member_ids,
                "triggering_junction_id": junction_id,
                "triggering_teacher_junction_id": teacher_junction_id,
                "seed_reason": "final_context_split_cluster_residual",
            }
            split_cluster_repair_seeds.append(seed)
            report_repair_seeds.append(seed)
        hard_failures = [
            {"field": field, "count": count}
            for field, count in delta.items()
            if field in {"traffic_light_junction_count", "tl_logic_count"} and count > 0
        ]
        if split_cluster_residuals:
            hard_failures.append(
                {
                    "field": "split_cluster_member_junction_count",
                    "count": len(split_cluster_residuals),
                }
            )
        reports.append(
            {
                "status": "fail" if hard_failures else "pass",
                "junction_id": junction_id,
                "teacher_junction_id": teacher_junction_id,
                "radius_m": radius_m,
                "teacher_context": teacher_context,
                "candidate_context": candidate_context,
                "delta_candidate_minus_teacher": delta,
                "split_cluster_member_residuals": split_cluster_residuals,
                "context_split_cluster_repair_seeds": report_repair_seeds,
                "hard_failures": hard_failures,
            }
        )
    return {
        "status": "pass" if reports and all(report.get("status") == "pass" for report in reports) else "fail",
        "checked_junction_count": len(reports),
        "radius_m": radius_m,
        "hard_failure_fields": [
            "traffic_light_junction_count",
            "tl_logic_count",
            "split_cluster_member_junction_count",
        ],
        "context_split_cluster_repair_seed_count": len(split_cluster_repair_seeds),
        "context_split_cluster_repair_seeds": split_cluster_repair_seeds,
        "reports": reports,
    }


APPROACH_INTEGRITY_FAILURE_FIELDS = {
    "approach_edge_signature_mismatch_count",
    "approach_endpoint_signature_mismatch_count",
    "incoming_vehicle_edge_count",
    "outgoing_vehicle_edge_count",
}


def _semantic_failure_counts(variant_reports: list[dict[str, object]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for report in variant_reports:
        if report.get("expanded_scope_followup_emitted"):
            continue
        gate = report.get("semantic_replay_gate")
        failures = gate.get("failures", []) if isinstance(gate, dict) else []
        for failure in failures:
            if not isinstance(failure, dict):
                continue
            key = f"{failure.get('report', '')}:{failure.get('field', '')}"
            if key != ":":
                counts[key] = counts.get(key, 0) + 1
    return dict(sorted(counts.items()))


def _semantic_layer_gate_counts(variant_reports: list[dict[str, object]]) -> dict[str, dict[str, int]]:
    counts: dict[str, dict[str, int]] = {}
    for report in variant_reports:
        if report.get("expanded_scope_followup_emitted"):
            continue
        layers = report.get("semantic_layer_gates")
        if not isinstance(layers, dict):
            continue
        for layer_name, layer in layers.items():
            if not isinstance(layer, dict):
                continue
            layer_counts = counts.setdefault(str(layer_name), {"pass": 0, "fail": 0, "failure_count": 0})
            status = "pass" if layer.get("status") == "pass" else "fail"
            layer_counts[status] += 1
            try:
                layer_counts["failure_count"] += int(layer.get("failure_count", 0) or 0)
            except (TypeError, ValueError):
                layer_counts["failure_count"] += 1 if status == "fail" else 0
    return {key: counts[key] for key in sorted(counts)}


def _approach_integrity_failure_counts(semantic_failure_counts: dict[str, int]) -> dict[str, int]:
    counts = {
        key: value
        for key, value in semantic_failure_counts.items()
        if key.split(":", 1)[-1] in APPROACH_INTEGRITY_FAILURE_FIELDS
    }
    return dict(sorted(counts.items()))


def _approach_integrity_status(
    *,
    parity_gate_status: str,
    attempted_count: int,
    semantic_failure_counts: dict[str, int],
    approach_failure_counts: dict[str, int],
) -> str:
    if approach_failure_counts:
        return "fail"
    if attempted_count == 0 or parity_gate_status == "blocked":
        return "blocked"
    if semantic_failure_counts or parity_gate_status == "pass":
        return "pass"
    return "blocked"
