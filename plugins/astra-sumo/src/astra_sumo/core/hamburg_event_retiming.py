"""Bounded departure calibration with measured SUMO feedback and fixed speed factors."""

from __future__ import annotations

import copy
import json
import math
import shutil
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from pathlib import Path

from .cached_detector_demand import read_canonical_count_file
from .candidate_contracts import file_sha256
from .command_runner import run_command


def read_passages(path, detector_station, vehicle_ids, *, expected_entries=None, period=900, step_length=None):
    """Keep enter events; deduplicate only during the V1 station/bin count."""
    if not math.isfinite(period) or period <= 0:
        raise ValueError("Detector period must be positive and finite.")
    if step_length is not None and (not math.isfinite(step_length) or step_length <= 0
            or not math.isclose(period / step_length, round(period / step_length), rel_tol=0, abs_tol=1e-9)):
        raise ValueError("Simulation step must be positive, finite, and divide the detector period.")
    events = defaultdict(set)
    entries = Counter()
    root = ET.parse(path).getroot()
    if root.tag != "instantE1":
        raise ValueError("instant E1 output has an invalid root")
    for row in root.findall("instantOut"):
        detector = row.attrib["id"]
        if detector not in detector_station:
            raise ValueError(f"unknown detector in instant E1 output: {detector}")
        if row.get("state") != "enter":
            continue
        vehicle = row.attrib["vehID"]
        time = float(row.attrib["time"])
        if vehicle not in vehicle_ids or not math.isfinite(time) or time < 0:
            raise ValueError("instant E1 event has an invalid vehicle or time")
        if step_length is not None:
            # Instantaneous enter times interpolate within a step; periodic
            # E1 nVehEntered counts the notification at the step boundary.
            time = math.ceil(math.nextafter(time / step_length, -math.inf)) * step_length
        entries[detector, math.floor(time / period) * period] += 1
        events[vehicle].add((detector_station[detector], time))
    if expected_entries is not None and any(entries[key] != count for key, count in expected_entries.items()):
        raise ValueError("instant enter counts differ from periodic E1 nVehEntered")
    return dict(events)


def compare_passages(events, targets, simulation_begin, period):
    measured = defaultdict(set)
    for vehicle, passages in events.items():
        for station, time in passages:
            start = math.floor((time - simulation_begin) / period) * period
            if (station, start) in targets:
                measured[station, start].add(vehicle)
    comparisons = []
    station_error = defaultdict(int)
    for (station, start), target in sorted(targets.items()):
        actual = len(measured[station, start])
        error = actual - target
        station_error[station] += abs(error)
        comparisons.append(dict(station_stream_id=station, begin=start, end=start + period,
                                target=target, measured=actual, error=error, absolute_error=abs(error)))
    total = sum(station_error.values())
    return dict(comparisons=comparisons, total_absolute_error=total,
                station_absolute_error=dict(station_error), mae=total / len(targets),
                exact_bins=sum(row["error"] == 0 for row in comparisons),
                max_absolute_error=max(row["absolute_error"] for row in comparisons))


def retiming_improves(current, candidate):
    return (
        candidate["healthy"]
        and candidate["total_absolute_error"] < current["total_absolute_error"]
        and all(candidate["station_absolute_error"][station] <= error
                for station, error in current["station_absolute_error"].items())
    )


def _proposals(events, report, departures, original, begin, period, max_shift):
    errors = {(r["station_stream_id"], r["begin"]): r["error"] for r in report["comparisons"]}
    candidates = set()
    for vehicle, passages in events.items():
        for station, time in passages:
            start = math.floor((time - begin) / period) * period
            if errors.get((station, start), 0) <= 0:
                continue
            for neighbor in (start - period, start + period):
                if errors.get((station, neighbor), 0) >= 0:
                    continue
                for margin in (2, 5, 10, 30, 60):
                    desired = begin + neighbor + (period - margin if neighbor < start else margin)
                    delta = round(desired - time, 2)
                    new_depart = departures[vehicle] + delta
                    if delta and new_depart >= 0 and abs(new_depart - original[vehicle]) <= max_shift:
                        candidates.add((vehicle, delta))
    # ponytail: first improving single-vehicle move; consider paired moves only after a measured plateau.
    return sorted(candidates, key=lambda item: (abs(item[1]), len(events[item[0]]), item[0], item[1]))


