"""Read-only SUMO edge assertions and explicit OSM lineage evidence."""

from __future__ import annotations

import gzip
import hashlib
import math
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Mapping
from xml.etree import ElementTree as ET

from pyproj import CRS, Transformer

from torii_sumo.corridor.netxml import RawEdge, RawLane, RawLocation, parse_net_xml
from torii_sumo.road_network.contracts import ROAD_NETWORK_SCHEMA, RoadObjectRef


SUMO_ADAPTER_SCHEMA = f"{ROAD_NETWORK_SCHEMA}/sumo-source-snapshot/v1"
SUMO_PROVIDER = "Eclipse SUMO"
SUMO_DATASET = "SUMO net.xml"


def read_sumo_road_snapshot(
    path: str | Path,
    *,
    target_time: datetime,
    retrieved_at: datetime,
    valid_from: datetime | None = None,
    valid_to: datetime | None = None,
    expected_sha256: str | None = None,
    imported_from: str = "unknown",
    imported_source_sha256: str | None = None,
) -> dict[str, Any]:
    """Read a SUMO network without treating generated edge IDs as source truth."""

    source_path = Path(path)
    _require_aware(target_time, "target_time")
    _require_aware(retrieved_at, "retrieved_at")
    _validate_optional_time(valid_from, "valid_from")
    _validate_optional_time(valid_to, "valid_to")
    imported_from = str(imported_from).strip().casefold() or "unknown"
    if imported_source_sha256 is not None and not re.fullmatch(r"[0-9a-fA-F]{64}", imported_source_sha256):
        raise ValueError("imported_source_sha256 must be a SHA-256 hex digest")
    imported_source_sha256 = imported_source_sha256.casefold() if imported_source_sha256 else None

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
        "imported_from": imported_from,
        "imported_source_sha256": imported_source_sha256,
    }
    blocking_reasons: list[str] = []
    review_reasons: list[str] = []
    if expected_sha256 is not None and expected_sha256.casefold() != actual_sha256:
        blocking_reasons.append("source_sha256_mismatch")
    if time_status == "blocked":
        blocking_reasons.append(time_reason)
    elif time_status == "review_required":
        review_reasons.append(time_reason)
    if imported_from == "osm" and imported_source_sha256 is None:
        review_reasons.append("imported_osm_source_sha256_not_declared")
    if blocking_reasons:
        return _empty_report(source_snapshot, blocking_reasons=blocking_reasons)

    try:
        xml_bytes = gzip.decompress(raw_bytes) if _is_gzip(source_path, raw_bytes) else raw_bytes
        root = ET.fromstring(xml_bytes)
        network = parse_net_xml(root)
    except (EOFError, OSError, ET.ParseError, ValueError) as exc:
        return _empty_report(
            source_snapshot,
            blocking_reasons=(f"invalid_sumo_xml:{type(exc).__name__}",),
        )
    if _local_name(root.tag) != "net":
        return _empty_report(source_snapshot, blocking_reasons=("sumo_net_root_required",))

    xy_to_lonlat, projection = _coordinate_converter(network.location)
    if projection["status"] != "pass":
        review_reasons.extend(projection["review_reasons"])

    edge_elements = list(network.edges.values())
    edge_assertions: list[dict[str, Any]] = []
    excluded_edges: list[dict[str, Any]] = []
    observed_count = 0
    rule_derived_count = 0
    unresolved_count = 0

    for edge in edge_elements:
        edge_id = edge.edge_id
        function = edge.function.strip().casefold()
        edge_role = _edge_role(edge_id, function)
        if edge_role == "unknown_function":
            review_reasons.append(f"sumo_edge_function_unknown:{edge_id}:{function}")
        if edge_id.startswith(":") and function not in {"", "internal", "connector", "crossing", "walkingarea"}:
            review_reasons.append(f"sumo_edge_id_function_conflict:{edge_id}")
        if edge_role == "junction_internal":
            excluded_edges.append(
                {
                    "edge_id": edge_id,
                    "function": function,
                    "exclusion_kind": "internal",
                    "reason": "SUMO internal edge is generated support topology, not a road identity",
                }
            )
            continue
        if edge_role != "road":
            excluded_edges.append(
                {
                    "edge_id": edge_id,
                    "function": function,
                    "exclusion_kind": "support",
                    "edge_role": edge_role,
                    "reason": "SUMO function edge is support topology, not a source road identity",
                }
            )
            continue
        if not edge_id:
            blocking_reasons.append("road_edge_id_required")
            continue

        params = dict(edge.params)
        origin_evidence = _origin_evidence(edge)
        if origin_evidence["conflict"]:
            review_reasons.append(f"sumo_origin_evidence_conflict:{edge_id}")
        lineage_refs, lineage_status = _osm_lineage(
            edge_id,
            origin_evidence,
            imported_from=imported_from,
        )
        if lineage_status == "observed":
            observed_count += 1
        elif lineage_status == "rule_derived":
            rule_derived_count += 1
            review_reasons.append("sumo_osm_lineage_rule_derived")
        else:
            unresolved_count += 1
            review_reasons.append("sumo_osm_lineage_unresolved")

        try:
            lanes = [_lane_assertion(lane, xy_to_lonlat) for lane in edge.lanes]
            if lanes:
                geometry_xy = [list(point) for point in lanes[0]["geometry_xy"]]
            else:
                geometry_xy = []
            geometry_lonlat = _convert_shape(geometry_xy, xy_to_lonlat)
        except ValueError:
            blocking_reasons.append(f"invalid_projected_geometry:{edge_id}")
            continue
        if not geometry_xy:
            review_reasons.append(f"sumo_edge_geometry_missing:{edge_id}")

        source_ref = RoadObjectRef(
            namespace="sumo",
            object_type="edge",
            object_id=edge_id,
            source_sha256=actual_sha256,
            valid_from=valid_from,
            valid_to=valid_to,
            provider=SUMO_PROVIDER,
            dataset=SUMO_DATASET,
        )
        edge_assertions.append(
            {
                "source_ref": source_ref.as_dict(),
                "edge_id": edge_id,
                "from_node_id": edge.from_junction,
                "to_node_id": edge.to_junction,
                "priority": edge.priority,
                "type_id": edge.edge_type,
                "function": function,
                "name": _single_parameter_value(edge, "name") or edge.name,
                "params": dict(sorted(params.items())),
                "lanes": lanes,
                "lane_count": len(lanes),
                "geometry_xy": geometry_xy,
                "geometry_lonlat": geometry_lonlat,
                "osm_source_way_ids": list(lineage_refs),
                "osm_lineage_status": lineage_status,
                "osm_lineage_basis": "param:origId/origID"
                if lineage_status == "observed"
                else ("numeric_netconvert_edge_root" if lineage_status == "rule_derived" else "unresolved"),
                "origin_evidence": origin_evidence,
                "imported_from": imported_from,
                "imported_source_sha256": imported_source_sha256,
                "relative_direction": "against" if edge_id.startswith("-") else "with",
                "relative_direction_status": "rule_derived",
                "relative_direction_basis": "netconvert numeric edge-id leading-minus convention",
                "classification_only": True,
                "automatic_promotion_gate": "blocked",
            }
        )

    if blocking_reasons:
        return _empty_report(source_snapshot, blocking_reasons=blocking_reasons)

    osm_source_index: dict[str, dict[str, Any]] = {}
    for assertion in edge_assertions:
        for way_id in assertion["osm_source_way_ids"]:
            item = osm_source_index.setdefault(
                way_id,
                {"edge_ids": [], "relative_directions": [], "lineage_statuses": []},
            )
            item["edge_ids"].append(assertion["edge_id"])
            item["relative_directions"].append(assertion["relative_direction"])
            item["lineage_statuses"].append(assertion["osm_lineage_status"])
    for item in osm_source_index.values():
        statuses = set(item.pop("lineage_statuses"))
        item["edge_ids"] = sorted(set(item["edge_ids"]))
        item["relative_directions"] = sorted(set(item["relative_directions"]))
        item["relative_direction_status"] = "rule_derived"
        item["relative_direction_basis"] = "netconvert numeric edge-id leading-minus convention"
        if statuses == {"observed"}:
            item["lineage_status"] = "observed"
        elif statuses == {"rule_derived"}:
            item["lineage_status"] = "rule_derived"
        else:
            item["lineage_status"] = "mixed"

    status = "review_required" if review_reasons else "pass"
    internal_count = sum(item["exclusion_kind"] == "internal" for item in excluded_edges)
    support_count = sum(item["exclusion_kind"] == "support" for item in excluded_edges)
    return {
        "schema": SUMO_ADAPTER_SCHEMA,
        "status": status,
        "acquisition_status": "pass",
        "claim_status": status,
        "source_snapshot": source_snapshot,
        "source_attribution": SUMO_PROVIDER,
        "imported_from": imported_from,
        "imported_source_sha256": imported_source_sha256,
        "location": projection["location"],
        "projection_status": projection["status"],
        "edge_assertions": sorted(edge_assertions, key=lambda item: item["edge_id"]),
        "excluded_edges": sorted(excluded_edges, key=lambda item: item["edge_id"]),
        "osm_source_index": dict(sorted(osm_source_index.items())),
        "counts": {
            "raw_edge_count": len(edge_elements),
            "road_edge_assertion_count": len(edge_assertions),
            "internal_edge_excluded_count": internal_count,
            "support_edge_excluded_count": support_count,
            "observed_osm_lineage_edge_count": observed_count,
            "rule_derived_osm_lineage_edge_count": rule_derived_count,
            "unresolved_osm_lineage_edge_count": unresolved_count,
        },
        "blocking_reasons": [],
        "review_reasons": sorted(set(review_reasons)),
        "classification_only": True,
        "automatic_promotion_gate": "blocked",
        "claim_boundary": (
            "SUMO edges and shapes are simulation-source assertions. Only explicit origId parameters are observed "
            "OSM lineage; numeric edge-root recovery is rule-derived and requires review."
        ),
    }


