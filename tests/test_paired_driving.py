import json
from pathlib import Path
import shutil
import subprocess
import xml.etree.ElementTree as ET

import pytest

from astra_sumo.core import paired_driving as module


def _inputs(tmp_path):
    paths = {name: tmp_path / f"{name}.net.xml" for name in ("source", "candidate")}
    for path in paths.values():
        path.write_text("<net/>", encoding="utf-8")
    routes = tmp_path / "routes.xml"
    routes.write_text('''<routes><vehicle id="car-A" depart="0"><route edges="a b"/></vehicle>
        <vehicle id="car-B" depart="1"><route edges="a b"/></vehicle></routes>''', encoding="utf-8")
    return dict(networks=paths, reference="source", routes=routes, seeds=[17, 42], horizon_s=60,
                output_dir=tmp_path / "comparison")


def test_failed_run_and_missing_arrivals_keep_the_original_cohort(tmp_path, monkeypatch):
    request = _inputs(tmp_path)
    calls = []

    def attempt(**kwargs):
        calls.append(kwargs)
        # One candidate cannot start. It must retain both requests in the pair.
        if kwargs["net_file"].name == "candidate.net.xml" and kwargs["seed"] == 17:
            return {"inspection": {"status": "fail", "summary": {}}, "command": {"returncode": 1}}
        rows = ET.Element("tripinfos")
        ET.SubElement(rows, "tripinfo", id="car-A", duration="11", arrival="11")
        if kwargs["net_file"].name == "source.net.xml":
            ET.SubElement(rows, "tripinfo", id="car-B", duration="12", arrival="13")
        ET.ElementTree(rows).write(kwargs["output_dir"] / "driving_end60_tripinfo.xml")
        result = {"inspection": {"status": "pass", "summary": {"arrived": len(rows)}}, "command": {"status": "pass", "returncode": 0}}
        if kwargs["net_file"].name == "candidate.net.xml":
            collisions = kwargs["output_dir"] / "collisions.xml"
            collisions.write_text('<collisions><collision collider="car-A" victim="car-B"/></collisions>', encoding="utf-8")
            result["diagnostic_outputs"] = {"collisions": str(collisions)}
        return result

    monkeypatch.setattr(module, "_run_attempt", attempt)
    report = module.compare_driving_cohort(**request)
    assert len(calls) == 4
    assert all(call["route_file"] == request["routes"] for call in calls)
    assert all(call["vehicle_count"] == 2 for call in calls)
    assert report["pair_validity_counts"] == {"invalid": 2}
    assert report["execution_failure_count"] == 1
    assert report["status"] == "execution_failed"
    assert report["runs"]["42"]["candidate"]["execution_status"] == "completed"
    assert report["performance_mean"] is None
    assert report["pairs"][0]["trips"][0]["candidate"] is None
    assert report["pairs"][1]["trips"][1]["candidate"] is None
    affected = report["pairs"][1]["trips"][0]
    assert affected["duration_delta_s"] is None
    assert affected["recorded_duration_delta_s"] == 0
    assert affected["duration_comparison_status"] == "collision_affected"
    assert affected["candidate_observation"]["status"] == "arrived_with_recorded_collision"
    assert all([row["vehicle_id"] for row in pair["trips"]] == ["car-A", "car-B"] for pair in report["pairs"])
    assert (request["output_dir"] / "candidate-17/result.json").is_file()
    assert json.loads((request["output_dir"] / "plan.json").read_text())["vehicle_ids"] == ["car-A", "car-B"]


def test_input_changes_invalidate_pairs_without_hiding_measured_trips(tmp_path, monkeypatch):
    request = _inputs(tmp_path)

    def attempt(**kwargs):
        Path(request["networks"]["candidate"]).write_text("<net changed='true'/>", encoding="utf-8")
        rows = ET.Element("tripinfos")
        for identity in ("car-A", "car-B"):
            ET.SubElement(rows, "tripinfo", id=identity, duration="10" if kwargs["net_file"].name.startswith("source") else "15")
        ET.ElementTree(rows).write(kwargs["output_dir"] / "driving_end60_tripinfo.xml")
        return {"inspection": {"status": "pass", "summary": {"arrived": 2}}, "command": {"status": "pass", "returncode": 0}}

    monkeypatch.setattr(module, "_run_attempt", attempt)
    report = module.compare_driving_cohort(**request)
    assert len(report["input_changes"]) == 1
    assert all(pair["comparison_status"] == "diagnostic_invalid_pair" for pair in report["pairs"])
    assert all(row["duration_delta_s"] == 5 for pair in report["pairs"] for row in pair["trips"])


