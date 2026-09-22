"""Materialize a diagnostic corridor candidate from an official splice plan.

This is intentionally a *candidate* materializer.  It replaces the official
HH-SIB axis interval occupied by a local MAP cell, rewires the local MAP
approach edges to deterministic splice nodes, and emits explicit axis-to-MAP
connections whenever lane conservation is proven.  An unresolved merge is
left unconnected and recorded as ``review_required``; the module never lets
netconvert's inferred connection become a promotion claim.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import xml.etree.ElementTree as ET
from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from torii_sumo.core.artifact_io import write_json_atomic, write_text_atomic
from torii_sumo.core.candidate_contracts import file_sha256
from torii_sumo.core.command_runner import run_command
from torii_sumo.core.connection_mode_audit import audit_network_connection_mode
from torii_sumo.core.hamburg_official_corridor_geometry import (
    _lane_endpoints,
    _reanchor_connection_shapes,
)
from torii_sumo.core.sumo_commands import run_sumo_load_audit
from torii_sumo.core.surface_overlap_audit import audit_sumo_lane_junction_surface_overlaps


OFFICIAL_SPLICE_MATERIALIZATION_SCHEMA = (
    "torii.hamburg-official-map-hh-sib-splice-materialization/v1"
)
_MOTOR_VCLASSES = "passenger taxi bus coach delivery truck motorcycle emergency"
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
# ponytail: one deterministic clearance is enough here; derive a polygon-aware
# value later if a measured SUMO junction footprint requires more precision.
_AXIS_SPLICE_CLEARANCE_M = 12.0
_CORE_AXIS_CLEARANCE_M = 4.0
# ponytail: boundary splice owners are represented as near-zero-area SUMO
# nodes; the local MAP core remains the only physical junction footprint.
_SPLICE_NODE_HALF_SIZE_M = 0.01


class OfficialSpliceMaterializationError(ValueError):
    """Raised when source or splice evidence cannot be verified."""


def materialize_hamburg_official_splice_candidate(
    *,
    hh_sib_nodes_file: str | Path,
    hh_sib_edges_file: str | Path,
    hh_sib_types_file: str | Path,
    intersection_sources: Mapping[str, str | Path],
    splice_plan: str | Path | Mapping[str, Any],
    output_dir: str | Path,
    netconvert_binary: str = "netconvert",
    sumo_binary: str = "sumo",
    timeout_seconds: float = 240.0,
    command_runner: Callable[..., object] = run_command,
) -> dict[str, Any]:
    """Write and compile one separate official MAP/HH-SIB candidate.

    ``intersection_sources`` maps each MAP node id to a directory containing
    ``hamburg-map-<id>.nod.xml``, ``.edg.xml``, ``.con.xml``, ``.tll.xml`` and
    ``.typ.xml``.  Source files are never changed.  The output directory must
    be new or empty so a rerun cannot silently replace a previous candidate.
    """

    axis_nodes = _existing(hh_sib_nodes_file, "HH-SIB nodes")
    axis_edges = _existing(hh_sib_edges_file, "HH-SIB edges")
    axis_types = _existing(hh_sib_types_file, "HH-SIB types")
    plan, plan_identity = _load_json_like(splice_plan, "official splice plan")
    if plan.get("schema") != "torii.hamburg-official-map-hh-sib-splice-plan/v1":
        raise OfficialSpliceMaterializationError("official splice plan schema is invalid")
    if str(plan.get("status")) not in {"pass", "review_required"}:
        raise OfficialSpliceMaterializationError("official splice plan is not executable evidence")
    _verify_plan_axis_hash(plan, axis_edges)

    destination = Path(output_dir).expanduser().resolve()
    destination.mkdir(parents=True, exist_ok=True)
    if any(destination.iterdir()):
        raise OfficialSpliceMaterializationError(
            "output_dir must be empty; choose a new versioned candidate directory"
        )

    local = _load_local_sources(intersection_sources)
    local_core_shapes = _load_local_core_shapes(local)
    events = _index_events(plan)
    axis_root = ET.parse(axis_edges).getroot()
    axis_nodes_root = ET.parse(axis_nodes).getroot()
    axis_types_root = ET.parse(axis_types).getroot()
    fragments, event_links, axis_cut_nodes = _build_axis_fragments(
        axis_root,
        events,
        core_shapes=local_core_shapes,
    )
    local_nodes, local_edges, local_connections, local_tllogics, local_types, local_bridge_evidence = (
        _build_local_components(local, events)
    )
    nodes = _unique_elements(
        [ET.fromstring(ET.tostring(node)) for node in axis_nodes_root.findall("node")]
        + axis_cut_nodes
        + local_nodes
        + [
            ET.Element(
                "node",
                {
                    "id": str(event["splice_node_id"]),
                    "x": f"{float(_splice_xy(event)[0]):.3f}",
                    "y": f"{float(_splice_xy(event)[1]):.3f}",
                    "type": "dead_end",
                    "shape": _small_node_shape(_splice_xy(event)),
                },
            )
            for event in events
            if str(event["splice_node_id"]) not in {node.attrib.get("id") for node in axis_cut_nodes}
        ]
    )
    edges = _unique_elements(fragments + local_edges)
    bridge_connections, bridge_status = _build_bridge_connections(
        events,
        event_links=event_links,
        local_edges=local_edges,
        local_connections=local_connections,
    )
    connections = _unique_connections(local_connections + bridge_connections)
    tllogics = _unique_elements(local_tllogics)
    types = _unique_elements(
        [ET.fromstring(ET.tostring(item)) for item in axis_types_root.findall("type")] + local_types
    )

    plain = {
        "nodes": destination / "hamburg_official_splice_candidate.nod.xml",
        "edges": destination / "hamburg_official_splice_candidate.edg.xml",
        "connections": destination / "hamburg_official_splice_candidate.con.xml",
        "tllogics": destination / "hamburg_official_splice_candidate.tll.xml",
        "types": destination / "hamburg_official_splice_candidate.typ.xml",
    }
    _write_xml(plain["nodes"], "nodes", nodes)
    _write_xml(plain["edges"], "edges", edges)
    _write_xml(plain["connections"], "connections", connections)
    _write_xml(plain["tllogics"], "tlLogics", tllogics)
    _write_xml(plain["types"], "types", types)

    output_net = destination / "hamburg_official_splice_candidate.net.xml"
    command = [
        str(netconvert_binary),
        "--node-files",
        str(plain["nodes"]),
        "--edge-files",
        str(plain["edges"]),
        "--connection-files",
        str(plain["connections"]),
        "--tllogic-files",
        str(plain["tllogics"]),
        "--type-files",
        str(plain["types"]),
        "--no-turnarounds",
        "--junctions.endpoint-shape",
        "true",
        # Use the authoritative MAP core face as the minimal physical
        # junction footprint; otherwise long curved lane endpoints can make
        # netconvert expand the core over adjacent HH-SIB junctions.
        "--junctions.minimal-shape",
        "true",
        "--offset.disable-normalization",
        "true",
        "--output-file",
        str(output_net),
    ]
    netconvert_passes = [
        _result_dict(command_runner(command, cwd=destination, timeout_seconds=timeout_seconds))
    ]
    # netconvert trims external lane shapes at generated junction polygons.
    # Re-anchor the preserved MAP movement curves to those compiled endpoints,
    # then compile the same hash-bound evidence until it reaches a fixed point.
    for _ in range(3):
        if netconvert_passes[-1].get("status") != "pass" or not output_net.is_file():
            break
        compiled_endpoints = _lane_endpoints(ET.parse(output_net).getroot().findall("edge"))
        if not _reanchor_connection_shapes(connections, compiled_endpoints):
            break
        _write_xml(plain["connections"], "connections", connections)
        netconvert_passes.append(
            _result_dict(command_runner(command, cwd=destination, timeout_seconds=timeout_seconds))
        )
    netconvert = dict(netconvert_passes[-1])
    netconvert["passes"] = netconvert_passes
    compiled = netconvert.get("status") == "pass" and output_net.is_file()
    load = (
        run_sumo_load_audit(
            net_file=output_net,
            output_dir=destination / "sumo_load",
            sumo_binary=sumo_binary,
            timeout_seconds=timeout_seconds,
            command_runner=command_runner,
        )
        if compiled
        else {"status": "not_run"}
    )
    overlap = (
        audit_sumo_lane_junction_surface_overlaps(
            output_net,
            report_file=destination / "surface_overlap" / "surface-overlap.json",
        )
        if compiled
        else {"status": "not_run"}
    )
    connection_mode = (
        audit_network_connection_mode(ET.parse(output_net).getroot())
        if compiled
        else {"status": "not_run"}
    )
    review_reasons = []
    if str(plan.get("status")) != "pass":
        review_reasons.append("official_splice_plan_contains_review_required_approaches")
    if int(bridge_status["unresolved_count"]) > 0:
        review_reasons.append("some_axis_to_map_lane_bindings_are_not_lane_conservative")
    gates = {
        "source_hashes": "pass",
        "plainxml_written": "pass",
        "netconvert": "pass" if compiled else "blocked",
        "sumo_load": load.get("status", "blocked") if compiled else "not_run",
        "surface_overlap": overlap.get("status", "blocked") if compiled else "not_run",
        "connection_mode": connection_mode.get("status", "blocked") if compiled else "not_run",
        "axis_to_map_lane_binding": bridge_status["status"],
        "automatic_promotion": "blocked",
    }
    status = "review_required" if compiled else "blocked"
    manifest: dict[str, Any] = {
        "schema": OFFICIAL_SPLICE_MATERIALIZATION_SCHEMA,
        "status": status,
        "execution_gate": "pass" if compiled else "blocked",
        "claim_status": "official-map-hh-sib-diagnostic-candidate",
        "automatic_promotion_gate": "blocked",
        "source": {
            "hh_sib_nodes": _file_identity(axis_nodes),
            "hh_sib_edges": _file_identity(axis_edges),
            "hh_sib_types": _file_identity(axis_types),
            "splice_plan": plan_identity,
            "intersections": {
                node_id: {
                    suffix: _file_identity(path)
                    for suffix, path in files.items()
                }
                for node_id, files in local.items()
            },
        },
        "plainxml": {key: _file_identity(path) for key, path in plain.items()},
        "materialization_policy": {
            "axis_splice_clearance_m": _AXIS_SPLICE_CLEARANCE_M,
            "core_axis_clearance_m": _CORE_AXIS_CLEARANCE_M,
            "splice_node_half_size_m": _SPLICE_NODE_HALF_SIZE_M,
            "splice_anchor_policy": "map_event_coordinate",
            "movement_curve_policy": "compiled_lane_endpoint_reanchoring",
        },
        "network": {
            "path": str(output_net),
            "sha256": file_sha256(output_net) if output_net.is_file() else None,
        },
        "netconvert": {"command": command, "result": netconvert},
        "bridge_connections": bridge_status,
        "surface_overlap_audit": overlap,
        "sumo_load_audit": load,
        "connection_mode_audit": connection_mode,
        "gates": gates,
        "review_reasons": review_reasons,
        "claim_boundary": (
            "This candidate proves only that the supplied official axis and local MAP components were compiled "
            "under the hash-bound splice operations. Unresolved lane bindings remain diagnostic; SUMO load or "
            "routeability cannot promote them to field topology, signal timing, or demand truth."
        ),
    }
    manifest["manifest_file"] = str(destination / "hamburg_official_splice_candidate.manifest.json")
    write_json_atomic(Path(manifest["manifest_file"]), manifest, ensure_ascii=False, sort_keys=True)
    manifest["manifest_sha256"] = file_sha256(Path(manifest["manifest_file"]))
    return manifest


def _load_local_sources(sources: Mapping[str, str | Path]) -> dict[str, dict[str, Path]]:
    if not isinstance(sources, Mapping) or not sources:
        raise OfficialSpliceMaterializationError("intersection_sources must not be empty")
    result: dict[str, dict[str, Path]] = {}
    for node_id, raw_dir in sorted(sources.items(), key=lambda item: _identifier_key(str(item[0]))):
        directory = Path(raw_dir).expanduser().resolve()
        if not directory.is_dir():
            raise OfficialSpliceMaterializationError(f"local intersection directory does not exist: {directory}")
        files: dict[str, Path] = {}
        prefix = f"hamburg-map-{node_id}."
        for suffix in ("nod.xml", "edg.xml", "con.xml", "tll.xml", "typ.xml"):
            path = directory / f"{prefix}{suffix}"
            if not path.is_file():
                raise OfficialSpliceMaterializationError(f"missing local MAP PlainXML file: {path}")
            files[suffix.split(".", 1)[0]] = path
        result[str(node_id)] = files
    return result


def _index_events(plan: Mapping[str, Any]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for approach in plan.get("approaches", []):
        if not isinstance(approach, Mapping):
            continue
        event = approach.get("splice_event")
        if not isinstance(event, Mapping):
            continue
        result.append(
            {
                **{str(key): value for key, value in event.items()},
                "node_id": str(approach.get("node_id", "")),
                "map_role": str(approach.get("map_role", "")),
                "approach_id": str(approach.get("approach_id", "")),
                "status": str(approach.get("status", "review_required")),
                "map_edge_id": str(approach.get("map_edge_id", "")),
                "old_boundary_node_id": str(approach.get("old_boundary_node_id", "")),
            }
        )
    if not result:
        raise OfficialSpliceMaterializationError("splice plan contains no executable approach events")
    return sorted(result, key=lambda item: (_identifier_key(item["node_id"]), item["map_role"], _identifier_key(item["approach_id"])))


def _load_local_core_shapes(local: Mapping[str, Mapping[str, Path]]) -> dict[str, list[tuple[float, float]]]:
    result: dict[str, list[tuple[float, float]]] = {}
    for node_id, files in local.items():
        root = ET.parse(files["nod"]).getroot()
        for node in root.findall("node"):
            if not str(node.attrib.get("id", "")).endswith("-core"):
                continue
            shape = _parse_shape(node.attrib.get("shape", ""))
            if len(shape) >= 3:
                result[str(node_id)] = shape
            break
    return result


def _build_axis_fragments(
    root: ET.Element,
    events: Sequence[Mapping[str, Any]],
    *,
    core_shapes: Mapping[str, Sequence[tuple[float, float]]],
) -> tuple[list[ET.Element], dict[str, dict[str, Any]], list[ET.Element]]:
    by_corridor: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for event in events:
        by_corridor[str(event["axis_corridor_id"])].append(event)
    forbidden = _forbidden_intervals(by_corridor)
    _add_core_clearance_intervals(root, by_corridor, core_shapes, forbidden)
    fragments: list[ET.Element] = []
    event_links: dict[str, dict[str, Any]] = {}
    cut_nodes: dict[str, ET.Element] = {}
    for source in root.findall("edge"):
        edge_id = str(source.attrib.get("id", ""))
        params = _params(source)
        orig_id = str(params.get("origId", ""))
        direction = str(params.get("torii:station_direction", ""))
        corridor_id = _corridor_id(orig_id, direction)
        if corridor_id not in by_corridor:
            # This edge belongs to the selected official scope but is not
            # touched by any local MAP cell; retain it verbatim.
            fragments.append(ET.fromstring(ET.tostring(source)))
            continue
        station_from = float(params["torii:station_from_m"])
        station_to = float(params["torii:station_to_m"])
        lo, hi = min(station_from, station_to), max(station_from, station_to)
        boundaries = {lo, hi}
        for event in by_corridor[corridor_id]:
            station = float(event["axis_station_m"])
            if lo + 1e-6 < station < hi - 1e-6:
                boundaries.add(station)
        for left, right in forbidden.get(corridor_id, []):
            if lo + 1e-6 < left < hi - 1e-6:
                boundaries.add(left)
            if lo + 1e-6 < right < hi - 1e-6:
                boundaries.add(right)
        ordered = sorted(boundaries)
        for index, (left, right) in enumerate(zip(ordered, ordered[1:])):
            if right - left <= 1e-6 or _inside_any((left + right) / 2.0, forbidden.get(corridor_id, [])):
                continue
            from_station, to_station = _edge_orientation_stations(direction, left, right)
            from_node = _axis_node_at_station(
                source,
                from_station,
                corridor_id=corridor_id,
                events=by_corridor[corridor_id],
                cut_nodes=cut_nodes,
            )
            to_node = _axis_node_at_station(
                source,
                to_station,
                corridor_id=corridor_id,
                events=by_corridor[corridor_id],
                cut_nodes=cut_nodes,
            )
            if from_node == to_node:
                continue
            item = ET.fromstring(ET.tostring(source))
            item.set("id", f"{edge_id}#splice-{index}")
            item.set("from", from_node)
            item.set("to", to_node)
            shape = _clip_shape(
                _parse_shape(source.attrib.get("shape", "")), source, from_station, to_station
            )
            for event in by_corridor[corridor_id]:
                event_node = str(event["splice_node_id"])
                if from_node == event_node:
                    shape[0] = _splice_xy(event)
                if to_node == event_node:
                    shape[-1] = _splice_xy(event)
            item.set("shape", _shape(shape))
            _set_param(item, "torii:station_from_m", str(_rounded(left)))
            _set_param(item, "torii:station_to_m", str(_rounded(right)))
            _set_param(item, "torii:splice_source_edge", edge_id)
            fragments.append(item)
            for event in by_corridor[corridor_id]:
                event_node = str(event["splice_node_id"])
                if to_node == event_node:
                    event_links.setdefault(event_node, {})["incoming_axis_edge_id"] = str(item.attrib["id"])
                    event_links[event_node]["axis_lane_count"] = int(item.attrib.get("numLanes", "0"))
                if from_node == event_node:
                    event_links.setdefault(event_node, {})["outgoing_axis_edge_id"] = str(item.attrib["id"])
                    event_links[event_node]["axis_lane_count"] = int(item.attrib.get("numLanes", "0"))
    return fragments, event_links, list(cut_nodes.values())


def _add_core_clearance_intervals(
    root: ET.Element,
    by_corridor: Mapping[str, Sequence[Mapping[str, Any]]],
    core_shapes: Mapping[str, Sequence[tuple[float, float]]],
    forbidden: dict[str, list[tuple[float, float]]],
) -> None:
    for source in root.findall("edge"):
        params = _params(source)
        corridor_id = _corridor_id(
            str(params.get("origId", "")), str(params.get("torii:station_direction", ""))
        )
        members = by_corridor.get(corridor_id)
        if not members:
            continue
        for event in members:
            polygon = core_shapes.get(str(event["node_id"]))
            if not polygon:
                continue
            interval = _source_polygon_station_interval(source, polygon)
            if interval is None:
                continue
            left, right = interval
            forbidden.setdefault(corridor_id, []).append(
                (left - _CORE_AXIS_CLEARANCE_M, right + _CORE_AXIS_CLEARANCE_M)
            )
    for corridor_id, intervals in list(forbidden.items()):
        forbidden[corridor_id] = _merge_intervals(intervals)


def _source_polygon_station_interval(
    source: ET.Element,
    polygon: Sequence[tuple[float, float]],
) -> tuple[float, float] | None:
    shape = _parse_shape(source.attrib.get("shape", ""))
    if len(shape) < 2 or len(polygon) < 3:
        return None
    points: list[tuple[float, float]] = [
        point
        for point in shape
        if _point_in_polygon(point, polygon)
    ]
    for first, second in zip(shape, shape[1:]):
        for left, right in zip(polygon, [*polygon[1:], polygon[0]]):
            point = _segment_intersection(first, second, left, right)
            if point is not None:
                points.append(point)
    if not points:
        return None
    stations = [_station_at_source_point(source, point) for point in points]
    return (min(stations), max(stations))


def _forbidden_intervals(
    by_corridor: Mapping[str, Sequence[Mapping[str, Any]]],
) -> dict[str, list[tuple[float, float]]]:
    result: dict[str, list[tuple[float, float]]] = {}
    for corridor_id, members in by_corridor.items():
        by_node: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
        for event in members:
            by_node[str(event["node_id"])].append(event)
        intervals: list[tuple[float, float]] = []
        for node_events in by_node.values():
            # Build the clearance on the junction-facing side of *each* event.
            # A corridor can carry two one-way events for the same physical
            # junction (one in each direction).  Treating those events as one
            # min/max interval erases both event boundaries and makes the
            # bridge unable to bind.  Per-event intervals merge naturally while
            # preserving the station at either outer boundary.
            for event in node_events:
                station = float(event["axis_station_m"])
                junction = event.get("junction_station_m")
                if junction is None:
                    left = right = station
                else:
                    junction_station = float(junction)
                    left, right = min(station, junction_station), max(station, junction_station)
                    if station < junction_station:
                        right += _AXIS_SPLICE_CLEARANCE_M
                    elif station > junction_station:
                        left -= _AXIS_SPLICE_CLEARANCE_M
                intervals.append((left, right))
        result[corridor_id] = _merge_intervals(intervals)
    return result


def _build_local_components(
    local: Mapping[str, Mapping[str, Path]],
    events: Sequence[Mapping[str, Any]],
) -> tuple[list[ET.Element], list[ET.Element], list[ET.Element], list[ET.Element], list[ET.Element], dict[str, Any]]:
    event_by_edge = {str(event["map_edge_id"]): event for event in events}
    nodes: list[ET.Element] = []
    edges: list[ET.Element] = []
    connections: list[ET.Element] = []
    tllogics: list[ET.Element] = []
    types: list[ET.Element] = []
    bridge: dict[str, Any] = {"status": "pass", "resolved_count": 0, "unresolved_count": 0, "unresolved": []}
    for node_id, files in sorted(local.items(), key=lambda item: _identifier_key(item[0])):
        nod_root = ET.parse(files["nod"]).getroot()
        for node in nod_root.findall("node"):
            if node.attrib.get("type") == "traffic_light" or node.attrib.get("id", "").endswith("-core"):
                item = ET.fromstring(ET.tostring(node))
                if not str(item.attrib.get("id", "")).endswith("-core"):
                    item.attrib.pop("shape", None)
                nodes.append(item)
        edg_root = ET.parse(files["edg"]).getroot()
        for source in edg_root.findall("edge"):
            item = ET.fromstring(ET.tostring(source))
            edge_id = str(item.attrib.get("id", ""))
            event = event_by_edge.get(edge_id)
            if event is not None:
                splice_node = str(event["splice_node_id"])
                if str(event["map_role"]) == "ingress":
                    item.set("from", splice_node)
                else:
                    item.set("to", splice_node)
                item.attrib.pop("fringe", None)
                _clip_local_edge_lanes(item, event)
            edges.append(item)
        con_root = ET.parse(files["con"]).getroot()
        connections.extend(ET.fromstring(ET.tostring(item)) for item in con_root.findall("connection"))
        tll_root = ET.parse(files["tll"]).getroot()
        tllogics.extend(ET.fromstring(ET.tostring(item)) for item in tll_root)
        typ_root = ET.parse(files["typ"]).getroot()
        types.extend(ET.fromstring(ET.tostring(item)) for item in typ_root.findall("type"))
    return nodes, edges, connections, tllogics, types, bridge


def _clip_local_edge_lanes(edge: ET.Element, event: Mapping[str, Any]) -> None:
    raw_event = event.get("map_event_xy")
    axis_xy = event.get("axis_xy")
    if not _valid_point(raw_event) or not _valid_point(axis_xy):
        return
    cuts: list[tuple[ET.Element, list[tuple[float, float]], tuple[float, float]]] = []
    for lane in edge.findall("lane"):
        points = _parse_shape(lane.attrib.get("shape", ""))
        if len(points) < 2:
            continue
        projection = _project_polyline(tuple(float(value) for value in raw_event[:2]), points)
        if str(event["map_role"]) == "ingress":
            clipped = _clip_polyline_fraction(points, projection["fraction"], 1.0)
        else:
            clipped = _clip_polyline_fraction(points, 0.0, projection["fraction"])
        cuts.append((lane, clipped, projection["point"]))
    if not cuts:
        return
    for lane, points, _cut in cuts:
        lane.set("shape", _shape(points))


def _build_bridge_connections(
    events: Sequence[Mapping[str, Any]],
    *,
    event_links: Mapping[str, Mapping[str, Any]],
    local_edges: Sequence[ET.Element],
    local_connections: Sequence[ET.Element],
) -> tuple[list[ET.Element], dict[str, Any]]:
    edge_lanes: dict[str, list[str]] = {}
    for edge in local_edges:
        edge_lanes[str(edge.attrib.get("id", ""))] = [
            str(lane.attrib.get("type", "")).removeprefix("official-map-lane-")
            for lane in sorted(edge.findall("lane"), key=lambda item: int(item.attrib.get("index", "0")))
        ]
    result: list[ET.Element] = []
    status: dict[str, Any] = {"status": "pass", "resolved_count": 0, "unresolved_count": 0, "unresolved": []}
    for event in events:
        node = str(event["splice_node_id"])
        links = event_links.get(node, {})
        local_edge = str(event["map_edge_id"])
        map_lanes = edge_lanes.get(local_edge, [])
        axis_count = int(links.get("axis_lane_count", 0))
        map_count = len(map_lanes)
        pairs: list[tuple[int, int]] = []
        if event["kind"] == "coherent_boundary" and axis_count == map_count:
            pairs = [(index, index) for index in range(axis_count)]
        elif event["kind"] == "official_merge_event":
            through = [str(value) for value in event.get("map_lane_ids_not_marked_as_starting_at_event", [])]
            through_indices = sorted(index for index, lane_id in enumerate(map_lanes) if lane_id in through)
            if len(through_indices) != axis_count:
                through_indices = _derive_merge_through_indices(
                    event,
                    map_lanes,
                    local_connections,
                    axis_count,
                    through_indices,
                )
            if len(through_indices) == axis_count and axis_count > 0:
                pairs = [(axis_index, through_indices[axis_index]) for axis_index in range(axis_count)]
        direction = str(event["map_role"])
        if not pairs:
            status["status"] = "review_required"
            status["unresolved_count"] += 1
            status["unresolved"].append(
                {
                    "splice_node_id": node,
                    "map_edge_id": local_edge,
                    "axis_lane_count": axis_count,
                    "map_lane_count": map_count,
                    "reason": "axis_to_map_lane_order_or_conservation_is_not_unique",
                }
            )
            continue
        if direction == "ingress":
            axis_edge = links.get("incoming_axis_edge_id")
            from_edge, to_edge = axis_edge, local_edge
        else:
            axis_edge = links.get("outgoing_axis_edge_id")
            from_edge, to_edge = local_edge, axis_edge
        if not axis_edge:
            status["status"] = "review_required"
            status["unresolved_count"] += 1
            status["unresolved"].append({"splice_node_id": node, "reason": "axis_fragment_does_not_reach_splice_node"})
            continue
        for from_lane, to_lane in pairs:
            result.append(
                ET.Element(
                    "connection",
                    {
                        "from": str(from_edge),
                        "to": str(to_edge),
                        "fromLane": str(from_lane if direction == "ingress" else to_lane),
                        "toLane": str(to_lane if direction == "ingress" else from_lane),
                    },
                )
            )
        status["resolved_count"] += 1
    return result, status


def _derive_merge_through_indices(
    event: Mapping[str, Any],
    map_lanes: Sequence[str],
    local_connections: Sequence[ET.Element],
    axis_count: int,
    known_indices: Sequence[int],
) -> list[int]:
    """Recover a unique through-lane group from MAP movement provenance.

    A lane may be tagged as starting at a merge point even when it continues
    through the junction.  Only accept the recovery when the known through
    lanes and exactly ``axis_count`` MAP lanes share the same destination-edge
    signature; otherwise the original fail-closed result is retained.
    """
    if not known_indices or axis_count <= 0:
        return list(known_indices)
    edge_id = str(event["map_edge_id"])
    destinations: dict[int, set[str]] = defaultdict(set)
    for connection in local_connections:
        if str(connection.attrib.get("from", "")) != edge_id:
            continue
        try:
            lane = int(connection.attrib.get("fromLane", ""))
        except (TypeError, ValueError):
            continue
        destination = str(connection.attrib.get("to", ""))
        if destination:
            destinations.setdefault(lane, set()).add(destination)
    signatures = {
        index: frozenset(destinations.get(index, set()))
        for index in range(len(map_lanes))
    }
    known_signature = signatures.get(known_indices[0], frozenset())
    if not known_signature or any(signatures.get(index) != known_signature for index in known_indices):
        return list(known_indices)
    candidates = [index for index, signature in signatures.items() if signature == known_signature]
    return candidates if len(candidates) == axis_count else list(known_indices)


def _axis_node_at_station(
    source: ET.Element,
    station: float,
    *,
    corridor_id: str,
    events: Sequence[Mapping[str, Any]],
    cut_nodes: dict[str, ET.Element],
) -> str:
    direction = _params(source).get("torii:station_direction", "")
    station_from = float(_params(source)["torii:station_from_m"])
    station_to = float(_params(source)["torii:station_to_m"])
    # Event nodes replace source-axis boundary nodes.  Resolve them first so
    # an event that lands exactly on a source edge endpoint still receives its
    # splice node and therefore remains bridgeable to the local MAP edge.
    for event in events:
        if abs(station - float(event["axis_station_m"])) <= 1e-4:
            return str(event["splice_node_id"])
    if abs(station - station_from) <= 1e-5:
        return str(source.attrib["from"] if direction == "with_stationing" else source.attrib["to"])
    if abs(station - station_to) <= 1e-5:
        return str(source.attrib["to"] if direction == "with_stationing" else source.attrib["from"])
    token = f"{corridor_id}:{station:.6f}"
    node_id = "hh-axis-cut-" + hashlib.sha256(token.encode("utf-8")).hexdigest()[:16]
    if node_id not in cut_nodes:
        point = _point_at_source_station(source, station)
        cut_nodes[node_id] = ET.Element(
            "node",
            {"id": node_id, "x": f"{point[0]:.3f}", "y": f"{point[1]:.3f}", "type": "priority"},
        )
    return node_id


def _splice_xy(event: Mapping[str, Any]) -> tuple[float, float]:
    value = event.get("map_event_xy")
    if _valid_point(value):
        return (float(value[0]), float(value[1]))
    axis = event["axis_xy"]
    return (float(axis[0]), float(axis[1]))


def _small_node_shape(point: Sequence[float]) -> str:
    x, y = float(point[0]), float(point[1])
    half = _SPLICE_NODE_HALF_SIZE_M
    return _shape([(x - half, y - half), (x + half, y - half), (x + half, y + half), (x - half, y + half)])


def _point_at_source_station(source: ET.Element, station: float) -> tuple[float, float]:
    points = _parse_shape(source.attrib.get("shape", ""))
    fraction = _station_fraction(source, station)
    return _polyline_fraction_point(points, fraction)


def _station_at_source_point(source: ET.Element, point: Sequence[float]) -> float:
    points = _parse_shape(source.attrib.get("shape", ""))
    projection = _project_polyline((float(point[0]), float(point[1])), points)
    params = _params(source)
    start = float(params["torii:station_from_m"])
    end = float(params["torii:station_to_m"])
    raw = projection["fraction"] if params.get("torii:station_direction") == "with_stationing" else 1.0 - projection["fraction"]
    return start + raw * (end - start)


def _point_in_polygon(
    point: Sequence[float],
    polygon: Sequence[Sequence[float]],
) -> bool:
    x, y = float(point[0]), float(point[1])
    inside = False
    for first, second in zip(polygon, [*polygon[1:], polygon[0]]):
        if (float(first[1]) > y) != (float(second[1]) > y):
            crossing = (float(second[0]) - float(first[0])) * (y - float(first[1])) / (
                float(second[1]) - float(first[1])
            ) + float(first[0])
            if x < crossing:
                inside = not inside
    return inside


def _segment_intersection(
    first: Sequence[float],
    second: Sequence[float],
    third: Sequence[float],
    fourth: Sequence[float],
) -> tuple[float, float] | None:
    ax, ay = float(first[0]), float(first[1])
    bx, by = float(second[0]), float(second[1])
    cx, cy = float(third[0]), float(third[1])
    dx, dy = float(fourth[0]), float(fourth[1])
    abx, aby = bx - ax, by - ay
    cdx, cdy = dx - cx, dy - cy
    denominator = abx * cdy - aby * cdx
    if abs(denominator) <= 1e-9:
        return None
    acx, acy = cx - ax, cy - ay
    along_first = (acx * cdy - acy * cdx) / denominator
    along_second = (acx * aby - acy * abx) / denominator
    if -1e-9 <= along_first <= 1.0 + 1e-9 and -1e-9 <= along_second <= 1.0 + 1e-9:
        return (ax + along_first * abx, ay + along_first * aby)
    return None


def _clip_shape(points: Sequence[tuple[float, float]], source: ET.Element, start_station: float, end_station: float) -> list[tuple[float, float]]:
    first = _station_fraction(source, start_station)
    second = _station_fraction(source, end_station)
    if first <= second:
        return _clip_polyline_fraction(points, first, second)
    return list(reversed(_clip_polyline_fraction(points, second, first)))


def _station_fraction(source: ET.Element, station: float) -> float:
    params = _params(source)
    start = float(params["torii:station_from_m"])
    end = float(params["torii:station_to_m"])
    raw = (station - start) / (end - start)
    return max(0.0, min(1.0, raw if params.get("torii:station_direction") == "with_stationing" else 1.0 - raw))


def _edge_orientation_stations(direction: str, left: float, right: float) -> tuple[float, float]:
    return (left, right) if direction == "with_stationing" else (right, left)


def _inside_any(value: float, intervals: Sequence[tuple[float, float]]) -> bool:
    return any(left + 1e-5 < value < right - 1e-5 for left, right in intervals)


def _merge_intervals(intervals: Sequence[tuple[float, float]]) -> list[tuple[float, float]]:
    ordered = sorted((min(a, b), max(a, b)) for a, b in intervals if b - a > 1e-5)
    result: list[tuple[float, float]] = []
    for left, right in ordered:
        if result and left <= result[-1][1] + 1e-5:
            result[-1] = (result[-1][0], max(result[-1][1], right))
        else:
            result.append((left, right))
    return result


def _project_polyline(point: tuple[float, float], points: Sequence[tuple[float, float]]) -> dict[str, Any]:
    lengths = [_distance(a, b) for a, b in zip(points, points[1:])]
    total = sum(lengths)
    if total <= 0:
        return {"fraction": 0.0, "point": points[0], "distance_m": _distance(point, points[0])}
    best: tuple[float, float, tuple[float, float]] | None = None
    preceding = 0.0
    for first, second, length in zip(points, points[1:], lengths):
        if length <= 0:
            preceding += length
            continue
        dx, dy = second[0] - first[0], second[1] - first[1]
        fraction = max(0.0, min(1.0, ((point[0] - first[0]) * dx + (point[1] - first[1]) * dy) / (length * length)))
        projected = (first[0] + fraction * dx, first[1] + fraction * dy)
        candidate = (_distance(point, projected), (preceding + fraction * length) / total, projected)
        if best is None or candidate[0] < best[0]:
            best = candidate
        preceding += length
    assert best is not None
    return {"fraction": best[1], "point": best[2], "distance_m": best[0]}


def _clip_polyline_fraction(points: Sequence[tuple[float, float]], start: float, end: float) -> list[tuple[float, float]]:
    if not points:
        return []
    if end < start:
        return list(reversed(_clip_polyline_fraction(points, end, start)))
    lengths = [_distance(a, b) for a, b in zip(points, points[1:])]
    total = sum(lengths)
    if total <= 0:
        return [points[0], points[-1]]
    start_distance, end_distance = max(0.0, min(total, start * total)), max(0.0, min(total, end * total))
    values: list[tuple[float, float]] = [_polyline_fraction_point(points, start)]
    travelled = 0.0
    for point, next_point, length in zip(points, points[1:], lengths):
        segment_end = travelled + length
        if start_distance < segment_end - 1e-8 and end_distance > travelled + 1e-8:
            if travelled > start_distance + 1e-8:
                values.append(point)
            if segment_end >= end_distance - 1e-8:
                values.append(_polyline_fraction_point(points, end))
                break
            values.append(next_point)
        travelled = segment_end
    if _distance(values[-1], _polyline_fraction_point(points, end)) > 1e-6:
        values.append(_polyline_fraction_point(points, end))
    return _dedupe_points(values)


def _polyline_fraction_point(points: Sequence[tuple[float, float]], fraction: float) -> tuple[float, float]:
    if len(points) == 1:
        return points[0]
    lengths = [_distance(a, b) for a, b in zip(points, points[1:])]
    total = sum(lengths)
    target = max(0.0, min(total, fraction * total))
    travelled = 0.0
    for first, second, length in zip(points, points[1:], lengths):
        if travelled + length >= target - 1e-9 and length > 0:
            local = (target - travelled) / length
            return (first[0] + local * (second[0] - first[0]), first[1] + local * (second[1] - first[1]))
        travelled += length
    return points[-1]


def _parse_shape(value: str) -> list[tuple[float, float]]:
    result = []
    for token in value.split():
        if "," not in token:
            continue
        x, y = token.split(",", 1)
        result.append((float(x), float(y)))
    return result


def _shape(points: Sequence[tuple[float, float]]) -> str:
    return " ".join(f"{float(x):.3f},{float(y):.3f}" for x, y in _dedupe_points(points))


def _dedupe_points(points: Sequence[tuple[float, float]]) -> list[tuple[float, float]]:
    result: list[tuple[float, float]] = []
    for point in points:
        if not result or _distance(result[-1], point) > 1e-7:
            result.append(point)
    return result


def _params(element: ET.Element) -> dict[str, str]:
    return {
        str(item.attrib.get("key")): str(item.attrib.get("value", ""))
        for item in element.findall("param")
        if item.attrib.get("key")
    }


def _set_param(element: ET.Element, key: str, value: str) -> None:
    for item in element.findall("param"):
        if item.attrib.get("key") == key:
            item.set("value", value)
            return
    ET.SubElement(element, "param", {"key": key, "value": value})


def _corridor_id(official_link_id: str, direction: str) -> str:
    return "hh-sib-axis-" + hashlib.sha256((official_link_id + "\0" + direction).encode("utf-8")).hexdigest()[:16]


def _write_xml(path: Path, root_name: str, children: Sequence[ET.Element]) -> None:
    root = ET.Element(root_name)
    for child in children:
        root.append(child)
    ET.indent(root, space="  ")
    write_text_atomic(path, '<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(root, encoding="unicode") + "\n")


def _unique_elements(elements: Sequence[ET.Element]) -> list[ET.Element]:
    result: list[ET.Element] = []
    seen: set[str] = set()
    for item in elements:
        identifier = str(item.attrib.get("id", ""))
        if identifier and identifier in seen:
            continue
        if identifier:
            seen.add(identifier)
        result.append(item)
    return result


def _unique_connections(elements: Sequence[ET.Element]) -> list[ET.Element]:
    result: list[ET.Element] = []
    seen: set[tuple[str, str, str, str]] = set()
    for item in elements:
        key = (
            str(item.attrib.get("from", "")),
            str(item.attrib.get("to", "")),
            str(item.attrib.get("fromLane", "")),
            str(item.attrib.get("toLane", "")),
        )
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result


def _existing(value: str | Path, label: str) -> Path:
    path = Path(value).expanduser().resolve()
    if not path.is_file():
        raise OfficialSpliceMaterializationError(f"{label} does not exist: {path}")
    return path


def _load_json_like(value: str | Path | Mapping[str, Any], label: str) -> tuple[dict[str, Any], dict[str, Any]]:
    if isinstance(value, Mapping):
        payload = json.loads(json.dumps(dict(value), ensure_ascii=False))
        return payload, {"path": None, "sha256": _stable_digest(payload), "identity_method": "canonical_json_sha256"}
    path = Path(value).expanduser().resolve()
    try:
        raw = path.read_bytes()
        payload = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise OfficialSpliceMaterializationError(f"invalid {label}: {path}") from exc
    if not isinstance(payload, dict):
        raise OfficialSpliceMaterializationError(f"{label} root must be an object")
    return payload, {"path": str(path), "sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw), "identity_method": "file_bytes_sha256"}


def _verify_plan_axis_hash(plan: Mapping[str, Any], edge_path: Path) -> None:
    expected = str(plan.get("inputs", {}).get("edges", {}).get("sha256", ""))
    if not _SHA256_PATTERN.fullmatch(expected) or file_sha256(edge_path) != expected:
        raise OfficialSpliceMaterializationError("splice plan does not match the supplied HH-SIB edges file")


def _file_identity(path: Path) -> dict[str, Any]:
    return {"path": str(path.resolve()), "sha256": file_sha256(path), "bytes": path.stat().st_size}


def _stable_digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def _result_dict(value: object) -> dict[str, Any]:
    if hasattr(value, "to_dict"):
        return dict(value.to_dict())
    if isinstance(value, Mapping):
        return dict(value)
    return {"status": "fail", "error": f"unsupported command result {type(value).__name__}"}


def _identifier_key(value: str) -> tuple[int, Any]:
    return (0, int(value)) if value.isdigit() else (1, value)


def _rounded(value: float) -> float:
    return round(float(value), 6)


def _distance(first: Sequence[float], second: Sequence[float]) -> float:
    return math.hypot(float(first[0]) - float(second[0]), float(first[1]) - float(second[1]))


def _valid_point(value: Any) -> bool:
    return isinstance(value, Sequence) and not isinstance(value, (str, bytes)) and len(value) >= 2


__all__ = [
    "OFFICIAL_SPLICE_MATERIALIZATION_SCHEMA",
    "OfficialSpliceMaterializationError",
    "materialize_hamburg_official_splice_candidate",
]
