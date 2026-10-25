"""Prepare a same-vehicle, distinct-field demand alternative from native route evidence."""

from collections import Counter, defaultdict
from copy import deepcopy
import gzip
import json
from pathlib import Path
import statistics
import xml.etree.ElementTree as ET

import numpy as np
from scipy.optimize import linear_sum_assignment

from .artifact_io import write_json_atomic
from .candidate_contracts import file_sha256
from .demand_route_pool import junction_visit_audit
from .detector_demand import read_csv_rows, read_net, write_csv
from .hamburg_collision_trace import read_trace_source
from .local_network_conversion import loaded_turn_permissions


FIRST = ("hh_0119_Z_12_31546", "hh_0119_Z_13_34018")
SECOND = "hh_0535_Z_4_33055"
PROFILE = {(FIRST[0], "142944318"): 178, (FIRST[1], "142944318"): 179,
           (FIRST[0], "28665128#0"): 180, (FIRST[1], "28665128#0"): 181}


def prepare_shared_population(*, repository, case_dir, probe_dir, output_dir):
    root, case, probe, out = [Path(p).resolve() for p in (repository, case_dir, probe_dir, output_dir)]
    if out.exists() or any(p.is_relative_to(out) or out.is_relative_to(p) for p in (case, probe)):
        raise ValueError("Use a new shared-population directory separate from its evidence.")
    checked = json.loads(read_trace_source(probe, "joint-response-check.json"))
    if (not checked["healthy"] or not checked["observer_complete"] or not checked["native_E1_reconciled"]
            or not checked["vehicle_signatures_matched"] or not checked["first_then_second_order_verified"]
            or checked["native_contributions"] != 80 or checked["native_version"] != [22, "SUMO 1.27.1"]):
        raise ValueError("Retain the verified distinct-field native route response.")
    names = ("network.net.xml", "demand.rou.xml", "field-loops.add.xml", "expected-counts.csv",
             "control.add.xml", "lane-intentions.json", "scenario.json", "initialization-counts.csv")
    raw = {name: read_trace_source(case / "inputs", name) for name in names}
    if any(raw[name] != read_trace_source(probe, name) for name in ("network.net.xml", "control.add.xml")):
        raise ValueError("Retain the tested network and control inputs.")
    profile_file = root / "benchmarks/hamburg_ring1_m1_2_demand_pool_20261021_v1/base/ideal-lane-profiles.json.gz"
    binding_file = root / "benchmarks/hamburg_ring1_m2_control_20261021_v1/link-bindings.json.gz"
    sources = {str(p): file_sha256(p) for p in (profile_file, binding_file, case / "manifest.json", probe / "manifest.json")}
    profiles = json.loads(gzip.decompress(profile_file.read_bytes()))["profiles"]
    network, demand, loops = (ET.fromstring(raw[name]) for name in ("network.net.xml", "demand.rou.xml", "field-loops.add.xml"))
    field_roads = {r.get("id"): r.get("lane").rsplit("_", 1)[0] for r in loops}
    intentions = json.loads(raw["lane-intentions.json"])
    original = {v.get("id"): v for v in demand.findall("vehicle")}
    paths = {identity: v.find("route").get("edges").split() for identity, v in original.items()}
    wanted = {r["vehicle_id"]: r for r in intentions["vehicles"]}
    if len(original) != 8640 or set(original) != set(wanted) or any("target_observations" in r for r in wanted.values()):
        raise ValueError("Use the complete original single-field population as the comparison source.")
    passage = json.loads(read_trace_source(case, "passage-reconciliation.json"))
    observed = {r["vehicle_id"]: r for r in passage["passages"]}
    if (not passage["reconciled"] or len(observed) != len(passage["passages"])
            or any(r["detector_id"] != wanted[v]["field_id"] for v, r in observed.items())):
        raise ValueError("Use a reconciled source with one observed contribution per model vehicle.")
    probe_plan = json.loads(read_trace_source(probe, "proposal.json"))["expected_fields_by_vehicle"]
    probe_vehicles = {v.get("id"): v for v in ET.fromstring(read_trace_source(probe, "demand.rou.xml")).findall("vehicle")}
    probe_history = {v.get("id"): v for v in ET.fromstring(read_trace_source(probe / "native", "vehicle-routes.xml")).findall("vehicle")}
    clearance_offsets = defaultdict(list)
    for identity, fields in probe_plan.items():
        destination = probe_vehicles[identity].find("route").get("edges").split()[-1]
        index = PROFILE[fields[0], destination]
        recorded = probe_history[identity].find("route")
        road_index = recorded.get("edges").split().index(field_roads[SECOND])
        front_exit = float(recorded.get("exitTimes").split()[road_index])
        clearance_offsets[index].append(checked["passage_times_by_vehicle"][identity][SECOND] - front_exit)
    medians = {index: statistics.median(values) for index, values in clearance_offsets.items()}
    edges, links = read_net(case / "inputs/network.net.xml")
    permissions = loaded_turn_permissions(network)
    native = {(c.get("from"), int(c.get("fromLane")), c.get("to"), int(c.get("toLane"))): c for c in network.findall("connection")}
    bindings = {tuple(r["physical_key"]): r for r in json.loads(gzip.decompress(binding_file.read_bytes()))["rows"]}
    owners = {j.get("id"): j.get("id").rsplit("_part", 1)[0] for j in network.findall("junction") if j.get("id", "").startswith("LSA")}
    audits = {}
    for index in PROFILE.values():
        profile = profiles[index]
        turns = [(a, profile["exit_lanes"][i], b, profile["entry_lanes"][i + 1]) for i, (a, b) in enumerate(zip(profile["edges"], profile["edges"][1:]))]
        audit = junction_visit_audit(profile["edges"], edges, links, owners)
        if (not audit["allowed"] or any("passenger" not in permissions.get(k, ()) or native[k].get("dir") in {"t", "T"} for k in turns)
                or any(not bindings.get(k, {}).get("groups") for k in turns if native[k].get("tl", "").startswith("LSA"))
                or any(network.find(f"edge[@id='{edge}']/param[@key='access'][@value='customers']") is not None for edge in profile["edges"])):
            raise ValueError("Retain supported simple passenger routes without turnarounds or customer roads.")
        audits[str(index)] = audit
    source_history = {v.get("id"): v for v in ET.fromstring(read_trace_source(case / "native", "vehicle-routes.xml")).findall("vehicle")}
    first_candidates = [v for v, r in wanted.items() if r["field_id"] in FIRST and v in observed and paths[v][0] == "44532531"]
    road_exit_times = {}
    for identity in first_candidates:
        if identity in source_history and field_roads[SECOND] in paths[identity]:
            history = source_history[identity].find("route")
            if history.get("edges").split() != paths[identity]:
                raise ValueError("Use the recorded original route for the native road-exit timing proxy.")
            road_exit_times[identity] = float(history.get("exitTimes").split()[paths[identity].index(field_roads[SECOND])])
    parents = sorted(road_exit_times)
    children = sorted(v for v, r in wanted.items() if r["field_id"] == SECOND)
    if len(children) != 495:
        raise ValueError("Retain every source Z4 initialization and main observation target.")
    costs = np.full((len(children), len(parents) + len(children)), 1e9)
    lengths = {e.get("id"): float(e.find("lane").get("length")) for e in network.findall("edge") if e.find("lane") is not None}
    distances = {v: sum(lengths[e] for e in path) for v, path in paths.items()}
    shared_distances = {i: sum(lengths[e] for e in profiles[i]["edges"]) for i in PROFILE.values()}
    for i, child in enumerate(children):
        target = wanted[child]
        destination = paths[child][-1]
        nominal = float(original[child].get("depart")) + target["free_flow_field_offset_s"]
        for j, parent in enumerate(parents):
            first = wanted[parent]
            index = PROFILE.get((first["field_id"], destination))
            if index is None or original[parent].get("type") != original[child].get("type"):
                continue
            path = paths[parent]
            shared = profiles[index]["edges"]
            road = field_roads[SECOND]
            if path[:path.index(road) + 1] != shared[:shared.index(road) + 1] or shared_distances[index] > distances[parent] + distances[child]:
                continue
            predicted = road_exit_times[parent] + medians[index]
            if target["target_begin_s"] + 90 <= predicted < target["target_end_s"] - 90 and first["target_begin_s"] <= target["target_begin_s"]:
                costs[i, j] = abs(predicted - nominal) + j * 1e-8
        costs[i, len(parents) + i] = 450
    selected_rows, selected_cols = linear_sum_assignment(costs)
    pairs = []
    for i, j in zip(selected_rows, selected_cols):
        if j >= len(parents) or costs[i, j] >= 450:
            continue
        child, parent = children[i], parents[j]
        destination = paths[child][-1]
        index = PROFILE[wanted[parent]["field_id"], destination]
        pairs.append({"parent_vehicle_id": parent, "removed_model_vehicle_id": child, "shared_profile_index": index,
            "source_first_time_s": observed[parent]["time_s"], "source_second_road_front_exit_s": road_exit_times[parent],
            "sparse_rear_clearance_offset_s": medians[index], "second_time_proxy_s": road_exit_times[parent] + medians[index],
            "normal_road_distance_before_m": distances[parent] + distances[child],
            "normal_road_distance_after_m": shared_distances[index], "pair_cost_s": float(costs[i, j])})

    def target(row, identity):
        keys = ("field_id", "target_begin_s", "target_end_s", "population_role", "field_row", "free_flow_field_offset_s")
        return {k: deepcopy(row[k]) for k in keys if k in row} | {"target_origin_model_vehicle_id": identity}

    declared = {identity: [target(row, identity)] for identity, row in wanted.items()}
    for pair in pairs:
        parent, child, index = (pair[k] for k in ("parent_vehicle_id", "removed_model_vehicle_id", "shared_profile_index"))
        profile = profiles[index]
        original[parent].find("route").set("edges", " ".join(profile["edges"]))
        wanted[parent]["profile_index"] = index
        declared[parent].append(target(wanted[child], child))
        for item in declared[parent]:
            field_index = next(i for i, loop in enumerate(loops) if loop.get("id") == item["field_id"])
            item["free_flow_field_offset_s"] = profile["field_offsets_s"][str(field_index)]
        demand.remove(original[child])
        del declared[child]
    remaining = []
    for row in intentions["vehicles"]:
        identity = row["vehicle_id"]
        if identity not in declared:
            continue
        row["target_observations"] = declared[identity]
        row["expected_field_ids"] = [t["field_id"] for t in declared[identity]]
        row["free_flow_field_offset_s"] = declared[identity][0]["free_flow_field_offset_s"]
        remaining.append(row)
    counter = Counter((t["field_id"], float(t["target_begin_s"]), float(t["target_end_s"]), t["population_role"])
                      for values in declared.values() for t in values)
    ledger = []
    for role, filename in (("initialization", "initialization-counts.csv"), ("main", "expected-counts.csv")):
        for row in read_csv_rows(case / "inputs" / filename):
            key = row["detector_id"], float(row["begin"]), float(row["end"]), role
            value = counter.pop(key, 0)
            if value != int(row["expected_total"]):
                raise ValueError("Keep every observed field and time target in the shared demand ledger.")
            ledger.append({"population_role": role, "detector_id": key[0], "begin": key[1], "end": key[2],
                           "expected_total": int(row["expected_total"]), "declared_target_contributions": value})
    if counter or len(remaining) != 8640 - len(pairs) or sum(map(len, declared.values())) != 8640:
        raise ValueError("Retain the complete target ledger and distinct model vehicle IDs.")
    out.mkdir(parents=True)
    for name, data in raw.items():
        (out / name).write_bytes(data)
    ET.ElementTree(demand).write(out / "demand.rou.xml", encoding="utf-8", xml_declaration=True)
    intentions["vehicles"] = remaining
    intentions["claim_boundary"] = "First-field driver intent only. Each model vehicle declares one or two distinct observation targets; the larger native response remains unverified."
    write_json_atomic(out / "lane-intentions.json", intentions)
    roles = Counter(r["population_role"] for r in remaining)
    scenario = json.loads(raw["scenario.json"])
    scenario.update(vehicles=len(remaining), main_vehicles=roles["main"], initialization_vehicles=roles["initialization"],
        shared_vehicle_pairs=len(pairs), field_observation_totals={"initialization": 1953, "main": 6687},
        profiles_used=len({r["profile_index"] for r in remaining}),
        demand_sha256=file_sha256(out / "demand.rou.xml"),
        demand_prior="Shared-route alternative using only native-tested paths already traversed through the second observed road. Match by the source native road-front exit plus a sparse rear-clearance offset, with unmatched cost 450 s and a 90 s target-edge margin. Normal-road vehicle distance cannot increase. Keep all field/time quotas and unmatched independent requests. The pairing fraction is a model prior, not measured OD.",
        time_prior="Every retained model request time is unchanged. Native road exits and sparse rear-clearance offsets guide target pairing only. The new left-turn and queue response remain unknown.")
    write_json_atomic(out / "scenario.json", scenario)
    write_csv(out / "target-ledger.csv", ledger, list(ledger[0]))
    report = {"formal_result": False, "source_case": str(case), "probe_case": str(probe),
        "model_vehicles_before": 8640, "model_vehicles_after": len(remaining), "shared_pairs": len(pairs),
        "model_roles_after": dict(roles), "field_observation_totals": {"initialization": 1953, "main": 6687},
        "source_observed_first_candidates": len(first_candidates), "source_observed_through_parents": len(parents), "second_target_candidates": len(children),
        "rear_clearance_offsets_s": medians, "unmatched_cost_s": 450, "target_edge_margin_s": 90, "pairings": pairs,
        "route_audits": audits, "source_sha256": sources,
        "sources_unchanged": all(file_sha256(Path(p)) == sha for p, sha in sources.items()),
        "input_sha256": {name: file_sha256(out / name) for name in names},
        "claim_boundary": "The exact 182-row target ledger is not native calibration. Road-front exits are timing proxies, not E1 counts. Model vehicle counts and pairing do not identify true OD or unique vehicles. The road prefix through the second observed road, request time and first waypoint stay fixed; the shared trip adopts the second target's destination. New lane choice, left-turn service, residuals and safety must be remeasured."}
    write_json_atomic(out / "shared-population.json", report)
    return report
