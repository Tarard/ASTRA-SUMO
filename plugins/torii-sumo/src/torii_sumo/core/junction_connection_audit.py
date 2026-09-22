from __future__ import annotations

import csv
import gzip
import json
import math
from collections import Counter
from pathlib import Path
from typing import Any
import xml.etree.ElementTree as ET


CONNECTION_RECORD_FIELDS = [
    "category",
    "from",
    "to",
    "fromLane",
    "toLane",
    "dir",
    "state",
    "via",
    "tl",
    "linkIndex",
    "linkIndex2",
    "pass",
    "uncontrolled",
    "allow",
    "disallow",
    "keepClear",
    "contPos",
    "shape",
]

TLS_MOVEMENT_SIGNATURE_FIELDS = ["from", "to", "fromLane", "toLane", "via", "linkIndex", "dir", "state"]
PEDESTRIAN_EDGE_FUNCTIONS = {"crossing", "walkingarea"}
PEDESTRIAN_CONNECTION_SIGNATURE_FIELDS = ["from", "to", "fromLane", "toLane", "via", "tl", "linkIndex", "dir", "state"]
TURNAROUND_DIR = "t"


def build_teacher_guided_owner_semantics_probe(
    teacher_net_file: Path,
    candidate_net_file: Path,
    *,
    owner_id: str,
    candidate_owner_id: str | None = None,
    teacher_edge_map: dict[str, str] | None = None,
    source_osm_file: Path | None = None,
) -> dict[str, Any]:
    from .road_connectivity_teacher_model import (
        build_internal_movement_owner_approach_edge_map,
        build_internal_movement_owner_road_connectivity_parity_audit,
    )

    candidate_owner_id = candidate_owner_id or owner_id
    if teacher_edge_map is None:
        edge_mapping_layer = build_internal_movement_owner_approach_edge_map(
            teacher_net_file,
            candidate_net_file,
            owner_id=owner_id,
            candidate_owner_id=candidate_owner_id,
        )
        teacher_edge_map = edge_mapping_layer["edge_map"]
        edge_mapping_layer = dict(edge_mapping_layer)
        edge_mapping_layer["status"] = (
            "fail"
            if edge_mapping_layer.get("ambiguous_teacher_edges") or edge_mapping_layer.get("unmapped_teacher_edges")
            else "pass"
        )
    else:
        edge_mapping_layer = {
            "status": "pass",
            "source": "explicit_teacher_edge_map",
            "edge_map": dict(sorted(teacher_edge_map.items())),
        }
    road_layer = build_internal_movement_owner_road_connectivity_parity_audit(
        teacher_net_file,
        candidate_net_file,
        owner_id=owner_id,
        teacher_edge_map=teacher_edge_map,
    )
    teacher_signature = build_connection_signature(teacher_net_file, owner_id)
    candidate_signature = build_connection_signature(candidate_net_file, candidate_owner_id)
    junction_layer = _compare_owner_connection_signatures(teacher_signature, candidate_signature)
    pedestrian_layer = compare_pedestrian_crossing_signatures(
        teacher_net_file,
        candidate_net_file,
        owner_id,
        candidate_owner_id,
        teacher_edge_map=teacher_edge_map,
    )
    if _has_tls_semantics(teacher_net_file, owner_id) or _has_tls_semantics(candidate_net_file, candidate_owner_id):
        tls_layer = compare_tls_movement_signatures(
            teacher_net_file,
            candidate_net_file,
            owner_id,
            candidate_owner_id,
            teacher_edge_map=teacher_edge_map,
            teacher_internal_scope_id=owner_id,
            candidate_internal_scope_id=candidate_owner_id,
        )
    else:
        tls_layer = {"status": "skipped", "reason": "no_tls_semantics_for_owner"}

    layer_statuses = {
        "edge_mapping": str(edge_mapping_layer.get("status", "")),
        "road_connectivity": str(road_layer.get("status", "")),
        "junction_connection": str(junction_layer.get("status", "")),
        "pedestrian_crossing": str(pedestrian_layer.get("status", "")),
        "tls_movement": str(tls_layer.get("status", "")),
    }
    report = {
        "status": "pass" if all(status in {"pass", "skipped"} for status in layer_statuses.values()) else "fail",
        "claim_status": "diagnostic-demo",
        "teacher_net_file": str(teacher_net_file),
        "candidate_net_file": str(candidate_net_file),
        "owner_id": owner_id,
        "candidate_owner_id": candidate_owner_id,
        "teacher_edge_map": dict(sorted(teacher_edge_map.items())),
        "layer_statuses": layer_statuses,
        "edge_mapping_layer": edge_mapping_layer,
        "road_connectivity_layer": road_layer,
        "junction_connection_layer": junction_layer,
        "pedestrian_crossing_layer": pedestrian_layer,
        "tls_movement_layer": tls_layer,
    }
    if source_osm_file is not None:
        report["source_coverage_layer"] = build_teacher_source_osm_coverage(
            teacher_net_file,
            source_osm_file,
            owner_id=owner_id,
        )
    return report


