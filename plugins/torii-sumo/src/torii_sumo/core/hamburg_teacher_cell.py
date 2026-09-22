from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import math
from pathlib import Path
import re
from typing import Sequence
import xml.etree.ElementTree as ET

from torii_sumo.intersection.schema import (
    Approach,
    BBox,
    ControlModel,
    IntersectionCore,
    IntersectionIR,
    Movement,
    MovementMatrix,
    OSMPatch,
    PatchSeed,
    RoadPairRelationGraph,
    TLSPhase,
)

from .digital_twin import MapConnection, MapLane
from .ocit_c import (
    OcitVehicleTopologyInventory,
    OcitVehicleTopologyMovement,
    topology_control_index_by_node,
)
from .tls_aggregation import build_tls_signal_grouping_variant


@dataclass(frozen=True)
class HamburgOfficialMovementPath:
    """One official MAP/OCIT movement resolved to a local SUMO lane path."""

    node_id: str
    connection_id: str
    ingress_lane_id: str
    egress_lane_id: str
    lane_ids: tuple[str, ...]


@dataclass(frozen=True)
class HamburgTeacherLaneIndex:
    direction: str
    official_lane_id: str
    official_approach_id: str
    teacher_approach_id: str
    teacher_edge_id: str
    teacher_lane_index: int
    candidate_lane_id: str
    candidate_edge_id: str


@dataclass(frozen=True)
class HamburgCandidateCellBoundary:
    candidate_junction_id: str
    candidate_junction_ids: tuple[str, ...]
    ingress_boundary_lanes: dict[str, str]
    egress_boundary_lanes: dict[str, str]
    ingress_boundary_edges: tuple[str, ...]
    egress_boundary_edges: tuple[str, ...]
    passenger_lane_indices_by_edge: dict[str, tuple[int, ...]]
    path_errors: tuple[str, ...]


@dataclass(frozen=True)
class HamburgOfficialApproachComponent:
    """One connected component of the official directional-approach graph.

    Vertices are typed ingress/egress MAP approaches and every official OCIT-C
    vehicle movement is one bipartite edge.  ``movement_ids`` are the official
    MAP/OCIT connection ids, not OSM TLS arcs selected along a routed path.
    """

    component_id: str
    movement_ids: tuple[str, ...]
    ingress_approach_ids: tuple[str, ...]
    egress_approach_ids: tuple[str, ...]


@dataclass(frozen=True)
class HamburgTeacherCellContract:
    ir: IntersectionIR
    approach_components: tuple[HamburgOfficialApproachComponent, ...]
    approach_pairs: tuple[dict[str, object], ...]
    lane_indices: tuple[HamburgTeacherLaneIndex, ...]
    candidate_junction_id: str
    candidate_junction_ids: tuple[str, ...]
    expression_index_by_key: dict[str, int]
    expression_compression_budget: int
    topology_status: str
    review_gates: tuple[str, ...]
    exact_signal_replay_gates: tuple[str, ...]


@dataclass
class _BoundaryGroup:
    direction: str
    official_approach_id: str
    candidate_edge_id: str
    official_lane_ids: set[str]
    candidate_lane_by_official_lane: dict[str, str]


def build_hamburg_official_approach_components(
    *,
    node_id: str,
    map_lanes: Sequence[MapLane],
    topology_inventory: OcitVehicleTopologyInventory,
) -> tuple[HamburgOfficialApproachComponent, ...]:
    """Partition official movements into directional-approach cells.

    This is deliberately independent of OSM/SUMO path topology.  It prevents
    one official movement from being counted once for every candidate TLS arc
    it happens to traverse and gives the existing shared-controller replay
    path a stable spatial-cell boundary.
    """

    official_lanes = {
        lane.lane_id: lane
        for lane in map_lanes
        if _same_node(lane.node_id, node_id) and lane.is_vehicle
    }
    movements = sorted(
        (
            movement
            for movement in topology_inventory.movements
            if _same_node(movement.node_id, node_id)
        ),
        key=lambda movement: (
            _natural_key(movement.connection_id),
            _natural_key(movement.ingress_lane_id),
            _natural_key(movement.egress_lane_id),
        ),
    )
    duplicate_movement_ids = sorted(
        (
            movement_id
            for movement_id, count in Counter(
                movement.connection_id for movement in movements
            ).items()
            if count > 1
        ),
        key=_natural_key,
    )
    if duplicate_movement_ids:
        raise ValueError(
            f"official topology contains duplicate movement ids for node {node_id}: "
            f"{duplicate_movement_ids}"
        )

    movement_rows: list[tuple[str, tuple[str, str], tuple[str, str]]] = []
    adjacency: dict[tuple[str, str], set[tuple[str, str]]] = {}
    for movement in movements:
        ingress_lane = official_lanes.get(movement.ingress_lane_id)
        egress_lane = official_lanes.get(movement.egress_lane_id)
        if ingress_lane is None or egress_lane is None:
            missing_lane_ids = [
                lane_id
                for lane_id, lane in (
                    (movement.ingress_lane_id, ingress_lane),
                    (movement.egress_lane_id, egress_lane),
                )
                if lane is None
            ]
            raise ValueError(
                f"official movement {node_id}/{movement.connection_id} references missing MAP lanes: "
                f"{missing_lane_ids}"
            )
        ingress_approach_id = ingress_lane.ingress_approach
        egress_approach_id = egress_lane.egress_approach
        if not ingress_approach_id or not egress_approach_id:
            raise ValueError(
                f"official movement {node_id}/{movement.connection_id} lacks a directional MAP approach"
            )
        ingress_vertex = ("ingress", ingress_approach_id)
        egress_vertex = ("egress", egress_approach_id)
        adjacency.setdefault(ingress_vertex, set()).add(egress_vertex)
        adjacency.setdefault(egress_vertex, set()).add(ingress_vertex)
        movement_rows.append((movement.connection_id, ingress_vertex, egress_vertex))

    raw_components: list[dict[str, tuple[str, ...]]] = []
    unseen = set(adjacency)
    while unseen:
        start = min(unseen, key=_approach_vertex_key)
        stack = [start]
        component_vertices: set[tuple[str, str]] = set()
        while stack:
            vertex = stack.pop()
            if vertex in component_vertices:
                continue
            component_vertices.add(vertex)
            unseen.discard(vertex)
            stack.extend(adjacency.get(vertex, set()) - component_vertices)
        raw_components.append(
            {
                "movement_ids": tuple(
                    sorted(
                        (
                            movement_id
                            for movement_id, ingress_vertex, egress_vertex in movement_rows
                            if ingress_vertex in component_vertices and egress_vertex in component_vertices
                        ),
                        key=_natural_key,
                    )
                ),
                "ingress_approach_ids": tuple(
                    sorted(
                        (
                            approach_id
                            for direction, approach_id in component_vertices
                            if direction == "ingress"
                        ),
                        key=_natural_key,
                    )
                ),
                "egress_approach_ids": tuple(
                    sorted(
                        (
                            approach_id
                            for direction, approach_id in component_vertices
                            if direction == "egress"
                        ),
                        key=_natural_key,
                    )
                ),
            }
        )

    raw_components.sort(
        key=lambda component: (
            tuple(_natural_key(item) for item in component["movement_ids"]),
            tuple(_natural_key(item) for item in component["ingress_approach_ids"]),
            tuple(_natural_key(item) for item in component["egress_approach_ids"]),
        )
    )
    return tuple(
        HamburgOfficialApproachComponent(
            component_id=f"HH_{node_id}__approach_cell_{index:02d}",
            movement_ids=component["movement_ids"],
            ingress_approach_ids=component["ingress_approach_ids"],
            egress_approach_ids=component["egress_approach_ids"],
        )
        for index, component in enumerate(raw_components)
    )


