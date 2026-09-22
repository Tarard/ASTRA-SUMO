"""Materialize the internal network of one declared teacher target."""

from __future__ import annotations

import copy
from collections import Counter
from pathlib import Path
import xml.etree.ElementTree as ET
from .artifacts import _failure
from .boundary_restore import (
    _append_edge_lanes_to_destination_junction,
    _remove_edge_lanes_from_destination_junction,
    _restore_existing_edge_geometry,
    _restore_external_boundary_connections,
    _restore_joined_split_edge_geometry,
)
from .edge_mapping import (
    _needed_unmapped_teacher_boundary_edges,
    _same_family_continuation_edge_map,
    _teacher_boundary_edge_ids_touching_internal_subgraph,
    _teacher_boundary_edge_needs_replay,
)
from .geometry import (
    _blend_geometry_anchor_at_target,
    _clone_transformed_boundary_edge,
    _clone_transformed_boundary_junction,
    _clone_transformed_junction,
    _clone_transformed_net_element,
    _expand_junction_shape_to_approach_endpoints,
    _geometry_anchor_junctions_by_id,
    _load_geometry_anchor_edges,
    _mapped_connection_attrs,
    _mapped_junction_attrs,
    _mapped_spatial_attrs,
    _restore_geometry_anchor_junctions,
    _translate_shape,
)
from .network import (
    _connection_lane_indices_valid,
    _connection_link_indices,
    _edge_family_id,
    _edge_is_vehicle_continuation_candidate,
    _first_junction_index,
    _net_lane_counts,
    _signed_edge_family_id,
    _touches_target_replay_scope,
)


