"""Plan and check source-bound road edits with no site-specific identifiers."""

from __future__ import annotations

from copy import deepcopy
import math
from pathlib import Path
import shutil
import time
import xml.etree.ElementTree as ET

from shapely.geometry import LineString, Polygon

from .artifact_io import copy_file_atomic, write_json_atomic
from .candidate_contracts import file_sha256
from .connection_mode_audit import audit_network_connection_mode
from .junction_rebuild.restoration import restore_off_scope_netconvert_artifacts
from .network_geometry_freeze import compare_frozen_geometry
from .sumo_commands import run_sumo_load_audit


SCOPED_EDIT_TYPES = (
    "join_junctions", "absorb_reciprocal_fragment", "suppress_turnaround",
    "set_junction_shape", "add_lane_connection", "delete_lane_connection",
    "relocate_tls_control", "demote_duplicate_tls", "set_lane_permissions", "set_lane_width",
    "set_edge_shape", "set_lane_count", "move_stop_line", "set_speed",
)
_CONNECTION_EDITS = {"add_lane_connection", "delete_lane_connection", "suppress_turnaround"}
_JOIN_EDITS = {"join_junctions", "absorb_reciprocal_fragment"}
_PARAMETERS = {
    **{kind: {"from", "fromLane", "to", "toLane"} for kind in _CONNECTION_EDITS},
    **{kind: {"node_ids", "join_id", "signal_policy", "interior_lane_change_policy"} for kind in _JOIN_EDITS},
    "set_junction_shape": {"node_id", "shape"},
    "demote_duplicate_tls": {"node_id", "retained_node_id"},
    "relocate_tls_control": {"from_node", "to_node", "tls_id", "link_mapping"},
    "set_lane_permissions": {"lane_id", "allow", "disallow"},
    "set_lane_width": {"lane_id", "width_m"},
    "set_edge_shape": {"edge_id", "shape"},
    "set_lane_count": {"edge_id", "num_lanes", "remove_index", "add_side", "connections"},
    "move_stop_line": {"lane_id", "end_offset_m"},
    "set_speed": {"lane_id", "speed_m_s"},
}
_PARAMETERS["add_lane_connection"] |= {"allow", "disallow"}
_PARAMETERS["absorb_reciprocal_fragment"] |= {"maximum_length_m"}


def _pair(record):
    values = tuple(record[key] for key in ("from", "fromLane", "to", "toLane"))
    if any(type(values[i]) is not int or values[i] < 0 for i in (1, 3)):
        raise ValueError("Connection lane indices must be nonnegative integers.")
    return str(values[0]), values[1], str(values[2]), values[3]


def _key(connection):
    return (connection.get("from"), int(connection.get("fromLane", "-1")),
            connection.get("to"), int(connection.get("toLane", "-1")))


def _shape(value):
    if (not isinstance(value, (list, tuple)) or len(value) < 3
            or any(not isinstance(p, (list, tuple)) or len(p) != 2 for p in value)
            or any(type(v) not in (int, float) or not math.isfinite(v) for p in value for v in p)):
        raise ValueError("Junction shape requires at least three finite XY points.")
    polygon = Polygon(value)
    if not polygon.is_valid or polygon.area <= 0:
        raise ValueError("Junction shape must be a valid polygon with positive area.")
    serialized = " ".join(f"{x:.12f},{y:.12f}" for x, y in value)
    written = Polygon([tuple(map(float, point.split(","))) for point in serialized.split()])
    if not written.is_valid or written.area <= 0:
        raise ValueError("Junction shape must remain valid at the native coordinate precision.")
    return serialized


def _inventory(root):
    edges = {e.get("id"): e for e in root.findall("edge") if not e.get("function")}
    nodes = {n.get("id"): n for n in root.findall("junction") if n.get("type") != "internal"}
    connections = {_key(c): c for c in root.findall("connection")}
    return edges, nodes, connections


def _line_shape(value):
    from .network_calibration import _points
    points = _points(value)
    if LineString(points).length <= 0:
        raise ValueError("Road shape must have positive length.")
    return " ".join(f"{x:.12f},{y:.12f}" for x, y in points)


def write_direct_lane_edit(source, candidate, operation):
    from .osm_access import _patch_lane_attributes
    params = operation["params"]
    if operation["operation"] == "move_stop_line":
        raw = source.read_bytes()
        root = ET.fromstring(raw)
        patches = _stop_line_patches(root, params)
        if patches is None:
            raise ValueError("Saved lane geometry cannot support a direct stop-line edit.")
        candidate.write_bytes(_patch_lane_attributes(raw, root, patches, {"shape", "length", "endOffset"}))
        return
    attrs = ({key: params[key] for key in ("allow", "disallow") if key in params}
             if operation["operation"] == "set_lane_permissions" else {"speed": str(params["speed_m_s"])})
    raw = source.read_bytes()
    candidate.write_bytes(_patch_lane_attributes(raw, ET.fromstring(raw), {params["lane_id"]: attrs},
                         {"allow", "disallow"} if operation["operation"] == "set_lane_permissions" else {"speed"}))


def _stop_line_patches(root, params):
    """Transfer a saved approach prefix between the lane and its first internal paths."""
    lanes = {lane.get("id"): lane for edge in root.findall("edge") for lane in edge.findall("lane")}
    target = lanes[params["lane_id"]]
    old_offset = float(target.get("endOffset", "0"))
    if not math.isfinite(old_offset) or old_offset < 0:
        raise ValueError("The source stop-line offset must be finite and nonnegative.")
    delta = params["end_offset_m"] - old_offset
    if delta == 0:
        return {}
    edge_id, index = next((edge.get("id"), lane.get("index")) for edge in root.findall("edge")
                          for lane in edge.findall("lane") if lane is target)
    connections = [row for row in root.findall("connection") if row.get("from") == edge_id and row.get("fromLane") == index]
    internal_ids = {row.get("via") for row in connections}
    if (not connections or None in internal_ids or internal_ids - lanes.keys()
            or any(row.get("via") in internal_ids
                   and (row.get("from"), row.get("fromLane")) != (edge_id, index) for row in root.findall("connection"))):
        return None

    def line(lane):
        points = [tuple(map(float, point.split(","))) for point in lane.get("shape", "").split()]
        length = float(lane.get("length", "nan"))
        if (len(points) < 2
                or any(len(point) not in (2, 3) or not all(math.isfinite(value) for value in point) for point in points)
                or not math.isfinite(length) or length <= 0):
            raise ValueError("Saved stop-line paths require finite geometry and positive driving lengths.")
        # SUMO omits zero elevations on individual vertices of a 3D shape.
        return LineString([(*point, 0.0) if len(point) == 2 else point for point in points])

    def spatial_length(shape):
        return sum(math.dist(a, b) for a, b in zip(shape.coords, list(shape.coords)[1:]))

    def split(shape, offset):
        points, seen = list(shape.coords), 0.0
        for i, (a, b) in enumerate(zip(points, points[1:])):
            segment = math.dist(a, b)
            if seen + segment + 1e-8 >= offset:
                ratio = min(1.0, max(0.0, (offset - seen) / segment)) if segment else 0.0
                point = tuple(x + ratio * (y - x) for x, y in zip(a, b))
                return LineString([*points[:i + 1], point]), LineString([point, *points[i + 1:]])
            seen += segment
        raise ValueError("The requested stop-line cut exceeds its saved path.")

    approach = line(target)
    paths = {key: line(lanes[key]) for key in internal_ids}
    if any(math.dist(approach.coords[-1], path.coords[0]) > 0.1
           for path in paths.values()):
        return None
    if delta > 0:
        if delta >= spatial_length(approach):
            return None
        shortened, prefix = split(approach, spatial_length(approach) - delta)
        shapes = {key: [*prefix.coords, *list(path.coords)[1:]] if prefix.coords[-1] == path.coords[0]
                  else [*prefix.coords, *path.coords] for key, path in paths.items()}
    else:
        amount = -delta
        if any(amount > spatial_length(path) + 1e-8 for path in paths.values()):
            return None
        pieces = {key: split(path, amount) for key, path in paths.items()}
        prefixes = [pieces[key][0] for key in sorted(pieces)]
        prefix = prefixes[0]
        # Extending across a movement-dependent curve has no unique physical lane end.
        if any(len(prefix.coords) != len(other.coords)
               or any(math.dist(a, b) > 1e-8 for a, b in zip(prefix.coords, other.coords))
               for other in prefixes[1:]):
            return None
        points = [*approach.coords, *list(prefix.coords)[1:]] if approach.coords[-1] == prefix.coords[0] else [*approach.coords, *prefix.coords]
        shortened = LineString(points)
        shapes = {key: list(pieces[key][1].coords) for key in paths}

    def attributes(lane, points, length):
        points = list(points)
        if len(points) == 1:
            points *= 2
        if (not any(len(point.split(",")) == 3 for point in lane.get("shape", "").split())
                and all(point[2] == 0 for point in points)):
            points = [point[:2] for point in points]
        return {**({"endOffset": lane.get("endOffset")} if lane.get("endOffset") is not None else {}),
                "shape": " ".join(",".join(f"{value:.12f}" for value in point) for point in points),
                "length": f"{length:.12f}"}

    patches = {key: attributes(lanes[key], points, float(lanes[key].get("length")) + delta) for key, points in shapes.items()}
    patches[params["lane_id"]] = {**attributes(target, shortened.coords, float(target.get("length")) - delta),
                                  "endOffset": str(params["end_offset_m"])}
    affected = [edge for edge in root.findall("edge") if any(lane.get("id") in patches for lane in edge.findall("lane"))]
    affected_lanes = {lane.get("id") for edge in affected for lane in edge.findall("lane")}
    if (any(edge.get("length") is not None or edge.get("bidi") for edge in affected)
            or any(row.get("via") in affected_lanes and row.get("length") is not None for row in root.findall("connection"))):
        return None
    # Normal roads and straight internal groups share a logical length in native SUMO.
    for edge in affected:
        members = edge.findall("lane")
        if edge.get("function") == "internal" and len(members) > 1:
            member_ids = {lane.get("id") for lane in members}
            directions = {row.get("dir") for row in root.findall("connection") if row.get("via") in member_ids}
            # Turning lanes may use separate lengths unless junctions.join-turns is enabled.
            if not directions or not directions <= {"s", "t", "T"}:
                return None
        if any(not math.isfinite(float(lane.get("length", "nan"))) or float(lane.get("length", "0")) <= 0 for lane in members):
            raise ValueError("Saved edge members require finite positive logical lengths.")
        mean = max(0.1, sum(float(patches.get(lane.get("id"), {}).get("length", lane.get("length"))) for lane in members) / len(members))
        for lane in members:
            key = lane.get("id")
            attrs = patches.setdefault(key, {name: lane.get(name) for name in ("shape", "length", "endOffset") if lane.get(name) is not None})
            attrs["length"] = f"{mean:.12f}"
    return patches


