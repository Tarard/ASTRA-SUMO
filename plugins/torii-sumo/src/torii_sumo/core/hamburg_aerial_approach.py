"""Rebuild only officially documented short lane additions on a SUMO approach."""

from __future__ import annotations

import hashlib
import json
import math
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from collections.abc import Mapping
from copy import deepcopy
from pathlib import Path
from typing import Any

from pyproj import Transformer

from .candidate_contracts import file_sha256
from .command_runner import run_command
from .connection_mode_audit import audit_network_connection_mode, lane_supports_motorized
from .digital_twin import parse_mapem
from .hamburg_map_kml import parse_hamburg_map_kml
from .hamburg_official_intersection_plainxml import _orient_lane_shape, _sort_right_to_left
from ..road_network.official_splice_materializer import (
    _clip_polyline_fraction,
    _project_polyline,
    _point_in_polygon,
    _small_node_shape,
    _segment_intersection,
)

SCHEMA = "torii.hamburg-aerial-approach-candidate/v1"
_LANE_ATTRIBUTES = ("speed", "width", "allow", "disallow", "changeLeft", "changeRight", "type", "acceleration")
_CONNECTION_ATTRIBUTES = ("from", "to", "fromLane", "toLane", "pass", "keepClear", "contPos", "visibility", "speed", "uncontrolled", "allow", "disallow", "changeLeft", "changeRight", "length", "type", "tl", "linkIndex", "linkIndex2")


