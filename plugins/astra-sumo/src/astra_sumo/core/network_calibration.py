"""Compare a SUMO network with dated, source-bound road facts."""

from __future__ import annotations

from collections import Counter
from datetime import date
import hashlib
import math
from pathlib import Path
from statistics import median, quantiles
from typing import Any, Literal
import xml.etree.ElementTree as ET

from pydantic import BaseModel, ConfigDict, Field
from pyproj import CRS, Transformer
from shapely.geometry import LineString, MultiLineString, Point, Polygon
from sumolib.net.lane import SUMO_VEHICLE_CLASSES, get_allowed

from .artifact_io import write_json_atomic


class FactSource(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(min_length=1)
    path: str = Field(min_length=1)
    sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    source_date: date | None
    authority: str = Field(min_length=1)
    license: str = Field(min_length=1)


class NetworkFact(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(min_length=1)
    kind: Literal["lane_centerline", "junction_surface", "lane_count", "lane_permissions",
                  "movement_inventory", "stop_line", "signal_group", "speed", "lane_width"]
    selector: dict[str, Any]
    expected: Any = None
    source_ids: list[str] = Field(default_factory=list)
    mapping_status: Literal["confirmed", "unresolved"] = "confirmed"
    mapping_confidence: Literal["confirmed", "high", "medium", "low", "none", "conflict"] = "confirmed"
    mapping_method: str = "supplied"
    traffic_mode: Literal["motor", "bicycle", "pedestrian", "unspecified"] = "unspecified"
    reference_direction: Literal[-1, 1] | None = None
    alternatives: list[dict[str, Any]] = Field(default_factory=list)
    mapping_diagnostics: dict[str, Any] = Field(default_factory=dict)
    tolerance_m: float | None = Field(default=None, ge=0, strict=True, allow_inf_nan=False)
    sample_spacing_m: float | None = Field(default=None, gt=0, strict=True, allow_inf_nan=False)
    minimum_iou: float | None = Field(default=None, ge=0, le=1, strict=True, allow_inf_nan=False)
    minimum_coverage: float | None = Field(default=None, ge=0, le=1, strict=True, allow_inf_nan=False)
    tolerance_m_s: float | None = Field(default=None, ge=0, strict=True, allow_inf_nan=False)
    maximum_mean_lateral_offset_m: float | None = Field(default=None, ge=0, strict=True, allow_inf_nan=False)
    tolerance_longitudinal_m: float | None = Field(default=None, ge=0, strict=True, allow_inf_nan=False)


class NetworkFactTable(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_id: Literal["astra.network-facts/v1"] = Field(alias="schema")
    site_id: str = Field(min_length=1)
    target_date: date
    coordinate_system: dict[str, Any]
    sources: list[FactSource]
    facts: list[NetworkFact] = Field(min_length=1)


_LAYERS = {"lane_centerline": "geometry", "junction_surface": "geometry",
           "lane_count": "lanes", "lane_permissions": "lanes", "speed": "lanes", "lane_width": "lanes",
           "movement_inventory": "movements", "stop_line": "control", "signal_group": "control"}


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _network_root(raw: bytes) -> ET.Element:
    root = ET.fromstring(raw)
    if root.tag != "net":
        raise ValueError("Expected a SUMO net root element.")
    for elements in (root.findall("edge"), root.findall("junction"), root.findall("edge/lane")):
        ids = [element.get("id") for element in elements]
        if any(not value for value in ids) or len(set(ids)) != len(ids):
            raise ValueError("Network object identifiers must be present and unique.")
    return root


def _points(value: Any, *, minimum: int = 2) -> list[tuple[float, float]]:
    if not isinstance(value, list) or len(value) < minimum:
        raise ValueError(f"Expected at least {minimum} coordinate pairs.")
    result = []
    for pair in value:
        if not isinstance(pair, (list, tuple)) or len(pair) != 2:
            raise ValueError("Coordinates must be two-dimensional pairs.")
        if any(isinstance(v, bool) for v in pair):
            raise ValueError("Coordinates cannot contain boolean values.")
        if any(not isinstance(v, (int, float, str)) for v in pair):
            raise ValueError("Coordinates must contain numeric values.")
        point = tuple(float(v) for v in pair)
        if not all(math.isfinite(v) for v in point):
            raise ValueError("Coordinates must be finite.")
        result.append(point)
    return result


def _shape(element: ET.Element) -> list[tuple[float, float]]:
    points = _points([[float(v) for v in token.split(",")[:2]] for token in element.get("shape", "").split()], minimum=1)
    return points if len(points) > 1 else points * 2


def _coordinate_transform(root: ET.Element, system: dict[str, Any]):
    location = root.find("location")
    attributes = dict(location.attrib) if location is not None else {}
    if system.get("type") == "network_xy_m":
        declared = system.get("location")
        if not isinstance(declared, dict):
            raise ValueError("Network coordinates require an exact location frame declaration.")
        offsets = [_points([record.get("netOffset", "0,0").split(",")], minimum=1)[0] for record in (declared, attributes)]
        a, b = declared.get("projParameter", ""), attributes.get("projParameter", "")
        same_projection = a == b or (a not in {"", "!", "-", "."} and b not in {"", "!", "-", "."}
                                      and CRS.from_user_input(a).equals(CRS.from_user_input(b)))
        if offsets[0] != offsets[1] or not same_projection:
            raise ValueError("Network coordinates require an exact location frame declaration.")
        return lambda points: points
    if system.get("type") != "projected_crs":
        raise ValueError("Use projected_crs or an explicit network_xy_m coordinate system.")
    reference = CRS.from_user_input(system["crs"])
    if not reference.is_projected or any(axis.unit_conversion_factor != 1 for axis in reference.axis_info[:2]):
        raise ValueError("Reference coordinates must use a projected CRS with metre units.")
    projection = attributes.get("projParameter", "")
    if projection in ("", "!", "-", "."):
        raise ValueError("The network has no geographic projection.")
    offset = _points([attributes.get("netOffset", "").split(",")], minimum=1)[0]
    transform = Transformer.from_crs(CRS.from_user_input(projection), reference, always_xy=True)
    return lambda points: [transform.transform(x - offset[0], y - offset[1], errcheck=True) for x, y in points]


def _list(value: Any, label: str) -> list[str]:
    if not isinstance(value, list) or not value or not all(isinstance(v, str) and v for v in value):
        raise ValueError(f"{label} must be a nonempty list of identifiers.")
    if len(set(value)) != len(value):
        raise ValueError(f"{label} contains duplicate identifiers.")
    return value


def _permissions(lane: ET.Element) -> set[str]:
    allow, disallow = lane.get("allow"), lane.get("disallow")
    tokens = set((allow or "").split()) | set((disallow or "").split())
    if tokens - set(SUMO_VEHICLE_CLASSES) - {"all"}:
        raise ValueError("Lane permissions contain an unknown vehicle class.")
    if allow == "all":
        allow = None
    return set(get_allowed(allow, disallow))


def _pairs(value: Any) -> set[tuple[str, str]]:
    if not isinstance(value, list):
        raise ValueError("Movement inventory must be a list of lane pairs.")
    pairs = set()
    for pair in value:
        if not isinstance(pair, list) or len(pair) != 2 or not all(isinstance(v, str) and v for v in pair):
            raise ValueError("Each movement must contain two lane identifiers.")
        if tuple(pair) in pairs:
            raise ValueError("Movement inventory contains a duplicate pair.")
        pairs.add(tuple(pair))
    return pairs


def _saved_lane_path(root, ordinary_lanes):
    from .source_movement_support import _candidate_modes, _index
    index = _index(root)
    paths = {}
    for _, start, target, connection in index["movements"]:
        _, path = _candidate_modes(index, start, target, connection)
        if path:
            paths.setdefault((start, target), []).append(path)
    result = [ordinary_lanes[0]]
    for pair in zip(ordinary_lanes, ordinary_lanes[1:]):
        candidates = paths.get(pair, [])
        if len(candidates) != 1:
            raise ValueError("Centerline correspondence requires one complete saved path between consecutive ordinary lanes.")
        result.extend(candidates[0][1:])
    return result


def centerline_overlap_metrics(reference, parts, spacing, *, direction=None, coverage_bands_m=(1.0, 2.0)):
    """Sample perpendicular feet on aligned segments, excluding longitudinal tails and gaps."""
    count = max(1, math.ceil(reference.length / spacing))
    segments = [(a, b) for part in parts for a, b in zip(part, part[1:]) if a != b]
    offsets, cosines = [], []
    for i in range(count):
        position = (i + 0.5) * reference.length / count
        point = reference.interpolate(position)
        epsilon = min(reference.length / count / 2, 0.1)
        left, right = reference.interpolate(position - epsilon), reference.interpolate(position + epsilon)
        tx, ty = right.x - left.x, right.y - left.y
        tangent_length = math.hypot(tx, ty)
        distances, aligned = [], []
        for (ax, ay), (bx, by) in segments:
            dx, dy = bx - ax, by - ay
            squared = dx * dx + dy * dy
            # A crossing road cannot supply longitudinal coverage of this lane.
            if tangent_length == 0:
                continue
            cosine = (dx * tx + dy * ty) / (math.sqrt(squared) * tangent_length)
            if abs(cosine) < 1 / math.sqrt(2):
                continue
            fraction = ((point.x - ax) * dx + (point.y - ay) * dy) / squared
            if 0 <= fraction <= 1:
                distance = math.hypot(point.x - ax - fraction * dx, point.y - ay - fraction * dy)
                aligned.append((distance, cosine))
                if direction is None or cosine * direction >= 1 / math.sqrt(2):
                    distances.append(distance)
        if aligned:
            cosines.append(min(aligned)[1])
        if distances:
            offsets.append(min(distances))
    covered = len(offsets) * reference.length / count
    return {"mean_lateral_offset_m": sum(offsets) / len(offsets) if offsets else None,
            "max_sampled_lateral_offset_m": max(offsets, default=None),
            "reference_coverage": len(offsets) / count, "covered_reference_length_m": covered,
            "uncovered_reference_length_m": reference.length - covered, "reference_length_m": reference.length,
            "coverage_sample_spacing_m": reference.length / count,
            "coverage_sample_count": count, "coverage_method": "midpoint_perpendicular_foot_heading_within_45deg",
            "mean_direction_cosine": sum(cosines) / len(cosines) if cosines else None,
            "expected_reference_direction": direction,
            "coverage_within_m": {str(float(band)): sum(value <= band for value in offsets) / count
                                  for band in sorted(set(coverage_bands_m))}}


def _measure(fact: NetworkFact, root: ET.Element, transform, mode="release") -> dict[str, Any]:
    lanes = {lane.get("id"): lane for edge in root.findall("edge") for lane in edge.findall("lane")}
    edges = {edge.get("id"): edge for edge in root.findall("edge") if edge.get("function") != "internal"}
    selector, expected = fact.selector, fact.expected
    result: dict[str, Any] = {"id": fact.id, "kind": fact.kind, "layer": _LAYERS[fact.kind],
                              "known": expected is not None, "source_ids": fact.source_ids,
                              "selector": selector, "mapping_status": fact.mapping_status,
                              "mapping_confidence": fact.mapping_confidence if fact.mapping_status == "confirmed"
                              or fact.mapping_confidence != "confirmed" else "low",
                              "mapping_method": fact.mapping_method,
                              "traffic_mode": fact.traffic_mode, "reference_direction": fact.reference_direction,
                              "alternatives": fact.alternatives, "mapping_diagnostics": fact.mapping_diagnostics,
                              "acceptance": {"tolerance_m": fact.tolerance_m, "minimum_iou": fact.minimum_iou,
                                  "minimum_coverage": fact.minimum_coverage,
                                  "maximum_mean_lateral_offset_m": fact.maximum_mean_lateral_offset_m,
                                  "tolerance_longitudinal_m": fact.tolerance_longitudinal_m},
                              "expected": expected, "observed": None, "metrics": {}, "status": "unknown"}
    if expected is None:
        return {**result, "reason": "reference_fact_unknown"}
    if not fact.source_ids and mode == "release":
        raise ValueError(f"Known fact {fact.id} requires source evidence.")
    if fact.mapping_status == "unresolved" and not fact.selector:
        return {**result, "reason": fact.mapping_method if fact.mapping_method.startswith("not_modeled")
                else "reference_to_network_mapping_unresolved"}
    kind = fact.kind
    selectors = {"lane_centerline": ({"lane_id"}, {"lane_ids"}, {"lane_ids", "include_internal_paths"}), "stop_line": ({"lane_id"},),
                 "lane_permissions": ({"lane_id"},), "speed": ({"lane_id"},), "lane_width": ({"lane_id"},),
                 "lane_count": ({"edge_ids", "vehicle_class"},),
                 "movement_inventory": ({"incoming_lane_ids"}, {"incoming_lane_ids", "outgoing_lane_ids", "junction_ids", "vehicle_class"}),
                 "signal_group": ({"lane_pair"},),
                 "junction_surface": ({"junction_id"},)}
    if set(selector) not in selectors[kind]:
        raise ValueError(f"Invalid selector fields for {kind}.")
    if kind in ("lane_centerline", "stop_line", "lane_permissions", "speed", "lane_width"):
        selected_ids = (_list(selector["lane_ids"], "Centerline lane IDs")
                        if kind == "lane_centerline" and "lane_ids" in selector else [selector["lane_id"]])
        if any(lane_id not in lanes for lane_id in selected_ids):
            return {**result, "status": "unknown" if fact.mapping_status == "unresolved" else "mismatched",
                    "reason": "network_lane_missing"}
        if selector.get("include_internal_paths"):
            if selector["include_internal_paths"] is not True:
                raise ValueError("include_internal_paths must be a boolean.")
            selected_ids = _saved_lane_path(root, selected_ids)
            result["resolved_lane_ids"] = selected_ids
        lane = lanes[selected_ids[0]]
        if kind == "lane_width":
            if type(expected) not in (int, float) or not math.isfinite(expected) or expected <= 0:
                raise ValueError("Expected lane width must be finite and positive in metres.")
            observed = float(lane.get("width", "3.2"))
            result["metrics"] = {"absolute_width_error_m": abs(observed - expected)}
            matches = abs(observed - expected) <= fact.tolerance_m if fact.tolerance_m is not None else None
        elif kind == "speed":
            if type(expected) not in (int, float) or not math.isfinite(expected) or expected <= 0:
                raise ValueError("Expected speed must be finite and positive in metres per second.")
            observed = float(lane.get("speed"))
            result["metrics"] = {"absolute_speed_error_m_s": abs(observed - expected)}
            matches = abs(observed - expected) <= fact.tolerance_m_s if fact.tolerance_m_s is not None else None
        elif kind == "lane_permissions":
            if not isinstance(expected, list):
                raise ValueError("Expected vehicle permissions must be a list.")
            wanted = set(_list(expected, "Expected vehicle classes")) if expected else set()
            if wanted - set(SUMO_VEHICLE_CLASSES):
                raise ValueError("Expected permissions contain an unknown vehicle class.")
            observed = sorted(_permissions(lane))
            matches = set(observed) == wanted
            result["metrics"] = {"permission_difference_count": len(set(observed) ^ wanted)}
        else:
            points = transform(_shape(lane))
            observed = [list(p) for p in points]
            if kind == "stop_line":
                observed = list(points[-1])
                reference_point = _points([expected], minimum=1)[0]
                distance = Point(points[-1]).distance(Point(reference_point))
                last = next((point for point in reversed(points[:-1]) if point != points[-1]), None)
                if last is None:
                    raise ValueError("The stop-line lane needs a nonzero terminal segment.")
                dx, dy = points[-1][0] - last[0], points[-1][1] - last[1]
                length = math.hypot(dx, dy)
                rx, ry = points[-1][0] - reference_point[0], points[-1][1] - reference_point[1]
                longitudinal, lateral = (rx * dx + ry * dy) / length, (-rx * dy + ry * dx) / length
                result["metrics"] = {"offset_m": distance, "longitudinal_offset_m": longitudinal,
                                     "lateral_offset_m": lateral}
                matches = (abs(longitudinal) <= fact.tolerance_longitudinal_m if fact.tolerance_longitudinal_m is not None
                           else distance <= fact.tolerance_m if fact.tolerance_m is not None else None)
            else:
                if fact.sample_spacing_m is None:
                    raise ValueError("lane_centerline requires an explicit sample_spacing_m.")
                reference = LineString(_points(expected))
                parts = [transform(_shape(lanes[lane_id])) for lane_id in selected_ids]
                candidate = LineString(parts[0]) if len(parts) == 1 else MultiLineString(parts)
                observed = [[list(p) for p in part] for part in parts]
                if reference.length <= 0 or candidate.length <= 0:
                    raise ValueError("Lane centerlines must have positive geometric length.")
                count = max(1, math.ceil(reference.length / fact.sample_spacing_m))
                # Equal arclength intervals make the mean independent of source vertex density.
                distances = [reference.interpolate(i / count, normalized=True).distance(candidate)
                             for i in range(count + 1)]
                mean = (sum(distances) - (distances[0] + distances[-1]) / 2) / count
                result["metrics"] = {"mean_reference_offset_m": mean, "max_sampled_reference_offset_m": max(distances),
                                     "sample_spacing_m": reference.length / count, "sample_count": count + 1}
                bands = (1.0, 2.0, fact.tolerance_m) if fact.tolerance_m is not None else (1.0, 2.0)
                result["metrics"].update(centerline_overlap_metrics(reference, parts, fact.sample_spacing_m,
                    direction=fact.reference_direction, coverage_bands_m=bands))
                lateral = result["metrics"]["max_sampled_lateral_offset_m"]
                matches = (lateral is not None and lateral <= fact.tolerance_m
                           and result["metrics"]["coverage_within_m"][str(float(fact.tolerance_m))]
                           >= (fact.minimum_coverage if fact.minimum_coverage is not None else 1)
                           and (fact.maximum_mean_lateral_offset_m is None
                                or result["metrics"]["mean_lateral_offset_m"] <= fact.maximum_mean_lateral_offset_m)) \
                    if fact.tolerance_m is not None else None
    elif kind == "lane_count":
        selected = _list(selector["edge_ids"], "Approach edge IDs")
        vehicle_class = selector["vehicle_class"]
        if vehicle_class not in SUMO_VEHICLE_CLASSES and vehicle_class != "all":
            raise ValueError("Approach lane count requires a valid vehicle class or all.")
        if type(expected) is not int or expected < 0:
            raise ValueError("Expected lane count must be a nonnegative integer.")
        if any(edge not in edges for edge in selected):
            return {**result, "status": "mismatched", "reason": "network_edge_missing"}
        observed = sum(vehicle_class == "all" or vehicle_class in _permissions(lane)
                       for edge in selected for lane in edges[edge].findall("lane"))
        result["metrics"] = {"absolute_count_error": abs(observed - expected)}
        matches = observed == expected
    elif kind in ("movement_inventory", "signal_group"):
        actual: dict[tuple[str, str], list[ET.Element]] = {}
        for connection in root.findall("connection"):
            if connection.get("from") not in edges:
                continue
            pair = (f'{connection.get("from")}_{connection.get("fromLane")}',
                    f'{connection.get("to")}_{connection.get("toLane")}')
            if pair[0] not in lanes or pair[1] not in lanes:
                raise ValueError("A network connection references a missing lane.")
            actual.setdefault(pair, []).append(connection)
        if kind == "movement_inventory":
            incoming = set(_list(selector["incoming_lane_ids"], "Incoming lane IDs"))
            wanted = _pairs(expected)
            if any(pair[0] not in incoming for pair in wanted):
                raise ValueError("Expected movements extend beyond the declared incoming lanes.")
            pairs = {pair for pair in actual if pair[0] in incoming}
            if "junction_ids" in selector:
                from .network_edit_applicability import _boundary_access
                members = set(_list(selector["junction_ids"], "Movement junction IDs"))
                outgoing = set(_list(selector["outgoing_lane_ids"], "Outgoing lane IDs"))
                if (incoming | outgoing) - lanes.keys():
                    return {**result, "status": "mismatched", "reason": "network_lane_missing"}
                vehicle_class = selector["vehicle_class"]
                if vehicle_class not in SUMO_VEHICLE_CLASSES:
                    raise ValueError("Movement paths require a valid vehicle class.")
                _, _, paths = _boundary_access(root, members, incoming_lane_ids=incoming, outgoing_lane_ids=outgoing)
                pairs = {pair for pair, modes in paths.items() if vehicle_class in modes}
                result["comparison_scope"] = "explicit_paths_within_declared_junctions"
            common = len(wanted & pairs)
            observed = [list(pair) for pair in sorted(pairs)]
            result["metrics"] = {"true_positive": common, "false_positive": len(pairs - wanted),
                                 "false_negative": len(wanted - pairs),
                                 "precision": common / len(pairs) if pairs else None,
                                 "recall": common / len(wanted) if wanted else None,
                                 "extra_movements": [list(p) for p in sorted(pairs - wanted)],
                                 "missing_movements": [list(p) for p in sorted(wanted - pairs)]}
            matches = pairs == wanted and all(lane in lanes for lane in incoming)
        else:
            pair = selector["lane_pair"]
            _pairs([pair])
            if not isinstance(expected, dict) or set(expected) != {"tls_id", "link_index"}:
                raise ValueError("signal_group requires expected tls_id and link_index from an evidenced binding.")
            if not isinstance(expected["tls_id"], str) or type(expected["link_index"]) is not int or expected["link_index"] < 0:
                raise ValueError("Signal binding requires a controller ID and a nonnegative link index.")
            observed = [{"tls_id": c.get("tl"), "link_index": int(c.get("linkIndex", "-1"))}
                        for c in actual.get(tuple(pair), [])]
            matches = observed == [expected]
            result["metrics"] = {"binding_match": int(matches)}
    else:
        junction = next((j for j in root.findall("junction") if j.get("id") == selector["junction_id"]), None)
        if junction is None:
            return {**result, "status": "mismatched", "reason": "network_junction_missing"}
        reference, candidate = Polygon(_points(expected, minimum=3)), Polygon(transform(_shape(junction)))
        if not reference.is_valid or not candidate.is_valid or min(reference.area, candidate.area) <= 0:
            raise ValueError("Junction surfaces must be valid polygons with positive area.")
        iou = reference.intersection(candidate).area / reference.union(candidate).area
        observed = [list(p) for p in candidate.exterior.coords]
        result["metrics"] = {"intersection_over_union": iou,
                             "symmetric_difference_m2": reference.symmetric_difference(candidate).area}
        matches = iou >= fact.minimum_iou if fact.minimum_iou is not None else None
    if fact.mapping_status == "unresolved" and mode == "release":
        return {**result, "observed": observed, "reason": "reference_to_network_mapping_unresolved"}
    if matches is None:
        if kind == "lane_centerline" and result["metrics"]["mean_lateral_offset_m"] is None:
            return {**result, "observed": observed, "reason": "no_directed_longitudinal_overlap"}
        if mode == "explore":
            return {**result, "status": "measured", "observed": observed, "reason": "acceptance_threshold_not_declared"}
        return {**result, "observed": observed, "reason": "acceptance_threshold_not_declared"}
    return {**result, "status": "matched" if matches else "mismatched", "observed": observed}


def _score(root: ET.Element, table: NetworkFactTable, mode="release") -> dict[str, Any]:
    transform = _coordinate_transform(root, table.coordinate_system)
    facts = []
    for fact in table.facts:
        try:
            facts.append(_measure(fact, root, transform, mode))
        except ValueError as error:
            if mode == "release":
                raise
            facts.append({"id": fact.id, "kind": fact.kind, "layer": _LAYERS[fact.kind],
                          "known": fact.expected is not None, "status": "unknown", "observed": None,
                          "metrics": {}, "mapping_status": fact.mapping_status,
                          "mapping_confidence": fact.mapping_confidence, "reason": str(error)})
    layers = {}
    for layer in ("geometry", "lanes", "movements", "control"):
        rows = [f for f in facts if f["layer"] == layer]
        counts = Counter(f["status"] for f in rows)
        known = sum(f["known"] for f in rows)
        layers[layer] = {"matched_facts": counts["matched"], "known_facts": known,
                         "unknown_facts": counts["unknown"], "mismatched_facts": counts["mismatched"],
                         "matched_fraction": counts["matched"] / known if known else None}
        if mode == "explore":
            judged = counts["matched"] + counts["mismatched"]
            measured = [f for f in rows if (f["metrics"].get("mean_lateral_offset_m") is not None
                        if f["kind"] == "lane_centerline" else bool(f["metrics"]))]
            layers[layer].update(measured_facts=len(measured),
                                 provisional_facts=sum(f["mapping_status"] == "unresolved" for f in measured),
                                 matched_fraction=counts["matched"] / judged if judged else None,
                                 matched_fraction_basis="measured_facts_with_declared_acceptance")
    status = "pass" if all(f["status"] == "matched" for f in facts) else "review_required"
    if not any(f["known"] for f in facts):
        status = "blocked"
    motor = [row for row in facts if row["kind"] == "lane_centerline" and row.get("traffic_mode") == "motor"
             and row["metrics"].get("mean_lateral_offset_m") is not None]
    def summary(rows):
        values = [row["metrics"]["mean_lateral_offset_m"] for row in rows]
        return {"count": len(values), "median_lateral_offset_m": median(values) if values else None,
                "p90_lateral_offset_m": quantiles(values, n=10, method="inclusive")[8] if len(values) > 1 else values[0] if values else None,
                "worst_facts": [{"id": row["id"], "mapping_confidence": row["mapping_confidence"],
                                 "metrics": row["metrics"]} for row in sorted(rows,
                    key=lambda row: (-row["metrics"]["mean_lateral_offset_m"], row["id"]))[:5]]}
    return {"status": "measured" if mode == "explore" else status, "layers": layers, "facts": facts,
            "motor_centerlines": {"all": summary(motor), "high_confidence": summary([row for row in motor
                if row["mapping_confidence"] in {"high", "confirmed"}])}}


def _numeric_deltas(before, after):
    result = {}
    for key, value in after.items():
        previous = before.get(key)
        if type(value) in (int, float) and type(previous) in (int, float):
            result[key] = value - previous
        elif isinstance(value, dict) and isinstance(previous, dict):
            result[key] = _numeric_deltas(previous, value)
    return result


def compare_calibration_scorecards(before, after):
    """Compare the same ordered physical facts after an explicit selector remap if needed."""
    if [row["id"] for row in before["facts"]] != [row["id"] for row in after["facts"]]:
        raise ValueError("Scorecard comparisons require the same ordered fact identities.")
    transitions = [{"fact_id": new["id"], "before": old["status"], "after": new["status"],
                    "metric_deltas": _numeric_deltas(old["metrics"], new["metrics"]),
                    "before_metrics": old["metrics"], "after_metrics": new["metrics"]}
                   for old, new in zip(before["facts"], after["facts"])]
    return {"transitions": transitions, "regressed_facts": [row["fact_id"] for row in transitions
            if row["before"] == "matched" and row["after"] != "matched"]}


def evaluate_network_calibration(*, net_file: str | Path, facts_file: str | Path,
                                 output_file: str | Path, parent_net_file: str | Path | None = None,
                                 mode: Literal["explore", "release"] = "explore") -> dict[str, Any]:
    """Write one new scorecard. Compare a parent only against the same fact table."""
    network, facts_path, output = Path(net_file).resolve(), Path(facts_file).resolve(), Path(output_file).resolve()
    if mode not in {"explore", "release"}:
        raise ValueError("Mode must be explore or release.")
    warnings = []
    network_bytes, facts_bytes = network.read_bytes(), facts_path.read_bytes()
    table = NetworkFactTable.model_validate_json(facts_bytes)
    identities = {str(network): hashlib.sha256(network_bytes).hexdigest(),
                  str(facts_path): hashlib.sha256(facts_bytes).hexdigest()}
    if len({f.id for f in table.facts}) != len(table.facts) or len({s.id for s in table.sources}) != len(table.sources):
        raise ValueError("Fact and source identifiers must be unique.")
    source_ids = {s.id for s in table.sources}
    if any(set(f.source_ids) - source_ids for f in table.facts):
        raise ValueError("A fact references an undeclared source.")
    paths = {network, facts_path}
    sources = []
    for source in table.sources:
        path = (facts_path.parent / source.path).resolve()
        if path == output:
            raise ValueError("Use a new scorecard path separate from every input.")
        if not path.is_file() and mode == "explore":
            warnings.append(f"Source unavailable: {source.id}")
            sources.append({**source.model_dump(mode="json"), "path": str(path), "observed_sha256": None})
            continue
        digest = _sha(path)
        if digest != source.sha256:
            if mode == "release":
                raise ValueError(f"Source hash mismatch: {source.id}")
            warnings.append(f"Source hash mismatch: {source.id}")
        paths.add(path)
        if str(path) in identities and identities[str(path)] != digest:
            raise ValueError("An input changed before source validation.")
        identities[str(path)] = digest
        sources.append({**source.model_dump(mode="json"), "path": str(path), "observed_sha256": digest})
    parent = Path(parent_net_file).resolve() if parent_net_file is not None else None
    parent_bytes = None
    if parent is not None:
        parent_bytes = parent.read_bytes()
        digest = hashlib.sha256(parent_bytes).hexdigest()
        if str(parent) in identities and identities[str(parent)] != digest:
            raise ValueError("An input changed before parent validation.")
        identities[str(parent)] = digest
        paths.add(parent)
    if output in paths or output.exists():
        raise ValueError("Use a new scorecard path separate from every input.")
    root = _network_root(network_bytes)
    location = root.find("location")
    report = {"schema": "astra.network-calibration/v1", "site_id": table.site_id,
              "target_date": table.target_date.isoformat(), "coordinate_system": table.coordinate_system,
              "inputs": identities, "sources": sources, "network_file": str(network),
              "network_location": dict(location.attrib) if location is not None else {},
              **(_score(root, table) if mode == "release" else _score(root, table, mode)),
              "mode": mode, "warnings": warnings, "promotion_authority": False,
              "claim_status": "reference_comparison_only",
              "limitations": ["Facts apply only to the declared site, date and selected objects.",
                              "Centerline offsets sample the reference at the declared spacing; maxima are sampled.",
                              "Lateral offsets exclude uncovered tails and gaps. Coverage uses aligned segment projections at interval midpoints.",
                              "Explore matches use tentative correspondence and are not release acceptance.",
                              "Movement metrics compare direct external-lane connections, not arbitrary multi-edge paths.",
                              "Stop-line offsets compare the geometric lane end, not vehicle-class stopping offsets.",
                              "A passing scorecard does not certify traffic safety or field correctness."]}
    if parent is not None:
        previous = (_score(_network_root(parent_bytes), table) if mode == "release"
                    else _score(_network_root(parent_bytes), table, mode))
        report["parent"] = {"network_file": str(parent), **previous}
        report["comparison"] = compare_calibration_scorecards(previous, report)
    if identities != {str(path): _sha(path) for path in paths}:
        if mode == "release":
            raise ValueError("An input changed while the scorecard was computed.")
        warnings.append("An input changed while the scorecard was computed. Recorded hashes identify the measured snapshots.")
    write_json_atomic(output, report, sort_keys=True)
    return {**report, "output_file": str(output)}
