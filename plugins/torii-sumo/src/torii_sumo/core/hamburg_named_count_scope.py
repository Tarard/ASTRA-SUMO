from __future__ import annotations

import csv
import json
import math
from dataclasses import dataclass, replace
from datetime import date
from pathlib import Path
from typing import Any, Mapping, Sequence

from .digital_twin import CanonicalCount, CountStream, recent_completed_saturdays, write_canonical_counts
from .digital_twin_timeline import (
    aggregate_simulation_counts,
    rank_complete_simulation_windows,
    select_comparison_count_rows,
)
from .hamburg_official import (
    HAMBURG_COUNT_SERVICE,
    HAMBURG_COUNT_STATION_LAYER,
    SensorThingsClient,
    fetch_hamburg_count_observations,
    fetch_hamburg_count_station_streams,
    fetch_hamburg_count_streams,
    sha256_file,
    write_json,
)


NAMED_COUNT_SCOPE_SCHEMA = "torii.hamburg-named-corridor-count-scope/v1"
COUNT_STATION_INVENTORY_SCHEMA = "torii.hamburg-count-station-inventory/v1"


@dataclass(frozen=True)
class OfficialSignalNodeReference:
    node_id: str
    official_name: str
    longitude: float
    latitude: float


class HamburgNamedCountScopeError(ValueError):
    """Raised when an official detector scope cannot be interpreted safely."""


def _detector_distance_m(first: CountStream, second: CountStream) -> float:
    meters_per_degree_latitude = 111_320.0
    meters_per_degree_longitude = meters_per_degree_latitude * math.cos(
        math.radians((first.latitude + second.latitude) / 2.0)
    )
    return math.hypot(
        (first.longitude - second.longitude) * meters_per_degree_longitude,
        (first.latitude - second.latitude) * meters_per_degree_latitude,
    )


def infer_hamburg_count_directions(
    streams: Sequence[CountStream],
    *,
    max_neighbor_distance_m: float = 12.0,
    minimum_separation_m: float = 2.0,
    minimum_distance_ratio: float = 1.5,
) -> tuple[list[CountStream], list[dict[str, Any]]]:
    """Fill unknown directions only when local detector geometry is decisive."""

    if max_neighbor_distance_m <= 0 or minimum_separation_m < 0 or minimum_distance_ratio < 1:
        raise HamburgNamedCountScopeError("direction inference thresholds are invalid")
    result = list(streams)
    evidence: list[dict[str, Any]] = []
    for index, stream in enumerate(streams):
        if stream.direction.strip() and stream.direction.casefold() not in {"unbekannt", "unknown"}:
            continue
        candidates = [
            (other_index, other, _detector_distance_m(stream, other))
            for other_index, other in enumerate(streams)
            if other_index != index
            and other.node_id == stream.node_id
            and other.direction.strip()
            and other.direction.casefold() not in {"unbekannt", "unknown"}
        ]
        candidates.sort(key=lambda item: (item[2], item[1].stream_id))
        if not candidates or candidates[0][2] > max_neighbor_distance_m:
            evidence.append({"stream_id": stream.stream_id, "status": "unresolved", "reason": "no_close_declared_direction"})
            continue
        nearest = candidates[0]
        second_distance = candidates[1][2] if len(candidates) > 1 else float("inf")
        separated = (
            second_distance - nearest[2] >= minimum_separation_m
            or second_distance / max(nearest[2], 1e-9) >= minimum_distance_ratio
        )
        if not separated:
            evidence.append({"stream_id": stream.stream_id, "status": "unresolved", "reason": "neighbor_directions_ambiguous"})
            continue
        result[index] = replace(stream, direction=nearest[1].direction)
        evidence.append(
            {
                "stream_id": stream.stream_id,
                "status": "inferred",
                "direction": nearest[1].direction,
                "method": "nearest_same_node_declared_direction",
                "reference_stream_id": nearest[1].stream_id,
                "distance_to_reference_m": round(nearest[2], 3),
                "second_nearest_distance_m": None if math.isinf(second_distance) else round(second_distance, 3),
            }
        )
    return result, evidence


