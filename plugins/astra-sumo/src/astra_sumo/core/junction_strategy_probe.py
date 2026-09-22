from __future__ import annotations

import csv
import json
import math
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

from .junction_footprint import build_lane_buffered_approach_footprint

Point = tuple[float, float]


def convex_hull(points: list[Point]) -> list[Point]:
    unique = sorted(set(points))
    if len(unique) <= 1:
        return unique

    def cross(origin: Point, a: Point, b: Point) -> float:
        return (a[0] - origin[0]) * (b[1] - origin[1]) - (a[1] - origin[1]) * (b[0] - origin[0])

    lower: list[Point] = []
    for point in unique:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], point) <= 0:
            lower.pop()
        lower.append(point)

    upper: list[Point] = []
    for point in reversed(unique):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], point) <= 0:
            upper.pop()
        upper.append(point)

    return lower[:-1] + upper[:-1]


def parse_net(net_file: Path) -> dict[str, Any]:
    root = ET.parse(net_file).getroot()
    location = root.find("location")
    junctions = {}
    for junction in root.findall("junction"):
        junction_id = junction.attrib.get("id", "")
        if not junction_id:
            continue
        point = (float(junction.attrib.get("x", "0")), float(junction.attrib.get("y", "0")))
        junctions[junction_id] = {
            "id": junction_id,
            "type": junction.attrib.get("type", ""),
            "point": point,
            "shape": _parse_shape(junction.attrib.get("shape", "")) or [point],
            "internal": junction_id.startswith(":"),
        }

    edges = []
    for edge in root.findall("edge"):
        edge_id = edge.attrib.get("id", "")
        lanes = edge.findall("lane")
        shape = _parse_shape(lanes[0].attrib.get("shape", "")) if lanes else []
        length = _edge_length(edge, lanes, shape)
        function = edge.attrib.get("function", "")
        lane_width = _lane_width(lanes)
        edges.append(
            {
                "id": edge_id,
                "from": edge.attrib.get("from", ""),
                "to": edge.attrib.get("to", ""),
                "type": edge.attrib.get("type", ""),
                "allow": edge.attrib.get("allow", "") or " ".join(lane.attrib.get("allow", "") for lane in lanes),
                "disallow": edge.attrib.get("disallow", "")
                or " ".join(lane.attrib.get("disallow", "") for lane in lanes),
                "function": function,
                "length": length,
                "shape": shape,
                "lane_count": len(lanes) or 1,
                "lane_width": lane_width,
                "internal": edge_id.startswith(":") or function in {"internal", "crossing", "walkingarea"},
            }
        )
    return {
        "junctions": junctions,
        "edges": edges,
        "location": dict(location.attrib) if location is not None else {},
        "net_file": str(net_file),
    }


def strategy_node_sets(
    net: dict[str, Any],
    *,
    seed_node_ids: list[str],
    radius_m: float,
    short_edge_m: float,
) -> dict[str, set[str]]:
    junctions = net["junctions"]
    seeds = {node_id for node_id in seed_node_ids if node_id in junctions}
    if not seeds:
        return {
            "reference_core": set(),
            "radius_all_top_level": set(),
            "short_vehicle_core": set(),
            "short_all_core": set(),
            "edge_bounded_short_core": set(),
            "short_all_core_with_protected_terminals": set(),
        }

    center = _centroid([junctions[node_id]["point"] for node_id in seeds])
    local = {
        node_id
        for node_id, junction in junctions.items()
        if not junction["internal"] and _distance(center, junction["point"]) <= radius_m
    }
    short_all_core = _short_edge_component(net, seeds, local, short_edge_m, vehicle_only=False)
    return {
        "reference_core": set(seeds),
        "radius_all_top_level": local,
        "short_vehicle_core": _short_edge_component(net, seeds, local, short_edge_m, vehicle_only=True),
        "short_all_core": short_all_core,
        "edge_bounded_short_core": short_all_core,
        "short_all_core_with_protected_terminals": short_all_core,
    }


