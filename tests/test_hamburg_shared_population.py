from collections import Counter
import json
from pathlib import Path
import xml.etree.ElementTree as ET

import pytest

from astra_sumo.core.hamburg_collision_trace import read_trace_source
from astra_sumo.core.hamburg_readme_results import vehicle_field_passage_rows
from astra_sumo.core.hamburg_shared_population import prepare_shared_population


ROOT = Path(__file__).parents[1]
CASE = ROOT / "benchmarks/hamburg_ring1_primary_entry_full_20261022_v1"
PROBE = ROOT / "benchmarks/hamburg_ring1_shared_field_probe_20261022_v1"


def test_shared_population_preserves_every_observation_and_retained_request(tmp_path):
    names = ("network.net.xml", "demand.rou.xml", "field-loops.add.xml", "expected-counts.csv",
             "control.add.xml", "lane-intentions.json", "scenario.json", "initialization-counts.csv")
    before = {n: read_trace_source(CASE / "inputs", n) for n in names}
    out = tmp_path / "shared"
    report = prepare_shared_population(repository=ROOT, case_dir=CASE, probe_dir=PROBE, output_dir=out)
    assert report["sources_unchanged"] and not report["formal_result"]
    assert report["shared_pairs"] == 455 and report["model_vehicles_after"] == 8185
    assert report["model_roles_after"] == {"initialization": 1848, "main": 6337}
    assert report["field_observation_totals"] == {"initialization": 1953, "main": 6687}
    old = {v.get("id"): v for v in ET.fromstring(before["demand.rou.xml"]).findall("vehicle")}
    new = {v.get("id"): v for v in ET.parse(out / "demand.rou.xml").getroot().findall("vehicle")}
    pairs = {r["parent_vehicle_id"]: r for r in report["pairings"]}
    removed = {r["removed_model_vehicle_id"] for r in report["pairings"]}
    assert len(pairs) == len(removed) == 455 and set(new) == set(old) - removed
    loops = {r.get("id"): r.get("lane").rsplit("_", 1)[0] for r in ET.fromstring(before["field-loops.add.xml"])}
    old_intent = {r["vehicle_id"]: r for r in json.loads(before["lane-intentions.json"])["vehicles"]}
    for identity, vehicle in new.items():
        previous = old[identity]
        assert vehicle.attrib == previous.attrib
        assert [r.attrib for r in vehicle.findall("stop")] == [r.attrib for r in previous.findall("stop")]
        a, b = (v.find("route").get("edges").split() for v in (previous, vehicle))
        if identity in pairs:
            road = loops[old_intent[identity]["field_id"]]
            assert a[:a.index(road) + 2] == b[:b.index(road) + 2]
            second = loops["hh_0535_Z_4_33055"]
            assert a[:a.index(second) + 1] == b[:b.index(second) + 1]
            assert pairs[identity]["normal_road_distance_after_m"] <= pairs[identity]["normal_road_distance_before_m"]
            assert b[-1] == old[pairs[identity]["removed_model_vehicle_id"]].find("route").get("edges").split()[-1]
        else:
            assert a == b
    intent = json.loads((out / "lane-intentions.json").read_bytes())["vehicles"]
    original_counts = Counter((r["field_id"], r["target_begin_s"], r["target_end_s"], r["population_role"]) for r in old_intent.values())
    shared_counts = Counter((r["field_id"], r["target_begin_s"], r["target_end_s"], r["population_role"])
                            for v in intent for r in v["target_observations"])
    assert original_counts == shared_counts and sum(shared_counts.values()) == 8640
    assert all(len(v["expected_field_ids"]) == len(set(v["expected_field_ids"])) for v in intent)
    assert sum(len(v["target_observations"]) == 2 for v in intent) == 455
    for name in ("network.net.xml", "field-loops.add.xml", "expected-counts.csv", "control.add.xml", "initialization-counts.csv"):
        assert (out / name).read_bytes() == before[name]
    assert all(read_trace_source(CASE / "inputs", name) == data for name, data in before.items())
    with pytest.raises(ValueError, match="new shared-population"):
        prepare_shared_population(repository=ROOT, case_dir=CASE, probe_dir=PROBE, output_dir=out)


def test_shared_population_requires_the_verified_native_signature(tmp_path):
    probe = tmp_path / "unverified"
    probe.mkdir()
    checked = json.loads((PROBE / "joint-response-check.json").read_bytes())
    checked["vehicle_signatures_matched"] = False
    (probe / "joint-response-check.json").write_text(json.dumps(checked), encoding="utf-8")
    with pytest.raises(ValueError, match="verified distinct-field"):
        prepare_shared_population(repository=ROOT, case_dir=CASE, probe_dir=probe, output_dir=tmp_path / "bad")
    assert not (tmp_path / "bad").exists()


