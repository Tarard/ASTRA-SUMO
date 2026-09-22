"""Transform junction and approach geometry without selecting repair policy."""

from __future__ import annotations

import copy
import math
from pathlib import Path
from typing import Any
import xml.etree.ElementTree as ET
from .network import (
    _edge_is_vehicle_continuation_candidate,
    _map_connection_endpoint,
    _map_internal_ref,
    _map_lane_ref,
    _mapped_internal_ref,
    _split,
)


def _clone_transformed_net_element(
    element: ET.Element,
    dx: float,
    dy: float,
    edge_map: dict[str, str],
    teacher_junction_id: str | None = None,
    candidate_junction_id: str | None = None,
) -> ET.Element:
    clone = ET.Element(
        element.tag,
        _mapped_spatial_attrs(element.attrib, dx, dy, edge_map, teacher_junction_id, candidate_junction_id),
    )
    clone.text = element.text
    clone.tail = element.tail
    for child in list(element):
        clone.append(
            _clone_transformed_net_element(child, dx, dy, edge_map, teacher_junction_id, candidate_junction_id)
        )
    return clone


def _clone_transformed_boundary_edge(
    edge: ET.Element,
    edge_id: str,
    dx: float,
    dy: float,
    edge_map: dict[str, str],
    teacher_junction_id: str,
    candidate_junction_id: str,
) -> ET.Element:
    clone = _clone_transformed_net_element(edge, dx, dy, edge_map, teacher_junction_id, candidate_junction_id)
    teacher_edge_id = edge.attrib.get("id", "")
    if edge_id and edge_id != teacher_edge_id:
        clone.set("id", edge_id)
        teacher_prefix = f"{teacher_edge_id}_"
        candidate_prefix = f"{edge_id}_"
        for lane in clone.findall("lane"):
            lane_id = lane.attrib.get("id", "")
            if lane_id.startswith(teacher_prefix):
                lane.set("id", f"{candidate_prefix}{lane_id[len(teacher_prefix):]}")
            elif lane.attrib.get("index"):
                lane.set("id", f"{candidate_prefix}{lane.attrib['index']}")
    return clone


def _translate_shape(shape: str, dx: float, dy: float) -> str:
    translated = []
    for point in _split(shape):
        coords = point.split(",")
        if len(coords) < 2:
            translated.append(point)
            continue
        coords[0] = _format_xy(float(coords[0]) + dx)
        coords[1] = _format_xy(float(coords[1]) + dy)
        translated.append(",".join(coords))
    return " ".join(translated)


def _format_xy(value: float) -> str:
    return f"{value:.2f}"


def _relative_shape(shape: str, x: str, y: str) -> str:
    if not shape or not x or not y:
        return shape
    try:
        origin_x = float(x)
        origin_y = float(y)
    except ValueError:
        return shape
    translated = []
    for point in shape.split():
        coords = point.split(",")
        if len(coords) < 2:
            translated.append(point)
            continue
        try:
            coords[0] = _format_xy(float(coords[0]) - origin_x)
            coords[1] = _format_xy(float(coords[1]) - origin_y)
        except ValueError:
            translated.append(point)
            continue
        translated.append(",".join(coords))
    return " ".join(translated)


def _model_junction_origin(model: dict[str, Any]) -> tuple[str, str]:
    junction = model.get("junction", {}) if isinstance(model.get("junction"), dict) else {}
    return str(junction.get("x", "")), str(junction.get("y", ""))


def _model_shape_delta(teacher_model: dict[str, Any], candidate_model: dict[str, Any]) -> tuple[float, float] | None:
    teacher_x, teacher_y = _model_junction_origin(teacher_model)
    candidate_x, candidate_y = _model_junction_origin(candidate_model)
    try:
        return float(candidate_x) - float(teacher_x), float(candidate_y) - float(teacher_y)
    except ValueError:
        return None


def _translated_lane_attrs(
    lane: dict[str, Any],
    teacher_junction_id: str,
    junction_id: str,
    dx: float,
    dy: float,
) -> dict[str, str]:
    lane_attrs = {str(key): str(value) for key, value in lane.items() if value not in (None, "")}
    if lane_attrs.get("id"):
        lane_attrs["id"] = _mapped_internal_ref(lane_attrs["id"], teacher_junction_id, junction_id)
    for attr in ("shape", "outlineShape", "customShape"):
        if lane_attrs.get(attr):
            lane_attrs[attr] = _translate_shape(lane_attrs[attr], dx, dy)
    return lane_attrs


