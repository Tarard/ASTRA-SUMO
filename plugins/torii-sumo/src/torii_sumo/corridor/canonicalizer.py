from __future__ import annotations

import hashlib
import math
from collections import defaultdict
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from pydantic import Field, model_validator

from .base import ContractModel, Sha256, StableToken
from .enums import TrafficSide
from .ids import (
    make_approach_id,
    make_boundary_port_id,
    make_lane_role_id,
    make_movement_id,
    make_physical_cell_id,
    make_signal_group_id,
    require_stable_id,
    stable_digest,
    stable_id,
)
from .netxml import RawConnection, RawEdge, RawLane, RawNetwork, parse_net_xml_file
from .pedestrian_crossings import (
    PedestrianFacilityOwner,
    infer_pedestrian_facility_owners,
    model_pedestrian_crossing,
)
from .review import build_pedestrian_coverage_gap


class CanonicalEntity(ContractModel):
    kind: str
    stable_entity_id: StableToken
    semantic_signature: StableToken
    owner_physical_cell_ids: tuple[StableToken, ...] = ()
    boundary_port_ids: tuple[StableToken, ...] = ()
    payload: dict[str, Any]

    @model_validator(mode="after")
    def validate_entity(self) -> CanonicalEntity:
        require_stable_id(self.stable_entity_id)
        require_stable_id(self.semantic_signature, kind="signature")
        for cell_id in self.owner_physical_cell_ids:
            require_stable_id(cell_id, kind="cell")
        for port_id in self.boundary_port_ids:
            require_stable_id(port_id, kind="port")
        return self


class CanonicalNetworkSnapshot(ContractModel):
    schema_id: str = "torii.corridor.canonical-network/v1"
    traffic_side: TrafficSide
    source_sha256: Sha256 | None = None
    entities: tuple[CanonicalEntity, ...]
    raw_id_maps: dict[str, dict[str, StableToken]] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_snapshot(self) -> CanonicalNetworkSnapshot:
        keys = [(entity.kind, entity.stable_entity_id) for entity in self.entities]
        if len(keys) != len(set(keys)):
            raise ValueError("Canonical entity kind/ID pairs must be unique.")
        if self.traffic_side is TrafficSide.UNKNOWN:
            raise ValueError("Canonicalization requires an explicit traffic side.")
        return self

    def entity_index(self) -> dict[tuple[str, str], CanonicalEntity]:
        return {(entity.kind, entity.stable_entity_id): entity for entity in self.entities}


def canonicalize_net_xml_file(
    path: Path,
    *,
    traffic_side: TrafficSide,
) -> CanonicalNetworkSnapshot:
    source = path.resolve()
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    return canonicalize_raw_network(
        parse_net_xml_file(source),
        traffic_side=traffic_side,
        source_sha256=digest,
    )


