"""Compose official local movements into complete connections after node joins."""

from __future__ import annotations

import hashlib
import json
import math
import xml.etree.ElementTree as ET
from collections import defaultdict, deque
from collections.abc import Mapping, Sequence
from typing import Any

from .connection_mode_audit import _MOTORIZED_MODES
from .hamburg_official_intersection_plainxml import _project_point_onto_polyline
from .source_movement_support import _candidate_modes, _index, _internal, _source_paths


def _shape(value: str) -> list[tuple[float, float]]:
    points = [tuple(map(float, token.split(",")[:2])) for token in value.split()]
    return [point for index, point in enumerate(points) if index == 0 or math.dist(point, points[index - 1]) > 1e-9]


def _allowed(lane: Mapping[str, Any]) -> set[str]:
    value = lane.get("allowed_vehicle_classes")
    if isinstance(value, str):
        value = value.split()
    return set(_MOTORIZED_MODES) if value is None else set(map(str, value)) & set(_MOTORIZED_MODES)


def _traffic_shape(lane):
    shape = [tuple(point) for point in lane.get("shape_network", [])]
    boundary = lane.get("junction_endpoint_network")
    if len(shape) < 2 or boundary is None:
        return None
    if min(math.dist(shape[0], boundary), math.dist(shape[-1], boundary)) > 0.1:
        return None
    outward = shape if math.dist(shape[0], boundary) < math.dist(shape[-1], boundary) else list(reversed(shape))
    return list(reversed(outward)) if lane.get("direction_role") == "ingress" else outward


def _internal_boundary_anchor(index, source_id, role, boundary, direction, allowed, limit):
    """Locate B on a connected, same-owner native lane, not a nearby road."""
    edge = index["edges"][index["lanes"][source_id][0]]
    owner = edge.get("to" if role == "ingress" else "from")
    choices = []
    for _, start, end, connection in index["movements"]:
        if (start if role == "ingress" else end) != source_id:
            continue
        modes, path = _candidate_modes(index, start, end, connection)
        if not modes.intersection(allowed) or len(path) < 3 or any(index["owners"].get(lane_id) != owner for lane_id in path[1:-1]):
            continue
        _, gap, _ = _native_shape(index, path)
        if gap > 0.1:
            continue
        for lane_id in path[1:-1]:
            shape = _shape(index["lanes"][lane_id][1].get("shape", ""))
            position, error = _project_point_onto_polyline(boundary, shape)
            length = sum(math.dist(a, b) for a, b in zip(shape, shape[1:]))
            if error > limit or not 1e-6 < position < length - 1e-6:
                continue
            station = 0.0
            for a, b in zip(shape, shape[1:]):
                segment_length = math.dist(a, b)
                if station + segment_length >= position:
                    norm = segment_length * math.hypot(*direction)
                    if norm > 1e-9 and sum((b[i] - a[i]) * direction[i] for i in (0, 1)) / norm >= math.cos(math.radians(45)):
                        at = path.index(lane_id)
                        carrier = {"normal_lane_id": source_id, "internal_lane_id": lane_id,
                                   "lane_ids": path[:at + 1] if role == "ingress" else path[at:],
                                   "full_lane_ids": path, "internal_position_m": position,
                                   "allowed_vehicle_classes": sorted(modes.intersection(allowed))}
                        choices.append((error, lane_id, carrier))
                    break
                station += segment_length
    return min(choices, key=lambda row: (row[0], row[1])) if choices else None


def _serial_direction_cosines(pieces):
    segments = [pieces[0][-2:], *(segment for shape in pieces[1:-1] for segment in zip(shape, shape[1:])), pieces[-1][:2]]
    direction = tuple(segments[0][1][i] - segments[0][0][i] for i in (0, 1))
    return [sum((b[i] - a[i]) * direction[i] for i in (0, 1)) / norm if (norm := math.dist(a, b) * math.hypot(*direction)) > 1e-9 else 0.0 for a, b in segments]


def _overlapping_serial_cut_defect(index, path, pieces, cosines):
    """Prove OSM lane identity separately from a backward native cut curve."""
    origins = [index["lanes"][lane_id][1].find("param[@key='origId']") for lane_id in (path[0], path[-1])]
    if any(origin is None or not origin.get("value", "").strip() for origin in origins) or origins[0].get("value") != origins[1].get("value"):
        return None
    if cosines[-1] < math.cos(math.pi / 4) or not cosines[1:-1] or any(value > -math.cos(math.pi / 4) for value in cosines[1:-1]):
        return None
    # A downstream lane starts inside the upstream lane's already occupied
    # interval. Its native internal curve retraces that same strip backwards.
    projections = [_project_point_onto_polyline(point, pieces[0]) for shape in pieces[1:-1] for point in shape]
    last = [_project_point_onto_polyline(point, pieces[0]) for point in pieces[-1][:2]]
    length = sum(math.dist(a, b) for a, b in zip(pieces[0], pieces[0][1:]))
    if max(error for _, error in [*projections, *last]) > 0.1 or not 1e-6 < last[0][0] < length - 0.1 or last[1][0] <= last[0][0] + 1e-6:
        return None
    if any(b[0] > a[0] + 1e-6 for a, b in zip(projections, projections[1:])):
        return None
    return {"reason": "overlapping_source_cut_with_reverse_internal_geometry", "orig_id": origins[0].get("value"),
            "source_lane_path": path, "overlapping_source_interval_m": [last[0][0], length],
            "maximum_strip_error_m": max(error for _, error in [*projections, *last]),
            "requires_reconstructed_geometry": True}


def _source_straight_reversals(index, path):
    defects = []
    normal = [i for i, lane_id in enumerate(path) if not _internal(index["edges"][index["lanes"][lane_id][0]])]
    for first, last in zip(normal, normal[1:]):
        if last - first < 2:
            continue
        start, end = path[first], path[last]
        if not any(connection.get("dir") == "s" and index["keys"].get((connection.get("to"), int(connection.get("toLane", "-1")))) == end for _, connection in index["outgoing"].get(start, [])):
            continue
        section = path[first:last + 1]
        pieces = [_shape(index["lanes"][lane_id][1].get("shape", "")) for lane_id in section]
        if any(len(shape) < 2 for shape in pieces):
            continue
        cosines = _serial_direction_cosines(pieces)
        if cosines[-1] >= math.cos(math.pi / 4) and any(value < 0 for value in cosines[1:-1]):
            defects.append({"source_lane_path": section, "internal_direction_cosines": cosines[1:-1]})
    return defects


