from collections import Counter
from copy import deepcopy
import gzip
import json
from pathlib import Path
import xml.etree.ElementTree as ET

import pytest

from astra_sumo.core.hamburg_through_routes import prepare_hamburg_through_prior


ROOT = Path(__file__).parents[1]
BASE = ROOT / "benchmarks/hamburg_ring1_primary_exit_prior_20261021_v1"
CASE = ROOT / "benchmarks/hamburg_ring1_through_route_coverage_20261021_v1"


def read(folder, name):
    path = folder / name
    if path.exists():
        return path.read_bytes()
    return gzip.decompress(path.with_name(path.name + ".gz").read_bytes())


def test_through_prior_preserves_requests_targets_and_previous_residential_share(tmp_path):
    out = tmp_path / "candidate"
    report = prepare_hamburg_through_prior(repository=ROOT, case_dir=BASE, probe_dir=CASE / "paired-probe", output_dir=out)
    assert report["changed_by_population"] == {"initialization": 425, "main": 1126}
    assert report["split_is_exploratory_not_measured"]
    assert [r["passenger_lanes"] for r in report["outlet_facts"].values()] == [2, 3]
    assert report["structure"]["structural_conditions_satisfied"]
    assert not report["structure"]["one_observed_road_per_route"]
    for n in ("network.net.xml", "control.add.xml", "field-loops.add.xml", "expected-counts.csv", "initialization-counts.csv"):
        assert read(out, n) == read(BASE / "inputs", n)
    old = {v.get("id"): v for v in ET.fromstring(read(BASE / "inputs", "demand.rou.xml")).findall("vehicle")}
    new = {v.get("id"): v for v in ET.parse(out / "demand.rou.xml").getroot().findall("vehicle")}
    before = {r["vehicle_id"]: r for r in json.loads(read(BASE / "inputs", "lane-intentions.json"))["vehicles"]}
    after = {r["vehicle_id"]: r for r in json.loads(read(out, "lane-intentions.json"))["vehicles"]}
    assert old.keys() == new.keys() == after.keys() and len(new) == 8640
    changed = Counter()
    for identity, vehicle in old.items():
        candidate = deepcopy(new[identity])
        if candidate.find("route").get("edges") != vehicle.find("route").get("edges"):
            assert (before[identity]["profile_index"], after[identity]["profile_index"]) in {(100, 106), (176, 182), (177, 183)}
            changed[after[identity]["population_role"]] += 1
            candidate.find("route").set("edges", vehicle.find("route").get("edges"))
        assert ET.tostring(vehicle) == ET.tostring(candidate)
        assert {k: v for k, v in before[identity].items() if k != "profile_index"} == {k: v for k, v in after[identity].items() if k != "profile_index"}
    assert changed == report["changed_by_population"]
    assert read(out, "demand.rou.xml") == read(CASE / "inputs", "demand.rou.xml")


@pytest.mark.parametrize("fault", ["unsafe", "wrong_identity", "stale_source"])
def test_through_preparation_rejects_unverified_or_changed_probe(tmp_path, fault):
    probe = tmp_path / "probe"
    probe.mkdir()
    report = json.loads(read(CASE / "paired-probe", "probe-check.json"))
    if fault == "unsafe":
        report["results"]["through"]["traffic_complete"] = False
    elif fault == "wrong_identity":
        report["results"]["through"]["assigned_field_passed_once"] = False
    else:
        report["source_sha256"] = {k: "0" * 64 for k in report["source_sha256"]}
    (probe / "probe-check.json").write_text(json.dumps(report), encoding="utf-8")
    with pytest.raises(ValueError, match="verified single-field|source hashes"):
        prepare_hamburg_through_prior(repository=ROOT, case_dir=BASE, probe_dir=probe, output_dir=tmp_path / "candidate")


def test_frozen_safe_toy_does_not_validate_unsafe_initialization():
    probe = json.loads(read(CASE / "paired-probe", "probe-check.json"))
    for result in probe["results"].values():
        assert result["traffic_complete"] and result["declared_signature_matched"]
        assert result["completion"]["arrived"] == 40
        assert result["E1_reconciled"] and result["assigned_field_passed_once"]
        assert not result["other_complete_fields"] and result["repeated_same_field_passages"] == 0
    result = json.loads(read(CASE / "native", "initialization-check.json"))
    assert result["available_initialization_bins"] == 52 and result["exact_initialization_bins"] == 33
    assert result["completion"]["collisions"] == result["completion"]["teleports"] == 2
    assert result["completion"]["waiting"] == 158
    assert not result["native_safe_prefix"] and not result["exact_calibration_established"] and not result["main_window_run"]
    intervals = ET.fromstring(read(CASE / "native", "field-counts.xml")).findall("interval")
    assert len(intervals) == 52 and sum(int(r.get("nVehContrib")) for r in intervals) == 1665
    context = json.loads(read(CASE / "analysis", "collision-context.json"))
    assert not context["cause_verified"]
    assert {r["junction_id"] for r in context["events"]} == {"14398547", "205069138"}
    checked = json.loads(read(CASE / "analysis", "independent-checks.json"))
    assert not checked["larger_run_valid"] and not checked["conditional_bound_applicable_to_larger_run"]
