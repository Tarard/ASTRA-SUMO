"""Bind frozen Hamburg count fields to an aerial SUMO candidate."""

from __future__ import annotations

import csv
import json
import xml.etree.ElementTree as ET
from collections.abc import Mapping, Sequence
from dataclasses import replace
from pathlib import Path
from typing import Any

from .candidate_contracts import file_sha256
from .digital_twin import CountStream, MapLane, parse_mapem
from .digital_twin_mapping import (
    bind_count_streams_to_network,
    bind_map_lanes_to_network,
    write_detector_mapping,
)

REQUEST_SCHEMA = "torii.hamburg-aerial-count-binding-request/v1"
REPORT_SCHEMA = "torii.hamburg-aerial-count-binding/v1"

HAMBURG_PUBLIC_COUNT_INFORMATION = {
    "publisher": "Freie und Hansestadt Hamburg, Behörde für Verkehr und Mobilitätswende",
    "dataset": "Verkehrsdaten Kfz (Infrarotdetektoren) Hamburg",
    "dataset_url": (
        "https://metaver.de/trefferanzeige?"
        "docuuid=2936465E-C045-4F5D-8614-24C3FBB522E2"
    ),
    "service_url": "https://iot.hamburg.de/v1.1/",
    "published_rules": [
        "Zählfeld streams are individual five-minute count fields.",
        "Zählstelle streams are processed count stations composed from named Zählfeld fields.",
        "The Zählstelle zusammensetzung property is the published membership list.",
        "Direction 1 and 2 station streams are directional totals; direction 0 is a total for QA.",
    ],
    "torii_interpretation": [
        "A Z.* asset number is not a MAP, TLD, or SUMO lane number.",
        "Only fields named by one directional station composition may constrain demand.",
        "Missing lane-use metadata must not be replaced by a guessed turn movement.",
    ],
}


def reconcile_count_binding_groups(
    mappings: Sequence[Any],
) -> tuple[list[Any], list[dict[str, Any]]]:
    """Block mixed directions and describe allowed many-to-one compression."""
    grouped: dict[tuple[str, str], list[int]] = {}
    result = list(mappings)
    for index, mapping in enumerate(result):
        if mapping.mapping_status == "active":
            grouped.setdefault((str(mapping.node_id), str(mapping.sumo_lane)), []).append(index)
    reports = []
    for (node_id, sumo_lane), indices in sorted(grouped.items()):
        if len(indices) < 2:
            continue
        rows = [result[index] for index in indices]
        directions = sorted(
            {str(row.real_direction).strip() for row in rows if str(row.real_direction).strip()}
        )
        map_lanes = sorted(
            {str(row.official_map_lane).strip() for row in rows if str(row.official_map_lane).strip()}
        )
        if len(directions) > 1:
            group_status = "mixed_direction"
            for index in indices:
                result[index] = replace(
                    result[index],
                    mapping_status="needs_review",
                    mapping_reason=(
                        f"{result[index].mapping_reason}; mixed count directions share one SUMO lane"
                    ),
                )
        elif len(map_lanes) > 1:
            group_status = "compressed"
        else:
            group_status = "multi_field"
        reports.append(
            {
                "node_id": node_id,
                "sumo_lane": sumo_lane,
                "stream_ids": [int(row.stream_id) for row in rows],
                "directions": directions,
                "official_map_lanes": map_lanes,
                "group_status": group_status,
                "effective_official_lane_count": max(len(map_lanes), 1),
            }
        )
    return result, reports


