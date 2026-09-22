"""Restore source-supported motor-vehicle boundaries after an explicit node join.

This is an after-join helper, not a physical-intersection detector. Source
reachability is not evidence that a turn is legal in the field.
"""

from __future__ import annotations

import math
import shutil
import xml.etree.ElementTree as ET
from collections.abc import Mapping, Sequence
from copy import deepcopy
from pathlib import Path
from typing import Any

from sumolib.geomhelper import polyLength, positionAtShapeOffset

from .artifact_io import write_json_atomic
from .candidate_contracts import file_sha256
from .command_runner import run_command
from .connection_mode_audit import audit_network_connection_mode
from .source_movement_support import _candidate_modes, _index, _internal, _source_paths


def _signature(element: ET.Element) -> tuple:
    return (element.tag, tuple(sorted(element.attrib.items())), (element.text or "").strip(), tuple(_signature(child) for child in element))


def _coordinate_frame(root: ET.Element) -> tuple:
    location = root.find("location")
    if location is None:
        raise ValueError("source and joined networks require a coordinate frame")
    return tuple(location.get(key) for key in ("netOffset", "projParameter"))


def _groups(root: ET.Element, groups: Mapping[str, Sequence[str]]) -> dict[str, set[str]]:
    if not isinstance(groups, Mapping) or not groups:
        raise ValueError("groups must name explicitly selected joined junctions")
    nodes = {row.get("id"): row for row in root.findall("junction") if not row.get("id", "").startswith(":")}
    normalized, seen = {}, set()
    for joined, values in groups.items():
        if not isinstance(joined, str) or not joined or joined.startswith(":") or isinstance(values, (str, bytes)):
            raise ValueError("groups require a junction ID and a sequence of source IDs")
        members = set(map(str, values))
        if len(members) < 2 or len(members) != len(values) or members - nodes.keys() or members & seen:
            raise ValueError("join members must be known, unique, and disjoint")
        if joined in nodes and joined not in members:
            raise ValueError("joined ID must not replace an unrelated existing junction")
        if any(nodes[node].get("type", "").startswith(("traffic_light", "rail_")) for node in members):
            raise ValueError("source groups with signal control are not supported")
        seen.update(members)
        normalized[joined] = members
    index = _index(root)
    for members in normalized.values():
        reachable, pending = set(), [next(iter(members))]
        while pending:
            current = pending.pop()
            if current in reachable:
                continue
            reachable.add(current)
            for edge in index["edges"].values():
                a, b = edge.get("from"), edge.get("to")
                if a in members and b in members and current in {a, b}:
                    pending.extend({a, b} - reachable)
        if reachable != members:
            raise ValueError("source join members must form one connected group")
    for _, start, _, connection in index["movements"]:
        if connection.get("tl") and index["edges"][index["lanes"][start][0]].get("to") in seen:
            raise ValueError("source groups with signal control are not supported")
    return normalized


def _diagnostic_signal_view(source_root, groups):
    """Ignore timing only for explicitly scoped, locally owned source signals."""
    members = {str(node) for values in groups.values() for node in values}
    index = _index(source_root)

    def owner(connection):
        edge = index["edges"].get(connection.get("from"))
        return (edge.get("to") if edge is not None and not _internal(edge) else
                index["owners"].get(connection.get("via")) or
                index["owners"].get(index["keys"].get((connection.get("from"), int(connection.get("fromLane", "0"))))))

    retired = {c.get("tl") for c in source_root.findall("connection") if c.get("tl") and owner(c) in members}
    if not retired:
        raise ValueError("diagnostic signal rebuilding requires source signal control")
    if any(c.get("tl") in retired and owner(c) not in members for c in source_root.findall("connection")):
        raise ValueError("a source controller also controls connections outside the declared group")
    if set(groups) & ({logic.get("id") for logic in source_root.findall("tlLogic")} - retired):
        raise ValueError("a joined controller would replace a controller outside the declared group")
    view = deepcopy(source_root)
    for node in view.findall("junction"):
        if node.get("id") in members and node.get("type", "").startswith("traffic_light"):
            node.set("type", "priority")
    for connection in view.findall("connection"):
        if connection.get("tl") in retired:
            for key in ("tl", "linkIndex", "linkIndex2"):
                connection.attrib.pop(key, None)
    for logic in list(view.findall("tlLogic")):
        if logic.get("id") in retired:
            view.remove(logic)
    return view, sorted(retired)


