from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

from torii_sumo.core.connectivity import extract_largest_passenger_component_core
from torii_sumo.core.connection_mode_audit import (
    build_connection_mode_regression_audit,
    build_network_connection_mode_audit,
)
from torii_sumo.core.corridor_simplification import build_corridor_geometry_simplification_variant
from torii_sumo.core.corridor_edit_ledger import (
    build_corridor_edit_ledger,
    materialize_corridor_edit_variant,
    run_corridor_candidate_gates,
)
from torii_sumo.core.junction_aggregation import build_junction_aggregation_variant
from torii_sumo.core.junction_rebuild_candidate import (
    build_teacher_guided_junction_variant,
    run_teacher_guided_repair_queue,
)
from torii_sumo.core.tls_aggregation import build_tls_aggregation_variant
from torii_sumo.core.osm_network import (
    audit_tls,
    audit_tls_multisource,
    build_osm_network,
    build_routeability_probe,
)
from torii_sumo.core.osm_area import resolve_osm_place
from torii_sumo.core.osm_workflow import run_osm_cleanup_workflow
from torii_sumo.core.road_scope import (
    resolve_highway_classes as resolve_highway_classes_from_scope,
)
from torii_sumo.core.routeability_audit import run_routeability_audit
from torii_sumo.core.standard_nema_binding import build_standard_nema_phase_binding
from torii_sumo.core.reference_hierarchy import audit_reference_hierarchy
from torii_sumo.core.reference_join_audit import audit_reference_join_patterns
from torii_sumo.core.reference_scope import audit_reference_scope, build_scope_pruning_variant
from torii_sumo.core.overlapping_junction_audit import audit_overlapping_junctions
from torii_sumo.core.sumo_warning_audit import compare_mapped_tls_warnings
from torii_sumo.core.topology_audit import audit_topology_fragmentation
from torii_sumo.core.teacher_corridor import build_teacher_corridor_comparison
from torii_sumo.core.tls_reference_cleanup import build_tls_reference_cleanup_variant
from torii_sumo.core.workflow_review_html import build_workflow_review_html
from torii_sumo.corridor.audit_pipeline import build_exact_semantic_regression_artifacts
from torii_sumo.corridor.calibration import build_connection_mode_calibration_artifact
from torii_sumo.corridor.enums import TrafficSide


def resolve_highway_classes(value: str | None) -> set[str]:
    resolved = resolve_highway_classes_from_scope(value, default_to_recommended=True)
    assert resolved is not None
    return resolved


def _read_json_report(path: str | None) -> dict[str, Any] | None:
    if not path:
        return None
    report_path = Path(path)
    with report_path.open("r", encoding="utf-8") as handle:
        loaded = json.load(handle)
    if not isinstance(loaded, dict):
        raise ValueError(f"report file must contain a JSON object: {report_path}")
    return loaded


def sumo_osm_build_network(
    bbox: str,
    output_dir: str,
    prefix: str = "sumo_osm_network",
    geo_boundary: str | None = None,
    tls_join_distance_m: float = 35.0,
    source_osm_path: str | None = None,
    clip_source_ways_to_bbox: bool = True,
    highway_classes: str | None = None,
    historical_date: str | None = None,
    overpass_url: str = "https://overpass-api.de/api/interpreter",
    timeout_seconds: float = 240.0,
    max_tile_area_km2: float = 2500.0,
    max_retries: int = 2,
    retry_pause_seconds: float = 5.0,
    netconvert_binary: str = "netconvert",
) -> dict[str, Any]:
    return build_osm_network(
        bbox=bbox,
        geo_boundary=geo_boundary,
        tls_join_distance_m=tls_join_distance_m,
        output_dir=Path(output_dir),
        prefix=prefix,
        source_osm_path=Path(source_osm_path) if source_osm_path else None,
        clip_source_ways_to_bbox=clip_source_ways_to_bbox,
        allowed_highways=resolve_highway_classes(highway_classes),
        historical_date=historical_date,
        overpass_url=overpass_url,
        timeout_seconds=timeout_seconds,
        max_tile_area_km2=max_tile_area_km2,
        max_retries=max_retries,
        retry_pause_seconds=retry_pause_seconds,
        netconvert_binary=netconvert_binary,
    )