def _lane_assertion(
    lane: RawLane,
    converter: Callable[[float, float], tuple[float, float]] | None,
) -> dict[str, Any]:
    geometry_xy = [list(point) for point in lane.shape]
    return {
        "lane_id": lane.lane_id,
        "index": lane.declared_index,
        "speed_m_s": lane.speed,
        "length_m": lane.length,
        "width_m": lane.width,
        "allow": list(lane.allow),
        "disallow": list(lane.disallow),
        "params": dict(sorted(lane.params.items())),
        "geometry_xy": geometry_xy,
        "geometry_lonlat": _convert_shape(geometry_xy, converter),
    }


def _coordinate_converter(
    location: RawLocation | None,
) -> tuple[Callable[[float, float], tuple[float, float]] | None, dict[str, Any]]:
    if location is None:
        return None, {
            "status": "review_required",
            "review_reasons": ["sumo_location_missing"],
            "location": {},
        }
    attributes = dict(location.attributes)
    projection = location.proj_parameter.strip()
    if location.net_offset is None:
        return None, {
            "status": "review_required",
            "review_reasons": ["sumo_net_offset_invalid"],
            "location": attributes,
        }
    offset_x, offset_y = location.net_offset
    if not projection or projection in {"!", "-", "."}:
        return None, {
            "status": "review_required",
            "review_reasons": ["sumo_geo_projection_not_declared"],
            "location": attributes,
        }
    try:
        source_crs = CRS.from_user_input(projection)
        if source_crs.is_geographic:

            def converter(x: float, y: float) -> tuple[float, float]:
                return x - offset_x, y - offset_y
        else:
            transformer = Transformer.from_crs(source_crs, CRS.from_epsg(4326), always_xy=True)

            def converter(x: float, y: float) -> tuple[float, float]:
                return transformer.transform(x - offset_x, y - offset_y)
    except Exception:  # noqa: BLE001 - malformed external CRS declarations are evidence failures.
        return None, {
            "status": "review_required",
            "review_reasons": ["sumo_geo_projection_invalid"],
            "location": attributes,
        }
    return converter, {"status": "pass", "review_reasons": [], "location": attributes}