def _shape_endpoints(shape: str) -> tuple[tuple[float, float], tuple[float, float]] | None:
    points = _split(shape)
    if not points:
        return None
    try:
        first_x, first_y = points[0].split(",")[:2]
        last_x, last_y = points[-1].split(",")[:2]
        return (float(first_x), float(first_y)), (float(last_x), float(last_y))
    except ValueError:
        return None


def _shape_points(shape: str) -> list[tuple[float, float]]:
    points = []
    for point in _split(shape):
        try:
            x, y = point.split(",")[:2]
            points.append((float(x), float(y)))
        except ValueError:
            continue
    return points


def _join_shape_text(first: str, second: str) -> str:
    joined: list[str] = []
    for shape in (first, second):
        for point in _split(shape):
            if not joined or joined[-1] != point:
                joined.append(point)
    return " ".join(joined)


def _mapped_spatial_attrs(
    attrs: dict[str, str],
    dx: float,
    dy: float,
    edge_map: dict[str, str],
    teacher_junction_id: str | None = None,
    candidate_junction_id: str | None = None,
) -> dict[str, str]:
    mapped = dict(attrs)
    teacher_internal_prefix = f":{teacher_junction_id}_" if teacher_junction_id else ""
    candidate_internal_prefix = f":{candidate_junction_id}_" if candidate_junction_id else ""
    for attr in ("id", "from", "to", "via"):
        if attr in mapped:
            mapped[attr] = _map_internal_ref(mapped[attr], teacher_internal_prefix, candidate_internal_prefix)
    if teacher_junction_id and candidate_junction_id:
        for attr in ("id", "from", "to", "tl"):
            if mapped.get(attr) == teacher_junction_id:
                mapped[attr] = candidate_junction_id
    if "x" in mapped:
        mapped["x"] = _format_xy(float(mapped["x"]) + dx)
    if "y" in mapped:
        mapped["y"] = _format_xy(float(mapped["y"]) + dy)
    for attr in ("shape", "outlineShape", "customShape"):
        if attr in mapped:
            mapped[attr] = _translate_shape(mapped[attr], dx, dy)
    if "crossingEdges" in mapped:
        mapped_edges = [
            edge_map.get(edge, _map_internal_ref(edge, teacher_internal_prefix, candidate_internal_prefix) if edge.startswith(":") else "")
            for edge in _split(mapped["crossingEdges"])
        ]
        mapped["crossingEdges"] = " ".join(edge for edge in mapped_edges if edge)
    return mapped


def _edge_lane_shapes(edge: ET.Element) -> list[str]:
    shapes = (lane.attrib.get("shape", "") for lane in edge.findall("lane"))
    return [_translate_shape(shape, 0.0, 0.0) if shape else "" for shape in shapes]


def _edge_touches_context(
    edge: ET.Element,
    local_junction_ids: set[str],
    *,
    center: tuple[float, float],
    radius_m: float,
) -> bool:
    if edge.attrib.get("from", "") in local_junction_ids or edge.attrib.get("to", "") in local_junction_ids:
        return True
    edge_id = str(edge.attrib.get("id", ""))
    if edge_id.startswith(":") and any(edge_id.startswith(f":{junction_id}_") for junction_id in local_junction_ids):
        return True
    for lane in edge.findall("lane"):
        for x, y in _shape_points(str(lane.attrib.get("shape", ""))):
            if math.hypot(x - center[0], y - center[1]) <= radius_m:
                return True
    return False


def _edge_geometry_matches_current_junctions(
    root: ET.Element,
    edge: ET.Element,
    existing_edge: ET.Element,
    max_endpoint_delta: float,
) -> bool:
    edge_endpoints = _shape_endpoints(_primary_edge_shape(edge))
    existing_endpoints = _shape_endpoints(_primary_edge_shape(existing_edge))
    from_xy = _junction_xy(root, edge.attrib.get("from", ""))
    to_xy = _junction_xy(root, edge.attrib.get("to", ""))
    if edge_endpoints is None or existing_endpoints is None or from_xy is None or to_xy is None:
        return False
    matches_replay = all(
        ((actual[0] - expected[0]) ** 2 + (actual[1] - expected[1]) ** 2) ** 0.5 <= max_endpoint_delta
        for actual, expected in zip(existing_endpoints, edge_endpoints)
    )
    matches_current_endpoints = all(
        ((actual[0] - expected[0]) ** 2 + (actual[1] - expected[1]) ** 2) ** 0.5 <= max_endpoint_delta
        for actual, expected in zip(existing_endpoints, (from_xy, to_xy))
    )
    return matches_replay and matches_current_endpoints


