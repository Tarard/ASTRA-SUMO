from __future__ import annotations

from collections import defaultdict
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Mapping, Sequence
import xml.etree.ElementTree as ET

from torii_sumo.core.digital_twin import _mapem_node_id


SCHEMA_ID = "torii.composable-intersection-archetype/v2"

BASE_SKELETONS = frozenset({"T3", "X4", "A5_plus", "roundabout", "other", "unknown"})
PHYSICAL_ARRANGEMENTS = frozenset(
    {
        "single_core",
        "staggered",
        "skew",
        "compound_candidate",
        "compound",
        "one_way_pair",
        "unknown",
    }
)
CONTROL_DOMAINS = frozenset(
    {
        "uncontrolled_or_unknown",
        "linked_controllers",
        "multi_owner_single_controller_candidate",
        "multi_owner_single_controller",
        "one_owner_one_controller_candidate",
        "one_owner_one_controller",
        "single_controller_owner_unknown",
    }
)
MOVEMENT_GRAPH_CLASSES = frozenset(
    {
        "unknown",
        "complete_no_uturn_arm_graph_with_lane_adjacency",
        "partial_arm_graph_with_lane_adjacency",
        "sparse_or_restricted_directed_lane_adjacency",
    }
)
CHANNELIZATION_MODIFIERS = frozenset(
    {
        "distributed_stopline_markers",
        "merge_diverge",
        "preserved_internal_connectors",
        "lane_fanout",
        "pedestrian_crossing",
    }
)
OWNER_LAYOUT_STATUSES = frozenset({"automatic_candidate", "review_required", "confirmed"})

INTERSECTION_ARCHETYPE_PROTOTYPES: dict[str, dict[str, str]] = {
    "simple_T3_v1": {
        "base_skeleton": "T3",
        "physical_arrangement": "single_core",
        "family": "simple_T3_family",
    },
    "simple_X4_v1": {
        "base_skeleton": "X4",
        "physical_arrangement": "single_core",
        "family": "simple_X4_family",
    },
    "hamburg_2394_v1": {
        "base_skeleton": "T3",
        "physical_arrangement": "compound_candidate",
        "family": "channelized_T3_family",
    },
}


def registered_intersection_archetypes() -> dict[str, dict[str, str]]:
    """Return the finite prototype registry used by simple T3/X4 and 2394."""

    return {
        prototype_id: dict(definition)
        for prototype_id, definition in sorted(INTERSECTION_ARCHETYPE_PROTOTYPES.items())
    }


