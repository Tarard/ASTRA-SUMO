"""Locate measured route delays without dropping unfinished Hamburg vehicles."""

from collections import Counter, defaultdict
import gzip
import json
import math
from pathlib import Path
from statistics import median
import xml.etree.ElementTree as ET

from .artifact_io import write_json_atomic
from .candidate_contracts import file_sha256
from .detector_demand import write_csv
from .hamburg_readme_control import group_state
from .ocit_c_programs import read_ocit_fixed_program


def censored_route_intervals(*, demand, arrived_routes, snapshot, trips, selected_ids):
    """Retain exact completed intervals and elapsed lower bounds at one snapshot."""
    if snapshot.get("version") != "1.27.1":
        raise ValueError("Read native state times with the declared SUMO 1.27.1 format.")
    horizon = float(snapshot.get("time"))
    planned = {v.get("id"): v for v in demand.findall("vehicle")}
    trip = {v.get("id"): v for v in trips.findall("tripinfo")}
    arrived = {v.get("id"): v for v in arrived_routes.findall("vehicle") if float(v.get("arrival")) <= horizon}
    live = {i for lane in snapshot.findall("lane") for i in lane.find("vehicles").get("value").split()}
    state_vehicles = {v.get("id"): v for v in snapshot.findall("vehicle")}
    state_routes = {r.get("id"): r.get("edges").split() for r in snapshot.findall("route")}
    if not selected_ids or not set(selected_ids) <= planned.keys() or not math.isfinite(horizon):
        raise ValueError("Select explicit vehicles and a finite native snapshot time.")
    population, intervals = [], []
    for identity in sorted(selected_ids):
        request = math.floor(float(planned[identity].get("depart")) * 1000 + .5) / 1000
        if not math.isfinite(request) or not 0 <= request <= horizon:
            raise ValueError("Select only vehicles whose requests are due at the snapshot.")
        route = planned[identity].find("route").get("edges").split()
        depart = float(trip[identity].get("depart")) if identity in trip else None
        if depart is None or depart > horizon:
            if identity in live or identity in arrived:
                raise ValueError("A native vehicle record has no matching actual departure.")
            population.append({"vehicle_id": identity, "status": "not_inserted", "requested_s": request,
                "actual_depart_s": None, "insertion_wait_s": None, "insertion_wait_lower_bound_s": horizon - request})
            continue
        if not math.isfinite(depart) or depart < request - 1e-6:
            raise ValueError("Native insertion must follow the native requested time.")
        if identity in arrived:
            if identity in live:
                raise ValueError("An arrived vehicle cannot remain in the native lane stock.")
            record = arrived[identity].find("route")
            actual_route = record.get("edges").split()
            exits = list(map(float, record.get("exitTimes").split()))
            if len(exits) != len(route):
                raise ValueError("An arrived vehicle needs every ordinary-road exit time.")
            status = "arrived"
        elif identity in live:
            vehicle = state_vehicles[identity]
            actual_route = state_routes[vehicle.get("route")]
            devices = [d for d in vehicle.findall("device") if d.get("id") == "vehroute_" + identity]
            if len(devices) != 1:
                raise ValueError("A running vehicle needs one native route device.")
            # SUMO 1.27.1 saves raw SUMOTime milliseconds in the route device.
            exits = [int(t) / 1000 for t in devices[0].get("exitTimes", "").split()]
            if len(exits) >= len(route) or exits and devices[0].get("edge") != route[len(exits) - 1]:
                raise ValueError("The saved route device must describe one completed route prefix.")
            status = "running"
        else:
            raise ValueError("Retain every inserted vehicle in the arrived output or native stock.")
        if actual_route != route or len(route) != len(set(route)):
            raise ValueError("Retain the planned route without repeated roads.")
        previous = depart
        for edge, end in zip(route, exits):
            if not math.isfinite(end) or not previous <= end <= horizon:
                raise ValueError("Native exit times must be finite, ordered, and inside the snapshot horizon.")
            intervals.append({"vehicle_id": identity, "road": edge, "start_s": previous,
                "end_s": end, "elapsed_s": end - previous, "right_censored": False})
            previous = end
        if status == "running":
            intervals.append({"vehicle_id": identity, "road": route[len(exits)], "start_s": previous,
                "end_s": horizon, "elapsed_s": horizon - previous, "right_censored": True})
        population.append({"vehicle_id": identity, "status": status, "requested_s": request,
            "actual_depart_s": depart, "insertion_wait_s": depart - request, "insertion_wait_lower_bound_s": None})
    return population, intervals


