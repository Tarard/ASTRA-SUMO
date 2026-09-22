from pathlib import Path

from torii_sumo.core.junction_teacher_model import (
    build_teacher_self_replay_corpus_report,
    canonical_teacher_junction_bundle,
    compare_junction_pattern_records,
    evaluate_netedit_semantics_gate,
    extract_junction_pattern_exemplar,
    extract_junction_pattern_index,
    extract_teacher_junction_model,
    materialize_exemplar_movement_signatures,
    match_teacher_approaches,
    slot_edge_map_from_exemplar,
    summarize_junction_pattern_templates,
    write_teacher_self_replay_net,
)


def test_canonical_teacher_junction_bundle_keeps_replay_critical_tables(tmp_path: Path) -> None:
    net_file = tmp_path / "teacher.net.xml"
    net_file.write_text(
        """<net version="1.20" junctionCornerDetail="5" limitTurnSpeed="5.50">
  <location netOffset="0.00,0.00" convBoundary="-10.00,-10.00,10.00,10.00" origBoundary="-10.00,-10.00,10.00,10.00" projParameter="!"/>
  <edge id="in" from="a" to="j"><lane id="in_0" index="0" allow="passenger" speed="13.89" length="10.00" shape="-10,0 0,0"/></edge>
  <edge id="out" from="j" to="b"><lane id="out_0" index="0" allow="passenger" speed="13.89" length="10.00" shape="0,0 10,0"/></edge>
  <edge id=":j_0" function="internal"><lane id=":j_0_0" index="0" allow="passenger" shape="0,0 5,0"/></edge>
  <edge id=":j_w0" function="walkingarea"><lane id=":j_w0_0" index="0" allow="pedestrian" shape="0,2 2,2"/></edge>
  <junction id="a" type="dead_end" x="-10" y="0" incLanes="" intLanes=""/>
  <junction id="b" type="dead_end" x="10" y="0" incLanes="out_0" intLanes=""/>
  <junction id="j" type="traffic_light" x="0" y="0" incLanes="in_0 :j_w0_0" intLanes=":j_0_0">
    <request index="0" response="0" foes="0" cont="0"/>
  </junction>
  <junction id=":j_0_0" type="internal" x="1" y="0" incLanes=":j_0_0" intLanes=""/>
  <tlLogic id="j" type="actuated" programID="0" offset="0"><phase duration="30" state="G"/></tlLogic>
  <connection from="in" to="out" fromLane="0" toLane="0" via=":j_0_0" tl="j" linkIndex="0" dir="s" state="O"/>
</net>""",
        encoding="utf-8",
    )

    bundle = canonical_teacher_junction_bundle(net_file, "j")

    assert bundle["junction_id"] == "j"
    assert bundle["net"]["version"] == "1.20"
    assert bundle["junctions"][0]["id"] == ":j_0_0"
    assert bundle["junctions"][1]["id"] == "a"
    assert bundle["junctions"][2]["id"] == "b"
    assert bundle["junctions"][3]["id"] == "j"
    assert bundle["edges"][0]["id"] == ":j_0"
    assert bundle["edges"][1]["id"] == ":j_w0"
    assert bundle["edges"][2]["id"] == "in"
    assert bundle["edges"][3]["id"] == "out"
    assert bundle["connections"] == [
        {
            "dir": "s",
            "from": "in",
            "fromLane": "0",
            "linkIndex": "0",
            "state": "O",
            "tl": "j",
            "to": "out",
            "toLane": "0",
            "via": ":j_0_0",
        }
    ]
    assert bundle["tlLogics"][0]["id"] == "j"
    assert bundle["summary"]["connection_count"] == 1


def test_canonical_teacher_junction_bundle_excludes_neighbor_boundary_connections(tmp_path: Path) -> None:
    net_file = tmp_path / "teacher.net.xml"
    net_file.write_text(
        """<net>
  <edge id="other" from="x" to="a"><lane id="other_0" index="0" allow="passenger" shape="-20,0 -10,0"/></edge>
  <edge id="in" from="a" to="j"><lane id="in_0" index="0" allow="passenger" shape="-10,0 0,0"/></edge>
  <edge id="out" from="j" to="b"><lane id="out_0" index="0" allow="passenger" shape="0,0 10,0"/></edge>
  <edge id=":a_0" function="internal"><lane id=":a_0_0" index="0" allow="passenger" shape="-12,0 -10,0"/></edge>
  <junction id="a" type="priority" x="-10" y="0" incLanes="other_0" intLanes=":a_0_0"/>
  <junction id="b" type="dead_end" x="10" y="0" incLanes="out_0" intLanes=""/>
  <junction id="j" type="priority" x="0" y="0" incLanes="in_0" intLanes=""/>
  <junction id="x" type="dead_end" x="-20" y="0" incLanes="" intLanes=""/>
  <junction id=":a_0_0" type="internal" x="-11" y="0" incLanes=":a_0_0" intLanes=""/>
  <connection from="other" to="in" fromLane="0" toLane="0" via=":a_0_0" dir="s"/>
  <connection from="in" to="out" fromLane="0" toLane="0" dir="s"/>
</net>""",
        encoding="utf-8",
    )

    bundle = canonical_teacher_junction_bundle(net_file, "j")

    assert bundle["connections"] == [{"dir": "s", "from": "in", "fromLane": "0", "to": "out", "toLane": "0"}]


def test_canonical_teacher_junction_bundle_trims_boundary_junction_lane_refs(tmp_path: Path) -> None:
    net_file = tmp_path / "teacher.net.xml"
    net_file.write_text(
        """<net>
  <edge id="other" from="x" to="a"><lane id="other_0" index="0" allow="passenger" shape="-20,0 -10,0"/></edge>
  <edge id="in" from="a" to="j"><lane id="in_0" index="0" allow="passenger" shape="-10,0 0,0"/></edge>
  <edge id="out" from="j" to="b"><lane id="out_0" index="0" allow="passenger" shape="0,0 10,0"/></edge>
  <edge id=":a_0" function="internal"><lane id=":a_0_0" index="0" allow="passenger" shape="-12,0 -10,0"/></edge>
  <junction id="a" type="priority" x="-10" y="0" incLanes="other_0" intLanes=":a_0_0"/>
  <junction id="b" type="dead_end" x="10" y="0" incLanes="out_0" intLanes=""/>
  <junction id="j" type="priority" x="0" y="0" incLanes="in_0" intLanes=""/>
  <junction id="x" type="dead_end" x="-20" y="0" incLanes="" intLanes=""/>
  <junction id=":a_0_0" type="internal" x="-11" y="0" incLanes=":a_0_0" intLanes=""/>
  <connection from="other" to="in" fromLane="0" toLane="0" via=":a_0_0" dir="s"/>
  <connection from="in" to="out" fromLane="0" toLane="0" dir="s"/>
</net>""",
        encoding="utf-8",
    )

    bundle = canonical_teacher_junction_bundle(net_file, "j")
    boundary = {junction["id"]: junction for junction in bundle["junctions"]}

    assert boundary["a"]["incLanes"] == ""
    assert boundary["a"]["intLanes"] == ""
    assert boundary["b"]["incLanes"] == "out_0"


