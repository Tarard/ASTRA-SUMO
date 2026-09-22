from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import sys
from pathlib import Path


SCRIPT = Path("plugins/torii-sumo/scripts/run_ingolstadt_corridor_teacher.py")


def _load_script():
    spec = importlib.util.spec_from_file_location("run_ingolstadt_corridor_teacher", SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _args(**overrides):
    values = {
        "bbox": "11.0,48.0,12.0,49.0",
        "junction_id": "267517510",
        "source_osm": None,
        "candidate_net": None,
        "historical_date": None,
        "map_temporal_scope": "current",
        "map_target_date": None,
        "overpass_url": "https://overpass-api.de/api/interpreter",
        "timeout_seconds": 45.0,
        "skip_runtime_audits": False,
        "materialize_teacher_candidates": False,
        "verbose": False,
    }
    values.update(overrides)
    return argparse.Namespace(**values)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _input_parity_fields(bbox: str = "11.0,48.0,12.0,49.0") -> dict[str, object]:
    return {
        "candidate_bbox": bbox,
        "reference_bbox_scope": {"status": "pass", "candidate_bbox": bbox},
    }


def test_default_cli_mode_remains_bounded_slice(monkeypatch) -> None:
    script = _load_script()
    monkeypatch.setattr(sys, "argv", [str(SCRIPT)])

    args = script._parse_args()

    assert args.workflow_mode == "bounded-slice"
    assert args.materialize_teacher_candidates is False


def test_reference_matched_mode_uses_seven_field_audit_contract(tmp_path: Path) -> None:
    script = _load_script()
    source = tmp_path / "source.osm.xml"
    teacher = tmp_path / "teacher.net.xml"
    source.write_text("<osm version='0.6'/>", encoding="utf-8")
    teacher.write_text("<net version='1.20'/>", encoding="utf-8")
    args = _args(source_osm=source)

    kwargs = script._reference_matched_workflow_kwargs(
        args,
        output_dir=tmp_path / "run",
        teacher_net=teacher,
    )

    assert kwargs == {
        "output_dir": str(tmp_path / "run" / "reference_matched"),
        "bbox": args.bbox,
        "profile": "reference_matched",
        "source_osm_path": str(source),
        "traffic_layers": None,
        "reference_net_file": str(teacher),
        "timeout_seconds": 45.0,
    }


def test_reference_matched_teacher_materialization_is_rejected(tmp_path: Path) -> None:
    script = _load_script()
    output_dir = tmp_path / "run"
    output_dir.mkdir()
    source = tmp_path / "source.osm.xml"
    teacher = tmp_path / "teacher.net.xml"
    source.write_text("<osm/>", encoding="utf-8")
    teacher.write_text("<net/>", encoding="utf-8")

    result = script._run_reference_matched(
        _args(source_osm=source, materialize_teacher_candidates=True),
        output_dir=output_dir,
        teacher_net=teacher,
        binaries={"netconvert": "netconvert-test", "sumo": "sumo-test"},
        workflow_func=lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError("audit-only cleanup must not materialize teacher candidates")
        ),
    )

    report = json.loads((output_dir / "ingolstadt_corridor_teacher_run.json").read_text(encoding="utf-8"))
    assert result == 1
    assert report["status"] == "blocked"
    assert report["candidate_net_file"] == ""


def test_comparison_selector_does_not_claim_review_layer_is_promoted(tmp_path: Path) -> None:
    script = _load_script()
    review_only = tmp_path / "review-only.net.xml"
    review_only.write_text("<net/>", encoding="utf-8")

    assert (
        script._select_reference_matched_comparison_net({"reference_join_aggregation_variant_file": str(review_only)})
        == ""
    )
    assert script._select_reference_matched_comparison_net(
        {"reference_visual_detail_comparison_net_file": str(review_only)}
    ) == str(review_only.resolve())


