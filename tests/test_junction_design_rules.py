import hashlib

import pytest

from torii_sumo.road_network.design_rules import RAST
from torii_sumo.road_network.junction_design_rules import (
    check_plan_marking_for_connection, check_through_lane_continuity,
)


def _context(**changes):
    return dict(purpose="design_review", jurisdiction="DE-HH", assessment_date="2026-09-08",
                standard_id=RAST, local_overrides_checked=True, **changes)


def _continuity(**changes):
    return dict(context=_context(), upstream_through_lane_count=2, junction_through_lane_count=2,
                through_lane_becomes_turn_only=False, control_type="priority_signs") | changes


def test_ordinary_continuity_and_source_scope():
    result = check_through_lane_continuity(**_continuity())
    assert result["status"] == "pass"
    assert result["sources"][0]["pdf_page"] == 106
    assert result["geometry_changes_authorized"] is False
    assert result["rule_strength"] == "general_recommendation_with_exceptions"
    missing = check_through_lane_continuity(**_continuity(through_lane_becomes_turn_only=None))
    assert missing["status"] == "review_required"
    assert "missing_conditions" in missing["reasons"][0]


def test_through_to_turn_exception_requires_early_marking_and_signs():
    args = _continuity(through_lane_becomes_turn_only=True, junction_through_lane_count=1)
    for marks, signs, expected in [(None, True, "unknown"), (False, True, "not_met"), (True, False, "not_met"),
                                    (True, True, "met")]:
        result = check_through_lane_continuity(**args, early_unambiguous_markings=marks, relevant_signs=signs)
        assert result["status"] == "review_required"
        assert result["exception_provisions"] == expected
        assert result["connection_changes_authorized"] is False


def test_signal_lane_addition_is_an_exception_not_a_prohibition_or_automatic_pass():
    result = check_through_lane_continuity(**_continuity(junction_through_lane_count=3, control_type="traffic_signals"))
    assert result["status"] == "review_required"
    assert "signal_controlled_additional_through_lanes_may_be_useful" in result["reasons"]
    assert check_through_lane_continuity(**_continuity(junction_through_lane_count=1))["status"] == "review_required"
    assert check_through_lane_continuity(**_continuity(context={**_context(), "purpose": "existing_road_reconstruction"}))["status"] == "not_applicable"


@pytest.mark.parametrize("changes", [dict(upstream_through_lane_count=True), dict(junction_through_lane_count=-1),
                                   dict(through_lane_becomes_turn_only="no"), dict(control_type=[]),
                                   dict(early_unambiguous_markings=1)])
def test_continuity_rejects_invalid_types(changes):
    with pytest.raises(ValueError):
        check_through_lane_continuity(**_continuity(**changes))


def _source(tmp_path):
    pdf = tmp_path / "plan.pdf"
    pdf.write_bytes(b"%PDF-1.7\nmanual marking example\n")
    return dict(path=str(pdf), sha256=hashlib.sha256(pdf.read_bytes()).hexdigest(), title="Marked road plan",
                document_date="2021-03-16", document_kind="design")


def test_removed_plan_marking_is_not_connection_or_prohibited_turn_evidence(tmp_path):
    source = _source(tmp_path)
    result = check_plan_marking_for_connection(source=source, page=67, marking_id="old-cross-1",
                                             marking_status="removed", legend_verified=True)
    assert result["status"] == "pass"
    assert result["connection_evidence"] == "excluded"
    assert result["rule_kind"] == "engineering_plan_interpretation"
    assert result["sources"][0]["source"]["sha256"] == source["sha256"]
    assert result["field_status"] == "not_verified"
    assert result["turn_prohibition_inferred"] is False
    assert result["connection_changes_authorized"] is False


@pytest.mark.parametrize("status,verified", [("removed", None), ("removed", False), ("unknown", True),
                                            ("retained", True), ("proposed", True)])
def test_markings_need_interpretation_and_never_authorize_connections(tmp_path, status, verified):
    result = check_plan_marking_for_connection(source=_source(tmp_path), page=67, marking_id="mark-1",
                                             marking_status=status, legend_verified=verified)
    assert result["status"] == "review_required"
    assert result["connection_evidence"] != "excluded"
    assert result["connection_changes_authorized"] is False


def test_marking_source_hash_and_page_are_validated(tmp_path):
    source = _source(tmp_path)
    with pytest.raises(ValueError, match="page"):
        check_plan_marking_for_connection(source=source, page=0, marking_id="mark-1")
    source["sha256"] = "0" * 64
    with pytest.raises(ValueError, match="SHA-256"):
        check_plan_marking_for_connection(source=source, page=67, marking_id="mark-1")
def test_current_middle_cycle_planning_rule_does_not_erase_historic_observation():
    from torii_sumo.road_network.junction_design_rules import check_cycle_lane_position
    from torii_sumo.road_network.design_rules import RESTRA
    context = dict(purpose='design_review', jurisdiction='DE-HH', assessment_date='2026-09-08', standard_id=RESTRA)
    assert check_cycle_lane_position(context=context, position='between_motor_lanes')['status'] == 'review_required'
    assert check_cycle_lane_position(context=context, position='right_of_motor_traffic')['status'] == 'pass'
    historic = check_cycle_lane_position(context={**context, 'purpose': 'existing_road_reconstruction'}, position='between_motor_lanes')
    assert historic['status'] == 'not_applicable'
    assert historic['historic_layout_changes_authorized'] is False
    assert check_cycle_lane_position(context=context, position='between_motor_lanes',
        alternative_has_substantial_disadvantages=True, comparison_documented=True, bvm_leadership_decision='approved')['status'] == 'pass'
