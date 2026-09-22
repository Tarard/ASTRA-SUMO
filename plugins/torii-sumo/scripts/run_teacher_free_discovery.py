from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
PLUGIN_SRC = REPOSITORY_ROOT / "plugins" / "torii-sumo" / "src"
if str(PLUGIN_SRC) not in sys.path:
    sys.path.insert(0, str(PLUGIN_SRC))

from torii_sumo.core.teacher_free_discovery_workflow import (  # noqa: E402
    run_teacher_free_discovery_workflow,
)


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Discover and canonicalize OSM signal cells without a teacher, "
            "reviewed scope, expected topology, or expected movement count."
        )
    )
    parser.add_argument("--osm", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument(
        "--traffic-side",
        choices=("right", "left", "unknown"),
        default="right",
    )
    return parser.parse_args()


def main() -> int:
    args = _args()
    report = run_teacher_free_discovery_workflow(
        osm_file=args.osm,
        output_dir=args.output_dir,
        traffic_side=args.traffic_side,
    )
    print(json.dumps(report, indent=2))
    return 0 if report["status"] in {"review_ready", "no_candidates"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