def _collapse_serial_lane_candidates(index, choices, members, role):
    """Collapse only explicit one-to-one straight lane cuts inside the join."""
    scores = {lane_id: score for score, lane_id in choices}
    outgoing, incoming = defaultdict(list), defaultdict(list)
    for movement in index["movements"]:
        outgoing[movement[1]].append(movement)
        incoming[movement[2]].append(movement)
    neighbours, paths, defects = defaultdict(set), {}, {}
    for pair, start, end, connection in index["movements"]:
        if start not in scores or end not in scores or connection.get("dir") != "s" or pair[1] != pair[3]:
            continue
        if len(outgoing[start]) != 1 or len(incoming[end]) != 1 or index["counts"][pair[0]] != index["counts"][pair[2]]:
            continue
        owner = index["edges"][pair[0]].get("to")
        modes, path = _candidate_modes(index, start, end, connection)
        if owner not in members or owner != index["edges"][pair[2]].get("from") or len(path) < 3:
            continue
        if not modes or modes != index["lanes"][start][2] or modes != index["lanes"][end][2] or any(index["owners"].get(lane_id) != owner for lane_id in path[1:-1]):
            continue
        _, gap, _ = _native_shape(index, path)
        pieces = [_shape(index["lanes"][lane_id][1].get("shape", "")) for lane_id in path]
        if gap > 0.1:
            continue
        cosines = _serial_direction_cosines(pieces)
        if any(value < math.cos(math.pi / 4) for value in cosines):
            defect = _overlapping_serial_cut_defect(index, path, pieces, cosines)
            if defect is None:
                continue
            defects[(start, end)] = defect
        neighbours[start].add(end)
        neighbours[end].add(start)
        paths[(start, end)] = path
    remaining, collapsed, proofs = set(scores), [], {}
    while remaining:
        component, pending = set(), [min(remaining)]
        while pending:
            current = pending.pop()
            if current not in component:
                component.add(current)
                pending.extend(neighbours[current] - component)
        remaining -= component
        witnesses = [path for (start, end), path in paths.items() if start in component and end in component]
        cuts = [lane_id for lane_id in component if index["edges"][index["lanes"][lane_id][0]].get("from" if role == "ingress" else "to") not in members]
        if len(cuts) > 1 or len(witnesses) >= len(component):
            collapsed.extend((scores[lane_id], lane_id) for lane_id in component)
            continue
        representative = cuts[0] if cuts else min(component, key=lambda lane_id: (scores[lane_id], lane_id))
        collapsed.append((min(scores[lane_id] for lane_id in component), representative))
        if len(component) > 1:
            proofs[representative] = {"reason": "same_mode_one_to_one_serial_lane_cut",
                                      "equivalent_segment_lane_ids": sorted(component), "serial_source_lane_paths": sorted(witnesses),
                                      "equivalent_segment_candidates": sorted((scores[lane_id], lane_id) for lane_id in component)}
            proofs[representative]["source_geometry_defects"] = [defect for (start, end), defect in defects.items() if start in component and end in component]
    return sorted(collapsed), proofs


def _original_lane_bindings(index, lanes, members, maximum_error, margin):
    from .hamburg_junctions.lanes import _lane_overlap_error

    bindings, reviews = {}, []
    for lane_id, lane in lanes.items():
        role = lane.get("direction_role")
        shape = _traffic_shape(lane)
        if role not in {"ingress", "egress"} or shape is None:
            continue
        boundary = shape[-1] if role == "ingress" else shape[0]
        a, b = shape[-2:] if role == "ingress" else shape[:2]
        dx, dy = b[0] - a[0], b[1] - a[1]
        choices = []
        anchor_proofs = {}
        for source_id, (edge_id, source_lane, modes) in index["lanes"].items():
            edge = index["edges"][edge_id]
            if _internal(edge) or not modes.intersection(_allowed(lane)) or edge.get("to" if role == "ingress" else "from") not in members:
                continue
            candidate = _shape(source_lane.get("shape", ""))
            if len(candidate) < 2:
                continue
            first, last = candidate[-2:] if role == "ingress" else candidate[:2]
            vx, vy = last[0] - first[0], last[1] - first[1]
            norm = math.hypot(dx, dy) * math.hypot(vx, vy)
            endpoint_error = math.dist(boundary, candidate[-1] if role == "ingress" else candidate[0])
            if norm <= 1e-9 or (dx * vx + dy * vy) / norm < math.cos(math.radians(45)):
                continue
            lateral_error = _lane_overlap_error(shape, candidate)
            if not math.isfinite(lateral_error) or lateral_error > maximum_error:
                continue
            # Compare every candidate to the same physical B section. Using a
            # lane endpoint for one and an internal projection for another
            # would favor the wrong lane merely because its edge was shorter.
            anchor_error, proof = endpoint_error, None
            position, error = _project_point_onto_polyline(boundary, candidate)
            length = sum(math.dist(a, b) for a, b in zip(candidate, candidate[1:]))
            if error < anchor_error and 1e-6 < position < length - 1e-6:
                anchor_error = error
                proof = {"reason": "official_boundary_on_source_lane", "source_lane_position_m": position}
            if anchor_error > 0.1:
                internal = _internal_boundary_anchor(index, source_id, role, boundary, (dx, dy), _allowed(lane), maximum_error)
                if internal is not None and internal[0] < anchor_error:
                    anchor_error, internal_id, carrier = internal
                    proof = {"reason": "official_boundary_on_source_internal_lane", "source_internal_lane_id": internal_id,
                             "source_boundary_carrier": carrier}
            if anchor_error > maximum_error:
                continue
            if proof is not None:
                anchor_proofs[source_id] = {**proof, "source_endpoint_distance_m": endpoint_error, "boundary_projection_error_m": anchor_error}
            choices.append((lateral_error + anchor_error, source_id))
        choices, serial_proofs = _collapse_serial_lane_candidates(index, choices, members, role)
        if choices and (len(choices) == 1 or choices[1][0] - choices[0][0] >= margin):
            bindings[lane_id] = choices[0][1]
            if choices[0][1] in serial_proofs:
                reviews.append({"lane_id": lane_id, "resolved_lane_id": choices[0][1], "candidates": [], **serial_proofs[choices[0][1]]})
            elif choices[0][1] in anchor_proofs:
                reviews.append({"lane_id": lane_id, "resolved_lane_id": choices[0][1], "candidates": [], **anchor_proofs[choices[0][1]]})
        else:
            reviews.append({"lane_id": lane_id, "reason": "original_lane_binding_unresolved", "candidates": choices})
            if serial_proofs:
                reviews[-1]["candidate_identity_proofs"] = serial_proofs
    return bindings, reviews


