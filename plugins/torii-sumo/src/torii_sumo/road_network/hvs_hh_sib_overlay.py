"""Exact official HVS membership overlay for a frozen HH-SIB corridor.

Hamburg's HVS collection is a classification source.  HH-SIB remains the
geometry, stationing, and lane-property source.  This module joins the two
official collections only when their complete feature identity and canonical
geometry hash agree.  It never uses OSM, Google, or a road-name-only match.
"""

from __future__ import annotations

import hashlib
import json
import math
import unicodedata
from collections import defaultdict
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from torii_sumo.core.artifact_io import write_json_atomic


HVS_HH_SIB_OVERLAY_SCHEMA = "torii.hamburg-hvs-hh-sib-exact-overlay/v1"
HVS_CLASSIFICATION_SCHEME = "de:hamburg:hvs"
_HH_SIB_PATH = "/datasets/v1/strassen_und_wegenetz/collections/strassennetz_gesamt/items"
_HVS_PATH = "/datasets/v1/hauptverkehrsstrassen/collections/hauptverkehrsstrassen/items"
_IDENTITY_FIELDS = (
    "von_netzknoten",
    "nach_netzknoten",
    "von_station",
    "bis_station",
    "strassenschluessel",
    "wegenummer",
    "ast",
    "strassenname",
    "abschnittslaenge",
)
_LINK_FIELDS = (
    "von_netzknoten",
    "nach_netzknoten",
    "strassenschluessel",
    "wegenummer",
    "ast",
    "strassenname",
    "abschnittslaenge",
)


class HvsHhSibOverlayError(ValueError):
    """Raised when caller arguments cannot name a safe overlay operation."""


