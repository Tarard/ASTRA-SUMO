from __future__ import annotations

import csv
import gzip
import hashlib
import math
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, NamedTuple
from urllib import error, parse, request
import unicodedata
import xml.etree.ElementTree as ET

from .artifact_io import write_json_atomic
from .command_runner import run_command


DEFAULT_ALLOWED_HIGHWAYS = {
    "trunk",
    "trunk_link",
    "primary",
    "primary_link",
    "secondary",
    "secondary_link",
    "tertiary",
    "tertiary_link",
}


class Bbox(NamedTuple):
    west: float
    south: float
    east: float
    north: float


@dataclass(frozen=True)
class EdgeInfo:
    edge_id: str
    from_node: str
    to_node: str
    name_values: tuple[str, ...]
    allows_passenger: bool


@dataclass(frozen=True)
class KeyEdgeQuery:
    label: str
    role: str
    search_terms: tuple[str, ...]
    reject_terms: tuple[str, ...] = ()
    preferred_edge_id: str = ""


@dataclass(frozen=True)
class KeyEdgeRow:
    label: str
    role: str
    from_hint: str
    to_hint: str
    required_edge_id: str
    notes: str


@dataclass(frozen=True)
class ProbeRoute:
    route_id: str
    vehicle_id: str
    edges: list[str]
    depart: int


def parse_bbox(value: str) -> Bbox:
    parts = [float(item.strip()) for item in value.split(",")]
    if len(parts) != 4:
        raise ValueError("bbox must be west,south,east,north")

    west, south, east, north = parts
    if west >= east:
        raise ValueError("bbox west must be smaller than east")
    if south >= north:
        raise ValueError("bbox south must be smaller than north")
    if not (-180 <= west <= 180 and -180 <= east <= 180):
        raise ValueError("bbox longitude outside valid range")
    if not (-90 <= south <= 90 and -90 <= north <= 90):
        raise ValueError("bbox latitude outside valid range")
    return Bbox(west=west, south=south, east=east, north=north)


def parse_geo_boundary(value: str | None) -> str | None:
    if value is None:
        return None
    boundary = value.strip()
    parts = [part.strip() for part in boundary.split(",")]
    if len(parts) < 6 or len(parts) % 2:
        raise ValueError("geo_boundary must contain at least three lon,lat pairs")
    coordinates = [float(part) for part in parts]
    if not all(math.isfinite(part) for part in coordinates):
        raise ValueError("geo_boundary coordinates must be finite")
    if any(not -180 <= coordinates[index] <= 180 for index in range(0, len(coordinates), 2)):
        raise ValueError("geo_boundary longitude outside valid range")
    if any(not -90 <= coordinates[index] <= 90 for index in range(1, len(coordinates), 2)):
        raise ValueError("geo_boundary latitude outside valid range")
    return boundary


def build_overpass_query(
    bbox: Bbox,
    *,
    timeout: int,
    historical_date: str | None = None,
    include_railway: bool = False,
) -> str:
    overpass_bbox = f"{bbox.south:g},{bbox.west:g},{bbox.north:g},{bbox.east:g}"
    historical_clause = ""
    if historical_date is not None:
        historical_date = historical_date.strip()
        if not historical_date:
            raise ValueError("historical_date must not be blank")
        historical_clause = f'[date:"{historical_date}"]'
    selectors = [f'  way["highway"]({overpass_bbox});']
    if include_railway:
        selectors.append(f'  way["railway"]({overpass_bbox});')
    selectors.append(f'  relation["type"="restriction"]({overpass_bbox});')
    return "\n".join(
        [
            f"[out:xml][timeout:{timeout}]{historical_clause};",
            "(",
            *selectors,
            ");",
            "(._;>;);",
            "out body;",
            "",
        ]
    )


def download_osm(
    query: str,
    *,
    url: str,
    user_agent: str,
    timeout_seconds: float,
) -> bytes:
    data = parse.urlencode({"data": query}).encode("utf-8")
    req = request.Request(url, data=data, headers={"User-Agent": user_agent})
    with request.urlopen(req, timeout=timeout_seconds) as response:
        return response.read()


def split_bbox(bbox: Bbox, *, max_tile_area_km2: float = 2500.0) -> list[Bbox]:
    if max_tile_area_km2 <= 0:
        raise ValueError("max_tile_area_km2 must be positive")
    mean_lat = (bbox.south + bbox.north) / 2.0
    km_per_degree_lat = 111.32
    km_per_degree_lon = max(1e-6, 111.32 * math.cos(math.radians(mean_lat)))
    width_km = (bbox.east - bbox.west) * km_per_degree_lon
    height_km = (bbox.north - bbox.south) * km_per_degree_lat
    area_km2 = width_km * height_km
    if area_km2 <= max_tile_area_km2:
        return [bbox]

    tile_side_km = math.sqrt(max_tile_area_km2)
    columns = max(1, math.ceil(width_km / tile_side_km))
    rows = max(1, math.ceil(height_km / tile_side_km))
    lon_step = (bbox.east - bbox.west) / columns
    lat_step = (bbox.north - bbox.south) / rows
    tiles = []
    for row in range(rows):
        south = bbox.south + row * lat_step
        north = bbox.north if row == rows - 1 else bbox.south + (row + 1) * lat_step
        for column in range(columns):
            west = bbox.west + column * lon_step
            east = bbox.east if column == columns - 1 else bbox.west + (column + 1) * lon_step
            tiles.append(Bbox(west=west, south=south, east=east, north=north))
    return tiles


def merge_osm_xml_payloads(payloads: list[bytes]) -> bytes:
    if not payloads:
        raise ValueError("at least one OSM XML payload is required")

    first_root = ET.fromstring(payloads[0])
    out_root = ET.Element("osm", first_root.attrib)
    seen: dict[str, set[str]] = {"node": set(), "way": set(), "relation": set()}

    for payload in payloads:
        root = ET.fromstring(payload)
        for child in root:
            if child.tag in {"bounds", "bound"}:
                continue
            if child.tag in seen:
                child_id = child.attrib.get("id")
                if child_id is not None:
                    if child_id in seen[child.tag]:
                        continue
                    seen[child.tag].add(child_id)
            out_root.append(child)
    return ET.tostring(out_root, encoding="utf-8", xml_declaration=True)


def robust_download_osm(
    bbox: Bbox,
    *,
    timeout_seconds: float,
    historical_date: str | None,
    overpass_url: str,
    user_agent: str,
    max_tile_area_km2: float = 2500.0,
    max_retries: int = 2,
    retry_pause_seconds: float = 5.0,
    download_func: Callable[..., bytes] = download_osm,
    include_railway: bool = False,
) -> tuple[bytes, dict[str, Any]]:
    if max_retries < 0:
        raise ValueError("max_retries must be non-negative")

    tiles = split_bbox(bbox, max_tile_area_km2=max_tile_area_km2)
    payloads = []
    retry_count = 0
    tile_records = []
    for index, tile in enumerate(tiles, start=1):
        query = build_overpass_query(
            tile,
            timeout=int(timeout_seconds),
            historical_date=historical_date,
            include_railway=include_railway,
        )
        last_error = ""
        for attempt in range(max_retries + 1):
            try:
                payload = download_func(
                    query,
                    url=overpass_url,
                    user_agent=user_agent,
                    timeout_seconds=timeout_seconds + 30,
                )
                payloads.append(payload)
                tile_records.append(
                    {
                        "tile_index": index,
                        "bbox": f"{tile.west:g},{tile.south:g},{tile.east:g},{tile.north:g}",
                        "attempts": attempt + 1,
                        "bytes": len(payload),
                    }
                )
                break
            except Exception as exc:
                last_error = f"{type(exc).__name__}: {exc}"
                if attempt >= max_retries:
                    raise RuntimeError(
                        f"Overpass tile {index}/{len(tiles)} failed after {attempt + 1} attempts: {last_error}"
                    ) from exc
                retry_count += 1
                if retry_pause_seconds > 0:
                    time.sleep(retry_pause_seconds)
    merged = merge_osm_xml_payloads(payloads)
    return merged, {
        "strategy": "tiled-retry-merge",
        "tile_count": len(tiles),
        "retry_count": retry_count,
        "max_tile_area_km2": max_tile_area_km2,
        "max_retries": max_retries,
        "merged_bytes": len(merged),
        "tiles": tile_records,
    }


def _open_xml(path: Path, mode: str):
    if path.suffix == ".gz":
        return gzip.open(path, mode, encoding="utf-8")
    return path.open(mode, encoding="utf-8")


