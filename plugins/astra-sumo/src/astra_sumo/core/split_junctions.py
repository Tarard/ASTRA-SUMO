"""Split junctions of an OSM-built SUMO net: the node groups to join and the turn restrictions a join changes.

OSM often maps one intersection as several nodes a few metres apart, for example the two carriageways of a dual
road. netconvert gives each node its own junction polygon; when the polygons cover the edge between two nodes, that
edge keeps a lane length of 0.1-1 m and holds no vehicle. A vehicle that turns into it stops inside the first
junction, and the foe rules of two separate junctions do not see each other's links: in central Dresden these edges
carried lane traps, a turning deadlock and the collisions of multi-node signal centres. Joining such a group into
one junction (a <join> row in the node file) gives it one polygon and one set of foe relations.
"""
from __future__ import annotations

import gzip
import math
import re
import xml.etree.ElementTree as ET
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# A vehicle edge whose every lane is shorter than SPLIT_EDGE_M after netconvert cut the junction polygons links two
# parts of one junction. A group that had to take in the rest of a multi-node controller and is then wider than
# SPLIT_SPAN_M is left alone.
SPLIT_EDGE_M = 1.0
SPLIT_SPAN_M = 30.0
_WAY_ID = re.compile(r"-?(\d+)")


@dataclass
class NetView:
    """The parts of a SUMO net the split-junction rules read."""

    edges: dict[str, tuple[str, str, list[float], bool, bool]] = field(default_factory=dict)  # from, to, lengths, passenger, vehicle
    junctions: dict[str, tuple[str, float, float]] = field(default_factory=dict)  # type, x, y
    passenger_links: dict[str, set[str]] = field(default_factory=dict)  # from edge: to edges
    controller_nodes: dict[str, set[str]] = field(default_factory=dict)  # tl: junctions it signals
    cycles: dict[str, float] = field(default_factory=dict)
    incoming: dict[str, list[str]] = field(default_factory=dict)
    outgoing: dict[str, list[str]] = field(default_factory=dict)


def _elements(path: Path) -> Iterator[ET.Element]:
    """Top-level children of a net, each complete, cleared after use."""
    context = ET.iterparse(path, events=("start", "end"))
    _, root = next(context)
    depth = 0
    for event, element in context:
        if event == "start":
            depth += 1
            continue
        depth -= 1
        if depth == 0:
            yield element
            root.remove(element)


def read_net(net_file: Path) -> NetView:
    from .network_layers import VEHICLE_LAYER_CLASSES
    from .osm_access import _permission_set

    vehicle_classes = frozenset(VEHICLE_LAYER_CLASSES)
    view = NetView()
    lane_classes: dict[str, frozenset[str]] = {}
    for element in _elements(Path(net_file)):
        if element.tag == "edge" and not element.get("function"):
            lengths, passenger, vehicle = [], False, False
            for lane in element.findall("lane"):
                attributes = lane.attrib if "allow" in lane.attrib or "disallow" in lane.attrib else element.attrib
                classes = frozenset(_permission_set(attributes))
                lane_classes[lane.get("id")] = classes
                lengths.append(float(lane.get("length", "0")))
                passenger |= "passenger" in classes
                vehicle |= bool(classes & vehicle_classes)
            view.edges[element.get("id")] = (element.get("from"), element.get("to"), lengths, passenger, vehicle)
            view.incoming.setdefault(element.get("to"), []).append(element.get("id"))
            view.outgoing.setdefault(element.get("from"), []).append(element.get("id"))
        elif element.tag == "junction" and element.get("type") != "internal":
            view.junctions[element.get("id")] = (element.get("type") or "", float(element.get("x", "0")),
                                                 float(element.get("y", "0")))
        elif element.tag == "tlLogic" and element.get("id") not in view.cycles:
            view.cycles[element.get("id")] = round(sum(float(phase.get("duration", "0"))
                                                       for phase in element.findall("phase")), 6)
        elif element.tag == "connection" and element.get("from") in view.edges:
            from_edge, to_edge = element.get("from"), element.get("to")
            if element.get("tl"):
                view.controller_nodes.setdefault(element.get("tl"), set()).add(view.edges[from_edge][1])
            if to_edge in view.edges and "passenger" in lane_classes.get(f"{from_edge}_{element.get('fromLane')}", ()) \
                    and "passenger" in lane_classes.get(f"{to_edge}_{element.get('toLane')}", ()):
                view.passenger_links.setdefault(from_edge, set()).add(to_edge)
    return view


