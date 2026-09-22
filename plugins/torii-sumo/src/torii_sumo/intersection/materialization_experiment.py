from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping
from xml.etree import ElementTree as ET

from torii_sumo.core.artifact_io import write_text_atomic


EXPERIMENT_PROFILE = {
    "profile_id": "teacher-free-standard-merge-v1",
    "topology_hypothesis": "merge_physical_cell",
    "selection_authority": (
        "preregistered_experiment_arm_independent_of_controller_membership"
    ),
    "applicability": {
        "classification": "vehicle_intersection",
        "physical_approach_counts": [3, 4],
        "traffic_sides": ["right"],
        "semantic_equivalence_class_count": 1,
        "pedestrian_facility_policy": (
            "inventory_and_preserve_as_promotion_blocker_during_experimental_materialization"
        ),
    },
    "netconvert": {
        "projection": "utm",
        "no_turnarounds": True,
        "osm_all_attributes": True,
        "tls_join": True,
        "tls_join_distance_m": 35.0,
    },
    "audit": {
        "endpoint_tolerance_m": 2.0,
        "normalized_lane_rank_tolerance": 0.5,
    },
    "runtime": {
        "departure_interval_s": 8,
        "end_time_s": 600,
    },
    "tls_policy": {
        "controller_source": "netconvert_regeneration_for_audit_only",
        "custom_phase_topology": "blocked",
        "field_timing_reconstruction": "out_of_scope",
        "automatic_promotion": "blocked",
    },
}


def build_preregistered_materialization_contract(
    discovery: Mapping[str, Any],
) -> dict[str, Any]:
    """Plan one teacher-free experimental candidate without benchmark answers.

    The profile is fixed in code and therefore cannot be adapted to a known
    junction.  It may authorize writing an *experimental* derived artifact, but
    never selects a topology as real-world truth or opens promotion.
    """

    assessments = [
        _assess_candidate(candidate, traffic_side=str(discovery.get("traffic_side", "unknown")))
        for candidate in discovery.get("candidates", ())
        if candidate.get("classification", {}).get("kind") == "vehicle_intersection"
    ]
    eligible = [item for item in assessments if item["pre_materialization_status"] == "pass"]
    global_blockers: list[str] = []
    if len(eligible) > 1:
        global_blockers.append("multiple_preflight_eligible_vehicle_cells")
    elif assessments and not eligible:
        global_blockers.append("no_vehicle_cell_passes_pre_materialization_gates")

    if not assessments:
        status = "not_applicable"
    elif len(eligible) == 1 and not global_blockers:
        status = "ready"
    else:
        status = "blocked"

    selected = eligible[0] if status == "ready" else None
    candidate_plan = _candidate_plan(selected) if selected is not None else None
    payload = {
        "schema": "torii.teacher-free-materialization-contract/v1",
        "workflow_state": "CANDIDATE_PLANNED" if status == "ready" else "HYPOTHESES_READY",
        "status": status,
        "write_candidate_authorized": status == "ready",
        "automatic_promotion_gate": "blocked",
        "experiment_profile": EXPERIMENT_PROFILE,
        "discovery_id": discovery.get("discovery_id"),
        "source_osm": discovery.get("source_osm"),
        "vehicle_candidate_count": len(assessments),
        "eligible_vehicle_candidate_count": len(eligible),
        "candidate_assessments": assessments,
        "materialization_blockers": sorted(set(global_blockers)),
        "candidate_plan": candidate_plan,
        "forbidden_inputs": [
            "teacher_network",
            "reviewed_scope",
            "manual_seed_node",
            "expected_topology",
            "expected_approach_count",
            "expected_movement_count",
            "materialized_candidate_network",
        ],
        "claim_boundary": (
            "Ready authorizes one preregistered, immutable experimental merge "
            "variant for falsification. It is not a topology decision, TLS "
            "certification, field-timing claim, or promotion decision."
        ),
    }
    return {
        **payload,
        "contract_id": f"materialization-contract-{_stable_digest(payload)[:20]}",
    }


