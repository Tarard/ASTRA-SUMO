from __future__ import annotations

import json
import gzip
import math
import xml.etree.ElementTree as ET
from html import escape
from pathlib import Path
from typing import Any, Mapping

from .artifact_io import write_json_atomic, write_text_atomic
from .candidate_contracts import file_sha256
from .corridor_edit_ledger import _write_review_overlay
from .junction_teacher_model import (
    compare_junction_pattern_records,
    extract_junction_pattern_index,
)
from .map_review import build_map_review_evidence, validate_map_review_evidence


TEACHER_CORRIDOR_SCHEMA = "torii.teacher_corridor_comparison.v1"
TEACHER_CORRIDOR_DECISION_SCHEMA = "torii.teacher_corridor_review.v1"


def build_teacher_corridor_comparison(
    *,
    teacher_net_file: Path,
    candidate_net_file: Path,
    junction_id: str,
    output_dir: Path,
    prefix: str = "teacher_corridor",
    map_temporal_scope: str = "current",
    map_target_date: str | None = None,
    osm_file: Path | None = None,
    evidence_radius_m: float = 35.0,
) -> dict[str, Any]:
    """Compare one candidate junction with a manually cleaned teacher junction.

    This is a diagnostic transfer package, not an automatic teacher replay. It
    narrows the human-modeling problem to one physical corridor cell and binds
    every review artifact to both network hashes.
    """
    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    report_file = output_dir / f"{prefix}.json"
    manifest_file = output_dir / f"{prefix}.manifest.json"
    teacher_net_file = teacher_net_file.resolve()
    candidate_net_file = candidate_net_file.resolve()
    if not teacher_net_file.is_file():
        return _persist_blocked(
            error=f"teacher net file does not exist: {teacher_net_file}",
            report_file=report_file,
            manifest_file=manifest_file,
            teacher_net_file=teacher_net_file,
            candidate_net_file=candidate_net_file,
            junction_id=junction_id,
        )
    if not candidate_net_file.is_file():
        return _persist_blocked(
            error=f"candidate net file does not exist: {candidate_net_file}",
            report_file=report_file,
            manifest_file=manifest_file,
            teacher_net_file=teacher_net_file,
            candidate_net_file=candidate_net_file,
            junction_id=junction_id,
        )
    try:
        teacher_root = ET.parse(teacher_net_file).getroot()
        candidate_root = ET.parse(candidate_net_file).getroot()
        teacher_records = extract_junction_pattern_index(
            teacher_net_file,
            junction_ids=[junction_id],
        )
        candidate_records = extract_junction_pattern_index(
            candidate_net_file,
            junction_ids=[junction_id],
        )
    except (OSError, ET.ParseError, KeyError, TypeError, ValueError) as exc:
        return _persist_blocked(
            error=f"{type(exc).__name__}: {exc}",
            report_file=report_file,
            manifest_file=manifest_file,
            teacher_net_file=teacher_net_file,
            candidate_net_file=candidate_net_file,
            junction_id=junction_id,
        )
    if not teacher_records or not candidate_records:
        missing = []
        if not teacher_records:
            missing.append("teacher")
        if not candidate_records:
            missing.append("candidate")
        return _persist_blocked(
            error=f"junction {junction_id} has no comparable 3- or 4-arm record in: {', '.join(missing)}",
            report_file=report_file,
            manifest_file=manifest_file,
            teacher_net_file=teacher_net_file,
            candidate_net_file=candidate_net_file,
            junction_id=junction_id,
        )

    teacher = teacher_records[0]
    candidate = candidate_records[0]
    approach_equivalence = _approach_edge_equivalence(
        teacher.get("approach_edge_ids", []),
        candidate.get("approach_edge_ids", []),
    )
    comparison = compare_junction_pattern_records(
        teacher,
        candidate,
        equivalent_approach_edge_map=approach_equivalence,
    )
    mismatches = list(comparison.get("mismatch_fields", []))
    review_required = bool(mismatches)
    teacher_sha256 = file_sha256(teacher_net_file)
    candidate_sha256 = file_sha256(candidate_net_file)
    candidate_junction = _junction(candidate_root, junction_id)
    teacher_junction = _junction(teacher_root, junction_id)
    if candidate_junction is None:
        return _persist_blocked(
            error=f"candidate junction element is missing: {junction_id}",
            report_file=report_file,
            manifest_file=manifest_file,
            teacher_net_file=teacher_net_file,
            candidate_net_file=candidate_net_file,
            junction_id=junction_id,
        )
    osm_evidence = _extract_osm_corridor_evidence(
        osm_file,
        junction_id=junction_id,
        radius_m=evidence_radius_m,
    )
    teacher_cell = _physical_junction_cell(
        teacher_root,
        junction_id=junction_id,
        radius_m=evidence_radius_m,
    )
    candidate_cell = _physical_junction_cell(
        candidate_root,
        junction_id=junction_id,
        radius_m=evidence_radius_m,
    )

    map_evidence_file = output_dir / f"{prefix}.map-review.json"
    overlay_file = output_dir / f"{prefix}.review.add.xml"
    review_file = output_dir / f"{prefix}.review.json"
    review_html_file = output_dir / f"{prefix}.review.html"
    operation = {
        "id": f"teacher-corridor-{junction_id}",
        "operation": "review_marker",
        "status": "candidate" if review_required else "matched",
        "target_ids": list(candidate.get("approach_edge_ids", [])),
        "rationale": "Compare current OSM-derived corridor semantics with the manually cleaned teacher cell.",
        "evidence": [{"kind": "teacher_junction_pattern", "junction_id": junction_id}],
        "rollback": {"action": "discard_future_candidate_and_keep_current_network"},
        "constraints": {},
        "review_requirements": {
            "map_review_required": review_required,
            "review_question": (
                "Which teacher differences still match the current real junction and should be transferred?"
            ),
        },
        "location": _junction_location(candidate_junction),
        "params": {"node_id": junction_id},
    }
    map_evidence = build_map_review_evidence(
        source_net_file=candidate_net_file,
        candidate_net_file=candidate_net_file,
        candidate_sha256=candidate_sha256,
        locations=[
            {
                "location_id": f"teacher_corridor:{junction_id}",
                "proposal_id": operation["id"],
                "operation": "review_marker",
                "location": operation["location"],
                "map_review_required": review_required,
                "review_question": operation["review_requirements"]["review_question"],
                "geometry_source": "candidate_net",
            }
        ],
        temporal_scope=map_temporal_scope,
        target_date=map_target_date,
    )
    write_json_atomic(map_evidence_file, map_evidence, sort_keys=True)
    map_evidence_sha256 = file_sha256(map_evidence_file)
    map_validation = validate_map_review_evidence(
        map_evidence,
        source_net_file=candidate_net_file,
        candidate_net_file=candidate_net_file,
        evidence_file=map_evidence_file,
        evidence_sha256=map_evidence_sha256,
    )
    overlay = _write_review_overlay(
        overlay_file,
        [operation],
        candidate_root,
        candidate_root=candidate_root,
        map_review_evidence=map_evidence,
        candidate_sha256=candidate_sha256,
        map_review_evidence_file=map_evidence_file,
        map_review_evidence_sha256=map_evidence_sha256,
    )
    recommendations = _recommendations(
        mismatches,
        junction_id=junction_id,
        teacher=teacher,
        candidate=candidate,
        osm_evidence=osm_evidence,
    )
    review_template = {
        "schema": TEACHER_CORRIDOR_DECISION_SCHEMA,
        "status": "pending" if review_required else "not_required",
        "junction_id": junction_id,
        "teacher_net_file": str(teacher_net_file),
        "candidate_net_file": str(candidate_net_file),
        "teacher_sha256": teacher_sha256,
        "candidate_sha256": candidate_sha256,
        "map_review_evidence_file": str(map_evidence_file),
        "map_review_evidence_sha256": map_evidence_sha256,
        "mismatch_fields": mismatches,
        "accepted_teacher_fields": [],
        "rejected_teacher_fields": [],
        "observed_facts": {
            "feature_presence": "",
            "geometry_connectivity": "",
            "access_modes": "",
            "source_limitations": "",
        },
        "reviewer": "",
        "reviewed_at": "",
        "rationale": "",
        "rollback": {"action": "discard_future_candidate_and_keep_current_network"},
        "instructions": (
            "Approve only teacher fields confirmed against current or time-aligned map evidence; "
            "a teacher difference is not truth by itself."
        ),
    }
    write_json_atomic(review_file, review_template, sort_keys=True)
    _write_comparison_html(
        review_html_file,
        junction_id=junction_id,
        teacher_sha256=teacher_sha256,
        candidate_sha256=candidate_sha256,
        comparison=comparison,
        map_evidence=map_evidence,
        map_evidence_sha256=map_evidence_sha256,
        recommendations=recommendations,
        osm_evidence=osm_evidence,
        teacher_cell=teacher_cell,
        candidate_cell=candidate_cell,
        review_file=review_file,
        overlay_file=overlay_file,
    )

    package_status = (
        "pass"
        if map_validation.get("status") == "pass" and overlay.get("status") == "pass"
        else "blocked"
    )
    report = {
        "schema": TEACHER_CORRIDOR_SCHEMA,
        "status": package_status,
        "claim_status": "diagnostic-demo" if package_status == "pass" else "construction-invalid",
        "teacher_transfer_status": "review_required" if review_required else "matched",
        "promotion_decision": "blocked_review_required" if review_required else "no_transfer_required",
        "junction_id": junction_id,
        "teacher_net_file": str(teacher_net_file),
        "candidate_net_file": str(candidate_net_file),
        "teacher_sha256": teacher_sha256,
        "candidate_sha256": candidate_sha256,
        "teacher_junction": dict(teacher_junction.attrib) if teacher_junction is not None else {},
        "candidate_junction": dict(candidate_junction.attrib),
        "approach_edge_equivalence": approach_equivalence,
        "teacher_pattern": teacher,
        "candidate_pattern": candidate,
        "comparison": comparison,
        "osm_file": str(osm_file.resolve()) if osm_file is not None and osm_file.is_file() else "",
        "osm_corridor_evidence": osm_evidence,
        "teacher_physical_cell": teacher_cell,
        "candidate_physical_cell": candidate_cell,
        "recommendations": recommendations,
        "map_review_evidence_file": str(map_evidence_file),
        "map_review_evidence_sha256": map_evidence_sha256,
        "map_review_readiness_status": map_validation.get("review_readiness_status", "blocked"),
        "review_overlay_file": str(overlay_file),
        "review_overlay_status": overlay.get("status", "blocked"),
        "review_decision_template_file": str(review_file),
        "review_html_file": str(review_html_file),
        "report_file": str(report_file),
        "manifest_file": str(manifest_file),
        "warnings": [
            "the teacher is a modeling example, not automatic current-map truth",
            "no candidate network mutation was performed",
        ],
    }
    write_json_atomic(report_file, report, sort_keys=True)
    _write_manifest(
        manifest_file,
        status=package_status,
        teacher_net_file=teacher_net_file,
        candidate_net_file=candidate_net_file,
        artifacts=[
            *([osm_file.resolve()] if osm_file is not None and osm_file.is_file() else []),
            map_evidence_file,
            overlay_file,
            review_file,
            review_html_file,
            report_file,
        ],
    )
    return report


