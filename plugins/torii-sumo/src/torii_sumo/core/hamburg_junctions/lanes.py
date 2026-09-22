"""Bind official lanes by direction, road order and retained identity."""

from __future__ import annotations

import math
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from typing import Any
import numpy as np
from .geometry import Point


def _bind_official_lanes(
    lanes: Sequence[Mapping[str, Any]],
    movements: Sequence[Mapping[str, Any]],
    candidates: Mapping[tuple[str, int], Sequence[Point]],
    *,
    role: str,
    max_error_m: float,
    margin_m: float,
    candidate_modes: Mapping[tuple[str, int], set[str]] | None = None,
    internal_lane_ids: Sequence[str] = (),
) -> dict[str, Any]:
    """Bind each official lane once; a close second match remains unresolved."""
    identity_key = f"{role}_lane_id"
    lane_by_id = {str(lane["lane_id"]): lane for lane in lanes if str(lane.get("lane_type", "")).lower() == "vehicle"}
    bindings: dict[str, tuple[str, int]] = {}
    records = []
    ambiguous = []
    lane_options = {}
    internal = set(map(str, internal_lane_ids))
    for lane_id in sorted({str(row[identity_key]) for row in movements}):
        if lane_id in internal:
            record = {"lane_id": lane_id, "role": role, "reason": "official_anchor_inside_join", "candidates": []}
            records.append(record)
            ambiguous.append(record)
            continue
        lane = lane_by_id.get(lane_id)
        if lane is None:
            ambiguous.append({"lane_id": lane_id, "role": role, "reason": "official_lane_geometry_missing"})
            continue
        if lane.get("permission_status") == "review_required":
            ambiguous.append({"lane_id": lane_id, "role": role, "reason": "official_vehicle_permission_requires_review"})
            continue
        required_modes = set(lane.get("allowed_vehicle_classes") or [])
        opposite = "egress_lane_id" if role == "ingress" else "ingress_lane_id"
        for movement in movements:
            if str(movement[identity_key]) == lane_id:
                other = lane_by_id.get(str(movement.get(opposite, "")), {})
                required_modes.update(other.get("allowed_vehicle_classes") or [])
        if lane.get("junction_endpoint_network") is not None:
            shape = lane["shape_network"]
            boundary = lane["junction_endpoint_network"]
            toward_boundary = (
                (shape[1], shape[0])
                if math.dist(shape[0], boundary) < math.dist(shape[-1], boundary)
                else (shape[-2], shape[-1])
            )
            first, last = toward_boundary if role == "ingress" else tuple(reversed(toward_boundary))
        else:
            movement = next(row for row in movements if str(row[identity_key]) == lane_id)
            shape = movement["selected_shape_network"]
            first, last = (shape[0], shape[1]) if role == "ingress" else (shape[-2], shape[-1])
        direction = (last[0] - first[0], last[1] - first[1])
        options = []
        for key, candidate in candidates.items():
            if required_modes and not required_modes.issubset((candidate_modes or {}).get(key, set())):
                continue
            a, b = (candidate[-2], candidate[-1]) if role == "ingress" else (candidate[0], candidate[1])
            delta = (b[0] - a[0], b[1] - a[1])
            norm = math.hypot(*direction) * math.hypot(*delta)
            if norm <= 1e-9 or sum(x * y for x, y in zip(direction, delta)) / norm < math.cos(math.radians(45)):
                continue
            error = _lane_overlap_error(lane["shape_network"], candidate)
            if math.isfinite(error):
                options.append((error, key))
        options.sort()
        lane_options[lane_id] = options
        if not options or options[0][0] > max_error_m:
            reason = "no_directed_lane_within_limit"
        elif len(options) > 1 and options[1][0] - options[0][0] < margin_m:
            reason = "lane_match_ambiguous"
        else:
            bindings[lane_id] = options[0][1]
            reason = "unique_directed_official_lane_fit"
        record = {"lane_id": lane_id, "role": role, "reason": reason, "candidates": [{"edge": key[0], "lane": key[1], "mean_error_m": round(error, 6)} for error, key in options[:2]]}
        records.append(record)
        if lane_id not in bindings:
            ambiguous.append(record)
    owners: dict[tuple[str, int], list[str]] = defaultdict(list)
    for lane_id, key in bindings.items():
        owners[key].append(lane_id)
    for key, lane_ids in owners.items():
        if len(lane_ids) > 1:
            for lane_id in lane_ids:
                bindings.pop(lane_id)
                ambiguous.append({"lane_id": lane_id, "role": role, "reason": "multiple_official_lanes_share_sumo_lane", "sumo_lane": list(key)})
    ordered = _ordered_approach_lane_bindings(
        lane_by_id, lane_options, candidates, bindings,
        role=role, max_error_m=max_error_m, margin_m=margin_m,
    )
    for group in ordered:
        for lane_id, key in group["bindings"].items():
            bindings[lane_id] = key
            next(row for row in records if row["lane_id"] == lane_id)["reason"] = "ordered_same_approach_lane_fit"
    # Missing source lanes need not erase the remaining clear matches. After
    # equal-count ordering, keep only mutually nearest, separated matches.
    # Use the existing margin in both directions; never assign both claims.
    for key, lane_ids in owners.items():
        if len(lane_ids) < 2 or key in bindings.values():
            continue
        reverse = sorted((error, lane_id) for lane_id, options in lane_options.items()
                         for error, target in options if target == key)
        error, lane_id = reverse[0]
        if lane_id not in lane_ids or lane_id in bindings or reverse[1][0] - error < margin_m:
            continue
        bindings[lane_id] = key
        record = next(row for row in records if row["lane_id"] == lane_id)
        record.update(reason="mutual_unique_directed_official_lane_fit", reverse_match_margin_m=round(reverse[1][0] - error, 6))
    ambiguous = [row for row in ambiguous if row["lane_id"] not in bindings]
    return {"bindings": bindings, "records": records, "ambiguous": ambiguous, "ordered_groups": ordered}


