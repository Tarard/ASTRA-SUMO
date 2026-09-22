from __future__ import annotations

from datetime import UTC, datetime

from torii_sumo.intersection.road_detail import classify_intersection_road_detail
from torii_sumo.intersection.schema import BBox, OSMNode, OSMPatch, OSMWay
from torii_sumo.road_network.contracts import (
    ConflationEvidence,
    RoadObjectRef,
    RoadPropertyAssignment,
    build_conflation_relation,
    project_road_detail_evidence,
)


def test_pass_only_semantic_bridge_projection_is_consumed_by_road_detail() -> None:
    valid_from = datetime(2026, 7, 19, tzinfo=UTC)
    valid_to = datetime(2026, 7, 20, tzinfo=UTC)
    target_time = datetime(2026, 7, 19, 12, tzinfo=UTC)
    official = RoadObjectRef(
        namespace="official.hh_sib",
        object_type="road_link_assertion",
        object_id="official-main",
        source_sha256="a" * 64,
        valid_from=valid_from,
        valid_to=valid_to,
    )
    osm_refs = tuple(
        RoadObjectRef(
            namespace="osm",
            object_type="way",
            object_id=way_id,
            source_sha256="b" * 64,
            valid_from=valid_from,
            valid_to=valid_to,
        )
        for way_id in ("main-in", "main-out")
    )
    relation = build_conflation_relation(
        left_refs=(official,),
        right_refs=osm_refs,
        relation_kind="covers",
        direction="both",
        target_time=target_time,
        evidence=ConflationEvidence(
            geometry_overlap_ratio=0.97,
            lateral_distance_m=0.8,
            heading_delta_deg=1.0,
            topology_agreement=1.0,
            name_agreement=1.0,
            official_road_key_agreement=1.0,
        ),
    )
    assignments = tuple(
        RoadPropertyAssignment(
            assignment_id=assignment_id,
            target_ref=official,
            property_name=property_name,
            classification_scheme=scheme,
            value=value,
            direction="both",
            evidence_refs=(official,),
            status="pass",
        )
        for assignment_id, property_name, scheme, value in (
            ("membership", "hamburg_membership", "de:hamburg:hvs", "hvs"),
            ("network", "network_role", "torii:network-role:v1", "arterial"),
            ("rin", "rin_category", "de:rin:2008", "HS III"),
        )
    )
    road_network_evidence = project_road_detail_evidence((relation,), assignments)
    patch = OSMPatch(
        nodes={
            "west": OSMNode(id="west", lat=0, lon=0, x=-10, y=0),
            "center": OSMNode(id="center", lat=0, lon=0, x=0, y=0),
            "east": OSMNode(id="east", lat=0, lon=0, x=10, y=0),
        },
        ways={
            "main-in": OSMWay(
                id="main-in",
                node_refs=["west", "center"],
                tags={"highway": "primary", "oneway": "yes"},
            ),
            "main-out": OSMWay(
                id="main-out",
                node_refs=["center", "east"],
                tags={"highway": "primary", "oneway": "yes"},
            ),
        },
        relations={},
        bbox=BBox(min_lon=-1, min_lat=-1, max_lon=1, max_lat=1),
    )

    result = classify_intersection_road_detail(
        patch,
        {
            "hypothesis_id": "cell-bridge",
            "physical_approaches": [
                {
                    "physical_approach_id": "arm-main",
                    "source_way_ids": ["main-in", "main-out"],
                    "member_boundary_port_ids": [],
                    "flow_roles": ["incoming", "outgoing"],
                    "member_count": 2,
                    "incoming_lane_count": 1,
                    "outgoing_lane_count": 1,
                    "bearing_from_seed_deg": 90.0,
                }
            ],
        },
        arm_model={"through_pairs": []},
        topology_evidence={
            "physical_cell_hypothesis_id": "cell-bridge",
            "topology_evidence_id": "topology-bridge",
            "path_closure_node_ids": ["center"],
            "branch_connectors": [],
            "storage_capable_connectors": [],
        },
        road_network_evidence=road_network_evidence,
    )

    identity = result["road_arms"][0]["road_identity"]
    assert road_network_evidence["status"] == "pass"
    assert identity["resolution"] == "authoritative"
    assert identity["authority_category"]["value"] == "hvs"
    assert identity["network_role"]["value"] == "arterial"
    assert identity["functional_category"] == "HS III"
    assert result["automatic_promotion_gate"] == "blocked"
