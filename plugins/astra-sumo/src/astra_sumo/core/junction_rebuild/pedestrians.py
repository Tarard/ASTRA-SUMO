"""Rebuild and restore pedestrian rings, crossings and walking areas."""

from __future__ import annotations

import copy
import xml.etree.ElementTree as ET
from pathlib import Path
from .artifacts import _failure
from .geometry import _teacher_to_candidate_delta, _translate_shape, _translated_lane_attrs
from .network import (
    _connection_lane_indices_valid,
    _first_junction_index,
    _mapped_internal_ref,
    _net_lane_counts,
    _split,
)


def _pedestrian_tl_pairs_from_records(records: object, junction_id: str) -> dict[str, tuple[str, str]]:
    pairs: dict[str, tuple[str, str]] = {}
    internal_prefix = f":{junction_id}_"
    items = records if isinstance(records, list) else []
    for record in items:
        if not isinstance(record, dict) or str(record.get("tl", "")) != junction_id:
            continue
        source = str(record.get("from", ""))
        target = str(record.get("to", ""))
        if source.startswith(f"{internal_prefix}w") and target.startswith(f"{internal_prefix}c") and record.get("linkIndex"):
            pairs[str(record["linkIndex"])] = (source, target)
    return pairs


def _pedestrian_tl_pairs_from_connections(connections: list[ET.Element], junction_id: str) -> dict[str, tuple[str, str]]:
    pairs: dict[str, tuple[str, str]] = {}
    internal_prefix = f":{junction_id}_"
    for connection in connections:
        if connection.attrib.get("tl") != junction_id:
            continue
        source = connection.attrib.get("from", "")
        target = connection.attrib.get("to", "")
        if source.startswith(f"{internal_prefix}w") and target.startswith(f"{internal_prefix}c") and connection.attrib.get("linkIndex"):
            pairs[connection.attrib["linkIndex"]] = (source, target)
    return pairs


def _touches_target_pedestrian_ring(connection: ET.Element, internal_prefix: str) -> bool:
    source = connection.attrib.get("from", "")
    target = connection.attrib.get("to", "")
    return source.startswith(f"{internal_prefix}w") or source.startswith(f"{internal_prefix}c") or target.startswith(f"{internal_prefix}w") or target.startswith(f"{internal_prefix}c")


def _map_teacher_pedestrian_endpoint(
    edge_id: str,
    walkingarea_map: dict[str, str],
    crossing_map: dict[str, str],
    edge_map: dict[str, str],
) -> str | None:
    if edge_id in walkingarea_map:
        return walkingarea_map[edge_id]
    if edge_id in crossing_map:
        return crossing_map[edge_id]
    if edge_id.startswith(":"):
        return None
    return edge_map.get(edge_id, edge_id)


