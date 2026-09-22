import csv
import json
import sys
import types
from pathlib import Path

from torii_sumo.core import reference_join_audit as reference_join_audit_module
from torii_sumo.core.reference_join_audit import _reference_join_cases, audit_reference_join_patterns


def test_tls_controller_alignment_pairs_by_geography_not_id() -> None:
    reference = {
        "tl_logic_control_records": [
            {
                "tl_id": "manual_cluster_a",
                "controlled_connection_count": 20,
                "junction_ids": ["manual_a"],
                "controlled_junction_centroid_lat": 48.76,
                "controlled_junction_centroid_lon": 11.42,
                "controlled_junction_centroid_status": "wgs84_from_sumo_projection",
                "passenger_approaches": [
                    {
                        "edge_id": "manual_a_in",
                        "split_root_edge_id": "road_a",
                        "bearing_deg": 10.0,
                        "controlled_connection_count": 2,
                        "movement_signature_counts": {"dir=s|fromLane=0|toLane=0": 2},
                    }
                ],
            },
            {
                "tl_id": "manual_cluster_b",
                "controlled_connection_count": 12,
                "junction_ids": ["manual_b"],
                "controlled_junction_centroid_lat": 48.761,
                "controlled_junction_centroid_lon": 11.421,
                "controlled_junction_centroid_status": "wgs84_from_sumo_projection",
                "passenger_approaches": [
                    {
                        "edge_id": "manual_b_in",
                        "split_root_edge_id": "road_b",
                        "bearing_deg": 100.0,
                        "controlled_connection_count": 1,
                        "movement_signature_counts": {"dir=l|fromLane=0|toLane=0": 1},
                    }
                ],
            },
        ]
    }
    candidate = {
        "tl_logic_control_records": [
            {
                "tl_id": "osm_node_900",
                "controlled_connection_count": 17,
                "junction_ids": ["osm_a1", "osm_a2"],
                "controlled_junction_centroid_lat": 48.76001,
                "controlled_junction_centroid_lon": 11.42001,
                "controlled_junction_centroid_status": "wgs84_from_sumo_projection",
                "passenger_approaches": [
                    {
                        "edge_id": "osm_a_in",
                        "split_root_edge_id": "road_a",
                        "bearing_deg": 12.0,
                        "controlled_connection_count": 1,
                        "movement_signature_counts": {"dir=s|fromLane=0|toLane=0": 1},
                    }
                ],
            },
            {
                "tl_id": "osm_node_901",
                "controlled_connection_count": 15,
                "junction_ids": ["osm_b"],
                "controlled_junction_centroid_lat": 48.76101,
                "controlled_junction_centroid_lon": 11.42101,
                "controlled_junction_centroid_status": "wgs84_from_sumo_projection",
                "passenger_approaches": [
                    {
                        "edge_id": "osm_b_in",
                        "split_root_edge_id": "road_b",
                        "bearing_deg": 98.0,
                        "controlled_connection_count": 1,
                        "movement_signature_counts": {"dir=l|fromLane=0|toLane=0": 1},
                    }
                ],
            },
        ]
    }

    alignment = reference_join_audit_module._tls_controller_alignment(reference, candidate)

    assert alignment["status"] == "diagnostic"
    assert alignment["pair_count"] == 2
    assert alignment["boundary_approach_pair_count"] == 2
    assert alignment["split_root_approach_pair_count"] == 2
    assert alignment["bearing_fallback_approach_pair_count"] == 0
    assert alignment["high_confidence_movement_gap_candidate_count"] == 1
    assert alignment["high_confidence_missing_direction_instance_count"] == 1
    assert alignment["unpaired_reference_boundary_approach_count"] == 0
    assert alignment["unpaired_candidate_boundary_approach_count"] == 0
    assert alignment["missing_movement_signature_instance_count"] == 1
    assert alignment["extra_movement_signature_instance_count"] == 0
    assert alignment["exact_direction_approach_pair_count"] == 1
    assert alignment["missing_direction_instance_count"] == 1
    assert alignment["extra_direction_instance_count"] == 0
    assert alignment["paired_reference_controlled_connection_count"] == 32
    assert alignment["paired_candidate_controlled_connection_count"] == 32
    assert alignment["paired_controlled_connection_delta"] == 0
    assert alignment["possible_candidate_split_reference_count"] == 0
    assert alignment["possible_candidate_merge_controller_count"] == 0
    assert alignment["controller_group_count"] == 2
    assert alignment["split_controller_group_count"] == 0
    assert alignment["merge_controller_group_count"] == 0
    assert alignment["many_to_many_controller_group_count"] == 0
    assert all(group["approach_pair_count"] == 1 for group in alignment["controller_groups"])
    assert alignment["repair_safe"] is False
    assert all(pair["approach_alignment_status"] == "diagnostic" for pair in alignment["pairs"])
    assert all(pair["approach_pair_count"] == 1 for pair in alignment["pairs"])
    first_pair = next(pair for pair in alignment["pairs"] if pair["reference_tl_id"] == "manual_cluster_a")
    assert first_pair["approach_pairs"][0]["missing_movement_signature_counts"] == {
        "dir=s|fromLane=0|toLane=0": 1
    }
    assert first_pair["approach_pairs"][0]["missing_direction_counts"] == {"s": 1}
    assert {
        (pair["reference_tl_id"], pair["candidate_tl_id"], pair["controlled_connection_delta"])
        for pair in alignment["pairs"]
    } == {
        ("manual_cluster_a", "osm_node_900", -3),
        ("manual_cluster_b", "osm_node_901", 3),
    }


def test_tls_controller_alignment_groups_candidate_split_controllers() -> None:
    def record(tl_id: str, count: int, lon_offset: float) -> dict:
        return {
            "tl_id": tl_id,
            "controlled_connection_count": count,
            "junction_ids": [tl_id],
            "controlled_junction_centroid_lat": 48.76,
            "controlled_junction_centroid_lon": 11.42 + lon_offset,
            "controlled_junction_centroid_status": "wgs84_from_sumo_projection",
            "passenger_approaches": [],
        }

    alignment = reference_join_audit_module._tls_controller_alignment(
        {"tl_logic_control_records": [record("manual", 10, 0.0)]},
        {
            "tl_logic_control_records": [
                record("candidate_a", 4, -0.00005),
                record("candidate_b", 6, 0.00005),
            ]
        },
    )

    assert alignment["controller_group_count"] == 1
    assert alignment["split_controller_group_count"] == 1
    group = alignment["controller_groups"][0]
    assert group["reference_tl_ids"] == ["manual"]
    assert group["candidate_tl_ids"] == ["candidate_a", "candidate_b"]
    assert group["controlled_connection_delta"] == 0


def test_tls_approach_alignment_prefers_same_split_root_over_closer_parallel_edge() -> None:
    movement = {"dir=s|fromPassengerLaneRank=0|toPassengerLaneRank=0": 1}
    alignment = reference_join_audit_module._tls_approach_alignment(
        {
            "passenger_approaches": [
                {"edge_id": "road#0", "bearing_deg": 0.0, "movement_signature_counts": movement}
            ]
        },
        {
            "passenger_approaches": [
                {"edge_id": "parallel", "bearing_deg": 1.0, "movement_signature_counts": movement},
                {"edge_id": "road#3", "bearing_deg": 10.0, "movement_signature_counts": movement},
            ]
        },
    )

    assert alignment["approach_pairs"][0]["candidate_edge_id"] == "road#3"
    assert alignment["approach_pairs"][0]["split_root_match"] is True


