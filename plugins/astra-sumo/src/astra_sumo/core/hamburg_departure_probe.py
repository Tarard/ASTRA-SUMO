"""Test one measured departure-time perturbation before another full calibration run."""

from copy import deepcopy
import csv
import gzip
import io
import json
import math
from pathlib import Path
import sys
import xml.etree.ElementTree as ET

from .artifact_io import write_json_atomic
from .candidate_contracts import file_sha256
from .command_runner import run_command
from .detector_demand import read_csv_rows
from .route_sensor_matrix import read_contributing_e1_passages


ENTRY = "44532531"
ADVANCE_S = 900


def apply_native_count_retiming(routes, proposals):
    """Apply measured identity-specific departure changes without resampling routes."""
    result = deepcopy(routes)
    vehicles = {v.get('id'): v for v in result.findall('vehicle')}
    changed = set()
    if len(vehicles) != len(result.findall('vehicle')):
        raise ValueError('Retain distinct explicit vehicle identities.')
    for row in proposals:
        identity = row['vehicle_id']
        old, new = row['source_depart_s'], row['proposed_depart_s']
        if (identity not in vehicles or identity in changed or not math.isfinite(old)
                or not math.isfinite(new) or new < 0
                or abs(float(vehicles[identity].get('depart')) - old) > 1e-8):
            raise ValueError('Match each changed departure to one source vehicle and a finite nonnegative time.')
        vehicles[identity].set('depart', f'{new:.9f}')
        changed.add(identity)
    for vehicle in vehicles.values():
        result.remove(vehicle)
    result.extend(sorted(vehicles.values(), key=lambda v: (float(v.get('depart')), v.get('id'))))
    return result


def prepare_hamburg_count_retiming(*, prepared_dir, measurement_dir, output_dir, scale=1):
    """Prepare bounded timing feedback from reconciled native E1 measurements."""
    from .native_temporal_feedback import propose_native_count_retiming

    source, measured = [Path(path).resolve(strict=True) for path in (prepared_dir, measurement_dir)]
    out = Path(output_dir).resolve()
    if out.exists() or any(path.is_relative_to(out) for path in (source, measured)) or scale not in (1, .5):
        raise ValueError("Use separate new outputs and a full or half correction.")
    inputs = {}

    def read(folder, name):
        path = folder / name
        if not path.is_file():
            path = path.with_name(path.name + ".gz")
        inputs[str(path)] = file_sha256(path)
        raw = path.read_bytes()
        return gzip.decompress(raw) if path.suffix == ".gz" else raw

    names = ("network.net.xml", "demand.rou.xml", "field-loops.add.xml", "expected-counts.csv",
             "initialization-counts.csv", "control.add.xml", "lane-intentions.json", "scenario.json")
    raw = {name: read(source, name) for name in names}
    reconciliation = json.loads(read(measured, "passage-reconciliation.json"))
    if reconciliation["status"] != "measured" or not reconciliation["reconciled"]:
        raise ValueError("Reconcile native E1 identities before proposing a correction.")
    routes, intentions = ET.fromstring(raw["demand.rou.xml"]), json.loads(raw["lane-intentions.json"])
    vehicles = {vehicle.get("id"): vehicle for vehicle in routes.findall("vehicle")}
    intent = {row["vehicle_id"]: row for row in intentions["vehicles"]}
    if (len(vehicles) != len(routes.findall("vehicle")) or len(intent) != len(intentions["vehicles"])
            or set(vehicles) != set(intent)):
        raise ValueError("Match every distinct vehicle to one declared lane intention.")
    requests = [{"vehicle_id": identity, "detector_id": intent[identity]["field_id"],
                 "requested_depart_s": float(vehicle.get("depart"))} for identity, vehicle in vehicles.items()]
    measurements = list(csv.DictReader(io.StringIO(read(measured, "field-comparison.csv").decode("utf-8"))))
    targets = list(csv.DictReader(io.StringIO(raw["expected-counts.csv"].decode("utf-8"))))
    def key(row):
        return row["detector_id"], float(row["begin"]), float(row["end"])
    if (len(measurements) != 130 or len(targets) != 130 or len({row["detector_id"] for row in targets}) != 13
            or {key(row): int(row["expected_total"]) for row in measurements}
            != {key(row): int(row["expected_total"]) for row in targets}):
        raise ValueError("Retain all 130 Hamburg field targets with their original identities and values.")
    passages = reconciliation["passages"]
    proposal = propose_native_count_retiming(requests=requests, passages=passages, measurements=measurements)
    for change in proposal["proposals"]:
        identity = change["vehicle_id"]
        shift = round(change["shift_s"] * scale / .1) * .1
        depart = round(change["source_depart_s"] + shift, 9)
        intent[identity]["depart_s"] = depart
        change.update(applied_shift_s=shift, applied_depart_s=depart)
    routes = apply_native_count_retiming(routes, [{**change, "proposed_depart_s": change["applied_depart_s"]}
                                                 for change in proposal["proposals"]])
    out.mkdir(parents=True)
    for name, content in raw.items():
        (out / name).write_bytes(content)
    ET.ElementTree(routes).write(out / "demand.rou.xml", encoding="utf-8", xml_declaration=True)
    write_json_atomic(out / "lane-intentions.json", intentions)
    scenario = json.loads(raw["scenario.json"])
    scenario.update(time_prior="Bounded corrections from surplus native E1 bins and later observed passages. Initialization requests, routes and target rows stay fixed. New native measurement required.",
                    demand_sha256=file_sha256(out / "demand.rou.xml"), native_feedback_scale=scale)
    write_json_atomic(out / "scenario.json", scenario)
    proposal.update(scale=scale, inputs=inputs, input_bytes_unchanged=all(
        file_sha256(Path(path)) == digest for path, digest in inputs.items()),
        unchanged_network_sha256=file_sha256(out / "network.net.xml"),
        unchanged_control_sha256=file_sha256(out / "control.add.xml"),
        unchanged_targets_sha256=file_sha256(out / "expected-counts.csv"))
    write_json_atomic(out / "native-count-feedback-proposal.json", proposal)
    return proposal


