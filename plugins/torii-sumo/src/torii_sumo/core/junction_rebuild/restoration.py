"""Restore network geometry and internal elements outside a declared repair."""

from __future__ import annotations

import copy
import xml.etree.ElementTree as ET
from pathlib import Path
from .artifacts import _failure
from .boundary_restore import _restore_existing_edge_geometry
from .network import (
    GEOMETRY_RESTORE_LANE_ATTRS,
    _connection_lane_indices_valid,
    _first_junction_index,
    _net_lane_counts,
    _touches_target_internal_subgraph,
    _via_lane_edge_id,
)
from .tls import _copy_referenced_tllogics


def _restore_non_target_internal_artifacts(
    *,
    source_file: Path,
    target_file: Path,
    exclude_junction_ids: set[str],
) -> dict[str, object]:
    if not source_file.exists():
        return _failure(f"source net file does not exist: {source_file}")
    if not target_file.exists():
        return _failure(f"target net file does not exist: {target_file}")

    source_root = ET.parse(source_file).getroot()
    target_tree = ET.parse(target_file)
    target_root = target_tree.getroot()
    junction_ids = {
        junction.attrib["id"]
        for root in (source_root, target_root)
        for junction in root.findall("junction")
        if junction.attrib.get("id") and not junction.attrib["id"].startswith(":")
    }
    junction_prefixes = [(f":{junction_id}_", junction_id) for junction_id in sorted(junction_ids, key=len, reverse=True)]
    owner_cache: dict[str, str] = {}

    def owner(value: str) -> str:
        if value in owner_cache:
            return owner_cache[value]
        candidates = (value, _via_lane_edge_id(value))
        if not any(edge_id.startswith(":") for edge_id in candidates):
            owner_cache[value] = ""
            return ""
        internal_owner = next(
            (
                junction_id
                for edge_id in candidates
                for prefix, junction_id in junction_prefixes
                if edge_id.startswith(prefix)
            ),
            "",
        )
        owner_cache[value] = internal_owner
        return internal_owner

    def is_restored_owner(value: str) -> bool:
        internal_owner = owner(value)
        return bool(internal_owner and internal_owner not in exclude_junction_ids)

    def connection_restored(connection: ET.Element) -> bool:
        return any(
            is_restored_owner(connection.attrib.get(attr, ""))
            for attr in ("from", "to", "via")
        )

    target_normal_junction_ids = {
        junction.attrib["id"]
        for junction in target_root.findall("junction")
        if junction.attrib.get("id") and not junction.attrib["id"].startswith(":")
    }

    def has_valid_normal_endpoints(edge: ET.Element) -> bool:
        internal_owner = owner(edge.attrib.get("id", ""))
        if internal_owner and internal_owner not in target_normal_junction_ids:
            return False
        return all(
            not node_id or node_id.startswith(":") or node_id in target_normal_junction_ids
            for node_id in (edge.attrib.get("from", ""), edge.attrib.get("to", ""))
        )

    skipped_internal_edges_missing_junctions = []
    source_internal_edges = []
    for edge in source_root.findall("edge"):
        if not is_restored_owner(edge.attrib.get("id", "")):
            continue
        if not has_valid_normal_endpoints(edge):
            skipped_internal_edges_missing_junctions.append(
                {key: edge.attrib.get(key, "") for key in ("id", "from", "to")}
            )
            continue
        source_internal_edges.append(edge)
    target_internal_edge_index = None
    removed_internal_edges = 0
    for child in list(target_root):
        if child.tag == "edge" and is_restored_owner(child.attrib.get("id", "")):
            if target_internal_edge_index is None:
                target_internal_edge_index = list(target_root).index(child)
            target_root.remove(child)
            removed_internal_edges += 1
    if target_internal_edge_index is None:
        target_internal_edge_index = _first_junction_index(target_root)
    for offset, edge in enumerate(source_internal_edges):
        target_root.insert(target_internal_edge_index + offset, copy.deepcopy(edge))

    source_internal_junctions = [
        junction for junction in source_root.findall("junction") if is_restored_owner(junction.attrib.get("id", ""))
    ]
    target_internal_junction_index = None
    removed_internal_junctions = 0
    for child in list(target_root):
        if child.tag == "junction" and is_restored_owner(child.attrib.get("id", "")):
            if target_internal_junction_index is None:
                target_internal_junction_index = list(target_root).index(child)
            target_root.remove(child)
            removed_internal_junctions += 1
    if target_internal_junction_index is None:
        target_internal_junction_index = next(
            (index for index, child in enumerate(list(target_root)) if child.tag == "connection"),
            len(list(target_root)),
        )
    for offset, junction in enumerate(source_internal_junctions):
        target_root.insert(target_internal_junction_index + offset, copy.deepcopy(junction))

    removed_connections = 0
    retained_children = []
    for child in list(target_root):
        if child.tag == "connection" and connection_restored(child):
            removed_connections += 1
            continue
        retained_children.append(child)
    if removed_connections:
        target_root[:] = retained_children
    target_edge_ids = {edge.attrib["id"] for edge in target_root.findall("edge") if edge.attrib.get("id")}
    target_lane_counts = _net_lane_counts(target_root)
    lane_ids = {
        lane.attrib["id"]
        for edge in target_root.findall("edge")
        for lane in edge.findall("lane")
        if lane.attrib.get("id")
    }
    source_connections = []
    skipped_missing_edge_connections = []
    skipped_invalid_lane_connections = []
    skipped_missing_via_lane_connections = []
    for connection in source_root.findall("connection"):
        if not connection_restored(connection):
            continue
        from_edge = connection.attrib.get("from", "")
        to_edge = connection.attrib.get("to", "")
        if from_edge not in target_edge_ids or to_edge not in target_edge_ids:
            skipped_missing_edge_connections.append(
                {key: connection.attrib.get(key, "") for key in ("from", "to", "via")}
            )
            continue
        if not _connection_lane_indices_valid(connection, target_lane_counts):
            skipped_invalid_lane_connections.append(
                {key: connection.attrib.get(key, "") for key in ("from", "to", "fromLane", "toLane", "via")}
            )
            continue
        via_lane = connection.attrib.get("via", "")
        if via_lane and via_lane not in lane_ids:
            skipped_missing_via_lane_connections.append(
                {key: connection.attrib.get(key, "") for key in ("from", "to", "via")}
            )
            continue
        source_connections.append(connection)
    for connection in source_connections:
        target_root.append(copy.deepcopy(connection))
    restored_tls_ids = {
        connection.attrib.get("tl", "")
        for connection in source_connections
        if connection.attrib.get("tl") and connection.attrib.get("tl") not in exclude_junction_ids
    }
    tl_logic_report = _copy_referenced_tllogics(source_root, target_root, restored_tls_ids)

    restored_normal_junction_attr_count = 0
    restored_request_count = 0
    target_junctions = {
        junction.attrib["id"]: junction
        for junction in target_root.findall("junction")
        if junction.attrib.get("id") and not junction.attrib["id"].startswith(":")
    }
    restored_source_normal_junction_ids = set()
    for source_junction in source_root.findall("junction"):
        junction_id = source_junction.attrib.get("id", "")
        target_junction = target_junctions.get(junction_id)
        if not junction_id or junction_id in exclude_junction_ids or target_junction is None:
            continue
        restored_source_normal_junction_ids.add(junction_id)

        source_inc_lanes = source_junction.attrib.get("incLanes", "").split()
        source_int_lanes = source_junction.attrib.get("intLanes", "").split()
        source_requests = list(source_junction.findall("request"))
        source_inc_lanes_are_valid = all(lane in lane_ids for lane in source_inc_lanes)
        source_matrix_is_valid = all(lane in lane_ids for lane in source_int_lanes) and len(source_requests) in {
            0,
            len(source_int_lanes),
        }
        filtered_target_inc_lanes = [
            lane for lane in target_junction.attrib.get("incLanes", "").split() if lane in lane_ids
        ]
        filtered_target_int_lanes = [
            lane for lane in target_junction.attrib.get("intLanes", "").split() if lane in lane_ids
        ]
        new_attrs = dict(target_junction.attrib)
        for attr in ("type", "x", "y", "z", "shape", "customShape"):
            if attr in source_junction.attrib:
                new_attrs[attr] = source_junction.attrib[attr]
            elif attr == "customShape":
                new_attrs.pop(attr, None)
        new_attrs["incLanes"] = (
            source_junction.attrib.get("incLanes", "")
            if source_inc_lanes_are_valid
            else " ".join(filtered_target_inc_lanes)
        )
        if source_matrix_is_valid:
            new_attrs["intLanes"] = source_junction.attrib.get("intLanes", "")
            requests_to_copy = source_requests
        else:
            new_attrs["intLanes"] = " ".join(filtered_target_int_lanes)
            target_requests = list(target_junction.findall("request"))
            requests_to_copy = target_requests if len(target_requests) in {0, len(filtered_target_int_lanes)} else []
        if dict(target_junction.attrib) != new_attrs:
            target_junction.attrib.clear()
            target_junction.attrib.update(new_attrs)
            restored_normal_junction_attr_count += 1
        for request in list(target_junction.findall("request")):
            target_junction.remove(request)
        for request in requests_to_copy:
            target_junction.append(ET.Element("request", dict(request.attrib)))
            restored_request_count += 1

    for junction_id, target_junction in target_junctions.items():
        if junction_id in exclude_junction_ids or junction_id in restored_source_normal_junction_ids:
            continue
        current_inc_lanes = target_junction.attrib.get("incLanes", "").split()
        current_int_lanes = target_junction.attrib.get("intLanes", "").split()
        current_requests = list(target_junction.findall("request"))
        filtered_inc_lanes = [lane for lane in current_inc_lanes if lane in lane_ids]
        filtered_int_lanes = [lane for lane in current_int_lanes if lane in lane_ids]
        current_matrix_is_valid = all(lane in lane_ids for lane in current_int_lanes) and len(current_requests) in {
            0,
            len(current_int_lanes),
        }
        requests_to_keep = (
            current_requests
            if current_matrix_is_valid or len(current_requests) in {0, len(filtered_int_lanes)}
            else []
        )
        new_attrs = dict(target_junction.attrib)
        new_attrs["incLanes"] = " ".join(filtered_inc_lanes)
        new_attrs["intLanes"] = (
            target_junction.attrib.get("intLanes", "") if current_matrix_is_valid else " ".join(filtered_int_lanes)
        )
        attrs_changed = dict(target_junction.attrib) != new_attrs
        requests_changed = len(requests_to_keep) != len(current_requests)
        if not attrs_changed and not requests_changed:
            continue
        target_junction.attrib.clear()
        target_junction.attrib.update(new_attrs)
        for request in current_requests:
            target_junction.remove(request)
        for request in requests_to_keep:
            target_junction.append(ET.Element("request", dict(request.attrib)))
        restored_normal_junction_attr_count += 1

    if (
        removed_internal_edges
        or removed_internal_junctions
        or removed_connections
        or restored_normal_junction_attr_count
        or restored_request_count
        or tl_logic_report["copied_tllogic_count"]
        or tl_logic_report["replaced_tllogic_count"]
    ):
        ET.indent(target_root, space="    ")
        target_tree.write(target_file, encoding="utf-8", xml_declaration=True)
    return {
        "status": "pass",
        "claim_status": "diagnostic-demo",
        "source_file": str(source_file),
        "target_file": str(target_file),
        "exclude_junction_ids": sorted(exclude_junction_ids),
        "removed_non_target_internal_edge_count": removed_internal_edges,
        "restored_non_target_internal_edge_count": len(source_internal_edges),
        "skipped_non_target_internal_edge_missing_junction_count": len(skipped_internal_edges_missing_junctions),
        "skipped_non_target_internal_edge_missing_junctions": skipped_internal_edges_missing_junctions,
        "removed_non_target_internal_junction_count": removed_internal_junctions,
        "restored_non_target_internal_junction_count": len(source_internal_junctions),
        "removed_non_target_internal_connection_count": removed_connections,
        "restored_non_target_internal_connection_count": len(source_connections),
        "skipped_non_target_internal_connection_missing_edge_count": len(skipped_missing_edge_connections),
        "skipped_non_target_internal_connection_missing_edges": skipped_missing_edge_connections,
        "skipped_non_target_internal_connection_invalid_lane_count": len(skipped_invalid_lane_connections),
        "skipped_non_target_internal_connection_invalid_lanes": skipped_invalid_lane_connections,
        "skipped_non_target_internal_connection_missing_via_lane_count": len(skipped_missing_via_lane_connections),
        "skipped_non_target_internal_connection_missing_via_lanes": skipped_missing_via_lane_connections,
        "restored_non_target_normal_junction_attr_count": restored_normal_junction_attr_count,
        "restored_non_target_request_count": restored_request_count,
        "restored_non_target_tllogic_count": (
            tl_logic_report["copied_tllogic_count"] + tl_logic_report["replaced_tllogic_count"]
        ),
        "missing_non_target_tllogic_count": tl_logic_report["missing_source_tllogic_count"],
        "missing_non_target_tllogic_ids": tl_logic_report["missing_source_tllogic_ids"],
    }


