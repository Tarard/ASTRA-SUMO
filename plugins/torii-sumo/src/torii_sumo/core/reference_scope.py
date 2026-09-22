from __future__ import annotations

import csv
import hashlib
import json
import math
from collections import Counter
from pathlib import Path
from typing import Any, Callable, Mapping
import xml.etree.ElementTree as ET

from .command_runner import run_command


DETAIL_EDGE_TYPES = {
    "highway.service",
    "highway.path",
    "highway.footway",
    "highway.cycleway",
    "highway.steps",
    "highway.pedestrian",
    "highway.track",
}


def build_reference_bbox_variant(
    *,
    reference_net_file: Path,
    bbox: str,
    output_dir: Path,
    prefix: str = "reference_bbox_scope",
    netconvert_binary: str = "netconvert",
    timeout_seconds: float = 240.0,
    command_runner: Callable[..., Any] = run_command,
) -> dict[str, Any]:
    """Create a teacher-network variant scoped to the candidate OSM bbox.

    A reference .net.xml can have a much larger original boundary than the
    OSM bbox used for a candidate build.  Comparing those files directly
    mixes out-of-scope teacher roads into every parity gate.  SUMO's boundary
    selector operates in network coordinates, so this helper projects the
    geographic bbox into the reference network's UTM coordinate system and
    runs a post-load keep-edges pass.  The source teacher is never modified.
    """
    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    reference_net_file = reference_net_file.resolve()
    if not reference_net_file.exists():
        return _reference_bbox_variant_failure(
            f"reference net file does not exist: {reference_net_file}",
            reference_net_file=reference_net_file,
        )
    try:
        west, south, east, north = _parse_bbox_text(bbox)
        root = ET.parse(reference_net_file).getroot()
        location = root.find("location")
        if location is None:
            raise ValueError("reference net has no <location> projection metadata")
        offset_x, offset_y = _parse_pair(location.attrib.get("netOffset", ""), "netOffset")
        zone = _parse_utm_zone(location.attrib.get("projParameter", ""))
        northern = not location.attrib.get("projParameter", "").lower().count("south")
        xy_corners = [
            _latlon_to_net_xy(lat, lon, zone=zone, northern=northern, offset=(offset_x, offset_y))
            for lat, lon in ((south, west), (south, east), (north, west), (north, east))
        ]
        boundary = (
            min(point[0] for point in xy_corners),
            min(point[1] for point in xy_corners),
            max(point[0] for point in xy_corners),
            max(point[1] for point in xy_corners),
        )
    except (OSError, ET.ParseError, KeyError, TypeError, ValueError) as exc:
        return _reference_bbox_variant_failure(
            f"{type(exc).__name__}: {exc}",
            reference_net_file=reference_net_file,
            bbox=bbox,
        )

    variant_file = _short_output_path(output_dir, prefix, "_scoped.net.xml")
    command_record = _short_output_path(output_dir, prefix, "_netconvert.cmd.txt")
    boundary_text = ",".join(f"{value:.3f}" for value in boundary)
    command = [
        netconvert_binary,
        "--sumo-net-file",
        str(reference_net_file),
        "--keep-edges.in-boundary",
        boundary_text,
        "--keep-edges.postload",
        "--output-file",
        str(variant_file),
    ]
    command_record.write_text(" ".join(command) + "\n", encoding="utf-8")
    try:
        result = _result_to_dict(command_runner(command, cwd=output_dir, timeout_seconds=timeout_seconds))
    except OSError as exc:
        return _reference_bbox_variant_failure(
            f"{type(exc).__name__}: {exc}",
            reference_net_file=reference_net_file,
            bbox=bbox,
            variant_file=variant_file,
            command_record=command_record,
            boundary=boundary,
        )

    input_counts = _network_counts(reference_net_file)
    output_counts = _network_counts(variant_file) if variant_file.exists() else {}
    output_edge_count = int(output_counts.get("scope_pruned_edge_count", 0) or 0)
    command_passed = result.get("status") == "pass" and variant_file.exists()
    status = "pass" if command_passed and output_edge_count > 0 else "fail"
    warnings = [
        "bbox scope is a separate teacher comparison variant; the source reference network is unchanged",
        "SUMO keeps edges located inside the boundary and does not claim manual boundary trimming equivalence",
    ]
    if command_passed and output_edge_count == 0:
        warnings.append("bbox scope produced no non-internal teacher edges")
    if not command_passed:
        warnings.append(f"reference bbox scope variant was not created: {variant_file}")
    return {
        "status": status,
        "claim_status": "diagnostic-demo" if status == "pass" else "construction-invalid",
        "reference_bbox_scope_status": "variant_created" if status == "pass" else "failed",
        "reference_net_file": str(reference_net_file),
        "variant_file": str(variant_file),
        "command_record": str(command_record),
        "candidate_bbox": bbox,
        "reference_projection_zone": zone,
        "reference_projection_northern": northern,
        "reference_xy_boundary": {
            "min_x": round(boundary[0], 3),
            "min_y": round(boundary[1], 3),
            "max_x": round(boundary[2], 3),
            "max_y": round(boundary[3], 3),
        },
        "netconvert": result,
        "input_counts": input_counts,
        "output_counts": output_counts,
        "edge_count_delta": output_edge_count - int(input_counts.get("scope_pruned_edge_count", 0) or 0),
        "warnings": warnings,
    }