def single_pass_structure(network, routes, loops):
    """Check the structural basis of this declared one-field-per-car model."""
    lanes = {lane.get("id"): (edge.get("id"), float(lane.get("length")), lane.get("index", str(i)))
             for edge in network.findall("edge") for i, lane in enumerate(edge.findall("lane"))}
    field_roads = {lanes[r.get("lane")][0] for r in loops}
    kinds = {r.get("id"): r for r in routes.findall("vType")}
    minimum_length = min(float(kinds[v.get("type")].get("length")) for v in routes.findall("vehicle"))
    road_routes = [v.find("route").get("edges").split() for v in routes.findall("vehicle")]
    exits = {(c.get("from"), c.get("fromLane"), c.get("to")) for c in network.findall("connection")}
    countable_roads = []
    for edges in road_routes:
        following = {edge: edges[i + 1] if i + 1 < len(edges) else None for i, edge in enumerate(edges)}
        possible = set()
        for loop in loops:
            road = lanes[loop.get("lane")][0]
            if road in following and (following[road] is None or (road, lanes[loop.get("lane")][2], following[road]) in exits):
                possible.add(road)
        countable_roads.append(possible)
    checks = {
        "at_most_one_countable_detector_road_per_route": all(len(roads) <= 1 for roads in countable_roads),
        "no_repeated_roads": all(len(set(edges)) == len(edges) for edges in road_routes),
        "one_detector_per_lane": len({r.get("lane") for r in loops}) == len(loops),
        "detectors_nearer_to_road_end_than_vehicle_length": all(
            0 <= lanes[r.get("lane")][1] - float(r.get("pos")) < minimum_length for r in loops),
        "standard_instantaneous_lane_change_model": all(k.get("laneChangeModel", "LC2013") == "LC2013" for k in kinds.values()),
    }
    return {"one_observed_road_per_route": all(len(field_roads & set(edges)) == 1 for edges in road_routes),
        **checks, "structural_conditions_satisfied": all(checks.values()),
        "minimum_vehicle_length_m": minimum_length,
        "condition": "Declared routes, forward driving, standard instantaneous lane changes, and no collision, teleport or vehicle relocation. Near-end complete E1 passages require an exit from the detector lane to the route's next road. Different observed roads alone do not imply repeated or multiple detector contributions."}


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
    if len(expected) != 130 or len({r["detector_id"] for r in expected}) != 13:
        raise ValueError("Retain the complete Hamburg target and every field.")
    baseline_early = sum(int(r.get("nVehContrib")) for r in
                         ET.parse(case / "native/field-counts.xml").getroot().findall("interval")
                         if float(r.get("end")) <= 3600)
    out.mkdir(parents=True)
    descriptions = []
    for label, demand, intent in [("baseline", root, intentions), ("advance900", moved, moved_intentions)]:
        full = out / label / "full-inputs"
        full.mkdir(parents=True)
        for name in names:
            (full / name).write_bytes(raw[name])
        if label != "baseline":
            ET.ElementTree(demand).write(full / "demand.rou.xml", encoding="utf-8", xml_declaration=True)
            write_json_atomic(full / "lane-intentions.json", intent)
            scenario = json.loads(raw["scenario.json"])
            scenario.update(all_integer_rows_exact=None,
                time_prior="Entry 44532531 requests advance by 900 s. All target field bins stay fixed. New native counts are required.")
            write_json_atomic(full / "scenario.json", scenario)
        descriptions.append({"case": label, "full_vehicles": len(demand.findall("vehicle")),
            "full_field_rows": 130,
            "requests_before_observation": sum(float(v.get("depart")) < 3600 for v in demand.findall("vehicle")),
            "unchanged_network_sha256": file_sha256(full / "network.net.xml"),
            "unchanged_control_sha256": file_sha256(full / "control.add.xml"),
            "unchanged_full_targets_sha256": file_sha256(full / "expected-counts.csv")})
    report = {"status": "prepared", "formal_result": False, "entry": ENTRY, "advance_s": ADVANCE_S,
        "simulation_end_s": 3600, "cases": descriptions, "baseline_native_pre_window_passages": baseline_early,
        "claim_boundary": "Pre-window feasibility check of one demand perturbation. Both full 130-row inputs remain intact. No global fit or derivative is claimed."}
    write_json_atomic(out / "proposal.json", report)
    return report


def run_hamburg_departure_probe(*, prepared_dir, output_dir, sumo_binary):
    source, out = Path(prepared_dir).resolve(strict=True), Path(output_dir).resolve()
    if out.exists() or source.is_relative_to(out):
        raise ValueError("Use a new native probe directory separate from its inputs.")
    out.mkdir(parents=True)
    warmup = run_departure_warmup_check(full_inputs=source / "advance900/full-inputs",
                                       output_dir=out / "warmup-check", sumo_binary=sumo_binary)
    report = {"formal_result": False, "global_calibration_established": False, "warmup_check": warmup,
        "decision": "Reject the fixed-population advance." if warmup["candidate_rejected_under_fixed_population"]
                    else "Early passage loss does not reject this candidate. Full-window fit remains untested."}
    write_json_atomic(out / "comparison.json", report)
    return report
