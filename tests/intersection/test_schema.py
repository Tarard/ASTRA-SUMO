import json

from torii_sumo.intersection.schema import (
    Approach,
    BBox,
    CompiledSUMOArtifacts,
    ControlModel,
    IntersectionCore,
    IntersectionIR,
    IntersectionValidation,
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
    TLSPhase,
)
from torii_sumo.road_semantics import classify_approach_mode_layer


def test_classify_approach_mode_layer_marks_bicycle_as_support_only() -> None:
    classification = classify_approach_mode_layer({"bicycle"}, [], [])

    assert classification.mode_layer == "support"
    assert classification.is_support_only is True
    assert classification.is_vehicle_approach is False
    assert [set(modes) for modes in classification.fused_support_modes] == []


def test_classify_approach_mode_layer_marks_passenger_with_support_lanes_as_fused() -> None:
    classification = classify_approach_mode_layer(
        {"passenger"},
        [{"bicycle", "pedestrian"}],
        [{"bicycle"}],
    )

    assert classification.mode_layer == "fused_support_lane"
    assert classification.is_vehicle_approach is True
    assert classification.is_support_only is False
    assert [set(modes) for modes in classification.fused_support_modes] == [
        {"bicycle", "pedestrian"},
        {"bicycle"},
    ]


def test_intersection_ir_models_dump_json_ready_schema() -> None:
    patch = OSMPatch(
        nodes={"n0": OSMNode(id="n0", lat=48.0, lon=11.0, x=0.0, y=0.0, tags={})},
        ways={"w0": OSMWay(id="w0", node_refs=["n0"], tags={"highway": "residential"})},
        relations={"r0": OSMRelation(id="r0", members=[], tags={})},
        bbox=BBox(min_lon=10.9, min_lat=47.9, max_lon=11.1, max_lat=48.1),
        seed=PatchSeed(osm_node_id="n0"),
    )
    core = IntersectionCore(
        core_id="core_n0",
        center_xy=(0.0, 0.0),
        center_latlon=(48.0, 11.0),
        core_osm_node_ids=["n0"],
        core_way_ids=["w0"],
        core_radius_m=20.0,
        topology_type="T3",
        internal_fragment_count=0,
        short_internal_edge_count=0,
        confidence=0.9,
    )
    approach = Approach(
        approach_id="leg_1",
        role="leg_1",
        source_way_ids=["w0"],
        road_name=None,
        highway_class="residential",
        bearing_to_core=180.0,
        bearing_from_core=0.0,
        incoming_lane_count=1,
        outgoing_lane_count=1,
        incoming_edge_ids=["w0_in"],
        outgoing_edge_ids=["w0_out"],
        oneway=False,
        allowed_modes={"passenger"},
        turn_lanes_raw=None,
        access_tags={},
    )
    relation = RoadPairRelation(
        relation_id="leg_1_leg_2",
        road_a_id="leg_1",
        road_b_id="leg_2",
        road_a_source_way_ids=["w0"],
        road_b_source_way_ids=["w1"],
        geometry_relation="shared_node",
        topology_relation="connected",
        expected_relation="should_connect",
        angle=RoadPairAngle(
            road_a_bearing_deg=0.0,
            road_b_bearing_deg=90.0,
            signed_delta_deg=90.0,
            abs_delta_deg=90.0,
            relation_class="right_angle",
            turn_angle_from_a_to_b_deg=90.0,
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
        inferred_turn="right",
        error_type="none",
        suggested_fix="none",
        confidence=1.0,
        evidence=["shared_node:n0"],
    )
    movement = Movement(
        movement_id="m1",
        from_approach_id="leg_1",
        to_approach_id="leg_2",
        road_pair_relation_id=relation.relation_id,
        turn="right",
        allowed=True,
        from_lane_indices=[0],
        to_lane_indices=[0],
        allowed_modes={"passenger"},
        evidence=["relation:leg_1_leg_2"],
        confidence=0.8,
        notes=[],
    )
    control = ControlModel(
        control_type="traffic_light",
        source=["osm:highway=traffic_signals"],
        priority_approach_ids=[],
        tls_id="core_n0",
        phases=[TLSPhase(phase_id="p0", duration=30.0, state="Gr", movement_ids=["m1"])],
        link_index_map={"m1": 0},
        confidence=0.9,
    )
    validation = IntersectionValidation(
        status="pass",
        sumo_load_status="pass",
        route_probe_status="skipped",
        approach_count=1,
        movement_count=1,
        missing_movement_count=0,
        forbidden_movement_count=0,
        internal_fragment_count=0,
        duplicate_junction_count=0,
        disconnected_edge_count=0,
        tls_linkindex_status="pass",
        warnings=[],
    )

    ir = IntersectionIR(
        intersection_id="core_n0",
        osm_patch=patch,
        core=core,
        approaches=[approach],
        road_pair_graph=RoadPairRelationGraph(
            relations=[relation],
            missing_connection_count=0,
            wrong_connection_count=0,
            overlap_conflict_count=0,
            near_miss_count=0,
            duplicate_parallel_count=0,
            blocking_error_count=0,
        ),
        movement_matrix=MovementMatrix(
            movements=[movement],
            legal_movement_count=1,
            forbidden_movement_count=0,
            inferred_movement_count=1,
            restriction_blocked_count=0,
        ),
        control=control,
        compiled=CompiledSUMOArtifacts(
            plain_node_file="out.nod.xml",
            plain_edge_file="out.edg.xml",
            plain_connection_file="out.con.xml",
            plain_type_file="out.typ.xml",
            plain_tllogic_file="out.tll.xml",
            net_file="out.net.xml",
            sumocfg_file=None,
        ),
        validation=validation,
        claim_status="intersection-cleaned",
    )

    dumped = ir.model_dump(mode="json")

    assert dumped["schema_version"] == "intersection-ir/v1"
    assert dumped["approaches"][0]["mode_layer"] == "vehicle"
    assert dumped["approaches"][0]["is_vehicle_approach"] is True
    assert dumped["approaches"][0]["is_support_only"] is False
    assert dumped["approaches"][0]["fused_support_modes"] == []
    assert dumped["approaches"][0]["has_incoming_vehicle_flow"] is True
    assert dumped["approaches"][0]["has_outgoing_vehicle_flow"] is True
    assert dumped["approaches"][0]["direction_evidence"] == []
    assert dumped["road_pair_graph"]["relations"][0]["severity"] == "none"
    assert dumped["road_pair_graph"]["relations"][0]["evidence"] == ["shared_node:n0"]
    assert dumped["movement_matrix"]["movements"][0]["road_pair_relation_id"] == "leg_1_leg_2"


def test_intersection_ir_accepts_v1_missing_new_schema_fields() -> None:
    patch = OSMPatch(
        nodes={"n0": OSMNode(id="n0", lat=48.0, lon=11.0, x=0.0, y=0.0, tags={})},
        ways={"w0": OSMWay(id="w0", node_refs=["n0"], tags={"highway": "residential"})},
        relations={"r0": OSMRelation(id="r0", members=[], tags={})},
        bbox=BBox(min_lon=10.9, min_lat=47.9, max_lon=11.1, max_lat=48.1),
        seed=PatchSeed(osm_node_id="n0"),
    )
    core = IntersectionCore(
        core_id="core_n0",
        center_xy=(0.0, 0.0),
        center_latlon=(48.0, 11.0),
        core_osm_node_ids=["n0"],
        core_way_ids=["w0"],
        core_radius_m=20.0,
        topology_type="T3",
        internal_fragment_count=0,
        short_internal_edge_count=0,
        confidence=0.9,
    )
    approach = Approach(
        approach_id="leg_1",
        role="leg_1",
        source_way_ids=["w0"],
        road_name=None,
        highway_class="residential",
        bearing_to_core=180.0,
        bearing_from_core=0.0,
        incoming_lane_count=1,
        outgoing_lane_count=1,
        incoming_edge_ids=["w0_in"],
        outgoing_edge_ids=["w0_out"],
        oneway=False,
        allowed_modes={"passenger"},
        turn_lanes_raw=None,
        access_tags={},
    )
    relation = RoadPairRelation(
        relation_id="leg_1_leg_2",
        road_a_id="leg_1",
        road_b_id="leg_2",
        road_a_source_way_ids=["w0"],
        road_b_source_way_ids=["w1"],
        geometry_relation="shared_node",
        topology_relation="connected",
        expected_relation="should_connect",
        angle=RoadPairAngle(
            road_a_bearing_deg=0.0,
            road_b_bearing_deg=90.0,
            signed_delta_deg=90.0,
            abs_delta_deg=90.0,
            relation_class="right_angle",
            turn_angle_from_a_to_b_deg=90.0,
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
        inferred_turn="right",
        error_type="none",
        suggested_fix="none",
        confidence=1.0,
        evidence=["shared_node:n0"],
    )
    movement = Movement(
        movement_id="m1",
        from_approach_id="leg_1",
        to_approach_id="leg_2",
        road_pair_relation_id=relation.relation_id,
        turn="right",
        allowed=True,
        from_lane_indices=[0],
        to_lane_indices=[0],
        allowed_modes={"passenger"},
        evidence=["relation:leg_1_leg_2"],
        confidence=0.8,
        notes=[],
    )
    ir = IntersectionIR(
        intersection_id="core_n0",
        osm_patch=patch,
        core=core,
        approaches=[approach],
        road_pair_graph=RoadPairRelationGraph(
            relations=[relation],
            missing_connection_count=0,
            wrong_connection_count=0,
            overlap_conflict_count=0,
            near_miss_count=0,
            duplicate_parallel_count=0,
            blocking_error_count=0,
        ),
        movement_matrix=MovementMatrix(
            movements=[movement],
            legal_movement_count=1,
            forbidden_movement_count=0,
            inferred_movement_count=1,
            restriction_blocked_count=0,
        ),
        control=ControlModel(
            control_type="traffic_light",
            source=["osm:highway=traffic_signals"],
            priority_approach_ids=[],
            tls_id="core_n0",
            phases=[TLSPhase(phase_id="p0", duration=30.0, state="Gr", movement_ids=["m1"])],
            link_index_map={"m1": 0},
            confidence=0.9,
        ),
        compiled=CompiledSUMOArtifacts(
            plain_node_file="out.nod.xml",
            plain_edge_file="out.edg.xml",
            plain_connection_file="out.con.xml",
            plain_type_file="out.typ.xml",
            plain_tllogic_file="out.tll.xml",
            net_file="out.net.xml",
            sumocfg_file=None,
        ),
        validation=IntersectionValidation(
            status="pass",
            sumo_load_status="pass",
            route_probe_status="skipped",
            approach_count=1,
            movement_count=1,
            missing_movement_count=0,
            forbidden_movement_count=0,
            internal_fragment_count=0,
            duplicate_junction_count=0,
            disconnected_edge_count=0,
            tls_linkindex_status="pass",
            warnings=[],
        ),
        claim_status="intersection-cleaned",
    )
    legacy_dump = ir.model_dump(mode="json")
    assert "restriction_blocked_count" in legacy_dump["movement_matrix"]
    assert "tls_linkindex_status" in legacy_dump["validation"]
    del legacy_dump["movement_matrix"]["restriction_blocked_count"]
    del legacy_dump["validation"]["tls_linkindex_status"]

    loaded = IntersectionIR.model_validate_json(json.dumps(legacy_dump))

    assert loaded.movement_matrix.restriction_blocked_count == 0
    assert loaded.validation is not None
    assert loaded.validation.tls_linkindex_status == "skipped"
