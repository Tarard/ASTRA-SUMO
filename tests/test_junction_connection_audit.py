from pathlib import Path

import pytest

from torii_sumo.core.junction_connection_audit import (
    build_connection_signature,
    build_teacher_guided_owner_semantics_probe,
    compare_tls_movement_signatures,
    compare_tls_via_path_semantics,
    write_connection_signature,
)


def test_connection_signature_separates_top_level_and_internal(tmp_path: Path) -> None:
    net_file = tmp_path / "connection.net.xml"
    net_file.write_text(
        """<net>
  <edge id="in" from="a" to="j" type="highway.primary">
    <lane id="in_0" index="0" allow="passenger" shape="-10,0 0,0"/>
  </edge>
  <edge id="out" from="j" to="b" type="highway.primary">
    <lane id="out_0" index="0" allow="passenger" shape="0,0 10,0"/>
  </edge>
  <edge id=":j_0" function="internal">
    <lane id=":j_0_0" index="0" allow="passenger" shape="0,0 4,0"/>
  </edge>
  <junction id="a" x="-10" y="0" type="priority"/>
  <junction id="j" x="0" y="0" type="traffic_light"/>
  <junction id="b" x="10" y="0" type="priority"/>
  <connection from="in" to="out" fromLane="0" toLane="0" via=":j_0_0" dir="s" state="o"/>
  <connection from=":j_0" to="out" fromLane="0" toLane="0" dir="s" state="o"/>
</net>
""",
        encoding="utf-8",
    )

    signature = build_connection_signature(net_file, "j")

    assert signature["top_external_connection_count"] == 1
    assert signature["top_external_pair_count"] == 1
    assert signature["category_counts"]["internal_or_other_to_outgoing"] == 1
    assert signature["top_external_dir_counts"] == {"s": 1}
    assert signature["top_external_turnaround_connection_count"] == 0
    assert signature["top_external_non_turnaround_connection_count"] == 1


def test_connection_signature_counts_turnaround_separately_from_normal_movements(tmp_path: Path) -> None:
    net_file = tmp_path / "turnaround.net.xml"
    net_file.write_text(
        """<net>
  <edge id="in" from="a" to="j"><lane id="in_0" index="0" allow="passenger" shape="-10,0 0,0"/></edge>
  <edge id="out" from="j" to="b"><lane id="out_0" index="0" allow="passenger" shape="0,0 10,0"/></edge>
  <edge id="back" from="j" to="a"><lane id="back_0" index="0" allow="passenger" shape="0,0 -10,0"/></edge>
  <junction id="j" x="0" y="0" type="priority"/>
  <connection from="in" to="out" fromLane="0" toLane="0" dir="s"/>
  <connection from="in" to="back" fromLane="0" toLane="0" dir="t"/>
</net>
""",
        encoding="utf-8",
    )

    signature = build_connection_signature(net_file, "j")

    assert signature["top_external_dir_counts"] == {"s": 1, "t": 1}
    assert signature["top_external_turnaround_connection_count"] == 1
    assert signature["top_external_non_turnaround_connection_count"] == 1


