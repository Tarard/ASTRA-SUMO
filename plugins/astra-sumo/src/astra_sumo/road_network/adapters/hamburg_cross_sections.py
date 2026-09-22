"""Read-only adapter for Hamburg's official cross-section inventory."""

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


HAMBURG_CROSS_SECTION_ADAPTER_SCHEMA = "torii.hamburg-cross-section-inventory/v1"
HAMBURG_CROSS_SECTION_DATASET_ID = "querschnitte"
HAMBURG_CROSS_SECTION_COLLECTION_ID = "querschnitte"
HAMBURG_CROSS_SECTION_PROVIDER = "Freie und Hansestadt Hamburg"
HAMBURG_CROSS_SECTION_JURISDICTION = "DE-HH"
HAMBURG_CROSS_SECTION_LICENSE = "Datenlizenz Deutschland Namensnennung 2.0"
HAMBURG_CROSS_SECTION_CRS = "http://www.opengis.net/def/crs/EPSG/0/25832"

_REQUIRED_PROPERTIES = (
    "von_netzknoten",
    "nach_netzknoten",
    "von_station",
    "bis_station",
    "streifen",
    "streifennr",
    "breite",
    "bis_breite",
    "art",
    "art_klartext",
)
_QUERY_KEYS = frozenset({"f", "limit", "offset", "bbox", "bbox-crs", "crs"})
_RAW_CLAIMS_ONLY = (
    "station_direction",
    "motor_vehicle_lane_class",
    "legal_lane_movement",
    "sumo_edge_or_lane",
    "lane_transition",
    "traffic_signal_control",
)