def canonicalize_raw_network(
    network: RawNetwork,
    *,
    traffic_side: TrafficSide,
    source_sha256: str | None = None,
) -> CanonicalNetworkSnapshot:
    if traffic_side is TrafficSide.UNKNOWN:
        raise ValueError("Canonicalization is blocked until traffic side is known.")
    network_traffic_side = TrafficSide.LEFT if network.lefthand else TrafficSide.RIGHT
    if traffic_side is not network_traffic_side:
        raise ValueError(
            "Canonicalization traffic side contradicts the SUMO network: "
            f"requested={traffic_side.value}, network={network_traffic_side.value}."
        )

    outgoing_connections: dict[tuple[str, int], list[RawConnection]] = defaultdict(list)
    for connection in network.connections:
        if connection.from_lane is not None:
            outgoing_connections[(connection.from_edge, connection.from_lane)].append(connection)
    request_index_by_connection = _junction_request_indices(
        network,
        outgoing_connections=outgoing_connections,
    )
    maximum_internal_hops = max(
        1,
        sum(len(edge.lanes) for edge in network.edges.values() if not edge.external) + 1,
    )

    entities: dict[tuple[str, str], CanonicalEntity] = {}
    junction_cell_ids: dict[str, str] = {}
    port_ids_by_edge_flow: dict[tuple[str, str, str], str] = {}
    approach_ids_by_edge_flow: dict[tuple[str, str, str], str] = {}
    lane_role_ids: dict[tuple[str, str, str, int], str] = {}
    raw_lane_role_ids: dict[tuple[str, str], str] = {}
    port_descriptors: dict[tuple[str, str, str], dict[str, Any]] = {}

    for junction_id, junction in sorted(network.junctions.items()):
        incident = _incident_external_edges(network, junction_id)
        if not _junction_requires_physical_cell(
            network,
            junction_id=junction_id,
            incident=incident,
        ):
            continue
        for edge, flow in incident:
            descriptor = _port_descriptor(
                edge,
                flow=flow,
                traffic_side=traffic_side,
                outgoing_connections=outgoing_connections,
                network=network,
            )
            key = (junction_id, edge.edge_id, flow)
            port_descriptors[key] = descriptor
            port_ids_by_edge_flow[key] = descriptor["port_id"]
        cell_id = make_physical_cell_id(
            boundary_port_ids=tuple(
                port_ids_by_edge_flow[(junction_id, edge.edge_id, flow)] for edge, flow in incident
            ),
            grade_separation_signature=_grade_separation_signature(incident),
        )
        junction_cell_ids[junction_id] = cell_id

        for edge, flow in incident:
            key = (junction_id, edge.edge_id, flow)
            port_id = port_ids_by_edge_flow[key]
            approach_id = make_approach_id(
                physical_cell_id=cell_id,
                boundary_port_id=port_id,
                flow=flow,
            )
            approach_ids_by_edge_flow[key] = approach_id
            lane_ids_for_port: list[str] = []
            for lane in edge.lanes:
                ordinal_from_curb = _ordinal_from_curb(
                    lane.ordinal,
                    lane_count=len(edge.lanes),
                    traffic_side=traffic_side,
                )
                role = _lane_role(
                    edge,
                    lane,
                    flow=flow,
                    outgoing_connections=outgoing_connections,
                    network=network,
                )
                lane_role_id = make_lane_role_id(
                    approach_id=approach_id,
                    ordinal_from_curb=ordinal_from_curb,
                    role=role,
                    modes=_permission_tokens(lane),
                    traffic_side=traffic_side.value,
                )
                lane_role_ids[(junction_id, edge.edge_id, flow, lane.ordinal)] = lane_role_id
                raw_lane_role_ids[(junction_id, lane.lane_id)] = lane_role_id
                lane_ids_for_port.append(lane_role_id)
                _put_entity(
                    entities,
                    kind="lane_role",
                    entity_id=lane_role_id,
                    owner_cell_ids=(cell_id,),
                    boundary_port_ids=(port_id,),
                    payload={
                        "approach_id": approach_id,
                        "boundary_port_id": port_id,
                        "flow": flow,
                        "ordinal_from_curb": ordinal_from_curb,
                        "role": role,
                        "permissions": _lane_permissions(lane),
                        "speed_mps": lane.speed,
                        "length_m": lane.length,
                        "width_m": lane.width,
                        "shape_xy": _rounded_shape(lane.shape),
                    },
                )
            descriptor = port_descriptors[key]
            _put_entity(
                entities,
                kind="boundary_port",
                entity_id=port_id,
                owner_cell_ids=(cell_id,),
                boundary_port_ids=(port_id,),
                payload={
                    "source_anchor_refs": descriptor["source_anchor_refs"],
                    "source_geometry_sha256": descriptor["source_geometry_sha256"],
                    "flow": flow,
                    "center_xy": descriptor["center_xy"],
                    "tangent_xy": descriptor["tangent_xy"],
                    "normal_xy": descriptor["normal_xy"],
                    "lane_role_ids": lane_ids_for_port,
                    "lane_widths_m": [lane.width if lane.width is not None else 3.2 for lane in edge.lanes],
                    "traffic_side": traffic_side.value,
                    "edge_semantics": {
                        "type": edge.edge_type,
                        "priority": edge.priority,
                        "name": edge.name,
                        "params": edge.params,
                    },
                },
            )
            _put_entity(
                entities,
                kind="approach",
                entity_id=approach_id,
                owner_cell_ids=(cell_id,),
                boundary_port_ids=(port_id,),
                payload={
                    "physical_cell_id": cell_id,
                    "boundary_port_id": port_id,
                    "flow": flow,
                    "lane_role_ids": lane_ids_for_port,
                },
            )

        request_payload = [
            {
                "index": request.index,
                "response": request.response,
                "foes": request.foes,
                "cont": request.cont,
            }
            for request in junction.requests
        ]
        _put_entity(
            entities,
            kind="physical_cell",
            entity_id=cell_id,
            owner_cell_ids=(cell_id,),
            boundary_port_ids=tuple(
                sorted(port_ids_by_edge_flow[(junction_id, edge.edge_id, flow)] for edge, flow in incident)
            ),
            payload={
                "junction_type": junction.junction_type,
                "position_xy": _rounded_point((junction.x, junction.y)),
                "boundary_port_ids": sorted(
                    port_ids_by_edge_flow[(junction_id, edge.edge_id, flow)] for edge, flow in incident
                ),
                "requests": request_payload,
            },
        )
        request_id = stable_id("request", {"physical_cell_id": cell_id})
        _put_entity(
            entities,
            kind="request_foes",
            entity_id=request_id,
            owner_cell_ids=(cell_id,),
            payload={
                "physical_cell_id": cell_id,
                "request_rows": request_payload,
            },
        )

    pedestrian_facility_owners = infer_pedestrian_facility_owners(
        network,
        junction_cell_ids=junction_cell_ids,
    )
    connection_movement_ids: dict[str, str] = {}
    movement_variants: dict[str, list[dict[str, Any]]] = defaultdict(list)
    movement_owners: dict[str, tuple[str, tuple[str, ...]]] = {}
    pedestrian_rejection_reasons: dict[str, tuple[str, ...]] = {}
    pedestrian_review_subjects: dict[str, dict[str, Any]] = {}
    pedestrian_coverage_gaps: dict[str, dict[str, Any]] = {}
    modeled_pedestrian_crossing_edges: dict[str, set[str]] = defaultdict(set)
    modeled_controlled_pedestrian_movement_ids: dict[str, set[str]] = (
        defaultdict(set)
    )
    modeled_uncontrolled_pedestrian_movement_ids: dict[str, set[str]] = (
        defaultdict(set)
    )
    pedestrian_control_binding_indices: dict[
        tuple[str, str],
        set[int],
    ] = defaultdict(set)
    pedestrian_control_binding_raw_controllers: dict[
        tuple[str, str],
        set[str],
    ] = defaultdict(set)
    pedestrian_control_binding_states: dict[
        tuple[str, str],
        set[str],
    ] = defaultdict(set)
    pedestrian_control_binding_evidence_refs: dict[
        tuple[str, str],
        set[str],
    ] = defaultdict(set)
    pedestrian_control_binding_link_index2: dict[
        tuple[str, str],
        bool,
    ] = defaultdict(bool)
    for connection in network.connections:
        source_edge = network.edges.get(connection.from_edge)
        target_edge = network.edges.get(connection.to_edge)
        if (
            source_edge is None
            or target_edge is None
            or not source_edge.external
            or not target_edge.external
            or source_edge.to_junction != target_edge.from_junction
        ):
            attempt = model_pedestrian_crossing(
                connection,
                network=network,
                facility_owners=pedestrian_facility_owners,
                port_ids_by_edge_flow=port_ids_by_edge_flow,
                outgoing_connections=outgoing_connections,
            )
            if not attempt.is_candidate:
                continue
            if attempt.model is None:
                pedestrian_rejection_reasons[str(connection.connection_index)] = attempt.rejection_reasons
                if attempt.review_subject is not None:
                    review_subject = attempt.review_subject.model_dump(
                        mode="python"
                    )
                    review_subject_id = str(
                        attempt.review_subject.review_subject_id
                    )
                    existing = pedestrian_review_subjects.get(
                        review_subject_id
                    )
                    if existing is not None and existing != review_subject:
                        raise ValueError(
                            "Pedestrian review-subject identity collision: "
                            f"{review_subject_id}"
                        )
                    pedestrian_review_subjects[review_subject_id] = (
                        review_subject
                    )
                    coverage_gap = build_pedestrian_coverage_gap(
                        attempt.review_subject
                    )
                    coverage_gap_payload = coverage_gap.model_dump(
                        mode="python"
                    )
                    coverage_gap_id = str(coverage_gap.coverage_gap_id)
                    existing_gap = pedestrian_coverage_gaps.get(
                        coverage_gap_id
                    )
                    if (
                        existing_gap is not None
                        and existing_gap != coverage_gap_payload
                    ):
                        raise ValueError(
                            "Pedestrian coverage-gap identity collision: "
                            f"{coverage_gap_id}"
                        )
                    pedestrian_coverage_gaps[coverage_gap_id] = (
                        coverage_gap_payload
                    )
                continue
            model = attempt.model
            for role_id, role_payload in model.lane_role_payloads:
                key = ("lane_role", role_id)
                existing = entities.get(key)
                if existing is None:
                    _put_entity(
                        entities,
                        kind="lane_role",
                        entity_id=role_id,
                        owner_cell_ids=(model.physical_cell_id,),
                        boundary_port_ids=model.boundary_port_ids,
                        payload=role_payload,
                    )
                elif (
                    existing.payload != role_payload
                    or existing.owner_physical_cell_ids != (model.physical_cell_id,)
                    or existing.boundary_port_ids != model.boundary_port_ids
                ):
                    raise ValueError(f"Pedestrian crossing lane-role identity collision: {role_id}")
            connection_movement_ids[str(connection.connection_index)] = model.movement_id
            movement_variants[model.movement_id].append({**model.movement_variant, "path": model.path})
            movement_owners[model.movement_id] = (
                model.physical_cell_id,
                model.boundary_port_ids,
            )
            control_kind = (
                "signalized"
                if model.controlled
                else "unknown-unsignalized"
            )
            source_link_indices = tuple(
                index
                for index in (connection.link_index, connection.link_index2)
                if index is not None
            )
            pedestrian_control_binding_indices[
                (model.movement_id, control_kind)
            ].update(source_link_indices)
            _record_pedestrian_control_binding_evidence(
                connection=connection,
                movement_id=model.movement_id,
                control_kind=control_kind,
                raw_controllers=pedestrian_control_binding_raw_controllers,
                states=pedestrian_control_binding_states,
                evidence_refs=pedestrian_control_binding_evidence_refs,
                link_index2=pedestrian_control_binding_link_index2,
            )
            modeled_pedestrian_crossing_edges[model.physical_cell_id].add(model.crossing_edge_id)
            target = (
                modeled_controlled_pedestrian_movement_ids
                if model.controlled
                else modeled_uncontrolled_pedestrian_movement_ids
            )
            target[model.physical_cell_id].add(model.movement_id)
            continue
        junction_id = source_edge.to_junction
        cell_id = junction_cell_ids.get(junction_id)
        source_port_id = port_ids_by_edge_flow.get((junction_id, source_edge.edge_id, "incoming"))
        target_port_id = port_ids_by_edge_flow.get((junction_id, target_edge.edge_id, "outgoing"))
        source_role_id = lane_role_ids.get((junction_id, source_edge.edge_id, "incoming", connection.from_lane))
        target_role_id = lane_role_ids.get((junction_id, target_edge.edge_id, "outgoing", connection.to_lane))
        if not all((cell_id, source_port_id, target_port_id, source_role_id, target_role_id)):
            continue
        source_lane = _lane_by_ordinal(source_edge, connection.from_lane)
        target_lane = _lane_by_ordinal(target_edge, connection.to_lane)
        mode = _movement_mode(source_lane, target_lane)
        turn_class = _turn_class(connection.direction)
        movement_id = make_movement_id(
            physical_cell_id=cell_id,
            source_boundary_port_id=source_port_id,
            source_lane_role_id=source_role_id,
            destination_boundary_port_id=target_port_id,
            destination_lane_role_id=target_role_id,
            mode=mode,
            turn_class=turn_class,
        )
        connection_movement_ids[str(connection.connection_index)] = movement_id
        path, traversed_internal_edge_ids = _trace_internal_semantics(
            connection,
            network,
            outgoing_connections=outgoing_connections,
            maximum_hops=maximum_internal_hops,
        )
        mode_classes = _movement_mode_classes(
            source_lane,
            target_lane,
            path=path,
        )
        path_signature = stable_id(
            "signature",
            {"movement_id": movement_id, "path": path},
        )
        movement_variant = {
                "source_boundary_port_id": source_port_id,
                "source_lane_role_id": source_role_id,
                "destination_boundary_port_id": target_port_id,
                "destination_lane_role_id": target_role_id,
                "mode": mode,
                "mode_classes": mode_classes,
                "turn_class": turn_class,
                "state": connection.state,
                "path_signature": path_signature,
                "path": path,
            }
        request_index = request_index_by_connection.get(
            connection.connection_index
        )
        if request_index is not None:
            movement_variant["request_index"] = request_index
        movement_variants[movement_id].append(movement_variant)
        movement_owners[movement_id] = (
            cell_id,
            (source_port_id, target_port_id),
        )
        crossing_edge_ids = {
            edge_id
            for edge_id in traversed_internal_edge_ids
            if (edge := network.edges.get(edge_id)) is not None
            and edge.function == "crossing"
        }
        if crossing_edge_ids and "pedestrian" in mode_classes:
            modeled_pedestrian_crossing_edges[cell_id].update(
                crossing_edge_ids
            )
            control_kind = (
                "signalized"
                if connection.controller_id
                else "unknown-unsignalized"
            )
            source_link_indices = {
                index
                for index in (
                    connection.link_index,
                    connection.link_index2,
                )
                if index is not None
            }
            pedestrian_control_binding_indices[
                (movement_id, control_kind)
            ].update(source_link_indices)
            _record_pedestrian_control_binding_evidence(
                connection=connection,
                movement_id=movement_id,
                control_kind=control_kind,
                raw_controllers=pedestrian_control_binding_raw_controllers,
                states=pedestrian_control_binding_states,
                evidence_refs=pedestrian_control_binding_evidence_refs,
                link_index2=pedestrian_control_binding_link_index2,
            )
            target = (
                modeled_controlled_pedestrian_movement_ids
                if connection.controller_id
                else modeled_uncontrolled_pedestrian_movement_ids
            )
            target[cell_id].add(movement_id)

    for movement_id, variants in sorted(movement_variants.items()):
        cell_id, boundary_port_ids = movement_owners[movement_id]
        normalized_variants = sorted(variants, key=lambda value: stable_digest("movement-variant", value))
        path_id = stable_id("path", {"movement_id": movement_id})
        _put_entity(
            entities,
            kind="internal_path",
            entity_id=path_id,
            owner_cell_ids=(cell_id,),
            boundary_port_ids=boundary_port_ids,
            payload={
                "movement_id": movement_id,
                "multiplicity": len(normalized_variants),
                "path_variants": [
                    {
                        "path_signature": variant["path_signature"],
                        "path": variant["path"],
                    }
                    for variant in normalized_variants
                ],
            },
        )
        _put_entity(
            entities,
            kind="movement",
            entity_id=movement_id,
            owner_cell_ids=(cell_id,),
            boundary_port_ids=boundary_port_ids,
            payload={
                "multiplicity": len(normalized_variants),
                "variants": [
                    {
                        key: value
                        for key, value in variant.items()
                        if key != "path"
                    }
                    for variant in normalized_variants
                ],
                "internal_path_id": path_id,
            },
        )

    for (
        movement_id,
        control_kind,
    ), source_link_indices in sorted(
        pedestrian_control_binding_indices.items()
    ):
        cell_id, boundary_port_ids = movement_owners[movement_id]
        sorted_indices = tuple(sorted(source_link_indices))
        binding_key = (movement_id, control_kind)
        raw_controller_ids = tuple(
            sorted(
                pedestrian_control_binding_raw_controllers.get(
                    binding_key,
                    (),
                )
            )
        )
        program_sources = tuple(
            sorted(
                {
                    (
                        "embedded-net"
                        if controller_id in network.tls_programs
                        else "unresolved"
                    )
                    for controller_id in raw_controller_ids
                }
                or {"not-applicable"}
            )
        )
        owner_junction_types = tuple(
            sorted(
                {
                    junction.junction_type
                    for junction_id, junction in network.junctions.items()
                    if junction_cell_ids.get(junction_id) == cell_id
                }
            )
        )
        _put_entity(
            entities,
            kind="pedestrian_control_binding",
            entity_id=stable_id(
                "binding",
                {
                    "movement_id": movement_id,
                    "control_kind": control_kind,
                },
            ),
            owner_cell_ids=(cell_id,),
            boundary_port_ids=boundary_port_ids,
            payload={
                "movement_id": movement_id,
                "control_kind": control_kind,
                "source_link_indices": sorted_indices,
                "multiple_source_indices": len(sorted_indices) > 1,
                "raw_controller_ids": raw_controller_ids,
                "owner_junction_types": owner_junction_types,
                "program_sources": program_sources,
                "source_connection_states": tuple(
                    sorted(
                        pedestrian_control_binding_states.get(
                            binding_key,
                            (),
                        )
                    )
                ),
                "link_index2_present": (
                    pedestrian_control_binding_link_index2.get(
                        binding_key,
                        False,
                    )
                ),
                "evidence_refs": tuple(
                    sorted(
                        pedestrian_control_binding_evidence_refs.get(
                            binding_key,
                            (),
                        )
                    )
                ),
                "classification_status": "unclassified",
            },
        )

    tls_controller_ids: dict[str, str] = {}
    for raw_controller_id, programs in sorted(network.tls_programs.items()):
        controlled = [
            connection
            for connection in network.connections
            if connection.controller_id == raw_controller_id
            and str(connection.connection_index) in connection_movement_ids
        ]
        if not controlled:
            continue
        movement_ids = sorted({connection_movement_ids[str(connection.connection_index)] for connection in controlled})
        owner_cells = sorted(
            {
                cell_id
                for movement_id in movement_ids
                for cell_id in entities[("movement", movement_id)].owner_physical_cell_ids
            }
        )
        controller_id = stable_id(
            "controller",
            {
                "owner_physical_cell_ids": owner_cells,
                "controlled_movement_ids": movement_ids,
                "program_ids": sorted(program.program_id for program in programs),
            },
        )
        tls_controller_ids[raw_controller_id] = controller_id
        movements_by_index: dict[int, set[str]] = defaultdict(set)
        for connection in controlled:
            movement_id = connection_movement_ids[str(connection.connection_index)]
            if connection.link_index is not None:
                movements_by_index[connection.link_index].add(movement_id)
            if connection.link_index2 is not None:
                movements_by_index[connection.link_index2].add(movement_id)
        indices_by_movement_set: dict[tuple[str, ...], list[int]] = defaultdict(list)
        for link_index, ids in movements_by_index.items():
            indices_by_movement_set[tuple(sorted(ids))].append(link_index)
        signal_groups: list[tuple[str, tuple[int, ...], tuple[str, ...]]] = []
        for grouped_movement_ids, indices in sorted(indices_by_movement_set.items()):
            signal_group_id = make_signal_group_id(
                controller_scope_id=controller_id,
                movement_ids=grouped_movement_ids,
            )
            sorted_indices = tuple(sorted(indices))
            signal_groups.append((signal_group_id, sorted_indices, grouped_movement_ids))
            _put_entity(
                entities,
                kind="signal_group",
                entity_id=signal_group_id,
                owner_cell_ids=tuple(owner_cells),
                payload={
                    "controller_id": controller_id,
                    "movement_ids": grouped_movement_ids,
                    "source_link_indices": sorted_indices,
                    "multiple_source_indices": len(sorted_indices) > 1,
                },
            )
        program_entity_ids: list[str] = []
        for program in programs:
            program_id = stable_id(
                "program",
                {
                    "controller_id": controller_id,
                    "source_program_id": program.program_id,
                    "controller_type": program.controller_type,
                },
            )
            phases = []
            for phase in program.phases:
                group_states = []
                for signal_group_id, indices, _ in signal_groups:
                    states = tuple(phase.state[index] if 0 <= index < len(phase.state) else "?" for index in indices)
                    group_states.append(
                        {
                            "signal_group_id": signal_group_id,
                            "states": states,
                            "consistent": len(set(states)) <= 1,
                        }
                    )
                phases.append(
                    {
                        "duration": phase.duration,
                        "minimum_duration": phase.minimum_duration,
                        "maximum_duration": phase.maximum_duration,
                        "next_phases": phase.next_phases,
                        "name": phase.name,
                        "group_states": group_states,
                    }
                )
            _put_entity(
                entities,
                kind="controller_program",
                entity_id=program_id,
                owner_cell_ids=tuple(owner_cells),
                payload={
                    "controller_id": controller_id,
                    "controller_type": program.controller_type,
                    "offset": program.offset,
                    "signal_group_ids": [group[0] for group in signal_groups],
                    "phases": phases,
                },
            )
            program_entity_ids.append(program_id)
        _put_entity(
            entities,
            kind="controller",
            entity_id=controller_id,
            owner_cell_ids=tuple(owner_cells),
            payload={
                "owner_physical_cell_ids": owner_cells,
                "controlled_movement_ids": movement_ids,
                "signal_group_ids": [group[0] for group in signal_groups],
                "program_ids": program_entity_ids,
            },
        )

    for coverage_gap_id, coverage_gap in sorted(
        pedestrian_coverage_gaps.items()
    ):
        _put_entity(
            entities,
            kind="pedestrian_coverage_gap",
            entity_id=coverage_gap_id,
            owner_cell_ids=tuple(
                coverage_gap["owner_candidate_physical_cell_ids"]
            ),
            boundary_port_ids=tuple(
                coverage_gap["boundary_port_candidate_ids"]
            ),
            payload=coverage_gap,
        )

    _add_safety_coverage_entities(
        entities,
        network=network,
        traffic_side=traffic_side,
        junction_cell_ids=junction_cell_ids,
        connection_movement_ids=connection_movement_ids,
        movement_variants=movement_variants,
        movement_owners=movement_owners,
        pedestrian_facility_owners=pedestrian_facility_owners,
        modeled_pedestrian_crossing_edges=modeled_pedestrian_crossing_edges,
        modeled_controlled_pedestrian_movement_ids=(
            modeled_controlled_pedestrian_movement_ids
        ),
        modeled_uncontrolled_pedestrian_movement_ids=(
            modeled_uncontrolled_pedestrian_movement_ids
        ),
        pedestrian_rejection_reasons=pedestrian_rejection_reasons,
        pedestrian_review_subjects=pedestrian_review_subjects,
    )

    return CanonicalNetworkSnapshot(
        traffic_side=traffic_side,
        source_sha256=source_sha256,
        entities=tuple(
            entity
            for _, entity in sorted(
                entities.items(),
                key=lambda item: item[0],
            )
        ),
        raw_id_maps={
            "junction_to_physical_cell": junction_cell_ids,
            "connection_index_to_movement": connection_movement_ids,
            "tls_to_controller": tls_controller_ids,
            "edge_flow_to_boundary_port": {"|".join(key): value for key, value in port_ids_by_edge_flow.items()},
            "edge_flow_lane_to_lane_role": {
                "|".join((*key[:3], str(key[3]))): value for key, value in lane_role_ids.items()
            },
            "junction_lane_to_lane_role": {"|".join(key): value for key, value in raw_lane_role_ids.items()},
        },
    )


