from __future__ import annotations

import math
from pathlib import Path
from xml.etree import ElementTree as ET

import sumolib

from torii_sumo.core.candidate_contracts import file_sha256

from .enums import GateStatus, TrafficSide
from .ids import stable_id
from .pedestrian_row_contracts import (
    ModelROWClaimClass,
    ROWGeometryEvidence,
    ROWModelClaimEvidence,
    ROWRequestRelation,
    ROWStaticAssessment,
    SourceROWBundle,
    SourceROWObservation,
    SourceROWOracleDecision,
)


_TRAFFIC_LIGHT_NODE_TYPES = frozenset(
    {
        "traffic_light",
        "traffic_light_unregulated",
        "traffic_light_right_on_red",
    }
)
_TRUE = frozenset({"1", "true", "yes", "on"})
_FALSE = frozenset({"0", "false", "no", "off"})


def parse_plain_source_row_bundle(
    *,
    nodes_file: Path,
    connections_file: Path,
    crossing_node_id: str,
    crossing_edge_ids: tuple[str, ...],
    traffic_side: TrafficSide,
    crossing_stage_count: int = 1,
) -> SourceROWBundle:
    node_path = nodes_file.resolve(strict=True)
    connection_path = connections_file.resolve(strict=True)
    normalized_edges = tuple(sorted(set(crossing_edge_ids)))
    node_root = ET.parse(node_path).getroot()
    connection_root = ET.parse(connection_path).getroot()
    node = next(
        (
            element
            for element in node_root.findall("node")
            if element.attrib.get("id") == crossing_node_id
        ),
        None,
    )
    node_type = node.attrib.get("type", "") if node is not None else ""
    matching_crossings = [
        element
        for element in connection_root.findall("crossing")
        if element.attrib.get("node") == crossing_node_id
        and tuple(
            sorted(set(element.attrib.get("edges", "").split()))
        )
        == normalized_edges
    ]
    priority_values = {
        _optional_bool(element.attrib.get("priority"))
        for element in matching_crossings
    }
    priority_values.discard(None)
    explicit_priority = (
        next(iter(priority_values)) if len(priority_values) == 1 else None
    )
    if node_type in _TRAFFIC_LIGHT_NODE_TYPES:
        control_kind = "traffic-light"
    elif node is None or not node_type:
        control_kind = "unknown"
    else:
        control_kind = "unsignalized"
    observations = [
        make_source_row_observation(
            source_kind="plain-node-control",
            source_sha256=file_sha256(node_path),
            subject=f"node/{crossing_node_id}",
            observed_value=node_type or "missing",
            expected_answer_eligible=True,
        ),
        make_source_row_observation(
            source_kind="plain-crossing-priority",
            source_sha256=file_sha256(connection_path),
            subject=(
                f"crossing/{crossing_node_id}/"
                + "|".join(normalized_edges)
            ),
            observed_value=(
                "missing"
                if not matching_crossings
                else "unspecified"
                if not priority_values
                else "contradictory"
                if len(priority_values) > 1
                else "true"
                if explicit_priority
                else "false"
            ),
            expected_answer_eligible=True,
        ),
    ]
    contradictory = len(priority_values) > 1 or (
        control_kind == "traffic-light" and explicit_priority is False
    )
    if contradictory:
        source_status = "contradictory"
    elif control_kind == "traffic-light":
        source_status = "complete"
    elif control_kind == "unsignalized" and explicit_priority is not None:
        source_status = "complete"
    else:
        source_status = "incomplete"
    return make_source_row_bundle(
        crossing_node_id=crossing_node_id,
        crossing_edge_ids=normalized_edges,
        traffic_side=traffic_side,
        crossing_stage_count=crossing_stage_count,
        junction_control_kind=control_kind,
        explicit_crossing_priority=explicit_priority,
        source_status=source_status,
        observations=tuple(observations),
    )


