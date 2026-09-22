"""Parse Hamburg's official MAP KML geometry without OSM inference.

The Hamburg traffic-light download contains two complementary MAP assets:
the XML carries the lane/movement semantics and the KML carries explicit
geographic polylines.  This module reads only the named ``MAP`` folder and
keeps all identifiers needed to cross-check the XML before any SUMO network
is materialized.  LISA display geometry is intentionally outside this
contract.
"""

from __future__ import annotations

import hashlib
import math
import re
import xml.etree.ElementTree as ET
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any


HAMBURG_MAP_KML_SCHEMA = "torii.hamburg-map-kml-geometry/v1"

_LANE_NAME = re.compile(r"^Lane\s+(\d+)$")
_CROSSWALK_NAME = re.compile(r"^(?:Crosswalk|Pedestrian way|Bike way)\s+(\d+)$")
_CONNECTION_NAME = re.compile(r"^Con\.\s+(\d+):\s+(\d+)\s+[→>-]+\s+(\d+)$")
_DRIVE_LINE_NAME = re.compile(
    r"^DrvLn\.\s+(?:(A|B)\s+)?(\d+):\s+(\d+)\s+[→>-]+\s+(\d+)$"
)
_POINT_NAME = re.compile(r"^(Lane|Crosswalk|Pedestrian way|Bike way)\s+(\d+)\s+([AB])$")
_MERGE_NAME = re.compile(r"^Lane\s+(\d+)\s+Merge$")

_LANE_ROLES = {
    "#laneStyleIn": "ingress",
    "#laneStyleOut": "egress",
    "#laneStyleInOut": "bidirectional",
}


class HamburgMapKmlError(ValueError):
    """Raised when an official MAP KML cannot be parsed deterministically."""


