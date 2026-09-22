from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .clean import build_intersection_ir
from .hypothesis import build_teacher_free_intersection_hypothesis
from .schema import Approach, Movement, PatchSeed


def build_intersection_review_proposal(
    *,
    osm_file: Path,
    seed_node_id: str,
    expected_topology_type: str,
    expected_vehicle_approach_count: int,
    expected_legal_vehicle_movement_count: int,
    reviewed_source_junction_ids: tuple[str, ...],
    traffic_side: str = "right",
) -> dict[str, Any]:
    """Build independent OSM hypotheses and compare them to reviewed scope.

    The reviewed source IDs are used only after both hypotheses are generated.
    They do not influence core expansion, signal anchors, shortest paths,
    approach inference, movement inference, or control inference.
    """

    source = osm_file.resolve(strict=True)
    ir = build_intersection_ir(
        source,
        source.parent,
        PatchSeed(osm_node_id=seed_node_id),
    )
    teacher_free_hypothesis = build_teacher_free_intersection_hypothesis(
        ir.osm_patch,
        seed_node_id=seed_node_id,
        traffic_side=traffic_side,
        seed_authority="caller_provided_anchor_only",
    )
    signal_anchor_cell = teacher_free_hypothesis["physical_cell"]
    movement_hypotheses = teacher_free_hypothesis["vehicle_movement_hypotheses"]

    approaches, stable_approach_ids = _approach_records(ir.approaches)
    legal_vehicle_movements = [
        movement
        for movement in ir.movement_matrix.movements
        if movement.allowed and "passenger" in movement.allowed_modes
    ]
    movement_records = [_movement_record(movement, stable_approach_ids) for movement in legal_vehicle_movements]
    vehicle_approach_count = sum(
        approach.is_vehicle_approach and "passenger" in approach.allowed_modes for approach in ir.approaches
    )
    physical_approach_count = len(signal_anchor_cell["physical_approaches"])
    physical_topology_type = {
        3: "T3",
        4: "X4",
    }.get(physical_approach_count, "complex")

    fixed_radius_membership = _membership_comparison(
        proposed_ids=tuple(ir.core.core_osm_node_ids),
        reviewed_ids=reviewed_source_junction_ids,
    )
    signal_anchor_membership = _membership_comparison(
        proposed_ids=tuple(signal_anchor_cell["proposed_source_junction_ids"]),
        reviewed_ids=reviewed_source_junction_ids,
    )
    physical_topology_alignment = _value_alignment(
        expected=expected_topology_type,
        observed=physical_topology_type,
    )
    legacy_topology_alignment = _value_alignment(
        expected=expected_topology_type,
        observed=ir.core.topology_type,
    )
    physical_approach_alignment = _value_alignment(
        expected=expected_vehicle_approach_count,
        observed=physical_approach_count,
    )
    legacy_approach_alignment = _value_alignment(
        expected=expected_vehicle_approach_count,
        observed=vehicle_approach_count,
    )
    legacy_movement_alignment = _value_alignment(
        expected=expected_legal_vehicle_movement_count,
        observed=len(legal_vehicle_movements),
    )
    movement_variant_alignments = [
        {
            "variant_id": variant["variant_id"],
            "method": variant["method"],
            **_value_alignment(
                expected=expected_legal_vehicle_movement_count,
                observed=variant["atomic_movement_count"],
            ),
        }
        for variant in movement_hypotheses["variants"]
    ]
    movement_hypothesis_alignment = {
        "status": (
            "match"
            if movement_hypotheses["variant_comparison"]["status"] == "exact"
            and all(item["status"] == "match" for item in movement_variant_alignments)
            else "review_required"
        ),
        "expected": expected_legal_vehicle_movement_count,
        "observed_by_variant": {item["method"]: item["observed"] for item in movement_variant_alignments},
        "variant_set_status": movement_hypotheses["variant_comparison"]["status"],
    }
    control_alignment = _value_alignment(
        expected="traffic_light",
        observed=ir.control.control_type,
    )

    unresolved_reasons = []
    if fixed_radius_membership["status"] != "exact":
        unresolved_reasons.append("fixed_radius_membership_disagrees_with_reviewed_scope")
    if signal_anchor_membership["status"] != "exact":
        unresolved_reasons.append("signal_anchor_membership_disagrees_with_reviewed_scope")
    if set(ir.core.core_osm_node_ids) != set(signal_anchor_cell["proposed_source_junction_ids"]):
        unresolved_reasons.append("physical_cell_hypotheses_disagree")
    if physical_topology_alignment["status"] != "match":
        unresolved_reasons.append("physical_topology_type_disagrees")
    if physical_approach_alignment["status"] != "match":
        unresolved_reasons.append("physical_approach_count_disagrees")
    if legacy_topology_alignment["status"] != "match":
        unresolved_reasons.append("legacy_ir_topology_disagrees")
    if legacy_approach_alignment["status"] != "match":
        unresolved_reasons.append("legacy_ir_vehicle_approach_count_disagrees")
    if movement_hypothesis_alignment["status"] != "match":
        unresolved_reasons.append("movement_hypotheses_disagree_or_count_mismatch")
    if legacy_movement_alignment["status"] != "match":
        unresolved_reasons.append("legacy_ir_legal_vehicle_movement_count_disagrees")
    if control_alignment["status"] != "match":
        unresolved_reasons.append("control_type_disagrees")
    unresolved_reasons.extend(f"signal_anchor:{risk}" for risk in signal_anchor_cell["risks"])
    unresolved_reasons.extend(f"movement_hypotheses:{reason}" for reason in movement_hypotheses["unresolved_reasons"])

    semantic_alignment = all(
        item["status"] == "match"
        for item in (
            physical_topology_alignment,
            physical_approach_alignment,
            movement_hypothesis_alignment,
            control_alignment,
        )
    )
    if movement_hypotheses["variant_comparison"]["status"] != "exact":
        recommendation = "movement_variants_disagree_review_required"
    elif signal_anchor_membership["status"] == "exact" and semantic_alignment:
        recommendation = "prefer_signal_anchor_cell_for_human_review"
    elif (
        signal_anchor_membership["status"] == "exact"
        and physical_topology_alignment["status"] == "match"
        and physical_approach_alignment["status"] == "match"
    ):
        recommendation = "cell_and_approaches_supported_but_movements_unresolved"
    elif signal_anchor_membership["status"] == "exact":
        recommendation = "cell_membership_supported_but_semantics_unresolved"
    else:
        recommendation = "compare_physical_cell_hypotheses_manually"

    payload = {
        "schema": "torii.intersection-review-proposal/v1",
        "generation_status": "pass",
        "disposition": "review" if unresolved_reasons else "suggest",
        "automatic_promotion_gate": "blocked",
        "source_osm": {
            "path": source.name,
            "sha256": _file_sha256(source),
        },
        "seed_node_id": seed_node_id,
        "teacher_free_generation": {
            "hypothesis_id": teacher_free_hypothesis["hypothesis_id"],
            "generation_status": teacher_free_hypothesis["generation_status"],
            "disposition": teacher_free_hypothesis["disposition"],
            "seed_authority": teacher_free_hypothesis["seed_authority"],
            "forbidden_generation_inputs": teacher_free_hypothesis["forbidden_generation_inputs"],
            "candidate_dag": teacher_free_hypothesis["candidate_dag"],
            "claim_boundary": teacher_free_hypothesis["claim_boundary"],
        },
        "physical_cell_hypotheses": {
            "fixed_radius_ir": {
                "method": "connected_core_candidates_within_fixed_radius",
                "core_id": ir.core.core_id,
                "radius_m": ir.core.core_radius_m,
                "topology_type": ir.core.topology_type,
                "confidence": ir.core.confidence,
                "proposed_source_junction_ids": sorted(ir.core.core_osm_node_ids),
                "membership_comparison": fixed_radius_membership,
            },
            "signal_anchor_cell": {
                **signal_anchor_cell,
                "membership_comparison": signal_anchor_membership,
            },
        },
        "vehicle_movement_hypotheses": movement_hypotheses,
        "semantic_ir": {
            "topology_type": ir.core.topology_type,
            "core_confidence": ir.core.confidence,
            "approach_count": len(ir.approaches),
            "vehicle_approach_count": vehicle_approach_count,
            "approaches": approaches,
            "inferred_movement_count": ir.movement_matrix.inferred_movement_count,
            "legal_movement_count": ir.movement_matrix.legal_movement_count,
            "legal_vehicle_movement_count": len(legal_vehicle_movements),
            "legal_vehicle_movements": movement_records,
            "restriction_blocked_count": (ir.movement_matrix.restriction_blocked_count),
            "restriction_warnings": ir.movement_matrix.restriction_warnings,
            "control": {
                "control_type": ir.control.control_type,
                "source": ir.control.source,
                "proposed_tls_id": ir.control.tls_id,
                "confidence": ir.control.confidence,
                "synthetic_phase_count": len(ir.control.phases),
                "timing_authority": "none",
            },
        },
        "reviewed_comparison": {
            "role": ("post-generation evaluation only; never an input to either physical-cell hypothesis"),
            "reviewed_source_junction_ids": sorted(set(reviewed_source_junction_ids)),
            "topology": physical_topology_alignment,
            "physical_approach_count": physical_approach_alignment,
            "legacy_ir_topology": legacy_topology_alignment,
            "legacy_ir_vehicle_approach_count": legacy_approach_alignment,
            "legal_vehicle_movement_count": movement_hypothesis_alignment,
            "movement_variant_count_alignments": movement_variant_alignments,
            "legacy_ir_legal_vehicle_movement_count": legacy_movement_alignment,
            "control_type": control_alignment,
        },
        "unresolved_reasons": sorted(set(unresolved_reasons)),
        "machine_recommendation": recommendation,
        "review_questions": _review_questions(
            signal_anchor_membership=signal_anchor_membership,
            semantic_alignment=semantic_alignment,
            physical_approaches=signal_anchor_cell["physical_approaches"],
        ),
        "claim_boundary": (
            "The proposal is OSM-only evidence for human review. It does not "
            "authorize joining nodes, rewiring lanes, or changing TLS phases."
        ),
    }
    return {
        **payload,
        "proposal_id": f"intersection-{_stable_digest(payload)[:20]}",
    }