def test_shared_passage_diagnostics_separate_vehicle_role_from_observation_role():
    intent = [{"vehicle_id": "v", "field_id": "first", "population_role": "initialization", "target_begin_s": 2700,
        "target_observations": [
            {"field_id": "first", "population_role": "initialization", "target_begin_s": 2700},
            {"field_id": "second", "population_role": "main", "target_begin_s": 3600}]}]
    passage = {"status": "measured", "passages": [
        {"vehicle_id": "v", "detector_id": "first", "time_s": 3500, "interval_begin_s": 2700},
        {"vehicle_id": "v", "detector_id": "second", "time_s": 3800, "interval_begin_s": 3600}]}
    rows = vehicle_field_passage_rows(intent, passage)
    assert len(rows) == 2 and [r["population_role"] for r in rows] == ["initialization", "main"]
    assert all(r["vehicle_population_role"] == "initialization" and r["actual_native_field_passages"] == 1
               and r["all_native_field_passages"] == 2 for r in rows)
    assert [r["planned_bin"] for r in rows] == [-1, 0] and [r["actual_bin"] for r in rows] == [-1, 0]
    unavailable = vehicle_field_passage_rows(intent, {"status": "unavailable"})
    assert len(unavailable) == 2 and all(r["actual_native_field_passages"] is None for r in unavailable)


@pytest.mark.parametrize("targets", [[{"field_id": "f"}, {"field_id": "f"}], [{"field_id": "other"}], [{"field_id": "f"}, {}]])
def test_shared_diagnostics_reject_repeated_or_unmatched_declarations(targets):
    with pytest.raises(ValueError, match="distinct fields"):
        vehicle_field_passage_rows([{"vehicle_id": "v", "field_id": "f", "target_observations": targets}], {"status": "unavailable"})


def test_shared_prefix_keeps_unique_vehicle_counts_separate_from_field_visits():
    from astra_sumo.core.detector_demand import read_csv_rows

    case = ROOT / "benchmarks/hamburg_ring1_shared_population_prefix_20261022_v1"
    report = json.loads(read_trace_source(case / "analysis", "prefix-check.json"))
    assert not report["formal_result"] and not report["main_window_completed"]
    assert report["candidate_model_requests_due"] == 1848 and report["initialization_field_target_contributions"] == 1953
    assert report["candidate_E1_total"] == 1735 and report["baseline_E1_total"] == 1716
    assert report["observed_vehicles_with_both_fields"] == 105 and report["prewindow_contributions_targeting_main"] == 0
    assert report["candidate_completion"]["collisions"] == report["candidate_completion"]["teleports"] == 0
    assert not any(report[key] for key in ("unexpected_fields", "repeated_same_field", "second_without_first", "wrong_field_order"))
    assert report["candidate_exact_rows"] == 32 and report["baseline_exact_rows"] == 34
    assert report["candidate_max_abs"] == 37 and report["baseline_max_abs"] == 50
    fields = read_csv_rows(case / "analysis/all-130-main-rows.csv")
    stations = read_csv_rows(case / "analysis/all-40-main-stations.csv")
    assert len(fields) == 130 and all(row["measured_nVehContrib"] == "" for row in fields)
    assert len(stations) == 40 and all(row["measured_nVehContrib"] == "" for row in stations)


def test_shared_full_run_keeps_native_visits_and_does_not_promote_worse_timing():
    from astra_sumo.core.detector_demand import read_csv_rows

    case = ROOT / "benchmarks/hamburg_ring1_shared_population_full_20261022_v1"
    result = json.loads(read_trace_source(case, "results.json"))
    assert result["field_rows"] == 130 and result["station_rows"] == 40
    assert result["missing_field_rows"] == result["missing_station_rows"] == 0
    assert result["exact_field_rows"] == 32 and result["exact_station_rows"] == 8
    assert result["diagnostic_field_MAE"] == pytest.approx(1863 / 130) and result["diagnostic_max_abs_error"] == 71
    assert result["completion"]["waiting"] == 882 and result["completion"]["running"] == 292
    assert result["completion"]["collisions"] == result["completion"]["teleports"] == 0
    assert not result["valid_run"] and not result["exact_count_fit"] and result["formal_field_metrics"] is None
    native = ET.fromstring(read_trace_source(case / "native", "field-counts.xml"))
    counts = {(r.get("id"), float(r.get("begin")), float(r.get("end"))): int(r.get("nVehContrib")) for r in native}
    rows = read_csv_rows(case / "field-comparison.csv")
    assert len(rows) == 130 and sum(int(r["measured_nVehContrib"]) for r in rows) == 4896
    assert all(counts[r["detector_id"], float(r["begin"]), float(r["end"])] == int(r["measured_nVehContrib"]) for r in rows)
    signature = json.loads(read_trace_source(case / "analysis", "native-signature-check.json"))
    assert signature["shared_vehicles_observed_at_both_fields"] == 455
    assert not any(signature[k] for k in ("repeated_same_field", "unexpected_fields", "arrived_missing_declared_fields", "wrong_field_order", "second_without_first"))
    decomposition = json.loads(read_trace_source(case / "analysis", "target-visit-decomposition.json"))
    assert decomposition["all_130_native_residual_identities_verified"] and decomposition["main_target_visits"] == 6687
    timing = json.loads(read_trace_source(case / "analysis", "pair-timing-check.json"))
    assert timing["frozen_uniform_shift_incompatible_pairs"] == 333 and not timing["formal_result"]
    assert timing["pairs_first_within_own_target"] == 77 and timing["pairs_second_within_own_target"] == 454