def parse_hamburg_map_kml(
    path: Path,
    *,
    expected_sha256: str | None = None,
) -> dict[str, Any]:
    """Return hash-bound lane and movement geometry from an official MAP KML.

    Unknown folders are ignored, but every feature in the seven documented
    MAP geometry folders is validated strictly.  This lets future file
    revisions add presentation material without silently changing the
    lane/movement contract.
    """

    source_path = Path(path).expanduser().resolve()
    raw = source_path.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if expected_sha256 is not None and digest != expected_sha256.lower():
        raise HamburgMapKmlError("MAP KML SHA-256 does not match expected_sha256")
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as exc:
        raise HamburgMapKmlError(f"invalid MAP KML XML: {exc}") from exc
    if _local_name(root.tag) != "kml":
        raise HamburgMapKmlError("MAP KML root element must be kml")

    map_folders = [folder for folder in root.iter() if _local_name(folder.tag) == "Folder" and _child_text(folder, "name") == "MAP"]
    if len(map_folders) != 1:
        raise HamburgMapKmlError(f"MAP KML requires exactly one MAP folder; found {len(map_folders)}")
    folders = _direct_named_folders(map_folders[0])
    required = {
        "Base Points",
        "Lanes",
        "Crosswalks",
        "Connections",
        "Drive lines",
        "Points",
        "Merge points",
    }
    missing = sorted(required.difference(folders))
    if missing:
        raise HamburgMapKmlError("MAP KML is missing folders: " + ", ".join(missing))

    base_points = _parse_base_points(folders["Base Points"])
    lanes = _parse_lanes(folders["Lanes"])
    crossing_lanes = _parse_crosswalks(folders["Crosswalks"])
    connections = _parse_connections(folders["Connections"])
    drive_lines = _parse_drive_lines(folders["Drive lines"])
    endpoints = _parse_endpoints(folders["Points"])
    merge_points = _parse_merge_points(folders["Merge points"])

    lane_ids = {item["lane_id"] for item in lanes}
    crossing_lane_ids = {item["lane_id"] for item in crossing_lanes}
    all_lane_ids = lane_ids | crossing_lane_ids
    if lane_ids & crossing_lane_ids:
        raise HamburgMapKmlError("Lanes and Crosswalks folder identifiers overlap")
    _require_unique(lanes, "lane_id", "Lanes-folder lane")
    _require_unique(crossing_lanes, "lane_id", "Crosswalks-folder lane")
    _require_unique(connections, "connection_id", "connection")
    _require_unique(drive_lines, "connection_id", "drive line")

    for item in [*connections, *drive_lines]:
        if item["from_lane_id"] not in all_lane_ids or item["to_lane_id"] not in all_lane_ids:
            raise HamburgMapKmlError(
                f"movement {item['connection_id']} references an unknown lane"
            )
    connection_keys = {
        (item["connection_id"], item["from_lane_id"], item["to_lane_id"])
        for item in connections
    }
    drive_line_keys = {
        (item["connection_id"], item["from_lane_id"], item["to_lane_id"])
        for item in drive_lines
    }
    if connection_keys != drive_line_keys:
        missing_drive_lines = sorted(connection_keys - drive_line_keys)
        extra_drive_lines = sorted(drive_line_keys - connection_keys)
        raise HamburgMapKmlError(
            "connection/drive-line identity mismatch: "
            f"missing={missing_drive_lines}, extra={extra_drive_lines}"
        )

    endpoint_keys = {(item["feature_kind"], item["lane_id"], item["endpoint"]) for item in endpoints}
    expected_endpoint_keys = {
        ("lane", lane_id, endpoint)
        for lane_id in lane_ids
        for endpoint in ("A", "B")
    } | {
        ("crosswalk", lane_id, endpoint)
        for lane_id in crossing_lane_ids
        for endpoint in ("A", "B")
    }
    if endpoint_keys != expected_endpoint_keys:
        raise HamburgMapKmlError(
            "lane endpoint inventory mismatch: "
            f"missing={sorted(expected_endpoint_keys - endpoint_keys)}, "
            f"extra={sorted(endpoint_keys - expected_endpoint_keys)}"
        )
    if any(item["lane_id"] not in lane_ids for item in merge_points):
        raise HamburgMapKmlError("merge point references a non-vehicle lane")

    return {
        "schema": HAMBURG_MAP_KML_SCHEMA,
        "status": "pass",
        "source": {
            "path": str(source_path),
            "sha256": digest,
            "bytes": len(raw),
        },
        "base_points": base_points,
        "lanes": lanes,
        "crossing_lanes": crossing_lanes,
        "connections": connections,
        "drive_lines": drive_lines,
        "endpoints": endpoints,
        "merge_points": merge_points,
        "counts": {
            "base_point_count": len(base_points),
            "lanes_folder_count": len(lanes),
            "crosswalks_folder_count": len(crossing_lanes),
            "connection_count": len(connections),
            "drive_line_count": len(drive_lines),
            "endpoint_count": len(endpoints),
            "merge_point_count": len(merge_points),
        },
        "gates": {
            "source_hash": "pass",
            "map_folder_structure": "pass",
            "lane_identity": "pass",
            "connection_drive_line_identity": "pass",
            "endpoint_completeness": "pass",
        },
        "claim_boundary": (
            "Coordinates, folder membership, and identifiers are transcribed from the official MAP KML. "
            "Vehicle, bicycle, and pedestrian lane types require an exact MAP XML join; no lane width, "
            "legal movement, signal state, or SUMO binding is inferred here."
        ),
    }


