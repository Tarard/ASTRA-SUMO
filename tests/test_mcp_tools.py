import json
import inspect
from pathlib import Path

from torii_sumo.tools.evidence_tools import sumo_collect_evidence, sumo_compare_outputs
from torii_sumo.tools.osm_tools import (
    sumo_network_connection_mode_audit,
    sumo_network_connection_mode_calibration,
    sumo_network_connection_mode_regression_audit,
    sumo_network_exact_semantic_regression_audit,
    sumo_network_review_html,
    sumo_network_junction_aggregation_variant,
    sumo_network_overlapping_junction_audit,
    sumo_network_reference_hierarchy_audit,
    sumo_network_corridor_geometry_simplification_variant,
    sumo_network_corridor_candidate_gates,
    sumo_network_reference_join_audit,
    sumo_network_reference_scope_audit,
    sumo_network_routeability_audit,
    sumo_network_scope_pruning_variant,
    sumo_network_standard_nema_phase_binding,
    sumo_network_teacher_guided_junction_variant,
    sumo_network_teacher_guided_repair_queue,
    sumo_network_tls_aggregation_variant,
    sumo_network_tls_reference_cleanup_variant,
    sumo_network_tls_warning_parity,
    sumo_network_topology_audit,
)
from torii_sumo.tools.run_tools import sumo_run_minimal_smoke


FIXTURES = Path(__file__).parent / "fixtures" / "outputs"


def test_sumo_compare_outputs_returns_json_dict() -> None:
    report = sumo_compare_outputs(
        baseline_summary=str(FIXTURES / "baseline-summary.xml"),
        baseline_tripinfo=str(FIXTURES / "baseline-tripinfo.xml"),
        variant_summary=str(FIXTURES / "variant-summary-complete.xml"),
        variant_tripinfo=str(FIXTURES / "variant-tripinfo.xml"),
    )

    assert report["status"] == "pass"
    assert report["claim_status"] == "diagnostic-demo"


def test_sumo_collect_evidence_success_returns_json_compatible_pass(tmp_path: Path) -> None:
    report = sumo_collect_evidence(
        output_dir=str(tmp_path / "evidence"),
        label="smoke",
        payload={"status": "pass", "claim_status": "diagnostic-demo"},
    )

    assert report["status"] == "pass"
    assert report["claim_status"] == "diagnostic-demo"
    json.dumps(report)


def test_sumo_collect_evidence_empty_output_dir_returns_json_compatible_failure() -> None:
    report = sumo_collect_evidence(
        output_dir="  ",
        label="smoke",
        payload={"status": "pass"},
    )

    assert report["status"] == "fail"
    assert report["claim_status"] == "construction-invalid"
    assert "output_dir is required" in str(report["error"])
    json.dumps(report)


def test_sumo_collect_evidence_file_path_conflict_returns_failure(tmp_path: Path) -> None:
    output_file = tmp_path / "already-a-file"
    output_file.write_text("not a directory", encoding="utf-8")

    report = sumo_collect_evidence(
        output_dir=str(output_file),
        label="smoke",
        payload={"status": "pass"},
    )

    assert report["status"] == "fail"
    assert report["claim_status"] == "construction-invalid"
    json.dumps(report)


def test_sumo_collect_evidence_non_json_payload_returns_failure(tmp_path: Path) -> None:
    report = sumo_collect_evidence(
        output_dir=str(tmp_path / "evidence"),
        label="smoke",
        payload={"not_json": {1, 2, 3}},
    )

    assert report["status"] == "fail"
    assert report["claim_status"] == "construction-invalid"
    json.dumps(report)


def test_sumo_run_minimal_smoke_can_be_forced_blocked(tmp_path: Path) -> None:
    report = sumo_run_minimal_smoke(
        work_dir=str(tmp_path),
        require_real_sumo=False,
    )

    assert report["status"] == "blocked"
    assert report["claim_status"] == "blocked"


def test_sumo_network_routeability_audit_tool_returns_json_compatible_report(monkeypatch, tmp_path: Path) -> None:
    from torii_sumo.tools import osm_tools

    net_file = tmp_path / "network.net.xml"
    net_file.write_text("<net/>", encoding="utf-8")

    def fake_audit(**kwargs):
        assert kwargs["net_file"] == net_file
        assert kwargs["vehicle_count"] == 25
        return {
            "status": "pass",
            "claim_status": "diagnostic-demo",
            "routeability_status": "pass",
            "report_file": str(tmp_path / "audit.json"),
        }

    monkeypatch.setattr(osm_tools, "run_routeability_audit", fake_audit)

    report = sumo_network_routeability_audit(
        net_file=str(net_file),
        output_dir=str(tmp_path / "audit"),
        vehicle_count=25,
    )

    assert report["status"] == "pass"
    assert report["routeability_status"] == "pass"
    json.dumps(report)


