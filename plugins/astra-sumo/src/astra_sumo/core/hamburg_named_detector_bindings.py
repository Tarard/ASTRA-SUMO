"""Fail-closed W3b detector-to-lane binding for the named Hamburg corridor.

The official detector service gives WGS84 points, while the official-first
candidate network is stored in ETRS89 / UTM 32N metres.  This stage performs
that explicit conversion, records every nearby passenger-lane candidate, and
promotes a geometry-only binding only when the nearest lane is both close and
separated from the runner-up.  A parallel-lane tie is evidence of missing lane
identity, not permission to guess.
"""

from __future__ import annotations

import json
import math
from dataclasses import replace
from itertools import permutations
from pathlib import Path
from typing import Any, Mapping

from .artifact_io import write_json_atomic
from .cached_detector_demand import read_hamburg_count_stream_snapshot
from .candidate_contracts import file_sha256
from .digital_twin import parse_mapem
from .digital_twin import angular_difference_degrees
from .digital_twin_mapping import (
    DetectorMapping,
    _convert_network_lonlat2xy,
    _safe_lane_position,
    bind_count_streams_to_network,
    bind_map_lanes_to_network,
    project_point_to_polyline,
    read_network_lanes,
    write_detector_mapping,
)


DETECTOR_BINDING_SCHEMA = "torii.hamburg-named-detector-binding/v1"
DEFAULT_NETWORK_PROJECTION = "EPSG:25832"
# ponytail: expose this as a CLI parameter only if another corridor needs a different cut tolerance.
SERIAL_CUT_MAX_GAP_M = 2.0
CONSTELLATION_HEADING_TOLERANCE_DEG = 10.0


class HamburgDetectorBindingError(ValueError):
    """Raised when the detector-binding inputs are not deterministic."""