def _lane_count_layout(params, edge):
    old_count, new_count = len(edge.findall("lane")), params["num_lanes"]
    if "remove_index" in params:
        if new_count != old_count - 1 or params["remove_index"] >= old_count:
            raise ValueError("remove_index requires removing exactly one existing lane.")
        survivors = [index for index in range(old_count) if index != params["remove_index"]]
        return {old: new for new, old in enumerate(survivors)}
    if "add_side" in params and new_count != old_count + 1:
        raise ValueError("add_side requires adding exactly one lane.")
    return {index: index + (1 if params.get("add_side") == "right" else 0)
            for index in range(min(old_count, new_count))}


def _patch_permissions(attributes, network_version):
    from .osm_access import _permission_set
    allowed = _permission_set(attributes, network_version=network_version)
    return {"allow": " ".join(sorted(allowed))} if allowed else {"disallow": "all"}


def _lane_count_connections(params, edges, connections, *, network_version=None):
    target = params["edge_id"]
    mapping = _lane_count_layout(params, edges[target])
    if "connections" in params:
        return [{key: str(value) for key, value in row.items()} for row in params["connections"]]
    result = []
    for pair, row in connections.items():
        if target not in (pair[0], pair[2]) or pair[0] not in edges or pair[2] not in edges:
            continue
        if any(edge == target and index not in mapping for edge, index in (pair[:2], pair[2:])):
            continue
        record = {key: row.get(key) for key in ("from", "fromLane", "to", "toLane", "allow", "disallow") if row.get(key) is not None}
        if record.keys() & {"allow", "disallow"}:
            permissions = _patch_permissions(record, network_version)
            record = {key: value for key, value in record.items() if key not in {"allow", "disallow"}}
            record.update(permissions)
        for end, lane_key in (("from", "fromLane"), ("to", "toLane")):
            if record[end] == target:
                record[lane_key] = str(mapping[int(record[lane_key])])
        result.append(record)
    return result


def _remap_lane_count_facts(facts_path, plan, output):
    """Keep physical references fixed when an explicit lane-count edit renumbers their SUMO lanes."""
    import json
    table = json.loads(facts_path.read_text(encoding="utf-8"))
    target, mapping = plan["operation"]["params"]["edge_id"], plan["lane_index_mapping"]
    source = ET.parse(plan["source"]["path"]).getroot()
    source_lanes = {lane.get("id"): lane.get("index") for edge in source.findall("edge") if edge.get("id") == target
                    for lane in edge.findall("lane")}
    renamed = {}
    def lane_id(value):
        if not isinstance(value, str) or value not in source_lanes:
            return value
        index = source_lanes[value]
        result = f"{target}_{mapping[index]}" if index in mapping else f"removed-by-edit:{value}"
        renamed[value] = result
        return result
    for fact in table["facts"]:
        selector = fact["selector"]
        if "lane_id" in selector:
            selector["lane_id"] = lane_id(selector["lane_id"])
        for key in ("lane_ids", "incoming_lane_ids", "outgoing_lane_ids", "lane_pair"):
            if isinstance(selector.get(key), list):
                selector[key] = [lane_id(value) for value in selector[key]]
        if (fact["kind"] == "movement_inventory" and isinstance(fact.get("expected"), list)
                and all(isinstance(pair, list) and len(pair) == 2 for pair in fact["expected"])):
            fact["expected"] = [[lane_id(value) for value in pair] for pair in fact["expected"]]
    for source in table["sources"]:
        source["path"] = str((facts_path.parent / source["path"]).resolve())
    write_json_atomic(output, table, sort_keys=True)
    return {"source_facts": {"path": str(facts_path), "sha256": file_sha256(facts_path)},
            "candidate_facts": {"path": str(output), "sha256": file_sha256(output)},
            "lane_id_mapping": renamed, "basis": "declared_lane_index_mapping_with_unchanged_physical_reference_geometry"}


def driving_integrity(root):
    from .source_movement_support import _candidate_modes, _index, _modes
    try:
        index = _index(root)
        broken = []
        for pair, start, target, connection in index["movements"]:
            modes = index["lanes"][start][2] & index["lanes"][target][2] & _modes(connection)
            if modes and not _candidate_modes(index, start, target, connection)[1]:
                broken.append(list(pair))
        return {"status": "blocked" if broken else "pass", "broken_internal_paths": broken}
    except (KeyError, ValueError) as error:
        return {"status": "blocked", "error": str(error)}


def _controllers(root, edges, node_id):
    from .source_movement_support import _index
    index = _index(root)
    return {c.get("tl") for c in root.findall("connection") if c.get("tl") and node_id == (
        index["owners"].get(c.get("via"))
        or index["owners"].get(index["keys"].get((c.get("from"), int(c.get("fromLane", "-1")))))
        or edges.get(c.get("from"), {}).get("to"))}


