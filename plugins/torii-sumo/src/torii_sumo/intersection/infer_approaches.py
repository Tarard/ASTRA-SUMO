from __future__ import annotations

from collections import defaultdict, deque

from torii_sumo.road_semantics import classify_approach_mode_layer, filtered_osm_modes

from .geometry import abs_angle_between as angle_difference, bearing_between_xy, euclidean_distance
from .infer_core import _adjacent_highway_nodes
from .schema import Approach, IntersectionCore, OSMWay, OSMPatch


MAX_CORRIDOR_EXTENSION_M = 120.0
MAX_CORRIDOR_HEADING_DELTA_DEG = 35.0

ApproachRow = tuple[
    float,
    str,
    OSMWay,
    str,
    list[str],
    str,
    tuple[float, float],
    list[tuple[float, float]],
    list[set[str]],
    list[str],
]


def infer_approaches(patch: OSMPatch, core: IntersectionCore) -> list[Approach]:
    highway_ways = [way for way in patch.ways.values() if "highway" in way.tags]
    adjacent = _adjacent_highway_nodes(highway_ways, core.core_osm_node_ids)
    rows = []
    used_way_ids = set()
    for neighbor_id, way_ids in adjacent.items():
        way = patch.ways[sorted(way_ids)[0]]
        terminal_id, source_way_ids, edge_way_id, corridor_extension_way_ids = _extended_vehicle_corridor_endpoint(
            patch,
            highway_ways,
            core,
            neighbor_id,
            way,
        )
        terminal = patch.nodes[terminal_id]
        endpoint_xy = (terminal.x or 0.0, terminal.y or 0.0)
        bearing = bearing_between_xy(core.center_xy, endpoint_xy)
        source_shape_xy = _approach_shape_xy(patch, core, terminal_id, source_way_ids)
        used_way_ids.update(source_way_ids)
        rows.append(
            (
                bearing,
                neighbor_id,
                way,
                terminal_id,
                source_way_ids,
                edge_way_id,
                endpoint_xy,
                source_shape_xy,
                _vehicle_support_lane_modes(way.tags),
                corridor_extension_way_ids,
            )
        )
    rows = _fuse_support_path_rows(
        rows,
        _crossing_support_path_rows(patch, highway_ways, core, set(adjacent), used_way_ids),
    )
    rows.sort()

    roles = _roles_for_bearings([row[0] for row in rows])
    approaches = []
    for index, (
        (
            bearing,
            neighbor_id,
            way,
            terminal_id,
            source_way_ids,
            edge_way_id,
            endpoint_xy,
            source_shape_xy,
            extra_lane_modes,
            corridor_extension_way_ids,
        ),
        role,
    ) in enumerate(
        zip(rows, roles, strict=True),
        start=1,
    ):
        incoming_lane_count, outgoing_lane_count = _directional_lane_counts(patch, core, terminal_id, source_way_ids)
        has_incoming_vehicle_flow, has_outgoing_vehicle_flow, direction_evidence = _vehicle_flow_direction(
            patch,
            core,
            terminal_id,
            source_way_ids,
            way,
        )
        allowed_modes = _allowed_modes(way.tags)
        mode_layer = classify_approach_mode_layer(allowed_modes, extra_lane_modes, extra_lane_modes)
        approaches.append(
            Approach(
                approach_id=f"leg_{index}",
                role=role,
                source_way_ids=source_way_ids,
                corridor_extension_way_ids=corridor_extension_way_ids,
                road_name=way.tags.get("name"),
                highway_class=way.tags.get("highway", "road"),
                bearing_to_core=(bearing + 180) % 360,
                bearing_from_core=bearing,
                endpoint_xy=endpoint_xy,
                source_shape_xy=source_shape_xy,
                incoming_lane_count=incoming_lane_count,
                outgoing_lane_count=outgoing_lane_count,
                incoming_extra_lane_modes=extra_lane_modes,
                outgoing_extra_lane_modes=extra_lane_modes,
                incoming_edge_ids=[f"{edge_way_id}_{terminal_id}_to_{core.core_id}"],
                outgoing_edge_ids=[f"{edge_way_id}_{core.core_id}_to_{terminal_id}"],
                oneway=_is_oneway(way.tags),
                has_incoming_vehicle_flow=has_incoming_vehicle_flow,
                has_outgoing_vehicle_flow=has_outgoing_vehicle_flow,
                direction_evidence=direction_evidence,
                allowed_modes=allowed_modes,
                mode_layer=mode_layer.mode_layer,
                is_vehicle_approach=mode_layer.is_vehicle_approach,
                is_support_only=mode_layer.is_support_only,
                fused_support_modes=[set(modes) for modes in mode_layer.fused_support_modes],
                turn_lanes_raw=_incoming_turn_lanes_raw(patch, core, terminal_id, source_way_ids),
                access_tags={
                    key: value
                    for key, value in way.tags.items()
                    if key in {"access", "vehicle", "motor_vehicle", "bicycle", "foot"}
                },
            )
        )
    return approaches