def bind_hamburg_map_kml_to_mapem(
    kml_geometry: Mapping[str, Any],
    map_lanes: Sequence[Any],
    map_connections: Sequence[Any],
    *,
    expected_node_id: str | None = None,
) -> dict[str, Any]:
    """Join KML geometry to MAPEM semantics by exact official identifiers."""

    if kml_geometry.get("schema") != HAMBURG_MAP_KML_SCHEMA or kml_geometry.get("status") != "pass":
        raise HamburgMapKmlError("a passing Hamburg MAP KML geometry report is required")
    if not map_lanes:
        raise HamburgMapKmlError("MAPEM lane inventory is empty")
    node_ids = {str(item.node_id) for item in map_lanes}
    node_ids.update(str(item.node_id) for item in map_connections)
    if len(node_ids) != 1:
        raise HamburgMapKmlError(f"MAPEM must contain exactly one node id; found {sorted(node_ids)}")
    node_id = next(iter(node_ids))
    if expected_node_id is not None and node_id != str(expected_node_id):
        raise HamburgMapKmlError(
            f"MAPEM node id {node_id!r} does not match expected node {expected_node_id!r}"
        )

    geometry_rows = [
        *(dict(item, geometry_folder="Lanes") for item in kml_geometry["lanes"]),
        *(dict(item, geometry_folder="Crosswalks") for item in kml_geometry["crossing_lanes"]),
    ]
    geometry_by_id = {str(item["lane_id"]): item for item in geometry_rows}
    map_lane_by_id = {str(item.lane_id): item for item in map_lanes}
    if len(map_lane_by_id) != len(map_lanes):
        raise HamburgMapKmlError("MAPEM contains duplicate lane identifiers")
    if set(geometry_by_id) != set(map_lane_by_id):
        raise HamburgMapKmlError(
            "KML/MAPEM lane identity mismatch: "
            f"missing_geometry={sorted(set(map_lane_by_id) - set(geometry_by_id))}, "
            f"extra_geometry={sorted(set(geometry_by_id) - set(map_lane_by_id))}"
        )

    endpoints_by_key = {
        (str(item["lane_id"]), item["endpoint"]): item["coordinate"]
        for item in kml_geometry["endpoints"]
    }
    merge_by_id: dict[str, list[list[float]]] = {}
    for item in kml_geometry["merge_points"]:
        merge_by_id.setdefault(str(item["lane_id"]), []).append(item["coordinate"])
    bound_lanes = []
    for lane_id in sorted(map_lane_by_id, key=_identifier_sort_key):
        semantic = map_lane_by_id[lane_id]
        geometry = geometry_by_id[lane_id]
        bound_lanes.append(
            {
                "node_id": node_id,
                "lane_id": lane_id,
                "lane_type": str(semantic.lane_type),
                "ingress_approach": str(semantic.ingress_approach),
                "egress_approach": str(semantic.egress_approach),
                "geometry_folder": geometry["geometry_folder"],
                "kml_direction_role": geometry["role"],
                "coordinates": geometry["coordinates"],
                "endpoint_a": endpoints_by_key[(lane_id, "A")],
                "endpoint_b": endpoints_by_key[(lane_id, "B")],
                # Older Hamburg exports have one merge point per lane.  Newer
                # files can legitimately expose multiple merge points for a
                # lane that splits/joins more than once; retain the legacy
                # singular field while preserving the complete geometry.
                "merge_point": (merge_by_id.get(lane_id) or [None])[0],
                "merge_points": merge_by_id.get(lane_id, []),
            }
        )

    kml_connection_by_key = {
        (
            str(item["connection_id"]),
            str(item["from_lane_id"]),
            str(item["to_lane_id"]),
        ): item
        for item in kml_geometry["connections"]
    }
    drive_line_by_key = {
        (
            str(item["connection_id"]),
            str(item["from_lane_id"]),
            str(item["to_lane_id"]),
        ): item
        for item in kml_geometry["drive_lines"]
    }
    map_connection_by_key = {
        (
            str(item.connection_id),
            str(item.ingress_lane_id),
            str(item.egress_lane_id),
        ): item
        for item in map_connections
    }
    if len(map_connection_by_key) != len(map_connections):
        raise HamburgMapKmlError("MAPEM contains duplicate connection identities")
    connection_identity_basis = "connection_id_and_lane_pair"
    connection_matches: list[tuple[tuple[str, str], Any, Mapping[str, Any]]]
    if set(map_connection_by_key) != set(kml_connection_by_key):
        # Hamburg's current MAPEM exports can restart connection numbering for
        # a second IntersectionGeometry (for example a bicycle geometry),
        # while the companion KML numbers the same lane pairs globally.  The
        # lane pair is the semantic movement identity; use it only when both
        # sides contain exactly one row per pair, never as a fuzzy match.
        map_by_lane_pair = {
            (key[1], key[2]): item for key, item in map_connection_by_key.items()
        }
        kml_by_lane_pair = {
            (key[1], key[2]): item for key, item in kml_connection_by_key.items()
        }
        if len(map_by_lane_pair) != len(map_connection_by_key) or len(kml_by_lane_pair) != len(kml_connection_by_key):
            raise HamburgMapKmlError(
                "KML/MAPEM connection identity mismatch and lane-pair fallback is ambiguous: "
                f"missing_geometry={sorted(set(map_connection_by_key) - set(kml_connection_by_key))}, "
                f"extra_geometry={sorted(set(kml_connection_by_key) - set(map_connection_by_key))}"
            )
        if set(map_by_lane_pair) != set(kml_by_lane_pair):
            raise HamburgMapKmlError(
                "KML/MAPEM connection identity mismatch: "
                f"missing_geometry={sorted(set(map_by_lane_pair) - set(kml_by_lane_pair))}, "
                f"extra_geometry={sorted(set(kml_by_lane_pair) - set(map_by_lane_pair))}"
            )
        connection_matches = [
            (pair, map_by_lane_pair[pair], kml_by_lane_pair[pair])
            for pair in sorted(map_by_lane_pair)
        ]
        connection_identity_basis = "unique_ingress_egress_lane_pair"
    else:
        connection_matches = [
            ((key[1], key[2]), map_connection_by_key[key], kml_connection_by_key[key])
            for key in sorted(map_connection_by_key, key=lambda value: _identifier_sort_key(value[0]))
        ]
    bound_connections = []
    for pair, semantic, connection_geometry in connection_matches:
        drive_line_key = (
            str(connection_geometry["connection_id"]),
            str(connection_geometry["from_lane_id"]),
            str(connection_geometry["to_lane_id"]),
        )
        drive_line = drive_line_by_key.get(drive_line_key)
        if drive_line is None and connection_identity_basis == "unique_ingress_egress_lane_pair":
            drive_line = next(
                (
                    item
                    for key, item in drive_line_by_key.items()
                    if (key[1], key[2]) == pair
                ),
                None,
            )
        if drive_line is None:
            raise HamburgMapKmlError(
                "KML drive-line geometry is missing for connection "
                f"{connection_geometry['connection_id']} lane pair {pair}"
            )
        bound_connections.append(
            {
                "node_id": node_id,
                "connection_id": str(semantic.connection_id),
                "mapem_connection_id": str(semantic.connection_id),
                "kml_connection_id": str(connection_geometry["connection_id"]),
                "ingress_lane_id": pair[0],
                "egress_lane_id": pair[1],
                "signal_group": str(semantic.signal_group),
                "maneuver_bits": str(semantic.maneuver_bits),
                "connection_coordinates": connection_geometry["coordinates"],
                "drive_line_variant": drive_line["variant"],
                "drive_line_coordinates": drive_line["coordinates"],
            }
        )

    lane_type_counts: dict[str, int] = {}
    for lane in bound_lanes:
        lane_type_counts[lane["lane_type"]] = lane_type_counts.get(lane["lane_type"], 0) + 1
    return {
        "schema": "torii.hamburg-map-kml-mapem-binding/v1",
        "status": "pass",
        "node_id": node_id,
        "source": dict(kml_geometry["source"]),
        "lanes": bound_lanes,
        "connections": bound_connections,
        "counts": {
            "lane_count": len(bound_lanes),
            "connection_count": len(bound_connections),
            "lane_types": dict(sorted(lane_type_counts.items())),
        },
        "gates": {
            "single_mapem_node": "pass",
            "exact_lane_identity": "pass",
            "exact_connection_identity": "pass" if connection_identity_basis == "connection_id_and_lane_pair" else "not_applicable",
            "connection_lane_pair_identity": "pass",
            "drive_line_geometry": "pass",
        },
        "connection_identity_basis": connection_identity_basis,
        "claim_boundary": (
            "This report binds official KML geometry to official MAPEM lane types and connections by exact ids "
            "when ids are globally aligned, or by a unique exact ingress-lane/egress-lane pair when a Hamburg "
            "MAPEM export restarts connection numbering across geometries. "
            "It does not yet bind those lanes to HH-SIB station segments or SUMO edges."
        ),
    }