def probe_junction_strategies(
    *,
    candidate_net_file: Path,
    reference_net_file: Path,
    reference_junction_id: str,
    output_dir: Path,
    radius_m: float = 40.0,
    short_edge_m: float = 2.0,
    gate_margin_m: float = 1.0,
    approach_setback_m: float = 8.0,
) -> dict[str, Any]:
    candidate = parse_net(candidate_net_file)
    reference = parse_net(reference_net_file)
    if reference_junction_id not in reference["junctions"]:
        return _failure(f"reference junction not found: {reference_junction_id}")

    seed_ids = _source_node_ids_from_cluster(reference_junction_id)
    node_sets = strategy_node_sets(candidate, seed_node_ids=seed_ids, radius_m=radius_m, short_edge_m=short_edge_m)
    reference_polygon_raw = convex_hull(reference["junctions"][reference_junction_id]["shape"])
    alignment_target = _candidate_seed_centroid(candidate, seed_ids)
    reference_polygon = _translate_polygon_to_centroid(reference_polygon_raw, alignment_target)

    strategies = {}
    for name, node_ids in node_sets.items():
        protected_terminals: set[str] = set()
        shape_support_node_ids = set(node_ids)
        if name == "short_all_core_with_protected_terminals":
            node_ids, protected_terminals = _split_protected_terminals(candidate, node_ids, set(seed_ids), short_edge_m)
            shape_support_node_ids = set(node_ids) | protected_terminals
        polygon = (
            _edge_bounded_footprint_for_nodes(candidate, node_ids, gate_margin_m)
            if name == "edge_bounded_short_core"
            else _footprint_for_nodes(candidate, shape_support_node_ids)
        )
        strategies[name] = {
            "node_count": len(node_ids),
            "node_ids": sorted(node_ids),
            "protected_terminal_ids": sorted(protected_terminals),
            "shape_support_node_ids": sorted(shape_support_node_ids),
            "local_metrics": _local_metrics(candidate, node_ids, short_edge_m=short_edge_m),
            "footprint": _footprint_metrics(polygon, reference_polygon),
            "polygon": polygon,
        }
    approach_node_ids = node_sets["reference_core"]
    approach_support_edges = _approach_setback_support_edges(candidate, approach_node_ids)
    approach_polygon = _approach_setback_footprint_for_edges(
        candidate, approach_node_ids, approach_support_edges, approach_setback_m
    )
    strategies["approach_setback_core"] = {
        "node_count": len(approach_node_ids),
        "node_ids": sorted(approach_node_ids),
        "collapse_node_ids": sorted(approach_node_ids),
        "inside_plain_edge_ids": _inside_plain_edge_ids(candidate, approach_node_ids),
        "boundary_edge_ids": _boundary_edge_ids(candidate, approach_node_ids),
        "protected_terminal_ids": [],
        "shape_support_node_ids": sorted(approach_node_ids),
        "shape_support_edge_ids": sorted(edge["id"] for edge in approach_support_edges),
        "local_metrics": _local_metrics(candidate, approach_node_ids, short_edge_m=short_edge_m),
        "footprint": _footprint_metrics(approach_polygon, reference_polygon),
        "polygon": approach_polygon,
    }
    buffered = build_lane_buffered_approach_footprint(
        candidate,
        approach_node_ids,
        setback_m=approach_setback_m,
    )
    strategies["lane_buffered_approach_setback_core"] = {
        "node_count": len(approach_node_ids),
        "node_ids": sorted(approach_node_ids),
        "collapse_node_ids": sorted(approach_node_ids),
        "inside_plain_edge_ids": _inside_plain_edge_ids(candidate, approach_node_ids),
        "boundary_edge_ids": _boundary_edge_ids(candidate, approach_node_ids),
        "protected_terminal_ids": [],
        "shape_support_node_ids": sorted(approach_node_ids),
        "shape_support_edge_ids": buffered["shape_support_edge_ids"],
        "review_support_edge_ids": buffered["review_support_edge_ids"],
        "buffer_point_count": buffered["buffer_point_count"],
        "local_metrics": _local_metrics(candidate, approach_node_ids, short_edge_m=short_edge_m),
        "footprint": _footprint_metrics(buffered["polygon"], reference_polygon),
        "polygon": buffered["polygon"],
    }

    output_dir.mkdir(parents=True, exist_ok=True)
    summary_file = output_dir / "junction_strategy_probe.json"
    csv_file = output_dir / "junction_strategy_probe.csv"
    svg_file = output_dir / "junction_strategy_probe.svg"
    png_file = output_dir / "junction_strategy_probe.png"
    report = {
        "status": "pass",
        "claim_status": "diagnostic-demo",
        "candidate_net_file": str(candidate_net_file),
        "reference_net_file": str(reference_net_file),
        "reference_junction_id": reference_junction_id,
        "seed_node_ids": seed_ids,
        "radius_m": radius_m,
        "short_edge_m": short_edge_m,
        "gate_margin_m": gate_margin_m,
        "approach_setback_m": approach_setback_m,
        "coordinate_alignment": "reference polygon translated to candidate seed-node centroid",
        "summary_file": str(summary_file),
        "csv_file": str(csv_file),
        "svg_file": str(svg_file),
        "png_file": str(png_file),
        "reference_polygon_raw": reference_polygon_raw,
        "reference_polygon": reference_polygon,
        "nearby_conflict_zone_audit": _nearby_conflict_zone_audit(reference, reference_junction_id),
        "strategies": strategies,
    }
    summary_file.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    _write_csv(csv_file, strategies)
    _write_svg(svg_file, candidate, reference_polygon, strategies)
    _write_png(png_file, candidate, reference_polygon, strategies)
    return report


