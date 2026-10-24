"""Test one measured departure-time perturbation before another full calibration run."""

from copy import deepcopy
import gzip
import json
import math
from pathlib import Path
import sys
import xml.etree.ElementTree as ET

from .artifact_io import write_json_atomic
from .candidate_contracts import file_sha256
from .command_runner import run_command
from .detector_demand import read_csv_rows, write_csv
from .detector_demand_replay import run_point_detector_demand
from .route_sensor_matrix import read_contributing_e1_passages


ENTRY = "44532531"
ADVANCE_S = 900
PREFIX_END_S = 5400


def single_pass_structure(network, routes, loops):
    """Check the structural basis of this declared one-field-per-car model."""
    lanes = {lane.get("id"): (edge.get("id"), float(lane.get("length")))
             for edge in network.findall("edge") for lane in edge.findall("lane")}
    field_roads = {lanes[r.get("lane")][0] for r in loops}
    kinds = {r.get("id"): r for r in routes.findall("vType")}
    minimum_length = min(float(kinds[v.get("type")].get("length")) for v in routes.findall("vehicle"))
    road_routes = [v.find("route").get("edges").split() for v in routes.findall("vehicle")]
    checks = {
        "one_observed_road_per_route": all(len(field_roads & set(edges)) == 1 for edges in road_routes),
        "no_repeated_roads": all(len(set(edges)) == len(edges) for edges in road_routes),
        "one_detector_per_lane": len({r.get("lane") for r in loops}) == len(loops),
        "detectors_nearer_to_road_end_than_vehicle_length": all(
            0 <= lanes[r.get("lane")][1] - float(r.get("pos")) < minimum_length for r in loops),
        "standard_instantaneous_lane_change_model": all(k.get("laneChangeModel", "LC2013") == "LC2013" for k in kinds.values()),
    }
    return {**checks, "structural_conditions_satisfied": all(checks.values()),
        "minimum_vehicle_length_m": minimum_length,
        "condition": "Declared routes, forward driving, standard instantaneous lane changes, and no collision, teleport or vehicle relocation."}


def run_departure_warmup_check(*, full_inputs, output_dir, sumo_binary):
    """Measure early native passages without inventing pre-window observations."""
    p, out = Path(full_inputs).resolve(strict=True), Path(output_dir).resolve()
    if out.exists():
        raise ValueError("Use a new warm-up measurement directory.")
    network, routes, loops = [ET.parse(p / name).getroot() for name in
                              ("network.net.xml", "demand.rou.xml", "field-loops.add.xml")]
    structure = single_pass_structure(network, routes, loops)
    out.mkdir(parents=True)
    instant, mapping = ET.Element("additional"), {}
    for i, loop in enumerate(loops):
        loop.set("file", str(out / "field-counts.xml"))
        loop.set("begin", "0")
        loop.set("end", "3600")
        identity = f"astra_trace_{i}"
        mapping[identity] = loop.get("id")
        ET.SubElement(instant, "instantInductionLoop", id=identity, lane=loop.get("lane"),
                      pos=loop.get("pos"), file=str(out / "instant-e1.xml"))
    ET.ElementTree(loops).write(out / "e1.add.xml", encoding="utf-8", xml_declaration=True)
    ET.ElementTree(instant).write(out / "instant.add.xml", encoding="utf-8", xml_declaration=True)
    command = [str(sumo_binary), "--net-file", str(p / "network.net.xml"), "--route-files", str(p / "demand.rou.xml"),
        "--additional-files", ",".join(map(str, [p / "control.add.xml", out / "e1.add.xml", out / "instant.add.xml"])),
        "--end", "3600", "--step-length", "0.1", "--seed", "104", "--precision", "12", "--time-to-teleport", "-1",
        "--collision.check-junctions", "true", "--summary-output", str(out / "summary.xml"),
        "--tripinfo-output", str(out / "tripinfo.xml"), "--tripinfo-output.write-unfinished", "true",
        "--collision-output", str(out / "collisions.xml"), "--error-log", str(out / "sumo-errors.log"),
        "--log", str(out / "sumo.log"), "--no-step-log", "true"]
    request = {"command": command, "end_s": 3600, "all_stopped_timeout_s": 300,
        "monitor_interval_s": None, "monitor_file": str(out / "stall-monitor.json"),
        "lane_intentions_file": str(p / "lane-intentions.json"), "route_file": str(p / "demand.rou.xml"),
        "e1_file": str(p / "field-loops.add.xml")}
    write_json_atomic(out / "native-request.json", request)
    executed = run_command([sys.executable, "-m", "astra_sumo.core.simulation_stall", str(out / "native-request.json")],
                           cwd=out, timeout_seconds=600)
    write_json_atomic(out / "command.json", executed.to_dict())
    if executed.status != "pass":
        raise ValueError("The native warm-up check did not complete. Retain its raw records.")
    monitor = json.loads((out / "stall-monitor.json").read_bytes())
    if not monitor["native_version"][1].endswith("1.27.1"):
        raise ValueError("Use the declared SUMO 1.27.1 runtime.")
    passage = read_contributing_e1_passages(instant_file=out / "instant-e1.xml", periodic_file=out / "field-counts.xml",
                                          detector_ids=mapping, step_length=.1)
    final = ET.parse(out / "summary.xml").getroot()[-1]
    early = len(passage["passages"])
    unique = len({r["vehicle_id"] for r in passage["passages"]})
    safe = int(final.get("collisions")) == int(final.get("teleports")) == 0
    bound_valid = structure["structural_conditions_satisfied"] and passage["reconciled"] and early == unique and safe
    target = sum(int(r["expected_total"]) for r in read_csv_rows(p / "expected-counts.csv"))
    population = len(routes.findall("vehicle"))
    report = {"status": "measured", "formal_result": False, "pre_window_real_observations": None,
        "native_monitor_status": monitor["status"], "native_end_s": monitor["simulation_time_s"],
        "structure": structure, "native_safe_prefix": safe, "native_E1_reconciled": passage["reconciled"],
        "early_native_complete_passages": early, "early_unique_vehicles": unique,
        "full_population": population, "real_window_target_sum": target,
        "bound_valid_under_declared_single_pass_model": bound_valid,
        "real_window_total_upper_bound": population - early if bound_valid else None,
        "candidate_rejected_under_fixed_population": bound_valid and population - early < target,
        "claim_boundary": "Conservation under this fixed one-field population and route model. No zero observation is assigned outside the real window. A different population or route model requires separate validation."}
    write_json_atomic(out / "warmup-check.json", report)
    return report