def _approach_edge_equivalence(
    teacher_edges: object,
    candidate_edges: object,
) -> dict[str, str]:
    candidate_by_key: dict[tuple[str, str], list[str]] = {}
    for value in candidate_edges if isinstance(candidate_edges, list) else []:
        edge_id = str(value)
        candidate_by_key.setdefault(_osm_edge_key(edge_id), []).append(edge_id)
    result: dict[str, str] = {}
    for value in teacher_edges if isinstance(teacher_edges, list) else []:
        edge_id = str(value)
        matches = candidate_by_key.get(_osm_edge_key(edge_id), [])
        if len(matches) == 1:
            result[edge_id] = matches[0]
    return result


def _osm_edge_key(edge_id: str) -> tuple[str, str]:
    direction = "-" if edge_id.startswith("-") else "+"
    unsigned = edge_id[1:] if direction == "-" else edge_id
    return direction, unsigned.split("#", 1)[0]


def _recommendations(
    mismatch_fields: list[str],
    *,
    junction_id: str,
    teacher: Mapping[str, Any],
    candidate: Mapping[str, Any],
    osm_evidence: Mapping[str, Any],
) -> list[dict[str, Any]]:
    recommendations: list[dict[str, Any]] = []
    if "approach_edge_ids" in mismatch_fields:
        recommendations.append(
            {
                "action": "review_corridor_topology",
                "implementation_status": "review_required",
                "map_review_required": True,
                "reason": "physical approach arms do not map one-to-one by OSM way identity",
            }
        )
    if {"control_type", "has_tls"} & set(mismatch_fields):
        recommendations.append(
            {
                "action": "review_tls_control",
                "implementation_status": "blocked_richer_tls_semantics",
                "map_review_required": True,
                "reason": "confirm that the teacher signal control still exists before any TLS replay",
            }
        )
    if "internal_function_counts" in mismatch_fields:
        explicit_crossing_count = int(osm_evidence.get("explicit_crossing_node_count", 0) or 0)
        recommendations.append(
            {
                "action": "plan_pedestrian_crossings",
                "candidate_operation_family": "add_crossing",
                "implementation_status": "requires_crossed_edge_mapping",
                "map_review_required": True,
                "junction_id": junction_id,
                "reason": "teacher and candidate crossing/walkingarea layers differ",
                "current_osm_explicit_crossing_node_count": explicit_crossing_count,
                "evidence_status": (
                    "supported_by_current_osm"
                    if explicit_crossing_count > 0
                    else "requires_external_map_review"
                ),
            }
        )
    if "movement_signature_counts" in mismatch_fields:
        recommendations.append(
            {
                "action": "map_lane_movements_and_tls_links",
                "implementation_status": "blocked_richer_tls_semantics",
                "map_review_required": True,
                "reason": "lane movements must be mapped before replaying connection or phase semantics",
            }
        )
        teacher_turnarounds = int(dict(teacher.get("dir_counts", {})).get("t", 0) or 0)
        candidate_turnarounds = int(dict(candidate.get("dir_counts", {})).get("t", 0) or 0)
        if candidate_turnarounds > teacher_turnarounds:
            recommendations.append(
                {
                    "action": "review_turnaround_connections",
                    "implementation_status": "blocked_until_connection_edit_contract_exists",
                    "map_review_required": True,
                    "candidate_turnaround_count": candidate_turnarounds,
                    "teacher_turnaround_count": teacher_turnarounds,
                    "reason": "teacher removed turnaround movements that raw netconvert retained",
                }
            )
    if "request_bit_lengths_ok" in mismatch_fields:
        recommendations.append(
            {
                "action": "rebuild_request_matrix",
                "implementation_status": "blocked_until_connection_mapping_passes",
                "map_review_required": False,
                "reason": "request/foe matrices are derived only after exact movement mapping",
            }
        )
    return recommendations


