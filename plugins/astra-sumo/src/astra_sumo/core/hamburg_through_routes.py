"""Measure routes crossing different observed roads without repeating a detector."""

from collections import Counter, defaultdict
from copy import deepcopy
import gzip
import json
from pathlib import Path
import xml.etree.ElementTree as ET

from .artifact_io import write_json_atomic
from .candidate_contracts import file_sha256
from .demand_route_pool import junction_visit_audit
from .detector_demand import write_csv, read_net
from .detector_demand_replay import run_point_detector_demand
from .hamburg_departure_probe import single_pass_structure
from .local_network_conversion import loaded_turn_permissions
from .osm_access import _permission_set
from .route_sensor_matrix import read_contributing_e1_passages


THROUGH_PROFILES = {100: 106, 176: 182, 177: 183}


def prepare_hamburg_through_prior(*, repository, case_dir, probe_dir, output_dir):
    root, case, probe, out = [Path(p).resolve() for p in (repository, case_dir, probe_dir, output_dir)]
    if out.exists() or any(p.is_relative_to(out) for p in (case, probe)):
        raise ValueError("Use a new through-prior directory separate from its evidence.")
    checked = json.loads((probe / "probe-check.json").read_bytes())
    if not checked["sources_unchanged"] or any(
        not r["traffic_complete"] or not r["declared_signature_matched"] or not r["E1_reconciled"]
        or not r["assigned_field_passed_once"] or r["other_complete_fields"] or r["repeated_same_field_passages"]
        for r in (checked["results"][name] for name in ("baseline", "through"))
    ):
        raise ValueError("Retain the verified single-field through response and its unchanged source evidence.")
    expected_sources = {p.replace("\\", "/").rsplit("/", 1)[-1]: h for p, h in checked["source_sha256"].items()}
    if len(expected_sources) != len(checked["source_sha256"]):
        raise ValueError("Use unambiguous source names in the paired-probe evidence.")
    names = ("network.net.xml", "demand.rou.xml", "field-loops.add.xml", "expected-counts.csv", "control.add.xml", "lane-intentions.json", "scenario.json", "initialization-counts.csv")
    raw, sources = {}, {str(probe / "probe-check.json"): file_sha256(probe / "probe-check.json")}
    for name in names:
        p = case / "inputs" / name
        if not p.exists():
            p = p.with_name(p.name + ".gz")
        sources[str(p)] = file_sha256(p)
        if name != "scenario.json" and expected_sources.get(p.name) != sources[str(p)]:
            raise ValueError("The candidate inputs must match the paired-probe source hashes.")
        raw[name] = gzip.decompress(p.read_bytes()) if p.suffix == ".gz" else p.read_bytes()
    base = root / "benchmarks/hamburg_ring1_m1_2_demand_pool_20261021_v1/base"
    profile_file = base / "ideal-lane-profiles.json.gz"
    binding_file = root / "benchmarks/hamburg_ring1_m2_control_20261021_v1/link-bindings.json.gz"
    if any(expected_sources.get(p.name) != file_sha256(p) for p in (profile_file, binding_file)):
        raise ValueError("Retain the paired-probe profile and movement-binding sources.")
    profiles = json.loads(gzip.decompress(profile_file.read_bytes()))["profiles"]
    # The exploratory half split preserves the previous aggregate major/residential share.
    demand = ET.fromstring(raw["demand.rou.xml"])
    intentions = json.loads(raw["lane-intentions.json"])
    vehicles = {v.get("id"): v for v in demand.findall("vehicle")}
    bins = defaultdict(list)
    for row in intentions["vehicles"]:
        if row["profile_index"] in THROUGH_PROFILES:
            if vehicles[row["vehicle_id"]].find("route").get("edges").split() != profiles[row["profile_index"]]["edges"]:
                raise ValueError("Retain the tested original major-destination route for each selected vehicle.")
            bins[row["population_role"], row["target_begin_s"]].append(row)
    selected = Counter(r["population_role"] for rows in bins.values() for r in rows)
    if len(vehicles) != 8640 or selected != {"initialization": 851, "main": 2257}:
        raise ValueError("Use the complete 8640-vehicle primary-exit case.")
    allocations, changed = [], Counter()
    for (role, begin), rows in sorted(bins.items()):
        rows.sort(key=lambda r: (float(vehicles[r["vehicle_id"]].get("depart")), r["vehicle_id"]))
        through = len(rows) // 2
        for i, row in enumerate(rows):
            if (i + 1) * through // len(rows) > i * through // len(rows):
                index = THROUGH_PROFILES[row["profile_index"]]
                vehicles[row["vehicle_id"]].find("route").set("edges", " ".join(profiles[index]["edges"]))
                row["profile_index"] = index
                changed[role] += 1
        allocations.append({"population_role": role, "target_begin_s": begin, "existing_major_destination": len(rows) - through, "through_destination": through})
    structure = single_pass_structure(ET.fromstring(raw["network.net.xml"]), demand, ET.fromstring(raw["field-loops.add.xml"]))
    if not structure["structural_conditions_satisfied"]:
        raise ValueError("Retain the conditional single-complete-field structure under the route-exit criterion.")
    out.mkdir(parents=True)
    for name, data in raw.items():
        (out / name).write_bytes(data)
    ET.ElementTree(demand).write(out / "demand.rou.xml", encoding="utf-8", xml_declaration=True)
    write_json_atomic(out / "lane-intentions.json", intentions)
    scenario = json.loads(raw["scenario.json"])
    scenario.update(demand_sha256=file_sha256(out / "demand.rou.xml"), profiles_used=len({r["profile_index"] for r in intentions["vehicles"]}),
        demand_prior="Keep all requests, times and the previous major/residential share. Explore an equal split between the two existing major Z12/Z13 outlets. Their two and three passenger lanes do not identify destination shares or capacity. Different observed roads are allowed under the conditional route-exit bound and native probe. No measured OD is inferred.")
    write_json_atomic(out / "scenario.json", scenario)
    sources[str(profile_file)] = file_sha256(profile_file)
    network = ET.fromstring(raw["network.net.xml"])
    outlet_facts = {}
    for identity in ("142944538", "142944477"):
        edge = network.find(f"edge[@id='{identity}']")
        outlet_facts[identity] = {"road_type": edge.get("type"), "passenger_lanes": sum(
            "passenger" in _permission_set(lane.attrib, network_version=network.get("version")) for lane in edge.findall("lane"))}
    report = {"status": "prepared", "formal_result": False, "selected_by_population": dict(selected),
        "changed_by_population": dict(changed), "major_destination_split": "1:1; odd vehicle retained on original outlet",
        "outlet_facts": outlet_facts, "split_is_exploratory_not_measured": True,
        "allocations": allocations, "structure": structure, "source_sha256": sources,
        "input_sha256": {n: file_sha256(out / n) for n in names},
        "claim_boundary": "Existing routes and source-supported turns only. A native artificial probe and conditional coverage bound do not establish larger-demand validity or count calibration. All real 130/40 targets remain unchanged."}
    write_json_atomic(out / "through-prior.json", report)
    return {k: v for k, v in report.items() if k not in {"allocations", "source_sha256"}}


