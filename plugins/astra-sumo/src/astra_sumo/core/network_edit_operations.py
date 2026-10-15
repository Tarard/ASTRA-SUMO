"""Plan and check source-bound road edits with no site-specific identifiers."""

from __future__ import annotations

from copy import deepcopy
import math
from pathlib import Path
import shutil
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
    "set_lane_count": {"edge_id", "num_lanes"},
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
    attrs = ({key: params[key] for key in ("allow", "disallow") if key in params}
             if operation["operation"] == "set_lane_permissions" else {"speed": str(params["speed_m_s"])})
    raw = source.read_bytes()
    candidate.write_bytes(_patch_lane_attributes(raw, ET.fromstring(raw), {params["lane_id"]: attrs},
                         {"allow", "disallow"} if operation["operation"] == "set_lane_permissions" else {"speed"}))


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
            if not members <= nodes.keys() or params["join_id"] in nodes and params["join_id"] not in members:
                raise ValueError("Join nodes are unknown or the target ID belongs to another node.")
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


def write_scoped_edit_plan(*, root: ET.Element, operation: dict, source_file: Path,
                           candidate_file: Path, output_dir: Path, prefix: str,
                           netconvert_binary: str, mode: str = "release") -> dict:
    """Write one typed PlainXML operation and its exact mutable scope."""
    errors = validate_scoped_operation(operation, root)
    if errors:
        raise ValueError(str(errors))
    if source_file.resolve().is_relative_to(output_dir.resolve()):
        raise ValueError("The output directory must not contain the source file.")
    evidence_sources = []
    for record in operation.get("evidence", []):
        if not isinstance(record, dict) or not record.get("path") or not record.get("sha256"):
            raise ValueError("Scoped edits require file evidence with a path and SHA-256.")
        if not Path(record["path"]).is_absolute():
            raise ValueError("Scoped edit evidence requires an absolute path.")
        path = Path(record["path"]).resolve(strict=True)
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
    mutable = set()
    if kind in _CONNECTION_EDITS:
        pair = _pair(params)
        attrs = {k: str(params[k]) for k in ("from", "fromLane", "to", "toLane", "allow", "disallow") if k in params}
        ET.SubElement(patches["connections"], "connection" if kind == "add_lane_connection" else "delete", attrs)
        mutable.add(edges[pair[0]].get("to"))
    elif kind in _JOIN_EDITS:
        mutable.update(params["node_ids"])
        mutable.add(params["join_id"])
        ET.SubElement(patches["nodes"], "join", id=params["join_id"], nodes=" ".join(params["node_ids"]),
            reset="false", type="traffic_light" if params.get("signal_policy") == "rebuild_diagnostic" else "priority")
    elif kind == "set_junction_shape":
        mutable.add(params["node_id"])
        ET.SubElement(patches["nodes"], "node", id=params["node_id"], shape=_shape(params["shape"]))
    elif kind in {"set_edge_shape", "set_lane_count"}:
        edge = edges[params["edge_id"]]
        mutable.update((edge.get("from"), edge.get("to")))
        attrs = {"shape": _line_shape(params["shape"])} if kind == "set_edge_shape" else {"numLanes": str(params["num_lanes"])}
        patch = ET.SubElement(patches["edges"], "edge", id=params["edge_id"], **attrs)
        # Existing custom lane shapes otherwise override the new road reference or lane layout.
        for index, _lane in enumerate(edge.findall("lane")):
            if kind == "set_edge_shape" or index < params["num_lanes"]:
                ET.SubElement(patch, "lane", index=str(index), shape="")
    elif kind == "demote_duplicate_tls":
        mutable.update((params["node_id"], params["retained_node_id"]))
        ET.SubElement(patches["nodes"], "node", id=params["node_id"], type="priority")
    elif kind == "relocate_tls_control":
        mutable.update((params["from_node"], params["to_node"]))
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
        mutable.update((edge.get("from"), edge.get("to")))
        attrs = ({k: params[k] for k in ("allow", "disallow") if k in params} if kind == "set_lane_permissions"
                 else {"width": str(params["width_m"])} if kind == "set_lane_width"
                 else {"endOffset": str(params["end_offset_m"])} if kind == "move_stop_line"
                 else {"speed": str(params["speed_m_s"])})
        ET.SubElement(ET.SubElement(patches["edges"], "edge", id=edge.get("id")), "lane", index=lane.get("index"), **attrs)
    mutable_edges = {e.get("id") for e in edges.values() if e.get("from") in mutable or e.get("to") in mutable}
    plan = {"operation": operation, "mutable_junction_ids": sorted(mutable), "mutable_edge_ids": sorted(mutable_edges),
            "evidence_sources": evidence_sources,
            "source": {"path": str(source_file), "sha256": file_sha256(source_file)}, "patches": {}}
    command = [netconvert_binary, "--sumo-net-file", str(source_file.resolve()), "--offset.disable-normalization", "true",
               "--junctions.internal-link-detail", "25", "--precision", "12", "--output-file", str(candidate_file.resolve())]
    plan["materialization_method"] = ("permission_attributes_only" if kind == "set_lane_permissions"
                                      else "speed_attributes_only" if kind == "set_speed" else "netconvert")
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
    plan["command"] = [] if kind in {"set_lane_permissions", "set_speed"} else command
    plan_file = output_dir / f"{prefix}.scoped-plan.json"
    write_json_atomic(plan_file, plan, sort_keys=True)
    return {**plan, "plan_file": plan_file}


