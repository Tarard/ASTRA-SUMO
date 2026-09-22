from __future__ import annotations

import hashlib
import json
import math
import xml.etree.ElementTree as ET
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

from .osm_network import (
    _net_xy_to_latlon,
    google_maps_baseline_fields,
    google_maps_url,
    kartaview_url,
    mapillary_url,
    regional_map_fields,
)


MAP_REVIEW_EVIDENCE_SCHEMA = "torii.map_review_evidence.v1"
MAP_REVIEW_DECISION_SCHEMA = "torii.map_review_decision.v1"

_OBSERVED_FACT_FIELDS = (
    "feature_presence",
    "geometry_connectivity",
    "access_modes",
    "source_limitations",
)

_REVIEW_QUESTIONS = {
    "add_edge": "Does this road or path exist with the proposed direction, access, and geometry?",
    "add_sidewalk": "Does a continuous pedestrian facility exist at this location?",
    "add_crossing": "Does a pedestrian crossing exist here, and which road edges does it cross?",
    "add_ramp": "Does this ramp exist with the proposed direction and access permissions?",
    "delete_edge": "Is this road or path absent, closed, duplicated, or inappropriate for the modeled network?",
    "merge_edges": "Do these SUMO fragments represent one continuous physical road or junction?",
    "review_marker": "What does the current or time-aligned map evidence show at this location?",
}


def build_map_review_evidence(
    *,
    source_net_file: Path,
    candidate_net_file: Path,
    candidate_sha256: str,
    locations: Sequence[Mapping[str, Any]],
    temporal_scope: str = "unspecified",
    target_date: str | None = None,
) -> dict[str, Any]:
    """Build a candidate-bound, human-review map evidence bundle.

    Map links are review aids.  They never approve an edit by themselves.  A
    required map review is ready only when the modeled time scope is explicit
    and every required location has a real geographic coordinate.
    """

    baseline = google_maps_baseline_fields(temporal_scope, target_date)
    converter, projection_status = _coordinate_converter(source_net_file)
    source_sha256 = _file_sha256(source_net_file)
    records: list[dict[str, Any]] = []
    for raw in locations:
        proposal_id = str(raw.get("proposal_id", raw.get("operation_id", ""))).strip()
        operation = str(raw.get("operation", "review_marker")).strip() or "review_marker"
        location_id = str(raw.get("location_id", "")).strip() or f"corridor_edit:{proposal_id}"
        required = raw.get("map_review_required") is True
        question = str(raw.get("review_question", "")).strip() or _REVIEW_QUESTIONS.get(
            operation,
            _REVIEW_QUESTIONS["review_marker"],
        )
        coordinate = _coordinate_record(
            raw.get("location"),
            converter=converter,
            projection_status=projection_status,
        )
        map_fields = _map_fields(coordinate, proposal_id=proposal_id)
        records.append(
            {
                "location_id": location_id,
                "proposal_id": proposal_id,
                "operation": operation,
                "map_review_required": required,
                "review_status": "needs_manual_review" if required else "optional_review",
                "review_question": question,
                "geometry_source": str(raw.get("geometry_source", "source_net")),
                "coordinate": coordinate,
                "candidate_sha256": candidate_sha256,
                **map_fields,
            }
        )

    required_ids = sorted(record["location_id"] for record in records if record["map_review_required"])
    unavailable_required = sorted(
        record["location_id"]
        for record in records
        if record["map_review_required"] and record["coordinate"]["status"] != "available"
    )
    missing_provider_required = sorted(
        record["location_id"]
        for record in records
        if record["map_review_required"] and not record.get("regional_map_url")
    )
    time_confirmation_required = bool(required_ids) and baseline["google_maps_requires_time_confirmation"] == "yes"
    if not required_ids:
        readiness = "not_required"
    elif unavailable_required or missing_provider_required or time_confirmation_required:
        readiness = "blocked"
    else:
        readiness = "pass"

    return {
        "schema": MAP_REVIEW_EVIDENCE_SCHEMA,
        "status": "pass",
        "claim_status": "diagnostic-demo",
        "source_net_file": str(source_net_file.resolve()),
        "candidate_net_file": str(candidate_net_file.resolve()),
        "source_sha256": source_sha256,
        "candidate_sha256": candidate_sha256,
        **baseline,
        "coordinate_projection_status": projection_status,
        "location_count": len(records),
        "required_location_count": len(required_ids),
        "required_location_ids": required_ids,
        "unavailable_required_location_ids": unavailable_required,
        "missing_provider_required_location_ids": missing_provider_required,
        "review_readiness_status": readiness,
        "locations": records,
        "warnings": _review_warnings(
            unavailable_required=unavailable_required,
            missing_provider_required=missing_provider_required,
            time_confirmation_required=time_confirmation_required,
        ),
    }


