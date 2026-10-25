"""Build the provisional offline demand pool for the README Hamburg case."""

from collections import Counter, defaultdict
from fractions import Fraction
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import xml.etree.ElementTree as ET

import numpy as np
from scipy.optimize import linprog

from .artifact_io import write_json_atomic
from .candidate_contracts import file_sha256
from .command_runner import run_command, _terminate_process_tree
from .connection_mode_audit import audit_network_connection_mode
from .demand_route_pool import boundary_route_pool, ideal_lane_profiles
from .detector_demand import compare_expected_to_e1_strict, read_csv_rows, read_declared_boundaries, read_net, write_csv
from .local_network_conversion import loaded_turn_permissions
from .osm_access import _permission_set
from .road_scope import road_hierarchy_tier
from .route_sensor_matrix import solve_nonnegative_integer_route_flows


def entry_rate_vph(edge, request):
    """Read the declared entry prior without treating road class as an observed capacity."""
    exclude_customers = request.get("exclude_customer_entries", False)
    if not isinstance(exclude_customers, bool):
        raise ValueError("Declare customer-entry exclusion as a boolean.")
    access = edge.find("param[@key='access']")
    if exclude_customers and access is not None and access.get("value") == "customers":
        return 0
    rates = request.get("entry_vph_by_highway")
    if rates is None:
        return request["entry_vph_per_lane"]
    highway = [value.removeprefix("highway.") for value in edge.get("type", "").split("|")
               if value.startswith("highway.")]
    if len(highway) != 1 or highway[0] not in rates:
        raise ValueError(f"Declare an inflow prior for entry road type {edge.get('type')}.")
    value = rates[highway[0]]
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
        raise ValueError("Entry class priors must be finite nonnegative numbers.")
    return value


def _capture_lanes(request, output):
    import traci
    from sumolib.miscutils import getFreeSocketPort

    network = Path(request["network"])
    root = ET.parse(network).getroot()
    port = getFreeSocketPort()
    command = [request["sumo_binary"], "-n", str(network), "--remote-port", str(port),
               "--no-step-log", "true", "--error-log", str(output.with_suffix(".errors.log"))]
    process = subprocess.Popen(command, stdin=subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0, start_new_session=os.name != "nt")
    connection = None
    try:
        connection = traci.connect(port, numRetries=3, proc=process)
        version = connection.getVersion()
        if not version[1].endswith("1.27.1"):
            raise ValueError("Use the declared SUMO 1.27.1 runtime.")
        lanes = {}
        for edge in root.findall("edge"):
            for lane in edge.findall("lane"):
                key = lane.get("id")
                passenger = "passenger" in _permission_set(lane.attrib, network_version=root.get("version", "0.0"))
                lanes[key] = {"id": key, "edge_id": edge.get("id"), "index": int(lane.get("index")),
                    "length": connection.lane.getLength(key), "speed": connection.lane.getMaxSpeed(key),
                    "passenger": passenger,
                    "change_left": connection.lane.getChangePermissions(key, 1) if passenger else [],
                    "change_right": connection.lane.getChangePermissions(key, -1) if passenger else []}
        write_json_atomic(output, {"native_version": version, "traci_module": traci.__file__,
            "simulation_time_s": connection.simulation.getTime(), "simulation_steps": 0, "lanes": lanes})
    finally:
        if connection is not None:
            connection.close(wait=False)
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            _terminate_process_tree(process)
    if process.returncode:
        raise RuntimeError(f"SUMO lane inspection exited with code {process.returncode}.")


def _internal_paths(root):
    audit = audit_network_connection_mode(root, endpoint_tolerance_m=.1)
    paths = {(m["from"], int(m["fromLane"]), m["to"], int(m["toLane"])): m["internal_path"]["internal_lane_chain"]
             for junction in audit["junctions"] for m in junction["connection_mode_audit"]["movement_checks"]
             if m["internal_path"]["status"] == "pass"}
    for key, modes in loaded_turn_permissions(root).items():
        if "passenger" in modes and key not in paths:
            raise ValueError(f"Passenger connection has no verified internal path: {key}")
    return paths