def derive_hamburg_candidate_cell_boundary(
    *,
    candidate_net_file: Path,
    node_id: str,
    map_lanes: Sequence[MapLane],
    movement_paths: Sequence[HamburgOfficialMovementPath],
) -> HamburgCandidateCellBoundary:
    """Derive one stable SUMO cross-section for every official MAP approach.

    MAP lane bindings can land on different segments of the same OSM split way.
    Consequently, the first/last lane of each individual movement path is not a
    valid cell boundary.  For every ``(direction, MAP approach)`` this function
    finds an external edge traversed by *all* related movement paths.  The edge
    must also be supported by at least one path endpoint (the original MAP lane
    binding), which prevents an internal movement edge from being mistaken for
    an approach.  Ingress selects the uniquely most-downstream such frontier;
    egress selects the uniquely most-upstream frontier.

    A selected edge maps every official lane to the actual SUMO lane it uses at
    that cross-section.  Missing common edges, inconsistent path ordering,
    repeated edge visits, or one official lane using multiple SUMO lanes at the
    selected section are explicit fail-closed errors.
    """

    root = ET.parse(candidate_net_file).getroot()
    edge_by_id = {
        edge.attrib["id"]: edge
        for edge in root.findall("edge")
        if edge.attrib.get("id") and not edge.attrib.get("id", "").startswith(":")
    }
    lane_to_edge: dict[str, tuple[str, int]] = {}
    passenger_lane_indices_by_edge: dict[str, tuple[int, ...]] = {}
    for edge_id, edge in edge_by_id.items():
        passenger_indices: list[int] = []
        for lane in edge.findall("lane"):
            lane_id = lane.attrib.get("id", "")
            try:
                lane_index = int(lane.attrib.get("index", "0"))
            except ValueError:
                continue
            if lane_id:
                lane_to_edge[lane_id] = (edge_id, lane_index)
            if _lane_allows_passenger(lane):
                passenger_indices.append(lane_index)
        passenger_lane_indices_by_edge[edge_id] = tuple(sorted(set(passenger_indices)))

    errors: list[str] = []
    selected_paths = [path for path in movement_paths if _same_node(path.node_id, node_id)]
    resolved_paths: list[tuple[HamburgOfficialMovementPath, tuple[str, ...]]] = []
    for path in selected_paths:
        if len(path.lane_ids) < 2:
            errors.append(f"movement_path_too_short:{path.connection_id}")
            continue
        missing_lanes = [lane_id for lane_id in path.lane_ids if lane_id not in lane_to_edge]
        if missing_lanes:
            errors.append(f"movement_path_missing_lanes:{path.connection_id}:{','.join(missing_lanes)}")
            continue
        resolved_paths.append(
            (path, tuple(lane_to_edge[lane_id][0] for lane_id in path.lane_ids))
        )

    official_lane_rows: dict[str, list[MapLane]] = {}
    for lane in map_lanes:
        if _same_node(lane.node_id, node_id) and lane.is_vehicle:
            official_lane_rows.setdefault(lane.lane_id, []).append(lane)

    approach_paths: dict[
        tuple[str, str],
        list[tuple[HamburgOfficialMovementPath, tuple[str, ...]]],
    ] = {}
    for path, path_edges in resolved_paths:
        for direction, official_lane_id in (
            ("ingress", path.ingress_lane_id),
            ("egress", path.egress_lane_id),
        ):
            lane_rows = official_lane_rows.get(official_lane_id, [])
            if len(lane_rows) != 1:
                errors.append(
                    "official_approach_cross_section_map_lane_not_unique:"
                    f"{direction}:{official_lane_id}:count={len(lane_rows)}"
                )
                continue
            lane = lane_rows[0]
            approach_id = (
                lane.ingress_approach if direction == "ingress" else lane.egress_approach
            )
            if not approach_id:
                errors.append(
                    "official_approach_cross_section_missing_map_approach:"
                    f"{direction}:{official_lane_id}"
                )
                continue
            approach_paths.setdefault((direction, approach_id), []).append(
                (path, path_edges)
            )

    ingress_boundary_lanes: dict[str, str] = {}
    egress_boundary_lanes: dict[str, str] = {}
    ingress_boundary_edges: set[str] = set()
    egress_boundary_edges: set[str] = set()
    for (direction, approach_id), group_paths in sorted(
        approach_paths.items(),
        key=lambda item: (
            0 if item[0][0] == "ingress" else 1,
            _natural_key(item[0][1]),
        ),
    ):
        selection = _select_official_approach_cross_section(
            direction=direction,
            official_approach_id=approach_id,
            group_paths=group_paths,
        )
        if isinstance(selection, str):
            errors.append(selection)
            continue
        selected_edge_id, lane_by_official_lane = selection
        if direction == "ingress":
            ingress_boundary_edges.add(selected_edge_id)
            ingress_boundary_lanes.update(lane_by_official_lane)
        else:
            egress_boundary_edges.add(selected_edge_id)
            egress_boundary_lanes.update(lane_by_official_lane)

    # Only junctions between the two selected approach cross-sections belong to
    # the candidate cell.  OSM split-way junctions outside those sections must
    # remain untouched by shared-controller replay.
    junction_incidence: Counter[str] = Counter()
    for path, path_edges in resolved_paths:
        ingress_lane = ingress_boundary_lanes.get(path.ingress_lane_id)
        egress_lane = egress_boundary_lanes.get(path.egress_lane_id)
        if not ingress_lane or not egress_lane:
            continue
        ingress_positions = [
            index for index, lane_id in enumerate(path.lane_ids) if lane_id == ingress_lane
        ]
        egress_positions = [
            index for index, lane_id in enumerate(path.lane_ids) if lane_id == egress_lane
        ]
        if len(ingress_positions) != 1 or len(egress_positions) != 1:
            errors.append(
                "movement_path_cross_section_lane_occurrence_ambiguous:"
                f"{path.connection_id}:ingress={ingress_positions}:egress={egress_positions}"
            )
            continue
        ingress_position = ingress_positions[0]
        egress_position = egress_positions[0]
        if ingress_position >= egress_position:
            errors.append(
                "movement_path_cross_section_order_invalid:"
                f"{path.connection_id}:{ingress_lane}@{ingress_position}>="
                f"{egress_lane}@{egress_position}"
            )
            continue
        cell_path_edges = path_edges[ingress_position : egress_position + 1]
        for from_edge_id, to_edge_id in zip(cell_path_edges, cell_path_edges[1:]):
            from_edge = edge_by_id[from_edge_id]
            to_edge = edge_by_id[to_edge_id]
            from_junction = from_edge.attrib.get("to", "")
            to_junction = to_edge.attrib.get("from", "")
            if not from_junction or from_junction != to_junction:
                errors.append(
                    f"movement_path_nonadjacent_edges:{path.connection_id}:{from_edge_id}->{to_edge_id}"
                )
                continue
            if not from_junction.startswith(":"):
                junction_incidence[from_junction] += 1

    candidate_junction_ids = tuple(sorted(junction_incidence))
    candidate_junction_id = (
        sorted(junction_incidence, key=lambda item: (-junction_incidence[item], item))[0]
        if junction_incidence
        else ""
    )
    return HamburgCandidateCellBoundary(
        candidate_junction_id=candidate_junction_id,
        candidate_junction_ids=candidate_junction_ids,
        ingress_boundary_lanes=ingress_boundary_lanes,
        egress_boundary_lanes=egress_boundary_lanes,
        ingress_boundary_edges=tuple(sorted(ingress_boundary_edges)),
        egress_boundary_edges=tuple(sorted(egress_boundary_edges)),
        passenger_lane_indices_by_edge=passenger_lane_indices_by_edge,
        path_errors=tuple(dict.fromkeys(errors)),
    )