def make_source_row_bundle(
    *,
    crossing_node_id: str,
    crossing_edge_ids: tuple[str, ...],
    traffic_side: TrafficSide,
    crossing_stage_count: int,
    junction_control_kind: str,
    explicit_crossing_priority: bool | None,
    source_status: str,
    observations: tuple[SourceROWObservation, ...],
) -> SourceROWBundle:
    normalized_edges = tuple(sorted(set(crossing_edge_ids)))
    crossing_signature = stable_id(
        "signature",
        {
            "crossing_node_id": crossing_node_id,
            "crossing_edge_ids": normalized_edges,
            "traffic_side": traffic_side,
            "crossing_stage_count": crossing_stage_count,
        },
    )
    sorted_observations = tuple(
        sorted(observations, key=lambda item: item.evidence_id)
    )
    identity = {
        "crossing_signature": crossing_signature,
        "crossing_node_id": crossing_node_id,
        "crossing_edge_ids": normalized_edges,
        "traffic_side": traffic_side,
        "crossing_stage_count": crossing_stage_count,
        "junction_control_kind": junction_control_kind,
        "explicit_crossing_priority": explicit_crossing_priority,
        "source_status": source_status,
        "observation_ids": [
            observation.evidence_id for observation in sorted_observations
        ],
    }
    return SourceROWBundle(
        source_bundle_id=stable_id("evidence", identity),
        crossing_signature=crossing_signature,
        crossing_node_id=crossing_node_id,
        crossing_edge_ids=normalized_edges,
        traffic_side=traffic_side,
        crossing_stage_count=crossing_stage_count,
        junction_control_kind=junction_control_kind,
        explicit_crossing_priority=explicit_crossing_priority,
        source_status=source_status,
        observations=sorted_observations,
    )


def infer_source_row_class(
    source_bundle: SourceROWBundle,
) -> SourceROWOracleDecision:
    reasons: list[str] = []
    if source_bundle.source_status == "contradictory":
        expected_class = "unknown-unsignalized"
        reasons.append("source_evidence_contradictory")
    elif source_bundle.junction_control_kind == "traffic-light":
        expected_class = "signalized"
        reasons.append(
            "osm_tag_declares_signalized_crossing"
            if any(
                observation.source_kind == "osm-tag"
                for observation in source_bundle.observations
            )
            else "plain_node_declares_traffic_light"
        )
    elif (
        source_bundle.junction_control_kind
        == "shared-space-or-unsupported"
        or source_bundle.source_status == "unsupported"
    ):
        expected_class = "shared-space-or-unsupported"
        reasons.append("source_form_outside_row1_domain")
    elif source_bundle.junction_control_kind != "unsignalized":
        expected_class = "unknown-unsignalized"
        reasons.append("junction_control_unknown")
    elif source_bundle.explicit_crossing_priority is True:
        expected_class = "priority-unsignalized"
        reasons.append("plain_crossing_priority_true")
    elif source_bundle.explicit_crossing_priority is False:
        expected_class = "unprioritized-unsignalized"
        reasons.append("plain_crossing_priority_false")
    else:
        expected_class = "unknown-unsignalized"
        reasons.append("crossing_priority_missing")
    abstained = expected_class in {
        "unknown-unsignalized",
        "shared-space-or-unsupported",
    }
    identity = {
        "source_bundle_id": source_bundle.source_bundle_id,
        "traffic_side": source_bundle.traffic_side,
        "crossing_stage_count": source_bundle.crossing_stage_count,
        "expected_class": expected_class,
        "status": GateStatus.REVIEW if abstained else GateStatus.PASS,
        "abstained": abstained,
        "reasons": tuple(reasons),
    }
    return SourceROWOracleDecision(
        decision_id=stable_id("evidence", identity),
        source_bundle_id=source_bundle.source_bundle_id,
        traffic_side=source_bundle.traffic_side,
        crossing_stage_count=source_bundle.crossing_stage_count,
        expected_class=expected_class,
        status=identity["status"],
        abstained=abstained,
        reasons=tuple(reasons),
        expected_answer_channels=("source-evidence",),
        model_claim_fields_read=(),
        automatic_promotion_gate=GateStatus.BLOCKED,
    )