def shift_entry_requests(routes, intentions, entry, advance_s):
    """Change requested times only. Keep target field bins and every vehicle."""
    root, updated = deepcopy(routes), deepcopy(intentions)
    if not math.isfinite(advance_s) or advance_s <= 0:
        raise ValueError("Use a positive finite departure advance.")
    assigned = {r["vehicle_id"]: r for r in updated["vehicles"]}
    vehicles = root.findall("vehicle")
    if len(assigned) != len(updated["vehicles"]) or set(assigned) != {v.get("id") for v in vehicles}:
        raise ValueError("Retain one lane intention for every explicit vehicle.")
    shifted = 0
    for vehicle in vehicles:
        old = float(vehicle.get("depart"))
        change = advance_s if vehicle.find("route").get("edges").split()[0] == entry else 0
        if not math.isfinite(old) or old - change < 0:
            raise ValueError("The perturbation needs an earlier declared simulation start.")
        vehicle.set("depart", f"{old - change:.9f}")
        row = assigned[vehicle.get("id")]
        row.update(source_depart_s=old, depart_s=old - change, departure_advance_s=change)
        shifted += bool(change)
    if not shifted:
        raise ValueError("The specified entry has no requested vehicles.")
    for vehicle in vehicles:
        root.remove(vehicle)
    root.extend(sorted(vehicles, key=lambda v: (float(v.get("depart")), v.get("id"))))
    return root, updated, shifted


