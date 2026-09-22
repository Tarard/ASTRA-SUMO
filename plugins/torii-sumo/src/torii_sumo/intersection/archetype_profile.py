"""Evidence-bound, composable intersection recognition.

This module deliberately stops before reconstruction.  It describes a
junction cell as a finite product of orthogonal dimensions and keeps familiar
labels such as T3 and X4 as derived aliases.  The result can constrain later
candidate generation, but it never authorizes a node join, channelization
rewrite, or traffic-light binding.
"""

from __future__ import annotations

import hashlib
import json
import math
from functools import lru_cache
from itertools import combinations
from typing import Any, Mapping

from .physical_cell import infer_roundabout_boundary_approaches
from .road_detail import (
    classify_intersection_road_detail,
    registered_road_detail_vocabulary,
)
from .schema import OSMPatch
from .topology_evidence import (
    build_protected_topology_features,
    build_topology_evidence,
)


SCHEMA_ID = "torii.intersection-archetype-profile/v1"
CLASSIFIER_VERSION = "osm-composable-v1"

_EVIDENCE_GRADES = (
    "observed",
    "rule_derived",
    "reviewed",
    "contradicted",
    "unknown",
)
_DECISIONS = ("pass", "review_required", "blocked", "not_applicable")
_ENUMS: dict[str, tuple[str, ...]] = {
    "grade_relation": ("at_grade", "grade_separated", "mixed", "unknown"),
    "interaction_kind": (
        "cross_and_turn",
        "merge_diverge",
        "access_on_continuous_mainline",
        "crossing_only",
        "mixed",
        "unknown",
    ),
    "cell_structure": (
        "atomic",
        "serial_compound",
        "network_compound",
        "ring_group",
        "interchange_group",
        "unknown",
    ),
    "angular_form": (
        "orthogonal_like",
        "skewed",
        "radial",
        "irregular",
        "unknown",
    ),
    "angular_distribution": (
        "orthogonal_like",
        "radial",
        "irregular",
        "unknown",
    ),
    "minimum_angle_status": (
        "non_skew",
        "small_angle_present",
        "unknown",
    ),
    "circulation_form": (
        "none",
        "nontraversable_ring",
        "traversable_mini",
        "unknown",
    ),
    "carriageway_organization": (
        "bidirectional",
        "divided_pair",
        "one_way_pair",
        "reversible",
        "mixed",
        "unknown",
    ),
    "control_rule": (
        "uncontrolled",
        "priority",
        "stop",
        "all_way_stop",
        "yield",
        "signalized",
        "rail",
        "mixed",
        "unknown",
    ),
    "controller_topology": (
        "single_core_single_controller",
        "multi_core_shared_controller",
        "linked_controllers",
        "unknown",
    ),
    "movement_graph_status": ("complete", "partial", "contradictory", "unknown"),
    "arm_count_class": ("A0", "A1", "A2", "A3", "A4", "A5_plus", "unknown"),
    "derived_alias": (
        "T3",
        "Y3",
        "X4",
        "irregular_3",
        "irregular_4",
        "multiarm",
        "roundabout",
        "two_arm",
        "unknown",
    ),
}
_CHANNELIZATION_TYPES = (
    "turn_bay",
    "slip_bypass",
    "splitter_island",
    "median_refuge",
    "flare_fanout",
    "storage_connector",
    "weave_segment",
    "protected_corner",
)
_MOVEMENT_TRANSFORMS = (
    "direct_left",
    "left_via_uturn",
    "left_via_auxiliary",
    "minor_street_via_uturn",
    "contraflow",
    "contraflow_plus_uturn",
    "circulation",
    "through_crossover",
    "left_crossover",
    "dynamic_lane_sharing",
)
_FACILITY_MODES = ("motor_vehicle", "bicycle", "pedestrian", "rail", "access")


def registered_intersection_type_vocabulary() -> dict[str, Any]:
    """Return the finite canonical vocabulary used by the generic classifier."""

    payload = {
        "schema": "torii.intersection-type-vocabulary/v1",
        "dimensions": {key: list(values) for key, values in sorted(_ENUMS.items())},
        "channelization_types": list(_CHANNELIZATION_TYPES),
        "movement_transforms": list(_MOVEMENT_TRANSFORMS),
        "facility_modes": list(_FACILITY_MODES),
        "evidence_grades": list(_EVIDENCE_GRADES),
        "decisions": list(_DECISIONS),
        "composition_model": {
            "cell": "atomic cores connected by typed internal connectors",
            "arms": "semantic boundary ports grouped independently of OSM way count",
            "control": "a separate domain that never proves physical-core identity",
            "aliases": "T3/Y3/X4/roundabout are derived views, not canonical storage",
            "road_detail": "road function, arm form, channelization, and connectors are separate lower-level axes",
        },
        "road_detail_vocabulary": registered_road_detail_vocabulary(),
    }
    return {**payload, "vocabulary_id": f"intersection-vocabulary-{_stable_digest(payload)[:20]}"}


