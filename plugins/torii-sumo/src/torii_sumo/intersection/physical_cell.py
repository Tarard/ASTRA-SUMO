from __future__ import annotations

from collections import defaultdict
import hashlib
import heapq
import json
import math
from types import SimpleNamespace
from typing import Any

from torii_sumo.road_semantics import filtered_osm_modes, is_osm_passenger_way

from .schema import OSMPatch


_SUPPORT_HIGHWAYS = {
    "bridleway",
    "corridor",
    "cycleway",
    "footway",
    "path",
    "pedestrian",
    "steps",
}


def infer_signal_anchor_physical_cell(
    patch: OSMPatch,
    *,
    seed_node_id: str,
) -> dict[str, Any]:
    """Propose one physical signal cell without using a reviewed join scope.

    Signal and signalized-crossing nodes are anchors. Shortest paths through
    passenger-drivable OSM ways form the local cell envelope. Pure degree-two
    shape nodes remain geometry evidence, while signal, branching, and
    multimodal attachment nodes become proposed SUMO-junction members.
    """

    if seed_node_id not in patch.nodes:
        raise ValueError(f"OSM seed node is absent from the patch: {seed_node_id}")

    graph, vehicle_way_ids, max_lane_count = build_osm_vehicle_graph(patch)
    radius_m = max(30.0, min(50.0, max_lane_count * 3.2 * 4.0))
    distances, predecessors = shortest_paths(graph, seed_node_id)
    seed = patch.nodes[seed_node_id]
    anchor_ids = []
    excluded_anchor_ids = []
    for node in patch.nodes.values():
        if not _is_signal_anchor(node.tags):
            continue
        euclidean_m = _distance_xy(seed, node)
        graph_distance_m = distances.get(node.id)
        if euclidean_m <= radius_m and graph_distance_m is not None and graph_distance_m <= radius_m * 1.5:
            anchor_ids.append(node.id)
        elif euclidean_m <= radius_m:
            excluded_anchor_ids.append(node.id)

    if _is_signal_anchor(seed.tags) and seed_node_id not in anchor_ids:
        anchor_ids.append(seed_node_id)
    anchor_ids.sort()

    path_node_ids = {seed_node_id}
    anchor_paths: list[dict[str, Any]] = []
    for anchor_id in anchor_ids:
        path = _reconstruct_path(predecessors, seed_node_id, anchor_id)
        if not path:
            continue
        path_node_ids.update(path)
        anchor_paths.append(
            {
                "anchor_node_id": anchor_id,
                "graph_distance_m": round(distances.get(anchor_id, 0.0), 3),
                "path_node_ids": path,
            }
        )

    path_node_ids = _complete_bounded_way_shapes(
        patch,
        path_node_ids=path_node_ids,
        vehicle_way_ids=vehicle_way_ids,
        seed_node_id=seed_node_id,
        maximum_distance_m=radius_m * 1.5,
    )

    vehicle_incident_way_ids = _incident_way_ids(patch, vehicle_way_ids)
    support_incident_way_ids = _support_incident_way_ids(patch)
    proposed_junction_ids = sorted(
        node_id
        for node_id in path_node_ids
        if _is_semantic_junction(
            patch,
            node_id=node_id,
            seed_node_id=seed_node_id,
            vehicle_incident_way_ids=vehicle_incident_way_ids,
            support_incident_way_ids=support_incident_way_ids,
        )
    )
    shape_node_ids = sorted(path_node_ids - set(proposed_junction_ids))
    boundary_ports = _boundary_ports(
        patch,
        graph=graph,
        path_node_ids=path_node_ids,
        seed_node_id=seed_node_id,
    )
    physical_approaches = _group_boundary_ports(boundary_ports)

    risks = []
    notes = []
    if not anchor_ids:
        risks.append("no_signal_anchor_in_adaptive_radius")
    if excluded_anchor_ids:
        risks.append("nearby_signal_anchor_not_vehicle_graph_connected")
    if len(physical_approaches) not in {3, 4}:
        risks.append("physical_approach_count_outside_standard_t3_x4_domain")
    if any(item["grouping_status"] != "pass" for item in physical_approaches):
        risks.append("physical_approach_grouping_unresolved")
    if shape_node_ids:
        notes.append("bounded_geometry_shape_nodes_excluded_from_junction_membership")
    if len(boundary_ports) > len(physical_approaches):
        notes.append("complementary_oneway_boundary_ports_grouped")

    payload = {
        "schema": "torii.signal-anchor-physical-cell/v1",
        "method": "signal_anchor_vehicle_shortest_path_closure",
        "generation_status": "pass",
        "disposition": "suggest" if not risks else "review",
        "automatic_promotion_gate": "blocked",
        "seed_node_id": seed_node_id,
        "adaptive_radius_m": round(radius_m, 3),
        "maximum_local_lane_count": max_lane_count,
        "signal_anchor_node_ids": anchor_ids,
        "excluded_nearby_anchor_node_ids": sorted(excluded_anchor_ids),
        "anchor_paths": anchor_paths,
        "path_closure_node_ids": sorted(path_node_ids),
        "proposed_source_junction_ids": proposed_junction_ids,
        "geometry_shape_node_ids": shape_node_ids,
        "raw_boundary_ports": boundary_ports,
        "physical_approaches": physical_approaches,
        "risks": risks,
        "notes": notes,
        "assumptions": [
            "OSM signal and signalized-crossing tags are anchor evidence, not controller truth",
            "passenger-drivable way connectivity defines path closure",
            "untagged single-way degree-two nodes are geometry shape evidence",
            "multimodal attachments inside the closure are protected junction evidence",
            "complementary one-way ports may form one physical approach only when road identity and direction agree",
        ],
        "claim_boundary": (
            "This is an OSM-only physical-cell hypothesis. It does not authorize "
            "a junction join, connection rewrite, or TLS program change."
        ),
    }
    return {
        **payload,
        "hypothesis_id": f"cell-{_stable_digest(payload)[:20]}",
    }