@pytest.mark.parametrize("changes", [{"seeds": [True]}, {"seeds": [1, 1]}, {"horizon_s": 0},
                                      {"reference": "absent"}, {"networks": {"../bad": "a", "source": "b"}}])
def test_invalid_requests_do_not_create_an_output(tmp_path, changes):
    request = _inputs(tmp_path)
    request.update(changes)
    with pytest.raises(ValueError):
        module.compare_driving_cohort(**request)
    assert not request["output_dir"].exists()


@pytest.mark.parametrize("trip,depart,horizon,execution,status,censored,time", [
    ({"arrival": "12"}, 2, 20, "completed", "arrived", False, 10),
    (None, 2, 20, "completed", "unfinished_at_horizon", True, 18),
    (None, 20, 20, "completed", "not_requested_before_horizon", False, None),
    (None, 2, 20, "failed", "unobserved_execution_failed", None, None),
    ({"arrival": "nan"}, 2, 20, "completed", "invalid_trip_record", None, None),
])
def test_request_time_keeps_censored_and_unobserved_vehicles_separate(trip, depart, horizon, execution, status, censored, time):
    assert module._request_observation(trip, depart, horizon, execution) == {
        "status": status, "right_censored": censored, "time_since_requested_departure_s": time}


def test_collision_participants_do_not_become_ordinary_arrivals():
    event = {"time": "5", "type": "junction", "lane": ":j_0_0", "collider": "a", "victim": "b"}
    case = {"execution_status": "completed", "trips": {key: {"arrival": "20"} for key in ("a", "b", "c")},
            "diagnostics": {"collisions": {"records": [event]}}}
    collider = module._driving_observation(case, "a", 0, 30)
    victim = module._driving_observation(case, "b", 0, 30)
    ordinary = module._driving_observation(case, "c", 0, 30)
    assert collider["status"] == victim["status"] == "arrived_with_recorded_collision"
    assert collider["teleported_after_collision"] is True
    assert victim["teleported_after_collision"] is False
    assert ordinary["status"] == "arrived" and ordinary["collision_affected"] is False
    assert collider["collision_events"] == [event]


@pytest.mark.skipif(not shutil.which("sumo") or not shutil.which("netconvert"), reason="SUMO required")
def test_native_runner_keeps_arrivals_unfinished_positions_and_future_requests(tmp_path):
    nodes, edges, network = [tmp_path / name for name in ("nodes.xml", "edges.xml", "network.net.xml")]
    nodes.write_text('<nodes><node id="a" x="0" y="0"/><node id="b" x="50" y="0"/></nodes>', encoding="utf-8")
    edges.write_text('<edges><edge id="road" from="a" to="b"/></edges>', encoding="utf-8")
    result = subprocess.run(["netconvert", "-n", str(nodes), "-e", str(edges), "-o", str(network)], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    routes = tmp_path / "routes.xml"
    routes.write_text('''<routes><vehicle id="arrived" depart="0"><route edges="road"/></vehicle>
        <vehicle id="pending" depart="9"><route edges="road"/></vehicle>
        <vehicle id="future" depart="100"><route edges="road"/></vehicle></routes>''', encoding="utf-8")
    report = module.compare_driving_cohort(networks={"source": network, "candidate": network}, reference="source",
        routes=routes, seeds=[17], horizon_s=10, output_dir=tmp_path / "comparison")
    assert report["execution_failure_count"] == 0
    rows = {row["vehicle_id"]: row for row in report["pairs"][0]["trips"]}
    assert rows["arrived"]["source_observation"]["status"] == "arrived"
    assert rows["pending"]["source_observation"]["right_censored"] is True
    assert rows["pending"]["source_observation"]["time_since_requested_departure_s"] == 1
    assert rows["future"]["source_observation"]["status"] == "not_requested_before_horizon"
    for case in report["runs"]["17"].values():
        vehicle_routes = case["diagnostics"]["vehicle_routes"]
        assert vehicle_routes["sha256"]
        native = {row.get("id"): row for row in ET.parse(vehicle_routes["path"]).getroot().findall("vehicle")}
        assert "pending" in native
        assert float(native["pending"].get("arrival", "-1")) < 0
        assert float(native["arrived"].get("arrival")) > 0
        assert case["diagnostics"]["collisions"]["sha256"]
        positions = case["diagnostics"]["last_step_positions"]
        assert positions["sha256"]
        assert positions["recorded_time_s"] == 9
        assert {row["id"] for row in positions["vehicles"]} == {"pending"}
