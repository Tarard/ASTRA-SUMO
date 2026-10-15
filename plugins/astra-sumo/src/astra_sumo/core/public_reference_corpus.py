"""Prepare public model targets for development without changing frozen human trials."""

from __future__ import annotations

from collections import Counter, defaultdict
from decimal import Decimal
import hashlib
from importlib.util import find_spec
from itertools import chain
import json
from pathlib import Path, PurePosixPath
import re
import shutil
import sys
import tempfile
from urllib.parse import quote
from urllib.request import urlopen
from xml.etree import ElementTree as ET

from .artifact_io import copy_file_atomic, write_json_atomic
from .candidate_contracts import file_sha256
from .command_runner import run_command
from .network_edit_operations import _sumo_binary
from .reference_road_alignment import audit_reference_road_alignment
from .sumo_commands import run_sumo_load_audit


def _identity(path):
    return {"path": str(path), "sha256": file_sha256(path)}


def _id(payload):
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()[:24]


def _catalog(path):
    document = json.loads(path.read_text(encoding="utf-8"))
    if document.get("schema") != "astra.public-network-catalog/v1" or not document.get("datasets"):
        raise ValueError("A nonempty public-network catalog is required")
    seen = set()
    for dataset in document["datasets"]:
        name = dataset["id"]
        if not re.fullmatch(r"[A-Za-z0-9_-]+", name) or name.lower() in seen:
            raise ValueError("Dataset IDs must be unique path components")
        seen.add(name.lower())
        if (not re.fullmatch(r"https://github.com/[\w.-]+/[\w.-]+", dataset["repository"])
                or not re.fullmatch(r"[a-f0-9]{40}", dataset["commit"])):
            raise ValueError("Public references require a GitHub repository and exact commit")
        roles = Counter(row["role"] for row in dataset["files"])
        if (roles["reference_net"] != 1 or roles["license"] != 1 or roles["source_osm"] > 1 or roles["source_net"] > 1
                or roles["build_config"] > 1 or roles["build_config"] and roles["source_osm"] != 1):
            raise ValueError("Each dataset needs one reference, one license, and at most one OSM input")
        paths = set()
        for row in dataset["files"]:
            revision = row.get("commit", dataset["commit"])
            if not re.fullmatch(r"[a-f0-9]{40}", revision):
                raise ValueError("Every artifact revision must be an exact commit")
            relative = PurePosixPath(row["path"])
            if (relative.is_absolute() or not relative.parts or str(relative) != row["path"] or any(p in {"..", "."} for p in relative.parts)
                    or "\\" in row["path"] or ":" in row["path"] or (revision, row["path"].lower()) in paths):
                raise ValueError("Artifact paths must be distinct relative paths inside their dataset")
            paths.add((revision, row["path"].lower()))
            if (not re.fullmatch(r"[a-f0-9]{64}", row["sha256"])
                    or type(row["bytes"]) is not int or row["bytes"] <= 0):
                raise ValueError("Artifacts require a SHA-256 and positive byte size")
    return document


