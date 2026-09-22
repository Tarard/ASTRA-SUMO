from pathlib import Path

from torii_sumo.intersection.infer_approaches import infer_approaches
from torii_sumo.intersection.infer_core import infer_intersection_core
from torii_sumo.intersection.osm_patch import parse_osm_xml
from torii_sumo.intersection.schema import OSMNode, OSMWay, PatchSeed


FIXTURES = Path(__file__).parent / "fixtures"


def test_infer_core_and_approaches_for_t3_priority_fixture() -> None:
    patch = parse_osm_xml(FIXTURES / "t3_priority.osm.xml")
    core = infer_intersection_core(patch)
    approaches = infer_approaches(patch, core)

    assert core.core_osm_node_ids == ["1"]
    assert core.topology_type == "T3"
    assert len(approaches) == 3
    assert [approach.role for approach in approaches] == ["leg_1", "leg_2", "leg_3"]


def test_infer_core_and_approaches_for_x4_priority_fixture() -> None:
    patch = parse_osm_xml(FIXTURES / "x4_priority.osm.xml")
    core = infer_intersection_core(patch)
    approaches = infer_approaches(patch, core)

    assert core.core_osm_node_ids == ["1"]
    assert core.topology_type == "X4"
    assert {approach.role for approach in approaches} == {"north", "east", "south", "west"}
    assert {approach.highway_class for approach in approaches} == {"primary", "secondary"}


def test_infer_core_expands_seed_to_nearby_connected_cluster_boundary() -> None:
    patch = parse_osm_xml(FIXTURES / "clustered_signalized_crossing.osm.xml")
    core = infer_intersection_core(patch, PatchSeed(osm_node_id="seed"))
    approaches = infer_approaches(patch, core)

    assert set(core.core_osm_node_ids) == {"seed", "crossing", "vehicle_core"}
    assert core.topology_type == "X4"
    assert len(approaches) == 4
    assert {approach.highway_class for approach in approaches} == {"secondary", "tertiary", "residential"}
    assert all("path_a" not in approach.source_way_ids for approach in approaches)


def test_infer_approaches_represents_complex_cluster_boundary_without_crashing() -> None:
    patch = parse_osm_xml(FIXTURES / "clustered_signalized_crossing.osm.xml")
    patch.nodes["extra"] = OSMNode(id="extra", lat=48.00075, lon=11.00035, x=100.0, y=100.0)
    patch.ways["road_extra"] = OSMWay(
        id="road_extra",
        node_refs=["extra", "vehicle_core"],
        tags={"highway": "service"},
    )

    core = infer_intersection_core(patch, PatchSeed(osm_node_id="seed"))
    approaches = infer_approaches(patch, core)

    assert core.topology_type == "complex"
    assert len(approaches) == 5
    assert approaches[-1].role == "leg_5"


def test_infer_approaches_preserves_pedestrian_and_bicycle_modes() -> None:
    patch = parse_osm_xml(FIXTURES / "clustered_signalized_crossing.osm.xml")
    patch.nodes["bike"] = OSMNode(id="bike", lat=48.00075, lon=11.00035, x=100.0, y=100.0)
    patch.ways["cycleway_extra"] = OSMWay(
        id="cycleway_extra",
        node_refs=["bike", "vehicle_core"],
        tags={"highway": "cycleway", "foot": "no"},
    )
    patch.nodes["foot"] = OSMNode(id="foot", lat=48.00025, lon=11.00035, x=-100.0, y=-100.0)
    patch.ways["footway_extra"] = OSMWay(
        id="footway_extra",
        node_refs=["foot", "vehicle_core"],
        tags={"highway": "footway", "bicycle": "yes"},
    )

    core = infer_intersection_core(patch, PatchSeed(osm_node_id="seed"))
    approaches = infer_approaches(patch, core)
    modes_by_way = {approach.source_way_ids[0]: approach.allowed_modes for approach in approaches}

    assert modes_by_way["cycleway_extra"] == {"bicycle"}
    assert modes_by_way["footway_extra"] == {"bicycle", "pedestrian"}
    assert modes_by_way["road_ew"] == {"passenger"}