def build_teacher_source_osm_coverage(
    teacher_net_file: Path,
    source_osm_file: Path,
    *,
    owner_id: str,
) -> dict[str, Any]:
    teacher_root = ET.parse(teacher_net_file).getroot()
    source_way_ids = _source_osm_way_ids(source_osm_file)
    rows = []
    for edge_id, direction in sorted(_owner_external_approach_edges(teacher_root, owner_id).items()):
        way_id = _teacher_edge_source_way_id(edge_id)
        status = (
            "synthetic_or_non_osm_edge"
            if not way_id
            else ("source_way_present" if way_id in source_way_ids else "source_way_missing")
        )
        rows.append(
            {
                "edge_id": edge_id,
                "direction": direction,
                "source_way_id": way_id,
                "status": status,
            }
        )
    return {
        "status": "pass",
        "claim_status": "diagnostic-demo",
        "teacher_net_file": str(teacher_net_file),
        "source_osm_file": str(source_osm_file),
        "owner_id": owner_id,
        "status_counts": dict(sorted(Counter(row["status"] for row in rows).items())),
        "teacher_edges": rows,
    }


def _source_osm_way_ids(source_osm_file: Path) -> set[str]:
    opener = gzip.open if source_osm_file.suffix.lower() == ".gz" else open
    with opener(source_osm_file, "rt", encoding="utf-8") as handle:
        root = ET.parse(handle).getroot()
    return {way.attrib["id"] for way in root.findall("way") if way.attrib.get("id")}


def _teacher_edge_source_way_id(edge_id: str) -> str:
    root = edge_id.lstrip("-").split("#", 1)[0]
    return root if root.isdigit() else ""


def _owner_external_approach_edges(root: ET.Element, owner_id: str) -> dict[str, str]:
    edges = {}
    for edge in root.findall("edge"):
        edge_id = edge.attrib.get("id", "")
        if not edge_id or edge_id.startswith(":") or edge.attrib.get("function") == "internal":
            continue
        if edge.attrib.get("to", "") == owner_id:
            edges[edge_id] = "incoming"
        elif edge.attrib.get("from", "") == owner_id:
            edges[edge_id] = "outgoing"
    return edges


def build_connection_signature(net_file: Path, junction_id: str) -> dict[str, Any]:
    root = ET.parse(net_file).getroot()
    plain_edges = {
        edge.attrib["id"]: edge
        for edge in root.findall("edge")
        if edge.attrib.get("id")
        and not edge.attrib["id"].startswith(":")
        and edge.attrib.get("function") != "internal"
    }
    incoming = {edge_id for edge_id, edge in plain_edges.items() if edge.attrib.get("to") == junction_id}
    outgoing = {edge_id for edge_id, edge in plain_edges.items() if edge.attrib.get("from") == junction_id}
    internal_prefix = f":{junction_id}_"
    target_internal_edges = [
        edge
        for edge in root.findall("edge")
        if edge.attrib.get("id", "").startswith(internal_prefix)
    ]

    records = []
    category_counts: Counter[str] = Counter()
    for connection in root.findall("connection"):
        source = connection.attrib.get("from", "")
        target = connection.attrib.get("to", "")
        via = connection.attrib.get("via", "")
        if not _is_related(source, target, via, incoming, outgoing, internal_prefix):
            continue
        category = _connection_category(source, target, incoming, outgoing, internal_prefix)
        category_counts[category] += 1
        record = {
            "category": category,
            "from": source,
            "to": target,
            "fromLane": connection.attrib.get("fromLane", ""),
            "toLane": connection.attrib.get("toLane", ""),
            "dir": connection.attrib.get("dir", ""),
            "state": connection.attrib.get("state", ""),
            "via": via,
            "tl": connection.attrib.get("tl", ""),
            "linkIndex": connection.attrib.get("linkIndex", ""),
        }
        for attr in CONNECTION_RECORD_FIELDS:
            record.setdefault(attr, connection.attrib.get(attr, ""))
        records.append(record)

    top_external = [record for record in records if record["category"] == "top_external"]
    top_external_turnaround = [record for record in top_external if _is_turnaround_record(record)]
    top_external_non_turnaround = [record for record in top_external if not _is_turnaround_record(record)]
    controlled_links = [record for record in records if record["tl"] and record["linkIndex"]]
    return {
        "status": "pass",
        "claim_status": "diagnostic-demo",
        "net_file": str(net_file),
        "junction_id": junction_id,
        "incoming_edges": sorted(incoming),
        "outgoing_edges": sorted(outgoing),
        "category_counts": dict(category_counts),
        "top_external_connection_count": len(top_external),
        "top_external_pair_count": len({(record["from"], record["to"]) for record in top_external}),
        "top_external_dir_counts": dict(Counter(record["dir"] or "blank" for record in top_external)),
        "top_external_turnaround_connection_count": len(top_external_turnaround),
        "top_external_non_turnaround_connection_count": len(top_external_non_turnaround),
        "controlled_link_count": len(controlled_links),
        "crossing_count": sum(1 for edge in target_internal_edges if edge.attrib.get("function") == "crossing"),
        "walkingarea_count": sum(1 for edge in target_internal_edges if edge.attrib.get("function") == "walkingarea"),
        "connection_records": records,
    }


