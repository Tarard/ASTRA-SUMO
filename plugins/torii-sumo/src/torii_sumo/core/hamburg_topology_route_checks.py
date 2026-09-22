"""Check fixed named-road endpoints and upstream access to constructed pockets."""

from __future__ import annotations

import json
import math
from collections import defaultdict, deque
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

import sumolib

from .artifact_io import write_json_atomic, write_text_atomic
from .candidate_contracts import file_sha256
from .command_runner import run_command
from .routeability_audit import _inspect_vehicle_routes, inspect_routeability_outputs
from .source_movement_support import _candidate_modes, _index, _internal


def _network(path: Path) -> dict[str, Any]:
    root = ET.parse(path).getroot()
    index = _index(root)
    net = sumolib.net.readNet(str(path), withInternal=True)
    edges = {key: edge for key, edge in index["edges"].items() if not _internal(edge)}
    movements = []
    for pair, start, target, connection in index["movements"]:
        modes, chain = _candidate_modes(index, start, target, connection)
        if modes and chain:
            movements.append((pair, modes, chain))
    location = root.find("location")
    offset = tuple(map(float, location.get("netOffset", "0,0").split(","))) if location is not None else (0.0, 0.0)
    return {"path": path, "net": net, "index": index, "edges": edges, "movements": movements,
            "offset": offset, "projection": location.get("projParameter", "!") if location is not None else "!"}


def _boundary_points(network, endpoints, lanes):
    return [[float(value - offset) for value, offset in zip(network["net"].getLane(f"{edge}_{lane}").getShape()[position], network["offset"])]
            for edge, lane, position in ((endpoints["from_edge"], lanes[0], 0), (endpoints["to_edge"], lanes[1], -1))]


def _named_edges(network, road_names):
    return {key for key, edge in network["edges"].items()
            if (edge.get("name") or next((p.get("value", "") for p in edge.findall("param") if p.get("key") == "name"), "")) in road_names
            and any(network["net"].getLane(lane.get("id")).allows("passenger") for lane in edge.findall("lane"))}


def _source_endpoints(network, named, groups, ordered):
    centers = []
    for node_id in (ordered[0], ordered[-1]):
        coordinates = [network["net"].getNode(member).getCoord() for member in groups[node_id]]
        centers.append(tuple(sum(point[i] for point in coordinates) / len(coordinates) for i in (0, 1)))
    axis = tuple(b - a for a, b in zip(*centers))
    length = math.hypot(*axis)
    if not math.isfinite(length) or length <= 0:
        raise ValueError("selected end intersections do not define a direction")
    axis = tuple(value / length for value in axis)
    directed = {}
    for edge in named:
        shape = network["net"].getEdge(edge).getShape()
        if len(shape) >= 2 and sum((b - a) * v for a, b, v in zip(shape[0], shape[-1], axis)) > 1e-6:
            directed[edge] = (sum(a * v for a, v in zip(shape[0], axis)), sum(a * v for a, v in zip(shape[-1], axis)))
    if not directed:
        raise ValueError("source has no named road in the selected direction")
    members = set().union(*groups.values())
    first_center, last_center = (sum(a * v for a, v in zip(center, axis)) for center in centers)
    starts = [key for key, span in directed.items() if span[0] < first_center and network["edges"][key].get("from") not in members]
    ends = [key for key, span in directed.items() if span[1] > last_center and network["edges"][key].get("to") not in members]
    pairs = sorted(((directed[end][1] - directed[start][0], start, end) for start in starts for end in ends if start != end),
                   key=lambda row: (-row[0], row[1], row[2]))
    report = {"status": "review_required", "selection_axis": list(axis), "candidate_pair_count": len(pairs),
              "checked_pair_count": 0, "pair_check_limit": 128, "excluded_source_pairs": [],
              "selection_basis": "greatest source-only projected span with a permitted named-road path through the complete selected order",
              "unselected_roads_policy": "Excluded pairs remain recorded; this narrow mainline check does not replace whole-network OD checks."}
    winners = []
    # ponytail: check at most 128 geometrically ranked source pairs; larger or ambiguous corridors require reviewed scope.
    for span, start, end in pairs:
        if winners and span < winners[0]["projected_span_m"] - 1e-6:
            break
        if report["checked_pair_count"] == report["pair_check_limit"]:
            return {**report, "reason": "source_endpoint_pair_check_limit_reached"}
        endpoints = {"from_edge": start, "to_edge": end, "projected_span_m": span}
        path = _mainline_path(network, named, groups, ordered, endpoints)
        report["checked_pair_count"] += 1
        if path["status"] == "pass":
            winners.append({**endpoints, "source_path": path})
        else:
            report["excluded_source_pairs"].append({**endpoints, "reason": path["reason"]})
    if len(winners) == 1:
        return {**report, **winners[0], "status": "pass"}
    if winners:
        return {**report, "reason": "equally_outer_source_endpoint_pairs", "equally_ranked_pairs": winners}
    return {**report, "reason": "no_source_endpoint_pair_traverses_selected_order"}


