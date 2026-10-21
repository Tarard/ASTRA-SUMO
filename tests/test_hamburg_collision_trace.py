import json
from pathlib import Path
import xml.etree.ElementTree as ET

import pytest

from astra_sumo.core.hamburg_collision_trace import prepare_collision_trace, read_trace_source, run_collision_trace


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
    for name in ("stall-monitor.json", "collisions.xml", "field-counts.xml", "summary.xml", "vehicle-routes.xml"):
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
