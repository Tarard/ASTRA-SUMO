from __future__ import annotations

import argparse
import json
import xml.etree.ElementTree as ET
from pathlib import Path

import numpy as np

from torii_sumo.core.hamburg_sensor_twin import (
    build_path_time_assignment_evidence,
    solve_bounded_l1_path_time_profiles,
    solve_conserved_path_time_profiles,
    write_path_time_profiles,
)

SENSOR_EDGES = {
    "141050975": ("24483344#0", "603103445#0"),
    "186821027#0": ("186821027#0",),
    "186821036#1": ("186821034#0", "186821035#0"),
    "24483192": ("24483192",),
}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--demand", required=True, type=Path)
    parser.add_argument("--vehroute", required=True, type=Path)
    parser.add_argument("--edge-counts", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--damping", type=float, default=0.2)
    parser.add_argument("--prior-weight", type=float, default=0.5)
    parser.add_argument("--departure-bin-seconds", type=int, default=900)
    parser.add_argument("--departure-end", type=int, default=10800)
    parser.add_argument("--method", choices=("l2", "bounded-l1"), default="l2")
    parser.add_argument("--max-bin-change", type=int, default=10)
    parser.add_argument(
        "--depart-lane",
        choices=("best", "free", "preserve"),
        default="free",
    )
    args = parser.parse_args()

    output_dir = args.output_dir.resolve()
    if output_dir.exists():
        raise ValueError("output-dir must not already exist")
    output_dir.mkdir(parents=True)
    evidence = build_path_time_assignment_evidence(
        args.demand,
        args.vehroute,
        sensor_pass_edges=SENSOR_EDGES,
        target_begin=1800,
        target_end=10800,
        departure_begin=0,
        departure_end=args.departure_end,
        bin_seconds=900,
        departure_bin_seconds=args.departure_bin_seconds,
    )
    target = _target(args.edge_counts, evidence.sensor_order)
    if args.method == "bounded-l1":
        profiles = solve_bounded_l1_path_time_profiles(
            evidence.assignment,
            target,
            evidence.prior_counts,
            route_count=len(evidence.route_order),
            bins_per_route=evidence.bins_per_route,
            prior_weight=args.prior_weight,
            max_bin_change=args.max_bin_change,
        )
    else:
        profiles = solve_conserved_path_time_profiles(
            evidence.assignment,
            target,
            evidence.prior_counts,
            route_count=len(evidence.route_order),
            bins_per_route=evidence.bins_per_route,
            prior_weight=args.prior_weight,
            damping=args.damping,
        )
    demand_file = output_dir / "demand.rou.xml"
    write_path_time_profiles(
        args.demand,
        demand_file,
        route_order=evidence.route_order,
        profiles=profiles,
        departure_begin=0,
        bin_seconds=args.departure_bin_seconds,
        depart_lane=None if args.depart_lane == "preserve" else args.depart_lane,
    )
    predicted_before = evidence.assignment @ evidence.prior_counts
    predicted_after = evidence.assignment @ profiles
    report = {
        "status": "pass",
        "method": args.method,
        "damping": args.damping,
        "prior_weight": args.prior_weight,
        "route_count": len(evidence.route_order),
        "bins_per_route": evidence.bins_per_route,
        "departure_bin_seconds": args.departure_bin_seconds,
        "departure_end": args.departure_end,
        "max_bin_change": args.max_bin_change,
        "vehicle_count": int(profiles.sum()),
        "linearized_mae_before": float(np.mean(np.abs(predicted_before - target))),
        "linearized_mae_after": float(np.mean(np.abs(predicted_after - target))),
        "demand_file": str(demand_file),
    }
    (output_dir / "assignment-update.json").write_text(
        json.dumps(report, indent=2), encoding="utf-8"
    )
    return 0


def _target(path: Path, sensor_order: tuple[str, ...]) -> np.ndarray:
    intervals = ET.parse(path.resolve(strict=True)).getroot().findall("interval")
    values = np.zeros((len(sensor_order), len(intervals)), dtype=float)
    for bin_index, interval in enumerate(intervals):
        by_edge = {edge.attrib["id"]: int(edge.attrib["count"]) for edge in interval.findall("edge")}
        for sensor_index, sensor in enumerate(sensor_order):
            values[sensor_index, bin_index] = by_edge[sensor]
    return values.reshape(-1)


if __name__ == "__main__":
    raise SystemExit(main())