def load_lsa_node_references(
    path: Path,
    *,
    expected_node_ids: Sequence[str] | None = None,
) -> dict[str, OfficialSignalNodeReference]:
    """Load point identities from Torii's frozen official Hamburg LSA evidence."""

    source = path.resolve(strict=True)
    payload = json.loads(source.read_text(encoding="utf-8"))
    selections = payload.get("selections")
    if not selections and payload.get("type") == "FeatureCollection":
        selections = []
        for feature in payload.get("features", []):
            properties = feature.get("properties") if isinstance(feature, Mapping) else None
            geometry = feature.get("geometry") if isinstance(feature, Mapping) else None
            coordinates = geometry.get("coordinates") if isinstance(geometry, Mapping) else None
            if not isinstance(properties, Mapping) or str(properties.get("art", "")).strip() != "K-LSA":
                continue
            if isinstance(coordinates, list) and len(coordinates) == 1:
                coordinates = coordinates[0]
            try:
                node_id = str(int(str(properties.get("knoten", "")).strip()))
            except ValueError:
                continue
            selections.append(
                {
                    "selected_node": {
                        "node_id": node_id,
                        "official_name": str(properties.get("LSA_Name", "")).strip(),
                        "point_geometry": {"coordinates": coordinates},
                    }
                }
            )
    if not isinstance(selections, list) or not selections:
        raise HamburgNamedCountScopeError("LSA identity evidence has no selections")
    expected = {str(node_id) for node_id in (expected_node_ids or ())}
    references: dict[str, OfficialSignalNodeReference] = {}
    for selection in selections:
        if not isinstance(selection, Mapping):
            raise HamburgNamedCountScopeError("LSA identity selection is not an object")
        node = selection.get("selected_node")
        if not isinstance(node, Mapping):
            raise HamburgNamedCountScopeError("LSA identity selection has no selected_node")
        node_id = str(node.get("node_id", "")).strip()
        point = node.get("point_geometry")
        coordinates = point.get("coordinates") if isinstance(point, Mapping) else None
        if not node_id or not isinstance(coordinates, list) or len(coordinates) != 2:
            raise HamburgNamedCountScopeError("LSA identity selection has invalid node geometry")
        try:
            longitude, latitude = float(coordinates[0]), float(coordinates[1])
        except (TypeError, ValueError) as exc:
            raise HamburgNamedCountScopeError("LSA identity coordinates are not numeric") from exc
        if not (math.isfinite(longitude) and math.isfinite(latitude)):
            raise HamburgNamedCountScopeError("LSA identity coordinates are not finite")
        if node_id in references:
            raise HamburgNamedCountScopeError(f"LSA identity repeats node {node_id!r}")
        references[node_id] = OfficialSignalNodeReference(
            node_id=node_id,
            official_name=str(node.get("official_name", "")).strip(),
            longitude=longitude,
            latitude=latitude,
        )
    if expected and expected - references.keys():
        raise HamburgNamedCountScopeError(
            "LSA identity evidence is missing requested nodes: "
            + ", ".join(sorted(expected - references.keys()))
        )
    return references


def _distance_m(longitude: float, latitude: float, reference: OfficialSignalNodeReference) -> float:
    """Use a bounded equirectangular distance for detector-to-LSA evidence."""

    meters_per_degree_latitude = 111_320.0
    meters_per_degree_longitude = meters_per_degree_latitude * math.cos(
        math.radians((latitude + reference.latitude) / 2.0)
    )
    return math.hypot(
        (longitude - reference.longitude) * meters_per_degree_longitude,
        (latitude - reference.latitude) * meters_per_degree_latitude,
    )


