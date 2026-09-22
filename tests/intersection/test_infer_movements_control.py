from pathlib import Path

from torii_sumo.intersection.infer_approaches import infer_approaches
from torii_sumo.intersection.infer_control import infer_control_model
from torii_sumo.intersection.infer_core import infer_intersection_core
from torii_sumo.intersection.infer_movements import infer_movement_matrix
from torii_sumo.intersection.infer_road_relations import build_road_pair_relation_graph
from torii_sumo.intersection.osm_patch import parse_osm_xml
from torii_sumo.intersection.schema import (
    Approach,
    BBox,
    IntersectionCore,
    Movement,
    MovementMatrix,
    OSMNode,
    OSMPatch,
    OSMRelation,
    OSMWay,
    PatchSeed,
    RoadPairAngle,
    RoadPairDistance,
    RoadPairRelation,
    RoadPairRelationGraph,
)


FIXTURES = Path(__file__).parent / "fixtures"


def test_infer_movement_matrix_references_road_pair_relations() -> None:
    patch = parse_osm_xml(FIXTURES / "t3_priority.osm.xml")
    core = infer_intersection_core(patch)
    approaches = infer_approaches(patch, core)
    graph = build_road_pair_relation_graph(patch, core, approaches)

    matrix = infer_movement_matrix(core, approaches, graph)

    assert matrix.legal_movement_count == 6
    assert matrix.forbidden_movement_count == 0
    matrix_with_patch = infer_movement_matrix(core, approaches, graph, patch=patch)
    assert matrix_with_patch.legal_movement_count == matrix.legal_movement_count
    assert matrix_with_patch.forbidden_movement_count == matrix.forbidden_movement_count
    assert matrix_with_patch.restriction_blocked_count == 0
    assert matrix_with_patch.restriction_warnings == []
    assert all(movement.road_pair_relation_id for movement in matrix.movements)
    assert all(movement.evidence for movement in matrix.movements)
    assert all(movement.confidence > 0 for movement in matrix.movements)


def test_infer_movement_matrix_labels_turns_from_incoming_heading() -> None:
    patch = parse_osm_xml(FIXTURES / "x4_signalized.osm.xml")
    core = infer_intersection_core(patch)
    approaches = infer_approaches(patch, core)
    graph = build_road_pair_relation_graph(patch, core, approaches)
    source = next(approach for approach in approaches if approach.endpoint_xy and approach.endpoint_xy[1] > 0)
    south = next(approach for approach in approaches if approach.endpoint_xy and approach.endpoint_xy[1] < 0)
    east = next(approach for approach in approaches if approach.endpoint_xy and approach.endpoint_xy[0] > 1)
    west = next(approach for approach in approaches if approach.endpoint_xy and approach.endpoint_xy[0] < -1)

    matrix = infer_movement_matrix(core, approaches, graph)
    by_target = {
        movement.to_approach_id: movement
        for movement in matrix.movements
        if movement.from_approach_id == source.approach_id and movement.allowed
    }

    assert by_target[south.approach_id].turn == "straight"
    assert by_target[east.approach_id].turn == "left"
    assert by_target[west.approach_id].turn == "right"


def test_infer_movement_matrix_warns_when_shared_way_no_u_turn_has_no_matching_turn() -> None:
    patch = parse_osm_xml(FIXTURES / "x4_signalized.osm.xml")
    core = infer_intersection_core(patch)
    approaches = infer_approaches(patch, core)
    graph = build_road_pair_relation_graph(patch, core, approaches)
    unrestricted = infer_movement_matrix(core, approaches, graph)
    patch.relations["r_no_u_shared"] = _restriction_relation(
        "r_no_u_shared",
        "no_u_turn",
        from_way_id="10",
        to_way_id="10",
        via_ref="1",
    )

    matrix = infer_movement_matrix(core, approaches, graph, patch=patch)
    movements = {(movement.from_approach_id, movement.to_approach_id): movement for movement in matrix.movements}

    assert matrix.legal_movement_count == unrestricted.legal_movement_count
    assert matrix.forbidden_movement_count == unrestricted.forbidden_movement_count
    assert matrix.restriction_blocked_count == 0
    assert matrix.restriction_warnings == [
        "osm_restriction:r_no_u_shared:ambiguous:shared_way_direction"
    ]
    for pair in [("leg_1", "leg_3"), ("leg_3", "leg_1")]:
        movement = movements[pair]
        assert movement.turn == "straight"
        assert movement.allowed is True
        assert "osm_restriction:r_no_u_shared:no_u_turn" not in movement.evidence
    assert all(
        "r_no_u_shared" not in item
        for movement in matrix.movements
        for item in movement.evidence
    )


def test_infer_movement_matrix_applies_shared_same_way_only_restriction_by_turn() -> None:
    patch = parse_osm_xml(FIXTURES / "x4_signalized.osm.xml")
    core = infer_intersection_core(patch)
    approaches = infer_approaches(patch, core)
    graph = build_road_pair_relation_graph(patch, core, approaches)
    unrestricted = infer_movement_matrix(core, approaches, graph)
    patch.relations["r_only_shared"] = _restriction_relation(
        "r_only_shared",
        "only_straight_on",
        from_way_id="10",
        to_way_id="10",
        via_ref="1",
    )

    matrix = infer_movement_matrix(core, approaches, graph, patch=patch)
    movements = {(movement.from_approach_id, movement.to_approach_id): movement for movement in matrix.movements}

    assert matrix.legal_movement_count == unrestricted.legal_movement_count - 4
    assert matrix.forbidden_movement_count == unrestricted.forbidden_movement_count + 4
    assert matrix.restriction_blocked_count == 4
    assert matrix.restriction_warnings == []
    for pair in [("leg_1", "leg_3"), ("leg_3", "leg_1")]:
        movement = movements[pair]
        assert movement.turn == "straight"
        assert movement.allowed is True
        assert "osm_restriction:r_only_shared:only_straight_on" not in movement.evidence
    for pair in [("leg_1", "leg_2"), ("leg_1", "leg_4"), ("leg_3", "leg_2"), ("leg_3", "leg_4")]:
        movement = movements[pair]
        assert movement.turn != "straight"
        assert movement.allowed is False
        assert "osm_restriction:r_only_shared:only_straight_on" in movement.evidence


