"""Write plain-XML inputs for expanded and sequential junction joins."""

from __future__ import annotations

import copy
from typing import Any
import xml.etree.ElementTree as ET
from pathlib import Path
from ..command_runner import run_command
from ..junction_join_definition import build_junction_join_definition
from .artifacts import _command_report
from .network import _connection_edges_are_adjacent, _edge_drop_requires_review, _net_junction_ids
from .scope import (
    _endpoint_rewrites,
    _join_patch_endpoint_rewrites,
    _join_patch_joined_node_ids,
    _joined_source_node_ids,
    _normalize_joined_junction_ids,
    _sumo_cluster_member_ids,
    _sumo_joined_cluster_id,
)


def write_expanded_scope_plain_inputs(
    *,
    raw_node_file: Path,
    raw_edge_file: Path,
    raw_connection_file: Path,
    output_dir: Path,
    expanded_rebuild_scope: object,
    approach_endpoint_rebuild_plan: object | None = None,
    teacher_join_groups_by_cluster: dict[str, list[str]] | None = None,
    netconvert_binary: str = "netconvert",
    sumo_binary: str = "sumo",
    timeout_seconds: float = 240.0,
    command_runner: Any = run_command,
) -> dict[str, object]:
    output_dir.mkdir(parents=True, exist_ok=True)
    node_file = output_dir / "expanded_scope.nod.xml"
    edge_file = output_dir / "expanded_scope.edg.xml"
    connection_file = output_dir / "expanded_scope.con.xml"
    net_file = output_dir / "expanded_scope.net.xml"

    scope = expanded_rebuild_scope if isinstance(expanded_rebuild_scope, dict) else {}
    raw_scope_junction_ids = [str(item) for item in scope.get("junction_ids", []) or [] if str(item)]
    core_junction_id = str(scope.get("core_junction_id", ""))
    requested_join_ids = scope.get("join_junction_ids", None)
    default_join_ids = [core_junction_id] if core_junction_id else raw_scope_junction_ids
    requested_join_ids_list = [
        str(item)
        for item in (requested_join_ids if requested_join_ids is not None else default_join_ids)
        if str(item)
    ]
    blocked_edge_ids = {str(item) for item in scope.get("blocked_teacher_edge_ids", []) or [] if str(item)}
    teacher_join_groups_by_cluster = teacher_join_groups_by_cluster or {}

    raw_nodes = {
        node.attrib["id"]: node
        for node in ET.parse(raw_node_file).getroot()
        if node.tag == "node" and node.attrib.get("id")
    }

    def expand_scope_ids(values: list[str]) -> set[str]:
        expanded: set[str] = set()
        for value in values:
            members = _sumo_cluster_member_ids(value)
            # Generated cluster ids are only expanded when their member ids
            # are present in the current plain source.  Otherwise retain the
            # id so the report can explain the missing teacher endpoint.
            present_members = [member for member in members if member in raw_nodes]
            if value.startswith("cluster_") and len(present_members) >= 2:
                expanded.update(present_members)
            elif value in raw_nodes:
                expanded.add(value)
            else:
                expanded.add(value)
        return expanded

    seed_node_ids = expand_scope_ids(raw_scope_junction_ids)
    join_seed_node_ids = expand_scope_ids(requested_join_ids_list)

    # A teacher expanded scope may list a generated cluster as context rather
    # than in join_junction_ids.  Recreate that cluster as a separate join;
    # do not merge it with the core junction.  Explicit join groups remain
    # authoritative when supplied by the caller.
    explicit_join_groups: list[list[str]] = []
    explicit_group = sorted(node_id for node_id in join_seed_node_ids if node_id in raw_nodes)
    if len(explicit_group) >= 2:
        explicit_join_groups.append(explicit_group)
    join_groups = list(explicit_join_groups)
    auto_cluster_join_count = 0
    for cluster_id in raw_scope_junction_ids:
        if not cluster_id.startswith("cluster_"):
            continue
        mapped_cluster_members = teacher_join_groups_by_cluster.get(cluster_id, [])
        cluster_members = sorted(
            member
            for member in (
                mapped_cluster_members
                if mapped_cluster_members
                else _sumo_cluster_member_ids(cluster_id)
            )
            if member in raw_nodes
        )
        if len(cluster_members) < 2:
            continue
        cluster_set = set(cluster_members)
        if any(cluster_set <= set(group) for group in join_groups):
            continue
        join_groups.append(cluster_members)
        auto_cluster_join_count += 1

    cluster_aliases: dict[str, str] = {}
    for group in join_groups:
        generated_id = _sumo_joined_cluster_id(group)
        if not generated_id:
            continue
        for source_cluster_id in raw_scope_junction_ids:
            if not source_cluster_id.startswith("cluster_"):
                continue
            mapped_source_members = teacher_join_groups_by_cluster.get(source_cluster_id, [])
            source_members = {
                member
                for member in (
                    mapped_source_members
                    if mapped_source_members
                    else _sumo_cluster_member_ids(source_cluster_id)
                )
                if member in raw_nodes
            }
            if source_members and source_members == set(group):
                cluster_aliases[source_cluster_id] = generated_id
        cluster_aliases.setdefault(generated_id, generated_id)

    def resolve_scope_endpoint(value: str) -> str:
        return cluster_aliases.get(value, value)

    joined_scope_junction_ids = [
        joined_id
        for joined_id in (_sumo_joined_cluster_id(group) for group in join_groups)
        if joined_id
    ]

    raw_edges = [edge for edge in ET.parse(raw_edge_file).getroot() if edge.tag == "edge"]
    selected_edges = [
        edge
        for edge in raw_edges
        if edge.attrib.get("id", "") in blocked_edge_ids
        or edge.attrib.get("from", "") in seed_node_ids
        or edge.attrib.get("to", "") in seed_node_ids
    ]
    selected_edge_ids = {edge.attrib.get("id", "") for edge in selected_edges if edge.attrib.get("id")}
    selected_node_ids = set(seed_node_ids)
    for edge in selected_edges:
        selected_node_ids.update(endpoint for endpoint in (edge.attrib.get("from", ""), edge.attrib.get("to", "")) if endpoint)

    edge_root = ET.Element("edges")
    endpoint_rewrites = _endpoint_rewrites(approach_endpoint_rebuild_plan)
    rewritten_endpoint_count = 0
    skipped_endpoint_rewrites = []
    for edge in selected_edges:
        copied_edge = copy.deepcopy(edge)
        edge_id = copied_edge.attrib.get("id", "")
        for endpoint_attr in ("from", "to"):
            original_endpoint = copied_edge.attrib.get(endpoint_attr, "")
            resolved_endpoint = resolve_scope_endpoint(original_endpoint)
            if resolved_endpoint != original_endpoint:
                copied_edge.set(endpoint_attr, resolved_endpoint)
                rewritten_endpoint_count += 1
        rewrite = endpoint_rewrites.get(edge_id)
        if rewrite is not None:
            desired_from, desired_to = rewrite
            desired_from = resolve_scope_endpoint(desired_from)
            desired_to = resolve_scope_endpoint(desired_to)
            missing_endpoint_ids = [
                node_id
                for node_id in (desired_from, desired_to)
                if node_id not in raw_nodes and node_id not in set(cluster_aliases.values())
            ]
            if missing_endpoint_ids:
                skipped_endpoint_rewrites.append(
                    {
                        "edge_id": edge_id,
                        "desired_from": desired_from,
                        "desired_to": desired_to,
                        "missing_endpoint_ids": missing_endpoint_ids,
                    }
                )
            else:
                copied_edge.set("from", desired_from)
                copied_edge.set("to", desired_to)
                selected_node_ids.update((desired_from, desired_to))
                rewritten_endpoint_count += 1
        edge_root.append(copied_edge)

    join_node_ids = sorted({node_id for group in join_groups for node_id in group})
    if len(join_groups) == 1 and joined_scope_junction_ids:
        joined_scope_junction_id = joined_scope_junction_ids[0]
    elif core_junction_id in raw_nodes:
        joined_scope_junction_id = core_junction_id
    elif joined_scope_junction_ids:
        joined_scope_junction_id = joined_scope_junction_ids[0]
    else:
        joined_scope_junction_id = ""

    node_root = ET.Element("nodes")
    stale_joined_node_ids = set(joined_scope_junction_ids)
    # Remove a source cluster node only when this invocation recreated it as a
    # join group.  If the current plain source contains only the already
    # materialized cluster node (without all source members), preserve it;
    # deleting it would turn a later sequential export into a split cluster.
    stale_joined_node_ids.update(
        cluster_id
        for cluster_id in raw_scope_junction_ids
        if cluster_id.startswith("cluster_") and cluster_id in cluster_aliases
    )
    for node_id in sorted(node_id for node_id in selected_node_ids if node_id in raw_nodes):
        if node_id in stale_joined_node_ids:
            continue
        node_root.append(copy.deepcopy(raw_nodes[node_id]))
    if join_groups:
        join_definition = build_junction_join_definition(
            [
                {
                    "source": "teacher_guided_expanded_scope",
                    "candidate_id": _sumo_joined_cluster_id(group),
                    "decision": "join",
                    # The expanded scope is not a radius guess: it is the
                    # explicit, already selected teacher target for this
                    # materialization.  Mark that narrow decision as confirmed
                    # so the generic join writer does not downgrade it back to
                    # <joinExclude>.
                    "confidence": "target_evidence_confirmed",
                    "node_ids": group,
                    "reason": "teacher-guided expanded scope restores an explicit reference cluster",
                }
                for group in join_groups
            ],
            output_dir=output_dir,
            prefix="expanded_scope",
        )
    elif core_junction_id in raw_nodes:
        join_definition = {}
    else:
        join_definition = {}

    connection_root = ET.Element("connections")
    selected_edge_endpoints = {
        edge.attrib.get("id", ""): (edge.attrib.get("from", ""), edge.attrib.get("to", ""))
        for edge in edge_root.findall("edge")
        if edge.attrib.get("id")
    }
    dropped_connection_count = 0
    for connection in ET.parse(raw_connection_file).getroot():
        if (
            connection.tag == "connection"
            and connection.attrib.get("from", "") in selected_edge_ids
            and connection.attrib.get("to", "") in selected_edge_ids
            and _connection_edges_are_adjacent(connection, selected_edge_endpoints)
        ):
            connection_root.append(copy.deepcopy(connection))
        elif connection.tag == "connection" and (
            connection.attrib.get("from", "") in selected_edge_ids or connection.attrib.get("to", "") in selected_edge_ids
        ):
            dropped_connection_count += 1

    ET.indent(node_root, space="    ")
    ET.indent(edge_root, space="    ")
    ET.indent(connection_root, space="    ")
    ET.ElementTree(node_root).write(node_file, encoding="utf-8", xml_declaration=True)
    ET.ElementTree(edge_root).write(edge_file, encoding="utf-8", xml_declaration=True)
    ET.ElementTree(connection_root).write(connection_file, encoding="utf-8", xml_declaration=True)

    command = [
        netconvert_binary,
        "--node-files",
        ",".join(
            [node_file.name]
            + ([Path(str(join_definition["nodes_patch_file"])).name] if join_definition.get("nodes_patch_file") else [])
        ),
        "--edge-files",
        edge_file.name,
        "--connection-files",
        connection_file.name,
        "--output-file",
        net_file.name,
    ]
    missing_node_ids = sorted(
        node_id
        for node_id in selected_node_ids
        if node_id not in raw_nodes and node_id not in set(joined_scope_junction_ids)
    )
    missing_blocked_edge_ids = sorted(edge_id for edge_id in blocked_edge_ids if edge_id not in selected_edge_ids)
    missing_desired_endpoint_ids = {
        resolve_scope_endpoint(str(item))
        for item in scope.get("missing_desired_endpoint_ids", []) or []
        if str(item)
    }
    blocking_missing_node_ids = sorted(
        node_id
        for node_id in missing_node_ids
        if node_id not in join_node_ids
        and node_id not in join_seed_node_ids
        and node_id not in set(joined_scope_junction_ids)
        and node_id not in missing_desired_endpoint_ids
    )
    netconvert_report = _command_report(command_runner(command, cwd=output_dir, timeout_seconds=timeout_seconds))
    sumo_command = [
        sumo_binary,
        "-n",
        net_file.name,
        "--no-step-log",
        "true",
        "--duration-log.disable",
        "true",
        "--begin",
        "0",
        "--end",
        "1",
    ]
    if netconvert_report.get("status") == "pass":
        sumo_report = _command_report(command_runner(sumo_command, cwd=output_dir, timeout_seconds=timeout_seconds))
    else:
        sumo_report = {"status": "skipped", "reason": "netconvert_failed"}
    joined_scope_junction_missing_from_net = False
    if netconvert_report.get("status") == "pass" and joined_scope_junction_id:
        try:
            joined_scope_junction_missing_from_net = joined_scope_junction_id not in _net_junction_ids(net_file)
        except (ET.ParseError, OSError):
            joined_scope_junction_missing_from_net = True
    blocking_missing_joined_scope_junction_ids = (
        [joined_scope_junction_id] if joined_scope_junction_missing_from_net else []
    )
    probe_status = "pass" if netconvert_report.get("status") == "pass" and sumo_report.get("status") == "pass" else "fail"
    return {
        "status": "review"
        if blocking_missing_node_ids or missing_blocked_edge_ids or blocking_missing_joined_scope_junction_ids
        else probe_status,
        "claim_status": "diagnostic-demo",
        "recommended_action": "run_netconvert_scope_probe",
        "node_file": str(node_file),
        "edge_file": str(edge_file),
        "connection_file": str(connection_file),
        "net_file": str(net_file),
        "netconvert_command": command,
        "sumo_command": sumo_command,
        "netconvert": netconvert_report,
        "sumo_load": sumo_report,
        "join_nodes_patch_file": str(join_definition.get("nodes_patch_file", "")),
        "join_definition_file": str(join_definition.get("definition_file", "")),
        "join_definition_csv": str(join_definition.get("definition_csv", "")),
        "join_explicit_join_count": join_definition.get("explicit_join_count", 0),
        "joined_scope_junction_id": joined_scope_junction_id,
        "joined_scope_junction_ids": joined_scope_junction_ids,
        "seed_node_ids": sorted(seed_node_ids),
        "join_node_ids": sorted({*join_seed_node_ids, *join_node_ids}),
        "join_groups": join_groups,
        "auto_cluster_join_count": auto_cluster_join_count,
        "cluster_aliases": cluster_aliases,
        "blocked_edge_ids": sorted(blocked_edge_ids),
        "missing_node_ids": missing_node_ids,
        "blocking_missing_node_ids": blocking_missing_node_ids,
        "missing_blocked_edge_ids": missing_blocked_edge_ids,
        "joined_scope_junction_missing_from_net": joined_scope_junction_missing_from_net,
        "blocking_missing_joined_scope_junction_ids": blocking_missing_joined_scope_junction_ids,
        "rewritten_endpoint_count": rewritten_endpoint_count,
        "skipped_endpoint_rewrites": skipped_endpoint_rewrites,
        "dropped_connection_count": dropped_connection_count,
        "node_count": len(node_root.findall("node")),
        "edge_count": len(edge_root.findall("edge")),
        "connection_count": len(connection_root.findall("connection")),
    }