def write_teacher_target_internal_replay_net(
    *,
    candidate_net_file: Path,
    teacher_net_file: Path,
    output_file: Path,
    junction_id: str,
    edge_map: dict[str, str],
    teacher_junction_id: str | None = None,
    geometry_anchor_edge_file: Path | None = None,
    blend_geometry_anchor_at_target: bool = False,
    copy_unmapped_boundary_edges: bool = True,
    preserve_mapped_boundary_endpoints: bool = False,
    preserve_target_junction_shape: bool = False,
) -> dict[str, object]:
    teacher_junction_id = teacher_junction_id or junction_id
    output_file.parent.mkdir(parents=True, exist_ok=True)

    candidate_tree = ET.parse(candidate_net_file)
    candidate_root = candidate_tree.getroot()
    source_candidate_root = copy.deepcopy(candidate_root)
    teacher_root = ET.parse(teacher_net_file).getroot()
    internal_prefix = f":{junction_id}_"
    teacher_internal_prefix = f":{teacher_junction_id}_"
    candidate_edges_by_id = {edge.attrib["id"]: edge for edge in candidate_root.findall("edge") if edge.attrib.get("id")}
    candidate_edge_ids = set(candidate_edges_by_id)
    replay_edge_map = dict(edge_map)
    geometry_anchor_edges_by_id = _load_geometry_anchor_edges(geometry_anchor_edge_file)
    geometry_anchor_edge_ids = set(geometry_anchor_edges_by_id)

    target_candidate_junction = candidate_root.find(f"junction[@id='{junction_id}']")
    teacher_junction = teacher_root.find(f"junction[@id='{teacher_junction_id}']")
    if target_candidate_junction is None:
        return _failure(f"candidate junction not found: {junction_id}")
    if teacher_junction is None:
        return _failure(f"teacher junction not found: {junction_id}")
    original_target_junction_shape = target_candidate_junction.attrib.get("shape")
    original_target_custom_shape = target_candidate_junction.attrib.get("customShape")

    dx = float(target_candidate_junction.attrib.get("x", "0") or 0) - float(
        teacher_junction.attrib.get("x", "0") or 0
    )
    dy = float(target_candidate_junction.attrib.get("y", "0") or 0) - float(
        teacher_junction.attrib.get("y", "0") or 0
    )

    removed_internal_edges = []
    insert_index = None
    for child in list(candidate_root):
        if child.tag == "edge" and child.attrib.get("id", "").startswith(internal_prefix):
            if insert_index is None:
                insert_index = list(candidate_root).index(child)
            removed_internal_edges.append(child.attrib.get("id", ""))
            candidate_root.remove(child)
    if insert_index is None:
        insert_index = _first_junction_index(candidate_root)

    teacher_internal_edges = [
        edge
        for edge in teacher_root.findall("edge")
        if edge.attrib.get("id", "").startswith(teacher_internal_prefix)
    ]
    copied_boundary_edges = []
    copied_boundary_candidate_edges = []
    blended_geometry_anchor_edge_ids = []
    skipped_boundary_edges = []
    preserved_mapped_boundary_endpoints = []
    replaced_boundary_edge_ids: set[str] = set()
    boundary_insert_offset = 0
    teacher_edges = {edge.attrib["id"]: edge for edge in teacher_root.findall("edge") if edge.attrib.get("id")}
    teacher_junctions = {
        junction.attrib["id"]: junction
        for junction in teacher_root.findall("junction")
        if junction.attrib.get("id")
    }
    candidate_junctions_by_id = {
        junction.attrib["id"]: junction for junction in candidate_root.findall("junction") if junction.attrib.get("id")
    }
    candidate_junction_ids = set(candidate_junctions_by_id)
    geometry_anchor_junctions_by_id: dict[str, ET.Element] = {}
    copied_boundary_junctions = []
    replaced_boundary_source_edges: dict[str, ET.Element] = {}
    needed_boundary_edge_ids = _needed_unmapped_teacher_boundary_edges(
        teacher_root.findall("connection"),
        teacher_edges,
        replay_edge_map,
        candidate_edges_by_id,
        teacher_internal_prefix,
        teacher_junction_id,
        junction_id,
        dx,
        dy,
    )
    teacher_boundary_edge_ids = _teacher_boundary_edge_ids_touching_internal_subgraph(
        teacher_root.findall("connection"),
        teacher_edges,
        teacher_junction_id,
    )
    teacher_boundary_edge_ids = list(
        dict.fromkeys(
            [
                *teacher_boundary_edge_ids,
                *[
                    edge_id
                    for edge_id, edge in teacher_edges.items()
                    if teacher_junction_id in (edge.attrib.get("from"), edge.attrib.get("to"))
                ],
            ]
        )
    )
    for edge_id in teacher_boundary_edge_ids:
        teacher_edge = teacher_edges.get(edge_id)
        if (
            edge_id not in replay_edge_map
            and teacher_edge is not None
            and edge_id in candidate_edges_by_id
            and not _teacher_boundary_edge_needs_replay(
                teacher_edge,
                replay_edge_map,
                candidate_edges_by_id,
                teacher_junction_id,
                junction_id,
                dx,
                dy,
            )
        ):
            replay_edge_map[edge_id] = edge_id
    teacher_boundary_edge_id_set = set(teacher_boundary_edge_ids)
    teacher_boundary_mapped_counts = Counter(replay_edge_map.get(edge_id, edge_id) for edge_id in teacher_boundary_edge_ids)
    needed_boundary_edge_ids = list(
        dict.fromkeys(
            [
                *needed_boundary_edge_ids,
                *[
                    edge_id
                    for edge_id in teacher_boundary_edge_ids
                    if _teacher_boundary_edge_needs_replay(
                        teacher_edges[edge_id],
                        replay_edge_map,
                        candidate_edges_by_id,
                        teacher_junction_id,
                        junction_id,
                        dx,
                        dy,
                    )
                ],
                *[
                    edge_id
                    for edge_id in teacher_boundary_edge_ids
                    if (
                        replay_edge_map.get(edge_id, edge_id) in teacher_boundary_edge_id_set
                        and replay_edge_map.get(edge_id, edge_id) != edge_id
                    )
                    or teacher_boundary_mapped_counts[replay_edge_map.get(edge_id, edge_id)] > 1
                ],
            ]
        )
    )
    skipped_unmapped_teacher_boundary_edges = []
    if not copy_unmapped_boundary_edges:
        skipped_unmapped_teacher_boundary_edges = [
            edge_id
            for edge_id in needed_boundary_edge_ids
            if edge_id not in replay_edge_map
        ]
        needed_boundary_edge_ids = [
            edge_id
            for edge_id in needed_boundary_edge_ids
            if edge_id in replay_edge_map
        ]
    needed_boundary_edge_id_set = set(needed_boundary_edge_ids)
    mapped_boundary_counts = Counter(replay_edge_map.get(edge_id, edge_id) for edge_id in needed_boundary_edge_ids)
    preserved_colliding_boundary_edges = []
    for edge_id in needed_boundary_edge_ids:
        mapped_edge_id = replay_edge_map.get(edge_id, edge_id)
        if mapped_edge_id == edge_id:
            continue
        if mapped_edge_id in needed_boundary_edge_id_set or mapped_boundary_counts[mapped_edge_id] > 1:
            replay_edge_map[edge_id] = edge_id
            preserved_colliding_boundary_edges.append(edge_id)
    same_family_continuation_edge_map = _same_family_continuation_edge_map(
        teacher_edges,
        candidate_edges_by_id,
        replay_edge_map,
        teacher_junction_id=teacher_junction_id,
        candidate_junction_id=junction_id,
    )
    replay_edge_map.update(same_family_continuation_edge_map)
    for edge_id in needed_boundary_edge_ids:
        teacher_edge = teacher_edges[edge_id]
        mapped_from = junction_id if teacher_edge.attrib.get("from") == teacher_junction_id else teacher_edge.attrib.get("from", "")
        mapped_to = junction_id if teacher_edge.attrib.get("to") == teacher_junction_id else teacher_edge.attrib.get("to", "")
        mapped_candidate_edge_id = replay_edge_map.get(edge_id, edge_id)
        mapped_candidate_edge = candidate_edges_by_id.get(mapped_candidate_edge_id)
        if (
            mapped_candidate_edge is not None
            and not copy_unmapped_boundary_edges
            and preserve_mapped_boundary_endpoints
            and junction_id
            in (
            mapped_candidate_edge.attrib.get("from"),
            mapped_candidate_edge.attrib.get("to"),
            )
            and (
                (
                    teacher_edge.attrib.get("from") == teacher_junction_id
                    and mapped_candidate_edge.attrib.get("from") == junction_id
                )
                or (
                    teacher_edge.attrib.get("to") == teacher_junction_id
                    and mapped_candidate_edge.attrib.get("to") == junction_id
                )
            )
        ):
            candidate_from = mapped_candidate_edge.attrib.get("from", "")
            candidate_to = mapped_candidate_edge.attrib.get("to", "")
            if (mapped_from, mapped_to) != (candidate_from, candidate_to):
                preserved_mapped_boundary_endpoints.append(
                    {
                        "teacher_edge_id": edge_id,
                        "candidate_edge_id": mapped_candidate_edge_id,
                        "teacher_mapped_from": mapped_from,
                        "teacher_mapped_to": mapped_to,
                        "candidate_from": candidate_from,
                        "candidate_to": candidate_to,
                    }
                )
            mapped_from = candidate_from
            mapped_to = candidate_to
        for teacher_endpoint, mapped_endpoint in (
            (teacher_edge.attrib.get("from", ""), mapped_from),
            (teacher_edge.attrib.get("to", ""), mapped_to),
        ):
            if mapped_endpoint in candidate_junction_ids:
                continue
            teacher_endpoint_junction = teacher_junctions.get(teacher_endpoint)
            if teacher_endpoint_junction is None:
                continue
            copied_junction = _clone_transformed_boundary_junction(
                teacher_endpoint_junction,
                dx,
                dy,
                replay_edge_map,
                teacher_junction_id,
                junction_id,
            )
            candidate_root.insert(list(candidate_root).index(target_candidate_junction), copied_junction)
            candidate_junction_ids.add(mapped_endpoint)
            copied_boundary_junctions.append(mapped_endpoint)
        if mapped_from not in candidate_junction_ids or mapped_to not in candidate_junction_ids:
            skipped_boundary_edges.append(edge_id)
            continue
        copied_edge_id = replay_edge_map.get(edge_id, edge_id)
        copied_edge = _clone_transformed_boundary_edge(
            teacher_edge,
            copied_edge_id,
            dx,
            dy,
            replay_edge_map,
            teacher_junction_id,
            junction_id,
        )
        copied_edge.set("from", mapped_from)
        copied_edge.set("to", mapped_to)
        copied_edge_id = copied_edge.attrib.get("id", "")
        if not copied_edge_id:
            skipped_boundary_edges.append(edge_id)
            continue
        replaced_edge = candidate_edges_by_id.get(copied_edge_id)
        insert_at = insert_index + boundary_insert_offset
        if replaced_edge is not None:
            replaced_boundary_source_edges[copied_edge_id] = copy.deepcopy(replaced_edge)
            if copied_edge_id in geometry_anchor_edge_ids:
                geometry_source_edge = geometry_anchor_edges_by_id.get(
                    copied_edge_id,
                    replaced_edge,
                )
                if blend_geometry_anchor_at_target and _blend_geometry_anchor_at_target(
                    copied_edge,
                    geometry_source_edge,
                    junction_id,
                ):
                    blended_geometry_anchor_edge_ids.append(copied_edge_id)
                else:
                    _restore_existing_edge_geometry(
                        copied_edge,
                        geometry_source_edge,
                        candidate_root,
                        max_endpoint_delta=None,
                    )
            insert_at = list(candidate_root).index(replaced_edge)
            _remove_edge_lanes_from_destination_junction(candidate_root, replaced_edge)
            candidate_root.remove(replaced_edge)
            candidate_edge_ids.remove(copied_edge_id)
            replaced_boundary_edge_ids.add(copied_edge_id)
        candidate_root.insert(insert_at, copied_edge)
        if replaced_edge is None:
            boundary_insert_offset += 1
        candidate_edge_ids.add(copied_edge_id)
        candidate_edges_by_id[copied_edge_id] = copied_edge
        replay_edge_map[edge_id] = copied_edge_id
        _append_edge_lanes_to_destination_junction(candidate_root, copied_edge)
        copied_boundary_edges.append(edge_id)
        copied_boundary_candidate_edges.append(copied_edge_id)

    removed_stale_split_fragment_edges = []
    rewired_stale_split_fragment_connections = []
    stale_split_replacements: dict[str, tuple[str, str]] = {}
    stale_split_continuation_replacements: dict[str, str] = {}
    stale_split_remote_junction_ids: set[str] = set()
    stale_split_stale_junction_ids: set[str] = set()
    teacher_connections_by_via = {
        connection.attrib["via"]: connection
        for connection in teacher_root.findall("connection")
        if connection.attrib.get("via")
    }
    teacher_tllogic_ids = {tllogic.attrib.get("id", "") for tllogic in teacher_root.findall("tlLogic")}
    replay_boundary_candidate_edge_ids = list(
        dict.fromkeys(
            [
                *copied_boundary_candidate_edges,
                *[
                    replay_edge_map.get(edge_id, edge_id)
                    for edge_id in teacher_boundary_edge_ids
                    if replay_edge_map.get(edge_id, edge_id) in candidate_edges_by_id
                ],
            ]
        )
    )
    for edge_id in replay_boundary_candidate_edge_ids:
        copied_edge = candidate_edges_by_id.get(edge_id)
        if copied_edge is None:
            continue
        remote_attr = "to" if copied_edge.attrib.get("from") == junction_id else "from"
        remote_junction_id = copied_edge.attrib.get(remote_attr, "")
        if not remote_junction_id:
            continue
        copied_family = _signed_edge_family_id(edge_id)
        for candidate_edge_id, candidate_edge in list(candidate_edges_by_id.items()):
            if (
                candidate_edge_id == edge_id
                or candidate_edge_id.startswith(":")
                or _signed_edge_family_id(candidate_edge_id) != copied_family
                or candidate_edge.attrib.get(remote_attr) != remote_junction_id
            ):
                continue
            stale_split_replacements[candidate_edge_id] = (edge_id, remote_junction_id)
            source_edge = replaced_boundary_source_edges.get(edge_id)
            source_anchor_edge = geometry_anchor_edges_by_id.get(edge_id)
            if source_edge is not None and source_anchor_edge is not None:
                source_edge = copy.deepcopy(source_edge)
                _restore_existing_edge_geometry(
                    source_edge,
                    source_anchor_edge,
                    candidate_root,
                    max_endpoint_delta=None,
                )
            stale_split_edge = candidate_edge
            stale_split_anchor_edge = geometry_anchor_edges_by_id.get(
                candidate_edge_id
            )
            if stale_split_anchor_edge is not None:
                stale_split_edge = copy.deepcopy(candidate_edge)
                _restore_existing_edge_geometry(
                    stale_split_edge,
                    stale_split_anchor_edge,
                    candidate_root,
                    max_endpoint_delta=None,
                )
            if (
                source_edge is not None
                and {candidate_edge_id, edge_id} & geometry_anchor_edge_ids
                and _restore_joined_split_edge_geometry(
                    copied_edge,
                    stale_split_edge,
                    source_edge,
                )
            ):
                geometry_anchor_junctions_by_id.update(
                    _geometry_anchor_junctions_by_id(
                        {
                            candidate_edge_id: candidate_edge,
                            edge_id: source_edge,
                        },
                        candidate_junctions_by_id,
                        {candidate_edge_id, edge_id} & geometry_anchor_edge_ids,
                        target_junction_id=junction_id,
                    )
                )
            stale_split_remote_junction_ids.add(remote_junction_id)
            stale_endpoint_attr = "to" if remote_attr == "from" else "from"
            stale_junction_id = candidate_edge.attrib.get(stale_endpoint_attr, "")
            if stale_junction_id:
                stale_split_stale_junction_ids.add(stale_junction_id)
    for connection in list(candidate_root.findall("connection")):
        touched_stale_edge_ids = {
            edge_id
            for edge_id in (connection.attrib.get("from", ""), connection.attrib.get("to", ""))
            if edge_id in stale_split_replacements
        }
        if not touched_stale_edge_ids:
            continue
        if len(touched_stale_edge_ids) != 1:
            candidate_root.remove(connection)
            continue
        stale_edge_id = next(iter(touched_stale_edge_ids))
        replacement_edge_id, remote_junction_id = stale_split_replacements[stale_edge_id]
        original_from = connection.attrib.get("from", "")
        original_to = connection.attrib.get("to", "")
        stale_was_from = original_from == stale_edge_id
        stale_was_to = original_to == stale_edge_id
        if not connection.attrib.get("via", "").startswith(f":{remote_junction_id}_"):
            candidate_root.remove(connection)
            continue
        if stale_was_from:
            connection.set("from", replacement_edge_id)
        if stale_was_to:
            connection.set("to", replacement_edge_id)
        teacher_connection = teacher_connections_by_via.get(connection.attrib.get("via", ""))
        if teacher_connection is not None:
            candidate_continuation_edge_id = original_to if stale_was_from else original_from if stale_was_to else ""
            teacher_continuation_edge_id = (
                teacher_connection.attrib.get("to", "")
                if stale_was_from
                else teacher_connection.attrib.get("from", "")
                if stale_was_to
                else ""
            )
            existing_continuation_mapping = replay_edge_map.get(teacher_continuation_edge_id)
            if (
                candidate_continuation_edge_id
                and teacher_continuation_edge_id
                and candidate_continuation_edge_id != replacement_edge_id
                and candidate_continuation_edge_id not in stale_split_replacements
                and not candidate_continuation_edge_id.startswith(":")
                and candidate_continuation_edge_id in candidate_edges_by_id
                and teacher_continuation_edge_id in teacher_edges
                and _signed_edge_family_id(candidate_continuation_edge_id)
                == _signed_edge_family_id(teacher_continuation_edge_id)
                and (
                    existing_continuation_mapping is None
                    or existing_continuation_mapping == candidate_continuation_edge_id
                )
            ):
                stale_split_continuation_replacements[candidate_continuation_edge_id] = teacher_continuation_edge_id
                replay_edge_map[teacher_continuation_edge_id] = candidate_continuation_edge_id
            rewritten_from = connection.attrib.get("from", "")
            rewritten_to = connection.attrib.get("to", "")
            connection.attrib.clear()
            connection.attrib.update(dict(teacher_connection.attrib))
            connection.set("from", rewritten_from)
            connection.set("to", rewritten_to)
        rewired_stale_split_fragment_connections.append(dict(connection.attrib))
    for stale_edge_id in stale_split_replacements:
        stale_edge = candidate_edges_by_id.get(stale_edge_id)
        if stale_edge is None:
            continue
        _remove_edge_lanes_from_destination_junction(candidate_root, stale_edge, all_junctions=True)
        candidate_root.remove(stale_edge)
        candidate_edge_ids.discard(stale_edge_id)
        candidate_edges_by_id.pop(stale_edge_id, None)
        removed_stale_split_fragment_edges.append(stale_edge_id)
    stale_split_spatial_junction_ids = set(stale_split_remote_junction_ids)
    removed_cluster_member_residual_edges = []
    removed_cluster_member_residual_connections = []
    removed_cluster_member_residual_junctions = []
    cluster_member_residual_junction_ids = set()
    if junction_id.startswith("cluster_"):
        cluster_member_residual_junction_ids = {
            member_id
            for member_id in junction_id.removeprefix("cluster_").split("_")
            if member_id and member_id in candidate_junction_ids and member_id not in teacher_junctions
        }
    if cluster_member_residual_junction_ids:
        covered_boundary_families = {
            _signed_edge_family_id(edge_id)
            for edge_id in replay_boundary_candidate_edge_ids
            if edge_id and not edge_id.startswith(":")
        }
        protected_replay_edge_ids = {edge_id for edge_id in replay_edge_map.values() if edge_id}
        removable_member_edges = [
            edge_id
            for edge_id, edge in sorted(candidate_edges_by_id.items())
            if (
                edge_id not in protected_replay_edge_ids
                and edge_id not in teacher_edges
                and not edge_id.startswith(":")
                and edge.attrib.get("function") not in {"internal", "crossing", "walkingarea"}
                and _signed_edge_family_id(edge_id) in covered_boundary_families
                and (
                    edge.attrib.get("from", "") in cluster_member_residual_junction_ids
                    or edge.attrib.get("to", "") in cluster_member_residual_junction_ids
                )
            )
        ]
        for edge_id in removable_member_edges:
            edge = candidate_edges_by_id.get(edge_id)
            if edge is None:
                continue
            for connection in list(candidate_root.findall("connection")):
                if edge_id in (connection.attrib.get("from", ""), connection.attrib.get("to", "")):
                    removed_cluster_member_residual_connections.append(dict(connection.attrib))
                    candidate_root.remove(connection)
            _remove_edge_lanes_from_destination_junction(candidate_root, edge, all_junctions=True)
            candidate_root.remove(edge)
            candidate_edge_ids.discard(edge_id)
            candidate_edges_by_id.pop(edge_id, None)
            removed_cluster_member_residual_edges.append(edge_id)
        if removed_cluster_member_residual_edges:
            stale_split_spatial_junction_ids.update(cluster_member_residual_junction_ids)
    replayed_stale_split_continuation_edges = []
    replayed_stale_split_teacher_edge_ids: set[str] = set()

    def replay_stale_split_edge_geometry(candidate_edge_id: str, teacher_edge_id: str) -> bool:
        existing_edge = candidate_edges_by_id.get(candidate_edge_id)
        teacher_edge = teacher_edges.get(teacher_edge_id)
        if existing_edge is None or teacher_edge is None:
            return False
        copied_edge = _clone_transformed_boundary_edge(
            teacher_edge,
            candidate_edge_id,
            dx,
            dy,
            replay_edge_map,
            teacher_junction_id,
            junction_id,
        )
        if candidate_edge_id in geometry_anchor_edge_ids:
            _restore_existing_edge_geometry(
                copied_edge,
                geometry_anchor_edges_by_id.get(candidate_edge_id, existing_edge),
                candidate_root,
                max_endpoint_delta=None,
            )
        insert_at = list(candidate_root).index(existing_edge)
        _remove_edge_lanes_from_destination_junction(candidate_root, existing_edge, all_junctions=True)
        candidate_root.remove(existing_edge)
        candidate_root.insert(insert_at, copied_edge)
        candidate_edge_ids.add(candidate_edge_id)
        candidate_edges_by_id[candidate_edge_id] = copied_edge
        _append_edge_lanes_to_destination_junction(candidate_root, copied_edge)
        for endpoint in (copied_edge.attrib.get("from", ""), copied_edge.attrib.get("to", "")):
            if endpoint and endpoint != junction_id:
                stale_split_spatial_junction_ids.add(endpoint)
        return True

    for candidate_edge_id, teacher_edge_id in sorted(stale_split_continuation_replacements.items()):
        if replay_stale_split_edge_geometry(candidate_edge_id, teacher_edge_id):
            replayed_stale_split_continuation_edges.append(candidate_edge_id)
            replayed_stale_split_teacher_edge_ids.add(teacher_edge_id)
    replayed_stale_split_followup_edges = []
    for teacher_edge_id in sorted(replayed_stale_split_teacher_edge_ids):
        for teacher_connection in teacher_root.findall("connection"):
            if teacher_connection.attrib.get("dir") == "t":
                continue
            from_edge_id = teacher_connection.attrib.get("from", "")
            to_edge_id = teacher_connection.attrib.get("to", "")
            if from_edge_id == teacher_edge_id:
                followup_teacher_edge_id = to_edge_id
            elif to_edge_id == teacher_edge_id:
                followup_teacher_edge_id = from_edge_id
            else:
                continue
            if (
                not followup_teacher_edge_id
                or followup_teacher_edge_id in replay_edge_map
                or followup_teacher_edge_id in teacher_boundary_edge_id_set
                or followup_teacher_edge_id.startswith(":")
            ):
                continue
            followup_teacher_edge = teacher_edges.get(followup_teacher_edge_id)
            followup_candidate_edge_id = followup_teacher_edge_id
            followup_candidate_edge = candidate_edges_by_id.get(followup_candidate_edge_id)
            if (
                followup_teacher_edge is None
                or followup_candidate_edge is None
                or not (
                    {
                        followup_candidate_edge.attrib.get("from", ""),
                        followup_candidate_edge.attrib.get("to", ""),
                    }
                    & stale_split_spatial_junction_ids
                )
                or not _edge_is_vehicle_continuation_candidate(followup_teacher_edge)
            ):
                continue
            replay_edge_map[followup_teacher_edge_id] = followup_candidate_edge_id
            if replay_stale_split_edge_geometry(followup_candidate_edge_id, followup_teacher_edge_id):
                replayed_stale_split_followup_edges.append(followup_candidate_edge_id)
    removed_teacher_absent_same_family_continuation_edges = []
    removed_teacher_absent_same_family_continuation_connections = []
    removed_teacher_absent_same_family_continuation_junctions = []
    replayed_stale_split_family_ids = {
        _edge_family_id(edge_id)
        for edge_id in [*replayed_stale_split_continuation_edges, *replayed_stale_split_followup_edges]
    }
    protected_candidate_edge_ids = set(replay_edge_map.values()) | {
        edge_id for edge_id in candidate_edges_by_id if edge_id in teacher_edges
    }
    stale_split_frontier_junction_ids = set(stale_split_spatial_junction_ids)
    removed_stale_split_dead_end_edges = []
    removed_stale_split_dead_end_connections = []
    teacher_dead_end_junction_ids = {
        endpoint
        for endpoint in stale_split_spatial_junction_ids
        if teacher_junctions.get(endpoint) is not None and teacher_junctions[endpoint].attrib.get("type") == "dead_end"
    }
    for edge_id, edge in list(candidate_edges_by_id.items()):
        if (
            edge_id in protected_candidate_edge_ids
            or edge_id.startswith(":")
            or edge_id in teacher_edges
            or _edge_family_id(edge_id) in replayed_stale_split_family_ids
            or not (
                edge.attrib.get("from", "") in teacher_dead_end_junction_ids
                or edge.attrib.get("to", "") in teacher_dead_end_junction_ids
            )
        ):
            continue
        for connection in list(candidate_root.findall("connection")):
            if edge_id in (connection.attrib.get("from", ""), connection.attrib.get("to", "")):
                removed_stale_split_dead_end_connections.append(dict(connection.attrib))
                candidate_root.remove(connection)
        _remove_edge_lanes_from_destination_junction(candidate_root, edge, all_junctions=True)
        candidate_root.remove(edge)
        candidate_edge_ids.discard(edge_id)
        candidate_edges_by_id.pop(edge_id, None)
        removed_stale_split_dead_end_edges.append(edge_id)
        for endpoint in (edge.attrib.get("from", ""), edge.attrib.get("to", "")):
            if endpoint:
                stale_split_frontier_junction_ids.add(endpoint)
                stale_split_spatial_junction_ids.add(endpoint)
    for endpoint in teacher_dead_end_junction_ids:
        teacher_dead_end_junction = teacher_junctions.get(endpoint)
        candidate_dead_end_junction = candidate_root.find(f"junction[@id='{endpoint}']")
        if teacher_dead_end_junction is not None and candidate_dead_end_junction is not None:
            candidate_dead_end_junction.set("intLanes", teacher_dead_end_junction.attrib.get("intLanes", ""))
    while replayed_stale_split_family_ids:
        removable_edge_ids = [
            edge_id
            for edge_id, edge in sorted(candidate_edges_by_id.items())
            if (
                edge_id not in protected_candidate_edge_ids
                and not edge_id.startswith(":")
                and _edge_family_id(edge_id) in replayed_stale_split_family_ids
                and (
                    edge.attrib.get("from", "") in stale_split_frontier_junction_ids
                    or edge.attrib.get("to", "") in stale_split_frontier_junction_ids
                )
            )
        ]
        if not removable_edge_ids:
            break
        for edge_id in removable_edge_ids:
            edge = candidate_edges_by_id.get(edge_id)
            if edge is None:
                continue
            for connection in list(candidate_root.findall("connection")):
                if edge_id in (connection.attrib.get("from", ""), connection.attrib.get("to", "")):
                    removed_teacher_absent_same_family_continuation_connections.append(dict(connection.attrib))
                    candidate_root.remove(connection)
            _remove_edge_lanes_from_destination_junction(candidate_root, edge, all_junctions=True)
            candidate_root.remove(edge)
            candidate_edge_ids.discard(edge_id)
            candidate_edges_by_id.pop(edge_id, None)
            removed_teacher_absent_same_family_continuation_edges.append(edge_id)
            for endpoint in (edge.attrib.get("from", ""), edge.attrib.get("to", "")):
                if endpoint:
                    stale_split_frontier_junction_ids.add(endpoint)
                    stale_split_spatial_junction_ids.add(endpoint)
    replayed_stale_split_context_edges = []
    replayed_stale_split_context_edge_ids = {
        *replayed_stale_split_continuation_edges,
        *replayed_stale_split_followup_edges,
    }
    context_frontier_junction_ids = set(teacher_dead_end_junction_ids)
    while context_frontier_junction_ids:
        replayed_context_this_pass = False
        for teacher_edge_id, teacher_edge in sorted(teacher_edges.items()):
            candidate_edge_id = replay_edge_map.get(teacher_edge_id, teacher_edge_id)
            context_candidate_edge = candidate_edges_by_id.get(candidate_edge_id)
            if (
                candidate_edge_id in replayed_stale_split_context_edge_ids
                or context_candidate_edge is None
                or teacher_edge_id.startswith(":")
                or teacher_edge.attrib.get("function") in {"internal", "crossing", "walkingarea"}
                or not (
                    teacher_edge.attrib.get("from", "") in context_frontier_junction_ids
                    or teacher_edge.attrib.get("to", "") in context_frontier_junction_ids
                )
                or not (
                    {
                        context_candidate_edge.attrib.get("from", ""),
                        context_candidate_edge.attrib.get("to", ""),
                    }
                    & context_frontier_junction_ids
                )
            ):
                continue
            replay_edge_map[teacher_edge_id] = candidate_edge_id
            if replay_stale_split_edge_geometry(candidate_edge_id, teacher_edge_id):
                replayed_stale_split_context_edge_ids.add(candidate_edge_id)
                replayed_stale_split_context_edges.append(candidate_edge_id)
                copied_edge = candidate_edges_by_id.get(candidate_edge_id)
                if copied_edge is not None:
                    for endpoint in (copied_edge.attrib.get("from", ""), copied_edge.attrib.get("to", "")):
                        if endpoint:
                            context_frontier_junction_ids.add(endpoint)
                            stale_split_frontier_junction_ids.add(endpoint)
                            stale_split_spatial_junction_ids.add(endpoint)
                replayed_context_this_pass = True
        if not replayed_context_this_pass:
            break
    for local_candidate_junction in list(candidate_root.findall("junction")):
        candidate_junction_id = local_candidate_junction.attrib.get("id", "")
        if (
            not candidate_junction_id
            or candidate_junction_id == junction_id
            or candidate_junction_id in teacher_junctions
            or candidate_junction_id not in stale_split_frontier_junction_ids
            or any(
                edge.attrib.get("from") == candidate_junction_id or edge.attrib.get("to") == candidate_junction_id
                for edge in candidate_edges_by_id.values()
            )
        ):
            continue
        orphan_internal_prefix = f":{candidate_junction_id}_"
        for connection in list(candidate_root.findall("connection")):
            if connection.attrib.get("via", "").startswith(orphan_internal_prefix) or any(
                value.startswith(orphan_internal_prefix)
                for value in (connection.attrib.get("from", ""), connection.attrib.get("to", ""))
            ):
                candidate_root.remove(connection)
        for edge_id, edge in list(candidate_edges_by_id.items()):
            if edge_id.startswith(orphan_internal_prefix):
                candidate_root.remove(edge)
                candidate_edge_ids.discard(edge_id)
                candidate_edges_by_id.pop(edge_id, None)
        candidate_root.remove(local_candidate_junction)
        candidate_junction_ids.discard(candidate_junction_id)
        removed_teacher_absent_same_family_continuation_junctions.append(candidate_junction_id)
        if candidate_junction_id in cluster_member_residual_junction_ids:
            removed_cluster_member_residual_junctions.append(candidate_junction_id)
    retuned_stale_split_junction_ids = []
    for remote_junction_id in sorted(stale_split_spatial_junction_ids):
        remote_teacher_junction = teacher_junctions.get(remote_junction_id)
        remote_candidate_junction = candidate_root.find(f"junction[@id='{remote_junction_id}']")
        if remote_teacher_junction is None or remote_candidate_junction is None:
            continue
        mapped_spatial_attrs = _mapped_spatial_attrs(
            remote_teacher_junction.attrib,
            dx,
            dy,
            replay_edge_map,
            remote_junction_id,
            remote_junction_id,
        )
        changed = False
        for attr in (
            "type",
            "x",
            "y",
            "z",
            "shape",
            "outlineShape",
            "customShape",
            "radius",
            "keepClear",
            "rightOfWay",
            "fringe",
            "roundabout",
            "name",
            "tlType",
            "tlLayout",
        ):
            if attr not in mapped_spatial_attrs:
                continue
            if remote_candidate_junction.attrib.get(attr) != mapped_spatial_attrs[attr]:
                remote_candidate_junction.set(attr, mapped_spatial_attrs[attr])
                changed = True
        if changed:
            retuned_stale_split_junction_ids.append(remote_junction_id)
    stripped_stale_split_tls_connections = []
    for connection in candidate_root.findall("connection"):
        tl_id = connection.attrib.get("tl", "")
        if tl_id not in stale_split_stale_junction_ids or tl_id in teacher_tllogic_ids:
            continue
        connection.attrib.pop("tl", None)
        connection.attrib.pop("linkIndex", None)
        if connection.attrib.get("state") == "O":
            connection.set("state", "M")
        elif connection.attrib.get("state") == "o":
            connection.set("state", "m")
        stripped_stale_split_tls_connections.append(dict(connection.attrib))
    removed_stale_split_tllogic_ids = []
    for tllogic in list(candidate_root.findall("tlLogic")):
        tllogic_id = tllogic.attrib.get("id", "")
        if tllogic_id in stale_split_stale_junction_ids and tllogic_id not in teacher_tllogic_ids:
            candidate_root.remove(tllogic)
            removed_stale_split_tllogic_ids.append(tllogic_id)

    copied_boundary_continuation_edges = []
    copied_boundary_continuation_connections = []
    if copied_boundary_edges:
        copied_boundary_edge_ids = set(copied_boundary_edges)
        continuation_edge_ids = []
        for connection in teacher_root.findall("connection"):
            from_edge_id = connection.attrib.get("from", "")
            to_edge_id = connection.attrib.get("to", "")
            for boundary_edge_id, continuation_edge_id in (
                (from_edge_id, to_edge_id),
                (to_edge_id, from_edge_id),
            ):
                if boundary_edge_id not in copied_boundary_edge_ids:
                    continue
                continuation_edge = teacher_edges.get(continuation_edge_id)
                boundary_edge = teacher_edges.get(boundary_edge_id)
                if (
                    continuation_edge is None
                    or boundary_edge is None
                    or continuation_edge_id in stale_split_replacements
                    or continuation_edge_id in candidate_edges_by_id
                    or continuation_edge_id in teacher_boundary_edge_id_set
                    or continuation_edge_id.startswith(":")
                    or not _edge_is_vehicle_continuation_candidate(continuation_edge)
                ):
                    continue
                shared_endpoints = {
                    boundary_edge.attrib.get("from", ""),
                    boundary_edge.attrib.get("to", ""),
                } & {
                    continuation_edge.attrib.get("from", ""),
                    continuation_edge.attrib.get("to", ""),
                }
                if not shared_endpoints or teacher_junction_id in shared_endpoints:
                    continue
                continuation_edge_ids.append(continuation_edge_id)
        continuation_edge_ids = list(dict.fromkeys(continuation_edge_ids))
        continuation_insert_at = _first_junction_index(candidate_root)
        for edge_id in continuation_edge_ids:
            teacher_edge = teacher_edges[edge_id]
            for endpoint in (teacher_edge.attrib.get("from", ""), teacher_edge.attrib.get("to", "")):
                if not endpoint or endpoint in candidate_junction_ids:
                    continue
                teacher_endpoint_junction = teacher_junctions.get(endpoint)
                if teacher_endpoint_junction is None:
                    continue
                candidate_root.insert(
                    list(candidate_root).index(target_candidate_junction),
                    _clone_transformed_boundary_junction(
                        teacher_endpoint_junction,
                        dx,
                        dy,
                        replay_edge_map,
                        teacher_junction_id,
                        junction_id,
                    ),
                )
                candidate_junction_ids.add(endpoint)
                copied_boundary_junctions.append(endpoint)
            if any(
                endpoint not in candidate_junction_ids
                for endpoint in (teacher_edge.attrib.get("from", ""), teacher_edge.attrib.get("to", ""))
            ):
                continue
            copied_edge = _clone_transformed_boundary_edge(
                teacher_edge,
                edge_id,
                dx,
                dy,
                replay_edge_map,
                teacher_junction_id,
                junction_id,
            )
            candidate_root.insert(continuation_insert_at, copied_edge)
            continuation_insert_at += 1
            candidate_edge_ids.add(edge_id)
            candidate_edges_by_id[edge_id] = copied_edge
            replay_edge_map[edge_id] = edge_id
            _append_edge_lanes_to_destination_junction(candidate_root, copied_edge)
            copied_boundary_continuation_edges.append(edge_id)
        continuation_edge_id_set = set(copied_boundary_continuation_edges)
        existing_connection_keys = {
            (
                connection.attrib.get("from", ""),
                connection.attrib.get("to", ""),
                connection.attrib.get("fromLane", "0"),
                connection.attrib.get("toLane", "0"),
            )
            for connection in candidate_root.findall("connection")
        }
        for connection in teacher_root.findall("connection"):
            from_edge_id = connection.attrib.get("from", "")
            to_edge_id = connection.attrib.get("to", "")
            if not (
                {from_edge_id, to_edge_id} & copied_boundary_edge_ids
                and {from_edge_id, to_edge_id} & continuation_edge_id_set
            ):
                continue
            mapped = dict(connection.attrib)
            mapped["from"] = replay_edge_map.get(from_edge_id, from_edge_id)
            mapped["to"] = replay_edge_map.get(to_edge_id, to_edge_id)
            if mapped["from"] not in candidate_edge_ids or mapped["to"] not in candidate_edge_ids:
                continue
            mapped.pop("via", None)
            for attr in ("tl", "linkIndex", "linkIndex2"):
                mapped.pop(attr, None)
            if mapped.get("shape"):
                mapped["shape"] = _translate_shape(mapped["shape"], dx, dy)
            key = (
                mapped.get("from", ""),
                mapped.get("to", ""),
                mapped.get("fromLane", "0"),
                mapped.get("toLane", "0"),
            )
            if key in existing_connection_keys:
                continue
            candidate_root.append(ET.Element("connection", mapped))
            existing_connection_keys.add(key)
            copied_boundary_continuation_connections.append(mapped)

    removed_stale_boundary_edges = []
    if teacher_boundary_edge_ids:
        expected_boundary_edge_ids = {
            replay_edge_map.get(edge_id, edge_id)
            for edge_id in teacher_boundary_edge_ids
            if replay_edge_map.get(edge_id, edge_id)
        }
        for edge in list(candidate_root.findall("edge")):
            edge_id = edge.attrib.get("id", "")
            if (
                not edge_id
                or edge_id in expected_boundary_edge_ids
                or edge_id.startswith(":")
                or edge.attrib.get("function") in {"internal", "crossing", "walkingarea"}
                or junction_id not in (edge.attrib.get("from", ""), edge.attrib.get("to", ""))
            ):
                continue
            _remove_edge_lanes_from_destination_junction(candidate_root, edge, all_junctions=True)
            candidate_root.remove(edge)
            candidate_edge_ids.discard(edge_id)
            candidate_edges_by_id.pop(edge_id, None)
            removed_stale_boundary_edges.append(edge_id)
    removed_stale_boundary_edge_connections = []
    if removed_stale_boundary_edges:
        removed_stale_boundary_edge_id_set = set(removed_stale_boundary_edges)
        for connection in list(candidate_root.findall("connection")):
            if {connection.attrib.get("from", ""), connection.attrib.get("to", "")} & removed_stale_boundary_edge_id_set:
                removed_stale_boundary_edge_connections.append(dict(connection.attrib))
                candidate_root.remove(connection)

    for offset, edge in enumerate(teacher_internal_edges):
        candidate_root.insert(
            insert_index + boundary_insert_offset + offset,
            _clone_transformed_net_element(edge, dx, dy, replay_edge_map, teacher_junction_id, junction_id),
        )

    removed_internal_junctions = []
    junction_insert_index = None
    for child in list(candidate_root):
        if child.tag == "junction" and child.attrib.get("id", "").startswith(internal_prefix):
            if junction_insert_index is None:
                junction_insert_index = list(candidate_root).index(child)
            removed_internal_junctions.append(child.attrib.get("id", ""))
            candidate_root.remove(child)
    if junction_insert_index is None:
        junction_insert_index = list(candidate_root).index(target_candidate_junction) + 1

    teacher_internal_junctions = [
        junction
        for junction in teacher_root.findall("junction")
        if junction.attrib.get("id", "").startswith(teacher_internal_prefix)
    ]
    for offset, junction in enumerate(teacher_internal_junctions):
        candidate_root.insert(
            junction_insert_index + offset,
            _clone_transformed_junction(junction, dx, dy, replay_edge_map, teacher_internal_prefix, internal_prefix),
        )

    mapped_target_attrs = _mapped_junction_attrs(
        teacher_junction,
        dx,
        dy,
        replay_edge_map,
        teacher_internal_prefix,
        internal_prefix,
    )
    if preserve_target_junction_shape and original_target_junction_shape:
        mapped_target_attrs["shape"] = original_target_junction_shape
        if original_target_custom_shape is not None:
            mapped_target_attrs["customShape"] = original_target_custom_shape
        else:
            mapped_target_attrs.pop("customShape", None)
    target_candidate_junction.attrib.clear()
    target_candidate_junction.attrib.update(mapped_target_attrs)
    for child in list(target_candidate_junction):
        target_candidate_junction.remove(child)
    for request in teacher_junction.findall("request"):
        target_candidate_junction.append(ET.Element("request", dict(request.attrib)))

    edge_endpoints = {
        edge.attrib.get("id", ""): (edge.attrib.get("from", ""), edge.attrib.get("to", ""))
        for edge in candidate_root.findall("edge")
        if edge.attrib.get("id")
    }
    edge_lane_counts = _net_lane_counts(candidate_root)
    removed_stale_replaced_edge_connections = []
    for connection in list(candidate_root.findall("connection")):
        from_edge_id = connection.attrib.get("from", "")
        to_edge_id = connection.attrib.get("to", "")
        connection_edge_ids = {from_edge_id, to_edge_id}
        source_endpoint = edge_endpoints.get(from_edge_id)
        target_endpoint = edge_endpoints.get(to_edge_id)
        shared_endpoint = (
            source_endpoint[1]
            if source_endpoint and target_endpoint and source_endpoint[1] and source_endpoint[1] == target_endpoint[0]
            else ""
        )
        via_edge_id = connection.attrib.get("via", "")
        stale_via = bool(via_edge_id and shared_endpoint and not via_edge_id.startswith(f":{shared_endpoint}_"))
        if (
            not _touches_target_replay_scope(connection, internal_prefix, junction_id, candidate_edges_by_id)
            and connection_edge_ids & replaced_boundary_edge_ids
            and (
                not shared_endpoint
                or stale_via
                or not _connection_lane_indices_valid(connection, edge_lane_counts)
            )
        ):
            removed_stale_replaced_edge_connections.append(dict(connection.attrib))
            candidate_root.remove(connection)

    removed_connections = 0
    for connection in list(candidate_root.findall("connection")):
        if _touches_target_replay_scope(connection, internal_prefix, junction_id, candidate_edges_by_id):
            candidate_root.remove(connection)
            removed_connections += 1

    copied_connections = 0
    skipped_connections = []
    ignored_off_scope_tls_connections = []
    for connection in teacher_root.findall("connection"):
        if not _touches_target_replay_scope(connection, teacher_internal_prefix, teacher_junction_id, teacher_edges):
            if connection.attrib.get("tl") == teacher_junction_id:
                ignored_off_scope_tls_connections.append(dict(connection.attrib))
            continue
        mapped = _mapped_connection_attrs(
            connection,
            replay_edge_map,
            teacher_internal_prefix,
            teacher_junction_id,
            internal_prefix,
            junction_id,
            candidate_edge_ids,
            dx,
            dy,
        )
        if mapped is None:
            skipped_connections.append(dict(connection.attrib))
            continue
        candidate_root.append(ET.Element("connection", mapped))
        copied_connections += 1

    teacher_tls_ids = [
        connection.attrib.get("tl", "")
        for connection in teacher_root.findall("connection")
        if _touches_target_replay_scope(connection, teacher_internal_prefix, teacher_junction_id, teacher_edges)
        and connection.attrib.get("tl")
        and connection.attrib.get("linkIndex")
    ]
    teacher_tllogic = teacher_root.find(f"tlLogic[@id='{teacher_junction_id}']")
    if teacher_tllogic is None:
        teacher_tllogic = next(
            (tl for tl in teacher_root.findall("tlLogic") if tl.attrib.get("id") in teacher_tls_ids),
            None,
        )
    removed_stale_tllogic_ids = []
    uncontrolled_stale_tls_connections = []
    removed_stale_tls_connections = []
    if teacher_tllogic is not None:
        target_tllogic = candidate_root.find(f"tlLogic[@id='{junction_id}']")
        root_children = list(candidate_root)
        target_index = (
            root_children.index(target_tllogic)
            if target_tllogic is not None
            else next(
                (index for index, child in enumerate(root_children) if child.tag == "connection"),
                len(root_children),
            )
        )
        if target_tllogic is not None:
            candidate_root.remove(target_tllogic)
        copied_tllogic = _clone_transformed_net_element(teacher_tllogic, dx, dy, replay_edge_map, teacher_junction_id, junction_id)
        copied_tllogic.set("id", junction_id)
        candidate_root.insert(target_index, copied_tllogic)
        teacher_link_capacity = max(
            (len(phase.attrib.get("state", "")) for phase in copied_tllogic.findall("phase")),
            default=0,
        )
        for connection in list(candidate_root.findall("connection")):
            if connection.attrib.get("tl") != junction_id or not teacher_link_capacity:
                continue
            link_indices = _connection_link_indices(connection)
            if (
                link_indices
                and max(link_indices) >= teacher_link_capacity
                and not _touches_target_replay_scope(connection, internal_prefix, junction_id, candidate_edges_by_id)
            ):
                removed_stale_tls_connections.append(dict(connection.attrib))
                candidate_root.remove(connection)
    else:
        target_tllogic = candidate_root.find(f"tlLogic[@id='{junction_id}']")
        if target_tllogic is not None:
            candidate_root.remove(target_tllogic)
            removed_stale_tllogic_ids.append(junction_id)
        for connection in candidate_root.findall("connection"):
            if connection.attrib.get("tl") != junction_id:
                continue
            uncontrolled_stale_tls_connections.append(dict(connection.attrib))
            for attr in ("tl", "linkIndex", "linkIndex2"):
                connection.attrib.pop(attr, None)
            connection.set("uncontrolled", "true")

    removed_invalid_lane_connections = []
    edge_lane_counts = _net_lane_counts(candidate_root)
    for connection in list(candidate_root.findall("connection")):
        if not _connection_lane_indices_valid(connection, edge_lane_counts):
            removed_invalid_lane_connections.append(dict(connection.attrib))
            candidate_root.remove(connection)

    added_missing_teacher_endpoint_junctions = []
    for edge in candidate_root.findall("edge"):
        for endpoint in (edge.attrib.get("from", ""), edge.attrib.get("to", "")):
            if not endpoint or endpoint in candidate_junction_ids:
                continue
            teacher_endpoint_junction = teacher_junctions.get(endpoint)
            if teacher_endpoint_junction is None or teacher_endpoint_junction.attrib.get("type") == "internal":
                continue
            candidate_root.insert(
                _first_junction_index(candidate_root),
                _clone_transformed_boundary_junction(
                    teacher_endpoint_junction,
                    dx,
                    dy,
                    replay_edge_map,
                    teacher_junction_id,
                    junction_id,
                ),
            )
            candidate_junction_ids.add(endpoint)
            added_missing_teacher_endpoint_junctions.append(endpoint)

    restored_geometry_anchor_junctions = _restore_geometry_anchor_junctions(candidate_root, geometry_anchor_junctions_by_id)
    external_boundary_connection_report: dict[str, object] = {
        "status": "skipped",
        "restored_connection_count": 0,
        "restored_connections": [],
        "preserved_existing_connection_count": 0,
        "preserved_existing_connections": [],
        "skipped_connection_count": 0,
        "skipped_connections": [],
    }
    if preserve_mapped_boundary_endpoints:
        external_boundary_connection_report = _restore_external_boundary_connections(
            source_root=source_candidate_root,
            target_root=candidate_root,
            boundary_edge_ids=set(replaced_boundary_edge_ids),
            source_local_junction_ids={junction_id},
        )

    unblended_geometry_anchor_edge_ids = geometry_anchor_edge_ids - set(
        blended_geometry_anchor_edge_ids
    )
    if geometry_anchor_edge_ids and not unblended_geometry_anchor_edge_ids:
        target_shape_anchor_report = {
            "status": "skipped",
            "reason": "all_geometry_anchor_edges_blended_at_target",
            "blended_approach_edge_ids": sorted(set(blended_geometry_anchor_edge_ids)),
        }
    else:
        target_shape_anchor_report = _expand_junction_shape_to_approach_endpoints(
            candidate_root,
            junction_id,
            unblended_geometry_anchor_edge_ids,
        )

    ET.indent(candidate_root, space="    ")
    candidate_tree.write(output_file, encoding="utf-8", xml_declaration=True)
    return {
        "status": "pass",
        "claim_status": "diagnostic-demo",
        "net_file": str(output_file),
        "dx": round(dx, 6),
        "dy": round(dy, 6),
        "removed_internal_edge_count": len(removed_internal_edges),
        "copied_internal_edge_count": len(teacher_internal_edges),
        "copied_boundary_edge_count": len(copied_boundary_edges),
        "copied_boundary_edges": copied_boundary_edges,
        "copied_boundary_candidate_edges": copied_boundary_candidate_edges,
        "copy_unmapped_boundary_edges": copy_unmapped_boundary_edges,
        "preserve_mapped_boundary_endpoints": preserve_mapped_boundary_endpoints,
        "preserve_target_junction_shape": preserve_target_junction_shape,
        "skipped_unmapped_teacher_boundary_edge_count": len(
            skipped_unmapped_teacher_boundary_edges
        ),
        "skipped_unmapped_teacher_boundary_edges": skipped_unmapped_teacher_boundary_edges,
        "preserved_mapped_boundary_endpoint_count": len(
            preserved_mapped_boundary_endpoints
        ),
        "preserved_mapped_boundary_endpoints": preserved_mapped_boundary_endpoints,
        "restored_external_boundary_connection_count": external_boundary_connection_report[
            "restored_connection_count"
        ],
        "restored_external_boundary_connections": external_boundary_connection_report[
            "restored_connections"
        ],
        "preserved_existing_external_boundary_connection_count": (
            external_boundary_connection_report["preserved_existing_connection_count"]
        ),
        "preserved_existing_external_boundary_connections": (
            external_boundary_connection_report["preserved_existing_connections"]
        ),
        "skipped_external_boundary_connection_count": external_boundary_connection_report[
            "skipped_connection_count"
        ],
        "skipped_external_boundary_connections": external_boundary_connection_report[
            "skipped_connections"
        ],
        "copied_boundary_continuation_edge_count": len(copied_boundary_continuation_edges),
        "copied_boundary_continuation_edges": copied_boundary_continuation_edges,
        "copied_boundary_continuation_connection_count": len(copied_boundary_continuation_connections),
        "copied_boundary_continuation_connections": copied_boundary_continuation_connections,
        "removed_stale_split_fragment_edge_count": len(removed_stale_split_fragment_edges),
        "removed_stale_split_fragment_edges": removed_stale_split_fragment_edges,
        "rewired_stale_split_fragment_connection_count": len(rewired_stale_split_fragment_connections),
        "rewired_stale_split_fragment_connections": rewired_stale_split_fragment_connections,
        "replayed_stale_split_continuation_edge_count": len(replayed_stale_split_continuation_edges),
        "replayed_stale_split_continuation_edges": replayed_stale_split_continuation_edges,
        "replayed_stale_split_followup_edge_count": len(replayed_stale_split_followup_edges),
        "replayed_stale_split_followup_edges": replayed_stale_split_followup_edges,
        "removed_stale_split_dead_end_edge_count": len(removed_stale_split_dead_end_edges),
        "removed_stale_split_dead_end_edges": removed_stale_split_dead_end_edges,
        "removed_stale_split_dead_end_connection_count": len(removed_stale_split_dead_end_connections),
        "removed_stale_split_dead_end_connections": removed_stale_split_dead_end_connections,
        "removed_teacher_absent_same_family_continuation_edge_count": len(
            removed_teacher_absent_same_family_continuation_edges
        ),
        "removed_teacher_absent_same_family_continuation_edges": removed_teacher_absent_same_family_continuation_edges,
        "removed_teacher_absent_same_family_continuation_connection_count": len(
            removed_teacher_absent_same_family_continuation_connections
        ),
        "removed_teacher_absent_same_family_continuation_connections": (
            removed_teacher_absent_same_family_continuation_connections
        ),
        "removed_teacher_absent_same_family_continuation_junctions": (
            removed_teacher_absent_same_family_continuation_junctions
        ),
        "removed_cluster_member_residual_edge_count": len(removed_cluster_member_residual_edges),
        "removed_cluster_member_residual_edges": removed_cluster_member_residual_edges,
        "removed_cluster_member_residual_connection_count": len(removed_cluster_member_residual_connections),
        "removed_cluster_member_residual_connections": removed_cluster_member_residual_connections,
        "removed_cluster_member_residual_junctions": removed_cluster_member_residual_junctions,
        "replayed_stale_split_context_edge_count": len(replayed_stale_split_context_edges),
        "replayed_stale_split_context_edges": replayed_stale_split_context_edges,
        "retuned_stale_split_junction_ids": retuned_stale_split_junction_ids,
        "stripped_stale_split_tls_connection_count": len(stripped_stale_split_tls_connections),
        "stripped_stale_split_tls_connections": stripped_stale_split_tls_connections,
        "removed_stale_split_tllogic_ids": removed_stale_split_tllogic_ids,
        "preserved_colliding_boundary_edge_count": len(preserved_colliding_boundary_edges),
        "preserved_colliding_boundary_edges": preserved_colliding_boundary_edges,
        "same_family_continuation_edge_map_count": len(same_family_continuation_edge_map),
        "same_family_continuation_edge_map": dict(sorted(same_family_continuation_edge_map.items())),
        "removed_stale_boundary_edge_count": len(removed_stale_boundary_edges),
        "removed_stale_boundary_edges": removed_stale_boundary_edges,
        "removed_stale_boundary_edge_connection_count": len(removed_stale_boundary_edge_connections),
        "removed_stale_boundary_edge_connections": removed_stale_boundary_edge_connections,
        "removed_stale_replaced_edge_connection_count": len(removed_stale_replaced_edge_connections),
        "removed_stale_replaced_edge_connections": removed_stale_replaced_edge_connections,
        "removed_invalid_lane_connection_count": len(removed_invalid_lane_connections),
        "removed_invalid_lane_connections": removed_invalid_lane_connections,
        "added_missing_teacher_endpoint_junction_count": len(added_missing_teacher_endpoint_junctions),
        "added_missing_teacher_endpoint_junction_ids": added_missing_teacher_endpoint_junctions,
        "geometry_anchor_edge_count": len(geometry_anchor_edge_ids),
        "blend_geometry_anchor_at_target": blend_geometry_anchor_at_target,
        "blended_geometry_anchor_edge_count": len(
            blended_geometry_anchor_edge_ids
        ),
        "blended_geometry_anchor_edge_ids": sorted(
            set(blended_geometry_anchor_edge_ids)
        ),
        "restored_geometry_anchor_junction_count": len(restored_geometry_anchor_junctions),
        "restored_geometry_anchor_junctions": restored_geometry_anchor_junctions,
        "target_shape_anchor": target_shape_anchor_report,
        "copied_boundary_junction_count": len(copied_boundary_junctions),
        "copied_boundary_junctions": copied_boundary_junctions,
        "skipped_boundary_edge_count": len(skipped_boundary_edges),
        "skipped_boundary_edges": skipped_boundary_edges,
        "removed_internal_junction_count": len(removed_internal_junctions),
        "copied_internal_junction_count": len(teacher_internal_junctions),
        "removed_connection_count": removed_connections,
        "copied_connection_count": copied_connections,
        "skipped_connection_count": len(skipped_connections),
        "skipped_connections": skipped_connections,
        "ignored_off_scope_tls_connection_count": len(ignored_off_scope_tls_connections),
        "ignored_off_scope_tls_connections": ignored_off_scope_tls_connections,
        "removed_stale_tllogic_count": len(removed_stale_tllogic_ids),
        "removed_stale_tllogic_ids": removed_stale_tllogic_ids,
        "uncontrolled_stale_tls_connection_count": len(uncontrolled_stale_tls_connections),
        "uncontrolled_stale_tls_connections": uncontrolled_stale_tls_connections,
        "removed_stale_tls_connection_count": len(removed_stale_tls_connections),
        "removed_stale_tls_connections": removed_stale_tls_connections,
        "copied_request_count": len(teacher_junction.findall("request")),
        "effective_edge_map": dict(sorted(replay_edge_map.items())),
    }
