from __future__ import annotations

import hashlib
import math
import re
import xml.etree.ElementTree as ET
from collections import defaultdict
from pathlib import Path
from typing import Any, Sequence


SCHEMA_ID = "torii.external-micro-junction-audit/v1"
CLASSIFICATIONS = {
    "geometry_fragment_candidate",
    "protected_or_review",
    "explicit_roundabout",
}
_PROTECTED_JUNCTION_TYPES = {
    "traffic_light",
    "traffic_light_right_on_red",
    "rail_signal",
    "rail_crossing",
}
_ROUNDABOUT_VALUES = {"roundabout", "mini_roundabout", "circular"}


class ExternalMicroJunctionAuditError(ValueError):
    """Raised when a frozen SUMO network cannot be audited safely."""


def audit_external_micro_junctions(
    net_file: Path,
    *,
    micro_edge_threshold_m: float = 1.0,
    junction_ids: Sequence[str] = (),
) -> dict[str, Any]:
    """Classify reciprocal external micro edges without authorizing an edit.

    The audit treats the declared SUMO lane length and the rendered lane-shape
    length as independent measurements.  A geometry-fragment classification is
    possible only when both directions are below the threshold by both
    measurements, have compatible road/lane lineage, and carry no controller,
    stop-line, crossing, or roundabout protection evidence.  ``dir='t'`` is
    recorded as a netconvert-output/small-loop symptom, not as deletion or
    preservation authority by itself.
    """

    if not math.isfinite(micro_edge_threshold_m) or micro_edge_threshold_m <= 0:
        raise ExternalMicroJunctionAuditError(
            "micro_edge_threshold_m must be a positive finite number"
        )
    source = net_file.resolve()
    try:
        root = ET.parse(source).getroot()
    except (OSError, ET.ParseError) as exc:
        raise ExternalMicroJunctionAuditError(f"cannot parse SUMO network: {exc}") from exc
    if root.tag != "net":
        raise ExternalMicroJunctionAuditError(
            f"SUMO network root must be <net>, got <{root.tag}>"
        )

    junctions = {
        row.attrib["id"]: row
        for row in root.findall("junction")
        if row.attrib.get("id") and not row.attrib["id"].startswith(":")
    }
    requested = tuple(sorted(set(junction_ids), key=_natural_key))
    missing = sorted(set(requested) - set(junctions), key=_natural_key)
    if missing:
        raise ExternalMicroJunctionAuditError(
            f"requested junctions are absent from SUMO network: {missing}"
        )
    scope = set(requested)

    external_edges: dict[str, dict[str, Any]] = {}
    special_edges: dict[str, dict[str, Any]] = {}
    for edge in root.findall("edge"):
        edge_id = edge.attrib.get("id", "")
        if not edge_id:
            continue
        function = edge.attrib.get("function", "")
        if edge_id.startswith(":") or function:
            special_edges[edge_id] = _special_edge_record(edge)
            continue
        from_id = edge.attrib.get("from", "")
        to_id = edge.attrib.get("to", "")
        if not from_id or not to_id:
            continue
        external_edges[edge_id] = _external_edge_record(
            edge,
            threshold_m=micro_edge_threshold_m,
        )

    connections = _connection_records(root, external_edges)
    roundabout_edge_ids, roundabout_node_ids = _explicit_roundabout_members(root)
    crossing_by_junction = _crossing_evidence(junctions, special_edges)

    endpoint_groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for edge in external_edges.values():
        endpoints = tuple(sorted((edge["from_junction_id"], edge["to_junction_id"]), key=_natural_key))
        if len(endpoints) != 2 or endpoints[0] == endpoints[1]:
            continue
        if scope and not set(endpoints).issubset(scope):
            continue
        endpoint_groups[endpoints].append(edge)

    pair_records: list[dict[str, Any]] = []
    covered_turnaround_signatures: set[tuple[str, str, str, str]] = set()
    for endpoints, edges in sorted(endpoint_groups.items(), key=lambda item: _pair_key(item[0])):
        directions: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
        for edge in edges:
            directions[(edge["from_junction_id"], edge["to_junction_id"])].append(edge)
        if len(directions) != 2:
            continue
        first, second = endpoints
        forward = directions.get((first, second), [])
        reverse = directions.get((second, first), [])
        if not forward or not reverse:
            continue
        if not any(edge["micro_measurement_seen"] for edge in edges):
            continue

        pair_edge_ids = {edge["edge_id"] for edge in edges}
        related_connections = [
            row
            for row in connections
            if (
                row["owner_junction_id"] in endpoints
                or row["from_edge_id"] in pair_edge_ids
                or row["to_edge_id"] in pair_edge_ids
            )
        ]
        related_turnarounds = [row for row in related_connections if row["dir"] == "t"]
        for row in related_turnarounds:
            covered_turnaround_signatures.add(_turnaround_signature(row))
        direct_turnarounds = [
            row
            for row in related_turnarounds
            if row["from_edge_id"] in pair_edge_ids and row["to_edge_id"] in pair_edge_ids
        ]
        pair_records.append(
            _classify_pair(
                endpoints=endpoints,
                forward=forward,
                reverse=reverse,
                junctions=junctions,
                crossing_by_junction=crossing_by_junction,
                related_connections=related_connections,
                related_turnarounds=related_turnarounds,
                direct_turnarounds=direct_turnarounds,
                roundabout_edge_ids=roundabout_edge_ids,
                roundabout_node_ids=roundabout_node_ids,
                threshold_m=micro_edge_threshold_m,
            )
        )

    turnarounds = []
    for row in connections:
        if row["dir"] != "t":
            continue
        if scope and row["owner_junction_id"] not in scope:
            continue
        turnarounds.append(
            {
                **row,
                "audit_disposition": "observed_not_authorizing_edit",
                "covered_by_micro_pair_record": (
                    _turnaround_signature(row) in covered_turnaround_signatures
                ),
                "evidence_boundary": (
                    "dir='t' is observed in compiled net.xml; whether it was inferred by "
                    "netconvert or supplied upstream is not identifiable from net.xml alone"
                ),
            }
        )

    counts = {name: 0 for name in sorted(CLASSIFICATIONS)}
    for row in pair_records:
        counts[row["classification"]] += 1
    payload = {
        "schema_id": SCHEMA_ID,
        "status": "pass",
        "source_net_file": str(source),
        "source_net_sha256": _file_sha256(source),
        "scope": {
            "junction_ids": list(requested),
            "mode": "explicit_junction_scope" if requested else "whole_network",
        },
        "policy": {
            "micro_edge_threshold_m": micro_edge_threshold_m,
            "threshold_rule": (
                "every lane in both reciprocal directions must have positive declared and "
                "rendered lengths at or below the threshold"
            ),
            "classification_only": True,
            "automatic_edit_authorization": "blocked",
            "source_mutation": "none",
        },
        "reciprocal_micro_pair_count": len(pair_records),
        "classification_counts": counts,
        "reciprocal_micro_pairs": pair_records,
        "dir_t_turnaround_count": len(turnarounds),
        "dir_t_turnarounds": sorted(
            turnarounds,
            key=lambda row: (
                _natural_key(row["owner_junction_id"]),
                _natural_key(row["from_edge_id"]),
                _natural_key(row["to_edge_id"]),
                row["from_lane"],
                row["to_lane"],
            ),
        ),
        "claim_boundary": (
            "geometry_fragment_candidate is a review classification only; this audit never "
            "authorizes deletion, node joining, connection rewriting, or promotion"
        ),
    }
    return payload


