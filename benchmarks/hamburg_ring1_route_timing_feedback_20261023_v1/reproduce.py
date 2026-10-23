"""Replay an explicit frozen route/timing case with the existing native runner."""

import argparse
import csv
import gzip
import hashlib
from importlib.metadata import version
import json
from pathlib import Path
import sys
import xml.etree.ElementTree as ET

import sumo

from astra_sumo.core.detector_demand_replay import run_point_detector_demand
from astra_sumo.core.hamburg_od_spsa import _body_hold_nodes, summarize_hamburg_od_measurement


def main():
    case = Path(__file__).resolve().parent
    metadata = json.loads((case / "results.json").read_bytes())
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", required=True, choices=metadata["complete_runs"])
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()
    assert Path(sys.prefix).resolve() == (case.parents[1] / ".venv").resolve()
    assert all(version(name) == "1.27.1" for name in ("eclipse-sumo", "libsumo", "traci", "sumolib"))
    source = case / args.case
    out = args.output_dir.resolve()
    if out.exists() or out.is_relative_to(case) or case.is_relative_to(out):
        raise ValueError("Use a new output directory separate from the frozen evidence.")
    prepared, evaluation = out / "prepared", out / "evaluation"
    prepared.mkdir(parents=True)
    evaluation.mkdir()
    for path in (source / "prepared").glob("*.gz"):
        (prepared / path.stem).write_bytes(gzip.decompress(path.read_bytes()))
    preflight = json.loads(gzip.decompress((source / "preflight.json.gz").read_bytes()))
    for name, expected_hash in preflight["input_sha256"].items():
        assert hashlib.sha256((prepared / name).read_bytes()).hexdigest() == expected_hash
    for name in ("demand.rou.xml", "lane-intentions.json"):
        (evaluation / name).write_bytes((prepared / name).read_bytes())
    (evaluation / "od-vector.json").write_bytes(gzip.decompress((source / "evaluation/od-vector.json.gz").read_bytes()))
    native = run_point_detector_demand(
        net_file=prepared / "network.net.xml",
        route_file=prepared / "demand.rou.xml",
        e1_additional_file=prepared / "field-loops.add.xml",
        expected_counts_csv=prepared / "expected-counts.csv",
        output_dir=evaluation / "native",
        end_s=14400,
        timeout_seconds=4800,
        seed=104,
        backend="libsumo",
        sumo_binary=str(Path(sumo.__file__).parent / "bin/sumo.exe"),
        signal_program_file=prepared / "control.add.xml",
        lane_intentions_file=prepared / "lane-intentions.json",
        junction_body_hold=True,
        junction_body_hold_nodes=_body_hold_nodes(prepared),
    )
    result = summarize_hamburg_od_measurement(prepared_dir=prepared, proposal_dir=evaluation, native=native)
    expected = json.loads(gzip.decompress((source / "evaluation/evaluation.json.gz").read_bytes()))
    assert result == expected
    counts = {
        (row.get("id"), float(row.get("begin")), float(row.get("end"))): int(row.get("nVehContrib"))
        for row in ET.parse(evaluation / "native/field-counts.xml").getroot()
    }
    rows = []
    for name in ("initialization-counts.csv", "expected-counts.csv"):
        with (prepared / name).open(encoding="utf-8", newline="") as stream:
            for row in csv.DictReader(stream):
                value = counts[row["detector_id"], float(row["begin"]), float(row["end"])]
                rows.append({**row, "measured_nVehContrib": value, "residual": value - int(row["expected_total"])})
    assert len(rows) == 182
    with (evaluation / "all182-comparison.csv").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    verdict = metadata["evaluations"][args.case]
    assert sum(abs(row["residual"]) for row in rows) == verdict["all182_TAE"]
    print(json.dumps({"evaluation": result, "all182_TAE": verdict["all182_TAE"], "formal_result": False}, indent=2))


if __name__ == "__main__":
    main()
