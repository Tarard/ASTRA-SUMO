from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any, Iterable
import xml.etree.ElementTree as ET

from torii_sumo.road_semantics import classify_turn_direction as _shared_classify_turn_direction

from .modal_aggregation_policy import classify_edge_modal_role


def classify_turn_direction(in_axis: tuple[float, float], out_axis: tuple[float, float]) -> str:
    return _shared_classify_turn_direction(in_axis, out_axis)


def build_approach_model(net_file: Path, junction_id: str) -> dict[str, Any]:
    root = ET.parse(net_file).getroot()
    junctions = {
        junction.attrib["id"]: (float(junction.attrib.get("x", "0")), float(junction.attrib.get("y", "0")))
        for junction in root.findall("junction")
        if "id" in junction.attrib
    }
    approaches = []
    vehicle_approaches = []
    support_approaches = []
    for edge in root.findall("edge"):
        if _skip_edge(edge):
            continue
        from_node = edge.attrib.get("from", "")
        to_node = edge.attrib.get("to", "")
        if to_node == junction_id:
            direction = "incoming"
            approach_id = f"in:{edge.attrib.get('id', '')}"
        elif from_node == junction_id:
            direction = "outgoing"
            approach_id = f"out:{edge.attrib.get('id', '')}"
        else:
            continue
        info = _edge_info(edge, junctions, direction)
        modal = classify_edge_modal_role(info)
        approach = {
            "id": approach_id,
            "edge_id": info["id"],
            "direction": direction,
            "from": from_node,
            "to": to_node,
            "road_name": info["name"],
            "highway_type": info["type"],
            "permissions": info["permissions"],
            "axis": info["axis"],
            "lane_count": info["lane_count"],
            "modal_role": modal["modal_primary_role"],
            "modal_decision": modal["modal_aggregation_decision"],
        }
        approaches.append(approach)
        if modal["modal_primary_role"] == "vehicle_core":
            vehicle_approaches.append(approach)
        else:
            support_approaches.append(approach)
    return {
        "status": "pass",
        "claim_status": "diagnostic-demo",
        "net_file": str(net_file),
        "junction_id": junction_id,
        "approaches": approaches,
        "vehicle_approaches": vehicle_approaches,
        "support_approaches": support_approaches,
    }


def build_movement_graph(net_file: Path, junction_id: str) -> dict[str, Any]:
    model = build_approach_model(net_file, junction_id)
    incoming = [approach for approach in model["vehicle_approaches"] if approach["direction"] == "incoming"]
    outgoing = [approach for approach in model["vehicle_approaches"] if approach["direction"] == "outgoing"]
    movements = []
    for source in incoming:
        for target in outgoing:
            turn_class = classify_turn_direction(tuple(source["axis"]), tuple(target["axis"]))
            status, confidence, reason = _movement_status(source, target, turn_class)
            movements.append(
                {
                    "source_approach_id": source["id"],
                    "target_approach_id": target["id"],
                    "source_edge_id": source["edge_id"],
                    "target_edge_id": target["edge_id"],
                    "turn_class": turn_class,
                    "confidence": confidence,
                    "status": status,
                    "reason": reason,
                }
            )
    return {
        **model,
        "incoming_vehicle_approach_count": len(incoming),
        "outgoing_vehicle_approach_count": len(outgoing),
        "movement_count": len(movements),
        "movements": movements,
    }


def audit_movement_graph(graph: dict[str, Any]) -> dict[str, Any]:
    issues = []
    if int(graph.get("incoming_vehicle_approach_count", 0)) == 0:
        issues.append(_issue("no_incoming_vehicle_approaches", "junction has no incoming vehicle approaches"))
    if int(graph.get("outgoing_vehicle_approach_count", 0)) == 0:
        issues.append(_issue("no_outgoing_vehicle_approaches", "junction has no outgoing vehicle approaches"))

    seen: set[tuple[str, str]] = set()
    for movement in graph.get("movements", []) or []:
        pair = (str(movement.get("source_approach_id", "")), str(movement.get("target_approach_id", "")))
        if pair in seen:
            issues.append(_issue("duplicate_movement", f"duplicate movement {pair[0]} -> {pair[1]}"))
        seen.add(pair)
        if float(movement.get("confidence", 0.0)) < 0.5 or movement.get("status") == "needs_review":
            issues.append(_issue("low_confidence_movement", f"movement {pair[0]} -> {pair[1]} needs review"))
        if movement.get("turn_class") == "u_turn":
            issues.append(_issue("u_turn_without_explicit_reason", f"u-turn {pair[0]} -> {pair[1]} needs review"))

    return {
        "status": "pass" if not issues else "review",
        "claim_status": "diagnostic-demo",
        "junction_id": graph.get("junction_id", ""),
        "issue_count": len(issues),
        "issues": issues,
    }