def _classify_pair(
    *,
    endpoints: tuple[str, str],
    forward: list[dict[str, Any]],
    reverse: list[dict[str, Any]],
    junctions: dict[str, ET.Element],
    crossing_by_junction: dict[str, list[dict[str, str]]],
    related_connections: list[dict[str, Any]],
    related_turnarounds: list[dict[str, Any]],
    direct_turnarounds: list[dict[str, Any]],
    roundabout_edge_ids: set[str],
    roundabout_node_ids: set[str],
    threshold_m: float,
) -> dict[str, Any]:
    edges = sorted([*forward, *reverse], key=lambda row: _natural_key(row["edge_id"]))
    edge_ids = {row["edge_id"] for row in edges}
    junction_rows = [junctions[node_id] for node_id in endpoints]
    explicit_roundabout_evidence = _roundabout_evidence(
        edges,
        junction_rows,
        roundabout_edge_ids,
        roundabout_node_ids,
    )
    protection = _protection_evidence(
        endpoints,
        edges,
        junctions,
        crossing_by_junction,
        related_connections,
        related_turnarounds,
    )
    exact_reciprocal_pair = len(forward) == 1 and len(reverse) == 1
    all_micro = exact_reciprocal_pair and all(row["fully_micro"] for row in edges)
    threshold_agreement = all(row["threshold_side_agreement"] for row in edges)
    semantics_compatible = exact_reciprocal_pair and (
        forward[0]["semantic_signature"] == reverse[0]["semantic_signature"]
    )
    lineage_intersection = sorted(
        set.intersection(*(set(row["lineage"]["osm_way_ids"]) for row in edges)),
        key=_natural_key,
    )
    lineage_compatible = bool(lineage_intersection)

    blockers: list[str] = []
    if not exact_reciprocal_pair:
        blockers.append("parallel_reciprocal_edges_are_ambiguous")
    if not all_micro:
        blockers.append("both_directions_are_not_fully_micro")
    if not threshold_agreement:
        blockers.append("declared_and_rendered_lengths_cross_micro_threshold")
    if not semantics_compatible:
        blockers.append("reciprocal_lane_or_road_semantics_differ")
    if not lineage_compatible:
        blockers.append("shared_osm_way_lineage_is_unproven")
    blockers.extend(protection["blocking_reasons"])

    if explicit_roundabout_evidence:
        classification = "explicit_roundabout"
        reasons = ["explicit_roundabout_evidence_present"]
    elif blockers:
        classification = "protected_or_review"
        reasons = sorted(set(blockers))
    else:
        classification = "geometry_fragment_candidate"
        reasons = [
            "exact_reciprocal_external_pair",
            "declared_and_rendered_lengths_are_below_threshold",
            "shared_osm_way_lineage_and_lane_semantics_match",
            "no_protected_junction_controller_stopline_or_crossing_evidence",
            "dir_t_turnarounds_recorded_without_treating_them_as_preservation_authority",
        ]

    direct_pairs = {(row["from_edge_id"], row["to_edge_id"]) for row in direct_turnarounds}
    traversable_two_edge_loop = any(
        (left, right) in direct_pairs and (right, left) in direct_pairs
        for left in edge_ids
        for right in edge_ids
        if left != right
    )
    return {
        "record_id": "micro-pair-" + hashlib.sha256(
            "\0".join((*endpoints, *sorted(edge_ids))).encode("utf-8")
        ).hexdigest()[:20],
        "classification": classification,
        "classification_reasons": reasons,
        "automatic_edit_authorization": "blocked",
        "junction_ids": list(endpoints),
        "forward_edge_ids": [row["edge_id"] for row in forward],
        "reverse_edge_ids": [row["edge_id"] for row in reverse],
        "micro_edge_threshold_m": threshold_m,
        "exact_reciprocal_pair": exact_reciprocal_pair,
        "all_lanes_below_threshold_by_declared_and_rendered_length": all_micro,
        "threshold_side_agreement": threshold_agreement,
        "lineage": {
            "shared_osm_way_ids": lineage_intersection,
            "compatible": lineage_compatible,
        },
        "lane_semantics_compatible": semantics_compatible,
        "edges": [{key: value for key, value in row.items() if key != "semantic_signature"} for row in edges],
        "protection_evidence": protection,
        "explicit_roundabout_evidence": explicit_roundabout_evidence,
        "small_loop_risk": {
            "reciprocal_directed_edge_pair": True,
            "direct_dir_t_connection_count": len(direct_turnarounds),
            "traversable_two_edge_loop": traversable_two_edge_loop,
            "status": "observed" if direct_turnarounds else "not_observed",
            "classification_effect": "none_without_independent_protection_evidence",
        },
        "related_dir_t_turnarounds": related_turnarounds,
    }