def classify_osm_intersection_archetype(
    patch: OSMPatch,
    physical_cell: Mapping[str, Any],
    *,
    topology_evidence: Mapping[str, Any] | None = None,
    movement_hypotheses: Mapping[str, Any] | None = None,
    source_evidence: Mapping[str, Any] | None = None,
    road_network_evidence: Mapping[str, Any] | None = None,
    opposite_tolerance_deg: float = 30.0,
    minimum_non_skew_angle_deg: float = 70.0,
) -> dict[str, Any]:
    """Classify one OSM-derived cell without selecting an execution strategy.

    ``opposite_tolerance_deg=30`` implements the reviewed 150--210 degree
    opposite-link interval used in published HD-map intersection work.  It is
    emitted as a configurable engineering threshold, not a universal fact.
    Likewise, the 70 degree skew boundary comes from signalized-junction design
    guidance and is evidence for review, not an automatic reconstruction gate.
    """

    if not 0.0 < opposite_tolerance_deg < 90.0:
        raise ValueError("opposite_tolerance_deg must be between 0 and 90")
    if not 0.0 < minimum_non_skew_angle_deg < 180.0:
        raise ValueError("minimum_non_skew_angle_deg must be between 0 and 180")

    physical_cell_id = physical_cell.get("hypothesis_id")
    topology = dict(topology_evidence or build_topology_evidence(patch, physical_cell))
    if topology.get("physical_cell_hypothesis_id") != physical_cell_id:
        raise ValueError("topology_evidence physical_cell_hypothesis_id does not match the supplied physical cell")
    if (
        movement_hypotheses is not None
        and movement_hypotheses.get("parent_physical_cell_hypothesis_id") != physical_cell_id
    ):
        raise ValueError(
            "movement_hypotheses parent_physical_cell_hypothesis_id does not match the supplied physical cell"
        )
    approaches = sorted(
        (dict(item) for item in physical_cell.get("physical_approaches", ())),
        key=lambda item: str(item.get("physical_approach_id", "")),
    )
    path_node_ids = set(map(str, physical_cell.get("path_closure_node_ids", ())))
    incident_way_ids = sorted(way.id for way in patch.ways.values() if path_node_ids.intersection(way.node_refs))
    circulation_evidence = _circulation_evidence(
        patch,
        path_node_ids=path_node_ids,
        incident_way_ids=incident_way_ids,
    )
    roundabout_boundary = None
    if circulation_evidence["value"] == "nontraversable_ring":
        roundabout_boundary = infer_roundabout_boundary_approaches(
            patch,
            roundabout_way_ids=circulation_evidence["evidence_ids"],
        )
        approaches = sorted(
            (dict(item) for item in roundabout_boundary["physical_approaches"]),
            key=lambda item: str(item.get("physical_approach_id", "")),
        )
        incident_way_ids = sorted(
            set(incident_way_ids) | {str(item["way_id"]) for item in roundabout_boundary["raw_boundary_ports"]}
        )
        path_node_ids.update(map(str, roundabout_boundary["ring_node_ids"]))
    cell_structure = _cell_structure(topology, circulation_evidence)
    arm_model = _arm_model(
        approaches,
        circulation_form=circulation_evidence["value"],
        cell_structure=cell_structure,
        opposite_tolerance_deg=opposite_tolerance_deg,
        count_evidence_grade=(
            "unknown"
            if roundabout_boundary is not None
            and roundabout_boundary.get("ring_validation", {}).get("status") != "pass"
            else "rule_derived"
        ),
    )
    protected = dict(topology.get("protected_features", {}))
    supplemental_protected_evidence = None
    if roundabout_boundary is not None:
        supplemental_features = build_protected_topology_features(
            patch,
            path_node_ids=path_node_ids,
        )
        protected = _merge_protected_features(protected, supplemental_features)
        supplemental_payload = {
            "source": "expanded_roundabout_ring_closure",
            "ring_boundary_hypothesis_id": roundabout_boundary["hypothesis_id"],
            "path_closure_node_ids": sorted(path_node_ids),
            "protected_features": supplemental_features,
        }
        supplemental_protected_evidence = {
            **supplemental_payload,
            "evidence_id": (f"roundabout-protected-{_stable_digest(supplemental_payload)[:20]}"),
        }
    link_way_ids = sorted(
        way_id for way_id in incident_way_ids if str(patch.ways[way_id].tags.get("highway", "")).endswith("_link")
    )

    angular_dimensions = _angular_dimensions(
        arm_model,
        minimum_non_skew_angle_deg=minimum_non_skew_angle_deg,
    )
    dimensions = {
        "grade_relation": _grade_relation(protected),
        "interaction_kind": _interaction_kind(
            arm_count=arm_model["arm_count"],
            link_way_ids=link_way_ids,
            circulation_form=circulation_evidence["value"],
        ),
        "cell_structure": cell_structure,
        **angular_dimensions,
        "circulation_form": circulation_evidence,
        "carriageway_organization": _carriageway_organization(approaches),
        "control_rule": _control_rule(patch, path_node_ids),
        "controller_topology": _dimension(
            "controller_topology",
            "unknown",
            grade="unknown",
            decision="review_required",
            rationale=(
                "OSM signal anchors contain no complete controller-to-owner contract; "
                "shared signal tags cannot establish physical-core identity."
            ),
        ),
        "movement_graph_status": _movement_graph_status(
            movement_hypotheses,
            semantic_arm_basis_matches=(roundabout_boundary is None),
        ),
    }
    channelization = _channelization_features(
        patch,
        path_node_ids=path_node_ids,
    )
    evidence_features = _evidence_features(
        patch,
        approaches,
        link_way_ids=link_way_ids,
        topology=topology,
        path_node_ids=path_node_ids,
    )
    facilities = _facility_modes(approaches, protected)
    movement_transforms = (
        [
            {
                "type": "circulation",
                "status": circulation_evidence["status"],
                "decision": "review_required",
                "evidence_ids": circulation_evidence["evidence_ids"],
            }
        ]
        if circulation_evidence["value"] in {"nontraversable_ring", "traversable_mini"}
        else []
    )
    atomic_cores = _atomic_core_candidates(topology, circulation_evidence)
    connectors = [
        {
            "connector_id": (
                "connector-"
                + _stable_digest(
                    {
                        "from": item.get("from_branch_node_id"),
                        "to": item.get("to_branch_node_id"),
                    }
                )[:16]
            ),
            "from_core_source_node_id": item.get("from_branch_node_id"),
            "to_core_source_node_id": item.get("to_branch_node_id"),
            "length_m": item.get("graph_distance_m"),
            "storage_capable": bool(item.get("storage_capable")),
            "status": "observed",
        }
        for item in topology.get("branch_connectors", ())
    ]
    road_detail = classify_intersection_road_detail(
        patch,
        physical_cell,
        arm_model=arm_model,
        topology_evidence=topology,
        movement_hypotheses=movement_hypotheses,
        road_network_evidence=road_network_evidence,
    )

    canonical_identity = {
        "grade_relation": dimensions["grade_relation"]["value"],
        "interaction_kind": dimensions["interaction_kind"]["value"],
        "cell_structure": dimensions["cell_structure"]["value"],
        "arm_count": arm_model["arm_count"],
        "entry_count": arm_model["entry_count"],
        "exit_count": arm_model["exit_count"],
        "arm_count_status": arm_model["count_status"],
        "arm_count_class": arm_model["arm_count_class"],
        "through_pairs": [pair["arm_ids"] for pair in arm_model["through_pairs"]],
        "angular_form": dimensions["angular_form"]["value"],
        "angular_distribution": dimensions["angular_distribution"]["value"],
        "minimum_angle_status": dimensions["minimum_angle_status"]["value"],
        "circulation_form": dimensions["circulation_form"]["value"],
        "carriageway_organization": dimensions["carriageway_organization"]["value"],
        "channelization_types": sorted({item["type"] for item in channelization}),
        "road_arm_network_roles": sorted(
            {
                str(item["road_identity"]["network_role"]["value"])
                for item in road_detail["road_arms"]
            }
        ),
        "connection_relation_types": sorted(
            {str(item["relation"]) for item in road_detail["connection_relations"]}
        ),
        "movement_transforms": sorted({item["type"] for item in movement_transforms}),
        "facility_modes": facilities,
        "control_rule": dimensions["control_rule"]["value"],
        "controller_topology": dimensions["controller_topology"]["value"],
        "movement_graph_status": dimensions["movement_graph_status"]["value"],
    }
    unknown_dimensions = sorted(name for name, record in dimensions.items() if record["value"] == "unknown")
    review_reasons = _review_reasons(
        physical_cell=physical_cell,
        dimensions=dimensions,
        arm_model=arm_model,
        additional_risks=(roundabout_boundary.get("risks", ()) if roundabout_boundary is not None else ()),
    )
    source = dict(source_evidence or {})
    payload = {
        "schema": SCHEMA_ID,
        "classifier_version": CLASSIFIER_VERSION,
        "vocabulary_id": registered_intersection_type_vocabulary()["vocabulary_id"],
        "generation_status": "pass",
        "disposition": "review" if review_reasons else "suggest",
        "classification_only": True,
        "automatic_promotion_gate": "blocked",
        "parent_physical_cell_hypothesis_id": physical_cell_id,
        "parent_topology_evidence_id": topology.get("topology_evidence_id"),
        "parent_movement_hypothesis_set_id": (
            movement_hypotheses.get("hypothesis_set_id") if movement_hypotheses else None
        ),
        "source_evidence": source,
        "semantic_arm_evidence": (
            {
                "source": "explicit_roundabout_ring_boundary",
                "hypothesis_id": roundabout_boundary["hypothesis_id"],
                "generation_status": roundabout_boundary["generation_status"],
                "roundabout_way_ids": roundabout_boundary["roundabout_way_ids"],
                "ring_validation": roundabout_boundary.get("ring_validation"),
                "risks": roundabout_boundary["risks"],
            }
            if roundabout_boundary is not None
            else {
                "source": "physical_cell_boundary_ports",
                "hypothesis_id": physical_cell.get("hypothesis_id"),
                "generation_status": physical_cell.get("generation_status"),
                "risks": list(physical_cell.get("risks", ())),
            }
        ),
        "supplemental_protected_evidence": supplemental_protected_evidence,
        "canonical_identity": canonical_identity,
        "arm_model": arm_model,
        "dimensions": dimensions,
        "atomic_core_candidates": atomic_cores,
        "internal_connectors": connectors,
        "road_detail": road_detail,
        "channelization": channelization,
        "movement_transforms": movement_transforms,
        "facility_modes": facilities,
        "evidence_features": evidence_features,
        "derived_alias": arm_model["derived_alias"],
        "legacy_projection": {
            "base_skeleton": arm_model["derived_alias"]["value"],
            "warning": (
                "Compatibility view only. Do not store or branch reconstruction "
                "logic on this flat label without the canonical identity."
            ),
        },
        "thresholds": {
            "opposite_tolerance_deg": opposite_tolerance_deg,
            "opposite_interval_deg": [
                180.0 - opposite_tolerance_deg,
                180.0 + opposite_tolerance_deg,
            ],
            "minimum_non_skew_angle_deg": minimum_non_skew_angle_deg,
            "status": "configurable_engineering_thresholds",
        },
        "unknown_dimensions": unknown_dimensions,
        "review_reasons": review_reasons,
        "decision_capabilities": {
            "type_recognition": (
                "pass"
                if (
                    arm_model["count_status"] == "rule_derived"
                    and arm_model["derived_alias"]["status"] == "rule_derived"
                    and arm_model["derived_alias"]["value"] != "unknown"
                )
                else "review_required"
            ),
            "existing_t3_x4_movement_generation": (
                "conditional"
                if (
                    arm_model["derived_alias"]["value"] in {"T3", "X4"}
                    and arm_model["opposition_pairing_status"] == "complete"
                    and arm_model["count_status"] == "rule_derived"
                )
                else "unsupported"
            ),
            "automatic_node_merge": "blocked",
            "automatic_channelization_rebuild": "blocked",
            "automatic_signal_binding": "blocked",
        },
        "claim_boundary": (
            "This profile identifies a finite composable junction hypothesis. "
            "It does not prove physical-core boundaries, legal lane movements, "
            "controller ownership, or authorize any SUMO mutation."
        ),
    }
    return {
        **payload,
        "classification_id": f"intersection-classification-{_stable_digest(payload)[:20]}",
    }


