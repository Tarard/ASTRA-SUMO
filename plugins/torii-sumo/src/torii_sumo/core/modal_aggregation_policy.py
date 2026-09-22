from __future__ import annotations

from collections import Counter
from typing import Any

from torii_sumo.road_semantics import classify_modal_role_from_edge

HARD_BLOCKER_ROLE_PRIORITY = ("rail", "ramp", "grade_separated", "roundabout")


def classify_edge_modal_role(edge: dict[str, Any]) -> dict[str, Any]:
    return classify_modal_role_from_edge(edge).as_dict()


def classify_cluster_modal_policy(
    *,
    internal_edges: list[dict[str, Any]],
    boundary_edges: list[dict[str, Any]],
) -> dict[str, Any]:
    roles = [classify_edge_modal_role(edge) for edge in internal_edges + boundary_edges]
    decisions = Counter(role["modal_aggregation_decision"] for role in roles)
    primary_roles = Counter(role["modal_primary_role"] for role in roles)
    risk_flags = sorted({flag for role in roles for flag in role["modal_risk_flags"]})

    if decisions["never_join"]:
        decision = "never_join"
        reason = "hard modal blocker present"
    elif decisions["join_core"] and (decisions["protected_terminal"] or decisions["shape_support"]):
        decision = "review_required"
        reason = "vehicle core mixed with support or terminal infrastructure"
    elif decisions["join_core"]:
        decision = "join_core"
        reason = "modal context is compatible with vehicle-core joining"
    elif decisions["protected_terminal"]:
        decision = "protected_terminal"
        reason = "cluster is dominated by protected terminal infrastructure"
    elif decisions["shape_support"]:
        decision = "shape_support"
        reason = "cluster is modal support infrastructure, not a vehicle core"
    else:
        decision = "review_required"
        reason = "modal context is unknown or ambiguous"

    return {
        "modal_aggregation_decision": decision,
        "modal_primary_role": _cluster_primary_role(roles, decision, primary_roles),
        "modal_review_action": _review_action(decision, decisions),
        "modal_reason": reason,
        "modal_risk_flags": risk_flags,
        "modal_decision_counts": dict(decisions),
        "modal_role_counts": dict(primary_roles),
    }


def _cluster_primary_role(roles: list[dict[str, Any]], decision: str, primary_roles: Counter[str]) -> str:
    if decision == "never_join":
        present = {role["modal_primary_role"] for role in roles if role["modal_aggregation_decision"] == "never_join"}
        for role_name in HARD_BLOCKER_ROLE_PRIORITY:
            if role_name in present:
                return role_name
    if decision == "review_required" and primary_roles["vehicle_core"]:
        return "vehicle_core"
    return primary_roles.most_common(1)[0][0] if primary_roles else "unknown"


def _review_action(decision: str, decisions: Counter[str]) -> str:
    if decision in {"never_join", "protected_terminal"}:
        return "exclude_from_join"
    if decision == "join_core":
        return "safe_vehicle_core_candidate"
    if decision == "shape_support":
        return "review_modal_support"
    if decisions["shape_support"] and not decisions["join_core"]:
        return "review_modal_support"
    return "review_vehicle_core_boundary"