def test_reference_matched_run_hash_binds_source_teacher_and_workflow_artifacts(
    tmp_path: Path,
) -> None:
    script = _load_script()
    output_dir = tmp_path / "run"
    output_dir.mkdir()
    source = tmp_path / "source.osm.xml"
    teacher = tmp_path / "teacher.net.xml"
    raw = tmp_path / "raw.net.xml"
    candidate = tmp_path / "candidate.net.xml"
    workflow_report = tmp_path / "workflow.json"
    review_manifest = tmp_path / "review.manifest.json"
    reference_join_report = tmp_path / "reference-join.json"
    for path, payload in (
        (source, "<osm version='0.6'/>"),
        (teacher, "<net version='1.20'/>"),
        (raw, "<net id='raw'/>"),
        (candidate, "<net id='candidate'/>"),
        (workflow_report, "{}"),
        (review_manifest, "{}"),
    ):
        path.write_text(payload, encoding="utf-8")
    reference_join_report.write_text(
        json.dumps(
            {
                "reference_net_file": str(teacher),
                "candidate_net_file": str(candidate),
                "all_cases": [
                    {
                        "reference_id": "cluster_1_2",
                        "reference_type": "traffic_light",
                        "reference_joined_source_nodes": ["1", "2"],
                        "reference_approach_edge_ids": ["in", "out"],
                        "matched_reference_source_node_ids": ["1", "2"],
                        "reference_source_node_match_ratio": 1.0,
                        "matched_reference_source_internal_edge_ids": ["core"],
                        "matched_reference_source_boundary_edge_ids": ["in", "out"],
                        "matched_candidate_node_ids": ["1", "2", "storage"],
                        "matched_candidate_risk_flags": ["map_review_required"],
                        "match_status": "matched",
                    },
                    {
                        "reference_id": "cluster_3_4",
                        "reference_type": "priority",
                        "reference_joined_source_nodes": ["3", "4"],
                        "matched_reference_source_node_ids": ["3"],
                        "reference_source_node_match_ratio": 0.5,
                        "matched_reference_source_internal_edge_ids": [],
                        "matched_reference_source_boundary_edge_ids": ["edge"],
                        "matched_candidate_node_ids": [],
                        "match_status": "unmatched",
                    },
                    {
                        "reference_id": "cluster_5_6",
                        "reference_type": "priority",
                        "reference_joined_source_nodes": ["5", "6"],
                        "reference_approach_edge_ids": ["in-2", "out-2"],
                        "matched_reference_source_node_ids": ["5", "6"],
                        "reference_source_node_match_ratio": 1.0,
                        "matched_reference_source_internal_edge_ids": ["core-2"],
                        "matched_reference_source_boundary_edge_ids": ["in-2", "out-2"],
                        "matched_candidate_node_ids": ["5", "6"],
                        "matched_candidate_risk_flags": [],
                        "match_status": "matched",
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    source_sha = _sha256(source)
    teacher_sha = _sha256(teacher)
    captured: dict[str, object] = {}

    def fake_workflow(**kwargs):
        captured.update(kwargs)
        return {
            **_input_parity_fields(),
            "status": "pass",
            "claim_status": "diagnostic-demo",
            "network_profile": "reference_matched",
            "raw_net_file": str(raw),
            "net_file": str(raw),
            "reference_visual_detail_comparison_net_file": str(candidate),
            "reference_join_audit_status": "pass",
            "reference_join_audit_report_file": str(reference_join_report),
            "reference_join_matched_case_count": 2,
            "reference_join_unmatched_case_count": 0,
            "routeability_audit_status": "pass",
            "workflow_review_html_status": "pass",
            "artifact_hash_gate_status": "pass",
            "workflow_report_file": str(workflow_report),
            "review_manifest_file": str(review_manifest),
            "gate_status": {"network_build": "pass"},
        }

    result = script._run_reference_matched(
        _args(source_osm=source),
        output_dir=output_dir,
        teacher_net=teacher,
        binaries={"netconvert": "netconvert-test", "sumo": "sumo-test"},
        workflow_func=fake_workflow,
    )

    assert result == 0
    assert captured == {
        "output_dir": str(output_dir / "reference_matched"),
        "bbox": "11.0,48.0,12.0,49.0",
        "profile": "reference_matched",
        "source_osm_path": str(source.resolve()),
        "traffic_layers": None,
        "reference_net_file": str(teacher),
        "timeout_seconds": 45.0,
    }
    assert _sha256(source) == source_sha
    assert _sha256(teacher) == teacher_sha
    manifest = json.loads((output_dir / "ingolstadt_corridor_teacher_run.manifest.json").read_text(encoding="utf-8"))
    artifact_hashes = {Path(item["path"]).name: item["sha256"] for item in manifest["artifacts"]}
    assert artifact_hashes[source.name] == source_sha
    assert artifact_hashes[teacher.name] == teacher_sha
    assert workflow_report.name in artifact_hashes
    assert review_manifest.name in artifact_hashes
    assert reference_join_report.name in artifact_hashes
    assert "ingolstadt_teacher_action_contracts.json" in artifact_hashes
    aggregate = json.loads((output_dir / "ingolstadt_corridor_teacher_run.json").read_text(encoding="utf-8"))
    assert aggregate["runtime_audited_net_file"] == str(raw)
    assert aggregate["comparison_net_file"] == str(candidate.resolve())
    assert aggregate["candidate_net_file"] == ""
    assert aggregate["promotion_gate_status"] == "blocked"
    assert aggregate["execution_status"] == "pass"
    assert aggregate["evidence_status"] == "pass"
    assert aggregate["comparison_status"] == "review_ready"
    assert aggregate["teacher_action_contracts"]["action_count"] == 3
    assert aggregate["teacher_action_contracts"]["action_family_counts"] == {
        "abstain_unmatched_reference_case": 1,
        "bounded_conflict_core_join": 1,
        "source_identity_join_review": 1,
    }
    action_contracts = json.loads((output_dir / "ingolstadt_teacher_action_contracts.json").read_text(encoding="utf-8"))
    provenance = action_contracts["input_provenance"]
    assert provenance["bbox"] == "11.0,48.0,12.0,49.0"
    assert provenance["artifacts"]["source_osm"]["sha256"] == source_sha
    assert provenance["artifacts"]["teacher_net"]["sha256"] == teacher_sha
    assert provenance["artifacts"]["reference_join_audit"]["sha256"] == _sha256(reference_join_report)
    assert provenance["artifacts"]["comparison_net"]["sha256"] == _sha256(candidate)
    join_action = action_contracts["actions"][0]
    assert join_action["action_family"] == "source_identity_join_review"
    assert join_action["teacher_action"]["absorbed_source_node_ids"] == ["1", "2"]
    assert join_action["teacher_action"]["absorbed_internal_edge_ids"] == ["core"]
    assert join_action["teacher_action"]["retained_boundary_edge_ids"] == ["in", "out"]
    assert join_action["counterexample_evidence"]["candidate_nodes_outside_teacher_core"] == ["storage"]
    assert join_action["transfer_gate_status"] == "blocked"
    clean_join_action = action_contracts["actions"][2]
    assert clean_join_action["action_family"] == "bounded_conflict_core_join"
    assert clean_join_action["counterexample_evidence"]["blockers"] == []


def test_reference_matched_run_separates_successful_estimator_from_review_evidence(
    tmp_path: Path,
) -> None:
    script = _load_script()
    output_dir = tmp_path / "run"
    output_dir.mkdir()
    source = tmp_path / "source.osm.xml"
    teacher = tmp_path / "teacher.net.xml"
    raw = tmp_path / "raw.net.xml"
    comparison = tmp_path / "comparison.net.xml"
    for path in (source, teacher, raw, comparison):
        path.write_text("<net/>", encoding="utf-8")

    result = script._run_reference_matched(
        _args(source_osm=source),
        output_dir=output_dir,
        teacher_net=teacher,
        binaries={"netconvert": "netconvert-test", "sumo": "sumo-test"},
        workflow_func=lambda **_kwargs: {
            **_input_parity_fields(),
            "status": "fail",
            "raw_net_file": str(raw),
            "net_file": str(raw),
            "reference_visual_detail_comparison_net_file": str(comparison),
            "routeability_audit_status": "pass",
            "workflow_review_html_status": "pass",
            "artifact_hash_gate_status": "pass",
            "gate_status": {"reference_road_alignment": "needs_review"},
        },
    )

    assert result == 0
    report = json.loads((output_dir / "ingolstadt_corridor_teacher_run.json").read_text(encoding="utf-8"))
    assert report["status"] == "pass"
    assert report["execution_status"] == "pass"
    assert report["evidence_status"] == "review_required"
    assert report["promotion_gate_status"] == "blocked"


def test_reference_matched_mode_rejects_explicit_candidate(tmp_path: Path) -> None:
    script = _load_script()
    output_dir = tmp_path / "run"
    output_dir.mkdir()
    source = tmp_path / "source.osm.xml"
    teacher = tmp_path / "teacher.net.xml"
    candidate = tmp_path / "candidate.net.xml"
    source.write_text("<osm/>", encoding="utf-8")
    teacher.write_text("<net/>", encoding="utf-8")
    candidate.write_text("<net/>", encoding="utf-8")
    called = False

    def fake_workflow(**_kwargs):
        nonlocal called
        called = True
        return {"status": "pass"}

    result = script._run_reference_matched(
        _args(source_osm=source, candidate_net=candidate),
        output_dir=output_dir,
        teacher_net=teacher,
        binaries={"netconvert": "netconvert-test", "sumo": "sumo-test"},
        workflow_func=fake_workflow,
    )

    assert result == 1
    assert called is False
    report = json.loads((output_dir / "ingolstadt_corridor_teacher_run.json").read_text(encoding="utf-8"))
    assert report["status"] == "blocked"
    assert report["claim_status"] == "construction-invalid"


def test_reference_matched_run_does_not_hide_blocked_runtime_audit(tmp_path: Path) -> None:
    script = _load_script()
    output_dir = tmp_path / "run"
    output_dir.mkdir()
    source = tmp_path / "source.osm.xml"
    teacher = tmp_path / "teacher.net.xml"
    raw = tmp_path / "raw.net.xml"
    source.write_text("<osm/>", encoding="utf-8")
    teacher.write_text("<net/>", encoding="utf-8")
    raw.write_text("<net/>", encoding="utf-8")

    result = script._run_reference_matched(
        _args(source_osm=source),
        output_dir=output_dir,
        teacher_net=teacher,
        binaries={"netconvert": "netconvert-test", "sumo": "sumo-test"},
        workflow_func=lambda **_kwargs: {
            **_input_parity_fields(),
            "status": "pass",
            "claim_status": "diagnostic-demo",
            "network_profile": "reference_matched",
            "raw_net_file": str(raw),
            "net_file": str(raw),
            "reference_visual_detail_comparison_net_file": str(raw),
            "routeability_audit_status": "blocked",
            "workflow_review_html_status": "pass",
            "artifact_hash_gate_status": "pass",
        },
    )

    assert result == 1
    report = json.loads((output_dir / "ingolstadt_corridor_teacher_run.json").read_text(encoding="utf-8"))
    assert report["status"] == "blocked"
    assert report["runtime_audit_status"] == "blocked"


def test_reference_matched_run_requires_inner_artifact_hash_gate(tmp_path: Path) -> None:
    script = _load_script()
    output_dir = tmp_path / "run"
    output_dir.mkdir()
    source = tmp_path / "source.osm.xml"
    teacher = tmp_path / "teacher.net.xml"
    raw = tmp_path / "raw.net.xml"
    comparison = tmp_path / "comparison.net.xml"
    for path in (source, teacher, raw, comparison):
        path.write_text("<net/>", encoding="utf-8")

    result = script._run_reference_matched(
        _args(source_osm=source),
        output_dir=output_dir,
        teacher_net=teacher,
        binaries={"netconvert": "netconvert-test", "sumo": "sumo-test"},
        workflow_func=lambda **_kwargs: {
            **_input_parity_fields(),
            "status": "pass",
            "raw_net_file": str(raw),
            "net_file": str(raw),
            "reference_visual_detail_comparison_net_file": str(comparison),
            "routeability_audit_status": "pass",
            "workflow_review_html_status": "pass",
            "artifact_hash_gate_status": "blocked",
        },
    )

    assert result == 1
    report = json.loads((output_dir / "ingolstadt_corridor_teacher_run.json").read_text(encoding="utf-8"))
    assert report["execution_status"] == "blocked"
    assert report["artifact_hash_gate_status"] == "blocked"
    assert report["comparison_status"] == "review_ready"
    assert report["promotion_gate_status"] == "blocked"


def test_reference_matched_run_blocks_if_teacher_changes_during_workflow(tmp_path: Path) -> None:
    script = _load_script()
    output_dir = tmp_path / "run"
    output_dir.mkdir()
    source = tmp_path / "source.osm.xml"
    teacher = tmp_path / "teacher.net.xml"
    raw = tmp_path / "raw.net.xml"
    comparison = tmp_path / "comparison.net.xml"
    for path in (source, teacher, raw, comparison):
        path.write_text("<net version='before'/>", encoding="utf-8")

    def fake_workflow(**_kwargs):
        teacher.write_text("<net version='after'/>", encoding="utf-8")
        return {
            **_input_parity_fields(),
            "status": "pass",
            "raw_net_file": str(raw),
            "net_file": str(raw),
            "reference_visual_detail_comparison_net_file": str(comparison),
            "routeability_audit_status": "pass",
            "workflow_review_html_status": "pass",
            "artifact_hash_gate_status": "pass",
        }

    result = script._run_reference_matched(
        _args(source_osm=source),
        output_dir=output_dir,
        teacher_net=teacher,
        binaries={"netconvert": "netconvert-test", "sumo": "sumo-test"},
        workflow_func=fake_workflow,
    )

    assert result == 1
    report = json.loads((output_dir / "ingolstadt_corridor_teacher_run.json").read_text(encoding="utf-8"))
    assert report["input_parity"]["status"] == "blocked"
    assert report["input_parity"]["teacher_net"]["unchanged"] is False


def test_reference_matched_run_blocks_mismatched_reference_bbox(tmp_path: Path) -> None:
    script = _load_script()
    output_dir = tmp_path / "run"
    output_dir.mkdir()
    source = tmp_path / "source.osm.xml"
    teacher = tmp_path / "teacher.net.xml"
    raw = tmp_path / "raw.net.xml"
    comparison = tmp_path / "comparison.net.xml"
    for path in (source, teacher, raw, comparison):
        path.write_text("<net/>", encoding="utf-8")

    result = script._run_reference_matched(
        _args(source_osm=source),
        output_dir=output_dir,
        teacher_net=teacher,
        binaries={"netconvert": "netconvert-test", "sumo": "sumo-test"},
        workflow_func=lambda **_kwargs: {
            **_input_parity_fields("11.1,48.1,12.1,49.1"),
            "status": "pass",
            "raw_net_file": str(raw),
            "net_file": str(raw),
            "reference_visual_detail_comparison_net_file": str(comparison),
            "routeability_audit_status": "pass",
            "workflow_review_html_status": "pass",
            "artifact_hash_gate_status": "pass",
        },
    )

    assert result == 1
    report = json.loads((output_dir / "ingolstadt_corridor_teacher_run.json").read_text(encoding="utf-8"))
    assert report["input_parity"]["status"] == "blocked"
    assert "bboxes are not identical" in report["input_parity"]["blockers"][0]
