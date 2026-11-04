"""Boundary completeness: every passenger entry and exit, the entry-exit pairs the turn graph connects, and one SUMO run
that loads every boundary endpoint and requires every vehicle to arrive."""

from __future__ import annotations

import csv
import io
import math
import random
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Callable, Mapping
import xml.etree.ElementTree as ET

from .artifact_io import write_json_atomic, write_text_atomic
from .candidate_contracts import file_sha256
from .command_runner import run_command
from .routeability_audit import run_routeability_audit
from .sumo_commands import discover_binaries

_SKIPPED_FUNCTIONS = {"internal", "crossing", "walkingarea"}
_LIST_LIMIT = 200
_COLORS = {"cut_entry_without_cut_exit": "255,0,0", "cut_exit_without_cut_entry": "255,0,0", "interior_gap": "255,165,0",
           "dead_end_without_turnaround": "255,215,0", "boundary_load_incomplete": "200,0,200"}
CLAIM_BOUNDARY = (
    "Synthetic structural load, not demand: every loaded entry and exit receives trips, and the counts are not traffic "
    "volumes or OD. A pass does not prove turn, lane, signal or field correctness. The bbox cut creates artificial "
    "endpoints; an endpoint within the margin of the cut counts as cut. Static reachability uses the loaded passenger "
    "turns, turnarounds included.")


def _read_network(net_file: Path) -> dict[str, Any]:
    """The non-internal edges, junction positions, location and passenger turn links of one streamed pass."""
    from .local_network_conversion import streamed_loaded_turn_permissions
    from .osm_access import _permission_set

    edges: dict[str, tuple[str, str, str, list[tuple[str | None, str | None, float]]]] = {}
    nodes: dict[str, tuple[float, float]] = {}
    location: dict[str, str] = {}

    def visit(element: ET.Element) -> None:
        if element.tag == "edge":
            identity = element.get("id", "")
            if identity.startswith(":") or element.get("function") in _SKIPPED_FUNCTIONS:
                return
            edges[identity] = (element.get("from"), element.get("to"), element.get("type", ""),
                               [(lane.get("allow"), lane.get("disallow"), float(lane.get("length", "0")))
                                for lane in element.findall("lane")])
        elif element.tag == "junction" and element.get("type") != "internal":
            nodes[element.get("id")] = (float(element.get("x")), float(element.get("y")))
        elif element.tag == "location" and not location:
            location.update(element.attrib)

    permissions, version = streamed_loaded_turn_permissions(net_file, visit=visit)
    passenger: dict[tuple[str | None, str | None], bool] = {}
    lane_km: dict[str, float] = {}
    for identity, (_, _, _, lanes) in edges.items():
        lengths = []
        for allow, disallow, length in lanes:
            key = (allow, disallow)
            if key not in passenger:
                declared = {name: value for name, value in zip(("allow", "disallow"), key) if value is not None}
                passenger[key] = "passenger" in _permission_set(declared, network_version=version)
            if passenger[key]:
                lengths.append(length)
        if lengths:
            lane_km[identity] = sum(lengths) / 1000.0
    links = {(start, target) for (start, _, target, _), classes in permissions.items()
             if "passenger" in classes and start in lane_km and target in lane_km}
    return {"edges": edges, "nodes": nodes, "location": location, "lane_km": lane_km, "links": links}


