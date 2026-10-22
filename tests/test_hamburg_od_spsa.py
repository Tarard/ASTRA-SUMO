from collections import Counter
import json
from pathlib import Path
import xml.etree.ElementTree as ET

import numpy as np
import pytest

from astra_sumo.core import hamburg_od_spsa as workflow
from astra_sumo.core.hamburg_od_definition import audit_hamburg_od_definition
from astra_sumo.core.hamburg_od_spsa import prepare_hamburg_od_baseline, write_hamburg_od_demand


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