def build_row_geometry_evidence(
    net_file: Path,
    *,
    crossing_edge_id: str,
    vehicle_from_edge_id: str,
    vehicle_to_edge_id: str,
) -> ROWGeometryEvidence:
    path = net_file.resolve(strict=True)
    root = ET.parse(path).getroot()
    crossing_shape = _edge_lane_shape(root, crossing_edge_id)
    vehicle_shape = _vehicle_occupancy_shape(
        root,
        from_edge_id=vehicle_from_edge_id,
        to_edge_id=vehicle_to_edge_id,
    )
    distance, intersects, angle = _polyline_relation(
        crossing_shape,
        vehicle_shape,
    )
    payload = {
        "candidate_net_sha256": file_sha256(path),
        "crossing_edge_id": crossing_edge_id,
        "vehicle_from_edge_id": vehicle_from_edge_id,
        "vehicle_to_edge_id": vehicle_to_edge_id,
        "centerline_intersects": intersects,
        "minimum_centerline_distance_m": round(distance, 6),
        "crossing_angle_deg": (
            round(angle, 6) if angle is not None else None
        ),
        "right_of_way_inference": "not-inferred",
        "request_foes_fields_read": (),
    }
    return ROWGeometryEvidence(
        geometry_evidence_id=stable_id("evidence", payload),
        **payload,
    )


def build_row_model_claim_evidence(
    net_file: Path,
    *,
    junction_id: str,
    crossing_edge_id: str,
    vehicle_from_edge_id: str,
    vehicle_to_edge_id: str,
) -> ROWModelClaimEvidence:
    path = net_file.resolve(strict=True)
    network = sumolib.net.readNet(
        str(path),
        withInternal=True,
        withPedestrianConnections=True,
    )
    node = network.getNode(junction_id)
    connections = tuple(node.getConnections())
    pedestrian = [
        connection
        for connection in connections
        if connection.getTo().getID() == crossing_edge_id
        and connection.getJunctionIndex() >= 0
    ]
    vehicles = [
        connection
        for connection in connections
        if connection.getFrom().getID() == vehicle_from_edge_id
        and connection.getTo().getID() == vehicle_to_edge_id
        and connection.getJunctionIndex() >= 0
    ]
    root = ET.parse(path).getroot()
    junction = root.find(f"junction[@id='{junction_id}']")
    rows = (
        {
            int(row.attrib["index"]): row.attrib
            for row in junction.findall("request")
        }
        if junction is not None
        else {}
    )
    relation: ROWRequestRelation | None = None
    limitations: list[str] = []
    controllers = tuple(
        sorted(
            {
                connection.getTLSID()
                for connection in (*pedestrian, *vehicles)
                if connection.getTLSID()
            }
        )
    )
    states = tuple(
        sorted(
            {
                connection.getState()
                for connection in (*pedestrian, *vehicles)
                if connection.getState()
            }
        )
    )
    if controllers:
        inferred: ModelROWClaimClass = "signalized"
    elif len(pedestrian) != 1 or len(vehicles) != 1:
        inferred = "unmapped"
        limitations.append(
            "expected_one_pedestrian_and_one_vehicle_request_mapping"
        )
    else:
        pedestrian_index = pedestrian[0].getJunctionIndex()
        vehicle_index = vehicles[0].getJunctionIndex()
        pedestrian_row = rows.get(pedestrian_index)
        vehicle_row = rows.get(vehicle_index)
        if pedestrian_row is None or vehicle_row is None:
            inferred = "unmapped"
            limitations.append("request_row_missing")
        else:
            relation = ROWRequestRelation(
                pedestrian_request_index=pedestrian_index,
                vehicle_request_index=vehicle_index,
                pedestrian_response_to_vehicle=_request_bit(
                    pedestrian_row.get("response", ""),
                    vehicle_index,
                ),
                vehicle_response_to_pedestrian=_request_bit(
                    vehicle_row.get("response", ""),
                    pedestrian_index,
                ),
                pedestrian_foe_to_vehicle=_request_bit(
                    pedestrian_row.get("foes", ""),
                    vehicle_index,
                ),
                vehicle_foe_to_pedestrian=_request_bit(
                    vehicle_row.get("foes", ""),
                    pedestrian_index,
                ),
                pedestrian_cont=_optional_bool(
                    pedestrian_row.get("cont")
                )
                is True,
                vehicle_cont=_optional_bool(vehicle_row.get("cont"))
                is True,
            )
            if relation.pedestrian_cont or relation.vehicle_cont:
                inferred = "ambiguous"
                limitations.append(
                    "continuation_request_closure_not_supported"
                )
            elif (
                relation.vehicle_response_to_pedestrian
                and not relation.pedestrian_response_to_vehicle
                and relation.vehicle_foe_to_pedestrian
                and relation.pedestrian_foe_to_vehicle
            ):
                inferred = "priority-unsignalized"
            elif (
                relation.pedestrian_response_to_vehicle
                and not relation.vehicle_response_to_pedestrian
                and relation.vehicle_foe_to_pedestrian
                and relation.pedestrian_foe_to_vehicle
            ):
                inferred = "unprioritized-unsignalized"
            else:
                inferred = "ambiguous"
                limitations.append("request_relation_not_directional")
    payload = {
        "candidate_net_sha256": file_sha256(path),
        "junction_id": junction_id,
        "crossing_edge_id": crossing_edge_id,
        "vehicle_from_edge_id": vehicle_from_edge_id,
        "vehicle_to_edge_id": vehicle_to_edge_id,
        "inferred_class": inferred,
        "relation": (
            relation.model_dump(mode="json", by_alias=True)
            if relation is not None
            else None
        ),
        "controller_ids": controllers,
        "source_connection_states": states,
        "bit_order": "rightmost-bit-is-index-zero",
        "ground_truth_authority": False,
        "limitations": tuple(limitations),
    }
    return ROWModelClaimEvidence(
        model_claim_id=stable_id("evidence", payload),
        **payload,
    )