def _arm_model(
    approaches: list[dict[str, Any]],
    *,
    circulation_form: str,
    cell_structure: Mapping[str, Any],
    opposite_tolerance_deg: float,
    count_evidence_grade: str = "rule_derived",
) -> dict[str, Any]:
    arms = [
        {
            "arm_id": str(item.get("physical_approach_id")),
            "bearing_deg": _finite_float(item.get("bearing_from_seed_deg")),
            "source_way_ids": sorted(map(str, item.get("source_way_ids", ()))),
            "boundary_port_ids": sorted(map(str, item.get("member_boundary_port_ids", ()))),
            "incoming_lane_count": int(item.get("incoming_lane_count", 0)),
            "outgoing_lane_count": int(item.get("outgoing_lane_count", 0)),
            "grouping_status": str(item.get("grouping_status", "review_required")),
        }
        for item in approaches
    ]
    arms.sort(key=lambda item: item["arm_id"])
    arm_count = len(arms)
    entry_count = sum(1 for item in arms if item["incoming_lane_count"] > 0)
    exit_count = sum(1 for item in arms if item["outgoing_lane_count"] > 0)
    count_class = _arm_count_class(arm_count)
    missing_bearings = [item["arm_id"] for item in arms if item["bearing_deg"] is None]
    sorted_bearings = sorted(item["bearing_deg"] for item in arms if item["bearing_deg"] is not None)
    adjacent_bearing_gaps = (
        [
            round(
                (sorted_bearings[(index + 1) % len(sorted_bearings)] - sorted_bearings[index]) % 360.0,
                3,
            )
            for index in range(len(sorted_bearings))
        ]
        if len(sorted_bearings) == len(arms) and len(arms) >= 2
        else []
    )
    grouping_uncertain = [item["arm_id"] for item in arms if item["grouping_status"] != "pass"]
    if circulation_form in {"nontraversable_ring", "traversable_mini"}:
        pairs: list[dict[str, Any]] = []
        pairing_status = "not_applicable"
    elif arm_count > 4:
        # Exact arm count is sufficient for the finite multi-arm class.  Avoid
        # an exponential maximum-matching search that cannot refine that alias.
        pairs = []
        pairing_status = "not_evaluated_multiarm"
    elif missing_bearings:
        pairs = []
        pairing_status = "unknown"
    elif grouping_uncertain:
        pairs = _best_opposition_pairs(arms, tolerance_deg=opposite_tolerance_deg)
        pairing_status = "partial"
    else:
        pairs = _best_opposition_pairs(arms, tolerance_deg=opposite_tolerance_deg)
        pairing_status = "complete"
    paired = {arm_id for pair in pairs for arm_id in pair["arm_ids"]}
    unmatched = (
        sorted(item["arm_id"] for item in arms if item["arm_id"] not in paired)
        if pairing_status in {"complete", "partial", "unknown"}
        else []
    )
    count_status = "rule_derived" if not grouping_uncertain and count_evidence_grade == "rule_derived" else "unknown"
    if circulation_form in {"nontraversable_ring", "traversable_mini"}:
        alias = "roundabout"
        alias_grade = "rule_derived" if count_status == "rule_derived" else "unknown"
    elif arm_count > 4 and count_status == "rule_derived":
        alias = "multiarm"
        alias_grade = "rule_derived"
    elif pairing_status == "complete" and count_status == "rule_derived":
        alias = _derived_alias(
            arm_count,
            pairs=pairs,
            arms=arms,
            circulation_form=circulation_form,
            cell_structure=cell_structure,
        )
        alias_grade = "rule_derived"
    else:
        alias = "unknown"
        alias_grade = "unknown"
    return {
        "arm_count": arm_count,
        "entry_count": entry_count,
        "exit_count": exit_count,
        "count_status": count_status,
        "arm_count_class": count_class,
        "arms": arms,
        "adjacent_bearing_gaps_deg": adjacent_bearing_gaps,
        "through_pairs": pairs,
        "unmatched_arm_ids": unmatched,
        "opposition_pairing_status": pairing_status,
        "derived_alias": _dimension(
            "derived_alias",
            alias,
            grade=alias_grade,
            decision="review_required",
            evidence_ids=[item["arm_id"] for item in arms],
            rationale=(
                "Alias derived from semantic arm count, disjoint opposite-bearing pairs, "
                "and explicit circulation evidence; it is not the canonical type."
            ),
            alternatives=_alias_alternatives(alias, arm_count),
        ),
    }


