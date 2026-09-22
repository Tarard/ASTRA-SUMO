"""Build lane-specific transition evidence from Hamburg official sources.

This module is the layer immediately below :mod:`official_lane_stitch`.  The
stitch planner proves that two MAP approaches belong to the same directed
HH-SIB road axis but deliberately abstains from lateral lane allocation.  This
module resolves only that remaining, much smaller question when overlapping
official KML lane centre-lines make the answer unique.

No OpenStreetMap, Google Maps, screenshot, or human decision is consumed.  A
passing result authorizes a lane-transition *graph*, not a SUMO network.  A
new downstream pocket starts with no upstream feed edge; vehicles may reach it
only through downstream SUMO lane changing after the model cut.  This compiler
policy is not a field claim about the physical taper or lane-change rules.
"""

from __future__ import annotations

import hashlib
import itertools
import json
import math
import re
import xml.etree.ElementTree as ET
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from pyproj import Transformer

from torii_sumo.core.artifact_io import write_json_atomic
from torii_sumo.core.candidate_contracts import file_sha256

from .official_lane_stitch import (
    HAMBURG_MAP_BINDING_SCHEMA,
    OFFICIAL_LANE_AXIS_STITCH_SCHEMA,
    lane_axis_stitch_identity_payload,
    plan_hamburg_official_map_lane_axis_stitch,
)


OFFICIAL_LANE_TRANSITION_GRAPH_SCHEMA = (
    "torii.hamburg-official-map-hh-sib-lane-transition-graph/v1"
)

_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_DIRECTIONS = frozenset({"with_stationing", "against_stationing"})


class OfficialLaneTransitionGraphError(ValueError):
    """Raised when hash or structural evidence cannot be verified."""