def cluster_id(nodes: Iterable[str]) -> str:
    """The id netconvert 1.27.1 gives a joined node group: the first four sorted ids and a count of the rest."""
    ordered = sorted(nodes)
    return "cluster_" + "_".join(ordered[:4]) + (f"_#{len(ordered) - 4}more" if len(ordered) > 4 else "")


def split_junction_groups(
    view: NetView, *, edge_m: float = SPLIT_EDGE_M, span_m: float = SPLIT_SPAN_M, excluded: Iterable[str] = (),
) -> dict[str, Any]:
    """Node groups linked by vehicle edges shorter than ``edge_m`` between nodes with 3+ vehicle-road neighbours.

    A group takes in every node of a controller that signals two or more junctions and has a node in the group, so
    that a join never splits a controller; a group that grew that way to more than ``span_m`` across is skipped, and
    so is every group with a node in ``excluded``.
    """
    excluded = set(excluded)
    neighbours: dict[str, set[str]] = {}
    for start, end, _, _, vehicle in view.edges.values():
        if vehicle and start != end:
            neighbours.setdefault(start, set()).add(end)
            neighbours.setdefault(end, set()).add(start)
    parent: dict[str, str] = {}

    def find(node_id: str) -> str:
        parent.setdefault(node_id, node_id)
        while parent[node_id] != node_id:
            parent[node_id] = parent[parent[node_id]]
            node_id = parent[node_id]
        return node_id

    linkers = []
    for edge_id, (start, end, lengths, _, vehicle) in sorted(view.edges.items()):
        if (vehicle and start != end and lengths and max(lengths) < edge_m
                and len(neighbours.get(start, ())) >= 3 and len(neighbours.get(end, ())) >= 3):
            parent[find(start)] = find(end)
            linkers.append(edge_id)
    grouped: dict[str, set[str]] = {}
    for node_id in parent:
        grouped.setdefault(find(node_id), set()).add(node_id)
    seeds = [nodes for nodes in grouped.values() if len(nodes) > 1]
    seeded = set().union(*seeds) if seeds else set()
    multi = {tl_id: nodes for tl_id, nodes in view.controller_nodes.items() if len(nodes) > 1}
    node_controllers: dict[str, set[str]] = {}
    for tl_id, nodes in multi.items():
        for node_id in nodes:
            node_controllers.setdefault(node_id, set()).add(tl_id)
    groups = [set(nodes) for nodes in seeds]
    changed = True
    while changed:
        changed = False
        for index, nodes in enumerate(groups):
            need = set().union(*(multi[tl_id] for node_id in nodes for tl_id in node_controllers.get(node_id, ())))
            if not need <= nodes:
                nodes |= need
                changed = True
            for other in range(len(groups) - 1, index, -1):
                if groups[other] & nodes:
                    nodes |= groups.pop(other)
                    changed = True

    def span(nodes: set[str]) -> float:
        points = [view.junctions[node_id][1:] for node_id in nodes if node_id in view.junctions]
        return max((math.dist(a, b) for a in points for b in points), default=0.0)

    kept, skipped = [], []
    for nodes in sorted(groups, key=lambda rows: sorted(rows)):
        row = {"nodes": sorted(nodes), "span_m": round(span(nodes), 2), "added_controller_nodes": sorted(nodes - seeded)}
        if nodes & excluded:
            skipped.append({**row, "reason": "excluded_after_check"})
        elif row["added_controller_nodes"] and row["span_m"] > span_m:
            skipped.append({**row, "reason": "controller_closure_too_wide"})
        else:
            kept.append(row)
    return {"edge_m": edge_m, "span_m": span_m, "linker_edge_count": len(linkers), "groups": kept,
            "skipped": skipped}