def materialize_hamburg_named_detector_bindings(
    *,
    net_file: Path,
    count_stream_file: Path,
    output_dir: Path,
    network_projection: str = DEFAULT_NETWORK_PROJECTION,
    period: int = 900,
    max_distance_m: float = 5.0,
    ambiguity_margin_m: float = 1.0,
    map_files: tuple[Path, ...] = (),
    movement_evidence_file: Path | None = None,
) -> dict[str, Any]:
    """Write an auditable detector mapping and its automatic quality gate.

    The function never creates SUMO E1/E2 sensors from an incomplete mapping.
    A later stage may consume the mapping only when every stream is active.  A
    partial result is still useful: it records why a particular field is
    blocked and which lane candidates the next plan must improve.
    """

    if period <= 0:
        raise HamburgDetectorBindingError("period must be positive")
    if not network_projection.strip():
        raise HamburgDetectorBindingError("network_projection is required")
    if not math.isfinite(max_distance_m) or max_distance_m <= 0:
        raise HamburgDetectorBindingError("max_distance_m must be finite and positive")
    if not math.isfinite(ambiguity_margin_m) or ambiguity_margin_m < 0:
        raise HamburgDetectorBindingError("ambiguity_margin_m must be finite and non-negative")

    net_path = Path(net_file).expanduser().resolve(strict=True)
    stream_path = Path(count_stream_file).expanduser().resolve(strict=True)
    destination = Path(output_dir).expanduser().resolve()
    destination.mkdir(parents=True, exist_ok=True)
    if any(destination.iterdir()):
        raise HamburgDetectorBindingError("output_dir must be empty; choose a new versioned run")

    streams = read_hamburg_count_stream_snapshot(stream_path)
    if not streams:
        raise HamburgDetectorBindingError("count stream snapshot is empty")
    official_map_paths = [Path(path).expanduser().resolve(strict=True) for path in map_files]
    if len(set(official_map_paths)) != len(official_map_paths):
        raise HamburgDetectorBindingError("official MAP files must be unique")
    movement_evidence_path = (
        Path(movement_evidence_file).expanduser().resolve(strict=True)
        if movement_evidence_file is not None
        else None
    )
    movement_evidence = (
        _read_movement_lane_evidence(movement_evidence_path)
        if movement_evidence_path is not None
        else None
    )
    map_lanes = []
    for map_path in official_map_paths:
        lanes, _ = parse_mapem(map_path)
        map_lanes.extend(lanes)
    if official_map_paths and not map_lanes:
        raise HamburgDetectorBindingError("official MAP files contain no lane geometry")
    map_lane_keys = [(lane.node_id, lane.lane_id) for lane in map_lanes]
    if len(set(map_lane_keys)) != len(map_lane_keys):
        raise HamburgDetectorBindingError("official MAP files repeat a node/lane identity")
    net, network_lanes = read_network_lanes(net_path)
    map_bindings = (
        bind_map_lanes_to_network(
            net_path,
            map_lanes,
            network_projection=network_projection,
        )
        if map_lanes
        else []
    )
    mappings = bind_count_streams_to_network(
        net_path,
        streams,
        map_lanes,
        map_bindings,
        period=period,
        network_projection=network_projection,
    )

    prepared_candidates: list[tuple[list[dict[str, Any]], list[dict[str, Any]]]] = []
    for stream in streams:
        raw_candidates = _candidate_rows(
            net,
            network_lanes,
            stream.longitude,
            stream.latitude,
            network_projection,
        )
        prepared_candidates.append((raw_candidates, _collapse_serial_lane_cuts(net, raw_candidates)))
    constellation_assignments = (
        _translation_normalized_constellation_assignments(
            net,
            streams,
            [item[1] for item in prepared_candidates],
            movement_evidence,
            network_projection=network_projection,
            max_distance_m=max_distance_m,
            ambiguity_margin_m=ambiguity_margin_m,
        )
        if movement_evidence is not None
        else {}
    )

    candidate_rows: list[dict[str, Any]] = []
    final_mappings: list[DetectorMapping] = []
    for stream, mapping, (raw_candidates, candidates) in zip(
        streams,
        mappings,
        prepared_candidates,
        strict=True,
    ):
        geometry_nearest = candidates[0] if candidates else None
        official_candidate = next(
            (candidate for candidate in raw_candidates if candidate["lane_id"] == mapping.sumo_lane),
            None,
        )
        has_official_identity = bool(mapping.official_map_lane)
        official_map_to_sumo_distance_m = mapping.distance_m if has_official_identity else None
        official_map_to_sumo_heading_error_deg = (
            mapping.heading_error_deg if has_official_identity else None
        )
        official_active = (
            has_official_identity
            and mapping.mapping_status == "active"
            and official_candidate is not None
            and float(official_candidate["distance_m"]) <= max_distance_m
        )
        constellation = constellation_assignments.get(stream.stream_id)
        constellation_candidate = constellation["candidate"] if constellation else None
        constellation_active = not has_official_identity and constellation_candidate is not None
        nearest = (
            official_candidate
            if official_active
            else constellation_candidate
            if constellation_active
            else geometry_nearest
        )
        second = next(
            (
                candidate
                for candidate in candidates
                if nearest is None
                or nearest["lane_id"] not in candidate["equivalent_segment_lane_ids"]
            ),
            None,
        )
        distance = float(nearest["distance_m"]) if nearest else math.inf
        separation = (
            float(second["distance_m"]) - distance
            if second is not None
            else math.inf
        )
        geometry_active = nearest is not None and distance <= max_distance_m and separation >= ambiguity_margin_m
        active = official_active or constellation_active or (not has_official_identity and geometry_active)
        if official_active:
            reason = "official MAP ingress-lane identity + detector-point projection onto the frozen SUMO lane"
        elif constellation_active:
            reason = (
                "official engineering-plan lane count + translation-normalized detector constellation; "
                "geometric inference, not an official Z-to-lane identity"
            )
        elif active:
            reason = (
                "strict geometry-only binding; serial SUMO edge cuts collapsed to the upstream lane; "
                "explicit EPSG:25832 conversion; no official MAP lane identity"
                if nearest and len(nearest["equivalent_segment_lane_ids"]) > 1
                else "strict geometry-only binding; explicit EPSG:25832 conversion; no official MAP lane identity"
            )
        elif has_official_identity:
            reason = "official MAP lane identity is not active or is outside the strict detector-distance gate"
        elif nearest is None or distance > max_distance_m:
            reason = "nearest lane is outside strict detector-distance gate"
        else:
            reason = "nearest and runner-up lanes are geometrically ambiguous"
        if active:
            mapping = replace(
                mapping,
                sumo_edge=str(nearest["edge_id"]),
                sumo_lane=str(nearest["lane_id"]),
                lane_position=_safe_lane_position(
                    float(nearest["lane_position_m"]),
                    float(nearest["lane_length_m"]),
                ),
                distance_m=distance,
                mapping_confidence=(
                    "medium"
                    if constellation_active
                    else "high"
                    if distance <= 2.0 and (not official_active or mapping.mapping_confidence == "high")
                    else "medium"
                ),
                mapping_status="active",
                mapping_reason=reason,
            )
        else:
            mapping = replace(mapping, mapping_status="needs_review", mapping_reason=reason)
        final_mappings.append(mapping)
        candidate_rows.append(
            {
                "stream_id": stream.stream_id,
                "detector_id": stream.detector_id,
                "node_id": stream.node_id,
                "asset_id": stream.asset_id,
                "longitude": stream.longitude,
                "latitude": stream.latitude,
                "official_map_lane": mapping.official_map_lane,
                "official_detector_to_map_distance_m": mapping.official_map_distance_m,
                "official_map_to_sumo_distance_m": official_map_to_sumo_distance_m,
                "official_map_to_sumo_heading_error_deg": official_map_to_sumo_heading_error_deg,
                "selected_lane": nearest["lane_id"] if nearest else "",
                "selected_edge": nearest["edge_id"] if nearest else "",
                "selected_distance_m": round(distance, 3) if math.isfinite(distance) else None,
                "runner_up_distance_m": round(float(second["distance_m"]), 3) if second else None,
                "separation_m": round(separation, 3) if math.isfinite(separation) else None,
                "candidate_status": "active" if active else "needs_review",
                "reason": reason,
                "constellation_inference": constellation["evidence"] if constellation else None,
                "candidates": candidates[:8],
                "raw_segment_candidates": raw_candidates[:8],
            }
        )

    mapping_file = destination / "detector_mapping.csv"
    candidate_file = destination / "detector_lane_candidates.json"
    manifest_file = destination / "detector-binding.manifest.json"
    write_detector_mapping(mapping_file, final_mappings)
    candidate_payload = {
        "schema": f"{DETECTOR_BINDING_SCHEMA}/candidates",
        "network_projection": network_projection,
        "thresholds": {
            "max_distance_m": max_distance_m,
            "ambiguity_margin_m": ambiguity_margin_m,
            "serial_cut_max_gap_m": SERIAL_CUT_MAX_GAP_M,
            "constellation_heading_tolerance_deg": CONSTELLATION_HEADING_TOLERANCE_DEG,
        },
        "rows": candidate_rows,
    }
    write_json_atomic(candidate_file, candidate_payload, ensure_ascii=False, sort_keys=True)

    active_count = sum(item.mapping_status == "active" for item in final_mappings)
    incomplete = [item.stream_id for item in final_mappings if item.mapping_status != "active"]
    unmapped = [item.stream_id for item in final_mappings if not item.sumo_lane]
    streams_by_lane: dict[tuple[str, str], list[DetectorMapping]] = {}
    for item in final_mappings:
        if item.mapping_status == "active":
            streams_by_lane.setdefault((item.node_id, item.sumo_lane), []).append(item)
    shared_lane_stream_groups = [
        {
            "node_id": node_id,
            "sumo_lane": sumo_lane,
            "sumo_edge": items[0].sumo_edge,
            "stream_ids": [item.stream_id for item in sorted(items, key=lambda value: value.stream_id)],
            "asset_ids": [item.asset_id for item in sorted(items, key=lambda value: value.stream_id)],
            "lane_positions": [item.lane_position for item in sorted(items, key=lambda value: value.stream_id)],
        }
        for (node_id, sumo_lane), items in sorted(streams_by_lane.items())
        if len(items) > 1
    ]
    execution_gate = "pass" if not unmapped else "blocked"
    binding_gate = "pass" if active_count == len(final_mappings) else "blocked"
    aggregation_gate = "pass" if not shared_lane_stream_groups else "blocked"
    promotion_gate = "pass" if binding_gate == aggregation_gate == "pass" else "blocked"
    status = "pass" if promotion_gate == "pass" else "partial"
    node_reports: dict[str, dict[str, Any]] = {}
    for node_id in sorted({item.node_id for item in final_mappings}):
        node_items = [item for item in final_mappings if item.node_id == node_id]
        node_reports[node_id] = {
            "stream_count": len(node_items),
            "active_count": sum(item.mapping_status == "active" for item in node_items),
            "blocked_stream_ids": [item.stream_id for item in node_items if item.mapping_status != "active"],
            "status": "pass" if all(item.mapping_status == "active" for item in node_items) else "blocked",
        }
    manifest: dict[str, Any] = {
        "schema": DETECTOR_BINDING_SCHEMA,
        "status": status,
        "execution_gate": execution_gate,
        "execution_gate_reason": (
            "all detector points have a structurally valid lane candidate"
            if execution_gate == "pass"
            else "one or more detector points have no passenger-lane candidate"
        ),
        "automatic_promotion_gate": promotion_gate,
        "automatic_promotion_reason": (
            "every official stream passed strict distance and parallel-lane ambiguity gates"
            if promotion_gate == "pass"
            else "E1/E2 materialization is withheld until shared-lane official streams have explicit aggregation semantics"
            if shared_lane_stream_groups
            else "E1/E2 materialization is withheld until every official stream has a unique strict lane binding"
        ),
        "required_node_ids": sorted({stream.node_id for stream in streams}),
        "stream_count": len(streams),
        "active_mapping_count": active_count,
        "incomplete_stream_ids": incomplete,
        "unmapped_stream_ids": unmapped,
        "shared_lane_stream_groups": shared_lane_stream_groups,
        "source": {
            "candidate_net": {"path": str(net_path), "sha256": file_sha256(net_path)},
            "count_stream_snapshot": {"path": str(stream_path), "sha256": file_sha256(stream_path)},
            "official_map_files": [
                {"path": str(path), "sha256": file_sha256(path)} for path in official_map_paths
            ],
            "movement_lane_evidence": (
                {
                    "path": str(movement_evidence_path),
                    "sha256": file_sha256(movement_evidence_path),
                }
                if movement_evidence_path is not None
                else None
            ),
        },
        "parameters": {
            "network_projection": network_projection,
            "period": period,
            "max_distance_m": max_distance_m,
            "ambiguity_margin_m": ambiguity_margin_m,
            "serial_cut_max_gap_m": SERIAL_CUT_MAX_GAP_M,
            "constellation_heading_tolerance_deg": CONSTELLATION_HEADING_TOLERANCE_DEG,
        },
        "node_reports": node_reports,
        "artifacts": {
            "detector_mapping": {"path": str(mapping_file), "sha256": file_sha256(mapping_file)},
            "detector_lane_candidates": {"path": str(candidate_file), "sha256": file_sha256(candidate_file)},
            "e1_e2_additional": {
                "status": "withheld_pending_complete_lane_mapping",
                "reason": "partial or ambiguous bindings must not create same-location SUMO sensors",
            },
        },
        "gates": {
            "coordinate_projection": "pass",
            "detector_stream_identity": "pass",
            "candidate_geometry": execution_gate,
            "unique_lane_binding": binding_gate,
            "sensor_aggregation_semantics": aggregation_gate,
            "sumo_sensor_materialization": promotion_gate,
            "automatic_promotion": promotion_gate,
        },
        "claim_boundary": {
            "proves": [
                "the explicit WGS84-to-network CRS conversion used for each detector",
                "official MAP ingress-lane identity for nodes whose MAP file is supplied",
                "all nearby passenger-lane candidates and the reason for each active or blocked mapping",
                "that one-to-one serial edge cuts are collapsed while ambiguous parallel lanes remain rejected",
                "translation-normalized cross-section inference only when official lane-count evidence and strict geometric gates agree",
            ],
            "does_not_prove": [
                "official MAP lane identity when the MAP cell is absent",
                "that a Zählfeld asset number is a Hamburg or SUMO lane index",
                "complete three-node detector coverage",
                "that SUMO E1/E2 sensors can be materialized before all mappings are active",
                "that multiple official fields mapped to one lane are additive, redundant, or longitudinally equivalent",
            ],
        },
        "next_action": (
            "materialize_same_location_sumo_sensors"
            if promotion_gate == "pass"
            else "resolve_shared_lane_detector_aggregation_semantics"
            if shared_lane_stream_groups
            else "repair_official_first_lane_geometry_or_supply_official_map_lane_identity"
        ),
        "artifacts_manifest": str(manifest_file),
    }
    write_json_atomic(manifest_file, manifest, ensure_ascii=False, sort_keys=True)
    return {**manifest, "manifest_file": str(manifest_file)}


