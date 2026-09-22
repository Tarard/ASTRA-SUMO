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

from torii_sumo.corridor.held_out_review_runner import (
    evaluate_held_out_review_trial,
)


BENCHMARK_DIR = REPOSITORY_ROOT / "benchmarks" / "corridor_human_modeling_v1"


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Evaluate a hash-bound Torii held-out blind-review trial."
    )
    parser.add_argument(
        "--policy",
        type=Path,
        default=BENCHMARK_DIR / "held_out_review_preregistration.v1.json",
    )
    parser.add_argument(
        "--parent-benchmark",
        type=Path,
        default=BENCHMARK_DIR / "benchmark.v1.json",
    )
    parser.add_argument("--blinded-dataset", type=Path, required=True)
    parser.add_argument("--evaluation-key", type=Path, required=True)
    parser.add_argument("--decision", type=Path, action="append", default=[])
    parser.add_argument("--adjudication", type=Path, action="append", default=[])
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    report = evaluate_held_out_review_trial(
        policy_file=args.policy,
        parent_benchmark_file=args.parent_benchmark,
        blinded_dataset_file=args.blinded_dataset,
        evaluation_key_file=args.evaluation_key,
        decision_files=args.decision,
        adjudication_files=args.adjudication,
        output_dir=args.output_dir,
    )
    print(
        json.dumps(
            {
                "status": report["status"],
                "automatic_promotion_gate": report["automatic_promotion_gate"],
                "case_count": report["metrics"]["case_count"],
                "blockers": report["blockers"],
                "report_file": report["report_file"],
                "manifest_file": report["manifest_file"],
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