def _warp_anchor_shape_to_teacher_endpoint(
    anchor_shape: str,
    teacher_shape: str,
    *,
    target_at_start: bool,
) -> str:
    anchor_tokens = _split(anchor_shape)
    teacher_points = _shape_points(teacher_shape)
    parsed: list[tuple[float, float, list[str]]] = []
    for token in anchor_tokens:
        coords = token.split(",")
        if len(coords) < 2:
            return ""
        try:
            parsed.append((float(coords[0]), float(coords[1]), coords[2:]))
        except ValueError:
            return ""
    if not parsed or not teacher_points:
        return ""

    desired_x, desired_y = teacher_points[0 if target_at_start else -1]
    anchor_x, anchor_y, _ = parsed[0 if target_at_start else -1]
    delta_x = desired_x - anchor_x
    delta_y = desired_y - anchor_y
    displacement = math.hypot(delta_x, delta_y)
    cumulative = [0.0]
    for (left_x, left_y, _), (right_x, right_y, _) in zip(
        parsed,
        parsed[1:],
    ):
        cumulative.append(
            cumulative[-1] + math.hypot(right_x - left_x, right_y - left_y)
        )
    total = cumulative[-1]
    if total <= 1e-9:
        weights = [1.0 for _ in parsed]
    else:
        # The teacher owns the local conflict core, not the complete OSM
        # approach.  A full-edge linear warp can drag a lane across a nearby
        # junction.  Taper the correction inside a bounded local splice and
        # preserve the remote public-road geometry exactly.
        blend_extent = min(total, max(15.0, displacement * 4.0))
        distances_from_target = (
            cumulative
            if target_at_start
            else [total - distance for distance in cumulative]
        )
        weights = []
        for distance in distances_from_target:
            linear_weight = max(0.0, 1.0 - distance / blend_extent)
            weights.append(linear_weight * linear_weight * (3.0 - 2.0 * linear_weight))

    output = []
    for (x, y, extra), weight in zip(parsed, weights):
        coords = [
            _format_xy(x + delta_x * weight),
            _format_xy(y + delta_y * weight),
            *extra,
        ]
        output.append(",".join(coords))
    return " ".join(output)


def _expand_junction_shape_to_approach_endpoints(
    root: ET.Element,
    junction_id: str,
    geometry_anchor_edge_ids: set[str],
) -> dict[str, object]:
    if not geometry_anchor_edge_ids:
        return {"status": "skipped", "reason": "no_geometry_anchor_edges"}
    junction = root.find(f"junction[@id='{junction_id}']")
    if junction is None:
        return {"status": "skipped", "reason": "junction_not_found"}
    shape_points = _shape_points(junction.attrib.get("shape", ""))
    endpoint_points: list[tuple[float, float]] = []
    endpoint_edge_ids: list[str] = []
    for edge in root.findall("edge"):
        edge_id = edge.attrib.get("id", "")
        if (
            edge_id not in geometry_anchor_edge_ids
            or edge.attrib.get("function") == "internal"
            or junction_id not in (edge.attrib.get("from"), edge.attrib.get("to"))
        ):
            continue
        use_first = edge.attrib.get("from") == junction_id
        for lane in edge.findall("lane"):
            points = _shape_points(lane.attrib.get("shape", "") or edge.attrib.get("shape", ""))
            if not points:
                continue
            endpoint_points.append(points[0] if use_first else points[-1])
            endpoint_edge_ids.append(edge_id)
    if not endpoint_points:
        return {"status": "skipped", "reason": "no_approach_endpoints"}
    hull_points = _convex_hull([*shape_points, *endpoint_points])
    if len(hull_points) < 3:
        return {"status": "skipped", "reason": "insufficient_hull_points"}
    old_shape = junction.attrib.get("shape", "")
    new_shape = " ".join(f"{x:.2f},{y:.2f}" for x, y in hull_points)
    if new_shape == old_shape:
        return {
            "status": "unchanged",
            "approach_endpoint_count": len(endpoint_points),
            "approach_edge_ids": sorted(set(endpoint_edge_ids)),
        }
    junction.set("shape", new_shape)
    return {
        "status": "pass",
        "approach_endpoint_count": len(endpoint_points),
        "approach_edge_ids": sorted(set(endpoint_edge_ids)),
        "old_shape_point_count": len(shape_points),
        "new_shape_point_count": len(hull_points),
    }