def _candidate_rows(
    net: object,
    network_lanes: list[Any],
    longitude: float,
    latitude: float,
    network_projection: str,
) -> list[dict[str, Any]]:
    x, y = _convert_network_lonlat2xy(
        net,
        longitude,
        latitude,
        network_projection=network_projection,
    )
    rows: list[dict[str, Any]] = []
    for lane in network_lanes:
        position, distance, heading = project_point_to_polyline((x, y), lane.shape)
        rows.append(
            {
                "lane_id": lane.lane_id,
                "edge_id": lane.edge_id,
                "lane_position_m": round(float(position), 3),
                "lane_length_m": round(float(lane.length), 3),
                "distance_m": round(float(distance), 3),
                "lane_heading_deg": round(float(heading), 3),
            }
        )
    rows.sort(key=lambda item: (item["distance_m"], item["edge_id"], item["lane_id"]))
    return rows


def _read_movement_lane_evidence(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise HamburgDetectorBindingError(f"cannot read movement lane evidence: {path}") from exc
    if not isinstance(payload, Mapping) or payload.get("schema") != "torii.hamburg-bounded-movement-evidence/v1":
        raise HamburgDetectorBindingError("movement lane evidence has an unsupported schema")
    node_id = str(payload.get("official_node_id", "")).strip()
    lane_policy = payload.get("lane_policy")
    authorized = payload.get("authorized_addition")
    sources = payload.get("official_sources")
    if not node_id or not isinstance(lane_policy, Mapping) or not isinstance(authorized, Mapping):
        raise HamburgDetectorBindingError("movement lane evidence is incomplete")
    try:
        lane_count = int(lane_policy["motor_lane_count"])
    except (KeyError, TypeError, ValueError) as exc:
        raise HamburgDetectorBindingError("movement lane evidence has no valid motor lane count") from exc
    # ponytail: factorial search is intentionally bounded; replace it only for a proven >6-lane site.
    if not 2 <= lane_count <= 6:
        raise HamburgDetectorBindingError("movement lane evidence motor lane count must be between 2 and 6")
    from_edge = str(authorized.get("from_edge", "")).strip()
    edge_stem, separator, _cut = from_edge.partition("#")
    if not from_edge or not separator or not edge_stem:
        raise HamburgDetectorBindingError("movement lane evidence must identify a cut OSM source edge")
    if not isinstance(sources, list) or not sources:
        raise HamburgDetectorBindingError("movement lane evidence must retain official source hashes")
    for source in sources:
        if not isinstance(source, Mapping):
            raise HamburgDetectorBindingError("movement lane evidence official source is invalid")
        url = str(source.get("url", ""))
        digest = str(source.get("sha256", "")).lower()
        if not url.startswith("https://") or len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest):
            raise HamburgDetectorBindingError("movement lane evidence official source is not hash-bound HTTPS evidence")
    return {
        "node_id": node_id,
        "motor_lane_count": lane_count,
        "edge_stem": edge_stem,
        "from_edge": from_edge,
        "official_source_sha256": [str(source["sha256"]).lower() for source in sources],
    }


