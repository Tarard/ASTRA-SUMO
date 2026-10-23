from collections import Counter
import json
import gzip
from pathlib import Path
import xml.etree.ElementTree as ET

import numpy as np
import pytest

from astra_sumo.core import hamburg_od_spsa as workflow
from astra_sumo.core.hamburg_od_definition import audit_hamburg_od_definition
from astra_sumo.core.hamburg_od_spsa import prepare_hamburg_od_baseline, write_hamburg_od_demand


def _mixed_explicit_prior(tmp_path):
    from astra_sumo.core.hamburg_primary_branch_repair import NEW_TAIL, OLD_TAIL

    frozen = Path(__file__).parents[1] / "benchmarks/hamburg_ring1_primary_branch_repair_20261023_v1/prepared"
    source = tmp_path / "explicit"
    source.mkdir()
    for path in frozen.glob("*.gz"):
        (source / path.stem).write_bytes(gzip.decompress(path.read_bytes()))
    intentions = json.loads((source / "lane-intentions.json").read_bytes())
    assigned = {row["vehicle_id"]: row["field_id"] for row in intentions["vehicles"]}
    tree = ET.parse(source / "demand.rou.xml")
    z15 = sorted((v for v in tree.getroot().findall("vehicle") if "Z_15_" in assigned[v.get("id")]),
                 key=lambda v: (float(v.get("depart")), v.get("id")))
    assert len(z15) == 1205
    for vehicle in z15[::2]:
        route = vehicle.find("route")
        edges = route.get("edges").split()
        assert tuple(edges[-len(NEW_TAIL):]) == NEW_TAIL
        route.set("edges", " ".join(edges[:-len(NEW_TAIL)] + list(OLD_TAIL)))
    tree.write(source / "demand.rou.xml", encoding="utf-8", xml_declaration=True)
    return source


def test_explicit_od_rebuild_preserves_initial_requests_and_mixed_tail_prior(tmp_path):
    source = _mixed_explicit_prior(tmp_path)
    prepared = tmp_path / "prepared"
    model = workflow.prepare_hamburg_od_from_explicit(prepared_dir=source, output_dir=prepared)
    assert len(model["cells"]) == 294
    assert sum(cell["initial_flow"] for cell in model["cells"]) == 8640
    assert model["exit_extensions"] == {}
    for path in source.iterdir():
        assert (prepared / path.name).read_bytes() == path.read_bytes()
    initial = prepared / "initial"
    assert (initial / "demand.rou.xml").read_bytes() == (source / "demand.rou.xml").read_bytes()
    assert (initial / "lane-intentions.json").read_bytes() == (source / "lane-intentions.json").read_bytes()
    vehicles = ET.parse(initial / "demand.rou.xml").getroot().findall("vehicle")
    assert sum("27064186#0" in v.find("route").get("edges").split() for v in vehicles) == 603
    report = json.loads((prepared / "initial-equivalence.json").read_bytes())
    assert report["status"] == "pass" and all(report["checks"].values())
    assert not report["new_native_run"] and not report["cache_reuse_authorized"]
    assert report["vehicle_count"] == 8640 and report["od_cells"] == 294
    assert report["quantile_resampling_changed"] is False


def test_explicit_od_rebuild_rejects_missing_intention_before_writing(tmp_path):
    source = _mixed_explicit_prior(tmp_path)
    path = source / "lane-intentions.json"
    data = json.loads(path.read_bytes())
    data["vehicles"].pop()
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError, match="intention"):
        workflow.prepare_hamburg_od_from_explicit(prepared_dir=source, output_dir=tmp_path / "rejected")
    assert not (tmp_path / "rejected").exists()


def test_explicit_od_rebuild_retains_failed_initial_identity_check(tmp_path):
    source = _mixed_explicit_prior(tmp_path)
    tree = ET.parse(source / "demand.rou.xml")
    vehicle = tree.getroot().find("vehicle")
    original_id = vehicle.get("id")
    vehicle.set("id", "noncanonical-source-id")
    tree.write(source / "demand.rou.xml", encoding="utf-8", xml_declaration=True)
    path = source / "lane-intentions.json"
    intentions = json.loads(path.read_bytes())
    next(r for r in intentions["vehicles"] if r["vehicle_id"] == original_id)["vehicle_id"] = "noncanonical-source-id"
    path.write_text(json.dumps(intentions), encoding="utf-8")
    with pytest.raises(ValueError, match="initial-equivalence.json"):
        workflow.prepare_hamburg_od_from_explicit(prepared_dir=source, output_dir=tmp_path / "blocked")
    report = json.loads((tmp_path / "blocked/initial-equivalence.json").read_bytes())
    assert report["status"] == "blocked" and not report["checks"]["unique_vehicle_ids_identical"]
    assert report["checks"]["all_source_inputs_unchanged"]
    assert (tmp_path / "blocked/initial/demand.rou.xml").exists()


