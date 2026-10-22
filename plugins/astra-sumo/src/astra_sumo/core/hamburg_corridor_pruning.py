"""Build the fixed Ring1 corridor while retaining complete official junctions."""

from collections import defaultdict
from copy import deepcopy
import csv
import gzip
import io
import json
from pathlib import Path
import xml.etree.ElementTree as ET

import numpy as np

from .artifact_io import write_json_atomic
from .candidate_contracts import file_sha256
from .road_hierarchy import _boundaries, _network, _path, _paths, materialize_road_selection
from .road_scope import road_hierarchy_tier
from .network_geometry_freeze import prepare_geometry_frozen_input
from .route_sensor_matrix import solve_nonnegative_integer_route_flows


OFFICIAL_PREFIXES = ("LSA104_", "LSA118_", "LSA119_", "LSA200_", "LSA535_")


def select_ring1_corridor(*, net_file, route_file, detector_file):
    """Keep source-supported through paths, full junction arms and detector roads."""
    root = ET.parse(net_file).getroot()
    roads, graph, lengths = _network(root, "passenger")
    raw = Path(route_file).read_bytes()
    route_root = ET.fromstring(gzip.decompress(raw) if str(route_file).endswith(".gz") else raw)
    reasons = defaultdict(set)
    routes = [vehicle.find("route").get("edges").split() for vehicle in route_root.findall("vehicle")]
    if not routes or any(len(route) != len(set(route)) or not set(route) <= graph.keys()
                         or any(last not in graph[first] for first, last in zip(route, route[1:])) for route in routes):
        raise ValueError("Use explicit legal single-pass reference routes.")
    for route in routes:
        for identity in route:
            reasons[identity].add("reference_boundary_to_boundary_corridor_path")
    official_nodes = {junction.get("id") for junction in root.findall("junction")
                      if junction.get("id", "").startswith(OFFICIAL_PREFIXES)}
    if {identity.split("_")[0] for identity in official_nodes} != {prefix[:-1] for prefix in OFFICIAL_PREFIXES}:
        raise ValueError("Retain all five source official junctions.")
    for identity, road in roads.items():
        if official_nodes & {road.get("from"), road.get("to")}:
            reasons[identity].add("complete_official_junction_arm")
    lane_roads = {lane.get("id"): identity for identity, road in roads.items() for lane in road.findall("lane")}
    loops = ET.parse(detector_file).getroot().findall("inductionLoop")
    if len(loops) != 13 or len({loop.get("id") for loop in loops}) != 13:
        raise ValueError("Retain all thirteen distinct detector fields.")
    for loop in loops:
        reasons[lane_roads[loop.get("lane")]].add("observed_detector_cross_section")
    turnarounds = {(row.get("from"), row.get("to")) for row in root.findall("connection") if row.get("dir") == "t"}
    forward = {key: {last for last in targets if (key, last) not in turnarounds} for key, targets in graph.items()}
    reverse = {key: set() for key in graph}
    for first, targets in forward.items():
        for last in targets:
            reverse[last].add(first)
    original_boundaries = _boundaries(root, roads, graph, set(roads))
    entries = {key for zone in original_boundaries for key in zone["entry_edges"]}
    exits = {key for zone in original_boundaries for key in zone["exit_edges"]}
    extensions = []
    tier_order = {"major": 0, "collector": 1, "local": 2, "unknown": 3}
    for identity in list(reasons):
        if identity not in graph or "complete_official_junction_arm" not in reasons[identity]:
            continue
        for direction, network, endpoints in (("entry", reverse, entries), ("exit", forward, exits)):
            if roads[identity].get("from") in official_nodes and roads[identity].get("to") in official_nodes:
                continue
            if roads[identity].get("to" if direction == "entry" else "from") not in official_nodes:
                continue
            distance, previous = _paths(network, lengths, identity, set(graph))
            choices = endpoints & distance.keys()
            if not choices:
                extensions.append({"arm": identity, "direction": direction, "status": "source_path_unresolved"})
                continue
            boundary = min(choices, key=lambda key: (tier_order[road_hierarchy_tier(roads[key].get("type", ""))], distance[key], key))
            path = _path(previous, identity, boundary)
            for key in path:
                reasons[key].add("official_arm_to_source_road_boundary")
            extensions.append({"arm": identity, "direction": direction, "boundary": boundary,
                               "path": list(reversed(path)) if direction == "entry" else path})
    # Retain the source's reverse carriageway where it has the same real endpoints.
    for identity in list(reasons):
        counterpart = identity[1:] if identity.startswith("-") else "-" + identity
        if counterpart in roads and {roads[counterpart].get("from"), roads[counterpart].get("to")} == {
                roads[identity].get("from"), roads[identity].get("to")}:
            reasons[counterpart].add("source_reverse_carriageway")
    selected = set(reasons)
    boundaries = _boundaries(root, roads, graph, selected)
    return {"schema": "astra.road-hierarchy-selection/v1", "keep_tier": "declared_ring1_corridor",
        "vehicle_class": "passenger", "source_road_count": len(roads), "selected_road_count": len(selected),
        "selected_edge_ids": sorted(selected), "excluded_edge_ids": sorted(roads.keys() - selected),
        "source_tier_counts": {}, "unserved_source_boundaries": [], "movements": [],
        "boundaries": boundaries, "official_junction_ids": sorted(official_nodes), "boundary_extensions": extensions,
        "roads": [{"edge_id": identity, "type": road.get("type"), "tier": road_hierarchy_tier(road.get("type", "")),
                   "selected": identity in selected, "reasons": sorted(reasons.get(identity, {"outside_declared_corridor"}))}
                  for identity, road in sorted(roads.items())],
        "claim_boundary": "Corridor paths are source-supported reference paths, not observed OD. All existing arms of the five official junctions and all thirteen field sections remain. Excluded roads are outside this simulation scope. Boundary rates require recalibration."}