def test_tls_semantic_summary_excludes_controller_internal_edges_from_approaches(tmp_path: Path) -> None:
    net_file = tmp_path / "multi_junction_tls.net.xml"
    net_file.write_text(
        """<net>
  <edge id="external" from="a" to="j1"><lane id="external_0" index="0" allow="passenger" shape="0,0 10,0"/></edge>
  <edge id="internal_road" from="j1" to="j2"><lane id="internal_road_0" index="0" allow="passenger" shape="10,0 20,0"/></edge>
  <edge id="out" from="j2" to="b"><lane id="out_0" index="0" allow="passenger" shape="20,0 30,0"/></edge>
  <edge id=":j1_0" function="internal"><lane id=":j1_0_0" index="0"/></edge>
  <edge id=":j2_0" function="internal"><lane id=":j2_0_0" index="0"/></edge>
  <junction id="a" type="priority" x="0" y="0"/>
  <junction id="j1" type="traffic_light" x="10" y="0"/>
  <junction id="j2" type="traffic_light" x="20" y="0"/>
  <junction id="b" type="priority" x="30" y="0"/>
  <connection from="external" to="internal_road" fromLane="0" toLane="0" tl="tls" linkIndex="0" via=":j1_0_0"/>
  <connection from="internal_road" to="out" fromLane="0" toLane="0" tl="tls" linkIndex="1" via=":j2_0_0"/>
  <tlLogic id="tls" type="static" programID="0"><phase duration="30" state="GG"/></tlLogic>
</net>""",
        encoding="utf-8",
    )

    record = reference_join_audit_module._net_structural_summary(net_file)["tl_logic_control_records"][0]

    assert record["passenger_from_edge_ids"] == ["external", "internal_road"]
    assert record["controller_internal_passenger_from_edge_ids"] == ["internal_road"]
    assert [approach["edge_id"] for approach in record["passenger_approaches"]] == ["external"]


def test_reference_join_audit_matches_tum_cluster_to_torii_fragment(tmp_path: Path) -> None:
    reference_net = tmp_path / "tum_reference.net.xml"
    reference_net.write_text(
        """<net>
  <edge id="west" from="w" to="cluster_a_b">
    <lane id="west_0" index="0" length="30" shape="-30,0 0,0"/>
  </edge>
  <edge id="east" from="cluster_a_b" to="e">
    <lane id="east_0" index="0" length="30" shape="0,0 30,0"/>
  </edge>
  <junction id="w" x="-30" y="0" type="priority"/>
  <junction id="e" x="30" y="0" type="priority"/>
  <junction id="cluster_a_b" x="0" y="0" type="traffic_light" incLanes="west_0" intLanes=":cluster_a_b_0_0 :cluster_a_b_1_0" shape="-4,-4 4,-4 4,4 -4,4"/>
</net>
""",
        encoding="utf-8",
    )
    torii_net = tmp_path / "torii_fragmented.net.xml"
    torii_net.write_text(
        """<net>
  <edge id="internal_ab" from="a" to="b">
    <lane id="internal_ab_0" index="0" length="7" shape="-3,0 4,0"/>
  </edge>
  <edge id="internal_bc" from="b" to="c">
    <lane id="internal_bc_0" index="0" length="6" shape="4,0 8,2"/>
  </edge>
  <edge id="west_in" from="w" to="a">
    <lane id="west_in_0" index="0" length="30" shape="-30,0 -3,0"/>
  </edge>
  <edge id="east_out" from="c" to="e">
    <lane id="east_out_0" index="0" length="30" shape="8,2 30,0"/>
  </edge>
  <junction id="w" x="-30" y="0" type="priority"/>
  <junction id="e" x="30" y="0" type="priority"/>
  <junction id="a" x="-3" y="0" type="traffic_light"/>
  <junction id="b" x="4" y="0" type="traffic_light"/>
  <junction id="c" x="8" y="2" type="priority"/>
</net>
""",
        encoding="utf-8",
    )

    report = audit_reference_join_patterns(
        reference_net_file=reference_net,
        candidate_net_file=torii_net,
        output_dir=tmp_path / "audit",
        prefix="case",
        reference_cluster_prefix="cluster_",
        candidate_cluster_radius_m=20,
        match_radius_m=20,
    )

    assert report["status"] == "pass"
    assert report["reference_case_count"] == 1
    assert report["matched_case_count"] == 1
    case = report["matched_cases"][0]
    assert case["reference_joined_source_node_count"] == 2
    assert case["reference_type"] == "traffic_light"
    assert case["matched_reference_source_node_count"] == 2
    assert case["reference_source_node_match_ratio"] == 1.0
    assert case["matched_reference_source_internal_edge_count"] == 1
    assert case["matched_reference_source_internal_edge_ids"] == ["internal_ab"]
    assert case["matched_candidate_node_count"] == 3
    assert case["matched_candidate_internal_edge_count"] == 2
    assert case["matched_candidate_internal_edge_ids"] == ["internal_ab", "internal_bc"]
    assert case["learned_rule_basis"] == "reference_source_nodes"
    assert case["learned_rule"] == "tum_like_join_candidate"
    assert report["pattern_stats"]["reference_joined_source_node_counts"] == {"2": 1}
    assert report["pattern_stats"]["reference_approach_edge_counts"] == {"1": 1}
    assert report["pattern_stats"]["matched_reference_source_node_counts"] == {"2": 1}
    assert report["pattern_stats"]["matched_reference_source_internal_edge_counts"] == {"1": 1}
    assert report["pattern_stats"]["matched_candidate_node_counts"] == {"3": 1}
    assert report["pattern_stats"]["matched_candidate_internal_edge_counts"] == {"2": 1}
    assert report["pattern_stats"]["learned_rule_basis_counts"] == {"reference_source_nodes": 1}
    assert Path(report["cases_file"]).is_file()
    assert Path(report["summary_file"]).is_file()
    assert "matched_candidate_internal_edge_count" in Path(report["cases_file"]).read_text(encoding="utf-8").splitlines()[0]


def test_reference_join_audit_matches_tum_cluster_by_source_members_without_internal_edges(tmp_path: Path) -> None:
    reference_net = tmp_path / "tum_reference.net.xml"
    reference_net.write_text(
        """<net>
  <edge id="west" from="w" to="cluster_a_b_c">
    <lane id="west_0" index="0" length="30" shape="-30,0 0,0"/>
  </edge>
  <edge id="east" from="cluster_a_b_c" to="e">
    <lane id="east_0" index="0" length="30" shape="0,0 30,0"/>
  </edge>
  <junction id="w" x="-30" y="0" type="priority"/>
  <junction id="e" x="30" y="0" type="priority"/>
  <junction id="cluster_a_b_c" x="0" y="0" type="traffic_light" incLanes="west_0" intLanes=":cluster_a_b_c_0_0" shape="-4,-4 4,-4 4,4 -4,4"/>
</net>
""",
        encoding="utf-8",
    )
    torii_net = tmp_path / "torii_fragmented.net.xml"
    torii_net.write_text(
        """<net>
  <edge id="west_in" from="w" to="a"><lane id="west_in_0" index="0" length="30" shape="-30,0 0,0"/></edge>
  <edge id="north_in" from="n" to="b"><lane id="north_in_0" index="0" length="30" shape="0,30 0,0"/></edge>
  <edge id="east_out" from="c" to="e"><lane id="east_out_0" index="0" length="30" shape="0,0 30,0"/></edge>
  <junction id="w" x="-30" y="0" type="priority"/>
  <junction id="n" x="0" y="30" type="priority"/>
  <junction id="e" x="30" y="0" type="priority"/>
  <junction id="a" x="0" y="0" type="dead_end"/>
  <junction id="b" x="40" y="0" type="priority"/>
  <junction id="c" x="80" y="0" type="dead_end"/>
</net>
""",
        encoding="utf-8",
    )

    report = audit_reference_join_patterns(
        reference_net_file=reference_net,
        candidate_net_file=torii_net,
        output_dir=tmp_path / "audit",
        prefix="case",
        reference_cluster_prefix="cluster_",
        candidate_cluster_radius_m=5,
        match_radius_m=5,
    )

    assert report["status"] == "pass"
    assert report["matched_case_count"] == 1
    case = report["matched_cases"][0]
    assert case["matched_reference_source_node_ids"] == ["a", "b", "c"]
    assert case["matched_reference_source_internal_edge_count"] == 0
    assert case["matched_reference_source_boundary_edge_count"] == 3
    assert case["matched_candidate_node_ids"] == ["a", "b", "c"]
    assert case["learned_rule_basis"] == "reference_source_nodes"
    assert case["learned_rule"] == "tum_like_join_candidate"


