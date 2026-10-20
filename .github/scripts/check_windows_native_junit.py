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
