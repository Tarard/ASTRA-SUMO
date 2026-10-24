"""Prepare one declared primary-road entry prior without changing count targets."""

from collections import Counter, defaultdict
import gzip
import json
from pathlib import Path
import xml.etree.ElementTree as ET

from .artifact_io import write_json_atomic
from .candidate_contracts import file_sha256
from .demand_route_pool import junction_visit_audit
from .detector_demand import read_net
from .hamburg_collision_trace import read_trace_source
from .hamburg_departure_probe import single_pass_structure
from .local_network_conversion import loaded_turn_permissions
from .osm_access import _permission_set


REPLACEMENTS = {98: 174, 100: 176, 106: 182}
FIELD = "hh_0119_Z_12_31546"
SHORT, PRIMARY, MERGE = "25866659#1", "44532531", "24483192"


def prepare_primary_entry_prior(*, repository, case_dir, output_dir):
    root, case, out = [Path(p).resolve() for p in (repository, case_dir, output_dir)]
    if out.exists() or case.is_relative_to(out):
        raise ValueError("Use a new entry-prior directory separate from its source.")
    names = ("network.net.xml", "demand.rou.xml", "field-loops.add.xml", "expected-counts.csv",
             "control.add.xml", "lane-intentions.json", "scenario.json", "initialization-counts.csv")
    raw = {name: read_trace_source(case / "inputs", name) for name in names}
    base = root / "benchmarks/hamburg_ring1_m1_2_demand_pool_20261021_v1/base"
    profile_file, boundary_file = base / "ideal-lane-profiles.json.gz", base / "boundary-declarations.json"
    sources = {str(p): file_sha256(p) for p in (profile_file, boundary_file, case / "manifest.json")}
    profiles = json.loads(gzip.decompress(profile_file.read_bytes()))["profiles"]
    zones = json.loads(boundary_file.read_bytes())["zones"]
    entries = {edge: zone for zone in zones for edge in zone["entry_edges"]}
    network, demand = ET.fromstring(raw["network.net.xml"]), ET.fromstring(raw["demand.rou.xml"])
    loops, intentions = ET.fromstring(raw["field-loops.add.xml"]), json.loads(raw["lane-intentions.json"])
    vehicles = {v.get("id"): v for v in demand.findall("vehicle")}
    edges, connections = read_net(case / "inputs/network.net.xml")
    permissions = loaded_turn_permissions(network)
    native = {(c.get("from"), int(c.get("fromLane")), c.get("to"), int(c.get("toLane"))): c for c in network.findall("connection")}
    owners = {j.get("id"): j.get("id").rsplit("_part", 1)[0] for j in network.findall("junction") if j.get("id", "").startswith("LSA")}
    facts, audits = {}, {}
    for identity in (SHORT, PRIMARY):
        edge = network.find(f"edge[@id='{identity}']")
        lanes = [lane for lane in edge.findall("lane") if "passenger" in _permission_set(lane.attrib, network_version=network.get("version"))]
        if entries[identity]["junction_id"] != edge.get("from") or any(e.get("to") == edge.get("from") for e in network.findall("edge")):
            raise ValueError("Retain the existing declared network boundaries.")
        facts[identity] = {"road_type": edge.get("type"), "passenger_lanes": len(lanes),
                           "minimum_lane_length_m": min(float(lane.get("length")) for lane in lanes),
                           "boundary": entries[identity]}
    if facts[SHORT]["passenger_lanes"] != 1 or facts[PRIMARY]["passenger_lanes"] != 3:
        raise ValueError("Reassess the declared 3:1 entry proxy when passenger lanes change.")
    for old_index, new_index in REPLACEMENTS.items():
        old, new = profiles[old_index], profiles[new_index]
        if (old["edges"][0] != SHORT or new["edges"][0] != PRIMARY or old["field_mask"] != new["field_mask"]
                or old["edges"][old["edges"].index(MERGE):] != new["edges"][new["edges"].index(MERGE):]):
            raise ValueError("Keep the same field and identical route after the shared merge.")
        turns = [(a, new["exit_lanes"][i], b, new["entry_lanes"][i + 1]) for i, (a, b) in enumerate(zip(new["edges"], new["edges"][1:]))]
        audit = junction_visit_audit(new["edges"], edges, connections, owners)
        if (not audit["allowed"] or any("passenger" not in permissions.get(key, ()) or native[key].get("dir") in {"t", "T"} for key in turns)
                or any(network.find(f"edge[@id='{edge}']/param[@key='access'][@value='customers']") is not None for edge in new["edges"])):
            raise ValueError("Retain legal passenger routes without tours or turnarounds.")
        audits[str(new_index)] = audit
    groups = defaultdict(list)
    for row in intentions["vehicles"]:
        if row["field_id"] == FIELD:
            index = row["profile_index"]
            if index not in set(REPLACEMENTS) | set(REPLACEMENTS.values()) or vehicles[row["vehicle_id"]].find("route").get("edges").split() != profiles[index]["edges"]:
                raise ValueError("Use the current declared Z12 source routes.")
            groups[row["population_role"], row["target_begin_s"], REPLACEMENTS.get(index, index)].append(row)
    if len(vehicles) != 8640 or sum(len(rows) for rows in groups.values()) != 1552:
        raise ValueError("Retain the complete case and all Z12 requests.")
    changed, allocations = [], []
    field_index = next(i for i, loop in enumerate(loops) if loop.get("id") == FIELD)
    for (role, begin, new_index), rows in sorted(groups.items()):
        short = [r for r in rows if r["profile_index"] in REPLACEMENTS]
        short.sort(key=lambda r: (float(vehicles[r["vehicle_id"]].get("depart")), r["vehicle_id"]))
        primary_target = (3 * len(rows) + 2) // 4
        move = max(0, len(short) - (len(rows) - primary_target))
        for i, row in enumerate(short):
            if (i + 1) * move // len(short) > i * move // len(short):
                old_index = row["profile_index"]
                vehicles[row["vehicle_id"]].find("route").set("edges", " ".join(profiles[new_index]["edges"]))
                row.update(profile_index=new_index, free_flow_field_offset_s=profiles[new_index]["field_offsets_s"][str(field_index)])
                changed.append({"vehicle_id": row["vehicle_id"], "population_role": role, "target_begin_s": begin,
                                "old_profile_index": old_index, "new_profile_index": new_index})
        allocations.append({"population_role": role, "target_begin_s": begin, "destination": profiles[new_index]["edges"][-1],
                            "total": len(rows), "short_before": len(short), "short_after": len(short) - move, "moved_to_primary": move})
    structure = single_pass_structure(network, demand, loops)
    if not structure["structural_conditions_satisfied"]:
        raise ValueError("Retain the declared conditional single-field route structure.")
    out.mkdir(parents=True)
    for name, data in raw.items():
        (out / name).write_bytes(data)
    ET.ElementTree(demand).write(out / "demand.rou.xml", encoding="utf-8", xml_declaration=True)
    write_json_atomic(out / "lane-intentions.json", intentions)
    scenario = json.loads(raw["scenario.json"])
    scenario.update(demand_sha256=file_sha256(out / "demand.rou.xml"),
        demand_prior="Explore a 3:1 primary/residential entry proxy for Z12 within each population, target bin and destination. Reuse existing routes, retain all requests and unchanged post-merge paths. Lane counts do not identify field OD or capacity.",
        time_prior="Every request time and target bin retained. Reference profile offsets are updated without retiming departures.")
    write_json_atomic(out / "scenario.json", scenario)
    report = {"status": "prepared", "formal_result": False, "entry_facts": facts, "changed_by_population": dict(Counter(r["population_role"] for r in changed)),
              "allocation_rule": "3:1 passenger-lane proxy per population, target bin and destination; nearest-integer primary count with ties favoring primary. Move only excess short-entry requests, spread across original request times.",
              "allocations": allocations, "changed_vehicles": changed, "route_audits": audits, "structure": structure,
              "source_case": str(case), "source_sha256": sources, "sources_unchanged": all(file_sha256(Path(p)) == sha for p, sha in sources.items()),
              "input_sha256": {name: file_sha256(out / name) for name in names},
              "claim_boundary": "Exploratory entry prior, not measured OD or capacity. All real targets, vehicle IDs, request times, destinations, signal inputs, driver settings and lane permissions are retained. Native effects remain unmeasured."}
    write_json_atomic(out / "entry-prior.json", report)
    return report
