from __future__ import annotations

from pathlib import Path

import pytest

from torii_sumo.intersection.composable_archetype import (
    build_hamburg_2394_archetype_profile,
    build_mapem_archetype_evidence,
    build_ocit_controller_domain_evidence,
    build_sumo_owner_layout_evidence,
    classify_composable_intersection,
    registered_intersection_archetypes,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SANDTORKAI_ROOT = REPOSITORY_ROOT / "artifacts" / "hamburg_sandtorkai_twin_20260711"
MAP_2394 = SANDTORKAI_ROOT / "twin" / "official" / "signals" / "assets" / "2394_map_xml.xml"
OCIT_2394 = (
    SANDTORKAI_ROOT / "twin" / "official" / "signals" / "assets" / "2394_ocit_xml.xml"
)
SOURCE_NET = (
    SANDTORKAI_ROOT
    / "network"
    / "official_osm_recovery_v1"
    / "compact_corridor_directional_parity_v2"
    / "hamburg_sandtorkai_official_osm_compact_parity_v2.net.xml"
)


def test_2394_is_registered_beside_simple_t3_and_x4_prototypes() -> None:
    registry = registered_intersection_archetypes()

    assert registry == {
        "hamburg_2394_v1": {
            "base_skeleton": "T3",
            "physical_arrangement": "compound_candidate",
            "family": "channelized_T3_family",
        },
        "simple_T3_v1": {
            "base_skeleton": "T3",
            "physical_arrangement": "single_core",
            "family": "simple_T3_family",
        },
        "simple_X4_v1": {
            "base_skeleton": "X4",
            "physical_arrangement": "single_core",
            "family": "simple_X4_family",
        },
    }


@pytest.mark.skipif(not MAP_2394.is_file(), reason="current Hamburg 2394 MAP artifact is not present")
def test_real_2394_mapem_proves_channelized_t3_skeleton() -> None:
    evidence = build_mapem_archetype_evidence(
        MAP_2394,
        "02394",
        validated_arm_groups=(("2",), ("3",), ("4",)),
        validated_arm_group_evidence_id="hamburg_2394_map_bearing_review_v1",
        validated_continuous_axis=("2", "4"),
        validated_continuous_axis_evidence_id="hamburg_2394_map_bearing_review_v1",
    )

    assert evidence["lane_count"] == 25
    assert evidence["vehicle_lane_count"] == 11
    assert evidence["connection_count"] == 16
    assert evidence["vehicle_movement_count"] == 8
    assert evidence["signal_group_count"] == 12
    assert evidence["stop_line_count"] == 20
    assert evidence["merge_point_count"] == 6
    assert evidence["vehicle_approach_ids"] == ["2", "3", "4"]
    assert evidence["vehicle_arm_ids"] == ["2", "3", "4"]
    assert evidence["vehicle_arm_count"] == 3
    assert evidence["vehicle_stop_line_cluster_count_candidate"] == 3
    assert evidence["vehicle_stop_line_marker_count"] == 7
    assert evidence["vehicle_stop_line_lane_coverage"]["complete"] is True
    assert {
        row["approach_ids"][0]: (row["marker_count"], row["longitudinal_span_m"])
        for row in evidence["vehicle_stop_line_clusters_candidate"]
    } == {
        "2": (2, pytest.approx(0.2966, abs=0.001)),
        "3": (2, pytest.approx(0.0672, abs=0.001)),
        "4": (3, pytest.approx(0.1004, abs=0.001)),
    }
    bearing_ranges = {
        approach_id: (min(values), max(values))
        for approach_id, values in evidence["vehicle_approach_lane_bearings_deg"].items()
    }
    assert bearing_ranges == {
        "2": pytest.approx((19.251, 23.247), abs=0.001),
        "3": pytest.approx((287.105, 293.936), abs=0.001),
        "4": pytest.approx((187.313, 193.950), abs=0.001),
    }
    assert evidence["vehicle_approach_movement_pairs"] == [
        ["2", "3"],
        ["2", "4"],
        ["3", "2"],
        ["3", "4"],
        ["4", "2"],
        ["4", "3"],
    ]
    assert evidence["continuous_axis_approach_ids"] == ["2", "4"]
    assert evidence["third_arm_approach_ids"] == ["3"]
    assert evidence["official_intersection_parts"] == ["0"]


@pytest.mark.skipif(not MAP_2394.is_file(), reason="current Hamburg 2394 MAP artifact is not present")
def test_raw_map_approach_count_does_not_claim_a_physical_arm_count() -> None:
    evidence = build_mapem_archetype_evidence(MAP_2394, "2394")

    assert evidence["vehicle_approach_id_count"] == 3
    assert evidence["vehicle_arm_count"] is None
    assert evidence["vehicle_arm_count_status"] == "unknown_pending_gate_validation"


@pytest.mark.skipif(not OCIT_2394.is_file(), reason="current Hamburg 2394 OCIT artifact is not present")
def test_real_2394_ocit_proves_one_controller_domain_and_one_technical_subnode() -> None:
    evidence = build_ocit_controller_domain_evidence(OCIT_2394, "02394")

    assert evidence["controller_domain_ids"] == ["2394"]
    assert evidence["technical_subnode_ids"] == ["1"]
    assert evidence["technical_subnode_count"] == 1
    assert evidence["phase_technical_subnode_ids"] == ["1"]
    assert evidence["omtc_tk_records"] == [
        {"short_name": "TK 1", "ocit_outstation_number": "1"}
    ]


@pytest.mark.skipif(
    not MAP_2394.is_file() or not OCIT_2394.is_file() or not SOURCE_NET.is_file(),
    reason="current Hamburg 2394 MAP/network artifacts are not present",
)
def test_real_2394_profile_separates_type_owner_and_controller_layers() -> None:
    profile = build_hamburg_2394_archetype_profile(
        map_file=MAP_2394,
        ocit_file=OCIT_2394,
        source_net_file=SOURCE_NET,
    )

    assert profile["prototype_id"] == "hamburg_2394_v1"
    assert profile["classification"] == {
        "base_skeleton": "T3",
        "physical_arrangement": "compound_candidate",
        "channelization_modifiers": [
            "distributed_stopline_markers",
            "lane_fanout",
            "merge_diverge",
            "pedestrian_crossing",
            "preserved_internal_connectors",
        ],
        "control_domain": "multi_owner_single_controller_candidate",
        "movement_graph_class": "complete_no_uturn_arm_graph_with_lane_adjacency",
        "mode_and_restriction_modifiers": ["bicycle", "motor_vehicle", "pedestrian"],
        "family": "channelized_T3_family",
    }
    assert profile["counts"] == {
        "raw_node_count": 8,
        "classification_join_group_count": 2,
        "physical_conflict_core_count": None,
        "owner_count_after_rebuild_candidate": 5,
        "controller_domain_count": 1,
    }
    assert profile["physical_conflict_core_status"] == "unknown_pending_conflict_analysis"
    assert profile["unknown_dimensions"] == ["physical_conflict_core_count"]
    assert profile["official_intersection_parts"] == ["0"]
    assert profile["status"] == "review_required"
    assert profile["automatic_promotion_gate"] == "blocked"

    hint = profile["execution_hint"]
    assert hint["classification_only"] is True
    assert hint["automatic_authorization"] == "blocked"
    assert hint["strategy"] == "local_join_candidates_preserve_split_shared_controller"
    assert {frozenset(group) for group in hint["local_join_candidate_groups"]} == {
        frozenset({"3847369287", "757036909", "76463166"}),
        frozenset({"2761334279", "757036795"}),
    }
    assert {tuple(component) for component in hint["preserve_owner_components"]} == {
        ("76463166", "757036909", "3847369287"),
        ("3847369288",),
        ("759714726",),
        ("757036795", "2761334279"),
        ("3847369285",),
    }


def test_owner_count_never_implies_physical_conflict_core_count() -> None:
    profile = classify_composable_intersection(
        {
            "node_id": "example",
            "vehicle_arm_count": 4,
            "vehicle_approach_ids": ["n", "e", "s", "w"],
            "vehicle_lane_count": 8,
            "vehicle_movement_count": 12,
            "vehicle_movements": [{"ingress_lane_id": "n0", "egress_lane_id": "e0"}],
            "lane_types": ["vehicle"],
            "stop_line_count": 4,
            "vehicle_stop_line_count": 4,
            "merge_point_count": 0,
            "official_intersection_parts": ["0"],
            "evidence_id": "map-example",
        },
        {
            "raw_node_count": 3,
            "owner_candidate_count": 3,
            "local_join_candidate_groups": [],
            "owner_candidate_components": [["a"], ["b"], ["c"]],
            "preserved_connector_edges": [],
            "evidence_id": "sumo-example",
        },
        {"controller_domain_ids": ["tls"], "evidence_id": "controller-example"},
    )

    assert profile["classification"]["base_skeleton"] == "X4"
    assert profile["classification"]["control_domain"] == "multi_owner_single_controller_candidate"
    assert profile["classification"]["physical_arrangement"] == "unknown"
    assert profile["counts"]["physical_conflict_core_count"] is None
    assert profile["physical_conflict_core_status"] == "unknown_pending_conflict_analysis"
    assert "execution_hint" not in profile["classification"]


def test_micro_edge_quotient_is_classification_only_and_respects_stopline_owners(
    tmp_path: Path,
) -> None:
    net_file = tmp_path / "micro.net.xml"
    net_file.write_text(
        """<?xml version="1.0" encoding="UTF-8"?>
<net>
    <edge id="ab" from="a" to="b"><lane id="ab_0" length="0.40" shape="0,0 0.4,0"/></edge>
    <edge id="bc" from="b" to="c"><lane id="bc_0" length="0.30" shape="0.4,0 0.7,0"/></edge>
    <junction id="a" type="priority" x="0" y="0" incLanes="" intLanes=""/>
    <junction id="b" type="priority" x="1" y="0" incLanes="" intLanes=""/>
    <junction id="c" type="priority" x="2" y="0" incLanes="" intLanes=""/>
</net>
""",
        encoding="utf-8",
    )

    evidence = build_sumo_owner_layout_evidence(
        net_file,
        ("a", "b", "c"),
        ("b", "c"),
        micro_edge_threshold_m=1.0,
    )

    assert evidence["classification_only"] is True
    assert evidence["automatic_geometry_authorization"] == "blocked"
    assert evidence["classification_quotient_components"] == [["a", "b"], ["c"]]
    assert [edge["edge_id"] for edge in evidence["micro_edge_candidates"]] == ["ab"]
    assert [edge["edge_id"] for edge in evidence["blocked_micro_edges"]] == ["bc"]
    assert evidence["physical_conflict_core_count"] is None


def test_classifier_rejects_non_finite_enums_and_invalid_container_shapes() -> None:
    mapem = {
        "node_id": "example",
        "vehicle_arm_count": 3,
        "vehicle_arm_ids": ["a", "b", "c"],
        "vehicle_approach_ids": ["a", "b", "c"],
        "vehicle_movements": [],
        "vehicle_arm_movement_pairs": [],
        "lane_types": ["vehicle"],
        "vehicle_stop_line_cluster_count_candidate": 0,
        "merge_point_count": 0,
    }
    owners = {
        "owner_candidate_count": 1,
        "owner_layout_status": "confirmed",
        "preserved_connector_edges": [],
    }

    with pytest.raises(ValueError, match="physical_arrangement"):
        classify_composable_intersection(
            mapem,
            owners,
            {"controller_domain_ids": ["tls"]},
            {"physical_arrangement": "banana"},
        )
    with pytest.raises(ValueError, match="controller_domain_ids"):
        classify_composable_intersection(
            mapem,
            owners,
            {"controller_domain_ids": "tls"},
        )
    with pytest.raises(ValueError, match="review_gates"):
        classify_composable_intersection(
            mapem,
            owners,
            {"controller_domain_ids": ["tls"]},
            {"review_gates": "render"},
        )
    with pytest.raises(ValueError, match="positive integer"):
        classify_composable_intersection(
            mapem,
            owners,
            {"controller_domain_ids": ["tls"]},
            {"physical_conflict_core_count": 0},
        )


def test_null_controller_identifier_is_not_counted_as_a_controller() -> None:
    profile = classify_composable_intersection(
        {
            "node_id": "example",
            "vehicle_arm_count": 3,
            "vehicle_arm_ids": ["a", "b", "c"],
            "vehicle_approach_ids": ["a", "b", "c"],
            "vehicle_movements": [],
            "vehicle_arm_movement_pairs": [],
            "lane_types": ["vehicle"],
            "vehicle_stop_line_cluster_count_candidate": 0,
            "merge_point_count": 0,
        },
        {
            "owner_candidate_count": 1,
            "owner_layout_status": "confirmed",
            "preserved_connector_edges": [],
        },
        {"controller_domain_ids": [None]},
    )

    assert profile["counts"]["controller_domain_count"] == 0
    assert profile["classification"]["control_domain"] == "uncontrolled_or_unknown"


def test_micro_edge_join_hint_blocks_threshold_side_disagreement(tmp_path: Path) -> None:
    net_file = tmp_path / "threshold.net.xml"
    net_file.write_text(
        """<?xml version="1.0" encoding="UTF-8"?>
<net>
    <edge id="ab" from="a" to="b"><lane id="ab_0" length="0.90" shape="0,0 1.1,0"/></edge>
    <junction id="a" type="priority" x="0" y="0" incLanes="" intLanes=""/>
    <junction id="b" type="priority" x="1.1" y="0" incLanes="" intLanes=""/>
</net>
""",
        encoding="utf-8",
    )

    evidence = build_sumo_owner_layout_evidence(
        net_file,
        ("a", "b"),
        (),
        micro_edge_threshold_m=1.0,
    )

    assert evidence["micro_edge_candidates"] == []
    assert evidence["blocked_micro_edges"][0]["blocked_reason"] == (
        "declared_and_rendered_lengths_cross_micro_threshold"
    )
    assert evidence["classification_quotient_components"] == [["a"], ["b"]]


@pytest.mark.skipif(not MAP_2394.is_file(), reason="current Hamburg 2394 MAP artifact is not present")
def test_map_evidence_id_depends_on_content_not_checkout_path(tmp_path: Path) -> None:
    copied = tmp_path / "copy-of-2394-map.xml"
    copied.write_bytes(MAP_2394.read_bytes())

    kwargs = {
        "validated_arm_groups": (("2",), ("3",), ("4",)),
        "validated_arm_group_evidence_id": "hamburg_2394_map_bearing_review_v1",
        "validated_continuous_axis": ("2", "4"),
        "validated_continuous_axis_evidence_id": "hamburg_2394_map_bearing_review_v1",
    }
    original = build_mapem_archetype_evidence(MAP_2394, "2394", **kwargs)
    duplicate = build_mapem_archetype_evidence(copied, "2394", **kwargs)

    assert original["source_file"] != duplicate["source_file"]
    assert original["evidence_id"] == duplicate["evidence_id"]


def test_multi_geometry_map_does_not_leak_intersection_parts_between_nodes(
    tmp_path: Path,
) -> None:
    def geometry(node_id: str, ingress: str, egress: str, approach: str) -> str:
        return f"""
<IntersectionGeometry>
  <id><id>{node_id}</id></id><laneWidth>325</laneWidth>
  <laneSet>
    <GenericLane>
      <laneID>{ingress}</laneID><ingressApproach>{approach}</ingressApproach>
      <laneAttributes><laneType><vehicle/></laneType></laneAttributes>
      <nodeList><nodes>
        <NodeXY><delta><nodeXY><x>0</x><y>0</y></nodeXY></delta></NodeXY>
        <NodeXY><delta><nodeXY><x>1000</x><y>0</y></nodeXY></delta></NodeXY>
      </nodes></nodeList>
      <connectsTo><Connection><connectionID>1</connectionID>
        <connectingLane><lane>{egress}</lane></connectingLane>
      </Connection></connectsTo>
    </GenericLane>
    <GenericLane>
      <laneID>{egress}</laneID><egressApproach>{approach}</egressApproach>
      <laneAttributes><laneType><vehicle/></laneType></laneAttributes>
      <nodeList><nodes>
        <NodeXY><delta><nodeXY><x>0</x><y>0</y></nodeXY></delta></NodeXY>
        <NodeXY><delta><nodeXY><x>1000</x><y>0</y></nodeXY></delta></NodeXY>
      </nodes></nodeList>
    </GenericLane>
  </laneSet>
</IntersectionGeometry>
"""

    map_file = tmp_path / "multi-map.xml"
    map_file.write_text(
        f"""<MAPEM><map><intersections>
{geometry("2394", "1", "2", "a")}
{geometry("999", "3", "4", "b")}
</intersections></map><trafficStreams>
  <TrafficStreamConfigData><refLaneId>1</refLaneId><refConnectTo>2</refConnectTo><intersectionPart>0</intersectionPart></TrafficStreamConfigData>
  <TrafficStreamConfigData><refLaneId>3</refLaneId><refConnectTo>4</refConnectTo><intersectionPart>9</intersectionPart></TrafficStreamConfigData>
</trafficStreams></MAPEM>""",
        encoding="utf-8",
    )

    evidence = build_mapem_archetype_evidence(map_file, "2394")

    assert evidence["official_intersection_parts"] == ["0"]
    assert evidence["official_intersection_part_status"] == "pair_bound"
    assert evidence["lane_count"] == 2
    assert evidence["connection_count"] == 1


def test_duplicate_normalized_geometry_ids_fail_closed(tmp_path: Path) -> None:
    map_file = tmp_path / "duplicate-map.xml"
    map_file.write_text(
        """<MAPEM><map><intersections>
<IntersectionGeometry><id><id>2394</id></id></IntersectionGeometry>
<IntersectionGeometry><id><id>02394</id></id></IntersectionGeometry>
</intersections></map></MAPEM>""",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="exactly one IntersectionGeometry"):
        build_mapem_archetype_evidence(map_file, "2394")