def test_infer_approaches_extends_vehicle_corridor_across_short_split_node() -> None:
    patch = parse_osm_xml(FIXTURES / "clustered_signalized_crossing.osm.xml")
    patch.nodes["outer"] = OSMNode(id="outer", lat=48.00055, lon=10.99950, x=-90.0, y=0.0)
    patch.ways["road_west_outer"] = OSMWay(
        id="road_west_outer",
        node_refs=["outer", "west"],
        tags={"highway": "secondary", "name": "Ring Road", "ref": "B 13", "lanes": "4"},
    )
    patch.ways["road_ew"].tags.update({"name": "Ring Road", "ref": "B 13"})

    core = infer_intersection_core(patch, PatchSeed(osm_node_id="seed"))
    approaches = infer_approaches(patch, core)
    west = next(approach for approach in approaches if "road_west_outer" in approach.source_way_ids)

    assert west.source_way_ids == ["road_ew", "road_west_outer"]
    assert west.incoming_edge_ids == ["road_west_outer_outer_to_core_seed"]
    assert west.outgoing_edge_ids == ["road_west_outer_core_seed_to_outer"]
    assert west.endpoint_xy == (-90.0, 0.0)
    assert west.source_shape_xy == [(-90.0, 0.0), (patch.nodes["west"].x, patch.nodes["west"].y), core.center_xy]


def test_infer_approaches_extends_vehicle_corridor_across_two_short_split_nodes() -> None:
    patch = parse_osm_xml(FIXTURES / "clustered_signalized_crossing.osm.xml")
    patch.nodes["outer_a"] = OSMNode(id="outer_a", lat=48.00055, lon=10.99950, x=-90.0, y=0.0)
    patch.nodes["outer_b"] = OSMNode(id="outer_b", lat=48.00055, lon=10.99920, x=-130.0, y=0.0)
    patch.ways["road_west_mid"] = OSMWay(id="road_west_mid", node_refs=["outer_a", "west"], tags={"highway": "secondary", "name": "Ring Road", "ref": "B 13"})
    patch.ways["road_west_outer"] = OSMWay(id="road_west_outer", node_refs=["outer_b", "outer_a"], tags={"highway": "secondary", "name": "Ring Road", "ref": "B 13"})
    patch.ways["road_ew"].tags.update({"name": "Ring Road", "ref": "B 13"})

    core = infer_intersection_core(patch, PatchSeed(osm_node_id="seed"))
    approaches = infer_approaches(patch, core)
    west = next(approach for approach in approaches if "road_west_outer" in approach.source_way_ids)

    assert west.endpoint_xy == (-130.0, 0.0)
    assert west.corridor_extension_way_ids == ["road_west_mid", "road_west_outer"]


def test_corridor_extension_stops_at_ambiguous_fork() -> None:
    patch = parse_osm_xml(FIXTURES / "clustered_signalized_crossing.osm.xml")
    patch.nodes["fork_a"] = OSMNode(id="fork_a", lat=48.00055, lon=10.99950, x=-90.0, y=0.0)
    patch.nodes["fork_b"] = OSMNode(id="fork_b", lat=48.00065, lon=10.99950, x=-90.0, y=30.0)
    patch.nodes["fork_c"] = OSMNode(id="fork_c", lat=48.00045, lon=10.99950, x=-90.0, y=-30.0)
    patch.ways["fork_one"] = OSMWay(id="fork_one", node_refs=["fork_a", "west"], tags={"highway": "secondary", "name": "Ring Road"})
    patch.ways["fork_two"] = OSMWay(id="fork_two", node_refs=["fork_b", "west"], tags={"highway": "secondary", "name": "Ring Road"})
    patch.ways["fork_three"] = OSMWay(id="fork_three", node_refs=["fork_c", "west"], tags={"highway": "secondary", "name": "Ring Road"})
    patch.ways["road_ew"].tags["name"] = "Ring Road"

    core = infer_intersection_core(patch, PatchSeed(osm_node_id="seed"))
    approaches = infer_approaches(patch, core)
    west = next(approach for approach in approaches if "road_ew" in approach.source_way_ids and approach.endpoint_xy and approach.endpoint_xy[0] < 0)

    assert west.source_way_ids == ["road_ew"]


