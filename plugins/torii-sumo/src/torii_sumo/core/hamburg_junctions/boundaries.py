"""Resolve road-mouth boundaries and verify their native reconstruction."""

from __future__ import annotations

import math
import shutil
from copy import deepcopy
import xml.etree.ElementTree as ET
from collections import defaultdict
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any
from ..command_runner import run_command
from ..connection_mode_audit import lane_supports_motorized
from ..hamburg_official_intersection_plainxml import _proper_segments_intersect
from .geometry import Point, _parse_shape, _point_segment_distance, _shape
from .source import _official_boundary_widths


def _boundary_distance(point: Point, polygon: Sequence[Point]) -> float:
    inside = False
    for a, b in zip(polygon, [*polygon[1:], polygon[0]]):
        if (a[1] > point[1]) != (b[1] > point[1]):
            crossing_x = a[0] + (point[1] - a[1]) * (b[0] - a[0]) / (b[1] - a[1])
            if point[0] < crossing_x:
                inside = not inside
    return 0.0 if inside else min(_point_segment_distance(point, a, b) for a, b in zip(polygon, [*polygon[1:], polygon[0]]))


def _official_boundary_sections(plan: Mapping[str, Any], points: Mapping[str, Point]) -> dict[str, dict[str, Any]]:
    widths = _official_boundary_widths(plan)
    result = {}
    for lane in plan.get("lanes", []):
        lane_id = str(lane["lane_id"])
        shape = lane.get("shape_network", [])
        if lane_id not in points or lane_id not in widths or len(shape) < 2:
            continue
        point = points[lane_id]
        a, b = min(zip(shape, shape[1:]), key=lambda pair: _point_segment_distance(point, *pair))
        dx, dy = b[0] - a[0], b[1] - a[1]
        length = math.hypot(dx, dy)
        if length <= 1e-9:
            continue
        half_width = widths[lane_id] / 2
        result[lane_id] = {"lane_id": lane_id, "width_m": widths[lane_id], "shape": [(point[0] - dy / length * half_width, point[1] + dx / length * half_width), (point[0] + dy / length * half_width, point[1] - dx / length * half_width)]}
    return result


def _section_touches_polygon(section: Sequence[Point], polygon: Sequence[Point]) -> bool:
    # Use the same 0.1 m geometry precision as the complete connection audit.
    if any(max(point[axis] for point in section) < min(point[axis] for point in polygon) - 0.1 or min(point[axis] for point in section) > max(point[axis] for point in polygon) + 0.1 for axis in (0, 1)):
        return False
    return any(_boundary_distance(point, polygon) <= 0.1 for point in section) or any(
        _proper_segments_intersect(section[0], section[-1], a, b, tolerance_m=1e-6)
        for a, b in zip(polygon, [*polygon[1:], polygon[0]])
    )


def _port_section(point, shape, width):
    a, b = min(zip(shape, shape[1:]), key=lambda pair: _point_segment_distance(point, *pair))
    dx, dy = b[0] - a[0], b[1] - a[1]
    length = math.hypot(dx, dy)
    if length <= 1e-9:
        raise ValueError("a boundary lane requires a nonzero direction")
    return [(point[0] - dy / length * width / 2, point[1] + dx / length * width / 2),
            (point[0] + dy / length * width / 2, point[1] - dx / length * width / 2)]


