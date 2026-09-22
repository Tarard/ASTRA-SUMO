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

from torii_sumo.corridor.held_out_corpus_runner import (
    build_held_out_osm_snapshots,
)


BENCHMARK_DIR = REPOSITORY_ROOT / "benchmarks" / "corridor_human_modeling_v1"


def main() -> None:
    parser = argparse.ArgumentParser(
        description=("Build hash-pinned, reference-complete OSM snapshots for the Torii real-corridor held-out corpus.")
    )
    parser.add_argument(
        "--spec",
        type=Path,
        default=BENCHMARK_DIR / "held_out_corpus.v1.json",
    )
    parser.add_argument(
        "--policy",
        type=Path,
        default=BENCHMARK_DIR / "held_out_review_preregistration.v1.json",
    )
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument(
        "--city-extract-cache-dir",
        type=Path,
        help=(
            "Reuse provider-verified frozen city PBFs from this directory while "
            "writing new corridor snapshots to --output-dir."
        ),
    )
    parser.add_argument(
        "--only-city",
        action="append",
        default=[],
        help="Build one city group; repeat for more. A partial run stays blocked.",
    )
    parser.add_argument("--timeout-seconds", type=float, default=900.0)
    args = parser.parse_args()
    report = build_held_out_osm_snapshots(
        args.spec,
        held_out_review_policy_file=args.policy,
        output_dir=args.output_dir,
        city_extract_cache_dir=args.city_extract_cache_dir,
        only_city_groups=tuple(args.only_city),
        timeout_seconds=args.timeout_seconds,
    )
    print(
        json.dumps(
            {
                "status": report["status"],
                "city_extract_count": len(report["city_extracts"]),
                "corridor_count": len(report["corridors"]),
                "review_corridor_count": sum(item["status"] == "review" for item in report["corridors"]),
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
