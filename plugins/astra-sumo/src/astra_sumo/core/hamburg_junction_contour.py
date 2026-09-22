"""Conservative local contour proposals with continuous lane-surface guards."""

from __future__ import annotations

import math
from xml.etree import ElementTree as ET

from .connection_mode_audit import _MOTORIZED_MODES
from .junction_footprint import _convex_hull
from .surface_overlap_audit import (
    _bbox, _bboxes_overlap, _convex_polygon_intersection, _cross_points,
    _deduplicate_polyline, _ensure_counter_clockwise, _lane_face_primitives,
    _normalize_polygon, _parse_shape, _signed_area, _triangulate_polygon,
    polygon_self_intersection_count,
)

_EPS = 1e-9
_TOLERANCE_M = 0.1


def _segment_distance(point, start, end):
    dx, dy = end[0] - start[0], end[1] - start[1]
    denominator = dx * dx + dy * dy
    t = max(0.0, min(1.0, ((point[0] - start[0]) * dx + (point[1] - start[1]) * dy) / denominator)) if denominator else 0.0
    return math.dist(point, (start[0] + t * dx, start[1] + t * dy))


def _distance(point, polygon):
    inside = False
    for a, b in zip(polygon, polygon[1:] + polygon[:1]):
        if (a[1] > point[1]) != (b[1] > point[1]) and point[0] < a[0] + (point[1] - a[1]) * (b[0] - a[0]) / (b[1] - a[1]):
            inside = not inside
    return 0.0 if inside else min(_segment_distance(point, a, b) for a, b in zip(polygon, polygon[1:] + polygon[:1]))


def _polygon(points):
    values = _normalize_polygon([tuple(map(float, point)) for point in points])
    if len(values) < 3 or any(len(p) != 2 or not all(map(math.isfinite, p)) for p in values):
        raise ValueError("polygon needs at least three finite points")
    if len(set(values)) != len(values) or polygon_self_intersection_count(values):
        raise ValueError("polygon repeats a vertex or intersects itself")
    for i, point in enumerate(values):
        if any(_segment_distance(point, a, b) <= _EPS for j, (a, b) in enumerate(zip(values, values[1:] + values[:1])) if i not in (j, (j + 1) % len(values))):
            raise ValueError("polygon touches a nonadjacent edge")
    _triangulate_polygon(values)
    if abs(_signed_area(values)) <= _EPS:
        raise ValueError("polygon has zero area")
    values = _ensure_counter_clockwise(values)
    first = min(range(len(values)), key=lambda i: values[i])
    return values[first:] + values[:first]


def _motorized(lane):
    allow, deny = set(lane.get("allow", "").split()), set(lane.get("disallow", "").split())
    return "all" not in deny and bool(((_MOTORIZED_MODES if not allow or "all" in allow else allow & _MOTORIZED_MODES) - deny))


def _expanded(cell):
    # A square contains the Euclidean 0.1 m neighbourhood; this guard is conservative, not a relaxed tolerance.
    return _convex_hull([(x + dx, y + dy) for x, y in cell for dx in (-_TOLERANCE_M, _TOLERANCE_M) for dy in (-_TOLERANCE_M, _TOLERANCE_M)])