def test_infer_movement_matrix_applies_shared_from_to_way_no_restriction_by_turn() -> None:
    patch = parse_osm_xml(FIXTURES / "x4_signalized.osm.xml")
    core = infer_intersection_core(patch)
    approaches = infer_approaches(patch, core)
    graph = build_road_pair_relation_graph(patch, core, approaches)
    unrestricted = infer_movement_matrix(core, approaches, graph)
    patch.relations["r_no_right_shared"] = _restriction_relation(
        "r_no_right_shared",
        "no_right_turn",
        from_way_id="10",
        to_way_id="11",
        via_ref="1",
    )

    matrix = infer_movement_matrix(core, approaches, graph, patch=patch)
    movements = {(movement.from_approach_id, movement.to_approach_id): movement for movement in matrix.movements}

    assert matrix.legal_movement_count == unrestricted.legal_movement_count - 2
    assert matrix.forbidden_movement_count == unrestricted.forbidden_movement_count + 2
    assert matrix.restriction_blocked_count == 2
    assert matrix.restriction_warnings == []
    for pair in [("leg_1", "leg_4"), ("leg_3", "leg_2")]:
        movement = movements[pair]
        assert movement.turn == "right"
        assert movement.allowed is False
        assert "osm_restriction:r_no_right_shared:no_right_turn" in movement.evidence
    for pair in [("leg_1", "leg_2"), ("leg_3", "leg_4")]:
        movement = movements[pair]
        assert movement.turn == "left"
        assert movement.allowed is True
        assert "osm_restriction:r_no_right_shared:no_right_turn" not in movement.evidence


def test_infer_movement_matrix_applies_shared_from_to_way_only_restriction_by_turn() -> None:
    patch = parse_osm_xml(FIXTURES / "x4_signalized.osm.xml")
    core = infer_intersection_core(patch)
    fixture_approaches = infer_approaches(patch, core)
    graph = build_road_pair_relation_graph(patch, core, fixture_approaches)
    approaches = [
        approach.model_copy(update={"source_way_ids": ["outside_relation_to_way"]})
        if approach.approach_id == "leg_4"
        else approach
        for approach in fixture_approaches
    ]
    unrestricted = infer_movement_matrix(core, approaches, graph)
    patch.relations["r_only_right_shared"] = _restriction_relation(
        "r_only_right_shared",
        "only_right_turn",
        from_way_id="10",
        to_way_id="11",
        via_ref="1",
    )

    matrix = infer_movement_matrix(core, approaches, graph, patch=patch)
    movements = {(movement.from_approach_id, movement.to_approach_id): movement for movement in matrix.movements}

    assert matrix.legal_movement_count == unrestricted.legal_movement_count - 4
    assert matrix.forbidden_movement_count == unrestricted.forbidden_movement_count + 4
    assert matrix.restriction_blocked_count == 4
    assert matrix.restriction_warnings == []
    right_movements_from_shared_source = [
        movement
        for movement in matrix.movements
        if movement.from_approach_id in {"leg_1", "leg_3"} and movement.turn == "right"
    ]
    assert {
        (movement.from_approach_id, movement.to_approach_id)
        for movement in right_movements_from_shared_source
    } == {("leg_1", "leg_4"), ("leg_3", "leg_2")}
    for movement in right_movements_from_shared_source:
        assert movement.allowed is True
        assert "osm_restriction:r_only_right_shared:only_right_turn" not in movement.evidence
    for pair in [("leg_1", "leg_2"), ("leg_1", "leg_3"), ("leg_3", "leg_1"), ("leg_3", "leg_4")]:
        movement = movements[pair]
        assert movement.turn != "right"
        assert movement.allowed is False
        assert "osm_restriction:r_only_right_shared:only_right_turn" in movement.evidence


def test_infer_movement_matrix_uses_turn_lanes_for_source_lane_indices() -> None:
    patch = parse_osm_xml(FIXTURES / "x4_signalized.osm.xml")
    patch.ways["10"].tags.update({"lanes": "3", "turn:lanes": "left|through|right"})
    core = infer_intersection_core(patch)
    approaches = infer_approaches(patch, core)
    graph = build_road_pair_relation_graph(patch, core, approaches)
    source = next(approach for approach in approaches if approach.endpoint_xy and approach.endpoint_xy[1] > 0)

    matrix = infer_movement_matrix(core, approaches, graph)
    by_turn = {
        movement.turn: movement
        for movement in matrix.movements
        if movement.from_approach_id == source.approach_id and movement.allowed
    }

    assert by_turn["left"].from_lane_indices == [0]
    assert by_turn["straight"].from_lane_indices == [1]
    assert by_turn["right"].from_lane_indices == [2]


def test_infer_movement_matrix_blocks_matching_no_left_turn_restriction() -> None:
    core = _core_with_refs(["w_from", "w_left", "w_right"], ["n_core"])
    approaches = [
        _approach("from", "w_from", 0),
        _approach("left", "w_left", 90),
        _approach("right", "w_right", 270),
    ]
    graph = _fully_connected_pair_graph(approaches)
    patch = _patch_with_relations(
        ["w_from", "w_left", "w_right"],
        {
            "r_no_left": _restriction_relation(
                "r_no_left",
                "no_left_turn",
                from_way_id="w_from",
                to_way_id="w_left",
            )
        },
    )

    matrix = infer_movement_matrix(core, approaches, graph, patch=patch)
    movements = {(movement.from_approach_id, movement.to_approach_id): movement for movement in matrix.movements}

    blocked = movements[("from", "left")]
    assert blocked.turn == "left"
    assert blocked.allowed is False
    assert "osm_restriction:r_no_left:no_left_turn" in blocked.evidence
    assert movements[("from", "right")].turn == "right"
    assert movements[("from", "right")].allowed is True
    assert matrix.restriction_blocked_count == 1
    assert matrix.legal_movement_count == 5
    assert matrix.forbidden_movement_count == 1


def test_infer_movement_matrix_blocks_no_restriction_by_authoritative_members_when_turn_label_differs() -> None:
    core = _core_with_refs(["w_from", "w_left", "w_right"], ["n_core"])
    approaches = [
        _approach("from", "w_from", 0),
        _approach("left", "w_left", 90),
        _approach("right", "w_right", 270),
    ]
    graph = _fully_connected_pair_graph(approaches)
    patch = _patch_with_relations(
        ["w_from", "w_left", "w_right"],
        {
            "r_no_straight": _restriction_relation(
                "r_no_straight",
                "no_straight_on",
                from_way_id="w_from",
                to_way_id="w_left",
            )
        },
    )

    matrix = infer_movement_matrix(core, approaches, graph, patch=patch)
    movements = {(movement.from_approach_id, movement.to_approach_id): movement for movement in matrix.movements}

    blocked = movements[("from", "left")]
    assert blocked.turn == "left"
    assert blocked.allowed is False
    assert "osm_restriction:r_no_straight:no_straight_on" in blocked.evidence
    assert movements[("from", "right")].allowed is True
    assert matrix.restriction_blocked_count == 1
    assert matrix.restriction_warnings == []


