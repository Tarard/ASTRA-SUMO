from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from .ids import stable_id
from .netxml import RawConnection, RawEdge, RawLane, RawNetwork
from .review import PedestrianCrossingReviewSubject


PEDESTRIAN_FACILITY_FUNCTIONS = frozenset({"crossing", "walkingarea"})


@dataclass(frozen=True)
class PedestrianFacilityOwner:
    junction_id: str
    physical_cell_id: str


@dataclass(frozen=True)
class PedestrianCrossingModel:
    connection_index: int
    physical_cell_id: str
    boundary_port_ids: tuple[str, ...]
    movement_id: str
    source_lane_role_id: str
    destination_lane_role_id: str
    lane_role_payloads: tuple[tuple[str, dict[str, Any]], ...]
    path_signature: str
    path: dict[str, Any]
    movement_variant: dict[str, Any]
    crossing_edge_id: str
    walkingarea_edge_ids: tuple[str, ...]
    controlled: bool


@dataclass(frozen=True)
class PedestrianCrossingAttempt:
    is_candidate: bool
    model: PedestrianCrossingModel | None = None
    rejection_reasons: tuple[str, ...] = ()
    review_subject: PedestrianCrossingReviewSubject | None = None


def infer_pedestrian_facility_owners(
    network: RawNetwork,
    *,
    junction_cell_ids: Mapping[str, str],
) -> dict[str, PedestrianFacilityOwner]:
    """Resolve crossing/walkingarea ownership without trusting internal edge IDs."""

    facility_edges = {
        edge_id: edge for edge_id, edge in network.edges.items() if edge.function in PEDESTRIAN_FACILITY_FUNCTIONS
    }
    candidates: dict[str, set[str]] = {edge_id: set() for edge_id in facility_edges}
    for edge_id, edge in facility_edges.items():
        lane_ids = {lane.lane_id for lane in edge.lanes}
        for junction_id, junction in network.junctions.items():
            if junction_id not in junction_cell_ids:
                continue
            if lane_ids & (set(junction.incoming_lane_ids) | set(junction.internal_lane_ids)):
                candidates[edge_id].add(junction_id)
    facility_links: list[tuple[str, str]] = []
    for connection in network.connections:
        source = network.edges.get(connection.from_edge)
        target = network.edges.get(connection.to_edge)
        if source is None or target is None:
            continue
        source_is_facility = source.edge_id in facility_edges
        target_is_facility = target.edge_id in facility_edges
        if source_is_facility and target_is_facility:
            facility_links.append((source.edge_id, target.edge_id))
            continue
        if source_is_facility and target.external:
            if target.from_junction in junction_cell_ids:
                candidates[source.edge_id].add(target.from_junction)
        if target_is_facility and source.external:
            if source.to_junction in junction_cell_ids:
                candidates[target.edge_id].add(source.to_junction)

    changed = True
    while changed:
        changed = False
        for source_id, target_id in facility_links:
            merged = candidates[source_id] | candidates[target_id]
            if merged != candidates[source_id]:
                candidates[source_id] = set(merged)
                changed = True
            if merged != candidates[target_id]:
                candidates[target_id] = set(merged)
                changed = True

    owners: dict[str, PedestrianFacilityOwner] = {}
    for edge_id, junction_ids in candidates.items():
        if len(junction_ids) != 1:
            continue
        junction_id = next(iter(junction_ids))
        owners[edge_id] = PedestrianFacilityOwner(
            junction_id=junction_id,
            physical_cell_id=junction_cell_ids[junction_id],
        )
    return owners


