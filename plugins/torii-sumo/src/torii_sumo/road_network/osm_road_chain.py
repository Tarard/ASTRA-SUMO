"""Inspect shared-node OSM road continuity without guessing links or compiling SUMO."""

from collections import defaultdict
from collections.abc import Mapping
import gzip
import hashlib
import math
from pathlib import Path
import re
import xml.etree.ElementTree as ET

from ..core.candidate_contracts import file_sha256
from ..road_semantics import OSM_PASSENGER_HIGHWAYS, classify_turn_direction
from .adapters.osm import _directionality, _haversine_m, _is_gzip


_ACCESS = {"access", "vehicle", "motor_vehicle", "motorcar", "bus", "psv", "hgv", "bicycle", "foot",
           "taxi", "emergency", "goods", "delivery", "motorcycle", "moped", "maxheight", "maxweight", "maxwidth", "maxlength"}
_FACILITIES = {"cycleway", "sidewalk", "segregated", "shoulder", "parking"}
_STRUCTURE = {"bridge", "tunnel", "layer", "level", "covered", "indoor"}
_PHYSICAL = _ACCESS | _FACILITIES | _STRUCTURE | {"lanes", "turn", "change", "maxspeed", "minspeed", "width", "est_width", "oneway", "highway", "junction"}


def _id(value):
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise ValueError("OSM IDs must be nonempty strings without surrounding whitespace.")
    return value


def _tags(element):
    tags = {}
    for child in element.findall("tag"):
        key, value = child.get("k"), child.get("v")
        if not key or value is None or key in tags:
            raise ValueError("OSM tags require unique keys and string values.")
        tags[key] = value
    return tags


def _flag(value):
    value = str(value or "").strip().lower()
    return "no" if value in {"", "no", "false", "0"} else "yes" if value in {"yes", "true", "1"} else value


def _layer(tags):
    try:
        value = float(tags.get("layer", "0"))
        return value if math.isfinite(value) else None
    except ValueError:
        return None


def _grade(tags):
    return _layer(tags), _flag(tags.get("bridge")), _flag(tags.get("tunnel"))


def _profile(way):
    result = {}
    opposite = {"forward": "backward", "backward": "forward", "left": "right", "right": "left"}
    for key, value in way["tags"].items():
        if key.split(":", 1)[0] in _PHYSICAL:
            normalized = ":".join(opposite.get(part, part) for part in key.split(":")) if way["reversed"] else key
            result[normalized] = value.strip()
    result.update(oneway=way["directionality"], layer=_layer(way["tags"]),
                  bridge=_flag(way["tags"].get("bridge")), tunnel=_flag(way["tags"].get("tunnel")))
    return result


def _heading(a, b):
    lon1, lat1, lon2, lat2 = map(math.radians, (*a, *b))
    dx = math.sin(lon2 - lon1) * math.cos(lat2)
    dy = math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(lon2 - lon1)
    return dx, dy