def build_hamburg_aerial_approach_candidate(
    *, source_net: str | Path, plans: Mapping[str, Mapping[str, Any]],
    junction_bindings: Mapping[tuple[str, str], str], output_dir: str | Path,
    netconvert_binary: str = "netconvert", timeout_seconds: float = 240,
    maximum_lane_error_m: float = 10, minimum_match_margin_m: float = 0.5,
) -> dict[str, Any]:
    """Adapt a bounded-join network before rebuilding its official movements."""
    source = Path(source_net).resolve(strict=True)
    destination = Path(output_dir).resolve()
    if destination.exists():
        raise ValueError("output_dir must not already exist")
    if any(not math.isfinite(value) or value <= 0 for value in (timeout_seconds, maximum_lane_error_m, minimum_match_margin_m)):
        raise ValueError("timeout and matching limits must be finite and positive")
    root = ET.parse(source).getroot()
    location = root.find("location")
    if location is None or location.get("projParameter") in (None, "!", "-", "."):
        raise ValueError("source network requires a geographic projection")
    transformer = Transformer.from_crs("EPSG:4326", location.get("projParameter"), always_xy=True)
    offset = tuple(map(float, location.get("netOffset", "0,0").split(",")))
    source_identity = _identity(source)
    inputs, proposals, reviews, not_applicable = {}, [], [], []
    for node_id, plan in plans.items():
        artifact = plan["inputs"]["map_kml"]
        kml_file = Path(artifact["path"]).resolve(strict=True)
        kml = parse_hamburg_map_kml(kml_file, expected_sha256=str(artifact["sha256"]))
        inputs[str(node_id)] = {"map_kml": _identity(kml_file), "normalized_plan_sha256": hashlib.sha256(json.dumps(plan, sort_keys=True).encode()).hexdigest()}
        if plan.get("inputs", {}).get("map_xml"):
            map_record = plan["inputs"]["map_xml"]
            map_file = Path(map_record["path"]).resolve(strict=True)
            if file_sha256(map_file) != str(map_record.get("sha256", "")).lower():
                raise ValueError("MAP XML SHA-256 does not match")
            metadata = {lane.lane_id: lane.permission_metadata for lane in parse_mapem(map_file)[0] if lane.node_id.lstrip("0") == str(node_id).lstrip("0")}
            if any(str(lane["lane_id"]) not in metadata for lane in plan["lanes"]):
                raise ValueError("MAP XML does not cover the declared lane identities")
            plan = {**plan, "lanes": [{**lane, **metadata[str(lane["lane_id"])]} for lane in plan["lanes"]]}
            inputs[str(node_id)]["map_xml"] = _identity(map_file)
        rows = _official_ingress_rows(plan, kml, transformer, offset)
        for (part, approach), members in rows.items():
            junction_id = junction_bindings.get((str(node_id), part))
            event_groups = _separate_official_merge_events(members)
            for group in event_groups:
                identity = {"node_id": str(node_id), "intersection_part": part, "approach": approach, "role": "ingress"}
                if len(event_groups) > 1:
                    identity.update(lane_event_id="merge-" + "-".join(sorted(row["lane_id"] for row in group)),
                                    same_approach_lanes_outside_event=sorted(row["lane_id"] for row in members if row not in group))
                try:
                    proposal, reason = _approach_proposal(root, group, junction_id, maximum_lane_error_m, minimum_match_margin_m)
                except ValueError as error:
                    proposal, reason = None, str(error)
                if proposal is None:
                    reviews.append({**identity, "reason": reason})
                elif proposal.get("status") == "not_applicable":
                    not_applicable.append({**identity, **proposal})
                else:
                    proposals.extend({**identity, **section} for section in proposal.get("_chain_sections", [proposal]))
    counts = Counter(row["source_edge_id"] for row in proposals)
    conflicting = {(row["node_id"], row["intersection_part"], row["approach"], row.get("lane_event_id")) for row in proposals if counts[row["source_edge_id"]] != 1}
    accepted = []
    for row in proposals:
        if (row["node_id"], row["intersection_part"], row["approach"], row.get("lane_event_id")) in conflicting:
            reviews.append({"node_id": row["node_id"], "approach": row["approach"], "reason": "multiple_approaches_compete_for_source_edge"})
        else:
            accepted.append(row)
    destination.mkdir(parents=True)
    if not accepted:
        return _save(destination, {"schema": SCHEMA, "status": "not_applicable" if not_applicable and not reviews else "review_required", "source_network": source_identity, "candidate_network": None, "approaches": [], "reviews": reviews, "not_applicable": not_applicable, "inputs": inputs})

    node_patch, edge_patch, connection_patch = ET.Element("nodes"), ET.Element("edges"), ET.Element("connections")
    source_edges = {edge.get("id"): edge for edge in root.findall("edge") if edge.get("function") != "internal"}
    rewritten = {}
    expected_upstream = {}
    boundary_scope = _boundary_adjustment_scope(root, {row["original_from"] for row in accepted if row["mode"] == "reuse_existing_merge_junction"}, {row["source_edge_id"] for row in accepted})
    fixed_junctions = {row["original_to"] for row in accepted} | {row["original_from"] for row in accepted}
    fixed_junctions.update(node_id for item in boundary_scope.values() for node_id in item["node_ids"])
    for junction_id in sorted(fixed_junctions):
        junction = root.find(f"junction[@id='{junction_id}']")
        ET.SubElement(node_patch, "node", {key: junction.get(key) for key in ("id", "x", "y", "z", "type", "shape") if junction.get(key) is not None})
    for row in accepted:
        edge_id = row["source_edge_id"]
        original = source_edges[edge_id]
        reuse = row["mode"] != "split_inside_source_edge"
        upstream_id = None if reuse else f"{edge_id}.torii-before-{row['node_id']}-{row['intersection_part']}-{row['approach']}"
        event_suffix = "-" + row["lane_event_id"] if row.get("lane_event_id") else ""
        split_id = row["original_from"] if reuse else f"torii-merge-{row['node_id']}-{row['intersection_part']}-{row['approach']}{event_suffix}"
        if not reuse and (upstream_id in source_edges or root.find(f"junction[@id='{split_id}']") is not None):
            raise ValueError("approach split identity already exists")
        row.update({"upstream_edge_id": upstream_id, "downstream_edge_id": edge_id, "split_node_id": split_id})
        x, y = row["split_xy"]
        if not reuse:
            ET.SubElement(node_patch, "node", {"id": split_id, "x": str(x), "y": str(y), "type": "priority", "shape": _small_node_shape((x, y))})
            upstream = deepcopy(original)
            upstream.set("id", upstream_id)
            upstream.set("to", split_id)
            upstream.set("numLanes", str(len(row["through_lane_ids"])))
            upstream.set("shape", _shape(row["_upstream_edge_shape"]))
            for index, lane in enumerate(upstream.findall("lane")):
                _set_lane_shape(lane, index, row["_upstream_lane_shapes"][index])
            expected_upstream[upstream_id] = deepcopy(upstream)
            edge_patch.append(upstream)
        downstream = deepcopy(original)
        downstream.set("from", split_id)
        downstream.set("numLanes", str(len(row["_ordered_lanes"])))
        downstream.set("shape", _shape([_mean([item["shape"][0] for item in row["_ordered_lanes"]]), _mean([item["shape"][-1] for item in row["_ordered_lanes"]])]))
        for lane in list(downstream.findall("lane")):
            downstream.remove(lane)
        original_lanes = original.findall("lane")
        for index, official in enumerate(row["_ordered_lanes"]):
            donor = row["_donor_indices"][official["lane_id"]]
            lane = deepcopy(original_lanes[donor])
            _set_lane_shape(lane, index, official["shape"])
            if official.get("allowed_vehicle_classes") is not None:
                lane.attrib.pop("disallow", None)
                lane.set("allow", " ".join(official["allowed_vehicle_classes"]))
            if official.get("revocable"):
                ET.SubElement(lane, "param", {"key": "torii:revocable", "value": "enabled_for_topology_test;activation_not_replayed"})
            downstream.append(lane)
        edge_patch.append(downstream)
        rewritten[edge_id] = row

    expected_connections = set()
    expected_controls = {}
    affected_tls = []
    touched_controller_ids = set()
    for connection in root.findall("connection"):
        if connection.get("from", "").startswith(":"):
            continue
        attrs = {key: connection.get(key) for key in _CONNECTION_ATTRIBUTES if connection.get(key) is not None}
        if attrs["from"] in rewritten or attrs["to"] in rewritten:
            if attrs.get("tl"):
                touched_controller_ids.add(attrs["tl"])
            ET.SubElement(connection_patch, "delete", {key: attrs[key] for key in ("from", "to", "fromLane", "toLane")})
            if attrs["from"] in rewritten:
                attrs["fromLane"] = str(rewritten[attrs["from"]]["_through_indices"][int(attrs["fromLane"])])
            if attrs["to"] in rewritten:
                target = rewritten[attrs["to"]]
                if target["upstream_edge_id"] is not None:
                    attrs["to"] = target["upstream_edge_id"]
                else:
                    attrs["toLane"] = str(target["_through_indices"][int(attrs["toLane"])])
            ET.SubElement(connection_patch, "connection", attrs)
        expected_connections.add(_connection_key(attrs))
        if attrs.get("tl"):
            expected_controls[_connection_key(attrs)] = (attrs["tl"], attrs.get("linkIndex"))
    for row in accepted:
        if row["upstream_edge_id"] is None:
            continue
        for index, local_index in enumerate(row["_through_indices"]):
            attrs = {"from": row["upstream_edge_id"], "to": row["source_edge_id"], "fromLane": str(index), "toLane": str(local_index)}
            ET.SubElement(connection_patch, "connection", attrs)
            expected_connections.add(_connection_key(attrs))
    for row in accepted:
        previous = rewritten.get(row.get("previous_section_edge_id"))
        if previous is None:
            continue
        for pocket_id in row["pocket_lane_ids"]:
            attrs = {"from": previous["source_edge_id"], "to": row["source_edge_id"], "fromLane": str(previous["official_lane_indices"][pocket_id]), "toLane": str(row["official_lane_indices"][pocket_id])}
            carrier = next(connection for connection in root.findall("connection") if connection.get("from") == previous["source_edge_id"] and connection.get("to") == row["source_edge_id"] and connection.get("fromLane") == str(previous["_donor_indices"][pocket_id]) and connection.get("toLane") == str(row["_donor_indices"][pocket_id]))
            if carrier.get("tl"):
                attrs.update({"tl": carrier.get("tl"), "linkIndex": carrier.get("linkIndex")})
                touched_controller_ids.add(attrs["tl"])
                expected_controls[_connection_key(attrs)] = (attrs["tl"], attrs["linkIndex"])
                affected_tls.append({"node_id": row["original_from"], "tls_id": attrs["tl"],
                    "source_carrier_connection": list(_connection_key(carrier.attrib)), "source_link_index": attrs["linkIndex"],
                    "added_connection": list(_connection_key(attrs)), "official_pocket_lane_id": pocket_id,
                    "basis": "same_direction_controlled_carrier_group_on_unbranched_chain",
                    "timing_claim": "provisional_source_group_only; rebuild_generic_test_signals_before_experiments"})
            ET.SubElement(connection_patch, "connection", attrs)
            expected_connections.add(_connection_key(attrs))

    # A right-side addition shifts old index 0 to 1; delete all old pairs
    # before adding remapped pairs so a later deletion cannot erase a new pair.
    connection_patch[:] = sorted(connection_patch, key=lambda row: row.tag != "delete")
    paths = {}
    for name, tree_root in (("nodes", node_patch), ("edges", edge_patch), ("connections", connection_patch)):
        paths[name] = destination / f"approach.{name}.xml"
        ET.indent(tree_root)
        ET.ElementTree(tree_root).write(paths[name], encoding="utf-8", xml_declaration=True)
    if touched_controller_ids:
        traffic_lights = ET.Element("tlLogics")
        for logic in root.findall("tlLogic"):
            if logic.get("id") in touched_controller_ids:
                traffic_lights.append(deepcopy(logic))
        for key, (tls_id, link_index) in expected_controls.items():
            if tls_id in touched_controller_ids:
                ET.SubElement(traffic_lights, "connection", {"from": key[0], "fromLane": key[1], "to": key[2], "toLane": key[3], "tl": tls_id, "linkIndex": link_index})
        paths["traffic_lights"] = destination / "approach.tll.xml"
        ET.indent(traffic_lights)
        ET.ElementTree(traffic_lights).write(paths["traffic_lights"], encoding="utf-8", xml_declaration=True)
    candidate = destination / "approach-candidate.net.xml"
    command = [netconvert_binary, "--sumo-net-file", str(source), "--node-files", str(paths["nodes"]), "--edge-files", str(paths["edges"]), "--connection-files", str(paths["connections"]), *(["--tllogic-files", str(paths["traffic_lights"])] if touched_controller_ids else []), "--offset.disable-normalization", "true", "--junctions.internal-link-detail", "25", "--output-file", str(candidate)]
    compiled = run_command(command, cwd=destination, timeout_seconds=timeout_seconds)
    (destination / "netconvert.log").write_text(compiled.stdout + compiled.stderr, encoding="utf-8")
    if compiled.returncode != 0 or not candidate.is_file():
        raise ValueError("netconvert could not compile approach sections: " + compiled.stderr)
    result_root = ET.parse(candidate).getroot()
    result_edges = {edge.get("id"): edge for edge in result_root.findall("edge") if edge.get("function") != "internal"}
    preserved = set(result_edges) == set(source_edges) | set(expected_upstream)
    evidence = []
    boundary_adjustments = []
    for edge_id, expected in {**{key: edge for key, edge in source_edges.items() if key not in rewritten}, **expected_upstream}.items():
        actual = result_edges.get(edge_id)
        okay = actual is not None and _preserved_edge(expected, actual)
        bounded = not okay and actual is not None and edge_id in boundary_scope and _preserved_boundary_edge(expected, actual, boundary_scope[edge_id]["polygons"])
        evidence.append({"edge_id": edge_id, "preserved": okay, "within_declared_boundary_adjustment": bounded})
        if bounded:
            boundary_adjustments.append({"edge_id": edge_id, **boundary_scope[edge_id],
                "basis": "source_lane_was_inside_both_owner_polygons_and_rebuilt_lane_stays_inside_their_union",
                "before": [lane.attrib for lane in expected.findall("lane")], "after": [lane.attrib for lane in actual.findall("lane")]})
        preserved &= okay or bounded
    actual_connections = {_connection_key(row.attrib) for row in result_root.findall("connection") if not row.get("from", "").startswith(":")}
    actual_controls = {_connection_key(row.attrib): (row.get("tl"), row.get("linkIndex")) for row in result_root.findall("connection") if not row.get("from", "").startswith(":") and row.get("tl")}
    controls_preserved = all(actual_controls.get(key) == value for key, value in expected_controls.items())
    audit = audit_network_connection_mode(result_root, endpoint_tolerance_m=0.1)
    audit_file = destination / "connection-audit.json"
    audit_file.write_text(json.dumps(audit, indent=2), encoding="utf-8")
    ordinary_preserved = True
    for event in accepted:
        if event["mode"] != "official_bounded_restricted_lane":
            continue
        old_lanes = source_edges[event["source_edge_id"]].findall("lane")
        new_lanes = result_edges[event["source_edge_id"]].findall("lane")
        for old_index, new_index in enumerate(event["_through_indices"]):
            before, after = ET.Element("edge"), ET.Element("edge")
            before.append(deepcopy(old_lanes[old_index]))
            after.append(deepcopy(new_lanes[new_index]))
            ordinary_preserved &= _preserved_edge(before, after)
    gates = {"source_immutable": "pass" if _identity(source) == source_identity else "blocked",
             "external_connection_set": "pass" if actual_connections == expected_connections else "blocked",
             "outside_geometry_and_attributes": "pass" if preserved else "blocked",
             "controlled_continuations": "pass" if controls_preserved else "blocked",
             "bounded_restricted_ordinary_preserved": "pass" if ordinary_preserved else "blocked",
             "internal_path_continuity": "pass" if audit["structural_failure_count"] == 0 else "blocked"}
    return _save(destination, {"schema": SCHEMA, "status": "pass" if all(value == "pass" for value in gates.values()) else "blocked",
        "claim_status": "diagnostic-demo", "source_network": source_identity, "candidate_network": _identity(candidate),
        "inputs": inputs, "approaches": [{key: value for key, value in row.items() if not key.startswith("_")} for row in accepted],
        "reviews": reviews, "not_applicable": not_applicable, "gates": gates, "outside_preservation": evidence, "boundary_geometry_adjustments": boundary_adjustments, "affected_tls": affected_tls, "preserved_controller_ids": sorted(touched_controller_ids),
        "source_priority": {"local_lane_identity_and_geometry": "official_MAP_KML", "vehicle_permissions": "explicit_official_MAP_XML_otherwise_source_lane", "outside_geometry_and_connections": "frozen_source_SUMO_network", "aerial_imagery": "context_only_not_automatically_interpreted"},
        "connection_changes": {"missing": sorted(expected_connections - actual_connections), "extra": sorted(actual_connections - expected_connections)},
        "commands": [compiled.to_dict()], "artifacts": {**{key: _identity(path) for key, path in paths.items()}, "connection_audit": _identity(audit_file)},
        "claim_boundary": "Only documented ingress lane additions are rebuilt. The original edge ID now denotes its shorter downstream section. Routes, detectors and signal bindings require remapping before traffic experiments. Official junction movements remain a separate construction step."})


