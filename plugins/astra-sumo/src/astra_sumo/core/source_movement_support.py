"""Source lane-path evidence for supplementary movements after junction joins."""

from __future__ import annotations

import xml.etree.ElementTree as ET
import hashlib
import heapq
import math
from collections import defaultdict, deque
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from .artifact_io import write_json_atomic, write_text_atomic
from .candidate_contracts import file_sha256
from .command_runner import run_command
from .connection_mode_audit import _MOTORIZED_MODES

Pair = tuple[str, int, str, int]


def _modes(element: ET.Element) -> set[str]:
    allowed = set(element.get("allow", "").split())
    denied = set(element.get("disallow", "").split())
    if "all" in denied:
        return set()
    return (set(_MOTORIZED_MODES) if not allowed or "all" in allowed else allowed & _MOTORIZED_MODES) - denied


def _index(root: ET.Element) -> dict[str, Any]:
    edges, lanes, keys = {}, {}, {}
    counts: dict[str, int] = {}
    for edge in root.findall("edge"):
        edge_id = edge.attrib["id"]
        if edge_id in edges:
            raise ValueError("Network contains a duplicate edge id")
        edges[edge_id] = edge
        counts[edge_id] = len(edge.findall("lane"))
        for ordinal, lane in enumerate(edge.findall("lane")):
            lane_id = lane.attrib["id"]
            # Match SUMO and Connection Mode audit: lane order is authoritative.
            key = (edge_id, ordinal)
            if lane_id in lanes or key in keys:
                raise ValueError("Network contains a duplicate lane identity")
            lanes[lane_id] = (edge_id, lane, _modes(lane))
            keys[key] = lane_id
    outgoing: dict[str, list[tuple[str, ET.Element]]] = defaultdict(list)
    incoming: dict[str, set[str]] = defaultdict(set)
    movements = []
    for connection in root.findall("connection"):
        pair = _pair((connection.get("from"), connection.get("fromLane"), connection.get("to"), connection.get("toLane")))
        if (pair[0], pair[1]) not in keys or (pair[2], pair[3]) not in keys:
            raise ValueError("Connection references an unknown lane")
        source = keys[pair[:2]]
        target = keys[pair[2:]]
        via = connection.get("via", "")
        if via and via not in lanes:
            raise ValueError("Connection references an unknown internal lane")
        outgoing[source].append((via or target, connection))
        incoming[via or target].add(source)
        if not _internal(edges[pair[0]]):
            movements.append((pair, source, target, connection))
    nodes = {node.get("id", "") for node in root.findall("junction") if not node.get("id", "").startswith(":")}
    nodes.update(edge.get(key, "") for edge in edges.values() for key in ("from", "to"))
    node_order = sorted(nodes - {""}, key=lambda node: (-len(node), node))
    owners = {lane_id: next((node for node in node_order if lane_id.startswith(f":{node}_")), None) for lane_id, (edge_id, _, _) in lanes.items() if _internal(edges[edge_id])}
    return {"edges": edges, "lanes": lanes, "keys": keys, "counts": counts, "outgoing": outgoing, "incoming": incoming, "movements": movements, "owners": owners}


def _internal(edge: ET.Element) -> bool:
    return edge.get("function") == "internal" or edge.attrib["id"].startswith(":")


def _pair(value: Sequence[Any]) -> Pair:
    if len(value) != 4 or not value[0] or not value[2]:
        raise ValueError("A movement requires two edge ids and two lane indices")
    result = (str(value[0]), int(value[1]), str(value[2]), int(value[3]))
    if result[1] < 0 or result[3] < 0:
        raise ValueError("Lane indices must be nonnegative")
    return result


def _candidate_modes(index, source, target, connection):
    modes = index["lanes"][source][2] & index["lanes"][target][2] & _modes(connection)
    path, visited = [source], set()
    current = connection.get("via") or target
    while current != target:
        if current in visited or not _internal(index["edges"][index["lanes"][current][0]]):
            return set(), []
        visited.add(current)
        path.append(current)
        modes &= index["lanes"][current][2]
        choices = index["outgoing"].get(current, [])
        if len(choices) != 1:
            return set(), []
        current, continuation = choices[0]
        if index["keys"].get((continuation.get("to"), int(continuation.get("toLane", "-1")))) != target:
            return set(), []
        modes &= _modes(continuation)
    return modes, [*path, target]