def _parse_base_points(folder: ET.Element) -> list[dict[str, Any]]:
    result = []
    for placemark in _direct_placemarks(folder):
        name = _required_name(placemark)
        if name not in {"Base Point", "Secondary Point"}:
            raise HamburgMapKmlError(f"unknown Base Points placemark: {name!r}")
        result.append({"name": name, "coordinate": _point_coordinate(placemark)})
    names = [item["name"] for item in result]
    if len(names) != len(set(names)) or "Base Point" not in names:
        raise HamburgMapKmlError("Base Points must contain one unique Base Point")
    return sorted(result, key=lambda item: item["name"])


def _parse_lanes(folder: ET.Element) -> list[dict[str, Any]]:
    result = []
    for placemark in _direct_placemarks(folder):
        name = _required_name(placemark)
        match = _LANE_NAME.fullmatch(name)
        if match is None:
            raise HamburgMapKmlError(f"invalid lane placemark name: {name!r}")
        style = _child_text(placemark, "styleUrl")
        role = _LANE_ROLES.get(style)
        if role is None:
            raise HamburgMapKmlError(f"lane {match.group(1)} has unknown role style {style!r}")
        result.append(
            {
                "lane_id": int(match.group(1)),
                "role": role,
                "style_url": style,
                "coordinates": _line_coordinates(placemark),
            }
        )
    return sorted(result, key=lambda item: item["lane_id"])