def write_movement_review(
    graph: dict[str, Any],
    audit: dict[str, Any],
    output_dir: Path,
    prefix: str,
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    graph_file = output_dir / f"{prefix}_movement_graph.json"
    audit_file = output_dir / f"{prefix}_movement_audit.json"
    approaches_file = output_dir / f"{prefix}_approaches.csv"
    movements_file = output_dir / f"{prefix}_movements.csv"
    graph_file.write_text(json.dumps(graph, indent=2, ensure_ascii=False), encoding="utf-8")
    audit_file.write_text(json.dumps(audit, indent=2, ensure_ascii=False), encoding="utf-8")
    _write_csv(
        approaches_file,
        [
            "id",
            "edge_id",
            "direction",
            "from",
            "to",
            "road_name",
            "highway_type",
            "lane_count",
            "modal_role",
            "modal_decision",
            "axis",
            "allow",
            "disallow",
        ],
        [_approach_csv_row(approach) for approach in graph.get("approaches", []) or []],
    )
    _write_csv(
        movements_file,
        [
            "source_approach_id",
            "target_approach_id",
            "source_edge_id",
            "target_edge_id",
            "turn_class",
            "confidence",
            "status",
            "reason",
        ],
        list(graph.get("movements", []) or []),
    )
    return {
        "status": "pass",
        "claim_status": "diagnostic-demo",
        "movement_graph_file": str(graph_file),
        "movement_audit_file": str(audit_file),
        "approaches_file": str(approaches_file),
        "movements_file": str(movements_file),
    }


def _skip_edge(edge: ET.Element) -> bool:
    edge_id = edge.attrib.get("id", "")
    return edge_id.startswith(":") or edge.attrib.get("function") == "internal"


def _edge_info(
    edge: ET.Element,
    junctions: dict[str, tuple[float, float]],
    direction: str,
) -> dict[str, Any]:
    lanes = edge.findall("lane")
    shape = _shape(edge, lanes)
    from_node = edge.attrib.get("from", "")
    to_node = edge.attrib.get("to", "")
    if len(shape) < 2 and from_node in junctions and to_node in junctions:
        shape = [junctions[from_node], junctions[to_node]]
    axis = _axis(shape, direction)
    return {
        "id": edge.attrib.get("id", ""),
        "from": from_node,
        "to": to_node,
        "type": edge.attrib.get("type", ""),
        "function": edge.attrib.get("function", ""),
        "allow": " ".join(_tokens([edge.attrib.get("allow", ""), *(lane.attrib.get("allow", "") for lane in lanes)])),
        "disallow": " ".join(
            _tokens([edge.attrib.get("disallow", ""), *(lane.attrib.get("disallow", "") for lane in lanes)])
        ),
        "name": edge.attrib.get("name", ""),
        "lane_count": len(lanes),
        "permissions": {
            "allow": _tokens([edge.attrib.get("allow", ""), *(lane.attrib.get("allow", "") for lane in lanes)]),
            "disallow": _tokens([edge.attrib.get("disallow", ""), *(lane.attrib.get("disallow", "") for lane in lanes)]),
        },
        "axis": axis,
    }


def _shape(edge: ET.Element, lanes: list[ET.Element]) -> list[tuple[float, float]]:
    raw = edge.attrib.get("shape") or (lanes[0].attrib.get("shape", "") if lanes else "")
    points = []
    for item in raw.split():
        try:
            x, y = item.split(",", 1)
            points.append((float(x), float(y)))
        except ValueError:
            continue
    return points


def _axis(shape: list[tuple[float, float]], direction: str) -> tuple[float, float]:
    if len(shape) < 2:
        return (0.0, 0.0)
    start, end = shape[0], shape[-1]
    if direction == "incoming":
        return (end[0] - start[0], end[1] - start[1])
    return (end[0] - start[0], end[1] - start[1])


def _tokens(values: Iterable[str]) -> list[str]:
    return sorted({token for value in values for token in str(value or "").split() if token})


def _movement_status(source: dict[str, Any], target: dict[str, Any], turn_class: str) -> tuple[str, float, str]:
    if turn_class in {"u_turn", "unknown"}:
        return "needs_review", 0.25, f"{turn_class} movement requires explicit review"
    if source["road_name"] and source["road_name"] == target["road_name"] and turn_class == "straight":
        return "emit", 0.95, "same-road continuation"
    if turn_class == "right":
        return "emit", 0.75, "conservative right-turn candidate"
    return "needs_review", 0.45, f"{turn_class} movement needs topology or reference evidence"


def _issue(code: str, message: str) -> dict[str, str]:
    return {"code": code, "message": message}


def _approach_csv_row(approach: dict[str, Any]) -> dict[str, Any]:
    permissions = approach.get("permissions", {}) or {}
    return {
        **approach,
        "axis": " ".join(str(item) for item in approach.get("axis", ())),
        "allow": " ".join(permissions.get("allow", []) or []),
        "disallow": " ".join(permissions.get("disallow", []) or []),
    }


def _write_csv(path: Path, fieldnames: list[str], rows: list[dict[str, Any]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