def check_scoped_edit(*, plan: dict, candidate_file: Path, output_dir: Path,
                      netconvert_binary: str, timeout_seconds: float, command_runner, mode: str = "release") -> dict:
    """Restore the declared exterior and verify the requested local effect."""
    source = Path(plan["source"]["path"])
    proposal = output_dir / "native-scoped-proposal.net.xml"
    copy_file_atomic(candidate_file, proposal)
    operation, mutable_nodes, mutable_edges = plan["operation"], set(plan["mutable_junction_ids"]), set(plan["mutable_edge_ids"])
    kind, params = operation["operation"], operation["params"]
    if kind in {"set_lane_permissions", "set_speed"}:
        rebuild = {"status": "pass", "method": plan["materialization_method"], "netconvert_rebuild_count": 0}
        mutable_nodes, mutable_edges = set(), set()
    elif kind in _JOIN_EDITS:
        from .junction_boundary_rebuild import restore_joined_boundary_connections
        rebuild = restore_joined_boundary_connections(source_net=source, joined_net=candidate_file,
            groups={params["join_id"]: params["node_ids"]}, output_dir=output_dir / "scoped-join",
            expected_source_sha256=plan["source"]["sha256"], expected_joined_sha256=file_sha256(candidate_file),
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
        effect &= all(_permission_set(old_connections[key].attrib) == _permission_set(connections[key].attrib)
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
        effect = (float(lane.get("width", "3.2")) == params["width_m"] if kind == "set_lane_width"
                  else float(lane.get("speed")) == params["speed_m_s"] if kind == "set_speed"
                  else float(lane.get("endOffset", "0")) == params["end_offset_m"] if kind == "move_stop_line"
                  else _permission_set(lane.attrib) == _permission_set(params))
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
        connections_preserved = {pair for pair in old_pairs if target not in (pair[0], pair[2])} == {
            pair for pair in new_pairs if target not in (pair[0], pair[2])}
    connection_permissions_preserved = kind in _JOIN_EDITS or all(
        _permission_set(old_connections[key].attrib) == _permission_set(connections[key].attrib)
        for key in old_pairs & new_pairs)
    if kind not in _JOIN_EDITS:
        changed_lane_layout = old_lanes.keys() != new_lanes.keys()
        if kind == "set_lane_count":
            target = params["edge_id"]
            changed_lane_layout = {key for key in old_lanes if key.rsplit("_", 1)[0] != target} != {
                key for key in new_lanes if key.rsplit("_", 1)[0] != target}
        unchanged_permissions = all(_permission_set(lane.attrib) == _permission_set(new_lanes[identifier].attrib)
            for identifier, lane in old_lanes.items() if identifier in new_lanes
            and not (kind == "set_lane_permissions" and identifier == params["lane_id"]))
    green_conflicts = [row for row in structure.get("tls_link_binding_audit", {}).get("protected_green_foe_conflicts", [])
                       if row.get("junction_id") in mutable_nodes]
    evidence_unchanged = all(file_sha256(Path(row["path"])) == row["sha256"] for row in plan["evidence_sources"])
    load = run_sumo_load_audit(net_file=candidate_file, output_dir=output_dir / "scoped-load",
        sumo_binary=_sumo_binary(netconvert_binary), timeout_seconds=timeout_seconds, command_runner=command_runner)
    passed = (rebuild["status"] == geometry["status"] == load["status"] == "pass" and effect
              and structure["structural_failure_count"] == 0 and not changed_lane_layout and unchanged_permissions
              and connections_preserved and connection_permissions_preserved
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
        added_modes = sorted(_permission_set(expected) - _permission_set(old_lanes[params["lane_id"]].attrib))
    if kind == "set_speed":
        from .osm_access import _patch_lane_attributes
        attrs = {"speed": str(params["speed_m_s"])}
        exact = _patch_lane_attributes(source.read_bytes(), before, {params["lane_id"]: attrs}, {"speed"}) == candidate_file.read_bytes()
        passed &= exact
        direct = {"lane_id": params["lane_id"], "expected_attributes": attrs,
                  "all_other_bytes_preserved": exact, "netconvert_rebuild_count": 0}
    integrity = driving_integrity(after)
    physical = load["status"] == "pass" and integrity["status"] == "pass"
    return {"status": "pass" if (physical if mode == "explore" else passed) else "blocked",
            "mode": mode, "release_checks_passed": passed,
            "driving_integrity": integrity,
            "warnings": [] if passed else ["Some release checks failed. Inspect the recorded measurements."],
            "source_immutable": file_sha256(source) == plan["source"]["sha256"],
            "direct_permission_edit": direct,
            "direct_speed_edit": direct if kind == "set_speed" else {},
            "connectivity_review_required": bool(added_modes), "newly_allowed_vehicle_classes": added_modes,
            "requested_effect_applied": effect, "geometry_preservation": geometry, "restoration": rebuild,
            "lane_layout_preserved": not changed_lane_layout, "other_lane_permissions_preserved": unchanged_permissions,
            "connection_inventory_preserved": connections_preserved,
            "restored_external_speed_ids": sorted(restored_speeds),
            "shape_coordinate_precision": 12 if kind == "set_junction_shape" else None,
            "declared_junction_shape_restored": kind == "set_junction_shape",
            "other_connection_permissions_preserved": connection_permissions_preserved,
            "new_protected_green_conflicts": green_conflicts, "evidence_unchanged": evidence_unchanged,
            "structural_failure_count": structure["structural_failure_count"], "sumo_load": load,
            "native_proposal": {"path": str(proposal), "sha256": file_sha256(proposal)},
            "claim_boundary": "Typed construction and source preservation only. Field legality, historical control and mixed-demand completion require their separate evidence."}


def explore_network_edit(*, ledger_file, output_dir, netconvert_binary="netconvert"):
    """Build a daily candidate and report release warnings without requiring approval evidence."""
    import json
    from .command_runner import run_command
    from .routeability_audit import run_routeability_audit

    ledger_path, destination = Path(ledger_file).resolve(strict=True), Path(output_dir).resolve()
    ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    source = Path(ledger["source_net_file"]).resolve(strict=True)
    if destination.exists() or source.is_relative_to(destination) or ledger_path.is_relative_to(destination):
        raise ValueError("Use a new output directory separate from the source and ledger.")
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
    command_result = None
    if plan["materialization_method"] in {"permission_attributes_only", "speed_attributes_only"}:
        write_direct_lane_edit(source, candidate, operation)
        command_result = {"returncode": 0, "netconvert_rebuild_count": 0}
    else:
        result = run_command(plan["command"], cwd=destination, timeout_seconds=240)
        command_result = result.to_dict()
    report = {"schema": "astra.network-edit-verification/v1", "mode": "explore", "status": "blocked",
              "promotion_authority": False, "release_gates": "not_run", "warnings": warnings,
              "ledger": {"path": str(ledger_path), "sha256": file_sha256(ledger_path)},
              "source": {"path": str(source), "sha256": source_digest}, "command_result": command_result,
              "candidate_net_file": str(candidate), "plan_file": str(plan["plan_file"])}
    if command_result.get("returncode") == 0 and candidate.is_file():
        checked = check_scoped_edit(plan=plan, candidate_file=candidate, output_dir=destination,
            netconvert_binary=netconvert_binary, timeout_seconds=240, command_runner=run_command, mode="explore")
        report["construction"] = checked
        warnings.extend(checked["warnings"])
        report["status"] = checked["status"]
        if report["status"] == "pass":
            sumo = _sumo_binary(netconvert_binary)
            binaries = {"sumo": sumo, "duarouter": str(Path(sumo).with_name("duarouter" + Path(sumo).suffix))}
            # Generate from the source alone so edited endpoints cannot disappear from the requests.
            baseline = run_routeability_audit(net_file=source, output_dir=destination / "source-routes", binaries=binaries)
            report["source_routeability"] = baseline
            if (baseline.get("route_generation", {}).get("status") == "pass"
                    and Path(baseline.get("trip_file", "")).is_file()):
                trip_file = Path(baseline["trip_file"])
                routed = run_routeability_audit(net_file=candidate, output_dir=destination / "candidate-routes",
                    binaries=binaries, frozen_trip_file=trip_file, expected_frozen_trip_sha256=file_sha256(trip_file))
                report["candidate_routeability"] = routed
                report["status"] = "pass" if routed.get("route_generation", {}).get("status") == "pass" else "blocked"
                if routed["status"] != "pass":
                    warnings.append("The candidate driving run did not complete. Inspect retained runtime results separately from route loss.")
            else:
                warnings.append("Source route requests could not be validated. Route preservation was not checked.")
        report["candidate_sha256"] = file_sha256(candidate)
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