def map_retained_control(*, source_net, candidate_net, source_control, output_file):
    """Preserve source phase timing and project its states onto retained native links."""
    source, candidate = [ET.parse(path).getroot() for path in (source_net, candidate_net)]

    def connections(root):
        crossings = {edge.get("id"): edge for edge in root.findall("edge") if edge.get("function") == "crossing"}
        result = {}
        for row in root.findall("connection"):
            if not row.get("tl"):
                continue
            crosswalk = crossings.get(row.get("to"))
            key = (row.get("from"), row.get("fromLane"), row.get("to"), row.get("toLane"))
            if crosswalk is not None:
                key = ("crosswalk", row.get("tl"), crosswalk.get("id").rsplit("_c", 1)[0],
                       tuple(sorted(crosswalk.get("crossingEdges").split())))
            if key in result:
                raise ValueError("A controlled movement has an ambiguous source identity.")
            result[key] = row.get("tl"), int(row.get("linkIndex"))
        return result

    old = connections(source)
    new = connections(candidate)
    indices = defaultdict(lambda: defaultdict(set))
    for movement, (tls, link) in new.items():
        if movement not in old or old[movement][0] != tls:
            raise ValueError("A retained controlled movement has no matching source controller.")
        indices[tls][link].add(old[movement][1])
    programs = ET.Element("additional")
    records = []
    for program in ET.parse(source_control).getroot():
        tls = program.get("id")
        if tls not in indices:
            continue
        size = max(indices[tls]) + 1
        mapped = deepcopy(program)
        for phase in mapped.findall("phase"):
            states = []
            for link in range(size):
                if link not in indices[tls]:
                    states.append("r")
                    continue
                colors = {phase.get("state")[original] for original in indices[tls][link]}
                if len(colors) != 1:
                    raise ValueError("Merged native links had different source signal states.")
                states.append(next(iter(colors)))
            phase.set("state", "".join(states))
        programs.append(mapped)
        records.append({"tls": tls, "old_links": len(program.find("phase").get("state")), "new_links": size,
                        "official": tls.startswith(OFFICIAL_PREFIXES)})
    if {row.get("id") for row in programs} != set(indices):
        raise ValueError("Every retained controller requires a source program.")
    ET.ElementTree(programs).write(output_file, encoding="utf-8", xml_declaration=True)
    return records


