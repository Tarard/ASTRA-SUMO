from __future__ import annotations

import json
from pathlib import Path

from torii_sumo.core.candidate_contracts import file_sha256
from torii_sumo.corridor.benchmark import BenchmarkSpecV1
from torii_sumo.corridor.synthetic_benchmark_contracts import (
    SyntheticFaultBenchmarkSpec,
)
from torii_sumo.corridor.synthetic_benchmark_runner import (
    run_synthetic_fault_benchmark,
)
from torii_sumo.corridor.schema import (
    build_synthetic_fault_benchmark_report_schema,
    build_synthetic_fault_benchmark_schema,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
BENCHMARK_DIR = REPOSITORY_ROOT / "benchmarks" / "corridor_human_modeling_v1"
PARENT_SPEC = BENCHMARK_DIR / "benchmark.v1.json"
SYNTHETIC_SPEC = BENCHMARK_DIR / "synthetic_fault_matrix.v1.json"


def test_synthetic_matrix_covers_every_frozen_fault_family_and_both_traffic_sides() -> None:
    parent = BenchmarkSpecV1.model_validate_json(PARENT_SPEC.read_text(encoding="utf-8"))
    synthetic = SyntheticFaultBenchmarkSpec.model_validate_json(
        SYNTHETIC_SPEC.read_text(encoding="utf-8")
    )
    fault_dimension = next(
        dimension for dimension in parent.dimensions if dimension.name == "fault-family"
    )

    assert synthetic.parent_benchmark_sha256 == file_sha256(PARENT_SPEC)
    assert {case.fault_family for case in synthetic.cases} == set(
        fault_dimension.values
    )
    assert {case.traffic_side.value for case in synthetic.cases} == {"left", "right"}


def test_synthetic_matrix_detects_or_abstains_on_all_gold_mutations(
    tmp_path: Path,
) -> None:
    result = run_synthetic_fault_benchmark(
        SYNTHETIC_SPEC,
        parent_benchmark_file=PARENT_SPEC,
        output_dir=tmp_path / "benchmark-run",
    )

    assert result["status"] == "pass"
    assert result["total_case_count"] == 23
    assert result["passed_case_count"] == 23
    assert result["failed_case_count"] == 0
    assert result["must_detect_case_count"] == 16
    assert result["must_detect_passed_count"] == 16
    assert result["must_abstain_case_count"] == 7
    assert result["must_abstain_passed_count"] == 7
    assert result["clean_fixture_statuses"] == {
        "parallel-two-lane-x4:right": "pass",
        "pedestrian-x4:right": "pass",
        "rail-x4:right": "review",
        "standard-x4:left": "pass",
        "standard-x4:right": "pass",
    }
    cases = {case["fault_family"]: case for case in result["cases"]}
    independent = cases["protected-green-conflict"]
    assert independent["connection_status"] == "pass"
    assert independent["independent_safety_status"] == "blocked"
    assert independent["observed"]["independent-safety"][
        "protected_green_movement_conflict"
    ] >= 1
    assert all(case["source_immutable"] for case in result["cases"])
    assert all(case["source_sha256"] != case["mutant_sha256"] for case in result["cases"])
    manifest = json.loads(Path(result["manifest_file"]).read_text(encoding="utf-8"))
    assert manifest["source_fixtures_immutable"] is True
    assert manifest["status"] == "pass"
    assert manifest["benchmark_spec"]["sha256"] == file_sha256(SYNTHETIC_SPEC)


def test_synthetic_benchmark_schemas_are_current() -> None:
    schemas = {
        "torii.corridor.synthetic-fault-benchmark.v1.schema.json": (
            build_synthetic_fault_benchmark_schema()
        ),
        "torii.corridor.synthetic-fault-benchmark-report.v1.schema.json": (
            build_synthetic_fault_benchmark_report_schema()
        ),
    }
    for filename, schema in schemas.items():
        expected = json.dumps(schema, indent=2, ensure_ascii=False, sort_keys=True)
        assert (REPOSITORY_ROOT / "schemas" / filename).read_text(
            encoding="utf-8"
        ) == expected
