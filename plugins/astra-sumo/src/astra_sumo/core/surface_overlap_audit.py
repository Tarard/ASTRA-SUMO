from __future__ import annotations

import hashlib
import json
import math
import xml.etree.ElementTree as ET
from pathlib import Path
from collections.abc import Mapping
from typing import Any, Iterable, Sequence


Point = tuple[float, float]
Polygon = list[Point]

SUMO_DEFAULT_LANE_WIDTH_M = 3.2
SURFACE_OVERLAP_AUDIT_SCHEMA = "torii.sumo-surface-overlap-audit/v1"
SURFACE_OVERLAP_COMPARISON_SCHEMA = "torii.sumo-surface-overlap-comparison/v1"
_GEOMETRY_EPSILON = 1e-9


def audit_sumo_lane_junction_surface_overlaps(
    net_file: Path,
    *,
    minimum_overlap_area_m2: float = 0.01,
    default_lane_width_m: float = SUMO_DEFAULT_LANE_WIDTH_M,
    report_file: Path | None = None,
) -> dict[str, Any]:
    """Audit junction polygons and reconstructed external-lane faces.

    SUMO's ``netconvert --geometry.check-overlap`` diagnoses overlapping edges,
    but does not claim to inspect junction polygons.  This complementary,
    read-only audit checks:

    * polygon-area overlap between distinct, non-internal junctions; and
    * a bevel-strip reconstruction of each external lane face against every
      non-owner junction polygon.

    A lane's ``from`` and ``to`` junctions are intentionally excluded because a
    lane face terminating at its owner junction is normal SUMO geometry.
    Internal lanes and internal junctions are also excluded because their faces
    are expected to lie within (and conflict inside) a junction polygon.
    """

    source = net_file.resolve()
    if not math.isfinite(minimum_overlap_area_m2) or minimum_overlap_area_m2 <= 0:
        raise ValueError("minimum_overlap_area_m2 must be finite and greater than zero")
    if not math.isfinite(default_lane_width_m) or default_lane_width_m <= 0:
        raise ValueError("default_lane_width_m must be finite and greater than zero")
    if report_file is not None and report_file.resolve() == source:
        raise ValueError("report_file must not overwrite source network")
    if not source.is_file():
        report = _base_report(
            source,
            minimum_overlap_area_m2=minimum_overlap_area_m2,
            default_lane_width_m=default_lane_width_m,
        )
        report["error"] = "source network file does not exist"
        if report_file is not None:
            _write_report(report_file, report)
        return report

    source_sha256 = _sha256_file(source)
    report = _base_report(
        source,
        minimum_overlap_area_m2=minimum_overlap_area_m2,
        default_lane_width_m=default_lane_width_m,
    )
    report["source_sha256"] = source_sha256

    try:
        root = ET.parse(source).getroot()
        junctions, junction_errors, non_area_junctions = _read_junction_polygons(root)
        lanes, lane_errors = _read_external_lanes(root, default_lane_width_m)
        geometry_errors = junction_errors + lane_errors

        junction_findings = _junction_overlap_findings(
            junctions,
            minimum_overlap_area_m2=minimum_overlap_area_m2,
        )
        lane_findings = _lane_non_owner_junction_findings(
            lanes,
            junctions,
            minimum_overlap_area_m2=minimum_overlap_area_m2,
        )
        report.update(
            {
                "audited_junction_count": len(junctions),
                "non_area_junction_exclusion_count": len(non_area_junctions),
                "non_area_junction_exclusions": non_area_junctions,
                "audited_external_lane_count": len(lanes),
                "junction_pair_candidate_count": len(junctions) * (len(junctions) - 1) // 2,
                "junction_junction_overlap_count": len(junction_findings),
                "junction_junction_overlaps": junction_findings,
                "external_lane_non_owner_junction_overlap_count": len(lane_findings),
                "external_lane_non_owner_junction_overlaps": lane_findings,
                "geometry_error_count": len(geometry_errors),
                "geometry_errors": geometry_errors,
            }
        )
    except (ET.ParseError, OSError, ValueError) as exc:
        report["error"] = f"{type(exc).__name__}: {exc}"

    source_unchanged = source.is_file() and _sha256_file(source) == source_sha256
    report["source_network_mutation"] = not source_unchanged
    report["status"] = (
        "pass"
        if source_unchanged
        and not report.get("error")
        and report.get("geometry_error_count") == 0
        and report.get("junction_junction_overlap_count") == 0
        and report.get("external_lane_non_owner_junction_overlap_count") == 0
        else "fail"
    )
    if report_file is not None:
        destination = report_file.resolve()
        _write_report(destination, report)
        report["report_file"] = str(destination)
        report["report_sha256"] = _sha256_file(destination)
    return report