def _classify_boundary_ports(root, plan, group, points, *, maximum_lane_projection_error_m=10.0, minimum_lane_match_margin_m=0.5):
    """Separate actual graph-cut ports from official anchors inside the join."""
    from ..official_movement_composition import _original_lane_bindings, _resolve_original_context
    from ..source_movement_support import _index

    movements = [row for row in plan["movements"] if str(row.get("intersection_part", "0")) == str(group["intersection_part"])]
    used = {str(row[key]) for row in movements for key in ("ingress_lane_id", "egress_lane_id")}
    lanes = {str(row["lane_id"]): {**row, "junction_endpoint_network": points[str(row["lane_id"])]}
             for row in plan.get("lanes", []) if str(row["lane_id"]) in used and str(row["lane_id"]) in points
             and row.get("direction_role") in {"ingress", "egress"} and len(row.get("shape_network", [])) >= 2}
    if not lanes:
        return None  # Legacy plans without lane identities keep their explicit boundary.
    index = _index(root)
    members = set(map(str, group["source_node_ids"]))
    bindings, reviews = _original_lane_bindings(index, lanes, members, maximum_lane_projection_error_m, minimum_lane_match_margin_m)
    _resolve_original_context(index, lanes, movements, bindings, reviews, members, minimum_lane_match_margin_m)
    widths = _official_boundary_widths(plan)
    external, internal = [], []
    unresolved = [{"lane_id": lane_id, "role": None, "junction_endpoint_network": points.get(lane_id),
                   "source_lane_id": None, "reason": "official_lane_geometry_or_direction_unproved"} for lane_id in sorted(used - set(lanes))]
    for lane_id, lane in lanes.items():
        original_id = bindings.get(lane_id)
        record = {"lane_id": lane_id, "role": lane["direction_role"], "junction_endpoint_network": points[lane_id], "source_lane_id": original_id}
        if original_id is None or lane.get("permission_status") == "review_required":
            unresolved.append({**record, "reason": "original_lane_identity_unproved"})
            continue
        edge_id, original_lane, _ = index["lanes"][original_id]
        edge = index["edges"][edge_id]
        record["source_edge_id"] = edge_id
        if edge.get("from") in members and edge.get("to") in members:
            internal.append({**record, "reason": "both_source_edge_nodes_inside_join"})
        elif edge.get("to" if lane["direction_role"] == "ingress" else "from") in members:
            width = widths.get(lane_id, float(original_lane.get("width", "3.2")))
            external.append({**record, "width_m": width, "shape": _port_section(points[lane_id], lane["shape_network"], width), "basis": "official_external_B"})
        else:
            unresolved.append({**record, "reason": "official_lane_is_not_an_actual_cut_edge"})
    return {"official_external_ports": external, "official_internal_anchors": internal,
            "official_unresolved_anchors": unresolved, "original_lane_binding_reviews": reviews,
            "maximum_lane_projection_error_m": maximum_lane_projection_error_m,
            "basis": "source_lane_identity_and_actual_join_graph_cut"}


def _strip_longitudinal_interval(a, b, origin, axis, half_width):
    normal = (-axis[1], axis[0])
    offset = sum((a[i] - origin[i]) * normal[i] for i in (0, 1))
    delta = sum((b[i] - a[i]) * normal[i] for i in (0, 1))
    if abs(delta) <= 1e-9:
        if abs(offset) > half_width:
            return None
        low, high = 0.0, 1.0
    else:
        cuts = sorted(((-half_width - offset) / delta, (half_width - offset) / delta))
        low, high = max(0.0, cuts[0]), min(1.0, cuts[1])
        if low > high:
            return None
    positions = [sum((a[i] + fraction * (b[i] - a[i]) - origin[i]) * axis[i] for i in (0, 1)) for fraction in (low, high)]
    return min(positions), max(positions)