def audit_reference_scope(
    *,
    reference_net_file: Path,
    candidate_net_file: Path,
    output_dir: Path,
    prefix: str = "reference_scope",
    overrepresentation_ratio: float = 1.5,
    min_extra_edges: int = 10,
    max_prune_edge_length_m: float = 80.0,
) -> dict[str, Any]:
    if overrepresentation_ratio <= 1.0:
        return _failure("overrepresentation_ratio must be greater than 1.0")
    if min_extra_edges < 0:
        return _failure("min_extra_edges must be non-negative")
    if max_prune_edge_length_m <= 0:
        return _failure("max_prune_edge_length_m must be positive")
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
    reference_counts = Counter(edge["type"] for edge in reference_edges)
    candidate_counts = Counter(edge["type"] for edge in candidate_edges)
    type_comparisons = _type_comparisons(
        reference_counts=reference_counts,
        candidate_counts=candidate_counts,
        overrepresentation_ratio=overrepresentation_ratio,
        min_extra_edges=min_extra_edges,
    )
    type_decisions = {row["edge_type"]: row["scope_decision"] for row in type_comparisons}
    candidate_degrees = _node_degrees(candidate_edges)
    protected_controlled_edge_ids: list[str] = []
    prune_candidates = _prune_candidates(
        candidate_edges,
        type_decisions=type_decisions,
        node_degrees=candidate_degrees,
        max_prune_edge_length_m=max_prune_edge_length_m,
        reference_edge_ids={str(edge.get("id", "")) for edge in reference_edges if str(edge.get("id", ""))},
        protected_controlled_edge_ids=protected_controlled_edge_ids,
    )

    type_comparison_file = _short_output_path(output_dir, prefix, "_type_comparison.csv")
    prune_candidates_file = _short_output_path(output_dir, prefix, "_prune_candidates.csv")
    report_file = _short_output_path(output_dir, prefix, "_reference_scope_audit.json")
    _write_type_comparison_csv(type_comparison_file, type_comparisons)
    _write_prune_candidates_csv(prune_candidates_file, prune_candidates)

    status = "blocked" if prune_candidates else "pass"
    report = {
        "status": status,
        "claim_status": "blocked" if prune_candidates else "diagnostic-demo",
        "reference_scope_status": "needs_pruning_review" if prune_candidates else "pass",
        "reference_net_file": str(reference_net_file),
        "candidate_net_file": str(candidate_net_file),
        "output_dir": str(output_dir),
        "overrepresentation_ratio": overrepresentation_ratio,
        "min_extra_edges": min_extra_edges,
        "max_prune_edge_length_m": max_prune_edge_length_m,
        "reference_edge_count": len(reference_edges),
        "candidate_edge_count": len(candidate_edges),
        "edge_count_delta": len(candidate_edges) - len(reference_edges),
        "overrepresented_type_count": sum(
            1 for row in type_comparisons if row["scope_decision"] != "reference_aligned"
        ),
        "prune_candidate_count": len(prune_candidates),
        "protected_controlled_edge_count": len(protected_controlled_edge_ids),
        "protected_controlled_edge_ids": sorted(protected_controlled_edge_ids),
        "candidate_edge_id_match_count": sum(
            1 for edge in candidate_edges if str(edge.get("id", "")) in {item.get("id") for item in reference_edges}
        ),
        "modal_detail_identity_policy": "preserve_exact_osm_edge_ids_present_in_reference",
        "type_comparison_file": str(type_comparison_file),
        "prune_candidates_file": str(prune_candidates_file),
        "report_file": str(report_file),
        "type_comparisons": type_comparisons,
        "prune_candidates": prune_candidates,
        "warnings": _scope_warnings(prune_candidates, protected_controlled_edge_ids),
    }
    report_file.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    return report