def write_connection_signature(signature: dict[str, Any], output_dir: Path, prefix: str) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    signature_file = output_dir / f"{prefix}_connection_signature.json"
    records_file = output_dir / f"{prefix}_connection_records_layered.csv"
    top_external_file = output_dir / f"{prefix}_top_external_connections.csv"

    records = list(signature.get("connection_records", []) or [])
    signature_file.write_text(json.dumps(signature, indent=2, ensure_ascii=False), encoding="utf-8")
    _write_csv(
        records_file,
        CONNECTION_RECORD_FIELDS,
        records,
    )
    _write_csv(
        top_external_file,
        [field for field in CONNECTION_RECORD_FIELDS if field != "category"],
        [record for record in records if record.get("category") == "top_external"],
    )
    return {
        "status": "pass",
        "claim_status": "diagnostic-demo",
        "signature_file": str(signature_file),
        "records_file": str(records_file),
        "top_external_file": str(top_external_file),
    }


def compare_tls_movement_signatures(
    teacher_net_file: Path,
    candidate_net_file: Path,
    teacher_tls_id: str,
    candidate_tls_id: str,
    *,
    teacher_edge_map: dict[str, str] | None = None,
    teacher_internal_scope_id: str | None = None,
    candidate_internal_scope_id: str | None = None,
) -> dict[str, Any]:
    teacher_root = ET.parse(teacher_net_file).getroot()
    candidate_root = ET.parse(candidate_net_file).getroot()
    teacher_signatures = Counter(
        _tls_movement_signatures(
            teacher_root,
            teacher_tls_id,
            teacher_edge_map,
            internal_scope_id=teacher_internal_scope_id,
        )
    )
    candidate_signatures = Counter(
        _tls_movement_signatures(
            candidate_root,
            candidate_tls_id,
            internal_scope_id=candidate_internal_scope_id,
        )
    )
    teacher_only = teacher_signatures - candidate_signatures
    candidate_only = candidate_signatures - teacher_signatures

    teacher_phase_states = _tls_phase_states(teacher_root, teacher_tls_id)
    candidate_phase_states = _tls_phase_states(candidate_root, candidate_tls_id)
    movement_equal = not teacher_only and not candidate_only
    phase_equal = teacher_phase_states == candidate_phase_states
    return {
        "status": "pass" if movement_equal and phase_equal else "fail",
        "claim_status": "diagnostic-demo",
        "teacher_net_file": str(teacher_net_file),
        "candidate_net_file": str(candidate_net_file),
        "teacher_tls_id": teacher_tls_id,
        "candidate_tls_id": candidate_tls_id,
        "teacher_edge_map": dict(sorted((teacher_edge_map or {}).items())),
        "normalized_internal_ids": True,
        "scoped_internal_ids": {
            "teacher": teacher_internal_scope_id or "",
            "candidate": candidate_internal_scope_id or "",
        },
        "compared_fields": TLS_MOVEMENT_SIGNATURE_FIELDS,
        "teacher_connection_count": teacher_signatures.total(),
        "candidate_connection_count": candidate_signatures.total(),
        "movement_signature_equal_after_internal_id_normalization": movement_equal,
        "teacher_only_normalized_movement_signatures": _counter_elements(teacher_only),
        "candidate_only_normalized_movement_signatures": _counter_elements(candidate_only),
        "teacher_phase_states": teacher_phase_states,
        "candidate_phase_states": candidate_phase_states,
        "teacher_phase_count": len(teacher_phase_states),
        "candidate_phase_count": len(candidate_phase_states),
        "phase_state_lengths": {
            "teacher": [len(state) for state in teacher_phase_states],
            "candidate": [len(state) for state in candidate_phase_states],
        },
        "tl_logic_phase_states_equal": phase_equal,
    }


