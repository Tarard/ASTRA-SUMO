from pathlib import Path
import xml.etree.ElementTree as ET

from torii_sumo.core.tls_aggregation import (
    build_tls_aggregation_variant,
    build_tls_low_vehicle_control_variant,
    build_tls_non_controller_junction_demotion_variant,
    build_tls_signal_grouping_variant,
    demote_tls_ids,
)


def _command_path(command: list[str], option: str, cwd: Path) -> Path:
    path = Path(command[command.index(option) + 1])
    return path if path.is_absolute() else cwd / path


def test_demote_tls_ids_preserves_other_controller_on_shared_junction(tmp_path: Path) -> None:
    net_file = tmp_path / "shared.net.xml"
    net_file.write_text(
        "<net>"
        '<junction id="junction" type="traffic_light"/>'
        '<tlLogic id="official"/><tlLogic id="osm"/>'
        '<connection from="a" to="b" via=":junction_0_0" '
        'tl="official" linkIndex="0"/>'
        '<connection from="c" to="d" via=":junction_1_0" '
        'tl="osm" linkIndex="0" linkIndex2="1"/>'
        "</net>",
        encoding="utf-8",
    )

    report = demote_tls_ids(net_file, ["osm"])

    root = ET.parse(net_file).getroot()
    logics = {logic.attrib["id"] for logic in root.findall("tlLogic")}
    connections = root.findall("connection")
    assert logics == {"official"}
    assert connections[0].attrib["tl"] == "official"
    assert "tl" not in connections[1].attrib
    assert "linkIndex" not in connections[1].attrib
    assert "linkIndex2" not in connections[1].attrib
    assert root.find("junction").attrib["type"] == "traffic_light"
    assert report["tls_demotion_selected_controller_ids"] == ["osm"]
    assert report["tls_demotion_removed_connection_count"] == 1
    assert report["tls_demotion_removed_tllogic_ids"] == ["osm"]
    assert report["tls_low_vehicle_control_removed_connection_count"] == 1


def test_build_tls_aggregation_variant_sets_one_real_junction_per_tls_cluster(tmp_path: Path) -> None:
    net_file = tmp_path / "candidate.net.xml"
    clusters_file = tmp_path / "tls_clusters.csv"
    net_file.write_text(
        """<net>
  <tlLogic id="tlA" type="actuated" programID="0">
    <phase duration="30" minDur="10" maxDur="60" state="G"/>
  </tlLogic>
  <connection from="src_a" to="src_b" tl="tlA" linkIndex="0"/>
  <connection from="src_c" to="src_d" tl="tlA"/>
</net>""",
        encoding="utf-8",
    )
    clusters_file.write_text(
        "\n".join(
            [
                "cluster_id,tls_ids,tls_count,google_maps_url",
                "G001,tlA;tlB,2,https://maps.example/g1",
                "G002,tlC,1,https://maps.example/g2",
            ]
        ),
        encoding="utf-8",
    )
    calls: list[list[str]] = []

    def fake_command_runner(command, **kwargs):
        calls.append(command)
        output_file = _command_path(command, "--output-file", kwargs["cwd"])
        output_file.write_text(
            """<net>
  <junction id="n1" type="traffic_light"/>
  <junction id="n3" type="traffic_light"/>
  <tlLogic id="n1" type="static"/>
  <tlLogic id="n3" type="static"/>
  <connection from="a" to="b" tl="n1" linkIndex="0"/>
  <connection from="e" to="f" tl="n3" linkIndex="0"/>
  <connection from="c" to="d" tl="n1"/>
</net>""",
            encoding="utf-8",
        )
        return {"status": "pass", "returncode": 0, "stdout": "Success.", "stderr": "", "error": ""}

    report = build_tls_aggregation_variant(
        net_file=net_file,
        tls_audit_report={
            "status": "pass",
            "tls_cluster_count": 2,
            "clusters_file": str(clusters_file),
        },
        output_dir=tmp_path / "tls_aggregation",
        prefix="demo_tls",
        command_runner=fake_command_runner,
        controlled_nodes_by_tls_func=lambda _net_file: {
            "tlA": ["n1"],
            "tlB": ["n1", "n2"],
            "tlC": ["n3"],
        },
    )

    assert report["status"] == "pass"
    assert report["claim_status"] == "blocked"
    assert report["tls_aggregation_status"] == "variant_created_for_review"
    assert report["tls_physical_cluster_count"] == 2
    assert report["source_tl_logic_count"] == 1
    assert report["source_actuated_tl_logic_count"] == 1
    assert report["source_tls_phase_count"] == 1
    assert report["source_tls_phase_with_minmax_count"] == 1
    assert report["source_tls_controlled_connection_count"] == 1
    assert report["source_tl_connection_missing_linkindex_count"] == 1
    assert report["tls_program_policy"] == "discard_loaded_programs_rebuild_tls_set"
    assert any("discards loaded tlLogic" in warning for warning in report["warnings"])
    assert report["tls_aggregated_tl_logic_count"] == 2
    assert report["tls_aggregated_traffic_light_junction_count"] == 2
    assert report["tls_aggregated_controlled_connection_count"] == 2
    assert report["tls_aggregated_tl_connection_missing_linkindex_count"] == 1
    assert report["tls_controlled_connection_preservation_status"] == "pass"
    assert report["tls_controlled_connection_regression_count"] == 0
    assert Path(report["tls_aggregation_variant_file"]).is_file()
    assert Path(report["tls_aggregation_plan_file"]).is_file()
    command = calls[0]
    assert "--tls.discard-loaded" in command
    assert command[command.index("--tls.set") + 1] == "n1,n3"
    assert "--tls.rebuild" in command
    assert command[command.index("--tls.join-dist") + 1] == "20"
    assert "--tls.guess-signals" not in command
    assert "--tls.join" in command
    assert command[command.index("--tls.default-type") + 1] == "actuated"
    assert command[command.index("--sumo-net-file") + 1] == str(net_file.resolve())
    assert command[command.index("--output-file") + 1] == "tls_aggregated.net.xml"