def test_owner_semantics_probe_keeps_road_connectivity_separate_from_turnaround_junction_signature(
    tmp_path: Path,
) -> None:
    teacher = tmp_path / "teacher.net.xml"
    candidate = tmp_path / "candidate.net.xml"
    teacher.write_text(
        """<net>
  <edge id="in#1" from="a" to="j"><lane id="in#1_0" index="0" allow="passenger"/></edge>
  <edge id="-in#1" from="j" to="a"><lane id="-in#1_0" index="0" allow="passenger"/></edge>
  <edge id="out#1" from="j" to="b"><lane id="out#1_0" index="0" allow="passenger"/></edge>
  <junction id="a" x="-10" y="0" type="priority"/>
  <junction id="j" x="0" y="0" type="priority"/>
  <junction id="b" x="10" y="0" type="priority"/>
  <connection from="in#1" to="out#1" fromLane="0" toLane="0" dir="s"/>
  <connection from="in#1" to="-in#1" fromLane="0" toLane="0" dir="t"/>
</net>
""",
        encoding="utf-8",
    )
    candidate.write_text(
        """<net>
  <edge id="in#3" from="a" to="j"><lane id="in#3_0" index="0" allow="passenger"/></edge>
  <edge id="-in#3" from="j" to="a"><lane id="-in#3_0" index="0" allow="passenger"/></edge>
  <edge id="out#3" from="j" to="b"><lane id="out#3_0" index="0" allow="passenger"/></edge>
  <junction id="a" x="-10" y="0" type="priority"/>
  <junction id="j" x="0" y="0" type="priority"/>
  <junction id="b" x="10" y="0" type="priority"/>
  <connection from="in#3" to="-in#3" fromLane="0" toLane="0" dir="t"/>
</net>
""",
        encoding="utf-8",
    )

    report = build_teacher_guided_owner_semantics_probe(
        teacher,
        candidate,
        owner_id="j",
        teacher_edge_map={"in#1": "in#3", "-in#1": "-in#3", "out#1": "out#3"},
    )

    assert report["status"] == "fail"
    assert report["layer_statuses"] == {
        "edge_mapping": "pass",
        "road_connectivity": "fail",
        "junction_connection": "fail",
        "pedestrian_crossing": "pass",
        "tls_movement": "skipped",
    }
    assert report["road_connectivity_layer"]["gate"]["missing_non_turnaround_outgoing_count"] == 1
    assert report["road_connectivity_layer"]["gate"]["turnaround_only_outgoing_count"] == 1
    assert report["junction_connection_layer"]["teacher"]["top_external_non_turnaround_connection_count"] == 1
    assert report["junction_connection_layer"]["candidate"]["top_external_non_turnaround_connection_count"] == 0
    assert report["junction_connection_layer"]["candidate_turnaround_only_top_external"] is True


def test_owner_semantics_probe_flags_missing_pedestrian_crossing_connection(
    tmp_path: Path,
) -> None:
    teacher = tmp_path / "teacher.net.xml"
    candidate = tmp_path / "candidate.net.xml"
    teacher.write_text(
        """<net>
  <edge id="in" from="a" to="j"><lane id="in_0" index="0" allow="passenger"/></edge>
  <edge id="out" from="j" to="b"><lane id="out_0" index="0" allow="passenger"/></edge>
  <edge id="foot" from="f" to="j"><lane id="foot_0" index="0" allow="pedestrian"/></edge>
  <edge id=":j_c0" function="crossing"><lane id=":j_c0_0" index="0" allow="pedestrian"/></edge>
  <edge id=":j_w0" function="walkingarea"><lane id=":j_w0_0" index="0" allow="pedestrian"/></edge>
  <junction id="a" x="-10" y="0" type="priority"/>
  <junction id="j" x="0" y="0" type="priority"/>
  <junction id="b" x="10" y="0" type="priority"/>
  <junction id="f" x="0" y="10" type="priority"/>
  <connection from="in" to="out" fromLane="0" toLane="0" dir="s"/>
  <connection from="foot" to=":j_w0" fromLane="0" toLane="0" dir="s" state="M"/>
  <connection from=":j_w0" to=":j_c0" fromLane="0" toLane="0" dir="s" state="M"/>
</net>
""",
        encoding="utf-8",
    )
    candidate.write_text(
        """<net>
  <edge id="in" from="a" to="j"><lane id="in_0" index="0" allow="passenger"/></edge>
  <edge id="out" from="j" to="b"><lane id="out_0" index="0" allow="passenger"/></edge>
  <edge id="foot" from="f" to="j"><lane id="foot_0" index="0" allow="pedestrian"/></edge>
  <edge id=":j_c0" function="crossing"><lane id=":j_c0_0" index="0" allow="pedestrian"/></edge>
  <edge id=":j_w0" function="walkingarea"><lane id=":j_w0_0" index="0" allow="pedestrian"/></edge>
  <junction id="a" x="-10" y="0" type="priority"/>
  <junction id="j" x="0" y="0" type="priority"/>
  <junction id="b" x="10" y="0" type="priority"/>
  <junction id="f" x="0" y="10" type="priority"/>
  <connection from="in" to="out" fromLane="0" toLane="0" dir="s"/>
  <connection from="foot" to=":j_w0" fromLane="0" toLane="0" dir="s" state="M"/>
</net>
""",
        encoding="utf-8",
    )

    report = build_teacher_guided_owner_semantics_probe(
        teacher,
        candidate,
        owner_id="j",
        teacher_edge_map={"foot": "foot", "in": "in", "out": "out"},
    )

    assert report["status"] == "fail"
    assert report["layer_statuses"]["road_connectivity"] == "pass"
    assert report["layer_statuses"]["junction_connection"] == "pass"
    assert report["layer_statuses"]["pedestrian_crossing"] == "fail"
    assert report["pedestrian_crossing_layer"]["teacher_edge_signature_count"] == 2
    assert report["pedestrian_crossing_layer"]["candidate_edge_signature_count"] == 2
    assert report["pedestrian_crossing_layer"]["teacher_only_normalized_connection_signatures"] == [
        "from=:TARGET_w0|to=:TARGET_c0|fromLane=0|toLane=0|via=|tl=|linkIndex=|dir=s|state=M"
    ]


