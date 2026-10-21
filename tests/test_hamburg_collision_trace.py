import gzip
import json
from pathlib import Path
import xml.etree.ElementTree as ET

import pytest

from astra_sumo.core.hamburg_collision_trace import compare_collision_trace, prepare_collision_trace, read_trace_source, run_collision_trace


ROOT = Path(__file__).parents[1]
CASE = ROOT / "benchmarks/hamburg_ring1_through_route_coverage_20261021_v1"


def test_collision_trace_changes_only_observation_outputs_and_paths(tmp_path):
    executable = tmp_path / "sumo.exe"
    executable.write_bytes(b"not executed")
    out = tmp_path / "trace"
    report = prepare_collision_trace(case_dir=CASE, output_dir=out, sumo_binary=executable)
    assert len(report["observed_vehicles"]) == 4 and not report["formal_result"]
    before = json.loads(read_trace_source(CASE / "native", "native-request.json"))
    after = json.loads((out / "native-request.json").read_bytes())
    old = before["route_file"].replace("\\", "/").rsplit("/", 1)[0]
    assert after["command"][1:len(before["command"])] == [v.replace("\\", "/").replace(old, out.as_posix()) for v in before["command"][1:]]
    assert after["end_s"] == 3600 and after["all_stopped_timeout_s"] == 300
    assert after["command"][len(before["command"]):] == [
        "--fcd-output", str(out / "collision-fcd.xml"), "--device.fcd.probability", "0",
        "--device.fcd.explicit", ",".join(report["observed_vehicles"]),
        "--fcd-output.skip-empty", "true", "--lanechange-output", str(out / "lanechanges.xml")]
    for name in ("network.net.xml", "demand.rou.xml", "signal-programs.add.xml", "source-e1.add.xml", "lane-intentions.json", "expected-counts.csv"):
        assert (out / name).read_bytes() == read_trace_source(CASE / "native", name)
    for name in ("e1.add.xml", "instant.add.xml"):
        old_elements = ET.fromstring(read_trace_source(CASE / "native", name))
        new_elements = ET.parse(out / name).getroot()
        assert [r.attrib | {"file": "output"} for r in old_elements] == [r.attrib | {"file": "output"} for r in new_elements]
        assert all(Path(r.get("file")).parent == out for r in new_elements)
    with pytest.raises(ValueError, match="new collision-trace"):
        prepare_collision_trace(case_dir=CASE, output_dir=out, sumo_binary=executable)


@pytest.mark.parametrize("changed_count", [False, True])
def test_frozen_trace_matches_traffic_and_detects_changed_measurements(tmp_path, monkeypatch, changed_count):
    from astra_sumo.core import hamburg_collision_trace
    from astra_sumo.core.command_runner import CommandResult

    trace = ROOT / "benchmarks/hamburg_ring1_collision_trace_20261021_v1"
    for name in ("native-request.json", "expected-counts.csv", "stall-monitor.json", "collisions.xml", "field-counts.xml", "summary.xml", "vehicle-routes.xml"):
        (tmp_path / name).write_bytes(read_trace_source(trace / "native", name))
    if changed_count:
        xml = ET.parse(tmp_path / "field-counts.xml")
        row = xml.getroot().find("interval")
        row.set("nVehContrib", str(int(row.get("nVehContrib")) + 1))
        xml.write(tmp_path / "field-counts.xml", encoding="utf-8")
    monkeypatch.setattr(hamburg_collision_trace, "run_command", lambda *a, **k: CommandResult(
        command=[], cwd=str(tmp_path), status="pass", returncode=0))
    report = run_collision_trace(case_dir=CASE, prepared_dir=tmp_path)
    assert report["reproduction_matched"] is not changed_count
    assert report["checks"]["same_vehicle_route_exit_times"]
    assert not report["run_valid"] and not report["formal_result"] and report["collisions"] == 2
    geometry = json.loads(read_trace_source(trace / "analysis", "geometry-check.json"))
    assert geometry["event0"]["front_separation_m"] == pytest.approx(.6945741796392354)
    assert geometry["event1"]["rear_position_error_m"] < 1e-6
    assert geometry["event1"]["victim_front_position_m"] < geometry["event1"]["vehicle_length_m"]
    foes = json.loads(read_trace_source(trace / "runtime-foes", "runtime-foes.json"))["lanes"]
    assert ":14398547_3_2" in foes[":14398547_3_1"]["runtime_internal_foes"]
    assert ":205069138_0_1" in foes[":205069138_2_2"]["runtime_internal_foes"]


