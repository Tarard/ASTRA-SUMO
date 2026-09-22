from __future__ import annotations

import argparse
import sys
from pathlib import Path


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = PLUGIN_ROOT.parents[1]
SRC_ROOT = PLUGIN_ROOT / "src"

if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from torii_sumo.corridor.pedestrian_row_experiment import (  # noqa: E402
    run_row1_experiment,
)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run the fail-closed ROW-1 pedestrian right-of-way experiment."
    )
    parser.add_argument(
        "--fixture-dir",
        type=Path,
        default=(
            REPOSITORY_ROOT
            / "benchmarks"
            / "corridor_human_modeling_v1"
            / "row_1"
        ),
    )
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--netconvert-binary", type=Path, required=True)
    parser.add_argument("--sumo-binary", type=Path, required=True)
    args = parser.parse_args()
    report = run_row1_experiment(
        fixture_dir=args.fixture_dir,
        output_dir=args.output_dir,
        netconvert_binary=args.netconvert_binary,
        sumo_binary=args.sumo_binary,
    )
    print(
        "ROW-1 "
        f"status={report.status.value} "
        f"cases={len(report.cases)} "
        f"failed={report.failed_case_count} "
        f"promotion={report.automatic_promotion_gate.value}"
    )
    if report.status.value != "pass":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