def _inputs(root, junction_id):
    junction = next((j for j in root.findall("junction") if j.get("id") == junction_id), None)
    if junction is None:
        raise ValueError("target junction is absent")
    raw = _parse_shape(junction.get("shape", ""))
    origin = raw[0] if raw else (0.0, 0.0)
    def local(points):
        return [(x - origin[0], y - origin[1]) for x, y in points]
    source_repair = None
    try:
        before = _polygon(local(raw))
    except ValueError as error:
        from shapely import LineString, Polygon, make_valid
        from shapely.errors import GEOSException

        # SUMO stores one exterior ring. An imported ring can loop through its
        # own interior. Keep its whole outer surface, never the largest of
        # several disconnected polygons or a surveyed-island interpretation.
        try:
            repaired = make_valid(Polygon(local(raw)))
            parts = list(repaired.geoms) if hasattr(repaired, "geoms") else [repaired]
            polygons = [part for part in parts if part.geom_type == "Polygon"]
            if len(polygons) != 1:
                raise error
            outer = Polygon(polygons[0].exterior)
            if not outer.buffer(_EPS).covers(repaired) or not outer.buffer(_EPS).covers(LineString(local(raw + raw[:1]))):
                raise error
            before = _polygon(list(outer.exterior.coords))
            source_repair = {"method": "single_surface_outer_ring/v1", "no_source_component_discarded": True,
                             "filled_unclassified_loop_area_m2": outer.area - polygons[0].area,
                             "claim_boundary": "Repairs an invalid model ring. Filled loops are not identified as field traffic islands."}
        except (GEOSException, TypeError):
            raise error from None
    lanes, mouths = [], []
    for edge in root.findall("edge"):
        internal = edge.get("id", "").startswith(f":{junction_id}_")
        roles = [role for role, key in (("ingress", "to"), ("egress", "from")) if edge.get(key) == junction_id]
        if not internal and not roles:
            continue
        if edge.get("function") == "walkingarea":
            raise NotImplementedError("walkingarea requires a separate face contract; original boundary retained")
        for lane in edge.findall("lane"):
            points = _deduplicate_polyline(local(_parse_shape(lane.get("shape", ""))))
            width = float(lane.get("width", "3.2"))
            if len(points) < 2 or not math.isfinite(width) or width <= 0:
                raise ValueError(f"invalid motor lane geometry: {lane.get('id')}")
            if internal:
                lanes.append({"id": lane.get("id"), "width": width, "motorized": _motorized(lane), "cells": _lane_face_primitives(points, width)})
            for role in roles:
                center, neighbor = (points[-1], points[-2]) if role == "ingress" else (points[0], points[1])
                length = math.dist(center, neighbor)
                direction = tuple((b - a) / length for a, b in zip(center, neighbor))
                normal = (-direction[1], direction[0])
                section = [tuple(center[i] + sign * normal[i] * width / 2 for i in (0, 1)) for sign in (-1, 1)]
                guard = _expanded(section)
                mouths.append({"id": lane.get("id"), "role": role, "section": section, "guard": guard})
    return raw, origin, before, lanes, mouths, source_repair


def _pieces(subject, triangles):
    bounds = _bbox(subject)
    return [piece for triangle, box in triangles if _bboxes_overlap(bounds, box)
            and (piece := _convex_polygon_intersection(subject, triangle))]


def _segment_covered(subject, pieces):
    a, b = subject
    delta = (b[0] - a[0], b[1] - a[1])
    length2 = delta[0] ** 2 + delta[1] ** 2
    intervals = []
    for piece in pieces:
        positions = [sum((p[i] - a[i]) * delta[i] for i in (0, 1)) / length2 for p in piece]
        intervals.append((min(positions), max(positions)))
    reached = 0.0
    for start, end in sorted(intervals):
        if start > reached + _EPS:
            return False
        reached = max(reached, end)
    return reached >= 1 - _EPS


def _samples(subject):
    samples = subject + [tuple(sum(p[i] for p in subject) / len(subject) for i in (0, 1))]
    samples += [tuple((a[i] + b[i]) / 2 for i in (0, 1)) for a, b in zip(subject, subject[1:] + subject[:1])]
    return samples


def _half_plane(subject, a, b):
    result = []
    if not subject:
        return result
    previous = subject[-1]
    before = _cross_points(a, b, previous)
    for current in subject:
        after = _cross_points(a, b, current)
        if (before >= -_EPS) != (after >= -_EPS):
            fraction = before / (before - after)
            result.append(tuple(previous[i] + fraction * (current[i] - previous[i]) for i in (0, 1)))
        if after >= -_EPS:
            result.append(current)
        previous, before = current, after
    return _normalize_polygon(result)