def test_build_tls_aggregation_variant_can_enable_bounded_osm_signal_guessing(tmp_path: Path) -> None:
    net_file = tmp_path / "candidate.net.xml"
    clusters_file = tmp_path / "tls_clusters.csv"
    net_file.write_text("<net/>", encoding="utf-8")
    clusters_file.write_text(
        "\n".join(["cluster_id,tls_ids,tls_count,google_maps_url", "G001,tlA,1,https://maps.example/g1"]),
        encoding="utf-8",
    )
    calls: list[list[str]] = []

    def fake_command_runner(command, **kwargs):
        calls.append(command)
        _command_path(command, "--output-file", kwargs["cwd"]).write_text(
            """<net>
  <junction id="n1" type="traffic_light"/>
  <edge id=":n1_0" function="internal"><lane id=":n1_0_0" index="0"/></edge>
  <tlLogic id="n1" type="actuated" programID="0"><phase duration="30" state="G"/></tlLogic>
  <connection from="a" to="b" tl="n1" linkIndex="0" via=":n1_0_0"/>
</net>""",
            encoding="utf-8",
        )
        return {"status": "pass", "returncode": 0}

    report = build_tls_aggregation_variant(
        net_file=net_file,
        tls_audit_report={"status": "pass", "tls_cluster_count": 1, "clusters_file": str(clusters_file)},
        output_dir=tmp_path / "tls_aggregation",
        prefix="demo_tls",
        command_runner=fake_command_runner,
        controlled_nodes_by_tls_func=lambda _net_file: {"tlA": ["n1"]},
        tls_guess_signals_dist_m=35.0,
    )

    command = calls[0]
    assert command[command.index("--tls.guess-signals.dist") + 1] == "35"
    assert command.index("--tls.guess-signals") < command.index("--output-file")
    assert report["tls_guess_signals_dist_m"] == 35.0


