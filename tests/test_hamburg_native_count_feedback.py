import gzip
import json
from pathlib import Path

import pytest

from astra_sumo.core.detector_demand import read_csv_rows, write_csv
from astra_sumo.core.hamburg_readme_pipeline import calibrate_hamburg_native_counts


@pytest.mark.parametrize("collision,expected_trials", [(0, 2), (1, 1)])
def test_feedback_keeps_reference_on_regression_and_stops_on_collision(tmp_path, monkeypatch, collision, expected_trials):
    root = Path(__file__).parents[1]
    reference = root / "benchmarks/hamburg_ring1_primary_entry_full_20261022_v1"
    baseline = json.loads(gzip.decompress((reference / "native/run.json.gz").read_bytes()))
    calls = []

    def native(**options):
        calls.append(options)
        assert options["end_s"] == 14400 and options.get("state_times_s", ()) == ()
        directory = options["output_dir"]
        directory.mkdir(parents=True)
        rows = read_csv_rows(reference / "field-comparison.csv")
        row = next(row for row in rows if int(row["measured_nVehContrib"]) >= int(row["expected_total"]))
        row["measured_nVehContrib"] = int(row["measured_nVehContrib"]) + 1
        write_csv(directory / "field-comparison.csv", rows, list(rows[0]))
        completion = {**baseline["completion"], "collisions": collision}
        return {"native_status": "pass", "stall_monitor": {"status": "completed"}, "completion": completion,
                "exact_count_fit": False}

    def summary(**options):
        directory = options["output_dir"]
        directory.mkdir(parents=True)
        (directory / "passage-reconciliation.json").write_text('{"reconciled":true}', encoding="utf-8")
        return {"diagnostic_field_MAE": 13, "diagnostic_max_abs_error": 60}

    monkeypatch.setattr("astra_sumo.core.hamburg_readme_pipeline.run_point_detector_demand", native)
    monkeypatch.setattr("astra_sumo.core.hamburg_readme_results.summarize_hamburg_readme", summary)
    result = calibrate_hamburg_native_counts(repository=root, reference_case=reference,
        output_dir=tmp_path / "feedback", sumo_binary="unused")
    assert len(calls) == expected_trials
    assert result["selected_case"] == str(reference.resolve())
    assert not result["formal_result"] and not any(row["retained_for_next_proposal"] for row in result["history"])
    if expected_trials == 2:
        assert [row["scale"] for row in result["history"]] == [1, .5]