def build_scope_pruning_variant(
    *,
    net_file: Path,
    reference_scope_report: Mapping[str, Any],
    output_dir: Path,
    prefix: str = "scope_pruning",
    timeout_seconds: float = 240.0,
    command_runner: Callable[..., Any] = run_command,
) -> dict[str, Any]:
    if not net_file.exists():
        return _failure(f"net file does not exist: {net_file}")

    net_file = net_file.resolve()
    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    remove_edge_ids = sorted(
        {
            str(candidate.get("edge_id", ""))
            for candidate in reference_scope_report.get("prune_candidates", []) or []
            if str(candidate.get("edge_id", "")) and str(candidate.get("prune_decision", "")) == "prune_candidate"
        }
    )
    plan_file = _short_output_path(output_dir, prefix, "_plan.json")
    remove_edges_file = _short_output_path(output_dir, prefix, "_remove_edges.txt")
    command_record = _short_output_path(output_dir, prefix, "_netconvert.cmd.txt")
    variant_file = _short_output_path(output_dir, prefix, "_scope_pruned.net.xml")

    plan = {
        "scope_pruning_status": "planned_for_review_variant" if remove_edge_ids else "not_needed",
        "net_file": str(net_file),
        "variant_file": str(variant_file) if remove_edge_ids else "",
        "remove_edge_count": len(remove_edge_ids),
        "review_policy": (
            "create a separate reference-scope pruning variant for Netedit/map review; "
            "do not overwrite the source network"
        ),
        "remove_edge_ids": remove_edge_ids,
    }
    plan_file.write_text(json.dumps(plan, indent=2, ensure_ascii=False), encoding="utf-8")
    remove_edges_file.write_text("\n".join(remove_edge_ids) + ("\n" if remove_edge_ids else ""), encoding="utf-8")

    pruning_safety = _pruning_safety(reference_scope_report)

    if not remove_edge_ids:
        return {
            "status": "pass",
            "claim_status": "diagnostic-demo",
            "scope_pruning_status": "not_needed",
            "scope_pruning_removed_edge_count": 0,
            "scope_pruning_plan_file": str(plan_file),
            "scope_pruning_remove_edges_file": str(remove_edges_file),
            "scope_pruning_variant_file": "",
            "scope_pruning_command_record": "",
            "scope_pruning_netconvert": {},
            **pruning_safety,
            "warnings": [],
        }

    command = [
        "netconvert",
        "--sumo-net-file",
        str(net_file),
        "--remove-edges.input-file",
        str(remove_edges_file),
        "--output-file",
        str(variant_file),
    ]
    command_record.write_text(" ".join(command) + "\n", encoding="utf-8")
    try:
        result = _result_to_dict(command_runner(command, cwd=output_dir, timeout_seconds=timeout_seconds))
    except OSError as exc:
        return {
            **_failure(f"{type(exc).__name__}: {exc}"),
            "scope_pruning_plan_file": str(plan_file),
            "scope_pruning_remove_edges_file": str(remove_edges_file),
            "scope_pruning_variant_file": str(variant_file),
            "scope_pruning_command_record": str(command_record),
        }

    status = "pass" if result.get("status") == "pass" and variant_file.exists() else "fail"
    counts = _network_counts(variant_file) if variant_file.exists() else {}
    warnings = ["scope pruning variant requires Netedit/map review before adoption"]
    if status != "pass":
        warnings.append(f"scope pruning variant was not created: {variant_file}")
    return {
        "status": status,
        "claim_status": "blocked" if status == "pass" else "construction-invalid",
        "scope_pruning_status": "variant_created_for_review" if status == "pass" else "failed",
        "scope_pruning_removed_edge_count": len(remove_edge_ids),
        "scope_pruning_plan_file": str(plan_file),
        "scope_pruning_remove_edges_file": str(remove_edges_file),
        "scope_pruning_variant_file": str(variant_file),
        "scope_pruning_command_record": str(command_record),
        "scope_pruning_netconvert": result,
        **pruning_safety,
        **counts,
        "warnings": warnings,
    }