def collect_join_boundary_paths(source_root: ET.Element, *, groups: Mapping[str, Sequence[str]],
                                signal_policy: str = "preserve") -> dict[str, Any]:
    """Enumerate fixed-lane, mode-consistent paths wholly within each group."""
    if signal_policy not in {"preserve", "rebuild_diagnostic"}:
        raise ValueError("unsupported signal_policy")
    retired = []
    if signal_policy == "rebuild_diagnostic":
        source_root, retired = _diagnostic_signal_view(source_root, groups)
    normalized = _groups(source_root, groups)
    index = _index(source_root)
    movements, records = [], []
    for joined, members in sorted(normalized.items()):
        internal = {key for key, edge in index["edges"].items() if not _internal(edge) and edge.get("from") in members and edge.get("to") in members}
        incoming = {key for key, edge in index["edges"].items() if not _internal(edge) and edge.get("to") in members and edge.get("from") not in members}
        outgoing = {key for key, edge in index["edges"].items() if not _internal(edge) and edge.get("from") in members and edge.get("to") not in members}
        if not incoming or not outgoing:
            raise ValueError("each source group needs incoming and outgoing boundary roads")
        for left in sorted(incoming):
            for li in range(index["counts"][left]):
                start = index["keys"][left, li]
                for right in sorted(outgoing):
                    for ri in range(index["counts"][right]):
                        target = index["keys"][right, ri]
                        paths = _source_paths(index, start, target, members, index["lanes"][start][2] & index["lanes"][target][2])
                        if paths:
                            movements.append({"join_id": joined, "connection": [left, li, right, ri], "source_paths": paths, "vehicle_classes": sorted({mode for path in paths for mode in path["vehicle_classes"]})})
        records.append({"join_id": joined, "source_node_ids": sorted(members), "interior_edge_ids": sorted(internal), "incoming_edge_ids": sorted(incoming), "outgoing_edge_ids": sorted(outgoing)})
    return {"groups": records, "movements": movements, "signal_policy": signal_policy, "retired_tls_ids": retired,
            "basis": "source fixed-lane paths inside declared members; no exterior detours or assumed lane changes", "field_turn_legality": "review_required"}


def _outside_delta(source: ET.Element, candidate: ET.Element, excluded: set[str], *, ignored_tls_ids=()) -> dict[str, Any]:
    def items(root):
        index = _index(root)
        result = {key: {} for key in ("external_edges", "internal_edges", "junctions", "connections", "tls_programs")}
        for key, edge in index["edges"].items():
            if _internal(edge):
                owner = next((index["owners"].get(lane.get("id")) for lane in edge.findall("lane")), None)
                if owner not in excluded:
                    result["internal_edges"][key] = _signature(edge)
            elif edge.get("from") not in excluded and edge.get("to") not in excluded:
                result["external_edges"][key] = _signature(edge)
        prefixes = sorted(index["owners"].values(), key=lambda value: len(value or ""), reverse=True)
        for row in root.findall("junction"):
            key = row.get("id", "")
            owner = next((value for value in prefixes if value and key.startswith(f":{value}_")), key)
            if owner not in excluded:
                result["junctions"][key] = _signature(row)
        for row in root.findall("connection"):
            edge = index["edges"][row.get("from")]
            owner = index["owners"].get(row.get("via")) or index["owners"].get(index["keys"][row.get("from"), int(row.get("fromLane"))]) or edge.get("to")
            if owner not in excluded:
                key = tuple(row.get(field, "") for field in ("from", "fromLane", "to", "toLane", "via"))
                result["connections"][key] = _signature(row)
        result["tls_programs"] = {(row.get("id"), row.get("programID")): _signature(row)
                                  for row in root.findall("tlLogic") if row.get("id") not in ignored_tls_ids}
        return result
    before, after = items(source), items(candidate)
    result = {f"changed_{key}": sorted(str(item) for item in before[key].keys() | after[key].keys() if before[key].get(item) != after[key].get(item)) for key in before}
    result["coordinate_frame_changed"] = _coordinate_frame(source) != _coordinate_frame(candidate)
    result["status"] = "blocked" if any(result.values()) else "pass"
    # Bounds describe the displayed extent, not the coordinate transformation.
    # Keep exact metadata differences visible without calling them a shifted
    # road coordinate. Actual exterior geometry is compared above.
    result["location_metadata_changed"] = _signature(source.find("location")) != _signature(candidate.find("location"))
    result["location_metadata"] = {"source": dict(source.find("location").attrib), "candidate": dict(candidate.find("location").attrib)}
    return result