def _polyline_length(shape: str) -> float | None:
    points = _shape_points(shape)
    if len(points) < 2:
        return None
    return sum(
        math.hypot(right[0] - left[0], right[1] - left[1])
        for left, right in zip(points, points[1:])
    )


def _clone_transformed_boundary_junction(
    junction: ET.Element,
    dx: float,
    dy: float,
    edge_map: dict[str, str],
    teacher_junction_id: str,
    candidate_junction_id: str,
) -> ET.Element:
    attrs = _mapped_spatial_attrs(junction.attrib, dx, dy, edge_map, teacher_junction_id, candidate_junction_id)
    attrs["incLanes"] = ""
    attrs["intLanes"] = ""
    return ET.Element("junction", attrs)


def _translated_edge_lane_shapes(edge: ET.Element, dx: float, dy: float) -> list[str]:
    return [_translate_shape(shape, dx, dy) if shape else "" for shape in _edge_lane_shapes(edge)]


def _blend_geometry_anchor_at_endpoint(
    edge: ET.Element,
    geometry_source_edge: ET.Element,
    *,
    target_at_start: bool,
) -> bool:
    """Blend a replayed local endpoint into an existing boundary edge.

    The source boundary may end at an OSM split member while the replayed
    boundary ends at the newly collapsed owner.  The endpoint ids therefore
    need not match even though their directed side does.  This lower-level
    primitive keeps the source's remote endpoint exactly and warps only toward
    the replayed teacher endpoint.
    """

    blended = False
    # SUMO nets commonly keep geometry only on lane children.  Falling back
    # to the first lane is essential here: leaving a translated teacher
    # centerline on the edge while blending only lane shapes makes netconvert
    # recreate the same remote-endpoint gap from the stale edge-level shape.
    teacher_edge_shape = _primary_edge_shape(edge)
    anchor_edge_shape = _primary_edge_shape(geometry_source_edge)
    if teacher_edge_shape and anchor_edge_shape:
        shape = _warp_anchor_shape_to_teacher_endpoint(
            anchor_edge_shape,
            teacher_edge_shape,
            target_at_start=target_at_start,
        )
        if shape:
            edge.set("shape", shape)
            blended = True

    anchor_lanes = {
        lane.attrib.get("index", ""): lane
        for lane in geometry_source_edge.findall("lane")
        if lane.attrib.get("index", "")
    }
    for lane in edge.findall("lane"):
        teacher_lane_shape = lane.attrib.get("shape", "")
        anchor_lane = anchor_lanes.get(lane.attrib.get("index", ""))
        anchor_lane_shape = (
            anchor_lane.attrib.get("shape", "")
            if anchor_lane is not None
            else anchor_edge_shape
        )
        if not teacher_lane_shape or not anchor_lane_shape:
            continue
        shape = _warp_anchor_shape_to_teacher_endpoint(
            anchor_lane_shape,
            teacher_lane_shape,
            target_at_start=target_at_start,
        )
        if shape:
            lane.set("shape", shape)
            # ``lane.length`` in a SUMO ``.net.xml`` is operational geometry,
            # not a teacher semantic.  The replayed lane was cloned before its
            # centre-line was warped and may therefore still carry the
            # teacher's (often much longer) declared length.  NetEdit renders
            # the new shape while SUMO positions vehicles using ``length``;
            # keeping the stale value makes the two views disagree.  Plain XML
            # conversion normally recomputes this field.  For the native net
            # replay path, recompute it whenever the cloned lane declared one.
            if "length" in lane.attrib:
                rendered_length = _polyline_length(shape)
                if rendered_length is not None:
                    lane.set("length", f"{rendered_length:.2f}")
            blended = True
    return blended