def _mainline_path(network, named, groups, ordered, endpoints):
    start, end = endpoints["from_edge"], endpoints["to_edge"]
    if start not in named or end not in named:
        return {"status": "review_required", "reason": "fixed_named_road_endpoint_missing_or_forbidden"}
    owner = {member: node for node, members in groups.items() for member in members}
    if len(owner) != sum(map(len, groups.values())):
        return {"status": "review_required", "reason": "selected_intersection_groups_overlap"}
    adjacency = defaultdict(list)
    for pair, modes, _ in network["movements"]:
        if pair[0] in named and pair[2] in named and "passenger" in modes:
            adjacency[pair[0]].append(pair)

    def advance(edge, progress):
        for endpoint in ("from", "to"):
            touched = owner.get(network["edges"][edge].get(endpoint))
            if touched is None or progress and touched == ordered[progress - 1]:
                continue
            if progress == len(ordered) or touched != ordered[progress]:
                return None
            progress += 1
        return progress

    progress = advance(start, 0)
    if progress is None:
        return {"status": "review_required", "reason": "source_order_mismatch_at_fixed_start"}
    state = (start, progress)
    queue, parents = deque([state]), {state: None}
    found = None
    while queue:
        state = queue.popleft()
        current, progress = state
        if current == end and progress == len(ordered):
            found = state
            break
        for pair in sorted(adjacency[current]):
            next_progress = advance(pair[2], progress)
            successor = (pair[2], next_progress)
            if next_progress is not None and successor not in parents:
                parents[successor] = (state, pair)
                queue.append(successor)
    if found is None:
        return {"status": "review_required", "reason": "no_permitted_named_path_through_selected_order"}
    pairs = []
    while parents[found] is not None:
        found, pair = parents[found]
        pairs.append(pair)
    pairs.reverse()
    return {"status": "pass", "route": [start, *(pair[2] for pair in pairs)],
            "start_lane": pairs[0][1], "arrival_lane": pairs[-1][3],
            "connection_lane_pairs": [list(pair) for pair in pairs], "required_node_order": ordered,
            "permission_scope": "passenger lanes, every connection and its complete internal lane chain"}