def test_canonical_teacher_junction_bundle_includes_target_modal_connection_edges(tmp_path: Path) -> None:
    net_file = tmp_path / "teacher.net.xml"
    net_file.write_text(
        """<net>
  <edge id="car_in" from="a" to="j"><lane id="car_in_0" index="0" allow="passenger" shape="-10,0 0,0"/></edge>
  <edge id="car_out" from="j" to="b"><lane id="car_out_0" index="0" allow="passenger" shape="0,0 10,0"/></edge>
  <edge id="ped_in" from="p1" to="j" type="highway.footway"><lane id="ped_in_0" index="0" allow="pedestrian" shape="-1,2 0,2"/></edge>
  <edge id="ped_out" from="j" to="p2" type="highway.footway"><lane id="ped_out_0" index="0" allow="pedestrian" shape="0,2 1,2"/></edge>
  <edge id=":j_w0" function="walkingarea"><lane id=":j_w0_0" index="0" allow="pedestrian" shape="0,2 0.5,2"/></edge>
  <junction id="a" type="dead_end" x="-10" y="0" incLanes="" intLanes=""/>
  <junction id="b" type="dead_end" x="10" y="0" incLanes="car_out_0" intLanes=""/>
  <junction id="j" type="priority" x="0" y="0" incLanes="car_in_0 ped_in_0" intLanes=""/>
  <junction id="p1" type="dead_end" x="-1" y="2" incLanes="" intLanes=""/>
  <junction id="p2" type="dead_end" x="1" y="2" incLanes="ped_out_0" intLanes=""/>
  <connection from="car_in" to="car_out" fromLane="0" toLane="0" dir="s"/>
  <connection from="ped_in" to=":j_w0" fromLane="0" toLane="0" dir="s"/>
  <connection from=":j_w0" to="ped_out" fromLane="0" toLane="0" dir="s"/>
</net>""",
        encoding="utf-8",
    )

    bundle = canonical_teacher_junction_bundle(net_file, "j")

    assert [edge["id"] for edge in bundle["edges"]] == [":j_w0", "car_in", "car_out", "ped_in", "ped_out"]


def test_write_teacher_self_replay_net_round_trips_canonical_bundle(tmp_path: Path) -> None:
    teacher = tmp_path / "teacher.net.xml"
    replay = tmp_path / "replay.net.xml"
    teacher.write_text(
        """<net>
  <edge id="in" from="a" to="j"><lane id="in_0" index="0" allow="passenger" shape="-10,0 0,0"/></edge>
  <edge id="out" from="j" to="b"><lane id="out_0" index="0" allow="passenger" shape="0,0 10,0"/></edge>
  <edge id=":j_0" function="internal"><lane id=":j_0_0" index="0" allow="passenger" shape="0,0 5,0"/></edge>
  <junction id="a" type="dead_end" x="-10" y="0" incLanes="" intLanes=""/>
  <junction id="b" type="dead_end" x="10" y="0" incLanes="out_0" intLanes=""/>
  <junction id="j" type="traffic_light" x="0" y="0" incLanes="in_0" intLanes=":j_0_0">
    <request index="0" response="0" foes="0" cont="0"/>
  </junction>
  <junction id=":j_0_0" type="internal" x="1" y="0" incLanes=":j_0_0" intLanes=""/>
  <tlLogic id="j" type="static" programID="0" offset="0"><phase duration="30" state="G"/></tlLogic>
  <connection from="in" to="out" fromLane="0" toLane="0" via=":j_0_0" tl="j" linkIndex="0" dir="s"/>
</net>""",
        encoding="utf-8",
    )

    report = write_teacher_self_replay_net(teacher, "j", replay)

    assert report["status"] == "pass"
    assert replay.exists()
    assert canonical_teacher_junction_bundle(teacher, "j") == canonical_teacher_junction_bundle(replay, "j")


def test_build_teacher_self_replay_corpus_report_writes_artifacts(tmp_path: Path) -> None:
    teacher = tmp_path / "teacher.net.xml"
    out_dir = tmp_path / "corpus"
    teacher.write_text(
        """<net>
  <edge id="in" from="a" to="j"><lane id="in_0" index="0" allow="passenger" shape="-10,0 0,0"/></edge>
  <edge id="out" from="j" to="b"><lane id="out_0" index="0" allow="passenger" shape="0,0 10,0"/></edge>
  <junction id="a" type="dead_end" x="-10" y="0" incLanes="" intLanes=""/>
  <junction id="b" type="dead_end" x="10" y="0" incLanes="out_0" intLanes=""/>
  <junction id="j" type="priority" x="0" y="0" incLanes="in_0" intLanes=""/>
  <connection from="in" to="out" fromLane="0" toLane="0" dir="s"/>
</net>""",
        encoding="utf-8",
    )

    report = build_teacher_self_replay_corpus_report(
        teacher,
        ["j"],
        out_dir,
        run_sumo=False,
    )

    assert report["status"] == "pass"
    assert report["cases"][0]["junction_id"] == "j"
    assert Path(report["cases"][0]["replay_net_file"]).exists()
    assert (out_dir / "teacher_self_replay_corpus.json").exists()