def _official_ingress_rows(plan, kml, transformer, offset):
    geometry = {str(row["lane_id"]): row for row in kml["lanes"]}
    endpoints = {(str(row["lane_id"]), row["endpoint"]): row["coordinate"] for row in kml["endpoints"]}
    def project(point):
        x, y = transformer.transform(*point[:2], errcheck=True)
        return (x + offset[0], y + offset[1])
    parts = defaultdict(set)
    for row in plan["movements"]:
        parts[str(row.get("intersection_part", "0"))].add(str(row["ingress_lane_id"]))
    result = defaultdict(list)
    for lane in plan["lanes"]:
        lane_id = str(lane["lane_id"])
        approach = str(lane.get("ingress_approach", "")).strip()
        if lane.get("lane_type", "").lower() != "vehicle" or not approach:
            continue
        item = geometry[lane_id]
        if item["role"] != "ingress":
            raise ValueError("official lane direction conflicts with approach plan")
        a, b = project(endpoints[(lane_id, "A")]), project(endpoints[(lane_id, "B")])
        shape = _orient_lane_shape(lane_id=lane_id, coordinates=[project(point) for point in item["coordinates"]], endpoint_a=a, endpoint_b=b, direction="ingress", tolerance_m=1)
        row = {"lane_id": lane_id, "direction": "ingress", "endpoint_a": a, "endpoint_b": b, "shape": shape,
               "allowed_vehicle_classes": lane.get("allowed_vehicle_classes"), "permission_status": lane.get("permission_status", "unknown"),
               "vehicle_attribute_bits": lane.get("vehicle_attribute_bits", ""), "shared_with_bits": lane.get("shared_with_bits", ""), "revocable": bool(lane.get("revocable", False)),
               "merge_points": [project(value["coordinate"]) for value in kml["merge_points"] if str(value["lane_id"]) == lane_id]}
        for part, lane_ids in parts.items():
            if lane_id in lane_ids:
                result[(part, approach)].append(row)
    return result