def _source_paths(index, start, target, members, modes):
    paths: dict[tuple[str, ...], list[str]] = defaultdict(list)
    for mode in sorted(modes & index["lanes"][start][2]):
        queue = deque([(start, (start,))])
        seen = {start}
        while queue:
            current, path = queue.popleft()
            if current == target:
                paths[path].append(mode)
                break
            for nxt, connection in index["outgoing"].get(current, []):
                edge_id, _, allowed = index["lanes"][nxt]
                edge = index["edges"][edge_id]
                interior = (index["owners"].get(nxt) in members if _internal(edge) else edge.get("from") in members and edge.get("to") in members)
                if (nxt == target or interior) and nxt not in seen and mode in allowed and mode in _modes(connection):
                    seen.add(nxt)
                    queue.append((nxt, (*path, nxt)))
    return [{"lane_ids": list(path), "vehicle_classes": classes} for path, classes in sorted(paths.items())]


def _interior_lane_change(index, source, target, members, mode, *, observed=False):
    if source not in index["lanes"] or target not in index["lanes"]:
        return None
    edge_id, lane, allowed = index["lanes"][source]
    target_edge, other, target_allowed = index["lanes"][target]
    edge = index["edges"][edge_id]
    inside = (observed and index["owners"].get(source) in members and index["owners"].get(target) in members
              if _internal(edge) else edge.get("from") in members and edge.get("to") in members)
    if target_edge != edge_id or not inside or mode not in allowed & target_allowed:
        return None
    if _internal(edge):
        # SUMO 1.27.1 MSLaneChanger excludes sibling lanes and unequal lengths.
        # Admit only unique predecessors; ambiguous native choices stay review.
        predecessors = [index["incoming"].get(lane_id, set()) for lane_id in (source, target)]
        if (any(len(values) != 1 for values in predecessors) or predecessors[0] == predecessors[1]
                or float(lane.get("length", "nan")) != float(other.get("length", "nan"))):
            return None
    lane_ids = [row.get("id") for row in edge.findall("lane")]
    delta = lane_ids.index(target) - lane_ids.index(source)
    if abs(delta) != 1:
        return None
    attribute = "changeLeft" if delta == 1 else "changeRight"
    permission = lane.get(attribute)
    if permission is not None and mode not in permission.split() and "all" not in permission.split():
        return None
    shapes = [[tuple(map(float, point.split(",")[:2])) for point in row.get("shape", "").split()] for row in (lane, other)]
    if any(len(shape) < 2 for shape in shapes):
        return None
    vectors = [(shape[-1][0] - shape[0][0], shape[-1][1] - shape[0][1]) for shape in shapes]
    if any(not math.isfinite(value) for vector in vectors for value in vector) or sum(a * b for a, b in zip(*vectors)) <= 0:
        return None
    return {"from_lane": source, "to_lane": target, "edge_id": edge_id, "direction": delta,
            "edge_length_m": float(lane.get("length", "0")),
            "permission_attribute": attribute, "permission_value": permission}


def _source_lane_change_path(index, start, target, members, mode):
    """Propose a path with the fewest interior lane changes, not a run proof."""
    if start not in index["lanes"] or target not in index["lanes"] or mode not in index["lanes"][start][2]:
        return None
    queue = [(0, 0, start, [start], [])]
    visited = set()
    while queue:
        changes, steps, current, path, events = heapq.heappop(queue)
        if current in visited:
            continue
        visited.add(current)
        if current == target:
            return {"lane_ids": path, "lane_changes": events}
        choices = []
        for nxt, connection in index["outgoing"].get(current, []):
            edge_id, _, allowed = index["lanes"][nxt]
            edge = index["edges"][edge_id]
            interior = index["owners"].get(nxt) in members if _internal(edge) else edge.get("from") in members and edge.get("to") in members
            if (nxt == target or interior) and mode in allowed and mode in _modes(connection):
                choices.append((nxt, None))
        edge = index["edges"][index["lanes"][current][0]]
        for lane in edge.findall("lane"):
            event = _interior_lane_change(index, current, lane.get("id"), members, mode)
            if event:
                choices.append((lane.get("id"), event))
        for nxt, event in choices:
            if nxt not in visited:
                heapq.heappush(queue, (changes + int(event is not None), steps + 1, nxt, [*path, nxt], [*events, event] if event else events))
    return None