def build_hamburg_count_scope_evidence(
    streams: Sequence[CountStream],
    *,
    requested_count_node_ids: Sequence[str],
    signal_nodes: Mapping[str, OfficialSignalNodeReference],
    max_distance_m: float = 250.0,
) -> dict[str, Any]:
    """Describe exactly which official detector nodes are present or absent.

    Missing detectors never get replaced by a nearby node implicitly.  Nearby
    geometry is retained as evidence only; the caller must declare any scope
    expansion explicitly.
    """

    requested = tuple(dict.fromkeys(str(node_id).strip() for node_id in requested_count_node_ids))
    if not requested or any(not node_id for node_id in requested):
        raise HamburgNamedCountScopeError("requested_count_node_ids must be non-empty")
    if max_distance_m <= 0 or not math.isfinite(max_distance_m):
        raise HamburgNamedCountScopeError("max_distance_m must be a finite positive value")
    missing_signal_nodes = sorted(set(requested) - set(signal_nodes))
    if missing_signal_nodes:
        raise HamburgNamedCountScopeError(
            "requested count nodes have no declared official reference point: "
            + ", ".join(missing_signal_nodes)
        )
    stream_node_ids = {stream.node_id for stream in streams}
    missing_count_nodes = sorted(set(requested) - stream_node_ids)
    unexpected_count_nodes = sorted(stream_node_ids - set(requested))
    if unexpected_count_nodes:
        raise HamburgNamedCountScopeError(
            "official response returned undeclared count nodes: "
            + ", ".join(unexpected_count_nodes)
        )

    stream_rows: list[dict[str, Any]] = []
    over_distance: list[dict[str, Any]] = []
    for stream in sorted(streams, key=lambda item: (item.node_id, item.asset_id, item.stream_id)):
        reference = signal_nodes[stream.node_id]
        distance = _distance_m(stream.longitude, stream.latitude, reference)
        row = {
            "stream_id": stream.stream_id,
            "detector_id": stream.detector_id,
            "count_node_id": stream.node_id,
            "asset_id": stream.asset_id,
            "direction": stream.direction,
            "lane_use": stream.lane_use,
            "longitude": stream.longitude,
            "latitude": stream.latitude,
            "reference_node_id": reference.node_id,
            "distance_to_reference_m": round(distance, 3),
            "distance_status": "within_declared_scope" if distance <= max_distance_m else "outside_declared_scope",
        }
        stream_rows.append(row)
        if distance > max_distance_m:
            over_distance.append(row)

    unknown_direction_stream_ids = sorted(
        stream.stream_id
        for stream in streams
        if not stream.direction.strip() or stream.direction.casefold() in {"unbekannt", "unknown"}
    )
    complete = not missing_count_nodes and not over_distance
    return {
        "schema": NAMED_COUNT_SCOPE_SCHEMA,
        "status": "pass" if complete else "partial",
        "automatic_promotion_gate": "pass" if complete and not unknown_direction_stream_ids else "blocked",
        "requested_count_node_ids": list(requested),
        "available_count_node_ids": sorted(stream_node_ids),
        "missing_count_node_ids": missing_count_nodes,
        "max_distance_m": max_distance_m,
        "stream_count": len(stream_rows),
        "stream_rows": stream_rows,
        "over_distance_stream_ids": [row["stream_id"] for row in over_distance],
        "unknown_direction_stream_ids": unknown_direction_stream_ids,
        "claim_boundary": {
            "proves": [
                "official_count_stream_inventory",
                "declared_detector_to_official_node_geometry",
                "source_complete_warmup_and_formal_count_window_when_present",
            ],
            "does_not_prove": [
                "SUMO lane binding",
                "OD matrix or unique route reconstruction",
                "signal timing or queue length",
                "full corridor coverage when a requested node is missing",
            ],
        },
    }


def _normalized_stream_rows(streams: Sequence[CountStream]) -> list[dict[str, Any]]:
    return [
        {
            "stream_id": stream.stream_id,
            "thing_id": stream.thing_id,
            "node_id": stream.node_id,
            "asset_id": stream.asset_id,
            "direction": stream.direction,
            "lane_use": stream.lane_use,
            "longitude": stream.longitude,
            "latitude": stream.latitude,
            "operation_start": stream.operation_start,
            "layer_name": stream.layer_name,
            "direction_code": stream.direction_code,
            "station_arm": stream.station_arm,
            "composition": list(stream.composition),
            "detector_id": stream.detector_id,
        }
        for stream in streams
    ]