def test_build_tls_aggregation_variant_demotes_traffic_light_junctions_without_controlled_links(tmp_path: Path) -> None:
    net_file = tmp_path / "candidate.net.xml"
    clusters_file = tmp_path / "tls_clusters.csv"
    net_file.write_text("<net/>", encoding="utf-8")
    clusters_file.write_text(
        "\n".join(["cluster_id,tls_ids,tls_count,google_maps_url", "G001,tlA,1,https://maps.example/g1"]),
        encoding="utf-8",
    )

    def fake_command_runner(command, **kwargs):
        _command_path(command, "--output-file", kwargs["cwd"]).write_text(
            """<net>
  <junction id="kept" type="traffic_light"/>
  <junction id="orphan" type="traffic_light"/>
  <edge id=":kept_0" function="internal"><lane id=":kept_0_0" index="0"/></edge>
  <tlLogic id="kept" type="actuated" programID="0"><phase duration="30" state="G"/></tlLogic>
  <tlLogic id="orphan" type="actuated" programID="0"><phase duration="30" state="G"/></tlLogic>
  <connection from="a" to="b" tl="kept" linkIndex="0" via=":kept_0_0"/>
</net>""",
            encoding="utf-8",
        )
        return {"status": "pass", "returncode": 0}

    report = build_tls_aggregation_variant(
        net_file=net_file,
        tls_audit_report={"status": "pass", "tls_cluster_count": 1, "clusters_file": str(clusters_file)},
        output_dir=tmp_path / "tls_aggregation",
        prefix="demo_tls",
        command_runner=fake_command_runner,
        controlled_nodes_by_tls_func=lambda _net_file: {"tlA": ["kept"]},
    )

    root = ET.parse(report["tls_aggregation_variant_file"]).getroot()

    assert root.find("junction[@id='orphan']").attrib["type"] == "priority"
    assert root.find("tlLogic[@id='orphan']") is None
    assert report["tls_orphan_traffic_light_junction_demoted_count"] == 1
    assert report["tls_uncontrolled_tllogic_removed_count"] == 1
    assert report["tls_aggregated_traffic_light_junction_count"] == 1
    assert report["tls_aggregated_tl_logic_count"] == 1


def test_build_tls_non_controller_junction_demotion_variant_preserves_shared_controller_links(
    tmp_path: Path,
) -> None:
    net_file = tmp_path / "candidate.net.xml"
    net_file.write_text(
        """<net>
  <junction id="main" type="traffic_light"/>
  <junction id="secondary" type="traffic_light"/>
  <edge id=":secondary_0" function="internal"><lane id=":secondary_0_0" index="0"/></edge>
  <tlLogic id="main" type="actuated" programID="0"><phase duration="30" state="G"/></tlLogic>
  <connection from="a" to="b" tl="main" linkIndex="0" via=":secondary_0_0"/>
</net>""",
        encoding="utf-8",
    )

    report = build_tls_non_controller_junction_demotion_variant(
        source_net_file=net_file,
        output_dir=tmp_path / "non_controller",
    )

    root = ET.parse(report["tls_non_controller_junction_demotion_variant_file"]).getroot()
    controlled_connection = root.find("connection[@from='a']")

    assert report["status"] == "pass"
    assert report["tls_non_controller_traffic_light_junction_demoted_count"] == 1
    assert report["tls_non_controller_traffic_light_junction_demoted_ids"] == ["secondary"]
    assert root.find("junction[@id='main']").attrib["type"] == "traffic_light"
    assert root.find("junction[@id='secondary']").attrib["type"] == "priority"
    assert root.find("tlLogic[@id='main']") is not None
    assert controlled_connection.attrib["tl"] == "main"
    assert controlled_connection.attrib["linkIndex"] == "0"


