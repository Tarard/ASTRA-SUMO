from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = PLUGIN_ROOT.parents[1]
SRC_ROOT = PLUGIN_ROOT / "src"

if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from torii_sumo.corridor.composite_benchmark_runner import (
    run_composite_fault_benchmark,
)


BENCHMARK_DIR = REPOSITORY_ROOT / "benchmarks" / "corridor_human_modeling_v1"
DEFAULT_SPEC = BENCHMARK_DIR / "composite_fault_matrix.v1.json"
DEFAULT_PARENT = BENCHMARK_DIR / "benchmark.v1.json"
DEFAULT_SINGLE_FAULT = BENCHMARK_DIR / "synthetic_fault_matrix.v1.json"


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run the Torii Stage-1 compound-fault interaction benchmark."
    )
    parser.add_argument("--spec", type=Path, default=DEFAULT_SPEC)
    parser.add_argument("--parent-benchmark", type=Path, default=DEFAULT_PARENT)
    parser.add_argument(
        "--single-fault-benchmark",
        type=Path,
        default=DEFAULT_SINGLE_FAULT,
    )
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    report = run_composite_fault_benchmark(
        args.spec,
        parent_benchmark_file=args.parent_benchmark,
        single_fault_benchmark_file=args.single_fault_benchmark,
        output_dir=args.output_dir,
    )
    print(
        json.dumps(
            {
                "status": report["status"],
                "passed_case_count": report["passed_case_count"],
                "failed_case_count": report["failed_case_count"],
                "covered_component_count": report["covered_component_count"],
                "total_component_count": report["total_component_count"],
                "report_file": report["report_file"],
                "manifest_file": report["manifest_file"],
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