def assess_row_static_consistency(
    source_decision: SourceROWOracleDecision,
    geometry: ROWGeometryEvidence,
    model_claim: ROWModelClaimEvidence,
) -> ROWStaticAssessment:
    contradictions: list[str] = []
    limitations: list[str] = []
    geometry_applicable = geometry.centerline_intersects
    if not geometry_applicable:
        limitations.append("movement_centerlines_do_not_intersect")
    if source_decision.abstained:
        consistent = None
        status = GateStatus.REVIEW
        limitations.append("source_oracle_abstained")
    elif model_claim.inferred_class in {"ambiguous", "unmapped"}:
        consistent = None
        status = GateStatus.REVIEW
        limitations.append("candidate_model_claim_unresolved")
    else:
        consistent = (
            source_decision.expected_class == model_claim.inferred_class
        )
        if not consistent:
            contradictions.append(
                "source_expected_"
                f"{source_decision.expected_class}_but_model_claimed_"
                f"{model_claim.inferred_class}"
            )
        if not consistent:
            status = GateStatus.BLOCKED
        elif not geometry_applicable:
            status = GateStatus.REVIEW
        elif source_decision.crossing_stage_count > 1:
            status = GateStatus.REVIEW
            limitations.append("multi_stage_priority_closure_not_proven")
        elif source_decision.expected_class == "signalized":
            status = GateStatus.REVIEW
            limitations.append("signal_phase_and_g_G_closure_not_proven")
        else:
            status = GateStatus.PASS
    payload = {
        "source_decision_id": source_decision.decision_id,
        "geometry_evidence_id": geometry.geometry_evidence_id,
        "model_claim_id": model_claim.model_claim_id,
        "status": status,
        "source_model_consistent": consistent,
        "geometry_applicable": geometry_applicable,
        "contradictions": tuple(contradictions),
        "limitations": tuple(limitations),
        "expected_answer_source_bundle_only": True,
        "automatic_promotion_gate": GateStatus.BLOCKED,
    }
    return ROWStaticAssessment(
        assessment_id=stable_id("finding", payload),
        **payload,
    )


def make_source_row_observation(
    *,
    source_kind: str,
    source_sha256: str,
    subject: str,
    observed_value: str,
    expected_answer_eligible: bool,
) -> SourceROWObservation:
    """Create one hash-bound source observation for the independent ROW oracle."""

    payload = {
        "source_kind": source_kind,
        "source_sha256": source_sha256,
        "subject": subject,
        "observed_value": observed_value,
        "expected_answer_eligible": expected_answer_eligible,
    }
    return SourceROWObservation(
        evidence_id=stable_id("evidence", payload),
        **payload,
    )