def _pruning_safety(reference_scope_report: Mapping[str, Any]) -> dict[str, Any]:
    """Prove that a scope-prune candidate cannot alter the vehicle core.

    Scope candidates are allowed to be pedestrian/bicycle detail, but only
    when they are leaves in the source graph.  Exact OSM identities present in
    the reference are never included in this set by the audit.  This is a
    conservative promotion gate for a visual-detail variant; it does not
    claim that arbitrary modal edits are safe.
    """

    candidates = [
        item
        for item in reference_scope_report.get("prune_candidates", []) or []
        if isinstance(item, Mapping) and str(item.get("prune_decision", "")) == "prune_candidate"
    ]
    vehicle_tokens = {
        "passenger",
        "delivery",
        "truck",
        "bus",
        "coach",
        "taxi",
        "motorcycle",
        "moped",
        "tram",
        "rail",
        "rail_urban",
        "rail_electric",
        "rail_fast",
    }
    modal_only = []
    leaf = []
    for item in candidates:
        allow = {
            token.strip()
            for token in str(item.get("allow", "")).replace(";", " ").split()
            if token.strip()
        }
        is_modal_only = not bool(allow & vehicle_tokens)
        is_leaf = min(int(item.get("from_degree", 0) or 0), int(item.get("to_degree", 0) or 0)) <= 1
        if is_modal_only:
            modal_only.append(str(item.get("edge_id", "")))
        if is_leaf:
            leaf.append(str(item.get("edge_id", "")))
    ids = [str(item.get("edge_id", "")) for item in candidates]
    return {
        "scope_pruning_modal_only_status": "pass" if len(modal_only) == len(candidates) else "blocked",
        "scope_pruning_modal_only_edge_ids": modal_only,
        "scope_pruning_modal_leaf_continuity_status": "pass" if len(leaf) == len(candidates) else "blocked",
        "scope_pruning_modal_leaf_edge_ids": leaf,
        "scope_pruning_vehicle_core_impact_status": "pass" if len(modal_only) == len(candidates) else "blocked",
        "scope_pruning_safety_status": (
            "pass" if len(modal_only) == len(candidates) and len(leaf) == len(candidates) else "blocked"
        ),
        "scope_pruning_safety_edge_ids": ids,
    }


def _read_edges(net_file: Path) -> list[dict[str, Any]]:
    root = ET.parse(net_file).getroot()
    controlled_connection_counts = Counter(
        connection.attrib.get("from", "")
        for connection in root.findall("connection")
        if connection.attrib.get("tl") and connection.attrib.get("linkIndex")
    )
    edges = []
    for edge in root.findall("edge"):
        edge_id = edge.attrib.get("id", "")
        if not edge_id or edge_id.startswith(":") or edge.attrib.get("function") == "internal":
            continue
        lanes = edge.findall("lane")
        length = _edge_length(lanes)
        edges.append(
            {
                "id": edge_id,
                "from": edge.attrib.get("from", ""),
                "to": edge.attrib.get("to", ""),
                "type": edge.attrib.get("type", "<missing>") or "<missing>",
                "name": edge.attrib.get("name", ""),
                "lane_count": len(lanes),
                "length": length,
                "allow": _edge_allow(lanes),
                "controlled_connection_count": int(controlled_connection_counts.get(edge_id, 0)),
            }
        )
    return edges


