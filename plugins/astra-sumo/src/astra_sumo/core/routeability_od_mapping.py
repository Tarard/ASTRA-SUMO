"""Keep original diagnostic endpoints when ordinary roads are merged."""

from bisect import bisect_right
from collections.abc import Mapping
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
    source_offset = position / length * offsets[-1]
    point = _point_at(points, offsets, source_offset)
    offset, distance = _project(point, after, after_offsets)
    source_segment = min(bisect_right(offsets, source_offset) - 1, len(points) - 2)
    candidate_segment = min(bisect_right(after_offsets, offset) - 1, len(after) - 2)
    direction = sum((b-a) * (d-c) for a, b, c, d in zip(
        points[source_segment], points[source_segment + 1], after[candidate_segment], after[candidate_segment + 1]))
    if direction <= 0:
        raise ValueError("The source endpoint does not follow the candidate lane direction.")
    candidate_position = offset / after_offsets[-1] * after_length
    return {"source_lane_id": source.get("id"), "candidate_lane_id": target.get("id"),
            "source_position_m": position, "candidate_position_m": candidate_position,
            "source_point_xyz_m": point, "projection_error_m": distance}


def _relative_lane_endpoint(source, target, value, source_base, candidate_base):
    points, offsets, length = _geometry(source)
    after, after_offsets, after_length = _geometry(target)
    position = length if value == "max" else source_base
    candidate_position = after_length if value == "max" else candidate_base
    point = _point_at(points, offsets, position / length * offsets[-1])
    candidate_point = _point_at(after, after_offsets, candidate_position / after_length * after_offsets[-1])
    return {"source_lane_id": source.get("id"), "candidate_lane_id": target.get("id"),
            "source_position_m": position, "candidate_position_m": candidate_position,
            "source_point_xyz_m": point, "candidate_point_xyz_m": candidate_point,
            "point_displacement_m": math.dist(point, candidate_point)}


