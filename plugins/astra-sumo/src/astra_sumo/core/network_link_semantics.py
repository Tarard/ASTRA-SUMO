"""Measure loaded successors and conflict relations without driving vehicles."""

import argparse
from collections import defaultdict
from collections.abc import Mapping
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
        controls = defaultdict(list)
        for tls in connection.trafficlight.getIDList():
            for index, links in enumerate(connection.trafficlight.getControlledLinks(tls)):
                for incoming, outgoing, via in links:
                    controls[incoming, outgoing, via].append([tls, index])
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
                    row = {"successor": link[0], "via": link[4], "direction": link[6],
                           "controls": sorted(controls[lane, link[0], link[4]])}
                    if lane in incoming:
                        row["yield_to"] = sorted(connection.lane.getFoes(lane, link[0]))
                    records.append(row)
                links[lane] = sorted(records, key=lambda row: (row["successor"], row["via"]))
            nodes[identity] = {"lane_links": links,
                "internal_foes": {lane: sorted(connection.lane.getInternalFoes(lane)) for lane in sorted(internal[identity])}}
        return {"native_version": connection.getVersion(), "command": command, "junctions": nodes}
    finally:
        connection.close()


def _remap_lanes(record, mapping):
    if record is None or not mapping:
        return record
    links = {}
    for lane, rows in record["lane_links"].items():
        renamed = [{**row, "successor": mapping.get(row["successor"], row["successor"]),
                    "via": mapping.get(row["via"], row["via"]),
                    **({"yield_to": sorted(mapping.get(item, item) for item in row["yield_to"])} if "yield_to" in row else {})}
                   for row in rows]
        links[mapping.get(lane, lane)] = sorted(renamed, key=lambda row: (row["successor"], row["via"]))
    return {"lane_links": links, "internal_foes": {mapping.get(lane, lane): sorted(mapping.get(item, item) for item in foes)
        for lane, foes in record["internal_foes"].items()}}


def _boundary_lane_mapping(source, candidate, edge_map):
    mapping, errors = {}, []
    old_edges = {row.get("id"): row for row in source.findall("edge")}
    new_edges = {row.get("id"): row for row in candidate.findall("edge")}
    if len(set(edge_map.values())) != len(edge_map):
        return {}, ["Boundary edge destinations must be unique."]
    for old, new in edge_map.items():
        if old not in old_edges or new not in new_edges:
            errors.append(f"Boundary edge correspondence is missing: {old} -> {new}")
            continue
        before = {row.get("index"): row.get("id") for row in old_edges[old].findall("lane")}
        after = {row.get("index"): row.get("id") for row in new_edges[new].findall("lane")}
        if (None in before or None in after or set(before) != set(after)
                or len(before) != len(old_edges[old].findall("lane"))
                or len(after) != len(new_edges[new].findall("lane"))):
            errors.append(f"Boundary lane indices differ: {old} -> {new}")
            continue
        mapping.update({lane: after[index] for index, lane in before.items()})
    return mapping, errors


def _compare(snapshots, mutable_junction_ids, references=None, lane_mapping=None):
    differences, counts = [], {"native": 0, "source": 0}
    unresolved = []
    def junctions(name):
        value = snapshots.get(name)
        return value.get("junctions") if isinstance(value, dict) and isinstance(value.get("junctions"), dict) else None

    candidate = junctions("candidate")
    if references is None:
        roles = {"native": mutable_junction_ids}
    else:
        roles = defaultdict(set)
        for node, role in references.items():
            roles[role].add(node)
        if candidate is not None:
            unresolved.extend(f"native:{node}" for node in sorted((set(candidate) & mutable_junction_ids) - references.keys()))
    for name in ("source", *roles):
        reference = junctions(name)
        if reference is None or candidate is None:
            unresolved.append(name)
            continue
        nodes = (set(reference) | set(candidate)) & roles[name] if name != "source" else (
            set(reference) | set(candidate)) - mutable_junction_ids
        for node in sorted(nodes):
            if name != "source" and node not in reference:
                unresolved.append(f"{name}:{node}")
                continue
            counts["source" if name == "source" else "native"] += 1
            expected = _remap_lanes(reference.get(node), lane_mapping) if name == "source" else reference.get(node)
            if expected != candidate.get(node):
                differences.append({"junction_id": node, "reference": name,
                    "expected": expected, "actual": candidate.get(node)})
    return {"status": "unavailable" if unresolved else "fail" if differences else "pass",
        "compared_junction_counts": counts, "unresolved_references": unresolved, "differences": differences}