def test_approved_od_initialization_preserves_requests_and_extends_internal_end(tmp_path):
    source = Path(__file__).resolve().parents[1] / "benchmarks/hamburg_ring1_munich_corridor_20261022_v1"
    audit_hamburg_od_definition(case_dir=source, output_dir=tmp_path / "audit")
    model = prepare_hamburg_od_baseline(case_dir=source, audit_dir=tmp_path / "audit", output_dir=tmp_path / "model")
    assert len(model["cells"]) == 294
    assert sum(c["initial_flow"] for c in model["cells"]) == 8640
    assert model["exit_extensions"] == {"27064186#0": ["27064186#0", "1073054122#1", "1345098540", "24483197", "199500676"]}
    values = [c["initial_flow"] for c in model["cells"]]
    output = write_hamburg_od_demand(model_file=tmp_path / "model/od-model.json", values=values, output_dir=tmp_path / "initial")
    vehicles = ET.parse(output / "demand.rou.xml").getroot().findall("vehicle")
    old = ET.parse(tmp_path / "model/demand.rou.xml").getroot().findall("vehicle")
    assert len(vehicles) == len(old) == 8640
    assert sorted(float(v.get("depart")) for v in vehicles) == sorted(float(v.get("depart")) for v in old)
    assert sum(v.find("route").get("edges").split()[-1] == "199500676" for v in vehicles) == 2589
    assert all(v.find("route").get("edges").split()[-1] != "27064186#0" for v in vehicles)
    assigned = json.loads((output / "lane-intentions.json").read_bytes())["vehicles"]
    assert len(assigned) == 8640 and all(v.get("id").startswith("od_") for v in vehicles)
    assert "target_begin_s" not in assigned[0]
    changed = np.zeros(294)
    changed[0], changed[1] = 2.5, 3.49
    out = write_hamburg_od_demand(model_file=tmp_path / "model/od-model.json", values=changed, output_dir=tmp_path / "changed")
    vector = json.loads((out / "od-vector.json").read_bytes())
    assert vector["integer_counts"][:2] == [3, 3] and vector["vehicle_count"] == 6
    types = Counter(r["od_cell"] for r in json.loads((out / "lane-intentions.json").read_bytes())["vehicles"])
    assert types == {0: 3, 1: 3}


@pytest.mark.parametrize("flat", [False, True])
def test_native_workflow_counts_evaluations_and_retains_failed_gain_pair(tmp_path, monkeypatch, flat):
    prepared = tmp_path / "prepared"
    prepared.mkdir()
    (prepared / "initial-od-vector.json").write_text(json.dumps({"values": [8]}), encoding="utf-8")
    called = []

    def measured(**kwargs):
        value = float(kwargs["values"][0])
        loss = 1 if flat else (value - 12) ** 2
        called.append((Path(kwargs["output_dir"]).name, value))
        return {"loss": loss, "MAE": loss, "max_abs_error": loss, "healthy": False,
                "exact_count_fit": False,
                "completion": dict(collisions=1, teleports=1, waiting=0, running=0)}

    monkeypatch.setattr(workflow, "measure_hamburg_od", measured)
    out = tmp_path / "study"
    result = workflow.run_hamburg_od_spsa(prepared_dir=prepared, output_dir=out, sumo_binary="unused",
        settings=dict(a=None, initial_step=1, A=3, c=2, alpha=.602, gamma=.101, iterations=1, seed=104))
    assert not result["formal_result"]
    assert [name for name, _ in called[:3]] == ["initial", "iteration-0001-plus", "iteration-0001-minus"]
    if flat:
        assert len(called) == 3 and result["completed_iterations"] == 0
        assert result["stopping_reason"] == "initial_gain_not_identified"
    else:
        assert len(called) == 4 and result["completed_iterations"] == 1
        assert called[-1] == ("final", 9)
    assert (out / "README.md").exists() and (out / "evaluations.json").exists()


def test_native_zero_target_stops_only_when_health_is_also_established(tmp_path, monkeypatch):
    prepared = tmp_path / "prepared"
    prepared.mkdir()
    (prepared / "initial-od-vector.json").write_text(json.dumps({"values": [8]}), encoding="utf-8")

    def exact(**kwargs):
        return {"loss": 0, "MAE": 0, "max_abs_error": 0, "healthy": True, "exact_count_fit": True,
                "completion": dict(collisions=0, teleports=0, waiting=0, running=0)}

    monkeypatch.setattr(workflow, "measure_hamburg_od", exact)
    result = workflow.run_hamburg_od_spsa(prepared_dir=prepared, output_dir=tmp_path / "exact", sumo_binary="unused",
        settings=dict(a=None, initial_step=1, A=3, c=2, alpha=.602, gamma=.101, iterations=30, seed=104))
    assert result["formal_result"] and result["native_evaluations"] == 1
    assert result["stopping_reason"] == "healthy_native_zero_residual"


