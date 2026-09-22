from __future__ import annotations

from itertools import combinations

from .geometry import (
    abs_angle_between,
    classify_angle_relation,
    euclidean_distance,
    normalize_signed_angle,
    segment_distance,
    segment_intersection,
)
from .schema import (
    Approach,
    IntersectionCore,
    OSMPatch,
    OSMWay,
    RoadPairAngle,
    RoadPairDistance,
    RoadPairRelation,
    RoadPairRelationGraph,
)


def build_road_pair_relation_graph(
    patch: OSMPatch,
    core: IntersectionCore,
    approaches: list[Approach],
) -> RoadPairRelationGraph:
    relations = [_relation_for_pair(patch, core, a, b) for a, b in combinations(approaches, 2)]
    return RoadPairRelationGraph(
        relations=relations,
        missing_connection_count=sum(r.error_type == "missing_connection" for r in relations),
        wrong_connection_count=sum(r.error_type == "wrong_connection" for r in relations),
        overlap_conflict_count=sum(r.error_type == "topology_overlap" for r in relations),
        near_miss_count=sum(r.geometry_relation == "near_miss" for r in relations),
        duplicate_parallel_count=sum(r.error_type == "duplicate_parallel_edge" for r in relations),
        blocking_error_count=sum(r.severity == "blocking" for r in relations),
    )


def _relation_for_pair(
    patch: OSMPatch,
    core: IntersectionCore,
    road_a: Approach,
    road_b: Approach,
) -> RoadPairRelation:
    shared_nodes = sorted(_node_refs_for(patch, road_a) & _node_refs_for(patch, road_b))
    points_a = _approach_points(patch, road_a, core.center_xy)
    points_b = _approach_points(patch, road_b, core.center_xy)
    segments_a = _segments(points_a)
    segments_b = _segments(points_b)
    crossing = _first_crossing(segments_a, segments_b)
    min_distance, nearest_a, nearest_b = _min_segment_distance(segments_a, segments_b)
    endpoint_gap = min(euclidean_distance(a, b) for a in _endpoints(points_a) for b in _endpoints(points_b))
    layer_separation = _layer_separation(patch, road_a, road_b)

    signed_delta = normalize_signed_angle(road_b.bearing_from_core - road_a.bearing_from_core)
    abs_delta = abs_angle_between(road_a.bearing_from_core, road_b.bearing_from_core)
    angle_class = classify_angle_relation(abs_delta)

    if shared_nodes:
        geometry_relation = "shared_node"
        topology_relation = "connected"
        expected_relation = "should_connect"
        error_type = "none"
        suggested_fix = "none"
    elif crossing is not None:
        geometry_relation = "crossing_without_node"
        topology_relation = "disconnected"
        expected_relation = "maybe_connect"
        error_type = "topology_overlap"
        suggested_fix = "split_edge_at_crossing"
    elif endpoint_gap <= 5:
        geometry_relation = "near_miss"
        topology_relation = "disconnected"
        expected_relation = "should_connect"
        error_type = "missing_connection"
        suggested_fix = "join_nodes"
    elif angle_class in {"same_direction", "opposite_direction"} and min_distance <= 5:
        geometry_relation = "parallel"
        topology_relation = "ambiguous"
        expected_relation = "unknown"
        error_type = "duplicate_parallel_edge"
        suggested_fix = "merge_duplicate_edges"
    else:
        geometry_relation = "disjoint"
        topology_relation = "disconnected"
        expected_relation = "unknown"
        error_type = "none"
        suggested_fix = "none"

    if layer_separation is not None:
        topology_relation = "disconnected"
        expected_relation = "should_not_connect"
        error_type = "none"
        suggested_fix = "preserve_separate_levels"

    return RoadPairRelation(
        relation_id=f"{road_a.approach_id}__{road_b.approach_id}",
        road_a_id=road_a.approach_id,
        road_b_id=road_b.approach_id,
        road_a_source_way_ids=road_a.source_way_ids,
        road_b_source_way_ids=road_b.source_way_ids,
        geometry_relation=geometry_relation,
        topology_relation=topology_relation,
        expected_relation=expected_relation,
        angle=RoadPairAngle(
            road_a_bearing_deg=road_a.bearing_from_core,
            road_b_bearing_deg=road_b.bearing_from_core,
            signed_delta_deg=signed_delta,
            abs_delta_deg=abs_delta,
            relation_class=angle_class,
            turn_angle_from_a_to_b_deg=signed_delta,
        ),
        distance=RoadPairDistance(
            endpoint_gap_m=endpoint_gap,
            min_geometry_distance_m=min_distance,
            projected_intersection_xy=crossing,
            overlap_length_m=0.0,
            overlap_ratio_a=0.0,
            overlap_ratio_b=0.0,
            crossing_point_inside_segments=crossing is not None,
            nearest_point_a_xy=nearest_a,
            nearest_point_b_xy=nearest_b,
        ),
        inferred_turn=_turn_from_signed_delta(signed_delta),
        error_type=error_type,
        suggested_fix=suggested_fix,
        severity=_severity(error_type, suggested_fix),
        confidence=0.9 if geometry_relation in {"shared_node", "near_miss", "crossing_without_node"} else 0.5,
        evidence=_evidence(geometry_relation, shared_nodes, endpoint_gap, crossing, layer_separation),
    )