def _external_edge_record(edge: ET.Element, *, threshold_m: float) -> dict[str, Any]:
    lanes = [_lane_record(lane, threshold_m=threshold_m) for lane in edge.findall("lane")]
    params = _params(edge)
    threshold_agreement = bool(lanes) and all(row["threshold_side_agreement"] for row in lanes)
    fully_micro = threshold_agreement and all(row["fully_micro"] for row in lanes)
    semantic_signature = (
        edge.attrib.get("type", ""),
        edge.attrib.get("priority", ""),
        tuple(sorted((_lane_semantic_signature(row) for row in lanes))),
        params.get("highway", ""),
        params.get("service", ""),
    )
    return {
        "edge_id": edge.attrib["id"],
        "from_junction_id": edge.attrib["from"],
        "to_junction_id": edge.attrib["to"],
        "edge_type": edge.attrib.get("type", ""),
        "priority": edge.attrib.get("priority", ""),
        "params": params,
        "lineage": _edge_lineage(edge, params),
        "lanes": lanes,
        "fully_micro": fully_micro,
        "threshold_side_agreement": threshold_agreement,
        "micro_measurement_seen": any(row["micro_measurement_seen"] for row in lanes),
        "semantic_signature": semantic_signature,
    }


def _lane_record(lane: ET.Element, *, threshold_m: float) -> dict[str, Any]:
    declared = _positive_float(lane.attrib.get("length"))
    rendered = _shape_length(lane.attrib.get("shape", ""))
    valid = declared is not None and rendered is not None and rendered > 0
    agreement = valid and ((declared <= threshold_m) == (rendered <= threshold_m))
    return {
        "lane_id": lane.attrib.get("id", ""),
        "index": _int_or_none(lane.attrib.get("index")),
        "allow": sorted(lane.attrib.get("allow", "").split()),
        "disallow": sorted(lane.attrib.get("disallow", "").split()),
        "speed_mps": _positive_float(lane.attrib.get("speed")),
        "width_m": _positive_float(lane.attrib.get("width")),
        "end_offset_m": _nonnegative_float(lane.attrib.get("endOffset")),
        "declared_length_m": declared,
        "rendered_shape_length_m": rendered,
        "threshold_side_agreement": agreement,
        "fully_micro": bool(agreement and declared <= threshold_m and rendered <= threshold_m),
        "micro_measurement_seen": bool(
            (declared is not None and declared <= threshold_m)
            or (rendered is not None and rendered <= threshold_m)
        ),
    }