def validate_scoped_operation(operation: dict, root: ET.Element | None = None) -> list[dict]:
    """Validate the edit itself. Ledger evidence and rollback are checked separately."""
    kind, params = operation["operation"], operation.get("params", {})
    errors = []
    try:
        if kind not in _PARAMETERS or set(params) - _PARAMETERS[kind]:
            raise ValueError("Edit type or parameters are unsupported.")
        if kind in _CONNECTION_EDITS:
            _pair(params)
        elif kind in _JOIN_EDITS:
            members = params["node_ids"]
            if (not isinstance(members, list) or len(members) < 2 or any(not isinstance(n, str) or not n for n in members)
                    or len(set(members)) != len(members) or not isinstance(params["join_id"], str) or not params["join_id"]):
                raise ValueError("A join requires distinct source nodes and a target ID.")
            if params.get("signal_policy", "preserve") not in {"preserve", "rebuild_diagnostic"}:
                raise ValueError("Join signal policy must preserve control or explicitly rebuild diagnostic control.")
            if params.get("interior_lane_change_policy", "fixed_paths") not in {"fixed_paths", "permitted_interior"}:
                raise ValueError("The interior lane-change policy is unsupported.")
            if kind == "absorb_reciprocal_fragment":
                limit = params["maximum_length_m"]
                if len(members) != 2 or type(limit) not in (int, float) or not math.isfinite(limit) or limit <= 0:
                    raise ValueError("A reciprocal absorption requires two nodes and an explicit positive length limit.")
        elif kind == "set_junction_shape":
            if not isinstance(params["node_id"], str):
                raise ValueError("A junction ID is required.")
            _shape(params["shape"])
        elif kind in {"set_edge_shape", "set_lane_count"}:
            if not isinstance(params["edge_id"], str) or not params["edge_id"]:
                raise ValueError("A road ID is required.")
            if kind == "set_edge_shape":
                _line_shape(params["shape"])
            elif type(params["num_lanes"]) is not int or params["num_lanes"] < 1:
                raise ValueError("Lane count must be a positive integer.")
            if kind == "set_lane_count":
                if "remove_index" in params and (type(params["remove_index"]) is not int or params["remove_index"] < 0 or "add_side" in params):
                    raise ValueError("Use one nonnegative remove_index or an add_side.")
                if "add_side" in params and params["add_side"] not in {"left", "right"}:
                    raise ValueError("add_side must be left or right.")
                if "connections" in params:
                    if not isinstance(params["connections"], list):
                        raise ValueError("Lane-count connections must be a complete list of incident lane pairs.")
                    pairs = set()
                    for row in params["connections"]:
                        pair = _pair(row)
                        if set(row) - {"from", "fromLane", "to", "toLane", "allow", "disallow"} or pair in pairs:
                            raise ValueError("Lane-count connections must contain distinct supported lane pairs.")
                        pairs.add(pair)
        elif kind == "demote_duplicate_tls":
            if params["node_id"] == params["retained_node_id"]:
                raise ValueError("Duplicate and retained signal nodes must differ.")
        elif kind == "relocate_tls_control":
            if params["from_node"] == params["to_node"] or not params["tls_id"]:
                raise ValueError("Signal relocation requires distinct nodes and a controller ID.")
            if not isinstance(params["link_mapping"], list) or not params["link_mapping"]:
                raise ValueError("Signal relocation requires an explicit complete link mapping.")
            for row in params["link_mapping"]:
                if (set(row) != {"source", "target"}
                        or any(set(row[key]) != {"from", "fromLane", "to", "toLane"} for key in row)):
                    raise ValueError("Each signal link mapping requires only explicit source and target lane pairs.")
                _pair(row["source"])
                _pair(row["target"])
        elif kind in {"set_lane_permissions", "set_lane_width", "move_stop_line", "set_speed"}:
            if not isinstance(params["lane_id"], str):
                raise ValueError("A lane ID is required.")
            if kind != "set_lane_permissions":
                field = {"set_lane_width": "width_m", "move_stop_line": "end_offset_m", "set_speed": "speed_m_s"}[kind]
                value = params[field]
                if type(value) not in (int, float) or not math.isfinite(value) or value < 0 or (kind != "move_stop_line" and value == 0):
                    raise ValueError("Lane dimensions and speed must be finite and within their physical range.")
            elif (not (set(params) & {"allow", "disallow"})
                  or any(not isinstance(params[key], str) for key in ("allow", "disallow") if key in params)):
                raise ValueError("Lane permissions require explicit class strings.")
        if root is None:
            return errors
        edges, nodes, connections = _inventory(root)

        def existing_pair(record):
            pair = _pair(record)
            for edge, index in ((pair[0], pair[1]), (pair[2], pair[3])):
                if edge not in edges or index >= len(edges[edge].findall("lane")):
                    raise ValueError("Connection addresses an unknown external lane.")
            if edges[pair[0]].get("to") != edges[pair[2]].get("from"):
                raise ValueError("Connection roads must meet at one junction.")
            return pair

        if kind in _CONNECTION_EDITS:
            pair = existing_pair(params)
            if (pair in connections) == (kind == "add_lane_connection"):
                raise ValueError("Connection addition requires absence, and deletion requires presence.")
            if kind == "suppress_turnaround" and connections[pair].get("dir") not in {"t", "T"}:
                raise ValueError("The source does not identify this connection as a turnaround.")
        elif kind in _JOIN_EDITS:
            members = set(params["node_ids"])
            controller_ids = {row.get("id") for row in root.findall("tlLogic")} | {row.get("tl") for row in root.findall("connection") if row.get("tl")}
            if (not members <= nodes.keys() or params["join_id"] in nodes
                    or params.get("signal_policy") == "rebuild_diagnostic" and params["join_id"] in controller_ids):
                raise ValueError("Join members must exist. Use a new target node ID and, for a signal rebuild, a new controller ID.")
            if any(nodes[n].get("type", "").startswith("rail_") for n in members):
                raise ValueError("Rail-control joins need a separate rail-specific operation.")
            from .corridor_edit_ledger import _is_structurally_protected_edge
            internal = [e for e in edges.values() if e.get("from") in members and e.get("to") in members]
            if any(_is_structurally_protected_edge(edge) or any(
                    p.get("key") in {"bridge", "tunnel", "railway"} and p.get("value", "").lower() not in {"", "no", "false", "0"}
                    for p in edge.findall("param")) for edge in internal):
                raise ValueError("Protected roads, including parking access, require a separate protected operation.")
            if kind == "absorb_reciprocal_fragment":
                if ({(e.get("from"), e.get("to")) for e in internal}
                        != {(a, b) for a in members for b in members if a != b}
                        or any(not e.findall("lane") or any(not math.isfinite(length := float(lane_item.get("length", "inf")))
                                                           or length <= 0 or length > params["maximum_length_m"]
                                                           for lane_item in e.findall("lane")) for e in internal)):
                    raise ValueError("The source does not contain the declared bounded reciprocal fragments.")
            from .junction_boundary_rebuild import collect_join_boundary_paths
            collect_join_boundary_paths(root, groups={params["join_id"]: params["node_ids"]},
                                       signal_policy=params.get("signal_policy", "preserve"))
        elif kind == "set_junction_shape":
            if params["node_id"] not in nodes:
                raise ValueError("Junction is unknown.")
        elif kind in {"set_edge_shape", "set_lane_count"}:
            if params["edge_id"] not in edges:
                raise ValueError("Road is unknown.")
            if kind == "set_lane_count":
                _lane_count_layout(params, edges[params["edge_id"]])
                for row in params.get("connections", []):
                    pair = _pair(row)
                    if params["edge_id"] not in (pair[0], pair[2]):
                        raise ValueError("Declared lane-count connections must touch the target road.")
                    for edge_id, index in (pair[:2], pair[2:]):
                        if edge_id not in edges or index >= (params["num_lanes"] if edge_id == params["edge_id"] else len(edges[edge_id].findall("lane"))):
                            raise ValueError("Lane-count connection addresses an unknown resulting lane.")
                    if edges[pair[0]].get("to") != edges[pair[2]].get("from"):
                        raise ValueError("Lane-count connection roads must meet at one junction.")
        elif kind == "demote_duplicate_tls":
            left, right = params["node_id"], params["retained_node_id"]
            left_controllers, right_controllers = _controllers(root, edges, left), _controllers(root, edges, right)
            if (left not in nodes or right not in nodes or not nodes[left].get("type", "").startswith("traffic_light")
                    or not nodes[right].get("type", "").startswith("traffic_light")
                    or not left_controllers or not left_controllers <= right_controllers):
                raise ValueError("Duplicate and retained nodes must share an existing source controller.")
        elif kind == "relocate_tls_control":
            left, right, tls = params["from_node"], params["to_node"], params["tls_id"]
            if left not in nodes or right not in nodes or _controllers(root, edges, left) != {tls} or _controllers(root, edges, right):
                raise ValueError("Signal relocation requires one source controller and an uncontrolled target node.")
            programs = [logic for logic in root.findall("tlLogic") if logic.get("id") == tls]
            if not programs or any(logic.get("type", "static") != "static" for logic in programs):
                raise ValueError("Actuated signal relocation requires a separate detector-correspondence operation.")
            controlled = {_key(c): c for c in root.findall("connection") if c.get("tl") == tls}
            sources, targets = [], []
            for row in params["link_mapping"]:
                a, b = existing_pair(row["source"]), existing_pair(row["target"])
                if (a not in controlled or b not in connections or controlled[a].get("linkIndex2") is not None
                        or edges[a[0]].get("to") != left or edges[b[0]].get("to") != right):
                    raise ValueError("Every mapped link must belong to its declared signal node.")
                sources.append(a)
                targets.append(b)
            if set(sources) != controlled.keys() or len(set(sources)) != len(sources) or len(set(targets)) != len(targets):
                raise ValueError("Signal relocation must map every source link exactly once to distinct target links.")
        else:
            matches = [(e, lane_item) for e in edges.values() for lane_item in e.findall("lane") if lane_item.get("id") == params["lane_id"]]
            if len(matches) != 1:
                raise ValueError("Lane must identify one existing external lane.")
            if kind == "move_stop_line":
                lane = matches[0][1]
                if params["end_offset_m"] >= float(lane.get("length")) + float(lane.get("endOffset", "0")):
                    raise ValueError("Stop-line offset must leave a positive driving length.")
    except (KeyError, TypeError, ValueError, OverflowError) as error:
        errors.append({"operation_id": operation.get("id"), "code": "invalid_scoped_edit", "detail": str(error)})
    return errors