def infer_roundabout_boundary_approaches(
    patch: OSMPatch,
    *,
    roundabout_way_ids: list[str],
) -> dict[str, Any]:
    """Extract semantic entry/exit arms around an explicitly tagged ring.

    A seed node on a roundabout only exposes its two immediate ring neighbors,
    so node degree cannot recover the roundabout's entry count.  This helper
    instead treats the tagged ring as a finite cell boundary and extracts
    every passenger-drivable non-ring way that crosses it.  Directional ways
    are grouped with the same evidence rules used by the generic physical-cell
    boundary logic.
    """

    selected_way_ids = sorted(way_id for way_id in map(str, roundabout_way_ids) if way_id in patch.ways)
    ring_node_ids = sorted(
        {
            str(node_id)
            for way_id in selected_way_ids
            for node_id in patch.ways[way_id].node_refs
            if node_id in patch.nodes
        }
    )
    if not ring_node_ids:
        payload = {
            "schema": "torii.roundabout-boundary-approaches/v1",
            "generation_status": "blocked",
            "disposition": "review",
            "roundabout_way_ids": selected_way_ids,
            "ring_node_ids": [],
            "ring_validation": {
                "status": "blocked",
                "connected": False,
                "closed": False,
                "ring_node_count": 0,
                "degree_by_node": {},
                "missing_node_ids": [],
                "risks": ["roundabout_ring_has_no_resolvable_nodes"],
            },
            "raw_boundary_ports": [],
            "physical_approaches": [],
            "risks": ["roundabout_ring_has_no_resolvable_nodes"],
        }
        return {
            **payload,
            "hypothesis_id": f"roundabout-boundary-{_stable_digest(payload)[:20]}",
        }

    center = SimpleNamespace(
        x=sum((patch.nodes[node_id].x or 0.0) for node_id in ring_node_ids) / len(ring_node_ids),
        y=sum((patch.nodes[node_id].y or 0.0) for node_id in ring_node_ids) / len(ring_node_ids),
    )
    ring_nodes = set(ring_node_ids)
    ring_ways = set(selected_way_ids)
    ports: dict[tuple[str, str, str], dict[str, Any]] = {}
    for way in patch.ways.values():
        if way.id in ring_ways or not is_osm_passenger_way(way.tags):
            continue
        for first_id, second_id in zip(way.node_refs, way.node_refs[1:], strict=False):
            if first_id in ring_nodes and second_id not in ring_nodes:
                inside_id, outside_id = first_id, second_id
            elif second_id in ring_nodes and first_id not in ring_nodes:
                inside_id, outside_id = second_id, first_id
            else:
                continue
            if outside_id not in patch.nodes:
                continue
            lane_semantics = _boundary_lane_semantics(
                way.tags,
                way_node_ids=way.node_refs,
                inside_node_id=inside_id,
                outside_node_id=outside_id,
            )
            identity_payload = {
                "inside_node_id": inside_id,
                "outside_node_id": outside_id,
                "way_id": way.id,
                "flow_role": lane_semantics["flow_role"],
                "boundary_kind": "roundabout_gate",
            }
            port = {
                **identity_payload,
                "road_name": way.tags.get("name"),
                "road_ref": way.tags.get("ref"),
                "highway_class": way.tags.get("highway"),
                "road_identity": _road_identity(way.tags, way.id),
                "oneway": way.tags.get("oneway", "no"),
                "incoming_lane_count": lane_semantics["incoming_lane_count"],
                "outgoing_lane_count": lane_semantics["outgoing_lane_count"],
                "incoming_osm_direction": lane_semantics["incoming_osm_direction"],
                "incoming_turn_lanes_raw": lane_semantics["incoming_turn_lanes_raw"],
                "bearing_from_seed_deg": round(
                    _bearing_from_to(center, patch.nodes[outside_id]),
                    3,
                ),
                "allowed_modes": sorted(filtered_osm_modes(way.tags, {"passenger"})),
                "identity_basis": identity_payload,
            }
            port["boundary_port_id"] = f"roundabout-port-{_stable_digest(identity_payload)[:16]}"
            port["evidence_signature"] = _stable_digest(port)
            ports[(inside_id, outside_id, way.id)] = port

    raw_boundary_ports = [ports[key] for key in sorted(ports)]
    approaches = _group_boundary_ports(raw_boundary_ports)
    ring_validation = _validate_roundabout_ring_component(
        patch,
        roundabout_way_ids=selected_way_ids,
    )
    risks = []
    if ring_validation["status"] != "pass":
        risks.extend(ring_validation["risks"])
    if not approaches:
        risks.append("roundabout_has_no_passenger_boundary_approaches")
    if any(item["grouping_status"] != "pass" for item in approaches):
        risks.append("roundabout_boundary_approach_grouping_unresolved")
    payload = {
        "schema": "torii.roundabout-boundary-approaches/v1",
        "generation_status": "pass",
        "disposition": "suggest" if approaches and not risks else "review",
        "roundabout_way_ids": selected_way_ids,
        "ring_node_ids": ring_node_ids,
        "ring_validation": ring_validation,
        "raw_boundary_ports": raw_boundary_ports,
        "physical_approaches": approaches,
        "risks": risks,
        "claim_boundary": (
            "These are OSM-derived semantic gates around an explicitly tagged "
            "ring. They do not prove circulating priority, legal lane movements, "
            "or authorize ring reconstruction."
        ),
    }
    return {
        **payload,
        "hypothesis_id": f"roundabout-boundary-{_stable_digest(payload)[:20]}",
    }