def test_owner_semantics_probe_reuses_inferred_edge_map_for_tls_compare(tmp_path: Path) -> None:
    teacher = tmp_path / "teacher.net.xml"
    candidate = tmp_path / "candidate.net.xml"
    teacher.write_text(
        """<net>
  <edge id="in#1" from="a" to="j"><lane id="in#1_0" index="0" allow="passenger"/></edge>
  <edge id="out#1" from="j" to="b"><lane id="out#1_0" index="0" allow="passenger"/></edge>
  <junction id="a" x="-10" y="0" type="priority"/>
  <junction id="j" x="0" y="0" type="traffic_light"/>
  <junction id="b" x="10" y="0" type="priority"/>
  <connection from="in#1" to="out#1" fromLane="0" toLane="0" via=":j_0_0" tl="j" linkIndex="0" dir="s" state="M"/>
  <tlLogic id="j"><phase duration="10" state="G"/></tlLogic>
</net>
""",
        encoding="utf-8",
    )
    candidate.write_text(
        """<net>
  <edge id="in#3" from="a" to="j"><lane id="in#3_0" index="0" allow="passenger"/></edge>
  <edge id="out#3" from="j" to="b"><lane id="out#3_0" index="0" allow="passenger"/></edge>
  <junction id="a" x="-10" y="0" type="priority"/>
  <junction id="j" x="0" y="0" type="traffic_light"/>
  <junction id="b" x="10" y="0" type="priority"/>
  <connection from="in#3" to="out#3" fromLane="0" toLane="0" via=":j_0_0" tl="j" linkIndex="0" dir="s" state="M"/>
  <tlLogic id="j"><phase duration="10" state="G"/></tlLogic>
</net>
""",
        encoding="utf-8",
    )

    report = build_teacher_guided_owner_semantics_probe(teacher, candidate, owner_id="j")

    assert report["layer_statuses"]["tls_movement"] == "pass"
    assert report["teacher_edge_map"] == {"in#1": "in#3", "out#1": "out#3"}
    assert report["tls_movement_layer"]["teacher_only_normalized_movement_signatures"] == []
    assert report["tls_movement_layer"]["candidate_only_normalized_movement_signatures"] == []