def test_reference_join_audit_limits_full_pattern_extraction_to_reference_cases(
    monkeypatch, tmp_path: Path
) -> None:
    reference_net = tmp_path / "tum_reference.net.xml"
    reference_net.write_text(
        """<net>
  <edge id="west" from="w" to="cluster_a_b"><lane id="west_0" index="0" shape="-10,0 0,0"/></edge>
  <junction id="w" x="-10" y="0" type="priority"/>
  <junction id="cluster_a_b" x="0" y="0" type="traffic_light" incLanes="west_0" intLanes=""/>
</net>
""",
        encoding="utf-8",
    )
    candidate_net = tmp_path / "candidate.net.xml"
    candidate_net.write_text(
        """<net>
  <edge id="ab" from="a" to="b"><lane id="ab_0" index="0" shape="0,0 5,0"/></edge>
  <junction id="a" x="0" y="0" type="priority"/>
  <junction id="b" x="5" y="0" type="priority"/>
</net>
""",
        encoding="utf-8",
    )
    calls: list[dict[str, object]] = []

    def fake_extract_junction_pattern_index(net_file: Path, **kwargs):
        calls.append(
            {
                "net_file": Path(net_file).name,
                "junction_ids": sorted(kwargs.get("junction_ids") or []),
            }
        )
        return []

    monkeypatch.setattr(
        reference_join_audit_module,
        "extract_junction_pattern_index",
        fake_extract_junction_pattern_index,
    )

    report = audit_reference_join_patterns(
        reference_net_file=reference_net,
        candidate_net_file=candidate_net,
        output_dir=tmp_path / "audit",
        reference_cluster_prefix="cluster_",
        candidate_cluster_radius_m=20,
        match_radius_m=20,
    )

    assert report["status"] == "pass"
    assert calls == [
        {"net_file": "tum_reference.net.xml", "junction_ids": ["cluster_a_b"]},
        {"net_file": "candidate.net.xml", "junction_ids": ["cluster_a_b"]},
    ]


def test_reference_join_cases_use_location_projection_without_sumolib_readnet(monkeypatch, tmp_path: Path) -> None:
    fake_sumolib = types.SimpleNamespace(
        net=types.SimpleNamespace(readNet=lambda _path: (_ for _ in ()).throw(RuntimeError("readNet should not run")))
    )
    monkeypatch.setitem(sys.modules, "sumolib", fake_sumolib)
    reference_net = tmp_path / "reference.net.xml"
    reference_net.write_text(
        """<net>
  <location netOffset="-500000.00,-5400000.00" convBoundary="0.00,0.00,1.00,1.00" origBoundary="9.00,48.00,9.01,48.01" projParameter="+proj=utm +zone=32 +ellps=WGS84 +datum=WGS84 +units=m +no_defs"/>
  <junction id="cluster_a_b" x="0" y="0" type="priority" incLanes="" intLanes=""/>
</net>""",
        encoding="utf-8",
    )

    cases = _reference_join_cases(reference_net, "cluster_")

    assert cases[0]["reference_coordinate_status"] == "wgs84_from_sumo_projection"
    assert 48.6 < cases[0]["reference_lat"] < 48.9
    assert 8.9 < cases[0]["reference_lon"] < 9.1


def test_reference_join_audit_reports_reusable_junction_patterns(tmp_path: Path) -> None:
    reference = tmp_path / "reference.net.xml"
    candidate = tmp_path / "candidate.net.xml"
    reference.write_text(
        """<net>
  <edge id="west_in" from="w" to="cluster_a_b"><lane id="west_in_0" index="0" allow="passenger" shape="-10,0 0,0"/></edge>
  <edge id="south_in" from="s" to="cluster_a_b"><lane id="south_in_0" index="0" allow="passenger" shape="0,-10 0,0"/></edge>
  <edge id="north_in" from="n" to="cluster_a_b"><lane id="north_in_0" index="0" allow="passenger" shape="0,10 0,0"/></edge>
  <edge id="east_out" from="cluster_a_b" to="e"><lane id="east_out_0" index="0" allow="passenger" shape="0,0 10,0"/></edge>
  <edge id="south_out" from="cluster_a_b" to="so"><lane id="south_out_0" index="0" allow="passenger" shape="0,0 0,-10"/></edge>
  <edge id="north_out" from="cluster_a_b" to="no"><lane id="north_out_0" index="0" allow="passenger" shape="0,0 0,10"/></edge>
  <junction id="cluster_a_b" type="right_before_left" x="0" y="0" incLanes="west_in_0 south_in_0 north_in_0" intLanes="">
    <request index="0" response="0" foes="0" cont="0"/>
  </junction>
  <connection from="west_in" to="east_out" fromLane="0" toLane="0" dir="s"/>
  <connection from="south_in" to="south_out" fromLane="0" toLane="0" dir="s"/>
  <connection from="north_in" to="north_out" fromLane="0" toLane="0" dir="s"/>
</net>""",
        encoding="utf-8",
    )
    candidate.write_text(
        """<net>
  <edge id="internal_ab" from="a" to="b"><lane id="internal_ab_0" index="0" length="7" shape="-3,0 4,0"/></edge>
  <edge id="internal_bc" from="b" to="c"><lane id="internal_bc_0" index="0" length="6" shape="4,0 8,2"/></edge>
  <junction id="a" x="-3" y="0" type="traffic_light"/>
  <junction id="b" x="4" y="0" type="traffic_light"/>
  <junction id="c" x="8" y="2" type="priority"/>
</net>""",
        encoding="utf-8",
    )

    report = audit_reference_join_patterns(
        reference_net_file=reference,
        candidate_net_file=candidate,
        output_dir=tmp_path / "audit",
        reference_cluster_prefix="cluster_",
        candidate_cluster_radius_m=20,
        match_radius_m=20,
    )

    assert report["junction_pattern_index"][0]["junction_id"] == "cluster_a_b"
    assert report["junction_pattern_index"][0]["control_type"] == "right_before_left"
    assert report["junction_pattern_index"][0]["arm_count"] == 3
    assert report["junction_pattern_templates"][0]["pattern_family"] == "three_way"
    assert report["junction_pattern_templates"][0]["count"] == 1
    assert report["junction_pattern_templates"][0]["example_junction_ids"] == ["cluster_a_b"]
    templates = json.loads(Path(report["junction_pattern_templates_file"]).read_text(encoding="utf-8"))
    assert templates["reference_templates"] == report["junction_pattern_templates"]
    assert templates["candidate_templates"] == []
    assert templates["reference_policy_summary"] == {
        "record_count": 1,
        "family_counts": {"three_way": 1},
        "control_counts": {"three_way|right_before_left|no_tls": 1},
    }
    assert templates["candidate_policy_summary"] == {
        "record_count": 0,
        "family_counts": {},
        "control_counts": {},
    }


