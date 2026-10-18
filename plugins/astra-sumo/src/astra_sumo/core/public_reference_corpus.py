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


def _prepared_import_arguments(files, osm, *, unjoined=True):
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
               "ignore-errors.connections", "show-errors.connections-first-try", "default.crossing-width",
               "precision", "proj.utm", "junctions.join-turns"}
    ignored_output_options = {"output-file", "output-prefix", "log", "error-log", "message-log", "verbose"}
    arguments, seen = [], set()
    if type_files:
        arguments = ["--type-files", ",".join(map(str, type_files))]
    for section in root:
        if section.tag == "input":
            continue
        if section.attrib:
            raise ValueError(f"Unsupported prepared import section: {section.tag}")
        for option in section:
            if option.tag not in allowed | ignored_output_options or option.tag in seen or set(option.attrib) != {"value"} or list(option):
                raise ValueError(f"Unsupported prepared import setting: {option.tag}")
            seen.add(option.tag)
            if option.tag in ignored_output_options:
                continue
            if not unjoined or option.tag not in {"tls.join", "junctions.join", "geometry.remove"}:
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


def _candidate_predictions(source, candidate, cases):
    mapping = _registered_nodes(source, candidate)
    source_edges, source_lanes, _ = _connections(source)
    edges, lanes, connections = _connections(candidate)
    registered = {key for key in source_edges.keys() & edges.keys() if all(
        mapping.get(source_edges[key].get(end)) == edges[key].get(end) for end in ("from", "to"))}
    for case in cases:
        if case["kind"] == "junction_membership":
            parents = [mapping.get(node) for node in case["source_node_ids"]]
            prediction = parents[0] == parents[1] if all(parents) else None
        else:
            pair = tuple(case["connection"])
            prediction = pair in connections if all(edge in registered and (edge, lane) in lanes and
                lanes[(edge, lane)] == source_lanes.get((edge, lane)) for edge, lane in (pair[:2], pair[2:])) else None
        yield case, prediction


def _controlled_predictions(source, variant, cases):
    """Trace IDs only inside the same-input, normalization-only native experiment."""
    original_edges, original_lanes, _ = _connections(source)
    edges, lanes, connections = _connections(variant)
    owners = _membership(variant)
    for case in cases:
        if case["kind"] == "junction_membership":
            parents = [owners.get(node, set()) for node in case["source_node_ids"]]
            value = parents[0] == parents[1] if all(len(parent) == 1 for parent in parents) else None
        else:
            pair = tuple(case["connection"])
            comparable = all(edge in original_edges and edge in edges and (edge, lane) in original_lanes
                and original_lanes[(edge, lane)] == lanes.get((edge, lane))
                and all(original_edges[edge].get(end) == edges[edge].get(end) for end in ("from", "to"))
                for edge, lane in (pair[:2], pair[2:]))
            value = pair in connections if comparable else None
        yield case, value


def _case_selector(case):
    return case["kind"], tuple(case["connection"] if case["kind"] == "lane_connection" else case["source_node_ids"])