def compare_tls_via_path_semantics(
    teacher_net_file: Path,
    candidate_net_file: Path,
    teacher_tls_id: str,
    candidate_tls_id: str,
    *,
    teacher_edge_map: dict[str, str] | None = None,
    teacher_internal_scope_id: str | None = None,
    candidate_internal_scope_id: str | None = None,
    shape_tolerance_m: float = 12.0,
    length_tolerance_m: float = 8.0,
) -> dict[str, Any]:
    """Compare TLS movements without treating SUMO's internal-edge suffix as stable.

    ``netconvert`` may renumber internal edge suffixes while preserving the
    same signal link.  This audit therefore keeps the strict movement key
    (mapped endpoints, lanes, linkIndex, dir, and state), then validates the
    referenced ``via`` lane by transformed geometry and length.  A changed
    linkIndex, direction, state, missing via lane, or materially different via
    path remains a failure.
    """

    teacher_root = ET.parse(teacher_net_file).getroot()
    candidate_root = ET.parse(candidate_net_file).getroot()
    teacher_scope = teacher_internal_scope_id or teacher_tls_id
    candidate_scope = candidate_internal_scope_id or candidate_tls_id
    teacher_prefix = f":{teacher_scope}_"
    candidate_prefix = f":{candidate_scope}_"
    teacher_edge_map = teacher_edge_map or {}

    def junction_xy(root: ET.Element, junction_id: str) -> tuple[float, float]:
        junction = root.find(f"junction[@id='{junction_id}']")
        if junction is None:
            return 0.0, 0.0
        try:
            return float(junction.attrib.get("x", "0")), float(junction.attrib.get("y", "0"))
        except (TypeError, ValueError):
            return 0.0, 0.0

    teacher_x, teacher_y = junction_xy(teacher_root, teacher_tls_id)
    candidate_x, candidate_y = junction_xy(candidate_root, candidate_tls_id)
    dx = candidate_x - teacher_x
    dy = candidate_y - teacher_y

    def parse_shape(value: str) -> list[tuple[float, float]]:
        points: list[tuple[float, float]] = []
        for token in value.split():
            parts = token.split(",")
            if len(parts) < 2:
                continue
            try:
                points.append((float(parts[0]), float(parts[1])))
            except ValueError:
                continue
        return points

    def shape_distance(source: list[tuple[float, float]], target: list[tuple[float, float]]) -> float | None:
        if not source or not target:
            return None

        def directed(points: list[tuple[float, float]], other: list[tuple[float, float]]) -> float:
            return max(
                min(math.hypot(point[0] - candidate[0], point[1] - candidate[1]) for candidate in other)
                for point in points
            )

        translated = [(x + dx, y + dy) for x, y in source]
        return max(directed(translated, target), directed(target, translated))

    def scoped_connections(root: ET.Element, tls_id: str, scope_prefix: str) -> list[ET.Element]:
        return [
            connection
            for connection in root.findall("connection")
            if connection.attrib.get("tl") == tls_id
            and connection.attrib.get("linkIndex")
            and (
                not scope_prefix
                or connection.attrib.get("via", "").startswith(scope_prefix)
            )
        ]

    def normalize_endpoint(value: str, edge_map: dict[str, str], scope_id: str) -> str:
        if value.startswith(f":{scope_id}"):
            return f":TARGET{value[len(scope_id) + 1:]}"
        return edge_map.get(value, value)

    def movement_key(connection: ET.Element, edge_map: dict[str, str], scope_id: str) -> tuple[str, ...]:
        return (
            normalize_endpoint(connection.attrib.get("from", ""), edge_map, scope_id),
            normalize_endpoint(connection.attrib.get("to", ""), edge_map, scope_id),
            connection.attrib.get("fromLane", ""),
            connection.attrib.get("toLane", ""),
            connection.attrib.get("linkIndex", ""),
            connection.attrib.get("dir", ""),
            connection.attrib.get("state", ""),
        )

    def internal_lanes(root: ET.Element, scope_prefix: str) -> dict[str, tuple[ET.Element, ET.Element]]:
        lanes: dict[str, tuple[ET.Element, ET.Element]] = {}
        for edge in root.findall("edge"):
            edge_id = edge.attrib.get("id", "")
            if not edge_id.startswith(scope_prefix):
                continue
            for lane in edge.findall("lane"):
                lane_id = lane.attrib.get("id", "")
                if lane_id:
                    lanes[lane_id] = (edge, lane)
        return lanes

    teacher_connections = scoped_connections(teacher_root, teacher_tls_id, teacher_prefix)
    candidate_connections = scoped_connections(candidate_root, candidate_tls_id, candidate_prefix)
    teacher_by_key: dict[tuple[str, ...], list[ET.Element]] = {}
    candidate_by_key: dict[tuple[str, ...], list[ET.Element]] = {}
    for connection in teacher_connections:
        teacher_by_key.setdefault(movement_key(connection, teacher_edge_map, teacher_scope), []).append(connection)
    for connection in candidate_connections:
        candidate_by_key.setdefault(movement_key(connection, {}, candidate_scope), []).append(connection)

    missing_keys = []
    extra_keys = []
    via_checks: list[dict[str, Any]] = []
    teacher_lanes = internal_lanes(teacher_root, teacher_prefix)
    candidate_lanes = internal_lanes(candidate_root, candidate_prefix)
    for key, teacher_items in sorted(teacher_by_key.items()):
        candidate_items = candidate_by_key.get(key, [])
        if len(candidate_items) < len(teacher_items):
            missing_keys.extend([key] * (len(teacher_items) - len(candidate_items)))
        for index, teacher_connection in enumerate(teacher_items):
            if index >= len(candidate_items):
                continue
            candidate_connection = candidate_items[index]
            teacher_via = teacher_connection.attrib.get("via", "")
            candidate_via = candidate_connection.attrib.get("via", "")
            teacher_via_record = teacher_lanes.get(teacher_via)
            candidate_via_record = candidate_lanes.get(candidate_via)
            check: dict[str, Any] = {
                "linkIndex": teacher_connection.attrib.get("linkIndex", ""),
                "teacher_via": teacher_via,
                "candidate_via": candidate_via,
                "teacher_via_present": teacher_via_record is not None,
                "candidate_via_present": candidate_via_record is not None,
            }
            if teacher_via_record is not None and candidate_via_record is not None:
                teacher_lane = teacher_via_record[1]
                candidate_lane = candidate_via_record[1]
                delta = shape_distance(
                    parse_shape(teacher_lane.attrib.get("shape", "")),
                    parse_shape(candidate_lane.attrib.get("shape", "")),
                )
                check["shape_delta_m"] = None if delta is None else round(delta, 6)
                try:
                    length_delta = abs(
                        float(candidate_lane.attrib.get("length", "0"))
                        - float(teacher_lane.attrib.get("length", "0"))
                    )
                except (TypeError, ValueError):
                    length_delta = None
                check["length_delta_m"] = None if length_delta is None else round(length_delta, 6)
                check["path_within_tolerance"] = (
                    delta is not None
                    and delta <= shape_tolerance_m
                    and length_delta is not None
                    and length_delta <= length_tolerance_m
                )
            else:
                check["shape_delta_m"] = None
                check["length_delta_m"] = None
                check["path_within_tolerance"] = False
            via_checks.append(check)
    for key, candidate_items in sorted(candidate_by_key.items()):
        teacher_items = teacher_by_key.get(key, [])
        if len(candidate_items) > len(teacher_items):
            extra_keys.extend([key] * (len(candidate_items) - len(teacher_items)))

    phase_states_teacher = _tls_phase_states(teacher_root, teacher_tls_id)
    phase_states_candidate = _tls_phase_states(candidate_root, candidate_tls_id)
    missing_via_count = sum(1 for check in via_checks if not check["candidate_via_present"])
    path_failure_count = sum(1 for check in via_checks if not check["path_within_tolerance"])
    movement_equal = not missing_keys and not extra_keys
    phase_equal = phase_states_teacher == phase_states_candidate
    via_geometry_status = "pass" if not path_failure_count else "needs_review"
    status = (
        "pass"
        if movement_equal and phase_equal and not missing_via_count and not path_failure_count
        else "fail"
    )
    return {
        "status": status,
        "claim_status": "diagnostic-demo",
        "teacher_net_file": str(teacher_net_file),
        "candidate_net_file": str(candidate_net_file),
        "teacher_tls_id": teacher_tls_id,
        "candidate_tls_id": candidate_tls_id,
        "teacher_edge_map": dict(sorted(teacher_edge_map.items())),
        "compared_fields": ["from", "to", "fromLane", "toLane", "linkIndex", "dir", "state", "via_path"],
        "teacher_connection_count": len(teacher_connections),
        "candidate_connection_count": len(candidate_connections),
        "movement_key_equal": movement_equal,
        "teacher_only_movement_keys": [list(key) for key in missing_keys],
        "candidate_only_movement_keys": [list(key) for key in extra_keys],
        "via_checks": via_checks,
        "missing_via_count": missing_via_count,
        "via_path_failure_count": path_failure_count,
        "via_geometry_status": via_geometry_status,
        "shape_tolerance_m": shape_tolerance_m,
        "length_tolerance_m": length_tolerance_m,
        "teacher_phase_states": phase_states_teacher,
        "candidate_phase_states": phase_states_candidate,
        "tl_logic_phase_states_equal": phase_equal,
        "normalized_translation": {"dx": round(dx, 6), "dy": round(dy, 6)},
    }