def _translation_normalized_constellation_assignments(
    net: object,
    streams: list[Any],
    candidate_sets: list[list[dict[str, Any]]],
    evidence: Mapping[str, Any],
    *,
    network_projection: str,
    max_distance_m: float,
    ambiguity_margin_m: float,
) -> dict[int, dict[str, Any]]:
    """Resolve a complete detector cross-section without treating Z.n as a lane index."""

    if len(streams) != len(candidate_sets):
        raise HamburgDetectorBindingError("detector candidate sets do not match the official streams")
    node_id = str(evidence["node_id"])
    lane_count = int(evidence["motor_lane_count"])
    edge_stem = str(evidence["edge_stem"])
    stream_rows = {stream.stream_id: rows for stream, rows in zip(streams, candidate_sets, strict=True)}
    points = {
        stream.stream_id: _convert_network_lonlat2xy(
            net,
            stream.longitude,
            stream.latitude,
            network_projection=network_projection,
        )
        for stream in streams
        if stream.node_id == node_id
    }
    assignments: dict[int, dict[str, Any]] = {}
    directions = sorted(
        {
            stream.direction.strip()
            for stream in streams
            if stream.stream_id in points and stream.direction.strip().lower() not in {"", "unbekannt", "unknown"}
        }
    )
    for direction in directions:
        direction_streams = [
            stream
            for stream in streams
            if stream.stream_id in points and stream.direction.strip() == direction
        ]
        unseen = {stream.stream_id for stream in direction_streams}
        while unseen:
            seed = min(unseen)
            component = {seed}
            pending = [seed]
            while pending:
                current = pending.pop()
                neighbours = {
                    other
                    for other in unseen - component
                    if math.dist(points[current], points[other]) <= 2.0 * max_distance_m
                }
                component.update(neighbours)
                pending.extend(sorted(neighbours))
            unseen -= component
            if len(component) != lane_count:
                continue
            component_ids = sorted(component)
            common_edges = set.intersection(
                *(
                    {
                        str(row["edge_id"])
                        for row in stream_rows[stream_id]
                        if str(row["edge_id"]).partition("#")[0] == edge_stem
                    }
                    for stream_id in component_ids
                )
            )
            ranked: list[dict[str, Any]] = []
            for edge_id in sorted(common_edges):
                lanes = [
                    lane
                    for lane in net.getEdge(edge_id).getLanes()  # type: ignore[attr-defined]
                    if lane.allows("passenger") or lane.allows("private")
                ]
                if len(lanes) != lane_count:
                    continue
                lanes.sort(key=lambda lane: lane.getIndex())
                rows_by_stream = {
                    stream_id: {
                        str(row["lane_id"]): row
                        for row in stream_rows[stream_id]
                        if str(row["edge_id"]) == edge_id
                    }
                    for stream_id in component_ids
                }
                lane_ids = tuple(lane.getID() for lane in lanes)
                if any(set(rows_by_stream[stream_id]) != set(lane_ids) for stream_id in component_ids):
                    continue
                for assigned_lane_ids in permutations(lane_ids):
                    vectors: list[tuple[float, float]] = []
                    lane_headings: list[float] = []
                    for stream_id, lane_id in zip(component_ids, assigned_lane_ids, strict=True):
                        lane = net.getLane(lane_id)  # type: ignore[attr-defined]
                        projected, heading = _nearest_projection_point(points[stream_id], lane.getShape())
                        vectors.append(
                            (
                                points[stream_id][0] - projected[0],
                                points[stream_id][1] - projected[1],
                            )
                        )
                        lane_headings.append(heading)
                    translation = (
                        sum(vector[0] for vector in vectors) / lane_count,
                        sum(vector[1] for vector in vectors) / lane_count,
                    )
                    residuals = [
                        math.dist(vector, translation)
                        for vector in vectors
                    ]
                    cross_heading = _cross_section_heading([points[stream_id] for stream_id in component_ids])
                    heading_error = max(
                        min(
                            angular_difference_degrees(cross_heading, lane_heading + 90.0),
                            angular_difference_degrees(cross_heading, lane_heading - 90.0),
                        )
                        for lane_heading in lane_headings
                    )
                    translation_m = math.hypot(*translation)
                    if (
                        translation_m > max_distance_m
                        or heading_error > CONSTELLATION_HEADING_TOLERANCE_DEG
                    ):
                        continue
                    ranked.append(
                        {
                            "edge_id": edge_id,
                            "lane_ids": assigned_lane_ids,
                            "score_m": sum(residuals),
                            "maximum_residual_m": max(residuals),
                            "translation_xy_m": translation,
                            "translation_m": translation_m,
                            "heading_error_deg": heading_error,
                            "rows_by_stream": rows_by_stream,
                        }
                    )
            ranked.sort(
                key=lambda item: (
                    item["score_m"],
                    item["translation_m"],
                    item["edge_id"],
                    item["lane_ids"],
                )
            )
            if not ranked:
                continue
            best = ranked[0]
            score_margin = (
                float(ranked[1]["score_m"]) - float(best["score_m"])
                if len(ranked) > 1
                else math.inf
            )
            if float(best["maximum_residual_m"]) > ambiguity_margin_m or score_margin < ambiguity_margin_m:
                continue
            group_evidence = {
                "basis": "official_lane_count_plus_translation_normalized_point_constellation",
                "claim_boundary": "geometric cross-section inference; Z.n is not treated as a lane index",
                "node_id": node_id,
                "direction": direction,
                "edge_stem": edge_stem,
                "selected_edge": best["edge_id"],
                "stream_ids": component_ids,
                "lane_ids": list(best["lane_ids"]),
                "translation_xy_m": [round(value, 3) for value in best["translation_xy_m"]],
                "translation_m": round(float(best["translation_m"]), 3),
                "maximum_residual_m": round(float(best["maximum_residual_m"]), 3),
                "assignment_score_m": round(float(best["score_m"]), 3),
                "runner_up_margin_m": round(score_margin, 3) if math.isfinite(score_margin) else None,
                "heading_error_deg": round(float(best["heading_error_deg"]), 3),
                "official_source_sha256": list(evidence["official_source_sha256"]),
            }
            for stream_id, lane_id in zip(component_ids, best["lane_ids"], strict=True):
                if stream_id in assignments:
                    raise HamburgDetectorBindingError(f"stream {stream_id} belongs to multiple detector constellations")
                assignments[stream_id] = {
                    "candidate": best["rows_by_stream"][stream_id][lane_id],
                    "evidence": group_evidence,
                }
    return assignments