def _boundary_area(boundary: str | None, location: Mapping[str, str], margin_m: float) -> tuple[Any, str, float]:
    """The cut polygon in network xy, shrunk by the margin, its source and its area."""
    import shapely
    from shapely.geometry import Polygon

    if boundary is None:
        x0, y0, x1, y1 = (float(value) for value in location["convBoundary"].split(","))
        ring, source = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)], "network_extent"
    else:
        from pyproj import CRS, Transformer
        from .osm_network import parse_bbox, parse_geo_boundary

        if len(boundary.split(",")) == 4:
            box = parse_bbox(boundary)
            corners, source = [(box.west, box.south), (box.east, box.south), (box.east, box.north), (box.west, box.north)], "bbox"
        else:
            values = [float(value) for value in parse_geo_boundary(boundary).split(",")]
            corners, source = list(zip(values[0::2], values[1::2])), "geo_boundary"
        projection = location.get("projParameter", "").strip()
        if not projection or projection == "!":
            raise ValueError("the network declares no geographic projection")
        offset_x, offset_y = (float(value) for value in location.get("netOffset", "0,0").split(","))
        transformer = Transformer.from_crs(CRS.from_epsg(4326), CRS.from_user_input(projection), always_xy=True)
        # UTM is not axis-aligned with longitude and latitude, so every side is densified before projection.
        points = [(a[0] + (b[0] - a[0]) * step / 32, a[1] + (b[1] - a[1]) * step / 32)
                  for a, b in zip(corners, corners[1:] + corners[:1]) for step in range(32)]
        xs, ys = transformer.transform([point[0] for point in points], [point[1] for point in points])
        ring = [(x + offset_x, y + offset_y) for x, y in zip(xs, ys)]
    if any(not math.isfinite(value) for point in ring for value in point):
        raise ValueError("the boundary does not project to finite network coordinates")
    polygon = Polygon(ring)
    if not polygon.is_valid or polygon.area <= 0:
        raise ValueError("the boundary is not a valid polygon")
    area = polygon.buffer(-margin_m)
    shapely.prepare(area)
    return area, source, polygon.area