def _parse_crosswalks(folder: ET.Element) -> list[dict[str, Any]]:
    result = []
    for placemark in _direct_placemarks(folder):
        name = _required_name(placemark)
        match = _CROSSWALK_NAME.fullmatch(name)
        if match is None:
            raise HamburgMapKmlError(f"invalid crosswalk placemark name: {name!r}")
        result.append(
            {
                "lane_id": int(match.group(1)),
                "role": "crosswalk",
                "style_url": _child_text(placemark, "styleUrl"),
                "coordinates": _line_coordinates(placemark),
            }
        )
    return sorted(result, key=lambda item: item["lane_id"])


def _parse_connections(folder: ET.Element) -> list[dict[str, Any]]:
    return _parse_movement_folder(folder, drive_lines=False)


def _parse_drive_lines(folder: ET.Element) -> list[dict[str, Any]]:
    return _parse_movement_folder(folder, drive_lines=True)


def _parse_movement_folder(folder: ET.Element, *, drive_lines: bool) -> list[dict[str, Any]]:
    result = []
    pattern = _DRIVE_LINE_NAME if drive_lines else _CONNECTION_NAME
    for placemark in _direct_placemarks(folder):
        name = _required_name(placemark)
        match = pattern.fullmatch(name)
        if match is None:
            kind = "drive-line" if drive_lines else "connection"
            raise HamburgMapKmlError(f"invalid {kind} placemark name: {name!r}")
        if drive_lines:
            variant, connection_id, from_lane, to_lane = match.groups()
        else:
            connection_id, from_lane, to_lane = match.groups()
            variant = None
        result.append(
            {
                "connection_id": int(connection_id),
                "variant": variant,
                "from_lane_id": int(from_lane),
                "to_lane_id": int(to_lane),
                "coordinates": _line_coordinates(placemark),
            }
        )
    return sorted(result, key=lambda item: item["connection_id"])


def _parse_endpoints(folder: ET.Element) -> list[dict[str, Any]]:
    result = []
    for placemark in _direct_placemarks(folder):
        name = _required_name(placemark)
        match = _POINT_NAME.fullmatch(name)
        if match is None:
            raise HamburgMapKmlError(f"invalid endpoint placemark name: {name!r}")
        kind, lane_id, endpoint = match.groups()
        result.append(
            {
                "feature_kind": "lane" if kind == "Lane" else "crosswalk",
                "lane_id": int(lane_id),
                "endpoint": endpoint,
                "coordinate": _point_coordinate(placemark),
            }
        )
    return sorted(result, key=lambda item: (item["feature_kind"], item["lane_id"], item["endpoint"]))


