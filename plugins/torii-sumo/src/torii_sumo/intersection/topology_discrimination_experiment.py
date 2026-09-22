from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping
from xml.etree import ElementTree as ET

from torii_sumo.core.artifact_io import write_text_atomic

from .schema import OSMPatch
from .topology_evidence import (
    assess_topology_hypothesis,
    build_topology_evidence,
)


TOPOLOGY_HYPOTHESES = (
    "preserve_split_shared_controller",
    "merge_physical_cell",
    "partial_internal_repair",
)

EXPERIMENT_PROFILE = {
    "profile_id": "teacher-free-topology-discrimination-v1",
    "selection_authority": "none_preregistered_parallel_hypothesis_arms",
    "applicability": {
        "classification": "vehicle_intersection",
        "physical_approach_counts": [3, 4],
        "traffic_sides": ["right"],
        "semantic_equivalence_class_count": 1,
    },
    "netconvert": {
        "projection": "utm",
        "no_turnarounds": True,
        "osm_all_attributes": True,
        "global_tls_join": False,
        "reason": "topology arms own only their explicit hash-bound node patch",
    },
    "audit": {
        "endpoint_tolerance_m": 2.0,
        "normalized_lane_rank_tolerance": 0.5,
    },
    "runtime": {
        "departure_interval_s": 8,
        "end_time_s": 900,
    },
    "tls_policy": {
        "program_source": "netconvert_regeneration_for_falsification_only",
        "custom_phase_topology": "blocked",
        "field_timing_reconstruction": "blocked",
        "automatic_topology_selection": "blocked",
        "automatic_promotion": "blocked",
    },
}


def build_topology_discrimination_contract(
    discovery: Mapping[str, Any],
    patch: OSMPatch,
) -> dict[str, Any]:
    """Freeze all topology arms before any candidate artifact is written."""

    assessments = [
        _assess_candidate(
            candidate,
            patch=patch,
            traffic_side=str(discovery.get("traffic_side", "unknown")),
        )
        for candidate in discovery.get("candidates", ())
        if candidate.get("classification", {}).get("kind") == "vehicle_intersection"
    ]
    base_eligible = [
        item for item in assessments if item["base_preflight_status"] == "pass"
    ]
    global_blockers: list[str] = []
    if len(base_eligible) > 1:
        global_blockers.append("multiple_preflight_eligible_vehicle_cells")
    elif assessments and not base_eligible:
        global_blockers.append("no_vehicle_cell_passes_base_preflight")

    selected = base_eligible[0] if len(base_eligible) == 1 else None
    ready_plans = []
    if selected is not None and not global_blockers:
        ready_plans = [
            _candidate_plan(selected, arm)
            for arm in selected["topology_arms"]
            if arm["pre_materialization_status"] == "ready"
        ]
        if not ready_plans:
            global_blockers.append("all_topology_hypotheses_falsified_pre_materialization")

    if not assessments:
        status = "not_applicable"
    elif selected is not None and ready_plans and not global_blockers:
        status = "ready"
    else:
        status = "blocked"

    payload = {
        "schema": "torii.teacher-free-topology-discrimination-contract/v1",
        "workflow_state": "CANDIDATE_PLANNED" if status == "ready" else "HYPOTHESES_READY",
        "status": status,
        "write_candidate_authorized": status == "ready",
        "automatic_topology_selection": False,
        "automatic_promotion_gate": "blocked",
        "field_timing_reconstruction": False,
        "manual_scope_input": False,
        "scope_expansion_allowed": False,
        "experiment_profile": EXPERIMENT_PROFILE,
        "discovery_id": discovery.get("discovery_id"),
        "source_osm": discovery.get("source_osm"),
        "vehicle_candidate_count": len(assessments),
        "base_eligible_vehicle_candidate_count": len(base_eligible),
        "candidate_assessments": assessments,
        "materialization_blockers": sorted(set(global_blockers)),
        "candidate_plans": ready_plans if status == "ready" else [],
        "topology_hypotheses": list(TOPOLOGY_HYPOTHESES),
        "forbidden_inputs": [
            "teacher_network",
            "teacher_coordinates",
            "reviewed_scope",
            "reviewed_junction_id",
            "manual_seed_node",
            "manual_scope",
            "expected_topology",
            "expected_approach_count",
            "expected_movement_count",
            "expected_topology_winner",
            "materialized_candidate_network",
            "field_signal_timing",
        ],
        "decision_rule": {
            "zero_machine_feasible": "reject_physical_cell_hypothesis_without_scope_expansion",
            "one_machine_feasible": "suggest_for_human_review",
            "multiple_machine_feasible": "blind_review_required",
            "automatic_selection": False,
        },
        "claim_boundary": (
            "Ready authorizes independent immutable falsification variants. It "
            "does not choose a topology, certify TLS timing, or open promotion."
        ),
    }
    return {
        **payload,
        "contract_id": f"topology-contract-{_stable_digest(payload)[:20]}",
    }