def test_infer_movement_matrix_only_straight_blocks_non_straight_alternative() -> None:
    core = _core_with_refs(["w_from", "w_straight", "w_left"], ["n_core"])
    approaches = [
        _approach("from", "w_from", 0),
        _approach("straight", "w_straight", 180),
        _approach("left", "w_left", 90),
    ]
    graph = _fully_connected_pair_graph(approaches)
    patch = _patch_with_relations(
        ["w_from", "w_straight", "w_left"],
        {
            "r_only_straight": _restriction_relation(
                "r_only_straight",
                "only_straight_on",
                from_way_id="w_from",
                to_way_id="w_straight",
            )
        },
    )

    matrix = infer_movement_matrix(core, approaches, graph, patch=patch)
    movements = {(movement.from_approach_id, movement.to_approach_id): movement for movement in matrix.movements}

    permitted = movements[("from", "straight")]
    blocked = movements[("from", "left")]
    assert permitted.turn == "straight"
    assert permitted.allowed is True
    assert "osm_restriction:r_only_straight:only_straight_on" not in permitted.evidence
    assert blocked.turn == "left"
    assert blocked.allowed is False
    assert "osm_restriction:r_only_straight:only_straight_on" in blocked.evidence
    assert all(movement.allowed for movement in matrix.movements if movement.from_approach_id != "from")
    assert matrix.restriction_blocked_count == 1
    assert matrix.restriction_warnings == []


def test_infer_movement_matrix_only_restriction_permits_authoritative_to_member_when_turn_label_differs() -> None:
    core = _core_with_refs(["w_from", "w_permitted", "w_other"], ["n_core"])
    approaches = [
        _approach("from", "w_from", 0),
        _approach("permitted", "w_permitted", 90),
        _approach("other", "w_other", 270),
    ]
    graph = _fully_connected_pair_graph(approaches)
    patch = _patch_with_relations(
        ["w_from", "w_permitted", "w_other"],
        {
            "r_only_straight": _restriction_relation(
                "r_only_straight",
                "only_straight_on",
                from_way_id="w_from",
                to_way_id="w_permitted",
            )
        },
    )

    matrix = infer_movement_matrix(core, approaches, graph, patch=patch)
    movements = {(movement.from_approach_id, movement.to_approach_id): movement for movement in matrix.movements}

    permitted = movements[("from", "permitted")]
    blocked = movements[("from", "other")]
    assert permitted.turn == "left"
    assert permitted.allowed is True
    assert "osm_restriction:r_only_straight:only_straight_on" not in permitted.evidence
    assert blocked.turn == "right"
    assert blocked.allowed is False
    assert "osm_restriction:r_only_straight:only_straight_on" in blocked.evidence
    assert all(movement.allowed for movement in matrix.movements if movement.from_approach_id != "from")
    assert matrix.restriction_blocked_count == 1
    assert matrix.restriction_warnings == []


def test_infer_movement_matrix_only_left_blocks_non_left_alternative() -> None:
    core = _core_with_refs(["w_from", "w_left", "w_right"], ["n_core"])
    approaches = [
        _approach("from", "w_from", 0),
        _approach("left", "w_left", 90),
        _approach("right", "w_right", 270),
    ]
    graph = _fully_connected_pair_graph(approaches)
    patch = _patch_with_relations(
        ["w_from", "w_left", "w_right"],
        {
            "r_only_left": _restriction_relation(
                "r_only_left",
                "only_left_turn",
                from_way_id="w_from",
                to_way_id="w_left",
            )
        },
    )

    matrix = infer_movement_matrix(core, approaches, graph, patch=patch)
    movements = {(movement.from_approach_id, movement.to_approach_id): movement for movement in matrix.movements}

    permitted = movements[("from", "left")]
    blocked = movements[("from", "right")]
    assert permitted.turn == "left"
    assert permitted.allowed is True
    assert "osm_restriction:r_only_left:only_left_turn" not in permitted.evidence
    assert blocked.turn == "right"
    assert blocked.allowed is False
    assert "osm_restriction:r_only_left:only_left_turn" in blocked.evidence
    assert matrix.restriction_blocked_count == 1
    assert matrix.restriction_warnings == []


def test_infer_movement_matrix_only_right_blocks_non_right_alternative() -> None:
    core = _core_with_refs(["w_from", "w_left", "w_right"], ["n_core"])
    approaches = [
        _approach("from", "w_from", 0),
        _approach("left", "w_left", 90),
        _approach("right", "w_right", 270),
    ]
    graph = _fully_connected_pair_graph(approaches)
    patch = _patch_with_relations(
        ["w_from", "w_left", "w_right"],
        {
            "r_only_right": _restriction_relation(
                "r_only_right",
                "only_right_turn",
                from_way_id="w_from",
                to_way_id="w_right",
            )
        },
    )

    matrix = infer_movement_matrix(core, approaches, graph, patch=patch)
    movements = {(movement.from_approach_id, movement.to_approach_id): movement for movement in matrix.movements}

    permitted = movements[("from", "right")]
    blocked = movements[("from", "left")]
    assert permitted.turn == "right"
    assert permitted.allowed is True
    assert "osm_restriction:r_only_right:only_right_turn" not in permitted.evidence
    assert blocked.turn == "left"
    assert blocked.allowed is False
    assert "osm_restriction:r_only_right:only_right_turn" in blocked.evidence
    assert matrix.restriction_blocked_count == 1
    assert matrix.restriction_warnings == []


def test_infer_movement_matrix_blocks_no_u_turn_even_when_turn_lanes_allow_reverse() -> None:
    core = _core_with_refs(["w_from", "w_return"], ["n_core"])
    source = _approach("from", "w_from", 0).model_copy(update={"turn_lanes_raw": "reverse|through"})
    target = _approach("return", "w_return", 0)
    graph = _fully_connected_pair_graph([source, target])
    patch = _patch_with_relations(
        ["w_from", "w_return"],
        {
            "r_no_u": _restriction_relation(
                "r_no_u",
                "no_u_turn",
                from_way_id="w_from",
                to_way_id="w_return",
            )
        },
    )

    unrestricted = infer_movement_matrix(core, [source, target], graph)
    unrestricted_uturn = next(
        movement
        for movement in unrestricted.movements
        if movement.from_approach_id == "from" and movement.to_approach_id == "return"
    )
    assert unrestricted_uturn.turn == "uturn"
    assert unrestricted_uturn.allowed is True

    matrix = infer_movement_matrix(core, [source, target], graph, patch=patch)
    restricted_uturn = next(
        movement
        for movement in matrix.movements
        if movement.from_approach_id == "from" and movement.to_approach_id == "return"
    )

    assert restricted_uturn.turn == "uturn"
    assert restricted_uturn.from_lane_indices == [0]
    assert restricted_uturn.allowed is False
    assert "osm_restriction:r_no_u:no_u_turn" in restricted_uturn.evidence
    assert matrix.restriction_blocked_count == 1