def _short_edge_component(
    net: dict[str, Any],
    seeds: set[str],
    local: set[str],
    short_edge_m: float,
    *,
    vehicle_only: bool,
) -> set[str]:
    selected = set(seeds)
    changed = True
    while changed:
        changed = False
        for edge in net["edges"]:
            if edge["internal"] or edge["length"] > short_edge_m:
                continue
            if vehicle_only and not _is_vehicle_core_edge(edge):
                continue
            a, b = edge["from"], edge["to"]
            if a not in local or b not in local:
                continue
            if a in selected and b not in selected:
                selected.add(b)
                changed = True
            elif b in selected and a not in selected:
                selected.add(a)
                changed = True
    return selected


def _is_vehicle_core_edge(edge: dict[str, Any]) -> bool:
    text = f"{edge['type']} {edge.get('allow', '')}".lower()
    return not any(token in text for token in ("footway", "cycleway", "pedestrian", "bicycle", "service"))


def _is_footprint_support_edge(edge: dict[str, Any]) -> bool:
    text = f"{edge['type']} {edge.get('allow', '')}".lower()
    if any(token in text for token in ("footway", "cycleway", "pedestrian", "bicycle")):
        return False
    return _is_vehicle_core_edge(edge) or any(token in text for token in ("service", "access"))


def _footprint_for_nodes(net: dict[str, Any], node_ids: set[str]) -> list[Point]:
    points: list[Point] = []
    for node_id in node_ids:
        junction = net["junctions"].get(node_id)
        if junction:
            points.extend(junction["shape"])
    return convex_hull(points)


def _edge_bounded_footprint_for_nodes(net: dict[str, Any], node_ids: set[str], gate_margin_m: float) -> list[Point]:
    gates: list[Point] = []
    for edge in net["edges"]:
        if edge["internal"] or not edge["shape"]:
            continue
        from_inside = edge["from"] in node_ids
        to_inside = edge["to"] in node_ids
        if from_inside == to_inside:
            continue
        gates.append(edge["shape"][0] if from_inside else edge["shape"][-1])
    if len(gates) < 3:
        return _footprint_for_nodes(net, node_ids)
    return convex_hull(_expand_points_from_centroid(gates, gate_margin_m))


def _approach_setback_footprint_for_nodes(
    net: dict[str, Any],
    node_ids: set[str],
    approach_setback_m: float,
) -> list[Point]:
    return _approach_setback_footprint_for_edges(
        net, node_ids, _approach_setback_support_edges(net, node_ids), approach_setback_m
    )