def build_hamburg_hvs_hh_sib_corridor_overlay(
    *,
    hh_sib_snapshot_file: str | Path,
    hvs_snapshot_file: str | Path,
    corridor_manifest_file: str | Path,
    expected_hh_sib_sha256: str | None = None,
    expected_hvs_sha256: str | None = None,
    expected_corridor_manifest_sha256: str | None = None,
) -> dict[str, Any]:
    """Classify selected HH-SIB intervals using exact official HVS identity.

    A returned ``hvs`` decision requires one and only one HVS feature with the
    same network-node pair, station interval, road key, way number, branch,
    road name, link length, and canonical geometry SHA-256.  A confident
    ``not_hvs`` decision requires complete, co-temporal official bbox
    responses and no overlapping or geometry-identical conflicting HVS row.
    Any one-to-many, segmentation, property, or geometry conflict is an
    autonomous abstention; it is never sent to a human or resolved by a
    heuristic.
    """

    hh_path = _resolved_file(hh_sib_snapshot_file, "hh_sib_snapshot_file")
    hvs_path = _resolved_file(hvs_snapshot_file, "hvs_snapshot_file")
    manifest_path = _resolved_file(corridor_manifest_file, "corridor_manifest_file")
    if len({hh_path, hvs_path, manifest_path}) != 3:
        raise HvsHhSibOverlayError("HH-SIB, HVS, and corridor manifest files must be distinct")

    sources, source_blockers = _read_sources(
        hh_path=hh_path,
        hvs_path=hvs_path,
        manifest_path=manifest_path,
        expected_hh_sha=expected_hh_sib_sha256,
        expected_hvs_sha=expected_hvs_sha256,
        expected_manifest_sha=expected_corridor_manifest_sha256,
    )
    if source_blockers:
        return _blocked_report(sources=sources, blockers=source_blockers)

    hh_payload = sources["hh_sib"]["payload"]
    hvs_payload = sources["hvs"]["payload"]
    manifest = sources["corridor_manifest"]["payload"]
    hh_features, hh_errors = _normalize_features(hh_payload, source="hh_sib")
    hvs_features, hvs_errors = _normalize_features(hvs_payload, source="hvs")
    blockers = [*hh_errors, *hvs_errors]
    blockers.extend(_source_pair_blockers(hh_payload, hvs_payload))
    blockers.extend(
        _manifest_source_blockers(
            manifest,
            hh_sha256=str(sources["hh_sib"]["sha256"]),
        )
    )
    if blockers:
        return _blocked_report(sources=sources, blockers=blockers)

    selected, selection_blockers = _selected_hh_intervals(
        manifest=manifest,
        hh_features=hh_features,
    )
    if selection_blockers:
        return _blocked_report(sources=sources, blockers=selection_blockers)

    exact_index: dict[tuple[Any, ...], list[dict[str, Any]]] = defaultdict(list)
    link_index: dict[tuple[Any, ...], list[dict[str, Any]]] = defaultdict(list)
    geometry_index: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for feature in hvs_features:
        if feature["identity_key"] is not None:
            exact_index[feature["identity_key"]].append(feature)
        if feature["link_key"] is not None:
            link_index[feature["link_key"]].append(feature)
        geometry_index[feature["geometry_sha256"]].append(feature)

    interval_results = [
        _classify_interval(
            selected_interval,
            exact_index=exact_index,
            link_index=link_index,
            geometry_index=geometry_index,
            hh_sha256=str(sources["hh_sib"]["sha256"]),
            hvs_sha256=str(sources["hvs"]["sha256"]),
        )
        for selected_interval in selected
    ]
    link_results = _aggregate_links(interval_results)
    abstentions = [item for item in interval_results if item["decision"] == "abstain"]
    status = "blocked" if abstentions else "pass"
    counts = {
        "selected_link_count": len(link_results),
        "selected_interval_count": len(interval_results),
        "hvs_interval_count": sum(item["membership"] == "hvs" for item in interval_results),
        "not_hvs_interval_count": sum(item["membership"] == "not_hvs" for item in interval_results),
        "abstained_interval_count": len(abstentions),
        "hvs_link_count": sum(item["membership"] == "hvs" for item in link_results),
        "not_hvs_link_count": sum(item["membership"] == "not_hvs" for item in link_results),
        "partial_hvs_link_count": sum(item["membership"] == "partial_hvs" for item in link_results),
        "abstained_link_count": sum(item["membership"] == "unknown" for item in link_results),
    }
    identity = {
        "schema": HVS_HH_SIB_OVERLAY_SCHEMA,
        "hh_sib_sha256": sources["hh_sib"]["sha256"],
        "hvs_sha256": sources["hvs"]["sha256"],
        "corridor_manifest_sha256": sources["corridor_manifest"]["sha256"],
        "interval_decisions": [
            {
                "hh_sib_feature_id": item["hh_sib_feature_id"],
                "selected_station_range_m": item["selected_station_range_m"],
                "membership": item["membership"],
                "hvs_feature_ids": item["hvs_feature_ids"],
                "decision": item["decision"],
            }
            for item in interval_results
        ],
    }
    return {
        "schema": HVS_HH_SIB_OVERLAY_SCHEMA,
        "overlay_id": f"hvs-hh-sib-overlay-{_stable_digest(identity)[:24]}",
        "status": status,
        "claim_status": status,
        "decision": "exact_overlay_complete" if status == "pass" else "autonomous_abstention",
        "human_review_required": False,
        "sources": _public_sources(sources),
        "matching_contract": {
            "positive_membership_rule": "one_exact_composite_key_and_geometry_sha256_match",
            "negative_membership_rule": (
                "complete_same_bbox_same_timestamp_hvs_snapshot_and_no_exact_or_conflicting_candidate"
            ),
            "identity_fields": list(_IDENTITY_FIELDS),
            "geometry_rule": "canonical_geojson_type_and_coordinate_order_sha256",
            "name_only_match_allowed": False,
            "osm_used": False,
            "google_maps_used": False,
            "conflict_policy": "autonomous_abstention",
        },
        "intervals": interval_results,
        "links": link_results,
        "classification_assignments": [
            item["classification_assignment"]
            for item in interval_results
            if item["classification_assignment"] is not None
        ],
        "counts": counts,
        "blocking_reasons": sorted(
            {
                reason
                for item in abstentions
                for reason in item.get("blocking_reasons", [])
            }
        ),
        "automatic_overlay_application_gate": "pass" if status == "pass" else "blocked",
        "automatic_promotion_gate": "not_applicable",
        "classification_only": True,
        "mutations": [],
        "authority_boundary": {
            "geometry": "hh_sib_unchanged",
            "stationing": "hh_sib_unchanged",
            "lane_count": "hh_sib_unchanged",
            "speed": "hh_sib_unchanged",
            "hvs_effect": "hamburg_membership_classification_only",
        },
        "temporal_claim": (
            "Membership is classified only for the two complete official collections carrying the same "
            "server timestamp and bbox. The server timestamp is not reinterpreted as a validity interval."
        ),
        "claim_boundary": (
            "This report overlays official HVS collection membership onto exact HH-SIB source intervals. "
            "It does not alter road geometry, stationing, lane count, speed, movements, signal control, "
            "demand, or any SUMO artifact, and it does not authorize digital-twin promotion."
        ),
    }