def osm_turn_restrictions(osm_file: Path) -> list[dict[str, Any]]:
    """Turn restriction relations with one from way, one via node and one to way."""
    opener = gzip.open if str(osm_file).endswith(".gz") else open
    rows = []
    with opener(osm_file, "rb") as handle:
        for _, element in ET.iterparse(handle, events=("end",)):
            if element.tag == "relation":
                tags = {tag.get("k"): tag.get("v") for tag in element.findall("tag")}
                value = tags.get("restriction", "")
                members = [(member.get("type"), member.get("ref"), member.get("role")) for member in element.findall("member")]
                ways = {role: [ref for kind, ref, member_role in members if kind == "way" and member_role == role]
                        for role in ("from", "to")}
                via = [ref for kind, ref, role in members if kind == "node" and role == "via"]
                if tags.get("type") == "restriction" and value.split("_")[0] in {"no", "only"} \
                        and len(ways["from"]) == 1 and len(ways["to"]) == 1 and len(via) == 1:
                    rows.append({"id": element.get("id"), "restriction": value, "from": ways["from"][0],
                                 "via": via[0], "to": ways["to"][0]})
                element.clear()
            elif element.tag in {"node", "way"}:
                element.clear()
    return rows


def _way(edge_id: str) -> str:
    match = _WAY_ID.match(edge_id)
    return match.group(1) if match else edge_id


def restriction_changes(
    base: NetView, joined: NetView, groups: Iterable[Iterable[str]], restrictions: Iterable[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """OSM turn restrictions whose via node was joined and that the joined junction no longer keeps.

    In the base net, each entry edge of a group reaches a set of exits through the group's inner edges; the joined
    junction connects the entry to its exits directly. A ``no_*`` restriction breaks when the joined junction adds
    an exit on the to way, an ``only_*`` restriction when it adds an exit off the to way or drops one on it.
    """
    member = {node_id: frozenset(nodes) for nodes in map(frozenset, groups) for node_id in nodes}
    reach: dict[frozenset[str], dict[str, tuple[set[str], set[str]]]] = {}

    def group_reach(nodes: frozenset[str]) -> dict[str, tuple[set[str], set[str]]]:
        if nodes not in reach:
            out = {}
            entries = [edge_id for node_id in nodes for edge_id in base.incoming.get(node_id, ())
                       if base.edges[edge_id][0] not in nodes and base.edges[edge_id][3]]
            for entry in entries:
                stack, seen, exits = [entry], {entry}, set()
                while stack:
                    for following in base.passenger_links.get(stack.pop(), ()):
                        start, end = base.edges[following][:2]
                        if start in nodes and end in nodes:
                            if following not in seen:
                                seen.add(following)
                                stack.append(following)
                        elif start in nodes:
                            exits.add(following)
                out[entry] = (seen, exits)
            reach[nodes] = out
        return reach[nodes]

    def exits_from(edge_id: str, nodes: frozenset[str]) -> set[str]:
        if base.edges[edge_id][1] not in nodes:
            return {edge_id}
        stack, seen, exits = [edge_id], {edge_id}, set()
        while stack:
            for following in base.passenger_links.get(stack.pop(), ()):
                if base.edges[following][1] in nodes:
                    if following not in seen:
                        seen.add(following)
                        stack.append(following)
                else:
                    exits.add(following)
        return exits

    changes = []
    for row in restrictions:
        nodes = member.get(row["via"])
        if nodes is None:
            continue
        from_edges = [edge_id for edge_id in base.incoming.get(row["via"], ()) if _way(edge_id) == row["from"]]
        to_edges = [edge_id for edge_id in base.outgoing.get(row["via"], ()) if _way(edge_id) == row["to"]]
        if not from_edges or not to_edges:
            continue
        entries = [entry for entry, (seen, _) in group_reach(nodes).items() if seen & set(from_edges)]
        entries = [entry for entry in entries if _way(entry) == row["from"]] or entries
        to_exits = set().union(*(exits_from(edge_id, nodes) for edge_id in to_edges))
        to_exits = {edge_id for edge_id in to_exits if _way(edge_id) == row["to"]} or to_exits
        status = None
        for entry in entries:
            before = group_reach(nodes)[entry][1]
            after = joined.passenger_links.get(entry, set())
            if row["restriction"].startswith("no_"):
                if (after & to_exits) - before:
                    status = "newly_allowed"
            elif (after - to_exits) - before:
                status = "newly_allowed"
            elif status is None and (to_exits & before) - after:
                status = "mandatory_turn_lost"
        if status:
            changes.append({**row, "status": status, "group": sorted(nodes)})
    return changes
