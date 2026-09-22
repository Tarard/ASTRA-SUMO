from __future__ import annotations

import argparse
import json
import xml.etree.ElementTree as ET
from pathlib import Path

import numpy as np

from torii_sumo.core.hamburg_sensor_twin import solve_vehicle_shift_selection

SENSORS = ("141050975", "186821027#0", "186821036#1", "24483192")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--demand", required=True, type=Path)
    parser.add_argument("--vehroute", required=True, type=Path)
    parser.add_argument("--e1", required=True, type=Path)
    parser.add_argument("--detector-bank", required=True, type=Path)
    parser.add_argument("--edge-counts", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--max-changes", type=int, default=100)
    parser.add_argument("--max-shift", type=int, default=300)
    parser.add_argument("--route-start-edge", action="append", default=[])
    args = parser.parse_args()

    output_dir = args.output_dir.resolve()
    if output_dir.exists():
        raise ValueError("output-dir must not already exist")
    output_dir.mkdir(parents=True)
    residual = _residual(args.e1, args.detector_bank, args.edge_counts)
    tree = ET.parse(args.demand.resolve(strict=True))
    demand_root = tree.getroot()
    demand = {vehicle.attrib["id"]: vehicle for vehicle in demand_root.findall("vehicle")}
    route_start_edges = set(args.route_start_edge)
    candidate_ids = []
    candidate_shifts = []
    candidate_effects = []
    for vehicle in ET.parse(args.vehroute.resolve(strict=True)).getroot().findall("vehicle"):
        route = vehicle.find("route")
        if route is None or vehicle.attrib["id"] not in demand:
            continue
        edges = tuple(route.attrib["edges"].split())
        if route_start_edges and edges[0] not in route_start_edges:
            continue
        times = [float(value) for value in route.attrib["exitTimes"].split()]
        passages = _passages(edges, times)
        planned_depart = float(demand[vehicle.attrib["id"]].attrib["depart"])
        for shift in (-600, -300, -120, -60, 60, 120, 300, 600):
            if abs(shift) > args.max_shift:
                continue
            if planned_depart + shift < 0:
                continue
            effect = np.zeros(40, dtype=np.int64)
            for sensor_index, sensor in enumerate(SENSORS):
                passage = passages.get(sensor)
                if passage is None:
                    continue
                old_bin = int((passage - 1800) // 900)
                new_bin = int((passage + shift - 1800) // 900)
                if old_bin == new_bin:
                    continue
                if 0 <= old_bin < 10:
                    effect[sensor_index * 10 + old_bin] -= 1
                if 0 <= new_bin < 10:
                    effect[sensor_index * 10 + new_bin] += 1
            if not np.any(effect):
                continue
            changed_rows = np.flatnonzero(effect)
            gain = np.abs(residual[changed_rows]).sum() - np.abs(
                residual[changed_rows] + effect[changed_rows]
            ).sum()
            if gain <= 0:
                continue
            candidate_ids.append(vehicle.attrib["id"])
            candidate_shifts.append(float(shift))
            candidate_effects.append(effect)
    effects = (
        np.column_stack(candidate_effects)
        if candidate_effects
        else np.zeros((40, 0), dtype=np.int64)
    )
    selected = solve_vehicle_shift_selection(
        residual,
        effects,
        vehicle_ids=candidate_ids,
        shift_costs=np.abs(candidate_shifts),
        max_changes=args.max_changes,
    )
    total_effect = np.zeros(40, dtype=np.int64)
    applied = []
    for candidate_index in selected:
        vehicle_id = candidate_ids[int(candidate_index)]
        shift = candidate_shifts[int(candidate_index)]
        vehicle = demand[vehicle_id]
        vehicle.attrib["depart"] = f"{float(vehicle.attrib['depart']) + shift:.2f}"
        total_effect += effects[:, int(candidate_index)]
        applied.append({"vehicle_id": vehicle_id, "shift_seconds": shift})
    ordered = sorted(
        demand_root.findall("vehicle"),
        key=lambda vehicle: (float(vehicle.attrib["depart"]), vehicle.attrib["id"]),
    )
    for vehicle in list(demand_root.findall("vehicle")):
        demand_root.remove(vehicle)
    demand_root.extend(ordered)
    ET.indent(tree, space="    ")
    demand_file = output_dir / "demand.rou.xml"
    tree.write(demand_file, encoding="utf-8", xml_declaration=True)
    report = {
        "status": "pass",
        "candidate_count": len(candidate_ids),
        "selected_vehicle_count": len(selected),
        "max_changes": args.max_changes,
        "linearized_total_absolute_error_before": int(np.abs(residual).sum()),
        "linearized_total_absolute_error_after": int(np.abs(residual + total_effect).sum()),
        "applied": applied,
        "demand_file": str(demand_file),
    }
    (output_dir / "vehicle-shift-update.json").write_text(
        json.dumps(report, indent=2), encoding="utf-8"
    )
    return 0


def _residual(e1_path: Path, bank_path: Path, target_path: Path) -> np.ndarray:
    bank = json.loads(bank_path.resolve(strict=True).read_text(encoding="utf-8"))
    detectors = {int(row["station_stream_id"]): row["detector_ids"] for row in bank}
    e1_root = ET.parse(e1_path.resolve(strict=True)).getroot()
    actual = {
        (row.attrib["id"], int(float(row.attrib["begin"])), int(float(row.attrib["end"]))): int(
            row.attrib["nVehContrib"]
        )
        for row in e1_root.findall("interval")
    }
    target_root = ET.parse(target_path.resolve(strict=True)).getroot()
    residual = np.zeros(40, dtype=np.int64)
    for bin_index, interval in enumerate(target_root.findall("interval")):
        by_edge = {edge.attrib["id"]: edge for edge in interval.findall("edge")}
        for sensor_index, sensor in enumerate(SENSORS):
            edge = by_edge[sensor]
            station_id = int(edge.attrib["detector_ids"].removeprefix("station_"))
            begin = int(float(interval.attrib["begin"])) + 1800
            end = int(float(interval.attrib["end"])) + 1800
            measured = sum(actual[(detector_id, begin, end)] for detector_id in detectors[station_id])
            residual[sensor_index * 10 + bin_index] = measured - int(edge.attrib["count"])
    return residual


def _passages(edges: tuple[str, ...], times: list[float]) -> dict[str, float]:
    result = {}
    for sensor in SENSORS:
        if sensor == "186821036#1":
            edge = next((value for value in ("186821034#0", "186821035#0") if value in edges), None)
            if edge is not None:
                result[sensor] = times[edges.index(edge)]
        elif sensor == "141050975" and sensor in edges:
            index = edges.index(sensor)
            result[sensor] = times[index - 1] if index else times[index]
        elif sensor in edges:
            result[sensor] = times[edges.index(sensor)]
    return result


if __name__ == "__main__":
    raise SystemExit(main())
