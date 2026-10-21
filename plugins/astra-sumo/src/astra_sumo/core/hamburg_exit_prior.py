"""Test a declared major-road destination prior for the constrained 119 flows."""

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
from .hamburg_readme_demand import entry_rate_vph
from .local_network_conversion import loaded_turn_permissions
from .osm_access import _permission_set
from .route_sensor_matrix import read_contributing_e1_passages


REPLACEMENTS = {98: 100, 174: 176, 175: 177}
FIELDS = {"hh_0119_Z_12_31546", "hh_0119_Z_13_34018"}


def prepare_major_exit_prior(*, repository, case_dir, output_dir):
    root, case, out = [Path(p).resolve() for p in (repository, case_dir, output_dir)]
    if out.exists() or case.is_relative_to(out):
        raise ValueError("Use a new destination-prior directory separate from its source.")
    base = root / "benchmarks/hamburg_ring1_m1_2_demand_pool_20261021_v1/base"
    profile_file, boundary_file, prior_file = [base / n for n in ("ideal-lane-profiles.json.gz", "boundary-declarations.json", "report.json")]
    binding_file = root / "benchmarks/hamburg_ring1_m2_control_20261021_v1/link-bindings.json.gz"
    sources = {str(p): file_sha256(p) for p in (profile_file, boundary_file, prior_file, binding_file)}
    names = ("network.net.xml", "demand.rou.xml", "field-loops.add.xml", "expected-counts.csv", "control.add.xml", "lane-intentions.json", "scenario.json", "initialization-counts.csv")
    raw = {}
    for name in names:
        p = case / "inputs" / name
        if not p.exists():
            p = p.with_name(p.name + ".gz")
        sources[str(p)] = file_sha256(p)
        raw[name] = gzip.decompress(p.read_bytes()) if p.suffix == ".gz" else p.read_bytes()
    profiles = json.loads(gzip.decompress(profile_file.read_bytes()))["profiles"]
    boundaries = json.loads(boundary_file.read_bytes())["zones"]
    exits = {e for z in boundaries for e in z["exit_edges"]}
    bindings = {tuple(r["physical_key"]): r for r in json.loads(gzip.decompress(binding_file.read_bytes()))["rows"]}
    network, demand = ET.fromstring(raw["network.net.xml"]), ET.fromstring(raw["demand.rou.xml"])
    intentions = json.loads(raw["lane-intentions.json"])
    vehicles = {v.get("id"): v for v in demand.findall("vehicle")}
    edges, connections = read_net(case / "inputs/network.net.xml")
    permissions = loaded_turn_permissions(network)
    native = {(c.get("from"), int(c.get("fromLane")), c.get("to"), int(c.get("toLane"))): c for c in network.findall("connection")}
    owners = {j.get("id"): j.get("id").rsplit("_part", 1)[0] for j in network.findall("junction") if j.get("id", "").startswith("LSA")}
    audits = {}
    for old_index, new_index in REPLACEMENTS.items():
        old, new = profiles[old_index], profiles[new_index]
        if (old["edges"][-1] != "19728446#0" or new["edges"][-1] != "142944538" or not {old["edges"][-1], new["edges"][-1]} <= exits
                or old["edges"][:-1] != new["edges"][:len(old["edges"]) - 1] or old["field_mask"] != new["field_mask"]
                or old["field_offsets_s"] != new["field_offsets_s"]):
            raise ValueError("Retain the existing detector prefix and choose the existing major-road boundary.")
        audit = junction_visit_audit(new["edges"], edges, connections, owners)
        turns = [(a, new["exit_lanes"][i], b, new["entry_lanes"][i + 1]) for i, (a, b) in enumerate(zip(new["edges"], new["edges"][1:]))]
        if (not audit["allowed"] or any("passenger" not in permissions.get(k, ()) or native[k].get("dir") in {"t", "T"} for k in turns)
                or any(network.find(f"edge[@id='{e}']/param[@key='access'][@value='customers']") is not None for e in new["edges"])):
            raise ValueError("Retain passenger access without tours, turnarounds or customer roads.")
        if any(k not in bindings or not bindings[k]["groups"] for k in turns if native[k].get("tl", "").startswith("LSA")):
            raise ValueError("Retain source-supported planned official movements.")
        audits[str(new_index)] = audit
    rates = json.loads(prior_file.read_bytes())["entry_vph_by_highway_assumption"]
    weights = {}
    for e in ("19728446#0", "142944538"):
        edge = network.find(f"edge[@id='{e}']")
        motor_lanes = sum("passenger" in _permission_set(lane.attrib, network_version=network.get("version")) for lane in edge.findall("lane"))
        weights[e] = motor_lanes * entry_rate_vph(edge, {"entry_vph_by_highway": rates})
    if weights != {"19728446#0": 200, "142944538": 1800}:
        raise ValueError("Reassess the declared 9:1 outlet proxy if road facts or grade priors change.")
    bins = defaultdict(list)
    for row in intentions["vehicles"]:
        if row["field_id"] in FIELDS:
            if row["profile_index"] not in REPLACEMENTS or vehicles[row["vehicle_id"]].find("route").get("edges").split() != profiles[row["profile_index"]]["edges"]:
                raise ValueError("Use the original residential-exit plans for these fields.")
            bins[row["population_role"], row["target_begin_s"]].append(row)
    selected_roles = Counter(r["population_role"] for rs in bins.values() for r in rs)
    if len(vehicles) != 8640 or selected_roles != {"main": 2506, "initialization": 946}:
        raise ValueError("Retain the full case and every Z12/Z13 request.")
    allocations, changed = [], Counter()
    for (role, begin), rs in sorted(bins.items()):
        rs.sort(key=lambda r: (float(vehicles[r["vehicle_id"]].get("depart")), r["vehicle_id"]))
        major = (9 * len(rs) + 5) // 10
        for i, row in enumerate(rs):
            if (i + 1) * major // len(rs) > i * major // len(rs):
                index = REPLACEMENTS[row["profile_index"]]
                vehicles[row["vehicle_id"]].find("route").set("edges", " ".join(profiles[index]["edges"]))
                row["profile_index"] = index
                changed[role] += 1
        allocations.append({"population_role": role, "target_begin_s": begin, "primary_destination": major, "residential_destination": len(rs) - major})
    structure = single_pass_structure(network, demand, ET.fromstring(raw["field-loops.add.xml"]))
    if not structure["structural_conditions_satisfied"]:
        raise ValueError("Retain the one-field, single-pass structure.")
    out.mkdir(parents=True)
    for n, data in raw.items():
        (out / n).write_bytes(data)
    ET.ElementTree(demand).write(out / "demand.rou.xml", encoding="utf-8", xml_declaration=True)
    write_json_atomic(out / "lane-intentions.json", intentions)
    scenario = json.loads(raw["scenario.json"])
    scenario.update(demand_sha256=file_sha256(out / "demand.rou.xml"), profiles_used=len({r["profile_index"] for r in intentions["vehicles"]}),
        demand_prior="Keep all 8640 requests and the existing Z15 K31/K2 routes. Apply a declared 9:1 primary/residential destination prior to Z12/Z13 per target bin. No measured OD or capacity is inferred.")
    write_json_atomic(out / "scenario.json", scenario)
    report = {"status": "prepared", "formal_result": False, "selected_by_population": dict(selected_roles), "changed_by_population": dict(changed),
        "outlet_proxy_weights": weights, "allocation_rule": "Largest remainder 9:1 per target bin, ties favor the primary outlet, spread across unchanged requests.",
        "allocations": allocations, "route_audits": audits, "source_sha256": sources,
        "input_sha256": {n: file_sha256(out / n) for n in names}, "sources_unchanged": all(file_sha256(Path(p)) == h for p, h in sources.items()),
        "claim_boundary": "New destination prior reuses existing grade weights as a proxy. It is not measured OD or capacity. Network, lane-change permissions, controls, request times and all 130/40 targets remain unchanged."}
    write_json_atomic(out / "destination-prior.json", report)
    return {k: v for k, v in report.items() if k not in {"route_audits", "source_sha256", "allocations"}}


