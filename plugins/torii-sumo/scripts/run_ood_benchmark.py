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

from torii_sumo.corridor.ood_benchmark_runner import run_ood_benchmark


BENCHMARK_DIR = REPOSITORY_ROOT / "benchmarks" / "corridor_human_modeling_v1"


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run the Torii Stage-1 selective-domain OOD benchmark."
    )
    parser.add_argument(
        "--spec",
        type=Path,
        default=BENCHMARK_DIR / "ood_matrix.v1.json",
    )
    parser.add_argument(
        "--parent-benchmark",
        type=Path,
        default=BENCHMARK_DIR / "benchmark.v1.json",
    )
    parser.add_argument(
        "--certification-envelope",
        type=Path,
        default=BENCHMARK_DIR / "vehicle_x4_certification_envelope.v1.json",
    )
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    report = run_ood_benchmark(
        args.spec,
        parent_benchmark_file=args.parent_benchmark,
        certification_envelope_file=args.certification_envelope,
        output_dir=args.output_dir,
    )
    print(
        json.dumps(
            {
                "status": report["status"],
                "passed_case_count": report["passed_case_count"],
                "failed_case_count": report["failed_case_count"],
                "out_of_domain_recall": report["out_of_domain_recall"],
                "in_domain_acceptance_rate": report[
                    "in_domain_acceptance_rate"
                ],
                "report_file": report["report_file"],
                "manifest_file": report["manifest_file"],
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
