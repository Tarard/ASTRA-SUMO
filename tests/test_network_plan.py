import json
from pathlib import Path
import xml.etree.ElementTree as ET

from torii_sumo.core.network_permissions import apply_service_passenger_permissions
from torii_sumo.core.network_plan import derive_network_plan
from torii_sumo.core.osm_workflow import (
    _junction_semantic_gate,
    _corridor_geometry_simplification_promotion_decision,
    _low_vehicle_control_candidate_limits,
    _movement_rebuild_reference_delta_promotion_decision,
    _reference_delta_promotion_decision,
    _reference_hierarchy_type_repair_promotion_decision,
    _scope_pruning_promotion_decision,
    _followup_reference_delta_structural_only,
    _teacher_guided_seed_candidate,
    _reference_join_audit_can_seed_teacher_guided_queue,
    _restore_followup_internal_regressions,
    _sumo_load_net,
    _filter_teacher_guided_queue_to_mismatch_fields,
    _teacher_guided_junction_parity_gate,
    _teacher_guided_application_stats,
    _teacher_guided_best_variant_file,
    _teacher_guided_direct_replay_needed,
    _teacher_guided_equivalent_approach_edge_map,
    _run_direct_local_teacher_replay,
    run_scoped_teacher_tls_cell_batch,
    _safe_path_part,
    _tls_connection_repair_promotion_decision,
    export_plain_net_for_teacher_guided_repair,
)
from torii_sumo.core.reference_bbox import derive_reference_net_bbox


def test_corridor_geometry_simplification_promotes_only_monotonic_candidate() -> None:
    variant = {
        "status": "pass",
        "semantic_preservation_status": "pass",
        "alias_normalized_connection_audit": {
            "normal_missing_count": 0,
            "normal_extra_count": 0,
            "controlled_missing_count": 0,
            "controlled_extra_count": 0,
        },
    }
    baseline_delta = {
        "status": "pass",
        "network_structural_missing_counts": {"tls_controlled_connection_count": 50},
        "network_structural_extra_counts": {"tl_logic_count": 16, "traffic_light_junction_count": 16},
    }
    candidate_delta = {
        "status": "pass",
        "network_structural_missing_counts": {"tls_controlled_connection_count": 50},
        "network_structural_extra_counts": {"tl_logic_count": 5, "traffic_light_junction_count": 16},
    }
    baseline_topology = {
        "suspicious_cluster_count": 158,
        "junction_aggregation_candidate_count": 94,
        "physical_intersection_candidate_count": 65,
        "topology_connection_cell_candidate_count": 64,
        "max_cluster_node_count": 122,
    }
    candidate_topology = {**baseline_topology, "topology_connection_cell_candidate_count": 61}

    decision = _corridor_geometry_simplification_promotion_decision(
        variant_report=variant,
        sumo_load_report={"status": "pass"},
        baseline_delta_report=baseline_delta,
        candidate_delta_report=candidate_delta,
        baseline_topology_report=baseline_topology,
        candidate_topology_report=candidate_topology,
    )

    assert decision["status"] == "pass"
    assert decision["reason"] == "corridor_geometry_simplification_promoted_by_semantic_and_topology_gates"
    assert decision["candidate_tls_semantic_delta_score"] < decision["baseline_tls_semantic_delta_score"]


def test_scope_pruning_blocks_controlled_tls_regression(tmp_path: Path) -> None:
    source = tmp_path / "source.net.xml"
    variant = tmp_path / "variant.net.xml"
    source.write_text(
        '<net><connection from="in" to="out" tl="tls" linkIndex="0"/></net>',
        encoding="utf-8",
    )
    variant.write_text('<net><connection from="in" to="out"/></net>', encoding="utf-8")

    decision = _scope_pruning_promotion_decision(
        pruning_report={
            "status": "pass",
            "scope_pruning_netconvert": {"status": "pass"},
            "scope_pruning_modal_only_status": "pass",
            "scope_pruning_modal_leaf_continuity_status": "pass",
            "scope_pruning_vehicle_core_impact_status": "pass",
        },
        post_scope_report={"status": "pass"},
        sumo_load_report={"status": "pass"},
        source_net_file=source,
        variant_net_file=variant,
    )

    assert decision["status"] == "blocked"
    assert decision["checks"]["controlled_tls_connection_preservation"] == "blocked"
    assert decision["controlled_connection_regression_count"] == 1


def test_corridor_geometry_simplification_blocks_topology_regression() -> None:
    variant = {
        "status": "pass",
        "semantic_preservation_status": "pass",
        "alias_normalized_connection_audit": {
            "normal_missing_count": 0,
            "normal_extra_count": 0,
            "controlled_missing_count": 0,
            "controlled_extra_count": 0,
        },
    }
    delta = {"status": "pass", "network_structural_missing_counts": {}, "network_structural_extra_counts": {}}
    baseline_topology = {
        "suspicious_cluster_count": 10,
        "junction_aggregation_candidate_count": 5,
        "physical_intersection_candidate_count": 3,
        "topology_connection_cell_candidate_count": 2,
        "max_cluster_node_count": 8,
    }

    decision = _corridor_geometry_simplification_promotion_decision(
        variant_report=variant,
        sumo_load_report={"status": "pass"},
        baseline_delta_report=delta,
        candidate_delta_report=delta,
        baseline_topology_report=baseline_topology,
        candidate_topology_report={**baseline_topology, "physical_intersection_candidate_count": 4},
    )

    assert decision["status"] == "blocked"
    assert decision["reason"] == "corridor_topology_regressed"


def test_reference_hierarchy_type_repair_promotes_strict_partial_improvement() -> None:
    decision = _reference_hierarchy_type_repair_promotion_decision(
        baseline_audit_report={
            "high_hierarchy_issue_count": 66,
            "decision_counts": {
                "aligned": 296,
                "matched_but_oversplit": 46,
                "type_hierarchy_mismatch": 20,
            },
        },
        candidate_audit_report={
            "status": "blocked",
            "reference_hierarchy_status": "needs_review",
            "high_hierarchy_issue_count": 51,
            "decision_counts": {
                "aligned": 311,
                "matched_but_oversplit": 50,
                "type_hierarchy_mismatch": 1,
            },
        },
        sumo_load_report={"status": "pass"},
    )

    assert decision["status"] == "pass"
    assert decision["reason"] == "reference_hierarchy_type_repair_promoted_by_strict_improvement"
    assert decision["baseline_issue_count"] == 66
    assert decision["candidate_issue_count"] == 51


def test_reference_hierarchy_type_repair_blocks_new_scope_risk() -> None:
    decision = _reference_hierarchy_type_repair_promotion_decision(
        baseline_audit_report={
            "high_hierarchy_issue_count": 5,
            "decision_counts": {"aligned": 10, "type_hierarchy_mismatch": 5},
        },
        candidate_audit_report={
            "status": "blocked",
            "reference_hierarchy_status": "needs_review",
            "high_hierarchy_issue_count": 4,
            "decision_counts": {
                "aligned": 11,
                "type_hierarchy_mismatch": 2,
                "out_of_reference_scope": 2,
            },
        },
        sumo_load_report={"status": "pass"},
    )

    assert decision["status"] == "blocked"
    assert decision["reason"] == "reference_hierarchy_type_repair_not_strictly_better"
    assert decision["risk_regressions"] == {"out_of_reference_scope": {"baseline": 0, "candidate": 2}}