def sumo_tls_audit(
    net_file: str,
    output_dir: str,
    prefix: str = "sumo_tls_audit",
    osm_file: str | None = None,
    min_connections: int = 1,
    cluster_radius_m: float = 60.0,
    google_maps_temporal_scope: str = "unspecified",
    google_maps_target_date: str | None = None,
) -> dict[str, Any]:
    return audit_tls(
        net_file=Path(net_file),
        output_dir=Path(output_dir),
        prefix=prefix,
        osm_file=Path(osm_file) if osm_file else None,
        min_connections=min_connections,
        cluster_radius_m=cluster_radius_m,
        google_maps_temporal_scope=google_maps_temporal_scope,
        google_maps_target_date=google_maps_target_date,
    )


def sumo_tls_multisource_review(
    net_file: str,
    output_dir: str,
    prefix: str = "sumo_tls_multisource_review",
    osm_file: str | None = None,
    official_inventory_csv: str | None = None,
    signal_plan_csv: str | None = None,
    field_evidence_csv: str | None = None,
    min_connections: int = 1,
    google_maps_temporal_scope: str = "unspecified",
    google_maps_target_date: str | None = None,
) -> dict[str, Any]:
    return audit_tls_multisource(
        net_file=Path(net_file),
        output_dir=Path(output_dir),
        prefix=prefix,
        osm_file=Path(osm_file) if osm_file else None,
        official_inventory_csv=Path(official_inventory_csv) if official_inventory_csv else None,
        signal_plan_csv=Path(signal_plan_csv) if signal_plan_csv else None,
        field_evidence_csv=Path(field_evidence_csv) if field_evidence_csv else None,
        min_connections=min_connections,
        google_maps_temporal_scope=google_maps_temporal_scope,
        google_maps_target_date=google_maps_target_date,
    )


def sumo_network_routeability_probe(
    net_file: str,
    output_dir: str,
    key_edge_queries: list[dict[str, Any]],
    prefix: str = "routeability_probe",
    seed: int = 42,
    end: int = 180,
) -> dict[str, Any]:
    return build_routeability_probe(
        net_file=Path(net_file),
        output_dir=Path(output_dir),
        prefix=prefix,
        key_edge_queries=key_edge_queries,
        seed=seed,
        end=end,
    )


def sumo_network_routeability_audit(
    net_file: str,
    output_dir: str,
    prefix: str = "routeability_audit",
    vehicle_count: int = 100,
    seed: int = 42,
    initial_end: int = 300,
    max_end: int = 2400,
    timeout_seconds: float = 240.0,
) -> dict[str, Any]:
    return run_routeability_audit(
        net_file=Path(net_file),
        output_dir=Path(output_dir),
        prefix=prefix,
        vehicle_count=vehicle_count,
        seed=seed,
        initial_end=initial_end,
        max_end=max_end,
        timeout_seconds=timeout_seconds,
    )


def sumo_network_topology_audit(
    net_file: str,
    output_dir: str,
    prefix: str = "topology_audit",
    cluster_radius_m: float = 30.0,
    min_cluster_nodes: int = 3,
    osm_file: str | None = None,
) -> dict[str, Any]:
    return audit_topology_fragmentation(
        net_file=Path(net_file),
        output_dir=Path(output_dir),
        prefix=prefix,
        cluster_radius_m=cluster_radius_m,
        min_cluster_nodes=min_cluster_nodes,
        osm_file=Path(osm_file) if osm_file else None,
    )


def sumo_network_overlapping_junction_audit(
    net_file: str,
    output_dir: str,
    prefix: str = "overlapping_junction_audit",
    overlap_radius_m: float = 12.0,
    short_edge_length_m: float = 20.0,
    min_group_nodes: int = 2,
    reference_join_audit_report_file: str | None = None,
) -> dict[str, Any]:
    return audit_overlapping_junctions(
        net_file=Path(net_file),
        output_dir=Path(output_dir),
        prefix=prefix,
        overlap_radius_m=overlap_radius_m,
        short_edge_length_m=short_edge_length_m,
        min_group_nodes=min_group_nodes,
        reference_join_audit_report=_read_json_report(reference_join_audit_report_file),
    )