def enforce_official_station_compositions(
    mappings: Sequence[Any],
    field_streams: Sequence[CountStream],
    station_streams: Sequence[CountStream],
) -> tuple[list[Any], list[dict[str, Any]]]:
    """Keep only complete directional groups published in ``zusammensetzung``."""

    fields = {_field_key(row.node_id, row.asset_id): row for row in field_streams}
    if len(fields) != len(field_streams):
        raise ValueError("count field inventory repeats node and asset identity")
    mapping_by_stream = {int(row.stream_id): row for row in mappings}
    if len(mapping_by_stream) != len(mappings):
        raise ValueError("count mapping repeats stream_id")
    directional = [row for row in station_streams if row.direction_code in {"1", "2"}]
    if not directional:
        raise ValueError("count station inventory has no directional station compositions")

    memberships: dict[int, list[int]] = {}
    resolved_groups: list[tuple[CountStream, list[CountStream], list[str]]] = []
    for station in directional:
        members = []
        missing = []
        for key in station.composition:
            field = fields.get(_normalized_composition_key(key))
            if field is None:
                missing.append(key)
                continue
            members.append(field)
            memberships.setdefault(field.stream_id, []).append(station.stream_id)
        resolved_groups.append((station, members, missing))

    result = [
        replace(
            row,
            mapping_status="needs_review",
            mapping_reason=(
                f"{row.mapping_reason}; not admitted by a complete official directional "
                "Zählstelle composition"
            ),
        )
        if row.mapping_status == "active"
        else row
        for row in mappings
    ]
    result_index = {int(row.stream_id): index for index, row in enumerate(result)}
    groups: list[dict[str, Any]] = []
    for station, members, missing in resolved_groups:
        member_mappings = [mapping_by_stream.get(row.stream_id) for row in members]
        repeated = sorted(
            row.stream_id for row in members if len(memberships.get(row.stream_id, ())) != 1
        )
        active_mappings = [
            row for row in member_mappings if row is not None and row.mapping_status == "active"
        ]
        all_edges = sorted(
            {str(row.sumo_edge) for row in member_mappings if row is not None and row.sumo_edge}
        )
        if missing:
            status = "missing_field"
        elif repeated:
            status = "overlapping_membership"
        elif len(all_edges) != 1:
            status = "split_edge"
        elif len(active_mappings) != len(members):
            status = "incomplete_binding"
        else:
            status = "active"
            for field, mapping in zip(members, member_mappings, strict=True):
                index = result_index[field.stream_id]
                result[index] = replace(
                    mapping,
                    real_direction=station.direction,
                    mapping_reason=(
                        f"{mapping.mapping_reason}; official station {station.asset_id} "
                        f"direction {station.direction_code} composition member"
                    ),
                )
        groups.append(
            {
                "station_stream_id": station.stream_id,
                "station_id": station.asset_id,
                "node_id": station.node_id,
                "station_arm": station.station_arm,
                "direction_code": station.direction_code,
                "direction": station.direction,
                "composition": list(station.composition),
                "member_stream_ids": [row.stream_id for row in members],
                "missing_composition_members": missing,
                "overlapping_member_stream_ids": repeated,
                "sumo_edges": all_edges,
                "group_status": status,
                "aggregation": "sum_official_zusammensetzung",
            }
        )
    return result, groups


def _field_key(node_id: str, asset_id: str) -> str:
    node = str(node_id).strip()
    if not node.isdecimal():
        raise ValueError(f"count field node id must be decimal: {node_id!r}")
    return f"{int(node):04d}-{str(asset_id).strip()}"


def _normalized_composition_key(value: str) -> str:
    node, separator, asset = str(value).strip().partition("-")
    if separator != "-" or not node.isdecimal() or not asset:
        raise ValueError(f"invalid official count composition member: {value!r}")
    return f"{int(node):04d}-{asset}"


