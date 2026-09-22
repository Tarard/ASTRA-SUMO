from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from torii_sumo.core.candidate_contracts import file_sha256
from torii_sumo.corridor.official_sumo_benchmark_contracts import (
    OfficialSumoBenchmarkSpec,
)
from torii_sumo.corridor.official_sumo_benchmark_runner import (
    run_official_sumo_benchmark,
)
from torii_sumo.corridor.schema import (
    build_official_sumo_benchmark_report_schema,
    build_official_sumo_benchmark_schema,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
BENCHMARK_DIR = REPOSITORY_ROOT / "benchmarks" / "corridor_human_modeling_v1"
SPEC_FILE = BENCHMARK_DIR / "official_sumo_scenarios.v1.json"
PARENT_FILE = BENCHMARK_DIR / "benchmark.v1.json"
TOOLCHAIN_FILE = BENCHMARK_DIR / "toolchain.lock.json"
SOURCE_ROOT = BENCHMARK_DIR / "official_sumo_v1_27_1"


def test_official_sumo_spec_binds_sources_toolchain_and_required_scenarios() -> None:
    spec = OfficialSumoBenchmarkSpec.model_validate_json(
        SPEC_FILE.read_text(encoding="utf-8")
    )

    assert spec.parent_benchmark_sha256 == file_sha256(PARENT_FILE)
    assert spec.toolchain_lock_sha256 == file_sha256(TOOLCHAIN_FILE)
    assert spec.upstream_tag == "v1_27_1"
    assert spec.upstream_commit == "7717f2379d9e314a0c81c5cec748444de06a2a91"
    assert spec.license_expression == "EPL-2.0 OR GPL-2.0-or-later"
    assert {case.case_id for case in spec.cases} == {
        "edge-to-edge-connections",
        "lane-to-lane-connections",
        "joined-junction",
        "no-internal-links",
        "nema-four-arm",
        "nema-four-arm-grouped",
        "pedestrian-crossing",
        "rail-crossing",
        "on-ramp",
    }
    assert all(case.expected_abstention for case in spec.cases)
    for source in spec.source_files:
        path = SOURCE_ROOT / source.vendored_path
        assert path.is_file()
        assert file_sha256(path) == source.vendored_sha256


def test_official_sumo_benchmark_schemas_are_current() -> None:
    schemas = {
        "torii.corridor.official-sumo-benchmark.v1.schema.json": (
            build_official_sumo_benchmark_schema()
        ),
        "torii.corridor.official-sumo-benchmark-report.v1.schema.json": (
            build_official_sumo_benchmark_report_schema()
        ),
    }
    for filename, schema in schemas.items():
        expected = json.dumps(schema, indent=2, ensure_ascii=False, sort_keys=True)
        assert (REPOSITORY_ROOT / "schemas" / filename).read_text(
            encoding="utf-8"
        ) == expected


def test_official_sumo_scenarios_regenerate_load_and_abstain(
    tmp_path: Path,
) -> None:
    netconvert, sumo = _locked_sumo_binaries_or_skip()

    report = run_official_sumo_benchmark(
        SPEC_FILE,
        parent_benchmark_file=PARENT_FILE,
        toolchain_lock_file=TOOLCHAIN_FILE,
        source_root=SOURCE_ROOT,
        output_dir=tmp_path / "official-sumo",
        netconvert_binary=netconvert,
        sumo_binary=sumo,
    )

    assert report["status"] == "pass"
    assert report["total_case_count"] == 9
    assert report["passed_case_count"] == 9
    assert report["failed_case_count"] == 0
    assert report["source_immutable"] is True
    assert all(case["reproducible_semantics"] for case in report["cases"])
    assert all(case["sumo_load_status"] == "pass" for case in report["cases"])
    assert all(case["abstention_proven"] for case in report["cases"])
    cases = {case["case_id"]: case for case in report["cases"]}
    assert cases["no-internal-links"]["connection_status"] == "pass"
    assert cases["no-internal-links"]["independent_safety_status"] == "review"
    assert cases["rail-crossing"]["connection_status"] == "pass"
    assert cases["pedestrian-crossing"]["independent_safety_status"] == "pass"
    assert cases["pedestrian-crossing"]["movement_count"] == 5
    assert cases["pedestrian-crossing"]["conflict_count"] == 4
    assert cases["pedestrian-crossing"]["abstention_proven"] is True
    manifest = json.loads(Path(report["manifest_file"]).read_text(encoding="utf-8"))
    assert manifest["status"] == "pass"
    assert manifest["source_immutable"] is True


def _locked_sumo_binaries_or_skip() -> tuple[str, str]:
    binaries: list[str] = []
    for name in ("netconvert", "sumo"):
        binary = shutil.which(name)
        if not binary:
            pytest.skip(f"{name} is not installed")
        completed = subprocess.run(
            [binary, "--version"],
            capture_output=True,
            text=True,
            check=False,
        )
        version_output = f"{completed.stdout}\n{completed.stderr}"
        match = re.search(r"\b(\d+\.\d+\.\d+)\b", version_output)
        if completed.returncode or not match or match.group(1) != "1.27.1":
            pytest.skip(f"{name} 1.27.1 is required for normative regeneration")
        binaries.append(binary)
    return binaries[0], binaries[1]
