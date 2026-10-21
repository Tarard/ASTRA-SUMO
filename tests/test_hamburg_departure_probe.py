import xml.etree.ElementTree as ET
import gzip
import json
from pathlib import Path
import runpy
import sys

import pytest

from astra_sumo.core.hamburg_departure_probe import shift_entry_requests, single_pass_structure


def test_entry_shift_preserves_vehicles_routes_lane_targets_and_other_departures():
    source = ET.fromstring('<routes><vType id="car"/><vehicle id="a" depart="1000"><route edges="entry exit"/><stop lane="exit_1" endPos="10" speed="5"/></vehicle><vehicle id="b" depart="900"><route edges="other exit"/></vehicle></routes>')
    intentions = {"vehicles": [{"vehicle_id": "a", "field_id": "f", "field_row": 8},
                               {"vehicle_id": "b", "field_id": "g", "field_row": 9}]}
    before = ET.tostring(source)
    root, updated, count = shift_entry_requests(source, intentions, "entry", 900)
    assert count == 1 and ET.tostring(source) == before
    assert [v.get("id") for v in root.findall("vehicle")] == ["a", "b"]
    assert root.find("vehicle[@id='a']").get("depart") == "100.000000000"
    assert root.find("vehicle[@id='b']").get("depart") == "900.000000000"
    assert ET.tostring(root.find("vehicle[@id='a']/route")) == ET.tostring(source.find("vehicle[@id='a']/route"))
    assert ET.tostring(root.find("vehicle[@id='a']/stop")) == ET.tostring(source.find("vehicle[@id='a']/stop"))
    assert [r["field_row"] for r in updated["vehicles"]] == [8, 9]
    assert updated["vehicles"][0]["source_depart_s"] == 1000
    assert "source_depart_s" not in intentions["vehicles"][0]
    with pytest.raises(ValueError, match="earlier declared"):
        shift_entry_requests(source, intentions, "entry", 1200)


def test_single_pass_bound_needs_unique_lanes_near_the_end_and_no_route_return():
    network = ET.fromstring('<net><edge id="in"><lane id="in_0" length="30"/><lane id="in_1" length="30"/></edge></net>')
    routes = ET.fromstring('<routes><vType id="car" length="5"/><vehicle id="v" type="car"><route edges="in out"/></vehicle></routes>')
    loops = ET.fromstring('<additional><inductionLoop id="f" lane="in_0" pos="29.9"/><inductionLoop id="g" lane="in_1" pos="29.9"/></additional>')
    assert single_pass_structure(network, routes, loops)["structural_conditions_satisfied"]
    loops[1].set("lane", "in_0")
    assert not single_pass_structure(network, routes, loops)["structural_conditions_satisfied"]
    loops[1].set("lane", "in_1")
    routes.find("vehicle/route").set("edges", "in out in")
    assert not single_pass_structure(network, routes, loops)["structural_conditions_satisfied"]


def test_frozen_early_native_passages_rule_out_only_the_fixed_population_advance():
    case = Path(__file__).parents[1] / "benchmarks/hamburg_ring1_departure_advance_20261021_v1"
    report = json.loads((case / "native/warmup-check/warmup-check.json").read_bytes())
    counts = ET.parse(case / "native/warmup-check/field-counts.xml").getroot().findall("interval")
    assert len(counts) == 52 and {float(r.get("end")) for r in counts} == {900, 1800, 2700, 3600}
    assert sum(int(r.get("nVehContrib")) for r in counts) == report["early_native_complete_passages"] == 206
    assert report["early_unique_vehicles"] == 206 and report["native_E1_reconciled"]
    assert report["full_population"] - report["early_unique_vehicles"] == report["real_window_total_upper_bound"] == 6481
    assert report["candidate_rejected_under_fixed_population"]
    assert report["pre_window_real_observations"] is None


def test_initialization_fetch_replays_frozen_official_data_without_changing_targets(tmp_path, monkeypatch):
    from astra_sumo.core import hamburg_official

    root = Path(__file__).parents[1]
    source = root / "benchmarks/hamburg_ring1_initialization_counts_20261021_v1"
    raw = json.loads(gzip.decompress((source / "monday-observations.json.gz").read_bytes()))
    observations = {int(stream): hamburg_official.parse_hamburg_count_observations(int(stream),
        [row for page in pages for row in page["value"]]) for stream, pages in raw["pages_by_stream"].items()}
    monkeypatch.setattr(hamburg_official, "fetch_hamburg_count_observations", lambda *a, **k: (observations, raw))
    script = root / "plugins/astra-sumo/scripts/fetch_hamburg_readme_initialization.py"
    out = tmp_path / "source-replay"
    monkeypatch.setattr(sys, "argv", [str(script), str(root / "benchmarks/hamburg_ring1_count_window_20261020_v1"), str(out)])
    runpy.run_path(str(script), run_name="__main__")
    report = json.loads((out / "report.json").read_bytes())
    assert report["initialization_field_bins"] == 52 and report["initialization_count_sum"] == 1953
    assert report["comparison_rows_checked"] == 130 and report["comparison_value_changes"] == []
    assert report["original_target_files_modified"] is False
    assert (out / "initialization-counts-15min.csv").read_bytes() == (source / "initialization-counts-15min.csv").read_bytes()
