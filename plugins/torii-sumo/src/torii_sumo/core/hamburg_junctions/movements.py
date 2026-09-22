"""Compile official movements and check junction contour preservation."""

from __future__ import annotations

import math
import shutil
from copy import deepcopy
import xml.etree.ElementTree as ET
from collections import defaultdict
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any
import numpy as np
from ..candidate_contracts import file_sha256
from ..command_runner import run_command
from ..connection_mode_audit import lane_supports_motorized
from ..official_movement_composition import compose_official_movements
from ..source_movement_support import audit_source_movement_support, probe_source_interior_lane_changes, _modes as _lane_motorized_modes
from .geometry import _parse_shape, _shape, fit_movement_shape_to_anchors, reanchor_movement_shape
from .groups import _movements_by_part
from .lanes import _bind_official_lanes, _keep_boundary_lane_identity, _keep_constructed_lane_identity


def _write_movement_candidate(
    *,
    joined_net: Path,
    plans: Mapping[str, Mapping[str, Any]],
    groups: Sequence[Mapping[str, Any]],
    skipped_join_ids: set[str],
    output_file: Path,
    maximum_anchor_projection_error_m: float,
    maximum_lane_projection_error_m: float = 10.0,
    minimum_lane_match_margin_m: float = 0.5,
    netconvert_binary: str = "netconvert",
    timeout_seconds: float = 240.0,
    original_net: Path | None = None,
    constructed_ingress: Mapping[tuple[str, str], Mapping[str, tuple[str, int]]] | None = None,
    lane_origins: Mapping[str, str | None] | None = None,
    identity_changed_edges: Sequence[str] = (),
    source_lane_change_evidence: list[dict[str, Any]] | None = None,
    sumo_binary: str = "sumo",
    seed: int = 104,
    junction_contours: str = "preserve",
    junction_corner_radius_m: float = 8.0,
) -> dict[str, Any]:
    """Write whole connection curves to netconvert, never only the first via."""
    root = ET.parse(joined_net).getroot()
    source_root = ET.parse(original_net or joined_net).getroot()
    external = {
        (edge.attrib["id"], index): _parse_shape(lane.get("shape", ""))
        for edge in root.findall("edge")
        if edge.get("function") != "internal"
        for index, lane in enumerate(edge.findall("lane"))
        if lane_supports_motorized(lane) and len(_parse_shape(lane.get("shape", ""))) >= 2
    }
    external_modes = {
        (edge.attrib["id"], index): _lane_motorized_modes(lane)
        for edge in root.findall("edge") if edge.get("function") != "internal"
        for index, lane in enumerate(edge.findall("lane"))
    }
    edges = {edge.get("id"): edge for edge in root.findall("edge") if edge.get("function") != "internal"}
    group_by_key = {(str(row["node_id"]), str(row["intersection_part"])): row for row in groups}
    patch = ET.Element("connections")
    required = []
    unresolved = []
    geometry_rejected = []
    lane_bindings = []
    part_reports = []
    patched_sources: dict[tuple[str, int, str, int], str] = {}
    for node_id, plan in plans.items():
        for part, movements in _movements_by_part(plan).items():
            group = group_by_key.get((node_id, part))
            if group is None:
                unresolved.extend({"node_id": node_id, "intersection_part": part, "movement_id": str(row["movement_id"]), "reason": "physical_part_unbound"} for row in movements)
                continue
            junction_id = str(group["join_id"])
            incoming = {key: shape for key, shape in external.items() if edges[key[0]].get("to") == junction_id}
            outgoing = {key: shape for key, shape in external.items() if edges[key[0]].get("from") == junction_id}
            internal = {role: [row["lane_id"] for row in group.get("boundary_port_classification", {}).get("official_internal_anchors", []) if row["role"] == role] for role in ("ingress", "egress")}
            ingress = _bind_official_lanes(plan["lanes"], movements, incoming, role="ingress", max_error_m=maximum_lane_projection_error_m, margin_m=minimum_lane_match_margin_m, candidate_modes=external_modes, internal_lane_ids=internal["ingress"])
            _keep_constructed_lane_identity(ingress, (constructed_ingress or {}).get((node_id, part), {}), incoming)
            egress = _bind_official_lanes(plan["lanes"], movements, outgoing, role="egress", max_error_m=maximum_lane_projection_error_m, margin_m=minimum_lane_match_margin_m, candidate_modes=external_modes, internal_lane_ids=internal["egress"])
            for role, matched, available in (("ingress", ingress, incoming), ("egress", egress, outgoing)):
                _keep_boundary_lane_identity(matched, group, available, role=role, lane_origins=lane_origins or {},
                    changed_edge_ids=identity_changed_edges, maximum_error_m=maximum_lane_projection_error_m)
            lane_bindings.append({"node_id": node_id, "intersection_part": part, "junction_id": junction_id, "ingress": ingress, "egress": egress})
            expected_keys = set()
            expected_exit_modes = defaultdict(set)
            for movement in movements:
                record = {
                    "node_id": node_id,
                    "intersection_part": part,
                    "join_id": junction_id,
                    "movement_id": str(movement["movement_id"]),
                    "ingress_lane_id": str(movement["ingress_lane_id"]),
                    "egress_lane_id": str(movement["egress_lane_id"]),
                    "selected_source": str(movement["selected_source"]),
                }
                official_lanes = {str(lane["lane_id"]): lane for lane in plan["lanes"]}
                restrictions = [set(official_lanes[lane_id]["allowed_vehicle_classes"]) for lane_id in (record["ingress_lane_id"], record["egress_lane_id"]) if official_lanes.get(lane_id, {}).get("allowed_vehicle_classes") is not None]
                record["allowed_vehicle_classes"] = sorted(set.intersection(*restrictions)) if restrictions else None
                record["revocable_lane_assumption"] = "enabled_for_topology_test;activation_not_replayed" if any(official_lanes.get(lane_id, {}).get("revocable") for lane_id in (record["ingress_lane_id"], record["egress_lane_id"])) else None
                source = ingress["bindings"].get(record["ingress_lane_id"])
                target = egress["bindings"].get(record["egress_lane_id"])
                if record["allowed_vehicle_classes"] == []:
                    unresolved.append({**record, "reason": "official_vehicle_permissions_conflict"})
                    continue
                if source is None or target is None:
                    unresolved.append({**record, "reason": "official_lane_binding_unresolved"})
                    continue
                key = (*source, *target)
                record["sumo_connection"] = list(key)
                expected_keys.add(key)
                modes = external_modes[source] & external_modes[target]
                if record["allowed_vehicle_classes"] is not None:
                    modes &= set(record["allowed_vehicle_classes"])
                expected_exit_modes[source, target[0]].update(modes)
                required.append(record)
                anchored, error = fit_movement_shape_to_anchors(movement["selected_shape_network"], start=external[source][-1], end=external[target][0])
                geometry_source = "selected_curve"
                if (error > maximum_anchor_projection_error_m and movement["selected_source"] == "aerial_trace"
                        and movement.get("official_shape_network")):
                    official_anchored, official_error = fit_movement_shape_to_anchors(
                        movement["official_shape_network"], start=external[source][-1], end=external[target][0])
                    record["selected_curve_anchor_projection_error_sum_m"] = error
                    record["official_curve_anchor_projection_error_sum_m"] = official_error
                    if official_error <= maximum_anchor_projection_error_m:
                        anchored, error = official_anchored, official_error
                        geometry_source = "official_map_curve"
                record["anchor_projection_error_sum_m"] = round(error, 6)
                attrs = {"from": source[0], "fromLane": str(source[1]), "to": target[0], "toLane": str(target[1])}
                if record["allowed_vehicle_classes"] is not None:
                    attrs["allow"] = " ".join(record["allowed_vehicle_classes"])
                if error > maximum_anchor_projection_error_m:
                    geometry_rejected.append({**record, "reason": "movement_anchor_fit_exceeds_limit", "geometry_source": "netconvert"})
                elif junction_id not in skipped_join_ids:
                    attrs["shape"] = _shape(anchored)
                    patched_sources[key] = geometry_source
                ET.SubElement(patch, "connection", attrs)
            # Matching every official lane does not make MAP an exhaustive
            # prohibition list. Remove only redundant targets on the same
            # exit road; unlisted road turns still need the source-path audit.
            mapped_in = set(ingress["bindings"].values())
            mapped_out = set(egress["bindings"].values())
            direct_scope_complete = (
                all(str(row["ingress_lane_id"]) in ingress["bindings"] for row in movements)
                and all(str(row["egress_lane_id"]) in egress["bindings"] for row in movements)
            )
            for connection in root.findall("connection"):
                key = _connection_key(connection)
                if not direct_scope_complete or key[:2] not in mapped_in or key[2:] not in mapped_out or key in expected_keys:
                    continue
                existing_modes = external_modes[key[:2]] & external_modes[key[2:]] & _lane_motorized_modes(connection)
                # A bus-only alternative cannot replace passenger access.
                if existing_modes and existing_modes <= expected_exit_modes.get((key[:2], key[2]), set()):
                    ET.SubElement(patch, "delete", {name: connection.attrib[name] for name in ("from", "fromLane", "to", "toLane")})
            part_reports.append({"node_id": node_id, "intersection_part": part, "join_id": junction_id, "official_movement_count": len(movements), "required_connection_count": len(expected_keys), "direct_official_scope_complete": direct_scope_complete})

    composition_groups = [group for group in groups if any(str(group["node_id"]) == row["node_id"] and str(group["intersection_part"]) == row["intersection_part"] for row in unresolved)]
    boundary_bindings = {row["junction_id"]: {role: row[role]["bindings"] for role in ("ingress", "egress")} for row in lane_bindings}
    composition_args = dict(plans=plans, groups=composition_groups, boundary_bindings=boundary_bindings,
        current_lane_to_original_lane=lane_origins, maximum_lane_error_m=maximum_lane_projection_error_m,
        minimum_match_margin_m=minimum_lane_match_margin_m,
        maximum_source_anchor_error_m=maximum_anchor_projection_error_m)
    composition = compose_official_movements(source_root, root, **composition_args) if composition_groups else None
    composed_pairs = set()
    if composition is not None:
        proposed = {}
        composed_permissions = defaultdict(set)
        for path in composition["boundary_paths"]:
            key = tuple(path["connection"])
            composed_pairs.add(key)
            composed_permissions[key].update(path["allowed_vehicle_classes"])
            error = path["connection_anchor_error_m"]
            previous_error = proposed.get(key, {}).get("connection_anchor_error_m")
            if key not in proposed or (error if error is not None else math.inf) < (previous_error if previous_error is not None else math.inf):
                proposed[key] = path
        # Official records can describe portions of a complete joined path.
        # Such a path must not be removed by the single-step comparison above.
        for deletion in list(patch.findall("delete")):
            if _connection_key(deletion) in composed_pairs:
                patch.remove(deletion)
        patched_keys = {_connection_key(row) for row in patch.findall("connection")}
        for key, path in proposed.items():
            if key in patched_keys:
                continue
            attrs = {"from": key[0], "fromLane": str(key[1]), "to": key[2], "toLane": str(key[3])}
            shape, error = path["connection_shape_network"], path["connection_anchor_error_m"]
            if shape and error is not None and error <= maximum_anchor_projection_error_m and path["join_id"] not in skipped_join_ids:
                attrs["shape"] = _shape(shape)
                patched_sources[key] = "selected_curve"
            ET.SubElement(patch, "connection", attrs)
        for connection in patch.findall("connection"):
            key = _connection_key(connection)
            if key in composed_permissions and composed_permissions[key] != external_modes[key[:2]] & external_modes[key[2:]]:
                connection.set("allow", " ".join(sorted(composed_permissions[key])))

    patch_file = output_file.with_suffix(".con.xml")
    command = [str(netconvert_binary), "--sumo-net-file", str(joined_net), "--connection-files", str(patch_file), "--offset.disable-normalization", "true", "--junctions.internal-link-detail", "25", "--output-file", str(output_file)]
    commands = []
    removed_source_fanouts = []
    restored_source_permissions = []
    change_evidence = source_lane_change_evidence if source_lane_change_evidence is not None else []
    source_members = {str(group["join_id"]): group["source_node_ids"] for group in groups}
    support_args = dict(group_source_node_ids=source_members,
        official_required_pairs=[*[row["sumo_connection"] for row in required], *composed_pairs],
        current_lane_to_original_lane=lane_origins, identity_changed_edges=identity_changed_edges,
        source_lane_change_evidence=change_evidence)
    # Reuse the same joined source each time: the compiled junction may trim
    # its approaches, so align full curves to those endpoints before rebuilding.
    for iteration in range(4):
        ET.indent(patch, space="    ")
        ET.ElementTree(patch).write(patch_file, encoding="utf-8", xml_declaration=True)
        result = run_command(command, cwd=output_file.parent, timeout_seconds=timeout_seconds)
        commands.append({"command": command, "returncode": result.returncode, "stdout": result.stdout, "stderr": result.stderr})
        if result.returncode != 0 or not output_file.is_file():
            raise ValueError("netconvert could not compile the official connection candidate: " + result.stderr)
        compiled_root = ET.parse(output_file).getroot()
        compiled_lanes = {
            (edge.attrib["id"], index): _parse_shape(lane.get("shape", ""))
            for edge in compiled_root.findall("edge") if edge.get("function") != "internal"
            for index, lane in enumerate(edge.findall("lane"))
        }
        source_support = audit_source_movement_support(source_root, compiled_root, **support_args)
        if original_net is not None and iteration == 0:
            before = len(change_evidence)
            for row in source_support["unresolved"]:
                start, target = row.get("original_from_lane"), row.get("original_to_lane")
                if not start or not target:
                    continue
                members = source_members[row["junction_id"]]
                modes = set(row["candidate_vehicle_classes"]) - set(row.get("supported_vehicle_classes", []))
                tested = {result["vehicle_class"] for report in change_evidence if report["start_lane"] == start and report["target_lane"] == target and set(report["source_node_ids"]) == set(members) for result in report["results"]}
                if modes - tested:
                    change_evidence.append(probe_source_interior_lane_changes(
                        source_net=original_net, start_lane=start, target_lane=target,
                        source_node_ids=members, vehicle_classes=sorted(modes - tested),
                        output_dir=output_file.parent / f"{output_file.stem}-source-lane-change-{len(change_evidence):04d}",
                        sumo_binary=sumo_binary, seed=seed, timeout_seconds=timeout_seconds))
            if len(change_evidence) != before:
                source_support = audit_source_movement_support(source_root, compiled_root, **support_args)
        deletions = {_connection_key(row) for row in patch.findall("delete")}
        redundant = [pair for pair in source_support["unsupported_redundant_pairs"] if tuple(pair) not in deletions]
        # A removed interior bus road must not become an unrestricted turn.
        # Keep the turn and the proved modes; official new movements are
        # protected by the source audit and are not restricted here.
        permissions = [row for row in source_support["unresolved"]
                       if row["reason"] == "candidate_permissions_exceed_proved_source_permissions"
                       and row.get("supported_vehicle_classes")]
        changes = []
        for connection in patch.findall("connection"):
            if not connection.get("shape"):
                continue
            key = _connection_key(connection)
            start, end = compiled_lanes[key[:2]][-1], compiled_lanes[key[2:]][0]
            old_shape = _parse_shape(connection.attrib["shape"])
            if max(math.dist(old_shape[0], start), math.dist(old_shape[-1], end)) > 0.05:
                changes.append((connection, reanchor_movement_shape(old_shape, start=start, end=end)))
        if not changes and not redundant and not permissions or iteration == 3:
            break
        compiled_connections = {_connection_key(row): row for row in compiled_root.findall("connection")}
        patched_connections = {_connection_key(row): row for row in patch.findall("connection")}
        for row in permissions:
            pair = tuple(row["connection"])
            connection = patched_connections.get(pair)
            if connection is None:
                original = compiled_connections[pair]
                connection = ET.SubElement(patch, "connection", {name: original.attrib[name]
                    for name in ("from", "fromLane", "to", "toLane", "allow", "disallow") if name in original.attrib})
            denied = set(row["candidate_vehicle_classes"]) - set(row["supported_vehicle_classes"])
            allowed = set(connection.get("allow", "").split())
            if allowed and "all" not in allowed:
                connection.set("allow", " ".join(sorted(allowed - denied - set(connection.get("disallow", "").split()))))
                connection.attrib.pop("disallow", None)
            else:
                connection.attrib.pop("allow", None)
                connection.set("disallow", " ".join(sorted(denied | set(connection.get("disallow", "").split()))))
            restored_source_permissions.append({"connection": list(pair),
                "previous_vehicle_classes": row["candidate_vehicle_classes"],
                "allowed_vehicle_classes": row["supported_vehicle_classes"],
                "basis": "original_source_paths_and_verified_lane_changes;nonmotorized_permissions_unchanged"})
        for pair in redundant:
            ET.SubElement(patch, "delete", {"from": pair[0], "fromLane": str(pair[1]), "to": pair[2], "toLane": str(pair[3])})
            removed_source_fanouts.append(pair)
        for connection, shape in changes:
            connection.set("shape", _shape(shape))

    from ..junction_rebuild.restoration import restore_off_scope_netconvert_artifacts

    mutable_nodes = {str(node) for group in groups for node in [group["join_id"], *group["source_node_ids"]]}
    mutable_edges = set(identity_changed_edges) | {
        edge.get("id") for network in (source_root, compiled_root) for edge in network.findall("edge")
        if edge.get("from") in mutable_nodes or edge.get("to") in mutable_nodes
    }
    outside_restoration = restore_off_scope_netconvert_artifacts(
        source_file=original_net or joined_net, target_file=output_file,
        mutable_junction_ids=mutable_nodes, mutable_edge_ids=mutable_edges,
    )
    if outside_restoration["status"] != "pass":
        raise ValueError("Official movement construction changed roads outside its declared scope: " + str(outside_restoration.get("failures", [])))
    compiled_root = ET.parse(output_file).getroot()

    contours = {"mode": "preserve", "status": "not_applicable", "parts": [],
                "accepted_junction_ids": [], "rejected_junction_ids": []}
    if junction_contours in ("guarded", "fused"):
        compiled_root, contours = _compile_junction_contours(
            compiled_root, command=command, groups=[row for row in groups if row["join_id"] not in skipped_join_ids],
            output_file=output_file, timeout_seconds=timeout_seconds, mode=junction_contours,
            corner_radius_m=junction_corner_radius_m)
        # The final compilation owns internal lane identities and full paths.
        source_support = audit_source_movement_support(source_root, compiled_root, **support_args)
    elif junction_contours != "preserve":
        raise ValueError("junction_contours must be preserve, guarded, or fused")
    tree = ET.ElementTree(compiled_root)
    ET.indent(tree, space="    ")
    tree.write(output_file, encoding="utf-8", xml_declaration=True)
    if composition is not None:
        composition = compose_official_movements(source_root, compiled_root, **composition_args)
        remaining = []
        for row in unresolved:
            paths = [path for path in composition["boundary_paths"] if path["node_id"] == row["node_id"] and path["intersection_part"] == row["intersection_part"] and any(segment["movement_id"] == row["movement_id"] and segment["official_lane_pair"] == [row["ingress_lane_id"], row["egress_lane_id"]] for segment in path["movement_segments"])]
            if not paths:
                remaining.append(row)
                continue
            paths.sort(key=lambda path: (path["status"] != "pass", path["connection"]))
            required.append({**row, "reason": "official_segments_composed_after_join",
                "sumo_connection": paths[0]["connection"],
                "sumo_connections": [path["connection"] for path in paths],
                "composition_witness_ids": [path["witness_id"] for path in paths],
                "composition_geometry_status": paths[0]["geometry_status"],
                "allowed_vehicle_classes": paths[0]["allowed_vehicle_classes"]})
        unresolved = remaining
    final_edges = {edge.get("id"): edge for edge in compiled_root.findall("edge") if edge.get("function") != "internal"}
    preserved = set(edges) == set(final_edges) and all(
        edge.get("from") == final_edges[edge_id].get("from")
        and edge.get("to") == final_edges[edge_id].get("to")
        and len(edge.findall("lane")) == len(final_edges[edge_id].findall("lane"))
        for edge_id, edge in edges.items()
    )
    scoped_junctions = {str(group["join_id"]) for group in groups}
    actual = {
        _connection_key(connection): connection
        for connection in compiled_root.findall("connection")
        if connection.get("from") in final_edges
        and final_edges[connection.get("from")].get("to") in scoped_junctions
        and _connection_key(connection)[:2] in external
        and _connection_key(connection)[2:] in external
    }
    required_keys = {tuple(row["sumo_connection"]) for row in required} | composed_pairs
    missing = [row for row in required if tuple(row["sumo_connection"]) not in actual]
    missing.extend({"sumo_connection": list(key), "reason": "composed_boundary_connection_missing"} for key in sorted(composed_pairs - set(actual)) if not any(tuple(row["sumo_connection"]) == key for row in missing))
    extra = [list(key) for key in sorted(set(actual) - required_keys)]
    unexplained_extra = [row["connection"] for row in source_support["unresolved"]] + source_support["unsupported_redundant_pairs"]
    materialized = []
    for row in required:
        key = tuple(row["sumo_connection"])
        connection = actual.get(key)
        if connection is not None:
            composed = bool(row.get("composition_witness_ids"))
            materialized.append({**row,
                "geometry_source": patched_sources.get(key, "netconvert"),
                "internal_lane_id": "" if composed else connection.get("via", "").split()[0] if connection.get("via") else "",
                "signal_binding_status": "composed_submovement_requires_control_mapping" if composed else "direct_movement"})
    composition_review = [path for path in (composition or {}).get("boundary_paths", []) if path["status"] != "pass"]
    boundary_connections = {tuple(row["sumo_connection"]): row for row in required}
    for path in (composition or {}).get("boundary_paths", []):
        key = tuple(path["connection"])
        boundary_connections.setdefault(key, {"node_id": path["node_id"], "intersection_part": path["intersection_part"], "join_id": path["join_id"], "movement_id": path["witness_id"], "sumo_connection": path["connection"], "allowed_vehicle_classes": path["allowed_vehicle_classes"]})
    audit = {
        "status": "pass" if not (missing or unexplained_extra or unresolved or composition_review) and preserved else "review_required" if preserved else "blocked",
        "required": required,
        "actual": [list(key) for key in sorted(actual)],
        "missing": missing,
        "extra": extra,
        "unexplained_extra": unexplained_extra,
        "source_supported_extra": source_support["source_backed_extra"],
        "removed_source_unsupported_fanouts": removed_source_fanouts,
        "restored_source_permissions": restored_source_permissions,
        "outside_road_restoration": outside_restoration,
        "source_movement_support": source_support,
        "source_lane_change_probes": change_evidence,
        "ambiguous": unresolved,
        "geometry_rejected": geometry_rejected,
        "exterior_edges_preserved": preserved,
        "extra_policy": "Unlisted road turns require source-path checks. Only redundant lane targets on the same exit road are removed.",
        "lane_bindings": lane_bindings,
        "composition": composition,
        "composed_boundary_path_reviews": composition_review,
        "boundary_connections": list(boundary_connections.values()),
    }
    return {
        "materialized_movement_count": len(materialized),
        "movements": materialized,
        "parts": part_reports,
        "official_connection_audit": audit,
        "connection_patch": str(patch_file),
        "netconvert_passes": commands,
        "selected_netconvert_command": contours.get("selected_command", command),
        "junction_contours": contours,
    }


