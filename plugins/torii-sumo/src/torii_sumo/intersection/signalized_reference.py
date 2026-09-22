from __future__ import annotations

import json
import math
import re
import shutil
import xml.etree.ElementTree as ET
from collections.abc import Callable
from pathlib import Path
from typing import Any

from torii_sumo.core.command_runner import CommandResult, run_command

from .nema_reference import _find_binary
from .scene_spec import IntersectionSceneSpec


SIM_END_S = 300
TLS_ID = "TLS0"


def build_signalized_intersection_reference(
    output_dir: Path,
    *,
    spec: IntersectionSceneSpec | dict[str, Any],
    prefix: str = "signalized_intersection_reference",
    run_sumo_smoke: bool = True,
    require_real_sumo: bool = False,
    command_runner: Callable[..., CommandResult] = run_command,
    which_func: Callable[[str], str | None] = shutil.which,
) -> dict[str, Any]:
    """Build a small, auditable TLS reference for the supported Phase 2 shapes.

    The four-way NEMA builder remains the default Phase 1 implementation.  This
    builder deliberately uses the same plain-XML -> netconvert -> SUMO path but
    makes topology, mode layers, ramp tagging, and controller semantics explicit
    in the manifest instead of hiding them in a hard-coded NEMA contract.
    """

    spec = IntersectionSceneSpec.model_validate(spec)
    output_dir.mkdir(parents=True, exist_ok=True)
    paths = _artifact_paths(output_dir, prefix)
    rows = _movement_rows(spec)
    _write_plain_network(paths, spec, rows)
    _write_tllogic(paths["tllogic_file"], spec, rows)
    _write_additional(paths["additional_file"], spec)

    netconvert = _find_binary("netconvert", which_func)
    if netconvert is None:
        return _blocked_report(paths, spec, "netconvert binary not found")
    netconvert_result = command_runner(
        [
            netconvert,
            "-n",
            str(paths["node_file"]),
            "-e",
            str(paths["edge_file"]),
            "-x",
            str(paths["connection_file"]),
            "-t",
            str(paths["type_file"]),
            "--tllogic-files",
            str(paths["tllogic_file"]),
            "--crossings.guess",
            "true",
            "--no-turnarounds",
            "true",
            "-o",
            str(paths["net_file"]),
        ],
        timeout_seconds=60.0,
    )
    _write_json(paths["netconvert_report_file"], netconvert_result.to_dict())
    if netconvert_result.status != "pass" or not paths["net_file"].is_file():
        return _failed_report(paths, spec, "netconvert failed or did not create .net.xml", netconvert_result.to_dict())

    try:
        audit = _audit_network(paths["net_file"], paths["additional_file"], spec, rows)
    except (ET.ParseError, AssertionError, KeyError, ValueError) as exc:
        return _failed_report(paths, spec, f"generic TLS audit failed: {exc}", netconvert_result.to_dict())
    _write_json(paths["audit_file"], audit)
    _write_routes(paths["route_file"], spec)
    _write_config(paths["sumocfg_file"], paths)
    _write_evidence(paths["evidence_file"], spec, paths["audit_file"])

    sumo_smoke_status = "skipped"
    sumo_result: dict[str, Any] = {}
    if run_sumo_smoke:
        sumo = _find_binary("sumo", which_func)
        if sumo is None:
            if require_real_sumo:
                return _blocked_report(paths, spec, "sumo binary not found", audit=audit, netconvert_status="pass")
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
            if result.status != "pass" or _has_error_lines(paths["sumo_error_log_file"]):
                return _failed_report(
                    paths,
                    spec,
                    "SUMO smoke failed or wrote an error log",
                    netconvert_result.to_dict(),
                    audit=audit,
                    sumo_result=sumo_result,
                )
            sumo_smoke_status = "pass"

    return {
        **_base_report(paths),
        "status": "pass",
        "claim_status": "diagnostic-demo",
        "netconvert_status": "pass",
        "sumo_smoke_status": sumo_smoke_status,
        "controlled_link_count": audit["controlled_link_count"],
        "tls_signal_group_count": audit["tls_signal_group_count"],
        "phase_order": audit["phase_order"],
        "controller": spec.controller,
        "tls_semantics": spec.tls_semantics,
        "feature_contract": audit["feature_contract"],
        "warnings": _sumo_warnings(sumo_result),
        "netconvert_report_file": str(paths["netconvert_report_file"]),
        "sumo_report_file": str(paths["sumo_report_file"]) if paths["sumo_report_file"].is_file() else "",
        "sumo_result": sumo_result,
    }