def _safe_native_cut_port(root, edge, lane, role, *, maximum_movement_m):
    """Keep an unknown cut beyond the other owner's full road-width exit."""
    shape = _parse_shape(lane.get("shape", ""))
    reference = _parse_shape(edge.get("shape", "")) or shape
    first, last = reference[-2:] if role == "ingress" else reference[:2]
    dx, dy = last[0] - first[0], last[1] - first[1]
    length = math.hypot(dx, dy)
    if length <= 1e-9:
        raise ValueError("native cut requires a nonzero source reference direction")
    point = shape[-1] if role == "ingress" else shape[0]
    width = float(lane.get("width", "3.2"))
    direction = (dx / length, dy / length)
    toward_join = direction if role == "ingress" else (-direction[0], -direction[1])
    remote_id = edge.get("from" if role == "ingress" else "to")
    remote = root.find(f"junction[@id='{remote_id}']")
    polygon = _parse_shape(remote.get("shape", "")) if remote is not None else []
    stations = []
    for a, b in zip(polygon, [*polygon[1:], polygon[0]] if polygon else []):
        interval = _strip_longitudinal_interval(a, b, point, toward_join, width / 2)
        if interval is not None:
            stations.extend(interval)
    # SUMO's minimum edge is 0.1 m. Add the existing 0.1 m boundary precision
    # so output rounding cannot turn a positive road segment into a fallback.
    visible = 0.2
    shift = max(0.0, max(stations) + visible) if stations else 0.0
    if shift > maximum_movement_m:
        raise ValueError(f"native cut cannot clear owner {remote_id} within the requested geometry limit")
    corrected = tuple(point[i] + shift * toward_join[i] for i in (0, 1))
    result = {"junction_endpoint_network": corrected, "width_m": width,
              "shape": _port_section(corrected, [corrected, (corrected[0] + dx, corrected[1] + dy)], width),
              "native_lane_direction": (dx, dy), "basis": "native_joined_cut_lane"}
    if shift > 1e-9:
        result.update(basis="native_cut_order_corrected_after_remote_owner", adjustment={
            "remote_owner": remote_id, "original_native_endpoint": point, "shift_m": shift,
            "minimum_visible_length_m": visible, "width_m": width,
            "basis": "full_lane_strip_exit_along_source_reference_not_internal_MAP_B"})
    else:
        a, b = shape[-2:] if role == "ingress" else shape[:2]
        result.update(shape=_port_section(point, shape, width), native_lane_direction=(b[0] - a[0], b[1] - a[1]))
    return result