def _approach_shape_xy(
    patch: OSMPatch,
    core: IntersectionCore,
    terminal_id: str,
    source_way_ids: list[str],
) -> list[tuple[float, float]]:
    graph = _way_graph(patch, source_way_ids)
    path = _shortest_path_to_core(graph, terminal_id, set(core.core_osm_node_ids))
    if not path:
        return [_node_xy(patch, terminal_id), core.center_xy]

    points = [_node_xy(patch, node_id) for node_id in path[:-1]]
    points.append(core.center_xy)
    return _dedupe_adjacent_points(points)


def _directional_lane_counts(
    patch: OSMPatch,
    core: IntersectionCore,
    terminal_id: str,
    source_way_ids: list[str],
) -> tuple[int, int]:
    tagged = _directional_way_and_direction(patch, core, terminal_id, source_way_ids)
    if tagged is None:
        way = patch.ways[source_way_ids[0]]
        count = _lane_count(way.tags)
        return count, count
    way, incoming_direction = tagged
    total = _lane_count(way.tags)
    incoming = _direction_lane_count(way.tags, incoming_direction)
    outgoing = _direction_lane_count(way.tags, _opposite_direction(incoming_direction))
    if incoming is None:
        incoming = _turn_lane_count(way.tags, incoming_direction)
    if outgoing is None:
        outgoing = _turn_lane_count(way.tags, _opposite_direction(incoming_direction))
    if incoming is None and outgoing is not None:
        incoming = max(1, total - outgoing)
    if outgoing is None and incoming is not None:
        outgoing = max(1, total - incoming)
    return incoming or total, outgoing or total


def _incoming_turn_lanes_raw(
    patch: OSMPatch,
    core: IntersectionCore,
    terminal_id: str,
    source_way_ids: list[str],
) -> str | None:
    tagged = _directional_way_and_direction(patch, core, terminal_id, source_way_ids)
    if tagged is None:
        return patch.ways[source_way_ids[0]].tags.get("turn:lanes")
    way, incoming_direction = tagged
    return way.tags.get(f"turn:lanes:{incoming_direction}") or way.tags.get("turn:lanes")


def _directional_way_and_direction(
    patch: OSMPatch,
    core: IntersectionCore,
    terminal_id: str,
    source_way_ids: list[str],
) -> tuple[OSMWay, str] | None:
    return _path_way_and_direction(
        patch,
        core,
        terminal_id,
        source_way_ids,
        require_directional_lane_data=True,
    )


def _path_way_and_direction(
    patch: OSMPatch,
    core: IntersectionCore,
    terminal_id: str,
    source_way_ids: list[str],
    *,
    require_directional_lane_data: bool = False,
) -> tuple[OSMWay, str] | None:
    return next(
        iter(
            _path_way_directions(
                patch,
                core,
                terminal_id,
                source_way_ids,
                require_directional_lane_data=require_directional_lane_data,
            )
        ),
        None,
    )