def _copy_teacher_walkingareas(
    root: ET.Element,
    *,
    junction_id: str,
    teacher_junction_id: str,
    teacher_junction: object,
    teacher_walkingareas: object,
) -> tuple[list[tuple[str, str]], int]:
    candidate_junction = root.find(f"junction[@id='{junction_id}']")
    if candidate_junction is None or not isinstance(teacher_junction, dict) or not isinstance(teacher_walkingareas, list):
        return [], 0
    try:
        dx = float(candidate_junction.attrib.get("x", "0")) - float(str(teacher_junction.get("x", "0")))
        dy = float(candidate_junction.attrib.get("y", "0")) - float(str(teacher_junction.get("y", "0")))
    except ValueError:
        dx = dy = 0.0

    copied = []
    copied_count = 0
    copied_edge_ids = []
    existing_edge_ids = {edge.attrib.get("id", "") for edge in root.findall("edge")}
    insert_index = _first_junction_index(root)
    for walkingarea in teacher_walkingareas:
        if not isinstance(walkingarea, dict):
            continue
        teacher_edge_id = str(walkingarea.get("edge_id", ""))
        candidate_edge_id = _mapped_internal_ref(teacher_edge_id, teacher_junction_id, junction_id)
        if not teacher_edge_id or not candidate_edge_id or candidate_edge_id in existing_edge_ids:
            if teacher_edge_id and candidate_edge_id and candidate_edge_id in existing_edge_ids:
                copied.append((teacher_edge_id, candidate_edge_id))
            continue
        edge = ET.Element("edge", {"id": candidate_edge_id, "function": "walkingarea"})
        for lane in walkingarea.get("lanes", []) or []:
            if not isinstance(lane, dict):
                continue
            lane_attrs = {str(key): str(value) for key, value in lane.items() if value not in (None, "")}
            if lane_attrs.get("id"):
                lane_attrs["id"] = _mapped_internal_ref(lane_attrs["id"], teacher_junction_id, junction_id)
            for attr in ("shape", "outlineShape", "customShape"):
                if lane_attrs.get(attr):
                    lane_attrs[attr] = _translate_shape(lane_attrs[attr], dx, dy)
            ET.SubElement(edge, "lane", lane_attrs)
        root.insert(insert_index, edge)
        insert_index += 1
        existing_edge_ids.add(candidate_edge_id)
        copied_edge_ids.append(candidate_edge_id)
        copied_count += 1
        copied.append((teacher_edge_id, candidate_edge_id))

    copied_lane_ids = [
        lane.attrib["id"]
        for candidate_edge_id in copied_edge_ids
        for edge in root.findall(f"edge[@id='{candidate_edge_id}']")
        for lane in edge.findall("lane")
        if lane.attrib.get("id")
    ]
    if copied_lane_ids:
        inc_lanes = _split(candidate_junction.attrib.get("incLanes", ""))
        candidate_junction.set("incLanes", " ".join([*inc_lanes, *copied_lane_ids]))
    return copied, copied_count


