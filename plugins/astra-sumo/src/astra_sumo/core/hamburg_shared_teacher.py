from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import math
from pathlib import Path
import re
from typing import TYPE_CHECKING, Mapping, Sequence
import xml.etree.ElementTree as ET

from astra_sumo.intersection import compile_plain as plain_compiler
from astra_sumo.intersection.schema import IntersectionIR, TLSPhase

from .junction_rebuild.replay_plans import build_shared_teacher_tls_controller_replay_plan
from .junction_rebuild.controller_replay import write_shared_teacher_tls_controller_replay_net
from .tls_aggregation import build_tls_signal_grouping_variant

if TYPE_CHECKING:
    from .hamburg_teacher_cell import HamburgTeacherCellContract


_KNOWN_HAMBURG_COMPONENT_COUNTS = {"228": 4, "2394": 1, "2421": 1}


@dataclass(frozen=True)
class HamburgDirectionalComponent:
    """One disconnected physical owner in an official directional graph."""

    approach_ids: tuple[str, ...]
    movement_ids: tuple[str, ...]


def derive_hamburg_directional_components(
    ir: IntersectionIR,
) -> tuple[HamburgDirectionalComponent, ...]:
    """Return deterministic components of the undirected approach/movement graph.

    Direction is retained by the movements themselves.  Connectivity is treated
    as undirected only to answer the physical question: which directional MAP
    approaches meet at the same SUMO internal owner?
    """

    approach_ids = {approach.approach_id for approach in ir.approaches}
    adjacency = {approach_id: set() for approach_id in approach_ids}
    movement_ids: set[str] = set()
    movement_by_component_edge: list[tuple[str, str, str]] = []
    for movement in ir.movement_matrix.movements:
        if not movement.allowed:
            raise ValueError(f"official teacher movement is not allowed: {movement.movement_id}")
        if movement.movement_id in movement_ids:
            raise ValueError(f"duplicate official movement id: {movement.movement_id}")
        movement_ids.add(movement.movement_id)
        if movement.from_approach_id not in approach_ids or movement.to_approach_id not in approach_ids:
            raise ValueError(f"movement references an unknown approach: {movement.movement_id}")
        adjacency[movement.from_approach_id].add(movement.to_approach_id)
        adjacency[movement.to_approach_id].add(movement.from_approach_id)
        movement_by_component_edge.append(
            (movement.movement_id, movement.from_approach_id, movement.to_approach_id)
        )

    components: list[HamburgDirectionalComponent] = []
    unseen = set(approach_ids)
    while unseen:
        seed = min(unseen, key=_natural_key)
        stack = [seed]
        member_ids: set[str] = set()
        while stack:
            current = stack.pop()
            if current in member_ids:
                continue
            member_ids.add(current)
            unseen.discard(current)
            stack.extend(sorted(adjacency[current] - member_ids, key=_natural_key, reverse=True))
        component_movement_ids = tuple(
            sorted(
                (
                    movement_id
                    for movement_id, from_id, to_id in movement_by_component_edge
                    if from_id in member_ids and to_id in member_ids
                ),
                key=_natural_key,
            )
        )
        if not component_movement_ids:
            raise ValueError(
                "official directional approach is not used by any movement: "
                f"{sorted(member_ids, key=_natural_key)}"
            )
        components.append(
            HamburgDirectionalComponent(
                approach_ids=tuple(sorted(member_ids, key=_natural_key)),
                movement_ids=component_movement_ids,
            )
        )
    components.sort(key=lambda item: tuple(_natural_key(value) for value in item.approach_ids))
    return tuple(components)


def materialize_hamburg_shared_teacher(
    *,
    contract: HamburgTeacherCellContract,
    output_dir: Path,
    prefix: str = "hamburg_shared_teacher",
    enforce_known_component_count: bool = True,
) -> dict[str, object]:
    """Materialize one official node contract as a multi-owner SUMO teacher."""

    controller_id = contract.ir.control.tls_id or contract.ir.core.core_id
    expected_count = _known_component_count(controller_id)
    return materialize_hamburg_shared_controller_teacher(
        ir=contract.ir,
        approach_pairs=contract.approach_pairs,
        output_dir=output_dir,
        prefix=prefix,
        control_key_by_expression_index={
            expression_index: control_key
            for control_key, expression_index in contract.expression_index_by_key.items()
        },
        expected_component_count=(
            expected_count if enforce_known_component_count and expected_count is not None else None
        ),
    )