def sumo_network_reference_join_audit(
    reference_net_file: str,
    candidate_net_file: str,
    output_dir: str,
    prefix: str = "reference_join_audit",
    reference_cluster_prefix: str = "cluster_",
    candidate_cluster_radius_m: float = 30.0,
    candidate_min_cluster_nodes: int = 3,
    match_radius_m: float = 45.0,
) -> dict[str, Any]:
    return audit_reference_join_patterns(
        reference_net_file=Path(reference_net_file),
        candidate_net_file=Path(candidate_net_file),
        output_dir=Path(output_dir),
        prefix=prefix,
        reference_cluster_prefix=reference_cluster_prefix,
        candidate_cluster_radius_m=candidate_cluster_radius_m,
        candidate_min_cluster_nodes=candidate_min_cluster_nodes,
        match_radius_m=match_radius_m,
    )


def sumo_network_reference_hierarchy_audit(
    reference_net_file: str,
    candidate_net_file: str,
    output_dir: str,
    prefix: str = "reference_hierarchy_audit",
    match_distance_m: float = 35.0,
    oversplit_length_ratio: float = 0.6,
    min_extra_edges: int = 10,
    resolve_equivalent_fragmentation: bool = False,
) -> dict[str, Any]:
    return audit_reference_hierarchy(
        reference_net_file=Path(reference_net_file),
        candidate_net_file=Path(candidate_net_file),
        output_dir=Path(output_dir),
        prefix=prefix,
        match_distance_m=match_distance_m,
        oversplit_length_ratio=oversplit_length_ratio,
        min_extra_edges=min_extra_edges,
        resolve_equivalent_fragmentation=resolve_equivalent_fragmentation,
    )


def sumo_network_junction_aggregation_variant(
    net_file: str,
    output_dir: str,
    prefix: str = "junction_aggregation",
    topology_audit_report_file: str | None = None,
    reference_join_audit_report_file: str | None = None,
    overlapping_junction_audit_report_file: str | None = None,
    join_dist_m: float = 30.0,
    timeout_seconds: float = 240.0,
) -> dict[str, Any]:
    return build_junction_aggregation_variant(
        net_file=Path(net_file),
        output_dir=Path(output_dir),
        prefix=prefix,
        topology_audit_report=_read_json_report(topology_audit_report_file),
        reference_join_audit_report=_read_json_report(reference_join_audit_report_file),
        overlapping_junction_audit_report=_read_json_report(overlapping_junction_audit_report_file),
        join_dist_m=join_dist_m,
        timeout_seconds=timeout_seconds,
    )


def sumo_network_reference_scope_audit(
    reference_net_file: str,
    candidate_net_file: str,
    output_dir: str,
    prefix: str = "reference_scope",
    overrepresentation_ratio: float = 1.5,
    min_extra_edges: int = 10,
    max_prune_edge_length_m: float = 80.0,
) -> dict[str, Any]:
    return audit_reference_scope(
        reference_net_file=Path(reference_net_file),
        candidate_net_file=Path(candidate_net_file),
        output_dir=Path(output_dir),
        prefix=prefix,
        overrepresentation_ratio=overrepresentation_ratio,
        min_extra_edges=min_extra_edges,
        max_prune_edge_length_m=max_prune_edge_length_m,
    )


def sumo_network_scope_pruning_variant(
    net_file: str,
    reference_scope_report_file: str,
    output_dir: str,
    prefix: str = "scope_pruning",
    timeout_seconds: float = 240.0,
) -> dict[str, Any]:
    return build_scope_pruning_variant(
        net_file=Path(net_file),
        reference_scope_report=_read_json_report(reference_scope_report_file) or {},
        output_dir=Path(output_dir),
        prefix=prefix,
        timeout_seconds=timeout_seconds,
    )


def sumo_network_corridor_geometry_simplification_variant(
    net_file: str,
    output_dir: str,
    reference_net_file: str | None = None,
    prefix: str = "corridor_geometry_simplification",
    max_micro_edge_length_m: float = 1.0,
    timeout_seconds: float = 240.0,
) -> dict[str, Any]:
    return build_corridor_geometry_simplification_variant(
        net_file=Path(net_file),
        reference_net_file=Path(reference_net_file) if reference_net_file else None,
        output_dir=Path(output_dir),
        prefix=prefix,
        max_micro_edge_length_m=max_micro_edge_length_m,
        timeout_seconds=timeout_seconds,
    )