def build_map_review_decision_binding(
    evidence: Mapping[str, Any] | None,
    *,
    evidence_file: Path | None = None,
    evidence_sha256: str = "",
) -> dict[str, Any]:
    if not isinstance(evidence, Mapping):
        return {
            "schema": MAP_REVIEW_DECISION_SCHEMA,
            "evidence_file": "",
            "evidence_sha256": "",
            "required_location_ids": [],
            "decisions": [],
        }
    locations = {
        str(item.get("location_id", "")): item
        for item in evidence.get("locations", []) or []
        if isinstance(item, Mapping) and str(item.get("location_id", "")).strip()
    }
    required_ids = [str(value) for value in evidence.get("required_location_ids", []) or []]
    decisions = []
    for location_id in sorted(required_ids):
        location = locations.get(location_id, {})
        decisions.append(
            {
                "location_id": location_id,
                "proposal_id": str(location.get("proposal_id", "")),
                "decision": "pending",
                "observed_facts": {field: "" for field in _OBSERVED_FACT_FIELDS},
                "reviewer": "",
                "reviewed_at": "",
                "provider": str(location.get("regional_map_provider", "")),
                "map_url": str(location.get("regional_map_url", "")),
                "temporal_scope": str(evidence.get("google_maps_temporal_scope", "unspecified")),
                "target_date": str(evidence.get("google_maps_target_date", "")),
                "candidate_sha256": str(evidence.get("candidate_sha256", "")),
            }
        )
    return {
        "schema": MAP_REVIEW_DECISION_SCHEMA,
        "evidence_file": str(evidence_file.resolve()) if evidence_file is not None else "",
        "evidence_sha256": evidence_sha256,
        "required_location_ids": sorted(required_ids),
        "review_readiness_status": str(evidence.get("review_readiness_status", "not_required")),
        "decisions": decisions,
    }