def _approach_setback_support_edges(net: dict[str, Any], node_ids: set[str]) -> list[dict[str, Any]]:
    return [
        edge
        for edge in net["edges"]
        if not edge["internal"]
        and edge["shape"]
        and _is_footprint_support_edge(edge)
        and ((edge["from"] in node_ids) != (edge["to"] in node_ids))
    ]


def _approach_setback_footprint_for_edges(
    net: dict[str, Any],
    node_ids: set[str],
    edges: list[dict[str, Any]],
    approach_setback_m: float,
) -> list[Point]:
    gates: list[Point] = []
    for edge in edges:
        from_inside = edge["from"] in node_ids
        gates.append(
            _point_along_shape(edge["shape"], approach_setback_m)
            if from_inside
            else _point_before_shape_end(edge["shape"], approach_setback_m)
        )
    if len(gates) < 3:
        return _edge_bounded_footprint_for_nodes(net, node_ids, 0.0)
    return convex_hull(gates)


def _inside_plain_edge_ids(net: dict[str, Any], node_ids: set[str]) -> list[str]:
    return sorted(
        edge["id"]
        for edge in net["edges"]
        if not edge["internal"] and edge["from"] in node_ids and edge["to"] in node_ids
    )


def _boundary_edge_ids(net: dict[str, Any], node_ids: set[str]) -> list[str]:
    return sorted(
        edge["id"]
        for edge in net["edges"]
        if not edge["internal"] and ((edge["from"] in node_ids) != (edge["to"] in node_ids))
    )


def _split_protected_terminals(
    net: dict[str, Any],
    node_ids: set[str],
    seed_ids: set[str],
    short_edge_m: float,
) -> tuple[set[str], set[str]]:
    protected = {
        node_id
        for node_id in node_ids - seed_ids
        if _has_long_modal_outside_edge(net, node_ids, node_id, short_edge_m)
    }
    return node_ids - protected, protected


def _has_long_modal_outside_edge(
    net: dict[str, Any],
    node_ids: set[str],
    node_id: str,
    short_edge_m: float,
) -> bool:
    for edge in net["edges"]:
        if edge["internal"] or (edge["from"] != node_id and edge["to"] != node_id):
            continue
        other = edge["to"] if edge["from"] == node_id else edge["from"]
        if other not in node_ids and edge["length"] > short_edge_m and not _is_vehicle_core_edge(edge):
            return True
    return False


def _nearby_conflict_zone_audit(
    reference: dict[str, Any],
    reference_junction_id: str,
    threshold_m: float = 50.0,
) -> dict[str, Any]:
    target = reference["junctions"][reference_junction_id]
    nearby = []
    for junction_id, junction in reference["junctions"].items():
        if junction_id == reference_junction_id or junction["internal"] or not _is_conflict_zone_candidate(junction):
            continue
        distance_m = _distance(target["point"], junction["point"])
        if distance_m <= threshold_m:
            latlon = _xy_to_latlon(reference, junction["point"])
            nearby.append(
                {
                    "junction_id": junction_id,
                    "type": junction["type"],
                    "distance_m": round(distance_m, 3),
                    "lat": round(latlon[0], 8),
                    "lon": round(latlon[1], 8),
                    "google_maps_url": _google_maps_url(latlon),
                    "osm_url": _osm_url(latlon),
                }
            )
    nearby.sort(key=lambda item: item["distance_m"])
    target_latlon = _xy_to_latlon(reference, target["point"])
    status = "nearby_core_review" if nearby else "single_core_safe"
    return {
        "status": status,
        "claim_status": "diagnostic-demo",
        "threshold_m": threshold_m,
        "target_junction_id": reference_junction_id,
        "target_lat": round(target_latlon[0], 8),
        "target_lon": round(target_latlon[1], 8),
        "target_google_maps_url": _google_maps_url(target_latlon),
        "target_osm_url": _osm_url(target_latlon),
        "nearby_count": len(nearby),
        "nearby_conflict_zones": nearby,
    }


def _is_conflict_zone_candidate(junction: dict[str, Any]) -> bool:
    return junction["type"] == "traffic_light" or (
        junction["type"] == "priority" and junction["id"].startswith("cluster_")
    )


