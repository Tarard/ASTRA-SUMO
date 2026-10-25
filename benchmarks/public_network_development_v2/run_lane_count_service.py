"""Reproduce Hamburg lane service, fixed turns, and paired source diagnostics."""
import gzip
import json
from pathlib import Path
import subprocess
import sys
import xml.etree.ElementTree as ET

import sumo

from astra_sumo.core.candidate_contracts import file_sha256
from astra_sumo.core.network_edit_applicability import verify_network_edit
from astra_sumo.core.paired_driving import compare_driving_cohort


def run(output_dir):
    output = Path(output_dir).resolve()
    output.mkdir(parents=True, exist_ok=False)
    repository = Path(__file__).resolve().parents[2]
    fixture = repository / "benchmarks/hamburg_loaded_links_20261018_v1/candidate.net.xml.gz"
    source = output / "source.net.xml"
    source.write_bytes(gzip.decompress(fixture.read_bytes()))
    digest = file_sha256(source)
    binary_dir = Path(sumo.__file__).resolve().parent / "bin"
    suffix = ".exe" if sys.platform == "win32" else ""
    binary = binary_dir / f"netconvert{suffix}"
    implementation = repository / "plugins/astra-sumo/src/astra_sumo/core/network_edit_operations.py"
    declaration = {"source_sha256": digest, "fixture_sha256": file_sha256(fixture),
        "implementation_sha256": file_sha256(implementation), "runner_sha256": file_sha256(Path(__file__)),
        "commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repository, text=True).strip(),
        "seeds": [6, 10, 42], "horizon_s": 600,
        "claim_boundary": "Synthetic source-support and driving checks. No field signal, physical endpoint identity or performance claim."}
    (output / "declaration.json").write_text(json.dumps(declaration, indent=2), encoding="utf-8")
    cases = {}
    for name, layout in (("add-right", {"num_lanes": 4, "add_side": "right"}),
                         ("remove-lower", {"num_lanes": 2, "remove_index": 0})):
        ledger = output / f"{name}.json"
        ledger.write_text(json.dumps({"source_net_file": str(source), "source_sha256": digest,
            "operations": [{"id": name, "operation": "set_lane_count",
                "params": {"edge_id": "47854310#1", "vehicle_class": "passenger", **layout}}]}, indent=2), encoding="utf-8")
        cases[name] = verify_network_edit(ledger_file=ledger, output_dir=output / name,
            netconvert_binary=str(binary), mode="explore")
        (output / "edits.json").write_text(json.dumps(cases, indent=2), encoding="utf-8")
        print(name, cases[name]["status"], flush=True)
    networks = {"source": source, **{name: Path(case["candidate_net_file"]) for name, case in cases.items()}}
    targeted_file = output / "targeted.rou.xml"
    root = ET.Element("routes")
    for name, mode in (("car", "passenger"), ("bus", "bus")):
        ET.SubElement(root, "vType", id=name, vClass=mode)
    requests = [("right-car", "car", "47854310#1 714078605", "0"),
                ("right-bus", "bus", "47854310#1 714078605", "0"),
                ("through-car", "car", "47854310#1 47854310#2", "0"),
                ("incoming-a", "car", "-780360331 47854310#1 714078605", "best"),
                ("incoming-b", "car", "47854310#0 47854310#1 47854310#2", "best")]
    for index, (name, mode, roads, lane) in enumerate(requests):
        vehicle = ET.SubElement(root, "vehicle", id=name, type=mode, depart=str(15 * index), departLane=lane)
        ET.SubElement(vehicle, "route", edges=roads)
    ET.ElementTree(root).write(targeted_file, encoding="utf-8", xml_declaration=True)
    targeted = compare_driving_cohort(networks=networks, reference="source", routes=targeted_file,
        seeds=[42], horizon_s=600, output_dir=output / "targeted-driving", sumo_binary=str(binary_dir / f"sumo{suffix}"))
    paired = compare_driving_cohort(networks=networks, reference="source",
        routes=Path(cases["add-right"]["source_routeability"]["route_file"]), seeds=declaration["seeds"],
        horizon_s=declaration["horizon_s"], output_dir=output / "paired-driving", sumo_binary=str(binary_dir / f"sumo{suffix}"))
    checks = {"source_unchanged": file_sha256(source) == digest,
        "implementation_unchanged": file_sha256(implementation) == declaration["implementation_sha256"],
        "fixture_unchanged": file_sha256(fixture) == declaration["fixture_sha256"],
        "new_lane_served": cases["add-right"]["construction"]["new_lane_service"]["status"] == "pass",
        "source_turns_kept": all(case["construction"]["road_movement_preservation"]["status"] == "pass" for case in cases.values()),
        "original_requests_route": all(case["candidate_routeability"]["route_generation"]["status"] == "pass" for case in cases.values()),
        "targeted_runs_valid": all(case["valid"] for case in targeted["runs"]["42"].values()),
        "new_lane_used": targeted["runs"]["42"]["add-right"]["trips"]["right-car"]["departLane"] == "47854310#1_0",
        "paired_inputs_unchanged": not paired["input_changes"]}
    result = {"checks": checks, "edit_reports": {name: case["report_file"] for name, case in cases.items()},
        "targeted_driving": str(output / "targeted-driving/comparison.json"),
        "paired_driving": str(output / "paired-driving/comparison.json"),
        "paired_validity": paired["pair_validity_counts"], "performance_mean": paired["performance_mean"]}
    (output / "result.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2), flush=True)
    assert all(checks.values()), checks
    return result


if __name__ == "__main__":
    run(sys.argv[1])
