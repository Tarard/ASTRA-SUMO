import json
from pathlib import Path

import pytest

from astra_sumo.core.command_runner import CommandResult
from astra_sumo.core.hamburg_collision_trace import read_trace_source
from astra_sumo.core import hamburg_junction_body_hold


ROOT = Path(__file__).parents[1]
BASE = ROOT / "benchmarks/hamburg_ring1_through_route_coverage_20261021_v1"


def test_driver_candidate_is_declared_before_execution_and_keeps_all_traffic_inputs(tmp_path, monkeypatch):
    binary = tmp_path / "sumo.exe"
    binary.write_bytes(b"not executed")
    out = tmp_path / "candidate"

    def failed_command(*args, **kwargs):
        request = json.loads((out / "native-request.json").read_bytes())
        proposal = json.loads((out / "driver-proposal.json").read_bytes())
        assert request["junction_body_hold"] is True and not proposal["formal_result"]
        assert request["end_s"] == 3600 and request["all_stopped_timeout_s"] == 300
        for name in ("network.net.xml", "demand.rou.xml", "signal-programs.add.xml", "source-e1.add.xml", "lane-intentions.json", "expected-counts.csv"):
            assert (out / name).read_bytes() == read_trace_source(BASE / "native", name)
        return CommandResult(command=[], cwd=str(out), status="timeout", returncode=None)

    monkeypatch.setattr(hamburg_junction_body_hold, "run_command", failed_command)
    with pytest.raises(RuntimeError, match="did not finish"):
        hamburg_junction_body_hold.probe_hamburg_junction_body_hold(case_dir=BASE, output_dir=out, sumo_binary=binary)
    assert json.loads((out / "command.json").read_bytes())["status"] == "timeout"
    assert not (out / "junction-body-check.json").exists()


def test_frozen_junction_body_candidate_keeps_all_residuals_and_sampling_limits():
    case = ROOT / "benchmarks/hamburg_ring1_junction_body_hold_20261021_v1"
    report = json.loads(read_trace_source(case / "native", "junction-body-check.json"))
    assert report["native_end_s"] == 3600 and report["native_monitor_status"] == "completed"
    assert report["completion"]["collisions"] == report["completion"]["teleports"] == 0
    assert report["completion"]["waiting"] == 147 and report["completion"]["running"] == 145
    assert report["available_initialization_bins"] == 52 and report["exact_initialization_bins"] == 33
    assert report["diagnostic_MAE"] == pytest.approx(342/52) and report["diagnostic_max_abs_error"] == 43
    assert report["native_E1_reconciled"] and report["complete_field_passages"] == 1703
    assert not report["other_complete_fields"] and not report["repeated_same_field_passages"]
    assert not report["exact_calibration_established"] and not report["main_window_run"] and not report["formal_result"]
    checked = json.loads(read_trace_source(case / "analysis", "independent-checks.json"))
    assert checked["all_fixed_traffic_inputs_unchanged"] and checked["all_original_participants_rear_cleared_target"]
    audit = json.loads(read_trace_source(case / "analysis", "hold-audit.json"))
    assert not audit["changes_during_active_body_hold"] and len(audit["internal_changes_outside_body_hold"]) == 21
    population = json.loads(read_trace_source(case / "analysis", "population-and-boundary-check.json"))
    assert population["total_requests"] == 8640 and population["future_main_requests"] == 6687
    assert population["entered_without_complete_field_passage"] == 103
    assert all(r["matches_one_step_observation_delay"] for r in population["unheld_internal_changes"])
    terminal = json.loads(read_trace_source(case / "analysis", "terminal-holds.json"))
    assert terminal["arrivals_removed_before_an_explicit_guard_release"] == 514
    assert terminal["by_terminal_lane"] == {"27064186#0_0": 514}