def test_owner_semantics_probe_maps_edges_when_candidate_owner_id_differs(tmp_path: Path) -> None:
    teacher = tmp_path / "teacher.net.xml"
    candidate = tmp_path / "candidate.net.xml"
    teacher.write_text(
        """<net>
  <edge id="in" from="a" to="j"><lane id="in_0" index="0" allow="passenger"/></edge>
  <edge id="out" from="j" to="b"><lane id="out_0" index="0" allow="passenger"/></edge>
  <junction id="a" x="-10" y="0" type="priority"/>
  <junction id="j" x="0" y="0" type="traffic_light"/>
  <junction id="b" x="10" y="0" type="priority"/>
  <connection from="in" to="out" fromLane="0" toLane="0" via=":j_0_0" tl="j" linkIndex="0" dir="s" state="M"/>
  <tlLogic id="j"><phase duration="10" state="G"/></tlLogic>
</net>
""",
        encoding="utf-8",
    )
    candidate.write_text(
        """<net>
  <edge id="in" from="a" to="c"><lane id="in_0" index="0" allow="passenger"/></edge>
  <edge id="out" from="c" to="b"><lane id="out_0" index="0" allow="passenger"/></edge>
  <junction id="a" x="-10" y="0" type="priority"/>
  <junction id="c" x="0" y="0" type="traffic_light"/>
  <junction id="b" x="10" y="0" type="priority"/>
  <connection from="in" to="out" fromLane="0" toLane="0" via=":c_0_0" tl="c" linkIndex="0" dir="s" state="M"/>
  <tlLogic id="c"><phase duration="10" state="G"/></tlLogic>
</net>
""",
        encoding="utf-8",
    )

    report = build_teacher_guided_owner_semantics_probe(teacher, candidate, owner_id="j", candidate_owner_id="c")

    assert report["status"] == "pass"
    assert report["teacher_edge_map"] == {"in": "in", "out": "out"}
    assert report["layer_statuses"]["edge_mapping"] == "pass"


def test_owner_semantics_probe_reports_teacher_source_osm_coverage(tmp_path: Path) -> None:
    teacher = tmp_path / "teacher.net.xml"
    candidate = tmp_path / "candidate.net.xml"
    source = tmp_path / "source.osm.xml"
    teacher.write_text(
        """<net>
  <edge id="123#0" from="a" to="j"><lane id="123#0_0" index="0"/></edge>
  <edge id="-456#1" from="b" to="j"><lane id="-456#1_0" index="0"/></edge>
  <edge id="gneE1" from="c" to="j"><lane id="gneE1_0" index="0"/></edge>
  <junction id="j" type="traffic_light"/>
</net>
""",
        encoding="utf-8",
    )
    candidate.write_text("""<net><junction id="j" type="traffic_light"/></net>""", encoding="utf-8")
    source.write_text(
        """<osm version="0.6">
  <way id="123"><nd ref="a"/><nd ref="j"/><tag k="highway" v="secondary"/></way>
</osm>
""",
        encoding="utf-8",
    )

    report = build_teacher_guided_owner_semantics_probe(teacher, candidate, owner_id="j", source_osm_file=source)

    assert report["source_coverage_layer"]["status_counts"] == {
        "source_way_missing": 1,
        "source_way_present": 1,
        "synthetic_or_non_osm_edge": 1,
    }
    assert report["source_coverage_layer"]["teacher_edges"] == [
        {"edge_id": "-456#1", "direction": "incoming", "source_way_id": "456", "status": "source_way_missing"},
        {"edge_id": "123#0", "direction": "incoming", "source_way_id": "123", "status": "source_way_present"},
        {"edge_id": "gneE1", "direction": "incoming", "source_way_id": "", "status": "synthetic_or_non_osm_edge"},
    ]


def test_owner_semantics_probe_reports_ambiguous_edge_mapping(tmp_path: Path) -> None:
    teacher = tmp_path / "teacher.net.xml"
    candidate = tmp_path / "candidate.net.xml"
    teacher.write_text(
        """<net>
  <edge id="road#0" from="j" to="b"><lane id="road#0_0" index="0" allow="passenger"/></edge>
  <junction id="j" x="0" y="0" type="priority"/>
  <junction id="b" x="10" y="0" type="priority"/>
</net>
""",
        encoding="utf-8",
    )
    candidate.write_text(
        """<net>
  <edge id="road#1" from="j" to="b"><lane id="road#1_0" index="0" allow="passenger"/></edge>
  <edge id="road#2" from="j" to="b"><lane id="road#2_0" index="0" allow="passenger"/></edge>
  <junction id="j" x="0" y="0" type="priority"/>
  <junction id="b" x="10" y="0" type="priority"/>
</net>
""",
        encoding="utf-8",
    )

    report = build_teacher_guided_owner_semantics_probe(teacher, candidate, owner_id="j")

    assert report["status"] == "fail"
    assert report["layer_statuses"]["edge_mapping"] == "fail"
    assert report["edge_mapping_layer"]["ambiguous_teacher_edges"] == ["road#0"]
    assert report["teacher_edge_map"] == {}