def _best_opposition_pairs(
    arms: list[dict[str, Any]],
    *,
    tolerance_deg: float,
) -> list[dict[str, Any]]:
    bearing_by_index = {index: item["bearing_deg"] for index, item in enumerate(arms)}
    errors: dict[tuple[int, int], float] = {}
    for first, second in combinations(range(len(arms)), 2):
        first_bearing = bearing_by_index[first]
        second_bearing = bearing_by_index[second]
        if first_bearing is None or second_bearing is None:
            continue
        separation = _circular_separation(first_bearing, second_bearing)
        error = abs(180.0 - separation)
        if error <= tolerance_deg:
            errors[(first, second)] = error

    @lru_cache(maxsize=None)
    def solve(remaining: tuple[int, ...]) -> tuple[tuple[int, int], ...]:
        if not remaining:
            return ()
        first = remaining[0]
        best = solve(remaining[1:])
        for offset, second in enumerate(remaining[1:], start=1):
            key = (min(first, second), max(first, second))
            if key not in errors:
                continue
            rest = remaining[1:offset] + remaining[offset + 1 :]
            candidate = (key, *solve(rest))
            if _pairing_score(candidate, errors) < _pairing_score(best, errors):
                best = candidate
        return tuple(sorted(best))

    selected = solve(tuple(range(len(arms))))
    records = []
    for first, second in selected:
        separation = _circular_separation(arms[first]["bearing_deg"], arms[second]["bearing_deg"])
        records.append(
            {
                "arm_ids": sorted([arms[first]["arm_id"], arms[second]["arm_id"]]),
                "bearing_separation_deg": round(separation, 3),
                "opposition_error_deg": round(errors[(first, second)], 3),
                "status": "rule_derived",
            }
        )
    return sorted(records, key=lambda item: item["arm_ids"])


