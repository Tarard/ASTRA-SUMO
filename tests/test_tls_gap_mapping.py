from pathlib import Path

from torii_sumo.core.tls_gap_mapping import (
    audit_tls_gap_variant_semantics,
    build_tls_gap_destination_mapping,
    build_tls_gap_repair_variant,
    build_tls_repair_decision_report,
)


def test_tls_gap_destination_mapping_finds_split_root_candidate_target(tmp_path: Path) -> None:
    reference = tmp_path / "reference.net.xml"
    candidate = tmp_path / "candidate.net.xml"
    reference.write_text(
        """<net>
  <edge id="r_in" from="r0" to="rj"><lane id="r_in_0" index="0" allow="passenger"/></edge>
  <edge id="road_out#2" from="rj" to="r2"><lane id="road_out#2_0" index="0" allow="passenger"/></edge>
  <connection from="r_in" to="road_out#2" fromLane="0" toLane="0" dir="l" tl="ref_tls" linkIndex="0"/>
  <tlLogic id="ref_tls" type="static" programID="0"><phase duration="30" state="rG"/></tlLogic>
</net>""",
        encoding="utf-8",
    )
    candidate.write_text(
        """<net>
  <edge id="c_in" from="c0" to="cj"><lane id="c_in_0" index="0" allow="passenger"/></edge>
  <edge id="road_out#0" from="cj" to="c2"><lane id="road_out#0_0" index="0" allow="passenger"/></edge>
  <tlLogic id="cand_tls" type="static" programID="0"><phase duration="30" state="r"/></tlLogic>
</net>""",
        encoding="utf-8",
    )
    alignment = {
        "tls_controller_alignment": {
            "high_confidence_movement_gap_queue": [
                {
                    "reference_tl_id": "ref_tls",
                    "candidate_tl_id": "cand_tls",
                    "reference_edge_id": "r_in",
                    "candidate_edge_id": "c_in",
                    "missing_direction_counts": {"l": 1},
                }
            ]
        }
    }

    report = build_tls_gap_destination_mapping(
        reference_net_file=reference,
        candidate_net_file=candidate,
        alignment_report=alignment,
        output_dir=tmp_path / "mapping",
    )

    assert report["status"] == "pass"
    assert report["repair_safe"] is False
    assert report["mapped_gap_count"] == 1
    assert report["destination_edge_and_endpoint_mapped_count"] == 1
    assert report["passenger_lane_rank_mapped_count"] == 1
    mapping = report["records"][0]["missing_connections"][0]
    assert mapping["candidate_destination_edge_id"] == "road_out#0"
    assert mapping["mapping_status"] == "destination_edge_and_endpoint_mapped"
    assert mapping["lane_mapping_status"] == "passenger_lane_rank_mapped"
    assert Path(report["report_file"]).is_file()


def test_tls_gap_mapping_matches_passenger_lane_rank_across_raw_index_changes(tmp_path: Path) -> None:
    reference = tmp_path / "reference_rank.net.xml"
    candidate = tmp_path / "candidate_rank.net.xml"
    reference.write_text(
        """<net>
  <edge id="r_in" from="r0" to="rj"><lane id="r_in_0" index="0"/><lane id="r_in_2" index="2"/><lane id="r_in_3" index="3"/></edge>
  <edge id="r_out" from="rj" to="r2"><lane id="r_out_0" index="0"/><lane id="r_out_2" index="2"/><lane id="r_out_3" index="3"/></edge>
  <connection from="r_in" to="r_out" fromLane="2" toLane="2" dir="s" tl="ref_tls" linkIndex="0"/>
  <tlLogic id="ref_tls" type="static" programID="0"><phase duration="30" state="G"/></tlLogic>
</net>""",
        encoding="utf-8",
    )
    candidate.write_text(
        """<net>
  <edge id="c_in" from="c0" to="cj"><lane id="c_in_0" index="0"/><lane id="c_in_1" index="1"/></edge>
  <edge id="r_out#0" from="cj" to="c2"><lane id="c_out_0" index="0"/><lane id="c_out_1" index="1"/></edge>
  <connection from="c_in" to="r_out#0" fromLane="1" toLane="1" dir="s" tl="cand_tls" linkIndex="0"/>
  <tlLogic id="cand_tls" type="static" programID="0"><phase duration="30" state="G"/></tlLogic>
</net>""",
        encoding="utf-8",
    )

    report = build_tls_gap_destination_mapping(
        reference_net_file=reference,
        candidate_net_file=candidate,
        alignment_report={
            "tls_controller_alignment": {
                "high_confidence_movement_gap_queue": [
                    {
                        "reference_tl_id": "ref_tls",
                        "candidate_tl_id": "cand_tls",
                        "reference_edge_id": "r_in",
                        "candidate_edge_id": "c_in",
                    }
                ]
            }
        },
        output_dir=tmp_path / "rank_mapping",
    )

    assert report["missing_connection_count"] == 0
    assert report["mapped_gap_count"] == 0