def _outside_fragments(subject, triangles):
    """Subtract disjoint target triangles, retaining continuous convex pieces."""
    if len(subject) == 2:
        a, b = subject
        delta = tuple(b[i] - a[i] for i in (0, 1))
        length2 = sum(value * value for value in delta)
        intervals = []
        for piece in _pieces(subject, triangles):
            values = [sum((p[i] - a[i]) * delta[i] for i in (0, 1)) / length2 for p in piece]
            intervals.append((max(0.0, min(values)), min(1.0, max(values))))
        missing, reached = [], 0.0
        for start, end in [*sorted(intervals), (1.0, 1.0)]:
            if start > reached + _EPS:
                missing.append([tuple(a[i] + position * delta[i] for i in (0, 1)) for position in (reached, start)])
            reached = max(reached, end)
        return missing
    fragments = [subject]
    for triangle, bounds in triangles:
        remaining = []
        for fragment in fragments:
            if not _bboxes_overlap(_bbox(fragment), bounds):
                remaining.append(fragment)
                continue
            inside = fragment
            for a, b in zip(triangle, triangle[1:] + triangle[:1]):
                outside = _half_plane(inside, b, a)
                if len(outside) > 2 and abs(_signed_area(outside)) > _EPS:
                    remaining.append(outside)
                inside = _half_plane(inside, a, b)
                if not inside:
                    break
        fragments = remaining
        if not fragments:
            break
    return fragments


def _coverage(subject, polygon, triangles, convex, tolerance=_TOLERANCE_M):
    samples = _samples(subject)
    distances = [_distance(point, polygon) for point in samples]
    maximum = max(distances)
    if maximum > tolerance + _EPS:
        return {"status": "blocked", "witness": samples[distances.index(maximum)], "distance_m": maximum}
    # Distance to a convex set is convex, so its maximum over a convex cell is at a vertex.
    if convex or len(subject) == 1:
        return {"status": "pass", "proof": "convex_distance_bound"}
    pieces = _pieces(subject, triangles)
    full = (_segment_covered(subject, pieces) if len(subject) == 2 else
            abs(abs(_signed_area(subject)) - sum(abs(_signed_area(p)) for p in pieces)) <= _EPS * max(1, len(pieces)))
    if full:
        return {"status": "pass", "proof": "continuous_polygon_intersection"}
    if any(all(_segment_distance(point, a, b) <= tolerance + _EPS for point in subject) for a, b in zip(polygon, polygon[1:] + polygon[:1])):
        return {"status": "pass", "proof": "one_boundary_segment_capsule_contains_cell"}
    outside = _outside_fragments(subject, triangles)
    # Each remaining convex piece must fit inside one boundary-segment capsule.
    # This proves the entire sliver is close, rather than relying on its area.
    if all(any(all(_segment_distance(point, a, b) <= tolerance + _EPS for point in piece)
                   for a, b in zip(polygon, polygon[1:] + polygon[:1])) for piece in outside):
        return {"status": "pass", "proof": "continuous_outside_fragments_in_boundary_capsules"}
    from shapely import LineString, Polygon

    # Several boundary capsules can jointly cover a convex piece even though
    # no single capsule covers it. An inscribed buffer proves that union
    # without increasing the declared Euclidean tolerance.
    region = Polygon(polygon).buffer(tolerance, quad_segs=64) if tolerance else Polygon(polygon)
    cell = Polygon(subject) if len(subject) > 2 else LineString(subject)
    if region.covers(cell):
        return {"status": "pass", "proof": "inscribed_euclidean_buffer_contains_complete_cell"}
    return {"status": "review_required", "reason": "concave_coverage_not_certified"}


