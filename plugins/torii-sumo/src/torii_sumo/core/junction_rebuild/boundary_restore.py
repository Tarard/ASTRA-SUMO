"""Preserve approach roads and restore external boundary connections."""

from __future__ import annotations

import copy
import xml.etree.ElementTree as ET
from .geometry import (
    _blend_geometry_anchor_at_endpoint,
    _edge_geometry_matches_current_junctions,
    _join_shape_text,
    _joined_lane_length,
    _polyline_length,
    _primary_edge_shape,
)
from .network import _connection_lane_indices_valid, _lanes_by_index, _net_lane_counts, _split


def _append_edge_lanes_to_destination_junction(root: ET.Element, edge: ET.Element) -> None:
    destination = edge.attrib.get("to", "")
    if not destination:
        return
    junction = next((item for item in root.findall("junction") if item.attrib.get("id") == destination), None)
    if junction is None:
        return
    lanes = [lane.attrib["id"] for lane in edge.findall("lane") if lane.attrib.get("id")]
    if not lanes:
        return
    inc_lanes = _split(junction.attrib.get("incLanes", ""))
    for lane in lanes:
        if lane not in inc_lanes:
            inc_lanes.append(lane)
    junction.set("incLanes", " ".join(inc_lanes))


def _remove_edge_lanes_from_destination_junction(
    root: ET.Element,
    edge: ET.Element,
    *,
    all_junctions: bool = False,
) -> None:
    lanes = {lane.attrib["id"] for lane in edge.findall("lane") if lane.attrib.get("id")}
    if not lanes:
        return
    if all_junctions:
        junctions = root.findall("junction")
    else:
        destination = edge.attrib.get("to", "")
        if not destination:
            return
        junction = next((item for item in root.findall("junction") if item.attrib.get("id") == destination), None)
        if junction is None:
            return
        junctions = [junction]
    for junction in junctions:
        inc_lanes = _split(junction.attrib.get("incLanes", ""))
        if not inc_lanes:
            continue
        filtered = [lane for lane in inc_lanes if lane not in lanes]
        if len(filtered) != len(inc_lanes):
            junction.set("incLanes", " ".join(filtered))


def _restore_joined_split_edge_geometry(
    edge: ET.Element,
    stale_edge: ET.Element,
    source_edge: ET.Element,
) -> bool:
    if (
        stale_edge.attrib.get("to") == source_edge.attrib.get("from")
        and edge.attrib.get("from") == stale_edge.attrib.get("from")
        and edge.attrib.get("to") == source_edge.attrib.get("to")
    ):
        first_edge, second_edge = stale_edge, source_edge
    elif (
        source_edge.attrib.get("to") == stale_edge.attrib.get("from")
        and edge.attrib.get("from") == source_edge.attrib.get("from")
        and edge.attrib.get("to") == stale_edge.attrib.get("to")
    ):
        first_edge, second_edge = source_edge, stale_edge
    else:
        return False

    edge_shape = _join_shape_text(_primary_edge_shape(first_edge), _primary_edge_shape(second_edge))
    if edge_shape:
        edge.set("shape", edge_shape)
    first_lanes = _lanes_by_index(first_edge)
    second_lanes = _lanes_by_index(second_edge)
    changed = bool(edge_shape)
    for lane in edge.findall("lane"):
        lane_index = lane.attrib.get("index", "")
        first_lane = first_lanes.get(lane_index)
        second_lane = second_lanes.get(lane_index)
        if first_lane is None or second_lane is None:
            continue
        shape = _join_shape_text(first_lane.attrib.get("shape", ""), second_lane.attrib.get("shape", ""))
        if shape:
            lane.set("shape", shape)
            changed = True
        length = _joined_lane_length(first_lane, second_lane)
        if length is not None:
            lane.set("length", length)
    return changed


def _restore_existing_edge_geometry(
    edge: ET.Element,
    geometry_source_edge: ET.Element,
    root: ET.Element,
    *,
    max_endpoint_delta: float | None = None,
) -> None:
    if max_endpoint_delta is not None and not _edge_geometry_matches_current_junctions(
        root,
        edge,
        geometry_source_edge,
        max_endpoint_delta,
    ):
        return
    source_edge_shape = geometry_source_edge.attrib.get("shape", "")
    if source_edge_shape:
        edge.set("shape", source_edge_shape)
    source_lane_shapes = {
        lane.attrib.get("index", ""): lane.attrib["shape"]
        for lane in geometry_source_edge.findall("lane")
        if lane.attrib.get("index", "") and lane.attrib.get("shape")
    }
    for lane in edge.findall("lane"):
        lane_shape = source_lane_shapes.get(lane.attrib.get("index", ""))
        if lane_shape:
            lane.set("shape", lane_shape)
        elif source_edge_shape:
            # Plain ``.edg.xml`` anchors often carry one centerline shape on
            # the edge and no explicit lane children.  It is still better
            # evidence than geometry translated from an unrelated teacher
            # endpoint, and preserves the legacy marker-file behaviour.
            lane.set("shape", source_edge_shape)
        restored_shape = lane.attrib.get("shape", "")
        if restored_shape and "length" in lane.attrib:
            rendered_length = _polyline_length(restored_shape)
            if rendered_length is not None:
                lane.set("length", f"{rendered_length:.2f}")