def sumo_network_corridor_edit_ledger(
    net_file: str,
    output_dir: str,
    operations: list[dict[str, Any]] | None = None,
    reference_net_file: str | None = None,
    osm_file: str | None = None,
    prefix: str = "corridor_edit_ledger",
    include_auto_proposals: bool = True,
    map_temporal_scope: str = "unspecified",
    map_target_date: str | None = None,
) -> dict[str, Any]:
    """Create a reversible, review-only human-style corridor edit ledger."""
    return build_corridor_edit_ledger(
        net_file=Path(net_file),
        output_dir=Path(output_dir),
        operations=operations,
        reference_net_file=Path(reference_net_file) if reference_net_file else None,
        osm_file=Path(osm_file) if osm_file else None,
        prefix=prefix,
        include_auto_proposals=include_auto_proposals,
        map_temporal_scope=map_temporal_scope,
        map_target_date=map_target_date,
    )


def sumo_network_corridor_candidate_gates(
    source_net_file: str,
    candidate_net_file: str,
    output_dir: str,
    materialization_report_file: str,
    review_decision_file: str | None = None,
    osm_file: str | None = None,
    prefix: str = "corridor_candidate_gates",
    sumo_binary: str = "sumo",
    vehicle_count: int = 20,
    initial_end: int = 300,
    max_end: int = 1200,
    timeout_seconds: float = 240.0,
) -> dict[str, Any]:
    """Run promotion gates from persisted, hash-bound materialization and review evidence."""
    materialization_report = _read_json_report(materialization_report_file)
    if materialization_report is not None:
        materialization_report["_materialization_report_file"] = str(
            Path(materialization_report_file).resolve()
        )
    review_decision = _read_json_report(review_decision_file)
    if review_decision is not None and review_decision_file:
        review_decision["_review_decision_file"] = str(Path(review_decision_file).resolve())
    return run_corridor_candidate_gates(
        source_net_file=Path(source_net_file),
        candidate_net_file=Path(candidate_net_file),
        output_dir=Path(output_dir),
        materialization_report=materialization_report,
        review_decision=review_decision,
        osm_file=Path(osm_file) if osm_file else None,
        prefix=prefix,
        sumo_binary=sumo_binary,
        vehicle_count=vehicle_count,
        initial_end=initial_end,
        max_end=max_end,
        timeout_seconds=timeout_seconds,
    )


def sumo_network_corridor_materialize_variant(
    ledger_file: str,
    output_dir: str,
    prefix: str = "corridor_materialized_variant",
    netconvert_binary: str = "netconvert",
    timeout_seconds: float = 240.0,
    map_temporal_scope: str | None = None,
    map_target_date: str | None = None,
) -> dict[str, Any]:
    """Materialize explicitly accepted destructive or additive ledger operations."""
    return materialize_corridor_edit_variant(
        ledger_file=Path(ledger_file),
        output_dir=Path(output_dir),
        prefix=prefix,
        netconvert_binary=netconvert_binary,
        timeout_seconds=timeout_seconds,
        map_temporal_scope=map_temporal_scope,
        map_target_date=map_target_date,
    )


def sumo_network_teacher_corridor_comparison(
    teacher_net_file: str,
    candidate_net_file: str,
    junction_id: str,
    output_dir: str,
    prefix: str = "teacher_corridor",
    map_temporal_scope: str = "current",
    map_target_date: str | None = None,
    osm_file: str | None = None,
    evidence_radius_m: float = 35.0,
) -> dict[str, Any]:
    """Build one candidate-bound human-modeling comparison against a cleaned teacher cell."""
    return build_teacher_corridor_comparison(
        teacher_net_file=Path(teacher_net_file),
        candidate_net_file=Path(candidate_net_file),
        junction_id=junction_id,
        output_dir=Path(output_dir),
        prefix=prefix,
        map_temporal_scope=map_temporal_scope,
        map_target_date=map_target_date,
        osm_file=Path(osm_file) if osm_file else None,
        evidence_radius_m=evidence_radius_m,
    )


def sumo_network_tls_reference_cleanup_variant(
    net_file: str,
    output_dir: str,
    prefix: str = "tls_reference_cleanup",
) -> dict[str, Any]:
    """Create a minimal candidate for narrowly provable stale TLS attributes."""
    return build_tls_reference_cleanup_variant(
        Path(net_file),
        output_dir=Path(output_dir),
        prefix=prefix,
    )


