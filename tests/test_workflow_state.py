from torii_sumo.core.workflow_state import (
    NetworkQualityVector,
    StageResult,
    build_promotion_trace,
    summarize_workflow_stages,
)


def test_network_quality_vector_serializes_named_metrics() -> None:
    quality = NetworkQualityVector(
        connectivity={"status": "pass"},
        routeability={"status": "blocked"},
        topology_fragmentation={"clusters": 2},
        tls_semantic_delta={"missing": 1},
        junction_pattern_delta={"extra": 3},
        reference_scope_delta={"outside_scope": 0},
        manual_review_load=4,
    )

    assert quality.as_dict() == {
        "connectivity": {"status": "pass"},
        "routeability": {"status": "blocked"},
        "topology_fragmentation": {"clusters": 2},
        "tls_semantic_delta": {"missing": 1},
        "junction_pattern_delta": {"extra": 3},
        "reference_scope_delta": {"outside_scope": 0},
        "manual_review_load": 4,
    }


def test_stage_result_serializes_quality_artifacts_and_claim_boundary() -> None:
    result = StageResult(
        stage_name="teacher_guided_repair",
        status="pass",
        output_artifacts={"best_variant": "best.net.xml"},
        after_quality=NetworkQualityVector(tls_semantic_delta={"status": "pass"}),
        promotion_decision="promote",
        evidence_files=["run.json"],
    )

    assert result.as_dict() == {
        "stage_name": "teacher_guided_repair",
        "status": "pass",
        "input_artifacts": {},
        "output_artifacts": {"best_variant": "best.net.xml"},
        "before_quality": NetworkQualityVector().as_dict(),
        "after_quality": {
            **NetworkQualityVector().as_dict(),
            "tls_semantic_delta": {"status": "pass"},
        },
        "delta_quality": {},
        "promotion_decision": "promote",
        "claim_status": "diagnostic-demo",
        "evidence_files": ["run.json"],
        "warnings": [],
    }


def test_summarize_workflow_stages_groups_existing_reference_matched_fields() -> None:
    stages = summarize_workflow_stages(
        {
            "claim_status": "diagnostic-demo",
            "routeability_audit_status": "pass",
            "teacher_guided_repair_run_status": "pass",
            "teacher_guided_repair_promotion_gate_status": "pass",
            "teacher_guided_repair_best_variant_file": "teacher_best.net.xml",
            "teacher_guided_repair_run_report_file": "teacher_run.json",
            "teacher_guided_repair_semantic_layer_gate_counts": {
                "topology": {"pass": 1},
            },
            "road_connectivity_replay_status": "pass",
            "road_connectivity_replay_gate_status": "pass",
            "road_connectivity_replay_best_variant_file": "road_best.net.xml",
            "road_connectivity_replay_run_report_file": "road_run.json",
            "workflow_review_html_status": "pass",
            "workflow_review_html_file": "review.html",
            "review_manifest_file": "manifest.json",
            "human_review_required_count": 3,
        }
    )

    by_name = {stage.stage_name: stage for stage in stages}

    assert list(by_name) == [
        "teacher_guided_repair",
        "road_connectivity",
        "routeability",
        "review_html",
    ]
    assert by_name["teacher_guided_repair"].promotion_decision == "pass"
    assert by_name["teacher_guided_repair"].output_artifacts == {
        "best_variant": "teacher_best.net.xml",
        "run_report": "teacher_run.json",
    }
    assert by_name["teacher_guided_repair"].after_quality.tls_semantic_delta == {
        "topology": {"pass": 1},
    }
    assert by_name["road_connectivity"].output_artifacts == {
        "best_variant": "road_best.net.xml",
        "run_report": "road_run.json",
    }
    assert by_name["routeability"].after_quality.routeability == {"status": "pass"}
    assert by_name["review_html"].after_quality.manual_review_load == 3


def test_summarize_workflow_stages_omits_absent_stages() -> None:
    assert summarize_workflow_stages({}) == []


def test_build_promotion_trace_serializes_stage_quality_deltas() -> None:
    trace = build_promotion_trace(
        case_id="small_probe",
        claim_status="construction-invalid",
        source_artifact="workflow_report.json",
        stages=[
            StageResult(
                stage_name="routeability",
                status="pass",
                after_quality=NetworkQualityVector(routeability={"status": "pass"}),
                promotion_decision="pass",
            ),
            StageResult(
                stage_name="review_html",
                status="blocked",
                after_quality=NetworkQualityVector(manual_review_load=3),
                promotion_decision="blocked_review_required",
            ),
        ],
    )

    assert trace == {
        "case_id": "small_probe",
        "claim_status": "construction-invalid",
        "source_artifact": "workflow_report.json",
        "stages": [
            {
                "stage_id": "routeability",
                "before_quality": NetworkQualityVector().as_dict(),
                "after_quality": {
                    **NetworkQualityVector().as_dict(),
                    "routeability": {"status": "pass"},
                },
                "delta_quality": {},
                "promotion_decision": "pass",
            },
            {
                "stage_id": "review_html",
                "before_quality": NetworkQualityVector().as_dict(),
                "after_quality": {
                    **NetworkQualityVector().as_dict(),
                    "manual_review_load": 3,
                },
                "delta_quality": {},
                "promotion_decision": "blocked_review_required",
            },
        ],
    }