def _xy_to_latlon(net: dict[str, Any], point: Point) -> tuple[float, float]:
    converted = _xy_to_latlon_with_sumolib(net, point)
    if converted:
        return converted
    return _xy_to_latlon_with_location_bounds(net, point)


def _xy_to_latlon_with_sumolib(net: dict[str, Any], point: Point) -> tuple[float, float] | None:
    try:
        import sumolib

        sumo_net = sumolib.net.readNet(net["net_file"])
        lon, lat = sumo_net.convertXY2LonLat(point[0], point[1])
        if math.isfinite(lat) and math.isfinite(lon):
            return (lat, lon)
    except Exception:  # noqa: BLE001 - optional sumolib conversion falls back to location bounds.
        return None
    return None


def _xy_to_latlon_with_location_bounds(net: dict[str, Any], point: Point) -> tuple[float, float]:
    conv = _parse_bounds(net["location"].get("convBoundary", ""))
    orig = _parse_bounds(net["location"].get("origBoundary", ""))
    if not conv or not orig or conv[2] == conv[0] or conv[3] == conv[1]:
        return (0.0, 0.0)
    x_ratio = (point[0] - conv[0]) / (conv[2] - conv[0])
    y_ratio = (point[1] - conv[1]) / (conv[3] - conv[1])
    lon = orig[0] + x_ratio * (orig[2] - orig[0])
    lat = orig[1] + y_ratio * (orig[3] - orig[1])
    return (lat, lon)


def _parse_bounds(raw: str) -> tuple[float, float, float, float] | None:
    parts = raw.split(",")
    if len(parts) != 4:
        return None
    return (float(parts[0]), float(parts[1]), float(parts[2]), float(parts[3]))


def _google_maps_url(latlon: tuple[float, float]) -> str:
    return f"https://www.google.com/maps/@{latlon[0]:.8f},{latlon[1]:.8f},19z"


def _osm_url(latlon: tuple[float, float]) -> str:
    return f"https://www.openstreetmap.org/#map=19/{latlon[0]:.8f}/{latlon[1]:.8f}"


def _expand_points_from_centroid(points: list[Point], margin_m: float) -> list[Point]:
    center = _centroid(points)
    expanded = []
    for point in points:
        distance = _distance(center, point)
        if distance == 0:
            expanded.append(point)
            continue
        scale = (distance + margin_m) / distance
        expanded.append((center[0] + (point[0] - center[0]) * scale, center[1] + (point[1] - center[1]) * scale))
    return expanded


def _point_along_shape(shape: list[Point], distance_m: float) -> Point:
    remaining = distance_m
    for start, end in zip(shape, shape[1:]):
        segment_length = _distance(start, end)
        if segment_length >= remaining:
            ratio = remaining / segment_length if segment_length else 0.0
            return (start[0] + (end[0] - start[0]) * ratio, start[1] + (end[1] - start[1]) * ratio)
        remaining -= segment_length
    return shape[-1]


def _point_before_shape_end(shape: list[Point], distance_m: float) -> Point:
    return _point_along_shape(list(reversed(shape)), distance_m)


def _candidate_seed_centroid(candidate: dict[str, Any], seed_ids: list[str]) -> Point:
    points = [candidate["junctions"][node_id]["point"] for node_id in seed_ids if node_id in candidate["junctions"]]
    return _centroid(points)


def _translate_polygon_to_centroid(polygon: list[Point], target: Point) -> list[Point]:
    if not polygon:
        return []
    center = _centroid(polygon)
    dx = target[0] - center[0]
    dy = target[1] - center[1]
    return [(x + dx, y + dy) for x, y in polygon]


def _local_metrics(net: dict[str, Any], node_ids: set[str], *, short_edge_m: float) -> dict[str, Any]:
    incident = [edge for edge in net["edges"] if edge["from"] in node_ids or edge["to"] in node_ids]
    inside = [edge for edge in incident if edge["from"] in node_ids and edge["to"] in node_ids]
    return {
        "incident_edge_count": len(incident),
        "inside_edge_count": len(inside),
        "short_incident_edge_count": sum(1 for edge in incident if not edge["internal"] and edge["length"] <= short_edge_m),
        "modal_incident_edge_count": sum(1 for edge in incident if not _is_vehicle_core_edge(edge)),
        "internal_incident_edge_count": sum(1 for edge in incident if edge["internal"]),
    }