def _pairing_score(
    pairs: tuple[tuple[int, int], ...],
    errors: Mapping[tuple[int, int], float],
) -> tuple[int, float, tuple[tuple[int, int], ...]]:
    return (-len(pairs), round(sum(errors[pair] for pair in pairs), 9), tuple(sorted(pairs)))


def _derived_alias(
    arm_count: int,
    *,
    pairs: list[dict[str, Any]],
    arms: list[dict[str, Any]],
    circulation_form: str,
    cell_structure: Mapping[str, Any],
) -> str:
    if circulation_form in {"nontraversable_ring", "traversable_mini"}:
        return "roundabout"
    if arm_count == 3:
        if len(pairs) == 1:
            return "T3"
        return "Y3" if _is_radial_three_arm(arms) else "irregular_3"
    if arm_count == 4:
        if len(pairs) == 2:
            return "X4" if cell_structure["value"] == "atomic" else "unknown"
        return "irregular_4"
    if arm_count >= 5:
        return "multiarm"
    if arm_count == 2:
        return "two_arm"
    return "unknown"


def _is_radial_three_arm(arms: list[dict[str, Any]]) -> bool:
    bearings = sorted(item["bearing_deg"] for item in arms if item["bearing_deg"] is not None)
    if len(bearings) != 3:
        return False
    gaps = [(bearings[(index + 1) % 3] - bearings[index]) % 360.0 for index in range(3)]
    return all(80.0 <= gap <= 160.0 for gap in gaps)


def _grade_relation(protected: Mapping[str, Any]) -> dict[str, Any]:
    way_ids = sorted(map(str, protected.get("grade_separation_way_ids", ())))
    if way_ids:
        return _dimension(
            "grade_relation",
            "unknown",
            grade="observed",
            decision="review_required",
            evidence_ids=way_ids,
            rationale=(
                "Bridge/tunnel/layer evidence is present, but incident tags alone do not "
                "prove whether the candidate interaction is grade-separated."
            ),
            alternatives=["grade_separated", "mixed"],
        )
    return _dimension(
        "grade_relation",
        "at_grade",
        grade="rule_derived",
        decision="review_required",
        rationale="Connected vehicle graph with no protected grade tag in the bounded cell.",
        alternatives=["unknown"],
    )


def _interaction_kind(
    *,
    arm_count: int,
    link_way_ids: list[str],
    circulation_form: str,
) -> dict[str, Any]:
    if circulation_form in {"nontraversable_ring", "traversable_mini"}:
        value = "cross_and_turn"
        grade = "rule_derived"
        alternatives = ["mixed"] if link_way_ids else []
    else:
        value = "unknown"
        grade = "unknown"
        alternatives = []
        if arm_count >= 3:
            alternatives.append("cross_and_turn")
        if link_way_ids:
            alternatives.extend(["merge_diverge", "access_on_continuous_mainline", "mixed"])
    return _dimension(
        "interaction_kind",
        value,
        grade=grade,
        decision="review_required",
        evidence_ids=link_way_ids,
        rationale=(
            "Explicit circulation establishes cross-and-turn interaction for a ring. "
            "Otherwise arm count and *_link ways are only alternatives: legal movement "
            "closure is required to distinguish crossing, merge/diverge, access, or mixed interaction."
        ),
        alternatives=sorted(set(alternatives)),
    )


def _cell_structure(
    topology: Mapping[str, Any],
    circulation: Mapping[str, Any],
) -> dict[str, Any]:
    if circulation["value"] in {"nontraversable_ring", "traversable_mini"}:
        return _dimension(
            "cell_structure",
            "ring_group",
            grade="observed",
            decision="review_required",
            evidence_ids=list(map(str, circulation.get("evidence_ids", ()))),
            rationale=(
                "Explicit circulation evidence defines a grouped ring cell; ring and "
                "movement validation remain separate."
            ),
        )
    branch_count = int(topology.get("branch_node_count", 0))
    storage = list(topology.get("storage_capable_connectors", ()))
    if branch_count == 1:
        value = "atomic"
        grade = "rule_derived"
        alternatives: list[str] = []
        rationale = (
            "One bounded vehicle branch center supports an atomic-core hypothesis; "
            "movement-conflict and stop-line evidence remain review gates."
        )
    elif branch_count > 1:
        value = "unknown"
        grade = "unknown"
        alternatives = ["serial_compound", "network_compound"] if storage else ["atomic", "network_compound"]
        rationale = (
            "Multiple OSM branch nodes are candidate core evidence, not physical conflict "
            "cores. Connector topology cannot distinguish micro-node expansion from a "
            "serial or network compound cell without stop-line/conflict evidence."
        )
    else:
        value = "unknown"
        grade = "unknown"
        alternatives = ["atomic"]
        rationale = "No bounded vehicle branch center establishes a physical core."
    evidence_ids = [str(topology.get("topology_evidence_id", ""))]
    return _dimension(
        "cell_structure",
        value,
        grade=grade,
        decision="review_required",
        evidence_ids=[item for item in evidence_ids if item],
        rationale=rationale,
        alternatives=alternatives,
    )