def _ordered_approach_lane_bindings(
    lanes, options, candidates, existing, *, role, max_error_m, margin_m,
) -> list[dict[str, Any]]:
    """Resolve a lateral offset only for one proven, equal-count road arm."""
    approaches = defaultdict(list)
    for lane_id, lane in lanes.items():
        if lane_id not in options:
            continue  # Only this physical part's movement lanes share an approach.
        approach = str(lane.get(f"{role}_approach", "")).strip()
        if approach and approach != "0":
            approaches[approach].append(lane_id)
    result = []
    occupied = dict(existing)
    for approach, lane_ids in approaches.items():
        if len(lane_ids) < 2 or any(lane_id not in options for lane_id in lane_ids):
            continue
        if all(lane_id in occupied for lane_id in lane_ids):
            continue
        # Select the road arm before checking its lane count. A closer road
        # with a different count cannot be replaced by a more distant match.
        chosen_edges = []
        for lane_id in lane_ids:
            edge_errors = {}
            for error, (edge_id, _) in options[lane_id]:
                edge_errors[edge_id] = min(error, edge_errors.get(edge_id, math.inf))
            ranked = sorted((error, edge_id) for edge_id, error in edge_errors.items())
            if not ranked or ranked[0][0] > max_error_m or (len(ranked) > 1 and ranked[1][0] - ranked[0][0] < margin_m):
                break
            chosen_edges.append(ranked[0][1])
        if len(chosen_edges) != len(lane_ids) or len(set(chosen_edges)) != 1:
            continue
        edge_id = chosen_edges[0]
        if any(
            other_id not in lane_ids and values and values[0][0] <= max_error_m and values[0][1][0] == edge_id
            for other_id, values in options.items()
        ):
            continue
        edge_lanes = {key: shape for key, shape in candidates.items() if key[0] == edge_id}
        if len(edge_lanes) != len(lane_ids):
            continue
        if any(key in edge_lanes for lane_id, key in occupied.items() if lane_id not in lane_ids):
            continue
        # The road axis comes from the SUMO approach. A movement curve may
        # already turn at its first point, so its mean tangent is not the arm axis.
        direction = np.asarray([
            np.asarray(shape[-1]) - shape[-2] if role == "ingress" else np.asarray(shape[1]) - shape[0]
            for shape in edge_lanes.values()
        ], dtype=float)
        lengths = np.linalg.norm(direction, axis=1)
        if min(lengths) <= 1e-9:
            continue
        unit = direction / lengths[:, None]
        axis = unit.mean(axis=0)
        axis_length = float(np.linalg.norm(axis))
        if axis_length <= 1e-9:
            continue
        axis /= axis_length
        if min(unit @ axis) < math.cos(math.radians(45)):
            continue
        official = {lane_id: lanes[lane_id]["shape_network"] for lane_id in lane_ids}
        all_shapes = [*official.values(), *edge_lanes.values()]
        extents = [(min(np.asarray(shape) @ axis), max(np.asarray(shape) @ axis)) for shape in all_shapes]
        lower, upper = max(a for a, _ in extents), min(b for _, b in extents)
        if upper - lower <= 1e-6:
            continue
        stations = [lower + fraction * (upper - lower) for fraction in (0.2, 0.5, 0.8)]
        official_order = _common_section_lane_order(official, axis, stations, margin_m)
        target_order = _common_section_lane_order(edge_lanes, axis, stations, margin_m)
        if official_order is None or target_order is None:
            continue
        mapping = dict(zip(official_order, target_order))
        errors = [next((error for error, key in options[lane_id] if key == target), math.inf) for lane_id, target in mapping.items()]
        if max(errors) > max_error_m:
            continue
        occupied.update(mapping)
        result.append({"approach": approach, "role": role, "edge_id": edge_id, "bindings": mapping,
                       "basis": "equal_lane_count_and_consistent_common_section_order",
                       "maximum_lane_error_m": round(max(errors), 6)})
    return result