def _regression(subject, before, before_triangles, after, after_triangles, convex):
    coverage = _coverage(subject, after, after_triangles, convex)
    if coverage["status"] == "pass":
        return coverage
    for point in _samples(subject):
        if _distance(point, before) <= _TOLERANCE_M + _EPS and _distance(point, after) > _TOLERANCE_M + _EPS:
            return {"status": "blocked", "reason": "previously_qualified_support_lost", "witness": point,
                    "before_distance_m": _distance(point, before), "after_distance_m": _distance(point, after)}
    # Preserve the old nearest-point region for every tolerance-qualified support point, without adding tolerance again.
    near = _pieces(_expanded(subject), before_triangles)
    if all(_coverage(piece, after, after_triangles, convex, tolerance=0)["status"] == "pass" for piece in near):
        return {"status": "pass", "proof": "old_near_support_region_continuously_preserved"}
    for piece in _pieces(subject, before_triangles):
        loss = _coverage(piece, after, after_triangles, convex)
        if loss["status"] == "blocked":
            return loss
    from shapely import LineString, Point, Polygon

    # Certify the actual Euclidean tolerance condition when the square guard
    # is inconclusive. The source buffer circumscribes the true neighbourhood;
    # the candidate buffer is inscribed. A pass cannot accumulate tolerances.
    segments = 64
    source_radius = _TOLERANCE_M / math.cos(math.pi / (4 * segments))
    cell = Polygon(subject) if len(subject) > 2 else LineString(subject) if len(subject) == 2 else Point(subject[0])
    old_near = Polygon(before).intersection(cell.buffer(source_radius, quad_segs=segments))
    if old_near.is_empty or Polygon(after).covers(old_near):
        return {"status": "pass", "proof": "conservative_euclidean_nearest_region_containment"}
    qualified = cell.intersection(Polygon(before).buffer(source_radius, quad_segs=segments))
    if qualified.is_empty or Polygon(after).buffer(_TOLERANCE_M, quad_segs=segments).covers(qualified):
        return {"status": "pass", "proof": "conservative_euclidean_qualified_support_containment"}
    return {"status": "review_required", "reason": "old_tolerance_band_preservation_not_certified"}


def audit_junction_contour(network_root: ET.Element, junction_id: str, proposed_shape) -> dict:
    """Audit continuous geometry; mouth containment is separate from native cut positions."""
    report = {"status": "blocked", "preservation_pass": False, "geometry_preservation_pass": False, "tolerance_m": _TOLERANCE_M,
              "quality_status": "review_required", "external_cut_status": "not_checked", "contained_within_source_tolerance": False}
    try:
        _, origin, before, lanes, mouths, source_repair = _inputs(network_root, junction_id)
        after = _polygon([(float(x) - origin[0], float(y) - origin[1]) for x, y in proposed_shape])
    except NotImplementedError as error:
        return {**report, "status": "review_required", "mathematically_valid": None, "reasons": [str(error)]}
    except (ValueError, TypeError) as error:
        return {**report, "mathematically_valid": False, "reasons": [str(error)]}
    triangles = [(p, _bbox(p)) for p in _triangulate_polygon(after)]
    before_triangles = [(p, _bbox(p)) for p in _triangulate_polygon(before)]
    convex = all(_cross_points(after[i - 1], after[i], after[(i + 1) % len(after)]) >= -_EPS for i in range(len(after)))
    before_convex = all(_cross_points(before[i - 1], before[i], before[(i + 1) % len(before)]) >= -_EPS for i in range(len(before)))
    same = before == after
    absolute, mouth_checks, regression = [], [], []
    for row in lanes:
        for cell in row["cells"]:
            absolute.append({"lane_id": row["id"], **_coverage(cell, after, triangles, convex)})
            if not same:
                regression.append({"lane_id": row["id"], **_regression(cell, before, before_triangles, after, triangles, convex)})
    for row in mouths:
        mouth_checks.append({"lane_id": row["id"], **_coverage(row["section"], after, triangles, convex)})
        if not same:
            regression.append({"mouth_lane_id": row["id"], **_regression(row["section"], before, before_triangles, after, triangles, convex)})
    containment = [_coverage(triangle, before, before_triangles, before_convex) for triangle, _ in triangles]
    conditions = regression + containment
    status = "blocked" if any(r["status"] == "blocked" for r in conditions) else "review_required" if any(r["status"] != "pass" for r in conditions) or not lanes else "pass"
    return {**report, "source_polygon_repair": source_repair, "status": status, "regression_status": status, "preservation_pass": status == "pass", "geometry_preservation_pass": status == "pass",
            "mathematically_valid": True, "same_geometry": same, "support_widths_m": {r["id"]: r["width"] for r in lanes},
            "support_modes": {r["id"]: "motorized" if r["motorized"] else "nonmotorized" for r in lanes},
            "contained_within_source_tolerance": all(r["status"] == "pass" for r in containment), "source_containment_checks": containment,
            "absolute_coverage_status": "pass" if absolute and all(r["status"] == "pass" for r in absolute) else "review_required",
            "mouth_coverage_status": "pass" if all(r["status"] == "pass" for r in mouth_checks) else "review_required",
            "absolute_coverage_checks": absolute, "mouth_coverage_checks": mouth_checks, "regression_checks": regression,
            "witness_coordinate_origin": list(origin),
            "reasons": ["Coverage does not prove fixed external cut positions. Native rebuild and imagery checks remain required."],
            "blank_region_policy": "No inferred support gap is labelled a surveyed traffic island."}


