"""Extract a native conversion input and merge its declared repair into a source."""

from copy import deepcopy
from pathlib import Path
import xml.etree.ElementTree as ET

from .candidate_contracts import file_sha256
from .internal_ownership import resolve_internal_ownership


def _index(root):
    edges = {row.get("id"): row for row in root.findall("edge")}
    if len(edges) != len(root.findall("edge")) or any(not identity for identity in edges):
        raise ValueError("Local conversion requires unique nonempty edge IDs.")
    return {"edges": edges, "ownership": resolve_internal_ownership(root)}


def _owner(element, index):
    if element.tag == "connection":
        edge = index["edges"].get(element.get("from"))
        if edge is None:
            return None
        if edge.get("id") not in index["ownership"]["edge"]:
            return edge.get("to")
        element = edge
    identity = element.get("id", "")
    if identity not in index["ownership"].get(element.tag, {}):
        return identity if element.tag == "junction" else None
    return index["ownership"][element.tag].get(identity)


def local_conversion_input(root, mutable_nodes, mutable_edges, *, join_groups=None):
    """Keep complete edit junctions and the roads needed to position their ends."""
    if root.find("location") is None:
        return None, {"status": "full_network", "reason": "coordinate_frame_not_declared"}
    index = _index(root)
    aliases = {node: joined for joined, nodes in (join_groups or {}).items() for node in nodes}
    absorbed = {key for key, edge in index["edges"].items() if key not in index["ownership"]["edge"]
                and edge.get("from") in aliases and aliases.get(edge.get("from")) == aliases.get(edge.get("to"))}
    controllers = {}
    for connection in root.findall("connection"):
        if connection.get("tl"):
            controllers.setdefault(connection.get("tl"), set()).add(_owner(connection, index))
    shared = sorted(key for key, owners in controllers.items()
                    if (owners & mutable_nodes or key in (join_groups or {})) and owners - mutable_nodes)
    if shared:
        return None, {"status": "full_network", "reason": "controller_spans_edit_boundary", "controller_ids": shared}
    selected = set(mutable_edges)
    changed = True
    while changed:
        before = set(selected)
        for ring in root.findall("roundabout"):
            edges = set(ring.get("edges", "").split())
            if edges & selected:
                selected.update(edges)
        changed = selected != before
    unpaired = sorted(key for key in selected if index["edges"][key].get("bidi")
                      and index["edges"][key].get("bidi") not in selected)
    if unpaired:
        return None, {"status": "full_network", "reason": "bidirectional_pair_exceeds_context", "edge_ids": unpaired}
    context = {index["edges"][key].get(end) for key in selected for end in ("from", "to")}
    internal = {key for key, owner in index["ownership"]["edge"].items() if owner in mutable_nodes}
    kept = selected | internal
    lanes = {lane.get("id") for key in kept for lane in index["edges"][key].findall("lane")}
    connections = []
    for connection in root.findall("connection"):
        if connection.get("from") not in kept or connection.get("to") not in kept:
            continue
        row = deepcopy(connection)
        if row.get("via") not in lanes:
            row.attrib.pop("via", None)
        connections.append(row)
    prohibitions = []
    for row in root.findall("prohibition"):
        paths = [row.get(name, "").split("->") for name in ("prohibitor", "prohibited")]
        if any(len(path) != 2 or any(edge not in index["edges"] for edge in path) for path in paths):
            return None, {"status": "full_network", "reason": "unresolved_prohibition_references"}
        if any(edge in absorbed for path in paths for edge in path):
            return None, {"status": "full_network", "reason": "prohibition_references_absorbed_road"}
        if all(edge in kept for path in paths for edge in path):
            prohibitions.append(deepcopy(row))
    used_tls = {row.get("tl") for row in connections if row.get("tl")}
    result = ET.Element("net", dict(root.attrib))
    for child in root:
        if child.tag in {"location", "type"} or child.tag == "tlLogic" and child.get("id") in used_tls:
            result.append(deepcopy(child))
        elif child.tag == "edge" and child.get("id") in kept:
            result.append(deepcopy(child))
        elif child.tag == "junction" and (child.get("id") in context or _owner(child, index) in mutable_nodes):
            row = deepcopy(child)
            for name in ("incLanes", "intLanes"):
                if name in row.attrib:
                    row.set(name, " ".join(value for value in row.get(name).split() if value in lanes))
            if child.get("id") not in mutable_nodes:
                for request in list(row.findall("request")):
                    row.remove(request)
                row.set("customShape", "true")
            result.append(row)
    result.extend(connections)
    result.extend(prohibitions)
    result.extend(deepcopy(row) for row in root.findall("roundabout") if set(row.get("edges", "").split()) <= selected)
    record = {"status": "local", "mutable_junction_ids": sorted(mutable_nodes),
                    "mutable_edge_ids": sorted(mutable_edges), "context_edge_ids": sorted(selected),
                    "context_junction_ids": sorted(context), "controller_ids": sorted(
                        {key for key, owners in controllers.items() if owners & mutable_nodes} | set(join_groups or {})),
                    "source_normal_edge_count": sum(not key.startswith(":") for key in index["edges"]),
                    "converted_normal_edge_count": len(selected)}
    if join_groups:
        record.update(junction_aliases=aliases, absorbed_edge_ids=sorted(absorbed))
    return result, record