def build_mapem_archetype_evidence(
    map_file: Path,
    node_id: str,
    *,
    validated_arm_groups: Sequence[Sequence[str]] = (),
    validated_arm_group_evidence_id: str | None = None,
    validated_continuous_axis: Sequence[str] = (),
    validated_continuous_axis_evidence_id: str | None = None,
) -> dict[str, Any]:
    """Extract classification evidence without flattening MAP lane semantics.

    The existing digital-twin MAP parser intentionally exposes the lane graph used
    for binding.  Classification additionally needs NodeXY markers such as
    ``stopLine`` and ``mergePoint`` plus Hamburg's ``intersectionPart`` field, so
    this reader keeps those fields in a separate, evidence-only artifact.
    """

    map_file = map_file.resolve()
    root = ET.parse(map_file).getroot()
    wanted = _normalize_identifier(node_id)
    all_geometries = _descendants(root, "IntersectionGeometry")
    geometries = [
        geometry
        for geometry in all_geometries
        if _normalize_identifier(_geometry_id(geometry)) == wanted
    ]
    if len(geometries) != 1:
        raise ValueError(
            f"expected exactly one IntersectionGeometry for {node_id}, got {len(geometries)}"
        )
    geometry = geometries[0]
    lane_width_raw = _float_or_none(_text(_child(geometry, "laneWidth")))
    lane_width_m = lane_width_raw / 100.0 if lane_width_raw is not None else None
    if lane_width_m is not None and (not math.isfinite(lane_width_m) or lane_width_m <= 0):
        lane_width_m = None
    lane_set = _child(geometry, "laneSet")
    if lane_set is None:
        raise ValueError(f"IntersectionGeometry {node_id} has no laneSet")

    lanes: list[dict[str, Any]] = []
    connections: list[dict[str, str]] = []
    stop_lines: list[dict[str, Any]] = []
    merge_points: list[dict[str, Any]] = []
    for lane_element in _children(lane_set, "GenericLane"):
        lane_id = _text(_child(lane_element, "laneID"))
        lane_type = _lane_type(lane_element)
        ingress_approach = _text(_child(lane_element, "ingressApproach"))
        egress_approach = _text(_child(lane_element, "egressApproach"))
        points, markers = _lane_points_and_markers(lane_element)
        lane = {
            "lane_id": lane_id,
            "lane_type": lane_type,
            "ingress_approach": ingress_approach,
            "egress_approach": egress_approach,
            "points_m": [[x, y] for x, y in points],
        }
        lanes.append(lane)
        approach_id = ingress_approach or egress_approach
        for marker in markers:
            record = {
                "lane_id": lane_id,
                "lane_type": lane_type,
                "approach_id": approach_id,
                "point_m": [marker[1][0], marker[1][1]],
            }
            if marker[0] == "stopLine":
                stop_lines.append(record)
            elif marker[0] == "mergePoint":
                merge_points.append(record)

        connects_to = _child(lane_element, "connectsTo")
        if connects_to is None:
            continue
        for connection in _children(connects_to, "Connection"):
            connecting_lane = _child(connection, "connectingLane")
            connections.append(
                {
                    "connection_id": _text(_child(connection, "connectionID")),
                    "ingress_lane_id": lane_id,
                    "egress_lane_id": _text(_child(connecting_lane, "lane")),
                    "signal_group": _text(_child(connection, "signalGroup")),
                    "maneuver_bits": _text(_child(connecting_lane, "maneuver")),
                }
            )

    lane_ids = [str(lane["lane_id"]) for lane in lanes]
    if any(not lane_id for lane_id in lane_ids):
        raise ValueError(f"IntersectionGeometry {node_id} contains an empty laneID")
    duplicate_lane_ids = sorted(
        {lane_id for lane_id in lane_ids if lane_ids.count(lane_id) > 1},
        key=_natural_key,
    )
    if duplicate_lane_ids:
        raise ValueError(
            f"IntersectionGeometry {node_id} contains duplicate lane IDs: {duplicate_lane_ids}"
        )
    lane_by_id = {lane["lane_id"]: lane for lane in lanes}
    unknown_connection_lanes = sorted(
        {
            lane_id
            for connection in connections
            for lane_id in (
                connection["ingress_lane_id"],
                connection["egress_lane_id"],
            )
            if lane_id not in lane_by_id
        },
        key=_natural_key,
    )
    if unknown_connection_lanes:
        raise ValueError(
            f"IntersectionGeometry {node_id} connections reference lanes outside the selected geometry: "
            f"{unknown_connection_lanes}"
        )
    lane_type_by_id = {lane_id: lane["lane_type"] for lane_id, lane in lane_by_id.items()}
    vehicle_lanes = [lane for lane in lanes if lane["lane_type"].lower() == "vehicle"]
    vehicle_connections = [
        {
            **connection,
            "ingress_approach_id": lane_by_id[connection["ingress_lane_id"]][
                "ingress_approach"
            ],
            "egress_approach_id": lane_by_id[connection["egress_lane_id"]][
                "egress_approach"
            ],
        }
        for connection in connections
        if connection["ingress_lane_id"] in lane_by_id
        and connection["egress_lane_id"] in lane_by_id
        and lane_type_by_id.get(connection["ingress_lane_id"], "").lower() == "vehicle"
        and lane_type_by_id.get(connection["egress_lane_id"], "").lower() == "vehicle"
    ]
    vehicle_approach_pairs = sorted(
        {
            (
                connection["ingress_approach_id"],
                connection["egress_approach_id"],
            )
            for connection in vehicle_connections
            if connection["ingress_approach_id"] and connection["egress_approach_id"]
        },
        key=lambda pair: (_natural_key(pair[0]), _natural_key(pair[1])),
    )
    vehicle_approach_ids = sorted(
        {
            approach
            for lane in vehicle_lanes
            for approach in (lane["ingress_approach"], lane["egress_approach"])
            if approach
        },
        key=_natural_key,
    )
    approach_bearing_samples = _vehicle_approach_bearing_samples(vehicle_lanes)
    approach_bearings = {
        approach_id: _circular_mean_deg(values)
        for approach_id, values in approach_bearing_samples.items()
    }
    arm_clusters, arm_cluster_status = _validated_arm_clusters(
        vehicle_approach_ids,
        validated_arm_groups,
        evidence_id=validated_arm_group_evidence_id,
    )
    arm_group_validation = _validate_reviewed_arm_groups(
        arm_clusters,
        vehicle_lanes,
        approach_bearing_samples,
        vehicle_approach_pairs,
    ) if arm_cluster_status == "reviewed_arm_groups" else {
        "status": "unknown_pending_gate_validation",
        "reasons": [],
    }
    if arm_group_validation["status"] != "pass" and arm_cluster_status == "reviewed_arm_groups":
        raise ValueError(
            "validated arm groups conflict with MAP evidence: "
            f"{arm_group_validation['reasons']}"
        )
    physical_arm_count = len(arm_clusters) if arm_cluster_status == "reviewed_arm_groups" else None
    arm_bearings = _cluster_bearings(arm_clusters, approach_bearings)
    continuous_axis, continuity_error, continuous_axis_status = _validated_axis(
        validated_continuous_axis,
        arm_bearings,
        evidence_id=validated_continuous_axis_evidence_id,
    )
    approach_to_arm = {
        approach_id: cluster[0]
        for cluster in arm_clusters
        for approach_id in cluster
    }
    vehicle_arm_movement_pairs = sorted(
        {
            (approach_to_arm[left], approach_to_arm[right])
            for left, right in vehicle_approach_pairs
            if left in approach_to_arm
            and right in approach_to_arm
            and approach_to_arm[left] != approach_to_arm[right]
        },
        key=lambda pair: (_natural_key(pair[0]), _natural_key(pair[1])),
    )

    selected_lane_pairs = {
        (connection["ingress_lane_id"], connection["egress_lane_id"])
        for connection in connections
        if connection["ingress_lane_id"] and connection["egress_lane_id"]
    }
    other_lane_pairs = set().union(
        *(
            _geometry_connection_pairs(other)
            for other in all_geometries
            if other is not geometry
        )
    ) if len(all_geometries) > 1 else set()
    ambiguous_lane_pairs = selected_lane_pairs & other_lane_pairs
    selected_streams: list[ET.Element] = []
    for stream in _descendants(root, "TrafficStreamConfigData"):
        pair = (
            _text(_child(stream, "refLaneId")),
            _text(_child(stream, "refConnectTo")),
        )
        if pair in selected_lane_pairs and pair not in ambiguous_lane_pairs:
            selected_streams.append(stream)
    intersection_parts = sorted(
        {
            _text(_child(stream, "intersectionPart"))
            for stream in selected_streams
            if _text(_child(stream, "intersectionPart"))
        },
        key=_natural_key,
    )
    intersection_part_status = (
        "ambiguous_shared_lane_pairs"
        if ambiguous_lane_pairs
        else "pair_bound"
        if selected_streams
        else "unavailable"
    )
    signal_groups = sorted(
        {
            connection["signal_group"]
            for connection in connections
            if connection["signal_group"]
        },
        key=_natural_key,
    )
    lane_types = sorted({lane["lane_type"] for lane in lanes})
    vehicle_stop_lines = [
        item for item in stop_lines if item["lane_type"].lower() == "vehicle"
    ]
    stop_line_clusters, stop_line_cluster_status = _cluster_stop_lines_by_approach(
        vehicle_stop_lines,
        approach_bearings=approach_bearings,
        lane_width_m=lane_width_m,
    )
    expected_stop_line_lanes = sorted(
        {
            str(lane["lane_id"])
            for lane in vehicle_lanes
            if lane["ingress_approach"]
        },
        key=_natural_key,
    )
    marker_stop_line_lanes = sorted(
        {str(item["lane_id"]) for item in vehicle_stop_lines},
        key=_natural_key,
    )
    if expected_stop_line_lanes != marker_stop_line_lanes:
        stop_line_cluster_status = "lower_bound_incomplete_ingress_lane_marker_coverage"
    vehicle_arm_ids = [cluster[0] for cluster in arm_clusters]
    third_arms = [approach for approach in vehicle_arm_ids if approach not in set(continuous_axis)]
    payload: dict[str, Any] = {
        "schema_id": "torii.mapem-archetype-evidence/v2",
        "node_id": str(node_id),
        "source_file": str(map_file),
        "source_sha256": _sha256_file(map_file),
        "intersection_geometry_count": 1,
        "official_intersection_parts": intersection_parts,
        "official_intersection_part_status": intersection_part_status,
        "ambiguous_traffic_stream_lane_pairs": [
            list(pair)
            for pair in sorted(
                ambiguous_lane_pairs,
                key=lambda pair: (_natural_key(pair[0]), _natural_key(pair[1])),
            )
        ],
        "lane_count": len(lanes),
        "vehicle_lane_count": len(vehicle_lanes),
        "lane_types": lane_types,
        "connection_count": len(connections),
        "vehicle_movement_count": len(vehicle_connections),
        "vehicle_approach_movement_pairs": [list(pair) for pair in vehicle_approach_pairs],
        "signal_group_count": len(signal_groups),
        "signal_groups": signal_groups,
        "stop_line_count": len(stop_lines),
        "vehicle_stop_line_count": sum(
            1 for item in stop_lines if item["lane_type"].lower() == "vehicle"
        ),
        "merge_point_count": len(merge_points),
        "vehicle_merge_point_count": sum(
            1 for item in merge_points if item["lane_type"].lower() == "vehicle"
        ),
        "vehicle_approach_ids": vehicle_approach_ids,
        "vehicle_approach_id_count": len(vehicle_approach_ids),
        "vehicle_arm_count": physical_arm_count,
        "vehicle_arm_count_status": arm_cluster_status,
        "vehicle_arm_group_validation": arm_group_validation,
        "vehicle_arm_clusters": arm_clusters,
        "vehicle_arm_ids": vehicle_arm_ids,
        "vehicle_arm_movement_pairs": [list(pair) for pair in vehicle_arm_movement_pairs],
        "validated_arm_group_evidence_id": str(validated_arm_group_evidence_id or ""),
        "vehicle_approach_bearings_deg": approach_bearings,
        "vehicle_approach_lane_bearings_deg": approach_bearing_samples,
        "vehicle_arm_bearings_deg": arm_bearings,
        "continuous_axis_approach_ids": list(continuous_axis),
        "continuous_axis_opposition_error_deg": continuity_error,
        "continuous_axis_status": continuous_axis_status,
        "validated_continuous_axis_evidence_id": str(
            validated_continuous_axis_evidence_id or ""
        ),
        "third_arm_approach_ids": third_arms,
        "stop_lines": stop_lines,
        "vehicle_stop_line_clusters_candidate": stop_line_clusters,
        "vehicle_stop_line_marker_count": len(vehicle_stop_lines),
        "vehicle_stop_line_lane_coverage": {
            "expected_ingress_lane_ids": expected_stop_line_lanes,
            "marker_lane_ids": marker_stop_line_lanes,
            "complete": expected_stop_line_lanes == marker_stop_line_lanes,
        },
        "vehicle_stop_line_cluster_count_candidate": len(stop_line_clusters),
        "vehicle_stop_line_cluster_status": stop_line_cluster_status,
        "official_lane_width_m": lane_width_m,
        "merge_points": merge_points,
        "vehicle_movements": vehicle_connections,
    }
    payload["evidence_id"] = f"mapem-archetype-{_stable_evidence_digest(payload)[:20]}"
    return payload