def test_infer_movement_matrix_does_not_count_restriction_when_movement_already_forbidden() -> None:
    core = _core_with_refs(["w_from", "w_left", "w_right"], ["n_core"])
    source = _approach("from", "w_from", 0).model_copy(update={"has_incoming_vehicle_flow": False})
    approaches = [
        source,
        _approach("left", "w_left", 90),
        _approach("right", "w_right", 270),
    ]
    graph = _fully_connected_pair_graph(approaches)
    patch = _patch_with_relations(
        ["w_from", "w_left", "w_right"],
        {
            "r_no_left": _restriction_relation(
                "r_no_left",
                "no_left_turn",
                from_way_id="w_from",
                to_way_id="w_left",
            )
        },
    )

    matrix = infer_movement_matrix(core, approaches, graph, patch=patch)
    blocked = next(
        movement
        for movement in matrix.movements
        if movement.from_approach_id == "from" and movement.to_approach_id == "left"
    )

    assert blocked.allowed is False
    assert "source_direction:blocked_incoming_vehicle_flow" in blocked.evidence
    assert "osm_restriction:r_no_left:no_left_turn" in blocked.evidence
    assert matrix.restriction_blocked_count == 0


def test_infer_movement_matrix_warns_for_unknown_restriction_without_blocking() -> None:
    core = _core_with_refs(["w_from", "w_to"], ["n_core"])
    source = _approach("from", "w_from", 0)
    target = _approach("to", "w_to", 180)
    graph = _fully_connected_pair_graph([source, target])
    patch = _patch_with_relations(
        ["w_from", "w_to"],
        {
            "r_unknown": _restriction_relation(
                "r_unknown",
                "no_diagonal_turn",
                from_way_id="w_from",
                to_way_id="w_to",
            )
        },
    )

    matrix = infer_movement_matrix(core, [source, target], graph, patch=patch)

    assert matrix.restriction_blocked_count == 0
    assert matrix.restriction_warnings == ["osm_restriction:r_unknown:unknown_restriction_type:no_diagonal_turn"]
    assert [movement.allowed for movement in matrix.movements] == [True, True]


def test_infer_movement_matrix_warns_for_missing_type_and_incomplete_core_restrictions() -> None:
    core = _core_with_refs(["w_from", "w_to"], ["n_core"])
    source = _approach("from", "w_from", 0)
    target = _approach("to", "w_to", 180)
    graph = _fully_connected_pair_graph([source, target])
    patch = _patch_with_relations(
        ["w_from", "w_to"],
        {
            "r_missing_type": OSMRelation(
                id="r_missing_type",
                members=[
                    {"type": "way", "ref": "w_from", "role": "from"},
                    {"type": "node", "ref": "n_core", "role": "via"},
                    {"type": "way", "ref": "w_to", "role": "to"},
                ],
                tags={"type": "restriction"},
            ),
            "r_incomplete": OSMRelation(
                id="r_incomplete",
                members=[
                    {"type": "way", "ref": "w_from", "role": "from"},
                    {"type": "way", "ref": "w_to", "role": "to"},
                ],
                tags={"type": "restriction", "restriction": "no_right_turn"},
            ),
        },
    )

    matrix = infer_movement_matrix(core, [source, target], graph, patch=patch)

    assert matrix.restriction_blocked_count == 0
    assert matrix.restriction_warnings == [
        "osm_restriction:r_missing_type:missing_restriction_type",
        "osm_restriction:r_incomplete:incomplete:via",
    ]
    assert [movement.allowed for movement in matrix.movements] == [True, True]


def test_infer_movement_matrix_warns_and_skips_multiple_from_to_restriction() -> None:
    core = _core_with_refs(["w_from", "w_extra_from", "w_left", "w_right"], ["n_core"])
    approaches = [
        _approach("from", "w_from", 0),
        _approach("extra_from", "w_extra_from", 180),
        _approach("left", "w_left", 90),
        _approach("right", "w_right", 270),
    ]
    graph = _fully_connected_pair_graph(approaches)
    patch = _patch_with_relations(
        ["w_from", "w_extra_from", "w_left", "w_right"],
        {
            "r_ambiguous_members": OSMRelation(
                id="r_ambiguous_members",
                members=[
                    {"type": "way", "ref": "w_from", "role": "from"},
                    {"type": "way", "ref": "w_extra_from", "role": "from"},
                    {"type": "node", "ref": "n_core", "role": "via"},
                    {"type": "way", "ref": "w_left", "role": "to"},
                    {"type": "way", "ref": "w_right", "role": "to"},
                ],
                tags={"type": "restriction", "restriction": "no_left_turn"},
            ),
        },
    )

    matrix = infer_movement_matrix(core, approaches, graph, patch=patch)

    assert matrix.restriction_blocked_count == 0
    assert matrix.restriction_warnings == [
        "osm_restriction:r_ambiguous_members:ambiguous:multiple_from_or_to"
    ]
    assert all(
        "r_ambiguous_members" not in item
        for movement in matrix.movements
        for item in movement.evidence
    )


def test_infer_movement_matrix_ignores_typed_via_node_ref_matching_core_way_id() -> None:
    core = _core_with_refs(["w_from", "w_to", "w_core"], ["n_core"])
    source = _approach("from", "w_from", 0)
    target = _approach("to", "w_to", 270)
    graph = _fully_connected_pair_graph([source, target])
    patch = _patch_with_relations(
        ["w_from", "w_to", "w_core"],
        {
            "r_typed_via": _restriction_relation(
                "r_typed_via",
                "no_right_turn",
                from_way_id="w_from",
                to_way_id="w_to",
                via_ref="w_core",
            )
        },
    )

    matrix = infer_movement_matrix(core, [source, target], graph, patch=patch)

    assert matrix.restriction_blocked_count == 0
    assert matrix.restriction_warnings == []
    assert [movement.allowed for movement in matrix.movements] == [True, True]
    assert all("r_typed_via" not in item for movement in matrix.movements for item in movement.evidence)