def test_reference_join_audit_compares_same_id_junction_patterns(tmp_path: Path) -> None:
    reference = tmp_path / "reference.net.xml"
    candidate = tmp_path / "candidate.net.xml"
    reference.write_text(
        """<net>
  <edge id="west_in" from="w" to="cluster_a_b"><lane id="west_in_0" index="0" allow="passenger" shape="-10,0 0,0"/></edge>
  <edge id="south_in" from="s" to="cluster_a_b"><lane id="south_in_0" index="0" allow="passenger" shape="0,-10 0,0"/></edge>
  <edge id="north_in" from="n" to="cluster_a_b"><lane id="north_in_0" index="0" allow="passenger" shape="0,10 0,0"/></edge>
  <edge id="east_out" from="cluster_a_b" to="e"><lane id="east_out_0" index="0" allow="passenger" shape="0,0 10,0"/></edge>
  <edge id="south_out" from="cluster_a_b" to="so"><lane id="south_out_0" index="0" allow="passenger" shape="0,0 0,-10"/></edge>
  <edge id="north_out" from="cluster_a_b" to="no"><lane id="north_out_0" index="0" allow="passenger" shape="0,0 0,10"/></edge>
  <edge id=":cluster_a_b_0" function="internal"><lane id=":cluster_a_b_0_0" index="0" allow="passenger" shape="0,0 1,1"/></edge>
  <edge id=":cluster_a_b_c0" function="crossing"><lane id=":cluster_a_b_c0_0" index="0" allow="pedestrian" shape="0,-1 0,1"/></edge>
  <edge id=":cluster_a_b_w0" function="walkingarea"><lane id=":cluster_a_b_w0_0" index="0" allow="pedestrian" shape="1,1 2,1"/></edge>
  <junction id="cluster_a_b" type="traffic_light" x="0" y="0" incLanes="west_in_0 south_in_0 north_in_0" intLanes=":cluster_a_b_0_0 :cluster_a_b_c0_0">
    <request index="0" response="00" foes="00" cont="0"/>
    <request index="1" response="00" foes="00" cont="1"/>
  </junction>
  <connection from="west_in" to="east_out" fromLane="0" toLane="0" tl="cluster_a_b" linkIndex="0" dir="s"/>
  <tlLogic id="cluster_a_b" type="actuated" programID="0"><phase duration="30" state="G"/></tlLogic>
</net>""",
        encoding="utf-8",
    )
    candidate.write_text(
        """<net>
  <edge id="west_in" from="w" to="cluster_a_b"><lane id="west_in_0" index="0" allow="passenger" shape="-10,0 0,0"/></edge>
  <edge id="south_in" from="s" to="cluster_a_b"><lane id="south_in_0" index="0" allow="passenger" shape="0,-10 0,0"/></edge>
  <edge id="north_in" from="n" to="cluster_a_b"><lane id="north_in_0" index="0" allow="passenger" shape="0,10 0,0"/></edge>
  <edge id="east_out" from="cluster_a_b" to="e"><lane id="east_out_0" index="0" allow="passenger" shape="0,0 10,0"/></edge>
  <edge id="south_out" from="cluster_a_b" to="so"><lane id="south_out_0" index="0" allow="passenger" shape="0,0 0,-10"/></edge>
  <edge id="north_out" from="cluster_a_b" to="no"><lane id="north_out_0" index="0" allow="passenger" shape="0,0 0,10"/></edge>
  <edge id=":cluster_a_b_0" function="internal"><lane id=":cluster_a_b_0_0" index="0" allow="passenger" shape="0,0 1,1"/></edge>
  <junction id="cluster_a_b" type="priority" x="0" y="0" incLanes="west_in_0 south_in_0 north_in_0" intLanes=":cluster_a_b_0_0">
    <request index="0" response="0" foes="0" cont="0"/>
    <request index="1" response="00" foes="00" cont="00"/>
  </junction>
  <connection from="west_in" to="east_out" fromLane="0" toLane="0" dir="s"/>
</net>""",
        encoding="utf-8",
    )

    report = audit_reference_join_patterns(
        reference_net_file=reference,
        candidate_net_file=candidate,
        output_dir=tmp_path / "audit",
        reference_cluster_prefix="cluster_",
        candidate_cluster_radius_m=20,
        match_radius_m=20,
    )

    assert report["candidate_junction_pattern_index"][0]["junction_id"] == "cluster_a_b"
    assert report["junction_pattern_comparison_status"] == "fail"
    assert report["junction_pattern_mismatch_count"] == 1
    assert report["junction_pattern_mismatch_field_counts"] == {
        "control_type": 1,
        "has_tls": 1,
        "internal_function_counts": 1,
        "movement_signature_counts": 1,
        "request_bit_lengths_ok": 1,
    }
    assert report["reference_structural_signature_summary"] == {
        "pattern_count": 1,
        "internal_bundle_pattern_count": 1,
        "movement_signature_pattern_count": 1,
        "pedestrian_separation_pattern_count": 1,
        "request_bit_vector_pattern_count": 1,
        "tls_pattern_count": 1,
    }
    assert report["candidate_structural_signature_summary"] == {
        "pattern_count": 1,
        "internal_bundle_pattern_count": 1,
        "movement_signature_pattern_count": 1,
        "pedestrian_separation_pattern_count": 0,
        "request_bit_vector_pattern_count": 0,
        "tls_pattern_count": 0,
    }
    assert report["junction_structural_signature_status"] == "fail"
    assert report["junction_structural_signature_missing_counts"] == {
        "pedestrian_separation_pattern_count": 1,
        "request_bit_vector_pattern_count": 1,
        "tls_pattern_count": 1,
    }
    assert report["reference_network_structural_summary"] == {
        "plain_edge_count": 6,
        "internal_edge_count": 1,
        "crossing_edge_count": 1,
        "walkingarea_edge_count": 1,
        "connection_count": 1,
        "request_count": 2,
        "tl_logic_count": 1,
        "traffic_light_junction_count": 1,
        "tls_controlled_connection_count": 1,
        "tl_connection_missing_linkindex_count": 0,
        "tl_logic_controlled_connection_count_distribution": {"1": 1},
        "tl_logic_controlled_junction_count_distribution": {"1": 1},
        "tl_logic_controlled_passenger_from_edge_count_distribution": {"1": 1},
        "low_passenger_approach_tl_logic_count": 1,
        "multi_junction_tl_logic_count": 0,
        "traffic_light_junction_without_tls_connection_count": 0,
        "traffic_light_junction_without_tls_connection_ids": [],
        "tls_shared_linkindex_group_count": 0,
        "tls_sparse_linkindex_tl_logic_count": 0,
        "tl_logic_control_records": [
            {
                "tl_id": "cluster_a_b",
                "controlled_connection_count": 1,
                "controlled_junction_count": 1,
                "junction_ids": ["cluster_a_b"],
                "controlled_known_from_edge_count": 1,
                    "controlled_passenger_from_edge_count": 1,
                    "passenger_from_edge_ids": ["west_in"],
                    "controller_internal_passenger_from_edge_ids": [],
                    "passenger_approaches": [
                        {
                            "edge_id": "west_in",
                            "split_root_edge_id": "west_in",
                            "bearing_deg": 0.0,
                            "controlled_connection_count": 1,
                            "movement_signature_counts": {
                                "dir=s|fromPassengerLaneRank=0|toPassengerLaneRank=0": 1,
                            },
                        }
                    ],
                "linkindexes": [0],
                "controlled_linkindex_count": 1,
                "phase_state_length": 1,
                    "shared_linkindex_group_count": 0,
                    "sparse_linkindex": False,
                    "controlled_junction_centroid_lat": 0.0,
                    "controlled_junction_centroid_lon": 0.0,
                    "controlled_junction_centroid_status": "xy_fallback_no_geo_projection",
                    "controlled_junction_points": [
                        {
                            "junction_id": "cluster_a_b",
                            "lat": 0.0,
                            "lon": 0.0,
                            "status": "xy_fallback_no_geo_projection",
                        }
                    ],
                }
        ],
        "junction_type_counts": {"traffic_light": 1},
        "edge_function_counts": {"crossing": 1, "internal": 1, "plain": 6, "walkingarea": 1},
        "plain_edge_type_counts": {"none": 6},
        "plain_edge_type_pedestrian_lane_counts": {},
    }
    assert report["candidate_network_structural_summary"] == {
        "plain_edge_count": 6,
        "internal_edge_count": 1,
        "crossing_edge_count": 0,
        "walkingarea_edge_count": 0,
        "connection_count": 1,
        "request_count": 2,
        "tl_logic_count": 0,
        "traffic_light_junction_count": 0,
        "tls_controlled_connection_count": 0,
        "tl_connection_missing_linkindex_count": 0,
        "tl_logic_controlled_connection_count_distribution": {},
        "tl_logic_controlled_junction_count_distribution": {},
        "tl_logic_controlled_passenger_from_edge_count_distribution": {},
        "low_passenger_approach_tl_logic_count": 0,
        "multi_junction_tl_logic_count": 0,
        "traffic_light_junction_without_tls_connection_count": 0,
        "traffic_light_junction_without_tls_connection_ids": [],
        "tls_shared_linkindex_group_count": 0,
        "tls_sparse_linkindex_tl_logic_count": 0,
        "tl_logic_control_records": [],
        "junction_type_counts": {"priority": 1},
        "edge_function_counts": {"internal": 1, "plain": 6},
        "plain_edge_type_counts": {"none": 6},
        "plain_edge_type_pedestrian_lane_counts": {},
    }
    assert report["network_structural_delta_status"] == "fail"
    assert report["network_structural_missing_counts"] == {
        "crossing_edge_count": 1,
        "tl_logic_count": 1,
        "traffic_light_junction_count": 1,
        "tls_controlled_connection_count": 1,
        "walkingarea_edge_count": 1,
    }
    assert report["network_structural_extra_counts"] == {}
    assert report["network_structural_junction_type_missing_counts"] == {"traffic_light": 1}
    assert report["network_structural_junction_type_extra_counts"] == {"priority": 1}
    comparison = report["junction_pattern_comparisons"][0]
    assert comparison["junction_id"] == "cluster_a_b"
    assert comparison["status"] == "fail"
    assert comparison["mismatch_fields"] == [
        "control_type",
        "has_tls",
        "internal_function_counts",
        "movement_signature_counts",
        "request_bit_lengths_ok",
    ]
    assert comparison["teacher"]["control_type"] == "traffic_light"
    assert comparison["candidate"]["control_type"] == "priority"
    assert comparison["teacher"]["internal_function_counts"] == {"crossing": 1, "internal": 1, "walkingarea": 1}
    assert comparison["candidate"]["internal_function_counts"] == {"crossing": 0, "internal": 1, "walkingarea": 0}

    delta_file = Path(report["junction_pattern_comparisons_file"])
    rows = list(csv.DictReader(delta_file.read_text(encoding="utf-8").splitlines()))
    assert rows[0]["junction_id"] == "cluster_a_b"
    assert (
        rows[0]["mismatch_fields"]
        == "control_type;has_tls;internal_function_counts;movement_signature_counts;request_bit_lengths_ok"
    )
    assert rows[0]["teacher_control_type"] == "traffic_light"
    assert rows[0]["candidate_control_type"] == "priority"
    assert json.loads(rows[0]["teacher_internal_function_counts"]) == {"crossing": 1, "internal": 1, "walkingarea": 1}
    assert json.loads(rows[0]["candidate_internal_function_counts"]) == {"crossing": 0, "internal": 1, "walkingarea": 0}

    delta = json.loads(Path(report["junction_teacher_delta_file"]).read_text(encoding="utf-8"))
    assert delta["schema_version"] == 1
    assert delta["reference_net_file"] == str(reference)
    assert delta["candidate_net_file"] == str(candidate)
    assert delta["junction_pattern_mismatch_field_counts"] == report["junction_pattern_mismatch_field_counts"]
    assert delta["junction_structural_signature_status"] == "fail"
    assert delta["junction_structural_signature_missing_counts"] == report["junction_structural_signature_missing_counts"]
    assert delta["network_structural_delta_status"] == "fail"
    assert delta["network_structural_missing_counts"] == report["network_structural_missing_counts"]
    assert delta["network_structural_extra_counts"] == report["network_structural_extra_counts"]
    assert delta["junction_pattern_comparisons"][0]["teacher"]["control_type"] == "traffic_light"