def _write_json(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def calibrate_departures(*, source_run, canonical_counts, output_dir,
                         sumo_binary="sumo", seed=23423, max_trials=120, max_shift=120.0):
    """Keep a V1 scenario fixed, freeze speed factors, and accept measured improvements only."""
    source = Path(source_run).resolve(strict=True)
    destination = Path(output_dir).resolve()
    if destination.exists() or max_trials < 1 or not math.isfinite(max_shift) or max_shift <= 0:
        raise ValueError("use a new output directory and positive search limits")
    audit = json.loads((source / "count-audit.json").read_text(encoding="utf-8"))
    window = audit["window"]
    begin, end, period = window["simulation_begin"], window["simulation_end"], window["bin_seconds"]
    if window["field_begin"] != 0 or window["field_end"] != end - begin or period <= 0:
        raise ValueError("V1 count and simulation windows do not align")
    bank = json.loads((source / "station-detector-bank.json").read_text(encoding="utf-8"))
    required_files = ("network.net.xml", "e1.add.xml", "instant.add.xml", "station-detector-bank.json")
    for name in (*required_files, "demand.rou.xml"):
        if file_sha256(source / name) != audit["artifacts"][name]["sha256"]:
            raise ValueError(f"V1 source hash changed: {name}")
    count_path = Path(canonical_counts).resolve(strict=True)
    canonical = {(r.stream_id, r.begin): r for r in read_canonical_count_file(count_path)}
    targets, detector_station = {}, {}
    used_streams = set()
    for station in bank:
        sid = int(station["station_stream_id"])
        streams = [int(s) for row in station["detector_sources"] for s in row["source_stream_ids"]]
        if not streams or len(streams) != len(set(streams)) or used_streams.intersection(streams):
            raise ValueError("station membership is empty or repeats official count fields")
        used_streams.update(streams)
        for start in range(0, end - begin, period):
            rows = [canonical[s, start] for s in streams]
            if any(r.quality_status != "complete" or r.end != start + period for r in rows):
                raise ValueError("official count window is incomplete")
            targets[sid, start] = sum(r.count for r in rows)
        for detector in station["detector_ids"]:
            key = "instant_" + detector
            if key in detector_station:
                raise ValueError("a detector belongs to multiple stations")
            detector_station[key] = sid
    instant = ET.parse(source / "instant.add.xml").getroot()
    if {r.get("id") for r in instant} != set(detector_station):
        raise ValueError("instant detector file does not match the station bank")
    if any(r.tag != "instantInductionLoop" or r.get("file") != "instant-e1.xml" for r in instant):
        raise ValueError("instant detector outputs must remain local to each run")
    e1 = ET.parse(source / "e1.add.xml").getroot()
    if any(r.tag != "inductionLoop" or r.get("file") != "physical-e1-15min.xml" for r in e1):
        raise ValueError("E1 outputs must remain local to each run")
    tree = ET.parse(source / "demand.rou.xml")
    vehicle_ids = {v.attrib["id"] for v in tree.getroot().findall("vehicle")}
    if len(vehicle_ids) != len(tree.getroot().findall("vehicle")):
        raise ValueError("demand contains duplicate vehicle ids")
    horizon = 24000
    destination.mkdir(parents=True)
    history = []

    def simulate(label, demand_tree):
        run = destination / label
        run.mkdir()
        for name in required_files:
            shutil.copy2(source / name, run / name)
        demand_tree.write(run / "demand.rou.xml", encoding="utf-8", xml_declaration=True)
        command = [str(sumo_binary), "-n", "network.net.xml", "-r", "demand.rou.xml",
                   "-a", "e1.add.xml,instant.add.xml", "--end", str(horizon), "--seed", str(seed),
                   "--summary-output", "summary.xml", "--tripinfo-output", "tripinfo.xml",
                   "--tripinfo-output.write-unfinished", "true", "--precision", "12", "--step-length", "1",
                   "--collision-output", "collisions.xml", "--collision.check-junctions", "true",
                   "--log", "sumo.log", "--error-log", "sumo-error.log",
                   "--duration-log.statistics", "true", "--no-step-log", "true"]
        command_result = run_command(command, cwd=run, timeout_seconds=60)
        _write_json(run / "command.json", command_result.to_dict())
        if command_result.status != "pass":
            raise RuntimeError(f"SUMO failed; see {run / 'command.json'}")
        last = ET.parse(run / "summary.xml").getroot().findall("step")[-1]
        health = {key: int(last.attrib[key]) for key in
                  ("loaded", "inserted", "arrived", "running", "waiting", "teleports", "collisions", "discarded")}
        # The regular E1 file supplies interval coverage, including explicit zero counts.
        expected = {(d, t) for s in bank for d in s["detector_ids"] for t in range(begin, end, period)}
        observed = set()
        expected_entries = {}
        for row in ET.parse(run / "physical-e1-15min.xml").getroot().findall("interval"):
            key = (row.attrib["id"], float(row.attrib["begin"]))
            if key in expected and float(row.attrib["end"]) == key[1] + period:
                if key in observed:
                    raise ValueError("E1 output repeats an interval")
                observed.add(key)
                expected_entries["instant_" + key[0], key[1]] = int(row.attrib["nVehEntered"])
        if observed != expected:
            raise ValueError("E1 output lacks a required detector interval")
        trips = ET.parse(run / "tripinfo.xml").getroot().findall("tripinfo")
        if len(trips) != len(vehicle_ids) or {v.get("id") for v in trips} != vehicle_ids:
            raise ValueError("tripinfo vehicle ids differ from the demand")
        expected_factors = {v.attrib["id"]: float(v.attrib["speedFactor"])
                            for v in demand_tree.getroot().findall("vehicle") if "speedFactor" in v.attrib}
        if any(not math.isclose(float(v.attrib["speedFactor"]), expected_factors[v.attrib["id"]],
                                rel_tol=0, abs_tol=1e-10)
               for v in trips if v.attrib["id"] in expected_factors):
            raise ValueError("SUMO changed a fixed vehicle speed factor")
        events = read_passages(run / "instant-e1.xml", detector_station, vehicle_ids,
                               expected_entries=expected_entries, period=period, step_length=1)
        report = compare_passages(events, targets, begin, period)
        report.update(run_directory=str(run), completion=health,
                      healthy=(all(health[k] == len(vehicle_ids) for k in ("loaded", "inserted", "arrived"))
                               and not any(health[k] for k in ("running", "waiting", "teleports", "collisions", "discarded"))
                               and not (run / "sumo-error.log").read_text(encoding="utf-8").strip()))
        _write_json(run / "count-audit.json", report)
        return report, events

    reproduction, _ = simulate("v1-reproduction", tree)
    if (not reproduction["healthy"] or [(r["station_stream_id"], r["begin"], r["target"], r["measured"])
            for r in reproduction["comparisons"]] != [(r["station_stream_id"], r["begin"], r["target"], r["measured"])
            for r in audit["comparisons"]]):
        raise ValueError("V1 reproduction differs; stop before calibration")
    trips = ET.parse(destination / "v1-reproduction/tripinfo.xml").getroot().findall("tripinfo")
    factors = {v.attrib["id"]: v.attrib["speedFactor"] for v in trips}
    for vehicle in tree.getroot().findall("vehicle"):
        vehicle.set("speedFactor", factors[vehicle.attrib["id"]])
    baseline, events = simulate("fixed-speed-baseline", tree)
    if not baseline["healthy"]:
        raise ValueError("fixed-speed baseline is incomplete or has warnings")
    current = baseline
    original = {v.attrib["id"]: float(v.attrib["depart"]) for v in tree.getroot().findall("vehicle")}
    print(json.dumps({"stage": "baseline", "v1_error": reproduction["total_absolute_error"],
                      "fixed_speed_error": baseline["total_absolute_error"]}), flush=True)
    tried = set()
    for trial in range(1, max_trials + 1):
        if current["total_absolute_error"] == 0:
            break
        departures = {v.attrib["id"]: float(v.attrib["depart"]) for v in tree.getroot().findall("vehicle")}
        proposals = [p for p in _proposals(events, current, departures, original, begin, period, max_shift)
                     if p not in tried]
        if not proposals:
            break
        vehicle_id, delta = proposals[0]
        tried.add((vehicle_id, delta))
        candidate_tree = copy.deepcopy(tree)
        root = candidate_tree.getroot()
        for vehicle in root.findall("vehicle"):
            if vehicle.attrib["id"] == vehicle_id:
                vehicle.set("depart", f"{departures[vehicle_id] + delta:.2f}")
        vehicles = sorted(root.findall("vehicle"), key=lambda v: (float(v.attrib["depart"]), v.attrib["id"]))
        for vehicle in root.findall("vehicle"):
            root.remove(vehicle)
        root.extend(vehicles)
        try:
            report, candidate_events = simulate(f"trial-{trial:03d}", candidate_tree)
        except (ValueError, RuntimeError, ET.ParseError) as error:
            report = dict(healthy=False, total_absolute_error=None, rejection_reason=str(error),
                          run_directory=str(destination / f"trial-{trial:03d}"))
            candidate_events = {}
            _write_json(destination / f"trial-{trial:03d}" / "rejected.json", report)
        accepted = retiming_improves(current, report)
        history.append(dict(trial=trial, vehicle_id=vehicle_id, shift_seconds=delta, accepted=accepted,
                            total_absolute_error=report["total_absolute_error"], healthy=report["healthy"],
                            rejection_reason=report.get("rejection_reason"),
                            run_directory=report["run_directory"]))
        if accepted:
            current, events, tree = report, candidate_events, candidate_tree
            tried.clear()
        _write_json(destination / "progress.json", dict(baseline=baseline, best=current, trials=history))
        if accepted or trial % 10 == 0:
            print(json.dumps(history[-1] | {"best_error": current["total_absolute_error"]}), flush=True)
    result = dict(status="exact_fit" if current["total_absolute_error"] == 0 else "bounded_search_complete",
                  claim_status="diagnostic-demo", source_run=str(source), seed=seed,
                  speed_factors="fixed per vehicle from 12-digit V1 reproduction output",
                  window=window, count_time_basis="periodic E1 notification bins, 1-second simulation steps",
                  max_shift_from_baseline_seconds=max_shift, max_trials=max_trials,
                  source_hashes={name: file_sha256(source / name) for name in (*required_files, "demand.rou.xml")},
                  canonical_counts=dict(path=str(count_path), sha256=file_sha256(count_path)),
                  source_limitations=audit.get("limitations", []), baseline=baseline, best=current,
                  trials=history, interpretation="Calibration-window fit only; no held-out validation.")
    _write_json(destination / "result.json", result)
    return result