def _contour_target_ids(root, groups, *, include_context):
    declared = list(dict.fromkeys(str(row["join_id"]) for row in groups))
    if not include_context:
        return declared
    neighbors = defaultdict(set)
    for edge in root.findall("edge"):
        start, end = edge.get("from"), edge.get("to")
        if not start or not end or start == end or not any(lane_supports_motorized(lane) for lane in edge.findall("lane")):
            continue
        neighbors[start].add(end)
        neighbors[end].add(start)
    context = {node.get("id") for node in root.findall("junction")
               if len(neighbors[node.get("id")]) >= 3}
    return declared + sorted(context - set(declared))


def _compile_junction_contours(root, *, command, groups, output_file, timeout_seconds, mode="guarded", corner_radius_m=8.0, include_context=True):
    """Compile each bounded shape from the original construction inputs."""
    from ..hamburg_junction_contour import audit_junction_contour, propose_junction_contour

    proposer_options = {}
    if mode == "fused":
        from ..hamburg_junction_contour import propose_fused_junction_contour as propose_junction_contour
        proposer_options["corner_radius_m"] = corner_radius_m
        proposer_options["native_boundary_refit"] = True
    elif mode != "guarded":
        raise ValueError("contour compilation mode must be guarded or fused")

    destination = output_file.parent / f"{output_file.stem}-contours"
    destination.mkdir()
    baseline_file = destination / "baseline.net.xml"
    ET.ElementTree(root).write(baseline_file, encoding="utf-8", xml_declaration=True)
    source_files = [Path(path) for option in ("--sumo-net-file", "--connection-files", "--node-files", "--edge-files")
                    if option in command for path in command[command.index(option) + 1].split(",")]
    sources = [{"path": str(path), "sha256": file_sha256(path)} for path in source_files]
    report = {"mode": mode, "status": "review_required", "sources": sources,
              "baseline_network": {"path": str(baseline_file), "sha256": file_sha256(baseline_file)},
              "parts": [], "accepted_junction_ids": [], "rejected_junction_ids": [],
              "claim_boundary": "These local shape cuts preserve known model surfaces and compiled traffic "
                                "semantics. They do not identify or verify field curbs or traffic islands."}
    target_ids = _contour_target_ids(root, groups, include_context=mode == "fused" and include_context)
    declared = {str(row["join_id"]) for row in groups}
    report["target_junction_ids"] = target_ids
    report["context_junction_ids"] = [identifier for identifier in target_ids if identifier not in declared]
    report["selection_basis"] = ("Declared groups and retained motorized road junctions with at least three distinct neighboring nodes, including priority nodes inside signalized intersections. This is a road-branch inventory, not a claim that every node is a separate physical intersection."
                                 if mode == "fused" and include_context else "Only the declared junction groups.")
    nodes = ET.Element("nodes")
    selected_file = None
    for identifier in target_ids:
        proposal = propose_junction_contour(root, identifier, **proposer_options)
        row = {"junction_id": identifier, "status": proposal["status"], "proposal": proposal}
        report["parts"].append(row)
        if not proposal["changed"]:
            continue
        node = ET.SubElement(nodes, "node", id=identifier, shape=_shape(proposal["proposed_shape"]))
        index = len(report["parts"])
        node_file = destination / f"parts-{index:03d}.nod.xml"
        trial_file = destination / f"trial-{index:03d}.net.xml"
        ET.indent(nodes, space="  ")
        ET.ElementTree(nodes).write(node_file, encoding="utf-8", xml_declaration=True)
        trial_command = list(command)
        trial_command[trial_command.index("--output-file") + 1] = str(trial_file)
        if "--node-files" in trial_command:
            trial_command[trial_command.index("--node-files") + 1] += "," + str(node_file)
        else:
            trial_command += ["--node-files", str(node_file)]
        result = run_command(trial_command, cwd=destination, timeout_seconds=timeout_seconds)
        row["netconvert"] = {"command": trial_command, "returncode": result.returncode,
                             "stdout": result.stdout, "stderr": result.stderr}
        row["node_file"] = {"path": str(node_file), "sha256": file_sha256(node_file)}
        accepted = False
        materialized_file = trial_file
        if result.returncode == 0 and trial_file.is_file():
            candidate = ET.parse(trial_file).getroot()
            row["network"] = {"path": str(trial_file), "sha256": file_sha256(trial_file)}
            if proposal.get("native_boundary_refit") and candidate.find(f"junction[@id='{identifier}']") is not None:
                # The requested change is the outline. Netconvert may also
                # recut roads or recalculate turn speeds. Keep those source
                # traffic attributes and test its actual outline against the
                # unchanged lane surfaces before adopting the shape alone.
                row["native_network"] = row["network"]
                native_comparison = _compare_contour_compilation(root, candidate, [identifier])
                row["native_compilation_preservation"] = {
                    "status": native_comparison["status"], "change_count": len(native_comparison["changes"]),
                    "geometry_tolerance_m": native_comparison["geometry_tolerance_m"],
                    "detail_basis": "The native network is retained for comparison. Its traffic changes are not adopted."}
                shape = candidate.find(f"junction[@id='{identifier}']").get("shape", "")
                candidate = deepcopy(root)
                junction = candidate.find(f"junction[@id='{identifier}']")
                junction.set("shape", shape)
                junction.set("customShape", "1")
                materialized_file = destination / f"outline-only-{index:03d}.net.xml"
                ET.ElementTree(candidate).write(materialized_file, encoding="utf-8", xml_declaration=True)
                row["network"] = {"path": str(materialized_file), "sha256": file_sha256(materialized_file)}
                row["traffic_restoration"] = "Only the compiled junction outline is adopted; all source lanes, connections, request tables and signal programs remain unchanged."
            comparison = _compare_contour_compilation(root, candidate, [identifier])
            row["preservation"] = comparison
            moved_lanes = [change for change in comparison["changes"] if change.get("element") == "lane"
                           and max(change.get("maximum_arclength_deviation_m", 0),
                                   change.get("polyline_length_difference_m", 0),
                                   change.get("length_difference_m", 0)) > 1e-8]
            row["lane_geometry_preservation"] = {"status": "blocked" if moved_lanes else "pass",
                "changed_lanes": moved_lanes, "roundoff_tolerance_m": 1e-8,
                "basis": "Contour-only edits may resample the same directed lane curve, but cannot move it. "
                         "This prevents compilation displacement from consuming coverage tolerance again."}
            junction = candidate.find(f"junction[@id='{identifier}']")
            if junction is not None:
                actual_shape = _parse_shape(junction.get("shape", ""))
                geometry = audit_junction_contour(root, identifier, actual_shape)
                row["compiled_geometry"] = geometry
                row["actual_lane_coverage"] = audit_junction_contour(candidate, identifier, actual_shape)
                accepted = comparison["status"] == "pass" and not moved_lanes and geometry["preservation_pass"]
        row["status"] = "pass" if accepted else "blocked"
        if accepted:
            root, selected_file = candidate, materialized_file
            report["selected_command"] = trial_command
            report["accepted_junction_ids"].append(identifier)
        else:
            nodes.remove(node)
            report["rejected_junction_ids"].append(identifier)
    if any(file_sha256(Path(row["path"])) != row["sha256"] for row in sources):
        raise ValueError("a contour construction input changed")
    if selected_file is not None:
        shutil.copyfile(selected_file, output_file)
    report["inputs_unchanged"] = True
    if mode == "fused":
        report["smooth_applied_junction_ids"] = [row["junction_id"] for row in report["parts"]
            if row["status"] == "pass" and row["proposal"].get("method", "").startswith("smooth_")]
        report["smooth_unresolved_junction_ids"] = [row["junction_id"] for row in report["parts"]
            if row["junction_id"] not in report["smooth_applied_junction_ids"]]
    return root, report


