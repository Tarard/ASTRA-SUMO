from pathlib import Path
import json
import shutil

import pytest

from astra_sumo.core import hamburg_readme_pipeline
from astra_sumo.core.hamburg_collision_trace import read_trace_source
from astra_sumo.core.detector_demand import read_csv_rows


def test_main_window_forwards_the_declared_driver_without_changing_duration_or_targets(tmp_path, monkeypatch):
    root = Path(__file__).parents[1]
    source = root / "benchmarks/hamburg_ring1_through_route_coverage_20261021_v1/inputs"

    def capture(**kwargs):
        assert kwargs["junction_body_hold"] is True
        assert kwargs["junction_body_hold_nodes"] is None
        assert kwargs["end_s"] == 14400 and kwargs["timeout_seconds"] == 2400
        assert kwargs["state_times_s"] == (14399.9,)
        assert kwargs["monitor_interval_s"] is None
        assert kwargs["expected_counts_csv"].read_bytes() == (source / "expected-counts.csv").read_bytes()
        assert kwargs["net_file"].read_bytes() == (source / "network.net.xml").read_bytes()
        assert kwargs["lane_intentions_file"].is_file()
        return {"checked": True}

    monkeypatch.setattr(hamburg_readme_pipeline, "run_point_detector_demand", capture)
    assert hamburg_readme_pipeline.run_hamburg_readme_prepared(prepared_dir=source, output_dir=tmp_path / "run",
        sumo_binary="not executed", hold_observed_lanes=True, hold_junction_body=True) == {"checked": True}
    with pytest.raises(ValueError, match="declared observation-lane hold"):
        hamburg_readme_pipeline.run_hamburg_readme_prepared(prepared_dir=source, output_dir=tmp_path / "bad",
            sumo_binary="not executed", hold_junction_body=True)


def test_prepared_scenario_selects_the_declared_junction_scope(tmp_path, monkeypatch):
    root = Path(__file__).parents[1]
    original = root / "benchmarks/hamburg_ring1_junction_body_full_20261021_v1/inputs"
    source = tmp_path / "source"
    source.mkdir()
    for name in ("network.net.xml", "demand.rou.xml.gz", "field-loops.add.xml", "expected-counts.csv", "control.add.xml", "lane-intentions.json.gz"):
        shutil.copyfile(original / name, source / name)
    (source / "scenario.json").write_text(json.dumps({"junction_body_hold_nodes": ["14398547", "205069138"]}), encoding="utf-8")

    def capture(**kwargs):
        assert kwargs["junction_body_hold"] is True and kwargs["junction_body_hold_nodes"] == ("14398547", "205069138")
        return {"scope_checked": True}

    monkeypatch.setattr(hamburg_readme_pipeline, "run_point_detector_demand", capture)
    assert hamburg_readme_pipeline.run_hamburg_readme_prepared(prepared_dir=source, output_dir=tmp_path / "run",
        sumo_binary="not executed", hold_observed_lanes=True, hold_junction_body=True) == {"scope_checked": True}


def test_frozen_full_run_is_invalid_and_short_road_probe_does_not_become_count_calibration():
    root = Path(__file__).parents[1] / "benchmarks/hamburg_ring1_junction_body_full_20261021_v1"
    result = json.loads(read_trace_source(root, "results.json"))
    assert not result["valid_run"] and not result["exact_count_fit"] and result["formal_field_metrics"] is None
    assert result["field_rows"] == 130 and result["station_rows"] == 40
    assert result["missing_field_rows"] == result["missing_station_rows"] == 0
    assert result["exact_field_rows"] == 28 and result["exact_station_rows"] == 6
    assert result["diagnostic_field_MAE"] == pytest.approx(3260/130) and result["diagnostic_max_abs_error"] == 154
    assert result["completion"]["waiting"] == 2771 and result["completion"]["running"] == 329
    assert result["completion"]["collisions"] == result["completion"]["teleports"] == 0
    witness = json.loads(read_trace_source(root / "data-analysis", "short-connector-witness.json"))
    assert witness["global_driver_candidate_rejected"]
    assert witness["road_length_m"] < witness["vehicle_length_m"]
    assert witness["selected_lane"] == "0" and witness["route_compatible_exit_lanes"] == ["1"]
    assert witness["suffix_probe"]["global_hold"]["monitor_status"] == "all_stopped"
    assert witness["suffix_probe"]["detector_only"]["healthy"]


def test_frozen_local_prefix_keeps_every_unmeasured_main_row_unknown():
    root = Path(__file__).parents[1] / "benchmarks/hamburg_ring1_localized_junction_body_20261021_v1"
    report = json.loads(read_trace_source(root / "analysis", "prefix-check.json"))
    assert not report["formal_result"] and not report["main_window_completed"]
    assert report["native_end_s"] == 6000 and report["completion"]["collisions"] == report["completion"]["teleports"] == 0
    assert report["main_field_rows_retained"] == 130 and report["main_field_rows_unknown"] == 104
    assert report["station_rows_retained"] == 40 and report["station_rows_unknown"] == 32
    assert report["north_entries_after_original_stoppage"] == 131
    assert float(report["known_trapped_vehicle"]["arrival"]) == 4637.1
    fields = read_csv_rows(root / "analysis/all-130-main-rows.csv")
    assert len(fields) == 130 and sum(r["measurement_status"] == "missing" for r in fields) == 104
    assert all(r["measured_nVehContrib"] == "" for r in fields if r["measurement_status"] == "missing")
