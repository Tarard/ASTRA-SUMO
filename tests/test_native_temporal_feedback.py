import numpy as np
import pytest
from pathlib import Path

from astra_sumo.core.hamburg_readme_results import write_hamburg_temporal_feedback
from astra_sumo.core.native_temporal_feedback import (
    native_departure_windows,
    native_temporal_feedback,
    propose_native_count_retiming,
)


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


def retiming_fixture():
    return {
        "requests": [{"vehicle_id": identity, "detector_id": "f", "requested_depart_s": depart}
                     for identity, depart in (("warmup", 100), ("surplus", 1200), ("late", 2200), ("unknown", 1000))],
        "measurements": [
            {"detector_id": "f", "begin": 900, "end": 1800, "expected_total": 1, "measured_nVehContrib": 2},
            {"detector_id": "f", "begin": 1800, "end": 2700, "expected_total": 2, "measured_nVehContrib": 0}],
        "passages": [
            {"vehicle_id": "warmup", "detector_id": "f", "notification_time_s": 950,
             "interval_begin_s": 900, "interval_end_s": 1800},
            {"vehicle_id": "surplus", "detector_id": "f", "notification_time_s": 1750,
             "interval_begin_s": 900, "interval_end_s": 1800},
            {"vehicle_id": "late", "detector_id": "f", "notification_time_s": 2750,
             "interval_begin_s": 2700, "interval_end_s": 3600}],
    }


def test_native_residual_moves_keep_warmup_and_unobserved_requests_fixed():
    result = propose_native_count_retiming(**retiming_fixture())
    changes = {row["vehicle_id"]: row for row in result["proposals"]}
    assert set(changes) == {"surplus", "late"}
    assert changes["surplus"]["shift_s"] == 80
    assert changes["late"]["shift_s"] == -80
    limited = propose_native_count_retiming(**retiming_fixture(), max_moves_per_field=1)
    assert limited["changed_vehicles"] == 1


def test_native_residual_feedback_rejects_missing_and_unreconciled_counts():
    from copy import deepcopy

    source = retiming_fixture()
    for value in (None, "", "nan", -1, 1.5):
        data = deepcopy(source)
        data["measurements"][0]["measured_nVehContrib"] = value
        with pytest.raises(ValueError):
            propose_native_count_retiming(**data)
    source["passages"].pop()
    source["measurements"][0]["measured_nVehContrib"] = 3
    with pytest.raises(ValueError, match="Reconcile"):
        propose_native_count_retiming(**source)


def test_frozen_hamburg_feedback_changes_only_bounded_main_departures(tmp_path):
    import gzip
    import json
    import xml.etree.ElementTree as ET
    from astra_sumo.core.hamburg_departure_probe import prepare_hamburg_count_retiming

    source = Path(__file__).parents[1] / "benchmarks/hamburg_ring1_primary_entry_full_20261022_v1"
    out = tmp_path / "proposal"
    result = prepare_hamburg_count_retiming(prepared_dir=source / "inputs", measurement_dir=source, output_dir=out)
    assert 0 < result["changed_vehicles"] <= 13 * 8
    before = ET.fromstring(gzip.decompress((source / "inputs/demand.rou.xml.gz").read_bytes()))
    after = ET.parse(out / "demand.rou.xml").getroot()
    original = {row.get("id"): row for row in before.findall("vehicle")}
    actual = {row.get("id"): row for row in after.findall("vehicle")}
    assert set(original) == set(actual) and len(actual) == 8640
    changes = {row["vehicle_id"]: row for row in result["proposals"]}
    intentions = json.loads((out / "lane-intentions.json").read_bytes())
    assert len(intentions["vehicles"]) == 8640
    for identity, row in actual.items():
        old = original[identity]
        if identity in changes:
            assert float(old.get("depart")) >= 3600 and float(row.get("depart")) >= 3600
            assert abs(float(old.get("depart")) - float(row.get("depart"))) <= 900 + 1e-8
        else:
            assert float(row.get("depart")) == float(old.get("depart"))
        row.set("depart", old.get("depart"))
        assert ET.tostring(row) == ET.tostring(old)
    for name in ("network.net.xml", "control.add.xml", "expected-counts.csv", "initialization-counts.csv"):
        assert (out / name).read_bytes() == (source / "inputs" / name).read_bytes()
    assert result["input_bytes_unchanged"]


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