def test_connection_signature_records_tls_link_indices(tmp_path: Path) -> None:
    net_file = tmp_path / "tls.net.xml"
    net_file.write_text(
        """<net>
  <edge id="in" from="a" to="j"><lane id="in_0" index="0" allow="passenger" shape="-10,0 0,0"/></edge>
  <edge id="out" from="j" to="b"><lane id="out_0" index="0" allow="passenger" shape="0,0 10,0"/></edge>
  <junction id="j" x="0" y="0" type="traffic_light"/>
  <connection from="in" to="out" fromLane="0" toLane="0" tl="j" linkIndex="7" linkIndex2="12" dir="s" state="O" pass="true" uncontrolled="true" allow="bicycle" disallow="truck" keepClear="0" contPos="43.00" shape="0,0 1,1"/>
</net>
""",
        encoding="utf-8",
    )

    signature = build_connection_signature(net_file, "j")

    assert signature["controlled_link_count"] == 1
    record = signature["connection_records"][0]
    assert record["tl"] == "j"
    assert record["linkIndex"] == "7"
    assert record["linkIndex2"] == "12"
    assert record["pass"] == "true"
    assert record["uncontrolled"] == "true"
    assert record["allow"] == "bicycle"
    assert record["disallow"] == "truck"
    assert record["keepClear"] == "0"
    assert record["contPos"] == "43.00"
    assert record["shape"] == "0,0 1,1"


def test_connection_signature_counts_crossings_and_walkingareas(tmp_path: Path) -> None:
    net_file = tmp_path / "modal.net.xml"
    net_file.write_text(
        """<net>
  <edge id="in" from="a" to="j"><lane id="in_0" index="0" allow="passenger" shape="-10,0 0,0"/></edge>
  <edge id="out" from="j" to="b"><lane id="out_0" index="0" allow="passenger" shape="0,0 10,0"/></edge>
  <edge id=":j_c0" function="crossing"><lane id=":j_c0_0" index="0" allow="pedestrian" shape="0,-2 0,2"/></edge>
  <edge id=":j_w0" function="walkingarea"><lane id=":j_w0_0" index="0" allow="pedestrian" shape="0,2 2,2"/></edge>
  <junction id="j" x="0" y="0" type="traffic_light" incLanes="in_0 :j_w0_0" intLanes=":j_c0_0"/>
  <connection from="in" to="out" fromLane="0" toLane="0"/>
</net>
""",
        encoding="utf-8",
    )

    signature = build_connection_signature(net_file, "j")

    assert signature["crossing_count"] == 1
    assert signature["walkingarea_count"] == 1


def test_write_connection_signature_outputs_review_files(tmp_path: Path) -> None:
    net_file = tmp_path / "connection.net.xml"
    net_file.write_text(
        """<net>
  <edge id="in" from="a" to="j" type="highway.primary"><lane id="in_0" index="0" allow="passenger" shape="-10,0 0,0"/></edge>
  <edge id="out" from="j" to="b" type="highway.primary"><lane id="out_0" index="0" allow="passenger" shape="0,0 10,0"/></edge>
  <junction id="a" x="-10" y="0" type="priority"/>
  <junction id="j" x="0" y="0" type="traffic_light"/>
  <junction id="b" x="10" y="0" type="priority"/>
  <connection from="in" to="out" fromLane="0" toLane="0" dir="s" state="o" keepClear="0" shape="0,0 1,1"/>
</net>
""",
        encoding="utf-8",
    )
    signature = build_connection_signature(net_file, "j")

    report = write_connection_signature(signature, tmp_path / "review", "demo")

    assert Path(report["signature_file"]).is_file()
    records_header = Path(report["records_file"]).read_text(encoding="utf-8").splitlines()[0]
    top_external_header = Path(report["top_external_file"]).read_text(encoding="utf-8").splitlines()[0]
    for field in ("linkIndex2", "pass", "uncontrolled", "allow", "disallow", "keepClear", "contPos", "shape"):
        assert field in records_header
        assert field in top_external_header