def _write_comparison_html(
    path: Path,
    *,
    junction_id: str,
    teacher_sha256: str,
    candidate_sha256: str,
    comparison: Mapping[str, Any],
    map_evidence: Mapping[str, Any],
    map_evidence_sha256: str,
    recommendations: list[dict[str, Any]],
    osm_evidence: Mapping[str, Any],
    teacher_cell: Mapping[str, Any],
    candidate_cell: Mapping[str, Any],
    review_file: Path,
    overlay_file: Path,
) -> None:
    location_value = next(iter(map_evidence.get("locations", []) or []), {})
    location = location_value if isinstance(location_value, Mapping) else {}
    links = " · ".join(
        _html_link(label, str(location.get(field, "")))
        for label, field in (
            ("regional map", "regional_map_url"),
            ("satellite", "google_maps_satellite_url"),
            ("Mapillary", "mapillary_url"),
            ("KartaView", "kartaview_url"),
        )
        if str(location.get(field, "")).strip()
    ) or "No geographic map link is available."
    rows = []
    teacher_values = comparison.get("teacher", {})
    candidate_values = comparison.get("candidate", {})
    for field in comparison.get("mismatch_fields", []) or []:
        rows.append(
            "<tr>"
            f"<td><code>{escape(str(field))}</code></td>"
            f"<td><pre>{escape(json.dumps(teacher_values.get(field), indent=2, ensure_ascii=False))}</pre></td>"
            f"<td><pre>{escape(json.dumps(candidate_values.get(field), indent=2, ensure_ascii=False))}</pre></td>"
            "</tr>"
        )
    recommendation_items = "".join(
        f"<li><code>{escape(str(item.get('action', '')))}</code>: "
        f"{escape(str(item.get('reason', '')))} "
        f"(<strong>{escape(str(item.get('implementation_status', '')))}</strong>)</li>"
        for item in recommendations
    )
    document = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Torii teacher corridor {escape(junction_id)}</title>