def validate_map_review_evidence(
    evidence: Mapping[str, Any] | None,
    *,
    source_net_file: Path,
    candidate_net_file: Path,
    evidence_file: Path | None = None,
    evidence_sha256: str = "",
) -> dict[str, Any]:
    errors: list[dict[str, Any]] = []
    if not isinstance(evidence, Mapping):
        return {"status": "blocked", "errors": [{"code": "map_review_evidence_required"}]}
    if str(evidence.get("schema", "")) != MAP_REVIEW_EVIDENCE_SCHEMA:
        errors.append({"code": "map_review_evidence_schema_mismatch"})
    if str(evidence.get("status", "")) != "pass":
        errors.append({"code": "map_review_evidence_status_not_pass"})
    if not _same_path(evidence.get("source_net_file"), source_net_file):
        errors.append({"code": "map_review_source_path_mismatch"})
    if not _same_path(evidence.get("candidate_net_file"), candidate_net_file):
        errors.append({"code": "map_review_candidate_path_mismatch"})
    expected_source_hash = _file_sha256(source_net_file) if source_net_file.is_file() else ""
    expected_candidate_hash = _file_sha256(candidate_net_file) if candidate_net_file.is_file() else ""
    if str(evidence.get("source_sha256", "")) != expected_source_hash:
        errors.append({"code": "map_review_source_hash_mismatch"})
    if str(evidence.get("candidate_sha256", "")) != expected_candidate_hash:
        errors.append({"code": "map_review_candidate_hash_mismatch"})

    locations = evidence.get("locations")
    if not isinstance(locations, list):
        errors.append({"code": "map_review_locations_must_be_list"})
        locations = []
    location_ids: list[str] = []
    actual_required: list[str] = []
    actual_unavailable_required: list[str] = []
    actual_missing_provider_required: list[str] = []
    for item in locations:
        if not isinstance(item, Mapping):
            errors.append({"code": "map_review_location_must_be_object"})
            continue
        location_id = str(item.get("location_id", "")).strip()
        if not location_id:
            errors.append({"code": "map_review_location_id_required"})
            continue
        location_ids.append(location_id)
        if "map_review_required" in item and type(item.get("map_review_required")) is not bool:
            errors.append({"code": "map_review_required_must_be_boolean", "location_id": location_id})
        is_required = item.get("map_review_required") is True
        if is_required:
            actual_required.append(location_id)
        if not str(item.get("proposal_id", "")).strip():
            errors.append({"code": "map_review_proposal_id_required", "location_id": location_id})
        if is_required and not str(item.get("review_question", "")).strip():
            errors.append({"code": "map_review_question_required", "location_id": location_id})
        if str(item.get("candidate_sha256", "")) != expected_candidate_hash:
            errors.append({"code": "map_review_location_candidate_hash_mismatch", "location_id": location_id})
        coordinate = item.get("coordinate")
        if not isinstance(coordinate, Mapping):
            errors.append({"code": "map_review_coordinate_must_be_object", "location_id": location_id})
            coordinate = {}
        coordinate_available = str(coordinate.get("status", "")) == "available"
        if coordinate_available:
            try:
                lat = float(coordinate["lat"])
                lon = float(coordinate["lon"])
            except (KeyError, TypeError, ValueError):
                lat = math.nan
                lon = math.nan
            if not _valid_latlon(lat, lon):
                errors.append({"code": "map_review_coordinate_invalid", "location_id": location_id})
            else:
                expected_map_fields = _map_fields(coordinate, proposal_id=str(item.get("proposal_id", "")))
                for field in (
                    "google_maps_url",
                    "google_maps_satellite_url",
                    "regional_map_provider",
                    "regional_map_url",
                    "regional_map_coordinate_system",
                    "regional_map_source_coordinate_system",
                    "mapillary_url",
                    "kartaview_url",
                ):
                    if str(item.get(field, "")) != str(expected_map_fields.get(field, "")):
                        errors.append(
                            {
                                "code": "map_review_generated_map_field_mismatch",
                                "location_id": location_id,
                                "field": field,
                            }
                        )
        if is_required and not coordinate_available:
            actual_unavailable_required.append(location_id)
        if is_required and (
            not str(item.get("regional_map_provider", "")).strip()
            or not str(item.get("regional_map_url", "")).strip()
        ):
            actual_missing_provider_required.append(location_id)
    duplicates = sorted({value for value in location_ids if location_ids.count(value) > 1})
    if duplicates:
        errors.append({"code": "map_review_duplicate_location_ids", "location_ids": duplicates})
    declared_required = sorted(str(value) for value in evidence.get("required_location_ids", []) or [])
    if declared_required != sorted(actual_required):
        errors.append(
            {
                "code": "map_review_required_location_ids_mismatch",
                "expected": sorted(actual_required),
                "actual": declared_required,
            }
        )

    temporal_scope = str(evidence.get("google_maps_temporal_scope", "")).strip().lower()
    target_date = str(evidence.get("google_maps_target_date", "")).strip()
    temporal_scope_valid = temporal_scope in {"unspecified", "current", "historical"}
    if not temporal_scope_valid:
        errors.append({"code": "map_review_temporal_scope_invalid"})
    time_confirmation_required = temporal_scope == "unspecified" or (
        temporal_scope == "historical" and not target_date
    )
    expected_confirmation = "yes" if time_confirmation_required else "no"
    if str(evidence.get("google_maps_requires_time_confirmation", "")) != expected_confirmation:
        errors.append({"code": "map_review_time_confirmation_mismatch"})
    expected_unavailable = sorted(actual_unavailable_required)
    expected_missing_provider = sorted(actual_missing_provider_required)
    if sorted(str(value) for value in evidence.get("unavailable_required_location_ids", []) or []) != (
        expected_unavailable
    ):
        errors.append({"code": "map_review_unavailable_required_ids_mismatch"})
    if sorted(
        str(value) for value in evidence.get("missing_provider_required_location_ids", []) or []
    ) != expected_missing_provider:
        errors.append({"code": "map_review_missing_provider_required_ids_mismatch"})
    if not declared_required:
        expected_readiness = "not_required"
    elif (
        expected_unavailable
        or expected_missing_provider
        or time_confirmation_required
        or not temporal_scope_valid
    ):
        expected_readiness = "blocked"
    else:
        expected_readiness = "pass"
    if str(evidence.get("review_readiness_status", "")) != expected_readiness:
        errors.append({"code": "map_review_readiness_status_mismatch"})
    if evidence.get("location_count") != len(locations):
        errors.append({"code": "map_review_location_count_mismatch"})
    if evidence.get("required_location_count") != len(declared_required):
        errors.append({"code": "map_review_required_location_count_mismatch"})

    persisted: Mapping[str, Any] | None = None
    if evidence_file is not None:
        if not evidence_file.is_file():
            errors.append({"code": "map_review_evidence_file_missing", "path": str(evidence_file)})
        else:
            actual_evidence_hash = _file_sha256(evidence_file)
            if not evidence_sha256:
                errors.append({"code": "map_review_evidence_sha256_required"})
            elif evidence_sha256 != actual_evidence_hash:
                errors.append({"code": "map_review_evidence_sha256_mismatch"})
            try:
                loaded = json.loads(evidence_file.read_text(encoding="utf-8"))
                persisted = loaded if isinstance(loaded, Mapping) else None
            except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
                errors.append(
                    {
                        "code": "map_review_evidence_file_invalid",
                        "error": f"{type(exc).__name__}: {exc}",
                    }
                )
            if persisted is None:
                errors.append({"code": "map_review_evidence_file_not_object"})
            elif _canonical_json(evidence) != _canonical_json(persisted):
                errors.append({"code": "map_review_persisted_content_mismatch"})

    return {
        "status": "pass" if not errors else "blocked",
        "review_readiness_status": expected_readiness,
        "required_location_ids": declared_required,
        "evidence_file": str(evidence_file.resolve()) if evidence_file is not None else "",
        "evidence_sha256": evidence_sha256,
        "errors": errors,
        "evidence": dict(evidence),
    }