def write_hamburg_hvs_hh_sib_corridor_overlay(
    output_file: str | Path,
    **build_kwargs: Any,
) -> dict[str, Any]:
    """Build and atomically write a new overlay report without overwriting."""

    destination = Path(output_file).expanduser().resolve()
    if destination.exists():
        raise FileExistsError(f"overlay output already exists: {destination}")
    report = build_hamburg_hvs_hh_sib_corridor_overlay(**build_kwargs)
    write_json_atomic(destination, report, indent=2, ensure_ascii=False, sort_keys=True)
    return report


def _read_sources(
    *,
    hh_path: Path,
    hvs_path: Path,
    manifest_path: Path,
    expected_hh_sha: str | None,
    expected_hvs_sha: str | None,
    expected_manifest_sha: str | None,
) -> tuple[dict[str, dict[str, Any]], list[str]]:
    sources: dict[str, dict[str, Any]] = {}
    blockers: list[str] = []
    for name, path, expected in (
        ("hh_sib", hh_path, expected_hh_sha),
        ("hvs", hvs_path, expected_hvs_sha),
        ("corridor_manifest", manifest_path, expected_manifest_sha),
    ):
        raw = path.read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        record: dict[str, Any] = {
            "path": str(path),
            "bytes": len(raw),
            "sha256": digest,
            "payload": None,
        }
        sources[name] = record
        if expected is not None:
            normalized_expected = _require_sha256(expected, f"expected_{name}_sha256")
            if digest != normalized_expected:
                blockers.append(f"{name}_sha256_mismatch")
                continue
        try:
            payload = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            blockers.append(f"{name}_invalid_json:{type(exc).__name__}")
            continue
        if not isinstance(payload, Mapping):
            blockers.append(f"{name}_json_object_required")
            continue
        record["payload"] = dict(payload)
    return sources, sorted(set(blockers))


def _source_pair_blockers(
    hh_payload: Mapping[str, Any],
    hvs_payload: Mapping[str, Any],
) -> list[str]:
    blockers: list[str] = []
    hh_bbox, hh_errors = _official_complete_bbox(hh_payload, expected_path=_HH_SIB_PATH, source="hh_sib")
    hvs_bbox, hvs_errors = _official_complete_bbox(hvs_payload, expected_path=_HVS_PATH, source="hvs")
    blockers.extend(hh_errors)
    blockers.extend(hvs_errors)
    if hh_bbox is not None and hvs_bbox is not None and hh_bbox != hvs_bbox:
        blockers.append("official_source_bbox_mismatch")
    hh_timestamp = hh_payload.get("timeStamp")
    hvs_timestamp = hvs_payload.get("timeStamp")
    if not isinstance(hh_timestamp, str) or not hh_timestamp:
        blockers.append("hh_sib_server_timestamp_required")
    if not isinstance(hvs_timestamp, str) or not hvs_timestamp:
        blockers.append("hvs_server_timestamp_required")
    if hh_timestamp and hvs_timestamp and hh_timestamp != hvs_timestamp:
        blockers.append("official_source_server_timestamp_mismatch")
    return blockers


