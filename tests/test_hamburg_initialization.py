from copy import deepcopy
import gzip
import json
from pathlib import Path
import xml.etree.ElementTree as ET

import pytest

from astra_sumo.core.detector_demand import read_csv_rows
from astra_sumo.core.hamburg_initialization import add_initialization_population, prepare_hamburg_entry_alternative, prepare_hamburg_initialization
from astra_sumo.core.hamburg_readme_results import vehicle_field_passage_rows


def population():
    routes = ET.fromstring('<routes><vType id="car"/><vehicle id="a" type="car" depart="3700"><route edges="in out"/><stop lane="in_1" speed="10" endPos="22.5"/></vehicle><vehicle id="b" type="car" depart="3800"><route edges="in out"/><stop lane="in_1" speed="10" endPos="22.5"/></vehicle><vehicle id="c" type="car" depart="4600"><route edges="other out"/></vehicle></routes>')
    intent = {"vehicles": [{"vehicle_id": name, "field_id": "f", "profile_index": profile,
        "field_row": period * 13, "depart_s": depart, "free_flow_field_offset_s": 117.5}
        for name, profile, period, depart in [("a", 1, 0, 3700), ("b", 1, 0, 3800), ("c", 2, 1, 4600)]]}
    counts = [{"detector_id": "f", "begin": b, "end": b + 900, "expected_total": n, "quality_status": "complete"}
              for b, n in [(0, 2), (900, 3), (1800, 0), (2700, 1)]]
    return routes, intent, counts


def test_initialization_preserves_main_and_apportions_without_inventing_od():
    source, original, counts = population()
    before, before_intent = ET.tostring(source), deepcopy(original)
    demand, intent, allocations = add_initialization_population(source, original, counts)
    assert ET.tostring(source) == before and original == before_intent
    assert len(demand.findall("vehicle")) == len(intent["vehicles"]) == 9
    assert [a["vehicles"] for a in allocations] == [1, 1, 2, 1, 0, 0, 1, 0]
    for vehicle in source.findall("vehicle"):
        assert ET.tostring(vehicle) == ET.tostring(demand.find(f"vehicle[@id='{vehicle.get('id')}']"))
    main = [r for r in intent["vehicles"] if r["population_role"] == "main"]
    init = [r for r in intent["vehicles"] if r["population_role"] == "initialization"]
    assert [r["target_begin_s"] for r in main] == [3600, 3600, 4500]
    assert all(r["field_row"] is None and 0 < r["depart_s"] < 3600 for r in init)
    assert all(r["target_begin_s"] + 150 < r["depart_s"] + 117.5 < r["target_begin_s"] + 700 for r in init)
    times = [float(v.get("depart")) for v in demand.findall("vehicle")]
    assert times == sorted(times)


@pytest.mark.parametrize("fault", ["missing", "duplicate", "incomplete", "fractional", "negative", "offset"])
def test_initialization_rejects_invalid_sources(fault):
    routes, intent, rows = population()
    if fault == "missing":
        rows.pop()
    elif fault == "duplicate":
        rows.append(rows[0])
    elif fault == "incomplete":
        rows[0]["quality_status"] = "missing"
    elif fault == "fractional":
        rows[0]["expected_total"] = 1.2
    elif fault == "negative":
        rows[0]["expected_total"] = -1
    else:
        intent["vehicles"][0]["free_flow_field_offset_s"] = 151
    with pytest.raises(ValueError):
        add_initialization_population(routes, intent, rows)


def test_passage_diagnostics_separate_initialization_from_legacy_main_bins():
    intentions = [{"vehicle_id": "warm", "field_id": "f", "field_row": None, "population_role": "initialization", "target_begin_s": 2700},
                  {"vehicle_id": "main", "field_id": "f", "field_row": 0}]
    passage = {"status": "measured", "passages": [{"vehicle_id": "warm", "detector_id": "f",
               "time_s": 3599.99, "interval_begin_s": 3600}]}
    rows = vehicle_field_passage_rows(intentions, passage)
    assert rows[0]["planned_bin"] == -1 and rows[0]["actual_bin"] == 0 and rows[0]["bin_shift"] == 1
    assert rows[0]["population_role"] == "initialization"
    assert rows[1]["population_role"] == "main" and rows[1]["planned_bin"] == 0
    assert rows[1]["actual_bin"] is rows[1]["actual_native_time_s"] is None
    assert rows[1]["actual_native_field_passages"] == 0
    assert vehicle_field_passage_rows(intentions, {"status": "unavailable"})[0]["actual_native_field_passages"] is None
    assert vehicle_field_passage_rows(intentions, {"status": "unavailable"})[0]["all_native_field_passages"] is None


