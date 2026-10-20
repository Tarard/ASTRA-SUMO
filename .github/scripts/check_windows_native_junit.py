"""Require the critical native cases in the complete Windows suite."""

import json
from pathlib import Path
import sys
import xml.etree.ElementTree as ET


REQUIRED = {
    "test_native_isolated_proposals": 3,
    "test_native_micro_proposals": 6,
    "test_legacy_native_lists_bind_scope": 2,
    "test_legacy_local_output_does_not_replace": 1,
    "test_native_spur_deletion": 1,
    "test_windows_native_fatal_exit_is_bounded": 20,
    "test_native_road_shape_edit_preserves": 1,
    "test_hamburg_branching_pair_does_not_report_a_successful_merge": 2,
    "test_existing_edge_deletion_uses_the_shared_exterior_protection": 2,
    "test_native_additions_use_local_context_and_replay_bound_plain_inputs": 4,
    "test_hamburg_added_sidewalk_reaches_its_walking_area": 2,
    "test_native_type_adds_declared_sidewalk_and_bicycle_lanes": 1,
    "test_standalone_simplifier_uses_shared_local_conversion_and_keeps_remote_roads": 1,
    "test_native_merged_od_retains_original_requests_and_physical_positions": 5,
    "test_native_unresolved_endpoint_does_not_hide_other_route_results": 2,
    "test_real_bus_roads_preserve_source_access_and_reject_delivery_and_bicycle": 1,
    "test_native_removed_endpoints_do_not_abort_remaining_requests": 3,
    "test_native_retained_road_defaults_keep_relative_placement": 2,
    "test_native_lane_correspondence_keeps_retained_and_removed_endpoint_requests": 2,
    "test_native_lane_count_keeps_unique_turn_and_serves_new_lane": 2,
    "test_native_hierarchy_scope_keeps_protected_turns_and_removes_local_tail": 2,
    "test_native_ring1_road_selection": 1,
    "test_native_ring1_official_approach_demo": 1,
    "test_native_ring1_approach_generation_from_frozen_sources": 1,
}


def check_native_execution(path):
    cases = list(ET.parse(path).getroot().iter("testcase"))
    counts = {}
    for prefix, minimum in REQUIRED.items():
        selected = [row for row in cases if row.get("name", "").startswith(prefix)]
        if len(selected) < minimum:
            raise ValueError(f"Missing critical native cases: {prefix} requires {minimum}, found {len(selected)}.")
        for row in selected:
            if any(row.find(kind) is not None for kind in ("skipped", "failure", "error")):
                raise ValueError(f"Critical native case did not pass: {row.get('name')}.")
        counts[prefix] = len(selected)
    return counts


if __name__ == "__main__":
    print(json.dumps(check_native_execution(Path(sys.argv[1])), sort_keys=True))