def _classify_endpoints(network: Mapping[str, Any], area: Any) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Entries and exits with their class.

    A link is a turnaround when its target runs between the same two nodes in the opposite direction; it neither makes
    nor removes an entry or exit. ``dead_end`` is a cul-de-sac: every edge at the node, of any class, runs to one
    neighbour and a passenger edge runs both ways, so a turnaround can serve it. A one-way road that ends, or a road
    that continues only for other classes, is an ``interior_gap``.
    """
    import shapely

    edges, lane_km, nodes = network["edges"], network["lane_km"], network["nodes"]
    has_predecessor, has_successor, turnaround_in, turnaround_out = set(), set(), set(), set()
    for start, target in network["links"]:
        if edges[target][0] == edges[start][1] and edges[target][1] == edges[start][0]:
            turnaround_out.add(start)
            turnaround_in.add(target)
        else:
            has_successor.add(start)
            has_predecessor.add(target)
    passenger_neighbours, neighbours = defaultdict(set), defaultdict(set)
    outgoing, incoming = defaultdict(list), defaultdict(list)
    for identity, (start, end, _, _) in edges.items():
        neighbours[start].add(end)
        neighbours[end].add(start)
        if identity in lane_km:
            passenger_neighbours[start].add(end)
            passenger_neighbours[end].add(start)
            outgoing[start].append(identity)
            incoming[end].append(identity)
    pairs = {edges[identity][:2] for identity in lane_km}
    result = []
    for role, linked, turned, others in (("entry", has_predecessor, turnaround_in, incoming),
                                          ("exit", has_successor, turnaround_out, outgoing)):
        rows = []
        for identity in lane_km:
            if identity in linked:
                continue
            start, end, edge_type, _ = edges[identity]
            node, far = (start, end) if role == "entry" else (end, start)
            x, y = nodes[node]
            row = {"edge": identity, "role": role, "node": node, "x": x, "y": y, "edge_type": edge_type,
                   "has_turnaround": identity in turned, "reason": ""}
            if not shapely.contains_xy(area, x, y):
                row["class"] = "cut_boundary"
            elif passenger_neighbours[node] == neighbours[node] == {far} and (end, start) in pairs:
                row["class"] = "dead_end"
                row["reason"] = "covered_by_turnaround" if row["has_turnaround"] else "dead_end_without_turnaround"
            else:
                # The needed direction: edges leaving the node after an exit, entering it before an entry, without
                # the reverse edge whose link would be a turnaround.
                end_index = 0 if role == "entry" else 1
                candidates = [other for other in others[node] if other != identity and edges[other][end_index] != far]
                row["class"] = "interior_gap"
                row["reason"] = "no_passenger_connection" if candidates else "no_other_passenger_edge"
            rows.append(row)
        result.append(rows)
    return result[0], result[1]


def _loadable(row: Mapping[str, Any]) -> bool:
    return row["class"] in {"cut_boundary", "interior_gap"} or row["class"] == "dead_end" and not row["has_turnaround"]


def _mask(rows: list[dict[str, Any]], test: Callable[[Mapping[str, Any]], bool]) -> int:
    return sum(1 << bit for bit, row in enumerate(rows) if test(row))


def _reachability(network: Mapping[str, Any], entries: list[dict[str, Any]], exits: list[dict[str, Any]]) -> dict[str, Any]:
    """Entry-exit reachability on the condensation of the passenger turn graph, with exit and entry bitsets."""
    from graphlib import TopologicalSorter
    import numpy as np
    from scipy.sparse import csr_matrix
    from scipy.sparse.csgraph import connected_components

    lane_km = network["lane_km"]
    ids = list(lane_km)
    position = {identity: index for index, identity in enumerate(ids)}
    links = [(position[start], position[target]) for start, target in network["links"]]
    rows, columns = zip(*links) if links else ((), ())
    graph = csr_matrix((np.ones(len(links), dtype=bool), (rows, columns)), shape=(len(ids), len(ids)))
    count, labels = connected_components(graph, directed=True, connection="strong")
    successors = {component: set() for component in range(count)}
    predecessors = {component: set() for component in range(count)}
    for start, target in links:
        a, b = int(labels[start]), int(labels[target])
        if a != b:
            successors[a].add(b)
            predecessors[b].add(a)
    component = {identity: int(labels[index]) for identity, index in position.items()}
    exit_bits, entry_bits = [0] * count, [0] * count
    for bit, row in enumerate(exits):
        exit_bits[component[row["edge"]]] |= 1 << bit
    for bit, row in enumerate(entries):
        entry_bits[component[row["edge"]]] |= 1 << bit
    # As a TopologicalSorter graph, successors lists what must come first: every successor before its component.
    for node in TopologicalSorter(successors).static_order():
        for target in successors[node]:
            exit_bits[node] |= exit_bits[target]
    for node in TopologicalSorter(predecessors).static_order():
        for source in predecessors[node]:
            entry_bits[node] |= entry_bits[source]
    # A pair whose exit ends where the entry starts sends the vehicle back out where it came in.
    exits_at, entries_at = defaultdict(int), defaultdict(int)
    for bit, row in enumerate(exits):
        exits_at[row["node"]] |= 1 << bit
    for bit, row in enumerate(entries):
        entries_at[row["node"]] |= 1 << bit
    entry_reach = [exit_bits[component[row["edge"]]] & ~exits_at[row["node"]] for row in entries]
    exit_reach = [entry_bits[component[row["edge"]]] & ~entries_at[row["node"]] for row in exits]
    sizes = np.bincount(labels, minlength=count)
    lengths = np.bincount(labels, weights=np.array([lane_km[identity] for identity in ids]), minlength=count)
    largest = int(np.argmax(sizes))
    return {"component": component, "successors": successors, "predecessors": predecessors, "count": count,
            "entry_reach": entry_reach, "exit_reach": exit_reach, "exits_at": exits_at,
            "largest": {"edge_count": int(sizes[largest]), "edge_share": round(float(sizes[largest]) / len(ids), 6),
                        "lane_km": round(float(lengths[largest]), 3),
                        "lane_km_share": round(float(lengths[largest]) / float(lengths.sum()), 6) if lengths.sum() else 0.0}}


def _closure(start: set[int], graph: Mapping[int, set[int]]) -> set[int]:
    seen, pending = set(start), list(start)
    while pending:
        for other in graph[pending.pop()]:
            if other not in seen:
                seen.add(other)
                pending.append(other)
    return seen


def _bits(mask: int, rows: list[dict[str, Any]]) -> list[str]:
    result = []
    while mask:
        bit = mask & -mask
        result.append(rows[bit.bit_length() - 1]["edge"])
        mask ^= bit
    return result


def _assign_trips(entries, exits, entry_reach, exit_reach, loaded_entry_mask, loaded_exit_mask, *,
                  vehicles_per_entry: int, min_vehicles_per_exit: int, generator: random.Random) -> list[tuple[str, str]]:
    """Give every loaded exit its minimum from the least-used reaching entries, then top every entry up."""
    entry_trips = {row["edge"]: 0 for bit, row in enumerate(entries) if loaded_entry_mask >> bit & 1}
    exit_trips = {row["edge"]: 0 for bit, row in enumerate(exits) if loaded_exit_mask >> bit & 1}

    def pick(candidates: list[str], counts: Mapping[str, int]) -> str:
        fewest = min(counts[candidate] for candidate in candidates)
        return generator.choice([candidate for candidate in candidates if counts[candidate] == fewest])

    trips = []
    for bit, row in enumerate(exits):
        if loaded_exit_mask >> bit & 1:
            sources = _bits(exit_reach[bit] & loaded_entry_mask, entries)
            for _ in range(min_vehicles_per_exit):
                source = pick(sources, entry_trips)
                trips.append((source, row["edge"]))
                entry_trips[source] += 1
                exit_trips[row["edge"]] += 1
    for bit, row in enumerate(entries):
        if loaded_entry_mask >> bit & 1 and entry_trips[row["edge"]] < vehicles_per_entry:
            targets = _bits(entry_reach[bit] & loaded_exit_mask, exits)
            while entry_trips[row["edge"]] < vehicles_per_entry:
                target = pick(targets, exit_trips)
                trips.append((row["edge"], target))
                entry_trips[row["edge"]] += 1
                exit_trips[target] += 1
    return trips


def _write_trips(path: Path, trips: list[tuple[str, str]], *, demand_seconds: int, generator: random.Random) -> list[dict[str, Any]]:
    """Spread each entry's trips evenly over the demand window from a seeded offset, ordered by departure."""
    by_entry: dict[str, list[str]] = defaultdict(list)
    for entry, target in trips:
        by_entry[entry].append(target)
    timed = []
    for entry, targets in by_entry.items():
        interval = demand_seconds / len(targets)
        offset = generator.random() * interval
        timed.extend((math.floor(offset + index * interval), entry, target) for index, target in enumerate(targets))
    timed.sort(key=lambda row: row[0])
    root = ET.Element("routes")
    rows = []
    for number, (depart, entry, target) in enumerate(timed):
        rows.append({"id": f"b{number}", "depart": depart, "entry": entry, "exit": target})
        ET.SubElement(root, "trip", {"id": f"b{number}", "depart": str(depart), "from": entry, "to": target,
                                      "departLane": "best"})
    ET.indent(root)
    write_text_atomic(path, ET.tostring(root, encoding="unicode", xml_declaration=True))
    return rows