def _write_joined_endpoint_edge_file(
    edge_file: Path,
    join_patch_file: Path,
    joined_junction_id: object,
    output_file: Path,
    *,
    rewrite_endpoints: bool = False,
) -> tuple[Path, int, list[str], list[str]]:
    if not join_patch_file.is_file() or not _normalize_joined_junction_ids(joined_junction_id):
        return edge_file, 0, [], []
    source_node_ids = _joined_source_node_ids(join_patch_file, joined_junction_id)
    if not source_node_ids:
        return edge_file, 0, [], []
    endpoint_rewrites = _join_patch_endpoint_rewrites(join_patch_file) if rewrite_endpoints else {}

    edge_root = ET.parse(edge_file).getroot()
    rewrite_count = 0
    dropped_self_loop_edges = []
    blocking_self_loop_edge_drops = []
    for edge in list(edge_root.findall("edge")):
        from_is_join_source = edge.attrib.get("from", "") in source_node_ids
        to_is_join_source = edge.attrib.get("to", "") in source_node_ids
        if from_is_join_source and to_is_join_source:
            edge_id = edge.attrib.get("id", "")
            dropped_self_loop_edges.append(edge_id)
            if _edge_drop_requires_review(edge):
                blocking_self_loop_edge_drops.append(edge_id)
            edge_root.remove(edge)
            continue
        if rewrite_endpoints:
            old_from = edge.attrib.get("from", "")
            old_to = edge.attrib.get("to", "")
            new_from = endpoint_rewrites.get(old_from, old_from)
            new_to = endpoint_rewrites.get(old_to, old_to)
            if new_from != old_from:
                edge.set("from", new_from)
                rewrite_count += 1
            if new_to != old_to:
                edge.set("to", new_to)
                rewrite_count += 1
        # With the default mode netconvert applies the <join> patch after
        # reading node ids.  Teacher replay inputs can opt into explicit
        # endpoint rewriting above.
    if rewrite_count == 0 and not dropped_self_loop_edges:
        return edge_file, 0, [], []

    ET.indent(edge_root, space="    ")
    output_file.parent.mkdir(parents=True, exist_ok=True)
    ET.ElementTree(edge_root).write(output_file, encoding="utf-8", xml_declaration=True)
    return output_file, rewrite_count, dropped_self_loop_edges, blocking_self_loop_edge_drops