def _edge_length(lanes: list[ET.Element]) -> float:
    lengths = []
    for lane in lanes:
        try:
            lengths.append(float(lane.attrib.get("length", "0") or 0))
        except ValueError:
            pass
    return max(lengths, default=0.0)


def _edge_allow(lanes: list[ET.Element]) -> str:
    allow_values = sorted({lane.attrib.get("allow", "all") or "all" for lane in lanes})
    return ";".join(allow_values) if allow_values else "all"


def _node_degrees(edges: list[dict[str, Any]]) -> dict[str, int]:
    degrees: dict[str, int] = {}
    for edge in edges:
        for node_id in (str(edge.get("from", "")), str(edge.get("to", ""))):
            if node_id:
                degrees[node_id] = degrees.get(node_id, 0) + 1
    return degrees


def _type_comparisons(
    *,
    reference_counts: Counter[str],
    candidate_counts: Counter[str],
    overrepresentation_ratio: float,
    min_extra_edges: int,
) -> list[dict[str, Any]]:
    rows = []
    for edge_type in sorted(set(reference_counts) | set(candidate_counts)):
        reference_count = int(reference_counts.get(edge_type, 0))
        candidate_count = int(candidate_counts.get(edge_type, 0))
        extra_edges = candidate_count - reference_count
        ratio = None if reference_count == 0 else candidate_count / reference_count
        if reference_count == 0 and candidate_count >= min_extra_edges:
            decision = "absent_in_reference"
        elif extra_edges >= min_extra_edges and ratio is not None and ratio >= overrepresentation_ratio:
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
                "scope_decision": decision,
            }
        )
    return rows


def _prune_candidates(
    candidate_edges: list[dict[str, Any]],
    *,
    type_decisions: Mapping[str, str],
    node_degrees: Mapping[str, int],
    max_prune_edge_length_m: float,
    reference_edge_ids: set[str] | None = None,
    protected_controlled_edge_ids: list[str] | None = None,
) -> list[dict[str, Any]]:
    reference_edge_ids = reference_edge_ids or set()
    rows = []
    for edge in candidate_edges:
        if str(edge.get("id", "")) in reference_edge_ids:
            continue
        edge_type = str(edge["type"])
        scope_decision = type_decisions.get(edge_type, "reference_aligned")
        if scope_decision == "reference_aligned":
            continue
        if edge_type not in DETAIL_EDGE_TYPES and scope_decision != "absent_in_reference":
            continue
        from_degree = int(node_degrees.get(str(edge.get("from", "")), 0))
        to_degree = int(node_degrees.get(str(edge.get("to", "")), 0))
        is_dead_end = min(from_degree, to_degree) <= 1
        is_short = float(edge.get("length", 0.0)) <= max_prune_edge_length_m
        if not (is_dead_end and is_short):
            continue
        if int(edge.get("controlled_connection_count", 0) or 0) > 0:
            if protected_controlled_edge_ids is not None:
                protected_controlled_edge_ids.append(str(edge["id"]))
            continue
        rows.append(
            {
                "edge_id": str(edge["id"]),
                "edge_type": edge_type,
                "edge_name": str(edge.get("name", "")),
                "from": str(edge.get("from", "")),
                "to": str(edge.get("to", "")),
                "length_m": round(float(edge.get("length", 0.0)), 3),
                "lane_count": int(edge.get("lane_count", 0)),
                "allow": str(edge.get("allow", "")),
                "from_degree": from_degree,
                "to_degree": to_degree,
                "scope_decision": scope_decision,
                "prune_decision": "prune_candidate",
                "prune_confidence": "medium",
                "reason": (
                    f"{edge_type} is {scope_decision} and this edge is a short dead-end detail fragment"
                ),
            }
        )
    rows.sort(key=lambda row: (row["edge_type"], row["edge_id"]))
    return rows