def profile_free_flow(route, profile, lanes, fields, internal_paths, vehicle_length_m, departure_position_m):
    """Use lane limits and rear-clear offsets, with no signal, queue, or lane-change delay."""
    segments, field_distances, approach_distances = [], [], []
    distance = 0.0
    by_road = defaultdict(list)
    for field in fields:
        by_road[field["edge_id"]].append(field)
    for index, edge in enumerate(route):
        lane = lanes[f"{edge}_{profile['exit_lanes'][index]}"]
        for field in by_road[edge]:
            if field["lane_id"] == lane["id"]:
                field_distances.append((field["field_index"], distance + field["pos"] + vehicle_length_m))
        segments.append((lane["length"], lane["speed"]))
        distance += lane["length"]
        approach_distances.append(distance)
        if index < len(route) - 1:
            key = edge, profile["exit_lanes"][index], route[index + 1], profile["entry_lanes"][index + 1]
            for identity in internal_paths[key]:
                internal = lanes[identity]
                segments.append((internal["length"], internal["speed"]))
                distance += internal["length"]

    def time_at(target):
        remaining, elapsed = target, 0.0
        for length, speed in segments:
            if speed <= 0:
                raise ValueError("Free-flow travel time requires positive lane speeds.")
            used = min(remaining, length)
            elapsed += used / speed
            remaining -= used
            if remaining <= 1e-8:
                return elapsed
        return None

    origin_time = time_at(departure_position_m)
    offsets = {field: time_at(position) for field, position in field_distances}
    return ({k: v - origin_time for k, v in offsets.items() if v is not None and v >= origin_time},
            [time_at(position) - origin_time for position in approach_distances], distance - departure_position_m)


def timed_columns(profiles, field_count, *, begin, interval, bins, end, step):
    """Give each provisional field signature a departure interval inside one entry time bin."""
    columns = {}
    for index, profile in enumerate(profiles):
        offsets = profile["field_offsets_s"]
        if not offsets:
            continue
        first_bin = max(0, math.floor((begin - max(offsets.values())) / interval) - 1)
        for departure_bin in range(first_bin, math.ceil((begin + bins * interval) / interval)):
            departure = (departure_bin + .5) * interval
            if departure >= end:
                continue
            signature = []
            lower, upper = departure_bin * interval, min(end, (departure_bin + 1) * interval)
            for field, offset in sorted(offsets.items()):
                target = math.floor((departure + offset - begin) / interval)
                if target < 0:
                    upper = min(upper, begin - offset)
                elif target >= bins:
                    lower = max(lower, begin + bins * interval - offset)
                else:
                    signature.append(target * field_count + field)
                    lower = max(lower, begin + target * interval - offset)
                    upper = min(upper, begin + (target + 1) * interval - offset)
            if not signature or upper - lower <= 2 * step:
                continue
            key = profile["source_edge"], departure_bin, tuple(signature)
            candidate = {"profile_index": index, "entry": profile["source_edge"], "departure_bin": departure_bin,
                         "field_rows": signature, "lower_s": lower + step, "upper_s": upper - step,
                         "distance_km": profile["distance_m"] / 1000}
            if key not in columns or candidate["distance_km"] < columns[key]["distance_km"]:
                columns[key] = candidate
    return list(columns.values())


