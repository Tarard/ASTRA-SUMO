import pytest

from torii_sumo.road_network.design_rules import (
    check_cycle_lane_width, check_left_turn_lane_width, check_two_lane_carriageway_width,
)


def hh_context(**changes):
    return {"purpose": "design_review", "jurisdiction": "DE-HH", "assessment_date": "2026-09-08",
            "standard_id": "restra-2017-revision-2026-03-23", **changes}


def test_cycle_width_keeps_minimum_regular_and_uncertainty_distinct():
    inputs = dict(context=hh_context(), facility_type="radfahrstreifen", width_basis="including_markings")
    assert check_cycle_lane_width(2.75, **inputs)["status"] == "pass"
    narrow = check_cycle_lane_width(2.50, **inputs)
    assert narrow["status"] == "review_required"
    assert narrow["minimum_check"] == "met"
    assert narrow["regular_check"] == "not_met"
    assert check_cycle_lane_width(2.20, **inputs)["minimum_check"] == "not_met"
    uncertain = check_cycle_lane_width(2.25, uncertainty_m=0.05, **inputs)
    assert uncertain["minimum_check"] == "uncertain"
    assert uncertain["geometry_changes_authorized"] is False
    assert uncertain["sources"][0]["pdf_page"] == 37
    regular_uncertainty = check_cycle_lane_width(2.75, uncertainty_m=0.05, **inputs)
    assert regular_uncertainty["reasons"] == ["uncertainty_crosses_regular_width"]


@pytest.mark.parametrize("change, expected", [
    ({"context": hh_context(purpose="existing_road_reconstruction")}, "not_applicable"),
    ({"context": hh_context(assessment_date="2024-07-01")}, "not_applicable"),
    ({"context": hh_context(jurisdiction="DE-BY")}, "not_applicable"),
    ({"context": hh_context(standard_id="restra-2017")}, "not_applicable"),
    ({"facility_type": "radweg"}, "not_applicable"),
    ({"facility_type": None}, "review_required"),
    ({"width_basis": "unknown"}, "review_required"),
])
def test_cycle_rule_requires_the_declared_version_and_measurement_basis(change, expected):
    inputs = dict(context=hh_context(), facility_type="radfahrstreifen", width_basis="including_markings")
    inputs.update(change)
    result = check_cycle_lane_width(2.56, **inputs)
    assert result["status"] == expected
    assert result["numeric_evaluation_performed"] is False


def test_carriageway_reference_is_not_a_universal_minimum_or_single_lane_width():
    inputs = dict(context=hh_context(), road_type="urban_main_road", lane_count=2,
                  directionality="two_way", infrequent_hgv_meetings=True, low_speed_meetings=True,
                  scheduled_bus_service=False, advisory_cycle_lanes_present=False, width_basis="whole_carriageway")
    assert check_two_lane_carriageway_width(5.90, **inputs)["status"] == "pass"
    for width in (3.2, 5.5, 6.5):
        result = check_two_lane_carriageway_width(width, **inputs)
        assert result["status"] == "review_required"
        assert result["reference_kind"] == "specified_design_value"
        assert result["geometry_changes_authorized"] is False
    inputs["low_speed_meetings"] = None
    assert check_two_lane_carriageway_width(5.90, **inputs)["numeric_evaluation_performed"] is False


def test_carriageway_does_not_silently_choose_one_overlapping_table_row():
    inputs = dict(context=hh_context(), road_type="urban_main_road", lane_count=2,
                  directionality="two_way", infrequent_hgv_meetings=True, low_speed_meetings=True,
                  width_basis="whole_carriageway")
    assert check_two_lane_carriageway_width(5.90, **inputs)["numeric_evaluation_performed"] is False
    result = check_two_lane_carriageway_width(5.90, **inputs, scheduled_bus_service=True, advisory_cycle_lanes_present=False)
    assert result["status"] == "not_applicable"
    assert result["reasons"] == ["outside_reviewed_single_table_row_scope"]
    assert result["numeric_evaluation_performed"] is False


def test_left_turn_width_combines_space_bus_and_through_lane_footnotes():
    inputs = dict(context={**hh_context(), "standard_id": "rast-2006-en-2012", "local_overrides_checked": True},
                  control_type="priority_signs", restricted_space=True, scheduled_bus_service=False,
                  through_lane_width_m=3.0, width_basis="standard_lane_width")
    assert check_left_turn_lane_width(2.75, **inputs)["status"] == "pass"
    assert check_left_turn_lane_width(2.75, **{**inputs, "scheduled_bus_service": True})["status"] == "review_required"
    wide_through = check_left_turn_lane_width(2.75, **{**inputs, "through_lane_width_m": 3.5})
    assert wide_through["required_width_interval_m"] == [3.25, 3.25]
    assert wide_through["status"] == "review_required"
    assert check_left_turn_lane_width(3.5, **{**inputs, "control_type": "traffic_signals"})["status"] == "not_applicable"
    assert check_left_turn_lane_width(3.5, **{**inputs, "control_type": "right_before_left"})["status"] == "not_applicable"
    assert check_left_turn_lane_width(3.5, **{**inputs, "scheduled_bus_service": None})["numeric_evaluation_performed"] is False


@pytest.mark.parametrize("width", [True, -1, float("nan"), float("inf"), "2.75"])
def test_invalid_widths_are_rejected(width):
    with pytest.raises(ValueError):
        check_cycle_lane_width(width, context=hh_context(), facility_type="radfahrstreifen", width_basis="including_markings")


@pytest.mark.parametrize("value", ["", "  ", False, []])
def test_unknown_facility_is_not_misreported_as_a_different_known_facility(value):
    kwargs = dict(context=hh_context(), facility_type=value, width_basis="including_markings")
    if isinstance(value, str):
        assert check_cycle_lane_width(2.75, **kwargs)["status"] == "review_required"
    else:
        with pytest.raises(ValueError):
            check_cycle_lane_width(2.75, **kwargs)