@pytest.mark.parametrize("body_nodes", [None, ["14398547", "205069138", "18653534", "LSA104_part0"]])
def test_real_hamburg_decimal_metadata_times_join_integer_native_times(tmp_path, monkeypatch, body_nodes):
    root = Path(__file__).resolve().parents[1]
    original = root / "benchmarks/hamburg_ring1_munich_corridor_20261022_v1"
    measured_case = root / "benchmarks/hamburg_ring1_spillback_repair_complete_20261022_v2"
    prepared, proposal = tmp_path / "prepared", tmp_path / "proposal"
    prepared.mkdir()
    proposal.mkdir()
    if body_nodes is not None:
        (prepared / "scenario.json").write_text(json.dumps({"junction_body_hold_nodes": body_nodes}), encoding="utf-8")
    (prepared / "expected-counts.csv").write_bytes(gzip.decompress((original / "inputs/expected-counts.csv.gz").read_bytes()))
    assert b"3600.0,4500.0" in (prepared / "expected-counts.csv").read_bytes()

    def native(**kwargs):
        assert kwargs["junction_body_hold_nodes"] == tuple(body_nodes or ["14398547", "205069138", "18653534"])
        out = kwargs["output_dir"]
        out.mkdir()
        (out / "field-comparison.csv").write_bytes((measured_case / "field-comparison.csv").read_bytes())
        run = json.loads(gzip.decompress((measured_case / "run.json.gz").read_bytes()))
        assert ",3600,4500," in (out / "field-comparison.csv").read_text(encoding="utf-8")
        return run

    monkeypatch.setattr(workflow, "write_hamburg_od_demand", lambda **kwargs: proposal)
    monkeypatch.setattr(workflow, "run_point_detector_demand", native)
    result = workflow.measure_hamburg_od(prepared_dir=prepared, values=[], output_dir=proposal, sumo_binary="unused")
    known = json.loads((measured_case / "results.json").read_bytes())
    assert result["MAE"] == known["diagnostic_field_MAE"]
    assert result["station_MAE"] == known["diagnostic_station_MAE"]
    assert result["field_rows"] == 130 and result["derived_station_rows"] == 40
    assert not result["healthy"] and not result["exact_count_fit"]


@pytest.mark.parametrize("nodes", [[], ["LSA104_part0", "LSA104_part0"], "LSA104_part0", [""], None])
def test_invalid_explicit_body_scope_stops_before_generating_demand(tmp_path, nodes):
    (tmp_path / "scenario.json").write_text(json.dumps({"junction_body_hold_nodes": nodes}), encoding="utf-8")
    with pytest.raises(ValueError, match="junction_body_hold_nodes"):
        workflow.measure_hamburg_od(prepared_dir=tmp_path, values=[], output_dir=tmp_path / "not-generated", sumo_binary="unused")
    assert not (tmp_path / "not-generated").exists()


def test_new_study_can_stop_on_unsafe_native_evaluation_without_an_update(tmp_path, monkeypatch):
    prepared = tmp_path / "prepared"
    prepared.mkdir()
    (prepared / "initial-od-vector.json").write_text(json.dumps({"values": [8]}), encoding="utf-8")
    calls = []

    def measured(**kwargs):
        calls.append(Path(kwargs["output_dir"]).name)
        return {"loss": 1, "MAE": 1, "max_abs_error": 1, "healthy": False, "exact_count_fit": False,
                "completion": dict(collisions=1, teleports=1, waiting=0, running=0)}

    monkeypatch.setattr(workflow, "measure_hamburg_od", measured)
    result = workflow.run_hamburg_od_spsa(prepared_dir=prepared, output_dir=tmp_path / "stopped", sumo_binary="unused",
        settings=dict(a=None, initial_step=1, A=0, c=2, alpha=.602, gamma=.101, iterations=3, seed=104),
        stop_on_unsafe=True)
    assert calls == ["initial"] and result["completed_iterations"] == 0
    assert result["stopping_reason"] == "unsafe_native_evaluation"
    assert not result["formal_result"]


def test_explicit_od_native_measurement_reuses_full_count_validation(tmp_path):
    from astra_sumo.core.hamburg_od_spsa import summarize_hamburg_od_measurement
    source = Path(__file__).resolve().parents[1] / "benchmarks/hamburg_ring1_directed_backlog_20261023_v1/merge-evaluation"
    prepared, proposal = tmp_path / "prepared", tmp_path / "proposal"
    prepared.mkdir()
    (proposal / "native").mkdir(parents=True)
    (prepared / "expected-counts.csv").write_bytes(gzip.decompress((source / "expected-counts.csv.gz").read_bytes()))
    (proposal / "native/field-comparison.csv").write_bytes((source / "field-comparison.csv").read_bytes())
    native = json.loads(gzip.decompress((source / "run.json.gz").read_bytes()))
    result = summarize_hamburg_od_measurement(prepared_dir=prepared, proposal_dir=proposal, native=native)
    assert result == json.loads((source / "evaluation.json").read_bytes())
