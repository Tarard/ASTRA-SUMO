from __future__ import annotations

import math
from typing import Any

Point = tuple[float, float]


def build_lane_buffered_approach_footprint(
    net: dict[str, Any],
    node_ids: set[str],
    *,
    setback_m: float,
    default_lane_width_m: float = 3.2,
) -> dict[str, Any]:
    shape_edges = []
    review_edges = []
    for edge in net["edges"]:
        if edge.get("internal") or not edge.get("shape") or ((edge["from"] in node_ids) == (edge["to"] in node_ids)):
            continue
        if _is_review_modal_edge(edge):
            review_edges.append(edge)
        elif _is_shape_support_edge(edge):
            shape_edges.append(edge)

    points: list[Point] = []
    for edge in shape_edges:
        points.extend(_lane_buffer_points(edge, node_ids, setback_m, default_lane_width_m))

    return {
        "polygon": _convex_hull(points),
        "shape_support_edge_ids": sorted(edge["id"] for edge in shape_edges),
        "review_support_edge_ids": sorted(edge["id"] for edge in review_edges),
        "buffer_point_count": len(points),
    }


def _is_shape_support_edge(edge: dict[str, Any]) -> bool:
    text = _edge_text(edge)
    if any(token in text for token in ("footway", "cycleway", "pedestrian", "bicycle")):
        return False
    return True


def _is_review_modal_edge(edge: dict[str, Any]) -> bool:
    text = _edge_text(edge)
    return any(token in text for token in ("footway", "cycleway", "pedestrian", "bicycle"))


def _edge_text(edge: dict[str, Any]) -> str:
    return f"{edge.get('type', '')} {edge.get('allow', '')}".lower()


def _lane_buffer_points(
    edge: dict[str, Any],
    node_ids: set[str],
    setback_m: float,
    default_lane_width_m: float,
) -> list[Point]:
    shape = edge["shape"]
    point = _point_along_shape(shape, setback_m) if edge["from"] in node_ids else _point_before_shape_end(shape, setback_m)
    direction = _direction_away_from_core(shape) if edge["from"] in node_ids else _direction_towards_core(shape)
    nx, ny = -direction[1], direction[0]
    half_width = _edge_half_width(edge, default_lane_width_m)
    return [(point[0] + nx * half_width, point[1] + ny * half_width), (point[0] - nx * half_width, point[1] - ny * half_width)]


def _edge_half_width(edge: dict[str, Any], default_lane_width_m: float) -> float:
    lane_count = max(1, int(edge.get("lane_count") or 1))
    lane_width = float(edge.get("lane_width") or default_lane_width_m)
    return lane_count * lane_width / 2


def _direction_away_from_core(shape: list[Point]) -> Point:
    return _unit_vector(shape[0], shape[1] if len(shape) > 1 else shape[0])


def _direction_towards_core(shape: list[Point]) -> Point:
    return _unit_vector(shape[-1], shape[-2] if len(shape) > 1 else shape[-1])


def _unit_vector(start: Point, end: Point) -> Point:
    dx, dy = end[0] - start[0], end[1] - start[1]
    length = math.hypot(dx, dy)
    if length == 0:
        return (1.0, 0.0)
    return (dx / length, dy / length)


def _point_along_shape(shape: list[Point], distance_m: float) -> Point:
    remaining = distance_m
    for start, end in zip(shape, shape[1:]):
        segment_length = _distance(start, end)
        if segment_length >= remaining:
            ratio = remaining / segment_length if segment_length else 0.0
            return (start[0] + (end[0] - start[0]) * ratio, start[1] + (end[1] - start[1]) * ratio)
        remaining -= segment_length
    return shape[-1]


def _point_before_shape_end(shape: list[Point], distance_m: float) -> Point:
    return _point_along_shape(list(reversed(shape)), distance_m)


def _convex_hull(points: list[Point]) -> list[Point]:
    unique = sorted(set(points))
    if len(unique) <= 1:
        return unique

    def cross(origin: Point, a: Point, b: Point) -> float:
        return (a[0] - origin[0]) * (b[1] - origin[1]) - (a[1] - origin[1]) * (b[0] - origin[0])

    lower: list[Point] = []
    for point in unique:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], point) <= 0:
            lower.pop()
        lower.append(point)

    upper: list[Point] = []
    for point in reversed(unique):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], point) <= 0:
            upper.pop()
        upper.append(point)

    return lower[:-1] + upper[:-1]


def _distance(a: Point, b: Point) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])
