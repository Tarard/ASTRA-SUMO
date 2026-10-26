"""INT direction consistency (M3): SUMO connection ``dir`` against the edge angles netconvert derives it from.

netconvert labels a connection in ``NBNode::getDirection`` from ``NBEdge::getAngleAtNode``: the last segment of the
incoming edge's geometry and the first segment of the outgoing edge's geometry (no lookahead, no lane shapes;
SUMO 1.27.1 ``src/netbuild/NBNode.cpp``, ``NBEdge.cpp``). In a ``.net.xml`` that geometry is the edge ``shape``
attribute, or the straight line between the two junction positions when the attribute is absent. Lane terminal
segments are cut at junction borders and can bend or even reverse on very short edges, so they are not used.

Only definite geometry turns that contradict ``dir`` are findings; partial directions (``L``/``R``) are compared by
side, and ambiguous geometry is reported, never flagged. A clean network can still hold findings, so a gate uses
``new_direction_findings`` against the frozen source baseline instead of requiring zero.
"""

from __future__ import annotations

import hashlib
import json
import math
import xml.etree.ElementTree as ET

from ..core.standard_nema_binding import _normalize_signed_angle, _turn_from_delta
from .ids import connection_key
from .junction_integrity import remap_lane

RULE = "sumo_edge_node_angle"
RULE_REASON = ("delta = heading of the outgoing edge's first geometry segment minus heading of the incoming edge's "
               "last geometry segment (left positive), as NBNode::getDirection computes it from getAngleAtNode; "
               "classified with standard_nema_binding._turn_from_delta (s <= 35, l/r 45-135, t >= 145 degrees)")
# SUMO dir -> geometry classes it cannot have. SUMO labels a sharp right (> 90 degrees) r and a left up to
# -170 degrees l, gives a turnaround t from the pairing of reverse edges (angle > 120) and L/R only below 90 degrees.
CONTRADICTS = {"s": {"l", "r", "t"}, "l": {"s", "r"}, "L": {"r", "t"}, "r": {"s", "l"}, "R": {"l", "t"},
               "t": {"s"}, "T": {"s"}}
_NON_ROAD = {"internal", "crossing", "walkingarea"}


def _points(value: str | None) -> list[tuple[float, float]]:
    result: list[tuple[float, float]] = []
    for token in (value or "").split():
        x, y = token.split(",")[:2]
        point = (float(x), float(y))
        if not result or result[-1] != point:
            result.append(point)
    return result


def _heading(first: tuple[float, float], second: tuple[float, float]) -> float:
    return math.degrees(math.atan2(second[1] - first[1], second[0] - first[0]))


def edge_geometries(root: ET.Element) -> dict[str, list[tuple[float, float]]]:
    """netconvert's edge geometry per road edge: ``shape`` or the line between its junction positions."""
    junctions = {row.get("id"): row for row in root.findall("junction")}
    result = {}
    for edge in root.findall("edge"):
        if edge.get("function") in _NON_ROAD:
            continue
        shape = _points(edge.get("shape"))
        if len(shape) < 2:
            ends = [junctions.get(edge.get(name)) for name in ("from", "to")]
            shape = _points(" ".join(f"{row.get('x')},{row.get('y')}" for row in ends)) if all(
                row is not None and row.get("x") is not None and row.get("y") is not None for row in ends) else []
        result[edge.get("id")] = shape
    return result


def check_connection_directions(root: ET.Element) -> dict:
    """Compare every road-to-road connection's ``dir`` with the SUMO edge-angle geometry."""
    geometry = edge_geometries(root)
    edges = {edge.get("id"): edge for edge in root.findall("edge")}
    roundabout = {edge for row in root.findall("roundabout") for edge in row.get("edges", "").split()}
    findings, ambiguous, unsupported, checked = [], [], [], 0
    for connection in root.findall("connection"):
        start, end = connection.get("from"), connection.get("to")
        if start not in geometry or end not in geometry:
            continue
        key, label = connection_key(connection), connection.get("dir")
        row = {"id": f"dir:{key}", "junction_id": edges[start].get("to"), "dir": label,
               "connection": [start, connection.get("fromLane"), end, connection.get("toLane")]}
        if start in roundabout and end in roundabout:
            unsupported.append({**row, "reason": "roundabout_links_are_straight_by_definition"})
            continue
        if label not in CONTRADICTS:
            unsupported.append({**row, "reason": "dir_label_not_compared"})
            continue
        incoming, outgoing = geometry[start], geometry[end]
        if len(incoming) < 2 or len(outgoing) < 2:
            unsupported.append({**row, "reason": "edge_geometry_degenerate"})
            continue
        checked += 1
        delta = _normalize_signed_angle(_heading(outgoing[0], outgoing[1]) - _heading(incoming[-2], incoming[-1]))
        turn = _turn_from_delta(delta)
        row.update(geometry_turn=turn, turn_angle_deg=round(delta, 3))
        if turn == "ambiguous":
            ambiguous.append(row)
        elif turn in CONTRADICTS[label]:
            findings.append({**row, "reason": f"dir_{label}_contradicts_edge_angle_{turn}"})
    return {"rule": RULE, "rule_reason": RULE_REASON, "checked_count": checked, "finding_count": len(findings),
            "ambiguous_count": len(ambiguous), "unsupported_count": len(unsupported), "findings": findings,
            "ambiguous": ambiguous, "unsupported": unsupported,
            "scope": "connections between two road edges; walking-area, crossing and internal links are excluded"}


def direction_baseline(report: dict, net_sha256: str) -> dict:
    """Freeze one source's findings, each with its ``[dir, geometry turn]`` label, so later gates compare
    differentially: a connection the source already flags is still new when its contradicting label changes."""
    finding_ids = sorted(row["id"] for row in report["findings"])
    labels = {row["id"]: [row["dir"], row["geometry_turn"]] for row in report["findings"]}
    payload = {"rule": report["rule"], "net_sha256": net_sha256, "finding_ids": finding_ids,
               "finding_labels": dict(sorted(labels.items()))}
    return {**payload, "ambiguous_ids": sorted(row["id"] for row in report["ambiguous"]),
            "sha256": hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()}


def new_direction_findings(baseline: dict, candidate: dict, lane_index_mapping: dict | None = None) -> list[dict]:
    """Candidate findings whose (connection, dir, geometry turn) the source baseline does not hold;
    ``lane_index_mapping`` is {edge_id: {old: new}}.

    Baseline connections follow that remap lane by lane, as INT rows do (``junction_integrity.remap_lane``): a
    finding on a removed lane is dropped, so a surviving lane renumbered into the removed lane's index cannot
    inherit it and a wrong label on that survivor is new."""
    if baseline.get("rule") != candidate.get("rule"):
        raise ValueError("Direction findings compare only under the same rule.")
    mapping = lane_index_mapping or {}
    labels = baseline.get("finding_labels") or {}
    known = {}
    for identity in baseline["finding_ids"]:
        lanes = [remap_lane(lane, mapping) for lane in identity[len("dir:"):].split(">")]
        if None not in lanes:
            known["dir:" + ">".join(lanes)] = labels.get(identity)
    return [row for row in candidate["findings"]
            if row["id"] not in known or known[row["id"]] != [row["dir"], row["geometry_turn"]]]