def test_netedit_semantics_gate_fails_on_non_same_statuses() -> None:
    summary = {
        "status_counts": {
            "tls_linkIndex_diff": {"same": 23},
            "phase_diff": {"same": 10},
            "request_diff": {"same": 41},
            "internal_lane_diff": {"same": 59},
            "crossing_walkingarea_diff": {"same": 10},
            "junction_attrs_diff": {"same": 8},
            "connection_exact_diff": {"same": 108, "candidate_extra": 1},
        }
    }

    result = evaluate_netedit_semantics_gate(summary)

    assert result == {
        "status": "fail",
        "failed_tables": ["connection_exact_diff"],
        "reason": "non_same_rows_present",
    }


def test_netedit_semantics_gate_passes_when_all_tables_are_same() -> None:
    summary = {
        "status_counts": {
            "tls_linkIndex_diff": {"same": 23},
            "phase_diff": {"same": 10},
            "request_diff": {"same": 41},
        }
    }

    assert evaluate_netedit_semantics_gate(summary) == {"status": "pass", "failed_tables": [], "reason": ""}


def test_teacher_model_extracts_multimodal_junction(tmp_path: Path) -> None:
    net_file = tmp_path / "teacher.net.xml"
    net_file.write_text(
        """<net>
  <edge id="in" from="a" to="j"><lane id="in_0" index="0" allow="passenger" disallow="pedestrian bicycle" speed="13.89" length="10.50" shape="-10,0 0,0"/></edge>
  <edge id="out" from="j" to="b"><lane id="out_0" index="0" allow="passenger" shape="0,0 10,0"/></edge>
  <edge id=":j_0" function="internal"><lane id=":j_0_0" index="0" allow="passenger" shape="0,0 5,0"/></edge>
  <edge id=":j_c0" function="crossing" crossingEdges="in out"><lane id=":j_c0_0" index="0" allow="pedestrian" width="4.00" shape="0,-2 0,2" outlineShape="0,-3 0,3"/></edge>
  <edge id=":j_w0" function="walkingarea"><lane id=":j_w0_0" index="0" allow="pedestrian" width="4.00" shape="0,2 2,2" outlineShape="0,1 2,1 2,3 0,3"/></edge>
  <junction id="j" type="traffic_light" x="0" y="0" incLanes="in_0 :j_w0_0" intLanes=":j_0_0 :j_c0_0"/>
  <junction id=":j_0_0" type="internal" x="1" y="1" incLanes=":j_0_0" intLanes="" shape="0,0 1,1" customShape="0,0 1,0 1,1"/>
  <tlLogic id="j" type="actuated" programID="0"><phase duration="30" state="Gr"/></tlLogic>
  <connection from="in" to="out" fromLane="0" toLane="0" via=":j_0_0" tl="j" linkIndex="0" dir="s"/>
  <connection from=":j_w0" to=":j_c0" fromLane="0" toLane="0" tl="j" linkIndex="1" dir="s"/>
</net>
""",
        encoding="utf-8",
    )

    model = extract_teacher_junction_model(net_file, "j")

    assert model["summary"]["vehicle_connection_count"] == 1
    assert model["summary"]["crossing_count"] == 1
    assert model["summary"]["walkingarea_count"] == 1
    assert model["summary"]["pedestrian_connection_count"] == 1
    assert model["summary"]["tl_phase_count"] == 1
    assert model["approaches"]["incoming"][0]["bearing"] == 0.0
    assert model["approaches"]["incoming"][0]["lanes"][0]["disallow"] == "pedestrian bicycle"
    assert model["approaches"]["incoming"][0]["lanes"][0]["speed"] == "13.89"
    assert model["approaches"]["incoming"][0]["lanes"][0]["length"] == "10.50"
    assert model["vehicle_connections"][0]["linkIndex"] == "0"
    assert model["internal_edges"][0]["edge_id"] == ":j_0"
    assert model["internal_edges"][0]["lanes"][0]["shape"] == "0,0 5,0"
    assert model["internal_junctions"][0]["junction_id"] == ":j_0_0"
    assert model["internal_junctions"][0]["shape"] == "0,0 1,1"
    assert model["internal_junctions"][0]["customShape"] == "0,0 1,0 1,1"
    assert model["crossings"][0]["crossingEdges"] == ["in", "out"]
    assert model["crossings"][0]["lanes"][0]["outlineShape"] == "0,-3 0,3"
    assert model["walking_areas"][0]["lanes"][0]["outlineShape"] == "0,1 2,1 2,3 0,3"


def test_teacher_model_excludes_pedestrian_only_incoming_from_vehicle_approaches(tmp_path: Path) -> None:
    net_file = tmp_path / "teacher.net.xml"
    net_file.write_text(
        """<net>
  <edge id="car_in" from="a" to="j"><lane id="car_in_0" index="0" allow="passenger" shape="-10,0 0,0"/></edge>
  <edge id="ped_in" from="p" to="j" type="highway.footway"><lane id="ped_in_0" index="0" allow="pedestrian" shape="-5,2 0,2"/></edge>
  <edge id="out" from="j" to="b"><lane id="out_0" index="0" allow="passenger" shape="0,0 10,0"/></edge>
  <edge id=":j_w0" function="walkingarea"><lane id=":j_w0_0" index="0" allow="pedestrian" shape="0,2 2,2"/></edge>
  <junction id="j" type="priority" x="0" y="0" incLanes="car_in_0 ped_in_0 :j_w0_0" intLanes=""/>
  <connection from="car_in" to="out" fromLane="0" toLane="0" dir="s"/>
  <connection from="ped_in" to=":j_w0" fromLane="0" toLane="0" dir="s" state="M"/>
</net>""",
        encoding="utf-8",
    )

    model = extract_teacher_junction_model(net_file, "j")

    assert [edge["edge_id"] for edge in model["approaches"]["incoming"]] == ["car_in"]
    assert model["summary"]["incoming_vehicle_edge_count"] == 1
    assert model["summary"]["vehicle_connection_count"] == 1
    assert model["summary"]["pedestrian_connection_count"] == 1
    assert model["pedestrian_connections"][0]["from"] == "ped_in"


