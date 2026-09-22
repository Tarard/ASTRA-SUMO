"""Read and identify SUMO network elements used by junction reconstruction."""

from __future__ import annotations

from typing import Any
import xml.etree.ElementTree as ET
from pathlib import Path
import hashlib


GEOMETRY_RESTORE_LANE_ATTRS = (
    "speed",
    "shape",
    "length",
    "width",
    "endOffset",
    "customShape",
    "outlineShape",
)


ROAD_CONTINUITY_COUNT_FIELDS = (
    "same_family_continuation_edge_map_count",
    "copied_boundary_continuation_edge_count",
    "copied_boundary_continuation_connection_count",
    "replayed_stale_split_continuation_edge_count",
    "replayed_stale_split_followup_edge_count",
    "rewired_stale_split_fragment_connection_count",
    "removed_teacher_absent_same_family_continuation_edge_count",
)


ROAD_CONTINUITY_FAILURE_FIELDS = (
    "removed_stale_boundary_edge_connection_count",
    "removed_stale_replaced_edge_connection_count",
    "removed_invalid_lane_connection_count",
    "skipped_connection_count",
)


TLS_CONNECTION_REPAIR_ATTRS = (
    "tl",
    "linkIndex",
    "linkIndex2",
    "dir",
    "state",
    "pass",
    "allow",
    "disallow",
    "keepClear",
    "contPos",
)


TURNAROUND_DIR = "t"


def _split(value: str) -> list[str]:
    return [part for part in value.split() if part]


def _approaches(model: dict[str, object], direction: str) -> list[dict[str, Any]]:
    approaches = model.get("approaches", {})
    if not isinstance(approaches, dict):
        return []
    return [edge for edge in approaches.get(direction, []) or [] if isinstance(edge, dict)]


def _via_lane_edge_id(via_lane_id: str) -> str:
    if not via_lane_id:
        return ""
    if via_lane_id.startswith(":") and "_" in via_lane_id:
        return via_lane_id.rsplit("_", 1)[0]
    return via_lane_id


def _connection_touches_walkingarea_internal(connection: dict[str, Any]) -> bool:
    return any(
        ref.startswith(":") and "_w" in ref
        for ref in (str(connection.get(field, "")) for field in ("from", "to", "via"))
    )


def _connection_touches_any_edge(connection: dict[str, Any], edge_ids: set[str]) -> bool:
    return bool(edge_ids) and any(str(connection.get(field, "")) in edge_ids for field in ("from", "to"))


def _net_junction_ids(net_file: Path) -> set[str]:
    return {
        junction.attrib["id"]
        for junction in ET.parse(net_file).getroot().findall("junction")
        if junction.attrib.get("id")
    }


def _target_internal_replay_input_file(
    *,
    vehicle_attrs_net_file: Path,
    candidate_net_file: Path,
    junction_id: str,
) -> Path:
    if junction_id in _net_junction_ids(vehicle_attrs_net_file):
        return vehicle_attrs_net_file
    if junction_id in _net_junction_ids(candidate_net_file):
        return candidate_net_file
    return vehicle_attrs_net_file


def _unique_connections_by_key(root: ET.Element) -> tuple[dict[tuple[str, str, str, str], ET.Element], set[tuple[str, str, str, str]]]:
    connections_by_key: dict[tuple[str, str, str, str], list[ET.Element]] = {}
    for connection in root.findall("connection"):
        key = _connection_key(connection)
        connections_by_key.setdefault(key, []).append(connection)
    duplicate_keys = {key for key, connections in connections_by_key.items() if len(connections) > 1}
    return (
        {key: connections[0] for key, connections in connections_by_key.items() if len(connections) == 1},
        duplicate_keys,
    )


def _connection_key(connection: ET.Element) -> tuple[str, str, str, str]:
    return (
        connection.attrib.get("from", ""),
        connection.attrib.get("to", ""),
        connection.attrib.get("fromLane", "0"),
        connection.attrib.get("toLane", "0"),
    )


def _connection_key_record(key: tuple[str, str, str, str]) -> dict[str, str]:
    return {"from": key[0], "to": key[1], "fromLane": key[2], "toLane": key[3]}


def _controlled_tls_connection_count(root: ET.Element) -> int:
    return sum(
        1
        for connection in root.findall("connection")
        if connection.attrib.get("tl") and connection.attrib.get("linkIndex")
    )


def _connection_link_indices(connection: ET.Element) -> set[int]:
    indices = set()
    for attr in ("linkIndex", "linkIndex2"):
        value = connection.attrib.get(attr, "")
        if value:
            try:
                indices.add(int(value))
            except ValueError:
                pass
    return indices