def build_hamburg_teacher_cell_contract(
    *,
    node_id: str,
    map_lanes: Sequence[MapLane],
    map_connections: Sequence[MapConnection],
    topology_inventory: OcitVehicleTopologyInventory,
    movement_paths: Sequence[HamburgOfficialMovementPath] = (),
    candidate_net_file: Path | None = None,
) -> HamburgTeacherCellContract:
    """Adapt official Hamburg MAP/OCIT topology to Torii's teacher-cell IR.

    Directional MAP approaches are the teacher-boundary identity.  Candidate
    edges are only alignment evidence: one is selected for scoped replay when
    every lane of an official approach resolves to that same edge.  An OSM
    collision can therefore never merge two distinct official approaches.
    """

    official_lanes = {
        lane.lane_id: lane
        for lane in map_lanes
        if _same_node(lane.node_id, node_id) and lane.is_vehicle
    }
    official_movements = [
        movement
        for movement in topology_inventory.movements
        if _same_node(movement.node_id, node_id)
    ]
    if not official_movements:
        raise ValueError(f"official topology inventory has no vehicle movement for node {node_id}")
    if len(official_lanes) != len(
        [lane for lane in map_lanes if _same_node(lane.node_id, node_id) and lane.is_vehicle]
    ):
        raise ValueError(f"official MAP contains duplicate vehicle lane ids for node {node_id}")

    map_connection_index = {
        (connection.ingress_lane_id, connection.egress_lane_id): connection
        for connection in map_connections
        if _same_node(connection.node_id, node_id)
    }
    movement_path_index = {
        (path.connection_id, path.ingress_lane_id, path.egress_lane_id): path
        for path in movement_paths
        if _same_node(path.node_id, node_id)
    }
    expected_path_keys = {
        (movement.connection_id, movement.ingress_lane_id, movement.egress_lane_id)
        for movement in official_movements
    }
    missing_path_keys = sorted(expected_path_keys - set(movement_path_index), key=_movement_path_key)
    approach_components = build_hamburg_official_approach_components(
        node_id=node_id,
        map_lanes=map_lanes,
        topology_inventory=topology_inventory,
    )
    core_center_xy = _official_movement_stopline_anchor_centroid(
        official_lanes,
        official_movements,
    )

    boundary = None
    if candidate_net_file is not None:
        boundary = derive_hamburg_candidate_cell_boundary(
            candidate_net_file=candidate_net_file,
            node_id=node_id,
            map_lanes=map_lanes,
            movement_paths=movement_paths,
        )
    ingress_boundary_lanes = boundary.ingress_boundary_lanes if boundary else {}
    egress_boundary_lanes = boundary.egress_boundary_lanes if boundary else {}

    groups: dict[tuple[str, str], _BoundaryGroup] = {}
    movement_lane_ids = {
        "ingress": {movement.ingress_lane_id for movement in official_movements},
        "egress": {movement.egress_lane_id for movement in official_movements},
    }
    for direction, lane_ids in movement_lane_ids.items():
        boundary_lanes = ingress_boundary_lanes if direction == "ingress" else egress_boundary_lanes
        for lane_id in sorted(lane_ids, key=_natural_key):
            lane = official_lanes.get(lane_id)
            if lane is None:
                raise ValueError(f"official movement references missing MAP lane {node_id}/{lane_id}")
            approach_id = lane.ingress_approach if direction == "ingress" else lane.egress_approach
            if not approach_id:
                raise ValueError(f"official MAP lane {node_id}/{lane_id} has no {direction} approach")
            candidate_lane_id = boundary_lanes.get(lane_id, "")
            group = groups.setdefault(
                (direction, approach_id),
                _BoundaryGroup(
                    direction=direction,
                    official_approach_id=approach_id,
                    candidate_edge_id="",
                    official_lane_ids=set(),
                    candidate_lane_by_official_lane={},
                ),
            )
            group.official_lane_ids.add(lane_id)
            if candidate_lane_id:
                group.candidate_lane_by_official_lane[lane_id] = candidate_lane_id

    group_alignment_gates: list[str] = []
    for group in groups.values():
        missing_candidate_lane_ids = sorted(
            group.official_lane_ids - set(group.candidate_lane_by_official_lane),
            key=_natural_key,
        )
        candidate_edge_ids = sorted(
            {
                _lane_edge_id(candidate_lane_id)
                for candidate_lane_id in group.candidate_lane_by_official_lane.values()
            }
        )
        if missing_candidate_lane_ids:
            group_alignment_gates.append(
                "official_approach_candidate_lanes_unresolved:"
                f"{group.direction}:{group.official_approach_id}:"
                f"lanes={missing_candidate_lane_ids}"
            )
        elif len(candidate_edge_ids) != 1:
            group_alignment_gates.append(
                "official_approach_candidate_edges_mismatch:"
                f"{group.direction}:{group.official_approach_id}:"
                f"edges={candidate_edge_ids}"
            )
        else:
            group.candidate_edge_id = candidate_edge_ids[0]

    sorted_groups = sorted(
        groups.values(),
        key=lambda group: (
            0 if group.direction == "ingress" else 1,
            _natural_key(group.official_approach_id),
            group.candidate_edge_id,
        ),
    )
    approaches: list[Approach] = []
    approach_pairs: list[dict[str, object]] = []
    lane_rows: list[HamburgTeacherLaneIndex] = []
    lane_lookup: dict[tuple[str, str], tuple[str, int]] = {}
    group_ordinals = {"ingress": 0, "egress": 0}
    candidate_lane_coverage_rows: list[tuple[str, set[int]]] = []
    for group in sorted_groups:
        ordinal = group_ordinals[group.direction]
        group_ordinals[group.direction] += 1
        short_direction = "I" if group.direction == "ingress" else "E"
        teacher_approach_id = f"HH_{node_id}__{short_direction}{ordinal:02d}"
        teacher_edge_id = f"HH_{node_id}__teacher_{group.direction}_{ordinal:02d}"
        unused_edge_id = f"HH_{node_id}__unused_{group.direction}_{ordinal:02d}"
        # Official MAP lanes are the teacher cardinality authority.  A many-to-one
        # OSM binding is evidence that the candidate edge must be replaced; it is
        # never permission to collapse two official lanes into one teacher lane.
        ordered_official_lane_ids = sorted(
            group.official_lane_ids,
            key=lambda lane_id: _official_lane_order_key(
                lane_id,
                group.candidate_lane_by_official_lane.get(lane_id, ""),
            ),
        )
        dense_index_by_official_lane = {
            lane_id: index for index, lane_id in enumerate(ordered_official_lane_ids)
        }
        source_shape, endpoint = _centered_official_approach_shape(
            [official_lanes[lane_id] for lane_id in ordered_official_lane_ids]
        )
        bearing_to_core = _bearing(endpoint, core_center_xy)
        bearing_from_core = (bearing_to_core + 180.0) % 360.0
        lane_count = len(ordered_official_lane_ids)
        incoming_count = lane_count if group.direction == "ingress" else 0
        outgoing_count = lane_count if group.direction == "egress" else 0
        approaches.append(
            Approach(
                approach_id=teacher_approach_id,
                role=f"hamburg_official_{group.direction}",
                source_way_ids=sorted(group.official_lane_ids, key=_natural_key),
                road_name=f"Hamburg MAP node {node_id}",
                highway_class="primary",
                bearing_to_core=bearing_to_core,
                bearing_from_core=bearing_from_core,
                endpoint_xy=endpoint,
                source_shape_xy=source_shape,
                incoming_lane_count=incoming_count,
                outgoing_lane_count=outgoing_count,
                incoming_edge_ids=[teacher_edge_id if group.direction == "ingress" else unused_edge_id],
                outgoing_edge_ids=[teacher_edge_id if group.direction == "egress" else unused_edge_id],
                oneway=True,
                has_incoming_vehicle_flow=group.direction == "ingress",
                has_outgoing_vehicle_flow=group.direction == "egress",
                direction_evidence=[
                    "hamburg_mapem_directional_approach",
                    "teacher_edge_centered_on_official_stopline_cross_section",
                    f"official_approach:{group.official_approach_id}",
                ],
                allowed_modes={"passenger"},
                turn_lanes_raw=None,
            )
        )
        if group.candidate_edge_id:
            approach_pairs.append(
                {
                    "reference_edge_id": teacher_edge_id,
                    "candidate_edge_id": group.candidate_edge_id,
                    "direction": group.direction,
                    "official_approach_id": group.official_approach_id,
                    "official_approach_ids": [group.official_approach_id],
                    "official_lane_ids": sorted(group.official_lane_ids, key=_natural_key),
                }
            )
        group_used_candidate_lane_indices: set[int] = set()
        for official_lane_id in sorted(group.official_lane_ids, key=_natural_key):
            teacher_lane_index = dense_index_by_official_lane[official_lane_id]
            candidate_lane_id = group.candidate_lane_by_official_lane.get(official_lane_id, "")
            candidate_edge_id = _lane_edge_id(candidate_lane_id) if candidate_lane_id else ""
            if candidate_lane_id:
                group_used_candidate_lane_indices.add(_lane_index(candidate_lane_id))
            official_lane = official_lanes[official_lane_id]
            official_approach_id = (
                official_lane.ingress_approach if group.direction == "ingress" else official_lane.egress_approach
            )
            lane_lookup[(group.direction, official_lane_id)] = (teacher_approach_id, teacher_lane_index)
            lane_rows.append(
                HamburgTeacherLaneIndex(
                    direction=group.direction,
                    official_lane_id=official_lane_id,
                    official_approach_id=official_approach_id,
                    teacher_approach_id=teacher_approach_id,
                    teacher_edge_id=teacher_edge_id,
                    teacher_lane_index=teacher_lane_index,
                    candidate_lane_id=candidate_lane_id,
                    candidate_edge_id=candidate_edge_id,
                )
            )
        if group.candidate_edge_id:
            candidate_lane_coverage_rows.append(
                (group.candidate_edge_id, group_used_candidate_lane_indices)
            )

    expression_by_node = topology_control_index_by_node(topology_inventory)
    expression_index_by_key = expression_by_node.get(_normalize_node(node_id), {})
    movements: list[Movement] = []
    movement_id_by_key: dict[tuple[str, str, str], str] = {}
    sorted_official_movements = sorted(
        official_movements,
        key=lambda movement: (
            _natural_key(movement.connection_id),
            _natural_key(movement.ingress_lane_id),
            _natural_key(movement.egress_lane_id),
        ),
    )
    for official_movement in sorted_official_movements:
        from_approach_id, from_lane_index = lane_lookup[("ingress", official_movement.ingress_lane_id)]
        to_approach_id, to_lane_index = lane_lookup[("egress", official_movement.egress_lane_id)]
        movement_id = (
            f"HH_{node_id}__C{_slug(official_movement.connection_id)}__"
            f"L{_slug(official_movement.ingress_lane_id)}_{_slug(official_movement.egress_lane_id)}"
        )
        movement_id_by_key[
            (
                official_movement.connection_id,
                official_movement.ingress_lane_id,
                official_movement.egress_lane_id,
            )
        ] = movement_id
        map_connection = map_connection_index.get(
            (official_movement.ingress_lane_id, official_movement.egress_lane_id)
        )
        turn = _turn_from_maneuver_bits(map_connection.maneuver_bits if map_connection else "")
        movements.append(
            Movement(
                movement_id=movement_id,
                from_approach_id=from_approach_id,
                to_approach_id=to_approach_id,
                road_pair_relation_id=(
                    f"hamburg-map:{node_id}:{official_movement.ingress_lane_id}->"
                    f"{official_movement.egress_lane_id}"
                ),
                turn=turn,
                allowed=True,
                from_lane_indices=[from_lane_index],
                to_lane_indices=[to_lane_index],
                allowed_modes={"passenger"},
                evidence=[
                    "hamburg_official_mapem_connection",
                    "hamburg_official_ocit_c_vehicle_movement",
                    f"topology_control_key:{official_movement.topology_control_key}",
                ],
                confidence=1.0,
            )
        )

    link_index_map = {
        movement_id_by_key[(movement.connection_id, movement.ingress_lane_id, movement.egress_lane_id)]:
        expression_index_by_key[movement.topology_control_key]
        for movement in sorted_official_movements
    }
    expression_key_by_index = {index: key for key, index in expression_index_by_key.items()}
    phases: list[TLSPhase] = []
    for index in sorted(expression_key_by_index):
        expression_key = expression_key_by_index[index]
        phases.append(
            TLSPhase(
                phase_id=f"topology_basis_{index:02d}_{_slug(expression_key)}",
                duration=1.0,
                state="".join("g" if item == index else "r" for item in range(len(expression_key_by_index))),
                movement_ids=[
                    movement_id_by_key[(movement.connection_id, movement.ingress_lane_id, movement.egress_lane_id)]
                    for movement in sorted_official_movements
                    if movement.topology_control_key == expression_key
                ],
            )
        )

    reference_lane = official_lanes[sorted(official_lanes, key=_natural_key)[0]]
    center_latlon = _offset_latlon(
        reference_lane.ref_latitude,
        reference_lane.ref_longitude,
        core_center_xy,
    )
    ir = IntersectionIR(
        intersection_id=f"hamburg-official-{node_id}",
        osm_patch=OSMPatch(
            nodes={},
            ways={},
            relations={},
            bbox=BBox(
                min_lon=reference_lane.ref_longitude,
                min_lat=reference_lane.ref_latitude,
                max_lon=reference_lane.ref_longitude,
                max_lat=reference_lane.ref_latitude,
            ),
            seed=PatchSeed(center_latlon=(reference_lane.ref_latitude, reference_lane.ref_longitude)),
        ),
        core=IntersectionCore(
            core_id=f"HH_{node_id}",
            center_xy=core_center_xy,
            center_latlon=center_latlon,
            core_osm_node_ids=[],
            core_way_ids=[],
            core_radius_m=max(
                (
                    math.dist(point, core_center_xy)
                    for lane in official_lanes.values()
                    for point in lane.points_m
                ),
                default=1.0,
            ),
            topology_type="complex",
            internal_fragment_count=0,
            short_internal_edge_count=0,
            confidence=1.0,
        ),
        approaches=approaches,
        road_pair_graph=RoadPairRelationGraph(
            relations=[],
            missing_connection_count=0,
            wrong_connection_count=0,
            overlap_conflict_count=0,
            near_miss_count=0,
            duplicate_parallel_count=0,
            blocking_error_count=0,
        ),
        movement_matrix=MovementMatrix(
            movements=movements,
            legal_movement_count=len(movements),
            forbidden_movement_count=0,
            inferred_movement_count=0,
        ),
        control=ControlModel(
            control_type="traffic_light",
            source=[
                "hamburg_official_mapem",
                "hamburg_official_ocit_c",
                "teacher_core_is_centroid_of_unique_official_movement_stopline_anchors",
                "map_ingress_lane_points_m_0_is_the_official_stopline_anchor",
                "topology_basis_only_not_an_official_signal_program",
            ],
            priority_approach_ids=[],
            tls_id=f"HH_{node_id}",
            phases=phases,
            link_index_map=link_index_map,
            confidence=1.0,
        ),
        claim_status="semantic-model-built",
    )

    review_gates: list[str] = []
    if len(approach_components) > 1:
        review_gates.append(
            "multiple_official_approach_cells_require_shared_controller_replay:"
            f"{len(approach_components)}"
        )
    if missing_path_keys:
        review_gates.append(f"missing_official_movement_paths:{len(missing_path_keys)}")
    if candidate_net_file is None:
        review_gates.append("candidate_net_required_for_scoped_cell_boundary")
    elif boundary is not None:
        review_gates.extend(boundary.path_errors)
        review_gates.extend(group_alignment_gates)
        if not boundary.candidate_junction_id:
            review_gates.append("no_candidate_junction_on_official_paths")
        for candidate_edge_id, used_indices in sorted(
            candidate_lane_coverage_rows,
            key=lambda item: (item[0], sorted(item[1])),
        ):
            expected_indices = set(boundary.passenger_lane_indices_by_edge.get(candidate_edge_id, ()))
            if expected_indices and used_indices != expected_indices:
                review_gates.append(
                    "candidate_boundary_passenger_lane_subset:"
                    f"{candidate_edge_id}:used={sorted(used_indices)}:expected={sorted(expected_indices)}"
                )
    pairs_by_candidate_edge: dict[str, list[dict[str, object]]] = {}
    for pair in approach_pairs:
        pairs_by_candidate_edge.setdefault(str(pair["candidate_edge_id"]), []).append(pair)
    for candidate_edge_id, pairs in sorted(pairs_by_candidate_edge.items()):
        if len(pairs) < 2:
            continue
        review_gates.append(
            "candidate_boundary_edge_reused_by_multiple_teacher_edges:"
            f"{candidate_edge_id}:"
            f"official_approaches={sorted(str(pair['official_approach_id']) for pair in pairs)}"
        )

    mixed_expression_keys = sorted(
        {
            movement.topology_control_key
            for movement in official_movements
            if movement.primary_motor_groups and movement.secondary_motor_groups
        }
    )
    exact_signal_replay_gates = ["topology_basis_phases_are_not_an_official_signal_program"]
    exact_signal_replay_gates.extend(
        f"mixed_primary_secondary_expression_requires_state_resolver:{key}"
        for key in mixed_expression_keys
    )
    return HamburgTeacherCellContract(
        ir=ir,
        approach_components=approach_components,
        approach_pairs=tuple(approach_pairs),
        lane_indices=tuple(lane_rows),
        candidate_junction_id=boundary.candidate_junction_id if boundary else "",
        candidate_junction_ids=boundary.candidate_junction_ids if boundary else (),
        expression_index_by_key=expression_index_by_key,
        expression_compression_budget=len(expression_index_by_key),
        topology_status="ready_for_scoped_teacher_replay" if not review_gates else "blocked",
        review_gates=tuple(review_gates),
        exact_signal_replay_gates=tuple(exact_signal_replay_gates),
    )