def test_reference_join_audit_accepts_explicit_approach_edge_equivalence(tmp_path: Path) -> None:
    reference = tmp_path / "reference.net.xml"
    candidate = tmp_path / "candidate.net.xml"
    reference.write_text(
        """<net>
  <edge id="teacher_west" from="w" to="j"><lane id="teacher_west_0" index="0" allow="passenger" shape="-10,0 0,0"/></edge>
  <edge id="teacher_south" from="s" to="j"><lane id="teacher_south_0" index="0" allow="passenger" shape="0,-10 0,0"/></edge>
  <edge id="teacher_east" from="e" to="j"><lane id="teacher_east_0" index="0" allow="passenger" shape="10,0 0,0"/></edge>
  <edge id="teacher_west_out" from="j" to="wo"><lane id="teacher_west_out_0" index="0" allow="passenger" shape="0,0 -10,0"/></edge>
  <edge id="teacher_south_out" from="j" to="so"><lane id="teacher_south_out_0" index="0" allow="passenger" shape="0,0 0,-10"/></edge>
  <edge id="teacher_east_out" from="j" to="eo"><lane id="teacher_east_out_0" index="0" allow="passenger" shape="0,0 10,0"/></edge>
  <junction id="j" type="traffic_light" x="0" y="0" incLanes="teacher_west_0 teacher_south_0 teacher_east_0" intLanes=""/>
  <connection from="teacher_west" to="teacher_west_out" fromLane="0" toLane="0" tl="j" linkIndex="0" dir="r"/>
  <connection from="teacher_south" to="teacher_south_out" fromLane="0" toLane="0" tl="j" linkIndex="1" dir="s"/>
  <connection from="teacher_east" to="teacher_east_out" fromLane="0" toLane="0" tl="j" linkIndex="2" dir="l"/>
  <tlLogic id="j" type="static" programID="0"><phase duration="30" state="G"/></tlLogic>
</net>""",
        encoding="utf-8",
    )
    candidate.write_text(
        """<net>
  <edge id="candidate_west" from="w" to="j"><lane id="candidate_west_0" index="0" allow="passenger" shape="-10,0 0,0"/></edge>
  <edge id="candidate_south" from="s" to="j"><lane id="candidate_south_0" index="0" allow="passenger" shape="0,-10 0,0"/></edge>
  <edge id="candidate_east" from="e" to="j"><lane id="candidate_east_0" index="0" allow="passenger" shape="10,0 0,0"/></edge>
  <edge id="candidate_west_out" from="j" to="wo"><lane id="candidate_west_out_0" index="0" allow="passenger" shape="0,0 -10,0"/></edge>
  <edge id="candidate_south_out" from="j" to="so"><lane id="candidate_south_out_0" index="0" allow="passenger" shape="0,0 0,-10"/></edge>
  <edge id="candidate_east_out" from="j" to="eo"><lane id="candidate_east_out_0" index="0" allow="passenger" shape="0,0 10,0"/></edge>
  <junction id="j" type="traffic_light" x="0" y="0" incLanes="candidate_west_0 candidate_south_0 candidate_east_0" intLanes=""/>
  <connection from="candidate_west" to="candidate_west_out" fromLane="0" toLane="0" tl="j" linkIndex="0" dir="r"/>
  <connection from="candidate_south" to="candidate_south_out" fromLane="0" toLane="0" tl="j" linkIndex="1" dir="s"/>
  <connection from="candidate_east" to="candidate_east_out" fromLane="0" toLane="0" tl="j" linkIndex="2" dir="l"/>
  <tlLogic id="j" type="static" programID="0"><phase duration="30" state="G"/></tlLogic>
</net>""",
        encoding="utf-8",
    )

    report = audit_reference_join_patterns(
        reference_net_file=reference,
        candidate_net_file=candidate,
        output_dir=tmp_path / "audit",
        structural_only=True,
        equivalent_approach_edge_map={
            "teacher_west": "candidate_west",
            "teacher_south": "candidate_south",
            "teacher_east": "candidate_east",
        },
    )

    assert report["junction_pattern_comparison_status"] == "pass"
    assert report["junction_pattern_mismatch_count"] == 0
    assert report["junction_pattern_comparisons"][0]["approach_edge_equivalence_applied"] is True


def test_reference_join_audit_structural_only_reports_extra_network_structure(tmp_path: Path) -> None:
    reference = tmp_path / "reference.net.xml"
    candidate = tmp_path / "candidate.net.xml"
    reference.write_text(
        """<net>
  <edge id="in"><lane id="in_0" index="0"/></edge>
  <junction id="j" type="priority"/>
  <connection from="in" to="out"/>
</net>""",
        encoding="utf-8",
    )
    candidate.write_text(
        """<net>
  <edge id="in"><lane id="in_0" index="0"/></edge>
  <edge id=":j_w0" function="walkingarea"><lane id=":j_w0_0" index="0"/></edge>
  <junction id="j" type="priority"/>
  <junction id="tls_extra" type="traffic_light"/>
  <connection from="in" to="out"/>
  <connection from="a" to="b" tl="tls_extra" linkIndex="0"/>
  <tlLogic id="tls_extra" type="actuated" programID="0"><phase duration="30" state="G"/></tlLogic>
</net>""",
        encoding="utf-8",
    )

    report = audit_reference_join_patterns(
        reference_net_file=reference,
        candidate_net_file=candidate,
        output_dir=tmp_path / "audit",
        structural_only=True,
    )

    assert report["network_structural_delta_status"] == "fail"
    assert report["network_structural_missing_counts"] == {}
    assert report["network_structural_extra_counts"] == {
        "connection_count": 1,
        "tl_logic_count": 1,
        "traffic_light_junction_count": 1,
        "tls_controlled_connection_count": 1,
        "walkingarea_edge_count": 1,
    }
    assert report["network_structural_junction_type_missing_counts"] == {}
    assert report["network_structural_junction_type_extra_counts"] == {"traffic_light": 1}