def _footprint_metrics(polygon: list[Point], reference_polygon: list[Point]) -> dict[str, Any]:
    area = _area(polygon)
    reference_area = _area(reference_polygon)
    return {
        "area": round(area, 3),
        "reference_area": round(reference_area, 3),
        "area_ratio": round(area / reference_area, 3) if reference_area else 0.0,
        "perimeter": round(_perimeter(polygon), 3),
        "centroid_distance_to_reference": round(_distance(_centroid(polygon), _centroid(reference_polygon)), 3),
        "vertex_hausdorff_to_reference": round(_hausdorff(polygon, reference_polygon), 3),
        "sharp_corner_audit": _sharp_corner_audit(polygon),
    }


def _sharp_corner_audit(
    polygon: list[Point],
    *,
    angle_threshold_deg: float = 35.0,
    min_leg_m: float = 1.0,
) -> dict[str, Any]:
    angles = []
    sharp = []
    if len(polygon) < 3:
        return {
            "status": "pass",
            "angle_threshold_deg": angle_threshold_deg,
            "min_leg_m": min_leg_m,
            "min_corner_angle_deg": 0.0,
            "sharp_corner_count": 0,
            "sharp_corner_points": [],
        }
    for index, point in enumerate(polygon):
        prev_point = polygon[index - 1]
        next_point = polygon[(index + 1) % len(polygon)]
        left = _distance(point, prev_point)
        right = _distance(point, next_point)
        if left == 0 or right == 0:
            continue
        angle = _corner_angle_deg(prev_point, point, next_point)
        angles.append(angle)
        if left >= min_leg_m and right >= min_leg_m and angle < angle_threshold_deg:
            sharp.append({"x": round(point[0], 3), "y": round(point[1], 3), "angle_deg": round(angle, 3)})
    return {
        "status": "needs_review" if sharp else "pass",
        "angle_threshold_deg": angle_threshold_deg,
        "min_leg_m": min_leg_m,
        "min_corner_angle_deg": round(min(angles), 3) if angles else 0.0,
        "sharp_corner_count": len(sharp),
        "sharp_corner_points": sharp,
    }


def _corner_angle_deg(prev_point: Point, point: Point, next_point: Point) -> float:
    ax, ay = prev_point[0] - point[0], prev_point[1] - point[1]
    bx, by = next_point[0] - point[0], next_point[1] - point[1]
    denom = math.hypot(ax, ay) * math.hypot(bx, by)
    if denom == 0:
        return 0.0
    cosine = max(-1.0, min(1.0, (ax * bx + ay * by) / denom))
    return math.degrees(math.acos(cosine))


def _write_csv(path: Path, strategies: dict[str, Any]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "strategy",
                "node_count",
                "incident_edge_count",
                "inside_edge_count",
                "short_incident_edge_count",
                "modal_incident_edge_count",
                "internal_incident_edge_count",
                "area",
                "reference_area",
                "area_ratio",
                "perimeter",
                "centroid_distance_to_reference",
                "vertex_hausdorff_to_reference",
                "sharp_corner_count",
                "min_corner_angle_deg",
            ],
        )
        writer.writeheader()
        for name, report in strategies.items():
            footprint = dict(report["footprint"])
            sharp_audit = footprint.pop("sharp_corner_audit", {})
            writer.writerow(
                {
                    "strategy": name,
                    "node_count": report["node_count"],
                    **report["local_metrics"],
                    **footprint,
                    "sharp_corner_count": sharp_audit.get("sharp_corner_count", 0),
                    "min_corner_angle_deg": sharp_audit.get("min_corner_angle_deg", 0.0),
                }
            )


