from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
PLUGIN_SRC = REPOSITORY_ROOT / "plugins" / "torii-sumo" / "src"
if str(PLUGIN_SRC) not in sys.path:
    sys.path.insert(0, str(PLUGIN_SRC))

from torii_sumo.core.teacher_free_topology_workflow import (  # noqa: E402
    run_teacher_free_topology_workflow,
)


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Discover one physical-cell hypothesis from a frozen OSM bbox and "
            "compare preregistered split, merge, and partial-repair variants."
        )
    )
    parser.add_argument("--osm", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument(
        "--traffic-side",
        choices=("right", "left", "unknown"),
        default="right",
    )
    parser.add_argument(
        "--toolchain-lock",
        type=Path,
        default=REPOSITORY_ROOT
        / "benchmarks"
        / "corridor_human_modeling_v1"
        / "toolchain.lock.json",
    )
    parser.add_argument("--timeout-seconds", type=float, default=180.0)
    return parser.parse_args()


def main() -> int:
    args = _args()
    report = run_teacher_free_topology_workflow(
        osm_file=args.osm,
        output_dir=args.output_dir,
        traffic_side=args.traffic_side,
        toolchain_lock_file=args.toolchain_lock,
        timeout_seconds=args.timeout_seconds,
    )
    print(json.dumps(report, indent=2))
    return 0 if report["status"] in {"review_ready", "not_applicable"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
