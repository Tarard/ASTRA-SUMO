from __future__ import annotations

from torii_sumo.intersection.road_detail import (
    _road_network_identity,
    classify_intersection_road_detail,
)
from torii_sumo.intersection.schema import BBox, OSMNode, OSMPatch, OSMWay


def test_unknown_authoritative_fields_do_not_suppress_the_osm_fallback() -> None:
    way = OSMWay(
        id="candidate-way",
        node_refs=["a", "b"],
        tags={"highway": "primary"},
    )

    identity = _road_network_identity(
        [way],
        way_ids=[way.id],
        road_network_evidence={
            "by_way_id": {
                way.id: {
                    "authority_category": "unknown",
                    "network_role": "unknown",
                    "functional_category": "",
                    "source_evidence_id": "reviewed-but-unknown",
                }
            }
        },
        fallback_osm_class="primary",
    )

    assert identity["network_role"]["value"] == "arterial"
    assert identity["network_role"]["status"] == "rule_derived"
    assert identity["resolution"] == "osm_fallback"


def test_official_road_identity_precedes_osm_fallback_and_connection_uses_arm_roles() -> None:
    patch = OSMPatch(
        nodes={
            "center": OSMNode(id="center", lat=0.0, lon=0.0, x=0.0, y=0.0),
            "a": OSMNode(id="a", lat=0.0, lon=0.0, x=-10.0, y=0.0),
            "b": OSMNode(id="b", lat=0.0, lon=0.0, x=10.0, y=0.0),
            "c": OSMNode(id="c", lat=0.0, lon=0.0, x=0.0, y=-10.0),
        },
        ways={
            "main-in": OSMWay(
                id="main-in",
                node_refs=["a", "center"],
                tags={
                    "highway": "primary",
                    "name": "Example Main Street",
                    "ref": "B 42",
                    "oneway": "yes",
                    "lanes": "2",
                    "turn:lanes": "left|through",
                },
            ),
            "main-out": OSMWay(
                id="main-out",
                node_refs=["center", "b"],
                tags={"highway": "primary", "name": "Example Main Street", "ref": "B 42", "oneway": "yes", "lanes": "2"},
            ),
            "side": OSMWay(
                id="side",
                node_refs=["c", "center"],
                tags={"highway": "service", "oneway": "yes"},
            ),
        },
        relations={},
        bbox=BBox(min_lon=-1.0, min_lat=-1.0, max_lon=1.0, max_lat=1.0),
    )
    physical_cell = {
        "hypothesis_id": "cell-1",
        "physical_approaches": [
            {
                "physical_approach_id": "arm-main",
                "source_way_ids": ["main-in", "main-out"],
                "member_boundary_port_ids": ["port-main-in", "port-main-out"],
                "flow_roles": ["incoming", "outgoing"],
                "member_count": 2,
                "incoming_lane_count": 2,
                "outgoing_lane_count": 2,
                "incoming_turn_lanes_raw": "left|through",
                "bearing_from_seed_deg": 90.0,
            },
            {
                "physical_approach_id": "arm-side",
                "source_way_ids": ["side"],
                "member_boundary_port_ids": ["port-side"],
                "flow_roles": ["incoming"],
                "member_count": 1,
                "incoming_lane_count": 1,
                "outgoing_lane_count": 0,
                "incoming_turn_lanes_raw": None,
                "bearing_from_seed_deg": 180.0,
            },
        ],
    }
    topology = {
        "physical_cell_hypothesis_id": "cell-1",
        "topology_evidence_id": "topology-1",
        "path_closure_node_ids": ["center"],
        "morphology": "single_conflict_center",
        "branch_connectors": [],
        "storage_capable_connectors": [],
    }
    movement = {
        "parent_physical_cell_hypothesis_id": "cell-1",
        "hypothesis_set_id": "movement-1",
        "consensus_variant_id": "variant-1",
        "variant_comparison": {"status": "exact"},
        "variants": [
            {
                "variant_id": "variant-1",
                "atomic_movements": [
                    {
                        "stable_movement_id": "movement-1",
                        "from_physical_approach_id": "arm-main",
                        "to_physical_approach_id": "arm-side",
                        "turn": "right",
                        "from_lane_index": 0,
                        "to_lane_index": 0,
                    }
                ],
            }
        ],
    }
    result = classify_intersection_road_detail(
        patch,
        physical_cell,
        arm_model={"through_pairs": []},
        topology_evidence=topology,
        movement_hypotheses=movement,
        road_network_evidence={
            "by_way_id": {
                "main-in": {
                    "authority_category": "hvs",
                    "network_role": "arterial",
                    "functional_category": "HS III",
                    "source_evidence_id": "hvs-1",
                },
                "main-out": {
                    "authority_category": "hvs",
                    "network_role": "arterial",
                    "functional_category": "HS III",
                    "source_evidence_id": "hvs-1",
                },
            }
        },
    )

    main = next(item for item in result["road_arms"] if item["physical_approach_id"] == "arm-main")
    side = next(item for item in result["road_arms"] if item["physical_approach_id"] == "arm-side")
    assert main["road_identity"]["resolution"] == "authoritative"
    assert main["road_identity"]["network_role"]["value"] == "arterial"
    assert main["road_identity"]["authority_category"]["value"] == "hvs"
    assert main["road_identity"]["osm_label_evidence"] == {
        "names": ["Example Main Street"],
        "refs": ["B 42"],
        "coherence": "single_named",
        "status": "observed",
        "decision": "review_required",
        "evidence_ids": ["main-in", "main-out"],
        "rationale": (
            "OSM name/ref labels identify a candidate road corridor for review; they do not prove "
            "official-link equivalence, physical continuity, legal movements, or signal ownership."
        ),
    }
    assert main["source_way_identity_evidence"] == [
        {
            "way_id": "main-in",
            "name": "Example Main Street",
            "ref": "B 42",
            "highway": "primary",
            "oneway": "yes",
            "lanes": "2",
            "turn_lanes": "left|through",
        },
        {
            "way_id": "main-out",
            "name": "Example Main Street",
            "ref": "B 42",
            "highway": "primary",
            "oneway": "yes",
            "lanes": "2",
            "turn_lanes": None,
        },
    ]
    assert side["road_identity"]["resolution"] == "osm_fallback"
    assert side["road_identity"]["network_role"]["value"] == "access"
    assert any(item["type"] == "turn_bay_candidate" for item in result["channelization"])
    assert result["connection_relations"][0]["relation"] == "access_to_network"
    assert result["automatic_promotion_gate"] == "blocked"