def materialize_hamburg_shared_controller_teacher(
    *,
    ir: IntersectionIR,
    approach_pairs: Sequence[Mapping[str, object]] = (),
    output_dir: Path,
    prefix: str = "hamburg_shared_teacher",
    expected_component_count: int | None = None,
    control_key_by_expression_index: Mapping[int, str] | None = None,
) -> dict[str, object]:
    """Compile, compose, and expression-group a Hamburg official teacher.

    ``IntersectionIR`` remains the movement truth source.  Each disconnected
    component is compiled by ASTRA's existing plain compiler.  This adapter only
    combines those compiler products into one network with unique physical owner
    junctions and one shared controller.
    """

    truth_ir, expression_count = _normalize_truth_ir(ir)
    components = derive_hamburg_directional_components(truth_ir)
    if expected_component_count is not None and len(components) != expected_component_count:
        raise ValueError(
            f"official component count mismatch: expected {expected_component_count}, "
            f"got {len(components)}"
        )
    boundary_evidence = _inspect_boundary_pairs(
        _official_boundary_edge_ids(truth_ir),
        approach_pairs,
    )
    controller_id = truth_ir.control.tls_id or truth_ir.core.core_id
    owner_ids = tuple(
        controller_id if index == 0 else f"{controller_id}__owner_{index:02d}"
        for index in range(len(components))
    )

    output_dir = output_dir.resolve()
    component_dir = output_dir / f"{prefix}_components"
    output_dir.mkdir(parents=True, exist_ok=True)
    compiled_components: list[tuple[str, object]] = []
    for index, (component, owner_id) in enumerate(zip(components, owner_ids)):
        component_ir = _component_ir(
            truth_ir,
            component=component,
            owner_id=owner_id,
            component_index=index,
        )
        artifacts = plain_compiler.compile_intersection_to_plain(
            component_ir,
            component_dir / f"owner_{index:02d}",
            f"owner_{index:02d}",
            compile_net=False,
        )
        compiled_components.append((owner_id, artifacts))

    plain_files, controlled_connection_count = _compose_plain_components(
        compiled_components=compiled_components,
        output_dir=output_dir,
        prefix=prefix,
        controller_id=controller_id,
    )
    movement_count = len(truth_ir.movement_matrix.movements)
    if controlled_connection_count != movement_count:
        raise RuntimeError(
            "ASTRA compiler did not preserve one controlled connection per official movement: "
            f"movements={movement_count}, connections={controlled_connection_count}"
        )

    net_file = output_dir / f"{prefix}.net.xml"
    compiled, netconvert_warnings = plain_compiler._run_netconvert(
        plain_files["node_file"],
        plain_files["edge_file"],
        plain_files["connection_file"],
        plain_files["type_file"],
        plain_files["tllogic_file"],
        net_file,
        guess_crossings=False,
    )
    if not compiled:
        raise RuntimeError(
            "netconvert could not compile the shared teacher"
            + (f": {'; '.join(netconvert_warnings)}" if netconvert_warnings else "")
        )
    physical_key_restore = restore_hamburg_compiled_teacher_control_by_physical_key(
        net_file,
        plain_connection_file=plain_files["connection_file"],
        plain_tllogic_file=plain_files["tllogic_file"],
        controller_id=controller_id,
        owner_ids=owner_ids,
    )

    grouping_report = build_tls_signal_grouping_variant(
        source_net_file=net_file,
        output_dir=output_dir / f"{prefix}_expression_grouping",
        prefix=f"{prefix}_expression_grouping",
        max_shared_linkindex_groups=expression_count,
        control_key_by_connection=_teacher_control_key_by_physical_connection(
            truth_ir,
            controller_id=controller_id,
            control_key_by_expression_index=control_key_by_expression_index,
        ),
    )
    if grouping_report.get("status") != "pass":
        raise RuntimeError(f"ASTRA expression grouping failed: {grouping_report}")
    if (
        grouping_report.get("tls_signal_grouping_request_foe_evidence_status") != "available"
        or int(grouping_report.get("tls_signal_grouping_request_bound_connection_count", 0) or 0)
        != movement_count
    ):
        raise RuntimeError(
            "Hamburg teacher expression grouping requires complete SUMO request/foe evidence: "
            f"status={grouping_report.get('tls_signal_grouping_request_foe_evidence_status')}, "
            "bound="
            f"{grouping_report.get('tls_signal_grouping_request_bound_connection_count')}, "
            f"movements={movement_count}"
        )
    grouped_net_file = Path(str(grouping_report["tls_signal_grouping_variant_file"]))
    audit = _audit_materialized_teacher(
        grouped_net_file,
        reference_net_file=net_file,
        controller_id=controller_id,
        expected_owner_ids=owner_ids,
        expected_movement_count=movement_count,
        expected_expression_count=expression_count,
    )
    return {
        "status": "pass",
        "claim_status": "diagnostic-demo",
        "teacher_controller_id": controller_id,
        "teacher_owner_ids": list(owner_ids),
        "directional_component_count": len(components),
        "directional_components": [
            {
                "owner_id": owner_id,
                "approach_ids": list(component.approach_ids),
                "movement_ids": list(component.movement_ids),
            }
            for owner_id, component in zip(owner_ids, components)
        ],
        "official_movement_count": movement_count,
        "topology_expression_count": expression_count,
        "approach_pairs": boundary_evidence["approach_pairs"],
        "candidate_boundary_mapping_status": boundary_evidence["status"],
        "candidate_boundary_mapping_issues": boundary_evidence["issues"],
        "official_boundary_edge_ids": boundary_evidence["expected_edge_ids"],
        "plain_node_file": str(plain_files["node_file"]),
        "plain_edge_file": str(plain_files["edge_file"]),
        "plain_connection_file": str(plain_files["connection_file"]),
        "plain_tllogic_file": str(plain_files["tllogic_file"]),
        "teacher_net_file": str(net_file),
        "grouped_teacher_net_file": str(grouped_net_file),
        "physical_key_control_restore": physical_key_restore,
        "tls_signal_grouping_report": grouping_report,
        "netconvert_warnings": netconvert_warnings,
        **audit,
        "warnings": [
            "Diagnostic expression-basis phases encode topology groups, not an official signal program",
            "The grouped teacher still requires SUMO load, routeability, and Netedit review before adoption",
        ],
    }


