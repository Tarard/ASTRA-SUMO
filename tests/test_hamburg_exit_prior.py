from copy import deepcopy
import gzip
import json
from pathlib import Path
import xml.etree.ElementTree as ET

import pytest

from astra_sumo.core.hamburg_exit_prior import FIELDS, prepare_major_exit_prior


def test_exit_prior_keeps_requests_permissions_targets_and_unselected_routes(tmp_path):
    root = Path(__file__).parents[1]
    case = root / "benchmarks/hamburg_ring1_z15_k2_routes_20261021_v1"
    out = tmp_path / "candidate"
    report = prepare_major_exit_prior(repository=root, case_dir=case, output_dir=out)
    assert report["outlet_proxy_weights"] == {"19728446#0": 200, "142944538": 1800}
    assert report["sources_unchanged"] and report["selected_by_population"] == {"initialization": 946, "main": 2506}
    for n in ("network.net.xml", "control.add.xml", "field-loops.add.xml", "expected-counts.csv", "initialization-counts.csv"):
        assert (out / n).read_bytes() == (case / "inputs" / n).read_bytes()
    old = {v.get("id"): v for v in ET.fromstring(gzip.decompress((case / "inputs/demand.rou.xml.gz").read_bytes())).findall("vehicle")}
    new = {v.get("id"): v for v in ET.parse(out / "demand.rou.xml").getroot().findall("vehicle")}
    meta = {r["vehicle_id"]: r for r in json.loads((out / "lane-intentions.json").read_bytes())["vehicles"]}
    assert old.keys() == new.keys() == meta.keys() and len(new) == 8640
    changed = 0
    for identity, a in old.items():
        b = deepcopy(new[identity])
        assert a.attrib == b.attrib
        if a.find("route").get("edges") != b.find("route").get("edges"):
            assert meta[identity]["field_id"] in FIELDS
            assert a.find("route").get("edges").split()[:-1] == b.find("route").get("edges").split()[:len(a.find("route").get("edges").split()) - 1]
            assert b.find("route").get("edges").split()[-1] == "142944538"
            b.find("route").set("edges", a.find("route").get("edges"))
            changed += 1
        assert ET.tostring(a) == ET.tostring(b)
    assert changed == sum(report["changed_by_population"].values())
    allocations = json.loads((out / "destination-prior.json").read_bytes())["allocations"]
    assert len(allocations) == 14
    assert all(r["primary_destination"] == (9 * (r["primary_destination"] + r["residential_destination"]) + 5) // 10 for r in allocations)
    before = json.loads(gzip.decompress((case / "inputs/lane-intentions.json.gz").read_bytes()))["vehicles"]
    for a in before:
        assert {k: v for k, v in a.items() if k != "profile_index"} == {k: v for k, v in meta[a["vehicle_id"]].items() if k != "profile_index"}
    with pytest.raises(ValueError, match="new destination-prior"):
        prepare_major_exit_prior(repository=root, case_dir=case, output_dir=out)


def test_frozen_exit_prior_keeps_all_residuals_and_does_not_claim_main_calibration():
    root = Path(__file__).parents[1] / "benchmarks/hamburg_ring1_primary_exit_prior_20261021_v1"
    probe = json.loads((root / "paired-probe/probe-check.json").read_bytes())
    assert probe["both_route_checks_passed"] and probe["artificial_vehicles_per_case"] == 40
    assert probe["results"]["baseline"]["K1_last_front_exit_s"] == 542.6
    assert probe["results"]["candidate"]["K1_last_front_exit_s"] == 455.3
    run = json.loads((root / "native/initialization-check.json").read_bytes())
    assert run["available_initialization_bins"] == 52 and run["exact_initialization_bins"] == 33
    assert run["completion"]["waiting"] == 226 and run["completion"]["running"] == 217
    assert run["completion"]["collisions"] == run["completion"]["teleports"] == 0
    assert not run["main_window_run"] and not run["exact_calibration_established"]
    result = json.loads((root / "analysis/comparison.json").read_bytes())
    assert result["improved_bins"] == 12 and result["worsened_bins"] == 6 and result["unchanged_bins"] == 34
    fields = ET.parse(root / "native/field-counts.xml").getroot().findall("interval")
    assert len(fields) == 52 and sum(int(r.get("nVehContrib")) for r in fields) == 1548