def sumo_network_connection_mode_audit(
    net_file: str,
    output_dir: str,
    prefix: str = "connection_mode_audit",
    junction_ids: list[str] | None = None,
    traffic_side: str = "auto",
    endpoint_tolerance_m: float = 2.0,
    normalized_lane_rank_tolerance: float = 0.5,
) -> dict[str, Any]:
    """Audit lane connections, internal paths, requests, foes, and TLS bindings in code."""
    return build_network_connection_mode_audit(
        Path(net_file),
        output_dir=Path(output_dir),
        prefix=prefix,
        junction_ids=junction_ids,
        traffic_side=traffic_side,
        endpoint_tolerance_m=endpoint_tolerance_m,
        normalized_lane_rank_tolerance=normalized_lane_rank_tolerance,
    )


def sumo_network_connection_mode_calibration(
    net_file: str,
    output_dir: str,
    traffic_side: str,
    prefix: str = "connection_mode_calibration",
) -> dict[str, Any]:
    """Calibrate endpoint tolerance from an immutable source baseline."""

    side = _required_traffic_side(traffic_side)
    output_file = Path(output_dir) / f"{prefix}.json"
    calibration = build_connection_mode_calibration_artifact(
        Path(net_file),
        output_file=output_file,
        traffic_side=side,
    )
    return calibration.model_dump(mode="json", by_alias=True)


def sumo_network_exact_semantic_regression_audit(
    source_net_file: str,
    candidate_net_file: str,
    output_dir: str,
    toolchain_lock_file: str,
    traffic_side: str,
    target_source_junction_ids: list[str],
    target_candidate_junction_ids: list[str],
    guard_source_junction_ids: list[str] | None = None,
    guard_candidate_junction_ids: list[str] | None = None,
    endpoint_tolerance_m: float | None = None,
    normalized_lane_rank_tolerance: float | None = None,
    calibration_file: str | None = None,
    prefix: str = "exact_semantic_regression",
) -> dict[str, Any]:
    """Run the stable-entity, exact-witness, independent-safety regression gate."""

    return build_exact_semantic_regression_artifacts(
        Path(source_net_file),
        Path(candidate_net_file),
        output_dir=Path(output_dir),
        toolchain_lock_file=Path(toolchain_lock_file),
        traffic_side=_required_traffic_side(traffic_side),
        target_source_junction_ids=target_source_junction_ids,
        target_candidate_junction_ids=target_candidate_junction_ids,
        guard_source_junction_ids=guard_source_junction_ids or (),
        guard_candidate_junction_ids=guard_candidate_junction_ids or (),
        endpoint_tolerance_m=endpoint_tolerance_m,
        normalized_lane_rank_tolerance=normalized_lane_rank_tolerance,
        calibration_file=Path(calibration_file) if calibration_file else None,
        prefix=prefix,
    )


def _required_traffic_side(value: str) -> TrafficSide:
    try:
        traffic_side = TrafficSide(str(value).strip().casefold())
    except ValueError as exc:
        raise ValueError("traffic_side must be explicitly set to right or left") from exc
    if traffic_side is TrafficSide.UNKNOWN:
        raise ValueError("traffic_side must be explicitly set to right or left")
    return traffic_side


def sumo_network_connection_mode_regression_audit(
    source_net_file: str,
    candidate_net_file: str,
    output_dir: str,
    prefix: str = "connection_mode_regression",
    target_source_junction_ids: list[str] | None = None,
    target_candidate_junction_ids: list[str] | None = None,
    traffic_side: str = "auto",
    endpoint_tolerance_m: float = 2.0,
    normalized_lane_rank_tolerance: float = 0.5,
) -> dict[str, Any]:
    """Compare source and candidate Connection Mode graphs and fail closed."""
    return build_connection_mode_regression_audit(
        Path(source_net_file),
        Path(candidate_net_file),
        output_dir=Path(output_dir),
        prefix=prefix,
        target_source_junction_ids=target_source_junction_ids or (),
        target_candidate_junction_ids=target_candidate_junction_ids or (),
        traffic_side=traffic_side,
        endpoint_tolerance_m=endpoint_tolerance_m,
        normalized_lane_rank_tolerance=normalized_lane_rank_tolerance,
    )


