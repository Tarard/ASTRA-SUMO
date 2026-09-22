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

from torii_sumo.corridor.official_sumo_benchmark_runner import (
    run_official_sumo_benchmark,
)


BENCHMARK_DIR = REPOSITORY_ROOT / "benchmarks" / "corridor_human_modeling_v1"
DEFAULT_SPEC = BENCHMARK_DIR / "official_sumo_scenarios.v1.json"
DEFAULT_PARENT = BENCHMARK_DIR / "benchmark.v1.json"
DEFAULT_TOOLCHAIN = BENCHMARK_DIR / "toolchain.lock.json"
DEFAULT_SOURCE_ROOT = BENCHMARK_DIR / "official_sumo_v1_27_1"


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Regenerate and audit Torii's frozen SUMO 1.27.1 scenarios."
    )
    parser.add_argument("--spec", type=Path, default=DEFAULT_SPEC)
    parser.add_argument("--parent-benchmark", type=Path, default=DEFAULT_PARENT)
    parser.add_argument("--toolchain-lock", type=Path, default=DEFAULT_TOOLCHAIN)
    parser.add_argument("--source-root", type=Path, default=DEFAULT_SOURCE_ROOT)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--netconvert-binary", default="netconvert")
    parser.add_argument("--sumo-binary", default="sumo")
    args = parser.parse_args()
    report = run_official_sumo_benchmark(
        args.spec,
        parent_benchmark_file=args.parent_benchmark,
        toolchain_lock_file=args.toolchain_lock,
        source_root=args.source_root,
        output_dir=args.output_dir,
        netconvert_binary=args.netconvert_binary,
        sumo_binary=args.sumo_binary,
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
