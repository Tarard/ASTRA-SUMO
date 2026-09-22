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

from torii_sumo.corridor.synthetic_benchmark_runner import (
    run_synthetic_fault_benchmark,
)


DEFAULT_SPEC = (
    REPOSITORY_ROOT
    / "benchmarks"
    / "corridor_human_modeling_v1"
    / "synthetic_fault_matrix.v1.json"
)
DEFAULT_PARENT = (
    REPOSITORY_ROOT
    / "benchmarks"
    / "corridor_human_modeling_v1"
    / "benchmark.v1.json"
)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run the Torii Stage-1 synthetic Connection Mode fault matrix."
    )
    parser.add_argument("--spec", type=Path, default=DEFAULT_SPEC)
    parser.add_argument("--parent-benchmark", type=Path, default=DEFAULT_PARENT)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    report = run_synthetic_fault_benchmark(
        args.spec,
        parent_benchmark_file=args.parent_benchmark,
        output_dir=args.output_dir,
    )
    print(
        json.dumps(
            {
                "status": report["status"],
                "passed_case_count": report["passed_case_count"],
                "failed_case_count": report["failed_case_count"],
                "report_file": report["report_file"],
                "manifest_file": report["manifest_file"],
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