def _blend_geometry_anchor_at_target(
    edge: ET.Element,
    geometry_source_edge: ET.Element,
    target_junction_id: str,
) -> bool:
    """Keep the anchor's remote endpoint and the teacher's target endpoint.

    A joined OSM cell and a hand-modelled teacher intersection rarely place
    the junction boundary at exactly the same coordinate.  Copying either
    complete shape creates a gap at one end.  This blend smoothly warps the
    anchor polyline from zero displacement at the untouched remote junction
    to the teacher displacement at the rebuilt target junction.
    """

    target_at_start = edge.attrib.get("from") == target_junction_id
    target_at_end = edge.attrib.get("to") == target_junction_id
    if target_at_start == target_at_end:
        return False
    if target_at_start and geometry_source_edge.attrib.get("from") != target_junction_id:
        return False
    if target_at_end and geometry_source_edge.attrib.get("to") != target_junction_id:
        return False

    return _blend_geometry_anchor_at_endpoint(
        edge,
        geometry_source_edge,
        target_at_start=target_at_start,
    )


def _mapped_connection_attrs(
    connection: ET.Element,
    edge_map: dict[str, str],
    teacher_internal_prefix: str,
    teacher_junction_id: str,
    candidate_internal_prefix: str,
    candidate_junction_id: str,
    candidate_edge_ids: set[str],
    dx: float,
    dy: float,
) -> dict[str, str] | None:
    mapped = dict(connection.attrib)
    for attr in ("from", "to"):
        endpoint = _map_connection_endpoint(
            mapped.get(attr, ""), edge_map, teacher_internal_prefix, candidate_internal_prefix, candidate_edge_ids
        )
        if not endpoint:
            return None
        mapped[attr] = endpoint
    if mapped.get("tl") and mapped.get("linkIndex"):
        mapped["tl"] = candidate_junction_id
    if mapped.get("via"):
        mapped["via"] = _map_internal_ref(mapped["via"], teacher_internal_prefix, candidate_internal_prefix)
        if not mapped["via"].startswith(candidate_internal_prefix):
            return None
    if mapped.get("shape"):
        mapped["shape"] = _translate_shape(mapped["shape"], dx, dy)
    return mapped


def _estimate_linear_lane_transition_shape(root: ET.Element, junction_id: str) -> dict[str, object]:
    junction = root.find(f"junction[@id='{junction_id}']")
    if junction is None:
        return {"status": "fail", "reason": "junction_not_found"}
    junction_type = junction.attrib.get("type", "")
    if junction_type.startswith("traffic_light") or junction.attrib.get("tl"):
        return {"status": "fail", "reason": "junction_is_signal_controlled", "junction_type": junction_type}

    external_edges = [
        edge
        for edge in root.findall("edge")
        if edge.attrib.get("id")
        and edge.attrib.get("function") not in {"internal", "crossing", "walkingarea"}
        and junction_id in (edge.attrib.get("from"), edge.attrib.get("to"))
        and _edge_is_vehicle_continuation_candidate(edge)
    ]
    incoming = [edge for edge in external_edges if edge.attrib.get("to") == junction_id]
    outgoing = [edge for edge in external_edges if edge.attrib.get("from") == junction_id]
    if len(incoming) != 1 or len(outgoing) != 1:
        return {
            "status": "fail",
            "reason": "transition_requires_one_vehicle_incoming_and_one_vehicle_outgoing_edge",
            "incoming_edge_ids": sorted(edge.attrib.get("id", "") for edge in incoming),
            "outgoing_edge_ids": sorted(edge.attrib.get("id", "") for edge in outgoing),
        }
    incoming_edge = incoming[0]
    outgoing_edge = outgoing[0]
    incoming_lanes = incoming_edge.findall("lane")
    outgoing_lanes = outgoing_edge.findall("lane")
    if not incoming_lanes or not outgoing_lanes or len(incoming_lanes) == len(outgoing_lanes):
        return {
            "status": "fail",
            "reason": "transition_requires_nonzero_lane_count_change",
            "incoming_lane_count": len(incoming_lanes),
            "outgoing_lane_count": len(outgoing_lanes),
        }

    incoming_edge_id = incoming_edge.attrib["id"]
    outgoing_edge_id = outgoing_edge.attrib["id"]
    relevant_connections = [
        connection
        for connection in root.findall("connection")
        if connection.attrib.get("from") == incoming_edge_id
    ]
    straight_connections = [
        connection
        for connection in relevant_connections
        if connection.attrib.get("to") == outgoing_edge_id
        and connection.attrib.get("dir", "s") == "s"
        and not connection.attrib.get("tl")
    ]
    if len(straight_connections) != min(len(incoming_lanes), len(outgoing_lanes)) or len(
        straight_connections
    ) != len(relevant_connections):
        return {
            "status": "fail",
            "reason": "transition_movements_are_not_complete_uncontrolled_straight_connections",
            "relevant_connection_count": len(relevant_connections),
            "straight_connection_count": len(straight_connections),
        }

    endpoint_points: list[tuple[float, float]] = []
    for lane in incoming_lanes:
        points = _shape_points(lane.attrib.get("shape", "") or incoming_edge.attrib.get("shape", ""))
        if points:
            endpoint_points.append(points[-1])
    for lane in outgoing_lanes:
        points = _shape_points(lane.attrib.get("shape", "") or outgoing_edge.attrib.get("shape", ""))
        if points:
            endpoint_points.append(points[0])
    hull = _convex_hull(endpoint_points)
    polygon_area = _polygon_area(hull)
    if len(endpoint_points) != len(incoming_lanes) + len(outgoing_lanes) or len(hull) < 3 or polygon_area <= 0:
        return {
            "status": "fail",
            "reason": "adjacent_lane_endpoints_do_not_form_nonzero_polygon",
            "endpoint_count": len(endpoint_points),
            "hull_point_count": len(hull),
            "polygon_area_m2": polygon_area,
        }

    return {
        "status": "pass",
        "junction_id": junction_id,
        "junction_type": junction_type,
        "incoming_edge_id": incoming_edge_id,
        "outgoing_edge_id": outgoing_edge_id,
        "incoming_lane_count": len(incoming_lanes),
        "outgoing_lane_count": len(outgoing_lanes),
        "straight_connection_signatures": sorted(
            (
                connection.attrib.get("fromLane", "0"),
                connection.attrib.get("toLane", "0"),
                connection.attrib.get("dir", "s"),
            )
            for connection in straight_connections
        ),
        "road_identity": {
            attr: (incoming_edge.attrib.get(attr), outgoing_edge.attrib.get(attr))
            for attr in ("name", "type", "priority", "spreadType")
        },
        "endpoint_count": len(endpoint_points),
        "estimated_shape": " ".join(f"{x:.2f},{y:.2f}" for x, y in hull),
        "polygon_area_m2": round(polygon_area, 6),
    }