def test_infer_movement_matrix_ignores_outside_core_unknown_and_incomplete_restrictions() -> None:
    core = _core_with_refs(["w_from", "w_to"], ["n_core"])
    source = _approach("from", "w_from", 0)
    target = _approach("to", "w_to", 180)
    graph = _fully_connected_pair_graph([source, target])
    patch = _patch_with_relations(
        ["w_from", "w_to"],
        {
            "r_unknown_outside": _restriction_relation(
                "r_unknown_outside",
                "no_hover_turn",
                from_way_id="w_from",
                to_way_id="w_to",
                via_ref="n_outside",
            ),
            "r_incomplete_outside": OSMRelation(
                id="r_incomplete_outside",
                members=[
                    {"type": "way", "ref": "w_from", "role": "from"},
                    {"type": "node", "ref": "n_outside", "role": "via"},
                ],
                tags={"type": "restriction", "restriction": "no_right_turn"},
            ),
        },
    )

    matrix = infer_movement_matrix(core, [source, target], graph, patch=patch)
    evidence = [item for movement in matrix.movements for item in movement.evidence]

    assert matrix.restriction_blocked_count == 0
    assert matrix.restriction_warnings == []
    assert all("r_unknown_outside" not in item and "r_incomplete_outside" not in item for item in evidence)


def test_infer_movement_matrix_blocks_passenger_movements_from_oneway_away_from_core() -> None:
    patch = parse_osm_xml(FIXTURES / "x4_signalized.osm.xml")
    patch.ways["10"].tags["oneway"] = "yes"
    core = infer_intersection_core(patch)
    approaches = infer_approaches(patch, core)
    graph = build_road_pair_relation_graph(patch, core, approaches)
    away_from_core = next(approach for approach in approaches if approach.endpoint_xy and approach.endpoint_xy[1] < 0)

    matrix = infer_movement_matrix(core, approaches, graph)
    movements_from_blocked_source = [
        movement for movement in matrix.movements if movement.from_approach_id == away_from_core.approach_id
    ]

    assert away_from_core.has_incoming_vehicle_flow is False
    assert away_from_core.has_outgoing_vehicle_flow is True
    assert movements_from_blocked_source
    assert all(movement.allowed is False for movement in movements_from_blocked_source)
    assert all("source_direction:oneway:backward_away_from_core" in movement.evidence for movement in movements_from_blocked_source)


def test_infer_control_model_uses_osm_traffic_signal_tag() -> None:
    patch = parse_osm_xml(FIXTURES / "x4_signalized.osm.xml")
    core = infer_intersection_core(patch)
    approaches = infer_approaches(patch, core)
    graph = build_road_pair_relation_graph(patch, core, approaches)
    matrix = infer_movement_matrix(core, approaches, graph)

    control = infer_control_model(patch, core, approaches, matrix)

    assert control.control_type == "traffic_light"
    assert control.tls_id == core.core_id
    assert "synthetic:conflict_grouped" in control.source
    assert "synthetic:alternating_placeholder" not in control.source
    assert len(control.link_index_map) == matrix.legal_movement_count
    assert {len(phase.state) for phase in control.phases} == {matrix.legal_movement_count}


def test_conflicting_left_turns_do_not_share_green_phase() -> None:
    patch = parse_osm_xml(FIXTURES / "x4_signalized.osm.xml")
    core = infer_intersection_core(patch)
    approaches = infer_approaches(patch, core)
    graph = build_road_pair_relation_graph(patch, core, approaches)
    matrix = infer_movement_matrix(core, approaches, graph, patch)

    control = infer_control_model(patch, core, approaches, matrix)
    left_ids = {
        movement.movement_id
        for movement in matrix.movements
        if movement.allowed and movement.turn == "left"
    }

    for phase in control.phases:
        greens = {
            movement_id
            for movement_id, index in control.link_index_map.items()
            if phase.state[index] == "G"
        }
        assert len(greens & left_ids) <= 1


def test_tls_grouping_keeps_singleton_conflict_groups_when_geometry_is_sufficient() -> None:
    core = _core_with_refs(["w_a", "w_b", "w_c"], ["n_core"])
    patch = _patch_with_relations(["w_a", "w_b", "w_c"], {})
    patch.nodes["n_core"].tags = {"highway": "traffic_signals"}
    approaches = [
        _approach("a", "w_a", 0).model_copy(update={"endpoint_xy": (0.0, 100.0)}),
        _approach("b", "w_b", 90).model_copy(update={"endpoint_xy": (100.0, 0.0)}),
        _approach("c", "w_c", 270).model_copy(update={"endpoint_xy": (-100.0, 0.0)}),
    ]
    movements = [
        Movement(
            movement_id="a_to_b",
            from_approach_id="a",
            to_approach_id="b",
            road_pair_relation_id="a_b",
            turn="left",
            allowed=True,
            from_lane_indices=[0],
            to_lane_indices=[0],
            allowed_modes={"passenger"},
            evidence=["fixture:all_conflict"],
            confidence=1.0,
        ),
        Movement(
            movement_id="b_to_c",
            from_approach_id="b",
            to_approach_id="c",
            road_pair_relation_id="b_c",
            turn="left",
            allowed=True,
            from_lane_indices=[0],
            to_lane_indices=[0],
            allowed_modes={"passenger"},
            evidence=["fixture:all_conflict"],
            confidence=1.0,
        ),
        Movement(
            movement_id="c_to_a",
            from_approach_id="c",
            to_approach_id="a",
            road_pair_relation_id="c_a",
            turn="left",
            allowed=True,
            from_lane_indices=[0],
            to_lane_indices=[0],
            allowed_modes={"passenger"},
            evidence=["fixture:all_conflict"],
            confidence=1.0,
        ),
    ]
    matrix = MovementMatrix(
        movements=movements,
        legal_movement_count=len(movements),
        forbidden_movement_count=0,
        inferred_movement_count=len(movements),
        restriction_blocked_count=0,
    )

    control = infer_control_model(patch, core, approaches, matrix)

    assert "synthetic:conflict_grouped" in control.source
    assert "synthetic:alternating_placeholder" not in control.source
    assert {len(phase.state) for phase in control.phases} == {len(control.link_index_map)}
    assert all(len(phase.movement_ids) == 1 for phase in control.phases)
    assert all(phase.state.count("G") <= 1 for phase in control.phases)