def _edge_role(edge_id: str, function: str) -> str:
    if function == "crossing":
        return "pedestrian_crossing"
    if function == "walkingarea":
        return "pedestrian_walking_area"
    if function == "connector":
        return "assignment_connector"
    if edge_id.startswith(":") or function == "internal":
        return "junction_internal"
    if function in {"", "normal"}:
        return "road"
    return "unknown_function"


def _origin_evidence(edge: RawEdge) -> dict[str, Any]:
    assertions: list[dict[str, Any]] = []
    token_sets: set[tuple[str, ...]] = set()
    containers = [("edge", edge.edge_id, edge.params)] + [("lane", lane.lane_id, lane.params) for lane in edge.lanes]
    for object_type, object_id, params in containers:
        for key, value in params.items():
            if key.casefold() != "origid" or not value.strip():
                continue
            tokens = tuple(sorted(set(_split_orig_ids(value))))
            token_sets.add(tokens)
            assertions.append(
                {
                    "object_type": object_type,
                    "object_id": object_id,
                    "key": key,
                    "raw_value": value,
                    "tokens": list(tokens),
                }
            )
    tokens = sorted({token for token_set in token_sets for token in token_set})
    return {
        "assertions": assertions,
        "tokens": tokens,
        "conflict": len(token_sets) > 1,
        "status": "observed" if assertions else "not_available",
    }