def _artifact_paths(output_dir: Path, prefix: str) -> dict[str, Path]:
    return {
        "output_dir": output_dir,
        "node_file": output_dir / f"{prefix}.nod.xml",
        "edge_file": output_dir / f"{prefix}.edg.xml",
        "connection_file": output_dir / f"{prefix}.con.xml",
        "type_file": output_dir / f"{prefix}.typ.xml",
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
        "workflow": "signalized_intersection_reference",
        "output_dir": str(paths["output_dir"]),
        **{key: str(paths[key]) for key in (
            "node_file",
            "edge_file",
            "connection_file",
            "type_file",
            "tllogic_file",
            "additional_file",
            "net_file",
            "route_file",
            "sumocfg_file",
            "audit_file",
            "evidence_file",
        )},
    }


def _blocked_report(
    paths: dict[str, Path],
    spec: IntersectionSceneSpec,
    error: str,
    *,
    audit: dict[str, Any] | None = None,
    netconvert_status: str = "blocked",
) -> dict[str, Any]:
    return {
        **_base_report(paths),
        "status": "blocked",
        "claim_status": "blocked",
        "netconvert_status": netconvert_status,
        "sumo_smoke_status": "blocked",
        "controlled_link_count": (audit or {}).get("controlled_link_count", 0),
        "tls_signal_group_count": (audit or {}).get("tls_signal_group_count", 0),
        "phase_order": (audit or {}).get("phase_order", []),
        "controller": spec.controller,
        "tls_semantics": spec.tls_semantics,
        "error": error,
    }


def _failed_report(
    paths: dict[str, Path],
    spec: IntersectionSceneSpec,
    error: str,
    netconvert_result: dict[str, Any],
    *,
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
        "phase_order": (audit or {}).get("phase_order", []),
        "controller": spec.controller,
        "tls_semantics": spec.tls_semantics,
        "error": error,
        "netconvert_result": netconvert_result,
        "sumo_result": sumo_result or {},
    }


def _labels(spec: IntersectionSceneSpec) -> tuple[str, ...]:
    if spec.approach_count == 4:
        return ("W", "N", "E", "S")
    return tuple(f"A{index}" for index in range(spec.approach_count))


def _coordinates(spec: IntersectionSceneSpec) -> dict[str, tuple[float, float]]:
    labels = _labels(spec)
    if spec.approach_count == 4:
        return {"W": (-spec.link_length_m, 0.0), "N": (0.0, spec.link_length_m), "E": (spec.link_length_m, 0.0), "S": (0.0, -spec.link_length_m)}
    return {
        label: (
            math.cos(2 * math.pi * index / spec.approach_count) * spec.link_length_m,
            math.sin(2 * math.pi * index / spec.approach_count) * spec.link_length_m,
        )
        for index, label in enumerate(labels)
    }


def _target_indices(spec: IntersectionSceneSpec, source_index: int) -> list[tuple[int, int, str]]:
    count = spec.approach_count
    if count == 3:
        return [((source_index + 1) % count, 0, "r"), ((source_index - 1) % count, 2, "l")]
    straight_offset = count // 2 if count % 2 == 0 else 2
    return [
        ((source_index + 1) % count, 0, "r"),
        ((source_index + straight_offset) % count, 1, "s"),
        ((source_index - 1) % count, 2, "l"),
    ]


def _movement_rows(spec: IntersectionSceneSpec) -> list[dict[str, Any]]:
    labels = _labels(spec)
    rows: list[dict[str, Any]] = []
    for source_index, source in enumerate(labels):
        for target_index, lane, turn in _target_indices(spec, source_index):
            target = labels[target_index]
            rows.append(
                {
                    "from": f"{source}_in",
                    "to": f"{target}_out",
                    "fromLane": str(lane),
                    "toLane": str(lane),
                    "turn": turn,
                    "mode": "passenger",
                    "source": source,
                }
            )
        if spec.bicycle_support:
            target_index = (source_index + 1) % len(labels)
            target = labels[target_index]
            rows.append(
                {
                    "from": f"{source}_in",
                    "to": f"{target}_out",
                    "fromLane": "3",
                    "toLane": "3",
                    "turn": "r",
                    "mode": "bicycle",
                    "source": source,
                }
            )
    for row_index, row in enumerate(rows):
        row["linkIndex"] = row_index
    return rows