def _write_type_comparison_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "edge_type",
                "reference_count",
                "candidate_count",
                "extra_edge_count",
                "candidate_to_reference_ratio",
                "scope_decision",
            ],
        )
        writer.writeheader()
        writer.writerows(rows)


def _write_prune_candidates_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "edge_id",
                "edge_type",
                "edge_name",
                "from",
                "to",
                "length_m",
                "lane_count",
                "allow",
                "from_degree",
                "to_degree",
                "scope_decision",
                "prune_decision",
                "prune_confidence",
                "reason",
            ],
        )
        writer.writeheader()
        writer.writerows(rows)


def _network_counts(net_file: Path) -> dict[str, int]:
    root = ET.parse(net_file).getroot()
    edges = [
        edge
        for edge in root.findall("edge")
        if not edge.attrib.get("id", "").startswith(":") and edge.attrib.get("function") != "internal"
    ]
    junctions = [
        junction
        for junction in root.findall("junction")
        if not junction.attrib.get("id", "").startswith(":") and junction.attrib.get("type") != "internal"
    ]
    return {
        "scope_pruned_edge_count": len(edges),
        "scope_pruned_lane_count": sum(len(edge.findall("lane")) for edge in edges),
        "scope_pruned_junction_count": len(junctions),
        "scope_pruned_traffic_light_junction_count": sum(
            1 for junction in junctions if junction.attrib.get("type") == "traffic_light"
        ),
        "scope_pruned_tl_logic_count": len(root.findall("tlLogic")),
        "scope_pruned_controlled_connection_count": sum(
            1
            for connection in root.findall("connection")
            if connection.attrib.get("tl") and connection.attrib.get("linkIndex")
        ),
    }


def _result_to_dict(result: Any) -> dict[str, Any]:
    if hasattr(result, "to_dict"):
        return dict(result.to_dict())
    if hasattr(result, "model_dump"):
        return dict(result.model_dump(mode="json"))
    return dict(result)


def _parse_bbox_text(value: str) -> tuple[float, float, float, float]:
    parts = [part.strip() for part in str(value).split(",")]
    if len(parts) != 4:
        raise ValueError("bbox must contain west,south,east,north")
    west, south, east, north = (float(part) for part in parts)
    if not all(math.isfinite(part) for part in (west, south, east, north)):
        raise ValueError("bbox coordinates must be finite")
    if not (-180.0 <= west <= 180.0 and -180.0 <= east <= 180.0):
        raise ValueError("bbox longitude must be in [-180, 180]")
    if not (-90.0 <= south <= 90.0 and -90.0 <= north <= 90.0):
        raise ValueError("bbox latitude must be in [-90, 90]")
    if west >= east or south >= north:
        raise ValueError("bbox must have west < east and south < north")
    return west, south, east, north


def _parse_pair(value: str, label: str) -> tuple[float, float]:
    parts = [part.strip() for part in str(value).split(",")]
    if len(parts) != 2:
        raise ValueError(f"{label} must contain two comma-separated coordinates")
    first, second = (float(part) for part in parts)
    if not all(math.isfinite(part) for part in (first, second)):
        raise ValueError(f"{label} coordinates must be finite")
    return first, second


def _parse_utm_zone(proj_parameter: str) -> int:
    for token in str(proj_parameter).split():
        if token.startswith("+zone="):
            zone = int(token.split("=", 1)[1])
            if 1 <= zone <= 60:
                return zone
    raise ValueError(f"cannot parse UTM zone from projParameter: {proj_parameter}")