def test_reference_join_audit_summarizes_tls_control_semantics(tmp_path: Path) -> None:
    reference = tmp_path / "reference.net.xml"
    candidate = tmp_path / "candidate.net.xml"
    reference.write_text(
        """<net>
  <edge id=":j1_0" function="internal"><lane id=":j1_0_0" index="0"/></edge>
  <edge id=":j2_0" function="internal"><lane id=":j2_0_0" index="0"/></edge>
  <edge id=":j2_1" function="internal"><lane id=":j2_1_0" index="0"/></edge>
  <junction id="j1" type="traffic_light"/>
  <junction id="j2" type="traffic_light"/>
  <junction id="j3" type="traffic_light"/>
  <connection from="a" to="b" tl="tlA" linkIndex="0" via=":j1_0_0"/>
  <connection from="c" to="d" tl="tlA" linkIndex="0" via=":j2_0_0"/>
  <connection from="e" to="f" tl="tlA" linkIndex="3" via=":j2_1_0"/>
  <tlLogic id="tlA" type="actuated" programID="0"><phase duration="30" state="rrrr"/></tlLogic>
</net>""",
        encoding="utf-8",
    )
    candidate.write_text("<net/>", encoding="utf-8")

    report = audit_reference_join_patterns(
        reference_net_file=reference,
        candidate_net_file=candidate,
        output_dir=tmp_path / "audit",
        structural_only=True,
    )
    summary = report["reference_network_structural_summary"]

    assert summary["tl_logic_controlled_connection_count_distribution"] == {"3": 1}
    assert summary["tl_logic_controlled_junction_count_distribution"] == {"2": 1}
    assert summary["multi_junction_tl_logic_count"] == 1
    assert summary["traffic_light_junction_without_tls_connection_count"] == 1
    assert summary["tls_shared_linkindex_group_count"] == 1
    assert summary["tls_sparse_linkindex_tl_logic_count"] == 1
    assert report["network_structural_missing_counts"]["multi_junction_tl_logic_count"] == 1
    assert report["network_structural_missing_counts"]["tls_shared_linkindex_group_count"] == 1
    assert report["network_structural_missing_counts"]["tls_sparse_linkindex_tl_logic_count"] == 1


def test_reference_join_audit_builds_tls_control_review_queue(tmp_path: Path) -> None:
    reference = tmp_path / "reference.net.xml"
    candidate = tmp_path / "candidate.net.xml"
    reference.write_text(
        """<net>
  <edge id=":r1_0" function="internal"><lane id=":r1_0_0" index="0"/></edge>
  <edge id=":r2_0" function="internal"><lane id=":r2_0_0" index="0"/></edge>
  <junction id="r1" type="traffic_light"/>
  <junction id="r2" type="traffic_light"/>
  <connection from="ra" to="rb" tl="tlRef" linkIndex="0" via=":r1_0_0"/>
  <connection from="rc" to="rd" tl="tlRef" linkIndex="1" via=":r2_0_0"/>
  <tlLogic id="tlRef" type="actuated" programID="0"><phase duration="30" state="GG"/></tlLogic>
</net>""",
        encoding="utf-8",
    )
    candidate.write_text(
        """<net>
  <edge id=":j1_0" function="internal"><lane id=":j1_0_0" index="0"/></edge>
  <edge id=":j2_0" function="internal"><lane id=":j2_0_0" index="0"/></edge>
  <edge id=":j3_0" function="internal"><lane id=":j3_0_0" index="0"/></edge>
  <edge id=":j4_0" function="internal"><lane id=":j4_0_0" index="0"/></edge>
  <edge id=":j4_1" function="internal"><lane id=":j4_1_0" index="0"/></edge>
  <junction id="j1" type="traffic_light"/>
  <junction id="j2" type="traffic_light"/>
  <junction id="j3" type="traffic_light"/>
  <junction id="j4" type="traffic_light"/>
  <junction id="j5" type="traffic_light"/>
  <connection from="a" to="b" tl="tlWide" linkIndex="0" via=":j1_0_0"/>
  <connection from="c" to="d" tl="tlWide" linkIndex="1" via=":j2_0_0"/>
  <connection from="e" to="f" tl="tlWide" linkIndex="2" via=":j3_0_0"/>
  <connection from="g" to="h" tl="tlSparse" linkIndex="0" via=":j4_0_0"/>
  <connection from="i" to="k" tl="tlSparse" linkIndex="3" via=":j4_1_0"/>
  <tlLogic id="tlWide" type="actuated" programID="0"><phase duration="30" state="GGG"/></tlLogic>
  <tlLogic id="tlSparse" type="actuated" programID="0"><phase duration="30" state="rrrr"/></tlLogic>
</net>""",
        encoding="utf-8",
    )

    report = audit_reference_join_patterns(
        reference_net_file=reference,
        candidate_net_file=candidate,
        output_dir=tmp_path / "audit",
        structural_only=True,
    )

    assert report["tls_control_review_status"] == "needs_review"
    assert report["tls_control_review_queue_count"] == 3
    queue = report["tls_control_review_queue"]
    assert [entry["review_type"] for entry in queue] == [
        "split_multi_junction_tls",
        "inspect_sparse_linkindex",
        "bind_or_downgrade_uncontrolled_traffic_light_junction",
    ]
    assert [entry["repair_category"] for entry in queue] == [
        "tls_controller_cardinality_repair",
        "tls_linkindex_phase_repair",
        "tls_controller_cardinality_repair",
    ]
    assert queue[0]["tl_id"] == "tlWide"
    assert queue[0]["controlled_junction_count"] == 3
    assert queue[0]["reference_max_controlled_junction_count"] == 2
    assert queue[0]["junction_ids"] == ["j1", "j2", "j3"]
    assert queue[1]["tl_id"] == "tlSparse"
    assert queue[1]["phase_state_length"] == 4
    assert queue[1]["controlled_linkindex_count"] == 2
    assert queue[1]["linkindexes"] == [0, 3]
    assert queue[2]["junction_id"] == "j5"


def test_reference_join_audit_queues_extra_low_vehicle_approach_tls(tmp_path: Path) -> None:
    reference = tmp_path / "reference.net.xml"
    candidate = tmp_path / "candidate.net.xml"
    reference.write_text(
        """<net>
  <edge id="r_in" from="ra" to="rj"><lane id="r_in_0" index="0" allow="passenger"/></edge>
  <edge id="r_out" from="rj" to="rb"><lane id="r_out_0" index="0" allow="passenger"/></edge>
  <edge id=":rj_0" function="internal"><lane id=":rj_0_0" index="0"/></edge>
  <junction id="rj" type="traffic_light"/>
  <connection from="r_in" to="r_out" tl="tlRef" linkIndex="0" via=":rj_0_0"/>
  <tlLogic id="tlRef" type="actuated" programID="0"><phase duration="30" state="G"/></tlLogic>
</net>""",
        encoding="utf-8",
    )
    candidate.write_text(
        """<net>
  <edge id="c1_in" from="c1a" to="c1"><lane id="c1_in_0" index="0" allow="passenger"/></edge>
  <edge id="c1_out" from="c1" to="c1b"><lane id="c1_out_0" index="0" allow="passenger"/></edge>
  <edge id="c2_in" from="c2a" to="c2"><lane id="c2_in_0" index="0" allow="passenger"/></edge>
  <edge id="c2_out" from="c2" to="c2b"><lane id="c2_out_0" index="0" allow="passenger"/></edge>
  <edge id=":c1_0" function="internal"><lane id=":c1_0_0" index="0"/></edge>
  <edge id=":c2_0" function="internal"><lane id=":c2_0_0" index="0"/></edge>
  <junction id="c1" type="traffic_light"/>
  <junction id="c2" type="traffic_light"/>
  <connection from="c1_in" to="c1_out" tl="tlKeep" linkIndex="0" via=":c1_0_0"/>
  <connection from="c2_in" to="c2_out" tl="tlExtra" linkIndex="0" via=":c2_0_0"/>
  <tlLogic id="tlKeep" type="actuated" programID="0"><phase duration="30" state="G"/></tlLogic>
  <tlLogic id="tlExtra" type="actuated" programID="0"><phase duration="30" state="G"/></tlLogic>
</net>""",
        encoding="utf-8",
    )

    report = audit_reference_join_patterns(
        reference_net_file=reference,
        candidate_net_file=candidate,
        output_dir=tmp_path / "audit",
        structural_only=True,
    )

    assert report["reference_network_structural_summary"]["low_passenger_approach_tl_logic_count"] == 1
    assert report["candidate_network_structural_summary"]["low_passenger_approach_tl_logic_count"] == 2
    queue = report["tls_control_review_queue"]
    assert len(queue) == 1
    assert queue[0]["repair_category"] == "tls_reality_review"
    assert queue[0]["review_type"] == "downgrade_low_vehicle_approach_tls"
    assert queue[0]["tl_id"] == "tlExtra"
    assert queue[0]["controlled_passenger_from_edge_count"] == 1
    assert queue[0]["reference_low_passenger_approach_tl_logic_count"] == 1


