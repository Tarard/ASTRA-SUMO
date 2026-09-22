"""Read-only adapter for Hamburg's Hauptverkehrsstraßen (HVS) OGC snapshot.

The HVS collection itself is the official membership assertion.  This adapter
therefore preserves that fact at the feature level, but deliberately does not
try to map an HVS feature to HH-SIB, OSM, or SUMO.  A reviewer must make that
separate conflation and retain the HVS feature reference in any resulting
road-property assignment.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from ..contracts import RoadObjectRef


HVS_ADAPTER_SCHEMA = "torii.hamburg-hauptverkehrsstrassen-membership/v1"
HVS_SOURCE_ID = "hamburg_hauptverkehrsstrassen"
HVS_DATASET_ID = "hauptverkehrsstrassen"
HVS_COLLECTION_ID = "hauptverkehrsstrassen"
HVS_PROVIDER = "Freie und Hansestadt Hamburg"
HVS_JURISDICTION = "DE-HH"
HVS_CLASSIFICATION_SCHEME = "de:hamburg:hvs"
HVS_LICENSE = "Datenlizenz Deutschland Namensnennung 2.0"


def read_hamburg_hvs_snapshot(
    snapshot_file: Path,
    *,
    request_url: str,
    bbox: tuple[float, float, float, float],
    target_time: datetime,
    retrieved_at: datetime,
    valid_from: datetime | None = None,
    valid_to: datetime | None = None,
    expected_sha256: str | None = None,
) -> dict[str, Any]:
    """Read a frozen official HVS collection without attempting conflation.

    A successful report means that each listed GeoJSON feature was present in
    this immutable HVS collection response.  It does not mean that any
    feature is an HH-SIB link, OSM way, SUMO edge, legal lane movement, or
    signal-controlled approach.
    """

    path = Path(snapshot_file).expanduser().resolve()
    raw_bytes = path.read_bytes()
    actual_sha256 = hashlib.sha256(raw_bytes).hexdigest()
    normalized_bbox = _validate_bbox(bbox)
    _require_aware(target_time, "target_time")
    _require_aware(retrieved_at, "retrieved_at")
    _validate_optional_time(valid_from, "valid_from")
    _validate_optional_time(valid_to, "valid_to")
    if valid_from is not None and valid_to is not None and valid_to <= valid_from:
        raise ValueError("valid_to must be later than valid_from")

    request_errors = _request_url_errors(request_url)
    source_snapshot = _source_snapshot(
        path=path,
        raw_bytes=raw_bytes,
        sha256=actual_sha256,
        request_url=request_url,
        bbox=normalized_bbox,
        target_time=target_time,
        retrieved_at=retrieved_at,
        valid_from=valid_from,
        valid_to=valid_to,
        server_timestamp=None,
    )
    if expected_sha256 is not None and expected_sha256.lower() != actual_sha256:
        return _blocked_empty_report(
            source_snapshot,
            blocking_reasons=["source_sha256_mismatch", *request_errors],
        )
    if request_errors:
        return _blocked_empty_report(source_snapshot, blocking_reasons=request_errors)

    try:
        payload = json.loads(raw_bytes)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        return _blocked_empty_report(
            source_snapshot,
            blocking_reasons=[f"invalid_geojson:{type(exc).__name__}"],
        )
    if not isinstance(payload, Mapping):
        return _blocked_empty_report(source_snapshot, blocking_reasons=["feature_collection_required"])

    server_timestamp = str(payload.get("timeStamp", "") or "") or None
    source_snapshot = _source_snapshot(
        path=path,
        raw_bytes=raw_bytes,
        sha256=actual_sha256,
        request_url=request_url,
        bbox=normalized_bbox,
        target_time=target_time,
        retrieved_at=retrieved_at,
        valid_from=valid_from,
        valid_to=valid_to,
        server_timestamp=server_timestamp,
    )
    acquisition_errors = _feature_collection_errors(payload)
    if acquisition_errors:
        return _blocked_empty_report(source_snapshot, blocking_reasons=acquisition_errors)

    normalized_features: list[dict[str, Any]] = []
    feature_errors: list[str] = []
    for index, raw_feature in enumerate(payload["features"]):
        normalized, errors = _normalize_feature(raw_feature, index=index)
        feature_errors.extend(errors)
        if normalized is not None:
            normalized_features.append(normalized)
    duplicate_ids = _duplicate_feature_ids(normalized_features)
    feature_errors.extend(f"duplicate_feature_id:{feature_id}" for feature_id in duplicate_ids)
    if feature_errors:
        return _blocked_empty_report(source_snapshot, blocking_reasons=feature_errors)

    normalized_features.sort(key=lambda item: item["feature_id"])
    time_status, time_reason = _time_alignment_status(target_time, valid_from, valid_to)
    membership_assertions = [
        _membership_assertion(
            feature,
            source_sha256=actual_sha256,
            valid_from=valid_from,
            valid_to=valid_to,
            status=time_status,
        )
        for feature in normalized_features
    ]
    raw_feature_assertions = [
        _raw_feature_assertion(feature, assertion) for feature, assertion in zip(normalized_features, membership_assertions)
    ]

    blocking_reasons = [time_reason] if time_status == "blocked" else []
    review_reasons = [time_reason] if time_status == "review_required" else []
    claim_status = "blocked" if blocking_reasons else "review_required" if review_reasons else "pass"
    official_category_source = _official_category_source(
        source_snapshot=source_snapshot,
        status=claim_status,
        membership_assertions=membership_assertions,
        blocking_reasons=blocking_reasons,
        review_reasons=review_reasons,
    )
    return {
        "schema": HVS_ADAPTER_SCHEMA,
        "source_id": HVS_SOURCE_ID,
        "status": claim_status,
        "acquisition_status": "pass",
        "claim_status": claim_status,
        "source_snapshot": source_snapshot,
        "source_attribution": HVS_PROVIDER,
        "license": HVS_LICENSE,
        "raw_feature_assertions": raw_feature_assertions,
        "membership_assertions": membership_assertions,
        "official_category_source": official_category_source,
        "reviewed_membership_assignment_template": _reviewed_membership_assignment_template(
            membership_assertions,
        ),
        "counts": {
            "feature_count": len(normalized_features),
            "membership_assertion_count": len(membership_assertions),
        },
        "blocking_reasons": blocking_reasons,
        "review_reasons": review_reasons,
        "classification_only": True,
        "automatic_promotion_gate": "blocked",
        "not_applicable_claims": [
            "hh_sib_road_link_identity",
            "osm_way_identity",
            "sumo_edge_identity",
            "physical_intersection_core",
            "legal_lane_movement",
            "stop_line",
            "traffic_signal_controller",
        ],
        "claim_boundary": (
            "Membership is asserted only because the frozen feature appears in Hamburg's official HVS collection. "
            "No value in feature properties, including HH-SIB-like klasse codes, is used to infer HVS membership. "
            "The adapter does not conflate HVS features with HH-SIB, OSM, or SUMO; a reviewed assignment must "
            "retain a selected HVS feature reference before a later road-detail projection may consume it."
        ),
    }


def _source_snapshot(
    *,
    path: Path,
    raw_bytes: bytes,
    sha256: str,
    request_url: str,
    bbox: tuple[float, float, float, float],
    target_time: datetime,
    retrieved_at: datetime,
    valid_from: datetime | None,
    valid_to: datetime | None,
    server_timestamp: str | None,
) -> dict[str, Any]:
    time_status, _ = _time_alignment_status(target_time, valid_from, valid_to)
    query_identity = {
        "schema": HVS_ADAPTER_SCHEMA,
        "request_url": _normalized_url(request_url),
        "bbox": list(bbox),
        "dataset_id": HVS_DATASET_ID,
        "collection_id": HVS_COLLECTION_ID,
    }
    return {
        "path": str(path),
        "bytes": len(raw_bytes),
        "sha256": sha256,
        "request_url": request_url,
        "normalized_request_url": query_identity["request_url"],
        "query_identity_sha256": _stable_digest(query_identity),
        "dataset_id": HVS_DATASET_ID,
        "collection_id": HVS_COLLECTION_ID,
        "bbox": list(bbox),
        "target_time": target_time.isoformat(),
        "retrieved_at": retrieved_at.isoformat(),
        "valid_from": valid_from.isoformat() if valid_from else None,
        "valid_to": valid_to.isoformat() if valid_to else None,
        "validity_basis": "caller_declared" if valid_from is not None and valid_to is not None else "unknown",
        "server_timestamp": server_timestamp,
        "server_timestamp_is_validity": False,
        "time_alignment_status": time_status,
    }


def _blocked_empty_report(
    source_snapshot: Mapping[str, Any],
    *,
    blocking_reasons: Sequence[str],
) -> dict[str, Any]:
    reasons = sorted(set(str(item) for item in blocking_reasons if str(item)))
    official_category_source = _official_category_source(
        source_snapshot=source_snapshot,
        status="blocked",
        membership_assertions=[],
        blocking_reasons=reasons,
        review_reasons=[],
    )
    return {
        "schema": HVS_ADAPTER_SCHEMA,
        "source_id": HVS_SOURCE_ID,
        "status": "blocked",
        "acquisition_status": "blocked",
        "claim_status": "blocked",
        "source_snapshot": dict(source_snapshot),
        "source_attribution": HVS_PROVIDER,
        "license": HVS_LICENSE,
        "raw_feature_assertions": [],
        "membership_assertions": [],
        "official_category_source": official_category_source,
        "reviewed_membership_assignment_template": _reviewed_membership_assignment_template([]),
        "counts": {"feature_count": 0, "membership_assertion_count": 0},
        "blocking_reasons": reasons,
        "review_reasons": [],
        "classification_only": True,
        "automatic_promotion_gate": "blocked",
    }


def _official_category_source(
    *,
    source_snapshot: Mapping[str, Any],
    status: str,
    membership_assertions: Sequence[Mapping[str, Any]],
    blocking_reasons: Sequence[str],
    review_reasons: Sequence[str],
) -> dict[str, Any]:
    """Return the minimal, reusable source report consumed by bridge review logic."""

    return {
        "schema": HVS_ADAPTER_SCHEMA,
        "source_id": HVS_SOURCE_ID,
        "status": status,
        "source_snapshot": dict(source_snapshot),
        "membership_assertions": [dict(item) for item in membership_assertions],
        "blocking_reasons": sorted(set(blocking_reasons)),
        "review_reasons": sorted(set(review_reasons)),
        "classification_only": True,
        "automatic_promotion_gate": "blocked",
        "claim_boundary": (
            "This source declares only feature-level HVS collection membership. It is not an automatic "
            "official-to-OSM/SUMO mapping or an authorization to assign a road category to another source object."
        ),
    }


def _feature_collection_errors(payload: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    if payload.get("type") != "FeatureCollection":
        errors.append("feature_collection_type_required")
    features = payload.get("features")
    if not isinstance(features, list):
        errors.append("features_list_required")
        return errors
    number_matched = payload.get("numberMatched")
    number_returned = payload.get("numberReturned")
    if not isinstance(number_matched, int) or not isinstance(number_returned, int):
        errors.append("number_matched_and_returned_required")
    elif number_matched != number_returned or number_returned != len(features):
        errors.append("incomplete_or_inconsistent_feature_collection")
    return errors


def _normalize_feature(raw_feature: Any, *, index: int) -> tuple[dict[str, Any] | None, list[str]]:
    if not isinstance(raw_feature, Mapping):
        return None, [f"feature_not_object:{index}"]
    errors: list[str] = []
    if raw_feature.get("type") != "Feature":
        errors.append(f"feature_type_required:{index}")
    feature_id = raw_feature.get("id")
    if feature_id is None or not str(feature_id).strip():
        errors.append(f"feature_id_required:{index}")
    geometry = raw_feature.get("geometry")
    if not isinstance(geometry, Mapping):
        errors.append(f"feature_geometry_required:{index}")
    elif geometry.get("type") not in {"LineString", "MultiLineString"}:
        errors.append(f"linear_feature_geometry_required:{index}")
    properties = raw_feature.get("properties")
    if not isinstance(properties, Mapping):
        errors.append(f"feature_properties_required:{index}")
    if errors:
        return None, errors
    return {
        "feature_id": str(feature_id),
        "geometry": dict(geometry),
        "properties": dict(properties),
    }, []


def _duplicate_feature_ids(features: Sequence[Mapping[str, Any]]) -> list[str]:
    seen: set[str] = set()
    duplicates: set[str] = set()
    for feature in features:
        feature_id = str(feature["feature_id"])
        if feature_id in seen:
            duplicates.add(feature_id)
        seen.add(feature_id)
    return sorted(duplicates)


def _membership_assertion(
    feature: Mapping[str, Any],
    *,
    source_sha256: str,
    valid_from: datetime | None,
    valid_to: datetime | None,
    status: str,
) -> dict[str, Any]:
    source_ref = RoadObjectRef(
        namespace="official.hamburg_hvs",
        object_type="hvs_feature",
        object_id=str(feature["feature_id"]),
        source_sha256=source_sha256,
        valid_from=valid_from,
        valid_to=valid_to,
        provider=HVS_PROVIDER,
        dataset=f"{HVS_DATASET_ID}/{HVS_COLLECTION_ID}",
        jurisdiction=HVS_JURISDICTION,
    )
    identity = {
        "source_ref": source_ref.as_dict(),
        "property_name": "hamburg_membership",
        "classification_scheme": HVS_CLASSIFICATION_SCHEME,
        "value": "hvs",
    }
    return {
        "assertion_id": f"hvs-membership-{_stable_digest(identity)[:20]}",
        "source_id": HVS_SOURCE_ID,
        "source_ref": source_ref.as_dict(),
        "feature_id": str(feature["feature_id"]),
        "property_name": "hamburg_membership",
        "classification_scheme": HVS_CLASSIFICATION_SCHEME,
        "value": "hvs",
        "status": status,
        "evidence_refs": [source_ref.as_dict()],
        "mapping_status": "unmapped",
        "reason": "direct membership in the official HVS collection; no property-code inference",
        "classification_only": True,
        "automatic_promotion_gate": "blocked",
    }


def _raw_feature_assertion(feature: Mapping[str, Any], assertion: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "feature_id": str(feature["feature_id"]),
        "source_ref": dict(assertion["source_ref"]),
        "geometry": dict(feature["geometry"]),
        "properties": dict(feature["properties"]),
        "membership_assertion_id": str(assertion["assertion_id"]),
        "classification_only": True,
    }


def _reviewed_membership_assignment_template(
    membership_assertions: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Show the required reviewed step without manufacturing an HH-SIB target."""

    evidence_refs: list[dict[str, Any]] = []
    if membership_assertions:
        evidence_refs.append(dict(membership_assertions[0]["source_ref"]))
    return {
        "schema": "torii.hamburg-hvs-reviewed-membership-assignment-template/v1",
        "status": "review_required",
        "requires_reviewed_official_conflation": True,
        "required_target": "an existing HH-SIB road_link_assertion selected by a human-reviewed conflation",
        "example": {
            "assignment_id": "reviewed-hvs-membership-<unique-id>",
            "target_ref": "<reviewed HH-SIB road_link_assertion ref>",
            "property_name": "hamburg_membership",
            "classification_scheme": HVS_CLASSIFICATION_SCHEME,
            "value": "hvs",
            "direction": "both",
            "evidence_refs": evidence_refs or ["<selected HVS feature source_ref>"],
            "status": "pass",
            "reason": "human-reviewed HVS feature to HH-SIB road-link conflation",
        },
        "claim_boundary": (
            "This is an example of a later review artifact, not an assignment generated by the adapter. "
            "The selected HVS feature must remain in evidence_refs so the frozen source hash can be verified."
        ),
    }