def test_teacher_model_keeps_junction_requests_internal_connections_and_referenced_tls(tmp_path: Path) -> None:
    net_file = tmp_path / "teacher.net.xml"
    net_file.write_text(
        """<net>
  <edge id="in" from="a" to="j"><lane id="in_0" index="0" allow="passenger" shape="-10,0 0,0"/></edge>
  <edge id="out" from="j" to="b"><lane id="out_0" index="0" allow="passenger" shape="0,0 10,0"/></edge>
  <edge id=":j_0" function="internal"><lane id=":j_0_0" index="0" allow="passenger" shape="0,0 5,0"/></edge>
  <junction id="j" type="traffic_light" x="0" y="0" incLanes="in_0" intLanes=":j_0_0">
    <request index="0" response="0" foes="0" cont="0"/>
  </junction>
  <connection from="in" to="out" fromLane="0" toLane="0" via=":j_0_0" tl="cluster_tls" linkIndex="0" linkIndex2="12" dir="s" pass="true" uncontrolled="true" allow="bicycle" disallow="truck" keepClear="0" contPos="43.00" shape="0,0 1,1"/>
  <connection from=":j_0" to="out" fromLane="0" toLane="0" dir="s"/>
  <tlLogic id="cluster_tls" type="actuated" programID="0"><phase duration="30" state="G"/></tlLogic>
</net>""",
        encoding="utf-8",
    )

    model = extract_teacher_junction_model(net_file, "j")

    assert model["junction"]["type"] == "traffic_light"
    assert model["requests"] == [{"index": "0", "response": "0", "foes": "0", "cont": "0"}]
    assert model["vehicle_connections"][0]["pass"] == "true"
    assert model["vehicle_connections"][0]["uncontrolled"] == "true"
    assert model["vehicle_connections"][0]["allow"] == "bicycle"
    assert model["vehicle_connections"][0]["disallow"] == "truck"
    assert model["vehicle_connections"][0]["keepClear"] == "0"
    assert model["vehicle_connections"][0]["contPos"] == "43.00"
    assert model["vehicle_connections"][0]["linkIndex2"] == "12"
    assert model["vehicle_connections"][0]["shape"] == "0,0 1,1"
    assert model["internal_connections"] == [
        {
            "from": ":j_0",
            "to": "out",
            "fromLane": "0",
            "toLane": "0",
            "via": "",
            "tl": "",
            "linkIndex": "",
            "dir": "s",
            "state": "",
            "pass": "",
            "uncontrolled": "",
            "allow": "",
            "disallow": "",
            "keepClear": "",
            "contPos": "",
            "linkIndex2": "",
            "shape": "",
        }
    ]
    assert model["traffic_light"]["attributes"]["id"] == "cluster_tls"
    assert model["summary"]["junction_type"] == "traffic_light"
    assert model["summary"]["internal_connection_count"] == 1
    assert model["summary"]["tl_phase_count"] == 1


def test_extract_junction_pattern_index_groups_by_reusable_counts(tmp_path: Path) -> None:
    net_file = tmp_path / "teacher.net.xml"
    net_file.write_text(
        """<net>
  <edge id="a_in" from="a" to="j"><lane id="a_in_0" index="0" allow="passenger" shape="-10,0 0,0"/></edge>
  <edge id="b_in" from="b" to="j"><lane id="b_in_0" index="0" allow="passenger" shape="0,-10 0,0"/></edge>
  <edge id="c_in" from="c" to="j"><lane id="c_in_0" index="0" allow="passenger" shape="10,0 0,0"/></edge>
  <edge id="a_out" from="j" to="a2"><lane id="a_out_0" index="0" allow="passenger" shape="0,0 -10,0"/></edge>
  <edge id="b_out" from="j" to="b2"><lane id="b_out_0" index="0" allow="passenger" shape="0,0 0,-10"/></edge>
  <edge id="c_out" from="j" to="c2"><lane id="c_out_0" index="0" allow="passenger" shape="0,0 10,0"/></edge>
  <edge id=":j_0" function="internal"><lane id=":j_0_0" index="0" shape="0,0 1,1"/></edge>
  <junction id="j" type="right_before_left" x="0" y="0" incLanes="a_in_0 b_in_0 c_in_0" intLanes=":j_0_0">
    <request index="0" response="000" foes="000" cont="0"/>
    <request index="1" response="000" foes="000" cont="0"/>
    <request index="2" response="000" foes="000" cont="0"/>
  </junction>
  <connection from="a_in" to="a_out" fromLane="0" toLane="0" dir="t"/>
  <connection from="a_in" to="b_out" fromLane="0" toLane="0" dir="r"/>
  <connection from="a_in" to="c_out" fromLane="0" toLane="0" dir="l"/>
</net>""",
        encoding="utf-8",
    )

    records = extract_junction_pattern_index(net_file, min_approaches=3, max_approaches=4)

    assert records == [
        {
            "junction_id": "j",
            "pattern_family": "three_way",
            "pattern_key": "three_way|control=right_before_left|dir=l:1,r:1,t:1|veh=3|tls=0/0|ped=0/0|internal=1/0|requests=3",
            "arm_count": 3,
            "control_type": "right_before_left",
            "has_tls": False,
            "approach_edge_ids": ["a_in", "b_in", "c_in"],
            "in_edge_count": 3,
            "out_edge_count": 3,
            "vehicle_connection_count": 3,
            "internal_edge_count": 1,
            "internal_connection_count": 0,
            "internal_function_counts": {"crossing": 0, "internal": 1, "walkingarea": 0},
            "dir_counts": {"l": 1, "r": 1, "t": 1},
            "movement_signature_counts": {
                "dir=l|state=blank|fromLane=0|toLane=0|controlled=false|via=false": 1,
                "dir=r|state=blank|fromLane=0|toLane=0|controlled=false|via=false": 1,
                "dir=t|state=blank|fromLane=0|toLane=0|controlled=false|via=false": 1,
            },
            "crossing_count": 0,
            "walkingarea_count": 0,
            "request_count": 3,
            "request_bit_lengths_ok": True,
            "tl_phase_count": 0,
            "controlled_link_count": 0,
        }
    ]