def _official_complete_bbox(
    payload: Mapping[str, Any],
    *,
    expected_path: str,
    source: str,
) -> tuple[tuple[float, float, float, float] | None, list[str]]:
    blockers: list[str] = []
    features = payload.get("features")
    matched = payload.get("numberMatched")
    returned = payload.get("numberReturned")
    if payload.get("type") != "FeatureCollection" or not isinstance(features, list):
        return None, [f"{source}_complete_feature_collection_required"]
    if not isinstance(matched, int) or not isinstance(returned, int):
        blockers.append(f"{source}_matched_and_returned_counts_required")
    elif matched != returned or returned != len(features):
        blockers.append(f"{source}_incomplete_feature_collection")

    self_links = [
        item
        for item in payload.get("links", [])
        if isinstance(item, Mapping) and item.get("rel") == "self" and isinstance(item.get("href"), str)
    ]
    if len(self_links) != 1:
        return None, [*blockers, f"{source}_single_self_link_required"]
    parsed = urlparse(str(self_links[0]["href"]))
    if parsed.scheme != "https" or parsed.hostname != "api.hamburg.de" or parsed.path.rstrip("/") != expected_path:
        blockers.append(f"{source}_official_collection_self_link_required")
    query = parse_qs(parsed.query)
    raw_bbox = query.get("bbox", [])
    if len(raw_bbox) != 1:
        return None, [*blockers, f"{source}_self_link_bbox_required"]
    try:
        bbox = tuple(float(item) for item in raw_bbox[0].split(","))
    except (TypeError, ValueError):
        return None, [*blockers, f"{source}_valid_bbox_required"]
    if len(bbox) != 4 or not all(math.isfinite(item) for item in bbox):
        return None, [*blockers, f"{source}_valid_bbox_required"]
    west, south, east, north = bbox
    if not west < east or not south < north:
        blockers.append(f"{source}_ordered_bbox_required")
    return (west, south, east, north), blockers


def _manifest_source_blockers(manifest: Mapping[str, Any], *, hh_sha256: str) -> list[str]:
    blockers: list[str] = []
    source = manifest.get("source")
    if not isinstance(source, Mapping):
        return ["corridor_manifest_hh_sib_source_required"]
    if source.get("sha256") != hh_sha256:
        blockers.append("corridor_manifest_hh_sib_sha256_mismatch")
    if source.get("kind") != "hamburg_hh_sib_official_snapshot":
        blockers.append("corridor_manifest_official_hh_sib_source_kind_required")
    if not isinstance(manifest.get("selected_links"), list) or not manifest.get("selected_links"):
        blockers.append("corridor_manifest_selected_links_required")
    return blockers


def _normalize_features(
    payload: Mapping[str, Any],
    *,
    source: str,
) -> tuple[list[dict[str, Any]], list[str]]:
    normalized: list[dict[str, Any]] = []
    errors: list[str] = []
    seen_ids: set[str] = set()
    features = payload.get("features")
    if not isinstance(features, list):
        return [], [f"{source}_features_list_required"]
    for index, raw in enumerate(features):
        if not isinstance(raw, Mapping):
            errors.append(f"{source}_feature_object_required:{index}")
            continue
        feature_id = str(raw.get("id", "")).strip()
        if not feature_id:
            errors.append(f"{source}_feature_id_required:{index}")
            continue
        if feature_id in seen_ids:
            errors.append(f"{source}_duplicate_feature_id:{feature_id}")
            continue
        seen_ids.add(feature_id)
        properties = raw.get("properties")
        geometry = raw.get("geometry")
        if not isinstance(properties, Mapping):
            errors.append(f"{source}_feature_properties_required:{feature_id}")
            continue
        try:
            geometry_body = _canonical_geometry(geometry)
        except HvsHhSibOverlayError as exc:
            errors.append(f"{source}_invalid_feature:{feature_id}:{exc}")
            continue
        missing = [field for field in _IDENTITY_FIELDS if properties.get(field) is None]
        identity: dict[str, Any] | None = None
        if not missing:
            try:
                identity = _identity(properties)
            except HvsHhSibOverlayError as exc:
                errors.append(f"{source}_invalid_feature:{feature_id}:{exc}")
                continue
        geometry_sha = _stable_digest(geometry_body)
        normalized.append(
            {
                "feature_id": feature_id,
                "properties": dict(properties),
                "identity": identity,
                "identity_key": (
                    tuple(identity[field] for field in _IDENTITY_FIELDS)
                    if identity is not None
                    else None
                ),
                "link_key": (
                    tuple(identity[field] for field in _LINK_FIELDS)
                    if identity is not None
                    else None
                ),
                "identity_sha256": _stable_digest(identity) if identity is not None else None,
                "identity_complete": identity is not None,
                "missing_identity_fields": missing,
                "geometry": geometry_body,
                "geometry_sha256": geometry_sha,
                "station_from_m": float(identity["von_station"]) if identity is not None else None,
                "station_to_m": float(identity["bis_station"]) if identity is not None else None,
            }
        )
    return normalized, errors