def _prepared_import_arguments(files, osm):
    """Read supported import settings without executing upstream output or report paths."""
    configuration = next((path for row, path in files if row["role"] == "build_config"), None)
    if configuration is None:
        return [], None
    root = ET.parse(configuration).getroot()
    inputs = root.find("input")
    if root.tag != "configuration" or inputs is None or len(root.findall("input")) != 1:
        raise ValueError("Prepared imports require a netconvert configuration with declared inputs")
    if any(row.tag not in {"osm-files", "type-files"} or set(row.attrib) != {"value"} for row in inputs):
        raise ValueError("Prepared configurations support only catalog-bound OSM and type inputs")
    osm_rows = inputs.findall("osm-files")
    if len(osm_rows) != 1 or (configuration.parent / osm_rows[0].get("value", "")).resolve() != osm.resolve():
        raise ValueError("Prepared configuration OSM input differs from the catalog")
    type_rows = inputs.findall("type-files")
    if len(type_rows) > 1:
        raise ValueError("Prepared configurations require one type-file list")
    type_files = [(configuration.parent / value.strip()).resolve() for value in type_rows[0].get("value", "").split(",")] if type_rows else []
    declared_types = {path.resolve() for row, path in files if row["role"] == "type_map"}
    if set(type_files) != declared_types or len(set(type_files)) != len(type_files):
        raise ValueError("Prepared configuration type inputs differ from the catalog")
    allowed = {"tls.join", "tls.guess-signals", "tls.default-type", "tls.min-dur", "tls.max-dur", "ramps.guess",
               "geometry.remove", "roundabouts.guess", "junctions.join", "junctions.limit-turn-speed", "crossings.guess",
               "walkingareas", "osm.elevation", "no-turnarounds.except-deadend", "no-internal-links", "ignore-errors",
               "ignore-errors.connections", "show-errors.connections-first-try", "default.crossing-width"}
    arguments, seen = [], set()
    if type_files:
        arguments = ["--type-files", ",".join(map(str, type_files))]
    for section in root:
        if section.tag in {"input", "output", "report"}:
            continue
        if section.tag not in {"tls_building", "ramp_guessing", "processing", "building_defaults"}:
            raise ValueError(f"Unsupported prepared import section: {section.tag}")
        for option in section:
            if option.tag not in allowed or option.tag in seen or set(option.attrib) != {"value"} or list(option):
                raise ValueError(f"Unsupported prepared import setting: {option.tag}")
            seen.add(option.tag)
            if option.tag not in {"tls.join", "junctions.join", "geometry.remove"}:
                arguments.append(f"--{option.tag}={option.get('value')}")
    return arguments, configuration


def _cached_artifact(cache, dataset, record):
    folder = (cache / dataset["id"]).resolve()
    revision = record.get("commit", dataset["commit"])
    if revision != dataset["commit"]:
        folder = folder / revision[:12]
    target = (folder / record["path"]).resolve()
    if not folder.is_relative_to(cache.resolve()) or not target.is_relative_to(folder):
        raise ValueError("Artifact path escapes its cache directory")
    if target.exists():
        if target.stat().st_size != record["bytes"] or file_sha256(target) != record["sha256"]:
            raise ValueError(f"Cached artifact differs from the catalog. Preserve it and use a new cache: {target}")
        return target
    repo = dataset["repository"].removeprefix("https://github.com/")
    url = f"https://raw.githubusercontent.com/{repo}/{revision}/{quote(record['path'], safe='/')}"
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=target.parent) as temporary:
        incoming = Path(temporary) / "download"
        size = 0
        with urlopen(url, timeout=120) as response, incoming.open("wb") as output:
            while chunk := response.read(1024 * 1024):
                size += len(chunk)
                if size > record["bytes"]:
                    raise ValueError("Downloaded artifact exceeds its declared byte size")
                output.write(chunk)
        if size != record["bytes"] or file_sha256(incoming) != record["sha256"]:
            raise ValueError("Downloaded artifact does not match its frozen identity")
        copy_file_atomic(incoming, target)
    return target


def _members(junction_id):
    # shortcut: custom or abbreviated join IDs stay unresolved; add persisted member maps before scoring those joins.
    if re.fullmatch(r"-?\d+", junction_id):
        return [junction_id]
    if not junction_id.startswith("cluster_"):
        return None
    tokens = junction_id.replace("cluster_", "").split("_")
    if not tokens or any(not re.fullmatch(r"-?\d+", value) for value in tokens) or len(set(tokens)) != len(tokens):
        return None
    return sorted(tokens)


def _membership(root):
    inverse = defaultdict(set)
    for node in root.findall("junction"):
        if node.get("id", "").startswith(":") or node.get("type") == "internal":
            continue
        inverse[node.get("id")].add(node.get("id"))
        for member in _members(node.get("id", "")) or []:
            inverse[member].add(node.get("id"))
    return inverse