def _adjust_native_port_conflicts(root, ports, *, maximum_movement_m):
    """Move unlocated incoming ports upstream, keeping official stops fixed."""
    from ..official_movement_composition import _slice_shape

    fixed = [port for port in ports if port["basis"] == "official_external_B"]
    result, reviews = [], []
    for port in ports:
        others = [row for row in fixed if row["source_edge_id"] != port["source_edge_id"]]
        if port["basis"] == "official_external_B" or port["role"] != "ingress" or not any(
            _proper_segments_intersect(*port["shape"], *row["shape"], tolerance_m=1e-6) for row in others
        ):
            result.append(port)
            continue
        edge = root.find(f"edge[@id='{port['source_edge_id']}']")
        lane = edge.find(f"lane[@id='{port['source_lane_id']}']")
        shape = _parse_shape(lane.get("shape", ""))
        lengths = [math.dist(a, b) for a, b in zip(shape, shape[1:])]
        total, end_station = sum(lengths), sum(lengths)
        replacement = None
        for index in range(len(lengths) - 1, -1, -1):
            length = lengths[index]
            start_station = end_station - length
            end_station = start_station
            if length <= 1e-9:
                continue
            a, b = shape[index:index + 2]
            direction = ((b[0] - a[0]) / length, (b[1] - a[1]) / length)
            forbidden = []
            for row in others:
                interval = _strip_longitudinal_interval(*row["shape"], a, direction, port["width_m"] / 2)
                if interval is not None:
                    forbidden.append((interval[0] - 0.1, interval[1] + 0.1))
            position = length
            while True:
                occupied = next((span for span in forbidden if span[0] - 1e-9 <= position <= span[1] + 1e-9), None)
                if occupied is None:
                    break
                position = occupied[0] - 1e-6
            if position < max(0.0, total - maximum_movement_m - start_station, 0.2 - start_station):
                continue
            point = tuple(a[i] + position * direction[i] for i in (0, 1))
            kept = _slice_shape(shape, 0.0, start_station + position)
            probe_edge, probe_lane = deepcopy(edge), deepcopy(lane)
            probe_edge.set("shape", _shape(kept))
            probe_lane.set("shape", _shape(kept))
            try:
                _safe_native_cut_port(root, probe_edge, probe_lane, "ingress", maximum_movement_m=0.0)
            except ValueError:
                break
            replacement = {**port, "junction_endpoint_network": point, "shape": _port_section(point, [a, b], port["width_m"]),
                           "native_lane_direction": direction, "basis": "native_geometry_adjustment_preserving_official_ports",
                           "adjustment": {"original_native_endpoint": port["junction_endpoint_network"], "upstream_distance_m": total - start_station - position,
                                          "source_lane_position_m": start_station + position, "width_m": port["width_m"], "source_owner_exit_preserved": True,
                                          "reason": "unlocated_native_port_overlaps_fixed_official_port"}}
            break
        result.append(replacement or port)
        if replacement is None:
            reviews.append({"source_lane_id": port["source_lane_id"], "reason": "no_ordered_native_section_within_requested_limit"})
    # A bidirectional road has one road mouth, even though SUMO stores two
    # directed edges. Moving only its inlet leaves a diagonal notch.
    for i, incoming in enumerate(list(result)):
        if incoming.get("basis") != "native_geometry_adjustment_preserving_official_ports":
            continue
        edge = root.find(f"edge[@id='{incoming['source_edge_id']}']")
        reference = _parse_shape(edge.get("shape", ""))
        for j, outgoing in enumerate(result):
            if outgoing.get("role") != "egress" or outgoing["basis"] == "official_external_B":
                continue
            other = root.find(f"edge[@id='{outgoing['source_edge_id']}']")
            reversed_reference = list(reversed(_parse_shape(other.get("shape", ""))))
            if edge.get("from") != other.get("to") or edge.get("to") != other.get("from") or len(reference) < 2 or len(reference) != len(reversed_reference) or any(math.dist(a, b) > 0.1 for a, b in zip(reference, reversed_reference)):
                continue
            group_ids = {incoming["source_edge_id"], outgoing["source_edge_id"]}
            if any(sum(row["source_edge_id"] == key for row in result) != 1 for key in group_ids):
                result[i] = ports[i]
                reviews.append({"source_lane_id": incoming["source_lane_id"], "reason": "multilane_reverse_road_requires_a_shared_section"})
                break
            vector = incoming["native_lane_direction"]
            norm = math.hypot(*vector)
            axis = (-vector[0] / norm, -vector[1] / norm)
            center = incoming["junction_endpoint_network"]
            lane = other.find(f"lane[@id='{outgoing['source_lane_id']}']")
            shape = _parse_shape(lane.get("shape", ""))
            remote = root.find(f"junction[@id='{other.get('to')}']")
            remote_polygon = _parse_shape(remote.get("shape", "")) if remote is not None else []
            options, station = [], 0.0
            total = sum(math.dist(a, b) for a, b in zip(shape, shape[1:]))
            for a, b in zip(shape, shape[1:]):
                length = math.dist(a, b)
                projection = sum((b[k] - a[k]) * axis[k] for k in (0, 1))
                if length > 1e-9 and projection > length * math.cos(math.pi / 4):
                    t = sum((center[k] - a[k]) * axis[k] for k in (0, 1)) / projection
                    position = station + t * length
                    if -1e-9 <= t <= 1 + 1e-9 and 0 <= position <= min(maximum_movement_m, total - 0.2):
                        point = tuple(a[k] + min(1.0, max(0.0, t)) * (b[k] - a[k]) for k in (0, 1))
                        section = _port_section(point, [point, (point[0] + axis[0], point[1] + axis[1])], outgoing["width_m"] * length / projection)
                        kept = _slice_shape(shape, position, total)
                        probe_edge, probe_lane = deepcopy(other), deepcopy(lane)
                        probe_edge.set("shape", _shape(kept))
                        probe_lane.set("shape", _shape(kept))
                        try:
                            _safe_native_cut_port(root, probe_edge, probe_lane, "egress", maximum_movement_m=0.0)
                        except ValueError:
                            continue
                        # An oblique road mouth is wider than the perpendicular
                        # lane section used by the owner-exit check above.
                        if remote_polygon and _section_touches_polygon(section, remote_polygon):
                            continue
                        if not any(_proper_segments_intersect(*section, *fixed_port["shape"], tolerance_m=1e-6) for fixed_port in fixed):
                            # Only a shared polyline vertex is one crossing.
                            # A later visit to the same point is another cut.
                            if not any(math.dist(point, old[0]) <= 0.1 and abs(position - old[2]) <= 1e-6 for old in options):
                                options.append((point, section, position, (b[0] - a[0], b[1] - a[1])))
                station += length
            if len(options) != 1:
                result[i] = ports[i]
                reviews.append({"source_lane_id": incoming["source_lane_id"], "reason": "no_unique_reverse_road_section_preserving_official_ports"})
                break
            point, section, position, direction = options[0]
            profile_id = "paired-road:" + "|".join(sorted(group_ids))
            result[i] = {**incoming, "road_profile_id": profile_id, "road_outward_direction": axis}
            result[j] = {**outgoing, "junction_endpoint_network": point, "shape": section,
                         "native_lane_direction": direction, "road_profile_id": profile_id, "road_outward_direction": axis,
                         "basis": "native_bidirectional_common_section",
                         "adjustment": {"original_native_endpoint": outgoing["junction_endpoint_network"], "source_lane_position_m": position,
                                        "width_m": outgoing["width_m"], "source_owner_exit_preserved": True,
                                        "reason": "reverse_source_nodes_and_reference_share_the_inlet_cut"}}
            break
    return result, reviews