def test_tls_repair_decision_blocks_when_mapping_is_incomplete(tmp_path: Path) -> None:
    report = build_tls_repair_decision_report(
        mapping_report={
            "report_file": "mapping.json",
            "missing_connection_count": 3,
            "destination_edge_and_endpoint_mapped_count": 1,
            "destination_edge_endpoint_mapped_lane_review_count": 1,
            "destination_root_mapped_endpoint_review_count": 1,
            "unmapped_destination_edge_count": 1,
            "passenger_lane_rank_mapped_count": 1,
        },
        output_dir=tmp_path,
    )

    assert report["status"] == "blocked"
    assert report["decision"] == "do_not_apply"
    assert report["repair_variant_status"] == "not_created"
    assert report["main_network_mutated"] is False
    assert len(report["blocked_reasons"]) >= 4
    assert Path(report["report_file"]).is_file()


def test_tls_gap_repair_variant_writes_exact_mapping_patch_without_mutating_source(tmp_path: Path) -> None:
    candidate = tmp_path / "candidate.net.xml"
    candidate.write_text(
        """<net>
  <edge id="c_in" from="c0" to="cj"><lane id="c_in_0" index="0" allow="passenger"/></edge>
  <edge id="road_out#0" from="cj" to="c2"><lane id="road_out#0_0" index="0" allow="passenger"/></edge>
  <tlLogic id="cand_tls" type="static" programID="0"><phase duration="30" state="r"/></tlLogic>
</net>""",
        encoding="utf-8",
    )
    before = candidate.read_bytes()

    def fake_command(command, *, cwd, timeout_seconds):
        output = Path(command[command.index("--output-file") + 1])
        output.write_bytes(candidate.read_bytes())
        return {"status": "pass", "returncode": 0, "command": command}

    report = build_tls_gap_repair_variant(
        mapping_report={
            "records": [
                {
                    "reference_tl_id": "ref_tls",
                    "candidate_tl_id": "cand_tls",
                    "candidate_from_edge_id": "c_in",
                    "missing_connections": [
                        {
                            "reference_connection_index": 0,
                            "reference_direction": "l",
                            "candidate_destination_edge_id": "road_out#0",
                            "candidate_from_lane": "0",
                            "candidate_to_lane": "0",
                            "mapping_status": "destination_edge_and_endpoint_mapped",
                            "lane_mapping_status": "passenger_lane_rank_mapped",
                        }
                    ],
                }
            ]
        },
        candidate_net_file=candidate,
        output_dir=tmp_path / "variant",
        command_runner=fake_command,
    )

    assert report["status"] == "pass"
    assert report["repair_variant_status"] == "created_for_review"
    assert report["selected_connection_count"] == 1
    assert report["main_network_mutated"] is False
    assert Path(report["patch_file"]).is_file()
    assert Path(report["variant_file"]).is_file()
    assert candidate.read_bytes() == before