def _angular_dimensions(
    arm_model: Mapping[str, Any],
    *,
    minimum_non_skew_angle_deg: float,
) -> dict[str, dict[str, Any]]:
    arms = list(arm_model["arms"])
    bearings = sorted(item["bearing_deg"] for item in arms if item["bearing_deg"] is not None)
    evidence_ids = [item["arm_id"] for item in arms]
    if len(bearings) != len(arms) or len(bearings) < 2:
        distribution = "unknown"
        minimum_angle_status = "unknown"
        gaps: list[float] = []
    else:
        gaps = list(arm_model["adjacent_bearing_gaps_deg"])
        minimum_angle_status = "small_angle_present" if min(gaps) < minimum_non_skew_angle_deg else "non_skew"
        if (len(arms) == 3 and len(arm_model["through_pairs"]) == 1) or (
            len(arms) == 4 and len(arm_model["through_pairs"]) == 2
        ):
            distribution = "orthogonal_like"
        elif len(arms) >= 3 and max(gaps) - min(gaps) <= 30.0:
            distribution = "radial"
        else:
            distribution = "irregular"

    if minimum_angle_status == "small_angle_present":
        compatibility_form = "skewed"
    else:
        compatibility_form = distribution
    compatibility_alternatives = [distribution] if compatibility_form == "skewed" and distribution != "unknown" else []
    return {
        "angular_distribution": _dimension(
            "angular_distribution",
            distribution,
            grade="rule_derived" if distribution != "unknown" else "unknown",
            decision="review_required",
            evidence_ids=evidence_ids,
            rationale=(
                "Bearing-gap distribution and opposition pairs interpreted independently "
                "of physical-cell structure and minimum-angle acceptability."
            ),
        ),
        "minimum_angle_status": _dimension(
            "minimum_angle_status",
            minimum_angle_status,
            grade=("rule_derived" if minimum_angle_status != "unknown" else "unknown"),
            decision="review_required",
            evidence_ids=evidence_ids,
            rationale=(
                "Minimum adjacent bearing gap compared with the emitted configurable "
                f"{minimum_non_skew_angle_deg:g} degree engineering threshold."
            ),
        ),
        "angular_form": _dimension(
            "angular_form",
            compatibility_form,
            grade="rule_derived" if compatibility_form != "unknown" else "unknown",
            decision="review_required",
            evidence_ids=evidence_ids,
            rationale=(
                "Compatibility projection only. Canonical logic must use angular_distribution "
                "and minimum_angle_status as separate dimensions."
            ),
            alternatives=compatibility_alternatives,
        ),
    }


def _circulation_evidence(
    patch: OSMPatch,
    *,
    path_node_ids: set[str],
    incident_way_ids: list[str],
) -> dict[str, Any]:
    roundabout_way_ids = _connected_tagged_way_component(
        patch,
        seed_way_ids=sorted(
            way_id for way_id in incident_way_ids if patch.ways[way_id].tags.get("junction") == "roundabout"
        ),
        junction_value="roundabout",
    )
    circular_way_ids = _connected_tagged_way_component(
        patch,
        seed_way_ids=sorted(
            way_id for way_id in incident_way_ids if patch.ways[way_id].tags.get("junction") == "circular"
        ),
        junction_value="circular",
    )
    mini_node_ids = sorted(
        node_id
        for node_id in path_node_ids
        if node_id in patch.nodes and patch.nodes[node_id].tags.get("highway") == "mini_roundabout"
    )
    if roundabout_way_ids:
        return _dimension(
            "circulation_form",
            "nontraversable_ring",
            grade="observed",
            decision="review_required",
            evidence_ids=roundabout_way_ids,
            rationale="Explicit OSM junction=roundabout evidence; ring continuity and priority still need validation.",
        )
    if mini_node_ids:
        return _dimension(
            "circulation_form",
            "traversable_mini",
            grade="observed",
            decision="review_required",
            evidence_ids=mini_node_ids,
            rationale="Explicit OSM highway=mini_roundabout evidence.",
        )
    if circular_way_ids:
        return _dimension(
            "circulation_form",
            "unknown",
            grade="observed",
            decision="review_required",
            evidence_ids=circular_way_ids,
            rationale="OSM junction=circular does not assert roundabout priority semantics.",
            alternatives=["nontraversable_ring", "none"],
        )
    return _dimension(
        "circulation_form",
        "none",
        grade="rule_derived",
        decision="review_required",
        rationale="No explicit roundabout, circular-junction, or mini-roundabout tag in the bounded cell.",
        alternatives=["unknown"],
    )


def _connected_tagged_way_component(
    patch: OSMPatch,
    *,
    seed_way_ids: list[str],
    junction_value: str,
) -> list[str]:
    selected = set(seed_way_ids)
    selected_node_ids = {str(node_id) for way_id in selected for node_id in patch.ways[way_id].node_refs}
    changed = True
    while changed:
        changed = False
        for way in patch.ways.values():
            if way.id in selected or way.tags.get("junction") != junction_value:
                continue
            if not selected_node_ids.intersection(map(str, way.node_refs)):
                continue
            selected.add(way.id)
            selected_node_ids.update(map(str, way.node_refs))
            changed = True
    return sorted(selected)


def _carriageway_organization(approaches: list[dict[str, Any]]) -> dict[str, Any]:
    paired = [
        item
        for item in approaches
        if item.get("member_count") == 2 and set(map(str, item.get("flow_roles", ()))) == {"incoming", "outgoing"}
    ]
    bidirectional = [
        item
        for item in approaches
        if item.get("member_count") == 1 and list(map(str, item.get("flow_roles", ()))) == ["bidirectional"]
    ]
    if approaches and len(paired) == len(approaches):
        value = "unknown"
        grade = "unknown"
        alternatives = ["divided_pair", "one_way_pair"]
    elif approaches and len(bidirectional) == len(approaches):
        value = "bidirectional"
        grade = "rule_derived"
        alternatives = []
    elif paired or bidirectional:
        value = "mixed"
        grade = "rule_derived"
        alternatives = ["divided_pair", "one_way_pair"] if paired else []
    else:
        value = "unknown"
        grade = "unknown"
        alternatives = []
    return _dimension(
        "carriageway_organization",
        value,
        grade=grade,
        decision="review_required",
        evidence_ids=[str(item.get("physical_approach_id")) for item in approaches],
        rationale=(
            "Grouped directional boundary ports establish a semantic arm, but OSM-only "
            "road identity does not distinguish a divided carriageway from a paired "
            "one-way street system."
        ),
        alternatives=alternatives,
    )


