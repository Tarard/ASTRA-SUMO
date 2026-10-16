"""Resolve internal SUMO objects from junction references and movement paths."""

from collections import defaultdict


def _internal(edge):
    return edge.get("id", "").startswith(":") or edge.get("function") in {"internal", "crossing", "walkingarea"}


def _prefix_candidates(identity, nodes):
    result = set()
    if identity.startswith(":"):
        candidate = identity[1:]
        while "_" in candidate:
            candidate = candidate.rsplit("_", 1)[0]
            if candidate in nodes:
                result.add(candidate)
    return result


def resolve_internal_ownership(root, *, extra_junction_ids=()):
    edges = {row.get("id"): row for row in root.findall("edge")}
    lane_edges, keys = {}, {}
    for name, edge in edges.items():
        for index, lane in enumerate(edge.findall("lane")):
            identity = lane.get("id")
            if identity in lane_edges:
                raise ValueError("Internal ownership requires unique lane IDs.")
            lane_edges[identity] = name
            keys[name, index] = identity
    internal = {identity for identity, edge in lane_edges.items() if _internal(edges[edge])}
    normal_nodes = {row.get("id"): row for row in root.findall("junction")
                    if not row.get("id", "").startswith(":") and row.get("type") != "internal"}
    nodes = set(normal_nodes) | set(extra_junction_ids)
    nodes.update(edge.get(end) for edge in edges.values() if not _internal(edge)
                 for end in ("from", "to") if edge.get(end))
    nodes.discard("")
    primary, secondary, adjacent = defaultdict(set), defaultdict(set), {identity: set() for identity in internal}
    for owner, node in normal_nodes.items():
        for identity in node.get("intLanes", "").split():
            if identity in internal:
                primary[identity].add(owner)
        for identity in node.get("incLanes", "").split():
            if identity in internal:
                secondary[identity].add(owner)
    for edge in edges.values():
        ids = [lane.get("id") for lane in edge.findall("lane") if lane.get("id") in internal]
        for identity in ids[1:]:
            adjacent[ids[0]].add(identity)
            adjacent[identity].add(ids[0])
    for connection in root.findall("connection"):
        left, right = edges.get(connection.get("from")), edges.get(connection.get("to"))
        if left is None or right is None:
            continue
        first = keys.get((left.get("id"), int(connection.get("fromLane", "0"))))
        last = keys.get((right.get("id"), int(connection.get("toLane", "0"))))
        via = connection.get("via") or last
        if first not in internal and last not in internal and via in internal and left.get("to") == right.get("from"):
            primary[via].add(left.get("to"))
        if first in internal and via in internal and edges[lane_edges[first]].get("function") == "internal" and edges[lane_edges[via]].get("function") == "internal":
            adjacent[first].add(via)
            adjacent[via].add(first)
    owners, remaining = {}, set(internal)
    while remaining:
        component, pending = set(), [next(iter(remaining))]
        while pending:
            identity = pending.pop()
            if identity not in component:
                component.add(identity)
                pending.extend(adjacent[identity] - component)
        remaining -= component
        candidates = set().union(*(primary[identity] for identity in component))
        if not candidates:
            candidates = set().union(*(secondary[identity] for identity in component))
            if len(candidates) != 1:
                candidates = set().union(*(_prefix_candidates(identity, nodes) for identity in component))
        owner = next(iter(candidates)) if len(candidates) == 1 else None
        owners.update({identity: owner for identity in component})
    edge_owners = {}
    for name, edge in edges.items():
        if not _internal(edge):
            continue
        candidates = {owners[lane.get("id")] for lane in edge.findall("lane") if owners.get(lane.get("id")) is not None}
        if not candidates and not edge.findall("lane"):
            candidates = _prefix_candidates(name, nodes)
        edge_owners[name] = next(iter(candidates)) if len(candidates) == 1 else None
    junction_owners = {}
    for node in root.findall("junction"):
        identity = node.get("id", "")
        if not identity.startswith(":") and node.get("type") != "internal":
            continue
        references = (node.get("incLanes", "") + " " + node.get("intLanes", "")).split()
        candidates = {owners[value] for value in references if owners.get(value) is not None}
        if not candidates and owners.get(identity) is not None:
            candidates = {owners[identity]}
        if not candidates:
            candidates = _prefix_candidates(identity, nodes)
        junction_owners[identity] = next(iter(candidates)) if len(candidates) == 1 else None
    return {"lane": owners, "edge": edge_owners, "junction": junction_owners}