def test_extract_junction_pattern_index_groups_parallel_edges_into_physical_arms(tmp_path: Path) -> None:
    net_file = tmp_path / "teacher.net.xml"
    net_file.write_text(
        """<net>
  <edge id="west_in_a" from="wa" to="j"><lane id="west_in_a_0" index="0" allow="passenger" shape="-10,0 0,0"/></edge>
  <edge id="west_in_b" from="wb" to="j"><lane id="west_in_b_0" index="0" allow="passenger" shape="-10,1 0,1"/></edge>
  <edge id="south_in" from="s" to="j"><lane id="south_in_0" index="0" allow="passenger" shape="0,-10 0,0"/></edge>
  <edge id="east_in" from="e" to="j"><lane id="east_in_0" index="0" allow="passenger" shape="10,0 0,0"/></edge>
  <edge id="north_in" from="n" to="j"><lane id="north_in_0" index="0" allow="passenger" shape="0,10 0,0"/></edge>
  <edge id="east_out_a" from="j" to="ea"><lane id="east_out_a_0" index="0" allow="passenger" shape="0,0 10,0"/></edge>
  <edge id="east_out_b" from="j" to="eb"><lane id="east_out_b_0" index="0" allow="passenger" shape="0,1 10,1"/></edge>
  <edge id="south_out" from="j" to="so"><lane id="south_out_0" index="0" allow="passenger" shape="0,0 0,-10"/></edge>
  <edge id="west_out" from="j" to="wo"><lane id="west_out_0" index="0" allow="passenger" shape="0,0 -10,0"/></edge>
  <edge id="north_out" from="j" to="no"><lane id="north_out_0" index="0" allow="passenger" shape="0,0 0,10"/></edge>
  <junction id="j" type="traffic_light" x="0" y="0" incLanes="west_in_a_0 west_in_b_0 south_in_0 east_in_0 north_in_0" intLanes=""/>
  <connection from="west_in_a" to="east_out_a" fromLane="0" toLane="0" tl="j" linkIndex="0" dir="s"/>
  <tlLogic id="j" type="actuated" programID="0"><phase duration="30" state="G"/></tlLogic>
</net>""",
        encoding="utf-8",
    )

    records = extract_junction_pattern_index(net_file, min_approaches=3, max_approaches=4)

    assert len(records) == 1
    assert records[0]["in_edge_count"] == 5
    assert records[0]["out_edge_count"] == 5
    assert records[0]["arm_count"] == 4
    assert records[0]["pattern_family"] == "four_way"


def test_summarize_junction_pattern_templates_groups_reusable_records() -> None:
    records = [
        {
            "junction_id": "j1",
            "pattern_key": "three_way|control=priority",
            "pattern_family": "three_way",
            "arm_count": 3,
            "control_type": "priority",
            "has_tls": False,
            "internal_function_counts": {"crossing": 0, "internal": 1, "walkingarea": 0},
            "dir_counts": {"s": 3},
            "movement_signature_counts": {"dir=s|state=blank|fromLane=0|toLane=0|controlled=false|via=false": 3},
            "request_count": 3,
            "request_bit_lengths_ok": True,
            "tl_phase_count": 0,
            "controlled_link_count": 0,
            "vehicle_connection_count": 3,
        },
        {
            "junction_id": "j2",
            "pattern_key": "three_way|control=priority",
            "pattern_family": "three_way",
            "arm_count": 3,
            "control_type": "priority",
            "has_tls": False,
            "internal_function_counts": {"crossing": 0, "internal": 1, "walkingarea": 0},
            "dir_counts": {"s": 3},
            "movement_signature_counts": {"dir=s|state=blank|fromLane=0|toLane=0|controlled=false|via=false": 3},
            "request_count": 3,
            "request_bit_lengths_ok": True,
            "tl_phase_count": 0,
            "controlled_link_count": 0,
            "vehicle_connection_count": 3,
        },
        {
            "junction_id": "j3",
            "pattern_key": "four_way|control=traffic_light",
            "pattern_family": "four_way",
            "arm_count": 4,
            "control_type": "traffic_light",
            "has_tls": True,
            "internal_function_counts": {"crossing": 4, "internal": 8, "walkingarea": 4},
            "dir_counts": {"l": 4, "r": 4, "s": 4},
            "movement_signature_counts": {"dir=s|state=O|fromLane=0|toLane=0|controlled=true|via=true": 4},
            "request_count": 12,
            "request_bit_lengths_ok": True,
            "tl_phase_count": 8,
            "controlled_link_count": 12,
            "vehicle_connection_count": 12,
        },
    ]

    assert summarize_junction_pattern_templates(records, max_examples=1) == [
        {
            "pattern_key": "three_way|control=priority",
            "pattern_family": "three_way",
            "arm_count": 3,
            "count": 2,
            "example_junction_ids": ["j1"],
            "control_type": "priority",
            "has_tls": False,
            "internal_function_counts": {"crossing": 0, "internal": 1, "walkingarea": 0},
            "dir_counts": {"s": 3},
            "movement_signature_counts": {"dir=s|state=blank|fromLane=0|toLane=0|controlled=false|via=false": 3},
            "request_count": 3,
            "request_bit_lengths_ok": True,
            "tl_phase_count": 0,
            "controlled_link_count": 0,
            "vehicle_connection_count": 3,
        },
        {
            "pattern_key": "four_way|control=traffic_light",
            "pattern_family": "four_way",
            "arm_count": 4,
            "count": 1,
            "example_junction_ids": ["j3"],
            "control_type": "traffic_light",
            "has_tls": True,
            "internal_function_counts": {"crossing": 4, "internal": 8, "walkingarea": 4},
            "dir_counts": {"l": 4, "r": 4, "s": 4},
            "movement_signature_counts": {"dir=s|state=O|fromLane=0|toLane=0|controlled=true|via=true": 4},
            "request_count": 12,
            "request_bit_lengths_ok": True,
            "tl_phase_count": 8,
            "controlled_link_count": 12,
            "vehicle_connection_count": 12,
        },
    ]


