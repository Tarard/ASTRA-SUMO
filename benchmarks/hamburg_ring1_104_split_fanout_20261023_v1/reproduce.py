"""Reproduce the LSA104 collision and its explicit complete-path candidate."""

import argparse
import gzip
from importlib.metadata import version
import json
from pathlib import Path
import sys
import xml.etree.ElementTree as ET

import sumo
import sumolib
import traci

from astra_sumo.core.artifact_io import write_json_atomic
from astra_sumo.core.candidate_contracts import file_sha256
from astra_sumo.core.command_runner import run_command
from astra_sumo.core.junction_split_fanout import materialize_unsplit_fanout


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    out = parser.parse_args().output_dir.resolve()
    repo = Path(__file__).resolve().parents[2]
    venv = repo / ".venv"
    if out.exists():
        raise ValueError("Use a new output directory.")
    if Path(sys.executable).resolve() != venv / "Scripts/python.exe" or any(
            not Path(m.__file__).is_relative_to(venv) for m in (sumo, sumolib, traci)):
        raise ValueError("Use repository Python, SUMO_HOME and PYTHONPATH; remove system SUMO paths.")
    if {version(n) for n in ("eclipse-sumo", "traci", "sumolib")} != {"1.27.1"}:
        raise ValueError("Use the locked SUMO 1.27.1 environment.")
    out.mkdir(parents=True)
    source = repo / "benchmarks/hamburg_ring1_od_spsa_completed_20261023_v3/evaluations/final"
    for name in ("network.net.xml", "signal-programs.add.xml"):
        (out / name).write_bytes(gzip.decompress((source / (name + ".gz")).read_bytes()))
    keys = [("139083723#0", 2, "234423652#1", 0), ("139083723#0", 2, "234423652#1", 1)]
    binaries = Path(sumo.__file__).parent / "bin"
    candidate = materialize_unsplit_fanout(source_file=out / "network.net.xml", junction_id="LSA104_part0",
        connection_keys=keys, output_dir=out / "candidate", netconvert_binary=binaries / "netconvert.exe")
    cases = {}
    for label, network, same_exit in [("source_different_exits", out / "network.net.xml", False),
                                     ("source_same_exit", out / "network.net.xml", True),
                                     ("candidate_different_exits", Path(candidate["candidate_file"]), False)]:
        folder = out / label
        folder.mkdir()
        demand = ET.parse(Path(__file__).with_name("cohort.rou.xml"))
        if same_exit:
            demand.find("vehicle[@id='slow']").set("arrivalLane", "0")
        route = folder / "demand.rou.xml"
        demand.write(route, encoding="utf-8", xml_declaration=True)
        command = [str(binaries / "sumo.exe"), "-n", str(network), "-r", str(route), "-a", str(out / "signal-programs.add.xml"),
            "--step-length", "0.1", "--seed", "104", "--end", "300", "--collision.check-junctions", "true",
            "--collision-output", "collisions.xml", "--summary-output", "summary.xml", "--tripinfo-output", "tripinfo.xml",
            "--fcd-output", "fcd.xml", "--lanechange-output", "lanechanges.xml", "--no-step-log", "true", "--precision", "12"]
        execution = run_command(command, cwd=folder, timeout_seconds=30)
        write_json_atomic(folder / "command.json", execution.to_dict())
        if execution.status != "pass":
            raise RuntimeError("Native probe failed; inspect its command.json.")
        collisions = [dict(r.attrib) for r in ET.parse(folder / "collisions.xml").getroot()]
        final = ET.parse(folder / "summary.xml").getroot().findall("step")[-1]
        cases[label] = {"collisions": collisions, "completion": {k: int(final.get(k)) for k in (
            "loaded", "inserted", "arrived", "running", "waiting", "collisions", "teleports")}}
    assert len(cases["source_different_exits"]["collisions"]) == 1
    assert not cases["source_same_exit"]["collisions"] and not cases["candidate_different_exits"]["collisions"]
    assert cases["candidate_different_exits"]["completion"]["arrived"] == 2
    report = {"status": "measured", "formal_result": False, "source_network_sha256": file_sha256(out / "network.net.xml"),
              "candidate": candidate, "cases": cases,
              "claim_boundary": "Synthetic slow leader exposes split-sibling overlap. This is not demand fitting or observed driver speed."}
    write_json_atomic(out / "evidence.json", report)
    print(json.dumps({"output": str(out), "cases": cases}, indent=2))


if __name__ == "__main__":
    main()