def _restore_replayed_geometry_attrs(*, source_file: Path, target_file: Path, junction_id: str) -> dict[str, object]:
    if not source_file.exists():
        return _failure(f"source net file does not exist: {source_file}")
    if not target_file.exists():
        return _failure(f"target net file does not exist: {target_file}")

    internal_prefix = f":{junction_id}_"
    source_root = ET.parse(source_file).getroot()
    target_tree = ET.parse(target_file)
    target_root = target_tree.getroot()
    source_internal_edges = [
        edge
        for edge in source_root.findall("edge")
        if edge.attrib.get("id", "").startswith(internal_prefix)
    ]
    internal_edge_insert_index = None
    for child in list(target_root):
        if child.tag == "edge" and child.attrib.get("id", "").startswith(internal_prefix):
            if internal_edge_insert_index is None:
                internal_edge_insert_index = list(target_root).index(child)
            target_root.remove(child)
    if internal_edge_insert_index is None:
        internal_edge_insert_index = _first_junction_index(target_root)
    for offset, edge in enumerate(source_internal_edges):
        target_root.insert(internal_edge_insert_index + offset, copy.deepcopy(edge))

    source_internal_junctions = [
        junction
        for junction in source_root.findall("junction")
        if junction.attrib.get("id", "").startswith(internal_prefix)
    ]
    internal_junction_insert_index = None
    for child in list(target_root):
        if child.tag == "junction" and child.attrib.get("id", "").startswith(internal_prefix):
            if internal_junction_insert_index is None:
                internal_junction_insert_index = list(target_root).index(child)
            target_root.remove(child)
    if internal_junction_insert_index is None:
        internal_junction_insert_index = next(
            (index for index, child in enumerate(list(target_root)) if child.tag == "connection"),
            len(list(target_root)),
        )
    for offset, junction in enumerate(source_internal_junctions):
        target_root.insert(internal_junction_insert_index + offset, copy.deepcopy(junction))

    removed_connection_count = 0
    for connection in list(target_root.findall("connection")):
        if _touches_target_internal_subgraph(connection, internal_prefix, junction_id):
            target_root.remove(connection)
            removed_connection_count += 1
    restored_connection_count = 0
    for connection in source_root.findall("connection"):
        if _touches_target_internal_subgraph(connection, internal_prefix, junction_id):
            target_root.append(copy.deepcopy(connection))
            restored_connection_count += 1

    restored_edge_ids = {
        edge.attrib.get("id", "")
        for edge in source_root.findall("edge")
        if edge.attrib.get("id", "").startswith(internal_prefix)
    }
    for connection in source_root.findall("connection"):
        if not _touches_target_internal_subgraph(connection, internal_prefix, junction_id):
            continue
        for attr in ("from", "to"):
            edge_id = connection.attrib.get(attr, "")
            if edge_id:
                restored_edge_ids.add(edge_id)
        via_edge_id = _via_lane_edge_id(connection.attrib.get("via", ""))
        if via_edge_id:
            restored_edge_ids.add(via_edge_id)

    source_edges = {edge.attrib.get("id", ""): edge for edge in source_root.findall("edge") if edge.attrib.get("id")}
    target_edges = {edge.attrib.get("id", ""): edge for edge in target_root.findall("edge") if edge.attrib.get("id")}
    missing_edge_ids = []
    restored_lane_count = 0
    for edge_id in sorted(edge_id for edge_id in restored_edge_ids if edge_id):
        source_edge = source_edges.get(edge_id)
        target_edge = target_edges.get(edge_id)
        if source_edge is None or target_edge is None:
            missing_edge_ids.append(edge_id)
            continue
        target_lanes = {lane.attrib.get("index", ""): lane for lane in target_edge.findall("lane")}
        for source_lane in source_edge.findall("lane"):
            target_lane = target_lanes.get(source_lane.attrib.get("index", ""))
            if target_lane is None:
                continue
            before = {attr: target_lane.attrib.get(attr) for attr in GEOMETRY_RESTORE_LANE_ATTRS}
            for attr in GEOMETRY_RESTORE_LANE_ATTRS:
                if attr in source_lane.attrib:
                    target_lane.set(attr, source_lane.attrib[attr])
                else:
                    target_lane.attrib.pop(attr, None)
            after = {attr: target_lane.attrib.get(attr) for attr in GEOMETRY_RESTORE_LANE_ATTRS}
            if before != after:
                restored_lane_count += 1
    restored_request_count = 0
    restored_junction_attr_count = 0
    source_junction = source_root.find(f"junction[@id='{junction_id}']")
    target_junction = target_root.find(f"junction[@id='{junction_id}']")
    if source_junction is not None and target_junction is not None:
        before_attrs = dict(target_junction.attrib)
        target_junction.attrib.clear()
        target_junction.attrib.update(dict(source_junction.attrib))
        restored_junction_attr_count = 1 if before_attrs != target_junction.attrib else 0
    source_requests = source_junction.findall("request") if source_junction is not None else []
    if source_requests and target_junction is not None:
        for request in list(target_junction.findall("request")):
            target_junction.remove(request)
        for request in source_requests:
            target_junction.append(ET.Element("request", dict(request.attrib)))
        restored_request_count = len(source_requests)

    ET.indent(target_root, space="    ")
    target_tree.write(target_file, encoding="utf-8", xml_declaration=True)
    return {
        "status": "pass",
        "claim_status": "diagnostic-demo",
        "source_file": str(source_file),
        "target_file": str(target_file),
        "restored_internal_edge_count": len(source_internal_edges),
        "restored_internal_junction_count": len(source_internal_junctions),
        "removed_connection_count": removed_connection_count,
        "restored_connection_count": restored_connection_count,
        "restored_edge_count": len(restored_edge_ids) - len(missing_edge_ids),
        "restored_lane_count": restored_lane_count,
        "restored_junction_attr_count": restored_junction_attr_count,
        "restored_request_count": restored_request_count,
        "missing_edge_count": len(missing_edge_ids),
        "missing_edge_ids": missing_edge_ids,
    }