def _mapped_internal_ref(value: str, source_junction_id: str, target_junction_id: str) -> str:
    source_prefix = f":{source_junction_id}_"
    target_prefix = f":{target_junction_id}_"
    if source_junction_id and target_junction_id and value.startswith(source_prefix):
        return f"{target_prefix}{value[len(source_prefix):]}"
    return value


def _dict_mismatch_count(left: dict[str, str], right: dict[str, str]) -> int:
    return sum(1 for key in set(left) | set(right) if left.get(key) != right.get(key))


def _vehicle_outgoing_by_lane(model: dict[str, object]) -> dict[tuple[str, str], dict[str, object]]:
    by_lane: dict[tuple[str, str], dict[str, object]] = {}
    for connection in model.get("vehicle_connections", []) or []:
        if not isinstance(connection, dict):
            continue
        source = str(connection.get("from", ""))
        if not source:
            continue
        lane = str(connection.get("fromLane", ""))
        stats = by_lane.setdefault(
            (source, lane),
            {
                "turnaround_count": 0,
                "non_turnaround_count": 0,
                "non_turnaround_targets": set(),
            },
        )
        if _is_turnaround_connection(connection):
            stats["turnaround_count"] = int(stats["turnaround_count"]) + 1
        else:
            stats["non_turnaround_count"] = int(stats["non_turnaround_count"]) + 1
            target = str(connection.get("to", ""))
            if target:
                stats["non_turnaround_targets"].add(target)
    return by_lane


def _root_vehicle_outgoing_by_lane(root: ET.Element) -> dict[tuple[str, str], dict[str, object]]:
    edges = {
        edge.attrib["id"]: edge
        for edge in root.findall("edge")
        if edge.attrib.get("id") and not edge.attrib["id"].startswith(":")
    }
    by_lane: dict[tuple[str, str], dict[str, object]] = {}
    for connection in root.findall("connection"):
        source = connection.attrib.get("from", "")
        target = connection.attrib.get("to", "")
        if source not in edges or target not in edges:
            continue
        if _edge_is_pedestrian_only(edges[source]) or _edge_is_pedestrian_only(edges[target]):
            continue
        lane = connection.attrib.get("fromLane", "")
        stats = by_lane.setdefault(
            (source, lane),
            {"turnaround_count": 0, "non_turnaround_count": 0, "non_turnaround_targets": set()},
        )
        if _is_turnaround_connection(connection.attrib):
            stats["turnaround_count"] = int(stats["turnaround_count"]) + 1
        else:
            stats["non_turnaround_count"] = int(stats["non_turnaround_count"]) + 1
            stats["non_turnaround_targets"].add(target)
    return by_lane


def _edge_is_vehicle_continuation_candidate(edge: ET.Element) -> bool:
    if edge.attrib.get("function") in {"internal", "crossing", "walkingarea"} or _edge_is_pedestrian_only(edge):
        return False
    edge_types = edge.attrib.get("type", "").split("|")
    return any(edge_type.startswith("highway.") for edge_type in edge_types) or any(
        set((lane.attrib.get("allow") or "").split())
        & {"passenger", "private", "bus", "coach", "truck", "motorcycle", "moped", "taxi", "delivery", "emergency"}
        for lane in edge.findall("lane")
    )


def _map_lane_ref(
    lane_id: str,
    edge_map: dict[str, str],
    teacher_internal_prefix: str,
    candidate_internal_prefix: str,
) -> str:
    if lane_id.startswith(teacher_internal_prefix):
        return _map_internal_ref(lane_id, teacher_internal_prefix, candidate_internal_prefix)
    if "_" not in lane_id:
        return ""
    edge_id, lane_index = lane_id.rsplit("_", 1)
    mapped_edge = edge_map.get(edge_id)
    return f"{mapped_edge}_{lane_index}" if mapped_edge else ""


def _map_connection_endpoint(
    value: str,
    edge_map: dict[str, str],
    teacher_internal_prefix: str,
    candidate_internal_prefix: str,
    candidate_edge_ids: set[str],
) -> str:
    if value.startswith(teacher_internal_prefix):
        return _map_internal_ref(value, teacher_internal_prefix, candidate_internal_prefix)
    return edge_map.get(value, value if value in candidate_edge_ids else "")


def _touches_target_replay_scope(
    connection: ET.Element,
    internal_prefix: str,
    junction_id: str,
    edges_by_id: dict[str, ET.Element],
) -> bool:
    if _touches_target_internal_owner(connection, internal_prefix):
        return True
    if connection.attrib.get("tl") != junction_id:
        return False
    if _touches_other_internal_owner(connection, internal_prefix):
        return False
    return any(
        junction_id in (edge.attrib.get("from"), edge.attrib.get("to"))
        for edge_id in (connection.attrib.get("from", ""), connection.attrib.get("to", ""))
        for edge in [edges_by_id.get(edge_id)]
        if edge is not None
    )