def sumo_network_standard_nema_phase_binding(
    net_file: str,
    output_dir: str,
    junction_id: str | None = None,
    prefix: str = "standard_nema_binding",
    run_runtime_checks: bool = True,
    run_routeability: bool = True,
    routeability_vehicle_count: int = 12,
    netconvert_binary: str | None = None,
    sumo_binary: str | None = None,
    random_trips_script: str | None = None,
    timeout_seconds: float = 240.0,
) -> dict[str, Any]:
    """Connection-audit standard TLS junctions or materialize one reversible NEMA candidate."""
    return build_standard_nema_phase_binding(
        Path(net_file),
        output_dir=Path(output_dir),
        prefix=prefix,
        junction_id=junction_id,
        run_runtime_checks=run_runtime_checks,
        run_routeability=run_routeability,
        routeability_vehicle_count=routeability_vehicle_count,
        netconvert_binary=netconvert_binary,
        sumo_binary=sumo_binary,
        random_trips_script=random_trips_script,
        timeout_seconds=timeout_seconds,
    )


def sumo_network_tls_aggregation_variant(
    net_file: str,
    tls_audit_report_file: str,
    output_dir: str,
    prefix: str = "tls_aggregation",
    timeout_seconds: float = 240.0,
) -> dict[str, Any]:
    return build_tls_aggregation_variant(
        net_file=Path(net_file),
        tls_audit_report=_read_json_report(tls_audit_report_file) or {},
        output_dir=Path(output_dir),
        prefix=prefix,
        timeout_seconds=timeout_seconds,
    )


def sumo_network_tls_warning_parity(
    teacher_sumo_load_report_file: str,
    candidate_sumo_load_report_file: str,
    tls_id_map: dict[str, str],
    output_dir: str,
    prefix: str = "tls_warning_parity",
) -> dict[str, Any]:
    teacher_report = _read_json_report(teacher_sumo_load_report_file) or {}
    candidate_report = _read_json_report(candidate_sumo_load_report_file) or {}
    report = compare_mapped_tls_warnings(
        str(teacher_report.get("stderr_tail", "")),
        str(candidate_report.get("stderr_tail", "")),
        tls_id_map,
    )
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    warning_parity_file = output_path / f"{prefix}_sumo_tls_warning_parity.json"
    report.update(
        {
            "teacher_sumo_load_report_file": str(Path(teacher_sumo_load_report_file)),
            "candidate_sumo_load_report_file": str(Path(candidate_sumo_load_report_file)),
            "warning_parity_file": str(warning_parity_file),
        }
    )
    warning_parity_file.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    return report


def sumo_network_teacher_guided_junction_variant(
    raw_node_file: str,
    raw_edge_file: str,
    raw_connection_file: str,
    teacher_net_file: str,
    candidate_net_file: str,
    junction_id: str,
    output_dir: str,
    edge_map: dict[str, str],
    prefix: str = "teacher_guided_junction",
    teacher_junction_id: str | None = None,
    raw_type_file: str | None = None,
    raw_tllogic_file: str | None = None,
    crossing_edge_overrides: dict[str, str | list[str]] | None = None,
    source_conflict_core_node_ids: list[str] | None = None,
    replay_target_internal_subgraph: bool = True,
    preserve_teacher_lane_shapes: bool = True,
    netconvert_binary: str = "netconvert",
    sumo_binary: str = "sumo",
    timeout_seconds: float = 240.0,
) -> dict[str, Any]:
    return build_teacher_guided_junction_variant(
        raw_node_file=Path(raw_node_file),
        raw_edge_file=Path(raw_edge_file),
        raw_connection_file=Path(raw_connection_file),
        raw_type_file=Path(raw_type_file) if raw_type_file else None,
        raw_tllogic_file=Path(raw_tllogic_file) if raw_tllogic_file else None,
        teacher_net_file=Path(teacher_net_file),
        candidate_net_file=Path(candidate_net_file),
        junction_id=junction_id,
        output_dir=Path(output_dir),
        edge_map=edge_map,
        prefix=prefix,
        teacher_junction_id=teacher_junction_id,
        crossing_edge_overrides=crossing_edge_overrides,
        source_conflict_core_node_ids=source_conflict_core_node_ids,
        replay_target_internal_subgraph=replay_target_internal_subgraph,
        preserve_teacher_lane_shapes=preserve_teacher_lane_shapes,
        netconvert_binary=netconvert_binary,
        sumo_binary=sumo_binary,
        timeout_seconds=timeout_seconds,
    )


