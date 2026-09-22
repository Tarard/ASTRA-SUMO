"""Movement curves, local coordinate helpers and legacy raster envelopes."""

from __future__ import annotations

import math
import xml.etree.ElementTree as ET
from collections import defaultdict
from collections.abc import Mapping, Sequence
from typing import Any
import numpy as np
from PIL import Image, ImageDraw
from scipy import ndimage


Point = tuple[float, float]


def movement_surface_polygon(
    movements: Sequence[Sequence[Point]],
    *,
    lane_width_m: float = 3.2,
    resolution_m: float = 0.1,
    simplify_m: float = 0.15,
) -> list[Point]:
    """Trace the outer boundary of buffered movement lines."""
    lines = [[(float(x), float(y)) for x, y in line] for line in movements if len(line) >= 2]
    if not lines:
        raise ValueError("movements must contain at least one two-point line")
    if lane_width_m <= 0 or resolution_m <= 0 or simplify_m <= 0:
        raise ValueError("surface dimensions must be positive")
    points = [point for line in lines for point in line]
    pad = lane_width_m
    min_x, max_x = min(x for x, _ in points) - pad, max(x for x, _ in points) + pad
    min_y, max_y = min(y for _, y in points) - pad, max(y for _, y in points) + pad
    width = math.ceil((max_x - min_x) / resolution_m) + 1
    height = math.ceil((max_y - min_y) / resolution_m) + 1
    image = Image.new("1", (width, height), 0)
    draw = ImageDraw.Draw(image)

    def pixel(point: Point) -> tuple[int, int]:
        return (
            round((point[0] - min_x) / resolution_m),
            round((max_y - point[1]) / resolution_m),
        )

    line_width = max(3, round(lane_width_m / resolution_m))
    for line in lines:
        draw.line([pixel(point) for point in line], fill=1, width=line_width, joint="curve")
    mask = ndimage.binary_closing(np.asarray(image, dtype=bool), iterations=2)
    labels, count = ndimage.label(mask)
    if count > 1:
        sizes = ndimage.sum(mask, labels, range(1, count + 1))
        mask = labels == int(np.argmax(sizes)) + 1
    contour = _marching_loop(mask)
    polygon = [
        (min_x + x * resolution_m, max_y - y * resolution_m)
        for x, y in contour
    ]
    simplified = _simplify_closed(polygon, simplify_m)
    if len(simplified) < 8:
        raise ValueError("movement surface produced fewer than eight boundary points")
    return simplified


def reanchor_movement_shape(
    shape: Sequence[Point],
    *,
    start: Point,
    end: Point,
) -> list[Point]:
    """Move both endpoints and blend their corrections through the curve."""
    values = [(float(x), float(y)) for x, y in shape]
    if len(values) < 2:
        raise ValueError("shape must contain at least two points")
    start_delta = (start[0] - values[0][0], start[1] - values[0][1])
    end_delta = (end[0] - values[-1][0], end[1] - values[-1][1])
    cumulative = [0.0]
    for left, right in zip(values, values[1:]):
        cumulative.append(cumulative[-1] + math.dist(left, right))
    total = cumulative[-1]
    result = []
    for point, station in zip(values, cumulative):
        ratio = station / total if total > 0 else 0.0
        result.append(
            (
                point[0] + start_delta[0] * (1 - ratio) + end_delta[0] * ratio,
                point[1] + start_delta[1] * (1 - ratio) + end_delta[1] * ratio,
            )
        )
    result[0], result[-1] = start, end
    return result


def fit_movement_shape_to_anchors(
    shape: Sequence[Point],
    *,
    start: Point,
    end: Point,
) -> tuple[list[Point], float]:
    """Trim a movement to ordered anchors, then remove the small residual gap."""
    values = [(float(x), float(y)) for x, y in shape]
    if len(values) < 2:
        raise ValueError("shape must contain at least two points")
    projections = [
        (
            _segment_projection(start, left, right),
            _segment_projection(end, left, right),
        )
        for left, right in zip(values, values[1:])
    ]
    choices = []
    for start_index, (start_projection, _) in enumerate(projections):
        for end_index in range(start_index, len(projections)):
            end_projection = projections[end_index][1]
            if start_index == end_index and end_projection[1] < start_projection[1]:
                continue
            choices.append(
                (
                    start_projection[2] + end_projection[2],
                    start_index,
                    end_index,
                    start_projection[0],
                    end_projection[0],
                )
            )
    if not choices:
        return [start, end], math.dist(start, values[0]) + math.dist(end, values[-1])
    error, start_index, end_index, first, last = min(choices, key=lambda row: row[0])
    trimmed = [first, *values[start_index + 1 : end_index + 1], last]
    trimmed = [
        point
        for index, point in enumerate(trimmed)
        if index == 0 or math.dist(point, trimmed[index - 1]) > 1e-6
    ]
    if len(trimmed) < 2:
        trimmed = [first, last]
    return reanchor_movement_shape(trimmed, start=start, end=end), error