def _validate_roundabout_ring_component(
    patch: OSMPatch,
    *,
    roundabout_way_ids: list[str],
) -> dict[str, Any]:
    adjacency: dict[str, set[str]] = defaultdict(set)
    missing_node_ids: set[str] = set()
    for way_id in roundabout_way_ids:
        way = patch.ways[way_id]
        for first_id, second_id in zip(way.node_refs, way.node_refs[1:], strict=False):
            if first_id not in patch.nodes:
                missing_node_ids.add(first_id)
            if second_id not in patch.nodes:
                missing_node_ids.add(second_id)
            if first_id not in patch.nodes or second_id not in patch.nodes:
                continue
            adjacency[first_id].add(second_id)
            adjacency[second_id].add(first_id)

    visited: set[str] = set()
    if adjacency:
        queue = [next(iter(adjacency))]
        while queue:
            node_id = queue.pop()
            if node_id in visited:
                continue
            visited.add(node_id)
            queue.extend(sorted(adjacency[node_id] - visited))
    degree_by_node = {node_id: len(neighbors) for node_id, neighbors in sorted(adjacency.items())}
    connected = bool(adjacency) and len(visited) == len(adjacency)
    closed = (
        connected
        and len(adjacency) >= 3
        and not missing_node_ids
        and all(degree == 2 for degree in degree_by_node.values())
    )
    risks = []
    if missing_node_ids:
        risks.append("roundabout_ring_has_missing_node_references")
    if adjacency and not connected:
        risks.append("roundabout_ring_component_disconnected")
    if not closed:
        risks.append("roundabout_ring_not_closed")
    return {
        "status": "pass" if closed else "review_required",
        "connected": connected,
        "closed": closed,
        "ring_node_count": len(adjacency),
        "degree_by_node": degree_by_node,
        "missing_node_ids": sorted(missing_node_ids),
        "risks": sorted(set(risks)),
        "claim_boundary": (
            "Graph closure validates completeness of the tagged ring component. "
            "It does not validate circulating priority or lane movements."
        ),
    }