def write_preregistered_join_patch(
    path: Path,
    *,
    contract: Mapping[str, Any],
) -> None:
    """Write the sole PlainXML edit declared by a ready experiment contract."""

    if contract.get("status") != "ready" or not contract.get("write_candidate_authorized"):
        raise ValueError("A join patch may only be written from a ready materialization contract.")
    plan = contract.get("candidate_plan")
    if not isinstance(plan, Mapping):
        raise ValueError("Ready materialization contract is missing its candidate plan.")
    node_ids = tuple(map(str, plan.get("source_junction_ids", ())))
    if len(node_ids) < 2:
        raise ValueError("A physical-cell join requires at least two source junction IDs.")
    if len(node_ids) != len(set(node_ids)):
        raise ValueError("Physical-cell join contains duplicate source junction IDs.")

    root = ET.Element("nodes")
    ET.SubElement(
        root,
        "join",
        {
            "id": str(plan["target_junction_id"]),
            "tl": str(plan["target_controller_id"]),
            "type": "traffic_light",
            "nodes": " ".join(sorted(node_ids)),
        },
    )
    ET.indent(root, space="  ")
    xml = ET.tostring(root, encoding="unicode")
    write_text_atomic(
        path,
        f"<?xml version='1.0' encoding='utf-8'?>\n{xml}\n",
    )


def _assess_candidate(
    candidate: Mapping[str, Any],
    *,
    traffic_side: str,
) -> dict[str, Any]:
    hypothesis = candidate.get("hypothesis", {})
    physical_cell = hypothesis.get("physical_cell", {})
    movements = hypothesis.get("vehicle_movement_hypotheses", {})
    candidate_dag = hypothesis.get("candidate_dag", {})
    semantic_classes = [
        item
        for item in candidate_dag.get("nodes", ())
        if item.get("node_kind") == "movement_semantic_class"
    ]
    blockers: list[str] = []

    if traffic_side not in EXPERIMENT_PROFILE["applicability"]["traffic_sides"]:
        blockers.append("traffic_side_outside_preregistered_domain")
    if candidate.get("disposition") != "suggest":
        blockers.append("discovered_cell_not_suggest_disposition")
    blockers.extend(
        f"discovery:{reason}" for reason in candidate.get("discovery_blockers", ())
    )
    if hypothesis.get("generation_status") != "pass":
        blockers.append("teacher_free_hypothesis_generation_not_pass")
    if physical_cell.get("risks"):
        blockers.append("physical_cell_has_unresolved_risks")
    approach_count = len(physical_cell.get("physical_approaches", ()))
    if approach_count not in EXPERIMENT_PROFILE["applicability"]["physical_approach_counts"]:
        blockers.append("physical_approach_count_outside_preregistered_domain")
    pedestrian_audits = list(candidate.get("pedestrian_facility_audits", ()))
    protected_review_dimensions = []
    if pedestrian_audits:
        protected_review_dimensions.append(
            "pedestrian_model_phase_and_runtime_closure_unverified"
        )
    if movements.get("generation_status") != "pass":
        blockers.append("vehicle_movement_hypothesis_generation_not_pass")
    if movements.get("variant_comparison", {}).get("status") != "exact":
        blockers.append("movement_semantic_variants_disagree")
    if movements.get("unresolved_reasons"):
        blockers.append("movement_hypotheses_have_unresolved_reasons")
    if movements.get("nested_restriction_ids"):
        blockers.append("nested_turn_restrictions_unresolved")
    if len(semantic_classes) != 1:
        blockers.append("movement_semantic_class_not_unique")

    semantic_class_id = (
        str(semantic_classes[0]["semantic_class_id"])
        if len(semantic_classes) == 1
        else None
    )
    merge_nodes = [
        item
        for item in candidate_dag.get("nodes", ())
        if item.get("node_kind") == "candidate_variant"
        and item.get("topology_hypothesis") == EXPERIMENT_PROFILE["topology_hypothesis"]
        and item.get("semantic_class_id") == semantic_class_id
    ]
    if len(merge_nodes) != 1:
        blockers.append("merge_experiment_dag_node_not_unique")
    elif merge_nodes[0].get("candidate_status") != "review_required" or merge_nodes[0].get("blockers"):
        blockers.append("merge_experiment_dag_node_blocked")

    representative = _representative_variant(movements, semantic_classes)
    if representative is None:
        blockers.append("movement_variant_representative_unavailable")
        movement_metrics = None
    else:
        movement_metrics = _movement_metrics(representative)

    return {
        "discovered_candidate_id": candidate.get("candidate_id"),
        "canonical_seed_node_id": candidate.get("canonical_seed_selection", {}).get("selected_node_id"),
        "seed_authority": hypothesis.get("seed_authority"),
        "classification": candidate.get("classification"),
        "physical_cell_hypothesis_id": physical_cell.get("hypothesis_id"),
        "source_junction_ids": sorted(map(str, physical_cell.get("proposed_source_junction_ids", ()))),
        "boundary_port_ids": sorted(
            str(item.get("boundary_port_id"))
            for item in physical_cell.get("raw_boundary_ports", ())
        ),
        "physical_approach_count": approach_count,
        "pedestrian_facility_audit_count": len(pedestrian_audits),
        "pedestrian_facility_audit_status": candidate.get(
            "pedestrian_facility_audit_status",
            "not_applicable",
        ),
        "protected_review_dimensions": protected_review_dimensions,
        "movement_variant_comparison_status": movements.get("variant_comparison", {}).get("status"),
        "semantic_equivalence_class_count": len(semantic_classes),
        "semantic_class_id": semantic_class_id,
        "candidate_dag_id": candidate_dag.get("candidate_dag_id"),
        "merge_experiment_candidate_id": (
            merge_nodes[0].get("candidate_id") if len(merge_nodes) == 1 else None
        ),
        "movement_metrics": movement_metrics,
        "pre_materialization_status": "pass" if not blockers else "blocked",
        "pre_materialization_blockers": sorted(set(blockers)),
        "automatic_promotion_gate": "blocked",
    }