def build_ocit_controller_domain_evidence(
    ocit_file: Path,
    node_id: str,
) -> dict[str, Any]:
    """Extract controller and technical-subnode evidence from official OCIT XML."""

    ocit_file = ocit_file.resolve()
    root = ET.parse(ocit_file).getroot()
    headers = _descendants(root, "Kopfdaten")
    if len(headers) != 1:
        raise ValueError(f"expected exactly one OCIT Kopfdaten, got {len(headers)}")
    official_id = _normalize_identifier(_text(_child(headers[0], "Kurzbezeichnung")))
    wanted = _normalize_identifier(node_id)
    if official_id != wanted:
        raise ValueError(
            f"OCIT Kurzbezeichnung does not match requested node: {official_id!r} != {wanted!r}"
        )

    phase_subnodes = sorted(
        {
            _normalize_identifier(_text(_child(phase, "VerkehrstechnischerTeilknotenNr")))
            for phase in _descendants(root, "Phase")
            if _text(_child(phase, "VerkehrstechnischerTeilknotenNr"))
        },
        key=_natural_key,
    )
    tk_records: list[dict[str, str]] = []
    for tk in _descendants(root, "Tk"):
        short_name = _text(_child(tk, "BezeichnungKurz"))
        outstation = _normalize_identifier(_text(_child(tk, "OCITOutstationNr")))
        if short_name or outstation:
            tk_records.append(
                {
                    "short_name": short_name,
                    "ocit_outstation_number": outstation,
                }
            )
    tk_records.sort(
        key=lambda item: (
            _natural_key(item["ocit_outstation_number"]),
            item["short_name"],
        )
    )
    tk_outstations = sorted(
        {item["ocit_outstation_number"] for item in tk_records if item["ocit_outstation_number"]},
        key=_natural_key,
    )
    if not phase_subnodes and not tk_outstations:
        raise ValueError("OCIT contains no technical-subnode evidence")
    subnode_consistency = "pass"
    subnode_consistency_detail: dict[str, Any] | None = None
    if phase_subnodes and tk_outstations and phase_subnodes != tk_outstations:
        # Hamburg publishes a few OCIT files where the phase table and OMTC
        # hardware inventory carry different technical-subnode lists.  This
        # does not make the controller identity unusable for structural MAP
        # binding, but it must remain explicit evidence for downstream timing
        # promotion.  Prefer the phase list for signal-domain classification
        # and retain the hardware list verbatim for the audit manifest.
        subnode_consistency = "review_required"
        subnode_consistency_detail = {
            "phase_ids": phase_subnodes,
            "omtc_outstation_ids": tk_outstations,
            "reason": "phase_and_omtc_technical_subnode_identifiers_disagree",
        }
    technical_subnodes = phase_subnodes or tk_outstations
    payload: dict[str, Any] = {
        "schema_id": "torii.ocit-controller-domain-evidence/v1",
        "node_id": wanted,
        "source_file": str(ocit_file),
        "source_sha256": _sha256_file(ocit_file),
        "controller_domain_ids": [wanted],
        "controller_domain_count": 1,
        "technical_subnode_ids": technical_subnodes,
        "technical_subnode_count": len(technical_subnodes),
        "phase_technical_subnode_ids": phase_subnodes,
        "omtc_tk_records": tk_records,
        "subnode_consistency": subnode_consistency,
        "subnode_consistency_detail": subnode_consistency_detail,
        "evidence_status": (
            "official_ocit_parsed"
            if subnode_consistency == "pass"
            else "official_ocit_parsed_with_subnode_mismatch"
        ),
    }
    payload["evidence_id"] = f"ocit-controller-domain-{_stable_evidence_digest(payload)[:20]}"
    return payload