def write_topology_node_patch(
    path: Path,
    *,
    contract: Mapping[str, Any],
    candidate_plan_id: str,
) -> None:
    """Write exactly one topology operation from a ready frozen contract."""

    if contract.get("status") != "ready" or not contract.get(
        "write_candidate_authorized"
    ):
        raise ValueError(
            "A topology patch may only be written from a ready discrimination contract."
        )
    plans = [
        item
        for item in contract.get("candidate_plans", ())
        if item.get("candidate_plan_id") == candidate_plan_id
    ]
    if len(plans) != 1:
        raise ValueError("Candidate plan ID does not bind one ready topology arm.")
    plan = plans[0]
    topology = str(plan["topology_hypothesis"])
    source_junction_ids = tuple(map(str, plan["source_junction_ids"]))
    signal_anchor_ids = tuple(map(str, plan["signal_anchor_node_ids"]))
    controller_id = str(plan["target_controller_id"])
    root = ET.Element("nodes")

    if topology == "preserve_split_shared_controller":
        for node_id in sorted(signal_anchor_ids):
            ET.SubElement(
                root,
                "node",
                {"id": node_id, "type": "traffic_light", "tl": controller_id},
            )
    elif topology == "merge_physical_cell":
        if len(source_junction_ids) < 2:
            raise ValueError("Physical-cell merge requires at least two source nodes.")
        ET.SubElement(
            root,
            "join",
            {
                "id": str(plan["target_junction_ids"][0]),
                "tl": controller_id,
                "type": "traffic_light",
                "nodes": " ".join(sorted(source_junction_ids)),
            },
        )
    elif topology == "partial_internal_repair":
        center_id = str(plan["conflict_center_node_id"])
        for node_id in sorted(set(signal_anchor_ids) - {center_id}):
            ET.SubElement(root, "node", {"id": node_id, "type": "priority"})
        ET.SubElement(
            root,
            "node",
            {"id": center_id, "type": "traffic_light", "tl": controller_id},
        )
    else:
        raise ValueError(f"Unknown topology hypothesis: {topology}")

    ET.indent(root, space="  ")
    xml = ET.tostring(root, encoding="unicode")
    write_text_atomic(
        path,
        f"<?xml version='1.0' encoding='utf-8'?>\n{xml}\n",
    )


