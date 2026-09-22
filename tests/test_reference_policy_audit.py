from pathlib import Path

from torii_sumo.core.reference_policy_audit import build_reference_policy_report


def test_reference_policy_report_extracts_teacher_rules(tmp_path: Path) -> None:
    net_file = tmp_path / "teacher.net.xml"
    net_file.write_text(
        """<net>
  <edge id="main_in" from="a" to="cluster_1_2" type="highway.primary"><lane id="main_in_0" index="0" allow="passenger" shape="-10,0 0,0"/></edge>
  <edge id="service" from="s" to="cluster_1_2" type="highway.service"><lane id="service_0" index="0" allow="delivery" shape="0,-10 0,0"/></edge>
  <edge id="main_out" from="cluster_1_2" to="b" type="highway.primary"><lane id="main_out_0" index="0" allow="passenger" shape="0,0 10,0"/></edge>
  <junction id="a" x="-10" y="0" type="priority"/>
  <junction id="s" x="0" y="-10" type="priority"/>
  <junction id="cluster_1_2" x="0" y="0" type="traffic_light"/>
  <junction id="b" x="10" y="0" type="priority"/>
  <tlLogic id="cluster_1_2" type="static" programID="0" offset="0"><phase duration="30" state="G"/></tlLogic>
  <connection from="main_in" to="main_out" fromLane="0" toLane="0" dir="s" state="O"/>
</net>
""",
        encoding="utf-8",
    )

    report = build_reference_policy_report(net_file)

    assert report["road_type_counts"]["highway.primary"] == 2
    assert report["passenger_drivable_type_counts"] == {"highway.primary": 2}
    assert report["cluster_source_node_count_histogram"] == {"2": 1}
    assert report["tls_logic_count"] == 1
    assert report["top_level_connection_count"] == 1


def test_reference_policy_report_extracts_junction_pattern_templates(tmp_path: Path) -> None:
    net_file = tmp_path / "teacher.net.xml"
    net_file.write_text(
        """<net>
  <edge id="a_in" from="a" to="cluster_1_2_3" type="highway.primary"><lane id="a_in_0" index="0" allow="passenger" shape="-10,0 0,0"/></edge>
  <edge id="b_in" from="b" to="cluster_1_2_3" type="highway.primary"><lane id="b_in_0" index="0" allow="passenger" shape="0,-10 0,0"/></edge>
  <edge id="c_in" from="c" to="cluster_1_2_3" type="highway.primary"><lane id="c_in_0" index="0" allow="passenger" shape="10,0 0,0"/></edge>
  <edge id="a_out" from="cluster_1_2_3" to="a2" type="highway.primary"><lane id="a_out_0" index="0" allow="passenger" shape="0,0 -10,0"/></edge>
  <edge id="b_out" from="cluster_1_2_3" to="b2" type="highway.primary"><lane id="b_out_0" index="0" allow="passenger" shape="0,0 0,-10"/></edge>
  <edge id="c_out" from="cluster_1_2_3" to="c2" type="highway.primary"><lane id="c_out_0" index="0" allow="passenger" shape="0,0 10,0"/></edge>
  <edge id=":cluster_1_2_3_0" function="internal"><lane id=":cluster_1_2_3_0_0" index="0" allow="passenger" shape="0,0 1,0"/></edge>
  <edge id=":cluster_1_2_3_c0" function="crossing"><lane id=":cluster_1_2_3_c0_0" index="0" allow="pedestrian" shape="0,-1 0,1"/></edge>
  <edge id=":cluster_1_2_3_w0" function="walkingarea"><lane id=":cluster_1_2_3_w0_0" index="0" allow="pedestrian" shape="1,1 2,1"/></edge>
  <junction id="cluster_1_2_3" x="0" y="0" type="traffic_light" incLanes="a_in_0 b_in_0 c_in_0" intLanes=":cluster_1_2_3_0_0 :cluster_1_2_3_c0_0">
    <request index="0" response="000" foes="000" cont="0"/>
    <request index="1" response="000" foes="000" cont="0"/>
    <request index="2" response="000" foes="000" cont="0"/>
  </junction>
  <connection from="a_in" to="a_out" fromLane="0" toLane="0" tl="cluster_1_2_3" linkIndex="0" dir="s"/>
  <connection from="b_in" to="b_out" fromLane="0" toLane="0" tl="cluster_1_2_3" linkIndex="1" dir="r"/>
  <connection from="c_in" to="c_out" fromLane="0" toLane="0" tl="cluster_1_2_3" linkIndex="2" dir="l"/>
  <tlLogic id="cluster_1_2_3" type="static" programID="0" offset="0"><phase duration="30" state="GrG"/></tlLogic>
</net>
""",
        encoding="utf-8",
    )

    report = build_reference_policy_report(net_file)

    assert report["junction_pattern_record_count"] == 1
    assert report["junction_pattern_family_counts"] == {"three_way": 1}
    assert report["junction_pattern_control_counts"] == {"three_way|traffic_light|tls": 1}
    assert report["junction_pattern_templates"][0]["pattern_family"] == "three_way"
    assert report["junction_pattern_templates"][0]["internal_function_counts"] == {
        "crossing": 1,
        "internal": 1,
        "walkingarea": 1,
    }