def build_hamburg_teacher_expression_grouping_variant(
    *,
    teacher_net_file: Path,
    output_dir: Path,
    contract: HamburgTeacherCellContract,
    prefix: str = "hamburg_teacher_expression_grouping",
) -> dict[str, object]:
    """Reuse Torii's identical-column compressor for official expressions."""

    return build_tls_signal_grouping_variant(
        source_net_file=teacher_net_file,
        output_dir=output_dir,
        prefix=prefix,
        max_shared_linkindex_groups=contract.expression_compression_budget,
    )


def materialize_hamburg_single_teacher_cell(
    *,
    contract: HamburgTeacherCellContract,
    output_dir: Path,
    prefix: str = "hamburg_single_teacher",
) -> dict[str, object]:
    """Materialize a one-cell Hamburg contract through Torii's native path.

    The shared-controller materializer is intentionally reused for the one-owner
    case.  It compiles the ``IntersectionIR``, lets netconvert build legal SUMO
    internals, restores official movement control by the stable physical key
    ``(from, to, fromLane, toLane)``, and only then applies Torii's identical-
    signature grouping.  This avoids trusting pre-netconvert ``linkIndex`` order.
    """

    if len(contract.approach_components) != 1:
        raise ValueError(
            "single-cell Hamburg materialization requires exactly one official "
            f"approach component, got {len(contract.approach_components)}"
        )
    # Local import keeps hamburg_shared_teacher free to use this contract type in
    # annotations without introducing a module import cycle.
    from .hamburg_shared_teacher import materialize_hamburg_shared_controller_teacher

    result = materialize_hamburg_shared_controller_teacher(
        ir=contract.ir,
        approach_pairs=contract.approach_pairs,
        output_dir=output_dir,
        prefix=prefix,
        control_key_by_expression_index={
            expression_index: control_key
            for control_key, expression_index in contract.expression_index_by_key.items()
        },
        expected_component_count=1,
    )
    return {
        **result,
        "materialization_mode": "single_official_approach_cell",
    }