def build_sumo_owner_layout_evidence(
    net_file: Path,
    cell_junction_ids: Sequence[str],
    stopline_owner_candidates: Sequence[str],
    *,
    micro_edge_threshold_m: float = 1.0,
    reviewed_owner_components: Sequence[Sequence[str]] = (),
    reviewed_owner_evidence_id: str | None = None,
    reviewed_owner_review_status: str = "review_required",
) -> dict[str, Any]:
    """Build a classification-only quotient of nearby SUMO junctions.

    Distance proposes candidates only.  Even reviewed components remain an
    execution hint and never authorize mutation of ``net_file``.
    """

    if micro_edge_threshold_m <= 0:
        raise ValueError("micro_edge_threshold_m must be positive")
    if reviewed_owner_review_status not in OWNER_LAYOUT_STATUSES:
        raise ValueError(
            "reviewed_owner_review_status must be one of "
            f"{sorted(OWNER_LAYOUT_STATUSES)}"
        )
    net_file = net_file.resolve()
    root = ET.parse(net_file).getroot()
    requested = tuple(
        dict.fromkeys(
            _string_sequence(
                cell_junction_ids,
                "cell_junction_ids",
                allow_empty=True,
                drop_null=False,
            )
        )
    )
    if not requested:
        raise ValueError("cell_junction_ids must not be empty")
    junction_ids = {
        element.attrib["id"]
        for element in root.findall("junction")
        if element.attrib.get("id") and not element.attrib["id"].startswith(":")
    }
    missing = sorted(set(requested) - junction_ids, key=_natural_key)
    if missing:
        raise ValueError(f"cell junctions are absent from SUMO network: {missing}")
    stopline_owners = tuple(
        sorted(
            set(
                _string_sequence(
                    stopline_owner_candidates,
                    "stopline_owner_candidates",
                    allow_empty=True,
                    drop_null=False,
                )
            ),
            key=_natural_key,
        )
    )
    unknown_stopline_owners = sorted(set(stopline_owners) - set(requested), key=_natural_key)
    if unknown_stopline_owners:
        raise ValueError(
            f"stopline owner candidates are outside the classification cell: {unknown_stopline_owners}"
        )

    internal_edges: list[dict[str, Any]] = []
    length_shape_checks: list[dict[str, Any]] = []
    parity_failures: list[dict[str, Any]] = []
    for edge in root.findall("edge"):
        edge_id = edge.attrib.get("id", "")
        from_id = edge.attrib.get("from", "")
        to_id = edge.attrib.get("to", "")
        if (
            not edge_id
            or edge_id.startswith(":")
            or edge.attrib.get("function") == "internal"
            or from_id not in set(requested)
            or to_id not in set(requested)
        ):
            continue
        edge_checks: list[dict[str, Any]] = []
        for lane in edge.findall("lane"):
            declared_length = _float_or_none(lane.attrib.get("length"))
            rendered_length = _shape_length(lane.attrib.get("shape", ""))
            reasons: list[str] = []
            if declared_length is None or not math.isfinite(declared_length) or declared_length <= 0:
                reasons.append("declared_length_missing_or_invalid")
            if rendered_length is None or not math.isfinite(rendered_length) or rendered_length <= 0:
                reasons.append("rendered_shape_missing_or_invalid")
            delta = (
                abs(declared_length - rendered_length)
                if declared_length is not None and rendered_length is not None
                else None
            )
            row = {
                "edge_id": edge_id,
                "lane_id": lane.attrib.get("id", ""),
                "declared_length_m": declared_length,
                "rendered_shape_length_m": rendered_length,
                "absolute_delta_m": delta,
                "status": "pass" if not reasons else "not_evaluable" if reasons == ["rendered_shape_missing_or_invalid"] else "fail",
                "reasons": reasons,
            }
            length_shape_checks.append(row)
            edge_checks.append(row)
            if row["status"] == "fail":
                parity_failures.append(row)
        threshold_side_disagreement = any(
            row["status"] == "pass"
            and (
                float(row["declared_length_m"]) < micro_edge_threshold_m
            )
            != (
                float(row["rendered_shape_length_m"]) < micro_edge_threshold_m
            )
            for row in edge_checks
        )
        micro_candidate_by_length = bool(edge_checks) and not threshold_side_disagreement and all(
            row["status"] == "pass"
            and float(row["declared_length_m"]) < micro_edge_threshold_m
            and float(row["rendered_shape_length_m"]) < micro_edge_threshold_m
            for row in edge_checks
        )
        comparable_lengths = [
            max(float(row["declared_length_m"]), float(row["rendered_shape_length_m"]))
            for row in edge_checks
            if row["status"] == "pass"
        ]
        length_m = max(comparable_lengths) if comparable_lengths else None
        internal_edges.append(
            {
                "edge_id": edge_id,
                "from_junction_id": from_id,
                "to_junction_id": to_id,
                "length_m": length_m,
                "micro_candidate_by_length": micro_candidate_by_length,
                "micro_threshold_side_disagreement": threshold_side_disagreement,
            }
        )

    parent = {node_id: node_id for node_id in requested}

    def find(value: str) -> str:
        while parent[value] != value:
            parent[value] = parent[parent[value]]
            value = parent[value]
        return value

    def union(left: str, right: str) -> None:
        left_root = find(left)
        right_root = find(right)
        if left_root != right_root:
            parent[right_root] = left_root

    stopline_set = set(stopline_owners)
    micro_edges: list[dict[str, Any]] = []
    blocked_micro_edges: list[dict[str, Any]] = []
    for edge in internal_edges:
        if edge["micro_threshold_side_disagreement"]:
            blocked_micro_edges.append(
                {**edge, "blocked_reason": "declared_and_rendered_lengths_cross_micro_threshold"}
            )
            continue
        if not edge["micro_candidate_by_length"]:
            continue
        endpoints = {edge["from_junction_id"], edge["to_junction_id"]}
        # Two independently evidenced stop-line owners must never be collapsed
        # merely because their SUMO node centers are close.
        if len(endpoints & stopline_set) >= 2:
            blocked_micro_edges.append(
                {**edge, "blocked_reason": "both_endpoints_have_stopline_owner_evidence"}
            )
            continue
        union(edge["from_junction_id"], edge["to_junction_id"])
        micro_edges.append(edge)

    quotient: dict[str, list[str]] = defaultdict(list)
    for node_id in requested:
        quotient[find(node_id)].append(node_id)
    quotient_components = sorted(
        (sorted(values, key=_natural_key) for values in quotient.values()),
        key=lambda values: tuple(_natural_key(value) for value in values),
    )

    reviewed = _validated_components(reviewed_owner_components, requested)
    if reviewed and not str(reviewed_owner_evidence_id or "").strip():
        raise ValueError(
            "reviewed_owner_evidence_id is required when reviewed_owner_components are supplied"
        )
    if parity_failures:
        failing_lanes = [str(row["lane_id"]) for row in parity_failures]
        raise ValueError(
            "SUMO lane length/shape parity blocks owner-layout classification: "
            f"{failing_lanes}"
        )
    owner_components = reviewed or quotient_components
    owner_index = {
        node_id: index
        for index, component in enumerate(owner_components)
        for node_id in component
    }
    preserved_connectors = [
        edge
        for edge in internal_edges
        if owner_index.get(edge["from_junction_id"]) != owner_index.get(edge["to_junction_id"])
    ]
    payload: dict[str, Any] = {
        "schema_id": "torii.sumo-owner-layout-evidence/v2",
        "source_file": str(net_file),
        "source_sha256": _sha256_file(net_file),
        "classification_only": True,
        "automatic_geometry_authorization": "blocked",
        "raw_node_ids": sorted(requested, key=_natural_key),
        "raw_node_count": len(requested),
        "stopline_owner_candidates": list(stopline_owners),
        "micro_edge_threshold_m": micro_edge_threshold_m,
        "length_shape_parity": {
            "status": (
                "blocked_threshold_side_disagreement"
                if any(edge["micro_threshold_side_disagreement"] for edge in internal_edges)
                else "decision_side_pass"
                if length_shape_checks
                and all(row["status"] == "pass" for row in length_shape_checks)
                else "not_evaluable"
            ),
            "policy": (
                "all lanes must have valid declared and rendered lengths on the same side "
                "of micro_edge_threshold_m; absolute deltas remain descriptive"
            ),
            "checks": length_shape_checks,
        },
        "micro_edge_candidates": micro_edges,
        "blocked_micro_edges": blocked_micro_edges,
        "classification_quotient_components": quotient_components,
        "reviewed_owner_components": reviewed,
        "reviewed_owner_evidence_id": str(reviewed_owner_evidence_id or ""),
        "owner_layout_status": (
            reviewed_owner_review_status if reviewed else "automatic_candidate"
        ),
        "owner_candidate_components": owner_components,
        "owner_candidate_count": len(owner_components),
        "local_join_candidate_groups": [
            component for component in owner_components if len(component) > 1
        ],
        "preserved_connector_edges": preserved_connectors,
        "physical_conflict_core_count": None,
        "physical_conflict_core_status": "unknown_pending_conflict_analysis",
    }
    payload["evidence_id"] = f"sumo-owner-layout-{_stable_evidence_digest(payload)[:20]}"
    return payload