def _resolve_original_context(index, lanes, movements, bindings, reviews, members, margin):
    pending = [row for row in reviews if not row.get("resolved_lane_id")]
    while pending:
        changed = False
        remaining = []
        for review in pending:
            lane_id = review["lane_id"]
            supported = []
            best_score = min((score for score, _ in review["candidates"]), default=math.inf)
            for score, candidate in review["candidates"]:
                if score - best_score >= margin:
                    continue
                proofs = []
                for movement in movements:
                    source, target = str(movement["ingress_lane_id"]), str(movement["egress_lane_id"])
                    start = candidate if source == lane_id else bindings.get(source)
                    end = candidate if target == lane_id else bindings.get(target)
                    if lane_id not in {source, target} or start is None or end is None:
                        continue
                    modes = _allowed(lanes[source]) & _allowed(lanes[target]) & _allowed(movement)
                    proofs.extend(_source_paths(index, start, end, members, modes))
                if proofs:
                    supported.append((score, candidate, proofs))
            supported.sort()
            if supported and (len(supported) == 1 or supported[1][0] - supported[0][0] >= margin):
                bindings[lane_id] = supported[0][1]
                review.update(reason="unique_original_movement_context", resolved_lane_id=supported[0][1], source_paths=supported[0][2])
                identity = review.get("candidate_identity_proofs", {}).get(supported[0][1])
                if identity:
                    review.update(source_identity_proof=identity, source_geometry_defects=identity["source_geometry_defects"])
                changed = True
            else:
                remaining.append(review)
        if not changed:
            break
        pending = remaining


def _official_geometry(plan, root, points):
    from pathlib import Path
    from pyproj import Transformer
    from .hamburg_map_kml import parse_hamburg_map_kml
    from .hamburg_junctions.geometry import fit_movement_shape_to_anchors

    artifact = plan.get("inputs", {}).get("map_kml")
    if not artifact:
        return {}, {}
    geometry = parse_hamburg_map_kml(Path(artifact["path"]), expected_sha256=str(artifact["sha256"]))
    location = root.find("location")
    transformer = Transformer.from_crs("EPSG:4326", location.attrib["projParameter"], always_xy=True)
    offset = tuple(map(float, location.get("netOffset", "0,0").split(",")))
    shapes, adjustments = {}, {}
    for row in geometry["drive_lines"]:
        key = (str(row["from_lane_id"]), str(row["to_lane_id"]))
        if any(lane_id not in points for lane_id in key):
            continue
        raw = [tuple(value + offset[i] for i, value in enumerate(transformer.transform(*point[:2], errcheck=True))) for point in row["coordinates"]]
        fitted, error = fit_movement_shape_to_anchors(raw, start=points[key[0]], end=points[key[1]])
        if error <= float(plan.get("max_error_m", 3.0)) and sum(math.dist(a, b) for a, b in zip(fitted, fitted[1:])) > 0.1:
            shapes[key], adjustments[key] = fitted, error
    return shapes, adjustments


def _continuations(index, lanes, members, source_bindings, maximum_gap, margin):
    outgoing = {key: _traffic_shape(lane) for key, lane in lanes.items() if lane.get("direction_role") == "egress"}
    incoming = {key: _traffic_shape(lane) for key, lane in lanes.items() if lane.get("direction_role") == "ingress"}
    accepted, reviews = [], []
    for source, left in outgoing.items():
        if left is None:
            continue
        choices = []
        for target, right in incoming.items():
            if right is None:
                continue
            gap = math.dist(left[-1], right[0])
            a = (left[-1][0] - left[-2][0], left[-1][1] - left[-2][1])
            b = (right[1][0] - right[0][0], right[1][1] - right[0][1])
            norm = math.hypot(*a) * math.hypot(*b)
            if gap > maximum_gap or norm <= 1e-9 or sum(x * y for x, y in zip(a, b)) / norm < math.cos(math.radians(45)):
                continue
            record = {"from_egress_lane_id": source, "to_ingress_lane_id": target, "endpoint_gap_m": gap}
            first, last = source_bindings.get(source), source_bindings.get(target)
            if first is None or last is None:
                reviews.append({**record, "reason": "original_lane_identity_missing"})
                continue
            if first == last:
                source_shape = _shape(index["lanes"][first][1].get("shape", ""))
                if _project_point_onto_polyline(right[-1], source_shape)[0] - _project_point_onto_polyline(left[0], source_shape)[0] <= 0.1:
                    reviews.append({**record, "reason": "same_lane_progress_unproved"})
                    continue
            modes = _allowed(lanes[source]) & _allowed(lanes[target])
            witnesses = _source_paths(index, first, last, members, modes)
            if not witnesses:
                reviews.append({**record, "reason": "no_original_lane_continuation", "original_from_lane": first, "original_to_lane": last})
                continue
            choices.append({**record, "source_lane_paths": witnesses, "allowed_vehicle_classes": sorted({mode for witness in witnesses for mode in witness["vehicle_classes"]}), "shape_network": _join_shapes([left, right]), "basis": "unique_aligned_official_A_endpoints_and_original_lane_path"})
        choices.sort(key=lambda row: (row["endpoint_gap_m"], row["to_ingress_lane_id"]))
        if choices and (len(choices) == 1 or choices[1]["endpoint_gap_m"] - choices[0]["endpoint_gap_m"] >= margin):
            accepted.append(choices[0])
        elif choices:
            reviews.extend({**row, "reason": "ambiguous_official_A_continuation"} for row in choices)
    return accepted, reviews


def _join_shapes(shapes):
    result = []
    for shape in shapes:
        for point in shape:
            value = tuple(point)
            if not result or math.dist(result[-1], value) > 1e-9:
                result.append(value)
    return result