def restore_off_scope_netconvert_artifacts(
    *,
    source_file: Path,
    target_file: Path,
    mutable_junction_ids: set[str],
    mutable_edge_ids: set[str],
    expand_mutable_edge_endpoints: bool = True,
    junction_aliases: dict[str, str] | None = None,
    declared_absorbed_edge_ids: set[str] | None = None,
) -> dict[str, object]:
    """Restore the network outside one explicitly mutable replay scope.

    ``netconvert --sumo-net-file`` normalizes the complete network even when a
    replay changes only one junction cell.  This helper preserves the current
    cell and its boundary edges while restoring every other internal subgraph,
    traffic-light program, junction shape, and external-lane geometry from the
    immutable pre-normalization network.  Topology or lane-cardinality drift
    outside the declared scope is reported as a hard failure rather than being
    silently repaired.  ``junction_aliases`` is the narrow exception needed
    after a declared SUMO junction join: a surviving public-road edge may keep
    its source geometry while one endpoint changes from an absorbed member to
    the joined cluster id.  When ``declared_absorbed_edge_ids`` is supplied,
    only those source edges may disappear and each must collapse completely
    inside one alias target.
    """

    if not source_file.exists():
        return _failure(f"source net file does not exist: {source_file}")
    if not target_file.exists():
        return _failure(f"target net file does not exist: {target_file}")

    source_root = ET.parse(source_file).getroot()
    target_root = ET.parse(target_file).getroot()
    source_edges = {
        edge.attrib["id"]: edge
        for edge in source_root.findall("edge")
        if edge.attrib.get("id") and not edge.attrib["id"].startswith(":")
    }
    target_edges = {
        edge.attrib["id"]: edge
        for edge in target_root.findall("edge")
        if edge.attrib.get("id") and not edge.attrib["id"].startswith(":")
    }
    normalized_junction_aliases = {
        str(source_id): str(target_id)
        for source_id, target_id in (junction_aliases or {}).items()
        if str(source_id) and str(target_id) and str(source_id) != str(target_id)
    }
    source_junction_ids = {
        junction.attrib.get("id", "")
        for junction in source_root.findall("junction")
        if junction.attrib.get("id", "") and not junction.attrib.get("id", "").startswith(":")
    }
    target_junction_ids = {
        junction.attrib.get("id", "")
        for junction in target_root.findall("junction")
        if junction.attrib.get("id", "") and not junction.attrib.get("id", "").startswith(":")
    }
    invalid_aliases = [
        {
            "source_junction_id": source_id,
            "target_junction_id": target_id,
            "source_exists": source_id in source_junction_ids,
            "target_exists": target_id in target_junction_ids,
        }
        for source_id, target_id in sorted(normalized_junction_aliases.items())
        if source_id not in source_junction_ids or target_id not in target_junction_ids
    ]
    if invalid_aliases:
        return {
            "status": "fail",
            "claim_status": "construction-invalid",
            "reason": "junction_alias_validation_failed",
            "source_file": str(source_file),
            "target_file": str(target_file),
            "invalid_junction_aliases": invalid_aliases,
        }
    expected_absorbed_edge_ids = (
        {str(edge_id) for edge_id in declared_absorbed_edge_ids if str(edge_id)}
        if declared_absorbed_edge_ids is not None
        else None
    )
    effective_mutable_junction_ids = {
        str(value) for value in mutable_junction_ids if str(value)
    }
    effective_mutable_junction_ids.update(normalized_junction_aliases)
    effective_mutable_junction_ids.update(normalized_junction_aliases.values())
    effective_mutable_edge_ids = {str(value) for value in mutable_edge_ids if str(value)}
    if expand_mutable_edge_endpoints:
        for edge_id in sorted(effective_mutable_edge_ids):
            for edge in (source_edges.get(edge_id), target_edges.get(edge_id)):
                if edge is None:
                    continue
                effective_mutable_junction_ids.update(
                    value
                    for value in (edge.attrib.get("from", ""), edge.attrib.get("to", ""))
                    if value
                )

    internal_report = _restore_non_target_internal_artifacts(
        source_file=source_file,
        target_file=target_file,
        exclude_junction_ids=effective_mutable_junction_ids,
    )
    if internal_report.get("status") != "pass":
        return {
            "status": "fail",
            "claim_status": "construction-invalid",
            "reason": "off_scope_internal_artifact_restore_not_pass",
            "source_file": str(source_file),
            "target_file": str(target_file),
            "internal_artifact_restore": internal_report,
        }

    target_tree = ET.parse(target_file)
    target_root = target_tree.getroot()
    target_edges = {
        edge.attrib["id"]: edge
        for edge in target_root.findall("edge")
        if edge.attrib.get("id") and not edge.attrib["id"].startswith(":")
    }
    failures: list[dict[str, object]] = []
    authorized_absorbed_edge_ids: list[str] = []
    restored_join_boundary_edge_ids: list[str] = []
    restored_edge_ids: list[str] = []
    restored_edge_centerline_ids: list[str] = []
    restored_lane_count = 0
    for edge_id, source_edge in sorted(source_edges.items()):
        if edge_id in effective_mutable_edge_ids:
            continue
        target_edge = target_edges.get(edge_id)
        source_endpoints = (
            source_edge.attrib.get("from", ""),
            source_edge.attrib.get("to", ""),
        )
        aliased_source_endpoints = tuple(
            normalized_junction_aliases.get(endpoint, endpoint)
            for endpoint in source_endpoints
        )
        if target_edge is None:
            if expected_absorbed_edge_ids is not None:
                authorized = (
                    edge_id in expected_absorbed_edge_ids
                    and aliased_source_endpoints[0]
                    and aliased_source_endpoints[0] == aliased_source_endpoints[1]
                )
            else:
                authorized = all(
                    endpoint and endpoint in effective_mutable_junction_ids
                    for endpoint in source_endpoints
                )
            if authorized:
                authorized_absorbed_edge_ids.append(edge_id)
            else:
                failures.append(
                    {
                        "edge_id": edge_id,
                        "reason": "off_scope_edge_missing",
                        "source_endpoints": source_endpoints,
                        "aliased_source_endpoints": aliased_source_endpoints,
                        "declared_absorbed": (
                            edge_id in expected_absorbed_edge_ids
                            if expected_absorbed_edge_ids is not None
                            else None
                        ),
                    }
                )
            continue
        target_endpoints = (
            target_edge.attrib.get("from", ""),
            target_edge.attrib.get("to", ""),
        )
        join_boundary_endpoint_change = (
            source_endpoints != target_endpoints
            and bool(normalized_junction_aliases)
            and aliased_source_endpoints == target_endpoints
        )
        if source_endpoints != target_endpoints and not join_boundary_endpoint_change:
            failures.append(
                {
                    "edge_id": edge_id,
                    "reason": "off_scope_edge_endpoints_changed",
                    "source_endpoints": source_endpoints,
                    "target_endpoints": target_endpoints,
                }
            )
            continue
        if join_boundary_endpoint_change:
            restored_join_boundary_edge_ids.append(edge_id)
        source_lanes = {
            lane.attrib.get("index", ""): lane for lane in source_edge.findall("lane")
        }
        target_lanes = {
            lane.attrib.get("index", ""): lane for lane in target_edge.findall("lane")
        }
        if source_lanes.keys() != target_lanes.keys():
            failures.append(
                {
                    "edge_id": edge_id,
                    "reason": "off_scope_lane_cardinality_changed",
                    "source_lane_indices": sorted(source_lanes),
                    "target_lane_indices": sorted(target_lanes),
                }
            )
            continue
        before_edge_shape = target_edge.attrib.get("shape")
        before_lanes = {
            lane_index: {
                attr: target_lane.attrib.get(attr)
                for attr in GEOMETRY_RESTORE_LANE_ATTRS
            }
            for lane_index, target_lane in target_lanes.items()
        }
        _restore_existing_edge_geometry(target_edge, source_edge, target_root)
        if before_edge_shape != target_edge.attrib.get("shape"):
            restored_edge_centerline_ids.append(edge_id)
        edge_changed = False
        for lane_index, source_lane in source_lanes.items():
            target_lane = target_lanes[lane_index]
            for attr in GEOMETRY_RESTORE_LANE_ATTRS:
                if attr in source_lane.attrib:
                    target_lane.set(attr, source_lane.attrib[attr])
                else:
                    target_lane.attrib.pop(attr, None)
            if target_lane.get("shape"):
                # Preserve the restored curve on the next native import too.
                # Without this marker SUMO rebuilds it from the old edge axis.
                target_lane.set("customShape", "1")
            after = {attr: target_lane.attrib.get(attr) for attr in GEOMETRY_RESTORE_LANE_ATTRS}
            if before_lanes[lane_index] != after:
                edge_changed = True
                restored_lane_count += 1
        if edge_changed:
            restored_edge_ids.append(edge_id)

    undeleted_declared_absorbed_edge_ids: list[str] = []
    if expected_absorbed_edge_ids is not None:
        undeleted_declared_absorbed_edge_ids = sorted(
            expected_absorbed_edge_ids - set(authorized_absorbed_edge_ids)
        )
        failures.extend(
            {
                "edge_id": edge_id,
                "reason": "declared_absorbed_edge_not_absorbed",
            }
            for edge_id in undeleted_declared_absorbed_edge_ids
        )

    if restored_lane_count or restored_edge_centerline_ids:
        ET.indent(target_root, space="    ")
        target_tree.write(target_file, encoding="utf-8", xml_declaration=True)

    internal_failures = {
        key: int(internal_report.get(key, 0) or 0)
        for key in (
            "skipped_non_target_internal_edge_missing_junction_count",
            "skipped_non_target_internal_connection_missing_edge_count",
            "skipped_non_target_internal_connection_invalid_lane_count",
            "skipped_non_target_internal_connection_missing_via_lane_count",
            "missing_non_target_tllogic_count",
        )
        if int(internal_report.get(key, 0) or 0)
    }
    status = "pass" if not failures and not internal_failures else "fail"
    return {
        "status": status,
        "claim_status": "diagnostic-demo" if status == "pass" else "construction-invalid",
        "source_file": str(source_file),
        "target_file": str(target_file),
        "mutable_junction_ids": sorted(effective_mutable_junction_ids),
        "mutable_edge_ids": sorted(effective_mutable_edge_ids),
        "junction_aliases": dict(sorted(normalized_junction_aliases.items())),
        "declared_absorbed_edge_ids": (
            sorted(expected_absorbed_edge_ids)
            if expected_absorbed_edge_ids is not None
            else None
        ),
        "expanded_mutable_edge_endpoints": expand_mutable_edge_endpoints,
        "authorized_absorbed_external_edge_ids": authorized_absorbed_edge_ids,
        "undeleted_declared_absorbed_edge_ids": undeleted_declared_absorbed_edge_ids,
        "restored_join_boundary_edge_count": len(restored_join_boundary_edge_ids),
        "restored_join_boundary_edge_ids": restored_join_boundary_edge_ids,
        "restored_external_edge_count": len(restored_edge_ids),
        "restored_external_edge_ids": restored_edge_ids,
        "restored_external_edge_centerline_count": len(restored_edge_centerline_ids),
        "restored_external_edge_centerline_ids": restored_edge_centerline_ids,
        "restored_external_lane_count": restored_lane_count,
        "restored_lane_shapes_fixed_for_native_reload": True,
        "failure_count": len(failures) + sum(internal_failures.values()),
        "failures": failures,
        "internal_failure_counts": internal_failures,
        "internal_artifact_restore": internal_report,
    }
