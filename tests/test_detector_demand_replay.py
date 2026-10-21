import shutil
import subprocess
import gzip
import json
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from astra_sumo.core.detector_demand_replay import run_point_detector_demand, recover_point_detector_measurements
from astra_sumo.core.route_sensor_matrix import read_contributing_e1_passages


@pytest.mark.skipif(not shutil.which("netconvert") or not shutil.which("sumo"), reason="SUMO programs required")
@pytest.mark.parametrize("monitor_interval,local_hold",[(None,False),(1,False),(None,True)])
def test_native_point_detector_demand_replay_keeps_inputs_and_counts(tmp_path,monitor_interval,local_hold):
    nodes, edges, network, routes, loops, expected = [tmp_path / name for name in
        ("nodes.xml", "edges.xml", "network.xml", "routes.xml", "loops.xml", "expected.csv")]
    nodes.write_text('<nodes><node id="a" x="0" y="0"/><node id="b" x="100" y="0"/><node id="c" x="200" y="0"/></nodes>', encoding="utf-8")
    edges.write_text('<edges><edge id="in" from="a" to="b"/><edge id="out" from="b" to="c"/></edges>', encoding="utf-8")
    compiled = subprocess.run([shutil.which("netconvert"), "-n", str(nodes), "-e", str(edges), "-o", str(network)],
        capture_output=True, text=True, timeout=30)
    assert compiled.returncode == 0, compiled.stderr
    routes.write_text('<routes><vehicle id="v" depart="0" departSpeed="max"><route edges="in out"/></vehicle></routes>', encoding="utf-8")
    loops.write_text('''<additional><inductionLoop id="first" lane="in_0" pos="30" period="30" file="unused.xml"/>
      <inductionLoop id="second" lane="out_0" pos="30" period="30" file="unused.xml"/></additional>''', encoding="utf-8")
    expected.write_text('detector_id,begin,end,expected_total\nfirst,0,30,1\nsecond,0,30,1\n', encoding="utf-8")
    originals = {path: path.read_bytes() for path in (network, routes, loops, expected)}
    intentions = tmp_path / "intentions.json"
    intentions.write_text(json.dumps({"vehicles": [{"vehicle_id": "v", "field_id": "first"}]}), encoding="utf-8")
    output = tmp_path / "replay"
    result = run_point_detector_demand(net_file=network, route_file=routes, e1_additional_file=loops,
        expected_counts_csv=expected, output_dir=output, end_s=60, sumo_binary=shutil.which("sumo"), state_times_s=(5,),
        monitor_interval_s=monitor_interval, lane_intentions_file=intentions if local_hold else None)
    assert result["status"] == "measured" and result["healthy"] and result["observer_complete"], result
    assert result["exact_count_fit"] and result["exact_field_bins"] == 2
    assert result["field_metrics"]["MAE"] == result["field_metrics"]["max_abs_error"] == 0
    assert not result["formal_result"] and result["inputs_unchanged"]
    assert result["stall_monitor"]["status"] == "completed"
    assert "SUMO" in result["stall_monitor"]["native_version"][1]
    if local_hold:
        hold = result["stall_monitor"]["lane_section_hold"]
        assert hold["released_vehicles"] == 1 and hold["left_before_release"] == 0 and not hold["still_held"]
        assert [row["event"] for row in hold["events"]] == ["held", "released"]
        assert hold["events"][-1]["restored_mode"] == 1621
        assert (output / "lane-intentions.json").read_bytes() == intentions.read_bytes()
        assert not (output / "signal-programs.add.xml").exists()
    response = read_contributing_e1_passages(instant_file=output / "instant-e1.xml", periodic_file=output / "field-counts.xml",
        detector_ids=result["instant_to_periodic_ids"], step_length=.1)
    assert response["reconciled"] and len(response["passages"]) == 2
    assert all(path.read_bytes() == raw for path, raw in originals.items())
    snapshot = Path(result["state_snapshots"][0]["path"])
    state = ET.fromstring(gzip.decompress(snapshot.read_bytes()))
    assert float(state.get("time")) == 5 and state.find("vehicle[@id='v']") is not None


@pytest.mark.parametrize("times", [(-1,), (60,), (float("nan"),), (5, 5)])
def test_state_snapshots_require_distinct_times_within_the_run(tmp_path, times):
    with pytest.raises(ValueError, match="State times"):
        run_point_detector_demand(net_file=tmp_path / "unused", route_file=tmp_path / "unused",
            e1_additional_file=tmp_path / "unused", expected_counts_csv=tmp_path / "unused",
            output_dir=tmp_path / "out", end_s=60, state_times_s=times)