def restore_scoped_pedestrian_internal_semantics_after_normalize(
    *,
    source_net_file: Path,
    target_net_file: Path,
    junction_id: str,
    edge_map: dict[str, str] | None = None,
) -> dict[str, object]:
    """Restore scoped crossing/walkingarea edges dropped by netconvert.

    SUMO may discard a crossing whose OSM-side boundary edge was remapped or
    whose geometry is not reconstructible from plain vehicle links.  The
    teacher replay already contains the audited pedestrian layer, so this
    function overlays only the target cell's crossing/walkingarea edges and
    their connections, then verifies that every source element is represented.
    It never copies pedestrian infrastructure outside the target prefix.
    """

    if not source_net_file.exists() or not target_net_file.exists():
        return _failure("source or target normalized net file is missing")
    try:
        source_root = ET.parse(source_net_file).getroot()
        target_tree = ET.parse(target_net_file)
        target_root = target_tree.getroot()
    except (ET.ParseError, OSError, ValueError) as exc:
        return _failure(f"pedestrian semantic restore parse failed: {type(exc).__name__}: {exc}")

    edge_map = {str(key): str(value) for key, value in (edge_map or {}).items() if str(key) and str(value)}
    internal_prefix = f":{junction_id}_"
    pedestrian_functions = {"crossing", "walkingarea"}
    source_pedestrian_edges = {
        edge.attrib.get("id", ""): edge
        for edge in source_root.findall("edge")
        if edge.attrib.get("id", "").startswith(internal_prefix)
        and edge.attrib.get("function") in pedestrian_functions
    }
    target_edges = {
        edge.attrib.get("id", ""): edge
        for edge in target_root.findall("edge")
        if edge.attrib.get("id")
    }
    replaced_edge_ids: list[str] = []
    added_edge_ids: list[str] = []

    def mapped_pedestrian_edge(source_edge: ET.Element) -> ET.Element:
        clone = copy.deepcopy(source_edge)
        if clone.attrib.get("crossingEdges"):
            clone.set(
                "crossingEdges",
                " ".join(
                    edge_map.get(value, value)
                    for value in clone.attrib.get("crossingEdges", "").split()
                    if value
                ),
            )
        return clone

    for edge_id, source_edge in sorted(source_pedestrian_edges.items()):
        replacement = mapped_pedestrian_edge(source_edge)
        existing = target_edges.get(edge_id)
        if existing is None:
            target_root.append(replacement)
            target_edges[edge_id] = replacement
            added_edge_ids.append(edge_id)
            continue
        index = list(target_root).index(existing)
        target_root.remove(existing)
        target_root.insert(index, replacement)
        target_edges[edge_id] = replacement
        replaced_edge_ids.append(edge_id)

    source_pedestrian_connections = [
        connection
        for connection in source_root.findall("connection")
        if connection.attrib.get("from", "") in source_pedestrian_edges
        or connection.attrib.get("to", "") in source_pedestrian_edges
    ]

    def mapped_connection(source_connection: ET.Element) -> ET.Element:
        attrs = dict(source_connection.attrib)
        for attr in ("from", "to"):
            value = attrs.get(attr, "")
            if value and not value.startswith(":"):
                attrs[attr] = edge_map.get(value, value)
        return ET.Element("connection", attrs)

    def connection_key(connection: ET.Element) -> tuple[str, ...]:
        return tuple(
            connection.attrib.get(attr, "")
            for attr in ("from", "to", "fromLane", "toLane", "via", "tl", "linkIndex")
        )

    target_connections = {connection_key(connection): connection for connection in target_root.findall("connection")}
    added_connection_count = 0
    updated_connection_count = 0
    for source_connection in source_pedestrian_connections:
        mapped = mapped_connection(source_connection)
        key = connection_key(mapped)
        existing = target_connections.get(key)
        if existing is None:
            target_root.append(mapped)
            target_connections[key] = mapped
            added_connection_count += 1
            continue
        before = dict(existing.attrib)
        existing.attrib.clear()
        existing.attrib.update(mapped.attrib)
        if before != existing.attrib:
            updated_connection_count += 1

    missing_edge_ids = sorted(set(source_pedestrian_edges) - set(target_edges))
    missing_connection_keys = sorted(
        set(connection_key(mapped_connection(connection)) for connection in source_pedestrian_connections)
        - set(target_connections)
    )
    missing_crossing_edge_refs = []
    target_edge_ids = set(target_edges)
    for edge in target_edges.values():
        if edge.attrib.get("id") not in source_pedestrian_edges or not edge.attrib.get("crossingEdges"):
            continue
        for referenced_edge_id in edge.attrib.get("crossingEdges", "").split():
            if referenced_edge_id not in target_edge_ids:
                missing_crossing_edge_refs.append(
                    {"crossing_edge_id": edge.attrib.get("id", ""), "referenced_edge_id": referenced_edge_id}
                )
    status = "pass" if not missing_edge_ids and not missing_connection_keys and not missing_crossing_edge_refs else "blocked"
    if source_pedestrian_edges or source_pedestrian_connections:
        ET.indent(target_root, space="    ")
        target_tree.write(target_net_file, encoding="utf-8", xml_declaration=True)
    return {
        "status": status,
        "claim_status": "diagnostic-demo",
        "source_net_file": str(source_net_file),
        "target_net_file": str(target_net_file),
        "junction_id": junction_id,
        "source_pedestrian_edge_count": len(source_pedestrian_edges),
        "source_pedestrian_connection_count": len(source_pedestrian_connections),
        "replaced_pedestrian_edge_count": len(replaced_edge_ids),
        "replaced_pedestrian_edge_ids": replaced_edge_ids,
        "added_pedestrian_edge_count": len(added_edge_ids),
        "added_pedestrian_edge_ids": added_edge_ids,
        "added_pedestrian_connection_count": added_connection_count,
        "updated_pedestrian_connection_count": updated_connection_count,
        "missing_pedestrian_edge_ids": missing_edge_ids,
        "missing_pedestrian_connection_keys": missing_connection_keys,
        "missing_crossing_edge_refs": missing_crossing_edge_refs,
        "policy": "scoped teacher crossing/walkingarea overlay; no outside-cell pedestrian copy",
    }


