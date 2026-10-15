"""Identify parking buildings and preserve their detailed SUMO access network."""

from __future__ import annotations

import gzip
import math
from pathlib import Path
import xml.etree.ElementTree as ET

from pyproj import CRS, Transformer
from shapely.geometry import LineString, Polygon, mapping

from .artifact_io import write_json_atomic
from .candidate_contracts import file_sha256
from .network_structure_review import review_network_structures
from .reference_spatial_registration import read_network_coordinate_frame


FACILITY_PARAM = "astra.parking.facility"
ROLE_PARAM = "astra.parking.role"


def _tags(element):
    return {tag.get("k"): tag.get("v", "") for tag in element.findall("tag")}


def _parking(tags):
    return tags.get("building") == "parking" or (
        tags.get("amenity") == "parking" and tags.get("parking") in {"multi-storey", "underground"})


def _osm(path: Path):
    nodes, ways, unresolved = {}, {}, []
    with (gzip.open(path, "rb") if path.suffix == ".gz" else path.open("rb")) as stream:
        first = True
        for event, element in ET.iterparse(stream, events=("start", "end")):
            if first:
                if element.tag != "osm":
                    raise ValueError("Use an OSM XML snapshot.")
                first = False
            if event != "end":
                continue
            if element.tag == "node":
                if element.get("id") in nodes:
                    raise ValueError("Use one OSM snapshot with unique node identities.")
                point = (float(element.get("lon")), float(element.get("lat")))
                if not all(math.isfinite(value) for value in point) or not (-180 <= point[0] <= 180 and -90 <= point[1] <= 90):
                    raise ValueError("OSM node coordinates must be finite longitude and latitude.")
                nodes[element.get("id")] = point
            elif element.tag == "way":
                tags = _tags(element)
                if _parking(tags) or tags.get("highway") == "service":
                    ways[element.get("id")] = ([node.get("ref") for node in element.findall("nd")], tags)
            elif element.tag == "relation" and _parking(_tags(element)):
                # shortcut: Relation footprints require review until complete multipolygon assembly is supported.
                unresolved.append({"id": f"relation/{element.get('id')}", "reason": "multipolygon_footprint_requires_review",
                    "member_way_ids": sorted(member.get("ref") for member in element.findall("member") if member.get("type") == "way")})
            if element.tag in {"node", "way", "relation"}:
                element.clear()
    return nodes, ways, unresolved


def identify_parking_facilities(*, net_file: Path, osm_file: Path) -> dict:
    nodes, ways, unresolved = _osm(osm_file)
    footprints = {}
    relation_members = {key for row in unresolved for key in row.get("member_way_ids", [])}
    for key, (refs, tags) in sorted(ways.items()):
        if not _parking(tags):
            continue
        if key in relation_members:
            continue
        if len(refs) < 4 or refs[0] != refs[-1] or any(ref not in nodes for ref in refs):
            unresolved.append({"id": f"way/{key}", "reason": "incomplete_parking_footprint"})
            continue
        polygon = Polygon([nodes[ref] for ref in refs])
        if not polygon.is_valid or polygon.area == 0 or polygon.bounds[2] - polygon.bounds[0] > 180:
            unresolved.append({"id": f"way/{key}", "reason": "invalid_parking_footprint"})
            continue
        footprints[key] = (polygon, tags)
    matched_ways = {key: set() for key in footprints}
    for way_id, (refs, tags) in sorted(ways.items()):
        covered = tags.get("tunnel", "").lower() not in {"", "no", "false", "0"} or any(
            tags.get(key) == "yes" for key in ("covered", "indoor"))
        if (tags.get("highway") != "service" or not (covered or tags.get("service") == "parking_aisle")
                or len(refs) < 2 or any(ref not in nodes for ref in refs)):
            continue
        line = LineString([nodes[ref] for ref in refs])
        matches = [key for key, (polygon, _tags_) in footprints.items() if polygon.covers(line)]
        if len(matches) == 1:
            matched_ways[matches[0]].add(way_id)
        elif len(matches) > 1:
            unresolved.append({"id": f"way/{way_id}", "reason": "overlapping_parking_footprints"})
    root = ET.parse(net_file).getroot()
    structures = review_network_structures(root)
    edges = {edge.get("id"): edge for edge in root.findall("edge")}
    ordinary = {key: edge for key, edge in edges.items() if edge.get("function", "normal") == "normal"}
    junctions = {node.get("id"): node for node in root.findall("junction")}
    frame = read_network_coordinate_frame(net_file)
    transformer = Transformer.from_crs(CRS.from_user_input(frame.projection), 4326, always_xy=True, allow_ballpark=False)

    def geographic(point):
        x, y = point
        if not math.isfinite(x) or not math.isfinite(y):
            raise ValueError("Parking boundary coordinates must be finite.")
        return list(transformer.transform(x - frame.net_offset_x, y - frame.net_offset_y, errcheck=True))

    facilities = []
    for identifier, (polygon, tags) in sorted(footprints.items()):
        matched = matched_ways[identifier]
        interior = set()
        for key, edge in ordinary.items():
            origin = {part for item in [edge, *edge.findall("lane")] for param in item.findall("param")
                      if param.get("key") == "origId" for part in param.get("value", "").split()}
            if origin and origin <= matched:
                interior.add(key)
            elif origin & matched:
                unresolved.append({"id": key, "reason": "mixed_interior_and_exterior_origins"})
        if not interior:
            continue
        facility_id = f"osm-way-{identifier}"
        internal_lanes, gates = set(), {}
        complete = True
        for connection in structures["connection_chains"]:
            source, target = connection["from"], connection["to"]
            if source not in interior and target not in interior:
                continue
            complete &= connection["complete"]
            internal_lanes.update(connection["via_lane_ids"])
            if (source in interior) == (target in interior):
                continue
            role = "entry" if target in interior else "exit"
            edge_id = source if role == "entry" else target
            node_id = ordinary[source].get("to")
            if node_id != ordinary[target].get("from") or node_id not in junctions:
                complete = False
                continue
            node = junctions[node_id]
            lane = ordinary[edge_id].findall("lane")[int(connection["fromLane"] if role == "entry" else connection["toLane"])]
            shape = [tuple(map(float, value.split(",")[:2])) for value in lane.get("shape", "").split()]
            if len(shape) < 2:
                complete = False
                continue
            pair = shape[-2:] if role == "entry" else shape[:2]
            gates[(role, edge_id, node_id)] = {"role": role, "edge_id": edge_id, "junction_id": node_id,
                "point_lon_lat": geographic((float(node.get("x")), float(node.get("y")))),
                "direction_lon_lat": [geographic(point) for point in pair]}
        capacity = tags.get("capacity", "")
        facilities.append({"id": facility_id, "osm_way_id": identifier,
            "name": tags.get("name", f"Parking {identifier}"), "tags": tags,
            "capacity": int(capacity) if capacity.isdecimal() else None,
            "footprint_lon_lat": mapping(polygon), "interior_osm_way_ids": sorted(matched),
            "interior_edge_ids": sorted(interior), "internal_lane_ids": sorted(internal_lanes),
            "interior_lane_ids": sorted(lane.get("id") for key in interior for lane in ordinary[key].findall("lane")),
            "interior_junction_ids": sorted({edge.get(name) for key, edge in ordinary.items() if key in interior
                                             for name in ("from", "to")}),
            "portals": [gates[key] for key in sorted(gates)], "connection_trace_complete": complete,
            "status": "review_required", "parking_behavior": "not_configured",
            "physical_constraints": "metadata_only"})
    return {"facilities": facilities, "supported_footprint_count": len(footprints),
            "unresolved": sorted(unresolved, key=lambda row: (row["id"], row["reason"]))}


