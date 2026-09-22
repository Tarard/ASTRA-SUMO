from __future__ import annotations

import html
import json
from pathlib import Path
import shutil
from typing import Any

from torii_sumo.intersection.autodiscovery import (
    discover_teacher_free_intersections,
)

from .artifact_io import write_json_atomic, write_text_atomic
from .candidate_contracts import file_sha256


_OWNER_SCHEMA = "torii.teacher-free-discovery-owner/v2"


def run_teacher_free_discovery_workflow(
    *,
    osm_file: Path,
    output_dir: Path,
    traffic_side: str,
) -> dict[str, Any]:
    """Write one immutable-input, hash-bound bbox discovery bundle."""

    source = osm_file.resolve(strict=True)
    destination = output_dir.resolve()
    if destination == source.parent or destination in source.parents:
        raise ValueError("The frozen source OSM must not be stored inside the generated output directory.")
    source_sha256 = file_sha256(source)
    _reset_owned_directory(destination)
    discovery = discover_teacher_free_intersections(
        source,
        traffic_side=traffic_side,
    )
    report_file = destination / "teacher-free-discovery.json"
    geojson_file = destination / "teacher-free-discovery.geojson"
    html_file = destination / "teacher-free-discovery.html"
    manifest_file = destination / "manifest.json"
    write_json_atomic(report_file, discovery, sort_keys=True)
    write_json_atomic(
        geojson_file,
        _review_geojson(discovery),
        sort_keys=True,
    )
    write_text_atomic(html_file, _review_html(discovery))
    if file_sha256(source) != source_sha256:
        raise RuntimeError("The frozen source OSM changed while discovery artifacts were generated.")
    status = (
        "review_ready"
        if discovery["generation_status"] == "pass" and discovery["candidate_count"] > 0
        else "no_candidates"
    )
    manifest = {
        "schema": "torii.teacher-free-discovery-manifest/v1",
        "status": status,
        "automatic_promotion_gate": "blocked",
        "source_mutation": False,
        "discovery_id": discovery["discovery_id"],
        "inputs": [
            {
                "role": "frozen_osm_bbox",
                "path": str(source),
                "sha256": source_sha256,
            }
        ],
        "artifacts": [
            _artifact(path) for path in sorted(destination.iterdir()) if path.is_file() and path != manifest_file
        ],
    }
    write_json_atomic(manifest_file, manifest, sort_keys=True)
    return {
        "schema": "torii.teacher-free-discovery-workflow/v1",
        "status": status,
        "automatic_promotion_gate": "blocked",
        "discovery_id": discovery["discovery_id"],
        "signal_anchor_count": discovery["signal_anchor_count"],
        "candidate_count": discovery["candidate_count"],
        "vehicle_intersection_candidate_count": discovery["vehicle_intersection_candidate_count"],
        "review_ready_vehicle_candidate_count": discovery["review_ready_vehicle_candidate_count"],
        "pedestrian_facility_candidate_count": discovery["pedestrian_facility_candidate_count"],
        "pedestrian_facility_audit_count": discovery["pedestrian_facility_audit_count"],
        "pedestrian_facility_audit_ready_count": discovery["pedestrian_facility_audit_ready_count"],
        "pedestrian_facility_audit_blocked_count": discovery["pedestrian_facility_audit_blocked_count"],
        "report_file": str(report_file),
        "geojson_file": str(geojson_file),
        "review_html_file": str(html_file),
        "manifest_file": str(manifest_file),
    }