def _connection_key(connection: ET.Element) -> tuple[str, int, str, int]:
    return (connection.get("from", ""), int(connection.get("fromLane", "0")), connection.get("to", ""), int(connection.get("toLane", "0")))


def _contour_curve_difference(before_shape, after_shape):
    """Compare directed curves by arclength, independent of vertex sampling."""
    tolerance = 0.1
    try:
        if before_shape is None or after_shape is None:
            raise ValueError("an explicit shape was added or removed")
        curves = [np.asarray([tuple(map(float, point.split(","))) for point in value.split()], dtype=float)
                  for value in (before_shape, after_shape)]
        if (any(curve.ndim != 2 or not len(curve) or curve.shape[1] not in (2, 3)
                or not np.isfinite(curve).all() for curve in curves) or curves[0].shape[1] != curves[1].shape[1]):
            raise ValueError("shapes require finite coordinates in the same dimension")
        stations = [np.r_[0.0, np.cumsum(np.linalg.norm(np.diff(curve, axis=0), axis=1))] for curve in curves]
        lengths = [float(values[-1]) for values in stations]
        fractions = [values / length if length > 0 else np.zeros(len(values)) for values, length in zip(stations, lengths)]
        knots = np.unique(np.concatenate(fractions))
        sampled = [np.column_stack([np.interp(knots, positions, curve[:, axis]) for axis in range(curve.shape[1])])
                   for curve, positions in zip(curves, fractions)]
        deviation = round(float(np.linalg.norm(sampled[0] - sampled[1], axis=1).max()), 9)
        length_delta = round(abs(lengths[1] - lengths[0]), 9)
        return {"status": "pass" if max(deviation, length_delta) <= tolerance else "blocked",
                "before_vertex_count": len(curves[0]), "after_vertex_count": len(curves[1]),
                "start_shift_m": float(np.linalg.norm(curves[0][0] - curves[1][0])),
                "end_shift_m": float(np.linalg.norm(curves[0][-1] - curves[1][-1])),
                "maximum_arclength_deviation_m": deviation, "polyline_length_difference_m": length_delta,
                "tolerance_m": tolerance}
    except (TypeError, ValueError) as error:
        return {"status": "blocked", "reason": str(error), "tolerance_m": tolerance}