def _inspect_source_lane_change_run(index, evidence, record):
    paths = {}
    for name in ("routes", "lanechanges", "fcd", "summary", "tripinfo", "vehroute"):
        artifact = record.get("artifacts", {}).get(name)
        if artifact is None:
            return {"status": "review_required", "reason": "source_run_output_missing"}
        path = Path(artifact["path"])
        if not path.is_file() or file_sha256(path) != artifact["sha256"]:
            raise ValueError("Source lane-change output SHA-256 does not match")
        paths[name] = path
    try:
        roots = {name: ET.parse(path).getroot() for name, path in paths.items()}
        mode, start, target = record["vehicle_class"], evidence["start_lane"], evidence["target_lane"]
        members = set(evidence["source_node_ids"])
        vehicles = roots["routes"].findall("vehicle")
        types = roots["routes"].findall("vType")
        input_route = vehicles[0].find("route").get("edges", "").split() if len(vehicles) == 1 else []
        allowed_type = (len(types) == 1 and types[0].attrib == {"id": "permitted", "vClass": mode}
                        and len(vehicles) == 1 and vehicles[0].get("id") == "probe"
                        and vehicles[0].get("type") == "permitted"
                        and set(vehicles[0].attrib) == {"id", "type", "depart", "departLane", "arrivalLane"})
        visits = []
        for step in roots["fcd"]:
            for car in step:
                if car.get("id") == "probe" and (not visits or visits[-1]["lane"] != car.get("lane")):
                    visits.append({"time": float(step.get("time")), "lane": car.get("lane")})
        last_in = [row for row in visits if index["lanes"].get(row["lane"], (None,))[0] == index["lanes"][start][0]]
        first_out = [row for row in visits if index["lanes"].get(row["lane"], (None,))[0] == index["lanes"][target][0]]
        boundary_ok = bool(last_in and first_out and last_in[-1]["lane"] == start and first_out[0]["lane"] == target and last_in[-1]["time"] < first_out[0]["time"])
        events = [dict(row.attrib) for row in roots["lanechanges"].findall("change")]
        event_times = [float(row.get("time", "nan")) for row in events]
        events_ok = (bool(events) and boundary_ok
                     and all(math.isfinite(time) and last_in[-1]["time"] <= time <= first_out[0]["time"] for time in event_times)
                     and event_times == sorted(event_times)
                     and all(row.get("id") == "probe" and row.get("type") == "permitted"
                             and "traci" not in row.get("reason", "").lower()
                             and (change := _interior_lane_change(index, row.get("from"), row.get("to"), members, mode, observed=True))
                             and int(row.get("dir", "0")) == change["direction"]
                             for row in events))
        travelled = roots["vehroute"].findall("vehicle")
        routes = travelled[0].findall("route") if len(travelled) == 1 else []
        actual_route = routes[0].get("edges", "").split() if len(routes) == 1 else []
        actual_external = [edge_id for edge_id in actual_route if edge_id in index["edges"] and not _internal(index["edges"][edge_id])]
        route_ok = bool(input_route) and actual_external == input_route and input_route[0] == index["lanes"][start][0] and input_route[-1] == index["lanes"][target][0]
        for edge_id in actual_route:
            edge = index["edges"].get(edge_id)
            if edge is None:
                route_ok = False
            elif edge_id not in (input_route[0], input_route[-1]):
                inside = all(index["owners"].get(lane.get("id")) in members for lane in edge.findall("lane")) if _internal(edge) else edge.get("from") in members and edge.get("to") in members
                route_ok = route_ok and inside
        summary = dict(roots["summary"][-1].attrib) if len(roots["summary"]) else {}
        trips = roots["tripinfo"].findall("tripinfo")
        terminal_ok = (len(trips) == len(travelled) == len(routes) == len(vehicles) == 1
                       and trips[0].get("id") == travelled[0].get("id") == vehicles[0].get("id") == "probe"
                       and trips[0].get("vaporized", "").strip().lower() in ("", "0", "false")
                       and not any(key.startswith("replaced") for key in routes[0].attrib))
        event_road_times_ok = False
        if terminal_ok:
            depart, arrival = (float(travelled[0].get(key, "nan")) for key in ("depart", "arrival"))
            terminal_ok = (0 <= float(vehicles[0].get("depart", "nan")) <= depart <= arrival <= float(evidence["end_time_s"])
                            and all(abs(float(trips[0].get(key, "nan")) - value) <= 1e-6 for key, value in (("depart", depart), ("arrival", arrival)))
                            and all(depart <= time <= arrival for time in event_times))
            exits = list(map(float, routes[0].get("exitTimes", "").split()))
            route_times_ok = (len(exits) == len(actual_route) and bool(exits)
                              and all(math.isfinite(value) and depart <= value <= arrival for value in exits)
                              and all(a <= b for a, b in zip(exits, exits[1:])) and exits[-1] == arrival)
            # Native exitTimes include internal edges. Inclusive endpoints retain
            # changes in a step that also enters/leaves a very short internal edge.
            # FCD need not sample that edge; a different part of the trip cannot
            # supply its event time. Repeated edges keep separate visit intervals.
            event_road_times_ok = route_times_ok and events_ok and all(
                any(edge == index["lanes"][row["from"]][0] and begin <= time <= end
                    for edge, begin, end in zip(actual_route, [depart, *exits[:-1]], exits))
                for row, time in zip(events, event_times))
        completed = (record.get("command", {}).get("returncode") == 0 and summary.get("arrived") == "1"
                     and all(summary.get(name) == "0" for name in ("collisions", "teleports", "running", "waiting", "discarded"))
                     and terminal_ok)
        proposal = _source_lane_change_path(index, start, target, members, mode)
        checks = {"completed_without_collision_or_teleport": completed, "boundary_lanes_preserved": boundary_ok,
                  "normal_vehicle_parameters": allowed_type, "fixed_route_inside_original_members": route_ok,
                  "observed_permitted_interior_lane_changes": events_ok, "lane_change_times_match_edge_visits": event_road_times_ok,
                  "static_lane_change_path": bool(proposal and proposal["lane_changes"])}
        return {"status": "pass" if all(checks.values()) else "review_required", "checks": checks,
                "summary": summary, "observed_lane_changes": events, "observed_lane_sequence": visits,
                "actual_route_with_internal_edges": actual_route}
    except (ET.ParseError, OSError, ValueError, KeyError, IndexError, AttributeError) as error:
        return {"status": "review_required", "reason": "source_run_output_invalid", "error": str(error)}