def _resolved_boundary_polygon(root, group):
    classification = group.get("boundary_port_classification")
    if classification is None:
        return group.get("official_boundary_shape", []), {"status": "review_required", "basis": "legacy_explicit_boundary_unverified", "reason": "official_lane_identity_not_available", "ports": []}
    join_id = str(group["join_id"])
    members = {join_id} if root.find(f"junction[@id='{join_id}']") is not None else set(map(str, group["source_node_ids"]))
    official = defaultdict(list)
    for row in classification["official_external_ports"]:
        official[row["source_lane_id"]].append(row)
    ports = []
    for edge in root.findall("edge"):
        if edge.get("function") == "internal" or (edge.get("from") in members) == (edge.get("to") in members):
            continue
        role = "ingress" if edge.get("to") in members else "egress"
        for lane_index, lane in enumerate(edge.findall("lane")):
            shape = _parse_shape(lane.get("shape", ""))
            if not lane_supports_motorized(lane) or len(shape) < 2:
                continue
            width = float(lane.get("width", "3.2"))
            a, b = shape[-2:] if role == "ingress" else shape[:2]
            native = {"source_lane_index": lane_index, "native_cut_endpoint": shape[-1] if role == "ingress" else shape[0],
                      "native_lane_direction": (b[0] - a[0], b[1] - a[1])}
            matched = [row for row in official[lane.get("id")] if row["role"] == role]
            if matched:
                for row in matched:
                    envelope_width = max(width, row["width_m"])
                    center = row["junction_endpoint_network"]
                    section = [tuple(center[i] + (point[i] - center[i]) * envelope_width / row["width_m"] for i in (0, 1)) for point in row["shape"]]
                    ports.append({**row, **native, "width_m": envelope_width, "shape": section})
            else:
                safe = _safe_native_cut_port(root, edge, lane, role,
                                             maximum_movement_m=classification.get("maximum_lane_projection_error_m", 10.0))
                ports.append({**native, "lane_id": None, "source_lane_id": lane.get("id"), "source_edge_id": edge.get("id"), "role": role,
                              **safe})
    ports, adjustment_reviews = _adjust_native_port_conflicts(root, ports, maximum_movement_m=classification.get("maximum_lane_projection_error_m", 10.0))
    report = {"basis": "actual_cut_ports_with_official_external_B_and_native_lane_widths", "ports": ports, "native_port_adjustment_reviews": adjustment_reviews}
    node = root.find(f"junction[@id='{join_id}']")
    native_boundary = _parse_shape(node.get("shape", "")) if node is not None else []
    try:
        polygon, profiles = _portal_boundary_polygon(ports, native_boundary)
        report["portal_profiles"] = profiles
        return polygon, report
    except ValueError as error:
        report["reason"] = str(error)
    report["status"] = "review_required"
    return [], report


