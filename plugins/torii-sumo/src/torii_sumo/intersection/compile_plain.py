from __future__ import annotations

import math
import shutil
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path

from torii_sumo.road_semantics import classify_approach_mode_layer

from .infer_movements import core_connection_movements
from .schema import CompiledSUMOArtifacts, IntersectionIR


def compile_intersection_to_plain(
    ir: IntersectionIR,
    output_dir: Path,
    prefix: str,
    compile_net: bool = True,
) -> CompiledSUMOArtifacts:
    output_dir.mkdir(parents=True, exist_ok=True)
    node_file = output_dir / f"{prefix}.nod.xml"
    edge_file = output_dir / f"{prefix}.edg.xml"
    connection_file = output_dir / f"{prefix}.con.xml"
    type_file = output_dir / f"{prefix}.typ.xml"
    tllogic_file = output_dir / f"{prefix}.tll.xml" if ir.control.control_type == "traffic_light" else None
    net_file = output_dir / f"{prefix}.net.xml"
    guess_crossings = needs_sumo_crossing(ir)
    custom_tllogic_applied = bool(tllogic_file and not guess_crossings)

    connection_rows = _connection_rows(ir)
    _write_nodes(node_file, ir)
    _write_edges(edge_file, ir)
    _write_connections(connection_file, ir, connection_rows)
    _write_types(type_file, {approach.highway_class for approach in ir.approaches})
    if tllogic_file is not None:
        _write_tllogic(tllogic_file, ir, connection_rows)

    netconvert_warnings: list[str] = []
    compiled_net = False
    if compile_net:
        compiled_net, netconvert_warnings = _run_netconvert(
            node_file,
            edge_file,
            connection_file,
            type_file,
            tllogic_file,
            net_file,
            guess_crossings=guess_crossings,
        )
    return CompiledSUMOArtifacts(
        plain_node_file=str(node_file),
        plain_edge_file=str(edge_file),
        plain_connection_file=str(connection_file),
        plain_type_file=str(type_file),
        plain_tllogic_file=str(tllogic_file) if tllogic_file else None,
        net_file=str(net_file) if compiled_net else "",
        sumocfg_file=None,
        netconvert_warnings=netconvert_warnings,
        custom_tllogic_applied=custom_tllogic_applied,
    )


def _write_nodes(path: Path, ir: IntersectionIR) -> None:
    root = ET.Element("nodes")
    center_x, center_y = ir.core.center_xy
    ET.SubElement(
        root,
        "node",
        id=ir.core.core_id,
        type=ir.control.control_type,
        x=f"{center_x:.2f}",
        y=f"{center_y:.2f}",
    )
    if support_core_id := _support_core_id(ir):
        ET.SubElement(
            root,
            "node",
            id=support_core_id,
            type=_support_core_type(ir),
            x=f"{center_x:.2f}",
            y=f"{center_y:.2f}",
        )
    for approach in ir.approaches:
        if approach.endpoint_xy is None:
            dx = math.sin(math.radians(approach.bearing_from_core)) * 50
            dy = math.cos(math.radians(approach.bearing_from_core)) * 50
            x, y = center_x + dx, center_y + dy
        else:
            x, y = approach.endpoint_xy
        ET.SubElement(
            root,
            "node",
            id=approach.approach_id,
            x=f"{x:.2f}",
            y=f"{y:.2f}",
            type="priority",
        )
    _write_xml(path, root)


def _write_edges(path: Path, ir: IntersectionIR) -> None:
    root = ET.Element("edges")
    support_core_id = _support_core_id(ir)
    for approach in ir.approaches:
        core_id = support_core_id if support_core_id and _is_support_only_approach(approach) else ir.core.core_id
        edge_type = f"highway.{approach.highway_class}"
        allow = " ".join(sorted(approach.allowed_modes))
        incoming_lane_modes = _incoming_lane_modes(approach)
        outgoing_lane_modes = _outgoing_lane_modes(approach, support_core_id)
        incoming_attrs = {"from": approach.approach_id, "to": core_id, "type": edge_type, "numLanes": str(len(incoming_lane_modes)), "allow": allow}
        outgoing_attrs = {"from": core_id, "to": approach.approach_id, "type": edge_type, "numLanes": str(len(outgoing_lane_modes)), "allow": allow}
        if approach.source_shape_xy:
            incoming_attrs["shape"] = _format_shape(approach.source_shape_xy)
            outgoing_attrs["shape"] = _format_shape(list(reversed(approach.source_shape_xy)))
        if _emits_incoming_edge(approach):
            _write_edge(root, approach.incoming_edge_ids[0], incoming_attrs, incoming_lane_modes if approach.incoming_extra_lane_modes else [])
        if _emits_outgoing_edge(approach, support_core_id):
            _write_edge(root, approach.outgoing_edge_ids[0], outgoing_attrs, outgoing_lane_modes if approach.outgoing_extra_lane_modes else [])
    _write_xml(path, root)