def _assess_candidate(
    candidate: Mapping[str, Any],
    *,
    patch: OSMPatch,
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
    if approach_count not in EXPERIMENT_PROFILE["applicability"][
        "physical_approach_counts"
    ]:
        blockers.append("physical_approach_count_outside_preregistered_domain")
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
    representative = _representative_variant(movements, semantic_classes)
    if representative is None:
        blockers.append("movement_variant_representative_unavailable")
        movement_metrics = None
    else:
        movement_metrics = _movement_metrics(representative)

    topology_evidence = build_topology_evidence(patch, physical_cell)
    topology_arms = []
    for topology_hypothesis in TOPOLOGY_HYPOTHESES:
        dag_nodes = [
            item
            for item in candidate_dag.get("nodes", ())
            if item.get("node_kind") == "candidate_variant"
            and item.get("topology_hypothesis") == topology_hypothesis
            and item.get("semantic_class_id") == semantic_class_id
        ]
        arm_blockers = list(blockers)
        if len(dag_nodes) != 1:
            arm_blockers.append("topology_candidate_dag_node_not_unique")
            dag_candidate_id = None
        else:
            dag_candidate_id = dag_nodes[0].get("candidate_id")
            if dag_nodes[0].get("candidate_status") != "review_required" or dag_nodes[
                0
            ].get("blockers"):
                arm_blockers.append("topology_candidate_dag_node_blocked")
        evidence_assessment = assess_topology_hypothesis(
            topology_evidence,
            topology_hypothesis,
        )
        arm_blockers.extend(evidence_assessment["materialization_blockers"])
        topology_arms.append(
            {
                **evidence_assessment,
                "candidate_dag_node_id": dag_candidate_id,
                "pre_materialization_status": (
                    "ready" if not arm_blockers else "blocked"
                ),
                "pre_materialization_blockers": sorted(set(arm_blockers)),
            }
        )

    pedestrian_audits = list(candidate.get("pedestrian_facility_audits", ()))
    protected_review_dimensions = []
    if pedestrian_audits:
        protected_review_dimensions.append(
            "pedestrian_model_phase_and_runtime_closure_unverified"
        )
    return {
        "discovered_candidate_id": candidate.get("candidate_id"),
        "canonical_seed_node_id": candidate.get("canonical_seed_selection", {}).get(
            "selected_node_id"
        ),
        "seed_authority": hypothesis.get("seed_authority"),
        "classification": candidate.get("classification"),
        "physical_cell_hypothesis_id": physical_cell.get("hypothesis_id"),
        "source_junction_ids": sorted(
            map(str, physical_cell.get("proposed_source_junction_ids", ()))
        ),
        "signal_anchor_node_ids": sorted(
            map(str, physical_cell.get("signal_anchor_node_ids", ()))
        ),
        "boundary_port_ids": sorted(
            str(item.get("boundary_port_id"))
            for item in physical_cell.get("raw_boundary_ports", ())
        ),
        "physical_approach_count": approach_count,
        "semantic_class_id": semantic_class_id,
        "candidate_dag_id": candidate_dag.get("candidate_dag_id"),
        "movement_metrics": movement_metrics,
        "topology_evidence": topology_evidence,
        "topology_arms": topology_arms,
        "pedestrian_facility_audit_count": len(pedestrian_audits),
        "protected_review_dimensions": protected_review_dimensions,
        "base_preflight_status": "pass" if not blockers else "blocked",
        "base_preflight_blockers": sorted(set(blockers)),
        "automatic_topology_selection": False,
        "automatic_promotion_gate": "blocked",
    }


def _candidate_plan(
    assessment: Mapping[str, Any],
    arm: Mapping[str, Any],
) -> dict[str, Any]:
    topology = str(arm["topology_hypothesis"])
    identity = {
        "profile_id": EXPERIMENT_PROFILE["profile_id"],
        "discovered_candidate_id": assessment["discovered_candidate_id"],
        "candidate_dag_id": assessment["candidate_dag_id"],
        "candidate_dag_node_id": arm["candidate_dag_node_id"],
        "topology_hypothesis": topology,
        "topology_evidence_id": assessment["topology_evidence"][
            "topology_evidence_id"
        ],
    }
    suffix = _stable_digest(identity)[:16]
    source_ids = list(assessment["source_junction_ids"])
    center_id = assessment["topology_evidence"].get(
        "unique_conflict_center_node_id"
    )
    if topology == "merge_physical_cell":
        target_junction_ids = [f"torii_cell_{suffix}"]
        expected_tls_junction_ids = list(target_junction_ids)
        retained_source_junction_ids: list[str] = []
        removed_source_junction_ids = source_ids
        operation = "join_physical_cell_nodes"
    elif topology == "partial_internal_repair":
        if center_id is None:
            raise ValueError("Ready partial-repair arm is missing its conflict center.")
        target_junction_ids = source_ids
        expected_tls_junction_ids = [str(center_id)]
        retained_source_junction_ids = source_ids
        removed_source_junction_ids = []
        operation = "demote_inline_signal_heads_promote_conflict_center"
    else:
        target_junction_ids = source_ids
        expected_tls_junction_ids = list(assessment["signal_anchor_node_ids"])
        retained_source_junction_ids = source_ids
        removed_source_junction_ids = []
        operation = "bind_preserved_signal_nodes_to_shared_controller"
    return {
        **identity,
        "candidate_plan_id": f"topology-plan-{suffix}",
        "selection_kind": "parallel_preregistered_falsification_arm",
        "selection_is_topology_truth_claim": False,
        "source_junction_ids": source_ids,
        "signal_anchor_node_ids": list(assessment["signal_anchor_node_ids"]),
        "conflict_center_node_id": center_id,
        "target_junction_ids": target_junction_ids,
        "target_controller_id": f"torii_tls_{suffix}",
        "expected_tls_junction_ids": expected_tls_junction_ids,
        "retained_source_junction_ids": retained_source_junction_ids,
        "removed_source_junction_ids": removed_source_junction_ids,
        "boundary_port_ids": list(assessment["boundary_port_ids"]),
        "physical_approach_count": assessment["physical_approach_count"],
        "semantic_class_id": assessment["semantic_class_id"],
        "movement_metrics": assessment["movement_metrics"],
        "topology_evidence_assessment": dict(arm),
        "protected_review_dimensions": sorted(
            set(assessment["protected_review_dimensions"])
            | set(arm["protected_review_dimensions"])
        ),
        "declared_operation": operation,
        "source_mutation": False,
        "rollback_strategy": "omit_topology_patch_and_rebuild_from_hash_bound_osm",
        "automatic_topology_selection": False,
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
    turn_symbols = {"right": "r", "straight": "s", "left": "l", "uturn": "t"}
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


def _stable_digest(payload: Any) -> str:
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()
