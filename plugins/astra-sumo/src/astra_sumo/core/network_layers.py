"""Cut network layers from one master SUMO network by removing ordinary edges only.

A derived layer keeps the master's edge, lane, junction and signal identifiers, its coordinate frame and the
signal timeline of every retained controlled link, so one OSM-keyed mapping serves every layer. Phase indices
are layer-specific: netconvert may merge phases whose only difference was a removed link. netconvert re-plans the
program of a controller that loses a pedestrian crossing; a second conversion of the same input loads the master
program back with ``--tllogic-files``, every state cut to the layer's links (``identity.signal_restore``).
Re-planned crossing timelines the restore cannot keep are listed for review, and changed vehicle timelines block
the layer. A crossing that keeps some of its crossed edges is the same link with fewer crossed edges. netconvert
also recomputes junction shapes, lane geometry and right of way around removed edges; those changes are measured
and reported, not gated.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Iterable, Iterator, Mapping
import gzip
from itertools import zip_longest
import math
from pathlib import Path
import re
from typing import Any
import xml.etree.ElementTree as ET
from xml.sax.saxutils import unescape

from .artifact_io import atomic_writer, copy_file_atomic, write_json_atomic
from .candidate_contracts import file_sha256
from .osm_access import _permission_set
from .road_hierarchy import keep_selected_edges_natively, run_native_keep
from .sumo_commands import run_sumo_load_audit


# Delivery, emergency and authority alone do not make a vehicle edge: SUMO footways allow delivery.
VEHICLE_LAYER_CLASSES = ("passenger", "bus", "coach", "truck", "trailer", "taxi", "motorcycle", "evehicle", "hov")
# None keeps every master edge.
NETWORK_LAYER_CLASSES: dict[str, tuple[str, ...] | None] = {"vehicle": VEHICLE_LAYER_CLASSES, "multimodal": None}
LONG_CYCLE_THRESHOLD_S = 120.0
COMPONENT_SHARE_THRESHOLD = 0.98
# The coordinate frame; convBoundary shrinks with the removed edges and is recorded, not compared.
_LOCATION_IDENTITY = ("netOffset", "projParameter")
# netOffset is compared as numbers, so a master written at another precision keeps its frame.
_OFFSET_TOLERANCE_M = 1e-6
# Lane and junction shapes that move less than this are rounding, not a change.
_GEOMETRY_TOLERANCE_M = 0.01
_EXAMPLE_LIMIT = 20
_CLAIM_BOUNDARY = ("Edges are removed from the master only. Identity checks cover identifiers, permissions, the "
                   "coordinate frame (netOffset and projection) and signal timing of retained links. netconvert "
                   "recomputes junction shapes, lane geometry and lengths, junction types and connection right of "
                   "way around removed edges; identity.geometry and identity.right_of_way count those changes. Phase "
                   "indices are specific to each layer. A controller that netconvert re-planned for a lost crossing "
                   "gets its master program back through a second conversion with --tllogic-files, its states cut to "
                   "the layer's links (identity.signal_restore); re-planned crossing timelines the restore cannot keep "
                   "are listed for review.")
# netconvert rebuilds the pedestrian phases of a loaded program whose crossing count changed.
_CROSSING_REVIEW_REASON = "crossing_signal_timelines_replanned"
_SIGNAL_RESTORE_PROGRAMS = "signal-restore.tll.xml"
_SIGNAL_RESTORE_PROPOSAL = "signal-restore-proposal.net.xml"
_SIGNAL_RESTORE_BASIS = ("netconvert (SUMO 1.27.1, NBLoadedSUMOTLDef::patchIfCrossingsAdded) re-plans a loaded program "
                         "whose valid crossing count changed unless its states are as long as its retained links and "
                         "valid crossings; the master program of each re-planned controller, every state cut to the "
                         "first cut's length, is loaded with --tllogic-files in a second conversion of the same input, "
                         "which replaces the first cut only when it differs from it in those programs alone, fewer "
                         "controllers keep a changed timeline and no identity check that held fails")
_TLLOGIC_START = re.compile(rb'<tlLogic\b[^>]*\bid="([^"]*)"[^>]*\bprogramID="([^"]*)"')
_XML_ATTRIBUTE_ENTITIES = {"&quot;": '"', "&apos;": "'"}


def _direct_children(net_file: Path):
    """Yield each complete direct child of <net> with the network version, keeping memory flat."""
    context = ET.iterparse(net_file, events=("start", "end"))
    _, root = next(context)
    version, depth = root.get("version", "0.0"), 0
    for event, element in context:
        if event == "start":
            depth += 1
            continue
        depth -= 1
        if depth:
            continue
        yield version, element
        root.clear()


def _orig_id(lane: ET.Element) -> str | None:
    return next((row.get("value") for row in lane.findall("param") if row.get("key") == "origId"), None)


def _permission_reader():
    """Permission sets by declaration, shared between lanes so a city network stays small in memory."""
    known: dict[tuple[str | None, str | None, str], frozenset[str]] = {}

    def permissions(lane: ET.Element, version: str) -> frozenset[str]:
        key = (lane.get("allow"), lane.get("disallow"), version)
        if key not in known:
            known[key] = frozenset(_permission_set(lane.attrib, network_version=version))
        return known[key]

    return permissions


def _net_inventory(net_file: Path) -> dict[str, Any]:
    """Read the identifiers and signal programs a layer must keep from a streamed network."""
    edges: dict[str, dict[str, Any]] = {}
    crossings: dict[str, tuple[str, str]] = {}
    junctions: dict[str, tuple[str, str]] = {}
    programs: dict[tuple[str, str], dict[str, Any]] = {}
    rows: list[tuple[str, ...]] = []
    location: dict[str, str] = {}
    permissions = _permission_reader()
    for version, element in _direct_children(net_file):
        if element.tag == "location":
            location = dict(element.attrib)
        elif element.tag == "edge":
            function = element.get("function") or ""
            if function == "crossing":
                junction = element.get("id", "")[1:].rsplit("_c", 1)[0]
                crossings[element.get("id", "")] = (junction, " ".join(sorted(element.get("crossingEdges", "").split())))
            elif not function:
                lanes = element.findall("lane")
                edges[element.get("id", "")] = {
                    "lanes": [lane.get("id") for lane in lanes],
                    "permissions": [permissions(lane, version) for lane in lanes],
                    "orig_ids": [_orig_id(lane) for lane in lanes],
                    "ends": (element.get("from", ""), element.get("to", "")),
                    "geometry": [(float(lane.get("length", "0")), lane.get("shape", "")) for lane in lanes],
                }
        elif element.tag == "junction" and element.get("type") != "internal":
            junctions[element.get("id", "")] = (element.get("type", ""), element.get("shape", ""))
        elif element.tag == "tlLogic":
            element.tail = None
            programs[(element.get("id", ""), element.get("programID", ""))] = {
                "type": element.get("type"), "offset": element.get("offset"),
                "phases": [(float(phase.get("duration", "0")), phase.get("state", "")) for phase in element.findall("phase")],
                # The declaration as written, so a signal restore loads the master program with every attribute.
                "source": ET.tostring(element, encoding="unicode")}
        elif element.tag == "connection":
            first, last, tl = element.get("from", ""), element.get("to", ""), element.get("tl")
            if tl or not (first.startswith(":") or last.startswith(":")):
                rows.append((first, element.get("fromLane", ""), last, element.get("toLane", ""), tl or "",
                             element.get("linkIndex", ""), element.get("dir", ""), element.get("state", "")))
    connections: dict[tuple[str, ...], tuple[str, str]] = {}
    right_of_way: dict[tuple[str, ...], tuple[str, str]] = {}
    links: dict[tuple[str, ...], tuple[tuple[str, str], ...]] = {}
    crossing_links: dict[tuple[str, ...], list[tuple[str, str]]] = defaultdict(list)
    for first, first_lane, last, last_lane, tl, index, direction, state in rows:
        if first in edges and last in edges:
            connections[(first, first_lane, last, last_lane)] = (tl, index)
            right_of_way[(first, first_lane, last, last_lane)] = (direction, state)
            if tl:
                links[("connection", first, first_lane, last, last_lane)] = ((tl, index),)
        crossing = crossings.get(last) or crossings.get(first)
        if tl and crossing:
            crossing_links[("crossing", *crossing)].append((tl, index))
    links.update({key: tuple(sorted(values)) for key, values in crossing_links.items()})
    return {"location": location, "edges": edges, "junctions": set(junctions), "junction_details": junctions,
            "programs": programs, "connections": connections, "right_of_way": right_of_way, "controlled_links": links}


def _layer_selection(inventory: Mapping[str, Any], layer: str) -> dict[str, Any]:
    if layer not in NETWORK_LAYER_CLASSES:
        raise ValueError(f"Choose a network layer from {', '.join(NETWORK_LAYER_CLASSES)}.")
    classes = NETWORK_LAYER_CLASSES[layer]
    kept, removed, signatures = [], [], Counter()
    for edge_id, edge in sorted(inventory["edges"].items()):
        allowed = set().union(*edge["permissions"])
        if classes is None or allowed & set(classes):
            kept.append(edge_id)
        else:
            removed.append(edge_id)
            signatures[" ".join(sorted(allowed)) or "none"] += 1
    return {"layer": layer, "classes": list(classes) if classes is not None else None,
            "basis": "every_master_edge" if classes is None else "a_lane_allows_a_layer_class",
            "kept_edge_ids": kept, "removed_edge_ids": removed,
            "removed_by_permission_signature": dict(sorted(signatures.items()))}


def select_layer_edges(net: Path | str, layer: str) -> dict[str, Any]:
    """Partition the master's ordinary edges into the ones ``layer`` keeps and the ones it removes.

    The vehicle layer keeps an edge when one of its lanes allows a class in ``VEHICLE_LAYER_CLASSES``; removed
    edges are counted by the classes their lanes allow. The multimodal layer keeps every edge.
    """
    return _layer_selection(_net_inventory(Path(net)), layer)


def _timeline(program: Mapping[str, Any], index: str) -> list[tuple[str, float]] | None:
    """Signal of one link over the cycle, with consecutive equal signals merged."""
    try:
        position = int(index)
    except ValueError:
        return None
    rows: list[list[Any]] = []
    for duration, state in program["phases"]:
        signal = state[position] if 0 <= position < len(state) else ""
        if rows and rows[-1][0] == signal:
            rows[-1][1] += duration
        else:
            rows.append([signal, duration])
    return [(signal, round(duration, 6)) for signal, duration in rows]


def _cycle(program: Mapping[str, Any]) -> float:
    return round(sum(duration for duration, _ in program["phases"]), 6)


def _layer_keyed_master_links(
    links: Mapping[tuple[str, ...], tuple[tuple[str, str], ...]], layer_edges: Mapping[str, Any]
) -> tuple[dict[tuple[str, ...], tuple[tuple[str, str], ...]], list[dict[str, str]]]:
    """Master controlled links under the keys the layer uses for them.

    netconvert keeps a crossing whose crossed edges were partly removed, with its id and link index, and lists only
    the crossed edges that remain, so that crossing is keyed by the edges the layer keeps. A crossing that keeps
    none of them is removed and stays under its master key.
    """
    keyed: dict[tuple[str, ...], list[tuple[str, str]]] = defaultdict(list)
    reduced: list[dict[str, str]] = []
    for key, values in links.items():
        if key[0] == "crossing":
            crossed = key[2].split()
            remaining = [edge_id for edge_id in crossed if edge_id in layer_edges]
            if remaining and len(remaining) < len(crossed):
                reduced.append({"junction": key[1], "master_crossing_edges": key[2],
                                "layer_crossing_edges": " ".join(remaining)})
                key = ("crossing", key[1], " ".join(remaining))
        keyed[key].extend(values)
    return {key: tuple(sorted(values)) for key, values in keyed.items()}, reduced


def _same_frame(master: Mapping[str, Any], layer: Mapping[str, Any]) -> bool:
    """Same projection and the same netOffset as numbers, whatever precision each file was written at."""
    if master.get("projParameter") != layer.get("projParameter"):
        return False
    try:
        before = [float(value) for value in str(master.get("netOffset", "")).split(",")]
        after = [float(value) for value in str(layer.get("netOffset", "")).split(",")]
    except ValueError:
        return master.get("netOffset") == layer.get("netOffset")
    return len(before) == len(after) and all(abs(old - new) <= _OFFSET_TOLERANCE_M for old, new in zip(before, after))


def _points(shape: str) -> list[tuple[float, float]]:
    return [(float(point.split(",")[0]), float(point.split(",")[1])) for point in shape.split()]


def _shape_shift(before: str, after: str) -> float | None:
    """Largest displacement of a shape point, or None when the point count changed."""
    if before == after:
        return 0.0
    old, new = _points(before), _points(after)
    if len(old) != len(new):
        return None
    return max((math.dist(first, second) for first, second in zip(old, new)), default=0.0)


def _geometry_changes(master: Mapping[str, Any], layer: Mapping[str, Any], retained: Iterable[str]) -> dict[str, Any]:
    """Retained lanes and junctions whose shape or length netconvert recomputed, measured against the master."""
    master_edges, layer_edges = master["edges"], layer["edges"]
    near_removed = {node for edge_id in set(master_edges) - set(layer_edges)
                    for node in master_edges[edge_id].get("ends", ())}
    lanes = []
    for edge_id in retained:
        before, after = master_edges[edge_id], layer_edges[edge_id]
        if before["lanes"] != after["lanes"]:
            continue
        near = bool(set(after.get("ends", ())) & near_removed)
        for lane_id, (old_length, old_shape), (new_length, new_shape) in zip(
                after["lanes"], before.get("geometry", ()), after.get("geometry", ())):
            shift, length_change = _shape_shift(old_shape, new_shape), new_length - old_length
            if shift is not None and shift <= _GEOMETRY_TOLERANCE_M and abs(length_change) <= _GEOMETRY_TOLERANCE_M:
                continue
            old_points, new_points = _points(old_shape), _points(new_shape)
            endpoint = (max(math.dist(old_points[0], new_points[0]), math.dist(old_points[-1], new_points[-1]))
                        if old_points and new_points else None)
            lanes.append({"lane": lane_id, "master_length_m": round(old_length, 6),
                          "layer_length_m": round(new_length, 6),
                          "endpoint_shift_m": None if endpoint is None else round(endpoint, 6),
                          "point_count_changed": shift is None, "at_junction_that_lost_an_edge": near})
    junctions = []
    layer_details = layer.get("junction_details", {})
    for junction_id, (_, old_shape) in sorted(master.get("junction_details", {}).items()):
        if junction_id not in layer_details:
            continue
        shift = _shape_shift(old_shape, layer_details[junction_id][1])
        if shift is None or shift > _GEOMETRY_TOLERANCE_M:
            junctions.append({"junction": junction_id, "shape_shift_m": None if shift is None else round(shift, 6),
                              "lost_an_edge": junction_id in near_removed})
    lanes.sort(key=lambda row: -(row["endpoint_shift_m"] or 0.0))
    return {
        "basis": "retained lane shapes and lengths and junction shapes compared point by point with the master",
        "tolerance_m": _GEOMETRY_TOLERANCE_M,
        "changed_lane_count": len(lanes),
        "changed_lane_length_count": sum(abs(row["layer_length_m"] - row["master_length_m"]) > _GEOMETRY_TOLERANCE_M
                                         for row in lanes),
        "changed_lanes_away_from_removed_edges_count": sum(not row["at_junction_that_lost_an_edge"] for row in lanes),
        "max_lane_endpoint_shift_m": max((row["endpoint_shift_m"] or 0.0 for row in lanes), default=0.0),
        "max_lane_length_change_m": round(max((abs(row["layer_length_m"] - row["master_length_m"]) for row in lanes),
                                              default=0.0), 6),
        "changed_lanes": lanes[:_EXAMPLE_LIMIT],
        "changed_junction_shape_count": len(junctions),
        "changed_junction_shapes_away_from_removed_edges_count": sum(not row["lost_an_edge"] for row in junctions),
        "changed_junction_shapes": junctions[:_EXAMPLE_LIMIT],
    }


def _right_of_way_changes(master: Mapping[str, Any], layer: Mapping[str, Any]) -> dict[str, Any]:
    """Direction and right-of-way state of retained connections, and type of retained junctions, against the master."""
    states, directions, connections = Counter(), Counter(), []
    master_rows = master.get("right_of_way", {})
    for key, (direction, state) in sorted(layer.get("right_of_way", {}).items()):
        before = master_rows.get(key)
        if before is None or before == (direction, state):
            continue
        if before[1] != state:
            states[f"{before[1]}->{state}"] += 1
        if before[0] != direction:
            directions[f"{before[0]}->{direction}"] += 1
        connections.append({"connection": list(key), "master": {"dir": before[0], "state": before[1]},
                            "layer": {"dir": direction, "state": state}})
    types, junctions = Counter(), []
    layer_details = layer.get("junction_details", {})
    for junction_id, (old_type, _) in sorted(master.get("junction_details", {}).items()):
        new_type = layer_details.get(junction_id, (old_type, ""))[0]
        if new_type != old_type:
            types[f"{old_type}->{new_type}"] += 1
            junctions.append({"junction": junction_id, "master_type": old_type, "layer_type": new_type})
    return {
        "basis": "dir and state of connections between retained edges and type of retained junctions",
        "changed_connection_count": len(connections),
        "connection_state_changes": dict(sorted(states.items())),
        "connection_dir_changes": dict(sorted(directions.items())),
        "changed_connections": connections[:_EXAMPLE_LIMIT],
        "changed_junction_type_count": len(junctions),
        "junction_type_changes": dict(sorted(types.items())),
        "changed_junction_types": junctions[:_EXAMPLE_LIMIT],
    }


def _layer_identity(master: Mapping[str, Any], layer: Mapping[str, Any], kept: set[str]) -> dict[str, Any]:
    master_edges, layer_edges = master["edges"], layer["edges"]
    retained = sorted(set(layer_edges) & set(master_edges))
    changed = {key: [] for key in ("lanes", "permissions", "orig_ids")}
    for edge_id in retained:
        before, after = master_edges[edge_id], layer_edges[edge_id]
        if before["lanes"] != after["lanes"]:
            changed["lanes"].append(edge_id)
            continue
        for key in ("permissions", "orig_ids"):
            changed[key].extend(lane for lane, old, new in zip(after["lanes"], before[key], after[key]) if old != new)
    lost = [key for key, value in master["connections"].items()
            if key[0] in layer_edges and key[2] in layer_edges and layer["connections"].get(key) != value]
    added = [key for key in layer["connections"] if key not in master["connections"]]
    master_links, reduced_crossings = _layer_keyed_master_links(master["controlled_links"], layer_edges)
    reduced_crossings = [row for row in reduced_crossings
                         if ("crossing", row["junction"], row["layer_crossing_edges"]) in layer["controlled_links"]]
    removed_links = sorted(set(master_links) - set(layer["controlled_links"]))
    # Crossing links of a controller that lost a crossing: netconvert re-plans their phases, so they need review.
    # A crossing that only crosses fewer edges keeps the crossing count, and netconvert keeps the program.
    lost_crossing_tls = {tl for key in removed_links if key[0] == "crossing" for tl, _ in master_links[key]}
    timelines, replanned = [], []
    for key, after in layer["controlled_links"].items():
        before = master_links.get(key)
        if before is None or len(before) != len(after):
            timelines.append({"link": list(key), "reason": "link_not_in_master" if before is None else "link_count_changed"})
            continue
        for (old_tl, old_index), (new_tl, new_index) in zip(before, after):
            for program_id in sorted({pid for tl, pid in layer["programs"] if tl == new_tl}):
                old_program = master["programs"].get((old_tl, program_id))
                new_program = layer["programs"][(new_tl, program_id)]
                if old_program is None or _timeline(old_program, old_index) != _timeline(new_program, new_index):
                    row = {"link": list(key), "tl": new_tl, "program_id": program_id}
                    if (old_program is not None and key[0] == "crossing" and old_tl == new_tl
                            and new_tl in lost_crossing_tls):
                        replanned.append({**row, "reason": _CROSSING_REVIEW_REASON})
                    else:
                        timelines.append({**row, "reason": "signal_timeline_changed"})
    controllers = []
    for (tl, program_id), program in sorted(layer["programs"].items()):
        before = master["programs"].get((tl, program_id))
        if before is None:
            controllers.append({"tl": tl, "program_id": program_id, "reason": "controller_not_in_master"})
        elif (_cycle(program), program["offset"], program["type"]) != (_cycle(before), before["offset"], before["type"]):
            controllers.append({"tl": tl, "program_id": program_id, "reason": "cycle_offset_or_type_changed",
                                "master_cycle_s": _cycle(before), "layer_cycle_s": _cycle(program)})
    master_tls, layer_tls = {tl for tl, _ in master["programs"]}, {tl for tl, _ in layer["programs"]}
    location = {key: {"master": master["location"].get(key), "layer": layer["location"].get(key)}
                for key in (*_LOCATION_IDENTITY, "convBoundary", "origBoundary")}
    checks = {
        "edge_ids_subset_of_master": set(layer_edges) <= set(master_edges),
        "selected_edges_realized": set(layer_edges) == kept,
        "junction_ids_subset_of_master": layer["junctions"] <= master["junctions"],
        "tls_ids_subset_of_master": layer_tls <= master_tls,
        "lane_ids_equal": not changed["lanes"],
        "lane_permissions_equal": not changed["permissions"],
        "orig_ids_equal": not changed["orig_ids"],
        "location_equal": _same_frame(master["location"], layer["location"]),
        "connections_between_retained_edges_kept": not lost and not added,
        "controlled_link_timelines_equal": not timelines,
        "controller_cycle_offset_type_equal": not controllers,
    }
    passed = all(checks.values())
    return {
        "status": "measured" if passed else "blocked",
        **({} if passed else {"reason": "layer_identity_check_failed",
                              "failed_checks": sorted(key for key, ok in checks.items() if not ok)}),
        **({"review_reason": _CROSSING_REVIEW_REASON} if passed and replanned else {}),
        "checks": checks,
        "retained_edge_count": len(retained),
        "retained_connection_count": sum(1 for key in layer["connections"] if key in master["connections"]),
        "retained_controlled_link_count": len(layer["controlled_links"]),
        "removed_controlled_link_count": len(removed_links),
        "removed_controlled_links": [list(key) for key in removed_links[:_EXAMPLE_LIMIT]],
        "reduced_crossing_count": len(reduced_crossings),
        "reduced_crossings": reduced_crossings[:_EXAMPLE_LIMIT],
        "removed_tls_ids": sorted(master_tls - layer_tls),
        "removed_junction_count": len(master["junctions"] - layer["junctions"]),
        "location": location,
        "changed_lane_lists": changed["lanes"][:_EXAMPLE_LIMIT],
        "changed_lane_permissions": changed["permissions"][:_EXAMPLE_LIMIT],
        "changed_orig_ids": changed["orig_ids"][:_EXAMPLE_LIMIT],
        "lost_connections": [list(key) for key in lost[:_EXAMPLE_LIMIT]],
        "added_connections": [list(key) for key in added[:_EXAMPLE_LIMIT]],
        "changed_signal_timeline_count": len(timelines),
        "changed_signal_timelines": timelines[:_EXAMPLE_LIMIT],
        # Every program with a changed or re-planned retained-link timeline or a changed cycle, offset or type.
        "changed_timeline_controllers": sorted(
            [list(row) for row in {(row["tl"], row["program_id"]) for row in (*timelines, *replanned, *controllers)
                                   if "tl" in row and row["reason"] != "controller_not_in_master"}]),
        "replanned_crossing_link_count": len(replanned),
        "replanned_crossing_controllers": sorted({row["tl"] for row in replanned}),
        "replanned_crossing_timelines": replanned[:_EXAMPLE_LIMIT],
        "changed_controllers": controllers[:_EXAMPLE_LIMIT],
        "basis": "per-link signal timelines with consecutive equal signals merged; phase indices are not compared",
        # Measured, not gated: netconvert recomputes these around removed edges.
        "geometry": _geometry_changes(master, layer, retained),
        "right_of_way": _right_of_way_changes(master, layer),
    }


def _state_length(program: Mapping[str, Any]) -> int | None:
    lengths = {len(state) for _, state in program["phases"]}
    return lengths.pop() if len(lengths) == 1 else None


def _signal_restore_plan(master: Mapping[str, Any], layer: Mapping[str, Any],
                         controllers: Iterable[Iterable[str]]) -> dict[str, list[dict[str, Any]]]:
    """Changed controllers whose master program, states cut to the first cut's length, netconvert keeps as loaded.

    netconvert re-plans a program when the controller lost a loaded crossing and the remaining crossings keep their
    sequential link indices; the re-planned states end after those crossings, so they are shorter than the master's.
    A controller qualifies when it lost a crossing link, its states got shorter and every retained link keeps its
    master controller and an index below the cut, so every cut column is unused or belongs to a removed link.
    """
    master_links, _ = _layer_keyed_master_links(master["controlled_links"], layer["edges"])
    removed = set(master_links) - set(layer["controlled_links"])
    lost_crossing_tls = {tl for key in removed if key[0] == "crossing" for tl, _ in master_links[key]}
    owners: dict[tuple[str, str], list[tuple[str, ...]]] = defaultdict(list)
    for key, values in master_links.items():
        for value in values:
            owners[value].append(key)
    retained: dict[str, list[tuple[Any, tuple[tuple[str, str], ...]]]] = defaultdict(list)
    for key, values in layer["controlled_links"].items():
        for tl in {tl for tl, _ in values}:
            retained[tl].append((master_links.get(key), values))
    planned, skipped = [], []
    for tl, program_id in (tuple(row) for row in controllers):
        before, after = master["programs"].get((tl, program_id)), layer["programs"].get((tl, program_id))
        row = {"tl": tl, "program_id": program_id}
        if before is None or after is None or not before["phases"] or not after["phases"]:
            reason = "program_without_phases"
        elif tl not in lost_crossing_tls:
            reason = "controller_lost_no_crossing"
        elif (length := _state_length(after)) is None or (master_length := _state_length(before)) is None:
            reason = "state_length_not_uniform"
        elif length >= master_length:
            # Not the crossing re-plan, so the identity keeps gating it.
            reason = "layer_states_not_shorter_than_master"
        elif any(old != new or not all(index.isdigit() and int(index) < length for _, index in new)
                 for old, new in retained[tl]):
            reason = "retained_link_index_changed_or_cut"
        else:
            cut = range(length, master_length)
            planned.append({**row, "master_state_length": master_length, "state_length": length,
                            "cut_link_indices": list(cut),
                            "cut_link_kinds": sorted({key[0] for index in cut for key in owners.get((tl, str(index)), ())}
                                                     or {"unused"}),
                            "master_phase_count": len(before["phases"]), "first_cut_phase_count": len(after["phases"])})
            continue
        skipped.append({**row, "reason": reason})
    return {"planned": planned, "skipped": skipped}


def _write_signal_restore(master: Mapping[str, Any], planned: Iterable[Mapping[str, Any]],
                          destination: Path) -> dict[str, Any]:
    """The master declaration of each planned program with every phase state cut to the planned length."""
    root = ET.Element("tlLogics")
    for row in planned:
        logic = ET.fromstring(master["programs"][(row["tl"], row["program_id"])]["source"])
        for phase in logic.findall("phase"):
            phase.set("state", phase.get("state", "")[:row["state_length"]])
        root.append(logic)
    ET.indent(root)
    with atomic_writer(destination, mode="wb") as handle:
        ET.ElementTree(root).write(handle, encoding="utf-8", xml_declaration=True)
    return {"path": str(destination), "sha256": file_sha256(destination), "controller_count": len(root)}


def _lines_outside_programs(net_file: Path, programs: set[tuple[str, str]]) -> Iterator[bytes]:
    """Lines from <net> on, without the tlLogic declarations of ``programs``; the header names the options."""
    with Path(net_file).open("rb") as handle:
        for line in handle:
            if line.lstrip().startswith(b"<net "):
                yield line
                break
        skipping = False
        for line in handle:
            if skipping:
                skipping = b"</tlLogic>" not in line
                continue
            match = _TLLOGIC_START.search(line)
            if match and tuple(unescape(value.decode("utf-8"), _XML_ATTRIBUTE_ENTITIES)
                               for value in match.groups()) in programs:
                skipping = not (b"</tlLogic>" in line or line.rstrip().endswith(b"/>"))
                continue
            yield line


def _changes_outside_programs(first: Path, second: Path, programs: set[tuple[str, str]]) -> dict[str, Any]:
    """Lines in which two conversions of the same input differ, other than the restored programs.

    netconvert writes the same input the same way, so a line-by-line comparison is exact; any drift in layout only
    adds differences and rejects the restore.
    """
    count, examples = 0, []
    for number, (before, after) in enumerate(zip_longest(_lines_outside_programs(first, programs),
                                                         _lines_outside_programs(second, programs)), start=1):
        if before != after:
            count += 1
            if len(examples) < _EXAMPLE_LIMIT:
                examples.append({"line": number, "first": (before or b"").decode("utf-8", "replace").strip()[:200],
                                 "second": (after or b"").decode("utf-8", "replace").strip()[:200]})
    return {"basis": "lines from <net> on, restored tlLogic declarations left out", "changed_line_count": count,
            "changed_lines": examples}


def _identity_summary(identity: Mapping[str, Any]) -> dict[str, Any]:
    controllers = identity.get("changed_timeline_controllers", [])
    return {"identity_status": identity.get("status"), "failed_checks": identity.get("failed_checks", []),
            "review_reason": identity.get("review_reason"),
            "changed_signal_timeline_count": identity.get("changed_signal_timeline_count", 0),
            "replanned_crossing_link_count": identity.get("replanned_crossing_link_count", 0),
            "changed_timeline_controller_count": len(controllers),
            "changed_timeline_controllers": controllers[:_EXAMPLE_LIMIT]}


def _signal_restore_outcome(first: Mapping[str, Any], restored: Mapping[str, Any]) -> dict[str, Any]:
    """Whether the restored conversion replaces the first cut, with the restore status and reason."""
    regressed = sorted(check for check, ok in restored["checks"].items() if first["checks"].get(check) and not ok)
    before = {tuple(row) for row in first["changed_timeline_controllers"]}
    after = {tuple(row) for row in restored["changed_timeline_controllers"]}
    replaced = not regressed and after < before
    reason = ("signal_restore_changed_other_identity" if regressed else "signal_timelines_not_restored" if not replaced
              else "signal_timelines_partly_restored" if after else "")
    return {"status": "measured" if replaced else "blocked", **({"reason": reason} if reason else {}),
            "restored": {**_identity_summary(restored), "regressed_checks": regressed, "replaced_first_cut": replaced}}


def _restore_signal_programs(source: Path, output: Path, kept_file: Path, candidate: Path, conversion: Mapping[str, Any],
                             report: dict[str, Any], kept: set[str], *, netconvert_binary: str,
                             timeout_seconds: float) -> dict[str, Any]:
    """The identity of the layer after restoring the master programs netconvert re-planned for a lost crossing.

    The restored conversion replaces ``candidate`` only when ``_signal_restore_outcome`` says so; otherwise the first
    cut stays with its own identity. The restore record is the identity's ``signal_restore``.
    """
    master_inventory, layer_inventory = _net_inventory(source), _net_inventory(candidate)
    identity = _layer_identity(master_inventory, layer_inventory, kept)
    if not identity["changed_timeline_controllers"]:
        identity["signal_restore"] = {"status": "no_effect", "reason": "no_changed_signal_timeline"}
        return identity
    plan = _signal_restore_plan(master_inventory, layer_inventory, identity["changed_timeline_controllers"])
    first_cut = conversion["attempts"][0]["output"]
    restore: dict[str, Any] = {
        "status": "blocked", "reason": "no_restorable_controller", "basis": _SIGNAL_RESTORE_BASIS,
        "planned_controller_count": len(plan["planned"]), "planned_controllers": plan["planned"][:_EXAMPLE_LIMIT],
        "skipped_controller_count": len(plan["skipped"]), "skipped_controllers": plan["skipped"][:_EXAMPLE_LIMIT],
        "first_cut": {"path": first_cut["path"], "sha256": first_cut["sha256"], **_identity_summary(identity)}}
    identity["signal_restore"] = restore
    if not plan["planned"]:
        return identity
    programs = _write_signal_restore(master_inventory, plan["planned"], output / _SIGNAL_RESTORE_PROGRAMS)
    # Neither inventory is held while netconvert converts a city-sized master again.
    del master_inventory, layer_inventory
    proposal = output / _SIGNAL_RESTORE_PROPOSAL
    _, attempt, successful = run_native_keep(conversion["prepared"]["path"], kept_file, proposal,
                                             netconvert_binary=netconvert_binary, timeout_seconds=timeout_seconds,
                                             tllogic_file=programs["path"])
    attempt["purpose"] = "signal_restore"
    report["native_attempts"].append(attempt)
    report["native_conversion_count"] = len(report["native_attempts"])
    restore.update(programs_file=programs, reason="signal_restore_conversion_failed")
    if not successful:
        return identity
    outside = _changes_outside_programs(Path(first_cut["path"]), proposal,
                                        {(row["tl"], row["program_id"]) for row in plan["planned"]})
    restore.update(outside_restored_programs=outside, reason="signal_restore_changed_other_elements")
    if outside["changed_line_count"]:
        return identity
    restored = _layer_identity(_net_inventory(source), _net_inventory(proposal), kept)
    restore.pop("reason")
    restore.update(_signal_restore_outcome(identity, restored))
    if not restore["restored"]["replaced_first_cut"]:
        return identity
    copy_file_atomic(proposal, candidate)
    restored["signal_restore"] = restore
    return restored


def derive_network_layer(master_net_file: Path | str, output_dir: Path | str, layer: str, *,
                         netconvert_binary: str = "netconvert", sumo_binary: str = "sumo",
                         timeout_seconds: float = 240.0) -> dict[str, Any]:
    """Cut ``layer`` from the master by removing ordinary edges only, and record what it kept.

    Uses ``netconvert --sumo-net-file <master> --keep-edges.input-file <list> --offset.disable-normalization true
    --precision 6`` (preload, never ``--keep-edges.postload``), then the identity checks and a SUMO load audit.
    When netconvert re-planned a controller for a lost crossing, the same conversion runs again with
    ``--tllogic-files`` holding that controller's master program, every state cut to the first cut's length; the
    result replaces the first cut only when it differs from it in those programs alone, fewer controllers keep a
    changed timeline and no identity check that held fails (``identity.signal_restore``).
    The output directory must be new and the master is never written. Status is ``measured`` when edges were
    removed and every check holds, ``no_effect`` when the layer keeps every master edge (the layer is then the
    master file), and ``blocked`` with a reason otherwise.
    """
    source, output = Path(master_net_file).resolve(strict=True), Path(output_dir).resolve()
    if output.exists() or source.is_relative_to(output):
        raise ValueError("Use a new output directory separate from the master network.")
    master_hash = file_sha256(source)
    # The master inventory is read again for the identity checks instead of being held while the signal
    # cleanup, the permission preparation and netconvert run on a city-sized master.
    selection = _layer_selection(_net_inventory(source), layer)
    output.mkdir(parents=True)
    kept_file, removed_file = output / "kept-edges.txt", output / "removed-edges.txt"
    kept_file.write_text("".join(f"{edge_id}\n" for edge_id in selection["kept_edge_ids"]), encoding="utf-8")
    removed_file.write_text("".join(f"{edge_id}\n" for edge_id in selection["removed_edge_ids"]), encoding="utf-8")
    candidate = output / f"{layer}.net.xml"
    report: dict[str, Any] = {
        "schema": "astra.network-layer/v1", "status": "blocked", "layer": layer, "classes": selection["classes"],
        "selection_basis": selection["basis"], "master": {"path": str(source), "sha256": master_hash},
        "net_file": "", "sha256": "", "derived_from_sha256": master_hash,
        "edge_count": len(selection["kept_edge_ids"]), "removed_edge_count": len(selection["removed_edge_ids"]),
        "kept_edges_file": str(kept_file), "removed_edges_file": str(removed_file),
        "removed_by_permission_signature": selection["removed_by_permission_signature"],
        "native_conversion_count": 0, "claim_boundary": _CLAIM_BOUNDARY}
    if not selection["removed_edge_ids"]:
        report.update(status="no_effect", reason="layer_keeps_every_master_edge", net_file=str(source), sha256=master_hash,
                      identity={"status": "no_effect", "reason": "layer_keeps_every_master_edge"})
    elif not selection["kept_edge_ids"]:
        report.update(reason="no_master_edge_serves_layer",
                      identity={"status": "blocked", "reason": "no_master_edge_serves_layer"})
    else:
        conversion = keep_selected_edges_natively(source, output, kept_file, candidate,
                                                  netconvert_binary=netconvert_binary, timeout_seconds=timeout_seconds)
        report["source_signal_cleanup"] = conversion["cleanup"]
        if "effective" not in conversion:
            report.update(reason="master_has_unresolved_signal_references",
                          identity={"status": "blocked", "reason": "master_has_unresolved_signal_references"})
        else:
            report.update(command=conversion["native"].to_dict(), native_attempts=conversion["attempts"],
                          native_conversion_count=len(conversion["attempts"]), permission_input=conversion["prepared"])
            if not candidate.is_file():
                report.update(reason="native_layer_conversion_failed",
                              identity={"status": "blocked", "reason": "native_layer_conversion_failed"})
            else:
                identity = _restore_signal_programs(source, output, kept_file, candidate, conversion, report,
                                                    set(selection["kept_edge_ids"]),
                                                    netconvert_binary=netconvert_binary, timeout_seconds=timeout_seconds)
                load = run_sumo_load_audit(net_file=candidate, output_dir=output / "sumo-load",
                                           sumo_binary=str(sumo_binary), timeout_seconds=timeout_seconds)
                report.update(net_file=str(candidate), sha256=file_sha256(candidate), identity=identity, sumo_load=load)
                if identity["status"] != "measured":
                    report["reason"] = "layer_identity_check_failed"
                elif load["status"] != "pass":
                    report["reason"] = "sumo_load_failed"
                else:
                    report["status"] = "measured"
                    if identity.get("review_reason"):
                        report["reason"] = identity["review_reason"]
    report["master_unchanged"] = file_sha256(source) == master_hash
    if not report["master_unchanged"]:
        report.update(status="blocked", reason="master_network_changed")
    report["manifest_file"] = str(output / "layer-manifest.json")
    write_json_atomic(output / "layer-manifest.json", report)
    return report


def master_network_layer(master_net_file: Path | str, layer: str) -> dict[str, Any]:
    """Describe the master itself as ``layer``: nothing is removed and the layer file is the master."""
    if layer not in NETWORK_LAYER_CLASSES:
        raise ValueError(f"Choose a network layer from {', '.join(NETWORK_LAYER_CLASSES)}.")
    source = Path(master_net_file).resolve(strict=True)
    digest = file_sha256(source)
    classes = NETWORK_LAYER_CLASSES[layer]
    return {"schema": "astra.network-layer/v1", "status": "no_effect", "reason": "layer_is_master", "layer": layer,
            "classes": list(classes) if classes is not None else None, "net_file": str(source), "sha256": digest,
            "derived_from_sha256": digest, "edge_count": len(_net_inventory(source)["edges"]), "removed_edge_count": 0,
            "identity": {"status": "no_effect", "reason": "layer_is_master"}}


def measure_mode_components(net_file: Path | str, modes: Iterable[str], *,
                            threshold: float = COMPONENT_SHARE_THRESHOLD) -> dict[str, Any]:
    """Measure, without pruning, how much of each mode's network its largest connected component holds.

    Ordinary edges are the nodes. A connection joins two of them when the mode is allowed on both lanes and on
    every internal lane on the way, crossings and walking areas included. Without walking areas, pedestrians
    change edges at any shared junction. A share below ``threshold`` needs review: edges outside the largest
    component cannot be reached from it.
    """
    wanted = frozenset(modes)
    modes = sorted(wanted)
    lane_modes: dict[str, frozenset[str]] = {}
    lane_node: dict[str, str] = {}
    mode_edges: dict[str, set[str]] = {mode: set() for mode in modes}
    edge_ends: dict[str, tuple[str, str]] = {}
    pairs: list[tuple[str, str]] = []
    walkingareas = False
    permissions = _permission_reader()
    for version, element in _direct_children(Path(net_file)):
        if element.tag == "edge":
            function = element.get("function") or ""
            walkingareas |= function == "walkingarea"
            edge_id = element.get("id", "")
            for lane in element.findall("lane"):
                allowed = permissions(lane, version) & wanted
                if not allowed:
                    continue
                lane_modes[lane.get("id", "")] = allowed
                lane_node[lane.get("id", "")] = lane.get("id", "") if function else edge_id
                if not function:
                    for mode in allowed:
                        mode_edges[mode].add(edge_id)
                    edge_ends[edge_id] = (element.get("from", ""), element.get("to", ""))
        elif element.tag == "connection":
            source = f'{element.get("from")}_{element.get("fromLane")}'
            # A movement through a junction continues from its first internal lane.
            following = element.get("via") or f'{element.get("to")}_{element.get("toLane")}'
            if source in lane_modes and following in lane_modes:
                pairs.append((source, following))
    parent: dict[str, str] = {}

    def find(node: str) -> str:
        parent.setdefault(node, node)
        while parent[node] != node:
            parent[node] = parent[parent[node]]
            node = parent[node]
        return node

    results = {}
    for mode in modes:
        parent.clear()
        for first, last in pairs:
            if mode in lane_modes[first] and mode in lane_modes[last]:
                parent[find(lane_node[first])] = find(lane_node[last])
        if mode == "pedestrian" and not walkingareas:
            at_node = defaultdict(list)
            for edge_id in mode_edges[mode]:
                for node in edge_ends[edge_id]:
                    at_node[node].append(edge_id)
            for edge_ids in at_node.values():
                for other in edge_ids[1:]:
                    parent[find(other)] = find(edge_ids[0])
        sizes = Counter(find(edge_id) for edge_id in mode_edges[mode])
        total, largest = len(mode_edges[mode]), max(sizes.values(), default=0)
        share = largest / total if total else None
        row = {"edge_count": total, "component_count": len(sizes), "largest_component_edge_count": largest,
               "outside_largest_component_edge_count": total - largest,
               "largest_component_share": None if share is None else round(share, 6)}
        if share is None:
            row.update(gate="review_required", reason="no_edge_allows_mode")
        elif share < threshold:
            row.update(gate="review_required", reason="largest_component_share_below_threshold")
        else:
            row["gate"] = "pass"
        results[mode] = row
    review = sorted(mode for mode, row in results.items() if row["gate"] != "pass")
    return {"status": "measured", "method": "streamed_connections_and_internal_lanes_without_pruning",
            "threshold": threshold, "network_has_walkingareas": walkingareas, "modes": results,
            "gate": "review_required" if review else "pass",
            **({"reason": "mode_network_needs_review", "review_modes": review} if review else {})}


_PLAIN_CYCLEWAY_VALUES = frozenset({"lane", "track", "opposite_lane", "opposite_track"})
_SIDED_CYCLEWAY_KEYS = ("cycleway:left", "cycleway:right", "cycleway:both")


def measure_plain_cycleway_tags(osm_file: Path | str, net_file: Path | str) -> dict[str, Any]:
    """Road ways tagged with a plain ``cycleway=lane|track`` and whether the network gave them a bicycle-only lane.

    The multimodal profile loads no bicycle typemap, so netconvert discards the compound type a plain tag builds
    (``cycleway.track|highway.primary``) and the way gets no bike lane; ``--osm.bike-access`` reads the side tags
    ``cycleway:left``, ``cycleway:right`` and ``cycleway:both``. Bicycles then share the carriageway where it allows
    them, and the mapped lane or track is missing from the bicycle network. Neither file is written.
    """
    source = Path(osm_file)
    tagged: dict[str, str] = {}
    with (gzip.open(source, "rb") if source.suffix == ".gz" else source.open("rb")) as handle:
        for _, element in ET.iterparse(handle):
            if element.tag == "way":
                tags = {tag.get("k", ""): tag.get("v", "") for tag in element.findall("tag")}
                value = tags.get("cycleway", "").strip().lower()
                if (tags.get("highway") and tags.get("highway") != "cycleway" and value in _PLAIN_CYCLEWAY_VALUES
                        and not any(key in tags for key in _SIDED_CYCLEWAY_KEYS)):
                    tagged[element.get("id", "")] = value
                element.clear()
            elif element.tag in {"node", "relation"}:
                element.clear()
    realised: set[str] = set()
    permissions = _permission_reader()
    for version, element in _direct_children(Path(net_file)):
        if element.tag == "edge" and not element.get("function"):
            for lane in element.findall("lane"):
                if permissions(lane, version) == {"bicycle"}:
                    realised.add((_orig_id(lane) or "").lstrip("-"))
    unrealised = sorted((way_id for way_id in tagged if way_id not in realised), key=str)
    return {"status": "measured", "basis": "bicycle-only lane on an edge whose origId is the tagged way",
            "plain_cycleway_way_count": len(tagged), "without_bicycle_lane_count": len(unrealised),
            "without_bicycle_lane_by_value": dict(sorted(Counter(tagged[way_id] for way_id in unrealised).items())),
            "without_bicycle_lane_way_ids": unrealised[:_EXAMPLE_LIMIT],
            **({"reason": "plain_cycleway_tag_without_bicycle_lane"} if unrealised else {})}


def measure_tls_cycles(net_file: Path | str, *, threshold_s: float = LONG_CYCLE_THRESHOLD_S) -> dict[str, Any]:
    """Cycle length of every signal program, and the programs above ``threshold_s``.

    netconvert can inflate the program of a joined controller once crossings are added, so long cycles are
    listed for review; the lengths are measured, not judged against field timing.
    """
    cycles = [{"tl_id": element.get("id", ""), "program_id": element.get("programID", ""), "type": element.get("type", ""),
               "phase_count": len(element.findall("phase")),
               "cycle_s": round(sum(float(phase.get("duration", "0")) for phase in element.findall("phase")), 6)}
              for _, element in _direct_children(Path(net_file)) if element.tag == "tlLogic"]
    long_cycles = [row for row in cycles if row["cycle_s"] > threshold_s]
    return {"status": "measured", "threshold_s": threshold_s, "controller_count": len(cycles),
            "max_cycle_s": max((row["cycle_s"] for row in cycles), default=None),
            "long_cycle_controllers": long_cycles, "cycles": cycles}


def routes_through_controllers(net_file: Path | str, route_file: Path | str, vehicle_ids: Iterable[str],
                               controller_ids: Iterable[str]) -> dict[str, Any]:
    """Which of ``vehicle_ids`` have a route through a link signalled by one of ``controller_ids``.

    A route passes a controller when two consecutive route edges are joined by a connection the controller
    signals. This names the signals on each route; it does not show where a vehicle waited.
    """
    wanted_tls, wanted = set(controller_ids), set(vehicle_ids)
    signalled: dict[tuple[str, str], set[str]] = defaultdict(set)
    for _, element in _direct_children(Path(net_file)):
        if element.tag == "connection" and element.get("tl") in wanted_tls:
            signalled[(element.get("from", ""), element.get("to", ""))].add(element.get("tl", ""))
    named_routes: dict[str, list[str]] = {}
    routes: dict[str, list[str]] = {}
    for _, element in _direct_children(Path(route_file)):
        if element.tag == "route" and element.get("id"):
            named_routes[element.get("id", "")] = element.get("edges", "").split()
        elif element.tag in {"vehicle", "trip", "flow"} and element.get("id") in wanted:
            route = element.find("route")
            routes[element.get("id", "")] = (route.get("edges", "").split() if route is not None
                                             else named_routes.get(element.get("route", ""), []))
    vehicles, counts = [], Counter()
    for vehicle_id in sorted(routes, key=str):
        edges = routes[vehicle_id]
        passed = sorted({tl for pair in zip(edges, edges[1:]) for tl in signalled.get(pair, ())})
        if passed:
            vehicles.append({"vehicle": vehicle_id, "controllers": passed})
            counts.update(passed)
    return {"status": "measured", "basis": "consecutive route edges joined by a connection the controller signals",
            "vehicle_count": len(wanted), "routed_vehicle_count": len(routes),
            "vehicles_through_controllers_count": len(vehicles), "controllers": dict(counts.most_common()),
            "vehicles": vehicles[:_EXAMPLE_LIMIT]}