def _compare_boundary(root: ET.Element, plan: Mapping[str, Any]) -> dict[str, Any]:
    index = _index(root)
    expected = {tuple(row["connection"]): set(row["vehicle_classes"]) for row in plan["movements"]}
    joined_ids = {row["join_id"] for row in plan["groups"]}
    actual = {}
    for pair, start, target, connection in index["movements"]:
        if index["edges"][pair[0]].get("to") in joined_ids:
            actual[pair] = _candidate_modes(index, start, target, connection)[0]
    extra = [list(pair) for pair, modes in sorted(actual.items()) if modes and pair not in expected]
    permission = [{"connection": list(pair), "expected": sorted(modes), "actual": sorted(actual[pair])} for pair, modes in expected.items() if pair in actual and actual[pair] != modes]
    return {"expected_count": len(expected), "actual_count": sum(bool(modes) for modes in actual.values()), "missing": [list(pair) for pair in sorted(expected.keys() - actual.keys())], "unexpected_motorized": extra, "permission_mismatches": permission}


def _demand_impact(path: Path, missing_edges: set[str]) -> dict[str, Any]:
    root = ET.parse(path).getroot()
    routes = {row.get("id"): row.get("edges", "").split() for row in root.findall("route") if row.get("id")}
    affected, unresolved = [], []
    for row in root:
        if row.tag not in {"trip", "vehicle", "flow"}:
            continue
        start, target = row.get("from"), row.get("to")
        nested = row.find("route")
        edges = nested.get("edges", "").split() if nested is not None else routes.get(row.get("route"), [])
        if edges:
            start, target = edges[0], edges[-1]
        if start is None or target is None:
            unresolved.append({"id": row.get("id"), "reason": "OD representation is not an explicit edge pair"})
        elif start in missing_edges or target in missing_edges:
            affected.append({"id": row.get("id"), "depart": row.get("depart", row.get("begin")), "from": start, "to": target, "affected_sides": [side for side, edge in (("from", start), ("to", target)) if edge in missing_edges]})
    return {"path": str(path), "sha256": file_sha256(path), "affected_od": affected, "unresolved_od": unresolved, "same_demand_runnable": not affected and not unresolved, "policy": "No vehicles, departure times, or OD locations are changed. Removed internal OD edges require a separate declared demand design."}


def _source_polygon_preserves_cuts(node, edges):
    """A fixed polygon must not swallow an already-built boundary lane."""
    from shapely.geometry import LineString, Polygon

    points = [tuple(map(float, token.split(",")[:2])) for token in node.get("shape", "").split()]
    if len(points) < 3:
        return False
    polygon = Polygon(points)
    if polygon.convex_hull.area <= 1e-8:
        return True  # A straight stop-line shape has no interior to recut.
    if not polygon.is_valid:
        return False
    interior = polygon.buffer(-0.1)
    for edge in edges:
        for lane in edge.findall("lane"):
            shape = [tuple(map(float, token.split(",")[:2])) for token in lane.get("shape", "").split()]
            if len(shape) < 2 or LineString(shape).intersection(interior).length > 0.1:
                return False
    return True