def _nearest_projection_point(
    point: tuple[float, float],
    shape: list[tuple[float, float]],
) -> tuple[tuple[float, float], float]:
    best: tuple[float, tuple[float, float], float] | None = None
    for start, end in zip(shape, shape[1:]):
        dx = end[0] - start[0]
        dy = end[1] - start[1]
        squared = dx * dx + dy * dy
        factor = (
            max(0.0, min(1.0, ((point[0] - start[0]) * dx + (point[1] - start[1]) * dy) / squared))
            if squared
            else 0.0
        )
        projected = (start[0] + factor * dx, start[1] + factor * dy)
        candidate = (
            math.dist(point, projected),
            projected,
            math.degrees(math.atan2(dy, dx)) % 360.0,
        )
        if best is None or candidate[0] < best[0]:
            best = candidate
    if best is None:
        raise HamburgDetectorBindingError("SUMO lane has no projectable shape")
    return best[1], best[2]


def _cross_section_heading(points: list[tuple[float, float]]) -> float:
    if len(points) < 2:
        raise HamburgDetectorBindingError("detector constellation needs at least two points")
    start, end = max(
        ((left, right) for index, left in enumerate(points) for right in points[index + 1 :]),
        key=lambda pair: math.dist(*pair),
    )
    return math.degrees(math.atan2(end[1] - start[1], end[0] - start[0])) % 360.0