def _coordinate_frame_control(*, command, source_file, reference_file, label_file, dataset, folder, sumo_binary):
    """Report generator state changes separately from exact registration changes."""
    alternate = folder / "alternate-coordinate-import.net.xml"
    variant_command = list(command)
    index = variant_command.index("--offset.disable-normalization") + 1
    variant_command[index] = "false" if command[index] == "true" else "true"
    variant_command[-1] = str(alternate)
    built = run_command(variant_command, cwd=folder, timeout_seconds=240).to_dict()
    report = {"status": "unavailable", "primary_command": command, "alternate_command": variant_command,
        "result": built, "primary_network": _identity(source_file), "reference_network": _identity(reference_file),
        "primary_labels": _identity(label_file), "human_cleaning_evaluation": False,
        "claim_boundary": "Same input and recipe with normalization toggled. ID-only tracing covers node membership and direct connection presence in these controlled imports only. Registration loss is not a changed generated value. Geometry, driving equivalence and field validity are not verified."}
    try:
        if built["status"] == "pass" and alternate.is_file():
            source, variant, reference = [ET.parse(path).getroot() for path in (source_file, alternate, reference_file)]
            primary = {_case_selector(row): row for line in label_file.read_text(encoding="utf-8").splitlines()
                       if (row := json.loads(line))}
            binding = [file_sha256(reference_file), file_sha256(alternate)]
            other = {_case_selector(row): row for row in chain(_pair_labels(variant, reference, dataset, binding),
                                                             _connection_labels(variant, reference, dataset, binding))}
            cases = [primary.get(key, other.get(key)) for key in sorted(primary.keys() | other.keys())]
            before = {_case_selector(row): value for row, value in _controlled_predictions(source, source, cases)}
            counts, registration_counts, rows = Counter(), Counter(), []
            sensitive_known = 0
            def expected(row):
                return (row.get("expected_present") if row["kind"] == "lane_connection" else row.get("expected_join")) if row else None
            for case, value in _controlled_predictions(source, variant, cases):
                key = _case_selector(case)
                old, new = primary.get(key), other.get(key)
                a, b = expected(old), expected(new)
                registration = ("shared" if a is not None and b is not None else "primary_only" if a is not None else
                                "alternate_only" if b is not None else "unresolved")
                state = "unresolved" if before[key] is None or value is None else "unchanged" if before[key] == value else "changed"
                counts[state] += 1
                registration_counts[registration] += 1
                sensitive_known += state == "changed" and a is not None
                rows.append({"primary_case_id": old["case_id"] if old else None, "kind": key[0], "selector": list(key[1]),
                    "primary_present_in_table": old is not None, "alternate_present_in_table": new is not None,
                    "primary_registered": a is not None, "alternate_registered": b is not None,
                    "primary_expected": a, "alternate_expected": b, "registration_relation": registration,
                    "primary_generated_value": before[key], "alternate_generated_value": value, "source_state_relation": state})
            provenance = folder / "coordinate-frame-labels.jsonl"
            with provenance.open("x", encoding="utf-8", newline="\n") as stream:
                for row in rows:
                    stream.write(json.dumps(row, sort_keys=True) + "\n")
            load = run_sumo_load_audit(net_file=alternate, output_dir=folder / "coordinate-frame-load", sumo_binary=sumo_binary)
            raw_connections = [{tuple(row.get(key) for key in ("from", "fromLane", "to", "toLane"))
                                for row in root.findall("connection")} for root in (source, variant)]
            report.update(status="measured" if load["status"] == "pass" else "unavailable", alternate_network=_identity(alternate),
                label_provenance=_identity(provenance), primary_case_count=len(primary), compared_selector_count=len(rows),
                source_state_counts=dict(counts), registration_counts=dict(registration_counts),
                sensitive_known_reference_count=sensitive_known, sumo_load=load,
                raw_connection_identity_differences={"primary_only": [list(row) for row in sorted(raw_connections[0] - raw_connections[1])],
                    "alternate_only": [list(row) for row in sorted(raw_connections[1] - raw_connections[0])],
                    "claim_boundary": "Exact saved connection IDs, including internal and pedestrian objects. Generated names may change; these are not independent field defects."})
    except (OSError, ValueError, ET.ParseError) as error:
        report.update(status="unavailable", error=f"{type(error).__name__}: {error}")
    output = folder / "coordinate-frame-control.json"
    write_json_atomic(output, report, sort_keys=True)
    return {**report, "report": _identity(output)}