def model_pedestrian_crossing(
    connection: RawConnection,
    *,
    network: RawNetwork,
    facility_owners: Mapping[str, PedestrianFacilityOwner],
    port_ids_by_edge_flow: Mapping[tuple[str, str, str], str],
    outgoing_connections: Mapping[tuple[str, int], Sequence[RawConnection]],
) -> PedestrianCrossingAttempt:
    source_edge = network.edges.get(connection.from_edge)
    crossing_edge = network.edges.get(connection.to_edge)
    is_candidate = bool(
        source_edge is not None
        and crossing_edge is not None
        and source_edge.function == "walkingarea"
        and crossing_edge.function == "crossing"
    )
    if not is_candidate:
        return PedestrianCrossingAttempt(is_candidate=False)

    rejection_reasons: list[str] = []
    controlled = bool(connection.controller_id)
    if controlled and connection.link_index is None:
        rejection_reasons.append("controlled_pedestrian_link_index_missing")
    if not controlled and (
        connection.link_index is not None or connection.link_index2 is not None
    ):
        rejection_reasons.append(
            "uncontrolled_pedestrian_signal_index_without_controller"
        )
    if connection.via:
        rejection_reasons.append("pedestrian_via_not_supported")
    source_lane = _lane_by_ordinal(source_edge, connection.from_lane)
    crossing_lane = _lane_by_ordinal(crossing_edge, connection.to_lane)
    if source_lane is None:
        rejection_reasons.append("pedestrian_source_lane_missing")
    if crossing_lane is None:
        rejection_reasons.append("pedestrian_crossing_lane_missing")

    source_owner = facility_owners.get(source_edge.edge_id)
    crossing_owner = facility_owners.get(crossing_edge.edge_id)
    if source_owner is None or crossing_owner is None:
        rejection_reasons.append("pedestrian_owner_unresolved")
    elif source_owner != crossing_owner:
        rejection_reasons.append("pedestrian_owner_mismatch")

    continuation: RawConnection | None = None
    destination_edge = None
    destination_lane = None
    if crossing_lane is not None:
        continuations = tuple(
            outgoing_connections.get(
                (crossing_edge.edge_id, crossing_lane.ordinal),
                (),
            )
        )
        if len(continuations) != 1:
            rejection_reasons.append(
                f"pedestrian_continuation_count:{len(continuations)}"
            )
        else:
            continuation = continuations[0]
            if continuation.via:
                rejection_reasons.append(
                    "pedestrian_continuation_via_not_supported"
                )
            destination_edge = network.edges.get(continuation.to_edge)
            if destination_edge is None or destination_edge.function != "walkingarea":
                rejection_reasons.append(
                    "pedestrian_destination_not_walkingarea"
                )
            else:
                destination_lane = _lane_by_ordinal(
                    destination_edge,
                    continuation.to_lane,
                )
                if destination_lane is None:
                    rejection_reasons.append(
                        "pedestrian_destination_lane_missing"
                    )
                destination_owner = facility_owners.get(destination_edge.edge_id)
                if crossing_owner is None or destination_owner is None:
                    rejection_reasons.append(
                        "pedestrian_destination_owner_unresolved"
                    )
                elif destination_owner != crossing_owner:
                    rejection_reasons.append(
                        "pedestrian_destination_owner_mismatch"
                    )

    if crossing_lane is not None:
        if len(crossing_lane.shape) < 2:
            rejection_reasons.append("pedestrian_geometry_missing")
        if crossing_lane.width is None or crossing_lane.width <= 0:
            rejection_reasons.append("pedestrian_width_missing")
    if not crossing_edge.crossing_edge_ids:
        rejection_reasons.append("pedestrian_crossing_edges_missing")

    crossed_boundary_ports: list[str] = []
    crossed_edge_signatures: list[str] = []
    for crossed_edge_id in crossing_edge.crossing_edge_ids:
        crossed_edge = network.edges.get(crossed_edge_id)
        if crossed_edge is None or not crossed_edge.external:
            rejection_reasons.append(
                "pedestrian_crossed_edge_missing_or_internal"
            )
            continue
        crossed_edge_signatures.append(
            _external_edge_semantic_signature(crossed_edge)
        )
        if crossing_owner is None:
            continue
        junction_id = crossing_owner.junction_id
        candidate_ports: list[str] = []
        if crossed_edge.to_junction == junction_id:
            port_id = port_ids_by_edge_flow.get(
                (junction_id, crossed_edge.edge_id, "incoming")
            )
            if port_id:
                candidate_ports.append(port_id)
        if crossed_edge.from_junction == junction_id:
            port_id = port_ids_by_edge_flow.get(
                (junction_id, crossed_edge.edge_id, "outgoing")
            )
            if port_id:
                candidate_ports.append(port_id)
        if len(candidate_ports) != 1:
            rejection_reasons.append(
                "pedestrian_crossed_port_unresolved"
            )
            continue
        crossed_boundary_ports.append(candidate_ports[0])
    if crossing_edge.crossing_edge_ids and not crossed_boundary_ports:
        rejection_reasons.append("pedestrian_crossed_ports_empty")

    permission_contract = _permission_contract((source_lane, connection, crossing_lane, continuation, destination_lane))
    if (
        permission_contract["coverage"] != "complete"
        or permission_contract["status"] != "pass"
        or not _contract_allows_pedestrian(permission_contract)
    ):
        rejection_reasons.append("pedestrian_permission_incompatible")

    if rejection_reasons:
        sorted_reasons = tuple(sorted(set(rejection_reasons)))
        return PedestrianCrossingAttempt(
            is_candidate=True,
            rejection_reasons=sorted_reasons,
            review_subject=_build_rejected_crossing_review_subject(
                controlled=controlled,
                source_lane=source_lane,
                crossing_lane=crossing_lane,
                destination_lane=destination_lane,
                physical_cell_id=(
                    crossing_owner.physical_cell_id
                    if crossing_owner is not None
                    else None
                ),
                boundary_port_ids=tuple(sorted(set(crossed_boundary_ports))),
                crossed_edge_signatures=tuple(
                    sorted(set(crossed_edge_signatures))
                ),
                permission_contract=permission_contract,
                rejection_reasons=sorted_reasons,
            ),
        )

    assert source_lane is not None
    assert crossing_lane is not None
    assert continuation is not None
    assert destination_edge is not None
    assert destination_lane is not None
    assert crossing_owner is not None
    boundary_port_ids = tuple(sorted(set(crossed_boundary_ports)))
    crossing_signature = stable_id(
        "signature",
        {
            "physical_cell_id": crossing_owner.physical_cell_id,
            "facility_kind": "pedestrian-crossing-occupancy",
            "crossed_boundary_port_ids": boundary_port_ids,
            "crossing_lane_ordinal": crossing_lane.ordinal,
        },
    )
    endpoints = tuple(sorted((_rounded_point(crossing_lane.shape[0]), _rounded_point(crossing_lane.shape[-1]))))
    source_lane_role_id = stable_id(
        "lane_role",
        {"crossing_signature": crossing_signature, "facility_endpoint": "a"},
    )
    destination_lane_role_id = stable_id(
        "lane_role",
        {"crossing_signature": crossing_signature, "facility_endpoint": "b"},
    )
    movement_id = stable_id(
        "movement",
        {
            "physical_cell_id": crossing_owner.physical_cell_id,
            "crossing_signature": crossing_signature,
            "mode": "pedestrian",
            "turn_class": "crossing",
        },
    )
    path = {
        "status": "pass",
        "segments": (
            {
                "shape_xy": tuple(_rounded_point(point) for point in crossing_lane.shape),
                "permissions": _lane_permissions(crossing_lane),
                "width_m": crossing_lane.width,
                "speed_mps": crossing_lane.speed,
            },
        ),
        "failures": (),
        "permission_contract": permission_contract,
    }
    path_signature = stable_id(
        "signature",
        {"movement_id": movement_id, "path": path},
    )
    role_payloads = tuple(
        (
            role_id,
            {
                "physical_cell_id": crossing_owner.physical_cell_id,
                "facility_kind": "pedestrian-crossing-endpoint",
                "facility_endpoint": endpoint_name,
                "crossing_signature": crossing_signature,
                "crossed_boundary_port_ids": boundary_port_ids,
                "permissions": {"allow": ("pedestrian",), "disallow": ()},
                "position_xy": endpoint,
                "width_m": crossing_lane.width,
            },
        )
        for role_id, endpoint_name, endpoint in (
            (source_lane_role_id, "a", endpoints[0]),
            (destination_lane_role_id, "b", endpoints[1]),
        )
    )
    return PedestrianCrossingAttempt(
        is_candidate=True,
        model=PedestrianCrossingModel(
            connection_index=connection.connection_index,
            physical_cell_id=crossing_owner.physical_cell_id,
            boundary_port_ids=boundary_port_ids,
            movement_id=movement_id,
            source_lane_role_id=source_lane_role_id,
            destination_lane_role_id=destination_lane_role_id,
            lane_role_payloads=role_payloads,
            path_signature=path_signature,
            path=path,
            movement_variant={
                "source_lane_role_id": source_lane_role_id,
                "destination_lane_role_id": destination_lane_role_id,
                "mode": "pedestrian",
                "mode_classes": ("pedestrian",),
                "turn_class": "crossing",
                "movement_kind": "pedestrian-crossing-occupancy",
                "crossing_signature": crossing_signature,
                "crossed_boundary_port_ids": boundary_port_ids,
                "state": connection.state,
                "path_signature": path_signature,
            },
            crossing_edge_id=crossing_edge.edge_id,
            walkingarea_edge_ids=tuple(sorted({source_edge.edge_id, destination_edge.edge_id})),
            controlled=controlled,
        ),
    )