def _outward_grid_point(point, a, b, origin):
    """Use SUMO's centimetre output grid without trimming the retained edge."""
    axes = [[value / 100 for value in {math.floor((point[i] + origin[i]) * 100),
                                    math.ceil((point[i] + origin[i]) * 100)}] for i in (0, 1)]
    candidates = [(x - origin[0], y - origin[1]) for x in axes[0] for y in axes[1]]
    outside = [p for p in candidates if _cross_points(a, b, p) <= _EPS]
    return min(outside, key=lambda p: (math.dist(point, p), p))


def _surface_grid_polygon(polygon, source, origin):
    """Round new points outward at retained boundary segments, on SUMO's grid."""
    from shapely import LineString, Point, Polygon, make_valid

    edges = list(source.exterior.coords)
    result = []
    for point in polygon.exterior.coords[:-1]:
        segments = [(a, b) for a, b in zip(edges, edges[1:])
                    if LineString([a, b]).distance(Point(point)) <= 1e-8]
        axes = [{math.floor((point[i] + origin[i]) * 100),
                 math.ceil((point[i] + origin[i]) * 100)} for i in (0, 1)]
        choices = [(x / 100 - origin[0], y / 100 - origin[1]) for x in axes[0] for y in axes[1]]
        outside = [p for p in choices if all(_cross_points(a, b, p) <= 1e-8 for a, b in segments)]
        if not outside:
            raise ValueError("no outward centimetre-grid point at a retained boundary")
        chosen = min(outside, key=lambda p: (math.dist(point, p), p))
        if not result or result[-1] != chosen:
            result.append(chosen)
    # Rounding may collapse sub-centimetre spikes. All resulting polygon parts
    # must still be present; support and native compilation are checked below.
    if len(set(result)) < 3:
        return Polygon()
    return make_valid(Polygon(result), method="structure", keep_collapsed=False)