def test_tls_movement_compare_ignores_internal_id_prefix_when_linkindex_and_phases_match(
    tmp_path: Path,
) -> None:
    teacher = tmp_path / "teacher.net.xml"
    candidate = tmp_path / "candidate.net.xml"
    teacher.write_text(
        """<net>
  <connection from="in" to="out" fromLane="0" toLane="1" via=":teacher_tls_0_0" tl="teacher_tls" linkIndex="3" dir="l" state="m"/>
  <tlLogic id="teacher_tls" type="static" programID="0" offset="0">
    <phase duration="42" state="Gr"/>
    <phase duration="3" state="yr"/>
  </tlLogic>
</net>
""",
        encoding="utf-8",
    )
    candidate.write_text(
        """<net>
  <connection from="in" to="out" fromLane="0" toLane="1" via=":candidate_tls_0_0" tl="candidate_tls" linkIndex="3" dir="l" state="m"/>
  <tlLogic id="candidate_tls" type="static" programID="0" offset="0">
    <phase duration="42" state="Gr"/>
    <phase duration="3" state="yr"/>
  </tlLogic>
</net>
""",
        encoding="utf-8",
    )

    report = compare_tls_movement_signatures(teacher, candidate, "teacher_tls", "candidate_tls")

    assert report["status"] == "pass"
    assert report["teacher_connection_count"] == 1
    assert report["candidate_connection_count"] == 1
    assert report["movement_signature_equal_after_internal_id_normalization"] is True
    assert report["tl_logic_phase_states_equal"] is True
    assert report["teacher_only_normalized_movement_signatures"] == []
    assert report["candidate_only_normalized_movement_signatures"] == []


def test_tls_via_path_semantics_accepts_netconvert_internal_suffix_renumbering(
    tmp_path: Path,
) -> None:
    teacher = tmp_path / "teacher_via.net.xml"
    candidate = tmp_path / "candidate_via.net.xml"
    teacher.write_text(
        """<net>
  <edge id="in" from="a" to="j"><lane id="in_0" index="0"/></edge>
  <edge id="out" from="j" to="b"><lane id="out_0" index="0"/></edge>
  <edge id=":teacher_0" function="internal"><lane id=":teacher_0_0" index="0" length="4" shape="0,0 2,0 4,0"/></edge>
  <junction id="j" type="traffic_light" x="0" y="0"/>
  <connection from="in" to="out" fromLane="0" toLane="0" via=":teacher_0_0" tl="j" linkIndex="0" dir="s" state="o"/>
  <tlLogic id="j"><phase duration="10" state="G"/></tlLogic>
</net>""",
        encoding="utf-8",
    )
    candidate.write_text(
        """<net>
  <edge id="in2" from="a2" to="c"><lane id="in2_0" index="0"/></edge>
  <edge id="out2" from="c" to="b2"><lane id="out2_0" index="0"/></edge>
  <edge id=":candidate_7" function="internal"><lane id=":candidate_7_0" index="0" length="4" shape="10,0 12,0 14,0"/></edge>
  <junction id="c" type="traffic_light" x="10" y="0"/>
  <connection from="in2" to="out2" fromLane="0" toLane="0" via=":candidate_7_0" tl="c" linkIndex="0" dir="s" state="o"/>
  <tlLogic id="c"><phase duration="10" state="G"/></tlLogic>
</net>""",
        encoding="utf-8",
    )

    report = compare_tls_via_path_semantics(
        teacher,
        candidate,
        "j",
        "c",
        teacher_edge_map={"in": "in2", "out": "out2"},
        teacher_internal_scope_id="teacher",
        candidate_internal_scope_id="candidate",
    )

    assert report["status"] == "pass"
    assert report["movement_key_equal"] is True
    assert report["via_geometry_status"] == "pass"


