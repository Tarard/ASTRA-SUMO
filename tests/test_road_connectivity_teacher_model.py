from pathlib import Path
import xml.etree.ElementTree as ET

from torii_sumo.core.road_connectivity_teacher_model import (
    audit_road_connectivity_parity,
    build_internal_movement_replay_audit,
    build_internal_movement_owner_approach_edge_map,
    build_internal_movement_owner_approach_edge_chain_map,
    build_internal_movement_owner_internal_lane_map,
    build_internal_movement_owner_missing_approach_edge_repair_candidates,
    build_internal_movement_owner_road_connectivity_parity_audit,
    build_internal_movement_owner_road_span_repair_candidates,
    build_internal_movement_owner_road_lane_repair_candidates,
    build_road_connection_topology_replay_audit,
    build_road_lane_template_edge_subset_repair_candidates,
    build_road_lane_template_repair_candidates,
    build_road_lane_template_single_edge_repair_candidates,
    build_road_template_repair_queue,
    canonical_road_connectivity_bundle,
    compare_net_road_template_parity,
    compare_road_template_summaries,
    compare_road_connectivity_bundles,
    evaluate_road_template_repair_promotion,
    run_road_lane_template_batch_repair_probe,
    run_road_lane_template_repair_probe,
    summarize_net_road_connection_templates,
    summarize_net_road_lane_model_templates,
    summarize_road_lane_model_templates,
    write_road_lane_template_repair_candidate,
    write_road_connection_topology_replay_candidate,
    write_internal_movement_owner_replay_candidate,
    write_internal_movement_owner_bundle_replacement_candidate,
    write_internal_movement_owner_missing_approach_edge_repair_candidate,
    write_internal_movement_owner_layered_teacher_replay_candidate,
    write_internal_movement_owner_ready_road_span_endpoint_replay_candidate,
    write_internal_movement_owner_teacher_replay_candidate,
    write_internal_movement_owner_road_span_repair_candidate,
    write_road_connectivity_self_replay_net,
    write_road_connectivity_split_root_alias_repair_candidate,
)


def test_canonical_road_connectivity_bundle_extracts_edge_chain_and_connections(tmp_path: Path) -> None:
    net_file = tmp_path / "teacher.net.xml"
    net_file.write_text(
        """<net version="1.20">
  <edge id="west" from="w" to="j1" name="Main"><lane id="west_0" index="0" allow="passenger" speed="13.89" shape="-20,0 -10,0"/></edge>
  <edge id="mid" from="j1" to="j2" name="Main"><lane id="mid_0" index="0" allow="passenger" speed="13.89" shape="-10,0 0,0"/></edge>
  <edge id="east" from="j2" to="e" name="Main"><lane id="east_0" index="0" allow="passenger" speed="13.89" shape="0,0 10,0"/></edge>
  <edge id="sidewalk" from="w" to="j2" type="highway.footway"><lane id="sidewalk_0" index="0" allow="pedestrian" shape="-20,2 0,2"/></edge>
  <edge id=":j1_0" function="internal"><lane id=":j1_0_0" index="0" shape="-10,0 -9,0"/></edge>
  <junction id="w" type="dead_end" x="-20" y="0" incLanes="" intLanes=""/>
  <junction id="j1" type="priority" x="-10" y="0" incLanes="west_0" intLanes=":j1_0_0"/>
  <junction id="j2" type="priority" x="0" y="0" incLanes="mid_0 sidewalk_0" intLanes=""/>
  <junction id="e" type="dead_end" x="10" y="0" incLanes="east_0" intLanes=""/>
  <connection from="west" to="mid" fromLane="0" toLane="0" dir="s"/>
  <connection from="mid" to="east" fromLane="0" toLane="0" dir="s"/>
</net>""",
        encoding="utf-8",
    )

    bundle = canonical_road_connectivity_bundle(net_file, seed_edge_ids=["mid"], hop_radius=1)

    assert [edge["id"] for edge in bundle["edges"]] == ["east", "mid", "sidewalk", "west"]
    assert bundle["connections"] == [
        {"dir": "s", "from": "mid", "fromLane": "0", "to": "east", "toLane": "0"},
        {"dir": "s", "from": "west", "fromLane": "0", "to": "mid", "toLane": "0"},
    ]
    assert bundle["summary"] == {
        "edge_count": 4,
        "junction_count": 4,
        "connection_count": 2,
        "missing_reference_count": 0,
        "seed_edge_count": 1,
        "missing_seed_edge_ids": [],
    }


def test_canonical_road_connectivity_bundle_reports_missing_seed_edges(tmp_path: Path) -> None:
    net_file = tmp_path / "candidate.net.xml"
    net_file.write_text(
        """<net>
  <edge id="a" from="n1" to="n2"><lane id="a_0" index="0" allow="passenger" shape="0,0 10,0"/></edge>
  <junction id="n1" type="dead_end" x="0" y="0" incLanes="" intLanes=""/>
  <junction id="n2" type="dead_end" x="10" y="0" incLanes="a_0" intLanes=""/>
</net>""",
        encoding="utf-8",
    )

    bundle = canonical_road_connectivity_bundle(net_file, seed_edge_ids=["a", "missing"], hop_radius=1)

    assert bundle["summary"]["seed_edge_count"] == 2
    assert bundle["summary"]["missing_seed_edge_ids"] == ["missing"]


def test_compare_road_connectivity_bundles_reports_exact_edge_and_connection_delta(tmp_path: Path) -> None:
    teacher_net = tmp_path / "teacher.net.xml"
    candidate_net = tmp_path / "candidate.net.xml"
    teacher_net.write_text(
        """<net>
  <edge id="a" from="n1" to="n2"><lane id="a_0" index="0" allow="passenger" shape="0,0 10,0"/></edge>
  <edge id="b" from="n2" to="n3"><lane id="b_0" index="0" allow="passenger" shape="10,0 20,0"/></edge>
  <junction id="n1" type="dead_end" x="0" y="0" incLanes="" intLanes=""/>
  <junction id="n2" type="priority" x="10" y="0" incLanes="a_0" intLanes=""/>
  <junction id="n3" type="dead_end" x="20" y="0" incLanes="b_0" intLanes=""/>
  <connection from="a" to="b" fromLane="0" toLane="0" dir="s"/>
</net>""",
        encoding="utf-8",
    )
    candidate_net.write_text(
        """<net>
  <edge id="a" from="n1" to="n2"><lane id="a_0" index="0" allow="passenger" shape="0,0 10,0"/></edge>
  <edge id="c" from="n2" to="n4"><lane id="c_0" index="0" allow="passenger" shape="10,0 20,1"/></edge>
  <junction id="n1" type="dead_end" x="0" y="0" incLanes="" intLanes=""/>
  <junction id="n2" type="priority" x="10" y="0" incLanes="a_0" intLanes=""/>
  <junction id="n4" type="dead_end" x="20" y="1" incLanes="c_0" intLanes=""/>
  <connection from="a" to="c" fromLane="0" toLane="0" dir="r"/>
</net>""",
        encoding="utf-8",
    )
    teacher = canonical_road_connectivity_bundle(teacher_net, seed_edge_ids=["a", "b"], hop_radius=1)
    candidate = canonical_road_connectivity_bundle(candidate_net, seed_edge_ids=["a", "b"], hop_radius=1)

    report = compare_road_connectivity_bundles(teacher, candidate)

    assert report == {
        "status": "fail",
        "candidate_missing_seed_edge_ids": ["b"],
        "edge_ids": {"missing_in_candidate": ["b"], "extra_in_candidate": ["c"]},
        "common_edge_geometry_mismatches": [],
        "connections": {
            "missing_in_candidate": [{"dir": "s", "from": "a", "fromLane": "0", "to": "b", "toLane": "0"}],
            "extra_in_candidate": [{"dir": "r", "from": "a", "fromLane": "0", "to": "c", "toLane": "0"}],
        },
        "summary": {
            "teacher_edge_count": 2,
            "candidate_edge_count": 2,
            "common_edge_count": 1,
            "common_edge_geometry_mismatch_count": 0,
            "teacher_connection_count": 1,
            "candidate_connection_count": 1,
        },
    }


def test_compare_road_connectivity_bundles_aliases_unambiguous_split_root_connections(tmp_path: Path) -> None:
    teacher_net = tmp_path / "teacher.net.xml"
    candidate_net = tmp_path / "candidate.net.xml"
    teacher_net.write_text(
        """<net>
  <edge id="in" from="n1" to="n2"><lane id="in_0" index="0" allow="passenger" shape="0,0 10,0"/></edge>
  <edge id="749560269#1" from="n2" to="n3"><lane id="749560269#1_0" index="0" allow="passenger" shape="10,0 20,0"/></edge>
  <junction id="n1" type="dead_end" x="0" y="0" incLanes="" intLanes=""/>
  <junction id="n2" type="priority" x="10" y="0" incLanes="in_0" intLanes=""/>
  <junction id="n3" type="dead_end" x="20" y="0" incLanes="749560269#1_0" intLanes=""/>
  <connection from="in" to="749560269#1" fromLane="0" toLane="0" dir="s"/>
</net>""",
        encoding="utf-8",
    )
    candidate_net.write_text(
        """<net>
  <edge id="in" from="n1" to="n2"><lane id="in_0" index="0" allow="passenger" shape="0,0 10,0"/></edge>
  <edge id="749560269" from="n2" to="n3"><lane id="749560269_0" index="0" allow="passenger" shape="10,0 20,0"/></edge>
  <junction id="n1" type="dead_end" x="0" y="0" incLanes="" intLanes=""/>
  <junction id="n2" type="priority" x="10" y="0" incLanes="in_0" intLanes=""/>
  <junction id="n3" type="dead_end" x="20" y="0" incLanes="749560269_0" intLanes=""/>
  <connection from="in" to="749560269" fromLane="0" toLane="0" dir="s"/>
</net>""",
        encoding="utf-8",
    )
    teacher = canonical_road_connectivity_bundle(teacher_net, seed_edge_ids=["in", "749560269#1"], hop_radius=1)
    candidate = canonical_road_connectivity_bundle(candidate_net, seed_edge_ids=["in", "749560269"], hop_radius=1)

    report = compare_road_connectivity_bundles(teacher, candidate)

    assert report["edge_ids"] == {
        "missing_in_candidate": ["749560269#1"],
        "extra_in_candidate": ["749560269"],
        "split_root_aliases": [
            {
                "root": "749560269",
                "teacher_edge_id": "749560269#1",
                "candidate_edge_id": "749560269",
            }
        ],
    }
    assert report["connections"] == {"missing_in_candidate": [], "extra_in_candidate": []}
    assert report["summary"]["split_root_alias_count"] == 1


def test_write_road_connectivity_split_root_alias_repair_renames_edge_and_references(tmp_path: Path) -> None:
    candidate_net = tmp_path / "candidate.net.xml"
    output_net = tmp_path / "candidate.alias_repaired.net.xml"
    candidate_net.write_text(
        """<net>
  <edge id="in" from="a" to="b"><lane id="in_0" index="0"/></edge>
  <edge id="road" from="b" to="c"><lane id="road_0" index="0"/></edge>
  <junction id="b" type="priority" incLanes="in_0" intLanes=""/>
  <junction id="c" type="priority" incLanes="road_0" intLanes=""/>
  <connection from="in" to="road" fromLane="0" toLane="0" dir="s"/>
  <crossing id=":b_c0" edges="road" priority="1"/>
</net>""",
        encoding="utf-8",
    )

    report = write_road_connectivity_split_root_alias_repair_candidate(
        candidate_net,
        output_net,
        [
            {
                "root": "road",
                "teacher_edge_id": "road#1",
                "candidate_edge_id": "road",
            }
        ],
    )

    root = ET.parse(output_net).getroot()
    assert report["renamed_edge_count"] == 1
    assert root.find("./edge[@id='road']") is None
    assert root.find("./edge[@id='road#1']/lane").attrib["id"] == "road#1_0"
    assert root.find("./connection[@from='in']").attrib["to"] == "road#1"
    assert root.find("./junction[@id='c']").attrib["incLanes"] == "road#1_0"
    assert root.find("./crossing").attrib["edges"] == "road#1"


def test_compare_road_connectivity_bundles_reports_same_id_geometry_mismatch(tmp_path: Path) -> None:
    teacher_net = tmp_path / "teacher.net.xml"
    candidate_net = tmp_path / "candidate.net.xml"
    teacher_net.write_text(
        """<net>
  <location netOffset="-100,-100"/>
  <edge id="a" from="n1" to="n2" type="highway.tertiary"><lane id="a_0" index="0" allow="pedestrian" disallow="truck" shape="100,100 110,100"/></edge>
  <junction id="n1" type="dead_end" x="100" y="100" incLanes="" intLanes=""/>
  <junction id="n2" type="dead_end" x="110" y="100" incLanes="a_0" intLanes=""/>
</net>""",
        encoding="utf-8",
    )
    candidate_net.write_text(
        """<net>
  <location netOffset="-200,-200"/>
  <edge id="a" from="n3" to="n4" type="highway.service">
    <lane id="a_0" index="0" shape="0,0 11,4"/>
    <lane id="a_1" index="1" allow="bicycle" shape="0,1 11,5"/>
  </edge>
  <junction id="n3" type="dead_end" x="0" y="0" incLanes="" intLanes=""/>
  <junction id="n4" type="dead_end" x="11" y="4" incLanes="a_0 a_1" intLanes=""/>
</net>""",
        encoding="utf-8",
    )
    teacher = canonical_road_connectivity_bundle(teacher_net, seed_edge_ids=["a"], hop_radius=1)
    candidate = canonical_road_connectivity_bundle(candidate_net, seed_edge_ids=["a"], hop_radius=1)

    report = compare_road_connectivity_bundles(teacher, candidate, geometry_tolerance=0.5)

    assert report["status"] == "fail"
    assert report["common_edge_geometry_mismatches"] == [
        {
            "edge_id": "a",
            "endpoint_delta": 4.123106,
            "teacher_from": "n1",
            "candidate_from": "n3",
            "teacher_to": "n2",
            "candidate_to": "n4",
            "teacher_type": "highway.tertiary",
            "candidate_type": "highway.service",
            "teacher_lane_count": 1,
            "candidate_lane_count": 2,
            "teacher_lane_signature": ["index=0|allow=pedestrian|disallow=truck"],
            "candidate_lane_signature": ["index=0|allow=|disallow=", "index=1|allow=bicycle|disallow="],
        }
    ]
    assert report["summary"]["common_edge_geometry_mismatch_count"] == 1


def test_summarize_road_lane_model_templates_groups_type_and_lane_signature(tmp_path: Path) -> None:
    net_file = tmp_path / "teacher.net.xml"
    net_file.write_text(
        """<net>
  <edge id="a" from="n1" to="n2" type="cycleway.track|highway.tertiary">
    <lane id="a_0" index="0" allow="pedestrian" shape="0,0 10,0"/>
    <lane id="a_1" index="1" allow="bicycle" shape="0,1 10,1"/>
  </edge>
  <edge id="b" from="n2" to="n3" type="cycleway.track|highway.tertiary">
    <lane id="b_0" index="0" allow="pedestrian" shape="10,0 20,0"/>
    <lane id="b_1" index="1" allow="bicycle" shape="10,1 20,1"/>
  </edge>
  <edge id="c" from="n3" to="n4" type="highway.service">
    <lane id="c_0" index="0" disallow="tram" shape="20,0 30,0"/>
  </edge>
  <junction id="n1" type="dead_end" x="0" y="0" incLanes="" intLanes=""/>
  <junction id="n2" type="priority" x="10" y="0" incLanes="a_0 a_1" intLanes=""/>
  <junction id="n3" type="priority" x="20" y="0" incLanes="b_0 b_1" intLanes=""/>
  <junction id="n4" type="dead_end" x="30" y="0" incLanes="c_0" intLanes=""/>
</net>""",
        encoding="utf-8",
    )
    bundle = canonical_road_connectivity_bundle(net_file, seed_edge_ids=["b"], hop_radius=1)

    templates = summarize_road_lane_model_templates(bundle)

    assert templates == [
        {
            "type": "cycleway.track|highway.tertiary",
            "lane_signature": [
                "index=0|allow=pedestrian|disallow=",
                "index=1|allow=bicycle|disallow=",
            ],
            "count": 2,
            "example_edge_ids": ["a", "b"],
        },
        {
            "type": "highway.service",
            "lane_signature": ["index=0|allow=|disallow=tram"],
            "count": 1,
            "example_edge_ids": ["c"],
        },
    ]


def test_summarize_net_road_lane_model_templates_excludes_internal_edges(tmp_path: Path) -> None:
    net_file = tmp_path / "teacher.net.xml"
    net_file.write_text(
        """<net>
  <edge id="a" from="n1" to="n2" type="cycleway.track|highway.tertiary">
    <lane id="a_0" index="0" allow="pedestrian" shape="0,0 10,0"/>
    <lane id="a_1" index="1" allow="bicycle" shape="0,1 10,1"/>
  </edge>
  <edge id="b" from="n2" to="n3" type="cycleway.track|highway.tertiary">
    <lane id="b_0" index="0" allow="pedestrian" shape="10,0 20,0"/>
    <lane id="b_1" index="1" allow="bicycle" shape="10,1 20,1"/>
  </edge>
  <edge id=":n2_0" function="internal">
    <lane id=":n2_0_0" index="0" allow="passenger" shape="10,0 11,0"/>
  </edge>
  <edge id="c" from="n3" to="n4" type="highway.residential">
    <lane id="c_0" index="0" allow="passenger" shape="20,0 30,0"/>
  </edge>
</net>""",
        encoding="utf-8",
    )

    templates = summarize_net_road_lane_model_templates(net_file)

    assert templates == [
        {
            "type": "cycleway.track|highway.tertiary",
            "lane_signature": [
                "index=0|allow=pedestrian|disallow=",
                "index=1|allow=bicycle|disallow=",
            ],
            "count": 2,
            "example_edge_ids": ["a", "b"],
        },
        {
            "type": "highway.residential",
            "lane_signature": ["index=0|allow=passenger|disallow="],
            "count": 1,
            "example_edge_ids": ["c"],
        },
    ]