def _path_way_directions(
    patch: OSMPatch,
    core: IntersectionCore,
    terminal_id: str,
    source_way_ids: list[str],
    *,
    require_directional_lane_data: bool = False,
) -> list[tuple[OSMWay, str]]:
    graph = _way_graph(patch, source_way_ids)
    path = _shortest_path_to_core(graph, terminal_id, set(core.core_osm_node_ids))
    if len(path) < 2:
        return []
    tagged = []
    for first, second in zip(path, path[1:], strict=False):
        for way_id in source_way_ids:
            way = patch.ways[way_id]
            if require_directional_lane_data and not _has_directional_lane_data(way.tags):
                continue
            refs = way.node_refs
            if first not in refs or second not in refs:
                continue
            first_index = refs.index(first)
            second_index = refs.index(second)
            if abs(first_index - second_index) == 1:
                tagged.append((way, "forward" if second_index > first_index else "backward"))
                break
    return tagged


def _vehicle_flow_direction(
    patch: OSMPatch,
    core: IntersectionCore,
    terminal_id: str,
    source_way_ids: list[str],
    fallback_way: OSMWay,
) -> tuple[bool, bool, list[str]]:
    path_directions = _path_way_directions(patch, core, terminal_id, source_way_ids)
    if path_directions:
        has_incoming = True
        has_outgoing = True
        evidence = []
        for way, terminal_to_core_direction in path_directions:
            allowed_direction = _oneway_direction(way.tags)
            if allowed_direction == "bidirectional":
                continue
            if allowed_direction == "unknown":
                has_outgoing = False
                evidence.append(_unknown_oneway_evidence(way.tags))
                continue
            if terminal_to_core_direction == allowed_direction:
                has_outgoing = False
                evidence.append(f"oneway:{terminal_to_core_direction}_toward_core")
            else:
                has_incoming = False
                evidence.append(f"oneway:{terminal_to_core_direction}_away_from_core")
        return has_incoming, has_outgoing, evidence

    way = fallback_way
    allowed_direction = _oneway_direction(way.tags)
    if allowed_direction == "bidirectional":
        return True, True, []
    if allowed_direction == "unknown":
        return True, False, [_unknown_oneway_evidence(way.tags)]
    return True, False, ["oneway:assumed_toward_core"]


def _is_oneway(tags: dict[str, str]) -> bool:
    return _oneway_direction(tags) != "bidirectional"


def _oneway_direction(tags: dict[str, str]) -> str:
    raw = tags.get("oneway", "").strip().lower()
    if raw in {"yes", "true", "1"}:
        return "forward"
    if raw == "-1":
        return "backward"
    if raw in {"", "no", "false", "0"}:
        return "bidirectional"
    return "unknown"


def _unknown_oneway_evidence(tags: dict[str, str]) -> str:
    raw = tags.get("oneway", "").strip().lower()
    marker = "".join(character if character.isalnum() else "_" for character in raw).strip("_")
    if not marker:
        return "oneway:assumed_toward_core"
    return f"oneway:unknown_{marker}_assumed_toward_core"


def _has_directional_lane_data(tags: dict[str, str]) -> bool:
    return any(key.startswith("lanes:") or key.startswith("turn:lanes:") or key == "turn:lanes" for key in tags)


def _direction_lane_count(tags: dict[str, str], direction: str) -> int | None:
    return _int_or_none(tags.get(f"lanes:{direction}"))


def _turn_lane_count(tags: dict[str, str], direction: str) -> int | None:
    raw = tags.get(f"turn:lanes:{direction}") or tags.get("turn:lanes")
    return len(raw.split("|")) if raw else None


def _opposite_direction(direction: str) -> str:
    return "backward" if direction == "forward" else "forward"


def _int_or_none(value: str | None) -> int | None:
    if not value:
        return None
    try:
        return max(1, int(value))
    except ValueError:
        return None