def compare_shared_tls_via_path_semantics(
    teacher_net_file: Path,
    candidate_net_file: Path,
    teacher_tls_id: str,
    candidate_tls_id: str,
    *,
    owner_map: dict[str, str],
    teacher_edge_map: dict[str, str] | None = None,
    shape_tolerance_m: float = 12.0,
    length_tolerance_m: float = 8.0,
) -> dict[str, Any]:
    """Compare a shared controller by linkIndex across multiple ``via`` owners.

    ``compare_tls_via_path_semantics`` intentionally scopes to one internal
    prefix.  A shared SUMO controller has one tlLogic but several prefixes, so
    this variant normalizes each reference owner to its explicit candidate
    owner and pairs the resulting path by stable linkIndex.
    """

    teacher_root = ET.parse(teacher_net_file).getroot()
    candidate_root = ET.parse(candidate_net_file).getroot()
    clean_owner_map = {
        str(key): str(value)
        for key, value in (owner_map or {}).items()
        if str(key) and str(value)
    }
    edge_map = {
        str(key): str(value)
        for key, value in (teacher_edge_map or {}).items()
        if str(key) and str(value)
    }
    teacher_owner_ids = sorted(clean_owner_map, key=len, reverse=True)

    def owner_for(value: str) -> str:
        for owner_id in teacher_owner_ids:
            if value.startswith(f":{owner_id}_"):
                return owner_id
        return ""

    def map_ref(value: str) -> str:
        owner_id = owner_for(value)
        if owner_id:
            prefix = f":{owner_id}_"
            return f":{clean_owner_map[owner_id]}_{value[len(prefix):]}"
        return edge_map.get(value, value)

    def xy(root: ET.Element, junction_id: str) -> tuple[float, float]:
        junction = root.find(f"junction[@id='{junction_id}']")
        if junction is None:
            return 0.0, 0.0
        try:
            return float(junction.attrib.get("x", "0")), float(junction.attrib.get("y", "0"))
        except (TypeError, ValueError):
            return 0.0, 0.0

    def points(value: str) -> list[tuple[float, float]]:
        result: list[tuple[float, float]] = []
        for token in value.split():
            parts = token.split(",")
            if len(parts) < 2:
                continue
            try:
                result.append((float(parts[0]), float(parts[1])))
            except ValueError:
                continue
        return result

    def directed_distance(source: list[tuple[float, float]], target: list[tuple[float, float]]) -> float:
        return max(
            min(math.hypot(point[0] - candidate[0], point[1] - candidate[1]) for candidate in target)
            for point in source
        )

    def path_distance(
        source: list[tuple[float, float]],
        target: list[tuple[float, float]],
        dx: float,
        dy: float,
    ) -> float | None:
        if not source or not target:
            return None
        translated = [(x + dx, y + dy) for x, y in source]
        return max(directed_distance(translated, target), directed_distance(target, translated))

    teacher_connections = [
        connection
        for connection in teacher_root.findall("connection")
        if connection.attrib.get("tl") == teacher_tls_id and connection.attrib.get("linkIndex") is not None
    ]
    candidate_connections = [
        connection
        for connection in candidate_root.findall("connection")
        if connection.attrib.get("tl") == candidate_tls_id and connection.attrib.get("linkIndex") is not None
    ]
    teacher_key_counts = Counter(
        (
            map_ref(connection.attrib.get("from", "")),
            map_ref(connection.attrib.get("to", "")),
            connection.attrib.get("fromLane", ""),
            connection.attrib.get("toLane", ""),
            connection.attrib.get("linkIndex", ""),
            connection.attrib.get("dir", ""),
            connection.attrib.get("state", ""),
        )
        for connection in teacher_connections
    )
    candidate_key_counts = Counter(
        (
            connection.attrib.get("from", ""),
            connection.attrib.get("to", ""),
            connection.attrib.get("fromLane", ""),
            connection.attrib.get("toLane", ""),
            connection.attrib.get("linkIndex", ""),
            connection.attrib.get("dir", ""),
            connection.attrib.get("state", ""),
        )
        for connection in candidate_connections
    )
    missing_keys = list((teacher_key_counts - candidate_key_counts).elements())
    extra_keys = list((candidate_key_counts - teacher_key_counts).elements())

    teacher_lanes = {
        lane.attrib.get("id", ""): lane
        for edge in teacher_root.findall("edge")
        for lane in edge.findall("lane")
        if lane.attrib.get("id")
    }
    candidate_lanes = {
        lane.attrib.get("id", ""): lane
        for edge in candidate_root.findall("edge")
        for lane in edge.findall("lane")
        if lane.attrib.get("id")
    }
    candidate_by_link_index: dict[str, list[ET.Element]] = {}
    for connection in candidate_connections:
        candidate_by_link_index.setdefault(connection.attrib.get("linkIndex", ""), []).append(connection)
    via_checks: list[dict[str, Any]] = []
    for teacher_connection in teacher_connections:
        link_index = teacher_connection.attrib.get("linkIndex", "")
        candidate_items = candidate_by_link_index.get(link_index, [])
        candidate_connection = candidate_items[0] if candidate_items else None
        teacher_via = teacher_connection.attrib.get("via", "")
        candidate_via = candidate_connection.attrib.get("via", "") if candidate_connection is not None else ""
        owner_id = owner_for(teacher_via) or teacher_tls_id
        candidate_owner_id = clean_owner_map.get(owner_id, candidate_tls_id)
        teacher_via_lane = teacher_lanes.get(teacher_via)
        candidate_via_lane = candidate_lanes.get(candidate_via)
        teacher_xy = xy(teacher_root, owner_id)
        candidate_xy = xy(candidate_root, candidate_owner_id)
        dx = candidate_xy[0] - teacher_xy[0]
        dy = candidate_xy[1] - teacher_xy[1]
        shape_delta = (
            path_distance(
                points(teacher_via_lane.attrib.get("shape", "")),
                points(candidate_via_lane.attrib.get("shape", "")),
                dx,
                dy,
            )
            if teacher_via_lane is not None and candidate_via_lane is not None
            else None
        )
        try:
            length_delta = (
                abs(float(candidate_via_lane.attrib.get("length", "0")) - float(teacher_via_lane.attrib.get("length", "0")))
                if teacher_via_lane is not None and candidate_via_lane is not None
                else None
            )
        except (TypeError, ValueError):
            length_delta = None
        via_checks.append(
            {
                "linkIndex": link_index,
                "teacher_via": teacher_via,
                "candidate_via": candidate_via,
                "owner_id": owner_id,
                "candidate_owner_id": candidate_owner_id,
                "teacher_via_present": teacher_via_lane is not None,
                "candidate_via_present": candidate_via_lane is not None,
                "shape_delta_m": None if shape_delta is None else round(shape_delta, 6),
                "length_delta_m": None if length_delta is None else round(length_delta, 6),
                "path_within_tolerance": (
                    shape_delta is not None
                    and length_delta is not None
                    and shape_delta <= shape_tolerance_m
                    and length_delta <= length_tolerance_m
                ),
            }
        )

    teacher_phases = _tls_phase_states(teacher_root, teacher_tls_id)
    candidate_phases = _tls_phase_states(candidate_root, candidate_tls_id)
    missing_via_count = sum(1 for item in via_checks if not item["candidate_via_present"])
    path_failure_count = sum(1 for item in via_checks if not item["path_within_tolerance"])
    movement_equal = not missing_keys and not extra_keys
    phase_equal = teacher_phases == candidate_phases
    status = "pass" if movement_equal and phase_equal and missing_via_count == 0 and path_failure_count == 0 else "fail"
    return {
        "status": status,
        "claim_status": "diagnostic-demo",
        "teacher_net_file": str(teacher_net_file),
        "candidate_net_file": str(candidate_net_file),
        "teacher_tls_id": teacher_tls_id,
        "candidate_tls_id": candidate_tls_id,
        "owner_map": dict(sorted(clean_owner_map.items())),
        "teacher_edge_map": dict(sorted(edge_map.items())),
        "compared_fields": ["from", "to", "fromLane", "toLane", "linkIndex", "dir", "state", "via_path"],
        "teacher_connection_count": len(teacher_connections),
        "candidate_connection_count": len(candidate_connections),
        "movement_key_equal": movement_equal,
        "teacher_only_movement_keys": [list(key) for key in missing_keys],
        "candidate_only_movement_keys": [list(key) for key in extra_keys],
        "via_checks": via_checks,
        "missing_via_count": missing_via_count,
        "via_path_failure_count": path_failure_count,
        "via_geometry_status": "pass" if path_failure_count == 0 else "needs_review",
        "shape_tolerance_m": shape_tolerance_m,
        "length_tolerance_m": length_tolerance_m,
        "teacher_phase_states": teacher_phases,
        "candidate_phase_states": candidate_phases,
        "tl_logic_phase_states_equal": phase_equal,
    }