def _single_parameter_value(edge: RawEdge, key: str) -> str:
    values = {
        value
        for params in (edge.params, *(lane.params for lane in edge.lanes))
        for candidate_key, value in params.items()
        if candidate_key.casefold() == key.casefold() and value.strip()
    }
    return sorted(values)[0] if values else ""


def _osm_lineage(
    edge_id: str,
    origin_evidence: Mapping[str, Any],
    *,
    imported_from: str,
) -> tuple[tuple[str, ...], str]:
    if imported_from != "osm":
        return (), "unresolved"
    observed = tuple(str(item) for item in origin_evidence.get("tokens", ()))
    if observed:
        return tuple(sorted(set(observed))), "observed"
    root = edge_id[1:] if edge_id.startswith("-") else edge_id
    root = root.split("#", 1)[0]
    if re.fullmatch(r"\d+", root):
        return (root,), "rule_derived"
    return (), "unresolved"


def _split_orig_ids(value: str) -> list[str]:
    return [item for item in re.split(r"[\s,;]+", value.strip()) if item]


def _convert_shape(
    points: list[list[float]],
    converter: Callable[[float, float], tuple[float, float]] | None,
) -> list[list[float]]:
    if converter is None:
        return []
    converted: list[list[float]] = []
    for x, y in points:
        lon, lat = converter(x, y)
        if not math.isfinite(lon) or not math.isfinite(lat) or not -180 <= lon <= 180 or not -90 <= lat <= 90:
            raise ValueError("SUMO projected coordinate is outside finite WGS84 longitude/latitude bounds")
        converted.append([round(float(lon), 9), round(float(lat), 9)])
    return converted


def _empty_report(
    source_snapshot: Mapping[str, Any],
    *,
    blocking_reasons: tuple[str, ...] | list[str],
) -> dict[str, Any]:
    return {
        "schema": SUMO_ADAPTER_SCHEMA,
        "status": "blocked",
        "acquisition_status": "blocked",
        "claim_status": "blocked",
        "source_snapshot": dict(source_snapshot),
        "source_attribution": SUMO_PROVIDER,
        "location": {},
        "projection_status": "blocked",
        "edge_assertions": [],
        "excluded_edges": [],
        "osm_source_index": {},
        "counts": {
            "raw_edge_count": 0,
            "road_edge_assertion_count": 0,
            "internal_edge_excluded_count": 0,
            "support_edge_excluded_count": 0,
            "observed_osm_lineage_edge_count": 0,
            "rule_derived_osm_lineage_edge_count": 0,
            "unresolved_osm_lineage_edge_count": 0,
        },
        "blocking_reasons": sorted(set(blocking_reasons)),
        "review_reasons": [],
        "classification_only": True,
        "automatic_promotion_gate": "blocked",
    }


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