def _plain_crossing_node_id(
    default_junction_id: str,
    crossing_edges: list[str],
    edge_endpoints: dict[str, tuple[str, str]],
    crossing_node_ids: set[str],
) -> str:
    if not crossing_edges or not edge_endpoints or not crossing_node_ids:
        return default_junction_id
    shared_node_ids: set[str] | None = None
    for edge_id in crossing_edges:
        endpoints = set(edge_endpoints.get(edge_id, ())) & crossing_node_ids
        if not endpoints:
            return default_junction_id
        shared_node_ids = endpoints if shared_node_ids is None else shared_node_ids & endpoints
        if not shared_node_ids:
            return default_junction_id
    return sorted(shared_node_ids)[0] if shared_node_ids else default_junction_id


def _string_list(value: Any) -> list[str]:
    return [str(item) for item in value or [] if str(item)]


def _report_used_unrestored_normalized_replay(report: dict[str, Any]) -> bool:
    target_internal_normalize = report.get("target_internal_normalize", {})
    if not isinstance(target_internal_normalize, dict):
        return False
    unrestored_sumo_load = target_internal_normalize.get("unrestored_sumo_load", {})
    return isinstance(unrestored_sumo_load, dict) and unrestored_sumo_load.get("status") == "pass"


def _net_contains_normal_junctions(net_file: Path, junction_ids: set[str]) -> bool:
    if not junction_ids:
        return True
    try:
        root = ET.parse(net_file).getroot()
    except (OSError, ET.ParseError):
        return False
    net_junction_ids = {
        junction.attrib["id"]
        for junction in root.findall("junction")
        if junction.attrib.get("id") and not junction.attrib["id"].startswith(":")
    }
    return junction_ids <= net_junction_ids


def _int_count(value: object) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _connection_edges_are_adjacent(connection: ET.Element, edge_endpoints: dict[str, tuple[str, str]]) -> bool:
    source = edge_endpoints.get(connection.attrib.get("from", ""))
    target = edge_endpoints.get(connection.attrib.get("to", ""))
    return bool(source and target and source[1] == target[0])


def _edge_file_ids(edge_file: Path) -> set[str]:
    try:
        return {
            edge.attrib["id"]
            for edge in ET.parse(edge_file).getroot().findall("edge")
            if edge.attrib.get("id")
        }
    except (ET.ParseError, OSError):
        return set()


def _plain_node_ids(node_file: Path) -> set[str]:
    try:
        return {
            node.attrib["id"]
            for node in ET.parse(node_file).getroot().findall("node")
            if node.attrib.get("id")
        }
    except (ET.ParseError, OSError):
        return set()


def _opposite_direction_edge_id(edge_id: str) -> str:
    return edge_id[1:] if edge_id.startswith("-") else f"-{edge_id}"


def _edge_family_id(edge_id: str) -> str:
    return edge_id.lstrip("-").split("#", 1)[0]


def _signed_edge_family_id(edge_id: str) -> str:
    return edge_id.split("#", 1)[0]


def _edge_drop_requires_review(edge: ET.Element) -> bool:
    edge_id = edge.attrib.get("id", "")
    if edge_id.startswith(":") or edge.attrib.get("function") in {"internal", "crossing", "walkingarea"}:
        return False
    if edge.attrib.get("type", "").startswith("highway."):
        return True
    vehicle_classes = {
        "passenger",
        "private",
        "bus",
        "coach",
        "truck",
        "trailer",
        "motorcycle",
        "moped",
        "taxi",
        "delivery",
        "emergency",
    }
    for lane in edge.findall("lane"):
        if set(lane.attrib.get("allow", "").split()) & vehicle_classes:
            return True
    return False


def _plain_edge_endpoints(edge_file: Path) -> dict[str, tuple[str, str]]:
    try:
        root = ET.parse(edge_file).getroot()
    except (ET.ParseError, OSError):
        return {}
    return {
        edge.attrib["id"]: (edge.attrib.get("from", ""), edge.attrib.get("to", ""))
        for edge in root.findall("edge")
        if edge.attrib.get("id")
    }


def _should_emit(movement: dict[str, object]) -> bool:
    return movement.get("status") == "emit" and float(movement.get("confidence", 0.0)) >= 0.5


def _real_junction_ids(root: ET.Element) -> set[str]:
    return {
        junction.attrib["id"]
        for junction in root.findall("junction")
        if junction.attrib.get("id")
        and not junction.attrib["id"].startswith(":")
        and junction.attrib.get("type") != "internal"
    }


def _stable_digest(value: str) -> str:
    return hashlib.sha1(value.encode("utf-8")).hexdigest()[:8]


