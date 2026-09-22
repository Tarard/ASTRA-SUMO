from __future__ import annotations

import json
from pathlib import Path

import pytest

from torii_sumo.core.candidate_contracts import file_sha256
from torii_sumo.corridor.applicability import (
    CertificationEnvelope,
    evaluate_certification_applicability,
)
from torii_sumo.corridor.canonicalizer import CanonicalNetworkSnapshot
from torii_sumo.corridor.enums import TrafficSide
from torii_sumo.corridor.ood_benchmark_contracts import OODBenchmarkSpec
from torii_sumo.corridor.ood_benchmark_runner import run_ood_benchmark
from torii_sumo.corridor.schema import (
    build_certification_applicability_report_schema,
    build_certification_envelope_schema,
    build_ood_benchmark_report_schema,
    build_ood_benchmark_schema,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
BENCHMARK_DIR = REPOSITORY_ROOT / "benchmarks" / "corridor_human_modeling_v1"
PARENT_SPEC = BENCHMARK_DIR / "benchmark.v1.json"
ENVELOPE_SPEC = BENCHMARK_DIR / "vehicle_x4_certification_envelope.v1.json"
OOD_SPEC = BENCHMARK_DIR / "ood_matrix.v1.json"


def test_ood_matrix_and_certification_envelope_are_content_bound() -> None:
    envelope = CertificationEnvelope.model_validate_json(
        ENVELOPE_SPEC.read_text(encoding="utf-8")
    )
    benchmark = OODBenchmarkSpec.model_validate_json(
        OOD_SPEC.read_text(encoding="utf-8")
    )

    assert envelope.claim_name == "vehicle-x4-independent-conflict-audit-v1"
    assert benchmark.parent_benchmark_sha256 == file_sha256(PARENT_SPEC)
    assert benchmark.certification_envelope_sha256 == file_sha256(ENVELOPE_SPEC)
    assert len(benchmark.cases) == 16
    assert sum(case.expected_decision == "in-domain" for case in benchmark.cases) == 4
    assert sum(
        case.expected_decision == "out-of-domain" for case in benchmark.cases
    ) == 12
    assert {case.traffic_side.value for case in benchmark.cases} == {
        "left",
        "right",
    }


def test_ood_matrix_separates_domain_shift_from_in_domain_defects(
    tmp_path: Path,
) -> None:
    result = run_ood_benchmark(
        OOD_SPEC,
        parent_benchmark_file=PARENT_SPEC,
        certification_envelope_file=ENVELOPE_SPEC,
        output_dir=tmp_path / "ood-run",
    )

    assert result["status"] == "pass"
    assert result["total_case_count"] == 16
    assert result["passed_case_count"] == 16
    assert result["failed_case_count"] == 0
    assert result["expected_out_of_domain_count"] == 12
    assert result["detected_out_of_domain_count"] == 12
    assert result["out_of_domain_recall"] == 1.0
    assert result["expected_in_domain_count"] == 4
    assert result["accepted_in_domain_count"] == 4
    assert result["in_domain_acceptance_rate"] == 1.0
    assert result["overconfident_case_count"] == 0
    assert result["false_ood_case_count"] == 0
    cases = {case["case_id"]: case for case in result["cases"]}
    in_domain_defect = cases["in-domain-protected-conflict"]
    assert in_domain_defect["observed_decision"] == "in-domain"
    assert in_domain_defect["certification_gate"] == "pass"
    assert in_domain_defect["independent_safety_status"] == "blocked"
    assert cases["three-arm-topology-ood"]["observed_reason_categories"] == [
        "physical_cell_arm_count_outside_envelope"
    ]
    assert cases["shared-controller-two-cells-ood"][
        "observed_reason_categories"
    ] == ["controller_ownership_outside_envelope"]
    assert cases["shared-controller-two-cells-ood"][
        "independent_safety_status"
    ] == "pass"
    manifest = json.loads(Path(result["manifest_file"]).read_text(encoding="utf-8"))
    assert manifest["status"] == "pass"
    assert manifest["source_fixtures_immutable"] is True
    assert manifest["certification_envelope"]["sha256"] == file_sha256(
        ENVELOPE_SPEC
    )


def test_applicability_fails_closed_when_canonical_evidence_is_empty() -> None:
    envelope = CertificationEnvelope.model_validate_json(
        ENVELOPE_SPEC.read_text(encoding="utf-8")
    )
    empty = CanonicalNetworkSnapshot(
        traffic_side=TrafficSide.RIGHT,
        entities=(),
    )

    report = evaluate_certification_applicability(empty, envelope)

    assert report.decision == "invalid"
    assert report.classification_status.value == "fail"
    assert report.certification_gate.value == "blocked"
    assert report.blockers == (
        "canonical_snapshot_contains_no_physical_cells",
        "canonical_snapshot_contains_no_controllers",
    )


def test_ood_runner_rejects_an_unbound_envelope(tmp_path: Path) -> None:
    changed = json.loads(ENVELOPE_SPEC.read_text(encoding="utf-8"))
    changed["claim_name"] = "silently-changed-claim"
    changed_path = tmp_path / "changed-envelope.json"
    changed_path.write_text(
        json.dumps(changed, indent=2, ensure_ascii=False, sort_keys=True),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="envelope_id does not match"):
        run_ood_benchmark(
            OOD_SPEC,
            parent_benchmark_file=PARENT_SPEC,
            certification_envelope_file=changed_path,
            output_dir=tmp_path / "invalid-run",
        )


def test_ood_benchmark_schemas_are_current() -> None:
    schemas = {
        "torii.corridor.certification-envelope.v1.schema.json": (
            build_certification_envelope_schema()
        ),
        "torii.corridor.certification-applicability-report.v1.schema.json": (
            build_certification_applicability_report_schema()
        ),
        "torii.corridor.ood-benchmark.v1.schema.json": (
            build_ood_benchmark_schema()
        ),
        "torii.corridor.ood-benchmark-report.v1.schema.json": (
            build_ood_benchmark_report_schema()
        ),
    }
    for filename, schema in schemas.items():
        expected = json.dumps(schema, indent=2, ensure_ascii=False, sort_keys=True)
        assert (REPOSITORY_ROOT / "schemas" / filename).read_text(
            encoding="utf-8"
        ) == expected
