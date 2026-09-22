from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any


TIER_NAMES = (
    "strict_valid",
    "reference_aligned",
    "manual_quality_reviewed",
)


def evaluate_claim_tiers(
    *,
    gate_status: Mapping[str, Any] | None,
    review_locations: Sequence[Mapping[str, Any]] = (),
    review_decisions: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Evaluate the three explicit Torii network-quality claim tiers.

    The evaluator is deliberately stricter than the legacy ``claim_status``
    field.  A routeability pass does not imply reference parity, and a review
    overlay does not imply that a human has accepted every marked location.
    """

    gates = {str(key): str(value) for key, value in (gate_status or {}).items()}
    strict_required = (
        "network_build",
        "connectivity",
        "road_connectivity_parity",
        "connection_semantics_parity",
        "routeability_audit",
    )
    reference_required = strict_required + (
        "reference_visual_detail",
        "reference_join_audit",
        "tls_semantics_parity",
        "tls_reality_audit",
        "tls_aggregation",
        "reference_hierarchy_audit",
        "reference_scope_audit",
        "topology_audit",
    )
    # Reference-matched workflows may add this gate once a geographic teacher
    # scope variant is materialized.  Keep it optional for non-reference
    # workflows and for legacy callers that do not compare a teacher network.
    if "reference_bbox_scope" in gates:
        reference_required = reference_required + ("reference_bbox_scope",)
    # Source-way alignment is optional for legacy/non-reference workflows, but
    # once a reference-matched run emits this gate it is part of the claim for
    # reference-aligned quality.  A marker overlay alone cannot make it pass.
    if "reference_road_alignment" in gates:
        reference_required = reference_required + ("reference_road_alignment",)

    def gate_result(required: Sequence[str]) -> dict[str, Any]:
        failed = [name for name in required if gates.get(name) != "pass"]
        return {
            "status": "pass" if not failed else "fail",
            "required_gates": list(required),
            "failed_gates": failed,
        }

    strict_result = gate_result(strict_required)
    reference_result = gate_result(reference_required)

    location_ids = {
        str(location.get("location_id", ""))
        for location in review_locations
        if str(location.get("location_id", ""))
    }
    supplied_decisions = {}
    if isinstance(review_decisions, Mapping):
        for item in review_decisions.get("locations", []) or []:
            if isinstance(item, Mapping) and str(item.get("location_id", "")):
                supplied_decisions[str(item["location_id"])] = item
    missing_decisions = sorted(location_ids - set(supplied_decisions))
    pending_decisions = sorted(
        location_id
        for location_id, item in supplied_decisions.items()
        if str(item.get("decision", "pending")) not in {"approved", "rejected_with_evidence"}
    )
    missing_evidence = sorted(
        location_id
        for location_id, item in supplied_decisions.items()
        if location_id in location_ids
        and str(item.get("decision", "")) in {"approved", "rejected_with_evidence"}
        and not str(item.get("evidence", "")).strip()
    )
    decisions_complete = not missing_decisions and not pending_decisions and not missing_evidence
    if strict_result["status"] != "pass":
        manual_status = "fail"
    elif not decisions_complete:
        manual_status = "pending"
    else:
        manual_status = "pass"
    manual_result = {
        "status": manual_status,
        "required_tier": "strict_valid",
        "basis": "strict_valid_plus_explicit_review_decisions",
        "reference_alignment_status": reference_result["status"],
        "review_location_count": len(location_ids),
        "decision_count": len(supplied_decisions),
        "missing_decision_location_ids": missing_decisions,
        "pending_decision_location_ids": pending_decisions,
        "missing_evidence_location_ids": missing_evidence,
        "decision_schema": "approved|rejected_with_evidence plus non-empty evidence",
    }

    highest = "none"
    if strict_result["status"] == "pass":
        highest = "strict_valid"
    if reference_result["status"] == "pass":
        highest = "reference_aligned"
    if manual_result["status"] == "pass":
        highest = "manual_quality_reviewed"
    return {
        "schema_version": 1,
        "tier_order": list(TIER_NAMES),
        "highest_passed_tier": highest,
        "strict_valid": strict_result,
        "reference_aligned": reference_result,
        "manual_quality_reviewed": manual_result,
    }
