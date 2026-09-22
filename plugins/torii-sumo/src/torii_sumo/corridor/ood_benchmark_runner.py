from __future__ import annotations

from pathlib import Path
from typing import Any

from torii_sumo.core.artifact_io import write_json_atomic
from torii_sumo.core.candidate_contracts import file_sha256

from .applicability import (
    CertificationEnvelope,
    evaluate_certification_applicability,
)
from .enums import GateStatus
from .ood_benchmark_contracts import (
    OODBenchmarkCaseResult,
    OODBenchmarkReport,
    OODBenchmarkSpec,
)
from .synthetic_case_engine import (
    CleanSyntheticFixture,
    materialize_clean_synthetic_fixture,
    run_synthetic_mutation_sequence,
)
from .synthetic_networks import FIXTURES


def run_ood_benchmark(
    spec_file: Path,
    *,
    parent_benchmark_file: Path,
    certification_envelope_file: Path,
    output_dir: Path,
    prefix: str = "ood_benchmark",
) -> dict[str, Any]:
    spec_path = spec_file.resolve()
    parent_path = parent_benchmark_file.resolve()
    envelope_path = certification_envelope_file.resolve()
    destination = output_dir.resolve()
    spec = OODBenchmarkSpec.model_validate_json(spec_path.read_text(encoding="utf-8"))
    envelope = CertificationEnvelope.model_validate_json(
        envelope_path.read_text(encoding="utf-8")
    )
    if file_sha256(parent_path) != spec.parent_benchmark_sha256:
        raise ValueError("OOD benchmark parent hash mismatch.")
    if file_sha256(envelope_path) != spec.certification_envelope_sha256:
        raise ValueError("OOD benchmark certification envelope hash mismatch.")
    destination.mkdir(parents=True, exist_ok=True)
    fixture_dir = destination / "fixtures"
    case_dir = destination / "cases"
    fixture_cache: dict[tuple[str, str], CleanSyntheticFixture] = {}
    clean_statuses: dict[str, GateStatus] = {}
    results: list[OODBenchmarkCaseResult] = []
    blockers: list[str] = []
    artifacts: set[Path] = {spec_path, parent_path, envelope_path}
    for case in spec.cases:
        if case.fixture_id not in FIXTURES:
            raise ValueError(f"Unknown OOD fixture: {case.fixture_id!r}.")
        fixture_key = (case.fixture_id, case.traffic_side.value)
        fixture = fixture_cache.get(fixture_key)
        if fixture is None:
            fixture = materialize_clean_synthetic_fixture(
                fixture_id=case.fixture_id,
                traffic_side=case.traffic_side,
                fixture_dir=fixture_dir,
            )
            fixture_cache[fixture_key] = fixture
            clean_key = f"{case.fixture_id}:{case.traffic_side.value}"
            clean_statuses[clean_key] = fixture.status
            expected_clean = FIXTURES[case.fixture_id].expected_clean_status
            if fixture.status is not expected_clean:
                blockers.append(
                    f"clean_fixture_status_mismatch:{clean_key}:"
                    f"expected_{expected_clean.value}:observed_{fixture.status.value}"
                )
            artifacts.update(fixture.artifact_paths)
        if case.mutation_ids:
            evidence = run_synthetic_mutation_sequence(
                case_id=case.case_id,
                mutation_ids=case.mutation_ids,
                clean_fixture=fixture,
                traffic_side=case.traffic_side,
                case_dir=case_dir,
            )
            snapshot = evidence.candidate_snapshot
            evaluated_path = evidence.mutant_path
            evaluated_sha256 = evidence.mutant_sha256
            source_immutable = evidence.source_immutable
            connection_status = str(evidence.candidate_audit.get("status", "unknown"))
            safety_status = evidence.candidate_safety.status
            artifacts.update(evidence.artifact_paths)
        else:
            snapshot = fixture.snapshot
            evaluated_path = fixture.path
            evaluated_sha256 = fixture.sha256
            source_immutable = file_sha256(fixture.path) == fixture.sha256
            connection_status = str(fixture.audit.get("status", "unknown"))
            safety_status = fixture.safety.status
        applicability = evaluate_certification_applicability(snapshot, envelope)
        report_path = case_dir / f"{case.case_id}.applicability.json"
        write_json_atomic(
            report_path,
            applicability.model_dump(mode="json", by_alias=True),
            sort_keys=True,
        )
        artifacts.add(report_path)
        observed_reasons = tuple(
            sorted({finding.category for finding in applicability.findings})
        )
        expected_reasons = tuple(sorted(case.expected_reason_categories))
        case_blockers: list[str] = []
        decision_matched = applicability.decision == case.expected_decision
        reasons_matched = observed_reasons == expected_reasons
        if not source_immutable:
            case_blockers.append("source_fixture_mutated")
        if not decision_matched:
            case_blockers.append(
                f"domain_decision_mismatch:expected_{case.expected_decision}:"
                f"observed_{applicability.decision}"
            )
        if not reasons_matched:
            case_blockers.append(
                "domain_reason_mismatch:"
                f"expected_{','.join(expected_reasons)}:"
                f"observed_{','.join(observed_reasons)}"
            )
        if safety_status is not case.expected_independent_safety_status:
            case_blockers.append(
                "independent_safety_status_mismatch:"
                f"expected_{case.expected_independent_safety_status.value}:"
                f"observed_{safety_status.value}"
            )
        result = OODBenchmarkCaseResult(
            case_id=case.case_id,
            status=GateStatus.FAIL if case_blockers else GateStatus.PASS,
            expected_decision=case.expected_decision,
            observed_decision=applicability.decision,
            expected_reason_categories=expected_reasons,
            observed_reason_categories=observed_reasons,
            decision_matched=decision_matched,
            reasons_matched_exactly=reasons_matched,
            source_sha256=fixture.sha256,
            evaluated_sha256=evaluated_sha256,
            source_immutable=source_immutable,
            mutation_ids=case.mutation_ids,
            connection_status=connection_status,
            independent_safety_status=safety_status,
            expected_independent_safety_status=(
                case.expected_independent_safety_status
            ),
            certification_gate=applicability.certification_gate,
            applicability_report_path=str(report_path),
            blockers=tuple(case_blockers),
        )
        result_path = case_dir / f"{case.case_id}.result.json"
        write_json_atomic(
            result_path,
            result.model_dump(mode="json", by_alias=True),
            sort_keys=True,
        )
        artifacts.add(result_path)
        artifacts.add(evaluated_path)
        results.append(result)

    failed_count = sum(result.status is not GateStatus.PASS for result in results)
    if failed_count:
        blockers.append(f"ood_case_failures:{failed_count}")
    expected_ood = [
        result for result in results if result.expected_decision == "out-of-domain"
    ]
    expected_in = [
        result for result in results if result.expected_decision == "in-domain"
    ]
    detected_ood = sum(
        result.observed_decision == "out-of-domain" for result in expected_ood
    )
    accepted_in = sum(
        result.observed_decision == "in-domain" for result in expected_in
    )
    overconfident = sum(
        result.observed_decision == "in-domain" for result in expected_ood
    )
    false_ood = sum(
        result.observed_decision == "out-of-domain" for result in expected_in
    )
    report = OODBenchmarkReport(
        benchmark_id=spec.benchmark_id,
        benchmark_spec_sha256=file_sha256(spec_path),
        certification_envelope_sha256=file_sha256(envelope_path),
        status=GateStatus.FAIL if blockers else GateStatus.PASS,
        total_case_count=len(results),
        passed_case_count=len(results) - failed_count,
        failed_case_count=failed_count,
        expected_out_of_domain_count=len(expected_ood),
        detected_out_of_domain_count=detected_ood,
        out_of_domain_recall=round(detected_ood / len(expected_ood), 6),
        expected_in_domain_count=len(expected_in),
        accepted_in_domain_count=accepted_in,
        in_domain_acceptance_rate=round(accepted_in / len(expected_in), 6),
        overconfident_case_count=overconfident,
        false_ood_case_count=false_ood,
        clean_fixture_statuses=clean_statuses,
        cases=tuple(results),
        blockers=tuple(dict.fromkeys(blockers)),
    )
    report_path = destination / f"{prefix}.report.json"
    write_json_atomic(
        report_path,
        report.model_dump(mode="json", by_alias=True),
        sort_keys=True,
    )
    artifacts.add(report_path)
    manifest_path = destination / f"{prefix}.manifest.json"
    write_json_atomic(
        manifest_path,
        {
            "schema": "torii.corridor.ood-benchmark-manifest/v1",
            "benchmark_id": spec.benchmark_id,
            "status": report.status.value,
            "benchmark_spec": {
                "path": str(spec_path),
                "sha256": file_sha256(spec_path),
            },
            "parent_benchmark": {
                "path": str(parent_path),
                "sha256": file_sha256(parent_path),
            },
            "certification_envelope": {
                "path": str(envelope_path),
                "sha256": file_sha256(envelope_path),
            },
            "source_fixtures_immutable": all(
                result.source_immutable for result in results
            ),
            "artifacts": [
                {"path": str(path), "sha256": file_sha256(path)}
                for path in sorted(artifacts, key=lambda item: item.as_posix())
            ],
        },
        sort_keys=True,
    )
    return {
        **report.model_dump(mode="json", by_alias=True),
        "report_file": str(report_path),
        "manifest_file": str(manifest_path),
    }