def replay_hamburg_shared_teacher(
    *,
    candidate_net_file: Path,
    teacher_net_file: Path,
    output_file: Path,
    teacher_controller_id: str,
    candidate_controller_id: str,
    candidate_junction_ids: set[str] | list[str],
    approach_pairs: Sequence[Mapping[str, object]],
    collapse_junction_ids: set[str] | list[str] | None = None,
    candidate_owner_map: Mapping[str, str] | None = None,
) -> dict[str, object]:
    """Run ASTRA's existing shared-controller planner and writer fail-closed."""

    boundary_evidence = _inspect_teacher_replay_boundary_pairs(
        teacher_net_file,
        teacher_controller_id=teacher_controller_id,
        approach_pairs=approach_pairs,
    )
    if boundary_evidence["status"] != "ready_for_replay":
        return {
            "status": "blocked",
            "claim_status": "blocked",
            "shared_controller_replay_reason": "official_candidate_boundary_mapping_not_one_to_one",
            "candidate_boundary_mapping_evidence": boundary_evidence,
            "shared_controller_replay_plan": None,
            "shared_controller_replay_report": None,
        }
    plan = build_shared_teacher_tls_controller_replay_plan(
        candidate_net_file=candidate_net_file,
        teacher_net_file=teacher_net_file,
        teacher_controller_id=teacher_controller_id,
        candidate_controller_id=candidate_controller_id,
        candidate_junction_ids=candidate_junction_ids,
        approach_pairs=boundary_evidence["approach_pairs"],
        collapse_junction_ids=collapse_junction_ids,
        candidate_owner_map=dict(candidate_owner_map or {}),
    )
    if plan.get("status") != "pass":
        return {
            "status": "blocked",
            "claim_status": "blocked",
            "shared_controller_replay_plan": plan,
            "shared_controller_replay_report": None,
        }
    replay = write_shared_teacher_tls_controller_replay_net(
        candidate_net_file=candidate_net_file,
        teacher_net_file=teacher_net_file,
        output_file=output_file,
        candidate_controller_id=candidate_controller_id,
        teacher_controller_id=teacher_controller_id,
        owner_map={str(key): str(value) for key, value in plan["owner_map"].items()},
        edge_map={str(key): str(value) for key, value in plan["edge_map"].items()},
        junction_map={str(key): str(value) for key, value in plan["junction_map"].items()},
        collapse_junction_ids=collapse_junction_ids,
    )
    return {
        "status": "pass" if replay.get("status") == "pass" else "blocked",
        "claim_status": "diagnostic-demo" if replay.get("status") == "pass" else "blocked",
        "shared_controller_replay_plan": plan,
        "shared_controller_replay_report": replay,
    }