def _compare_contour_compilation(before_root, after_root, target_junction_ids):
    """Allow reviewed target outlines, preserving all other construction data."""
    from ..junction_boundary_rebuild import _signature

    targets = set(target_junction_ids)
    changes, target_shapes = [], []
    if before_root.attrib != after_root.attrib:
        changes.append({"status": "blocked", "kind": "network_attributes",
                        "before": dict(before_root.attrib), "after": dict(after_root.attrib)})
    node_ids = [{row.get("id") for row in root.findall("junction")} for root in (before_root, after_root)]
    if not targets or targets - (node_ids[0] & node_ids[1]):
        changes.append({"status": "blocked", "kind": "missing_target_junction",
                        "junction_ids": sorted(targets - (node_ids[0] & node_ids[1]))})

    def indexed(root):
        records = defaultdict(list)
        for row in root:
            fields = ("from", "fromLane", "to", "toLane") if row.tag == "connection" else (
                ("id", "programID") if row.tag == "tlLogic" else ("id",))
            records[(row.tag, *(row.get(field, "") for field in fields))].append(row)
        return records

    before, after = indexed(before_root), indexed(after_root)
    for key in sorted(before.keys() | after.keys()):
        if len(before[key]) != len(after[key]):
            changes.append({"status": "blocked", "kind": "element_count_or_identity", "key": list(key),
                            "before_count": len(before[key]), "after_count": len(after[key])})
            continue
        for original, current in zip(before[key], after[key]):
            left, right = deepcopy(original), deepcopy(current)
            if original.tag == "junction" and original.get("id") in targets:
                fields = ("shape", "customShape")
                if any(left.get(field) != right.get(field) for field in fields):
                    target_shapes.append({"junction_id": original.get("id"),
                        "before": {field: left.get(field) for field in fields},
                        "after": {field: right.get(field) for field in fields}})
                for field in fields:
                    left.attrib.pop(field, None)
                    right.attrib.pop(field, None)
            elif original.tag in {"edge", "connection", "junction"}:
                pairs = [(left, right)]
                if original.tag == "edge":
                    pairs.extend(zip(left.findall("lane"), right.findall("lane")))
                for first, second in pairs:
                    fields = ("shape", "length") if first.tag in {"edge", "lane"} else ("shape",)
                    for field in fields:
                        a, b = first.attrib.pop(field, None), second.attrib.pop(field, None)
                        if a == b:
                            continue
                        if field == "shape":
                            difference = _contour_curve_difference(a, b)
                        else:
                            try:
                                values = [float(value) for value in (a, b)]
                                if any(not math.isfinite(value) or value <= 0 for value in values):
                                    raise ValueError("length must stay finite and positive")
                                delta = round(abs(values[1] - values[0]), 9)
                                difference = {"status": "pass" if delta <= 0.1 else "blocked",
                                              "length_difference_m": delta, "tolerance_m": 0.1}
                            except (TypeError, ValueError) as error:
                                difference = {"status": "blocked", "reason": str(error)}
                        changes.append({"kind": "geometry", "key": list(key), "element": first.tag,
                                        "id": first.get("id"), "field": field, "before": a, "after": b, **difference})
            if _signature(left) != _signature(right):
                changes.append({"status": "blocked", "kind": "attributes_or_children", "key": list(key),
                                "before": _signature(left), "after": _signature(right)})
    return {"status": "blocked" if any(row["status"] == "blocked" for row in changes) else "pass",
            "geometry_tolerance_m": 0.1, "changes": changes, "target_shape_changes": target_shapes,
            "claim_boundary": "Only selected junction shape/customShape may change freely. Directed curves may be "
                              "resampled within existing 0.1 m geometry precision. All other attributes and child "
                              "records, including permissions, speeds, connection indices, conflicts and signal "
                              "programs, must match. Outline validity and coverage require their separate check."}