def probe_source_interior_lane_changes(
    *, source_net: Path | str, start_lane: str, target_lane: str,
    source_node_ids: Sequence[str], vehicle_classes: Sequence[str], output_dir: Path | str,
    sumo_binary: str = "sumo", seed: int = 104, end_time_s: float = 600,
    step_length_s: float = 0.1, timeout_seconds: float = 120,
) -> dict[str, Any]:
    """Test proposed interior lane changes on an unchanged source network."""
    source = Path(source_net).resolve(strict=True)
    digest = file_sha256(source)
    root = ET.parse(source).getroot()
    index = _index(root)
    members, modes = set(map(str, source_node_ids)), sorted(set(vehicle_classes))
    if not members or not modes or not set(modes) <= _MOTORIZED_MODES:
        raise ValueError("Source node ids and known motor vehicle classes are required")
    if any(lane not in index["lanes"] for lane in (start_lane, target_lane)):
        raise ValueError("Source boundary lane does not exist")
    first, last = (index["edges"][index["lanes"][lane][0]] for lane in (start_lane, target_lane))
    if start_lane == target_lane or first.get("to") not in members or last.get("from") not in members:
        raise ValueError("Source boundary lanes do not belong to the declared junction members")
    if any(not math.isfinite(value) or value <= 0 for value in (end_time_s, step_length_s, timeout_seconds)) or isinstance(seed, bool) or not isinstance(seed, int) or seed < 0:
        raise ValueError("Run duration, step, timeout and seed are invalid")
    destination = Path(output_dir).resolve()
    if destination.exists():
        raise ValueError("output_dir must not already exist")
    destination.mkdir(parents=True)
    evidence = {"schema": "torii.source-interior-lane-change-probes/v1", "source": {"path": str(source), "sha256": digest},
                "original_xml_sha256": hashlib.sha256(ET.tostring(root)).hexdigest(),
                "start_lane": start_lane, "target_lane": target_lane, "source_node_ids": sorted(members),
                "seed": seed, "end_time_s": end_time_s, "step_length_s": step_length_s,
                "time_to_teleport_s": end_time_s,
                "waiting_policy": "finite_horizon_wait_with_normal_completion_required",
                "sumo_version": run_command([sumo_binary, "--version"]).to_dict(), "results": []}
    for mode in modes:
        proposal = _source_lane_change_path(index, start_lane, target_lane, members, mode)
        record = {"vehicle_class": mode, "proposal": proposal, "status": "review_required"}
        if not proposal or not proposal["lane_changes"]:
            record["reason"] = "no_permitted_interior_lane_change_proposal"
        else:
            directory = destination / mode
            directory.mkdir()
            route = []
            for lane_id in proposal["lane_ids"]:
                edge_id = index["lanes"][lane_id][0]
                if not _internal(index["edges"][edge_id]) and (not route or route[-1] != edge_id):
                    route.append(edge_id)
            routes = ET.Element("routes")
            ET.SubElement(routes, "vType", id="permitted", vClass=mode)
            vehicle = ET.SubElement(routes, "vehicle", id="probe", type="permitted", depart="0", departLane=str(first.findall("lane").index(index["lanes"][start_lane][1])), arrivalLane=str(last.findall("lane").index(index["lanes"][target_lane][1])))
            ET.SubElement(vehicle, "route", edges=" ".join(route))
            files = {name: directory / f"{name}.xml" for name in ("routes", "lanechanges", "fcd", "summary", "tripinfo", "vehroute")}
            write_text_atomic(files["routes"], ET.tostring(routes, encoding="unicode"))
            command = [sumo_binary, "--net-file", str(source), "--route-files", str(files["routes"]), "--begin", "0", "--end", str(end_time_s), "--step-length", str(step_length_s), "--seed", str(seed), "--time-to-teleport", str(end_time_s), "--collision.check-junctions", "true", "--no-step-log", "true", "--vehroute-output.internal", "true", "--vehroute-output.exit-times", "true"]
            for name, option in (("lanechanges", "--lanechange-output"), ("fcd", "--fcd-output"), ("summary", "--summary-output"), ("tripinfo", "--tripinfo-output"), ("vehroute", "--vehroute-output")):
                command.extend((option, str(files[name])))
            result = run_command(command, cwd=directory, timeout_seconds=timeout_seconds)
            write_text_atomic(directory / "stdout.txt", result.stdout)
            write_text_atomic(directory / "stderr.txt", result.stderr)
            record.update(command=result.to_dict(), artifacts={name: {"path": str(path), "sha256": file_sha256(path)} for name, path in files.items() if path.is_file()})
            record.update(_inspect_source_lane_change_run(index, evidence, record))
        evidence["results"].append(record)
    evidence["source_immutable"] = file_sha256(source) == digest
    evidence["status"] = "pass" if evidence["source_immutable"] and all(row["status"] == "pass" for row in evidence["results"]) else "review_required"
    evidence["claim_boundary"] = "Single-vehicle source route and normal interior lane-change evidence, separately tested by vehicle class. No field demand, lane-change capacity, or exact observation of every short internal lane is claimed. Source signals, permissions and driving behavior are unchanged. Teleport waiting is finite and equals the declared horizon; unfinished vehicles still fail."
    evidence["report_file"] = str(destination / "report.json")
    write_json_atomic(Path(evidence["report_file"]), evidence, sort_keys=True)
    return evidence