def _protection_evidence(
    endpoints: tuple[str, str],
    edges: list[dict[str, Any]],
    junctions: dict[str, ET.Element],
    crossing_by_junction: dict[str, list[dict[str, str]]],
    connections: list[dict[str, Any]],
    turnarounds: list[dict[str, Any]],
) -> dict[str, Any]:
    junction_types = {
        node_id: junctions[node_id].attrib.get("type", "") for node_id in endpoints
    }
    protected_types = {
        node_id: kind for node_id, kind in junction_types.items() if kind in _PROTECTED_JUNCTION_TYPES
    }
    controller_ids = sorted(
        {
            str(row["controller_id"])
            for row in connections
            if str(row.get("controller_id", ""))
        },
        key=_natural_key,
    )
    stopline_markers: list[dict[str, str]] = []
    for node_id in endpoints:
        for key, value in _params(junctions[node_id]).items():
            if _contains_stopline_text(key) or _contains_stopline_text(value):
                stopline_markers.append(
                    {"owner_id": node_id, "key": key, "value": value}
                )
    for edge in edges:
        for key, value in edge["params"].items():
            if _contains_stopline_text(key) or _contains_stopline_text(value):
                stopline_markers.append(
                    {"owner_id": edge["edge_id"], "key": key, "value": value}
                )
    crossings = {
        node_id: crossing_by_junction.get(node_id, [])
        for node_id in endpoints
        if crossing_by_junction.get(node_id)
    }
    reasons: list[str] = []
    if protected_types:
        reasons.append("protected_junction_type_present")
    if controller_ids:
        reasons.append("controlled_connection_or_stopline_controller_present")
    if stopline_markers:
        reasons.append("explicit_stopline_marker_present")
    if crossings:
        reasons.append("crossing_or_walkingarea_present")
    return {
        "junction_types": junction_types,
        "protected_junction_types": protected_types,
        "controller_ids": controller_ids,
        "controlled_connection_count": sum(
            1 for row in connections if str(row.get("controller_id", ""))
        ),
        "stopline_markers": stopline_markers,
        "crossing_or_walkingarea_by_junction": crossings,
        "dir_t_turnaround_count": len(turnarounds),
        "blocking_reasons": reasons,
    }


