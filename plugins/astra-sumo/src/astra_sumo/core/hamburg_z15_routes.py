"""Test existing Z15 routes through K31/K2 while retaining the full count case."""

from collections import Counter, defaultdict
from copy import deepcopy
import gzip
import json
from pathlib import Path
import xml.etree.ElementTree as ET

from .artifact_io import write_json_atomic
from .candidate_contracts import file_sha256
from .demand_route_pool import junction_visit_audit
from .detector_demand import read_net, write_csv
from .detector_demand_replay import run_point_detector_demand
from .hamburg_departure_probe import single_pass_structure
from .local_network_conversion import loaded_turn_permissions
from .route_sensor_matrix import read_contributing_e1_passages


FIELD = "hh_0119_Z_15_29375"


def prepare_z15_k2_routes(*, case_dir, profiles_file, bindings_file, output_dir):
    case, profile_path, binding_path, out = [Path(p).resolve() for p in (case_dir, profiles_file, bindings_file, output_dir)]
    if out.exists() or any(p.is_relative_to(out) for p in (case, profile_path, binding_path)):
        raise ValueError("Use a new route-candidate directory separate from its sources.")
    names = ("network.net.xml", "demand.rou.xml", "field-loops.add.xml", "expected-counts.csv",
             "control.add.xml", "lane-intentions.json", "scenario.json", "initialization-counts.csv")
    raw, sources = {}, {}
    for name in names:
        path = case / "inputs" / name
        if not path.is_file():
            path = path.with_name(name + ".gz")
        sources[str(path)] = file_sha256(path)
        raw[name] = gzip.decompress(path.read_bytes()) if path.suffix == ".gz" else path.read_bytes()
    profiles = json.loads(gzip.decompress(profile_path.read_bytes()))["profiles"]
    bindings = {tuple(r["physical_key"]): r for r in json.loads(gzip.decompress(binding_path.read_bytes()))["rows"]}
    network, demand = ET.fromstring(raw["network.net.xml"]), ET.fromstring(raw["demand.rou.xml"])
    intentions = json.loads(raw["lane-intentions.json"])
    vehicles = {v.get("id"): v for v in demand.findall("vehicle")}
    assigned = {r["vehicle_id"]: r for r in intentions["vehicles"]}
    if len(vehicles) != 8640 or vehicles.keys() != assigned.keys():
        raise ValueError("Retain all 8640 vehicles and their declared intentions.")
    boundaries_path = profile_path.parent / "boundary-declarations.json"
    boundaries = json.loads(boundaries_path.read_bytes())["zones"]
    entries = {e for z in boundaries for e in z["entry_edges"]}
    edges, connections = read_net(case / "inputs/network.net.xml")
    permissions = loaded_turn_permissions(network)
    native = {(c.get("from"), int(c.get("fromLane")), c.get("to"), int(c.get("toLane"))): c for c in network.findall("connection")}
    owners = {j.get("id"): j.get("id").rsplit("_part", 1)[0] for j in network.findall("junction") if j.get("id", "").startswith("LSA")}
    audits = {}
    for index, entry in ((89, "189764301"), (252, "61578529#1")):
        profile = profiles[index]
        if profile["source_edge"] != entry or entry not in entries or profile["field_mask"] != 8:
            raise ValueError("Use the existing one-field K31/K2 boundary routes.")
        audit = junction_visit_audit(profile["edges"], edges, connections, owners)
        turns = [(a, profile["exit_lanes"][i], b, profile["entry_lanes"][i + 1]) for i, (a, b) in enumerate(zip(profile["edges"], profile["edges"][1:]))]
        if (not audit["allowed"] or any("passenger" not in permissions.get(k, ()) or native[k].get("dir") in {"t", "T"} for k in turns)
                or any(network.find(f"edge[@id='{e}']/param[@key='access'][@value='customers']") is not None for e in profile["edges"])):
            raise ValueError("Retain passenger access, without customer roads, tours or turnarounds.")
        official = [bindings.get(k) for k in turns if native[k].get("tl", "").startswith("LSA")]
        if any(r is None or not r["groups"] for r in official):
            raise ValueError("Retain source-supported planned official movements.")
        groups = {tuple(g) for r in official for g in r["groups"]}
        if ("200", "K8") in groups or not {("200", "K31"), ("200", "K2")} <= groups:
            raise ValueError("The selected routes must use K31 and K2 and avoid K8.")
        audits[str(index)] = {"entry": entry, "junction_audit": audit, "official_groups": sorted(groups)}
    bins = defaultdict(list)
    for row in intentions["vehicles"]:
        if row["field_id"] == FIELD:
            if row["profile_index"] not in {240, 246} or vehicles[row["vehicle_id"]].find("route").get("edges").split() != profiles[row["profile_index"]]["edges"]:
                raise ValueError("Use the frozen Z15 source routes before applying this change.")
            bins[row["population_role"], row["target_begin_s"]].append(row)
    roles = Counter(r["population_role"] for rows in bins.values() for r in rows)
    if roles != {"main": 896, "initialization": 309}:
        raise ValueError("Retain all 1205 main and initialization Z15 vehicles.")
    allocations = []
    for (role, begin), rows in sorted(bins.items()):
        rows.sort(key=lambda r: (float(vehicles[r["vehicle_id"]].get("depart")), r["vehicle_id"]))
        first = (2 * len(rows) + 1) // 3
        second = len(rows) - first
        counts = Counter()
        for i, row in enumerate(rows):
            index = 252 if (i + 1) * second // len(rows) > i * second // len(rows) else 89
            vehicles[row["vehicle_id"]].find("route").set("edges", " ".join(profiles[index]["edges"]))
            row.update(profile_index=index, free_flow_field_offset_s=profiles[index]["field_offsets_s"]["3"])
            counts[index] += 1
        allocations.append({"population_role": role, "target_begin_s": begin, "profile_89_vehicles": counts[89], "profile_252_vehicles": counts[252]})
    structure = single_pass_structure(network, demand, ET.fromstring(raw["field-loops.add.xml"]))
    if not structure["structural_conditions_satisfied"]:
        raise ValueError("Retain the complete one-field, single-pass route structure.")
    loads = Counter((v.find("route").get("edges").split()[0], int(float(v.get("depart")) // 900)) for v in vehicles.values())
    if any(n > {"189764301": 100, "61578529#1": 50}[e] for (e, b), n in loads.items() if e in {"189764301", "61578529#1"}):
        raise ValueError("Retain the original entry priors for the replacement roads.")
    out.mkdir(parents=True)
    for name, data in raw.items():
        (out / name).write_bytes(data)
    ET.ElementTree(demand).write(out / "demand.rou.xml", encoding="utf-8", xml_declaration=True)
    write_json_atomic(out / "lane-intentions.json", intentions)
    scenario = json.loads(raw["scenario.json"])
    scenario.update(demand_sha256=file_sha256(out / "demand.rou.xml"),
        demand_prior="Keep all 8640 model vehicles. Allocate Z15 requests to existing K31/K2 profiles 89 and 252 in a declared 2:1 motor-lane prior per target bin. No measured OD is inferred.",
        time_prior="All request times and target field bins unchanged. No free-flow time correction after changing the Z15 routes.")
    write_json_atomic(out / "scenario.json", scenario)
    sources.update({str(p): file_sha256(p) for p in (profile_path, binding_path, boundaries_path)})
    report = {"status": "prepared", "formal_result": False, "changed_field": FIELD, "changed_by_population": dict(roles),
        "allocation_prior": "Largest remainder 2:1 by the existing two versus one passenger entry lanes. Spread both routes across each bin's unchanged requests.",
        "allocations": allocations, "route_audits": audits, "single_pass_structure": structure,
        "source_sha256": sources, "input_sha256": {n: file_sha256(out / n) for n in names},
        "sources_unchanged": all(file_sha256(Path(p)) == h for p, h in sources.items()),
        "claim_boundary": "Declared route prior only. Native lane realization, count fit, and capacity require measurement. All original 130 main field bins and 52 initialization bins remain intact."}
    write_json_atomic(out / "z15-k2-proposal.json", report)
    return {k: v for k, v in report.items() if k not in {"route_audits", "source_sha256", "allocations"}}


def probe_z15_k2_routes(*, prepared_dir, output_dir, sumo_binary):
    p, out = Path(prepared_dir).resolve(strict=True), Path(output_dir).resolve()
    if out.exists() or p.is_relative_to(out):
        raise ValueError("Use a new probe directory separate from the full case.")
    full = ET.parse(p / "demand.rou.xml").getroot()
    intentions = json.loads((p / "lane-intentions.json").read_bytes())
    selected = {r["profile_index"]: r for r in intentions["vehicles"] if r["field_id"] == FIELD}
    by_id = {v.get("id"): v for v in full.findall("vehicle")}
    demand = ET.Element("routes")
    demand.extend(deepcopy(full.findall("vType")))
    rows = []
    for i in range(20):
        row = deepcopy(selected[(89, 252)[i % 2]])
        vehicle = deepcopy(by_id[row["vehicle_id"]])
        identity, depart = f"z15_k2_probe_{i}", 100 + i * 3
        vehicle.set("id", identity)
        vehicle.set("depart", str(depart))
        demand.append(vehicle)
        row.update(vehicle_id=identity, depart_s=depart, population_role="artificial_probe", field_row=None, target_begin_s=0, target_end_s=900)
        rows.append(row)
    out.mkdir(parents=True)
    ET.ElementTree(demand).write(out / "probe-demand.rou.xml", encoding="utf-8", xml_declaration=True)
    write_json_atomic(out / "probe-intentions.json", {"vehicles": rows})
    expected = [{"detector_id": r.get("id"), "begin": 0, "end": 900, "expected_total": 20 if r.get("id") == FIELD else 0}
                for r in ET.parse(p / "field-loops.add.xml").getroot()]
    write_csv(out / "probe-counts.csv", expected, list(expected[0]))
    run = run_point_detector_demand(net_file=p / "network.net.xml", route_file=out / "probe-demand.rou.xml",
        e1_additional_file=p / "field-loops.add.xml", expected_counts_csv=out / "probe-counts.csv",
        signal_program_file=p / "control.add.xml", lane_intentions_file=out / "probe-intentions.json",
        output_dir=out / "native", end_s=900, sumo_binary=str(sumo_binary), timeout_seconds=240)
    passage = read_contributing_e1_passages(instant_file=out / "native/instant-e1.xml", periodic_file=out / "native/field-counts.xml",
        detector_ids=run["instant_to_periodic_ids"], step_length=.1)
    write_json_atomic(out / "passage-reconciliation.json", passage)
    version = run["stall_monitor"]["native_version"][1]
    counts = Counter((r["vehicle_id"], r["detector_id"]) for r in passage["passages"])
    passed = run["healthy"] and run["exact_count_fit"] and passage["reconciled"] and version.endswith("1.27.1") and counts == {(r["vehicle_id"], FIELD): 1 for r in rows}
    report = {"passed": passed, "formal_result": False, "artificial_vehicles": 20, "vehicles_per_route": 10,
        "native_version": version, "completion": run.get("completion"), "native_E1_reconciled": passage["reconciled"],
        "claim_boundary": "Artificial route check only. The 130 real main bins and 52 initialization bins remain unchanged. No full-window calibration, natural route choice or capacity is established."}
    write_json_atomic(out / "probe-check.json", report)
    return report
