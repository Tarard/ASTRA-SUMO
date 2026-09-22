from pathlib import Path

from torii_sumo.intersection.infer_approaches import infer_approaches
from torii_sumo.intersection.infer_core import infer_intersection_core
from torii_sumo.intersection.infer_road_relations import build_road_pair_relation_graph
from torii_sumo.intersection.osm_patch import parse_osm_xml
from torii_sumo.intersection.schema import (
    Approach,
    BBox,
    IntersectionCore,
    OSMNode,
    OSMPatch,
    OSMWay,
)


FIXTURES = Path(__file__).parent / "fixtures"


def test_road_pair_relation_graph_detects_shared_node_connected_relations() -> None:
    patch = parse_osm_xml(FIXTURES / "x4_priority.osm.xml")
    core = infer_intersection_core(patch)
    approaches = infer_approaches(patch, core)

    graph = build_road_pair_relation_graph(patch, core, approaches)

    assert len(graph.relations) == 6
    assert {relation.geometry_relation for relation in graph.relations} == {"shared_node"}
    assert {relation.topology_relation for relation in graph.relations} == {"connected"}
    assert graph.blocking_error_count == 0


def test_road_pair_relation_graph_flags_near_miss_missing_connection() -> None:
    patch, core, approaches = _two_road_patch((0.0, 0.0), (4.0, 0.0))

    graph = build_road_pair_relation_graph(patch, core, approaches)

    relation = graph.relations[0]
    assert relation.geometry_relation == "near_miss"
    assert relation.topology_relation == "disconnected"
    assert relation.error_type == "missing_connection"
    assert relation.suggested_fix == "join_nodes"
    assert graph.missing_connection_count == 1


def test_road_pair_relation_graph_flags_crossing_without_node() -> None:
    patch = OSMPatch(
        nodes={
            "a": OSMNode(id="a", lat=0, lon=0, x=-1, y=0, tags={}),
            "b": OSMNode(id="b", lat=0, lon=0, x=1, y=0, tags={}),
            "c": OSMNode(id="c", lat=0, lon=0, x=0, y=-1, tags={}),
            "d": OSMNode(id="d", lat=0, lon=0, x=0, y=1, tags={}),
        },
        ways={
            "wa": OSMWay(id="wa", node_refs=["a", "b"], tags={"highway": "residential"}),
            "wb": OSMWay(id="wb", node_refs=["c", "d"], tags={"highway": "residential"}),
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
        _approach("a", "wa", 90),
        _approach("b", "wb", 0),
    ]

    graph = build_road_pair_relation_graph(patch, core, approaches)

    relation = graph.relations[0]
    assert relation.geometry_relation == "crossing_without_node"
    assert relation.error_type == "topology_overlap"
    assert relation.suggested_fix == "split_edge_at_crossing"
    assert graph.overlap_conflict_count == 1


def test_road_pair_relation_graph_uses_approach_shape_after_corridor_extension() -> None:
    patch = OSMPatch(
        nodes={
            "far_a": OSMNode(id="far_a", lat=0, lon=0, x=200, y=200, tags={}),
            "far_b": OSMNode(id="far_b", lat=0, lon=0, x=-200, y=-200, tags={}),
            "near_a": OSMNode(id="near_a", lat=0, lon=0, x=0, y=0, tags={}),
            "near_b": OSMNode(id="near_b", lat=0, lon=0, x=4, y=0, tags={}),
        },
        ways={
            "far_way_a": OSMWay(id="far_way_a", node_refs=["far_a"], tags={"highway": "residential"}),
            "near_way_a": OSMWay(id="near_way_a", node_refs=["near_a"], tags={"highway": "residential"}),
            "far_way_b": OSMWay(id="far_way_b", node_refs=["far_b"], tags={"highway": "residential"}),
            "near_way_b": OSMWay(id="near_way_b", node_refs=["near_b"], tags={"highway": "residential"}),
        },
        relations={},
        bbox=BBox(min_lon=0, min_lat=0, max_lon=0, max_lat=0),
    )
    core = IntersectionCore(
        core_id="core",
        center_xy=(0, 0),
        core_osm_node_ids=[],
        core_way_ids=["near_way_a", "near_way_b"],
        core_radius_m=20,
        topology_type="unknown",
        internal_fragment_count=0,
        short_internal_edge_count=0,
        confidence=0.5,
    )
    a = _approach("a", "far_way_a", 90).model_copy(
        update={"source_way_ids": ["far_way_a", "near_way_a"], "source_shape_xy": [(0.0, 0.0)]}
    )
    b = _approach("b", "far_way_b", 90).model_copy(
        update={"source_way_ids": ["far_way_b", "near_way_b"], "source_shape_xy": [(4.0, 0.0)]}
    )

    graph = build_road_pair_relation_graph(patch, core, [a, b])

    relation = graph.relations[0]
    assert relation.geometry_relation == "near_miss"
    assert relation.evidence == ["endpoint_gap_m:4.00"]


def test_road_pair_relation_graph_preserves_bridge_layer_crossing() -> None:
    patch = OSMPatch(
        nodes={
            "a": OSMNode(id="a", lat=0, lon=0, x=-1, y=0, tags={}),
            "b": OSMNode(id="b", lat=0, lon=0, x=1, y=0, tags={}),
            "c": OSMNode(id="c", lat=0, lon=0, x=0, y=-1, tags={}),
            "d": OSMNode(id="d", lat=0, lon=0, x=0, y=1, tags={}),
        },
        ways={
            "wa": OSMWay(id="wa", node_refs=["a", "b"], tags={"highway": "residential", "bridge": "yes", "layer": "1"}),
            "wb": OSMWay(id="wb", node_refs=["c", "d"], tags={"highway": "residential", "layer": "0"}),
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

    graph = build_road_pair_relation_graph(patch, core, [_approach("a", "wa", 90), _approach("b", "wb", 0)])

    relation = graph.relations[0]
    assert relation.geometry_relation == "crossing_without_node"
    assert relation.expected_relation == "should_not_connect"
    assert relation.suggested_fix == "preserve_separate_levels"
    assert "layer_separation:1/0" in relation.evidence


def _two_road_patch(a_xy, b_xy):
    patch = OSMPatch(
        nodes={
            "a": OSMNode(id="a", lat=0, lon=0, x=a_xy[0], y=a_xy[1], tags={}),
            "b": OSMNode(id="b", lat=0, lon=0, x=b_xy[0], y=b_xy[1], tags={}),
        },
        ways={
            "wa": OSMWay(id="wa", node_refs=["a"], tags={"highway": "residential", "name": "Main"}),
            "wb": OSMWay(id="wb", node_refs=["b"], tags={"highway": "residential", "name": "Main"}),
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
    return patch, core, [_approach("a", "wa", 90), _approach("b", "wb", 90)]


def _approach(approach_id: str, way_id: str, bearing: float) -> Approach:
    return Approach(
        approach_id=approach_id,
        role="leg_1",
        source_way_ids=[way_id],
        road_name="Main",
        highway_class="residential",
        bearing_to_core=(bearing + 180) % 360,
        bearing_from_core=bearing,
        incoming_lane_count=1,
        outgoing_lane_count=1,
        incoming_edge_ids=[],
        outgoing_edge_ids=[],
        oneway=False,
        allowed_modes={"passenger"},
        access_tags={},
    )
