"""Describe junction visits and ideal lane intentions for boundary demand proposals."""

from collections import defaultdict
from itertools import product

from .road_hierarchy import _path, _paths


def junction_visit_audit(route, edges, connections, junction_groups):
    """Reject repeated junction visits without changing the network or its turnarounds."""
    if not route or len(route) != len(set(route)):
        return {"allowed": False, "reason": "repeated_directed_road", "repeats": [], "passes": []}
    ordinary, official = [], []
    previous_owner, visited_parts = None, set()
    for index, (first, last) in enumerate(zip(route, route[1:])):
        if last not in connections.get(first, ()) or edges[first].to_node != edges[last].from_node:
            raise ValueError("Route contains an unavailable directed passenger connection.")
        node = edges[first].to_node
        owner = junction_groups.get(node)
        event = {"junction": owner or node, "parts": [node], "first_index": index, "last_index": index,
                 "inlet": first, "outlet": last}
        if owner is None:
            ordinary.append(event)
            previous_owner, visited_parts = None, set()
        elif owner == previous_owner and node not in visited_parts:
            official[-1].update(last_index=index, outlet=last)
            official[-1]["parts"].append(node)
            visited_parts.add(node)
        else:
            official.append(event)
            previous_owner, visited_parts = owner, {node}
    passes = sorted(ordinary + official, key=lambda row: row["first_index"])
    grouped = defaultdict(list)
    for row in passes:
        grouped[row["junction"]].append(row)
    repeats = []
    for node, rows in grouped.items():
        if len(rows) < 2:
            continue
        first, last = rows[0]["inlet"], rows[-1]["outlet"]
        shortcut = last in connections.get(first, ())
        repeats.append({"junction": node, "visits": len(rows), "first_inlet": first, "last_outlet": last,
                        "direct_shortcut_exists": shortcut,
                        "classification": "avoidable_tour" if shortcut else "flagged_indirect_turn"})
    return {"allowed": not repeats,
            "reason": "repeated_junction" if repeats else "retained",
            "repeats": repeats, "passes": passes}


def boundary_route_pool(edges, connections, sources, sinks, detector_edges, junction_groups, entry_alternatives=()):
    """Keep shortest boundary and detector-anchored proposals after the declared tour check."""
    roads = {key for key, edge in edges.items() if edge.allows_passenger}
    graph = {key: set(connections.get(key, ())) & roads for key in roads}
    lengths = {key: edges[key].length for key in roads}
    candidates = {}
    starts = {}
    for source in sorted(sources):
        distance, previous = _paths(graph, lengths, source, roads)
        starts[source] = distance, previous
        for sink in sorted(set(sinks) & distance.keys()):
            path = tuple(_path(previous, source, sink))
            if len(path) > 1:
                candidates.setdefault(path, set()).add("boundary_shortest")
    for anchor in sorted(set(detector_edges) & roads):
        distance, previous = _paths(graph, lengths, anchor, roads)
        for source, sink in product(sorted(sources), sorted(set(sinks) & distance.keys())):
            left, before = starts[source]
            if anchor not in left:
                continue
            path = tuple(_path(before, source, anchor) + _path(previous, anchor, sink)[1:])
            if len(path) > 1:
                candidates.setdefault(path, set()).add("detector_anchored")
    for alternative in entry_alternatives:
        replacement, merge = alternative["replacement_entry"], alternative["merge_edge"]
        if replacement not in starts or merge not in starts[replacement][0]:
            raise ValueError("A declared entry alternative cannot reach its merge road.")
        prefix = _path(starts[replacement][1], replacement, merge)
        for path in list(candidates):
            if path[0] == alternative["source_entry"] and merge in path:
                changed = tuple(prefix + list(path[path.index(merge) + 1:]))
                candidates.setdefault(changed, set()).add(f"entry_alternative:{alternative['id']}")
    retained, excluded = [], []
    for index, (path, origins) in enumerate(sorted(candidates.items())):
        audit = junction_visit_audit(path, edges, connections, junction_groups)
        row = {"route_id": f"route_{index:05d}", "edges": path, "source_edge": path[0],
               "sink_edge": path[-1], "distance_m": sum(lengths[e] for e in path),
               "origins": sorted(origins), "junction_audit": audit}
        (retained if audit["allowed"] else excluded).append(row)
    return retained, excluded