def propose_fused_junction_contour(network_root: ET.Element, junction_id: str, *, corner_radius_m: float = 8.0, native_boundary_refit: bool = False) -> dict:
    """Round the movement surface while keeping the original road-mouth edges."""
    from shapely import LineString, Point, Polygon, set_precision, union_all
    from shapely.errors import GEOSException

    if isinstance(corner_radius_m, bool) or not isinstance(corner_radius_m, (int, float)) or not math.isfinite(corner_radius_m) or corner_radius_m <= 0:
        raise ValueError("junction_corner_radius_m must be finite and positive")
    if not isinstance(native_boundary_refit, bool):
        raise ValueError("native_boundary_refit must be boolean")
    surface = {"method": "smooth_surface_with_preserved_mouth_edges/v2", "status": "review_required",
               "changed": False, "lane_surface_margin_m": .25, "closing_radius_m": corner_radius_m,
               "mouth_edge_collar_m": .15, "simplify_m": .02, "output_grid_m": .01,
               "native_boundary_roundoff_margin_m": .02,
               "native_boundary_refit": native_boundary_refit,
               "radius_basis": "Configurable model outline radius, not a surveyed curb radius.",
               "claim_boundary": "The envelope follows constructed lane curves. It does not identify field curbs or islands."}
    try:
        raw, origin, before, lanes, mouths, source_repair = _inputs(network_root, junction_id)
        if not lanes:
            raise ValueError("no internal lane surfaces")
        source = Polygon(before)
        support = union_all([Polygon(cell) for row in lanes for cell in row["cells"]]
                            + [Polygon(row["guard"]) for row in mouths])
        edges = [LineString([a, b]) for a, b in zip(before, before[1:] + before[:1])]
        cuts = [min(edges, key=lambda edge: edge.distance(Point(tuple((a + b) / 2 for a, b in zip(*row["section"])))))
                for row in mouths]
        # Keep whole original cut edges. Inserting rounded points along a cut
        # changes its slope and can shift the compiled lane end by a centimetre.
        mouth_zone = union_all([edge.buffer(.15, cap_style="square") for edge in cuts])
        # The 0.25 m proposal margin covers the existing 0.1 m support guard,
        # simplification and centimetre rounding. It is not an acceptance tolerance.
        surface_support = support.buffer(.25)
        if not native_boundary_refit:
            surface_support = surface_support.union(source.intersection(mouth_zone))
        envelope = surface_support.buffer(corner_radius_m).buffer(-corner_radius_m).simplify(.02, preserve_topology=True)
        if native_boundary_refit:
            protected = union_all([Polygon(_expanded(cell)) for row in lanes for cell in row["cells"]]
                                  + [Polygon(row["guard"]) for row in mouths]).intersection(source)
            envelope = envelope.union(protected.buffer(.02))
        # Native collinear-point removal can cut a sub-millimetre sliver from
        # a rounded outline. Leave headroom outside non-mouth boundary edges;
        # the unchanged 0.1 m audit still checks the compiled result.
        fused = (envelope if native_boundary_refit else envelope.difference(mouth_zone).union(source.intersection(mouth_zone))).intersection(source.buffer(.02))
        polygons = list(fused.geoms) if fused.geom_type == "MultiPolygon" else [fused]
        if any(part.geom_type != "Polygon" for part in polygons):
            raise ValueError("the surface contains non-polygon geometry")
        interior_area = sum(Polygon(part.exterior).area - part.area for part in polygons)
        # Keep every polygon through native-grid rounding; never select the largest.
        # The continuous support audit rejects loss of a required narrow part.
        fused = set_precision(union_all([_surface_grid_polygon(part, source, origin) for part in polygons]), .01)
        if fused.geom_type == "MultiPolygon" and len(fused.geoms) == 1:
            fused = fused.geoms[0]
        if fused.geom_type != "Polygon" or not fused.is_valid or fused.is_empty:
            raise ValueError("the rounded surface is not one valid polygon")
        shape = [(round(x + origin[0], 2), round(y + origin[1], 2)) for x, y in fused.exterior.coords[:-1]]
        audit = audit_junction_contour(network_root, junction_id, shape)
        surface.update(source_shape=raw, source_polygon_repair=source_repair, proposed_shape=shape, before_area_m2=source.area,
                       after_area_m2=fused.area, protected_lane_ids=[row["id"] for row in lanes],
                       protected_mouth_count=len(mouths), unclassified_interior_area_m2=interior_area,
                       surface_audit=audit, status=audit["status"])
        if audit["preservation_pass"] and (fused.area < source.area or native_boundary_refit):
            return {**surface, "changed": True}
        surface["reasons"] = ["surface preservation was not certified; try the existing local contour method"]
    except (ValueError, TypeError, NotImplementedError, GEOSException) as error:
        surface.update(status="review_required", reasons=[str(error)])
    fallback = propose_junction_contour(network_root, junction_id)
    return {**fallback, "preferred_method": surface["method"], "surface_proposal": surface}