def test_corridor_extension_stops_at_sharp_angle_single_candidate() -> None:
    patch = parse_osm_xml(FIXTURES / "clustered_signalized_crossing.osm.xml")
    patch.nodes["sharp"] = OSMNode(id="sharp", lat=48.00085, lon=10.99980, x=-40.0, y=120.0)
    patch.ways["sharp_same_name"] = OSMWay(id="sharp_same_name", node_refs=["sharp", "west"], tags={"highway": "secondary", "name": "Ring Road"})
    patch.ways["road_ew"].tags["name"] = "Ring Road"

    core = infer_intersection_core(patch, PatchSeed(osm_node_id="seed"))
    approaches = infer_approaches(patch, core)
    west = next(approach for approach in approaches if "road_ew" in approach.source_way_ids and approach.endpoint_xy and approach.endpoint_xy[0] < 0)

    assert not any("sharp_same_name" in approach.source_way_ids for approach in approaches)
    assert west.source_way_ids == ["road_ew"]
    assert west.corridor_extension_way_ids == []


def test_corridor_extension_stops_at_long_distance_single_candidate() -> None:
    patch = parse_osm_xml(FIXTURES / "clustered_signalized_crossing.osm.xml")
    patch.nodes["far"] = OSMNode(id="far", lat=48.00055, lon=10.99800, x=-300.0, y=0.0)
    patch.ways["far_same_name"] = OSMWay(id="far_same_name", node_refs=["far", "west"], tags={"highway": "secondary", "name": "Ring Road"})
    patch.ways["road_ew"].tags["name"] = "Ring Road"

    core = infer_intersection_core(patch, PatchSeed(osm_node_id="seed"))
    approaches = infer_approaches(patch, core)
    west = next(approach for approach in approaches if "road_ew" in approach.source_way_ids and approach.endpoint_xy and approach.endpoint_xy[0] < 0)

    assert not any("far_same_name" in approach.source_way_ids for approach in approaches)
    assert west.source_way_ids == ["road_ew"]
    assert west.corridor_extension_way_ids == []


def test_corridor_extension_uses_polyline_distance_guard_for_single_candidate() -> None:
    patch = parse_osm_xml(FIXTURES / "clustered_signalized_crossing.osm.xml")
    west_x = patch.nodes["west"].x or 0.0
    west_y = patch.nodes["west"].y or 0.0
    patch.nodes["poly_a"] = OSMNode(id="poly_a", lat=48.00055, lon=10.99980, x=west_x - 30.0, y=west_y)
    patch.nodes["poly_b"] = OSMNode(id="poly_b", lat=48.00105, lon=10.99980, x=west_x - 30.0, y=west_y + 60.0)
    patch.nodes["poly_c"] = OSMNode(id="poly_c", lat=48.00105, lon=10.99930, x=west_x - 70.0, y=west_y + 60.0)
    patch.nodes["poly_end"] = OSMNode(id="poly_end", lat=48.00055, lon=10.99930, x=west_x - 70.0, y=west_y)
    patch.ways["polyline_long"] = OSMWay(
        id="polyline_long",
        node_refs=["west", "poly_a", "poly_b", "poly_c", "poly_end"],
        tags={"highway": "secondary", "name": "Ring Road"},
    )
    patch.ways["road_ew"].tags["name"] = "Ring Road"

    core = infer_intersection_core(patch, PatchSeed(osm_node_id="seed"))
    approaches = infer_approaches(patch, core)
    west = next(approach for approach in approaches if "road_ew" in approach.source_way_ids and approach.endpoint_xy and approach.endpoint_xy[0] < 0)

    assert not any("polyline_long" in approach.source_way_ids for approach in approaches)
    assert west.source_way_ids == ["road_ew"]
    assert west.corridor_extension_way_ids == []