def build_parking_facility_model(*, net_file: str | Path, osm_file: str | Path, output_dir: str | Path,
                                 expected_net_sha256: str, expected_osm_sha256: str) -> dict:
    source, osm = Path(net_file).resolve(strict=True), Path(osm_file).resolve(strict=True)
    destination = Path(output_dir).resolve()
    if destination.exists():
        raise ValueError("Choose a new parking model directory.")
    if file_sha256(source) != expected_net_sha256 or file_sha256(osm) != expected_osm_sha256:
        raise ValueError("Parking model input hashes do not match.")
    report = identify_parking_facilities(net_file=source, osm_file=osm)
    tree = ET.parse(source)
    annotations = {}

    def annotate(key, value):
        if key in annotations and annotations[key] != value:
            raise ValueError("Shared parking access requires a separate facility review.")
        annotations[key] = value

    for facility in report["facilities"]:
        if not facility["connection_trace_complete"]:
            continue
        for edge_id in facility["interior_edge_ids"]:
            annotate(("edge", edge_id), (facility["id"], "interior"))
        for lane_id in facility["internal_lane_ids"]:
            annotate(("lane", lane_id), (facility["id"], "movement"))
        for gate in facility["portals"]:
            annotate(("edge", gate["edge_id"]), (facility["id"], gate["role"]))
    for edge in tree.getroot().findall("edge"):
        for element in [edge, *edge.findall("lane")]:
            match = annotations.get((element.tag, element.get("id")))
            if match is None:
                continue
            existing = {p.get("key"): p.get("value") for p in element.findall("param")}
            for key, value in zip((FACILITY_PARAM, ROLE_PARAM), match):
                if key in existing and existing[key] != value:
                    raise ValueError("Existing parking annotations disagree with the source evidence.")
                if key not in existing:
                    ET.SubElement(element, "param", key=key, value=value)
    if file_sha256(source) != expected_net_sha256 or file_sha256(osm) != expected_osm_sha256:
        raise ValueError("An input changed during parking recognition.")
    destination.mkdir(parents=True, exist_ok=False)
    network = destination / "parking-aware.net.xml"
    tree.write(network, encoding="utf-8", xml_declaration=True)
    report = {"schema": "astra.parking-facilities/v1", "status": "review_required" if report["facilities"] else "not_applicable",
        "annotation_status": "pass", **report,
        "source": {"path": str(source), "sha256": expected_net_sha256},
        "osm": {"path": str(osm), "sha256": expected_osm_sha256},
        "network": {"path": network.name, "sha256": file_sha256(network)},
        "implementation_sha256": file_sha256(Path(__file__)),
        "inputs_unchanged": True, "runtime_geometry_changed": False, "promotion_authority": False,
        "claim_boundary": "Facility recognition preserves all roads, lane order, connections, and demand. "
            "Only namespaced metadata is added. Parking spaces, occupancy, dwell times, and clearance enforcement remain unconfigured."}
    report_file = destination / "parking-facilities.json"
    write_json_atomic(report_file, report, sort_keys=True)
    return {**report, "report_file": str(report_file), "network_file": str(network)}