def _run_probe(network, directory, *, route, start_lane, arrival_lane, vehicle_class,
               pockets=(), sumo_binary, seed, end_time_s, timeout_seconds):
    directory.mkdir()
    root = ET.Element("routes")
    length = 12 if vehicle_class == "bus" else 5
    ET.SubElement(root, "vType", id="permitted", vClass=vehicle_class, length=str(length))
    vehicle = ET.SubElement(root, "vehicle", id="probe", type="permitted", depart="0", departSpeed="0",
                            departLane=str(start_lane), arrivalLane=str(arrival_lane))
    ET.SubElement(vehicle, "route", edges=" ".join(route))
    for lane in pockets:
        lane_length = network["net"].getLane(lane).getLength()
        ET.SubElement(vehicle, "stop", lane=lane, endPos=str(max(lane_length - 1, lane_length / 2)), duration="1")
    route_file = directory / "routes.rou.xml"
    ET.indent(root)
    write_text_atomic(route_file, ET.tostring(root, encoding="unicode", xml_declaration=True))
    route_hash = file_sha256(route_file)
    outputs = {name: directory / f"{name}.xml" for name in ("summary", "tripinfo", "vehroute", "fcd", "lanechanges", "stops")}
    command = [sumo_binary, "--net-file", str(network["path"]), "--route-files", str(route_file),
               "--begin", "0", "--end", str(end_time_s), "--step-length", "0.1", "--seed", str(seed),
               "--time-to-teleport", str(end_time_s), "--collision.check-junctions", "true", "--no-step-log", "true",
               "--vehroute-output.exit-times", "true", "--fcd-output.attributes", "id,lane,speed"]
    for flag, name in (("summary-output", "summary"), ("tripinfo-output", "tripinfo"), ("vehroute-output", "vehroute"),
                       ("fcd-output", "fcd"), ("lanechange-output", "lanechanges"), ("stop-output", "stops")):
        command.extend([f"--{flag}", str(outputs[name])])
    result = run_command(command, cwd=directory, timeout_seconds=timeout_seconds)
    write_text_atomic(directory / "stdout.txt", result.stdout)
    write_text_atomic(directory / "stderr.txt", result.stderr)
    inspection = inspect_routeability_outputs(summary_path=outputs["summary"], tripinfo_path=outputs["tripinfo"], expected_vehicle_count=1)
    completed_routes = _inspect_vehicle_routes(route_file, outputs["vehroute"], 1, end_time_s, outputs["tripinfo"])
    summary = inspection["summary"]
    counts_ok = all(summary.get(k) == 1 for k in ("loaded", "inserted", "arrived")) and all(summary.get(k) == 0 for k in ("running", "waiting", "teleports", "collisions", "discarded"))
    visits, changes, stops, error = [], [], [], None
    try:
        for frame in ET.parse(outputs["fcd"]).getroot():
            for car in frame:
                if car.get("id") == "probe" and (not visits or visits[-1]["lane"] != car.get("lane")):
                    visits.append({"time": float(frame.get("time")), "lane": car.get("lane")})
        changes = [dict(row.attrib) for row in ET.parse(outputs["lanechanges"]).getroot().findall("change") if row.get("id") == "probe"]
        stops = [dict(row.attrib) for row in ET.parse(outputs["stops"]).getroot().findall("stopinfo") if row.get("id") == "probe"]
    except (OSError, ET.ParseError, TypeError, ValueError) as exc:
        error = str(exc)
    observed = [row["lane"] for row in visits]
    positions = [observed.index(lane) if lane in observed else -1 for lane in pockets]
    pocket_order = all(p >= 0 for p in positions) and positions == sorted(set(positions))
    native_change = any(row.get("to") in pockets and row.get("from", "").rsplit("_", 1)[0] == row.get("to", "").rsplit("_", 1)[0]
                        and "traci" not in row.get("reason", "").lower() for row in changes)
    try:
        stops_ok = all(any(row.get("lane") == lane and float(row.get("ended", "-1")) - float(row.get("started", "0")) >= 1 - 1e-6 for row in stops) for lane in pockets)
    except (TypeError, ValueError) as exc:
        stops_ok, error = False, str(exc)
    start_observed = bool(observed) and observed[0] == f"{route[0]}_{start_lane}"
    passed = (result.returncode == 0 and inspection["status"] == "pass" and counts_ok and completed_routes["status"] == "pass"
              and error is None and start_observed and (not pockets or pocket_order and native_change and stops_ok)
              and file_sha256(route_file) == route_hash)
    report = {"status": "pass" if passed else "review_required", "route": route, "start_lane": start_lane,
              "arrival_lane": arrival_lane, "vehicle_class": vehicle_class, "expected_pocket_chain": list(pockets),
              "native_lane_change_observed": native_change, "pocket_stops_completed": stops_ok,
              "observed_lane_sequence": visits, "lane_changes": changes, "stops": stops, "parse_error": error,
              "inspection": inspection, "vehicle_routes": completed_routes, "command": result.to_dict(),
              "route_sha256": route_hash, "outputs": {name: {"path": str(path), "sha256": file_sha256(path) if path.is_file() else None} for name, path in outputs.items()}}
    write_json_atomic(directory / "result.json", report)
    return report