def _walks(start, graph, ends, required_movement):
    initial_modes = frozenset(_MOTORIZED_MODES)
    queue = deque([(start, False, initial_modes, [start], [])])
    visited = {(start, False, initial_modes)}
    while queue:
        lane, seen, modes, lane_path, arcs = queue.popleft()
        if lane in ends:
            if seen:
                yield lane, lane_path, arcs
            continue
        for arc in graph.get(lane, []):
            reached = seen or arc.get("movement_token") == required_movement
            permitted = modes.intersection(arc["allowed_vehicle_classes"])
            state = (arc["target"], reached, permitted)
            if not permitted or state in visited or arc["target"] in lane_path:
                continue
            visited.add(state)
            queue.append((arc["target"], reached, permitted, [*lane_path, arc["target"]], [*arcs, arc]))


def _samples(shape, spacing=1.0):
    if shape:
        yield shape[0]
    for a, b in zip(shape, shape[1:]):
        count = max(1, math.ceil(math.dist(a, b) / spacing))
        for index in range(1, count + 1):
            yield (a[0] + (b[0] - a[0]) * index / count, a[1] + (b[1] - a[1]) * index / count)


def _native_shape(index, lane_path):
    if len(lane_path) < 2:
        return [], math.inf, []
    pieces = [_shape(index["lanes"][lane_id][1].get("shape", "")) for lane_id in lane_path]
    if any(len(shape) < 2 for shape in pieces):
        return [], math.inf, []
    gaps = [math.dist(a[-1], b[0]) for a, b in zip(pieces, pieces[1:])]
    ranges = []
    station = 0.0
    for index, (lane_id, shape) in enumerate(zip(lane_path, pieces)):
        if index:
            station += gaps[index - 1]
        end = station + sum(math.dist(a, b) for a, b in zip(shape, shape[1:]))
        ranges.append({"lane_id": lane_id, "path_arc_start_m": station, "path_arc_end_m": end})
        station = end
    return _join_shapes(pieces), max(gaps, default=0.0), ranges


def _slice_shape(shape, first, last):
    result = []
    station = 0.0
    for a, b in zip(shape, shape[1:]):
        length = math.dist(a, b)
        if length > 1e-9 and station <= last and station + length >= first:
            for position in (max(first, station), min(last, station + length)):
                t = (position - station) / length
                result.append((a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t))
        station += length
    return _join_shapes([result])


def _clip_witness_shapes(pieces, *, start, end):
    """Clip only the first/last witnessed lane, never an earlier nearby lane."""
    if not pieces or any(len(shape) < 2 for shape in pieces):
        return [], math.inf, math.inf
    first, first_error = _project_point_onto_polyline(start, pieces[0])
    last, last_error = _project_point_onto_polyline(end, pieces[-1])
    if math.dist(start, pieces[0][0]) <= 1e-9:
        first, first_error = 0.0, 0.0
    if math.dist(end, pieces[-1][-1]) <= 1e-9:
        last = sum(math.dist(a, b) for a, b in zip(pieces[-1], pieces[-1][1:]))
        last_error = 0.0
    gaps = [math.dist(left[-1], right[0]) for left, right in zip(pieces, pieces[1:])]
    if len(pieces) == 1:
        clipped = _slice_shape(pieces[0], first, last) if last > first else []
    else:
        first_length = sum(math.dist(a, b) for a, b in zip(pieces[0], pieces[0][1:]))
        clipped = _join_shapes([_slice_shape(pieces[0], first, first_length), *pieces[1:-1], _slice_shape(pieces[-1], 0.0, last)])
    if len(clipped) < 2:
        return [], math.inf, max(gaps, default=0.0)
    clipped[0], clipped[-1] = tuple(start), tuple(end)
    return _join_shapes([clipped]), first_error + last_error, max(gaps, default=0.0)


def _source_point_transform(source_root, candidate_root):
    source, candidate = source_root.find("location"), candidate_root.find("location")
    if source is None and candidate is None:
        return lambda point: tuple(point)
    if source is None or candidate is None:
        raise ValueError("Source and candidate must both declare their coordinate system")
    before = tuple(map(float, source.get("netOffset", "0,0").split(",")))
    after = tuple(map(float, candidate.get("netOffset", "0,0").split(",")))
    source_crs, candidate_crs = source.get("projParameter"), candidate.get("projParameter")
    if source_crs == candidate_crs:
        return lambda point: tuple(point[i] - before[i] + after[i] for i in (0, 1))
    from pyproj import Transformer

    transformer = Transformer.from_crs(source_crs, candidate_crs, always_xy=True)
    return lambda point: tuple(value + after[i] for i, value in enumerate(transformer.transform(point[0] - before[0], point[1] - before[1], errcheck=True)))


def _carried_source_sections(pieces, boundary, *, reverse, limit):
    """Match the boundary along the ordered source chain, with no loop jumps."""
    shapes = [list(reversed(shape)) for shape in reversed(pieces)] if reverse else pieces
    boundary = list(reversed(boundary)) if reverse else boundary
    lengths = [sum(math.dist(a, b) for a, b in zip(shape, shape[1:])) for shape in shapes]
    offsets = [0.0]
    for index in range(1, len(shapes)):
        offsets.append(offsets[-1] + lengths[index - 1] + math.dist(shapes[index - 1][-1], shapes[index][0]))
    joined = _join_shapes(shapes)
    previous_station, previous_point, start_station = None, None, None
    lane_index, maximum_error = 0, 0.0
    for point in _samples(boundary):
        found = None
        for index in range(lane_index, len(shapes)):
            position, error = _project_point_onto_polyline(point, shapes[index])
            station = offsets[index] + position
            if error > limit or previous_station is not None and station < previous_station - 1e-6:
                continue
            if previous_station is not None and station > previous_station:
                advanced = _slice_shape(joined, previous_station, station)
                error = max(error, max((_project_point_onto_polyline(p, [previous_point, point])[1] for p in _samples(advanced)), default=0.0))
                if error > limit:
                    continue
            found = index, position, station, error
            break  # Earliest ordered lane, not the closest point anywhere ahead.
        if found is None:
            return None
        lane_index, position, station, error = found
        maximum_error = max(maximum_error, error)
        if start_station is None:
            start_station = station
        previous_station, previous_point = station, point
    if previous_station is None or previous_station <= start_station + 1e-6:
        return None
    carried, before = [], []
    for index, (offset, length) in enumerate(zip(offsets, lengths)):
        original_index = len(shapes) - index - 1 if reverse else index
        for first, last, rows in ((max(offset, start_station), min(offset + length, previous_station), carried), (offset, min(offset + length, start_station), before)):
            if last > first + 1e-6:
                a, b = first - offset, last - offset
                rows.append({"lane_index": original_index, "start_position_m": length - b if reverse else a, "end_position_m": length - a if reverse else b})
    remainder = [_slice_shape(shapes[lane_index], position, lengths[lane_index]), *shapes[lane_index + 1:]]
    indices = list(range(lane_index, len(shapes)))
    kept = [(index, shape) for index, shape in zip(indices, remainder) if len(shape) >= 2 and sum(math.dist(a, b) for a, b in zip(shape, shape[1:])) > 1e-6]
    if reverse:
        kept = [(len(shapes) - index - 1, list(reversed(shape))) for index, shape in reversed(kept)]
    return {"pieces": [shape for _, shape in kept], "lane_indices": [index for index, _ in kept], "carried": carried, "before": before, "maximum_error_m": maximum_error}