def build_osm_vehicle_graph(
    patch: OSMPatch,
) -> tuple[dict[str, list[tuple[str, float, str]]], set[str], int]:
    graph: dict[str, list[tuple[str, float, str]]] = defaultdict(list)
    vehicle_way_ids: set[str] = set()
    maximum_lane_count = 1
    for way in patch.ways.values():
        if not is_osm_passenger_way(way.tags):
            continue
        vehicle_way_ids.add(way.id)
        maximum_lane_count = max(maximum_lane_count, _lane_count(way.tags))
        for first_id, second_id in zip(
            way.node_refs,
            way.node_refs[1:],
            strict=False,
        ):
            if first_id not in patch.nodes or second_id not in patch.nodes:
                continue
            distance_m = _distance_xy(
                patch.nodes[first_id],
                patch.nodes[second_id],
            )
            graph[first_id].append((second_id, distance_m, way.id))
            graph[second_id].append((first_id, distance_m, way.id))
    return dict(graph), vehicle_way_ids, maximum_lane_count


def _lane_count(tags: dict[str, str]) -> int:
    try:
        return max(1, int(tags.get("lanes", "1")))
    except ValueError:
        return 1


def shortest_paths(
    graph: dict[str, list[tuple[str, float, str]]],
    source_id: str,
) -> tuple[dict[str, float], dict[str, str]]:
    distances = {source_id: 0.0}
    predecessors: dict[str, str] = {}
    queue = [(0.0, source_id)]
    while queue:
        distance_m, node_id = heapq.heappop(queue)
        if distance_m != distances.get(node_id):
            continue
        for neighbor_id, segment_length_m, _ in graph.get(node_id, ()):
            candidate_distance = distance_m + segment_length_m
            if candidate_distance >= distances.get(neighbor_id, float("inf")):
                continue
            distances[neighbor_id] = candidate_distance
            predecessors[neighbor_id] = node_id
            heapq.heappush(queue, (candidate_distance, neighbor_id))
    return distances, predecessors


def _reconstruct_path(
    predecessors: dict[str, str],
    source_id: str,
    target_id: str,
) -> list[str]:
    if target_id == source_id:
        return [source_id]
    if target_id not in predecessors:
        return []
    path = [target_id]
    seen = {target_id}
    while path[-1] != source_id:
        previous = predecessors.get(path[-1])
        if previous is None or previous in seen:
            return []
        path.append(previous)
        seen.add(previous)
    return list(reversed(path))


def _complete_bounded_way_shapes(
    patch: OSMPatch,
    *,
    path_node_ids: set[str],
    vehicle_way_ids: set[str],
    seed_node_id: str,
    maximum_distance_m: float,
) -> set[str]:
    """Include shape vertices bounded by two already-inside nodes on one way."""

    completed = set(path_node_ids)
    original = set(path_node_ids)
    seed = patch.nodes[seed_node_id]
    for way_id in sorted(vehicle_way_ids):
        refs = patch.ways[way_id].node_refs
        inside_indices = [index for index, node_id in enumerate(refs) if node_id in original]
        if len(inside_indices) < 2:
            continue
        start, end = min(inside_indices), max(inside_indices)
        completed.update(
            node_id
            for node_id in refs[start : end + 1]
            if node_id in patch.nodes and _distance_xy(seed, patch.nodes[node_id]) <= maximum_distance_m
        )
    return completed