def _apply_teacher_pedestrian_internal_geometry(
    root: ET.Element,
    *,
    junction_id: str,
    teacher_junction_id: str,
    teacher_junction: object,
    edge_maps: tuple[dict[str, str], ...],
    teacher_edges: object,
) -> int:
    if not isinstance(teacher_edges, tuple):
        return 0
    dx, dy = _teacher_to_candidate_delta(root, junction_id, teacher_junction)
    updated = 0
    for teacher_edge in teacher_edges:
        if not isinstance(teacher_edge, dict):
            continue
        teacher_edge_id = str(teacher_edge.get("edge_id", ""))
        candidate_edge_id = next((edge_map[teacher_edge_id] for edge_map in edge_maps if teacher_edge_id in edge_map), "")
        if not candidate_edge_id:
            candidate_edge_id = _mapped_internal_ref(teacher_edge_id, teacher_junction_id, junction_id)
        edge = root.find(f"edge[@id='{candidate_edge_id}']")
        lanes = teacher_edge.get("lanes", [])
        if edge is None or not isinstance(lanes, list):
            continue
        for lane in list(edge.findall("lane")):
            edge.remove(lane)
        for lane in lanes:
            if not isinstance(lane, dict):
                continue
            lane_attrs = _translated_lane_attrs(lane, teacher_junction_id, junction_id, dx, dy)
            ET.SubElement(edge, "lane", lane_attrs)
        updated += 1
    return updated