def _select_official_approach_cross_section(
    *,
    direction: str,
    official_approach_id: str,
    group_paths: Sequence[
        tuple[HamburgOfficialMovementPath, tuple[str, ...]]
    ],
) -> tuple[str, dict[str, str]] | str:
    """Select a unique endpoint-supported common edge for one MAP approach."""

    label = f"{direction}:{official_approach_id}"
    if not group_paths:
        return f"official_approach_cross_section_missing:{label}:paths=[]"

    common_edge_ids = set(group_paths[0][1])
    for _path, path_edges in group_paths[1:]:
        common_edge_ids.intersection_update(path_edges)
    if not common_edge_ids:
        movement_ids = sorted(
            {path.connection_id for path, _path_edges in group_paths},
            key=_natural_key,
        )
        return (
            f"official_approach_cross_section_missing:{label}:"
            f"movements={movement_ids}"
        )

    endpoint_position = 0 if direction == "ingress" else -1
    endpoint_edge_ids = {
        path_edges[endpoint_position] for _path, path_edges in group_paths
    }
    frontier_edge_ids = sorted(common_edge_ids & endpoint_edge_ids)
    if not frontier_edge_ids:
        return (
            f"official_approach_cross_section_missing_endpoint_frontier:{label}:"
            f"common_edges={sorted(common_edge_ids)}:"
            f"endpoint_edges={sorted(endpoint_edge_ids)}"
        )

    positions_by_edge: dict[str, tuple[int, ...]] = {}
    for edge_id in frontier_edge_ids:
        positions: list[int] = []
        repeated_by_movement: list[str] = []
        for path, path_edges in group_paths:
            occurrences = [
                index for index, candidate_edge_id in enumerate(path_edges)
                if candidate_edge_id == edge_id
            ]
            if len(occurrences) != 1:
                repeated_by_movement.append(path.connection_id)
                continue
            positions.append(occurrences[0])
        if repeated_by_movement:
            return (
                f"official_approach_cross_section_edge_occurrence_ambiguous:{label}:"
                f"edge={edge_id}:movements={sorted(repeated_by_movement, key=_natural_key)}"
            )
        positions_by_edge[edge_id] = tuple(positions)

    def dominates(left_edge_id: str, right_edge_id: str) -> bool:
        left = positions_by_edge[left_edge_id]
        right = positions_by_edge[right_edge_id]
        if direction == "ingress":
            return all(a >= b for a, b in zip(left, right, strict=True)) and any(
                a > b for a, b in zip(left, right, strict=True)
            )
        return all(a <= b for a, b in zip(left, right, strict=True)) and any(
            a < b for a, b in zip(left, right, strict=True)
        )

    extrema = [
        edge_id
        for edge_id in frontier_edge_ids
        if not any(
            dominates(other_edge_id, edge_id)
            for other_edge_id in frontier_edge_ids
            if other_edge_id != edge_id
        )
    ]
    if len(extrema) != 1:
        return (
            f"official_approach_cross_section_order_ambiguous:{label}:"
            f"edges={extrema}:positions="
            f"{[(edge_id, positions_by_edge[edge_id]) for edge_id in extrema]}"
        )

    selected_edge_id = extrema[0]
    lane_ids_by_official_lane: dict[str, set[str]] = {}
    selected_positions = positions_by_edge[selected_edge_id]
    for (path, _path_edges), position in zip(
        group_paths,
        selected_positions,
        strict=True,
    ):
        official_lane_id = (
            path.ingress_lane_id if direction == "ingress" else path.egress_lane_id
        )
        lane_ids_by_official_lane.setdefault(official_lane_id, set()).add(
            path.lane_ids[position]
        )
    ambiguous_lanes = {
        official_lane_id: sorted(candidate_lane_ids)
        for official_lane_id, candidate_lane_ids in lane_ids_by_official_lane.items()
        if len(candidate_lane_ids) != 1
    }
    if ambiguous_lanes:
        return (
            f"official_approach_cross_section_lane_ambiguous:{label}:"
            f"edge={selected_edge_id}:lanes={ambiguous_lanes}"
        )
    return selected_edge_id, {
        official_lane_id: next(iter(candidate_lane_ids))
        for official_lane_id, candidate_lane_ids in lane_ids_by_official_lane.items()
    }