def compare_pedestrian_crossing_signatures(
    teacher_net_file: Path,
    candidate_net_file: Path,
    teacher_owner_id: str,
    candidate_owner_id: str,
    *,
    teacher_edge_map: dict[str, str] | None = None,
) -> dict[str, Any]:
    teacher_root = ET.parse(teacher_net_file).getroot()
    candidate_root = ET.parse(candidate_net_file).getroot()
    teacher_edge_signatures, teacher_connection_signatures = _pedestrian_crossing_signatures(
        teacher_root,
        teacher_owner_id,
        teacher_edge_map,
    )
    candidate_edge_signatures, candidate_connection_signatures = _pedestrian_crossing_signatures(
        candidate_root,
        candidate_owner_id,
        None,
    )
    teacher_edges = Counter(teacher_edge_signatures)
    candidate_edges = Counter(candidate_edge_signatures)
    teacher_connections = Counter(teacher_connection_signatures)
    candidate_connections = Counter(candidate_connection_signatures)
    teacher_only_edges = teacher_edges - candidate_edges
    candidate_only_edges = candidate_edges - teacher_edges
    teacher_only_connections = teacher_connections - candidate_connections
    candidate_only_connections = candidate_connections - teacher_connections
    equal = (
        not teacher_only_edges
        and not candidate_only_edges
        and not teacher_only_connections
        and not candidate_only_connections
    )
    return {
        "status": "pass" if equal else "fail",
        "claim_status": "diagnostic-demo",
        "teacher_net_file": str(teacher_net_file),
        "candidate_net_file": str(candidate_net_file),
        "teacher_owner_id": teacher_owner_id,
        "candidate_owner_id": candidate_owner_id,
        "teacher_edge_map": dict(sorted((teacher_edge_map or {}).items())),
        "normalized_internal_ids": True,
        "compared_connection_fields": PEDESTRIAN_CONNECTION_SIGNATURE_FIELDS,
        "teacher_edge_signature_count": teacher_edges.total(),
        "candidate_edge_signature_count": candidate_edges.total(),
        "teacher_connection_signature_count": teacher_connections.total(),
        "candidate_connection_signature_count": candidate_connections.total(),
        "teacher_only_normalized_edge_signatures": _counter_elements(teacher_only_edges),
        "candidate_only_normalized_edge_signatures": _counter_elements(candidate_only_edges),
        "teacher_only_normalized_connection_signatures": _counter_elements(teacher_only_connections),
        "candidate_only_normalized_connection_signatures": _counter_elements(candidate_only_connections),
    }