def test_tls_grouping_keeps_vehicle_uturns_singleton() -> None:
    core = _core_with_refs(["w_north", "w_return", "w_south", "w_exit"], ["n_core"])
    patch = _patch_with_relations(["w_north", "w_return", "w_south", "w_exit"], {})
    patch.nodes["n_core"].tags = {"highway": "traffic_signals"}
    approaches = [
        _approach("north", "w_north", 0).model_copy(update={"endpoint_xy": (0.0, 100.0)}),
        _approach("return", "w_return", 0).model_copy(update={"endpoint_xy": (0.0, 120.0)}),
        _approach("south", "w_south", 180).model_copy(update={"endpoint_xy": (0.0, -100.0)}),
        _approach("exit", "w_exit", 180).model_copy(update={"endpoint_xy": (0.0, -120.0)}),
    ]
    uturn = Movement(
        movement_id="north_to_return",
        from_approach_id="north",
        to_approach_id="return",
        road_pair_relation_id="north_return",
        turn="uturn",
        allowed=True,
        from_lane_indices=[0],
        to_lane_indices=[0],
        allowed_modes={"passenger"},
        evidence=["fixture:uturn"],
        confidence=1.0,
    )
    opposite = Movement(
        movement_id="south_to_exit",
        from_approach_id="south",
        to_approach_id="exit",
        road_pair_relation_id="south_exit",
        turn="straight",
        allowed=True,
        from_lane_indices=[0],
        to_lane_indices=[0],
        allowed_modes={"passenger"},
        evidence=["fixture:opposite_vehicle"],
        confidence=1.0,
    )
    matrix = MovementMatrix(
        movements=[uturn, opposite],
        legal_movement_count=2,
        forbidden_movement_count=0,
        inferred_movement_count=2,
        restriction_blocked_count=0,
    )

    control = infer_control_model(patch, core, approaches, matrix)

    uturn_index = control.link_index_map[uturn.movement_id]
    assert "synthetic:conflict_grouped" in control.source
    for phase in control.phases:
        if phase.state[uturn_index] == "G":
            assert phase.state.count("G") == 1


def test_compatible_opposite_straights_share_green_phase() -> None:
    patch = parse_osm_xml(FIXTURES / "x4_signalized.osm.xml")
    core = infer_intersection_core(patch)
    approaches = infer_approaches(patch, core)
    graph = build_road_pair_relation_graph(patch, core, approaches)
    matrix = infer_movement_matrix(core, approaches, graph, patch)
    control = infer_control_model(patch, core, approaches, matrix)

    straight_ids = {
        movement.movement_id
        for movement in matrix.movements
        if movement.allowed and movement.turn == "straight"
    }

    assert any(
        len(
            {
                movement_id
                for movement_id, index in control.link_index_map.items()
                if phase.state[index] == "G"
            }
            & straight_ids
        )
        >= 2
        for phase in control.phases
    )


def test_support_movement_not_grouped_with_conflicting_vehicle_movement() -> None:
    patch = parse_osm_xml(FIXTURES / "x4_signalized.osm.xml")
    core = infer_intersection_core(patch)
    approaches = infer_approaches(patch, core)
    graph = build_road_pair_relation_graph(patch, core, approaches)
    matrix = infer_movement_matrix(core, approaches, graph, patch)
    support_a = approaches[0].model_copy(update={"approach_id": "support_a", "allowed_modes": {"bicycle"}})
    support_b = approaches[1].model_copy(update={"approach_id": "support_b", "allowed_modes": {"bicycle"}})
    support = Movement(
        movement_id="support_a_to_support_b",
        from_approach_id="support_a",
        to_approach_id="support_b",
        road_pair_relation_id="support_pair",
        turn="straight",
        allowed=True,
        from_lane_indices=[0],
        to_lane_indices=[0],
        allowed_modes={"bicycle"},
        evidence=["fixture:conflicting_support"],
        confidence=1.0,
    )
    matrix = matrix.model_copy(
        update={
            "movements": [*matrix.movements, support],
            "legal_movement_count": matrix.legal_movement_count + 1,
            "inferred_movement_count": matrix.inferred_movement_count + 1,
        }
    )

    control = infer_control_model(patch, core, [*approaches, support_a, support_b], matrix)

    support_index = control.link_index_map[support.movement_id]
    for phase in control.phases:
        if phase.state[support_index] == "G":
            vehicle_green = [
                movement_id
                for movement_id, index in control.link_index_map.items()
                if phase.state[index] == "G" and movement_id != support.movement_id
            ]
            assert not vehicle_green


def test_tls_grouping_falls_back_when_geometry_is_insufficient() -> None:
    patch = parse_osm_xml(FIXTURES / "x4_signalized.osm.xml")
    core = infer_intersection_core(patch)
    approaches = [
        approach.model_copy(update={"endpoint_xy": None, "source_shape_xy": []})
        for approach in infer_approaches(patch, core)
    ]
    graph = build_road_pair_relation_graph(patch, core, approaches)
    matrix = infer_movement_matrix(core, approaches, graph, patch)

    control = infer_control_model(patch, core, approaches, matrix)

    assert "synthetic:alternating_placeholder" in control.source


def test_infer_control_model_uses_nearby_osm_traffic_signal_node() -> None:
    patch = parse_osm_xml(FIXTURES / "x4_signalized.osm.xml")
    patch.nodes["1"].tags = {}
    core_x = patch.nodes["1"].x or 0.0
    core_y = patch.nodes["1"].y or 0.0
    patch.nodes["signal_near_core"] = OSMNode(
        id="signal_near_core",
        lat=48.0005,
        lon=11.0005,
        x=core_x + 10.0,
        y=core_y,
        tags={"highway": "traffic_signals"},
    )
    core = infer_intersection_core(patch)
    approaches = infer_approaches(patch, core)
    graph = build_road_pair_relation_graph(patch, core, approaches)
    matrix = infer_movement_matrix(core, approaches, graph)

    control = infer_control_model(patch, core, approaches, matrix)

    assert control.control_type == "traffic_light"
    assert "osm:nearby_highway=traffic_signals" in control.source


def test_infer_control_model_excludes_support_path_movements_from_tls() -> None:
    patch = parse_osm_xml(FIXTURES / "clustered_signalized_crossing.osm.xml")
    patch.nodes["seed"].tags = {"highway": "traffic_signals"}
    core = infer_intersection_core(patch, PatchSeed(osm_node_id="seed"))
    approaches = infer_approaches(patch, core)
    graph = build_road_pair_relation_graph(patch, core, approaches)
    matrix = infer_movement_matrix(core, approaches, graph)
    support = Movement(
        movement_id="support_path_to_support_path",
        from_approach_id="path_a",
        to_approach_id="path_b",
        road_pair_relation_id="support_pair",
        turn="straight",
        allowed=True,
        from_lane_indices=[0],
        to_lane_indices=[0],
        allowed_modes={"bicycle"},
        evidence=["fixture:support_path"],
        confidence=1.0,
    )
    matrix = matrix.model_copy(
        update={
            "movements": [*matrix.movements, support],
            "legal_movement_count": matrix.legal_movement_count + 1,
            "inferred_movement_count": matrix.inferred_movement_count + 1,
        }
    )

    control = infer_control_model(patch, core, approaches, matrix)

    indexed = {movement.movement_id: movement for movement in matrix.movements if movement.movement_id in control.link_index_map}
    assert indexed
    assert all("passenger" in movement.allowed_modes for movement in indexed.values())


