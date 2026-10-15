"""Measure a frozen Hamburg network against freshly acquired official sources."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from astra_sumo.core.hamburg_network_calibration import evaluate_hamburg_calibration_snapshot


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--network", required=True, type=Path)
    parser.add_argument("--official-sources", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--target-date", required=True)
    parser.add_argument("--sample-spacing-m", required=True, type=float)
    parser.add_argument("--mode", choices=("explore", "release"), default="explore")
    parser.add_argument("--tolerance-m", type=float)
    args = parser.parse_args()
    summary = evaluate_hamburg_calibration_snapshot(network=args.network, official_sources=args.official_sources,
        output_dir=args.output_dir, target_date=args.target_date, sample_spacing_m=args.sample_spacing_m,
        mode=args.mode, tolerance_m=args.tolerance_m)
    print(json.dumps(summary, indent=2))
    return 0 if summary["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