def _put_entity(
    entities: dict[tuple[str, str], CanonicalEntity],
    *,
    kind: str,
    entity_id: str,
    payload: dict[str, Any],
    owner_cell_ids: tuple[str, ...] = (),
    boundary_port_ids: tuple[str, ...] = (),
) -> None:
    entity = CanonicalEntity(
        kind=kind,
        stable_entity_id=entity_id,
        semantic_signature=stable_id(
            "signature",
            {"kind": kind, "payload": payload},
        ),
        owner_physical_cell_ids=tuple(sorted(set(owner_cell_ids))),
        boundary_port_ids=tuple(sorted(set(boundary_port_ids))),
        payload=payload,
    )
    key = (kind, entity_id)
    if key in entities:
        raise ValueError(f"Canonical entity collision: {kind}/{entity_id}")
    entities[key] = entity


def _record_pedestrian_control_binding_evidence(
    *,
    connection: RawConnection,
    movement_id: str,
    control_kind: str,
    raw_controllers: dict[tuple[str, str], set[str]],
    states: dict[tuple[str, str], set[str]],
    evidence_refs: dict[tuple[str, str], set[str]],
    link_index2: dict[tuple[str, str], bool],
) -> None:
    key = (movement_id, control_kind)
    if connection.controller_id:
        raw_controllers[key].add(connection.controller_id)
    if connection.state:
        states[key].add(connection.state)
    link_index2[key] = (
        link_index2[key] or connection.link_index2 is not None
    )
    evidence_refs[key].add(
        stable_id(
            "evidence",
            {
                "movement_id": movement_id,
                "from_edge": connection.from_edge,
                "to_edge": connection.to_edge,
                "from_lane": connection.from_lane,
                "to_lane": connection.to_lane,
                "direction": connection.direction,
                "state": connection.state,
                "controller_id": connection.controller_id,
                "link_index": connection.link_index,
                "link_index2": connection.link_index2,
            },
        )
    )