def test_conflicting_authoritative_way_records_abstain() -> None:
    patch = OSMPatch(
        nodes={"center": OSMNode(id="center", lat=0.0, lon=0.0, x=0.0, y=0.0)},
        ways={
            "a": OSMWay(id="a", node_refs=["center"], tags={"highway": "primary"}),
            "b": OSMWay(id="b", node_refs=["center"], tags={"highway": "secondary"}),
        },
        relations={},
        bbox=BBox(min_lon=0.0, min_lat=0.0, max_lon=1.0, max_lat=1.0),
    )
    result = classify_intersection_road_detail(
        patch,
        {"hypothesis_id": "cell-2", "physical_approaches": [{
            "physical_approach_id": "arm",
            "source_way_ids": ["a", "b"],
            "member_boundary_port_ids": [],
            "flow_roles": ["bidirectional"],
            "member_count": 1,
            "incoming_lane_count": 1,
            "outgoing_lane_count": 1,
            "bearing_from_seed_deg": 0.0,
        }]},
        arm_model={"through_pairs": []},
        topology_evidence={
            "physical_cell_hypothesis_id": "cell-2",
            "topology_evidence_id": "topology-2",
            "path_closure_node_ids": ["center"],
            "branch_connectors": [],
            "storage_capable_connectors": [],
        },
        road_network_evidence={
            "by_way_id": {
                "a": {"authority_category": "hvs", "network_role": "arterial", "source_evidence_id": "one"},
                "b": {"authority_category": "bezirksstrasse", "network_role": "collector", "source_evidence_id": "two"},
            }
        },
    )
    arm = result["road_arms"][0]
    assert arm["road_identity"]["authority_category"]["value"] == "unknown"
    assert arm["road_identity"]["authority_category"]["status"] == "contradicted"
    assert arm["road_identity"]["resolution"] == "contradicted"
    assert "road_class_unresolved" not in result["review_reasons"]