def audit_hamburg_count_station_compositions(
    streams: Sequence[CountStream],
    *,
    requested_node_ids: Sequence[str],
) -> dict[str, Any]:
    """Verify that official directional station streams reconcile with their total stream."""

    requested = tuple(dict.fromkeys(str(node_id).strip() for node_id in requested_node_ids))
    if not requested or any(not node_id for node_id in requested):
        raise HamburgNamedCountScopeError("requested station node ids must be non-empty")
    unexpected = sorted({stream.node_id for stream in streams} - set(requested))
    if unexpected:
        raise HamburgNamedCountScopeError(
            "official station inventory returned undeclared nodes: " + ", ".join(unexpected)
        )

    blocking_reasons: list[str] = []
    review_reasons: list[str] = []
    group_rows: list[dict[str, Any]] = []
    canonical_stream_ids: list[int] = []
    total_validation_stream_ids: list[int] = []
    grouped: dict[tuple[str, str], list[CountStream]] = {}
    for stream in streams:
        if (
            stream.layer_name != HAMBURG_COUNT_STATION_LAYER
            or not stream.station_arm
            or not stream.composition
            or stream.direction_code not in {"0", "1", "2"}
        ):
            blocking_reasons.append(f"invalid_station_stream_metadata:{stream.stream_id}")
            continue
        grouped.setdefault((stream.node_id, stream.station_arm), []).append(stream)

    for (node_id, station_arm), group in sorted(grouped.items()):
        by_direction: dict[str, CountStream] = {}
        group_blockers: list[str] = []
        group_reviews: list[str] = []
        for stream in group:
            if stream.direction_code in by_direction:
                group_blockers.append(
                    f"duplicate_station_direction:{node_id}:{station_arm}:{stream.direction_code}"
                )
            by_direction[stream.direction_code] = stream
        directional = [by_direction[code] for code in ("1", "2") if code in by_direction]
        if not directional:
            group_blockers.append(f"directional_station_stream_missing:{node_id}:{station_arm}")
        directional_members = [set(stream.composition) for stream in directional]
        if len(directional_members) == 2 and directional_members[0] & directional_members[1]:
            group_blockers.append(f"directional_station_composition_overlap:{node_id}:{station_arm}")
        canonical_stream_ids.extend(stream.stream_id for stream in directional)

        total = by_direction.get("0")
        if total is None:
            group_reviews.append(f"station_total_stream_missing:{node_id}:{station_arm}")
        else:
            total_validation_stream_ids.append(total.stream_id)
            expected_total = set().union(*directional_members) if directional_members else set()
            if set(total.composition) != expected_total:
                group_blockers.append(f"station_total_composition_mismatch:{node_id}:{station_arm}")
        if len(directional) != 2:
            group_reviews.append(f"station_direction_pair_incomplete:{node_id}:{station_arm}")

        blocking_reasons.extend(group_blockers)
        review_reasons.extend(group_reviews)
        group_rows.append(
            {
                "node_id": node_id,
                "station_arm": station_arm,
                "status": "blocked" if group_blockers else "review_required" if group_reviews else "pass",
                "directional_streams": [
                    {
                        "stream_id": stream.stream_id,
                        "station_id": stream.asset_id,
                        "direction_code": stream.direction_code,
                        "direction": stream.direction,
                        "composition": list(stream.composition),
                        "longitude": stream.longitude,
                        "latitude": stream.latitude,
                    }
                    for stream in directional
                ],
                "total_validation_stream": (
                    {
                        "stream_id": total.stream_id,
                        "station_id": total.asset_id,
                        "composition": list(total.composition),
                    }
                    if total is not None
                    else None
                ),
                "blocking_reasons": group_blockers,
                "review_reasons": group_reviews,
            }
        )

    available_nodes = sorted({stream.node_id for stream in streams})
    missing_nodes = sorted(set(requested) - set(available_nodes))
    status = "blocked" if blocking_reasons else "partial" if missing_nodes or review_reasons else "pass"
    return {
        "schema": COUNT_STATION_INVENTORY_SCHEMA,
        "status": status,
        "execution_gate": "blocked" if blocking_reasons else "pass",
        "automatic_promotion_gate": "blocked",
        "requested_node_ids": list(requested),
        "available_node_ids": available_nodes,
        "missing_node_ids": missing_nodes,
        "station_group_count": len(group_rows),
        "directional_station_stream_ids": sorted(canonical_stream_ids),
        "total_validation_stream_ids": sorted(total_validation_stream_ids),
        "groups": group_rows,
        "blocking_reasons": sorted(set(blocking_reasons)),
        "review_reasons": sorted(set(review_reasons)),
        "claim_boundary": (
            "Direction-1/2 Zählstelle streams are the official cross-section count truth. Direction-0 streams "
            "only validate their composition union. This inventory does not bind a station to a SUMO edge or lane."
        ),
    }