def _xml_rows(path: Any, tag: str) -> list[ET.Element]:
    try:
        return ET.parse(path).getroot().findall(tag) if path and Path(path).is_file() else []
    except (OSError, ET.ParseError):
        return []


def _load_outcome(routeability: Mapping[str, Any], trips: list[dict[str, Any]]) -> dict[str, Any]:
    """Per-vehicle completion of the final attempt from its tripinfo, unfinished vehicle routes and last positions."""
    final = routeability.get("final_attempt") or {}
    arrived, departed, last_edge = set(), set(), {}
    for row in _xml_rows(final.get("tripinfo_file"), "tripinfo"):
        departed.add(row.get("id"))
        if row.get("vaporized", "").strip().lower() in {"", "false", "0"}:
            arrived.add(row.get("id"))
    for row in _xml_rows(final.get("vehicle_route_file"), "vehicle"):
        route = row.find("route")
        try:
            if float(row.get("depart", "-1")) >= 0:
                departed.add(row.get("id"))
            edges = route.get("edges", "").split() if route is not None else []
            times = [float(value) for value in route.get("exitTimes", "").split()] if route is not None else []
        except ValueError:
            continue
        open_edges = [edge for edge, time in zip(edges, times) if time < 0]
        if open_edges or edges:
            last_edge[row.get("id")] = open_edges[0] if open_edges else edges[-1]
    steps = _xml_rows((final.get("diagnostic_outputs") or {}).get("last_step_positions"), "timestep")
    for row in steps[-1].findall("vehicle") if steps else []:
        departed.add(row.get("id"))
        last_edge[row.get("id")] = row.get("lane", "").rsplit("_", 1)[0]
    failures = {row.get("vehicle_id"): row.get("reason") for row in (final.get("vehicle_routes") or {}).get("failures", [])}
    complete = arrived - failures.keys()
    vehicles = []
    for trip in trips:
        identity = trip["id"]
        if identity in complete:
            continue
        state = ("not_run" if not final else "not_inserted" if identity not in departed
                 else "unfinished" if identity not in arrived else failures[identity])
        vehicles.append({"vehicle_id": identity, "entry": trip["entry"], "exit": trip["exit"], "depart": trip["depart"],
                         "state": state, "last_edge": last_edge.get(identity)})
    return {"complete": complete, "departed": departed, "vehicles": vehicles}