def _registered_nodes(source, reference):
    """Require exact declared frames and coordinates, or a complete joined-member trace."""
    def points(root):
        location = root.find("location")
        if location is None:
            return None, {}
        offset = [Decimal(value) for value in location.get("netOffset", "").split(",")]
        if len(offset) != 2:
            raise ValueError("Network offsets require two coordinates")
        positions = {row.get("id"): (Decimal(row.get("x")) - offset[0], Decimal(row.get("y")) - offset[1],
                                      *([Decimal(row.get("z"))] if row.get("z") is not None else []))
                     for row in root.findall("junction") if row.get("x") is not None and row.get("y") is not None}
        return location.get("projParameter"), positions

    source_frame, old_points = points(source)
    reference_frame, new_points = points(reference)
    if source_frame is None or source_frame != reference_frame:
        return {}
    inverse = _membership(reference)
    old_groups = {row.get("id"): _members(row.get("id", "")) or [row.get("id")]
                  for row in source.findall("junction") if not row.get("id", "").startswith(":")}
    reference_groups = {row.get("id"): set(_members(row.get("id", "")) or [row.get("id")])
                        for row in reference.findall("junction")}
    mapping = {}
    for name, members in old_groups.items():
        owners = [inverse.get(member, set()) for member in members]
        if not all(len(owner) == 1 for owner in owners) or len(set().union(*owners)) != 1:
            continue
        target = next(iter(owners[0]))
        if name == target:
            if name in old_points and target in new_points and old_points[name] == new_points[target]:
                mapping[name] = target
            continue
        wanted = reference_groups[target]
        group = [key for key, group_members in old_groups.items() if set(group_members) <= wanted]
        complete = set().union(*(set(old_groups[key]) for key in group)) == wanted
        positions = [old_points[key] for key in group if key in old_points]
        if (complete and len(positions) == len(group) and positions and target in new_points
                and all(len(point) == len(new_points[target]) for point in positions) and all(
            min(point[axis] for point in positions) <= new_points[target][axis] <= max(point[axis] for point in positions)
            for axis in range(len(new_points[target])))):
            mapping[name] = target
    return mapping


def _targets(root, dataset, reference_sha):
    edges = {row.get("id"): row for row in root.findall("edge") if row.get("function", "normal") == "normal"}
    incoming, outgoing, connections = defaultdict(list), defaultdict(list), defaultdict(list)
    for name, edge in edges.items():
        incoming[edge.get("to")].append(name)
        outgoing[edge.get("from")].append(name)
    for connection in root.findall("connection"):
        edge = edges.get(connection.get("from"))
        if edge is not None:
            connections[edge.get("to")].append(dict(connection.attrib))
    for node in sorted(root.findall("junction"), key=lambda row: row.get("id", "")):
        name = node.get("id", "")
        if name.startswith(":") or node.get("type") == "internal":
            continue
        members = _members(name)
        yield {"case_id": _id([dataset["id"], reference_sha, name]), "dataset_id": dataset["id"],
               "family_id": dataset["family_id"], "split": "development_only", "junction_id": name,
               "source_node_ids": members, "source_membership": "resolved_from_model_id" if members else "unresolved",
               "observed_model_state": "joined" if members and len(members) > 1 else "single" if members else "unknown",
               "action_origin": "unresolved_manual_or_automatic", "field_defect_label": None,
               "target": {"junction": dict(node.attrib), "incoming_edge_ids": sorted(incoming[name]),
                          "outgoing_edge_ids": sorted(outgoing[name]), "ordinary_connections": connections[name]}}


def _pair_labels(raw, reference, dataset, binding):
    mapping = _registered_nodes(raw, reference)
    pairs = {tuple(sorted((edge.get("from"), edge.get("to")))) for edge in raw.findall("edge")
             if edge.get("function", "normal") == "normal" and edge.get("from") and edge.get("to")
             and edge.get("from") != edge.get("to")}
    for pair in sorted(pairs):
        parents = [mapping.get(node) for node in pair]
        known = all(parents)
        ids = sorted(set(parents) - {None})
        yield {"case_id": _id([dataset["id"], binding, pair]), "dataset_id": dataset["id"],
               "kind": "junction_membership",
               "source_node_ids": list(pair), "reference_junction_ids": ids,
               "source_member_groups": [_members(node) or [node] for node in pair],
               "expected_join": parents[0] == parents[1] if known else None,
               "status": "model_target" if known else "unknown_correspondence",
               "label_scope": "published_model_membership_only", "action_origin": "unresolved_manual_or_automatic",
               "field_defect_label": None, "family_id": dataset["family_id"], "split": "development_only"}