<style>body{{font-family:system-ui,sans-serif;max-width:1400px;margin:2rem auto;padding:0 1rem;line-height:1.45}}table{{border-collapse:collapse;width:100%}}th,td{{border:1px solid #888;padding:.5rem;vertical-align:top;text-align:left}}pre,code{{white-space:pre-wrap;overflow-wrap:anywhere}}.hash{{font-size:.85rem}}</style></head>
<body><h1>Teacher corridor comparison: {escape(junction_id)}</h1>
<p>This teacher is evidence about human modeling choices, not automatic current-map truth. Confirm each transfer against the map sources below.</p>
<p>{links}</p>
<p class="hash"><strong>Teacher SHA-256:</strong> <code>{escape(teacher_sha256)}</code><br>
<strong>Candidate SHA-256:</strong> <code>{escape(candidate_sha256)}</code><br>
<strong>Map evidence SHA-256:</strong> <code>{escape(map_evidence_sha256)}</code></p>
<p><strong>Review decision template:</strong> <code>{escape(str(review_file.resolve()))}</code><br>
<strong>SUMO overlay:</strong> <code>{escape(str(overlay_file.resolve()))}</code></p>
<h2>Current OSM evidence</h2>
<pre>{escape(json.dumps(osm_evidence, indent=2, ensure_ascii=False))}</pre>
<h2>Physical-cell context</h2>
<table><thead><tr><th>Teacher cell</th><th>Candidate cell</th></tr></thead><tbody><tr>
<td><pre>{escape(json.dumps(teacher_cell, indent=2, ensure_ascii=False))}</pre></td>
<td><pre>{escape(json.dumps(candidate_cell, indent=2, ensure_ascii=False))}</pre></td>
</tr></tbody></table>
<h2>Semantic differences</h2><table><thead><tr><th>Field</th><th>Teacher</th><th>Candidate</th></tr></thead><tbody>{''.join(rows)}</tbody></table>
<h2>Next bounded actions</h2><ul>{recommendation_items}</ul>
</body></html>
"""
    write_text_atomic(path, document)


def _html_link(label: str, url: str) -> str:
    if not url.startswith(("https://", "http://")):
        return ""
    return f'<a href="{escape(url, quote=True)}" target="_blank" rel="noopener noreferrer">{escape(label)}</a>'


def _junction(root: ET.Element, junction_id: str) -> ET.Element | None:
    return next(
        (junction for junction in root.findall("junction") if junction.attrib.get("id") == junction_id),
        None,
    )


def _junction_location(junction: ET.Element) -> dict[str, float]:
    return {"x": float(junction.attrib["x"]), "y": float(junction.attrib["y"])}


def _extract_osm_corridor_evidence(
    osm_file: Path | None,
    *,
    junction_id: str,
    radius_m: float,
) -> dict[str, Any]:
    if osm_file is None:
        return {
            "status": "not_supplied",
            "junction_id": junction_id,
            "explicit_crossing_node_count": 0,
            "traffic_signal_node_count": 0,
            "warnings": ["no source OSM file was supplied for current-tag evidence"],
        }
    osm_file = osm_file.resolve()
    if not osm_file.is_file():
        return {
            "status": "blocked",
            "junction_id": junction_id,
            "osm_file": str(osm_file),
            "explicit_crossing_node_count": 0,
            "traffic_signal_node_count": 0,
            "warnings": ["source OSM file does not exist"],
        }
    try:
        if osm_file.suffix.lower() == ".gz":
            with gzip.open(osm_file, "rb") as handle:
                root = ET.parse(handle).getroot()
        else:
            root = ET.parse(osm_file).getroot()
    except (OSError, ET.ParseError) as exc:
        return {
            "status": "blocked",
            "junction_id": junction_id,
            "osm_file": str(osm_file),
            "explicit_crossing_node_count": 0,
            "traffic_signal_node_count": 0,
            "warnings": [f"{type(exc).__name__}: {exc}"],
        }
    target = next((node for node in root.findall("node") if node.attrib.get("id") == junction_id), None)
    if target is None:
        return {
            "status": "blocked",
            "junction_id": junction_id,
            "osm_file": str(osm_file),
            "explicit_crossing_node_count": 0,
            "traffic_signal_node_count": 0,
            "warnings": ["junction node is absent from the supplied OSM snapshot"],
        }
    target_lat = float(target.attrib["lat"])
    target_lon = float(target.attrib["lon"])
    target_ways: list[dict[str, Any]] = []
    for way in root.findall("way"):
        refs = [str(node.attrib.get("ref", "")) for node in way.findall("nd")]
        if junction_id not in refs:
            continue
        target_ways.append(
            {
                "id": str(way.attrib.get("id", "")),
                "tags": _osm_tags(way),
            }
        )
    nearby_tagged_nodes: list[dict[str, Any]] = []
    crossing_count = 0
    signal_count = 0
    for node in root.findall("node"):
        tags = _osm_tags(node)
        if not tags:
            continue
        distance = _haversine_m(
            target_lat,
            target_lon,
            float(node.attrib["lat"]),
            float(node.attrib["lon"]),
        )
        if distance > radius_m:
            continue
        is_crossing = tags.get("highway") == "crossing" or bool(tags.get("crossing"))
        is_signal = tags.get("highway") == "traffic_signals" or bool(tags.get("traffic_signals"))
        if not is_crossing and not is_signal and not tags.get("kerb"):
            continue
        crossing_count += int(is_crossing)
        signal_count += int(is_signal)
        nearby_tagged_nodes.append(
            {
                "id": str(node.attrib.get("id", "")),
                "lat": float(node.attrib["lat"]),
                "lon": float(node.attrib["lon"]),
                "distance_m": round(distance, 2),
                "tags": tags,
            }
        )
    return {
        "status": "pass",
        "osm_file": str(osm_file),
        "osm_sha256": file_sha256(osm_file),
        "junction_id": junction_id,
        "radius_m": radius_m,
        "target_coordinate": {"lat": target_lat, "lon": target_lon},
        "target_node_tags": _osm_tags(target),
        "target_approach_ways": sorted(target_ways, key=lambda item: item["id"]),
        "explicit_crossing_node_count": crossing_count,
        "traffic_signal_node_count": signal_count,
        "nearby_tagged_nodes": sorted(nearby_tagged_nodes, key=lambda item: item["id"]),
        "warnings": [],
    }


def _physical_junction_cell(
    root: ET.Element,
    *,
    junction_id: str,
    radius_m: float,
) -> dict[str, Any]:
    target = _junction(root, junction_id)
    if target is None:
        return {"status": "blocked", "junction_id": junction_id, "reason": "junction_missing"}
    target_x = float(target.attrib["x"])
    target_y = float(target.attrib["y"])
    junctions: list[dict[str, Any]] = []
    nearby_ids: set[str] = set()
    for junction in root.findall("junction"):
        if junction.attrib.get("type") == "internal" or not junction.attrib.get("id"):
            continue
        try:
            distance = math.hypot(
                float(junction.attrib["x"]) - target_x,
                float(junction.attrib["y"]) - target_y,
            )
        except (KeyError, ValueError):
            continue
        if distance > radius_m:
            continue
        nearby_ids.add(str(junction.attrib["id"]))
        junctions.append(
            {
                "id": str(junction.attrib["id"]),
                "type": str(junction.attrib.get("type", "")),
                "distance_m": round(distance, 2),
            }
        )
    function_edge_ids: dict[str, list[str]] = {"crossing": [], "walkingarea": [], "internal": []}
    incident_edges: set[str] = set()
    for edge in root.findall("edge"):
        edge_id = str(edge.attrib.get("id", ""))
        function = str(edge.attrib.get("function", ""))
        if function in function_edge_ids:
            centroid = _edge_centroid(edge)
            if centroid is not None and math.hypot(centroid[0] - target_x, centroid[1] - target_y) <= radius_m:
                function_edge_ids[function].append(edge_id)
        elif edge.attrib.get("from") in nearby_ids or edge.attrib.get("to") in nearby_ids:
            incident_edges.add(edge_id)
    tls_logic_ids = sorted(
        {
            str(connection.attrib.get("tl", ""))
            for connection in root.findall("connection")
            if connection.attrib.get("tl")
            and (
                connection.attrib.get("from") in incident_edges
                or connection.attrib.get("to") in incident_edges
            )
        }
    )
    return {
        "status": "pass",
        "junction_id": junction_id,
        "radius_m": radius_m,
        "junction_count": len(junctions),
        "junctions": sorted(junctions, key=lambda item: item["id"]),
        "incident_edge_count": len(incident_edges),
        "function_edge_counts": {
            key: len(values) for key, values in sorted(function_edge_ids.items())
        },
        "function_edge_ids": {
            key: sorted(values) for key, values in sorted(function_edge_ids.items())
        },
        "tls_logic_ids": tls_logic_ids,
    }


def _edge_centroid(edge: ET.Element) -> tuple[float, float] | None:
    for lane in edge.findall("lane"):
        points: list[tuple[float, float]] = []
        for token in str(lane.attrib.get("shape", "")).split():
            try:
                x_text, y_text = token.split(",", 1)
                points.append((float(x_text), float(y_text)))
            except (TypeError, ValueError):
                continue
        if points:
            return (
                sum(point[0] for point in points) / len(points),
                sum(point[1] for point in points) / len(points),
            )
    return None


def _osm_tags(element: ET.Element) -> dict[str, str]:
    return {
        str(tag.attrib.get("k", "")): str(tag.attrib.get("v", ""))
        for tag in element.findall("tag")
        if tag.attrib.get("k")
    }


def _haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    radius = 6_371_000.0
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)
    value = (
        math.sin(delta_phi / 2.0) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2.0) ** 2
    )
    return radius * 2.0 * math.atan2(math.sqrt(value), math.sqrt(1.0 - value))


def _persist_blocked(
    *,
    error: str,
    report_file: Path,
    manifest_file: Path,
    teacher_net_file: Path,
    candidate_net_file: Path,
    junction_id: str,
) -> dict[str, Any]:
    report = {
        "schema": TEACHER_CORRIDOR_SCHEMA,
        "status": "blocked",
        "claim_status": "construction-invalid",
        "teacher_transfer_status": "blocked",
        "junction_id": junction_id,
        "teacher_net_file": str(teacher_net_file),
        "candidate_net_file": str(candidate_net_file),
        "error": error,
        "report_file": str(report_file),
        "manifest_file": str(manifest_file),
        "warnings": ["no candidate network mutation was performed"],
    }
    write_json_atomic(report_file, report, sort_keys=True)
    artifacts = [report_file]
    if teacher_net_file.is_file():
        artifacts.insert(0, teacher_net_file)
    if candidate_net_file.is_file():
        artifacts.insert(1, candidate_net_file)
    _write_manifest(
        manifest_file,
        status="blocked",
        teacher_net_file=teacher_net_file,
        candidate_net_file=candidate_net_file,
        artifacts=artifacts,
    )
    return report


def _write_manifest(
    path: Path,
    *,
    status: str,
    teacher_net_file: Path,
    candidate_net_file: Path,
    artifacts: list[Path],
) -> None:
    unique: list[Path] = []
    seen: set[str] = set()
    for artifact in [teacher_net_file, candidate_net_file, *artifacts]:
        if not artifact.is_file():
            continue
        resolved = str(artifact.resolve())
        if resolved in seen:
            continue
        seen.add(resolved)
        unique.append(artifact.resolve())
    manifest = {
        "schema": "torii.teacher_corridor_manifest.v1",
        "status": status,
        "claim_status": "diagnostic-demo" if status == "pass" else "construction-invalid",
        "teacher_net_file": str(teacher_net_file),
        "candidate_net_file": str(candidate_net_file),
        "source_overwrite_forbidden": True,
        "artifacts": [
            {
                "path": str(artifact),
                "size_bytes": artifact.stat().st_size,
                "sha256": file_sha256(artifact),
            }
            for artifact in unique
        ],
    }
    write_json_atomic(path, manifest, sort_keys=True)