def test_compare_junction_pattern_records_catches_tum_template_fields(tmp_path: Path) -> None:
    teacher_net = tmp_path / "teacher.net.xml"
    teacher_net.write_text(
        """<net>
  <edge id="a_in" from="a" to="j"><lane id="a_in_0" index="0" allow="passenger" shape="-10,0 0,0"/></edge>
  <edge id="b_in" from="b" to="j"><lane id="b_in_0" index="0" allow="passenger" shape="0,-10 0,0"/></edge>
  <edge id="c_in" from="c" to="j"><lane id="c_in_0" index="0" allow="passenger" shape="10,0 0,0"/></edge>
  <edge id="a_out" from="j" to="a2"><lane id="a_out_0" index="0" allow="passenger" shape="0,0 -10,0"/></edge>
  <edge id="b_out" from="j" to="b2"><lane id="b_out_0" index="0" allow="passenger" shape="0,0 0,-10"/></edge>
  <edge id="c_out" from="j" to="c2"><lane id="c_out_0" index="0" allow="passenger" shape="0,0 10,0"/></edge>
  <edge id=":j_0" function="internal"><lane id=":j_0_0" index="0" allow="passenger" shape="0,0 1,1"/></edge>
  <edge id=":j_c0" function="crossing"><lane id=":j_c0_0" index="0" allow="pedestrian" shape="0,-1 0,1"/></edge>
  <edge id=":j_w0" function="walkingarea"><lane id=":j_w0_0" index="0" allow="pedestrian" shape="1,1 2,1"/></edge>
  <junction id="j" type="traffic_light" x="0" y="0" incLanes="a_in_0 b_in_0 c_in_0" intLanes=":j_0_0 :j_c0_0">
    <request index="0" response="00" foes="00" cont="0"/>
    <request index="1" response="00" foes="00" cont="1"/>
  </junction>
  <connection from="a_in" to="a_out" fromLane="0" toLane="0" tl="j" linkIndex="0" dir="s"/>
  <tlLogic id="j" type="actuated" programID="0"><phase duration="30" state="G"/></tlLogic>
</net>""",
        encoding="utf-8",
    )
    candidate_net = tmp_path / "candidate.net.xml"
    candidate_net.write_text(
        """<net>
  <edge id="a_in" from="a" to="j"><lane id="a_in_0" index="0" allow="passenger" shape="-10,0 0,0"/></edge>
  <edge id="b_in" from="b" to="j"><lane id="b_in_0" index="0" allow="passenger" shape="0,-10 0,0"/></edge>
  <edge id="d_in" from="d" to="j"><lane id="d_in_0" index="0" allow="passenger" shape="10,0 0,0"/></edge>
  <edge id="a_out" from="j" to="a2"><lane id="a_out_0" index="0" allow="passenger" shape="0,0 -10,0"/></edge>
  <edge id="b_out" from="j" to="b2"><lane id="b_out_0" index="0" allow="passenger" shape="0,0 0,-10"/></edge>
  <edge id="d_out" from="j" to="d2"><lane id="d_out_0" index="0" allow="passenger" shape="0,0 10,0"/></edge>
  <edge id=":j_0" function="internal"><lane id=":j_0_0" index="0" allow="passenger" shape="0,0 1,1"/></edge>
  <junction id="j" type="priority" x="0" y="0" incLanes="a_in_0 b_in_0 d_in_0" intLanes=":j_0_0">
    <request index="0" response="0" foes="0" cont="0"/>
    <request index="1" response="00" foes="00" cont="00"/>
  </junction>
  <connection from="a_in" to="a_out" fromLane="0" toLane="0" dir="s"/>
</net>""",
        encoding="utf-8",
    )

    teacher = extract_junction_pattern_index(teacher_net)[0]
    candidate = extract_junction_pattern_index(candidate_net)[0]
    comparison = compare_junction_pattern_records(teacher, candidate)

    assert teacher["has_tls"] is True
    assert teacher["approach_edge_ids"] == ["a_in", "b_in", "c_in"]
    assert teacher["internal_function_counts"] == {"crossing": 1, "internal": 1, "walkingarea": 1}
    assert teacher["request_bit_lengths_ok"] is True
    assert comparison["status"] == "fail"
    assert comparison["mismatch_fields"] == [
        "approach_edge_ids",
        "control_type",
        "has_tls",
        "internal_function_counts",
        "movement_signature_counts",
        "request_bit_lengths_ok",
    ]


def test_compare_junction_pattern_records_flags_movement_signature_template_delta() -> None:
    teacher = {
        "approach_edge_ids": ["west_in", "south_in", "east_in"],
        "control_type": "traffic_light",
        "has_tls": True,
        "internal_function_counts": {"crossing": 0, "internal": 3, "walkingarea": 0},
        "request_bit_lengths_ok": True,
        "movement_signature_counts": {
            "dir=s|state=O|fromLane=0|toLane=0|controlled=true|via=true": 3,
        },
    }
    candidate = {
        **teacher,
        "movement_signature_counts": {
            "dir=s|state=blank|fromLane=0|toLane=0|controlled=false|via=false": 3,
        },
    }

    comparison = compare_junction_pattern_records(teacher, candidate)

    assert comparison["status"] == "fail"
    assert comparison["mismatch_fields"] == ["movement_signature_counts"]
    assert comparison["teacher"]["movement_signature_counts"] != comparison["candidate"]["movement_signature_counts"]


def test_compare_junction_pattern_records_accepts_explicit_approach_edge_equivalence() -> None:
    teacher = {
        "approach_edge_ids": ["teacher_west", "teacher_south"],
        "control_type": "traffic_light",
        "has_tls": True,
        "internal_function_counts": {"crossing": 2, "internal": 5, "walkingarea": 2},
        "request_bit_lengths_ok": True,
        "movement_signature_counts": {
            "dir=s|state=o|fromLane=0|toLane=0|controlled=true|via=true": 2,
        },
    }
    candidate = {
        **teacher,
        "approach_edge_ids": ["candidate_west", "candidate_south"],
    }

    comparison = compare_junction_pattern_records(
        teacher,
        candidate,
        equivalent_approach_edge_map={
            "teacher_west": "candidate_west",
            "teacher_south": "candidate_south",
        },
    )

    assert comparison["status"] == "pass"
    assert comparison["mismatch_fields"] == []
    assert comparison["approach_edge_equivalence_applied"] is True


def test_extract_junction_pattern_index_keeps_blank_connection_dir(tmp_path: Path) -> None:
    net_file = tmp_path / "teacher.net.xml"
    net_file.write_text(
        """<net>
  <edge id="a_in" from="a" to="j"><lane id="a_in_0" index="0" allow="passenger" shape="-10,0 0,0"/></edge>
  <edge id="b_in" from="b" to="j"><lane id="b_in_0" index="0" allow="passenger" shape="0,-10 0,0"/></edge>
  <edge id="c_in" from="c" to="j"><lane id="c_in_0" index="0" allow="passenger" shape="10,0 0,0"/></edge>
  <edge id="a_out" from="j" to="a2"><lane id="a_out_0" index="0" allow="passenger" shape="0,0 -10,0"/></edge>
  <edge id="b_out" from="j" to="b2"><lane id="b_out_0" index="0" allow="passenger" shape="0,0 0,-10"/></edge>
  <edge id="c_out" from="j" to="c2"><lane id="c_out_0" index="0" allow="passenger" shape="0,0 10,0"/></edge>
  <junction id="j" type="right_before_left" x="0" y="0" incLanes="a_in_0 b_in_0 c_in_0" intLanes=""/>
  <connection from="a_in" to="a_out" fromLane="0" toLane="0"/>
</net>""",
        encoding="utf-8",
    )

    records = extract_junction_pattern_index(net_file)

    assert records[0]["dir_counts"] == {"blank": 1}