def _lane_allows_passenger(lane: ET.Element) -> bool:
    from .osm_access import _permission_set

    return "passenger" in _permission_set(lane.attrib)


def _lane_edge_id(lane_id: str) -> str:
    if not lane_id or "_" not in lane_id:
        raise ValueError(f"SUMO lane id has no numeric suffix: {lane_id}")
    edge_id, lane_index = lane_id.rsplit("_", 1)
    int(lane_index)
    return edge_id


def _lane_index(lane_id: str) -> int:
    _edge_id, lane_index = lane_id.rsplit("_", 1)
    return int(lane_index)


def _official_lane_order_key(
    official_lane_id: str,
    candidate_lane_id: str,
) -> tuple[int, object, tuple[object, ...]]:
    if candidate_lane_id:
        return (0, _lane_index(candidate_lane_id), _natural_key(official_lane_id))
    return (1, _natural_key(official_lane_id), _natural_key(official_lane_id))


def _centered_official_approach_shape(
    lanes: Sequence[MapLane],
) -> tuple[list[tuple[float, float]], tuple[float, float]]:
    """Return a road centre-line instead of reusing one side lane as the edge shape.

    MAP lane polylines are lane centre-lines.  SUMO plain-edge geometry is the
    cross-section centre-line from which its lane shapes are offset.  Reusing the
    first official lane therefore shifts a two- or three-lane road laterally and
    can make the final MAP projection swap lane indices.  Keep the longest official
    polyline for longitudinal detail, but translate it so its stop-line anchor lies
    at the centroid of all official stop-line anchors.
    """

    usable = [lane for lane in lanes if lane.points_m]
    if not usable:
        return [], (0.0, 0.0)
    stopline_center = (
        sum(lane.points_m[0][0] for lane in usable) / len(usable),
        sum(lane.points_m[0][1] for lane in usable) / len(usable),
    )
    representative = min(
        usable,
        key=lambda lane: (
            -_polyline_length(lane.points_m),
            -len(lane.points_m),
            _natural_key(lane.lane_id),
        ),
    )
    shift = (
        stopline_center[0] - representative.points_m[0][0],
        stopline_center[1] - representative.points_m[0][1],
    )
    shifted = [
        (point[0] + shift[0], point[1] + shift[1])
        for point in representative.points_m
    ]
    return list(reversed(shifted)), shifted[-1]