def inspect_osm_road_chain(source_osm, way_ids, *, start_node_id=None):
    """Return the whole directed axis and boundaries that must survive any later road model."""
    if not isinstance(source_osm, Mapping) or not isinstance(source_osm.get("path"), (str, Path)):
        raise ValueError("source_osm requires a path and SHA-256.")
    expected = source_osm.get("sha256")
    if not isinstance(expected, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", expected):
        raise ValueError("source_osm requires a valid SHA-256.")
    year = source_osm.get("data_year")
    if year is not None and (type(year) is not int or not 1900 <= year <= 2200):
        raise ValueError("data_year must be a year or null.")
    if not isinstance(way_ids, list) or not way_ids:
        raise ValueError("way_ids must be a nonempty ordered list.")
    for value in way_ids:
        _id(value)
    if len(set(way_ids)) != len(way_ids):
        raise ValueError("way_ids must not repeat a way.")
    if start_node_id is not None:
        _id(start_node_id)
    path = Path(source_osm["path"]).expanduser().resolve(strict=True)
    raw = path.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if digest != expected.lower():
        raise ValueError("OSM SHA-256 does not match.")
    try:
        root = ET.fromstring(gzip.decompress(raw) if _is_gzip(path, raw) else raw)
    except (ET.ParseError, OSError, EOFError) as error:
        raise ValueError("source_osm must contain readable OSM XML.") from error
    if root.tag != "osm":
        raise ValueError("source_osm must contain raw OSM XML.")
    objects = {}
    for kind in ("node", "way", "relation"):
        objects[kind] = {}
        for element in root.findall(kind):
            key = _id(element.get("id"))
            if key in objects[kind]:
                raise ValueError(f"Duplicate OSM {kind} ID: {key}")
            objects[kind][key] = element
    all_ways, incident = {}, defaultdict(set)
    for key, element in objects["way"].items():
        refs = [_id(n.get("ref")) for n in element.findall("nd")]
        all_ways[key] = dict(way_id=key, node_ids=refs, tags=_tags(element), attributes=dict(element.attrib))
        if "highway" in all_ways[key]["tags"]:
            for node_id in refs:
                incident[node_id].add(key)
    memberships, relations = defaultdict(list), {}
    selected_refs = set(way_ids) | {ref for key in way_ids if key in all_ways for ref in all_ways[key]["node_ids"]}
    for key, element in objects["relation"].items():
        members = [dict(m.attrib) for m in element.findall("member")]
        if not any(m.get("ref") in selected_refs for m in members):
            continue
        tags = _tags(element)
        relations[key] = dict(relation_id=key, tags=tags, members=members, attributes=dict(element.attrib))
        for index, member in enumerate(members):
            memberships[(member.get("type"), member.get("ref"))].append(
                dict(relation_id=key, type=tags.get("type"), role=member.get("role", ""), member_index=index))
    report = dict(schema="torii.osm-road-chain/v1", status="review_required", source=dict(source_osm, path=str(path), sha256=digest),
                  requested_way_ids=way_ids, start_node_id=start_node_id, ways=[], nodes={}, relations=list(relations.values()),
                  axis_lonlat=[], axis_node_ids=[], axis_length_m=None, boundaries=[], mandatory_boundary_node_ids=[],
                  source_lineage=[], aggregation_allowed=False, reasons=[], topology_connected=False,
                  direction_resolved=False, network_changed=False,
                  claim_boundary="The axis follows shared OSM endpoint identities and base road direction. It is not a surveyed lane centreline. "
                                 "Mandatory interior boundaries prohibit whole-chain aggregation. Source tags and relations remain evidence, not field certification.")
    def finish():
        report["source_immutable"] = path.is_file() and file_sha256(path) == digest
        if not report["source_immutable"]:
            report["reasons"].append(dict(reason="source_changed_during_inspection"))
            report["aggregation_allowed"] = False
        report["status"] = "review_required" if report["reasons"] else "pass"
        return report
    for key in way_ids:
        if key not in all_ways:
            report["reasons"].append(dict(way_id=key, reason="requested_way_not_found"))
            continue
        row = dict(all_ways[key], relation_membership=memberships[("way", key)], geometry_lonlat=[],
                   missing_node_ids=[], oriented_node_ids=[], reversed=None)
        direction, permitted, status, basis = _directionality(row["tags"])
        row.update(directionality=direction, oneway_direction=permitted, directionality_status=status, directionality_basis=basis)
        if (len(row["node_ids"]) < 2 or len(set(row["node_ids"])) != len(row["node_ids"])
                or "highway" not in row["tags"] or _flag(row["tags"].get("area")) == "yes"):
            report["reasons"].append(dict(way_id=key, reason="non_simple_linear_highway_way"))
        if direction == "unknown" or any(k.startswith("oneway") and "conditional" in k.split(":") for k in row["tags"]):
            report["reasons"].append(dict(way_id=key, reason="directionality_unresolved"))
        for node_id in row["node_ids"]:
            element = objects["node"].get(node_id)
            coordinate = None
            if element is not None and element.get("lon") is not None and element.get("lat") is not None:
                try:
                    coordinate = [float(element.get("lon")), float(element.get("lat"))]
                except ValueError as error:
                    raise ValueError(f"Invalid coordinate at node {node_id}.") from error
                if not all(math.isfinite(v) for v in coordinate) or not -180 <= coordinate[0] <= 180 or not -90 <= coordinate[1] <= 90:
                    raise ValueError(f"Invalid coordinate at node {node_id}.")
            if coordinate is None:
                row["missing_node_ids"].append(node_id)
            row["geometry_lonlat"].append(coordinate)
            report["nodes"][node_id] = dict(node_id=node_id, lonlat=coordinate,
                tags=_tags(element) if element is not None else {}, relation_membership=memberships[("node", node_id)])
        if row["missing_node_ids"]:
            report["reasons"].append(dict(way_id=key, reason="missing_node_geometry", node_ids=row["missing_node_ids"]))
        report["ways"].append(row)
    if report["reasons"]:
        return finish()
    joins = []
    for left, right in zip(report["ways"], report["ways"][1:]):
        shared = set(left["node_ids"]) & set(right["node_ids"])
        if len(shared) != 1:
            report["reasons"].append(dict(way_ids=[left["way_id"], right["way_id"]],
                reason="disconnected_ways" if not shared else "multiple_shared_nodes", shared_node_ids=sorted(shared)))
            continue
        node_id = next(iter(shared))
        if node_id not in (left["node_ids"][0], left["node_ids"][-1]) or node_id not in (right["node_ids"][0], right["node_ids"][-1]):
            report["reasons"].append(dict(node_id=node_id, reason="shared_node_not_way_endpoint"))
        joins.append(node_id)
    if report["reasons"]:
        return finish()
    report["topology_connected"] = True
    axis_ids = []
    for index, row in enumerate(report["ways"]):
        refs = row["node_ids"]
        desired = joins[index - 1] if index else start_node_id
        if desired is None:
            desired = (refs[-1] if refs[0] == joins[0] else refs[0]) if joins else refs[-1] if row["oneway_direction"] == "against" else refs[0]
        reverse = desired == refs[-1]
        oriented = list(reversed(refs)) if reverse else list(refs)
        row.update(reversed=reverse, oriented_node_ids=oriented)
        if desired != oriented[0] or index < len(joins) and oriented[-1] != joins[index]:
            report["reasons"].append(dict(way_id=row["way_id"], reason="start_or_end_does_not_follow_ordered_chain"))
        if row["oneway_direction"] in {"with", "against"} and reverse != (row["oneway_direction"] == "against"):
            report["reasons"].append(dict(way_id=row["way_id"], reason="oneway_reverse_forbidden"))
        first_index = max(0, len(axis_ids) - 1)
        axis_ids.extend(oriented if not axis_ids else oriented[1:])
        report["source_lineage"].append(dict(way_id=row["way_id"], first_axis_index=first_index,
                                            last_axis_index=len(axis_ids) - 1, reversed=reverse))
    if len(set(axis_ids)) != len(axis_ids):
        report["reasons"].append(dict(reason="chain_revisits_node"))
    if report["reasons"]:
        return finish()
    geometry = [report["nodes"][key]["lonlat"] for key in axis_ids]
    stations = [0.0]
    for a, b in zip(geometry, geometry[1:]):
        stations.append(stations[-1] + _haversine_m(a, b))
    if stations[-1] <= 1e-6:
        report["reasons"].append(dict(reason="degenerate_axis_geometry"))
        return finish()
    report.update(axis_node_ids=axis_ids, axis_lonlat=geometry, axis_length_m=stations[-1], direction_resolved=True)
    protected = {key for key, rel in relations.items() if rel["tags"].get("type") in {"restriction", "connectivity"}
                 or any(k.startswith("restriction") for k in rel["tags"])}
    profiles = {row["way_id"]: _profile(row) for row in report["ways"]}
    for index, node_id in enumerate(axis_ids):
        members = [row for row in report["ways"] if node_id in row["node_ids"]]
        reasons, changes, side_roads, uncertain_roads = [], {}, [], []
        if index in (0, len(axis_ids) - 1):
            reasons.append("chain_endpoint")
        if len(members) == 2:
            before, after = (profiles[row["way_id"]] for row in members)
            changes = {key: dict(before=before.get(key), after=after.get(key)) for key in before.keys() | after.keys()
                       if before.get(key) != after.get(key)}
            if changes:
                reasons.append("physical_attribute_change")
            if any(key.split(":", 1)[0] in _STRUCTURE for key in changes):
                reasons.append("structure_or_layer_change")
        tags = report["nodes"][node_id]["tags"]
        if tags.get("highway") in {"traffic_signals", "crossing", "stop", "give_way"} or any(k == "crossing" or k.startswith("crossing:") for k in tags):
            reasons.append("node_control_or_crossing")
        if tags.get("barrier") not in (None, "no"):
            reasons.append("node_barrier")
        relation_ids = {m["relation_id"] for m in memberships[("node", node_id)]} & protected
        relation_ids |= {m["relation_id"] for way in members if node_id in (way["node_ids"][0], way["node_ids"][-1])
                         for m in way["relation_membership"]} & protected
        if relation_ids:
            reasons.append("protected_relation")
        grades = {_grade(row["tags"]) for row in members}
        if any(grade[0] is None for grade in grades):
            reasons.append("layer_unresolved")
            report["reasons"].append(dict(node_id=node_id, reason="layer_unresolved"))
        chain_neighbors = set(axis_ids[max(0, index - 1):index] + axis_ids[index + 1:index + 2])
        physical_neighbors = set(chain_neighbors)
        for way_id in sorted(incident[node_id] - set(way_ids)):
            other = all_ways[way_id]
            refs = other["node_ids"]
            adjacent = {refs[j + delta] for j, ref in enumerate(refs) if ref == node_id
                        for delta in (-1, 1) if 0 <= j + delta < len(refs)}
            if _grade(other["tags"]) not in grades or _layer(other["tags"]) is None:
                uncertain_roads.append(way_id)
            elif adjacent <= chain_neighbors:
                reasons.append("overlapping_way_membership")
                report["reasons"].append(dict(node_id=node_id, way_id=way_id, reason="overlapping_way_membership"))
            elif other["tags"].get("highway") in OSM_PASSENGER_HIGHWAYS:
                side_roads.append(way_id)
                physical_neighbors.update(adjacent)
            else:
                reasons.append("other_highway_connection")
        if side_roads:
            reasons.append("same_plane_side_road")
        if uncertain_roads:
            reasons.append("side_road_grade_unresolved")
            report["reasons"].append(dict(node_id=node_id, reason="side_road_grade_unresolved", way_ids=uncertain_roads))
        if 0 < index < len(axis_ids) - 1 and classify_turn_direction(_heading(geometry[index - 1], geometry[index]), _heading(geometry[index], geometry[index + 1])) == "u_turn":
            reasons.append("opposed_direction_at_join")
        mandatory = bool(reasons)
        report["boundaries"].append(dict(node_id=node_id, axis_index=index, station_m=stations[index],
            classification="mandatory_boundary" if mandatory else "continuation_candidate", mandatory_boundary=mandatory,
            reasons=sorted(set(reasons)), attribute_changes=changes, side_road_way_ids=side_roads,
            grade_unresolved_way_ids=uncertain_roads, protected_relation_ids=sorted(relation_ids),
            same_plane_road_degree=len(physical_neighbors), source_way_ids=[w["way_id"] for w in members]))
    report["mandatory_boundary_node_ids"] = [b["node_id"] for b in report["boundaries"] if b["mandatory_boundary"]]
    report["aggregation_allowed"] = not report["reasons"] and not any(b["mandatory_boundary"] for b in report["boundaries"][1:-1])
    return finish()