def test_build_tls_low_vehicle_control_variant_demotes_review_queue_entries(tmp_path: Path) -> None:
    net_file = tmp_path / "candidate.net.xml"
    net_file.write_text(
        """<net>
  <junction id="keep" type="traffic_light"/>
  <junction id="drop" type="traffic_light"/>
  <edge id=":keep_0" function="internal"><lane id=":keep_0_0" index="0"/></edge>
  <edge id=":drop_0" function="internal"><lane id=":drop_0_0" index="0"/></edge>
  <tlLogic id="tlKeep" type="actuated" programID="0"><phase duration="30" state="G"/></tlLogic>
  <tlLogic id="tlDrop" type="actuated" programID="0"><phase duration="30" state="G"/></tlLogic>
  <connection from="a" to="b" tl="tlKeep" linkIndex="0" via=":keep_0_0"/>
  <connection from="c" to="d" tl="tlDrop" linkIndex="0" linkIndex2="4" via=":drop_0_0"/>
</net>""",
        encoding="utf-8",
    )

    report = build_tls_low_vehicle_control_variant(
        source_net_file=net_file,
        tls_control_review_queue=[
            {
                "repair_category": "tls_reality_review",
                "review_type": "downgrade_low_vehicle_approach_tls",
                "tl_id": "tlDrop",
                "controlled_connection_count": 1,
                "controlled_passenger_from_edge_count": 1,
            }
        ],
        output_dir=tmp_path / "low_vehicle",
        max_removed_controlled_connections=1,
    )

    root = ET.parse(report["tls_low_vehicle_control_variant_file"]).getroot()
    dropped_connection = next(conn for conn in root.findall("connection") if conn.attrib.get("from") == "c")

    assert report["status"] == "pass"
    assert report["tls_low_vehicle_control_status"] == "variant_created_for_review"
    assert report["tls_low_vehicle_control_selected_tllogic_count"] == 1
    assert report["tls_low_vehicle_control_removed_connection_count"] == 1
    assert root.find("tlLogic[@id='tlDrop']") is None
    assert root.find("tlLogic[@id='tlKeep']") is not None
    assert root.find("junction[@id='drop']").attrib["type"] == "priority"
    assert root.find("junction[@id='keep']").attrib["type"] == "traffic_light"
    assert "tl" not in dropped_connection.attrib
    assert "linkIndex" not in dropped_connection.attrib
    assert "linkIndex2" not in dropped_connection.attrib


def test_build_tls_low_vehicle_control_variant_respects_selected_tllogic_limit(tmp_path: Path) -> None:
    net_file = tmp_path / "candidate.net.xml"
    net_file.write_text(
        """<net>
  <junction id="first" type="traffic_light"/>
  <junction id="second" type="traffic_light"/>
  <edge id=":first_0" function="internal"><lane id=":first_0_0" index="0"/></edge>
  <edge id=":second_0" function="internal"><lane id=":second_0_0" index="0"/></edge>
  <tlLogic id="tlFirst" type="actuated" programID="0"><phase duration="30" state="G"/></tlLogic>
  <tlLogic id="tlSecond" type="actuated" programID="0"><phase duration="30" state="G"/></tlLogic>
  <connection from="a" to="b" tl="tlFirst" linkIndex="0" via=":first_0_0"/>
  <connection from="c" to="d" tl="tlSecond" linkIndex="0" via=":second_0_0"/>
</net>""",
        encoding="utf-8",
    )

    report = build_tls_low_vehicle_control_variant(
        source_net_file=net_file,
        tls_control_review_queue=[
            {
                "review_type": "downgrade_low_vehicle_approach_tls",
                "tl_id": "tlFirst",
                "controlled_connection_count": 1,
                "controlled_passenger_from_edge_count": 1,
            },
            {
                "review_type": "downgrade_low_vehicle_approach_tls",
                "tl_id": "tlSecond",
                "controlled_connection_count": 1,
                "controlled_passenger_from_edge_count": 2,
            },
        ],
        output_dir=tmp_path / "low_vehicle",
        max_selected_tllogic_count=1,
    )

    root = ET.parse(report["tls_low_vehicle_control_variant_file"]).getroot()

    assert report["tls_low_vehicle_control_selected_tllogic_count"] == 1
    assert root.find("tlLogic[@id='tlFirst']") is None
    assert root.find("tlLogic[@id='tlSecond']") is not None