def test_junction_semantic_gate_uses_comparison_evidence_when_case_counts_are_zero() -> None:
    report = {
        "status": "pass",
        "matched_case_count": 0,
        "junction_pattern_mismatch_count": 0,
        "junction_pattern_mismatch_field_counts": {},
        "junction_pattern_comparisons": [{"junction_id": "89129103", "status": "pass", "mismatch_fields": []}],
    }

    assert _junction_semantic_gate(report, {"movement_signature_counts"}) == "pass"


def test_followup_reference_delta_inherits_structural_only_baseline() -> None:
    assert _followup_reference_delta_structural_only({"audit_mode": "structural_only"}, default=False) is True
    assert _followup_reference_delta_structural_only({"audit_mode": "full"}, default=False) is False
    assert _followup_reference_delta_structural_only({"audit_mode": "full"}, default=True) is True


def test_direct_replay_path_part_is_short_and_stable_for_windows() -> None:
    long_junction_id = "cluster_267395411_270697844_915689881_915690365"

    path_part = _safe_path_part(long_junction_id)

    assert len(path_part) <= 16
    assert path_part == _safe_path_part(long_junction_id)
    assert path_part != _safe_path_part(long_junction_id + "_different")


def test_direct_local_teacher_replay_shortens_generated_net_paths_for_windows(tmp_path: Path, monkeypatch) -> None:
    source_net = tmp_path / "source.net.xml"
    teacher_net = tmp_path / "teacher.net.xml"
    source_net.write_text("<net/>", encoding="utf-8")
    teacher_net.write_text("<net/>", encoding="utf-8")
    captured: dict[str, Path] = {}

    def fake_replay(**kwargs):
        captured["output_file"] = kwargs["output_file"]
        return {"status": "fail"}

    monkeypatch.setattr("torii_sumo.core.osm_workflow.write_teacher_target_internal_replay_net", fake_replay)
    report = _run_direct_local_teacher_replay(
        queue_report={
            "teacher_net_file": str(teacher_net),
            "ready_candidate_count": 1,
            "repair_candidates": [
                {
                    "candidate_status": "ready_for_teacher_guided_variant",
                    "junction_id": "junction_" + "x" * 80,
                    "reference_id": "teacher_junction",
                    "edge_map": {"teacher_in": "candidate_in"},
                }
            ],
        },
        source_net_file=source_net,
        output_dir=tmp_path / ("output_" + "y" * (195-len(str(tmp_path.resolve()))-8)),
        prefix="very_long_teacher_guided_direct_replay_prefix",
        netconvert_binary="netconvert",
        sumo_binary="sumo",
        timeout_seconds=1.0,
        command_runner=lambda *args, **kwargs: {"status": "fail"},
    )

    assert report["status"] == "blocked"
    assert len(str(captured["output_file"].resolve())) < 260
    assert captured["output_file"].name in {"target_internal_replay.net.xml", "tir.net.xml"}