def _incident_way_ids(
    patch: OSMPatch,
    included_way_ids: set[str],
) -> dict[str, set[str]]:
    incident: dict[str, set[str]] = defaultdict(set)
    for way_id in included_way_ids:
        for node_id in patch.ways[way_id].node_refs:
            incident[node_id].add(way_id)
    return dict(incident)


def _support_incident_way_ids(patch: OSMPatch) -> dict[str, set[str]]:
    incident: dict[str, set[str]] = defaultdict(set)
    for way in patch.ways.values():
        if way.tags.get("highway") not in _SUPPORT_HIGHWAYS:
            continue
        for node_id in way.node_refs:
            incident[node_id].add(way.id)
    return dict(incident)


def _is_semantic_junction(
    patch: OSMPatch,
    *,
    node_id: str,
    seed_node_id: str,
    vehicle_incident_way_ids: dict[str, set[str]],
    support_incident_way_ids: dict[str, set[str]],
) -> bool:
    node = patch.nodes[node_id]
    return bool(
        node_id == seed_node_id
        or _is_signal_anchor(node.tags)
        or len(vehicle_incident_way_ids.get(node_id, ())) > 1
        or (vehicle_incident_way_ids.get(node_id) and support_incident_way_ids.get(node_id))
        or node.tags.get("junction")
    )


def _boundary_ports(
    patch: OSMPatch,
    *,
    graph: dict[str, list[tuple[str, float, str]]],
    path_node_ids: set[str],
    seed_node_id: str,
) -> list[dict[str, Any]]:
    seed = patch.nodes[seed_node_id]
    records: dict[tuple[str, str, str], dict[str, Any]] = {}
    for inside_id in path_node_ids:
        for outside_id, _, way_id in graph.get(inside_id, ()):
            if outside_id in path_node_ids:
                continue
            way = patch.ways[way_id]
            outside = patch.nodes[outside_id]
            lane_semantics = _boundary_lane_semantics(
                way.tags,
                way_node_ids=way.node_refs,
                inside_node_id=inside_id,
                outside_node_id=outside_id,
            )
            payload = {
                "inside_node_id": inside_id,
                "outside_node_id": outside_id,
                "way_id": way_id,
                "road_name": way.tags.get("name"),
                "road_ref": way.tags.get("ref"),
                "highway_class": way.tags.get("highway"),
                "road_identity": _road_identity(way.tags, way_id),
                "oneway": way.tags.get("oneway", "no"),
                "flow_role": lane_semantics["flow_role"],
                "incoming_lane_count": lane_semantics["incoming_lane_count"],
                "outgoing_lane_count": lane_semantics["outgoing_lane_count"],
                "incoming_osm_direction": lane_semantics["incoming_osm_direction"],
                "incoming_turn_lanes_raw": lane_semantics["incoming_turn_lanes_raw"],
                "bearing_from_seed_deg": round(
                    _bearing_from_to(seed, outside),
                    3,
                ),
                "allowed_modes": sorted(filtered_osm_modes(way.tags, {"passenger"})),
            }
            identity_payload = {
                "inside_node_id": inside_id,
                "outside_node_id": outside_id,
                "way_id": way_id,
                "flow_role": lane_semantics["flow_role"],
            }
            payload["boundary_port_id"] = f"port-{_stable_digest(identity_payload)[:16]}"
            payload["identity_basis"] = identity_payload
            payload["evidence_signature"] = _stable_digest(payload)
            records[(inside_id, outside_id, way_id)] = payload
    return [records[key] for key in sorted(records)]