def _write_replay_node_file(node_file: Path, join_patch_file: Path, output_file: Path) -> Path:
    if not join_patch_file.is_file():
        return node_file
    node_root = ET.parse(node_file).getroot()
    join_root = ET.parse(join_patch_file).getroot()
    joins = [copy.deepcopy(join) for join in join_root.findall("join")]
    if not joins:
        return node_file
    stale_joined_node_ids = _join_patch_joined_node_ids(join_patch_file)
    for node in list(node_root.findall("node")):
        if node.attrib.get("id") in stale_joined_node_ids:
            node_root.remove(node)
    for join in joins:
        node_root.append(join)
    ET.indent(node_root, space="    ")
    output_file.parent.mkdir(parents=True, exist_ok=True)
    ET.ElementTree(node_root).write(output_file, encoding="utf-8", xml_declaration=True)
    return output_file


def _write_joined_endpoint_connection_file(
    connection_file: Path,
    join_patch_file: Path,
    output_file: Path,
) -> tuple[Path, int]:
    if not join_patch_file.is_file():
        return connection_file, 0
    endpoint_rewrites = _join_patch_endpoint_rewrites(join_patch_file)
    try:
        connection_root = ET.parse(connection_file).getroot()
    except (ET.ParseError, OSError):
        return connection_file, 0
    rewrite_count = 0
    sanitized_crossing_count = 0
    for crossing in connection_root.findall("crossing"):
        old_node = crossing.attrib.get("node", "")
        new_node = endpoint_rewrites.get(old_node, old_node)
        if new_node != old_node:
            crossing.set("node", new_node)
            rewrite_count += 1
        # netconvert plain-output can emit non-finite outlineShape values for
        # crossings.  They are not valid SUMO XML geometry and can abort a
        # later full-network join replay.  Dropping only that optional
        # geometry keeps the crossing semantics and lets SUMO rebuild it.
        for attr in ("outlineShape", "shape"):
            value = crossing.attrib.get(attr, "")
            if value and any(token in value.lower() for token in ("nan", "inf")):
                crossing.attrib.pop(attr, None)
                sanitized_crossing_count += 1
    if not rewrite_count and not sanitized_crossing_count:
        return connection_file, 0
    ET.indent(connection_root, space="    ")
    output_file.parent.mkdir(parents=True, exist_ok=True)
    ET.ElementTree(connection_root).write(output_file, encoding="utf-8", xml_declaration=True)
    return output_file, rewrite_count


