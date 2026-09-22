from __future__ import annotations

import csv
import hashlib
import json
import math
import xml.etree.ElementTree as ET
from dataclasses import dataclass, replace
from datetime import datetime
from itertools import permutations
from pathlib import Path
from typing import Iterable, Mapping, Sequence

from pyproj import Transformer
import sumolib

from .detector_demand import Detector, lane_allows_passenger, safe_id
from .digital_twin import (
    CanonicalCount,
    CountStream,
    MapLane,
    SignalObservation,
    SignalStream,
    angular_difference_degrees,
    lane_heading_degrees,
    map_signal_state_to_sumo,
)


@dataclass(frozen=True)
class NetworkLane:
    lane_id: str
    edge_id: str
    length: float
    shape: tuple[tuple[float, float], ...]


@dataclass(frozen=True)
class _MapLaneCandidate:
    score: float
    position: float
    distance: float
    heading_error: float | None
    network_lane: NetworkLane


@dataclass(frozen=True)
class _PreparedMapLane:
    source_index: int
    lane: MapLane
    role: str
    approach: str
    anchor: tuple[float, float]
    source_heading: float | None
    candidates: tuple[_MapLaneCandidate, ...]


@dataclass(frozen=True)
class MapLaneBinding:
    node_id: str
    map_lane_id: str
    map_lane_type: str
    map_role: str
    sumo_edge: str
    sumo_lane: str
    lane_position: float
    distance_m: float
    heading_error_deg: float | None
    mapping_confidence: str
    mapping_status: str


@dataclass(frozen=True)
class DetectorMapping:
    detector_id: str
    stream_id: int
    node_id: str
    asset_id: str
    real_direction: str
    lane_use: str
    longitude: float
    latitude: float
    official_map_lane: str
    official_map_distance_m: float | None
    sumo_edge: str
    sumo_lane: str
    lane_position: float
    distance_m: float | None
    heading_error_deg: float | None
    period: int
    mapping_confidence: str
    mapping_status: str
    mapping_reason: str


@dataclass(frozen=True)
class VirtualDetectorGroup:
    """One auditable SUMO detector representing official fields collapsed onto one lane.

    Groups are deliberately keyed by both the physical Hamburg node and the SUMO lane.  This
    prevents a simplified OSM network from silently merging count stations at different junctions.
    ``lane_position`` is the downstream-most (maximum) active source position, a deterministic
    choice that keeps the virtual loop closest to the stop line represented by the source fields.
    The source-id tuples use the same order and therefore preserve field membership exactly.
    """

    virtual_detector_id: str
    node_id: str
    sumo_edge: str
    sumo_lane: str
    lane_position: float
    period: int
    source_detector_ids: tuple[str, ...]
    source_stream_ids: tuple[int, ...]
    source_mapping_confidences: tuple[str, ...]
    position_rule: str = "downstream_most_active_source_position_max"

    def as_detector(self) -> Detector:
        return Detector(
            detector_id=self.virtual_detector_id,
            source_system="hamburg_official_aggregated_count_fields",
            direction="aggregated_official_fields",
            edge_id=self.sumo_edge,
            lane_id=self.sumo_lane,
            lane_position=self.lane_position,
            period=str(self.period),
            mapping_confidence=_weakest_confidence(self.source_mapping_confidences),
            mapping_status="active",
        )


@dataclass(frozen=True)
class VirtualExpectedCount:
    virtual_detector_id: str
    node_id: str
    sumo_edge: str
    sumo_lane: str
    begin: int
    end: int
    expected_total: int
    source_detector_ids: tuple[str, ...]
    source_stream_ids: tuple[int, ...]
    source_row_count: int
    quality_status: str = "complete"


@dataclass(frozen=True)
class VirtualSensorAggregation:
    groups: tuple[VirtualDetectorGroup, ...]
    detectors: tuple[Detector, ...]
    expected_counts: tuple[VirtualExpectedCount, ...]


@dataclass(frozen=True)
class EdgeFlow:
    begin: int
    end: int
    edge_id: str
    count: int
    detector_ids: tuple[str, ...]
    node_ids: tuple[str, ...]
    lane_ids: tuple[str, ...]


@dataclass(frozen=True)
class EdgeConstraintAudit:
    node_id: str
    edge_id: str
    network_passenger_lanes: tuple[str, ...]
    observed_lanes: tuple[str, ...]
    detector_ids: tuple[str, ...]
    position_spread_m: float
    constraint_status: str
    constraint_reason: str


@dataclass(frozen=True)
class TlsBinding:
    stream_id: int
    node_id: str
    connection_id: str
    signal_group: str
    official_ingress_lane: str
    official_egress_lane: str
    sumo_from_lane: str
    sumo_to_lane: str
    sumo_tls_id: str
    sumo_link_index: int | None
    mapping_confidence: str
    mapping_status: str
    mapping_reason: str


@dataclass(frozen=True)
class LanePathArc:
    """One directed external-lane connection used only for bounded path search.

    ``evidence_id`` is populated when the arc is backed by caller-supplied
    connection evidence rather than (or in addition to) a connection already
    present in the SUMO network.  It carries no TLS ownership semantics.
    """

    from_lane: str
    to_lane: str
    tls_id: str
    link_index: int | None
    to_lane_length_m: float
    evidence_id: str = ""


@dataclass(frozen=True, order=True)
class LaneConnectionEvidence:
    """An explicit missing lane connection admitted as connectivity evidence.

    The endpoints are full SUMO lane ids.  This deliberately does not expose
    ``tl`` or ``linkIndex`` fields: evidence may make an official movement
    routable, but cannot select or assign a controlled TLS arc.
    """

    from_lane: str
    to_lane: str
    evidence_id: str
    reason: str = ""


# Backwards-compatible private name for the existing TLS-binding implementation.
_LaneArc = LanePathArc


def read_network_lanes(net_file: Path) -> tuple[object, list[NetworkLane]]:
    net = sumolib.net.readNet(str(net_file), withInternal=True)
    lanes: list[NetworkLane] = []
    for edge in net.getEdges(withInternal=True):
        edge_id = edge.getID()
        if edge_id.startswith(":") or getattr(edge, "getFunction", lambda: "")():
            continue
        for lane in edge.getLanes():
            if not (lane.allows("passenger") or lane.allows("private")):
                continue
            shape = tuple((float(x), float(y)) for x, y in lane.getShape())
            if len(shape) < 2:
                continue
            lanes.append(
                NetworkLane(
                    lane_id=lane.getID(),
                    edge_id=edge_id,
                    length=float(lane.getLength()),
                    shape=shape,
                )
            )
    if not lanes:
        raise ValueError(f"network has no passenger lanes: {net_file}")
    return net, lanes


def bind_map_lanes_to_network(
    net_file: Path,
    map_lanes: Sequence[MapLane],
    *,
    max_distance_m: float = 50.0,
    max_heading_error_deg: float = 80.0,
    network_projection: str | None = None,
) -> list[MapLaneBinding]:
    net, network_lanes = read_network_lanes(net_file)
    prepared: list[_PreparedMapLane] = []
    for source_index, lane in enumerate(map_lanes):
        if not lane.is_vehicle or not lane.points_m:
            continue
        map_xy = tuple(
            _map_point_to_network(net, lane, point, network_projection=network_projection)
            for point in lane.points_m
        )
        source_heading = lane_heading_degrees(map_xy, ingress=lane.is_ingress)
        anchor = map_xy[0]
        candidates: list[_MapLaneCandidate] = []
        for network_lane in network_lanes:
            position, distance, heading = project_point_to_polyline(anchor, network_lane.shape)
            heading_error = (
                angular_difference_degrees(source_heading, heading) if source_heading is not None else None
            )
            if distance <= max_distance_m and (
                heading_error is None or heading_error <= max_heading_error_deg
            ):
                candidates.append(
                    _MapLaneCandidate(
                        score=distance + 0.20 * (heading_error or 0.0),
                        position=position,
                        distance=distance,
                        heading_error=heading_error,
                        network_lane=network_lane,
                    )
                )
        candidates.sort(
            key=lambda item: (
                item.score,
                item.network_lane.edge_id,
                item.network_lane.lane_id,
            )
        )
        prepared.append(
            _PreparedMapLane(
                source_index=source_index,
                lane=lane,
                role="ingress" if lane.is_ingress else "egress",
                approach=lane.ingress_approach if lane.is_ingress else lane.egress_approach,
                anchor=anchor,
                source_heading=source_heading,
                candidates=tuple(candidates),
            )
        )

    selected: dict[int, tuple[_MapLaneCandidate | None, bool]] = {}
    for group in _parallel_map_lane_groups(prepared):
        assignments, ambiguous = _minimum_cost_parallel_assignment(group)
        for item, candidate in zip(group, assignments):
            selected[item.source_index] = (candidate, ambiguous)

    return [
        _map_lane_binding(item, *selected[item.source_index])
        for item in sorted(prepared, key=lambda value: value.source_index)
    ]