def _source_sensitivity(dataset, cases):
    labels = dataset["paired_labels"]
    control = labels.get("coordinate_frame_control")
    if control is None:
        return {"status": "not_measured"}, {}
    if not isinstance(control, dict) or control.get("status") != "measured":
        return {"status": "unavailable", "reason": "The coordinate-frame control did not complete."}, {}
    try:
        record = control["label_provenance"]
        raw = Path(record["path"]).read_bytes()
        if (hashlib.sha256(raw).hexdigest() != record["sha256"] or control["primary_labels"]["sha256"] != labels["sha256"]
                or control["primary_network"]["sha256"] != dataset["comparison_source"]["sha256"]
                or control["reference_network"]["sha256"] != dataset["reference_net"]["sha256"]
                or file_sha256(Path(control["alternate_network"]["path"])) != control["alternate_network"]["sha256"]):
            raise ValueError("Coordinate-frame evidence does not match the primary source and labels.")
        rows = [json.loads(line) for line in raw.splitlines()]
        states = {row["primary_case_id"]: row["source_state_relation"] for row in rows if row["primary_case_id"] is not None}
        if (set(states) != {case["case_id"] for case in cases} or len(states) != sum(row["primary_case_id"] is not None for row in rows)
                or any(value not in {"changed", "unchanged", "unresolved"} for value in states.values())):
            raise ValueError("Coordinate-frame evidence has an invalid case inventory or state.")
        return {"status": "measured", "label_provenance": record, "source_state_counts": dict(Counter(states.values())),
            "unscored_changed_selector_count": sum(row["primary_case_id"] is None and row["source_state_relation"] == "changed" for row in rows),
            "claim_boundary": "Node membership and direct connection presence under a normalization-only control. Registration-only changes do not imply changed source values. Geometry, field accuracy and driving equivalence are not verified."}, states
    except (OSError, KeyError, TypeError, ValueError) as error:
        return {"status": "unavailable", "error": f"{type(error).__name__}: {error}"}, {}