def prepare_hamburg_departure_probe(*, case_dir, output_dir):
    case, out = Path(case_dir).resolve(strict=True), Path(output_dir).resolve()
    if out.exists() or case.is_relative_to(out):
        raise ValueError("Use a new probe directory separate from the frozen case.")

    def read(name):
        path = case / "inputs" / name
        return path.read_bytes() if path.is_file() else gzip.decompress(path.with_name(name + ".gz").read_bytes())

    names = ["network.net.xml", "demand.rou.xml", "field-loops.add.xml", "expected-counts.csv",
             "control.add.xml", "lane-intentions.json", "scenario.json"]
    raw = {name: read(name) for name in names}
    root, intentions = ET.fromstring(raw["demand.rou.xml"]), json.loads(raw["lane-intentions.json"])
    moved, moved_intentions, shifted = shift_entry_requests(root, intentions, ENTRY, ADVANCE_S)
    if len(root.findall("vehicle")) != 6687 or shifted != 2232:
        raise ValueError("Use the declared 6687-vehicle Hamburg source with its original entry allocation.")
    expected = read_csv_rows(case / "inputs/expected-counts.csv")
    probe_rows = [r for r in expected if float(r["end"]) <= PREFIX_END_S]
    if len(expected) != 130 or len(probe_rows) != 26 or len({r["detector_id"] for r in probe_rows}) != 13:
        raise ValueError("Retain the complete Hamburg target and all fields in its first two bins.")
    source_counts = read_csv_rows(case / "field-comparison.csv")
    baseline_counts = [r for r in source_counts if float(r["end"]) <= PREFIX_END_S]
    out.mkdir(parents=True)
    descriptions = []
    for label, demand, intent in [("baseline", root, intentions), ("advance900", moved, moved_intentions)]:
        full = out / label / "full-inputs"
        prefix = out / label / "prefix-inputs"
        full.mkdir(parents=True)
        prefix.mkdir()
        for name in names:
            (full / name).write_bytes(raw[name])
        if label != "baseline":
            ET.ElementTree(demand).write(full / "demand.rou.xml", encoding="utf-8", xml_declaration=True)
            write_json_atomic(full / "lane-intentions.json", intent)
            scenario = json.loads(raw["scenario.json"])
            scenario.update(all_integer_rows_exact=None,
                time_prior="Entry 44532531 requests advance by 900 s. All target field bins stay fixed. New native counts are required.")
            write_json_atomic(full / "scenario.json", scenario)
        selected = {v.get("id") for v in demand.findall("vehicle")
                    if math.floor(float(v.get("depart")) * 1000 + .5) < PREFIX_END_S * 1000}
        subset = deepcopy(demand)
        for v in subset.findall("vehicle"):
            if v.get("id") not in selected:
                subset.remove(v)
        for name in ["network.net.xml", "field-loops.add.xml", "control.add.xml"]:
            (prefix / name).write_bytes(raw[name])
        ET.ElementTree(subset).write(prefix / "demand.rou.xml", encoding="utf-8", xml_declaration=True)
        prefix_intent = deepcopy(intent)
        prefix_intent["vehicles"] = [r for r in intent["vehicles"] if r["vehicle_id"] in selected]
        write_json_atomic(prefix / "lane-intentions.json", prefix_intent)
        write_csv(prefix / "expected-counts.csv", probe_rows, list(probe_rows[0]))
        descriptions.append({"case": label, "full_vehicles": len(demand.findall("vehicle")),
            "prefix_vehicles": len(selected), "full_field_rows": 130, "probe_field_rows": 26,
            "requests_before_observation": sum(float(v.get("depart")) < 3600 for v in demand.findall("vehicle")),
            "unchanged_network_sha256": file_sha256(full / "network.net.xml"),
            "unchanged_control_sha256": file_sha256(full / "control.add.xml"),
            "unchanged_full_targets_sha256": file_sha256(full / "expected-counts.csv")})
    write_csv(out / "frozen-baseline-prefix-counts.csv", baseline_counts, list(baseline_counts[0]))
    report = {"status": "prepared", "formal_result": False, "entry": ENTRY, "advance_s": ADVANCE_S,
        "prefix_end_s": PREFIX_END_S, "simulation_end_s": 9000, "cases": descriptions,
        "claim_boundary": "One-sided finite demand perturbation. Prefix counts must first reproduce the frozen baseline. Both full 130-row inputs remain intact. No global fit or derivative is claimed."}
    write_json_atomic(out / "proposal.json", report)
    return report


def run_hamburg_departure_probe(*, prepared_dir, output_dir, sumo_binary):
    source, out = Path(prepared_dir).resolve(strict=True), Path(output_dir).resolve()
    if out.exists() or source.is_relative_to(out):
        raise ValueError("Use a new native probe directory separate from its inputs.")
    reference = read_csv_rows(source / "frozen-baseline-prefix-counts.csv")

    def key(row):
        return row["detector_id"], float(row["begin"]), float(row["end"])

    ref = {key(r): r["measured_nVehContrib"] for r in reference}
    out.mkdir(parents=True)
    results = []
    warmup = run_departure_warmup_check(full_inputs=source / "advance900/full-inputs",
                                       output_dir=out / "warmup-check", sumo_binary=sumo_binary)
    if warmup["candidate_rejected_under_fixed_population"]:
        report = {"formal_result": False, "global_calibration_established": False,
            "cases": [], "warmup_check": warmup,
            "decision": "Reject the fixed-population advance. Do not run longer prefix or full-window trials."}
        write_json_atomic(out / "comparison.json", report)
        return report
    for label in ["baseline", "advance900"]:
        p, native = source / label / "prefix-inputs", out / label
        run = run_point_detector_demand(net_file=p / "network.net.xml", route_file=p / "demand.rou.xml",
            e1_additional_file=p / "field-loops.add.xml", expected_counts_csv=p / "expected-counts.csv",
            output_dir=native, end_s=9000, sumo_binary=str(sumo_binary), timeout_seconds=1800,
            signal_program_file=p / "control.add.xml", lane_intentions_file=p / "lane-intentions.json")
        rows = read_csv_rows(native / "field-comparison.csv")
        same_prefix = {key(r): r["measured_nVehContrib"] for r in rows} == ref
        residuals = [abs(int(r["measured_nVehContrib"]) - int(r["expected_total"])) for r in rows
                     if r["measured_nVehContrib"] not in {"", "None"}]
        summary = {"case": label, "probe_only": True, "healthy": run.get("healthy", False),
            "completion": run.get("completion"), "field_rows": len(rows),
            "observed_rows": len(residuals), "exact_rows": run["exact_field_bins"],
            "diagnostic_L1": sum(residuals) if len(residuals) == 26 else None,
            "diagnostic_max_abs": max(residuals) if len(residuals) == 26 else None,
            "baseline_prefix_equal_frozen_full_run": same_prefix if label == "baseline" else None}
        results.append(summary)
        write_json_atomic(out / "comparison.json", {"formal_result": False, "global_calibration_established": False,
            "cases": results, "claim_boundary": "Only the first two observed bins, all thirteen fields. Full-window evaluation remains required."})
        if label == "baseline" and not same_prefix:
            raise ValueError("The prefix does not reproduce the frozen full run. Do not interpret the perturbation.")
    return {"formal_result": False, "cases": results}