def build_ring1_corridor(*, repository, output_dir, netconvert_binary, sumo_binary):
    """Materialize one independent corridor candidate and unchanged count targets."""
    root, output = Path(repository).resolve(strict=True), Path(output_dir).resolve()
    source = root / "benchmarks/hamburg_ring1_primary_entry_full_20261022_v1/inputs"
    plan = select_ring1_corridor(net_file=source / "network.net.xml", route_file=source / "demand.rou.xml.gz",
                                detector_file=source / "field-loops.add.xml")
    frozen = output.with_name(output.name + "-geometry-input.net.xml")
    freeze = prepare_geometry_frozen_input(source_file=source / "network.net.xml", output_file=frozen)
    result = materialize_road_selection(net_file=frozen, output_dir=output,
        selection=plan, netconvert_binary=netconvert_binary, sumo_binary=sumo_binary)
    if result["status"] != "measured":
        return result
    tree = ET.parse(result["candidate"]["path"])
    candidate = tree.getroot()
    source_roads = {edge.get("id"): edge for edge in ET.parse(source / "network.net.xml").getroot().findall("edge")
                    if not edge.get("function")}
    for edge in list(candidate.findall("edge")):
        if not edge.get("function"):
            if any(edge.get(key) != source_roads[edge.get("id")].get(key) for key in ("from", "to")):
                raise ValueError("Scope extraction must retain source road endpoints.")
            index = list(candidate).index(edge)
            candidate.remove(edge)
            candidate.insert(index, deepcopy(source_roads[edge.get("id")]))
    final_net = output / "corridor.net.xml"
    tree.write(final_net, encoding="utf-8", xml_declaration=True)
    result.update(reference_network_sha256=file_sha256(source / "network.net.xml"), geometry_freeze=freeze,
                  retained_ordinary_roads_restored=True, extraction_candidate=deepcopy(result["candidate"]))
    result["candidate"] = {"path": str(final_net), "sha256": file_sha256(final_net)}
    inputs = output / "inputs"
    inputs.mkdir()
    for name in ("demand.rou.xml", "lane-intentions.json", "scenario.json", "field-loops.add.xml",
                 "expected-counts.csv", "initialization-counts.csv"):
        path = source / name
        raw = path.read_bytes() if path.is_file() else gzip.decompress(path.with_name(name + ".gz").read_bytes())
        (inputs / name).write_bytes(raw)
    (inputs / "network.net.xml").write_bytes(Path(result["candidate"]["path"]).read_bytes())
    mapped = map_retained_control(source_net=source / "network.net.xml", candidate_net=inputs / "network.net.xml",
        source_control=source / "control.add.xml", output_file=inputs / "control.add.xml")
    scenario = json.loads((inputs / "scenario.json").read_bytes())
    scenario.update(network_sha256=file_sha256(inputs / "network.net.xml"),
        control_sha256=file_sha256(inputs / "control.add.xml"),
        pruning_treatment="Source-supported Ring1 corridor paths, complete five official junctions and thirteen detector sections.",
        control_assumptions="Nine official M2 programs unchanged. Z15 retains labelled K9 alternative A. Retained non-official OSM controllers keep source actuated timing with states mapped to retained links. This is not observed field SPaT.",
        unverified="New corridor demand and native counts require recalibration.")
    write_json_atomic(inputs / "scenario.json", scenario)
    result.update(control_projection=mapped, prepared_inputs=str(inputs),
        targets_sha256=file_sha256(inputs / "expected-counts.csv"), field_bins=130, station_bins=40)
    write_json_atomic(output / "corridor-result.json", result)
    return result


