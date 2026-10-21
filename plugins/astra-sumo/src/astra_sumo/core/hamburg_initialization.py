"""Add a separately labelled preceding-hour demand prior to the Hamburg case."""

from collections import Counter, defaultdict
from copy import deepcopy
import gzip
import json
import math
from pathlib import Path
import xml.etree.ElementTree as ET

from .artifact_io import write_json_atomic
from .candidate_contracts import file_sha256
from .detector_demand import read_csv_rows
from .detector_demand_replay import run_point_detector_demand
from .route_sensor_matrix import read_contributing_e1_passages


def add_initialization_population(routes, intentions, count_rows):
    """Allocate real field passages using the declared one-field-per-car prior."""
    demand, intent = deepcopy(routes), deepcopy(intentions)
    vehicles = {v.get("id"): v for v in demand.findall("vehicle")}
    assigned = {r["vehicle_id"]: r for r in intent["vehicles"]}
    if (len(vehicles) != len(demand.findall("vehicle")) or len(assigned) != len(intent["vehicles"])
            or vehicles.keys() != assigned.keys() or any(r.get("population_role") == "initialization" for r in assigned.values())):
        raise ValueError("Supply the original main-window vehicles and one intention for each vehicle.")
    # shortcut: Inherit main-window route shares until independent initialization OD evidence is available.
    weights, templates = defaultdict(Counter), {}
    for identity, row in assigned.items():
        field, profile = row["field_id"], row["profile_index"]
        weights[field][profile] += 1
        template = deepcopy(vehicles[identity])
        for attribute in ("id", "depart"):
            template.attrib.pop(attribute)
        key = field, profile
        if key in templates and ET.tostring(templates[key][0]) != ET.tostring(template):
            raise ValueError("Each field/profile must retain one vehicle, road, and waypoint template.")
        offset = float(row["free_flow_field_offset_s"])
        if not math.isfinite(offset) or not 0 <= offset < 150:
            raise ValueError("The initialization time guess needs a finite free-flow offset below 150 seconds.")
        if key in templates and templates[key][1] != offset:
            raise ValueError("Each field/profile must retain one free-flow offset.")
        templates[key] = template, offset, row
        row.update(population_role="main", target_begin_s=3600 + 900 * (row["field_row"] // 13),
                   target_end_s=4500 + 900 * (row["field_row"] // 13))
    expected_keys = {(field, begin, begin + 900) for field in weights for begin in range(0, 3600, 900)}
    keys = [(r["detector_id"], float(r["begin"]), float(r["end"])) for r in count_rows]
    if len(set(keys)) != len(keys) or set(keys) != expected_keys:
        raise ValueError("Supply every preceding-hour field/bin exactly once.")
    allocations = []
    for source in count_rows:
        field, begin, end = source["detector_id"], float(source["begin"]), float(source["end"])
        value = float(source["expected_total"])
        if (not math.isfinite(value) or value < 0 or not value.is_integer()
                or source.get("quality_status") != "complete"):
            raise ValueError("Use complete nonnegative integer source counts. Do not fill missing counts with zero.")
        total, profiles = int(value), sorted(weights[field])
        denominator = sum(weights[field].values())
        counts = {p: total * weights[field][p] // denominator for p in profiles}
        order = sorted(profiles, key=lambda p: (-(total * weights[field][p] % denominator), p))
        for profile in order[:total - sum(counts.values())]:
            counts[profile] += 1
        for profile in profiles:
            count = counts[profile]
            template, offset, main_row = templates[field, profile]
            allocations.append({"detector_id": field, "begin": begin, "end": end, "profile_index": profile,
                "vehicles": count, "main_prior_weight": weights[field][profile], "main_prior_denominator": denominator})
            for n in range(count):
                identity = f"initialization_{field}_b{int(begin // 900)}_p{profile}_v{n}"
                if identity in vehicles:
                    raise ValueError("Initialization vehicle identifiers must be new.")
                vehicle = deepcopy(template)
                depart = begin + 150 + (n + .5) * 550 / count - offset
                vehicle.set("id", identity)
                vehicle.set("depart", f"{depart:.9f}")
                vehicles[identity] = vehicle
                demand.append(vehicle)
                new_row = {**main_row, "vehicle_id": identity, "population_role": "initialization",
                    "field_row": None, "target_begin_s": begin, "target_end_s": end, "depart_s": depart}
                intent["vehicles"].append(new_row)
    all_vehicles = demand.findall("vehicle")
    for vehicle in all_vehicles:
        demand.remove(vehicle)
    demand.extend(sorted(all_vehicles, key=lambda v: (float(v.get("depart")), v.get("id"))))
    return demand, intent, allocations


def prepare_hamburg_initialization(*, case_dir, counts_file, output_dir):
    case, counts, out = Path(case_dir).resolve(strict=True), Path(counts_file).resolve(strict=True), Path(output_dir).resolve()
    if out.exists() or case.is_relative_to(out) or counts.is_relative_to(out):
        raise ValueError("Use a new initialization directory separate from its sources.")
    names = ("network.net.xml", "demand.rou.xml", "field-loops.add.xml", "expected-counts.csv",
             "control.add.xml", "lane-intentions.json", "scenario.json")
    raw, sources = {}, {}
    for name in names:
        path = case / "inputs" / name
        if not path.is_file():
            path = path.with_name(name + ".gz")
        sources[str(path)] = file_sha256(path)
        raw[name] = gzip.decompress(path.read_bytes()) if path.suffix == ".gz" else path.read_bytes()
    source_routes = ET.fromstring(raw["demand.rou.xml"])
    rows = read_csv_rows(counts)
    if len(source_routes.findall("vehicle")) != 6687 or len(rows) != 52 or sum(int(r["expected_total"]) for r in rows) != 1953:
        raise ValueError("Use the declared Hamburg main population and the complete 1953-passage initialization source.")
    demand, intentions, allocations = add_initialization_population(source_routes, json.loads(raw["lane-intentions.json"]), rows)
    source_vehicles = {v.get("id"): ET.tostring(v) for v in source_routes.findall("vehicle")}
    assert all(ET.tostring(v) == source_vehicles[v.get("id")] for v in demand.findall("vehicle") if v.get("id") in source_vehicles)
    out.mkdir(parents=True)
    for name, data in raw.items():
        (out / name).write_bytes(data)
    (out / "initialization-counts.csv").write_bytes(counts.read_bytes())
    ET.ElementTree(demand).write(out / "demand.rou.xml", encoding="utf-8", xml_declaration=True)
    write_json_atomic(out / "lane-intentions.json", intentions)
    write_json_atomic(out / "initialization-allocation.json", {"rows": allocations})
    scenario = json.loads(raw["scenario.json"])
    scenario.update(vehicles=8640, main_vehicles=6687, initialization_vehicles=1953,
        vehicle_km=None, all_integer_rows_exact=None, demand_sha256=file_sha256(out / "demand.rou.xml"),
        native_snapshot_directory=None,
        demand_prior="One field per model vehicle. Add 1953 initialization vehicles using inherited main-window field/profile shares. Retain all 6687 main vehicles unchanged. No inferred real distinct-vehicle total or measured OD.",
        time_prior="Main requests unchanged. Initialization rear-clear guesses span 150–700 s of each 900 s bin minus profile free-flow offsets. This is an initial guess, not native count fit.",
        initialization_state="Empty network at local 06:30. Preceding-hour demand builds a candidate state at 07:30. Earlier traffic and real stock remain unknown.")
    write_json_atomic(out / "scenario.json", scenario)
    sources[str(counts)] = file_sha256(counts)
    report = {"status": "prepared", "formal_result": False, "vehicles": 8640, "main_vehicles_unchanged": 6687,
        "initialization_vehicles": 1953, "main_target_rows": 130, "initialization_target_rows": 52,
        "profile_allocation": "Largest remainder of each field's original main-window profile frequencies. Ties use ascending profile index.",
        "input_sha256": {name: file_sha256(out / name) for name in names},
        "source_sha256": sources, "source_files_unchanged": all(file_sha256(Path(p)) == h for p, h in sources.items()),
        "claim_boundary": "Source-informed model population, not real distinct vehicles or identified OD. No native fit or useful initial state is established."}
    write_json_atomic(out / "initialization-proposal.json", report)
    return report


def run_hamburg_initialization(*, prepared_dir, output_dir, sumo_binary):
    """Measure only the preceding hour while retaining the full future demand file."""
    p, out = Path(prepared_dir).resolve(strict=True), Path(output_dir).resolve()
    run = run_point_detector_demand(net_file=p / "network.net.xml", route_file=p / "demand.rou.xml",
        e1_additional_file=p / "field-loops.add.xml", expected_counts_csv=p / "initialization-counts.csv",
        output_dir=out, end_s=3600, sumo_binary=str(sumo_binary), timeout_seconds=900,
        signal_program_file=p / "control.add.xml", lane_intentions_file=p / "lane-intentions.json",
        state_times_s=(3599.9,))
    monitor = run.get("stall_monitor")
    if monitor is None or not monitor["native_version"][1].endswith("1.27.1"):
        raise ValueError("Use SUMO 1.27.1. Retain the completed native records.")
    passages = read_contributing_e1_passages(instant_file=out / "instant-e1.xml", periodic_file=out / "field-counts.xml",
        detector_ids=run["instant_to_periodic_ids"], step_length=.1)
    write_json_atomic(out / "passage-reconciliation.json", passages)
    comparisons = read_csv_rows(out / "field-comparison.csv")
    intentions = json.loads((p / "lane-intentions.json").read_bytes())["vehicles"]
    roles = {r["vehicle_id"]: r["population_role"] for r in intentions}
    trips = ET.parse(out / "tripinfo.xml").getroot().findall("tripinfo")
    vehicles = ET.parse(p / "demand.rou.xml").getroot().findall("vehicle")
    requested = {v.get("id") for v in vehicles if float(v.get("depart")) < 3600}
    complete_counts = all(r["measured_nVehContrib"] not in (None, "", "None") for r in comparisons)
    residuals = [int(r["diff_nVehContrib_minus_expected"]) for r in comparisons] if complete_counts else []
    completion = run.get("completion", {})
    report = {"status": "measured" if complete_counts and passages["reconciled"] else "incomplete",
        "formal_result": False, "main_window_run": False, "exact_calibration_established": False,
        "native_end_s": monitor["simulation_time_s"], "native_monitor_status": monitor["status"],
        "native_version": monitor["native_version"], "completion": completion,
        "vehicles_requested_before_end": len(requested), "future_main_vehicles_retained": len(vehicles) - len(requested),
        "requested_but_not_inserted": len(requested - {t.get("id") for t in trips}),
        "native_passages_by_population": dict(Counter(roles[r["vehicle_id"]] for r in passages["passages"])),
        "native_safe_prefix": completion.get("collisions") == completion.get("teleports") == 0,
        "native_E1_reconciled": passages["reconciled"], "available_initialization_bins": sum(r["measured_nVehContrib"] not in (None, "", "None") for r in comparisons),
        "exact_initialization_bins": sum(r["diff_nVehContrib_minus_expected"] == "0" for r in comparisons),
        "diagnostic_MAE": sum(map(abs, residuals)) / len(residuals) if residuals else None,
        "diagnostic_max_abs_error": max(map(abs, residuals), default=None),
        "state_snapshots": run["state_snapshots"], "restart_validated": False,
        "claim_boundary": "Initialization-hour diagnostic only. Generic full-demand completion is false because future main vehicles are not due. The saved native state does not serialize lane-hold controller state and is not a validated restart. Main 130/40 targets remain untested."}
    write_json_atomic(out / "initialization-check.json", report)
    return report