def _control_rule(patch: OSMPatch, path_node_ids: set[str]) -> dict[str, Any]:
    evidence: dict[str, list[str]] = {
        "signalized": [],
        "stop": [],
        "yield": [],
        "rail": [],
    }
    for node_id in sorted(path_node_ids):
        if node_id not in patch.nodes:
            continue
        tags = patch.nodes[node_id].tags
        if tags.get("highway") == "traffic_signals" or tags.get("crossing") == "traffic_signals":
            evidence["signalized"].append(node_id)
        if tags.get("highway") == "stop":
            evidence["stop"].append(node_id)
        if tags.get("highway") == "give_way":
            evidence["yield"].append(node_id)
        if tags.get("railway") in {"level_crossing", "crossing"}:
            evidence["rail"].append(node_id)
    present = [key for key, ids in evidence.items() if ids]
    value = present[0] if len(present) == 1 else ("mixed" if present else "unknown")
    return _dimension(
        "control_rule",
        value,
        grade="observed" if present else "unknown",
        decision="review_required",
        evidence_ids=sorted({item for ids in evidence.values() for item in ids}),
        rationale=(
            "OSM control tags describe local rules or signal anchors; they do not provide a "
            "complete controller/link/phase binding."
        ),
    )


def _movement_graph_status(
    movement_hypotheses: Mapping[str, Any] | None,
    *,
    semantic_arm_basis_matches: bool = True,
) -> dict[str, Any]:
    if not movement_hypotheses:
        return _dimension(
            "movement_graph_status",
            "unknown",
            grade="unknown",
            decision="review_required",
            rationale="No legal lane-movement evidence was supplied to the classifier.",
        )
    variants = list(movement_hypotheses.get("variants", ()))
    if not semantic_arm_basis_matches:
        return _dimension(
            "movement_graph_status",
            "unknown",
            grade="unknown",
            decision="review_required",
            evidence_ids=[str(item.get("variant_id")) for item in variants if item.get("variant_id")],
            rationale=(
                "The supplied movement variants use the seed-cell boundary rather than "
                "the explicit roundabout ring gates, so they are retained as evidence but "
                "cannot classify the roundabout movement graph."
            ),
        )
    comparison = dict(movement_hypotheses.get("variant_comparison", {}))
    if comparison.get("status") == "review_required":
        value = "contradictory"
        grade = "contradicted"
    elif variants and all(item.get("generation_status") == "pass" for item in variants):
        value = "partial"
        grade = "rule_derived"
    else:
        value = "unknown"
        grade = "unknown"
    return _dimension(
        "movement_graph_status",
        value,
        grade=grade,
        decision="review_required",
        evidence_ids=[str(item.get("variant_id")) for item in variants if item.get("variant_id")],
        rationale=(
            "OSM-derived variants remain partial until lane connectivity, restrictions, access, "
            "and all applicable ingress-to-egress paths are closed."
        ),
    )


def _channelization_features(
    patch: OSMPatch,
    *,
    path_node_ids: set[str],
) -> list[dict[str, Any]]:
    features: list[dict[str, Any]] = []
    island_node_ids = sorted(
        node_id
        for node_id in path_node_ids
        if node_id in patch.nodes and patch.nodes[node_id].tags.get("crossing:island") == "yes"
    )
    for node_id in island_node_ids:
        features.append(
            _feature(
                "median_refuge",
                evidence_ids=[node_id],
                attachments={"source_node_ids": [node_id]},
                rationale="Explicit OSM crossing:island=yes tag inside the bounded cell.",
            )
        )
    unique: dict[str, dict[str, Any]] = {}
    for item in features:
        key = _stable_digest(item)
        unique[key] = item
    return [unique[key] for key in sorted(unique)]


def _evidence_features(
    patch: OSMPatch,
    approaches: list[dict[str, Any]],
    *,
    link_way_ids: list[str],
    topology: Mapping[str, Any],
    path_node_ids: set[str],
) -> list[dict[str, Any]]:
    records = []
    for node_id in sorted(map(str, path_node_ids)):
        if node_id in patch.nodes and patch.nodes[node_id].tags.get("traffic_calming") == "island":
            records.append(
                {
                    "feature": "island_candidate",
                    "source_node_ids": [node_id],
                    "status": "observed",
                    "interpretation_gate": "review_required",
                    "alternatives": ["splitter_island", "median_refuge", "traffic_calming_island"],
                    "claim_boundary": (
                        "traffic_calming=island does not identify a canonical splitter "
                        "or refuge function without geometry and movement evidence."
                    ),
                }
            )
    for way_id in link_way_ids:
        records.append(
            {
                "feature": "auxiliary_link_candidate",
                "source_way_ids": [way_id],
                "status": "observed",
                "interpretation_gate": "review_required",
                "claim_boundary": (
                    "An OSM *_link way is auxiliary-road evidence; it does not prove a "
                    "slip bypass or a merge/diverge core."
                ),
            }
        )
    for approach in approaches:
        raw = approach.get("incoming_turn_lanes_raw")
        if raw:
            records.append(
                {
                    "feature": "turn_lane_marking",
                    "arm_id": str(approach.get("physical_approach_id")),
                    "raw_value": str(raw),
                    "status": "observed",
                    "interpretation_gate": "review_required",
                    "claim_boundary": (
                        "turn:lanes is marking evidence and does not by itself prove a "
                        "turn bay or complete legal lane connectivity."
                    ),
                }
            )
        incoming = int(approach.get("incoming_lane_count", 0))
        outgoing = int(approach.get("outgoing_lane_count", 0))
        if incoming and outgoing and incoming != outgoing:
            records.append(
                {
                    "feature": "lane_count_transition",
                    "arm_id": str(approach.get("physical_approach_id")),
                    "incoming_lane_count": incoming,
                    "outgoing_lane_count": outgoing,
                    "status": "observed",
                    "interpretation_gate": "review_required",
                    "claim_boundary": (
                        "A directional lane-count change does not prove a flare, fanout, "
                        "turn bay, or channelization geometry."
                    ),
                }
            )
        if approach.get("member_count") == 2 and set(map(str, approach.get("flow_roles", ()))) == {
            "incoming",
            "outgoing",
        }:
            records.append(
                {
                    "feature": "directional_carriageway_pair",
                    "arm_id": str(approach.get("physical_approach_id")),
                    "source_way_ids": sorted(map(str, approach.get("source_way_ids", ()))),
                    "status": "rule_derived",
                    "interpretation_gate": "review_required",
                    "alternatives": ["divided_pair", "one_way_pair"],
                }
            )
    for connector in topology.get("storage_capable_connectors", ()):
        records.append(
            {
                "feature": "storage_capable_branch_connector",
                "from_branch_node_id": connector.get("from_branch_node_id"),
                "to_branch_node_id": connector.get("to_branch_node_id"),
                "graph_distance_m": connector.get("graph_distance_m"),
                "status": "rule_derived",
                "interpretation_gate": "review_required",
                "claim_boundary": (
                    "The emitted length threshold identifies storage evidence; it does "
                    "not prove two physical cores or canonical channelization."
                ),
            }
        )
    return sorted(records, key=_stable_digest)