def vehicle_lower_bound_certificate(response, observations, entries, capacities, field_dual, entry_dual):
    """Check an exact weak-duality bound for the finite, provisional route matrix."""
    field = [Fraction(float(v)).limit_denominator(1000000) for v in field_dual]
    entry = [min(Fraction(0), Fraction(float(v)).limit_denominator(1000000)) for v in entry_dual]
    values = []
    for column in range(response.shape[1]):
        value = sum((int(response[i, column]) * field[i] for i in np.flatnonzero(response[:, column])), Fraction(0))
        value += sum((int(entries[i, column]) * entry[i] for i in np.flatnonzero(entries[:, column])), Fraction(0))
        values.append(value)
    scale = max([Fraction(1), *values])
    field, entry = [v / scale for v in field], [v / scale for v in entry]
    objective = sum((int(v) * w for v, w in zip(observations, field)), Fraction(0))
    objective += sum((int(v) * w for v, w in zip(capacities, entry)), Fraction(0))
    assert all(v / scale <= 1 for v in values) and all(v <= 0 for v in entry)
    def encode(value):
        return [value.numerator, value.denominator]
    return {"integer_vehicle_lower_bound": max(0, math.ceil(objective)), "dual_objective": encode(objective),
        "field_dual": [encode(v) for v in field], "entry_dual": [encode(v) for v in entry],
        "maximum_column_dual_sum": encode(max(values, default=Fraction(0)) / scale),
        "columns_checked": len(values), "scope": "Finite provisional matrix only. Not a bound for all possible field traffic."}