def compare_loaded_link_semantics(*, source_net_file, native_net_file, candidate_net_file,
                                  mutable_junction_ids, output_dir, sumo_binary="sumo",
                                  timeout_seconds=120, command_runner=run_command, boundary_edge_map=None):
    """Use one native file or a junction-to-artifact mapping. Retain the source elsewhere."""
    destination = Path(output_dir).resolve()
    mutable_junction_ids = set(mutable_junction_ids)
    destination.mkdir(parents=True, exist_ok=False)
    files = {"source": source_net_file, "candidate": candidate_net_file}
    declared_hashes = {}
    if isinstance(native_net_file, Mapping):
        paths, references = {}, {}
        for node, value in sorted(native_net_file.items()):
            path = value["path"] if isinstance(value, Mapping) else value
            resolved = Path(path).resolve()
            role = paths.setdefault(resolved, f"native-{len(paths)}")
            files[role] = resolved
            references[node] = role
            if isinstance(value, Mapping):
                declared_hashes[node] = value.get("sha256")
    else:
        files["native"] = native_net_file
        references = {node: "native" for node in mutable_junction_ids}
    inputs = {name: {"path": str(Path(path).resolve()),
        "sha256": file_sha256(Path(path)) if Path(path).is_file() else None} for name, path in files.items()}
    hash_errors = [{"junction_id": node, "declared_sha256": digest, "actual_sha256": inputs[references[node]]["sha256"]}
        for node, digest in declared_hashes.items() if digest is None or digest != inputs[references[node]]["sha256"]]
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
    lane_mapping, mapping_errors = {}, []
    if boundary_edge_map:
        try:
            roots = [ET.parse(observed["snapshots"][name]["input"]["path"]).getroot() for name in ("source", "candidate")]
            lane_mapping, mapping_errors = _boundary_lane_mapping(*roots, boundary_edge_map)
        except (OSError, KeyError, TypeError, ET.ParseError) as error:
            mapping_errors.append(f"Boundary correspondence cannot be measured: {error}")
    comparison = _compare(observed.get("snapshots", {}), mutable_junction_ids, references, lane_mapping)
    scope_errors = sorted(set(references) - mutable_junction_ids)
    def unchanged(record):
        try:
            return file_sha256(Path(record["path"])) == record["sha256"]
        except (OSError, KeyError, TypeError):
            return False

    originals_unchanged = all(unchanged(row) for row in inputs.values())
    frozen_unchanged = all(unchanged(row) for row in observed.get("frozen_inputs", []))
    if result.status != "pass" or not originals_unchanged or not frozen_unchanged or mapping_errors or scope_errors or hash_errors:
        comparison["status"] = "unavailable"
    report = {"schema": "astra.loaded-link-preservation/v1", **comparison,
        "inputs": inputs, "inputs_unchanged": originals_unchanged, "frozen_inputs_unchanged": frozen_unchanged,
        "mutable_junction_ids": sorted(mutable_junction_ids),
        "native_junction_references": references, "reference_scope_errors": scope_errors,
        "declared_native_hashes": declared_hashes, "native_hash_errors": hash_errors,
        "boundary_edge_map": boundary_edge_map or {}, "boundary_lane_mapping": lane_mapping,
        "boundary_mapping_errors": mapping_errors,
        "command": result.to_dict(), "observation_errors": observed.get("errors", {}),
        "frozen_inputs": observed.get("frozen_inputs", []),
        "request": {"path": str(request_file), "sha256": file_sha256(request_file)},
        "observations": {"path": str(observations), "sha256": file_sha256(observations)} if observations.is_file() else None,
        "claim_boundary": "Loaded successors, via lanes, turn directions, signal assignments, yielding lanes, and internal conflicts only. No signal timing, geometry, demand, or field correctness claim."}
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