def test_sumo_network_topology_audit_tool_returns_json_compatible_report(monkeypatch, tmp_path: Path) -> None:
    from torii_sumo.tools import osm_tools

    net_file = tmp_path / "network.net.xml"
    net_file.write_text("<net/>", encoding="utf-8")

    def fake_audit(**kwargs):
        assert kwargs["net_file"] == net_file
        assert kwargs["cluster_radius_m"] == 25.0
        assert kwargs["min_cluster_nodes"] == 3
        return {
            "status": "blocked",
            "claim_status": "blocked",
            "topology_fragmentation_status": "needs_review",
            "suspicious_cluster_count": 1,
            "clusters_file": str(tmp_path / "clusters.csv"),
        }

    monkeypatch.setattr(osm_tools, "audit_topology_fragmentation", fake_audit)

    report = sumo_network_topology_audit(
        net_file=str(net_file),
        output_dir=str(tmp_path / "topology"),
        cluster_radius_m=25.0,
        min_cluster_nodes=3,
    )

    assert report["status"] == "blocked"
    assert report["topology_fragmentation_status"] == "needs_review"
    json.dumps(report)


def test_sumo_network_review_html_tool_returns_review_artifact(tmp_path: Path) -> None:
    net_file = tmp_path / "partial.net.xml"
    topology_report = tmp_path / "topology.json"
    net_file.write_text("<net/>", encoding="utf-8")
    topology_report.write_text(
        json.dumps(
            {
                "topology_fragmentation_status": "needs_review",
                "suspicious_cluster_count": 2,
                "clusters_file": str(tmp_path / "clusters.csv"),
            }
        ),
        encoding="utf-8",
    )

    report = sumo_network_review_html(
        output_dir=str(tmp_path / "review"),
        net_file=str(net_file),
        title="Partial network review",
        claim_status="construction-invalid",
        topology_audit_report_file=str(topology_report),
    )

    assert report["status"] == "pass"
    assert report["workflow_review_html_status"] == "pass"
    html_file = Path(str(report["workflow_review_html_file"]))
    assert html_file.is_file()
    html = html_file.read_text(encoding="utf-8")
    assert "Partial network review" in html
    assert "Human Review Required" in html
    assert "topology_fragmentation_status" in html
    json.dumps(report)


def test_sumo_network_reference_join_audit_tool_returns_json_compatible_report(monkeypatch, tmp_path: Path) -> None:
    from torii_sumo.tools import osm_tools

    reference_net_file = tmp_path / "reference.net.xml"
    candidate_net_file = tmp_path / "candidate.net.xml"
    reference_net_file.write_text("<net/>", encoding="utf-8")
    candidate_net_file.write_text("<net/>", encoding="utf-8")

    def fake_audit(**kwargs):
        assert kwargs["reference_net_file"] == reference_net_file
        assert kwargs["candidate_net_file"] == candidate_net_file
        assert kwargs["candidate_cluster_radius_m"] == 25.0
        assert kwargs["candidate_min_cluster_nodes"] == 4
        assert kwargs["match_radius_m"] == 50.0
        return {
            "status": "pass",
            "claim_status": "diagnostic-demo",
            "reference_case_count": 3,
            "matched_case_count": 2,
            "summary_file": str(tmp_path / "summary.json"),
        }

    monkeypatch.setattr(osm_tools, "audit_reference_join_patterns", fake_audit)

    report = sumo_network_reference_join_audit(
        reference_net_file=str(reference_net_file),
        candidate_net_file=str(candidate_net_file),
        output_dir=str(tmp_path / "reference-join"),
        candidate_cluster_radius_m=25.0,
        candidate_min_cluster_nodes=4,
        match_radius_m=50.0,
    )

    assert report["status"] == "pass"
    assert report["matched_case_count"] == 2
    json.dumps(report)


def test_sumo_network_overlapping_junction_audit_tool_returns_json_compatible_report(
    monkeypatch, tmp_path: Path
) -> None:
    from torii_sumo.tools import osm_tools

    net_file = tmp_path / "candidate.net.xml"
    net_file.write_text("<net/>", encoding="utf-8")
    reference_join_file = tmp_path / "reference_join.json"
    reference_join_file.write_text('{"matched_cases": []}', encoding="utf-8")

    def fake_audit(**kwargs):
        assert kwargs["net_file"] == net_file
        assert kwargs["overlap_radius_m"] == 9.0
        assert kwargs["short_edge_length_m"] == 13.0
        assert kwargs["min_group_nodes"] == 3
        assert kwargs["reference_join_audit_report"] == {"matched_cases": []}
        return {
            "status": "pass",
            "claim_status": "diagnostic-demo",
            "overlapping_junction_group_count": 1,
            "summary_file": str(tmp_path / "summary.json"),
        }

    monkeypatch.setattr(osm_tools, "audit_overlapping_junctions", fake_audit)

    report = sumo_network_overlapping_junction_audit(
        net_file=str(net_file),
        output_dir=str(tmp_path / "overlap"),
        overlap_radius_m=9.0,
        short_edge_length_m=13.0,
        min_group_nodes=3,
        reference_join_audit_report_file=str(reference_join_file),
    )

    assert report["status"] == "pass"
    assert report["overlapping_junction_group_count"] == 1
    json.dumps(report)