def scoped_operation_scope(root, operation):
    edges, _, _ = _inventory(root)
    kind, params = operation["operation"], operation["params"]
    if kind in _CONNECTION_EDITS:
        nodes = {edges[params["from"]].get("to")}
    elif kind in _JOIN_EDITS:
        nodes = set(params["node_ids"]) | {params["join_id"]}
    elif kind == "set_junction_shape":
        nodes = {params["node_id"]}
    elif kind == "demote_duplicate_tls":
        nodes = {params["node_id"], params["retained_node_id"]}
    elif kind == "relocate_tls_control":
        nodes = {params["from_node"], params["to_node"]}
    else:
        edge = edges[params["edge_id"]] if kind in {"set_edge_shape", "set_lane_count"} else next(
            edge for edge in edges.values() if any(lane.get("id") == params["lane_id"] for lane in edge.findall("lane")))
        nodes = {edge.get("from"), edge.get("to")}
    return nodes, {key for key, edge in edges.items() if edge.get("from") in nodes or edge.get("to") in nodes}


def write_scoped_edit_plan(*, root: ET.Element, operation: dict, source_file: Path,
                           candidate_file: Path, output_dir: Path, prefix: str,
                           netconvert_binary: str, mode: str = "release") -> dict:
    """Write one typed PlainXML operation and its exact mutable scope."""
    errors = validate_scoped_operation(operation, root)
    if errors:
        raise ValueError(str(errors))
    if source_file.resolve().is_relative_to(output_dir.resolve()):
        raise ValueError("The output directory must not contain the source file.")
    if candidate_file.resolve() == source_file.resolve() or candidate_file.exists() and candidate_file.samefile(source_file):
        raise ValueError("The candidate must not replace its source.")
    evidence_sources = []
    for record in operation.get("evidence", []):
        if not isinstance(record, dict) or not record.get("path") or not record.get("sha256"):
            raise ValueError("Scoped edits require file evidence with a path and SHA-256.")
        if not Path(record["path"]).is_absolute():
            raise ValueError("Scoped edit evidence requires an absolute path.")
        path = Path(record["path"]).resolve(strict=True)
        if candidate_file.resolve() == path or candidate_file.exists() and candidate_file.samefile(path):
            raise ValueError("The candidate must not replace edit evidence.")
        if path.is_relative_to(output_dir.resolve()):
            raise ValueError("The output directory must not contain edit evidence.")
        if file_sha256(path) != record["sha256"]:
            if mode == "release":
                raise ValueError("Scoped edit evidence hash does not match.")
        evidence_sources.append({"path": str(path), "sha256": file_sha256(path), "declared_sha256": record["sha256"]})
    kind, params = operation["operation"], operation["params"]
    edges, nodes, connections = _inventory(root)
    patches = {"nodes": ET.Element("nodes"), "edges": ET.Element("edges"),
               "connections": ET.Element("connections"), "tllogics": ET.Element("tlLogics")}
    mutable, mutable_edges = scoped_operation_scope(root, operation)
    if kind in _CONNECTION_EDITS:
        attrs = {k: str(params[k]) for k in ("from", "fromLane", "to", "toLane", "allow", "disallow") if k in params}
        ET.SubElement(patches["connections"], "connection" if kind == "add_lane_connection" else "delete", attrs)
    elif kind in _JOIN_EDITS:
        ET.SubElement(patches["nodes"], "join", id=params["join_id"], nodes=" ".join(params["node_ids"]),
            reset="false", type="traffic_light" if params.get("signal_policy") == "rebuild_diagnostic" else "priority")
    elif kind == "set_junction_shape":
        ET.SubElement(patches["nodes"], "node", id=params["node_id"], shape=_shape(params["shape"]))
    elif kind in {"set_edge_shape", "set_lane_count"}:
        edge = edges[params["edge_id"]]
        attrs = {"shape": _line_shape(params["shape"])} if kind == "set_edge_shape" else {"numLanes": str(params["num_lanes"])}
        patch = ET.SubElement(patches["edges"], "edge", id=params["edge_id"], **attrs)
        # Existing custom lane shapes otherwise override the new road reference or lane layout.
        if kind == "set_edge_shape":
            for index, _lane in enumerate(edge.findall("lane")):
                ET.SubElement(patch, "lane", index=str(index), shape="")
        else:
            mapping = _lane_count_layout(params, edge)
            new_to_old = {new: old for old, new in mapping.items()}
            for new in range(params["num_lanes"]):
                old = new_to_old.get(new, 0 if params.get("add_side") == "right" else len(edge.findall("lane")) - 1)
                original = edge.findall("lane")[old]
                permissions = _patch_permissions(original.attrib, root.get("version", "0.0"))
                lane_patch = ET.SubElement(patch, "lane", index=str(new), shape="", **permissions,
                    width=original.get("width", "3.2"), **{key: original.get(key)
                    for key in ("speed", "endOffset") if original.get(key) is not None})
                for parameter in original.findall("param"):
                    lane_patch.append(deepcopy(parameter))
            for left, right in sorted({(pair[0], pair[2]) for pair in connections
                    if params["edge_id"] in (pair[0], pair[2]) and pair[0] in edges and pair[2] in edges}):
                ET.SubElement(patches["connections"], "delete", **{"from": left, "to": right})
            for record in _lane_count_connections(params, edges, connections, network_version=root.get("version", "0.0")):
                ET.SubElement(patches["connections"], "connection", **record)
    elif kind == "demote_duplicate_tls":
        ET.SubElement(patches["nodes"], "node", id=params["node_id"], type="priority")
    elif kind == "relocate_tls_control":
        ET.SubElement(patches["nodes"], "node", id=params["from_node"], type="priority")
        ET.SubElement(patches["nodes"], "node", id=params["to_node"], type="traffic_light", tl=params["tls_id"])
        for logic in root.findall("tlLogic"):
            if logic.get("id") == params["tls_id"]:
                patches["tllogics"].append(deepcopy(logic))
        for row in params["link_mapping"]:
            old = connections[_pair(row["source"])]
            attrs = {k: str(row["target"][k]) for k in ("from", "fromLane", "to", "toLane")}
            ET.SubElement(patches["tllogics"], "connection", **attrs, tl=params["tls_id"], linkIndex=old.get("linkIndex"))
    else:
        edge, lane = next((e, lane_item) for e in edges.values() for lane_item in e.findall("lane") if lane_item.get("id") == params["lane_id"])
        attrs = ({k: params[k] for k in ("allow", "disallow") if k in params} if kind == "set_lane_permissions"
                 else {"width": str(params["width_m"])} if kind == "set_lane_width"
                 else {"endOffset": str(params["end_offset_m"])} if kind == "move_stop_line"
                 else {"speed": str(params["speed_m_s"])})
        ET.SubElement(ET.SubElement(patches["edges"], "edge", id=edge.get("id")), "lane", index=lane.get("index"), **attrs)
    plan = {"operation": operation, "mutable_junction_ids": sorted(mutable), "mutable_edge_ids": sorted(mutable_edges),
            "evidence_sources": evidence_sources,
            "source": {"path": str(source_file), "sha256": file_sha256(source_file)}, "patches": {}}
    if kind == "set_lane_count":
        plan["lane_index_mapping"] = {str(old): new for old, new in _lane_count_layout(params, edges[params["edge_id"]]).items()}
        plan["requested_connections"] = _lane_count_connections(params, edges, connections, network_version=root.get("version", "0.0"))
    plan["junction_link_detail"] = int(root.get("junctionLinkDetail", "5"))
    command = [netconvert_binary, "--sumo-net-file", str(source_file.resolve()), "--offset.disable-normalization", "true",
               "--junctions.internal-link-detail", str(plan["junction_link_detail"]), "--precision", "12", "--output-file", str(candidate_file.resolve())]
    plan["materialization_method"] = ("permission_attributes_only" if kind == "set_lane_permissions"
                                      else "speed_attributes_only" if kind == "set_speed"
                                      else "stop_line_edge_mean_3d" if kind == "move_stop_line"
                                      and _stop_line_patches(root, params) is not None else "netconvert")
    if plan["materialization_method"] == "netconvert":
        if kind in {"demote_duplicate_tls", "relocate_tls_control"}:
            plan["local_conversion"] = {"status": "full_network", "reason": "controller_reconstruction"}
        else:
            from .local_network_conversion import local_conversion_input
            groups = {params["join_id"]: params["node_ids"]} if kind in _JOIN_EDITS else None
            subset, local = local_conversion_input(root, mutable, mutable_edges, join_groups=groups)
            plan["local_conversion"] = local
            if subset is not None:
                local_file = output_dir / f"{prefix}.local-input.net.xml"
                if local_file.resolve() == candidate_file.resolve():
                    raise ValueError("The local conversion input must differ from its output.")
                with local_file.open("xb") as stream:
                    stream.write(ET.tostring(subset, encoding="utf-8", xml_declaration=True))
                local.update(input={"path": str(local_file.resolve()), "sha256": file_sha256(local_file)},
                             operation=operation)
                command[command.index("--sumo-net-file") + 1] = str(local_file.resolve())
    for name, flag in (("nodes", "--node-files"), ("edges", "--edge-files"),
                       ("connections", "--connection-files"), ("tllogics", "--tllogic-files")):
        if not len(patches[name]):
            continue
        file = output_dir / f"{prefix}.{name}.xml"
        if file.exists():
            raise ValueError("Use a new scoped edit output directory.")
        ET.indent(patches[name])
        ET.ElementTree(patches[name]).write(file, encoding="utf-8", xml_declaration=True)
        plan["patches"][name] = {"path": str(file.resolve()), "sha256": file_sha256(file)}
        command.extend((flag, str(file.resolve())))
    plan["command"] = [] if plan["materialization_method"] != "netconvert" else command
    plan_file = output_dir / f"{prefix}.scoped-plan.json"
    write_json_atomic(plan_file, plan, sort_keys=True)
    return {**plan, "plan_file": plan_file}