def test_summarize_net_road_connection_templates_groups_road_level_movements(tmp_path: Path) -> None:
    net_file = tmp_path / "teacher.net.xml"
    net_file.write_text(
        """<net>
  <edge id="a" from="n1" to="n2" type="highway.tertiary">
    <lane id="a_0" index="0" allow="passenger" shape="0,0 10,0"/>
  </edge>
  <edge id="b" from="n2" to="n3" type="highway.residential">
    <lane id="b_0" index="0" allow="passenger" shape="10,0 20,0"/>
  </edge>
  <edge id="c" from="n4" to="n2" type="highway.tertiary">
    <lane id="c_0" index="0" allow="passenger" shape="0,5 10,0"/>
  </edge>
  <edge id=":n2_0" function="internal">
    <lane id=":n2_0_0" index="0" allow="passenger" shape="10,0 11,0"/>
  </edge>
  <connection from="a" to="b" fromLane="0" toLane="0" dir="s" via=":n2_0_0" tl="n2" linkIndex="0"/>
  <connection from="c" to="b" fromLane="0" toLane="0" dir="s"/>
  <connection from="b" to="a" fromLane="0" toLane="0" dir="t"/>
  <connection from="a" to=":n2_0" fromLane="0" toLane="0" dir="s"/>
</net>""",
        encoding="utf-8",
    )

    templates = summarize_net_road_connection_templates(net_file)

    assert templates == [
        {
            "dir": "s",
            "from_type": "highway.tertiary",
            "from_lane": "0",
            "from_lane_signature": ["index=0|allow=passenger|disallow="],
            "to_type": "highway.residential",
            "to_lane": "0",
            "to_lane_signature": ["index=0|allow=passenger|disallow="],
            "count": 2,
            "example_connections": ["a[0]->b[0]", "c[0]->b[0]"],
        },
        {
            "dir": "t",
            "from_type": "highway.residential",
            "from_lane": "0",
            "from_lane_signature": ["index=0|allow=passenger|disallow="],
            "to_type": "highway.tertiary",
            "to_lane": "0",
            "to_lane_signature": ["index=0|allow=passenger|disallow="],
            "count": 1,
            "example_connections": ["b[0]->a[0]"],
        },
    ]


def test_compare_road_template_summaries_reports_key_level_parity() -> None:
    teacher_templates = [
        {"type": "highway.service", "lane_signature": ["vehicle"], "count": 10},
        {"type": "highway.footway", "lane_signature": ["pedestrian"], "count": 4},
    ]
    candidate_templates = [
        {"type": "highway.service", "lane_signature": ["vehicle"], "count": 7},
        {"type": "highway.path", "lane_signature": ["pedestrian bicycle"], "count": 3},
    ]

    report = compare_road_template_summaries(
        teacher_templates,
        candidate_templates,
        key_fields=["type", "lane_signature"],
    )

    assert report == {
        "status": "fail",
        "missing_template_count": 1,
        "extra_template_count": 1,
        "common_template_count": 1,
        "common_count_delta_sum": 3,
        "missing_templates": [
            {"type": "highway.footway", "lane_signature": ["pedestrian"], "count": 4}
        ],
        "extra_templates": [
            {"type": "highway.path", "lane_signature": ["pedestrian bicycle"], "count": 3}
        ],
    }


def test_compare_net_road_template_parity_reports_lane_and_connection_delta(tmp_path: Path) -> None:
    teacher_net = tmp_path / "teacher.net.xml"
    candidate_net = tmp_path / "candidate.net.xml"
    teacher_net.write_text(
        """<net>
  <edge id="a" from="n1" to="n2" type="highway.service">
    <lane id="a_0" index="0" allow="passenger" shape="0,0 10,0"/>
  </edge>
  <edge id="b" from="n2" to="n3" type="highway.service">
    <lane id="b_0" index="0" allow="passenger" shape="10,0 20,0"/>
  </edge>
  <edge id="foot" from="n2" to="n4" type="highway.footway">
    <lane id="foot_0" index="0" allow="pedestrian" shape="10,0 10,5"/>
  </edge>
  <connection from="a" to="b" fromLane="0" toLane="0" dir="s"/>
</net>""",
        encoding="utf-8",
    )
    candidate_net.write_text(
        """<net>
  <edge id="a" from="n1" to="n2" type="highway.service">
    <lane id="a_0" index="0" allow="passenger" shape="0,0 10,0"/>
  </edge>
  <edge id="b" from="n2" to="n3" type="highway.residential">
    <lane id="b_0" index="0" allow="passenger" shape="10,0 20,0"/>
  </edge>
  <connection from="a" to="b" fromLane="0" toLane="0" dir="s"/>
</net>""",
        encoding="utf-8",
    )

    report = compare_net_road_template_parity(teacher_net, candidate_net)

    assert report["status"] == "fail"
    assert report["gate"] == {
        "road_layer_status": "fail",
        "can_enter_junction_replay": False,
        "blocking_reason": "road_template_parity_failed",
        "lane_missing_template_count": 1,
        "connection_missing_template_count": 1,
        "lane_extra_template_count": 1,
        "connection_extra_template_count": 1,
        "lane_common_count_delta_sum": 1,
        "connection_common_count_delta_sum": 0,
    }
    assert report["lane_template_summary"]["teacher_edge_count"] == 3
    assert report["lane_template_summary"]["candidate_edge_count"] == 2
    assert report["lane_template_summary"]["parity"]["missing_template_count"] == 1
    assert report["lane_template_summary"]["parity"]["extra_template_count"] == 1
    assert report["lane_template_summary"]["parity"]["common_count_delta_sum"] == 1
    assert report["connection_template_summary"]["teacher_connection_count"] == 1
    assert report["connection_template_summary"]["candidate_connection_count"] == 1
    assert report["connection_template_summary"]["parity"]["missing_template_count"] == 1
    assert report["connection_template_summary"]["parity"]["extra_template_count"] == 1
    assert report["connection_template_summary"]["parity"]["common_template_count"] == 0
    assert {item["layer"] for item in report["repair_queue"]} == {"lane", "connection"}
    assert {item["difference"] for item in report["repair_queue"]} == {
        "missing_teacher_template",
        "extra_candidate_template",
    }


def test_compare_net_road_template_parity_separates_connection_topology_from_lane_signature(
    tmp_path: Path,
) -> None:
    teacher_net = tmp_path / "teacher.net.xml"
    candidate_net = tmp_path / "candidate.net.xml"
    teacher_net.write_text(
        """<net>
  <edge id="a" from="n1" to="n2" type="highway.service">
    <lane id="a_0" index="0" allow="passenger" shape="0,0 10,0"/>
  </edge>
  <edge id="b" from="n2" to="n3" type="highway.service">
    <lane id="b_0" index="0" allow="passenger" shape="10,0 20,0"/>
  </edge>
  <connection from="a" to="b" fromLane="0" toLane="0" dir="s"/>
</net>""",
        encoding="utf-8",
    )
    candidate_net.write_text(
        """<net>
  <edge id="a" from="n1" to="n2" type="highway.service">
    <lane id="a_0" index="0" allow="pedestrian passenger" shape="0,0 10,0"/>
  </edge>
  <edge id="b" from="n2" to="n3" type="highway.service">
    <lane id="b_0" index="0" allow="pedestrian passenger" shape="10,0 20,0"/>
  </edge>
  <connection from="a" to="b" fromLane="0" toLane="0" dir="s"/>
</net>""",
        encoding="utf-8",
    )

    report = compare_net_road_template_parity(teacher_net, candidate_net)

    assert report["connection_template_summary"]["parity"]["status"] == "fail"
    assert report["connection_topology_summary"]["parity"]["status"] == "pass"
    assert report["connection_topology_summary"]["parity"]["common_template_count"] == 1


def test_compare_net_road_template_parity_can_use_effective_permission_roles(
    tmp_path: Path,
) -> None:
    teacher_net = tmp_path / "teacher.net.xml"
    candidate_net = tmp_path / "candidate.net.xml"
    teacher_net.write_text(
        """<net>
  <edge id="a" from="n1" to="n2" type="highway.service">
    <lane id="a_0" index="0" allow="passenger" shape="0,0 10,0"/>
  </edge>
</net>""",
        encoding="utf-8",
    )
    candidate_net.write_text(
        """<net>
  <edge id="a" from="n1" to="n2" type="highway.service">
    <lane id="a_0" index="0"
      disallow="private taxi delivery truck trailer bus coach bicycle pedestrian wheelchair motorcycle moped scooter tram rail_urban rail rail_electric rail_fast"
      shape="0,0 10,0"/>
  </edge>
</net>""",
        encoding="utf-8",
    )

    raw_report = compare_net_road_template_parity(teacher_net, candidate_net)
    semantic_report = compare_net_road_template_parity(
        teacher_net,
        candidate_net,
        semantic_gate=True,
    )

    assert raw_report["status"] == "fail"
    assert semantic_report["status"] == "pass"
    assert semantic_report["gate"]["comparison_basis"] == "semantic"
    assert semantic_report["gate"]["raw_road_layer_status"] == "fail"
    assert semantic_report["semantic_lane_template_summary"]["parity"]["status"] == "pass"


def test_build_road_template_repair_queue_prioritizes_road_layer_deltas() -> None:
    parity_report = {
        "lane_template_summary": {
            "parity": {
                "missing_templates": [
                    {"type": "highway.footway", "lane_signature": ["pedestrian"], "count": 4}
                ],
                "extra_templates": [
                    {"type": "highway.path", "lane_signature": ["bicycle"], "count": 2}
                ],
            }
        },
        "connection_template_summary": {
            "parity": {
                "missing_templates": [
                    {"dir": "s", "from_type": "highway.service", "to_type": "highway.service", "count": 6}
                ],
                "extra_templates": [
                    {"dir": "t", "from_type": "highway.service", "to_type": "highway.service", "count": 1}
                ],
            }
        },
    }

    queue = build_road_template_repair_queue(parity_report, max_items=3)

    assert queue == [
        {
            "layer": "connection",
            "difference": "missing_teacher_template",
            "priority": 6,
            "template": {
                "dir": "s",
                "from_type": "highway.service",
                "to_type": "highway.service",
                "count": 6,
            },
        },
        {
            "layer": "lane",
            "difference": "missing_teacher_template",
            "priority": 4,
            "template": {
                "type": "highway.footway",
                "lane_signature": ["pedestrian"],
                "count": 4,
            },
        },
        {
            "layer": "lane",
            "difference": "extra_candidate_template",
            "priority": 2,
            "template": {
                "type": "highway.path",
                "lane_signature": ["bicycle"],
                "count": 2,
            },
        },
    ]


def test_build_road_lane_template_repair_candidates_pairs_same_type_lane_signatures() -> None:
    parity_report = {
        "lane_template_summary": {
            "parity": {
                "missing_templates": [
                    {
                        "type": "highway.service",
                        "lane_signature": [
                            "index=0|allow=|disallow=pedestrian tram rail_urban rail rail_electric rail_fast ship cable_car subway"
                        ],
                        "count": 944,
                    }
                ],
                "extra_templates": [
                    {
                        "type": "highway.service",
                        "lane_signature": [
                            "index=0|allow=pedestrian passenger delivery bicycle|disallow="
                        ],
                        "count": 1288,
                    }
                ],
            }
        }
    }

    candidates = build_road_lane_template_repair_candidates(parity_report)

    assert candidates == [
        {
            "action": "replace_lane_signature",
            "type": "highway.service",
            "priority": 944,
            "candidate_count": 1288,
            "teacher_count": 944,
            "from_lane_signature": [
                "index=0|allow=pedestrian passenger delivery bicycle|disallow="
            ],
            "to_lane_signature": [
                "index=0|allow=|disallow=pedestrian tram rail_urban rail rail_electric rail_fast ship cable_car subway"
            ],
        }
    ]


def test_write_road_lane_template_repair_candidate_applies_teacher_lane_signature(
    tmp_path: Path,
) -> None:
    candidate_net = tmp_path / "candidate.net.xml"
    output_net = tmp_path / "candidate.repaired.net.xml"
    candidate_net.write_text(
        """<net>
  <edge id="service_a" type="highway.service">
    <lane id="service_a_0" index="0" allow="pedestrian passenger delivery bicycle" speed="5.0" length="25.0"/>
  </edge>
  <edge id="service_b" type="highway.service">
    <lane id="service_b_0" index="0" allow="pedestrian passenger delivery bicycle" speed="5.0" length="25.0"/>
  </edge>
  <edge id="residential" type="highway.residential">
    <lane id="residential_0" index="0" allow="pedestrian passenger delivery bicycle" speed="5.0" length="25.0"/>
  </edge>
</net>""",
        encoding="utf-8",
    )
    repair_candidates = [
        {
            "action": "replace_lane_signature",
            "type": "highway.service",
            "from_lane_signature": [
                "index=0|allow=pedestrian passenger delivery bicycle|disallow="
            ],
            "to_lane_signature": [
                "index=0|allow=|disallow=pedestrian tram rail_urban rail rail_electric rail_fast ship cable_car subway"
            ],
        }
    ]

    report = write_road_lane_template_repair_candidate(
        candidate_net,
        output_net,
        repair_candidates,
    )

    root = ET.parse(output_net).getroot()
    service_lane = root.find("./edge[@id='service_a']/lane")
    residential_lane = root.find("./edge[@id='residential']/lane")
    assert report["status"] == "pass"
    assert report["changed_edge_count"] == 2
    assert report["changed_lane_count"] == 2
    assert service_lane.attrib.get("allow") is None
    assert service_lane.attrib["disallow"].startswith("pedestrian tram")
    assert residential_lane.attrib["allow"] == "pedestrian passenger delivery bicycle"


def test_edge_subset_lane_template_repair_only_changes_teacher_confirmed_edges(
    tmp_path: Path,
) -> None:
    teacher_net = tmp_path / "teacher.net.xml"
    candidate_net = tmp_path / "candidate.net.xml"
    output_net = tmp_path / "candidate.edge_subset.net.xml"
    teacher_net.write_text(
        """<net>
  <edge id="fix_me" type="highway.service">
    <lane id="fix_me_0" index="0" allow="passenger" speed="5.0" length="25.0"/>
  </edge>
</net>""",
        encoding="utf-8",
    )
    candidate_net.write_text(
        """<net>
  <edge id="fix_me" type="highway.service">
    <lane id="fix_me_0" index="0" allow="pedestrian passenger" speed="5.0" length="25.0"/>
  </edge>
  <edge id="leave_me" type="highway.service">
    <lane id="leave_me_0" index="0" allow="pedestrian passenger" speed="5.0" length="25.0"/>
  </edge>
</net>""",
        encoding="utf-8",
    )
    parity_report = compare_net_road_template_parity(teacher_net, candidate_net)

    repair_candidates = build_road_lane_template_edge_subset_repair_candidates(
        teacher_net,
        candidate_net,
        parity_report,
    )
    report = write_road_lane_template_repair_candidate(
        candidate_net,
        output_net,
        [repair_candidates[0]],
    )

    root = ET.parse(output_net).getroot()
    fixed_lane = root.find("./edge[@id='fix_me']/lane")
    untouched_lane = root.find("./edge[@id='leave_me']/lane")
    assert repair_candidates[0]["edge_ids"] == ["fix_me"]
    assert repair_candidates[0]["edge_count"] == 1
    assert report["changed_edge_count"] == 1
    assert report["changed_lane_count"] == 1
    assert fixed_lane.attrib["allow"] == "passenger"
    assert untouched_lane.attrib["allow"] == "pedestrian passenger"


def test_single_edge_lane_template_repair_splits_teacher_confirmed_edges(tmp_path: Path) -> None:
    teacher_net = tmp_path / "teacher.net.xml"
    candidate_net = tmp_path / "candidate.net.xml"
    teacher_net.write_text(
        """<net>
  <edge id="fix_a" type="highway.service">
    <lane id="fix_a_0" index="0" allow="passenger" speed="5.0" length="25.0"/>
  </edge>
  <edge id="fix_b" type="highway.service">
    <lane id="fix_b_0" index="0" allow="passenger" speed="5.0" length="25.0"/>
  </edge>
</net>""",
        encoding="utf-8",
    )
    candidate_net.write_text(
        """<net>
  <edge id="fix_a" type="highway.service">
    <lane id="fix_a_0" index="0" allow="pedestrian passenger" speed="5.0" length="25.0"/>
  </edge>
  <edge id="fix_b" type="highway.service">
    <lane id="fix_b_0" index="0" allow="pedestrian passenger" speed="5.0" length="25.0"/>
  </edge>
</net>""",
        encoding="utf-8",
    )
    parity_report = compare_net_road_template_parity(teacher_net, candidate_net)

    repair_candidates = build_road_lane_template_single_edge_repair_candidates(
        teacher_net,
        candidate_net,
        parity_report,
    )

    assert [candidate["edge_ids"] for candidate in repair_candidates] == [["fix_a"], ["fix_b"]]
    assert [candidate["edge_count"] for candidate in repair_candidates] == [1, 1]
    assert [candidate["priority"] for candidate in repair_candidates] == [1, 1]


def test_owner_road_lane_repair_candidate_replays_mapped_approach_lanes(tmp_path: Path) -> None:
    teacher_net = tmp_path / "teacher.net.xml"
    candidate_net = tmp_path / "candidate.net.xml"
    output_net = tmp_path / "candidate.owner_lanes.net.xml"
    teacher_net.write_text(
        """<net>
  <edge id="road#3" type="highway.primary" from="a" to="j">
    <lane id="road#3_0" index="0" allow="pedestrian" speed="5.0" length="10.0"/>
    <lane id="road#3_1" index="1" disallow="pedestrian bicycle" speed="13.9" length="10.0"/>
  </edge>
  <junction id="j" type="priority"/>
</net>""",
        encoding="utf-8",
    )
    candidate_net.write_text(
        """<net>
  <edge id="road#5" type="highway.primary" from="a2" to="j">
    <lane id="road#5_0" index="0" disallow="tram" speed="13.9" length="10.0"/>
  </edge>
  <junction id="j" type="priority"/>
</net>""",
        encoding="utf-8",
    )

    repair_candidates = build_internal_movement_owner_road_lane_repair_candidates(
        teacher_net,
        candidate_net,
        owner_id="j",
        teacher_edge_map={"road#3": "road#5"},
    )
    report = write_road_lane_template_repair_candidate(
        candidate_net,
        output_net,
        repair_candidates,
    )

    lanes = ET.parse(output_net).getroot().findall("./edge[@id='road#5']/lane")
    assert repair_candidates[0]["action"] == "replace_edge_lanes"
    assert repair_candidates[0]["teacher_edge_id"] == "road#3"
    assert repair_candidates[0]["edge_ids"] == ["road#5"]
    assert report["changed_edge_count"] == 1
    assert report["changed_lane_count"] == 2
    assert [lane.attrib["id"] for lane in lanes] == ["road#5_0", "road#5_1"]
    assert lanes[0].attrib["allow"] == "pedestrian"
    assert lanes[1].attrib["disallow"] == "pedestrian bicycle"