def _lane_modes(base_count: int, base_modes: set[str], extra_modes: list[set[str]]) -> list[set[str]]:
    return [base_modes for _ in range(base_count)] + extra_modes


def _incoming_lane_modes(approach) -> list[set[str]]:
    return _lane_modes(approach.incoming_lane_count, approach.allowed_modes, approach.incoming_extra_lane_modes)


def _outgoing_lane_modes(approach, support_core_id: str | None) -> list[set[str]]:
    outgoing_extra_lane_modes = [] if support_core_id and "passenger" in approach.allowed_modes else approach.outgoing_extra_lane_modes
    return _lane_modes(approach.outgoing_lane_count, approach.allowed_modes, outgoing_extra_lane_modes)


def _emits_incoming_edge(approach) -> bool:
    return _has_incoming_flow(approach) and bool(_incoming_lane_modes(approach))


def _emits_outgoing_edge(approach, support_core_id: str | None) -> bool:
    return _has_outgoing_flow(approach) and bool(_outgoing_lane_modes(approach, support_core_id))


def _has_incoming_flow(approach) -> bool:
    return "passenger" not in approach.allowed_modes or approach.has_incoming_vehicle_flow


def _has_outgoing_flow(approach) -> bool:
    return "passenger" not in approach.allowed_modes or approach.has_outgoing_vehicle_flow


def _support_core_id(ir: IntersectionIR) -> str | None:
    if ir.control.control_type != "traffic_light":
        return None
    if not any("passenger" in approach.allowed_modes for approach in ir.approaches):
        return None
    if not any(_is_support_only_approach(approach) for approach in ir.approaches):
        return None
    return f"support_{ir.core.core_id}"


def _support_core_type(ir: IntersectionIR) -> str:
    return "traffic_light" if _has_controlled_support_movement(ir) else "priority"


def _has_controlled_support_movement(ir: IntersectionIR) -> bool:
    approaches = {approach.approach_id: approach for approach in ir.approaches}
    for movement in ir.movement_matrix.movements:
        source = approaches.get(movement.from_approach_id)
        target = approaches.get(movement.to_approach_id)
        if (
            movement.allowed
            and movement.movement_id in ir.control.link_index_map
            and source is not None
            and target is not None
            and _is_support_only_approach(source)
            and _is_support_only_approach(target)
        ):
            return True
    return False


def _is_support_only_approach(approach) -> bool:
    return classify_approach_mode_layer(
        approach.allowed_modes,
        approach.incoming_extra_lane_modes,
        approach.outgoing_extra_lane_modes,
    ).is_support_only


def _write_edge(root: ET.Element, edge_id: str, attrs: dict[str, str], lane_modes: list[set[str]]) -> None:
    edge = ET.SubElement(root, "edge", id=edge_id, **attrs)
    for index, modes in enumerate(lane_modes):
        ET.SubElement(edge, "lane", index=str(index), allow=" ".join(sorted(modes)))


def _write_connections(path: Path, ir: IntersectionIR, connection_rows) -> None:
    root = ET.Element("connections")
    for index, (movement, source, target, from_lane, to_lane) in enumerate(connection_rows):
        attrs = {
            "from": source.incoming_edge_ids[0],
            "to": target.outgoing_edge_ids[0],
            "fromLane": str(from_lane),
            "toLane": str(to_lane),
            "dir": movement.turn[0],
        }
        if movement.movement_id in ir.control.link_index_map:
            attrs["tl"] = ir.control.tls_id or ir.core.core_id
            attrs["linkIndex"] = str(index)
        ET.SubElement(root, "connection", **attrs)
    _write_xml(path, root)