def test_reference_join_audit_queues_missing_reference_tls_semantics(tmp_path: Path) -> None:
    reference = tmp_path / "reference.net.xml"
    candidate = tmp_path / "candidate.net.xml"
    reference.write_text(
        """<net>
  <edge id=":r1_0" function="internal"><lane id=":r1_0_0" index="0"/></edge>
  <edge id=":r2_0" function="internal"><lane id=":r2_0_0" index="0"/></edge>
  <edge id=":r2_1" function="internal"><lane id=":r2_1_0" index="0"/></edge>
  <junction id="r1" type="traffic_light"/>
  <junction id="r2" type="traffic_light"/>
  <connection from="ra" to="rb" tl="tlRef" linkIndex="0" via=":r1_0_0"/>
  <connection from="rc" to="rd" tl="tlRef" linkIndex="0" via=":r2_0_0"/>
  <connection from="re" to="rf" tl="tlRef" linkIndex="3" via=":r2_1_0"/>
  <tlLogic id="tlRef" type="actuated" programID="0"><phase duration="30" state="rrrr"/></tlLogic>
</net>""",
        encoding="utf-8",
    )
    candidate.write_text(
        """<net>
  <edge id=":c1_0" function="internal"><lane id=":c1_0_0" index="0"/></edge>
  <junction id="c1" type="traffic_light"/>
  <connection from="ca" to="cb" tl="tlCand" linkIndex="0" via=":c1_0_0"/>
  <tlLogic id="tlCand" type="actuated" programID="0"><phase duration="30" state="G"/></tlLogic>
</net>""",
        encoding="utf-8",
    )

    report = audit_reference_join_patterns(
        reference_net_file=reference,
        candidate_net_file=candidate,
        output_dir=tmp_path / "audit",
        structural_only=True,
    )

    assert report["tls_control_review_status"] == "needs_review"
    queue = report["tls_control_review_queue"]
    assert [entry["review_type"] for entry in queue] == [
        "restore_tls_controlled_connections",
        "restore_reference_multi_junction_tls_scope",
        "restore_shared_linkindex_groups",
        "inspect_reference_sparse_linkindex_programs",
    ]
    assert [entry["repair_category"] for entry in queue] == [
        "tls_controller_cardinality_repair",
        "tls_controller_cardinality_repair",
        "tls_linkindex_phase_repair",
        "tls_linkindex_phase_repair",
    ]
    assert queue[0]["reference_count"] == 3
    assert queue[0]["candidate_count"] == 1
    assert queue[0]["missing_count"] == 2
    assert queue[1]["missing_count"] == 1
    assert queue[2]["missing_count"] == 1
    assert queue[3]["missing_count"] == 1


def test_reference_join_audit_structural_only_skips_case_matching(monkeypatch, tmp_path: Path) -> None:
    reference = tmp_path / "reference.net.xml"
    candidate = tmp_path / "candidate.net.xml"
    reference.write_text(
        """<net>
  <edge id="west_in" from="w" to="cluster_a_b"><lane id="west_in_0" index="0" allow="passenger" shape="-10,0 0,0"/></edge>
  <edge id="south_in" from="s" to="cluster_a_b"><lane id="south_in_0" index="0" allow="passenger" shape="0,-10 0,0"/></edge>
  <edge id="north_in" from="n" to="cluster_a_b"><lane id="north_in_0" index="0" allow="passenger" shape="0,10 0,0"/></edge>
  <edge id="east_out" from="cluster_a_b" to="e"><lane id="east_out_0" index="0" allow="passenger" shape="0,0 10,0"/></edge>
  <edge id="south_out" from="cluster_a_b" to="so"><lane id="south_out_0" index="0" allow="passenger" shape="0,0 0,-10"/></edge>
  <edge id="north_out" from="cluster_a_b" to="no"><lane id="north_out_0" index="0" allow="passenger" shape="0,0 0,10"/></edge>
  <edge id=":cluster_a_b_0" function="internal"><lane id=":cluster_a_b_0_0" index="0"/></edge>
  <edge id=":cluster_a_b_c0" function="crossing"><lane id=":cluster_a_b_c0_0" index="0"/></edge>
  <edge id=":cluster_a_b_w0" function="walkingarea"><lane id=":cluster_a_b_w0_0" index="0"/></edge>
  <junction id="cluster_a_b" type="traffic_light" x="0" y="0" incLanes="west_in_0 south_in_0 north_in_0" intLanes=":cluster_a_b_0_0 :cluster_a_b_c0_0">
    <request index="0" response="0" foes="0" cont="0"/>
  </junction>
  <connection from="west_in" to="east_out" fromLane="0" toLane="0" tl="cluster_a_b" linkIndex="0" dir="s"/>
  <connection from="south_in" to="south_out" fromLane="0" toLane="0" tl="cluster_a_b" linkIndex="1" dir="s"/>
  <connection from="north_in" to="north_out" fromLane="0" toLane="0" tl="cluster_a_b" linkIndex="2" dir="s"/>
  <tlLogic id="cluster_a_b" type="actuated" programID="0"><phase duration="30" state="GGG"/></tlLogic>
</net>""",
        encoding="utf-8",
    )
    candidate.write_text(
        """<net>
  <edge id="west_in" from="w" to="cluster_a_b"><lane id="west_in_0" index="0" allow="passenger" shape="-10,0 0,0"/></edge>
  <edge id="south_in" from="s" to="cluster_a_b"><lane id="south_in_0" index="0" allow="passenger" shape="0,-10 0,0"/></edge>
  <edge id="north_in" from="n" to="cluster_a_b"><lane id="north_in_0" index="0" allow="passenger" shape="0,10 0,0"/></edge>
  <edge id="east_out" from="cluster_a_b" to="e"><lane id="east_out_0" index="0" allow="passenger" shape="0,0 10,0"/></edge>
  <edge id="south_out" from="cluster_a_b" to="so"><lane id="south_out_0" index="0" allow="passenger" shape="0,0 0,-10"/></edge>
  <edge id="north_out" from="cluster_a_b" to="no"><lane id="north_out_0" index="0" allow="passenger" shape="0,0 0,10"/></edge>
  <edge id=":cluster_a_b_0" function="internal"><lane id=":cluster_a_b_0_0" index="0"/></edge>
  <junction id="cluster_a_b" type="priority" x="0" y="0" incLanes="west_in_0 south_in_0 north_in_0" intLanes=":cluster_a_b_0_0"/>
  <connection from="west_in" to="east_out" fromLane="0" toLane="0" dir="s"/>
  <connection from="south_in" to="south_out" fromLane="0" toLane="0" dir="s"/>
  <connection from="north_in" to="north_out" fromLane="0" toLane="0" dir="s"/>
</net>""",
        encoding="utf-8",
    )

    monkeypatch.setattr(
        "torii_sumo.core.reference_join_audit._reference_join_cases",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("case matching should be skipped")),
    )
    monkeypatch.setattr(
        "torii_sumo.core.reference_join_audit.audit_topology_fragmentation",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("topology matching should be skipped")),
    )

    report = audit_reference_join_patterns(
        reference_net_file=reference,
        candidate_net_file=candidate,
        output_dir=tmp_path / "audit",
        prefix="structural",
        structural_only=True,
    )

    assert report["status"] == "pass"
    assert report["audit_mode"] == "structural_only"
    assert report["reference_case_count"] == 0
    assert report["matched_case_count"] == 0
    assert report["cases_file"] == ""
    assert report["candidate_topology_audit_file"] == ""
    assert report["junction_pattern_comparison_status"] == "fail"
    assert report["junction_pattern_mismatch_count"] == 1
    assert report["junction_pattern_mismatch_field_counts"] == {
        "control_type": 1,
        "has_tls": 1,
        "internal_function_counts": 1,
        "movement_signature_counts": 1,
    }
    assert report["junction_pattern_comparisons"][0]["junction_id"] == "cluster_a_b"
    assert report["junction_pattern_comparisons_file"]
    assert report["network_structural_delta_status"] == "fail"
    assert report["network_structural_missing_counts"] == {
        "crossing_edge_count": 1,
        "request_count": 1,
        "tl_logic_count": 1,
        "traffic_light_junction_count": 1,
        "tls_controlled_connection_count": 3,
        "walkingarea_edge_count": 1,
    }
    assert report["network_structural_extra_counts"] == {}
    assert report["network_structural_junction_type_missing_counts"] == {"traffic_light": 1}
    assert report["network_structural_junction_type_extra_counts"] == {"priority": 1}
    assert Path(report["junction_teacher_delta_file"]).is_file()
    assert Path(report["junction_pattern_comparisons_file"]).is_file()
    summary = json.loads(Path(report["summary_file"]).read_text(encoding="utf-8"))
    assert summary["audit_mode"] == "structural_only"
    assert summary["junction_pattern_comparison_status"] == "fail"
    assert any("structural-only mode" in warning for warning in report["warnings"])


