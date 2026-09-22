"""Read-only adapter for Hamburg's HH-SIB road-inventory OGC API snapshot."""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from ..contracts import RoadObjectRef, RoadPropertyAssignment


HH_SIB_ADAPTER_SCHEMA = "torii.hamburg-hh-sib-road-inventory/v1"
HH_SIB_DATASET_ID = "strassen_und_wegenetz"
HH_SIB_COLLECTION_ID = "strassennetz_gesamt"
HH_SIB_PROVIDER = "Freie und Hansestadt Hamburg"
HH_SIB_JURISDICTION = "DE-HH"
HH_SIB_CLASSIFICATION_SCHEME = "de:hamburg:hh-sib:strassennetz_gesamt"
HH_SIB_LICENSE = "Datenlizenz Deutschland Namensnennung 2.0"

_REQUIRED_PROPERTIES = (
    "von_netzknoten",
    "nach_netzknoten",
    "von_station",
    "bis_station",
    "klasse",
    "wegenummer",
    "strassenname",
    "landesschluessel",
    "kreisschluessel",
    "gemeindeschluessel",
    "abschnittslaenge",
    "ast",
    "fahrstreifenanzahl_in_stationierungsrichtung",
    "fahrstreifenanzahl_in_beide_richtungen",
    "fahrstreifenanzahl_gegen_stationierungsrichtung",
    "bahnigkeit",
    "wegeart",
)
_LINK_PROPERTY_FIELDS = (
    ("hh_sib_class_raw", "klasse"),
    ("road_key", "strassenschluessel"),
    ("way_number", "wegenummer"),
    ("road_name", "strassenname"),
    ("road_kind_raw", "wegeart"),
    ("state_key", "landesschluessel"),
    ("district_key", "kreisschluessel"),
    ("municipality_key", "gemeindeschluessel"),
    ("branch_code", "ast"),
    ("section_length_m", "abschnittslaenge"),
)
_INTERVAL_PROPERTY_FIELDS = (
    ("lane_count_with_stationing", "fahrstreifenanzahl_in_stationierungsrichtung"),
    ("lane_count_against_stationing", "fahrstreifenanzahl_gegen_stationierungsrichtung"),
    ("lane_count_both_directions", "fahrstreifenanzahl_in_beide_richtungen"),
    ("carriageway_code_raw", "bahnigkeit"),
    ("speed_rule_raw", "geschwindigkeit"),
)


