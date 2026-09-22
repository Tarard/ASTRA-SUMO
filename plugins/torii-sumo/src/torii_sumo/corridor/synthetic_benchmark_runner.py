from __future__ import annotations

from pathlib import Path
from typing import Any

from torii_sumo.core.artifact_io import write_json_atomic
from torii_sumo.core.candidate_contracts import file_sha256
from .enums import GateStatus
from .synthetic_benchmark_contracts import (
    SyntheticFaultBenchmarkReport,
    SyntheticFaultBenchmarkSpec,
    SyntheticFaultCaseResult,
)
from .synthetic_case_engine import (
    CleanSyntheticFixture,
    match_synthetic_observation,
    materialize_clean_synthetic_fixture,
    run_synthetic_mutation_sequence,
)
from .synthetic_networks import FIXTURES


def run_synthetic_fault_benchmark(
    spec_file: Path,
    *,
    parent_benchmark_file: Path,
    output_dir: Path,
    prefix: str = "synthetic_fault_benchmark",
) -> dict[str, Any]:
    """Execute immutable gold fixtures against all declared fault mutations."""

    spec_path = spec_file.resolve()
    parent_path = parent_benchmark_file.resolve()
    destination = output_dir.resolve()
    spec = SyntheticFaultBenchmarkSpec.model_validate_json(
        spec_path.read_text(encoding="utf-8")
    )
    if file_sha256(parent_path) != spec.parent_benchmark_sha256:
        raise ValueError("Synthetic benchmark is not bound to this parent benchmark hash.")
    destination.mkdir(parents=True, exist_ok=True)
    fixture_dir = destination / "fixtures"
    case_dir = destination / "cases"
    fixture_dir.mkdir(parents=True, exist_ok=True)
    case_dir.mkdir(parents=True, exist_ok=True)

    fixture_cache: dict[tuple[str, str], CleanSyntheticFixture] = {}
    clean_fixture_statuses: dict[str, GateStatus] = {}
    global_blockers: list[str] = []
    results: list[SyntheticFaultCaseResult] = []
    artifact_paths: set[Path] = {spec_path, parent_path}

    for case in spec.cases:
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
            clean_fixture_statuses[clean_key] = fixture.status
            expected = FIXTURES[case.fixture_id].expected_clean_status
            if fixture.status is not expected:
                global_blockers.append(
                    f"clean_fixture_status_mismatch:{clean_key}:"
                    f"expected_{expected.value}:observed_{fixture.status.value}"
                )
            artifact_paths.update(fixture.artifact_paths)

        result, case_artifacts = _run_case(
            case=case,
            clean_fixture=fixture,
            case_dir=case_dir,
        )
        results.append(result)
        artifact_paths.update(case_artifacts)

    passed = sum(result.status is GateStatus.PASS for result in results)
    failed = len(results) - passed
    must_detect = [
        result for result in results if result.certification_expectation == "must-detect"
    ]
    must_abstain = [
        result for result in results if result.certification_expectation == "must-abstain"
    ]
    if failed:
        global_blockers.append(f"benchmark_case_failures:{failed}")
    report = SyntheticFaultBenchmarkReport(
        benchmark_id=spec.benchmark_id,
        benchmark_spec_sha256=file_sha256(spec_path),
        status=GateStatus.FAIL if global_blockers else GateStatus.PASS,
        total_case_count=len(results),
        passed_case_count=passed,
        failed_case_count=failed,
        must_detect_case_count=len(must_detect),
        must_detect_passed_count=sum(
            result.status is GateStatus.PASS for result in must_detect
        ),
        must_abstain_case_count=len(must_abstain),
        must_abstain_passed_count=sum(
            result.status is GateStatus.PASS for result in must_abstain
        ),
        clean_fixture_statuses=clean_fixture_statuses,
        cases=tuple(results),
        blockers=tuple(dict.fromkeys(global_blockers)),
    )
    report_path = destination / f"{prefix}.report.json"
    write_json_atomic(
        report_path,
        report.model_dump(mode="json", by_alias=True),
        sort_keys=True,
    )
    artifact_paths.add(report_path)
    manifest_path = destination / f"{prefix}.manifest.json"
    manifest = {
        "schema": "torii.corridor.synthetic-fault-benchmark-manifest/v1",
        "benchmark_id": spec.benchmark_id,
        "benchmark_spec": {
            "path": str(spec_path),
            "sha256": file_sha256(spec_path),
        },
        "parent_benchmark": {
            "path": str(parent_path),
            "sha256": file_sha256(parent_path),
        },
        "source_fixtures_immutable": all(
            result.source_immutable for result in results
        ),
        "status": report.status.value,
        "artifacts": [
            {
                "path": str(path),
                "sha256": file_sha256(path),
            }
            for path in sorted(artifact_paths, key=lambda item: item.as_posix())
        ],
    }
    write_json_atomic(manifest_path, manifest, sort_keys=True)
    return {
        **report.model_dump(mode="json", by_alias=True),
        "report_file": str(report_path),
        "manifest_file": str(manifest_path),
    }


def _run_case(
    *,
    case: Any,
    clean_fixture: CleanSyntheticFixture,
    case_dir: Path,
) -> tuple[SyntheticFaultCaseResult, set[Path]]:
    evidence = run_synthetic_mutation_sequence(
        case_id=case.case_id,
        mutation_ids=(case.mutation_id,),
        clean_fixture=clean_fixture,
        traffic_side=case.traffic_side,
        case_dir=case_dir,
    )
    matches = tuple(
        match_synthetic_observation(expectation, evidence.observed)
        for expectation in case.expected_observations
    )
    blockers: list[str] = []
    if not evidence.source_immutable:
        blockers.append("source_fixture_mutated")
    if evidence.source_sha256 == evidence.mutant_sha256:
        blockers.append("mutation_did_not_change_fixture")
    for match in matches:
        if not match.matched:
            blockers.append(
                f"gold_observation_missing:{match.channel}:{match.value}:"
                f"{match.observed_count}/{match.expected_minimum_count}"
            )
    if not evidence.abstention_proven:
        blockers.append("fault_not_blocked_or_exposed_as_exact_delta")
    status = GateStatus.FAIL if blockers else GateStatus.PASS
    result_path = case_dir / f"{case.case_id}.result.json"
    result = SyntheticFaultCaseResult(
        case_id=case.case_id,
        fault_family=case.fault_family,
        certification_expectation=case.certification_expectation,
        status=status,
        source_sha256=evidence.source_sha256,
        mutant_sha256=evidence.mutant_sha256,
        source_immutable=evidence.source_immutable,
        connection_status=str(evidence.candidate_audit.get("status", "unknown")),
        independent_safety_status=evidence.candidate_safety.status,
        exact_delta_count=evidence.exact_delta_count,
        observation_matches=matches,
        observed=evidence.observed,
        abstention_proven=evidence.abstention_proven,
        blockers=tuple(blockers),
        source_net_path=str(evidence.source_path),
        mutant_net_path=str(evidence.mutant_path),
    )
    write_json_atomic(
        result_path,
        result.model_dump(mode="json", by_alias=True),
        sort_keys=True,
    )
    return result, {*evidence.artifact_paths, result_path}