def _selected_hh_intervals(
    *,
    manifest: Mapping[str, Any],
    hh_features: Sequence[Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], list[str]]:
    by_id = {str(item["feature_id"]): item for item in hh_features}
    selected: list[dict[str, Any]] = []
    errors: list[str] = []
    seen_feature_ids: set[str] = set()
    seen_link_ids: set[str] = set()
    for link_index, raw_link in enumerate(manifest.get("selected_links", [])):
        if not isinstance(raw_link, Mapping):
            errors.append(f"selected_link_object_required:{link_index}")
            continue
        link_id = str(raw_link.get("link_object_id", "")).strip()
        if not link_id or link_id in seen_link_ids:
            errors.append(f"selected_link_unique_id_required:{link_index}")
            continue
        seen_link_ids.add(link_id)
        raw_ids = raw_link.get("feature_ids")
        station_range = raw_link.get("selected_station_range_m")
        if not isinstance(raw_ids, list) or not raw_ids:
            errors.append(f"selected_link_feature_ids_required:{link_id}")
            continue
        if not isinstance(station_range, list) or len(station_range) != 2:
            errors.append(f"selected_link_station_range_required:{link_id}")
            continue
        try:
            selected_from, selected_to = (float(station_range[0]), float(station_range[1]))
        except (TypeError, ValueError):
            errors.append(f"selected_link_valid_station_range_required:{link_id}")
            continue
        if not math.isfinite(selected_from) or not math.isfinite(selected_to) or selected_from >= selected_to:
            errors.append(f"selected_link_valid_station_range_required:{link_id}")
            continue

        link_features: list[Mapping[str, Any]] = []
        for raw_feature_id in raw_ids:
            feature_id = str(raw_feature_id)
            if feature_id in seen_feature_ids:
                errors.append(f"selected_hh_sib_feature_reused:{feature_id}")
                continue
            seen_feature_ids.add(feature_id)
            feature = by_id.get(feature_id)
            if feature is None:
                errors.append(f"selected_hh_sib_feature_missing:{feature_id}")
                continue
            if not feature.get("identity_complete"):
                errors.append(f"selected_hh_sib_feature_identity_incomplete:{feature_id}")
                continue
            link_features.append(feature)
        if not link_features:
            continue

        expected_link_values = {
            "von_netzknoten": str(raw_link.get("from_network_node", "")),
            "nach_netzknoten": str(raw_link.get("to_network_node", "")),
            "strassenschluessel": str(raw_link.get("road_key", "")),
            "ast": _canonical_number(raw_link.get("branch_code"), "branch_code"),
            "strassenname": _canonical_text(raw_link.get("road_name"), "road_name"),
            "abschnittslaenge": _canonical_number(raw_link.get("length_m"), "length_m"),
        }
        for feature in link_features:
            identity = feature["identity"]
            if any(identity[field] != value for field, value in expected_link_values.items()):
                errors.append(f"selected_link_manifest_identity_mismatch:{link_id}:{feature['feature_id']}")

        clipped: list[tuple[float, float, Mapping[str, Any]]] = []
        for feature in link_features:
            start = max(selected_from, float(feature["station_from_m"]))
            end = min(selected_to, float(feature["station_to_m"]))
            if start < end:
                clipped.append((start, end, feature))
            else:
                errors.append(f"selected_feature_outside_station_range:{link_id}:{feature['feature_id']}")
        clipped.sort(key=lambda item: (item[0], item[1], str(item[2]["feature_id"])))
        cursor = selected_from
        for start, end, _ in clipped:
            if not math.isclose(start, cursor, abs_tol=1e-9):
                errors.append(f"selected_link_station_coverage_gap_or_overlap:{link_id}")
                break
            cursor = end
        if not math.isclose(cursor, selected_to, abs_tol=1e-9):
            errors.append(f"selected_link_station_coverage_incomplete:{link_id}")
        for start, end, feature in clipped:
            selected.append(
                {
                    **dict(feature),
                    "link_object_id": link_id,
                    "selected_station_range_m": [start, end],
                }
            )
    selected.sort(
        key=lambda item: (
            str(item["identity"]["strassenschluessel"]),
            str(item["link_object_id"]),
            item["selected_station_range_m"][0],
            str(item["feature_id"]),
        )
    )
    return selected, sorted(set(errors))


