"""Prepare the five-round study on the candidate with a collision-free baseline evaluation."""

import argparse
import gzip
import hashlib
import json
from pathlib import Path
import shutil

from astra_sumo.core.hamburg_od_spsa import prepare_hamburg_od_baseline, write_hamburg_od_demand


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    out = parser.parse_args().output_dir.resolve()
    if out.exists():
        raise ValueError("Use a new study directory.")
    out.mkdir(parents=True)
    repo = Path(__file__).resolve().parents[2]
    prepared = out / "prepared"
    model = prepare_hamburg_od_baseline(case_dir=repo / "benchmarks/hamburg_ring1_munich_corridor_20261022_v1",
        audit_dir=repo / "benchmarks/hamburg_ring1_spsa_od_definition_20261022_v1", output_dir=prepared)
    source = repo / "benchmarks/hamburg_ring1_od_spsa_completed_20261023_v3/evaluations/final"
    candidate = repo / "benchmarks/hamburg_ring1_104_split_fanout_20261023_v1/candidate.net.xml.gz"
    network = gzip.decompress(candidate.read_bytes())
    (prepared / "network.net.xml").write_bytes(network)
    model["source_network_sha256"] = hashlib.sha256(network).hexdigest()
    model["network_representation_source"] = str(candidate)
    (prepared / "od-model.json").write_text(json.dumps(model, indent=2) + "\n", encoding="utf-8")
    vector = json.loads((source / "od-vector.json").read_bytes())
    assert len(vector["values"]) == 294 and vector["vehicle_count"] == 8622
    (prepared / "initial-od-vector.json").write_text(json.dumps({"values": vector["values"],
        "vehicle_count": vector["vehicle_count"], "source_vector": str(source / "od-vector.json")}, indent=2) + "\n", encoding="utf-8")
    scenario = json.loads((prepared / "scenario.json").read_bytes())
    scenario.update(junction_body_hold_nodes=["14398547", "205069138", "18653534", "LSA104_part0"],
        network_sha256=model["source_network_sha256"], status="diagnostic_candidate", exact_count_fit=False)
    (prepared / "scenario.json").write_text(json.dumps(scenario, indent=2) + "\n", encoding="utf-8")
    shutil.copyfile(Path(__file__).with_name("settings.json"), out / "settings.json")
    generated = write_hamburg_od_demand(model_file=prepared / "od-model.json", values=vector["values"], output_dir=out / "preflight-demand")
    for path, name in [(generated / "demand.rou.xml", "demand.rou.xml"),
                       (generated / "lane-intentions.json", "lane-intentions.json"),
                       (prepared / "expected-counts.csv", "expected-counts.csv"),
                       (prepared / "control.add.xml", "signal-programs.add.xml"),
                       (prepared / "field-loops.add.xml", "source-e1.add.xml")]:
        assert path.read_bytes() == gzip.decompress((source / (name + ".gz")).read_bytes()), name
    print(prepared)


if __name__ == "__main__":
    main()