def _connection_rows(ir: IntersectionIR):
    approaches = {approach.approach_id: approach for approach in ir.approaches}
    controlled_ids = set(ir.control.link_index_map)
    support_core_id = _support_core_id(ir)
    movements = core_connection_movements(ir.movement_matrix.movements)
    seen = {movement.movement_id for movement in movements}
    movements = [
        *movements,
        *[
            movement
            for movement in ir.movement_matrix.movements
            if movement.allowed
            and movement.movement_id in controlled_ids
            and movement.movement_id not in seen
            and movement.from_approach_id in approaches
            and movement.to_approach_id in approaches
        ],
    ]
    rows = []
    for movement in movements:
        source = approaches[movement.from_approach_id]
        target = approaches[movement.to_approach_id]
        if not (_emits_incoming_edge(source) and _emits_outgoing_edge(target, support_core_id)):
            continue
        for from_lane, to_lane in _lane_pairs(movement.from_lane_indices, movement.to_lane_indices):
            rows.append((movement, source, target, from_lane, to_lane))
    return rows


def _lane_pairs(from_lanes: list[int], to_lanes: list[int]) -> list[tuple[int, int]]:
    if not from_lanes or not to_lanes:
        return []
    return [
        (from_lanes[min(index, len(from_lanes) - 1)], to_lanes[min(index, len(to_lanes) - 1)])
        for index in range(max(len(from_lanes), len(to_lanes)))
    ]


def _write_types(path: Path, highway_classes: set[str]) -> None:
    root = ET.Element("types")
    for highway_class in sorted({"primary", "secondary", "residential", "road", *highway_classes}):
        ET.SubElement(root, "type", id=f"highway.{highway_class}", numLanes="1", speed="13.89")
    _write_xml(path, root)


def _write_tllogic(path: Path, ir: IntersectionIR, connection_rows) -> None:
    root = ET.Element("tlLogics")
    logic = ET.SubElement(root, "tlLogic", id=ir.control.tls_id or ir.core.core_id, type="static", programID="0", offset="0")
    for phase in ir.control.phases:
        ET.SubElement(logic, "phase", duration=f"{phase.duration:g}", state=_expanded_phase_state(ir, phase.state, connection_rows))
    _write_xml(path, root)


def _expanded_phase_state(ir: IntersectionIR, state: str, connection_rows) -> str:
    return "".join(state[ir.control.link_index_map[movement.movement_id]] for movement, *_ in connection_rows)


def _format_shape(points: list[tuple[float, float]]) -> str:
    return " ".join(f"{x:.2f},{y:.2f}" for x, y in points)


def _run_netconvert(
    node_file: Path,
    edge_file: Path,
    connection_file: Path,
    type_file: Path,
    tllogic_file: Path | None,
    net_file: Path,
    guess_crossings: bool = False,
) -> tuple[bool, list[str]]:
    netconvert = shutil.which("netconvert")
    if not netconvert:
        return False, []
    command = [
        netconvert,
        "--node-files",
        str(node_file),
        "--edge-files",
        str(edge_file),
        "--connection-files",
        str(connection_file),
        "--type-files",
        str(type_file),
        "--no-turnarounds",
    ]
    if guess_crossings:
        command.extend(["--crossings.guess", "--walkingareas"])
    command.extend(["--output-file", str(net_file)])
    if tllogic_file and not guess_crossings:
        command.extend(["--tllogic-files", str(tllogic_file)])
    result = subprocess.run(command, capture_output=True, text=True, timeout=30)
    return result.returncode == 0, _warning_lines(result.stdout, result.stderr)


def _warning_lines(*texts: str) -> list[str]:
    return [line.strip() for text in texts for line in text.splitlines() if line.strip().startswith("Warning:")]


def _write_xml(path: Path, root: ET.Element) -> None:
    ET.ElementTree(root).write(path, encoding="utf-8", xml_declaration=True)


def needs_sumo_crossing(ir: IntersectionIR) -> bool:
    if any(
        "pedestrian" in modes
        for approach in ir.approaches
        for modes in [*approach.incoming_extra_lane_modes, *approach.outgoing_extra_lane_modes]
    ):
        return True
    crossing_points = {
        (node.x or 0.0, node.y or 0.0)
        for node in ir.osm_patch.nodes.values()
        if node.tags.get("highway") == "crossing" or "crossing" in node.tags
    }
    return any(
        "pedestrian" in approach.allowed_modes
        and "passenger" not in approach.allowed_modes
        and any(point in crossing_points for point in approach.source_shape_xy)
        for approach in ir.approaches
    )
