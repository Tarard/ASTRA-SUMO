"""Compare explicit junction access of retained dedicated walking and cycling lanes."""

from .internal_ownership import resolve_internal_ownership
from .osm_access import _permission_set


def _snapshot(root, excluded=()):
    owners = resolve_internal_ownership(root)["lane"]
    lanes, keys, roads, facilities = {}, {}, {}, {}
    for edge in root.findall("edge"):
        for index, lane in enumerate(edge.findall("lane")):
            identity = lane.get("id")
            modes = _permission_set(lane.attrib, network_version=root.get("version", "0.0"))
            lanes[identity] = modes
            keys[edge.get("id"), str(index)] = identity
            if not edge.get("function"):
                roads[identity] = edge
                if modes and modes <= {"pedestrian", "bicycle"}:
                    facilities[identity] = dict(edge_id=edge.get("id"), index=index, modes=modes, access=set())
        if not edge.get("function"):
            candidates = [lane.get("id") for lane in edge.findall("lane") if lanes[lane.get("id")] == {"pedestrian"}]
            candidates = candidates or [lane.get("id") for lane in edge.findall("lane") if "pedestrian" in lanes[lane.get("id")]]
            for lane in edge.findall("lane"):
                if lane.get("id") in facilities:
                    facilities[lane.get("id")]["pedestrian_model_lane"] = bool(candidates and candidates[0] == lane.get("id"))
    unresolved = set()
    for connection in root.findall("connection"):
        if tuple(connection.get(key) for key in ("from", "fromLane", "to", "toLane")) in excluded:
            continue
        first = keys.get((connection.get("from"), connection.get("fromLane")))
        last = keys.get((connection.get("to"), connection.get("toLane")))
        if first not in facilities and last not in facilities:
            continue
        modes = lanes.get(first, set()) & lanes.get(last, set()) & {"pedestrian", "bicycle"}
        node = (owners.get(connection.get("via")) or (owners.get(first) if first not in roads else None)
                or (owners.get(last) if last not in roads else None))
        if node is None and first in roads and last in roads:
            if roads[first].get("to") == roads[last].get("from"):
                node = roads[first].get("to")
            else:
                common = {roads[first].get("from"), roads[first].get("to")} & {roads[last].get("from"), roads[last].get("to")}
                if len(common) == 1:
                    node = common.pop()
        for identity, direction in ((first, "outgoing"), (last, "incoming")):
            if identity not in facilities or not modes:
                continue
            if node is None:
                unresolved.add(identity)
                continue
            for mode in modes:
                facilities[identity]["access"].add((mode, node, "either" if mode == "pedestrian" else direction))
    return facilities, unresolved


def compare_nonmotor_access(before, after, plan):
    """Measure explicit source access; this does not certify implicit pedestrian routing."""
    operation = plan["operation"]
    kind, params = operation["operation"], operation["params"]
    mapping = {int(old): new for old, new in plan.get("lane_index_mapping", {}).items()}
    excluded = set()
    if kind in {"delete_lane_connection", "suppress_turnaround"}:
        excluded.add(tuple(str(params[key]) for key in ("from", "fromLane", "to", "toLane")))
    if kind == "set_lane_count":
        ordinary = {edge.get("id") for edge in before.findall("edge") if not edge.get("function")}
        requested = {tuple(str(row[key]) for key in ("from", "fromLane", "to", "toLane"))
                     for row in params.get("connections", [])}
        for row in before.findall("connection"):
            key = tuple(row.get(name) for name in ("from", "fromLane", "to", "toLane"))
            if key[0] not in ordinary or key[2] not in ordinary or params["edge_id"] not in (key[0], key[2]):
                continue
            changed = list(key)
            removed = False
            for end in (0, 2):
                if key[end] == params["edge_id"]:
                    if int(key[end+1]) not in mapping:
                        removed = True
                    else:
                        changed[end+1] = str(mapping[int(key[end+1])])
            if removed or "connections" in params and tuple(changed) not in requested:
                excluded.add(key)
    try:
        source, source_unknown = _snapshot(before, excluded)
        candidate, candidate_unknown = _snapshot(after)
    except ValueError as error:
        return {"status": "review_required", "reason": "nonmotor_access_unavailable", "error": str(error)}
    scope = set(plan["mutable_edge_ids"])
    members = set(params.get("node_ids", [])) if kind in {"join_junctions", "absorb_reciprocal_fragment"} else set()
    aliases = {node: params["join_id"] for node in members}
    old_edges = {edge.get("id"): edge for edge in before.findall("edge")}
    new_ids = {lane.get("id") for edge in after.findall("edge") for lane in edge.findall("lane")}
    changes, removed, unknown, retained = [], [], [], []
    for identity, old in source.items():
        if old["edge_id"] not in scope:
            continue
        target = identity
        if kind == "set_lane_count" and old["edge_id"] == params["edge_id"]:
            if old["index"] not in mapping:
                removed.append(identity)
                continue
            target = f"{old['edge_id']}_{mapping[old['index']]}"
        edge = old_edges[old["edge_id"]]
        if members and edge.get("from") in members and edge.get("to") in members and target not in new_ids:
            removed.append(identity)
            continue
        retained.append(target)
        current = candidate.get(target)
        modes = set(old["modes"])
        if kind == "set_lane_permissions" and identity == params["lane_id"]:
            modes &= _permission_set(params, network_version=after.get("version", "0.0"))
        if not modes:
            continue
        if current is None or not modes <= current["modes"]:
            changes.append({"source_lane": identity, "candidate_lane": target, "reason": "retained_nonmotor_lane_or_modes_missing"})
            continue
        if "pedestrian" in modes and old["pedestrian_model_lane"] and not current["pedestrian_model_lane"]:
            changes.append({"source_lane": identity, "candidate_lane": target, "reason": "retained_sidewalk_no_longer_selected_by_pedestrian_model"})
        missing = {(mode, aliases.get(node, node), direction) for mode, node, direction in old["access"] if mode in modes} - current["access"]
        if missing:
            changes.append({"source_lane": identity, "candidate_lane": target,
                            "reason": "retained_nonmotor_junction_access_lost", "missing_access": [list(key) for key in sorted(missing)]})
        if identity in source_unknown or target in candidate_unknown:
            unknown.append(target)
    ambiguous = []
    for edge_id in scope:
        old_count = sum(lane["edge_id"] == edge_id and lane["modes"] == {"pedestrian"} for lane in source.values())
        new_count = sum(lane["edge_id"] == edge_id and lane["modes"] == {"pedestrian"} for lane in candidate.values())
        if new_count > max(1, old_count):
            ambiguous.append({"edge_id": edge_id, "source_sidewalk_count": old_count, "candidate_sidewalk_count": new_count})
    return {"status": "fail" if changes or ambiguous else "review_required" if unknown else "pass",
            "checked_retained_lane_count": len(retained), "changes": changes, "new_multiple_sidewalks": ambiguous,
            "declared_removed_lane_ids": removed, "unresolved_lane_ids": sorted(set(unknown)),
            "declared_removed_connections": [list(key) for key in sorted(excluded)],
            "claim_boundary": "Explicit junction access for dedicated pedestrian/bicycle lanes only. No implicit-route or trajectory-equivalence claim."}