def test_owner_road_lane_repair_candidate_reprojects_teacher_lane_shape_to_candidate_offset(
    tmp_path: Path,
) -> None:
    teacher_net = tmp_path / "teacher.net.xml"
    candidate_net = tmp_path / "candidate.net.xml"
    output_net = tmp_path / "candidate.owner_lanes.net.xml"
    teacher_net.write_text(
        """<net>
  <location netOffset="-100,-100"/>
  <edge id="road#3" type="highway.primary" from="a" to="j">
    <lane id="road#3_0" index="0" allow="passenger" speed="13.9" length="10.0" shape="100,100 110,100"/>
    <lane id="road#3_1" index="1" allow="passenger" speed="13.9" length="10.0" shape="100,101 110,101"/>
  </edge>
  <junction id="j" type="priority"/>
</net>""",
        encoding="utf-8",
    )
    candidate_net.write_text(
        """<net>
  <location netOffset="-200,-200"/>
  <edge id="road#5" type="highway.primary" from="a2" to="j">
    <lane id="road#5_0" index="0" disallow="tram" speed="13.9" length="10.0" shape="0,0 10,0"/>
  </edge>
  <junction id="j" type="priority"/>
</net>""",
        encoding="utf-8",
    )

    repair_candidates = build_internal_movement_owner_road_lane_repair_candidates(
        teacher_net,
        candidate_net,
        owner_id="j",
        teacher_edge_map={"road#3": "road#5"},
    )
    write_road_lane_template_repair_candidate(
        candidate_net,
        output_net,
        repair_candidates,
    )

    lanes = ET.parse(output_net).getroot().findall("./edge[@id='road#5']/lane")
    assert [lane.attrib["shape"] for lane in lanes] == ["0.00,0.00 10.00,0.00", "0.00,1.00 10.00,1.00"]


def test_owner_missing_approach_edge_repair_replays_edge_and_minimal_endpoint(
    tmp_path: Path,
) -> None:
    teacher_net = tmp_path / "teacher.net.xml"
    candidate_net = tmp_path / "candidate.net.xml"
    output_net = tmp_path / "candidate.missing_edge.net.xml"
    teacher_net.write_text(
        """<net>
  <edge id="path#1" type="highway.path" from="stub" to="j">
    <lane id="path#1_0" index="0" allow="pedestrian bicycle" speed="2.78" length="4.0"/>
  </edge>
  <junction id="stub" type="right_before_left" x="1" y="2" incLanes="other_0" intLanes=":stub_0_0" shape="0,0 1,1">
    <request index="0" response="0" foes="0" cont="0"/>
  </junction>
  <junction id="j" type="priority" x="3" y="4"/>
</net>""",
        encoding="utf-8",
    )
    candidate_net.write_text(
        """<net>
  <junction id="j" type="priority" x="3" y="4"/>
</net>""",
        encoding="utf-8",
    )

    repair_candidates = build_internal_movement_owner_missing_approach_edge_repair_candidates(
        teacher_net,
        candidate_net,
        owner_id="j",
    )
    report = write_internal_movement_owner_missing_approach_edge_repair_candidate(
        candidate_net,
        output_net,
        repair_candidates,
    )

    root = ET.parse(output_net).getroot()
    edge = root.find("edge[@id='path#1']")
    endpoint = root.find("junction[@id='stub']")
    assert repair_candidates[0]["action"] == "add_missing_approach_edge"
    assert repair_candidates[0]["edge_id"] == "path#1"
    assert repair_candidates[0]["edge_map_addition"] == {"path#1": "path#1"}
    assert report["added_edge_count"] == 1
    assert report["added_junction_count"] == 1
    assert edge is not None
    assert edge.find("lane").attrib["allow"] == "pedestrian bicycle"
    assert endpoint.attrib == {
        "id": "stub",
        "type": "priority",
        "x": "1",
        "y": "2",
        "shape": "0,0 1,1",
    }
    assert endpoint.find("request") is None


def test_owner_missing_approach_edge_repair_reprojects_teacher_edge_to_candidate_offset(
    tmp_path: Path,
) -> None:
    teacher_net = tmp_path / "teacher.net.xml"
    candidate_net = tmp_path / "candidate.net.xml"
    output_net = tmp_path / "candidate.missing_edge.net.xml"
    teacher_net.write_text(
        """<net>
  <location netOffset="-100,-100"/>
  <edge id="path#1" type="highway.path" from="stub" to="j" shape="100,100 110,100">
    <lane id="path#1_0" index="0" allow="passenger" speed="13.9" length="10.0" shape="100,100 110,100"/>
  </edge>
  <junction id="stub" type="priority" x="100" y="100" shape="99,99 101,101"/>
  <junction id="j" type="priority"/>
</net>""",
        encoding="utf-8",
    )
    candidate_net.write_text(
        """<net>
  <location netOffset="-200,-200"/>
  <junction id="j" type="priority"/>
</net>""",
        encoding="utf-8",
    )

    repair_candidates = build_internal_movement_owner_missing_approach_edge_repair_candidates(
        teacher_net,
        candidate_net,
        owner_id="j",
    )
    write_internal_movement_owner_missing_approach_edge_repair_candidate(
        candidate_net,
        output_net,
        repair_candidates,
    )

    root = ET.parse(output_net).getroot()
    edge = root.find("./edge[@id='path#1']")
    lane = root.find("./edge[@id='path#1']/lane")
    stub = root.find("./junction[@id='stub']")
    assert edge is not None
    assert lane is not None
    assert stub is not None
    assert edge.attrib["shape"] == "0.00,0.00 10.00,0.00"
    assert lane.attrib["shape"] == "0.00,0.00 10.00,0.00"
    assert stub.attrib["x"] == "0.00"
    assert stub.attrib["y"] == "0.00"
    assert stub.attrib["shape"] == "-1.00,-1.00 1.00,1.00"


def test_owner_teacher_replay_runs_road_lane_missing_edge_then_bundle(
    tmp_path: Path,
) -> None:
    teacher_net = tmp_path / "teacher.net.xml"
    candidate_net = tmp_path / "candidate.net.xml"
    output_net = tmp_path / "candidate.teacher_replayed.net.xml"
    teacher_net.write_text(
        """<net>
  <edge id="road#3" from="a" to="j"><lane id="road#3_0" index="0"/><lane id="road#3_1" index="1"/></edge>
  <edge id="path#1" from="stub" to="j"><lane id="path#1_0" index="0" allow="pedestrian bicycle"/></edge>
  <edge id="out#2" from="j" to="b"><lane id="out#2_0" index="0"/></edge>
  <edge id=":j_0" function="internal"><lane id=":j_0_0" index="0"/></edge>
  <junction id="stub" type="priority" x="1" y="2" shape="0,0 1,1"/>
  <junction id="j" type="priority" x="0" y="0" incLanes="road#3_0 road#3_1 path#1_0" intLanes=":j_0_0"/>
  <connection from="path#1" to="out#2" fromLane="0" toLane="0" via=":j_0_0" dir="s"/>
  <connection from=":j_0" to="out#2" fromLane="0" toLane="0" dir="s"/>
</net>""",
        encoding="utf-8",
    )
    candidate_net.write_text(
        """<net>
  <edge id="road#5" from="a2" to="j"><lane id="road#5_0" index="0"/></edge>
  <edge id="out#4" from="j" to="b2"><lane id="out#4_0" index="0"/></edge>
  <junction id="j" type="priority" x="0" y="0"/>
</net>""",
        encoding="utf-8",
    )

    report = write_internal_movement_owner_teacher_replay_candidate(
        teacher_net,
        candidate_net,
        output_net,
        owner_id="j",
        copy_tls=False,
    )

    root = ET.parse(output_net).getroot()
    road_lanes = root.findall("./edge[@id='road#5']/lane")
    assert report["status"] == "pass"
    assert report["edge_map"] == {"out#2": "out#4", "path#1": "path#1", "road#3": "road#5"}
    assert report["road_lane_repair"]["changed_edge_count"] == 1
    assert report["missing_approach_edge_repair"]["added_edge_count"] == 1
    assert report["bundle_replay"]["status"] == "pass"
    assert report["road_connectivity_audit"]["status"] == "pass"
    assert report["approach_edge_chain_map"]["fragmented_teacher_edge_count"] == 0
    assert root.find("edge[@id='road#3']") is None
    assert len(road_lanes) == 2
    assert root.find("edge[@id='path#1']") is not None
    assert root.find("edge[@id=':j_0']") is not None
    assert root.find("connection[@from='path#1'][@to='out#4']") is not None


def test_owner_teacher_replay_replaces_stale_same_id_approach_edge(
    tmp_path: Path,
) -> None:
    teacher_net = tmp_path / "teacher.net.xml"
    candidate_net = tmp_path / "candidate.net.xml"
    output_net = tmp_path / "candidate.teacher_replayed.net.xml"
    teacher_net.write_text(
        """<net>
  <edge id="in" from="a" to="j"><lane id="in_0" index="0"/></edge>
  <edge id="road#1" from="j" to="b"><lane id="road#1_0" index="0"/><lane id="road#1_1" index="1"/></edge>
  <edge id=":j_0" function="internal"><lane id=":j_0_0" index="0"/></edge>
  <junction id="j" type="priority" x="0" y="0" incLanes="in_0" intLanes=":j_0_0"/>
  <junction id="b" type="priority" x="10" y="0"/>
  <connection from="in" to="road#1" fromLane="0" toLane="1" via=":j_0_0" dir="s"/>
  <connection from=":j_0" to="road#1" fromLane="0" toLane="1" dir="s"/>
</net>""",
        encoding="utf-8",
    )
    candidate_net.write_text(
        """<net>
  <edge id="in" from="a" to="j"><lane id="in_0" index="0"/></edge>
  <edge id="road#0" from="j" to="wrong"><lane id="road#0_0" index="0"/></edge>
  <edge id="road#1" from="x" to="y"><lane id="road#1_0" index="0"/></edge>
  <edge id=":j_old" function="internal"><lane id=":j_old_0" index="0"/></edge>
  <junction id="j" type="priority" x="0" y="0" incLanes="in_0" intLanes=":j_old_0"/>
  <junction id="wrong" type="priority" x="5" y="0"/>
  <junction id="x" type="priority" x="20" y="0"/>
  <junction id="y" type="priority" x="30" y="0"/>
  <connection from="in" to="road#0" fromLane="0" toLane="0" via=":j_old_0" dir="s"/>
</net>""",
        encoding="utf-8",
    )

    report = write_internal_movement_owner_teacher_replay_candidate(
        teacher_net,
        candidate_net,
        output_net,
        owner_id="j",
    )

    root = ET.parse(output_net).getroot()
    assert report["status"] == "pass"
    assert report["edge_map"]["road#1"] == "road#1"
    assert root.find("edge[@id='road#1']").attrib["from"] == "j"
    assert len(root.findall("./edge[@id='road#1']/lane")) == 2
    assert root.find("connection[@from='in'][@to='road#1']") is not None
    assert root.find("connection[@from='in'][@to='road#0']") is None


def test_owner_road_connectivity_audit_does_not_treat_turnaround_as_route_complete(
    tmp_path: Path,
) -> None:
    teacher_net = tmp_path / "teacher.net.xml"
    candidate_net = tmp_path / "candidate.net.xml"
    teacher_net.write_text(
        """<net>
  <edge id="out#1" from="j" to="b"><lane id="out#1_0" index="0" allow="passenger"/></edge>
  <edge id="-out#1" from="b" to="j"><lane id="-out#1_0" index="0" allow="passenger"/></edge>
  <edge id="next" from="b" to="c"><lane id="next_0" index="0" allow="passenger"/></edge>
  <junction id="j" type="priority" x="0" y="0"/>
  <junction id="b" type="priority" x="10" y="0"/>
  <junction id="c" type="dead_end" x="20" y="0"/>
  <connection from="out#1" to="next" fromLane="0" toLane="0" dir="s"/>
  <connection from="out#1" to="-out#1" fromLane="0" toLane="0" dir="t"/>
</net>""",
        encoding="utf-8",
    )
    candidate_net.write_text(
        """<net>
  <edge id="out#3" from="j" to="b"><lane id="out#3_0" index="0" allow="passenger"/></edge>
  <edge id="-out#3" from="b" to="j"><lane id="-out#3_0" index="0" allow="passenger"/></edge>
  <edge id="next" from="b" to="c"><lane id="next_0" index="0" allow="passenger"/></edge>
  <junction id="j" type="priority" x="0" y="0"/>
  <junction id="b" type="priority" x="10" y="0"/>
  <junction id="c" type="dead_end" x="20" y="0"/>
  <connection from="out#3" to="-out#3" fromLane="0" toLane="0" dir="t"/>
</net>""",
        encoding="utf-8",
    )

    report = build_internal_movement_owner_road_connectivity_parity_audit(
        teacher_net,
        candidate_net,
        owner_id="j",
        teacher_edge_map={"out#1": "out#3"},
    )

    assert report["status"] == "fail"
    assert report["gate"] == {
        "lane_delta_count": 1,
        "missing_non_turnaround_outgoing_count": 1,
        "turnaround_only_outgoing_count": 1,
        "missing_turnaround_outgoing_count": 0,
        "missing_non_turnaround_incoming_count": 0,
        "turnaround_only_incoming_count": 0,
        "missing_turnaround_incoming_count": 0,
    }
    assert report["lane_deltas"][0]["outgoing"]["flags"] == [
        "missing_non_turnaround_vehicle_connection",
        "turnaround_only_candidate",
    ]


def test_owner_approach_edge_chain_map_detects_split_candidate_road_span(
    tmp_path: Path,
) -> None:
    teacher_net = tmp_path / "teacher.net.xml"
    candidate_net = tmp_path / "candidate.net.xml"
    teacher_net.write_text(
        """<net>
  <edge id="road#0" from="a" to="j"><lane id="road#0_0" index="0"/></edge>
  <junction id="a" type="priority" x="0" y="0"/>
  <junction id="j" type="priority" x="20" y="0"/>
</net>""",
        encoding="utf-8",
    )
    candidate_net.write_text(
        """<net>
  <edge id="road#2" from="a" to="mid"><lane id="road#2_0" index="0"/></edge>
  <edge id="road#1" from="mid" to="j"><lane id="road#1_0" index="0"/></edge>
  <junction id="a" type="priority" x="0" y="0"/>
  <junction id="mid" type="priority" x="10" y="0"/>
  <junction id="j" type="priority" x="20" y="0"/>
</net>""",
        encoding="utf-8",
    )

    report = build_internal_movement_owner_approach_edge_chain_map(
        teacher_net,
        candidate_net,
        owner_id="j",
    )

    assert report["edge_chain_map"] == {"road#0": ["road#2", "road#1"]}
    assert report["fragmented_teacher_edge_count"] == 1
    assert report["fragmented_teacher_edges"] == [
        {
            "teacher_edge_id": "road#0",
            "candidate_edge_ids": ["road#2", "road#1"],
            "direction": "incoming",
        }
    ]


def test_owner_road_span_repair_candidates_group_bidirectional_split_chain(
    tmp_path: Path,
) -> None:
    teacher_net = tmp_path / "teacher.net.xml"
    candidate_net = tmp_path / "candidate.net.xml"
    teacher_net.write_text(
        """<net>
  <edge id="road#0" from="j" to="a"><lane id="road#0_0" index="0"/></edge>
  <edge id="-road#0" from="a" to="j"><lane id="-road#0_0" index="0"/></edge>
  <junction id="a" type="priority" x="0" y="0"/>
  <junction id="j" type="priority" x="20" y="0"/>
</net>""",
        encoding="utf-8",
    )
    candidate_net.write_text(
        """<net>
  <edge id="road#0" from="j" to="mid"><lane id="road#0_0" index="0"/></edge>
  <edge id="road#1" from="mid" to="a"><lane id="road#1_0" index="0"/></edge>
  <edge id="-road#1" from="a" to="mid"><lane id="-road#1_0" index="0"/></edge>
  <edge id="-road#0" from="mid" to="j"><lane id="-road#0_0" index="0"/></edge>
  <junction id="a" type="priority" x="0" y="0"/>
  <junction id="mid" type="priority" x="10" y="0"/>
  <junction id="j" type="priority" x="20" y="0"/>
</net>""",
        encoding="utf-8",
    )

    candidates = build_internal_movement_owner_road_span_repair_candidates(
        teacher_net,
        candidate_net,
        owner_id="j",
    )

    assert candidates == [
        {
            "action": "replace_split_approach_road_span",
            "status": "ready",
            "span_key": "road#0",
            "teacher_edge_ids": ["-road#0", "road#0"],
            "keep_edge_ids": ["-road#0", "road#0"],
            "remove_edge_ids": ["-road#1", "road#1"],
            "intermediate_junction_ids": ["mid"],
            "blocked_incident_edge_ids": [],
            "fragmented_teacher_edges": [
                {
                    "teacher_edge_id": "-road#0",
                    "candidate_edge_ids": ["-road#1", "-road#0"],
                    "direction": "incoming",
                },
                {
                    "teacher_edge_id": "road#0",
                    "candidate_edge_ids": ["road#0", "road#1"],
                    "direction": "outgoing",
                },
            ],
        }
    ]