def _preserve_mapped_boundary_geometry(
    replayed_edge: ET.Element,
    geometry_source_edge: ET.Element,
    *,
    target_junction_ids: set[str],
    source_local_junction_ids: set[str],
) -> dict[str, object]:
    """Preserve a mapped candidate boundary while moving its local endpoint.

    A teacher boundary is a local intersection model, not evidence for the
    complete public-road segment outside the rebuilt cell.  Replacing a mapped
    candidate edge with the complete translated teacher shape can therefore
    move its remote endpoint by tens of metres.  Require the teacher and source
    to address the same directed side, keep the source remote endpoint, and
    blend only toward the replayed local endpoint.
    """

    source_has_geometry = bool(_primary_edge_shape(geometry_source_edge))
    replay_has_geometry = bool(_primary_edge_shape(replayed_edge))
    if not source_has_geometry or not replay_has_geometry:
        return {
            "status": "skipped",
            "reason": "source_or_replay_geometry_missing",
        }

    target_at_start = replayed_edge.attrib.get("from", "") in target_junction_ids
    target_at_end = replayed_edge.attrib.get("to", "") in target_junction_ids
    source_local_at_start = geometry_source_edge.attrib.get("from", "") in source_local_junction_ids
    source_local_at_end = geometry_source_edge.attrib.get("to", "") in source_local_junction_ids
    if target_at_start == target_at_end:
        return {
            "status": "blocked",
            "reason": "replayed_boundary_does_not_have_one_local_endpoint",
        }
    if source_local_at_start == source_local_at_end:
        return {
            "status": "blocked",
            "reason": "source_boundary_does_not_have_one_local_endpoint",
        }
    if target_at_start != source_local_at_start:
        return {
            "status": "blocked",
            "reason": "source_and_replayed_boundary_orientation_mismatch",
        }

    source_remote_id = geometry_source_edge.attrib.get("to" if source_local_at_start else "from", "")
    replayed_remote_id = replayed_edge.attrib.get("to" if target_at_start else "from", "")
    if source_remote_id != replayed_remote_id:
        return {
            "status": "blocked",
            "reason": "source_and_replayed_boundary_remote_endpoint_mismatch",
            "source_remote_junction_id": source_remote_id,
            "replayed_remote_junction_id": replayed_remote_id,
        }

    operational_restore = _preserve_boundary_operational_attributes(
        replayed_edge,
        geometry_source_edge,
    )
    if not _blend_geometry_anchor_at_endpoint(
        replayed_edge,
        geometry_source_edge,
        target_at_start=target_at_start,
    ):
        return {
            "status": "blocked",
            "reason": "boundary_geometry_blend_failed",
        }
    return {
        "status": "pass",
        "target_at_start": target_at_start,
        "source_remote_junction_id": source_remote_id,
        **operational_restore,
    }