def _write_payload(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.suffix == ".gz":
        path.write_bytes(gzip.compress(payload))
    else:
        path.write_bytes(payload)


def _highway_value(element: ET.Element) -> str | None:
    for tag in element.findall("tag"):
        if tag.attrib.get("k") == "highway":
            return tag.attrib.get("v")
    return None


def _tag_value(element: ET.Element, key: str) -> str | None:
    for tag in element.findall("tag"):
        if tag.attrib.get("k") == key:
            return tag.attrib.get("v")
    return None


def _way_node_refs(way: ET.Element) -> list[str]:
    return [node.attrib["ref"] for node in way.findall("nd")]


def _node_in_bbox(node: ET.Element, bbox: Bbox) -> bool:
    try:
        lon = float(node.attrib.get("lon", ""))
        lat = float(node.attrib.get("lat", ""))
    except ValueError:
        return False
    return bbox.west <= lon <= bbox.east and bbox.south <= lat <= bbox.north


def _way_intersects_bbox(refs: list[str], nodes: dict[str, ET.Element], bbox: Bbox) -> bool:
    """Select by original segments, including crossings with both ends outside."""
    points = []
    for ref in refs:
        try:
            point = tuple(float(nodes[ref].attrib[key]) for key in ("lon", "lat"))
        except (KeyError, ValueError):
            point = None
        points.append(point if point is not None and all(map(math.isfinite, point)) else None)
    for start, end in zip(points, points[1:]):
        if start is None or end is None:
            continue
        lower, upper = 0.0, 1.0
        for a, b, minimum, maximum in zip(start, end, (bbox.west, bbox.south), (bbox.east, bbox.north)):
            if a == b:
                if not minimum <= a <= maximum:
                    break
            else:
                first, last = sorted(((minimum-a)/(b-a), (maximum-a)/(b-a)))
                lower, upper = max(lower, first), min(upper, last)
                if lower > upper:
                    break
        else:
            return True
    return False


def _relation_way_refs(relation: ET.Element) -> set[str]:
    refs = set()
    for member in relation.findall("member"):
        if member.attrib.get("type") == "way" and "ref" in member.attrib:
            refs.add(member.attrib["ref"])
    return refs


def filter_osm_by_highways(
    source: Path,
    target: Path,
    allowed_highways: set[str],
    *,
    bbox: Bbox | None = None,
    allowed_way_ids: set[str] | None = None,
    forced_way_ids: set[str] | None = None,
    include_railway: bool = False,
    allowed_railways: set[str] | None = None,
) -> dict[str, Any]:
    """Select complete source ways; bbox selection never cuts their geometry."""
    from .road_scope import HIGHWAY_CLASS_PRESETS

    with _open_xml(source, "rt") as handle:
        root = ET.parse(handle).getroot()

    nodes_by_id = {node.attrib["id"]: node for node in root.findall("node")}
    bbox_node_refs = None
    if bbox is not None:
        bbox_node_refs = {node.attrib["id"] for node in root.findall("node") if _node_in_bbox(node, bbox)}

    requested_forced_way_ids = set(forced_way_ids or ())
    forced_construction_way_ids = {
        way.attrib.get("id", "")
        for way in root.findall("way")
        if way.attrib.get("id", "") in requested_forced_way_ids
        and _highway_value(way) == "construction"
    }

    kept_ways = []
    kept_way_ids = set()
    kept_node_refs = set()
    dropped_ways = 0
    dropped_ways_outside_bbox = 0
    dropped_ways_outside_reference_scope = 0
    kept_railway_ways = 0
    dropped_vehicle_area_way_ids = []
    for way in root.findall("way"):
        way_id = way.attrib.get("id", "")
        highway = _highway_value(way)
        railway = _tag_value(way, "railway")
        force_way = way_id in requested_forced_way_ids
        keep_railway = include_railway and (
            allowed_railways is None or railway in allowed_railways
        )
        keep_by_category = highway in allowed_highways or keep_railway
        if (
            forced_construction_way_ids
            and highway == "construction"
            and not force_way
        ):
            # A discard=false overlay applies to every construction way in the
            # filtered OSM.  Keep that scope evidence-exact even when a caller
            # also supplied construction in allowed_highways.
            keep_by_category = False
        if force_way or keep_by_category:
            # A drivable surface is not a road along its perimeter. Keep
            # pedestrian areas and independent railway geometry unchanged.
            if (highway in HIGHWAY_CLASS_PRESETS["full_vehicle"]
                    and _tag_value(way, "area") == "yes" and not railway):
                dropped_vehicle_area_way_ids.append(way_id)
                dropped_ways += 1
                continue
            if (
                not force_way
                and allowed_way_ids is not None
                and way_id not in allowed_way_ids
            ):
                dropped_ways_outside_reference_scope += 1
                continue
            refs = _way_node_refs(way)
            if len(refs) < 2 or (bbox is not None and not _way_intersects_bbox(refs, nodes_by_id, bbox)):
                dropped_ways_outside_bbox += 1
                continue
            kept_ways.append(way)
            kept_way_ids.add(way.attrib["id"])
            kept_node_refs.update(refs)
            kept_railway_ways += int(keep_railway and highway is None)
        elif highway is not None:
            dropped_ways += 1

    kept_nodes = [
        node for node in root.findall("node")
        if node.attrib.get("id") in kept_node_refs
    ]

    kept_relations = []
    for relation in root.findall("relation"):
        tags = {tag.attrib.get("k"): tag.attrib.get("v") for tag in relation.findall("tag")}
        if tags.get("type") == "restriction" and _relation_way_refs(relation) & kept_way_ids:
            kept_relations.append(relation)

    out_root = ET.Element("osm", root.attrib)
    for child in root:
        if child.tag in {"bounds", "bound"}:
            out_root.append(child)
    for node in kept_nodes:
        out_root.append(node)
    for way in kept_ways:
        out_root.append(way)
    for relation in kept_relations:
        out_root.append(relation)

    target.parent.mkdir(parents=True, exist_ok=True)
    payload = ET.tostring(out_root, encoding="utf-8", xml_declaration=True)
    if target.suffix == ".gz":
        target.write_bytes(gzip.compress(payload))
    else:
        target.write_bytes(payload)

    stats = {
        "kept_nodes": len(kept_nodes),
        "kept_ways": len(kept_ways),
        "dropped_ways": dropped_ways,
        "kept_relations": len(kept_relations),
    }
    if dropped_vehicle_area_way_ids:
        stats["dropped_vehicle_area_way_ids"] = sorted(dropped_vehicle_area_way_ids)
    if bbox is not None:
        stats.update(
            {
                "bbox_selection_mode": "whole_ways_intersecting_bbox",
                "geometry_clipped": False,
                "trimmed_ways": 0,
                "dropped_ways_outside_bbox": dropped_ways_outside_bbox,
                "dropped_nodes_outside_bbox": 0,
                "retained_nodes_outside_bbox": len(kept_node_refs - bbox_node_refs),
            }
        )
    if allowed_way_ids is not None:
        stats["dropped_ways_outside_reference_scope"] = dropped_ways_outside_reference_scope
    if forced_way_ids is not None:
        kept_forced_way_ids = requested_forced_way_ids & kept_way_ids
        stats.update(
            {
                "forced_way_ids_requested": sorted(requested_forced_way_ids),
                "forced_way_ids_kept": sorted(kept_forced_way_ids),
                "forced_way_ids_missing": sorted(
                    requested_forced_way_ids - kept_forced_way_ids
                ),
                "forced_construction_way_ids_kept": sorted(
                    forced_construction_way_ids & kept_way_ids
                ),
            }
        )
    if include_railway:
        stats["kept_railway_ways"] = kept_railway_ways
    return stats


def _result_to_dict(result: Any) -> dict[str, Any]:
    if hasattr(result, "to_dict"):
        return result.to_dict()
    if isinstance(result, Mapping):
        return dict(result)
    return {
        "status": "fail",
        "returncode": None,
        "stdout": "",
        "stderr": "",
        "error": f"unexpected command result type: {type(result).__name__}",
    }


def _relative_to_root(path: Path, root: Path) -> str:
    return path.resolve().relative_to(root.resolve()).as_posix()


def _command_path(path: Path, root: Path) -> str:
    try:
        return _relative_to_root(path, root)
    except ValueError:
        return str(path)


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _origin_name_output_summary(net_file: Path) -> dict[str, Any]:
    """Inspect only explicit output metadata; never infer an OSM edge lineage."""

    summary: dict[str, Any] = {
        "requested": True,
        "netconvert_option": OSM_ORIGINAL_NAME_OUTPUT_OPTION,
        "expected_parameter_keys": list(OSM_ORIGINAL_NAME_OUTPUT_PARAMETER_KEYS),
        "status": "blocked",
        "road_edge_count": 0,
        "road_edge_with_observed_orig_id_count": 0,
        "observed_orig_id_parameter_count": 0,
        "observed_orig_id_token_count": 0,
        "claim_boundary": (
            "An origId/origID parameter is observed output metadata only. It does not prove "
            "direction, lane, legal-turn, or traffic-light correspondence."
        ),
    }
    if not net_file.is_file():
        summary["reason"] = "sumo_net_not_created"
        return summary
    try:
        root = ET.parse(net_file).getroot()
    except (OSError, ET.ParseError) as exc:
        summary["reason"] = f"sumo_net_not_parseable:{type(exc).__name__}"
        return summary

    observed_tokens: set[str] = set()
    for edge in root.findall("edge"):
        edge_id = edge.attrib.get("id", "")
        function = edge.attrib.get("function", "").strip().casefold()
        if edge_id.startswith(":") or function not in {"", "normal"}:
            continue
        summary["road_edge_count"] += 1
        edge_has_observed_origin = False
        for container in (edge, *edge.findall("lane")):
            for parameter in container.findall("param"):
                if parameter.attrib.get("key", "").casefold() != "origid":
                    continue
                raw_value = parameter.attrib.get("value", "").strip()
                if not raw_value:
                    continue
                edge_has_observed_origin = True
                summary["observed_orig_id_parameter_count"] += 1
                observed_tokens.update(
                    token
                    for token in raw_value.replace(",", " ").replace(";", " ").split()
                    if token
                )
        if edge_has_observed_origin:
            summary["road_edge_with_observed_orig_id_count"] += 1
    summary["observed_orig_id_token_count"] = len(observed_tokens)
    road_edge_count = int(summary["road_edge_count"])
    observed_edge_count = int(summary["road_edge_with_observed_orig_id_count"])
    if road_edge_count == 0:
        summary["status"] = "not_applicable"
        summary["reason"] = "no_external_road_edges"
    elif observed_edge_count == 0:
        summary["status"] = "review_required"
        summary["reason"] = "no_observed_orig_id_parameters"
    elif observed_edge_count < road_edge_count:
        summary["status"] = "review_required"
        summary["reason"] = "partial_observed_orig_id_coverage"
    else:
        summary["status"] = "pass"
        summary["reason"] = "all_external_road_edges_have_observed_orig_id"
    return summary


def _failure(error_message: str, *, artifacts: dict[str, Any] | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "status": "fail",
        "claim_status": "construction-invalid",
        "error": error_message,
    }
    if artifacts:
        payload.update(artifacts)
    return payload


REFERENCE_VISUAL_PROFILES = {"reference_visual_detail", "reference_matched_visual_detail"}
REFERENCE_VISUAL_SUMO_TYPEMAPS = (
    "osmNetconvert.typ.xml",
    "osmNetconvertBicycle.typ.xml",
    "osmNetconvertPedestrians.typ.xml",
)
OSM_BASE_SUMO_TYPEMAP = "osmNetconvert.typ.xml"
OSM_CONSTRUCTION_TYPE_ID = "highway.construction"
OSM_ORIGINAL_NAME_OUTPUT_OPTION = "--output.original-names"
OSM_ORIGINAL_NAME_OUTPUT_PARAMETER_KEYS = ("origId", "origID")
OSM_BUILD_PROVENANCE_SCHEMA = "torii.osm-sumo-build-provenance/v1"
REFERENCE_VISUAL_SERVICE_TYPE_XML = """<types xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xsi:noNamespaceSchemaLocation="http://sumo.dlr.de/xsd/types_file.xsd">
    <type id="highway.service" numLanes="1" speed="5.56" priority="1" oneway="false" disallow="pedestrian tram rail_urban rail rail_electric rail_fast ship cable_car subway"/>
    <type id="highway.service|psv" numLanes="1" speed="13.89" priority="1" oneway="false" allow="bus coach"/>
    <type id="highway.service|bus" numLanes="1" speed="13.89" priority="1" oneway="false" allow="bus coach"/>
</types>
"""


def _reference_visual_type_options(
    root: Path,
    logs_dir: Path,
    prefix: str,
    *,
    explicit_sumo_home: Path | None = None,
) -> tuple[list[str], list[str]]:
    sumo_home_value = (
        str(explicit_sumo_home.resolve())
        if explicit_sumo_home is not None
        else os.environ.get("SUMO_HOME", "").strip()
    )
    if not sumo_home_value:
        return [], ["SUMO_HOME is not set; reference visual service typemap overlay skipped"]
    typemap_dir = Path(sumo_home_value) / "data" / "typemap"
    base_type_files = [typemap_dir / name for name in REFERENCE_VISUAL_SUMO_TYPEMAPS]
    missing = [str(path) for path in base_type_files if not path.exists()]
    if missing:
        return [], ["SUMO typemap files missing; reference visual service typemap overlay skipped: " + ", ".join(missing)]
    service_type_file = logs_dir / f"{prefix}_service_no_pedestrian.typ.xml"
    _write_text(service_type_file, REFERENCE_VISUAL_SERVICE_TYPE_XML)
    type_files = [*base_type_files, service_type_file]
    return ["--type-files", ",".join(_command_path(path, root) for path in type_files)], []


def _forced_construction_type_options(
    root: Path,
    logs_dir: Path,
    prefix: str,
    *,
    explicit_sumo_home: Path | None = None,
) -> tuple[list[str], Path]:
    sumo_home_value = (
        str(explicit_sumo_home.resolve())
        if explicit_sumo_home is not None
        else os.environ.get("SUMO_HOME", "").strip()
    )
    if not sumo_home_value:
        raise ValueError(
            "SUMO_HOME is not set; forced highway=construction import requires "
            "the official SUMO OSM typemap"
        )

    base_type_file = (
        Path(sumo_home_value) / "data" / "typemap" / OSM_BASE_SUMO_TYPEMAP
    )
    if not base_type_file.is_file():
        raise ValueError(
            "SUMO official OSM typemap is missing for forced "
            f"highway=construction import: {base_type_file}"
        )
    try:
        base_root = ET.parse(base_type_file).getroot()
    except (OSError, ET.ParseError) as exc:
        raise ValueError(
            "SUMO official OSM typemap is not parseable for forced "
            f"highway=construction import: {base_type_file}: {exc}"
        ) from exc

    construction_types = [
        element
        for element in base_root.findall(".//type")
        if element.attrib.get("id") == OSM_CONSTRUCTION_TYPE_ID
    ]
    if len(construction_types) != 1:
        raise ValueError(
            "SUMO official OSM typemap must define exactly one "
            f"{OSM_CONSTRUCTION_TYPE_ID!r} type: {base_type_file}"
        )

    construction_attributes = dict(construction_types[0].attrib)
    construction_attributes["discard"] = "false"
    overlay_root = ET.Element("types")
    ET.SubElement(overlay_root, "type", construction_attributes)
    overlay_file = logs_dir / f"{prefix}_forced_construction.typ.xml"
    overlay_payload = ET.tostring(
        overlay_root,
        encoding="unicode",
        xml_declaration=True,
    )
    _write_text(overlay_file, overlay_payload)
    type_files = [base_type_file, overlay_file]
    return [
        "--type-files",
        ",".join(_command_path(path, root) for path in type_files),
    ], overlay_file


def _merge_type_file_options(*options: list[str]) -> list[str]:
    type_files: list[str] = []
    for option in options:
        if not option:
            continue
        if len(option) != 2 or option[0] != "--type-files":
            raise ValueError(f"unexpected netconvert type options: {option}")
        for type_file in option[1].split(","):
            if type_file not in type_files:
                type_files.append(type_file)
    if not type_files:
        return []
    return ["--type-files", ",".join(type_files)]


def _netconvert_profile_options(profile: str | None) -> list[str]:
    normalized = (profile or "vehicle_core").strip().lower().replace("-", "_")
    if normalized in {"", "default", "vehicle", "vehicle_core"}:
        return []
    if normalized in REFERENCE_VISUAL_PROFILES:
        return [
            "--osm.bike-access",
            "--osm.sidewalks",
            "--osm.crossings",
            "--osm.turn-lanes",
            "--sidewalks.guess.from-permissions",
            "--crossings.guess",
            "--walkingareas",
            "--tls.guess",
            "--tls.guess-signals",
            "--tls.rebuild",
            "--tls.default-type",
            "actuated",
        ]
    raise ValueError(f"unsupported netconvert_profile: {profile}")


def _normalized_netconvert_profile(profile: str | None) -> str:
    normalized = (profile or "vehicle_core").strip().lower().replace("-", "_")
    if normalized in {"", "default", "vehicle"}:
        return "vehicle_core"
    return normalized


def _traffic_side_options(traffic_side: str) -> tuple[str, list[str]]:
    normalized = traffic_side.strip().lower().replace("_", "-")
    if normalized in {"right", "right-hand", "rht"}:
        return "right", []
    if normalized in {"left", "left-hand", "lht"}:
        return "left", ["--lefthand"]
    raise ValueError("traffic_side must be explicitly 'right' or 'left'")


def build_osm_network(
    *,
    bbox: str,
    geo_boundary: str | None = None,
    tls_join_distance_m: float = 35.0,
    output_dir: Path,
    prefix: str = "sumo_osm_network",
    source_osm_path: Path | None = None,
    allowed_highways: set[str] | None = None,
    historical_date: str | None = None,
    overpass_url: str = "https://overpass-api.de/api/interpreter",
    user_agent: str = "Torii local OSM builder",
    timeout_seconds: float = 240.0,
    max_tile_area_km2: float = 2500.0,
    max_retries: int = 2,
    retry_pause_seconds: float = 5.0,
    command_runner: Callable[..., Any] = run_command,
    download_func: Callable[..., bytes] = download_osm,
    netconvert_profile: str | None = "vehicle_core",
    allowed_way_ids: set[str] | None = None,
    forced_way_ids: set[str] | None = None,
    include_railway: bool = False,
    allowed_railways: set[str] | None = None,
    netconvert_binary: str = "netconvert",
    clip_source_ways_to_bbox: bool = True,
    sumo_home: Path | None = None,
    traffic_side: str = "right",
) -> dict[str, Any]:
    """Build from selected source ways, preserving every selected way's geometry.

    The legacy clip_source_ways_to_bbox option selects whole intersecting ways;
    it does not geometrically clip them at the rectangle boundary.
    """
    try:
        parsed_bbox = parse_bbox(bbox)
        parsed_geo_boundary = parse_geo_boundary(geo_boundary)
        if not math.isfinite(tls_join_distance_m) or tls_join_distance_m < 0:
            raise ValueError("tls_join_distance_m must be finite and non-negative")
        query = build_overpass_query(
            parsed_bbox,
            timeout=int(timeout_seconds),
            historical_date=historical_date,
            include_railway=include_railway,
        )
        profile_options = _netconvert_profile_options(netconvert_profile)
        normalized_profile = _normalized_netconvert_profile(netconvert_profile)
        normalized_traffic_side, traffic_side_options = _traffic_side_options(
            traffic_side
        )
    except ValueError as exc:
        return _failure(str(exc))

    allowed = allowed_highways if allowed_highways is not None else DEFAULT_ALLOWED_HIGHWAYS
    root = output_dir
    osm_dir = root / "osm"
    sumo_dir = root / "sumo"
    logs_dir = root / "logs"
    raw_osm = osm_dir / f"{prefix}_bbox.osm.xml.gz"
    filtered_osm = osm_dir / f"{prefix}_filtered.osm.xml.gz"
    net_file = sumo_dir / f"{prefix}.net.xml"
    query_file = logs_dir / f"{prefix}_overpass_query.ql"
    command_record = logs_dir / f"{prefix}_commands.txt"
    netconvert_log = logs_dir / f"{prefix}_netconvert.log"
    build_manifest_file = logs_dir / f"{prefix}_build_manifest.json"
    root.mkdir(parents=True, exist_ok=True)
    sumo_dir.mkdir(parents=True, exist_ok=True)
    _write_text(query_file, query)
    overpass_report: dict[str, Any] | None = None

    try:
        if source_osm_path is None:
            payload, overpass_report = robust_download_osm(
                parsed_bbox,
                timeout_seconds=timeout_seconds,
                historical_date=historical_date,
                overpass_url=overpass_url,
                user_agent=user_agent,
                max_tile_area_km2=max_tile_area_km2,
                max_retries=max_retries,
                retry_pause_seconds=retry_pause_seconds,
                download_func=download_func,
                include_railway=include_railway,
            )
            _write_payload(raw_osm, payload)
            source_osm = raw_osm
        else:
            source_osm = Path(source_osm_path)
            if not source_osm.exists():
                return _failure(
                    f"source OSM file not found: {source_osm}",
                    artifacts={"query_file": str(query_file)},
                )

        filter_stats = filter_osm_by_highways(
            source_osm,
            filtered_osm,
            allowed,
            bbox=(
                parsed_bbox
                if clip_source_ways_to_bbox or forced_way_ids is not None
                else None
            ),
            allowed_way_ids=allowed_way_ids,
            forced_way_ids=forced_way_ids,
            include_railway=include_railway,
            allowed_railways=allowed_railways,
        )
        source_osm_sha256 = _file_sha256(source_osm)
        filtered_osm_sha256 = _file_sha256(filtered_osm)
        forced_way_report = (
            {
                key: filter_stats[key]
                for key in (
                    "forced_way_ids_requested",
                    "forced_way_ids_kept",
                    "forced_way_ids_missing",
                    "forced_construction_way_ids_kept",
                )
            }
            if forced_way_ids is not None
            else {}
        )
        missing_forced_way_ids = forced_way_report.get(
            "forced_way_ids_missing", []
        )
        if missing_forced_way_ids:
            return _failure(
                "forced OSM way ids were not kept inside the requested bbox: "
                + ", ".join(missing_forced_way_ids),
                artifacts={
                    "source_osm_file": str(source_osm),
                    "filtered_osm_file": str(filtered_osm),
                    "query_file": str(query_file),
                    "command_record": str(command_record),
                    "filter_stats": filter_stats,
                    **forced_way_report,
                },
            )
        turnaround_options = (
            []
            if normalized_profile in REFERENCE_VISUAL_PROFILES
            else ["--no-turnarounds"]
        )
        type_options, type_warnings = (
            _reference_visual_type_options(
                root,
                logs_dir,
                prefix,
                explicit_sumo_home=sumo_home,
            )
            if normalized_profile in REFERENCE_VISUAL_PROFILES
            else ([], [])
        )
        forced_construction_overlay_file: Path | None = None
        if forced_way_report.get("forced_construction_way_ids_kept"):
            try:
                construction_type_options, forced_construction_overlay_file = (
                    _forced_construction_type_options(
                        root,
                        logs_dir,
                        prefix,
                        explicit_sumo_home=sumo_home,
                    )
                )
            except ValueError as exc:
                return _failure(
                    str(exc),
                    artifacts={
                        "source_osm_file": str(source_osm),
                        "filtered_osm_file": str(filtered_osm),
                        "query_file": str(query_file),
                        "command_record": str(command_record),
                        "filter_stats": filter_stats,
                        **forced_way_report,
                    },
                )
            type_options = _merge_type_file_options(
                type_options,
                construction_type_options,
            )
        command = [
            netconvert_binary,
            "--osm-files",
            _relative_to_root(filtered_osm, root),
            "--output-file",
            _relative_to_root(net_file, root),
            "--proj.utm",
            "--junctions.internal-link-detail",
            "25",
            *traffic_side_options,
            *turnaround_options,
            "--osm.all-attributes",
            OSM_ORIGINAL_NAME_OUTPUT_OPTION,
            "--tls.join",
            "--tls.join-dist",
            f"{tls_join_distance_m:g}",
            *type_options,
            *profile_options,
            *(
                ["--keep-edges.in-geo-boundary", parsed_geo_boundary]
                if parsed_geo_boundary is not None
                else []
            ),
            "--verbose",
        ]
        _write_text(
            command_record,
            "\n".join(
                [
                    f"bbox={bbox}",
                    f"geo_boundary={parsed_geo_boundary or ''}",
                    f"tls_join_distance_m={tls_join_distance_m:g}",
                    f"source_osm={source_osm}",
                    f"source_osm_sha256={source_osm_sha256}",
                    f"filtered_osm={filtered_osm}",
                    f"filtered_osm_sha256={filtered_osm_sha256}",
                    f"net_file={net_file}",
                    "allowed_highways=" + ",".join(sorted(allowed)),
                    "allowed_way_ids_count="
                    + ("not_applied" if allowed_way_ids is None else str(len(allowed_way_ids))),
                    *(
                        [
                            "forced_way_ids_requested="
                            + ",".join(
                                forced_way_report["forced_way_ids_requested"]
                            ),
                            "forced_way_ids_kept="
                            + ",".join(forced_way_report["forced_way_ids_kept"]),
                            "forced_way_ids_missing="
                            + ",".join(
                                forced_way_report["forced_way_ids_missing"]
                            ),
                            "forced_construction_way_ids_kept="
                            + ",".join(
                                forced_way_report[
                                    "forced_construction_way_ids_kept"
                                ]
                            ),
                        ]
                        if forced_way_ids is not None
                        else []
                    ),
                    f"include_railway={include_railway}",
                    f"clip_source_ways_to_bbox={clip_source_ways_to_bbox}",
                    f"bbox_selection_mode={filter_stats.get('bbox_selection_mode', 'unrestricted')}",
                    f"traffic_side={normalized_traffic_side}",
                    "allowed_railways="
                    + ("all" if allowed_railways is None else ",".join(sorted(allowed_railways))),
                    f"overpass_strategy={overpass_report['strategy'] if overpass_report else 'source-osm'}",
                    f"overpass_tile_count={overpass_report['tile_count'] if overpass_report else 0}",
                    f"overpass_retry_count={overpass_report['retry_count'] if overpass_report else 0}",
                    f"netconvert_profile={normalized_profile}",
                    "netconvert_profile_options=" + " ".join(profile_options),
                    "netconvert_type_options=" + " ".join(type_options),
                    "netconvert_output_original_names_requested=true",
                    "netconvert_output_original_names_option="
                    + OSM_ORIGINAL_NAME_OUTPUT_OPTION,
                    "netconvert_output_original_names_expected_parameters="
                    + ",".join(OSM_ORIGINAL_NAME_OUTPUT_PARAMETER_KEYS),
                    "netconvert_command=" + " ".join(command),
                    "",
                ]
            ),
        )
        result = _result_to_dict(
            command_runner(command, cwd=root, timeout_seconds=timeout_seconds)
        )
        _write_text(
            netconvert_log,
            "\n".join(
                [
                    result.get("stdout", "") or "",
                    result.get("stderr", "") or "",
                    result.get("error", "") or "",
                ]
            ),
        )
        from .osm_access import correct_osm_access_permissions

        access_correction = (
            correct_osm_access_permissions(
                osm_file=filtered_osm,
                net_file=net_file,
                output_dir=logs_dir / f"{prefix}_osm_access",
                netconvert_binary=netconvert_binary,
                timeout_seconds=timeout_seconds,
                command_runner=command_runner,
            )
            if result.get("status") == "pass" and net_file.is_file()
            else {"status": "not_run", "changed_lane_count": 0}
        )
    except (OSError, ET.ParseError, error.URLError, error.HTTPError, RuntimeError, ValueError) as exc:
        return _failure(
            f"{type(exc).__name__}: {exc}",
            artifacts={"query_file": str(query_file), "command_record": str(command_record)},
        )

    status = "pass" if result.get("status") == "pass" and net_file.exists() and access_correction["status"] != "blocked" else "fail"
    warnings = list(type_warnings)
    if not net_file.exists():
        warnings.append(f"net file was not created: {net_file}")
    origin_name_output = _origin_name_output_summary(net_file)
    build_manifest = {
        "schema": OSM_BUILD_PROVENANCE_SCHEMA,
        "status": status,
        "claim_status": "diagnostic-demo" if status == "pass" else "construction-invalid",
        "build_scope": {
            "bbox": bbox,
            "geo_boundary": parsed_geo_boundary,
            "tls_join_distance_m": tls_join_distance_m,
            "clip_source_ways_to_bbox": clip_source_ways_to_bbox,
            "bbox_selection_mode": filter_stats.get("bbox_selection_mode", "unrestricted"),
            "road_classes": sorted(allowed),
            "allowed_way_ids_count": None if allowed_way_ids is None else len(allowed_way_ids),
            "include_railway": include_railway,
            "traffic_side": normalized_traffic_side,
        },
        "source_osm_snapshot": {
            "path": str(source_osm.resolve()),
            "sha256": source_osm_sha256,
        },
        "netconvert_input_osm_snapshot": {
            "path": str(filtered_osm.resolve()),
            "sha256": filtered_osm_sha256,
            "derivation": "filtered_from_source_osm_by_declared_build_scope",
        },
        "sumo_road_snapshot_import_contract": {
            "imported_from": "osm",
            "imported_source_sha256": filtered_osm_sha256,
            "source_snapshot_path": str(filtered_osm.resolve()),
            "status": "pass",
            "claim_boundary": (
                "This declares the exact filtered OSM bytes consumed by netconvert. "
                "It must be combined with parsed origId/origID output evidence before "
                "a specific SUMO edge is treated as observed OSM lineage."
            ),
        },
        "sumo_net_snapshot": {
            "path": str(net_file.resolve()),
            "sha256": _file_sha256(net_file) if net_file.is_file() else None,
        },
        "netconvert": {
            "command": command,
            "result": result,
            "profile": normalized_profile,
            "profile_options": profile_options,
            "type_options": type_options,
            "output_original_names": origin_name_output,
        },
        "osm_access_correction": access_correction,
        "artifacts": {
            "command_record": str(command_record.resolve()),
            "netconvert_log": str(netconvert_log.resolve()),
        },
        "claim_boundary": (
            "The original-name option requests explicit source-ID metadata in SUMO output. "
            "It does not establish complete OSM-to-SUMO conflation or authorize topology, "
            "lane, movement, or signal changes."
        ),
    }
    try:
        write_json_atomic(build_manifest_file, build_manifest, sort_keys=True)
    except OSError as exc:
        return _failure(
            f"could not write OSM-to-SUMO build provenance manifest: {exc}",
            artifacts={
                "source_osm_file": str(source_osm),
                "filtered_osm_file": str(filtered_osm),
                "net_file": str(net_file),
                "command_record": str(command_record),
                "netconvert_log": str(netconvert_log),
            },
        )
    return {
        "status": status,
        "claim_status": "diagnostic-demo" if status == "pass" else "construction-invalid",
        "bbox": bbox,
        "geo_boundary": parsed_geo_boundary,
        "tls_join_distance_m": tls_join_distance_m,
        "road_classes": sorted(allowed),
        "allowed_way_ids_count": None if allowed_way_ids is None else len(allowed_way_ids),
        **forced_way_report,
        "include_railway": include_railway,
        "clip_source_ways_to_bbox": clip_source_ways_to_bbox,
        "bbox_selection_mode": filter_stats.get("bbox_selection_mode", "unrestricted"),
        "traffic_side": normalized_traffic_side,
        "allowed_railways": None if allowed_railways is None else sorted(allowed_railways),
        "source_osm_file": str(source_osm),
        "filtered_osm_file": str(filtered_osm),
        "net_file": str(net_file),
        "query_file": str(query_file),
        "command_record": str(command_record),
        "netconvert_log": str(netconvert_log),
        "build_manifest_file": str(build_manifest_file),
        "build_manifest_sha256": _file_sha256(build_manifest_file),
        "sumo_road_snapshot_import_contract": build_manifest[
            "sumo_road_snapshot_import_contract"
        ],
        "filter_stats": filter_stats,
        "overpass": overpass_report,
        "netconvert_profile": normalized_profile,
        "netconvert_profile_options": profile_options,
        "netconvert_output_original_names": origin_name_output,
        "osm_access_correction": access_correction,
        **(
            {
                "forced_construction_type_overlay_file": str(
                    forced_construction_overlay_file
                )
            }
            if forced_construction_overlay_file is not None
            else {}
        ),
        "netconvert": result,
        "warnings": warnings,
    }


def google_maps_url(lat: float, lon: float) -> str:
    return f"https://www.google.com/maps/search/?api=1&query={lat:.6f},{lon:.6f}"


def is_mainland_china_coordinate(lat: float, lon: float) -> bool:
    if not (18.0 <= lat <= 54.0 and 73.0 <= lon <= 135.5):
        return False
    if 21.7 <= lat <= 22.7 and 113.4 <= lon <= 114.7:
        return False
    if 21.5 <= lat <= 25.7 and 119.0 <= lon <= 122.5:
        return False
    return True


def _gcj02_transform_lat(x: float, y: float) -> float:
    ret = -100.0 + 2.0 * x + 3.0 * y + 0.2 * y * y + 0.1 * x * y + 0.2 * math.sqrt(abs(x))
    ret += (20.0 * math.sin(6.0 * x * math.pi) + 20.0 * math.sin(2.0 * x * math.pi)) * 2.0 / 3.0
    ret += (20.0 * math.sin(y * math.pi) + 40.0 * math.sin(y / 3.0 * math.pi)) * 2.0 / 3.0
    ret += (160.0 * math.sin(y / 12.0 * math.pi) + 320 * math.sin(y * math.pi / 30.0)) * 2.0 / 3.0
    return ret


def _gcj02_transform_lon(x: float, y: float) -> float:
    ret = 300.0 + x + 2.0 * y + 0.1 * x * x + 0.1 * x * y + 0.1 * math.sqrt(abs(x))
    ret += (20.0 * math.sin(6.0 * x * math.pi) + 20.0 * math.sin(2.0 * x * math.pi)) * 2.0 / 3.0
    ret += (20.0 * math.sin(x * math.pi) + 40.0 * math.sin(x / 3.0 * math.pi)) * 2.0 / 3.0
    ret += (150.0 * math.sin(x / 12.0 * math.pi) + 300.0 * math.sin(x / 30.0 * math.pi)) * 2.0 / 3.0
    return ret


def wgs84_to_gcj02(lat: float, lon: float) -> tuple[float, float]:
    if not is_mainland_china_coordinate(lat, lon):
        return lat, lon
    semi_major_axis = 6378245.0
    eccentricity_squared = 0.00669342162296594323
    dlat = _gcj02_transform_lat(lon - 105.0, lat - 35.0)
    dlon = _gcj02_transform_lon(lon - 105.0, lat - 35.0)
    radlat = lat / 180.0 * math.pi
    magic = math.sin(radlat)
    magic = 1 - eccentricity_squared * magic * magic
    sqrt_magic = math.sqrt(magic)
    dlat = (dlat * 180.0) / ((semi_major_axis * (1 - eccentricity_squared)) / (magic * sqrt_magic) * math.pi)
    dlon = (dlon * 180.0) / (semi_major_axis / sqrt_magic * math.cos(radlat) * math.pi)
    return lat + dlat, lon + dlon


def amap_marker_url(lat: float, lon: float, *, label: str = "SUMO TLS candidate") -> str:
    marker_lat, marker_lon = wgs84_to_gcj02(lat, lon)
    encoded_label = parse.quote_plus(label)
    return f"https://uri.amap.com/marker?position={marker_lon:.6f},{marker_lat:.6f}&name={encoded_label}"


def regional_map_fields(lat: float, lon: float, *, label: str = "SUMO TLS candidate") -> dict[str, str]:
    if is_mainland_china_coordinate(lat, lon):
        return {
            "regional_map_provider": "Amap/Gaode",
            "regional_map_url": amap_marker_url(lat, lon, label=label),
            "regional_map_coordinate_system": "GCJ-02",
            "regional_map_source_coordinate_system": "WGS84",
            "regional_map_audit_status": "needs_amap_review",
            "regional_map_note": "Generated from WGS84 SUMO/OSM coordinates converted to GCJ-02 for Amap/Gaode visual review.",
        }
    return {
        "regional_map_provider": "Google Maps",
        "regional_map_url": google_maps_url(lat, lon),
        "regional_map_coordinate_system": "WGS84",
        "regional_map_source_coordinate_system": "WGS84",
        "regional_map_audit_status": "needs_google_review",
        "regional_map_note": "Use Google Maps only where it is reliable for the modeled region and time scope.",
    }


def regional_map_baseline_for_bbox(bbox: str, *, label: str = "SUMO network area") -> dict[str, str]:
    parsed = parse_bbox(bbox)
    center_lat = (parsed.south + parsed.north) / 2.0
    center_lon = (parsed.west + parsed.east) / 2.0
    fields = regional_map_fields(center_lat, center_lon, label=label)
    return {
        **fields,
        "regional_map_center_lat": f"{center_lat:.7f}",
        "regional_map_center_lon": f"{center_lon:.7f}",
    }


def regional_map_baseline_for_rows(rows: list[Mapping[str, Any]]) -> dict[str, Any]:
    if not rows:
        fields = regional_map_fields(0.0, 0.0)
        return {
            **fields,
            "regional_map_provider_counts": {fields["regional_map_provider"]: 0},
        }
    providers: dict[str, int] = {}
    selected: Mapping[str, Any] | None = None
    for row in rows:
        provider = str(row.get("regional_map_provider") or "")
        if not provider:
            lat = float(row["lat"])
            lon = float(row["lon"])
            row = {**row, **regional_map_fields(lat, lon)}
            provider = str(row["regional_map_provider"])
        providers[provider] = providers.get(provider, 0) + 1
        if selected is None or provider == "Amap/Gaode":
            selected = row
    assert selected is not None
    return {
        "regional_map_provider": str(selected.get("regional_map_provider", "")),
        "regional_map_url": str(selected.get("regional_map_url", "")),
        "regional_map_coordinate_system": str(selected.get("regional_map_coordinate_system", "")),
        "regional_map_source_coordinate_system": str(selected.get("regional_map_source_coordinate_system", "")),
        "regional_map_audit_status": str(selected.get("regional_map_audit_status", "")),
        "regional_map_note": str(selected.get("regional_map_note", "")),
        "regional_map_provider_counts": providers,
    }


def mapillary_url(lat: float, lon: float) -> str:
    return f"https://www.mapillary.com/app/?lat={lat:.6f}&lng={lon:.6f}&z=18"


def kartaview_url(lat: float, lon: float) -> str:
    return f"https://kartaview.org/map/@{lat:.6f},{lon:.6f},18z"


def google_maps_baseline_fields(
    temporal_scope: str = "unspecified",
    target_date: str | None = None,
) -> dict[str, str]:
    normalized_scope = temporal_scope.strip().lower() if temporal_scope else "unspecified"
    if normalized_scope not in {"unspecified", "current", "historical"}:
        raise ValueError("google_maps_temporal_scope must be one of: unspecified, current, historical")
    clean_target_date = (target_date or "").strip()
    needs_confirmation = normalized_scope == "unspecified" or (
        normalized_scope == "historical" and not clean_target_date
    )
    if normalized_scope == "current":
        note = "Use current Google Maps as the reality baseline for current-network modeling."
    elif normalized_scope == "historical":
        note = (
            "Use Google Maps only if its visible evidence matches the historical target period; "
            "otherwise use dated imagery, OSM history, or agency inventory."
        )
    else:
        note = "Ask whether the user needs the current map or a historical target date before deciding TLS actions."
    return {
        "google_maps_baseline_source": "Google Maps",
        "google_maps_temporal_scope": normalized_scope,
        "google_maps_target_date": clean_target_date,
        "google_maps_requires_time_confirmation": "yes" if needs_confirmation else "no",
        "google_maps_time_note": note,
    }


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    radius = 6371000.0
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return 2 * radius * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _row_lat(row: Mapping[str, Any]) -> float:
    return float(row["lat"])


def _row_lon(row: Mapping[str, Any]) -> float:
    return float(row["lon"])


def _road_names(rows: list[Mapping[str, Any]]) -> str:
    names = set()
    for row in rows:
        for field in ("incoming_road_names", "outgoing_road_names", "road_names"):
            for name in str(row.get(field, "")).split("; "):
                if name:
                    names.add(name)
    return "; ".join(sorted(names))


def _max_pair_distance_m(rows: list[Mapping[str, Any]]) -> float:
    max_distance = 0.0
    for index, row in enumerate(rows):
        for other in rows[index + 1:]:
            max_distance = max(
                max_distance,
                haversine_m(_row_lat(row), _row_lon(row), _row_lat(other), _row_lon(other)),
            )
    return max_distance


def _summarize_cluster(
    cluster_id: str,
    rows: list[Mapping[str, Any]],
    *,
    google_maps_temporal_scope: str = "unspecified",
    google_maps_target_date: str | None = None,
) -> dict[str, Any]:
    ordered = sorted(rows, key=lambda row: str(row.get("tls_id", "")))
    lat = sum(_row_lat(row) for row in ordered) / len(ordered)
    lon = sum(_row_lon(row) for row in ordered) / len(ordered)
    osm_signal_count = sum(1 for row in ordered if row.get("has_osm_signal_within_35m") == "yes")
    return {
        "cluster_id": cluster_id,
        "lat": f"{lat:.7f}",
        "lon": f"{lon:.7f}",
        "tls_count": len(ordered),
        "tls_ids": ";".join(str(row.get("tls_id", "")) for row in ordered),
        "max_pair_distance_m": f"{_max_pair_distance_m(ordered):.1f}",
        "osm_signal_count": osm_signal_count,
        "road_names": _road_names(ordered),
        "google_maps_url": google_maps_url(lat, lon),
        "google_audit_status": "needs_google_review",
        "google_audit_note": "",
        **regional_map_fields(lat, lon, label=f"SUMO TLS cluster {cluster_id}"),
        **google_maps_baseline_fields(google_maps_temporal_scope, google_maps_target_date),
    }


def cluster_tls_candidates(
    rows: list[Mapping[str, Any]],
    *,
    radius_m: float = 60.0,
    google_maps_temporal_scope: str = "unspecified",
    google_maps_target_date: str | None = None,
) -> list[dict[str, Any]]:
    if not rows:
        return []
    parent = list(range(len(rows)))

    def find(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    def union(left: int, right: int) -> None:
        left_root = find(left)
        right_root = find(right)
        if left_root != right_root:
            parent[right_root] = left_root

    for i, row in enumerate(rows):
        for j in range(i + 1, len(rows)):
            if haversine_m(_row_lat(row), _row_lon(row), _row_lat(rows[j]), _row_lon(rows[j])) <= radius_m:
                union(i, j)

    groups: dict[int, list[Mapping[str, Any]]] = {}
    for index, row in enumerate(rows):
        groups.setdefault(find(index), []).append(row)

    ordered_groups = sorted(
        groups.values(),
        key=lambda group: (
            _road_names(group),
            sum(_row_lat(row) for row in group) / len(group),
            sum(_row_lon(row) for row in group) / len(group),
        ),
    )
    return [
        _summarize_cluster(
            f"G{index:03d}",
            list(group),
            google_maps_temporal_scope=google_maps_temporal_scope,
            google_maps_target_date=google_maps_target_date,
        )
        for index, group in enumerate(ordered_groups, start=1)
    ]


def _iter_osm_signal_nodes(osm_file: Path) -> list[dict[str, Any]]:
    opener = gzip.open if osm_file.suffix == ".gz" else open
    signals = []
    with opener(osm_file, "rt", encoding="utf-8") as handle:
        for _event, elem in ET.iterparse(handle, events=("end",)):
            if elem.tag == "node":
                tags = {tag.attrib.get("k"): tag.attrib.get("v") for tag in elem.findall("tag")}
                if tags.get("highway") == "traffic_signals":
                    signals.append(
                        {
                            "osm_signal_id": elem.attrib.get("id", ""),
                            "lat": float(elem.attrib["lat"]),
                            "lon": float(elem.attrib["lon"]),
                        }
                    )
                elem.clear()
    return signals


def _nearest_signal(lat: float, lon: float, signals: list[dict[str, Any]]) -> tuple[str, float | None]:
    if not signals:
        return "", None
    best = min(signals, key=lambda item: haversine_m(lat, lon, item["lat"], item["lon"]))
    return best["osm_signal_id"], haversine_m(lat, lon, best["lat"], best["lon"])


def _edge_name_lookup(net_file: Path) -> dict[str, str]:
    names = {}
    current_edge = None
    for event, elem in ET.iterparse(net_file, events=("start", "end")):
        if event == "start" and elem.tag == "edge":
            current_edge = elem.attrib.get("id")
            if elem.attrib.get("name"):
                names[current_edge] = elem.attrib["name"]
        elif event == "end":
            if elem.tag == "param" and current_edge and elem.attrib.get("key") in {"name", "origId"}:
                names.setdefault(current_edge, elem.attrib.get("value", ""))
            elif elem.tag == "edge":
                current_edge = None
            elem.clear()
    return names


def _parse_utm_zone(proj_parameter: str) -> int:
    for token in proj_parameter.split():
        if token.startswith("+zone="):
            return int(token.split("=", 1)[1])
    raise ValueError(f"Cannot parse UTM zone from projParameter: {proj_parameter}")


def _utm_to_latlon(easting: float, northing: float, zone: int, northern: bool = True) -> tuple[float, float]:
    a = 6378137.0
    ecc_squared = 0.00669438
    k0 = 0.9996
    ecc_prime_squared = ecc_squared / (1 - ecc_squared)
    x = easting - 500000.0
    y = northing
    if not northern:
        y -= 10000000.0
    lon_origin = (zone - 1) * 6 - 180 + 3
    m = y / k0
    mu = m / (a * (1 - ecc_squared / 4 - 3 * ecc_squared**2 / 64 - 5 * ecc_squared**3 / 256))
    e1 = (1 - math.sqrt(1 - ecc_squared)) / (1 + math.sqrt(1 - ecc_squared))
    j1 = 3 * e1 / 2 - 27 * e1**3 / 32
    j2 = 21 * e1**2 / 16 - 55 * e1**4 / 32
    j3 = 151 * e1**3 / 96
    j4 = 1097 * e1**4 / 512
    fp = mu + j1 * math.sin(2 * mu) + j2 * math.sin(4 * mu) + j3 * math.sin(6 * mu) + j4 * math.sin(8 * mu)
    sin_fp = math.sin(fp)
    cos_fp = math.cos(fp)
    tan_fp = math.tan(fp)
    c1 = ecc_prime_squared * cos_fp**2
    t1 = tan_fp**2
    n1 = a / math.sqrt(1 - ecc_squared * sin_fp**2)
    r1 = a * (1 - ecc_squared) / (1 - ecc_squared * sin_fp**2) ** 1.5
    d = x / (n1 * k0)
    lat = fp - (n1 * tan_fp / r1) * (
        d**2 / 2
        - (5 + 3 * t1 + 10 * c1 - 4 * c1**2 - 9 * ecc_prime_squared) * d**4 / 24
        + (61 + 90 * t1 + 298 * c1 + 45 * t1**2 - 252 * ecc_prime_squared - 3 * c1**2) * d**6 / 720
    )
    lon = math.radians(lon_origin) + (
        d
        - (1 + 2 * t1 + c1) * d**3 / 6
        + (5 - 2 * c1 + 28 * t1 - 3 * c1**2 + 8 * ecc_prime_squared + 24 * t1**2) * d**5 / 120
    ) / cos_fp
    return math.degrees(lat), math.degrees(lon)


def _net_xy_to_latlon(net: Any, x: float, y: float) -> tuple[float, float]:
    try:
        lon, lat = net.convertXY2LonLat(x, y)
        return lat, lon
    except ModuleNotFoundError as exc:
        if exc.name != "pyproj":
            raise
    except RuntimeError as exc:
        if "geo-projection" not in str(exc) and "pyproj" not in str(exc):
            raise
    x_off, y_off = net.getLocationOffset()
    zone = _parse_utm_zone(net._location["projParameter"])
    return _utm_to_latlon(x - x_off, y - y_off, zone=zone, northern=True)


def extract_tls_candidates(
    *,
    net_file: Path,
    osm_file: Path | None = None,
    min_connections: int = 1,
    google_maps_temporal_scope: str = "unspecified",
    google_maps_target_date: str | None = None,
) -> list[dict[str, Any]]:
    try:
        import sumolib.net  # type: ignore
    except ModuleNotFoundError as exc:
        raise RuntimeError("sumolib is required for TLS coordinate extraction") from exc

    net = sumolib.net.readNet(str(net_file), withPrograms=True)
    signals = _iter_osm_signal_nodes(osm_file) if osm_file is not None else []
    edge_names = _edge_name_lookup(net_file)
    rows = []
    for tls in net.getTrafficLights():
        connections = tls.getConnections()
        if len(connections) < min_connections:
            continue
        try:
            node = net.getNode(tls.getID())
            lat, lon = _net_xy_to_latlon(net, node.getCoord()[0], node.getCoord()[1])
        except Exception:  # noqa: BLE001 - malformed third-party TLS objects use lane-shape fallback.
            points = []
            for incoming_lane, outgoing_lane, _ in connections:
                for lane in (incoming_lane, outgoing_lane):
                    shape = lane.getShape()
                    if shape:
                        points.extend([shape[0], shape[-1]])
            if not points:
                continue
            x = sum(point[0] for point in points) / len(points)
            y = sum(point[1] for point in points) / len(points)
            lat, lon = _net_xy_to_latlon(net, x, y)
        signal_id, signal_distance = _nearest_signal(lat, lon, signals)
        incoming_edge_ids = sorted({incoming_lane.getEdge().getID() for incoming_lane, _, _ in connections})
        outgoing_edge_ids = sorted({outgoing_lane.getEdge().getID() for _, outgoing_lane, _ in connections})
        incoming_names = sorted({edge_names.get(edge_id, "") for edge_id in incoming_edge_ids if edge_names.get(edge_id, "")})
        outgoing_names = sorted({edge_names.get(edge_id, "") for edge_id in outgoing_edge_ids if edge_names.get(edge_id, "")})
        has_osm_signal = signal_distance is not None and signal_distance <= 35.0
        rows.append(
            {
                "tls_id": tls.getID(),
                "lat": f"{lat:.7f}",
                "lon": f"{lon:.7f}",
                "connection_count": len(connections),
                "nearest_osm_signal_id": signal_id,
                "nearest_osm_signal_distance_m": "" if signal_distance is None else f"{signal_distance:.1f}",
                "has_osm_signal_within_35m": "yes" if has_osm_signal else "no",
                "incoming_road_names": "; ".join(incoming_names[:8]),
                "outgoing_road_names": "; ".join(outgoing_names[:8]),
                "incoming_edge_ids": "; ".join(incoming_edge_ids[:12]),
                "outgoing_edge_ids": "; ".join(outgoing_edge_ids[:12]),
                "google_maps_url": google_maps_url(lat, lon),
                "google_audit_status": "needs_google_review",
                "google_audit_note": "",
                **regional_map_fields(lat, lon, label=f"SUMO TLS {tls.getID()}"),
                **google_maps_baseline_fields(google_maps_temporal_scope, google_maps_target_date),
            }
        )
    rows.sort(key=lambda row: (row["incoming_road_names"], row["tls_id"]))
    return rows


TLS_CANDIDATE_FIELDS = [
    "tls_id",
    "lat",
    "lon",
    "connection_count",
    "nearest_osm_signal_id",
    "nearest_osm_signal_distance_m",
    "has_osm_signal_within_35m",
    "incoming_road_names",
    "outgoing_road_names",
    "incoming_edge_ids",
    "outgoing_edge_ids",
    "google_maps_url",
    "google_audit_status",
    "google_audit_note",
    "regional_map_provider",
    "regional_map_url",
    "regional_map_coordinate_system",
    "regional_map_source_coordinate_system",
    "regional_map_audit_status",
    "regional_map_note",
    "google_maps_baseline_source",
    "google_maps_temporal_scope",
    "google_maps_target_date",
    "google_maps_requires_time_confirmation",
    "google_maps_time_note",
]


TLS_CLUSTER_FIELDS = [
    "cluster_id",
    "lat",
    "lon",
    "tls_count",
    "tls_ids",
    "max_pair_distance_m",
    "osm_signal_count",
    "road_names",
    "google_maps_url",
    "google_audit_status",
    "google_audit_note",
    "regional_map_provider",
    "regional_map_url",
    "regional_map_coordinate_system",
    "regional_map_source_coordinate_system",
    "regional_map_audit_status",
    "regional_map_note",
    "google_maps_baseline_source",
    "google_maps_temporal_scope",
    "google_maps_target_date",
    "google_maps_requires_time_confirmation",
    "google_maps_time_note",
]


TLS_MULTISOURCE_REVIEW_FIELDS = [
    "tls_id",
    "lat",
    "lon",
    "connection_count",
    "incoming_road_names",
    "outgoing_road_names",
    "osm_signal_status",
    "nearest_osm_signal_id",
    "nearest_osm_signal_distance_m",
    "google_maps_url",
    "regional_map_provider",
    "regional_map_url",
    "regional_map_coordinate_system",
    "regional_map_source_coordinate_system",
    "regional_map_audit_status",
    "regional_map_note",
    "mapillary_url",
    "kartaview_url",
    "official_inventory_status",
    "official_inventory_id",
    "signal_plan_status",
    "signal_plan_id",
    "field_evidence_status",
    "field_evidence_id",
    "evidence_level",
    "review_status",
    "claim_status",
    "review_note",
]


def _write_csv(path: Path, rows: list[Mapping[str, Any]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _clean_review_value(value: Any) -> str:
    return str(value or "").strip()


def _source_record(
    records: Mapping[str, Mapping[str, Any]] | None,
    tls_id: str,
) -> Mapping[str, Any]:
    if not records:
        return {}
    return records.get(tls_id, {})


def _source_field(record: Mapping[str, Any], *names: str) -> str:
    for name in names:
        value = _clean_review_value(record.get(name))
        if value:
            return value
    return ""


def _status_confirms_external_evidence(status: str) -> bool:
    normalized = normalize_text(status)
    if not normalized:
        return False
    negative_tokens = {
        "missing",
        "none",
        "no",
        "not found",
        "not available",
        "rejected",
        "unknown",
        "unverified",
    }
    return normalized not in negative_tokens


def _tls_evidence_level(
    *,
    official_status: str,
    signal_plan_status: str,
    field_evidence_status: str,
    has_osm_signal: bool,
) -> str:
    if _status_confirms_external_evidence(official_status) or _status_confirms_external_evidence(signal_plan_status):
        return "authoritative"
    if _status_confirms_external_evidence(field_evidence_status):
        return "field-observed"
    if has_osm_signal:
        return "osm-only"
    return "sumo-guess-only"


def _join_review_notes(*values: str) -> str:
    notes = [value for value in values if value]
    notes.append("Human review required before treating the TLS as correct.")
    return " | ".join(notes)


def build_tls_multisource_review(
    rows: list[Mapping[str, Any]],
    *,
    official_inventory: Mapping[str, Mapping[str, Any]] | None = None,
    signal_plans: Mapping[str, Mapping[str, Any]] | None = None,
    field_evidence: Mapping[str, Mapping[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    review_rows = []
    for row in rows:
        tls_id = _clean_review_value(row.get("tls_id"))
        lat = float(row["lat"])
        lon = float(row["lon"])
        official = _source_record(official_inventory, tls_id)
        signal_plan = _source_record(signal_plans, tls_id)
        field = _source_record(field_evidence, tls_id)
        official_status = _source_field(official, "status", "review_status", "audit_status")
        signal_plan_status = _source_field(signal_plan, "status", "review_status", "audit_status")
        field_status = _source_field(field, "status", "review_status", "audit_status")
        has_osm_signal = _clean_review_value(row.get("has_osm_signal_within_35m")) == "yes"
        regional_fields = regional_map_fields(lat, lon, label=f"SUMO TLS {tls_id}")
        review_rows.append(
            {
                "tls_id": tls_id,
                "lat": f"{lat:.7f}",
                "lon": f"{lon:.7f}",
                "connection_count": _clean_review_value(row.get("connection_count")),
                "incoming_road_names": _clean_review_value(row.get("incoming_road_names")),
                "outgoing_road_names": _clean_review_value(row.get("outgoing_road_names")),
                "osm_signal_status": "match_within_35m" if has_osm_signal else "no_nearby_osm_signal",
                "nearest_osm_signal_id": _clean_review_value(row.get("nearest_osm_signal_id")),
                "nearest_osm_signal_distance_m": _clean_review_value(row.get("nearest_osm_signal_distance_m")),
                "google_maps_url": _clean_review_value(row.get("google_maps_url")) or google_maps_url(lat, lon),
                "regional_map_provider": _clean_review_value(row.get("regional_map_provider")) or regional_fields["regional_map_provider"],
                "regional_map_url": _clean_review_value(row.get("regional_map_url")) or regional_fields["regional_map_url"],
                "regional_map_coordinate_system": _clean_review_value(row.get("regional_map_coordinate_system")) or regional_fields["regional_map_coordinate_system"],
                "regional_map_source_coordinate_system": _clean_review_value(row.get("regional_map_source_coordinate_system")) or regional_fields["regional_map_source_coordinate_system"],
                "regional_map_audit_status": _clean_review_value(row.get("regional_map_audit_status")) or regional_fields["regional_map_audit_status"],
                "regional_map_note": _clean_review_value(row.get("regional_map_note")) or regional_fields["regional_map_note"],
                "mapillary_url": mapillary_url(lat, lon),
                "kartaview_url": kartaview_url(lat, lon),
                "official_inventory_status": official_status,
                "official_inventory_id": _source_field(official, "source_id", "record_id", "inventory_id", "id"),
                "signal_plan_status": signal_plan_status,
                "signal_plan_id": _source_field(signal_plan, "source_id", "record_id", "plan_id", "id"),
                "field_evidence_status": field_status,
                "field_evidence_id": _source_field(field, "source_id", "record_id", "photo_id", "id"),
                "evidence_level": _tls_evidence_level(
                    official_status=official_status,
                    signal_plan_status=signal_plan_status,
                    field_evidence_status=field_status,
                    has_osm_signal=has_osm_signal,
                ),
                "review_status": "needs_manual_review",
                "claim_status": "diagnostic-demo",
                "review_note": _join_review_notes(
                    _source_field(official, "note", "review_note"),
                    _source_field(signal_plan, "note", "review_note"),
                    _source_field(field, "note", "review_note"),
                ),
            }
        )
    return review_rows


def read_tls_review_status_csv(path: Path | None) -> dict[str, dict[str, str]]:
    if path is None:
        return {}
    records: dict[str, dict[str, str]] = {}
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            tls_id = _source_field(row, "tls_id", "sumo_tls_id", "junction_id")
            if not tls_id:
                continue
            records[tls_id] = {
                "status": _source_field(row, "status", "review_status", "audit_status"),
                "source_id": _source_field(row, "source_id", "record_id", "inventory_id", "plan_id", "photo_id", "id"),
                "note": _source_field(row, "note", "review_note"),
            }
    return records


def audit_tls(
    *,
    net_file: Path,
    output_dir: Path,
    prefix: str = "sumo_tls_audit",
    osm_file: Path | None = None,
    min_connections: int = 1,
    cluster_radius_m: float = 60.0,
    google_maps_temporal_scope: str = "unspecified",
    google_maps_target_date: str | None = None,
) -> dict[str, Any]:
    try:
        google_maps_baseline = google_maps_baseline_fields(
            google_maps_temporal_scope,
            google_maps_target_date,
        )
        rows = extract_tls_candidates(
            net_file=net_file,
            osm_file=osm_file,
            min_connections=min_connections,
            google_maps_temporal_scope=google_maps_temporal_scope,
            google_maps_target_date=google_maps_target_date,
        )
        clusters = cluster_tls_candidates(
            rows,
            radius_m=cluster_radius_m,
            google_maps_temporal_scope=google_maps_temporal_scope,
            google_maps_target_date=google_maps_target_date,
        )
        regional_map_baseline = regional_map_baseline_for_rows(rows)
    except (OSError, ET.ParseError, RuntimeError, ValueError) as exc:
        return _failure(f"{type(exc).__name__}: {exc}")

    candidates_file = output_dir / f"{prefix}_tls_candidates.csv"
    clusters_file = output_dir / f"{prefix}_tls_clusters.csv"
    _write_csv(candidates_file, rows, TLS_CANDIDATE_FIELDS)
    _write_csv(clusters_file, clusters, TLS_CLUSTER_FIELDS)
    return {
        "status": "pass",
        "claim_status": "diagnostic-demo",
        "net_file": str(net_file),
        "osm_file": str(osm_file) if osm_file else None,
        "tls_candidate_count": len(rows),
        "tls_cluster_count": len(clusters),
        "candidates_file": str(candidates_file),
        "clusters_file": str(clusters_file),
        "cluster_radius_m": cluster_radius_m,
        "google_maps_baseline": google_maps_baseline,
        "regional_map_baseline": regional_map_baseline,
        "warnings": (
            ["Google Maps baseline temporal scope is unspecified; ask whether current map or historical target date applies."]
            if google_maps_baseline["google_maps_requires_time_confirmation"] == "yes"
            else []
        ),
    }


def audit_tls_multisource(
    *,
    net_file: Path,
    output_dir: Path,
    prefix: str = "sumo_tls_multisource_review",
    osm_file: Path | None = None,
    official_inventory_csv: Path | None = None,
    signal_plan_csv: Path | None = None,
    field_evidence_csv: Path | None = None,
    min_connections: int = 1,
    google_maps_temporal_scope: str = "unspecified",
    google_maps_target_date: str | None = None,
) -> dict[str, Any]:
    try:
        google_maps_baseline = google_maps_baseline_fields(
            google_maps_temporal_scope,
            google_maps_target_date,
        )
        rows = extract_tls_candidates(
            net_file=net_file,
            osm_file=osm_file,
            min_connections=min_connections,
            google_maps_temporal_scope=google_maps_temporal_scope,
            google_maps_target_date=google_maps_target_date,
        )
        review_rows = build_tls_multisource_review(
            rows,
            official_inventory=read_tls_review_status_csv(official_inventory_csv),
            signal_plans=read_tls_review_status_csv(signal_plan_csv),
            field_evidence=read_tls_review_status_csv(field_evidence_csv),
        )
        regional_map_baseline = regional_map_baseline_for_rows(review_rows)
    except (OSError, ET.ParseError, RuntimeError, ValueError, KeyError) as exc:
        return _failure(f"{type(exc).__name__}: {exc}")

    review_file = output_dir / f"{prefix}.csv"
    _write_csv(review_file, review_rows, TLS_MULTISOURCE_REVIEW_FIELDS)
    needs_review_count = sum(1 for row in review_rows if row["review_status"] == "needs_manual_review")
    evidence_counts: dict[str, int] = {}
    for row in review_rows:
        level = str(row["evidence_level"])
        evidence_counts[level] = evidence_counts.get(level, 0) + 1
    warnings = [
        "Multi-source TLS review is a human review aid; it does not certify signal existence, phasing, timing, or controller readiness."
    ]
    if google_maps_baseline["google_maps_requires_time_confirmation"] == "yes":
        warnings.append(
            "Google Maps temporal scope is unspecified; ask whether current map or historical target date applies."
        )
    if not official_inventory_csv and not signal_plan_csv:
        warnings.append("No official inventory or signal-plan manifest was supplied.")
    return {
        "status": "pass",
        "claim_status": "diagnostic-demo",
        "net_file": str(net_file),
        "osm_file": str(osm_file) if osm_file else None,
        "official_inventory_csv": str(official_inventory_csv) if official_inventory_csv else None,
        "signal_plan_csv": str(signal_plan_csv) if signal_plan_csv else None,
        "field_evidence_csv": str(field_evidence_csv) if field_evidence_csv else None,
        "tls_candidate_count": len(review_rows),
        "needs_manual_review_count": needs_review_count,
        "evidence_level_counts": evidence_counts,
        "review_file": str(review_file),
        "google_maps_baseline": google_maps_baseline,
        "regional_map_baseline": regional_map_baseline,
        "warnings": warnings,
    }


def normalize_text(value: str) -> str:
    replacements = {
        "\u00e4": "ae",
        "\u00f6": "oe",
        "\u00fc": "ue",
        "\u00df": "ss",
        "\u00c4": "Ae",
        "\u00d6": "Oe",
        "\u00dc": "Ue",
    }
    for source, target in replacements.items():
        value = value.replace(source, target)
    decomposed = unicodedata.normalize("NFKD", value)
    ascii_value = decomposed.encode("ascii", "ignore").decode("ascii")
    return " ".join("".join(ch if ch.isalnum() else " " for ch in ascii_value.lower()).split())


def _lane_allows_passenger(lane: ET.Element) -> bool:
    from .osm_access import _permission_set

    return "passenger" in _permission_set(lane.attrib)


def read_net_edges(path: Path) -> tuple[dict[str, EdgeInfo], dict[str, set[str]]]:
    edges: dict[str, EdgeInfo] = {}
    connections: dict[str, set[str]] = {}
    root = ET.parse(path).getroot()
    for edge in root.findall("edge"):
        edge_id = edge.attrib["id"]
        if edge.attrib.get("function") or edge_id.startswith(":"):
            continue
        params = [
            param.attrib.get("value", "")
            for param in edge.findall("param")
            if param.attrib.get("key") in {"name", "bridge:name", "loc_name", "alt_name", "destination"}
            and param.attrib.get("value")
        ]
        if edge.attrib.get("name"):
            params.append(edge.attrib["name"])
        lanes = edge.findall("lane")
        edges[edge_id] = EdgeInfo(
            edge_id=edge_id,
            from_node=edge.attrib.get("from", ""),
            to_node=edge.attrib.get("to", ""),
            name_values=tuple(params),
            allows_passenger=any(_lane_allows_passenger(lane) for lane in lanes),
        )
    for connection in root.findall("connection"):
        from_edge = connection.attrib.get("from")
        to_edge = connection.attrib.get("to")
        if from_edge in edges and to_edge in edges:
            connections.setdefault(from_edge, set()).add(to_edge)
    return edges, connections


def _incoming_outgoing(connections: dict[str, set[str]]) -> tuple[dict[str, set[str]], dict[str, set[str]]]:
    incoming: dict[str, set[str]] = {}
    outgoing: dict[str, set[str]] = {}
    for from_edge, to_edges in connections.items():
        for to_edge in to_edges:
            outgoing.setdefault(from_edge, set()).add(to_edge)
            incoming.setdefault(to_edge, set()).add(from_edge)
    return incoming, outgoing


def _edge_matches_query(edge: EdgeInfo, query: KeyEdgeQuery) -> tuple[bool, str]:
    normalized_values = [(value, normalize_text(value)) for value in edge.name_values]
    reject_terms = [normalize_text(term) for term in query.reject_terms]
    search_terms = [normalize_text(term) for term in query.search_terms]
    for original, normalized in normalized_values:
        if any(reject in normalized for reject in reject_terms):
            continue
        for search in search_terms:
            if search in normalized:
                return True, original
    return False, ""


def _coerce_key_edge_query(value: Mapping[str, Any]) -> KeyEdgeQuery:
    return KeyEdgeQuery(
        label=str(value["label"]),
        role=str(value.get("role", "key_edge")),
        search_terms=tuple(str(item) for item in value.get("search_terms", ())),
        reject_terms=tuple(str(item) for item in value.get("reject_terms", ())),
        preferred_edge_id=str(value.get("preferred_edge_id", "")),
    )


def select_required_edges(
    edges: dict[str, EdgeInfo],
    incoming: dict[str, set[str]],
    outgoing: dict[str, set[str]],
    queries: list[KeyEdgeQuery],
) -> list[KeyEdgeRow]:
    rows = []
    for query in queries:
        candidates = []
        for edge in edges.values():
            matched, matched_value = _edge_matches_query(edge, query)
            if not matched or not edge.allows_passenger:
                continue
            has_in = bool(incoming.get(edge.edge_id))
            has_out = bool(outgoing.get(edge.edge_id))
            preferred = int(bool(query.preferred_edge_id) and edge.edge_id == query.preferred_edge_id)
            score = (preferred, int(has_in) + int(has_out), len(edge.name_values), -len(edge.edge_id))
            candidates.append((score, edge, matched_value))
        if not candidates:
            rows.append(KeyEdgeRow(query.label, query.role, "", "", "", "missing_match"))
            continue
        candidates.sort(key=lambda item: item[0], reverse=True)
        _score, edge, matched_value = candidates[0]
        rows.append(
            KeyEdgeRow(
                query.label,
                query.role,
                edge.from_node,
                edge.to_node,
                edge.edge_id,
                f"matched={matched_value}; candidates={len(candidates)}",
            )
        )
    return rows


def _route_edges_for_required_edge(
    required_edge_id: str,
    edges: dict[str, EdgeInfo],
    incoming: dict[str, set[str]],
    outgoing: dict[str, set[str]],
) -> list[str]:
    if required_edge_id not in edges:
        return []
    route = []
    predecessors = sorted(edge for edge in incoming.get(required_edge_id, set()) if edges[edge].allows_passenger)
    successors = sorted(edge for edge in outgoing.get(required_edge_id, set()) if edges[edge].allows_passenger)
    if predecessors:
        route.append(predecessors[0])
    route.append(required_edge_id)
    if successors:
        route.append(successors[0])
    return route


def _build_probe_routes(
    rows: list[KeyEdgeRow],
    edges: dict[str, EdgeInfo],
    incoming: dict[str, set[str]],
    outgoing: dict[str, set[str]],
) -> list[ProbeRoute]:
    routes = []
    for index, row in enumerate(rows):
        route_edges = _route_edges_for_required_edge(row.required_edge_id, edges, incoming, outgoing)
        if not route_edges:
            continue
        routes.append(
            ProbeRoute(
                route_id=f"probe_{row.label}",
                vehicle_id=f"veh_{row.label}",
                edges=route_edges,
                depart=index * 10,
            )
        )
    return routes


def _write_key_edges(path: Path, rows: list[KeyEdgeRow]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["label", "role", "from_hint", "to_hint", "required_edge_id", "notes"])
        for row in rows:
            writer.writerow([row.label, row.role, row.from_hint, row.to_hint, row.required_edge_id, row.notes])


def _write_route_file(path: Path, routes: list[ProbeRoute]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    root = ET.Element("routes")
    ET.SubElement(root, "vType", id="passenger_probe", vClass="passenger", accel="2.6", decel="4.5")
    for route in routes:
        ET.SubElement(root, "route", id=route.route_id, edges=" ".join(route.edges))
        ET.SubElement(
            root,
            "vehicle",
            id=route.vehicle_id,
            type="passenger_probe",
            route=route.route_id,
            depart=str(route.depart),
            departLane="best",
            departSpeed="max",
        )
    ET.ElementTree(root).write(path, encoding="utf-8", xml_declaration=True)


def _relpath(target_path: Path, start_dir: Path) -> str:
    return Path(os.path.relpath(target_path.resolve(), start=start_dir.resolve())).as_posix()


def _write_sumocfg(
    path: Path,
    *,
    net_file: Path,
    route_file: Path,
    summary_output: Path,
    tripinfo_output: Path,
    seed: int,
    end: int,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    root = ET.Element("configuration")
    input_node = ET.SubElement(root, "input")
    ET.SubElement(input_node, "net-file", value=_relpath(net_file, path.parent))
    ET.SubElement(input_node, "route-files", value=_relpath(route_file, path.parent))
    output_node = ET.SubElement(root, "output")
    ET.SubElement(output_node, "summary-output", value=_relpath(summary_output, path.parent))
    ET.SubElement(output_node, "tripinfo-output", value=_relpath(tripinfo_output, path.parent))
    time_node = ET.SubElement(root, "time")
    ET.SubElement(time_node, "begin", value="0")
    ET.SubElement(time_node, "end", value=str(end))
    random_node = ET.SubElement(root, "random_number")
    ET.SubElement(random_node, "seed", value=str(seed))
    ET.ElementTree(root).write(path, encoding="utf-8", xml_declaration=True)


def build_routeability_probe(
    *,
    net_file: Path,
    output_dir: Path,
    prefix: str = "routeability_probe",
    key_edge_queries: list[Mapping[str, Any]],
    seed: int = 42,
    end: int = 180,
) -> dict[str, Any]:
    try:
        queries = [_coerce_key_edge_query(query) for query in key_edge_queries]
        if not queries:
            return _failure("key_edge_queries must contain at least one query")
        if any(not query.search_terms for query in queries):
            return _failure("each key edge query must include at least one search term")
        edges, connections = read_net_edges(net_file)
        incoming, outgoing = _incoming_outgoing(connections)
        rows = select_required_edges(edges, incoming, outgoing, queries)
        routes = _build_probe_routes(rows, edges, incoming, outgoing)
    except (OSError, ET.ParseError, KeyError, ValueError) as exc:
        return _failure(f"{type(exc).__name__}: {exc}")

    key_edges_file = output_dir / f"{prefix}_key_edges.csv"
    route_file = output_dir / f"{prefix}.rou.xml"
    sumocfg_file = output_dir / f"{prefix}.sumocfg"
    summary_file = output_dir / f"{prefix}_summary.xml"
    tripinfo_file = output_dir / f"{prefix}_tripinfo.xml"
    _write_key_edges(key_edges_file, rows)
    _write_route_file(route_file, routes)
    _write_sumocfg(
        sumocfg_file,
        net_file=net_file,
        route_file=route_file,
        summary_output=summary_file,
        tripinfo_output=tripinfo_file,
        seed=seed,
        end=end,
    )
    missing = [row.label for row in rows if not row.required_edge_id]
    status = "pass" if not missing and routes else "fail"
    warnings = []
    if missing:
        warnings.append("missing key edge matches: " + ", ".join(missing))
    if not routes:
        warnings.append("no routeability probe routes were generated")
    return {
        "status": status,
        "claim_status": "diagnostic-demo" if status == "pass" else "construction-invalid",
        "net_file": str(net_file),
        "key_edges_file": str(key_edges_file),
        "route_file": str(route_file),
        "sumocfg_file": str(sumocfg_file),
        "summary_file": str(summary_file),
        "tripinfo_file": str(tripinfo_file),
        "route_count": len(routes),
        "missing_key_edges": missing,
        "warnings": warnings,
    }