def propose_junction_contour(network_root: ET.Element, junction_id: str) -> dict:
    """Tighten provably empty corner triangles without expanding or selecting components."""
    junction = next((j for j in network_root.findall("junction") if j.get("id") == junction_id), None)
    raw = _parse_shape(junction.get("shape", "")) if junction is not None else []
    report = {"status": "blocked", "changed": False, "source_shape": raw, "proposed_shape": raw,
              "before_area_m2": None, "after_area_m2": None, "method": "analytic_guarded_corner_tightening/v1"}
    try:
        raw, origin, before, lanes, mouths, source_repair = _inputs(network_root, junction_id)
    except NotImplementedError as error:
        return {**report, "status": "review_required", "reasons": [str(error)]}
    except (ValueError, TypeError) as error:
        return {**report, "reasons": [str(error)]}
    cells = [_expanded(cell) for row in lanes for cell in row["cells"]] + [r["guard"] for r in mouths]
    area_before = abs(_signed_area(before))
    report.update(source_polygon_repair=source_repair, before_area_m2=area_before, after_area_m2=area_before, protected_lane_ids=sorted(r["id"] for r in lanes),
                  protected_mouth_count=len(mouths), guard_depth_m=_TOLERANCE_M,
                  output_grid_m=0.01,
                  support_guard="L-infinity 0.1 m expansion contains the Euclidean tolerance neighbourhood")
    # ponytail: bounded one-pass local tightening; larger contours need a reviewed method, not a general optimizer.
    if not lanes or len(before) > 256 or len(cells) > 4096:
        return {**report, "status": "review_required", "reasons": ["missing lane support or local geometry limit exceeded"]}
    protected = [(cell, _bbox(cell)) for cell in cells]
    remaining = list(enumerate(before))
    cuts = []
    for vertex in range(len(before)):
        if len(remaining) <= 3:
            break
        index = next(i for i, (label, _) in enumerate(remaining) if label == vertex)
        previous, current, following = (remaining[i % len(remaining)][1] for i in (index - 1, index, index + 1))
        if _cross_points(previous, current, following) <= _EPS:
            continue
        other_points = [point for i, (_, point) in enumerate(remaining) if i not in ((index - 1) % len(remaining), index, (index + 1) % len(remaining))]
        def ear_at(fraction):
            ear = [tuple(current[i] + fraction * (end[i] - current[i]) for i in (0, 1)) for end in (previous, current, following)]
            if fraction not in (0, 1):
                ear[0] = _outward_grid_point(ear[0], previous, current, origin)
                ear[2] = _outward_grid_point(ear[2], current, following, origin)
            return ear
        def safe(fraction):
            ear = ear_at(fraction)
            if _cross_points(*ear) <= _EPS:
                return False
            if any(all(_cross_points(ear[j], ear[(j + 1) % 3], point) >= -_EPS for j in range(3)) for point in other_points):
                return False
            box = _bbox(ear)
            return not any(_bboxes_overlap(box, bounds) and _convex_polygon_intersection(ear, cell) for cell, bounds in protected)
        fraction = 1.0
        if not safe(fraction):
            low, high = 0.0, 1.0
            for _ in range(28):
                middle = (low + high) / 2
                if safe(middle):
                    low = middle
                else:
                    high = middle
            fraction = low
        ear = ear_at(fraction)
        if abs(_signed_area(ear)) <= _EPS:
            continue
        cuts.append({"removed_vertex": [current[i] + origin[i] for i in (0, 1)], "removed_area_m2": abs(_signed_area(ear)),
                     "fraction": fraction, "kind": "full_ear" if fraction == 1 else "partial_ear"})
        remaining[index:index + 1] = [] if fraction == 1 else [(None, ear[0]), (None, ear[2])]
    after = [point for _, point in remaining]
    try:
        _polygon(after)
    except ValueError as error:
        return {**report, "reasons": ["proposed boundary failed validation", str(error)]}
    proposed = [(x + origin[0], y + origin[1]) for x, y in after] if cuts else raw
    checked = audit_junction_contour(network_root, junction_id, proposed) if cuts else None
    if checked is not None and not checked["preservation_pass"]:
        return {**report, "status": "review_required", "attempted_shape": proposed, "grid_checks": checked,
                "reasons": ["rounded corner preservation could not be certified; original boundary retained"]}
    return {**report, "status": "pass" if cuts else "not_applicable", "changed": bool(cuts),
            "proposed_shape": proposed,
            "after_area_m2": abs(_signed_area(after)), "cuts": cuts,
            "checks": {"zero_protected_intersection": True, "contained_within_source_tolerance": True,
                       "no_component_discarded": True, "all_mouth_profiles_protected": True},
            "grid_rounding_policy": "Round new edge points outward onto the native output grid, then certify "
                                    "actual coverage and source containment at the unchanged 0.1 m tolerance.",
            "reasons": [] if cuts else ["no provably empty ear; original boundary retained"],
            "quality_status": "review_required", "external_cut_status": "not_checked"}