def test_frozen_hamburg_population_retains_all_targets_and_driving_inputs(tmp_path):
    root = Path(__file__).parents[1]
    case = root / "benchmarks/hamburg_ring1_lane_section_hold_full_20261021_v1"
    counts = root / "benchmarks/hamburg_ring1_initialization_counts_20261021_v1/initialization-counts-15min.csv"
    out = tmp_path / "candidate"
    report = prepare_hamburg_initialization(case_dir=case, counts_file=counts, output_dir=out)
    assert report["vehicles"] == 8640 and report["source_files_unchanged"]
    for name in ("network.net.xml", "control.add.xml", "field-loops.add.xml", "expected-counts.csv"):
        assert (out / name).read_bytes() == (case / "inputs" / name).read_bytes()
    demand = ET.parse(out / "demand.rou.xml").getroot()
    original = ET.fromstring(gzip.decompress((case / "inputs/demand.rou.xml.gz").read_bytes()))
    assert ET.tostring(demand.find("vType")) == ET.tostring(original.find("vType"))
    intentions = json.loads((out / "lane-intentions.json").read_bytes())["vehicles"]
    added = [r for r in intentions if r["population_role"] == "initialization"]
    assert len(added) == 1953 and len(intentions) == 8640
    allocated = {}
    for r in added:
        key = r["field_id"], r["target_begin_s"]
        allocated[key] = allocated.get(key, 0) + 1
    assert all(allocated.get((r["detector_id"], float(r["begin"])), 0) == int(r["expected_total"]) for r in read_csv_rows(counts))
    assert json.loads((out / "scenario.json").read_bytes())["all_integer_rows_exact"] is None
    with pytest.raises(ValueError, match="new initialization"):
        prepare_hamburg_initialization(case_dir=case, counts_file=counts, output_dir=out)


def test_entry_alternative_changes_only_the_declared_prefix_and_retains_all_requests(tmp_path):
    root = Path(__file__).parents[1]
    case = root / "benchmarks/hamburg_ring1_initialization_population_20261021_v1"
    profile_file = root / "benchmarks/hamburg_ring1_m1_2_demand_pool_20261021_v1/base/ideal-lane-profiles.json.gz"
    out = tmp_path / "alternative"
    report = prepare_hamburg_entry_alternative(case_dir=case, profiles_file=profile_file, output_dir=out)
    assert report["changed_by_population"] == {"main": 890, "initialization": 307}
    assert report["source_files_unchanged"]
    for name in ("network.net.xml", "control.add.xml", "field-loops.add.xml", "expected-counts.csv", "initialization-counts.csv"):
        assert (out / name).read_bytes() == (case / "inputs" / name).read_bytes()
    source = ET.fromstring(gzip.decompress((case / "inputs/demand.rou.xml.gz").read_bytes()))
    target = ET.parse(out / "demand.rou.xml").getroot()
    old = {v.get("id"): v for v in source.findall("vehicle")}
    new = {v.get("id"): v for v in target.findall("vehicle")}
    assert old.keys() == new.keys() and len(new) == 8640
    changed = 0
    for key, a in old.items():
        b = deepcopy(new[key])
        assert a.attrib == b.attrib
        if a.find("route").get("edges") != b.find("route").get("edges"):
            changed += 1
            assert a.find("route").get("edges").split()[0] == "543550497#0"
            assert b.find("route").get("edges").split()[0] == "551288837#2"
            b.find("route").set("edges", a.find("route").get("edges"))
        assert ET.tostring(a) == ET.tostring(b)
    assert changed == 1197
    before = json.loads(gzip.decompress((case / "inputs/lane-intentions.json.gz").read_bytes()))["vehicles"]
    after = json.loads((out / "lane-intentions.json").read_bytes())["vehicles"]
    for a, b in zip(before, after):
        assert {k: v for k, v in a.items() if k not in {"profile_index", "free_flow_field_offset_s"}} == {k: v for k, v in b.items() if k not in {"profile_index", "free_flow_field_offset_s"}}
    with pytest.raises(ValueError, match="new entry-alternative"):
        prepare_hamburg_entry_alternative(case_dir=case, profiles_file=profile_file, output_dir=out)


def test_frozen_entry_replacement_has_no_native_count_gain_and_keeps_failure_visible():
    root = Path(__file__).parents[1] / "benchmarks"
    baseline = root / "hamburg_ring1_initialization_population_20261021_v1"
    candidate = root / "hamburg_ring1_ballindamm_entry_probe_20261021_v1"
    before, after = [read_csv_rows(p / "native/field-comparison.csv") for p in (baseline, candidate)]
    def values(rows):
        return {(r["detector_id"], float(r["begin"]), float(r["end"])):
                (int(r["expected_total"]), int(r["measured_nVehContrib"])) for r in rows}
    assert len(before) == len(after) == 52 and values(before) == values(after)
    native = ET.parse(candidate / "native/field-counts.xml").getroot().findall("interval")
    assert sum(int(r.get("nVehContrib")) for r in native) == 1431
    report = json.loads((candidate / "native/initialization-check.json").read_bytes())
    assert report["completion"]["waiting"] == 412 and report["completion"]["running"] == 233
    assert report["completion"]["arrived"] == 1308 and report["exact_initialization_bins"] == 32
    assert report["completion"]["collisions"] == report["completion"]["teleports"] == 0
    assert not report["main_window_run"] and not report["exact_calibration_established"]