class _AutomaticAbstention(RuntimeError):
    def __init__(self, reason: str, details: Mapping[str, Any] | None = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.details = dict(details or {})


@dataclass(frozen=True)
class OfficialLaneTransitionThresholds:
    """Deterministic geometry gates for official lane continuation matching."""

    minimum_common_station_overlap_m: float = 5.0
    sample_fractions: tuple[float, ...] = (0.15, 0.30, 0.50, 0.70, 0.85)
    maximum_selected_pair_rms_distance_m: float = 0.75
    maximum_selected_pair_peak_distance_m: float = 1.25
    maximum_selected_pair_rms_heading_delta_deg: float = 10.0
    minimum_assignment_rms_margin_m: float = 0.75
    minimum_lane_index_lateral_separation_m: float = 0.05
    maximum_map_boundary_station_envelope_m: float = 0.50
    endpoint_identity_tolerance_m: float = 1.0
    station_monotonicity_tolerance_m: float = 0.25
    maximum_added_lane_count: int = 1

    def validated(self) -> OfficialLaneTransitionThresholds:
        positive = {
            "minimum_common_station_overlap_m": self.minimum_common_station_overlap_m,
            "maximum_selected_pair_rms_distance_m": self.maximum_selected_pair_rms_distance_m,
            "maximum_selected_pair_peak_distance_m": self.maximum_selected_pair_peak_distance_m,
            "maximum_selected_pair_rms_heading_delta_deg": (
                self.maximum_selected_pair_rms_heading_delta_deg
            ),
            "minimum_lane_index_lateral_separation_m": (
                self.minimum_lane_index_lateral_separation_m
            ),
            "maximum_map_boundary_station_envelope_m": (
                self.maximum_map_boundary_station_envelope_m
            ),
            "endpoint_identity_tolerance_m": self.endpoint_identity_tolerance_m,
            "station_monotonicity_tolerance_m": self.station_monotonicity_tolerance_m,
        }
        for name, value in positive.items():
            if not math.isfinite(value) or value <= 0:
                raise OfficialLaneTransitionGraphError(f"{name} must be finite and positive")
        if (
            not math.isfinite(self.minimum_assignment_rms_margin_m)
            or self.minimum_assignment_rms_margin_m < 0
        ):
            raise OfficialLaneTransitionGraphError(
                "minimum_assignment_rms_margin_m must be finite and non-negative"
            )
        if self.maximum_selected_pair_rms_heading_delta_deg > 180:
            raise OfficialLaneTransitionGraphError(
                "maximum_selected_pair_rms_heading_delta_deg cannot exceed 180"
            )
        if self.maximum_added_lane_count < 0:
            raise OfficialLaneTransitionGraphError(
                "maximum_added_lane_count must be non-negative"
            )
        if len(self.sample_fractions) < 3:
            raise OfficialLaneTransitionGraphError("at least three sample_fractions are required")
        if any(
            not math.isfinite(value) or not 0 < value < 1
            for value in self.sample_fractions
        ):
            raise OfficialLaneTransitionGraphError(
                "sample_fractions must be finite and strictly between zero and one"
            )
        if tuple(sorted(set(self.sample_fractions))) != self.sample_fractions:
            raise OfficialLaneTransitionGraphError(
                "sample_fractions must be unique and strictly increasing"
            )
        return self


@dataclass(frozen=True)
class _AxisEdge:
    edge_id: str
    official_link_id: str
    station_direction: str
    station_from_m: float
    station_to_m: float
    num_lanes: int
    shape: tuple[tuple[float, float], ...]


@dataclass(frozen=True)
class _PreparedLane:
    node_id: str
    lane_id: str
    role: str
    approach_id: str
    traffic_shape: tuple[tuple[float, float], ...]
    stations_m: tuple[float, ...]
    endpoint_a_xy: tuple[float, float]
    endpoint_b_xy: tuple[float, float]
    endpoint_a_station_m: float
    endpoint_b_station_m: float


def build_hamburg_official_lane_transition_graph(
    *,
    map_binding_reports: Sequence[Mapping[str, Any] | str | Path],
    lane_axis_stitch_plan: Mapping[str, Any] | str | Path,
    nodes_file: str | Path,
    edges_file: str | Path,
    plainxml_manifest_file: str | Path,
    thresholds: OfficialLaneTransitionThresholds | None = None,
    output_file: str | Path | None = None,
) -> dict[str, Any]:
    """Return a hash-bound official lane transition graph.

    The supplied stitch plan is recomputed from the same inputs and must match
    byte-for-byte at the JSON value level.  This prevents a stale or edited
    approach plan from authorizing a lane transition.
    """

    limits = (thresholds or OfficialLaneTransitionThresholds()).validated()
    node_path = Path(nodes_file).expanduser().resolve()
    edge_path = Path(edges_file).expanduser().resolve()
    manifest_path = Path(plainxml_manifest_file).expanduser().resolve()

    loaded_reports = [_load_json_like(value, "MAP binding report") for value in map_binding_reports]
    if not loaded_reports:
        raise OfficialLaneTransitionGraphError("map_binding_reports must not be empty")
    loaded_reports.sort(key=lambda item: _identifier_sort_key(str(item[0].get("node_id", ""))))
    reports = [item[0] for item in loaded_reports]
    report_identities = [item[1] for item in loaded_reports]
    _validate_reports(reports)

    supplied_plan, supplied_plan_identity = _load_json_like(
        lane_axis_stitch_plan, "lane-axis stitch plan"
    )
    supplied_plan_identity["canonical_sha256"] = _stable_digest(
        lane_axis_stitch_identity_payload(supplied_plan)
    )
    recomputed_plan = plan_hamburg_official_map_lane_axis_stitch(
        map_binding_reports=[
            identity["path"] if identity.get("identity_method") == "file_bytes_sha256" else report
            for report, identity in loaded_reports
        ],
        nodes_file=node_path,
        edges_file=edge_path,
        plainxml_manifest_file=manifest_path,
    )
    if lane_axis_stitch_identity_payload(
        supplied_plan
    ) != lane_axis_stitch_identity_payload(recomputed_plan):
        raise OfficialLaneTransitionGraphError(
            "lane-axis stitch plan does not exactly match the recomputed official-input plan"
        )
    if supplied_plan.get("schema") != OFFICIAL_LANE_AXIS_STITCH_SCHEMA:
        raise OfficialLaneTransitionGraphError("unexpected lane-axis stitch plan schema")

    manifest, manifest_identity = _load_json_like(manifest_path, "PlainXML manifest")
    projection = _projection(manifest)
    transformer = Transformer.from_crs(
        projection["source_crs"], projection["crs"], always_xy=True
    )
    axes = _parse_axes(edge_path)
    report_by_node = {str(report["node_id"]): report for report in reports}

    transitions = [
        _build_direction_transition(
            candidate,
            plan=recomputed_plan,
            report_by_node=report_by_node,
            axes=axes,
            transformer=transformer,
            thresholds=limits,
        )
        for candidate in recomputed_plan.get("stitch_candidates", [])
    ]
    transitions.sort(key=lambda item: item["transition_id"])
    passing = sum(item["status"] == "pass" for item in transitions)
    overall_status = "pass" if transitions and passing == len(transitions) else "review_required"

    input_identity = {
        "map_binding_reports": report_identities,
        "lane_axis_stitch_plan": supplied_plan_identity,
        "lane_axis_stitch_plan_id": str(recomputed_plan["plan_id"]),
        "plainxml_manifest": manifest_identity,
        "nodes": _file_identity(node_path),
        "edges": _file_identity(edge_path),
        "hh_sib_source_sha256": str(recomputed_plan["inputs"]["hh_sib_source_sha256"]),
        "plainxml_candidate_id": str(recomputed_plan["inputs"]["plainxml_candidate_id"]),
    }
    graph: dict[str, Any] = {
        "schema": OFFICIAL_LANE_TRANSITION_GRAPH_SCHEMA,
        "status": overall_status,
        "decision": (
            "authorize_lane_specific_transition_graph"
            if overall_status == "pass"
            else "automatic_abstention_for_unresolved_directions"
        ),
        "human_action_required": False,
        "network_materialization_performed": False,
        "inputs": input_identity,
        "projection": projection,
        "thresholds": _threshold_payload(limits),
        "source_policy": {
            "authoritative_runtime_sources": [
                "Hamburg_MAP_KML_MAPEM_binding",
                "Hamburg_HH_SIB_PlainXML",
            ],
            "excluded_sources": [
                "OpenStreetMap",
                "Google_Maps",
                "manual_map_review",
                "screenshot_interpretation",
            ],
        },
        "transitions": transitions,
        "counts": {
            "direction_count": len(transitions),
            "authorized_direction_count": passing,
            "abstained_direction_count": len(transitions) - passing,
            "continuation_edge_count": sum(
                len(item.get("continuation_edges", []))
                for item in transitions
                if item["status"] == "pass"
            ),
            "continuation_identity_count": sum(
                len(item.get("continuation_identity_evidence", []))
                for item in transitions
            ),
            "empty_start_added_lane_count": sum(
                len(item.get("added_lanes", []))
                for item in transitions
                if item["status"] == "pass"
            ),
            "unresolved_added_lane_count": sum(
                len(item.get("unresolved_added_lanes", []))
                for item in transitions
            ),
        },
        "gates": {
            "official_input_hashes": "pass",
            "exact_recomputed_stitch_plan": "pass",
            "all_directional_transition_graphs": overall_status,
            "automatic_network_materialization": "blocked",
        },
        "claim_boundary": (
            "A passing direction proves a unique, order-preserving continuation between overlapping "
            "official MAP lane centre-lines on one official HH-SIB directional axis. It authorizes only "
            "the listed continuation edges and an empty-start downstream pocket. It does not prove a "
            "physical taper location, lane width, field lane-change permission, signal timing, or a "
            "complete SUMO network. No upstream connection to an added lane may be invented."
        ),
    }
    graph["graph_id"] = "official-lane-transition-" + _stable_digest(
        {
            "schema": OFFICIAL_LANE_TRANSITION_GRAPH_SCHEMA,
            "input_hashes": _promotion_input_hashes(input_identity),
            "thresholds": graph["thresholds"],
            "transitions": transitions,
        }
    )[:24]

    if output_file is not None:
        destination = Path(output_file).expanduser().resolve()
        source_paths = {
            node_path,
            edge_path,
            manifest_path,
            *(
                Path(str(item["path"])).expanduser().resolve()
                for item in report_identities
                if item.get("path")
            ),
            *(
                [Path(str(supplied_plan_identity["path"])).expanduser().resolve()]
                if supplied_plan_identity.get("path")
                else []
            ),
        }
        if destination in source_paths:
            raise OfficialLaneTransitionGraphError("output_file must not overwrite an input artifact")
        write_json_atomic(destination, graph, sort_keys=True)
    return graph


def _build_direction_transition(
    candidate: Mapping[str, Any],
    *,
    plan: Mapping[str, Any],
    report_by_node: Mapping[str, Mapping[str, Any]],
    axes: Mapping[str, tuple[_AxisEdge, ...]],
    transformer: Transformer,
    thresholds: OfficialLaneTransitionThresholds,
) -> dict[str, Any]:
    upstream = candidate.get("upstream_egress")
    downstream = candidate.get("downstream_ingress")
    if not isinstance(upstream, Mapping) or not isinstance(downstream, Mapping):
        raise OfficialLaneTransitionGraphError("stitch candidate lacks approach endpoints")
    corridor_id = str(candidate.get("corridor_id", ""))
    base = {
        "transition_id": "lane-transition-"
        + _stable_digest(
            {
                "candidate_id": candidate.get("candidate_id"),
                "upstream": [upstream.get("node_id"), upstream.get("approach_id")],
                "downstream": [downstream.get("node_id"), downstream.get("approach_id")],
                "corridor_id": corridor_id,
            }
        )[:16],
        "approach_stitch_candidate_id": str(candidate.get("candidate_id", "")),
        "corridor_id": corridor_id,
        "official_link_id": str(candidate.get("official_link_id", "")),
        "station_direction": str(candidate.get("station_direction", "")),
        "upstream_egress": dict(upstream),
        "downstream_ingress": dict(downstream),
        "human_action_required": False,
        "network_materialization_performed": False,
    }
    try:
        if candidate.get("envelope_relation") != "overlap":
            raise _AutomaticAbstention("official_MAP_lane_envelopes_do_not_overlap")
        axis = axes.get(corridor_id)
        if not axis:
            raise OfficialLaneTransitionGraphError(
                f"no PlainXML axis edges found for corridor {corridor_id!r}"
            )
        source_rows = _approach_lane_rows(
            plan,
            node_id=str(upstream["node_id"]),
            approach_id=str(upstream["approach_id"]),
            role="egress",
        )
        target_rows = _approach_lane_rows(
            plan,
            node_id=str(downstream["node_id"]),
            approach_id=str(downstream["approach_id"]),
            role="ingress",
        )
        if len(source_rows) != int(upstream["vehicle_lane_count"]):
            raise OfficialLaneTransitionGraphError("upstream approach lane count disagrees with plan")
        if len(target_rows) != int(downstream["vehicle_lane_count"]):
            raise OfficialLaneTransitionGraphError("downstream approach lane count disagrees with plan")
        if any(row.get("status") != "pass" for row in [*source_rows, *target_rows]):
            raise _AutomaticAbstention("approach_contains_unmatched_official_lane")

        source_lanes = _prepare_group(
            source_rows,
            report=report_by_node[str(upstream["node_id"])],
            axis=axis,
            transformer=transformer,
            thresholds=thresholds,
        )
        target_lanes = _prepare_group(
            target_rows,
            report=report_by_node[str(downstream["node_id"])],
            axis=axis,
            transformer=transformer,
            thresholds=thresholds,
        )
        source_order = _rank_right_to_left(source_lanes, thresholds=thresholds)
        target_order = _rank_right_to_left(target_lanes, thresholds=thresholds)
        if len(target_order) < len(source_order):
            raise _AutomaticAbstention(
                "lane_drop_is_outside_empty_start_added_lane_contract",
                {"upstream_lane_count": len(source_order), "downstream_lane_count": len(target_order)},
            )
        added_count = len(target_order) - len(source_order)
        if added_count > thresholds.maximum_added_lane_count:
            raise _AutomaticAbstention(
                "added_lane_count_exceeds_automatic_contract",
                {"added_lane_count": added_count},
            )

        overlap_low = max(
            min(lane.stations_m) for lane in [*source_order, *target_order]
        )
        overlap_high = min(
            max(lane.stations_m) for lane in [*source_order, *target_order]
        )
        overlap_length = overlap_high - overlap_low
        if overlap_length < thresholds.minimum_common_station_overlap_m:
            raise _AutomaticAbstention(
                "insufficient_all_lane_station_overlap",
                {
                    "overlap_interval_m": [_rounded(overlap_low), _rounded(overlap_high)],
                    "overlap_length_m": _rounded(overlap_length),
                },
            )
        sample_stations = [
            overlap_low + fraction * overlap_length
            for fraction in thresholds.sample_fractions
        ]
        assignments = _assignment_candidates(
            source_order,
            target_order,
            sample_stations=sample_stations,
        )
        if not assignments:
            raise _AutomaticAbstention("no_order_preserving_assignment_candidate")
        selected = assignments[0]
        score_margin = (
            None
            if len(assignments) == 1
            else float(assignments[1]["rms_centerline_distance_m"])
            - float(selected["rms_centerline_distance_m"])
        )
        if (
            score_margin is not None
            and score_margin < thresholds.minimum_assignment_rms_margin_m
        ):
            raise _AutomaticAbstention(
                "ambiguous_assignment_score_margin",
                {
                    "score_margin_m": _rounded(score_margin),
                    "assignment_candidates": assignments,
                },
            )
        if any(
            float(pair["rms_centerline_distance_m"])
            > thresholds.maximum_selected_pair_rms_distance_m
            for pair in selected["pairs"]
        ):
            raise _AutomaticAbstention(
                "selected_continuation_rms_distance_exceeds_threshold",
                {"selected_assignment": selected},
            )
        if any(
            float(pair["peak_centerline_distance_m"])
            > thresholds.maximum_selected_pair_peak_distance_m
            for pair in selected["pairs"]
        ):
            raise _AutomaticAbstention(
                "selected_continuation_peak_distance_exceeds_threshold",
                {"selected_assignment": selected},
            )
        if any(
            float(pair["rms_heading_delta_deg"])
            > thresholds.maximum_selected_pair_rms_heading_delta_deg
            for pair in selected["pairs"]
        ):
            raise _AutomaticAbstention(
                "selected_continuation_heading_delta_exceeds_threshold",
                {"selected_assignment": selected},
            )

        selected_target_indices = {int(pair["downstream_sumo_lane_index"]) for pair in selected["pairs"]}
        unmatched = [
            (index, lane)
            for index, lane in enumerate(target_order)
            if index not in selected_target_indices
        ]
        added_side = _added_lane_side(
            [index for index, _lane in unmatched], selected_target_indices
        )
        if unmatched and added_side not in {"right", "left"}:
            raise _AutomaticAbstention(
                "added_lane_is_not_on_an_external_side",
                {"unmatched_downstream_lane_indices": [index for index, _lane in unmatched]},
            )

        target_report = report_by_node[str(downstream["node_id"])]
        added_lanes = [
            _added_lane_record(index, lane, side=added_side, report=target_report)
            for index, lane in unmatched
        ]
        if any(not item["official_downstream_movements"] for item in added_lanes):
            raise _AutomaticAbstention(
                "added_lane_has_no_official_downstream_MAP_movement",
                {"added_lanes": added_lanes},
            )

        boundary_stations = [lane.endpoint_a_station_m for lane in target_order]
        boundary_envelope = [min(boundary_stations), max(boundary_stations)]
        if boundary_envelope[1] - boundary_envelope[0] > (
            thresholds.maximum_map_boundary_station_envelope_m
        ):
            raise _AutomaticAbstention(
                "downstream_MAP_boundary_station_envelope_is_too_wide",
                {"boundary_station_envelope_m": [_rounded(value) for value in boundary_envelope]},
            )

        evidence_station = overlap_low + 0.5 * overlap_length
        profile_cut = candidate.get("selected_cut")
        evidence_axis = _axis_point_at_station(axis, evidence_station)
        cross_sections = [
            _cross_section(
                axis,
                station,
                source_order=source_order,
                target_order=target_order,
            )
            for station in sample_stations
        ]
        continuation_evidence = [
            {
                "upstream_official_lane_id": pair["upstream_official_lane_id"],
                "upstream_sumo_lane_index": pair["upstream_sumo_lane_index"],
                "downstream_official_lane_id": pair["downstream_official_lane_id"],
                "downstream_sumo_lane_index": pair["downstream_sumo_lane_index"],
                "rms_centerline_distance_m": pair["rms_centerline_distance_m"],
                "peak_centerline_distance_m": pair["peak_centerline_distance_m"],
                "rms_heading_delta_deg": pair["rms_heading_delta_deg"],
            }
            for pair in selected["pairs"]
        ]
        common_evidence = {
            "upstream_lane_order_right_to_left": _lane_order_payload(source_order),
            "downstream_lane_order_right_to_left": _lane_order_payload(target_order),
            "overlap_evidence": {
                "all_lane_common_station_interval_m": [
                    _rounded(overlap_low),
                    _rounded(overlap_high),
                ],
                "all_lane_common_station_length_m": _rounded(overlap_length),
                "sample_stations_m": [_rounded(value) for value in sample_stations],
                "cross_sections": cross_sections,
                "matching_cross_section": {
                    "station_m": _rounded(evidence_station),
                    "axis_xy": [_rounded(value) for value in evidence_axis["point_xy"]],
                    "traffic_heading_deg": _rounded(evidence_axis["heading_deg"]),
                    "basis": "midpoint_of_all_lane_common_official_KML_station_overlap",
                },
            },
            "assignment_candidates": assignments,
            "selected_assignment_score_margin_m": _rounded(score_margin),
            "continuation_identity_evidence": continuation_evidence,
        }

        # MAP approach boundaries delimit the published MAP geometry; they are
        # not evidence of the physical lane-birth/taper station.  An added lane
        # therefore needs the independent HH-SIB lane-profile transition before
        # this graph may authorize a materializable cut.
        if unmatched and not isinstance(profile_cut, Mapping):
            unresolved_added = [
                {
                    **item,
                    "initial_vehicle_state": None,
                    "entry_after_cut": None,
                    "origin_cut_status": "review_required",
                }
                for item in added_lanes
            ]
            return {
                **base,
                "status": "review_required",
                "decision": "automatic_abstention_missing_official_lane_birth_cut",
                "reason": "no_HH_SIB_lane_profile_transition_for_MAP_lane_count_increase",
                "lane_transition_authorized": False,
                "full_network_materialization_authorized": False,
                "continuation_identity_status": "pass",
                **common_evidence,
                "continuation_edges": [],
                "unresolved_added_lanes": unresolved_added,
                "added_lanes": [],
                "cut_evidence": {
                    "authorized_model_cut_station_m": None,
                    "cut_basis": None,
                    "downstream_MAP_endpoint_A_station_envelope_m": [
                        _rounded(value) for value in boundary_envelope
                    ],
                    "HH_SIB_profile_cut": None,
                    "MAP_endpoint_A_used_as_physical_taper": False,
                    "physical_taper_location_claimed": False,
                },
                "compiler_directive": None,
                "gates": {
                    "same_official_directional_axis": "pass",
                    "all_lane_common_KML_overlap": "pass",
                    "SUMO_right_to_left_lane_order": "pass",
                    "unique_order_preserving_continuation_identity": "pass",
                    "added_lane_external_side": "pass",
                    "added_lane_official_MAP_movement": "pass",
                    "independent_official_lane_birth_cut": "review_required",
                    "lane_specific_transition": "review_required",
                    "complete_network_materialization": "blocked",
                },
                "claim_boundary": (
                    "The continuation identity is unique, but the added lane's origin is not. "
                    "The MAP A boundary is not treated as a physical taper, so no transition edge, "
                    "empty-start lane, or compiler directive is authorized for this direction."
                ),
            }

        if isinstance(profile_cut, Mapping):
            cut_station = float(profile_cut["station_m"])
            cut_basis = "exact_HH_SIB_lane_profile_transition"
        else:
            # Equal lane-count continuations need no lane-birth inference.  The
            # matching cross-section is a sufficient structural cut anchor.
            cut_station = evidence_station
            cut_basis = "official_KML_overlap_matching_cross_section"
        cut_axis = _axis_point_at_station(axis, cut_station)
        continuation_edges = [
            {**item, "authorization": "exact_official_overlap_continuation_only"}
            for item in continuation_evidence
        ]
        return {
            **base,
            "status": "pass",
            "decision": "authorize_lane_specific_transition_only",
            "reason": "unique_order_preserving_official_KML_overlap_assignment",
            "lane_transition_authorized": True,
            "full_network_materialization_authorized": False,
            "continuation_identity_status": "pass",
            **common_evidence,
            "continuation_edges": continuation_edges,
            "added_lanes": added_lanes,
            "cut_evidence": {
                "authorized_model_cut_station_m": _rounded(cut_station),
                "cut_basis": cut_basis,
                "axis_xy": [_rounded(value) for value in cut_axis["point_xy"]],
                "traffic_heading_deg": _rounded(cut_axis["heading_deg"]),
                "downstream_MAP_endpoint_A_station_envelope_m": [
                    _rounded(value) for value in boundary_envelope
                ],
                "HH_SIB_profile_cut": dict(profile_cut) if isinstance(profile_cut, Mapping) else None,
                "cut_inside_matching_overlap": overlap_low <= cut_station <= overlap_high,
                "physical_taper_location_claimed": False,
            },
            "compiler_directive": {
                "write_only_continuation_edges": continuation_edges,
                "added_lane_initial_vehicle_state": "empty_at_model_cut",
                "added_lane_upstream_feed_connections": [],
                "downstream_lane_change_policy": {
                    "policy": "allow_SUMO_lane_changing_after_model_cut",
                    "status": "structural_compiler_default",
                    "field_permission_claimed": False,
                },
                "forbidden": [
                    "invent_upstream_connection_to_added_lane",
                    "infer_physical_taper_location_from_rendering",
                    "materialize_complete_network_from_this_graph_alone",
                ],
            },
            "gates": {
                "same_official_directional_axis": "pass",
                "all_lane_common_KML_overlap": "pass",
                "SUMO_right_to_left_lane_order": "pass",
                "unique_order_preserving_assignment": "pass",
                "added_lane_external_side": "pass" if unmatched else "not_applicable",
                "added_lane_official_MAP_movement": "pass" if unmatched else "not_applicable",
                "model_cut_station": "pass",
                "complete_network_materialization": "blocked",
            },
            "claim_boundary": (
                "Only the listed lane continuation edges and empty-start added lane are authorized. "
                "The cut is a model boundary; its physical taper position and field lane-change rules "
                "are not identified."
            ),
        }
    except _AutomaticAbstention as exc:
        return {
            **base,
            "status": "review_required",
            "decision": "automatic_abstention_no_lane_transition",
            "reason": exc.reason,
            "details": exc.details,
            "lane_transition_authorized": False,
            "full_network_materialization_authorized": False,
            "continuation_edges": [],
            "added_lanes": [],
            "gates": {"lane_specific_transition": "review_required"},
            "claim_boundary": (
                "No lane transition is authorized because the official evidence did not identify a "
                "unique continuation without adding an unsupported connection."
            ),
        }


def _prepare_group(
    plan_rows: Sequence[Mapping[str, Any]],
    *,
    report: Mapping[str, Any],
    axis: Sequence[_AxisEdge],
    transformer: Transformer,
    thresholds: OfficialLaneTransitionThresholds,
) -> list[_PreparedLane]:
    raw_by_id: dict[str, Mapping[str, Any]] = {}
    for raw in report["lanes"]:
        if not isinstance(raw, Mapping):
            continue
        lane_id = str(raw.get("lane_id", ""))
        if lane_id in raw_by_id:
            raise OfficialLaneTransitionGraphError(
                f"MAP binding report repeats lane_id {lane_id!r}"
            )
        raw_by_id[lane_id] = raw
    result: list[_PreparedLane] = []
    for row in plan_rows:
        lane_id = str(row["lane_id"])
        raw = raw_by_id.get(lane_id)
        if raw is None:
            raise OfficialLaneTransitionGraphError(
                f"stitch-plan lane {lane_id!r} is absent from its MAP binding report"
            )
        result.append(
            _prepare_lane(
                raw,
                node_id=str(row["node_id"]),
                approach_id=str(row["approach_id"]),
                role=str(row["map_role"]),
                axis=axis,
                transformer=transformer,
                thresholds=thresholds,
            )
        )
    return result


def _prepare_lane(
    raw: Mapping[str, Any],
    *,
    node_id: str,
    approach_id: str,
    role: str,
    axis: Sequence[_AxisEdge],
    transformer: Transformer,
    thresholds: OfficialLaneTransitionThresholds,
) -> _PreparedLane:
    lane_id = str(raw.get("lane_id", ""))
    if str(raw.get("lane_type")) != "vehicle" or str(raw.get("kml_direction_role")) != role:
        raise OfficialLaneTransitionGraphError(
            f"MAP lane {node_id}/{lane_id} semantic role disagrees with stitch plan"
        )
    coordinates = tuple(_project_coordinate(value, transformer) for value in raw.get("coordinates", []))
    if len(coordinates) < 2:
        raise OfficialLaneTransitionGraphError(f"MAP lane {node_id}/{lane_id} is degenerate")
    endpoint_a = _project_coordinate(raw.get("endpoint_a"), transformer)
    endpoint_b = _project_coordinate(raw.get("endpoint_b"), transformer)
    direct_error = _distance(coordinates[0], endpoint_a) + _distance(coordinates[-1], endpoint_b)
    reverse_error = _distance(coordinates[0], endpoint_b) + _distance(coordinates[-1], endpoint_a)
    if min(direct_error, reverse_error) > 2 * thresholds.endpoint_identity_tolerance_m:
        raise OfficialLaneTransitionGraphError(
            f"MAP lane {node_id}/{lane_id} terminals do not match official A/B endpoints"
        )
    if abs(direct_error - reverse_error) <= 1e-9:
        raise OfficialLaneTransitionGraphError(
            f"MAP lane {node_id}/{lane_id} A/B orientation is ambiguous"
        )
    a_to_b = coordinates if direct_error < reverse_error else tuple(reversed(coordinates))
    traffic_shape = a_to_b if role == "ingress" else tuple(reversed(a_to_b))
    stations = tuple(_project_point_to_axis(point, axis)["station_m"] for point in traffic_shape)
    oriented = stations if axis[0].station_direction == "with_stationing" else tuple(-v for v in stations)
    if any(
        right < left - thresholds.station_monotonicity_tolerance_m
        for left, right in zip(oriented, oriented[1:])
    ):
        raise _AutomaticAbstention(
            "official_lane_geometry_is_not_monotone_on_directed_axis",
            {"node_id": node_id, "lane_id": lane_id, "stations_m": list(stations)},
        )
    endpoint_a_station = _project_point_to_axis(endpoint_a, axis)["station_m"]
    endpoint_b_station = _project_point_to_axis(endpoint_b, axis)["station_m"]
    return _PreparedLane(
        node_id=node_id,
        lane_id=lane_id,
        role=role,
        approach_id=approach_id,
        traffic_shape=traffic_shape,
        stations_m=stations,
        endpoint_a_xy=endpoint_a,
        endpoint_b_xy=endpoint_b,
        endpoint_a_station_m=endpoint_a_station,
        endpoint_b_station_m=endpoint_b_station,
    )


def _rank_right_to_left(
    lanes: Sequence[_PreparedLane],
    *,
    thresholds: OfficialLaneTransitionThresholds,
) -> list[_PreparedLane]:
    if not lanes:
        raise OfficialLaneTransitionGraphError("lane group is empty")
    tangents: list[tuple[float, float]] = []
    for lane in lanes:
        if lane.role == "ingress":
            start, end = lane.traffic_shape[-2], lane.traffic_shape[-1]
        else:
            start, end = lane.traffic_shape[0], lane.traffic_shape[1]
        length = _distance(start, end)
        if length <= 1e-9:
            raise _AutomaticAbstention(
                "lane_group_has_degenerate_junction_B_tangent",
                {"lane_id": lane.lane_id},
            )
        tangents.append(((end[0] - start[0]) / length, (end[1] - start[1]) / length))
    mean_x = sum(value[0] for value in tangents)
    mean_y = sum(value[1] for value in tangents)
    mean_length = math.hypot(mean_x, mean_y)
    if mean_length / len(tangents) < 0.5:
        raise _AutomaticAbstention("lane_group_has_contradictory_junction_B_headings")
    forward = (mean_x / mean_length, mean_y / mean_length)
    left_normal = (-forward[1], forward[0])
    ordered = sorted(
        lanes,
        key=lambda lane: (
            lane.endpoint_b_xy[0] * left_normal[0]
            + lane.endpoint_b_xy[1] * left_normal[1],
            _identifier_sort_key(lane.lane_id),
        ),
    )
    lateral = [
        lane.endpoint_b_xy[0] * left_normal[0] + lane.endpoint_b_xy[1] * left_normal[1]
        for lane in ordered
    ]
    if any(
        right - left < thresholds.minimum_lane_index_lateral_separation_m
        for left, right in zip(lateral, lateral[1:])
    ):
        raise _AutomaticAbstention(
            "SUMO_right_to_left_lane_index_order_is_geometrically_ambiguous",
            {"lateral_projections_m": [_rounded(value) for value in lateral]},
        )
    return ordered


def _assignment_candidates(
    source_order: Sequence[_PreparedLane],
    target_order: Sequence[_PreparedLane],
    *,
    sample_stations: Sequence[float],
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for selected_indices in itertools.combinations(range(len(target_order)), len(source_order)):
        pairs: list[dict[str, Any]] = []
        assignment_squared: list[float] = []
        for source_index, target_index in enumerate(selected_indices):
            source = source_order[source_index]
            target = target_order[target_index]
            distances: list[float] = []
            heading_deltas: list[float] = []
            for station in sample_stations:
                source_point, source_heading = _lane_at_station(source, station)
                target_point, target_heading = _lane_at_station(target, station)
                distance = _distance(source_point, target_point)
                distances.append(distance)
                heading_deltas.append(_heading_delta(source_heading, target_heading))
                assignment_squared.append(distance**2)
            pairs.append(
                {
                    "upstream_official_lane_id": source.lane_id,
                    "upstream_sumo_lane_index": source_index,
                    "downstream_official_lane_id": target.lane_id,
                    "downstream_sumo_lane_index": target_index,
                    "rms_centerline_distance_m": _rounded(_rms(distances)),
                    "peak_centerline_distance_m": _rounded(max(distances)),
                    "rms_heading_delta_deg": _rounded(_rms(heading_deltas)),
                    "sample_centerline_distances_m": [_rounded(value) for value in distances],
                }
            )
        results.append(
            {
                "downstream_index_subset": list(selected_indices),
                "pairs": pairs,
                "rms_centerline_distance_m": _rounded(math.sqrt(sum(assignment_squared) / len(assignment_squared))),
            }
        )
    return sorted(
        results,
        key=lambda item: (
            item["rms_centerline_distance_m"],
            tuple(item["downstream_index_subset"]),
        ),
    )


def _cross_section(
    axis: Sequence[_AxisEdge],
    station: float,
    *,
    source_order: Sequence[_PreparedLane],
    target_order: Sequence[_PreparedLane],
) -> dict[str, Any]:
    axis_record = _axis_point_at_station(axis, station)
    tangent = axis_record["unit_tangent"]
    left_normal = (-tangent[1], tangent[0])

    def rows(lanes: Sequence[_PreparedLane]) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for index, lane in enumerate(lanes):
            point, heading = _lane_at_station(lane, station)
            offset = (
                (point[0] - axis_record["point_xy"][0]) * left_normal[0]
                + (point[1] - axis_record["point_xy"][1]) * left_normal[1]
            )
            result.append(
                {
                    "official_lane_id": lane.lane_id,
                    "sumo_lane_index": index,
                    "point_xy": [_rounded(value) for value in point],
                    "signed_lateral_offset_m": _rounded(offset),
                    "traffic_heading_deg": _rounded(heading),
                }
            )
        return result

    return {
        "station_m": _rounded(station),
        "axis_xy": [_rounded(value) for value in axis_record["point_xy"]],
        "axis_traffic_heading_deg": _rounded(axis_record["heading_deg"]),
        "upstream_lanes": rows(source_order),
        "downstream_lanes": rows(target_order),
    }


def _lane_at_station(
    lane: _PreparedLane, station: float
) -> tuple[tuple[float, float], float]:
    hits: list[tuple[tuple[float, float], float]] = []
    for first_station, second_station, first, second in zip(
        lane.stations_m,
        lane.stations_m[1:],
        lane.traffic_shape,
        lane.traffic_shape[1:],
    ):
        if abs(second_station - first_station) <= 1e-9:
            continue
        if not min(first_station, second_station) - 1e-7 <= station <= max(
            first_station, second_station
        ) + 1e-7:
            continue
        fraction = (station - first_station) / (second_station - first_station)
        point = (
            first[0] + fraction * (second[0] - first[0]),
            first[1] + fraction * (second[1] - first[1]),
        )
        heading = math.degrees(math.atan2(second[1] - first[1], second[0] - first[0])) % 360
        hits.append((point, heading))
    if not hits:
        raise _AutomaticAbstention(
            "lane_does_not_intersect_matching_station_cross_section",
            {"lane_id": lane.lane_id, "station_m": station},
        )
    first_point, first_heading = hits[0]
    if any(
        _distance(first_point, point) > 0.05 or _heading_delta(first_heading, heading) > 5.0
        for point, heading in hits[1:]
    ):
        raise _AutomaticAbstention(
            "lane_has_multiple_ambiguous_crossings_at_station",
            {"lane_id": lane.lane_id, "station_m": station},
        )
    return first_point, first_heading


def _added_lane_record(
    index: int,
    lane: _PreparedLane,
    *,
    side: str,
    report: Mapping[str, Any],
) -> dict[str, Any]:
    movements = []
    for connection in report.get("connections", []):
        if not isinstance(connection, Mapping) or str(connection.get("ingress_lane_id")) != lane.lane_id:
            continue
        movements.append(
            {
                "connection_id": str(connection.get("connection_id", "")),
                "egress_lane_id": str(connection.get("egress_lane_id", "")),
                "signal_group": (
                    None
                    if connection.get("signal_group") in {None, ""}
                    else str(connection.get("signal_group"))
                ),
                "maneuver_bits": (
                    None
                    if connection.get("maneuver_bits") in {None, ""}
                    else str(connection.get("maneuver_bits"))
                ),
            }
        )
    movements.sort(
        key=lambda item: (
            _identifier_sort_key(item["connection_id"]),
            _identifier_sort_key(item["egress_lane_id"]),
        )
    )
    return {
        "downstream_official_lane_id": lane.lane_id,
        "downstream_sumo_lane_index": index,
        "added_side": side,
        "initial_vehicle_state": "empty_at_model_cut",
        "upstream_feed_connections": [],
        "official_downstream_movements": movements,
        "pocket_semantics": (
            "dedicated_single_official_MAP_movement_pocket"
            if len(movements) == 1
            else "official_MAP_multi_movement_approach_lane"
        ),
        "entry_after_cut": "downstream_lane_change_only",
        "invented_connection_forbidden": True,
    }


def _added_lane_side(added: Sequence[int], selected: set[int]) -> str:
    if not added:
        return "not_applicable"
    if not selected:
        return "undetermined"
    if max(added) < min(selected):
        return "right"
    if min(added) > max(selected):
        return "left"
    return "internal"


def _approach_lane_rows(
    plan: Mapping[str, Any],
    *,
    node_id: str,
    approach_id: str,
    role: str,
) -> list[Mapping[str, Any]]:
    rows = [
        item
        for item in plan.get("lanes", [])
        if isinstance(item, Mapping)
        and str(item.get("node_id")) == node_id
        and str(item.get("approach_id")) == approach_id
        and str(item.get("map_role")) == role
    ]
    return sorted(rows, key=lambda item: _identifier_sort_key(str(item.get("lane_id", ""))))


def _parse_axes(path: Path) -> dict[str, tuple[_AxisEdge, ...]]:
    try:
        root = ET.parse(path).getroot()
    except (OSError, ET.ParseError) as exc:
        raise OfficialLaneTransitionGraphError(f"invalid PlainXML edges file: {path}") from exc
    if root.tag != "edges":
        raise OfficialLaneTransitionGraphError("PlainXML edges root must be edges")
    grouped: dict[str, list[_AxisEdge]] = {}
    for element in root.findall("edge"):
        edge_id = str(element.get("id", ""))
        params = _unique_params(element, edge_id=edge_id)
        official_link_id = str(params.get("origId", ""))
        direction = str(params.get("torii:station_direction", ""))
        if not official_link_id or direction not in _DIRECTIONS:
            raise OfficialLaneTransitionGraphError(
                f"edge {edge_id!r} lacks official link/direction identity"
            )
        station_from = _finite_float(params.get("torii:station_from_m"), "station_from_m")
        station_to = _finite_float(params.get("torii:station_to_m"), "station_to_m")
        if station_to <= station_from:
            raise OfficialLaneTransitionGraphError(f"edge {edge_id!r} station interval is invalid")
        try:
            num_lanes = int(str(element.get("numLanes", "")))
        except ValueError as exc:
            raise OfficialLaneTransitionGraphError(
                f"edge {edge_id!r} numLanes must be an integer"
            ) from exc
        shape = _parse_shape(element.get("shape"), edge_id=edge_id)
        corridor_id = "hh-sib-axis-" + hashlib.sha256(
            (official_link_id + "\0" + direction).encode("utf-8")
        ).hexdigest()[:16]
        grouped.setdefault(corridor_id, []).append(
            _AxisEdge(
                edge_id=edge_id,
                official_link_id=official_link_id,
                station_direction=direction,
                station_from_m=station_from,
                station_to_m=station_to,
                num_lanes=num_lanes,
                shape=shape,
            )
        )
    return {
        key: tuple(sorted(value, key=lambda edge: (edge.station_from_m, edge.edge_id)))
        for key, value in sorted(grouped.items())
    }


def _project_point_to_axis(
    point: tuple[float, float], axis: Sequence[_AxisEdge]
) -> dict[str, Any]:
    candidates: list[dict[str, Any]] = []
    for edge in axis:
        lengths = [_distance(first, second) for first, second in zip(edge.shape, edge.shape[1:])]
        total = sum(lengths)
        if total <= 0:
            raise OfficialLaneTransitionGraphError(f"edge {edge.edge_id!r} has zero length")
        preceding = 0.0
        for index, (first, second, length) in enumerate(zip(edge.shape, edge.shape[1:], lengths)):
            if length <= 0:
                preceding += length
                continue
            dx, dy = second[0] - first[0], second[1] - first[1]
            fraction = max(
                0.0,
                min(
                    1.0,
                    ((point[0] - first[0]) * dx + (point[1] - first[1]) * dy)
                    / (length**2),
                ),
            )
            projected = (first[0] + fraction * dx, first[1] + fraction * dy)
            edge_fraction = (preceding + fraction * length) / total
            if edge.station_direction == "with_stationing":
                station = edge.station_from_m + edge_fraction * (
                    edge.station_to_m - edge.station_from_m
                )
            else:
                station = edge.station_to_m - edge_fraction * (
                    edge.station_to_m - edge.station_from_m
                )
            candidates.append(
                {
                    "station_m": station,
                    "distance_m": _distance(point, projected),
                    "edge_id": edge.edge_id,
                    "segment_index": index,
                }
            )
            preceding += length
    if not candidates:
        raise OfficialLaneTransitionGraphError("directional axis contains no usable segments")
    return min(
        candidates,
        key=lambda item: (item["distance_m"], item["edge_id"], item["segment_index"]),
    )


def _axis_point_at_station(
    axis: Sequence[_AxisEdge], station: float
) -> dict[str, Any]:
    candidates = [
        edge
        for edge in axis
        if edge.station_from_m - 1e-7 <= station <= edge.station_to_m + 1e-7
    ]
    if not candidates:
        raise _AutomaticAbstention(
            "model_cut_station_is_outside_official_axis",
            {"station_m": station},
        )
    records: list[dict[str, Any]] = []
    for edge in candidates:
        station_fraction = (station - edge.station_from_m) / (
            edge.station_to_m - edge.station_from_m
        )
        if edge.station_direction == "against_stationing":
            station_fraction = 1.0 - station_fraction
        lengths = [_distance(first, second) for first, second in zip(edge.shape, edge.shape[1:])]
        total = sum(lengths)
        target = max(0.0, min(total, station_fraction * total))
        preceding = 0.0
        for index, (first, second, length) in enumerate(zip(edge.shape, edge.shape[1:], lengths)):
            if length <= 0:
                preceding += length
                continue
            if preceding + length + 1e-8 < target:
                preceding += length
                continue
            fraction = max(0.0, min(1.0, (target - preceding) / length))
            point = (
                first[0] + fraction * (second[0] - first[0]),
                first[1] + fraction * (second[1] - first[1]),
            )
            tangent = (
                (second[0] - first[0]) / length,
                (second[1] - first[1]) / length,
            )
            records.append(
                {
                    "point_xy": point,
                    "unit_tangent": tangent,
                    "heading_deg": math.degrees(math.atan2(tangent[1], tangent[0])) % 360,
                    "edge_id": edge.edge_id,
                    "segment_index": index,
                    "num_lanes": edge.num_lanes,
                }
            )
            break
    if not records:
        raise OfficialLaneTransitionGraphError("could not interpolate official axis station")
    selected = sorted(records, key=lambda item: (item["edge_id"], item["segment_index"]))[0]
    if any(_distance(selected["point_xy"], item["point_xy"]) > 0.10 for item in records[1:]):
        raise _AutomaticAbstention(
            "official_axis_is_geometrically_discontinuous_at_model_cut",
            {"station_m": station},
        )
    return selected


def _validate_reports(reports: Sequence[Mapping[str, Any]]) -> None:
    node_ids: set[str] = set()
    for report in reports:
        if report.get("schema") != HAMBURG_MAP_BINDING_SCHEMA or report.get("status") != "pass":
            raise OfficialLaneTransitionGraphError(
                "passing Hamburg MAP/KML-to-MAPEM binding reports are required"
            )
        node_id = str(report.get("node_id", ""))
        if not node_id or node_id in node_ids:
            raise OfficialLaneTransitionGraphError("MAP binding reports require unique node_id values")
        node_ids.add(node_id)
        if not isinstance(report.get("lanes"), list) or not isinstance(report.get("connections"), list):
            raise OfficialLaneTransitionGraphError("MAP binding report lanes/connections must be lists")
        source = report.get("source")
        if not isinstance(source, Mapping) or not _is_sha256(source.get("sha256")):
            raise OfficialLaneTransitionGraphError("MAP binding report source hash is invalid")


def _projection(manifest: Mapping[str, Any]) -> dict[str, str]:
    raw = manifest.get("projection")
    if not isinstance(raw, Mapping):
        raise OfficialLaneTransitionGraphError("PlainXML manifest projection is required")
    source = str(raw.get("source_crs", ""))
    target = str(raw.get("crs", ""))
    if not source or not target:
        raise OfficialLaneTransitionGraphError("PlainXML manifest projection is incomplete")
    return {"source_crs": source, "crs": target}


def _load_json_like(
    value: Mapping[str, Any] | str | Path,
    label: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    if isinstance(value, Mapping):
        payload = json.loads(json.dumps(dict(value), ensure_ascii=False))
        return payload, {
            "path": None,
            "sha256": _stable_digest(payload),
            "identity_method": "canonical_json_sha256",
        }
    path = Path(value).expanduser().resolve()
    try:
        raw = path.read_bytes()
        payload = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise OfficialLaneTransitionGraphError(f"cannot read valid {label}: {path}") from exc
    if not isinstance(payload, dict):
        raise OfficialLaneTransitionGraphError(f"{label} root must be an object")
    return payload, {
        "path": str(path),
        "sha256": hashlib.sha256(raw).hexdigest(),
        "bytes": len(raw),
        "identity_method": "file_bytes_sha256",
    }


def _promotion_input_hashes(identity: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "map_binding_reports": [item["sha256"] for item in identity["map_binding_reports"]],
        "lane_axis_stitch_plan": identity["lane_axis_stitch_plan"]["canonical_sha256"],
        "plainxml_manifest": identity["plainxml_manifest"]["sha256"],
        "nodes": identity["nodes"]["sha256"],
        "edges": identity["edges"]["sha256"],
        "hh_sib_source_sha256": identity["hh_sib_source_sha256"],
        "plainxml_candidate_id": identity["plainxml_candidate_id"],
    }


def _threshold_payload(value: OfficialLaneTransitionThresholds) -> dict[str, Any]:
    payload = asdict(value)
    payload["sample_fractions"] = list(value.sample_fractions)
    return payload


def _lane_order_payload(lanes: Sequence[_PreparedLane]) -> list[dict[str, Any]]:
    return [
        {
            "official_lane_id": lane.lane_id,
            "sumo_lane_index": index,
            "junction_B_xy": [_rounded(value) for value in lane.endpoint_b_xy],
            "station_range_m": [
                _rounded(min(lane.stations_m)),
                _rounded(max(lane.stations_m)),
            ],
        }
        for index, lane in enumerate(lanes)
    ]


def _project_coordinate(value: Any, transformer: Transformer) -> tuple[float, float]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)) or len(value) < 2:
        raise OfficialLaneTransitionGraphError("MAP coordinate requires longitude and latitude")
    lon = _finite_float(value[0], "longitude")
    lat = _finite_float(value[1], "latitude")
    if not -180 <= lon <= 180 or not -90 <= lat <= 90:
        raise OfficialLaneTransitionGraphError("MAP coordinate is outside longitude/latitude bounds")
    x, y = transformer.transform(lon, lat)
    if not math.isfinite(x) or not math.isfinite(y):
        raise OfficialLaneTransitionGraphError("MAP coordinate projection is non-finite")
    return float(x), float(y)


def _parse_shape(value: str | None, *, edge_id: str) -> tuple[tuple[float, float], ...]:
    points: list[tuple[float, float]] = []
    for token in str(value or "").split():
        parts = token.split(",")
        if len(parts) != 2:
            raise OfficialLaneTransitionGraphError(f"edge {edge_id!r} shape is invalid")
        points.append((_finite_float(parts[0], "shape x"), _finite_float(parts[1], "shape y")))
    if len(points) < 2:
        raise OfficialLaneTransitionGraphError(f"edge {edge_id!r} requires at least two shape points")
    return tuple(points)


def _unique_params(element: ET.Element, *, edge_id: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for param in element.findall("param"):
        key = str(param.get("key", ""))
        if not key or key in result:
            raise OfficialLaneTransitionGraphError(
                f"edge {edge_id!r} has a missing or duplicate param key"
            )
        result[key] = str(param.get("value", ""))
    return result


def _file_identity(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise OfficialLaneTransitionGraphError(f"input file does not exist: {path}")
    return {"path": str(path), "sha256": file_sha256(path), "bytes": path.stat().st_size}


def _finite_float(value: Any, field: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise OfficialLaneTransitionGraphError(f"{field} must be finite") from exc
    if not math.isfinite(result):
        raise OfficialLaneTransitionGraphError(f"{field} must be finite")
    return result


def _distance(first: Sequence[float], second: Sequence[float]) -> float:
    return math.hypot(float(first[0]) - float(second[0]), float(first[1]) - float(second[1]))


def _heading_delta(first: float, second: float) -> float:
    return abs((first - second + 180) % 360 - 180)


def _rms(values: Sequence[float]) -> float:
    return math.sqrt(sum(value**2 for value in values) / len(values))


def _rounded(value: float | None) -> float | None:
    return None if value is None else round(float(value), 6)


def _stable_digest(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )
    return hashlib.sha256(payload).hexdigest()


def _is_sha256(value: Any) -> bool:
    return bool(_SHA256_PATTERN.fullmatch(str(value or "").lower()))


def _identifier_sort_key(value: str) -> tuple[int, int | str]:
    return (0, int(value)) if value.isdigit() else (1, value)


__all__ = [
    "OFFICIAL_LANE_TRANSITION_GRAPH_SCHEMA",
    "OfficialLaneTransitionGraphError",
    "OfficialLaneTransitionThresholds",
    "build_hamburg_official_lane_transition_graph",
]