def _incident_external_edges(
    network: RawNetwork,
    junction_id: str,
) -> list[tuple[RawEdge, str]]:
    incident: list[tuple[RawEdge, str]] = []
    for edge in network.edges.values():
        if not edge.external:
            continue
        if edge.to_junction == junction_id:
            incident.append((edge, "incoming"))
        if edge.from_junction == junction_id:
            incident.append((edge, "outgoing"))
    return sorted(incident, key=lambda item: (item[0].edge_id, item[1]))


def _junction_requires_physical_cell(
    network: RawNetwork,
    *,
    junction_id: str,
    incident: list[tuple[RawEdge, str]],
) -> bool:
    if not incident:
        return False
    junction = network.junctions[junction_id]
    if junction.junction_type == "dead_end":
        return False
    if junction.requests or junction.junction_type in {
        "traffic_light",
        "traffic_light_unregulated",
        "rail_crossing",
        "rail_signal",
    }:
        return True
    incoming_edge_ids = {edge.edge_id for edge, flow in incident if flow == "incoming"}
    outgoing_edge_ids = {edge.edge_id for edge, flow in incident if flow == "outgoing"}
    return any(
        connection.from_edge in incoming_edge_ids and connection.to_edge in outgoing_edge_ids
        for connection in network.connections
    )


