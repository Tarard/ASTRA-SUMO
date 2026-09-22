"""Trace a bounded road curve on a caller-supplied probability raster.

The moving-rectangle idea follows the public description in patent
US8938094B1. Torii supplies neither Google's imagery model nor Google data.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np


Point = tuple[float, float]


@dataclass(frozen=True)
class _State:
    score: float
    x: float
    y: float
    heading_deg: float
    path: tuple[Point, ...]


def trace_probability_curve(
    probability: np.ndarray,
    *,
    start: Point,
    end: Point,
    start_heading_deg: float,
    end_heading_deg: float,
    step_px: float = 2.0,
    probe_length_px: float = 5.0,
    probe_width_px: float = 3.0,
    beam_width: int = 20,
    minimum_probe_probability: float = 0.05,
) -> list[Point]:
    """Trace one curve while a moving rectangle favors high-probability pixels."""
    field = np.asarray(probability, dtype=float)
    if field.ndim != 2 or not np.isfinite(field).all() or field.max(initial=0.0) <= 0 or field.min(initial=0) < 0 or field.max() > 1:
        raise ValueError("probability field must be a finite, non-empty 2D array")
    if any(not math.isfinite(v) or v <= 0 for v in (step_px, probe_length_px, probe_width_px)) or type(beam_width) is not int or beam_width < 1:
        raise ValueError("trace dimensions and beam_width must be positive")
    if not math.isfinite(minimum_probe_probability) or not 0 < minimum_probe_probability <= 1:
        raise ValueError("minimum probe probability must be in (0, 1]")
    if not all(map(math.isfinite, (start_heading_deg, end_heading_deg))):
        raise ValueError("trace headings must be finite")
    _require_inside(field, start)
    _require_inside(field, end)

    initial_distance = math.dist(start, end)
    max_steps = max(8, math.ceil(initial_distance / step_px * 2.4) + 12)
    reach_distance = max(2.0, step_px * 1.6)
    beam = [_State(0.0, start[0], start[1], start_heading_deg, (start,))]
    completed: list[_State] = []

    for _ in range(max_steps):
        candidates: list[_State] = []
        for state in beam:
            old_distance = math.hypot(end[0] - state.x, end[1] - state.y)
            target_heading = math.degrees(math.atan2(end[1] - state.y, end[0] - state.x))
            steering = {-24.0, -12.0, 0.0, 12.0, 24.0}
            steering.add(_clamp(_angle_delta(target_heading, state.heading_deg), -24.0, 24.0))
            for turn in steering:
                heading = state.heading_deg + turn
                radians = math.radians(heading)
                point = (state.x + math.cos(radians) * step_px, state.y + math.sin(radians) * step_px)
                if not _inside(field, point) or _loops_back(state.path, point, step_px):
                    continue
                if not curve_has_support(field, [(state.x, state.y), point], minimum_probe_probability):
                    continue
                new_distance = math.dist(point, end)
                if new_distance > old_distance + step_px * 0.15:
                    continue
                road_score = _rectangle_mean(
                    field,
                    point,
                    heading,
                    length=probe_length_px,
                    width=probe_width_px,
                )
                if road_score < minimum_probe_probability:
                    continue
                progress = (old_distance - new_distance) / step_px
                end_weight = max(0.0, 1.0 - new_distance / max(initial_distance, 1.0))
                end_alignment = abs(_angle_delta(end_heading_deg, heading)) / 180.0
                score = (
                    state.score
                    + 3.0 * road_score
                    + 0.8 * progress
                    - 0.18 * abs(turn) / 24.0
                    - 0.25 * end_weight * end_alignment
                )
                next_state = _State(score, point[0], point[1], heading, (*state.path, point))
                if new_distance <= reach_distance and curve_has_support(field, [point, end], minimum_probe_probability):
                    completed.append(next_state)
                else:
                    candidates.append(next_state)
        if completed:
            best = max(
                completed,
                key=lambda item: item.score
                - math.dist((item.x, item.y), end)
                - abs(_angle_delta(end_heading_deg, item.heading_deg)) / 90.0,
            )
            return [*best.path, end]
        if not candidates:
            break
        beam = _deduplicate(candidates, step_px)[:beam_width]

    raise ValueError("probability trace did not reach the requested endpoint")


def _rectangle_mean(
    field: np.ndarray,
    center: Point,
    heading_deg: float,
    *,
    length: float,
    width: float,
) -> float:
    angle = math.radians(heading_deg)
    forward = (math.cos(angle), math.sin(angle))
    side = (-forward[1], forward[0])
    values = []
    for along in np.linspace(-length / 2, length / 2, 7):
        for across in np.linspace(-width / 2, width / 2, 5):
            x = center[0] + forward[0] * along + side[0] * across
            y = center[1] + forward[1] * along + side[1] * across
            if _inside(field, (x, y)):
                values.append(float(field[round(y), round(x)]))
    return float(np.mean(values)) if values else 0.0


def _deduplicate(states: list[_State], step_px: float) -> list[_State]:
    best: dict[tuple[int, int, int], _State] = {}
    for state in sorted(states, key=lambda item: item.score, reverse=True):
        key = (
            round(state.x / step_px),
            round(state.y / step_px),
            round((state.heading_deg % 360) / 12),
        )
        best.setdefault(key, state)
    return sorted(best.values(), key=lambda item: item.score, reverse=True)


def _loops_back(path: tuple[Point, ...], point: Point, step_px: float) -> bool:
    return any(math.dist(previous, point) < step_px * 0.65 for previous in path[:-3])


def _angle_delta(target_deg: float, source_deg: float) -> float:
    return (target_deg - source_deg + 180.0) % 360.0 - 180.0


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _inside(field: np.ndarray, point: Point) -> bool:
    return all(map(math.isfinite, point)) and 0 <= point[0] <= field.shape[1] - 1 and 0 <= point[1] <= field.shape[0] - 1


def curve_has_support(field: np.ndarray, points: list[Point], minimum: float = 0.05) -> bool:
    """Check every segment, including a final bridge or a smoothed curve, at half-pixel spacing."""
    for a, b in zip(points, points[1:]):
        if not _inside(field, a) or not _inside(field, b):
            return False
        samples = np.linspace(a, b, max(2, math.ceil(math.dist(a, b) * 2) + 1))
        x, y = np.rint(samples).astype(int).T
        if np.any(field[y, x] < minimum):
            return False
    return len(points) >= 2


def _require_inside(field: np.ndarray, point: Point) -> None:
    if not _inside(field, point):
        raise ValueError(f"trace endpoint is outside probability field: {point}")