def probe_hamburg_through_routes(*, repository, case_dir, output_dir, sumo_binary):
    root, case, out = [Path(v).resolve() for v in (repository, case_dir, output_dir)]
    if out.exists() or case.is_relative_to(out):
        raise ValueError("Use a new through-route probe directory separate from the source.")
    sources = {}

    def read(folder, name):
        p = folder / name
        if not p.exists():
            p = p.with_name(p.name + ".gz")
        sources[str(p)] = file_sha256(p)
        raw = p.read_bytes()
        return gzip.decompress(raw) if p.suffix == ".gz" else raw

    base = root / "benchmarks/hamburg_ring1_m1_2_demand_pool_20261021_v1/base"
    profiles = json.loads(read(base, "ideal-lane-profiles.json"))["profiles"]
    network_raw = read(case / "inputs", "network.net.xml")
    network = ET.fromstring(network_raw)
    full = ET.fromstring(read(case / "inputs", "demand.rou.xml"))
    intentions = json.loads(read(case / "inputs", "lane-intentions.json"))["vehicles"]
    loops_raw = read(case / "inputs", "field-loops.add.xml")
    loops = ET.fromstring(loops_raw)
    control = read(case / "inputs", "control.add.xml")
    main_targets = read(case / "inputs", "expected-counts.csv")
    initial_targets = read(case / "inputs", "initialization-counts.csv")
    edges, connections = read_net(case / "inputs/network.net.xml")
    permissions = loaded_turn_permissions(network)
    native = {(c.get("from"), int(c.get("fromLane")), c.get("to"), int(c.get("toLane"))): c for c in network.findall("connection")}
    owners = {j.get("id"): j.get("id").rsplit("_part", 1)[0] for j in network.findall("junction") if j.get("id", "").startswith("LSA")}
    bindings = {tuple(r["physical_key"]): r for r in json.loads(read(root / "benchmarks/hamburg_ring1_m2_control_20261021_v1", "link-bindings.json"))["rows"]}
    audits = {}
    for old_index, new_index in THROUGH_PROFILES.items():
        old, new = profiles[old_index], profiles[new_index]
        if old["field_mask"] != new["field_mask"] or old["field_offsets_s"] != new["field_offsets_s"] or old["source_edge"] != new["source_edge"]:
            raise ValueError("Retain the declared first field and boundary entry.")
        audit = junction_visit_audit(new["edges"], edges, connections, owners)
        turns = [(a, new["exit_lanes"][i], b, new["entry_lanes"][i + 1]) for i, (a, b) in enumerate(zip(new["edges"], new["edges"][1:]))]
        if (not audit["allowed"] or any("passenger" not in permissions.get(k, ()) or native[k].get("dir") in {"t", "T"} for k in turns)
                or any(not bindings.get(k, {}).get("groups") for k in turns if native[k].get("tl", "").startswith("LSA"))):
            raise ValueError("Keep source-supported legal routes without tours or turnarounds.")
        audits[str(new_index)] = {"junction_audit": audit, "observed_roads": sorted(set(new["edges"]) & {r.get("lane").rsplit("_", 1)[0] for r in loops})}
    groups = defaultdict(list)
    for row in intentions:
        if row["population_role"] == "initialization" and row["profile_index"] in THROUGH_PROFILES:
            groups[row["profile_index"]].append(row)
    by_id = {v.get("id"): v for v in full.findall("vehicle")}
    for group in groups.values():
        group.sort(key=lambda r: float(by_id[r["vehicle_id"]].get("depart")))
    selected = [groups[index][i // 3] for i, index in enumerate([100, 176, 177] * 14)][:40]
    out.mkdir(parents=True)
    for name, raw in [("network.net.xml", network_raw), ("field-loops.add.xml", loops_raw),
                      ("control.add.xml", control), ("original-main-targets.csv", main_targets), ("original-initialization-targets.csv", initial_targets)]:
        (out / name).write_bytes(raw)
    results = {}
    for label in ("baseline", "through"):
        p = out / label
        p.mkdir()
        demand, rows = ET.Element("routes"), []
        demand.extend(deepcopy(full.findall("vType")))
        for i, source in enumerate(selected):
            row = deepcopy(source)
            vehicle = deepcopy(by_id[row["vehicle_id"]])
            if label == "through":
                index = THROUGH_PROFILES[row["profile_index"]]
                vehicle.find("route").set("edges", " ".join(profiles[index]["edges"]))
                row["profile_index"] = index
            identity, depart = f"through_probe_{i}", 100 + i * 2
            vehicle.set("id", identity)
            vehicle.set("depart", str(depart))
            demand.append(vehicle)
            row.update(vehicle_id=identity, depart_s=depart, population_role="artificial_probe", field_row=None, target_begin_s=0, target_end_s=900)
            rows.append(row)
        ET.ElementTree(demand).write(p / "demand.rou.xml", encoding="utf-8", xml_declaration=True)
        write_json_atomic(p / "intentions.json", {"vehicles": rows})
        expected = Counter(r["field_id"] for r in rows)
        target_rows = [{"detector_id": r.get("id"), "begin": 0, "end": 900, "expected_total": expected[r.get("id")]} for r in loops]
        write_csv(p / "artificial-targets.csv", target_rows, list(target_rows[0]))
        run = run_point_detector_demand(net_file=case / "inputs/network.net.xml", route_file=p / "demand.rou.xml", e1_additional_file=out / "field-loops.add.xml",
            expected_counts_csv=p / "artificial-targets.csv", signal_program_file=out / "control.add.xml", lane_intentions_file=p / "intentions.json",
            output_dir=p / "native", end_s=900, sumo_binary=str(sumo_binary), timeout_seconds=240)
        passage = read_contributing_e1_passages(instant_file=p / "native/instant-e1.xml", periodic_file=p / "native/field-counts.xml", detector_ids=run["instant_to_periodic_ids"], step_length=.1)
        write_json_atomic(p / "passage-reconciliation.json", passage)
        assignments = {r["vehicle_id"]: r["field_id"] for r in rows}
        counts = Counter((r["vehicle_id"], r["detector_id"]) for r in passage["passages"])
        version = run["stall_monitor"]["native_version"][1]
        results[label] = {"traffic_complete": bool(run.get("healthy")) and version.endswith("1.27.1"),
            "declared_signature_matched": run["exact_count_fit"], "completion": run.get("completion"), "native_version": version,
            "E1_reconciled": passage["reconciled"], "assigned_field_passed_once": all(counts[i, f] == 1 for i, f in assignments.items()),
            "other_complete_fields": dict(Counter(r["detector_id"] for r in passage["passages"] if r["detector_id"] != assignments[r["vehicle_id"]])),
            "repeated_same_field_passages": sum(max(0, n - 1) for n in counts.values())}
    report = {"formal_result": False, "artificial_vehicles_per_case": 40, "results": results,
        "through_route_audits": audits, "source_sha256": sources,
        "sources_unchanged": all(file_sha256(Path(p)) == h for p, h in sources.items()),
        "claim_boundary": "Paired artificial route-signature check. Different observed roads are not repeated detectors. Real 130/40 targets remain unchanged. A measured additional field would require joint demand accounting, not removal of that field."}
    write_json_atomic(out / "probe-check.json", report)
    return {k: v for k, v in report.items() if k not in {"source_sha256", "through_route_audits"}}