def test_build_tls_aggregation_variant_deduplicates_representatives_in_tls_set(tmp_path: Path) -> None:
    net_file = tmp_path / "candidate.net.xml"
    clusters_file = tmp_path / "tls_clusters.csv"
    net_file.write_text("<net/>", encoding="utf-8")
    clusters_file.write_text(
        "\n".join(
            [
                "cluster_id,tls_ids,tls_count,google_maps_url",
                "G001,tlA,1,https://maps.example/g1",
                "G002,tlB,1,https://maps.example/g2",
            ]
        ),
        encoding="utf-8",
    )
    calls: list[list[str]] = []

    def fake_command_runner(command, **kwargs):
        calls.append(command)
        _command_path(command, "--output-file", kwargs["cwd"]).write_text(
            """<net>
  <junction id="n1" type="traffic_light"/>
  <edge id=":n1_0" function="internal"><lane id=":n1_0_0" index="0"/></edge>
  <tlLogic id="n1" type="actuated" programID="0"><phase duration="30" state="G"/></tlLogic>
  <connection from="a" to="b" tl="n1" linkIndex="0" via=":n1_0_0"/>
</net>""",
            encoding="utf-8",
        )
        return {"status": "pass", "returncode": 0}

    build_tls_aggregation_variant(
        net_file=net_file,
        tls_audit_report={"status": "pass", "tls_cluster_count": 2, "clusters_file": str(clusters_file)},
        output_dir=tmp_path / "tls_aggregation",
        prefix="demo_tls",
        command_runner=fake_command_runner,
        controlled_nodes_by_tls_func=lambda _net_file: {"tlA": ["n1"], "tlB": ["n1"]},
    )

    assert calls[0][calls[0].index("--tls.set") + 1] == "n1"


def test_build_tls_aggregation_variant_prunes_nearby_representatives_before_tls_join(tmp_path: Path) -> None:
    net_file = tmp_path / "candidate.net.xml"
    clusters_file = tmp_path / "tls_clusters.csv"
    net_file.write_text(
        """<net>
  <junction id="n1" x="0" y="0" type="traffic_light"/>
  <junction id="n2" x="30" y="0" type="traffic_light"/>
</net>""",
        encoding="utf-8",
    )
    clusters_file.write_text(
        "\n".join(
            [
                "cluster_id,tls_ids,tls_count,google_maps_url",
                "G001,tlA,1,https://maps.example/g1",
                "G002,tlB,1,https://maps.example/g2",
            ]
        ),
        encoding="utf-8",
    )
    calls: list[list[str]] = []

    def fake_command_runner(command, **kwargs):
        calls.append(command)
        _command_path(command, "--output-file", kwargs["cwd"]).write_text(
            """<net>
  <junction id="n1" type="traffic_light"/>
  <edge id=":n1_0" function="internal"><lane id=":n1_0_0" index="0"/></edge>
  <tlLogic id="n1" type="actuated" programID="0"><phase duration="30" state="G"/></tlLogic>
  <connection from="a" to="b" tl="n1" linkIndex="0" via=":n1_0_0"/>
</net>""",
            encoding="utf-8",
        )
        return {"status": "pass", "returncode": 0}

    report = build_tls_aggregation_variant(
        net_file=net_file,
        tls_audit_report={"status": "pass", "tls_cluster_count": 2, "clusters_file": str(clusters_file)},
        output_dir=tmp_path / "tls_aggregation",
        prefix="demo_tls",
        command_runner=fake_command_runner,
        controlled_nodes_by_tls_func=lambda _net_file: {"tlA": ["n1"], "tlB": ["n2"]},
    )

    assert calls[0][calls[0].index("--tls.set") + 1] == "n1"
    assert calls[0][calls[0].index("--tls.join-dist") + 1] == "20"
    assert report["tls_set_representative_count"] == 1
    assert report["tls_set_spatially_pruned_count"] == 1
    assert report["tls_set_spatially_pruned_representatives"] == [
        {"representative_node_id": "n2", "kept_representative_node_id": "n1", "distance_m": 30.0}
    ]