def diagnose_hamburg_z15(*, case_dir, bindings_file, ocit_file, output_dir):
    """Diagnose the shared K8 approach from existing native records only."""
    case, out = Path(case_dir).resolve(strict=True), Path(output_dir).resolve()
    if out.exists() or case.is_relative_to(out):
        raise ValueError("Use a new diagnosis directory separate from its sources.")
    sources = {}

    def read(relative):
        path = case / relative
        if not path.is_file():
            path = path.with_name(path.name + ".gz")
        sources[str(path)] = file_sha256(path)
        raw = path.read_bytes()
        return gzip.decompress(raw) if path.suffix == ".gz" else raw

    demand = ET.fromstring(read("inputs/demand.rou.xml"))
    intentions = json.loads(read("inputs/lane-intentions.json"))["vehicles"]
    selected = {r["vehicle_id"] for r in intentions if r["field_id"] == "hh_0119_Z_15_29375" and r["population_role"] == "initialization"}
    snapshot = ET.fromstring(read("native/states/state_3599.900.xml.gz"))
    population, intervals = censored_route_intervals(demand=demand,
        arrived_routes=ET.fromstring(read("native/vehicle-routes.xml")), snapshot=snapshot,
        trips=ET.fromstring(read("native/tripinfo.xml")), selected_ids=selected)
    control = ET.fromstring(read("inputs/control.add.xml"))
    native_connections = {(c.get("from"), int(c.get("fromLane")), c.get("to"), int(c.get("toLane"))): c
                          for c in ET.fromstring(read("inputs/network.net.xml")).findall("connection")}
    bindings_path, ocit_path = Path(bindings_file).resolve(strict=True), Path(ocit_file).resolve(strict=True)
    if bindings_path.is_relative_to(out) or ocit_path.is_relative_to(out):
        raise ValueError("Keep source control records outside the diagnosis directory.")
    raw = bindings_path.read_bytes()
    bindings = json.loads(gzip.decompress(raw) if bindings_path.suffix == ".gz" else raw)["rows"]
    bound = [r for r in bindings if r["physical_key"][0] == "186713403#0" and r["physical_key"][2] == "142944399#2"]
    if len(bound) != 3 or any(r["groups"] != [["200", "K8"]] for r in bound):
        raise ValueError("Match the three physical left-turn links to source K8.")
    bound = [{**r, "native_link_index": int(native_connections[tuple(r["physical_key"])].get("linkIndex"))} for r in bound]
    program = control.find("tlLogic[@id='LSA200_part0']")
    source_program = read_ocit_fixed_program(ocit_path, "5")
    source_group = next(g for g in source_program["groups"] if g["group_id"] == "K8")
    clock, phases = 0.0, []
    for phase in program.findall("phase"):
        end = clock + float(phase.get("duration"))
        states = [phase.get("state")[r["native_link_index"]] for r in bound]
        expected = group_state(source_group, clock)
        if any(("G" if s in "Gg" else s) != expected for s in states):
            raise ValueError("Installed K8 states must retain the source program.")
        phases.append({"begin_s": clock, "end_s": end, "states": states})
        clock = end
    if program.get("type") != "static" or clock != source_program["cycle_s"] or float(program.get("offset", "0")) != 0:
        raise ValueError("Reassess the fixed source cycle and clock alignment.")
    green = sum(p["end_s"] - p["begin_s"] for p in phases if p["states"][0] in "Gg")
    by_road = defaultdict(list)
    for row in intervals:
        by_road[row["road"]].append(row)
    stages = []
    for road, rows in by_road.items():
        complete = [r["elapsed_s"] for r in rows if not r["right_censored"]]
        pending = [r["elapsed_s"] for r in rows if r["right_censored"]]
        stages.append({"road": road, "completed_intervals": len(complete), "censored_intervals": len(pending),
            "completed_elapsed_sum_s": sum(complete), "censored_elapsed_lower_sum_s": sum(pending),
            "observed_elapsed_sum_s": sum(complete) + sum(pending),
            "median_completed_interval_s": median(complete) if complete else None,
            "max_completed_interval_s": max(complete, default=None)})
    stages.sort(key=lambda r: r["observed_elapsed_sum_s"], reverse=True)
    crossing = [r for r in intervals if r["road"] == "186713403#0" and not r["right_censored"]]
    crossed = {r["vehicle_id"] for r in crossing}
    reconciliation = json.loads(read("native/passage-reconciliation.json"))
    all_field_passages = [r for r in reconciliation["passages"] if r["vehicle_id"] in selected]
    field_passed = {r["vehicle_id"] for r in all_field_passages if r["notification_time_s"] <= float(snapshot.get("time"))}
    periodic = ET.fromstring(read("native/field-counts.xml")).findall("interval")
    field_total = sum(int(r.get("nVehContrib")) for r in periodic if r.get("id") == "hh_0119_Z_15_29375")
    if (not reconciliation["reconciled"] or field_total != len(all_field_passages) or not field_passed <= crossed
            or any(r["detector_id"] != "hh_0119_Z_15_29375" for r in all_field_passages)):
        raise ValueError("Match the field passages to native E1 and the preceding K8 exits.")
    cycle_counts = Counter(int(r["end_s"] // clock) for r in crossing)
    cycle_rows = [{"cycle": i, "begin_s": i * clock, "observed_end_s": min((i + 1) * clock, float(snapshot.get("time"))),
                   "native_front_exits": cycle_counts[i]} for i in range(math.ceil(float(snapshot.get("time")) / clock))]
    if any("186713403#0" not in v.find("route").get("edges").split() for v in demand.findall("vehicle") if v.get("id") in selected):
        raise ValueError("This diagnosis requires the declared common K8 route.")
    sources.update({str(bindings_path): file_sha256(bindings_path), str(ocit_path): file_sha256(ocit_path)})
    out.mkdir(parents=True)
    for name, rows in [("population.csv", population), ("vehicle-road-intervals.csv", intervals),
                       ("road-interval-summary.csv", stages), ("K8-exits-by-cycle.csv", cycle_rows)]:
        write_csv(out / name, rows, list(rows[0]))
    write_json_atomic(out / "K8-control.json", {"source_program": "5", "cycle_s": clock, "green_s": green,
        "physical_movements": bound, "inlet_motor_lanes": 2, "phases": phases,
        "source_group": source_group, "source_states_retained": True})
    report = {"status": "measured", "formal_result": False, "new_simulations": 0,
        "snapshot_s": float(snapshot.get("time")), "selected_field": "hh_0119_Z_15_29375",
        "requested_initialization_vehicles": len(population), "population_status": dict(Counter(r["status"] for r in population)),
        "K8_cycle_s": clock, "K8_green_s": green, "K8_front_exits": len(crossing),
        "native_E1_count_0_3600": field_total, "complete_field_passages_at_snapshot": len(field_passed),
        "after_K8_before_complete_field_passage": len(crossed - field_passed),
        "K8_exit_cycle_phase_min_s": min((r["end_s"] % clock for r in crossing), default=None),
        "K8_exit_cycle_phase_max_s": max((r["end_s"] % clock for r in crossing), default=None),
        "inserted_without_K8_front_exit": sum(r["status"] != "not_inserted" and r["vehicle_id"] not in crossed for r in population),
        "source_sha256": sources, "sources_unchanged": all(file_sha256(Path(p)) == h for p, h in sources.items()),
        "claim_boundary": "Observed route intervals and elapsed lower bounds at one native snapshot. Intervals include the incoming internal connection and the ordinary road. Completed medians are conditional. Front exits are diagnostics, not native E1 counts or field observations. A fixed green duration is not a proven capacity bound or field timing. Main calibration remains incomplete."}
    write_json_atomic(out / "report.json", report)
    return report
