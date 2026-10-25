"""Recheck the frozen merge hypothesis with the repaired network and fixed OD."""

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

from astra_sumo.core.hamburg_od_spsa import measure_hamburg_od, prepare_hamburg_od_baseline
from astra_sumo.core.hamburg_od_spsa import _body_hold_nodes, summarize_hamburg_od_measurement
from astra_sumo.core.detector_demand_replay import run_point_detector_demand
from astra_sumo.core.hamburg_spillback_repair import (
    replay_frozen_merge_program,
    remove_deleted_service_stage,
    extend_merge_tail,
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument(
        "--variant",
        choices=["merge", "exit-stage", "retiming", "tail", "tail27", "original-seed-restored"],
        required=True,
    )
    options = parser.parse_args()
    out = options.output_dir.resolve()
    out.mkdir(parents=True, exist_ok=False)
    repo = Path(__file__).resolve().parents[2]
    assert Path(sys.executable).resolve() == (repo / ".venv/Scripts/python.exe").resolve()
    assert all(version(name) == "1.27.1" for name in ("eclipse-sumo", "traci", "sumolib", "libsumo"))
    source = repo / "benchmarks/hamburg_ring1_119_straight_order_20261023_v1"
    prepared = out / "prepared"
    model = prepare_hamburg_od_baseline(
        case_dir=repo / "benchmarks/hamburg_ring1_munich_corridor_20261022_v1",
        audit_dir=repo / "benchmarks/hamburg_ring1_spsa_od_definition_20261022_v1",
        output_dir=prepared,
    )
    network = gzip.decompress((source / "candidate.net.xml.gz").read_bytes())
    (prepared / "network.net.xml").write_bytes(network)
    model["source_network_sha256"] = hashlib.sha256(network).hexdigest()
    (prepared / "od-model.json").write_bytes((json.dumps(model, indent=2) + "\n").encode())
    scenario = json.loads(gzip.decompress((source / "scenario.json.gz").read_bytes()))
    scenario["control_variant"] = "historical_merge_coordination_recheck_on_repaired_network"
    scenario["control_assumptions"] += (
        " GS_266199063 uses the exact frozen coordinated placeholder. No measured field SPaT claim."
    )
    frozen = repo / "benchmarks/hamburg_ring1_spillback_repair_20261022_v1/proposals/merge_coordinated/control.add.xml"
    current = ET.parse(prepared / "control.add.xml").getroot()
    candidate = replay_frozen_merge_program(current, ET.parse(frozen).getroot())
    assert [a.get("id") for a, b in zip(current, candidate) if ET.tostring(a) != ET.tostring(b)] == ["GS_266199063"]
    if options.variant in ("tail", "tail27"):
        candidate = extend_merge_tail(candidate, 18 if options.variant == "tail" else 27)
        scenario["control_variant"] = "merge_tail_extension"
    if options.variant == "exit-stage":
        original = ET.parse(
            repo / "benchmarks/hamburg_ring1_primary_entry_full_20261022_v1/inputs/control.add.xml"
        ).getroot()
        old = candidate.find("tlLogic[@id='266197525']")
        fixed, removed = remove_deleted_service_stage(original.find("tlLogic[@id='266197525']"), old)
        assert [x["source_phase_index"] for x in removed] == [2]
        position = list(candidate).index(old)
        candidate.remove(old)
        candidate.insert(position, fixed)
        scenario["control_variant"] = "coordinated_merge_with_deleted_service_removed"
        scenario["control_assumptions"] += (
            " Remove only the source-proven deleted service at 266197525, preserving original clearances."
        )
    ET.ElementTree(candidate).write(prepared / "control.add.xml", encoding="utf-8", xml_declaration=True)
    scenario["control_sha256"] = hashlib.sha256((prepared / "control.add.xml").read_bytes()).hexdigest()
    (prepared / "scenario.json").write_bytes((json.dumps(scenario, indent=2) + "\n").encode())
    vector = json.loads((source / "full-native/od-vector.json").read_bytes())["values"]
    if options.variant == "original-seed-restored":
        vector = [cell["initial_flow"] for cell in model["cells"]]
        assert sum(vector) == 8640 and vector == [len(cell["templates"]) for cell in model["cells"]]
    else:
        assert sum(int(value + 0.5) for value in vector) == 8603
    case = Path(__file__).parent
    if options.variant == "retiming":
        evaluation = out / "evaluation"
        evaluation.mkdir()
        for name in ("demand.rou.xml", "lane-intentions.json"):
            (evaluation / name).write_bytes(
                gzip.decompress((case / "retiming-evaluation" / (name + ".gz")).read_bytes())
            )
        (evaluation / "od-vector.json").write_bytes((case / "retiming-evaluation/od-vector.json").read_bytes())
        native = run_point_detector_demand(
            net_file=prepared / "network.net.xml",
            route_file=evaluation / "demand.rou.xml",
            e1_additional_file=prepared / "field-loops.add.xml",
            expected_counts_csv=prepared / "expected-counts.csv",
            output_dir=evaluation / "native",
            end_s=14400,
            timeout_seconds=4800,
            sumo_binary=str(Path(sumo.__file__).parent / "bin/sumo.exe"),
            signal_program_file=prepared / "control.add.xml",
            lane_intentions_file=evaluation / "lane-intentions.json",
            junction_body_hold=True,
            junction_body_hold_nodes=_body_hold_nodes(prepared),
            seed=104,
            backend="libsumo",
        )
        result = summarize_hamburg_od_measurement(prepared_dir=prepared, proposal_dir=evaluation, native=native)
    else:
        result = measure_hamburg_od(
            prepared_dir=prepared,
            values=vector,
            output_dir=out / "evaluation",
            sumo_binary=Path(sumo.__file__).parent / "bin/sumo.exe",
            backend="libsumo",
        )
    assert result == json.loads((case / f"{options.variant}-evaluation/evaluation.json").read_bytes())
    counts = {
        (row.get("id"), float(row.get("begin")), float(row.get("end"))): int(row.get("nVehContrib"))
        for row in ET.parse(out / "evaluation/native/field-counts.xml").getroot()
    }
    targets = []
    for name in ("initialization-counts.csv", "expected-counts.csv"):
        with (prepared / name).open(encoding="utf-8", newline="") as stream:
            targets.extend(csv.DictReader(stream))
    rows = [
        {
            **row,
            "measured_nVehContrib": counts[row["detector_id"], float(row["begin"]), float(row["end"])],
            "residual": counts[row["detector_id"], float(row["begin"]), float(row["end"])] - int(row["expected_total"]),
        }
        for row in targets
    ]
    assert len(rows) == 182
    with (out / "evaluation/all182-comparison.csv").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    summary = json.loads((case / "results.json").read_bytes())
    assert sum(abs(row["residual"]) for row in rows) == summary["all182_TAE"][options.variant]
    if options.variant not in ("retiming", "original-seed-restored"):
        for name in ("demand.rou.xml", "lane-intentions.json"):
            assert (out / "evaluation" / name).read_bytes() == gzip.decompress(
                (source / "full-native" / (name + ".gz")).read_bytes()
            )
    if options.variant == "original-seed-restored":
        for name in ("demand.rou.xml", "lane-intentions.json"):
            assert (out / "evaluation" / name).read_bytes() == gzip.decompress(
                (case / "original-seed-restored-evaluation" / (name + ".gz")).read_bytes()
            )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