def _source_boundary_extensions(original, candidate, *, lanes, source_lanes, source_bindings, members,
                                bindings, boundary_candidates, links, aliases, transform,
                                geometry_limit, anchor_limit, source_binding_reviews=()):
    from .hamburg_junctions.lanes import _lane_overlap_error

    extensions, reviews = [], []
    carriers = {row["lane_id"]: row["source_boundary_carrier"] for row in source_binding_reviews if row.get("source_boundary_carrier")}
    connected = {"ingress": {row["to_ingress_lane_id"] for row in links}, "egress": {row["from_egress_lane_id"] for row in links}}
    for official_id, lane in lanes.items():
        role = lane.get("direction_role")
        if role not in connected or official_id in bindings[role] or official_id in connected[role]:
            continue
        old_lane = source_bindings.get(official_id)
        if old_lane is None or lane.get("junction_endpoint_network") is None or lane.get("permission_status") == "review_required":
            continue
        old_edge = original["edges"][original["lanes"][old_lane][0]]
        if old_edge.get("from") not in members or old_edge.get("to") not in members:
            continue  # A missing exterior lane is not a swallowed interior port.
        carrier = carriers.get(official_id)
        identity_path = [old_lane]
        identity_pieces = [_shape(original["lanes"][old_lane][1].get("shape", ""))]
        if carrier:
            traffic = _traffic_shape(source_lanes[official_id])
            if traffic is None:
                continue
            a, b = traffic[-2:] if role == "ingress" else traffic[:2]
            boundary = traffic[-1] if role == "ingress" else traffic[0]
            verified = _internal_boundary_anchor(original, old_lane, role, boundary, (b[0] - a[0], b[1] - a[1]), _allowed(lane), anchor_limit)
            if verified is None or verified[2] != carrier:
                reviews.append({"official_lane_id": official_id, "role": role, "reason": "bound_source_boundary_carrier_unproved", "original_lane_id": old_lane})
                continue
            defects = _source_straight_reversals(original, carrier["full_lane_ids"])
            if defects:
                reviews.append({"official_lane_id": official_id, "role": role, "reason": "source_internal_geometry_reverses_a_straight_continuation",
                                "original_lane_path": carrier["full_lane_ids"], "source_geometry_defects": defects})
                continue
            identity_path = carrier["lane_ids"]
            identity_pieces = [_shape(original["lanes"][lane_id][1].get("shape", "")) for lane_id in identity_path]
            at = -1 if role == "ingress" else 0
            length = sum(math.dist(a, b) for a, b in zip(identity_pieces[at], identity_pieces[at][1:]))
            first, last = (0.0, carrier["internal_position_m"]) if role == "ingress" else (carrier["internal_position_m"], length)
            identity_pieces[at] = _slice_shape(identity_pieces[at], first, last)
        identity_error = _lane_overlap_error(source_lanes[official_id]["shape_network"], _join_shapes(identity_pieces))
        if not math.isfinite(identity_error) or identity_error > geometry_limit:
            reviews.append({"official_lane_id": official_id, "role": role, "reason": "source_lane_geometry_identity_unproved", "original_lane_id": old_lane,
                            "source_geometry_lane_path": identity_path, "source_boundary_carrier": carrier,
                            "source_lane_geometry_error_m": identity_error if math.isfinite(identity_error) else None})
            continue
        accepted = False
        for pair, shape in boundary_candidates[role].items():
            current_lane = candidate["keys"][pair]
            previous = aliases.get(current_lane, current_lane if current_lane in original["lanes"] else None)
            if previous is None:
                continue
            boundary_edge = original["edges"][original["lanes"][previous][0]]
            if boundary_edge.get("to" if role == "ingress" else "from") not in members:
                continue
            start, end = (previous, old_lane) if role == "ingress" else (old_lane, previous)
            permitted = candidate["lanes"][current_lane][2] & _allowed(lane)
            if carrier:
                permitted &= set(carrier["allowed_vehicle_classes"])
            for proof in _source_paths(original, start, end, members, permitted):
                defects = _source_straight_reversals(original, proof["lane_ids"])
                if defects:
                    reviews.append({"official_lane_id": official_id, "role": role, "reason": "source_internal_geometry_reverses_a_straight_continuation",
                                    "original_lane_path": proof["lane_ids"], "source_geometry_defects": defects})
                    continue
                source_path = proof["lane_ids"]
                pieces = [_shape(original["lanes"][lane_id][1].get("shape", "")) for lane_id in source_path]
                if carrier:
                    source_path = [*source_path[:-1], *identity_path] if role == "ingress" else [*identity_path, *source_path[1:]]
                    pieces = [*pieces[:-1], *identity_pieces] if role == "ingress" else [*identity_pieces, *pieces[1:]]
                pieces = [[transform(point) for point in shape] for shape in pieces]
                first, last = (shape[-1], lane["junction_endpoint_network"]) if role == "ingress" else (lane["junction_endpoint_network"], shape[0])
                carried = _carried_source_sections(pieces, shape, reverse=role == "egress", limit=geometry_limit)
                if carried is not None and carried["pieces"]:
                    curve_pieces, curve_indices = carried["pieces"], carried["lane_indices"]
                else:
                    curve_pieces, curve_indices = pieces, list(range(len(pieces)))
                    port_segment = pieces[0][-2:] if role == "ingress" else pieces[-1][:2]
                    endpoint = port_segment[-1] if role == "ingress" else port_segment[0]
                    anchor = first if role == "ingress" else last
                    along = sum((anchor[i] - endpoint[i]) * (port_segment[1][i] - port_segment[0][i]) for i in (0, 1))
                    if (along if role == "ingress" else -along) > 1e-6:
                        reviews.append({"official_lane_id": official_id, "role": role, "reason": "boundary_extension_coverage_unproved", "original_lane_path": proof["lane_ids"]})
                        continue
                curve, error, _ = _clip_witness_shapes(curve_pieces, start=first, end=last)
                gap = max((math.dist(a[-1], b[0]) for a, b in zip(pieces, pieces[1:])), default=0.0)
                if not curve or error > anchor_limit or gap > 0.1:
                    reviews.append({"official_lane_id": official_id, "role": role, "reason": "source_boundary_geometry_unproved", "original_lane_path": proof["lane_ids"], "geometry_anchor_error_m": error if math.isfinite(error) else None, "source_chain_gap_m": gap if math.isfinite(gap) else None})
                    continue
                port = f"@source-{role}:{current_lane}"
                bindings[role][port] = pair
                extensions.append({"kind": "source_prefix" if role == "ingress" else "source_suffix", "source": port if role == "ingress" else official_id, "target": official_id if role == "ingress" else port, "official_lane_id": official_id, "boundary_lane_id": current_lane, "original_lane_path": source_path, "allowed_vehicle_classes": proof["vehicle_classes"], "shape_network": curve, "geometry_anchor_error_m": error, "source_chain_gap_m": gap, "source_lane_geometry_error_m": identity_error, "source_boundary_carrier": carrier, "basis": "source_derived_inside_joined_members_not_an_official_movement"})
                extensions[-1].update(
                    curve_original_lane_path=[source_path[index] for index in curve_indices],
                    carried_by_boundary=[{**row, "original_lane_id": source_path[row["lane_index"]]} for row in (carried or {}).get("carried", [])],
                    source_before_boundary_shape=[{**row, "original_lane_id": source_path[row["lane_index"]]} for row in (carried or {}).get("before", [])],
                    maximum_boundary_coverage_error_m=(carried or {}).get("maximum_error_m"),
                )
                accepted = True
        if not accepted:
            reviews.append({"official_lane_id": official_id, "role": role, "reason": "no_verified_source_boundary_extension", "original_lane_id": old_lane})
    return extensions, reviews