def test_corridor_extension_uses_first_segment_heading_guard_for_single_candidate() -> None:
    patch = parse_osm_xml(FIXTURES / "clustered_signalized_crossing.osm.xml")
    west_x = patch.nodes["west"].x or 0.0
    west_y = patch.nodes["west"].y or 0.0
    patch.nodes["turn_a"] = OSMNode(id="turn_a", lat=48.00085, lon=10.99900, x=west_x, y=west_y + 35.0)
    patch.nodes["turn_end"] = OSMNode(id="turn_end", lat=48.00055, lon=10.99930, x=west_x - 70.0, y=west_y)
    patch.ways["first_segment_turn"] = OSMWay(
        id="first_segment_turn",
        node_refs=["west", "turn_a", "turn_end"],
        tags={"highway": "secondary", "name": "Ring Road"},
    )
    patch.ways["road_ew"].tags["name"] = "Ring Road"

    core = infer_intersection_core(patch, PatchSeed(osm_node_id="seed"))
    approaches = infer_approaches(patch, core)
    west = next(approach for approach in approaches if "road_ew" in approach.source_way_ids and approach.endpoint_xy and approach.endpoint_xy[0] < 0)

    assert not any("first_segment_turn" in approach.source_way_ids for approach in approaches)
    assert west.source_way_ids == ["road_ew"]
    assert west.corridor_extension_way_ids == []


def test_corridor_extension_stops_before_distinct_way_cycle_revisits_path_node() -> None:
    patch = parse_osm_xml(FIXTURES / "clustered_signalized_crossing.osm.xml")
    west_x = patch.nodes["west"].x or 0.0
    west_y = patch.nodes["west"].y or 0.0
    patch.nodes["cycle_mid"] = OSMNode(id="cycle_mid", lat=48.00055, lon=10.99980, x=west_x - 20.0, y=west_y)
    patch.nodes["cycle_a"] = OSMNode(id="cycle_a", lat=48.00055, lon=10.99960, x=west_x - 40.0, y=west_y)
    patch.nodes["cycle_b_mid"] = OSMNode(id="cycle_b_mid", lat=48.00055, lon=10.99940, x=west_x - 60.0, y=west_y)
    patch.nodes["cycle_end"] = OSMNode(id="cycle_end", lat=48.00055, lon=10.99930, x=west_x - 70.0, y=west_y)
    patch.ways["road_cycle_a"] = OSMWay(
        id="road_cycle_a",
        node_refs=["west", "cycle_mid", "cycle_a"],
        tags={"highway": "secondary", "name": "Ring Road"},
    )
    patch.ways["road_cycle_b"] = OSMWay(
        id="road_cycle_b",
        node_refs=["cycle_a", "cycle_b_mid", "cycle_mid", "cycle_end"],
        tags={"highway": "secondary", "name": "Ring Road"},
    )
    patch.ways["road_ew"].tags["name"] = "Ring Road"

    core = infer_intersection_core(patch, PatchSeed(osm_node_id="seed"))
    approaches = infer_approaches(patch, core)
    west = next(approach for approach in approaches if "road_cycle_a" in approach.source_way_ids)

    assert not any("road_cycle_b" in approach.source_way_ids for approach in approaches)
    assert west.source_way_ids == ["road_ew", "road_cycle_a"]
    assert west.corridor_extension_way_ids == ["road_cycle_a"]
    assert west.endpoint_xy == (west_x - 40.0, west_y)


def test_infer_approaches_uses_directional_lane_counts_and_turn_lanes() -> None:
    patch = parse_osm_xml(FIXTURES / "x4_signalized.osm.xml")
    patch.ways["10"].tags.update(
        {
            "lanes": "3",
            "lanes:forward": "2",
            "turn:lanes:forward": "left|through",
            "turn:lanes:backward": "right",
        }
    )

    core = infer_intersection_core(patch)
    approaches = infer_approaches(patch, core)
    north = next(approach for approach in approaches if approach.endpoint_xy and approach.endpoint_xy[1] > 0)
    south = next(approach for approach in approaches if approach.endpoint_xy and approach.endpoint_xy[1] < 0)

    assert north.incoming_lane_count == 2
    assert north.outgoing_lane_count == 1
    assert north.turn_lanes_raw == "left|through"
    assert south.incoming_lane_count == 1
    assert south.outgoing_lane_count == 2
    assert south.turn_lanes_raw == "right"