def test_extract_junction_pattern_index_counts_non_matching_tls_id(tmp_path: Path) -> None:
    net_file = tmp_path / "teacher.net.xml"
    net_file.write_text(
        """<net>
  <edge id="a_in" from="a" to="j"><lane id="a_in_0" index="0" allow="passenger" shape="-10,0 0,0"/></edge>
  <edge id="b_in" from="b" to="j"><lane id="b_in_0" index="0" allow="passenger" shape="0,-10 0,0"/></edge>
  <edge id="c_in" from="c" to="j"><lane id="c_in_0" index="0" allow="passenger" shape="10,0 0,0"/></edge>
  <edge id="a_out" from="j" to="a2"><lane id="a_out_0" index="0" allow="passenger" shape="0,0 -10,0"/></edge>
  <edge id="b_out" from="j" to="b2"><lane id="b_out_0" index="0" allow="passenger" shape="0,0 0,-10"/></edge>
  <edge id="c_out" from="j" to="c2"><lane id="c_out_0" index="0" allow="passenger" shape="0,0 10,0"/></edge>
  <junction id="j" type="traffic_light" x="0" y="0" incLanes="a_in_0 b_in_0 c_in_0" intLanes=""/>
  <connection from="a_in" to="a_out" fromLane="0" toLane="0" tl="cluster_tls" linkIndex="0" dir="s"/>
  <tlLogic id="cluster_tls" type="actuated" programID="0"><phase duration="30" state="G"/></tlLogic>
</net>""",
        encoding="utf-8",
    )

    records = extract_junction_pattern_index(net_file)

    assert records[0]["controlled_link_count"] == 1
    assert records[0]["tl_phase_count"] == 1


def test_extract_junction_pattern_index_parses_net_once(tmp_path: Path, monkeypatch) -> None:
    net_file = tmp_path / "teacher.net.xml"
    net_file.write_text(
        """<net>
  <edge id="a_in" from="a" to="j"><lane id="a_in_0" index="0" allow="passenger" shape="-10,0 0,0"/></edge>
  <edge id="b_in" from="b" to="j"><lane id="b_in_0" index="0" allow="passenger" shape="0,-10 0,0"/></edge>
  <edge id="c_in" from="c" to="j"><lane id="c_in_0" index="0" allow="passenger" shape="10,0 0,0"/></edge>
  <edge id="a_out" from="j" to="a2"><lane id="a_out_0" index="0" allow="passenger" shape="0,0 -10,0"/></edge>
  <edge id="b_out" from="j" to="b2"><lane id="b_out_0" index="0" allow="passenger" shape="0,0 0,-10"/></edge>
  <edge id="c_out" from="j" to="c2"><lane id="c_out_0" index="0" allow="passenger" shape="0,0 10,0"/></edge>
  <junction id="j" type="right_before_left" x="0" y="0" incLanes="a_in_0 b_in_0 c_in_0" intLanes=""/>
</net>""",
        encoding="utf-8",
    )
    from torii_sumo.core import junction_teacher_model

    parse_calls = 0
    original_parse = junction_teacher_model.ET.parse

    def counted_parse(path):
        nonlocal parse_calls
        parse_calls += 1
        return original_parse(path)

    monkeypatch.setattr(junction_teacher_model.ET, "parse", counted_parse)

    extract_junction_pattern_index(net_file)

    assert parse_calls == 1


def test_extract_junction_pattern_index_skips_low_approach_junctions_before_model_extraction(
    tmp_path: Path, monkeypatch
) -> None:
    net_file = tmp_path / "teacher.net.xml"
    net_file.write_text(
        """<net>
  <edge id="two_a" from="a" to="two"><lane id="two_a_0" index="0" allow="passenger" shape="-10,0 0,0"/></edge>
  <edge id="two_b" from="b" to="two"><lane id="two_b_0" index="0" allow="passenger" shape="0,-10 0,0"/></edge>
  <edge id="three_a" from="a" to="three"><lane id="three_a_0" index="0" allow="passenger" shape="-10,0 0,0"/></edge>
  <edge id="three_b" from="b" to="three"><lane id="three_b_0" index="0" allow="passenger" shape="0,-10 0,0"/></edge>
  <edge id="three_c" from="c" to="three"><lane id="three_c_0" index="0" allow="passenger" shape="10,0 0,0"/></edge>
  <edge id="three_out" from="three" to="out"><lane id="three_out_0" index="0" allow="passenger" shape="0,0 10,0"/></edge>
  <edge id="five_a" from="a" to="five"><lane id="five_a_0" index="0" allow="passenger" shape="-10,0 0,0"/></edge>
  <edge id="five_b" from="b" to="five"><lane id="five_b_0" index="0" allow="passenger" shape="0,-10 0,0"/></edge>
  <edge id="five_c" from="c" to="five"><lane id="five_c_0" index="0" allow="passenger" shape="10,0 0,0"/></edge>
  <edge id="five_d" from="d" to="five"><lane id="five_d_0" index="0" allow="passenger" shape="0,10 0,0"/></edge>
  <edge id="five_e" from="e" to="five"><lane id="five_e_0" index="0" allow="passenger" shape="1,10 0,0"/></edge>
  <junction id="two" type="priority" x="0" y="0" incLanes="two_a_0 two_b_0" intLanes=""/>
  <junction id="three" type="priority" x="0" y="0" incLanes="three_a_0 three_b_0 three_c_0" intLanes=""/>
  <junction id="five" type="priority" x="0" y="0" incLanes="five_a_0 five_b_0 five_c_0 five_d_0 five_e_0" intLanes=""/>
  <connection from="three_a" to="three_out" fromLane="0" toLane="0" dir="s"/>
</net>""",
        encoding="utf-8",
    )
    from torii_sumo.core import junction_teacher_model

    calls: list[str] = []
    original_extract = junction_teacher_model._extract_teacher_junction_model

    def counted_extract(root, net_file, junction_id):
        calls.append(junction_id)
        return original_extract(root, net_file, junction_id)

    monkeypatch.setattr(junction_teacher_model, "_extract_teacher_junction_model", counted_extract)

    extract_junction_pattern_index(net_file, min_approaches=3, max_approaches=4)

    assert calls == ["three", "five"]