def _junction_request_indices(
    network: RawNetwork,
    *,
    outgoing_connections: dict[tuple[str, int], list[RawConnection]],
) -> dict[int, int]:
    """Bind raw connections to junction request rows without using TLS indices."""

    result: dict[int, int] = {}
    for junction in network.junctions.values():
        if not junction.requests:
            continue
        ordered_connections: list[RawConnection] = []
        for lane_id in junction.incoming_lane_ids:
            lane = network.lanes.get(lane_id)
            edge_id = network.lane_edge_ids.get(lane_id)
            if lane is None or edge_id is None:
                ordered_connections = []
                break
            ordered_connections.extend(
                connection
                for connection in outgoing_connections.get(
                    (edge_id, lane.ordinal),
                    (),
                )
                if _counts_as_junction_request(connection, network=network)
            )
        request_indices = {
            request.index
            for request in junction.requests
            if request.index is not None
        }
        if (
            len(ordered_connections) != len(junction.requests)
            or request_indices != set(range(len(junction.requests)))
        ):
            continue
        for request_index, connection in enumerate(ordered_connections):
            result[connection.connection_index] = request_index
    return result


def _counts_as_junction_request(
    connection: RawConnection,
    *,
    network: RawNetwork,
) -> bool:
    source_edge = network.edges.get(connection.from_edge)
    target_edge = network.edges.get(connection.to_edge)
    source_function = source_edge.function if source_edge is not None else ""
    target_function = target_edge.function if target_edge is not None else ""
    return not (
        target_function == "walkingarea"
        or source_function == "walkingarea"
        and target_function != "crossing"
    )


def _port_descriptor(
    edge: RawEdge,
    *,
    flow: str,
    traffic_side: TrafficSide,
    outgoing_connections: dict[tuple[str, int], list[RawConnection]],
    network: RawNetwork,
) -> dict[str, Any]:
    lane_semantics = [
        {
            "ordinal_from_curb": _ordinal_from_curb(
                lane.ordinal,
                lane_count=len(edge.lanes),
                traffic_side=traffic_side,
            ),
            "role": _lane_role(
                edge,
                lane,
                flow=flow,
                outgoing_connections=outgoing_connections,
                network=network,
            ),
            "permissions": _lane_permissions(lane),
            "width_m": lane.width,
        }
        for lane in edge.lanes
    ]
    source_anchor_refs = _source_anchor_refs(edge)
    far_geometry = [_far_shape(lane.shape, flow=flow) for lane in edge.lanes]
    source_geometry_sha256 = stable_digest(
        "boundary-port-source-geometry/v1",
        far_geometry,
    )
    port_id = make_boundary_port_id(
        source_anchor_refs=source_anchor_refs,
        source_geometry_sha256=source_geometry_sha256,
        lane_semantic_keys=lane_semantics,
        traffic_side=traffic_side.value,
    )
    far_points = [lane.shape[0] if flow == "incoming" else lane.shape[-1] for lane in edge.lanes if lane.shape]
    center = _mean_point(far_points)
    tangent = _mean_tangent(edge.lanes, flow=flow)
    normal = (-tangent[1], tangent[0])
    return {
        "port_id": port_id,
        "source_anchor_refs": source_anchor_refs,
        "source_geometry_sha256": source_geometry_sha256,
        "center_xy": center,
        "tangent_xy": tangent,
        "normal_xy": normal,
    }