def test_build_tls_signal_grouping_variant_limits_identical_signal_column_merges(tmp_path: Path) -> None:
    source_net_file = tmp_path / "source.net.xml"
    source_net_file.write_text(
        """<net>
  <junction id="n1" type="traffic_light"/>
  <edge id=":n1_0" function="internal"><lane id=":n1_0_0" index="0"/></edge>
  <tlLogic id="n1" type="actuated" programID="0">
    <phase duration="30" state="GGGr"/>
    <phase duration="4" state="yyyr"/>
  </tlLogic>
  <connection from="a" to="b" tl="n1" linkIndex="0" via=":n1_0_0"/>
  <connection from="c" to="d" tl="n1" linkIndex="1" via=":n1_0_0"/>
  <connection from="e" to="f" tl="n1" linkIndex="2" via=":n1_0_0"/>
  <connection from="g" to="h" tl="n1" linkIndex="3" via=":n1_0_0"/>
</net>""",
        encoding="utf-8",
    )

    report = build_tls_signal_grouping_variant(
        source_net_file=source_net_file,
        output_dir=tmp_path / "signal_grouping",
        prefix="demo_signal_grouping",
        max_shared_linkindex_groups=1,
    )

    root = ET.parse(report["tls_signal_grouping_variant_file"]).getroot()
    phases = root.findall("tlLogic[@id='n1']/phase")
    link_indexes = [connection.attrib["linkIndex"] for connection in root.findall("connection")]

    assert Path(report["tls_signal_grouping_variant_file"]).name == "tls_signal_grouped.net.xml"
    assert [phase.attrib["state"] for phase in phases] == ["Gr", "yr"]
    assert link_indexes == ["0", "0", "0", "1"]
    assert report["tls_signal_grouping_merged_group_count"] == 1
    assert report["tls_signal_grouping_remapped_connection_count"] == 3
    assert report["tls_aggregated_controlled_connection_count"] == 4


def test_signal_grouping_keeps_foe_requests_separate_and_persists_multilink_control_key(
    tmp_path: Path,
) -> None:
    source_net_file = tmp_path / "foe_source.net.xml"
    source_net_file.write_text(
        """<net>
  <edge id="in_a" from="a" to="j"><lane id="in_a_0" index="0" allow="passenger"/></edge>
  <edge id="in_b" from="b" to="j"><lane id="in_b_0" index="0" allow="passenger"/></edge>
  <edge id="in_c" from="c" to="j"><lane id="in_c_0" index="0" allow="passenger"/></edge>
  <edge id="out_a" from="j" to="d"><lane id="out_a_0" index="0" allow="passenger"/></edge>
  <edge id="out_b" from="j" to="e"><lane id="out_b_0" index="0" allow="passenger"/></edge>
  <edge id="out_c" from="j" to="f"><lane id="out_c_0" index="0" allow="passenger"/></edge>
  <junction id="a" type="dead_end"/><junction id="b" type="dead_end"/>
  <junction id="c" type="dead_end"/>
  <junction id="j" type="traffic_light" incLanes="in_a_0 in_b_0 in_c_0">
    <request index="0" response="000" foes="010" cont="0"/>
    <request index="1" response="000" foes="001" cont="0"/>
    <request index="2" response="000" foes="000" cont="0"/>
  </junction>
  <junction id="d" type="dead_end"/><junction id="e" type="dead_end"/>
  <junction id="f" type="dead_end"/>
  <tlLogic id="j" type="static" programID="0">
    <phase duration="1" state="ggg"/>
    <phase duration="1" state="rrr"/>
  </tlLogic>
  <connection from="in_a" to="out_a" fromLane="0" toLane="0" tl="j" linkIndex="0"/>
  <connection from="in_b" to="out_b" fromLane="0" toLane="0" tl="j" linkIndex="1"/>
  <connection from="in_c" to="out_c" fromLane="0" toLane="0" tl="j" linkIndex="2"/>
</net>""",
        encoding="utf-8",
    )
    control_keys = {
        ("j", "in_a", "out_a", "0", "0"): "expression-A",
        ("j", "in_b", "out_b", "0", "0"): "expression-A",
        ("j", "in_c", "out_c", "0", "0"): "expression-A",
    }

    report = build_tls_signal_grouping_variant(
        source_net_file=source_net_file,
        output_dir=tmp_path / "foe_grouping",
        max_shared_linkindex_groups=1,
        control_key_by_connection=control_keys,
    )

    grouped_root = ET.parse(report["tls_signal_grouping_variant_file"]).getroot()
    assert [
        connection.attrib["linkIndex"] for connection in grouped_root.findall("connection")
    ] == ["0", "1", "0"]
    assert [
        phase.attrib["state"] for phase in grouped_root.findall("tlLogic[@id='j']/phase")
    ] == ["gg", "rr"]
    assert report["tls_signal_grouping_request_foe_evidence_status"] == "available"
    assert report["tls_signal_grouping_blocked_foe_pair_count"] == 1
    assert report["tls_signal_grouping_control_key_to_link_indices"] == {
        "j": {"expression-A": [0, 1]}
    }
    assert report["tls_signal_grouping_control_bindings"] == [
        {
            "controller_id": "j",
            "control_key": "expression-A",
            "phase_signature": ["g", "r"],
            "source_link_indices": [0, 1, 2],
            "sumo_link_indices": [0, 1],
            "physical_connection_count": 3,
            "foe_separated": True,
        }
    ]