@pytest.mark.parametrize(
    ("changed_side", "change", "missing_via_count"),
    [
        ("candidate", "displaced_shape", 0),
        ("candidate", "changed_length", 0),
        ("candidate", "missing_lane", 1),
        ("teacher", "missing_lane", 0),
    ],
)
def test_tls_via_path_semantics_rejects_one_bad_path_among_valid_movements(
    tmp_path: Path, changed_side: str, change: str, missing_via_count: int
) -> None:
    net = """<net>
  <edge id=":j_0" function="internal"><lane id=":j_0_0" index="0" length="4" shape="0,0 4,0"/></edge>
  <edge id=":j_1" function="internal"><lane id=":j_1_0" index="0" length="4" shape="0,1 4,1"/></edge>
  <junction id="j" type="traffic_light" x="0" y="0"/>
  <connection from="in" to="out" fromLane="0" toLane="0" via=":j_0_0" tl="j" linkIndex="0" dir="s" state="o"/>
  <connection from="in" to="out" fromLane="1" toLane="1" via=":j_1_0" tl="j" linkIndex="1" dir="s" state="o"/>
  <tlLogic id="j"><phase duration="10" state="GG"/></tlLogic>
</net>"""
    changes = {
        "displaced_shape": ('shape="0,1 4,1"', 'shape="1000,1 1004,1"'),
        "changed_length": ('id=":j_1_0" index="0" length="4"', 'id=":j_1_0" index="0" length="1004"'),
        "missing_lane": ('<lane id=":j_1_0" index="0" length="4" shape="0,1 4,1"/>', ""),
    }
    old, new = changes[change]
    teacher = tmp_path / "teacher.net.xml"
    candidate = tmp_path / "candidate.net.xml"
    teacher.write_text(net.replace(old, new) if changed_side == "teacher" else net, encoding="utf-8")
    candidate.write_text(net.replace(old, new) if changed_side == "candidate" else net, encoding="utf-8")

    report = compare_tls_via_path_semantics(teacher, candidate, "j", "j")

    assert report["movement_key_equal"] is True
    assert report["tl_logic_phase_states_equal"] is True
    assert len(report["via_checks"]) == 2
    assert report["via_path_failure_count"] == 1
    assert report["missing_via_count"] == missing_via_count
    assert report["via_geometry_status"] != "pass"
    assert report["status"] == "fail"


def test_tls_movement_compare_maps_teacher_external_edges(tmp_path: Path) -> None:
    teacher = tmp_path / "teacher.net.xml"
    candidate = tmp_path / "candidate.net.xml"
    teacher.write_text(
        """<net>
  <connection from="teacher_in" to="teacher_out" fromLane="0" toLane="0" tl="teacher_tls" linkIndex="0" dir="s" state="O"/>
  <tlLogic id="teacher_tls"><phase duration="10" state="G"/></tlLogic>
</net>
""",
        encoding="utf-8",
    )
    candidate.write_text(
        """<net>
  <connection from="cand_in" to="cand_out" fromLane="0" toLane="0" tl="candidate_tls" linkIndex="0" dir="s" state="O"/>
  <tlLogic id="candidate_tls"><phase duration="10" state="G"/></tlLogic>
</net>
""",
        encoding="utf-8",
    )

    report = compare_tls_movement_signatures(
        teacher,
        candidate,
        "teacher_tls",
        "candidate_tls",
        teacher_edge_map={"teacher_in": "cand_in", "teacher_out": "cand_out"},
    )

    assert report["status"] == "pass"
    assert report["teacher_only_normalized_movement_signatures"] == []
    assert report["candidate_only_normalized_movement_signatures"] == []