def _crossing_support_path_rows(
    patch: OSMPatch,
    highway_ways: list[OSMWay],
    core: IntersectionCore,
    adjacent_node_ids: set[str],
    used_way_ids: set[str],
) -> list[ApproachRow]:
    rows = []
    for way in highway_ways:
        if way.id in used_way_ids or "passenger" in _allowed_modes(way.tags):
            continue
        anchors = [
            node_id
            for node_id in way.node_refs
            if node_id in adjacent_node_ids and _is_crossing_terminal(patch.nodes[node_id].tags)
        ]
        if not anchors:
            continue
        anchor_id = min(anchors, key=lambda node_id: _distance_to_center(patch, node_id, core.center_xy))
        for terminal_id in _support_terminal_ids(patch, way, anchor_id, core.center_xy):
            if terminal_id == anchor_id:
                continue
            endpoint_xy = _node_xy(patch, terminal_id)
            bearing = bearing_between_xy(core.center_xy, endpoint_xy)
            rows.append(
                (
                    bearing,
                    anchor_id,
                    way,
                    terminal_id,
                    [way.id],
                    way.id,
                    endpoint_xy,
                    _support_shape_xy(patch, core, terminal_id, anchor_id, way.id),
                    [],
                    [],
                )
            )
    return rows


def _fuse_support_path_rows(
    rows: list[ApproachRow],
    support_rows: list[ApproachRow],
) -> list[ApproachRow]:
    fused = list(rows)
    for support in support_rows:
        support_modes = _allowed_modes(support[2].tags)
        match_index = _support_lane_match_index(fused, support)
        if match_index is None:
            fused.append(support)
            continue
        row = fused[match_index]
        fused[match_index] = (*row[:8], [_merged_support_modes(row[8], support_modes)], row[9])
    return fused


def _merged_support_modes(extra_modes: list[set[str]], support_modes: set[str]) -> set[str]:
    merged = set(support_modes)
    for modes in extra_modes:
        merged.update(modes)
    return merged


def _vehicle_support_lane_modes(tags: dict[str, str]) -> list[set[str]]:
    if "passenger" not in _allowed_modes(tags):
        return []
    modes = set()
    if _has_positive_tag(tags, "sidewalk"):
        modes.add("pedestrian")
    if any(key.startswith("cycleway") and value not in {"", "no", "none"} for key, value in tags.items()):
        modes.add("bicycle")
    return [modes] if modes else []


def _has_positive_tag(tags: dict[str, str], prefix: str) -> bool:
    return any(key == prefix or key.startswith(f"{prefix}:") for key, value in tags.items() if value not in {"", "no", "none"})


def _support_lane_match_index(
    rows: list[ApproachRow],
    support: ApproachRow,
) -> int | None:
    return next(
        (
            index
            for index, row in enumerate(rows)
            if row[1] == support[1] and "passenger" in _allowed_modes(row[2].tags)
        ),
        None,
    )


def _support_terminal_ids(
    patch: OSMPatch,
    way: OSMWay,
    anchor_id: str,
    center_xy: tuple[float, float],
) -> list[str]:
    endpoints = [way.node_refs[0], way.node_refs[-1]]
    if anchor_id in endpoints:
        return [_far_endpoint_id(patch, way, anchor_id, center_xy)]
    return list(dict.fromkeys(endpoints))


def _support_shape_xy(
    patch: OSMPatch,
    core: IntersectionCore,
    terminal_id: str,
    anchor_id: str,
    way_id: str,
) -> list[tuple[float, float]]:
    path = _shortest_path_to_core(_way_graph(patch, [way_id]), terminal_id, {anchor_id})
    if not path:
        return [_node_xy(patch, terminal_id), core.center_xy]
    points = [_node_xy(patch, node_id) for node_id in path]
    points.append(core.center_xy)
    return _dedupe_adjacent_points(points)


def _way_graph(patch: OSMPatch, way_ids: list[str]) -> dict[str, list[str]]:
    graph: dict[str, list[str]] = defaultdict(list)
    for way_id in way_ids:
        way = patch.ways.get(way_id)
        if way is None:
            continue
        refs = [node_id for node_id in way.node_refs if node_id in patch.nodes]
        for first, second in zip(refs, refs[1:], strict=False):
            graph[first].append(second)
            graph[second].append(first)
    return graph


def _is_crossing_terminal(tags: dict[str, str]) -> bool:
    return tags.get("highway") in {"crossing", "traffic_signals"} or "crossing" in tags