def _connections(root):
    edges = {row.get("id"): row for row in root.findall("edge") if row.get("function", "normal") == "normal"}
    lanes = {(edge_id, lane.get("index", str(index))): lane.get("id") for edge_id, edge in edges.items()
             for index, lane in enumerate(edge.findall("lane"))}
    connections = {tuple(row.get(key) for key in ("from", "fromLane", "to", "toLane"))
                   for row in root.findall("connection") if row.get("from") in edges and row.get("to") in edges}
    return edges, lanes, connections


def _connection_labels(source, reference, dataset, binding):
    mapping = _registered_nodes(source, reference)
    old_edges, old_lanes, old = _connections(source)
    edges, lanes, current = _connections(reference)
    registered = {key for key in old_edges.keys() & edges.keys() if all(
        mapping.get(old_edges[key].get(end)) == edges[key].get(end) for end in ("from", "to"))}
    for pair in sorted(old | current):
        known = all(edge in registered and (edge, lane) in old_lanes and
                    old_lanes[(edge, lane)] == lanes.get((edge, lane))
                    for edge, lane in (pair[:2], pair[2:]))
        yield {"case_id": _id([dataset["id"], binding, "connection", pair]), "dataset_id": dataset["id"],
               "kind": "lane_connection", "connection": list(pair), "before_present": pair in old,
               "expected_present": pair in current if known else None,
               "change": "added" if pair not in old else "removed" if pair not in current else "retained",
               "status": "model_target" if known else "unknown_correspondence",
               "label_scope": "published_model_direct_connection_only", "action_origin": "observed_model_difference_no_manual_attribution",
               "field_defect_label": None, "family_id": dataset["family_id"], "split": "development_only"}


def _jsonl(path, rows):
    counts = Counter()
    with path.open("x", encoding="utf-8", newline="\n") as output:
        for row in rows:
            output.write(json.dumps(row, sort_keys=True, ensure_ascii=False) + "\n")
            counts["total"] += 1
            if row.get("kind") == "lane_connection":
                key = "connection_" + row["change"] if row["expected_present"] is not None else "connection_unknown"
            else:
                key = "joined" if row.get("expected_join") is True else "separate" if row.get("expected_join") is False else "unknown"
            counts[key] += 1
    return dict(counts)