def timing_request(targets=None, depart=100):
    return {"vehicle_id": "v", "requested_depart_s": depart, "targets": targets or [
        {"detector_id": "f", "begin_s": 0, "end_s": 900}]}


def timing_passage(field="f", time=950):
    return {"vehicle_id": "v", "detector_id": field, "notification_time_s": time, "time_s": time - .01}


def test_departure_window_uses_native_notification_and_exclusive_upper_bound():
    result = native_departure_windows(requests=[timing_request()], passages=[timing_passage()], guard_s=0)
    row = result["rows"][0]
    assert row["proposed_depart_s"] == 49.9 and row["proposed_shift_s"] == -50.1
    assert row["status"] == "feasible_frozen_lags" and not result["changed_demand_response_verified"]
    boundary = native_departure_windows(requests=[timing_request()], passages=[timing_passage(time=900)], guard_s=0)
    assert boundary["rows"][0]["proposed_shift_s"] == -.1
    rounded = native_departure_windows(requests=[timing_request(depart=100.0008)],
        passages=[timing_passage(time=900)], guard_s=0)
    assert rounded["rows"][0]["native_requested_depart_s"] == 100.001
    assert rounded["rows"][0]["proposed_depart_s"] == 99.901


def test_shared_departure_must_satisfy_both_windows_and_does_not_hide_missing_passages():
    request = timing_request([{"detector_id": "f", "begin_s": 0, "end_s": 900},
                             {"detector_id": "g", "begin_s": 900, "end_s": 1800}])
    incompatible = native_departure_windows(requests=[request],
        passages=[timing_passage(time=1200), timing_passage("g", 1300)], guard_s=90)
    assert incompatible["status_counts"] == {"incompatible_target_windows": 1}
    assert incompatible["rows"][0]["proposed_depart_s"] is None
    missing = native_departure_windows(requests=[request], passages=[timing_passage(time=800)])
    assert missing["model_vehicles"] == 1 and missing["target_field_visits"] == 2
    assert missing["status_counts"] == {"missing_native_passage": 1}
    assert missing["rows"][0]["missing_fields"] == ["g"]
    assert missing["rows"][0]["joint_depart_min_s"] is None


def test_departure_limits_do_not_turn_impossible_advances_into_proposals():
    before_zero = native_departure_windows(requests=[timing_request()], passages=[timing_passage(time=2000)], guard_s=0)
    assert before_zero["status_counts"] == {"before_simulation_start": 1}
    limited = native_departure_windows(requests=[timing_request(
        [{"detector_id": "f", "begin_s": 900, "end_s": 1800}], depart=2000)],
        passages=[timing_passage(time=3000)], max_shift_s=900, guard_s=0)
    assert limited["status_counts"] == {"shift_budget_exceeded": 1}
    with pytest.raises(ValueError, match="one native notification"):
        native_departure_windows(requests=[timing_request()], passages=[timing_passage(), timing_passage()])
    with pytest.raises(ValueError, match="one native notification"):
        native_departure_windows(requests=[timing_request()], passages=[timing_passage("unknown")])


def test_departure_window_rejects_invalid_requests_and_margins():
    with pytest.raises(ValueError, match="unique vehicles"):
        native_departure_windows(requests=[timing_request(), timing_request()], passages=[])
    with pytest.raises(ValueError, match="wider than both margins"):
        native_departure_windows(requests=[timing_request()], passages=[], guard_s=450)
    with pytest.raises(ValueError, match="finite nonnegative shift"):
        native_departure_windows(requests=[timing_request()], passages=[], max_shift_s=-1)
    for step in (.0001, 1e-12):
        with pytest.raises(ValueError, match="whole milliseconds"):
            native_departure_windows(requests=[timing_request()], passages=[], step_s=step)


