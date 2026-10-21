import numpy as np
import pytest
from pathlib import Path

from astra_sumo.core.hamburg_readme_results import write_hamburg_temporal_feedback
from astra_sumo.core.native_temporal_feedback import native_temporal_feedback


def fixture():
    return {
        "requests": [{"vehicle_id": f"v{i}", "plan_id": "same_plan", "requested_depart_s": 10} for i in range(4)],
        "trips": [{"vehicle_id": f"v{i}", "actual_depart_s": 20} for i in range(3)],
        "measurements": [{"detector_id": "f", "begin": 0, "end": 900, "measured_nVehContrib": 0},
                         {"detector_id": "f", "begin": 900, "end": 1800, "measured_nVehContrib": 1}],
        "passages": [{"vehicle_id": "v0", "detector_id": "f", "notification_time_s": 900,
                      "interval_begin_s": 900, "interval_end_s": 1800},
                     {"vehicle_id": "v1", "detector_id": "f", "notification_time_s": 1900,
                      "interval_begin_s": 1800, "interval_end_s": 2700}],
    }


def test_requested_denominator_retains_queue_censoring_and_cross_bin_passages():
    result = native_temporal_feedback(**fixture())
    np.testing.assert_array_equal(result["numerators"], [[0], [1]])
    np.testing.assert_array_equal(result["denominators"], [4])
    np.testing.assert_array_equal(result["fractions"], [[0], [.25]])
    assert result["totals"] == {"requested_vehicles": 4, "inserted_vehicles": 3,
        "not_inserted_vehicles": 1, "inserted_without_complete_passage": 1,
        "vehicles_with_observed_window_passage": 1, "vehicles_with_only_outside_window_passage": 1,
        "observed_window_passages": 1, "outside_window_passages": 1}
    column = result["columns"][0]
    assert column["median_insertion_delay_s"] == 10
    assert column["median_inserted_to_passage_delay_s"] == 1380


def test_missing_measurements_and_inconsistent_native_identities_are_not_zero():
    data = fixture()
    data["measurements"][0]["measured_nVehContrib"] = None
    with pytest.raises(ValueError, match="missing detector"):
        native_temporal_feedback(**data)
    data = fixture()
    data["measurements"][1]["measured_nVehContrib"] = 2
    with pytest.raises(ValueError, match="reconcile"):
        native_temporal_feedback(**data)


def test_native_boundary_interval_wins_over_fractional_rear_crossing_time():
    data = fixture()
    data["passages"][0]["time_s"] = 899.999
    result = native_temporal_feedback(**data)
    assert result["numerators"][0, 0] == 0 and result["numerators"][1, 0] == 1


def test_requested_departures_use_native_millisecond_rounding():
    data = fixture()
    data["requests"][0]["requested_depart_s"] = 899.9998
    data["trips"][0]["actual_depart_s"] = 900
    result = native_temporal_feedback(**data)
    assert result["requested_time_resolution_s"] == .001
    assert result["columns"][1]["requested_depart_begin_s"] == 900
    assert result["columns"][1]["median_insertion_delay_s"] == 0


def test_frozen_hamburg_feedback_accounts_for_all_demand_and_every_real_field_row(tmp_path):
    case = Path(__file__).parents[1] / "benchmarks/hamburg_ring1_lane_section_hold_full_20261021_v1"
    result = write_hamburg_temporal_feedback(case_dir=case, output_dir=tmp_path / "feedback")
    assert result["field_rows"] == 130
    assert result["totals"]["requested_vehicles"] == 6687
    assert result["totals"]["not_inserted_vehicles"] == 1593
    assert result["totals"]["inserted_without_complete_passage"] == 192
    assert result["totals"]["observed_window_passages"] == 4223
    assert result["totals"]["outside_window_passages"] == 679
    assert result["first_observation_begin_s"] == 3600
    assert result["requested_before_first_observation"] == 0
    assert result["inputs_unchanged"] and result["integer_reconstruction_verified"]
    assert not result["native_run_valid"] and not result["formal_result"]
    assert not result["derivative_established"] and not result["reuse_under_changed_demand_established"]