def bind_map_lanes_to_explicit_network_lanes(
    net_file: Path,
    map_lanes: Sequence[MapLane],
    explicit_lane_by_map_lane: Mapping[tuple[str, str], str],
    *,
    max_distance_m: float = 50.0,
    max_heading_error_deg: float = 80.0,
    network_projection: str | None = None,
) -> list[MapLaneBinding]:
    """Project selected official MAP lanes onto caller-proven SUMO lanes.

    This is the fail-closed companion to nearest-lane matching.  It is intended
    for a teacher/replay contract that already proves which physical lane was
    materialized.  Distance, heading, confidence, and active/review status are
    still recomputed from the final network; an explicit target is authority for
    identity, not permission to bypass geometric validation.
    """

    if not math.isfinite(max_distance_m) or max_distance_m < 0:
        raise ValueError("max_distance_m must be finite and non-negative")
    if not math.isfinite(max_heading_error_deg) or not 0 <= max_heading_error_deg <= 180:
        raise ValueError("max_heading_error_deg must be finite and between 0 and 180")
    requested = {
        (_normalize_node(str(node_id)), str(map_lane_id)): str(sumo_lane_id)
        for (node_id, map_lane_id), sumo_lane_id in explicit_lane_by_map_lane.items()
        if str(node_id) and str(map_lane_id)
    }
    if len(requested) != len(explicit_lane_by_map_lane):
        raise ValueError("explicit MAP-lane keys must be non-empty and unique after normalization")
    if any(not lane_id for lane_id in requested.values()):
        raise ValueError("explicit SUMO lane ids must be non-empty")

    net, network_lanes = read_network_lanes(net_file)
    network_lane_by_id = {lane.lane_id: lane for lane in network_lanes}
    map_lane_by_key = {
        (_normalize_node(lane.node_id), lane.lane_id): lane
        for lane in map_lanes
        if lane.is_vehicle and lane.points_m
    }
    unknown_keys = sorted(set(requested) - set(map_lane_by_key))
    if unknown_keys:
        raise ValueError(f"explicit binding references unknown MAP lanes: {unknown_keys}")

    result: list[MapLaneBinding] = []
    for key in sorted(requested):
        lane = map_lane_by_key[key]
        role = "ingress" if lane.is_ingress else "egress"
        network_lane = network_lane_by_id.get(requested[key])
        if network_lane is None:
            result.append(
                MapLaneBinding(
                    node_id=lane.node_id,
                    map_lane_id=lane.lane_id,
                    map_lane_type=lane.lane_type,
                    map_role=role,
                    sumo_edge="",
                    sumo_lane="",
                    lane_position=0.0,
                    distance_m=math.inf,
                    heading_error_deg=None,
                    mapping_confidence="none",
                    mapping_status="unmapped",
                )
            )
            continue
        map_xy = tuple(
            _map_point_to_network(net, lane, point, network_projection=network_projection)
            for point in lane.points_m
        )
        source_heading = lane_heading_degrees(map_xy, ingress=lane.is_ingress)
        position, distance, network_heading = project_point_to_polyline(
            map_xy[0], network_lane.shape
        )
        heading_error = (
            angular_difference_degrees(source_heading, network_heading)
            if source_heading is not None
            else None
        )
        candidate = _MapLaneCandidate(
            score=distance + 0.20 * (heading_error or 0.0),
            position=position,
            distance=distance,
            heading_error=heading_error,
            network_lane=network_lane,
        )
        prepared = _PreparedMapLane(
            source_index=0,
            lane=lane,
            role=role,
            approach=lane.ingress_approach if lane.is_ingress else lane.egress_approach,
            anchor=map_xy[0],
            source_heading=source_heading,
            candidates=(candidate,),
        )
        binding = _map_lane_binding(prepared, candidate, False)
        if distance > max_distance_m or (
            heading_error is not None and heading_error > max_heading_error_deg
        ):
            binding = replace(
                binding,
                mapping_confidence="none",
                mapping_status="needs_review",
            )
        result.append(binding)
    return result


def _parallel_map_lane_groups(
    prepared: Sequence[_PreparedMapLane],
) -> list[tuple[_PreparedMapLane, ...]]:
    """Group only MAP lanes that describe one physical cross-section.

    Hamburg MAP data can reuse an approach id for consecutive lane segments.  Those
    segments must remain independently mappable, so approach identity alone is not
    enough: anchors also need to be laterally adjacent with little longitudinal
    separation.  Connected components allow a three-lane cross-section to be
    recovered through neighbouring lanes without depending on source row order.
    """

    base_groups: dict[tuple[str, str, str], list[_PreparedMapLane]] = {}
    for item in prepared:
        key = (_normalize_node(item.lane.node_id), item.role, item.approach.strip())
        base_groups.setdefault(key, []).append(item)

    result: list[tuple[_PreparedMapLane, ...]] = []
    for key in sorted(base_groups):
        remaining = {item.source_index: item for item in base_groups[key]}
        while remaining:
            seed_index = min(remaining)
            component_indices = {seed_index}
            frontier = [seed_index]
            while frontier:
                current_index = frontier.pop()
                current = remaining[current_index]
                neighbours = [
                    other_index
                    for other_index, other in remaining.items()
                    if other_index not in component_indices
                    and _map_lane_anchors_are_parallel(current, other)
                ]
                component_indices.update(neighbours)
                frontier.extend(neighbours)
            component = tuple(
                remaining.pop(index) for index in sorted(component_indices)
            )
            result.append(component)
    return sorted(result, key=lambda group: min(item.source_index for item in group))


def _map_lane_anchors_are_parallel(left: _PreparedMapLane, right: _PreparedMapLane) -> bool:
    dx = right.anchor[0] - left.anchor[0]
    dy = right.anchor[1] - left.anchor[1]
    if left.source_heading is None or right.source_heading is None:
        return math.hypot(dx, dy) <= 12.0
    if angular_difference_degrees(left.source_heading, right.source_heading) > 35.0:
        return False
    heading = math.radians(left.source_heading)
    longitudinal = abs(dx * math.cos(heading) + dy * math.sin(heading))
    lateral = abs(-dx * math.sin(heading) + dy * math.cos(heading))
    return longitudinal <= 10.0 and lateral <= 12.0


def _minimum_cost_parallel_assignment(
    group: Sequence[_PreparedMapLane],
) -> tuple[tuple[_MapLaneCandidate | None, ...], bool]:
    """Assign parallel official lanes uniquely when the SUMO cross-section permits it.

    A SUMO edge is the smallest reliable representation of one cross-section.  The
    independently nearest candidates may straddle a short lane-count transition (for
    example two lanes on a geometry stub followed immediately by the real three-lane
    cross-section).  Therefore every candidate edge is considered, rather than only
    the edge selected by all independent nearest matches.  If no one-edge assignment
    has enough distinct lanes, retain the auditable many-to-one fallback used by
    genuinely simplified OSM networks.
    """

    independent = tuple(item.candidates[0] if item.candidates else None for item in group)
    if len(group) <= 1:
        return independent, False

    independent_candidates = [item for item in independent if item is not None]
    if len(independent_candidates) != len(group):
        return independent, False
    independent_lane_ids = {item.network_lane.lane_id for item in independent_candidates}
    independent_edge_ids = {item.network_lane.edge_id for item in independent_candidates}
    if len(independent_lane_ids) == len(group) and len(independent_edge_ids) == 1:
        return independent, False
    candidate_edge_ids = sorted(
        set.intersection(
            *(
                {candidate.network_lane.edge_id for candidate in item.candidates}
                for item in group
            )
        )
    )
    ranked: list[
        tuple[float, str, tuple[str, ...], tuple[_MapLaneCandidate, ...]]
    ] = []
    for edge_id in candidate_edge_ids:
        rows = [
            {
                candidate.network_lane.lane_id: candidate
                for candidate in item.candidates
                if candidate.network_lane.edge_id == edge_id
            }
            for item in group
        ]
        lane_ids = sorted({lane_id for row in rows for lane_id in row})
        if len(lane_ids) < len(group):
            continue
        for lane_assignment in permutations(lane_ids, len(group)):
            if any(
                lane_id not in row
                for lane_id, row in zip(lane_assignment, rows, strict=True)
            ):
                continue
            assignment = tuple(
                row[lane_id]
                for lane_id, row in zip(lane_assignment, rows, strict=True)
            )
            ranked.append(
                (
                    sum(item.score for item in assignment),
                    edge_id,
                    lane_assignment,
                    assignment,
                )
            )

    if not ranked:
        return independent, False
    ranked.sort(key=lambda item: (item[0], item[1], item[2]))
    independent_cost = sum(item.score for item in independent_candidates)
    # A coherent cross-section may cost slightly more than independently snapping
    # every official lane to a micro stub.  A larger displacement is not evidence
    # for silently choosing a different road and therefore retains the fallback.
    if ranked[0][0] > independent_cost + 2.0 * len(group):
        return independent, False
    ambiguous = len(ranked) > 1 and math.isclose(
        ranked[0][0],
        ranked[1][0],
        rel_tol=1e-12,
        abs_tol=1e-9,
    )
    return ranked[0][3], ambiguous


def _map_lane_binding(
    item: _PreparedMapLane,
    candidate: _MapLaneCandidate | None,
    ambiguous: bool,
) -> MapLaneBinding:
    lane = item.lane
    if candidate is None:
        return MapLaneBinding(
            node_id=lane.node_id,
            map_lane_id=lane.lane_id,
            map_lane_type=lane.lane_type,
            map_role=item.role,
            sumo_edge="",
            sumo_lane="",
            lane_position=0.0,
            distance_m=math.inf,
            heading_error_deg=None,
            mapping_confidence="none",
            mapping_status="unmapped",
        )
    network_lane = candidate.network_lane
    confidence = _confidence(candidate.distance, candidate.heading_error)
    return MapLaneBinding(
        node_id=lane.node_id,
        map_lane_id=lane.lane_id,
        map_lane_type=lane.lane_type,
        map_role=item.role,
        sumo_edge=network_lane.edge_id,
        sumo_lane=network_lane.lane_id,
        lane_position=_safe_lane_position(candidate.position, network_lane.length),
        distance_m=candidate.distance,
        heading_error_deg=candidate.heading_error,
        mapping_confidence=confidence,
        mapping_status=(
            "needs_review" if ambiguous or confidence == "low" else "active"
        ),
    )


