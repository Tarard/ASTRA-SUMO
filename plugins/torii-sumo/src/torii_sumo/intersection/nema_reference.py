from __future__ import annotations

import json
import shutil
import xml.etree.ElementTree as ET
from collections.abc import Callable
from pathlib import Path
from typing import Any

from torii_sumo.core.command_runner import CommandResult, run_command


RUN_NAME = "nema_four_way_reference"
SIM_END_S = 600
LINK_LENGTH_M = 180
SPEED_MPS = 13.89
LANES = 3
NEMA_PHASE_ORDER = ("1", "2", "3", "4", "5", "6", "7", "8")
LEFT_PHASES = {"1", "3", "5", "7"}
NEMA_PARAMS = {
    "detector-length": "20",
    "detector-length-leftTurnLane": "10",
    "total-cycle-length": "90",
    "ring1": "1,2,3,4",
    "ring2": "5,6,7,8",
    "barrierPhases": "2,6",
    "coordinate-mode": "false",
    "barrier2Phases": "4,8",
    "minRecall": "2,6",
    "maxRecall": "",
    "whetherOutputState": "true",
    "fixForceOff": "false",
}


def build_nema_four_way_reference(
    output_dir: Path,
    *,
    prefix: str = RUN_NAME,
    run_sumo_smoke: bool = True,
    require_real_sumo: bool = False,
    command_runner: Callable[..., CommandResult] = run_command,
    which_func: Callable[[str], str | None] = shutil.which,
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    paths = _artifact_paths(output_dir, prefix)

    _write_plain_network(paths["node_file"], paths["edge_file"], paths["connection_file"])
    _write_tllogic(paths["tllogic_file"])
    _write_nema_additional(paths["additional_file"])

    netconvert = _find_binary("netconvert", which_func)
    if netconvert is None:
        return _blocked_report(paths, "netconvert binary not found", "blocked")

    netconvert_result = command_runner(
        [
            netconvert,
            "-n",
            str(paths["node_file"]),
            "-e",
            str(paths["edge_file"]),
            "-x",
            str(paths["connection_file"]),
            "--tllogic-files",
            str(paths["tllogic_file"]),
            "-o",
            str(paths["net_file"]),
            "--no-turnarounds",
            "true",
            "--tls.cycle.time",
            "90",
        ],
        timeout_seconds=30.0,
    )
    _write_json(paths["netconvert_report_file"], netconvert_result.to_dict())
    if netconvert_result.status != "pass" or not paths["net_file"].is_file():
        return _failed_report(paths, "netconvert failed or did not create .net.xml", netconvert_result.to_dict())

    try:
        audit = _audit_nema(paths["net_file"], paths["additional_file"])
    except (ET.ParseError, AssertionError, KeyError, ValueError) as exc:
        return _failed_report(paths, f"NEMA audit failed: {exc}", netconvert_result.to_dict())
    _write_json(paths["audit_file"], audit)

    _write_routes(paths["route_file"])
    _write_config(paths["sumocfg_file"], paths["net_file"], paths["route_file"], paths["summary_file"], paths["tripinfo_file"])
    _write_evidence(paths["evidence_file"], paths["audit_file"])

    sumo_smoke_status = "skipped"
    sumo_result: dict[str, Any] | None = None
    if run_sumo_smoke:
        sumo = _find_binary("sumo", which_func)
        if sumo is None:
            if require_real_sumo:
                return _blocked_report(paths, "sumo binary not found", "blocked", audit=audit, netconvert_status="pass")
        else:
            result = command_runner(
                [
                    sumo,
                    "-c",
                    str(paths["sumocfg_file"]),
                    "--log",
                    str(paths["sumo_log_file"]),
                    "--error-log",
                    str(paths["sumo_error_log_file"]),
                ],
                timeout_seconds=90.0,
            )
            sumo_result = result.to_dict()
            _write_json(paths["sumo_report_file"], sumo_result)
            if result.status != "pass" or _nonempty(paths["sumo_error_log_file"]):
                return _failed_report(paths, "SUMO smoke failed or wrote an error log", netconvert_result.to_dict(), audit=audit, sumo_result=sumo_result)
            sumo_smoke_status = "pass"

    return {
        **_base_report(paths),
        "status": "pass",
        "claim_status": "diagnostic-demo",
        "netconvert_status": "pass",
        "sumo_smoke_status": sumo_smoke_status,
        "controlled_link_count": audit["controlled_link_count"],
        "tls_signal_group_count": audit["tls_signal_group_count"],
        "phase_order": list(NEMA_PHASE_ORDER),
        "nema_params": dict(NEMA_PARAMS),
        "netconvert_report_file": str(paths["netconvert_report_file"]),
        "sumo_report_file": str(paths["sumo_report_file"]) if paths["sumo_report_file"].is_file() else "",
        "sumo_result": sumo_result or {},
    }


def _artifact_paths(output_dir: Path, prefix: str) -> dict[str, Path]:
    return {
        "output_dir": output_dir,
        "node_file": output_dir / f"{prefix}.nod.xml",
        "edge_file": output_dir / f"{prefix}.edg.xml",
        "connection_file": output_dir / f"{prefix}.con.xml",
        "tllogic_file": output_dir / f"{prefix}.tll.xml",
        "additional_file": output_dir / f"{prefix}.add.xml",
        "net_file": output_dir / f"{prefix}.net.xml",
        "route_file": output_dir / f"{prefix}.rou.xml",
        "sumocfg_file": output_dir / f"{prefix}.sumocfg",
        "summary_file": output_dir / f"{prefix}_summary.xml",
        "tripinfo_file": output_dir / f"{prefix}_tripinfo.xml",
        "audit_file": output_dir / f"{prefix}_nema_audit.json",
        "evidence_file": output_dir / f"{prefix}_evidence.md",
        "netconvert_report_file": output_dir / f"{prefix}_netconvert_report.json",
        "sumo_report_file": output_dir / f"{prefix}_sumo_report.json",
        "sumo_log_file": output_dir / f"{prefix}_sumo.log",
        "sumo_error_log_file": output_dir / f"{prefix}_sumo_errors.log",
    }


def _base_report(paths: dict[str, Path]) -> dict[str, Any]:
    return {
        "workflow": "nema_four_way_reference",
        "output_dir": str(paths["output_dir"]),
        "node_file": str(paths["node_file"]),
        "edge_file": str(paths["edge_file"]),
        "connection_file": str(paths["connection_file"]),
        "tllogic_file": str(paths["tllogic_file"]),
        "additional_file": str(paths["additional_file"]),
        "net_file": str(paths["net_file"]),
        "route_file": str(paths["route_file"]),
        "sumocfg_file": str(paths["sumocfg_file"]),
        "audit_file": str(paths["audit_file"]),
        "evidence_file": str(paths["evidence_file"]),
    }


def _blocked_report(
    paths: dict[str, Path],
    error: str,
    claim_status: str,
    audit: dict[str, Any] | None = None,
    netconvert_status: str = "blocked",
) -> dict[str, Any]:
    return {
        **_base_report(paths),
        "status": "blocked",
        "claim_status": claim_status,
        "netconvert_status": netconvert_status,
        "sumo_smoke_status": "blocked",
        "controlled_link_count": (audit or {}).get("controlled_link_count", 0),
        "tls_signal_group_count": (audit or {}).get("tls_signal_group_count", 0),
        "phase_order": list(NEMA_PHASE_ORDER),
        "nema_params": dict(NEMA_PARAMS),
        "error": error,
    }


def _failed_report(
    paths: dict[str, Path],
    error: str,
    netconvert_result: dict[str, Any],
    audit: dict[str, Any] | None = None,
    sumo_result: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        **_base_report(paths),
        "status": "fail",
        "claim_status": "construction-invalid",
        "netconvert_status": str(netconvert_result.get("status", "fail")),
        "sumo_smoke_status": str((sumo_result or {}).get("status", "skipped")),
        "controlled_link_count": (audit or {}).get("controlled_link_count", 0),
        "tls_signal_group_count": (audit or {}).get("tls_signal_group_count", 0),
        "phase_order": list(NEMA_PHASE_ORDER),
        "nema_params": dict(NEMA_PARAMS),
        "error": error,
        "netconvert_result": netconvert_result,
        "sumo_result": sumo_result or {},
    }


def _write_plain_network(node_path: Path, edge_path: Path, connection_path: Path) -> None:
    nodes = ET.Element("nodes")
    for node_id, x, y, node_type in (
        ("J0", 0, 0, "traffic_light"),
        ("N", 0, LINK_LENGTH_M, "priority"),
        ("S", 0, -LINK_LENGTH_M, "priority"),
        ("E", LINK_LENGTH_M, 0, "priority"),
        ("W", -LINK_LENGTH_M, 0, "priority"),
    ):
        ET.SubElement(nodes, "node", id=node_id, x=str(x), y=str(y), type=node_type)

    edges = ET.Element("edges")
    for edge_id, src, dst, priority in (
        ("N_in", "N", "J0", 2),
        ("N_out", "J0", "N", 2),
        ("S_in", "S", "J0", 2),
        ("S_out", "J0", "S", 2),
        ("E_in", "E", "J0", 3),
        ("E_out", "J0", "E", 3),
        ("W_in", "W", "J0", 3),
        ("W_out", "J0", "W", 3),
    ):
        ET.SubElement(
            edges,
            "edge",
            id=edge_id,
            **{"from": src, "to": dst},
            priority=str(priority),
            numLanes=str(LANES),
            speed=f"{SPEED_MPS:.2f}",
            length=str(LINK_LENGTH_M),
        )

    connections = ET.Element("connections")
    for from_edge, right_edge, through_edge, left_edge in _approach_connections():
        ET.SubElement(connections, "connection", **{"from": from_edge, "to": right_edge, "fromLane": "0", "toLane": "0"})
        ET.SubElement(connections, "connection", **{"from": from_edge, "to": through_edge, "fromLane": "1", "toLane": "1"})
        ET.SubElement(connections, "connection", **{"from": from_edge, "to": left_edge, "fromLane": "2", "toLane": "2"})

    _write_xml(node_path, nodes)
    _write_xml(edge_path, edges)
    _write_xml(connection_path, connections)


def _approach_connections() -> tuple[tuple[str, str, str, str], ...]:
    return (
        ("W_in", "S_out", "E_out", "N_out"),
        ("E_in", "N_out", "W_out", "S_out"),
        ("S_in", "E_out", "N_out", "W_out"),
        ("N_in", "W_out", "S_out", "E_out"),
    )


def _phase_for_connection(connection: ET.Element) -> str:
    from_edge = connection.attrib["from"]
    is_left = connection.attrib.get("dir", "").lower() == "l" or connection.attrib.get("fromLane") == "2"
    if from_edge == "W_in":
        return "5" if is_left else "2"
    if from_edge == "E_in":
        return "1" if is_left else "6"
    if from_edge == "S_in":
        return "7" if is_left else "4"
    if from_edge == "N_in":
        return "3" if is_left else "8"
    raise ValueError(from_edge)


def _state_for_phase(phase: str) -> str:
    state = ["r"] * len(NEMA_PHASE_ORDER)
    state[int(phase) - 1] = "G"
    return "".join(state)


def _add_nema_tl_logic(parent: ET.Element) -> None:
    tl_logic = ET.SubElement(parent, "tlLogic", id="J0", offset="0", programID="NEMA", type="NEMA")
    for key, value in NEMA_PARAMS.items():
        ET.SubElement(tl_logic, "param", key=key, value=value)
    for phase in NEMA_PHASE_ORDER:
        ET.SubElement(
            tl_logic,
            "phase",
            duration="99",
            minDur="5",
            maxDur="20" if phase in LEFT_PHASES else "35",
            vehext="2",
            yellow="3",
            red="1",
            name=phase,
            state=_state_for_phase(phase),
        )


def _add_signal_group_connections(parent: ET.Element) -> None:
    for from_edge, right_edge, through_edge, left_edge in _approach_connections():
        for to_edge, from_lane, to_lane in (
            (right_edge, "0", "0"),
            (through_edge, "1", "1"),
            (left_edge, "2", "2"),
        ):
            connection = ET.Element("connection", **{"from": from_edge, "to": to_edge, "fromLane": from_lane, "toLane": to_lane})
            ET.SubElement(
                parent,
                "connection",
                **{
                    "from": from_edge,
                    "to": to_edge,
                    "fromLane": from_lane,
                    "toLane": to_lane,
                    "tl": "J0",
                    "linkIndex": str(int(_phase_for_connection(connection)) - 1),
                },
            )


def _write_tllogic(path: Path) -> None:
    root = ET.Element("tlLogics")
    _add_nema_tl_logic(root)
    _add_signal_group_connections(root)
    _write_xml(path, root)


def _write_nema_additional(path: Path) -> None:
    root = ET.Element("additional")
    _add_nema_tl_logic(root)
    _write_xml(path, root)


def _write_routes(path: Path) -> None:
    routes = ET.Element("routes")
    ET.SubElement(routes, "vType", id="car", accel="2.0", decel="4.5", sigma="0.5", length="5", minGap="2.5")
    for index, (route_id, edges) in enumerate(
        {
            "W_E_through": "W_in E_out",
            "E_W_through": "E_in W_out",
            "S_N_through": "S_in N_out",
            "N_S_through": "N_in S_out",
            "W_N_left": "W_in N_out",
            "E_S_left": "E_in S_out",
            "S_W_left": "S_in W_out",
            "N_E_left": "N_in E_out",
        }.items()
    ):
        ET.SubElement(routes, "route", id=route_id, edges=edges)
        ET.SubElement(routes, "flow", id=f"{route_id}_flow", type="car", route=route_id, begin=str(index * 5), end="300", period="30")
    _write_xml(path, routes)


def _write_config(config_path: Path, net_path: Path, route_path: Path, summary_path: Path, tripinfo_path: Path) -> None:
    config = ET.Element("configuration")
    inputs = ET.SubElement(config, "input")
    ET.SubElement(inputs, "net-file", value=net_path.name)
    ET.SubElement(inputs, "route-files", value=route_path.name)
    outputs = ET.SubElement(config, "output")
    ET.SubElement(outputs, "summary-output", value=summary_path.name)
    ET.SubElement(outputs, "tripinfo-output", value=tripinfo_path.name)
    time = ET.SubElement(config, "time")
    ET.SubElement(time, "begin", value="0")
    ET.SubElement(time, "end", value=str(SIM_END_S))
    processing = ET.SubElement(config, "processing")
    ET.SubElement(processing, "time-to-teleport", value="-1")
    report = ET.SubElement(config, "report")
    ET.SubElement(report, "duration-log.statistics", value="true")
    ET.SubElement(report, "no-step-log", value="true")
    _write_xml(config_path, config)


def _audit_nema(net_path: Path, additional_path: Path) -> dict[str, Any]:
    net_root = ET.parse(net_path).getroot()
    tl_logic = net_root.find("tlLogic[@id='J0']")
    if tl_logic is None:
        raise AssertionError("compiled net missing J0 NEMA tlLogic")
    if tl_logic.attrib != {"id": "J0", "offset": "0", "programID": "NEMA", "type": "NEMA"}:
        raise AssertionError(f"NEMA tlLogic attributes do not match: {tl_logic.attrib}")
    params = {param.attrib["key"]: param.attrib["value"] for param in tl_logic.findall("param")}
    if params != NEMA_PARAMS:
        raise AssertionError(f"NEMA params do not match: {params}")
    phases = tl_logic.findall("phase")
    expected_phases = [
        {
            "duration": "99",
            "minDur": "5",
            "maxDur": "20" if phase in LEFT_PHASES else "35",
            "vehext": "2",
            "yellow": "3",
            "red": "1",
            "name": phase,
            "state": _state_for_phase(phase),
        }
        for phase in NEMA_PHASE_ORDER
    ]
    if [phase.attrib for phase in phases] != expected_phases:
        raise AssertionError("compiled NEMA phases do not match the reference plan")

    controlled = [connection for connection in net_root.findall("connection") if connection.attrib.get("tl") == "J0"]
    expected_movements = set()
    for from_edge, right_edge, through_edge, left_edge in _approach_connections():
        for to_edge, lane, turn in ((right_edge, "0", "r"), (through_edge, "1", "s"), (left_edge, "2", "l")):
            phase = _phase_for_connection(ET.Element("connection", **{"from": from_edge, "fromLane": lane, "dir": turn}))
            expected_movements.add((from_edge, to_edge, lane, lane, turn, int(phase) - 1))
    actual_movements = {
        (
            connection.attrib["from"],
            connection.attrib["to"],
            connection.attrib["fromLane"],
            connection.attrib["toLane"],
            connection.attrib["dir"],
            int(connection.attrib["linkIndex"]),
        )
        for connection in controlled
    }
    if len(controlled) != len(expected_movements) or actual_movements != expected_movements:
        raise AssertionError("compiled NEMA controlled movements do not match the 12-movement reference map")

    movement_rows = []
    for connection in controlled:
        phase = _phase_for_connection(connection)
        link_index = int(connection.attrib["linkIndex"])
        expected_link_index = int(phase) - 1
        if link_index != expected_link_index:
            raise AssertionError(f"{connection.attrib} has linkIndex {link_index}, expected {expected_link_index}")
        state = phases[NEMA_PHASE_ORDER.index(phase)].attrib["state"]
        if state[link_index] != "G":
            raise AssertionError(f"{connection.attrib} is not green in phase {phase}")
        movement_rows.append(
            {
                "linkIndex": link_index,
                "from": connection.attrib["from"],
                "fromLane": connection.attrib["fromLane"],
                "to": connection.attrib["to"],
                "toLane": connection.attrib["toLane"],
                "turn": connection.attrib.get("dir", ""),
                "nemaPhase": phase,
            }
        )

    return {
        "source_model": "SUMO plain XML plus .tll signal groups and embedded NEMA",
        "net": str(net_path.resolve()),
        "additional": str(additional_path.resolve()),
        "tls_id": "J0",
        "controlled_link_count": len(controlled),
        "tls_signal_group_count": len({row["linkIndex"] for row in movement_rows}),
        "phase_order": list(NEMA_PHASE_ORDER),
        "params": dict(NEMA_PARAMS),
        "lane_rule": {"lane0": "right turn", "lane1": "through", "lane2": "protected left turn"},
        "movement_map": sorted(movement_rows, key=lambda row: (row["linkIndex"], row["from"], row["to"])),
    }


def _write_evidence(evidence_path: Path, audit_path: Path) -> None:
    evidence_path.write_text(
        "\n".join(
            [
                "# NEMA Four-Way Reference",
                "",
                "Single-intersection reference built with SUMO plain XML source files.",
                "",
                "- source files: `.nod.xml`, `.edg.xml`, `.con.xml`",
                "- signal groups: `.tll.xml` loaded by `netconvert`",
                "- generated network: `.net.xml` from `netconvert`",
                "- NEMA controller: embedded by `.tll.xml`; `.add.xml` is written as a reusable template",
                "- no direct hand-editing of `.net.xml` traffic-light logic",
                "",
                "NEMA settings: `total-cycle-length=90`, `ring1=1,2,3,4`, `ring2=5,6,7,8`, "
                "`barrierPhases=2,6`, `barrier2Phases=4,8`, `minRecall=2,6`, empty `maxRecall`, "
                "`yellow=3`, `red=1`, `minDur=5`, left `maxDur=20`, through `maxDur=35`.",
                "",
                f"Audit: `{audit_path.name}`",
                "",
                "`diagnostic-demo`: this is a calibration/reference network, not a calibrated field signal plan.",
                "",
            ]
        ),
        encoding="utf-8",
    )


def _find_binary(name: str, which_func: Callable[[str], str | None]) -> str | None:
    candidates = [name, f"{name}.exe"] if not name.endswith(".exe") else [name]
    for candidate in candidates:
        if path := which_func(candidate):
            return path
    for root in (Path(r"C:\Program Files\Eclipse\Sumo\bin"), Path(r"C:\Program Files (x86)\Eclipse\Sumo\bin")):
        for candidate in candidates:
            path = root / candidate
            if path.exists():
                return str(path)
    return None


def _nonempty(path: Path) -> bool:
    return path.is_file() and path.stat().st_size > 0


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _write_xml(path: Path, root: ET.Element) -> None:
    tree = ET.ElementTree(root)
    ET.indent(tree, space="  ")
    tree.write(path, encoding="utf-8", xml_declaration=True)
