from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from torii_sumo.core.artifact_io import write_json_atomic
from torii_sumo.core.candidate_contracts import file_sha256
from torii_sumo.corridor.held_out_review_contracts import (
    BlindReviewDecision,
    HeldOutAdjudication,
    HeldOutCaseStratum,
    HeldOutReviewPolicy,
    HeldOutReviewMaterial,
    MachineAssessment,
)
from torii_sumo.corridor.held_out_review_runner import (
    build_blinded_review_artifacts,
    evaluate_held_out_review_trial,
)
from torii_sumo.corridor.ids import stable_id
from torii_sumo.corridor.review import ReviewCase
from torii_sumo.corridor.schema import (
    build_held_out_review_contract_bundle_schema,
    build_held_out_review_policy_schema,
    build_held_out_review_report_schema,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
PARENT_BENCHMARK = (
    REPOSITORY_ROOT
    / "benchmarks"
    / "corridor_human_modeling_v1"
    / "benchmark.v1.json"
)
PREREGISTRATION = (
    REPOSITORY_ROOT
    / "benchmarks"
    / "corridor_human_modeling_v1"
    / "held_out_review_preregistration.v1.json"
)


def _sha(label: str) -> str:
    import hashlib

    return hashlib.sha256(label.encode("utf-8")).hexdigest()


def _policy(*, minimum_case_count: int = 4) -> HeldOutReviewPolicy:
    reviewer_ids = (
        stable_id("review", {"reviewer-slot": "A"}),
        stable_id("review", {"reviewer-slot": "B"}),
    )
    payload = {
        "parent_benchmark_sha256": file_sha256(PARENT_BENCHMARK),
        "reviewer_ids": reviewer_ids,
        "adjudicator_id": stable_id("review", {"adjudicator-slot": "A"}),
        "minimum_case_count": minimum_case_count,
        "minimum_city_group_count": 2,
        "minimum_morphology_count": 4,
        "minimum_cases_per_city_group": 2,
        "required_traffic_sides": ("right", "left"),
        "required_mode_features": ("pedestrian", "rail"),
        "minimum_raw_agreement": 0.8,
        "minimum_cohen_kappa": 0.6,
        "minimum_attention_precision": 0.9,
        "minimum_attention_recall": 0.95,
        "minimum_auto_precision": 0.99,
        "maximum_median_review_seconds": 300.0,
        "maximum_safety_critical_false_negatives": 0,
    }
    return HeldOutReviewPolicy(
        trial_id=stable_id("review", payload),
        **payload,
    )


def _review_case(index: int) -> ReviewCase:
    review_case_id = stable_id("review", {"held-out-case": index})
    variants = (
        stable_id("candidate", {"held-out-case": index, "variant": "A"}),
        stable_id("candidate", {"held-out-case": index, "variant": "B"}),
    )
    return ReviewCase(
        review_case_id=review_case_id,
        source_sha256=_sha(f"source-{index}"),
        candidate_sha256_by_variant={
            variant_id: _sha(f"candidate-{index}-{variant_id}")
            for variant_id in variants
        },
        scope_id=stable_id("scope", {"held-out-case": index}),
        finding_ids=(stable_id("finding", {"held-out-case": index}),),
        affected_stable_entity_ids=(
            stable_id("movement", {"held-out-case": index}),
        ),
        decision_type="corridor-modeling-validity",
        machine_question="Which candidate matches the observed road semantics?",
        candidate_variant_ids=variants,
        machine_recommendation=variants[0],
        confidence_components={"structural": 1.0, "semantic": 0.5},
        passed_gates=("identity", "structural"),
        unresolved_gates=("semantic",),
        evidence_refs=(stable_id("evidence", {"held-out-case": index}),),
        required_observations=("lane use", "legal movement", "conflict geometry"),
        rollback_artifact_id=stable_id("artifact", {"held-out-case": index}),
    )


def _prepare_trial(
    tmp_path: Path,
    *,
    machine_labels: tuple[str, ...],
) -> tuple[HeldOutReviewPolicy, dict, Path]:
    policy = _policy()
    cases = tuple(_review_case(index) for index in range(4))
    assessments = {
        case.review_case_id: MachineAssessment(
            machine_label=machine_labels[index],
            machine_report_sha256=_sha(f"machine-report-{index}"),
            finding_categories=("held-out-finding",),
            safety_critical=index in {0, 3},
        )
        for index, case in enumerate(cases)
    }
    strata = {
        cases[0].review_case_id: {
            "city_group": "held-out-city-a",
            "morphology": "grid",
            "traffic_side": "right",
            "osm_completeness": "high",
            "mode_features": ("road-motorized", "pedestrian"),
        },
        cases[1].review_case_id: {
            "city_group": "held-out-city-a",
            "morphology": "historic-core",
            "traffic_side": "left",
            "osm_completeness": "medium",
            "mode_features": ("road-motorized", "bicycle"),
        },
        cases[2].review_case_id: {
            "city_group": "held-out-city-b",
            "morphology": "ramp-interchange",
            "traffic_side": "right",
            "osm_completeness": "low",
            "mode_features": ("road-motorized", "ramp"),
        },
        cases[3].review_case_id: {
            "city_group": "held-out-city-b",
            "morphology": "tram-rail",
            "traffic_side": "left",
            "osm_completeness": "medium",
            "mode_features": ("road-motorized", "rail"),
        },
    }
    package = build_blinded_review_artifacts(
        cases,
        machine_assessments=assessments,
        case_strata=strata,
        trial_id=policy.trial_id,
        created_at=datetime(2026, 7, 14, 12, 0, tzinfo=UTC),
        blinding_seed="held-out-blinding-seed-000000000001",
        output_dir=tmp_path / "package",
    )
    policy_path = tmp_path / "policy.json"
    write_json_atomic(
        policy_path,
        policy.model_dump(mode="json", by_alias=True),
        sort_keys=True,
    )
    return policy, package, policy_path


def _write_human_evidence(
    tmp_path: Path,
    *,
    policy: HeldOutReviewPolicy,
    package: dict,
    labels: tuple[str, ...],
) -> tuple[list[Path], list[Path]]:
    dataset = json.loads(
        Path(package["blinded_dataset_file"]).read_text(encoding="utf-8")
    )
    evaluation_key = json.loads(
        Path(package["evaluation_key_file"]).read_text(encoding="utf-8")
    )
    label_by_case_code = {
        key_case["case_code"]: labels[index]
        for key_case in evaluation_key["cases"]
        for index in range(len(labels))
        if key_case["review_case_id"] == _review_case(index).review_case_id
    }
    base_time = datetime(2026, 7, 14, 13, 0, tzinfo=UTC)
    decision_paths: list[Path] = []
    adjudication_paths: list[Path] = []
    for index, case in enumerate(dataset["cases"]):
        case_code = case["case_code"]
        selected_variant = next(iter(case["candidate_sha256_by_variant_code"]))
        decisions: list[BlindReviewDecision] = []
        for reviewer_index, reviewer_id in enumerate(policy.reviewer_ids):
            started_at = base_time + timedelta(minutes=index * 10 + reviewer_index)
            label = label_by_case_code[case_code]
            decision = BlindReviewDecision(
                decision_id=stable_id(
                    "review",
                    {
                        "trial": policy.trial_id,
                        "case": case_code,
                        "reviewer": reviewer_id,
                    },
                ),
                trial_id=policy.trial_id,
                case_code=case_code,
                reviewer_id=reviewer_id,
                label=label,
                selected_variant_code=(
                    selected_variant if label == "acceptable" else None
                ),
                started_at=started_at,
                decided_at=started_at + timedelta(seconds=30 + index),
                observed_facts=("reviewed lane and movement evidence",),
                rationale="Independent blinded judgment based on the required evidence.",
            )
            decisions.append(decision)
            path = tmp_path / f"decision-{index}-{reviewer_index}.json"
            write_json_atomic(
                path,
                decision.model_dump(mode="json", by_alias=True),
                sort_keys=True,
            )
            decision_paths.append(path)
        adjudication = HeldOutAdjudication(
            adjudication_id=stable_id(
                "review",
                {"trial": policy.trial_id, "case": case_code, "adjudication": 1},
            ),
            trial_id=policy.trial_id,
            case_code=case_code,
            reviewer_decision_ids=tuple(
                decision.decision_id for decision in decisions
            ),
            adjudicator_id=policy.adjudicator_id,
            final_label=label_by_case_code[case_code],
            selected_variant_code=(
                selected_variant
                if label_by_case_code[case_code] == "acceptable"
                else None
            ),
            decided_at=base_time + timedelta(hours=2, minutes=index),
            observed_facts=("compared both independent evidence records",),
            rationale="Adjudicated against the preregistered corridor evidence.",
        )
        path = tmp_path / f"adjudication-{index}.json"
        write_json_atomic(
            path,
            adjudication.model_dump(mode="json", by_alias=True),
            sort_keys=True,
        )
        adjudication_paths.append(path)
    return decision_paths, adjudication_paths


def test_blinded_trial_separates_machine_key_and_computes_passing_metrics(
    tmp_path: Path,
) -> None:
    labels = ("defect", "acceptable", "ambiguous", "defect")
    policy, package, policy_path = _prepare_trial(
        tmp_path,
        machine_labels=("defect", "acceptable", "ambiguous", "ambiguous"),
    )
    dataset_text = Path(package["blinded_dataset_file"]).read_text(encoding="utf-8")
    key_text = Path(package["evaluation_key_file"]).read_text(encoding="utf-8")
    assert "machine_label" not in dataset_text
    assert '"machine_recommendation_hidden": true' in dataset_text
    assert "machine_label" in key_text
    for index in range(4):
        assert _review_case(index).review_case_id not in dataset_text
    decision_paths, adjudication_paths = _write_human_evidence(
        tmp_path,
        policy=policy,
        package=package,
        labels=labels,
    )

    report = evaluate_held_out_review_trial(
        policy_file=policy_path,
        parent_benchmark_file=PARENT_BENCHMARK,
        blinded_dataset_file=Path(package["blinded_dataset_file"]),
        evaluation_key_file=Path(package["evaluation_key_file"]),
        decision_files=decision_paths,
        adjudication_files=adjudication_paths,
        output_dir=tmp_path / "evaluation",
    )

    assert report["status"] == "pass"
    assert report["automatic_promotion_gate"] == "pass"
    assert report["metrics"]["raw_agreement"] == 1.0
    assert report["metrics"]["cohen_kappa"] == 1.0
    assert report["metrics"]["attention_precision"] == 1.0
    assert report["metrics"]["attention_recall"] == 1.0
    assert report["metrics"]["auto_precision"] == 1.0
    assert report["metrics"]["safety_critical_false_negative_count"] == 0


def test_safety_critical_false_negative_blocks_held_out_trial(tmp_path: Path) -> None:
    labels = ("defect", "acceptable", "ambiguous", "defect")
    policy, package, policy_path = _prepare_trial(
        tmp_path,
        machine_labels=("acceptable", "acceptable", "ambiguous", "ambiguous"),
    )
    decision_paths, adjudication_paths = _write_human_evidence(
        tmp_path,
        policy=policy,
        package=package,
        labels=labels,
    )

    report = evaluate_held_out_review_trial(
        policy_file=policy_path,
        parent_benchmark_file=PARENT_BENCHMARK,
        blinded_dataset_file=Path(package["blinded_dataset_file"]),
        evaluation_key_file=Path(package["evaluation_key_file"]),
        decision_files=decision_paths,
        adjudication_files=adjudication_paths,
        output_dir=tmp_path / "evaluation",
    )

    assert report["status"] == "blocked"
    assert report["automatic_promotion_gate"] == "blocked"
    assert report["metrics"]["safety_critical_false_negative_count"] == 1
    assert "held_out_safety_critical_false_negative" in report["blockers"]


def test_missing_human_evidence_remains_explicitly_blocked(tmp_path: Path) -> None:
    policy, package, policy_path = _prepare_trial(
        tmp_path,
        machine_labels=("defect", "acceptable", "ambiguous", "ambiguous"),
    )

    report = evaluate_held_out_review_trial(
        policy_file=policy_path,
        parent_benchmark_file=PARENT_BENCHMARK,
        blinded_dataset_file=Path(package["blinded_dataset_file"]),
        evaluation_key_file=Path(package["evaluation_key_file"]),
        decision_files=(),
        adjudication_files=(),
        output_dir=tmp_path / "evaluation",
    )

    assert report["status"] == "blocked"
    assert report["metrics"]["completed_review_count"] == 0
    assert report["metrics"]["adjudicated_case_count"] == 0
    assert any(
        blocker.startswith("held_out_case_requires_exactly_two_reviews")
        for blocker in report["blockers"]
    )


def test_post_hoc_machine_label_change_breaks_hash_and_blocks(tmp_path: Path) -> None:
    policy, package, policy_path = _prepare_trial(
        tmp_path,
        machine_labels=("defect", "acceptable", "ambiguous", "ambiguous"),
    )
    key_path = Path(package["evaluation_key_file"])
    key = json.loads(key_path.read_text(encoding="utf-8"))
    assessment_path = key_path.parent / key["cases"][0][
        "machine_assessment_artifact_path"
    ]
    assessment = json.loads(assessment_path.read_text(encoding="utf-8"))
    assessment["machine_label"] = "acceptable"
    write_json_atomic(assessment_path, assessment, sort_keys=True)

    report = evaluate_held_out_review_trial(
        policy_file=policy_path,
        parent_benchmark_file=PARENT_BENCHMARK,
        blinded_dataset_file=Path(package["blinded_dataset_file"]),
        evaluation_key_file=key_path,
        decision_files=(),
        adjudication_files=(),
        output_dir=tmp_path / "evaluation",
    )

    assert report["status"] == "blocked"
    assert any(
        blocker.startswith("machine_assessment_artifact_hash_mismatch")
        for blocker in report["blockers"]
    )


def test_false_independence_attestation_is_rejected() -> None:
    payload = {
        "decision_id": stable_id("review", {"decision": "false-attestation"}),
        "trial_id": stable_id("review", {"trial": "false-attestation"}),
        "case_code": "case-0123456789ab",
        "reviewer_id": stable_id("review", {"reviewer": "false-attestation"}),
        "label": "defect",
        "started_at": "2026-07-14T12:00:00+00:00",
        "decided_at": "2026-07-14T12:01:00+00:00",
        "observed_facts": ["fact"],
        "rationale": "rationale",
        "independent_review_attested": False,
    }

    with pytest.raises(ValueError):
        BlindReviewDecision.model_validate(payload)


def test_held_out_review_schemas_are_current() -> None:
    schemas = {
        "torii.corridor.held-out-review-policy.v1.schema.json": (
            build_held_out_review_policy_schema()
        ),
        "torii.corridor.held-out-review-contract-bundle.v1.schema.json": (
            build_held_out_review_contract_bundle_schema()
        ),
        "torii.corridor.held-out-review-report.v1.schema.json": (
            build_held_out_review_report_schema()
        ),
    }
    for filename, schema in schemas.items():
        expected = json.dumps(schema, indent=2, ensure_ascii=False, sort_keys=True)
        assert (REPOSITORY_ROOT / "schemas" / filename).read_text(
            encoding="utf-8"
        ) == expected


def test_real_held_out_trial_policy_is_preregistered_and_hash_bound() -> None:
    policy = HeldOutReviewPolicy.model_validate_json(
        PREREGISTRATION.read_text(encoding="utf-8")
    )

    assert policy.parent_benchmark_sha256 == file_sha256(PARENT_BENCHMARK)
    assert policy.minimum_case_count == 30
    assert policy.minimum_city_group_count == 3
    assert policy.minimum_morphology_count == 6
    assert {side.value for side in policy.required_traffic_sides} == {
        "right",
        "left",
    }
    assert set(policy.required_mode_features) == {
        "pedestrian",
        "bicycle",
        "ramp",
        "rail",
        "bridge",
        "tunnel",
    }
    assert policy.maximum_safety_critical_false_negatives == 0


def test_blinded_package_materializes_review_html_network_and_display_overlay(
    tmp_path: Path,
) -> None:
    policy = _policy(minimum_case_count=2)
    base_case = _review_case(0)
    variant_files: dict[str, Path] = {}
    candidate_hashes: dict[str, str] = {}
    for index, variant_id in enumerate(base_case.candidate_variant_ids):
        path = tmp_path / f"candidate-{index}.net.xml"
        path.write_text(f'<net version="1.20" id="{index}"/>', encoding="utf-8")
        variant_files[variant_id] = path
        candidate_hashes[variant_id] = file_sha256(path)
    case_payload = base_case.model_dump(mode="json", by_alias=True)
    case_payload["candidate_sha256_by_variant"] = candidate_hashes
    review_case = ReviewCase.model_validate(case_payload)
    overlay = tmp_path / "review.add.xml"
    overlay.write_text(
        """<additional>
  <poi id="review-marker" x="0" y="0" color="255,165,0">
    <param key="display_only" value="true"/>
  </poi>
</additional>
""",
        encoding="utf-8",
    )
    assessment = MachineAssessment(
        machine_label="ambiguous",
        machine_report_sha256=_sha("material-machine-report"),
        finding_categories=("human-map-review-required",),
        safety_critical=False,
    )
    stratum = HeldOutCaseStratum(
        city_group="material-city",
        morphology="multimodal",
        traffic_side="left",
        osm_completeness="unassessed",
        mode_features=("road-motorized", "pedestrian", "bicycle"),
    )
    material = HeldOutReviewMaterial(
        candidate_artifact_path_by_variant={
            variant_id: str(path) for variant_id, path in variant_files.items()
        },
        review_overlay_path=str(overlay),
        map_evidence_urls=(
            "https://www.openstreetmap.org/#map=17/0/0",
            "https://www.google.com/maps/search/?api=1&query=0,0",
        ),
    )

    package = build_blinded_review_artifacts(
        (review_case,),
        machine_assessments={review_case.review_case_id: assessment},
        case_strata={review_case.review_case_id: stratum},
        review_materials={review_case.review_case_id: material},
        trial_id=policy.trial_id,
        created_at=datetime(2026, 7, 14, 12, 0, tzinfo=UTC),
        blinding_seed="material-blinding-seed-000000000001",
        output_dir=tmp_path / "package",
    )

    dataset_path = Path(package["blinded_dataset_file"])
    dataset = json.loads(dataset_path.read_text(encoding="utf-8"))
    blind_case = dataset["cases"][0]
    assert set(blind_case["candidate_artifact_path_by_variant_code"]) == set(
        blind_case["candidate_sha256_by_variant_code"]
    )
    for code, relative_path in blind_case[
        "candidate_artifact_path_by_variant_code"
    ].items():
        assert file_sha256(dataset_path.parent / relative_path) == blind_case[
            "candidate_sha256_by_variant_code"
        ][code]
    html_path = dataset_path.parent / blind_case["review_html_path"]
    html_text = html_path.read_text(encoding="utf-8")
    assert "display-only" in html_text
    assert "Machine recommendations and peer decisions are hidden" in html_text
    assert "machine_label" not in html_text
    assert review_case.review_case_id not in html_text
    for variant_id in review_case.candidate_variant_ids:
        assert variant_id not in html_text
    copied_overlay = dataset_path.parent / blind_case["review_overlay_path"]
    assert "<tlLogic" not in copied_overlay.read_text(encoding="utf-8")
    package_manifest = json.loads(
        Path(package["manifest_file"]).read_text(encoding="utf-8")
    )
    assert any(
        artifact["role"] == "reviewer-visible-case-material"
        for artifact in package_manifest["artifacts"]
    )


def test_blinded_package_rejects_behavioral_additional_xml(tmp_path: Path) -> None:
    policy = _policy(minimum_case_count=2)
    base_case = _review_case(0)
    candidate_files: dict[str, str] = {}
    candidate_hashes: dict[str, str] = {}
    for index, variant_id in enumerate(base_case.candidate_variant_ids):
        path = tmp_path / f"candidate-{index}.net.xml"
        path.write_text(f'<net version="1.20" id="{index}"/>', encoding="utf-8")
        candidate_files[variant_id] = str(path)
        candidate_hashes[variant_id] = file_sha256(path)
    case_payload = base_case.model_dump(mode="json", by_alias=True)
    case_payload["candidate_sha256_by_variant"] = candidate_hashes
    review_case = ReviewCase.model_validate(case_payload)
    overlay = tmp_path / "behavioral.add.xml"
    overlay.write_text(
        """<additional>
  <tlLogic id="forbidden" type="static" programID="review" offset="0"/>
</additional>
""",
        encoding="utf-8",
    )
    assessment = MachineAssessment(
        machine_label="ambiguous",
        machine_report_sha256=_sha("behavioral-overlay-machine-report"),
        finding_categories=("human-map-review-required",),
        safety_critical=False,
    )
    stratum = HeldOutCaseStratum(
        city_group="material-city",
        morphology="multimodal",
        traffic_side="right",
        osm_completeness="unassessed",
        mode_features=("road-motorized",),
    )
    material = HeldOutReviewMaterial(
        candidate_artifact_path_by_variant=candidate_files,
        review_overlay_path=str(overlay),
        map_evidence_urls=("https://www.openstreetmap.org/#map=17/0/0",),
    )

    with pytest.raises(ValueError, match="display-only additional.xml"):
        build_blinded_review_artifacts(
            (review_case,),
            machine_assessments={review_case.review_case_id: assessment},
            case_strata={review_case.review_case_id: stratum},
            review_materials={review_case.review_case_id: material},
            trial_id=policy.trial_id,
            created_at=datetime(2026, 7, 14, 12, 0, tzinfo=UTC),
            blinding_seed="behavioral-overlay-blinding-seed-0001",
            output_dir=tmp_path / "package",
        )