def build_hamburg_readme_demand(*, request_file, output_dir, sumo_binary):
    request_file = Path(request_file).resolve(strict=True)
    request = json.loads(request_file.read_text(encoding="utf-8"))
    output = Path(output_dir).resolve()
    inputs = {name: (request_file.parent / request[name]).resolve(strict=True)
              for name in ("network", "boundary_selection", "field_loops", "counts", "junction_groups")}
    if output.exists() or any(p.is_relative_to(output) for p in inputs.values()):
        raise ValueError("Use a new demand directory separate from its inputs.")
    for name in ("entry_min_length_m", "entry_vph_per_lane", "nominal_approach_vph", "vehicle_length_m",
                 "departure_position_m", "step_length_s"):
        if not math.isfinite(request[name]) or request[name] <= 0:
            raise ValueError(f"Use a positive finite {name}.")
    if not request["vehicle_length_m"] <= request["departure_position_m"] <= request["entry_min_length_m"]:
        raise ValueError("The complete departing vehicle must fit inside every eligible entry lane.")
    if request["interval_s"] != 900 or not math.isfinite(request["comparison_offset_s"]) or request["comparison_offset_s"] < 0:
        raise ValueError("Use the declared 900-second count grid and a finite nonnegative time offset.")
    snapshots = {k: {"path": str(v), "sha256": file_sha256(v)} for k, v in inputs.items()}
    output.mkdir(parents=True)
    write_json_atomic(output / "request.json", request)
    native_request = {"network": str(inputs["network"]), "sumo_binary": str(sumo_binary)}
    write_json_atomic(output / "native-lane-request.json", native_request)
    environment = {**os.environ, "PYTHONPATH": str(Path(__file__).parents[2]),
                   "SUMO_HOME": str(Path(sumo_binary).parent.parent)}
    executed = run_command([sys.executable, "-m", __name__, str(output / "native-lane-request.json"),
                            str(output / "native-lanes.json")], cwd=output, timeout_seconds=120, env=environment)
    write_json_atomic(output / "native-lane-command.json", executed.to_dict())
    if executed.status != "pass":
        raise RuntimeError("Native lane inspection failed. Inspect its saved command and log.")
    native = json.loads((output / "native-lanes.json").read_text(encoding="utf-8"))
    lanes = native["lanes"]
    root = ET.parse(inputs["network"]).getroot()
    edges, connections = read_net(inputs["network"])
    selection = json.loads(inputs["boundary_selection"].read_text(encoding="utf-8"))
    write_json_atomic(output / "boundary-declarations.json", {"zones": selection["boundaries"]})
    sources, sinks, boundary_audit = read_declared_boundaries(output / "boundary-declarations.json", edges)
    lane_edges = defaultdict(list)
    for row in lanes.values():
        if row["passenger"] and not row["edge_id"].startswith(":"):
            lane_edges[row["edge_id"]].append(row)
    fields = [{"id": row.get("id"), "lane_id": row.get("lane"),
               "edge_id": row.get("lane").rsplit("_", 1)[0], "pos": float(row.get("pos")), "field_index": index}
              for index, row in enumerate(sorted(ET.parse(inputs["field_loops"]).getroot(), key=lambda r: r.get("id")))]
    if len(fields) != 13 or len({r["lane_id"] for r in fields}) != 13:
        raise ValueError("The README case requires all 13 distinct field lanes.")
    if any(not math.isfinite(f["pos"]) or not 0 <= f["pos"] <= lanes[f["lane_id"]]["length"]
           or not lanes[f["lane_id"]]["passenger"] for f in fields):
        raise ValueError("Fields must lie on their declared native passenger lanes.")
    expected = read_csv_rows(inputs["counts"])
    compare_expected_to_e1_strict(expected, {}, count_attribute="nVehContrib")
    if len(expected) != 130:
        raise ValueError("Retain all 130 declared field intervals.")
    ids = {row["id"]: row["field_index"] for row in fields}
    interval = request["interval_s"]
    begin = min(float(row["begin"]) for row in expected) + request["comparison_offset_s"]
    if not math.isfinite(request["end_s"]) or request["end_s"] < begin + 10 * interval:
        raise ValueError("The horizon must retain the complete count window.")
    observations = np.zeros(130, dtype=np.int64)
    expected_by_row = {}
    for row in expected:
        position = (float(row["begin"]) + request["comparison_offset_s"] - begin) / interval
        if position != int(position) or not 0 <= position < 10:
            raise ValueError("Field intervals must lie on the complete ten-bin grid.")
        b = int(position)
        index = b * 13 + ids[row["detector_id"]]
        if index in expected_by_row or float(row["end"]) - float(row["begin"]) != interval:
            raise ValueError("Use ten complete, distinct 15-minute intervals per field.")
        observations[index] = int(row["expected_total"])
        expected_by_row[index] = row
    entry_lanes = {edge: [r for r in lane_edges[edge] if r["length"] >= request["entry_min_length_m"]] for edge in sources}
    allowed_sources = [edge for edge in sources if entry_lanes[edge]]
    entry_inventory = [{"entry": edge, "motor_lanes": len(lane_edges[edge]), "eligible_lanes": len(entry_lanes[edge]),
        "minimum_native_lane_length_m": min(r["length"] for r in lane_edges[edge]),
        "maximum_inflow_veh_per_h": len(entry_lanes[edge]) * entry_rate_vph(root.find(f"edge[@id='{edge}']"), request),
        "short_entry_excluded": not entry_lanes[edge]} for edge in sources]
    write_csv(output / "entry-inventory.csv", entry_inventory, list(entry_inventory[0]))
    groups = json.loads(inputs["junction_groups"].read_text(encoding="utf-8"))["groups"]
    junction_groups = {r["join_id"]: f"LSA{r['node_id']}" for r in groups}
    pool, excluded = boundary_route_pool(edges, connections, allowed_sources, sinks,
        {r["edge_id"] for r in fields}, junction_groups, request.get("entry_alternatives", ()))
    write_json_atomic(output / "route-pool.json", {"routes": pool, "excluded": excluded,
        "multipart_rule": "Consecutive different official parts form one traversal. An ordinary junction or a revisited part starts a new traversal. Every repeated junction is excluded."})
    access = loaded_turn_permissions(root)
    lane_connections = defaultdict(set)
    for (a, la, b, lb), modes in access.items():
        if "passenger" in modes:
            lane_connections[a, la, b].add(lb)
    internal = _internal_paths(root)
    profiles, fixed_signatures = [], []
    coverage = {r["id"]: {"single_pass": 0, "flagged_indirect": 0} for r in fields}
    for route in pool:
        path = route["edges"]
        departure_lanes = {r["index"] for r in entry_lanes[route["source_edge"]]}
        fixed_signatures.extend(p["field_mask"] for p in ideal_lane_profiles(path, lane_edges, lane_connections, fields,
            allow_changes=False, allowed_departure_lanes=departure_lanes))
        for profile in ideal_lane_profiles(path, lane_edges, lane_connections, fields, allowed_departure_lanes=departure_lanes):
            offsets, approach_times, distance = profile_free_flow(path, profile, lanes, fields, internal,
                request["vehicle_length_m"], request["departure_position_m"])
            if not offsets:
                continue
            profile = {**profile, "field_mask": sum(1 << field for field in offsets),
                "route_id": route["route_id"], "source_edge": route["source_edge"],
                "edges": path, "distance_m": distance, "field_offsets_s": offsets,
                "approach_offsets_s": approach_times, "flagged_indirect": bool(route["junction_audit"]["repeats"])}
            for field in fields:
                if field["field_index"] in offsets:
                    coverage[field["id"]]["flagged_indirect" if profile["flagged_indirect"] else "single_pass"] += 1
            profiles.append(profile)
    write_json_atomic(output / "ideal-lane-profiles.json", {"profiles": profiles,
        "claim_boundary": "Permitted lane intentions with instantaneous ordinary-road changes. No native response or realized lane allocation."})
    columns = timed_columns(profiles, 13, begin=begin, interval=interval, bins=10,
                            end=request["end_s"], step=request["step_length_s"])
    entry_rows = sorted({(r["entry"], r["departure_bin"]) for r in columns})
    row_indices = {key: i for i, key in enumerate(entry_rows)}
    response = np.zeros((130, len(columns)), dtype=np.int64)
    entry_matrix = np.zeros((len(entry_rows), len(columns)), dtype=np.int64)
    for col, row in enumerate(columns):
        response[row["field_rows"], col] = 1
        entry_matrix[row_indices[row["entry"], row["departure_bin"]], col] = 1
    capacities = np.asarray([math.floor(len(entry_lanes[edge]) * entry_rate_vph(root.find(f"edge[@id='{edge}']"), request) * interval / 3600)
                             for edge, b in entry_rows], dtype=np.int64)
    np.savez_compressed(output / "provisional-system.npz", response=response, observations=observations,
                        entry_matrix=entry_matrix, entry_capacities=capacities)
    write_json_atomic(output / "columns.json", {"columns": columns, "entry_rows": entry_rows})
    proposal = solve_nonnegative_integer_route_flows(response, observations,
        route_costs=np.asarray([r["distance_km"] for r in columns]), entry_matrix=entry_matrix, entry_capacities=capacities,
        time_limit_seconds=request["solver_limit_s"])
    write_json_atomic(output / "integer-proposal.json", proposal)
    relaxed = linprog(np.ones(len(columns)), A_eq=response, b_eq=observations,
                     A_ub=entry_matrix, b_ub=capacities, bounds=(0, None), method="highs",
                     options={"time_limit": request["solver_limit_s"]})
    certificate = (vehicle_lower_bound_certificate(response, observations, entry_matrix, capacities,
        relaxed.eqlin.marginals, relaxed.ineqlin.marginals) if relaxed.success else None)
    write_json_atomic(output / "vehicle-lower-bound.json", {"lp_status": int(relaxed.status),
        "lp_message": str(relaxed.message), "certificate": certificate})
    lower = certificate["integer_vehicle_lower_bound"] if certificate else None
    summary = {"schema": "astra.hamburg-readme-demand-pool/v1", "status": "measured", "formal_result": False,
        "exact_count_fit": False, "inputs": snapshots, "native_lane_inventory": str(output / "native-lanes.json"),
        "boundary_audit": boundary_audit, "route_count": len(pool), "excluded_routes": len(excluded),
        "flagged_indirect_routes": sum(bool(r["junction_audit"]["repeats"]) for r in pool),
        "ideal_profile_count": len(profiles), "field_coverage": coverage,
        "ideal_lane_intent_rank": int(np.linalg.matrix_rank(np.asarray([[int(p["field_mask"] >> f & 1) for p in profiles] for f in range(13)]))),
        "fixed_lane_no_changes_rank": int(np.linalg.matrix_rank(np.asarray([[int(mask >> f & 1) for mask in fixed_signatures] for f in range(13)]))),
        "native_response_rank_without_lane_intent": None,
        "rank_boundary": "Both reported ranks are structural. Native driver behavior is not measured in this offline assignment.",
        "maximum_fields_per_vehicle_in_pool": max((len(p["field_offsets_s"]) for p in profiles), default=0),
        "vehicle_count_lower_bound_within_declared_pool": lower,
        "integer_status": proposal["status"], "integer_equations_exact": proposal["status"] == "pass",
        "entry_vph_per_lane_assumption": request["entry_vph_per_lane"] if "entry_vph_by_highway" not in request else None,
        "entry_vph_by_highway_assumption": request.get("entry_vph_by_highway"),
        "exclude_customer_entries_assumption": request.get("exclude_customer_entries", False),
        "entry_alternatives": request.get("entry_alternatives", []),
        "entry_inventory": entry_inventory,
        "distance_basis": "Selected native ordinary and internal lane lengths, minus the explicit departure front position.",
        "claim_boundary": "Offline route and ideal-lane proposal only. Free-flow times omit signals, queues, acceleration and lane-change delay. No SUMO E1 count fit, field OD, or operating capacity is established."}
    if proposal["status"] == "pass":
        _write_proposal(output, request, pool, profiles, columns, proposal, entry_rows, capacities,
                        sources, entry_lanes, lane_edges, root, junction_groups, expected_by_row, observations, response, summary)
    summary["inputs_unchanged"] = all(file_sha256(Path(r["path"])) == r["sha256"] for r in snapshots.values())
    write_json_atomic(output / "report.json", summary)
    return summary


