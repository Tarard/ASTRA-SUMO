"""Diagnose lane correspondence without turning positional agreement into field truth."""

from collections import Counter
import math

from scipy.optimize import linear_sum_assignment
from shapely.geometry import LineString, Point

from .network_calibration import _permissions, centerline_overlap_metrics


def assign_nonmotor_lanes(lanes, network_lanes, reserved):
    """Assign only nearby crossings or dedicated cycle lanes, with distinct SUMO identities."""
    assignments, diagnostics = {}, {}
    for mode in ("bicycle", "pedestrian"):
        official = [(key, row) for key, row in lanes.items() if row["mode"] == mode and key not in reserved]
        available = [(key, row) for key, row in network_lanes.items() if key not in reserved.values()
                     and ((row[0].get("function") == "crossing") if mode == "pedestrian"
                          else row[0].get("function", "normal") == "normal" and _permissions(row[1]) == {"bicycle"})]
        available.sort(key=lambda item: item[0])
        if not official:
            continue
        costs = []
        for key, row in official:
            distances = [sum(Point(point).distance(candidate[2]) for point in row["points"]) / len(row["points"])
                         for _, candidate in available]
            diagnostics[key] = {"candidate_class": "crossing" if mode == "pedestrian" else "dedicated_bicycle",
                                "nearest_candidate_distance_m": min(distances, default=None),
                                "maximum_association_distance_m": 2.0, "assignment": "one_to_one"}
            if row.get("direction") is not None:
                for j, (_, candidate) in enumerate(available):
                    metrics = centerline_overlap_metrics(LineString(row["points"]), [list(candidate[2].coords)], 0.5,
                        direction=row["direction"])
                    if metrics["reference_coverage"] == 0:
                        distances[j] = 1e9
            costs.append(distances + [2.000001] * len(official))
        rows, columns = linear_sum_assignment(costs)
        for i, j in zip(rows, columns):
            if j < len(available) and costs[i][j] <= 2.0:
                assignments[official[i][0]] = available[j][0]
    return assignments, diagnostics


def diagnose_lane_match(*, parts, reference, network_lanes, spacing, direction, alternative_paths=None):
    """Keep alternate lane indices visible instead of silently choosing a better score."""
    shapes = [list(network_lanes[key][2].coords) for key in parts]
    metrics = centerline_overlap_metrics(reference, shapes, spacing, direction=direction)
    width = min(float(network_lanes[key][1].get("width", "3.2")) for key in parts)
    alternatives = []
    candidates = alternative_paths if alternative_paths is not None else [
        {"index_shift": shift, "lane_ids": [f"{key.rsplit('_', 1)[0]}_{int(key.rsplit('_', 1)[1]) + shift}" for key in parts]}
        for shift in (-1, 1)]
    for candidate in candidates:
        shift, other = candidate["index_shift"], candidate["lane_ids"]
        if any(key not in network_lanes or "passenger" not in _permissions(network_lanes[key][1]) for key in other):
            continue
        result = centerline_overlap_metrics(reference, [list(network_lanes[key][2].coords) for key in other], spacing,
                                            direction=direction)
        alternatives.append({"lane_ids": other, "index_shift": shift, "metrics": result})
    mean = metrics["mean_lateral_offset_m"]
    other_means = [row["metrics"]["mean_lateral_offset_m"] for row in alternatives
                   if row["metrics"]["mean_lateral_offset_m"] is not None]
    best_other = min(other_means, default=math.inf)
    conflict = mean is not None and best_other < mean
    confidence = ("none" if mean is None else "conflict" if conflict else "high"
                  if mean <= width / 2 and best_other - mean >= 1 else "medium" if mean <= width else "low")
    diagnostics = {"lane_width_m": width, "mean_lateral_offset_m": mean,
                   "best_alternative_margin_m": best_other - mean if mean is not None and other_means else None,
                   "conflicting_alternative": conflict,
                   "confidence_rule": "whole_lane_mean_le_half_width_and_neighbor_margin_ge_1m"}
    return confidence, alternatives, diagnostics


def duplicate_lane_assignments(selected):
    counts = Counter(selected.values())
    return {key for key, lane in selected.items() if counts[lane] > 1}
