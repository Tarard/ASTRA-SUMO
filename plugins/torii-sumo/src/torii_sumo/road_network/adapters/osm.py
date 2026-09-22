"""Read-only OSM road assertions for source-neutral road conflation.

The adapter preserves every ``highway=*`` way and its raw tags.  Derived modal
roles are indexing aids only; they do not promote OSM classifications into an
official road inventory or authorize network edits.
"""

from __future__ import annotations

import gzip
import hashlib
import math
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping
from xml.etree import ElementTree as ET

from torii_sumo.road_network.contracts import ROAD_NETWORK_SCHEMA, RoadObjectRef
from torii_sumo.road_semantics import filtered_osm_modes, is_osm_passenger_way


OSM_ADAPTER_SCHEMA = f"{ROAD_NETWORK_SCHEMA}/osm-source-snapshot/v1"
OSM_PROVIDER = "OpenStreetMap contributors"
OSM_DATASET = "OpenStreetMap XML"

_PEDESTRIAN_HIGHWAYS = frozenset({"footway", "pedestrian", "steps", "path"})
_BICYCLE_HIGHWAYS = frozenset({"cycleway"})


def read_osm_road_snapshot(
    path: str | Path,
    *,
    target_time: datetime,
    retrieved_at: datetime,
    valid_from: datetime | None = None,
    valid_to: datetime | None = None,
    expected_sha256: str | None = None,
    derived_from_source_sha256: str | None = None,
    derivation_kind: str = "unknown",
) -> dict[str, Any]:
    """Read exact OSM bytes into provenance-bound, non-mutating assertions."""

    source_path = Path(path)
    _require_aware(target_time, "target_time")
    _require_aware(retrieved_at, "retrieved_at")
    _validate_optional_time(valid_from, "valid_from")
    _validate_optional_time(valid_to, "valid_to")
    if derived_from_source_sha256 is not None and not re.fullmatch(r"[0-9a-fA-F]{64}", derived_from_source_sha256):
        raise ValueError("derived_from_source_sha256 must be a SHA-256 hex digest")
    derived_from_source_sha256 = derived_from_source_sha256.casefold() if derived_from_source_sha256 else None
    derivation_kind = str(derivation_kind).strip() or "unknown"

    raw_bytes = source_path.read_bytes()
    actual_sha256 = hashlib.sha256(raw_bytes).hexdigest()
    time_status, time_reason = _time_alignment_status(target_time, valid_from, valid_to)
    source_snapshot = {
        "path": str(source_path.resolve()),
        "bytes": len(raw_bytes),
        "sha256": actual_sha256,
        "target_time": target_time.isoformat(),
        "retrieved_at": retrieved_at.isoformat(),
        "valid_from": valid_from.isoformat() if valid_from else None,
        "valid_to": valid_to.isoformat() if valid_to else None,
        "validity_basis": "caller_declared" if valid_from is not None and valid_to is not None else "unknown",
        "time_alignment_status": time_status,
        "derived_from_source_sha256": derived_from_source_sha256,
        "derivation_kind": derivation_kind,
    }
    blocking_reasons: list[str] = []
    review_reasons: list[str] = []
    if expected_sha256 is not None and expected_sha256.casefold() != actual_sha256:
        blocking_reasons.append("source_sha256_mismatch")
    if time_status == "blocked":
        blocking_reasons.append(time_reason)
    elif time_status == "review_required":
        review_reasons.append(time_reason)
    if blocking_reasons:
        return _empty_report(source_snapshot, blocking_reasons=blocking_reasons)

    try:
        xml_bytes = gzip.decompress(raw_bytes) if _is_gzip(source_path, raw_bytes) else raw_bytes
        root = ET.fromstring(xml_bytes)
    except (EOFError, OSError, ET.ParseError, ValueError) as exc:
        return _empty_report(
            source_snapshot,
            blocking_reasons=(f"invalid_osm_xml:{type(exc).__name__}",),
        )
    if _local_name(root.tag) != "osm":
        return _empty_report(source_snapshot, blocking_reasons=("osm_root_required",))

    nodes: dict[str, tuple[float, float]] = {}
    duplicate_node_ids: set[str] = set()
    for element in root:
        if _local_name(element.tag) != "node":
            continue
        node_id = str(element.attrib.get("id", "")).strip()
        if not node_id:
            blocking_reasons.append("node_id_required")
            continue
        if node_id in nodes:
            duplicate_node_ids.add(node_id)
            continue
        try:
            lon = float(element.attrib["lon"])
            lat = float(element.attrib["lat"])
        except (KeyError, TypeError, ValueError):
            blocking_reasons.append(f"invalid_node_coordinate:{node_id}")
            continue
        if not math.isfinite(lon) or not math.isfinite(lat) or not -180 <= lon <= 180 or not -90 <= lat <= 90:
            blocking_reasons.append(f"invalid_node_coordinate:{node_id}")
            continue
        nodes[node_id] = (lon, lat)
    blocking_reasons.extend(f"duplicate_node_id:{node_id}" for node_id in sorted(duplicate_node_ids))

    way_elements = [element for element in root if _local_name(element.tag) == "way"]
    seen_way_ids: set[str] = set()
    highway_records: list[tuple[str, list[str], dict[str, str]]] = []
    for element in way_elements:
        way_id = str(element.attrib.get("id", "")).strip()
        if not way_id:
            blocking_reasons.append("way_id_required")
            continue
        if way_id in seen_way_ids:
            blocking_reasons.append(f"duplicate_way_id:{way_id}")
            continue
        seen_way_ids.add(way_id)
        node_refs = [str(child.attrib.get("ref", "")).strip() for child in element if _local_name(child.tag) == "nd"]
        tags = {
            str(child.attrib.get("k", "")): str(child.attrib.get("v", ""))
            for child in element
            if _local_name(child.tag) == "tag" and child.attrib.get("k")
        }
        if "highway" not in tags:
            continue
        if len(node_refs) < 2 or any(not ref for ref in node_refs):
            blocking_reasons.append(f"highway_way_geometry_required:{way_id}")
            continue
        missing = sorted({ref for ref in node_refs if ref not in nodes})
        blocking_reasons.extend(f"missing_node_ref:{way_id}:{ref}" for ref in missing)
        highway_records.append((way_id, node_refs, tags))

    if blocking_reasons:
        return _empty_report(source_snapshot, blocking_reasons=blocking_reasons)

    way_assertions: list[dict[str, Any]] = []
    role_counts = {"motor_vehicle": 0, "pedestrian": 0, "bicycle": 0, "unknown": 0}
    for way_id, node_refs, tags in highway_records:
        geometry = [list(nodes[ref]) for ref in node_refs]
        roles = _derived_mode_roles(tags)
        for role in roles or ("unknown",):
            role_counts[role] += 1
        directionality, oneway_direction, directionality_status, directionality_basis = _directionality(tags)
        source_ref = RoadObjectRef(
            namespace="osm",
            object_type="way",
            object_id=way_id,
            source_sha256=actual_sha256,
            valid_from=valid_from,
            valid_to=valid_to,
            provider=OSM_PROVIDER,
            dataset=OSM_DATASET,
        )
        way_assertions.append(
            {
                "source_ref": source_ref.as_dict(),
                "way_id": way_id,
                "node_refs": node_refs,
                "tags": dict(sorted(tags.items())),
                "geometry_lonlat": geometry,
                "geometry_role": "area_boundary" if str(tags.get("area", "")).casefold() == "yes" else "linear",
                "length_m": _polyline_length_m(geometry),
                "directionality": directionality,
                "oneway_direction": oneway_direction,
                "directionality_status": directionality_status,
                "directionality_basis": directionality_basis,
                "derived_mode_roles": list(roles),
                "mode_role_status": "rule_derived",
                "classification_only": True,
                "automatic_promotion_gate": "blocked",
            }
        )

    status = "review_required" if review_reasons else "pass"
    return {
        "schema": OSM_ADAPTER_SCHEMA,
        "status": status,
        "acquisition_status": "pass",
        "claim_status": status,
        "source_snapshot": source_snapshot,
        "source_attribution": OSM_PROVIDER,
        "derived_from_source_sha256": derived_from_source_sha256,
        "derivation_kind": derivation_kind,
        "way_assertions": sorted(way_assertions, key=lambda item: item["way_id"]),
        "counts": {
            "node_count": len(nodes),
            "way_count": len(way_elements),
            "highway_way_count": len(way_assertions),
            "motor_vehicle_way_count": role_counts["motor_vehicle"],
            "pedestrian_way_count": role_counts["pedestrian"],
            "bicycle_way_count": role_counts["bicycle"],
            "unknown_mode_way_count": role_counts["unknown"],
        },
        "blocking_reasons": [],
        "review_reasons": sorted(set(review_reasons)),
        "classification_only": True,
        "automatic_promotion_gate": "blocked",
        "claim_boundary": (
            "OSM geometry, tags, and way direction are source assertions. Derived modal roles are indexing aids, "
            "not official road classes, legal movement evidence, or permission to modify a SUMO network."
        ),
    }


