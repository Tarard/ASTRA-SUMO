from __future__ import annotations

import csv
import hashlib
import json
import math
from collections import Counter
from pathlib import Path
from typing import Any, Mapping
import xml.etree.ElementTree as ET

from .osm_network import _parse_utm_zone, _utm_to_latlon, haversine_m


HIGH_HIERARCHY_TYPES = {
    "highway.motorway",
    "highway.motorway_link",
    "highway.trunk",
    "highway.trunk_link",
    "highway.primary",
    "highway.primary_link",
    "highway.secondary",
    "highway.secondary_link",
    "highway.tertiary",
    "highway.tertiary_link",
}


def audit_reference_hierarchy(
    *,
    reference_net_file: Path,
    candidate_net_file: Path,
    output_dir: Path,
    prefix: str = "reference_hierarchy",
    match_distance_m: float = 35.0,
    oversplit_length_ratio: float = 0.6,
    min_extra_edges: int = 10,
    resolve_equivalent_fragmentation: bool = False,
) -> dict[str, Any]:
    if match_distance_m <= 0:
        return _failure("match_distance_m must be positive")
    if not 0 < oversplit_length_ratio <= 1:
        return _failure("oversplit_length_ratio must be in (0, 1]")
    if min_extra_edges < 0:
        return _failure("min_extra_edges must be non-negative")
    if not reference_net_file.exists():
        return _failure(f"reference net file does not exist: {reference_net_file}")
    if not candidate_net_file.exists():
        return _failure(f"candidate net file does not exist: {candidate_net_file}")

    try:
        reference_edges = _read_edges(reference_net_file)
        candidate_edges = _read_edges(candidate_net_file)
    except (OSError, ET.ParseError, KeyError, ValueError) as exc:
        return _failure(f"{type(exc).__name__}: {exc}")

    output_dir.mkdir(parents=True, exist_ok=True)
    reference_high_edges = [edge for edge in reference_edges if _is_high_hierarchy_type(edge["type"])]
    candidate_high_edges = [edge for edge in candidate_edges if _is_high_hierarchy_type(edge["type"])]
    type_comparisons = _type_comparisons(
        reference_high_edges=reference_high_edges,
        candidate_high_edges=candidate_high_edges,
        min_extra_edges=min_extra_edges,
    )
    type_decisions = {row["edge_type"]: row["hierarchy_scope_decision"] for row in type_comparisons}
    candidate_cases = [
        _classify_candidate_edge(
            edge,
            reference_edges=reference_edges,
            reference_high_edges=reference_high_edges,
            type_decisions=type_decisions,
            match_distance_m=match_distance_m,
            oversplit_length_ratio=oversplit_length_ratio,
            resolve_equivalent_fragmentation=resolve_equivalent_fragmentation,
        )
        for edge in candidate_high_edges
    ]
    decision_counts = dict(sorted(Counter(case["hierarchy_decision"] for case in candidate_cases).items()))
    corridor_match_basis_counts = dict(
        sorted(Counter(case["corridor_match_basis"] for case in candidate_cases).items())
    )
    same_name_match_status_counts = dict(
        sorted(Counter(case["same_name_match_status"] for case in candidate_cases).items())
    )
    issue_count = sum(1 for case in candidate_cases if case["hierarchy_decision"] != "aligned")
    equivalent_fragmentation_count = sum(
        1 for case in candidate_cases if case.get("hierarchy_resolution") == "equivalent_fragmentation"
    )

    cases_file = _short_output_path(output_dir, prefix, "_high_hierarchy_cases.csv")
    type_comparison_file = _short_output_path(output_dir, prefix, "_high_hierarchy_type_comparison.csv")
    summary_file = _short_output_path(output_dir, prefix, "_reference_hierarchy_audit.json")
    _write_cases_csv(cases_file, candidate_cases)
    _write_type_comparison_csv(type_comparison_file, type_comparisons)

    status = "blocked" if issue_count else "pass"
    report = {
        "status": status,
        "claim_status": "blocked" if issue_count else "diagnostic-demo",
        "reference_hierarchy_status": "needs_review" if issue_count else "pass",
        "reference_net_file": str(reference_net_file),
        "candidate_net_file": str(candidate_net_file),
        "output_dir": str(output_dir),
        "match_distance_m": match_distance_m,
        "oversplit_length_ratio": oversplit_length_ratio,
        "min_extra_edges": min_extra_edges,
        "resolve_equivalent_fragmentation": resolve_equivalent_fragmentation,
        "reference_high_hierarchy_edge_count": len(reference_high_edges),
        "candidate_high_hierarchy_edge_count": len(candidate_high_edges),
        "high_hierarchy_issue_count": issue_count,
        "raw_high_hierarchy_issue_count": issue_count + equivalent_fragmentation_count,
        "equivalent_fragmentation_count": equivalent_fragmentation_count,
        "hierarchy_equivalence_policy": (
            "same_high_type_and_nearby_reference_or_same_name_nearby_reference"
            if resolve_equivalent_fragmentation
            else "disabled"
        ),
        "decision_counts": decision_counts,
        "corridor_match_basis_counts": corridor_match_basis_counts,
        "same_name_match_status_counts": same_name_match_status_counts,
        "type_comparisons": type_comparisons,
        "candidate_cases": candidate_cases,
        "cases_file": str(cases_file),
        "type_comparison_file": str(type_comparison_file),
        "summary_file": str(summary_file),
        "warnings": _warnings(issue_count),
    }
    summary_file.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    return report