def _common_section_lane_order(shapes, axis, stations, margin_m):
    """Return right-to-left order only when three shared sections agree."""
    normal = np.asarray((-axis[1], axis[0]))
    orders = []
    for station in stations:
        offsets = []
        for lane_id, shape in shapes.items():
            crossings = []
            for a, b in zip(shape, shape[1:]):
                start, end = float(np.dot(a, axis)), float(np.dot(b, axis))
                if abs(end - start) <= 1e-9:
                    continue
                ratio = (station - start) / (end - start)
                if 0 <= ratio <= 1:
                    lateral = float(np.dot(np.asarray(a) + ratio * (np.asarray(b) - a), normal))
                    if not any(abs(lateral - old) < 1e-6 for old in crossings):
                        crossings.append(lateral)
            if len(crossings) != 1:
                return None
            offsets.append((crossings[0], lane_id))
        offsets.sort()
        if any(right[0] - left[0] < margin_m for left, right in zip(offsets, offsets[1:])):
            return None
        orders.append([lane_id for _, lane_id in offsets])
    return orders[0] if all(order == orders[0] for order in orders) else None


def _lane_overlap_error(official: Sequence[Point], candidate: Sequence[Point]) -> float:
    """Mean lateral distance over the entire shared longitudinal interval.

    Reconcile coverage before comparison, not by choosing the best short
    segment. This ordered approach-section fit is not a Frechet distance.
    """
    if len(official) < 2 or len(candidate) < 2:
        return math.inf
    origin = np.asarray(official[0], dtype=float)
    axis = np.asarray(official[-1], dtype=float) - origin
    length = float(np.linalg.norm(axis))
    if not math.isfinite(length) or length <= 1e-9:
        return math.inf
    axis /= length
    normal = np.asarray((-axis[1], axis[0]))
    profiles = [
        np.column_stack(((np.asarray(shape) - origin) @ axis, (np.asarray(shape) - origin) @ normal))
        for shape in (official, candidate)
    ]
    if any(not np.isfinite(profile).all() for profile in profiles):
        return math.inf
    lower = max(float(profile[:, 0].min()) for profile in profiles)
    upper = min(float(profile[:, 0].max()) for profile in profiles)
    if upper - lower <= 1e-6:
        return math.inf
    stations = sorted({lower, upper, *(float(x) for profile in profiles for x in profile[:, 0] if lower < x < upper)})
    differences = []
    for station in stations:
        offsets = [_lane_section_lateral_offset(profile, station) for profile in profiles]
        if any(offset is None for offset in offsets):
            return math.inf
        differences.append(offsets[0] - offsets[1])
    area = 0.0
    for start, end, a, b in zip(stations, stations[1:], differences, differences[1:]):
        # Vertex stations make both offsets linear on each interval. Split
        # at a sign change analytically, so vertex density cannot change fit.
        mean = (abs(a) + abs(b)) / 2 if a * b >= 0 else (a * a + b * b) / (2 * (abs(a) + abs(b)))
        area += (end - start) * mean
    return area / (upper - lower)


def _lane_section_lateral_offset(profile, station):
    """Return one section crossing, never select a branch by proximity."""
    crossings = []
    for a, b in zip(profile, profile[1:]):
        if min(a[0], b[0]) - 1e-9 <= station <= max(a[0], b[0]) + 1e-9:
            if abs(b[0] - a[0]) <= 1e-9:
                crossings.extend((float(a[1]), float(b[1])))
            else:
                ratio = min(1.0, max(0.0, (station - a[0]) / (b[0] - a[0])))
                crossings.append(float(a[1] + ratio * (b[1] - a[1])))
    if not crossings or max(crossings) - min(crossings) > 1e-6:
        return None
    return sum(crossings) / len(crossings)