def validate_map_review_decisions(
    decision: Mapping[str, Any] | None,
    *,
    evidence: Mapping[str, Any] | None,
    evidence_file: Path | None,
    evidence_sha256: str,
    candidate_net_file: Path,
) -> dict[str, Any]:
    evidence_mapping = evidence if isinstance(evidence, Mapping) else {}
    required_ids = sorted(
        str(value) for value in evidence_mapping.get("required_location_ids", []) or []
    )
    if not required_ids:
        return {
            "status": "pass",
            "required_location_ids": [],
            "decision_count": 0,
            "errors": [],
        }
    errors: list[dict[str, Any]] = []
    if not isinstance(evidence, Mapping):
        errors.append({"code": "required_map_review_evidence_missing"})
        return {"status": "blocked", "required_location_ids": required_ids, "errors": errors}
    if str(evidence.get("review_readiness_status", "")) != "pass":
        errors.append({"code": "map_review_evidence_not_ready"})
    if not isinstance(decision, Mapping):
        errors.append({"code": "map_review_decision_required"})
        return {"status": "blocked", "required_location_ids": required_ids, "errors": errors}
    if str(decision.get("schema", "")) != MAP_REVIEW_DECISION_SCHEMA:
        errors.append({"code": "map_review_decision_schema_mismatch"})
    if evidence_file is None or not _same_path(decision.get("evidence_file"), evidence_file):
        errors.append({"code": "map_review_decision_evidence_path_mismatch"})
    if str(decision.get("evidence_sha256", "")) != evidence_sha256:
        errors.append({"code": "map_review_decision_evidence_hash_mismatch"})
    declared_required = sorted(str(value) for value in decision.get("required_location_ids", []) or [])
    if declared_required != required_ids:
        errors.append({"code": "map_review_decision_required_ids_mismatch"})

    evidence_locations = {
        str(item.get("location_id", "")): item
        for item in evidence.get("locations", []) or []
        if isinstance(item, Mapping) and str(item.get("location_id", "")).strip()
    }
    decisions = decision.get("decisions")
    if not isinstance(decisions, list):
        errors.append({"code": "map_review_decisions_must_be_list"})
        decisions = []
    decisions_by_id: dict[str, Mapping[str, Any]] = {}
    for item in decisions:
        if not isinstance(item, Mapping):
            errors.append({"code": "map_review_decision_item_must_be_object"})
            continue
        location_id = str(item.get("location_id", "")).strip()
        if not location_id:
            errors.append({"code": "map_review_decision_location_id_required"})
        elif location_id in decisions_by_id:
            errors.append({"code": "map_review_duplicate_decision", "location_id": location_id})
        else:
            decisions_by_id[location_id] = item
    unexpected_decisions = sorted(set(decisions_by_id) - set(required_ids))
    if unexpected_decisions:
        errors.append(
            {
                "code": "map_review_unexpected_decisions",
                "location_ids": unexpected_decisions,
            }
        )

    candidate_sha256 = _file_sha256(candidate_net_file) if candidate_net_file.is_file() else ""
    for location_id in required_ids:
        item = decisions_by_id.get(location_id)
        location = evidence_locations.get(location_id, {})
        if item is None:
            errors.append({"code": "map_review_decision_missing", "location_id": location_id})
            continue
        if str(item.get("decision", "")) != "approved":
            errors.append({"code": "map_review_decision_not_approved", "location_id": location_id})
        observed_facts = item.get("observed_facts")
        if not isinstance(observed_facts, Mapping):
            errors.append({"code": "map_review_observed_facts_must_be_object", "location_id": location_id})
        else:
            for field in _OBSERVED_FACT_FIELDS:
                if not str(observed_facts.get(field, "")).strip():
                    errors.append(
                        {
                            "code": "map_review_observed_fact_required",
                            "location_id": location_id,
                            "field": field,
                        }
                    )
        if not str(item.get("reviewer", "")).strip():
            errors.append({"code": "map_review_reviewer_required", "location_id": location_id})
        if not _is_timezone_aware_iso8601(str(item.get("reviewed_at", ""))):
            errors.append({"code": "map_review_reviewed_at_invalid", "location_id": location_id})
        if str(item.get("candidate_sha256", "")) != candidate_sha256:
            errors.append({"code": "map_review_decision_candidate_hash_mismatch", "location_id": location_id})
        if str(item.get("proposal_id", "")) != str(location.get("proposal_id", "")):
            errors.append({"code": "map_review_decision_proposal_id_mismatch", "location_id": location_id})
        for field, evidence_field in (
            ("provider", "regional_map_provider"),
            ("map_url", "regional_map_url"),
        ):
            if str(item.get(field, "")) != str(location.get(evidence_field, "")):
                errors.append({"code": f"map_review_decision_{field}_mismatch", "location_id": location_id})
        if str(item.get("temporal_scope", "")) != str(evidence.get("google_maps_temporal_scope", "")):
            errors.append({"code": "map_review_decision_temporal_scope_mismatch", "location_id": location_id})
        if str(item.get("target_date", "")) != str(evidence.get("google_maps_target_date", "")):
            errors.append({"code": "map_review_decision_target_date_mismatch", "location_id": location_id})

    return {
        "status": "pass" if not errors else "blocked",
        "required_location_ids": required_ids,
        "decision_count": len(decisions_by_id),
        "errors": errors,
    }