def _source_anchor_refs(edge: RawEdge) -> tuple[str, ...]:
    origin = edge.params.get("origId", "").strip()
    if origin:
        return tuple(sorted({token for token in origin.replace(",", " ").split() if token}))
    return (f"external-edge/{edge.edge_id}",)


def _far_shape(
    shape: tuple[tuple[float, float], ...],
    *,
    flow: str,
) -> tuple[tuple[float, float], ...]:
    if flow == "incoming":
        return _rounded_shape(shape[: min(2, len(shape))])
    return _rounded_shape(shape[max(0, len(shape) - 2) :])


def _mean_point(points: list[tuple[float, float]]) -> tuple[float, float]:
    if not points:
        return (0.0, 0.0)
    return (
        round(sum(point[0] for point in points) / len(points), 6),
        round(sum(point[1] for point in points) / len(points), 6),
    )


def _mean_tangent(lanes: tuple[RawLane, ...], *, flow: str) -> tuple[float, float]:
    vectors: list[tuple[float, float]] = []
    for lane in lanes:
        if len(lane.shape) < 2:
            continue
        if flow == "incoming":
            start, end = lane.shape[0], lane.shape[1]
        else:
            start, end = lane.shape[-2], lane.shape[-1]
        dx, dy = end[0] - start[0], end[1] - start[1]
        length = math.hypot(dx, dy)
        if length:
            vectors.append((dx / length, dy / length))
    if not vectors:
        return (1.0, 0.0)
    dx = sum(vector[0] for vector in vectors)
    dy = sum(vector[1] for vector in vectors)
    length = math.hypot(dx, dy)
    if not length:
        return (1.0, 0.0)
    return (round(dx / length, 6), round(dy / length, 6))


def _grade_separation_signature(
    incident: list[tuple[RawEdge, str]],
) -> dict[str, Any]:
    return {
        "layers": sorted({edge.params.get("layer", "0") for edge, _ in incident}),
        "bridge": any(edge.params.get("bridge", "") not in {"", "no", "false", "0"} for edge, _ in incident),
        "tunnel": any(edge.params.get("tunnel", "") not in {"", "no", "false", "0"} for edge, _ in incident),
    }


def _ordinal_from_curb(
    ordinal: int,
    *,
    lane_count: int,
    traffic_side: TrafficSide,
) -> int:
    if traffic_side is TrafficSide.RIGHT:
        return ordinal
    return lane_count - ordinal - 1


def _lane_role(
    edge: RawEdge,
    lane: RawLane,
    *,
    flow: str,
    outgoing_connections: dict[tuple[str, int], list[RawConnection]],
    network: RawNetwork,
) -> str:
    if flow == "outgoing":
        return "receiving"
    turns = sorted(
        {
            _turn_class(connection.direction)
            for connection in outgoing_connections.get((edge.edge_id, lane.ordinal), [])
            if network.edges.get(connection.to_edge) is not None and network.edges[connection.to_edge].external
        }
    )
    return "+".join(turns) if turns else "unconnected"


def _lane_permissions(lane: RawLane | None) -> dict[str, tuple[str, ...]]:
    if lane is None:
        return {"allow": (), "disallow": ()}
    return {"allow": lane.allow, "disallow": lane.disallow}


def _permission_tokens(lane: RawLane) -> tuple[str, ...]:
    if lane.allow:
        return tuple(f"allow:{mode}" for mode in lane.allow)
    if lane.disallow:
        return tuple(f"not:{mode}" for mode in lane.disallow)
    return ("allow:default",)


def _movement_mode(source_lane: RawLane | None, target_lane: RawLane | None) -> str:
    return stable_digest(
        "movement-permission/v1",
        {
            "source": _lane_permissions(source_lane),
            "target": _lane_permissions(target_lane),
        },
    )[:16]


def _movement_mode_classes(
    source_lane: RawLane | None,
    target_lane: RawLane | None,
    *,
    path: Mapping[str, Any] | None = None,
) -> tuple[str, ...]:
    permission_contract = path.get("permission_contract", {}) if isinstance(path, Mapping) else {}
    if permission_contract.get("coverage") == "complete":
        if permission_contract.get("status") == "fail":
            return ("incompatible",)

        def allows_mode(mode: str) -> bool:
            return _permission_contract_allows_mode(
                permission_contract,
                mode,
            )
    else:
        if source_lane is None or target_lane is None:
            return ("unspecified",)

        def allows_mode(mode: str) -> bool:
            return _lane_allows_mode(
                source_lane,
                mode,
            ) and _lane_allows_mode(target_lane, mode)

    road_modes = {
        "passenger",
        "private",
        "taxi",
        "hov",
        "delivery",
        "truck",
        "trailer",
        "bus",
        "coach",
        "emergency",
        "authority",
        "motorcycle",
        "moped",
    }
    rail_modes = {
        "tram",
        "rail_urban",
        "rail",
        "rail_electric",
        "rail_fast",
        "rail_slow",
    }
    if any(allows_mode(mode) for mode in road_modes):
        return ("road-motorized",)
    if any(allows_mode(mode) for mode in rail_modes):
        return ("rail",)
    if allows_mode("bicycle"):
        return ("bicycle",)
    if allows_mode("pedestrian"):
        return ("pedestrian",)
    return ("unspecified",)


def _lane_allows_mode(lane: RawLane, mode: str) -> bool:
    if lane.allow:
        return mode in lane.allow
    return mode not in lane.disallow


def _permission_contract_allows_mode(
    contract: Mapping[str, Any],
    mode: str,
) -> bool:
    if contract.get("status") != "pass":
        return False
    allowed = set(map(str, contract.get("allow", ())))
    disallowed = set(map(str, contract.get("disallow", ())))
    return mode not in disallowed if "*" in allowed else mode in allowed and mode not in disallowed