def test_sumo_network_reference_hierarchy_audit_tool_returns_json_compatible_report(
    monkeypatch, tmp_path: Path
) -> None:
    from torii_sumo.tools import osm_tools

    reference_net_file = tmp_path / "reference.net.xml"
    candidate_net_file = tmp_path / "candidate.net.xml"
    reference_net_file.write_text("<net/>", encoding="utf-8")
    candidate_net_file.write_text("<net/>", encoding="utf-8")

    def fake_audit(**kwargs):
        assert kwargs["reference_net_file"] == reference_net_file
        assert kwargs["candidate_net_file"] == candidate_net_file
        assert kwargs["match_distance_m"] == 25.0
        assert kwargs["oversplit_length_ratio"] == 0.4
        assert kwargs["min_extra_edges"] == 2
        return {
            "status": "blocked",
            "claim_status": "blocked",
            "reference_hierarchy_status": "needs_review",
            "high_hierarchy_issue_count": 3,
            "cases_file": str(tmp_path / "cases.csv"),
        }

    monkeypatch.setattr(osm_tools, "audit_reference_hierarchy", fake_audit)

    report = sumo_network_reference_hierarchy_audit(
        reference_net_file=str(reference_net_file),
        candidate_net_file=str(candidate_net_file),
        output_dir=str(tmp_path / "hierarchy"),
        match_distance_m=25.0,
        oversplit_length_ratio=0.4,
        min_extra_edges=2,
    )

    assert report["status"] == "blocked"
    assert report["reference_hierarchy_status"] == "needs_review"
    assert report["high_hierarchy_issue_count"] == 3
    json.dumps(report)