def resolve_station_group_constraint_edges(
    net_file: Path,
    groups: Sequence[Mapping[str, Any]],
    mappings: Sequence[Any],
) -> list[dict[str, Any]]:
    """Resolve a station group to one direct or uniquely adjacent SUMO edge."""

    root = ET.parse(net_file).getroot()
    edge_ids = {
        edge.attrib["id"]
        for edge in root.findall("edge")
        if edge.attrib.get("id") and not edge.attrib["id"].startswith(":")
    }
    predecessors: dict[str, set[str]] = {}
    successors: dict[str, set[str]] = {}
    for connection in root.findall("connection"):
        source = connection.attrib.get("from", "")
        target = connection.attrib.get("to", "")
        if source in edge_ids and target in edge_ids:
            successors.setdefault(source, set()).add(target)
            predecessors.setdefault(target, set()).add(source)
    mapping_by_stream = {int(row.stream_id): row for row in mappings}
    result = []
    for source_group in groups:
        group = dict(source_group)
        member_edges = {
            str(mapping_by_stream[int(stream_id)].sumo_edge)
            for stream_id in group.get("member_stream_ids", [])
            if int(stream_id) in mapping_by_stream
            and str(mapping_by_stream[int(stream_id)].sumo_edge)
        }
        constraint_edge = ""
        resolution = ""
        if len(member_edges) == 1:
            constraint_edge = next(iter(member_edges))
            resolution = "direct_member_edge"
        elif len(member_edges) > 1:
            common_predecessors = set.intersection(
                *(predecessors.get(edge_id, set()) for edge_id in member_edges)
            )
            common_successors = set.intersection(
                *(successors.get(edge_id, set()) for edge_id in member_edges)
            )
            candidates = [
                ("unique_common_predecessor", next(iter(common_predecessors)))
                for _ in [0]
                if len(common_predecessors) == 1 and not common_successors
            ]
            candidates += [
                ("unique_common_successor", next(iter(common_successors)))
                for _ in [0]
                if len(common_successors) == 1 and not common_predecessors
            ]
            if len(candidates) == 1:
                resolution, constraint_edge = candidates[0]
        if constraint_edge:
            group["group_status"] = "active"
            group["constraint_edge"] = constraint_edge
            group["constraint_resolution"] = resolution
        else:
            group["constraint_edge"] = None
            group["constraint_resolution"] = "unresolved"
        result.append(group)
    return result


