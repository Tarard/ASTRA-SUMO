from __future__ import annotations

import gzip
import math
import xml.etree.ElementTree as ET
from pathlib import Path

from .schema import BBox, OSMNode, OSMPatch, OSMRelation, OSMWay


def parse_osm_xml(path: Path) -> OSMPatch:
    return parse_osm_xml_bytes(
        path.read_bytes(),
        gzip_compressed=path.suffix.lower() == ".gz",
    )


def parse_osm_xml_bytes(
    source_bytes: bytes,
    *,
    gzip_compressed: bool = False,
) -> OSMPatch:
    """Parse the exact byte snapshot supplied by a caller.

    Byte-oriented parsing lets hash-bound adapters derive evidence from the
    same immutable snapshot whose digest they publish.
    """

    xml_bytes = gzip.decompress(source_bytes) if gzip_compressed else source_bytes
    root = ET.fromstring(xml_bytes)
    return _parse_osm_root(root)


def _parse_osm_root(root: ET.Element) -> OSMPatch:
    bbox = _parse_bbox(root)
    center_lat = (bbox.min_lat + bbox.max_lat) / 2
    center_lon = (bbox.min_lon + bbox.max_lon) / 2

    nodes = {
        element.attrib["id"]: OSMNode(
            id=element.attrib["id"],
            lat=float(element.attrib["lat"]),
            lon=float(element.attrib["lon"]),
            tags=_tags(element),
        )
        for element in root.findall("node")
    }
    for node in nodes.values():
        node.x, node.y = _project_xy(node.lat, node.lon, center_lat, center_lon)

    ways = {
        element.attrib["id"]: OSMWay(
            id=element.attrib["id"],
            node_refs=[nd.attrib["ref"] for nd in element.findall("nd")],
            tags=_tags(element),
        )
        for element in root.findall("way")
    }
    relations = {
        element.attrib["id"]: OSMRelation(
            id=element.attrib["id"],
            members=[
                {
                    "type": member.attrib.get("type", ""),
                    "ref": member.attrib.get("ref", ""),
                    "role": member.attrib.get("role", ""),
                }
                for member in element.findall("member")
            ],
            tags=_tags(element),
        )
        for element in root.findall("relation")
    }
    return OSMPatch(nodes=nodes, ways=ways, relations=relations, bbox=bbox)


def _tags(element: ET.Element) -> dict[str, str]:
    return {tag.attrib["k"]: tag.attrib["v"] for tag in element.findall("tag")}


def _parse_bbox(root: ET.Element) -> BBox:
    bounds = root.find("bounds")
    if bounds is not None:
        return BBox(
            min_lon=float(bounds.attrib["minlon"]),
            min_lat=float(bounds.attrib["minlat"]),
            max_lon=float(bounds.attrib["maxlon"]),
            max_lat=float(bounds.attrib["maxlat"]),
        )

    lats = [float(node.attrib["lat"]) for node in root.findall("node")]
    lons = [float(node.attrib["lon"]) for node in root.findall("node")]
    return BBox(min_lon=min(lons), min_lat=min(lats), max_lon=max(lons), max_lat=max(lats))


def _project_xy(lat: float, lon: float, center_lat: float, center_lon: float) -> tuple[float, float]:
    meters_per_deg_lat = 111_320.0
    meters_per_deg_lon = meters_per_deg_lat * math.cos(math.radians(center_lat))
    return ((lon - center_lon) * meters_per_deg_lon, (lat - center_lat) * meters_per_deg_lat)