def _boundary_geometry(source_root, source_index, plan, destination):
    from .hamburg_official_intersection_plainxml import _angular_endpoint_polygon

    nodes, patches, records = ET.Element("nodes"), {}, []
    members = {node for group in plan["groups"] for node in group["source_node_ids"]}
    frozen_neighbors = set()
    boundary_edges = {name: source_index["edges"][name] for group in plan["groups"]
                      for key in ("incoming_edge_ids", "outgoing_edge_ids") for name in group[key]}
    native_neighbors = set()
    for group in plan["groups"]:
        corners = []
        for side, names in (("to", group["incoming_edge_ids"]), ("from", group["outgoing_edge_ids"])):
            for edge_id in names:
                original = source_index["edges"][edge_id]
                remote = original.get("from" if side == "to" else "to")
                if remote not in members and remote not in frozen_neighbors and remote not in native_neighbors:
                    node = source_root.find(f"junction[@id='{remote}']")
                    if node is not None and len(node.get("shape", "").split()) >= 3:
                        incident = [edge for edge in boundary_edges.values() if remote in (edge.get("from"), edge.get("to"))]
                        if _source_polygon_preserves_cuts(node, incident):
                            ET.SubElement(nodes, "node", id=remote, shape=node.get("shape"))
                            frozen_neighbors.add(remote)
                        else:
                            native_neighbors.add(remote)
                edge = patches.setdefault(edge_id, deepcopy(original))
                edge.set(side, group["join_id"])
                edge.set("numLanes", str(len(edge.findall("lane"))))
                for ordinal, patched_lane in enumerate(edge.findall("lane")):
                    patched_lane.attrib.pop("id", None)
                    patched_lane.set("index", str(ordinal))
                lane_shapes = [[tuple(map(float, token.split(",")[:2])) for token in lane.get("shape", "").split()] for lane in original.findall("lane")]
                if any(len(shape) < 2 for shape in lane_shapes):
                    raise ValueError("source boundary lanes require explicit geometry")
                lengths = [polyLength(shape) for shape in lane_shapes]
                if any(length <= 0 for length in lengths):
                    raise ValueError("source boundary lane geometry is degenerate")
                # Use only the already-clipped lane geometry. Old reference
                # control points may lie beyond these ends and reverse the
                # apparent approach/exit direction after endpoint replacement.
                fractions = {0.0, 1.0}
                for shape, length in zip(lane_shapes, lengths):
                    distance = 0.0
                    for a, b in zip(shape, shape[1:]):
                        distance += math.dist(a, b)
                        fractions.add(min(1.0, distance / length))
                reference = []
                for fraction in sorted(fractions):
                    positions = [positionAtShapeOffset(shape, fraction * length) for shape, length in zip(lane_shapes, lengths)]
                    reference.append(tuple(sum(point[axis] for point in positions) / len(positions) for axis in (0, 1)))
                edge.set("shape", " ".join(f"{x:.6f},{y:.6f}" for x, y in reference))
                for lane in original.findall("lane"):
                    points = [tuple(map(float, token.split(",")[:2])) for token in lane.get("shape", "").split()]
                    if len(points) < 2:
                        raise ValueError("source boundary lanes require explicit geometry")
                    a, b = points[-2:] if side == "to" else points[:2]
                    length = math.dist(a, b)
                    if length <= 0:
                        raise ValueError("source boundary lane endpoint direction is degenerate")
                    endpoint = b if side == "to" else a
                    half_width = float(lane.get("width", "3.2")) / 2
                    normal = (-(b[1] - a[1]) / length * half_width, (b[0] - a[0]) / length * half_width)
                    corners.extend([(endpoint[0] + normal[0], endpoint[1] + normal[1]), (endpoint[0] - normal[0], endpoint[1] - normal[1])])
        center = tuple(sum(point[axis] for point in corners) / len(corners) for axis in (0, 1))
        polygon = _angular_endpoint_polygon(corners, center=center, tolerance_m=0.1)
        shape = " ".join(f"{x:.6f},{y:.6f}" for x, y in polygon)
        ET.SubElement(nodes, "node", id=group["join_id"], shape=shape)
        records.append({"join_id": group["join_id"], "shape": shape, "basis": "source boundary lane endpoint cross-sections; not an official field polygon"})
    edges = ET.Element("edges")
    edges.extend(patches[key] for key in sorted(patches))
    paths = {}
    for name, root in (("nodes", nodes), ("edges", edges)):
        paths[name] = destination / f"boundary.{name}.xml"
        ET.indent(root)
        ET.ElementTree(root).write(paths[name], encoding="utf-8", xml_declaration=True)
    return paths, {"groups": records, "frozen_neighbor_junction_ids": sorted(frozen_neighbors),
                   "native_neighbor_junction_ids": sorted(native_neighbors),
                   "native_neighbor_reason": "source polygon would recut an existing boundary lane; require outside preservation"}


