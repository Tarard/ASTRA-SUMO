"""Remeasure the frozen final OD after the explicit LSA104 repair candidate."""

import argparse
import gzip
from importlib.metadata import version
from pathlib import Path
import sys

import sumo
import sumolib
import traci

from astra_sumo.core.artifact_io import write_json_atomic
from astra_sumo.core.candidate_contracts import file_sha256
from astra_sumo.core.detector_demand_replay import run_point_detector_demand
from astra_sumo.core.junction_split_fanout import materialize_unsplit_fanout


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--treatment", choices=("native-only", "body-hold"), required=True)
    args = parser.parse_args()
    repo = Path(__file__).resolve().parents[2]
    venv = repo / ".venv"
    if Path(sys.executable).resolve() != venv / "Scripts/python.exe" or any(
            not Path(m.__file__).is_relative_to(venv) for m in (sumo, sumolib, traci)):
        raise ValueError("Use repository Python, SUMO_HOME and PYTHONPATH; remove system SUMO paths.")
    if {version(n) for n in ("eclipse-sumo", "traci", "sumolib")} != {"1.27.1"}:
        raise ValueError("Use the locked SUMO 1.27.1 environment.")
    out = args.output_dir.resolve()
    if out.exists():
        raise ValueError("Use a new output directory.")
    out.mkdir(parents=True)
    source = repo / "benchmarks/hamburg_ring1_od_spsa_completed_20261023_v3/evaluations/final"
    prepared = out / "inputs"
    prepared.mkdir()
    for name in ("network.net.xml", "demand.rou.xml", "signal-programs.add.xml", "source-e1.add.xml", "expected-counts.csv", "lane-intentions.json"):
        (prepared / name).write_bytes(gzip.decompress((source / (name + ".gz")).read_bytes()))
    keys = [("139083723#0", 2, "234423652#1", 0), ("139083723#0", 2, "234423652#1", 1)]
    binaries = Path(sumo.__file__).parent / "bin"
    candidate = materialize_unsplit_fanout(source_file=prepared / "network.net.xml", junction_id="LSA104_part0",
        connection_keys=keys, output_dir=out / "candidate", netconvert_binary=binaries / "netconvert.exe")
    nodes = ["14398547", "205069138", "18653534"]
    if args.treatment == "body-hold":
        nodes.append("LSA104_part0")
    write_json_atomic(out / "request.json", {"treatment": args.treatment, "junction_body_hold_nodes": nodes,
        "candidate": candidate, "input_sha256": {p.name: file_sha256(p) for p in prepared.iterdir()},
        "claim_boundary": "Same final OD, control, times, seed and all 130 targets. Body hold is an explicit driver assumption."})
    result = run_point_detector_demand(net_file=Path(candidate["candidate_file"]), route_file=prepared / "demand.rou.xml",
        e1_additional_file=prepared / "source-e1.add.xml", expected_counts_csv=prepared / "expected-counts.csv",
        output_dir=out / "native", end_s=14400, sumo_binary=str(binaries / "sumo.exe"), step_length=0.1, seed=104,
        timeout_seconds=4800, signal_program_file=prepared / "signal-programs.add.xml",
        lane_intentions_file=prepared / "lane-intentions.json", junction_body_hold=True, junction_body_hold_nodes=tuple(nodes))
    write_json_atomic(out / "result.json", {k: result.get(k) for k in ("status", "healthy", "completion", "field_metrics", "exact_field_bins", "observer_complete")})


if __name__ == "__main__":
    main()