def test_infer_approaches_marks_oneway_toward_core_as_incoming_only() -> None:
    patch = parse_osm_xml(FIXTURES / "x4_signalized.osm.xml")
    patch.ways["10"].tags["oneway"] = "yes"

    core = infer_intersection_core(patch)
    approaches = infer_approaches(patch, core)
    north = next(approach for approach in approaches if approach.endpoint_xy and approach.endpoint_xy[1] > 0)

    assert north.has_incoming_vehicle_flow is True
    assert north.has_outgoing_vehicle_flow is False
    assert "oneway:forward_toward_core" in north.direction_evidence


def test_infer_approaches_marks_reverse_oneway_away_from_core_as_outgoing_only() -> None:
    patch = parse_osm_xml(FIXTURES / "x4_signalized.osm.xml")
    patch.ways["10"].tags["oneway"] = "-1"

    core = infer_intersection_core(patch)
    approaches = infer_approaches(patch, core)
    north = next(approach for approach in approaches if approach.endpoint_xy and approach.endpoint_xy[1] > 0)

    assert north.oneway is True
    assert north.has_incoming_vehicle_flow is False
    assert north.has_outgoing_vehicle_flow is True
    assert north.direction_evidence == ["oneway:forward_away_from_core"]


def test_infer_approaches_assumes_unknown_oneway_toward_core() -> None:
    patch = parse_osm_xml(FIXTURES / "x4_signalized.osm.xml")
    patch.ways["10"].tags["oneway"] = "reversible"

    core = infer_intersection_core(patch)
    approaches = infer_approaches(patch, core)
    north = next(approach for approach in approaches if approach.endpoint_xy and approach.endpoint_xy[1] > 0)

    assert north.oneway is True
    assert north.has_incoming_vehicle_flow is True
    assert north.has_outgoing_vehicle_flow is False
    assert north.direction_evidence == ["oneway:unknown_reversible_assumed_toward_core"]


def test_infer_approaches_uses_core_adjacent_oneway_for_extended_corridor_flow() -> None:
    patch = parse_osm_xml(FIXTURES / "x4_signalized.osm.xml")
    core = infer_intersection_core(patch)
    patch.nodes["99"] = OSMNode(id="99", lat=48.0015, lon=11.0005, x=0.0, y=110.0)
    patch.ways["10"].tags.update({"name": "Main Road", "oneway": "yes"})
    patch.ways["99"] = OSMWay(
        id="99",
        node_refs=["99", "2"],
        tags={"highway": "primary", "name": "Main Road"},
    )

    approaches = infer_approaches(patch, core)
    north = next(approach for approach in approaches if approach.endpoint_xy and approach.endpoint_xy[1] == 110.0)

    assert north.source_way_ids == ["10", "99"]
    assert north.has_incoming_vehicle_flow is True
    assert north.has_outgoing_vehicle_flow is False
    assert north.direction_evidence == ["oneway:forward_toward_core"]


def test_infer_approaches_uses_reverse_core_adjacent_oneway_for_extended_corridor_flow() -> None:
    patch = parse_osm_xml(FIXTURES / "x4_signalized.osm.xml")
    core = infer_intersection_core(patch)
    patch.nodes["99"] = OSMNode(id="99", lat=48.0015, lon=11.0005, x=0.0, y=110.0)
    patch.ways["10"].tags.update({"name": "Main Road", "oneway": "-1"})
    patch.ways["99"] = OSMWay(
        id="99",
        node_refs=["99", "2"],
        tags={"highway": "primary", "name": "Main Road"},
    )

    approaches = infer_approaches(patch, core)
    north = next(approach for approach in approaches if approach.endpoint_xy and approach.endpoint_xy[1] == 110.0)

    assert north.source_way_ids == ["10", "99"]
    assert north.oneway is True
    assert north.has_incoming_vehicle_flow is False
    assert north.has_outgoing_vehicle_flow is True
    assert north.direction_evidence == ["oneway:forward_away_from_core"]