def _separate_official_merge_events(members):
    """Separate explicit, disjoint Merge memberships, not nearby road arms."""
    points, memberships = [], []
    for row in members:
        events = set()
        for point in row["merge_points"]:
            choices = [index for index, old in enumerate(points) if math.dist(point, old) <= 0.05]
            if len(choices) > 1:
                return [members]
            if not choices:
                choices = [len(points)]
                points.append(point)
            events.add(choices[0])
        memberships.append(events)
    if len(points) <= 1 or any(len(events) != 1 for events in memberships):
        return [members]
    return [[row for row, events in zip(members, memberships) if index in events] for index in range(len(points))]


def _source_arm_options(root, through, junction_id, max_error):
    options = []
    for edge in root.findall("edge"):
        lanes = [lane for lane in edge.findall("lane") if lane_supports_motorized(lane)]
        if edge.get("function") or edge.get("to") != junction_id or not lanes:
            continue
        shapes = [_parse(lane.get("shape", "")) for lane in lanes]
        errors = []
        for official in through:
            dx, dy = official["shape"][-1][0] - official["shape"][-2][0], official["shape"][-1][1] - official["shape"][-2][1]
            lane_errors = []
            for shape in shapes:
                sx, sy = shape[-1][0] - shape[-2][0], shape[-1][1] - shape[-2][1]
                norm = math.hypot(dx, dy) * math.hypot(sx, sy)
                lane_errors.append(math.dist(official["endpoint_b"], shape[-1]) if norm > 0 and (dx * sx + dy * sy) / norm >= math.cos(math.pi / 4) else math.inf)
            errors.append(min(lane_errors))
        if max(errors) <= max_error:
            options.append((sum(errors) / len(errors), edge, shapes))
    return sorted(options, key=lambda row: row[0])