def compose_official_movements(
    original_root: ET.Element,
    candidate_root: ET.Element,
    *,
    plans: Mapping[str, Mapping[str, Any]],
    groups: Sequence[Mapping[str, Any]],
    boundary_bindings: Mapping[str, Mapping[str, Mapping[str, Sequence[Any]]]] | None = None,
    current_lane_to_original_lane: Mapping[str, str | None] | None = None,
    maximum_lane_error_m: float = 10.0,
    minimum_match_margin_m: float = 0.5,
    maximum_continuation_gap_m: float = 1.0,
    maximum_source_anchor_error_m: float = 10.0,
) -> dict[str, Any]:
    """Return proposed boundary pairs and separately verified spatial coverage.

    ``actual_allowed_vehicle_classes`` is the compatible intersection for one
    official path. ``candidate_allowed_vehicle_classes`` is the full SUMO set.
    Excess permissions are checked against all official paths for the same pair.
    """
    from .hamburg_junctions.lanes import _bind_official_lanes
    from .hamburg_junctions.source import _project_plan_to_network
    from .hamburg_junctions.geometry import fit_movement_shape_to_anchors

    if any(not math.isfinite(value) or value <= 0 for value in (maximum_lane_error_m, minimum_match_margin_m, maximum_continuation_gap_m, maximum_source_anchor_error_m)):
        raise ValueError("composition limits must be finite and positive")
    original, candidate = _index(original_root), _index(candidate_root)
    actual = {pair: (source, target, connection) for pair, source, target, connection in candidate["movements"]}
    aliases = dict(current_lane_to_original_lane or {})
    for current, previous in aliases.items():
        if current not in candidate["lanes"] or previous is not None and previous not in original["lanes"]:
            raise ValueError("lane alias references an unknown candidate or original lane")
    boundary_paths, coverage, continuations, unresolved_continuations, reviews = [], [], [], [], []
    source_extensions, source_extension_reviews = [], []
    required_pairs = set()
    for group in groups:
        node_id, part, join_id = str(group["node_id"]), str(group["intersection_part"]), str(group["join_id"])
        raw = plans[node_id]
        projected = bool(raw.get("crs") and all("shape_epsg25832" in lane for lane in raw.get("lanes", [])))
        source_plan = _project_plan_to_network(raw, original_root) if projected else raw
        plan = _project_plan_to_network(raw, candidate_root) if projected else raw
        movements = [row for row in plan["movements"] if str(row.get("intersection_part", "0")) == part]
        used_lanes = {str(row[key]) for row in movements for key in ("ingress_lane_id", "egress_lane_id")}
        lanes = {str(row["lane_id"]): row for row in plan["lanes"] if str(row["lane_id"]) in used_lanes}
        source_lanes = {str(row["lane_id"]): row for row in source_plan["lanes"] if str(row["lane_id"]) in used_lanes}
        members = set(map(str, group["source_node_ids"]))
        source_bindings, source_reviews = _original_lane_bindings(original, source_lanes, members, maximum_lane_error_m, minimum_match_margin_m)
        _resolve_original_context(original, source_lanes, movements, source_bindings, source_reviews, members, minimum_match_margin_m)
        reviews.extend({"node_id": node_id, "intersection_part": part, **row} for row in source_reviews)
        links, link_reviews = _continuations(original, source_lanes, members, source_bindings, maximum_continuation_gap_m, minimum_match_margin_m)
        for link in links:
            # Rebuild its geometry in the candidate coordinate frame.
            link["shape_network"] = _join_shapes([_traffic_shape(lanes[link["from_egress_lane_id"]]), _traffic_shape(lanes[link["to_ingress_lane_id"]])])
        continuations.extend({"node_id": node_id, "intersection_part": part, **row} for row in links)
        unresolved_continuations.extend({"node_id": node_id, "intersection_part": part, **row} for row in link_reviews)
        bindings, boundary_candidates = {}, {}
        for role in ("ingress", "egress"):
            candidates = {key: _shape(candidate["lanes"][lane_id][1].get("shape", "")) for key, lane_id in candidate["keys"].items() if not _internal(candidate["edges"][key[0]]) and candidate["edges"][key[0]].get("to" if role == "ingress" else "from") == join_id and candidate["lanes"][lane_id][2]}
            boundary_candidates[role] = candidates
            supplied = (boundary_bindings or {}).get(join_id, {}).get(role)
            if supplied is not None:
                bindings[role] = {str(key): (str(value[0]), int(value[1])) for key, value in supplied.items() if (str(value[0]), int(value[1])) in candidates}
            else:
                modes = {key: candidate["lanes"][candidate["keys"][key]][2] for key in candidates}
                internal = [row["lane_id"] for row in group.get("boundary_port_classification", {}).get("official_internal_anchors", []) if row["role"] == role]
                bindings[role] = _bind_official_lanes(plan["lanes"], movements, candidates, role=role, max_error_m=maximum_lane_error_m, margin_m=minimum_match_margin_m, candidate_modes=modes, internal_lane_ids=internal)["bindings"]
        points = {key: lane["junction_endpoint_network"] for key, lane in lanes.items() if lane.get("junction_endpoint_network") is not None}
        official_geometry, geometry_adjustments = _official_geometry(plan, candidate_root, points)
        graph = defaultdict(list)
        tokens = []
        for index, row in enumerate(movements):
            source, target = str(row["ingress_lane_id"]), str(row["egress_lane_id"])
            token = f"{node_id}/{part}/{index}"
            tokens.append(token)
            shape = row.get("official_shape_network") or official_geometry.get((source, target))
            graph[source].append({"kind": "movement", "source": source, "target": target, "movement_token": token, "movement_id": str(row["movement_id"]), "shape_network": shape, "official_B_adjustment_m": geometry_adjustments.get((source, target), 0.0), "allowed_vehicle_classes": sorted(_allowed(lanes[source]) & _allowed(lanes[target]) & _allowed(row))})
        for row in links:
            graph[row["from_egress_lane_id"]].append({"kind": "continuation", "source": row["from_egress_lane_id"], "target": row["to_ingress_lane_id"], **row})
        paths_by_token = defaultdict(list)
        seen_paths = set()
        geometry_limit = float(plan.get("max_error_m", 3.0))
        if not math.isfinite(geometry_limit) or geometry_limit <= 0:
            raise ValueError("plan max_error_m must be finite and positive")
        extensions, extension_reviews = _source_boundary_extensions(
            original, candidate, lanes=lanes, source_lanes=source_lanes, source_bindings=source_bindings,
            members=members, bindings=bindings, boundary_candidates=boundary_candidates, links=links,
            aliases=aliases, transform=_source_point_transform(original_root, candidate_root),
            geometry_limit=geometry_limit, anchor_limit=maximum_source_anchor_error_m,
            source_binding_reviews=source_reviews,
        )
        for arc in extensions:
            graph[arc["source"]].append(arc)
        source_extensions.extend({"node_id": node_id, "intersection_part": part, **row} for row in extensions)
        source_extension_reviews.extend({"node_id": node_id, "intersection_part": part, **row} for row in extension_reviews)
        for token in tokens:
            for start in sorted(bindings["ingress"]):
                for end, lane_sequence, arcs in _walks(start, graph, set(bindings["egress"]), token):
                    signature = (start, end, tuple(arc.get("movement_token", "C:" + arc["source"] + ":" + arc["target"]) for arc in arcs))
                    if signature in seen_paths:
                        continue
                    seen_paths.add(signature)
                    pair = (*bindings["ingress"][start], *bindings["egress"][end])
                    permitted = set(_MOTORIZED_MODES)
                    for arc in arcs:
                        permitted &= set(arc["allowed_vehicle_classes"])
                    permitted &= candidate["lanes"][candidate["keys"][pair[:2]]][2]
                    permitted &= candidate["lanes"][candidate["keys"][pair[2:]]][2]
                    if not permitted:
                        continue
                    required_pairs.add(pair)
                    witness_id = f"official-composition-{len(boundary_paths):05d}"
                    found = actual.get(pair)
                    modes, lane_path = _candidate_modes(candidate, *found) if found is not None else (set(), [])
                    actual_permitted = permitted & modes
                    full_native, max_gap, lane_ranges = _native_shape(candidate, lane_path)
                    complete_geometry = all(arc.get("shape_network") for arc in arcs)
                    composed = _join_shapes([arc["shape_network"] for arc in arcs]) if complete_geometry else None
                    derived = [arc for arc in arcs if arc["kind"] in {"source_prefix", "source_suffix"}]
                    native = full_native
                    connection_shape, connection_error = None, None
                    if composed:
                        source_shape = _shape(candidate["lanes"][candidate["keys"][pair[:2]]][1].get("shape", ""))
                        target_shape = _shape(candidate["lanes"][candidate["keys"][pair[2:]]][1].get("shape", ""))
                        if derived:
                            connection_shape, connection_error, _ = _clip_witness_shapes([arc["shape_network"] for arc in arcs], start=source_shape[-1], end=target_shape[0])
                        else:
                            connection_shape, connection_error = fit_movement_shape_to_anchors(composed, start=source_shape[-1], end=target_shape[0])
                        if len(full_native) >= 2:
                            first = _project_point_onto_polyline(composed[0], full_native)[0]
                            last = _project_point_onto_polyline(composed[-1], full_native)[0]
                            native = _slice_shape(full_native, first, last) if last > first else []
                    segments = []
                    previous_end = -math.inf
                    spatial_ok = bool(found and actual_permitted and composed and len(native) >= 2 and max_gap <= 0.1)
                    max_offset = None
                    if spatial_ok:
                        spacing = min(1.0, geometry_limit / 2)
                        max_offset = max(max(_project_point_onto_polyline(point, native)[1] for point in _samples(composed, spacing)), max(_project_point_onto_polyline(point, composed)[1] for point in _samples(native, spacing)))
                        spatial_ok = max_offset <= geometry_limit
                    for arc in arcs:
                        if arc["kind"] != "movement":
                            continue
                        subpath = arc.get("shape_network")
                        arc_start, start_error = _project_point_onto_polyline(subpath[0], full_native) if subpath and len(full_native) >= 2 else (0.0, math.inf)
                        arc_end, end_error = _project_point_onto_polyline(subpath[-1], full_native) if subpath and len(full_native) >= 2 else (0.0, math.inf)
                        if arc_start < previous_end - 0.1 or arc_end <= arc_start or max(start_error, end_error) > geometry_limit:
                            spatial_ok = False
                        previous_end = arc_end
                        segments.append({"movement_token": arc["movement_token"], "movement_id": arc["movement_id"], "official_lane_pair": [arc["source"], arc["target"]], "official_shape_network": subpath, "official_B_adjustment_m": arc["official_B_adjustment_m"], "candidate_arc_start_m": arc_start, "candidate_arc_end_m": arc_end, "candidate_lane_ids": [item["lane_id"] for item in lane_ranges if item["path_arc_end_m"] > arc_start and item["path_arc_start_m"] < arc_end]})
                        paths_by_token[arc["movement_token"]].append(len(boundary_paths))
                    old_lane_path = []
                    original_mode = None
                    candidate_first, candidate_last = candidate["keys"][pair[:2]], candidate["keys"][pair[2:]]
                    first = aliases.get(candidate_first, candidate_first if candidate_first in original["lanes"] else None)
                    last = aliases.get(candidate_last, candidate_last if candidate_last in original["lanes"] else None)
                    if first is not None and last is not None:
                        anchors = [first]
                        for arc in arcs:
                            if arc["kind"] == "continuation":
                                anchors.extend((source_bindings[arc["source"]], source_bindings[arc["target"]]))
                            elif arc["kind"] in {"source_prefix", "source_suffix"}:
                                anchors.extend((arc["original_lane_path"][0], arc["original_lane_path"][-1]))
                        anchors.append(last)
                        if all(anchors):
                            hops = []
                            common_modes = set(permitted)
                            for left, right in zip(anchors, anchors[1:]):
                                witnesses = _source_paths(original, left, right, members, permitted)
                                if not witnesses:
                                    common_modes.clear()
                                    break
                                hops.append(witnesses)
                                common_modes &= {mode for witness in witnesses for mode in witness["vehicle_classes"]}
                            if common_modes:
                                original_mode = "passenger" if "passenger" in common_modes else sorted(common_modes)[0]
                                for witnesses in hops:
                                    path = next(w["lane_ids"] for w in witnesses if original_mode in w["vehicle_classes"])
                                    old_lane_path.extend(path if not old_lane_path else path[1:])
                    boundary_paths.append({"witness_id": witness_id, "node_id": node_id, "intersection_part": part, "join_id": join_id, "connection": list(pair), "official_lane_sequence": lane_sequence, "original_lane_path": old_lane_path, "original_path_vehicle_class": original_mode, "candidate_lane_path": lane_path, "candidate_lane_ranges": lane_ranges, "allowed_vehicle_classes": sorted(permitted), "actual_allowed_vehicle_classes": sorted(actual_permitted), "composed_shape_network": composed, "connection_shape_network": connection_shape, "connection_anchor_error_m": connection_error, "native_shape_network": native, "geometry_status": "pass" if spatial_ok else "review_required", "maximum_sampled_geometry_offset_m": max_offset, "maximum_candidate_chain_gap_m": max_gap if math.isfinite(max_gap) else None, "movement_segments": segments, "status": "pass" if spatial_ok else "review_required", "topology_covered": bool(found and actual_permitted), "geometry_fallback": None if complete_geometry else "preserve_native_curve_pending_official_subpath_geometry"})
                    boundary_paths[-1]["candidate_allowed_vehicle_classes"] = sorted(modes)
                    boundary_paths[-1]["official_lane_sequence"] = [lane for lane in lane_sequence if lane in lanes]
                    boundary_paths[-1]["source_derived_segments"] = derived
        for token, row in zip(tokens, movements):
            witnesses = paths_by_token[token]
            coverage.append({"node_id": node_id, "intersection_part": part, "movement_id": str(row["movement_id"]), "ingress_lane_id": str(row["ingress_lane_id"]), "egress_lane_id": str(row["egress_lane_id"]), "status": "pass" if any(boundary_paths[index]["status"] == "pass" for index in witnesses) else "review_required", "witness_ids": [boundary_paths[index]["witness_id"] for index in witnesses], "reason": "composed_lane_and_spatial_subpath_witness" if witnesses else "no_verified_boundary_composition"})
    allowed_by_pair = defaultdict(set)
    for path in boundary_paths:
        allowed_by_pair[tuple(path["connection"])].update(path["allowed_vehicle_classes"])
    for path in boundary_paths:
        allowed = allowed_by_pair[tuple(path["connection"])]
        excess = set(path["candidate_allowed_vehicle_classes"]) - allowed
        path["boundary_allowed_vehicle_classes"] = sorted(allowed)
        path["excess_vehicle_classes"] = sorted(excess)
        path["permission_status"] = "pass" if path["actual_allowed_vehicle_classes"] and not excess else "review_required"
        if excess:
            path.update(status="review_required", geometry_status="review_required", topology_covered=False)
    witnesses_by_id = {path["witness_id"]: path for path in boundary_paths}
    for row in coverage:
        row["status"] = "pass" if any(witnesses_by_id[key]["status"] == "pass" for key in row["witness_ids"]) else "review_required"
    return {
        "schema": "torii.official-movement-composition/v1",
        "status": "pass" if coverage and all(row["status"] == "pass" for row in coverage) else "review_required",
        "original_xml_sha256": hashlib.sha256(ET.tostring(original_root)).hexdigest(),
        "candidate_xml_sha256": hashlib.sha256(ET.tostring(candidate_root)).hexdigest(),
        "plans_sha256": hashlib.sha256(json.dumps(plans, sort_keys=True).encode()).hexdigest(),
        "required_boundary_pairs": [list(pair) for pair in sorted(required_pairs)],
        "boundary_paths": boundary_paths,
        "movement_coverage": coverage,
        "continuations": continuations,
        "unresolved_continuations": unresolved_continuations,
        "original_lane_binding_reviews": reviews,
        "source_boundary_extensions": source_extensions,
        "source_boundary_extension_reviews": source_extension_reviews,
        "parameters": {"maximum_lane_error_m": maximum_lane_error_m, "minimum_match_margin_m": minimum_match_margin_m, "maximum_continuation_gap_m": maximum_continuation_gap_m, "maximum_source_anchor_error_m": maximum_source_anchor_error_m},
        "claim_boundary": "Official local movements are covered only by explicit composed lane sequences and candidate spatial subpaths. Source-backed supplementary links alone do not establish official coverage. Missing or ambiguous continuations remain under review.",
    }