def map_merged_od_requests(source_root, candidate_root, requests, *, source_lane_indices=None):
    """Return all mapped requests or retain all unresolved requests for review."""
    aliases = _corridor_edge_aliases(source_root, candidate_root)
    validate_merged_road_access(source_root, candidate_root, aliases)
    original = {row.get("id"): row for row in source_root.findall("edge")}
    candidate = {row.get("id"): row for row in candidate_root.findall("edge")}
    if source_lane_indices is not None and not isinstance(source_lane_indices, Mapping):
        raise ValueError("Source lane correspondence must map roads to lane indices.")
    for identity, mapping in (source_lane_indices or {}).items():
        if identity not in original or not isinstance(mapping, Mapping):
            raise ValueError("Source lane correspondence requires existing source roads and lane mappings.")
        indices = {lane.get("index") for lane in original[identity].findall("lane")}
        retained = [index for index in mapping.values() if index is not None]
        if (any(index not in indices for index in mapping)
                or any(not isinstance(index, str) or not index.isdigit() for index in retained)
                or len(set(retained)) != len(retained)):
            raise ValueError("Source lane correspondence requires distinct candidate lane indices.")
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
                lane_mapping = (source_lane_indices or {}).get(identity)
                if (lane_mapping is None and identity not in aliases and target is not None
                        and signature(source) == signature(target)):
                    continue
                same_road = target is not None and all(source.get(key) == target.get(key) for key in ("from", "to"))
                if identity not in aliases and not same_road:
                    raise ValueError(f"Road {identity!r} has no verified directed-road correspondence.")
                if row.get("type") is not None or requests.find("vType") is not None or requests.find("vTypeDistribution") is not None:
                    raise ValueError("Merged endpoint mapping currently requires the default passenger vehicle type.")
                if row.get("via") or list(row) or any(row.get(name) is not None for name in ("departPosLat", "arrivalPosLat")):
                    raise ValueError("Merged endpoint mapping requires trips without intermediate stops or lateral offsets.")
                lanes = [lane for lane in source.findall("lane")
                         if "passenger" in _permission_set(lane.attrib, network_version=source_root.get("version", "0.0"))]
                lanes.sort(key=lambda lane: int(lane.get("index")))
                selector = row.get(role + "Lane", "best_prob" if role == "depart" else "current")
                if selector == "first" and role == "depart":
                    lanes = lanes[:1]
                elif not (role == "arrival" and selector == "current" or role == "depart" and selector in {"best_prob", "best", "free", "allowed", "random"}):
                    lanes = [lane for lane in lanes if lane.get("index") == str(int(selector))]
                if not lanes:
                    raise ValueError("The source endpoint has no selected passenger lane.")
                value = row.get(role + "Pos", "base" if role == "depart" else "max")
                targets = {lane.get("index"): lane for lane in target.findall("lane")}
                if lane_mapping is None:
                    if len(source.findall("lane")) != len(targets):
                        raise ValueError("A changed lane count requires declared source lane correspondence.")
                    lane_mapping = {index: index for index in targets}
                relative_default = identity not in aliases and same_road and value == ("base" if role == "depart" else "max")
                removed_lanes = []
                if relative_default and selector in {"best_prob", "best", "free", "allowed", "random", "current"}:
                    removed_lanes = [lane.get("id") for lane in lanes if lane_mapping.get(lane.get("index")) is None]
                    lanes = [lane for lane in lanes if lane_mapping.get(lane.get("index")) is not None]
                selected = [lane_mapping.get(lane.get("index")) for lane in lanes]
                if not lanes or any(index is None or str(index) not in targets for index in selected):
                    raise ValueError("A selected source lane is absent from the candidate road.")
                selected_targets = [targets[str(index)] for index in selected]
                if any("passenger" not in _permission_set(lane.attrib, network_version=candidate_root.get("version", "0.0"))
                       for lane in selected_targets):
                    raise ValueError("A mapped candidate lane no longer permits the source passenger request.")
                if selector.isdigit() or selector == "first" and role == "depart":
                    if row.get(role + "Lane") != str(selected[0]):
                        row.set(role + "Lane", str(selected[0]))
                        changed = True
                if relative_default:
                    # Road-relative defaults move with a retained road. Explicit positions keep their physical correspondence checks.
                    pairs = [_relative_lane_endpoint(lane, target_lane, value,
                        min(5.1, float(source.find("lane").get("length"))),
                        min(5.1, float(target.find("lane").get("length")))) for lane, target_lane in zip(lanes, selected_targets)]
                    record["endpoints"].append({"role": role, "source_edge_id": identity, "candidate_edge_id": identity,
                        "position_semantics": "road_relative_default", "requested_position": value,
                        "removed_source_lane_ids": removed_lanes,
                        "candidate_lane_choice_ids": ([lane.get("id") for lane in target.findall("lane")
                            if "passenger" in _permission_set(lane.attrib, network_version=candidate_root.get("version", "0.0"))]
                            if selector in {"best_prob", "best", "free", "allowed", "random", "current"}
                            else [lane.get("id") for lane in selected_targets]),
                        "lane_selection": selector, "candidate_lane_selection": row.get(role + "Lane", selector), "lanes": pairs})
                    continue
                if selector in {"best_prob", "best", "free", "allowed", "random", "current"} and {
                        lane.get("id") for lane in selected_targets} != {
                        lane.get("id") for lane in target.findall("lane")
                        if "passenger" in _permission_set(lane.attrib, network_version=candidate_root.get("version", "0.0"))}:
                    raise ValueError("A physical endpoint on a changed lane set requires an explicit source lane.")
                if value == "base" and role == "depart":
                    # SUMO basePos adds POSITION_EPS and clamps to the source edge length.
                    value = str(min(5.1, float(source.find("lane").get("length"))))
                pairs = [_lane_endpoint(lane, target_lane, value) for lane, target_lane in zip(lanes, selected_targets)]
                position = pairs[0]["candidate_position_m"]
                for pair, target_lane in zip(pairs, selected_targets):
                    shape, offsets, length = _geometry(target_lane)
                    point = _point_at(shape, offsets, position / length * offsets[-1])
                    pair["encoded_candidate_position_m"] = position
                    pair["encoded_position_error_m"] = math.dist(pair["source_point_xyz_m"], point)
                    if not 0 <= position <= length or pair["encoded_position_error_m"] > TOLERANCE_M:
                        raise ValueError(f"The {role} lane set needs different candidate positions; use an explicit source lane.")
                row.set(key, aliases.get(identity, identity))
                row.set(role + "Pos", format(position, ".12g"))
                record["endpoints"].append({"role": role, "source_edge_id": identity,
                                             "candidate_edge_id": aliases.get(identity, identity),
                                             "lane_selection": selector, "candidate_lane_selection": row.get(role + "Lane", selector), "lanes": pairs})
                changed = True
            if row.get("from") == row.get("to") and record["source"]["from"] != record["source"]["to"]:
                if float(row.get("arrivalPos", "nan")) < float(row.get("departPos", "nan")):
                    raise ValueError("The merged endpoints require a return route rather than a forward same-road trip.")
            record["candidate"] = dict(row.attrib)
            record["probe_mode"] = ("road_relative_endpoints" if any(
                endpoint.get("position_semantics") == "road_relative_default" for endpoint in record["endpoints"])
                else "physical_endpoints")
        except (KeyError, ValueError, StopIteration) as error:
            removed = [record["source"][key] for key in ("from", "to")
                       if record["source"][key] in original
                       and aliases.get(record["source"][key], record["source"][key]) not in candidate]
            failures.append({"vehicle_id": row.get("id"), "reason": str(error),
                             "classification": "source_endpoint_removed" if removed else "physical_position_unresolved",
                             "removed_source_edge_ids": removed})
            # Preserve every OD for strict routing, without claiming unresolved physical positions.
            row.attrib.clear()
            row.attrib.update(record["source"])
            for key in ("from", "to"):
                row.set(key, aliases.get(record["source"][key], record["source"][key]))
            for key in ("departPos", "arrivalPos", "departLane", "arrivalLane", "departPosLat", "arrivalPosLat"):
                row.attrib.pop(key, None)
            record["attempted_mapped_endpoints"] = record["endpoints"]
            record.update(candidate=dict(row.attrib), endpoints=[],
                          probe_mode="removed_endpoint" if removed else "logical_only")
            changed = True
        records.append(record)
    removed_ids = [row["vehicle_id"] for row in failures if row["classification"] == "source_endpoint_removed"]
    relative_count = sum(row["probe_mode"] == "road_relative_endpoints" for row in records)
    return mapped, {"status": "review_required" if failures else "pass", "changed": changed,
                    "method": "directed_road_identity_or_verified_merge_and_lane_geometry/v3", "maximum_projection_error_m": TOLERANCE_M,
                    "request_count": len(records), "unresolved_requests": failures, "requests": records,
                    "source_lane_indices": source_lane_indices or {},
                    "physical_request_count": len(records) - len(failures) - relative_count,
                    "road_relative_request_count": relative_count, "logical_only_request_count": len(failures) - len(removed_ids),
                    "removed_endpoint_request_count": len(removed_ids), "removed_endpoint_vehicle_ids": removed_ids,
                    "claim_boundary": "Every original request remains recorded without resampling. Retained-road defaults preserve road-relative placement and record coordinate displacements separately. They do not establish fixed physical endpoints. Explicit positions and merged-road defaults require physical correspondence. Removed endpoints cannot receive native routes. Remaining unresolved positions receive strict road-level probes. Default lane selection does not require identical realized lane choices."}
