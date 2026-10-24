from collections import Counter
import json
from pathlib import Path
import xml.etree.ElementTree as ET

import pytest

from astra_sumo.core.hamburg_collision_trace import read_trace_source
from astra_sumo.core.hamburg_entry_prior import FIELD, MERGE, PRIMARY, SHORT, prepare_primary_entry_prior, prepare_western_entry_prior


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


def test_complete_entry_measurement_retains_native_counts_and_worse_station_peak():
    from astra_sumo.core.detector_demand import read_csv_rows

    case = ROOT / "benchmarks/hamburg_ring1_primary_entry_full_20261022_v1"
    result = json.loads(read_trace_source(case, "results.json"))
    assert not result["valid_run"] and not result["exact_count_fit"] and result["formal_field_metrics"] is None
    assert result["field_rows"] == 130 and result["station_rows"] == 40
    assert result["missing_field_rows"] == result["missing_station_rows"] == 0
    assert result["exact_field_rows"] == 31 and result["exact_station_rows"] == 7
    assert result["diagnostic_field_MAE"] == pytest.approx(1685 / 130) and result["diagnostic_max_abs_error"] == 59
    assert result["diagnostic_station_max_abs_error"] == 150
    assert result["completion"]["collisions"] == result["completion"]["teleports"] == 0
    assert result["completion"]["waiting"] == 644 and result["completion"]["running"] == 306
    native = ET.fromstring(read_trace_source(case / "native", "field-counts.xml"))
    counts = {(row.get("id"), float(row.get("begin")), float(row.get("end"))): int(row.get("nVehContrib")) for row in native}
    rows = read_csv_rows(case / "field-comparison.csv")
    assert len(rows) == 130
    assert all(counts[row["detector_id"], float(row["begin"]), float(row["end"])] == int(row["measured_nVehContrib"]) for row in rows)
    prefix = json.loads(read_trace_source(case / "analysis", "prefix-reproduction-check.json"))
    assert prefix["all_initialization_intervals_match"] and prefix["compared_intervals"] == 52
    witnesses = json.loads(read_trace_source(case / "analysis", "original-participant-check.json"))
    assert witnesses["all_six_rear_cleared"] and witnesses["all_six_arrived"]


def test_western_prior_changes_only_supported_entry_prefixes_and_keeps_local_requests(tmp_path):
    source = ROOT / "benchmarks/hamburg_ring1_primary_entry_full_20261022_v1"
    probe = ROOT / "benchmarks/hamburg_ring1_western_entry_prefix_20261022_v1/route-probe"
    names = ("network.net.xml", "demand.rou.xml", "field-loops.add.xml", "expected-counts.csv",
             "control.add.xml", "lane-intentions.json", "scenario.json", "initialization-counts.csv")
    before = {name: read_trace_source(source / "inputs", name) for name in names}
    out = tmp_path / "western"
    result = prepare_western_entry_prior(repository=ROOT, case_dir=source, probe_dir=probe, output_dir=out)
    assert not result["formal_result"] and result["sources_unchanged"]
    assert result["changed_by_population"] == {"initialization": 95, "main": 427}
    assert result["entry"] == "963776077#0" and result["passenger_lanes"] == 3
    assert result["minimum_passenger_lane_length_m"] == pytest.approx(323.23)
    assert result["boundary"]["basis"] == ["source_model_boundary"]
    assert result["structure"]["structural_conditions_satisfied"]
    assert json.loads((out / "scenario.json").read_bytes())["profiles_used"] == 29
    old = {v.get("id"): v for v in ET.fromstring(before["demand.rou.xml"]).findall("vehicle")}
    new = {v.get("id"): v for v in ET.parse(out / "demand.rou.xml").getroot().findall("vehicle")}
    assert old.keys() == new.keys() and len(new) == 8640
    changed = {r["vehicle_id"] for r in result["changed_vehicles"]}
    for identity, vehicle in new.items():
        previous = old[identity]
        assert vehicle.attrib == previous.attrib
        assert [ET.tostring(r) for r in vehicle.findall("stop")] == [ET.tostring(r) for r in previous.findall("stop")]
        a, b = (v.find("route").get("edges").split() for v in (previous, vehicle))
        if identity in changed:
            assert a[0] in {"-983227468#1", "448511533#3"} and b[0] == "963776077#0"
            assert a[a.index("234423652#1"):] == b[b.index("234423652#1"):]
        else:
            assert a == b
    old_intent = json.loads(before["lane-intentions.json"])["vehicles"]
    new_intent = json.loads((out / "lane-intentions.json").read_bytes())["vehicles"]
    keys = ("vehicle_id", "field_id", "population_role", "target_begin_s", "target_end_s", "depart_s")
    assert [tuple(r[k] for k in keys) for r in old_intent] == [tuple(r[k] for k in keys) for r in new_intent]
    assert all((out / name).read_bytes() == before[name] for name in names if name not in {"demand.rou.xml", "lane-intentions.json", "scenario.json"})
    assert all(read_trace_source(source / "inputs", name) == raw for name, raw in before.items())
    with pytest.raises(ValueError, match="new western-entry"):
        prepare_western_entry_prior(repository=ROOT, case_dir=source, probe_dir=probe, output_dir=out)