def _restore_external_boundary_connections(
    *,
    source_root: ET.Element,
    target_root: ET.Element,
    boundary_edge_ids: set[str],
    source_local_junction_ids: set[str],
) -> dict[str, object]:
    """Restore only the source connections on the remote side of boundaries."""

    source_edges = {
        edge.attrib["id"]: edge
        for edge in source_root.findall("edge")
        if edge.attrib.get("id")
    }
    target_edge_ids = {
        edge.attrib["id"]
        for edge in target_root.findall("edge")
        if edge.attrib.get("id")
    }
    target_lane_counts = _net_lane_counts(target_root)
    target_lane_ids = {
        lane.attrib["id"]
        for edge in target_root.findall("edge")
        for lane in edge.findall("lane")
        if lane.attrib.get("id")
    }
    existing_keys = {
        tuple(sorted(connection.attrib.items()))
        for connection in target_root.findall("connection")
    }
    restored: list[dict[str, str]] = []
    preserved_existing: list[dict[str, str]] = []
    skipped: list[dict[str, str]] = []

    source_connections = list(source_root.findall("connection"))
    considered_connection_keys: set[tuple[tuple[str, str], ...]] = set()

    def remote_connection_chain(seed: ET.Element) -> list[ET.Element]:
        """Return a remote connection and its compiled internal continuations."""

        chain: list[ET.Element] = []
        queue = [seed]
        queued_keys = {tuple(sorted(seed.attrib.items()))}
        while queue:
            connection = queue.pop(0)
            chain.append(connection)
            via_lane = connection.attrib.get("via", "")
            if not via_lane:
                continue
            via_edge_id = via_lane.rsplit("_", 1)[0]
            for continuation in source_connections:
                if continuation.attrib.get("from", "") != via_edge_id:
                    continue
                key = tuple(sorted(continuation.attrib.items()))
                if key not in queued_keys:
                    queued_keys.add(key)
                    queue.append(continuation)
        return chain

    for boundary_edge_id in sorted(boundary_edge_ids):
        source_edge = source_edges.get(boundary_edge_id)
        if source_edge is None:
            continue
        local_at_start = source_edge.attrib.get("from", "") in source_local_junction_ids
        local_at_end = source_edge.attrib.get("to", "") in source_local_junction_ids
        if local_at_start == local_at_end:
            skipped.append(
                {
                    "boundary_edge_id": boundary_edge_id,
                    "reason": "source_boundary_does_not_have_one_local_endpoint",
                }
            )
            continue
        # At the remote junction an outgoing boundary is the source of the
        # continuation; an incoming boundary is its destination.
        boundary_attr = "from" if local_at_start else "to"
        for seed_connection in source_connections:
            if seed_connection.attrib.get(boundary_attr, "") != boundary_edge_id:
                continue
            for connection in remote_connection_chain(seed_connection):
                connection_key = tuple(sorted(connection.attrib.items()))
                if connection_key in considered_connection_keys:
                    continue
                considered_connection_keys.add(connection_key)
                record = dict(connection.attrib)
                if (
                    connection.attrib.get("from", "") not in target_edge_ids
                    or connection.attrib.get("to", "") not in target_edge_ids
                ):
                    skipped.append({**record, "reason": "missing_target_edge"})
                    continue
                if not _connection_lane_indices_valid(connection, target_lane_counts):
                    skipped.append({**record, "reason": "invalid_target_lane_index"})
                    continue
                via_lane = connection.attrib.get("via", "")
                if via_lane and via_lane not in target_lane_ids:
                    skipped.append({**record, "reason": "missing_target_via_lane"})
                    continue
                if connection_key in existing_keys:
                    preserved_existing.append(record)
                    continue
                target_root.append(copy.deepcopy(connection))
                existing_keys.add(connection_key)
                restored.append(record)

    return {
        "status": "pass",
        "restored_connection_count": len(restored),
        "restored_connections": restored,
        "preserved_existing_connection_count": len(preserved_existing),
        "preserved_existing_connections": preserved_existing,
        "skipped_connection_count": len(skipped),
        "skipped_connections": skipped,
    }


BOUNDARY_EDGE_OPERATIONAL_ATTRS = (
    "type",
    "priority",
    "name",
    "spreadType",
    "allow",
    "disallow",
    "speed",
    "width",
)


BOUNDARY_LANE_OPERATIONAL_ATTRS = (
    "speed",
    "width",
    "allow",
    "disallow",
    "endOffset",
    "acceleration",
    "changeLeft",
    "changeRight",
    "stopOffset",
)


def _preserve_boundary_operational_attributes(
    replayed_edge: ET.Element,
    source_edge: ET.Element,
) -> dict[str, object]:
    """Keep OSM road semantics while MAP/teacher owns only local topology.

    Official MAP evidence can authorize a lane-count expansion and the movement
    matrix, but it does not reclassify the complete boundary segment or restrict
    its vehicle classes.  Existing lanes retain their source operational fields;
    a newly added lane clones the nearest source lane's fields.
    """

    changed_edge_attrs = 0
    for attr in BOUNDARY_EDGE_OPERATIONAL_ATTRS:
        before = replayed_edge.attrib.get(attr)
        if attr in source_edge.attrib:
            replayed_edge.set(attr, source_edge.attrib[attr])
        else:
            replayed_edge.attrib.pop(attr, None)
        if before != replayed_edge.attrib.get(attr):
            changed_edge_attrs += 1

    source_lanes = {
        int(lane.attrib.get("index", "0") or 0): lane
        for lane in source_edge.findall("lane")
    }
    changed_lane_attrs = 0
    cloned_lane_indices: list[int] = []
    for replayed_lane in replayed_edge.findall("lane"):
        replayed_index = int(replayed_lane.attrib.get("index", "0") or 0)
        source_lane = source_lanes.get(replayed_index)
        if source_lane is None and source_lanes:
            source_index = min(source_lanes, key=lambda index: (abs(index - replayed_index), index))
            source_lane = source_lanes[source_index]
            cloned_lane_indices.append(replayed_index)
        if source_lane is None:
            continue
        for attr in BOUNDARY_LANE_OPERATIONAL_ATTRS:
            before = replayed_lane.attrib.get(attr)
            if attr in source_lane.attrib:
                replayed_lane.set(attr, source_lane.attrib[attr])
            else:
                replayed_lane.attrib.pop(attr, None)
            if before != replayed_lane.attrib.get(attr):
                changed_lane_attrs += 1
    return {
        "preserved_boundary_edge_operational_attr_count": changed_edge_attrs,
        "preserved_boundary_lane_operational_attr_count": changed_lane_attrs,
        "operational_attrs_cloned_to_new_lane_indices": sorted(cloned_lane_indices),
    }