def _write_connections(
    path: Path,
    movements: list[dict[str, object]],
) -> None:
    root = ET.Element("connections")
    for movement in movements:
        ET.SubElement(
            root,
            "connection",
            {
                "from": str(movement.get("source_edge_id") or movement.get("from_edge_id") or ""),
                "to": str(movement.get("target_edge_id") or movement.get("to_edge_id") or ""),
                "fromLane": str(movement.get("fromLane", "0") or "0"),
                "toLane": str(movement.get("toLane", "0") or "0"),
            },
        )
    ET.ElementTree(root).write(path, encoding="utf-8", xml_declaration=True)


def _approach_edges(candidate_model: dict[str, object], direction: str) -> list[str]:
    approaches = candidate_model.get("approaches", {})
    if not isinstance(approaches, dict):
        return []
    return [
        str(edge.get("edge_id", ""))
        for edge in approaches.get(direction, []) or []
        if isinstance(edge, dict) and edge.get("edge_id")
    ]


def _is_turnaround_connection(connection: dict[str, object]) -> bool:
    return str(connection.get("dir", "")).lower() == TURNAROUND_DIR


def _candidate_lane_counts(candidate_model: dict[str, object]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for direction in ("incoming", "outgoing"):
        approaches = candidate_model.get("approaches", {})
        if not isinstance(approaches, dict):
            continue
        for edge in approaches.get(direction, []) or []:
            if isinstance(edge, dict) and edge.get("edge_id"):
                counts[str(edge["edge_id"])] = max(1, int(edge.get("lane_count", 1) or 1))
    return counts


def _edge_file_lane_counts(edge_file: Path) -> dict[str, int]:
    counts: dict[str, int] = {}
    for edge in ET.parse(edge_file).getroot().findall("edge"):
        edge_id = edge.attrib.get("id")
        if not edge_id:
            continue
        lanes = edge.findall("lane")
        if lanes:
            counts[edge_id] = len(lanes)
        elif edge.attrib.get("numLanes"):
            counts[edge_id] = max(1, int(edge.attrib["numLanes"]))
    return counts


def _net_lane_counts(root: ET.Element) -> dict[str, int]:
    counts: dict[str, int] = {}
    for edge in root.findall("edge"):
        edge_id = edge.attrib.get("id")
        if edge_id:
            counts[edge_id] = max(1, len(edge.findall("lane")))
    return counts


def _connection_lane_indices_valid(connection: ET.Element, lane_counts: dict[str, int]) -> bool:
    def _valid(edge_id: str, lane_index: str) -> bool:
        try:
            index = int(lane_index or "0")
        except ValueError:
            return False
        return 0 <= index < lane_counts.get(edge_id, 0)

    return _valid(connection.attrib.get("from", ""), connection.attrib.get("fromLane", "0")) and _valid(
        connection.attrib.get("to", ""),
        connection.attrib.get("toLane", "0"),
    )


def _edge_is_pedestrian_only(edge: ET.Element) -> bool:
    lanes = edge.findall("lane")
    return bool(lanes) and all(set((lane.attrib.get("allow") or "").split()) == {"pedestrian"} for lane in lanes)


def _edge_lane_count(edge: ET.Element) -> int:
    return max(1, len(edge.findall("lane")))


def _edge_type_signature(edge: ET.Element) -> str:
    return edge.attrib.get("type", "")


def _lanes_by_index(edge: ET.Element) -> dict[str, ET.Element]:
    return {lane.attrib.get("index", ""): lane for lane in edge.findall("lane") if lane.attrib.get("index", "")}


def _first_junction_index(root: ET.Element) -> int:
    for index, child in enumerate(list(root)):
        if child.tag == "junction":
            return index
    return len(list(root))


def _map_internal_ref(value: str, teacher_internal_prefix: str, candidate_internal_prefix: str) -> str:
    if teacher_internal_prefix and candidate_internal_prefix and value.startswith(teacher_internal_prefix):
        return f"{candidate_internal_prefix}{value[len(teacher_internal_prefix):]}"
    return value


def _touches_target_internal_subgraph(connection: ET.Element, internal_prefix: str, junction_id: str) -> bool:
    return (
        connection.attrib.get("from", "").startswith(internal_prefix)
        or connection.attrib.get("to", "").startswith(internal_prefix)
        or connection.attrib.get("via", "").startswith(internal_prefix)
        or connection.attrib.get("tl", "") == junction_id
    )


def _touches_target_internal_owner(connection: ET.Element, internal_prefix: str) -> bool:
    return any(
        connection.attrib.get(attr, "").startswith(internal_prefix)
        for attr in ("from", "to", "via")
    )


def _touches_other_internal_owner(connection: ET.Element, internal_prefix: str) -> bool:
    return any(
        value.startswith(":") and not value.startswith(internal_prefix)
        for value in (connection.attrib.get(attr, "") for attr in ("from", "to", "via"))
        if value
    )