def _empty_report(
    source_snapshot: Mapping[str, Any],
    *,
    blocking_reasons: tuple[str, ...] | list[str],
) -> dict[str, Any]:
    return {
        "schema": OSM_ADAPTER_SCHEMA,
        "status": "blocked",
        "acquisition_status": "blocked",
        "claim_status": "blocked",
        "source_snapshot": dict(source_snapshot),
        "source_attribution": OSM_PROVIDER,
        "way_assertions": [],
        "counts": {
            "node_count": 0,
            "way_count": 0,
            "highway_way_count": 0,
            "motor_vehicle_way_count": 0,
            "pedestrian_way_count": 0,
            "bicycle_way_count": 0,
            "unknown_mode_way_count": 0,
        },
        "blocking_reasons": sorted(set(blocking_reasons)),
        "review_reasons": [],
        "classification_only": True,
        "automatic_promotion_gate": "blocked",
    }


def _derived_mode_roles(tags: Mapping[str, str]) -> tuple[str, ...]:
    roles: list[str] = []
    highway = str(tags.get("highway", "")).strip().casefold()
    if is_osm_passenger_way(tags):
        roles.append("motor_vehicle")
    if highway in _PEDESTRIAN_HIGHWAYS and "pedestrian" in filtered_osm_modes(tags, {"pedestrian"}):
        roles.append("pedestrian")
    bicycle = str(tags.get("bicycle", "")).strip().casefold()
    if highway in _BICYCLE_HIGHWAYS or bicycle in {"yes", "designated", "permissive"}:
        if "bicycle" in filtered_osm_modes(tags, {"bicycle"}):
            roles.append("bicycle")
    return tuple(roles)


