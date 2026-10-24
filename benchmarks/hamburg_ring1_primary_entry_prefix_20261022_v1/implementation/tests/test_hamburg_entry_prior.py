from collections import Counter
import json
from pathlib import Path
import xml.etree.ElementTree as ET

import pytest

from astra_sumo.core.hamburg_collision_trace import read_trace_source
from astra_sumo.core.hamburg_entry_prior import FIELD, MERGE, PRIMARY, SHORT, prepare_primary_entry_prior


ROOT = Path(__file__).parents[1]
CASE = ROOT / "benchmarks/hamburg_ring1_three_node_body_full_20261022_v1"


def test_entry_prior_only_changes_existing_prefixes_and_preserves_every_request(tmp_path):
    names = ("network.net.xml", "demand.rou.xml", "field-loops.add.xml", "expected-counts.csv",
             "control.add.xml", "lane-intentions.json", "scenario.json", "initialization-counts.csv")
    before = {n: read_trace_source(CASE / "inputs", n) for n in names}
    out = tmp_path / "candidate"
    report = prepare_primary_entry_prior(repository=ROOT, case_dir=CASE, output_dir=out)
    assert not report["formal_result"] and report["sources_unchanged"]
    assert report["changed_by_population"] == {"initialization": 73, "main": 221}
    assert report["structure"]["structural_conditions_satisfied"]
    old = {v.get("id"): v for v in ET.fromstring(before["demand.rou.xml"]).findall("vehicle")}
    new = {v.get("id"): v for v in ET.parse(out / "demand.rou.xml").getroot().findall("vehicle")}
    assert old.keys() == new.keys() and len(new) == 8640
    changed = {r["vehicle_id"] for r in report["changed_vehicles"]}
    for identity, vehicle in new.items():
        previous = old[identity]
        assert vehicle.attrib == previous.attrib
        assert [r.attrib for r in vehicle.findall("stop")] == [r.attrib for r in previous.findall("stop")]
        a, b = (r.find("route").get("edges").split() for r in (previous, vehicle))
        if identity in changed:
            assert a[0] == SHORT and b[0] == PRIMARY
            assert a[a.index(MERGE):] == b[b.index(MERGE):]
        else:
            assert a == b
    prior = json.loads(before["lane-intentions.json"])["vehicles"]
    after = json.loads((out / "lane-intentions.json").read_bytes())["vehicles"]
    def cohort(row):
        return row["field_id"], row["population_role"], row["target_begin_s"], row["target_end_s"]

    assert Counter(map(cohort, prior)) == Counter(map(cohort, after))
    assert all(r["field_id"] == FIELD for r in after if r["vehicle_id"] in changed)
    for name in ("network.net.xml", "field-loops.add.xml", "expected-counts.csv", "control.add.xml", "initialization-counts.csv"):
        assert (out / name).read_bytes() == before[name]
    assert all(read_trace_source(CASE / "inputs", n) == data for n, data in before.items())
    with pytest.raises(ValueError, match="new entry-prior"):
        prepare_primary_entry_prior(repository=ROOT, case_dir=CASE, output_dir=out)


def test_entry_prior_rejects_an_unmatched_source_route_without_writing_a_candidate(tmp_path):
    source = tmp_path / "source"
    inputs = source / "inputs"
    inputs.mkdir(parents=True)
    for name in ("network.net.xml", "demand.rou.xml", "field-loops.add.xml", "expected-counts.csv",
                 "control.add.xml", "lane-intentions.json", "scenario.json", "initialization-counts.csv"):
        (inputs / name).write_bytes(read_trace_source(CASE / "inputs", name))
    (source / "manifest.json").write_bytes((CASE / "manifest.json").read_bytes())
    demand = ET.parse(inputs / "demand.rou.xml")
    vehicle = next(v for v in demand.getroot().findall("vehicle") if v.find("route").get("edges").split()[0] == SHORT)
    vehicle.find("route").set("edges", "25866659#1 25866659#1")
    demand.write(inputs / "demand.rou.xml", encoding="utf-8")
    out = tmp_path / "bad"
    with pytest.raises(ValueError, match="current declared Z12"):
        prepare_primary_entry_prior(repository=ROOT, case_dir=source, output_dir=out)
    assert not out.exists()


def test_frozen_entry_prefix_retains_other_field_losses_and_every_future_target():
    from astra_sumo.core.detector_demand import read_csv_rows

    case = ROOT / "benchmarks/hamburg_ring1_primary_entry_prefix_20261022_v1"
    report = json.loads(read_trace_source(case / "analysis", "prefix-check.json"))
    assert not report["formal_result"] and not report["main_window_completed"]
    assert report["native_end_s"] == 3600 and report["requests_due"] == 1953 and report["future_requests_retained"] == 6687
    assert report["baseline_completion"]["waiting"] == 187 and report["candidate_completion"]["waiting"] == 120
    assert report["candidate_completion"]["collisions"] == report["candidate_completion"]["teleports"] == 0
    assert report["candidate_E1_total"] == 1716 and report["baseline_E1_total"] == 1638
    assert report["baseline_max_abs"] == 48 and report["candidate_max_abs"] == 50
    z4 = next(r for r in report["field_totals"] if r["detector_id"] == "hh_0119_Z_4_29437")
    assert z4["change"] == -3
    fields = read_csv_rows(case / "analysis/all-130-main-rows.csv")
    stations = read_csv_rows(case / "analysis/all-40-main-stations.csv")
    assert len(fields) == 130 and all(r["measured_nVehContrib"] == "" for r in fields)
    assert len(stations) == 40 and all(r["measured_nVehContrib"] == "" for r in stations)
