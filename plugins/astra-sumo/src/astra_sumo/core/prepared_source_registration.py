"""Register prepared source roads by file identity and unique provenance."""

from collections import Counter
import hashlib
import math
from pathlib import Path
import xml.etree.ElementTree as ET


def _tags(element):
    return {row.get("k"): row.get("v") for row in element.findall("tag")}


def _read(path):
    raw = path.read_bytes()
    root = ET.fromstring(raw)
    if root.tag != "osm":
        raise ValueError("Prepared source registration requires OSM-like XML.")
    result = {}
    for kind in ("node", "way"):
        rows = root.findall(kind)
        result[kind] = {row.get("id"): row for row in rows}
        if None in result[kind] or len(result[kind]) != len(rows):
            raise ValueError("Prepared sources require unique node and way identifiers.")
    return result, {"path": str(path), "sha256": hashlib.sha256(raw).hexdigest()}


def _way_state(way, nodes):
    references = [row.get("ref") for row in way.findall("nd")]
    coordinates, node_tags = [], []
    for reference in references:
        node = nodes[reference]
        longitude, latitude = float(node.get("lon")), float(node.get("lat"))
        if (
            not math.isfinite(longitude)
            or not math.isfinite(latitude)
            or not -180 <= longitude <= 180
            or not -90 <= latitude <= 90
        ):
            raise ValueError("Prepared node coordinates require finite longitude and latitude.")
        coordinates.append([longitude, latitude])
        node_tags.append(_tags(node))
    if len(coordinates) < 2:
        raise ValueError("Prepared road geometry requires at least two nodes.")
    return {"node_ids": references, "coordinates": coordinates, "way_tags": _tags(way), "node_tags": node_tags}


def register_prepared_source_way_geometry(*, before_osm_file, reference_osm_file, external_source_tag):
    """Measure source state changes without inferring road removals or field truth."""
    if not isinstance(external_source_tag, str) or not external_source_tag.strip():
        raise ValueError("Declare the external road provenance tag.")
    paths = [Path(value).resolve(strict=True) for value in (before_osm_file, reference_osm_file)]
    parsed = [_read(path) for path in paths]
    snapshots, identities = [row[0] for row in parsed], [row[1] for row in parsed]
    ways = [row["way"] for row in snapshots]
    counts = [
        Counter(
            _tags(way).get(external_source_tag)
            for way in side.values()
            if _tags(way).get("highway") and _tags(way).get(external_source_tag)
        )
        for side in ways
    ]
    records, unresolved = [], []
    for identity in sorted(ways[0].keys() & ways[1].keys()):
        if not identity.isdigit() or int(identity) <= 0:
            continue
        pair = [side[identity] for side in ways]
        tags = [_tags(way) for way in pair]
        if not all(row.get("highway") for row in tags):
            continue
        provenance = tags[0].get(external_source_tag)
        if (
            not provenance
            or provenance != tags[1].get(external_source_tag)
            or any(count[provenance] != 1 for count in counts)
        ):
            unresolved.append({"way_id": identity, "reason": "missing_changed_or_nonunique_external_source_id"})
            continue
        try:
            before, reference = [_way_state(way, source["node"]) for way, source in zip(pair, snapshots)]
        except (KeyError, TypeError, ValueError) as error:
            unresolved.append({"way_id": identity, "reason": "unresolved_source_geometry", "detail": str(error)})
            continue
        records.append(
            {
                "way_id": identity,
                "external_source_id": provenance,
                "before": before,
                "reference": reference,
                "coordinate_sequence_changed": before["coordinates"] != reference["coordinates"],
                "source_state_unchanged": before == reference,
            }
        )
    return {
        "schema": "astra.prepared-source-way-registration/v1",
        "status": "measured",
        "external_source_tag": external_source_tag,
        "inputs": {name: value for name, value in zip(("before", "reference"), identities)},
        "records": records,
        "unresolved": unresolved,
        "field_validity_date": None,
        "field_defect_labels": False,
        "limitations": [
            "Positive identifiers are file-local, not confirmed global OSM identities.",
            "Geometry or source-state changes do not establish field defects.",
            "Negative editor identifiers and added or removed source IDs do not create road-removal labels.",
        ],
    }
