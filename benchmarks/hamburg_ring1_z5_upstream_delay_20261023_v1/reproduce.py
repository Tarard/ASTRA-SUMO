"""Explicitly replay one frozen lane-prior demand; never call the OD generator."""

import argparse
import gzip
from importlib.metadata import version
import importlib.util
import json
from pathlib import Path
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", required=True, choices=("lane-swap-1pair", "lane-swap-5pair"))
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()
    case, out = Path(__file__).resolve().parent, args.output_dir.resolve()
    assert Path(sys.prefix).resolve() == (case.parents[1] / ".venv").resolve()
    assert all(version(n) == "1.27.1" for n in ("eclipse-sumo", "libsumo", "traci", "sumolib"))
    assert not out.exists() and not out.is_relative_to(case) and not case.is_relative_to(out)
    spec = importlib.util.spec_from_file_location("upstream_evidence", case / "freeze_evidence.py")
    audit = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(audit)
    sys.path.insert(0, str(case.parents[1] / "plugins/astra-sumo/src"))
    audit.verify(case)
    source = case / args.case
    prepared, evaluation = out / "prepared", out / "evaluation"
    prepared.mkdir(parents=True)
    evaluation.mkdir()
    for path in (source / "prepared").glob("*.gz"):
        (prepared / path.stem).write_bytes(gzip.decompress(path.read_bytes()))
    for name in ("demand.rou.xml", "lane-intentions.json"):
        (evaluation / name).write_bytes((prepared / name).read_bytes())
    (evaluation / "od-vector.json").write_bytes(audit.raw(source / "evaluation/od-vector.json"))
    import sumo
    from astra_sumo.core.detector_demand import audit_expected_to_e1_strict, read_csv_rows, write_csv
    from astra_sumo.core.detector_demand_replay import run_point_detector_demand
    from astra_sumo.core.hamburg_od_spsa import _body_hold_nodes, summarize_hamburg_od_measurement
    from astra_sumo.core.route_sensor_matrix import read_contributing_e1_passages

    print("Starting one complete native libsumo replay of explicit demand, 14400 s.", flush=True)
    native = run_point_detector_demand(net_file=prepared / "network.net.xml", route_file=prepared / "demand.rou.xml",
        e1_additional_file=prepared / "field-loops.add.xml", expected_counts_csv=prepared / "expected-counts.csv",
        output_dir=evaluation / "native", end_s=14400, timeout_seconds=4800, seed=104, backend="libsumo",
        sumo_binary=str(Path(sumo.__file__).parent / "bin/sumo.exe"), signal_program_file=prepared / "control.add.xml",
        lane_intentions_file=prepared / "lane-intentions.json", junction_body_hold=True,
        junction_body_hold_nodes=_body_hold_nodes(prepared))
    measured = summarize_hamburg_od_measurement(prepared_dir=prepared, proposal_dir=evaluation, native=native)
    assert measured == audit.read_json(source / "evaluation/evaluation.json")
    full = audit_expected_to_e1_strict(read_csv_rows(prepared / "initialization-counts.csv") +
        read_csv_rows(prepared / "expected-counts.csv"), evaluation / "native/field-counts.xml")
    assert len(full) == 182 and all(r["measured_nVehContrib"] is not None for r in full)
    write_csv(evaluation / "all182-comparison.csv", full, list(full[0]))
    assert read_csv_rows(evaluation / "all182-comparison.csv") == audit.rows(source / "evaluation/all182-comparison.csv")
    assert read_csv_rows(evaluation / "station-comparison.csv") == audit.rows(source / "evaluation/station-comparison.csv")
    passages = read_contributing_e1_passages(instant_file=evaluation / "native/instant-e1.xml",
        periodic_file=evaluation / "native/field-counts.xml", detector_ids=native["instant_to_periodic_ids"], step_length=.1)
    original = json.loads(gzip.decompress(audit.raw(source / "evaluation/contributing-passages.json.gz")))
    assert passages["reconciled"] and passages["passages"] == original["passages"]
    report = {"status": "pass", "new_native_run": True, "formal_result": False, "case": args.case,
              "measurement": measured, "all182_rows": len(full), "full_passages_identical": True}
    (out / "reproduction.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2), flush=True)


if __name__ == "__main__":
    main()