def read_hamburg_hh_sib_snapshot(
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
    """Parse a frozen official response into raw and typed road assertions.

    ``timeStamp`` in the OGC response is retained as a server-response time and
    is never treated as asset validity.  Only the explicitly supplied validity
    interval can make a target-time claim pass.
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

    raw_features = list(payload["features"])
    feature_errors: list[str] = []
    normalized_features: list[dict[str, Any]] = []
    seen_feature_ids: set[str] = set()
    for index, raw_feature in enumerate(raw_features):
        normalized, errors = _normalize_feature(raw_feature, index=index)
        feature_errors.extend(errors)
        if normalized is not None:
            feature_id = str(normalized["feature_id"])
            if feature_id in seen_feature_ids:
                feature_errors.append(f"duplicate_feature_id:{feature_id}")
            else:
                seen_feature_ids.add(feature_id)
                normalized_features.append(normalized)
    if feature_errors:
        return _blocked_empty_report(source_snapshot, blocking_reasons=feature_errors)

    normalized_features.sort(key=_feature_sort_key)
    groups: dict[tuple[Any, ...], list[dict[str, Any]]] = {}
    for feature in normalized_features:
        groups.setdefault(_link_group_key(feature), []).append(feature)

    time_status, time_reason = _time_alignment_status(target_time, valid_from, valid_to)
    assignment_status = time_status
    raw_feature_assertions = [
        _raw_feature_assertion(
            feature,
            source_sha256=actual_sha256,
            valid_from=valid_from,
            valid_to=valid_to,
        )
        for feature in normalized_features
    ]
    raw_ref_by_id = {item["feature_id"]: _road_ref_from_dict(item["source_ref"]) for item in raw_feature_assertions}

    road_link_assertions: list[dict[str, Any]] = []
    assignments: list[RoadPropertyAssignment] = []
    total_gaps = 0
    total_overlaps = 0
    review_reasons: list[str] = []
    blocking_reasons: list[str] = []
    if time_status == "review_required":
        review_reasons.append(time_reason)
    elif time_status == "blocked":
        blocking_reasons.append(time_reason)

    for group_key, features in sorted(groups.items(), key=lambda item: tuple(str(value) for value in item[0])):
        features.sort(key=_feature_sort_key)
        first_feature = features[0]
        first = first_feature["properties"]
        link_identity = _link_identity(first_feature)
        link_object_id = _link_object_id(first_feature)
        link_ref = RoadObjectRef(
            namespace="official.hh_sib",
            object_type="road_link_assertion",
            object_id=link_object_id,
            source_sha256=actual_sha256,
            valid_from=valid_from,
            valid_to=valid_to,
            provider=HH_SIB_PROVIDER,
            dataset=f"{HH_SIB_DATASET_ID}/{HH_SIB_COLLECTION_ID}",
            jurisdiction=HH_SIB_JURISDICTION,
        )
        coverage = _station_coverage(features, float(first["abschnittslaenge"]))
        total_gaps += coverage["gap_count"]
        total_overlaps += coverage["overlap_count"]
        if coverage["status"] != "pass":
            review_reasons.append(f"non_contiguous_stationing:{link_object_id}")
        link_conflicts = _link_property_conflicts(features)
        if link_conflicts:
            review_reasons.extend(
                f"conflicting_link_property:{link_object_id}:{field_name}" for field_name in link_conflicts
            )
        if link_identity["status"] != "pass":
            review_reasons.append(f"road_link_identity_unresolved:{link_object_id}")
        transport_role = _transport_role(first)
        feature_refs = tuple(raw_ref_by_id[feature["feature_id"]] for feature in features)
        road_link_assertions.append(
            {
                "source_ref": link_ref.as_dict(),
                "from_network_node": str(first["von_netzknoten"]),
                "to_network_node": str(first["nach_netzknoten"]),
                "branch_code": first["ast"],
                "road_key": link_identity["road_key"],
                "road_key_status": link_identity["road_key_status"],
                "road_link_identity_status": link_identity["status"],
                "road_link_identity_basis": link_identity["basis"],
                "way_number": str(first["wegenummer"]),
                "road_name": str(first["strassenname"]),
                "length_m": float(first["abschnittslaenge"]),
                "hh_sib_class_raw": str(first["klasse"]),
                "road_kind_raw": str(first["wegeart"]),
                "derived_transport_role": transport_role,
                "transport_role_status": "rule_derived",
                "feature_refs": [ref.as_dict() for ref in feature_refs],
                "station_coverage": coverage,
                "link_property_conflicts": link_conflicts,
                "status": _combined_status(
                    assignment_status,
                    coverage["status"],
                    bool(link_conflicts),
                    link_identity["status"],
                ),
                "classification_only": True,
                "automatic_promotion_gate": "blocked",
            }
        )
        for property_name, source_field in _LINK_PROPERTY_FIELDS:
            if source_field not in first or first[source_field] is None:
                continue
            assignments.append(
                RoadPropertyAssignment(
                    assignment_id=_assignment_id(link_object_id, property_name, None, None),
                    target_ref=link_ref,
                    property_name=property_name,
                    classification_scheme=HH_SIB_CLASSIFICATION_SCHEME,
                    value=first[source_field],
                    direction="both",
                    evidence_refs=feature_refs,
                    status=assignment_status,
                    reason="direct HH-SIB link property; raw code retained without HVS/RIN/OSM/SUMO inference",
                )
            )
        for feature in features:
            properties = feature["properties"]
            feature_ref = raw_ref_by_id[feature["feature_id"]]
            s_from = float(properties["von_station"])
            s_to = float(properties["bis_station"])
            for property_name, source_field in _INTERVAL_PROPERTY_FIELDS:
                if source_field not in properties:
                    continue
                assignments.append(
                    RoadPropertyAssignment(
                        assignment_id=_assignment_id(link_object_id, property_name, s_from, s_to),
                        target_ref=link_ref,
                        property_name=property_name,
                        classification_scheme=HH_SIB_CLASSIFICATION_SCHEME,
                        value=properties[source_field],
                        direction="both",
                        evidence_refs=(feature_ref,),
                        status=assignment_status,
                        s_from_m=s_from,
                        s_to_m=s_to,
                        reason="direct HH-SIB linear property; stationing direction is preserved in the property name",
                    )
                )

    if blocking_reasons:
        claim_status = "blocked"
    elif review_reasons:
        claim_status = "review_required"
    else:
        claim_status = "pass"
    motor_links = [item for item in road_link_assertions if item["derived_transport_role"] == "motor_vehicle"]
    pedestrian_links = [item for item in road_link_assertions if item["derived_transport_role"] == "pedestrian"]
    unknown_links = [item for item in road_link_assertions if item["derived_transport_role"] == "unknown"]
    if unknown_links and claim_status == "pass":
        claim_status = "review_required"
        review_reasons.append("unknown_transport_role_present")

    return {
        "schema": HH_SIB_ADAPTER_SCHEMA,
        "status": claim_status,
        "acquisition_status": "pass",
        "claim_status": claim_status,
        "source_snapshot": source_snapshot,
        "source_attribution": HH_SIB_PROVIDER,
        "license": HH_SIB_LICENSE,
        "raw_feature_assertions": raw_feature_assertions,
        "road_link_assertions": road_link_assertions,
        "motor_road_link_assertions": motor_links,
        "pedestrian_road_link_assertions": pedestrian_links,
        "unknown_transport_road_link_assertions": unknown_links,
        "property_assignments": [assignment.as_dict() for assignment in assignments],
        "counts": {
            "feature_count": len(normalized_features),
            "road_link_assertion_count": len(road_link_assertions),
            "motor_road_link_assertion_count": len(motor_links),
            "pedestrian_road_link_assertion_count": len(pedestrian_links),
            "property_assignment_count": len(assignments),
            "gap_count": total_gaps,
            "overlap_count": total_overlaps,
        },
        "blocking_reasons": sorted(set(blocking_reasons)),
        "review_reasons": sorted(set(review_reasons)),
        "not_applicable_claims": [
            "physical_intersection_core",
            "legal_lane_movement",
            "stop_line",
            "traffic_signal_controller",
            "sumo_junction_or_connection",
        ],
        "classification_only": True,
        "automatic_promotion_gate": "blocked",
        "claim_boundary": (
            "HH-SIB Netzknoten/Abschnitt/Ast and linear properties are official road-inventory evidence. "
            "They are not OSM nodes, physical conflict cores, SUMO junctions, legal movements, stop lines, "
            "or signal-controller bindings. Lane and carriageway fields are channelization-candidate evidence only."
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
        "schema": HH_SIB_ADAPTER_SCHEMA,
        "request_url": _normalized_url(request_url),
        "bbox": list(bbox),
        "dataset_id": HH_SIB_DATASET_ID,
        "collection_id": HH_SIB_COLLECTION_ID,
    }
    return {
        "path": str(path),
        "bytes": len(raw_bytes),
        "sha256": sha256,
        "request_url": request_url,
        "normalized_request_url": query_identity["request_url"],
        "query_identity_sha256": _stable_digest(query_identity),
        "dataset_id": HH_SIB_DATASET_ID,
        "collection_id": HH_SIB_COLLECTION_ID,
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
    return {
        "schema": HH_SIB_ADAPTER_SCHEMA,
        "status": "blocked",
        "acquisition_status": "blocked",
        "claim_status": "blocked",
        "source_snapshot": dict(source_snapshot),
        "source_attribution": HH_SIB_PROVIDER,
        "license": HH_SIB_LICENSE,
        "raw_feature_assertions": [],
        "road_link_assertions": [],
        "motor_road_link_assertions": [],
        "pedestrian_road_link_assertions": [],
        "unknown_transport_road_link_assertions": [],
        "property_assignments": [],
        "counts": {
            "feature_count": 0,
            "road_link_assertion_count": 0,
            "motor_road_link_assertion_count": 0,
            "pedestrian_road_link_assertion_count": 0,
            "property_assignment_count": 0,
            "gap_count": 0,
            "overlap_count": 0,
        },
        "blocking_reasons": sorted(set(blocking_reasons)),
        "review_reasons": [],
        "classification_only": True,
        "automatic_promotion_gate": "blocked",
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


def _normalize_feature(
    raw_feature: Any,
    *,
    index: int,
) -> tuple[dict[str, Any] | None, list[str]]:
    errors: list[str] = []
    if not isinstance(raw_feature, Mapping):
        return None, [f"feature_not_object:{index}"]
    feature_id = raw_feature.get("id")
    if feature_id is None or str(feature_id).strip() == "":
        errors.append(f"feature_id_required:{index}")
    properties = raw_feature.get("properties")
    if not isinstance(properties, Mapping):
        errors.append(f"feature_properties_required:{index}")
        return None, errors
    for field_name in _REQUIRED_PROPERTIES:
        if field_name not in properties or properties[field_name] is None:
            errors.append(f"required_property_missing:{index}:{field_name}")
    if errors:
        return None, errors
    try:
        s_from = float(properties["von_station"])
        s_to = float(properties["bis_station"])
        length = float(properties["abschnittslaenge"])
    except (TypeError, ValueError):
        return None, [f"invalid_station_or_length:{index}"]
    if not all(math.isfinite(value) for value in (s_from, s_to, length)):
        return None, [f"non_finite_station_or_length:{index}"]
    if s_from < 0 or s_to <= s_from or length <= 0 or s_to > length:
        return None, [f"invalid_station_interval:{index}"]
    return (
        {
            "type": str(raw_feature.get("type", "Feature")),
            "feature_id": str(feature_id),
            "geometry": raw_feature.get("geometry"),
            "properties": dict(properties),
        },
        [],
    )


def _raw_feature_assertion(
    feature: Mapping[str, Any],
    *,
    source_sha256: str,
    valid_from: datetime | None,
    valid_to: datetime | None,
) -> dict[str, Any]:
    feature_ref = RoadObjectRef(
        namespace="official.hh_sib",
        object_type="linear_property_feature",
        object_id=str(feature["feature_id"]),
        source_sha256=source_sha256,
        valid_from=valid_from,
        valid_to=valid_to,
        provider=HH_SIB_PROVIDER,
        dataset=f"{HH_SIB_DATASET_ID}/{HH_SIB_COLLECTION_ID}",
        jurisdiction=HH_SIB_JURISDICTION,
    )
    properties = dict(feature["properties"])
    return {
        "feature_id": str(feature["feature_id"]),
        "source_ref": feature_ref.as_dict(),
        "geometry": feature.get("geometry"),
        "properties": properties,
        "derived_transport_role": _transport_role(properties),
        "transport_role_status": "rule_derived",
        "classification_only": True,
    }


def _station_coverage(features: Sequence[Mapping[str, Any]], length_m: float) -> dict[str, Any]:
    intervals = sorted(
        (
            float(feature["properties"]["von_station"]),
            float(feature["properties"]["bis_station"]),
        )
        for feature in features
    )
    gap_count = 0
    overlap_count = 0
    cursor = 0.0
    for start, end in intervals:
        if start > cursor:
            gap_count += 1
        elif start < cursor:
            overlap_count += 1
        cursor = max(cursor, end)
    if cursor < length_m:
        gap_count += 1
    return {
        "begin_m": intervals[0][0],
        "end_m": intervals[-1][1],
        "gap_count": gap_count,
        "overlap_count": overlap_count,
        "status": "pass" if gap_count == 0 and overlap_count == 0 else "review_required",
    }


def _transport_role(properties: Mapping[str, Any]) -> str:
    road_kind = str(properties.get("wegeart", "") or "").casefold()
    if "fußweg" in road_kind or "fussweg" in road_kind:
        return "pedestrian"
    lane_total = sum(
        _non_negative_int(properties.get(field_name))
        for field_name in (
            "fahrstreifenanzahl_in_stationierungsrichtung",
            "fahrstreifenanzahl_gegen_stationierungsrichtung",
            "fahrstreifenanzahl_in_beide_richtungen",
        )
    )
    if "stadtstraße" in road_kind or lane_total > 0:
        return "motor_vehicle"
    return "unknown"


def _non_negative_int(value: Any) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def _link_group_key(feature: Mapping[str, Any]) -> tuple[Any, ...]:
    """Return a grouping key without inventing a road key when it is absent.

    ``strassenschluessel`` is absent from valid current HH-SIB rows.  Such a
    row is retained, but it cannot safely be combined with another row merely
    because their remaining descriptive fields happen to agree.  Its frozen
    feature id is therefore used only as a local non-merge key, never exposed
    as a replacement road key.
    """

    properties = feature["properties"]
    road_key = _optional_text(properties.get("strassenschluessel"))
    if road_key is None:
        return ("feature_local_missing_road_key", str(feature["feature_id"]))
    return (
        "official_road_key",
        str(properties["von_netzknoten"]),
        str(properties["nach_netzknoten"]),
        road_key,
        str(properties["wegenummer"]),
        properties["ast"],
        properties["abschnittslaenge"],
    )


def _link_object_id(feature: Mapping[str, Any]) -> str:
    properties = feature["properties"]
    road_key = _optional_text(properties.get("strassenschluessel"))
    if road_key is None:
        return f"{HH_SIB_COLLECTION_ID}:unresolved-road-key:feature-{feature['feature_id']}"
    return ":".join(
        (
            HH_SIB_COLLECTION_ID,
            str(properties["von_netzknoten"]),
            str(properties["nach_netzknoten"]),
            road_key,
            str(properties["wegenummer"]),
            f"ast-{properties['ast']}",
            f"len-{properties['abschnittslaenge']}",
        )
    )


def _link_identity(feature: Mapping[str, Any]) -> dict[str, str | None]:
    """Describe whether an HH-SIB row has an authoritative link identity."""

    road_key = _optional_text(feature["properties"].get("strassenschluessel"))
    if road_key is None:
        return {
            "road_key": None,
            "road_key_status": "missing_in_source",
            "status": "blocked",
            "basis": "feature_local_only_missing_strassenschluessel",
        }
    return {
        "road_key": road_key,
        "road_key_status": "present",
        "status": "pass",
        "basis": "hh_sib_network_nodes_and_strassenschluessel",
    }


def _feature_sort_key(feature: Mapping[str, Any]) -> tuple[Any, ...]:
    properties = feature["properties"]
    return (
        str(properties["von_netzknoten"]),
        str(properties["nach_netzknoten"]),
        float(properties["von_station"]),
        float(properties["bis_station"]),
        str(feature["feature_id"]),
    )


def _link_property_conflicts(features: Sequence[Mapping[str, Any]]) -> list[str]:
    conflicts: list[str] = []
    for _, source_field in _LINK_PROPERTY_FIELDS:
        values = {
            json.dumps(feature["properties"].get(source_field), ensure_ascii=False, sort_keys=True)
            for feature in features
        }
        if len(values) > 1:
            conflicts.append(source_field)
    return conflicts


def _combined_status(
    time_status: str,
    coverage_status: str,
    has_conflicts: bool,
    identity_status: str,
) -> str:
    if time_status == "blocked" or identity_status == "blocked":
        return "blocked"
    if time_status == "review_required" or coverage_status != "pass" or has_conflicts:
        return "review_required"
    return "pass"


def _assignment_id(
    link_object_id: str,
    property_name: str,
    s_from_m: float | None,
    s_to_m: float | None,
) -> str:
    identity = {
        "link_object_id": link_object_id,
        "property_name": property_name,
        "s_from_m": s_from_m,
        "s_to_m": s_to_m,
        "scheme": HH_SIB_CLASSIFICATION_SCHEME,
    }
    return f"hh-sib-property-{_stable_digest(identity)[:20]}"


def _road_ref_from_dict(value: Mapping[str, Any]) -> RoadObjectRef:
    return RoadObjectRef(
        namespace=str(value["namespace"]),
        object_type=str(value["object_type"]),
        object_id=str(value["object_id"]),
        source_sha256=str(value["source_sha256"]),
        valid_from=_parse_optional_datetime(value.get("valid_from")),
        valid_to=_parse_optional_datetime(value.get("valid_to")),
        provider=str(value.get("provider", "")),
        dataset=str(value.get("dataset", "")),
        edition=str(value.get("edition", "")),
        jurisdiction=str(value.get("jurisdiction", "")),
    )


def _parse_optional_datetime(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    return datetime.fromisoformat(str(value))


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
    expected_path = f"/datasets/v1/{HH_SIB_DATASET_ID}/collections/{HH_SIB_COLLECTION_ID}/items"
    if parsed.path.rstrip("/") != expected_path:
        errors.append("hh_sib_collection_request_url_required")
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


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None