def _connection_records(
    root: ET.Element,
    external_edges: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for row in root.findall("connection"):
        from_id = row.attrib.get("from", "")
        to_id = row.attrib.get("to", "")
        source = external_edges.get(from_id)
        target = external_edges.get(to_id)
        if source is None or target is None:
            continue
        records.append(
            {
                "from_edge_id": from_id,
                "to_edge_id": to_id,
                "from_lane": _int_or_none(row.attrib.get("fromLane")),
                "to_lane": _int_or_none(row.attrib.get("toLane")),
                "owner_junction_id": source["to_junction_id"],
                "dir": row.attrib.get("dir", "").lower(),
                "via": row.attrib.get("via", ""),
                "state": row.attrib.get("state", ""),
                "controller_id": row.attrib.get("tl", ""),
                "link_index": _int_or_none(row.attrib.get("linkIndex")),
                "from_lineage": source["lineage"],
                "to_lineage": target["lineage"],
            }
        )
    return records


def _crossing_evidence(
    junctions: dict[str, ET.Element],
    special_edges: dict[str, dict[str, Any]],
) -> dict[str, list[dict[str, str]]]:
    lane_to_special: dict[str, dict[str, Any]] = {}
    for row in special_edges.values():
        for lane_id in row["lane_ids"]:
            lane_to_special[lane_id] = row
    result: dict[str, list[dict[str, str]]] = {}
    for node_id, junction in junctions.items():
        rows: dict[str, dict[str, str]] = {}
        lane_ids = (junction.attrib.get("incLanes", "") + " " + junction.attrib.get("intLanes", "")).split()
        for lane_id in lane_ids:
            special = lane_to_special.get(lane_id)
            if special and special["function"] in {"crossing", "walkingarea"}:
                rows[special["edge_id"]] = {
                    "edge_id": special["edge_id"],
                    "function": special["function"],
                }
        if rows:
            result[node_id] = [rows[key] for key in sorted(rows, key=_natural_key)]
    return result


def _special_edge_record(edge: ET.Element) -> dict[str, Any]:
    return {
        "edge_id": edge.attrib.get("id", ""),
        "function": edge.attrib.get("function", ""),
        "lane_ids": [lane.attrib.get("id", "") for lane in edge.findall("lane")],
    }


def _explicit_roundabout_members(root: ET.Element) -> tuple[set[str], set[str]]:
    edge_ids: set[str] = set()
    node_ids: set[str] = set()
    for row in root.findall("roundabout"):
        edge_ids.update(row.attrib.get("edges", "").split())
        node_ids.update(row.attrib.get("nodes", "").split())
    return edge_ids, node_ids


def _roundabout_evidence(
    edges: list[dict[str, Any]],
    junctions: list[ET.Element],
    roundabout_edge_ids: set[str],
    roundabout_node_ids: set[str],
) -> list[dict[str, str]]:
    evidence: list[dict[str, str]] = []
    for edge in edges:
        if edge["edge_id"] in roundabout_edge_ids:
            evidence.append(
                {"owner_id": edge["edge_id"], "source": "sumo_roundabout_record"}
            )
        for key, value in edge["params"].items():
            if key.lower() == "junction" and value.lower() in _ROUNDABOUT_VALUES:
                evidence.append(
                    {
                        "owner_id": edge["edge_id"],
                        "source": "edge_param",
                        "value": value,
                    }
                )
    for junction in junctions:
        node_id = junction.attrib.get("id", "")
        if node_id in roundabout_node_ids:
            evidence.append({"owner_id": node_id, "source": "sumo_roundabout_record"})
        for key, value in _params(junction).items():
            if key.lower() in {"junction", "highway"} and value.lower() in _ROUNDABOUT_VALUES:
                evidence.append(
                    {
                        "owner_id": node_id,
                        "key": key,
                        "value": value,
                        "source": "junction_param",
                    }
                )
    return evidence


def _edge_lineage(edge: ET.Element, params: dict[str, str]) -> dict[str, Any]:
    edge_id = edge.attrib.get("id", "")
    split_root = edge_id.lstrip("-").split("#", 1)[0]
    way_ids: set[str] = set()
    if split_root.isdigit():
        way_ids.add(split_root)
    for key, value in params.items():
        if key.lower() in {"origid", "orig_id", "osm_way_id", "osm:way:id"}:
            way_ids.update(re.findall(r"\d+", value))
    for lane in edge.findall("lane"):
        for key, value in _params(lane).items():
            if key.lower() in {"origid", "orig_id", "osm_way_id", "osm:way:id"}:
                way_ids.update(re.findall(r"\d+", value))
    return {
        "split_root_edge_id": split_root,
        "osm_way_ids": sorted(way_ids, key=_natural_key),
        "road_name": edge.attrib.get("name", params.get("name", "")),
        "road_ref": params.get("ref", ""),
        "highway": params.get("highway", ""),
        "service": params.get("service", ""),
    }


def _params(element: ET.Element) -> dict[str, str]:
    return {
        row.attrib["key"]: row.attrib.get("value", "")
        for row in element.findall("param")
        if row.attrib.get("key")
    }


def _lane_semantic_signature(row: dict[str, Any]) -> tuple[Any, ...]:
    return (
        tuple(row["allow"]),
        tuple(row["disallow"]),
        _semantic_number(row["speed_mps"]),
        _semantic_number(row["width_m"]),
        _semantic_number(row["end_offset_m"]),
    )


def _semantic_number(value: float | None) -> float:
    return -1.0 if value is None else round(value, 6)


def _contains_stopline_text(value: str) -> bool:
    normalized = value.lower().replace("-", "").replace("_", "")
    return "stopline" in normalized


def _shape_length(raw_shape: str) -> float | None:
    points: list[tuple[float, float]] = []
    for token in raw_shape.split():
        values = token.split(",")
        if len(values) < 2:
            return None
        try:
            point = (float(values[0]), float(values[1]))
        except ValueError:
            return None
        if not all(math.isfinite(value) for value in point):
            return None
        points.append(point)
    if len(points) < 2:
        return None
    return sum(math.dist(left, right) for left, right in zip(points, points[1:]))


def _positive_float(value: str | None) -> float | None:
    try:
        parsed = float(value) if value is not None else None
    except ValueError:
        return None
    return parsed if parsed is not None and math.isfinite(parsed) and parsed > 0 else None


def _nonnegative_float(value: str | None) -> float | None:
    try:
        parsed = float(value) if value is not None else None
    except ValueError:
        return None
    return parsed if parsed is not None and math.isfinite(parsed) and parsed >= 0 else None


def _int_or_none(value: str | None) -> int | None:
    try:
        return int(value) if value is not None else None
    except ValueError:
        return None


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _turnaround_signature(row: dict[str, Any]) -> tuple[str, str, str, str]:
    return (
        str(row["from_edge_id"]),
        str(row["to_edge_id"]),
        str(row["from_lane"]),
        str(row["to_lane"]),
    )


def _pair_key(endpoints: tuple[str, str]) -> tuple[tuple[Any, ...], tuple[Any, ...]]:
    return _natural_key(endpoints[0]), _natural_key(endpoints[1])


def _natural_key(value: str) -> tuple[Any, ...]:
    return tuple(int(part) if part.isdigit() else part for part in re.split(r"(\d+)", value))