def _approach_records(
    approaches: list[Approach],
) -> tuple[list[dict[str, Any]], dict[str, str]]:
    records = []
    stable_ids: dict[str, str] = {}
    for approach in approaches:
        identity_payload = {
            "source_way_ids": sorted(approach.source_way_ids),
            "corridor_extension_way_ids": sorted(approach.corridor_extension_way_ids),
            "bearing_from_core": round(approach.bearing_from_core, 6),
            "endpoint_xy": _rounded_point(approach.endpoint_xy),
            "allowed_modes": sorted(approach.allowed_modes),
            "has_incoming_vehicle_flow": approach.has_incoming_vehicle_flow,
            "has_outgoing_vehicle_flow": approach.has_outgoing_vehicle_flow,
        }
        stable_id = f"approach-{_stable_digest(identity_payload)[:16]}"
        stable_ids[approach.approach_id] = stable_id
        records.append(
            {
                "stable_approach_id": stable_id,
                "source_ir_approach_id": approach.approach_id,
                "role": approach.role,
                **identity_payload,
                "road_name": approach.road_name,
                "highway_class": approach.highway_class,
                "incoming_lane_count": approach.incoming_lane_count,
                "outgoing_lane_count": approach.outgoing_lane_count,
                "is_vehicle_approach": approach.is_vehicle_approach,
                "is_support_only": approach.is_support_only,
                "turn_lanes_raw": approach.turn_lanes_raw,
                "direction_evidence": approach.direction_evidence,
            }
        )
    return records, stable_ids