def _pocket_requests(manifest, network):
    groups = defaultdict(list)
    for row in (manifest.get("approach_rebuild") or {}).get("approaches", []):
        groups[(row["node_id"], row["intersection_part"], row["approach"])].append(row)
    required = manifest.get("official_connection_audit", {}).get("required", [])
    requests = []
    for identity, rows in sorted(groups.items()):
        rows.sort(key=lambda row: row["section_index"])
        first, last = rows[0], rows[-1]
        for official in sorted({lane for row in rows for lane in row["pocket_lane_ids"]}):
            record = {"node_id": identity[0], "intersection_part": identity[1], "approach": identity[2], "official_lane_id": official}
            try:
                movements = [r for r in required if r["node_id"] == identity[0] and r["intersection_part"] == identity[1] and r["ingress_lane_id"] == official]
                choices = []
                for movement in movements:
                    pair = tuple(movement["sumo_connection"])
                    for actual, modes, _ in network["movements"]:
                        if pair == actual:
                            choices.extend((mode, pair) for mode in ("passenger", "bus", "delivery")
                                           if mode in modes and (movement.get("allowed_vehicle_classes") is None or mode in movement["allowed_vehicle_classes"]))
                if not choices:
                    raise ValueError("pocket has no permitted official outlet")
                mode, pair = choices[0]
                route = ([first["upstream_edge_id"]] if first.get("upstream_edge_id") else []) + list(last["chain_edge_ids"]) + [pair[2]]
                start_lane = first["lane_attribute_inheritance"][official]["source_lane_index"]
                continuing = set(first["source_lane_to_downstream_lane"].values())
                if first.get("upstream_edge_id") is None:
                    incoming = sorted(pair for pair, modes, _ in network["movements"] if mode in modes and pair[2] == route[0]
                                      and pair[3] in continuing and pair[0] not in route)
                    if not incoming:
                        raise ValueError("no permitted upstream entry into a continuing lane")
                    route.insert(0, incoming[0][0])
                    start_lane = incoming[0][1]
                while network["net"].getLane(f"{route[0]}_{start_lane}").getLength() < (14.5 if mode == "bus" else 7.5):
                    incoming = sorted(pair for pair, modes, _ in network["movements"] if mode in modes and pair[2:] == (route[0], start_lane) and pair[0] not in route)
                    if not incoming:
                        raise ValueError("upstream departure lane cannot contain the probe vehicle and its minimum gap")
                    route.insert(0, incoming[0][0])
                    start_lane = incoming[0][1]
                pockets = [f"{row['downstream_edge_id']}_{row['official_lane_indices'][official]}" for row in rows if official in row["official_lane_indices"]]
                pocket_edges = [network["net"].getLane(lane).getEdge().getID() for lane in pockets]
                indices = [route.index(edge) for edge in pocket_edges]
                if indices != sorted(set(indices)) or route[0] in pocket_edges:
                    raise ValueError("pocket sections do not form an ordered upstream route")
                if any(not network["net"].getLane(lane).allows(mode) for lane in pockets):
                    raise ValueError("pocket lane does not permit the selected vehicle class")
                if any(not any(p[0] == a and p[2] == b and mode in modes for p, modes, _ in network["movements"]) for a, b in zip(route, route[1:])):
                    raise ValueError("pocket route has a forbidden or missing connection")
                record.update(status="ready", route=route, start_lane=start_lane, arrival_lane=pair[3], vehicle_class=mode, pockets=pockets)
            except (KeyError, IndexError, TypeError, ValueError) as error:
                record.update(status="review_required", reason=str(error))
            requests.append(record)
    return requests


