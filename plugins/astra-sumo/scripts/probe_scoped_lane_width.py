"""Test one declared lane-width change with fixed outside geometry and ports."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from astra_sumo.core.network_rebuild_probe import run_scoped_lane_width_probe


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-net", type=Path, required=True)
    parser.add_argument("--lane-id", required=True)
    parser.add_argument("--width-m", type=float, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--netconvert-binary", default="netconvert")
    parser.add_argument("--sumo-binary", default="sumo")
    args = parser.parse_args()
    report = run_scoped_lane_width_probe(**vars(args))
    print(json.dumps({"status": report["status"], "width_applied": report.get("width_applied"),
        "boundary_failures_before": len(report.get("before_admission", {}).get("fixed_boundary_failures", [])),
        "boundary_failures_after": len(report.get("after_admission", {}).get("fixed_boundary_failures", [])),
        "outside_geometry": report.get("after_admission", {}).get("status"),
        "sumo_load": report.get("candidate_load", {}).get("status")}, indent=2))
    return 0 if report["status"] == "pass" else 3


if __name__ == "__main__":
    raise SystemExit(main())