def test_infer_control_model_includes_known_bicycle_support_movements() -> None:
    patch = parse_osm_xml(FIXTURES / "x4_signalized.osm.xml")
    core = infer_intersection_core(patch)
    approaches = infer_approaches(patch, core)
    graph = build_road_pair_relation_graph(patch, core, approaches)
    matrix = infer_movement_matrix(core, approaches, graph)
    support_a = approaches[0].model_copy(update={"approach_id": "support_a", "allowed_modes": {"bicycle"}})
    support_b = approaches[1].model_copy(update={"approach_id": "support_b", "allowed_modes": {"bicycle"}})
    assert support_a.is_support_only is False
    assert support_b.is_support_only is False
    support = Movement(
        movement_id="support_a_to_support_b",
        from_approach_id="support_a",
        to_approach_id="support_b",
        road_pair_relation_id="support_pair",
        turn="straight",
        allowed=True,
        from_lane_indices=[0],
        to_lane_indices=[0],
        allowed_modes={"bicycle"},
        evidence=["fixture:signalized_support_path"],
        confidence=1.0,
    )
    matrix = matrix.model_copy(
        update={
            "movements": [*matrix.movements, support],
            "legal_movement_count": matrix.legal_movement_count + 1,
            "inferred_movement_count": matrix.inferred_movement_count + 1,
        }
    )

    control = infer_control_model(patch, core, [*approaches, support_a, support_b], matrix)

    assert support.movement_id in control.link_index_map


def test_infer_control_model_keeps_one_same_way_bicycle_support_turnaround() -> None:
    patch = parse_osm_xml(FIXTURES / "x4_signalized.osm.xml")
    core = infer_intersection_core(patch)
    approaches = infer_approaches(patch, core)
    graph = build_road_pair_relation_graph(patch, core, approaches)
    matrix = infer_movement_matrix(core, approaches, graph)
    support_a = approaches[0].model_copy(
        update={"approach_id": "support_a", "source_way_ids": ["cycleway_1"], "allowed_modes": {"bicycle"}}
    )
    support_b = approaches[1].model_copy(
        update={"approach_id": "support_b", "source_way_ids": ["cycleway_1"], "allowed_modes": {"bicycle"}}
    )
    support_ab = Movement(
        movement_id="support_a_to_support_b",
        from_approach_id="support_a",
        to_approach_id="support_b",
        road_pair_relation_id="support_pair",
        turn="straight",
        allowed=True,
        from_lane_indices=[0],
        to_lane_indices=[0],
        allowed_modes={"bicycle"},
        evidence=["fixture:same_way_support_path"],
        confidence=1.0,
    )
    support_ba = support_ab.model_copy(
        update={
            "movement_id": "support_b_to_support_a",
            "from_approach_id": "support_b",
            "to_approach_id": "support_a",
        }
    )
    matrix = matrix.model_copy(
        update={
            "movements": [*matrix.movements, support_ab, support_ba],
            "legal_movement_count": matrix.legal_movement_count + 2,
            "inferred_movement_count": matrix.inferred_movement_count + 2,
        }
    )

    control = infer_control_model(patch, core, [*approaches, support_a, support_b], matrix)

    controlled_support = {support_ab.movement_id, support_ba.movement_id} & set(control.link_index_map)
    assert controlled_support == {support_ab.movement_id}


def test_infer_control_model_keeps_one_mixed_support_feeder_to_same_bike_corridor() -> None:
    patch = parse_osm_xml(FIXTURES / "x4_signalized.osm.xml")
    core = infer_intersection_core(patch)
    approaches = infer_approaches(patch, core)
    graph = build_road_pair_relation_graph(patch, core, approaches)
    matrix = infer_movement_matrix(core, approaches, graph)
    feeder = approaches[0].model_copy(
        update={"approach_id": "feeder", "source_way_ids": ["feeder_path"], "allowed_modes": {"bicycle", "pedestrian"}}
    )
    bike_a = approaches[1].model_copy(
        update={"approach_id": "bike_a", "source_way_ids": ["cycleway_1"], "allowed_modes": {"bicycle"}}
    )
    bike_b = approaches[2].model_copy(
        update={"approach_id": "bike_b", "source_way_ids": ["cycleway_1"], "allowed_modes": {"bicycle"}}
    )
    feeder_to_a = Movement(
        movement_id="feeder_to_bike_a",
        from_approach_id="feeder",
        to_approach_id="bike_a",
        road_pair_relation_id="support_pair_a",
        turn="right",
        allowed=True,
        from_lane_indices=[0],
        to_lane_indices=[0],
        allowed_modes={"bicycle"},
        evidence=["fixture:mixed_support_feeder"],
        confidence=1.0,
    )
    feeder_to_b = feeder_to_a.model_copy(
        update={
            "movement_id": "feeder_to_bike_b",
            "to_approach_id": "bike_b",
            "road_pair_relation_id": "support_pair_b",
        }
    )
    matrix = matrix.model_copy(
        update={
            "movements": [*matrix.movements, feeder_to_a, feeder_to_b],
            "legal_movement_count": matrix.legal_movement_count + 2,
            "inferred_movement_count": matrix.inferred_movement_count + 2,
        }
    )

    control = infer_control_model(patch, core, [*approaches, feeder, bike_a, bike_b], matrix)

    controlled_support = {feeder_to_a.movement_id, feeder_to_b.movement_id} & set(control.link_index_map)
    assert controlled_support == {feeder_to_a.movement_id}


def test_infer_movement_matrix_does_not_allow_cross_mode_movements() -> None:
    patch = parse_osm_xml(FIXTURES / "clustered_signalized_crossing.osm.xml")
    patch.nodes["bike"] = OSMNode(id="bike", lat=48.00075, lon=11.00035, x=100.0, y=100.0)
    patch.ways["cycleway_extra"] = OSMWay(
        id="cycleway_extra",
        node_refs=["bike", "vehicle_core"],
        tags={"highway": "cycleway", "foot": "no"},
    )
    core = infer_intersection_core(patch, PatchSeed(osm_node_id="seed"))
    approaches = infer_approaches(patch, core)
    graph = build_road_pair_relation_graph(patch, core, approaches)

    matrix = infer_movement_matrix(core, approaches, graph)
    movements_by_pair = {(movement.from_approach_id, movement.to_approach_id): movement for movement in matrix.movements}
    by_way = {approach.source_way_ids[0]: approach.approach_id for approach in approaches}
    road_id = by_way["road_ew"]
    bike_id = by_way["cycleway_extra"]

    assert movements_by_pair[(road_id, bike_id)].allowed_modes == set()
    assert movements_by_pair[(road_id, bike_id)].allowed is False