def _write_svg(
    path: Path,
    candidate: dict[str, Any],
    reference_polygon: list[Point],
    strategies: dict[str, Any],
) -> None:
    polygons = [reference_polygon] + [report["polygon"] for report in strategies.values() if report["polygon"]]
    xs = [x for polygon in polygons for x, _ in polygon]
    ys = [y for polygon in polygons for _, y in polygon]
    min_x, max_x = min(xs) - 10, max(xs) + 10
    min_y, max_y = min(ys) - 10, max(ys) + 10
    width, height = max_x - min_x, max_y - min_y

    def map_point(point: Point) -> str:
        x, y = point
        return f"{x - min_x:.2f},{max_y - y:.2f}"

    colors = {
        "reference_core": "#2563eb",
        "radius_all_top_level": "#dc2626",
        "short_vehicle_core": "#16a34a",
        "short_all_core": "#f59e0b",
        "edge_bounded_short_core": "#7c3aed",
        "short_all_core_with_protected_terminals": "#0ea5e9",
        "approach_setback_core": "#be123c",
        "lane_buffered_approach_setback_core": "#059669",
    }
    lines = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width:.2f} {height:.2f}">',
        '<rect width="100%" height="100%" fill="white"/>',
        _svg_polygon(reference_polygon, map_point, "#111827", "none", 3, "TUM reference"),
    ]
    for edge in candidate["edges"]:
        if edge["shape"] and all(min_x <= x <= max_x and min_y <= y <= max_y for x, y in edge["shape"]):
            color = "#9ca3af" if _is_vehicle_core_edge(edge) else "#d1d5db"
            lines.append(
                f'<polyline points="{" ".join(map_point(point) for point in edge["shape"])}" '
                f'stroke="{color}" stroke-width="0.7" fill="none"/>'
            )
    for name, report in strategies.items():
        if report["polygon"]:
            lines.append(_svg_polygon(report["polygon"], map_point, colors.get(name, "#7c3aed"), "none", 2, name))
    lines.append("</svg>")
    path.write_text("\n".join(lines), encoding="utf-8")


def _write_png(
    path: Path,
    candidate: dict[str, Any],
    reference_polygon: list[Point],
    strategies: dict[str, Any],
) -> None:
    from PIL import Image, ImageDraw

    polygons = [reference_polygon] + [report["polygon"] for report in strategies.values() if report["polygon"]]
    xs = [x for polygon in polygons for x, _ in polygon]
    ys = [y for polygon in polygons for _, y in polygon]
    min_x, max_x = min(xs) - 12, max(xs) + 12
    min_y, max_y = min(ys) - 12, max(ys) + 12
    width, height = 1400, 1000
    scale = min(width / (max_x - min_x), height / (max_y - min_y))
    x_offset = (width - (max_x - min_x) * scale) / 2
    y_offset = (height - (max_y - min_y) * scale) / 2

    def map_point(point: Point) -> tuple[float, float]:
        x, y = point
        return (x_offset + (x - min_x) * scale, height - (y_offset + (y - min_y) * scale))

    image = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(image, "RGBA")
    for edge in candidate["edges"]:
        if edge["shape"] and any(min_x <= x <= max_x and min_y <= y <= max_y for x, y in edge["shape"]):
            color = (90, 90, 90, 90) if _is_vehicle_core_edge(edge) else (200, 80, 80, 85)
            line_width = 2 if _is_vehicle_core_edge(edge) else 1
            draw.line([map_point(point) for point in edge["shape"]], fill=color, width=line_width)

    colors = {
        "reference": (17, 24, 39, 255),
        "reference_core": (37, 99, 235, 255),
        "radius_all_top_level": (220, 38, 38, 255),
        "short_vehicle_core": (22, 163, 74, 255),
        "short_all_core": (245, 158, 11, 255),
        "edge_bounded_short_core": (124, 58, 237, 255),
        "short_all_core_with_protected_terminals": (14, 165, 233, 255),
        "approach_setback_core": (190, 18, 60, 255),
        "lane_buffered_approach_setback_core": (5, 150, 105, 255),
    }
    _draw_png_polygon(draw, reference_polygon, map_point, colors["reference"], 6)
    for name, report in strategies.items():
        _draw_png_polygon(draw, report["polygon"], map_point, colors.get(name, (124, 58, 237, 255)), 4)

    legend = [
        ("reference", "TUM reference, aligned"),
        ("reference_core", "reference_core: source ids only"),
        ("radius_all_top_level", "radius_all_top_level: all nearby nodes"),
        ("short_vehicle_core", "short_vehicle_core: short vehicle-edge component"),
        ("short_all_core", "short_all_core: short all-edge component"),
        ("edge_bounded_short_core", "edge_bounded_short_core: bounded by incident edge gates"),
        ("short_all_core_with_protected_terminals", "short_all_core_with_protected_terminals: join core plus protected exits"),
        ("approach_setback_core", "approach_setback_core: vehicle approaches set back from the core"),
        ("lane_buffered_approach_setback_core", "lane_buffered_approach_setback_core: lane-width buffered approaches"),
    ]
    y = 20
    for key, label in legend:
        draw.rectangle([20, y, 48, y + 16], fill=colors[key])
        draw.text((58, y - 2), label, fill=(0, 0, 0, 255))
        y += 26
    image.save(path)