def _coordinate_converter(net_file: Path) -> tuple[Callable[[float, float], tuple[float, float]] | None, str]:
    try:
        root = ET.parse(net_file).getroot()
    except (OSError, ET.ParseError) as exc:
        return None, f"source_net_invalid:{type(exc).__name__}"
    location = root.find("location")
    proj_parameter = str(location.attrib.get("projParameter", "")).strip() if location is not None else ""
    if proj_parameter in {"", "!", "-"}:
        return None, "unavailable_no_geo_projection"
    try:
        import sumolib.net  # type: ignore

        net = sumolib.net.readNet(str(net_file))
    except (ImportError, OSError, RuntimeError, ValueError) as exc:
        return None, f"unavailable_projection_loader:{type(exc).__name__}"
    return lambda x, y: _net_xy_to_latlon(net, x, y), "wgs84_from_sumo_projection"


def _coordinate_record(
    raw_location: object,
    *,
    converter: Callable[[float, float], tuple[float, float]] | None,
    projection_status: str,
) -> dict[str, Any]:
    location = dict(raw_location) if isinstance(raw_location, Mapping) else {}
    try:
        x = float(location["x"])
        y = float(location["y"])
    except (KeyError, TypeError, ValueError):
        x = None
        y = None
    try:
        lat = float(location["lat"])
        lon = float(location["lon"])
    except (KeyError, TypeError, ValueError):
        lat = None
        lon = None
    if lat is not None and lon is not None and _valid_latlon(lat, lon):
        return {
            "status": "available",
            "coordinate_status": "explicit_wgs84",
            "source_coordinate_system": "WGS84",
            "map_coordinate_system": "WGS84",
            "x": x,
            "y": y,
            "lat": round(lat, 7),
            "lon": round(lon, 7),
        }
    if x is None or y is None:
        return {
            "status": "unavailable",
            "coordinate_status": "missing_xy_and_wgs84",
            "source_coordinate_system": "SUMO_XY",
            "map_coordinate_system": "WGS84",
            "x": x,
            "y": y,
            "lat": None,
            "lon": None,
        }
    if converter is None:
        return {
            "status": "unavailable",
            "coordinate_status": projection_status,
            "source_coordinate_system": "SUMO_XY",
            "map_coordinate_system": "WGS84",
            "x": x,
            "y": y,
            "lat": None,
            "lon": None,
        }
    try:
        lat, lon = converter(x, y)
    except (KeyError, ModuleNotFoundError, OSError, RuntimeError, TypeError, ValueError) as exc:
        return {
            "status": "unavailable",
            "coordinate_status": f"projection_failed:{type(exc).__name__}",
            "source_coordinate_system": "SUMO_XY",
            "map_coordinate_system": "WGS84",
            "x": x,
            "y": y,
            "lat": None,
            "lon": None,
        }
    if not _valid_latlon(lat, lon):
        return {
            "status": "unavailable",
            "coordinate_status": "projected_coordinate_out_of_range",
            "source_coordinate_system": "SUMO_XY",
            "map_coordinate_system": "WGS84",
            "x": x,
            "y": y,
            "lat": None,
            "lon": None,
        }
    return {
        "status": "available",
        "coordinate_status": projection_status,
        "source_coordinate_system": "SUMO_XY",
        "map_coordinate_system": "WGS84",
        "x": x,
        "y": y,
        "lat": round(lat, 7),
        "lon": round(lon, 7),
    }


