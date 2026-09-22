"""Contract tests for the ``junction_rebuild_tail`` compatibility extraction."""

from __future__ import annotations

import xml.etree.ElementTree as ET

from torii_sumo.core import junction_rebuild_candidate as candidate_module
from torii_sumo.core import junction_rebuild_tail as tail_module


def test_original_module_reexports_public_tail_api() -> None:
    public_functions = (
        "build_rebuild_candidate",
        "build_scoped_teacher_tls_cell_replay_plan",
        "build_shared_teacher_tls_controller_replay_plan",
        "build_tls_connection_repair_variant",
        "restore_off_scope_netconvert_artifacts",
        "restore_scoped_pedestrian_internal_semantics_after_normalize",
        "restore_teacher_tls_connection_semantics_after_normalize",
        "write_expanded_scope_plain_inputs",
        "write_missing_edge_type_patch",
        "write_teacher_connection_plan",
        "write_teacher_endpoint_patch_nodes",
        "write_teacher_lane_patch_edges",
        "write_teacher_pedestrian_ring_net",
        "write_teacher_tllogic_net",
        "write_teacher_vehicle_connection_attrs_net",
    )
    assert all(
        getattr(candidate_module, name) is getattr(tail_module, name)
        for name in public_functions
    )


def test_tail_leaf_helpers_keep_behavior() -> None:
    assert tail_module._split("  a   b ") == ["a", "b"]
    assert tail_module._format_xy(1.0) == "1.00"
    assert tail_module._approaches(
        {"approaches": {"in": [{"edge_id": "e1"}, 1]}},
        "in",
    ) == [{"edge_id": "e1"}]

    connection = ET.Element(
        "connection",
        {"from": "a", "to": "b", "fromLane": "1", "toLane": "2"},
    )
    assert tail_module._connection_key(connection) == ("a", "b", "1", "2")
    assert tail_module._connection_key_record(("a", "b", "1", "2")) == {
        "from": "a",
        "to": "b",
        "fromLane": "1",
        "toLane": "2",
    }
    assert tail_module._controlled_link_signature_group(["s", "s", "t"]) == "2x s || 1x t"
    assert tail_module._failure("boom") == {
        "status": "fail",
        "claim_status": "construction-invalid",
        "error": "boom",
    }


def test_third_slice_shape_helpers_keep_behavior() -> None:
    assert tail_module._shape_points("0,0 1,1") == [(0.0, 0.0), (1.0, 1.0)]
    assert tail_module._shape_endpoints("0,0 1,1 2,2") == ((0.0, 0.0), (2.0, 2.0))
    assert tail_module._shape_endpoints("") is None
    assert tail_module._join_shape_text("0,0 1,1", "1,1 2,2") == "0,0 1,1 2,2"
    assert tail_module._join_shape_text("0,0 1,1", "") == "0,0 1,1"