def _generator_control(*, files, osm, source, label_file, folder, netconvert_binary, sumo_binary):
    settings, configuration = _prepared_import_arguments(files, osm, unjoined=False)
    output = folder / "published-settings-control.net.xml"
    command = [netconvert_binary, "--osm-files", str(osm), *settings, "--output-file", str(output)]
    built = run_command(command, cwd=folder, timeout_seconds=240).to_dict()
    report = {"status": "failed", "configuration": _identity(configuration), "applied_arguments": settings,
              "command": command, "result": built, "human_cleaning_evaluation": False,
              "method": "current_sumo_with_published_configuration",
              "claim_boundary": "Recipe agreement is model reproducibility. Remaining differences have no manual or field attribution."}
    if built["status"] == "pass" and output.is_file():
        control = ET.parse(output).getroot()
        cases = [json.loads(line) for line in label_file.read_text(encoding="utf-8").splitlines()]
        baseline = {case["case_id"]: value for case, value in _candidate_predictions(source, source, cases)}
        rows, counts, changed_counts = [], Counter(), Counter()
        for case, prediction in _candidate_predictions(source, control, cases):
            expected = case["expected_join"] if case["kind"] == "junction_membership" else case["expected_present"]
            outcome = ("unknown_reference" if expected is None else "unresolved_control" if prediction is None else
                       "recipe_reproduced" if expected == prediction else "unexplained_difference")
            before = baseline[case["case_id"]]
            changed = before != expected if before is not None and expected is not None else None
            rows.append({"case_id": case["case_id"], "kind": case["kind"], "expected": expected,
                         "unjoined_prediction": before, "control_prediction": prediction,
                         "baseline_difference": changed, "outcome": outcome})
            counts[outcome] += 1
            if changed:
                changed_counts[outcome] += 1
        provenance = folder / "generator-control-labels.jsonl"
        with provenance.open("x", encoding="utf-8", newline="\n") as stream:
            for row in rows:
                stream.write(json.dumps(row, sort_keys=True) + "\n")
        load = run_sumo_load_audit(net_file=output, output_dir=folder / "generator-control-load", sumo_binary=sumo_binary)
        report.update(status="measured", network=_identity(output), label_provenance=_identity(provenance),
                      sumo_load=load, counts=dict(counts), baseline_difference_counts=dict(changed_counts))
    report_file = folder / "generator-control.json"
    write_json_atomic(report_file, report, sort_keys=True)
    return {**report, "report": _identity(report_file)}


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
    report["implementation"] = _identity(Path(__file__).resolve())
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
            location = root.find("location")
            offset = location.get("netOffset", "0,0") if location is not None else "0,0"
            # Global-coordinate rounding can break exact registration with a normalized reference.
            disable_normalization = not any(Decimal(value) for value in offset.split(","))
            command = [netconvert_binary, "--osm-files", str(osm), *settings, "--junctions.join", "false", "--tls.join", "false",
                       "--geometry.remove", "false", "--offset.disable-normalization", str(disable_normalization).lower(),
                       "--output-file", str(raw)]
            built = run_command(command, cwd=folder, timeout_seconds=240).to_dict()
            write_json_atomic(folder / "import-command.json", {"source_osm": _identity(osm), "result": built}, sort_keys=True)
            row["source_osm"] = _identity(osm)
            row["import_coordinate_frame"] = {"reference_net_offset": location.get("netOffset") if location is not None else None,
                "normalization_mode": "disabled" if disable_normalization else "native",
                "basis": "reference_declared_offset" if location is not None and location.get("netOffset") else "missing_offset_keep_global",
                "registration": "unchanged_exact_coordinates_and_source_ids"}
            if configuration is not None:
                row["import_configuration"] = {**_identity(configuration), "applied_arguments": settings,
                    "overrides": {"junctions.join": False, "tls.join": False, "geometry.remove": False},
                    "ignored_sections": [],
                    "ignored_output_options": ["output-file", "output-prefix", "log", "error-log", "message-log", "verbose"],
                    "toolchain_relation": "current_sumo_with_published_import_settings"}
            row["import_status"] = built["status"]
            if built["status"] == "pass" and raw.is_file():
                comparison_root = ET.parse(raw).getroot()
                actual_location = comparison_root.find("location")
                actual_offset = actual_location.get("netOffset") if actual_location is not None else None
                actual_projection = actual_location.get("projParameter") if actual_location is not None else None
                reference_projection = location.get("projParameter") if location is not None else None
                reference_offset = location.get("netOffset") if location is not None else None
                row["import_coordinate_frame"].update(actual_net_offset=actual_offset,
                    actual_projection=actual_projection, reference_projection=reference_projection,
                    offset_matches_reference=([Decimal(value) for value in actual_offset.split(",")] ==
                        [Decimal(value) for value in reference_offset.split(",")]) if actual_offset and reference_offset else None,
                    projection_matches_reference=(actual_projection == reference_projection)
                        if actual_projection is not None and reference_projection is not None else None)
                row["raw_net"] = _identity(raw)
                if dataset["source_relation"] == "upstream_prepared_build_input":
                    alignment = audit_reference_road_alignment(net, raw, source_osm_file=osm, output_dir=folder / "alignment")
                    row["alignment"] = alignment
                    row["comparison_source"] = {**_identity(raw), "kind": "prepared_osm_import"}
                else:
                    comparison_root = None
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
            if row["comparison_source"]["kind"] == "prepared_osm_import":
                row["paired_labels"]["coordinate_frame_control"] = _coordinate_frame_control(command=command,
                    source_file=raw, reference_file=net, label_file=label_file, dataset=dataset,
                    folder=folder, sumo_binary=sumo_binary)
            if row.get("import_configuration") and row["comparison_source"]["kind"] == "prepared_osm_import":
                row["paired_labels"].update(label_usage="generator_recipe_regression", human_cleaning_evaluation=False,
                    generator_control=_generator_control(files=files, osm=osm, source=comparison_root,
                        label_file=label_file, folder=folder, netconvert_binary=netconvert_binary, sumo_binary=sumo_binary))
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
    source_edges, _, _ = _connections(source_root)
    edges, _, connections = _connections(candidate_root)
    reference_edges, _, reference_connections = _connections(reference_root)
    counts, change_counts, cases = defaultdict(Counter), defaultdict(Counter), []
    labelled_connections = set()
    label_cases = [json.loads(text) for text in label_path.read_text(encoding="utf-8").splitlines()]
    sensitivity, source_states = _source_sensitivity(dataset, label_cases)
    for case, predicted in _candidate_predictions(source_root, candidate_root, label_cases):
        if case["kind"] == "junction_membership":
            expected = case["expected_join"]
        else:
            expected = case["expected_present"]
            pair = tuple(case["connection"])
            labelled_connections.add(pair)
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
                      "predicted": predicted, "outcome": outcome, "reference_change": case.get("change"),
                      "source_coordinate_sensitivity": source_states.get(case["case_id"], "not_measured")})
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
              "coordinate_frame_sensitivity": sensitivity,
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