def _optional_bool(value: str | None) -> bool | None:
    if value is None:
        return None
    normalized = value.strip().lower()
    if normalized in _TRUE:
        return True
    if normalized in _FALSE:
        return False
    return None


def _request_bit(bit_string: str, index: int) -> bool:
    if index < 0 or index >= len(bit_string):
        return False
    return bit_string[-1 - index] == "1"


def _edge_lane_shape(root: ET.Element, edge_id: str) -> tuple[tuple[float, float], ...]:
    edge = root.find(f"edge[@id='{edge_id}']")
    if edge is None:
        raise ValueError(f"ROW geometry edge not found: {edge_id}")
    lane = edge.find("lane")
    if lane is None:
        raise ValueError(f"ROW geometry edge has no lane: {edge_id}")
    return _shape(lane.attrib.get("shape", ""))


def _vehicle_occupancy_shape(
    root: ET.Element,
    *,
    from_edge_id: str,
    to_edge_id: str,
) -> tuple[tuple[float, float], ...]:
    direct = next(
        (
            connection
            for connection in root.findall("connection")
            if connection.attrib.get("from") == from_edge_id
            and connection.attrib.get("to") == to_edge_id
            and connection.attrib.get("via")
        ),
        None,
    )
    if direct is None:
        raise ValueError("ROW vehicle direct connection cannot be mapped.")
    lanes: dict[str, tuple[str, tuple[tuple[float, float], ...]]] = {}
    for edge in root.findall("edge"):
        edge_id = edge.attrib.get("id", "")
        for lane in edge.findall("lane"):
            lane_id = lane.attrib.get("id", "")
            if lane_id:
                lanes[lane_id] = (
                    edge_id,
                    _shape(lane.attrib.get("shape", "")),
                )
    from_lane = _edge_lane_by_index(
        root,
        from_edge_id,
        direct.attrib.get("fromLane"),
    )
    to_lane = _edge_lane_by_index(
        root,
        to_edge_id,
        direct.attrib.get("toLane"),
    )
    parts = [from_lane]
    via = direct.attrib["via"]
    visited: set[str] = set()
    while via:
        if via in visited:
            raise ValueError("ROW vehicle internal path contains a cycle.")
        visited.add(via)
        mapped = lanes.get(via)
        if mapped is None:
            raise ValueError(f"ROW vehicle via lane not found: {via}")
        current_edge, shape = mapped
        parts.append(shape)
        continuation = next(
            (
                connection
                for connection in root.findall("connection")
                if connection.attrib.get("from") == current_edge
                and connection.attrib.get("to") == to_edge_id
            ),
            None,
        )
        via = (
            continuation.attrib.get("via", "")
            if continuation is not None
            else ""
        )
        if len(visited) > len(lanes):
            raise ValueError("ROW vehicle internal path exceeds the lane graph.")
    parts.append(to_lane)
    return _join_shapes(parts)


def _edge_lane_by_index(
    root: ET.Element,
    edge_id: str,
    lane_index: str | None,
) -> tuple[tuple[float, float], ...]:
    edge = root.find(f"edge[@id='{edge_id}']")
    if edge is None:
        raise ValueError(f"ROW vehicle external edge not found: {edge_id}")
    lanes = edge.findall("lane")
    lane = next(
        (
            item
            for ordinal, item in enumerate(lanes)
            if item.attrib.get("index", str(ordinal)) == str(lane_index)
        ),
        None,
    )
    if lane is None:
        raise ValueError(
            f"ROW vehicle external lane not found: {edge_id}/{lane_index}"
        )
    return _shape(lane.attrib.get("shape", ""))


def _join_shapes(
    parts: list[tuple[tuple[float, float], ...]],
) -> tuple[tuple[float, float], ...]:
    points: list[tuple[float, float]] = []
    for part in parts:
        if points and part and points[-1] == part[0]:
            points.extend(part[1:])
        else:
            points.extend(part)
    if len(points) < 2:
        raise ValueError("ROW vehicle occupancy path is incomplete.")
    return tuple(points)