def lane_change_reachability(lanes):
    """Respect passenger access and directional lane-change permissions, without a time model."""
    by_index = {int(row["index"]): row for row in lanes}
    closure = {}
    for index in by_index:
        visited, pending = {index}, [index]
        while pending:
            current = pending.pop()
            for offset, permission in ((1, "change_left"), (-1, "change_right")):
                target = current + offset
                if target in by_index and target not in visited and "passenger" in by_index[current][permission]:
                    visited.add(target)
                    pending.append(target)
        closure[index] = visited
    return closure


def lane_feasible_connections(route, lanes_by_edge, lane_connections, *, allowed_departure_lanes=None):
    """Find every connection on a complete permitted lane path, including alternative lane choices."""
    closures = [lane_change_reachability(lanes_by_edge[edge]) for edge in route]
    incoming = [set(closures[0]) if allowed_departure_lanes is None else set(allowed_departure_lanes)]
    outgoing = []
    for i, edge in enumerate(route):
        outgoing.append(set().union(*(closures[i][lane] for lane in incoming[i])))
        if i + 1 < len(route):
            incoming.append({target for lane in outgoing[i]
                for target in lane_connections.get((edge,lane,route[i+1]),())})
    if not incoming[-1]:
        return set()
    possible = set(closures[-1])
    result = set()
    for i in range(len(route)-2,-1,-1):
        pairs = {(lane,target) for lane in outgoing[i]
            for target in lane_connections.get((route[i],lane,route[i+1]),()) if target in possible}
        result.update((route[i],lane,route[i+1],target) for lane,target in pairs)
        exits = {lane for lane,target in pairs}
        possible = {lane for lane,reachable in closures[i].items() if reachable & exits}
    return result


def ideal_lane_profiles(route, lanes_by_edge, lane_connections, fields, *, allow_changes=True, allowed_departure_lanes=None):
    """Return one permitted lane witness per field signature, allowing instantaneous ordinary-road changes.

    This is a structural intention bound. It does not establish natural lane allocation or execute lane guidance.
    """
    closures = {edge: lane_change_reachability(lanes_by_edge[edge]) for edge in route}
    field_at_lane = {row["lane_id"]: row["field_index"] for row in fields}
    states = {(int(lane["index"]), 0): {"entry_lanes": [int(lane["index"])], "exit_lanes": []}
              for lane in lanes_by_edge[route[0]]
              if allowed_departure_lanes is None or int(lane["index"]) in allowed_departure_lanes}
    final = {}
    for position, edge in enumerate(route):
        following = {}
        by_index = {int(row["index"]): row for row in lanes_by_edge[edge]}
        for (incoming, mask), witness in sorted(states.items()):
            for outgoing in sorted(closures[edge][incoming] if allow_changes else {incoming}):
                field = field_at_lane.get(by_index[outgoing]["id"])
                signature = mask if field is None else mask | (1 << field)
                updated = {"entry_lanes": witness["entry_lanes"], "exit_lanes": witness["exit_lanes"] + [outgoing]}
                if position == len(route) - 1:
                    final.setdefault(signature, updated)
                else:
                    for target in sorted(lane_connections.get((edge, outgoing, route[position + 1]), ())):
                        following.setdefault((target, signature), {"entry_lanes": updated["entry_lanes"] + [target],
                                                                  "exit_lanes": updated["exit_lanes"]})
        states = following
    return [{"field_mask": mask, **witness} for mask, witness in sorted(final.items())]