def read_hamburg_cross_section_snapshot(
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
    """Validate a complete frozen OAF response without deriving lane topology."""

    path = Path(snapshot_file).expanduser().resolve()
    raw_bytes = path.read_bytes()
    source_sha256 = hashlib.sha256(raw_bytes).hexdigest()
    normalized_bbox = _validate_bbox(bbox)
    _require_aware(target_time, "target_time")
    _require_aware(retrieved_at, "retrieved_at")
    _validate_optional_time(valid_from, "valid_from")
    _validate_optional_time(valid_to, "valid_to")
    if valid_from is not None and valid_to is not None and valid_to <= valid_from:
        raise ValueError("valid_to must be later than valid_from")

    request_errors, request_identity = _validate_request_url(request_url, normalized_bbox)
    source_snapshot = _source_snapshot(
        path=path,
        raw_bytes=raw_bytes,
        source_sha256=source_sha256,
        request_url=request_url,
        request_identity=request_identity,
        bbox=normalized_bbox,
        target_time=target_time,
        retrieved_at=retrieved_at,
        valid_from=valid_from,
        valid_to=valid_to,
        server_timestamp=None,
        number_matched=None,
        number_returned=None,
    )
    if expected_sha256 is not None and expected_sha256.lower() != source_sha256:
        return _blocked_report(source_snapshot, ["source_sha256_mismatch", *request_errors])
    if request_errors:
        return _blocked_report(source_snapshot, request_errors)

    try:
        payload = json.loads(raw_bytes)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        return _blocked_report(source_snapshot, [f"invalid_geojson:{type(exc).__name__}"])
    if not isinstance(payload, Mapping):
        return _blocked_report(source_snapshot, ["feature_collection_required"])

    source_snapshot = _source_snapshot(
        path=path,
        raw_bytes=raw_bytes,
        source_sha256=source_sha256,
        request_url=request_url,
        request_identity=request_identity,
        bbox=normalized_bbox,
        target_time=target_time,
        retrieved_at=retrieved_at,
        valid_from=valid_from,
        valid_to=valid_to,
        server_timestamp=_optional_text(payload.get("timeStamp")),
        number_matched=payload.get("numberMatched"),
        number_returned=payload.get("numberReturned"),
    )
    acquisition_errors = _feature_collection_errors(payload, request_identity)
    if acquisition_errors:
        return _blocked_report(source_snapshot, acquisition_errors)

    features: list[dict[str, Any]] = []
    errors: list[str] = []
    feature_ids: set[str] = set()
    strip_keys: set[tuple[Any, ...]] = set()
    for index, raw_feature in enumerate(payload["features"]):
        feature, feature_errors = _normalize_feature(raw_feature, index=index)
        errors.extend(feature_errors)
        if feature is None:
            continue
        feature_id = str(feature["feature_id"])
        strip_key = _strip_key(feature["properties"])
        if feature_id in feature_ids:
            errors.append(f"duplicate_feature_id:{feature_id}")
        elif strip_key in strip_keys:
            errors.append(f"duplicate_cross_section_strip:{feature_id}")
        else:
            feature_ids.add(feature_id)
            strip_keys.add(strip_key)
            features.append(feature)
    if errors:
        return _blocked_report(source_snapshot, errors)

    features.sort(key=_feature_sort_key)
    time_status, time_reason = _time_alignment_status(target_time, valid_from, valid_to)
    blocking_reasons = [time_reason] if time_status == "blocked" else []
    review_reasons = [time_reason] if time_status == "review_required" else []
    assertions = [
        _raw_assertion(
            feature,
            source_sha256=source_sha256,
            valid_from=valid_from,
            valid_to=valid_to,
        )
        for feature in features
    ]

    profiles: list[dict[str, Any]] = []
    grouped: dict[tuple[Any, ...], list[dict[str, Any]]] = {}
    for assertion in assertions:
        grouped.setdefault(_profile_key(assertion["properties"]), []).append(assertion)
    for key, group in sorted(grouped.items(), key=lambda item: tuple(str(value) for value in item[0])):
        from_node, to_node, s_from, s_to = key
        profiles.append(
            {
                "profile_id": f"hamburg-cross-section-{_stable_digest(key)[:20]}",
                "from_network_node": from_node,
                "to_network_node": to_node,
                "station_interval_m": [s_from, s_to],
                "strip_feature_ids": [item["feature_id"] for item in group],
                "strip_count": len(group),
                "strip_sides_raw": sorted({item["strip_side_raw"] for item in group}),
                "status": time_status,
                "classification_only": True,
                "automatic_promotion_gate": "blocked",
            }
        )

    return {
        "schema": HAMBURG_CROSS_SECTION_ADAPTER_SCHEMA,
        "status": time_status,
        "acquisition_status": "pass",
        "claim_status": time_status,
        "source_snapshot": source_snapshot,
        "source_attribution": HAMBURG_CROSS_SECTION_PROVIDER,
        "license": HAMBURG_CROSS_SECTION_LICENSE,
        "raw_feature_assertions": assertions,
        "cross_section_profiles": profiles,
        "counts": {
            "feature_count": len(assertions),
            "profile_count": len(profiles),
        },
        "blocking_reasons": blocking_reasons,
        "review_reasons": review_reasons,
        "not_applicable_claims": list(_RAW_CLAIMS_ONLY),
        "classification_only": True,
        "automatic_promotion_gate": "blocked",
        "claim_boundary": (
            "Hamburg cross-section polygons and raw strip attributes are official inventory evidence only. "
            "Raw L/R/M sides and strip kind codes do not establish traffic direction, legal movement, "
            "SUMO lane identity, lane transitions, or signal control."
        ),
    }


def _source_snapshot(
    *,
    path: Path,
    raw_bytes: bytes,
    source_sha256: str,
    request_url: str,
    request_identity: Mapping[str, Any],
    bbox: tuple[float, float, float, float],
    target_time: datetime,
    retrieved_at: datetime,
    valid_from: datetime | None,
    valid_to: datetime | None,
    server_timestamp: str | None,
    number_matched: Any,
    number_returned: Any,
) -> dict[str, Any]:
    time_status, _ = _time_alignment_status(target_time, valid_from, valid_to)
    return {
        "path": str(path),
        "bytes": len(raw_bytes),
        "sha256": source_sha256,
        "request_url": request_url,
        "normalized_request_url": _normalized_url(request_url),
        "query_identity_sha256": _stable_digest(request_identity),
        "dataset_id": HAMBURG_CROSS_SECTION_DATASET_ID,
        "collection_id": HAMBURG_CROSS_SECTION_COLLECTION_ID,
        "bbox": list(bbox),
        "bbox_crs": HAMBURG_CROSS_SECTION_CRS,
        "output_crs": HAMBURG_CROSS_SECTION_CRS,
        "target_time": target_time.isoformat(),
        "retrieved_at": retrieved_at.isoformat(),
        "valid_from": valid_from.isoformat() if valid_from else None,
        "valid_to": valid_to.isoformat() if valid_to else None,
        "validity_basis": "caller_declared" if valid_from is not None and valid_to is not None else "unknown",
        "server_timestamp": server_timestamp,
        "server_timestamp_is_validity": False,
        "time_alignment_status": time_status,
        "number_matched": number_matched,
        "number_returned": number_returned,
    }


def _blocked_report(source_snapshot: Mapping[str, Any], reasons: Sequence[str]) -> dict[str, Any]:
    return {
        "schema": HAMBURG_CROSS_SECTION_ADAPTER_SCHEMA,
        "status": "blocked",
        "acquisition_status": "blocked",
        "claim_status": "blocked",
        "source_snapshot": dict(source_snapshot),
        "source_attribution": HAMBURG_CROSS_SECTION_PROVIDER,
        "license": HAMBURG_CROSS_SECTION_LICENSE,
        "raw_feature_assertions": [],
        "cross_section_profiles": [],
        "counts": {"feature_count": 0, "profile_count": 0},
        "blocking_reasons": sorted(set(reasons)),
        "review_reasons": [],
        "not_applicable_claims": list(_RAW_CLAIMS_ONLY),
        "classification_only": True,
        "automatic_promotion_gate": "blocked",
        "claim_boundary": (
            "No topology, lane, movement, SUMO, or signal claim is authorized by this blocked acquisition."
        ),
    }


def _validate_request_url(
    request_url: str,
    bbox: tuple[float, float, float, float],
) -> tuple[list[str], dict[str, Any]]:
    parsed = urlparse(request_url)
    errors: list[str] = []
    if parsed.scheme != "https" or parsed.hostname != "api.hamburg.de":
        errors.append("official_hamburg_https_request_url_required")
    expected_path = (
        f"/datasets/v1/{HAMBURG_CROSS_SECTION_DATASET_ID}/collections/"
        f"{HAMBURG_CROSS_SECTION_COLLECTION_ID}/items"
    )
    if parsed.path.rstrip("/") != expected_path:
        errors.append("hamburg_cross_section_collection_request_url_required")

    pairs = parse_qsl(parsed.query, keep_blank_values=True)
    values: dict[str, str] = {}
    for key, value in pairs:
        if key in values:
            errors.append(f"duplicate_request_parameter:{key}")
        values[key] = value
        if key not in _QUERY_KEYS:
            errors.append(f"unsupported_request_parameter:{key}")
    if values.get("f") != "json":
        errors.append("json_response_format_required")
    if values.get("bbox-crs") != HAMBURG_CROSS_SECTION_CRS or values.get("crs") != HAMBURG_CROSS_SECTION_CRS:
        errors.append("epsg_25832_request_crs_required")
    try:
        request_bbox = tuple(float(item) for item in values.get("bbox", "").split(","))
    except ValueError:
        request_bbox = ()
    if len(request_bbox) != 4 or any(
        not math.isclose(observed, expected, rel_tol=0.0, abs_tol=1e-6)
        for observed, expected in zip(request_bbox, bbox, strict=False)
    ):
        errors.append("request_bbox_mismatch")
    try:
        limit = int(values.get("limit", ""))
    except ValueError:
        limit = 0
    if limit <= 0:
        errors.append("positive_request_limit_required")
    try:
        offset = int(values.get("offset", "0"))
    except ValueError:
        offset = -1
    if offset != 0:
        errors.append("zero_request_offset_required")

    identity = {
        "dataset_id": HAMBURG_CROSS_SECTION_DATASET_ID,
        "collection_id": HAMBURG_CROSS_SECTION_COLLECTION_ID,
        "bbox": list(bbox),
        "bbox_crs": HAMBURG_CROSS_SECTION_CRS,
        "output_crs": HAMBURG_CROSS_SECTION_CRS,
        "limit": limit,
        "offset": offset,
        "request_url": _normalized_url(request_url),
    }
    return errors, identity


def _feature_collection_errors(payload: Mapping[str, Any], request_identity: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    if payload.get("type") != "FeatureCollection":
        errors.append("feature_collection_type_required")
    features = payload.get("features")
    if not isinstance(features, list):
        errors.append("features_list_required")
        return errors
    matched = payload.get("numberMatched")
    returned = payload.get("numberReturned")
    if not _is_int(matched) or not _is_int(returned):
        errors.append("number_matched_and_returned_required")
    elif matched != returned or returned != len(features):
        errors.append("incomplete_or_inconsistent_feature_collection")
    elif matched > request_identity["limit"]:
        errors.append("request_limit_does_not_cover_matches")
    links = payload.get("links", [])
    if isinstance(links, list) and any(
        isinstance(link, Mapping) and str(link.get("rel", "")).casefold() == "next" for link in links
    ):
        errors.append("next_page_link_present")
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
    properties = raw_feature.get("properties")
    if not isinstance(properties, Mapping):
        return None, [*errors, f"feature_properties_required:{index}"]
    for field_name in _REQUIRED_PROPERTIES:
        if field_name not in properties or properties[field_name] is None:
            errors.append(f"required_property_missing:{index}:{field_name}")
    geometry = raw_feature.get("geometry")
    errors.extend(_geometry_errors(geometry, index=index))
    srs_name = raw_feature.get("srsName")
    if srs_name is not None and not _is_epsg_25832(str(srs_name)):
        errors.append(f"feature_crs_not_epsg_25832:{index}")
    if errors:
        return None, errors

    try:
        s_from = float(properties["von_station"])
        s_to = float(properties["bis_station"])
        width_from = float(properties["breite"])
        width_to = float(properties["bis_breite"])
        strip_number_value = float(properties["streifennr"])
    except (TypeError, ValueError):
        return None, [f"invalid_cross_section_numeric_value:{index}"]
    if not all(math.isfinite(value) for value in (s_from, s_to, width_from, width_to, strip_number_value)):
        return None, [f"non_finite_cross_section_numeric_value:{index}"]
    if s_from < 0 or s_to <= s_from:
        errors.append(f"invalid_station_interval:{index}")
    if width_from < 0 or width_to < 0:
        errors.append(f"negative_width:{index}")
    if (
        strip_number_value < 0
        or not strip_number_value.is_integer()
        or isinstance(properties["streifennr"], bool)
    ):
        errors.append(f"invalid_strip_number:{index}")
    side = str(properties["streifen"]).strip()
    if side not in {"L", "M", "R"}:
        errors.append(f"unsupported_strip_side:{index}:{side}")
    for field_name in ("von_netzknoten", "nach_netzknoten", "art", "art_klartext"):
        if not str(properties[field_name]).strip():
            errors.append(f"empty_required_property:{index}:{field_name}")
    if errors:
        return None, errors
    return {
        "feature_id": str(feature_id),
        "geometry": geometry,
        "properties": dict(properties),
    }, []


def _geometry_errors(geometry: Any, *, index: int) -> list[str]:
    if not isinstance(geometry, Mapping) or geometry.get("type") != "Polygon":
        return [f"polygon_geometry_required:{index}"]
    rings = geometry.get("coordinates")
    if not isinstance(rings, list) or not rings:
        return [f"polygon_coordinates_required:{index}"]
    for ring in rings:
        if not isinstance(ring, list) or len(ring) < 4:
            return [f"invalid_polygon_ring:{index}"]
        normalized_points: list[tuple[float, ...]] = []
        for point in ring:
            if not isinstance(point, list) or len(point) < 2:
                return [f"invalid_polygon_coordinate:{index}"]
            try:
                values = tuple(float(value) for value in point)
            except (TypeError, ValueError):
                return [f"invalid_polygon_coordinate:{index}"]
            if not all(math.isfinite(value) for value in values):
                return [f"non_finite_polygon_coordinate:{index}"]
            normalized_points.append(values)
        if normalized_points[0] != normalized_points[-1]:
            return [f"polygon_ring_not_closed:{index}"]
    return []


def _raw_assertion(
    feature: Mapping[str, Any],
    *,
    source_sha256: str,
    valid_from: datetime | None,
    valid_to: datetime | None,
) -> dict[str, Any]:
    properties = dict(feature["properties"])
    source_ref = RoadObjectRef(
        namespace="official.hamburg_querschnitte",
        object_type="cross_section_strip",
        object_id=str(feature["feature_id"]),
        source_sha256=source_sha256,
        valid_from=valid_from,
        valid_to=valid_to,
        provider=HAMBURG_CROSS_SECTION_PROVIDER,
        dataset=f"{HAMBURG_CROSS_SECTION_DATASET_ID}/{HAMBURG_CROSS_SECTION_COLLECTION_ID}",
        jurisdiction=HAMBURG_CROSS_SECTION_JURISDICTION,
    )
    return {
        "feature_id": str(feature["feature_id"]),
        "source_ref": source_ref.as_dict(),
        "geometry": feature["geometry"],
        "properties": properties,
        "link_key": {
            "from_network_node": str(properties["von_netzknoten"]),
            "to_network_node": str(properties["nach_netzknoten"]),
        },
        "station_interval_m": [float(properties["von_station"]), float(properties["bis_station"])],
        "strip_side_raw": str(properties["streifen"]),
        "strip_number_raw": properties["streifennr"],
        "strip_kind_code_raw": properties["art"],
        "strip_kind_label_raw": properties["art_klartext"],
        "width_from_raw": properties["breite"],
        "width_to_raw": properties["bis_breite"],
        "classification_only": True,
        "automatic_promotion_gate": "blocked",
    }


def _strip_key(properties: Mapping[str, Any]) -> tuple[Any, ...]:
    return (*_profile_key(properties), str(properties["streifen"]), int(properties["streifennr"]))


def _profile_key(properties: Mapping[str, Any]) -> tuple[Any, ...]:
    return (
        str(properties["von_netzknoten"]),
        str(properties["nach_netzknoten"]),
        float(properties["von_station"]),
        float(properties["bis_station"]),
    )


def _feature_sort_key(feature: Mapping[str, Any]) -> tuple[Any, ...]:
    return (*_strip_key(feature["properties"]), str(feature["feature_id"]))


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
    return "review_required", "source_validity_is_caller_declared"


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


def _normalized_url(value: str) -> str:
    parsed = urlparse(value)
    query = urlencode(sorted(parse_qsl(parsed.query, keep_blank_values=True)))
    return urlunparse((parsed.scheme.lower(), parsed.netloc.lower(), parsed.path.rstrip("/"), "", query, ""))


def _stable_digest(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_epsg_25832(value: str) -> bool:
    normalized = value.strip().casefold()
    return normalized in {
        "epsg:25832",
        "urn:ogc:def:crs:epsg::25832",
        HAMBURG_CROSS_SECTION_CRS.casefold(),
    }


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None