def run_hamburg_topology_route_checks(
    candidate_manifest: Path | str, output_dir: Path | str, *, road_names: list[str],
    ordered_node_ids: list[str], sumo_binary: str = "sumo", seed: int = 104,
    end_time_s: int = 600, timeout_seconds: float = 120,
) -> dict[str, Any]:
    """Run paired named-mainline checks and native access checks for new pockets."""
    if (not road_names or any(not isinstance(name, str) or not name.strip() for name in road_names)
            or len(ordered_node_ids) < 2 or any(not isinstance(node, str) or not node for node in ordered_node_ids)
            or len(set(ordered_node_ids)) != len(ordered_node_ids)):
        raise ValueError("road_names and at least two unique ordered_node_ids are required")
    if isinstance(seed, bool) or not isinstance(seed, int) or seed < 0:
        raise ValueError("seed must be a nonnegative integer")
    if any(isinstance(value, bool) or not math.isfinite(value) or value <= 0 for value in (end_time_s, timeout_seconds)):
        raise ValueError("run duration and timeout must be finite and positive")
    manifest_path = Path(candidate_manifest).resolve(strict=True)
    hashes = {manifest_path: file_sha256(manifest_path)}
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("schema") != "torii.hamburg-aerial-corridor-candidate/v1":
        raise ValueError("candidate manifest schema is invalid")
    network_paths = {}
    for role, artifact in (("source", manifest["inputs"]["source_net"]), ("candidate", manifest["artifacts"]["network"])):
        path = Path(artifact["path"])
        path = (path if path.is_absolute() else manifest_path.parent / path).resolve(strict=True)
        digest = file_sha256(path)
        if digest != str(artifact["sha256"]).lower():
            raise ValueError(f"{role} network SHA-256 does not match its manifest")
        hashes[path] = digest
        network_paths[role] = path
    destination = Path(output_dir).resolve()
    if destination.exists():
        raise ValueError("output_dir must not already exist")
    destination.mkdir(parents=True)
    groups = {role: defaultdict(set) for role in network_paths}
    for part in manifest["physical_parts"]:
        groups["source"][part["node_id"]].update(part["source_node_ids"])
        groups["candidate"][part["node_id"]].add(part["join_id"])
    networks = {"source": _network(network_paths["source"])}
    named = {"source": _named_edges(networks["source"], set(road_names))}
    selections = []
    for label, ordered in (("forward", ordered_node_ids), ("reverse", list(reversed(ordered_node_ids)))):
        try:
            if any(not groups["source"].get(node) for node in ordered):
                raise ValueError("selected source intersection groups are missing")
            selection = _source_endpoints(networks["source"], named["source"], groups["source"], ordered)
        except (KeyError, IndexError, TypeError, ValueError) as error:
            selection = {"status": "review_required", "reason": str(error)}
        selections.append((label, ordered, selection))
    # Both directions are frozen before reading candidate geometry or connectivity.
    networks["candidate"] = _network(network_paths["candidate"])
    named["candidate"] = _named_edges(networks["candidate"], set(road_names))
    run_options = {"sumo_binary": sumo_binary, "seed": seed, "end_time_s": end_time_s, "timeout_seconds": timeout_seconds}
    directions = []
    for label, ordered, selection in selections:
        row = {"direction": label, "required_node_order": ordered, "status": "review_required"}
        try:
            if selection["status"] != "pass":
                row.update(reason=selection["reason"], endpoint_selection=selection)
                directions.append(row)
                continue
            if any(not groups["candidate"].get(node) for node in ordered):
                raise ValueError("selected candidate intersection groups are missing")
            endpoints = {key: value for key, value in selection.items() if key != "source_path"}
            row["endpoints"] = endpoints
            fixed_lanes = None
            for role, network in networks.items():
                proposal = dict(selection["source_path"]) if role == "source" else _mainline_path(network, named[role], groups[role], ordered, endpoints)
                proposal["path_status"] = proposal["status"]
                row[role] = proposal
                if proposal["status"] == "pass":
                    if role == "source":
                        fixed_lanes = (proposal["start_lane"], proposal["arrival_lane"])
                    if fixed_lanes is None:
                        proposal.update(status="review_required", reason="source_did_not_establish_boundary_lanes")
                        continue
                    start_lane, arrival_lane = fixed_lanes
                    if not all(network["net"].getLane(f"{edge}_{lane}").allows("passenger") for edge, lane in ((endpoints["from_edge"], start_lane), (endpoints["to_edge"], arrival_lane))):
                        proposal.update(status="review_required", reason="fixed_boundary_lane_permission_changed")
                        continue
                    points = _boundary_points(network, endpoints, fixed_lanes)
                    if role == "source":
                        endpoints.update(from_lane=start_lane, to_lane=arrival_lane, projected_boundary_points=points)
                    elif network["projection"] != networks["source"]["projection"] or any(
                            not math.isfinite(distance := math.dist(a, b)) or distance > 0.1
                            for a, b in zip(points, endpoints["projected_boundary_points"])):
                        proposal.update(status="review_required", reason="fixed_boundary_geometry_changed", observed_boundary_points=points)
                        continue
                    proposal.update(fixed_start_lane=start_lane, fixed_arrival_lane=arrival_lane)
                    probe = _run_probe(network, destination / f"mainline-{label}-{role}", route=proposal["route"], start_lane=start_lane,
                                       arrival_lane=arrival_lane, vehicle_class="passenger", **run_options)
                    proposal.update(status=probe["status"], probe=probe)
            if row.get("source", {}).get("path_status") == "pass" and row.get("candidate", {}).get("status") == "pass":
                row["status"] = "pass"
        except (KeyError, IndexError, TypeError, ValueError) as error:
            row["reason"] = str(error)
        directions.append(row)
    pockets = []
    for number, request in enumerate(_pocket_requests(manifest, networks["candidate"])):
        if request["status"] == "ready":
            probe = _run_probe(networks["candidate"], destination / f"pocket-{number:03d}",
                               **{key: request[key] for key in ("route", "start_lane", "arrival_lane", "vehicle_class", "pockets")}, **run_options)
            request.update(probe)
        pockets.append(request)
    unchanged = all(path.is_file() and file_sha256(path) == digest for path, digest in hashes.items())
    mainline = {"status": "pass" if all(row["status"] == "pass" for row in directions) else "review_required", "directions": directions,
                "source_runtime_policy": "Source paths determine the fixed endpoints and order. Source run failures remain visible; candidate acceptance requires its own normal completion."}
    pocket_access = {"status": ("pass" if all(row["status"] == "pass" for row in pockets) else "review_required") if pockets else "not_applicable",
                     "total": len(pockets), "passed": sum(row["status"] == "pass" for row in pockets), "results": pockets}
    report = {"schema": "torii.hamburg-topology-route-checks/v1", "status": "pass" if unchanged and mainline["status"] == "pass" and pocket_access["status"] in ("pass", "not_applicable") else "review_required",
              "road_names": road_names, "ordered_node_ids": ordered_node_ids, "mainline": mainline, "pocket_access": pocket_access,
              "inputs_unchanged": unchanged, "inputs": [{"path": str(path), "sha256": digest} for path, digest in hashes.items()],
              "seed": seed, "end_time_s": end_time_s, "boundary_position_tolerance_m": 0.1,
              "claim_boundary": "Named-road endpoints are selected by source-only geometry and reachability before the candidate is read. Excluded source pairs remain visible. Candidate paths use the same endpoint lanes, positions and selected intersection order. Pocket probes start upstream, use one-second stops, and require observed native lane changes. No candidate-driven endpoint changes or forced lane changes are used."}
    report_file = destination / "summary.json"
    write_json_atomic(report_file, report)
    return {**report, "report_file": str(report_file)}