def _time_alignment_status(
    target_time: datetime,
    valid_from: datetime | None,
    valid_to: datetime | None,
) -> tuple[str, str]:
    if valid_from is None and valid_to is None:
        return "review_required", "source_validity_not_declared"
    if valid_from is None or valid_to is None:
        return "blocked", "source_validity_interval_incomplete"
    if not valid_from <= target_time < valid_to:
        return "blocked", "target_time_outside_declared_validity"
    return "pass", "target_time_inside_declared_validity"


def _request_url_errors(request_url: str) -> list[str]:
    parsed = urlparse(request_url)
    errors: list[str] = []
    if parsed.scheme != "https" or parsed.hostname != "api.hamburg.de":
        errors.append("official_hamburg_https_request_url_required")
    expected_path = f"/datasets/v1/{HVS_DATASET_ID}/collections/{HVS_COLLECTION_ID}/items"
    if parsed.path.rstrip("/") != expected_path:
        errors.append("hvs_collection_request_url_required")
    return errors


def _normalized_url(value: str) -> str:
    parsed = urlparse(value)
    query = urlencode(sorted(parse_qsl(parsed.query, keep_blank_values=True)))
    return urlunparse(
        (
            parsed.scheme.lower(),
            parsed.netloc.lower(),
            parsed.path.rstrip("/"),
            "",
            query,
            "",
        )
    )


def _validate_bbox(value: tuple[float, float, float, float]) -> tuple[float, float, float, float]:
    if len(value) != 4:
        raise ValueError("bbox must contain west, south, east, north")
    bbox = tuple(float(item) for item in value)
    if not all(math.isfinite(item) for item in bbox):
        raise ValueError("bbox values must be finite")
    west, south, east, north = bbox
    if not west < east or not south < north:
        raise ValueError("bbox must satisfy west < east and south < north")
    return west, south, east, north


def _validate_optional_time(value: datetime | None, field_name: str) -> None:
    if value is not None:
        _require_aware(value, field_name)


def _require_aware(value: datetime, field_name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")


def _stable_digest(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