def test_owner_road_span_repair_candidates_keep_teacher_split_segments_independent(
    tmp_path: Path,
) -> None:
    teacher_net = tmp_path / "teacher.net.xml"
    candidate_net = tmp_path / "candidate.net.xml"
    teacher_net.write_text(
        """<net>
  <edge id="road#0" from="a" to="j"><lane id="road#0_0" index="0"/></edge>
  <edge id="-road#0" from="j" to="a"><lane id="-road#0_0" index="0"/></edge>
  <edge id="road#1" from="j" to="b"><lane id="road#1_0" index="0"/></edge>
  <edge id="-road#1" from="b" to="j"><lane id="-road#1_0" index="0"/></edge>
  <junction id="a" type="priority" x="0" y="0"/>
  <junction id="j" type="priority" x="20" y="0"/>
  <junction id="b" type="priority" x="40" y="0"/>
</net>""",
        encoding="utf-8",
    )
    candidate_net.write_text(
        """<net>
  <edge id="road#0" from="a" to="mid0"><lane id="road#0_0" index="0"/></edge>
  <edge id="road#2" from="mid0" to="j"><lane id="road#2_0" index="0"/></edge>
  <edge id="-road#2" from="j" to="mid0"><lane id="-road#2_0" index="0"/></edge>
  <edge id="-road#0" from="mid0" to="a"><lane id="-road#0_0" index="0"/></edge>
  <edge id="road#1" from="j" to="mid1"><lane id="road#1_0" index="0"/></edge>
  <edge id="road#3" from="mid1" to="b"><lane id="road#3_0" index="0"/></edge>
  <edge id="-road#3" from="b" to="mid1"><lane id="-road#3_0" index="0"/></edge>
  <edge id="-road#1" from="mid1" to="j"><lane id="-road#1_0" index="0"/></edge>
  <edge id="side" from="mid1" to="s"><lane id="side_0" index="0"/></edge>
  <junction id="a" type="priority" x="0" y="0"/>
  <junction id="mid0" type="priority" x="10" y="0"/>
  <junction id="j" type="priority" x="20" y="0"/>
  <junction id="mid1" type="priority" x="30" y="0"/>
  <junction id="b" type="priority" x="40" y="0"/>
  <junction id="s" type="priority" x="30" y="10"/>
</net>""",
        encoding="utf-8",
    )

    candidates = build_internal_movement_owner_road_span_repair_candidates(
        teacher_net,
        candidate_net,
        owner_id="j",
    )

    assert [(candidate["span_key"], candidate["status"]) for candidate in candidates] == [
        ("road#0", "ready"),
        ("road#1", "blocked"),
    ]
    assert candidates[0]["blocked_incident_edge_ids"] == []
    assert candidates[1]["blocked_incident_edge_ids"] == ["side"]


def test_write_owner_road_span_repair_candidate_replaces_ready_split_chain(
    tmp_path: Path,
) -> None:
    teacher_net = tmp_path / "teacher.net.xml"
    candidate_net = tmp_path / "candidate.net.xml"
    output_net = tmp_path / "candidate.road_span.net.xml"
    teacher_net.write_text(
        """<net>
  <edge id="road#0" from="j" to="a" type="highway.residential"><lane id="road#0_0" index="0" allow="passenger" speed="13.89"/></edge>
  <edge id="-road#0" from="a" to="j" type="highway.residential"><lane id="-road#0_0" index="0" allow="passenger" speed="13.89"/></edge>
  <junction id="a" type="priority" x="0" y="0"/>
  <junction id="j" type="priority" x="20" y="0"/>
</net>""",
        encoding="utf-8",
    )
    candidate_net.write_text(
        """<net>
  <edge id="road#0" from="j" to="mid"><lane id="road#0_0" index="0"/></edge>
  <edge id="road#1" from="mid" to="a"><lane id="road#1_0" index="0"/></edge>
  <edge id="-road#1" from="a" to="mid"><lane id="-road#1_0" index="0"/></edge>
  <edge id="-road#0" from="mid" to="j"><lane id="-road#0_0" index="0"/></edge>
  <junction id="a" type="priority" x="0" y="0" incLanes="road#1_0"/>
  <junction id="mid" type="priority" x="10" y="0"/>
  <junction id="j" type="priority" x="20" y="0" intLanes=":mid_0_0"/>
  <connection from="road#0" to="road#1" fromLane="0" toLane="0" dir="s"/>
  <connection from="-road#1" to="-road#0" fromLane="0" toLane="0" dir="s"/>
</net>""",
        encoding="utf-8",
    )
    candidates = build_internal_movement_owner_road_span_repair_candidates(
        teacher_net,
        candidate_net,
        owner_id="j",
    )

    report = write_internal_movement_owner_road_span_repair_candidate(
        teacher_net,
        candidate_net,
        output_net,
        candidates,
    )

    root = ET.parse(output_net).getroot()
    road = root.find("edge[@id='road#0']")
    reverse = root.find("edge[@id='-road#0']")
    assert report["applied_candidate_count"] == 1
    assert report["skipped_blocked_candidate_count"] == 0
    assert report["removed_edge_count"] == 2
    assert report["removed_junction_count"] == 1
    assert report["removed_connection_count"] == 2
    assert road.attrib["from"] == "j"
    assert road.attrib["to"] == "a"
    assert road.find("lane").attrib["allow"] == "passenger"
    assert reverse.attrib["from"] == "a"
    assert reverse.attrib["to"] == "j"
    assert root.find("edge[@id='road#1']") is None
    assert root.find("edge[@id='-road#1']") is None
    assert root.find("junction[@id='mid']") is None
    assert root.find("junction[@id='a']").attrib["incLanes"] == ""
    assert root.find("junction[@id='j']").attrib["intLanes"] == ""
    assert root.findall("connection") == []


def test_write_owner_ready_road_span_endpoint_replay_candidate_replays_other_endpoint(
    tmp_path: Path,
) -> None:
    teacher_net = tmp_path / "teacher.net.xml"
    candidate_net = tmp_path / "candidate.net.xml"
    output_net = tmp_path / "candidate.ready_span_replayed.net.xml"
    teacher_net.write_text(
        """<net>
  <edge id="road#0" from="j" to="a" type="highway.residential"><lane id="road#0_0" index="0" allow="passenger" speed="13.89"/></edge>
  <edge id="-road#0" from="a" to="j" type="highway.residential"><lane id="-road#0_0" index="0" allow="passenger" speed="13.89"/></edge>
  <junction id="a" type="priority" x="0" y="0" incLanes="road#0_0"/>
  <junction id="j" type="priority" x="20" y="0" incLanes="-road#0_0"/>
</net>""",
        encoding="utf-8",
    )
    candidate_net.write_text(
        """<net>
  <edge id="road#0" from="j" to="mid"><lane id="road#0_0" index="0"/></edge>
  <edge id="road#1" from="mid" to="a"><lane id="road#1_0" index="0"/></edge>
  <edge id="-road#1" from="a" to="mid"><lane id="-road#1_0" index="0"/></edge>
  <edge id="-road#0" from="mid" to="j"><lane id="-road#0_0" index="0"/></edge>
  <junction id="a" type="priority" x="0" y="0" incLanes="road#1_0"/>
  <junction id="mid" type="priority" x="10" y="0"/>
  <junction id="j" type="priority" x="20" y="0" incLanes="-road#0_0"/>
  <connection from="road#0" to="road#1" fromLane="0" toLane="0" dir="s"/>
  <connection from="-road#1" to="-road#0" fromLane="0" toLane="0" dir="s"/>
</net>""",
        encoding="utf-8",
    )

    report = write_internal_movement_owner_ready_road_span_endpoint_replay_candidate(
        teacher_net,
        candidate_net,
        output_net,
        owner_id="j",
    )

    root = ET.parse(output_net).getroot()
    road = root.find("edge[@id='road#0']")
    reverse = root.find("edge[@id='-road#0']")
    assert report["status"] == "pass"
    assert report["repair_scope"] == "ready_road_span_endpoint_replay"
    assert report["selected_ready_road_span_candidate_count"] == 1
    assert report["skipped_blocked_road_span_candidate_count"] == 0
    assert report["replayed_endpoint_owner_ids"] == ["a"]
    assert report["owner_road_connectivity_audit"]["status"] == "pass"
    assert road.attrib["from"] == "j"
    assert road.attrib["to"] == "a"
    assert reverse.attrib["from"] == "a"
    assert reverse.attrib["to"] == "j"
    assert root.find("edge[@id='road#1']") is None
    assert root.find("edge[@id='-road#1']") is None
    assert root.find("junction[@id='mid']") is None


def test_write_owner_ready_road_span_endpoint_replay_candidate_skips_owner_terminal_removal(
    tmp_path: Path,
) -> None:
    teacher_net = tmp_path / "teacher.net.xml"
    candidate_net = tmp_path / "candidate.net.xml"
    output_net = tmp_path / "candidate.ready_span_replayed.net.xml"
    teacher_net.write_text(
        """<net>
  <edge id="road#0" from="a" to="j"><lane id="road#0_0" index="0"/></edge>
  <edge id="-road#0" from="j" to="a"><lane id="-road#0_0" index="0"/></edge>
  <junction id="a" type="priority" x="0" y="0"/>
  <junction id="j" type="priority" x="20" y="0"/>
</net>""",
        encoding="utf-8",
    )
    candidate_net.write_text(
        """<net>
  <edge id="road#0" from="a" to="mid"><lane id="road#0_0" index="0"/></edge>
  <edge id="road#1" from="mid" to="j"><lane id="road#1_0" index="0"/></edge>
  <edge id="-road#1" from="j" to="mid"><lane id="-road#1_0" index="0"/></edge>
  <edge id="-road#0" from="mid" to="a"><lane id="-road#0_0" index="0"/></edge>
  <junction id="a" type="priority" x="0" y="0"/>
  <junction id="mid" type="priority" x="10" y="0"/>
  <junction id="j" type="priority" x="20" y="0"/>
</net>""",
        encoding="utf-8",
    )

    report = write_internal_movement_owner_ready_road_span_endpoint_replay_candidate(
        teacher_net,
        candidate_net,
        output_net,
        owner_id="j",
    )

    root = ET.parse(output_net).getroot()
    assert report["selected_ready_road_span_candidate_count"] == 0
    assert report["skipped_owner_terminal_road_span_candidate_count"] == 1
    assert "skipped_owner_terminal_road_span_candidate" in report["warnings"]
    assert root.find("edge[@id='road#1']") is not None
    assert root.find("junction[@id='mid']") is not None


def test_write_owner_layered_teacher_replay_candidate_runs_owner_then_road_span_endpoint(
    tmp_path: Path,
) -> None:
    teacher_net = tmp_path / "teacher.net.xml"
    candidate_net = tmp_path / "candidate.net.xml"
    output_net = tmp_path / "candidate.layered.net.xml"
    teacher_net.write_text(
        """<net>
  <edge id="road#0" from="j" to="a" type="highway.residential"><lane id="road#0_0" index="0" allow="passenger" speed="13.89"/></edge>
  <edge id="-road#0" from="a" to="j" type="highway.residential"><lane id="-road#0_0" index="0" allow="passenger" speed="13.89"/></edge>
  <junction id="a" type="priority" x="0" y="0" incLanes="road#0_0"/>
  <junction id="j" type="priority" x="20" y="0" incLanes="-road#0_0"/>
</net>""",
        encoding="utf-8",
    )
    candidate_net.write_text(
        """<net>
  <edge id="road#0" from="j" to="mid"><lane id="road#0_0" index="0"/></edge>
  <edge id="road#1" from="mid" to="a"><lane id="road#1_0" index="0"/></edge>
  <edge id="-road#1" from="a" to="mid"><lane id="-road#1_0" index="0"/></edge>
  <edge id="-road#0" from="mid" to="j"><lane id="-road#0_0" index="0"/></edge>
  <junction id="a" type="priority" x="0" y="0" incLanes="road#1_0"/>
  <junction id="mid" type="priority" x="10" y="0"/>
  <junction id="j" type="traffic_light" x="20" y="0" incLanes="-road#0_0"/>
  <connection from="road#0" to="road#1" fromLane="0" toLane="0" dir="s"/>
  <connection from="-road#1" to="-road#0" fromLane="0" toLane="0" dir="s"/>
</net>""",
        encoding="utf-8",
    )

    report = write_internal_movement_owner_layered_teacher_replay_candidate(
        teacher_net,
        candidate_net,
        output_net,
        owner_id="j",
    )

    root = ET.parse(output_net).getroot()
    assert report["status"] == "pass"
    assert report["repair_scope"] == "layered_internal_movement_owner_teacher_replay"
    assert Path(report["owner_replay_file"]).exists()
    assert report["owner_replay_report"]["repair_scope"] == "internal_movement_owner_teacher_replay"
    assert report["road_span_endpoint_replay_report"]["repair_scope"] == "ready_road_span_endpoint_replay"
    assert report["road_span_endpoint_replay_report"]["replayed_endpoint_owner_ids"] == ["a"]
    assert root.find("edge[@id='road#0']").attrib["to"] == "a"
    assert root.find("edge[@id='-road#0']").attrib["from"] == "a"
    assert root.find("junction[@id='mid']") is None


def test_write_owner_layered_teacher_replay_candidate_can_pre_repair_ready_spans(
    tmp_path: Path,
) -> None:
    teacher_net = tmp_path / "teacher.net.xml"
    candidate_net = tmp_path / "candidate.net.xml"
    output_net = tmp_path / "candidate.layered.net.xml"
    teacher_net.write_text(
        """<net>
  <edge id="road#0" from="a" to="j"><lane id="road#0_0" index="0"/></edge>
  <edge id="-road#0" from="j" to="a"><lane id="-road#0_0" index="0"/></edge>
  <junction id="a" type="priority" x="0" y="0"/>
  <junction id="j" type="priority" x="20" y="0"/>
</net>""",
        encoding="utf-8",
    )
    candidate_net.write_text(
        """<net>
  <edge id="road#0" from="a" to="mid"><lane id="road#0_0" index="0"/></edge>
  <edge id="road#1" from="mid" to="j"><lane id="road#1_0" index="0"/></edge>
  <edge id="-road#1" from="j" to="mid"><lane id="-road#1_0" index="0"/></edge>
  <edge id="-road#0" from="mid" to="a"><lane id="-road#0_0" index="0"/></edge>
  <junction id="a" type="priority" x="0" y="0"/>
  <junction id="mid" type="priority" x="10" y="0"/>
  <junction id="j" type="priority" x="20" y="0"/>
</net>""",
        encoding="utf-8",
    )

    report = write_internal_movement_owner_layered_teacher_replay_candidate(
        teacher_net,
        candidate_net,
        output_net,
        owner_id="j",
        pre_repair_ready_road_spans=True,
    )

    root = ET.parse(output_net).getroot()
    assert report["status"] == "pass"
    assert report["pre_road_span_repair"]["applied_candidate_count"] == 1
    assert report["pre_replayed_endpoint_owner_ids"] == ["a"]
    assert report["pre_endpoint_replay_reports"][0]["owner_id"] == "a"
    assert root.find("edge[@id='road#0']").attrib["to"] == "j"
    assert root.find("edge[@id='-road#0']").attrib["from"] == "j"
    assert root.find("junction[@id='mid']") is None


def test_write_owner_layered_teacher_replay_candidate_can_replay_blocked_span_endpoint_owner(
    tmp_path: Path,
) -> None:
    teacher_net = tmp_path / "teacher.net.xml"
    candidate_net = tmp_path / "candidate.net.xml"
    output_net = tmp_path / "candidate.layered.net.xml"
    teacher_net.write_text(
        """<net>
  <edge id="road#0" from="j" to="cluster_a_b"><lane id="road#0_0" index="0"/></edge>
  <edge id="-road#0" from="cluster_a_b" to="j"><lane id="-road#0_0" index="0"/></edge>
  <edge id="out" from="cluster_a_b" to="b"><lane id="out_0" index="0"/></edge>
  <edge id=":cluster_a_b_0" function="internal"><lane id=":cluster_a_b_0_0" index="0"/></edge>
  <junction id="j" type="priority" x="0" y="0" incLanes="-road#0_0"/>
  <junction id="cluster_a_b" type="priority" x="20" y="0" incLanes="road#0_0" intLanes=":cluster_a_b_0_0"/>
  <connection from="road#0" to="out" fromLane="0" toLane="0" via=":cluster_a_b_0_0" dir="s"/>
  <connection from=":cluster_a_b_0" to="out" fromLane="0" toLane="0" dir="s"/>
</net>""",
        encoding="utf-8",
    )
    candidate_net.write_text(
        """<net>
  <edge id="road#0" from="j" to="mid"><lane id="road#0_0" index="0"/></edge>
  <edge id="road#1" from="mid" to="cluster_a_b"><lane id="road#1_0" index="0"/></edge>
  <edge id="-road#1" from="cluster_a_b" to="mid"><lane id="-road#1_0" index="0"/></edge>
  <edge id="-road#0" from="mid" to="j"><lane id="-road#0_0" index="0"/></edge>
  <edge id="side" from="mid" to="s"><lane id="side_0" index="0"/></edge>
  <edge id="out" from="cluster_a_b" to="b"><lane id="out_0" index="0"/></edge>
  <junction id="j" type="priority" x="0" y="0"/>
  <junction id="mid" type="priority" x="10" y="0"/>
  <junction id="cluster_a_b" type="priority" x="20" y="0"/>
  <junction id="s" type="priority" x="10" y="10"/>
  <junction id="b" type="priority" x="30" y="0"/>
</net>""",
        encoding="utf-8",
    )

    report = write_internal_movement_owner_layered_teacher_replay_candidate(
        teacher_net,
        candidate_net,
        output_net,
        owner_id="j",
        replay_blocked_road_span_endpoint_owners=True,
    )

    root = ET.parse(output_net).getroot()
    assert report["status"] == "pass"
    assert report["blocked_overlay_replayed_endpoint_owner_ids"] == ["cluster_a_b"]
    assert report["blocked_replayed_endpoint_owner_ids"] == []
    assert report["pre_endpoint_replay_reports"][0]["owner_id"] == "cluster_a_b"
    assert root.find("edge[@id=':cluster_a_b_0']") is not None
    assert root.find("connection[@from='road#1'][@to='out']") is not None
    assert root.find("junction[@id='mid']") is not None