def _pedestrian_crossing_signatures(
    root: ET.Element,
    owner_id: str,
    edge_map: dict[str, str] | None,
) -> tuple[list[str], list[str]]:
    internal_prefix = f":{owner_id}_"
    pedestrian_edge_ids = {
        edge.attrib["id"]
        for edge in root.findall("edge")
        if edge.attrib.get("id", "").startswith(internal_prefix)
        and edge.attrib.get("function") in PEDESTRIAN_EDGE_FUNCTIONS
    }
    pedestrian_edge_signatures = [
        _pedestrian_edge_signature(edge, owner_id)
        for edge in root.findall("edge")
        if edge.attrib.get("id") in pedestrian_edge_ids
    ]
    pedestrian_connection_signatures = []
    for connection in root.findall("connection"):
        source = connection.attrib.get("from", "")
        target = connection.attrib.get("to", "")
        via = connection.attrib.get("via", "")
        if not (
            source in pedestrian_edge_ids
            or target in pedestrian_edge_ids
            or via.startswith(internal_prefix)
            and _edge_id_from_lane_id(via) in pedestrian_edge_ids
        ):
            continue
        fields = {
            field: _normalize_pedestrian_crossing_value(
                field,
                connection.attrib.get(field, ""),
                owner_id,
                edge_map,
            )
            for field in PEDESTRIAN_CONNECTION_SIGNATURE_FIELDS
        }
        pedestrian_connection_signatures.append(
            "|".join(f"{field}={fields[field]}" for field in PEDESTRIAN_CONNECTION_SIGNATURE_FIELDS)
        )
    return sorted(pedestrian_edge_signatures), sorted(pedestrian_connection_signatures)


def _pedestrian_edge_signature(edge: ET.Element, owner_id: str) -> str:
    lane_signatures = []
    for lane in edge.findall("lane"):
        lane_signatures.append(
            "|".join(
                (
                    f"index={lane.attrib.get('index', '')}",
                    f"allow={lane.attrib.get('allow', '')}",
                    f"disallow={lane.attrib.get('disallow', '')}",
                )
            )
        )
    return "|".join(
        (
            f"id={_normalize_tls_internal_id(edge.attrib.get('id', ''), owner_id)}",
            f"function={edge.attrib.get('function', '')}",
            f"lanes={';'.join(sorted(lane_signatures))}",
        )
    )