def test_sumo_network_junction_aggregation_variant_tool_returns_json_compatible_report(
    monkeypatch, tmp_path: Path
) -> None:
    from torii_sumo.tools import osm_tools

    net_file = tmp_path / "candidate.net.xml"
    topology_report_file = tmp_path / "topology.json"
    overlap_report_file = tmp_path / "overlap.json"
    net_file.write_text("<net/>", encoding="utf-8")
    topology_report_file.write_text(
        json.dumps(
            {
                "suspicious_clusters": [
                    {
                        "cluster_id": "C001",
                        "aggregation_decision": "join",
                        "aggregation_confidence": "medium",
                        "node_ids": ["j1", "j2"],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    overlap_report_file.write_text(
        json.dumps({"overlapping_junction_groups": [{"group_id": "OJ001", "node_ids": ["j1", "j2"]}]}),
        encoding="utf-8",
    )

    def fake_aggregation(**kwargs):
        assert kwargs["net_file"] == net_file
        assert kwargs["join_dist_m"] == 25.0
        assert kwargs["topology_audit_report"]["suspicious_clusters"][0]["cluster_id"] == "C001"
        assert kwargs["overlapping_junction_audit_report"]["overlapping_junction_groups"][0]["group_id"] == "OJ001"
        return {
            "status": "pass",
            "claim_status": "blocked",
            "junction_aggregation_status": "variant_created_for_review",
            "junction_aggregation_candidate_count": 1,
            "junction_aggregation_variant_file": str(tmp_path / "variant.net.xml"),
        }

    monkeypatch.setattr(osm_tools, "build_junction_aggregation_variant", fake_aggregation)

    report = sumo_network_junction_aggregation_variant(
        net_file=str(net_file),
        output_dir=str(tmp_path / "aggregation"),
        topology_audit_report_file=str(topology_report_file),
        overlapping_junction_audit_report_file=str(overlap_report_file),
        join_dist_m=25.0,
    )

    assert report["status"] == "pass"
    assert report["junction_aggregation_candidate_count"] == 1
    json.dumps(report)


def test_sumo_network_reference_scope_audit_tool_returns_json_compatible_report(monkeypatch, tmp_path: Path) -> None:
    from torii_sumo.tools import osm_tools

    reference_net_file = tmp_path / "reference.net.xml"
    candidate_net_file = tmp_path / "candidate.net.xml"
    reference_net_file.write_text("<net/>", encoding="utf-8")
    candidate_net_file.write_text("<net/>", encoding="utf-8")

    def fake_audit(**kwargs):
        assert kwargs["reference_net_file"] == reference_net_file
        assert kwargs["candidate_net_file"] == candidate_net_file
        assert kwargs["overrepresentation_ratio"] == 1.75
        assert kwargs["max_prune_edge_length_m"] == 45.0
        return {
            "status": "blocked",
            "claim_status": "blocked",
            "reference_scope_status": "needs_pruning_review",
            "prune_candidate_count": 2,
            "report_file": str(tmp_path / "scope.json"),
        }

    monkeypatch.setattr(osm_tools, "audit_reference_scope", fake_audit)

    report = sumo_network_reference_scope_audit(
        reference_net_file=str(reference_net_file),
        candidate_net_file=str(candidate_net_file),
        output_dir=str(tmp_path / "scope"),
        overrepresentation_ratio=1.75,
        max_prune_edge_length_m=45.0,
    )

    assert report["status"] == "blocked"
    assert report["prune_candidate_count"] == 2
    json.dumps(report)


def test_sumo_network_scope_pruning_variant_tool_returns_json_compatible_report(monkeypatch, tmp_path: Path) -> None:
    from torii_sumo.tools import osm_tools

    net_file = tmp_path / "candidate.net.xml"
    scope_report_file = tmp_path / "scope.json"
    net_file.write_text("<net/>", encoding="utf-8")
    scope_report_file.write_text(
        json.dumps({"prune_candidates": [{"edge_id": "edge_a", "prune_decision": "prune_candidate"}]}),
        encoding="utf-8",
    )

    def fake_pruning(**kwargs):
        assert kwargs["net_file"] == net_file
        assert kwargs["reference_scope_report"]["prune_candidates"][0]["edge_id"] == "edge_a"
        return {
            "status": "pass",
            "claim_status": "blocked",
            "scope_pruning_status": "variant_created_for_review",
            "scope_pruning_removed_edge_count": 1,
            "scope_pruning_variant_file": str(tmp_path / "scope_pruned.net.xml"),
        }

    monkeypatch.setattr(osm_tools, "build_scope_pruning_variant", fake_pruning)

    report = sumo_network_scope_pruning_variant(
        net_file=str(net_file),
        reference_scope_report_file=str(scope_report_file),
        output_dir=str(tmp_path / "scope-pruning"),
    )

    assert report["status"] == "pass"
    assert report["scope_pruning_removed_edge_count"] == 1
    json.dumps(report)


def test_corridor_candidate_gate_tool_accepts_only_persisted_evidence_files(monkeypatch, tmp_path: Path) -> None:
    from torii_sumo.tools import osm_tools

    materialization_file = tmp_path / "materialization.json"
    review_file = tmp_path / "review.json"
    materialization_file.write_text(json.dumps({"status": "pass"}), encoding="utf-8")
    review_file.write_text(json.dumps({"status": "accepted"}), encoding="utf-8")

    def fake_gates(**kwargs):
        assert kwargs["materialization_report"]["_materialization_report_file"] == str(
            materialization_file.resolve()
        )
        assert kwargs["review_decision"]["_review_decision_file"] == str(review_file.resolve())
        return {"status": "blocked", "claim_status": "blocked"}

    monkeypatch.setattr(osm_tools, "run_corridor_candidate_gates", fake_gates)
    report = sumo_network_corridor_candidate_gates(
        source_net_file=str(tmp_path / "source.net.xml"),
        candidate_net_file=str(tmp_path / "candidate.net.xml"),
        output_dir=str(tmp_path / "gates"),
        materialization_report_file=str(materialization_file),
        review_decision_file=str(review_file),
    )

    parameters = inspect.signature(sumo_network_corridor_candidate_gates).parameters
    assert "semantic_allowances" not in parameters
    assert "tls_logic_allowances" not in parameters
    assert report["status"] == "blocked"


def test_sumo_network_corridor_geometry_simplification_tool_returns_json_report(
    monkeypatch, tmp_path: Path
) -> None:
    from torii_sumo.tools import osm_tools

    net_file = tmp_path / "candidate.net.xml"
    reference_file = tmp_path / "reference.net.xml"
    net_file.write_text("<net/>", encoding="utf-8")
    reference_file.write_text("<net/>", encoding="utf-8")

    def fake_simplification(**kwargs):
        assert kwargs["net_file"] == net_file
        assert kwargs["reference_net_file"] == reference_file
        assert kwargs["max_micro_edge_length_m"] == 0.75
        return {
            "status": "pass",
            "claim_status": "blocked",
            "corridor_geometry_simplification_status": "variant_created_for_review",
            "candidate_node_count": 2,
            "semantic_preservation_status": "pass",
        }

    monkeypatch.setattr(osm_tools, "build_corridor_geometry_simplification_variant", fake_simplification)

    report = sumo_network_corridor_geometry_simplification_variant(
        net_file=str(net_file),
        reference_net_file=str(reference_file),
        output_dir=str(tmp_path / "corridor"),
        max_micro_edge_length_m=0.75,
    )

    assert report["status"] == "pass"
    assert report["candidate_node_count"] == 2
    json.dumps(report)


def test_sumo_network_tls_aggregation_variant_tool_returns_json_compatible_report(monkeypatch, tmp_path: Path) -> None:
    from torii_sumo.tools import osm_tools

    net_file = tmp_path / "candidate.net.xml"
    tls_report_file = tmp_path / "tls_audit.json"
    net_file.write_text("<net/>", encoding="utf-8")
    tls_report_file.write_text(
        json.dumps(
            {
                "status": "pass",
                "tls_cluster_count": 2,
                "clusters_file": str(tmp_path / "tls_clusters.csv"),
            }
        ),
        encoding="utf-8",
    )

    def fake_tls_aggregation(**kwargs):
        assert kwargs["net_file"] == net_file
        assert kwargs["tls_audit_report"]["tls_cluster_count"] == 2
        return {
            "status": "pass",
            "claim_status": "blocked",
            "tls_aggregation_status": "variant_created_for_review",
            "tls_physical_cluster_count": 2,
            "tls_aggregation_variant_file": str(tmp_path / "tls_aggregated.net.xml"),
        }

    monkeypatch.setattr(osm_tools, "build_tls_aggregation_variant", fake_tls_aggregation)

    report = sumo_network_tls_aggregation_variant(
        net_file=str(net_file),
        tls_audit_report_file=str(tls_report_file),
        output_dir=str(tmp_path / "tls_aggregation"),
    )

    assert report["status"] == "pass"
    assert report["tls_physical_cluster_count"] == 2
    json.dumps(report)


def test_sumo_network_tls_reference_cleanup_tool_returns_json_compatible_report(
    monkeypatch,
    tmp_path: Path,
) -> None:
    from torii_sumo.tools import osm_tools

    net_file = tmp_path / "candidate.net.xml"
    net_file.write_text("<net/>", encoding="utf-8")

    def fake_cleanup(net_file_arg, *, output_dir, prefix):
        assert net_file_arg == net_file
        assert output_dir == tmp_path / "cleanup"
        assert prefix == "bounded"
        return {
            "status": "pass",
            "tls_reference_cleanup_status": "no_change",
            "effective_net_file": str(net_file),
        }

    monkeypatch.setattr(osm_tools, "build_tls_reference_cleanup_variant", fake_cleanup)
    report = sumo_network_tls_reference_cleanup_variant(
        net_file=str(net_file),
        output_dir=str(tmp_path / "cleanup"),
        prefix="bounded",
    )

    assert report["status"] == "pass"
    assert report["tls_reference_cleanup_status"] == "no_change"


def test_sumo_network_standard_nema_phase_binding_tool_returns_json_report(
    monkeypatch,
    tmp_path: Path,
) -> None:
    from torii_sumo.tools import osm_tools

    captured = {}

    def fake_binding(net_file: Path, **kwargs):
        captured.update({"net_file": net_file, **kwargs})
        return {
            "status": "pass",
            "claim_status": "diagnostic-demo",
            "nema_binding_status": "scan_complete",
            "scan_counts": {"eligible_count": 1},
        }

    monkeypatch.setattr(osm_tools, "build_standard_nema_phase_binding", fake_binding)

    report = sumo_network_standard_nema_phase_binding(
        net_file=str(tmp_path / "source.net.xml"),
        output_dir=str(tmp_path / "review"),
        junction_id="J0",
        prefix="probe",
        run_runtime_checks=False,
        run_routeability=False,
    )

    assert report["status"] == "pass"
    assert report["nema_binding_status"] == "scan_complete"
    assert captured["net_file"] == tmp_path / "source.net.xml"
    assert captured["output_dir"] == tmp_path / "review"
    assert captured["junction_id"] == "J0"
    assert captured["prefix"] == "probe"
    assert captured["run_runtime_checks"] is False
    assert captured["run_routeability"] is False
    json.dumps(report)


def test_sumo_network_connection_mode_audit_tool_returns_json_report(
    monkeypatch,
    tmp_path: Path,
) -> None:
    from torii_sumo.tools import osm_tools

    captured = {}

    def fake_audit(net_file: Path, **kwargs):
        captured.update({"net_file": net_file, **kwargs})
        return {
            "status": "review_required",
            "automatic_promotion_gate": "blocked",
            "review_required_count": 1,
        }

    monkeypatch.setattr(osm_tools, "build_network_connection_mode_audit", fake_audit)

    report = sumo_network_connection_mode_audit(
        net_file=str(tmp_path / "source.net.xml"),
        output_dir=str(tmp_path / "connection-review"),
        prefix="probe",
        junction_ids=["J0"],
    )

    assert report["status"] == "review_required"
    assert report["automatic_promotion_gate"] == "blocked"
    assert captured["net_file"] == tmp_path / "source.net.xml"
    assert captured["output_dir"] == tmp_path / "connection-review"
    assert captured["prefix"] == "probe"
    assert captured["junction_ids"] == ["J0"]
    json.dumps(report)


def test_sumo_network_connection_mode_regression_tool_returns_json_report(
    monkeypatch,
    tmp_path: Path,
) -> None:
    from torii_sumo.tools import osm_tools

    captured = {}

    def fake_regression(source_net_file: Path, candidate_net_file: Path, **kwargs):
        captured.update(
            {
                "source_net_file": source_net_file,
                "candidate_net_file": candidate_net_file,
                **kwargs,
            }
        )
        return {
            "status": "fail",
            "automatic_promotion_gate": "blocked",
            "outside_scope_new_review_finding_count": 2,
        }

    monkeypatch.setattr(
        osm_tools,
        "build_connection_mode_regression_audit",
        fake_regression,
    )

    report = sumo_network_connection_mode_regression_audit(
        source_net_file=str(tmp_path / "source.net.xml"),
        candidate_net_file=str(tmp_path / "candidate.net.xml"),
        output_dir=str(tmp_path / "regression"),
        prefix="delta",
        target_source_junction_ids=["old_j"],
        target_candidate_junction_ids=["new_j"],
    )

    assert report["status"] == "fail"
    assert captured["source_net_file"] == tmp_path / "source.net.xml"
    assert captured["candidate_net_file"] == tmp_path / "candidate.net.xml"
    assert captured["target_source_junction_ids"] == ["old_j"]
    assert captured["target_candidate_junction_ids"] == ["new_j"]
    assert captured["prefix"] == "delta"
    json.dumps(report)


def test_connection_mode_calibration_tool_requires_and_forwards_traffic_side(
    monkeypatch,
    tmp_path: Path,
) -> None:
    from torii_sumo.tools import osm_tools

    captured = {}

    class FakeCalibration:
        def model_dump(self, **_kwargs):
            return {"status": "pass", "endpoint_tolerance_m": 0.02}

    def fake_calibration(net_file: Path, **kwargs):
        captured.update({"net_file": net_file, **kwargs})
        return FakeCalibration()

    monkeypatch.setattr(
        osm_tools,
        "build_connection_mode_calibration_artifact",
        fake_calibration,
    )

    report = sumo_network_connection_mode_calibration(
        net_file=str(tmp_path / "source.net.xml"),
        output_dir=str(tmp_path / "calibration"),
        traffic_side="left",
        prefix="baseline",
    )

    assert report["status"] == "pass"
    assert captured["traffic_side"].value == "left"
    assert captured["output_file"] == tmp_path / "calibration" / "baseline.json"


def test_exact_semantic_regression_tool_forwards_hash_bound_contract(
    monkeypatch,
    tmp_path: Path,
) -> None:
    from torii_sumo.tools import osm_tools

    captured = {}

    def fake_exact(source: Path, candidate: Path, **kwargs):
        captured.update({"source": source, "candidate": candidate, **kwargs})
        return {"status": "pass", "automatic_promotion_gate": "pass"}

    monkeypatch.setattr(
        osm_tools,
        "build_exact_semantic_regression_artifacts",
        fake_exact,
    )

    report = sumo_network_exact_semantic_regression_audit(
        source_net_file=str(tmp_path / "source.net.xml"),
        candidate_net_file=str(tmp_path / "candidate.net.xml"),
        output_dir=str(tmp_path / "audit"),
        toolchain_lock_file=str(tmp_path / "toolchain.json"),
        traffic_side="right",
        target_source_junction_ids=["old"],
        target_candidate_junction_ids=["new"],
        calibration_file=str(tmp_path / "calibration.json"),
    )

    assert report["automatic_promotion_gate"] == "pass"
    assert captured["traffic_side"].value == "right"
    assert captured["calibration_file"] == tmp_path / "calibration.json"
    assert captured["target_source_junction_ids"] == ["old"]
    assert captured["target_candidate_junction_ids"] == ["new"]


def test_sumo_network_tls_warning_parity_tool_writes_reference_aware_report(tmp_path: Path) -> None:
    teacher_report = tmp_path / "teacher_load.json"
    candidate_report = tmp_path / "candidate_load.json"
    teacher_report.write_text(
        json.dumps(
            {
                "stderr_tail": "Warning: Unused states in tlLogic '7616444534', program '0' in phase 0 after tl-index 17\n"
                "Warning: Missing green phase in tlLogic '7616444534', program '0' for tl-index 9.",
            }
        ),
        encoding="utf-8",
    )
    candidate_report.write_text(
        json.dumps(
            {
                "stderr_tail": "Warning: Unused states in tlLogic 'cluster_tls', program '0' in phase 0 after tl-index 17\n"
                "Warning: Missing yellow phase in tlLogic 'cluster_tls', program '0' for tl-index 3 when switching to phase 2.",
            }
        ),
        encoding="utf-8",
    )

    report = sumo_network_tls_warning_parity(
        teacher_sumo_load_report_file=str(teacher_report),
        candidate_sumo_load_report_file=str(candidate_report),
        tls_id_map={"7616444534": "cluster_tls"},
        output_dir=str(tmp_path / "warning-parity"),
        prefix="probe",
    )

    assert report["status"] == "pass"
    assert report["inherited_warning_count"] == 1
    assert report["candidate_only_warning_count"] == 1
    assert report["teacher_only_warning_count"] == 1
    assert Path(report["warning_parity_file"]).is_file()
    json.dumps(report)


def test_sumo_network_teacher_guided_junction_variant_tool_returns_json_compatible_report(
    monkeypatch, tmp_path: Path
) -> None:
    from torii_sumo.tools import osm_tools

    raw_node_file = tmp_path / "raw.nod.xml"
    raw_edge_file = tmp_path / "raw.edg.xml"
    raw_connection_file = tmp_path / "raw.con.xml"
    teacher_net_file = tmp_path / "teacher.net.xml"
    candidate_net_file = tmp_path / "candidate.net.xml"
    raw_type_file = tmp_path / "raw.typ.xml"
    raw_tllogic_file = tmp_path / "raw.tll.xml"
    for path in (
        raw_node_file,
        raw_edge_file,
        raw_connection_file,
        teacher_net_file,
        candidate_net_file,
        raw_type_file,
        raw_tllogic_file,
    ):
        path.write_text("<xml/>", encoding="utf-8")

    def fake_builder(**kwargs):
        assert kwargs["raw_node_file"] == raw_node_file
        assert kwargs["raw_edge_file"] == raw_edge_file
        assert kwargs["raw_connection_file"] == raw_connection_file
        assert kwargs["raw_type_file"] == raw_type_file
        assert kwargs["raw_tllogic_file"] == raw_tllogic_file
        assert kwargs["teacher_net_file"] == teacher_net_file
        assert kwargs["candidate_net_file"] == candidate_net_file
        assert kwargs["teacher_junction_id"] == "teacher_j"
        assert kwargs["edge_map"] == {"teacher_in": "cand_in"}
        assert kwargs["crossing_edge_overrides"] == {":j_c5": "cand_crossing"}
        assert kwargs["source_conflict_core_node_ids"] == ["n1", "n2"]
        assert kwargs["replay_target_internal_subgraph"] is True
        assert kwargs["preserve_teacher_lane_shapes"] is False
        return {
            "status": "pass",
            "claim_status": "diagnostic-demo",
            "final_net_file": str(tmp_path / "teacher_guided.net.xml"),
            "parity": {"delta": {"vehicle_connection_count": 0}},
        }

    monkeypatch.setattr(osm_tools, "build_teacher_guided_junction_variant", fake_builder)

    report = sumo_network_teacher_guided_junction_variant(
        raw_node_file=str(raw_node_file),
        raw_edge_file=str(raw_edge_file),
        raw_connection_file=str(raw_connection_file),
        raw_type_file=str(raw_type_file),
        raw_tllogic_file=str(raw_tllogic_file),
        teacher_net_file=str(teacher_net_file),
        candidate_net_file=str(candidate_net_file),
        junction_id="j",
        teacher_junction_id="teacher_j",
        output_dir=str(tmp_path / "teacher-guided"),
        edge_map={"teacher_in": "cand_in"},
        crossing_edge_overrides={":j_c5": "cand_crossing"},
        source_conflict_core_node_ids=["n1", "n2"],
        replay_target_internal_subgraph=True,
        preserve_teacher_lane_shapes=False,
    )

    assert report["status"] == "pass"
    assert report["claim_status"] == "diagnostic-demo"
    assert report["parity"]["delta"]["vehicle_connection_count"] == 0
    json.dumps(report)


def test_sumo_network_teacher_guided_junction_variant_tool_replays_internal_subgraph_by_default(
    monkeypatch, tmp_path: Path
) -> None:
    from torii_sumo.tools import osm_tools

    for name in ("raw.nod.xml", "raw.edg.xml", "raw.con.xml", "teacher.net.xml", "candidate.net.xml"):
        (tmp_path / name).write_text("<xml/>", encoding="utf-8")

    def fake_builder(**kwargs):
        assert kwargs["replay_target_internal_subgraph"] is True
        return {"status": "pass", "claim_status": "diagnostic-demo"}

    monkeypatch.setattr(osm_tools, "build_teacher_guided_junction_variant", fake_builder)

    report = sumo_network_teacher_guided_junction_variant(
        raw_node_file=str(tmp_path / "raw.nod.xml"),
        raw_edge_file=str(tmp_path / "raw.edg.xml"),
        raw_connection_file=str(tmp_path / "raw.con.xml"),
        teacher_net_file=str(tmp_path / "teacher.net.xml"),
        candidate_net_file=str(tmp_path / "candidate.net.xml"),
        junction_id="j",
        output_dir=str(tmp_path / "out"),
        edge_map={},
    )

    assert report["status"] == "pass"


def test_sumo_network_teacher_guided_repair_queue_tool_returns_json_compatible_report(
    monkeypatch, tmp_path: Path
) -> None:
    from torii_sumo.tools import osm_tools

    raw_node_file = tmp_path / "raw.nod.xml"
    raw_edge_file = tmp_path / "raw.edg.xml"
    raw_connection_file = tmp_path / "raw.con.xml"
    raw_type_file = tmp_path / "raw.typ.xml"
    raw_tllogic_file = tmp_path / "raw.tll.xml"
    for path in (raw_node_file, raw_edge_file, raw_connection_file, raw_type_file, raw_tllogic_file):
        path.write_text("<xml/>", encoding="utf-8")
    queue_file = tmp_path / "queue.json"
    queue_file.write_text(
        json.dumps(
            {
                "teacher_net_file": str(tmp_path / "teacher.net.xml"),
                "candidate_net_file": str(tmp_path / "candidate.net.xml"),
                "repair_candidates": [],
            }
        ),
        encoding="utf-8",
    )

    def fake_runner(**kwargs):
        assert kwargs["queue_report"]["repair_candidates"] == []
        assert kwargs["raw_node_file"] == raw_node_file
        assert kwargs["raw_edge_file"] == raw_edge_file
        assert kwargs["raw_connection_file"] == raw_connection_file
        assert kwargs["raw_type_file"] == raw_type_file
        assert kwargs["raw_tllogic_file"] == raw_tllogic_file
        assert kwargs["output_dir"] == tmp_path / "queue-run"
        assert kwargs["queue_base_dir"] == queue_file.resolve().parent
        assert kwargs["replay_target_internal_subgraph"] is True
        return {
            "status": "blocked",
            "claim_status": "blocked",
            "attempted_candidate_count": 0,
            "skipped_candidate_count": 0,
        }

    monkeypatch.setattr(osm_tools, "run_teacher_guided_repair_queue", fake_runner)

    report = sumo_network_teacher_guided_repair_queue(
        queue_report_file=str(queue_file),
        raw_node_file=str(raw_node_file),
        raw_edge_file=str(raw_edge_file),
        raw_connection_file=str(raw_connection_file),
        raw_type_file=str(raw_type_file),
        raw_tllogic_file=str(raw_tllogic_file),
        output_dir=str(tmp_path / "queue-run"),
        replay_target_internal_subgraph=True,
    )

    assert report["status"] == "blocked"
    assert report["claim_status"] == "blocked"
    json.dumps(report)


def test_sumo_network_teacher_guided_repair_queue_tool_replays_internal_subgraph_by_default(
    monkeypatch, tmp_path: Path
) -> None:
    from torii_sumo.tools import osm_tools

    for name in ("raw.nod.xml", "raw.edg.xml", "raw.con.xml"):
        (tmp_path / name).write_text("<xml/>", encoding="utf-8")
    queue_file = tmp_path / "queue.json"
    queue_file.write_text(json.dumps({"repair_candidates": []}), encoding="utf-8")

    def fake_runner(**kwargs):
        assert kwargs["replay_target_internal_subgraph"] is True
        return {"status": "blocked", "claim_status": "blocked"}

    monkeypatch.setattr(osm_tools, "run_teacher_guided_repair_queue", fake_runner)

    report = sumo_network_teacher_guided_repair_queue(
        queue_report_file=str(queue_file),
        raw_node_file=str(tmp_path / "raw.nod.xml"),
        raw_edge_file=str(tmp_path / "raw.edg.xml"),
        raw_connection_file=str(tmp_path / "raw.con.xml"),
        output_dir=str(tmp_path / "out"),
    )

    assert report["status"] == "blocked"