def check_scoped_edit(*, plan: dict, candidate_file: Path, output_dir: Path,
                      netconvert_binary: str, timeout_seconds: float, command_runner, mode: str = "release") -> dict:
    """Restore the declared exterior and verify the requested local effect."""
    source = Path(plan["source"]["path"])
    proposal = output_dir / "native-scoped-proposal.net.xml"
    path_repair = {"attempts": [], "remaining_endpoint_gap_count": 0}
    if plan["materialization_method"] == "netconvert" and plan["operation"]["operation"] in {
            "set_lane_width", "set_edge_shape", "set_lane_count", "move_stop_line", "set_junction_shape"}:
        from .junction_rebuild.native_path_repair import rebuild_custom_path_endpoints
        path_repair = rebuild_custom_path_endpoints(plan=plan, candidate_file=candidate_file,
            output_dir=output_dir, command_runner=command_runner, timeout_seconds=timeout_seconds)
    local_conversion = deepcopy(plan.get("local_conversion", {}))
    if local_conversion.get("status") == "local":
        from .local_network_conversion import merge_local_conversion
        raw = output_dir / "native-local-output.net.xml"
        if raw.exists():
            raise ValueError("Use a new local conversion output directory.")
        copy_file_atomic(candidate_file, raw)
        merged = merge_local_conversion(ET.parse(source).getroot(), ET.parse(raw).getroot(), local_conversion)
        candidate_file.write_bytes(ET.tostring(merged, encoding="utf-8", xml_declaration=True))
        local_conversion.update(native_output={"path": str(raw.resolve()), "sha256": file_sha256(raw)},
            merged_proposal={"path": str(proposal.resolve()), "sha256": file_sha256(candidate_file), "stage": "before_exterior_restoration"},
            native_rebuild_count=1 + len(path_repair["attempts"]))
    copy_file_atomic(candidate_file, proposal)
    operation, mutable_nodes, mutable_edges = plan["operation"], set(plan["mutable_junction_ids"]), set(plan["mutable_edge_ids"])
    kind, params = operation["operation"], operation["params"]
    before_restore = compare_frozen_geometry(source_file=source, candidate_file=proposal,
        mutable_junction_ids=mutable_nodes, mutable_edge_ids=mutable_edges)
    if plan["materialization_method"] != "netconvert":
        rebuild = {"status": "pass", "method": plan["materialization_method"], "netconvert_rebuild_count": 0}
        if kind in {"set_lane_permissions", "set_speed"}:
            mutable_nodes, mutable_edges = set(), set()
    elif kind in _JOIN_EDITS:
        from .junction_boundary_rebuild import restore_joined_boundary_connections
        rebuild = restore_joined_boundary_connections(source_net=source, joined_net=proposal,
            groups={params["join_id"]: params["node_ids"]}, output_dir=output_dir / "scoped-join",
            expected_source_sha256=plan["source"]["sha256"], expected_joined_sha256=file_sha256(proposal),
            signal_policy=params.get("signal_policy", "preserve"),
            interior_lane_change_policy=params.get("interior_lane_change_policy", "fixed_paths"),
            netconvert_binary=netconvert_binary, sumo_binary=_sumo_binary(netconvert_binary), timeout_seconds=timeout_seconds)
        copy_file_atomic(Path(rebuild["candidate_network"]["path"]), candidate_file)
    else:
        rebuild = restore_off_scope_netconvert_artifacts(source_file=source, target_file=candidate_file,
            mutable_junction_ids=mutable_nodes, mutable_edge_ids=mutable_edges, expand_mutable_edge_endpoints=False)
    geometry = compare_frozen_geometry(source_file=source, candidate_file=candidate_file,
        mutable_junction_ids=mutable_nodes, mutable_edge_ids=mutable_edges)
    before, after = ET.parse(source).getroot(), ET.parse(candidate_file).getroot()
    restored_speeds = []
    if kind not in {"set_lane_permissions", "set_speed"}:
        source_speeds = {lane.get("id"): lane.get("speed") for edge in before.findall("edge")
                         if not edge.get("function") for lane in edge.findall("lane")}
        for edge in after.findall("edge"):
            if edge.get("function"):
                continue
            if kind == "set_lane_count" and edge.get("id") == params["edge_id"]:
                continue
            for lane in edge.findall("lane"):
                if lane.get("id") in source_speeds and lane.get("speed") != source_speeds[lane.get("id")]:
                    restored_speeds.append(lane.get("id"))
                    original = source_speeds[lane.get("id")]
                    if original is None:
                        lane.attrib.pop("speed", None)
                    else:
                        lane.set("speed", original)
        if kind == "set_junction_shape":
            # Native input parsing can quantize coordinates before its high-precision writer runs.
            after.find(f"junction[@id='{params['node_id']}']").set("shape", _shape(params["shape"]))
        if restored_speeds or kind == "set_junction_shape":
            ET.ElementTree(after).write(candidate_file, encoding="utf-8", xml_declaration=True)
    old_edges, _, old_connections = _inventory(before)
    _, nodes, connections = _inventory(after)
    if kind in _CONNECTION_EDITS:
        pair = _pair(params)
        expected = set(old_connections) | {pair} if kind == "add_lane_connection" else set(old_connections) - {pair}
        ordinary = {key for key in connections if key[0] in old_edges and key[2] in old_edges}
        effect = ordinary == {key for key in expected if key[0] in old_edges and key[2] in old_edges}
        from .osm_access import _permission_set
        effect &= all(_permission_set(old_connections[key].attrib, network_version=before.get("version", "0.0")) ==
                      _permission_set(connections[key].attrib, network_version=after.get("version", "0.0"))
                      for key in ordinary & old_connections.keys() if key != pair)
        if kind == "add_lane_connection" and set(params) & {"allow", "disallow"}:
            effect &= _permission_set(connections[pair].attrib) == _permission_set(params)
    elif kind in _JOIN_EDITS:
        effect = rebuild["status"] == "pass"
    elif kind == "set_junction_shape":
        # SUMO may normalize the polygon winding and repeated terminal point.
        actual = [tuple(map(float, p.split(","))) for p in nodes[params["node_id"]].get("shape", "").split()]
        requested = [tuple(map(float, point.split(","))) for point in _shape(params["shape"]).split()]
        effect = len(actual) >= 3 and Polygon(actual).equals(Polygon(requested))
    elif kind == "set_edge_shape":
        edge = after.find(f"edge[@id='{params['edge_id']}']")
        actual = [tuple(map(float, point.split(","))) for point in edge.get("shape", "").split()]
        effect = len(actual) >= 2 and LineString(actual).hausdorff_distance(LineString(params["shape"])) < 0.02
    elif kind == "set_lane_count":
        effect = len(after.find(f"edge[@id='{params['edge_id']}']").findall("lane")) == params["num_lanes"]
    elif kind == "demote_duplicate_tls":
        retained = params["retained_node_id"]
        retained_pairs = [key for key, connection in old_connections.items() if connection.get("tl")
                          and key[0] in old_edges and old_edges[key[0]].get("to") == retained]
        effect = (nodes[params["node_id"]].get("type") == "priority"
                  and nodes[retained].get("type", "").startswith("traffic_light")
                  and all(key in connections and _link_program(before, old_connections[key]) == _link_program(after, connections[key])
                          for key in retained_pairs))
    elif kind == "relocate_tls_control":
        expected = {_pair(row["target"]): old_connections[_pair(row["source"])].get("linkIndex") for row in params["link_mapping"]}
        actual = {_key(c): c.get("linkIndex") for c in after.findall("connection") if c.get("tl") == params["tls_id"]}
        old_programs = [_program(logic) for logic in before.findall("tlLogic") if logic.get("id") == params["tls_id"]]
        new_programs = [_program(logic) for logic in after.findall("tlLogic") if logic.get("id") == params["tls_id"]]
        effect = actual == expected and old_programs == new_programs and nodes[params["from_node"]].get("type") == "priority"
    else:
        lane = next(lane_item for e in after.findall("edge") for lane_item in e.findall("lane") if lane_item.get("id") == params["lane_id"])
        from .osm_access import _permission_set
        effect = (round(float(lane.get("width", "3.2")), 12) == round(params["width_m"], 12) if kind == "set_lane_width"
                  else float(lane.get("speed")) == params["speed_m_s"] if kind == "set_speed"
                  else round(float(lane.get("endOffset", "0")), 12) == round(params["end_offset_m"], 12) if kind == "move_stop_line"
                  else _permission_set(lane.attrib, network_version=after.get("version", "0.0")) ==
                       _permission_set(params, network_version=after.get("version", "0.0")))
    structure = audit_network_connection_mode(after, endpoint_tolerance_m=0.1)
    changed_lane_layout = False
    unchanged_permissions = True
    old_lanes = {lane.get("id"): lane for edge in before.findall("edge") if not edge.get("function") for lane in edge.findall("lane")}
    new_lanes = {lane.get("id"): lane for edge in after.findall("edge") if not edge.get("function") for lane in edge.findall("lane")}
    from .osm_access import _permission_set
    old_pairs = {key for key in old_connections if key[0] in old_edges and key[2] in old_edges}
    new_pairs = {key for key in connections if key[0] in old_edges and key[2] in old_edges}
    expected_pairs = set(old_pairs)
    if kind in _CONNECTION_EDITS:
        expected_pairs = old_pairs | {_pair(params)} if kind == "add_lane_connection" else old_pairs - {_pair(params)}
    connections_preserved = kind in _JOIN_EDITS or new_pairs == expected_pairs
    if kind == "set_lane_count":
        target = params["edge_id"]
        target_records = {_key(ET.Element("connection", record)): record for record in plan["requested_connections"]}
        expected_pairs = {pair for pair in old_pairs if target not in (pair[0], pair[2])} | target_records.keys()
        connections_preserved = new_pairs == expected_pairs
        effect &= connections_preserved and all(pair in connections and _permission_set(connections[pair].attrib) == _permission_set(record)
                                               for pair, record in target_records.items())
    connection_permissions_preserved = kind in _JOIN_EDITS or all(
        _permission_set(old_connections[key].attrib, network_version=before.get("version", "0.0")) ==
        _permission_set(connections[key].attrib, network_version=after.get("version", "0.0"))
        for key in old_pairs & new_pairs if not (kind == "set_lane_count" and params["edge_id"] in (key[0], key[2])))
    if kind not in _JOIN_EDITS:
        changed_lane_layout = old_lanes.keys() != new_lanes.keys()
        if kind == "set_lane_count":
            target = params["edge_id"]
            changed_lane_layout = {key for key in old_lanes if key.rsplit("_", 1)[0] != target} != {
                key for key in new_lanes if key.rsplit("_", 1)[0] != target}
        unchanged_permissions = all(_permission_set(lane.attrib, network_version=before.get("version", "0.0")) ==
                                    _permission_set(new_lanes[identifier].attrib, network_version=after.get("version", "0.0"))
            for identifier, lane in old_lanes.items() if identifier in new_lanes
            and not (kind == "set_lane_permissions" and identifier == params["lane_id"])
            and not (kind == "set_lane_count" and identifier.rsplit("_", 1)[0] == params["edge_id"]))
        if kind == "set_lane_count":
            original = old_edges[params["edge_id"]].findall("lane")
            mapping = {new: old for old, new in _lane_count_layout(params, old_edges[params["edge_id"]]).items()}
            for new, lane in enumerate(after.find(f"edge[@id='{params['edge_id']}']").findall("lane")):
                old = mapping.get(new, 0 if params.get("add_side") == "right" else len(original) - 1)
                unchanged_permissions &= (_permission_set(original[old].attrib, network_version=before.get("version", "0.0")) ==
                                          _permission_set(lane.attrib, network_version=after.get("version", "0.0")))
    else:
        unchanged_permissions = all(_permission_set(old_lanes[identity].attrib, network_version=before.get("version", "0.0")) ==
                                    _permission_set(new_lanes[identity].attrib, network_version=after.get("version", "0.0"))
                                    for identity in old_lanes.keys() & new_lanes.keys())
    green_conflicts = [row for row in structure.get("tls_link_binding_audit", {}).get("protected_green_foe_conflicts", [])
                       if row.get("junction_id") in mutable_nodes]
    evidence_unchanged = all(file_sha256(Path(row["path"])) == row["sha256"] for row in plan["evidence_sources"])
    load = run_sumo_load_audit(net_file=candidate_file, output_dir=output_dir / "scoped-load",
        sumo_binary=_sumo_binary(netconvert_binary), timeout_seconds=timeout_seconds, command_runner=command_runner)
    from .network_link_semantics import compare_loaded_link_semantics
    native_reference = source
    if kind in _JOIN_EDITS:
        native_reference = Path(rebuild.get("local_conversion", {}).get("native_output", rebuild["compiled_network"])["path"])
    elif local_conversion.get("status") == "local":
        native_reference = Path(local_conversion["native_output"]["path"])
    elif plan["materialization_method"] == "netconvert":
        native_reference = proposal
    link_preservation = compare_loaded_link_semantics(source_net_file=source, native_net_file=native_reference,
        candidate_net_file=candidate_file, mutable_junction_ids=mutable_nodes, output_dir=output_dir / "loaded-links",
        sumo_binary=_sumo_binary(netconvert_binary), timeout_seconds=timeout_seconds, command_runner=command_runner)
    passed = (rebuild["status"] == geometry["status"] == load["status"] == "pass" and effect
              and structure["structural_failure_count"] == 0 and not changed_lane_layout and unchanged_permissions
              and connections_preserved and connection_permissions_preserved
              and link_preservation["status"] == "pass"
              and not green_conflicts and evidence_unchanged and file_sha256(source) == plan["source"]["sha256"])
    direct = {}
    added_modes = []
    if kind == "set_lane_permissions":
        from .osm_access import _patch_lane_permissions
        expected = {k: params[k] for k in ("allow", "disallow") if k in params}
        exact = _patch_lane_permissions(source.read_bytes(), before, {params["lane_id"]: expected}) == candidate_file.read_bytes()
        passed &= exact
        direct = {"lane_id": params["lane_id"], "expected_attributes": expected,
                  "all_other_bytes_preserved": exact, "netconvert_rebuild_count": 0}
        added_modes = sorted(_permission_set(expected, network_version=after.get("version", "0.0")) -
                             _permission_set(old_lanes[params["lane_id"]].attrib, network_version=before.get("version", "0.0")))
    if kind == "set_speed":
        from .osm_access import _patch_lane_attributes
        attrs = {"speed": str(params["speed_m_s"])}
        exact = _patch_lane_attributes(source.read_bytes(), before, {params["lane_id"]: attrs}, {"speed"}) == candidate_file.read_bytes()
        passed &= exact
        direct = {"lane_id": params["lane_id"], "expected_attributes": attrs,
                  "all_other_bytes_preserved": exact, "netconvert_rebuild_count": 0}
    if plan["materialization_method"] == "stop_line_edge_mean_3d":
        from .osm_access import _patch_lane_attributes
        patches = _stop_line_patches(before, params)
        exact = _patch_lane_attributes(source.read_bytes(), before, patches, {"shape", "length", "endOffset"}) == candidate_file.read_bytes()
        passed &= exact
        if source.read_bytes() == candidate_file.read_bytes():
            effect = False
            passed = False
        source_lengths = {lane.get("id"): float(lane.get("length")) for edge in before.findall("edge") for lane in edge.findall("lane")}
        direct = {"lane_id": params["lane_id"], "end_offset_m": params["end_offset_m"],
                  "expected_lane_attributes": patches, "all_other_bytes_preserved": exact, "netconvert_rebuild_count": 0,
                  "length_convention": "sumo_edge_mean_of_saved_logical_lengths", "distance_metric": "xyz_polyline_metres",
                  "logical_lengths": {key: {"before_m": source_lengths[key], "after_m": float(attrs["length"])}
                                      for key, attrs in patches.items()}}
    integrity = driving_integrity(after)
    def lane_coordinates(root):
        return {lane.get("id"): (float(lane.get("length", "0")),
                tuple(tuple(map(float, point.split(","))) for point in lane.get("shape", "").split()))
                for edge in root.findall("edge") for lane in edge.findall("lane")}
    old_coordinates, new_coordinates = lane_coordinates(before), lane_coordinates(after)
    coordinate_changes = {key for key in old_coordinates.keys() | new_coordinates.keys()
                          if old_coordinates.get(key) != new_coordinates.get(key)}
    if not geometry.get("coordinate_frame_preserved", False):
        coordinate_changes.update(old_coordinates.keys() | new_coordinates.keys())
    if kind == "set_lane_count" and len(old_edges[params["edge_id"]].findall("lane")) != params["num_lanes"]:
        coordinate_changes.update(lane.get("id") for root in (before, after) for edge in root.findall("edge")
                                  if edge.get("id") == params["edge_id"] for lane in edge.findall("lane"))
    dependent = {"status": "not_checked" if coordinate_changes else "not_applicable",
                 "affected_lane_ids": sorted(coordinate_changes),
                 "reason": "This network edit does not check the positions of detectors, stops, parking areas, or other lane references."}
    physical = load["status"] == "pass" and integrity["status"] == "pass" and path_repair["remaining_endpoint_gap_count"] == 0
    exploration_status = "blocked" if not physical else "measured" if effect else "no_effect"
    warnings = [] if passed else ["Some release checks failed. Inspect the recorded measurements."]
    if link_preservation["status"] != "pass":
        warnings.append(f"Loaded link comparison: {link_preservation['status']}. Inspect the saved observations and differences.")
    new_warnings = link_preservation.get("load_warning_comparison", {}).get("candidate_only", {}).get("count", 0)
    if new_warnings:
        warnings.append(f"Network loading added {new_warnings} warning lines. Inspect load_warning_comparison for the exact text.")
    return {"status": exploration_status if mode == "explore" else "pass" if passed else "blocked",
            "mode": mode, "release_checks_passed": passed,
            "driving_integrity": integrity,
            "lane_coordinate_artifacts": dependent,
            "warnings": warnings,
            "source_immutable": file_sha256(source) == plan["source"]["sha256"],
            "direct_permission_edit": direct if kind == "set_lane_permissions" else {},
            "direct_speed_edit": direct if kind == "set_speed" else {},
            "direct_stop_line_edit": direct if plan["materialization_method"] == "stop_line_edge_mean_3d" else {},
            "connectivity_review_required": bool(added_modes), "newly_allowed_vehicle_classes": added_modes,
            "requested_effect_applied": effect, "geometry_preservation": geometry, "restoration": rebuild,
            "geometry_before_restoration": before_restore,
            "lane_layout_preserved": not changed_lane_layout, "other_lane_permissions_preserved": unchanged_permissions,
            "connection_inventory_preserved": connections_preserved,
            "lane_index_mapping": plan.get("lane_index_mapping"),
            "target_connection_changes": {"added": [list(pair) for pair in sorted(new_pairs - old_pairs)],
                                          "removed": [list(pair) for pair in sorted(old_pairs - new_pairs)]},
            "restored_external_speed_ids": sorted(restored_speeds),
            "shape_coordinate_precision": 12 if kind == "set_junction_shape" else None,
            "declared_junction_shape_restored": kind == "set_junction_shape",
            "other_connection_permissions_preserved": connection_permissions_preserved,
            "new_protected_green_conflicts": green_conflicts, "evidence_unchanged": evidence_unchanged,
            "structural_failure_count": structure["structural_failure_count"], "sumo_load": load,
            "native_proposal": {"path": str(proposal), "sha256": file_sha256(proposal), "stage": "before_exterior_restoration"},
            "local_conversion": local_conversion,
            "native_path_repair": path_repair,
            "loaded_link_preservation": link_preservation,
            "claim_boundary": "Typed construction and source preservation only. Field legality, historical control and mixed-demand completion require their separate evidence."}