def _map_fields(coordinate: Mapping[str, Any], *, proposal_id: str) -> dict[str, str]:
    if coordinate.get("status") != "available":
        return {
            "google_maps_url": "",
            "google_maps_satellite_url": "",
            "regional_map_provider": "",
            "regional_map_url": "",
            "regional_map_coordinate_system": "",
            "regional_map_source_coordinate_system": "WGS84",
            "regional_map_audit_status": "unavailable",
            "regional_map_note": "No geographic coordinate is available for map review.",
            "mapillary_url": "",
            "kartaview_url": "",
        }
    lat = float(coordinate["lat"])
    lon = float(coordinate["lon"])
    regional = regional_map_fields(lat, lon, label=f"Torii proposal {proposal_id}")
    return {
        "google_maps_url": google_maps_url(lat, lon),
        "google_maps_satellite_url": (
            f"https://www.google.com/maps/@{lat:.7f},{lon:.7f},50m/data=!3m1!1e3"
        ),
        **regional,
        "mapillary_url": mapillary_url(lat, lon),
        "kartaview_url": kartaview_url(lat, lon),
    }


def _review_warnings(
    *,
    unavailable_required: Sequence[str],
    missing_provider_required: Sequence[str],
    time_confirmation_required: bool,
) -> list[str]:
    warnings: list[str] = []
    if unavailable_required:
        warnings.append("required map-review locations do not have usable geographic coordinates")
    if missing_provider_required:
        warnings.append("required map-review locations do not have a regional map URL")
    if time_confirmation_required:
        warnings.append("required map review needs an explicit current or historical target scope")
    warnings.append("map links are human-review aids and never approve a candidate automatically")
    return warnings


def _same_path(value: object, expected: Path) -> bool:
    if not value:
        return False
    try:
        return Path(str(value)).resolve() == expected.resolve()
    except OSError:
        return False


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_json(value: Mapping[str, Any]) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str)


def _valid_latlon(lat: float, lon: float) -> bool:
    return math.isfinite(lat) and math.isfinite(lon) and -90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0


def _is_timezone_aware_iso8601(value: str) -> bool:
    clean = value.strip()
    if not clean:
        return False
    try:
        parsed = datetime.fromisoformat(clean.replace("Z", "+00:00"))
    except ValueError:
        return False
    return parsed.tzinfo is not None and parsed.utcoffset() is not None