def build_public_reference_corpus(*, catalog_file, cache_dir, output_dir, netconvert_binary="netconvert"):
    """Freeze model targets and prepared-source comparisons for ordinary development."""
    catalog_path, cache, destination = Path(catalog_file).resolve(strict=True), Path(cache_dir).resolve(), Path(output_dir).resolve()
    catalog = _catalog(catalog_path)
    catalog_sha = file_sha256(catalog_path)
    if (destination.exists() or catalog_path.is_relative_to(destination)
            or cache.is_relative_to(destination) or destination.is_relative_to(cache)):
        raise ValueError("Use a new corpus output directory separate from the catalog and cache")
    destination.mkdir(parents=True)
    catalog_snapshot = destination / "catalog.json"
    copy_file_atomic(catalog_path, catalog_snapshot)
    if netconvert_binary == "netconvert":
        sibling = Path(sys.executable).with_name("netconvert.exe" if sys.platform == "win32" else "netconvert")
        if sibling.is_file():
            netconvert_binary = str(sibling)
    sumo_binary = _sumo_binary(netconvert_binary)
    report = {"schema": "astra.public-reference-corpus/v1", "status": "pass", "datasets": [],
              "catalog": _identity(catalog_path), "split": "development_only", "automatic_promotion_gate": "blocked",
              "formal_human_trial": "not_modified", "field_precision": None,
              "catalog_snapshot": _identity(catalog_snapshot),
              "limitations": ["Published model targets are not field defect labels.",
                              "OSM-like files may already include manual and synthetic changes.",
                              "Current SUMO imports differ from the upstream historical toolchain.",
                              "Missing source dates remain unknown. No current OSM is substituted.",
                              "Ingolstadt sources share a city family and are not independent holdouts."]}
    report["toolchain"] = {name: {"path": str(path), "sha256": file_sha256(path) if path.is_file() else None}
        for name, path in (("netconvert", Path(shutil.which(netconvert_binary) or netconvert_binary).resolve()),
                           ("sumo", Path(sumo_binary).resolve()), ("python", Path(sys.executable).resolve()))}
    report["toolchain"]["python"]["version"] = sys.version
    for name in ("netconvert", "sumo"):
        program = report["toolchain"][name]
        if program["sha256"] is not None:
            program["version_result"] = run_command([program["path"], "--version"], timeout_seconds=30).to_dict()
    package = find_spec("sumo")
    if package is not None and package.origin:
        backend = Path(package.origin).parent / "bin"
        report["toolchain"]["installed_sumo_package_binaries"] = [_identity(path) for name in ("sumo", "netconvert")
            if (path := backend / (name + (".exe" if sys.platform == "win32" else ""))).is_file()]
    manifest_artifacts = [_identity(catalog_snapshot)]
    for dataset in catalog["datasets"]:
        folder = destination / dataset["id"]
        folder.mkdir()
        files = [(row, _cached_artifact(cache, dataset, row)) for row in dataset["files"]]
        net = next(path for row, path in files if row["role"] == "reference_net")
        osm = next((path for row, path in files if row["role"] == "source_osm"), None)
        source_net = next((path for row, path in files if row["role"] == "source_net"), None)
        root = ET.parse(net).getroot()
        if root.tag != "net":
            raise ValueError("Public reference artifact is not a SUMO network")
        target_file = folder / "model-targets.jsonl"
        target_counts = _jsonl(target_file, _targets(root, dataset, file_sha256(net)))
        load = run_sumo_load_audit(net_file=net, output_dir=folder / "reference-load", sumo_binary=sumo_binary)
        row = {"schema": "astra.public-reference-dataset/v1", "dataset_id": dataset["id"], "family_id": dataset["family_id"], "license": dataset["license"],
               "status": "pass" if load["status"] == "pass" else "review_required", "source_relation": dataset["source_relation"],
               "osm_snapshot_date": dataset["osm_snapshot_date"], "field_validity_date": dataset["field_validity_date"],
               "reference_net": _identity(net), "model_targets": {**_identity(target_file), "count": target_counts["total"]},
               "reference_load": load, "limitations": dataset["limitations"], "paired_labels": {"count": 0},
               "source_revision_note": dataset.get("source_revision_note", ""),
               "upstream_files": [{**record, "local_path": str(path)} for record, path in files]}
        comparison_root = None
        if osm is not None:
            raw = folder / "unjoined-import.net.xml"
            settings, configuration = _prepared_import_arguments(files, osm)
            command = [netconvert_binary, "--osm-files", str(osm), *settings, "--junctions.join", "false", "--tls.join", "false",
                       "--geometry.remove", "false", "--offset.disable-normalization", "true", "--output-file", str(raw)]
            built = run_command(command, cwd=folder, timeout_seconds=240).to_dict()
            write_json_atomic(folder / "import-command.json", {"source_osm": _identity(osm), "result": built}, sort_keys=True)
            row["source_osm"] = _identity(osm)
            if configuration is not None:
                row["import_configuration"] = {**_identity(configuration), "applied_arguments": settings,
                    "overrides": {"junctions.join": False, "tls.join": False, "geometry.remove": False},
                    "ignored_sections": ["output", "report"], "toolchain_relation": "current_sumo_with_published_import_settings"}
            row["import_status"] = built["status"]
            if built["status"] == "pass" and raw.is_file():
                row["raw_net"] = _identity(raw)
                if dataset["source_relation"] == "upstream_prepared_build_input":
                    alignment = audit_reference_road_alignment(net, raw, source_osm_file=osm, output_dir=folder / "alignment")
                    row["alignment"] = alignment
                    comparison_root = ET.parse(raw).getroot()
                    row["comparison_source"] = {**_identity(raw), "kind": "prepared_osm_import"}
                else:
                    row["pairing_reason"] = "prepared_source_relation_unverified_no_labels_inferred"
            else:
                row["status"] = "review_required"
                row["pairing_reason"] = "source_import_failed"
        else:
            row["pairing_reason"] = "original_osm_not_published_in_this_snapshot"
        if source_net is not None:
            comparison_root = ET.parse(source_net).getroot()
            row["comparison_source"] = {**_identity(source_net), "kind": "published_model_revision"}
            row["comparison_source_load"] = run_sumo_load_audit(net_file=source_net,
                output_dir=folder / "comparison-source-load", sumo_binary=sumo_binary)
            if row["comparison_source_load"]["status"] != "pass":
                row["status"] = "review_required"
        if comparison_root is not None:
            label_file = folder / "model-labels.jsonl"
            binding = [file_sha256(net), row["comparison_source"]["sha256"]]
            counts = _jsonl(label_file, chain(_pair_labels(comparison_root, root, dataset, binding),
                                             _connection_labels(comparison_root, root, dataset, binding)))
            row["paired_labels"] = {**_identity(label_file), "count": counts.get("total", 0), "counts": counts,
                                    "evidence_scope": row["comparison_source"]["kind"], "field_temporal_match": "unverified",
                                    "manual_edit_origin": "unestablished"}
        dataset_report = folder / "dataset.json"
        write_json_atomic(dataset_report, row, sort_keys=True)
        manifest_artifacts.extend(_identity(path) for path in sorted(folder.rglob("*")) if path.is_file())
        report["datasets"].append({"id": dataset["id"], "status": row["status"], "report": _identity(dataset_report),
                                   "model_target_count": target_counts["total"], "paired_labels": row["paired_labels"]})
        if any(file_sha256(path) != record["sha256"] for record, path in files):
            raise ValueError("A public source artifact changed during corpus construction")
        if row["status"] != "pass":
            report["status"] = "review_required"
    if file_sha256(catalog_path) != catalog_sha:
        raise ValueError("The catalog changed during corpus construction")
    report_file = destination / "corpus.json"
    write_json_atomic(report_file, report, sort_keys=True)
    manifest_artifacts.append(_identity(report_file))
    write_json_atomic(destination / "manifest.json", {"schema": "astra.public-reference-manifest/v1",
        "catalog": report["catalog"], "artifacts": manifest_artifacts}, sort_keys=True)
    return {**report, "report_file": str(report_file), "manifest_file": str(destination / "manifest.json")}