def _normalize_truth_ir(ir: IntersectionIR) -> tuple[IntersectionIR, int]:
    movements = ir.movement_matrix.movements
    movement_ids = [movement.movement_id for movement in movements]
    if len(set(movement_ids)) != len(movement_ids):
        raise ValueError("official movement ids are not unique")
    if not movements:
        raise ValueError("official teacher has no movements")
    if set(ir.control.link_index_map) != set(movement_ids):
        raise ValueError("every official movement must have exactly one topology control expression")
    physical_keys: set[tuple[str, int, str, int]] = set()
    for movement in movements:
        if len(movement.from_lane_indices) != 1 or len(movement.to_lane_indices) != 1:
            raise ValueError(
                f"official movement must resolve to exactly one lane pair: {movement.movement_id}"
            )
        key = (
            movement.from_approach_id,
            movement.from_lane_indices[0],
            movement.to_approach_id,
            movement.to_lane_indices[0],
        )
        if key in physical_keys:
            raise ValueError(f"duplicate physical official movement: {key}")
        physical_keys.add(key)

    old_indexes = sorted(set(ir.control.link_index_map.values()))
    if not old_indexes or old_indexes[0] < 0:
        raise ValueError("topology control expression indexes must be non-negative")
    dense_index = {old: new for new, old in enumerate(old_indexes)}
    link_index_map = {
        movement_id: dense_index[index]
        for movement_id, index in ir.control.link_index_map.items()
    }
    expression_count = len(old_indexes)
    phases = [
        TLSPhase(
            phase_id=f"topology_expression_basis_{index:02d}",
            duration=1.0,
            state="".join("g" if item == index else "r" for item in range(expression_count)),
            movement_ids=sorted(
                (
                    movement_id
                    for movement_id, expression_index in link_index_map.items()
                    if expression_index == index
                ),
                key=_natural_key,
            ),
        )
        for index in range(expression_count)
    ]
    normalized_control = ir.control.model_copy(
        update={
            "tls_id": ir.control.tls_id or ir.core.core_id,
            "link_index_map": link_index_map,
            "phases": phases,
            "source": [
                *ir.control.source,
                "hamburg_shared_teacher_expression_basis",
                "topology_basis_only_not_an_official_signal_program",
            ],
        }
    )
    return ir.model_copy(update={"control": normalized_control}), expression_count


def _official_boundary_edge_ids(ir: IntersectionIR) -> set[str]:
    approaches = {approach.approach_id: approach for approach in ir.approaches}
    edge_ids: set[str] = set()
    for movement in ir.movement_matrix.movements:
        edge_ids.add(approaches[movement.from_approach_id].incoming_edge_ids[0])
        edge_ids.add(approaches[movement.to_approach_id].outgoing_edge_ids[0])
    return edge_ids


def _teacher_control_key_by_physical_connection(
    ir: IntersectionIR,
    *,
    controller_id: str,
    control_key_by_expression_index: Mapping[int, str] | None,
) -> dict[tuple[str, str, str, str, str], str]:
    approaches = {approach.approach_id: approach for approach in ir.approaches}
    result: dict[tuple[str, str, str, str, str], str] = {}
    for movement in ir.movement_matrix.movements:
        source = approaches[movement.from_approach_id]
        target = approaches[movement.to_approach_id]
        expression_index = int(ir.control.link_index_map[movement.movement_id])
        control_key = (
            str(control_key_by_expression_index.get(expression_index, "")).strip()
            if control_key_by_expression_index is not None
            else ""
        ) or f"topology_expression:{expression_index}"
        physical_key = (
            controller_id,
            source.incoming_edge_ids[0],
            target.outgoing_edge_ids[0],
            str(movement.from_lane_indices[0]),
            str(movement.to_lane_indices[0]),
        )
        if physical_key in result:
            raise ValueError(f"duplicate teacher physical control key: {physical_key}")
        result[physical_key] = control_key
    return result