@pytest.mark.parametrize("limit", [0, -1, float("nan"), float("inf")])
def test_standstill_limit_must_be_positive_and_finite(tmp_path, limit):
    with pytest.raises(ValueError, match="positive finite"):
        run_point_detector_demand(net_file=tmp_path / "unused", route_file=tmp_path / "unused",
            e1_additional_file=tmp_path / "unused", expected_counts_csv=tmp_path / "unused",
            output_dir=tmp_path / "out", end_s=60, all_stopped_timeout_s=limit)


@pytest.mark.skipif(not shutil.which("netconvert") or not shutil.which("sumo"), reason="SUMO programs required")
@pytest.mark.parametrize("stop_duration", [2, 100])
def test_native_all_stopped_limit_retains_diagnostics_and_does_not_accept_a_fit(tmp_path, stop_duration):
    nodes, edges, network, routes, loops, expected = [tmp_path / name for name in
        ("nodes.xml", "edges.xml", "network.xml", "routes.xml", "loops.xml", "expected.csv")]
    nodes.write_text('<nodes><node id="a" x="0" y="0"/><node id="b" x="100" y="0"/></nodes>', encoding="utf-8")
    edges.write_text('<edges><edge id="in" from="a" to="b"/></edges>', encoding="utf-8")
    subprocess.run([shutil.which("netconvert"), "-n", str(nodes), "-e", str(edges), "-o", str(network)],
        capture_output=True, text=True, timeout=30, check=True)
    routes.write_text(f'''<routes><vehicle id="v" depart="10"><route edges="in"/>
      <stop lane="in_0" endPos="40" duration="{stop_duration}"/></vehicle></routes>''', encoding="utf-8")
    loops.write_text('<additional><inductionLoop id="field" lane="in_0" pos="80" period="30" file="unused"/></additional>', encoding="utf-8")
    expected.write_text('detector_id,begin,end,expected_total\nfield,0,30,1\nfield,30,60,0\n', encoding="utf-8")
    output = tmp_path / "replay"
    result = run_point_detector_demand(net_file=network, route_file=routes, e1_additional_file=loops,
        expected_counts_csv=expected, output_dir=output, end_s=60, sumo_binary=shutil.which("sumo"),
        all_stopped_timeout_s=3)
    monitor = result["stall_monitor"]
    if stop_duration == 2:
        assert result["healthy"] and monitor["status"] == "completed", result
        assert result["completion"]["arrived"] == 1
    else:
        assert result["status"] == "invalid" and not result["healthy"], result
        assert not result["exact_count_fit"] and result["field_metrics"] is None
        assert monitor["status"] == "all_stopped" and monitor["simulation_time_s"] < 60
        assert monitor["stop"]["duration_s"] == pytest.approx(3)
        assert monitor["stop"]["blocked_lanes"] == ["in_0"]
        assert monitor["stop"]["candidate_wait_cycles"] == []
        assert monitor["stop"]["vehicles"]["v"]["speed_m_s"] == 0
        assert result["completion"]["collisions"] == result["completion"]["teleports"] == 0
        state = ET.fromstring(gzip.decompress(Path(monitor["stop"]["state"]["path"]).read_bytes()))
        assert state.find("vehicle[@id='v']") is not None
        assert result["missing_field_bins"] > 0 and result["inputs_unchanged"]


def test_stopped_vehicle_report_keeps_cycles_separate_from_unrelated_queues():
    from types import SimpleNamespace
    from astra_sumo.core.simulation_stall import stopped_vehicle_report

    vehicle = SimpleNamespace(getLeader=lambda identity, distance: ("b", 0) if identity == "a" else None,
        getJunctionFoes=lambda identity, distance: [("a", 0, 0, 0, 0, "b_0", "a_0", True, False)] if identity == "b" else [],
        getLaneID=lambda identity: f"{identity}_0", getLanePosition=lambda identity: 10,
        getSpeed=lambda identity: 0, getMinGap=lambda identity: 2.5,
        getNextLinks=lambda identity: [], getNextTLS=lambda identity: [])
    report = stopped_vehicle_report(SimpleNamespace(vehicle=vehicle), ["b", "c", "a"])
    assert report["blocked_lanes"] == ["a_0", "b_0", "c_0"]
    assert report["candidate_wait_cycles"] == [{"vehicles": ["a", "b"], "lanes": ["a_0", "b_0"]}]
    assert {row["kind"] for row in report["waiting_relations"]} == {"near_leader", "junction_yield"}