def bind_count_streams_to_network(
    net_file: Path,
    streams: Sequence[CountStream],
    map_lanes: Sequence[MapLane],
    map_bindings: Sequence[MapLaneBinding] | None = None,
    *,
    period: int = 900,
    network_projection: str | None = None,
) -> list[DetectorMapping]:
    net, network_lanes = read_network_lanes(net_file)
    lane_bindings = list(map_bindings or bind_map_lanes_to_network(net_file, map_lanes))
    lane_binding_index = {
        (_normalize_node(binding.node_id), binding.map_lane_id): binding for binding in lane_bindings
    }
    lanes_by_node: dict[str, list[MapLane]] = {}
    for lane in map_lanes:
        if lane.is_vehicle and lane.is_ingress and lane.points_m:
            lanes_by_node.setdefault(_normalize_node(lane.node_id), []).append(lane)

    results: list[DetectorMapping] = []
    for stream in streams:
        node_lanes = lanes_by_node.get(_normalize_node(stream.node_id), [])
        matched_map_lane, map_distance = _nearest_map_lane(stream, node_lanes)
        binding = (
            lane_binding_index.get((_normalize_node(stream.node_id), matched_map_lane.lane_id))
            if matched_map_lane is not None
            else None
        )
        if binding is not None and binding.mapping_status == "active":
            results.append(
                DetectorMapping(
                    detector_id=stream.detector_id,
                    stream_id=stream.stream_id,
                    node_id=stream.node_id,
                    asset_id=stream.asset_id,
                    real_direction=stream.direction,
                    lane_use=stream.lane_use,
                    longitude=stream.longitude,
                    latitude=stream.latitude,
                    official_map_lane=matched_map_lane.lane_id if matched_map_lane else "",
                    official_map_distance_m=map_distance,
                    sumo_edge=binding.sumo_edge,
                    sumo_lane=binding.sumo_lane,
                    lane_position=binding.lane_position,
                    distance_m=binding.distance_m,
                    heading_error_deg=binding.heading_error_deg,
                    period=period,
                    mapping_confidence=binding.mapping_confidence,
                    mapping_status="active",
                    mapping_reason="Hamburg field -> official MAP ingress lane -> SUMO lane",
                )
            )
            continue

        # MAP geometry can be absent or not match a cleaned network.  The nearest
        # lane fallback is emitted for review but never silently promoted.
        x, y = _convert_network_lonlat2xy(
            net,
            stream.longitude,
            stream.latitude,
            network_projection=network_projection,
        )
        nearest = min(
            (
                (*project_point_to_polyline((x, y), lane.shape)[:2], lane)
                for lane in network_lanes
            ),
            key=lambda item: item[1],
            default=None,
        )
        if nearest is None:
            results.append(_unmapped_detector(stream, period, "network contains no candidate lane"))
            continue
        position, distance, network_lane = nearest
        results.append(
            DetectorMapping(
                detector_id=stream.detector_id,
                stream_id=stream.stream_id,
                node_id=stream.node_id,
                asset_id=stream.asset_id,
                real_direction=stream.direction,
                lane_use=stream.lane_use,
                longitude=stream.longitude,
                latitude=stream.latitude,
                official_map_lane=matched_map_lane.lane_id if matched_map_lane else "",
                official_map_distance_m=map_distance,
                sumo_edge=network_lane.edge_id,
                sumo_lane=network_lane.lane_id,
                lane_position=_safe_lane_position(position, network_lane.length),
                distance_m=distance,
                heading_error_deg=None,
                period=period,
                mapping_confidence="low",
                mapping_status="needs_review",
                mapping_reason="nearest-lane fallback lacks official MAP direction confirmation",
            )
        )
    return results