def _movement_record(
    movement: Movement,
    stable_approach_ids: dict[str, str],
) -> dict[str, Any]:
    payload = {
        "from_stable_approach_id": stable_approach_ids[movement.from_approach_id],
        "to_stable_approach_id": stable_approach_ids[movement.to_approach_id],
        "turn": movement.turn,
        "from_lane_indices": movement.from_lane_indices,
        "to_lane_indices": movement.to_lane_indices,
        "allowed_modes": sorted(movement.allowed_modes),
    }
    return {
        "stable_movement_id": f"movement-{_stable_digest(payload)[:16]}",
        "source_ir_movement_id": movement.movement_id,
        **payload,
        "confidence": movement.confidence,
        "evidence": movement.evidence,
    }


def _membership_comparison(
    *,
    proposed_ids: tuple[str, ...],
    reviewed_ids: tuple[str, ...],
) -> dict[str, Any]:
    proposed = set(map(str, proposed_ids))
    reviewed = set(map(str, reviewed_ids))
    matched = sorted(proposed & reviewed)
    missing = sorted(reviewed - proposed)
    extra = sorted(proposed - reviewed)
    return {
        "status": "exact" if not missing and not extra else "review_required",
        "matched_node_ids": matched,
        "missing_reviewed_node_ids": missing,
        "extra_proposed_node_ids": extra,
        "reviewed_coverage": (round(len(matched) / len(reviewed), 6) if reviewed else 1.0),
    }


def _value_alignment(*, expected: Any, observed: Any) -> dict[str, Any]:
    return {
        "status": "match" if expected == observed else "review_required",
        "expected": expected,
        "observed": observed,
    }


def _review_questions(
    *,
    signal_anchor_membership: dict[str, Any],
    semantic_alignment: bool,
    physical_approaches: list[dict[str, Any]],
) -> list[str]:
    questions = []
    if signal_anchor_membership["status"] != "exact":
        questions.append("Which proposed or missing OSM nodes belong to the physical conflict cell?")
    if any(item["grouping_status"] != "pass" for item in physical_approaches):
        questions.append("Which raw boundary ports are lanes of the same physical approach?")
    if not semantic_alignment:
        questions.append("After fixing the physical cell, what are the legal vehicle approaches and movements?")
    if not questions:
        questions.append("Does independent map evidence support this physical cell and movement set?")
    return questions


def _rounded_point(value: tuple[float, float] | None) -> list[float] | None:
    if value is None:
        return None
    return [round(value[0], 3), round(value[1], 3)]


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _stable_digest(payload: Any) -> str:
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()