def _review_geojson(discovery: dict[str, Any]) -> dict[str, Any]:
    features = []
    for candidate in discovery["candidates"]:
        location = candidate["canonical_location"]
        features.append(
            {
                "type": "Feature",
                "id": candidate["candidate_id"],
                "geometry": {
                    "type": "Point",
                    "coordinates": [location["lon"], location["lat"]],
                },
                "properties": {
                    "candidate_id": candidate["candidate_id"],
                    "group_id": candidate["group_id"],
                    "classification": candidate["classification"]["kind"],
                    "physical_approach_count": candidate["classification"]["physical_approach_count"],
                    "disposition": candidate["disposition"],
                    "canonical_seed_node_id": location["node_id"],
                    "anchor_count": len(candidate["anchor_node_ids"]),
                    "pedestrian_facility_audit_status": candidate["pedestrian_facility_audit_status"],
                    "pedestrian_facility_audit_count": len(candidate["pedestrian_facility_audits"]),
                    "discovery_blockers": candidate["discovery_blockers"],
                    "automatic_promotion_gate": "blocked",
                },
            }
        )
    return {
        "type": "FeatureCollection",
        "name": "Torii teacher-free OSM signal-cell review",
        "features": features,
    }


def _review_html(discovery: dict[str, Any]) -> str:
    rows = []
    for candidate in discovery["candidates"]:
        location = candidate["canonical_location"]
        lat = float(location["lat"])
        lon = float(location["lon"])
        osm_link = f"https://www.openstreetmap.org/?mlat={lat:.7f}&mlon={lon:.7f}#map=19/{lat:.7f}/{lon:.7f}"
        blockers = ", ".join(candidate["discovery_blockers"]) or "none"
        pedestrian_audit = candidate["pedestrian_facility_audit_status"]
        rows.append(
            "<tr>"
            f"<td><code>{html.escape(candidate['candidate_id'])}</code></td>"
            f"<td>{html.escape(candidate['classification']['kind'])}</td>"
            f"<td>{candidate['classification']['physical_approach_count']}</td>"
            f"<td>{html.escape(candidate['disposition'])}</td>"
            f"<td>{html.escape(pedestrian_audit)}</td>"
            f"<td>{html.escape(blockers)}</td>"
            f'<td><a href="{html.escape(osm_link)}">OSM</a></td>'
            "</tr>"
        )
    table_rows = "\n".join(rows)
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<title>Torii teacher-free discovery</title>
<style>body{{font-family:system-ui,sans-serif;margin:2rem}}table{{border-collapse:collapse;width:100%}}th,td{{border:1px solid #ccc;padding:.45rem;text-align:left}}code{{font-size:.85em}}</style>
</head><body>
<h1>Teacher-free OSM signal-cell discovery</h1>
<p>Discovery <code>{html.escape(discovery["discovery_id"])}</code> found {discovery["candidate_count"]} candidate cells from {discovery["signal_anchor_count"]} signal anchors.</p>
<p>No teacher, reviewed scope, expected topology/count, or materialized SUMO candidate was used. Automatic promotion remains <strong>blocked</strong>.</p>
<table><thead><tr><th>Candidate</th><th>Class</th><th>Approaches</th><th>Disposition</th><th>Pedestrian audit</th><th>Blockers</th><th>Map</th></tr></thead><tbody>
{table_rows}
</tbody></table>
</body></html>"""


def _artifact(path: Path) -> dict[str, Any]:
    return {
        "role": "generated",
        "path": str(path),
        "sha256": file_sha256(path),
    }


def _reset_owned_directory(destination: Path) -> None:
    owner = destination / "teacher-free-discovery.owner.json"
    if destination.exists() and any(destination.iterdir()):
        if not owner.is_file():
            raise ValueError("Refusing to clear a non-empty discovery directory without Torii ownership metadata.")
        payload = json.loads(owner.read_text(encoding="utf-8"))
        if payload.get("schema") != _OWNER_SCHEMA:
            raise ValueError("Teacher-free discovery ownership metadata is invalid.")
        if payload.get("owned_root") != str(destination):
            raise ValueError("Teacher-free discovery ownership root does not match the output directory.")
        shutil.rmtree(destination)
    destination.mkdir(parents=True, exist_ok=True)
    write_json_atomic(
        owner,
        {
            "schema": _OWNER_SCHEMA,
            "purpose": "generated teacher-free OSM discovery artifacts",
            "owned_root": str(destination),
        },
        sort_keys=True,
    )