def _write_join_scope_connection_file(
    edge_file: Path,
    connection_file: Path,
    join_node_ids: set[str],
    output_file: Path,
    *,
    drop_edge_ids: set[str] | None = None,
) -> tuple[Path, int, list[str]]:
    drop_edge_ids = drop_edge_ids or set()
    if not join_node_ids and not drop_edge_ids:
        return connection_file, 0, []
    incident_edge_ids = {
        edge.attrib["id"]
        for edge in ET.parse(edge_file).getroot().findall("edge")
        if edge.attrib.get("id") and (edge.attrib.get("from") in join_node_ids or edge.attrib.get("to") in join_node_ids)
    }
    if not incident_edge_ids and not drop_edge_ids:
        return connection_file, 0, []
    connection_root = ET.parse(connection_file).getroot()
    filtered_root = ET.Element(connection_root.tag, connection_root.attrib)
    dropped_edge_ids = []
    for connection in connection_root:
        if connection.tag == "crossing":
            crossing_edge_ids = set(connection.attrib.get("edges", "").split())
            if crossing_edge_ids & drop_edge_ids:
                dropped_edge_ids.extend(sorted(edge_id for edge_id in crossing_edge_ids & drop_edge_ids if edge_id))
                continue
        connection_edge_ids = {connection.attrib.get("from", ""), connection.attrib.get("to", "")}
        if connection.tag == "connection" and connection_edge_ids & drop_edge_ids:
            dropped_edge_ids.extend(sorted(edge_id for edge_id in connection_edge_ids & drop_edge_ids if edge_id))
            continue
        if (
            connection.tag == "connection"
            and connection.attrib.get("from", "") in incident_edge_ids
            and not connection.attrib.get("to")
        ):
            dropped_edge_ids.append(connection.attrib.get("from", ""))
            continue
        filtered_root.append(copy.deepcopy(connection))
    if not dropped_edge_ids:
        return connection_file, 0, []
    ET.indent(filtered_root, space="    ")
    output_file.parent.mkdir(parents=True, exist_ok=True)
    ET.ElementTree(filtered_root).write(output_file, encoding="utf-8", xml_declaration=True)
    return output_file, len(dropped_edge_ids), dropped_edge_ids