def _portal_boundary_polygon(ports, native_boundary):
    """Keep each current road mouth intact around the native boundary.

    SUMO's NBNodeShapeComputer operates on ordered road boundary pairs, not
    a global sort of individual lane corners. Preserve that road-level order.
    """
    from ..hamburg_official_intersection_plainxml import _polygon_has_self_intersection, _project_point_onto_polyline

    grouped = defaultdict(list)
    for port in ports:
        grouped[port.get("road_profile_id", port["source_edge_id"])].append(port)
    for index, left in enumerate(ports):
        for right in ports[index + 1:]:
            if left["source_edge_id"] == right["source_edge_id"]:
                continue
            overlap = _proper_segments_intersect(*left["shape"], *right["shape"], tolerance_m=1e-6)
            a, b = left["shape"]
            dx, dy = b[0] - a[0], b[1] - a[1]
            length = math.hypot(dx, dy)
            if length > 0 and all(abs(dx * (p[1] - a[1]) - dy * (p[0] - a[0])) / length <= 1e-6 for p in right["shape"]):
                positions = [((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / length for p in right["shape"]]
                overlap |= min(length, max(positions)) - max(0.0, min(positions)) > 0.1
            if overlap:
                raise ValueError(f"physical port profiles overlap: {left['source_edge_id']}, {right['source_edge_id']}")
    native_closed = [*native_boundary, native_boundary[0]] if len(native_boundary) >= 3 else []
    native_area = sum(a[0] * b[1] - b[0] * a[1] for a, b in zip(native_closed, native_closed[1:]))
    profiles = []
    for edge_id, rows in grouped.items():
        direction = tuple(sum(row.get("road_outward_direction", tuple((-1 if row["role"] == "ingress" else 1) * value for value in row["native_lane_direction"]))[i] for row in rows) for i in (0, 1))
        normal = (-direction[1], direction[0])
        def lateral(point):
            return point[0] * normal[0] + point[1] * normal[1]
        if len({row["source_edge_id"] for row in rows}) == 1:
            sign = -1 if rows[0]["role"] == "ingress" else 1
            ordered = sorted(rows, key=lambda row: (sign * row["source_lane_index"], lateral(row["junction_endpoint_network"])))
        else:
            ordered = sorted(rows, key=lambda row: lateral(row["junction_endpoint_network"]))
        points = []
        for row in ordered:
            for point in sorted([*row["shape"], row["junction_endpoint_network"]], key=lateral):
                if not points or math.dist(point, points[-1]) > 0.1:
                    points.append(tuple(point))
        midpoint = tuple(sum(row["junction_endpoint_network"][i] for row in rows) / len(rows) for i in (0, 1))
        if native_closed:
            position = _project_point_onto_polyline(midpoint, native_closed)[0]
            order = position if native_area > 0 else -position
        else:
            order = math.atan2(direction[1], direction[0])
        profiles.append({"source_edge_id": edge_id, "points": points, "order": order,
                         "basis": "current_port_position_on_native_boundary" if native_closed else "source_road_direction_preview"})
    profiles.sort(key=lambda row: row["order"])
    polygon = []
    for profile in profiles:
        for point in profile["points"]:
            if not polygon or math.dist(point, polygon[-1]) > 0.1:
                polygon.append(point)
    if len(polygon) >= 3 and math.dist(polygon[0], polygon[-1]) <= 0.1:
        polygon.pop()
    area = sum(a[0] * b[1] - b[0] * a[1] for a, b in zip(polygon, [*polygon[1:], polygon[0]])) if polygon else 0
    if len(polygon) < 3 or abs(area) <= 0.01 or _polygon_has_self_intersection(polygon, tolerance_m=0.1):
        raise ValueError("ordered physical port profiles do not form a simple polygon")
    return polygon, profiles


def _rebuild_join_boundaries(
    source: Path,
    output: Path,
    groups: Sequence[Mapping[str, Any]],
    *,
    netconvert_binary: str,
    timeout_seconds: float,
) -> dict[str, Any]:
    """Apply external cut-port boundaries without moving internal MAP anchors."""
    source_root = ET.parse(source).getroot()
    nodes = ET.Element("nodes")
    boundaries = []
    for group in groups:
        polygon, detail = _resolved_boundary_polygon(source_root, group)
        boundaries.append({"join_id": group["join_id"], "shape": polygon, **detail})
        if polygon:
            ET.SubElement(nodes, "node", id=str(group["join_id"]), shape=_shape(polygon))
    if not len(nodes):
        shutil.copy2(source, output)
        return {"status": "not_applicable", "reason": "no_resolved_boundary_polygon", "boundaries": boundaries, "network": str(output)}
    patch_file = output.with_suffix(".boundaries.nod.xml")
    ET.indent(nodes, space="    ")
    ET.ElementTree(nodes).write(patch_file, encoding="utf-8", xml_declaration=True)
    command = [str(netconvert_binary), "--sumo-net-file", str(source), "--node-files", str(patch_file), "--offset.disable-normalization", "true", "--junctions.internal-link-detail", "25", "--output-file", str(output)]
    result = run_command(command, cwd=output.parent, timeout_seconds=timeout_seconds)
    if result.returncode != 0 or not output.is_file():
        raise ValueError("netconvert could not apply the official junction boundary: " + result.stderr)
    bounded = ET.parse(output).getroot()
    from ..hamburg_aerial_approach import _preserved_edge

    stable_edges = {edge.get("id"): edge for edge in bounded.findall("edge") if edge.get("function") != "internal"}
    roundtrip_file = output.with_suffix(".roundtrip.net.xml")
    check_command = [str(netconvert_binary), "--sumo-net-file", str(output), "--offset.disable-normalization", "true", "--junctions.internal-link-detail", "25", "--output-file", str(roundtrip_file)]
    checked = run_command(check_command, cwd=output.parent, timeout_seconds=timeout_seconds)
    if checked.returncode != 0 or not roundtrip_file.is_file():
        raise ValueError("netconvert could not verify boundary round-trip stability: " + checked.stderr)
    reloaded_edges = {edge.get("id"): edge for edge in ET.parse(roundtrip_file).getroot().findall("edge") if edge.get("function") != "internal"}
    unstable = [edge_id for edge_id, edge in stable_edges.items() if edge_id not in reloaded_edges or not _preserved_edge(edge, reloaded_edges[edge_id])]
    if unstable or set(stable_edges) != set(reloaded_edges):
        raise ValueError("boundary lane geometry is not stable under netconvert reload: " + ", ".join(unstable))
    basis = "external_cut_ports_and_unverified_legacy_shapes" if any(row["basis"] == "legacy_explicit_boundary_unverified" for row in boundaries) else "actual_external_cut_ports"
    return {"status": "pass", "basis": basis, "boundaries": boundaries, "node_patch": str(patch_file), "network": str(output), "command": command, "stdout": result.stdout, "stderr": result.stderr,
            "cut_geometry": {"roundtrip_stable": True, "tolerance_m": 0.1, "command": check_command, "roundtrip_network": str(roundtrip_file)}}