def test_write_owner_layered_teacher_replay_candidate_overlays_blocked_span_before_owner_replay(
    tmp_path: Path,
) -> None:
    teacher_net = tmp_path / "teacher.net.xml"
    candidate_net = tmp_path / "candidate.net.xml"
    output_net = tmp_path / "candidate.layered.net.xml"
    teacher_net.write_text(
        """<net>
  <edge id="in" from="a" to="j"><lane id="in_0" index="0"/></edge>
  <edge id="road#3" from="j" to="cluster_a_b"><lane id="road#3_0" index="0"/></edge>
  <edge id="-road#3" from="cluster_a_b" to="j"><lane id="-road#3_0" index="0"/></edge>
  <edge id="out" from="cluster_a_b" to="b"><lane id="out_0" index="0"/></edge>
  <edge id=":j_0" function="internal"><lane id=":j_0_0" index="0"/></edge>
  <edge id=":cluster_a_b_0" function="internal"><lane id=":cluster_a_b_0_0" index="0"/></edge>
  <junction id="j" type="priority" x="0" y="0" incLanes="in_0 -road#3_0" intLanes=":j_0_0"/>
  <junction id="cluster_a_b" type="priority" x="20" y="0" incLanes="road#3_0" intLanes=":cluster_a_b_0_0"/>
  <junction id="a" type="priority" x="-10" y="0"/>
  <junction id="b" type="priority" x="30" y="0"/>
  <connection from="in" to="road#3" fromLane="0" toLane="0" via=":j_0_0" dir="s"/>
  <connection from=":j_0" to="road#3" fromLane="0" toLane="0" dir="s"/>
  <connection from="road#3" to="out" fromLane="0" toLane="0" via=":cluster_a_b_0_0" dir="s"/>
  <connection from=":cluster_a_b_0" to="out" fromLane="0" toLane="0" dir="s"/>
</net>""",
        encoding="utf-8",
    )
    candidate_net.write_text(
        """<net>
  <edge id="in" from="a" to="j"><lane id="in_0" index="0"/></edge>
  <edge id="road#5" from="j" to="mid"><lane id="road#5_0" index="0"/></edge>
  <edge id="road#6" from="mid" to="cluster_a_b"><lane id="road#6_0" index="0"/></edge>
  <edge id="-road#6" from="cluster_a_b" to="mid"><lane id="-road#6_0" index="0"/></edge>
  <edge id="-road#5" from="mid" to="j"><lane id="-road#5_0" index="0"/></edge>
  <edge id="side" from="mid" to="s"><lane id="side_0" index="0"/></edge>
  <edge id="out" from="cluster_a_b" to="b"><lane id="out_0" index="0"/></edge>
  <edge id=":j_old" function="internal"><lane id=":j_old_0" index="0"/></edge>
  <junction id="j" type="priority" x="0" y="0" incLanes="in_0 -road#5_0" intLanes=":j_old_0"/>
  <junction id="mid" type="priority" x="10" y="0"/>
  <junction id="cluster_a_b" type="priority" x="20" y="0"/>
  <junction id="a" type="priority" x="-10" y="0"/>
  <junction id="b" type="priority" x="30" y="0"/>
  <junction id="s" type="priority" x="10" y="10"/>
  <connection from="in" to="road#5" fromLane="0" toLane="0" via=":j_old_0" dir="s"/>
  <connection from=":j_old" to="road#5" fromLane="0" toLane="0" dir="s"/>
  <connection from="road#5" to="road#6" fromLane="0" toLane="0" dir="s"/>
  <connection from="-road#6" to="-road#5" fromLane="0" toLane="0" dir="s"/>
</net>""",
        encoding="utf-8",
    )

    report = write_internal_movement_owner_layered_teacher_replay_candidate(
        teacher_net,
        candidate_net,
        output_net,
        owner_id="j",
        replay_blocked_road_span_endpoint_owners=True,
    )

    root = ET.parse(output_net).getroot()
    assert report["status"] == "pass"
    assert report["blocked_road_span_overlay"]["added_edge_count"] == 2
    assert report["blocked_overlay_replayed_endpoint_owner_ids"] == ["cluster_a_b"]
    assert report["owner_replay_report"]["edge_map"]["road#3"] == "road#3"
    assert root.find("edge[@id='road#3']").attrib["from"] == "j"
    assert root.find("connection[@from='in'][@to='road#3']") is not None
    assert root.find("connection[@from='in'][@to='road#5']") is None
    assert root.find("connection[@from='road#3'][@to='out']") is not None
    assert root.find("junction[@id='mid']") is not None


def test_write_owner_layered_teacher_replay_candidate_replays_mapped_terminal_endpoint(
    tmp_path: Path,
) -> None:
    teacher_net = tmp_path / "teacher.net.xml"
    candidate_net = tmp_path / "candidate.net.xml"
    output_net = tmp_path / "candidate.layered.net.xml"
    teacher_net.write_text(
        """<net>
  <edge id="road" from="b" to="j"><lane id="road_0" index="0"/></edge>
  <edge id="-road" from="j" to="b"><lane id="-road_0" index="0"/></edge>
  <edge id="next" from="b" to="c"><lane id="next_0" index="0"/></edge>
  <edge id="-next" from="c" to="b"><lane id="-next_0" index="0"/></edge>
  <edge id=":j_0" function="internal"><lane id=":j_0_0" index="0"/></edge>
  <edge id=":b_0" function="internal"><lane id=":b_0_0" index="0"/></edge>
  <junction id="j" type="priority" x="0" y="0" incLanes="road_0" intLanes=":j_0_0"/>
  <junction id="b" type="priority" x="10" y="0" incLanes="-road_0 -next_0" intLanes=":b_0_0"/>
  <junction id="c" type="priority" x="20" y="0"/>
  <connection from="-road" to="next" fromLane="0" toLane="0" via=":b_0_0" dir="s"/>
  <connection from=":b_0" to="next" fromLane="0" toLane="0" dir="s"/>
</net>""",
        encoding="utf-8",
    )
    candidate_net.write_text(
        """<net>
  <edge id="road" from="b" to="j"><lane id="road_0" index="0"/></edge>
  <edge id="-road" from="j" to="b"><lane id="-road_0" index="0"/></edge>
  <edge id="next" from="b" to="c"><lane id="next_0" index="0"/></edge>
  <edge id="-next" from="c" to="b"><lane id="-next_0" index="0"/></edge>
  <junction id="j" type="priority" x="0" y="0" incLanes="road_0"/>
  <junction id="b" type="priority" x="10" y="0"/>
  <junction id="c" type="priority" x="20" y="0"/>
</net>""",
        encoding="utf-8",
    )

    report = write_internal_movement_owner_layered_teacher_replay_candidate(
        teacher_net,
        candidate_net,
        output_net,
        owner_id="j",
        replay_blocked_road_span_endpoint_owners=True,
    )

    root = ET.parse(output_net).getroot()
    assert report["status"] == "pass"
    assert "b" in report["pre_endpoint_owner_ids"]
    assert root.find("connection[@from='-road'][@to='next']") is not None
    assert report["owner_road_connectivity_audit"]["status"] == "pass"


def test_write_owner_layered_teacher_replay_candidate_refreshes_owner_after_endpoint_replay(
    tmp_path: Path,
) -> None:
    teacher_net = tmp_path / "teacher.net.xml"
    candidate_net = tmp_path / "candidate.net.xml"
    output_net = tmp_path / "candidate.layered.net.xml"
    teacher_net.write_text(
        """<net>
  <edge id="in" from="a" to="j"><lane id="in_0" index="0"/></edge>
  <edge id="road#3" from="j" to="b"><lane id="road#3_0" index="0"/></edge>
  <edge id=":j_0" function="internal"><lane id=":j_0_0" index="0"/></edge>
  <junction id="a" type="priority" x="-10" y="0"/>
  <junction id="j" type="priority" x="0" y="0" incLanes="in_0" intLanes=":j_0_0"/>
  <junction id="b" type="priority" x="10" y="0" incLanes="road#3_0" intLanes=""/>
  <connection from="in" to="road#3" fromLane="0" toLane="0" via=":j_0_0" dir="s"/>
  <connection from=":j_0" to="road#3" fromLane="0" toLane="0" dir="s"/>
</net>""",
        encoding="utf-8",
    )
    candidate_net.write_text(
        """<net>
  <edge id="in" from="a" to="j"><lane id="in_0" index="0"/></edge>
  <edge id="road#5" from="j" to="mid"><lane id="road#5_0" index="0"/></edge>
  <junction id="a" type="priority" x="-10" y="0"/>
  <junction id="j" type="priority" x="0" y="0" incLanes="in_0" intLanes=""/>
  <junction id="mid" type="priority" x="5" y="0" incLanes="road#5_0" intLanes=""/>
  <junction id="b" type="priority" x="10" y="0"/>
</net>""",
        encoding="utf-8",
    )

    report = write_internal_movement_owner_layered_teacher_replay_candidate(
        teacher_net,
        candidate_net,
        output_net,
        owner_id="j",
    )

    root = ET.parse(output_net).getroot()
    assert report["status"] == "pass"
    assert root.find("edge[@id='road#3']") is not None
    assert root.find("connection[@from='in'][@to='road#3']") is not None
    assert root.find("connection[@from='in'][@to='road#5']") is None
    assert report["owner_road_connectivity_audit"]["status"] == "pass"