@pytest.mark.parametrize("case_name,vehicle_count,missing,conflicts", [
    ("hamburg_ring1_primary_entry_full_20261022_v1", 8640, 911, 0),
    ("hamburg_ring1_shared_population_full_20261022_v1", 8185, 1138, 333),
])
def test_frozen_departure_windows_keep_all_requests_and_joint_targets(case_name, vehicle_count, missing, conflicts):
    import json
    import xml.etree.ElementTree as ET

    from astra_sumo.core.hamburg_collision_trace import read_trace_source

    case = Path(__file__).parents[1] / "benchmarks" / case_name
    vehicles = {v.get("id"): v for v in ET.fromstring(read_trace_source(case / "inputs", "demand.rou.xml")).findall("vehicle")}
    intentions = json.loads(read_trace_source(case / "inputs", "lane-intentions.json"))["vehicles"]
    requests = [{"vehicle_id": r["vehicle_id"], "requested_depart_s": float(vehicles[r["vehicle_id"]].get("depart")),
        "targets": [{"detector_id": t["field_id"], "begin_s": t["target_begin_s"], "end_s": t["target_end_s"]}
                    for t in r.get("target_observations", [r])]} for r in intentions]
    before = json.dumps(requests)
    native = json.loads(read_trace_source(case, "passage-reconciliation.json"))
    result = native_departure_windows(requests=requests, passages=native["passages"])
    assert result["model_vehicles"] == vehicle_count and result["target_field_visits"] == 8640
    assert result["status_counts"]["missing_native_passage"] == missing
    assert result["status_counts"].get("incompatible_target_windows", 0) == conflicts
    assert json.dumps(requests) == before and result["new_simulations"] == 0 and not result["formal_result"]
    assert all(r["proposed_depart_s"] is None for r in result["rows"] if r["status"] != "feasible_frozen_lags")


def test_bounded_timing_prefix_keeps_failed_native_response_and_unknown_main_rows():
    import json

    from astra_sumo.core.detector_demand import read_csv_rows
    from astra_sumo.core.hamburg_collision_trace import read_trace_source

    case = Path(__file__).parents[1] / "benchmarks/hamburg_ring1_native_departure_windows_20261022_v1"
    result = json.loads(read_trace_source(case / "analysis", "prefix-check.json"))
    assert not result["formal_result"] and not result["main_window_completed"]
    assert result["total_model_requests"] == 8640 and result["changed_requests"] == 2094
    assert result["candidate_completion"]["collisions"] == result["candidate_completion"]["teleports"] == 0
    assert result["candidate_completion"]["waiting"] == 259 and result["initialization_not_inserted"] == 114
    assert result["candidate_E1_total"] == 1656 and result["baseline_E1_total"] == 1716
    assert result["candidate_diagnostic_MAE"] == pytest.approx(313 / 52)
    assert result["candidate_diagnostic_MAE"] > result["baseline_diagnostic_MAE"]
    assert result["candidate_exact_rows"] == 26 and result["candidate_max_abs"] == 42
    assert result["native_E1_reconciled"] and not result["unexpected_fields"] and not result["repeated_same_field"]
    assert result["prewindow_contributions_targeting_main"] == 0
    main = read_csv_rows(case / "analysis/all-130-main-rows.csv")
    stations = read_csv_rows(case / "analysis/all-40-main-stations.csv")
    assert len(main) == 130 and all(r["measured_nVehContrib"] == "" for r in main)
    assert len(stations) == 40 and all(r["measured_nVehContrib"] == "" for r in stations)
    assert json.loads((case / "decision.json").read_bytes())["new_full_window_runs"] == 0