def _adjacent_geometry(original, native, plan, node_ids):
    requested = set(map(str, node_ids))
    if len(requested) != len(node_ids):
        raise ValueError("adjacent_geometry_junction_ids must be unique")
    before, after = _index(original), _index(native)
    boundary = {edge for group in plan["groups"] for key in ("incoming_edge_ids", "outgoing_edge_ids") for edge in group[key]}
    members = {node for group in plan["groups"] for node in group["source_node_ids"]}
    adjacent = {before["edges"][edge].get(side) for edge in boundary for side in ("from", "to")} - members
    if requested - adjacent:
        raise ValueError("adjacent geometry nodes must directly meet a source group boundary")
    actual = {row[0]: row for row in after["movements"]}
    selected = {pair for pair, _, _, _ in before["movements"] if before["edges"][pair[0]].get("to") in requested and (pair[0] in boundary or pair[2] in boundary)}
    records, patches = [], {}
    for pair, start, target, connection in before["movements"]:
        if pair not in selected:
            continue
        current = actual.get(pair)
        if current is None or _signature(connection) != _signature(current[3]):
            raise ValueError("adjacent connection identity, permission, or signal binding changed")
        modes, old_path = _candidate_modes(before, start, target, connection)
        new_modes, new_path = _candidate_modes(after, current[1], current[2], current[3])
        if not modes or modes != new_modes or old_path != new_path or len(old_path) < 3:
            raise ValueError("adjacent internal path identity or permissions changed")
        owner = before["edges"][pair[0]].get("to")
        old_node = original.find(f"junction[@id='{owner}']")
        new_node = native.find(f"junction[@id='{owner}']")
        if [_signature(row) for row in old_node.findall("request")] != [_signature(row) for row in new_node.findall("request")]:
            raise ValueError("adjacent request conflicts changed")
        shapes = []
        for lane_id in old_path[1:-1]:
            old_lane, new_lane = before["lanes"][lane_id][1], after["lanes"][lane_id][1]
            if {key: value for key, value in old_lane.attrib.items() if key not in {"shape", "length"}} != {key: value for key, value in new_lane.attrib.items() if key not in {"shape", "length"}}:
                raise ValueError("adjacent lane attributes other than geometry changed")
            old_points = [tuple(map(float, point.split(",")[:2])) for point in old_lane.get("shape", "").split()]
            new_points = [tuple(map(float, point.split(",")[:2])) for point in new_lane.get("shape", "").split()]
            if len(old_points) < 2 or len(new_points) < 2:
                raise ValueError("adjacent lane geometry is incomplete")
            shapes.append({"lane_id": lane_id, "before": dict(old_lane.attrib), "after": dict(new_lane.attrib), "start_shift_m": math.dist(old_points[0], new_points[0]), "end_shift_m": math.dist(old_points[-1], new_points[-1])})
            patches[lane_id] = {key: new_lane.get(key) for key in ("shape", "length")}
        records.append({"junction_id": owner, "connection_before": dict(connection.attrib), "connection_after": dict(current[3].attrib), "internal_geometry": shapes, "signal_and_conflicts_unchanged": True})
    if {row["junction_id"] for row in records} != requested:
        raise ValueError("each adjacent geometry node must have a verified boundary connection")
    for pair, start, target, connection in before["movements"]:
        if pair not in selected and set(_candidate_modes(before, start, target, connection)[1][1:-1]) & patches.keys():
            raise ValueError("adjacent geometry is shared by an undeclared connection")
    return patches, records