def test_tls_movement_compare_can_scope_multi_junction_tls_to_target_internal_owner(
    tmp_path: Path,
) -> None:
    teacher = tmp_path / "teacher.net.xml"
    candidate = tmp_path / "candidate.net.xml"
    teacher.write_text(
        """<net>
  <connection from="teacher_in" to="teacher_out" fromLane="0" toLane="0" via=":teacher_j_0_0" tl="teacher_tls" linkIndex="0" dir="s" state="O"/>
  <connection from="neighbor_in" to="neighbor_out" fromLane="0" toLane="0" via=":neighbor_j_0_0" tl="teacher_tls" linkIndex="1" dir="s" state="O"/>
  <tlLogic id="teacher_tls"><phase duration="10" state="GG"/></tlLogic>
</net>
""",
        encoding="utf-8",
    )
    candidate.write_text(
        """<net>
  <connection from="cand_in" to="cand_out" fromLane="0" toLane="0" via=":candidate_j_0_0" tl="candidate_tls" linkIndex="0" dir="s" state="O"/>
  <tlLogic id="candidate_tls"><phase duration="10" state="GG"/></tlLogic>
</net>
""",
        encoding="utf-8",
    )

    report = compare_tls_movement_signatures(
        teacher,
        candidate,
        "teacher_tls",
        "candidate_tls",
        teacher_edge_map={"teacher_in": "cand_in", "teacher_out": "cand_out"},
        teacher_internal_scope_id="teacher_j",
        candidate_internal_scope_id="candidate_j",
    )

    assert report["status"] == "pass"
    assert report["teacher_connection_count"] == 1
    assert report["candidate_connection_count"] == 1
    assert report["teacher_only_normalized_movement_signatures"] == []
    assert report["candidate_only_normalized_movement_signatures"] == []
    assert report["scoped_internal_ids"] == {"teacher": "teacher_j", "candidate": "candidate_j"}


def test_tls_movement_compare_flags_linkindex_delta(tmp_path: Path) -> None:
    teacher = tmp_path / "teacher.net.xml"
    candidate = tmp_path / "candidate.net.xml"
    teacher.write_text(
        """<net>
  <connection from="in" to="out" fromLane="0" toLane="0" via=":teacher_tls_0_0" tl="teacher_tls" linkIndex="0" dir="s" state="M"/>
  <tlLogic id="teacher_tls"><phase duration="10" state="G"/></tlLogic>
</net>
""",
        encoding="utf-8",
    )
    candidate.write_text(
        """<net>
  <connection from="in" to="out" fromLane="0" toLane="0" via=":candidate_tls_0_0" tl="candidate_tls" linkIndex="1" dir="s" state="M"/>
  <tlLogic id="candidate_tls"><phase duration="10" state="G"/></tlLogic>
</net>
""",
        encoding="utf-8",
    )

    report = compare_tls_movement_signatures(teacher, candidate, "teacher_tls", "candidate_tls")

    assert report["status"] == "fail"
    assert report["movement_signature_equal_after_internal_id_normalization"] is False
    assert report["tl_logic_phase_states_equal"] is True
    assert report["teacher_only_normalized_movement_signatures"] == [
        "from=in|to=out|fromLane=0|toLane=0|via=:TARGET_0_0|linkIndex=0|dir=s|state=M"
    ]
    assert report["candidate_only_normalized_movement_signatures"] == [
        "from=in|to=out|fromLane=0|toLane=0|via=:TARGET_0_0|linkIndex=1|dir=s|state=M"
    ]


def test_tls_movement_compare_flags_phase_state_delta(tmp_path: Path) -> None:
    teacher = tmp_path / "teacher.net.xml"
    candidate = tmp_path / "candidate.net.xml"
    teacher.write_text(
        """<net>
  <connection from="in" to="out" fromLane="0" toLane="0" via=":teacher_tls_0_0" tl="teacher_tls" linkIndex="0" dir="s" state="M"/>
  <tlLogic id="teacher_tls"><phase duration="10" state="G"/><phase duration="3" state="y"/></tlLogic>
</net>
""",
        encoding="utf-8",
    )
    candidate.write_text(
        """<net>
  <connection from="in" to="out" fromLane="0" toLane="0" via=":candidate_tls_0_0" tl="candidate_tls" linkIndex="0" dir="s" state="M"/>
  <tlLogic id="candidate_tls"><phase duration="10" state="G"/><phase duration="3" state="r"/></tlLogic>
</net>
""",
        encoding="utf-8",
    )

    report = compare_tls_movement_signatures(teacher, candidate, "teacher_tls", "candidate_tls")

    assert report["status"] == "fail"
    assert report["movement_signature_equal_after_internal_id_normalization"] is True
    assert report["tl_logic_phase_states_equal"] is False
    assert report["teacher_phase_states"] == ["G", "y"]
    assert report["candidate_phase_states"] == ["G", "r"]
