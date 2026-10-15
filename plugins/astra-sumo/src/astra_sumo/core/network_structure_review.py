"""Trace declared road structures through saved SUMO lane connections."""

from __future__ import annotations

from collections import defaultdict
import xml.etree.ElementTree as ET


_KEYS = ("tunnel", "bridge", "covered", "indoor", "layer", "level", "maxheight", "service")
_NO = {"", "no", "false", "0"}


def _annotations(element: ET.Element) -> dict[str, str]:
    return {p.get("key"): p.get("value", "") for p in element.findall("param") if p.get("key") in _KEYS}


def _structure(tags: dict[str, str]) -> str:
    active = []
    if tags.get("tunnel", "").lower() not in _NO:
        active.append("building_passage" if tags["tunnel"] == "building_passage" else "tunnel")
    if tags.get("bridge", "").lower() not in _NO:
        active.append("bridge")
    if not active and any(tags.get(key, "").lower() not in _NO for key in ("covered", "indoor")):
        active.append("covered")
    if not active and any(tags.get(key, "").lower() not in _NO for key in ("layer", "level")):
        active.append("layered")
    return active[0] if len(active) == 1 else "mixed" if active else "unspecified"


def _combine(states) -> str:
    values = set(states)
    return next(iter(values)) if len(values) == 1 else "transition" if values - {"unspecified"} else "unspecified"


def review_network_structures(root: ET.Element) -> dict:
    edges = {edge.get("id"): edge for edge in root.findall("edge")}
    if None in edges or "" in edges or len(edges) != len(root.findall("edge")):
        raise ValueError("Edge identifiers must be present and unique.")
    ordinary = {key: edge for key, edge in edges.items()
                if edge.get("function", "normal") in {"normal", "connector"}}
    # SUMO connections index the lane sequence. Retain inconsistent XML index attributes for review.
    lane_lookup = {(edge.get("id"), str(index)): lane.get("id")
                   for edge in edges.values() for index, lane in enumerate(edge.findall("lane"))}
    index_mismatches = [{"lane_id": lane.get("id"), "declared_index": lane.get("index"), "sequence_index": index}
                        for edge in edges.values() for index, lane in enumerate(edge.findall("lane"))
                        if lane.get("index") is not None and lane.get("index") != str(index)]
    lane_edges = {lane.get("id"): edge.get("id") for edge in edges.values() for lane in edge.findall("lane")}
    tags = {key: _annotations(edge) for key, edge in ordinary.items()}
    edge_states = {key: _structure(value) for key, value in tags.items()}
    lane_states = {lane.get("id"): _structure(tags[key] | _annotations(lane))
                   for key, edge in ordinary.items() for lane in edge.findall("lane")}
    connections = root.findall("connection")
    continuations = defaultdict(list)
    for connection in connections:
        lane_id = lane_lookup.get((connection.get("from"), connection.get("fromLane")))
        if lane_id:
            continuations[lane_id].append(connection)
    internal_states = defaultdict(set)
    untraced, turnarounds = [], []
    for connection in connections:
        source, target = connection.get("from"), connection.get("to")
        if source not in ordinary or target not in ordinary:
            continue
        source_lane = lane_lookup.get((source, connection.get("fromLane")))
        target_lane = lane_lookup.get((target, connection.get("toLane")))
        state = _combine([lane_states.get(source_lane, "unspecified"), lane_states.get(target_lane, "unspecified")])
        movement = {key: connection.get(key) for key in ("from", "to", "fromLane", "toLane", "via", "dir")}
        if source_lane is None or target_lane is None:
            untraced.append({**movement, "reason": "missing_endpoint_lane"})
            continue
        via, visited = connection.get("via"), set()
        while via:
            if via in visited or via not in lane_edges or lane_edges[via] in ordinary:
                untraced.append({**movement, "unresolved_via_lane": via, "reason": "missing_or_cyclic_internal_lane"})
                break
            visited.add(via)
            internal_states[via].add(state)
            options = [c for c in continuations[via]
                       if c.get("to") == target and c.get("toLane") == connection.get("toLane")]
            if len(options) != 1:
                untraced.append({**movement, "unresolved_via_lane": via,
                                 "reason": "missing_or_ambiguous_internal_continuation"})
                break
            via = options[0].get("via")
        if state != "unspecified" and connection.get("dir") in {"t", "T"}:
            turnarounds.append({**movement, "junction_id": ordinary[source].get("to"),
                               "structure": state, "status": "review_required",
                               "from_annotations": tags[source], "to_annotations": tags[target],
                               "reason": "The structure tag does not establish turnaround legality or available turning space."})
    lane_states.update({key: _combine(value) for key, value in internal_states.items()})
    incident = defaultdict(list)
    for key, edge in ordinary.items():
        for node_id in {edge.get("from"), edge.get("to")}:
            incident[node_id].append(edge_states[key])
    junction_states = {}
    for junction in root.findall("junction"):
        states = incident[junction.get("id")]
        if not states:
            states = [lane_states.get(lane, "unspecified") for lane in junction.get("incLanes", "").split()]
        junction_states[junction.get("id")] = _combine(states)
    return {
        "edge_annotations": {key: value for key, value in sorted(tags.items()) if value},
        "lane_index_mismatches": sorted(index_mismatches, key=lambda row: row["lane_id"]),
        "lane_structures": dict(sorted(lane_states.items())),
        "junction_structures": dict(sorted(junction_states.items())),
        "turnarounds_requiring_review": sorted(turnarounds, key=lambda c: (c["from"], c["to"], c["fromLane"], c["toLane"])),
        "untraced_connections": sorted(untraced, key=lambda c: (c["from"], c["to"], c["fromLane"], c["toLane"])),
        "claim_boundary": "Structures follow saved annotations and explicit via-lane chains. Unspecified does not prove ground level. "
            "Building passages can be at ground level. Tags do not establish geometry, turning permission, or runtime height enforcement.",
    }
