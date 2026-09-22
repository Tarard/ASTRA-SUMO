"""Compatibility imports for the junction rebuild domain modules."""

from .junction_rebuild.artifacts import (
    _command_path as _command_path,
    _command_report as _command_report,
    _failure as _failure,
    _queue_candidate_dir as _queue_candidate_dir,
    _queue_path as _queue_path,
    _safe_stage_name as _safe_stage_name,
    _stage_file as _stage_file,
    _variant_exception_report as _variant_exception_report,
    _write_teacher_guided_promotion_gate as _write_teacher_guided_promotion_gate,
    _write_teacher_guided_queue_csv as _write_teacher_guided_queue_csv,
    _write_teacher_guided_report as _write_teacher_guided_report,
)
from .junction_rebuild.boundary_restore import (
    _append_edge_lanes_to_destination_junction as _append_edge_lanes_to_destination_junction,
    _preserve_mapped_boundary_geometry as _preserve_mapped_boundary_geometry,
    _remove_edge_lanes_from_destination_junction as _remove_edge_lanes_from_destination_junction,
    _restore_existing_edge_geometry as _restore_existing_edge_geometry,
    _restore_external_boundary_connections as _restore_external_boundary_connections,
    _restore_joined_split_edge_geometry as _restore_joined_split_edge_geometry,
)
from .junction_rebuild.cases import (
    _attach_candidate_template_context as _attach_candidate_template_context,
    _attach_junction_pattern_delta as _attach_junction_pattern_delta,
    _attach_teacher_pattern_template as _attach_teacher_pattern_template,
    _junction_pattern_delta_by_id as _junction_pattern_delta_by_id,
    _junction_pattern_record_by_id as _junction_pattern_record_by_id,
    _junction_pattern_template_by_key as _junction_pattern_template_by_key,
    _limit_ready_repair_candidates as _limit_ready_repair_candidates,
    _same_id_pattern_cases as _same_id_pattern_cases,
    _same_id_tls_mismatch_cases as _same_id_tls_mismatch_cases,
    _teacher_guided_candidate_sort_key as _teacher_guided_candidate_sort_key,
    _teacher_guided_case_sort_key as _teacher_guided_case_sort_key,
    _teacher_pattern_contexts as _teacher_pattern_contexts,
    _tls_repair_candidates as _tls_repair_candidates,
    _topology_fragmented_non_tls_cases as _topology_fragmented_non_tls_cases,
    _topology_fragmented_tls_cases as _topology_fragmented_tls_cases,
    _turnaround_only_lane_cases as _turnaround_only_lane_cases,
)
from .junction_rebuild.connections import (
    build_tls_connection_repair_variant as build_tls_connection_repair_variant,
    write_teacher_connection_plan as write_teacher_connection_plan,
    write_teacher_vehicle_connection_attrs_net as write_teacher_vehicle_connection_attrs_net,
)
from .junction_rebuild.controller_replay import (
    write_scoped_teacher_tls_cell_replay_net as write_scoped_teacher_tls_cell_replay_net,
    write_shared_teacher_tls_controller_replay_net as write_shared_teacher_tls_controller_replay_net,
)
from .junction_rebuild.edge_mapping import (
    _approach_endpoint_rebuild_plan as _approach_endpoint_rebuild_plan,
    _augment_candidate_edge_map_from_tls_approach_pairs as _augment_candidate_edge_map_from_tls_approach_pairs,
    _edge_map_from_approach_endpoint_rebuild_plan as _edge_map_from_approach_endpoint_rebuild_plan,
    _needed_unmapped_teacher_boundary_edges as _needed_unmapped_teacher_boundary_edges,
    _same_family_continuation_edge_map as _same_family_continuation_edge_map,
    _teacher_boundary_edge_has_target_junction as _teacher_boundary_edge_has_target_junction,
    _teacher_boundary_edge_ids_touching_internal_subgraph as _teacher_boundary_edge_ids_touching_internal_subgraph,
    _teacher_boundary_edge_needs_replay as _teacher_boundary_edge_needs_replay,
    _teacher_candidate_edge_map as _teacher_candidate_edge_map,
    _valid_edge_map as _valid_edge_map,
)
from .junction_rebuild.geometry import (
    _blend_geometry_anchor_at_target as _blend_geometry_anchor_at_target,
    _clone_transformed_boundary_edge as _clone_transformed_boundary_edge,
    _clone_transformed_boundary_junction as _clone_transformed_boundary_junction,
    _clone_transformed_junction as _clone_transformed_junction,
    _clone_transformed_net_element as _clone_transformed_net_element,
    _estimate_linear_lane_transition_shape as _estimate_linear_lane_transition_shape,
    _expand_junction_shape_to_approach_endpoints as _expand_junction_shape_to_approach_endpoints,
    _format_xy as _format_xy,
    _geometry_anchor_junctions_by_id as _geometry_anchor_junctions_by_id,
    _load_geometry_anchor_edges as _load_geometry_anchor_edges,
    _mapped_connection_attrs as _mapped_connection_attrs,
    _mapped_junction_attrs as _mapped_junction_attrs,
    _mapped_spatial_attrs as _mapped_spatial_attrs,
    _model_shape_delta as _model_shape_delta,
    _restore_geometry_anchor_junctions as _restore_geometry_anchor_junctions,
    _shape_points as _shape_points,
    _translate_shape as _translate_shape,
)
from .junction_rebuild.lane_inputs import (
    write_missing_edge_type_patch as write_missing_edge_type_patch,
    write_teacher_endpoint_patch_nodes as write_teacher_endpoint_patch_nodes,
    write_teacher_lane_patch_edges as write_teacher_lane_patch_edges,
)
from .junction_rebuild.network import (
    _connection_key as _connection_key,
    _connection_key_record as _connection_key_record,
    _connection_lane_indices_valid as _connection_lane_indices_valid,
    _connection_link_indices as _connection_link_indices,
    _edge_family_id as _edge_family_id,
    _edge_file_ids as _edge_file_ids,
    _edge_is_pedestrian_only as _edge_is_pedestrian_only,
    _edge_is_vehicle_continuation_candidate as _edge_is_vehicle_continuation_candidate,
    _first_junction_index as _first_junction_index,
    _net_contains_normal_junctions as _net_contains_normal_junctions,
    _net_junction_ids as _net_junction_ids,
    _net_lane_counts as _net_lane_counts,
    _plain_node_ids as _plain_node_ids,
    _signed_edge_family_id as _signed_edge_family_id,
    _split as _split,
    _target_internal_replay_input_file as _target_internal_replay_input_file,
    _touches_target_replay_scope as _touches_target_replay_scope,
)
from .junction_rebuild.parity import (
    _approach_integrity_failure_counts as _approach_integrity_failure_counts,
    _approach_integrity_status as _approach_integrity_status,
    _compare_teacher_models as _compare_teacher_models,
    _final_composite_parity_gate as _final_composite_parity_gate,
    _final_context_parity_gate as _final_context_parity_gate,
    _hybrid_osm_approach_authority_policy as _hybrid_osm_approach_authority_policy,
    _non_target_internal_restore_changed as _non_target_internal_restore_changed,
    _road_continuity_probe_summary as _road_continuity_probe_summary,
    _semantic_failure_counts as _semantic_failure_counts,
    _semantic_layer_gate_counts as _semantic_layer_gate_counts,
    _semantic_layer_gates as _semantic_layer_gates,
    _teacher_guided_semantics_gate as _teacher_guided_semantics_gate,
)
from .junction_rebuild.pedestrians import (
    restore_scoped_pedestrian_internal_semantics_after_normalize as restore_scoped_pedestrian_internal_semantics_after_normalize,
    write_teacher_pedestrian_ring_net as write_teacher_pedestrian_ring_net,
)
from .junction_rebuild.planning import (
    _accepted_target_internal_replay_entry as _accepted_target_internal_replay_entry,
    _candidate_requests_target_internal_replay as _candidate_requests_target_internal_replay,
    _expanded_scope_followup_candidate_for_unsafe_internal_replay as _expanded_scope_followup_candidate_for_unsafe_internal_replay,
    _expanded_scope_skip_entry as _expanded_scope_skip_entry,
    _teacher_guided_repair_candidate as _teacher_guided_repair_candidate,
    build_rebuild_candidate as build_rebuild_candidate,
    build_teacher_guided_repair_queue as build_teacher_guided_repair_queue,
)
from .junction_rebuild.queue import (
    run_teacher_guided_repair_matrix as run_teacher_guided_repair_matrix,
    run_teacher_guided_repair_queue as run_teacher_guided_repair_queue,
)
from .junction_rebuild.replay_plans import (
    build_scoped_teacher_tls_cell_replay_plan as build_scoped_teacher_tls_cell_replay_plan,
    build_shared_teacher_tls_controller_replay_plan as build_shared_teacher_tls_controller_replay_plan,
)
from .junction_rebuild.restoration import (
    _restore_non_target_internal_artifacts as _restore_non_target_internal_artifacts,
    _restore_replayed_geometry_attrs as _restore_replayed_geometry_attrs,
    restore_off_scope_netconvert_artifacts as restore_off_scope_netconvert_artifacts,
)
from .junction_rebuild.scope import (
    _blocking_sequential_overlap_edge_ids as _blocking_sequential_overlap_edge_ids,
    _candidate_connection_mode_scope_ids as _candidate_connection_mode_scope_ids,
    _endpoint_rewrite_old_endpoint_ids as _endpoint_rewrite_old_endpoint_ids,
    _expand_fragmented_tls_join_scope_candidate as _expand_fragmented_tls_join_scope_candidate,
    _join_internal_self_loop_drop_has_witness as _join_internal_self_loop_drop_has_witness,
    _join_patch_joined_node_ids as _join_patch_joined_node_ids,
    _joined_endpoint_self_loop_edge_ids as _joined_endpoint_self_loop_edge_ids,
    _joined_source_node_ids as _joined_source_node_ids,
    _load_teacher_join_groups_by_cluster as _load_teacher_join_groups_by_cluster,
)
from .junction_rebuild.scope_inputs import (
    _prune_plain_node_controlled_inner_edges as _prune_plain_node_controlled_inner_edges,
    _write_join_scope_connection_file as _write_join_scope_connection_file,
    _write_join_scope_tllogic_file as _write_join_scope_tllogic_file,
    _write_joined_endpoint_connection_file as _write_joined_endpoint_connection_file,
    _write_joined_endpoint_edge_file as _write_joined_endpoint_edge_file,
    _write_replay_node_file as _write_replay_node_file,
    write_expanded_scope_plain_inputs as write_expanded_scope_plain_inputs,
)
from .junction_rebuild.shape_repair import (
    write_authorized_junction_shapes_from_reference as write_authorized_junction_shapes_from_reference,
    write_authorized_lane_transition_junction_shapes as write_authorized_lane_transition_junction_shapes,
    write_reanchored_normal_junction_movements as write_reanchored_normal_junction_movements,
)
from .junction_rebuild.signatures import (
    _junction_shape_repair_topology_sha256 as _junction_shape_repair_topology_sha256,
    _junction_shape_tls_sha256 as _junction_shape_tls_sha256,
    _model_tls_id as _model_tls_id,
    _xml_element_semantic_payload as _xml_element_semantic_payload,
)
from .junction_rebuild.target_replay import (
    write_teacher_target_internal_replay_net as write_teacher_target_internal_replay_net,
)
from .junction_rebuild.tls import (
    _demote_teacher_absent_context_tls as _demote_teacher_absent_context_tls,
    _prune_plain_tls_against_teacher as _prune_plain_tls_against_teacher,
    _remove_teacher_non_tls_tllogics as _remove_teacher_non_tls_tllogics,
    _restore_false_traffic_light_junction_types as _restore_false_traffic_light_junction_types,
    restore_teacher_tls_connection_semantics_after_normalize as restore_teacher_tls_connection_semantics_after_normalize,
    write_teacher_tllogic_net as write_teacher_tllogic_net,
)
from .junction_rebuild.variant import (
    build_teacher_guided_junction_variant as build_teacher_guided_junction_variant,
)
