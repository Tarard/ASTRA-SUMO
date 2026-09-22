from torii_sumo.core.claim_tiers import evaluate_claim_tiers


def _passing_gates() -> dict[str, str]:
    return {
        "network_build": "pass",
        "connectivity": "pass",
        "road_connectivity_parity": "pass",
        "connection_semantics_parity": "pass",
        "routeability_audit": "pass",
        "reference_visual_detail": "pass",
        "reference_join_audit": "pass",
        "tls_semantics_parity": "pass",
        "tls_reality_audit": "pass",
        "tls_aggregation": "pass",
        "reference_hierarchy_audit": "pass",
        "reference_scope_audit": "pass",
        "topology_audit": "pass",
    }


def test_claim_tiers_stop_at_strict_valid_when_reference_gates_fail() -> None:
    result = evaluate_claim_tiers(
        gate_status={**_passing_gates(), "tls_reality_audit": "blocked"},
        review_locations=[{"location_id": "tls_1"}],
    )

    assert result["highest_passed_tier"] == "strict_valid"
    assert result["strict_valid"]["status"] == "pass"
    assert result["reference_aligned"]["status"] == "fail"
    assert result["manual_quality_reviewed"]["status"] == "pending"
    assert result["manual_quality_reviewed"]["missing_decision_location_ids"] == ["tls_1"]


def test_claim_tiers_require_evidence_for_every_review_location() -> None:
    locations = [{"location_id": "tls_1"}, {"location_id": "scope_1"}]
    incomplete = evaluate_claim_tiers(
        gate_status=_passing_gates(),
        review_locations=locations,
        review_decisions={
            "locations": [
                {"location_id": "tls_1", "decision": "approved", "evidence": ""},
                {"location_id": "scope_1", "decision": "pending", "evidence": "map"},
            ]
        },
    )

    assert incomplete["reference_aligned"]["status"] == "pass"
    assert incomplete["manual_quality_reviewed"]["status"] == "pending"
    assert incomplete["manual_quality_reviewed"]["missing_evidence_location_ids"] == ["tls_1"]
    assert incomplete["manual_quality_reviewed"]["pending_decision_location_ids"] == ["scope_1"]


def test_claim_tiers_reach_manual_quality_reviewed_with_evidence() -> None:
    result = evaluate_claim_tiers(
        gate_status=_passing_gates(),
        review_locations=[{"location_id": "tls_1"}],
        review_decisions={
            "locations": [
                {
                    "location_id": "tls_1",
                    "decision": "rejected_with_evidence",
                    "evidence": "Google Maps review: signal is not present.",
                }
            ]
        },
    )

    assert result["highest_passed_tier"] == "manual_quality_reviewed"
    assert result["manual_quality_reviewed"]["status"] == "pass"


def test_manual_quality_review_can_close_reviewed_reference_difference() -> None:
    gates = {**_passing_gates(), "reference_join_audit": "fail"}
    result = evaluate_claim_tiers(
        gate_status=gates,
        review_locations=[{"location_id": "tls_1"}],
        review_decisions={
            "locations": [
                {
                    "location_id": "tls_1",
                    "decision": "rejected_with_evidence",
                    "evidence": "Map review confirms the OSM signal differs from the reference oracle.",
                }
            ]
        },
    )

    assert result["strict_valid"]["status"] == "pass"
    assert result["reference_aligned"]["status"] == "fail"
    assert result["manual_quality_reviewed"]["status"] == "pass"
    assert result["manual_quality_reviewed"]["reference_alignment_status"] == "fail"
    assert result["highest_passed_tier"] == "manual_quality_reviewed"


def test_manual_quality_review_fails_when_strict_valid_is_not_met() -> None:
    gates = {**_passing_gates(), "routeability_audit": "fail"}
    result = evaluate_claim_tiers(
        gate_status=gates,
        review_decisions={"locations": []},
    )

    assert result["manual_quality_reviewed"]["status"] == "fail"


def test_reference_claim_requires_bbox_scope_when_workflow_provides_the_gate() -> None:
    result = evaluate_claim_tiers(
        gate_status={**_passing_gates(), "reference_bbox_scope": "blocked"},
    )

    assert result["strict_valid"]["status"] == "pass"
    assert result["reference_aligned"]["status"] == "fail"
    assert result["reference_aligned"]["failed_gates"] == ["reference_bbox_scope"]