def _verified_lane_change_evidence(original_root, index, evidence):
    verified = []
    expected = hashlib.sha256(ET.tostring(original_root)).hexdigest()
    for report in evidence:
        if report.get("schema") != "torii.source-interior-lane-change-probes/v1" or report.get("original_xml_sha256") != expected:
            raise ValueError("Source lane-change evidence XML SHA-256 does not match")
        source = report["source"]
        path = Path(source["path"])
        if not path.is_file() or file_sha256(path) != source["sha256"] or hashlib.sha256(ET.tostring(ET.parse(path).getroot())).hexdigest() != expected:
            raise ValueError("Source lane-change evidence network SHA-256 does not match")
        for record in report["results"]:
            checked = _inspect_source_lane_change_run(index, report, record) if record.get("status") == "pass" else {}
            if checked.get("status") == "pass":
                verified.append({"start_lane": report["start_lane"], "target_lane": report["target_lane"],
                                 "source_node_ids": report["source_node_ids"], "vehicle_class": record["vehicle_class"],
                                 "source_sha256": source["sha256"], "report_file": report.get("report_file"),
                                 "observed_lane_changes": checked["observed_lane_changes"], "artifacts": record["artifacts"]})
    return verified


def audit_source_movement_support(
    original_root: ET.Element,
    candidate_root: ET.Element,
    *,
    group_source_node_ids: Mapping[str, Sequence[str]],
    official_required_pairs: Sequence[Sequence[Any]],
    current_lane_to_original_lane: Mapping[str, str | None] | None = None,
    identity_changed_edges: Sequence[str] = (),
    source_lane_change_evidence: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    """Suggest only redundant, source-unsupported target-lane fanout removals.

    Official movements are never removal candidates. A genuinely new exit,
    an uncertain lane identity, or a permission mismatch stays under review.
    The caller applies suggestions to a separate candidate and verifies it.
    """
    original, candidate = _index(original_root), _index(candidate_root)
    lane_change_evidence = _verified_lane_change_evidence(original_root, original, source_lane_change_evidence)
    groups = {str(key): set(map(str, values)) for key, values in group_source_node_ids.items()}
    if any(not key or not values for key, values in groups.items()):
        raise ValueError("Each joined junction requires original node ids")
    official = {_pair(value) for value in official_required_pairs}
    aliases = dict(current_lane_to_original_lane or {})
    changed = set(identity_changed_edges)
    for current, previous in aliases.items():
        if current not in candidate["lanes"] or previous is not None and previous not in original["lanes"]:
            raise ValueError("Lane alias references an unknown lane")

    def origin(lane_id):
        if lane_id in aliases:
            return aliases[lane_id]
        edge = candidate["lanes"][lane_id][0]
        if edge in changed or candidate["counts"][edge] != original["counts"].get(edge):
            return None
        return lane_id if lane_id in original["lanes"] else None

    supported, unresolved, unsupported, supported_alternatives = [], [], [], []
    checked = 0
    for pair, source, target, connection in sorted(candidate["movements"], key=lambda row: row[0]):
        owner = candidate["edges"][pair[0]].get("to")
        if owner not in groups:
            continue
        protected = pair in official
        modes, current_path = _candidate_modes(candidate, source, target, connection)
        if not modes and current_path:
            continue  # Non-motorized movements are outside this helper's scope.
        checked += int(not protected)
        record = {"connection": list(pair), "junction_id": owner, "candidate_vehicle_classes": sorted(modes), "candidate_lane_path": current_path}
        previous_source, previous_target = origin(source), origin(target)
        if not modes or previous_source is None or previous_target is None:
            if not protected:
                unresolved.append({**record, "reason": "candidate_path_or_original_lane_identity_unproved"})
            continue
        source_edge = original["edges"][original["lanes"][previous_source][0]]
        target_edge = original["edges"][original["lanes"][previous_target][0]]
        if previous_source == previous_target or source_edge.get("to") not in groups[owner] or target_edge.get("from") not in groups[owner]:
            if not protected:
                unresolved.append({**record, "reason": "original_boundary_ownership_or_positions_unproved"})
            continue
        witnesses = _source_paths(original, previous_source, previous_target, groups[owner], modes)
        proved_modes = {mode for witness in witnesses for mode in witness["vehicle_classes"]}
        changes = [row for row in lane_change_evidence if row["start_lane"] == previous_source and row["target_lane"] == previous_target and set(row["source_node_ids"]) == groups[owner] and row["vehicle_class"] in modes]
        proved_modes.update(row["vehicle_class"] for row in changes)
        record.update({"original_from_lane": previous_source, "original_to_lane": previous_target, "source_paths": witnesses, "supported_vehicle_classes": sorted(proved_modes), "source_lane_change_evidence": changes})
        if proved_modes == modes:
            supported_alternatives.append(record)
            if not protected:
                supported.append(record)
        elif protected:
            continue
        elif proved_modes:
            unresolved.append({**record, "reason": "candidate_permissions_exceed_proved_source_permissions"})
        else:
            unsupported.append(record)

    redundant = []
    for record in unsupported:
        source, source_lane, target, _ = record["connection"]
        alternatives = [row for row in supported_alternatives if row["connection"][:3] == [source, source_lane, target] and set(record["candidate_vehicle_classes"]) <= set(row["supported_vehicle_classes"])]
        if alternatives:
            redundant.append({**record, "reason": "unsupported_target_lane_with_source_backed_same_exit_alternative", "source_supported_alternatives": [row["connection"] for row in alternatives]})
        else:
            unresolved.append({**record, "reason": "no_source_backed_same_exit_alternative"})
    return {
        "schema": "torii.source-movement-support/v1",
        "status": "review_required" if unresolved or redundant else "pass",
        "checked_extra_count": checked,
        "source_backed_extra": supported,
        "unsupported_redundant_pairs": [row["connection"] for row in redundant],
        "unsupported_redundant_extra": redundant,
        "unresolved": sorted(unresolved, key=lambda row: row["connection"]),
        "policy": "Preserve official movements and source-supported supplementary paths; never delete a new exit without a source-backed same-exit alternative. Interior lane changes require hash-bound source runs for each vehicle class. Paths or lane changes outside the declared original junction members are not support.",
    }