def _shape(value: str) -> tuple[tuple[float, float], ...]:
    points = tuple(
        (float(tokens[0]), float(tokens[1]))
        for token in value.split()
        for tokens in (token.split(","),)
        if len(tokens) >= 2
    )
    if len(points) < 2:
        raise ValueError("ROW geometry requires at least two points.")
    return points


def _polyline_relation(
    first: tuple[tuple[float, float], ...],
    second: tuple[tuple[float, float], ...],
) -> tuple[float, bool, float | None]:
    best_distance = math.inf
    best_intersects = False
    best_angle: float | None = None
    for a0, a1 in zip(first, first[1:]):
        for b0, b1 in zip(second, second[1:]):
            intersects = _segments_intersect(a0, a1, b0, b1)
            distance = 0.0 if intersects else min(
                _point_segment_distance(a0, b0, b1),
                _point_segment_distance(a1, b0, b1),
                _point_segment_distance(b0, a0, a1),
                _point_segment_distance(b1, a0, a1),
            )
            if distance < best_distance:
                best_distance = distance
                best_intersects = intersects
                best_angle = _segment_angle(a0, a1, b0, b1)
    return best_distance, best_intersects, best_angle


def _segments_intersect(
    a0: tuple[float, float],
    a1: tuple[float, float],
    b0: tuple[float, float],
    b1: tuple[float, float],
) -> bool:
    def orientation(
        p: tuple[float, float],
        q: tuple[float, float],
        r: tuple[float, float],
    ) -> float:
        return (q[0] - p[0]) * (r[1] - p[1]) - (
            q[1] - p[1]
        ) * (r[0] - p[0])

    def on_segment(
        start: tuple[float, float],
        point: tuple[float, float],
        end: tuple[float, float],
    ) -> bool:
        epsilon = 1e-9
        return (
            min(start[0], end[0]) - epsilon
            <= point[0]
            <= max(start[0], end[0]) + epsilon
            and min(start[1], end[1]) - epsilon
            <= point[1]
            <= max(start[1], end[1]) + epsilon
        )

    epsilon = 1e-9
    o1 = orientation(a0, a1, b0)
    o2 = orientation(a0, a1, b1)
    o3 = orientation(b0, b1, a0)
    o4 = orientation(b0, b1, a1)
    if (o1 > epsilon and o2 < -epsilon or o1 < -epsilon and o2 > epsilon) and (
        o3 > epsilon and o4 < -epsilon or o3 < -epsilon and o4 > epsilon
    ):
        return True
    return (
        abs(o1) <= epsilon
        and on_segment(a0, b0, a1)
        or abs(o2) <= epsilon
        and on_segment(a0, b1, a1)
        or abs(o3) <= epsilon
        and on_segment(b0, a0, b1)
        or abs(o4) <= epsilon
        and on_segment(b0, a1, b1)
    )


def _point_segment_distance(
    point: tuple[float, float],
    start: tuple[float, float],
    end: tuple[float, float],
) -> float:
    dx = end[0] - start[0]
    dy = end[1] - start[1]
    denominator = dx * dx + dy * dy
    if denominator == 0.0:
        return math.hypot(point[0] - start[0], point[1] - start[1])
    ratio = max(
        0.0,
        min(
            1.0,
            (
                (point[0] - start[0]) * dx
                + (point[1] - start[1]) * dy
            )
            / denominator,
        ),
    )
    projection = (start[0] + ratio * dx, start[1] + ratio * dy)
    return math.hypot(point[0] - projection[0], point[1] - projection[1])


def _segment_angle(
    a0: tuple[float, float],
    a1: tuple[float, float],
    b0: tuple[float, float],
    b1: tuple[float, float],
) -> float | None:
    first = (a1[0] - a0[0], a1[1] - a0[1])
    second = (b1[0] - b0[0], b1[1] - b0[1])
    lengths = math.hypot(*first) * math.hypot(*second)
    if lengths == 0.0:
        return None
    cosine = max(
        -1.0,
        min(1.0, (first[0] * second[0] + first[1] * second[1]) / lengths),
    )
    angle = math.degrees(math.acos(cosine))
    return min(angle, 180.0 - angle)