def _classify_interval(
    selected: Mapping[str, Any],
    *,
    exact_index: Mapping[tuple[Any, ...], Sequence[Mapping[str, Any]]],
    link_index: Mapping[tuple[Any, ...], Sequence[Mapping[str, Any]]],
    geometry_index: Mapping[str, Sequence[Mapping[str, Any]]],
    hh_sha256: str,
    hvs_sha256: str,
) -> dict[str, Any]:
    exact_candidates = list(exact_index.get(selected["identity_key"], ()))
    exact_geometry = [
        candidate
        for candidate in exact_candidates
        if candidate["geometry_sha256"] == selected["geometry_sha256"]
    ]
    blockers: list[str] = []
    membership: str
    decision: str
    matched: list[Mapping[str, Any]] = []
    if len(exact_candidates) == 1 and len(exact_geometry) == 1:
        membership = "hvs"
        decision = "exact_match"
        matched = exact_geometry
    elif exact_candidates:
        membership = "unknown"
        decision = "abstain"
        matched = exact_candidates
        blockers.append(
            "duplicate_exact_hvs_identity"
            if len(exact_candidates) > 1
            else "exact_identity_geometry_hash_mismatch"
        )
    else:
        overlapping = [
            candidate
            for candidate in link_index.get(selected["link_key"], ())
            if _intervals_overlap(
                float(selected["station_from_m"]),
                float(selected["station_to_m"]),
                float(candidate["station_from_m"]),
                float(candidate["station_to_m"]),
            )
        ]
        geometry_conflicts = [
            candidate
            for candidate in geometry_index.get(str(selected["geometry_sha256"]), ())
            if candidate["identity_key"] != selected["identity_key"]
        ]
        if overlapping:
            membership = "unknown"
            decision = "abstain"
            blockers.append("overlapping_hvs_segment_without_exact_station_identity")
            matched = overlapping
        elif geometry_conflicts:
            membership = "unknown"
            decision = "abstain"
            blockers.append("geometry_identical_hvs_feature_has_conflicting_identity")
            matched = geometry_conflicts
        else:
            membership = "not_hvs"
            decision = "exact_absence"

    hvs_ids = sorted({str(item["feature_id"]) for item in matched})
    assignment = None
    if membership == "hvs":
        hvs_feature = matched[0]
        assignment_identity = {
            "hh_sib_feature_id": selected["feature_id"],
            "hh_sib_sha256": hh_sha256,
            "hvs_feature_id": hvs_feature["feature_id"],
            "hvs_sha256": hvs_sha256,
            "classification_scheme": HVS_CLASSIFICATION_SCHEME,
        }
        assignment = {
            "assignment_id": f"exact-hvs-membership-{_stable_digest(assignment_identity)[:20]}",
            "target_ref": {
                "namespace": "official.hh_sib",
                "object_type": "linear_property_feature",
                "object_id": str(selected["feature_id"]),
                "source_sha256": hh_sha256,
            },
            "property_name": "hamburg_membership",
            "classification_scheme": HVS_CLASSIFICATION_SCHEME,
            "value": "hvs",
            "direction": "both",
            "evidence_refs": [
                {
                    "namespace": "official.hamburg_hvs",
                    "object_type": "hvs_feature",
                    "object_id": str(hvs_feature["feature_id"]),
                    "source_sha256": hvs_sha256,
                }
            ],
            "status": "pass",
            "mapping_basis": "exact_composite_identity_and_geometry_sha256",
            "classification_only": True,
        }
    return {
        "link_object_id": str(selected["link_object_id"]),
        "hh_sib_feature_id": str(selected["feature_id"]),
        "selected_station_range_m": list(selected["selected_station_range_m"]),
        "source_station_range_m": [selected["station_from_m"], selected["station_to_m"]],
        "road_key": str(selected["identity"]["strassenschluessel"]),
        "road_name": str(selected["identity"]["strassenname"]),
        "identity": dict(selected["identity"]),
        "identity_sha256": str(selected["identity_sha256"]),
        "geometry_sha256": str(selected["geometry_sha256"]),
        "membership": membership,
        "decision": decision,
        "status": "blocked" if decision == "abstain" else "pass",
        "human_review_required": False,
        "hvs_feature_ids": hvs_ids,
        "blocking_reasons": blockers,
        "classification_assignment": assignment,
    }