def _write_plain_network(paths: dict[str, Path], spec: IntersectionSceneSpec, rows: list[dict[str, Any]]) -> None:
    coordinates = _coordinates(spec)
    nodes = ET.Element("nodes")
    ET.SubElement(nodes, "node", id=TLS_ID, x="0", y="0", type="traffic_light")
    if spec.pedestrian_crossing:
        ET.SubElement(nodes, "node", id="P0", x="0", y="0", type="priority")
    for label, (x, y) in coordinates.items():
        ET.SubElement(nodes, "node", id=label, x=f"{x:.2f}", y=f"{y:.2f}", type="priority")
    if spec.pedestrian_crossing:
        ET.SubElement(
            nodes,
            "crossing",
            node=TLS_ID,
            edges=" ".join(f"{label}_in" for label in _labels(spec)),
            width="4",
            priority="1",
        )

    edges = ET.Element("edges")
    lanes = 4 if spec.bicycle_support else 3
    for index, label in enumerate(_labels(spec)):
        edge_type = "ramp" if spec.ramp and index == 0 else "arterial"
        allow = "passenger bicycle" if spec.bicycle_support else "passenger"
        for suffix, source, target in (("in", label, TLS_ID), ("out", TLS_ID, label)):
            edge = ET.SubElement(
                edges,
                "edge",
                id=f"{label}_{suffix}",
                **{"from": source, "to": target},
                type=edge_type,
                priority="1" if edge_type == "ramp" else "2",
                numLanes=str(lanes),
                speed=f"{spec.speed_mps:.2f}",
                length=f"{spec.link_length_m:.2f}",
                allow=allow,
            )
            for lane_index in range(lanes):
                lane_allow = "bicycle" if spec.bicycle_support and lane_index == 3 else allow
                ET.SubElement(edge, "lane", index=str(lane_index), allow=lane_allow)

    if spec.pedestrian_crossing:
        for label in _labels(spec):
            for suffix, source, target in (("in", label, "P0"), ("out", "P0", label)):
                ET.SubElement(
                    edges,
                    "edge",
                    id=f"p_{label}_{suffix}",
                    **{"from": source, "to": target},
                    type="walkway",
                    priority="1",
                    numLanes="1",
                    speed="1.40",
                    allow="pedestrian",
                )

    connections = ET.Element("connections")
    for row in rows:
        ET.SubElement(
            connections,
            "connection",
            **{
                "from": row["from"],
                "to": row["to"],
                "fromLane": row["fromLane"],
                "toLane": row["toLane"],
                "dir": row["turn"],
            },
        )
    if spec.pedestrian_crossing:
        labels = _labels(spec)
        for source in labels:
            for target in labels:
                if source != target:
                    ET.SubElement(
                        connections,
                        "connection",
                        **{
                            "from": f"p_{source}_in",
                            "to": f"p_{target}_out",
                            "fromLane": "0",
                            "toLane": "0",
                            "dir": "s",
                        },
                    )

    types = ET.Element("types")
    ET.SubElement(types, "type", id="arterial", priority="2", numLanes="3", speed=f"{spec.speed_mps:.2f}")
    ET.SubElement(types, "type", id="ramp", priority="1", numLanes="3", speed=f"{spec.speed_mps:.2f}")
    ET.SubElement(types, "type", id="walkway", priority="1", numLanes="1", speed="1.40", allow="pedestrian")
    _write_xml(paths["node_file"], nodes)
    _write_xml(paths["edge_file"], edges)
    _write_xml(paths["connection_file"], connections)
    _write_xml(paths["type_file"], types)


def _controller_type(spec: IntersectionSceneSpec) -> str:
    return "actuated" if spec.controller == "actuated" else "static"


