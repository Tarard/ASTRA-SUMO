"""Explicitly rerun the frozen two-car support test; never fit measured counts."""

import argparse
import gzip
from importlib.metadata import version
import json
from pathlib import Path
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    case, out = Path(__file__).resolve().parent, args.output_dir.resolve()
    assert not out.exists() and not out.is_relative_to(case) and not case.is_relative_to(out)
    assert Path(sys.prefix).resolve() == (case.parents[1] / ".venv").resolve()
    assert all(version(n) == "1.27.1" for n in ("eclipse-sumo", "libsumo", "traci", "sumolib"))
    from validate import verify
    verify(case)
    inputs = out / "inputs"
    inputs.mkdir(parents=True)
    for name in ("network.net.xml", "demand.rou.xml", "source-e1.add.xml", "signal-programs.add.xml", "expected-counts.csv", "lane-intentions.json"):
        (inputs / name).write_bytes(gzip.decompress((case / "native" / (name + ".gz")).read_bytes()))
    import sumo
    from astra_sumo.core.detector_demand_replay import run_point_detector_demand
    from astra_sumo.core.route_sensor_matrix import read_contributing_e1_passages
    native = out / "native"
    print("Starting explicit two-car native support replay, not calibration.", flush=True)
    result = run_point_detector_demand(net_file=inputs / "network.net.xml", route_file=inputs / "demand.rou.xml",
        e1_additional_file=inputs / "source-e1.add.xml", expected_counts_csv=inputs / "expected-counts.csv",
        signal_program_file=inputs / "signal-programs.add.xml", lane_intentions_file=inputs / "lane-intentions.json",
        output_dir=native, end_s=14400, step_length=.1, seed=104, backend="libsumo", timeout_seconds=300,
        sumo_binary=str(Path(sumo.__file__).parent / "bin/sumo.exe"), junction_body_hold=True,
        junction_body_hold_nodes=("14398547", "205069138", "18653534", "LSA104_part0"))
    passages = read_contributing_e1_passages(instant_file=native / "instant-e1.xml", periodic_file=native / "field-counts.xml",
        detector_ids=result["instant_to_periodic_ids"], step_length=.1)
    original = json.loads(gzip.decompress((case / "result.json.gz").read_bytes()))
    assert result["healthy"] and result["completion"] == original["completion"] and passages["reconciled"]
    assert passages["passages"] == original["passages"]["passages"]
    (out / "reproduction.json").write_text(json.dumps({"status": "pass", "formal_count_calibration": False,
        "new_native_run": True, "completion": result["completion"], "passages": passages["passages"]}, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