def test_reference_join_audit_shortens_long_artifact_names_portably(tmp_path: Path) -> None:
    reference = tmp_path / "reference.net.xml"
    candidate = tmp_path / "candidate.net.xml"
    reference.write_text("<net/>", encoding="utf-8")
    candidate.write_text("<net/>", encoding="utf-8")
    output_dir = tmp_path / "nested_reference_delta"
    long_prefix = "reference_visual_detail_tls_connection_repair_reference_delta_" * 4

    report = audit_reference_join_patterns(
        reference_net_file=reference,
        candidate_net_file=candidate,
        output_dir=output_dir,
        prefix=long_prefix,
        structural_only=True,
    )

    assert report["status"] == "pass"
    artifact_paths = [
        Path(report["junction_teacher_delta_file"]),
        Path(report["junction_pattern_comparisons_file"]),
        Path(report["junction_pattern_templates_file"]),
        Path(report["summary_file"]),
    ]
    assert all(path.is_file() for path in artifact_paths)
    assert all(len(str(path.resolve())) <= 240 for path in artifact_paths)
    assert all("_" in path.stem for path in artifact_paths)


def test_reference_join_audit_reports_pedestrian_lane_counts_by_edge_type(tmp_path: Path) -> None:
    reference = tmp_path / "reference.net.xml"
    candidate = tmp_path / "candidate.net.xml"
    reference.write_text(
        """<net>
  <edge id="street" type="highway.residential" from="a" to="b">
    <lane id="street_0" index="0" allow="pedestrian"/>
    <lane id="street_1" index="1" allow="passenger"/>
  </edge>
  <junction id="a" type="priority"/>
  <junction id="b" type="priority"/>
</net>""",
        encoding="utf-8",
    )
    candidate.write_text(
        """<net>
  <edge id="street" type="highway.residential" from="a" to="b">
    <lane id="street_0" index="0" allow="passenger"/>
  </edge>
  <junction id="a" type="priority"/>
  <junction id="b" type="priority"/>
</net>""",
        encoding="utf-8",
    )

    report = audit_reference_join_patterns(
        reference_net_file=reference,
        candidate_net_file=candidate,
        output_dir=tmp_path / "audit",
        prefix="ped_policy",
        structural_only=True,
    )

    assert report["reference_network_structural_summary"]["plain_edge_type_counts"] == {"highway.residential": 1}
    assert report["reference_network_structural_summary"]["plain_edge_type_pedestrian_lane_counts"] == {
        "highway.residential": 1
    }
    assert report["candidate_network_structural_summary"]["plain_edge_type_pedestrian_lane_counts"] == {}


def test_reference_join_audit_structural_only_samples_more_than_five_common_tls(
    monkeypatch,
    tmp_path: Path,
) -> None:
    reference = tmp_path / "reference.net.xml"
    candidate = tmp_path / "candidate.net.xml"
    junctions = "\n".join(
        f'  <junction id="tls{index}" type="traffic_light" incLanes="edge{index}_0" intLanes=""/>'
        for index in range(6)
    )
    reference.write_text(f"<net>\n{junctions}\n</net>", encoding="utf-8")
    candidate.write_text(f"<net>\n{junctions}\n</net>", encoding="utf-8")
    sampled_ids: list[list[str]] = []

    def fake_extract_junction_pattern_index(_net_file: Path, **kwargs):
        sampled_ids.append(list(kwargs["junction_ids"]))
        return []

    monkeypatch.setattr(
        reference_join_audit_module,
        "extract_junction_pattern_index",
        fake_extract_junction_pattern_index,
    )

    report = audit_reference_join_patterns(
        reference_net_file=reference,
        candidate_net_file=candidate,
        output_dir=tmp_path / "audit",
        prefix="structural",
        structural_only=True,
    )

    assert report["status"] == "pass"
    assert len(sampled_ids[0]) == 6
    assert not any("sampled 5" in warning for warning in report["warnings"])


def test_reference_join_audit_keeps_audit_when_pattern_extraction_fails(tmp_path: Path) -> None:
    reference = tmp_path / "reference.net.xml"
    candidate = tmp_path / "candidate.net.xml"
    reference.write_text(
        """<net>
  <edge id="west_in" from="w" to="cluster_a_b"><lane id="west_in_0" index="0" allow="passenger" shape="bad,0 0,0"/></edge>
  <edge id="south_in" from="s" to="cluster_a_b"><lane id="south_in_0" index="0" allow="passenger" shape="0,-10 0,0"/></edge>
  <edge id="north_in" from="n" to="cluster_a_b"><lane id="north_in_0" index="0" allow="passenger" shape="0,10 0,0"/></edge>
  <edge id="east_out" from="cluster_a_b" to="e"><lane id="east_out_0" index="0" allow="passenger" shape="0,0 10,0"/></edge>
  <junction id="cluster_a_b" type="priority" x="0" y="0" incLanes="west_in_0 south_in_0 north_in_0" intLanes=""/>
  <connection from="west_in" to="east_out" fromLane="0" toLane="0" dir="s"/>
</net>""",
        encoding="utf-8",
    )
    candidate.write_text(
        """<net>
  <edge id="internal_ab" from="a" to="b"><lane id="internal_ab_0" index="0" length="7" shape="-3,0 4,0"/></edge>
  <junction id="a" x="-3" y="0" type="traffic_light"/>
  <junction id="b" x="4" y="0" type="traffic_light"/>
</net>""",
        encoding="utf-8",
    )

    report = audit_reference_join_patterns(
        reference_net_file=reference,
        candidate_net_file=candidate,
        output_dir=tmp_path / "audit",
        reference_cluster_prefix="cluster_",
        candidate_cluster_radius_m=20,
        candidate_min_cluster_nodes=2,
        match_radius_m=20,
    )

    assert report["status"] == "pass"
    assert report["junction_pattern_index"] == []
    assert any("junction pattern extraction failed" in warning for warning in report["warnings"])