def merge_local_conversion(source_root, native_root, record):
    """Copy the exterior from the source and use native elements only inside the edit."""
    mutable_nodes, mutable_edges = set(record["mutable_junction_ids"]), set(record["mutable_edge_ids"])
    before, after = _index(source_root), _index(native_root)
    actual = {key for key in after["edges"] if not key.startswith(":")}
    if actual != set(record["context_edge_ids"]) - set(record.get("absorbed_edge_ids", [])):
        raise ValueError("Local conversion changed the retained ordinary-road inventory.")
    aliases = record.get("junction_aliases", {})
    for key in actual:
        for end in ("from", "to"):
            original = before["edges"][key].get(end)
            if after["edges"][key].get(end) != aliases.get(original, original):
                raise ValueError("Local conversion changed an undeclared ordinary-road endpoint.")
    locations = [root.find("location") for root in (source_root, native_root)]
    frames = [(tuple(map(float, row.get("netOffset", "0,0").split(","))), row.get("projParameter"))
              if row is not None else None for row in locations]
    if frames[0] is None or frames[0] != frames[1]:
        raise ValueError("Local conversion changed the declared coordinate frame.")
    controllers = set(record["controller_ids"])

    def belongs(child, index):
        if child.tag == "edge":
            return child.get("id") in mutable_edges or _owner(child, index) in mutable_nodes
        if child.tag in {"junction", "connection"}:
            return _owner(child, index) in mutable_nodes
        if child.tag == "roundabout":
            return bool(set(child.get("edges", "").split()) & mutable_edges)
        return child.tag == "tlLogic" and child.get("id") in controllers

    result = deepcopy(source_root)
    for child in list(result):
        if belongs(child, before):
            result.remove(child)
    ranks = {name: rank for rank, name in enumerate(("location", "type", "edge", "tlLogic", "junction", "connection", "prohibition", "roundabout"))}
    for name in ("edge", "tlLogic", "junction", "connection", "roundabout"):
        additions = [deepcopy(child) for child in native_root if child.tag == name and belongs(child, after)]
        at = next((i for i, child in enumerate(result) if ranks.get(child.tag, 8) > ranks[name]), len(result))
        for offset, child in enumerate(additions):
            result.insert(at + offset, child)
    boxes = [row.get("convBoundary") for row in locations]
    if all(boxes):
        a, b = [list(map(float, value.split(","))) for value in boxes]
        result.find("location").set("convBoundary", ",".join(map(str, [min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3])])))
    return result


def validate_local_conversion(record, source_file):
    """Rebuild the local input and merged proposal from saved source and native files."""
    from .network_edit_operations import scoped_operation_scope, validate_scoped_operation
    source_root = ET.parse(source_file).getroot()
    operation = record["operation"]
    if validate_scoped_operation(operation, source_root):
        raise ValueError("Local conversion has an invalid scoped operation.")
    nodes, edges = scoped_operation_scope(source_root, operation)
    params = operation["params"]
    groups = {params["join_id"]: params["node_ids"]} if operation["operation"] in {"join_junctions", "absorb_reciprocal_fragment"} else None
    subset, expected = local_conversion_input(source_root, nodes, edges, join_groups=groups)
    return _validate_local_artifacts(record, source_root, subset, expected)


def _validate_local_artifacts(record, source_root, subset, expected):
    if subset is None or any(record.get(key) != value for key, value in expected.items()):
        raise ValueError("Local conversion scope differs from the source operation.")
    input_file = Path(record["input"]["path"])
    if (file_sha256(input_file) != record["input"]["sha256"]
            or input_file.read_bytes() != ET.tostring(subset, encoding="utf-8", xml_declaration=True)):
        raise ValueError("Local conversion input differs from its source-derived subset.")
    raw = Path(record["native_output"]["path"])
    merged = Path(record["merged_proposal"]["path"])
    if file_sha256(raw) != record["native_output"]["sha256"] or file_sha256(merged) != record["merged_proposal"]["sha256"]:
        raise ValueError("Local conversion artifacts changed.")
    root = merge_local_conversion(source_root, ET.parse(raw).getroot(), expected)
    if merged.read_bytes() != ET.tostring(root, encoding="utf-8", xml_declaration=True):
        raise ValueError("The merged proposal differs from its source and native result.")
    return input_file


def validate_join_boundary_conversion(report, source_file):
    """Reconstruct the post-join conversion input and its retained exterior."""
    from .junction_boundary_rebuild import _groups, _diagnostic_signal_view
    source = Path(source_file).resolve(strict=True)
    original = report["source_network"]
    joined = report["joined_network"]
    record = report["local_conversion"]
    if not Path(original["path"]).samefile(source) or file_sha256(source) != original["sha256"]:
        raise ValueError("Join-boundary conversion does not match its original source.")
    joined_path = Path(joined["path"])
    if file_sha256(joined_path) != joined["sha256"] or joined != record["source"]:
        raise ValueError("The saved joined input changed.")
    groups = {row["join_id"]: row["source_node_ids"] for row in report["plan"]["groups"]}
    original_root = ET.parse(source).getroot()
    if report["signal_policy"] == "rebuild_diagnostic":
        original_root, _ = _diagnostic_signal_view(original_root, groups)
    groups = _groups(original_root, groups)
    nodes = set(groups) | set().union(*groups.values()) | set(report["adjacent_geometry"]["requested_junction_ids"])
    root = ET.parse(joined_path).getroot()
    edges = {row.get("id") for row in root.findall("edge") if not row.get("id", "").startswith(":")
             and any(row.get(end) in nodes for end in ("from", "to"))}
    subset, expected = local_conversion_input(root, nodes, edges)
    input_file = _validate_local_artifacts(record, root, subset, expected)
    command = report["commands"][0]["command"]
    if command.count("--sumo-net-file") != 1 or command.index("--sumo-net-file") + 1 >= len(command):
        raise ValueError("Join-boundary native command has no unique input path.")
    if Path(command[command.index("--sumo-net-file") + 1]).resolve() != input_file.resolve():
        raise ValueError("Join-boundary native command uses another input.")
    return input_file
