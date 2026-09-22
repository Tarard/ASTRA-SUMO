from __future__ import annotations

import argparse
import json
from pathlib import Path

from torii_sumo.core.cached_detector_demand import prepare_cached_detector_demand_package


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Build deterministic SUMO detector and routeSampler inputs from a frozen network, "
            "cached Hamburg counts, MAP files, and an audited MAP-to-SUMO lane contract."
        )
    )
    parser.add_argument("--official-tls-manifest", required=True, type=Path)
    parser.add_argument("--count-stream-snapshot", required=True, type=Path)
    parser.add_argument("--canonical-count-file", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--prefix", default="cached_detector_demand")
    parser.add_argument("--simulation-begin", type=int, default=0)
    parser.add_argument("--simulation-end", type=int, default=9000)
    parser.add_argument("--comparison-begin", type=int, default=1800)
    parser.add_argument("--comparison-end", type=int, default=9000)
    parser.add_argument("--interval", type=int, default=900)
    parser.add_argument("--exclude-route-edge", action="append", default=[])
    parser.add_argument("--route-sampler-optimize", default="full")
    parser.add_argument("--route-sampler-script", type=Path)
    parser.add_argument("--timeout-seconds", type=float, default=300.0)
    parser.add_argument(
        "--allow-detector-cross-section-boundaries",
        action="store_true",
        help=(
            "Use official detector edges as explicit open source/sink ports; this reproduces "
            "the measured local cut but does not claim upstream OD demand."
        ),
    )
    args = parser.parse_args()

    report = prepare_cached_detector_demand_package(
        official_tls_manifest=args.official_tls_manifest,
        count_stream_snapshot=args.count_stream_snapshot,
        canonical_count_file=args.canonical_count_file,
        output_dir=args.output_dir,
        prefix=args.prefix,
        simulation_begin=args.simulation_begin,
        simulation_end=args.simulation_end,
        comparison_begin=args.comparison_begin,
        comparison_end=args.comparison_end,
        interval=args.interval,
        excluded_route_edges=args.exclude_route_edge,
        route_sampler_optimize=args.route_sampler_optimize,
        route_sampler_script=args.route_sampler_script,
        timeout_seconds=args.timeout_seconds,
        allow_detector_cross_section_boundaries=args.allow_detector_cross_section_boundaries,
    )
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report.get("demand_generation_status") == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
