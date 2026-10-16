import json
from pathlib import Path
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
        ET.SubElement(rows, "tripinfo", id="car-A", duration="11")
        if kwargs["net_file"].name == "source.net.xml":
            ET.SubElement(rows, "tripinfo", id="car-B", duration="12")
        ET.ElementTree(rows).write(kwargs["output_dir"] / "driving_end60_tripinfo.xml")
        return {"inspection": {"status": "pass", "summary": {"arrived": len(rows)}}, "command": {"status": "pass", "returncode": 0}}

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