def _shortest_path_to_core(graph: dict[str, list[str]], terminal_id: str, core_ids: set[str]) -> list[str]:
    queue = deque([(terminal_id, [terminal_id])])
    seen = {terminal_id}
    while queue:
        node_id, path = queue.popleft()
        if node_id in core_ids:
            return path
        for neighbor_id in graph.get(node_id, []):
            if neighbor_id in seen:
                continue
            seen.add(neighbor_id)
            queue.append((neighbor_id, [*path, neighbor_id]))
    return []


def _node_xy(patch: OSMPatch, node_id: str) -> tuple[float, float]:
    node = patch.nodes[node_id]
    return (node.x or 0.0, node.y or 0.0)


def _dedupe_adjacent_points(points: list[tuple[float, float]]) -> list[tuple[float, float]]:
    deduped = []
    for point in points:
        if point != (deduped[-1] if deduped else None):
            deduped.append(point)
    return deduped


def _extended_vehicle_corridor_endpoint(
    patch: OSMPatch,
    highway_ways: list[OSMWay],
    core: IntersectionCore,
    neighbor_id: str,
    way: OSMWay,
) -> tuple[str, list[str], str, list[str]]:
    source_way_ids = [way.id]
    corridor_extension_way_ids: list[str] = []
    if "passenger" not in _allowed_modes(way.tags):
        return neighbor_id, source_way_ids, way.id, corridor_extension_way_ids

    terminal_id = neighbor_id
    edge_way_id = way.id
    current_way = way
    current_heading = _outward_heading_at_terminal(patch, way, terminal_id, core.center_xy)
    cumulative_distance_m = 0.0
    seen_way_ids = {way.id}
    visited_node_ids = set(_initial_corridor_path_node_ids(way, terminal_id, set(core.core_osm_node_ids)))

    while True:
        continuations = [
            candidate
            for candidate in highway_ways
            if candidate.id not in seen_way_ids
            and terminal_id in candidate.node_refs
            and candidate.tags.get("highway") not in {"service", "parking_aisle"}
            and "passenger" in _allowed_modes(candidate.tags)
            and _same_vehicle_corridor(current_way, candidate)
        ]
        if len(continuations) != 1:
            break

        continuation = continuations[0]
        next_terminal_id = _far_endpoint_id(patch, continuation, terminal_id, core.center_xy)
        if next_terminal_id == terminal_id:
            break

        candidate_subpath = _way_subpath_between(continuation, terminal_id, next_terminal_id)
        if len(candidate_subpath) < 2:
            break

        if any(node_id in visited_node_ids for node_id in candidate_subpath[1:]):
            break

        segment_length_m = _path_length_m(patch, candidate_subpath)
        if cumulative_distance_m + segment_length_m > MAX_CORRIDOR_EXTENSION_M:
            break

        first_segment_heading = bearing_between_xy(_node_xy(patch, candidate_subpath[0]), _node_xy(patch, candidate_subpath[1]))
        if angle_difference(current_heading, first_segment_heading) > MAX_CORRIDOR_HEADING_DELTA_DEG:
            break

        source_way_ids.append(continuation.id)
        corridor_extension_way_ids.append(continuation.id)
        seen_way_ids.add(continuation.id)
        visited_node_ids.update(candidate_subpath)
        terminal_id = next_terminal_id
        edge_way_id = continuation.id
        current_way = continuation
        current_heading = bearing_between_xy(_node_xy(patch, candidate_subpath[-2]), _node_xy(patch, candidate_subpath[-1]))
        cumulative_distance_m += segment_length_m

    return terminal_id, source_way_ids, edge_way_id, corridor_extension_way_ids


def _initial_corridor_path_node_ids(
    way: OSMWay,
    terminal_id: str,
    core_node_ids: set[str],
) -> list[str]:
    refs = way.node_refs
    try:
        terminal_index = refs.index(terminal_id)
    except ValueError:
        return [terminal_id]

    core_indices = [index for index, node_id in enumerate(refs) if node_id in core_node_ids]
    if not core_indices:
        return [terminal_id]

    core_index = min(core_indices, key=lambda index: abs(index - terminal_index))
    start_index, end_index = sorted((core_index, terminal_index))
    return refs[start_index : end_index + 1]