def _lane_by_ordinal(edge: Any, ordinal: int | None) -> RawLane | None:
    if edge is None or ordinal is None or not 0 <= ordinal < len(edge.lanes):
        return None
    return edge.lanes[ordinal]


def _lane_permissions(lane: RawLane) -> dict[str, tuple[str, ...]]:
    return {"allow": lane.allow, "disallow": lane.disallow}


def _permission_contract(
    records: Sequence[RawLane | RawConnection | None],
) -> dict[str, Any]:
    available = tuple(record for record in records if record is not None)
    complete = len(available) == len(records)
    explicit: set[str] | None = None
    disallowed: set[str] = set()
    forced_empty = False
    for record in available:
        allowed = set(record.allow)
        denied = set(record.disallow)
        if "all" in denied:
            forced_empty = True
        disallowed.update(denied - {"all"})
        if allowed and "all" not in allowed:
            explicit = allowed if explicit is None else explicit & allowed
    if forced_empty:
        overlap: tuple[str, ...] = ()
        basis = "explicit_empty"
    elif explicit is None:
        overlap = ("*",)
        basis = "implicit_all_except_disallow"
    else:
        overlap = tuple(sorted(explicit - disallowed))
        basis = "explicit_allow_intersection"
    return {
        "coverage": "complete" if complete else "partial",
        "status": ("fail" if not overlap else "pass" if complete else "unresolved"),
        "basis": basis,
        "allow": overlap,
        "disallow": tuple(sorted(disallowed)),
        "element_count": len(available),
    }