def _prune_plain_node_controlled_inner_edges(
    node_file: Path,
    drop_edge_ids: set[str],
) -> dict[str, object]:
    """Remove absorbed join-internal edges from plain-node TLS hints.

    ``netconvert --plain-output-prefix`` repeats ``controlledInner`` on every
    node that belongs to a joined controller.  Once a physical micro-edge is
    absorbed by an explicit junction join, leaving that id in the staged node
    file can revive a reference to an edge that no longer exists.
    """

    if not drop_edge_ids:
        return {
            "status": "pass",
            "updated_node_count": 0,
            "removed_edge_reference_count": 0,
            "removed_edge_ids": [],
        }
    try:
        node_tree = ET.parse(node_file)
    except (ET.ParseError, OSError) as exc:
        return {
            "status": "fail",
            "updated_node_count": 0,
            "removed_edge_reference_count": 0,
            "removed_edge_ids": [],
            "error": f"{type(exc).__name__}: {exc}",
        }

    updated_node_count = 0
    removed_edge_reference_count = 0
    removed_edge_ids: list[str] = []
    for node in node_tree.getroot().findall("node"):
        raw_value = node.attrib.get("controlledInner", "")
        if not raw_value:
            continue
        edge_ids = raw_value.split()
        kept = [edge_id for edge_id in edge_ids if edge_id not in drop_edge_ids]
        removed = [edge_id for edge_id in edge_ids if edge_id in drop_edge_ids]
        if not removed:
            continue
        updated_node_count += 1
        removed_edge_reference_count += len(removed)
        removed_edge_ids.extend(removed)
        if kept:
            node.set("controlledInner", " ".join(kept))
        else:
            node.attrib.pop("controlledInner", None)

    if updated_node_count:
        ET.indent(node_tree.getroot(), space="    ")
        node_tree.write(node_file, encoding="utf-8", xml_declaration=True)
    return {
        "status": "pass",
        "updated_node_count": updated_node_count,
        "removed_edge_reference_count": removed_edge_reference_count,
        "removed_edge_ids": sorted(set(removed_edge_ids)),
    }


def _write_join_scope_tllogic_file(
    tllogic_file: Path,
    drop_edge_ids: set[str],
    output_file: Path,
) -> tuple[Path, int, list[str]]:
    """Stage TLS definitions without bindings to absorbed micro-edges."""

    if not drop_edge_ids:
        return tllogic_file, 0, []
    tllogic_tree = ET.parse(tllogic_file)
    root = tllogic_tree.getroot()
    dropped_edge_ids: list[str] = []
    for child in list(root):
        if child.tag != "connection":
            continue
        connection_edge_ids = {
            child.attrib.get("from", ""),
            child.attrib.get("to", ""),
        }
        removed = sorted(
            edge_id
            for edge_id in connection_edge_ids & drop_edge_ids
            if edge_id
        )
        if not removed:
            continue
        root.remove(child)
        dropped_edge_ids.extend(removed)

    if not dropped_edge_ids:
        return tllogic_file, 0, []
    ET.indent(root, space="    ")
    output_file.parent.mkdir(parents=True, exist_ok=True)
    tllogic_tree.write(output_file, encoding="utf-8", xml_declaration=True)
    return output_file, len(dropped_edge_ids), dropped_edge_ids
