"""Keep original diagnostic endpoints when ordinary roads are merged."""

from bisect import bisect_right
from copy import deepcopy
import math

from .corridor_simplification import _corridor_edge_aliases, validate_merged_road_access
from .osm_access import _permission_set


TOLERANCE_M = 0.02


def _geometry(lane):
    points = [tuple(map(float, value.split(","))) for value in lane.get("shape", "").split()]
    if len(points) < 2 or any(len(point) not in {2, 3} or not all(map(math.isfinite, point)) for point in points):
        raise ValueError("Endpoint lanes need finite XY or XYZ shapes.")
    points = [(*point, 0.0) if len(point) == 2 else point for point in points]
    points = [point for index, point in enumerate(points) if not index or point != points[index - 1]]
    offsets = [0.0]
    for first, last in zip(points, points[1:]):
        offsets.append(offsets[-1] + math.dist(first, last))
    length = float(lane.get("length", "nan"))
    if not math.isfinite(length) or length <= 0 or offsets[-1] <= 0:
        raise ValueError("Endpoint lanes need positive logical and geometric lengths.")
    return points, offsets, length


def _point_at(points, offsets, position):
    index = min(bisect_right(offsets, position) - 1, len(points) - 2)
    fraction = (position - offsets[index]) / (offsets[index + 1] - offsets[index])
    return tuple(a + fraction * (b - a) for a, b in zip(points[index], points[index + 1]))


def _project(point, points, offsets):
    fits = []
    for index, (first, last) in enumerate(zip(points, points[1:])):
        vector = tuple(b - a for a, b in zip(first, last))
        length = offsets[index + 1] - offsets[index]
        if not length:
            continue
        fraction = max(0.0, min(1.0, sum((p - a) * v for p, a, v in zip(point, first, vector)) / length**2))
        projected = tuple(a + fraction * v for a, v in zip(first, vector))
        fits.append((math.dist(point, projected), offsets[index] + fraction * length))
    distance, position = min(fits)
    if distance > TOLERANCE_M or any(error <= TOLERANCE_M and abs(offset - position) > TOLERANCE_M for error, offset in fits):
        raise ValueError("The source endpoint has no unique candidate position within 0.02 m.")
    return position, distance


def _lane_endpoint(source, target, value):
    points, offsets, length = _geometry(source)
    after, after_offsets, after_length = _geometry(target)
    position = length if value == "max" else float(value)
    if position < 0:
        position += length
    if not math.isfinite(position) or not 0 <= position <= length:
        raise ValueError("The source endpoint lies outside its lane.")
    projected = [_project(point, after, after_offsets)[0] for point in points]
    if any(b < a - TOLERANCE_M for a, b in zip(projected, projected[1:])) or projected[-1] <= projected[0]:
        raise ValueError("The source lane does not follow the candidate lane direction.")
    point = _point_at(points, offsets, position / length * offsets[-1])
    offset, distance = _project(point, after, after_offsets)
    candidate_position = offset / after_offsets[-1] * after_length
    return {"source_lane_id": source.get("id"), "candidate_lane_id": target.get("id"),
            "source_position_m": position, "candidate_position_m": candidate_position,
            "source_point_xyz_m": point, "projection_error_m": distance}