def _aggregate_links(intervals: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for interval in intervals:
        grouped[str(interval["link_object_id"])].append(interval)
    results: list[dict[str, Any]] = []
    for link_id in sorted(grouped):
        members = grouped[link_id]
        memberships = {str(item["membership"]) for item in members}
        if "unknown" in memberships:
            membership = "unknown"
            status = "blocked"
            decision = "autonomous_abstention"
        elif memberships == {"hvs"}:
            membership = "hvs"
            status = "pass"
            decision = "all_selected_intervals_hvs"
        elif memberships == {"not_hvs"}:
            membership = "not_hvs"
            status = "pass"
            decision = "all_selected_intervals_not_hvs"
        else:
            membership = "partial_hvs"
            status = "pass"
            decision = "mixed_interval_membership"
        first = members[0]
        results.append(
            {
                "link_object_id": link_id,
                "road_key": first["road_key"],
                "road_name": first["road_name"],
                "membership": membership,
                "decision": decision,
                "status": status,
                "human_review_required": False,
                "hh_sib_feature_ids": sorted(str(item["hh_sib_feature_id"]) for item in members),
                "hvs_feature_ids": sorted(
                    {
                        str(feature_id)
                        for item in members
                        for feature_id in item.get("hvs_feature_ids", [])
                    }
                ),
            }
        )
    return results


def _blocked_report(
    *,
    sources: Mapping[str, Mapping[str, Any]],
    blockers: Sequence[str],
) -> dict[str, Any]:
    return {
        "schema": HVS_HH_SIB_OVERLAY_SCHEMA,
        "overlay_id": None,
        "status": "blocked",
        "claim_status": "blocked",
        "decision": "autonomous_abstention",
        "human_review_required": False,
        "sources": _public_sources(sources),
        "matching_contract": {
            "identity_fields": list(_IDENTITY_FIELDS),
            "geometry_rule": "canonical_geojson_type_and_coordinate_order_sha256",
            "name_only_match_allowed": False,
            "osm_used": False,
            "google_maps_used": False,
            "conflict_policy": "autonomous_abstention",
        },
        "intervals": [],
        "links": [],
        "classification_assignments": [],
        "counts": {
            "selected_link_count": 0,
            "selected_interval_count": 0,
            "hvs_interval_count": 0,
            "not_hvs_interval_count": 0,
            "abstained_interval_count": 0,
            "hvs_link_count": 0,
            "not_hvs_link_count": 0,
            "partial_hvs_link_count": 0,
            "abstained_link_count": 0,
        },
        "blocking_reasons": sorted(set(blockers)),
        "automatic_overlay_application_gate": "blocked",
        "automatic_promotion_gate": "not_applicable",
        "classification_only": True,
        "mutations": [],
        "authority_boundary": {
            "geometry": "hh_sib_unchanged",
            "stationing": "hh_sib_unchanged",
            "lane_count": "hh_sib_unchanged",
            "speed": "hh_sib_unchanged",
            "hvs_effect": "hamburg_membership_classification_only",
        },
        "claim_boundary": (
            "No overlay is applied when official-source identity, completeness, synchrony, corridor provenance, "
            "or exact feature matching is unresolved. No human or heuristic fallback is requested."
        ),
    }


def _public_sources(sources: Mapping[str, Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    return {
        name: {
            "path": record.get("path"),
            "bytes": record.get("bytes"),
            "sha256": record.get("sha256"),
        }
        for name, record in sources.items()
    }


def _identity(properties: Mapping[str, Any]) -> dict[str, Any]:
    station_from = _canonical_number(properties["von_station"], "von_station")
    station_to = _canonical_number(properties["bis_station"], "bis_station")
    length = _canonical_number(properties["abschnittslaenge"], "abschnittslaenge")
    if station_from < 0 or station_to <= station_from or station_to > length:
        raise HvsHhSibOverlayError("invalid_station_interval")
    return {
        "von_netzknoten": _canonical_text(properties["von_netzknoten"], "von_netzknoten"),
        "nach_netzknoten": _canonical_text(properties["nach_netzknoten"], "nach_netzknoten"),
        "von_station": station_from,
        "bis_station": station_to,
        "strassenschluessel": _canonical_text(
            properties["strassenschluessel"],
            "strassenschluessel",
        ),
        "wegenummer": _canonical_text(properties["wegenummer"], "wegenummer"),
        "ast": _canonical_number(properties["ast"], "ast"),
        "strassenname": _canonical_text(properties["strassenname"], "strassenname"),
        "abschnittslaenge": length,
    }


def _canonical_geometry(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise HvsHhSibOverlayError("geometry_object_required")
    geometry_type = value.get("type")
    if geometry_type not in {"LineString", "MultiLineString"}:
        raise HvsHhSibOverlayError("linear_geometry_required")
    coordinates = value.get("coordinates")
    if not isinstance(coordinates, list) or not coordinates:
        raise HvsHhSibOverlayError("geometry_coordinates_required")

    def normalize(node: Any) -> Any:
        if isinstance(node, list):
            if not node:
                raise HvsHhSibOverlayError("empty_geometry_coordinate_sequence")
            return [normalize(item) for item in node]
        try:
            number = float(node)
        except (TypeError, ValueError) as exc:
            raise HvsHhSibOverlayError("numeric_geometry_coordinate_required") from exc
        if not math.isfinite(number):
            raise HvsHhSibOverlayError("finite_geometry_coordinate_required")
        return 0.0 if number == 0 else number

    return {"type": str(geometry_type), "coordinates": normalize(coordinates)}


def _canonical_text(value: Any, field_name: str) -> str:
    if value is None:
        raise HvsHhSibOverlayError(f"{field_name}_required")
    text = unicodedata.normalize("NFC", str(value)).strip()
    if not text:
        raise HvsHhSibOverlayError(f"{field_name}_required")
    return text


def _canonical_number(value: Any, field_name: str) -> int | float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise HvsHhSibOverlayError(f"{field_name}_numeric_required") from exc
    if not math.isfinite(number):
        raise HvsHhSibOverlayError(f"{field_name}_finite_required")
    if number.is_integer():
        return int(number)
    return number


def _intervals_overlap(a0: float, a1: float, b0: float, b1: float) -> bool:
    return max(a0, b0) < min(a1, b1)


def _stable_digest(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _require_sha256(value: str, field_name: str) -> str:
    normalized = str(value).strip().lower()
    if len(normalized) != 64 or any(character not in "0123456789abcdef" for character in normalized):
        raise HvsHhSibOverlayError(f"{field_name} must be a SHA-256 digest")
    return normalized


def _resolved_file(value: str | Path, field_name: str) -> Path:
    path = Path(value).expanduser().resolve()
    if not path.is_file():
        raise HvsHhSibOverlayError(f"{field_name} must be an existing file: {path}")
    return path
