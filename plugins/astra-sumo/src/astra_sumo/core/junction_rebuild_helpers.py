"""Compatibility imports for the junction rebuild domain modules."""

from .junction_rebuild.artifacts import (
    _command_path as _command_path,
    _queue_path as _queue_path,
    _stage_file as _stage_file,
    _write_teacher_guided_promotion_gate as _write_teacher_guided_promotion_gate,
    _write_teacher_guided_queue_csv as _write_teacher_guided_queue_csv,
)
from .junction_rebuild.boundary_restore import (
    BOUNDARY_EDGE_OPERATIONAL_ATTRS as BOUNDARY_EDGE_OPERATIONAL_ATTRS,
    BOUNDARY_LANE_OPERATIONAL_ATTRS as BOUNDARY_LANE_OPERATIONAL_ATTRS,
    _preserve_boundary_operational_attributes as _preserve_boundary_operational_attributes,
)
from .junction_rebuild.cases import (
    _attach_candidate_template_context as _attach_candidate_template_context,
    _attach_teacher_pattern_template as _attach_teacher_pattern_template,
    _junction_pattern_delta_by_id as _junction_pattern_delta_by_id,
    _junction_pattern_delta_keys as _junction_pattern_delta_keys,
    _junction_pattern_record_by_id as _junction_pattern_record_by_id,
    _junction_pattern_template_by_key as _junction_pattern_template_by_key,
    _limit_ready_repair_candidates as _limit_ready_repair_candidates,
    _netedit_review_actions as _netedit_review_actions,
    _teacher_guided_candidate_sort_key as _teacher_guided_candidate_sort_key,
    _teacher_junction_has_tls as _teacher_junction_has_tls,
    _teacher_pattern_contexts as _teacher_pattern_contexts,
    _teacher_pattern_metric_is_positive as _teacher_pattern_metric_is_positive,
    _teacher_template_count_for_case as _teacher_template_count_for_case,
    _tls_repair_actions as _tls_repair_actions,
)
from .junction_rebuild.edge_mapping import (
    _candidate_edge_by_exact_or_unsplit_id as _candidate_edge_by_exact_or_unsplit_id,
    _missing_teacher_edge_endpoint_ids as _missing_teacher_edge_endpoint_ids,
    _prefer_existing_exact_edge_ids as _prefer_existing_exact_edge_ids,
    _teacher_boundary_edge_has_target_junction as _teacher_boundary_edge_has_target_junction,
    _valid_edge_map as _valid_edge_map,
)
from .junction_rebuild.geometry import (
    _convex_hull as _convex_hull,
    _geometry_anchor_junctions_by_id as _geometry_anchor_junctions_by_id,
    _joined_lane_length as _joined_lane_length,
    _junction_within_radius as _junction_within_radius,
    _junction_xy as _junction_xy,
    _load_geometry_anchor_edges as _load_geometry_anchor_edges,
    _polygon_area as _polygon_area,
    _primary_edge_shape as _primary_edge_shape,
    _restore_geometry_anchor_junctions as _restore_geometry_anchor_junctions,
    _teacher_to_candidate_delta as _teacher_to_candidate_delta,
)
from .junction_rebuild.network import (
    TURNAROUND_DIR as TURNAROUND_DIR,
    _approach_edges as _approach_edges,
    _candidate_lane_counts as _candidate_lane_counts,
    _connection_edges_are_adjacent as _connection_edges_are_adjacent,
    _connection_lane_indices_valid as _connection_lane_indices_valid,
    _edge_drop_requires_review as _edge_drop_requires_review,
    _edge_family_id as _edge_family_id,
    _edge_file_ids as _edge_file_ids,
    _edge_file_lane_counts as _edge_file_lane_counts,
    _edge_is_pedestrian_only as _edge_is_pedestrian_only,
    _edge_lane_count as _edge_lane_count,
    _edge_type_signature as _edge_type_signature,
    _first_junction_index as _first_junction_index,
    _int_count as _int_count,
    _is_turnaround_connection as _is_turnaround_connection,
    _lanes_by_index as _lanes_by_index,
    _map_internal_ref as _map_internal_ref,
    _net_contains_normal_junctions as _net_contains_normal_junctions,
    _net_lane_counts as _net_lane_counts,
    _opposite_direction_edge_id as _opposite_direction_edge_id,
    _plain_crossing_node_id as _plain_crossing_node_id,
    _plain_edge_endpoints as _plain_edge_endpoints,
    _plain_node_ids as _plain_node_ids,
    _real_junction_ids as _real_junction_ids,
    _report_used_unrestored_normalized_replay as _report_used_unrestored_normalized_replay,
    _should_emit as _should_emit,
    _signed_edge_family_id as _signed_edge_family_id,
    _stable_digest as _stable_digest,
    _string_list as _string_list,
    _touches_other_internal_owner as _touches_other_internal_owner,
    _touches_target_internal_owner as _touches_target_internal_owner,
    _touches_target_internal_subgraph as _touches_target_internal_subgraph,
    _write_connections as _write_connections,
)
from .junction_rebuild.parity import (
    APPROACH_INTEGRITY_FAILURE_FIELDS as APPROACH_INTEGRITY_FAILURE_FIELDS,
    _approach_integrity_failure_counts as _approach_integrity_failure_counts,
    _approach_integrity_status as _approach_integrity_status,
    _semantic_failure_counts as _semantic_failure_counts,
    _semantic_layer_gate_counts as _semantic_layer_gate_counts,
)
from .junction_rebuild.scope import (
    _blocking_sequential_overlap_edge_ids as _blocking_sequential_overlap_edge_ids,
    _candidate_connection_mode_scope_ids as _candidate_connection_mode_scope_ids,
    _conservative_join_node_ids as _conservative_join_node_ids,
    _endpoint_rewrites as _endpoint_rewrites,
    _expand_fragmented_tls_join_scope_candidate as _expand_fragmented_tls_join_scope_candidate,
    _expanded_rebuild_scope as _expanded_rebuild_scope,
    _normalize_joined_junction_ids as _normalize_joined_junction_ids,
    _split_cluster_member_residuals as _split_cluster_member_residuals,
    _sumo_cluster_member_ids as _sumo_cluster_member_ids,
    _sumo_joined_cluster_id as _sumo_joined_cluster_id,
)
from .junction_rebuild.scope_inputs import (
    _prune_plain_node_controlled_inner_edges as _prune_plain_node_controlled_inner_edges,
    _write_join_scope_connection_file as _write_join_scope_connection_file,
    _write_join_scope_tllogic_file as _write_join_scope_tllogic_file,
)
from .junction_rebuild.tls import (
    _prune_plain_tls_against_teacher as _prune_plain_tls_against_teacher,
)