def _inspect_boundary_pairs(
    expected_edge_ids: set[str],
    approach_pairs: Sequence[Mapping[str, object]],
) -> dict[str, object]:
    pair_by_reference: dict[str, dict[str, object]] = {}
    candidate_to_reference: dict[str, str] = {}
    issues: list[str] = []
    for raw_pair in approach_pairs:
        pair = dict(raw_pair)
        reference_edge_id = str(pair.get("reference_edge_id", ""))
        candidate_edge_id = str(pair.get("candidate_edge_id", ""))
        if not reference_edge_id or not candidate_edge_id:
            issues.append("boundary_pair_missing_teacher_or_candidate_edge")
            continue
        if reference_edge_id in pair_by_reference:
            issues.append(f"official_boundary_mapped_more_than_once:{reference_edge_id}")
            continue
        previous_reference = candidate_to_reference.get(candidate_edge_id)
        if previous_reference is not None and previous_reference != reference_edge_id:
            issues.append(
                "candidate_boundary_reused_by_multiple_official_approaches:"
                f"{candidate_edge_id}:{previous_reference},{reference_edge_id}"
            )
        pair_by_reference[reference_edge_id] = pair
        candidate_to_reference[candidate_edge_id] = reference_edge_id
    if set(pair_by_reference) != expected_edge_ids:
        missing = sorted(expected_edge_ids - set(pair_by_reference), key=_natural_key)
        extra = sorted(set(pair_by_reference) - expected_edge_ids, key=_natural_key)
        if missing:
            issues.append(f"official_boundary_edges_unmapped:{missing}")
        if extra:
            issues.append(f"non_official_teacher_boundary_edges_mapped:{extra}")
    return {
        "status": "ready_for_replay" if not issues else "incomplete_or_ambiguous",
        "expected_edge_ids": sorted(expected_edge_ids, key=_natural_key),
        "approach_pairs": [
            pair_by_reference[edge_id]
            for edge_id in sorted(pair_by_reference, key=_natural_key)
        ],
        "issues": issues,
    }


def _inspect_teacher_replay_boundary_pairs(
    teacher_net_file: Path,
    *,
    teacher_controller_id: str,
    approach_pairs: Sequence[Mapping[str, object]],
) -> dict[str, object]:
    if not teacher_net_file.exists():
        return {
            "status": "incomplete_or_ambiguous",
            "expected_edge_ids": [],
            "approach_pairs": [],
            "issues": [f"teacher_net_file_missing:{teacher_net_file}"],
        }
    try:
        root = ET.parse(teacher_net_file).getroot()
    except (ET.ParseError, OSError, ValueError) as exc:
        return {
            "status": "incomplete_or_ambiguous",
            "expected_edge_ids": [],
            "approach_pairs": [],
            "issues": [f"teacher_net_parse_failed:{type(exc).__name__}:{exc}"],
        }
    expected_edge_ids = {
        edge_id
        for connection in root.findall("connection")
        if connection.attrib.get("tl") == teacher_controller_id
        and connection.attrib.get("linkIndex") is not None
        for edge_id in (
            connection.attrib.get("from", ""),
            connection.attrib.get("to", ""),
        )
        if edge_id and not edge_id.startswith(":")
    }
    return _inspect_boundary_pairs(expected_edge_ids, approach_pairs)


def _component_ir(
    ir: IntersectionIR,
    *,
    component: HamburgDirectionalComponent,
    owner_id: str,
    component_index: int,
) -> IntersectionIR:
    approach_ids = set(component.approach_ids)
    movement_ids = set(component.movement_ids)
    selected_approaches = [
        approach for approach in ir.approaches if approach.approach_id in approach_ids
    ]
    center = _component_center(selected_approaches, fallback=ir.core.center_xy)
    selected_approaches = [
        approach.model_copy(update={"source_shape_xy": _shape_to_center(approach, center)})
        for approach in selected_approaches
    ]
    selected_movements = [
        movement for movement in ir.movement_matrix.movements if movement.movement_id in movement_ids
    ]
    selected_link_map = {
        movement_id: index
        for movement_id, index in ir.control.link_index_map.items()
        if movement_id in movement_ids
    }
    selected_phases = [
        phase.model_copy(
            update={
                "movement_ids": [
                    movement_id for movement_id in phase.movement_ids if movement_id in movement_ids
                ]
            }
        )
        for phase in ir.control.phases
    ]
    return ir.model_copy(
        update={
            "intersection_id": f"{ir.intersection_id}__owner_{component_index:02d}",
            "core": ir.core.model_copy(update={"core_id": owner_id, "center_xy": center}),
            "approaches": selected_approaches,
            "movement_matrix": ir.movement_matrix.model_copy(
                update={
                    "movements": selected_movements,
                    "legal_movement_count": len(selected_movements),
                    "forbidden_movement_count": 0,
                    "inferred_movement_count": 0,
                }
            ),
            "control": ir.control.model_copy(
                update={"link_index_map": selected_link_map, "phases": selected_phases}
            ),
            "compiled": None,
            "validation": None,
        }
    )