def materialize_hamburg_count_station_inventory(
    *,
    output_dir: Path,
    client: SensorThingsClient,
    requested_node_ids: Sequence[str],
) -> dict[str, Any]:
    """Freeze and audit official station aggregation metadata before SUMO binding."""

    output_dir = Path(output_dir).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    if any(output_dir.iterdir()):
        raise HamburgNamedCountScopeError("output_dir must be empty; choose a new versioned run directory")
    streams, raw = fetch_hamburg_count_station_streams(client, requested_node_ids)
    raw_path = output_dir / "count_station_streams.raw.json"
    normalized_path = output_dir / "count_station_streams.normalized.json"
    audit_path = output_dir / "station-composition.audit.json"
    write_json(raw_path, raw)
    write_json(normalized_path, _normalized_stream_rows(streams))
    audit = audit_hamburg_count_station_compositions(streams, requested_node_ids=requested_node_ids)
    write_json(audit_path, audit)
    manifest = {
        "schema": COUNT_STATION_INVENTORY_SCHEMA,
        "status": audit["status"],
        "execution_gate": audit["execution_gate"],
        "automatic_promotion_gate": "blocked",
        "source": {
            "system": "Hamburg OGC SensorThings count service",
            "api_base": client.base_url,
            "service": HAMBURG_COUNT_SERVICE,
            "layer": HAMBURG_COUNT_STATION_LAYER,
        },
        "requested_node_ids": list(dict.fromkeys(str(node_id) for node_id in requested_node_ids)),
        "station_stream_count": len(streams),
        "directional_station_stream_ids": audit["directional_station_stream_ids"],
        "total_validation_stream_ids": audit["total_validation_stream_ids"],
        "missing_node_ids": audit["missing_node_ids"],
        "artifacts": {
            "count_station_streams_raw": {"path": str(raw_path), "sha256": sha256_file(raw_path)},
            "count_station_streams_normalized": {
                "path": str(normalized_path),
                "sha256": sha256_file(normalized_path),
            },
            "station_composition_audit": {"path": str(audit_path), "sha256": sha256_file(audit_path)},
        },
        "next_action": "bind_official_station_cross_sections_to_frozen_w1",
        "claim_boundary": audit["claim_boundary"],
    }
    manifest_path = output_dir / "count-station-inventory.manifest.json"
    write_json(manifest_path, manifest)
    return {**manifest, "manifest_path": str(manifest_path)}


def write_corridor_aggregate_counts(
    path: Path,
    rows: Sequence[CanonicalCount],
    *,
    scope_id: str,
) -> None:
    """Write one traffic file aggregated by official node and direction.

    Detector rows stay available for audit.  This file is the compact demand
    hand-off; an unknown official direction remains its own group rather than
    being assigned to an inferred lane.
    """

    grouped: dict[tuple[str, str, int, int], dict[str, Any]] = {}
    for row in rows:
        key = (row.node_id, row.direction or "unbekannt", row.begin, row.end)
        item = grouped.setdefault(
            key,
            {
                "scope_id": scope_id,
                "node_id": row.node_id,
                "direction": row.direction or "unbekannt",
                "begin": row.begin,
                "end": row.end,
                "detector_ids": [],
                "expected_total": 0,
                "quality_status": "complete",
            },
        )
        item["detector_ids"].append(row.detector_id)
        item["expected_total"] += row.count
        if row.quality_status != "complete":
            item["quality_status"] = "missing_source_bins"

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "scope_id",
                "node_id",
                "direction",
                "begin",
                "end",
                "interval_seconds",
                "detector_count",
                "expected_total",
                "detector_ids",
                "quality_status",
                "aggregation_method",
            ],
        )
        writer.writeheader()
        for item in sorted(grouped.values(), key=lambda value: (value["begin"], value["node_id"], value["direction"])):
            detector_ids = sorted(item["detector_ids"])
            writer.writerow(
                {
                    **item,
                    "end": item["end"],
                    "interval_seconds": item["end"] - item["begin"],
                    "detector_count": len(detector_ids),
                    "detector_ids": " ".join(detector_ids),
                    "aggregation_method": "sum_official_detector_15min_rows_by_node_and_direction",
                }
            )