def test_western_prior_requires_native_route_proof_before_writing(tmp_path):
    probe = tmp_path / "probe"
    probe.mkdir()
    reference = ROOT / "benchmarks/hamburg_ring1_western_entry_prefix_20261022_v1/route-probe/route-response-check.json"
    report = json.loads(reference.read_bytes())
    report["vehicle_signatures_matched"] = False
    (probe / reference.name).write_text(json.dumps(report), encoding="utf-8")
    out = tmp_path / "candidate"
    with pytest.raises(ValueError, match="verified western-entry"):
        prepare_western_entry_prior(repository=ROOT, case_dir=CASE, probe_dir=probe, output_dir=out)
    assert not out.exists()


def test_western_prefix_reports_losses_without_claiming_main_window_effects():
    from astra_sumo.core.detector_demand import read_csv_rows

    case = ROOT / "benchmarks/hamburg_ring1_western_entry_prefix_20261022_v1"
    sparse = json.loads(read_trace_source(case / "route-probe", "route-response-check.json"))
    assert sparse["healthy"] and sparse["vehicle_signatures_matched"] and sparse["native_E1_reconciled"]
    assert sparse["completion"]["arrived"] == 40 and sparse["native_contributions"] == 40
    assert sparse["sum_native_stop_time_s"] == 0
    report = json.loads(read_trace_source(case / "analysis", "prefix-check.json"))
    assert not report["formal_result"] and not report["main_window_completed"]
    assert report["requests_due"] == 1953 and report["future_requests_retained"] == 6687
    assert report["changed_initialization_requests"] == 95
    assert report["candidate_completion"]["waiting"] == 118 and report["candidate_completion"]["running"] == 210
    assert report["candidate_completion"]["collisions"] == report["candidate_completion"]["teleports"] == 0
    assert report["candidate_E1_total"] == 1670 and report["baseline_E1_total"] == 1716
    assert report["candidate_diagnostic_MAE"] == pytest.approx(343 / 52)
    assert report["candidate_exact_initialization_rows"] == 27 and report["candidate_max_abs"] == 59
    z15 = next(r for r in report["field_totals"] if r["detector_id"] == "hh_0119_Z_15_29375")
    assert z15["change"] == -31
    fields = read_csv_rows(case / "analysis/all-130-main-rows.csv")
    stations = read_csv_rows(case / "analysis/all-40-main-stations.csv")
    assert len(fields) == 130 and all(r["measured_nVehContrib"] == "" for r in fields)
    assert len(stations) == 40 and all(r["measured_nVehContrib"] == "" for r in stations)


def test_western_full_run_keeps_every_native_residual_and_rejects_the_share():
    from astra_sumo.core.detector_demand import read_csv_rows

    case = ROOT / "benchmarks/hamburg_ring1_western_entry_full_20261022_v1"
    result = json.loads((case / "results.json").read_bytes())
    assert result["field_rows"] == 130 and result["station_rows"] == 40
    assert result["missing_field_rows"] == result["missing_station_rows"] == 0
    assert not result["valid_run"] and not result["exact_count_fit"] and result["formal_field_metrics"] is None
    assert result["diagnostic_field_MAE"] == pytest.approx(1868 / 130) and result["diagnostic_max_abs_error"] == 62
    assert result["diagnostic_station_MAE"] == 45.5 and result["diagnostic_station_max_abs_error"] == 152
    assert result["exact_field_rows"] == 31 and result["exact_station_rows"] == 7
    assert result["completion"]["waiting"] == 934 and result["completion"]["running"] == 410
    assert result["completion"]["collisions"] == result["completion"]["teleports"] == 0
    fields = read_csv_rows(case / "field-comparison.csv")
    native = ET.fromstring(read_trace_source(case / "native", "field-counts.xml"))
    counts = {(r.get("id"), float(r.get("begin")), float(r.get("end"))): int(r.get("nVehContrib")) for r in native}
    assert len(fields) == 130 and sum(int(r["measured_nVehContrib"]) for r in fields) == 4887
    assert all(counts[r["detector_id"], float(r["begin"]), float(r["end"])] == int(r["measured_nVehContrib"]) for r in fields)
    decomposition = json.loads((case / "data-analysis/decomposition.json").read_bytes())
    assert decomposition["all_130_residual_identities_verified"] and not decomposition["arrivals_without_complete_assigned_passage"]
    assert decomposition["totals"]["not_entered_by_native_end"] == 934
    prefix = json.loads((case / "analysis/prefix-reproduction-check.json").read_bytes())
    assert prefix["compared_intervals"] == 52 and prefix["all_initialization_intervals_match"]
    participants = json.loads((case / "analysis/original-participant-route-correspondence.json").read_bytes())
    assert participants["applicable_original_sites"] == 5 and participants["bypassed_original_sites"] == 1
    assert sum(r["native_arrival_s"] >= 0 for r in participants["vehicles"]) == 5
    assert json.loads((case / "decision.json").read_bytes())["decision"] == "not_promoted"