def sumo_network_teacher_guided_repair_queue(
    queue_report_file: str,
    raw_node_file: str,
    raw_edge_file: str,
    raw_connection_file: str,
    output_dir: str,
    prefix: str = "teacher_guided_repair",
    raw_type_file: str | None = None,
    raw_tllogic_file: str | None = None,
    replay_target_internal_subgraph: bool = True,
    expand_fragmented_tls_join_scope: bool = False,
    max_ready_candidates: int | None = None,
    netconvert_binary: str = "netconvert",
    sumo_binary: str = "sumo",
    timeout_seconds: float = 240.0,
) -> dict[str, Any]:
    queue_report_path = Path(queue_report_file)
    return run_teacher_guided_repair_queue(
        queue_report=_read_json_report(str(queue_report_path)) or {},
        raw_node_file=Path(raw_node_file),
        raw_edge_file=Path(raw_edge_file),
        raw_connection_file=Path(raw_connection_file),
        raw_type_file=Path(raw_type_file) if raw_type_file else None,
        raw_tllogic_file=Path(raw_tllogic_file) if raw_tllogic_file else None,
        output_dir=Path(output_dir),
        prefix=prefix,
        queue_base_dir=queue_report_path.resolve().parent,
        replay_target_internal_subgraph=replay_target_internal_subgraph,
        expand_fragmented_tls_join_scope=expand_fragmented_tls_join_scope,
        max_ready_candidates=max_ready_candidates,
        netconvert_binary=netconvert_binary,
        sumo_binary=sumo_binary,
        timeout_seconds=timeout_seconds,
        connection_mode_regression_builder=build_connection_mode_regression_audit,
    )


def sumo_network_review_html(
    output_dir: str,
    net_file: str | None = None,
    title: str = "SUMO Network Review",
    claim_status: str = "diagnostic-demo",
    raw_net_file: str | None = None,
    connected_core_file: str | None = None,
    tls_review_file: str | None = None,
    topology_audit_report_file: str | None = None,
    junction_aggregation_report_file: str | None = None,
    routeability_audit_report_file: str | None = None,
) -> dict[str, Any]:
    return build_workflow_review_html(
        output_dir=Path(output_dir),
        prefix="workflow_review",
        title=title,
        claim_status=claim_status,
        net_file=Path(net_file) if net_file else None,
        raw_net_file=Path(raw_net_file) if raw_net_file else None,
        connected_core_file=Path(connected_core_file) if connected_core_file else None,
        tls_review_file=Path(tls_review_file) if tls_review_file else None,
        topology_audit_report=_read_json_report(topology_audit_report_file),
        topology_audit_report_file=Path(topology_audit_report_file) if topology_audit_report_file else None,
        junction_aggregation_report=_read_json_report(junction_aggregation_report_file),
        junction_aggregation_report_file=Path(junction_aggregation_report_file)
        if junction_aggregation_report_file
        else None,
        routeability_audit_report=_read_json_report(routeability_audit_report_file),
        routeability_audit_report_file=Path(routeability_audit_report_file)
        if routeability_audit_report_file
        else None,
    )


def sumo_network_connected_core(
    net_file: str,
    output_dir: str,
    prefix: str = "sumo_network",
    timeout_seconds: float = 240.0,
) -> dict[str, Any]:
    return extract_largest_passenger_component_core(
        Path(net_file),
        output_dir=Path(output_dir),
        prefix=prefix,
        timeout_seconds=timeout_seconds,
    )


def sumo_osm_resolve_place(
    place_name: str,
    limit: int = 1,
    nominatim_url: str = "https://nominatim.openstreetmap.org/search",
    timeout_seconds: float = 30.0,
) -> dict[str, Any]:
    return resolve_osm_place(
        place_name,
        limit=limit,
        nominatim_url=nominatim_url,
        timeout_seconds=timeout_seconds,
    )


def sumo_osm_cleanup_workflow(
    output_dir: str,
    bbox: str,
    profile: Literal["standard", "reference_matched"] = "standard",
    source_osm_path: str | None = None,
    traffic_layers: str | None = None,
    reference_net_file: str | None = None,
    timeout_seconds: float = 240.0,
) -> dict[str, Any]:
    return run_osm_cleanup_workflow(
        output_dir=Path(output_dir),
        bbox=bbox,
        profile=profile,
        source_osm_path=Path(source_osm_path) if source_osm_path else None,
        traffic_layers=traffic_layers,
        reference_net_file=Path(reference_net_file) if reference_net_file else None,
        timeout_seconds=timeout_seconds,
    )