def _approach_proposal(root, members, junction_id, max_error, margin):
    if any(row.get("permission_status") == "review_required" for row in members):
        return None, "official_vehicle_permission_requires_explicit_interpretation"
    merge_points = []
    for row in members:
        for point in row["merge_points"]:
            if not any(math.dist(point, old) <= 0.05 for old in merge_points):
                merge_points.append(point)
    restricted = [row for row in members if row.get("allowed_vehicle_classes") == ["bus"]]
    bounded_restricted = not merge_points and len(restricted) == 1 and len(members) > 1
    if junction_id and not merge_points and not bounded_restricted:
        unchanged = _no_lane_addition_evidence(root, members, junction_id, max_error, margin)
        if unchanged is not None:
            return unchanged, unchanged["reason"]
    if not junction_id or (len(merge_points) != 1 and not bounded_restricted):
        return None, "no_unique_official_merge_event"
    event = next(row["endpoint_a"] for row in members if row not in restricted) if bounded_restricted else merge_points[0]
    pockets = restricted if bounded_restricted else [row for row in members if math.dist(row["endpoint_a"], event) <= 1 and row["merge_points"]]
    carriers = [row for row in members if row not in pockets and (bounded_restricted or any(math.dist(point, event) <= 0.05 for point in row["merge_points"]))]
    if not carriers or (len(carriers) != 1 and not bounded_restricted):
        return None, "official_merge_has_no_unique_continuing_lane"
    carrier = carriers[0]
    dx, dy = carrier["endpoint_b"][0] - carrier["endpoint_a"][0], carrier["endpoint_b"][1] - carrier["endpoint_a"][1]
    norm = math.hypot(dx, dy)
    if norm <= 1:
        return None, "official_carrier_has_no_longitudinal_extent"
    axis = (dx / norm, dy / norm)
    station = event[0] * axis[0] + event[1] * axis[1]
    local_members = members if bounded_restricted else [row for row in members if min(x * axis[0] + y * axis[1] for x, y in row["shape"]) - 1 <= station <= max(x * axis[0] + y * axis[1] for x, y in row["shape"]) + 1]
    outside_section = [row["lane_id"] for row in members if row not in local_members]
    members = local_members
    through = [row for row in members if row not in pockets]
    other_roads = {}
    if not bounded_restricted:
        assignments = {}
        for row in through:
            matches = _source_arm_options(root, [row], junction_id, max_error)
            if matches and (len(matches) == 1 or matches[1][0] - matches[0][0] >= margin):
                assignments[row["lane_id"]] = matches[0][1].get("id")
        if len(assignments) == len(through) and len(set(assignments.values())) > 1:
            carrier_road = assignments[carrier["lane_id"]]
            for row in through:
                if assignments[row["lane_id"]] != carrier_road:
                    other_roads.setdefault(assignments[row["lane_id"]], []).append(row["lane_id"])
            outside_section.extend(row["lane_id"] for row in through if assignments[row["lane_id"]] != carrier_road)
            through = [row for row in through if assignments[row["lane_id"]] == carrier_road]
            members = [row for row in members if row in through or row in pockets]
    if len(pockets) != 1 or len(through) not in (1, 2, 3):
        return None, "not_a_documented_one_to_three_lane_plus_one_pocket"
    through = _sort_right_to_left(through)
    options = _source_arm_options(root, through, junction_id, max_error)
    if not options or (len(options) > 1 and options[1][0] - options[0][0] < margin):
        return None, "source_edge_not_uniquely_bound"
    arm_error, edge, source_shapes = options[0]
    if len(edge.findall("lane")) != len(source_shapes):
        return None, "nearest_source_arm_has_protected_nonmotor_lanes"
    if len(source_shapes) != len(through):
        return None, "nearest_source_arm_lane_count_mismatch"
    if max(math.dist(official["endpoint_b"], shape[-1]) for official, shape in zip(through, source_shapes)) > max_error:
        return None, "ordered_through_lane_identity_exceeds_limit"
    if bounded_restricted and any(math.dist(row["endpoint_a"], _reference_shape(edge)[0]) > max_error or math.dist(row["endpoint_b"], _reference_shape(edge)[-1]) > max_error for row in members):
        return None, "official_restricted_lane_does_not_cover_source_section"
    chain, reason = ([edge], "official_A_B_bounded_restricted_lane") if bounded_restricted else _source_chain_to_merge(root, edge, event, max_error, margin=margin)
    if chain is None:
        return None, reason
    ordered = _sort_right_to_left(members)
    through_indices = [next(index for index, row in enumerate(ordered) if row["lane_id"] == lane["lane_id"]) for lane in through]
    donors = {lane["lane_id"]: index for index, lane in enumerate(through)}
    for pocket in pockets:
        reference_point = pocket["endpoint_b"] if bounded_restricted else event
        carrier = sorted((_project_polyline(reference_point, row["shape"])["distance_m"], index) for index, row in enumerate(through))
        if carrier[0][0] > (max_error if bounded_restricted else 1) or (len(carrier) > 1 and carrier[1][0] - carrier[0][0] < margin):
            return None, "pocket_carrier_lane_not_unique"
        donors[pocket["lane_id"]] = carrier[0][1]
    sections = []
    for section_index, edge in enumerate(chain):
        source_shapes = [_parse(lane.get("shape", "")) for lane in edge.findall("lane")]
        source_shape = _reference_shape(edge)
        projection = _project_polyline(event, source_shape)
        from_junction = root.find(f"junction[@id='{edge.get('from')}']")
        attachment = _merge_attachment(edge, from_junction, event, max_error)
        reuse = bounded_restricted or section_index > 0 or attachment["merge_attachment_basis"] != "interior_source_edge_projection"
        start_point = event if section_index == 0 else (float(from_junction.get("x")), float(from_junction.get("y")))
        to_junction = root.find(f"junction[@id='{edge.get('to')}']")
        end_point = (float(to_junction.get("x")), float(to_junction.get("y")))
        local_lanes = []
        for row in ordered:
            start_fraction = 0 if bounded_restricted else _project_polyline(start_point, row["shape"])["fraction"]
            end_fraction = 1 if section_index == len(chain) - 1 else _project_polyline(end_point, row["shape"])["fraction"]
            if end_fraction <= start_fraction:
                return None, "official_lane_does_not_cover_source_chain_section"
            retained_shape = source_shapes[donors[row["lane_id"]]] if bounded_restricted and row in through else _clip_polyline_fraction(row["shape"], start_fraction, end_fraction)
            local_lanes.append({**row, "shape": retained_shape})
        upstream_shapes = [_clip_polyline_fraction(shape, 0, _project_polyline(event, shape)["fraction"]) for shape in source_shapes]
        if not reuse and any(len(shape) < 2 or sum(math.dist(a, b) for a, b in zip(shape, shape[1:])) <= 1e-6 for shape in upstream_shapes):
            return None, "source_split_has_no_nonzero_visible_prefix"
        length = sum(math.dist(a, b) for a, b in zip(source_shape, source_shape[1:]))
        sections.append({"source_edge_id": edge.get("id"), "original_from": edge.get("from"), "original_to": edge.get("to"), "split_xy": [float(from_junction.get("x")), float(from_junction.get("y"))] if reuse else list(projection["point"]), "official_merge_xy": None if bounded_restricted else list(event),
            "mode": "official_bounded_restricted_lane" if bounded_restricted else "existing_chain_section" if section_index > 0 else "reuse_existing_merge_junction" if reuse else "split_inside_source_edge",
            **attachment,
            "revocable_lane_policy": "enabled_for_topology_test;activation_not_replayed" if any(row.get("revocable") for row in members) else "not_revocable",
            "official_lane_permissions": {row["lane_id"]: {key: row.get(key) for key in ("vehicle_attribute_bits", "shared_with_bits", "allowed_vehicle_classes", "permission_status", "revocable")} for row in members},
            "chain_edge_ids": [item.get("id") for item in chain], "section_index": section_index,
            "previous_section_edge_id": chain[section_index - 1].get("id") if section_index else None,
            "source_interval_m": [0, length], "upstream_interval_m": None if reuse else [0, length * projection["fraction"]], "downstream_interval_m": [0 if reuse else length * projection["fraction"], length],
            "through_lane_ids": [row["lane_id"] for row in through], "pocket_lane_ids": [row["lane_id"] for row in pockets], "lane_count_before": len(through), "lane_count_after": len(members),
            "same_approach_lanes_outside_section": outside_section,
            "same_approach_lanes_on_other_source_roads": other_roads,
            "source_chain_selection_basis": reason,
            "official_lane_indices": {row["lane_id"]: index for index, row in enumerate(ordered)},
            "source_lane_to_downstream_lane": {str(index): value for index, value in enumerate(through_indices)},
            "lane_attribute_inheritance": {row["lane_id"]: {"source_edge_id": edge.get("id"), "source_lane_index": donors[row["lane_id"]], "basis": "continuing_lane" if row["lane_id"] in [lane["lane_id"] for lane in through] else "official_restricted_lane_neighbor" if bounded_restricted else "official_merge_carrier", "attributes": {key: edge.findall("lane")[donors[row["lane_id"]]].get(key) for key in _LANE_ATTRIBUTES if edge.findall("lane")[donors[row["lane_id"]]].get(key) is not None}, "official_permission_override": row.get("allowed_vehicle_classes")} for row in ordered},
            "source_arm_match_error_m": arm_error,
            "local_geometry_basis": "official_A_B_bounded_restricted_lane" if bounded_restricted else "explicit_official_MAP_Merge_and_lane_geometry",
            "transition_geometry_basis": "source_junction_internal_links_recomputed_by_netconvert_not_independently_surveyed",
            "_upstream_edge_shape": _clip_polyline_fraction(source_shape, 0, projection["fraction"]), "_upstream_lane_shapes": upstream_shapes,
            "_ordered_lanes": local_lanes, "_through_indices": through_indices, "_donor_indices": donors})
    return (sections[0] if len(sections) == 1 else {"_chain_sections": sections}), "official_merge_split"