def _draw_png_polygon(
    draw: Any,
    polygon: list[Point],
    map_point: Any,
    color: tuple[int, int, int, int],
    width: int,
) -> None:
    if len(polygon) < 2:
        return
    points = [map_point(point) for point in polygon]
    draw.line(points + [points[0]], fill=color, width=width)


def _svg_polygon(
    polygon: list[Point],
    map_point: Any,
    stroke: str,
    fill: str,
    stroke_width: int,
    label: str,
) -> str:
    points = " ".join(map_point(point) for point in polygon)
    return f'<polygon points="{points}" stroke="{stroke}" stroke-width="{stroke_width}" fill="{fill}"><title>{label}</title></polygon>'


def _source_node_ids_from_cluster(junction_id: str) -> list[str]:
    if not junction_id.startswith("cluster_"):
        return [junction_id]
    return [part for part in junction_id.removeprefix("cluster_").split("_") if part]


def _parse_shape(raw: str) -> list[Point]:
    points = []
    for item in raw.split():
        parts = item.split(",")
        if len(parts) >= 2:
            points.append((float(parts[0]), float(parts[1])))
    return points


def _lane_width(lanes: list[ET.Element]) -> float | None:
    widths = [float(lane.attrib["width"]) for lane in lanes if lane.attrib.get("width")]
    if not widths:
        return None
    return sum(widths) / len(widths)


def _edge_length(edge: ET.Element, lanes: list[ET.Element], shape: list[Point]) -> float:
    if lanes and lanes[0].attrib.get("length"):
        return float(lanes[0].attrib["length"])
    if shape:
        return sum(_distance(a, b) for a, b in zip(shape, shape[1:]))
    return float(edge.attrib.get("length", "0") or 0.0)


def _area(polygon: list[Point]) -> float:
    if len(polygon) < 3:
        return 0.0
    return abs(sum(x1 * y2 - x2 * y1 for (x1, y1), (x2, y2) in zip(polygon, polygon[1:] + polygon[:1]))) / 2


def _perimeter(polygon: list[Point]) -> float:
    if len(polygon) < 2:
        return 0.0
    return sum(_distance(a, b) for a, b in zip(polygon, polygon[1:] + polygon[:1]))


def _centroid(points: list[Point] | set[Point]) -> Point:
    items = list(points)
    if not items:
        return (0.0, 0.0)
    return (sum(x for x, _ in items) / len(items), sum(y for _, y in items) / len(items))


def _hausdorff(a: list[Point], b: list[Point]) -> float:
    if not a or not b:
        return 0.0
    return max(_directed_hausdorff(a, b), _directed_hausdorff(b, a))


def _directed_hausdorff(a: list[Point], b: list[Point]) -> float:
    return max(min(_distance(point, other) for other in b) for point in a)


def _distance(a: Point, b: Point) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def _failure(error: str) -> dict[str, Any]:
    return {"status": "fail", "claim_status": "blocked", "error": error}