def _representative_variant(
    movements: Mapping[str, Any],
    semantic_classes: list[Mapping[str, Any]],
) -> Mapping[str, Any] | None:
    if len(semantic_classes) != 1:
        return None
    allowed_ids = set(map(str, semantic_classes[0].get("movement_variant_ids", ())))
    variants = sorted(
        (
            item
            for item in movements.get("variants", ())
            if str(item.get("variant_id")) in allowed_ids
        ),
        key=lambda item: (str(item.get("method", "")), str(item.get("variant_id", ""))),
    )
    return variants[0] if variants else None


def _movement_metrics(variant: Mapping[str, Any]) -> dict[str, Any]:
    movements = list(variant.get("atomic_movements", ()))
    turn_symbols = {
        "right": "r",
        "straight": "s",
        "left": "l",
        "uturn": "t",
    }
    turn_counts: dict[str, int] = {}
    for movement in movements:
        symbol = turn_symbols.get(str(movement.get("turn", "")), "unknown")
        turn_counts[symbol] = turn_counts.get(symbol, 0) + 1
    return {
        "derivation": "sole_movement_semantic_equivalence_class",
        "representative_variant_id": variant.get("variant_id"),
        "representative_method": variant.get("method"),
        "movement_count": len(movements),
        "incoming_approach_count": len(
            {str(item.get("from_physical_approach_id")) for item in movements}
        ),
        "outgoing_approach_count": len(
            {str(item.get("to_physical_approach_id")) for item in movements}
        ),
        "turn_counts": dict(sorted(turn_counts.items())),
        "stable_movement_ids": sorted(
            str(item.get("stable_movement_id")) for item in movements
        ),
    }


def _candidate_plan(assessment: Mapping[str, Any]) -> dict[str, Any]:
    identity = {
        "profile_id": EXPERIMENT_PROFILE["profile_id"],
        "discovered_candidate_id": assessment["discovered_candidate_id"],
        "candidate_dag_id": assessment["candidate_dag_id"],
        "merge_experiment_candidate_id": assessment["merge_experiment_candidate_id"],
    }
    suffix = _stable_digest(identity)[:16]
    return {
        **identity,
        "selection_kind": "preregistered_experiment_arm",
        "selection_is_topology_truth_claim": False,
        "target_junction_id": f"torii_cell_{suffix}",
        "target_controller_id": f"torii_tls_{suffix}",
        "source_junction_ids": assessment["source_junction_ids"],
        "boundary_port_ids": assessment["boundary_port_ids"],
        "physical_approach_count": assessment["physical_approach_count"],
        "semantic_class_id": assessment["semantic_class_id"],
        "movement_metrics": assessment["movement_metrics"],
        "protected_review_dimensions": assessment["protected_review_dimensions"],
        "declared_operation": "plainxml_join_patch_plus_netconvert_regeneration",
        "source_mutation": False,
        "rollback_strategy": "omit_join_patch_and_rebuild_from_hash_bound_osm",
        "automatic_promotion_gate": "blocked",
    }


def _stable_digest(payload: Any) -> str:
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()