def _polyline_length(points: Sequence[tuple[float, float]]) -> float:
    return sum(
        math.hypot(right[0] - left[0], right[1] - left[1])
        for left, right in zip(points, points[1:])
    )


def _turn_from_maneuver_bits(bits: str) -> str:
    value = bits.strip()
    if value.startswith("100"):
        return "straight"
    if value.startswith("010"):
        return "left"
    if value.startswith("001"):
        return "right"
    return "straight"


def _official_movement_stopline_anchor_centroid(
    official_lanes: dict[str, MapLane],
    official_movements: Sequence[OcitVehicleTopologyMovement],
) -> tuple[float, float]:
    used_lane_ids = {
        movement.ingress_lane_id
        for movement in official_movements
    }
    anchors = [
        official_lanes[lane_id].points_m[0]
        for lane_id in sorted(used_lane_ids, key=_natural_key)
        if lane_id in official_lanes and official_lanes[lane_id].points_m
    ]
    if not anchors:
        return (0.0, 0.0)
    return (
        sum(point[0] for point in anchors) / len(anchors),
        sum(point[1] for point in anchors) / len(anchors),
    )


def _offset_latlon(
    reference_latitude: float,
    reference_longitude: float,
    offset_xy_m: tuple[float, float],
) -> tuple[float, float]:
    earth_radius_m = 6_378_137.0
    latitude = reference_latitude + math.degrees(offset_xy_m[1] / earth_radius_m)
    longitude_scale = max(1e-9, math.cos(math.radians(reference_latitude)))
    longitude = reference_longitude + math.degrees(
        offset_xy_m[0] / (earth_radius_m * longitude_scale)
    )
    return (latitude, longitude)


def _bearing(start: tuple[float, float], end: tuple[float, float]) -> float:
    dx = end[0] - start[0]
    dy = end[1] - start[1]
    if math.hypot(dx, dy) < 1e-9:
        return 0.0
    return math.degrees(math.atan2(dx, dy)) % 360.0


def _normalize_node(value: str) -> str:
    return value.lstrip("0") or "0"


def _same_node(left: str, right: str) -> bool:
    return _normalize_node(left) == _normalize_node(right)


def _slug(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", value).strip("_") or "NONE"


def _natural_key(value: str) -> tuple[object, ...]:
    return tuple(int(part) if part.isdigit() else part.casefold() for part in re.split(r"(\d+)", value))


def _approach_vertex_key(value: tuple[str, str]) -> tuple[int, tuple[object, ...]]:
    direction, approach_id = value
    return (0 if direction == "ingress" else 1, _natural_key(approach_id))


def _movement_path_key(value: tuple[str, str, str]) -> tuple[tuple[object, ...], ...]:
    return tuple(_natural_key(item) for item in value)
