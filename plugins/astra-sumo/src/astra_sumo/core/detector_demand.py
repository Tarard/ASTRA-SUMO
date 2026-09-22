from __future__ import annotations

import csv
import math
import re
import xml.etree.ElementTree as ET
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


@dataclass(frozen=True)
class EdgeInfo:
    edge_id: str
    from_node: str
    to_node: str
    allows_passenger: bool
    length: float


@dataclass(frozen=True)
class LaneInfo:
    lane_id: str
    edge_id: str
    length: float


@dataclass(frozen=True)
class CandidateRoute:
    route_id: str
    source_edge: str
    sink_edge: str
    edges: tuple[str, ...]


@dataclass(frozen=True)
class Detector:
    detector_id: str
    source_system: str
    direction: str
    edge_id: str
    lane_id: str
    lane_position: float
    period: str
    mapping_confidence: str
    mapping_status: str


@dataclass(frozen=True)
class EdgeCount:
    edge_id: str
    entered: int
    detector_ids: tuple[str, ...]
    lane_ids: tuple[str, ...]
    begin: float
    end: float


def read_csv_rows(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def write_csv(path: Path, rows: list[dict[str, object]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def safe_id(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "_", value.strip())
    return cleaned.strip("_") or "unnamed"


def _register_safe_id(raw_value: str, raw_by_safe_id: dict[str, str], *, context: str) -> str:
    raw_id = raw_value.strip()
    if not raw_id:
        raise ValueError(f"{context} id must not be blank")
    sanitized_id = safe_id(raw_id)
    previous_raw_id = raw_by_safe_id.get(sanitized_id)
    if previous_raw_id is not None and previous_raw_id != raw_id:
        raise ValueError(
            f"{context} ids collide after sanitization: {previous_raw_id!r} and {raw_id!r} both become "
            f"{sanitized_id!r}"
        )
    raw_by_safe_id[sanitized_id] = raw_id
    return sanitized_id


def _strict_float(value: str | float | int | None, *, field_name: str) -> float:
    if value in (None, ""):
        raise ValueError(f"{field_name} is required")
    try:
        numeric = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be numeric: {value!r}") from exc
    if not math.isfinite(numeric):
        raise ValueError(f"{field_name} must be finite: {value!r}")
    return numeric


def _strict_nonnegative_int(value: str | float | int | None, *, field_name: str) -> int:
    numeric = _strict_float(value, field_name=field_name)
    if not numeric.is_integer() or numeric < 0:
        raise ValueError(f"{field_name} must be a non-negative integer: {value!r}")
    return int(numeric)


def _row_value(row: dict[str, str], *names: str, default: str = "") -> str:
    for name in names:
        value = row.get(name)
        if value not in (None, ""):
            return str(value)
    return default


def _float_value(value: str | float | int | None, default: float = 0.0) -> float:
    if value in (None, ""):
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _int_value(value: str | float | int | None, default: int = 0) -> int:
    if value in (None, ""):
        return default
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _time_key(value: str | float | int | None) -> str:
    if value in (None, ""):
        return ""
    numeric = _float_value(value)
    return f"{numeric:g}"


def lane_allows_passenger(lane: ET.Element) -> bool:
    allow = lane.attrib.get("allow")
    disallow = lane.attrib.get("disallow", "")
    if allow:
        classes = set(allow.split())
        return bool(classes & {"passenger", "private"})
    return "passenger" not in disallow.split()


def connection_allows_passenger(connection: ET.Element) -> bool:
    allow = connection.attrib.get("allow")
    disallow = connection.attrib.get("disallow", "")
    if allow:
        classes = set(allow.split())
        return bool(classes & {"passenger", "private"})
    return "passenger" not in disallow.split()


def read_net(path: Path) -> tuple[dict[str, EdgeInfo], dict[str, set[str]]]:
    root = ET.parse(path).getroot()
    edges: dict[str, EdgeInfo] = {}
    connections: dict[str, set[str]] = {}

    for edge in root.findall("edge"):
        edge_id = edge.attrib.get("id", "")
        if not edge_id or edge.attrib.get("function") or edge_id.startswith(":"):
            continue
        passenger_lanes = [lane for lane in edge.findall("lane") if lane_allows_passenger(lane)]
        length = max((_float_value(lane.attrib.get("length")) for lane in passenger_lanes), default=0.0)
        edges[edge_id] = EdgeInfo(
            edge_id=edge_id,
            from_node=edge.attrib.get("from", ""),
            to_node=edge.attrib.get("to", ""),
            allows_passenger=bool(passenger_lanes),
            length=length,
        )

    for connection in root.findall("connection"):
        from_edge = connection.attrib.get("from", "")
        to_edge = connection.attrib.get("to", "")
        if (
            from_edge in edges
            and to_edge in edges
            and edges[from_edge].allows_passenger
            and edges[to_edge].allows_passenger
            and connection_allows_passenger(connection)
        ):
            connections.setdefault(from_edge, set()).add(to_edge)

    return edges, connections


def read_net_lanes(path: Path) -> dict[str, LaneInfo]:
    """Read non-internal lane identities and lengths for strict detector placement checks."""

    root = ET.parse(path).getroot()
    lanes: dict[str, LaneInfo] = {}
    for edge in root.findall("edge"):
        edge_id = edge.attrib.get("id", "").strip()
        if not edge_id or edge.attrib.get("function") or edge_id.startswith(":"):
            continue
        for lane in edge.findall("lane"):
            lane_id = lane.attrib.get("id", "").strip()
            if not lane_id:
                raise ValueError(f"edge {edge_id!r} contains a lane without an id")
            length = _strict_float(lane.attrib.get("length"), field_name=f"lane {lane_id!r} length")
            if length <= 0:
                raise ValueError(f"lane {lane_id!r} length must be positive")
            if lane_id in lanes:
                raise ValueError(f"duplicate lane id in network: {lane_id!r}")
            lanes[lane_id] = LaneInfo(lane_id=lane_id, edge_id=edge_id, length=length)
    return lanes


def incoming_outgoing(connections: dict[str, set[str]]) -> tuple[dict[str, set[str]], dict[str, set[str]]]:
    incoming: dict[str, set[str]] = {}
    outgoing: dict[str, set[str]] = {}
    for from_edge, to_edges in connections.items():
        outgoing.setdefault(from_edge, set()).update(to_edges)
        for to_edge in to_edges:
            incoming.setdefault(to_edge, set()).add(from_edge)
    return incoming, outgoing


def boundary_edges(edges: dict[str, EdgeInfo], connections: dict[str, set[str]]) -> tuple[list[str], list[str]]:
    incoming, outgoing = incoming_outgoing(connections)
    passenger_edges = [edge_id for edge_id, edge in edges.items() if edge.allows_passenger]
    sources = sorted(edge_id for edge_id in passenger_edges if not incoming.get(edge_id) and outgoing.get(edge_id))
    sinks = sorted(edge_id for edge_id in passenger_edges if incoming.get(edge_id) and not outgoing.get(edge_id))
    return sources, sinks


def reverse_connections(connections: dict[str, set[str]]) -> dict[str, set[str]]:
    reversed_graph: dict[str, set[str]] = {}
    for from_edge, to_edges in connections.items():
        for to_edge in to_edges:
            reversed_graph.setdefault(to_edge, set()).add(from_edge)
    return reversed_graph


def shortest_route_to_any(
    adjacency: dict[str, set[str]],
    start_edge: str,
    target_edges: set[str],
    max_hops: int,
) -> list[str]:
    queue = deque([(start_edge, [start_edge])])
    seen = {start_edge}
    while queue:
        edge_id, route = queue.popleft()
        if edge_id in target_edges:
            return route
        if len(route) >= max_hops:
            continue
        for next_edge in sorted(adjacency.get(edge_id, set())):
            if next_edge in seen:
                continue
            seen.add(next_edge)
            queue.append((next_edge, [*route, next_edge]))
    return []


def shortest_routes_to_targets(
    adjacency: dict[str, set[str]],
    start_edge: str,
    target_edges: set[str],
    max_hops: int,
) -> dict[str, list[str]]:
    """Return one deterministic shortest path to every reachable target.

    A single nearest boundary path is not enough for detector-constrained demand: at a junction it
    can force all vehicles through one measured downstream edge and make otherwise valid turning
    flows infeasible.  This bounded breadth-first search exposes the reachable boundary choices
    without enumerating arbitrary cyclic paths.
    """

    queue = deque([(start_edge, [start_edge])])
    seen = {start_edge}
    paths: dict[str, list[str]] = {}
    while queue:
        edge_id, route = queue.popleft()
        if edge_id in target_edges:
            paths[edge_id] = route
            if len(paths) == len(target_edges):
                break
        if len(route) >= max_hops:
            continue
        for next_edge in sorted(adjacency.get(edge_id, set())):
            if next_edge in seen:
                continue
            seen.add(next_edge)
            queue.append((next_edge, [*route, next_edge]))
    return paths


def shortest_route(
    connections: dict[str, set[str]],
    source_edge: str,
    sink_edge: str,
    max_hops: int,
) -> list[str]:
    path = shortest_route_to_any(connections, source_edge, {sink_edge}, max_hops=max_hops)
    return path if path and path[-1] == sink_edge else []


def read_detector_mapping(path: Path) -> list[Detector]:
    detectors: list[Detector] = []
    raw_by_safe_id: dict[str, str] = {}
    for row in read_csv_rows(path):
        status = _row_value(row, "mapping_status", "status", default="active").lower()
        edge_id = _row_value(row, "sumo_edge", "edge_id", "edge", default="")
        raw_detector_id = _row_value(row, "detector_id", "s_idx", "id", default="")
        if not raw_detector_id.strip() or not edge_id:
            continue
        detector_id = _register_safe_id(raw_detector_id, raw_by_safe_id, context="detector")
        detectors.append(
            Detector(
                detector_id=detector_id,
                source_system=_row_value(row, "source_system", default=""),
                direction=_row_value(row, "real_direction", "direction", default=""),
                edge_id=edge_id,
                lane_id=_row_value(row, "sumo_lane", "lane_id", "lane", default=""),
                lane_position=_float_value(_row_value(row, "lane_position", "lane_pos", "pos", default="0")),
                period=_row_value(row, "period", default=""),
                mapping_confidence=_row_value(row, "mapping_confidence", "confidence", default=""),
                mapping_status=status or "active",
            )
        )
    return detectors


def active_detectors(detectors: Iterable[Detector]) -> list[Detector]:
    return [detector for detector in detectors if detector.mapping_status == "active"]


def validate_detector_lane_positions(
    detectors: Iterable[Detector],
    lanes: dict[str, LaneInfo],
) -> list[Detector]:
    """Fail closed when an E1 detector is not placed on its declared network lane and edge."""

    validated = list(detectors)
    raw_by_safe_id: dict[str, str] = {}
    seen_detector_ids: set[str] = set()
    for detector in validated:
        detector_id = _register_safe_id(detector.detector_id, raw_by_safe_id, context="detector")
        if detector_id in seen_detector_ids:
            raise ValueError(f"duplicate detector id: {detector_id!r}")
        seen_detector_ids.add(detector_id)

        lane_id = detector.lane_id.strip()
        if not lane_id:
            raise ValueError(f"detector {detector_id!r} has no lane id")
        lane = lanes.get(lane_id)
        if lane is None:
            raise ValueError(f"detector {detector_id!r} references unknown lane {lane_id!r}")
        if detector.edge_id != lane.edge_id:
            raise ValueError(
                f"detector {detector_id!r} declares edge {detector.edge_id!r}, but lane {lane_id!r} belongs to "
                f"edge {lane.edge_id!r}"
            )
        position = detector.lane_position
        if not math.isfinite(position):
            raise ValueError(f"detector {detector_id!r} lane position must be finite")
        if position < 0 or position > lane.length:
            raise ValueError(
                f"detector {detector_id!r} lane position {position:g} is outside [0, {lane.length:g}] "
                f"for lane {lane_id!r}"
            )
    return validated


def write_e1_additional(
    path: Path,
    detectors: Iterable[Detector],
    *,
    lanes: dict[str, LaneInfo],
    period: float,
    output_file: str | Path,
) -> dict[str, str]:
    """Write strict E1 definitions aligned to one count bin.

    All detectors use ``period`` so their output intervals align with the real-data bin. The shared
    ``output_file`` receives SUMO E1 interval records. Downstream comparison should use ``nVehContrib``:
    it counts vehicles that completely passed the loop, unlike ``nVehEntered`` which also includes
    vehicles that merely touched the detector before an interval ended.

    The returned mapping records the caller-provided detector id to the sanitized SUMO id. Distinct ids
    that collapse to the same sanitized id fail closed.
    """

    bin_period = _strict_float(period, field_name="period")
    if bin_period <= 0:
        raise ValueError("period must be positive")
    output_name = str(output_file).strip()
    if not output_name:
        raise ValueError("output_file is required")

    validated = validate_detector_lane_positions(detectors, lanes)
    raw_by_safe_id: dict[str, str] = {}
    id_mapping: dict[str, str] = {}
    detector_rows: list[tuple[str, Detector]] = []
    for detector in validated:
        raw_id = detector.detector_id.strip()
        detector_id = _register_safe_id(raw_id, raw_by_safe_id, context="detector")
        id_mapping[raw_id] = detector_id
        detector_rows.append((detector_id, detector))

    path.parent.mkdir(parents=True, exist_ok=True)
    root = ET.Element("additional")
    for detector_id, detector in sorted(detector_rows, key=lambda item: item[0]):
        ET.SubElement(
            root,
            "inductionLoop",
            {
                "id": detector_id,
                "lane": detector.lane_id,
                "pos": f"{detector.lane_position:g}",
                "period": f"{bin_period:g}",
                "file": output_name,
            },
        )
    tree = ET.ElementTree(root)
    ET.indent(tree, space="    ")
    tree.write(path, encoding="utf-8", xml_declaration=True)
    return id_mapping


def build_detector_anchored_routes(
    detectors: list[Detector],
    sources: list[str],
    sinks: list[str],
    connections: dict[str, set[str]],
    max_hops: int,
    max_routes_per_detector: int = 32,
) -> list[CandidateRoute]:
    if max_routes_per_detector <= 0:
        raise ValueError("max_routes_per_detector must be positive")
    reverse = reverse_connections(connections)
    source_set = set(sources)
    sink_set = set(sinks)
    routes: list[CandidateRoute] = []
    seen: set[tuple[str, ...]] = set()

    for detector in sorted(detectors, key=lambda item: item.detector_id):
        reverse_paths = shortest_routes_to_targets(reverse, detector.edge_id, source_set, max_hops=max_hops)
        forward_paths = shortest_routes_to_targets(connections, detector.edge_id, sink_set, max_hops=max_hops)
        if not reverse_paths or not forward_paths:
            continue
        candidates: list[tuple[str, str, tuple[str, ...]]] = []
        for source_edge, reverse_path in reverse_paths.items():
            for sink_edge, forward_path in forward_paths.items():
                route_edges = tuple([*reversed(reverse_path), *forward_path[1:]])
                if len(route_edges) != len(set(route_edges)):
                    continue
                candidates.append((source_edge, sink_edge, route_edges))
        candidates.sort(key=lambda item: (len(item[2]), item[0], item[1], item[2]))
        added = 0
        for source_edge, sink_edge, route_edges in candidates:
            if route_edges in seen:
                continue
            seen.add(route_edges)
            suffix = "" if added == 0 else f"_{added:03d}"
            routes.append(
                CandidateRoute(
                    route_id=f"detector_route_{detector.detector_id}{suffix}",
                    source_edge=source_edge,
                    sink_edge=sink_edge,
                    edges=route_edges,
                )
            )
            added += 1
            if added >= max_routes_per_detector:
                break
    return routes


def build_boundary_routes(
    sources: list[str],
    sinks: list[str],
    connections: dict[str, set[str]],
    max_routes: int,
    max_hops: int,
    min_edges: int,
) -> list[CandidateRoute]:
    routes: list[CandidateRoute] = []
    seen: set[tuple[str, ...]] = set()
    for source_edge in sources:
        for sink_edge in sinks:
            route_edges = tuple(shortest_route(connections, source_edge, sink_edge, max_hops=max_hops))
            if len(route_edges) < min_edges or route_edges in seen:
                continue
            seen.add(route_edges)
            routes.append(
                CandidateRoute(
                    route_id=f"od_route_{len(routes):04d}",
                    source_edge=source_edge,
                    sink_edge=sink_edge,
                    edges=route_edges,
                )
            )
            if len(routes) >= max_routes:
                return routes
    return routes


def merge_routes(primary: list[CandidateRoute], secondary: list[CandidateRoute], max_routes: int) -> list[CandidateRoute]:
    routes: list[CandidateRoute] = []
    seen: set[tuple[str, ...]] = set()
    for route in [*primary, *secondary]:
        if route.edges in seen:
            continue
        seen.add(route.edges)
        routes.append(route)
        if len(routes) >= max_routes:
            return routes
    return routes


def source_sink_rows(
    edges: dict[str, EdgeInfo],
    sources: list[str],
    sinks: list[str],
    *,
    measured_edge_ids: Iterable[str] = (),
) -> list[dict[str, object]]:
    """Serialize route boundary edges with an explicit provenance reason.

    The default remains the graph's physical boundary.  An opt-in replay can
    add official detector cross-sections as open boundaries; those rows are
    deliberately labelled so downstream manifests cannot mistake a local
    measured cut for a closed corridor/OD boundary.
    """

    measured = set(measured_edge_ids)
    rows: list[dict[str, object]] = []
    for role, edge_ids in (("source", sources), ("sink", sinks)):
        for edge_id in edge_ids:
            edge = edges[edge_id]
            rows.append(
                {
                    "role": role,
                    "edge_id": edge.edge_id,
                    "from_node": edge.from_node,
                    "to_node": edge.to_node,
                    "length": f"{edge.length:.2f}",
                    "reason": (
                        "official_detector_cross_section"
                        if edge_id in measured
                        else "network_boundary"
                    ),
                }
            )
    return rows


def route_rows(routes: list[CandidateRoute], edges: dict[str, EdgeInfo]) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for route in routes:
        route_length = sum(edges[edge_id].length for edge_id in route.edges if edge_id in edges)
        rows.append(
            {
                "route_id": route.route_id,
                "source_edge": route.source_edge,
                "sink_edge": route.sink_edge,
                "edge_count": len(route.edges),
                "route_length": f"{route_length:.2f}",
                "edges": " ".join(route.edges),
            }
        )
    return rows


def route_detector_incidence(routes: list[CandidateRoute], detectors: list[Detector]) -> list[dict[str, object]]:
    detectors_by_edge: dict[str, list[Detector]] = {}
    for detector in detectors:
        detectors_by_edge.setdefault(detector.edge_id, []).append(detector)

    rows: list[dict[str, object]] = []
    for route in routes:
        route_edges = set(route.edges)
        for edge_id in route.edges:
            for detector in detectors_by_edge.get(edge_id, []):
                rows.append(
                    {
                        "route_id": route.route_id,
                        "source_edge": route.source_edge,
                        "sink_edge": route.sink_edge,
                        "detector_id": detector.detector_id,
                        "detector_edge": detector.edge_id,
                        "detector_direction": detector.direction,
                        "incidence": 1 if detector.edge_id in route_edges else 0,
                    }
                )
    return rows


def aggregate_edge_counts(rows: list[dict[str, str]], begin: float, end: float) -> list[EdgeCount]:
    totals: dict[str, int] = {}
    detector_ids: dict[str, set[str]] = {}
    lane_ids: dict[str, set[str]] = {}

    for row in rows:
        row_begin = _float_value(_row_value(row, "begin", default=str(begin)))
        row_end = _float_value(_row_value(row, "end", default=str(end)))
        if row_begin < begin or row_end > end:
            continue
        edge_id = _row_value(row, "edge_id", "sumo_edge", "edge", default="")
        if not edge_id:
            continue
        total = _int_value(_row_value(row, "expected_total", "entered", "count", "total", default="0"))
        totals[edge_id] = totals.get(edge_id, 0) + total
        detector_id = _row_value(row, "detector_id", "s_idx", "id", default="")
        if detector_id:
            detector_ids.setdefault(edge_id, set()).add(safe_id(detector_id))
        lane_id = _row_value(row, "lane_id", "sumo_lane", "lane", default="")
        if lane_id:
            lane_ids.setdefault(edge_id, set()).add(lane_id)

    edge_counts = [
        EdgeCount(
            edge_id=edge_id,
            entered=totals[edge_id],
            detector_ids=tuple(sorted(detector_ids.get(edge_id, set()))),
            lane_ids=tuple(sorted(lane_ids.get(edge_id, set()))),
            begin=begin,
            end=end,
        )
        for edge_id in sorted(totals)
    ]
    return edge_counts


def aggregate_edge_counts_by_interval(
    rows: list[dict[str, str]],
    *,
    begin: float | None = None,
    end: float | None = None,
) -> list[EdgeCount]:
    """Aggregate counts per ``(begin, end, edge)`` without collapsing distinct time bins.

    When ``begin`` and ``end`` are supplied, only complete bins contained in that window are retained.
    Rows without explicit interval bounds may use the supplied window as their single bin.
    """

    if (begin is None) != (end is None):
        raise ValueError("begin and end must either both be supplied or both be omitted")
    window_begin: float | None = None
    window_end: float | None = None
    if begin is not None and end is not None:
        window_begin = _strict_float(begin, field_name="begin")
        window_end = _strict_float(end, field_name="end")
        if window_end <= window_begin:
            raise ValueError("end must be greater than begin")

    totals: dict[tuple[float, float, str], int] = {}
    detector_ids: dict[tuple[float, float, str], set[str]] = {}
    lane_ids: dict[tuple[float, float, str], set[str]] = {}
    raw_by_safe_id: dict[str, str] = {}

    for row_index, row in enumerate(rows, start=1):
        raw_begin = _row_value(row, "begin", default="")
        raw_end = _row_value(row, "end", default="")
        if not raw_begin and window_begin is None:
            raise ValueError(f"row {row_index} begin is required")
        if not raw_end and window_end is None:
            raise ValueError(f"row {row_index} end is required")
        row_begin = _strict_float(
            raw_begin if raw_begin else window_begin,
            field_name=f"row {row_index} begin",
        )
        row_end = _strict_float(
            raw_end if raw_end else window_end,
            field_name=f"row {row_index} end",
        )
        if row_end <= row_begin:
            raise ValueError(f"row {row_index} end must be greater than begin")
        if window_begin is not None and window_end is not None:
            if row_begin < window_begin or row_end > window_end:
                continue

        edge_id = _row_value(row, "edge_id", "sumo_edge", "edge", default="").strip()
        if not edge_id:
            raise ValueError(f"row {row_index} edge_id is required")
        raw_total = _row_value(row, "expected_total", "entered", "count", "total", default="")
        total = _strict_nonnegative_int(raw_total, field_name=f"row {row_index} expected count")
        key = (row_begin, row_end, edge_id)
        totals[key] = totals.get(key, 0) + total

        raw_detector_id = _row_value(row, "detector_id", "s_idx", "id", default="").strip()
        if raw_detector_id:
            detector_id = _register_safe_id(raw_detector_id, raw_by_safe_id, context="detector")
            detector_ids.setdefault(key, set()).add(detector_id)
        lane_id = _row_value(row, "lane_id", "sumo_lane", "lane", default="").strip()
        if lane_id:
            lane_ids.setdefault(key, set()).add(lane_id)

    return [
        EdgeCount(
            edge_id=edge_id,
            entered=totals[(row_begin, row_end, edge_id)],
            detector_ids=tuple(sorted(detector_ids.get((row_begin, row_end, edge_id), set()))),
            lane_ids=tuple(sorted(lane_ids.get((row_begin, row_end, edge_id), set()))),
            begin=row_begin,
            end=row_end,
        )
        for row_begin, row_end, edge_id in sorted(totals)
    ]


def constraint_rows(edge_counts: list[EdgeCount]) -> list[dict[str, object]]:
    return [
        {
            "begin": f"{edge_count.begin:g}",
            "end": f"{edge_count.end:g}",
            "edge_id": edge_count.edge_id,
            "lane_ids": " ".join(edge_count.lane_ids),
            "detector_ids": " ".join(edge_count.detector_ids),
            "expected_total": edge_count.entered,
        }
        for edge_count in edge_counts
    ]


def write_edge_data(path: Path, edge_counts: list[EdgeCount]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    root = ET.Element("data")
    if edge_counts:
        begin = min(edge_count.begin for edge_count in edge_counts)
        end = max(edge_count.end for edge_count in edge_counts)
    else:
        begin = 0.0
        end = 0.0
    interval = ET.SubElement(root, "interval", id="detector_demand_counts", begin=f"{begin:g}", end=f"{end:g}")
    for edge_count in edge_counts:
        attrs = {
            "id": edge_count.edge_id,
            "entered": str(int(edge_count.entered)),
        }
        if edge_count.detector_ids:
            attrs["detector_ids"] = " ".join(edge_count.detector_ids)
        ET.SubElement(interval, "edge", attrs)
    tree = ET.ElementTree(root)
    ET.indent(tree, space="    ")
    tree.write(path, encoding="utf-8", xml_declaration=True)


def write_interval_edge_data(path: Path, edge_counts: Iterable[EdgeCount]) -> None:
    """Write routeSampler-compatible multi-interval edge counts.

    The standard edgeData ``count`` attribute is intended for routeSampler calls that explicitly set
    ``--edgedata-attribute count``. Custom detector metadata is deliberately omitted from the XML and
    remains available in the CSV/audit layer.
    """

    grouped: dict[tuple[float, float], list[EdgeCount]] = {}
    seen_edges: set[tuple[float, float, str]] = set()
    for edge_count in edge_counts:
        interval_begin = _strict_float(edge_count.begin, field_name="edge count begin")
        interval_end = _strict_float(edge_count.end, field_name="edge count end")
        if interval_end <= interval_begin:
            raise ValueError("edge count end must be greater than begin")
        edge_id = edge_count.edge_id.strip()
        if not edge_id:
            raise ValueError("edge count edge_id is required")
        entered = _strict_nonnegative_int(edge_count.entered, field_name=f"edge {edge_id!r} entered")
        key = (interval_begin, interval_end, edge_id)
        if key in seen_edges:
            raise ValueError(
                f"duplicate edge count for edge {edge_id!r} in interval [{interval_begin:g}, {interval_end:g}]"
            )
        seen_edges.add(key)
        grouped.setdefault((interval_begin, interval_end), []).append(
            EdgeCount(
                edge_id=edge_id,
                entered=entered,
                detector_ids=edge_count.detector_ids,
                lane_ids=edge_count.lane_ids,
                begin=interval_begin,
                end=interval_end,
            )
        )

    path.parent.mkdir(parents=True, exist_ok=True)
    root = ET.Element("data")
    for interval_index, ((interval_begin, interval_end), counts) in enumerate(sorted(grouped.items())):
        interval = ET.SubElement(
            root,
            "interval",
            {
                "id": f"detector_demand_counts_{interval_index:04d}",
                "begin": f"{interval_begin:g}",
                "end": f"{interval_end:g}",
            },
        )
        for edge_count in sorted(counts, key=lambda item: item.edge_id):
            ET.SubElement(
                interval,
                "edge",
                {
                    "id": edge_count.edge_id,
                    "count": str(edge_count.entered),
                },
            )
    tree = ET.ElementTree(root)
    ET.indent(tree, space="    ")
    tree.write(path, encoding="utf-8", xml_declaration=True)


def e1_counts_by_detector_interval(detector_xml: Path) -> dict[tuple[str, str, str], int]:
    root = ET.parse(detector_xml).getroot()
    counts: dict[tuple[str, str, str], int] = {}
    for interval in root.findall(".//interval"):
        detector_id = safe_id(interval.attrib.get("id", ""))
        if not detector_id:
            continue
        key = (detector_id, _time_key(interval.attrib.get("begin")), _time_key(interval.attrib.get("end")))
        counts[key] = counts.get(key, 0) + _int_value(interval.attrib.get("nVehEntered"))
    return counts


def compare_expected_to_e1(
    expected_rows: list[dict[str, str]],
    detector_counts: dict[tuple[str, str, str], int],
) -> list[dict[str, object]]:
    comparisons: list[dict[str, object]] = []
    for row in expected_rows:
        detector_id = safe_id(_row_value(row, "detector_id", "s_idx", "id", default=""))
        if not detector_id:
            continue
        begin = _time_key(_row_value(row, "begin", default=""))
        end = _time_key(_row_value(row, "end", default=""))
        expected = _int_value(_row_value(row, "expected_total", "entered", "count", "total", default="0"))
        if begin or end:
            measured = detector_counts.get((detector_id, begin, end), 0)
        else:
            measured = sum(value for (current_id, _begin, _end), value in detector_counts.items() if current_id == detector_id)
        comparisons.append(
            {
                "detector_id": detector_id,
                "edge_id": _row_value(row, "edge_id", "sumo_edge", "edge", default=""),
                "begin": begin,
                "end": end,
                "expected_total": expected,
                "measured_nVehEntered": measured,
                "diff_entered_minus_expected": measured - expected,
            }
        )
    return comparisons


def e1_counts_by_detector_interval_strict(
    detector_xml: Path,
    *,
    count_attribute: str = "nVehContrib",
) -> dict[tuple[str, str, str], int]:
    """Parse E1 counts without conflating missing attributes, invalid values, or duplicate intervals.

    ``nVehContrib`` is the default because it counts vehicles that completely passed an E1 loop during
    the interval. ``nVehEntered`` is available explicitly for analyses that intentionally count every
    vehicle which touched the detector, including incomplete passages.
    """

    if count_attribute not in {"nVehContrib", "nVehEntered"}:
        raise ValueError("count_attribute must be 'nVehContrib' or 'nVehEntered'")

    root = ET.parse(detector_xml).getroot()
    counts: dict[tuple[str, str, str], int] = {}
    raw_by_safe_id: dict[str, str] = {}
    for interval_index, interval in enumerate(root.findall(".//interval"), start=1):
        raw_detector_id = interval.attrib.get("id", "")
        detector_id = _register_safe_id(raw_detector_id, raw_by_safe_id, context="E1 detector")
        interval_begin = _strict_float(
            interval.attrib.get("begin"),
            field_name=f"E1 interval {interval_index} begin",
        )
        interval_end = _strict_float(
            interval.attrib.get("end"),
            field_name=f"E1 interval {interval_index} end",
        )
        if interval_end <= interval_begin:
            raise ValueError(f"E1 interval {interval_index} end must be greater than begin")
        measured = _strict_nonnegative_int(
            interval.attrib.get(count_attribute),
            field_name=f"E1 interval {interval_index} {count_attribute}",
        )
        key = (detector_id, f"{interval_begin:g}", f"{interval_end:g}")
        if key in counts:
            raise ValueError(
                f"duplicate E1 interval for detector {detector_id!r} in [{interval_begin:g}, {interval_end:g}]"
            )
        counts[key] = measured
    return counts


def compare_expected_to_e1_strict(
    expected_rows: list[dict[str, str]],
    detector_counts: dict[tuple[str, str, str], int],
    *,
    count_attribute: str = "nVehContrib",
) -> list[dict[str, object]]:
    """Compare exact detector bins while representing a missing measurement as ``None``, not zero."""

    if count_attribute not in {"nVehContrib", "nVehEntered"}:
        raise ValueError("count_attribute must be 'nVehContrib' or 'nVehEntered'")
    measured_field = f"measured_{count_attribute}"
    difference_field = f"diff_{count_attribute}_minus_expected"
    comparisons: list[dict[str, object]] = []
    raw_by_safe_id: dict[str, str] = {}
    seen_expected_keys: set[tuple[str, str, str]] = set()

    for row_index, row in enumerate(expected_rows, start=1):
        raw_detector_id = _row_value(row, "detector_id", "s_idx", "id", default="")
        detector_id = _register_safe_id(raw_detector_id, raw_by_safe_id, context="expected detector")
        interval_begin = _strict_float(
            _row_value(row, "begin", default=""),
            field_name=f"expected row {row_index} begin",
        )
        interval_end = _strict_float(
            _row_value(row, "end", default=""),
            field_name=f"expected row {row_index} end",
        )
        if interval_end <= interval_begin:
            raise ValueError(f"expected row {row_index} end must be greater than begin")
        expected = _strict_nonnegative_int(
            _row_value(row, "expected_total", "entered", "count", "total", default=""),
            field_name=f"expected row {row_index} expected count",
        )
        begin_key = f"{interval_begin:g}"
        end_key = f"{interval_end:g}"
        key = (detector_id, begin_key, end_key)
        if key in seen_expected_keys:
            raise ValueError(
                f"duplicate expected interval for detector {detector_id!r} in [{begin_key}, {end_key}]"
            )
        seen_expected_keys.add(key)

        is_present = key in detector_counts
        measured = detector_counts[key] if is_present else None
        if measured is not None and measured < 0:
            raise ValueError(f"measured count for {key!r} must be non-negative")
        comparisons.append(
            {
                "detector_id": detector_id,
                "edge_id": _row_value(row, "edge_id", "sumo_edge", "edge", default=""),
                "begin": begin_key,
                "end": end_key,
                "expected_total": expected,
                "measurement_attribute": count_attribute,
                "measurement_status": "matched" if is_present else "missing",
                measured_field: measured,
                difference_field: measured - expected if measured is not None else None,
            }
        )
    return comparisons


def audit_expected_to_e1_strict(
    expected_rows: list[dict[str, str]],
    detector_xml: Path,
    *,
    count_attribute: str = "nVehContrib",
) -> list[dict[str, object]]:
    """Parse and compare strict E1 interval counts using ``nVehContrib`` by default."""

    detector_counts = e1_counts_by_detector_interval_strict(
        detector_xml,
        count_attribute=count_attribute,
    )
    return compare_expected_to_e1_strict(
        expected_rows,
        detector_counts,
        count_attribute=count_attribute,
    )


def geh_value(expected: float, measured: float) -> float:
    denominator = expected + measured
    if denominator <= 0:
        return 0.0
    return math.sqrt(2.0 * (measured - expected) ** 2 / denominator)


def summarize_comparison(rows: list[dict[str, object]]) -> dict[str, object]:
    if not rows:
        return {
            "edge_rows": 0,
            "expected_total": 0,
            "measured_total": None,
            "MAE": None,
            "RMSE": None,
            "max_abs_error": None,
            "signed_bias": None,
            "GEH_lt5_percent": None,
        }

    expected = [float(row["expected_total"]) for row in rows]
    measured = [float(row["measured_nVehEntered"]) for row in rows]
    diffs = [m - e for e, m in zip(expected, measured)]
    abs_diffs = [abs(diff) for diff in diffs]
    squared = [diff * diff for diff in diffs]
    geh_lt5 = sum(1 for e, m in zip(expected, measured) if geh_value(e, m) < 5)
    return {
        "edge_rows": len(rows),
        "expected_total": int(sum(expected)),
        "measured_total": int(sum(measured)),
        "MAE": sum(abs_diffs) / len(rows),
        "RMSE": math.sqrt(sum(squared) / len(rows)),
        "max_abs_error": int(max(abs_diffs)),
        "signed_bias": sum(diffs) / len(rows),
        "GEH_lt5_percent": 100.0 * geh_lt5 / len(rows),
    }
