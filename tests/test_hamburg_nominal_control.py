import json
from pathlib import Path
import xml.etree.ElementTree as ET

from astra_sumo.core.hamburg_collision_trace import read_trace_source
from astra_sumo.core.hamburg_readme_control import prepare_nominal_osm_control


ROOT = Path(__file__).parents[1]
BASE = ROOT / "benchmarks/hamburg_ring1_primary_entry_full_20261022_v1"


def test_nominal_control_changes_only_58_osm_program_types_and_actuation_limits(tmp_path):
    out = tmp_path / "nominal"
    result = prepare_nominal_osm_control(case_dir=BASE, output_dir=out)
    assert len(result["changed_programs"]) == 58 and len(result["original_fixed_programs_retained"]) == 10
    assert not result["formal_result"] and not result["native_verified"]
    old = {r.get("id"): r for r in ET.fromstring(read_trace_source(BASE / "inputs", "control.add.xml"))}
    new = {r.get("id"): r for r in ET.parse(out / "control.add.xml").getroot()}
    assert old.keys() == new.keys()
    for identity, before in old.items():
        after = new[identity]
        if before.get("type") == "static":
            assert ET.tostring(before) == ET.tostring(after)
        else:
            assert after.get("type") == "static"
            assert {k: v for k, v in before.attrib.items() if k != "type"} == {k: v for k, v in after.attrib.items() if k != "type"}
            assert [{k: v for k, v in p.attrib.items() if k not in {"minDur", "maxDur", "next"}} for p in before.findall("phase")] == [dict(p.attrib) for p in after.findall("phase")]
    for name in ("network.net.xml", "demand.rou.xml", "field-loops.add.xml", "lane-intentions.json", "initialization-counts.csv", "expected-counts.csv"):
        assert (out / name).read_bytes() == read_trace_source(BASE / "inputs", name)
    assert json.loads((out / "scenario.json").read_bytes())["control_variant"] == "A_nominal_OSM"


def test_green_diagnosis_reproduces_events_and_keeps_main_measurements_unknown():
    from astra_sumo.core.detector_demand import read_csv_rows

    case = ROOT / "benchmarks/hamburg_ring1_119_control_diagnosis_20261022_v1"
    report = json.loads((case / "diagnostic-analysis/report.json").read_bytes())
    assert report["reference_prefix_52_attributes_match"] and report["new_full_window_runs"] == 0
    assert report["field_green_service"]["hh_0119_Z_4_29437"]["queued_but_no_front_exit_cycles"] == 29
    assert report["field_green_service"]["hh_0119_Z_5_30494"]["queued_but_no_front_exit_cycles"] == 26
    events = json.loads((case / "diagnostic-analysis/event-correspondence.json").read_bytes())
    assert events["reference_complete_passages"] == events["diagnostic_complete_passages"] == 1778
    assert events["all_passage_attributes_match"] and events["native_E1_reconciled"]
    sample = json.loads(read_trace_source(case / "diagnostic-native/green-observations", "state-3682.json"))
    assert sample["native_time_before_queries_s"] == sample["native_time_after_queries_s"] == 3682
    assert sample["traffic_lights"]["LSA119_part0"]["state"][12] == 'G'
    assert sample["traffic_lights"]["GS_266199063"]["state"][3] == 'r'
    assert sample["lanes"]["24483344#1_0"]["head_next_stop"] is None
    assert len(read_csv_rows(case / "diagnostic-analysis/reference-field-comparison.csv")) == 130
    assert len(read_csv_rows(case / "diagnostic-analysis/reference-station-comparison.csv")) == 40


def test_nominal_control_prefix_retains_other_field_losses_and_no_main_fit_claim():
    from astra_sumo.core.detector_demand import read_csv_rows

    case = ROOT / "benchmarks/hamburg_ring1_119_control_diagnosis_20261022_v1"
    installed = json.loads((case / "nominal-native/installed-programs.json").read_bytes())
    assert len(installed["rows"]) == 68 and installed["all_active_programs_static"]
    assert installed["time_before_s"] == installed["time_after_s"] == 0
    report = json.loads((case / "nominal-analysis/prefix-check.json").read_bytes())
    assert not report["formal_result"] and not report["main_window_completed"]
    assert report["candidate_E1_total"] == 1407 and report["candidate_diagnostic_MAE"] > report["baseline_diagnostic_MAE"]
    assert report["candidate_completion"]["waiting"] == 473
    assert report["candidate_completion"]["collisions"] == report["candidate_completion"]["teleports"] == 0
    fields = read_csv_rows(case / "nominal-analysis/all-130-main-rows.csv")
    stations = read_csv_rows(case / "nominal-analysis/all-40-main-stations.csv")
    assert len(fields) == 130 and all(r['measured_nVehContrib'] == '' for r in fields)
    assert len(stations) == 40 and all(r['measured_nVehContrib'] == '' for r in stations)