def build_reference_hierarchy_type_repair_variant(
    *,
    candidate_net_file: Path,
    reference_hierarchy_report: Mapping[str, Any],
    output_dir: Path,
    prefix: str = "reference_hierarchy_type_repair",
    max_same_name_distance_m: float | None = None,
) -> dict[str, Any]:
    if not candidate_net_file.exists():
        return _failure(f"candidate net file does not exist: {candidate_net_file}")
    if max_same_name_distance_m is not None and max_same_name_distance_m <= 0:
        return _failure("max_same_name_distance_m must be positive")

    match_distance = (
        float(max_same_name_distance_m)
        if max_same_name_distance_m is not None
        else float(reference_hierarchy_report.get("match_distance_m", 35.0) or 35.0)
    )
    repair_rows = _type_repair_rows(reference_hierarchy_report, max_same_name_distance_m=match_distance)

    output_dir.mkdir(parents=True, exist_ok=True)
    plan_file = _short_output_path(output_dir, prefix, "_plan.json")
    repairs_file = _short_output_path(output_dir, prefix, "_repairs.csv")
    variant_file = _short_output_path(output_dir, prefix, "_type_repaired.net.xml")
    _write_type_repair_csv(repairs_file, repair_rows)
    plan_file.write_text(
        json.dumps(
            {
                "reference_hierarchy_type_repair_status": "planned_for_review_variant"
                if repair_rows
                else "not_needed",
                "candidate_net_file": str(candidate_net_file),
                "variant_file": str(variant_file) if repair_rows else "",
                "repair_count": len(repair_rows),
                "max_same_name_distance_m": match_distance,
                "review_policy": (
                    "copy high-road edge type from same-name reference geometry into a separate review variant; "
                    "do not overwrite the source network"
                ),
                "repairs": repair_rows,
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    if not repair_rows:
        return {
            "status": "pass",
            "claim_status": "diagnostic-demo",
            "reference_hierarchy_type_repair_status": "not_needed",
            "reference_hierarchy_type_repair_count": 0,
            "reference_hierarchy_type_repair_plan_file": str(plan_file),
            "reference_hierarchy_type_repair_repairs_file": str(repairs_file),
            "reference_hierarchy_type_repair_variant_file": "",
            "warnings": [],
        }

    try:
        tree = ET.parse(candidate_net_file)
    except (OSError, ET.ParseError) as exc:
        return _failure(f"{type(exc).__name__}: {exc}")
    repairs_by_edge = {row["candidate_edge_id"]: row for row in repair_rows}
    applied_count = 0
    for edge in tree.getroot().findall("edge"):
        edge_id = edge.attrib.get("id", "")
        repair = repairs_by_edge.get(edge_id)
        if repair is None:
            continue
        edge.set("type", repair["applied_edge_type"])
        applied_count += 1
    tree.write(variant_file, encoding="utf-8", xml_declaration=True)

    status = "pass" if applied_count == len(repair_rows) else "fail"
    warnings = ["reference hierarchy type repair variant requires SUMO load and Netedit review before adoption"]
    if status != "pass":
        warnings.append("some planned edge type repairs were not applied")
    return {
        "status": status,
        "claim_status": "blocked" if status == "pass" else "construction-invalid",
        "reference_hierarchy_type_repair_status": "variant_created_for_review" if status == "pass" else "failed",
        "reference_hierarchy_type_repair_count": applied_count,
        "reference_hierarchy_type_repair_plan_file": str(plan_file),
        "reference_hierarchy_type_repair_repairs_file": str(repairs_file),
        "reference_hierarchy_type_repair_variant_file": str(variant_file),
        "warnings": warnings,
    }


def _read_edges(net_file: Path) -> list[dict[str, Any]]:
    root = ET.parse(net_file).getroot()
    coordinate_converter = _coordinate_converter(root)
    junction_positions = {
        junction.attrib["id"]: (
            float(junction.attrib.get("x", "0") or 0),
            float(junction.attrib.get("y", "0") or 0),
        )
        for junction in root.findall("junction")
        if "id" in junction.attrib and not junction.attrib["id"].startswith(":")
    }
    edges = []
    for edge in root.findall("edge"):
        edge_id = edge.attrib.get("id", "")
        if not edge_id or edge_id.startswith(":") or edge.attrib.get("function") == "internal":
            continue
        lanes = edge.findall("lane")
        shape = _edge_shape(edge, lanes, junction_positions)
        geo_shape = [coordinate_converter(x, y) for x, y in shape] if coordinate_converter else []
        edge_name = _edge_name(edge)
        edges.append(
            {
                "id": edge_id,
                "from": edge.attrib.get("from", ""),
                "to": edge.attrib.get("to", ""),
                "type": edge.attrib.get("type", "<missing>") or "<missing>",
                "name": edge_name,
                "normalized_name": _normalized_name(edge_name),
                "length": _edge_length(lanes, shape),
                "shape": shape,
                "geo_shape": geo_shape,
                "center": _shape_center(shape),
            }
        )
    return edges


def _edge_name(edge: ET.Element) -> str:
    direct_name = edge.attrib.get("name", "")
    if direct_name:
        return direct_name
    for key in ("name", "ref"):
        value = _edge_param_value(edge, key)
        if value:
            return value
    return ""


def _edge_param_value(edge: ET.Element, key: str) -> str:
    for param in edge.findall("param"):
        if param.attrib.get("key") == key:
            return param.attrib.get("value", "")
    return ""


def _normalized_name(value: str) -> str:
    return " ".join(value.casefold().strip().split())


def _coordinate_converter(root: ET.Element):
    location = root.find("location")
    if location is None:
        return None
    offset_value = location.attrib.get("netOffset", "")
    proj_parameter = location.attrib.get("projParameter", "")
    if not offset_value or "+proj=utm" not in proj_parameter:
        return None
    try:
        x_offset, y_offset = (float(value) for value in offset_value.split(",", 1))
        zone = _parse_utm_zone(proj_parameter)
    except (TypeError, ValueError):
        return None

    def _convert(x: float, y: float) -> tuple[float, float]:
        return _utm_to_latlon(x - x_offset, y - y_offset, zone=zone, northern=True)

    return _convert


def _edge_shape(
    edge: ET.Element,
    lanes: list[ET.Element],
    junction_positions: dict[str, tuple[float, float]],
) -> list[tuple[float, float]]:
    for lane in lanes:
        parsed = _parse_shape(lane.attrib.get("shape", ""))
        if parsed:
            return parsed
    parsed = _parse_shape(edge.attrib.get("shape", ""))
    if parsed:
        return parsed
    from_node = edge.attrib.get("from", "")
    to_node = edge.attrib.get("to", "")
    if from_node in junction_positions and to_node in junction_positions:
        return [junction_positions[from_node], junction_positions[to_node]]
    return [(0.0, 0.0), (0.0, 0.0)]


def _parse_shape(value: str) -> list[tuple[float, float]]:
    points = []
    for raw_point in value.split():
        try:
            x, y = raw_point.split(",", 1)
            points.append((float(x), float(y)))
        except ValueError:
            continue
    return points


def _edge_length(lanes: list[ET.Element], shape: list[tuple[float, float]]) -> float:
    lengths = []
    for lane in lanes:
        try:
            lengths.append(float(lane.attrib.get("length", "0") or 0))
        except ValueError:
            pass
    return max(lengths, default=_polyline_length(shape))


def _polyline_length(shape: list[tuple[float, float]]) -> float:
    if len(shape) < 2:
        return 0.0
    return sum(_distance(a, b) for a, b in zip(shape, shape[1:]))


def _shape_center(shape: list[tuple[float, float]]) -> tuple[float, float]:
    if not shape:
        return (0.0, 0.0)
    return (
        sum(point[0] for point in shape) / len(shape),
        sum(point[1] for point in shape) / len(shape),
    )


def _is_high_hierarchy_type(edge_type: str) -> bool:
    return bool(_high_hierarchy_type_tokens(edge_type))


def _type_tokens(edge_type: str) -> set[str]:
    return {token.strip() for token in str(edge_type).split("|") if token.strip()}


def _high_hierarchy_type_tokens(edge_type: str) -> set[str]:
    return _type_tokens(edge_type) & HIGH_HIERARCHY_TYPES


def _has_matching_high_hierarchy_type(left_type: str, right_type: str) -> bool:
    return bool(_high_hierarchy_type_tokens(left_type) & _high_hierarchy_type_tokens(right_type))


def _is_link_or_slip_lane(edge: dict[str, Any]) -> bool:
    return str(edge.get("type", "")).endswith("_link")


def _type_comparisons(
    *,
    reference_high_edges: list[dict[str, Any]],
    candidate_high_edges: list[dict[str, Any]],
    min_extra_edges: int,
) -> list[dict[str, Any]]:
    reference_counts = Counter(
        token for edge in reference_high_edges for token in _high_hierarchy_type_tokens(str(edge["type"]))
    )
    candidate_counts = Counter(
        token for edge in candidate_high_edges for token in _high_hierarchy_type_tokens(str(edge["type"]))
    )
    rows = []
    for edge_type in sorted(set(reference_counts) | set(candidate_counts)):
        reference_count = int(reference_counts.get(edge_type, 0))
        candidate_count = int(candidate_counts.get(edge_type, 0))
        extra_edges = candidate_count - reference_count
        ratio = None if reference_count == 0 else candidate_count / reference_count
        if reference_count == 0 and candidate_count >= min_extra_edges:
            decision = "absent_in_reference"
        elif extra_edges >= min_extra_edges:
            decision = "overrepresented_in_candidate"
        else:
            decision = "reference_aligned"
        rows.append(
            {
                "edge_type": edge_type,
                "reference_count": reference_count,
                "candidate_count": candidate_count,
                "extra_edge_count": extra_edges,
                "candidate_to_reference_ratio": "" if ratio is None else round(ratio, 3),
                "hierarchy_scope_decision": decision,
            }
        )
    return rows


def _classify_candidate_edge(
    edge: dict[str, Any],
    *,
    reference_edges: list[dict[str, Any]],
    reference_high_edges: list[dict[str, Any]],
    type_decisions: dict[str, str],
    match_distance_m: float,
    oversplit_length_ratio: float,
    resolve_equivalent_fragmentation: bool = False,
) -> dict[str, Any]:
    nearest_same = _nearest_edge(
        edge,
        [
            item
            for item in reference_high_edges
            if _has_matching_high_hierarchy_type(str(edge["type"]), str(item["type"]))
        ],
    )
    same_name_candidates = [
        item
        for item in reference_high_edges
        if edge.get("normalized_name") and item.get("normalized_name") == edge.get("normalized_name")
    ]
    nearest_same_name = _nearest_edge(edge, same_name_candidates)
    nearest_any = _nearest_edge(edge, reference_edges)
    same_id_reference = next(
        (item for item in reference_edges if str(item.get("id", "")) == str(edge.get("id", ""))),
        None,
    )
    same_id_reference_type = str(same_id_reference.get("type", "")) if same_id_reference else ""
    if (
        same_id_reference is not None
        and _explicit_road_hierarchy_token(same_id_reference_type) is not None
        and not _has_matching_high_hierarchy_type(str(edge["type"]), same_id_reference_type)
    ):
        decision = "type_hierarchy_mismatch"
        action = "copy_same_edge_id_reference_hierarchy"
        reason = "same OSM edge id has a different reference hierarchy type"
        corridor_match_basis = "same_edge_id"
    elif _is_link_or_slip_lane(edge) and nearest_same["distance_m"] > match_distance_m:
        decision = "link_or_slip_lane"
        action = "protect_for_map_review"
        reason = "high-hierarchy link/slip lane requires map review before pruning or downgrading"
        corridor_match_basis = "link_or_slip_lane"
    elif nearest_same["distance_m"] <= match_distance_m:
        reference_length = float(nearest_same["edge"].get("length", 0.0))
        type_decision = _edge_type_decision(edge, type_decisions)
        is_short_fragment = float(edge["length"]) <= reference_length * oversplit_length_ratio
        if type_decision == "overrepresented_in_candidate" and is_short_fragment:
            decision = "matched_but_oversplit"
            action = "corridor_merge_review"
            reason = "candidate high-road edge matches a reference corridor but appears over-split"
        else:
            decision = "aligned"
            action = "keep"
            reason = "candidate high-road edge has a nearby same-type reference corridor"
        corridor_match_basis = "same_type_distance"
    elif nearest_same_name["edge"] is not None and _is_same_name_oversplit_case(
        edge=edge,
        reference_edge=nearest_same_name["edge"],
        type_decisions=type_decisions,
        oversplit_length_ratio=oversplit_length_ratio,
    ):
        decision = "matched_but_oversplit"
        action = "corridor_merge_review"
        reason = "candidate high-road edge shares a road name with a reference corridor and appears over-split"
        corridor_match_basis = "same_name"
    elif nearest_same_name["edge"] is not None and _has_matching_high_hierarchy_type(
        str(edge["type"]), str(nearest_same_name["edge"].get("type", ""))
    ):
        decision = "aligned"
        action = "keep"
        reason = "candidate high-road edge shares a road name and hierarchy type with a reference corridor"
        corridor_match_basis = "same_name"
    elif nearest_any["distance_m"] <= match_distance_m and _reference_type_missing(nearest_any["edge"]):
        decision = "aligned"
        action = "keep"
        reason = "candidate high-road edge overlaps reference geometry whose type is missing"
        corridor_match_basis = "reference_type_missing"
    elif nearest_any["distance_m"] <= match_distance_m:
        decision = "type_hierarchy_mismatch"
        action = "map_review_type_or_scope"
        reason = "candidate high-road edge is near reference geometry but not the same hierarchy type"
        corridor_match_basis = "nearest_any"
    else:
        decision = "out_of_reference_scope"
        action = "verify_bbox_or_exclude"
        reason = "candidate high-road edge has no nearby reference geometry within the match distance"
        corridor_match_basis = "same_name" if nearest_same_name["edge"] is not None else "none"

    raw_decision = decision
    hierarchy_resolution = ""
    if resolve_equivalent_fragmentation and _is_equivalent_fragmentation_case(
        edge=edge,
        decision=decision,
        nearest_same=nearest_same,
        nearest_same_name=nearest_same_name,
        match_distance_m=match_distance_m,
    ):
        decision = "aligned"
        action = "keep"
        reason = (
            "candidate high-road edge is an equivalent OSM fragmentation of a nearby reference corridor; "
            "road and connection parity remain separate gates"
        )
        hierarchy_resolution = "equivalent_fragmentation"

    nearest_same_edge = nearest_same["edge"]
    nearest_same_name_edge = nearest_same_name["edge"]
    nearest_any_edge = nearest_any["edge"]
    return {
        "candidate_edge_id": str(edge["id"]),
        "candidate_edge_type": str(edge["type"]),
        "candidate_edge_name": str(edge.get("name", "")),
        "candidate_edge_name_normalized": str(edge.get("normalized_name", "")),
        "candidate_length_m": round(float(edge["length"]), 3),
        "candidate_center_x": round(float(edge["center"][0]), 3),
        "candidate_center_y": round(float(edge["center"][1]), 3),
        "nearest_same_type_reference_edge_id": str(nearest_same_edge.get("id", "")) if nearest_same_edge else "",
        "nearest_same_type_reference_distance_m": _distance_field(nearest_same["distance_m"]),
        "nearest_same_type_reference_length_m": round(float(nearest_same_edge.get("length", 0.0)), 3)
        if nearest_same_edge
        else "",
        "nearest_any_reference_edge_id": str(nearest_any_edge.get("id", "")) if nearest_any_edge else "",
        "nearest_any_reference_edge_type": str(nearest_any_edge.get("type", "")) if nearest_any_edge else "",
        "nearest_any_reference_distance_m": _distance_field(nearest_any["distance_m"]),
        "same_name_reference_edge_id": str(nearest_same_name_edge.get("id", "")) if nearest_same_name_edge else "",
        "same_name_reference_edge_type": str(nearest_same_name_edge.get("type", "")) if nearest_same_name_edge else "",
        "same_name_reference_distance_m": _distance_field(nearest_same_name["distance_m"]),
        "same_id_reference_edge_id": str(same_id_reference.get("id", "")) if same_id_reference else "",
        "same_id_reference_edge_type": same_id_reference_type,
        "same_name_match_status": _same_name_match_status(edge, nearest_same_name_edge),
        "corridor_match_basis": corridor_match_basis,
        "hierarchy_decision": decision,
        "raw_hierarchy_decision": raw_decision,
        "hierarchy_resolution": hierarchy_resolution,
        "recommended_action": action,
        "reason": reason,
    }


def _is_equivalent_fragmentation_case(
    *,
    edge: dict[str, Any],
    decision: str,
    nearest_same: dict[str, Any],
    nearest_same_name: dict[str, Any],
    match_distance_m: float,
) -> bool:
    """Recognize harmless OSM edge splitting without hiding a hierarchy mismatch.

    A short fragment is only resolved when it already matched the strict
    oversplit classifier, retains a high-hierarchy type, and has either a
    same-type reference corridor within the ordinary distance gate or a
    same-name reference corridor within a slightly wider name gate.  TLS,
    topology, scope, and connection audits still evaluate the resulting
    network independently.
    """

    if decision != "matched_but_oversplit" or _is_link_or_slip_lane(edge):
        return False
    candidate_types = _high_hierarchy_type_tokens(str(edge.get("type", "")))
    if not candidate_types:
        return False
    same_edge = nearest_same.get("edge")
    same_type_near = same_edge is not None and float(nearest_same.get("distance_m", math.inf)) <= match_distance_m
    same_name_edge = nearest_same_name.get("edge")
    same_name = bool(edge.get("normalized_name")) and bool(same_name_edge)
    same_name_type_match = same_name and _has_matching_high_hierarchy_type(
        str(edge.get("type", "")), str(same_name_edge.get("type", ""))
    )
    same_name_near = (
        same_name_type_match
        and float(nearest_same_name.get("distance_m", math.inf)) <= match_distance_m * 1.5
    )
    if not (same_type_near or same_name_near):
        return False
    if same_edge is not None and not _has_matching_high_hierarchy_type(
        str(edge.get("type", "")), str(same_edge.get("type", ""))
    ):
        return False
    return True


def _is_same_name_oversplit_case(
    *,
    edge: dict[str, Any],
    reference_edge: dict[str, Any],
    type_decisions: dict[str, str],
    oversplit_length_ratio: float,
) -> bool:
    type_decision = _edge_type_decision(edge, type_decisions)
    if type_decision not in {"overrepresented_in_candidate", "absent_in_reference"}:
        return False
    if not _has_matching_high_hierarchy_type(str(edge.get("type", "")), str(reference_edge.get("type", ""))):
        return False
    reference_length = float(reference_edge.get("length", 0.0))
    if reference_length <= 0:
        return False
    return float(edge["length"]) <= reference_length * oversplit_length_ratio


def _edge_type_decision(edge: dict[str, Any], type_decisions: dict[str, str]) -> str:
    decisions = {
        type_decisions.get(token, "reference_aligned")
        for token in _high_hierarchy_type_tokens(str(edge["type"]))
    }
    if "absent_in_reference" in decisions:
        return "absent_in_reference"
    if "overrepresented_in_candidate" in decisions:
        return "overrepresented_in_candidate"
    return "reference_aligned"


def _reference_type_missing(edge: dict[str, Any] | None) -> bool:
    if edge is None:
        return False
    return str(edge.get("type", "") or "").strip() in {"", "<missing>"}


def _type_repair_rows(
    reference_hierarchy_report: Mapping[str, Any],
    *,
    max_same_name_distance_m: float,
) -> list[dict[str, Any]]:
    rows = []
    for case in reference_hierarchy_report.get("candidate_cases", []) or []:
        if not isinstance(case, Mapping):
            continue
        if str(case.get("hierarchy_decision", "")) != "type_hierarchy_mismatch":
            continue
        candidate_type = str(case.get("candidate_edge_type", ""))
        same_id_reference_type = str(case.get("same_id_reference_edge_type", ""))
        same_id_edge_id = str(case.get("same_id_reference_edge_id", ""))
        if str(case.get("corridor_match_basis", "")) == "same_edge_id" and same_id_edge_id:
            reference_type = same_id_reference_type
            repair_reason = "same_osm_edge_id_reference_hierarchy"
            distance = 0.0
            applied_type = _repaired_reference_hierarchy_type(candidate_type, reference_type)
        else:
            if str(case.get("same_name_match_status", "")) != "matched_by_name":
                continue
            reference_type = str(case.get("same_name_reference_edge_type", ""))
            repair_reason = "same_name_reference_high_hierarchy_type"
            distance = _float_field(case.get("same_name_reference_distance_m"))
            applied_type = _repaired_high_hierarchy_type(candidate_type, reference_type)
            if distance is None or distance > max_same_name_distance_m:
                continue
        if not reference_type or reference_type == "<missing>" or reference_type == candidate_type:
            continue
        if not applied_type or applied_type == candidate_type:
            continue
        rows.append(
            {
                "candidate_edge_id": str(case.get("candidate_edge_id", "")),
                "candidate_edge_name": str(case.get("candidate_edge_name", "")),
                "candidate_edge_type": candidate_type,
                "reference_edge_id": (
                    same_id_edge_id
                    if str(case.get("corridor_match_basis", "")) == "same_edge_id"
                    else str(case.get("same_name_reference_edge_id", ""))
                ),
                "reference_edge_type": reference_type,
                "applied_edge_type": applied_type,
                "same_name_reference_distance_m": round(distance, 3),
                "repair_reason": repair_reason,
            }
        )
    return [row for row in rows if row["candidate_edge_id"]]


def _repaired_high_hierarchy_type(candidate_type: str, reference_type: str) -> str:
    """Copy only the reference road hierarchy, preserving candidate modal decoration.

    A manual reference may fuse pedestrian/cycleway support lanes into a compound
    type. Copying that complete type onto a vehicle-only candidate edge claims
    support lanes that do not exist. Keep the candidate's non-hierarchy tokens
    and replace only its single high-road token with the reference token.
    """
    reference_high_tokens = [
        token for token in str(reference_type).split("|") if token.strip() in HIGH_HIERARCHY_TYPES
    ]
    if len(reference_high_tokens) != 1:
        return ""
    candidate_support_tokens = [
        token.strip()
        for token in str(candidate_type).split("|")
        if token.strip() and token.strip() not in HIGH_HIERARCHY_TYPES
    ]
    return "|".join([*candidate_support_tokens, reference_high_tokens[0].strip()])


def _explicit_road_hierarchy_token(edge_type: str) -> str | None:
    road_tokens = [token.strip() for token in str(edge_type).split("|") if token.strip().startswith("highway.")]
    return road_tokens[0] if len(road_tokens) == 1 else None


def _repaired_reference_hierarchy_type(candidate_type: str, reference_type: str) -> str:
    """Apply the reference highway token for an exact OSM edge-id match.

    This also handles deliberate downgrades such as ``tertiary`` to
    ``unclassified`` while preserving candidate-side modal decoration.
    """

    reference_road_token = _explicit_road_hierarchy_token(reference_type)
    if reference_road_token is None:
        return ""
    candidate_support_tokens = [
        token.strip()
        for token in str(candidate_type).split("|")
        if token.strip() and not token.strip().startswith("highway.")
    ]
    return "|".join([*candidate_support_tokens, reference_road_token])


def _float_field(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _same_name_match_status(edge: dict[str, Any], same_name_edge: dict[str, Any] | None) -> str:
    if not edge.get("normalized_name"):
        return "candidate_name_missing"
    if same_name_edge is None:
        return "no_same_name_reference"
    return "matched_by_name"


def _nearest_edge(edge: dict[str, Any], candidates: list[dict[str, Any]]) -> dict[str, Any]:
    best_edge = None
    best_distance = math.inf
    for candidate in candidates:
        distance = _edge_distance(edge, candidate)
        if distance < best_distance:
            best_distance = distance
            best_edge = candidate
    return {"edge": best_edge, "distance_m": best_distance}


def _shape_distance(a: list[tuple[float, float]], b: list[tuple[float, float]]) -> float:
    if not a or not b:
        return math.inf
    if len(a) == 1:
        a = [a[0], a[0]]
    if len(b) == 1:
        b = [b[0], b[0]]
    best = math.inf
    for a0, a1 in zip(a, a[1:]):
        for b0, b1 in zip(b, b[1:]):
            best = min(
                best,
                _point_segment_distance(a0, b0, b1),
                _point_segment_distance(a1, b0, b1),
                _point_segment_distance(b0, a0, a1),
                _point_segment_distance(b1, a0, a1),
            )
    return best


def _edge_distance(a: dict[str, Any], b: dict[str, Any]) -> float:
    if a.get("geo_shape") and b.get("geo_shape"):
        return _latlon_shape_distance_m(a["geo_shape"], b["geo_shape"])
    return _shape_distance(a["shape"], b["shape"])


def _latlon_shape_distance_m(
    a: list[tuple[float, float]],
    b: list[tuple[float, float]],
) -> float:
    if not a or not b:
        return math.inf
    if len(a) == 1:
        a = [a[0], a[0]]
    if len(b) == 1:
        b = [b[0], b[0]]
    best = math.inf
    for a0, a1 in zip(a, a[1:]):
        for b0, b1 in zip(b, b[1:]):
            origin = ((a0[0] + a1[0] + b0[0] + b1[0]) / 4, (a0[1] + a1[1] + b0[1] + b1[1]) / 4)
            a0_xy = _latlon_to_local_xy_m(a0, origin)
            a1_xy = _latlon_to_local_xy_m(a1, origin)
            b0_xy = _latlon_to_local_xy_m(b0, origin)
            b1_xy = _latlon_to_local_xy_m(b1, origin)
            best = min(
                best,
                _point_segment_distance(a0_xy, b0_xy, b1_xy),
                _point_segment_distance(a1_xy, b0_xy, b1_xy),
                _point_segment_distance(b0_xy, a0_xy, a1_xy),
                _point_segment_distance(b1_xy, a0_xy, a1_xy),
                haversine_m(a0[0], a0[1], b0[0], b0[1]),
                haversine_m(a1[0], a1[1], b1[0], b1[1]),
            )
    return best


def _latlon_to_local_xy_m(
    point: tuple[float, float],
    origin: tuple[float, float],
) -> tuple[float, float]:
    lat, lon = point
    origin_lat, origin_lon = origin
    meters_per_degree_lat = 111_320.0
    meters_per_degree_lon = 111_320.0 * math.cos(math.radians(origin_lat))
    return (
        (lon - origin_lon) * meters_per_degree_lon,
        (lat - origin_lat) * meters_per_degree_lat,
    )


def _point_segment_distance(
    point: tuple[float, float],
    start: tuple[float, float],
    end: tuple[float, float],
) -> float:
    dx = end[0] - start[0]
    dy = end[1] - start[1]
    if dx == 0 and dy == 0:
        return _distance(point, start)
    t = ((point[0] - start[0]) * dx + (point[1] - start[1]) * dy) / (dx * dx + dy * dy)
    t = max(0.0, min(1.0, t))
    projected = (start[0] + t * dx, start[1] + t * dy)
    return _distance(point, projected)


def _distance(a: tuple[float, float], b: tuple[float, float]) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def _distance_field(value: float) -> float | str:
    if not math.isfinite(value):
        return ""
    return round(value, 3)


def _write_cases_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    fieldnames = [
        "candidate_edge_id",
        "candidate_edge_type",
        "candidate_edge_name",
        "candidate_edge_name_normalized",
        "candidate_length_m",
        "candidate_center_x",
        "candidate_center_y",
        "nearest_same_type_reference_edge_id",
        "nearest_same_type_reference_distance_m",
        "nearest_same_type_reference_length_m",
        "nearest_any_reference_edge_id",
        "nearest_any_reference_edge_type",
        "nearest_any_reference_distance_m",
        "same_name_reference_edge_id",
        "same_name_reference_edge_type",
        "same_name_reference_distance_m",
        "same_id_reference_edge_id",
        "same_id_reference_edge_type",
        "same_name_match_status",
        "corridor_match_basis",
        "hierarchy_decision",
        "raw_hierarchy_decision",
        "hierarchy_resolution",
        "recommended_action",
        "reason",
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _write_type_comparison_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    fieldnames = [
        "edge_type",
        "reference_count",
        "candidate_count",
        "extra_edge_count",
        "candidate_to_reference_ratio",
        "hierarchy_scope_decision",
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _write_type_repair_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    fieldnames = [
        "candidate_edge_id",
        "candidate_edge_name",
        "candidate_edge_type",
        "reference_edge_id",
        "reference_edge_type",
        "applied_edge_type",
        "same_name_reference_distance_m",
        "repair_reason",
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _short_output_path(output_dir: Path, prefix: str, suffix: str) -> Path:
    """Keep hierarchy artifacts writable under Windows path limits."""
    candidate = output_dir / f"{prefix}{suffix}"
    if len(str(candidate.resolve())) < 239:
        return candidate
    digest = hashlib.sha1(str(candidate).encode("utf-8")).hexdigest()[:10]
    return output_dir / f"p_{digest}{suffix}"


def _warnings(issue_count: int) -> list[str]:
    if not issue_count:
        return []
    return [
        (
            f"reference hierarchy audit found {issue_count} high-road review case(s); "
            "inspect the CSV before pruning, downgrading, or merging high-hierarchy roads"
        )
    ]


def _failure(error: str) -> dict[str, Any]:
    return {
        "status": "fail",
        "claim_status": "construction-invalid",
        "reference_hierarchy_status": "failed",
        "error": error,
        "warnings": [error],
    }
