from copy import deepcopy
import gzip
import json
from pathlib import Path
import xml.etree.ElementTree as ET

import pytest

from astra_sumo.core.hamburg_z15_routes import FIELD, prepare_z15_k2_routes
from astra_sumo.core.hamburg_readme_results import last_complete_summary_snapshot


def test_z15_candidate_changes_only_routes_and_preserves_all_targets(tmp_path):
    root = Path(__file__).parents[1]
    case = root / "benchmarks/hamburg_ring1_initialization_population_20261021_v1"
    profiles = root / "benchmarks/hamburg_ring1_m1_2_demand_pool_20261021_v1/base/ideal-lane-profiles.json.gz"
    bindings = root / "benchmarks/hamburg_ring1_m2_control_20261021_v1/link-bindings.json.gz"
    out = tmp_path / "candidate"
    report = prepare_z15_k2_routes(case_dir=case, profiles_file=profiles, bindings_file=bindings, output_dir=out)
    assert report["sources_unchanged"] and report["changed_by_population"] == {"main": 896, "initialization": 309}
    for name in ("network.net.xml", "control.add.xml", "field-loops.add.xml", "expected-counts.csv", "initialization-counts.csv"):
        assert (out / name).read_bytes() == (case / "inputs" / name).read_bytes()
    old = {v.get("id"): v for v in ET.fromstring(gzip.decompress((case / "inputs/demand.rou.xml.gz").read_bytes())).findall("vehicle")}
    new = {v.get("id"): v for v in ET.parse(out / "demand.rou.xml").getroot().findall("vehicle")}
    meta = {r["vehicle_id"]: r for r in json.loads((out / "lane-intentions.json").read_bytes())["vehicles"]}
    original_meta = json.loads(gzip.decompress((case / "inputs/lane-intentions.json.gz").read_bytes()))["vehicles"]
    assert old.keys() == new.keys() == meta.keys() and len(new) == 8640
    changed = 0
    for identity, a in old.items():
        b = deepcopy(new[identity])
        assert a.attrib == b.attrib
        if meta[identity]["field_id"] == FIELD:
            assert meta[identity]["profile_index"] in {89, 252}
            b.find("route").set("edges", a.find("route").get("edges"))
            changed += 1
        assert ET.tostring(a) == ET.tostring(b)
    assert changed == 1205
    for a in original_meta:
        b = meta[a["vehicle_id"]]
        assert {k: v for k, v in a.items() if k not in {"profile_index", "free_flow_field_offset_s"}} == {k: v for k, v in b.items() if k not in {"profile_index", "free_flow_field_offset_s"}}
    allocations = json.loads((out / "z15-k2-proposal.json").read_bytes())["allocations"]
    assert len(allocations) == 14
    for row in allocations:
        n = row["profile_89_vehicles"] + row["profile_252_vehicles"]
        assert row["profile_89_vehicles"] == (2 * n + 1) // 3
    with pytest.raises(ValueError, match="new route-candidate"):
        prepare_z15_k2_routes(case_dir=case, profiles_file=profiles, bindings_file=bindings, output_dir=out)


def test_frozen_z15_probes_and_real_initialization_counts_remain_separate():
    root = Path(__file__).parents[1] / "benchmarks/hamburg_ring1_z15_k2_routes_20261021_v1"
    probe = json.loads((root / "small-probe/probe-check.json").read_bytes())
    run = json.loads((root / "native/initialization-check.json").read_bytes())
    checks = json.loads((root / "analysis/independent-checks.json").read_bytes())
    assert probe["passed"] and probe["completion"]["arrived"] == 20
    assert run["completion"]["arrived"] == 1347 and run["completion"]["waiting"] == 364
    assert run["completion"]["collisions"] == run["completion"]["teleports"] == 0
    assert run["available_initialization_bins"] == 52 and run["exact_initialization_bins"] == 32
    assert not run["main_window_run"] and not run["exact_calibration_established"]
    assert checks["wrong_field_passages"] == checks["duplicate_field_passages"] == 0
    native = ET.parse(root / "native/field-counts.xml").getroot().findall("interval")
    assert sum(int(r.get("nVehContrib")) for r in native if r.get("id") == FIELD) == 273
    assert sum(int(r.get("nVehContrib")) for r in native) == 1471


def test_incomplete_summary_retains_a_snapshot_without_establishing_completion(tmp_path):
    p = tmp_path / "summary.xml"
    p.write_text('<summary><step time="1" inserted="2"/><step time="2" inserted="3"/><step time="3"', encoding="utf-8")
    result = last_complete_summary_snapshot(p)
    assert result["step"] == {"time": "2", "inserted": "3"}
    assert result["parse_error"] and not result["native_completion_established"]


def test_frozen_full_observation_rows_do_not_hide_the_tail_timeout():
    root = Path(__file__).parents[1] / "benchmarks/hamburg_ring1_z15_k2_full_20261021_v1"
    result = json.loads((root / "results.json").read_bytes())
    checks = json.loads((root / "independent-checks.json").read_bytes())
    assert result["execution_status"] == "timeout" and not result["valid_run"]
    assert result["formal_field_metrics"] is None and not result["exact_count_fit"]
    assert result["missing_field_rows"] == result["missing_station_rows"] == 0
    assert result["field_rows"] == 130 and result["station_rows"] == 40
    assert result["exact_field_rows"] == 32 and result["exact_station_rows"] == 7
    assert result["completion"] is None
    snapshot = result["last_complete_summary_snapshot"]
    assert float(snapshot["step"]["time"]) == 13645
    assert snapshot["parse_error"] and not snapshot["native_completion_established"]
    assert checks["all_130_native_E1_counts_rechecked"] and checks["all_40_station_sums_rechecked"]
    text = (root / "README.md").read_text(encoding="utf-8")
    assert "均非计划结束时的最终数量" in text and "停止检查每 0.1 秒" in text
    assert "完整整数等式在仿真前成立" not in text