def _node_refs_for(patch: OSMPatch, road: Approach) -> set[str]:
    refs: set[str] = set()
    for way_id in road.source_way_ids:
        way = patch.ways.get(way_id)
        if way is not None:
            refs.update(way.node_refs)
    return refs


def _approach_points(patch: OSMPatch, road: Approach, center_xy: tuple[float, float]) -> list[tuple[float, float]]:
    if road.source_shape_xy:
        return road.source_shape_xy
    points = [
        (patch.nodes[ref].x or 0.0, patch.nodes[ref].y or 0.0)
        for way_id in road.source_way_ids
        for ref in patch.ways.get(way_id, OSMWay(id="", node_refs=[], tags={})).node_refs
        if ref in patch.nodes
    ]
    return points or [road.endpoint_xy or center_xy]


def _segments(points: list[tuple[float, float]]) -> list[tuple[tuple[float, float], tuple[float, float]]]:
    if len(points) < 2:
        return [(points[0], points[0])]
    return list(zip(points, points[1:], strict=False))


def _endpoints(points: list[tuple[float, float]]) -> list[tuple[float, float]]:
    return [points[0], points[-1]]


def _first_crossing(segments_a, segments_b):
    for segment_a in segments_a:
        for segment_b in segments_b:
            crossing = segment_intersection(*segment_a, *segment_b)
            if crossing is not None:
                return crossing
    return None


def _min_segment_distance(segments_a, segments_b):
    best = (float("inf"), segments_a[0][0], segments_b[0][0])
    for segment_a in segments_a:
        for segment_b in segments_b:
            distance = segment_distance(*segment_a, *segment_b)
            if distance < best[0]:
                best = (distance, segment_a[0], segment_b[0])
    return best


def _turn_from_signed_delta(delta: float) -> str:
    if abs(delta) > 155:
        return "uturn"
    if abs(delta) < 25:
        return "straight"
    return "right" if delta > 0 else "left"


def _evidence(geometry_relation: str, shared_nodes: list[str], endpoint_gap: float, crossing, layer_separation: str | None):
    evidence = []
    if geometry_relation == "shared_node":
        evidence.extend(f"shared_node:{node_id}" for node_id in shared_nodes)
    if geometry_relation == "near_miss":
        evidence.append(f"endpoint_gap_m:{endpoint_gap:.2f}")
    if geometry_relation == "crossing_without_node":
        evidence.append(f"crossing_xy:{crossing[0]:.2f},{crossing[1]:.2f}")
    if not evidence:
        evidence.append(f"geometry_relation:{geometry_relation}")
    if layer_separation is not None:
        evidence.append(f"layer_separation:{layer_separation}")
    return evidence


def _layer_separation(patch: OSMPatch, road_a: Approach, road_b: Approach) -> str | None:
    a_key, a_label = _level_info(patch, road_a)
    b_key, b_label = _level_info(patch, road_b)
    if a_key == b_key:
        return None
    return f"{a_label}/{b_label}"


def _level_info(patch: OSMPatch, road: Approach) -> tuple[str, str]:
    keys = []
    layers = []
    for way_id in road.source_way_ids:
        tags = patch.ways.get(way_id, OSMWay(id="", node_refs=[], tags={})).tags
        layer = tags.get("layer", "0")
        layers.append(layer)
        bridge = "bridge" if tags.get("bridge") not in {None, "", "no"} else ""
        tunnel = "tunnel" if tags.get("tunnel") not in {None, "", "no"} else ""
        keys.append(":".join(part for part in (layer, bridge, tunnel) if part))
    return ",".join(sorted(set(keys))) or "0", ",".join(sorted(set(layers))) or "0"


def _severity(error_type: str, suggested_fix: str) -> str:
    if suggested_fix == "preserve_separate_levels" or error_type == "none":
        return "none"
    if error_type == "duplicate_parallel_edge":
        return "diagnostic"
    if suggested_fix == "manual_review" or error_type == "ambiguous":
        return "manual_review"
    return "blocking"