def _no_lane_addition_evidence(root, members, junction_id, max_error, margin):
    """A missing Merge alone does not establish that an approach needs no addition."""
    if not members or any(row["merge_points"] or row.get("allowed_vehicle_classes") is not None or row.get("revocable") for row in members):
        return None
    start, end = members[0]["endpoint_a"], members[0]["endpoint_b"]
    length = math.dist(start, end)
    if length <= 1:
        return None
    axis = ((end[0] - start[0]) / length, (end[1] - start[1]) / length)
    starts = [sum(row["endpoint_a"][index] * axis[index] for index in (0, 1)) for row in members]
    if max(starts) - min(starts) > 1:
        return None
    ordered = _sort_right_to_left(members)
    options = _source_arm_options(root, ordered, junction_id, max_error)
    if not options or len(options) > 1 and options[1][0] - options[0][0] < margin:
        return None
    _, edge, shapes = options[0]
    if len(shapes) != len(ordered) or len(edge.findall("lane")) != len(shapes):
        return None
    errors = []
    for index, official in enumerate(ordered):
        fits = [max(_project_polyline(point, shape)["distance_m"] for point in official["shape"]) for shape in shapes]
        if fits[index] > max_error or any(value - fits[index] < margin for other, value in enumerate(fits) if other != index):
            return None
        errors.append(fits[index])
    return {"status": "not_applicable", "reason": "no_documented_addition_with_matching_source_lanes",
            "source_edge_id": edge.get("id"), "official_lane_indices": {row["lane_id"]: index for index, row in enumerate(ordered)},
            "official_merge_count": 0, "official_start_section_spread_m": max(starts) - min(starts),
            "maximum_lane_match_error_m": max(errors),
            "claim_boundary": "The documented lanes share one upstream section and match existing source lanes. No lane addition is inferred. This does not certify source geometry or rule out an undocumented lane."}