def _approach_lane_identities(source_root, approach_rebuild, groups):
    constructed_ingress, lane_origins, changed_edge_ids = {}, {}, []
    original_lane_ids = {lane.get("id") for edge in source_root.findall("edge") for lane in edge.findall("lane")}
    for section in approach_rebuild.get("approaches", []):
        key = (str(section["node_id"]), str(section["intersection_part"]))
        edge_id = str(section["downstream_edge_id"])
        changed_edge_ids.append(edge_id)
        for old_index, current_index in section.get("source_lane_to_downstream_lane", {}).items():
            original_id = f"{section['source_edge_id']}_{old_index}"
            lane_origins[f"{edge_id}_{current_index}"] = original_id if original_id in original_lane_ids else None
        for official_id in section["pocket_lane_ids"]:
            lane_origins[f"{edge_id}_{section['official_lane_indices'][official_id]}"] = None
        if any((str(group["node_id"]), str(group["intersection_part"])) == key and str(group["join_id"]) == section["original_to"] for group in groups):
            bound = constructed_ingress.setdefault(key, {})
            for lane_id, index in section["official_lane_indices"].items():
                lane_id, value = str(lane_id), (edge_id, int(index))
                if lane_id in bound and bound[lane_id] != value:
                    raise ValueError("official lane identity refers to multiple rebuilt approaches")
                bound[lane_id] = value
    return constructed_ingress, lane_origins, changed_edge_ids


def _keep_boundary_lane_identity(result, group, available, *, role, lane_origins, changed_edge_ids, maximum_error_m):
    """Resolve only a geometric tie with an unchanged, unique cut-lane identity."""
    ports = [row for row in group.get("boundary_port_classification", {}).get("official_external_ports", []) if row.get("role") == role]
    source_counts = Counter(row.get("source_lane_id") for row in ports)
    official_counts = Counter(str(row["lane_id"]) for row in ports)
    by_official = {str(row["lane_id"]): row for row in ports}
    current_by_source = defaultdict(list)
    for key in available:
        current_id = f"{key[0]}_{key[1]}"
        original_id = lane_origins.get(current_id, current_id if key[0] not in changed_edge_ids else None)
        if original_id is not None:
            current_by_source[original_id].append(key)
    occupied = set(result["bindings"].values())
    preserved, reviews = {}, []
    for record in result["records"]:
        lane_id = str(record["lane_id"])
        if lane_id in result["bindings"] or record["reason"] != "lane_match_ambiguous" or lane_id not in by_official:
            continue
        port = by_official[lane_id]
        source_id = port.get("source_lane_id")
        keys = current_by_source.get(source_id, [])
        evidence = {"lane_id": lane_id, "source_lane_id": source_id, "junction_endpoint_network": port.get("junction_endpoint_network")}
        if not source_id or source_counts[source_id] != 1 or official_counts[lane_id] != 1 or len(keys) != 1:
            reviews.append({**evidence, "reason": "source_boundary_identity_not_unique"})
            continue
        key = keys[0]
        # The current binder already checked direction and vehicle permissions.
        # A known identity does not excuse an incompatible or distant lane.
        if not any((row["edge"], int(row["lane"])) == key and row["mean_error_m"] <= maximum_error_m for row in record["candidates"]):
            reviews.append({**evidence, "reason": "source_boundary_lane_not_a_compatible_geometric_candidate"})
        elif key in occupied:
            reviews.append({**evidence, "reason": "source_boundary_lane_already_assigned"})
        else:
            preserved[lane_id] = key
            occupied.add(key)
            record["source_boundary_identity"] = {**evidence, "sumo_lane": list(key),
                "basis": "explicit_lane_origin" if f"{key[0]}_{key[1]}" in lane_origins else "unchanged_source_lane_identity"}
    if preserved:
        _keep_constructed_lane_identity(result, preserved, available, reason="preserved_source_boundary_lane_identity")
    if reviews:
        result["boundary_identity_reviews"] = reviews


def _keep_constructed_lane_identity(result, constructed, available, *, reason="constructed_official_lane_identity"):
    """Do not infer a second identity for lanes just built from official data."""
    if any(tuple(key) not in available for key in constructed.values()) or len(set(constructed.values())) != len(constructed):
        raise ValueError("constructed lane identity does not match the current approach")
    assigned = set(constructed.values())
    for lane_id, key in list(result["bindings"].items()):
        if lane_id not in constructed and key in assigned:
            result["bindings"].pop(lane_id)
            result["ambiguous"].append({"lane_id": lane_id, "reason": "conflicts_with_constructed_lane_identity"})
    result["bindings"].update(constructed)
    result["ambiguous"] = [row for row in result["ambiguous"] if row["lane_id"] not in constructed]
    for row in result["records"]:
        if row["lane_id"] in constructed:
            row["reason"] = reason