def _write_proposal(output, request, pool, profiles, columns, proposal, entry_rows, capacities,
                    sources, entry_lanes, lane_edges, root, junction_groups, expected_rows, observations, response, summary):
    routes = ET.Element("routes")
    ET.SubElement(routes, "vType", id="m1_passenger", vClass="passenger", length=str(request["vehicle_length_m"]),
                  minGap="2.5", sigma="0", speedFactor="1", speedDev="0")
    emitted, intentions = [], []
    entry_loads, approach_loads, turnaround_loads = Counter(), Counter(), Counter()
    edge_nodes = {e.get("id"): e.get("to") for e in root.findall("edge")}
    connections = {(c.get("from"), int(c.get("fromLane", "0")), c.get("to"), int(c.get("toLane", "0"))): c
                   for c in root.findall("connection") if not c.get("from", "").startswith(":")}
    for index, (column, count) in enumerate(zip(columns, proposal["solution"])):
        if not count:
            continue
        profile = profiles[column["profile_index"]]
        for n in range(count):
            depart = column["lower_s"] + (n + .5) * (column["upper_s"] - column["lower_s"]) / count
            identity = f"m1_c{index}_v{n}"
            vehicle = ET.Element("vehicle", id=identity, type="m1_passenger", depart=f"{depart:.9f}",
                                 departLane=str(profile["entry_lanes"][0]), departSpeed="max", speedFactor="1",
                                 departPos=str(request["departure_position_m"]))
            ET.SubElement(vehicle, "route", edges=" ".join(profile["edges"]))
            emitted.append(vehicle)
            intentions.append({"vehicle_id": identity, "profile_index": column["profile_index"], "depart_s": depart})
            entry_loads[column["entry"], column["departure_bin"]] += 1
            for i, (a, b) in enumerate(zip(profile["edges"], profile["edges"][1:])):
                node = edge_nodes[a]
                if node in junction_groups:
                    period = math.floor((depart + profile["approach_offsets_s"][i]) / request["interval_s"])
                    approach_loads[node, a, profile["exit_lanes"][i], period] += 1
                connection = connections[a, profile["exit_lanes"][i], b, profile["entry_lanes"][i + 1]]
                if connection.get("dir") in {"t", "T"}:
                    turnaround_loads[junction_groups.get(node, node)] += 1
    routes.extend(sorted(emitted, key=lambda r: (float(r.get("depart")), r.get("id"))))
    ET.ElementTree(routes).write(output / "provisional-demand.rou.xml", encoding="utf-8", xml_declaration=True)
    write_json_atomic(output / "provisional-lane-intentions.json", {"implemented_in_vehicle_control": False,
        "vehicles": intentions, "claim_boundary": "Profile sidecar only. The route XML does not enforce these lane intentions."})
    entry_caps = dict(zip(entry_rows, capacities.tolist()))
    rows = []
    for edge in sources:
        periods = sorted({b for e, b in entry_rows if e == edge}) or [0]
        for period in periods:
            count = entry_loads[edge, period]
            rate = entry_rate_vph(root.find(f"edge[@id='{edge}']"), request)
            cap = entry_caps.get((edge, period), math.floor(len(entry_lanes[edge]) * rate
                                                          * request["interval_s"] / 3600))
            assert count <= cap
            rows.append({"entry": edge, "begin_s": period * request["interval_s"],
                "end_s": (period + 1) * request["interval_s"], "motor_lanes": len(lane_edges[edge]),
                "eligible_lanes": len(entry_lanes[edge]), "minimum_native_lane_length_m": min(r["length"] for r in lane_edges[edge]),
                "road_tier": road_hierarchy_tier(root.find(f"edge[@id='{edge}']").get("type", "")),
                "assigned_vehicles": count, "ceiling_vehicles": cap, "binding": cap > 0 and count == cap,
                "source": "declared_inflow_prior", "vph_per_lane_assumption": rate,
                "short_entry_excluded": not entry_lanes[edge]})
    write_csv(output / "entry-loads.csv", rows, list(rows[0]))
    loads = [{"junction": node, "entry_road": edge, "lane_index": lane, "begin_s": b * request["interval_s"],
              "vehicles_per_15min": n, "equivalent_veh_per_h": n * 3600 / request["interval_s"],
              "above_nominal": n * 3600 / request["interval_s"] > request["nominal_approach_vph"]}
             for (node, edge, lane, b), n in sorted(approach_loads.items())]
    write_csv(output / "approach-lane-loads.csv", loads, list(loads[0]))
    comparisons = [{**expected_rows[i], "begin": str(float(expected_rows[i]["begin"]) + request["comparison_offset_s"]),
                    "end": str(float(expected_rows[i]["end"]) + request["comparison_offset_s"]),
                    "provisional_passages": int(v), "provisional_residual": int(v) - int(observations[i]),
                    "native_E1_measured": False} for i, v in enumerate(response @ np.asarray(proposal["solution"]))]
    write_csv(output / "provisional-field-comparison.csv", comparisons, list(comparisons[0]))
    summary.update(vehicle_count=len(emitted), vehicle_km=proposal["objective_value"],
        passages_per_vehicle=float(observations.sum()) / len(emitted), binding_entry_rows=sum(r["binding"] for r in rows),
        short_entries=[r["entry"] for r in rows if r["short_entry_excluded"]],
        overloaded_approach_rows=[r for r in loads if r["above_nominal"]],
        turnaround_vehicle_passages_by_junction=dict(turnaround_loads))


if __name__ == "__main__":
    _capture_lanes(json.loads(Path(sys.argv[1]).read_text(encoding="utf-8")), Path(sys.argv[2]))