def _compose_plain_components(
    *,
    compiled_components: Sequence[tuple[str, object]],
    output_dir: Path,
    prefix: str,
    controller_id: str,
) -> tuple[dict[str, Path], int]:
    roots = {
        "nodes": ET.Element("nodes"),
        "edges": ET.Element("edges"),
        "connections": ET.Element("connections"),
        "types": ET.Element("types"),
    }
    seen_ids = {"nodes": set(), "edges": set(), "types": set()}
    combined_phase_states: list[list[str]] | None = None
    phase_durations: list[str] = []
    connection_offset = 0
    for _owner_id, artifacts in compiled_components:
        file_by_kind = {
            "nodes": Path(artifacts.plain_node_file),
            "edges": Path(artifacts.plain_edge_file),
            "connections": Path(artifacts.plain_connection_file),
            "types": Path(str(artifacts.plain_type_file)),
        }
        for kind in ("nodes", "edges", "types"):
            for element in ET.parse(file_by_kind[kind]).getroot():
                element_id = element.attrib.get("id", "")
                if element_id in seen_ids[kind]:
                    if kind == "types":
                        continue
                    raise ValueError(f"duplicate composed {kind} id: {element_id}")
                seen_ids[kind].add(element_id)
                roots[kind].append(element)

        connections = list(ET.parse(file_by_kind["connections"]).getroot())
        for local_index, connection in enumerate(connections):
            if connection.attrib.get("tl") != controller_id:
                raise ValueError("component compiler emitted a connection outside the shared controller")
            connection.set("linkIndex", str(connection_offset + local_index))
            roots["connections"].append(connection)

        if not artifacts.plain_tllogic_file:
            raise ValueError("component compiler did not emit TLS logic")
        logic_root = ET.parse(Path(artifacts.plain_tllogic_file)).getroot()
        logics = logic_root.findall("tlLogic")
        if len(logics) != 1 or logics[0].attrib.get("id") != controller_id:
            raise ValueError("component compiler did not retain the shared controller id")
        phases = logics[0].findall("phase")
        if combined_phase_states is None:
            combined_phase_states = [[] for _ in phases]
            phase_durations = [phase.attrib.get("duration", "1") for phase in phases]
        if len(phases) != len(combined_phase_states):
            raise ValueError("component expression-basis phase counts differ")
        for index, phase in enumerate(phases):
            state = phase.attrib.get("state", "")
            if len(state) != len(connections):
                raise ValueError("component phase state does not cover every controlled connection")
            if phase.attrib.get("duration", "1") != phase_durations[index]:
                raise ValueError("component expression-basis phase durations differ")
            combined_phase_states[index].append(state)
        connection_offset += len(connections)

    if combined_phase_states is None:
        raise ValueError("no component TLS phases were compiled")
    tls_root = ET.Element("tlLogics")
    logic = ET.SubElement(
        tls_root,
        "tlLogic",
        id=controller_id,
        type="static",
        programID="0",
        offset="0",
    )
    for duration, state_parts in zip(phase_durations, combined_phase_states):
        ET.SubElement(logic, "phase", duration=duration, state="".join(state_parts))

    files = {
        "node_file": output_dir / f"{prefix}.nod.xml",
        "edge_file": output_dir / f"{prefix}.edg.xml",
        "connection_file": output_dir / f"{prefix}.con.xml",
        "type_file": output_dir / f"{prefix}.typ.xml",
        "tllogic_file": output_dir / f"{prefix}.tll.xml",
    }
    _write_xml(files["node_file"], roots["nodes"])
    _write_xml(files["edge_file"], roots["edges"])
    _write_xml(files["connection_file"], roots["connections"])
    _write_xml(files["type_file"], roots["types"])
    _write_xml(files["tllogic_file"], tls_root)
    return files, connection_offset