def test_scoped_teacher_tls_cell_batch_keeps_cells_as_independent_variants(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "source.net.xml"
    source.write_text("<net/>", encoding="utf-8")
    calls = []

    def fake_direct(**kwargs):
        calls.append(kwargs["output_dir"])
        kwargs["output_dir"].mkdir(parents=True, exist_ok=True)
        (kwargs["output_dir"] / "normalized.net.xml").write_text("<net/>", encoding="utf-8")
        return {
            "status": "pass",
            "variant_file": str(kwargs["output_dir"] / "normalized.net.xml"),
            "variant_reports": [
                {
                    "status": "pass",
                    "tls_via_path_semantics": {"status": "pass", "via_geometry_status": "pass"},
                }
            ],
        }

    monkeypatch.setattr("torii_sumo.core.osm_workflow._run_direct_local_teacher_replay", fake_direct)
    report = run_scoped_teacher_tls_cell_batch(
        queue_report={
            "repair_candidates": [
                {
                    "candidate_status": "needs_expanded_rebuild_scope",
                    "tls_reference_tl_id": "teacher_tls",
                    "tls_candidate_tl_id": "candidate_tls",
                    "tls_candidate_junction_ids": ["candidate_tls", "member_tls"],
                }
            ]
        },
        source_net_file=source,
        output_dir=tmp_path / "batch",
        prefix="demo",
        netconvert_binary="netconvert",
        sumo_binary="sumo",
        timeout_seconds=1.0,
    )

    assert report["status"] == "pass"
    assert report["cell_count"] == 1
    assert report["policy"].startswith("independent scoped variants")
    assert len(calls) == 1
    assert Path(report["report_file"]).is_file()
    assert report["artifact_manifest_status"] == "pass"
    assert Path(report["artifact_manifest_file"]).is_file()
    manifest = json.loads(Path(report["artifact_manifest_file"]).read_text(encoding="utf-8"))
    assert manifest["status"] == "pass"
    assert manifest["artifact_hash_gate"]["status"] == "pass"


def test_sumo_load_retries_netconvert_normalized_net_after_direct_failure(tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    sumo_binary = bin_dir / "sumo.exe"
    netconvert_binary = bin_dir / "netconvert.exe"
    sumo_binary.write_text("", encoding="utf-8")
    netconvert_binary.write_text("", encoding="utf-8")
    net_file = tmp_path / "candidate.net.xml"
    net_file.write_text("<net/>", encoding="utf-8")
    calls = []

    def command_runner(command, *, cwd, timeout_seconds):
        calls.append(command)
        if Path(command[0]).name == "netconvert.exe":
            output_file = Path(cwd) / command[command.index("--output-file") + 1]
            output_file.write_text("<net/>", encoding="utf-8")
            return {"returncode": 0, "stdout": "", "stderr": ""}
        sumo_call_count = sum(1 for call in calls if Path(call[0]).name == "sumo.exe")
        if sumo_call_count == 1:
            return {"returncode": 1, "stdout": "", "stderr": "direct load failed"}
        return {"returncode": 0, "stdout": "", "stderr": ""}

    report = _sumo_load_net(
        net_file,
        output_dir=tmp_path / "sumo_load",
        sumo_binary=str(sumo_binary),
        timeout_seconds=10,
        command_runner=command_runner,
    )

    assert report["status"] == "pass"
    assert [Path(call[0]).name for call in calls] == ["sumo.exe", "netconvert.exe", "sumo.exe"]
    assert report["direct_sumo_load"]["status"] == "fail"
    assert report["normalization_netconvert"]["status"] == "pass"
    assert report["load_net_file"].endswith("sumo_load_candidate_normalized.net.xml")


def test_teacher_guided_junction_parity_gate_uses_final_semantic_parity() -> None:
    final_semantic_report = {
        "status": "pass",
        "matched_case_count": 1,
        "junction_pattern_mismatch_count": 0,
        "junction_pattern_mismatch_field_counts": {},
        "junction_pattern_comparisons": [{"junction_id": "267517510", "status": "pass", "mismatch_fields": []}],
    }
    early_teacher_run = {
        "status": "fail",
        "parity_gate_status": "fail",
        "repair_candidate_count": 132,
    }

    assert _teacher_guided_junction_parity_gate(early_teacher_run, final_semantic_report) == "pass"


def test_restore_followup_internal_regressions_restores_only_new_internal_failures(tmp_path: Path) -> None:
    baseline_net = tmp_path / "baseline.net.xml"
    baseline_net.write_text(
        """<net>
    <edge id="in" from="a" to="j"><lane id="in_0" index="0" shape="0,0 10,0"/></edge>
    <edge id="out" from="j" to="b"><lane id="out_0" index="0" shape="10,0 20,0"/></edge>
    <edge id=":j_0" function="internal"><lane id=":j_0_0" index="0" shape="10,0 11,0"/></edge>
    <junction id="j" type="priority" x="10" y="0" incLanes="in_0" intLanes=":j_0_0"/>
    <connection from="in" to="out" fromLane="0" toLane="0" via=":j_0_0" dir="s"/>
    <connection from=":j_0" to="out" fromLane="0" toLane="0" dir="s"/>
</net>""",
        encoding="utf-8",
    )
    followup_net = tmp_path / "followup.net.xml"
    followup_net.write_text(
        """<net>
    <edge id="in" from="a" to="j"><lane id="in_0" index="0" shape="0,0 10,0"/></edge>
    <edge id="out" from="j" to="b"><lane id="out_0" index="0" shape="10,0 20,0"/></edge>
    <edge id=":j_0" function="internal"><lane id=":j_0_0" index="0" shape="10,0 11,0"/></edge>
    <edge id=":j_1" function="internal"><lane id=":j_1_0" index="0" shape="11,0 12,0"/></edge>
    <junction id="j" type="priority" x="10" y="0" incLanes="in_0" intLanes=":j_1_0"/>
    <connection from="in" to="out" fromLane="0" toLane="0" via=":j_0_0" dir="s"/>
    <connection from=":j_0" to="out" fromLane="0" toLane="0" via=":j_1_0" dir="s"/>
</net>""",
        encoding="utf-8",
    )

    report = _restore_followup_internal_regressions(
        baseline_delta_report={"junction_pattern_comparisons": []},
        followup_delta_report={
            "junction_pattern_comparisons": [
                {"junction_id": "j", "mismatch_fields": ["internal_function_counts"]},
                {"junction_id": "k", "mismatch_fields": ["approach_edge_ids"]},
            ]
        },
        baseline_net_file=baseline_net,
        followup_net_file=followup_net,
        output_dir=tmp_path / "restore",
        prefix="probe",
    )

    root = ET.parse(report["restored_net_file"]).getroot()
    assert report["status"] == "pass"
    assert report["restored_junction_ids"] == ["j"]
    assert root.find("edge[@id=':j_1']") is None
    assert root.find("junction[@id='j']").attrib["intLanes"] == ":j_0_0"


def _write_reference_net(path: Path) -> None:
    path.write_text(
        """<net>
    <edge id="primary_a" type="highway.primary">
        <lane id="primary_a_0" index="0" allow="passenger bus" speed="13.9" length="25.0"/>
    </edge>
    <edge id="residential_a" type="highway.residential">
        <lane id="residential_a_0" index="0" speed="13.9" length="25.0"/>
    </edge>
    <edge id="service_a" type="highway.service">
        <lane id="service_a_0" index="0" allow="delivery passenger pedestrian" speed="5.0" length="25.0"/>
    </edge>
    <edge id="service_b" type="highway.service">
        <lane id="service_b_0" index="0" allow="delivery passenger" speed="5.0" length="25.0"/>
    </edge>
    <edge id="cycle_a" type="highway.cycleway">
        <lane id="cycle_a_0" index="0" allow="bicycle" speed="5.0" length="25.0"/>
    </edge>
    <edge id="foot_a" type="highway.footway">
        <lane id="foot_a_0" index="0" allow="pedestrian" speed="5.0" length="25.0"/>
    </edge>
    <edge id="path_rare" type="highway.path">
        <lane id="path_rare_0" index="0" allow="passenger pedestrian" speed="5.0" length="25.0"/>
    </edge>
</net>""",
        encoding="utf-8",
    )


def _write_osm_highways(path: Path, highways: list[str]) -> None:
    nodes = "\n".join(
        f'    <node id="{index}" lat="48.{index:06d}" lon="11.{index:06d}"/>'
        for index in range(1, len(highways) * 2 + 1)
    )
    ways = []
    for index, highway in enumerate(highways, start=1):
        start = index * 2 - 1
        end = index * 2
        ways.append(
            f"""    <way id="{1000 + index}">
        <nd ref="{start}"/>
        <nd ref="{end}"/>
        <tag k="highway" v="{highway}"/>
    </way>"""
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"<osm>\n{nodes}\n{chr(10).join(ways)}\n</osm>", encoding="utf-8")


def test_teacher_guided_application_stats_reports_single_variant_scope(tmp_path: Path) -> None:
    best_net = tmp_path / "candidate_001_teacher_guided.net.xml"
    best_net.write_text("<net/>", encoding="utf-8")

    stats = _teacher_guided_application_stats(
        {
            "status": "pass",
            "parity_gate_status": "pass",
            "pass_candidate_count": 3,
        },
        best_net,
    )

    assert stats == {
        "teacher_guided_repair_application_scope": "single_best_variant",
        "teacher_guided_repair_applied_candidate_count": 1,
        "teacher_guided_repair_unapplied_pass_candidate_count": 2,
    }


def test_teacher_guided_application_stats_reports_sequential_composite_scope(tmp_path: Path) -> None:
    composite_net = tmp_path / "composite_teacher_guided.net.xml"
    composite_net.write_text("<net/>", encoding="utf-8")

    stats = _teacher_guided_application_stats(
        {
            "status": "pass",
            "parity_gate_status": "pass",
            "pass_candidate_count": 5,
            "composite_applied_candidate_count": 3,
            "composite_net_file": str(composite_net),
        },
        composite_net,
    )

    assert stats == {
        "teacher_guided_repair_application_scope": "sequential_composite",
        "teacher_guided_repair_applied_candidate_count": 3,
        "teacher_guided_repair_unapplied_pass_candidate_count": 2,
    }


def test_teacher_guided_best_variant_file_prefers_composite_net(tmp_path: Path) -> None:
    first_variant = tmp_path / "candidate_001_teacher_guided.net.xml"
    composite_net = tmp_path / "candidate_002_teacher_guided.net.xml"
    first_variant.write_text("<net/>", encoding="utf-8")
    composite_net.write_text("<net/>", encoding="utf-8")

    best = _teacher_guided_best_variant_file(
        {
            "status": "pass",
            "parity_gate_status": "pass",
            "composite_net_file": str(composite_net),
            "variant_reports": [
                {
                    "status": "pass",
                    "parity_gate_status": "pass",
                    "final_net_file": str(first_variant),
                }
            ],
        }
    )

    assert best == composite_net


def test_teacher_guided_best_variant_file_uses_partial_sequential_composite(tmp_path: Path) -> None:
    composite_net = tmp_path / "partial_composite_teacher_guided.net.xml"
    composite_net.write_text("<net/>", encoding="utf-8")

    best = _teacher_guided_best_variant_file(
        {
            "status": "fail",
            "parity_gate_status": "fail",
            "pass_candidate_count": 33,
            "parity_pass_candidate_count": 26,
            "composite_applied_candidate_count": 26,
            "composite_net_file": str(composite_net),
        }
    )

    assert best == composite_net


def test_teacher_guided_best_variant_file_does_not_promote_unapplied_local_variant(tmp_path: Path) -> None:
    local_variant = tmp_path / "local_teacher_guided.net.xml"
    local_variant.write_text("<net/>", encoding="utf-8")

    best = _teacher_guided_best_variant_file(
        {
            "status": "pass",
            "parity_gate_status": "pass",
            "composite_net_file": "",
            "composite_applied_candidate_count": 0,
            "variant_reports": [
                {
                    "status": "pass",
                    "parity_gate_status": "pass",
                    "sumo_load": {"status": "pass"},
                    "final_net_file": str(local_variant),
                }
            ],
        }
    )

    assert best is None


def test_teacher_guided_equivalent_approach_edge_map_collects_passed_replay_maps() -> None:
    edge_map = _teacher_guided_equivalent_approach_edge_map(
        {
            "variant_reports": [
                {
                    "status": "fail",
                    "parity_gate_status": "pass",
                    "target_internal_replay": {"effective_edge_map": {"ignored": "ignored_candidate"}},
                },
                {
                    "status": "pass",
                    "parity_gate_status": "pass",
                    "target_internal_replay": {"effective_edge_map": {"teacher_west": "candidate_west"}},
                },
            ]
        }
    )

    assert edge_map == {"teacher_west": "candidate_west"}


def test_filter_teacher_guided_queue_to_movement_mismatches(tmp_path: Path) -> None:
    queue_report = {
        "status": "pass",
        "queue_file": str(tmp_path / "all_queue.json"),
        "repair_candidate_count": 4,
        "ready_candidate_count": 2,
        "expanded_scope_candidate_count": 2,
        "blocked_candidate_count": 0,
        "repair_candidates": [
            {"reference_id": "cluster_keep", "candidate_status": "ready_for_teacher_guided_variant"},
            {"reference_id": "cluster_drop", "candidate_status": "ready_for_teacher_guided_variant"},
            {"junction_id": "cluster_approach", "candidate_status": "needs_expanded_rebuild_scope"},
            {
                "reference_id": "same_id_tls",
                "candidate_status": "needs_expanded_rebuild_scope",
                "learned_rule": "tum_like_same_id_tls_candidate",
            },
        ],
    }
    audit_report = {
        "junction_pattern_comparisons": [
            {"junction_id": "cluster_keep", "mismatch_fields": ["movement_signature_counts"]},
            {"junction_id": "cluster_drop", "mismatch_fields": ["approach_edge_ids"]},
            {"junction_id": "cluster_missing", "mismatch_fields": ["internal_function_counts"]},
        ]
    }

    filtered = _filter_teacher_guided_queue_to_mismatch_fields(
        queue_report,
        audit_report,
        {"movement_signature_counts", "internal_function_counts"},
        output_dir=tmp_path / "filtered",
        prefix="final_movement",
    )

    assert filtered["repair_candidate_count"] == 2
    assert filtered["ready_candidate_count"] == 1
    assert filtered["expanded_scope_candidate_count"] == 1
    assert filtered["queue_filter_target_junction_ids"] == ["cluster_keep", "cluster_missing"]
    assert filtered["queue_filter_original_repair_candidate_count"] == 4
    assert filtered["repair_candidates"] == [
        {"reference_id": "cluster_keep", "candidate_status": "ready_for_teacher_guided_variant"},
        {
            "reference_id": "same_id_tls",
            "candidate_status": "needs_expanded_rebuild_scope",
            "learned_rule": "tum_like_same_id_tls_candidate",
        },
    ]
    assert Path(str(filtered["queue_file"])).exists()


def test_filter_teacher_guided_queue_keeps_topology_fragmented_tls_candidate(tmp_path: Path) -> None:
    queue_report = {
        "status": "pass",
        "queue_file": str(tmp_path / "all_queue.json"),
        "repair_candidates": [
            {"reference_id": "cluster_keep", "candidate_status": "ready_for_teacher_guided_variant"},
            {
                "reference_id": "teacher_tls",
                "candidate_status": "needs_expanded_rebuild_scope",
                "learned_rule": "tum_like_topology_fragmented_tls_candidate",
            },
        ],
    }
    audit_report = {
        "junction_pattern_comparisons": [
            {"junction_id": "cluster_keep", "mismatch_fields": ["movement_signature_counts"]},
        ]
    }

    filtered = _filter_teacher_guided_queue_to_mismatch_fields(
        queue_report,
        audit_report,
        {"movement_signature_counts", "internal_function_counts"},
        output_dir=tmp_path / "filtered",
        prefix="final_movement",
    )

    assert [candidate["reference_id"] for candidate in filtered["repair_candidates"]] == [
        "cluster_keep",
        "teacher_tls",
    ]
    assert filtered["expanded_scope_candidate_count"] == 1


def test_filter_teacher_guided_queue_keeps_context_split_cluster_repair_seeds(tmp_path: Path) -> None:
    queue_report = {
        "status": "pass",
        "queue_file": str(tmp_path / "all_queue.json"),
        "repair_candidates": [
            {"reference_id": "cluster_context", "candidate_status": "needs_expanded_rebuild_scope"},
            {"reference_id": "cluster_drop", "candidate_status": "needs_expanded_rebuild_scope"},
        ],
    }
    context_report = {
        "context_split_cluster_repair_seeds": [
            {
                "reference_id": "cluster_context",
                "candidate_member_junction_ids": ["a", "b"],
                "seed_reason": "final_context_split_cluster_residual",
            }
        ]
    }

    filtered = _filter_teacher_guided_queue_to_mismatch_fields(
        queue_report,
        context_report,
        {"movement_signature_counts", "internal_function_counts"},
        output_dir=tmp_path / "filtered",
        prefix="context_followup",
    )

    assert filtered["repair_candidate_count"] == 1
    assert filtered["expanded_scope_candidate_count"] == 1
    assert filtered["queue_filter_target_junction_ids"] == ["cluster_context"]
    assert filtered["repair_candidates"] == [
        {"reference_id": "cluster_context", "candidate_status": "needs_expanded_rebuild_scope"}
    ]


def test_full_reference_join_audit_without_movement_delta_does_not_seed_teacher_guided_queue() -> None:
    assert not _reference_join_audit_can_seed_teacher_guided_queue(
        {
            "audit_mode": "full",
            "matched_case_count": 132,
            "junction_pattern_mismatch_count": 0,
            "junction_pattern_mismatch_field_counts": {},
            "junction_pattern_comparisons": [],
        },
        structural_only=False,
    )
    assert _reference_join_audit_can_seed_teacher_guided_queue(
        {
            "audit_mode": "full",
            "junction_pattern_mismatch_count": 1,
            "junction_pattern_mismatch_field_counts": {"movement_signature_counts": 1},
        },
        structural_only=False,
    )


def test_teacher_guided_seed_candidate_uses_structural_delta_fallback() -> None:
    primary = {
        "audit_mode": "full",
        "junction_pattern_mismatch_count": 0,
        "junction_pattern_mismatch_field_counts": {},
        "junction_pattern_comparisons": [],
    }
    structural = {
        "audit_mode": "structural_only",
        "junction_pattern_comparisons": [{"junction_id": "1611608462", "status": "fail"}],
    }

    report, structural_only, requires_promotion, source = _teacher_guided_seed_candidate(
        primary,
        primary_structural_only=False,
        fallback_reports=[("reference_visual_detail_delta", structural)],
    )

    assert report is structural
    assert structural_only is True
    assert requires_promotion is True
    assert source == "reference_visual_detail_delta"


def test_teacher_guided_seed_candidate_keeps_primary_movement_delta() -> None:
    primary = {
        "audit_mode": "full",
        "junction_pattern_mismatch_count": 1,
        "junction_pattern_mismatch_field_counts": {"movement_signature_counts": 1},
    }
    structural = {
        "audit_mode": "structural_only",
        "junction_pattern_comparisons": [{"junction_id": "1611608462", "status": "fail"}],
    }

    report, structural_only, requires_promotion, source = _teacher_guided_seed_candidate(
        primary,
        primary_structural_only=False,
        fallback_reports=[("reference_visual_detail_delta", structural)],
    )

    assert report is primary
    assert structural_only is False
    assert requires_promotion is False
    assert source == "reference_join_audit"


def test_teacher_guided_direct_replay_needed_when_composite_run_parity_fails() -> None:
    assert _teacher_guided_direct_replay_needed(
        repair_promotion_report={"status": "pass"},
        repair_run_report={"status": "fail", "parity_gate_status": "fail"},
    )
    assert not _teacher_guided_direct_replay_needed(
        repair_promotion_report={"status": "pass"},
        repair_run_report={"status": "pass", "parity_gate_status": "pass"},
    )


def test_tls_connection_repair_promotion_blocks_reference_delta_regression(tmp_path: Path) -> None:
    variant_file = tmp_path / "repaired.net.xml"
    variant_file.write_text("<net/>", encoding="utf-8")

    decision = _tls_connection_repair_promotion_decision(
        repair_report={
            "status": "pass",
            "variant_file": str(variant_file),
            "skipped_invalid_mapped_linkindex_connection_count": 0,
        },
        sumo_load_report={"status": "pass"},
        rejected_delta_report={
            "network_structural_missing_counts": {"tls_controlled_connection_count": 10},
            "network_structural_extra_counts": {},
        },
        repair_delta_report={
            "status": "pass",
            "network_structural_missing_counts": {"tls_controlled_connection_count": 20},
            "network_structural_extra_counts": {},
        },
    )

    assert decision["status"] == "blocked"
    assert decision["reason"] == "reference_tls_semantic_delta_regressed"


def test_tls_connection_repair_promotion_blocks_incompatible_tllogic_warning(tmp_path: Path) -> None:
    variant_file = tmp_path / "repaired.net.xml"
    variant_file.write_text("<net/>", encoding="utf-8")

    decision = _tls_connection_repair_promotion_decision(
        repair_report={
            "status": "pass",
            "variant_file": str(variant_file),
            "skipped_invalid_mapped_linkindex_connection_count": 0,
        },
        sumo_load_report={
            "status": "pass",
            "stderr": (
                "Warning: Program '0' at tlLogic 'joinedS_10176312934_7881057697' "
                "is incompatible with logic at junction '7881057697'."
            ),
        },
        rejected_delta_report={
            "network_structural_missing_counts": {"tls_controlled_connection_count": 10},
            "network_structural_extra_counts": {},
        },
        repair_delta_report={
            "status": "pass",
            "network_structural_missing_counts": {},
            "network_structural_extra_counts": {},
        },
    )

    assert decision["status"] == "blocked"
    assert decision["reason"] == "sumo_load_tls_incompatible"


def test_movement_rebuild_promotion_blocks_large_structural_regression() -> None:
    decision = _movement_rebuild_reference_delta_promotion_decision(
        baseline_delta_report={
            "status": "pass",
            "junction_pattern_mismatch_field_counts": {
                "movement_signature_counts": 7,
                "internal_function_counts": 6,
            },
            "network_structural_missing_counts": {"crossing_edge_count": 145},
            "network_structural_extra_counts": {"connection_count": 570},
        },
        candidate_delta_report={
            "status": "pass",
            "junction_pattern_mismatch_field_counts": {},
            "junction_pattern_mismatch_count": 0,
            "network_structural_missing_counts": {"crossing_edge_count": 147},
            "network_structural_extra_counts": {"connection_count": 2649},
        },
        reason="final_movement_rebuild_promoted_by_reference_delta",
    )

    assert decision["status"] == "blocked"
    assert decision["reason"] == "reference_structural_delta_regressed"


def test_movement_rebuild_promotion_blocks_no_benefit_structural_regression() -> None:
    decision = _movement_rebuild_reference_delta_promotion_decision(
        baseline_delta_report={
            "status": "pass",
            "junction_pattern_mismatch_field_counts": {"movement_signature_counts": 13},
            "network_structural_missing_counts": {"crossing_edge_count": 100},
            "network_structural_extra_counts": {"connection_count": 1861},
        },
        candidate_delta_report={
            "status": "pass",
            "junction_pattern_mismatch_field_counts": {"movement_signature_counts": 13},
            "network_structural_missing_counts": {"crossing_edge_count": 100},
            "network_structural_extra_counts": {"connection_count": 1872},
        },
        reason="iterative_direct_local_teacher_replay_promoted_by_reference_delta",
    )

    assert decision["status"] == "blocked"
    assert decision["reason"] == "movement_rebuild_no_reference_delta_improvement"


def test_movement_rebuild_promotion_uses_structural_guard_baseline() -> None:
    decision = _movement_rebuild_reference_delta_promotion_decision(
        baseline_delta_report={
            "status": "pass",
            "junction_pattern_mismatch_field_counts": {
                "movement_signature_counts": 13,
            },
            "network_structural_missing_counts": {"crossing_edge_count": 127},
            "network_structural_extra_counts": {"connection_count": 2664},
        },
        candidate_delta_report={
            "status": "pass",
            "junction_pattern_mismatch_field_counts": {},
            "junction_pattern_mismatch_count": 0,
            "network_structural_missing_counts": {"crossing_edge_count": 147},
            "network_structural_extra_counts": {"connection_count": 2649},
        },
        structural_guard_delta_report={
            "status": "pass",
            "junction_pattern_mismatch_field_counts": {
                "movement_signature_counts": 30,
            },
            "network_structural_missing_counts": {"crossing_edge_count": 145},
            "network_structural_extra_counts": {"connection_count": 570},
        },
        reason="final_movement_rebuild_promoted_by_reference_delta",
    )

    assert decision["status"] == "blocked"
    assert decision["reason"] == "reference_structural_delta_regressed"
    assert decision["guard_total_structural_delta_score"] == 715


def test_movement_rebuild_promotion_blocks_controlled_tls_regression(tmp_path: Path) -> None:
    baseline_net = tmp_path / "baseline.net.xml"
    candidate_net = tmp_path / "candidate.net.xml"
    baseline_net.write_text(
        '<net><connection from="a" to="b" fromLane="0" toLane="0" tl="tls" linkIndex="0" /></net>',
        encoding="utf-8",
    )
    candidate_net.write_text(
        '<net><connection from="a" to="b" fromLane="0" toLane="0" /></net>',
        encoding="utf-8",
    )

    decision = _movement_rebuild_reference_delta_promotion_decision(
        baseline_delta_report={
            "status": "pass",
            "candidate_net_file": str(baseline_net),
            "junction_pattern_mismatch_field_counts": {"movement_signature_counts": 5},
            "network_structural_missing_counts": {"connection_count": 10},
            "network_structural_extra_counts": {},
        },
        candidate_delta_report={
            "status": "pass",
            "candidate_net_file": str(candidate_net),
            "junction_pattern_mismatch_field_counts": {},
            "network_structural_missing_counts": {},
            "network_structural_extra_counts": {},
        },
        reason="final_movement_rebuild_promoted_by_reference_delta",
    )

    assert decision["status"] == "blocked"
    assert decision["reason"] == "controlled_tls_connection_regressed"
    assert decision["baseline_controlled_connection_count"] == 1
    assert decision["candidate_controlled_connection_count"] == 0
    assert decision["controlled_connection_regression_count"] == 1


def test_reference_delta_promotion_prefers_candidate_with_lower_tls_semantic_score() -> None:
    decision = _reference_delta_promotion_decision(
        candidate_delta_report={
            "status": "pass",
            "network_structural_missing_counts": {"tls_controlled_connection_count": 165},
            "network_structural_extra_counts": {"traffic_light_junction_count": 46},
        },
        baseline_delta_report={
            "status": "pass",
            "network_structural_missing_counts": {"tls_shared_linkindex_group_count": 40},
            "network_structural_extra_counts": {"traffic_light_junction_count": 354},
        },
        reason="tls_aggregation_promoted_by_reference_delta",
    )

    assert decision["status"] == "pass"
    assert decision["reason"] == "tls_aggregation_promoted_by_reference_delta"
    assert decision["candidate_tls_semantic_delta_score"] == 211
    assert decision["baseline_tls_semantic_delta_score"] == 394


def test_low_vehicle_control_candidate_limits_include_tls_count_fallback() -> None:
    limits = _low_vehicle_control_candidate_limits(
        {
            "network_structural_extra_counts": {
                "tl_logic_count": 41,
                "traffic_light_junction_count": 46,
            },
            "tls_control_review_queue": [
                {
                    "review_type": "downgrade_low_vehicle_approach_tls",
                    "tl_id": str(index),
                }
                for index in range(60)
            ],
        }
    )

    assert limits == [
        {
            "label": "tls10",
            "max_removed_controlled_connections": None,
            "max_selected_tllogic_count": 10,
        },
        {
            "label": "tls20",
            "max_removed_controlled_connections": None,
            "max_selected_tllogic_count": 20,
        },
        {
            "label": "tls41",
            "max_removed_controlled_connections": None,
            "max_selected_tllogic_count": 41,
        },
    ]


def test_network_plan_blocks_when_layers_and_reference_are_missing() -> None:
    plan = derive_network_plan()

    assert plan["status"] == "blocked"
    assert plan["network_plan_status"] == "needs_user_confirmation"
    assert plan["missing_blockers"] == ["network_plan"]
    assert "traffic layers" in plan["next_question"]
    assert "reference_matched" in plan["network_detail_options"]


def test_network_plan_blocks_named_reference_without_reference_artifact() -> None:
    plan = derive_network_plan(
        user_request="Generate a city-center SUMO network matching a manually cleaned reference network",
    )

    assert plan["status"] == "blocked"
    assert plan["network_plan_status"] == "needs_reference_artifact"
    assert plan["network_detail_target"] == "reference_matched"
    assert plan["reference_target"] == "manually cleaned reference network"
    assert plan["missing_blockers"] == ["reference_network_or_policy"]
    assert "reference SUMO .net.xml" in plan["next_question"]


def test_network_plan_derives_reference_policy_from_reference_net(tmp_path: Path) -> None:
    reference_net_file = tmp_path / "manual-reference.net.xml"
    _write_reference_net(reference_net_file)

    plan = derive_network_plan(
        user_request="Generate an OSM network that matches a manually cleaned reference network",
        network_profile="reference_matched",
        reference_net_file=reference_net_file,
    )

    assert plan["status"] == "pass"
    assert plan["network_plan_status"] == "inferred_from_reference_policy"
    assert plan["network_profile"] == "reference_matched"
    assert plan["reference_net_file"] == str(reference_net_file)
    assert plan["network_detail_target"] == "reference_matched"
    assert plan["primary_network_layer"] == "passenger_vehicle"
    assert plan["default_routeability_layer"] == "vehicle_core"
    assert plan["default_netedit_comparison_layer"] == "reference_visual_detail"
    assert plan["vehicle_core_highway_classes"] == plan["highway_classes"]
    assert "service" not in plan["highway_classes"]
    assert "service" in plan["reference_visual_detail_highway_classes"]
    assert "service" in plan["reference_visual_detail_only_highway_classes"]
    assert "primary" in plan["highway_classes"]
    assert "residential" in plan["highway_classes"]
    assert "cycleway" not in plan["highway_classes"]
    assert "footway" not in plan["highway_classes"]
    assert "path" not in plan["highway_classes"]
    assert {"cycleway", "footway", "path"} <= set(plan["reference_visual_detail_highway_classes"])
    assert {"cycleway", "footway", "path"} <= set(plan["reference_visual_detail_only_highway_classes"])
    assert {"passenger", "bicycle", "pedestrian", "bus"} <= set(plan["movement_layers"])
    assert set(plan["auxiliary_modal_layers"]) == {"bicycle", "pedestrian", "bus"}
    assert plan["reference_policy"]["reference_policy_status"] == "analyzed"
    assert plan["reference_policy"]["passenger_edge_type_counts"]["highway.service"] == 2
    assert plan["reference_policy"]["visual_detail_edge_type_counts"]["highway.footway"] == 1
    assert plan["service_passenger_policy"] == "reference_match"
    assert "routeability_audit" in plan["validation_gates"]
    assert "standard_nema_scan" in plan["validation_gates"]
    assert "scope_matched_reference_comparison" in plan["validation_gates"]
    assert "reference_join_audit" in plan["validation_gates"]
    assert "junction_pattern_index" in plan["validation_gates"]
    assert "road_connectivity_parity" in plan["validation_gates"]
    assert "connection_semantics_parity" in plan["validation_gates"]
    assert "tls_semantics_parity" in plan["validation_gates"]
    assert "internal_junction_parity" in plan["validation_gates"]
    assert "connection_mode_audit" in plan["validation_gates"]
    assert "netedit_connection_mode_review" not in plan["validation_gates"]
    assert "teacher_guided_junction_parity" in plan["validation_gates"]


def test_network_plan_derives_reference_source_way_ids_from_osm_edge_ids(tmp_path: Path) -> None:
    reference_net_file = tmp_path / "manual-reference.net.xml"
    reference_net_file.write_text(
        """<net>
    <edge id="12345#0" type="highway.primary">
        <lane id="12345#0_0" index="0" allow="passenger" speed="13.9" length="25.0"/>
    </edge>
    <edge id="-67890#2" type="highway.service">
        <lane id="-67890#2_0" index="0" allow="passenger pedestrian" speed="5.0" length="25.0"/>
    </edge>
    <edge id="synthetic_edge" type="highway.footway">
        <lane id="synthetic_edge_0" index="0" allow="pedestrian" speed="5.0" length="25.0"/>
    </edge>
</net>""",
        encoding="utf-8",
    )

    plan = derive_network_plan(
        user_request="Generate an OSM network that matches a manually cleaned reference network",
        network_profile="reference_matched",
        reference_net_file=reference_net_file,
    )

    assert plan["reference_source_way_ids"] == ["12345", "67890"]
    assert plan["reference_policy"]["reference_source_way_id_count"] == 2


def test_reference_matched_plan_keeps_service_out_of_vehicle_core(tmp_path: Path) -> None:
    reference_net_file = tmp_path / "manual-reference.net.xml"
    _write_reference_net(reference_net_file)

    plan = derive_network_plan(
        user_request="Generate an OSM network that mimics a manually cleaned TUM reference network",
        network_profile="reference_matched",
        reference_net_file=reference_net_file,
    )

    assert plan["status"] == "pass"
    assert "service" not in plan["highway_classes"]
    assert "service" not in plan["vehicle_core_highway_classes"]
    assert "service" in plan["reference_visual_detail_highway_classes"]
    assert "service" in plan["reference_visual_detail_only_highway_classes"]
    assert plan["service_passenger_policy"] == "reference_match"


def test_reference_bbox_uses_reference_geometry_not_stale_orig_boundary(tmp_path: Path) -> None:
    reference_net_file = tmp_path / "clipped-reference.net.xml"
    reference_net_file.write_text(
        """<net>
    <location netOffset="0.00,0.00" convBoundary="100.00,300.00,200.00,400.00" origBoundary="0.000000,0.000000,99.000000,99.000000"/>
    <junction id="left" type="priority" x="100.00" y="300.00"/>
    <junction id="right" type="priority" x="200.00" y="400.00"/>
    <edge id="e0" from="left" to="right" type="highway.primary">
        <lane id="e0_0" index="0" speed="13.9" length="141.0" shape="100.00,300.00 200.00,400.00"/>
    </edge>
</net>""",
        encoding="utf-8",
    )

    report = derive_reference_net_bbox(
        reference_net_file,
        padding_m=0.0,
        xy_to_latlon_func=lambda x, y: (y / 100.0, x / 100.0),
    )

    assert report["status"] == "pass"
    assert report["reference_bbox_status"] == "derived_from_reference_geometry"
    assert report["reference_bbox"] == "1.0000000,3.0000000,2.0000000,4.0000000"
    assert report["reference_bbox_source"] == "junction_and_lane_geometry"
    assert report["reference_orig_boundary"] == "0.000000,0.000000,99.000000,99.000000"


def test_service_permissions_do_not_overwrite_an_invalid_network(tmp_path: Path) -> None:
    net_file = tmp_path / "network.net.xml"
    net_file.write_text(
        """<net>
    <edge id="service_a" type="highway.service">
        <lane id="service_a_0" index="0" allow="bicycle delivery pedestrian" speed="5.0" length="25.0"/>
    </edge>
    <edge id="residential_b" type="highway.residential">
        <lane id="residential_b_0" index="0" allow="passenger" speed="13.9" length="25.0"/>
    </edge>
</net>""",
        encoding="utf-8",
    )

    before = net_file.read_bytes()
    report = apply_service_passenger_permissions(net_file, policy="allow_vehicle_service")

    root = ET.parse(net_file).getroot()
    service_lane = root.find("./edge[@id='service_a']/lane")
    residential_lane = root.find("./edge[@id='residential_b']/lane")
    assert report["status"] == "blocked"
    assert report["service_passenger_permission_status"] == "failed"
    assert net_file.read_bytes() == before
    assert report["service_edge_count"] == 1
    assert report["changed_lane_count"] == 1
    assert "passenger" not in service_lane.attrib["allow"].split()
    assert residential_lane.attrib["allow"] == "passenger"


def test_export_plain_net_for_teacher_guided_repair_resolves_relative_output_dir(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    net_file = tmp_path / "candidate.net.xml"
    net_file.write_text("<net/>", encoding="utf-8")
    calls: dict[str, object] = {}

    def fake_command(command, **kwargs):
        calls["command"] = command
        calls["cwd"] = kwargs["cwd"]
        plain_prefix = Path(command[-1])
        for suffix in (".nod.xml", ".edg.xml", ".con.xml"):
            Path(f"{plain_prefix}{suffix}").write_text("<xml/>", encoding="utf-8")
        return {"status": "pass", "returncode": 0}

    report = export_plain_net_for_teacher_guided_repair(
        net_file=Path("candidate.net.xml"),
        output_dir=Path("plain"),
        prefix="demo",
        command_runner=fake_command,
    )

    expected_prefix = tmp_path / "plain" / "demo"
    assert report["status"] == "pass"
    assert calls["command"][-1] == str(expected_prefix)
    assert calls["cwd"] == tmp_path / "plain"
    assert report["raw_node_file"] == str(expected_prefix) + ".nod.xml"


def test_export_plain_net_for_teacher_guided_repair_uses_short_source_copy(
    tmp_path: Path,
) -> None:
    # Keep the fixture deliberately long while reserving room for the
    # per-run pytest temp root on Windows, whose legacy path limit is still
    # enforced by some Python installations.
    short_root = tmp_path.parent.parent
    source_name = f"source_{tmp_path.parent.name}_{tmp_path.name[-8:]}_"
    file_name = "candidate_" + "y" * 48 + ".net.xml"
    available_dir_chars = 220 - len(str(short_root)) - 1 - len(file_name)
    long_dir = short_root / (source_name + "x" * max(16, available_dir_chars - len(source_name)))
    net_file = long_dir / file_name
    net_file.parent.mkdir(parents=True, exist_ok=True)
    net_file.write_text("<net/>", encoding="utf-8")
    calls: dict[str, object] = {}

    def fake_command(command, **kwargs):
        calls["command"] = command
        source_arg = Path(command[2])
        assert len(str(source_arg)) < 120
        assert source_arg.name == "plain_source.net.xml"
        assert (kwargs["cwd"] / source_arg).exists()
        plain_prefix = Path(command[-1])
        for suffix in (".nod.xml", ".edg.xml", ".con.xml"):
            Path(f"{plain_prefix}{suffix}").write_text("<xml/>", encoding="utf-8")
        return {"status": "pass", "returncode": 0}

    report = export_plain_net_for_teacher_guided_repair(
        net_file=net_file,
        output_dir=tmp_path / "plain",
        prefix="demo",
        command_runner=fake_command,
    )

    assert report["status"] == "pass"
    assert calls["command"][2] == "plain_source.net.xml"


def test_export_plain_net_for_teacher_guided_repair_synthesizes_missing_used_edge_types(
    tmp_path: Path,
) -> None:
    net_file = tmp_path / "candidate.net.xml"
    net_file.write_text("<net/>", encoding="utf-8")

    def fake_command(command, **_kwargs):
        plain_prefix = Path(command[-1])
        Path(f"{plain_prefix}.nod.xml").write_text("<nodes/>", encoding="utf-8")
        Path(f"{plain_prefix}.con.xml").write_text("<connections/>", encoding="utf-8")
        Path(f"{plain_prefix}.typ.xml").write_text(
            '<types><type id="highway.residential" priority="3" numLanes="1" speed="13.89"/></types>',
            encoding="utf-8",
        )
        Path(f"{plain_prefix}.edg.xml").write_text(
            """<edges>
    <edge id="753083363" from="a" to="b" type="cycleway.lane|highway.unclassified"
          priority="4" numLanes="4" speed="8.33">
        <lane index="0" allow="pedestrian" width="2.00"/>
        <lane index="1" allow="bicycle" width="1.00"/>
    </edge>
</edges>""",
            encoding="utf-8",
        )
        return {"status": "pass", "returncode": 0}

    report = export_plain_net_for_teacher_guided_repair(
        net_file=net_file,
        output_dir=tmp_path / "plain",
        prefix="demo",
        command_runner=fake_command,
    )

    type_root = ET.parse(report["raw_type_file"]).getroot()
    synthesized = type_root.find("./type[@id='cycleway.lane|highway.unclassified']")
    assert report["status"] == "pass"
    assert report["synthesized_edge_type_count"] == 1
    assert report["synthesized_edge_type_ids"] == ["cycleway.lane|highway.unclassified"]
    assert synthesized is not None
    assert synthesized.attrib["priority"] == "4"
    assert synthesized.attrib["numLanes"] == "4"
    assert synthesized.attrib["speed"] == "8.33"


def test_export_plain_net_for_teacher_guided_repair_restores_false_tls_plain_nodes(
    tmp_path: Path,
) -> None:
    net_file = tmp_path / "candidate.net.xml"
    net_file.write_text(
        """<net>
    <junction id="j_false" type="priority"/>
    <junction id="j_real" type="traffic_light"/>
    <tlLogic id="j_real" type="static" programID="0" offset="0"/>
</net>""",
        encoding="utf-8",
    )

    def fake_command(command, **_kwargs):
        plain_prefix = Path(command[-1])
        Path(f"{plain_prefix}.nod.xml").write_text(
            """<nodes>
    <node id="j_false" type="traffic_light" x="0" y="0"/>
    <node id="j_real" type="traffic_light" x="1" y="0"/>
</nodes>""",
            encoding="utf-8",
        )
        Path(f"{plain_prefix}.edg.xml").write_text("<edges/>", encoding="utf-8")
        Path(f"{plain_prefix}.con.xml").write_text("<connections/>", encoding="utf-8")
        Path(f"{plain_prefix}.tll.xml").write_text(
            '<tlLogics><tlLogic id="j_real" type="static" programID="0" offset="0"/></tlLogics>',
            encoding="utf-8",
        )
        return {"status": "pass", "returncode": 0}

    report = export_plain_net_for_teacher_guided_repair(
        net_file=net_file,
        output_dir=tmp_path / "plain",
        prefix="demo",
        command_runner=fake_command,
    )

    node_root = ET.parse(report["raw_node_file"]).getroot()
    assert report["restored_false_traffic_light_plain_node_count"] == 1
    assert report["restored_false_traffic_light_plain_node_ids"] == ["j_false"]
    assert node_root.find("./node[@id='j_false']").attrib["type"] == "priority"
    assert node_root.find("./node[@id='j_real']").attrib["type"] == "traffic_light"


def test_export_plain_net_for_teacher_guided_repair_prunes_stale_plain_tllogics(
    tmp_path: Path,
) -> None:
    net_file = tmp_path / "candidate.net.xml"
    net_file.write_text(
        """<net>
    <junction id="old_tls" type="priority"/>
    <junction id="cluster_tls" type="traffic_light" tl="cluster_tls"/>
    <tlLogic id="cluster_tls" type="static" programID="0" offset="0"/>
</net>""",
        encoding="utf-8",
    )

    def fake_command(command, **_kwargs):
        plain_prefix = Path(command[-1])
        Path(f"{plain_prefix}.nod.xml").write_text(
            """<nodes>
    <node id="old_tls" type="traffic_light" x="0" y="0"/>
    <node id="cluster_tls" type="traffic_light" tl="cluster_tls" x="1" y="0"/>
</nodes>""",
            encoding="utf-8",
        )
        Path(f"{plain_prefix}.edg.xml").write_text("<edges/>", encoding="utf-8")
        Path(f"{plain_prefix}.con.xml").write_text("<connections/>", encoding="utf-8")
        Path(f"{plain_prefix}.tll.xml").write_text(
            """<tlLogics>
    <tlLogic id="old_tls" type="static" programID="0" offset="0"/>
    <tlLogic id="cluster_tls" type="static" programID="0" offset="0"/>
    <connection from="a" to="b" fromLane="0" toLane="0" tl="old_tls" linkIndex="0"/>
    <connection from="c" to="d" fromLane="0" toLane="0" tl="cluster_tls" linkIndex="0"/>
</tlLogics>""",
            encoding="utf-8",
        )
        return {"status": "pass", "returncode": 0}

    report = export_plain_net_for_teacher_guided_repair(
        net_file=net_file,
        output_dir=tmp_path / "plain",
        prefix="demo",
        command_runner=fake_command,
    )

    tllogic_root = ET.parse(report["raw_tllogic_file"]).getroot()
    assert report["status"] == "pass"
    assert report["restored_false_traffic_light_plain_node_ids"] == ["old_tls"]
    assert report["removed_stale_plain_tllogic_ids"] == ["old_tls"]
    assert report["removed_stale_plain_tllogic_count"] == 1
    assert report["removed_stale_plain_tllogic_connection_count"] == 1
    assert tllogic_root.find("./tlLogic[@id='old_tls']") is None
    assert tllogic_root.find("./tlLogic[@id='cluster_tls']") is not None
    assert tllogic_root.find("./connection[@tl='old_tls']") is None
    assert tllogic_root.find("./connection[@tl='cluster_tls']") is not None


def test_export_plain_net_for_teacher_guided_repair_shortens_long_plain_prefix(
    tmp_path: Path,
) -> None:
    net_file = tmp_path / "candidate.net.xml"
    net_file.write_text("<net/>", encoding="utf-8")
    long_prefix = "sumo_osm_cleanup_post_teacher_tls_connection_repair_movement_rebuild_" + ("x" * 160)

    def fake_command(command, **_kwargs):
        plain_prefix = Path(command[-1])
        assert len(str(plain_prefix.resolve())) + len(".nod.xml") < 240
        for suffix in (".nod.xml", ".edg.xml", ".con.xml"):
            Path(f"{plain_prefix}{suffix}").write_text("<xml/>", encoding="utf-8")
        return {"status": "pass", "returncode": 0}

    report = export_plain_net_for_teacher_guided_repair(
        net_file=net_file,
        output_dir=tmp_path / "plain",
        prefix=long_prefix,
        command_runner=fake_command,
    )

    assert report["status"] == "pass"
    assert report["plain_output_prefix_shortened"] is True
    assert Path(report["plain_output_prefix"]).name.endswith("_" + report["plain_output_prefix_digest"])