def _reference_shape(edge):
    shapes = [_parse(lane.get("shape", "")) for lane in edge.findall("lane")]
    return _parse(edge.get("shape", "")) or [_mean([shape[0] for shape in shapes]), _mean([shape[-1] for shape in shapes])]


def _merge_attachment(edge, node, event, max_error):
    """Do not split off a fictitious lane prefix hidden by a source junction."""
    polygon = _parse(node.get("shape", ""))
    inside = _point_in_polygon(event, polygon)
    projection = _project_polyline(event, _reference_shape(edge))
    lane_shapes = [_parse(lane.get("shape", "")) for lane in edge.findall("lane")]
    covered = bool(lane_shapes) and 0.001 < projection["fraction"] < 0.999 and projection["distance_m"] <= max_error and _point_in_polygon(projection["point"], polygon) and all(len(shape) >= 2 and _project_polyline(event, shape)["fraction"] <= 1e-9 for shape in lane_shapes)
    return {"merge_attachment_basis": "source_junction_contains_official_merge" if inside else "source_junction_covers_longitudinal_transition" if covered else "interior_source_edge_projection",
            "official_merge_inside_source_junction": inside,
            "official_merge_projection_error_m": projection["distance_m"]}


def _source_chain_to_merge(root, target, event, max_error, *, margin=0.5):
    edges = {edge.get("id"): edge for edge in root.findall("edge") if not edge.get("function")}
    chain = [target]
    selected_by_merge = False
    while True:
        current = chain[0]
        projection = _project_polyline(event, _reference_shape(current))
        node = root.find(f"junction[@id='{current.get('from')}']")
        if node is None:
            return None, "source_chain_boundary_node_missing"
        if _merge_attachment(current, node, event, max_error)["merge_attachment_basis"] != "interior_source_edge_projection" or (projection["distance_m"] <= max_error and 0.001 < projection["fraction"] < 0.999):
            return chain, "official_merge_selects_unique_predecessor_at_branch" if selected_by_merge else "merge_reached"
        if projection["fraction"] > 0.001:
            return None, "official_merge_is_not_upstream_of_source_chain"
        if node.get("type", "").startswith("rail"):
            return None, "upstream_chain_crosses_rail_control"
        links = [row for row in root.findall("connection") if row.get("to") == current.get("id") and not row.get("from", "").startswith(":")]
        if node.get("type", "").startswith("traffic_light") or any(row.get("tl") for row in links):
            for link in links:
                index = link.get("linkIndex", "")
                programs = root.findall(f"tlLogic[@id='{link.get('tl', '')}']")
                if not link.get("tl") or not index.isdecimal() or link.get("linkIndex2") or not programs or any(not program.findall("phase") or any(int(index) >= len(phase.get("state", "")) for phase in program.findall("phase")) for program in programs):
                    return None, "upstream_chain_missing_control_identity"
        predecessor_ids = {row.get("from") for row in links}
        branch_selected = False
        if len(predecessor_ids) != 1:
            choices = []
            for predecessor in predecessor_ids:
                previous = edges.get(predecessor)
                if previous is None:
                    continue
                fit = _project_polyline(event, _reference_shape(previous))
                if fit["distance_m"] <= max_error and 0.001 < fit["fraction"] < 0.999:
                    choices.append((fit["distance_m"], predecessor))
            choices.sort()
            if not choices or len(choices) > 1 and choices[1][0] - choices[0][0] < margin:
                return None, "upstream_chain_has_no_unique_predecessor"
            predecessor_ids = {choices[0][1]}
            branch_selected = selected_by_merge = True
        previous = edges.get(next(iter(predecessor_ids)))
        if previous is None or previous in chain:
            return None, "upstream_chain_is_missing_or_cyclic"
        neighbors = {edge.get("from") if edge.get("to") == node.get("id") else edge.get("to") for edge in edges.values() if node.get("id") in (edge.get("from"), edge.get("to"))}
        if not branch_selected and neighbors != {previous.get("from"), current.get("to")}:
            return None, "upstream_chain_crosses_a_branch"
        old_lanes, new_lanes = previous.findall("lane"), current.findall("lane")
        selected_links = [row for row in links if row.get("from") == previous.get("id")]
        if len(old_lanes) != len(new_lanes) or {(row.get("fromLane"), row.get("toLane")) for row in selected_links} != {(str(index), str(index)) for index in range(len(new_lanes))}:
            return None, "upstream_chain_lane_continuity_not_proven"
        if any(any(old.get(key) != new.get(key) for key in ("allow", "disallow", "changeLeft", "changeRight")) for old, new in zip(old_lanes, new_lanes)):
            return None, "upstream_chain_lane_permissions_differ"
        old_params = {item.get("key"): item.get("value") for item in previous.findall("param")}
        new_params = {item.get("key"): item.get("value") for item in current.findall("param")}
        if any(old_params.get(key, default) != new_params.get(key, default) for key, default in (("layer", "0"), ("level", "0"), ("tunnel", "no"), ("bridge", "no"))):
            return None, "upstream_chain_changes_road_grade"
        old_shape, new_shape = _reference_shape(previous), _reference_shape(current)
        dx, dy = old_shape[-1][0] - old_shape[-2][0], old_shape[-1][1] - old_shape[-2][1]
        sx, sy = new_shape[1][0] - new_shape[0][0], new_shape[1][1] - new_shape[0][1]
        norm = math.hypot(dx, dy) * math.hypot(sx, sy)
        if norm <= 0 or (dx * sx + dy * sy) / norm < math.cos(math.pi / 4):
            return None, "upstream_chain_direction_not_continuous"
        previous_node = root.find(f"junction[@id='{previous.get('from')}']")
        previous_owns_merge = previous_node is not None and _point_in_polygon(event, _parse(previous_node.get("shape", "")))
        if not previous_owns_merge and _project_polyline(event, old_shape)["distance_m"] >= projection["distance_m"]:
            return None, "upstream_chain_does_not_approach_official_merge"
        chain.insert(0, previous)