def compare_public_reference(*, dataset_file, request_file, output_file):
    """Compare a candidate's node grouping with model-relative labels, retaining unknowns."""
    dataset_path, request_path = Path(dataset_file).resolve(strict=True), Path(request_file).resolve(strict=True)
    dataset, request = [json.loads(path.read_text(encoding="utf-8")) for path in (dataset_path, request_path)]
    if (dataset.get("schema") != "astra.public-reference-dataset/v1"
            or request.get("schema") != "astra.public-reference-request/v1"
            or request.get("dataset_sha256") != file_sha256(dataset_path)):
        raise ValueError("Comparison requires an exact dataset identity and request schema")
    labels = dataset["paired_labels"]
    if not labels.get("path"):
        raise ValueError("This dataset has no prepared-input model labels")
    label_path = Path(labels["path"]).resolve(strict=True)
    candidate = Path(request["candidate_net_file"]).resolve(strict=True)
    if (file_sha256(label_path) != labels["sha256"] or file_sha256(candidate) != request["candidate_sha256"]
            or request["comparison_source_sha256"] != dataset["comparison_source"]["sha256"]):
        raise ValueError("Comparison input identities do not match")
    output = Path(output_file).resolve()
    source_inputs = [Path(dataset[key]["path"]).resolve(strict=True) for key in ("reference_net", "comparison_source")]
    for key, path in zip(("reference_net", "comparison_source"), source_inputs):
        if file_sha256(path) != dataset[key]["sha256"]:
            raise ValueError("Prepared-source or reference identity changed")
    inputs = [dataset_path, request_path, label_path, candidate, *source_inputs]
    if output.exists() or output in inputs:
        raise ValueError("Use a new comparison output file separate from every input")
    hashes = {path: file_sha256(path) for path in inputs}
    candidate_root = ET.parse(candidate).getroot()
    source_root = ET.parse(dataset["comparison_source"]["path"]).getroot()
    reference_root = ET.parse(dataset["reference_net"]["path"]).getroot()
    mapping = _registered_nodes(source_root, candidate_root)
    source_edges, source_lanes, _ = _connections(source_root)
    edges, lanes, connections = _connections(candidate_root)
    reference_edges, _, reference_connections = _connections(reference_root)
    registered_edges = {key for key in source_edges.keys() & edges.keys() if all(
        mapping.get(source_edges[key].get(end)) == edges[key].get(end) for end in ("from", "to"))}
    counts, change_counts, cases = defaultdict(Counter), defaultdict(Counter), []
    labelled_connections = set()
    for text in label_path.read_text(encoding="utf-8").splitlines():
        case = json.loads(text)
        if case["kind"] == "junction_membership":
            expected = case["expected_join"]
            parents = [mapping.get(node) for node in case["source_node_ids"]]
            predicted = parents[0] == parents[1] if all(parents) else None
        else:
            expected = case["expected_present"]
            pair = tuple(case["connection"])
            labelled_connections.add(pair)
            predicted = pair in connections if all(edge in registered_edges and (edge, lane) in lanes and
                lanes[(edge, lane)] == source_lanes.get((edge, lane)) for edge, lane in (pair[:2], pair[2:])) else None
        known = expected is not None
        if not known:
            outcome = "unknown_reference"
        elif predicted is None:
            outcome = "unresolved_candidate"
        else:
            outcome = ("true_positive" if predicted else "false_negative") if expected else (
                "false_positive" if predicted else "true_negative")
        counts[case["kind"]][outcome] += 1
        if case["kind"] == "lane_connection":
            change_counts[case["change"]][outcome] += 1
        cases.append({"case_id": case["case_id"], "kind": case["kind"], "expected": expected,
                      "predicted": predicted, "outcome": outcome, "reference_change": case.get("change")})
    if len(cases) != labels["count"] or len({c["case_id"] for c in cases}) != len(cases):
        raise ValueError("Model label count or identity is inconsistent")
    totals = Counter(case["outcome"] for case in cases)
    additional_connections = sorted(connections - reference_connections - labelled_connections)
    new_edges = sorted(edges.keys() - source_edges.keys() - reference_edges.keys())
    passed = bool(cases) and not any(totals[key] for key in (
        "unknown_reference", "unresolved_candidate", "false_positive", "false_negative")) and not additional_connections and not new_edges
    report = {"schema": "astra.public-reference-comparison/v1", "status": "pass" if passed else "review_required",
              "label_scope": "published_model_structure_only", "field_precision": None, "automatic_promotion_gate": "blocked",
              "dataset_id": dataset["dataset_id"], "family_id": dataset["family_id"], "split": "development_only",
              "counts": {key: dict(value) for key, value in counts.items()}, "total_case_count": len(cases), "cases": cases,
              "connection_change_counts": {key: dict(value) for key, value in change_counts.items()},
              "known_reference_case_count": len(cases) - totals["unknown_reference"],
              "scored_case_count": len(cases) - totals["unknown_reference"] - totals["unresolved_candidate"],
              "additional_candidate_connections": [list(pair) for pair in additional_connections],
              "unscored_new_edge_ids": new_edges,
              "candidate_source_claim": "caller_supplied_hash_bound_source_identity",
              "inputs": [_identity(path) for path in inputs]}
    if any(file_sha256(path) != digest for path, digest in hashes.items()):
        raise ValueError("Comparison input changed during evaluation")
    write_json_atomic(output, report, sort_keys=True)
    return {**report, "report_file": str(output)}