def _directionality(tags: Mapping[str, str]) -> tuple[str, str, str, str]:
    oneway = str(tags.get("oneway", "")).strip().casefold()
    if oneway == "-1":
        return "one_way", "against", "observed_tag", "oneway=-1"
    if oneway in {"yes", "true", "1"}:
        return "one_way", "with", "observed_tag", f"oneway={oneway}"
    if oneway in {"no", "false", "0"}:
        return "bidirectional", "both", "observed_tag", f"oneway={oneway}"
    if oneway:
        return "unknown", "unknown", "review_required", f"unsupported_oneway_value:{oneway}"
    if str(tags.get("junction", "")).strip().casefold() == "roundabout":
        return "one_way", "with", "rule_derived", "junction=roundabout implicit oneway"
    if str(tags.get("highway", "")).strip().casefold() in {"motorway", "motorway_link"}:
        return "one_way", "with", "rule_derived", "motorway class implicit oneway"
    return "bidirectional", "both", "rule_derived", "OSM default in absence of oneway evidence"


def _polyline_length_m(points: list[list[float]]) -> float:
    return sum(_haversine_m(first, second) for first, second in zip(points, points[1:], strict=False))


def _haversine_m(first: list[float], second: list[float]) -> float:
    lon1, lat1 = map(math.radians, first)
    lon2, lat2 = map(math.radians, second)
    delta_lon = lon2 - lon1
    delta_lat = lat2 - lat1
    value = math.sin(delta_lat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(delta_lon / 2) ** 2
    return 6_371_008.8 * 2 * math.atan2(math.sqrt(value), math.sqrt(max(0.0, 1 - value)))


def _is_gzip(path: Path, raw_bytes: bytes) -> bool:
    return path.suffix.casefold() == ".gz" or raw_bytes.startswith(b"\x1f\x8b")


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


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


def _validate_optional_time(value: datetime | None, field_name: str) -> None:
    if value is not None:
        _require_aware(value, field_name)


def _require_aware(value: datetime, field_name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")