def _contract_allows_pedestrian(contract: Mapping[str, Any]) -> bool:
    allowed = set(map(str, contract.get("allow", ())))
    disallowed = set(map(str, contract.get("disallow", ())))
    return "pedestrian" not in disallowed and ("*" in allowed or "pedestrian" in allowed)


def _rounded_point(point: tuple[float, float]) -> tuple[float, float]:
    return (round(point[0], 6), round(point[1], 6))


def _external_edge_semantic_signature(edge: RawEdge) -> str:
    return stable_id(
        "signature",
        {
            "edge_type": edge.edge_type,
            "priority": edge.priority,
            "name": edge.name,
            "params": edge.params,
            "lanes": tuple(
                {
                    "ordinal": lane.ordinal,
                    "shape_xy": tuple(
                        _rounded_point(point) for point in lane.shape
                    ),
                    "permissions": _lane_permissions(lane),
                    "width_m": lane.width,
                    "speed_mps": lane.speed,
                }
                for lane in edge.lanes
            ),
        },
    )


def _build_rejected_crossing_review_subject(
    *,
    controlled: bool,
    source_lane: RawLane | None,
    crossing_lane: RawLane | None,
    destination_lane: RawLane | None,
    physical_cell_id: str | None,
    boundary_port_ids: tuple[str, ...],
    crossed_edge_signatures: tuple[str, ...],
    permission_contract: Mapping[str, Any],
    rejection_reasons: tuple[str, ...],
) -> PedestrianCrossingReviewSubject:
    crossing_shape = tuple(
        _rounded_point(point)
        for point in (crossing_lane.shape if crossing_lane is not None else ())
    )
    all_points = crossing_shape or tuple(
        point
        for lane in (source_lane, destination_lane)
        if lane is not None
        for point in (_rounded_point(value) for value in lane.shape)
    )
    position_xy = (
        (
            round(sum(point[0] for point in all_points) / len(all_points), 6),
            round(sum(point[1] for point in all_points) / len(all_points), 6),
        )
        if all_points
        else None
    )
    semantic_subject = {
        "facility_kind": "pedestrian-crossing",
        "control_kind": (
            "signalized" if controlled else "unknown-unsignalized"
        ),
        "crossing_shape_xy": crossing_shape,
        "crossing_width_m": (
            crossing_lane.width if crossing_lane is not None else None
        ),
        "source_endpoint_shape_xy": tuple(
            _rounded_point(point)
            for point in (source_lane.shape if source_lane is not None else ())
        ),
        "destination_endpoint_shape_xy": tuple(
            _rounded_point(point)
            for point in (
                destination_lane.shape if destination_lane is not None else ()
            )
        ),
        "crossed_edge_signatures": crossed_edge_signatures,
    }
    review_subject_id = stable_id("review", semantic_subject)
    return PedestrianCrossingReviewSubject(
        review_subject_id=review_subject_id,
        **semantic_subject,
        position_xy=position_xy,
        physical_cell_id=physical_cell_id,
        boundary_port_ids=boundary_port_ids,
        permission_contract=dict(permission_contract),
        rejection_reasons=rejection_reasons,
        machine_question=(
            "Which physical cell and boundary ports own this pedestrian "
            "crossing, or should it remain outside the canonical junction model?"
        ),
        required_observations=(
            "physical crossing location",
            "crossed carriageway",
            "adjacent walkingarea continuity",
            "junction ownership",
            "traffic-control evidence",
        ),
    )
