"""Describe motor dark links, alternative lane paths, and missing same-approach conflict relations."""

from collections import Counter, defaultdict
import gzip
import json
from pathlib import Path
import xml.etree.ElementTree as ET

from shapely.geometry import LineString
from shapely.ops import substring

from .artifact_io import write_json_atomic
from .candidate_contracts import file_sha256
from .connection_mode_audit import _MOTORIZED_MODES
from .demand_route_pool import lane_feasible_connections
from .hamburg_readme_demand import _internal_paths
from .local_network_conversion import loaded_turn_permissions
from .ocit_c import _child, _descendants, _text


def _json(path):
    path = Path(path)
    if not path.exists():
        path = path.with_suffix(path.suffix + ".gz")
    return json.loads(gzip.decompress(path.read_bytes()) if path.suffix == ".gz" else path.read_bytes())


def path_proximity(first, second):
    """Screen model centerlines with a declared 1 m buffer after clipping common endpoints."""
    trimmed = [
        substring(line, min(5.0, line.length * 0.2), line.length - min(5.0, line.length * 0.2))
        for line in (first, second)
    ]
    overlap = trimmed[0].buffer(1.0).intersection(trimmed[1].buffer(1.0)).area
    return {
        "centerlines_cross": first.crosses(second),
        "centerline_overlap_m": first.intersection(second).length,
        "trimmed_minimum_distance_m": trimmed[0].distance(trimmed[1]),
        "buffer_overlap_m2": overlap,
        "proximity_flag": overlap > 1e-6,
    }