def test_write_owner_layered_teacher_replay_candidate_promotes_bounded_convergence_pass(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from torii_sumo.core import road_connectivity_teacher_model as road_model

    teacher_net = tmp_path / "teacher.net.xml"
    candidate_net = tmp_path / "candidate.net.xml"
    output_net = tmp_path / "candidate.layered.net.xml"
    teacher_net.write_text("<net/>", encoding="utf-8")
    candidate_net.write_text("<net/>", encoding="utf-8")

    def fake_owner_replay(_teacher, source, target, *, owner_id, **_kwargs):
        Path(target).write_bytes(Path(source).read_bytes())
        return {
            "status": "pass",
            "owner_id": owner_id,
            "output_file": str(target),
            "edge_map": {"road": "road"},
        }

    def fake_road_span_replay(_teacher, source, target, **_kwargs):
        Path(target).write_bytes(Path(source).read_bytes())
        return {
            "status": "pass",
            "replayed_endpoint_owner_ids": [],
        }

    def fake_audit(_teacher, candidate, **_kwargs):
        converged = "convergence_endpoint" in Path(candidate).name
        return {
            "status": "pass" if converged else "fail",
            "gate": {"lane_delta_count": 0 if converged else 2},
            "lane_deltas": [],
        }

    monkeypatch.setattr(
        road_model,
        "write_internal_movement_owner_teacher_replay_candidate",
        fake_owner_replay,
    )
    monkeypatch.setattr(
        road_model,
        "write_internal_movement_owner_ready_road_span_endpoint_replay_candidate",
        fake_road_span_replay,
    )
    monkeypatch.setattr(
        road_model,
        "build_internal_movement_owner_road_connectivity_parity_audit",
        fake_audit,
    )
    monkeypatch.setattr(
        road_model,
        "_teacher_endpoint_owner_ids_for_edges",
        lambda *_args, **_kwargs: ["endpoint"],
    )

    report = road_model.write_internal_movement_owner_layered_teacher_replay_candidate(
        teacher_net,
        candidate_net,
        output_net,
        owner_id="j",
    )

    assert report["status"] == "pass"
    assert report["convergence_status"] == "promoted"
    assert report["before_convergence_lane_delta_count"] == 2
    assert report["after_convergence_lane_delta_count"] == 0
    assert [item["owner_id"] for item in report["convergence_endpoint_replay_reports"]] == [
        "endpoint"
    ]
    assert report["owner_road_connectivity_audit"]["status"] == "pass"


def test_write_owner_layered_teacher_replay_candidate_returns_fail_when_owner_replay_blocks(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from torii_sumo.core import road_connectivity_teacher_model as road_model

    teacher_net = tmp_path / "teacher.net.xml"
    candidate_net = tmp_path / "candidate.net.xml"
    output_net = tmp_path / "candidate.layered.net.xml"
    teacher_net.write_text(
        """<net>
  <edge id="in" from="a" to="j"><lane id="in_0" index="0"/></edge>
  <junction id="j" type="priority" x="0" y="0"/>
</net>""",
        encoding="utf-8",
    )
    candidate_net.write_text(
        """<net>
  <edge id="in" from="a" to="j"><lane id="in_0" index="0"/></edge>
  <junction id="j" type="priority" x="0" y="0"/>
</net>""",
        encoding="utf-8",
    )

    def fake_owner_replay(*args, **kwargs):
        return {
            "status": "blocked",
            "repair_scope": "internal_movement_owner_teacher_replay",
            "blocking_reason": "missing_road_dependencies",
            "output_file": str(args[2]),
        }

    monkeypatch.setattr(
        road_model,
        "write_internal_movement_owner_teacher_replay_candidate",
        fake_owner_replay,
    )

    report = road_model.write_internal_movement_owner_layered_teacher_replay_candidate(
        teacher_net,
        candidate_net,
        output_net,
        owner_id="j",
        replay_blocked_road_span_endpoint_owners=True,
    )

    assert report["status"] == "fail"
    assert report["owner_replay_report"]["status"] == "blocked"
    assert report["road_span_endpoint_replay_report"]["status"] == "skipped"
    assert output_net.exists()


def test_write_internal_movement_owner_teacher_replay_candidate_returns_blocked_output(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from torii_sumo.core import road_connectivity_teacher_model as road_model

    teacher_net = tmp_path / "teacher.net.xml"
    candidate_net = tmp_path / "candidate.net.xml"
    output_net = tmp_path / "candidate.replayed.net.xml"
    teacher_net.write_text(
        """<net>
  <edge id="in" from="a" to="j"><lane id="in_0" index="0"/></edge>
  <junction id="j" type="priority" x="0" y="0"/>
</net>""",
        encoding="utf-8",
    )
    candidate_net.write_text(
        """<net>
  <edge id="in" from="a" to="j"><lane id="in_0" index="0"/></edge>
  <junction id="j" type="priority" x="0" y="0"/>
</net>""",
        encoding="utf-8",
    )

    def fake_bundle_replay(*args, **kwargs):
        return {
            "status": "blocked",
            "repair_scope": "internal_movement_owner_bundle",
            "blocking_reason": "missing_road_dependencies",
            "output_file": str(args[2]),
        }

    monkeypatch.setattr(
        road_model,
        "write_internal_movement_owner_bundle_replacement_candidate",
        fake_bundle_replay,
    )

    report = road_model.write_internal_movement_owner_teacher_replay_candidate(
        teacher_net,
        candidate_net,
        output_net,
        owner_id="j",
    )

    assert report["status"] == "blocked"
    assert report["bundle_replay"]["blocking_reason"] == "missing_road_dependencies"
    assert output_net.exists()


def test_owner_teacher_replay_downgrades_stale_tls_connection_beyond_teacher_capacity(
    tmp_path: Path,
) -> None:
    teacher_net = tmp_path / "teacher.net.xml"
    candidate_net = tmp_path / "candidate.net.xml"
    output_net = tmp_path / "candidate.replayed.net.xml"
    teacher_net.write_text(
        """<net>
  <edge id="in" from="a" to="j"><lane id="in_0" index="0"/></edge>
  <edge id="out" from="j" to="b"><lane id="out_0" index="0"/></edge>
  <edge id=":j_0" function="internal"><lane id=":j_0_0" index="0"/></edge>
  <junction id="a" type="priority" x="0" y="0"/>
  <junction id="j" type="traffic_light" x="10" y="0" incLanes="in_0" intLanes=":j_0_0"/>
  <junction id="b" type="priority" x="20" y="0" incLanes="out_0"/>
  <connection from="in" to="out" fromLane="0" toLane="0" via=":j_0_0" tl="j" linkIndex="0" dir="s" state="O"/>
  <tlLogic id="j" type="static" programID="0" offset="0"><phase duration="30" state="G"/></tlLogic>
</net>""",
        encoding="utf-8",
    )
    candidate_net.write_text(
        """<net>
  <edge id="in" from="a" to="j"><lane id="in_0" index="0"/></edge>
  <edge id="out" from="j" to="b"><lane id="out_0" index="0"/></edge>
  <edge id="remote_in" from="x" to="mid"><lane id="remote_in_0" index="0"/></edge>
  <edge id="remote_out" from="mid" to="z"><lane id="remote_out_0" index="0"/></edge>
  <edge id=":mid_0" function="internal"><lane id=":mid_0_0" index="0"/></edge>
  <junction id="a" type="priority" x="0" y="0"/>
  <junction id="j" type="traffic_light" x="10" y="0" incLanes="in_0" intLanes=""/>
  <junction id="b" type="priority" x="20" y="0" incLanes="out_0"/>
  <junction id="x" type="priority" x="0" y="10"/>
  <junction id="mid" type="priority" x="10" y="10" incLanes="remote_in_0" intLanes=":mid_0_0"/>
  <junction id="z" type="priority" x="20" y="10" incLanes="remote_out_0"/>
  <connection from="in" to="out" fromLane="0" toLane="0" tl="j" linkIndex="0" dir="s" state="O"/>
  <connection from="remote_in" to="remote_out" fromLane="0" toLane="0" via=":mid_0_0" tl="j" linkIndex="3" dir="s" state="O"/>
  <tlLogic id="j" type="static" programID="0" offset="0"><phase duration="30" state="GGGG"/></tlLogic>
</net>""",
        encoding="utf-8",
    )

    report = write_internal_movement_owner_teacher_replay_candidate(
        teacher_net,
        candidate_net,
        output_net,
        owner_id="j",
        copy_tls=True,
    )

    root = ET.parse(output_net).getroot()
    stale_continuation = root.find("connection[@from='remote_in'][@to='remote_out']")
    assert report["status"] == "pass"
    assert stale_continuation is not None
    assert "tl" not in stale_continuation.attrib
    assert "linkIndex" not in stale_continuation.attrib
    assert stale_continuation.attrib["uncontrolled"] == "true"
    assert report["bundle_replay"]["downgraded_stale_tls_connection_count"] == 1


def test_evaluate_road_template_repair_promotion_blocks_worsened_common_delta() -> None:
    before = {
        "gate": {
            "lane_missing_template_count": 104,
            "lane_extra_template_count": 49,
            "lane_common_count_delta_sum": 1759,
            "connection_missing_template_count": 1282,
            "connection_extra_template_count": 822,
            "connection_common_count_delta_sum": 1388,
        }
    }
    after = {
        "gate": {
            "lane_missing_template_count": 103,
            "lane_extra_template_count": 48,
            "lane_common_count_delta_sum": 2103,
            "connection_missing_template_count": 1263,
            "connection_extra_template_count": 803,
            "connection_common_count_delta_sum": 1899,
        }
    }

    report = evaluate_road_template_repair_promotion(before, after)

    assert report == {
        "status": "fail",
        "claim_status": "diagnostic-demo",
        "metric_scope": "road",
        "promotion_status": "blocked",
        "reason": "road_template_gate_metric_worsened",
        "before_score": 5404,
        "after_score": 6219,
        "score_delta": 815,
        "improved_metrics": {
            "lane_missing_template_count": -1,
            "lane_extra_template_count": -1,
            "connection_missing_template_count": -19,
            "connection_extra_template_count": -19,
        },
        "worsened_metrics": {
            "lane_common_count_delta_sum": 344,
            "connection_common_count_delta_sum": 511,
        },
    }


def test_evaluate_road_template_repair_promotion_passes_non_worsening_improvement() -> None:
    before = {
        "gate": {
            "lane_missing_template_count": 2,
            "lane_extra_template_count": 1,
            "lane_common_count_delta_sum": 0,
            "connection_missing_template_count": 3,
            "connection_extra_template_count": 0,
            "connection_common_count_delta_sum": 0,
        }
    }
    after = {
        "gate": {
            "lane_missing_template_count": 1,
            "lane_extra_template_count": 1,
            "lane_common_count_delta_sum": 0,
            "connection_missing_template_count": 3,
            "connection_extra_template_count": 0,
            "connection_common_count_delta_sum": 0,
        }
    }

    report = evaluate_road_template_repair_promotion(before, after)

    assert report["status"] == "pass"
    assert report["promotion_status"] == "pass"
    assert report["reason"] == "road_template_gate_improved"
    assert report["before_score"] == 6
    assert report["after_score"] == 5
    assert report["improved_metrics"] == {"lane_missing_template_count": -1}
    assert report["worsened_metrics"] == {}


def test_evaluate_road_template_repair_promotion_lane_scope_ignores_connection_regression() -> None:
    before = {
        "gate": {
            "lane_missing_template_count": 2,
            "lane_extra_template_count": 1,
            "lane_common_count_delta_sum": 0,
            "connection_missing_template_count": 1,
            "connection_extra_template_count": 0,
            "connection_common_count_delta_sum": 0,
        }
    }
    after = {
        "gate": {
            "lane_missing_template_count": 1,
            "lane_extra_template_count": 1,
            "lane_common_count_delta_sum": 0,
            "connection_missing_template_count": 1,
            "connection_extra_template_count": 0,
            "connection_common_count_delta_sum": 5,
        }
    }

    road_report = evaluate_road_template_repair_promotion(before, after)
    lane_report = evaluate_road_template_repair_promotion(before, after, metric_scope="lane")

    assert road_report["promotion_status"] == "blocked"
    assert road_report["reason"] == "road_template_gate_metric_worsened"
    assert lane_report["metric_scope"] == "lane"
    assert lane_report["status"] == "pass"
    assert lane_report["promotion_status"] == "pass"
    assert lane_report["reason"] == "road_template_gate_improved"
    assert lane_report["before_score"] == 3
    assert lane_report["after_score"] == 2
    assert lane_report["improved_metrics"] == {"lane_missing_template_count": -1}
    assert lane_report["worsened_metrics"] == {}


def test_run_road_lane_template_repair_probe_promotes_safe_candidate(tmp_path: Path) -> None:
    teacher_net = tmp_path / "teacher.net.xml"
    candidate_net = tmp_path / "candidate.net.xml"
    teacher_net.write_text(
        """<net>
  <edge id="service_a" type="highway.service">
    <lane id="service_a_0" index="0" allow="passenger" speed="5.0" length="25.0"/>
  </edge>
</net>""",
        encoding="utf-8",
    )
    candidate_net.write_text(
        """<net>
  <edge id="service_a" type="highway.service">
    <lane id="service_a_0" index="0" allow="pedestrian passenger" speed="5.0" length="25.0"/>
  </edge>
</net>""",
        encoding="utf-8",
    )

    report = run_road_lane_template_repair_probe(
        teacher_net,
        candidate_net,
        tmp_path / "probe",
        prefix="demo",
    )

    best_variant = Path(report["best_variant_file"])
    repaired_lane = ET.parse(best_variant).getroot().find("./edge[@id='service_a']/lane")
    assert report["status"] == "pass"
    assert report["road_lane_template_repair_status"] == "evaluated"
    assert report["repair_scope"] == "teacher_edge_subset"
    assert report["promotion_metric_scope"] == "lane"
    assert report["candidate_count"] == 1
    assert report["pass_candidate_count"] == 1
    assert report["blocked_candidate_count"] == 0
    assert report["best_candidate_index"] == 1
    assert best_variant.name == "demo_001.net.xml"
    assert repaired_lane.attrib["allow"] == "passenger"
    assert report["candidates"][0]["promotion_gate"]["promotion_status"] == "pass"
    assert report["candidates"][0]["promotion_gate"]["before_score"] == 2
    assert report["candidates"][0]["promotion_gate"]["after_score"] == 0


def test_run_road_lane_template_repair_probe_can_use_single_edge_scope(tmp_path: Path) -> None:
    teacher_net = tmp_path / "teacher.net.xml"
    candidate_net = tmp_path / "candidate.net.xml"
    teacher_net.write_text(
        """<net>
  <edge id="service_a" type="highway.service">
    <lane id="service_a_0" index="0" allow="passenger" speed="5.0" length="25.0"/>
  </edge>
</net>""",
        encoding="utf-8",
    )
    candidate_net.write_text(
        """<net>
  <edge id="service_a" type="highway.service">
    <lane id="service_a_0" index="0" allow="pedestrian passenger" speed="5.0" length="25.0"/>
  </edge>
</net>""",
        encoding="utf-8",
    )

    report = run_road_lane_template_repair_probe(
        teacher_net,
        candidate_net,
        tmp_path / "probe",
        prefix="demo",
        use_single_edge=True,
    )

    assert report["repair_scope"] == "teacher_single_edge"
    assert report["candidate_count"] == 1
    assert report["pass_candidate_count"] == 1


def test_run_road_lane_template_repair_probe_can_use_local_lane_gate(tmp_path: Path) -> None:
    teacher_net = tmp_path / "teacher.net.xml"
    candidate_net = tmp_path / "candidate.net.xml"
    teacher_net.write_text(
        """<net>
  <edge id="service_a" type="highway.service">
    <lane id="service_a_0" index="0" allow="passenger" speed="5.0" length="25.0"/>
  </edge>
</net>""",
        encoding="utf-8",
    )
    candidate_net.write_text(
        """<net>
  <edge id="service_a" type="highway.service">
    <lane id="service_a_0" index="0" allow="pedestrian passenger" speed="5.0" length="25.0"/>
  </edge>
</net>""",
        encoding="utf-8",
    )

    report = run_road_lane_template_repair_probe(
        teacher_net,
        candidate_net,
        tmp_path / "probe",
        prefix="demo",
        use_single_edge=True,
        promotion_metric_scope="local_lane",
    )

    gate = report["candidates"][0]["promotion_gate"]
    assert report["promotion_metric_scope"] == "local_lane"
    assert report["pass_candidate_count"] == 1
    assert gate["metric_scope"] == "local_lane"
    assert gate["promotion_status"] == "pass"
    assert gate["reason"] == "road_lane_local_replay_applied"


def test_run_road_lane_template_batch_repair_probe_writes_combined_single_edge_candidate(tmp_path: Path) -> None:
    teacher_net = tmp_path / "teacher.net.xml"
    candidate_net = tmp_path / "candidate.net.xml"
    output_net = tmp_path / "candidate.batch.net.xml"
    teacher_net.write_text(
        """<net>
  <edge id="fix_a" type="highway.service">
    <lane id="fix_a_0" index="0" allow="passenger" speed="5.0" length="25.0"/>
  </edge>
  <edge id="fix_b" type="highway.service">
    <lane id="fix_b_0" index="0" allow="passenger" speed="5.0" length="25.0"/>
  </edge>
</net>""",
        encoding="utf-8",
    )
    candidate_net.write_text(
        """<net>
  <edge id="fix_a" type="highway.service">
    <lane id="fix_a_0" index="0" allow="pedestrian passenger" speed="5.0" length="25.0"/>
  </edge>
  <edge id="fix_b" type="highway.service">
    <lane id="fix_b_0" index="0" allow="pedestrian passenger" speed="5.0" length="25.0"/>
  </edge>
</net>""",
        encoding="utf-8",
    )

    report = run_road_lane_template_batch_repair_probe(
        teacher_net,
        candidate_net,
        output_net,
        max_candidates=2,
    )

    root = ET.parse(output_net).getroot()
    assert report["status"] == "pass"
    assert report["repair_scope"] == "teacher_single_edge_batch"
    assert report["selected_candidate_count"] == 2
    assert report["repair_report"]["changed_edge_count"] == 2
    assert report["promotion_gate"]["promotion_status"] == "pass"
    assert report["after_gate"]["road_layer_status"] == "pass"
    assert root.find("./edge[@id='fix_a']/lane").attrib["allow"] == "passenger"
    assert root.find("./edge[@id='fix_b']/lane").attrib["allow"] == "passenger"


def test_build_road_connection_topology_replay_audit_classifies_replayability(tmp_path: Path) -> None:
    teacher_net = tmp_path / "teacher.net.xml"
    candidate_net = tmp_path / "candidate.net.xml"
    teacher_net.write_text(
        """<net>
  <edge id="a" from="n1" to="n2"><lane id="a_0" index="0" allow="passenger"/></edge>
  <edge id="b" from="n2" to="n3"><lane id="b_0" index="0" allow="passenger"/></edge>
  <edge id="c" from="n2" to="n4"><lane id="c_0" index="0" allow="passenger"/></edge>
  <edge id="d" from="n2" to="n5"><lane id="d_0" index="0" allow="passenger"/></edge>
  <edge id=":n2_0" function="internal"><lane id=":n2_0_0" index="0"/></edge>
  <connection from="a" to="b" fromLane="0" toLane="0" dir="s"/>
  <connection from="a" to="c" fromLane="0" toLane="0" dir="r"/>
  <connection from="a" to="d" fromLane="0" toLane="0" via=":n2_0_0" dir="l"/>
</net>""",
        encoding="utf-8",
    )
    candidate_net.write_text(
        """<net>
  <edge id="a" from="n1" to="n2"><lane id="a_0" index="0" allow="passenger"/></edge>
  <edge id="b" from="n2" to="n3"><lane id="b_0" index="0" allow="passenger"/></edge>
  <edge id="d" from="n2" to="n5"><lane id="d_0" index="0" allow="passenger"/></edge>
</net>""",
        encoding="utf-8",
    )

    report = build_road_connection_topology_replay_audit(teacher_net, candidate_net)

    assert report["status"] == "pass"
    assert report["replayable_connection_count"] == 1
    assert report["blocked_connection_count"] == 2
    assert report["replayable_connections"] == [
        {"from": "a", "to": "b", "fromLane": "0", "toLane": "0", "dir": "s"}
    ]
    assert [item["blocking_reason"] for item in report["blocked_connections"]] == [
        "missing_to_edge",
        "missing_via_lane",
    ]


def test_build_road_connection_topology_replay_audit_ignores_existing_topology(tmp_path: Path) -> None:
    teacher_net = tmp_path / "teacher.net.xml"
    candidate_net = tmp_path / "candidate.net.xml"
    teacher_net.write_text(
        """<net>
  <edge id="a"><lane id="a_0" index="0"/></edge>
  <edge id="b"><lane id="b_0" index="0"/></edge>
  <connection from="a" to="b" fromLane="0" toLane="0" dir="s"/>
</net>""",
        encoding="utf-8",
    )
    candidate_net.write_text(
        """<net>
  <edge id="a"><lane id="a_0" index="0"/></edge>
  <edge id="b"><lane id="b_0" index="0"/></edge>
  <connection from="a" to="b" fromLane="0" toLane="0" dir="s"/>
</net>""",
        encoding="utf-8",
    )

    report = build_road_connection_topology_replay_audit(teacher_net, candidate_net)

    assert report["already_present_connection_count"] == 1
    assert report["replayable_connection_count"] == 0


def test_audit_road_connectivity_parity_writes_complete_gate_and_components(tmp_path: Path) -> None:
    teacher_net = tmp_path / "teacher.net.xml"
    candidate_net = tmp_path / "candidate.net.xml"
    network = """<net>
  <edge id="in" from="west" to="center"><lane id="in_0" index="0" allow="passenger" speed="13.9" shape="0,0 10,0"/></edge>
  <edge id="out" from="center" to="east"><lane id="out_0" index="0" allow="passenger" speed="13.9" shape="10,0 20,0"/></edge>
  <junction id="west" type="dead_end" x="0" y="0" incLanes="" intLanes=""/>
  <junction id="center" type="priority" x="10" y="0" incLanes="in_0" intLanes=""/>
  <junction id="east" type="dead_end" x="20" y="0" incLanes="out_0" intLanes=""/>
  <connection from="in" to="out" fromLane="0" toLane="0" dir="s"/>
</net>"""
    teacher_net.write_text(network, encoding="utf-8")
    candidate_net.write_text(network.replace('allow="passenger"', 'allow="bus"'), encoding="utf-8")

    report = audit_road_connectivity_parity(
        teacher_net,
        candidate_net,
        output_dir=tmp_path / "audit",
        prefix="road",
    )

    assert report["status"] == "fail"
    assert report["connection_topology_gate"]["status"] == "pass"
    assert report["road_template_gate"]["road_layer_status"] == "fail"
    assert Path(report["report_file"]).exists()
    assert Path(report["road_template_report_file"]).exists()
    assert Path(report["connection_topology_report_file"]).exists()
    assert Path(report["internal_movement_report_file"]).exists()


def test_build_internal_movement_replay_audit_groups_missing_movements_by_owner(
    tmp_path: Path,
) -> None:
    teacher_net = tmp_path / "teacher.net.xml"
    candidate_net = tmp_path / "candidate.net.xml"
    teacher_net.write_text(
        """<net>
  <edge id="in" from="a" to="j"><lane id="in_0" index="0"/></edge>
  <edge id="out" from="j" to="b"><lane id="out_0" index="0"/></edge>
  <edge id="back" from="j" to="a"><lane id="back_0" index="0"/></edge>
  <edge id=":j_0" function="internal"><lane id=":j_0_0" index="0"/></edge>
  <edge id=":j_1" function="internal"><lane id=":j_1_0" index="0"/></edge>
  <connection from="in" to="out" fromLane="0" toLane="0" via=":j_0_0" tl="j" linkIndex="0" dir="s"/>
  <connection from=":j_0" to="out" fromLane="0" toLane="0" dir="s"/>
  <connection from="in" to="back" fromLane="0" toLane="0" via=":j_1_0" tl="j" linkIndex="1" dir="t"/>
</net>""",
        encoding="utf-8",
    )
    candidate_net.write_text(
        """<net>
  <edge id="in" from="a" to="j"><lane id="in_0" index="0"/></edge>
  <edge id="out" from="j" to="b"><lane id="out_0" index="0"/></edge>
  <edge id="back" from="j" to="a"><lane id="back_0" index="0"/></edge>
  <edge id=":j_0" function="internal"><lane id=":j_0_0" index="0"/></edge>
  <edge id=":j_1" function="internal"><lane id=":j_1_0" index="0"/></edge>
</net>""",
        encoding="utf-8",
    )

    report = build_internal_movement_replay_audit(teacher_net, candidate_net)

    assert report["status"] == "pass"
    assert report["internal_movement_missing_count"] == 3
    assert report["tls_controlled_missing_count"] == 2
    assert report["turnaround_missing_count"] == 1
    assert report["non_turnaround_missing_count"] == 2
    assert report["owner_count"] == 1
    owner = report["owners"][0]
    assert owner["owner_id"] == "j"
    assert owner["missing_connection_count"] == 3
    assert owner["internal_via_connection_count"] == 2
    assert owner["internal_edge_connection_count"] == 1
    assert owner["tls_controlled_connection_count"] == 2
    assert owner["turnaround_connection_count"] == 1
    assert owner["non_turnaround_connection_count"] == 2
    assert owner["dir_counts"] == {"s": 2, "t": 1}
    assert owner["example_connections"][0]["tl"] == "j"
    assert owner["example_connections"][0]["linkIndex"] == "0"


def test_write_road_connection_topology_replay_candidate_adds_safe_missing_connections(
    tmp_path: Path,
) -> None:
    teacher_net = tmp_path / "teacher.net.xml"
    candidate_net = tmp_path / "candidate.net.xml"
    output_net = tmp_path / "candidate_replayed.net.xml"
    teacher_net.write_text(
        """<net>
  <edge id="a"><lane id="a_0" index="0"/></edge>
  <edge id="b"><lane id="b_0" index="0"/></edge>
  <connection from="a" to="b" fromLane="0" toLane="0" dir="s" state="M" tl="J" linkIndex="0"/>
</net>""",
        encoding="utf-8",
    )
    candidate_net.write_text(
        """<net>
  <edge id="a"><lane id="a_0" index="0"/></edge>
  <edge id="b"><lane id="b_0" index="0"/></edge>
</net>""",
        encoding="utf-8",
    )

    report = write_road_connection_topology_replay_candidate(
        teacher_net,
        candidate_net,
        output_net,
    )

    root = ET.parse(output_net).getroot()
    connection = root.find("connection")
    assert report["status"] == "pass"
    assert report["repair_scope"] == "road_connection_topology"
    assert report["added_connection_count"] == 1
    assert connection is not None
    assert connection.attrib == {
        "from": "a",
        "to": "b",
        "fromLane": "0",
        "toLane": "0",
        "dir": "s",
        "state": "M",
    }


def test_road_connection_topology_replay_does_not_treat_turnaround_as_normal_movement(
    tmp_path: Path,
) -> None:
    teacher_net = tmp_path / "teacher.net.xml"
    candidate_net = tmp_path / "candidate.net.xml"
    output_net = tmp_path / "candidate_replayed.net.xml"
    teacher_net.write_text(
        """<net>
  <edge id="out" from="j" to="b"><lane id="out_0" index="0" allow="passenger"/></edge>
  <edge id="-out" from="b" to="j"><lane id="-out_0" index="0" allow="passenger"/></edge>
  <edge id="next" from="b" to="c"><lane id="next_0" index="0" allow="passenger"/></edge>
  <connection from="out" to="-out" fromLane="0" toLane="0" dir="t"/>
  <connection from="out" to="next" fromLane="0" toLane="0" dir="s"/>
</net>""",
        encoding="utf-8",
    )
    candidate_net.write_text(
        """<net>
  <edge id="out" from="j" to="b"><lane id="out_0" index="0" allow="passenger"/></edge>
  <edge id="-out" from="b" to="j"><lane id="-out_0" index="0" allow="passenger"/></edge>
  <edge id="next" from="b" to="c"><lane id="next_0" index="0" allow="passenger"/></edge>
  <connection from="out" to="-out" fromLane="0" toLane="0" dir="t"/>
</net>""",
        encoding="utf-8",
    )

    audit = build_road_connection_topology_replay_audit(teacher_net, candidate_net)
    report = write_road_connection_topology_replay_candidate(
        teacher_net,
        candidate_net,
        output_net,
    )

    root = ET.parse(output_net).getroot()
    assert audit["already_present_connection_count"] == 1
    assert audit["replayable_connection_count"] == 1
    assert audit["replayable_connections"][0]["dir"] == "s"
    assert report["added_connection_count"] == 1
    assert root.find("connection[@from='out'][@to='-out'][@dir='t']") is not None
    assert root.find("connection[@from='out'][@to='next'][@dir='s']") is not None


def test_write_internal_movement_owner_replay_candidate_adds_only_target_owner(
    tmp_path: Path,
) -> None:
    teacher_net = tmp_path / "teacher.net.xml"
    candidate_net = tmp_path / "candidate.net.xml"
    output_net = tmp_path / "candidate_replayed.net.xml"
    teacher_net.write_text(
        """<net>
  <edge id="in" from="a" to="j"><lane id="in_0" index="0"/></edge>
  <edge id="out" from="j" to="b"><lane id="out_0" index="0"/></edge>
  <edge id="other_in" from="c" to="k"><lane id="other_in_0" index="0"/></edge>
  <edge id="other_out" from="k" to="d"><lane id="other_out_0" index="0"/></edge>
  <edge id=":j_0" function="internal"><lane id=":j_0_0" index="0"/></edge>
  <edge id=":k_0" function="internal"><lane id=":k_0_0" index="0"/></edge>
  <connection from="in" to="out" fromLane="0" toLane="0" via=":j_0_0" tl="j" linkIndex="0" dir="s" state="M"/>
  <connection from=":j_0" to="out" fromLane="0" toLane="0" dir="s"/>
  <connection from="other_in" to="other_out" fromLane="0" toLane="0" via=":k_0_0" tl="k" linkIndex="0" dir="s"/>
</net>""",
        encoding="utf-8",
    )
    candidate_net.write_text(
        """<net>
  <edge id="in" from="a" to="j"><lane id="in_0" index="0"/></edge>
  <edge id="out" from="j" to="b"><lane id="out_0" index="0"/></edge>
  <edge id="other_in" from="c" to="k"><lane id="other_in_0" index="0"/></edge>
  <edge id="other_out" from="k" to="d"><lane id="other_out_0" index="0"/></edge>
  <edge id=":j_0" function="internal"><lane id=":j_0_0" index="0"/></edge>
  <edge id=":k_0" function="internal"><lane id=":k_0_0" index="0"/></edge>
</net>""",
        encoding="utf-8",
    )

    report = write_internal_movement_owner_replay_candidate(
        teacher_net,
        candidate_net,
        output_net,
        owner_id="j",
    )

    connections = [connection.attrib for connection in ET.parse(output_net).getroot().findall("connection")]
    assert report["status"] == "pass"
    assert report["repair_scope"] == "internal_movement_owner"
    assert report["owner_id"] == "j"
    assert report["added_connection_count"] == 2
    assert connections == [
        {
            "from": "in",
            "to": "out",
            "fromLane": "0",
            "toLane": "0",
            "via": ":j_0_0",
            "tl": "j",
            "linkIndex": "0",
            "dir": "s",
            "state": "M",
        },
        {"from": ":j_0", "to": "out", "fromLane": "0", "toLane": "0", "dir": "s"},
    ]


def test_write_internal_movement_owner_replay_candidate_can_strip_tls_attrs(
    tmp_path: Path,
) -> None:
    teacher_net = tmp_path / "teacher.net.xml"
    candidate_net = tmp_path / "candidate.net.xml"
    output_net = tmp_path / "candidate_replayed.net.xml"
    teacher_net.write_text(
        """<net>
  <edge id="in" from="a" to="j"><lane id="in_0" index="0"/></edge>
  <edge id="out" from="j" to="b"><lane id="out_0" index="0"/></edge>
  <edge id=":j_0" function="internal"><lane id=":j_0_0" index="0"/></edge>
  <connection from="in" to="out" fromLane="0" toLane="0" via=":j_0_0" tl="j" linkIndex="0" linkIndex2="4" dir="s"/>
</net>""",
        encoding="utf-8",
    )
    candidate_net.write_text(
        """<net>
  <edge id="in" from="a" to="j"><lane id="in_0" index="0"/></edge>
  <edge id="out" from="j" to="b"><lane id="out_0" index="0"/></edge>
  <edge id=":j_0" function="internal"><lane id=":j_0_0" index="0"/></edge>
</net>""",
        encoding="utf-8",
    )

    report = write_internal_movement_owner_replay_candidate(
        teacher_net,
        candidate_net,
        output_net,
        owner_id="j",
        copy_tls=False,
    )

    connection = ET.parse(output_net).getroot().find("connection")
    assert report["added_connection_count"] == 1
    assert connection is not None
    assert connection.attrib == {
        "from": "in",
        "to": "out",
        "fromLane": "0",
        "toLane": "0",
        "via": ":j_0_0",
        "dir": "s",
    }


def test_write_internal_movement_owner_replay_candidate_skips_nonlocal_candidate_edges(
    tmp_path: Path,
) -> None:
    teacher_net = tmp_path / "teacher.net.xml"
    candidate_net = tmp_path / "candidate.net.xml"
    output_net = tmp_path / "candidate_replayed.net.xml"
    teacher_net.write_text(
        """<net>
  <edge id="in" from="a" to="j"><lane id="in_0" index="0"/></edge>
  <edge id="out" from="j" to="b"><lane id="out_0" index="0"/></edge>
  <edge id=":j_0" function="internal"><lane id=":j_0_0" index="0"/></edge>
  <connection from="in" to="out" fromLane="0" toLane="0" via=":j_0_0" dir="s"/>
</net>""",
        encoding="utf-8",
    )
    candidate_net.write_text(
        """<net>
  <edge id="in" from="a" to="not_j"><lane id="in_0" index="0"/></edge>
  <edge id="out" from="not_j" to="b"><lane id="out_0" index="0"/></edge>
  <edge id=":j_0" function="internal"><lane id=":j_0_0" index="0"/></edge>
</net>""",
        encoding="utf-8",
    )

    report = write_internal_movement_owner_replay_candidate(
        teacher_net,
        candidate_net,
        output_net,
        owner_id="j",
    )

    assert report["added_connection_count"] == 0
    assert report["skipped_nonlocal_edge_connection_count"] == 1
    assert ET.parse(output_net).getroot().find("connection") is None


def test_build_internal_movement_owner_approach_edge_map_matches_split_roots(
    tmp_path: Path,
) -> None:
    teacher_net = tmp_path / "teacher.net.xml"
    candidate_net = tmp_path / "candidate.net.xml"
    teacher_net.write_text(
        """<net>
  <edge id="road#3" from="j" to="b"><lane id="road#3_0" index="0"/></edge>
  <edge id="-road#2" from="a" to="j"><lane id="-road#2_0" index="0"/></edge>
  <edge id="other#0" from="j" to="c"><lane id="other#0_0" index="0"/></edge>
</net>""",
        encoding="utf-8",
    )
    candidate_net.write_text(
        """<net>
  <edge id="road#5" from="j" to="b2"><lane id="road#5_0" index="0"/></edge>
  <edge id="-road#4" from="a2" to="j"><lane id="-road#4_0" index="0"/></edge>
  <edge id="other#0" from="not_j" to="c"><lane id="other#0_0" index="0"/></edge>
</net>""",
        encoding="utf-8",
    )

    report = build_internal_movement_owner_approach_edge_map(
        teacher_net,
        candidate_net,
        owner_id="j",
    )

    assert report["status"] == "pass"
    assert report["mapped_edge_count"] == 2
    assert report["edge_map"] == {"-road#2": "-road#4", "road#3": "road#5"}
    assert report["unmapped_teacher_edges"] == ["other#0"]


def test_build_internal_movement_owner_approach_edge_map_prefers_matching_terminal_endpoint(
    tmp_path: Path,
) -> None:
    teacher_net = tmp_path / "teacher.net.xml"
    candidate_net = tmp_path / "candidate.net.xml"
    teacher_net.write_text(
        """<net>
  <edge id="road#3" from="j" to="b"><lane id="road#3_0" index="0"/></edge>
  <edge id="-road#3" from="b" to="j"><lane id="-road#3_0" index="0"/></edge>
</net>""",
        encoding="utf-8",
    )
    candidate_net.write_text(
        """<net>
  <edge id="road#3" from="j" to="b"><lane id="road#3_0" index="0"/></edge>
  <edge id="road#5" from="j" to="c"><lane id="road#5_0" index="0"/></edge>
  <edge id="-road#3" from="b" to="j"><lane id="-road#3_0" index="0"/></edge>
  <edge id="-road#5" from="c" to="j"><lane id="-road#5_0" index="0"/></edge>
</net>""",
        encoding="utf-8",
    )

    report = build_internal_movement_owner_approach_edge_map(
        teacher_net,
        candidate_net,
        owner_id="j",
    )

    assert report["edge_map"] == {"-road#3": "-road#3", "road#3": "road#3"}
    assert report["ambiguous_teacher_edges"] == []


def test_build_internal_movement_owner_approach_edge_map_does_not_reuse_candidate_split_edge(
    tmp_path: Path,
) -> None:
    teacher_net = tmp_path / "teacher.net.xml"
    candidate_net = tmp_path / "candidate.net.xml"
    teacher_net.write_text(
        """<net>
  <edge id="road#4" from="j" to="a"><lane id="road#4_0" index="0"/></edge>
  <edge id="road#5" from="j" to="b"><lane id="road#5_0" index="0"/></edge>
</net>""",
        encoding="utf-8",
    )
    candidate_net.write_text(
        """<net>
  <edge id="road#4" from="j" to="c"><lane id="road#4_0" index="0"/></edge>
</net>""",
        encoding="utf-8",
    )

    report = build_internal_movement_owner_approach_edge_map(
        teacher_net,
        candidate_net,
        owner_id="j",
    )

    assert report["edge_map"] == {"road#4": "road#4"}
    assert report["unmapped_teacher_edges"] == ["road#5"]
    assert report["candidate_edge_collisions"] == [
        {
            "teacher_edge_id": "road#5",
            "candidate_edge_ids": ["road#4"],
        }
    ]


def test_build_internal_movement_owner_approach_edge_map_reserves_mismatched_exact_id(
    tmp_path: Path,
) -> None:
    teacher_net = tmp_path / "teacher.net.xml"
    candidate_net = tmp_path / "candidate.net.xml"
    teacher_net.write_text(
        """<net>
  <edge id="road#4" from="a" to="j"><lane id="road#4_0" index="0"/></edge>
  <edge id="road#5" from="j" to="b"><lane id="road#5_0" index="0"/></edge>
</net>""",
        encoding="utf-8",
    )
    candidate_net.write_text(
        """<net>
  <edge id="road#4" from="j" to="c"><lane id="road#4_0" index="0"/></edge>
</net>""",
        encoding="utf-8",
    )

    report = build_internal_movement_owner_approach_edge_map(
        teacher_net,
        candidate_net,
        owner_id="j",
    )

    assert report["edge_map"] == {}
    assert report["reserved_mismatched_exact_edges"] == ["road#4"]
    assert report["unmapped_teacher_edges"] == ["road#4", "road#5"]
    assert report["candidate_edge_collisions"] == [
        {
            "teacher_edge_id": "road#5",
            "candidate_edge_ids": ["road#4"],
        }
    ]


def test_build_internal_movement_owner_approach_edge_map_matches_torii_osm_way_prefixes(
    tmp_path: Path,
) -> None:
    teacher = tmp_path / "teacher.net.xml"
    candidate = tmp_path / "candidate.net.xml"
    teacher.write_text(
        """<net>
  <edge id="-24693977#0" from="a" to="j"><lane id="-24693977#0_0" index="0"/></edge>
  <edge id="24693977#0" from="j" to="a"><lane id="24693977#0_0" index="0"/></edge>
  <junction id="a" type="priority"/>
  <junction id="j" type="traffic_light"/>
</net>""",
        encoding="utf-8",
    )
    candidate.write_text(
        """<net>
  <edge id="24693977_10176312934_to_core_1833941950" from="a" to="core"><lane id="24693977_10176312934_to_core_1833941950_0" index="0"/></edge>
  <edge id="24693977_core_1833941950_to_10176312934" from="core" to="a"><lane id="24693977_core_1833941950_to_10176312934_0" index="0"/></edge>
  <junction id="a" type="priority"/>
  <junction id="core" type="traffic_light"/>
</net>""",
        encoding="utf-8",
    )

    report = build_internal_movement_owner_approach_edge_map(
        teacher,
        candidate,
        owner_id="j",
        candidate_owner_id="core",
    )

    assert report["edge_map"] == {
        "-24693977#0": "24693977_10176312934_to_core_1833941950",
        "24693977#0": "24693977_core_1833941950_to_10176312934",
    }
    assert report["unmapped_teacher_edges"] == []


def test_write_internal_movement_owner_replay_candidate_applies_teacher_edge_map(
    tmp_path: Path,
) -> None:
    teacher_net = tmp_path / "teacher.net.xml"
    candidate_net = tmp_path / "candidate.net.xml"
    output_net = tmp_path / "candidate_replayed.net.xml"
    teacher_net.write_text(
        """<net>
  <edge id="road#3" from="a" to="j"><lane id="road#3_0" index="0"/></edge>
  <edge id="out#2" from="j" to="b"><lane id="out#2_0" index="0"/></edge>
  <edge id=":j_0" function="internal"><lane id=":j_0_0" index="0"/></edge>
  <connection from="road#3" to="out#2" fromLane="0" toLane="0" via=":j_0_0" dir="s"/>
</net>""",
        encoding="utf-8",
    )
    candidate_net.write_text(
        """<net>
  <edge id="road#5" from="a2" to="j"><lane id="road#5_0" index="0"/></edge>
  <edge id="out#4" from="j" to="b2"><lane id="out#4_0" index="0"/></edge>
  <edge id=":j_0" function="internal"><lane id=":j_0_0" index="0"/></edge>
</net>""",
        encoding="utf-8",
    )

    report = write_internal_movement_owner_replay_candidate(
        teacher_net,
        candidate_net,
        output_net,
        owner_id="j",
        teacher_edge_map={"road#3": "road#5", "out#2": "out#4"},
    )

    connection = ET.parse(output_net).getroot().find("connection")
    assert report["added_connection_count"] == 1
    assert report["mapped_external_edge_count"] == 2
    assert connection is not None
    assert connection.attrib["from"] == "road#5"
    assert connection.attrib["to"] == "out#4"


def test_build_internal_movement_owner_internal_lane_map_matches_mapped_movements(
    tmp_path: Path,
) -> None:
    teacher_net = tmp_path / "teacher.net.xml"
    candidate_net = tmp_path / "candidate.net.xml"
    teacher_net.write_text(
        """<net>
  <edge id="road#3" from="a" to="j"><lane id="road#3_0" index="0"/></edge>
  <edge id="out#2" from="j" to="b"><lane id="out#2_0" index="0"/></edge>
  <edge id=":j_7" function="internal"><lane id=":j_7_0" index="0"/></edge>
  <connection from="road#3" to="out#2" fromLane="0" toLane="0" via=":j_7_0" dir="s"/>
</net>""",
        encoding="utf-8",
    )
    candidate_net.write_text(
        """<net>
  <edge id="road#5" from="a2" to="j"><lane id="road#5_0" index="0"/></edge>
  <edge id="out#4" from="j" to="b2"><lane id="out#4_0" index="0"/></edge>
  <edge id=":j_11" function="internal"><lane id=":j_11_0" index="0"/></edge>
  <connection from="road#5" to="out#4" fromLane="0" toLane="0" via=":j_11_0" dir="s"/>
</net>""",
        encoding="utf-8",
    )

    report = build_internal_movement_owner_internal_lane_map(
        teacher_net,
        candidate_net,
        owner_id="j",
        teacher_edge_map={"road#3": "road#5", "out#2": "out#4"},
    )

    assert report["status"] == "pass"
    assert report["mapped_internal_lane_count"] == 1
    assert report["internal_lane_map"] == {":j_7_0": ":j_11_0"}


def test_write_internal_movement_owner_replay_candidate_applies_internal_lane_map(
    tmp_path: Path,
) -> None:
    teacher_net = tmp_path / "teacher.net.xml"
    candidate_net = tmp_path / "candidate.net.xml"
    output_net = tmp_path / "candidate_replayed.net.xml"
    teacher_net.write_text(
        """<net>
  <edge id="road#3" from="a" to="j"><lane id="road#3_0" index="0"/></edge>
  <edge id="out#2" from="j" to="b"><lane id="out#2_0" index="0"/></edge>
  <edge id=":j_7" function="internal"><lane id=":j_7_0" index="0"/></edge>
  <connection from="road#3" to="out#2" fromLane="0" toLane="0" via=":j_7_0" dir="s"/>
</net>""",
        encoding="utf-8",
    )
    candidate_net.write_text(
        """<net>
  <edge id="road#5" from="a2" to="j"><lane id="road#5_0" index="0"/></edge>
  <edge id="out#4" from="j" to="b2"><lane id="out#4_0" index="0"/></edge>
  <edge id=":j_11" function="internal"><lane id=":j_11_0" index="0"/></edge>
</net>""",
        encoding="utf-8",
    )

    report = write_internal_movement_owner_replay_candidate(
        teacher_net,
        candidate_net,
        output_net,
        owner_id="j",
        teacher_edge_map={"road#3": "road#5", "out#2": "out#4"},
        teacher_internal_lane_map={":j_7_0": ":j_11_0"},
    )

    connection = ET.parse(output_net).getroot().find("connection")
    assert report["added_connection_count"] == 1
    assert report["mapped_internal_lane_count"] == 1
    assert connection is not None
    assert connection.attrib["via"] == ":j_11_0"


def test_write_internal_movement_owner_bundle_replacement_candidate_replaces_owner_bundle(
    tmp_path: Path,
) -> None:
    teacher_net = tmp_path / "teacher.net.xml"
    candidate_net = tmp_path / "candidate.net.xml"
    output_net = tmp_path / "candidate_bundle.net.xml"
    teacher_net.write_text(
        """<net>
  <edge id="road#3" from="a" to="j"><lane id="road#3_0" index="0"/></edge>
  <edge id="out#2" from="j" to="b"><lane id="out#2_0" index="0"/></edge>
  <edge id=":j_0" function="internal"><lane id=":j_0_0" index="0" shape="0,0 1,0"/></edge>
  <junction id="j" type="priority" x="0" y="0" incLanes="road#3_0" intLanes=":j_0_0">
    <request index="0" response="0" foes="0" cont="0"/>
  </junction>
  <junction id=":j_0_0" type="internal" x="0" y="0" incLanes="road#3_0" intLanes=":j_0_0"/>
  <connection from="road#3" to="out#2" fromLane="0" toLane="0" via=":j_0_0" dir="s"/>
  <connection from=":j_0" to="out#2" fromLane="0" toLane="0" dir="s"/>
</net>""",
        encoding="utf-8",
    )
    candidate_net.write_text(
        """<net>
  <edge id="road#5" from="a2" to="j"><lane id="road#5_0" index="0"/></edge>
  <edge id="out#4" from="j" to="b2"><lane id="out#4_0" index="0"/></edge>
  <edge id=":j_old" function="internal"><lane id=":j_old_0" index="0"/></edge>
  <junction id="j" type="priority" x="0" y="0" incLanes="road#5_0" intLanes=":j_old_0"/>
  <connection from="road#5" to="out#4" fromLane="0" toLane="0" via=":j_old_0" dir="s"/>
  <connection from=":j_old" to="out#4" fromLane="0" toLane="0" dir="s"/>
</net>""",
        encoding="utf-8",
    )

    report = write_internal_movement_owner_bundle_replacement_candidate(
        teacher_net,
        candidate_net,
        output_net,
        owner_id="j",
        teacher_edge_map={"road#3": "road#5", "out#2": "out#4"},
        copy_tls=False,
    )

    root = ET.parse(output_net).getroot()
    connections = [connection.attrib for connection in root.findall("connection")]
    assert report["status"] == "pass"
    assert report["repair_scope"] == "internal_movement_owner_bundle"
    assert report["removed_internal_edge_count"] == 1
    assert report["added_internal_edge_count"] == 1
    assert report["added_connection_count"] == 2
    assert root.find("edge[@id=':j_old']") is None
    assert root.find("edge[@id=':j_0']") is not None
    assert root.find("junction[@id='j']").attrib["incLanes"] == "road#5_0"
    assert root.find("junction[@id='j']").attrib["intLanes"] == ":j_0_0"
    assert root.find("junction[@id='j']/request").attrib["index"] == "0"
    assert root.find("junction[@id=':j_0_0']").attrib["incLanes"] == "road#5_0"
    assert connections == [
        {"from": "road#5", "to": "out#4", "fromLane": "0", "toLane": "0", "via": ":j_0_0", "dir": "s"},
        {"from": ":j_0", "to": "out#4", "fromLane": "0", "toLane": "0", "dir": "s"},
    ]


def test_write_internal_movement_owner_bundle_replacement_candidate_copies_owner_tllogic(
    tmp_path: Path,
) -> None:
    teacher_net = tmp_path / "teacher.net.xml"
    candidate_net = tmp_path / "candidate.net.xml"
    output_net = tmp_path / "candidate_bundle.net.xml"
    teacher_net.write_text(
        """<net>
  <edge id="road#3" from="a" to="j"><lane id="road#3_0" index="0"/></edge>
  <edge id="out#2" from="j" to="b"><lane id="out#2_0" index="0"/></edge>
  <edge id=":j_0" function="internal"><lane id=":j_0_0" index="0"/></edge>
  <tlLogic id="j" type="actuated" programID="0" offset="0">
    <phase duration="30" state="G"/>
  </tlLogic>
  <junction id="j" type="traffic_light" tl="j" x="0" y="0" incLanes="road#3_0" intLanes=":j_0_0"/>
  <connection from="road#3" to="out#2" fromLane="0" toLane="0" via=":j_0_0" tl="j" linkIndex="0" dir="s"/>
</net>""",
        encoding="utf-8",
    )
    candidate_net.write_text(
        """<net>
  <edge id="road#5" from="a2" to="j"><lane id="road#5_0" index="0"/></edge>
  <edge id="out#4" from="j" to="b2"><lane id="out#4_0" index="0"/></edge>
  <junction id="j" type="priority" x="0" y="0"/>
</net>""",
        encoding="utf-8",
    )

    report = write_internal_movement_owner_bundle_replacement_candidate(
        teacher_net,
        candidate_net,
        output_net,
        owner_id="j",
        teacher_edge_map={"road#3": "road#5", "out#2": "out#4"},
        copy_tls=True,
    )

    root = ET.parse(output_net).getroot()
    children = list(root)
    target_tls = root.find("tlLogic[@id='j']")
    connection = root.find("connection[@tl='j']")
    assert report["status"] == "pass"
    assert report["copied_tl_logic_count"] == 1
    assert root.find("junction[@id='j']").attrib["type"] == "traffic_light"
    assert target_tls is not None
    assert target_tls.find("phase").attrib["state"] == "G"
    assert connection is not None
    assert connection.attrib["linkIndex"] == "0"
    assert children.index(target_tls) < children.index(connection)


def test_write_internal_movement_owner_bundle_replacement_candidate_blocks_missing_road_dependencies(
    tmp_path: Path,
) -> None:
    teacher_net = tmp_path / "teacher.net.xml"
    candidate_net = tmp_path / "candidate.net.xml"
    output_net = tmp_path / "candidate_bundle.net.xml"
    teacher_net.write_text(
        """<net>
  <edge id="road#3" from="a" to="j"><lane id="road#3_0" index="0"/></edge>
  <edge id="out#2" from="j" to="b"><lane id="out#2_0" index="0"/></edge>
  <edge id=":j_0" function="internal"><lane id=":j_0_0" index="0"/></edge>
  <junction id="j" type="priority" x="0" y="0" incLanes="road#3_0" intLanes=":j_0_0"/>
  <connection from="road#3" to="out#2" fromLane="0" toLane="0" via=":j_0_0" dir="s"/>
</net>""",
        encoding="utf-8",
    )
    candidate_net.write_text(
        """<net>
  <edge id="out#4" from="j" to="b2"><lane id="out#4_0" index="0"/></edge>
  <junction id="j" type="priority" x="0" y="0"/>
</net>""",
        encoding="utf-8",
    )

    report = write_internal_movement_owner_bundle_replacement_candidate(
        teacher_net,
        candidate_net,
        output_net,
        owner_id="j",
        teacher_edge_map={"road#3": "road#5", "out#2": "out#4"},
        copy_tls=False,
    )

    assert report["status"] == "blocked"
    assert report["blocking_reason"] == "missing_road_dependencies"
    assert report["blocked_dependency_count"] == 2
    assert report["blocked_dependencies"] == [
        {
            "kind": "missing_junction_lane",
            "junction": "j",
            "field": "incLanes",
            "lane": "road#5_0",
        },
        {
            "kind": "missing_connection_dependency",
            "reason": "missing_from_edge",
            "from": "road#5",
            "to": "out#4",
            "via": ":j_0_0",
            "teacher_from": "road#3",
            "teacher_to": "out#2",
        },
    ]
    assert not output_net.exists()


def test_write_internal_movement_owner_bundle_replacement_candidate_allows_cluster_internal_lanes(
    tmp_path: Path,
) -> None:
    teacher_net = tmp_path / "teacher.net.xml"
    candidate_net = tmp_path / "candidate.net.xml"
    output_net = tmp_path / "candidate_bundle.net.xml"
    teacher_net.write_text(
        """<net>
  <edge id="in" from="a" to="cluster_a_b"><lane id="in_0" index="0"/></edge>
  <edge id="out" from="cluster_a_b" to="b"><lane id="out_0" index="0"/></edge>
  <edge id=":cluster_a_b_0" function="internal"><lane id=":cluster_a_b_0_0" index="0"/></edge>
  <junction id="cluster_a_b" type="priority" x="0" y="0" incLanes="in_0" intLanes=":cluster_a_b_0_0"/>
  <connection from="in" to="out" fromLane="0" toLane="0" via=":cluster_a_b_0_0" dir="s"/>
  <connection from=":cluster_a_b_0" to="out" fromLane="0" toLane="0" dir="s"/>
</net>""",
        encoding="utf-8",
    )
    candidate_net.write_text(
        """<net>
  <edge id="in" from="a" to="cluster_a_b"><lane id="in_0" index="0"/></edge>
  <edge id="out" from="cluster_a_b" to="b"><lane id="out_0" index="0"/></edge>
  <junction id="cluster_a_b" type="priority" x="0" y="0"/>
</net>""",
        encoding="utf-8",
    )

    report = write_internal_movement_owner_bundle_replacement_candidate(
        teacher_net,
        candidate_net,
        output_net,
        owner_id="cluster_a_b",
        teacher_edge_map={"in": "in", "out": "out"},
        copy_tls=False,
    )

    root = ET.parse(output_net).getroot()
    assert report["status"] == "pass"
    assert report["added_internal_edge_count"] == 1
    assert root.find("edge[@id=':cluster_a_b_0']") is not None
    assert root.find("connection[@from='in'][@to='out']") is not None


def test_write_road_connection_topology_replay_candidate_skips_internal_via_by_default(
    tmp_path: Path,
) -> None:
    teacher_net = tmp_path / "teacher.net.xml"
    candidate_net = tmp_path / "candidate.net.xml"
    output_net = tmp_path / "candidate_replayed.net.xml"
    teacher_net.write_text(
        """<net>
  <edge id="a"><lane id="a_0" index="0"/></edge>
  <edge id="b"><lane id="b_0" index="0"/></edge>
  <edge id=":j_0" function="internal"><lane id=":j_0_0" index="0"/></edge>
  <connection from="a" to="b" fromLane="0" toLane="0" via=":j_0_0" dir="s"/>
</net>""",
        encoding="utf-8",
    )
    candidate_net.write_text(
        """<net>
  <edge id="a"><lane id="a_0" index="0"/></edge>
  <edge id="b"><lane id="b_0" index="0"/></edge>
  <edge id=":j_0" function="internal"><lane id=":j_0_0" index="0"/></edge>
</net>""",
        encoding="utf-8",
    )

    report = write_road_connection_topology_replay_candidate(
        teacher_net,
        candidate_net,
        output_net,
    )

    assert report["added_connection_count"] == 0
    assert report["skipped_internal_via_connection_count"] == 1
    assert ET.parse(output_net).getroot().find("connection") is None


def test_write_road_connection_topology_replay_candidate_collects_road_only_limit(
    tmp_path: Path,
) -> None:
    teacher_net = tmp_path / "teacher.net.xml"
    candidate_net = tmp_path / "candidate.net.xml"
    output_net = tmp_path / "candidate_replayed.net.xml"
    teacher_net.write_text(
        """<net>
  <edge id="a"><lane id="a_0" index="0"/></edge>
  <edge id="b"><lane id="b_0" index="0"/></edge>
  <edge id="c"><lane id="c_0" index="0"/></edge>
  <edge id="d"><lane id="d_0" index="0"/></edge>
  <edge id=":j_0" function="internal"><lane id=":j_0_0" index="0"/></edge>
  <connection from="a" to="b" fromLane="0" toLane="0" via=":j_0_0" dir="s"/>
  <connection from="c" to="d" fromLane="0" toLane="0" dir="s"/>
</net>""",
        encoding="utf-8",
    )
    candidate_net.write_text(
        """<net>
  <edge id="a"><lane id="a_0" index="0"/></edge>
  <edge id="b"><lane id="b_0" index="0"/></edge>
  <edge id="c"><lane id="c_0" index="0"/></edge>
  <edge id="d"><lane id="d_0" index="0"/></edge>
  <edge id=":j_0" function="internal"><lane id=":j_0_0" index="0"/></edge>
</net>""",
        encoding="utf-8",
    )

    report = write_road_connection_topology_replay_candidate(
        teacher_net,
        candidate_net,
        output_net,
        max_connections=1,
    )

    connection = ET.parse(output_net).getroot().find("connection")
    assert report["added_connection_count"] == 1
    assert report["skipped_internal_via_connection_count"] == 1
    assert connection is not None
    assert connection.attrib["from"] == "c"
    assert connection.attrib["to"] == "d"


def test_write_road_connection_topology_replay_candidate_skips_candidate_internal_junctions(
    tmp_path: Path,
) -> None:
    teacher_net = tmp_path / "teacher.net.xml"
    candidate_net = tmp_path / "candidate.net.xml"
    output_net = tmp_path / "candidate_replayed.net.xml"
    teacher_net.write_text(
        """<net>
  <edge id="a" from="n1" to="j"><lane id="a_0" index="0"/></edge>
  <edge id="b" from="j" to="n2"><lane id="b_0" index="0"/></edge>
  <connection from="a" to="b" fromLane="0" toLane="0" dir="s"/>
</net>""",
        encoding="utf-8",
    )
    candidate_net.write_text(
        """<net>
  <edge id="a" from="n1" to="j"><lane id="a_0" index="0"/></edge>
  <edge id="b" from="j" to="n2"><lane id="b_0" index="0"/></edge>
  <edge id=":j_0" function="internal"><lane id=":j_0_0" index="0"/></edge>
  <junction id="j" type="traffic_light" x="0" y="0" incLanes="a_0" intLanes=":j_0_0"/>
</net>""",
        encoding="utf-8",
    )

    report = write_road_connection_topology_replay_candidate(
        teacher_net,
        candidate_net,
        output_net,
    )

    assert report["added_connection_count"] == 0
    assert report["skipped_candidate_internal_junction_connection_count"] == 1
    assert ET.parse(output_net).getroot().find("connection") is None


def test_write_road_connection_topology_replay_candidate_skips_internal_edges(
    tmp_path: Path,
) -> None:
    teacher_net = tmp_path / "teacher.net.xml"
    candidate_net = tmp_path / "candidate.net.xml"
    output_net = tmp_path / "candidate_replayed.net.xml"
    teacher_net.write_text(
        """<net>
  <edge id=":j_0" function="internal"><lane id=":j_0_0" index="0"/></edge>
  <edge id="b" from="j" to="n2"><lane id="b_0" index="0"/></edge>
  <connection from=":j_0" to="b" fromLane="0" toLane="0" dir="s"/>
</net>""",
        encoding="utf-8",
    )
    candidate_net.write_text(
        """<net>
  <edge id=":j_0" function="internal"><lane id=":j_0_0" index="0"/></edge>
  <edge id="b" from="j" to="n2"><lane id="b_0" index="0"/></edge>
</net>""",
        encoding="utf-8",
    )

    report = write_road_connection_topology_replay_candidate(
        teacher_net,
        candidate_net,
        output_net,
    )

    assert report["added_connection_count"] == 0
    assert report["skipped_internal_edge_connection_count"] == 1
    assert ET.parse(output_net).getroot().find("connection") is None


def test_write_road_connectivity_self_replay_net_round_trips_bundle(tmp_path: Path) -> None:
    teacher = tmp_path / "teacher.net.xml"
    replay = tmp_path / "replay.net.xml"
    teacher.write_text(
        """<net version="1.20" junctionCornerDetail="5">
  <edge id="a" from="n1" to="n2"><lane id="a_0" index="0" allow="passenger" speed="13.89" length="10" shape="0,0 10,0"/></edge>
  <edge id="b" from="n2" to="n3"><lane id="b_0" index="0" allow="passenger" speed="13.89" length="10" shape="10,0 20,0"/></edge>
  <junction id="n1" type="dead_end" x="0" y="0" incLanes="" intLanes=""/>
  <junction id="n2" type="priority" x="10" y="0" incLanes="a_0" intLanes=""/>
  <junction id="n3" type="dead_end" x="20" y="0" incLanes="b_0" intLanes=""/>
  <connection from="a" to="b" fromLane="0" toLane="0" dir="s"/>
</net>""",
        encoding="utf-8",
    )

    report = write_road_connectivity_self_replay_net(teacher, ["a"], replay, hop_radius=1)

    assert report["status"] == "pass"
    assert replay.exists()
    assert canonical_road_connectivity_bundle(
        teacher,
        seed_edge_ids=["a"],
        hop_radius=1,
    ) == canonical_road_connectivity_bundle(replay, seed_edge_ids=["a"], hop_radius=1)


def test_road_connectivity_bundle_projects_connections_to_road_level_attrs(tmp_path: Path) -> None:
    net_file = tmp_path / "teacher.net.xml"
    net_file.write_text(
        """<net>
  <edge id="a" from="n1" to="n2"><lane id="a_0" index="0" allow="passenger" shape="0,0 10,0"/></edge>
  <edge id="b" from="n2" to="n3"><lane id="b_0" index="0" allow="passenger" shape="10,0 20,0"/></edge>
  <edge id=":n2_0" function="internal"><lane id=":n2_0_0" index="0" allow="passenger" shape="10,0 11,0"/></edge>
  <junction id="n1" type="dead_end" x="0" y="0" incLanes="" intLanes=""/>
  <junction id="n2" type="traffic_light" x="10" y="0" incLanes="a_0" intLanes=":n2_0_0"/>
  <junction id="n3" type="dead_end" x="20" y="0" incLanes="b_0" intLanes=""/>
  <tlLogic id="n2" type="static" programID="0" offset="0"><phase duration="30" state="G"/></tlLogic>
  <connection from="a" to="b" fromLane="0" toLane="0" via=":n2_0_0" tl="n2" linkIndex="0" state="O" dir="s"/>
</net>""",
        encoding="utf-8",
    )

    bundle = canonical_road_connectivity_bundle(net_file, seed_edge_ids=["a"], hop_radius=1)

    assert bundle["connections"] == [
        {"dir": "s", "from": "a", "fromLane": "0", "state": "O", "to": "b", "toLane": "0"}
    ]
    junctions = {junction["id"]: junction for junction in bundle["junctions"]}
    assert junctions["n2"]["requests"] == [{"cont": "0", "foes": "0", "index": "0", "response": "0"}]