def test_infer_approaches_uses_outer_oneway_for_extended_corridor_flow() -> None:
    patch = parse_osm_xml(FIXTURES / "x4_signalized.osm.xml")
    core = infer_intersection_core(patch)
    patch.nodes["99"] = OSMNode(id="99", lat=48.0015, lon=11.0005, x=0.0, y=110.0)
    patch.ways["10"].tags.update({"name": "Main Road"})
    patch.ways["99"] = OSMWay(
        id="99",
        node_refs=["99", "2"],
        tags={"highway": "primary", "name": "Main Road", "oneway": "yes"},
    )

    approaches = infer_approaches(patch, core)
    north = next(approach for approach in approaches if approach.endpoint_xy and approach.endpoint_xy[1] == 110.0)

    assert north.source_way_ids == ["10", "99"]
    assert north.has_incoming_vehicle_flow is True
    assert north.has_outgoing_vehicle_flow is False
    assert north.direction_evidence == ["oneway:forward_toward_core"]


def test_infer_approaches_filters_access_tags_through_road_semantics() -> None:
    patch = parse_osm_xml(FIXTURES / "clustered_signalized_crossing.osm.xml")
    patch.nodes["vehicle_blocked"] = OSMNode(id="vehicle_blocked", lat=48.00075, lon=11.00035, x=100.0, y=100.0)
    patch.ways["vehicle_blocked"] = OSMWay(
        id="vehicle_blocked",
        node_refs=["vehicle_blocked", "vehicle_core"],
        tags={"highway": "service", "vehicle": "no"},
    )
    patch.nodes["cycle_blocked"] = OSMNode(id="cycle_blocked", lat=48.00025, lon=11.00035, x=-100.0, y=-100.0)
    patch.ways["cycle_blocked"] = OSMWay(
        id="cycle_blocked",
        node_refs=["cycle_blocked", "vehicle_core"],
        tags={"highway": "cycleway", "bicycle": "no", "foot": "yes"},
    )
    patch.nodes["foot_blocked"] = OSMNode(id="foot_blocked", lat=48.00085, lon=11.00035, x=100.0, y=-100.0)
    patch.ways["foot_blocked"] = OSMWay(
        id="foot_blocked",
        node_refs=["foot_blocked", "vehicle_core"],
        tags={"highway": "footway", "foot": "no", "bicycle": "yes"},
    )

    core = infer_intersection_core(patch, PatchSeed(osm_node_id="seed"))
    approaches = infer_approaches(patch, core)
    modes_by_way = {approach.source_way_ids[0]: approach.allowed_modes for approach in approaches}

    assert modes_by_way["vehicle_blocked"] == set()
    assert modes_by_way["cycle_blocked"] == {"pedestrian"}
    assert modes_by_way["foot_blocked"] == {"bicycle"}


def test_infer_approaches_preserves_motor_vehicle_positive_override_in_access_tags() -> None:
    patch = parse_osm_xml(FIXTURES / "clustered_signalized_crossing.osm.xml")
    patch.nodes["override"] = OSMNode(id="override", lat=48.00075, lon=11.00035, x=100.0, y=100.0)
    patch.ways["override"] = OSMWay(
        id="override",
        node_refs=["override", "vehicle_core"],
        tags={"highway": "service", "access": "no", "motor_vehicle": "yes"},
    )

    core = infer_intersection_core(patch, PatchSeed(osm_node_id="seed"))
    approaches = infer_approaches(patch, core)
    override = next(approach for approach in approaches if approach.source_way_ids == ["override"])

    assert override.allowed_modes == {"passenger"}
    assert override.access_tags == {"access": "no", "motor_vehicle": "yes"}


def test_infer_approaches_turns_vehicle_way_sidewalk_and_cycleway_tags_into_support_lanes() -> None:
    patch = parse_osm_xml(FIXTURES / "x4_signalized.osm.xml")
    patch.ways["10"].tags.update({"sidewalk": "separate", "cycleway:both": "track"})

    core = infer_intersection_core(patch)
    approaches = infer_approaches(patch, core)
    way_10_approaches = [approach for approach in approaches if approach.source_way_ids == ["10"]]

    assert way_10_approaches
    assert all({"bicycle", "pedestrian"} in approach.incoming_extra_lane_modes for approach in way_10_approaches)
    assert all({"bicycle", "pedestrian"} in approach.outgoing_extra_lane_modes for approach in way_10_approaches)