def explore_network_edit(*, ledger_file, output_dir, netconvert_binary="netconvert", facts_file=None):
    """Build a daily candidate and report release warnings without requiring approval evidence."""
    import json
    from .command_runner import run_command
    from .routeability_audit import run_routeability_audit

    ledger_path, destination = Path(ledger_file).resolve(strict=True), Path(output_dir).resolve()
    ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    source = Path(ledger["source_net_file"]).resolve(strict=True)
    facts_path = Path(facts_file).resolve(strict=True) if facts_file is not None else None
    if destination.exists() or source.is_relative_to(destination) or ledger_path.is_relative_to(destination):
        raise ValueError("Use a new output directory separate from the source and ledger.")
    if facts_path is not None and facts_path.is_relative_to(destination):
        raise ValueError("The output directory must not contain the fact table.")
    operations = ledger.get("operations", [])
    if len(operations) != 1 or operations[0].get("operation") not in SCOPED_EDIT_TYPES:
        raise ValueError("Explore requires one typed road edit.")
    operation = deepcopy(operations[0])
    root = ET.parse(source).getroot()
    errors = validate_scoped_operation(operation, root)
    if errors:
        raise ValueError(str(errors))
    warnings = []
    source_digest = file_sha256(source)
    if ledger.get("source_sha256") != source_digest:
        warnings.append("The declared source hash differs or is absent. The measured source hash is recorded.")
    if operation.get("status") != "accepted":
        warnings.append("The edit has no release approval.")
    supplied_evidence = operation.get("evidence", [])
    operation["evidence"] = []
    for record in supplied_evidence:
        if not isinstance(record, dict) or not record.get("path"):
            warnings.append("An evidence record has no usable path.")
            continue
        path = Path(record["path"]).resolve()
        if path.is_relative_to(destination):
            raise ValueError("The output directory must not contain edit evidence.")
        if not path.is_file():
            warnings.append(f"Evidence unavailable: {path}")
            continue
        digest = file_sha256(path)
        if record.get("sha256") != digest:
            warnings.append(f"Evidence hash mismatch: {path}")
        operation["evidence"].append({"path": str(path), "sha256": digest})
    destination.mkdir(parents=True)
    candidate = destination / "candidate.net.xml"
    plan = write_scoped_edit_plan(root=root, operation=operation, source_file=source, candidate_file=candidate,
        output_dir=destination, prefix="explore", netconvert_binary=netconvert_binary, mode="explore")
    if operation["operation"] == "move_stop_line" and plan["materialization_method"] == "netconvert":
        warnings.append("Saved geometry does not support a unique direct stop-line adjustment. Inspect native changes to local junction paths.")
    baseline_score = None
    if facts_path is not None:
        from .network_calibration import evaluate_network_calibration
        baseline_score = evaluate_network_calibration(net_file=source, facts_file=facts_path,
            output_file=destination / "source-scorecard.json", mode="explore")
    command_result = None
    command_started = time.perf_counter()
    if plan["materialization_method"] != "netconvert":
        write_direct_lane_edit(source, candidate, operation)
        command_result = {"returncode": 0, "netconvert_rebuild_count": 0}
    else:
        result = run_command(plan["command"], cwd=destination, timeout_seconds=240)
        command_result = result.to_dict()
    report = {"schema": "astra.network-edit-verification/v1", "mode": "explore", "status": "blocked",
              "promotion_authority": False, "release_gates": "not_run", "warnings": warnings,
              "ledger": {"path": str(ledger_path), "sha256": file_sha256(ledger_path)},
              "source": {"path": str(source), "sha256": source_digest}, "command_result": command_result,
              "command_elapsed_seconds": time.perf_counter() - command_started,
              "candidate_net_file": str(candidate), "plan_file": str(plan["plan_file"])}
    report["source_baseline"] = {"status": "not_run", "scope": "synthetic_passenger_driving_diagnostic"}
    if command_result.get("returncode") == 0 and candidate.is_file():
        checked = check_scoped_edit(plan=plan, candidate_file=candidate, output_dir=destination,
            netconvert_binary=netconvert_binary, timeout_seconds=240, command_runner=run_command, mode="explore")
        report["construction"] = checked
        report["native_conversion_elapsed_seconds"] = (0 if plan["materialization_method"] != "netconvert" else
            None if operation["operation"] in _JOIN_EDITS else report["command_elapsed_seconds"] + sum(
                attempt["elapsed_seconds"] for attempt in checked["native_path_repair"]["attempts"]))
        warnings.extend(checked["warnings"])
        if checked["lane_coordinate_artifacts"]["affected_lane_ids"]:
            warnings.append("Lane coordinates changed. Recheck detectors, stops, parking placements, and other lane-coordinate artifacts before reuse.")
        report["status"] = checked["status"]
        if report["status"] in {"measured", "no_effect"}:
            sumo = _sumo_binary(netconvert_binary)
            binaries = {"sumo": sumo, "duarouter": str(Path(sumo).with_name("duarouter" + Path(sumo).suffix))}
            # Generate from the source alone so edited endpoints cannot disappear from the requests.
            baseline = run_routeability_audit(net_file=source, output_dir=destination / "source-routes", binaries=binaries)
            report["source_routeability"] = baseline
            report["source_baseline"].update(status=("clean" if baseline["status"] == "pass" else "dirty")
                if baseline.get("attempts") else "not_run", diagnostic_status=baseline["status"],
                reason=baseline.get("routeability_status"), report_file=baseline.get("report_file"))
            if baseline.get("attempts") and baseline["status"] != "pass":
                warnings.append("Source driving checks failed. Candidate differences do not establish a valid improvement.")
            if (baseline.get("route_generation", {}).get("status") == "pass"
                    and Path(baseline.get("trip_file", "")).is_file()):
                trip_file = Path(baseline["trip_file"])
                routed = run_routeability_audit(net_file=candidate, output_dir=destination / "candidate-routes",
                    binaries=binaries, frozen_trip_file=trip_file, expected_frozen_trip_sha256=file_sha256(trip_file))
                report["candidate_routeability"] = routed
                if routed.get("route_generation", {}).get("status") != "pass":
                    report["status"] = "blocked"
                if routed.get("attempts") and routed["status"] != "pass":
                    warnings.append("Candidate driving checks failed. Inspect collisions, teleports, and completion separately from missing routes.")
            else:
                warnings.append("Source route requests could not be validated. Route preservation was not checked.")
        report["candidate_sha256"] = file_sha256(candidate)
        if baseline_score is not None:
            from .network_calibration import compare_calibration_scorecards, evaluate_network_calibration
            candidate_facts = facts_path
            if operation["operation"] == "set_lane_count":
                candidate_facts = destination / "candidate-facts.json"
                report["fact_selector_remap"] = _remap_lane_count_facts(facts_path, plan, candidate_facts)
            score = evaluate_network_calibration(net_file=candidate, facts_file=candidate_facts,
                output_file=destination / "candidate-scorecard.json", mode="explore")
            report["source_scorecard"] = baseline_score["output_file"]
            report["candidate_scorecard"] = score["output_file"]
            report["fact_deltas"] = compare_calibration_scorecards(baseline_score, score)["transitions"]
            lane_edges = {lane.get("id"): edge.get("id") for edge in root.findall("edge") for lane in edge.findall("lane")}
            scope_edges, scope_nodes = set(plan["mutable_edge_ids"]), set(plan["mutable_junction_ids"])
            changed_outside = []
            for before, after in zip(baseline_score["facts"], score["facts"]):
                selector = before.get("selector", {})
                selected_lanes = set(selector.get("lane_ids", []) + selector.get("incoming_lane_ids", [])
                    + selector.get("outgoing_lane_ids", []) + selector.get("lane_pair", []))
                if selector.get("lane_id"):
                    selected_lanes.add(selector["lane_id"])
                touched = (set(selector.get("edge_ids", [])) & scope_edges
                    or any(lane_edges.get(key) in scope_edges for key in selected_lanes)
                    or selector.get("junction_id") in scope_nodes or set(selector.get("junction_ids", [])) & scope_nodes)
                if not touched and any(before.get(key) != after.get(key) for key in ("status", "metrics", "observed")):
                    changed_outside.append(before["id"])
            report["outside_fact_changes"] = changed_outside
            report["outside_fact_change_count"] = len(changed_outside)
    report["source_immutable"] = file_sha256(source) == source_digest
    path = destination / "edit-verification.json"
    write_json_atomic(path, report, sort_keys=True)
    return {**report, "report_file": str(path)}


def _program(logic):
    numeric = {"duration", "offset", "minDur", "maxDur", "earliestEnd", "latestEnd"}
    return (logic.tag, tuple(sorted((key, float(value) if key in numeric else value)
                                   for key, value in logic.attrib.items())), [_program(child) for child in logic])


def _link_program(root, connection):
    if not connection.get("tl"):
        return None
    indices = [int(connection.get("linkIndex", "-1"))]
    if connection.get("linkIndex2") is not None:
        indices.append(int(connection.get("linkIndex2")))
    result = []
    for logic in root.findall("tlLogic"):
        if logic.get("id") != connection.get("tl"):
            continue
        scoped = deepcopy(logic)
        for phase in scoped.findall("phase"):
            if any(index < 0 or index >= len(phase.get("state", "")) for index in indices):
                return None
            phase.set("state", "".join(phase.get("state")[index] for index in indices))
        result.append(_program(scoped))
    return result


def _sumo_binary(netconvert_binary):
    executable = Path(shutil.which(netconvert_binary) or netconvert_binary).resolve()
    return str(executable.with_name("sumo" + executable.suffix))