def write_teacher_pedestrian_ring_net(
    *,
    candidate_net_file: Path,
    output_file: Path,
    junction_id: str,
    teacher_model: dict[str, object],
    edge_map: dict[str, str],
    teacher_junction_id: str | None = None,
    crossing_edge_overrides: dict[str, str | list[str]] | None = None,
) -> dict[str, object]:
    teacher_junction_id = teacher_junction_id or junction_id
    crossing_edge_overrides = crossing_edge_overrides or {}
    output_file.parent.mkdir(parents=True, exist_ok=True)

    tree = ET.parse(candidate_net_file)
    root = tree.getroot()
    internal_prefix = f":{junction_id}_"
    candidate_crossings = {
        frozenset(_split(edge.attrib.get("crossingEdges", ""))): edge.attrib["id"]
        for edge in root.findall("edge")
        if edge.attrib.get("id", "").startswith(internal_prefix) and edge.attrib.get("function") == "crossing"
    }

    crossing_map: dict[str, str] = {}
    for crossing in teacher_model.get("crossings", []) or []:
        if not isinstance(crossing, dict):
            continue
        teacher_crossing_id = str(crossing.get("edge_id", ""))
        mapped_edges = crossing_edge_overrides.get(teacher_crossing_id)
        if mapped_edges is None:
            mapped_edges = [edge_map.get(str(edge), "") for edge in crossing.get("crossingEdges", []) or []]
        if isinstance(mapped_edges, str):
            mapped_edges = [mapped_edges]
        candidate_crossing_id = candidate_crossings.get(frozenset(edge for edge in mapped_edges if edge))
        if candidate_crossing_id:
            crossing_map[teacher_crossing_id] = candidate_crossing_id

    teacher_link_pairs = _pedestrian_tl_pairs_from_records(teacher_model.get("pedestrian_connections", []) or [], teacher_junction_id)
    candidate_link_pairs = _pedestrian_tl_pairs_from_connections(root.findall("connection"), junction_id)
    walkingarea_map: dict[str, str] = {}
    for link_index, (teacher_walkingarea, teacher_crossing) in teacher_link_pairs.items():
        candidate_pair = candidate_link_pairs.get(link_index)
        if not candidate_pair:
            continue
        walkingarea_map[teacher_walkingarea] = candidate_pair[0]
        crossing_map.setdefault(teacher_crossing, candidate_pair[1])

    copied_walkingareas = []
    copied_walkingarea_count = 0
    if not walkingarea_map:
        copied_walkingareas, copied_walkingarea_count = _copy_teacher_walkingareas(
            root,
            junction_id=junction_id,
            teacher_junction_id=teacher_junction_id,
            teacher_junction=teacher_model.get("junction", {}),
            teacher_walkingareas=teacher_model.get("walking_areas", []),
        )
        for teacher_edge_id, candidate_edge_id in copied_walkingareas:
            walkingarea_map[teacher_edge_id] = candidate_edge_id

    pedestrian_geometry_update_count = _apply_teacher_pedestrian_internal_geometry(
        root,
        junction_id=junction_id,
        teacher_junction_id=teacher_junction_id,
        teacher_junction=teacher_model.get("junction", {}),
        edge_maps=(crossing_map, walkingarea_map),
        teacher_edges=(
            *(teacher_model.get("crossings", []) or []),
            *(teacher_model.get("walking_areas", []) or []),
        ),
    )

    kept_walkingareas = set(walkingarea_map.values())
    removed_walkingareas = []
    for edge in list(root.findall("edge")):
        edge_id = edge.attrib.get("id", "")
        if edge_id.startswith(internal_prefix) and edge.attrib.get("function") == "walkingarea" and edge_id not in kept_walkingareas:
            root.remove(edge)
            removed_walkingareas.append(edge_id)

    removed_connections = 0
    for connection in list(root.findall("connection")):
        if _touches_target_pedestrian_ring(connection, internal_prefix):
            root.remove(connection)
            removed_connections += 1

    edge_ids = {edge.attrib["id"] for edge in root.findall("edge") if edge.attrib.get("id")}
    lane_counts = _net_lane_counts(root)
    inserted_connections = 0
    skipped_connections = []
    skipped_missing_edge_connections = []
    skipped_invalid_lane_connections = []
    for connection in teacher_model.get("pedestrian_connections", []) or []:
        if not isinstance(connection, dict):
            continue
        mapped_from = _map_teacher_pedestrian_endpoint(str(connection.get("from", "")), walkingarea_map, crossing_map, edge_map)
        mapped_to = _map_teacher_pedestrian_endpoint(str(connection.get("to", "")), walkingarea_map, crossing_map, edge_map)
        if not mapped_from or not mapped_to:
            skipped_connections.append(connection)
            continue
        if mapped_from not in edge_ids or mapped_to not in edge_ids:
            skipped_connections.append(connection)
            skipped_missing_edge_connections.append(
                {
                    "from": mapped_from,
                    "to": mapped_to,
                    "teacher_from": str(connection.get("from", "")),
                    "teacher_to": str(connection.get("to", "")),
                }
            )
            continue
        attributes = {
            "from": mapped_from,
            "to": mapped_to,
            "fromLane": str(connection.get("fromLane", "0") or "0"),
            "toLane": str(connection.get("toLane", "0") or "0"),
            "dir": str(connection.get("dir", "s") or "s"),
            "state": str(connection.get("state", "M") or "M"),
        }
        if connection.get("tl"):
            attributes["tl"] = junction_id
        if connection.get("linkIndex"):
            attributes["linkIndex"] = str(connection["linkIndex"])
        if not _connection_lane_indices_valid(ET.Element("connection", attributes), lane_counts):
            skipped_connections.append(connection)
            skipped_invalid_lane_connections.append(attributes)
            continue
        root.append(ET.Element("connection", attributes))
        inserted_connections += 1

    existing_lane_ids = {
        lane.attrib["id"]
        for edge in root.findall("edge")
        for lane in edge.findall("lane")
        if lane.attrib.get("id")
    }
    for junction in root.findall("junction"):
        if junction.attrib.get("id") != junction_id:
            continue
        for attr in ("incLanes", "intLanes"):
            junction.set(attr, " ".join(lane for lane in _split(junction.attrib.get(attr, "")) if lane in existing_lane_ids))

    ET.indent(root, space="    ")
    tree.write(output_file, encoding="utf-8", xml_declaration=True)
    return {
        "status": "pass",
        "claim_status": "diagnostic-demo",
        "net_file": str(output_file),
        "crossing_map_count": len(crossing_map),
        "walkingarea_map_count": len(walkingarea_map),
        "copied_walkingarea_count": copied_walkingarea_count,
        "pedestrian_geometry_update_count": pedestrian_geometry_update_count,
        "kept_walkingarea_count": len(kept_walkingareas),
        "removed_walkingarea_count": len(removed_walkingareas),
        "removed_pedestrian_connection_count": removed_connections,
        "inserted_pedestrian_connection_count": inserted_connections,
        "skipped_pedestrian_connection_count": len(skipped_connections),
        "skipped_pedestrian_connections": skipped_connections,
        "skipped_pedestrian_connection_missing_edge_count": len(skipped_missing_edge_connections),
        "skipped_pedestrian_connection_missing_edges": skipped_missing_edge_connections,
        "skipped_pedestrian_connection_invalid_lane_count": len(skipped_invalid_lane_connections),
        "skipped_pedestrian_connection_invalid_lanes": skipped_invalid_lane_connections,
    }