def test_infer_approaches_fuses_support_path_at_crossing_terminal() -> None:
    patch = parse_osm_xml(FIXTURES / "clustered_signalized_crossing.osm.xml")
    patch.nodes["west"].tags = {"highway": "crossing", "crossing": "traffic_signals"}
    patch.nodes["side_path"] = OSMNode(id="side_path", lat=48.00055, lon=10.99950, x=-90.0, y=0.0)
    patch.ways["path_side"] = OSMWay(
        id="path_side",
        node_refs=["side_path", "west"],
        tags={"highway": "path", "foot": "designated", "bicycle": "designated"},
    )

    core = infer_intersection_core(patch, PatchSeed(osm_node_id="seed"))
    approaches = infer_approaches(patch, core)
    west = next(
        approach
        for approach in approaches
        if "road_ew" in approach.source_way_ids and approach.endpoint_xy and approach.endpoint_xy[0] < 0
    )

    assert not [approach for approach in approaches if approach.source_way_ids == ["path_side"]]
    assert west.incoming_extra_lane_modes == [{"bicycle", "pedestrian"}]
    assert west.outgoing_extra_lane_modes == [{"bicycle", "pedestrian"}]


def test_infer_approaches_fuses_both_sides_of_support_path_crossing() -> None:
    patch = parse_osm_xml(FIXTURES / "clustered_signalized_crossing.osm.xml")
    patch.nodes["west"].tags = {"highway": "crossing", "crossing": "traffic_signals"}
    patch.nodes["side_a"] = OSMNode(id="side_a", lat=48.00055, lon=10.99950, x=-90.0, y=0.0)
    patch.nodes["side_b"] = OSMNode(id="side_b", lat=48.00055, lon=10.99980, x=-40.0, y=0.0)
    patch.ways["path_crossing"] = OSMWay(
        id="path_crossing",
        node_refs=["side_a", "west", "side_b"],
        tags={"highway": "path", "foot": "designated", "bicycle": "designated"},
    )

    core = infer_intersection_core(patch, PatchSeed(osm_node_id="seed"))
    approaches = infer_approaches(patch, core)
    west = next(
        approach
        for approach in approaches
        if "road_ew" in approach.source_way_ids and approach.endpoint_xy and approach.endpoint_xy[0] < 0
    )

    assert not [approach for approach in approaches if approach.source_way_ids == ["path_crossing"]]
    assert west.incoming_extra_lane_modes == [{"bicycle", "pedestrian"}]
    assert west.outgoing_extra_lane_modes == [{"bicycle", "pedestrian"}]


def test_infer_approaches_fuses_crossing_support_path_into_vehicle_lanes() -> None:
    patch = parse_osm_xml(FIXTURES / "clustered_signalized_crossing.osm.xml")
    patch.nodes["west"].tags = {"highway": "crossing", "crossing": "traffic_signals"}
    patch.nodes["side_path"] = OSMNode(id="side_path", lat=48.00055, lon=10.99950, x=-90.0, y=0.0)
    patch.ways["path_side"] = OSMWay(
        id="path_side",
        node_refs=["side_path", "west"],
        tags={"highway": "path", "foot": "designated", "bicycle": "designated"},
    )

    core = infer_intersection_core(patch, PatchSeed(osm_node_id="seed"))
    approaches = infer_approaches(patch, core)
    west = next(
        approach
        for approach in approaches
        if "road_ew" in approach.source_way_ids and approach.endpoint_xy and approach.endpoint_xy[0] < 0
    )

    assert not [approach for approach in approaches if approach.source_way_ids == ["path_side"]]
    assert {"bicycle", "pedestrian"} in west.incoming_extra_lane_modes
    assert {"bicycle", "pedestrian"} in west.outgoing_extra_lane_modes
