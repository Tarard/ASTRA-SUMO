"""Independently check the retained native measurements and early safety stop."""

import csv
import gzip
import hashlib
import json
from pathlib import Path
import xml.etree.ElementTree as ET

import pytest

from astra_sumo.core.detector_demand import audit_expected_to_e1_strict
from astra_sumo.core.hamburg_readme_results import STATIONS


CASE = Path(__file__).resolve().parents[1] / "benchmarks/hamburg_ring1_od_spsa_five_rounds_20261023_v1"


def test_five_round_extension_retains_early_stop_and_all_file_hashes():
    for row in json.loads((CASE / "manifest.json").read_bytes())["files"]:
        data = (CASE / row["path"]).read_bytes()
        assert len(data) == row["bytes"] and hashlib.sha256(data).hexdigest() == row["sha256"]
    result = json.loads((CASE / "result.json").read_bytes())
    assert result["stopping_reason"] == "unsafe_native_evaluation" and result["stop_on_unsafe"]
    assert result["completed_iterations"] == 0 and result["history"] == []
    assert result["native_evaluations"] == result["new_native_evaluations"] == 2
    assert not result["formal_result"] and not result["final_vector_remeasured"]
    assert json.loads((CASE / "settings.json").read_bytes())["iterations"] == 5
    assert {p.name for p in (CASE / "evaluations").iterdir()} == {"initial", "iteration-0001-plus"}


@pytest.mark.parametrize("label", ["initial", "iteration-0001-plus"])
def test_five_round_extension_counts_are_native_and_stations_are_derived(tmp_path, label):
    folder = CASE / "evaluations" / label
    expected = list(csv.DictReader(gzip.decompress((folder / "expected-counts.csv.gz").read_bytes()).decode().splitlines()))
    native = tmp_path / "field-counts.xml"
    native.write_bytes(gzip.decompress((folder / "field-counts.xml.gz").read_bytes()))
    measured = audit_expected_to_e1_strict(expected, native, count_attribute="nVehContrib")
    stored = list(csv.DictReader((folder / "field-comparison.csv").open(encoding="utf-8")))
    assert len(measured) == len(stored) == 130
    assert [r["measured_nVehContrib"] for r in measured] == [int(r["measured_nVehContrib"]) for r in stored]
    errors = [r["diff_nVehContrib_minus_expected"] for r in measured]
    evaluation = json.loads((folder / "evaluation.json").read_bytes())
    assert sum(map(abs, errors)) / 130 == evaluation["MAE"]
    assert sum(e * e for e in errors) == evaluation["loss"]
    assert max(map(abs, errors)) == evaluation["max_abs_error"]
    assert not evaluation["healthy"] and not evaluation["exact_count_fit"]
    stations = list(csv.DictReader((folder / "station-comparison.csv").open(encoding="utf-8")))
    assert len(stations) == 40
    for row in stations:
        node, assets = STATIONS[row["station_stream_id"]]
        indices = [i for i, e in enumerate(expected) if e["node_id"] == node and e["asset_id"] in assets
                   and float(e["begin"]) == float(row["begin"]) and float(e["end"]) == float(row["end"])]
        assert indices and row["independent_evidence"] == "False"
        assert sum(measured[i]["measured_nVehContrib"] for i in indices) == int(row["measured_nVehContrib"])
        assert sum(errors[i] for i in indices) == int(row["residual"])
    assert sum(abs(int(r["residual"])) for r in stations) / 40 == evaluation["station_MAE"]
    collision = ET.fromstring(gzip.decompress((folder / "collisions.xml.gz").read_bytes()))
    assert len(collision) == evaluation["completion"]["collisions"] == (label != "initial")
    if label != "initial":
        assert collision[0].get("lane") == ":LSA119_part0_10_1"
    request = json.loads(gzip.decompress((folder / "native-request.json.gz").read_bytes()))
    assert request["junction_body_hold_nodes"] == ["14398547", "205069138", "18653534", "LSA104_part0"]