def test_infer_movement_matrix_blocks_disjoint_same_mode_movements() -> None:
    patch = OSMPatch(
        nodes={
            "a": OSMNode(id="a", lat=0, lon=0, x=0, y=0, tags={}),
            "b": OSMNode(id="b", lat=0, lon=0, x=100, y=0, tags={}),
        },
        ways={
            "wa": OSMWay(id="wa", node_refs=["a"], tags={"highway": "residential"}),
            "wb": OSMWay(id="wb", node_refs=["b"], tags={"highway": "residential"}),
        },
        relations={},
        bbox=BBox(min_lon=0, min_lat=0, max_lon=0, max_lat=0),
    )
    core = IntersectionCore(
        core_id="core",
        center_xy=(0, 0),
        core_osm_node_ids=[],
        core_way_ids=["wa", "wb"],
        core_radius_m=20,
        topology_type="unknown",
        internal_fragment_count=0,
        short_internal_edge_count=0,
        confidence=0.5,
    )
    approaches = [
        _approach("a", "wa", 0),
        _approach("b", "wb", 90),
    ]
    graph = build_road_pair_relation_graph(patch, core, approaches)

    matrix = infer_movement_matrix(core, approaches, graph)

    assert {relation.expected_relation for relation in graph.relations} == {"unknown"}
    assert [movement.allowed for movement in matrix.movements] == [False, False]


def test_infer_movement_matrix_derives_support_only_from_allowed_modes_after_model_copy() -> None:
    core = IntersectionCore(
        core_id="core",
        center_xy=(0, 0),
        core_osm_node_ids=[],
        core_way_ids=["wa", "wb"],
        core_radius_m=20,
        topology_type="unknown",
        internal_fragment_count=0,
        short_internal_edge_count=0,
        confidence=0.5,
    )
    source = _approach("a", "wa", 0).model_copy(update={"allowed_modes": {"bicycle"}})
    target = _approach("b", "wb", 180).model_copy(update={"allowed_modes": {"bicycle"}})
    graph = _connected_pair_graph("a", "b")

    assert source.is_support_only is False
    assert target.is_support_only is False

    matrix = infer_movement_matrix(core, [source, target], graph)

    assert matrix.legal_movement_count == 2
    assert [movement.allowed_modes for movement in matrix.movements] == [{"bicycle"}, {"bicycle"}]
    assert all(movement.allowed for movement in matrix.movements)


def _approach(approach_id: str, way_id: str, bearing: float) -> Approach:
    return Approach(
        approach_id=approach_id,
        role=approach_id,
        source_way_ids=[way_id],
        road_name=None,
        highway_class="residential",
        bearing_to_core=(bearing + 180) % 360,
        bearing_from_core=bearing,
        incoming_lane_count=1,
        outgoing_lane_count=1,
        incoming_edge_ids=[f"{approach_id}_in"],
        outgoing_edge_ids=[f"{approach_id}_out"],
        oneway=False,
        allowed_modes={"passenger"},
        access_tags={},
    )


def _connected_pair_graph(first_id: str, second_id: str) -> RoadPairRelationGraph:
    relation = RoadPairRelation(
        relation_id=f"{first_id}_{second_id}",
        road_a_id=first_id,
        road_b_id=second_id,
        road_a_source_way_ids=[first_id],
        road_b_source_way_ids=[second_id],
        geometry_relation="shared_node",
        topology_relation="connected",
        expected_relation="should_connect",
        angle=RoadPairAngle(
            road_a_bearing_deg=0.0,
            road_b_bearing_deg=180.0,
            signed_delta_deg=180.0,
            abs_delta_deg=180.0,
            relation_class="opposite_direction",
            turn_angle_from_a_to_b_deg=180.0,
        ),
        distance=RoadPairDistance(
            endpoint_gap_m=0.0,
            min_geometry_distance_m=0.0,
            projected_intersection_xy=(0.0, 0.0),
            overlap_length_m=0.0,
            overlap_ratio_a=0.0,
            overlap_ratio_b=0.0,
            crossing_point_inside_segments=True,
            nearest_point_a_xy=(0.0, 0.0),
            nearest_point_b_xy=(0.0, 0.0),
        ),
        inferred_turn="straight",
        error_type="none",
        suggested_fix="none",
        confidence=1.0,
        evidence=["fixture:connected_support_pair"],
    )
    return RoadPairRelationGraph(
        relations=[relation],
        missing_connection_count=0,
        wrong_connection_count=0,
        overlap_conflict_count=0,
        near_miss_count=0,
        duplicate_parallel_count=0,
        blocking_error_count=0,
    )


def _core_with_refs(way_ids: list[str], node_ids: list[str]) -> IntersectionCore:
    return IntersectionCore(
        core_id="core",
        center_xy=(0, 0),
        core_osm_node_ids=node_ids,
        core_way_ids=way_ids,
        core_radius_m=20,
        topology_type="unknown",
        internal_fragment_count=0,
        short_internal_edge_count=0,
        confidence=0.5,
    )


def _patch_with_relations(way_ids: list[str], relations: dict[str, OSMRelation]) -> OSMPatch:
    return OSMPatch(
        nodes={"n_core": OSMNode(id="n_core", lat=0, lon=0, x=0, y=0, tags={})},
        ways={way_id: OSMWay(id=way_id, node_refs=["n_core"], tags={"highway": "residential"}) for way_id in way_ids},
        relations=relations,
        bbox=BBox(min_lon=0, min_lat=0, max_lon=0, max_lat=0),
    )


def _restriction_relation(
    relation_id: str,
    restriction_type: str,
    *,
    from_way_id: str,
    to_way_id: str,
    via_ref: str = "n_core",
) -> OSMRelation:
    return OSMRelation(
        id=relation_id,
        members=[
            {"type": "way", "ref": from_way_id, "role": "from"},
            {"type": "node", "ref": via_ref, "role": "via"},
            {"type": "way", "ref": to_way_id, "role": "to"},
        ],
        tags={"type": "restriction", "restriction": restriction_type},
    )


def _fully_connected_pair_graph(approaches: list[Approach]) -> RoadPairRelationGraph:
    relations: list[RoadPairRelation] = []
    for index, first in enumerate(approaches):
        for second in approaches[index + 1 :]:
            relation = _connected_pair_graph(first.approach_id, second.approach_id).relations[0]
            relations.append(
                relation.model_copy(
                    update={
                        "relation_id": f"{first.approach_id}_{second.approach_id}",
                        "road_a_id": first.approach_id,
                        "road_b_id": second.approach_id,
                        "road_a_source_way_ids": first.source_way_ids,
                        "road_b_source_way_ids": second.source_way_ids,
                    }
                )
            )
    return RoadPairRelationGraph(
        relations=relations,
        missing_connection_count=0,
        wrong_connection_count=0,
        overlap_conflict_count=0,
        near_miss_count=0,
        duplicate_parallel_count=0,
        blocking_error_count=0,
    )