def _group_boundary_ports(
    ports: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    parents = list(range(len(ports)))

    def find(index: int) -> int:
        while parents[index] != index:
            parents[index] = parents[parents[index]]
            index = parents[index]
        return index

    def union(first: int, second: int) -> None:
        first_root = find(first)
        second_root = find(second)
        if first_root != second_root:
            parents[second_root] = first_root

    for first in range(len(ports)):
        for second in range(first + 1, len(ports)):
            if _ports_form_one_physical_approach(ports[first], ports[second]):
                union(first, second)

    groups: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for index, port in enumerate(ports):
        groups[find(index)].append(port)

    records = []
    for members in groups.values():
        members.sort(key=lambda item: item["boundary_port_id"])
        member_ids = [item["boundary_port_id"] for item in members]
        source_way_ids = sorted({str(item["way_id"]) for item in members})
        flow_roles = sorted({item["flow_role"] for item in members})
        road_identities = sorted({item["road_identity"] for item in members})
        if len(members) == 1 and flow_roles == ["bidirectional"]:
            status = "pass"
            reason = "single_bidirectional_boundary_port"
        elif len(members) == 2 and len(road_identities) == 1 and set(flow_roles) == {"incoming", "outgoing"}:
            status = "pass"
            reason = "exact_complementary_oneway_pair_same_road"
        else:
            status = "review_required"
            reason = "boundary_port_flow_or_identity_ambiguous"
        identity_payload = {
            "member_boundary_port_ids": member_ids,
            "source_way_ids": source_way_ids,
            "road_identities": road_identities,
            "flow_roles": flow_roles,
        }
        incoming_lane_count = sum(int(item["incoming_lane_count"]) for item in members)
        outgoing_lane_count = sum(int(item["outgoing_lane_count"]) for item in members)
        turn_lane_values = sorted(
            {str(item["incoming_turn_lanes_raw"]) for item in members if item.get("incoming_turn_lanes_raw")}
        )
        records.append(
            {
                "physical_approach_id": (f"physical-approach-{_stable_digest(identity_payload)[:16]}"),
                **identity_payload,
                "member_count": len(members),
                "bearing_from_seed_deg": round(
                    _circular_mean([item["bearing_from_seed_deg"] for item in members]),
                    3,
                ),
                "road_names": sorted({str(item["road_name"]) for item in members if item.get("road_name")}),
                "highway_classes": sorted({str(item["highway_class"]) for item in members}),
                "incoming_lane_count": incoming_lane_count,
                "outgoing_lane_count": outgoing_lane_count,
                "incoming_turn_lanes_raw": (turn_lane_values[0] if len(turn_lane_values) == 1 else None),
                "turn_lane_evidence_status": ("pass" if len(turn_lane_values) <= 1 else "review_required"),
                "grouping_status": status,
                "grouping_reason": reason,
            }
        )
    return sorted(records, key=lambda item: item["physical_approach_id"])


def _ports_form_one_physical_approach(
    first: dict[str, Any],
    second: dict[str, Any],
) -> bool:
    return bool(
        first["road_identity"] == second["road_identity"]
        and first["highway_class"] == second["highway_class"]
        and {first["flow_role"], second["flow_role"]} == {"incoming", "outgoing"}
        and _circular_delta(
            first["bearing_from_seed_deg"],
            second["bearing_from_seed_deg"],
        )
        <= 65.0
    )


def _boundary_flow_role(
    way_node_ids: list[str],
    *,
    inside_node_id: str,
    outside_node_id: str,
    oneway: str,
) -> str:
    forward_inside_to_outside = _forward_inside_to_outside(
        way_node_ids,
        inside_node_id=inside_node_id,
        outside_node_id=outside_node_id,
    )
    if oneway not in {"yes", "1", "true", "-1"}:
        return "bidirectional"
    if forward_inside_to_outside is None:
        return "unknown"
    if oneway == "-1":
        forward_inside_to_outside = not forward_inside_to_outside
    return "outgoing" if forward_inside_to_outside else "incoming"


def _boundary_lane_semantics(
    tags: dict[str, str],
    *,
    way_node_ids: list[str],
    inside_node_id: str,
    outside_node_id: str,
) -> dict[str, Any]:
    oneway = tags.get("oneway", "no")
    forward_inside_to_outside = _forward_inside_to_outside(
        way_node_ids,
        inside_node_id=inside_node_id,
        outside_node_id=outside_node_id,
    )
    flow_role = _boundary_flow_role(
        way_node_ids,
        inside_node_id=inside_node_id,
        outside_node_id=outside_node_id,
        oneway=oneway,
    )
    total = _lane_count(tags)
    if flow_role == "incoming":
        incoming_direction = "backward" if oneway == "-1" else "forward"
        return {
            "flow_role": flow_role,
            "incoming_lane_count": total,
            "outgoing_lane_count": 0,
            "incoming_osm_direction": incoming_direction,
            "incoming_turn_lanes_raw": _turn_lanes_for_direction(
                tags,
                incoming_direction,
            ),
        }
    if flow_role == "outgoing":
        return {
            "flow_role": flow_role,
            "incoming_lane_count": 0,
            "outgoing_lane_count": total,
            "incoming_osm_direction": None,
            "incoming_turn_lanes_raw": None,
        }
    if forward_inside_to_outside is None:
        return {
            "flow_role": "unknown",
            "incoming_lane_count": 0,
            "outgoing_lane_count": 0,
            "incoming_osm_direction": None,
            "incoming_turn_lanes_raw": None,
        }

    incoming_direction = "backward" if forward_inside_to_outside else "forward"
    outgoing_direction = "forward" if forward_inside_to_outside else "backward"
    incoming_count = _directional_lane_count(tags, incoming_direction)
    outgoing_count = _directional_lane_count(tags, outgoing_direction)
    if incoming_count is None and outgoing_count is not None:
        incoming_count = max(1, total - outgoing_count)
    if outgoing_count is None and incoming_count is not None:
        outgoing_count = max(1, total - incoming_count)
    if incoming_count is None and outgoing_count is None:
        incoming_count = max(1, total // 2)
        outgoing_count = max(1, total - incoming_count)
    return {
        "flow_role": "bidirectional",
        "incoming_lane_count": incoming_count,
        "outgoing_lane_count": outgoing_count,
        "incoming_osm_direction": incoming_direction,
        "incoming_turn_lanes_raw": _turn_lanes_for_direction(
            tags,
            incoming_direction,
        ),
    }


def _forward_inside_to_outside(
    way_node_ids: list[str],
    *,
    inside_node_id: str,
    outside_node_id: str,
) -> bool | None:
    for first, second in zip(way_node_ids, way_node_ids[1:], strict=False):
        if first == inside_node_id and second == outside_node_id:
            return True
        if first == outside_node_id and second == inside_node_id:
            return False
    return None


def _directional_lane_count(
    tags: dict[str, str],
    direction: str,
) -> int | None:
    raw = tags.get(f"lanes:{direction}")
    if raw is None:
        return None
    try:
        return max(1, int(raw))
    except ValueError:
        return None


def _turn_lanes_for_direction(
    tags: dict[str, str],
    direction: str,
) -> str | None:
    return tags.get(f"turn:lanes:{direction}") or tags.get("turn:lanes")


def _road_identity(tags: dict[str, str], way_id: str) -> str:
    if tags.get("ref"):
        return f"ref:{tags['ref'].strip().casefold()}"
    if tags.get("name"):
        return f"name:{tags['name'].strip().casefold()}"
    return f"way:{way_id}"


def _circular_delta(first: float, second: float) -> float:
    return abs((first - second + 180.0) % 360.0 - 180.0)


def _circular_mean(values: list[float]) -> float:
    x = sum(math.sin(math.radians(value)) for value in values)
    y = sum(math.cos(math.radians(value)) for value in values)
    return (math.degrees(math.atan2(x, y)) + 360.0) % 360.0


def _is_signal_anchor(tags: dict[str, str]) -> bool:
    return bool(tags.get("highway") == "traffic_signals" or tags.get("crossing") == "traffic_signals")


def _distance_xy(first: Any, second: Any) -> float:
    return math.hypot(
        (first.x or 0.0) - (second.x or 0.0),
        (first.y or 0.0) - (second.y or 0.0),
    )


def _bearing_from_to(first: Any, second: Any) -> float:
    return (
        math.degrees(
            math.atan2(
                (second.x or 0.0) - (first.x or 0.0),
                (second.y or 0.0) - (first.y or 0.0),
            )
        )
        + 360.0
    ) % 360.0


def _stable_digest(payload: Any) -> str:
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()