def _preserved_edge(expected, actual):
    if any(expected.get(key) != actual.get(key) for key in ("from", "to", "priority", "type", "spreadType")):
        return False
    left, right = expected.findall("lane"), actual.findall("lane")
    if len(left) != len(right):
        return False
    for first, second in zip(left, right):
        if any(first.get(key) != second.get(key) for key in _LANE_ATTRIBUTES):
            return False
        a, b = _parse(first.get("shape", "")), _parse(second.get("shape", ""))
        if not a or not b or max(_project_polyline(point, b)["distance_m"] for point in a) > 0.1 or max(_project_polyline(point, a)["distance_m"] for point in b) > 0.1:
            return False
        if not {(item.get("key"), item.get("value")) for item in first.findall("param")} <= {(item.get("key"), item.get("value")) for item in second.findall("param")}:
            return False
    return True


def _boundary_adjustment_scope(root, seeds, excluded_edges):
    """Only pre-existing lanes buried inside overlapping boundary owners qualify."""
    nodes = {node.get("id"): node for node in root.findall("junction")}
    scope = {}
    for edge in root.findall("edge"):
        owners = [edge.get("from"), edge.get("to")]
        if edge.get("function") or edge.get("id") in excluded_edges or not seeds.intersection(owners) or any(owner not in nodes for owner in owners):
            continue
        polygons = [_parse(nodes[owner].get("shape", "")) for owner in owners]
        shapes = [_parse(lane.get("shape", "")) for lane in edge.findall("lane")]
        if shapes and all(len(poly) >= 3 for poly in polygons) and all(_line_inside_polygons(shape, [polygon]) for shape in shapes for polygon in polygons):
            scope[edge.get("id")] = {"node_ids": owners, "polygons": polygons, "geometry_tolerance_m": 0.1}
    return scope


def _line_inside_polygons(shape, polygons):
    def inside(point):
        return any(_point_in_polygon(point, polygon) or _project_polyline(point, [*polygon, polygon[0]])["distance_m"] <= 0.1 for polygon in polygons)
    if len(shape) < 2 or not all(inside(point) for point in shape):
        return False
    for a, b in zip(shape, shape[1:]):
        dx, dy = b[0] - a[0], b[1] - a[1]
        length2 = dx * dx + dy * dy
        if length2 <= 1e-12:
            continue
        cuts = [0.0, 1.0]
        for polygon in polygons:
            for c, d in zip(polygon, [*polygon[1:], polygon[0]]):
                point = _segment_intersection(a, b, c, d)
                if point is not None:
                    cuts.append(max(0.0, min(1.0, ((point[0] - a[0]) * dx + (point[1] - a[1]) * dy) / length2)))
        cuts.sort()
        if any(not inside((a[0] + dx * (lo + hi) / 2, a[1] + dy * (lo + hi) / 2)) for lo, hi in zip(cuts, cuts[1:])):
            return False
    return True


def _preserved_boundary_edge(expected, actual, polygons):
    old_lanes, new_lanes = expected.findall("lane"), actual.findall("lane")
    if len(old_lanes) != len(new_lanes) or not all(_line_inside_polygons(_parse(lane.get("shape", "")), [polygon]) for lane in old_lanes for polygon in polygons):
        return False
    if not all(_line_inside_polygons(_parse(lane.get("shape", "")), polygons) for lane in new_lanes):
        return False
    comparable = deepcopy(actual)
    for old, new in zip(old_lanes, comparable.findall("lane")):
        new.set("shape", old.get("shape", ""))
    return _preserved_edge(expected, comparable)


def _set_lane_shape(lane, index, shape):
    for key in ("id", "length"):
        lane.attrib.pop(key, None)
    lane.set("index", str(index))
    lane.set("shape", _shape(shape))


def _parse(value):
    result = []
    for token in value.split():
        point = tuple(map(float, token.split(",")))
        if len(point) > 2 and point[2] != 0:
            raise ValueError("nonzero 3D lane geometry requires a separate grade-aware approach rebuild")
        result.append(point[:2])
    return result


def _mean(points):
    return tuple(sum(point[index] for point in points) / len(points) for index in (0, 1))


def _shape(points):
    return " ".join(f"{x:.6f},{y:.6f}" for x, y in points)


def _connection_key(attrs):
    return tuple(str(attrs.get(key, "")) for key in ("from", "fromLane", "to", "toLane"))


def _identity(path):
    return {"path": str(path), "sha256": file_sha256(path)}


def _save(destination, report):
    manifest = destination / "manifest.json"
    manifest.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    return {**report, "manifest": _identity(manifest)}