def _segment_projection(point: Point, start: Point, end: Point) -> tuple[Point, float, float]:
    dx, dy = end[0] - start[0], end[1] - start[1]
    if dx == dy == 0:
        return start, 0.0, math.dist(point, start)
    ratio = max(
        0.0,
        min(
            1.0,
            ((point[0] - start[0]) * dx + (point[1] - start[1]) * dy)
            / (dx * dx + dy * dy),
        ),
    )
    projection = (start[0] + ratio * dx, start[1] + ratio * dy)
    return projection, ratio, math.dist(point, projection)


def _marching_loop(mask: np.ndarray) -> list[Point]:
    top, right, bottom, left = (0.5, 0.0), (1.0, 0.5), (0.5, 1.0), (0.0, 0.5)
    table = {
        1: ((left, top),),
        2: ((top, right),),
        3: ((left, right),),
        4: ((right, bottom),),
        5: ((left, top), (right, bottom)),
        6: ((top, bottom),),
        7: ((left, bottom),),
        8: ((bottom, left),),
        9: ((top, bottom),),
        10: ((top, right), (bottom, left)),
        11: ((right, bottom),),
        12: ((left, right),),
        13: ((top, right),),
        14: ((left, top),),
    }
    segments = []
    for y in range(mask.shape[0] - 1):
        for x in range(mask.shape[1] - 1):
            code = (
                int(mask[y, x])
                + 2 * int(mask[y, x + 1])
                + 4 * int(mask[y + 1, x + 1])
                + 8 * int(mask[y + 1, x])
            )
            for first, second in table.get(code, ()):
                segments.append(
                    ((x + first[0], y + first[1]), (x + second[0], y + second[1]))
                )
    adjacency: dict[Point, list[tuple[int, Point]]] = defaultdict(list)
    for index, (first, second) in enumerate(segments):
        adjacency[first].append((index, second))
        adjacency[second].append((index, first))
    unused = set(range(len(segments)))
    loops = []
    while unused:
        index = next(iter(unused))
        start, current = segments[index]
        unused.remove(index)
        loop = [start, current]
        while current != start:
            choices = [(item, other) for item, other in adjacency[current] if item in unused]
            if not choices:
                break
            item, current = choices[0]
            unused.remove(item)
            loop.append(current)
        if len(loop) >= 4 and loop[-1] == start:
            loops.append(loop[:-1])
    if not loops:
        raise ValueError("movement surface has no closed outer boundary")
    return max(loops, key=_polygon_area)


def _simplify_closed(points: list[Point], tolerance: float) -> list[Point]:
    values = points[:]
    changed = True
    while changed and len(values) > 12:
        changed = False
        kept = []
        for index, point in enumerate(values):
            if (
                index % 2 == 0
                and _point_segment_distance(
                    point,
                    values[index - 1],
                    values[(index + 1) % len(values)],
                )
                < tolerance
            ):
                changed = True
            else:
                kept.append(point)
        values = kept
    return values


def _point_segment_distance(point: Point, start: Point, end: Point) -> float:
    dx, dy = end[0] - start[0], end[1] - start[1]
    if dx == dy == 0:
        return math.dist(point, start)
    ratio = max(
        0.0,
        min(
            1.0,
            ((point[0] - start[0]) * dx + (point[1] - start[1]) * dy)
            / (dx * dx + dy * dy),
        ),
    )
    return math.dist(point, (start[0] + ratio * dx, start[1] + ratio * dy))


def _polygon_area(points: Sequence[Point]) -> float:
    return abs(
        sum(
            left[0] * right[1] - right[0] * left[1]
            for left, right in zip(points, [*points[1:], points[0]])
        )
    ) / 2


def _movement_centroid(movements: Sequence[Mapping[str, Any]], offset: Point) -> Point:
    values = []
    for movement in movements:
        shape = movement["selected_shape_network"]
        values.append(
            (
                (float(shape[0][0]) + float(shape[-1][0])) / 2 + offset[0],
                (float(shape[0][1]) + float(shape[-1][1])) / 2 + offset[1],
            )
        )
    return (
        sum(point[0] for point in values) / len(values),
        sum(point[1] for point in values) / len(values),
    )


def _distance_to_lines(point: Point, lines: Sequence[Sequence[Point]]) -> float:
    return min(
        _point_segment_distance(point, start, end)
        for line in lines
        for start, end in zip(line, line[1:])
    )


def _net_offset(root: ET.Element) -> Point:
    location = root.find("location")
    if location is None or not location.attrib.get("netOffset"):
        return (0.0, 0.0)
    values = location.attrib["netOffset"].split(",")
    return (float(values[0]), float(values[1]))


def _parse_shape(value: str) -> list[Point]:
    return [
        (float(token.split(",")[0]), float(token.split(",")[1]))
        for token in value.split()
        if "," in token
    ]


def _shape(points: Sequence[Point]) -> str:
    return " ".join(f"{x:.3f},{y:.3f}" for x, y in points)