def _normalize_pedestrian_crossing_value(
    field: str,
    value: str,
    owner_id: str,
    edge_map: dict[str, str] | None,
) -> str:
    if field in {"from", "to"}:
        if value.startswith(":"):
            return _normalize_tls_internal_id(value, owner_id)
        return (edge_map or {}).get(value, value)
    if field == "via":
        return _normalize_tls_internal_id(value, owner_id)
    if field == "tl" and value == owner_id:
        return "TARGET_TLS"
    return value


def _edge_id_from_lane_id(lane_id: str) -> str:
    if lane_id.startswith(":"):
        parts = lane_id.rsplit("_", 1)
        return parts[0] if len(parts) == 2 else lane_id
    return lane_id.rsplit("_", 1)[0] if "_" in lane_id else lane_id


def _is_related(
    source: str,
    target: str,
    via: str,
    incoming: set[str],
    outgoing: set[str],
    internal_prefix: str,
) -> bool:
    return (
        via.startswith(internal_prefix)
        or source in incoming
        or target in outgoing
        or source.startswith(internal_prefix)
        or target.startswith(internal_prefix)
    )


def _connection_category(
    source: str,
    target: str,
    incoming: set[str],
    outgoing: set[str],
    internal_prefix: str,
) -> str:
    if source in incoming and target in outgoing:
        return "top_external"
    if source in incoming:
        return "incoming_to_internal_or_other"
    if target in outgoing:
        return "internal_or_other_to_outgoing"
    if source.startswith(internal_prefix) or target.startswith(internal_prefix):
        return "internal_continuation"
    return "other_related"


def _is_turnaround_record(record: dict[str, Any]) -> bool:
    return str(record.get("dir", "")).lower() == TURNAROUND_DIR


def _compare_owner_connection_signatures(
    teacher_signature: dict[str, Any],
    candidate_signature: dict[str, Any],
) -> dict[str, Any]:
    fields = (
        "top_external_connection_count",
        "top_external_non_turnaround_connection_count",
        "top_external_turnaround_connection_count",
        "controlled_link_count",
        "crossing_count",
        "walkingarea_count",
    )
    deltas = {
        field: int(candidate_signature.get(field, 0)) - int(teacher_signature.get(field, 0))
        for field in fields
    }
    turnaround_only = (
        int(teacher_signature.get("top_external_non_turnaround_connection_count", 0)) > 0
        and int(candidate_signature.get("top_external_non_turnaround_connection_count", 0)) == 0
        and int(candidate_signature.get("top_external_turnaround_connection_count", 0)) > 0
    )
    return {
        "status": "fail" if any(deltas.values()) or turnaround_only else "pass",
        "teacher": {field: teacher_signature.get(field, 0) for field in fields},
        "candidate": {field: candidate_signature.get(field, 0) for field in fields},
        "count_delta": deltas,
        "candidate_turnaround_only_top_external": turnaround_only,
    }


def _has_tls_semantics(net_file: Path, tls_id: str) -> bool:
    root = ET.parse(net_file).getroot()
    return root.find(f"tlLogic[@id='{tls_id}']") is not None or any(
        connection.attrib.get("tl") == tls_id for connection in root.findall("connection")
    )


def _write_csv(path: Path, fieldnames: list[str], rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _tls_movement_signatures(
    root: ET.Element,
    tls_id: str,
    edge_map: dict[str, str] | None = None,
    *,
    internal_scope_id: str | None = None,
) -> list[str]:
    signatures = []
    normalize_internal_id = internal_scope_id or tls_id
    internal_scope_prefix = f":{internal_scope_id}_" if internal_scope_id else ""
    for connection in root.findall("connection"):
        if connection.attrib.get("tl") != tls_id:
            continue
        if internal_scope_prefix and not connection.attrib.get("via", "").startswith(internal_scope_prefix):
            continue
        fields = {
            field: _normalize_tls_movement_value(field, connection.attrib.get(field, ""), normalize_internal_id, edge_map)
            for field in TLS_MOVEMENT_SIGNATURE_FIELDS
        }
        signatures.append("|".join(f"{field}={fields[field]}" for field in TLS_MOVEMENT_SIGNATURE_FIELDS))
    return signatures


def _normalize_tls_movement_value(
    field: str,
    value: str,
    tls_id: str,
    edge_map: dict[str, str] | None,
) -> str:
    if field in {"from", "to"} and edge_map and value and not value.startswith(":"):
        return edge_map.get(value, value)
    return _normalize_tls_internal_id(value, tls_id)


def _normalize_tls_internal_id(value: str, tls_id: str) -> str:
    prefix = f":{tls_id}"
    if value.startswith(prefix):
        return f":TARGET{value[len(prefix):]}"
    return value


def _tls_phase_states(root: ET.Element, tls_id: str) -> list[str]:
    states = []
    for tl_logic in root.findall("tlLogic"):
        if tl_logic.attrib.get("id") != tls_id:
            continue
        states.extend(phase.attrib.get("state", "") for phase in tl_logic.findall("phase"))
    return states


def _counter_elements(counter: Counter[str]) -> list[str]:
    return sorted(counter.elements())