def test_extract_junction_pattern_exemplar_uses_slots_not_edge_ids(tmp_path: Path) -> None:
    net_file = tmp_path / "teacher.net.xml"
    net_file.write_text(
        """<net>
  <edge id="north_in" from="n" to="j"><lane id="north_in_0" index="0" allow="passenger" speed="13.89" shape="0,10 0,0"/></edge>
  <edge id="east_out" from="j" to="e"><lane id="east_out_0" index="0" allow="passenger" speed="13.89" shape="0,0 10,0"/></edge>
  <edge id=":j_0" function="internal"><lane id=":j_0_0" index="0" speed="10.0" shape="0,0 2,0"/></edge>
  <junction id="j" type="traffic_light" x="0" y="0" incLanes="north_in_0" intLanes=":j_0_0">
    <request index="0" response="0" foes="0" cont="0"/>
  </junction>
  <tlLogic id="j" type="actuated" programID="0" offset="0"><phase duration="30" state="G"/></tlLogic>
  <connection from="north_in" to="east_out" fromLane="0" toLane="0" via=":j_0_0" tl="j" linkIndex="0" dir="r" state="O"/>
</net>""",
        encoding="utf-8",
    )

    exemplar = extract_junction_pattern_exemplar(net_file, "j")

    assert exemplar["junction_id"] == "j"
    assert exemplar["pattern_family"] == "1_arm"
    assert exemplar["pattern_key"] == "1_arm|control=traffic_light|dir=r:1|veh=1|tls=1/1|ped=0/0|internal=1/0|requests=1"
    assert exemplar["approach_slots"][0]["slot_id"] == "slot_0"
    assert exemplar["approach_slots"][0]["members"] == ["north_in"]
    assert exemplar["vehicle_connections"] == [
        {
            "from_slot": "slot_0",
            "to_slot": "slot_1",
            "fromLane": "0",
            "toLane": "0",
            "via": ":j_0_0",
            "tl": "j",
            "linkIndex": "0",
            "dir": "r",
            "state": "O",
        }
    ]
    assert exemplar["movement_signatures"] == [
        {
            "from_slot": "slot_0",
            "to_slot": "slot_1",
            "fromLane": "0",
            "toLane": "0",
            "dir": "r",
            "state": "O",
            "controlled": True,
            "linkIndex": "0",
            "has_internal_via": True,
        }
    ]
    assert exemplar["traffic_light"]["phases"][0]["state"] == "G"
    assert exemplar["requests"][0]["foes"] == "0"


def test_materialize_exemplar_movement_signatures_filters_four_way_cross_product() -> None:
    exemplar = {
        "movement_signatures": [
            {
                "from_slot": "slot_0",
                "to_slot": "slot_2",
                "fromLane": "0",
                "toLane": "0",
                "dir": "s",
                "state": "O",
                "controlled": True,
                "linkIndex": "0",
                "has_internal_via": True,
            },
            {
                "from_slot": "slot_1",
                "to_slot": "slot_3",
                "fromLane": "0",
                "toLane": "0",
                "dir": "r",
                "state": "o",
                "controlled": True,
                "linkIndex": "1",
                "has_internal_via": True,
            },
        ]
    }

    movements = materialize_exemplar_movement_signatures(
        exemplar,
        {"slot_0": "west_in", "slot_1": "south_in", "slot_2": "east_out", "slot_3": "west_out"},
    )

    assert [(movement["from_edge_id"], movement["to_edge_id"]) for movement in movements] == [
        ("west_in", "east_out"),
        ("south_in", "west_out"),
    ]
    assert {movement["dir"] for movement in movements} == {"s", "r"}
    assert len(movements) == 2


def test_slot_edge_map_from_exemplar_uses_teacher_edge_map() -> None:
    exemplar = {
        "approach_slots": [
            {"slot_id": "slot_0", "members": ["teacher_in"]},
            {"slot_id": "slot_1", "members": ["teacher_out", "teacher_out_alt"]},
            {"slot_id": "slot_2", "members": ["unmatched"]},
        ]
    }

    slot_edge_map = slot_edge_map_from_exemplar(
        exemplar,
        {"teacher_in": "cand_in", "teacher_out_alt": "cand_out"},
    )

    assert slot_edge_map == {"slot_0": "cand_in", "slot_1": "cand_out"}


def test_match_teacher_edges_by_bearing_and_lane_count() -> None:
    teacher = [{"edge_id": "t1", "bearing": 90.0, "lane_count": 2}]
    candidate = [{"edge_id": "c1", "bearing": 92.0, "lane_count": 2}]

    matches = match_teacher_approaches(teacher, candidate, max_bearing_delta=10.0)

    assert matches == {"t1": "c1"}


def test_match_teacher_edges_does_not_treat_shared_junction_from_as_external_source() -> None:
    teacher = [{"edge_id": "t1", "from": "junction", "bearing": 90.0, "lane_count": 1}]
    candidate = [
        {"edge_id": "wrong", "from": "junction", "bearing": 250.0, "lane_count": 1},
        {"edge_id": "right", "from": "other", "bearing": 91.0, "lane_count": 1},
    ]

    matches = match_teacher_approaches(teacher, candidate, max_bearing_delta=10.0)

    assert matches == {"t1": "right"}


def test_match_teacher_edges_prefers_same_type_over_slightly_closer_bearing() -> None:
    teacher = [{"edge_id": "cycle", "bearing": 304.0, "lane_count": 1, "type": "highway.cycleway"}]
    candidate = [
        {"edge_id": "footway", "bearing": 297.0, "lane_count": 1, "type": "highway.footway"},
        {"edge_id": "cycleway", "bearing": 319.0, "lane_count": 1, "type": "highway.cycleway"},
    ]

    matches = match_teacher_approaches(teacher, candidate, max_bearing_delta=35.0)

    assert matches == {"cycle": "cycleway"}