def _parse_merge_points(folder: ET.Element) -> list[dict[str, Any]]:
    result = []
    for placemark in _direct_placemarks(folder):
        name = _required_name(placemark)
        match = _MERGE_NAME.fullmatch(name)
        if match is None:
            raise HamburgMapKmlError(f"invalid merge-point placemark name: {name!r}")
        result.append(
            {
                "lane_id": int(match.group(1)),
                "coordinate": _point_coordinate(placemark),
            }
        )
    return sorted(result, key=lambda item: item["lane_id"])


def _direct_named_folders(parent: ET.Element) -> dict[str, ET.Element]:
    result: dict[str, ET.Element] = {}
    for child in parent:
        if _local_name(child.tag) != "Folder":
            continue
        name = _child_text(child, "name")
        if not name:
            raise HamburgMapKmlError("MAP contains an unnamed folder")
        if name in result:
            raise HamburgMapKmlError(f"MAP contains duplicate folder {name!r}")
        result[name] = child
    return result


def _direct_placemarks(folder: ET.Element) -> list[ET.Element]:
    return [child for child in folder if _local_name(child.tag) == "Placemark"]


def _required_name(element: ET.Element) -> str:
    name = _child_text(element, "name")
    if not name:
        raise HamburgMapKmlError("MAP placemark is missing a name")
    return name


def _child_text(element: ET.Element, local_name: str) -> str:
    for child in element:
        if _local_name(child.tag) == local_name:
            return (child.text or "").strip()
    return ""


def _point_coordinate(placemark: ET.Element) -> list[float]:
    geometries = [item for item in placemark if _local_name(item.tag) == "Point"]
    if len(geometries) != 1:
        raise HamburgMapKmlError(f"{_required_name(placemark)!r} requires exactly one Point")
    coordinates = _geometry_coordinates(geometries[0])
    if len(coordinates) != 1:
        raise HamburgMapKmlError(f"{_required_name(placemark)!r} Point requires one coordinate")
    return coordinates[0]


def _line_coordinates(placemark: ET.Element) -> list[list[float]]:
    geometries = [item for item in placemark if _local_name(item.tag) == "LineString"]
    if len(geometries) != 1:
        raise HamburgMapKmlError(f"{_required_name(placemark)!r} requires exactly one LineString")
    coordinates = _geometry_coordinates(geometries[0])
    if len(coordinates) < 2:
        raise HamburgMapKmlError(f"{_required_name(placemark)!r} LineString requires at least two coordinates")
    return coordinates


def _geometry_coordinates(geometry: ET.Element) -> list[list[float]]:
    coordinate_elements = [item for item in geometry if _local_name(item.tag) == "coordinates"]
    if len(coordinate_elements) != 1:
        raise HamburgMapKmlError("KML geometry requires exactly one coordinates element")
    text = coordinate_elements[0].text or ""
    result: list[list[float]] = []
    for token in text.split():
        values = token.split(",")
        if len(values) not in {2, 3}:
            raise HamburgMapKmlError(f"invalid KML coordinate token: {token!r}")
        try:
            coordinate = [float(value) for value in values]
        except ValueError as exc:
            raise HamburgMapKmlError(f"invalid KML coordinate token: {token!r}") from exc
        if not all(math.isfinite(value) for value in coordinate):
            raise HamburgMapKmlError("KML coordinate must be finite")
        if not -180 <= coordinate[0] <= 180 or not -90 <= coordinate[1] <= 90:
            raise HamburgMapKmlError("KML coordinate is outside longitude/latitude bounds")
        result.append(coordinate)
    return result


def _require_unique(items: Sequence[Mapping[str, Any]], key: str, label: str) -> None:
    values = [item[key] for item in items]
    if len(values) != len(set(values)):
        raise HamburgMapKmlError(f"duplicate {label} identifier")


def _identifier_sort_key(value: str) -> tuple[int, int | str]:
    return (0, int(value)) if value.isdigit() else (1, value)


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


__all__ = [
    "HAMBURG_MAP_KML_SCHEMA",
    "HamburgMapKmlError",
    "bind_hamburg_map_kml_to_mapem",
    "parse_hamburg_map_kml",
]
