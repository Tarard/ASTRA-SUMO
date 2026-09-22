from __future__ import annotations

from collections import Counter, defaultdict
import math

from .schema import IntersectionCore, OSMWay, OSMPatch, PatchSeed


CORE_RADIUS_M = 20.0


def infer_intersection_core(patch: OSMPatch, seed: PatchSeed | None = None) -> IntersectionCore:
    highway_ways = [way for way in patch.ways.values() if "highway" in way.tags]
    incident = Counter(ref for way in highway_ways for ref in way.node_refs)
    center_id = seed.osm_node_id if seed and seed.osm_node_id else incident.most_common(1)[0][0]
    center = patch.nodes[center_id]
    core_node_ids = _expanded_core_node_ids(patch, highway_ways, center_id)
    adjacent = _adjacent_highway_nodes(highway_ways, core_node_ids)
    topology_type = "T3" if len(adjacent) == 3 else ("X4" if len(adjacent) == 4 else ("complex" if len(adjacent) > 4 else "unknown"))
    core_way_ids = sorted(
        way.id for way in highway_ways if any(node_id in core_node_ids for node_id in way.node_refs)
    )
    return IntersectionCore(
        core_id=f"core_{center_id}",
        center_xy=(center.x or 0.0, center.y or 0.0),
        center_latlon=(center.lat, center.lon),
        core_osm_node_ids=core_node_ids,
        core_way_ids=core_way_ids,
        core_radius_m=CORE_RADIUS_M,
        topology_type=topology_type,
        internal_fragment_count=max(0, len(adjacent) - 4),
        short_internal_edge_count=0,
        confidence=0.9 if topology_type in {"T3", "X4"} else 0.4,
    )


def _expanded_core_node_ids(patch: OSMPatch, highway_ways: list[OSMWay], center_id: str) -> list[str]:
    center = patch.nodes[center_id]
    incident: dict[str, list[OSMWay]] = defaultdict(list)
    for way in highway_ways:
        for node_id in way.node_refs:
            incident[node_id].append(way)

    core_ids = {center_id}
    for node_id, ways in incident.items():
        node = patch.nodes[node_id]
        if node.x is None or node.y is None:
            continue
        distance = math.hypot(node.x - (center.x or 0.0), node.y - (center.y or 0.0))
        if distance <= CORE_RADIUS_M and _is_core_candidate(node.tags, ways):
            core_ids.add(node_id)
    return [center_id, *sorted(core_ids - {center_id})]


def _is_core_candidate(tags: dict[str, str], ways: list[OSMWay]) -> bool:
    if len({way.id for way in ways}) > 1:
        return True
    return tags.get("highway") in {"crossing", "traffic_signals"} or tags.get("crossing") == "traffic_signals"


def _adjacent_highway_nodes(highway_ways, center_ids: str | list[str]) -> dict[str, list[str]]:
    core_ids = {center_ids} if isinstance(center_ids, str) else set(center_ids)
    adjacent: dict[str, list[str]] = defaultdict(list)
    for way in highway_ways:
        refs = way.node_refs
        for index, ref in enumerate(refs):
            if ref not in core_ids:
                continue
            for neighbor_index in (index - 1, index + 1):
                if 0 <= neighbor_index < len(refs) and refs[neighbor_index] not in core_ids:
                    adjacent[refs[neighbor_index]].append(way.id)
    return dict(adjacent)