def _mapped_junction_attrs(
    teacher_junction: ET.Element,
    dx: float,
    dy: float,
    edge_map: dict[str, str],
    teacher_internal_prefix: str,
    candidate_internal_prefix: str,
) -> dict[str, str]:
    teacher_junction_id = teacher_internal_prefix[1:-1] if teacher_internal_prefix.startswith(":") else None
    candidate_junction_id = candidate_internal_prefix[1:-1] if candidate_internal_prefix.startswith(":") else None
    attrs = _mapped_spatial_attrs(teacher_junction.attrib, dx, dy, edge_map, teacher_junction_id, candidate_junction_id)
    if "incLanes" in attrs:
        attrs["incLanes"] = " ".join(
            lane
            for lane in (
                _map_lane_ref(lane, edge_map, teacher_internal_prefix, candidate_internal_prefix)
                for lane in _split(attrs["incLanes"])
            )
            if lane
        )
    if "intLanes" in attrs:
        attrs["intLanes"] = " ".join(
            lane
            for lane in (
                _map_lane_ref(lane, edge_map, teacher_internal_prefix, candidate_internal_prefix)
                for lane in _split(attrs["intLanes"])
            )
            if lane
        )
    return attrs


def _clone_transformed_junction(
    junction: ET.Element,
    dx: float,
    dy: float,
    edge_map: dict[str, str],
    teacher_internal_prefix: str,
    candidate_internal_prefix: str,
) -> ET.Element:
    clone = ET.Element(
        "junction",
        _mapped_junction_attrs(junction, dx, dy, edge_map, teacher_internal_prefix, candidate_internal_prefix),
    )
    clone.text = junction.text
    clone.tail = junction.tail
    for child in list(junction):
        clone.append(ET.Element(child.tag, dict(child.attrib)))
    return clone


def _teacher_to_candidate_delta(root: ET.Element, junction_id: str, teacher_junction: object) -> tuple[float, float]:
    candidate_junction = root.find(f"junction[@id='{junction_id}']")
    if candidate_junction is None or not isinstance(teacher_junction, dict):
        return 0.0, 0.0
    try:
        return (
            float(candidate_junction.attrib.get("x", "0")) - float(str(teacher_junction.get("x", "0"))),
            float(candidate_junction.attrib.get("y", "0")) - float(str(teacher_junction.get("y", "0"))),
        )
    except ValueError:
        return 0.0, 0.0