def _add_safety_coverage_entities(
    entities: dict[tuple[str, str], CanonicalEntity],
    *,
    network: RawNetwork,
    traffic_side: TrafficSide,
    junction_cell_ids: dict[str, str],
    connection_movement_ids: dict[str, str],
    movement_variants: dict[str, list[dict[str, Any]]],
    movement_owners: dict[str, tuple[str, tuple[str, ...]]],
    pedestrian_facility_owners: Mapping[str, PedestrianFacilityOwner],
    modeled_pedestrian_crossing_edges: Mapping[str, set[str]],
    modeled_controlled_pedestrian_movement_ids: Mapping[str, set[str]],
    modeled_uncontrolled_pedestrian_movement_ids: Mapping[str, set[str]],
    pedestrian_rejection_reasons: Mapping[str, tuple[str, ...]],
    pedestrian_review_subjects: Mapping[str, dict[str, Any]],
) -> None:
    movement_ids_by_cell: dict[str, set[str]] = defaultdict(set)
    movement_mode_counts_by_cell: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for movement_id, variants in movement_variants.items():
        cell_id = movement_owners[movement_id][0]
        movement_ids_by_cell[cell_id].add(movement_id)
        for variant in variants:
            for mode_class in variant.get("mode_classes", ()):
                movement_mode_counts_by_cell[cell_id][str(mode_class)] += 1

    controlled_by_cell: dict[str, list[RawConnection]] = defaultdict(list)
    unresolved_controlled: list[RawConnection] = []
    for connection in network.connections:
        if not connection.controller_id:
            continue
        movement_id = connection_movement_ids.get(str(connection.connection_index))
        if movement_id is not None:
            cell_id = movement_owners[movement_id][0]
        else:
            cell_id = _infer_connection_cell_id(
                connection,
                network=network,
                junction_cell_ids=junction_cell_ids,
            )
        if cell_id is None:
            unresolved_controlled.append(connection)
        else:
            controlled_by_cell[cell_id].append(connection)

    facility_counts_by_cell: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    unresolved_facilities: dict[str, int] = defaultdict(int)
    for edge in network.edges.values():
        if edge.function not in {"crossing", "walkingarea"}:
            continue
        owner = pedestrian_facility_owners.get(edge.edge_id)
        if owner is None:
            unresolved_facilities[edge.function] += 1
            continue
        facility_counts_by_cell[owner.physical_cell_id][edge.function] += 1

    review_subjects_by_cell: dict[str, list[dict[str, Any]]] = defaultdict(
        list
    )
    unresolved_review_subjects: list[dict[str, Any]] = []
    known_cell_ids = set(junction_cell_ids.values())
    for subject in pedestrian_review_subjects.values():
        cell_id = subject.get("physical_cell_id")
        if isinstance(cell_id, str) and cell_id in known_cell_ids:
            review_subjects_by_cell[cell_id].append(subject)
        else:
            unresolved_review_subjects.append(subject)

    for cell_id in sorted(set(junction_cell_ids.values())):
        controlled = controlled_by_cell.get(cell_id, [])
        unsupported = [
            _unsupported_controlled_connection_payload(
                connection,
                network,
                rejection_reasons=pedestrian_rejection_reasons.get(
                    str(connection.connection_index),
                    (),
                ),
            )
            for connection in controlled
            if str(connection.connection_index) not in connection_movement_ids
        ]
        crossing_edge_count = facility_counts_by_cell[cell_id]["crossing"]
        modeled_crossing_edge_count = len(modeled_pedestrian_crossing_edges.get(cell_id, set()))
        controlled_pedestrian_count = len(
            modeled_controlled_pedestrian_movement_ids.get(cell_id, set())
        )
        uncontrolled_pedestrian_count = len(
            modeled_uncontrolled_pedestrian_movement_ids.get(cell_id, set())
        )
        payload = {
            "canonical_movement_count": len(movement_ids_by_cell.get(cell_id, set())),
            "controlled_connection_count": len(controlled),
            "mapped_controlled_movement_count": sum(
                str(connection.connection_index) in connection_movement_ids for connection in controlled
            ),
            "unsupported_controlled_connection_count": len(unsupported),
            "unsupported_controlled_connections": sorted(
                unsupported,
                key=lambda value: stable_digest("unsupported-controlled-link", value),
            ),
            "crossing_edge_count": crossing_edge_count,
            "walkingarea_edge_count": facility_counts_by_cell[cell_id]["walkingarea"],
            "modeled_pedestrian_movement_count": (
                controlled_pedestrian_count + uncontrolled_pedestrian_count
            ),
            "modeled_controlled_pedestrian_movement_count": (
                controlled_pedestrian_count
            ),
            "modeled_uncontrolled_pedestrian_movement_count": (
                uncontrolled_pedestrian_count
            ),
            "modeled_unsignalized_pedestrian_movement_count": (
                uncontrolled_pedestrian_count
            ),
            "modeled_crossing_edge_count": modeled_crossing_edge_count,
            "unmodeled_crossing_edge_count": max(
                0,
                crossing_edge_count - modeled_crossing_edge_count,
            ),
            "unmodeled_pedestrian_crossings": sorted(
                review_subjects_by_cell.get(cell_id, ()),
                key=lambda value: str(value["review_subject_id"]),
            ),
            "link_index2_connection_count": sum(connection.link_index2 is not None for connection in controlled),
            "movement_mode_class_counts": dict(sorted(movement_mode_counts_by_cell[cell_id].items())),
            "ownership_status": "resolved",
        }
        _put_entity(
            entities,
            kind="safety_coverage",
            entity_id=stable_id(
                "coverage",
                {
                    "physical_cell_id": cell_id,
                    "traffic_side": traffic_side.value,
                },
            ),
            owner_cell_ids=(cell_id,),
            payload=payload,
        )

    if unresolved_controlled or unresolved_facilities:
        unresolved_payload = {
            "canonical_movement_count": 0,
            "controlled_connection_count": len(unresolved_controlled),
            "mapped_controlled_movement_count": 0,
            "unsupported_controlled_connection_count": len(unresolved_controlled),
            "unsupported_controlled_connections": sorted(
                (
                    _unsupported_controlled_connection_payload(
                        connection,
                        network,
                        rejection_reasons=pedestrian_rejection_reasons.get(
                            str(connection.connection_index),
                            (),
                        ),
                    )
                    for connection in unresolved_controlled
                ),
                key=lambda value: stable_digest("unsupported-controlled-link", value),
            ),
            "crossing_edge_count": unresolved_facilities["crossing"],
            "walkingarea_edge_count": unresolved_facilities["walkingarea"],
            "modeled_pedestrian_movement_count": 0,
            "modeled_controlled_pedestrian_movement_count": 0,
            "modeled_uncontrolled_pedestrian_movement_count": 0,
            "modeled_unsignalized_pedestrian_movement_count": 0,
            "modeled_crossing_edge_count": 0,
            "unmodeled_crossing_edge_count": unresolved_facilities["crossing"],
            "unmodeled_pedestrian_crossings": sorted(
                unresolved_review_subjects,
                key=lambda value: str(value["review_subject_id"]),
            ),
            "link_index2_connection_count": sum(
                connection.link_index2 is not None for connection in unresolved_controlled
            ),
            "movement_mode_class_counts": {},
            "ownership_status": "unresolved",
        }
        _put_entity(
            entities,
            kind="safety_coverage",
            entity_id=stable_id(
                "coverage",
                {
                    "physical_cell_id": "unresolved",
                    "traffic_side": traffic_side.value,
                },
            ),
            payload=unresolved_payload,
        )