def classify_composable_intersection(
    mapem_evidence: Mapping[str, Any],
    owner_layout_evidence: Mapping[str, Any],
    controller_evidence: Mapping[str, Any],
    candidate_gates: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Classify a junction independently from any reconstruction strategy."""

    gates = dict(candidate_gates or {})
    arm_count = _positive_int_or_none(mapem_evidence.get("vehicle_arm_count"))
    explicit_base = _optional_enum(gates.get("base_skeleton"), BASE_SKELETONS, "base_skeleton")
    base_skeleton = explicit_base or _base_skeleton(
        arm_count,
        _strict_bool(gates.get("is_roundabout", False), "is_roundabout"),
    )
    owner_count = _positive_int_or_none(owner_layout_evidence.get("owner_candidate_count"))
    raw_physical_core_count = gates.get("physical_conflict_core_count")
    physical_core_count = _positive_int_or_none(raw_physical_core_count)
    if raw_physical_core_count is not None and physical_core_count is None:
        raise ValueError("physical_conflict_core_count must be a positive integer or null")
    controller_ids = sorted(
        set(
            _string_sequence(
                controller_evidence.get("controller_domain_ids", ()),
                "controller_domain_ids",
                allow_empty=True,
                drop_null=True,
            )
        ),
        key=_natural_key,
    )
    controller_count = len(controller_ids)

    explicit_arrangement = _optional_enum(
        gates.get("physical_arrangement"),
        PHYSICAL_ARRANGEMENTS,
        "physical_arrangement",
    )
    if explicit_arrangement:
        physical_arrangement = explicit_arrangement
    elif physical_core_count is not None and physical_core_count > 1:
        physical_arrangement = "compound"
    elif physical_core_count == 1:
        physical_arrangement = "single_core"
    else:
        physical_arrangement = "unknown"

    owner_layout_status = str(
        owner_layout_evidence.get("owner_layout_status", "automatic_candidate")
    )
    if owner_layout_status not in OWNER_LAYOUT_STATUSES:
        raise ValueError(f"invalid owner_layout_status: {owner_layout_status!r}")
    if controller_count == 0:
        control_domain = "uncontrolled_or_unknown"
    elif controller_count > 1:
        control_domain = "linked_controllers"
    elif owner_count is not None and owner_count > 1:
        control_domain = (
            "multi_owner_single_controller"
            if owner_layout_status == "confirmed"
            else "multi_owner_single_controller_candidate"
        )
    elif owner_count == 1:
        control_domain = (
            "one_owner_one_controller"
            if owner_layout_status == "confirmed"
            else "one_owner_one_controller_candidate"
        )
    else:
        control_domain = "single_controller_owner_unknown"
    _require_enum(control_domain, CONTROL_DOMAINS, "control_domain")

    channelization = _channelization_modifiers(mapem_evidence, owner_layout_evidence)
    mode_modifiers = _mode_modifiers(mapem_evidence)
    movement_graph_class = _movement_graph_class(mapem_evidence)
    family = _family_name(base_skeleton, channelization)

    unknown_dimensions: list[str] = []
    if base_skeleton == "unknown":
        unknown_dimensions.append("base_skeleton")
    if physical_core_count is None:
        unknown_dimensions.append("physical_conflict_core_count")
    if physical_arrangement == "unknown":
        unknown_dimensions.append("physical_arrangement")
    if control_domain.endswith("unknown"):
        unknown_dimensions.append("owner_control_domain")

    review_gates = list(
        dict.fromkeys(
            _string_sequence(
                gates.get("review_gates", ()),
                "review_gates",
                allow_empty=True,
                drop_null=False,
            )
        )
    )
    if physical_core_count is None:
        review_gates.append(
            "separate conflict envelopes, storage segments, and graph cut evidence before claiming multiple physical cores"
        )
    if owner_count is not None and owner_count > 1:
        review_gates.append(
            "background Netedit render must prove owner polygons and lane surfaces do not overlap"
        )
    if owner_layout_status != "confirmed":
        review_gates.append(
            "owner components remain candidates until their provenance and rendered geometry are approved"
        )
    review_gates = list(dict.fromkeys(review_gates))

    confidence = {
        "base_skeleton": (
            0.95
            if explicit_base
            else 0.85
            if arm_count is not None and base_skeleton != "unknown"
            else 0.0
        ),
        "channelization": 0.82 if channelization else 0.45,
        "physical_arrangement": (
            0.72
            if physical_arrangement == "compound_candidate"
            else 0.9
            if physical_core_count is not None
            else 0.0
        ),
        "control_domain": (
            0.99
            if controller_count and owner_count is not None and owner_layout_status == "confirmed"
            else 0.78
            if controller_count and owner_count is not None
            else 0.45
        ),
        "physical_conflict_core_count": 0.0 if physical_core_count is None else 0.9,
    }
    classification = {
        "base_skeleton": base_skeleton,
        "physical_arrangement": physical_arrangement,
        "channelization_modifiers": channelization,
        "control_domain": control_domain,
        "movement_graph_class": movement_graph_class,
        "mode_and_restriction_modifiers": mode_modifiers,
        "family": family,
    }
    prototype_id = str(gates.get("prototype_id", "")).strip()
    if prototype_id:
        definition = INTERSECTION_ARCHETYPE_PROTOTYPES.get(prototype_id)
        if definition is None:
            raise ValueError(
                f"prototype_id must be registered; got {prototype_id!r}"
            )
        mismatches = {
            field: {"expected": expected, "observed": classification.get(field)}
            for field, expected in definition.items()
            if classification.get(field) != expected
        }
        if mismatches:
            raise ValueError(
                f"classification does not match registered prototype {prototype_id}: {mismatches}"
            )
    execution_hint = {
        "strategy": str(
            gates.get(
                "execution_strategy",
                "local_join_candidates_preserve_split_shared_controller"
                if owner_count and owner_count > 1 and controller_count == 1
                else "preserve_until_review",
            )
        ),
        "classification_only": True,
        "automatic_authorization": "blocked",
        "authorization_status": "review_required",
        "local_join_candidate_groups": owner_layout_evidence.get(
            "local_join_candidate_groups", []
        ),
        "preserve_owner_components": owner_layout_evidence.get(
            "owner_candidate_components", []
        ),
        "preserve_connector_edges": owner_layout_evidence.get(
            "preserved_connector_edges", []
        ),
        "controller_domain_ids": controller_ids,
        "review_gates": review_gates,
    }
    payload: dict[str, Any] = {
        "schema_id": SCHEMA_ID,
        "junction_id": str(mapem_evidence.get("node_id", "")),
        "prototype_id": prototype_id,
        "status": "review_required" if unknown_dimensions or review_gates else "classified",
        "automatic_promotion_gate": "blocked" if unknown_dimensions or review_gates else "review_required",
        "classification": classification,
        "canonical_type_key": "+".join(
            [
                base_skeleton,
                physical_arrangement,
                ",".join(channelization) or "unchannelized",
                control_domain,
                movement_graph_class,
                ",".join(mode_modifiers) or "mode_unknown",
            ]
        ),
        "counts": {
            "raw_node_count": owner_layout_evidence.get("raw_node_count"),
            "classification_join_group_count": len(
                owner_layout_evidence.get("local_join_candidate_groups", ())
            ),
            "physical_conflict_core_count": physical_core_count,
            "owner_count_after_rebuild_candidate": owner_count,
            "controller_domain_count": controller_count,
        },
        "official_intersection_parts": mapem_evidence.get(
            "official_intersection_parts", []
        ),
        "movement_graph": {
            "vehicle_lane_count": mapem_evidence.get("vehicle_lane_count", 0),
            "vehicle_movement_count": mapem_evidence.get("vehicle_movement_count", 0),
            "vehicle_approach_ids": mapem_evidence.get("vehicle_approach_ids", []),
            "vehicle_arm_ids": mapem_evidence.get("vehicle_arm_ids", []),
            "vehicle_arm_clusters": mapem_evidence.get("vehicle_arm_clusters", []),
            "continuous_axis_approach_ids": mapem_evidence.get(
                "continuous_axis_approach_ids", []
            ),
            "third_arm_approach_ids": mapem_evidence.get("third_arm_approach_ids", []),
        },
        "physical_conflict_core_status": (
            "known" if physical_core_count is not None else "unknown_pending_conflict_analysis"
        ),
        "unknown_dimensions": unknown_dimensions,
        "review_gates": review_gates,
        "confidence_by_dimension": confidence,
        "evidence": [
            {
                "dimension": "base_skeleton_and_movement_graph",
                "source": mapem_evidence.get("evidence_id"),
            },
            {
                "dimension": "owner_layout_candidate",
                "source": owner_layout_evidence.get("evidence_id"),
            },
            {
                "dimension": "controller_domain",
                "source": controller_evidence.get("evidence_id", "caller-declared"),
            },
        ],
        # Kept deliberately outside ``classification``.  An archetype is not a
        # materialization plan, and no classifier result may mutate a network.
        "execution_hint": execution_hint,
    }
    payload["classification_id"] = (
        f"intersection-archetype-{_stable_evidence_digest(payload)[:20]}"
    )
    return payload


def build_hamburg_2394_archetype_profile(
    *,
    map_file: Path,
    ocit_file: Path,
    source_net_file: Path,
) -> dict[str, Any]:
    """Build the versioned 2394 prototype through the generic classifier."""

    owner_components = (
        ("3847369287", "757036909", "76463166"),
        ("3847369288",),
        ("759714726",),
        ("2761334279", "757036795"),
        ("3847369285",),
    )
    cell_nodes = tuple(node_id for component in owner_components for node_id in component)
    mapem = build_mapem_archetype_evidence(
        map_file,
        "2394",
        validated_arm_groups=(("2",), ("3",), ("4",)),
        validated_arm_group_evidence_id="hamburg_2394_map_bearing_review_v1",
        validated_continuous_axis=("2", "4"),
        validated_continuous_axis_evidence_id="hamburg_2394_map_bearing_review_v1",
    )
    controller = build_ocit_controller_domain_evidence(ocit_file, "2394")
    if controller["technical_subnode_ids"] != ["1"]:
        raise ValueError(
            "Hamburg 2394 prototype requires the official OCIT single TK 1 contract; "
            f"got {controller['technical_subnode_ids']}"
        )
    owners = build_sumo_owner_layout_evidence(
        source_net_file,
        cell_nodes,
        ("3847369288", "759714726", "2761334279", "3847369285"),
        reviewed_owner_components=owner_components,
        reviewed_owner_evidence_id="hamburg_2394_owner_layout_review_v1",
        reviewed_owner_review_status="review_required",
    )
    return classify_composable_intersection(
        mapem,
        owners,
        controller,
        {
            "prototype_id": "hamburg_2394_v1",
            "base_skeleton": "T3",
            "physical_arrangement": "compound_candidate",
            "physical_conflict_core_count": None,
            "execution_strategy": "local_join_candidates_preserve_split_shared_controller",
            "review_gates": [
                "confirm south 6.25 m segment as crossing conflict segment or usable storage",
                "confirm whether east-outbound owner carries a motor signal link or only crosswalk conflict",
            ],
        },
    )


def _base_skeleton(arm_count: int | None, is_roundabout: bool) -> str:
    if is_roundabout:
        result = "roundabout"
    elif arm_count == 3:
        result = "T3"
    elif arm_count == 4:
        result = "X4"
    elif arm_count is not None and arm_count >= 5:
        result = "A5_plus"
    elif arm_count is not None and arm_count > 0:
        result = "other"
    else:
        result = "unknown"
    return _require_enum(result, BASE_SKELETONS, "base_skeleton")


def _family_name(base_skeleton: str, modifiers: Sequence[str]) -> str:
    return f"channelized_{base_skeleton}_family" if modifiers else f"simple_{base_skeleton}_family"


def _channelization_modifiers(
    mapem: Mapping[str, Any],
    owner_layout: Mapping[str, Any],
) -> list[str]:
    modifiers: list[str] = []
    vehicle_stop_line_cluster_count = _nonnegative_int(
        mapem.get("vehicle_stop_line_cluster_count_candidate", 0),
        "vehicle_stop_line_cluster_count_candidate",
    )
    if vehicle_stop_line_cluster_count > 1:
        modifiers.append("distributed_stopline_markers")
    if int(mapem.get("merge_point_count", 0) or 0) > 0:
        modifiers.append("merge_diverge")
    if owner_layout.get("preserved_connector_edges"):
        modifiers.append("preserved_internal_connectors")
    movements = mapem.get("vehicle_movements", ())
    by_ingress: dict[str, set[str]] = defaultdict(set)
    for movement in movements if isinstance(movements, Sequence) else ():
        if isinstance(movement, Mapping):
            by_ingress[str(movement.get("ingress_lane_id", ""))].add(
                str(movement.get("egress_lane_id", ""))
            )
    if any(len(targets - {""}) > 1 for targets in by_ingress.values()):
        modifiers.append("lane_fanout")
    lane_types = {
        value.lower()
        for value in _string_sequence(
            mapem.get("lane_types", ()),
            "lane_types",
            allow_empty=True,
            drop_null=False,
        )
    }
    if lane_types & {"crosswalk", "sidewalk", "pedestrian"}:
        modifiers.append("pedestrian_crossing")
    result = sorted(set(modifiers))
    for modifier in result:
        _require_enum(modifier, CHANNELIZATION_MODIFIERS, "channelization_modifier")
    return result


def _mode_modifiers(mapem: Mapping[str, Any]) -> list[str]:
    lane_types = {
        value.lower()
        for value in _string_sequence(
            mapem.get("lane_types", ()),
            "lane_types",
            allow_empty=True,
            drop_null=False,
        )
    }
    modifiers = ["motor_vehicle"] if "vehicle" in lane_types else []
    if lane_types & {"crosswalk", "sidewalk", "pedestrian"}:
        modifiers.append("pedestrian")
    if lane_types & {"bikelane", "bike", "bicycle"}:
        modifiers.append("bicycle")
    return sorted(set(modifiers))


def _movement_graph_class(mapem: Mapping[str, Any]) -> str:
    approaches = _string_sequence(
        mapem.get("vehicle_arm_ids", mapem.get("vehicle_approach_ids", ())),
        "vehicle_arm_ids",
        allow_empty=True,
        drop_null=False,
    )
    movements = mapem.get("vehicle_movements", ())
    explicit_raw = mapem.get("movement_graph_class")
    explicit = _optional_enum(
        explicit_raw,
        MOVEMENT_GRAPH_CLASSES,
        "movement_graph_class",
    )
    if explicit is not None:
        return explicit
    if not approaches or not movements:
        return "unknown"
    pairs = {
        (str(pair[0]), str(pair[1]))
        for pair in _sequence_value(
            mapem.get(
                "vehicle_arm_movement_pairs",
                mapem.get("vehicle_approach_movement_pairs", ()),
            ),
            "vehicle_arm_movement_pairs",
        )
        if isinstance(pair, Sequence)
        and not isinstance(pair, (str, bytes))
        and len(pair) == 2
        and str(pair[0]) != str(pair[1])
    }
    possible = len(approaches) * (len(approaches) - 1)
    if possible and len(pairs) == possible:
        result = "complete_no_uturn_arm_graph_with_lane_adjacency"
        _require_enum(result, MOVEMENT_GRAPH_CLASSES, "movement_graph_class")
        return result
    if possible and len(pairs) / possible >= 0.5:
        result = "partial_arm_graph_with_lane_adjacency"
    else:
        result = "sparse_or_restricted_directed_lane_adjacency"
    _require_enum(result, MOVEMENT_GRAPH_CLASSES, "movement_graph_class")
    return result


def _validated_arm_clusters(
    approach_ids: Sequence[str],
    reviewed_groups: Sequence[Sequence[str]],
    *,
    evidence_id: str | None,
) -> tuple[list[list[str]], str]:
    """Use only provenance-bound arm groups; raw MAP approach count is not an arm count."""

    ordered = sorted(set(map(str, approach_ids)), key=_natural_key)
    if not ordered:
        return [], "unavailable"
    if not reviewed_groups:
        return [], "unknown_pending_gate_validation"
    if not str(evidence_id or "").strip():
        raise ValueError(
            "validated_arm_group_evidence_id is required with validated_arm_groups"
        )
    clusters = _validated_components(reviewed_groups, ordered)
    return clusters, "reviewed_arm_groups"


def _validate_reviewed_arm_groups(
    clusters: Sequence[Sequence[str]],
    vehicle_lanes: Sequence[Mapping[str, Any]],
    bearing_samples: Mapping[str, Sequence[float]],
    movement_pairs: Sequence[tuple[str, str]],
) -> dict[str, Any]:
    reasons: list[str] = []
    lane_directions: dict[str, set[str]] = defaultdict(set)
    for lane in vehicle_lanes:
        ingress = str(lane.get("ingress_approach", ""))
        egress = str(lane.get("egress_approach", ""))
        if ingress:
            lane_directions[ingress].add("ingress")
        if egress:
            lane_directions[egress].add("egress")
    participating = {value for pair in movement_pairs for value in pair}
    group_samples: list[list[float]] = []
    for cluster in clusters:
        values: list[float] = []
        for approach_id in cluster:
            if not bearing_samples.get(approach_id):
                reasons.append(f"approach {approach_id} has no lane-bearing samples")
            values.extend(float(value) for value in bearing_samples.get(approach_id, ()))
            if lane_directions.get(approach_id) != {"ingress", "egress"}:
                reasons.append(
                    f"approach {approach_id} lacks both ingress and egress vehicle lanes"
                )
            if approach_id not in participating:
                reasons.append(f"approach {approach_id} has no cross-approach movement")
        group_samples.append(values)

    within_spans = [
        max(
            (_circular_distance_deg(left, right) for left in values for right in values),
            default=0.0,
        )
        for values in group_samples
    ]
    separations: list[dict[str, Any]] = []
    for left_index, left_values in enumerate(group_samples):
        for right_index, right_values in enumerate(group_samples[left_index + 1 :], start=left_index + 1):
            separation = min(
                (
                    _circular_distance_deg(left, right)
                    for left in left_values
                    for right in right_values
                ),
                default=0.0,
            )
            local_dispersion = max(within_spans[left_index], within_spans[right_index])
            separations.append(
                {
                    "left_group": list(clusters[left_index]),
                    "right_group": list(clusters[right_index]),
                    "minimum_between_group_separation_deg": round(separation, 6),
                    "maximum_within_group_dispersion_deg": round(local_dispersion, 6),
                }
            )
            if separation <= local_dispersion:
                reasons.append(
                    "reviewed arm groups have overlapping bearing envelopes: "
                    f"{list(clusters[left_index])} vs {list(clusters[right_index])}"
                )
    return {
        "status": "pass" if not reasons else "fail",
        "reasons": reasons,
        "within_group_dispersion_deg": [round(value, 6) for value in within_spans],
        "between_group_separation_checks": separations,
    }


def _cluster_bearings(
    clusters: Sequence[Sequence[str]],
    bearings: Mapping[str, float],
) -> dict[str, float]:
    result: dict[str, float] = {}
    for cluster in clusters:
        values = [bearings[value] for value in cluster if value in bearings]
        if not cluster or not values:
            continue
        x = sum(math.cos(math.radians(value)) for value in values)
        y = sum(math.sin(math.radians(value)) for value in values)
        if math.hypot(x, y) <= 1e-9:
            continue
        result[str(cluster[0])] = round(math.degrees(math.atan2(y, x)) % 360.0, 6)
    return dict(sorted(result.items(), key=lambda item: _natural_key(item[0])))


def _cluster_stop_lines_by_approach(
    records: Sequence[Mapping[str, Any]],
    *,
    approach_bearings: Mapping[str, float],
    lane_width_m: float | None,
) -> tuple[list[dict[str, Any]], str]:
    if lane_width_m is None:
        return [], "unknown_missing_official_lane_width"
    longitudinal_tolerance_m = lane_width_m / 2.0
    usable: dict[
        str,
        list[tuple[Mapping[str, Any], tuple[float, float], float]],
    ] = defaultdict(list)
    for record in records:
        raw_point = record.get("point_m")
        approach_id = str(record.get("approach_id", ""))
        if (
            approach_id in approach_bearings
            and isinstance(raw_point, Sequence)
            and not isinstance(raw_point, (str, bytes))
            and len(raw_point) >= 2
        ):
            try:
                point = (float(raw_point[0]), float(raw_point[1]))
            except (TypeError, ValueError):
                continue
            if all(math.isfinite(value) for value in point):
                bearing = math.radians(approach_bearings[approach_id])
                longitudinal = point[0] * math.cos(bearing) + point[1] * math.sin(bearing)
                usable[approach_id].append((record, point, longitudinal))
    grouped: list[
        tuple[str, list[tuple[Mapping[str, Any], tuple[float, float], float]]]
    ] = []
    for approach_id, values in sorted(usable.items(), key=lambda item: _natural_key(item[0])):
        values.sort(key=lambda item: item[2])
        current: list[tuple[Mapping[str, Any], tuple[float, float], float]] = []
        for item in values:
            candidate = [*current, item]
            longitudinal_values = [value[2] for value in candidate]
            if current and max(longitudinal_values) - min(longitudinal_values) > longitudinal_tolerance_m:
                grouped.append((approach_id, current))
                current = [item]
            else:
                current = candidate
        if current:
            grouped.append((approach_id, current))
    rows: list[dict[str, Any]] = []
    for approach_id, items in grouped:
        centroid = (
            sum(point[0] for _, point, _ in items) / len(items),
            sum(point[1] for _, point, _ in items) / len(items),
        )
        longitudinal_values = [value for _, _, value in items]
        rows.append(
            {
                "lane_ids": sorted(
                    {
                        str(record.get("lane_id", ""))
                        for record, _, _ in items
                        if record.get("lane_id")
                    },
                    key=_natural_key,
                ),
                "approach_ids": [approach_id],
                "centroid_m": [round(centroid[0], 6), round(centroid[1], 6)],
                "max_radius_m": round(
                    max(math.dist(point, centroid) for _, point, _ in items),
                    6,
                ),
                "longitudinal_span_m": round(
                    max(longitudinal_values) - min(longitudinal_values),
                    6,
                ),
                "marker_count": len(items),
            }
        )
    rows.sort(key=lambda row: (row["centroid_m"][0], row["centroid_m"][1]))
    for index, row in enumerate(rows, start=1):
        row["cluster_id"] = f"stopline-candidate-{index}"
    return rows, "approach_projected_lane_width_scaled_candidate_not_owner_bound"


def _circular_distance_deg(left: float, right: float) -> float:
    return abs((float(left) - float(right) + 180.0) % 360.0 - 180.0)


def _vehicle_approach_bearing_samples(
    vehicle_lanes: Sequence[Mapping[str, Any]],
) -> dict[str, list[float]]:
    bearings: dict[str, list[float]] = defaultdict(list)
    for lane in vehicle_lanes:
        approach_id = str(lane.get("ingress_approach") or lane.get("egress_approach") or "")
        raw_points = lane.get("points_m", ())
        if not approach_id or not isinstance(raw_points, Sequence) or len(raw_points) < 2:
            continue
        first = raw_points[0]
        last = raw_points[-1]
        if not isinstance(first, Sequence) or not isinstance(last, Sequence):
            continue
        # MAP lanes are encoded from the intersection boundary outwards.  The
        # radial vector therefore supplies a common arm bearing for ingress and
        # egress lanes without confusing travel direction with arm identity.
        dx = float(last[0]) - float(first[0])
        dy = float(last[1]) - float(first[1])
        magnitude = math.hypot(dx, dy)
        if magnitude > 1e-6:
            bearings[approach_id].append(
                round(math.degrees(math.atan2(dy, dx)) % 360.0, 6)
            )
    return {
        approach_id: sorted(values)
        for approach_id, values in sorted(
            bearings.items(),
            key=lambda item: _natural_key(item[0]),
        )
    }


def _circular_mean_deg(values: Sequence[float]) -> float:
    x = sum(math.cos(math.radians(value)) for value in values)
    y = sum(math.sin(math.radians(value)) for value in values)
    if not values or math.hypot(x, y) <= 1e-9:
        raise ValueError("bearing samples do not define a circular mean")
    return round(math.degrees(math.atan2(y, x)) % 360.0, 6)


def _validated_axis(
    raw_axis: Sequence[str],
    bearings: Mapping[str, float],
    *,
    evidence_id: str | None,
) -> tuple[tuple[str, ...], float | None, str]:
    if not raw_axis:
        return (), None, "unknown_pending_axis_validation"
    axis = _string_sequence(
        raw_axis,
        "validated_continuous_axis",
        allow_empty=False,
        drop_null=False,
    )
    if len(axis) != 2 or axis[0] == axis[1]:
        raise ValueError("validated_continuous_axis must contain two distinct arm IDs")
    if not str(evidence_id or "").strip():
        raise ValueError(
            "validated_continuous_axis_evidence_id is required with validated_continuous_axis"
        )
    missing = sorted(set(axis) - set(bearings), key=_natural_key)
    if missing:
        raise ValueError(f"validated continuous-axis arms have no bearing evidence: {missing}")
    ordered = tuple(sorted(axis, key=_natural_key))
    delta = _circular_distance_deg(bearings[ordered[0]], bearings[ordered[1]])
    return ordered, round(abs(180.0 - delta), 6), "reviewed_continuous_axis"


def _validated_components(
    raw_components: Sequence[Sequence[str]],
    requested: Sequence[str],
) -> list[list[str]]:
    if not raw_components:
        return []
    _sequence_value(raw_components, "reviewed_owner_components")
    components: list[list[str]] = []
    for index, component in enumerate(raw_components):
        values = _string_sequence(
            component,
            f"reviewed_owner_components[{index}]",
            allow_empty=True,
            drop_null=False,
        )
        components.append(sorted(set(values), key=_natural_key))
    components = [component for component in components if component]
    flat = [node_id for component in components for node_id in component]
    duplicates = sorted(
        {node_id for node_id in flat if flat.count(node_id) > 1},
        key=_natural_key,
    )
    if duplicates:
        raise ValueError(f"reviewed owner components overlap: {duplicates}")
    missing = sorted(set(requested) - set(flat), key=_natural_key)
    extra = sorted(set(flat) - set(requested), key=_natural_key)
    if missing or extra:
        raise ValueError(f"reviewed owner components do not close the cell: missing={missing}, extra={extra}")
    return sorted(
        components,
        key=lambda values: tuple(_natural_key(value) for value in values),
    )


def _geometry_id(geometry: ET.Element) -> str:
    identifier = _child(geometry, "id")
    return _mapem_node_id(geometry, identifier)


def _geometry_connection_pairs(geometry: ET.Element) -> set[tuple[str, str]]:
    lane_set = _child(geometry, "laneSet")
    if lane_set is None:
        return set()
    pairs: set[tuple[str, str]] = set()
    for lane in _children(lane_set, "GenericLane"):
        ingress_lane_id = _text(_child(lane, "laneID"))
        connects_to = _child(lane, "connectsTo")
        if not ingress_lane_id or connects_to is None:
            continue
        for connection in _children(connects_to, "Connection"):
            target = _text(_child(_child(connection, "connectingLane"), "lane"))
            if target:
                pairs.add((ingress_lane_id, target))
    return pairs


def _lane_type(lane_element: ET.Element) -> str:
    attributes = _child(lane_element, "laneAttributes")
    lane_type_parent = _child(attributes, "laneType")
    lane_type_element = next(iter(lane_type_parent), None) if lane_type_parent is not None else None
    return _local_name(lane_type_element.tag) if lane_type_element is not None else "unknown"


def _lane_points_and_markers(
    lane_element: ET.Element,
) -> tuple[list[tuple[float, float]], list[tuple[str, tuple[float, float]]]]:
    node_list = _child(lane_element, "nodeList")
    nodes = _child(node_list, "nodes")
    if nodes is None:
        return [], []
    x = 0.0
    y = 0.0
    points: list[tuple[float, float]] = []
    markers: list[tuple[str, tuple[float, float]]] = []
    for node in _children(nodes, "NodeXY"):
        delta = _child(node, "delta")
        coordinate = next(iter(delta), None) if delta is not None else None
        if coordinate is None:
            continue
        try:
            x += float(_text(_child(coordinate, "x"))) / 100.0
            y += float(_text(_child(coordinate, "y"))) / 100.0
        except ValueError:
            continue
        point = (x, y)
        points.append(point)
        attributes = _child(node, "attributes")
        local_node = _child(attributes, "localNode")
        if local_node is None:
            continue
        for marker in local_node:
            marker_name = _local_name(marker.tag)
            if marker_name in {"stopLine", "mergePoint"}:
                markers.append((marker_name, point))
    return points, markers


def _shape_length(raw_shape: str) -> float | None:
    points: list[tuple[float, float]] = []
    try:
        for token in raw_shape.split():
            x, y = token.split(",", maxsplit=1)
            points.append((float(x), float(y)))
    except ValueError:
        return None
    if len(points) < 2:
        return None
    return sum(
        math.hypot(right[0] - left[0], right[1] - left[1])
        for left, right in zip(points, points[1:])
    )


def _float_or_none(value: str | None) -> float | None:
    try:
        return float(value) if value not in {None, ""} else None
    except ValueError:
        return None


def _int_or_none(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _positive_int_or_none(value: Any) -> int | None:
    parsed = _int_or_none(value)
    return parsed if parsed is not None and parsed > 0 else None


def _nonnegative_int(value: Any, field_name: str) -> int:
    parsed = _int_or_none(value)
    if parsed is None or parsed < 0:
        raise ValueError(f"{field_name} must be a non-negative integer")
    return parsed


def _strict_bool(value: Any, field_name: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{field_name} must be a boolean")
    return value


def _require_enum(value: str, allowed: frozenset[str], field_name: str) -> str:
    if value not in allowed:
        raise ValueError(f"{field_name} must be one of {sorted(allowed)}; got {value!r}")
    return value


def _optional_enum(
    value: Any,
    allowed: frozenset[str],
    field_name: str,
) -> str | None:
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a string enum or null")
    return _require_enum(value, allowed, field_name)


def _sequence_value(value: Any, field_name: str) -> Sequence[Any]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise ValueError(f"{field_name} must be an array")
    return value


def _string_sequence(
    value: Any,
    field_name: str,
    *,
    allow_empty: bool,
    drop_null: bool,
) -> list[str]:
    sequence = _sequence_value(value, field_name)
    result: list[str] = []
    for item in sequence:
        if item is None and drop_null:
            continue
        if not isinstance(item, str):
            raise ValueError(f"{field_name} must contain only strings")
        text = item.strip()
        if not text:
            if allow_empty:
                continue
            raise ValueError(f"{field_name} must not contain empty strings")
        result.append(text)
    if not allow_empty and not result:
        raise ValueError(f"{field_name} must not be empty")
    return result


def _normalize_identifier(value: str) -> str:
    text = str(value).strip()
    try:
        return str(int(text))
    except ValueError:
        return text


def _natural_key(value: str) -> tuple[Any, ...]:
    text = str(value)
    return (0, int(text)) if text.isdigit() else (1, text)


def _stable_digest(payload: Any) -> str:
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
    ).hexdigest()


def _stable_evidence_digest(payload: Any) -> str:
    """Hash semantic evidence, excluding checkout-specific absolute paths and IDs."""

    def semantic(value: Any) -> Any:
        if isinstance(value, Mapping):
            return {
                str(key): semantic(item)
                for key, item in value.items()
                if str(key) not in {"source_file", "artifact_file", "evidence_id", "classification_id"}
            }
        if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
            return [semantic(item) for item in value]
        return value

    return _stable_digest(semantic(payload))


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _local_name(tag: str) -> str:
    return tag.rsplit("}", maxsplit=1)[-1]


def _child(parent: ET.Element | None, name: str) -> ET.Element | None:
    if parent is None:
        return None
    return next((child for child in parent if _local_name(child.tag) == name), None)


def _children(parent: ET.Element, name: str) -> list[ET.Element]:
    return [child for child in parent if _local_name(child.tag) == name]


def _descendants(parent: ET.Element, name: str) -> list[ET.Element]:
    return [element for element in parent.iter() if _local_name(element.tag) == name]


def _text(element: ET.Element | None) -> str:
    return "" if element is None or element.text is None else element.text.strip()