def prepare_corridor_demand(*, repository, corridor_dir, output_dir):
    """Refit entry and exit shares using existing natively traversed route templates."""
    root, corridor, out = [Path(path).resolve() for path in (repository, corridor_dir, output_dir)]
    if out.exists() or corridor.is_relative_to(out):
        raise ValueError("Use a new demand directory separate from its corridor source.")
    source = corridor / "inputs"
    demand = ET.parse(source / "demand.rou.xml").getroot()
    intentions = json.loads((source / "lane-intentions.json").read_bytes())
    assigned = {row["vehicle_id"]: row for row in intentions["vehicles"]}
    vehicles = {row.get("id"): row for row in demand.findall("vehicle")}
    if len(vehicles) != 8640 or set(vehicles) != set(assigned):
        raise ValueError("Retain all 8640 declared model requests and target identities.")

    def read(folder, name):
        path = folder / name
        return path.read_bytes() if path.is_file() else gzip.decompress(path.with_name(name + ".gz").read_bytes())

    templates = {}
    provenance = []
    for name in ("hamburg_ring1_primary_entry_full_20261022_v1", "hamburg_ring1_western_entry_full_20261022_v1"):
        case = root / "benchmarks" / name
        raw_demand, raw_intent = read(case / "inputs", "demand.rou.xml"), read(case / "inputs", "lane-intentions.json")
        checked = json.loads(read(case, "passage-reconciliation.json"))
        if not checked.get("reconciled"):
            raise ValueError("Use route templates with reconciled native E1 identities.")
        completed = {(row["vehicle_id"], row["detector_id"]) for row in checked["passages"]}
        wanted = {row["vehicle_id"]: row for row in json.loads(raw_intent)["vehicles"]}
        for vehicle in ET.fromstring(raw_demand).findall("vehicle"):
            row = wanted[vehicle.get("id")]
            if (vehicle.get("id"), row["field_id"]) not in completed:
                continue
            key = row["field_id"], int(row["profile_index"])
            templates.setdefault(key, (deepcopy(vehicle), row))
        provenance.append({"case": name, "network_sha256": file_sha256(case / "inputs/network.net.xml"),
                           "native_passage_reconciliation": True})
    network = ET.parse(source / "network.net.xml").getroot()
    roads, graph, _ = _network(network, "passenger")
    templates = {key: value for key, value in templates.items()
                 if set(value[0].find("route").get("edges").split()) <= roads.keys()}
    base = root / "benchmarks/hamburg_ring1_m1_2_demand_pool_20261021_v1/base"
    rows = json.loads(gzip.decompress((base / "columns.json.gz").read_bytes()))["entry_rows"]
    with np.load(base / "provisional-system.npz") as system:
        capacities = system["entry_capacities"].copy()
    cap = {}
    for (entry, _period), value in zip(rows, capacities):
        if entry in cap and cap[entry] != int(value):
            raise ValueError("Reassess time-varying source entry priors before using initialization bins.")
        cap[entry] = int(value)
    templates = {key: value for key, value in templates.items() if value[0].find("route").get("edges").split()[0] in cap}
    keys = sorted(templates)
    fields = sorted({row["field_id"] for row in assigned.values()})
    field_index = {identity: index for index, identity in enumerate(fields)}
    entries = sorted({templates[key][0].find("route").get("edges").split()[0] for key in keys})
    entry_index = {identity: index for index, identity in enumerate(entries)}
    H, B = np.zeros((13, len(keys)), dtype=int), np.zeros((len(entries), len(keys)), dtype=int)
    by_field_entry = defaultdict(list)
    for column, key in enumerate(keys):
        route = templates[key][0].find("route").get("edges").split()
        if len(route) != len(set(route)) or any(last not in graph[first] for first, last in zip(route, route[1:])):
            raise ValueError("Keep only source-supported single-pass routes in the new corridor.")
        H[field_index[key[0]], column] = 1
        B[entry_index[route[0]], column] = 1
        by_field_entry[key[0], route[0]].append(column)
    if len(fields) != 13 or np.any(H.sum(axis=1) == 0):
        raise ValueError("Every one of the thirteen fields needs a retained native route template.")
    targets = []
    for name in ("initialization-counts.csv", "expected-counts.csv"):
        targets.extend(csv.DictReader(io.StringIO((source / name).read_text(encoding="utf-8"))))
    groups = defaultdict(list)
    for identity, row in assigned.items():
        groups[row["field_id"], float(row["target_begin_s"])].append(identity)
    allocation = []
    for begin in sorted({float(row["begin"]) for row in targets}):
        expected = {row["detector_id"]: int(row["expected_total"]) for row in targets if float(row["begin"]) == begin}
        y = np.array([expected[field] for field in fields])
        prior = np.zeros(len(keys), dtype=int)
        for field in fields:
            available = [entry for entry in entries if (field, entry) in by_field_entry]
            shares = {column: cap[entry] / len(by_field_entry[field, entry])
                      for entry in available for column in by_field_entry[field, entry]}
            weights = sum(shares.values())
            fractional = {column: expected[field] * share / weights for column, share in shares.items()}
            for column, value in fractional.items():
                prior[column] = int(value)
            left = expected[field] - sum(prior[column] for column in fractional)
            for column in sorted(fractional, key=lambda column: (-(fractional[column] - prior[column]), column))[:left]:
                prior[column] += 1
        solution = solve_nonnegative_integer_route_flows(H, y, prior=prior, entry_matrix=B,
            entry_capacities=np.array([cap[entry] for entry in entries]), time_limit_seconds=60)
        if solution["status"] != "pass":
            raise ValueError("The inherited entry-prior seed is infeasible. Retain all count targets.")
        x = np.array(solution["solution"], dtype=int)
        for field in fields:
            identities = sorted(groups[field, begin], key=lambda identity: (float(vehicles[identity].get("depart")), identity))
            sequence = sorted(((ordinal + .5) / int(count), column)
                              for column, count in enumerate(x) if keys[column][0] == field for ordinal in range(int(count)))
            if len(sequence) != len(identities) or len(identities) != expected[field]:
                raise ValueError("Retain every model request, target field and target bin.")
            for identity, (_, column) in zip(identities, sequence):
                template, row = templates[keys[column]]
                original = vehicles[identity]
                replacement = deepcopy(template)
                replacement.set("id", identity)
                replacement.set("depart", original.get("depart"))
                demand.remove(original)
                vehicles[identity] = replacement
                assigned[identity].update(profile_index=row["profile_index"],
                    free_flow_field_offset_s=row["free_flow_field_offset_s"])
            allocation.extend({"begin_s": begin, "field_id": keys[column][0], "profile_index": keys[column][1],
                               "entry": entries[int(np.argmax(B[:, column]))], "vehicles": int(count)}
                              for column, count in enumerate(x) if count and keys[column][0] == field)
    demand.extend(sorted(vehicles.values(), key=lambda vehicle: (float(vehicle.get("depart")), vehicle.get("id"))))
    out.mkdir(parents=True)
    for path in source.iterdir():
        if path.is_file():
            (out / path.name).write_bytes(path.read_bytes())
    ET.ElementTree(demand).write(out / "demand.rou.xml", encoding="utf-8", xml_declaration=True)
    write_json_atomic(out / "lane-intentions.json", intentions)
    scenario = json.loads((out / "scenario.json").read_bytes())
    scenario.update(demand_sha256=file_sha256(out / "demand.rou.xml"), profiles_used=len({r["profile_index"] for r in assigned.values()}),
        demand_prior="Refit field/bin route shares from natively traversed single-field templates. Prior entry weights reuse frozen road-class entry ceilings, with equal shares across retained exit templates. These are priors, not measured OD or native capacity.",
        time_prior="Every source model request time and field/bin identity retained. New native measurements are required.")
    write_json_atomic(out / "scenario.json", scenario)
    report = {"route_templates": len(keys), "vehicles": len(vehicles), "field_bins": 130,
              "initialization_bins": 52, "entry_prior_per_900s": cap, "sources": provenance, "allocations": allocation,
              "formal_result": False, "integer_seed_only": True, "new_native_measurement_required": True}
    write_json_atomic(out / "corridor-demand-seed.json", report)
    return report
