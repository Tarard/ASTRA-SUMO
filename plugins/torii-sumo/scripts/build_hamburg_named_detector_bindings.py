from __future__ import annotations

import argparse
import json
from pathlib import Path

from torii_sumo.core.hamburg_named_detector_bindings import (
    DEFAULT_NETWORK_PROJECTION,
    HamburgDetectorBindingError,
    materialize_hamburg_named_detector_bindings,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Bind Hamburg official detector points to a named SUMO corridor, "
            "rejecting distance and parallel-lane ambiguity automatically."
        )
    )
    parser.add_argument("--net-file", required=True, type=Path)
    parser.add_argument("--count-stream-file", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--network-projection", default=DEFAULT_NETWORK_PROJECTION)
    parser.add_argument("--period", type=int, default=900)
    parser.add_argument("--max-distance-m", type=float, default=5.0)
    parser.add_argument("--ambiguity-margin-m", type=float, default=1.0)
    parser.add_argument("--map-file", action="append", default=[], type=Path)
    parser.add_argument("--movement-evidence-file", type=Path)
    args = parser.parse_args()

    try:
        report = materialize_hamburg_named_detector_bindings(
            net_file=args.net_file,
            count_stream_file=args.count_stream_file,
            output_dir=args.output_dir,
            network_projection=args.network_projection,
            period=args.period,
            max_distance_m=args.max_distance_m,
            ambiguity_margin_m=args.ambiguity_margin_m,
            map_files=tuple(args.map_file),
            movement_evidence_file=args.movement_evidence_file,
        )
    except (OSError, ValueError, HamburgDetectorBindingError) as exc:
        print(json.dumps({"status": "error", "automatic_promotion_gate": "blocked", "error": str(exc)}))
        return 2

    print(json.dumps(report, indent=2, sort_keys=True, default=str))
    return 0 if report.get("automatic_promotion_gate") == "pass" else 3


if __name__ == "__main__":
    raise SystemExit(main())