def _phase_groups(spec: IntersectionSceneSpec, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    phases: list[dict[str, Any]] = []
    for source in _labels(spec):
        source_rows = [row for row in rows if row["source"] == source]
        green_state = ["r"] * len(rows)
        yellow_state = ["r"] * len(rows)
        for row in source_rows:
            index = int(row["linkIndex"])
            green_state[index] = "g" if spec.controller == "protected_permissive" and row["turn"] == "l" else "G"
            yellow_state[index] = "y"
        phases.append({"name": f"{source}_green", "duration": "20", "state": "".join(green_state)})
        phases.append({"name": f"{source}_yellow", "duration": "3", "state": "".join(yellow_state)})
    return phases


def _write_tllogic(path: Path, spec: IntersectionSceneSpec, rows: list[dict[str, Any]]) -> None:
    root = ET.Element("tlLogics")
    logic = ET.SubElement(root, "tlLogic", id=TLS_ID, offset="0", programID="0", type=_controller_type(spec))
    for key, value in {
        "controller": spec.controller,
        "topology": spec.topology,
        "approach-count": str(spec.approach_count),
        "pedestrian-crossing": str(spec.pedestrian_crossing).lower(),
        "bicycle-support": str(spec.bicycle_support).lower(),
        "ramp": str(spec.ramp).lower(),
    }.items():
        ET.SubElement(logic, "param", key=key, value=value)
    for phase in _phase_groups(spec, rows):
        attrs = {"duration": phase["duration"], "state": phase["state"], "name": phase["name"]}
        if spec.controller == "actuated":
            attrs.update(minDur="5", maxDur="30", vehext="2")
        ET.SubElement(logic, "phase", **attrs)
    for row in rows:
        ET.SubElement(
            root,
            "connection",
            **{
                "from": row["from"],
                "to": row["to"],
                "fromLane": row["fromLane"],
                "toLane": row["toLane"],
                "tl": TLS_ID,
                "linkIndex": str(row["linkIndex"]),
            },
        )
    _write_xml(path, root)


def _write_additional(path: Path, spec: IntersectionSceneSpec) -> None:
    root = ET.Element("additional")
    logic = ET.SubElement(root, "tlLogic", id=TLS_ID, offset="0", programID="0", type=_controller_type(spec))
    ET.SubElement(logic, "param", key="torii-controller", value=spec.controller)
    ET.SubElement(logic, "param", key="torii-topology", value=spec.topology)
    ET.SubElement(logic, "param", key="torii-feature-review", value="pedestrian,bicycle,ramp")
    _write_xml(path, root)


def _write_routes(path: Path, spec: IntersectionSceneSpec) -> None:
    labels = _labels(spec)
    source, target = spec.smoke_route
    if source not in labels or target not in labels or source == target:
        source, target = labels[0], labels[1]
    routes = ET.Element("routes")
    ET.SubElement(routes, "vType", id="car", vClass="passenger", accel="2.0", decel="4.5", sigma="0.5", length="5", minGap="2.5")
    route_id = "car_smoke_route"
    ET.SubElement(routes, "route", id=route_id, edges=f"{source}_in {target}_out")
    ET.SubElement(routes, "vehicle", id="car_smoke", type="car", route=route_id, depart="0")
    if spec.bicycle_support:
        ET.SubElement(routes, "vType", id="bike", vClass="bicycle", accel="1.5", decel="3.0", sigma="0.5", length="1.8", minGap="1.0")
        ET.SubElement(routes, "vehicle", id="bike_smoke", type="bike", route=route_id, depart="5")
    if spec.pedestrian_crossing:
        ET.SubElement(
            routes,
            "person",
            id="ped_smoke",
            depart="10",
        )
        person = routes[-1]
        ET.SubElement(person, "walk", edges=f"p_{source}_in p_{target}_out")
    _write_xml(path, routes)


def _write_config(path: Path, paths: dict[str, Path]) -> None:
    root = ET.Element("configuration")
    inputs = ET.SubElement(root, "input")
    ET.SubElement(inputs, "net-file", value=paths["net_file"].name)
    ET.SubElement(inputs, "route-files", value=paths["route_file"].name)
    outputs = ET.SubElement(root, "output")
    ET.SubElement(outputs, "summary-output", value=paths["summary_file"].name)
    ET.SubElement(outputs, "tripinfo-output", value=paths["tripinfo_file"].name)
    time = ET.SubElement(root, "time")
    ET.SubElement(time, "begin", value="0")
    ET.SubElement(time, "end", value=str(SIM_END_S))
    processing = ET.SubElement(root, "processing")
    ET.SubElement(processing, "time-to-teleport", value="-1")
    report = ET.SubElement(root, "report")
    ET.SubElement(report, "duration-log.statistics", value="true")
    ET.SubElement(report, "no-step-log", value="true")
    _write_xml(path, root)


def _audit_network(path: Path, additional_path: Path, spec: IntersectionSceneSpec, rows: list[dict[str, Any]]) -> dict[str, Any]:
    root = ET.parse(path).getroot()
    logic = root.find(f"tlLogic[@id='{TLS_ID}']")
    if logic is None:
        raise AssertionError(f"compiled net missing {TLS_ID} tlLogic")
    controlled = [connection for connection in root.findall("connection") if connection.attrib.get("tl") == TLS_ID]
    if not controlled:
        raise AssertionError("compiled net contains no controlled connections")
    link_indexes = [int(connection.attrib["linkIndex"]) for connection in controlled]
    if sorted(link_indexes) != list(range(len(controlled))):
        raise AssertionError("compiled TLS linkIndex values are not contiguous")
    phases = logic.findall("phase")
    phase_names = [phase.attrib.get("name", str(index)) for index, phase in enumerate(phases)]
    if not phases or any(len(phase.attrib.get("state", "")) < len(controlled) for phase in phases):
        raise AssertionError("compiled TLS phase state length is shorter than controlled link count")
    expected_by_key = {
        (row["from"], row["to"], row["fromLane"], row["toLane"]): row
        for row in rows
    }
    movement_map: list[dict[str, Any]] = []
    for connection in controlled:
        key = (
            connection.attrib["from"],
            connection.attrib["to"],
            connection.attrib["fromLane"],
            connection.attrib["toLane"],
        )
        source = expected_by_key.get(key, {})
        movement_map.append(
            {
                "linkIndex": int(connection.attrib["linkIndex"]),
                "from": key[0],
                "fromLane": key[2],
                "to": key[1],
                "toLane": key[3],
                "turn": source.get("turn", connection.attrib.get("dir", "")),
                "mode": source.get("mode", "passenger"),
                "sourceApproach": source.get("source", key[0].removesuffix("_in")),
            }
        )
    feature_contract = {
        "pedestrian_crossing": spec.pedestrian_crossing,
        "pedestrian_crossing_count": spec.approach_count if spec.pedestrian_crossing else 0,
        "bicycle_support": spec.bicycle_support,
        "bicycle_connection_count": sum(1 for row in movement_map if row["mode"] == "bicycle"),
        "ramp": spec.ramp,
        "ramp_approach": _labels(spec)[0] if spec.ramp else None,
    }
    return {
        "contract": "generic-tls/v1",
        "source_model": "SUMO plain XML plus explicit generic TLS phases",
        "net": str(path.resolve()),
        "additional": str(additional_path.resolve()),
        "tls_id": TLS_ID,
        "controller": spec.controller,
        "tls_semantics": spec.tls_semantics,
        "topology": spec.topology,
        "approach_count": spec.approach_count,
        "controlled_link_count": len(controlled),
        "tls_signal_group_count": len({row["sourceApproach"] for row in movement_map}),
        "phase_order": phase_names,
        "phase_states": [phase.attrib.get("state", "") for phase in phases],
        "params": {param.attrib["key"]: param.attrib["value"] for param in logic.findall("param")},
        "movement_map": sorted(movement_map, key=lambda row: row["linkIndex"]),
        "feature_contract": feature_contract,
    }


def _write_evidence(path: Path, spec: IntersectionSceneSpec, audit_path: Path) -> None:
    path.write_text(
        "\n".join(
            [
                "# Generic Signalized Intersection Reference",
                "",
                f"Topology: `{spec.topology}` ({spec.approach_count} approaches).",
                f"Controller: `{spec.controller}` / `{spec.tls_semantics}`.",
                f"Modes: `{', '.join(sorted(spec.allowed_modes))}`.",
                f"Pedestrian crossing: `{spec.pedestrian_crossing}`; bicycle support: `{spec.bicycle_support}`; ramp: `{spec.ramp}`.",
                "",
                "The network is generated as plain SUMO XML, normalized by netconvert, loaded by SUMO, and audited from the compiled network.",
                f"Audit: `{audit_path.name}`.",
                "",
                "`diagnostic-demo`: synthetic semantics require later field calibration before operational use.",
                "",
            ]
        ),
        encoding="utf-8",
    )


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def _write_xml(path: Path, root: ET.Element) -> None:
    tree = ET.ElementTree(root)
    ET.indent(tree, space="  ")
    tree.write(path, encoding="utf-8", xml_declaration=True)


def _nonempty(path: Path) -> bool:
    return path.is_file() and path.stat().st_size > 0


def _has_error_lines(path: Path) -> bool:
    if not path.is_file():
        return False
    return any(
        line.strip() and not line.lstrip().startswith("Warning:") and re.search(r"\b(?:error|fatal)\b", line, re.IGNORECASE)
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines()
    )


def _sumo_warnings(result: dict[str, Any]) -> list[str]:
    return [
        line.strip()
        for line in str(result.get("stderr", "")).splitlines()
        if line.strip()
    ]
