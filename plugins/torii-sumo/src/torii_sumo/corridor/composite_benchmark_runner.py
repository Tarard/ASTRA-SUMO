from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any

from torii_sumo.core.artifact_io import write_json_atomic
from torii_sumo.core.candidate_contracts import file_sha256

from .composite_benchmark_contracts import (
    CompositeFaultBenchmarkReport,
    CompositeFaultBenchmarkSpec,
    CompositeFaultCaseResult,
)
from .enums import GateStatus
from .synthetic_case_engine import (
    CleanSyntheticFixture,
    match_synthetic_observation,
    materialize_clean_synthetic_fixture,
    run_synthetic_mutation_sequence,
)
from .synthetic_benchmark_contracts import SyntheticFaultBenchmarkSpec
from .synthetic_networks import FIXTURES


def run_composite_fault_benchmark(
    spec_file: Path,
    *,
    parent_benchmark_file: Path,
    single_fault_benchmark_file: Path,
    output_dir: Path,
    prefix: str = "composite_fault_benchmark",
) -> dict[str, Any]:
    """Verify that compound failures do not mask their constituent evidence."""

    spec_path = spec_file.resolve()
    parent_path = parent_benchmark_file.resolve()
    single_fault_path = single_fault_benchmark_file.resolve()
    destination = output_dir.resolve()
    spec = CompositeFaultBenchmarkSpec.model_validate_json(
        spec_path.read_text(encoding="utf-8")
    )
    single_fault_spec = SyntheticFaultBenchmarkSpec.model_validate_json(
        single_fault_path.read_text(encoding="utf-8")
    )
    if file_sha256(parent_path) != spec.parent_benchmark_sha256:
        raise ValueError("Composite benchmark parent benchmark hash mismatch.")
    if file_sha256(single_fault_path) != spec.single_fault_benchmark_sha256:
        raise ValueError("Composite benchmark single-fault benchmark hash mismatch.")
    if single_fault_spec.parent_benchmark_sha256 != spec.parent_benchmark_sha256:
        raise ValueError("Composite and single-fault benchmarks have different parents.")
    _validate_component_lineage(spec, single_fault_spec)
    destination.mkdir(parents=True, exist_ok=True)
    fixture_dir = destination / "fixtures"
    case_dir = destination / "cases"
    fixture_cache: dict[tuple[str, str], CleanSyntheticFixture] = {}
    clean_statuses: dict[str, GateStatus] = {}
    results: list[CompositeFaultCaseResult] = []
    blockers: list[str] = []
    artifacts: set[Path] = {spec_path, parent_path, single_fault_path}
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
            clean_statuses[clean_key] = fixture.status
            expected = FIXTURES[case.fixture_id].expected_clean_status
            if fixture.status is not expected:
                blockers.append(
                    f"clean_fixture_status_mismatch:{clean_key}:"
                    f"expected_{expected.value}:observed_{fixture.status.value}"
                )
            artifacts.update(fixture.artifact_paths)
        evidence = run_synthetic_mutation_sequence(
            case_id=case.case_id,
            mutation_ids=tuple(
                component.mutation_id for component in case.components
            ),
            clean_fixture=fixture,
            traffic_side=case.traffic_side,
            case_dir=case_dir,
        )
        case_blockers: list[str] = []
        component_matches = {}
        component_coverage = {}
        total_observations = 0
        matched_observations = 0
        for component in case.components:
            matches = tuple(
                match_synthetic_observation(expectation, evidence.observed)
                for expectation in component.expected_observations
            )
            component_matches[component.fault_family] = matches
            covered = all(match.matched for match in matches)
            component_coverage[component.fault_family] = covered
            total_observations += len(matches)
            matched_observations += sum(match.matched for match in matches)
            for match in matches:
                if not match.matched:
                    case_blockers.append(
                        f"component_observation_missing:{component.fault_family}:"
                        f"{match.channel}:{match.value}:"
                        f"{match.observed_count}/{match.expected_minimum_count}"
                    )
        if not evidence.source_immutable:
            case_blockers.append("source_fixture_mutated")
        if evidence.source_sha256 == evidence.mutant_sha256:
            case_blockers.append("mutation_sequence_did_not_change_fixture")
        if case.expected_abstention and not evidence.abstention_proven:
            case_blockers.append("compound_fault_not_blocked_or_exposed")
        result = CompositeFaultCaseResult(
            case_id=case.case_id,
            interaction_class=case.interaction_class,
            component_fault_families=tuple(
                component.fault_family for component in case.components
            ),
            mutation_ids=tuple(component.mutation_id for component in case.components),
            status=GateStatus.FAIL if case_blockers else GateStatus.PASS,
            source_sha256=evidence.source_sha256,
            mutant_sha256=evidence.mutant_sha256,
            source_immutable=evidence.source_immutable,
            connection_status=str(evidence.candidate_audit.get("status", "unknown")),
            independent_safety_status=evidence.candidate_safety.status,
            exact_delta_count=evidence.exact_delta_count,
            component_observation_matches=component_matches,
            component_coverage=component_coverage,
            component_recall=round(
                sum(component_coverage.values()) / len(component_coverage),
                6,
            ),
            total_observation_count=total_observations,
            matched_observation_count=matched_observations,
            observation_recall=round(
                matched_observations / total_observations,
                6,
            ),
            observed=evidence.observed,
            abstention_proven=evidence.abstention_proven,
            blockers=tuple(case_blockers),
            source_net_path=str(evidence.source_path),
            mutant_net_path=str(evidence.mutant_path),
        )
        result_path = case_dir / f"{case.case_id}.result.json"
        write_json_atomic(
            result_path,
            result.model_dump(mode="json", by_alias=True),
            sort_keys=True,
        )
        results.append(result)
        artifacts.update(evidence.artifact_paths)
        artifacts.add(result_path)

    failed = sum(result.status is not GateStatus.PASS for result in results)
    if failed:
        blockers.append(f"composite_case_failures:{failed}")
    total_components = sum(len(result.component_coverage) for result in results)
    covered_components = sum(
        sum(result.component_coverage.values()) for result in results
    )
    total_observations = sum(result.total_observation_count for result in results)
    matched_observations = sum(
        result.matched_observation_count for result in results
    )
    report = CompositeFaultBenchmarkReport(
        benchmark_id=spec.benchmark_id,
        benchmark_spec_sha256=file_sha256(spec_path),
        status=GateStatus.FAIL if blockers else GateStatus.PASS,
        total_case_count=len(results),
        passed_case_count=len(results) - failed,
        failed_case_count=failed,
        total_component_count=total_components,
        covered_component_count=covered_components,
        component_recall=round(
            covered_components / total_components,
            6,
        ),
        total_observation_count=total_observations,
        matched_observation_count=matched_observations,
        observation_recall=round(
            matched_observations / total_observations,
            6,
        ),
        interaction_class_case_counts=dict(
            sorted(Counter(result.interaction_class for result in results).items())
        ),
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
            "schema": "torii.corridor.composite-fault-benchmark-manifest/v1",
            "benchmark_id": spec.benchmark_id,
            "benchmark_spec": {
                "path": str(spec_path),
                "sha256": file_sha256(spec_path),
            },
            "parent_benchmark": {
                "path": str(parent_path),
                "sha256": file_sha256(parent_path),
            },
            "single_fault_benchmark": {
                "path": str(single_fault_path),
                "sha256": file_sha256(single_fault_path),
            },
            "status": report.status.value,
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


def _validate_component_lineage(
    composite_spec: CompositeFaultBenchmarkSpec,
    single_fault_spec: SyntheticFaultBenchmarkSpec,
) -> None:
    single_by_family = {
        case.fault_family: case for case in single_fault_spec.cases
    }
    for case in composite_spec.cases:
        if case.fixture_id not in FIXTURES:
            raise ValueError(
                f"Unknown composite fixture {case.fixture_id!r} in {case.case_id!r}."
            )
        for component in case.components:
            single = single_by_family.get(component.fault_family)
            if single is None:
                raise ValueError(
                    f"Composite component {component.fault_family!r} is not frozen "
                    "in the single-fault benchmark."
                )
            if component.mutation_id != single.mutation_id:
                raise ValueError(
                    f"Composite component {component.fault_family!r} changed its "
                    "single-fault mutation identity."
                )
            single_witnesses = {
                (observation.channel, observation.value, observation.minimum_count)
                for observation in single.expected_observations
            }
            for observation in component.expected_observations:
                witness = (
                    observation.channel,
                    observation.value,
                    observation.minimum_count,
                )
                if witness not in single_witnesses:
                    raise ValueError(
                        f"Composite component {component.fault_family!r} contains "
                        "a witness not preregistered by its single-fault case."
                    )
