from __future__ import annotations

import csv
import hashlib
import json
import math
from collections import Counter
from collections.abc import Callable
from pathlib import Path
from typing import Any
import xml.etree.ElementTree as ET

from .junction_teacher_model import (
    compare_junction_pattern_records,
    extract_junction_pattern_index,
    summarize_junction_pattern_policy,
    summarize_junction_pattern_templates,
)
from .detector_demand import lane_allows_passenger
from .osm_network import _net_xy_to_latlon, _parse_utm_zone, _utm_to_latlon
from .topology_audit import audit_topology_fragmentation


STRUCTURAL_ONLY_PATTERN_SAMPLE_LIMIT = 40
PORTABLE_ARTIFACT_PATH_LIMIT = 240


def _artifact_path(output_dir: Path, prefix: str, suffix: str) -> Path:
    """Return a deterministic artifact path that remains usable on Windows.

    Workflow stage names are intentionally descriptive, but nested stages can
    otherwise exceed the traditional Windows MAX_PATH limit.  Keep short names
    unchanged and shorten only the prefix, retaining a digest of the complete
    logical name so distinct stages cannot collide.
    """
    candidate = output_dir / f"{prefix}{suffix}"
    if len(str(candidate.resolve())) <= PORTABLE_ARTIFACT_PATH_LIMIT:
        return candidate

    digest = hashlib.sha256(prefix.encode("utf-8")).hexdigest()[:10]
    fixed_path = (output_dir / f"_{digest}{suffix}").resolve()
    available_prefix_chars = PORTABLE_ARTIFACT_PATH_LIMIT - len(str(fixed_path))
    if available_prefix_chars < 1:
        raise OSError(
            f"output directory is too long for portable artifact names: {output_dir.resolve()}"
        )
    shortened_prefix = prefix[:available_prefix_chars].rstrip("._-") or "artifact"
    return output_dir / f"{shortened_prefix}_{digest}{suffix}"