def test_tls_repair_decision_keeps_variant_blocked_when_reference_delta_fails(tmp_path: Path) -> None:
    report = build_tls_repair_decision_report(
        mapping_report={
            "report_file": "mapping.json",
            "missing_connection_count": 1,
            "destination_edge_and_endpoint_mapped_count": 1,
            "passenger_lane_rank_mapped_count": 1,
        },
        variant_report={
            "status": "pass",
            "repair_variant_status": "created_for_review",
            "variant_file": "variant.net.xml",
            "report_file": "variant.json",
            "variant_phase_capacity_status": "pass",
        },
        sumo_load_report={"status": "pass"},
        semantic_report={"status": "pass", "network_structural_delta_status": "fail"},
        output_dir=tmp_path,
    )

    assert report["repair_variant_status"] == "created_for_review"
    assert report["repair_variant_reference_parity_status"] == "fail"
    assert report["status"] == "blocked"
    assert any("semantic audit failed" in reason for reason in report["blocked_reasons"])


def test_tls_gap_variant_semantic_audit_proves_direction_linkindex_and_via(tmp_path: Path) -> None:
    candidate = tmp_path / "candidate.net.xml"
    variant = tmp_path / "variant.net.xml"
    candidate.write_text(
        """<net>
  <edge id="c_in" from="c0" to="cj"><lane id="c_in_0" index="0"/></edge>
  <edge id="road_out" from="cj" to="c2"><lane id="road_out_0" index="0"/></edge>
  <tlLogic id="cand_tls" type="static" programID="0"><phase duration="30" state="r"/></tlLogic>
</net>""",
        encoding="utf-8",
    )
    variant.write_text(
        """<net>
  <edge id="c_in" from="c0" to="cj"><lane id="c_in_0" index="0"/></edge>
  <edge id="road_out" from="cj" to="c2"><lane id="road_out_0" index="0"/></edge>
  <edge id=":cand_tls_0" function="internal"><lane id=":cand_tls_0_0" index="0"/></edge>
  <connection from="c_in" to="road_out" fromLane="0" toLane="0" dir="l" tl="cand_tls" linkIndex="0" via=":cand_tls_0"/>
  <tlLogic id="cand_tls" type="static" programID="0"><phase duration="30" state="G"/></tlLogic>
</net>""",
        encoding="utf-8",
    )

    report = audit_tls_gap_variant_semantics(
        mapping_report={"report_file": "mapping.json"},
        variant_report={
            "status": "pass",
            "report_file": "variant.json",
            "selected_connections": [
                {
                    "candidate_tl_id": "cand_tls",
                    "candidate_from_edge_id": "c_in",
                    "candidate_destination_edge_id": "road_out",
                    "candidate_from_lane": "0",
                    "candidate_to_lane": "0",
                    "direction": "l",
                }
            ],
        },
        candidate_net_file=candidate,
        variant_net_file=variant,
        output_dir=tmp_path / "semantic",
    )

    assert report["status"] == "pass"
    assert report["direction_parity_status"] == "pass"
    assert report["phase_linkindex_parity_status"] == "pass"
    assert report["via_parity_status"] == "pass"
    assert Path(report["report_file"]).is_file()


def test_tls_repair_decision_can_pass_only_after_all_variant_gates_pass(tmp_path: Path) -> None:
    report = build_tls_repair_decision_report(
        mapping_report={
            "report_file": "mapping.json",
            "missing_connection_count": 1,
            "destination_edge_and_endpoint_mapped_count": 1,
            "passenger_lane_rank_mapped_count": 1,
        },
        variant_report={
            "status": "pass",
            "repair_variant_status": "created_for_review",
            "variant_file": "variant.net.xml",
            "report_file": "variant.json",
            "variant_phase_capacity_status": "pass",
        },
        sumo_load_report={"status": "pass"},
        semantic_report={"status": "pass", "network_structural_delta_status": "pass"},
        tls_variant_semantic_report={"status": "pass", "report_file": "semantic.json"},
        output_dir=tmp_path,
    )

    assert report["status"] == "pass"
    assert report["repair_safe"] is True
    assert report["decision"] == "promote_variant"
