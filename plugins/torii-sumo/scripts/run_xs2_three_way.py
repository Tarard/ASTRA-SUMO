from __future__ import annotations

import argparse
import json
from pathlib import Path

from torii_sumo.core.xs1_workflow import run_isolated_junction_workflow


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the frozen XS-2 three-way TLS workflow."
    )
    parser.add_argument(
        "--example-dir",
        type=Path,
        default=REPOSITORY_ROOT / "examples" / "04_xs2_three_way_tls",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=REPOSITORY_ROOT / "outputs" / "xs2-three-way",
    )
    parser.add_argument("--timeout-seconds", type=float, default=120.0)
    return parser.parse_args()


def main() -> int:
    args = _args()
    result = run_isolated_junction_workflow(
        example_dir=args.example_dir,
        output_dir=args.output_dir,
        toolchain_lock_file=(
            REPOSITORY_ROOT
            / "benchmarks"
            / "corridor_human_modeling_v1"
            / "toolchain.lock.json"
        ),
        timeout_seconds=args.timeout_seconds,
    )
    concise = {
        "status": result["status"],
        "automatic_promotion_gate": result["automatic_promotion_gate"],
        "gates": result.get("gates", {}),
        "summary_file": result.get("summary_file"),
        "review_html_file": result.get("review_html_file"),
        "manifest_file": result.get("manifest_file"),
    }
    print(json.dumps(concise, indent=2))
    return 0 if str(result["status"]).startswith("review_ready") else 1


if __name__ == "__main__":
    raise SystemExit(main())
