import json
from pathlib import Path
import xml.etree.ElementTree as ET

import pytest

from astra_sumo.core.hamburg_route_delay import censored_route_intervals, diagnose_hamburg_z15


def fixture():
    return {"demand": ET.fromstring('<routes><vehicle id="a" depart="1"><route edges="x y"/></vehicle><vehicle id="b" depart="2"><route edges="x y"/></vehicle><vehicle id="c" depart="3"><route edges="x y"/></vehicle></routes>'),
        "arrived_routes": ET.fromstring('<routes><vehicle id="a" arrival="8"><route edges="x y" exitTimes="5 8"/></vehicle></routes>'),
        "snapshot": ET.fromstring('<snapshot version="1.27.1" time="10"><route id="!b" edges="x y"/><vehicle id="b" route="!b"><device id="vehroute_b" exitTimes="7000" edge="x"/></vehicle><lane id="y_0"><vehicles value="b"/></lane></snapshot>'),
        "trips": ET.fromstring('<tripinfos><tripinfo id="a" depart="2"/><tripinfo id="b" depart="4"/></tripinfos>'),
        "selected_ids": {"a", "b", "c"}}


def test_intervals_keep_pending_requests_and_raw_millisecond_prefix_times():
    population, intervals = censored_route_intervals(**fixture())
    assert [r["status"] for r in population] == ["arrived", "running", "not_inserted"]
    assert [r["insertion_wait_s"] for r in population] == [1, 2, None]
    assert population[2]["insertion_wait_lower_bound_s"] == 7
    assert [(r["road"], r["elapsed_s"], r["right_censored"]) for r in intervals] == [
        ("x", 3, False), ("y", 3, False), ("x", 3, False), ("y", 3, True)]
    assert sum(r["elapsed_s"] for r in intervals if r["vehicle_id"] == "b") == 10 - 4


@pytest.mark.parametrize("fault", ["missing_stock", "wrong_route", "future_exit", "wrong_last_road", "unsupported_version"])
def test_interval_accounting_rejects_missing_or_inconsistent_native_records(fault):
    data = fixture()
    if fault == "missing_stock":
        data["snapshot"].find("lane/vehicles").set("value", "")
    elif fault == "wrong_route":
        data["snapshot"].find("route").set("edges", "other y")
    elif fault == "future_exit":
        data["snapshot"].find("vehicle/device").set("exitTimes", "11000")
    elif fault == "wrong_last_road":
        data["snapshot"].find("vehicle/device").set("edge", "y")
    else:
        data["snapshot"].set("version", "old")
    with pytest.raises(ValueError):
        censored_route_intervals(**data)


def test_frozen_z15_diagnosis_retains_all_requested_vehicles_and_checks_source_k8(tmp_path):
    root = Path(__file__).parents[1]
    control = root / "benchmarks/hamburg_ring1_m2_control_20261021_v1"
    result = diagnose_hamburg_z15(case_dir=root / "benchmarks/hamburg_ring1_ballindamm_entry_probe_20261021_v1",
        bindings_file=control / "link-bindings.json.gz", ocit_file=control / "inputs/ocit_200.xml", output_dir=tmp_path / "diagnosis")
    assert result["requested_initialization_vehicles"] == 309
    assert result["population_status"] == {"arrived": 211, "running": 54, "not_inserted": 44}
    assert result["K8_cycle_s"] == 90 and result["K8_green_s"] == 5
    assert result["K8_front_exits"] == 230 and result["inserted_without_K8_front_exit"] == 35
    assert result["native_E1_count_0_3600"] == result["complete_field_passages_at_snapshot"] == 222
    assert result["after_K8_before_complete_field_passage"] == 8
    assert result["sources_unchanged"] and result["new_simulations"] == 0
    saved = json.loads((tmp_path / "diagnosis/K8-control.json").read_bytes())
    assert saved["source_states_retained"] and saved["inlet_motor_lanes"] == 2