def build_hamburg_aerial_count_binding(
    *,
    request_file: Path | str,
    output_dir: Path | str,
) -> dict[str, Any]:
    """Audit official count-field lane bindings without generating demand."""
    request_path = Path(request_file).expanduser().resolve()
    request = _load_object(request_path, "request")
    if request.get("schema") != REQUEST_SCHEMA:
        raise ValueError(f"request schema must be {REQUEST_SCHEMA}")
    candidate_manifest = _verified_artifact(request.get("candidate_manifest"), "candidate_manifest")
    count_scope_manifest = _verified_artifact(request.get("count_scope_manifest"), "count_scope_manifest")
    station_inventory_manifest = _verified_artifact(
        request.get("count_station_inventory_manifest"),
        "count_station_inventory_manifest",
    )
    map_files = [
        _verified_artifact(value, f"map_xml_files[{index}]")
        for index, value in enumerate(request.get("map_xml_files", []))
    ]
    if not map_files:
        raise ValueError("request map_xml_files must not be empty")
    destination = Path(output_dir).expanduser().resolve()
    if destination.exists():
        raise ValueError("output_dir must not already exist")

    candidate = _load_object(candidate_manifest, "candidate manifest")
    if candidate.get("schema") != "torii.hamburg-aerial-corridor-candidate/v1":
        raise ValueError("candidate manifest schema is invalid")
    network_record = candidate.get("artifacts", {}).get("network", {})
    network = Path(str(network_record.get("path", ""))).expanduser().resolve(strict=True)
    if file_sha256(network) != str(network_record.get("sha256", "")).lower():
        raise ValueError("candidate network hash does not match its manifest")

    count_scope = _load_object(count_scope_manifest, "count scope manifest")
    if count_scope.get("status") != "pass":
        raise ValueError("count scope manifest is not pass")
    stream_record = count_scope.get("artifacts", {}).get("count_streams_normalized", {})
    stream_file = _verified_artifact(stream_record, "count_streams_normalized")
    streams = _load_count_streams(stream_file)
    if int(count_scope.get("stream_count", -1)) != len(streams):
        raise ValueError("count scope stream_count does not match the normalized snapshot")

    station_inventory = _load_object(station_inventory_manifest, "count station inventory")
    if station_inventory.get("schema") != "torii.hamburg-count-station-inventory/v1":
        raise ValueError("count station inventory schema is invalid")
    if station_inventory.get("execution_gate") != "pass":
        raise ValueError("count station inventory execution gate is not pass")
    station_record = station_inventory.get("artifacts", {}).get(
        "count_station_streams_normalized",
        {},
    )
    station_file = _verified_artifact(station_record, "count_station_streams_normalized")
    station_streams = _load_count_streams(station_file)

    map_lanes: list[MapLane] = []
    for map_file in map_files:
        lanes, _connections = parse_mapem(map_file)
        map_lanes.extend(lanes)
    map_bindings = bind_map_lanes_to_network(network, map_lanes)
    detector_mappings = bind_count_streams_to_network(
        network,
        streams,
        map_lanes,
        map_bindings,
        period=900,
    )
    detector_mappings, _ = enforce_official_station_compositions(
        detector_mappings,
        streams,
        station_streams,
    )
    detector_mappings, lane_binding_groups = reconcile_count_binding_groups(detector_mappings)
    detector_mappings, station_groups = enforce_official_station_compositions(
        detector_mappings,
        streams,
        station_streams,
    )
    station_groups = resolve_station_group_constraint_edges(
        network,
        station_groups,
        detector_mappings,
    )
    mapping_counts = _status_counts(detector_mappings)
    node_counts = {
        node_id: _status_counts(
            [mapping for mapping in detector_mappings if str(mapping.node_id) == node_id]
        )
        for node_id in ("104", "118", "119", "200", "535")
    }

    destination.mkdir(parents=True)
    detector_file = destination / "detector-mapping.csv"
    write_detector_mapping(detector_file, detector_mappings)
    map_binding_file = destination / "official-map-lane-bindings.csv"
    _write_dataclasses(map_binding_file, map_bindings)
    binding_group_file = destination / "count-binding-groups.json"
    binding_group_file.write_text(
        json.dumps(station_groups, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    lane_binding_group_file = destination / "lane-binding-groups.json"
    lane_binding_group_file.write_text(
        json.dumps(lane_binding_groups, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    public_information_file = destination / "hamburg-public-count-information.json"
    public_information_file.write_text(
        json.dumps(HAMBURG_PUBLIC_COUNT_INFORMATION, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    report = {
        "schema": REPORT_SCHEMA,
        "status": "pass" if mapping_counts.get("active", 0) > 0 else "blocked",
        "decision": "review_required",
        "claim_status": "diagnostic-demo",
        "inputs": {
            "request": {"path": str(request_path), "sha256": file_sha256(request_path)},
            "candidate_manifest": {
                "path": str(candidate_manifest),
                "sha256": file_sha256(candidate_manifest),
            },
            "network": {"path": str(network), "sha256": file_sha256(network)},
            "count_scope_manifest": {
                "path": str(count_scope_manifest),
                "sha256": file_sha256(count_scope_manifest),
            },
            "count_streams": {"path": str(stream_file), "sha256": file_sha256(stream_file)},
            "count_station_inventory_manifest": {
                "path": str(station_inventory_manifest),
                "sha256": file_sha256(station_inventory_manifest),
            },
            "count_station_streams": {
                "path": str(station_file),
                "sha256": file_sha256(station_file),
            },
            "map_xml_files": [
                {"path": str(path), "sha256": file_sha256(path)} for path in map_files
            ],
        },
        "selected_window": count_scope.get("selected_window"),
        "counts": {
            "official_count_streams": len(streams),
            "official_count_station_streams": len(station_streams),
            "directional_station_groups": len(station_groups),
            "map_vehicle_lanes": sum(lane.is_vehicle for lane in map_lanes),
            "detector_binding_status": mapping_counts,
            "detector_binding_status_by_node": node_counts,
            "station_group_status": _row_status_counts(station_groups, "group_status"),
            "lane_binding_group_status": _row_status_counts(
                lane_binding_groups,
                "group_status",
            ),
        },
        "gates": {
            "source_hashes": "pass",
            "complete_official_count_window": "pass",
            "active_count_bindings_available": (
                "pass" if mapping_counts.get("active", 0) > 0 else "blocked"
            ),
            "official_station_compositions": (
                "pass"
                if station_groups and all(row["group_status"] == "active" for row in station_groups)
                else "review_required"
            ),
            "all_count_streams_active": (
                "pass" if mapping_counts == {"active": len(streams)} else "review_required"
            ),
            "demand_generation": "not_run",
        },
        "artifacts": {
            "detector_mapping": {
                "path": str(detector_file),
                "sha256": file_sha256(detector_file),
            },
            "map_lane_bindings": {
                "path": str(map_binding_file),
                "sha256": file_sha256(map_binding_file),
            },
            "binding_groups": {
                "path": str(binding_group_file),
                "sha256": file_sha256(binding_group_file),
            },
            "lane_binding_groups": {
                "path": str(lane_binding_group_file),
                "sha256": file_sha256(lane_binding_group_file),
            },
            "hamburg_public_count_information": {
                "path": str(public_information_file),
                "sha256": file_sha256(public_information_file),
            },
        },
        "claim_boundary": (
            "Only complete direction-1/2 Zählstelle compositions may constrain demand. Their published "
            "Zählfeld members are summed; direction-0 totals are QA-only and unlisted fields are excluded. "
            "Nearest-lane fallbacks remain review-only. This stage does not claim a unique OD matrix."
        ),
    }
    manifest_file = destination / "manifest.json"
    manifest_file.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return {**report, "manifest_file": str(manifest_file), "manifest_sha256": file_sha256(manifest_file)}


def _verified_artifact(value: Any, label: str) -> Path:
    if not isinstance(value, Mapping) or not value.get("path") or not value.get("sha256"):
        raise ValueError(f"request {label} must contain path and sha256")
    path = Path(str(value["path"])).expanduser().resolve(strict=True)
    if file_sha256(path) != str(value["sha256"]).lower():
        raise ValueError(f"{label} SHA-256 does not match")
    return path


def _load_object(path: Path, label: str) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a JSON object")
    return value


def _load_count_streams(path: Path) -> list[CountStream]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, list) or not value:
        raise ValueError("normalized count stream snapshot must be a non-empty JSON list")
    streams = []
    for row in value:
        payload = dict(row)
        payload.pop("detector_id", None)
        payload["composition"] = tuple(payload.get("composition", ()))
        streams.append(CountStream(**payload))
    if len({stream.stream_id for stream in streams}) != len(streams):
        raise ValueError("count stream snapshot repeats stream_id")
    return sorted(streams, key=lambda stream: stream.stream_id)


def _status_counts(rows: Sequence[Any]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        status = str(row.mapping_status)
        counts[status] = counts.get(status, 0) + 1
    return dict(sorted(counts.items()))


def _row_status_counts(rows: Sequence[Mapping[str, Any]], key: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        value = str(row[key])
        counts[value] = counts.get(value, 0) + 1
    return dict(sorted(counts.items()))


def _write_dataclasses(path: Path, rows: Sequence[Any]) -> None:
    if not rows:
        raise ValueError("MAP lane binding produced no rows")
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].__dataclass_fields__))
        writer.writeheader()
        for row in rows:
            writer.writerow(row.__dict__)


__all__ = [
    "REPORT_SCHEMA",
    "REQUEST_SCHEMA",
    "build_hamburg_aerial_count_binding",
    "enforce_official_station_compositions",
    "reconcile_count_binding_groups",
    "resolve_station_group_constraint_edges",
]