def _collapse_serial_lane_cuts(net: object, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Treat a detector on a one-to-one edge cut as one lane hypothesis."""

    by_lane = {str(row["lane_id"]): row for row in rows}
    neighbours = {lane_id: set() for lane_id in by_lane}
    predecessors = {lane_id: set() for lane_id in by_lane}
    for lane_id, row in by_lane.items():
        lane = net.getLane(lane_id)  # type: ignore[attr-defined]
        outgoing = [
            connection
            for connection in lane.getOutgoing()
            if not connection.getToLane().getID().startswith(":")
            and (connection.allows("passenger") or connection.allows("private"))
        ]
        if len({connection.getToLane().getID() for connection in outgoing}) != 1:
            continue
        connection = outgoing[0]
        target = connection.getToLane()
        target_id = target.getID()
        target_row = by_lane.get(target_id)
        source_stem, source_cut, _ = lane.getEdge().getID().partition("#")
        target_stem, target_cut, _ = target.getEdge().getID().partition("#")
        external_incoming = {
            incoming.getID() for incoming in target.getIncoming() if not incoming.getID().startswith(":")
        }
        if (
            target_row is None
            or not source_cut
            or not target_cut
            or source_stem != target_stem
            or connection.getDirection() != "s"
            or math.dist(lane.getShape()[-1], target.getShape()[0]) > SERIAL_CUT_MAX_GAP_M
            or lane.getIndex() != target.getIndex()
            or abs(float(row["lane_length_m"]) - float(row["lane_position_m"])) > 1.0
            or float(target_row["lane_position_m"]) > 1.0
            or external_incoming != {lane_id}
        ):
            continue
        neighbours[lane_id].add(target_id)
        neighbours[target_id].add(lane_id)
        predecessors[target_id].add(lane_id)

    collapsed: list[dict[str, Any]] = []
    unseen = set(by_lane)
    while unseen:
        seed = min(unseen)
        component = {seed}
        pending = [seed]
        while pending:
            current = pending.pop()
            for neighbour in neighbours[current] - component:
                component.add(neighbour)
                pending.append(neighbour)
        unseen -= component
        roots = [lane_id for lane_id in component if not (predecessors[lane_id] & component)]
        representative_id = min(
            roots or component,
            key=lambda lane_id: (
                float(by_lane[lane_id]["distance_m"]),
                str(by_lane[lane_id]["edge_id"]),
                lane_id,
            ),
        )
        representative = dict(by_lane[representative_id])
        representative["equivalent_segment_lane_ids"] = sorted(component)
        representative["candidate_basis"] = (
            "same_osm_way_one_to_one_serial_cut" if len(component) > 1 else "single_sumo_lane_segment"
        )
        representative["equivalent_segment_candidates"] = [
            dict(by_lane[lane_id]) for lane_id in sorted(component)
        ]
        collapsed.append(representative)

    collapsed.sort(key=lambda item: (item["distance_m"], item["edge_id"], item["lane_id"]))
    return collapsed


__all__ = [
    "DEFAULT_NETWORK_PROJECTION",
    "DETECTOR_BINDING_SCHEMA",
    "HamburgDetectorBindingError",
    "materialize_hamburg_named_detector_bindings",
]