def _latlon_to_net_xy(
    lat: float,
    lon: float,
    *,
    zone: int,
    northern: bool,
    offset: tuple[float, float],
) -> tuple[float, float]:
    """Project WGS84 latitude/longitude to SUMO's UTM network coordinates."""
    a = 6378137.0
    ecc_squared = 0.00669438
    k0 = 0.9996
    ecc_prime_squared = ecc_squared / (1.0 - ecc_squared)
    lat_rad = math.radians(lat)
    lon_rad = math.radians(lon)
    lon_origin = math.radians((zone - 1) * 6 - 180 + 3)
    sin_lat = math.sin(lat_rad)
    cos_lat = math.cos(lat_rad)
    tan_lat = math.tan(lat_rad)
    n_value = a / math.sqrt(1.0 - ecc_squared * sin_lat**2)
    t_value = tan_lat**2
    c_value = ecc_prime_squared * cos_lat**2
    a_value = cos_lat * (lon_rad - lon_origin)
    m_value = a * (
        (1 - ecc_squared / 4 - 3 * ecc_squared**2 / 64 - 5 * ecc_squared**3 / 256) * lat_rad
        - (3 * ecc_squared / 8 + 3 * ecc_squared**2 / 32 + 45 * ecc_squared**3 / 1024)
        * math.sin(2 * lat_rad)
        + (15 * ecc_squared**2 / 256 + 45 * ecc_squared**3 / 1024) * math.sin(4 * lat_rad)
        - (35 * ecc_squared**3 / 3072) * math.sin(6 * lat_rad)
    )
    easting = k0 * n_value * (
        a_value
        + (1 - t_value + c_value) * a_value**3 / 6
        + (5 - 18 * t_value + t_value**2 + 72 * c_value - 58 * ecc_prime_squared) * a_value**5 / 120
    ) + 500000.0
    northing = k0 * (
        m_value
        + n_value
        * tan_lat
        * (
            a_value**2 / 2
            + (5 - t_value + 9 * c_value + 4 * c_value**2) * a_value**4 / 24
            + (61 - 58 * t_value + t_value**2 + 600 * c_value - 330 * ecc_prime_squared)
            * a_value**6
            / 720
        )
    )
    if not northern:
        northing += 10_000_000.0
    return easting + offset[0], northing + offset[1]


def _reference_bbox_variant_failure(
    error: str,
    *,
    reference_net_file: Path,
    bbox: str = "",
    variant_file: Path | None = None,
    command_record: Path | None = None,
    boundary: tuple[float, float, float, float] | None = None,
) -> dict[str, Any]:
    return {
        "status": "blocked" if "does not exist" not in error else "fail",
        "claim_status": "blocked" if "does not exist" not in error else "construction-invalid",
        "reference_bbox_scope_status": "blocked",
        "reference_net_file": str(reference_net_file),
        "variant_file": str(variant_file or ""),
        "command_record": str(command_record or ""),
        "candidate_bbox": bbox,
        "reference_xy_boundary": (
            {
                "min_x": round(boundary[0], 3),
                "min_y": round(boundary[1], 3),
                "max_x": round(boundary[2], 3),
                "max_y": round(boundary[3], 3),
            }
            if boundary is not None
            else {}
        ),
        "error": error,
        "warnings": [error],
    }


def _scope_warnings(
    prune_candidates: list[dict[str, Any]],
    protected_controlled_edge_ids: list[str] | None = None,
) -> list[str]:
    warnings = []
    if prune_candidates:
        warnings.append(
            f"reference scope audit found {len(prune_candidates)} prune candidate edge(s); "
            "create a separate scope-pruned variant and review it before adoption"
        )
    if protected_controlled_edge_ids:
        warnings.append(
            f"reference scope protected {len(protected_controlled_edge_ids)} short detail edge(s) "
            "because they carry TLS-controlled connections"
        )
    return warnings


def _short_output_path(output_dir: Path, prefix: str, suffix: str) -> Path:
    candidate = output_dir / f"{prefix}{suffix}"
    if len(str(candidate.resolve())) < 239:
        return candidate
    digest = hashlib.sha1(str(candidate).encode("utf-8")).hexdigest()[:10]
    return output_dir / f"p_{digest}{suffix}"


def _failure(error: str) -> dict[str, Any]:
    return {
        "status": "fail",
        "claim_status": "construction-invalid",
        "reference_scope_status": "failed",
        "scope_pruning_status": "failed",
        "error": error,
        "warnings": [error],
    }
