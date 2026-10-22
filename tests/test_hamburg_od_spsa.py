from collections import Counter
import json
from pathlib import Path
import xml.etree.ElementTree as ET

import numpy as np

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