def compare_sumo_surface_overlap_reports(
    baseline_report: Mapping[str, Any],
    candidate_report: Mapping[str, Any],
    *,
    focus_junction_ids: Iterable[str],
    report_file: Path | None = None,
) -> dict[str, Any]:
    """Compare a bounded rebuild with its baseline without hiding inherited defects.

    The comparison passes only when the candidate introduces no new finding and
    has no finding related to the explicit focus junctions.  Findings elsewhere
    remain visible as inherited global defects; a comparison pass therefore does
    not rewrite either input audit's global status.
    """

    for label, report in (("baseline", baseline_report), ("candidate", candidate_report)):
        if report.get("schema") != SURFACE_OVERLAP_AUDIT_SCHEMA:
            raise ValueError(f"{label}_report is not a {SURFACE_OVERLAP_AUDIT_SCHEMA} report")
        if report.get("source_network_mutation") is not False:
            raise ValueError(f"{label}_report does not prove source immutability")
        if report.get("geometry_error_count") != 0:
            raise ValueError(f"{label}_report contains geometry errors")

    focus = sorted({str(identifier).strip() for identifier in focus_junction_ids if str(identifier).strip()})
    if not focus:
        raise ValueError("focus_junction_ids must contain at least one junction id")
    focus_set = set(focus)
    baseline_findings = _indexed_findings(baseline_report)
    candidate_findings = _indexed_findings(candidate_report)
    baseline_keys = set(baseline_findings)
    candidate_keys = set(candidate_findings)
    introduced_keys = sorted(candidate_keys - baseline_keys)
    resolved_keys = sorted(baseline_keys - candidate_keys)
    inherited_keys = sorted(candidate_keys & baseline_keys)
    candidate_focus_keys = [
        key for key in sorted(candidate_keys) if _finding_touches_focus(candidate_findings[key], focus_set)
    ]
    baseline_focus_keys = [
        key for key in sorted(baseline_keys) if _finding_touches_focus(baseline_findings[key], focus_set)
    ]
    changed_inherited = []
    for key in inherited_keys:
        before_area = float(baseline_findings[key]["overlap_area_m2"])
        after_area = float(candidate_findings[key]["overlap_area_m2"])
        delta = after_area - before_area
        if abs(delta) > 1e-6:
            changed_inherited.append(
                {
                    "finding_key": key,
                    "baseline_overlap_area_m2": before_area,
                    "candidate_overlap_area_m2": after_area,
                    "delta_overlap_area_m2": round(delta, 6),
                }
            )

    status = "pass" if not introduced_keys and not candidate_focus_keys else "fail"
    report: dict[str, Any] = {
        "schema": SURFACE_OVERLAP_COMPARISON_SCHEMA,
        "status": status,
        "claim_status": (
            "bounded-rebuild-surface-overlap-clean"
            if status == "pass"
            else "bounded-rebuild-surface-overlap-invalid"
        ),
        "policy": (
            "candidate must introduce zero junction-surface findings and retain zero "
            "findings touching the explicit rebuild focus; inherited out-of-scope findings "
            "remain blocking for any global-clean claim"
        ),
        "focus_junction_ids": focus,
        "baseline_source_net_file": baseline_report.get("source_net_file"),
        "baseline_source_sha256": baseline_report.get("source_sha256"),
        "candidate_source_net_file": candidate_report.get("source_net_file"),
        "candidate_source_sha256": candidate_report.get("source_sha256"),
        "baseline_global_status": baseline_report.get("status"),
        "candidate_global_status": candidate_report.get("status"),
        "baseline_global_finding_count": len(baseline_keys),
        "candidate_global_finding_count": len(candidate_keys),
        "introduced_finding_count": len(introduced_keys),
        "introduced_findings": [candidate_findings[key] for key in introduced_keys],
        "resolved_finding_count": len(resolved_keys),
        "resolved_findings": [baseline_findings[key] for key in resolved_keys],
        "inherited_out_of_scope_finding_count": len(
            [key for key in inherited_keys if not _finding_touches_focus(candidate_findings[key], focus_set)]
        ),
        "inherited_out_of_scope_findings": [
            candidate_findings[key]
            for key in inherited_keys
            if not _finding_touches_focus(candidate_findings[key], focus_set)
        ],
        "changed_inherited_finding_count": len(changed_inherited),
        "changed_inherited_findings": changed_inherited,
        "baseline_focus_finding_count": len(baseline_focus_keys),
        "baseline_focus_findings": [baseline_findings[key] for key in baseline_focus_keys],
        "candidate_focus_finding_count": len(candidate_focus_keys),
        "candidate_focus_findings": [candidate_findings[key] for key in candidate_focus_keys],
        "evidence_boundary": (
            "pass is limited to introduced findings and explicit focus junctions; "
            "candidate_global_status remains authoritative for the complete corridor"
        ),
    }
    if report_file is not None:
        destination = report_file.resolve()
        _write_report(destination, report)
        report["report_file"] = str(destination)
        report["report_sha256"] = _sha256_file(destination)
    return report