def _facility_modes(
    approaches: list[dict[str, Any]],
    protected: Mapping[str, Any],
) -> list[str]:
    modes = set()
    if approaches:
        modes.add("motor_vehicle")
    if protected.get("pedestrian_way_ids") or protected.get("crossing_node_ids"):
        modes.add("pedestrian")
    if protected.get("bicycle_way_ids") or protected.get("bicycle_crossing_node_ids"):
        modes.add("bicycle")
    if protected.get("rail_way_ids"):
        modes.add("rail")
    if protected.get("access_way_ids"):
        modes.add("access")
    return sorted(modes)


def _merge_protected_features(
    *feature_sets: Mapping[str, Any],
) -> dict[str, list[str]]:
    keys = sorted({str(key) for features in feature_sets for key in features})
    return {key: sorted({str(value) for features in feature_sets for value in features.get(key, ())}) for key in keys}


def _atomic_core_candidates(
    topology: Mapping[str, Any],
    circulation: Mapping[str, Any],
) -> list[dict[str, Any]]:
    if circulation["value"] in {"nontraversable_ring", "traversable_mini"}:
        return [
            {
                "core_id": f"ring-core-{_stable_digest(circulation.get('evidence_ids', []))[:16]}",
                "interaction_kind": "cross_and_turn",
                "source_node_ids": [],
                "status": "candidate",
            }
        ]
    return [
        {
            "core_id": f"atomic-core-{node_id}",
            "interaction_kind": "cross_and_turn",
            "source_node_ids": [str(node_id)],
            "status": "candidate",
        }
        for node_id in sorted(map(str, topology.get("branch_node_ids", ())))
    ]


def _review_reasons(
    *,
    physical_cell: Mapping[str, Any],
    dimensions: Mapping[str, Mapping[str, Any]],
    arm_model: Mapping[str, Any],
    additional_risks: Any = (),
) -> list[str]:
    reasons = [f"physical_cell:{item}" for item in physical_cell.get("risks", ())]
    reasons.extend(f"semantic_arms:{item}" for item in additional_risks)
    reasons.extend(f"unknown_dimension:{name}" for name, record in dimensions.items() if record["value"] == "unknown")
    if arm_model["arm_count"] in {3, 4} and arm_model["opposition_pairing_status"] in {"unknown", "partial"}:
        reasons.append("semantic_arm_opposition_pairing_unresolved")
    return sorted(set(reasons))


def _feature(
    feature_type: str,
    *,
    evidence_ids: list[str],
    attachments: Mapping[str, Any],
    rationale: str,
) -> dict[str, Any]:
    if feature_type not in _CHANNELIZATION_TYPES:
        raise ValueError(f"Unknown channelization type: {feature_type}")
    return {
        "type": feature_type,
        "status": "rule_derived",
        "decision": "review_required",
        "evidence_ids": sorted(map(str, evidence_ids)),
        "attachments": dict(attachments),
        "rationale": rationale,
    }


def _dimension(
    dimension: str,
    value: str,
    *,
    grade: str,
    decision: str,
    evidence_ids: list[str] | None = None,
    rationale: str,
    alternatives: list[str] | None = None,
) -> dict[str, Any]:
    if dimension not in _ENUMS:
        raise ValueError(f"Unknown intersection dimension: {dimension}")
    if value not in _ENUMS[dimension]:
        raise ValueError(f"Invalid {dimension} value: {value}")
    if grade not in _EVIDENCE_GRADES:
        raise ValueError(f"Invalid evidence grade: {grade}")
    if decision not in _DECISIONS:
        raise ValueError(f"Invalid decision: {decision}")
    invalid_alternatives = sorted(set(alternatives or ()) - set(_ENUMS[dimension]))
    if invalid_alternatives:
        raise ValueError(f"Invalid {dimension} alternatives: {invalid_alternatives}")
    return {
        "value": value,
        "status": grade,
        "decision": decision,
        "evidence_ids": sorted(map(str, evidence_ids or ())),
        "rationale": rationale,
        "alternatives": sorted(set(alternatives or ())),
    }


def _alias_alternatives(alias: str, arm_count: int) -> list[str]:
    if arm_count == 3:
        return sorted({"T3", "Y3", "irregular_3"} - {alias})
    if arm_count == 4:
        return sorted({"X4", "irregular_4"} - {alias})
    return []


def _arm_count_class(count: int) -> str:
    if count >= 5:
        return "A5_plus"
    if 0 <= count <= 4:
        return f"A{count}"
    return "unknown"


def _finite_float(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _circular_separation(first: float, second: float) -> float:
    delta = abs((float(first) - float(second)) % 360.0)
    return min(delta, 360.0 - delta)


def _stable_digest(payload: Any) -> str:
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()