def write_detector_mapping(path: Path, mappings: Iterable[DetectorMapping]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "detector_id",
        "stream_id",
        "node_id",
        "asset_id",
        "source_system",
        "real_direction",
        "lane_use",
        "longitude",
        "latitude",
        "official_map_lane",
        "official_map_distance_m",
        "sumo_edge",
        "sumo_lane",
        "lane_position",
        "distance_m",
        "heading_error_deg",
        "period",
        "mapping_confidence",
        "mapping_status",
        "mapping_reason",
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for mapping in mappings:
            row = mapping.__dict__.copy()
            row["source_system"] = "hamburg_official_infrared_count_fields"
            row["official_map_distance_m"] = _format_optional(mapping.official_map_distance_m)
            row["distance_m"] = _format_optional(mapping.distance_m)
            row["heading_error_deg"] = _format_optional(mapping.heading_error_deg)
            writer.writerow(row)


def build_virtual_sensor_aggregation(
    mappings: Sequence[DetectorMapping],
    counts: Sequence[CanonicalCount],
    *,
    bin_seconds: int = 900,
    expected_begin: int | None = None,
    expected_end: int | None = None,
    group_policies: Mapping[tuple[str, str], str] | None = None,
) -> VirtualSensorAggregation:
    """Collapse active official fields onto one E1 detector per ``(node_id, SUMO lane)``.

    The operation fails closed if a source mapping is ambiguous, a canonical row is incomplete,
    active sources do not cover exactly the same intervals, or the requested interval range is not
    fully present.  Summing official fields is only safe after those checks: otherwise the result
    would make missing data look like observed zero traffic.
    """

    if bin_seconds <= 0:
        raise ValueError("bin_seconds must be positive")
    policies = dict(group_policies or {})
    invalid_policies = sorted({value for value in policies.values() if value not in {"sum", "max"}})
    if invalid_policies:
        raise ValueError(f"unsupported virtual detector group policies: {invalid_policies}")
    if (expected_begin is None) != (expected_end is None):
        raise ValueError("expected_begin and expected_end must be provided together")
    if expected_begin is not None and expected_end is not None:
        if expected_end <= expected_begin:
            raise ValueError("expected_end must be greater than expected_begin")
        if (expected_end - expected_begin) % bin_seconds:
            raise ValueError("expected interval range must be divisible by bin_seconds")

    active_mappings = [mapping for mapping in mappings if mapping.mapping_status == "active"]
    if not active_mappings:
        raise ValueError("no active detector mappings are available for virtual aggregation")

    mapping_by_stream: dict[int, DetectorMapping] = {}
    groups_by_key: dict[tuple[str, str], list[DetectorMapping]] = {}
    for mapping in active_mappings:
        _validate_active_mapping(mapping, bin_seconds=bin_seconds)
        previous = mapping_by_stream.get(mapping.stream_id)
        if previous is not None:
            raise ValueError(f"active count stream {mapping.stream_id} has multiple detector mappings")
        mapping_by_stream[mapping.stream_id] = mapping
        groups_by_key.setdefault((mapping.node_id.strip(), mapping.sumo_lane.strip()), []).append(mapping)

    groups: list[VirtualDetectorGroup] = []
    generated_ids: dict[str, tuple[str, str]] = {}
    group_by_stream: dict[int, VirtualDetectorGroup] = {}
    for (node_id, sumo_lane), source_mappings in sorted(groups_by_key.items()):
        edge_ids = {mapping.sumo_edge.strip() for mapping in source_mappings}
        if len(edge_ids) != 1:
            raise ValueError(
                f"node {node_id!r} SUMO lane {sumo_lane!r} maps to multiple edges {sorted(edge_ids)}"
            )
        ordered_sources = sorted(source_mappings, key=lambda item: (item.stream_id, item.detector_id))
        virtual_id = _virtual_detector_id(node_id, sumo_lane)
        previous_key = generated_ids.get(virtual_id)
        if previous_key is not None and previous_key != (node_id, sumo_lane):
            raise ValueError(
                f"virtual detector id collision for {previous_key!r} and {(node_id, sumo_lane)!r}: {virtual_id!r}"
            )
        generated_ids[virtual_id] = (node_id, sumo_lane)
        group = VirtualDetectorGroup(
            virtual_detector_id=virtual_id,
            node_id=node_id,
            sumo_edge=next(iter(edge_ids)),
            sumo_lane=sumo_lane,
            lane_position=max(mapping.lane_position for mapping in ordered_sources),
            period=bin_seconds,
            source_detector_ids=tuple(mapping.detector_id for mapping in ordered_sources),
            source_stream_ids=tuple(mapping.stream_id for mapping in ordered_sources),
            source_mapping_confidences=tuple(mapping.mapping_confidence for mapping in ordered_sources),
        )
        groups.append(group)
        for mapping in ordered_sources:
            group_by_stream[mapping.stream_id] = group

    rows_by_stream: dict[int, dict[tuple[int, int], CanonicalCount]] = {
        stream_id: {} for stream_id in mapping_by_stream
    }
    for row in counts:
        mapping = mapping_by_stream.get(row.stream_id)
        if mapping is None:
            matching = [item for item in mappings if item.stream_id == row.stream_id]
            if matching:
                raise ValueError(f"count stream {row.stream_id} does not have an active detector mapping")
            raise ValueError(f"count stream {row.stream_id} has no detector mapping")
        _validate_canonical_virtual_row(row, mapping, bin_seconds=bin_seconds)
        interval = (row.begin, row.end)
        if interval in rows_by_stream[row.stream_id]:
            raise ValueError(
                f"duplicate canonical row for stream {row.stream_id} in interval {row.begin}-{row.end}"
            )
        rows_by_stream[row.stream_id][interval] = row

    interval_sets = {stream_id: set(rows) for stream_id, rows in rows_by_stream.items()}
    missing_streams = sorted(stream_id for stream_id, intervals in interval_sets.items() if not intervals)
    if missing_streams:
        raise ValueError(f"active detector streams have no canonical rows: {missing_streams}")
    reference_stream = min(interval_sets)
    required_intervals = interval_sets[reference_stream]
    for stream_id, intervals in sorted(interval_sets.items()):
        if intervals != required_intervals:
            missing = sorted(required_intervals - intervals)
            extra = sorted(intervals - required_intervals)
            raise ValueError(
                f"canonical interval coverage differs for stream {stream_id}; missing={missing}, extra={extra}"
            )

    sorted_intervals = sorted(required_intervals)
    if sorted_intervals:
        contiguous = [
            (begin, begin + bin_seconds)
            for begin in range(sorted_intervals[0][0], sorted_intervals[-1][1], bin_seconds)
        ]
        if sorted_intervals != contiguous:
            raise ValueError("canonical 15-minute intervals are not contiguous")
    if expected_begin is not None and expected_end is not None:
        expected_intervals = [
            (begin, begin + bin_seconds) for begin in range(expected_begin, expected_end, bin_seconds)
        ]
        if sorted_intervals != expected_intervals:
            missing = sorted(set(expected_intervals) - required_intervals)
            extra = sorted(required_intervals - set(expected_intervals))
            raise ValueError(f"canonical rows do not cover the requested window; missing={missing}, extra={extra}")

    expected_counts: list[VirtualExpectedCount] = []
    for group in groups:
        policy = policies.get((group.node_id, group.sumo_lane), "sum")
        for begin, end in sorted_intervals:
            source_rows = [rows_by_stream[stream_id][(begin, end)] for stream_id in group.source_stream_ids]
            expected_counts.append(
                VirtualExpectedCount(
                    virtual_detector_id=group.virtual_detector_id,
                    node_id=group.node_id,
                    sumo_edge=group.sumo_edge,
                    sumo_lane=group.sumo_lane,
                    begin=begin,
                    end=end,
                    expected_total=(
                        max(row.count for row in source_rows)
                        if policy == "max"
                        else sum(row.count for row in source_rows)
                    ),
                    source_detector_ids=group.source_detector_ids,
                    source_stream_ids=group.source_stream_ids,
                    source_row_count=len(source_rows),
                )
            )

    return VirtualSensorAggregation(
        groups=tuple(groups),
        detectors=tuple(group.as_detector() for group in groups),
        expected_counts=tuple(expected_counts),
    )


def write_virtual_detector_mapping(path: Path, groups: Iterable[VirtualDetectorGroup]) -> None:
    """Write one auditable row per virtual detector with JSON-encoded source membership."""

    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "virtual_detector_id",
        "node_id",
        "sumo_edge",
        "sumo_lane",
        "lane_position",
        "period",
        "position_rule",
        "source_detector_ids",
        "source_stream_ids",
        "source_mapping_confidences",
        "source_detector_count",
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for group in sorted(groups, key=lambda item: item.virtual_detector_id):
            writer.writerow(
                {
                    "virtual_detector_id": group.virtual_detector_id,
                    "node_id": group.node_id,
                    "sumo_edge": group.sumo_edge,
                    "sumo_lane": group.sumo_lane,
                    "lane_position": f"{group.lane_position:.3f}",
                    "period": group.period,
                    "position_rule": group.position_rule,
                    "source_detector_ids": _json_tuple(group.source_detector_ids),
                    "source_stream_ids": _json_tuple(group.source_stream_ids),
                    "source_mapping_confidences": _json_tuple(group.source_mapping_confidences),
                    "source_detector_count": len(group.source_detector_ids),
                }
            )


def write_virtual_expected_counts(path: Path, counts: Iterable[VirtualExpectedCount]) -> None:
    """Write interval counts directly consumable by ``audit_expected_to_e1_strict``."""

    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "detector_id",
        "edge_id",
        "lane_id",
        "node_id",
        "begin",
        "end",
        "expected_total",
        "source_detector_ids",
        "source_stream_ids",
        "source_row_count",
        "quality_status",
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in sorted(counts, key=lambda item: (item.begin, item.virtual_detector_id)):
            writer.writerow(
                {
                    "detector_id": row.virtual_detector_id,
                    "edge_id": row.sumo_edge,
                    "lane_id": row.sumo_lane,
                    "node_id": row.node_id,
                    "begin": row.begin,
                    "end": row.end,
                    "expected_total": row.expected_total,
                    "source_detector_ids": _json_tuple(row.source_detector_ids),
                    "source_stream_ids": _json_tuple(row.source_stream_ids),
                    "source_row_count": row.source_row_count,
                    "quality_status": row.quality_status,
                }
            )


def aggregate_canonical_counts_to_edges(
    counts: Sequence[CanonicalCount],
    mappings: Sequence[DetectorMapping],
    *,
    require_active: bool = True,
) -> tuple[list[EdgeFlow], list[str]]:
    mapping_by_stream = {mapping.stream_id: mapping for mapping in mappings}
    grouped: dict[tuple[int, int, str], dict[str, object]] = {}
    warnings: list[str] = []
    for row in counts:
        if row.quality_status != "complete":
            raise ValueError(f"count row {row.detector_id} {row.begin}-{row.end} has missing source bins")
        mapping = mapping_by_stream.get(row.stream_id)
        if mapping is None or not mapping.sumo_edge:
            if require_active:
                raise ValueError(f"count stream {row.stream_id} has no SUMO edge mapping")
            continue
        if mapping.mapping_status != "active":
            if require_active:
                raise ValueError(f"count stream {row.stream_id} mapping is not active")
            continue
        key = (row.begin, row.end, mapping.sumo_edge)
        bucket = grouped.setdefault(
            key,
            {"count": 0, "detector_ids": set(), "node_ids": set(), "lane_ids": set()},
        )
        bucket["count"] = int(bucket["count"]) + row.count
        bucket["detector_ids"].add(mapping.detector_id)  # type: ignore[union-attr]
        bucket["node_ids"].add(mapping.node_id)  # type: ignore[union-attr]
        bucket["lane_ids"].add(mapping.sumo_lane)  # type: ignore[union-attr]

    flows: list[EdgeFlow] = []
    for (begin, end, edge_id), bucket in sorted(grouped.items()):
        node_ids = tuple(sorted(bucket["node_ids"]))  # type: ignore[arg-type]
        if len(node_ids) > 1:
            raise ValueError(
                f"SUMO edge {edge_id} receives detectors from multiple physical nodes {node_ids}; "
                "the network must be split or mappings reviewed before aggregation"
            )
        lane_ids = tuple(sorted(bucket["lane_ids"]))  # type: ignore[arg-type]
        detector_ids = tuple(sorted(bucket["detector_ids"]))  # type: ignore[arg-type]
        if len(detector_ids) > len(lane_ids):
            warnings.append(
                f"edge {edge_id} interval {begin}-{end} has more source fields than mapped lanes; review double counting"
            )
        flows.append(
            EdgeFlow(
                begin=begin,
                end=end,
                edge_id=edge_id,
                count=int(bucket["count"]),
                detector_ids=detector_ids,
                node_ids=node_ids,
                lane_ids=lane_ids,
            )
        )
    return flows, sorted(set(warnings))


def aggregate_virtual_counts_to_complete_edge_sections(
    net_file: Path,
    aggregation: VirtualSensorAggregation,
    *,
    max_position_spread_m: float = 15.0,
) -> tuple[list[EdgeFlow], list[EdgeConstraintAudit]]:
    """Build routeSampler edge counts only from complete measured cross-sections.

    Hamburg's source fields are lane-level.  SUMO routeSampler's ``edgeData`` constraint is
    edge-level, so treating a single measured lane as the total for a multi-lane edge creates a
    false conservation constraint.  Every passenger lane on an edge must be represented at the
    same physical node and approximately the same longitudinal section before the lane counts are
    summed for routeSampler.  All source fields remain available separately through the virtual E1
    expected-count file regardless of this eligibility decision.
    """

    if not math.isfinite(max_position_spread_m) or max_position_spread_m < 0:
        raise ValueError("max_position_spread_m must be finite and non-negative")
    root = ET.parse(net_file).getroot()
    passenger_lanes: dict[str, tuple[str, ...]] = {}
    for edge in root.findall("edge"):
        edge_id = edge.attrib.get("id", "")
        if not edge_id or edge_id.startswith(":") or edge.attrib.get("function"):
            continue
        lanes = tuple(
            sorted(
                lane.attrib["id"]
                for lane in edge.findall("lane")
                if lane.attrib.get("id") and lane_allows_passenger(lane)
            )
        )
        if lanes:
            passenger_lanes[edge_id] = lanes

    groups_by_section: dict[tuple[str, str], list[VirtualDetectorGroup]] = {}
    group_by_id: dict[str, VirtualDetectorGroup] = {}
    for group in aggregation.groups:
        if group.virtual_detector_id in group_by_id:
            raise ValueError(f"duplicate virtual detector id {group.virtual_detector_id!r}")
        group_by_id[group.virtual_detector_id] = group
        groups_by_section.setdefault((group.node_id, group.sumo_edge), []).append(group)

    eligible_sections: set[tuple[str, str]] = set()
    audits: list[EdgeConstraintAudit] = []
    for (node_id, edge_id), groups in sorted(groups_by_section.items()):
        expected_lanes = passenger_lanes.get(edge_id, ())
        observed_lanes = tuple(sorted(group.sumo_lane for group in groups))
        detector_ids = tuple(sorted(group.virtual_detector_id for group in groups))
        positions = [group.lane_position for group in groups]
        position_spread = max(positions) - min(positions) if positions else math.inf
        if not expected_lanes:
            status = "excluded"
            reason = "SUMO edge has no passenger lanes"
        elif len(observed_lanes) != len(set(observed_lanes)):
            status = "excluded"
            reason = "multiple virtual detectors reuse one SUMO lane"
        elif set(observed_lanes) != set(expected_lanes):
            missing = sorted(set(expected_lanes) - set(observed_lanes))
            extra = sorted(set(observed_lanes) - set(expected_lanes))
            status = "excluded"
            reason = f"partial passenger-lane coverage; missing={missing}, unexpected={extra}"
        elif position_spread > max_position_spread_m:
            status = "excluded"
            reason = (
                f"detectors span {position_spread:.3f}m, exceeding the "
                f"{max_position_spread_m:.3f}m cross-section tolerance"
            )
        else:
            status = "active"
            reason = "all SUMO passenger lanes observed at one cross-section"
            eligible_sections.add((node_id, edge_id))
        audits.append(
            EdgeConstraintAudit(
                node_id=node_id,
                edge_id=edge_id,
                network_passenger_lanes=expected_lanes,
                observed_lanes=observed_lanes,
                detector_ids=detector_ids,
                position_spread_m=position_spread,
                constraint_status=status,
                constraint_reason=reason,
            )
        )

    grouped: dict[tuple[int, int, str, str], dict[str, object]] = {}
    for row in aggregation.expected_counts:
        group = group_by_id.get(row.virtual_detector_id)
        if group is None:
            raise ValueError(f"expected count references unknown virtual detector {row.virtual_detector_id!r}")
        section = (row.node_id, row.sumo_edge)
        if section not in eligible_sections:
            continue
        key = (row.begin, row.end, row.node_id, row.sumo_edge)
        bucket = grouped.setdefault(key, {"count": 0, "detector_ids": set(), "lane_ids": set()})
        bucket["count"] = int(bucket["count"]) + row.expected_total
        bucket["detector_ids"].add(row.virtual_detector_id)  # type: ignore[union-attr]
        bucket["lane_ids"].add(group.sumo_lane)  # type: ignore[union-attr]

    flows = [
        EdgeFlow(
            begin=begin,
            end=end,
            edge_id=edge_id,
            count=int(bucket["count"]),
            detector_ids=tuple(sorted(bucket["detector_ids"])),  # type: ignore[arg-type]
            node_ids=(node_id,),
            lane_ids=tuple(sorted(bucket["lane_ids"])),  # type: ignore[arg-type]
        )
        for (begin, end, node_id, edge_id), bucket in sorted(grouped.items())
    ]
    return flows, audits


def write_edge_constraint_audit(path: Path, rows: Iterable[EdgeConstraintAudit]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = list(EdgeConstraintAudit.__dataclass_fields__)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            values = dict(row.__dict__)
            for field in ("network_passenger_lanes", "observed_lanes", "detector_ids"):
                values[field] = _json_tuple(values[field])
            writer.writerow(values)


def write_route_sampler_edge_counts(path: Path, flows: Sequence[EdgeFlow]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    root = ET.Element("data")
    by_interval: dict[tuple[int, int], list[EdgeFlow]] = {}
    for flow in flows:
        by_interval.setdefault((flow.begin, flow.end), []).append(flow)
    for (begin, end), interval_flows in sorted(by_interval.items()):
        interval = ET.SubElement(root, "interval", id=f"counts_{begin}_{end}", begin=str(begin), end=str(end))
        for flow in sorted(interval_flows, key=lambda item: item.edge_id):
            ET.SubElement(
                interval,
                "edge",
                id=flow.edge_id,
                count=str(flow.count),
                detector_ids=" ".join(flow.detector_ids),
            )
    tree = ET.ElementTree(root)
    ET.indent(tree, space="    ")
    tree.write(path, encoding="utf-8", xml_declaration=True)


def write_e2_additional(
    path: Path,
    mappings: Sequence[DetectorMapping],
    *,
    output_file: str = "e2_15min.xml",
    period: int = 900,
    requested_length: float = 100.0,
) -> None:
    root = ET.Element("additional")
    for mapping in sorted(mappings, key=lambda item: item.detector_id):
        if mapping.mapping_status != "active":
            continue
        length = max(1.0, min(requested_length, mapping.lane_position))
        position = max(0.0, mapping.lane_position - length)
        ET.SubElement(
            root,
            "laneAreaDetector",
            id=f"queue_{mapping.detector_id}",
            lane=mapping.sumo_lane,
            pos=f"{position:.3f}",
            length=f"{length:.3f}",
            period=str(period),
            file=output_file,
            timeThreshold="1",
            speedThreshold="1.3889",
            jamThreshold="10",
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    tree = ET.ElementTree(root)
    ET.indent(tree, space="    ")
    tree.write(path, encoding="utf-8", xml_declaration=True)


def write_virtual_e2_additional(
    path: Path,
    virtual_detectors: Sequence[VirtualDetectorGroup | Detector],
    *,
    output_file: str = "e2_15min.xml",
    period: int = 900,
    requested_length: float = 100.0,
) -> None:
    """Write one queue detector per aggregated ``(node_id, SUMO lane)`` group.

    Unlike :func:`write_e2_additional`, this consumes already-collapsed groups or their concrete
    :class:`Detector` values.  It fails closed if two physical-node groups still reuse the same SUMO
    lane: silently merging those queue measurements would violate node isolation, while emitting two
    lane-area detectors would reintroduce the duplicate-sensor bug.
    """

    if period <= 0:
        raise ValueError("period must be positive")
    if not math.isfinite(requested_length) or requested_length <= 0:
        raise ValueError("requested_length must be finite and positive")
    root = ET.Element("additional")
    seen_ids: set[str] = set()
    # Multiple official detector fields may lie on one long corridor lane at
    # different control nodes.  Keep them separate when their queue areas do
    # not overlap; an overlapping pair is still ambiguous and remains a hard
    # failure rather than being silently merged.
    seen_lanes: dict[str, list[tuple[float, float, str]]] = {}
    detectors = [
        item.as_detector() if isinstance(item, VirtualDetectorGroup) else item
        for item in virtual_detectors
    ]
    for detector in sorted(detectors, key=lambda item: item.detector_id):
        if detector.mapping_status != "active":
            raise ValueError(f"virtual detector {detector.detector_id!r} is not active")
        try:
            detector_period = float(detector.period)
        except ValueError as exc:
            raise ValueError(f"virtual detector {detector.detector_id!r} has an invalid period") from exc
        if not math.isfinite(detector_period) or detector_period != period:
            raise ValueError(
                f"virtual detector {detector.detector_id!r} period {detector.period} does not match {period}"
            )
        if not math.isfinite(detector.lane_position) or detector.lane_position < 0:
            raise ValueError(f"virtual detector {detector.detector_id!r} has invalid lane_position")
        detector_id = safe_id(f"queue_{detector.detector_id}")
        if detector_id in seen_ids:
            raise ValueError(f"duplicate virtual E2 detector id {detector_id!r}")
        seen_ids.add(detector_id)
        length = max(1.0, min(requested_length, detector.lane_position))
        position = max(0.0, detector.lane_position - length)
        end_position = position + length
        previous_intervals = seen_lanes.setdefault(detector.lane_id, [])
        for previous_start, previous_end, previous_detector in previous_intervals:
            if min(end_position, previous_end) - max(position, previous_start) > 1e-6:
                raise ValueError(
                    f"SUMO lane {detector.lane_id!r} belongs to multiple virtual detectors "
                    f"{previous_detector!r} and {detector.detector_id!r}; "
                    "split the network or review mappings"
                )
        previous_intervals.append((position, end_position, detector.detector_id))
        ET.SubElement(
            root,
            "laneAreaDetector",
            id=detector_id,
            lane=detector.lane_id,
            pos=f"{position:.3f}",
            length=f"{length:.3f}",
            period=str(period),
            file=output_file,
            timeThreshold="1",
            speedThreshold="1.3889",
            jamThreshold="10",
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    tree = ET.ElementTree(root)
    ET.indent(tree, space="    ")
    tree.write(path, encoding="utf-8", xml_declaration=True)


def build_local_lane_graph(
    net_file: Path,
    *,
    connection_evidence: Sequence[LaneConnectionEvidence] = (),
) -> tuple[dict[str, tuple[LanePathArc, ...]], frozenset[str]]:
    """Read the external SUMO lane graph used by local movement matching.

    Extra connections are explicit, auditable *connectivity* evidence.  They
    never inherit or declare a TLS controller/link index, even when their
    endpoints are near an existing controlled connection.
    """

    root = ET.parse(net_file).getroot()
    lane_lengths: dict[str, float] = {}
    for edge in root.findall("edge"):
        if edge.attrib.get("function") or edge.attrib.get("id", "").startswith(":"):
            continue
        for lane in edge.findall("lane"):
            lane_id = lane.attrib.get("id", "")
            if not lane_id:
                raise ValueError("external SUMO edge contains a lane without an id")
            try:
                lane_length = float(lane.attrib.get("length", "0"))
            except ValueError as exc:
                raise ValueError(f"SUMO lane {lane_id!r} has an invalid length") from exc
            if not math.isfinite(lane_length) or lane_length < 0:
                raise ValueError(f"SUMO lane {lane_id!r} has an invalid length")
            if lane_id in lane_lengths:
                raise ValueError(f"duplicate external SUMO lane id {lane_id!r}")
            lane_lengths[lane_id] = lane_length

    evidence_by_arc: dict[tuple[str, str], LaneConnectionEvidence] = {}
    evidence_ids: set[str] = set()
    for evidence in connection_evidence:
        if not evidence.from_lane or not evidence.to_lane or not evidence.evidence_id:
            raise ValueError("lane connection evidence requires two lane ids and an evidence id")
        if evidence.evidence_id in evidence_ids:
            raise ValueError(f"duplicate lane connection evidence id {evidence.evidence_id!r}")
        evidence_ids.add(evidence.evidence_id)
        if evidence.from_lane == evidence.to_lane:
            raise ValueError(
                f"lane connection evidence {evidence.evidence_id!r} is a self-loop"
            )
        missing_lanes = sorted(
            {evidence.from_lane, evidence.to_lane} - set(lane_lengths)
        )
        if missing_lanes:
            raise ValueError(
                f"lane connection evidence {evidence.evidence_id!r} references missing lanes: "
                f"{missing_lanes}"
            )
        key = (evidence.from_lane, evidence.to_lane)
        if key in evidence_by_arc:
            raise ValueError(
                f"multiple lane connection evidence rows declare {key[0]}->{key[1]}"
            )
        evidence_by_arc[key] = evidence

    graph: dict[str, list[LanePathArc]] = {}
    source_arcs: set[tuple[str, str]] = set()
    for connection in root.findall("connection"):
        from_edge = connection.attrib.get("from", "")
        to_edge = connection.attrib.get("to", "")
        if not from_edge or not to_edge or from_edge.startswith(":") or to_edge.startswith(":"):
            continue
        from_lane = f"{from_edge}_{connection.attrib.get('fromLane', '')}"
        to_lane = f"{to_edge}_{connection.attrib.get('toLane', '')}"
        key = (from_lane, to_lane)
        if from_lane not in lane_lengths or to_lane not in lane_lengths:
            raise ValueError(
                f"SUMO connection {from_lane}->{to_lane} references a missing external lane"
            )
        if key in source_arcs:
            raise ValueError(f"duplicate SUMO lane connection {from_lane}->{to_lane}")
        source_arcs.add(key)
        tls_id = connection.attrib.get("tl", "")
        raw_link_index = connection.attrib.get("linkIndex")
        try:
            link_index = (
                int(raw_link_index)
                if tls_id and raw_link_index not in (None, "")
                else None
            )
        except ValueError as exc:
            raise ValueError(
                f"SUMO connection {from_lane}->{to_lane} has an invalid linkIndex"
            ) from exc
        evidence = evidence_by_arc.get(key)
        graph.setdefault(from_lane, []).append(
            LanePathArc(
                from_lane=from_lane,
                to_lane=to_lane,
                tls_id=tls_id,
                link_index=link_index,
                to_lane_length_m=lane_lengths[to_lane],
                evidence_id=evidence.evidence_id if evidence else "",
            )
        )

    for key, evidence in sorted(evidence_by_arc.items()):
        if key in source_arcs:
            continue
        graph.setdefault(evidence.from_lane, []).append(
            LanePathArc(
                from_lane=evidence.from_lane,
                to_lane=evidence.to_lane,
                tls_id="",
                link_index=None,
                to_lane_length_m=lane_lengths[evidence.to_lane],
                evidence_id=evidence.evidence_id,
            )
        )
    frozen_graph = {
        lane_id: tuple(
            sorted(
                arcs,
                key=lambda arc: (
                    arc.to_lane,
                    arc.tls_id,
                    arc.link_index if arc.link_index is not None else -1,
                    arc.evidence_id,
                ),
            )
        )
        for lane_id, arcs in graph.items()
    }
    return frozen_graph, frozenset(lane_lengths)


def bind_signal_streams_to_tls(
    net_file: Path,
    signal_streams: Sequence[SignalStream],
    lane_bindings: Sequence[MapLaneBinding],
    *,
    max_path_hops: int = 6,
    max_path_span_m: float = 150.0,
    max_candidate_paths: int = 64,
    official_link_indices: Mapping[tuple[str, str], Sequence[int]] | None = None,
) -> list[TlsBinding]:
    if max_path_hops <= 0 or max_candidate_paths <= 0:
        raise ValueError("TLS path search limits must be positive")
    if not math.isfinite(max_path_span_m) or max_path_span_m < 0:
        raise ValueError("max_path_span_m must be finite and non-negative")
    lane_index: dict[tuple[str, str], list[MapLaneBinding]] = {}
    for binding in lane_bindings:
        lane_index.setdefault((_normalize_node(binding.node_id), binding.map_lane_id), []).append(binding)
    graph, _lane_ids = build_local_lane_graph(net_file)

    bindings: list[TlsBinding] = []
    for stream in signal_streams:
        if stream.layer_name != "primary_signal":
            continue
        node = _normalize_node(stream.node_id)
        ingress_candidates = lane_index.get((node, stream.ingress_lane_id), [])
        egress_candidates = lane_index.get((node, stream.egress_lane_id), [])
        if not ingress_candidates or not egress_candidates:
            bindings.append(_unmapped_tls(stream, "official MAP ingress/egress lane is not mapped"))
            continue
        if len(ingress_candidates) != 1 or len(egress_candidates) != 1:
            bindings.append(
                _review_tls(
                    stream,
                    "official MAP ingress/egress lane has duplicate SUMO bindings",
                )
            )
            continue
        ingress = ingress_candidates[0]
        egress = egress_candidates[0]
        if (
            ingress.mapping_status != "active"
            or egress.mapping_status != "active"
            or not ingress.sumo_lane
            or not egress.sumo_lane
        ):
            bindings.append(
                _review_tls(
                    stream,
                    "official MAP ingress/egress SUMO binding is not active",
                    from_lane=ingress.sumo_lane,
                    to_lane=egress.sumo_lane,
                )
            )
            continue
        paths, overflow = find_local_lane_paths(
            graph,
            ingress.sumo_lane,
            egress.sumo_lane,
            max_hops=max_path_hops,
            max_span_m=max_path_span_m,
            max_paths=max_candidate_paths,
        )
        if overflow:
            bindings.append(
                _review_tls(
                    stream,
                    f"local lane path search exceeded {max_candidate_paths} candidates",
                    from_lane=ingress.sumo_lane,
                    to_lane=egress.sumo_lane,
                )
            )
            continue
        if not paths:
            bindings.append(
                _unmapped_tls(
                    stream,
                    (
                        f"no local SUMO lane path for {ingress.sumo_lane}->{egress.sumo_lane} "
                        f"within {max_path_hops} hops/{max_path_span_m:g}m"
                    ),
                    from_lane=ingress.sumo_lane,
                    to_lane=egress.sumo_lane,
                )
            )
            continue
        controlled_by_path = [
            [arc for arc in path if arc.tls_id and arc.link_index is not None] for path in paths
        ]
        controlled_keys_by_path = [
            {(arc.tls_id, arc.link_index) for arc in controlled}
            for controlled in controlled_by_path
        ]
        official_indices = {
            int(index)
            for index in (official_link_indices or {}).get((node, stream.connection_id), ())
        }
        if official_indices and any(len(keys) != 1 for keys in controlled_keys_by_path):
            # Joining a complex Hamburg intersection can make one official MAP
            # movement traverse several controlled links in the same shared
            # controller.  The official MAP/OCIT endpoint contract identifies
            # the link that represents this movement; use that evidence to
            # select the single matching arc instead of rejecting the path as
            # ambiguous.  The evidence is only accepted when every candidate
            # path resolves to the same (TLS, linkIndex) pair.
            endpoint_controlled: list[list[LanePathArc]] = []
            endpoint_keys: list[set[tuple[str, int]]] = []
            for controlled in controlled_by_path:
                selected = [
                    arc
                    for arc in controlled
                    if arc.link_index is not None and int(arc.link_index) in official_indices
                ]
                endpoint_controlled.append(selected)
                endpoint_keys.append(
                    {(arc.tls_id, int(arc.link_index)) for arc in selected if arc.tls_id}
                )
            if endpoint_keys and all(len(keys) == 1 for keys in endpoint_keys):
                endpoint_resolved_keys = {next(iter(keys)) for keys in endpoint_keys}
                if len(endpoint_resolved_keys) == 1:
                    controlled_by_path = endpoint_controlled
                    controlled_keys_by_path = endpoint_keys
        if any(len(keys) != 1 for keys in controlled_keys_by_path):
            counts = sorted({len(keys) for keys in controlled_keys_by_path})
            bindings.append(
                _review_tls(
                    stream,
                    f"local SUMO lane paths have non-unique controlled-link counts {counts}",
                    from_lane=ingress.sumo_lane,
                    to_lane=egress.sumo_lane,
                )
            )
            continue
        controlled_keys = {next(iter(keys)) for keys in controlled_keys_by_path}
        if len(controlled_keys) != 1:
            bindings.append(
                _review_tls(
                    stream,
                    f"{len(paths)} local SUMO lane paths resolve to different controlled links",
                    from_lane=ingress.sumo_lane,
                    to_lane=egress.sumo_lane,
                )
            )
            continue
        tls_id, link_index = next(iter(controlled_keys))
        controlled_arcs = {
            (arc.from_lane, arc.to_lane)
            for controlled in controlled_by_path
            for arc in controlled
            if (arc.tls_id, arc.link_index) == (tls_id, link_index)
        }
        controlled_from, controlled_to = min(controlled_arcs)
        direct = all(len(path) == 1 for path in paths)
        confidence = (
            "high"
            if direct and ingress.mapping_confidence == egress.mapping_confidence == "high"
            else "medium"
        )
        max_hops_used = max(len(path) for path in paths)
        bindings.append(
            TlsBinding(
                stream_id=stream.stream_id,
                node_id=stream.node_id,
                connection_id=stream.connection_id,
                signal_group=stream.signal_group,
                official_ingress_lane=stream.ingress_lane_id,
                official_egress_lane=stream.egress_lane_id,
                sumo_from_lane=controlled_from,
                sumo_to_lane=controlled_to,
                sumo_tls_id=tls_id,
                sumo_link_index=link_index,
                mapping_confidence=confidence,
                mapping_status="active",
                mapping_reason=(
                    "official TLD connection -> official MAP lanes -> unique local SUMO controlled link; "
                    f"candidate_paths={len(paths)}, max_hops={max_hops_used}, "
                    f"collapsed_controlled_arcs={len(controlled_arcs)}"
                ),
            )
        )
    return _deduplicate_tls_link_bindings(bindings)


def find_local_lane_paths(
    graph: Mapping[str, Sequence[LanePathArc]],
    start_lane: str,
    target_lane: str,
    *,
    max_hops: int,
    max_span_m: float,
    max_paths: int,
) -> tuple[list[tuple[LanePathArc, ...]], bool]:
    """Enumerate simple bounded lane paths using Torii's local BFS policy."""

    if max_hops <= 0 or max_paths <= 0:
        raise ValueError("lane path search hop and candidate limits must be positive")
    if not math.isfinite(max_span_m) or max_span_m < 0:
        raise ValueError("lane path search span must be finite and non-negative")
    queue: list[tuple[str, tuple[LanePathArc, ...], frozenset[str], float]] = [
        (start_lane, (), frozenset({start_lane}), 0.0)
    ]
    paths: list[tuple[LanePathArc, ...]] = []
    cursor = 0
    while cursor < len(queue):
        lane_id, path, seen, span_m = queue[cursor]
        cursor += 1
        if lane_id == target_lane and path:
            paths.append(path)
            if len(paths) > max_paths:
                return paths[:max_paths], True
            continue
        if len(path) >= max_hops:
            continue
        for arc in graph.get(lane_id, ()):
            if arc.to_lane in seen:
                continue
            next_span = span_m + (0.0 if arc.to_lane == target_lane else arc.to_lane_length_m)
            if next_span > max_span_m:
                continue
            queue.append((arc.to_lane, (*path, arc), seen | {arc.to_lane}, next_span))
    return paths, False


# Private compatibility alias for callers/tests written before the path primitive
# became part of the reusable Hamburg teacher-cell adapter.
_find_local_lane_paths = find_local_lane_paths


def select_active_signal_streams(
    signal_streams: Sequence[SignalStream],
    bindings: Sequence[TlsBinding],
) -> list[SignalStream]:
    """Return the one official primary-signal stream selected for each SUMO controlled link."""
    stream_by_id = {stream.stream_id: stream for stream in signal_streams}
    active_ids = {binding.stream_id for binding in bindings if binding.mapping_status == "active"}
    missing = sorted(active_ids - set(stream_by_id))
    if missing:
        raise ValueError(f"active TLS bindings reference unknown signal streams: {missing}")
    return sorted((stream_by_id[stream_id] for stream_id in active_ids), key=lambda item: item.stream_id)


def _deduplicate_tls_link_bindings(bindings: Sequence[TlsBinding]) -> list[TlsBinding]:
    grouped: dict[tuple[str, int], list[TlsBinding]] = {}
    untouched: list[TlsBinding] = []
    for binding in bindings:
        if binding.mapping_status != "active" or binding.sumo_link_index is None or not binding.sumo_tls_id:
            untouched.append(binding)
            continue
        grouped.setdefault((binding.sumo_tls_id, binding.sumo_link_index), []).append(binding)

    resolved: list[TlsBinding] = list(untouched)
    for (tls_id, link_index), candidates in sorted(grouped.items()):
        ordered = sorted(candidates, key=lambda item: (item.stream_id, item.connection_id))
        if len(ordered) == 1:
            resolved.extend(ordered)
            continue
        signal_groups = {item.signal_group.strip() for item in ordered}
        if len(signal_groups) == 1 and "" not in signal_groups:
            representative = ordered[0]
            resolved.append(
                replace(
                    representative,
                    mapping_reason=(
                        f"{representative.mapping_reason}; canonical representative for "
                        f"{len(ordered)} official streams collapsed onto {tls_id}[{link_index}]"
                    ),
                )
            )
            for duplicate in ordered[1:]:
                resolved.append(
                    replace(
                        duplicate,
                        mapping_status="redundant",
                        mapping_reason=(
                            f"same official signal group as representative stream {representative.stream_id} "
                            f"on collapsed SUMO link {tls_id}[{link_index}]"
                        ),
                    )
                )
            continue
        group_text = ", ".join(sorted(group or "<blank>" for group in signal_groups))
        for candidate in ordered:
            resolved.append(
                replace(
                    candidate,
                    mapping_confidence="none",
                    mapping_status="needs_review",
                    mapping_reason=(
                        f"conflicting official signal groups ({group_text}) collapse onto SUMO link "
                        f"{tls_id}[{link_index}]"
                    ),
                )
            )
    return sorted(
        resolved,
        key=lambda item: (
            _normalize_node(item.node_id),
            item.stream_id,
            item.connection_id,
            item.sumo_tls_id,
            item.sumo_link_index if item.sumo_link_index is not None else -1,
            item.sumo_from_lane,
            item.sumo_to_lane,
        ),
    )


def write_tls_bindings(path: Path, bindings: Iterable[TlsBinding]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = list(TlsBinding.__dataclass_fields__)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for binding in bindings:
            writer.writerow(binding.__dict__)


def write_tls_link_events(
    path: Path,
    streams: Sequence[SignalStream],
    observations: Mapping[int, Sequence[SignalObservation]],
    bindings: Sequence[TlsBinding],
    *,
    begin_utc: datetime,
    end_utc: datetime,
) -> dict[str, int]:
    stream_index = {stream.stream_id: stream for stream in streams}
    bindings_by_stream: dict[int, list[TlsBinding]] = {}
    for binding in bindings:
        if binding.mapping_status != "active":
            continue
        bindings_by_stream.setdefault(binding.stream_id, []).append(binding)
    for stream_bindings in bindings_by_stream.values():
        stream_bindings.sort(
            key=lambda item: (
                item.sumo_tls_id,
                item.sumo_link_index if item.sumo_link_index is not None else -1,
                item.sumo_from_lane,
                item.sumo_to_lane,
            )
        )
    event_rows: dict[tuple[float, str, int], dict[str, object]] = {}
    event_order: dict[tuple[float, str, int], tuple[datetime, str, int]] = {}
    collapsed_event_count = 0
    conflict_event_count = 0
    initialized: set[tuple[int, str, int]] = set()
    for stream_id, stream_bindings in sorted(bindings_by_stream.items()):
        candidates = sorted(
            observations.get(stream_id, ()),
            key=lambda item: (item.phenomenon_time_utc, item.result_time, item.observation_id or -1),
        )
        preceding = [item for item in candidates if item.phenomenon_time_utc <= begin_utc]
        in_window = [item for item in candidates if begin_utc < item.phenomenon_time_utc < end_utc]
        selected = ([preceding[-1]] if preceding else []) + in_window
        if preceding:
            initialized.update(
                (stream_id, binding.sumo_tls_id, int(binding.sumo_link_index))
                for binding in stream_bindings
                if binding.sumo_link_index is not None
            )
        for index, observation in enumerate(selected):
            simulation_time = 0.0 if index == 0 and preceding else (
                observation.phenomenon_time_utc - begin_utc
            ).total_seconds()
            for binding in stream_bindings:
                row = {
                    "simulation_time": f"{simulation_time:.3f}",
                    "phenomenon_time_utc": observation.phenomenon_time_utc.isoformat().replace("+00:00", "Z"),
                    "node_id": binding.node_id,
                    "stream_id": stream_id,
                    "connection_id": binding.connection_id,
                    "signal_group": binding.signal_group,
                    "sumo_tls_id": binding.sumo_tls_id,
                    "sumo_link_index": binding.sumo_link_index,
                    "source_state": observation.result,
                    "sumo_state": map_signal_state_to_sumo(observation.result),
                    "conversion_exact": (
                        "true" if observation.result in {"0", "1", "2", "3", "4"} else "false"
                    ),
                    "source_layer": stream_index[stream_id].layer_name,
                }
                key = (simulation_time, binding.sumo_tls_id, int(binding.sumo_link_index))
                order = (observation.phenomenon_time_utc, observation.result_time, observation.observation_id or -1)
                previous = event_rows.get(key)
                if previous is not None:
                    collapsed_event_count += 1
                    if previous["sumo_state"] != row["sumo_state"]:
                        conflict_event_count += 1
                    if order <= event_order[key]:
                        continue
                event_rows[key] = row
                event_order[key] = order
    rows = list(event_rows.values())
    rows.sort(key=lambda row: (float(row["simulation_time"]), str(row["sumo_tls_id"]), int(row["sumo_link_index"])))
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "simulation_time",
        "phenomenon_time_utc",
        "node_id",
        "stream_id",
        "connection_id",
        "signal_group",
        "sumo_tls_id",
        "sumo_link_index",
        "source_state",
        "sumo_state",
        "conversion_exact",
        "source_layer",
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    return {
        "active_binding_count": sum(len(items) for items in bindings_by_stream.values()),
        "initialized_binding_count": len(initialized),
        "event_count": len(rows),
        "collapsed_event_count": collapsed_event_count,
        "conflict_event_count": conflict_event_count,
    }


def project_point_to_polyline(
    point: tuple[float, float],
    shape: Sequence[tuple[float, float]],
) -> tuple[float, float, float]:
    if len(shape) < 2:
        return 0.0, math.inf, 0.0
    best_distance = math.inf
    best_position = 0.0
    best_heading = 0.0
    cumulative = 0.0
    px, py = point
    for start, end in zip(shape, shape[1:]):
        dx = end[0] - start[0]
        dy = end[1] - start[1]
        length_sq = dx * dx + dy * dy
        length = math.sqrt(length_sq)
        if length <= 1e-12:
            continue
        fraction = max(0.0, min(1.0, ((px - start[0]) * dx + (py - start[1]) * dy) / length_sq))
        projected = (start[0] + fraction * dx, start[1] + fraction * dy)
        distance = math.hypot(px - projected[0], py - projected[1])
        if distance < best_distance:
            best_distance = distance
            best_position = cumulative + fraction * length
            best_heading = math.degrees(math.atan2(dy, dx)) % 360.0
        cumulative += length
    return best_position, best_distance, best_heading


def _nearest_map_lane(stream: CountStream, lanes: Sequence[MapLane]) -> tuple[MapLane | None, float | None]:
    best: tuple[float, MapLane] | None = None
    for lane in lanes:
        cos_lat = math.cos(math.radians(lane.ref_latitude))
        x = (stream.longitude - lane.ref_longitude) * 111_320.0 * cos_lat
        y = (stream.latitude - lane.ref_latitude) * 110_540.0
        _position, distance, _heading = project_point_to_polyline((x, y), lane.points_m)
        if best is None or distance < best[0]:
            best = (distance, lane)
    return (None, None) if best is None else (best[1], best[0])


def _convert_network_lonlat2xy(
    net: object,
    longitude: float,
    latitude: float,
    *,
    network_projection: str | None = None,
) -> tuple[float, float]:
    """Convert WGS84 coordinates even when a plain network omitted projection metadata.

    ``netconvert`` can legitimately emit ``projParameter=\"!\"`` when its
    input coordinates are already metric.  SUMO then cannot perform the
    inverse WGS84 conversion used by detector and MAP binding.  The caller
    must provide the authoritative CRS in that case; no CRS is guessed here.
    """

    try:
        converted = net.convertLonLat2XY(longitude, latitude)  # type: ignore[attr-defined]
        return tuple(float(value) for value in converted)
    except (AttributeError, ModuleNotFoundError, RuntimeError):
        if not network_projection or not str(network_projection).strip():
            raise
        try:
            transformer = Transformer.from_crs(
                "EPSG:4326",
                str(network_projection).strip(),
                always_xy=True,
            )
            x, y = transformer.transform(longitude, latitude)
            offset = net.getLocationOffset()  # type: ignore[attr-defined]
            return float(x) + float(offset[0]), float(y) + float(offset[1])
        except Exception as projection_error:  # noqa: BLE001 - pyproj exposes several CRS-specific errors.
            raise ValueError(
                f"cannot convert WGS84 coordinates using network projection {network_projection!r}"
            ) from projection_error


def _map_point_to_network(
    net: object,
    lane: MapLane,
    point: tuple[float, float],
    *,
    network_projection: str | None = None,
) -> tuple[float, float]:
    cos_lat = math.cos(math.radians(lane.ref_latitude))
    longitude = lane.ref_longitude + point[0] / (111_320.0 * cos_lat)
    latitude = lane.ref_latitude + point[1] / 110_540.0
    return _convert_network_lonlat2xy(
        net,
        longitude,
        latitude,
        network_projection=network_projection,
    )


def _confidence(distance: float, heading_error: float | None) -> str:
    if distance <= 8.0 and (heading_error is None or heading_error <= 30.0):
        return "high"
    if distance <= 20.0 and (heading_error is None or heading_error <= 60.0):
        return "medium"
    return "low"


def _safe_lane_position(position: float, length: float) -> float:
    if length <= 0.2:
        return max(0.0, min(position, length))
    return max(0.1, min(position, length - 0.1))


def _normalize_node(value: str) -> str:
    try:
        return str(int(value))
    except ValueError:
        return value.strip().lower()


def _format_optional(value: float | None) -> str:
    if value is None or not math.isfinite(value):
        return ""
    return f"{value:.3f}"


def _virtual_detector_id(node_id: str, sumo_lane: str) -> str:
    identity = f"{node_id}\x1f{sumo_lane}"
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:10]
    return safe_id(f"vdet_{node_id}_{sumo_lane}_{digest}")


def _validate_active_mapping(mapping: DetectorMapping, *, bin_seconds: int) -> None:
    if not mapping.node_id.strip():
        raise ValueError(f"active count stream {mapping.stream_id} has a blank node_id")
    if not mapping.detector_id.strip():
        raise ValueError(f"active count stream {mapping.stream_id} has a blank detector_id")
    if not mapping.sumo_edge.strip() or not mapping.sumo_lane.strip():
        raise ValueError(f"active count stream {mapping.stream_id} has an incomplete SUMO lane mapping")
    if not math.isfinite(mapping.lane_position) or mapping.lane_position < 0:
        raise ValueError(f"active count stream {mapping.stream_id} has an invalid lane_position")
    if mapping.period != bin_seconds:
        raise ValueError(
            f"active count stream {mapping.stream_id} period {mapping.period} does not match bin_seconds {bin_seconds}"
        )


def _validate_canonical_virtual_row(
    row: CanonicalCount,
    mapping: DetectorMapping,
    *,
    bin_seconds: int,
) -> None:
    if row.quality_status != "complete":
        raise ValueError(
            f"count row {row.detector_id} {row.begin}-{row.end} has quality_status={row.quality_status!r}"
        )
    if row.source_observation_count != row.expected_source_observation_count:
        raise ValueError(
            f"count row {row.detector_id} {row.begin}-{row.end} has incomplete source observations "
            f"({row.source_observation_count}/{row.expected_source_observation_count})"
        )
    if row.expected_source_observation_count <= 0:
        raise ValueError(f"count row {row.detector_id} {row.begin}-{row.end} has no expected source observations")
    if row.detector_id != mapping.detector_id:
        raise ValueError(
            f"count stream {row.stream_id} detector id {row.detector_id!r} does not match mapping "
            f"{mapping.detector_id!r}"
        )
    if _normalize_node(row.node_id) != _normalize_node(mapping.node_id):
        raise ValueError(
            f"count stream {row.stream_id} node {row.node_id!r} does not match mapping {mapping.node_id!r}"
        )
    if row.begin < 0 or row.end - row.begin != bin_seconds or row.begin % bin_seconds:
        raise ValueError(
            f"count row {row.detector_id} interval {row.begin}-{row.end} is not an aligned "
            f"{bin_seconds}-second bin"
        )
    if not isinstance(row.count, int) or isinstance(row.count, bool) or row.count < 0:
        raise ValueError(f"count row {row.detector_id} {row.begin}-{row.end} has an invalid count")


def _weakest_confidence(confidences: Sequence[str]) -> str:
    if not confidences:
        return "none"
    rank = {"none": 0, "low": 1, "medium": 2, "high": 3}
    return min(confidences, key=lambda value: rank.get(value.lower(), 0))


def _json_tuple(values: Sequence[object]) -> str:
    return json.dumps(list(values), ensure_ascii=False, separators=(",", ":"))


def _unmapped_detector(stream: CountStream, period: int, reason: str) -> DetectorMapping:
    return DetectorMapping(
        detector_id=stream.detector_id,
        stream_id=stream.stream_id,
        node_id=stream.node_id,
        asset_id=stream.asset_id,
        real_direction=stream.direction,
        lane_use=stream.lane_use,
        longitude=stream.longitude,
        latitude=stream.latitude,
        official_map_lane="",
        official_map_distance_m=None,
        sumo_edge="",
        sumo_lane="",
        lane_position=0.0,
        distance_m=None,
        heading_error_deg=None,
        period=period,
        mapping_confidence="none",
        mapping_status="unmapped",
        mapping_reason=reason,
    )


def _unmapped_tls(
    stream: SignalStream,
    reason: str,
    *,
    from_lane: str = "",
    to_lane: str = "",
) -> TlsBinding:
    return TlsBinding(
        stream_id=stream.stream_id,
        node_id=stream.node_id,
        connection_id=stream.connection_id,
        signal_group=stream.signal_group,
        official_ingress_lane=stream.ingress_lane_id,
        official_egress_lane=stream.egress_lane_id,
        sumo_from_lane=from_lane,
        sumo_to_lane=to_lane,
        sumo_tls_id="",
        sumo_link_index=None,
        mapping_confidence="none",
        mapping_status="unmapped",
        mapping_reason=reason,
    )


def _review_tls(
    stream: SignalStream,
    reason: str,
    *,
    from_lane: str = "",
    to_lane: str = "",
) -> TlsBinding:
    return replace(
        _unmapped_tls(stream, reason, from_lane=from_lane, to_lane=to_lane),
        mapping_status="needs_review",
    )