def inventory_hamburg_motor_dark_links(*, control_dir, demand_dir, output_dir):
    control_dir, demand_dir = Path(control_dir).resolve(strict=True), Path(demand_dir).resolve(strict=True)
    output = Path(output_dir).resolve()
    if output.exists():
        raise ValueError("Use a new dark-link inventory directory.")
    network, control = control_dir / "inputs/network.xml", control_dir / "control-A.add.xml"
    root = ET.parse(network).getroot()
    modes = loaded_turn_permissions(root)
    native = _json(demand_dir / "native-lanes.json")["lanes"]
    lanes = defaultdict(list)
    for lane in native.values():
        if lane["passenger"] and not lane["edge_id"].startswith(":"):
            lanes[lane["edge_id"]].append(lane)
    links = defaultdict(set)
    for (a, la, b, lb), permitted in modes.items():
        if "passenger" in permitted:
            links[a, la, b].add(lb)
    pool = _json(demand_dir / "route-pool.json")["routes"]
    profiles = _json(demand_dir / "ideal-lane-profiles.json")["profiles"]
    vehicles = _json(demand_dir / "provisional-lane-intentions.json")["vehicles"]
    pool_usage = defaultdict(list)
    for route in pool:
        start = {lane["index"] for lane in lanes[route["source_edge"]] if lane["length"] >= 7.5}
        for key in lane_feasible_connections(route["edges"], lanes, links, allowed_departure_lanes=start):
            pool_usage[key].append(route["route_id"])
    intended = Counter()
    selected_counts = Counter(v["profile_index"] for v in vehicles)
    possible_selected = Counter()
    selected_routes = defaultdict(set)
    for index, count in selected_counts.items():
        profile = profiles[index]
        for i, (a, b) in enumerate(zip(profile["edges"], profile["edges"][1:])):
            intended[a, profile["exit_lanes"][i], b, profile["entry_lanes"][i + 1]] += count
        for key in lane_feasible_connections(
            profile["edges"], lanes, links, allowed_departure_lanes={profile["entry_lanes"][0]}
        ):
            possible_selected[key] += count
            selected_routes[key].add(profile["route_id"])
    bindings = _json(control_dir / "link-bindings.json")["rows"]
    phases = {logic.get("id"): logic.findall("phase") for logic in ET.parse(control).getroot().findall("tlLogic")}
    semantics = _json(control_dir / "native-network-semantics.json")
    foes = {
        lane: set(values) for node in semantics["junctions"].values() for lane, values in node["internal_foes"].items()
    }
    paths = _internal_paths(root)
    shape_by_lane = {
        lane.get("id"): [tuple(map(float, p.split(","))) for p in lane.get("shape").split()]
        for edge in root.findall("edge")
        for lane in edge.findall("lane")
    }
    geometry = {
        key: LineString([point for lane in chain for point in shape_by_lane[lane]])
        for key, chain in paths.items()
        if chain
    }
    main_direction = {}
    for node in ("104", "118", "119", "200", "535"):
        for group in _descendants(ET.parse(control_dir / f"inputs/ocit_{node}.xml").getroot(), "Signalgruppe"):
            name = _text(_child(group, "BezeichnungKurz"))
            if name:
                main_direction[node, name] = _text(_child(group, "Hauptrichtung"))
    rows = []
    for row in bindings:
        key, tls = tuple(row["physical_key"]), row["tls"]
        permitted = sorted(modes.get(key, set()) & _MOTORIZED_MODES)
        if not tls.startswith("LSA") or not permitted:
            continue
        clock, intervals = 0.0, []
        for phase in phases[tls]:
            end = clock + float(phase.get("duration"))
            if phase.get("state")[row["link_index"]] == "O":
                intervals.append([clock, end])
            clock = end
        if not intervals:
            continue
        same_approach, foe_links = [], []
        for other in bindings:
            if other["tls"] != tls or other["physical_key"] == row["physical_key"]:
                continue
            native_foe = other["via"] in foes.get(row["via"], set()) or row["via"] in foes.get(other["via"], set())
            if native_foe:
                foe_links.append(
                    {
                        "physical_key": other["physical_key"],
                        "link_index": other["link_index"],
                        "groups": other["groups"],
                    }
                )
            other_key = tuple(other["physical_key"])
            if other_key[0] != key[0] or other_key not in geometry or key not in geometry:
                continue
            clock, overlap_s = 0.0, 0.0
            for phase in phases[tls]:
                end = clock + float(phase.get("duration"))
                if phase.get("state")[other["link_index"]] in "Gg":
                    overlap_s += sum(max(0.0, min(end, b) - max(clock, a)) for a, b in intervals)
                clock = end
            proximity = path_proximity(geometry[key], geometry[other_key])
            if overlap_s and proximity["proximity_flag"]:
                same_approach.append(
                    {
                        "physical_key": other["physical_key"],
                        "groups": other["groups"],
                        "same_source_lane": key[1] == other_key[1],
                        "native_foe_relation": native_foe,
                        "dark_reports_other_as_foe": other["via"] in foes.get(row["via"], set()),
                        "other_reports_dark_as_foe": row["via"] in foes.get(other["via"], set()),
                        "green_overlap_with_dark_s": overlap_s,
                        **proximity,
                    }
                )
        alternatives = [
            other
            for other in bindings
            if other["physical_key"][0] == key[0]
            and other["physical_key"][2] == key[2]
            and other["physical_key"][1] != key[1]
            and other["origin"] == "embedded_official_vt"
            and other["groups"]
        ]
        rows.append(
            {
                **row,
                "motor_classes": permitted,
                "dark_intervals_s": intervals,
                "native_foe_lanes": sorted(foes.get(row["via"], set())),
                "native_foe_connections": foe_links,
                "pool_lane_feasible_route_ids": pool_usage[key],
                "selected_lane_feasible_route_ids": sorted(selected_routes[key]),
                "selected_lane_feasible_vehicles": possible_selected[key],
                "intended_vehicle_passages": intended[key],
                "official_other_lane_same_road_pair": [
                    {
                        "physical_key": a["physical_key"],
                        "groups": a["groups"],
                        "official_movements": a["official_movements"],
                    }
                    for a in alternatives
                ],
                "same_approach_proximity": same_approach,
                "same_approach_proximity_without_foe": any(not r["native_foe_relation"] for r in same_approach),
                "source_main_direction": {"/".join(pair): main_direction.get(tuple(pair)) for pair in row["groups"]},
            }
        )
    output.mkdir(parents=True)
    write_json_atomic(output / "motor-dark-links.json", {"rows": rows})
    source_dark = []
    for node in ("104", "118", "119", "200", "535"):
        for group in _json(control_dir / f"source-program-{node}.json")["groups"]:
            dark_only = set(group["allowed_image_securing_states"]) == {"00"}
            dark_spans = [
                r for r in group["intervals"] if r["signal_image"] == "00" and r["securing_states"] == ["release"]
            ]
            if group["traffic_class_native"] != "Kfz" or not (dark_only or dark_spans):
                continue
            members = [r for r in bindings if [node, group["group_id"]] in r["groups"]]
            source_dark.append(
                {
                    "node": node,
                    "group": group["group_id"],
                    "source_status": group["status"],
                    "dark_only": dark_only,
                    "source_dark_intervals": dark_spans,
                    "source_main_direction": main_direction.get((node, group["group_id"])),
                    "installed_physical_keys": [r["physical_key"] for r in members],
                    "pool_lane_feasible_route_ids": sorted(
                        {route for r in members for route in pool_usage[tuple(r["physical_key"])]}
                    ),
                    "intended_bound_movement_passages": sum(intended[tuple(r["physical_key"])] for r in members),
                    "correspondence_limit": "No installed group-to-road binding; field movement volume remains unknown."
                    if not members
                    else "Composed output may follow another group; inspect the connection-level dark intervals separately.",
                }
            )
    write_json_atomic(output / "source-dark-groups.json", {"rows": source_dark})
    unmatched = [r for r in rows if r["origin"] == "declared_uncontrolled"]
    report = {
        "schema": "astra.hamburg-motor-dark-inventory/v1",
        "status": "measured",
        "formal_result": False,
        "network_sha256": file_sha256(network),
        "control_sha256": file_sha256(control),
        "motor_dark_connections": len(rows),
        "source_dark_motor_groups": len(source_dark),
        "origin_counts": dict(Counter(r["origin"] for r in rows)),
        "unmatched_passenger_connections": sum("passenger" in r["motor_classes"] for r in unmatched),
        "unmatched_lane_feasible_in_pool": sum(bool(r["pool_lane_feasible_route_ids"]) for r in unmatched),
        "unmatched_with_selected_intent": sum(bool(r["intended_vehicle_passages"]) for r in unmatched),
        "unmatched_proximity_without_foe": sum(r["same_approach_proximity_without_foe"] for r in unmatched),
        "geometry_screen": "Reference network internal centerlines only. Clip min(5 m,20% path length) at both ends and buffer each path by 1 m. This flags proximity, not a measured vehicle collision or field turn prohibition.",
        "lane_feasibility": "All 468 retained routes, with native access/change permissions, including alternative lanes. Changes are instantaneous; selected-vehicle reachability is an upper bound, not realized lane choice.",
        "traffic_simulation_performed": False,
        "control_changes_materialized": False,
    }
    write_json_atomic(output / "report.json", report)
    return report
