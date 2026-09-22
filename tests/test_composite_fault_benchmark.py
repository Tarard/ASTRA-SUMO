from __future__ import annotations

import json
from pathlib import Path

from torii_sumo.core.candidate_contracts import file_sha256
from torii_sumo.corridor.composite_benchmark_contracts import (
    CompositeFaultBenchmarkSpec,
    CompositeFaultCase,
)
from torii_sumo.corridor.composite_benchmark_runner import (
    run_composite_fault_benchmark,
)
from torii_sumo.corridor.ids import stable_id
from torii_sumo.corridor.schema import (
    build_composite_fault_benchmark_report_schema,
    build_composite_fault_benchmark_schema,
)
from torii_sumo.corridor.synthetic_benchmark_contracts import (
    SyntheticFaultBenchmarkSpec,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
BENCHMARK_DIR = REPOSITORY_ROOT / "benchmarks" / "corridor_human_modeling_v1"
PARENT_SPEC = BENCHMARK_DIR / "benchmark.v1.json"
SINGLE_FAULT_SPEC = BENCHMARK_DIR / "synthetic_fault_matrix.v1.json"
COMPOSITE_SPEC = BENCHMARK_DIR / "composite_fault_matrix.v1.json"


def test_composite_matrix_is_hash_bound_to_every_single_fault_component() -> None:
    single = SyntheticFaultBenchmarkSpec.model_validate_json(
        SINGLE_FAULT_SPEC.read_text(encoding="utf-8")
    )
    composite = CompositeFaultBenchmarkSpec.model_validate_json(
        COMPOSITE_SPEC.read_text(encoding="utf-8")
    )
    single_by_family = {case.fault_family: case for case in single.cases}

    assert composite.parent_benchmark_sha256 == file_sha256(PARENT_SPEC)
    assert composite.single_fault_benchmark_sha256 == file_sha256(SINGLE_FAULT_SPEC)
    assert len(composite.cases) == 10
    assert sum(len(case.components) for case in composite.cases) == 20
    assert {case.fixture_id for case in composite.cases} >= {
        "standard-x4",
        "parallel-two-lane-x4",
        "pedestrian-x4",
        "rail-x4",
    }
    for case in composite.cases:
        for component in case.components:
            gold = single_by_family[component.fault_family]
            assert component.mutation_id == gold.mutation_id
            assert set(component.expected_observations) <= set(
                gold.expected_observations
            )


def test_composite_matrix_exposes_every_component_without_source_mutation(
    tmp_path: Path,
) -> None:
    result = run_composite_fault_benchmark(
        COMPOSITE_SPEC,
        parent_benchmark_file=PARENT_SPEC,
        single_fault_benchmark_file=SINGLE_FAULT_SPEC,
        output_dir=tmp_path / "composite-run",
    )

    assert result["status"] == "pass"
    assert result["total_case_count"] == 10
    assert result["passed_case_count"] == 10
    assert result["failed_case_count"] == 0
    assert result["total_component_count"] == 20
    assert result["covered_component_count"] == 20
    assert result["component_recall"] == 1.0
    assert result["total_observation_count"] == 21
    assert result["matched_observation_count"] == 21
    assert result["observation_recall"] == 1.0
    assert all(case["source_immutable"] for case in result["cases"])
    assert all(case["abstention_proven"] for case in result["cases"])
    cases = {case["case_id"]: case for case in result["cases"]}
    assert cases["x4-geometry-plus-protected-conflict"]["component_coverage"] == {
        "endpoint-gap": True,
        "protected-green-conflict": True,
    }
    assert cases["x4-lane-map-plus-shared-group"]["component_coverage"] == {
        "crossed-lane-map": True,
        "shared-link-index-conflict": True,
    }
    manifest = json.loads(Path(result["manifest_file"]).read_text(encoding="utf-8"))
    assert manifest["status"] == "pass"
    assert manifest["source_fixtures_immutable"] is True
    assert manifest["benchmark_spec"]["sha256"] == file_sha256(COMPOSITE_SPEC)
    assert manifest["single_fault_benchmark"]["sha256"] == file_sha256(
        SINGLE_FAULT_SPEC
    )


def test_composite_matrix_fails_when_one_fault_masks_another(
    tmp_path: Path,
) -> None:
    masking_case = CompositeFaultCase.model_validate(
        {
            "case_id": "rail-conflict-masked-by-state-shape",
            "fixture_id": "rail-x4",
            "traffic_side": "right",
            "interaction_class": "known-masking-negative-control",
            "components": [
                {
                    "fault_family": "rail-conflict",
                    "mutation_id": "rail-conflict",
                    "expected_observations": [
                        {
                            "channel": "independent-safety",
                            "value": "protected_green_movement_conflict",
                        },
                        {
                            "channel": "independent-review",
                            "value": "movement_modes_outside_certified_conflict_applicability",
                        },
                    ],
                },
                {
                    "fault_family": "phase-state-length",
                    "mutation_id": "phase-state-length",
                    "expected_observations": [
                        {
                            "channel": "connection-structural",
                            "value": "controller_program_state_lengths_inconsistent",
                        }
                    ],
                },
            ],
            "expected_abstention": True,
        }
    )
    payload = {
        "parent_benchmark_sha256": file_sha256(PARENT_SPEC),
        "single_fault_benchmark_sha256": file_sha256(SINGLE_FAULT_SPEC),
        "cases": [masking_case.model_dump(mode="json", by_alias=True)],
    }
    spec = CompositeFaultBenchmarkSpec(
        benchmark_id=stable_id("manifest", payload),
        parent_benchmark_sha256=file_sha256(PARENT_SPEC),
        single_fault_benchmark_sha256=file_sha256(SINGLE_FAULT_SPEC),
        cases=(masking_case,),
    )
    spec_path = tmp_path / "masking-negative-control.json"
    spec_path.write_text(
        json.dumps(
            spec.model_dump(mode="json", by_alias=True),
            indent=2,
            ensure_ascii=False,
            sort_keys=True,
        ),
        encoding="utf-8",
    )

    result = run_composite_fault_benchmark(
        spec_path,
        parent_benchmark_file=PARENT_SPEC,
        single_fault_benchmark_file=SINGLE_FAULT_SPEC,
        output_dir=tmp_path / "negative-control-run",
    )

    assert result["status"] == "fail"
    assert result["failed_case_count"] == 1
    case = result["cases"][0]
    assert case["component_coverage"] == {
        "phase-state-length": True,
        "rail-conflict": False,
    }
    assert any(
        blocker.startswith(
            "component_observation_missing:rail-conflict:independent-safety:"
        )
        for blocker in case["blockers"]
    )


def test_composite_benchmark_schemas_are_current() -> None:
    schemas = {
        "torii.corridor.composite-fault-benchmark.v1.schema.json": (
            build_composite_fault_benchmark_schema()
        ),
        "torii.corridor.composite-fault-benchmark-report.v1.schema.json": (
            build_composite_fault_benchmark_report_schema()
        ),
    }
    for filename, schema in schemas.items():
        expected = json.dumps(schema, indent=2, ensure_ascii=False, sort_keys=True)
        assert (REPOSITORY_ROOT / "schemas" / filename).read_text(
            encoding="utf-8"
        ) == expected