def materialize_hamburg_named_count_scope(
    *,
    output_dir: Path,
    client: SensorThingsClient,
    signal_nodes: Mapping[str, OfficialSignalNodeReference],
    requested_count_node_ids: Sequence[str],
    scope_id: str,
    saturday_date: date | None = None,
    max_saturdays_to_try: int = 8,
    warmup_seconds: int = 1800,
    formal_duration_seconds: int = 7200,
    source_bin_seconds: int = 300,
    output_bin_seconds: int = 900,
    max_distance_m: float = 250.0,
) -> dict[str, Any]:
    """Fetch and aggregate a declared Hamburg detector scope into SUMO-ready bins."""

    if not scope_id.strip():
        raise HamburgNamedCountScopeError("scope_id is required")
    if max_saturdays_to_try < 1:
        raise HamburgNamedCountScopeError("max_saturdays_to_try must be positive")
    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    if any(output_dir.iterdir()):
        raise HamburgNamedCountScopeError(
            "output_dir must be empty; choose a new versioned run directory"
        )
    streams, stream_raw = fetch_hamburg_count_streams(client, requested_count_node_ids)
    streams, direction_inference = infer_hamburg_count_directions(streams)
    stream_raw_path = output_dir / "count_streams.raw.json"
    write_json(stream_raw_path, stream_raw)
    normalized_stream_path = output_dir / "count_streams.normalized.json"
    write_json(normalized_stream_path, _normalized_stream_rows(streams))
    scope_evidence = build_hamburg_count_scope_evidence(
        streams,
        requested_count_node_ids=requested_count_node_ids,
        signal_nodes=signal_nodes,
        max_distance_m=max_distance_m,
    )
    scope_evidence["direction_inference"] = direction_inference
    scope_path = output_dir / "sensor-scope.evidence.json"
    write_json(scope_path, scope_evidence)

    base_manifest: dict[str, Any] = {
        "schema": NAMED_COUNT_SCOPE_SCHEMA,
        "scope_id": scope_id,
        "status": "blocked",
        "claim_status": "diagnostic-demo",
        "automatic_promotion_gate": "blocked",
        "execution_gate": "blocked",
        "execution_gate_reason": "official detector window and diagnostics are not yet complete",
        "source": {
            "system": "Hamburg OGC SensorThings count service",
            "api_base": client.base_url,
            "service": "HH_STA_Verkehrsdaten_Kfz_Infrarotdetektoren",
            "layer": "Anzahl_Kfz_Zaehlfeld_5-Min",
        },
        "parameters": {
            "requested_count_node_ids": list(requested_count_node_ids),
            "warmup_seconds": warmup_seconds,
            "formal_duration_seconds": formal_duration_seconds,
            "source_bin_seconds": source_bin_seconds,
            "output_bin_seconds": output_bin_seconds,
            "max_distance_m": max_distance_m,
        },
        "scope_evidence": {
            "path": str(scope_path),
            "sha256": sha256_file(scope_path),
        },
        "artifacts": {
            "count_streams_raw": {
                "path": str(stream_raw_path),
                "sha256": sha256_file(stream_raw_path),
            },
            "count_streams_normalized": {
                "path": str(normalized_stream_path),
                "sha256": sha256_file(normalized_stream_path),
            },
        },
    }
    if not streams:
        manifest_path = output_dir / "sensor-count-scope.manifest.json"
        write_json(manifest_path, base_manifest)
        return {**base_manifest, "manifest_path": str(manifest_path)}

    attempts: list[dict[str, Any]] = []
    selected: tuple[date, dict[int, list[Any]], Any, Any, dict[str, Any]] | None = None
    candidate_dates = [saturday_date] if saturday_date else recent_completed_saturdays(limit=max_saturdays_to_try)
    for candidate_date in candidate_dates:
        try:
            observations, observation_raw = fetch_hamburg_count_observations(
                client,
                streams,
                local_date=candidate_date,
            )
            ranked = rank_complete_simulation_windows(
                streams,
                observations,
                local_date=candidate_date,
                formal_duration_seconds=formal_duration_seconds,
                warmup_seconds=warmup_seconds,
                source_bin_seconds=source_bin_seconds,
                output_bin_seconds=output_bin_seconds,
            )
        except Exception as exc:
            attempts.append({"date": candidate_date.isoformat(), "status": "fetch_error", "error": str(exc)})
            continue
        if not ranked:
            attempts.append(
                {
                    "date": candidate_date.isoformat(),
                    "status": "no_complete_simulation_window",
                    "required_interval_seconds": warmup_seconds + formal_duration_seconds,
                }
            )
            continue
        window = ranked[0]
        attempts.append(
            {
                "date": candidate_date.isoformat(),
                "status": "complete",
                "complete_window_count": len(ranked),
                "formal_begin_utc": window.formal_window.begin_utc,
                "formal_end_utc": window.formal_window.end_utc,
                "simulation_begin_utc": window.simulation_begin_utc,
                "score": window.formal_window.score,
            }
        )
        selected = (candidate_date, observations, window, window.formal_window, observation_raw)
        break

    attempts_path = output_dir / "count_window_attempts.json"
    write_json(attempts_path, attempts)
    base_manifest["artifacts"]["count_window_attempts"] = {
        "path": str(attempts_path),
        "sha256": sha256_file(attempts_path),
    }
    if selected is None:
        base_manifest["scope_evidence"] = {
            **base_manifest["scope_evidence"],
            "status": scope_evidence["status"],
        }
        manifest_path = output_dir / "sensor-count-scope.manifest.json"
        write_json(manifest_path, base_manifest)
        return {**base_manifest, "manifest_path": str(manifest_path)}

    selected_date, observations, simulation_window, formal_window, observation_raw = selected
    observation_path = output_dir / "count_observations.raw.json"
    write_json(observation_path, observation_raw)
    normalized_observation_path = output_dir / "count_observations.normalized.json"
    write_json(
        normalized_observation_path,
        {
            str(stream_id): rows
            for stream_id, rows in sorted(observations.items())
        },
    )
    simulation_rows = aggregate_simulation_counts(streams, observations, simulation_window)
    comparison_rows = select_comparison_count_rows(simulation_rows, simulation_window)
    simulation_count_path = output_dir / "counts.simulation.15min.csv"
    comparison_count_path = output_dir / "counts.formal_comparison.15min.csv"
    aggregate_count_path = output_dir / "counts.corridor.aggregate.15min.csv"
    write_canonical_counts(simulation_count_path, simulation_rows)
    write_canonical_counts(comparison_count_path, comparison_rows)
    write_corridor_aggregate_counts(aggregate_count_path, simulation_rows, scope_id=scope_id)
    base_manifest.update(
        {
            "status": scope_evidence["status"],
            "automatic_promotion_gate": scope_evidence["automatic_promotion_gate"],
            "execution_gate": (
                "pass"
                if not scope_evidence["over_distance_stream_ids"]
                and not scope_evidence["unknown_direction_stream_ids"]
                else "blocked"
            ),
            "execution_gate_reason": (
                "official observation window and detector metadata gates are complete for all requested nodes"
                if not scope_evidence["over_distance_stream_ids"]
                and not scope_evidence["unknown_direction_stream_ids"]
                else "detector geometry or direction remains unresolved"
            ),
            "selected_window": {
                "local_date": selected_date,
                "formal_window": formal_window,
                "simulation_window": simulation_window,
            },
            "stream_count": len(streams),
            "simulation_row_count": len(simulation_rows),
            "formal_comparison_row_count": len(comparison_rows),
            "aggregate_row_count": len({
                (row.node_id, row.direction or "unbekannt", row.begin, row.end)
                for row in simulation_rows
            }),
        }
    )
    base_manifest["gates"] = {
        "official_observation_window": "pass",
        "detector_geometry": "pass" if not scope_evidence["over_distance_stream_ids"] else "blocked",
        "direction_mapping": "pass" if not scope_evidence["unknown_direction_stream_ids"] else "blocked",
        "full_named_node_coverage": "pass" if not scope_evidence["missing_count_node_ids"] else "blocked",
        "automatic_promotion": base_manifest["automatic_promotion_gate"],
    }
    for key, path in {
        "count_observations_raw": observation_path,
        "count_observations_normalized": normalized_observation_path,
        "counts_simulation_15min": simulation_count_path,
        "counts_formal_comparison_15min": comparison_count_path,
        "counts_corridor_aggregate_15min": aggregate_count_path,
    }.items():
        base_manifest["artifacts"][key] = {"path": str(path), "sha256": sha256_file(path)}
    manifest_path = output_dir / "sensor-count-scope.manifest.json"
    write_json(manifest_path, base_manifest)
    serialized_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    return {**serialized_manifest, "manifest_path": str(manifest_path)}