def test_build_tls_aggregation_variant_reports_controlled_connection_regression(tmp_path: Path) -> None:
    net_file = tmp_path / "candidate.net.xml"
    clusters_file = tmp_path / "tls_clusters.csv"
    net_file.write_text(
        """<net>
  <tlLogic id="tlA" type="actuated" programID="0"><phase duration="30" state="GG"/></tlLogic>
  <connection from="a" to="b" tl="tlA" linkIndex="0"/>
  <connection from="c" to="d" tl="tlA" linkIndex="1"/>
</net>""",
        encoding="utf-8",
    )
    clusters_file.write_text(
        "\n".join(["cluster_id,tls_ids,tls_count,google_maps_url", "G001,tlA,1,https://maps.example/g1"]),
        encoding="utf-8",
    )

    def fake_command_runner(command, **kwargs):
        _command_path(command, "--output-file", kwargs["cwd"]).write_text(
            """<net>
  <junction id="n1" type="traffic_light"/>
  <tlLogic id="n1" type="actuated" programID="0"><phase duration="30" state="G"/></tlLogic>
  <connection from="a" to="b" tl="n1" linkIndex="0"/>
</net>""",
            encoding="utf-8",
        )
        return {"status": "pass", "returncode": 0}

    report = build_tls_aggregation_variant(
        net_file=net_file,
        tls_audit_report={"status": "pass", "tls_cluster_count": 1, "clusters_file": str(clusters_file)},
        output_dir=tmp_path / "tls_aggregation",
        prefix="demo_tls",
        command_runner=fake_command_runner,
        controlled_nodes_by_tls_func=lambda _net_file: {"tlA": ["n1"]},
    )

    assert report["tls_controlled_connection_preservation_status"] == "fail"
    assert report["tls_controlled_connection_regression_count"] == 1
    assert any("controlled TLS connections" in warning for warning in report["warnings"])


def test_build_tls_aggregation_variant_preserves_compatible_actuated_program(tmp_path: Path) -> None:
    net_file = tmp_path / "candidate.net.xml"
    clusters_file = tmp_path / "tls_clusters.csv"
    net_file.write_text(
        """<net>
  <tlLogic id="tlA" type="actuated" programID="0" offset="5">
    <phase duration="30" minDur="10" maxDur="60" state="G"/>
  </tlLogic>
</net>""",
        encoding="utf-8",
    )
    clusters_file.write_text(
        "\n".join(["cluster_id,tls_ids,tls_count,google_maps_url", "G001,tlA,1,https://maps.example/g1"]),
        encoding="utf-8",
    )

    def fake_command_runner(command, **kwargs):
        output_file = _command_path(command, "--output-file", kwargs["cwd"])
        output_file.write_text(
            """<net>
  <junction id="n1" type="traffic_light"/>
  <edge id=":n1_0" function="internal"><lane id=":n1_0_0" index="0"/></edge>
  <tlLogic id="n1" type="static" programID="0" offset="0">
    <phase duration="1" state="r"/>
  </tlLogic>
  <connection from="a" to="b" tl="n1" linkIndex="0" via=":n1_0_0"/>
</net>""",
            encoding="utf-8",
        )
        return {"status": "pass", "returncode": 0}

    report = build_tls_aggregation_variant(
        net_file=net_file,
        tls_audit_report={"status": "pass", "tls_cluster_count": 1, "clusters_file": str(clusters_file)},
        output_dir=tmp_path / "tls_aggregation",
        prefix="demo_tls",
        command_runner=fake_command_runner,
        controlled_nodes_by_tls_func=lambda _net_file: {"tlA": ["n1"]},
    )

    root = ET.parse(report["tls_aggregation_variant_file"]).getroot()
    target_tls = root.find("tlLogic[@id='n1']")
    phase = target_tls.find("phase")
    assert report["tls_program_preserved_count"] == 1
    assert report["tls_program_skipped_count"] == 0
    assert target_tls.attrib["type"] == "actuated"
    assert target_tls.attrib["offset"] == "5"
    assert phase.attrib == {"duration": "30", "minDur": "10", "maxDur": "60", "state": "G"}