def _junction_within_radius(junction: ET.Element, *, center: tuple[float, float], radius_m: float) -> bool:
    try:
        x = float(junction.attrib.get("x", ""))
        y = float(junction.attrib.get("y", ""))
    except ValueError:
        return False
    return math.hypot(x - center[0], y - center[1]) <= radius_m


def _primary_edge_shape(edge: ET.Element) -> str:
    if edge.attrib.get("shape"):
        return edge.attrib["shape"]
    lane = edge.find("lane")
    return lane.attrib.get("shape", "") if lane is not None else ""


def _load_geometry_anchor_edges(edge_file: Path | None) -> dict[str, ET.Element]:
    if edge_file is None:
        return {}
    try:
        root = ET.parse(edge_file).getroot()
    except (ET.ParseError, OSError):
        return {}
    return {
        edge.attrib["id"]: copy.deepcopy(edge)
        for edge in root.findall("edge")
        if edge.attrib.get("id")
        and edge.attrib.get("function") != "internal"
        and (edge.attrib.get("shape") or any(lane.attrib.get("shape") for lane in edge.findall("lane")))
    }


def _geometry_anchor_junctions_by_id(
    candidate_edges_by_id: dict[str, ET.Element],
    candidate_junctions_by_id: dict[str, ET.Element],
    geometry_anchor_edge_ids: set[str],
    *,
    target_junction_id: str,
) -> dict[str, ET.Element]:
    anchored: dict[str, ET.Element] = {}
    for edge_id in geometry_anchor_edge_ids:
        edge = candidate_edges_by_id.get(edge_id)
        if edge is None:
            continue
        for endpoint_id in (edge.attrib.get("from", ""), edge.attrib.get("to", "")):
            if not endpoint_id or endpoint_id == target_junction_id:
                continue
            junction = candidate_junctions_by_id.get(endpoint_id)
            if junction is not None:
                anchored[endpoint_id] = copy.deepcopy(junction)
    return anchored


def _restore_geometry_anchor_junctions(
    root: ET.Element,
    geometry_anchor_junctions_by_id: dict[str, ET.Element],
) -> list[str]:
    restored = []
    for junction_id, source_junction in sorted(geometry_anchor_junctions_by_id.items()):
        junction = root.find(f"junction[@id='{junction_id}']")
        if junction is None:
            continue
        for attr in ("x", "y", "z", "shape"):
            if source_junction.attrib.get(attr):
                junction.set(attr, source_junction.attrib[attr])
            else:
                junction.attrib.pop(attr, None)
        restored.append(junction_id)
    return restored


def _convex_hull(points: list[tuple[float, float]]) -> list[tuple[float, float]]:
    unique = sorted(set(points))
    if len(unique) <= 1:
        return unique

    def cross(origin: tuple[float, float], left: tuple[float, float], right: tuple[float, float]) -> float:
        return (left[0] - origin[0]) * (right[1] - origin[1]) - (left[1] - origin[1]) * (right[0] - origin[0])

    lower: list[tuple[float, float]] = []
    for point in unique:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], point) <= 0:
            lower.pop()
        lower.append(point)
    upper: list[tuple[float, float]] = []
    for point in reversed(unique):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], point) <= 0:
            upper.pop()
        upper.append(point)
    return lower[:-1] + upper[:-1]


def _joined_lane_length(first_lane: ET.Element, second_lane: ET.Element) -> str | None:
    try:
        return f"{float(first_lane.attrib['length']) + float(second_lane.attrib['length']):.2f}"
    except (KeyError, ValueError):
        return None


def _junction_xy(root: ET.Element, junction_id: str) -> tuple[float, float] | None:
    junction = next((item for item in root.findall("junction") if item.attrib.get("id") == junction_id), None)
    if junction is None:
        return None
    try:
        return float(junction.attrib["x"]), float(junction.attrib["y"])
    except (KeyError, ValueError):
        return None


def _polygon_area(points: list[tuple[float, float]]) -> float:
    if len(points) < 3:
        return 0.0
    return abs(
        sum(
            left[0] * right[1] - right[0] * left[1]
            for left, right in zip(points, [*points[1:], points[0]])
        )
    ) / 2.0
