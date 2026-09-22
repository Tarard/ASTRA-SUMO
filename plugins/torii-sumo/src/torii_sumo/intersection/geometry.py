from __future__ import annotations

import math


Point = tuple[float, float]


def normalize_signed_angle(delta: float) -> float:
    """Return an angle in [-180, 180]."""
    normalized = (delta + 180) % 360 - 180
    return 180 if normalized == -180 and delta > 0 else normalized


def abs_angle_between(a: float, b: float) -> float:
    return abs(normalize_signed_angle(b - a))


def classify_angle_relation(abs_delta: float) -> str:
    if abs_delta < 25:
        return "same_direction"
    if abs_delta < 70:
        return "acute_merge"
    if abs_delta <= 115:
        return "right_angle"
    if abs_delta < 155:
        return "obtuse_merge"
    if abs_delta <= 180:
        return "opposite_direction"
    return "unknown"


def bearing_between_xy(a: Point, b: Point) -> float:
    dx = b[0] - a[0]
    dy = b[1] - a[1]
    return (math.degrees(math.atan2(dx, dy)) + 360) % 360


def euclidean_distance(a: Point, b: Point) -> float:
    return math.hypot(b[0] - a[0], b[1] - a[1])


def segment_intersection(a1: Point, a2: Point, b1: Point, b2: Point) -> Point | None:
    ax = a2[0] - a1[0]
    ay = a2[1] - a1[1]
    bx = b2[0] - b1[0]
    by = b2[1] - b1[1]
    denom = ax * by - ay * bx
    if denom == 0:
        return None
    cx = b1[0] - a1[0]
    cy = b1[1] - a1[1]
    t = (cx * by - cy * bx) / denom
    u = (cx * ay - cy * ax) / denom
    if 0 <= t <= 1 and 0 <= u <= 1:
        return (a1[0] + t * ax, a1[1] + t * ay)
    return None


def segment_distance(a1: Point, a2: Point, b1: Point, b2: Point) -> float:
    if segment_intersection(a1, a2, b1, b2) is not None:
        return 0.0
    return min(
        _point_segment_distance(a1, b1, b2),
        _point_segment_distance(a2, b1, b2),
        _point_segment_distance(b1, a1, a2),
        _point_segment_distance(b2, a1, a2),
    )


def _point_segment_distance(p: Point, a: Point, b: Point) -> float:
    dx = b[0] - a[0]
    dy = b[1] - a[1]
    length_sq = dx * dx + dy * dy
    if length_sq == 0:
        return euclidean_distance(p, a)
    t = max(0.0, min(1.0, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / length_sq))
    projection = (a[0] + t * dx, a[1] + t * dy)
    return euclidean_distance(p, projection)