def _write_endpoints_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(("edge", "role", "class", "reason", "reachable_count", "loaded", "planned", "arrived"))
    for row in rows:
        writer.writerow((row["edge"], row["role"], row["class"], row["reason"], row["reachable_count"],
                         "true" if row["loaded"] else "false", row.get("planned", ""), row.get("arrived", "")))
    write_text_atomic(path, buffer.getvalue())


def _write_overlay(path: Path, findings: list[dict[str, Any]], *, source_sha256: str) -> None:
    """Display-only review markers at the endpoint node of every finding."""
    root = ET.Element("additional")
    for index, finding in enumerate(findings):
        poi = ET.SubElement(root, "poi", {
            "id": f"astra_boundary_{index}", "type": f"astra.review.boundary.{finding['reason']}",
            "color": _COLORS.get(finding["reason"], "255,165,0"), "layer": "1002",
            "x": f"{finding['x']:.3f}", "y": f"{finding['y']:.3f}",
            "name": f"{finding['reason']}: {finding['role']} {finding['edge']}"})
        for key, value in (("display_only", "true"), ("edge", finding["edge"]), ("role", finding["role"]),
                           ("class", finding["class"]), ("reason", finding["reason"]), ("detail", finding.get("detail", "")),
                           ("source_sha256", source_sha256), ("operational_change", "none")):
            ET.SubElement(poi, "param", {"key": key, "value": str(value)})
    ET.indent(root, space="    ")
    write_text_atomic(path, ET.tostring(root, encoding="unicode", xml_declaration=True))


def _finding(row: Mapping[str, Any], reason: str, detail: str = "") -> dict[str, Any]:
    return {"status": "review_required", "reason": reason, "edge": row["edge"], "role": row["role"], "class": row["class"],
            "node": row["node"], "x": row["x"], "y": row["y"], "edge_type": row["edge_type"], "detail": detail}