def _way_subpath_between(way: OSMWay, start_node_id: str, end_node_id: str) -> list[str]:
    refs = way.node_refs
    try:
        start_index = refs.index(start_node_id)
        end_index = refs.index(end_node_id)
    except ValueError:
        return []

    if start_index <= end_index:
        return refs[start_index : end_index + 1]
    return list(reversed(refs[end_index : start_index + 1]))


def _path_length_m(patch: OSMPatch, node_ids: list[str]) -> float:
    return sum(
        euclidean_distance(_node_xy(patch, first), _node_xy(patch, second))
        for first, second in zip(node_ids, node_ids[1:], strict=False)
    )


def _outward_heading_at_terminal(
    patch: OSMPatch,
    way: OSMWay,
    terminal_id: str,
    center_xy: tuple[float, float],
) -> float:
    refs = way.node_refs
    try:
        terminal_index = refs.index(terminal_id)
    except ValueError:
        return bearing_between_xy(center_xy, _node_xy(patch, terminal_id))

    adjacent_node_ids = [
        refs[index]
        for index in (terminal_index - 1, terminal_index + 1)
        if 0 <= index < len(refs) and refs[index] in patch.nodes
    ]
    if not adjacent_node_ids:
        return bearing_between_xy(center_xy, _node_xy(patch, terminal_id))

    from_node_id = min(adjacent_node_ids, key=lambda node_id: _distance_to_center(patch, node_id, center_xy))
    return bearing_between_xy(_node_xy(patch, from_node_id), _node_xy(patch, terminal_id))


def _same_vehicle_corridor(first: OSMWay, second: OSMWay) -> bool:
    if first.tags.get("highway") != second.tags.get("highway"):
        return False
    if first.tags.get("ref") and first.tags.get("ref") == second.tags.get("ref"):
        return True
    return bool(first.tags.get("name") and first.tags.get("name") == second.tags.get("name"))


def _far_endpoint_id(patch: OSMPatch, way: OSMWay, from_node_id: str, center_xy: tuple[float, float]) -> str:
    endpoints = [way.node_refs[0], way.node_refs[-1]]
    if from_node_id in endpoints and len(set(endpoints)) == 2:
        return endpoints[1] if endpoints[0] == from_node_id else endpoints[0]
    return max(endpoints, key=lambda node_id: _distance_to_center(patch, node_id, center_xy))


def _distance_to_center(patch: OSMPatch, node_id: str, center_xy: tuple[float, float]) -> float:
    node = patch.nodes[node_id]
    return ((node.x or 0.0) - center_xy[0]) ** 2 + ((node.y or 0.0) - center_xy[1]) ** 2


def _roles_for_bearings(bearings: list[float]) -> list[str]:
    if len(bearings) != 4:
        return [f"leg_{index}" for index in range(1, len(bearings) + 1)]
    return [_cardinal_role(bearing) for bearing in bearings]


def _cardinal_role(bearing: float) -> str:
    roles = ["north", "east", "south", "west"]
    return roles[int((bearing + 45) // 90) % 4]


def _lane_count(tags: dict[str, str]) -> int:
    try:
        return max(1, int(tags.get("lanes", "1")))
    except ValueError:
        return 1


def _allowed_modes(tags: dict[str, str]) -> set[str]:
    highway = tags.get("highway", "road")
    if highway == "cycleway":
        modes = {"bicycle"}
        if tags.get("foot") in {"yes", "designated", "permissive"}:
            modes.add("pedestrian")
        return filtered_osm_modes(tags, modes)
    if highway in {"footway", "pedestrian", "steps", "crossing"}:
        modes = {"pedestrian"}
        if tags.get("bicycle") in {"yes", "designated", "permissive"}:
            modes.add("bicycle")
        return filtered_osm_modes(tags, modes)
    if highway == "path":
        modes = set()
        if tags.get("foot") != "no":
            modes.add("pedestrian")
        if tags.get("bicycle") != "no":
            modes.add("bicycle")
        return filtered_osm_modes(tags, modes or {"pedestrian"})
    return filtered_osm_modes(tags, {"passenger"})