def map_merged_od_requests(source_root, candidate_root, requests):
    """Return all mapped requests or retain all unresolved requests for review."""
    aliases = _corridor_edge_aliases(source_root, candidate_root)
    validate_merged_road_access(source_root, candidate_root, aliases)
    original = {row.get("id"): row for row in source_root.findall("edge")}
    candidate = {row.get("id"): row for row in candidate_root.findall("edge")}
    before_frame, after_frame = source_root.find("location"), candidate_root.find("location")
    if before_frame is None or after_frame is None or (
        before_frame.get("projParameter"), tuple(map(float, before_frame.get("netOffset", "0,0").split(",")))
    ) != (after_frame.get("projParameter"), tuple(map(float, after_frame.get("netOffset", "0,0").split(",")))):
        raise ValueError("Endpoint correspondence requires matching declared coordinate frames.")
    mapped = deepcopy(requests)
    records, failures = [], []
    changed = False
    def signature(edge):
        return [(lane.get("index"), _geometry(lane)) for lane in edge.findall("lane")]

    for row in mapped.findall("trip"):
        record = {"vehicle_id": row.get("id"), "source": dict(row.attrib), "endpoints": []}
        try:
            for role, key in (("depart", "from"), ("arrival", "to")):
                identity = row.get(key)
                source = original[identity]
                target = candidate.get(aliases.get(identity, identity))
                if identity not in aliases and target is not None and signature(source) == signature(target):
                    continue
                same_road = target is not None and all(source.get(key) == target.get(key) for key in ("from", "to")) and (
                    [(lane.get("id"), lane.get("index")) for lane in source.findall("lane")]
                    == [(lane.get("id"), lane.get("index")) for lane in target.findall("lane")])
                if identity not in aliases and not same_road:
                    raise ValueError(f"Road {identity!r} has no verified merge correspondence.")
                if row.get("type") is not None or requests.find("vType") is not None or requests.find("vTypeDistribution") is not None:
                    raise ValueError("Merged endpoint mapping currently requires the default passenger vehicle type.")
                if row.get("via") or list(row) or any(row.get(name) is not None for name in ("departPosLat", "arrivalPosLat")):
                    raise ValueError("Merged endpoint mapping requires trips without intermediate stops or lateral offsets.")
                lanes = [lane for lane in source.findall("lane")
                         if "passenger" in _permission_set(lane.attrib, network_version=source_root.get("version", "0.0"))]
                lanes.sort(key=lambda lane: int(lane.get("index")))
                selector = row.get(role + "Lane", "first" if role == "depart" else "current")
                if selector == "first" and role == "depart":
                    lanes = lanes[:1]
                elif selector != "current" or role != "arrival":
                    lanes = [lane for lane in lanes if lane.get("index") == str(int(selector))]
                if not lanes:
                    raise ValueError("The source endpoint has no selected passenger lane.")
                value = row.get(role + "Pos", "base" if role == "depart" else "max")
                if value == "base" and role == "depart":
                    value = "5"  # SUMO's default passenger front starts at its 5 m vehicle length.
                pairs = [_lane_endpoint(lane, next(target_lane for target_lane in target.findall("lane")
                          if target_lane.get("index") == lane.get("index")), value) for lane in lanes]
                position = pairs[0]["candidate_position_m"]
                for pair, lane in zip(pairs, lanes):
                    target_lane = next(value for value in target.findall("lane") if value.get("index") == lane.get("index"))
                    shape, offsets, length = _geometry(target_lane)
                    point = _point_at(shape, offsets, position / length * offsets[-1])
                    pair["encoded_candidate_position_m"] = position
                    pair["encoded_position_error_m"] = math.dist(pair["source_point_xyz_m"], point)
                    if not 0 <= position <= length or pair["encoded_position_error_m"] > TOLERANCE_M:
                        raise ValueError("The current arrival lane needs different candidate positions; use an explicit source lane.")
                row.set(key, aliases.get(identity, identity))
                row.set(role + "Pos", format(position, ".12g"))
                record["endpoints"].append({"role": role, "source_edge_id": identity,
                                             "candidate_edge_id": aliases.get(identity, identity), "lanes": pairs})
                changed = True
            if row.get("from") == row.get("to") and record["source"]["from"] != record["source"]["to"]:
                if float(row.get("arrivalPos", "nan")) < float(row.get("departPos", "nan")):
                    raise ValueError("The merged endpoints require a return route rather than a forward same-road trip.")
            record["candidate"] = dict(row.attrib)
        except (KeyError, ValueError, StopIteration) as error:
            failures.append({"vehicle_id": row.get("id"), "reason": str(error)})
        records.append(record)
    return mapped, {"status": "review_required" if failures else "pass", "changed": changed,
                    "method": "directed_road_identity_or_verified_merge_and_lane_geometry/v1", "maximum_projection_error_m": TOLERANCE_M,
                    "request_count": len(records), "unresolved_requests": failures, "requests": records,
                    "claim_boundary": "Source requests are never filtered or resampled. Logical lane positions use each lane's geometric-length ratio. Mapping requires retained directed road and lane identities or a verified merge path. Both projection and encoded position errors must stay within 0.02 m. Other endpoints remain unverified."}