def _audit_materialized_teacher(
    net_file: Path,
    *,
    reference_net_file: Path,
    controller_id: str,
    expected_owner_ids: Sequence[str],
    expected_movement_count: int,
    expected_expression_count: int,
) -> dict[str, object]:
    reference_root = ET.parse(reference_net_file).getroot()
    root = ET.parse(net_file).getroot()
    controlled = [
        connection
        for connection in root.findall("connection")
        if connection.attrib.get("tl") == controller_id
        and connection.attrib.get("linkIndex") is not None
    ]
    if len(controlled) != expected_movement_count:
        raise RuntimeError(
            f"grouped teacher movement count changed: {len(controlled)} != {expected_movement_count}"
        )
    physical_keys = [
        (
            connection.attrib.get("from", ""),
            connection.attrib.get("fromLane", ""),
            connection.attrib.get("to", ""),
            connection.attrib.get("toLane", ""),
        )
        for connection in controlled
    ]
    if len(set(physical_keys)) != len(physical_keys):
        raise RuntimeError("grouped teacher contains duplicate controlled physical movements")
    used_link_indexes = sorted({int(connection.attrib["linkIndex"]) for connection in controlled})
    if len(used_link_indexes) < expected_expression_count:
        raise RuntimeError(
            "expression grouping lost an official expression link index: "
            f"{len(used_link_indexes)} < {expected_expression_count}"
        )
    logics = [logic for logic in root.findall("tlLogic") if logic.attrib.get("id") == controller_id]
    if len(logics) != 1:
        raise RuntimeError("shared teacher must contain exactly one controller tlLogic")
    unexpected_phase_characters = sorted(
        {
            character
            for phase in logics[0].findall("phase")
            for character in phase.attrib.get("state", "")
            if character not in {"r", "g"}
        }
    )
    if unexpected_phase_characters:
        raise RuntimeError(
            "Hamburg topology-basis phases must be permissive diagnostic r/g states: "
            f"{unexpected_phase_characters}"
        )
    reference_signatures = _control_signatures_by_physical_key(
        reference_root,
        controller_id=controller_id,
    )
    grouped_signatures = _control_signatures_by_physical_key(
        root,
        controller_id=controller_id,
    )
    signature_mismatches = sorted(
        key
        for key in set(reference_signatures) | set(grouped_signatures)
        if reference_signatures.get(key) != grouped_signatures.get(key)
    )
    if signature_mismatches:
        raise RuntimeError(
            "expression grouping changed official movement control signatures: "
            f"{signature_mismatches}"
        )
    expression_signatures = set(grouped_signatures.values())
    if len(expression_signatures) != expected_expression_count:
        raise RuntimeError(
            "physical-key control signatures do not match the official expression count: "
            f"{len(expression_signatures)} != {expected_expression_count}"
        )
    owner_ids = sorted(expected_owner_ids, key=len, reverse=True)
    detected_owners = sorted(
        {
            owner_id
            for connection in controlled
            for owner_id in owner_ids
            if connection.attrib.get("via", "").startswith(f":{owner_id}_")
        },
        key=_natural_key,
    )
    if set(detected_owners) != set(expected_owner_ids):
        raise RuntimeError(
            f"shared teacher owner closure changed: {detected_owners} != {list(expected_owner_ids)}"
        )
    return {
        "compiled_controlled_connection_count": len(controlled),
        "duplicate_controlled_connection_count": len(physical_keys) - len(set(physical_keys)),
        "grouped_link_index_count": len(used_link_indexes),
        "foe_separated_link_index_count": len(used_link_indexes) - expected_expression_count,
        "physical_key_control_signature_count": len(grouped_signatures),
        "physical_key_control_signature_mismatch_count": len(signature_mismatches),
        "diagnostic_phase_alphabet": sorted(
            {
                character
                for phase in logics[0].findall("phase")
                for character in phase.attrib.get("state", "")
            }
        ),
        "detected_teacher_owner_ids": detected_owners,
        "shared_tllogic_count": len(logics),
    }


def _control_signatures_by_physical_key(
    root: ET.Element,
    *,
    controller_id: str,
) -> dict[tuple[str, str, str, str], tuple[str, ...]]:
    logics = [logic for logic in root.findall("tlLogic") if logic.attrib.get("id") == controller_id]
    if len(logics) != 1:
        raise RuntimeError(f"teacher must contain one tlLogic for {controller_id}")
    phase_states = [phase.attrib.get("state", "") for phase in logics[0].findall("phase")]
    signatures: dict[tuple[str, str, str, str], tuple[str, ...]] = {}
    for connection in root.findall("connection"):
        if connection.attrib.get("tl") != controller_id:
            continue
        raw_index = connection.attrib.get("linkIndex")
        if raw_index is None:
            continue
        try:
            link_index = int(raw_index)
        except ValueError as exc:
            raise RuntimeError(f"non-numeric Hamburg teacher linkIndex: {raw_index}") from exc
        if any(link_index >= len(state) for state in phase_states):
            raise RuntimeError(
                f"Hamburg teacher linkIndex {link_index} exceeds a phase state for {controller_id}"
            )
        key = (
            connection.attrib.get("from", ""),
            connection.attrib.get("to", ""),
            connection.attrib.get("fromLane", ""),
            connection.attrib.get("toLane", ""),
        )
        if key in signatures:
            raise RuntimeError(f"duplicate Hamburg teacher physical movement: {key}")
        signatures[key] = tuple(state[link_index] for state in phase_states)
    return signatures