def _infer_connection_cell_id(
    connection: RawConnection,
    *,
    network: RawNetwork,
    junction_cell_ids: dict[str, str],
) -> str | None:
    source_edge = network.edges.get(connection.from_edge)
    target_edge = network.edges.get(connection.to_edge)
    junction_candidates: set[str] = set()
    if source_edge is not None and source_edge.to_junction in junction_cell_ids:
        junction_candidates.add(source_edge.to_junction)
    if target_edge is not None and target_edge.from_junction in junction_cell_ids:
        junction_candidates.add(target_edge.from_junction)
    lane_ids = {lane.lane_id for edge in (source_edge, target_edge) if edge is not None for lane in edge.lanes}
    for junction_id, junction in network.junctions.items():
        if junction_id in junction_cell_ids and lane_ids & set(junction.internal_lane_ids):
            junction_candidates.add(junction_id)
    if len(junction_candidates) != 1:
        return None
    return junction_cell_ids[next(iter(junction_candidates))]


def _unsupported_controlled_connection_payload(
    connection: RawConnection,
    network: RawNetwork,
    *,
    rejection_reasons: Sequence[str] = (),
) -> dict[str, Any]:
    source_edge = network.edges.get(connection.from_edge)
    target_edge = network.edges.get(connection.to_edge)
    source_lane = _lane_by_ordinal(source_edge, connection.from_lane) if source_edge is not None else None
    target_lane = _lane_by_ordinal(target_edge, connection.to_lane) if target_edge is not None else None
    return {
        "from_function": source_edge.function if source_edge is not None else "missing",
        "to_function": target_edge.function if target_edge is not None else "missing",
        "source_permissions": _lane_permissions(source_lane),
        "target_permissions": _lane_permissions(target_lane),
        "direction": _turn_class(connection.direction),
        "has_link_index": connection.link_index is not None,
        "has_link_index2": connection.link_index2 is not None,
        "rejection_reasons": tuple(sorted(set(rejection_reasons))),
    }


def _turn_class(direction: str) -> str:
    return {
        "r": "right",
        "R": "right",
        "s": "straight",
        "l": "left",
        "L": "left",
        "t": "uturn",
        "T": "uturn",
    }.get(direction, "unknown")


def _lane_by_ordinal(edge: RawEdge, ordinal: int | None) -> RawLane | None:
    if ordinal is None or not 0 <= ordinal < len(edge.lanes):
        return None
    return edge.lanes[ordinal]


def _trace_internal_semantics(
    direct: RawConnection,
    network: RawNetwork,
    *,
    outgoing_connections: dict[tuple[str, int], list[RawConnection]],
    maximum_hops: int,
) -> tuple[dict[str, Any], tuple[str, ...]]:
    source_edge = network.edges.get(direct.from_edge)
    target_edge = network.edges.get(direct.to_edge)
    source_lane = _lane_by_ordinal(source_edge, direct.from_lane) if source_edge is not None else None
    target_lane = _lane_by_ordinal(target_edge, direct.to_lane) if target_edge is not None else None
    permission_records: list[RawLane | RawConnection | None] = [
        source_lane,
        direct,
    ]
    if not direct.via:
        permission_records.append(target_lane)
        if _raw_network_internal_link_mode(network) == "no-internal-links":
            return (
                {
                    "status": "no-internal-links",
                    "segments": (),
                    "failures": (),
                    "permission_contract": _path_permission_contract(
                        permission_records,
                        complete=True,
                    ),
                },
                (),
            )
        return (
            {
                "status": "missing-via",
                "segments": (),
                "failures": ("missing-via",),
                "permission_contract": _path_permission_contract(
                    permission_records,
                    complete=False,
                ),
            },
            (),
        )
    lane = network.lanes.get(direct.via)
    if lane is None:
        permission_records.append(None)
        return (
            {
                "status": "invalid",
                "segments": (),
                "failures": ("via-not-found",),
                "permission_contract": _path_permission_contract(
                    permission_records,
                    complete=False,
                ),
            },
            (),
        )
    permission_records.append(lane)
    segments: list[dict[str, Any]] = []
    failures: list[str] = []
    visited: set[str] = set()
    traversed_edge_ids: list[str] = []
    current = lane
    path_complete = False
    for _ in range(maximum_hops):
        if current.lane_id in visited:
            failures.append("cycle")
            break
        visited.add(current.lane_id)
        edge_id = network.lane_edge_ids.get(current.lane_id, "")
        if edge_id:
            traversed_edge_ids.append(edge_id)
        segments.append(
            {
                "shape_xy": _rounded_shape(current.shape),
                "permissions": _lane_permissions(current),
                "width_m": current.width,
                "speed_mps": current.speed,
            }
        )
        candidates = outgoing_connections.get((edge_id, current.ordinal), [])
        if len(candidates) != 1:
            failures.append(f"outgoing-count:{len(candidates)}")
            break
        continuation = candidates[0]
        permission_records.append(continuation)
        if continuation.to_edge != direct.to_edge or continuation.to_lane != direct.to_lane:
            failures.append("target-mismatch")
        if continuation.via:
            next_lane = network.lanes.get(continuation.via)
            if next_lane is None:
                permission_records.append(None)
                failures.append("continuation-not-found")
                break
            permission_records.append(next_lane)
            current = next_lane
            continue
        permission_records.append(target_lane)
        path_complete = target_lane is not None
        break
    else:
        failures.append("bounded-trace-exhausted")
    return (
        {
            "status": "pass" if not failures else "invalid",
            "segments": segments,
            "failures": tuple(failures),
            "permission_contract": _path_permission_contract(
                permission_records,
                complete=path_complete,
            ),
        },
        tuple(traversed_edge_ids),
    )


def _path_permission_contract(
    records: Sequence[RawLane | RawConnection | None],
    *,
    complete: bool,
) -> dict[str, Any]:
    available = tuple(record for record in records if record is not None)
    coverage = "complete" if complete and len(available) == len(records) else "partial"
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
        "coverage": coverage,
        "status": "fail" if not overlap else "pass" if coverage == "complete" else "unresolved",
        "basis": basis,
        "allow": overlap,
        "disallow": tuple(sorted(disallowed)),
        "element_count": len(available),
    }


def _raw_network_internal_link_mode(network: RawNetwork) -> str:
    internal_edge_count = sum(not edge.external for edge in network.edges.values())
    external_direct = [
        connection
        for connection in network.connections
        if (source := network.edges.get(connection.from_edge)) is not None
        and (target := network.edges.get(connection.to_edge)) is not None
        and source.external
        and target.external
    ]
    direct_with_via = sum(bool(connection.via) for connection in external_direct)
    if external_direct and not internal_edge_count and not direct_with_via:
        return "no-internal-links"
    if external_direct and 0 < direct_with_via < len(external_direct):
        return "mixed"
    if internal_edge_count or direct_with_via:
        return "internal-links"
    return "undetermined"


def _rounded_shape(
    shape: tuple[tuple[float, float], ...],
) -> tuple[tuple[float, float], ...]:
    return tuple((round(point[0], 6), round(point[1], 6)) for point in shape)


def _rounded_point(
    point: tuple[float | None, float | None],
) -> tuple[float | None, float | None]:
    return tuple(round(value, 6) if value is not None else None for value in point)