def test_prefix_trace_preserves_all_source_targets_and_only_shortens_output_horizon(tmp_path):
    executable = tmp_path / "sumo.exe"
    executable.write_bytes(b"not executed")
    out = tmp_path / "prefix"
    report = prepare_collision_trace(case_dir=CASE, output_dir=out, sumo_binary=executable, end_s=2000)
    request = json.loads((out / "native-request.json").read_bytes())
    assert report["source_end_s"] == 3600 and report["trace_end_s"] == request["end_s"] == 2000
    assert len(report["observed_vehicles"]) == 2
    assert request["command"][request["command"].index("--end") + 1] == "2000"
    assert all(r.get("end") == "2000" for r in ET.parse(out / "e1.add.xml").getroot())
    assert (out / "expected-counts.csv").read_bytes() == read_trace_source(CASE / "native", "expected-counts.csv")
    assert (out / "demand.rou.xml").read_bytes() == read_trace_source(CASE / "native", "demand.rou.xml")
    with pytest.raises(ValueError, match="native-step-aligned"):
        prepare_collision_trace(case_dir=CASE, output_dir=tmp_path / "off-step", sumo_binary=executable, end_s=2000.05)


@pytest.mark.parametrize("compressed", [False, True])
def test_saved_observations_compare_without_running_sumo_or_overwriting_records(tmp_path, monkeypatch, compressed):
    from astra_sumo.core import hamburg_collision_trace

    source = ROOT / "benchmarks/hamburg_ring1_collision_trace_20261021_v1/native"
    for name in ("native-request.json", "expected-counts.csv", "stall-monitor.json", "collisions.xml", "field-counts.xml", "summary.xml", "vehicle-routes.xml", "command.json"):
        (tmp_path / name).write_bytes(read_trace_source(source, name))
    if compressed:
        for name in ("stall-monitor.json", "collisions.xml", "field-counts.xml", "summary.xml", "vehicle-routes.xml"):
            original = tmp_path / name
            original.with_name(name + ".gz").write_bytes(gzip.compress(original.read_bytes(), mtime=0))
            original.unlink()
    (tmp_path / "trace-check.json").write_bytes(b"keep the preceding comparison")
    before = {p.name: p.read_bytes() for p in tmp_path.iterdir()}

    def unexpected_execution(*args, **kwargs):
        raise AssertionError("A saved-observation comparison must not run SUMO.")

    monkeypatch.setattr(hamburg_collision_trace, "run_command", unexpected_execution)
    report = compare_collision_trace(case_dir=CASE, prepared_dir=tmp_path)
    assert report["reproduction_matched"] and not report["run_valid"]
    assert {p.name: p.read_bytes() for p in tmp_path.iterdir()} == before