def restore_hamburg_compiled_teacher_control_by_physical_key(
    net_file: Path,
    *,
    plain_connection_file: Path,
    plain_tllogic_file: Path,
    controller_id: str,
    owner_ids: Sequence[str],
) -> dict[str, object]:
    """Restore Hamburg teacher control after netconvert builds internals.

    Netconvert assigns each traffic-light junction its own controller even when
    the plain connections name a remote TLS.  ASTRA's existing shared-controller
    fixtures use the same safe two-step representation: let netconvert build each
    owner's requests/vias, then bind those already-built movements to one shared
    controller without changing their physical topology.  The stable physical key
    is ``(from, to, fromLane, toLane)``; a pre-netconvert ``linkIndex`` is never
    assumed to survive netconvert ordering.

    The helper also serves a one-owner Hamburg teacher.  It restores only control
    attributes and the diagnostic tlLogic; it never changes endpoints, lanes, vias,
    or request/foe topology.
    """

    requested_indexes: dict[tuple[str, str, str, str], str] = {}
    for connection in ET.parse(plain_connection_file).getroot().findall("connection"):
        key = (
            connection.attrib.get("from", ""),
            connection.attrib.get("to", ""),
            connection.attrib.get("fromLane", ""),
            connection.attrib.get("toLane", ""),
        )
        if key in requested_indexes:
            raise ValueError(f"duplicate plain controlled movement during shared bind: {key}")
        requested_indexes[key] = connection.attrib["linkIndex"]

    tree = ET.parse(net_file)
    root = tree.getroot()
    matched: set[tuple[str, str, str, str]] = set()
    for connection in root.findall("connection"):
        key = (
            connection.attrib.get("from", ""),
            connection.attrib.get("to", ""),
            connection.attrib.get("fromLane", ""),
            connection.attrib.get("toLane", ""),
        )
        if key not in requested_indexes:
            continue
        connection.set("tl", controller_id)
        connection.set("linkIndex", requested_indexes[key])
        matched.add(key)
    if matched != set(requested_indexes):
        raise RuntimeError(
            "netconvert dropped or changed an official movement before shared binding: "
            f"missing={sorted(set(requested_indexes) - matched)}"
        )

    owner_set = set(owner_ids)
    for logic in list(root.findall("tlLogic")):
        if logic.attrib.get("id") in owner_set:
            root.remove(logic)
    source_logics = ET.parse(plain_tllogic_file).getroot().findall("tlLogic")
    if len(source_logics) != 1 or source_logics[0].attrib.get("id") != controller_id:
        raise ValueError("combined plain TLS logic does not contain the shared controller")
    insert_at = next(
        (index for index, child in enumerate(root) if child.tag == "junction"),
        len(root),
    )
    root.insert(insert_at, deepcopy(source_logics[0]))
    ET.indent(root, space="    ")
    tree.write(net_file, encoding="utf-8", xml_declaration=True)
    return {
        "status": "pass",
        "physical_key_policy": "from,to,fromLane,toLane",
        "requested_controlled_connection_count": len(requested_indexes),
        "restored_controlled_connection_count": len(matched),
        "restored_controller_id": controller_id,
        "replaced_owner_controller_ids": sorted(owner_set, key=_natural_key),
        "restored_phase_count": len(source_logics[0].findall("phase")),
    }


def _component_center(approaches: Sequence[object], *, fallback: tuple[float, float]) -> tuple[float, float]:
    anchors = [
        approach.source_shape_xy[-1]
        for approach in approaches
        if approach.source_shape_xy
    ]
    if not anchors:
        return fallback
    return (
        sum(point[0] for point in anchors) / len(anchors),
        sum(point[1] for point in anchors) / len(anchors),
    )


def _shape_to_center(approach: object, center: tuple[float, float]) -> list[tuple[float, float]]:
    shape = list(approach.source_shape_xy)
    if not shape:
        shape = [approach.endpoint_xy or center]
    if math.dist(shape[-1], center) <= 1e-6:
        shape[-1] = center
    else:
        shape.append(center)
    return shape


def _known_component_count(controller_id: str) -> int | None:
    match = re.search(r"(\d+)$", controller_id)
    if not match:
        return None
    normalized = match.group(1).lstrip("0") or "0"
    return _KNOWN_HAMBURG_COMPONENT_COUNTS.get(normalized)


def _write_xml(path: Path, root: ET.Element) -> None:
    ET.indent(root, space="    ")
    ET.ElementTree(root).write(path, encoding="utf-8", xml_declaration=True)


def _natural_key(value: str) -> tuple[object, ...]:
    return tuple(
        int(part) if part.isdigit() else part.casefold()
        for part in re.split(r"(\d+)", value)
    )