def test_build_tls_aggregation_variant_resolves_relative_paths(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    Path("candidate.net.xml").write_text("<net/>", encoding="utf-8")
    Path("clusters.csv").write_text(
        "\n".join(["cluster_id,tls_ids,tls_count,google_maps_url", "G001,tlA,1,https://maps.example/g1"]),
        encoding="utf-8",
    )
    calls: list[list[str]] = []

    def fake_command_runner(command, **kwargs):
        calls.append(command)
        assert kwargs["cwd"] == tmp_path / "tls_aggregation"
        _command_path(command, "--output-file", kwargs["cwd"]).write_text("<net/>", encoding="utf-8")
        return {"status": "pass", "returncode": 0}

    report = build_tls_aggregation_variant(
        net_file=Path("candidate.net.xml"),
        tls_audit_report={"status": "pass", "tls_cluster_count": 1, "clusters_file": "clusters.csv"},
        output_dir=Path("tls_aggregation"),
        prefix="demo_tls",
        command_runner=fake_command_runner,
        controlled_nodes_by_tls_func=lambda _net_file: {"tlA": ["n1"]},
    )

    assert report["status"] == "pass"
    assert calls[0][calls[0].index("--sumo-net-file") + 1] == str(tmp_path / "candidate.net.xml")
    assert calls[0][calls[0].index("--output-file") + 1] == "tls_aggregated.net.xml"


def test_build_tls_aggregation_variant_skips_when_no_tls_clusters(tmp_path: Path) -> None:
    net_file = tmp_path / "candidate.net.xml"
    net_file.write_text("<net/>", encoding="utf-8")

    report = build_tls_aggregation_variant(
        net_file=net_file,
        tls_audit_report={"status": "pass", "tls_cluster_count": 0},
        output_dir=tmp_path / "tls_aggregation",
        prefix="demo_tls",
        command_runner=lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("should not run netconvert")),
        controlled_nodes_by_tls_func=lambda _net_file: {},
    )

    assert report["status"] == "pass"
    assert report["tls_aggregation_status"] == "not_needed"
    assert report["tls_physical_cluster_count"] == 0
    assert report["tls_aggregation_variant_file"] == ""


def test_build_tls_aggregation_variant_reports_controlled_node_parse_failure(tmp_path: Path) -> None:
    net_file = tmp_path / "candidate.net.xml"
    clusters_file = tmp_path / "tls_clusters.csv"
    net_file.write_text("<net/>", encoding="utf-8")
    clusters_file.write_text(
        "\n".join(["cluster_id,tls_ids,tls_count,google_maps_url", "G001,tlA,1,https://maps.example/g1"]),
        encoding="utf-8",
    )

    report = build_tls_aggregation_variant(
        net_file=net_file,
        tls_audit_report={"status": "pass", "tls_cluster_count": 1, "clusters_file": str(clusters_file)},
        output_dir=tmp_path / "tls_aggregation",
        prefix="demo_tls",
        command_runner=lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("should not run netconvert")),
        controlled_nodes_by_tls_func=lambda _net_file: (_ for _ in ()).throw(RuntimeError("bad TLS programs")),
    )

    assert report["status"] == "fail"
    assert report["tls_aggregation_status"] == "failed"
    assert "could not derive TLS-controlled junctions" in report["error"]
    assert report["tls_aggregation_variant_file"] == ""