def run_boundary_completeness_audit(*, net_file: Path, output_dir: Path, prefix: str = "boundary_completeness",
                                    boundary: str | None = None, margin_m: float = 50.0, vehicles_per_entry: int = 4,
                                    min_vehicles_per_exit: int = 2, demand_seconds: int = 1800, seed: int = 42,
                                    run_simulation: bool = True, timeout_seconds: float = 900.0,
                                    binaries: Mapping[str, str | None] | None = None,
                                    command_runner: Callable[..., Any] = run_command) -> dict[str, Any]:
    """Classify the passenger boundary of ``net_file``, measure entry-exit reachability and load every boundary endpoint.

    ``boundary`` is the WGS84 cut: ``W,S,E,N`` or ``lon,lat`` pairs; without it the network's convBoundary is the cut.
    Every loaded entry gets ``vehicles_per_entry`` trips and every loaded exit at least ``min_vehicles_per_exit``. The
    SUMO run is ``run_routeability_audit`` on the frozen trips: teleporting is effectively off, and collisions,
    teleports and unfinished vehicles fail it. The input network is never written.
    """
    if not prefix or Path(prefix).name != prefix or prefix in {".", ".."}:
        raise ValueError("prefix must be a single file-name component")
    if vehicles_per_entry < 1 or min_vehicles_per_exit < 1 or demand_seconds < 1 or seed < 0:
        raise ValueError("vehicles_per_entry, min_vehicles_per_exit and demand_seconds must be positive and seed nonnegative")
    if not math.isfinite(margin_m) or margin_m < 0:
        raise ValueError("margin_m must be finite and nonnegative")
    net_file, output_dir = Path(net_file).resolve(), Path(output_dir).resolve()
    report_file, endpoints_file = output_dir / f"{prefix}.json", output_dir / f"{prefix}.endpoints.csv"
    overlay_file, trip_file, load_dir = output_dir / f"{prefix}.review.add.xml", output_dir / f"{prefix}.trips.xml", output_dir / "load"
    report: dict[str, Any] = {
        "schema": "astra.boundary_completeness/v1", "status": "blocked", "reasons": [], "net_file": str(net_file),
        "net_sha256": None, "output_dir": str(output_dir), "report_file": str(report_file),
        "parameters": {"boundary": boundary, "margin_m": margin_m, "vehicles_per_entry": vehicles_per_entry,
                       "min_vehicles_per_exit": min_vehicles_per_exit, "demand_seconds": demand_seconds, "seed": seed,
                       "run_simulation": run_simulation, "timeout_seconds": timeout_seconds},
        "claim_boundary": CLAIM_BOUNDARY, "warnings": []}

    def finish(status: str, reasons: list[str], error: str | None = None) -> dict[str, Any]:
        report.update(status=status, reasons=list(dict.fromkeys(reasons)))
        if error:
            report["error"] = error
        output_dir.mkdir(parents=True, exist_ok=True)
        write_json_atomic(report_file, report)
        return report

    if any(path == net_file for path in (report_file, endpoints_file, overlay_file, trip_file)):
        report["reasons"] = ["output_overlaps_input"]
        return report
    if not net_file.is_file():
        return finish("blocked", ["network_missing"])
    selected = dict(binaries or discover_binaries())
    if run_simulation and not all(selected.get(name) for name in ("duarouter", "sumo")):
        return finish("blocked", ["sumo_binaries_missing"])
    net_sha256 = report["net_sha256"] = file_sha256(net_file)
    try:
        network = _read_network(net_file)
    except (OSError, ET.ParseError, ValueError, TypeError, KeyError) as exc:
        return finish("blocked", ["network_unreadable"], f"{type(exc).__name__}: {exc}")
    if not network["lane_km"]:
        return finish("blocked", ["no_passenger_edge"])
    try:
        area, boundary_source, area_m2 = _boundary_area(boundary, network["location"], margin_m)
    except (ValueError, TypeError, KeyError) as exc:
        return finish("blocked", ["boundary_unusable"], f"{type(exc).__name__}: {exc}")
    try:
        entries, exits = _classify_endpoints(network, area)
    except KeyError as exc:
        return finish("blocked", ["network_unreadable"], f"edge endpoint without a junction: {exc}")
    report["boundary"] = {"source": boundary_source, "value": boundary, "margin_m": margin_m, "area_km2": round(area_m2 / 1e6, 4)}

    # S2: static reachability over the full passenger turn graph.
    reach = _reachability(network, entries, exits)
    entry_reach, exit_reach = reach["entry_reach"], reach["exit_reach"]
    cut_exits, all_exits = _mask(exits, lambda row: row["class"] == "cut_boundary"), (1 << len(exits)) - 1
    cut_entries = _mask(entries, lambda row: row["class"] == "cut_boundary")
    for rows, masks in ((entries, entry_reach), (exits, exit_reach)):
        for row, mask in zip(rows, masks):
            row["reachable_count"] = mask.bit_count()

    def pairs(entry_mask: int, exit_mask: int) -> dict[str, int]:
        selected_rows = [bit for bit in range(len(entries)) if entry_mask >> bit & 1]
        return {"reachable": sum((entry_reach[bit] & exit_mask).bit_count() for bit in selected_rows),
                "total": sum((exit_mask & ~reach["exits_at"][entries[bit]["node"]]).bit_count() for bit in selected_rows)}

    loadable_entries, loadable_exits = _mask(entries, _loadable), _mask(exits, _loadable)
    loaded_entries = sum(1 << bit for bit, mask in enumerate(entry_reach) if loadable_entries >> bit & 1 and mask & loadable_exits)
    loaded_exits = sum(1 << bit for bit, mask in enumerate(exit_reach) if loadable_exits >> bit & 1 and mask & loadable_entries)
    for rows, mask in ((entries, loaded_entries), (exits, loaded_exits)):
        for bit, row in enumerate(rows):
            row["loaded"] = bool(mask >> bit & 1)
    components = reach["component"]
    forward = _closure({components[row["edge"]] for row in entries if row["loaded"]}, reach["successors"])
    backward = _closure({components[row["edge"]] for row in exits if row["loaded"]}, reach["predecessors"])
    off_paths = [identity for identity in network["lane_km"] if not (components[identity] in forward and components[identity] in backward)]

    def without(rows: list[dict[str, Any]]) -> dict[str, Any]:
        by_class: dict[str, list[str]] = defaultdict(list)
        for row in rows:
            if not row["reachable_count"]:
                by_class[row["class"]].append(row["edge"])
        return {name: {"count": len(edges), "edges": edges[:_LIST_LIMIT]} for name, edges in sorted(by_class.items())}

    findings = []
    for bit, row in enumerate(entries):
        if row["class"] == "cut_boundary" and not entry_reach[bit] & cut_exits:
            findings.append(_finding(row, "cut_entry_without_cut_exit"))
    for bit, row in enumerate(exits):
        if row["class"] == "cut_boundary" and not exit_reach[bit] & cut_entries:
            findings.append(_finding(row, "cut_exit_without_cut_entry"))
    for row in entries + exits:
        if row["class"] == "interior_gap":
            findings.append(_finding(row, "interior_gap", row["reason"]))
        elif row["class"] == "dead_end" and not row["has_turnaround"]:
            findings.append(_finding(row, "dead_end_without_turnaround"))
    edges = network["edges"]
    report["network"] = {"passenger_edge_count": len(network["lane_km"]), "passenger_lane_km": round(sum(network["lane_km"].values()), 3),
                         "passenger_link_count": len(network["links"]),
                         "turnaround_link_count": sum(edges[target][0] == edges[start][1] and edges[target][1] == edges[start][0]
                                                      for start, target in network["links"]),
                         "strong_component_count": reach["count"], "largest_strong_component": reach["largest"]}
    report["endpoints"] = {
        "entry_count": len(entries), "exit_count": len(exits),
        "by_class": {role: dict(sorted(Counter(row["class"] for row in rows).items())) for role, rows in (("entry", entries), ("exit", exits))},
        "dead_ends_with_turnaround": {role: sum(row["class"] == "dead_end" and row["has_turnaround"] for row in rows)
                                      for role, rows in (("entry", entries), ("exit", exits))},
        "interior_gap_reasons": {role: dict(sorted(Counter(row["reason"] for row in rows if row["class"] == "interior_gap").items()))
                                 for role, rows in (("entry", entries), ("exit", exits))},
        "loaded": {"entry": loaded_entries.bit_count(), "exit": loaded_exits.bit_count()}}
    report["reachability"] = {
        "method": "Loaded passenger turn graph from endpoint and complete internal-lane permissions, turnarounds included; "
                  "pairs whose exit ends where the entry starts are excluded",
        "pairs": {"cut_to_cut": pairs(cut_entries, cut_exits), "cut_to_any": pairs(cut_entries, all_exits),
                  "any_to_any": pairs((1 << len(entries)) - 1, all_exits)},
        "entries_without_reachable_exit": without(entries), "exits_without_reaching_entry": without(exits),
        "edges_off_boundary_paths": {"count": len(off_paths), "lane_km": round(sum(network["lane_km"][identity] for identity in off_paths), 3),
                                     "edges": off_paths[:_LIST_LIMIT]}}

    # D2: load every boundary endpoint and require every vehicle to arrive.
    load: dict[str, Any] = {"loaded_entry_count": loaded_entries.bit_count(), "loaded_exit_count": loaded_exits.bit_count()}
    load_reasons: list[str] = []
    blocked = False
    if not run_simulation:
        load.update(status="not_applicable", reason="static_only")
    elif not loaded_entries:
        load.update(status="not_applicable", reason="no_loaded_endpoint_pair")
        load_reasons.append("no_loaded_endpoint_pair")
    else:
        generator = random.Random(seed)
        assigned = _assign_trips(entries, exits, entry_reach, exit_reach, loaded_entries, loaded_exits,
                                 vehicles_per_entry=vehicles_per_entry, min_vehicles_per_exit=min_vehicles_per_exit, generator=generator)
        trips = _write_trips(trip_file, assigned, demand_seconds=demand_seconds, generator=generator)
        trip_sha256 = file_sha256(trip_file)
        initial_end, max_end = demand_seconds + 900, demand_seconds + 1800
        routeability = run_routeability_audit(
            net_file=net_file, output_dir=load_dir, prefix=prefix, vehicle_count=len(trips), seed=seed,
            initial_end=initial_end, max_end=max_end, timeout_seconds=timeout_seconds, binaries=selected,
            command_runner=command_runner, frozen_trip_file=trip_file, expected_frozen_trip_sha256=trip_sha256,
            diagnostic_outputs=True)
        outcome = _load_outcome(routeability, trips)
        final = routeability.get("final_attempt") or {}
        summary = (final.get("inspection") or {}).get("summary") or {}
        complete = outcome["complete"]
        passed = routeability.get("status") == "pass" and len(complete) == len(trips)
        ran = bool(final) and (final.get("command") or {}).get("status") == "pass"
        blocked = routeability.get("status") == "blocked"
        for rows, key in ((entries, "entry"), (exits, "exit")):
            planned, inserted, arrived = Counter(), Counter(), Counter()
            for trip in trips:
                planned[trip[key]] += 1
                inserted[trip[key]] += trip["id"] in outcome["departed"]
                arrived[trip[key]] += trip["id"] in complete
            for row in rows:
                if row["loaded"]:
                    row.update(planned=planned[row["edge"]], arrived=arrived[row["edge"]])
                    if key == "entry":
                        row["inserted"] = inserted[row["edge"]]
                    if ran and row["arrived"] < row["planned"]:
                        findings.append(_finding(row, "boundary_load_incomplete", f"{row['arrived']}/{row['planned']} arrived"))
        load.update(
            status="blocked" if blocked else "pass" if passed else "review_required",
            trip_file=str(trip_file), trip_sha256=trip_sha256, vehicle_count=len(trips), initial_end=initial_end, max_end=max_end,
            inserted_count=len(outcome["departed"] & {trip["id"] for trip in trips}), arrived_count=len(complete),
            routeability={"status": routeability.get("status"), "routeability_status": routeability.get("routeability_status"),
                          "report_file": routeability.get("report_file"), "final_end": final.get("end"),
                          "summary": {key: summary.get(key) for key in ("loaded", "inserted", "arrived", "running", "waiting",
                                                                        "teleports", "collisions", "discarded")},
                          "warnings": list(routeability.get("warnings", []))[:20]},
            entries=[{key: row[key] for key in ("edge", "class", "planned", "inserted", "arrived")} for row in entries if row["loaded"]],
            exits=[{key: row[key] for key in ("edge", "class", "planned", "arrived")} for row in exits if row["loaded"]],
            unfinished_count=len(outcome["vehicles"]), unfinished_vehicles=outcome["vehicles"][:_LIST_LIMIT])
        if not passed and not blocked:
            load_reasons.append("boundary_load_incomplete" if ran else "boundary_load_run_failed")
            report["warnings"].append(f"boundary load: {len(complete)} of {len(trips)} vehicles arrived")
    report["load"] = load
    report["findings"] = findings
    report["finding_count"] = len(findings)
    report["findings_by_reason"] = dict(sorted(Counter(finding["reason"] for finding in findings).items()))
    report["endpoints_file"], report["review_overlay_file"] = str(endpoints_file), str(overlay_file)
    output_dir.mkdir(parents=True, exist_ok=True)
    _write_endpoints_csv(endpoints_file, entries + exits)
    _write_overlay(overlay_file, findings, source_sha256=net_sha256)
    report["net_unchanged"] = file_sha256(net_file) == net_sha256
    if not report["net_unchanged"]:
        return finish("blocked", ["input_network_changed"])
    if blocked:
        return finish("blocked", ["sumo_binaries_missing"])
    reasons = sorted({finding["reason"] for finding in findings}) + load_reasons
    return finish("review_required" if reasons else "pass", reasons)