def _indexed_findings(report: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    indexed: dict[str, dict[str, Any]] = {}
    for raw in report.get("junction_junction_overlaps", []) or []:
        finding = dict(raw)
        first, second = sorted(
            (str(finding["first_junction_id"]), str(finding["second_junction_id"]))
        )
        key = f"junction-junction:{first}|{second}"
        finding["finding_kind"] = "junction_junction"
        finding["finding_key"] = key
        indexed[key] = finding
    for raw in report.get("external_lane_non_owner_junction_overlaps", []) or []:
        finding = dict(raw)
        key = f"external-lane-non-owner-junction:{finding['lane_id']}|{finding['non_owner_junction_id']}"
        finding["finding_kind"] = "external_lane_non_owner_junction"
        finding["finding_key"] = key
        indexed[key] = finding
    return indexed


def _finding_touches_focus(finding: Mapping[str, Any], focus: set[str]) -> bool:
    identifiers = {
        str(finding.get(field, ""))
        for field in (
            "first_junction_id",
            "second_junction_id",
            "from_junction_id",
            "to_junction_id",
            "non_owner_junction_id",
        )
    }
    return bool(identifiers & focus)


def _base_report(
    source: Path,
    *,
    minimum_overlap_area_m2: float,
    default_lane_width_m: float,
) -> dict[str, Any]:
    return {
        "schema": SURFACE_OVERLAP_AUDIT_SCHEMA,
        "status": "fail",
        "audit_engine": "torii.bevel-strip-and-junction-polygon-area/v2",
        "source_net_file": str(source),
        "source_network_mutation": False,
        "minimum_overlap_area_m2": minimum_overlap_area_m2,
        "default_lane_width_m": default_lane_width_m,
        "scope": {
            "included": [
                "non-internal junction polygon self-intersection",
                "non-internal junction polygon versus non-internal junction polygon",
                "reconstructed external lane face versus non-owner, non-internal junction polygon",
            ],
            "excluded_as_expected_sumo_geometry": [
                "external lane face versus its from/to owner junction",
                "internal lane faces inside junction polygons",
                "internal junction polygons",
            ],
            "not_claimed": [
                "GPU-pixel identity with a particular Netedit build",
                "internal movement conflict freedom",
                "external edge-to-edge overlap already covered by netconvert --geometry.check-overlap",
            ],
        },
        "lane_face_model": (
            "effective lane width centred on each SUMO lane shape; segment rectangles "
            "plus non-overlapping outer bevel triangles at polyline bends; butt end caps"
        ),
        "junction_junction_overlap_count": 0,
        "junction_junction_overlaps": [],
        "external_lane_non_owner_junction_overlap_count": 0,
        "external_lane_non_owner_junction_overlaps": [],
        "geometry_error_count": 0,
        "geometry_errors": [],
        "non_area_junction_exclusion_count": 0,
        "non_area_junction_exclusions": [],
    }


def _read_junction_polygons(
    root: ET.Element,
) -> tuple[dict[str, Polygon], list[dict[str, str]], list[dict[str, str]]]:
    junctions: dict[str, Polygon] = {}
    errors: list[dict[str, str]] = []
    non_area: list[dict[str, str]] = []
    for element in root.findall("junction"):
        junction_id = element.get("id", "").strip()
        junction_type = element.get("type", "").strip()
        if not junction_id or junction_id.startswith(":") or junction_type == "internal":
            continue
        try:
            polygon = _normalize_polygon(_parse_shape(element.get("shape", "")))
            if len(polygon) < 3:
                non_area.append(
                    {
                        "junction_id": junction_id,
                        "reason": "fewer_than_three_distinct_shape_points",
                    }
                )
                continue
            self_crossings = polygon_self_intersection_count(polygon)
            if self_crossings:
                raise ValueError(
                    f"junction polygon is self-intersecting ({self_crossings} proper segment crossings)"
                )
            if abs(_signed_area(polygon)) <= _GEOMETRY_EPSILON:
                non_area.append(
                    {
                        "junction_id": junction_id,
                        "reason": "zero_area_shape",
                    }
                )
                continue
            _triangulate_polygon(polygon)
            junctions[junction_id] = polygon
        except ValueError as exc:
            errors.append(
                {
                    "kind": "invalid_junction_polygon",
                    "junction_id": junction_id,
                    "error": str(exc),
                }
            )
    return junctions, errors, non_area


def _read_external_lanes(
    root: ET.Element,
    default_lane_width_m: float,
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    lanes: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    for edge in root.findall("edge"):
        if edge.get("function", "").strip() == "internal" or edge.get("id", "").startswith(":"):
            continue
        edge_id = edge.get("id", "").strip()
        from_junction = edge.get("from", "").strip()
        to_junction = edge.get("to", "").strip()
        if not edge_id or not from_junction or not to_junction:
            continue
        for lane in edge.findall("lane"):
            lane_id = lane.get("id", "").strip()
            try:
                shape = _deduplicate_polyline(_parse_shape(lane.get("shape", "")))
                if len(shape) < 2:
                    raise ValueError("lane shape needs at least two distinct points")
                width_text = lane.get("width")
                width = default_lane_width_m if width_text is None else float(width_text)
                if not math.isfinite(width) or width <= 0:
                    raise ValueError("lane width must be finite and greater than zero")
                primitives = _lane_face_primitives(shape, width)
                lanes.append(
                    {
                        "lane_id": lane_id,
                        "edge_id": edge_id,
                        "from_junction_id": from_junction,
                        "to_junction_id": to_junction,
                        "width_m": width,
                        "primitives": primitives,
                    }
                )
            except ValueError as exc:
                errors.append(
                    {
                        "kind": "invalid_external_lane_geometry",
                        "edge_id": edge_id,
                        "lane_id": lane_id,
                        "error": str(exc),
                    }
                )
    return lanes, errors


def _junction_overlap_findings(
    junctions: dict[str, Polygon],
    *,
    minimum_overlap_area_m2: float,
) -> list[dict[str, Any]]:
    identifiers = sorted(junctions)
    triangles = {identifier: _triangulate_polygon(junctions[identifier]) for identifier in identifiers}
    bboxes = {identifier: _bbox(junctions[identifier]) for identifier in identifiers}
    findings: list[dict[str, Any]] = []
    for index, first_id in enumerate(identifiers):
        for second_id in identifiers[index + 1 :]:
            if not _bboxes_overlap(bboxes[first_id], bboxes[second_id]):
                continue
            area = _triangle_sets_intersection_area(triangles[first_id], triangles[second_id])
            if area > minimum_overlap_area_m2:
                findings.append(
                    {
                        "first_junction_id": first_id,
                        "second_junction_id": second_id,
                        "overlap_area_m2": round(area, 6),
                    }
                )
    return findings


def _lane_non_owner_junction_findings(
    lanes: Sequence[dict[str, Any]],
    junctions: dict[str, Polygon],
    *,
    minimum_overlap_area_m2: float,
) -> list[dict[str, Any]]:
    junction_triangles = {
        junction_id: _triangulate_polygon(polygon) for junction_id, polygon in junctions.items()
    }
    junction_bboxes = {junction_id: _bbox(polygon) for junction_id, polygon in junctions.items()}
    findings: list[dict[str, Any]] = []
    for lane in lanes:
        owner_ids = {lane["from_junction_id"], lane["to_junction_id"]}
        primitives: list[Polygon] = lane["primitives"]
        primitive_bboxes = [_bbox(primitive) for primitive in primitives]
        for junction_id in sorted(junctions):
            if junction_id in owner_ids:
                continue
            relevant = [
                primitive
                for primitive, primitive_bbox in zip(primitives, primitive_bboxes, strict=True)
                if _bboxes_overlap(primitive_bbox, junction_bboxes[junction_id])
            ]
            if not relevant:
                continue
            area = sum(
                _triangle_sets_intersection_area([primitive], junction_triangles[junction_id])
                for primitive in relevant
            )
            if area > minimum_overlap_area_m2:
                findings.append(
                    {
                        "lane_id": lane["lane_id"],
                        "edge_id": lane["edge_id"],
                        "from_junction_id": lane["from_junction_id"],
                        "to_junction_id": lane["to_junction_id"],
                        "non_owner_junction_id": junction_id,
                        "lane_width_m": round(float(lane["width_m"]), 6),
                        "overlap_area_m2": round(area, 6),
                    }
                )
    return findings


def _lane_face_primitives(shape: Sequence[Point], width_m: float) -> list[Polygon]:
    radius = width_m / 2.0
    segment_normals: list[Point] = []
    primitives: list[Polygon] = []
    for start, end in zip(shape, shape[1:]):
        delta_x = end[0] - start[0]
        delta_y = end[1] - start[1]
        length = math.hypot(delta_x, delta_y)
        if length <= _GEOMETRY_EPSILON:
            raise ValueError("lane shape contains a zero-length segment")
        normal = (-delta_y / length, delta_x / length)
        segment_normals.append(normal)
        primitives.append(
            _ensure_counter_clockwise(
                [
                    _add_scaled(start, normal, radius),
                    _add_scaled(end, normal, radius),
                    _add_scaled(end, normal, -radius),
                    _add_scaled(start, normal, -radius),
                ]
            )
        )

    for index, vertex in enumerate(shape[1:-1], start=1):
        previous = shape[index - 1]
        following = shape[index + 1]
        incoming = (vertex[0] - previous[0], vertex[1] - previous[1])
        outgoing = (following[0] - vertex[0], following[1] - vertex[1])
        turn = _cross(incoming, outgoing)
        if abs(turn) <= _GEOMETRY_EPSILON:
            continue
        previous_normal = segment_normals[index - 1]
        next_normal = segment_normals[index]
        outer_sign = -1.0 if turn > 0 else 1.0
        bevel = _ensure_counter_clockwise(
            [
                vertex,
                _add_scaled(vertex, previous_normal, outer_sign * radius),
                _add_scaled(vertex, next_normal, outer_sign * radius),
            ]
        )
        if abs(_signed_area(bevel)) > _GEOMETRY_EPSILON:
            primitives.append(bevel)
    return primitives


def _triangle_sets_intersection_area(
    first_triangles: Iterable[Polygon],
    second_triangles: Iterable[Polygon],
) -> float:
    first = list(first_triangles)
    second = list(second_triangles)
    second_with_boxes = [(triangle, _bbox(triangle)) for triangle in second]
    area = 0.0
    for first_triangle in first:
        first_bbox = _bbox(first_triangle)
        for second_triangle, second_bbox in second_with_boxes:
            if not _bboxes_overlap(first_bbox, second_bbox):
                continue
            intersection = _convex_polygon_intersection(first_triangle, second_triangle)
            if len(intersection) >= 3:
                area += abs(_signed_area(intersection))
    return area


def _triangulate_polygon(polygon: Sequence[Point]) -> list[Polygon]:
    vertices = _ensure_counter_clockwise(_normalize_polygon(list(polygon)))
    if len(vertices) < 3:
        raise ValueError("polygon needs at least three distinct points")
    if len(vertices) == 3:
        return [vertices]

    remaining = list(range(len(vertices)))
    triangles: list[Polygon] = []
    guard = 0
    while len(remaining) > 3:
        ear_found = False
        for position, current_index in enumerate(remaining):
            previous_index = remaining[position - 1]
            next_index = remaining[(position + 1) % len(remaining)]
            previous = vertices[previous_index]
            current = vertices[current_index]
            following = vertices[next_index]
            if _cross_points(previous, current, following) <= _GEOMETRY_EPSILON:
                continue
            triangle = [previous, current, following]
            if any(
                _point_strictly_inside_triangle(vertices[index], triangle)
                for index in remaining
                if index not in {previous_index, current_index, next_index}
            ):
                continue
            triangles.append(triangle)
            del remaining[position]
            ear_found = True
            break
        guard += 1
        if not ear_found or guard > len(vertices) * len(vertices):
            raise ValueError("junction polygon is not a simple triangulable polygon")
    triangles.append([vertices[index] for index in remaining])
    return triangles


def polygon_self_intersection_count(polygon: Sequence[Point]) -> int:
    segments = list(zip(polygon, polygon[1:] + polygon[:1], strict=True))
    count = 0
    for left_index, (left_start, left_end) in enumerate(segments):
        for right_index in range(left_index + 1, len(segments)):
            if right_index == left_index + 1 or (left_index == 0 and right_index == len(segments) - 1):
                continue
            right_start, right_end = segments[right_index]
            left_a = _cross_points(left_start, left_end, right_start)
            left_b = _cross_points(left_start, left_end, right_end)
            right_a = _cross_points(right_start, right_end, left_start)
            right_b = _cross_points(right_start, right_end, left_end)
            if left_a * left_b < -_GEOMETRY_EPSILON and right_a * right_b < -_GEOMETRY_EPSILON:
                count += 1
    return count


def _convex_polygon_intersection(subject: Sequence[Point], clip: Sequence[Point]) -> Polygon:
    output = list(_ensure_counter_clockwise(list(subject)))
    clip_polygon = _ensure_counter_clockwise(list(clip))
    for clip_start, clip_end in zip(clip_polygon, clip_polygon[1:] + clip_polygon[:1], strict=True):
        input_vertices = output
        output = []
        if not input_vertices:
            break
        previous = input_vertices[-1]
        previous_inside = _is_left_of_or_on(previous, clip_start, clip_end)
        for current in input_vertices:
            current_inside = _is_left_of_or_on(current, clip_start, clip_end)
            if current_inside != previous_inside:
                output.append(_line_intersection(previous, current, clip_start, clip_end))
            if current_inside:
                output.append(current)
            previous = current
            previous_inside = current_inside
    return _normalize_polygon(output)


def _point_strictly_inside_triangle(point: Point, triangle: Sequence[Point]) -> bool:
    signs = [
        _cross_points(triangle[index], triangle[(index + 1) % 3], point)
        for index in range(3)
    ]
    return all(value > _GEOMETRY_EPSILON for value in signs)


def _line_intersection(first_start: Point, first_end: Point, second_start: Point, second_end: Point) -> Point:
    first_delta = (first_end[0] - first_start[0], first_end[1] - first_start[1])
    second_delta = (second_end[0] - second_start[0], second_end[1] - second_start[1])
    denominator = _cross(first_delta, second_delta)
    if abs(denominator) <= _GEOMETRY_EPSILON:
        return first_end
    offset = (second_start[0] - first_start[0], second_start[1] - first_start[1])
    factor = _cross(offset, second_delta) / denominator
    return (
        first_start[0] + factor * first_delta[0],
        first_start[1] + factor * first_delta[1],
    )


def _is_left_of_or_on(point: Point, line_start: Point, line_end: Point) -> bool:
    return _cross_points(line_start, line_end, point) >= -_GEOMETRY_EPSILON


def _parse_shape(text: str) -> Polygon:
    points: Polygon = []
    for token in text.split():
        coordinates = token.split(",")
        if len(coordinates) < 2:
            raise ValueError(f"invalid shape coordinate {token!r}")
        point = (float(coordinates[0]), float(coordinates[1]))
        if not all(math.isfinite(value) for value in point):
            raise ValueError("shape coordinates must be finite")
        points.append(point)
    return points


def _deduplicate_polyline(points: Sequence[Point]) -> Polygon:
    result: Polygon = []
    for point in points:
        if not result or _distance_squared(point, result[-1]) > _GEOMETRY_EPSILON**2:
            result.append(point)
    return result


def _normalize_polygon(points: Sequence[Point]) -> Polygon:
    normalized = _deduplicate_polyline(points)
    if len(normalized) > 1 and _distance_squared(normalized[0], normalized[-1]) <= _GEOMETRY_EPSILON**2:
        normalized.pop()
    changed = True
    while changed and len(normalized) > 3:
        changed = False
        for index in range(len(normalized)):
            previous = normalized[index - 1]
            current = normalized[index]
            following = normalized[(index + 1) % len(normalized)]
            if abs(_cross_points(previous, current, following)) <= _GEOMETRY_EPSILON:
                del normalized[index]
                changed = True
                break
    return normalized


def _ensure_counter_clockwise(polygon: Polygon) -> Polygon:
    return list(reversed(polygon)) if _signed_area(polygon) < 0 else list(polygon)


def _signed_area(polygon: Sequence[Point]) -> float:
    return 0.5 * sum(
        first[0] * second[1] - second[0] * first[1]
        for first, second in zip(polygon, polygon[1:] + polygon[:1], strict=True)
    )


def _bbox(polygon: Sequence[Point]) -> tuple[float, float, float, float]:
    return (
        min(point[0] for point in polygon),
        min(point[1] for point in polygon),
        max(point[0] for point in polygon),
        max(point[1] for point in polygon),
    )


def _bboxes_overlap(
    first: tuple[float, float, float, float],
    second: tuple[float, float, float, float],
) -> bool:
    return not (
        first[2] < second[0]
        or second[2] < first[0]
        or first[3] < second[1]
        or second[3] < first[1]
    )


def _cross(first: Point, second: Point) -> float:
    return first[0] * second[1] - first[1] * second[0]


def _cross_points(first: Point, second: Point, third: Point) -> float:
    return _cross((second[0] - first[0], second[1] - first[1]), (third[0] - first[0], third[1] - first[1]))


def _add_scaled(point: Point, vector: Point, scale: float) -> Point:
    return (point[0] + vector[0] * scale, point[1] + vector[1] * scale)


def _distance_squared(first: Point, second: Point) -> float:
    return (first[0] - second[0]) ** 2 + (first[1] - second[1]) ** 2


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_report(path: Path, report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