def restore_joined_boundary_connections(
    *, source_net: Path | str, joined_net: Path | str,
    groups: Mapping[str, Sequence[str]], output_dir: Path | str,
    expected_source_sha256: str, expected_joined_sha256: str,
    original_trip_files: Sequence[Path | str] = (),
    adjacent_geometry_junction_ids: Sequence[str] = (),
    netconvert_binary: str = "netconvert", sumo_binary: str = "sumo",
    timeout_seconds: float = 240.0,
    signal_policy: str = "preserve",
    junction_contours: str = "preserve", junction_corner_radius_m: float = 8.0,
    interior_lane_change_policy: str = "fixed_paths",
) -> dict[str, Any]:
    """Restore source-supported pairs; preserve non-target artifacts."""
    source, joined = Path(source_net).resolve(strict=True), Path(joined_net).resolve(strict=True)
    destination = Path(output_dir).resolve()
    if destination.exists():
        raise ValueError("output_dir must not already exist")
    if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be finite and positive")
    if junction_contours not in {"preserve", "guarded", "fused"}:
        raise ValueError("unsupported junction contour method")
    if interior_lane_change_policy not in ("fixed_paths", "permitted_interior"):
        raise ValueError("unsupported interior lane-change policy")
    source_hash, joined_hash = file_sha256(source), file_sha256(joined)
    if source_hash != expected_source_sha256.lower() or joined_hash != expected_joined_sha256.lower():
        raise ValueError("source or joined network SHA-256 does not match")
    original, native = ET.parse(source).getroot(), ET.parse(joined).getroot()
    if _coordinate_frame(original) != _coordinate_frame(native):
        raise ValueError("source and joined coordinate frames must match")
    trip_paths = [Path(path).resolve(strict=True) for path in original_trip_files]
    trip_hashes = {path: file_sha256(path) for path in trip_paths}
    plan = collect_join_boundary_paths(original, groups=groups, signal_policy=signal_policy)
    normalized = {group["join_id"]: set(group["source_node_ids"]) for group in plan["groups"]}
    allowed_tls_changes = set(plan["retired_tls_ids"]) | (set(normalized) if signal_policy == "rebuild_diagnostic" else set())
    excluded = set(normalized) | set().union(*normalized.values())
    source_index, joined_index = _index(original), _index(native)
    for group in plan["groups"]:
        owner = native.find(f"junction[@id='{group['join_id']}']")
        permitted_owner = (owner is not None and owner.get("type") == "traffic_light") if signal_policy == "rebuild_diagnostic" else (owner is not None and not owner.get("type", "").startswith(("traffic_light", "rail_")))
        if not permitted_owner:
            raise ValueError("joined groups must match the declared signal policy")
        for side, names in (("to", group["incoming_edge_ids"]), ("from", group["outgoing_edge_ids"])):
            for edge_id in names:
                edge = joined_index["edges"].get(edge_id)
                if edge is None or edge.get(side) != group["join_id"] or joined_index["counts"][edge_id] != source_index["counts"][edge_id]:
                    raise ValueError("joined boundary lane identity or ownership changed")
                if [lane.get("id") for lane in edge.findall("lane")] != [lane.get("id") for lane in source_index["edges"][edge_id].findall("lane")]:
                    raise ValueError("joined boundary lane identities require an explicit mapping")
    before = _compare_boundary(native, plan)
    if interior_lane_change_policy == "permitted_interior":
        from .source_movement_support import _source_lane_change_path

        # Explicit geometric repair policy: a short absorbed road can prevent
        # execution of an otherwise permitted lane change in the broken source.
        # Admit only native turns with complete, mode-consistent permission paths.
        for pair in before["unexpected_motorized"]:
            start = source_index["keys"][pair[0], pair[1]]
            target = source_index["keys"][pair[2], pair[3]]
            owner = joined_index["edges"][pair[0]].get("to")
            movement = next(row for row in joined_index["movements"] if row[0] == tuple(pair))
            modes = _candidate_modes(joined_index, *movement[1:])[0]
            paths = [_source_lane_change_path(source_index, start, target, normalized[owner], mode)
                     for mode in sorted(modes)]
            if all(paths):
                plan["movements"].append({"join_id": owner, "connection": pair,
                    "vehicle_classes": sorted(modes), "source_paths": [],
                    "permitted_interior_lane_change_paths": [dict(path, vehicle_class=mode)
                        for path, mode in zip(paths, sorted(modes))],
                    "basis": "permitted source lane changes within absorbed ordinary roads; source execution is not claimed"})
        before = _compare_boundary(native, plan)
    plan["interior_lane_change_policy"] = interior_lane_change_policy
    if interior_lane_change_policy == "permitted_interior":
        plan["basis"] = ("source fixed-lane paths plus explicitly permitted lane-change paths for native turns "
                         "inside absorbed ordinary roads; no outside detours or source-execution claim")
    destination.mkdir(parents=True)
    patch = ET.Element("connections")
    missing = {tuple(pair) for pair in before["missing"]}
    for row in plan["movements"]:
        if tuple(row["connection"]) in missing:
            left, li, right, ri = row["connection"]
            ET.SubElement(patch, "connection", {"from": left, "fromLane": str(li), "to": right, "toLane": str(ri), "allow": " ".join(row["vehicle_classes"])})
    patch_file = destination / "boundary.con.xml"
    ET.indent(patch)
    ET.ElementTree(patch).write(patch_file, encoding="utf-8", xml_declaration=True)
    geometry_paths, boundary_geometry = _boundary_geometry(original, source_index, plan, destination)
    compiled = destination / "compiled.net.xml"
    commands = []
    result = run_command([netconvert_binary, "--sumo-net-file", str(joined), "--node-files", str(geometry_paths["nodes"]), "--edge-files", str(geometry_paths["edges"]), "--connection-files", str(patch_file), "--offset.disable-normalization", "true", "--junctions.internal-link-detail", "25", "--output-file", str(compiled)], cwd=destination, timeout_seconds=timeout_seconds)
    commands.append(result.to_dict())
    if result.returncode != 0 or not compiled.is_file():
        write_json_atomic(destination / "failed-command.json", commands[-1])
        raise ValueError("netconvert could not restore boundary connections: " + result.stderr)
    contour_report = None
    if junction_contours != "preserve":
        from .hamburg_junctions.movements import _compile_junction_contours

        _, contour_report = _compile_junction_contours(ET.parse(compiled).getroot(),
            command=commands[-1]["command"], groups=plan["groups"], output_file=compiled,
            timeout_seconds=timeout_seconds, mode=junction_contours, corner_radius_m=junction_corner_radius_m,
            include_context=False)
    candidate = destination / "boundary-restored.net.xml"
    shutil.copy2(compiled, candidate)
    raw_root = ET.parse(compiled).getroot()
    outside_before = _outside_delta(original, raw_root, excluded, ignored_tls_ids=allowed_tls_changes)
    adjacent_patches, adjacent_records = _adjacent_geometry(original, raw_root, plan, adjacent_geometry_junction_ids)
    # Reuse the existing coherent restoration of non-target internal lanes,
    # requests, connections and controller programs. Do not restore only a
    # polygon or silently accept netconvert's unrelated round-trip changes.
    from .junction_rebuild.restoration import _restore_non_target_internal_artifacts
    tree = ET.parse(candidate)
    root = tree.getroot()
    for position, edge in enumerate(list(root)):
        if edge.tag == "edge" and not _internal(edge) and edge.get("from") not in excluded and edge.get("to") not in excluded:
            original_edge = source_index["edges"].get(edge.get("id"))
            if original_edge is not None:
                root[position] = deepcopy(original_edge)
    tree.write(candidate, encoding="utf-8", xml_declaration=True)
    restoration = _restore_non_target_internal_artifacts(source_file=source, target_file=candidate, exclude_junction_ids=excluded)
    # The legacy restore appends requests after params. Preserve the whole
    # non-target node, including exact child order, once its referenced lanes
    # and connections have been coherently restored.
    tree = ET.parse(candidate)
    source_nodes = {node.get("id"): node for node in original.findall("junction")}
    for position, node in enumerate(list(tree.getroot())):
        if node.tag == "junction" and not node.get("id", "").startswith(":") and node.get("id") not in excluded and node.get("id") in source_nodes:
            tree.getroot()[position] = deepcopy(source_nodes[node.get("id")])
    tree.write(candidate, encoding="utf-8", xml_declaration=True)
    final = ET.parse(candidate).getroot()
    expected_outside = deepcopy(original)
    fixed_markers = boundary_geometry["frozen_neighbor_junction_ids"]
    for root in (expected_outside, final):
        # Keep the fixed-boundary meaning of the explicit node patch. Dropping
        # this marker lets a later import move a neighbor's cut on each reload,
        # even though its restored polygon coordinates have not changed here.
        for node in root.findall("junction"):
            if node.get("id") in fixed_markers:
                node.set("customShape", "1")
        for edge in root.findall("edge"):
            for lane in edge.findall("lane"):
                if lane.get("id") in adjacent_patches:
                    lane.attrib.update(adjacent_patches[lane.get("id")])
    ET.ElementTree(final).write(candidate, encoding="utf-8", xml_declaration=True)
    signal_report = None
    if signal_policy == "rebuild_diagnostic":
        from .topology_signal_rebuild import rebuild_topology_test_signals

        signal_report = rebuild_topology_test_signals(net_file=candidate, output_dir=destination / "diagnostic-signals",
            target_junction_ids=sorted(normalized), expected_source_sha256=file_sha256(candidate))
        if signal_report["status"] != "pass":
            raise ValueError("diagnostic signal rebuilding did not pass its conflict checks")
        candidate = Path(signal_report["candidate_network"]["path"])
        final = ET.parse(candidate).getroot()
    outside_after = _outside_delta(expected_outside, final, excluded, ignored_tls_ids=allowed_tls_changes)
    outside_after["explicit_adjacent_geometry_lane_ids"] = sorted(adjacent_patches)
    outside_after["fixed_shape_marker_junction_ids"] = fixed_markers
    boundary_after = _compare_boundary(final, plan)
    structural = audit_network_connection_mode(final, endpoint_tolerance_m=0.1)
    write_json_atomic(destination / "connection-audit.json", structural)
    load = run_command([sumo_binary, "--net-file", str(candidate), "--begin", "0", "--end", "1", "--no-step-log", "true"], cwd=destination, timeout_seconds=timeout_seconds)
    commands.append(load.to_dict())
    removed = {key for key, edge in source_index["edges"].items() if not _internal(edge)} - {edge.get("id") for edge in final.findall("edge") if not _internal(edge)}
    expected_removed = {key for group in plan["groups"] for key in group["interior_edge_ids"]}
    demand = [_demand_impact(path, removed) for path in trip_paths]
    remaining_old_nodes = (set().union(*normalized.values()) - normalized.keys()) & {node.get("id") for node in final.findall("junction")}
    gates = {"source_immutable": source_hash == file_sha256(source) and joined_hash == file_sha256(joined) and all(file_sha256(path) == digest for path, digest in trip_hashes.items()), "outside_preserved": outside_after["status"] == "pass", "boundary_coverage": not boundary_after["missing"] and not boundary_after["permission_mismatches"] and not boundary_after["unexpected_motorized"], "only_declared_interior_edges_removed": removed == expected_removed, "source_members_collapsed": not remaining_old_nodes, "connection_structure": structural["structural_failure_count"] == 0, "sumo_load": load.returncode == 0}
    report = {"schema": "torii.junction-boundary-rebuild/v1", "status": "pass" if all(gates.values()) else "blocked", "decision": "review_required", "claim_status": "diagnostic-demo", "field_turn_legality": "review_required", "source_network": {"path": str(source), "sha256": source_hash}, "joined_network": {"path": str(joined), "sha256": joined_hash}, "candidate_network": {"path": str(candidate), "sha256": file_sha256(candidate)}, "compiled_network": {"path": str(compiled), "sha256": file_sha256(compiled)}, "plan": plan, "boundary_before": before, "boundary_after": boundary_after, "outside_before_restore": outside_before, "outside_preservation": outside_after, "restoration": restoration, "removed_interior_edges": sorted(removed), "original_demand": demand, "gates": gates, "commands": commands, "non_motorized_connectivity": "not_audited", "claim_boundary": "Preserves source motor-vehicle boundary paths after explicitly selected uncontrolled joins. No physical grouping, field turn permission, historical signals or unchanged-demand completion is inferred.", "sources": ["https://sumo.dlr.de/docs/Networks/PlainXML.html#joining-junctions", "https://sumo.dlr.de/docs/Networks/PlainXML.html#lane-to-lane-connectivity"]}
    report["input_bounds_metadata_changed"] = _signature(original.find("location")) != _signature(native.find("location"))
    report["signal_policy"] = signal_policy
    report["retired_source_tls_ids"] = plan["retired_tls_ids"]
    report["generic_test_signals"] = signal_report
    report["junction_contours"] = contour_report
    if signal_report is not None:
        report["claim_boundary"] = "Preserves source boundary paths under an explicit diagnostic signal rebuild. Physical grouping requires source review. Field turn legality and historical signal timing remain unverified."
    report["boundary_geometry"] = boundary_geometry
    report["adjacent_geometry"] = {"requested_junction_ids": list(adjacent_geometry_junction_ids), "connections": adjacent_records, "policy": "Only shape and length of explicitly adjacent, identity-stable existing internal lanes may use native geometry. Node shapes, request conflicts, permissions, speeds, signal programs, and all other connections stay unchanged."}
    final_index = _index(final)
    boundary_ids = {edge for group in plan["groups"] for key in ("incoming_edge_ids", "outgoing_edge_ids") for edge in group[key]}
    report["boundary_lane_geometry_changes"] = [{"lane_id": lane_id, "before": dict(lane.attrib), "after": dict(final_index["lanes"][lane_id][1].attrib)} for lane_id, (edge, lane, _) in source_index["lanes"].items() if edge in boundary_ids and lane.attrib != final_index["lanes"][lane_id][1].attrib]
    manifest = destination / "manifest.json"
    report["manifest_file"] = str(manifest)
    write_json_atomic(manifest, report)
    return report