def audit_reference_join_patterns(
    *,
    reference_net_file: Path,
    candidate_net_file: Path,
    output_dir: Path,
    prefix: str = "reference_join_audit",
    reference_cluster_prefix: str = "cluster_",
    candidate_cluster_radius_m: float = 30.0,
    candidate_min_cluster_nodes: int = 3,
    match_radius_m: float = 45.0,
    structural_only: bool = False,
    equivalent_approach_edge_map: dict[str, str] | None = None,
) -> dict[str, Any]:
    if match_radius_m <= 0:
        return _failure("match_radius_m must be positive")
    if not reference_net_file.exists():
        return _failure(f"reference net file does not exist: {reference_net_file}")
    if not candidate_net_file.exists():
        return _failure(f"candidate net file does not exist: {candidate_net_file}")
    if structural_only:
        try:
            return _structural_only_report(
                reference_net_file=reference_net_file,
                candidate_net_file=candidate_net_file,
                output_dir=output_dir,
                prefix=prefix,
                reference_cluster_prefix=reference_cluster_prefix,
                candidate_cluster_radius_m=candidate_cluster_radius_m,
                candidate_min_cluster_nodes=candidate_min_cluster_nodes,
                match_radius_m=match_radius_m,
                equivalent_approach_edge_map=equivalent_approach_edge_map,
            )
        except (OSError, ET.ParseError, KeyError, ValueError) as exc:
            return _failure(f"{type(exc).__name__}: {exc}")

    try:
        reference_cases = _reference_join_cases(reference_net_file, reference_cluster_prefix)
        candidate_graph = _candidate_graph(candidate_net_file)
    except (OSError, ET.ParseError, KeyError, ValueError) as exc:
        return _failure(f"{type(exc).__name__}: {exc}")

    pattern_junction_ids = [
        str(case.get("reference_id", ""))
        for case in reference_cases
        if str(case.get("reference_id", ""))
    ]
    pattern_warnings = []
    try:
        junction_pattern_index = extract_junction_pattern_index(
            reference_net_file,
            junction_ids=pattern_junction_ids,
        )
    except (KeyError, TypeError, ValueError) as exc:
        junction_pattern_index = []
        pattern_warnings.append(f"junction pattern extraction failed: {type(exc).__name__}: {exc}")
    try:
        candidate_junction_pattern_index = extract_junction_pattern_index(
            candidate_net_file,
            junction_ids=pattern_junction_ids,
        )
    except (KeyError, TypeError, ValueError) as exc:
        candidate_junction_pattern_index = []
        pattern_warnings.append(f"candidate junction pattern extraction failed: {type(exc).__name__}: {exc}")
    junction_pattern_comparisons = _compare_same_id_patterns(
        junction_pattern_index,
        candidate_junction_pattern_index,
        equivalent_approach_edge_map=equivalent_approach_edge_map,
    )
    junction_pattern_mismatch_count = sum(
        1 for comparison in junction_pattern_comparisons if comparison["status"] != "pass"
    )
    junction_pattern_mismatch_field_counts = dict(
        Counter(
            field
            for comparison in junction_pattern_comparisons
            for field in comparison.get("mismatch_fields", [])
        )
    )
    junction_pattern_templates = summarize_junction_pattern_templates(junction_pattern_index)
    candidate_junction_pattern_templates = summarize_junction_pattern_templates(candidate_junction_pattern_index)
    reference_structural_signature_summary = _structural_signature_summary(junction_pattern_index)
    candidate_structural_signature_summary = _structural_signature_summary(candidate_junction_pattern_index)
    junction_structural_signature_delta = _structural_signature_delta(
        reference_structural_signature_summary,
        candidate_structural_signature_summary,
    )
    reference_network_structural_summary = _net_structural_summary(reference_net_file)
    candidate_network_structural_summary = _net_structural_summary(candidate_net_file)
    network_structural_delta = _network_structural_delta(
        reference_network_structural_summary,
        candidate_network_structural_summary,
    )
    tls_control_review = _tls_control_review(reference_network_structural_summary, candidate_network_structural_summary)

    output_dir.mkdir(parents=True, exist_ok=True)
    candidate_audit = audit_topology_fragmentation(
        net_file=candidate_net_file,
        output_dir=output_dir / "candidate_topology",
        prefix=f"{prefix}_candidate",
        cluster_radius_m=candidate_cluster_radius_m,
        min_cluster_nodes=candidate_min_cluster_nodes,
    )
    if candidate_audit.get("status") == "fail":
        return {
            **_failure(f"candidate topology audit failed: {candidate_audit.get('error', 'unknown error')}"),
            "candidate_topology_audit": candidate_audit,
        }

    candidate_clusters = list(candidate_audit.get("suspicious_clusters", []))
    matched_cases = [
        _match_reference_case(reference_case, candidate_clusters, candidate_graph, match_radius_m)
        for reference_case in reference_cases
    ]
    matched = [case for case in matched_cases if case["match_status"] == "matched"]

    cases_file = _artifact_path(output_dir, prefix, "_reference_join_cases.csv")
    junction_pattern_comparisons_file = _artifact_path(
        output_dir, prefix, "_junction_pattern_comparisons.csv"
    )
    junction_pattern_templates_file = _artifact_path(output_dir, prefix, "_junction_pattern_templates.json")
    junction_teacher_delta_file = _artifact_path(output_dir, prefix, "_junction_teacher_delta.json")
    summary_file = _artifact_path(output_dir, prefix, "_reference_join_audit.json")
    _write_cases_csv(cases_file, matched_cases)
    _write_junction_pattern_comparisons_csv(junction_pattern_comparisons_file, junction_pattern_comparisons)
    junction_pattern_templates_file.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "reference_net_file": str(reference_net_file),
                "candidate_net_file": str(candidate_net_file),
                "reference_policy_summary": summarize_junction_pattern_policy(junction_pattern_index),
                "candidate_policy_summary": summarize_junction_pattern_policy(candidate_junction_pattern_index),
                "reference_templates": junction_pattern_templates,
                "candidate_templates": candidate_junction_pattern_templates,
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    junction_teacher_delta = {
        "schema_version": 1,
        "reference_net_file": str(reference_net_file),
        "candidate_net_file": str(candidate_net_file),
        "reference_cluster_prefix": reference_cluster_prefix,
        "equivalent_approach_edge_map": equivalent_approach_edge_map or {},
        "junction_pattern_comparison_status": "fail"
        if junction_pattern_mismatch_count
        else ("pass" if junction_pattern_comparisons else "skipped"),
        "junction_pattern_mismatch_count": junction_pattern_mismatch_count,
        "junction_pattern_mismatch_field_counts": junction_pattern_mismatch_field_counts,
        "junction_structural_signature_status": junction_structural_signature_delta["status"],
        "junction_structural_signature_missing_counts": junction_structural_signature_delta["missing_counts"],
        "reference_structural_signature_summary": reference_structural_signature_summary,
        "candidate_structural_signature_summary": candidate_structural_signature_summary,
        "network_structural_delta_status": network_structural_delta["status"],
        "network_structural_missing_counts": network_structural_delta["missing_counts"],
        "network_structural_extra_counts": network_structural_delta["extra_counts"],
        "network_structural_junction_type_missing_counts": network_structural_delta["junction_type_missing_counts"],
        "network_structural_junction_type_extra_counts": network_structural_delta["junction_type_extra_counts"],
        "tls_control_review_status": tls_control_review["status"],
        "tls_control_review_queue_count": tls_control_review["queue_count"],
        "tls_control_review_queue": tls_control_review["queue"],
        "tls_controller_alignment": tls_control_review["controller_alignment"],
        "reference_network_structural_summary": reference_network_structural_summary,
        "candidate_network_structural_summary": candidate_network_structural_summary,
        "junction_pattern_comparisons": junction_pattern_comparisons,
        "junction_pattern_templates": junction_pattern_templates,
        "candidate_junction_pattern_templates": candidate_junction_pattern_templates,
        "matched_cases": matched,
    }
    junction_teacher_delta_file.write_text(
        json.dumps(junction_teacher_delta, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    report = {
        "status": "pass" if reference_cases else "blocked",
        "claim_status": "diagnostic-demo" if reference_cases else "blocked",
        "audit_mode": "full",
        "reference_net_file": str(reference_net_file),
        "candidate_net_file": str(candidate_net_file),
        "output_dir": str(output_dir),
        "reference_cluster_prefix": reference_cluster_prefix,
        "candidate_cluster_radius_m": candidate_cluster_radius_m,
        "candidate_min_cluster_nodes": candidate_min_cluster_nodes,
        "match_radius_m": match_radius_m,
        "equivalent_approach_edge_map": equivalent_approach_edge_map or {},
        "reference_case_count": len(reference_cases),
        "matched_case_count": len(matched),
        "unmatched_case_count": len(matched_cases) - len(matched),
        "candidate_topology_cluster_count": candidate_audit.get("suspicious_cluster_count", 0),
        "reference_type_counts": dict(Counter(case["reference_type"] for case in reference_cases)),
        "learned_rule_counts": dict(Counter(case["learned_rule"] for case in matched_cases)),
        "pattern_stats": _pattern_stats(reference_cases, matched),
        "junction_pattern_index": junction_pattern_index,
        "candidate_junction_pattern_index": candidate_junction_pattern_index,
        "junction_pattern_comparison_status": "fail"
        if junction_pattern_mismatch_count
        else ("pass" if junction_pattern_comparisons else "skipped"),
        "junction_pattern_mismatch_count": junction_pattern_mismatch_count,
        "junction_pattern_mismatch_field_counts": junction_pattern_mismatch_field_counts,
        "junction_structural_signature_status": junction_structural_signature_delta["status"],
        "junction_structural_signature_missing_counts": junction_structural_signature_delta["missing_counts"],
        "reference_structural_signature_summary": reference_structural_signature_summary,
        "candidate_structural_signature_summary": candidate_structural_signature_summary,
        "network_structural_delta_status": network_structural_delta["status"],
        "network_structural_missing_counts": network_structural_delta["missing_counts"],
        "network_structural_extra_counts": network_structural_delta["extra_counts"],
        "network_structural_junction_type_missing_counts": network_structural_delta["junction_type_missing_counts"],
        "network_structural_junction_type_extra_counts": network_structural_delta["junction_type_extra_counts"],
        "tls_control_review_status": tls_control_review["status"],
        "tls_control_review_queue_count": tls_control_review["queue_count"],
        "tls_control_review_queue": tls_control_review["queue"],
        "tls_controller_alignment": tls_control_review["controller_alignment"],
        "reference_network_structural_summary": reference_network_structural_summary,
        "candidate_network_structural_summary": candidate_network_structural_summary,
        "junction_pattern_comparisons": junction_pattern_comparisons,
        "junction_pattern_templates": junction_pattern_templates,
        "candidate_junction_pattern_templates": candidate_junction_pattern_templates,
        "junction_pattern_comparisons_file": str(junction_pattern_comparisons_file),
        "junction_pattern_templates_file": str(junction_pattern_templates_file),
        "junction_teacher_delta_file": str(junction_teacher_delta_file),
        "cases_file": str(cases_file),
        "summary_file": str(summary_file),
        "candidate_topology_audit_file": str(candidate_audit.get("report_file", "")),
        "matched_cases": matched,
        "all_cases": matched_cases,
        "warnings": _warnings(reference_cases, matched_cases) + pattern_warnings,
    }
    summary_file.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    return report


def _structural_only_report(
    *,
    reference_net_file: Path,
    candidate_net_file: Path,
    output_dir: Path,
    prefix: str,
    reference_cluster_prefix: str,
    candidate_cluster_radius_m: float,
    candidate_min_cluster_nodes: int,
    match_radius_m: float,
    equivalent_approach_edge_map: dict[str, str] | None = None,
) -> dict[str, Any]:
    reference_network_structural_summary = _net_structural_summary(reference_net_file)
    candidate_network_structural_summary = _net_structural_summary(candidate_net_file)
    network_structural_delta = _network_structural_delta(
        reference_network_structural_summary,
        candidate_network_structural_summary,
    )
    tls_control_review = _tls_control_review(reference_network_structural_summary, candidate_network_structural_summary)
    pattern_junction_ids, pattern_sample_warning = _structural_only_pattern_junction_ids(
        reference_net_file,
        candidate_net_file,
        limit=STRUCTURAL_ONLY_PATTERN_SAMPLE_LIMIT,
    )
    pattern_warnings = []
    try:
        junction_pattern_index = extract_junction_pattern_index(reference_net_file, junction_ids=pattern_junction_ids)
    except (KeyError, TypeError, ValueError) as exc:
        junction_pattern_index = []
        pattern_warnings.append(f"junction pattern extraction failed: {type(exc).__name__}: {exc}")
    try:
        candidate_junction_pattern_index = extract_junction_pattern_index(
            candidate_net_file,
            junction_ids=pattern_junction_ids,
        )
    except (KeyError, TypeError, ValueError) as exc:
        candidate_junction_pattern_index = []
        pattern_warnings.append(f"candidate junction pattern extraction failed: {type(exc).__name__}: {exc}")
    junction_pattern_comparisons = _compare_same_id_patterns(
        junction_pattern_index,
        candidate_junction_pattern_index,
        equivalent_approach_edge_map=equivalent_approach_edge_map,
    )
    junction_pattern_mismatch_count = sum(
        1 for comparison in junction_pattern_comparisons if comparison["status"] != "pass"
    )
    junction_pattern_mismatch_field_counts = dict(
        Counter(
            field
            for comparison in junction_pattern_comparisons
            for field in comparison.get("mismatch_fields", [])
        )
    )
    junction_pattern_templates = summarize_junction_pattern_templates(junction_pattern_index)
    candidate_junction_pattern_templates = summarize_junction_pattern_templates(candidate_junction_pattern_index)
    reference_structural_signature_summary = _structural_signature_summary(junction_pattern_index)
    candidate_structural_signature_summary = _structural_signature_summary(candidate_junction_pattern_index)
    junction_structural_signature_delta = _structural_signature_delta(
        reference_structural_signature_summary,
        candidate_structural_signature_summary,
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    junction_teacher_delta_file = _artifact_path(output_dir, prefix, "_junction_teacher_delta.json")
    junction_pattern_comparisons_file = _artifact_path(
        output_dir, prefix, "_junction_pattern_comparisons.csv"
    )
    junction_pattern_templates_file = _artifact_path(output_dir, prefix, "_junction_pattern_templates.json")
    summary_file = _artifact_path(output_dir, prefix, "_reference_join_audit.json")
    _write_junction_pattern_comparisons_csv(junction_pattern_comparisons_file, junction_pattern_comparisons)
    junction_pattern_templates_file.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "reference_net_file": str(reference_net_file),
                "candidate_net_file": str(candidate_net_file),
                "reference_policy_summary": summarize_junction_pattern_policy(junction_pattern_index),
                "candidate_policy_summary": summarize_junction_pattern_policy(candidate_junction_pattern_index),
                "reference_templates": junction_pattern_templates,
                "candidate_templates": candidate_junction_pattern_templates,
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    report = {
        "status": "pass",
        "claim_status": "diagnostic-demo",
        "audit_mode": "structural_only",
        "reference_net_file": str(reference_net_file),
        "candidate_net_file": str(candidate_net_file),
        "output_dir": str(output_dir),
        "reference_cluster_prefix": reference_cluster_prefix,
        "candidate_cluster_radius_m": candidate_cluster_radius_m,
        "candidate_min_cluster_nodes": candidate_min_cluster_nodes,
        "match_radius_m": match_radius_m,
        "equivalent_approach_edge_map": equivalent_approach_edge_map or {},
        "reference_case_count": 0,
        "matched_case_count": 0,
        "unmatched_case_count": 0,
        "candidate_topology_cluster_count": 0,
        "reference_type_counts": {},
        "learned_rule_counts": {},
        "pattern_stats": {},
        "junction_pattern_index": junction_pattern_index,
        "candidate_junction_pattern_index": candidate_junction_pattern_index,
        "junction_pattern_comparison_status": "fail"
        if junction_pattern_mismatch_count
        else ("pass" if junction_pattern_comparisons else "skipped"),
        "junction_pattern_mismatch_count": junction_pattern_mismatch_count,
        "junction_pattern_mismatch_field_counts": junction_pattern_mismatch_field_counts,
        "junction_structural_signature_status": junction_structural_signature_delta["status"],
        "junction_structural_signature_missing_counts": junction_structural_signature_delta["missing_counts"],
        "reference_structural_signature_summary": reference_structural_signature_summary,
        "candidate_structural_signature_summary": candidate_structural_signature_summary,
        "network_structural_delta_status": network_structural_delta["status"],
        "network_structural_missing_counts": network_structural_delta["missing_counts"],
        "network_structural_extra_counts": network_structural_delta["extra_counts"],
        "network_structural_junction_type_missing_counts": network_structural_delta["junction_type_missing_counts"],
        "network_structural_junction_type_extra_counts": network_structural_delta["junction_type_extra_counts"],
        "tls_control_review_status": tls_control_review["status"],
        "tls_control_review_queue_count": tls_control_review["queue_count"],
        "tls_control_review_queue": tls_control_review["queue"],
        "tls_controller_alignment": tls_control_review["controller_alignment"],
        "reference_network_structural_summary": reference_network_structural_summary,
        "candidate_network_structural_summary": candidate_network_structural_summary,
        "junction_pattern_comparisons": junction_pattern_comparisons,
        "junction_pattern_templates": junction_pattern_templates,
        "candidate_junction_pattern_templates": candidate_junction_pattern_templates,
        "junction_pattern_comparisons_file": str(junction_pattern_comparisons_file),
        "junction_pattern_templates_file": str(junction_pattern_templates_file),
        "junction_teacher_delta_file": str(junction_teacher_delta_file),
        "cases_file": "",
        "summary_file": str(summary_file),
        "candidate_topology_audit_file": "",
        "matched_cases": [],
        "all_cases": [],
        "warnings": ["reference join audit ran in structural-only mode; full case matching was skipped"]
        + ([pattern_sample_warning] if pattern_sample_warning else [])
        + pattern_warnings,
    }
    junction_teacher_delta_file.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "audit_mode": "structural_only",
                "reference_net_file": str(reference_net_file),
                "candidate_net_file": str(candidate_net_file),
                "equivalent_approach_edge_map": equivalent_approach_edge_map or {},
                "junction_pattern_comparison_status": report["junction_pattern_comparison_status"],
                "junction_pattern_mismatch_count": junction_pattern_mismatch_count,
                "junction_pattern_mismatch_field_counts": junction_pattern_mismatch_field_counts,
                "junction_structural_signature_status": junction_structural_signature_delta["status"],
                "junction_structural_signature_missing_counts": junction_structural_signature_delta["missing_counts"],
                "network_structural_delta_status": network_structural_delta["status"],
                "network_structural_missing_counts": network_structural_delta["missing_counts"],
                "network_structural_extra_counts": network_structural_delta["extra_counts"],
                "network_structural_junction_type_missing_counts": network_structural_delta[
                    "junction_type_missing_counts"
                ],
                "network_structural_junction_type_extra_counts": network_structural_delta[
                    "junction_type_extra_counts"
                ],
                "tls_control_review_status": tls_control_review["status"],
                "tls_control_review_queue_count": tls_control_review["queue_count"],
                "tls_control_review_queue": tls_control_review["queue"],
                "tls_controller_alignment": tls_control_review["controller_alignment"],
                "reference_network_structural_summary": reference_network_structural_summary,
                "candidate_network_structural_summary": candidate_network_structural_summary,
                "junction_pattern_comparisons": junction_pattern_comparisons,
                "matched_cases": [],
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    summary_file.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    return report


def _structural_only_pattern_junction_ids(
    reference_net_file: Path,
    candidate_net_file: Path,
    *,
    limit: int,
) -> tuple[list[str], str]:
    reference = _junction_pattern_candidates(reference_net_file)
    candidate = _junction_pattern_candidates(candidate_net_file)
    common_ids = sorted(set(reference) & set(candidate))
    ranked = sorted(
        common_ids,
        key=lambda junction_id: (
            not (reference[junction_id]["is_tls"] or candidate[junction_id]["is_tls"]),
            -max(reference[junction_id]["inc_lane_count"], candidate[junction_id]["inc_lane_count"]),
            junction_id,
        ),
    )
    warning = ""
    if len(ranked) > limit:
        warning = (
            f"structural-only junction pattern comparison sampled {limit} of {len(ranked)} common junction ids; "
            "full topology matching was skipped"
        )
    return ranked[:limit], warning


def _junction_pattern_candidates(net_file: Path) -> dict[str, dict[str, Any]]:
    root = ET.parse(net_file).getroot()
    candidates: dict[str, dict[str, Any]] = {}
    for junction in root.findall("junction"):
        junction_id = junction.attrib.get("id", "")
        if not junction_id or junction_id.startswith(":") or junction.attrib.get("type") == "internal":
            continue
        candidates[junction_id] = {
            "is_tls": junction.attrib.get("type") == "traffic_light",
            "inc_lane_count": len(junction.attrib.get("incLanes", "").split()),
        }
    return candidates


def _failure(error: str) -> dict[str, Any]:
    return {
        "status": "fail",
        "claim_status": "construction-invalid",
        "error": error,
    }


def _reference_join_cases(net_file: Path, cluster_prefix: str) -> list[dict[str, Any]]:
    root = ET.parse(net_file).getroot()
    xy_to_latlon = _coordinate_converter(root, net_file)
    cases = []
    for junction in root.findall("junction"):
        junction_id = junction.attrib.get("id", "")
        if not junction_id.startswith(cluster_prefix):
            continue
        x = float(junction.attrib["x"])
        y = float(junction.attrib["y"])
        lat, lon, coordinate_status = _junction_latlon(x, y, xy_to_latlon)
        joined_nodes = _joined_source_nodes(junction_id, cluster_prefix)
        inc_edges = sorted(
            {
                _lane_to_edge_id(lane_id)
                for lane_id in junction.attrib.get("incLanes", "").split()
                if lane_id and not lane_id.startswith(":")
            }
        )
        int_lanes = [lane_id for lane_id in junction.attrib.get("intLanes", "").split() if lane_id]
        cases.append(
            {
                "reference_id": junction_id,
                "reference_type": junction.attrib.get("type", ""),
                "reference_x": round(x, 3),
                "reference_y": round(y, 3),
                "reference_lat": round(lat, 7),
                "reference_lon": round(lon, 7),
                "reference_coordinate_status": coordinate_status,
                "reference_joined_source_nodes": joined_nodes,
                "reference_joined_source_node_count": len(joined_nodes),
                "reference_approach_edge_ids": inc_edges,
                "reference_approach_edge_count": len(inc_edges),
                "reference_internal_lane_count": len(int_lanes),
                "reference_shape_point_count": len(_parse_shape(junction.attrib.get("shape", ""))),
            }
        )
    cases.sort(
        key=lambda case: (
            -int(case["reference_joined_source_node_count"]),
            -int(case["reference_approach_edge_count"]),
            str(case["reference_id"]),
        )
    )
    return cases


def _candidate_graph(net_file: Path) -> dict[str, Any]:
    root = ET.parse(net_file).getroot()
    junction_ids = {
        junction.attrib["id"]
        for junction in root.findall("junction")
        if "id" in junction.attrib and not junction.attrib["id"].startswith(":")
    }
    edges = []
    for edge in root.findall("edge"):
        edge_id = edge.attrib.get("id", "")
        if not edge_id or edge_id.startswith(":"):
            continue
        from_node = edge.attrib.get("from", "")
        to_node = edge.attrib.get("to", "")
        if not from_node or not to_node:
            continue
        edges.append({"id": edge_id, "from": from_node, "to": to_node})
    return {
        "junction_ids": junction_ids,
        "edges": edges,
    }


def _match_reference_case(
    reference_case: dict[str, Any],
    candidate_clusters: list[dict[str, Any]],
    candidate_graph: dict[str, Any],
    match_radius_m: float,
) -> dict[str, Any]:
    source_match = _match_reference_sources(reference_case, candidate_graph)
    best_cluster = None
    best_distance = math.inf
    for cluster in candidate_clusters:
        distance = _case_cluster_distance(reference_case, cluster)
        if distance < best_distance:
            best_distance = distance
            best_cluster = cluster

    if best_cluster is None or best_distance > match_radius_m:
        if _has_source_join_evidence(source_match):
            return {
                **reference_case,
                **source_match,
                "match_status": "matched",
                "match_distance_m": round(best_distance, 3) if math.isfinite(best_distance) else "",
                "matched_candidate_cluster_id": "",
                "matched_candidate_node_ids": source_match["matched_reference_source_node_ids"],
                "matched_candidate_node_count": source_match["matched_reference_source_node_count"],
                "matched_candidate_internal_edge_count": source_match["matched_reference_source_internal_edge_count"],
                "matched_candidate_internal_edge_ids": source_match["matched_reference_source_internal_edge_ids"],
                "matched_candidate_boundary_edge_ids": source_match["matched_reference_source_boundary_edge_ids"],
                "matched_candidate_approach_count": source_match["matched_reference_source_boundary_edge_count"],
                "matched_candidate_traffic_light_node_count": 0,
                "matched_candidate_risk_flags": ["map_review_required", "reference_source_join"],
                "matched_candidate_google_maps_url": _google_maps_url(reference_case),
                "learned_rule_basis": "reference_source_nodes",
                "learned_rule": "tum_like_join_candidate",
            }
        return {
            **reference_case,
            **source_match,
            "match_status": "unmatched",
            "match_distance_m": round(best_distance, 3) if math.isfinite(best_distance) else "",
            "matched_candidate_cluster_id": "",
            "matched_candidate_node_count": 0,
            "matched_candidate_internal_edge_count": 0,
            "matched_candidate_internal_edge_ids": [],
            "learned_rule_basis": "none",
            "learned_rule": "no_nearby_torii_cluster",
        }

    learned_rule_basis = "reference_source_nodes" if _has_source_join_evidence(source_match) else "spatial_cluster"
    learned_rule = _learned_rule(reference_case, best_cluster, source_match)
    return {
        **reference_case,
        **source_match,
        "match_status": "matched",
        "match_distance_m": round(best_distance, 3),
        "matched_candidate_cluster_id": best_cluster.get("cluster_id", ""),
        "matched_candidate_node_ids": list(best_cluster.get("node_ids", [])),
        "matched_candidate_node_count": int(best_cluster.get("node_count", 0)),
        "matched_candidate_internal_edge_count": int(best_cluster.get("internal_edge_count", 0)),
        "matched_candidate_internal_edge_ids": list(best_cluster.get("internal_edge_ids", [])),
        "matched_candidate_boundary_edge_ids": list(best_cluster.get("boundary_edge_ids", [])),
        "matched_candidate_approach_count": int(best_cluster.get("approach_count", 0)),
        "matched_candidate_traffic_light_node_count": int(best_cluster.get("traffic_light_node_count", 0)),
        "matched_candidate_risk_flags": list(best_cluster.get("risk_flags", [])),
        "matched_candidate_google_maps_url": best_cluster.get("google_maps_url", ""),
        "learned_rule_basis": learned_rule_basis,
        "learned_rule": learned_rule,
    }


def _match_reference_sources(reference_case: dict[str, Any], candidate_graph: dict[str, Any]) -> dict[str, Any]:
    source_node_ids = list(reference_case.get("reference_joined_source_nodes", []))
    junction_ids = set(candidate_graph.get("junction_ids", set()))
    matched_node_ids = sorted(node_id for node_id in source_node_ids if node_id in junction_ids)
    matched_node_id_set = set(matched_node_ids)
    internal_edge_ids = sorted(
        edge["id"]
        for edge in candidate_graph.get("edges", [])
        if edge["from"] in matched_node_id_set and edge["to"] in matched_node_id_set
    )
    boundary_edge_ids = sorted(
        edge["id"]
        for edge in candidate_graph.get("edges", [])
        if (edge["from"] in matched_node_id_set) ^ (edge["to"] in matched_node_id_set)
    )
    return {
        "matched_reference_source_node_ids": matched_node_ids,
        "matched_reference_source_node_count": len(matched_node_ids),
        "reference_source_node_match_ratio": round(len(matched_node_ids) / len(source_node_ids), 3)
        if source_node_ids
        else 0.0,
        "matched_reference_source_internal_edge_ids": internal_edge_ids,
        "matched_reference_source_internal_edge_count": len(internal_edge_ids),
        "matched_reference_source_boundary_edge_ids": boundary_edge_ids,
        "matched_reference_source_boundary_edge_count": len(boundary_edge_ids),
    }


def _has_source_join_evidence(source_match: dict[str, Any]) -> bool:
    if int(source_match.get("matched_reference_source_node_count", 0)) < 2:
        return False
    return (
        int(source_match.get("matched_reference_source_internal_edge_count", 0)) > 0
        or int(source_match.get("matched_reference_source_boundary_edge_count", 0)) >= 2
    )


def _learned_rule(
    reference_case: dict[str, Any],
    candidate_cluster: dict[str, Any],
    source_match: dict[str, Any],
) -> str:
    if _has_source_join_evidence(source_match) or int(candidate_cluster.get("internal_edge_count", 0)) > 0:
        return "tum_like_join_candidate"
    return "needs_case_review"


def _pattern_stats(reference_cases: list[dict[str, Any]], matched_cases: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "reference_joined_source_node_counts": _count_field(reference_cases, "reference_joined_source_node_count"),
        "reference_approach_edge_counts": _count_field(reference_cases, "reference_approach_edge_count"),
        "reference_type_counts": _count_field(reference_cases, "reference_type"),
        "matched_reference_source_node_counts": _count_field(matched_cases, "matched_reference_source_node_count"),
        "matched_reference_source_internal_edge_counts": _count_field(
            matched_cases,
            "matched_reference_source_internal_edge_count",
        ),
        "matched_candidate_node_counts": _count_field(matched_cases, "matched_candidate_node_count"),
        "matched_candidate_approach_counts": _count_field(matched_cases, "matched_candidate_approach_count"),
        "matched_candidate_internal_edge_counts": _count_field(matched_cases, "matched_candidate_internal_edge_count"),
        "matched_reference_type_counts": _count_field(matched_cases, "reference_type"),
        "learned_rule_basis_counts": _count_field(matched_cases, "learned_rule_basis"),
    }


def _structural_signature_summary(patterns: list[dict[str, Any]]) -> dict[str, int]:
    return {
        "pattern_count": len(patterns),
        "internal_bundle_pattern_count": sum(1 for pattern in patterns if int(pattern.get("internal_edge_count", 0)) > 0),
        "movement_signature_pattern_count": sum(1 for pattern in patterns if pattern.get("movement_signature_counts")),
        "pedestrian_separation_pattern_count": sum(
            1
            for pattern in patterns
            if int((pattern.get("internal_function_counts", {}) or {}).get("crossing", 0)) > 0
            or int((pattern.get("internal_function_counts", {}) or {}).get("walkingarea", 0)) > 0
        ),
        "request_bit_vector_pattern_count": sum(
            1
            for pattern in patterns
            if int(pattern.get("request_count", 0)) > 0 and bool(pattern.get("request_bit_lengths_ok", False))
        ),
        "tls_pattern_count": sum(1 for pattern in patterns if bool(pattern.get("has_tls", False))),
    }


def _structural_signature_delta(reference: dict[str, int], candidate: dict[str, int]) -> dict[str, Any]:
    missing = {
        key: reference[key] - candidate.get(key, 0)
        for key in reference
        if key != "pattern_count" and reference[key] > candidate.get(key, 0)
    }
    return {"status": "fail" if missing else "pass", "missing_counts": missing}


def _net_structural_summary(net_file: Path) -> dict[str, Any]:
    root = ET.parse(net_file).getroot()
    edge_function_counts = Counter(_edge_function(edge) for edge in root.findall("edge"))
    plain_edge_type_counts: Counter[str] = Counter()
    plain_edge_type_pedestrian_lane_counts: Counter[str] = Counter()
    for edge in root.findall("edge"):
        if _edge_function(edge) != "plain":
            continue
        edge_type = edge.attrib.get("type", "") or "none"
        plain_edge_type_counts[edge_type] += 1
        if any("pedestrian" in lane.attrib.get("allow", "").split() for lane in edge.findall("lane")):
            plain_edge_type_pedestrian_lane_counts[edge_type] += 1
    junction_ids = {
        junction.attrib.get("id", "")
        for junction in root.findall("junction")
        if junction.attrib.get("id") and not junction.attrib.get("id", "").startswith(":")
    }
    junction_type_counts = Counter(
        junction.attrib.get("type", "") or "blank"
        for junction in root.findall("junction")
        if not junction.attrib.get("id", "").startswith(":") and junction.attrib.get("type") != "internal"
    )
    connections = root.findall("connection")
    tls_summary = _tls_semantic_summary(root, connections, junction_ids, net_file=net_file)
    return {
        "plain_edge_count": edge_function_counts.get("plain", 0),
        "internal_edge_count": edge_function_counts.get("internal", 0),
        "crossing_edge_count": edge_function_counts.get("crossing", 0),
        "walkingarea_edge_count": edge_function_counts.get("walkingarea", 0),
        "connection_count": len(connections),
        "request_count": sum(len(junction.findall("request")) for junction in root.findall("junction")),
        "tl_logic_count": len(root.findall("tlLogic")),
        "traffic_light_junction_count": junction_type_counts.get("traffic_light", 0),
        "tls_controlled_connection_count": sum(
            1 for connection in connections if connection.attrib.get("tl") and connection.attrib.get("linkIndex")
        ),
        "tl_connection_missing_linkindex_count": sum(
            1 for connection in connections if connection.attrib.get("tl") and not connection.attrib.get("linkIndex")
        ),
        **tls_summary,
        "junction_type_counts": dict(sorted(junction_type_counts.items())),
        "edge_function_counts": dict(sorted(edge_function_counts.items())),
        "plain_edge_type_counts": dict(sorted(plain_edge_type_counts.items())),
        "plain_edge_type_pedestrian_lane_counts": dict(sorted(plain_edge_type_pedestrian_lane_counts.items())),
    }


def _tls_semantic_summary(
    root: ET.Element,
    connections: list[ET.Element],
    junction_ids: set[str],
    *,
    net_file: Path,
) -> dict[str, Any]:
    tl_logic_ids = [tl.attrib["id"] for tl in root.findall("tlLogic") if tl.attrib.get("id")]
    passenger_edges = {
        edge.attrib.get("id", "")
        for edge in root.findall("edge")
        if edge.attrib.get("id") and any(lane_allows_passenger(lane) for lane in edge.findall("lane"))
    }
    known_edges = {edge.attrib.get("id", "") for edge in root.findall("edge") if edge.attrib.get("id")}
    edge_endpoints = {
        edge.attrib.get("id", ""): (edge.attrib.get("from", ""), edge.attrib.get("to", ""))
        for edge in root.findall("edge")
        if edge.attrib.get("id")
    }
    passenger_lane_rank_by_edge: dict[str, dict[str, int]] = {}
    for edge in root.findall("edge"):
        edge_id = edge.attrib.get("id", "")
        if not edge_id:
            continue
        passenger_lane_indexes = sorted(
            (lane.attrib.get("index", "") for lane in edge.findall("lane") if lane_allows_passenger(lane)),
            key=lambda value: (0, int(value)) if value.isdigit() else (1, value),
        )
        passenger_lane_rank_by_edge[edge_id] = {
            lane_index: rank for rank, lane_index in enumerate(passenger_lane_indexes)
        }
    passenger_approach_bearings = {
        edge_id: bearing
        for edge in root.findall("edge")
        if (edge_id := edge.attrib.get("id", "")) in passenger_edges
        and (bearing := _incoming_edge_bearing_deg(edge)) is not None
    }
    traffic_light_ids = {
        junction.attrib["id"]
        for junction in root.findall("junction")
        if junction.attrib.get("id") and junction.attrib.get("type") == "traffic_light"
    }
    junction_xy = {
        junction.attrib["id"]: (float(junction.attrib["x"]), float(junction.attrib["y"]))
        for junction in root.findall("junction")
        if junction.attrib.get("id")
        and junction.attrib.get("x") is not None
        and junction.attrib.get("y") is not None
    }
    xy_to_latlon = _coordinate_converter(root, net_file)
    phase_state_lengths = {
        tl_id: max((len(phase.attrib.get("state", "")) for phase in tl.findall("phase")), default=0)
        for tl in root.findall("tlLogic")
        if (tl_id := tl.attrib.get("id"))
    }
    controlled_connection_counts: Counter[str] = Counter()
    controlled_junctions_by_tl = {tl_id: set() for tl_id in tl_logic_ids}
    controlled_junctions: set[str] = set()
    linkindex_group_counts: Counter[tuple[str, str]] = Counter()
    linkindexes_by_tl = {tl_id: set() for tl_id in tl_logic_ids}
    known_from_edges_by_tl = {tl_id: set() for tl_id in tl_logic_ids}
    passenger_from_edges_by_tl = {tl_id: set() for tl_id in tl_logic_ids}
    passenger_movements_by_tl_and_edge: dict[str, dict[str, Counter[str]]] = {
        tl_id: {} for tl_id in tl_logic_ids
    }
    for connection in connections:
        tl_id = connection.attrib.get("tl", "")
        link_index = connection.attrib.get("linkIndex", "")
        if not tl_id or not link_index:
            continue
        controlled_connection_counts[tl_id] += 1
        from_edge = connection.attrib.get("from", "")
        if from_edge in known_edges:
            known_from_edges_by_tl.setdefault(tl_id, set()).add(from_edge)
        if from_edge in passenger_edges:
            passenger_from_edges_by_tl.setdefault(tl_id, set()).add(from_edge)
            to_edge = connection.attrib.get("to", "")
            from_lane = connection.attrib.get("fromLane", "")
            to_lane = connection.attrib.get("toLane", "")
            from_passenger_rank = passenger_lane_rank_by_edge.get(from_edge, {}).get(from_lane)
            to_passenger_rank = passenger_lane_rank_by_edge.get(to_edge, {}).get(to_lane)
            movement_signature = "|".join(
                (
                    f"dir={connection.attrib.get('dir', '')}",
                    f"fromPassengerLaneRank={from_passenger_rank if from_passenger_rank is not None else from_lane}",
                    f"toPassengerLaneRank={to_passenger_rank if to_passenger_rank is not None else to_lane}",
                )
            )
            passenger_movements_by_tl_and_edge.setdefault(tl_id, {}).setdefault(
                from_edge, Counter()
            )[movement_signature] += 1
        linkindex_group_counts[(tl_id, link_index)] += 1
        try:
            linkindexes_by_tl.setdefault(tl_id, set()).add(int(link_index))
        except ValueError:
            pass
        junction_id = _connection_junction_id(connection, junction_ids)
        if junction_id:
            controlled_junctions_by_tl.setdefault(tl_id, set()).add(junction_id)
            controlled_junctions.add(junction_id)
    shared_linkindex_groups_by_tl: Counter[str] = Counter()
    for (tl_id, _link_index), count in linkindex_group_counts.items():
        if count > 1:
            shared_linkindex_groups_by_tl[tl_id] += 1
    tl_logic_control_records = []
    for tl_id in tl_logic_ids:
        linkindexes = sorted(linkindexes_by_tl.get(tl_id, set()))
        phase_state_length = int(phase_state_lengths.get(tl_id, 0))
        controlled_xy = [
            junction_xy[junction_id]
            for junction_id in controlled_junctions_by_tl.get(tl_id, set())
            if junction_id in junction_xy
        ]
        centroid_fields: dict[str, Any] = {
            "controlled_junction_centroid_lat": None,
            "controlled_junction_centroid_lon": None,
            "controlled_junction_centroid_status": "unavailable",
        }
        if controlled_xy:
            centroid_x = sum(point[0] for point in controlled_xy) / len(controlled_xy)
            centroid_y = sum(point[1] for point in controlled_xy) / len(controlled_xy)
            centroid_lat, centroid_lon, centroid_status = _junction_latlon(
                centroid_x,
                centroid_y,
                xy_to_latlon,
            )
            centroid_fields = {
                "controlled_junction_centroid_lat": round(centroid_lat, 8),
                "controlled_junction_centroid_lon": round(centroid_lon, 8),
                "controlled_junction_centroid_status": centroid_status,
                "controlled_junction_points": [
                    {
                        "junction_id": junction_id,
                        "lat": round(point_lat, 8),
                        "lon": round(point_lon, 8),
                        "status": point_status,
                    }
                    for junction_id in sorted(controlled_junctions_by_tl.get(tl_id, set()))
                    if junction_id in junction_xy
                    for point_lat, point_lon, point_status in [
                        _junction_latlon(*junction_xy[junction_id], xy_to_latlon)
                    ]
                ],
            }
        else:
            centroid_fields["controlled_junction_points"] = []
        controlled_junction_ids = controlled_junctions_by_tl.get(tl_id, set())
        controller_internal_passenger_edges = {
            edge_id
            for edge_id in passenger_from_edges_by_tl.get(tl_id, set())
            if edge_id in edge_endpoints
            and edge_endpoints[edge_id][0] in controlled_junction_ids
            and edge_endpoints[edge_id][1] in controlled_junction_ids
        }
        tl_logic_control_records.append(
            {
                "tl_id": tl_id,
                "controlled_connection_count": int(controlled_connection_counts.get(tl_id, 0)),
                "controlled_junction_count": len(controlled_junctions_by_tl.get(tl_id, set())),
                "junction_ids": sorted(controlled_junctions_by_tl.get(tl_id, set())),
                "controlled_known_from_edge_count": len(known_from_edges_by_tl.get(tl_id, set())),
                "controlled_passenger_from_edge_count": len(passenger_from_edges_by_tl.get(tl_id, set())),
                "passenger_from_edge_ids": sorted(passenger_from_edges_by_tl.get(tl_id, set())),
                "controller_internal_passenger_from_edge_ids": sorted(
                    controller_internal_passenger_edges
                ),
                "passenger_approaches": [
                    {
                        "edge_id": edge_id,
                        "split_root_edge_id": _split_root_edge_id(edge_id),
                        "bearing_deg": passenger_approach_bearings.get(edge_id),
                        "controlled_connection_count": sum(
                            passenger_movements_by_tl_and_edge.get(tl_id, {}).get(edge_id, {}).values()
                        ),
                        "movement_signature_counts": dict(
                            sorted(
                                passenger_movements_by_tl_and_edge.get(tl_id, {}).get(
                                    edge_id, {}
                                ).items()
                            )
                        ),
                    }
                    for edge_id in sorted(passenger_from_edges_by_tl.get(tl_id, set()))
                    if edge_id not in controller_internal_passenger_edges
                ],
                "linkindexes": linkindexes,
                "controlled_linkindex_count": len(linkindexes),
                "phase_state_length": phase_state_length,
                "shared_linkindex_group_count": int(shared_linkindex_groups_by_tl.get(tl_id, 0)),
                "sparse_linkindex": bool(linkindexes and phase_state_length > len(linkindexes)),
                **centroid_fields,
            }
        )
    return {
        "tl_logic_controlled_connection_count_distribution": _count_distribution(
            controlled_connection_counts.get(tl_id, 0) for tl_id in tl_logic_ids
        ),
        "tl_logic_controlled_junction_count_distribution": _count_distribution(
            len(controlled_junctions_by_tl.get(tl_id, set())) for tl_id in tl_logic_ids
        ),
        "tl_logic_controlled_passenger_from_edge_count_distribution": _count_distribution(
            len(passenger_from_edges_by_tl.get(tl_id, set())) for tl_id in tl_logic_ids
        ),
        "low_passenger_approach_tl_logic_count": sum(
            1
            for tl_id in tl_logic_ids
            if known_from_edges_by_tl.get(tl_id) and len(passenger_from_edges_by_tl.get(tl_id, set())) <= 2
        ),
        "multi_junction_tl_logic_count": sum(
            1 for tl_id in tl_logic_ids if len(controlled_junctions_by_tl.get(tl_id, set())) > 1
        ),
        "traffic_light_junction_without_tls_connection_count": len(traffic_light_ids - controlled_junctions),
        "traffic_light_junction_without_tls_connection_ids": sorted(traffic_light_ids - controlled_junctions),
        "tls_shared_linkindex_group_count": sum(1 for count in linkindex_group_counts.values() if count > 1),
        "tls_sparse_linkindex_tl_logic_count": sum(
            1
            for tl_id in tl_logic_ids
            if linkindexes_by_tl.get(tl_id)
            and int(phase_state_lengths.get(tl_id, 0)) > len(linkindexes_by_tl.get(tl_id, set()))
        ),
        "tl_logic_control_records": tl_logic_control_records,
    }


def _count_distribution(values: Any) -> dict[str, int]:
    return dict(sorted(Counter(str(value) for value in values).items(), key=lambda item: int(item[0])))


def _tls_control_review(reference: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
    controller_alignment = _tls_controller_alignment(reference, candidate)
    reference_max_junction_count = _max_distribution_key(
        reference.get("tl_logic_controlled_junction_count_distribution", {})
    )
    reference_sparse_budget = int(reference.get("tls_sparse_linkindex_tl_logic_count", 0))
    reference_low_passenger_approach_budget = int(reference.get("low_passenger_approach_tl_logic_count", 0))
    sparse_seen = 0
    low_passenger_approach_seen = 0
    queue = []
    for record in candidate.get("tl_logic_control_records", []):
        controlled_junction_count = int(record.get("controlled_junction_count", 0))
        controlled_known_from_edge_count = int(record.get("controlled_known_from_edge_count", 0))
        controlled_passenger_from_edge_count = int(record.get("controlled_passenger_from_edge_count", 0))
        if controlled_known_from_edge_count and controlled_passenger_from_edge_count <= 2:
            low_passenger_approach_seen += 1
            if low_passenger_approach_seen > reference_low_passenger_approach_budget:
                queue.append(
                    {
                        "repair_category": "tls_reality_review",
                        "review_type": "downgrade_low_vehicle_approach_tls",
                        "tl_id": record.get("tl_id", ""),
                        "controlled_connection_count": int(record.get("controlled_connection_count", 0)),
                        "controlled_known_from_edge_count": controlled_known_from_edge_count,
                        "controlled_passenger_from_edge_count": controlled_passenger_from_edge_count,
                        "reference_low_passenger_approach_tl_logic_count": reference_low_passenger_approach_budget,
                        "passenger_from_edge_ids": list(record.get("passenger_from_edge_ids", [])),
                        "reason": "candidate tlLogic has few passenger from-edges beyond the reference low-approach budget",
                    }
                )
        if controlled_junction_count > reference_max_junction_count:
            queue.append(
                {
                    "repair_category": "tls_controller_cardinality_repair",
                    "review_type": "split_multi_junction_tls",
                    "tl_id": record.get("tl_id", ""),
                    "controlled_connection_count": int(record.get("controlled_connection_count", 0)),
                    "controlled_junction_count": controlled_junction_count,
                    "reference_max_controlled_junction_count": reference_max_junction_count,
                    "junction_ids": list(record.get("junction_ids", [])),
                    "reason": "candidate tlLogic controls more junctions than the reference maximum",
                }
            )
        if bool(record.get("sparse_linkindex", False)):
            sparse_seen += 1
            if sparse_seen > reference_sparse_budget:
                queue.append(
                    {
                        "repair_category": "tls_linkindex_phase_repair",
                        "review_type": "inspect_sparse_linkindex",
                        "tl_id": record.get("tl_id", ""),
                        "controlled_connection_count": int(record.get("controlled_connection_count", 0)),
                        "controlled_linkindex_count": int(record.get("controlled_linkindex_count", 0)),
                        "phase_state_length": int(record.get("phase_state_length", 0)),
                        "linkindexes": list(record.get("linkindexes", [])),
                        "reason": "candidate tlLogic phase state has more positions than controlled linkIndexes",
                    }
                )
    for junction_id in candidate.get("traffic_light_junction_without_tls_connection_ids", []):
        queue.append(
            {
                "repair_category": "tls_controller_cardinality_repair",
                "review_type": "bind_or_downgrade_uncontrolled_traffic_light_junction",
                "junction_id": junction_id,
                "reason": "traffic_light junction has no controlled connection",
            }
        )
    for key, review_type, repair_category, reason in (
        (
            "tls_controlled_connection_count",
            "restore_tls_controlled_connections",
            "tls_controller_cardinality_repair",
            "candidate has fewer TLS-controlled connections than the reference",
        ),
        (
            "multi_junction_tl_logic_count",
            "restore_reference_multi_junction_tls_scope",
            "tls_controller_cardinality_repair",
            "candidate has fewer reference-style multi-junction tlLogic scopes",
        ),
        (
            "tls_shared_linkindex_group_count",
            "restore_shared_linkindex_groups",
            "tls_linkindex_phase_repair",
            "candidate has fewer shared linkIndex groups than the reference",
        ),
        (
            "tls_sparse_linkindex_tl_logic_count",
            "inspect_reference_sparse_linkindex_programs",
            "tls_linkindex_phase_repair",
            "candidate has fewer reference-style sparse linkIndex programs",
        ),
    ):
        reference_count = int(reference.get(key, 0))
        candidate_count = int(candidate.get(key, 0))
        if reference_count > candidate_count:
            queue.append(
                {
                    "repair_category": repair_category,
                    "review_type": review_type,
                    "reference_count": reference_count,
                    "candidate_count": candidate_count,
                    "missing_count": reference_count - candidate_count,
                    "reason": reason,
                }
            )
    return {
        "status": "needs_review" if queue else "pass",
        "queue_count": len(queue),
        "queue": queue,
        "controller_alignment": controller_alignment,
    }


def _tls_controller_alignment(
    reference: dict[str, Any],
    candidate: dict[str, Any],
    *,
    max_distance_m: float = 100.0,
    controller_group_distance_m: float = 30.0,
) -> dict[str, Any]:
    """Pair TLS controllers geographically without assuming stable controller IDs.

    This is diagnostic evidence, not a repair instruction.  A global count delta
    can hide simultaneous local deficits and surpluses caused by controller
    splitting, joining, or manual junction aggregation.
    """

    def usable_records(summary: dict[str, Any]) -> list[dict[str, Any]]:
        return [
            record
            for record in summary.get("tl_logic_control_records", [])
            if int(record.get("controlled_connection_count", 0)) > 0
            and isinstance(record.get("controlled_junction_centroid_lat"), (int, float))
            and isinstance(record.get("controlled_junction_centroid_lon"), (int, float))
            and not str(record.get("controlled_junction_centroid_status", "")).startswith("xy_fallback")
        ]

    reference_records = usable_records(reference)
    candidate_records = usable_records(candidate)
    if not reference_records or not candidate_records:
        return {
            "status": "unavailable",
            "method": "injective_nearest_controlled_junction_centroid",
            "max_distance_m": max_distance_m,
            "reason": "one or both networks lack georeferenced controlled-junction centroids",
            "pairs": [],
        }

    distances = sorted(
        (
            _tls_controller_distance_m(reference_record, candidate_record),
            reference_index,
            candidate_index,
        )
        for reference_index, reference_record in enumerate(reference_records)
        for candidate_index, candidate_record in enumerate(candidate_records)
    )
    reference_neighborhoods = []
    for reference_index, reference_record in enumerate(reference_records):
        neighbors = sorted(
            [
                [distance_m, candidate_records[candidate_index]]
                for distance_m, candidate_reference_index, candidate_index in distances
                if candidate_reference_index == reference_index and distance_m <= max_distance_m
            ],
            key=lambda item: (item[0], str(item[1].get("tl_id", ""))),
        )
        reference_neighborhoods.append(
            {
                "reference_tl_id": str(reference_record.get("tl_id", "")),
                "candidate_tl_ids": [str(record.get("tl_id", "")) for _, record in neighbors],
                "candidate_controller_count": len(neighbors),
                "reference_controlled_connection_count": int(
                    reference_record.get("controlled_connection_count", 0)
                ),
                "nearby_candidate_controlled_connection_count": sum(
                    int(record.get("controlled_connection_count", 0)) for _, record in neighbors
                ),
                "possible_candidate_split": len(neighbors) > 1,
            }
        )
    candidate_neighborhoods = []
    for candidate_index, candidate_record in enumerate(candidate_records):
        neighbors = sorted(
            [
                [distance_m, reference_records[reference_index]]
                for distance_m, reference_index, candidate_candidate_index in distances
                if candidate_candidate_index == candidate_index and distance_m <= max_distance_m
            ],
            key=lambda item: (item[0], str(item[1].get("tl_id", ""))),
        )
        candidate_neighborhoods.append(
            {
                "candidate_tl_id": str(candidate_record.get("tl_id", "")),
                "reference_tl_ids": [str(record.get("tl_id", "")) for _, record in neighbors],
                "reference_controller_count": len(neighbors),
                "candidate_controlled_connection_count": int(
                    candidate_record.get("controlled_connection_count", 0)
                ),
                "nearby_reference_controlled_connection_count": sum(
                    int(record.get("controlled_connection_count", 0)) for _, record in neighbors
                ),
                "possible_candidate_merge": len(neighbors) > 1,
            }
        )
    controller_groups = _tls_controller_proximity_groups(
        reference_records,
        candidate_records,
        distances,
        max_distance_m=controller_group_distance_m,
    )
    paired_reference: set[int] = set()
    paired_candidate: set[int] = set()
    pairs = []
    for distance_m, reference_index, candidate_index in distances:
        if distance_m > max_distance_m:
            break
        if reference_index in paired_reference or candidate_index in paired_candidate:
            continue
        paired_reference.add(reference_index)
        paired_candidate.add(candidate_index)
        reference_record = reference_records[reference_index]
        candidate_record = candidate_records[candidate_index]
        reference_count = int(reference_record.get("controlled_connection_count", 0))
        candidate_count = int(candidate_record.get("controlled_connection_count", 0))
        pair = {
                "reference_tl_id": str(reference_record.get("tl_id", "")),
                "candidate_tl_id": str(candidate_record.get("tl_id", "")),
                "distance_m": round(distance_m, 3),
                "reference_controlled_connection_count": reference_count,
                "candidate_controlled_connection_count": candidate_count,
                "controlled_connection_delta": candidate_count - reference_count,
                "reference_junction_ids": list(reference_record.get("junction_ids", [])),
                "candidate_junction_ids": list(candidate_record.get("junction_ids", [])),
            }
        pair.update(_tls_approach_alignment(reference_record, candidate_record))
        pairs.append(pair)

    unpaired_reference = [
        reference_records[index] for index in range(len(reference_records)) if index not in paired_reference
    ]
    unpaired_candidate = [
        candidate_records[index] for index in range(len(candidate_records)) if index not in paired_candidate
    ]
    paired_reference_count = sum(pair["reference_controlled_connection_count"] for pair in pairs)
    paired_candidate_count = sum(pair["candidate_controlled_connection_count"] for pair in pairs)
    high_confidence_movement_gap_queue = [
        {
            "reference_tl_id": pair["reference_tl_id"],
            "candidate_tl_id": pair["candidate_tl_id"],
            "controller_distance_m": pair["distance_m"],
            "reference_edge_id": approach["reference_edge_id"],
            "candidate_edge_id": approach["candidate_edge_id"],
            "split_root_edge_id": approach["reference_split_root_edge_id"],
            "bearing_delta_deg": approach["bearing_delta_deg"],
            "missing_direction_counts": approach["missing_direction_counts"],
            "missing_direction_instance_count": sum(approach["missing_direction_counts"].values()),
            "readiness": "needs_destination_edge_and_lane_mapping",
        }
        for pair in pairs
        if float(pair.get("distance_m", max_distance_m + 1.0)) <= controller_group_distance_m
        for approach in pair.get("approach_pairs", [])
        if bool(approach.get("split_root_match", False))
        and float(approach.get("bearing_delta_deg", 360.0)) <= 15.0
        and bool(approach.get("missing_direction_counts", {}))
        and not bool(approach.get("extra_direction_counts", {}))
    ]
    return {
        "status": "diagnostic",
        "method": "injective_nearest_controlled_junction_centroid",
        "max_distance_m": max_distance_m,
        "controller_group_distance_m": controller_group_distance_m,
        "pair_count": len(pairs),
        "pairs": sorted(pairs, key=lambda pair: (pair["distance_m"], pair["reference_tl_id"])),
        "boundary_approach_pair_count": sum(
            int(pair.get("approach_pair_count", 0)) for pair in pairs
        ),
        "split_root_approach_pair_count": sum(
            1
            for pair in pairs
            for approach in pair.get("approach_pairs", [])
            if bool(approach.get("split_root_match", False))
        ),
        "bearing_fallback_approach_pair_count": sum(
            1
            for pair in pairs
            for approach in pair.get("approach_pairs", [])
            if not bool(approach.get("split_root_match", False))
        ),
        "high_confidence_movement_gap_candidate_count": len(high_confidence_movement_gap_queue),
        "high_confidence_missing_direction_instance_count": sum(
            item["missing_direction_instance_count"] for item in high_confidence_movement_gap_queue
        ),
        "high_confidence_movement_gap_queue": sorted(
            high_confidence_movement_gap_queue,
            key=lambda item: (
                item["candidate_tl_id"],
                item["candidate_edge_id"],
                item["reference_edge_id"],
            ),
        ),
        "unpaired_reference_boundary_approach_count": sum(
            int(pair.get("unpaired_reference_approach_count", 0)) for pair in pairs
            if pair.get("approach_alignment_status") == "diagnostic"
        ),
        "unpaired_candidate_boundary_approach_count": sum(
            int(pair.get("unpaired_candidate_approach_count", 0)) for pair in pairs
            if pair.get("approach_alignment_status") == "diagnostic"
        ),
        "missing_movement_signature_instance_count": sum(
            sum(approach.get("missing_movement_signature_counts", {}).values())
            for pair in pairs
            for approach in pair.get("approach_pairs", [])
        ),
        "extra_movement_signature_instance_count": sum(
            sum(approach.get("extra_movement_signature_counts", {}).values())
            for pair in pairs
            for approach in pair.get("approach_pairs", [])
        ),
        "exact_direction_approach_pair_count": sum(
            int(pair.get("exact_direction_approach_pair_count", 0)) for pair in pairs
        ),
        "missing_direction_instance_count": sum(
            int(pair.get("missing_direction_instance_count", 0)) for pair in pairs
        ),
        "extra_direction_instance_count": sum(
            int(pair.get("extra_direction_instance_count", 0)) for pair in pairs
        ),
        "reference_controller_internal_passenger_edge_count": sum(
            len(record.get("controller_internal_passenger_from_edge_ids", []))
            for record in reference_records
        ),
        "candidate_controller_internal_passenger_edge_count": sum(
            len(record.get("controller_internal_passenger_from_edge_ids", []))
            for record in candidate_records
        ),
        "paired_reference_controlled_connection_count": paired_reference_count,
        "paired_candidate_controlled_connection_count": paired_candidate_count,
        "paired_controlled_connection_delta": paired_candidate_count - paired_reference_count,
        "unpaired_reference_tl_ids": sorted(str(record.get("tl_id", "")) for record in unpaired_reference),
        "unpaired_candidate_tl_ids": sorted(str(record.get("tl_id", "")) for record in unpaired_candidate),
        "unpaired_reference_controlled_connection_count": sum(
            int(record.get("controlled_connection_count", 0)) for record in unpaired_reference
        ),
        "unpaired_candidate_controlled_connection_count": sum(
            int(record.get("controlled_connection_count", 0)) for record in unpaired_candidate
        ),
        "aggregate_reference_controlled_connection_count": sum(
            int(record.get("controlled_connection_count", 0)) for record in reference_records
        ),
        "aggregate_candidate_controlled_connection_count": sum(
            int(record.get("controlled_connection_count", 0)) for record in candidate_records
        ),
        "reference_neighborhoods": sorted(
            reference_neighborhoods,
            key=lambda item: item["reference_tl_id"],
        ),
        "candidate_neighborhoods": sorted(
            candidate_neighborhoods,
            key=lambda item: item["candidate_tl_id"],
        ),
        "possible_candidate_split_reference_count": sum(
            1 for item in reference_neighborhoods if item["possible_candidate_split"]
        ),
        "possible_candidate_merge_controller_count": sum(
            1 for item in candidate_neighborhoods if item["possible_candidate_merge"]
        ),
        "controller_group_count": len(controller_groups),
        "controller_groups": controller_groups,
        "split_controller_group_count": sum(
            1
            for group in controller_groups
            if group["reference_controller_count"] == 1 and group["candidate_controller_count"] > 1
        ),
        "merge_controller_group_count": sum(
            1
            for group in controller_groups
            if group["reference_controller_count"] > 1 and group["candidate_controller_count"] == 1
        ),
        "many_to_many_controller_group_count": sum(
            1
            for group in controller_groups
            if group["reference_controller_count"] > 1 and group["candidate_controller_count"] > 1
        ),
        "repair_safe": False,
        "warning": "centroid pairing does not yet align approaches or controller split/merge groups",
    }


def _latlon_distance_m(lat_a: float, lon_a: float, lat_b: float, lon_b: float) -> float:
    mean_lat_radians = math.radians((lat_a + lat_b) / 2.0)
    delta_y = (lat_a - lat_b) * 111_320.0
    delta_x = (lon_a - lon_b) * 111_320.0 * math.cos(mean_lat_radians)
    return math.hypot(delta_x, delta_y)


def _tls_controller_distance_m(
    reference_record: dict[str, Any],
    candidate_record: dict[str, Any],
) -> float:
    reference_points = [
        point
        for point in reference_record.get("controlled_junction_points", [])
        if isinstance(point.get("lat"), (int, float))
        and isinstance(point.get("lon"), (int, float))
        and not str(point.get("status", "")).startswith("xy_fallback")
    ]
    candidate_points = [
        point
        for point in candidate_record.get("controlled_junction_points", [])
        if isinstance(point.get("lat"), (int, float))
        and isinstance(point.get("lon"), (int, float))
        and not str(point.get("status", "")).startswith("xy_fallback")
    ]
    if reference_points and candidate_points:
        return min(
            _latlon_distance_m(
                float(reference_point["lat"]),
                float(reference_point["lon"]),
                float(candidate_point["lat"]),
                float(candidate_point["lon"]),
            )
            for reference_point in reference_points
            for candidate_point in candidate_points
        )
    return _latlon_distance_m(
        float(reference_record["controlled_junction_centroid_lat"]),
        float(reference_record["controlled_junction_centroid_lon"]),
        float(candidate_record["controlled_junction_centroid_lat"]),
        float(candidate_record["controlled_junction_centroid_lon"]),
    )


def _tls_controller_proximity_groups(
    reference_records: list[dict[str, Any]],
    candidate_records: list[dict[str, Any]],
    distances: list[tuple[float, int, int]],
    *,
    max_distance_m: float,
) -> list[dict[str, Any]]:
    adjacency: dict[tuple[str, int], set[tuple[str, int]]] = {}
    for distance_m, reference_index, candidate_index in distances:
        if distance_m > max_distance_m:
            break
        reference_node = ("reference", reference_index)
        candidate_node = ("candidate", candidate_index)
        adjacency.setdefault(reference_node, set()).add(candidate_node)
        adjacency.setdefault(candidate_node, set()).add(reference_node)

    groups = []
    visited: set[tuple[str, int]] = set()
    for start in sorted(adjacency):
        if start in visited:
            continue
        stack = [start]
        component: set[tuple[str, int]] = set()
        while stack:
            node = stack.pop()
            if node in visited:
                continue
            visited.add(node)
            component.add(node)
            stack.extend(adjacency.get(node, set()) - visited)
        reference_indexes = sorted(index for side, index in component if side == "reference")
        candidate_indexes = sorted(index for side, index in component if side == "candidate")
        if not reference_indexes or not candidate_indexes:
            continue
        references = [reference_records[index] for index in reference_indexes]
        candidates = [candidate_records[index] for index in candidate_indexes]
        reference_count = sum(int(record.get("controlled_connection_count", 0)) for record in references)
        candidate_count = sum(int(record.get("controlled_connection_count", 0)) for record in candidates)
        group = {
            "group_id": f"tls_group_{len(groups) + 1:03d}",
            "reference_tl_ids": sorted(str(record.get("tl_id", "")) for record in references),
            "candidate_tl_ids": sorted(str(record.get("tl_id", "")) for record in candidates),
            "reference_controller_count": len(references),
            "candidate_controller_count": len(candidates),
            "reference_controlled_connection_count": reference_count,
            "candidate_controlled_connection_count": candidate_count,
            "controlled_connection_delta": candidate_count - reference_count,
        }
        group.update(
            _tls_approach_alignment(
                {
                    "passenger_approaches": [
                        approach
                        for record in references
                        for approach in record.get("passenger_approaches", [])
                    ]
                },
                {
                    "passenger_approaches": [
                        approach
                        for record in candidates
                        for approach in record.get("passenger_approaches", [])
                    ]
                },
            )
        )
        groups.append(group)
    return sorted(groups, key=lambda group: group["group_id"])


def _incoming_edge_bearing_deg(edge: ET.Element) -> float | None:
    for lane in edge.findall("lane"):
        if not lane_allows_passenger(lane):
            continue
        try:
            points = _parse_shape(lane.attrib.get("shape", ""))
        except ValueError:
            continue
        if len(points) < 2:
            continue
        start, end = points[-2], points[-1]
        delta_x = end[0] - start[0]
        delta_y = end[1] - start[1]
        if math.hypot(delta_x, delta_y) <= 1e-9:
            continue
        return round(math.degrees(math.atan2(delta_y, delta_x)) % 360.0, 3)
    return None


def _tls_approach_alignment(
    reference_record: dict[str, Any],
    candidate_record: dict[str, Any],
    *,
    max_bearing_delta_deg: float = 35.0,
) -> dict[str, Any]:
    reference_approaches = [
        approach
        for approach in reference_record.get("passenger_approaches", [])
        if isinstance(approach.get("bearing_deg"), (int, float))
    ]
    candidate_approaches = [
        approach
        for approach in candidate_record.get("passenger_approaches", [])
        if isinstance(approach.get("bearing_deg"), (int, float))
    ]
    if not reference_approaches or not candidate_approaches:
        return {
            "approach_alignment_status": "unavailable",
            "approach_pair_count": 0,
            "approach_pairs": [],
        }
    distances = sorted(
        (
            0
            if _approach_split_root(reference_approach)
            and _approach_split_root(reference_approach) == _approach_split_root(candidate_approach)
            else 1,
            min(
                abs(float(reference_approach["bearing_deg"]) - float(candidate_approach["bearing_deg"])),
                360.0
                - abs(
                    float(reference_approach["bearing_deg"])
                    - float(candidate_approach["bearing_deg"])
                ),
            ),
            reference_index,
            candidate_index,
        )
        for reference_index, reference_approach in enumerate(reference_approaches)
        for candidate_index, candidate_approach in enumerate(candidate_approaches)
    )
    used_reference: set[int] = set()
    used_candidate: set[int] = set()
    approach_pairs = []
    for root_mismatch, bearing_delta_deg, reference_index, candidate_index in distances:
        if bearing_delta_deg > max_bearing_delta_deg:
            continue
        if reference_index in used_reference or candidate_index in used_candidate:
            continue
        used_reference.add(reference_index)
        used_candidate.add(candidate_index)
        reference_approach = reference_approaches[reference_index]
        candidate_approach = candidate_approaches[candidate_index]
        reference_movements = Counter(reference_approach.get("movement_signature_counts", {}))
        candidate_movements = Counter(candidate_approach.get("movement_signature_counts", {}))
        reference_directions = _movement_direction_counts(reference_movements)
        candidate_directions = _movement_direction_counts(candidate_movements)
        approach_pairs.append(
            {
                "reference_edge_id": str(reference_approach.get("edge_id", "")),
                "candidate_edge_id": str(candidate_approach.get("edge_id", "")),
                "bearing_delta_deg": round(bearing_delta_deg, 3),
                "split_root_match": root_mismatch == 0,
                "reference_split_root_edge_id": _approach_split_root(reference_approach),
                "candidate_split_root_edge_id": _approach_split_root(candidate_approach),
                "reference_controlled_connection_count": int(
                    reference_approach.get("controlled_connection_count", 0)
                ),
                "candidate_controlled_connection_count": int(
                    candidate_approach.get("controlled_connection_count", 0)
                ),
                "missing_movement_signature_counts": dict(reference_movements - candidate_movements),
                "extra_movement_signature_counts": dict(candidate_movements - reference_movements),
                "missing_direction_counts": dict(reference_directions - candidate_directions),
                "extra_direction_counts": dict(candidate_directions - reference_directions),
            }
        )
    return {
        "approach_alignment_status": "diagnostic",
        "approach_max_bearing_delta_deg": max_bearing_delta_deg,
        "approach_pair_count": len(approach_pairs),
        "reference_approach_count": len(reference_approaches),
        "candidate_approach_count": len(candidate_approaches),
        "unpaired_reference_approach_count": len(reference_approaches) - len(used_reference),
        "unpaired_candidate_approach_count": len(candidate_approaches) - len(used_candidate),
        "approach_pairs": sorted(
            approach_pairs,
            key=lambda item: (item["bearing_delta_deg"], item["reference_edge_id"]),
        ),
        "exact_direction_approach_pair_count": sum(
            1
            for approach in approach_pairs
            if not approach["missing_direction_counts"] and not approach["extra_direction_counts"]
        ),
        "missing_direction_instance_count": sum(
            sum(approach["missing_direction_counts"].values()) for approach in approach_pairs
        ),
        "extra_direction_instance_count": sum(
            sum(approach["extra_direction_counts"].values()) for approach in approach_pairs
        ),
    }


def _movement_direction_counts(movements: Counter[str]) -> Counter[str]:
    directions: Counter[str] = Counter()
    for signature, count in movements.items():
        direction = next(
            (
                field.partition("=")[2]
                for field in str(signature).split("|")
                if field.startswith("dir=")
            ),
            "",
        )
        directions[direction] += int(count)
    return directions


def _split_root_edge_id(edge_id: str) -> str:
    return edge_id.split("#", 1)[0]


def _approach_split_root(approach: dict[str, Any]) -> str:
    return str(
        approach.get("split_root_edge_id")
        or _split_root_edge_id(str(approach.get("edge_id", "")))
    )


def _max_distribution_key(distribution: Any) -> int:
    if not isinstance(distribution, dict) or not distribution:
        return 1
    return max((int(key) for key in distribution), default=1)


def _connection_junction_id(connection: ET.Element, junction_ids: set[str]) -> str:
    via = connection.attrib.get("via", "")
    if via.startswith(":"):
        lane_id = via[1:]
        matches = [junction_id for junction_id in junction_ids if lane_id == junction_id or lane_id.startswith(f"{junction_id}_")]
        if matches:
            return max(matches, key=len)
    tl_id = connection.attrib.get("tl", "")
    return tl_id if tl_id in junction_ids else ""


def _edge_function(edge: ET.Element) -> str:
    function = edge.attrib.get("function", "")
    if function in {"crossing", "walkingarea"}:
        return function
    if edge.attrib.get("id", "").startswith(":") or function == "internal":
        return "internal"
    return "plain"


def _network_structural_delta(reference: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
    scalar_keys = [
        "crossing_edge_count",
        "walkingarea_edge_count",
        "internal_edge_count",
        "connection_count",
        "request_count",
        "tl_logic_count",
        "traffic_light_junction_count",
        "tls_controlled_connection_count",
        "multi_junction_tl_logic_count",
        "traffic_light_junction_without_tls_connection_count",
        "tls_shared_linkindex_group_count",
        "tls_sparse_linkindex_tl_logic_count",
    ]
    missing_counts = {
        key: int(reference.get(key, 0)) - int(candidate.get(key, 0))
        for key in scalar_keys
        if int(reference.get(key, 0)) > int(candidate.get(key, 0))
    }
    extra_counts = {
        key: int(candidate.get(key, 0)) - int(reference.get(key, 0))
        for key in scalar_keys
        if int(candidate.get(key, 0)) > int(reference.get(key, 0))
    }
    reference_types = reference.get("junction_type_counts", {}) if isinstance(reference.get("junction_type_counts"), dict) else {}
    candidate_types = candidate.get("junction_type_counts", {}) if isinstance(candidate.get("junction_type_counts"), dict) else {}
    junction_type_missing_counts = {
        key: int(value) - int(candidate_types.get(key, 0))
        for key, value in reference_types.items()
        if int(value) > int(candidate_types.get(key, 0))
    }
    junction_type_extra_counts = {
        key: int(value) - int(reference_types.get(key, 0))
        for key, value in candidate_types.items()
        if int(value) > int(reference_types.get(key, 0))
    }
    return {
        "status": "fail"
        if missing_counts or extra_counts or junction_type_missing_counts or junction_type_extra_counts
        else "pass",
        "missing_counts": dict(sorted(missing_counts.items())),
        "extra_counts": dict(sorted(extra_counts.items())),
        "junction_type_missing_counts": dict(sorted(junction_type_missing_counts.items())),
        "junction_type_extra_counts": dict(sorted(junction_type_extra_counts.items())),
    }


def _compare_same_id_patterns(
    reference_patterns: list[dict[str, Any]],
    candidate_patterns: list[dict[str, Any]],
    *,
    equivalent_approach_edge_map: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    candidates_by_id = {str(pattern.get("junction_id", "")): pattern for pattern in candidate_patterns}
    comparisons = []
    for reference in reference_patterns:
        junction_id = str(reference.get("junction_id", ""))
        candidate = candidates_by_id.get(junction_id)
        if not junction_id or candidate is None:
            continue
        comparison = compare_junction_pattern_records(
            reference,
            candidate,
            equivalent_approach_edge_map=equivalent_approach_edge_map,
        )
        comparisons.append(
            {
                "junction_id": junction_id,
                "status": comparison["status"],
                "mismatch_fields": comparison["mismatch_fields"],
                "teacher": comparison["teacher"],
                "candidate": comparison["candidate"],
                "approach_edge_equivalence_applied": comparison["approach_edge_equivalence_applied"],
            }
        )
    return comparisons


def _count_field(cases: list[dict[str, Any]], field: str) -> dict[str, int]:
    return dict(sorted(Counter(str(case.get(field, "")) for case in cases).items()))


def _case_cluster_distance(reference_case: dict[str, Any], cluster: dict[str, Any]) -> float:
    ref_status = str(reference_case.get("reference_coordinate_status", ""))
    cluster_status = str(cluster.get("coordinate_status", ""))
    if ref_status.startswith("xy_fallback") or cluster_status.startswith("xy_fallback"):
        return math.hypot(
            float(reference_case["reference_x"]) - float(cluster["centroid_x"]),
            float(reference_case["reference_y"]) - float(cluster["centroid_y"]),
        )
    return _haversine_m(
        float(reference_case["reference_lat"]),
        float(reference_case["reference_lon"]),
        float(cluster["centroid_lat"]),
        float(cluster["centroid_lon"]),
    )


def _haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    radius_m = 6_371_000.0
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)
    a = math.sin(delta_phi / 2.0) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2.0) ** 2
    return 2.0 * radius_m * math.atan2(math.sqrt(a), math.sqrt(1.0 - a))


def _joined_source_nodes(junction_id: str, cluster_prefix: str) -> list[str]:
    return [part for part in junction_id.removeprefix(cluster_prefix).split("_") if part]


def _lane_to_edge_id(lane_id: str) -> str:
    raw_edge, separator, lane_index = lane_id.rpartition("_")
    if separator and lane_index.isdigit():
        return raw_edge
    return lane_id


def _parse_shape(shape_text: str) -> list[tuple[float, float]]:
    points = []
    for raw_point in shape_text.split():
        parts = raw_point.split(",")
        if len(parts) >= 2:
            points.append((float(parts[0]), float(parts[1])))
    return points


def _coordinate_converter(root: ET.Element, net_file: Path) -> Callable[[float, float], tuple[float, float]] | None:
    location = root.find("location")
    if location is not None:
        try:
            offset_x, offset_y = (float(value) for value in location.attrib["netOffset"].split(",", 1))
            zone = _parse_utm_zone(location.attrib["projParameter"])
            return lambda x, y: _utm_to_latlon(x - offset_x, y - offset_y, zone=zone, northern=True)
        except (KeyError, TypeError, ValueError):
            pass
    try:
        import sumolib  # type: ignore

        net = sumolib.net.readNet(str(net_file))
        return lambda x, y: _net_xy_to_latlon(net, x, y)
    except Exception:  # noqa: BLE001 - optional sumolib projection has an explicit no-geo fallback.
        return None


def _junction_latlon(
    x: float,
    y: float,
    xy_to_latlon: Callable[[float, float], tuple[float, float]] | None,
) -> tuple[float, float, str]:
    if xy_to_latlon is None:
        return y, x, "xy_fallback_no_geo_projection"
    try:
        lat, lon = xy_to_latlon(x, y)
    except Exception:  # noqa: BLE001 - converter may be supplied by sumolib/pyproj and is best-effort.
        return y, x, "xy_fallback_geo_projection_failed"
    return lat, lon, "wgs84_from_sumo_projection"


def _google_maps_url(case: dict[str, Any]) -> str:
    if str(case.get("reference_coordinate_status", "")).startswith("xy_fallback"):
        return ""
    return f"https://www.google.com/maps/@{float(case['reference_lat']):.7f},{float(case['reference_lon']):.7f},50m"


def _write_cases_csv(path: Path, cases: list[dict[str, Any]]) -> None:
    fields = [
        "reference_id",
        "reference_type",
        "reference_joined_source_node_count",
        "reference_approach_edge_count",
        "reference_internal_lane_count",
        "reference_shape_point_count",
        "reference_lat",
        "reference_lon",
        "match_status",
        "match_distance_m",
        "matched_reference_source_node_count",
        "reference_source_node_match_ratio",
        "matched_reference_source_internal_edge_count",
        "matched_reference_source_boundary_edge_count",
        "matched_candidate_cluster_id",
        "matched_candidate_node_count",
        "matched_candidate_internal_edge_count",
        "matched_candidate_approach_count",
        "matched_candidate_traffic_light_node_count",
        "learned_rule_basis",
        "learned_rule",
        "matched_candidate_google_maps_url",
        "reference_joined_source_nodes",
        "reference_approach_edge_ids",
        "matched_reference_source_node_ids",
        "matched_reference_source_internal_edge_ids",
        "matched_reference_source_boundary_edge_ids",
        "matched_candidate_node_ids",
        "matched_candidate_internal_edge_ids",
        "matched_candidate_boundary_edge_ids",
        "matched_candidate_risk_flags",
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for case in cases:
            row = {field: case.get(field, "") for field in fields}
            for field in (
                "reference_joined_source_nodes",
                "reference_approach_edge_ids",
                "matched_reference_source_node_ids",
                "matched_reference_source_internal_edge_ids",
                "matched_reference_source_boundary_edge_ids",
                "matched_candidate_node_ids",
                "matched_candidate_internal_edge_ids",
                "matched_candidate_boundary_edge_ids",
                "matched_candidate_risk_flags",
            ):
                value = row.get(field, [])
                if isinstance(value, list):
                    row[field] = ";".join(str(item) for item in value)
            writer.writerow(row)


def _write_junction_pattern_comparisons_csv(path: Path, comparisons: list[dict[str, Any]]) -> None:
    fields = [
        "junction_id",
        "status",
        "mismatch_fields",
        "teacher_approach_edge_ids",
        "candidate_approach_edge_ids",
        "teacher_control_type",
        "candidate_control_type",
        "teacher_has_tls",
        "candidate_has_tls",
        "teacher_internal_function_counts",
        "candidate_internal_function_counts",
        "teacher_request_bit_lengths_ok",
        "candidate_request_bit_lengths_ok",
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for comparison in comparisons:
            teacher = comparison.get("teacher", {})
            candidate = comparison.get("candidate", {})
            writer.writerow(
                {
                    "junction_id": comparison.get("junction_id", ""),
                    "status": comparison.get("status", ""),
                    "mismatch_fields": ";".join(str(field) for field in comparison.get("mismatch_fields", [])),
                    "teacher_approach_edge_ids": ";".join(teacher.get("approach_edge_ids", []) or []),
                    "candidate_approach_edge_ids": ";".join(candidate.get("approach_edge_ids", []) or []),
                    "teacher_control_type": teacher.get("control_type", ""),
                    "candidate_control_type": candidate.get("control_type", ""),
                    "teacher_has_tls": teacher.get("has_tls", ""),
                    "candidate_has_tls": candidate.get("has_tls", ""),
                    "teacher_internal_function_counts": json.dumps(
                        teacher.get("internal_function_counts", {}), ensure_ascii=False, sort_keys=True
                    ),
                    "candidate_internal_function_counts": json.dumps(
                        candidate.get("internal_function_counts", {}), ensure_ascii=False, sort_keys=True
                    ),
                    "teacher_request_bit_lengths_ok": teacher.get("request_bit_lengths_ok", ""),
                    "candidate_request_bit_lengths_ok": candidate.get("request_bit_lengths_ok", ""),
                }
            )


def _warnings(reference_cases: list[dict[str, Any]], matched_cases: list[dict[str, Any]]) -> list[str]:
    warnings = []
    if not reference_cases:
        warnings.append("no reference joined junction cases found")
    unmatched = sum(1 for case in matched_cases if case["match_status"] != "matched")
    if unmatched:
        warnings.append(f"{unmatched} reference joined junction case(s) did not match a nearby candidate cluster")
    warnings.append("reference-derived join candidates remain diagnostic until map review and routeability gates pass")
    return warnings