def test_replay_rejects_an_existing_destination_without_modifying_inputs(tmp_path):
    files = [tmp_path / name for name in ("net", "routes", "loops", "expected")]
    for path in files:
        path.write_text("source", encoding="utf-8")
    with pytest.raises(ValueError, match="new replay directory"):
        run_point_detector_demand(net_file=files[0], route_file=files[1], e1_additional_file=files[2],
            expected_counts_csv=files[3], output_dir=tmp_path, end_s=60)
    assert all(path.read_text(encoding="utf-8") == "source" for path in files)


def test_backend_failure_keeps_all_expected_bins_as_missing(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from astra_sumo.core.detector_demand import read_csv_rows
    import astra_sumo.core.detector_demand_replay as replay

    network, routes, loops, expected = [tmp_path / name for name in ("network.xml", "routes.xml", "loops.xml", "expected.csv")]
    network.write_text('<net/>', encoding="utf-8")
    routes.write_text('<routes><vehicle id="v" depart="0"><route edges="a"/></vehicle></routes>', encoding="utf-8")
    loops.write_text('<additional><inductionLoop id="field" lane="a_0" pos="1" period="30" file="unused"/></additional>', encoding="utf-8")
    expected.write_text('detector_id,begin,end,expected_total\nfield,0,30,2\nfield,30,60,3\n', encoding="utf-8")
    monkeypatch.setattr(replay, "run_command", lambda *args, **kwargs:
        SimpleNamespace(status="fail", returncode=1, to_dict=lambda: {"status": "fail", "returncode": 1}))
    result = run_point_detector_demand(net_file=network, route_file=routes, e1_additional_file=loops,
        expected_counts_csv=expected, output_dir=tmp_path / "failed", end_s=60)
    assert result["status"] == "invalid" and result["errors"]
    assert result["native_status"] == "fail" and result["native_returncode"] == 1
    assert result["expected_field_bins"] == result["missing_field_bins"] == 2
    assert result["field_metrics"] is None and not result["exact_count_fit"]
    rows = read_csv_rows(tmp_path / "failed/field-comparison.csv")
    assert len(rows) == 2 and all(row["measured_nVehContrib"] == "" for row in rows)


def test_truncated_detector_recovery_keeps_zero_separate_from_a_missing_record(tmp_path):
    from astra_sumo.core.detector_demand import read_csv_rows

    raw, expected = tmp_path / "detectors.xml", tmp_path / "expected.csv"
    raw.write_text('<detector><interval id="field" begin="0" end="30" nVehContrib="0"/>'
                   '<interval id="field" begin="30" end="60" nVehContrib="', encoding="utf-8")
    expected.write_text('detector_id,begin,end,expected_total\nfield,0,30,2\nfield,30,60,3\n', encoding="utf-8")
    original = raw.read_bytes()
    result = recover_point_detector_measurements(detector_xml=raw, expected_counts_csv=expected, output_dir=tmp_path / "recovered")
    rows = read_csv_rows(tmp_path / "recovered/field-comparison.csv")
    assert result["status"] == "measured" and result["source_parse_error"]
    assert result["completed_records"] == result["recovered_field_bins"] == result["missing_field_bins"] == 1
    assert rows[0]["measured_nVehContrib"] == "0" and rows[1]["measured_nVehContrib"] == ""
    assert result["field_metrics"] is None and not result["exact_count_fit"]
    assert not result["native_completion_established"] and raw.read_bytes() == original


@pytest.mark.parametrize("record", [
    '<interval id="field" begin="0" end="30" nVehContrib="2"/>',
    '<interval id="field" begin="30" end="60" nVehContrib="1.5"/>',
    '<interval id="field" begin="30" end="60"/>',
])
def test_recovery_keeps_strict_count_and_duplicate_checks(tmp_path, record):
    raw, expected = tmp_path / "detectors.xml", tmp_path / "expected.csv"
    raw.write_text('<detector><interval id="field" begin="0" end="30" nVehContrib="2"/>' + record, encoding="utf-8")
    expected.write_text('detector_id,begin,end,expected_total\nfield,0,30,2\nfield,30,60,3\n', encoding="utf-8")
    result = recover_point_detector_measurements(detector_xml=raw, expected_counts_csv=expected, output_dir=tmp_path / "invalid")
    assert result["status"] == "invalid" and result["errors"]
    assert result["recovered_field_bins"] == 0 and result["missing_field_bins"] == 2
    assert result["field_metrics"] is None and not result["exact_count_fit"]


def test_complete_detector_records_alone_do_not_establish_native_validity(tmp_path):
    raw, expected = tmp_path / "detectors.xml", tmp_path / "expected.csv"
    raw.write_text('<detector><interval id="field" begin="0" end="30" nVehContrib="2"/></detector>', encoding="utf-8")
    expected.write_text('detector_id,begin,end,expected_total\nfield,0,30,2\n', encoding="utf-8")
    result = recover_point_detector_measurements(detector_xml=raw, expected_counts_csv=expected, output_dir=tmp_path / "diagnostic")
    assert result["exact_field_bins"] == 1 and result["source_parse_error"] is None
    assert not result["exact_count_fit"] and not result["formal_result"] and result["field_metrics"] is None


def test_timeout_reports_completed_field_bins_without_accepting_a_fit(tmp_path, monkeypatch):
    from types import SimpleNamespace
    import astra_sumo.core.detector_demand_replay as replay

    network, routes, loops, expected = [tmp_path / name for name in ("net.xml", "routes.xml", "loops.xml", "counts.csv")]
    network.write_text('<net/>', encoding="utf-8")
    routes.write_text('<routes><vehicle id="v" depart="0"><route edges="a"/></vehicle></routes>', encoding="utf-8")
    loops.write_text('<additional><inductionLoop id="field" lane="a_0" pos="1" period="30" file="unused"/></additional>', encoding="utf-8")
    expected.write_text('detector_id,begin,end,expected_total\nfield,0,30,2\nfield,30,60,3\n', encoding="utf-8")

    def timeout(command, *, cwd, timeout_seconds):
        (cwd / "summary.xml").write_text('<summary><step', encoding="utf-8")
        (cwd / "field-counts.xml").write_text('<detector><interval id="field" begin="0" end="30" nVehContrib="2"/><interval', encoding="utf-8")
        return SimpleNamespace(status="timeout", returncode=None, to_dict=lambda: {"status": "timeout", "returncode": None})

    monkeypatch.setattr(replay, "run_command", timeout)
    result = run_point_detector_demand(net_file=network, route_file=routes, e1_additional_file=loops,
        expected_counts_csv=expected, output_dir=tmp_path / "timed-out", end_s=60)
    assert result["status"] == "invalid" and result["native_status"] == "timeout"
    assert result["missing_field_bins"] == result["exact_field_bins"] == 1
    assert result["partial_measurements"]["recovered_field_bins"] == 1
    assert not result["observer_complete"] and not result["exact_count_fit"] and result["field_metrics"] is None
    assert replay.read_csv_rows(tmp_path / "timed-out/field-comparison.csv")[0]["measured_nVehContrib"] == "2"


def test_frozen_timed_out_ring1_retains_completed_field_counts(tmp_path):
    from astra_sumo.core.candidate_contracts import file_sha256
    from astra_sumo.core.detector_demand import read_csv_rows, write_csv

    root = Path(__file__).parents[1]
    package = root / "benchmarks/hamburg_ring1_road_scope_20261020_v1"
    evidence = json.loads((package / "clearance-timeout-evidence-v1.json").read_text(encoding="utf-8"))
    fixture = evidence["files"]["truncated_e1"]
    raw = tmp_path / "native-e1.xml"
    raw.write_bytes(gzip.decompress((root / fixture["compressed"]["path"]).read_bytes()))
    assert file_sha256(raw) == fixture["uncompressed_sha256"]
    rows = read_csv_rows(root / "benchmarks/hamburg_ring1_count_window_20261020_v1/field-counts-15min.csv")
    shifted = [{**r, "begin": str(int(r["begin"])+3600), "end": str(int(r["end"])+3600)} for r in rows]
    expected = tmp_path / "counts.csv"
    write_csv(expected, shifted, list(shifted[0]))
    result = recover_point_detector_measurements(detector_xml=raw, expected_counts_csv=expected, output_dir=tmp_path / "recovered")
    assert result["completed_records"] == 137
    assert result["recovered_field_bins"] == 85 and result["missing_field_bins"] == 45
    assert result["exact_field_bins"] == 7 and len(read_csv_rows(tmp_path / "recovered/field-comparison.csv")) == 130
    assert result["field_metrics"] is None and not result["exact_count_fit"] and result["inputs_unchanged"]