def test_prefix_comparison_ignores_partial_interval_and_future_events_without_filling_zeros(tmp_path, monkeypatch):
    from astra_sumo.core import hamburg_collision_trace
    from astra_sumo.core.command_runner import CommandResult

    trace = ROOT / "benchmarks/hamburg_ring1_collision_trace_20261021_v1/native"
    request = json.loads(read_trace_source(trace, "native-request.json"))
    request["end_s"] = 2000
    (tmp_path / "native-request.json").write_text(json.dumps(request), encoding="utf-8")
    (tmp_path / "expected-counts.csv").write_bytes(read_trace_source(trace, "expected-counts.csv"))
    monitor = json.loads(read_trace_source(trace, "stall-monitor.json"))
    monitor["simulation_time_s"] = 2000
    monitor["lane_section_hold"]["events"] = [r for r in monitor["lane_section_hold"]["events"] if r["time_s"] <= 2000]
    (tmp_path / "stall-monitor.json").write_text(json.dumps(monitor), encoding="utf-8")
    for name, tag, cutoff in (("summary.xml", "step", "time"), ("vehicle-routes.xml", "vehicle", "arrival"), ("collisions.xml", "collision", "time")):
        original = ET.fromstring(read_trace_source(trace, name))
        selected = ET.Element(original.tag)
        selected.extend(r for r in original.findall(tag) if float(r.get(cutoff)) < 2000)
        if tag == "vehicle":
            selected[-1].tail = "\n"
        ET.ElementTree(selected).write(tmp_path / name, encoding="utf-8")
    original = ET.fromstring(read_trace_source(trace, "field-counts.xml"))
    selected = ET.Element(original.tag)
    selected.extend(r for r in original.findall("interval") if float(r.get("end")) <= 2000)
    ET.SubElement(selected, "interval", id="partial_diagnostic_only", begin="1800", end="2000", nVehContrib="99")
    ET.ElementTree(selected).write(tmp_path / "field-counts.xml", encoding="utf-8")
    monkeypatch.setattr(hamburg_collision_trace, "run_command", lambda *a, **k: CommandResult(command=[], cwd=str(tmp_path), status="pass", returncode=0))
    result = run_collision_trace(case_dir=CASE, prepared_dir=tmp_path)
    assert result["reproduction_matched"] and result["collisions"] == 1
    assert result["compared_E1_intervals"] == result["source_target_rows_available"] == 26
    assert result["source_target_rows_total"] == 52 and not result["run_valid"]
    routes = ET.parse(tmp_path / "vehicle-routes.xml")
    routes.getroot().find("vehicle/route").set("exitTimes", "changed")
    routes.write(tmp_path / "vehicle-routes.xml", encoding="utf-8")
    changed = run_collision_trace(case_dir=CASE, prepared_dir=tmp_path)
    assert not changed["reproduction_matched"] and not changed["checks"]["same_vehicle_route_exit_times"]


def test_late_event_trace_keeps_target_coverage_and_reconstructs_rear_position():
    import math

    from shapely.geometry import LineString, Point

    case = ROOT / "benchmarks/hamburg_ring1_late_collision_trace_20261021_v1"
    checked = json.loads(read_trace_source(case / "native", "trace-check.json"))
    assert checked["reproduction_matched"] and all(checked["checks"].values())
    assert checked["source_target_rows_total"] == 130 and checked["source_target_rows_available"] == 78
    assert checked["compared_E1_intervals"] == 130 and checked["trace_end_s"] == 9420
    assert not checked["run_valid"] and not checked["formal_result"]
    geometry = json.loads(read_trace_source(case / "analysis", "geometry-check.json"))
    change = geometry["observed_lane_changes"][0]
    assert change["id"] == geometry["native_event"]["victim"]
    assert float(change["time"]) == float(geometry["native_event"]["time"]) == 9402.6
    assert change["from"] == "139083723#0_3" and change["to"] == "139083723#0_2"
    assert float(change["pos"]) < geometry["vehicle_length_m"] == 5
    network = ET.fromstring(read_trace_source(case / "native", "network.net.xml"))
    lane = network.find(f"edge/lane[@id='{geometry['victim_last_internal_lane']}']")
    shape = LineString([tuple(map(float, p.split(','))) for p in lane.get("shape").split()])
    rear_position = float(lane.get("length")) + float(change["pos"]) - 5
    expected_rear = shape.interpolate(rear_position / float(lane.get("length")), normalized=True)
    assert expected_rear.distance(Point(geometry["native_rear_xy"])) < 1e-6
    assert math.dist(geometry["native_rear_xy"], geometry["reconstructed_rear_xy"]) < 1e-6
