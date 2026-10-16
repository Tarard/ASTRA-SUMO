"""Measure loaded successors and conflict relations without driving vehicles."""

import argparse
from collections import defaultdict
from hashlib import sha256
import json
from pathlib import Path
import shutil
import sys
import xml.etree.ElementTree as ET

from .artifact_io import write_json_atomic
from .candidate_contracts import file_sha256
from .command_runner import run_command
from .internal_ownership import resolve_internal_ownership


def _capture(network, binary, label):
    import traci

    root = ET.parse(network).getroot()
    ownership = resolve_internal_ownership(root)
    internal = defaultdict(list)
    for lane, owner in ownership["lane"].items():
        if owner is None:
            raise ValueError(f"Internal lane ownership is unresolved: {lane}")
        internal[owner].append(lane)
    command = [binary, "-n", str(network), "--no-step-log", "true", "--duration-log.disable", "true"]
    traci.start(command, label=label, numRetries=3)
    connection = traci.getConnection(label)
    try:
        if set(connection.junction.getIDList()) != {row.get("id") for row in root.findall("junction")}:
            raise ValueError("Loaded junctions differ from the explicit network inventory.")
        if set(connection.lane.getIDList()) != {row.get("id") for row in root.findall("edge/lane")}:
            raise ValueError("Loaded lanes differ from the explicit network inventory.")
        nodes = {}
        for node in root.findall("junction"):
            identity = node.get("id")
            if identity.startswith(":") or node.get("type") == "internal":
                continue
            incoming = set(node.get("incLanes", "").split())
            links = {}
            for lane in sorted(incoming | set(internal[identity])):
                records = []
                for link in connection.lane.getLinks(lane):
                    row = {"successor": link[0], "via": link[4], "direction": link[6]}
                    if lane in incoming:
                        row["yield_to"] = sorted(connection.lane.getFoes(lane, link[0]))
                    records.append(row)
                links[lane] = sorted(records, key=lambda row: (row["successor"], row["via"]))
            nodes[identity] = {"lane_links": links,
                "internal_foes": {lane: sorted(connection.lane.getInternalFoes(lane)) for lane in sorted(internal[identity])}}
        return {"native_version": connection.getVersion(), "command": command, "junctions": nodes}
    finally:
        connection.close()


def _compare(snapshots, mutable_junction_ids):
    differences, counts = [], {"native": 0, "source": 0}
    unresolved = []
    def junctions(name):
        value = snapshots.get(name)
        return value.get("junctions") if isinstance(value, dict) and isinstance(value.get("junctions"), dict) else None

    candidate = junctions("candidate")
    for name, edited in (("source", False), ("native", True)):
        reference = junctions(name)
        if reference is None or candidate is None:
            unresolved.append(name)
            continue
        nodes = (set(reference) | set(candidate)) & mutable_junction_ids if edited else (
            set(reference) | set(candidate)) - mutable_junction_ids
        for node in sorted(nodes):
            counts[name] += 1
            if reference.get(node) != candidate.get(node):
                differences.append({"junction_id": node, "reference": name,
                    "expected": reference.get(node), "actual": candidate.get(node)})
    return {"status": "unavailable" if unresolved else "fail" if differences else "pass",
        "compared_junction_counts": counts, "unresolved_references": unresolved, "differences": differences}


def compare_loaded_link_semantics(*, source_net_file, native_net_file, candidate_net_file,
                                  mutable_junction_ids, output_dir, sumo_binary="sumo",
                                  timeout_seconds=120, command_runner=run_command):
    """Compare edited junctions with native output and all other junctions with source."""
    destination = Path(output_dir).resolve()
    mutable_junction_ids = set(mutable_junction_ids)
    destination.mkdir(parents=True, exist_ok=False)
    inputs = {name: {"path": str(Path(path).resolve(strict=True)), "sha256": file_sha256(Path(path))}
        for name, path in (("source", source_net_file), ("native", native_net_file), ("candidate", candidate_net_file))}
    request_file, observations = destination / "request.json", destination / "observations.json"
    binary = str(Path(shutil.which(str(sumo_binary)) or sumo_binary).resolve())
    write_json_atomic(request_file, {"inputs": inputs, "sumo_binary": binary})
    result = command_runner([sys.executable, "-m", __name__, str(request_file), str(observations)],
                            cwd=destination, timeout_seconds=timeout_seconds)
    try:
        observed = json.loads(observations.read_text(encoding="utf-8")) if observations.is_file() else {}
        if not isinstance(observed, dict) or not isinstance(observed.get("snapshots", {}), dict):
            raise ValueError("The observation record has an invalid structure.")
        if not isinstance(observed.get("frozen_inputs", []), list):
            raise ValueError("The frozen input inventory has an invalid structure.")
    except (OSError, ValueError) as error:
        observed = {"errors": {"worker_output": f"{type(error).__name__}: {error}"}}
    comparison = _compare(observed.get("snapshots", {}), set(mutable_junction_ids))
    def unchanged(record):
        try:
            return file_sha256(Path(record["path"])) == record["sha256"]
        except (OSError, KeyError, TypeError):
            return False

    originals_unchanged = all(unchanged(row) for row in inputs.values())
    frozen_unchanged = all(unchanged(row) for row in observed.get("frozen_inputs", []))
    if result.status != "pass" or not originals_unchanged or not frozen_unchanged:
        comparison["status"] = "unavailable"
    report = {"schema": "astra.loaded-link-preservation/v1", **comparison,
        "inputs": inputs, "inputs_unchanged": originals_unchanged, "frozen_inputs_unchanged": frozen_unchanged,
        "mutable_junction_ids": sorted(mutable_junction_ids),
        "command": result.to_dict(), "observation_errors": observed.get("errors", {}),
        "frozen_inputs": observed.get("frozen_inputs", []),
        "request": {"path": str(request_file), "sha256": file_sha256(request_file)},
        "observations": {"path": str(observations), "sha256": file_sha256(observations)} if observations.is_file() else None,
        "claim_boundary": "Loaded successors, via lanes, turn directions, yielding lanes, and internal conflicts only. No signal timing, geometry, demand, or field correctness claim."}
    report_file = destination / "link-preservation.json"
    write_json_atomic(report_file, report)
    return {**report, "report_file": str(report_file)}


def _main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("request", type=Path)
    parser.add_argument("observations", type=Path)
    args = parser.parse_args()
    request = json.loads(args.request.read_text(encoding="utf-8"))
    report, cache = {"snapshots": {}, "errors": {}, "frozen_inputs": []}, {}
    for name, record in request["inputs"].items():
        try:
            path = Path(record["path"])
            raw = path.read_bytes()
            if sha256(raw).hexdigest() != record["sha256"]:
                raise ValueError("The network changed before observation.")
            key = record["path"], record["sha256"]
            if key not in cache:
                frozen = args.observations.parent / f"input-{len(report['frozen_inputs']):03d}.net.xml"
                with frozen.open("xb") as stream:
                    stream.write(raw)
                report["frozen_inputs"].append({"path": str(frozen), "sha256": record["sha256"]})
                write_json_atomic(args.observations, report)
                cache[key] = {**_capture(frozen, request["sumo_binary"], name),
                    "input": report["frozen_inputs"][-1]}
            if file_sha256(path) != record["sha256"]:
                raise ValueError("The network changed during observation.")
            report["snapshots"][name] = cache[key]
        except Exception as error:
            report["errors"][name] = f"{type(error).__name__}: {error}"
        write_json_atomic(args.observations, report)


if __name__ == "__main__":
    _main()