def probe_major_exit_prior(*, repository, prepared_dir, output_dir, sumo_binary):
    root, p, out = [Path(v).resolve() for v in (repository, prepared_dir, output_dir)]
    if out.exists() or p.is_relative_to(out):
        raise ValueError("Use a new paired-probe directory.")
    profiles = json.loads(gzip.decompress((root / "benchmarks/hamburg_ring1_m1_2_demand_pool_20261021_v1/base/ideal-lane-profiles.json.gz").read_bytes()))["profiles"]
    reverse = {v: k for k, v in REPLACEMENTS.items()}
    full = ET.parse(p / "demand.rou.xml").getroot()
    by_id = {v.get("id"): v for v in full.findall("vehicle")}
    groups = defaultdict(list)
    for row in json.loads((p / "lane-intentions.json").read_bytes())["vehicles"]:
        if row["field_id"] in FIELDS and row["population_role"] == "initialization":
            groups[reverse.get(row["profile_index"], row["profile_index"])].append(row)
    for rs in groups.values():
        rs.sort(key=lambda r: float(by_id[r["vehicle_id"]].get("depart")))
    selected = [groups[old][i // 3] for i, old in enumerate([98, 174, 175] * 14)][:40]
    out.mkdir(parents=True)
    results = {}
    for label in ("baseline", "candidate"):
        d = out / label
        d.mkdir()
        demand = ET.Element("routes")
        demand.extend(deepcopy(full.findall("vType")))
        intentions = []
        for i, source in enumerate(selected):
            row = deepcopy(source)
            vehicle = deepcopy(by_id[row["vehicle_id"]])
            if label == "baseline":
                index = reverse.get(row["profile_index"], row["profile_index"])
                vehicle.find("route").set("edges", " ".join(profiles[index]["edges"]))
                row["profile_index"] = index
            identity, depart = f"destination_probe_{i}", 100 + 2 * i
            vehicle.set("id", identity)
            vehicle.set("depart", str(depart))
            demand.append(vehicle)
            row.update(vehicle_id=identity, depart_s=depart, population_role="artificial_probe", field_row=None, target_begin_s=0, target_end_s=900)
            intentions.append(row)
        ET.ElementTree(demand).write(d / "demand.rou.xml", encoding="utf-8", xml_declaration=True)
        write_json_atomic(d / "intentions.json", {"vehicles": intentions})
        targets = Counter(r["field_id"] for r in intentions)
        expected = [{"detector_id": r.get("id"), "begin": 0, "end": 900, "expected_total": targets[r.get("id")]}
                    for r in ET.parse(p / "field-loops.add.xml").getroot()]
        write_csv(d / "expected.csv", expected, list(expected[0]))
        run = run_point_detector_demand(net_file=p / "network.net.xml", route_file=d / "demand.rou.xml", e1_additional_file=p / "field-loops.add.xml",
            expected_counts_csv=d / "expected.csv", signal_program_file=p / "control.add.xml", lane_intentions_file=d / "intentions.json",
            output_dir=d / "native", end_s=900, sumo_binary=str(sumo_binary), timeout_seconds=240)
        passage = read_contributing_e1_passages(instant_file=d / "native/instant-e1.xml", periodic_file=d / "native/field-counts.xml", detector_ids=run["instant_to_periodic_ids"], step_length=.1)
        write_json_atomic(d / "passage-reconciliation.json", passage)
        version = run["stall_monitor"]["native_version"][1]
        counts = Counter((r["vehicle_id"], r["detector_id"]) for r in passage["passages"])
        passed = run.get("healthy", False) and run["exact_count_fit"] and passage["reconciled"] and version.endswith("1.27.1") and counts == {(r["vehicle_id"], r["field_id"]): 1 for r in intentions}
        k1_exits = []
        if passed:
            for v in ET.parse(d / "native/vehicle-routes.xml").getroot().findall("vehicle"):
                route = v.find("route")
                roads = route.get("edges").split()
                k1_exits.append(float(route.get("exitTimes").split()[roads.index("551303410")]))
        results[label] = {"passed": passed, "completion": run.get("completion"), "native_version": version,
            "K1_first_front_exit_s": min(k1_exits, default=None), "K1_last_front_exit_s": max(k1_exits, default=None)}
    report = {"formal_result": False, "artificial_vehicles_per_case": 40, "results": results,
        "both_route_checks_passed": all(r["passed"] for r in results.values()),
        "claim_boundary": "Matched artificial route check only. Road-front exit times are diagnostics and do not replace native E1 counts or establish field capacity. Real targets remain unchanged."}
    write_json_atomic(out / "probe-check.json", report)
    return report
