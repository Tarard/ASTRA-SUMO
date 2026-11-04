from __future__ import annotations

from collections import Counter
from contextlib import ExitStack, nullcontext
import csv
import gzip
import hashlib
import math
import os
from importlib.util import find_spec
import shutil
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator, Mapping, NamedTuple
from urllib import error, parse, request
import unicodedata
import xml.etree.ElementTree as ET

from .artifact_io import atomic_writer, copy_file_atomic, write_json_atomic
from .command_runner import run_command
from .sumolib_load_check import SumolibLoadCheck
from .xml_stream import DirectChildren, binary_blocks, read_blocks


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


def _node_point(node: ET.Element) -> tuple[float, ...] | None:
    try:
        point = tuple(float(node.attrib[key]) for key in ("lon", "lat"))
    except (KeyError, ValueError):
        return None
    return point if all(map(math.isfinite, point)) else None


def _way_intersects_bbox(refs: list[str], points_by_id: Mapping[str, tuple[float, ...] | None], bbox: Bbox) -> bool:
    """Select by original segments, including crossings with both ends outside."""
    points = [points_by_id.get(ref) for ref in refs]
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


def _write_kept_osm(source: Path, target: Path, kept: Mapping[str, set], *, namespaced: bool) -> int:
    """Write the kept source elements as ``ET.tostring`` writes a new <osm> root holding them; return the node count.

    The root takes the source root's attributes and holds the bounds, then the kept nodes, ways and relations, each
    group in source order. Nodes are kept by id, ways and relations by their position among their kind. The groups
    are spooled to temporary files because merged tiles interleave them. A source that declares namespaces is
    written as one tree, since ElementTree names the namespaces of the whole tree.
    """
    groups: dict[str, list[ET.Element]] = {name: [] for name in ("bounds", "node", "way", "relation")}
    positions = {"way": 0, "relation": 0}
    kept_node_count = 0
    with ExitStack() as stack:
        spools = {name: stack.enter_context(tempfile.TemporaryFile()) for name in groups}
        with _open_xml(source, "rt") as handle:
            stream = DirectChildren(read_blocks(handle))
            for element in stream:
                group = "bounds" if element.tag in {"bounds", "bound"} else element.tag
                if group == "node":
                    keep = element.attrib.get("id") in kept["node"]
                    kept_node_count += keep
                elif group in positions:
                    keep = positions[group] in kept[group]
                    positions[group] += 1
                else:
                    keep = group == "bounds"
                if keep and namespaced:
                    groups[group].append(element)
                elif keep:
                    spools[group].write(ET.tostring(element, encoding="utf-8"))
        root = ET.Element("osm", stream.root.attrib)
        for elements in groups.values():
            root.extend(elements)
        payload = ET.tostring(root, encoding="utf-8", xml_declaration=True)
        target.parent.mkdir(parents=True, exist_ok=True)
        with atomic_writer(target) as raw:
            with (gzip.GzipFile(filename="", mode="wb", compresslevel=9, fileobj=raw)
                  if target.suffix == ".gz" else nullcontext(raw)) as handle:
                if not any(spool.tell() for spool in spools.values()):
                    handle.write(payload)
                    return kept_node_count
                # The root start tag, every kept element with its tail, then the end tag.
                handle.write(payload.removesuffix(b" />") + b">")
                for spool in spools.values():
                    spool.seek(0)
                    shutil.copyfileobj(spool, handle)
                handle.write(b"</osm>")
    return kept_node_count


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
    """Select complete source ways; bbox selection never cuts their geometry.

    The source is read one top-level element at a time, so a city extract stays small in memory: a first pass
    takes node coordinates and forced construction ways, a second decides ways and relations, a third writes the
    kept elements grouped as bounds, nodes, ways and relations.
    """
    from .road_scope import HIGHWAY_CLASS_PRESETS

    requested_forced_way_ids = set(forced_way_ids or ())
    points_by_id: dict[str, tuple[float, ...] | None] = {}
    bbox_node_refs = None if bbox is None else set()
    forced_construction_way_ids = set()
    if bbox is not None or requested_forced_way_ids:
        with _open_xml(source, "rt") as handle:
            for element in DirectChildren(read_blocks(handle)):
                if element.tag == "node" and bbox is not None:
                    node_id = element.attrib["id"]
                    points_by_id[node_id] = _node_point(element)
                    if _node_in_bbox(element, bbox):
                        bbox_node_refs.add(node_id)
                elif (element.tag == "way" and element.attrib.get("id", "") in requested_forced_way_ids
                        and _highway_value(element) == "construction"):
                    forced_construction_way_ids.add(element.attrib.get("id", ""))

    kept_way_positions = set()
    kept_way_ids = set()
    kept_node_refs = set()
    dropped_ways = 0
    dropped_ways_outside_bbox = 0
    dropped_ways_outside_reference_scope = 0
    kept_railway_ways = 0
    dropped_vehicle_area_way_ids = []
    restriction_refs: list[tuple[int, set[str]]] = []
    namespaces: list[tuple[str, str]] = []

    def source_ways() -> Iterator[tuple[int, ET.Element]]:
        """Each way with its position among the ways; restriction relations and namespaces are noted on the way."""
        way_position = relation_position = 0
        with _open_xml(source, "rt") as handle:
            stream = DirectChildren(read_blocks(handle), record_namespaces=True)
            for element in stream:
                if element.tag == "way":
                    yield way_position, element
                    way_position += 1
                elif element.tag == "relation":
                    tags = {tag.attrib.get("k"): tag.attrib.get("v") for tag in element.findall("tag")}
                    if tags.get("type") == "restriction":
                        restriction_refs.append((relation_position, _relation_way_refs(element)))
                    relation_position += 1
                elif element.tag == "node" and "id" not in element.attrib:
                    raise KeyError("id")  # Every node needs an id, as in a full node index.
            namespaces.extend(stream.namespaces)

    for position, way in source_ways():
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
            if len(refs) < 2 or (bbox is not None and not _way_intersects_bbox(refs, points_by_id, bbox)):
                dropped_ways_outside_bbox += 1
                continue
            kept_way_positions.add(position)
            kept_way_ids.add(way.attrib["id"])
            kept_node_refs.update(refs)
            kept_railway_ways += int(keep_railway and highway is None)
        elif highway is not None:
            dropped_ways += 1
    del points_by_id
    kept_relation_positions = {position for position, refs in restriction_refs if refs & kept_way_ids}
    kept_node_count = _write_kept_osm(source, target, {"node": kept_node_refs, "way": kept_way_positions,
                                                       "relation": kept_relation_positions}, namespaced=bool(namespaces))

    stats = {
        "kept_nodes": kept_node_count,
        "kept_ways": len(kept_way_positions),
        "dropped_ways": dropped_ways,
        "kept_relations": len(kept_relation_positions),
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

    # The network is read one top-level element at a time; counts are kept only when the whole file parses.
    road_edge_count = parameter_count = observed_edge_count = 0
    observed_tokens: set[str] = set()
    try:
        for edge in DirectChildren(binary_blocks(net_file)):
            if edge.tag != "edge":
                continue
            edge_id = edge.attrib.get("id", "")
            function = edge.attrib.get("function", "").strip().casefold()
            if edge_id.startswith(":") or function not in {"", "normal"}:
                continue
            road_edge_count += 1
            edge_has_observed_origin = False
            for container in (edge, *edge.findall("lane")):
                for parameter in container.findall("param"):
                    if parameter.attrib.get("key", "").casefold() != "origid":
                        continue
                    raw_value = parameter.attrib.get("value", "").strip()
                    if not raw_value:
                        continue
                    edge_has_observed_origin = True
                    parameter_count += 1
                    observed_tokens.update(
                        token
                        for token in raw_value.replace(",", " ").replace(";", " ").split()
                        if token
                    )
            if edge_has_observed_origin:
                observed_edge_count += 1
    except (OSError, ET.ParseError) as exc:
        summary["reason"] = f"sumo_net_not_parseable:{type(exc).__name__}"
        return summary
    summary["road_edge_count"] = road_edge_count
    summary["road_edge_with_observed_orig_id_count"] = observed_edge_count
    summary["observed_orig_id_parameter_count"] = parameter_count
    summary["observed_orig_id_token_count"] = len(observed_tokens)
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


def _sumo_typemap_directory(netconvert_binary, explicit_sumo_home=None):
    configured = explicit_sumo_home or os.environ.get("SUMO_HOME", "").strip()
    if configured:
        return Path(configured).resolve() / "data" / "typemap"
    executable = Path(shutil.which(str(netconvert_binary)) or netconvert_binary).resolve()
    candidates = [executable.parent.parent / "data" / "typemap",
                  executable.parent.parent / "share" / "sumo" / "data" / "typemap"]
    package = find_spec("sumo")
    if package is not None and package.origin:
        candidates.append(Path(package.origin).parent / "data" / "typemap")
    for directory in candidates:
        if directory.is_dir():
            return directory
    raise ValueError("Cannot find the SUMO OSM type maps. Set sumo_home to the SUMO installation directory.")


def _reference_visual_type_options(
    root: Path,
    logs_dir: Path,
    prefix: str,
    *,
    explicit_sumo_home: Path | None = None,
    netconvert_binary: str = "netconvert",
) -> tuple[list[str], list[str]]:
    typemap_dir = _sumo_typemap_directory(netconvert_binary, explicit_sumo_home)
    base_type_files = [typemap_dir / name for name in REFERENCE_VISUAL_SUMO_TYPEMAPS]
    missing = [str(path) for path in base_type_files if not path.exists()]
    if missing:
        raise ValueError("The selected visual profile requires SUMO type maps. Missing files: " + ", ".join(missing))
    service_type_file = logs_dir / f"{prefix}_service_no_pedestrian.typ.xml"
    _write_text(service_type_file, REFERENCE_VISUAL_SERVICE_TYPE_XML)
    type_files = [*base_type_files, service_type_file]
    executable = Path(shutil.which(str(netconvert_binary)) or netconvert_binary).resolve()
    adjacent = next((path for path in (executable.parent.parent / "data" / "typemap",
        executable.parent.parent / "share" / "sumo" / "data" / "typemap") if path.is_dir()), None)
    warnings = ([f"Configured SUMO type maps differ from the selected binary installation: {typemap_dir}; binary type maps: {adjacent}. Check their versions."]
                if adjacent is not None and adjacent.resolve() != typemap_dir.resolve() else [])
    return ["--type-files", ",".join(_command_path(path, root) for path in type_files)], warnings


def _forced_construction_type_options(
    root: Path,
    logs_dir: Path,
    prefix: str,
    *,
    explicit_sumo_home: Path | None = None,
    netconvert_binary: str = "netconvert",
) -> tuple[list[str], Path]:
    base_type_file = _sumo_typemap_directory(netconvert_binary, explicit_sumo_home) / OSM_BASE_SUMO_TYPEMAP
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


MULTIMODAL_NETCONVERT_PROFILE = "multimodal"
# Lanes only: no signal guessing or rebuild, although crossings and bike links still change the programs netconvert
# generates. The signal groups come from a vehicle-road build (_vehicle_signal_groups), not from --tls.join on the
# master. No --sidewalks.guess* (they duplicate separately mapped sidewalks) and no type maps (the Pedestrians map
# removes pedestrian access from untagged roads, the Bicycle map creates compound "cycleway.lane|highway.x" types).
MULTIMODAL_MODE_OPTIONS = {
    "bicycle": ("--osm.bike-access",),
    "pedestrian": ("--osm.sidewalks", "--osm.crossings", "--crossings.guess", "--walkingareas"),
}


def _multimodal_modes(normalized_profile: str, modes: str | Iterable[str] | None) -> list[str]:
    pieces = modes.split(",") if isinstance(modes, str) else list(modes or [])
    requested = {str(item).strip().lower() for item in pieces if str(item).strip()}
    if normalized_profile != MULTIMODAL_NETCONVERT_PROFILE:
        if requested:
            raise ValueError("multimodal_modes is only used by netconvert_profile='multimodal'")
        return []
    if modes is None:
        requested = set(MULTIMODAL_MODE_OPTIONS)
    unknown = sorted(requested - set(MULTIMODAL_MODE_OPTIONS))
    if unknown:
        raise ValueError("unsupported multimodal_modes: " + ", ".join(unknown))
    if not requested:
        raise ValueError("netconvert_profile='multimodal' needs bicycle, pedestrian or both in multimodal_modes")
    return sorted(requested)


def _netconvert_profile_options(profile: str | None, multimodal_modes: str | Iterable[str] | None = None) -> list[str]:
    normalized = (profile or "vehicle_core").strip().lower().replace("-", "_")
    if normalized in {"", "default", "vehicle", "vehicle_core"}:
        return []
    if normalized == MULTIMODAL_NETCONVERT_PROFILE:
        return [option for mode in _multimodal_modes(normalized, multimodal_modes)
                for option in MULTIMODAL_MODE_OPTIONS[mode]]
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


def _completed_elements(handle: Any) -> Iterator[ET.Element]:
    """Each element at its end, in the order of ET.iterparse, dropping every finished top-level element."""
    with handle:
        context = ET.iterparse(handle, events=("start", "end"))
        _, root = next(context)
        depth = 0
        for event, element in context:
            if event == "start":
                depth += 1
                continue
            yield element
            depth -= 1
            if depth == 0:
                root.remove(element)


_SPLIT_CONTROLLER_LIMIT = 20
# traffic_signals values for a light that turns red only to let pedestrians or cyclists cross.
_CROSSING_ONLY_SIGNAL_VALUES = frozenset({"crossing", "pedestrian_crossing", "cyclist_crossing", "hawk"})


def _tag_values(tags: Mapping[str, str], key: str) -> set[str]:
    return {value.strip() for value in (tags.get(key) or "").split(";") if value.strip()}


def _is_crossing_signal(tags: Mapping[str, str], road_neighbours: int) -> bool:
    """Whether a signal node serves a crossing, from its OSM tags and its neighbour count on the vehicle roads.

    ``highway=crossing`` is a crossing signal wherever it sits; such nodes on a junction, kept in their group, still
    gave 222-306 s programs on central Dresden extracts. The other crossing tags count only off a junction (at most two
    neighbours): a crossing-only ``traffic_signals`` value, ``crossing=traffic_signals`` or ``crossing:signals=yes``.
    On a junction they mark the junction's own signal, and so does ``crossing=traffic_signals`` on a
    ``highway=traffic_signals`` stop line with ``traffic_signals:direction``.
    """
    if tags.get("highway") == "crossing":
        return True
    if road_neighbours > 2:
        return False
    if _tag_values(tags, "traffic_signals") & _CROSSING_ONLY_SIGNAL_VALUES:
        return True
    marked = "traffic_signals" in _tag_values(tags, "crossing") or tags.get("crossing:signals") == "yes"
    return marked and not (tags.get("highway") == "traffic_signals" and "traffic_signals:direction" in tags)


def _carries_crossing(tags: Mapping[str, str]) -> bool:
    """Whether OSM puts a crossing on a signal node, wherever it sits (see _STOP_LINE_CROSSING_SIGNALS)."""
    return (tags.get("highway") == "crossing" or bool(_tag_values(tags, "crossing") - {"no"})
            or tags.get("crossing:signals") == "yes"
            or bool(_tag_values(tags, "traffic_signals") & _CROSSING_ONLY_SIGNAL_VALUES))


# Stop-line signal centres. OSM maps a signal at the stop line of each approach, so netconvert gives every approach
# its own controller and the junction itself stays a priority node. A junction (a node with 3+ road neighbours, or
# nodes of that kind linked by road chains of at most _STOP_LINE_CLUSTER_DISTANCE_M) whose external passenger
# approaches all, up to _STOP_LINE_SIGNAL_SLACK of them, carry a signal within _STOP_LINE_SIGNAL_DISTANCE_M upstream
# gets one controller "SL_<smallest node id>". Every signal within that distance on an approach and the signals on
# the chains inside the junction serve it and become priority nodes; a joined group gives up the members that serve
# a centre. Signals that carry a crossing (_is_crossing_signal, _carries_crossing, or a footway, cycleway or path
# through the node) follow _STOP_LINE_CROSSING_SIGNALS. An approach that ends at a dead end within _STOP_LINE_STUB_M
# (a road the extract cuts) does not count. When a cluster does not qualify, each of its nodes without a controller
# is tried alone. The distances run node to node, which at a wide junction is about 10 m more than along the lanes.
# The values are module constants: a request cannot set them.
_STOP_LINE_SIGNAL_DISTANCE_M = 36.0
_STOP_LINE_CLUSTER_DISTANCE_M = 30.0
_STOP_LINE_STUB_M = 30.0
_STOP_LINE_SIGNAL_SLACK = 0
# What happens to the crossing signals that served a centre: "keep" their own controller, "join" the centre's
# controller, or "demote" them to priority nodes like the stop lines. A kept crossing signal that blocks the centre
# is demoted by the series check below once the master shows it.
_STOP_LINE_CROSSING_SIGNALS = "keep"
_STOP_LINE_CENTRE_PREFIX = "SL_"
_STOP_LINE_CENTRE_LIMIT = 20
_RAIL_NODE_TYPES = frozenset({"rail_crossing", "rail_signal"})
# netconvert fits a program into its 90 s cycle only up to 7 sequential green groups; a multi-node centre gets one
# group per incoming edge, so 8 or more keep 31 s green and 3 s yellow each (272 s, 408 s on central Dresden). A centre
# above _STOP_LINE_CYCLE_LIMIT_S is replaced by its members that qualify alone, and a single-node centre above it is
# dropped. The vehicle-road check build catches most of them; the master catches the ones that its paths add.
_STOP_LINE_CYCLE_LIMIT_S = 90.0
_STOP_LINE_GUARD_ROUNDS = 3
# Two controllers in series on a vehicle lane with less than one car of storage between them act as one stop line
# whose green is the time both are green; with no common green at all the lane is blocked for good. When the common
# green is under _SERIES_MIN_COMMON_GREEN_SHARE of the shorter green, the signal node of the pair is demoted
# (_series_demotions) and the master rebuilt, at most _SIGNAL_REBUILD_ROUNDS times. On central Dresden, 0.5 left as
# many signalised crossings in the core as no centres at all (436 against 420) and cleared the 0.5x boundary load as
# well as 1.0 did (2-15 of 1224 vehicles unfinished against 4-12, 17-33 when only pairs without common green go).
_SERIES_STORAGE_M = 7.5
_SERIES_WALK_LANES = 4
_SERIES_HORIZON_CAP_S = 3600
_SERIES_MIN_COMMON_GREEN_SHARE = 0.5
_SIGNAL_REBUILD_ROUNDS = 6


class _PlainRoadGraph(NamedTuple):
    nodes: dict[str, tuple[float, float, str, str | None]]  # id: x, y, type, tl
    edges: dict[str, tuple[str, str, float, bool]]  # road edges only, id: from, to, length, allows passenger
    incoming: dict[str, list[str]]
    neighbours: dict[str, set[str]]
    pair_length: dict[tuple[str, str], float]
    controlled_into: dict[str, set[str]]  # edge id: controllers of the signalised connections into it


def _polyline_length(points: list[tuple[float, float]]) -> float:
    return sum(math.dist(a, b) for a, b in zip(points, points[1:]))


def _plain_road_graph(plain_prefix: Path) -> _PlainRoadGraph | None:
    """The vehicle roads of a plain netconvert output: edges whose lanes allow a vehicle-layer class."""
    from .network_layers import VEHICLE_LAYER_CLASSES
    from .osm_access import _permission_set

    node_path, edge_path, tll_path = (Path(f"{plain_prefix}.{suffix}.xml") for suffix in ("nod", "edg", "tll"))
    if not (node_path.is_file() and edge_path.is_file()):
        return None
    nodes: dict[str, tuple[float, float, str, str | None]] = {}
    for element in _completed_elements(node_path.open("rb")):
        if element.tag == "node":
            nodes[element.get("id")] = (float(element.get("x", 0)), float(element.get("y", 0)),
                                        element.get("type") or "", element.get("tl"))
    road_classes = frozenset(VEHICLE_LAYER_CLASSES)
    permissions: dict[tuple[str | None, str | None], frozenset[str]] = {}

    def allowed(attributes: Mapping[str, str]) -> frozenset[str]:
        key = (attributes.get("allow"), attributes.get("disallow"))
        if key not in permissions:
            permissions[key] = frozenset(_permission_set(attributes))
        return permissions[key]

    edges: dict[str, tuple[str, str, float, bool]] = {}
    for element in _completed_elements(edge_path.open("rb")):
        if element.tag != "edge" or element.get("from") not in nodes or element.get("to") not in nodes:
            continue
        lanes = [lane.attrib for lane in element.findall("lane")]
        classes = frozenset().union(*(allowed(lane if "allow" in lane or "disallow" in lane else element.attrib)
                                      for lane in lanes or [element.attrib]))
        if not classes & road_classes:
            continue
        start, end = (nodes[element.get(key)][:2] for key in ("from", "to"))
        if element.get("length"):
            length = float(element.get("length"))
        else:
            shape = [tuple(map(float, point.split(",")[:2])) for point in (element.get("shape") or "").split()]
            length = _polyline_length([start, *shape, end])
        edges[element.get("id")] = (element.get("from"), element.get("to"), length, "passenger" in classes)
    incoming: dict[str, list[str]] = {}
    neighbours: dict[str, set[str]] = {}
    pair_length: dict[tuple[str, str], float] = {}
    for edge_id, (start, end, length, _) in sorted(edges.items()):
        if start == end:
            continue
        incoming.setdefault(end, []).append(edge_id)
        neighbours.setdefault(start, set()).add(end)
        neighbours.setdefault(end, set()).add(start)
        for pair in ((start, end), (end, start)):
            pair_length[pair] = min(length, pair_length.get(pair, length))
    controlled_into: dict[str, set[str]] = {}
    if tll_path.is_file():
        for element in _completed_elements(tll_path.open("rb")):
            if element.tag == "connection" and element.get("tl") and element.get("to") in edges:
                controlled_into.setdefault(element.get("to"), set()).add(element.get("tl"))
    return _PlainRoadGraph(nodes, edges, incoming, neighbours, pair_length, controlled_into)


def _upstream_signals(graph: _PlainRoadGraph, edge_id: str, limit_m: float) -> tuple[str, float, list[tuple[str, float]]]:
    """Follow the traffic on ``edge_id`` back through two-neighbour nodes to the first other node.

    Returns that node, its distance along the road and the signals passed within ``limit_m``, nearest first: nodes
    with at most two neighbours whose signalised connections lead into the walked edge, with their distance to the
    end of ``edge_id``.
    """
    signals: list[tuple[str, float]] = []
    distance, seen = 0.0, set()
    while True:
        start, end, length, _ = graph.edges[edge_id]
        distance += length
        seen.add(edge_id)
        neighbour_count = len(graph.neighbours.get(start, ()))
        if neighbour_count <= 2 and distance <= limit_m and graph.controlled_into.get(edge_id):
            signals.append((start, distance))
        upstream = [row for row in graph.incoming.get(start, ())
                    if graph.edges[row][0] not in (end, start) and row not in seen]
        if neighbour_count != 2 or not upstream:
            return start, distance, signals
        edge_id = upstream[0]


def _chain_end(graph: _PlainRoadGraph, first: str, second: str, junctions: set[str], limit_m: float) -> tuple[str, float]:
    """The node where the road from ``first`` through ``second`` stops having two neighbours, and its distance."""
    previous, current, distance = first, second, graph.pair_length[(first, second)]
    while distance <= limit_m and current not in junctions and len(graph.neighbours.get(current, ())) == 2:
        following = next(iter(graph.neighbours[current] - {previous}))
        previous, current = current, following
        distance += graph.pair_length[(previous, current)]
    return current, distance


def _node_order(node_id: str) -> tuple[int, str]:
    return len(node_id), node_id


def _stop_line_centres(
    graph: _PlainRoadGraph,
    *,
    source_nodes: set[str],
    crossing_signals: set[str],
    grouped_signals: set[str],
    signal_distance_m: float,
    cluster_distance_m: float,
    slack: int,
    stub_m: float,
    split_clusters: set[str] | None = None,
    dropped_nodes: set[str] | None = None,
) -> dict[str, Any]:
    """Junction centres that the stop-line signals around them should control, and the signals that served them.

    Junction nodes linked by a road chain of at most ``cluster_distance_m`` form one cluster; every chain between two
    nodes of a cluster lies inside it. A signalised junction node takes part when it is a member of a joined group
    (``grouped_signals``) or a crossing signal, but it is never a centre on its own. Approaches are the road edges into
    the cluster from outside it, and only passenger approaches decide, as in netconvert's --tls.guess-signals: at
    least two signalised, at most ``slack`` without a signal and fewer missing than found. An approach whose walk
    ends at a dead end within ``stub_m`` does not count. When a cluster of several nodes does not qualify, each of
    its nodes is tried alone. The long-cycle guard passes the ids of the clusters to try node by node at once
    (``split_clusters``) and the nodes that are never a centre on their own (``dropped_nodes``).
    """
    split_clusters, dropped_nodes = split_clusters or set(), dropped_nodes or set()
    junctions = {node_id for node_id, (_, _, node_type, tl) in graph.nodes.items()
                 if node_type not in _RAIL_NODE_TYPES and len(graph.neighbours.get(node_id, ())) >= 3
                 and (tl is None or node_id in grouped_signals or node_id in crossing_signals)}
    parent = {node_id: node_id for node_id in junctions}

    def find(node_id: str) -> str:
        while parent[node_id] != node_id:
            parent[node_id] = parent[parent[node_id]]
            node_id = parent[node_id]
        return node_id

    for node_id in sorted(junctions):
        for neighbour in sorted(graph.neighbours[node_id]):
            end, distance = _chain_end(graph, node_id, neighbour, junctions, cluster_distance_m)
            if end != node_id and end in junctions and distance <= cluster_distance_m:
                parent[find(end)] = find(node_id)
    clusters: dict[str, list[str]] = {}
    for node_id in junctions:
        clusters.setdefault(find(node_id), []).append(node_id)
    limit_m = max(signal_distance_m, cluster_distance_m, stub_m)

    def centre(members: list[str]) -> tuple[dict[str, Any] | None, str]:
        inside = set(members)
        approaches, inner, stubs = [], [], []
        for node_id in members:
            for edge_id in graph.incoming.get(node_id, ()):
                if graph.edges[edge_id][0] == node_id:
                    continue
                end, distance, signals = _upstream_signals(graph, edge_id, limit_m)
                if end in inside:
                    inner.extend(node for node, _ in signals)
                    continue
                if len(graph.neighbours.get(end, ())) == 1 and distance <= stub_m:
                    stubs.append(edge_id)
                    continue
                within = [(node, round(at, 2)) for node, at in signals if at <= signal_distance_m]
                approaches.append({"edge": edge_id, "junction": node_id, "passenger": graph.edges[edge_id][3],
                                   "signal": within[0][0] if within else None,
                                   "signal_distance_m": within[0][1] if within else None,
                                   "signals": [node for node, _ in within]})
        passenger = [row for row in approaches if row["passenger"]]
        found = sum(1 for row in passenger if row["signal"])
        missing = len(passenger) - found
        if found < 2 or missing > slack or missing >= found:
            return None, ("no_signalised_approach" if not found else "unsignalised_approach" if missing
                          else "fewer_than_two_signalised_approaches")
        if not inside <= source_nodes:
            return None, "node_not_in_source_osm"
        served = sorted(({node for row in approaches for node in row["signals"]} | set(inner)) - inside, key=_node_order)
        return {
            "tl": _STOP_LINE_CENTRE_PREFIX + members[0], "nodes": members,
            "diameter_m": round(max((math.dist(graph.nodes[a][:2], graph.nodes[b][:2])
                                     for a in members for b in members), default=0.0), 2),
            "passenger_approach_count": len(passenger), "signalised_passenger_approach_count": found,
            "approaches": approaches, "dead_end_stub_edges": stubs,
            "inner_signal_nodes": sorted(set(inner), key=_node_order),
            "signalised_member_nodes": [node for node in members if graph.nodes[node][3]],
            "demoted_signal_nodes": [node for node in served if node in source_nodes and node not in crossing_signals],
            "crossing_signal_nodes": [node for node in served if node in source_nodes and node in crossing_signals],
            "grouped_signal_nodes": [node for node in served if node in grouped_signals],
        }, ""

    centres, rejected, fallback, guarded = [], Counter(), 0, []
    for members in sorted((sorted(rows, key=_node_order) for rows in clusters.values()),
                          key=lambda rows: _node_order(rows[0])):
        tl_id = _STOP_LINE_CENTRE_PREFIX + members[0]
        if len(members) == 1 and members[0] in dropped_nodes:
            guarded.append({"tl": tl_id, "nodes": members, "action": "dropped"})
            continue
        row, reason = (None, "long_cycle") if tl_id in split_clusters else centre(members)
        if row is not None:
            centres.append(row)
            continue
        rejected[reason] += 1
        if len(members) > 1:
            # A cluster that does not qualify as a whole: each of its nodes may still qualify alone. A node that
            # already has a controller keeps it.
            singles = [row for row, _ in (centre([node_id]) for node_id in members
                                          if graph.nodes[node_id][3] is None and node_id not in dropped_nodes)
                       if row is not None]
            centres.extend(singles)
            fallback += len(singles)
            if tl_id in split_clusters:
                guarded.append({"tl": tl_id, "nodes": members, "action": "single_nodes",
                                "single_centres": [row["tl"] for row in singles]})
    # A signal that served two centres is demoted once.
    demoted = sorted({node for row in centres for node in row["demoted_signal_nodes"]}, key=_node_order)
    return {
        "signal_distance_m": signal_distance_m, "cluster_distance_m": cluster_distance_m, "slack": slack,
        "stub_m": stub_m, "junction_node_count": len(junctions), "junction_cluster_count": len(clusters),
        "multi_node_junction_cluster_count": sum(1 for rows in clusters.values() if len(rows) > 1),
        "centre_count": len(centres), "multi_node_centre_count": sum(1 for row in centres if len(row["nodes"]) > 1),
        "single_node_fallback_centre_count": fallback,
        "centre_node_count": sum(len(row["nodes"]) for row in centres),
        "demoted_signal_node_count": len(demoted),
        "served_crossing_signal_node_count": len({node for row in centres for node in row["crossing_signal_nodes"]}),
        "served_grouped_signal_node_count": len({node for row in centres for node in row["grouped_signal_nodes"]}),
        "rejected_cluster_counts": dict(sorted(rejected.items())), "long_cycle_fallbacks": guarded,
        "centres": centres, "demoted_signal_nodes": demoted,
    }


def _program_cycles(path: Path, prefix: str) -> dict[str, float]:
    """Cycle of every program in a net or plain tll file whose controller id starts with ``prefix``."""
    cycles: dict[str, float] = {}
    for element in _completed_elements(path.open("rb")):
        if element.tag == "tlLogic" and element.get("id", "").startswith(prefix):
            cycles[element.get("id")] = round(sum(float(phase.get("duration", "0"))
                                                  for phase in element.findall("phase")), 6)
    return cycles


def _signal_node_rows(
    patched: list[tuple[str, str, str]], centres: list[dict[str, Any]], demoted: Iterable[str],
) -> tuple[list[tuple[str, str, str | None]], list[str]]:
    """Node file rows: joined groups without the members that serve a centre or are demoted, centres, demotions.

    A joined group left with fewer than two members dissolves, and its last member keeps its own controller.
    """
    centre_nodes = {node_id: row["tl"] for row in centres for node_id in row["nodes"]}
    demoted = set(demoted) - set(centre_nodes)
    groups: dict[str, list[tuple[str, str]]] = {}
    for node_id, node_type, tl_id in patched:
        if node_id not in demoted and node_id not in centre_nodes:
            groups.setdefault(tl_id, []).append((node_id, node_type))
    dissolved = sorted({tl_id for _, _, tl_id in patched} - {tl_id for tl_id, rows in groups.items() if len(rows) > 1})
    rows: list[tuple[str, str, str | None]] = [
        (node_id, node_type, tl_id) for tl_id, members in groups.items() if len(members) > 1
        for node_id, node_type in members]
    rows += [(node_id, "traffic_light", tl_id) for node_id, tl_id in centre_nodes.items()]
    rows += [(node_id, "priority", None) for node_id in sorted(demoted, key=_node_order)]
    return rows, dissolved


def _series_demotions(
    graph: _PlainRoadGraph, traps: Iterable[Mapping[str, Any]], *, source_nodes: set[str], centre_nodes: set[str],
) -> tuple[list[str], list[dict[str, Any]]]:
    """The signal node to demote for each series trap of the master, and the traps that keep both signals.

    A node can be demoted when it is not a centre node, has at most two road neighbours (a stop line or crossing
    off the junction) and comes from the source OSM. When both can, the downstream one goes: drivers keep the
    first stop line they meet.
    """
    demoted: set[str] = set()
    unresolved = []
    for trap in traps:
        candidates = [trap[key] for key in ("downstream_node", "upstream_node")
                      if trap[key] in source_nodes and trap[key] not in centre_nodes
                      and len(graph.neighbours.get(trap[key], ())) <= 2]
        if candidates:
            demoted.add(candidates[0])
        else:
            unresolved.append(dict(trap))
    return sorted(demoted, key=_node_order), unresolved


def _series_signal_traps(net_file: Path) -> dict[str, Any]:
    """Pairs of controllers in series on a vehicle lane with less than _SERIES_STORAGE_M between their stop lines.

    From each signalised vehicle link the walk follows the lane it leads into, and further through unsignalised
    connections, while the summed length of these normal lanes stays below _SERIES_STORAGE_M and at most
    _SERIES_WALK_LANES lanes; a signalised link of another controller on the way closes a pair. The two links'
    greens (G or g, per second over the least common multiple of both cycles, capped at _SERIES_HORIZON_CAP_S) are
    compared: a pair is a trap when both links turn green but are green together for less than
    _SERIES_MIN_COMMON_GREEN_SHARE of the shorter green.
    """
    storage_m, walk_lanes = _SERIES_STORAGE_M, _SERIES_WALK_LANES
    min_share, horizon_cap_s = _SERIES_MIN_COMMON_GREEN_SHARE, _SERIES_HORIZON_CAP_S
    from .network_layers import VEHICLE_LAYER_CLASSES
    from .osm_access import _permission_set

    vehicle_classes = frozenset(VEHICLE_LAYER_CLASSES)
    lane_length: dict[str, float] = {}
    edge_to: dict[str, str] = {}
    out: dict[str, list[tuple[str, str | None, int]]] = {}
    programs: dict[str, tuple[int, list[str]]] = {}
    for element in _completed_elements(net_file.open("rb")):
        if element.tag == "edge" and not element.get("function"):
            edge_to[element.get("id")] = element.get("to")
            for lane in element.findall("lane"):
                attributes = lane.attrib if "allow" in lane.attrib or "disallow" in lane.attrib else element.attrib
                if _permission_set(attributes) & vehicle_classes:
                    lane_length[lane.get("id")] = float(lane.get("length", "0"))
        elif element.tag == "tlLogic" and element.get("id") not in programs:
            seconds = [phase.get("state", "") for phase in element.findall("phase")
                       for _ in range(max(1, round(float(phase.get("duration", "0")))))]
            offset = round(float(element.get("offset", "0") or 0))
            programs[element.get("id")] = (offset, seconds)
        elif element.tag == "connection" and element.get("from") in edge_to:
            from_lane = f"{element.get('from')}_{element.get('fromLane')}"
            to_lane = f"{element.get('to')}_{element.get('toLane')}"
            if from_lane in lane_length and to_lane in lane_length:
                out.setdefault(from_lane, []).append(
                    (to_lane, element.get("tl"), int(element.get("linkIndex", "-1"))))
    green_cache: dict[tuple[str, int], list[bool]] = {}

    def green(tl_id: str, index: int) -> list[bool]:
        if (tl_id, index) not in green_cache:
            _, seconds = programs[tl_id]
            green_cache[(tl_id, index)] = [len(state) > index and state[index] in "Gg" for state in seconds]
        return green_cache[(tl_id, index)]

    pairs: dict[tuple[str, int, str, int], dict[str, Any]] = {}
    for from_lane, links in out.items():
        for to_lane, tl_id, index in links:
            if not tl_id or tl_id not in programs or lane_length[to_lane] >= storage_m:
                continue
            frontier = [(to_lane, lane_length[to_lane], 1)]
            while frontier:
                lane, storage, depth = frontier.pop()
                for next_lane, next_tl, next_index in out.get(lane, ()):
                    if next_tl and next_tl != tl_id and next_tl in programs:
                        key = (tl_id, index, next_tl, next_index)
                        if key not in pairs or storage < pairs[key]["storage_m"]:
                            pairs[key] = {"upstream_tl": tl_id, "upstream_link": index,
                                          "upstream_node": edge_to[from_lane.rsplit("_", 1)[0]],
                                          "upstream_lane": from_lane, "downstream_tl": next_tl,
                                          "downstream_link": next_index, "downstream_lane": lane,
                                          "downstream_node": edge_to[lane.rsplit("_", 1)[0]],
                                          "storage_m": round(storage, 2)}
                    elif not next_tl and depth < walk_lanes and storage + lane_length[next_lane] < storage_m:
                        frontier.append((next_lane, storage + lane_length[next_lane], depth + 1))
    traps = []
    for (up_tl, up_index, down_tl, down_index), row in sorted(pairs.items()):
        up, down = green(up_tl, up_index), green(down_tl, down_index)
        up_offset, down_offset = programs[up_tl][0], programs[down_tl][0]
        horizon = min(math.lcm(len(up), len(down)), horizon_cap_s)
        up_green = down_green = common = 0
        for second in range(horizon):
            a, b = up[(second - up_offset) % len(up)], down[(second - down_offset) % len(down)]
            up_green, down_green, common = up_green + a, down_green + b, common + (a and b)
        shorter = min(up_green, down_green)
        row.update(horizon_s=horizon, upstream_green_s=up_green, downstream_green_s=down_green, common_green_s=common)
        if shorter and common < min_share * shorter:
            traps.append(row)
    return {"storage_m": storage_m, "min_common_green_share": min_share, "pair_count": len(pairs),
            "trap_count": len(traps), "no_common_green_count": sum(1 for row in traps if not row["common_green_s"]),
            "traps": traps}


def _vehicle_signal_groups(
    *,
    filtered_osm: Path,
    root: Path,
    work_dir: Path,
    allowed_highways: set[str],
    tls_join_distance_m: float,
    input_options: list[str],
    include_railway: bool,
    allowed_railways: set[str] | None,
    netconvert_binary: str,
    command_runner: Callable[..., Any],
    timeout_seconds: float,
    split_clusters: Iterable[str] = (),
    dropped_nodes: Iterable[str] = (),
    long_centres: Iterable[str] = (),
    series_traps: Iterable[Mapping[str, Any]] = (),
    joins: Iterable[Iterable[str]] = (),
) -> dict[str, Any]:
    """Signal groups of a vehicle-road build of the same input, as a node file for the multimodal master.

    netconvert's --tls.join clusters signals across short edges of any class. In a multimodal build the short
    footway, cycleway and crossing segments link neighbouring junctions, so whole blocks become one controller.
    The vehicle-road build runs the same join on the vehicle highways only; every node whose controller id differs
    from its own id gets that id in the node file, which the master loads instead of --tls.join.

    Crossing signals (``_is_crossing_signal``, neighbours counted on the reference OSM's highway ways) stay out of
    every group and keep their own controller. On vehicle roads only they control no conflict, so the join takes
    them in by distance alone. In the master each brings crossings and bike links, and netconvert gives every signal
    group of a joined program its own green. Central Dresden: Georgplatz joined 9 junction and 8 crossing signals
    into a 612 s, 54-phase program, 90 s without the crossings. A joined controller keeps the reference build's id
    while two or more of its signals remain; a group left with one signal dissolves, and that signal keeps its own.

    The same file carries the stop-line centres (_stop_line_centres) and the signals they demote. A check build of
    the vehicle roads with the node file shows each centre's program; a centre above _STOP_LINE_CYCLE_LIMIT_S is
    split into its nodes or dropped. The master check passes back the centres it found long (``long_centres``),
    the fallbacks already made (``split_clusters``, ``dropped_nodes``) and its series traps (``series_traps``).
    ``joins`` are split-junction groups (split_junctions.split_junction_groups) the node file joins.
    """
    from .road_scope import HIGHWAY_CLASS_PRESETS

    highways = sorted(set(allowed_highways) & HIGHWAY_CLASS_PRESETS["full_vehicle"])
    report: dict[str, Any] = {"source": "vehicle_reference_build", "reference_highways": highways,
                              "tls_join_distance_m": tls_join_distance_m, "node_file": None}
    if not highways:
        return {**report, "status": "no_effect", "reason": "no_vehicle_highways"}
    reference_osm = work_dir / "vehicle_reference.osm.xml.gz"
    plain_prefix = work_dir / "vehicle_reference"
    filter_osm_by_highways(filtered_osm, reference_osm, set(highways), include_railway=include_railway,
                           allowed_railways=allowed_railways)
    command = [netconvert_binary, "--osm-files", _relative_to_root(reference_osm, root),
               "--plain-output-prefix", _relative_to_root(plain_prefix, root), *input_options,
               "--no-internal-links", "--tls.join", "--tls.join-dist", f"{tls_join_distance_m:g}"]
    result = _result_to_dict(command_runner(command, cwd=root, timeout_seconds=timeout_seconds))
    report.update(reference_osm={"path": str(reference_osm.resolve()), "sha256": _file_sha256(reference_osm)},
                  command=command, returncode=result.get("returncode"))
    plain_nodes = Path(f"{plain_prefix}.nod.xml")
    controllers: dict[str, list[tuple[str, str]]] = {}
    try:
        if result.get("status") != "pass" or not plain_nodes.is_file():
            raise ET.ParseError("no plain node output")
        for element in _completed_elements(plain_nodes.open("rb")):
            if element.tag == "node" and element.get("tl"):
                controllers.setdefault(element.get("tl"), []).append(
                    (element.get("id"), element.get("type") or "traffic_light"))
    except ET.ParseError:
        return {**report, "status": "blocked", "reason": "vehicle_signal_reference_build_failed"}
    members = {node_id for rows in controllers.values() for node_id, _ in rows}
    source_nodes: set[str] = set()
    member_tags: dict[str, dict[str, str]] = {}
    neighbours: dict[str, set[str]] = {node_id: set() for node_id in members}
    for element in _completed_elements(_open_xml(reference_osm, "rt")):
        if element.tag == "node":
            source_nodes.add(element.get("id"))
            if element.get("id") in members:
                member_tags[element.get("id")] = {tag.get("k"): tag.get("v") for tag in element.findall("tag")}
        elif element.tag == "way" and any(tag.get("k") == "highway" for tag in element.findall("tag")):
            refs = [nd.get("ref") for nd in element.findall("nd")]
            for index, ref in enumerate(refs):
                if ref in members:
                    neighbours[ref].update(refs[max(index - 1, 0):index] + refs[index + 1:index + 2])
    crossing_signals = {node_id for node_id, tags in member_tags.items()
                        if _is_crossing_signal(tags, len(neighbours[node_id] - {node_id}))}
    patched, skipped, split = [], [], []
    for tl_id, members in sorted(controllers.items()):
        if all(node_id == tl_id for node_id, _ in members):
            continue
        missing = sorted(node_id for node_id, _ in members if node_id not in source_nodes)
        if missing or "," in tl_id:
            skipped.append({"tl": tl_id, "reason": "node_not_in_source_osm" if missing else "node_has_several_controllers",
                            "nodes": missing or [node_id for node_id, _ in members]})
            continue
        kept = [(node_id, node_type) for node_id, node_type in members if node_id not in crossing_signals]
        if len(kept) < len(members):
            split.append({"tl": tl_id, "crossing_signal_nodes": [node_id for node_id, _ in members
                                                                 if node_id in crossing_signals],
                          "junction_signal_nodes": [node_id for node_id, _ in kept], "dissolved": len(kept) < 2})
        if len(kept) > 1:
            patched.extend((node_id, node_type, tl_id) for node_id, node_type in kept)
    report.update(controller_count=len(controllers), joined_controller_count=len({tl for _, _, tl in patched}),
                  patched_node_count=len(patched), skipped_controllers=skipped,
                  crossing_signal_node_count=sum(len(row["crossing_signal_nodes"]) for row in split),
                  split_controller_count=len(split), split_controllers=split[:_SPLIT_CONTROLLER_LIMIT])
    centres: list[dict[str, Any]] = []
    rows: list[tuple[str, str, str | None]] = list(patched)
    joins = [sorted(nodes) for nodes in joins]
    split_clusters, dropped_nodes = set(split_clusters or ()), set(dropped_nodes or ())
    node_file = work_dir / "vehicle_signal_groups.nod.xml"
    graph = _plain_road_graph(plain_prefix)
    if graph is None:
        report["stop_line_centres"] = {"status": "blocked", "reason": "reference_plain_edges_missing"}
    else:
        kept_signals = {node_id for node_id, _, _ in patched} | {
            node_id for row in skipped for node_id, _ in controllers[row["tl"]]}
        # A footway, cycleway or path through a signal node gives it a crossing in the master as well.
        signal_nodes = {node_id for rows in controllers.values() for node_id, _ in rows}
        on_paths: set[str] = set()
        for element in _completed_elements(_open_xml(filtered_osm, "rt")):
            if element.tag == "way":
                highway = next((tag.get("v") for tag in element.findall("tag") if tag.get("k") == "highway"), None)
                if highway and highway not in HIGHWAY_CLASS_PRESETS["full_vehicle"]:
                    on_paths.update(nd.get("ref") for nd in element.findall("nd") if nd.get("ref") in signal_nodes)
        tagged = crossing_signals | {node_id for node_id, tags in member_tags.items() if _carries_crossing(tags)}
        crossings = tagged | on_paths
        check_prefix = work_dir / "vehicle_reference_check"
        check_command = [netconvert_binary, "--osm-files", _relative_to_root(reference_osm, root),
                         "--plain-output-prefix", _relative_to_root(check_prefix, root), *input_options,
                         "--no-internal-links", "--node-files", _relative_to_root(node_file, root)]
        pending, guard_rounds = set(long_centres or ()), []
        for guard_round in range(_STOP_LINE_GUARD_ROUNDS + 1):
            found = _stop_line_centres(graph, source_nodes=source_nodes, crossing_signals=crossings,
                                       grouped_signals=kept_signals, signal_distance_m=_STOP_LINE_SIGNAL_DISTANCE_M,
                                       cluster_distance_m=_STOP_LINE_CLUSTER_DISTANCE_M,
                                       slack=_STOP_LINE_SIGNAL_SLACK, stub_m=_STOP_LINE_STUB_M,
                                       split_clusters=split_clusters, dropped_nodes=dropped_nodes)
            centres = found.pop("centres")
            by_tl = {row["tl"]: row for row in centres}
            centre_nodes = {node_id for row in centres for node_id in row["nodes"]}
            served_crossings = {node_id for row in centres for node_id in row["crossing_signal_nodes"]}
            series_demoted, unresolved = _series_demotions(graph, series_traps or (), source_nodes=source_nodes,
                                                           centre_nodes=centre_nodes)
            demoted = {*found["demoted_signal_nodes"], *series_demoted,
                       *(served_crossings if _STOP_LINE_CROSSING_SIGNALS == "demote" else ())}
            rows, dissolved = _signal_node_rows(patched, centres, demoted)
            _write_signal_node_file(node_file, rows, joins)
            long = {tl_id for tl_id in pending if tl_id in by_tl}
            pending = set()
            if not long and centres and guard_round < _STOP_LINE_GUARD_ROUNDS:
                # The vehicle-road build with the node file shows the centre programs netconvert makes.
                checked = _result_to_dict(command_runner(check_command, cwd=root, timeout_seconds=timeout_seconds))
                check_tll = Path(f"{check_prefix}.tll.xml")
                cycles = (_program_cycles(check_tll, _STOP_LINE_CENTRE_PREFIX)
                          if checked.get("status") == "pass" and check_tll.is_file() else {})
                long = {tl_id for tl_id, cycle in cycles.items() if cycle > _STOP_LINE_CYCLE_LIMIT_S and tl_id in by_tl}
                guard_rounds.append({"returncode": checked.get("returncode"), "centre_programs": len(cycles),
                                     "long_centres": {tl_id: cycles[tl_id] for tl_id in sorted(long)}})
            if not long or guard_round == _STOP_LINE_GUARD_ROUNDS:
                break
            for tl_id in long:
                if len(by_tl[tl_id]["nodes"]) > 1:
                    split_clusters.add(tl_id)
                else:
                    dropped_nodes.add(by_tl[tl_id]["nodes"][0])
        found.update(kept_path_crossing_signal_node_count=len(served_crossings - tagged),
                     cycle_limit_s=_STOP_LINE_CYCLE_LIMIT_S, guard_rounds=guard_rounds,
                     split_clusters=sorted(split_clusters, key=_node_order),
                     dropped_nodes=sorted(dropped_nodes, key=_node_order),
                     series_demoted_signal_nodes=series_demoted, unresolved_series_traps=unresolved,
                     dissolved_joined_controllers=dissolved)
        detail_file = work_dir / "stop_line_centres.json"
        write_json_atomic(detail_file, {**found, "centres": centres})
        report["stop_line_centres"] = {
            "status": "measured" if centres else "no_effect",
            **{key: value for key, value in found.items()
               if key not in ("demoted_signal_nodes", "series_demoted_signal_nodes", "unresolved_series_traps",
                              "long_cycle_fallbacks")},
            "long_cycle_fallback_count": len(found["long_cycle_fallbacks"]),
            "series_demoted_signal_node_count": len(series_demoted), "unresolved_series_trap_count": len(unresolved),
            "crossing_signals": _STOP_LINE_CROSSING_SIGNALS, "detail_file": str(detail_file.resolve()),
            "centres": [{key: row[key] for key in ("tl", "nodes", "passenger_approach_count",
                                                   "signalised_passenger_approach_count", "demoted_signal_nodes",
                                                   "crossing_signal_nodes")}
                        for row in centres[:_STOP_LINE_CENTRE_LIMIT]]}
        report.update(demoted_signal_node_count=sum(1 for _, node_type, tl_id in rows if tl_id is None),
                      demoted_crossing_signal_node_count=sum(
                          1 for node_id, _, tl_id in rows if tl_id is None and node_id in crossings),
                      kept_crossing_signal_node_count=len(served_crossings - {node_id for node_id, _, tl_id in rows
                                                                             if tl_id is None}))
    report.update(centre_controller_count=len(centres), centre_node_count=sum(len(row["nodes"]) for row in centres),
                  split_clusters=sorted(split_clusters, key=_node_order),
                  dropped_nodes=sorted(dropped_nodes, key=_node_order), joins=joins)
    if not rows and not joins:
        reason = "every_joined_group_dissolved" if split else "no_joined_vehicle_signals"
        return {**report, "status": "no_effect", "reason": reason}
    if graph is None:
        _write_signal_node_file(node_file, rows, joins)
    return {**report, "status": "measured", "node_file": str(node_file.resolve()),
            "node_file_sha256": _file_sha256(node_file)}


def _write_signal_node_file(
    path: Path, rows: Iterable[tuple[str, str, str | None]], joins: Iterable[Iterable[str]] = (),
) -> None:
    nodes = ET.Element("nodes")
    for node_id, node_type, tl_id in rows:
        ET.SubElement(nodes, "node", {"id": node_id, "type": node_type, **({"tl": tl_id} if tl_id else {})})
    for group in joins:
        ET.SubElement(nodes, "join", {"nodes": " ".join(group)})
    ET.indent(nodes)
    path.write_bytes(ET.tostring(nodes, encoding="utf-8", xml_declaration=True))


_SIGNAL_TYPES = frozenset({"traffic_light", "traffic_light_right_on_red", "traffic_light_unregulated"})


def _signal_node_file_master_check(node_file: Path, net_file: Path) -> dict[str, Any]:
    """Whether every row of the signal node file took effect in the master, its centre cycles, crossings and traps.

    A row with a ``tl`` took effect when its node is a signal junction and a connection from an edge ending there
    carries that controller; a priority row, when its node is not a signal junction. A demoted row was a signal node
    in the reference build, so its crossings would have been signalised without the node file; a centre node was a
    priority node, so its signalised crossings are new. ``series_traps`` lists every pair of _series_signal_traps.

    A row whose node a <join> row joined took effect when the joined junction exists and, for a row with a ``tl``,
    is a signal junction (netconvert names its controller after the cluster). The programs of joined junctions that
    took a centre are checked against the cycle limit like the centres.
    """
    from .split_junctions import cluster_id

    root = ET.parse(node_file).getroot()
    rows = [(element.get("id"), element.get("type"), element.get("tl")) for element in root if element.tag == "node"]
    joined_into = {node_id: cluster_id(element.get("nodes", "").split())
                   for element in root if element.tag == "join" for node_id in element.get("nodes", "").split()}
    wanted = {node_id for node_id, _, _ in rows} | set(joined_into.values())
    edge_to: dict[str, str] = {}
    crossing_edges: set[str] = set()
    junctions: dict[str, tuple[str, list[str]]] = {}
    controllers_at: dict[str, set[str]] = {}
    controlled_crossings: set[str] = set()
    for element in _completed_elements(net_file.open("rb")):
        if element.tag == "edge":
            if element.get("function") == "crossing":
                crossing_edges.add(element.get("id"))
            elif not element.get("function"):
                edge_to[element.get("id")] = element.get("to")
        elif element.tag == "junction" and element.get("id") in wanted:
            junctions[element.get("id")] = (element.get("type") or "", (element.get("intLanes") or "").split())
        elif element.tag == "connection" and element.get("tl"):
            node_id = edge_to.get(element.get("from"))
            if node_id in wanted:
                controllers_at.setdefault(node_id, set()).add(element.get("tl"))
            for key in ("from", "to"):
                if element.get(key) in crossing_edges:
                    controlled_crossings.add(element.get(key))

    def crossings(node_id: str) -> set[str]:
        return {lane.rsplit("_", 1)[0] for lane in junctions.get(node_id, ("", []))[1]} & crossing_edges

    not_applied, lost, gained, centre_clusters = [], set(), set(), set()
    for node_id, node_type, tl_id in rows:
        target = joined_into.get(node_id, node_id)
        found_type = junctions.get(target, (None, []))[0]
        found_tls = sorted(controllers_at.get(target, ()))
        if target != node_id:
            applied = found_type in _SIGNAL_TYPES if tl_id else found_type is not None
            if tl_id and tl_id.startswith(_STOP_LINE_CENTRE_PREFIX):
                centre_clusters.add(target)
        else:
            applied = (found_type in _SIGNAL_TYPES and tl_id in found_tls) if tl_id else (
                found_type is not None and found_type not in _SIGNAL_TYPES)
        if not applied:
            not_applied.append({"node": node_id, "type": node_type, "tl": tl_id, "found_type": found_type,
                                "found_tls": found_tls, **({"joined_into": target} if target != node_id else {})})
        if target != node_id:
            continue
        if tl_id is None:
            lost |= crossings(node_id)
        elif tl_id.startswith(_STOP_LINE_CENTRE_PREFIX):
            gained |= crossings(node_id) & controlled_crossings
    cycles = _program_cycles(net_file, _STOP_LINE_CENTRE_PREFIX)
    cluster_cycles = {tl_id: cycle for tl_id, cycle in _program_cycles(net_file, "cluster_").items()
                      if tl_id in centre_clusters}
    longest = sorted({**cycles, **cluster_cycles}.items(), key=lambda row: (-row[1], row[0]))
    series = _series_signal_traps(net_file)
    return {
        "status": "review_required" if not_applied else "pass",
        **({"reason": "node_file_rows_not_applied"} if not_applied else {}),
        "node_file_row_count": len(rows), "not_applied_count": len(not_applied),
        "missing_node_count": sum(1 for row in not_applied if row["found_type"] is None),
        "join_count": len(set(joined_into.values())), "joined_row_count": sum(1 for row in rows if row[0] in joined_into),
        "missing_join_count": len(set(joined_into.values()) - set(junctions)),
        "not_applied": not_applied[:_STOP_LINE_CENTRE_LIMIT],
        "crossings_unsignalised_at_demoted_nodes": len(lost),
        "crossings_signalised_at_centres": len(gained),
        "signalised_crossing_count": len(controlled_crossings),
        "centre_controller_count": len(cycles) + len(cluster_cycles),
        "centre_cycle_max_s": longest[0][1] if longest else None,
        "long_centre_cycles": {tl_id: cycle for tl_id, cycle in longest
                               if cycle > _STOP_LINE_CYCLE_LIMIT_S and tl_id in cycles},
        "long_joined_cycles": {tl_id: cycle for tl_id, cycle in cluster_cycles.items()
                               if cycle > _STOP_LINE_CYCLE_LIMIT_S},
        "series_pair_count": series["pair_count"], "series_trap_count": series["trap_count"],
        "series_no_common_green_count": series["no_common_green_count"],
        "series_traps": series["traps"],
    }


def _settle_master_signals(
    signal_grouping: dict[str, Any], result: dict[str, Any], *, net_file: Path, osm_file: Path,
    regroup: Callable[..., dict[str, Any]], rebuild: Callable[[], dict[str, Any]],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Check the master against its signal node file and rebuild it while the check finds something to change.

    The vehicle-road build cannot show the green groups that paths and crossings add, nor the programs of crossing
    signals, so each master is streamed: a centre program above the cycle limit gets the long-cycle fallback and a
    series trap the demotion of _series_demotions. Once no centre is long, the split junctions of the master
    (split_junctions.split_junction_groups) are joined; the next master drops every join that changed an OSM turn
    restriction or gave a centre a long program. The master is rebuilt with the new node file at most
    _SIGNAL_REBUILD_ROUNDS times; what the last check still finds stays in the report.
    """
    from .split_junctions import (cluster_id, osm_turn_restrictions, read_net, restriction_changes,
                                  split_junction_groups)

    rounds: list[dict[str, Any]] = []
    traps_seen: dict[tuple[str, ...], dict[str, Any]] = {}
    long_seen: set[str] = set()
    joins: list[list[str]] = []
    join_state, joining, base_view, restrictions = "pending", {"status": "not_run"}, None, []
    while result.get("status") == "pass" and net_file.is_file():
        check = _signal_node_file_master_check(Path(signal_grouping["node_file"]), net_file)
        traps = check.pop("series_traps")
        traps_file = Path(signal_grouping["node_file"]).with_name(f"master_series_traps_round{len(rounds) + 1}.json")
        write_json_atomic(traps_file, {"net_file": str(net_file.resolve()), "traps": traps})
        new_traps = {key: row for row in traps if (key := tuple(row[name] for name in (
            "upstream_node", "downstream_node", "upstream_lane", "downstream_lane"))) not in traps_seen}
        long = sorted(set(check["long_centre_cycles"]) - long_seen)
        joins_changed = False
        if join_state == "guard":
            changes = restriction_changes(base_view, read_net(net_file), joins, restrictions)
            dropped = {cluster_id(row["group"]) for row in changes} | set(check["long_joined_cycles"])
            joining.update(restriction_change_count=len(changes), restriction_changes=changes[:_STOP_LINE_CENTRE_LIMIT],
                           long_joined_cycles=check["long_joined_cycles"], dropped_joins=sorted(dropped))
            if dropped:
                joins = [nodes for nodes in joins if cluster_id(nodes) not in dropped]
                joins_changed = True
            joining["join_count"] = len(joins)
            join_state, base_view = "done", None
        elif join_state == "pending" and not long:
            base_view = read_net(net_file)
            found = split_junction_groups(base_view)
            joins = [row["nodes"] for row in found["groups"]]
            joining = {"status": "measured" if joins else "no_effect", "edge_m": found["edge_m"],
                       "span_m": found["span_m"], "linker_edge_count": found["linker_edge_count"],
                       "join_count": len(joins), "joined_node_count": sum(len(nodes) for nodes in joins),
                       "skipped_groups": found["skipped"][:_STOP_LINE_CENTRE_LIMIT],
                       "skipped_group_count": len(found["skipped"])}
            if joins:
                restrictions = osm_turn_restrictions(osm_file)
                join_state, joins_changed = "guard", True
            else:
                join_state, base_view = "done", None
        rounds.append({"round": len(rounds) + 1, "node_file_sha256": signal_grouping["node_file_sha256"], **check,
                       "new_series_trap_count": len(new_traps), "series_traps_file": str(traps_file.resolve())})
        if not (new_traps or long or joins_changed) or len(rounds) > _SIGNAL_REBUILD_ROUNDS:
            break
        traps_seen.update(new_traps)
        long_seen.update(long)
        regrouped = regroup(split_clusters=signal_grouping.get("split_clusters", ()),
                            dropped_nodes=signal_grouping.get("dropped_nodes", ()), long_centres=long,
                            series_traps=list(traps_seen.values()), joins=joins)
        if regrouped.get("status") == "blocked":
            break
        signal_grouping = regrouped
        result = rebuild()
    final = rounds[-1] if rounds else {"status": "blocked", "reason": "master_build_failed"}
    return {**signal_grouping, "master_rebuild_count": max(len(rounds) - 1, 0), "master_rounds": rounds,
            "master_check": final, "split_junction_joins": joining}, result


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
    user_agent: str = "ASTRA local OSM builder",
    timeout_seconds: float = 240.0,
    max_tile_area_km2: float = 2500.0,
    max_retries: int = 2,
    retry_pause_seconds: float = 5.0,
    command_runner: Callable[..., Any] = run_command,
    download_func: Callable[..., bytes] = download_osm,
    netconvert_profile: str | None = "vehicle_core",
    multimodal_modes: str | Iterable[str] | None = None,
    output_precision: int | None = None,
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
    multimodal_modes (bicycle, pedestrian; default both) selects the lane options
    of netconvert_profile="multimodal" and is rejected by every other profile.
    """
    try:
        if output_precision is not None and (isinstance(output_precision, bool)
                or not isinstance(output_precision, int) or output_precision < 0):
            raise ValueError("output_precision must be a non-negative integer.")
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
        profile_options = _netconvert_profile_options(netconvert_profile, multimodal_modes)
        normalized_profile = _normalized_netconvert_profile(netconvert_profile)
        resolved_multimodal_modes = _multimodal_modes(normalized_profile, multimodal_modes)
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
        # No U-turns at junctions, but a dead end keeps its turnaround: without one every cul-de-sac traps the
        # vehicles that enter it (central Dresden: 179 of them, boundary_completeness dead_end_without_turnaround).
        turnaround_options = (
            []
            if normalized_profile in REFERENCE_VISUAL_PROFILES
            else ["--no-turnarounds.except-deadend"]
        )
        type_options, type_warnings = (
            _reference_visual_type_options(
                root,
                logs_dir,
                prefix,
                explicit_sumo_home=sumo_home,
                netconvert_binary=netconvert_binary,
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
                        netconvert_binary=netconvert_binary,
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
        type_map_inputs = []
        for filename in (type_options[1].split(",") if type_options else []):
            path = Path(filename)
            path = path if path.is_absolute() else root / path
            type_map_inputs.append({"path": str(path.resolve()), "sha256": _file_sha256(path)})
        geo_boundary_options = (["--keep-edges.in-geo-boundary", parsed_geo_boundary]
                                if parsed_geo_boundary is not None else [])
        tls_join_options = ["--tls.join", "--tls.join-dist", f"{tls_join_distance_m:g}"]
        signal_grouping: dict[str, Any] | None = None
        signal_warnings: list[str] = []
        if normalized_profile == MULTIMODAL_NETCONVERT_PROFILE:
            def group_signals(**fallbacks: Any) -> dict[str, Any]:
                return _vehicle_signal_groups(
                    filtered_osm=filtered_osm, root=root, work_dir=logs_dir / f"{prefix}_signal_groups",
                    allowed_highways=allowed, tls_join_distance_m=tls_join_distance_m,
                    input_options=["--proj.utm", *traffic_side_options, *type_options, *geo_boundary_options],
                    include_railway=include_railway, allowed_railways=allowed_railways,
                    netconvert_binary=netconvert_binary, command_runner=command_runner,
                    timeout_seconds=timeout_seconds, **fallbacks)

            signal_grouping = group_signals()
            if signal_grouping["status"] == "blocked":
                signal_warnings.append("The vehicle-road signal reference build failed; netconvert joined the "
                                       "multimodal signals across every road class instead.")
            else:
                node_file = signal_grouping["node_file"]
                tls_join_options = [] if node_file is None else ["--node-files", _relative_to_root(Path(node_file), root)]
        from .local_network_conversion import with_geometry_precision

        command = with_geometry_precision([
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
            *tls_join_options,
            *type_options,
            *profile_options,
            *([] if output_precision is None else ["--precision", str(output_precision)]),
            *geo_boundary_options,
            "--verbose",
        ])
        effective_precision = int(command[command.index("--precision") + 1])
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
                    "multimodal_modes=" + ",".join(resolved_multimodal_modes),
                    *(
                        [
                            f"signal_grouping={signal_grouping['status']}",
                            "signal_grouping_command=" + " ".join(signal_grouping.get("command", [])),
                            f"signal_grouping_node_file={signal_grouping['node_file'] or ''}",
                        ]
                        if signal_grouping is not None
                        else []
                    ),
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
        if signal_grouping is not None and signal_grouping.get("node_file"):
            signal_grouping, result = _settle_master_signals(
                signal_grouping, result, net_file=net_file, osm_file=filtered_osm, regroup=group_signals,
                rebuild=lambda: _result_to_dict(command_runner(command, cwd=root, timeout_seconds=timeout_seconds)))
            if signal_grouping["master_check"]["status"] != "pass":
                signal_warnings.append(f"{signal_grouping['master_check']['not_applied_count']} signal node file "
                                       "rows did not take effect in the master.")
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
        from .tls_reference_cleanup import build_tls_reference_cleanup_variant

        tls_cleanup = {"status": "not_run"}
        if result.get("status") == "pass" and net_file.is_file():
            tls_cleanup = build_tls_reference_cleanup_variant(
                net_file, output_dir=logs_dir / f"{prefix}_tls_cleanup")
            if tls_cleanup["status"] == "pass":
                access_input = logs_dir / f"{prefix}_access-input.net.xml"
                copy_file_atomic(Path(tls_cleanup["effective_net_file"]), access_input)
                net_file = access_input

        access_correction = (
            correct_osm_access_permissions(
                osm_file=filtered_osm,
                net_file=net_file,
                output_dir=logs_dir / f"{prefix}_osm_access",
                netconvert_binary=netconvert_binary,
                timeout_seconds=timeout_seconds,
                command_runner=command_runner,
            )
            if result.get("status") == "pass" and tls_cleanup["status"] == "pass" and net_file.is_file()
            else {"status": "not_run", "changed_lane_count": 0}
        )
    except (OSError, ET.ParseError, error.URLError, error.HTTPError, RuntimeError, ValueError) as exc:
        return _failure(
            f"{type(exc).__name__}: {exc}",
            artifacts={"query_file": str(query_file), "command_record": str(command_record)},
        )

    status = "pass" if result.get("status") == "pass" and tls_cleanup["status"] == "pass" and net_file.exists() and access_correction["status"] != "blocked" else "fail"
    warnings = [*type_warnings, *signal_warnings]
    review_reason = None
    reverted = (access_correction.get("added_class_movements") or {}).get("reverted_by_class") or {}
    if reverted:
        warnings.append("OSM access correction reverted lane-class additions without a loaded movement: "
                        + ", ".join(f"{name} {count}" for name, count in reverted.items()) + ".")
    if status == "pass" and access_correction.get("status") == "review_required":
        status = "review_required"
        if access_correction.get("connectivity_review_required"):
            review_reason = "newly_allowed_modes_require_connectivity_review"
            warnings.append("Newly allowed vehicle classes without a loaded movement could not be reverted.")
        else:
            review_reason = "osm_access_findings_open"
            open_reasons = Counter(finding.get("reason", "unknown") for row in access_correction.get("review", [])
                                   for finding in row.get("findings", [row]))
            warnings.append("OSM access correction left findings open: "
                            + ", ".join(f"{name} {count}" for name, count in sorted(open_reasons.items())) + ".")
    claim_status = "diagnostic-demo" if status in {"pass", "review_required"} else "construction-invalid"
    if not net_file.exists():
        warnings.append(f"net file was not created: {net_file}")
    origin_name_output = _origin_name_output_summary(net_file)
    review_fields = {"reason": review_reason} if review_reason else {}
    build_manifest = {
        "schema": OSM_BUILD_PROVENANCE_SCHEMA,
        "status": status,
        "claim_status": claim_status,
        **review_fields,
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
            "precision": effective_precision,
            "result": result,
            "profile": normalized_profile,
            "profile_options": profile_options,
            "multimodal_modes": resolved_multimodal_modes,
            **({"signal_grouping": signal_grouping} if signal_grouping is not None else {}),
            "type_options": type_options,
            "type_map_inputs": type_map_inputs,
            "output_original_names": origin_name_output,
        },
        "osm_access_correction": access_correction,
        "tls_reference_cleanup": tls_cleanup,
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
        "claim_status": claim_status,
        **review_fields,
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
        "netconvert_precision": effective_precision,
        "netconvert_profile_options": profile_options,
        "netconvert_multimodal_modes": resolved_multimodal_modes,
        **({"netconvert_signal_grouping": signal_grouping} if signal_grouping is not None else {}),
        "netconvert_type_map_inputs": type_map_inputs,
        "netconvert_output_original_names": origin_name_output,
        "osm_access_correction": access_correction,
        "tls_reference_cleanup": tls_cleanup,
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
        # Every top-level element is dropped once read, so ways and relations do not pile up in the tree.
        for element in DirectChildren(read_blocks(handle)):
            for elem in element.iter("node"):
                tags = {tag.attrib.get("k"): tag.attrib.get("v") for tag in elem.findall("tag")}
                if tags.get("highway") == "traffic_signals":
                    signals.append(
                        {
                            "osm_signal_id": elem.attrib.get("id", ""),
                            "lat": float(elem.attrib["lat"]),
                            "lon": float(elem.attrib["lon"]),
                        }
                    )
    return signals


def _nearest_signal(lat: float, lon: float, signals: list[dict[str, Any]]) -> tuple[str, float | None]:
    if not signals:
        return "", None
    best = min(signals, key=lambda item: haversine_m(lat, lon, item["lat"], item["lon"]))
    return best["osm_signal_id"], haversine_m(lat, lon, best["lat"], best["lon"])


def _record_edge_name(names: dict[str, str], edge: ET.Element) -> None:
    """Name an edge by its name attribute, else by its first name or origId parameter, edge or lane level."""
    edge_id = edge.attrib.get("id")
    if edge.attrib.get("name"):
        names[edge_id] = edge.attrib["name"]
    if edge_id:
        for parameter in edge.iter("param"):
            if parameter.attrib.get("key") in {"name", "origId"}:
                names.setdefault(edge_id, parameter.attrib.get("value", ""))


def _sumolib_lane_position(lane_counts: Mapping[str, int], edge_id: str, spelled: str) -> int:
    """The lane list position sumolib's ``edge.getLane(int(spelled))`` reads, raising as it raises."""
    count = lane_counts[edge_id]
    index = int(spelled)
    if not -count <= index < count:
        raise IndexError("list index out of range")
    return index % count


class SumolibTlsView:
    """What ``sumolib.net.readNet(net_file, withPrograms=True)`` loads about traffic lights, fed one top-level
    element at a time with ``add`` and then ``finish``, both with the document root.

    It keeps the location, the coordinates of the first junction of each non-internal id, the junction ids sumolib
    creates (non-internal junctions and the end junctions of plain edges), the end junctions and lane count of each
    plain edge (a later edge of the same id only adds lanes) and, per traffic light in order of first appearance,
    its loaded connections as ``(from edge, lane position, to edge, lane position)`` in document order. Connections
    from internal edges or touching crossings, walking areas or macro connectors are not loaded. Each element is
    first checked as sumolib loads it, so a network sumolib cannot load raises the error sumolib raises.
    """

    def __init__(self) -> None:
        self.location: dict[str, str] | None = None
        self.coordinates: dict[str, tuple[float, float]] = {}
        self.node_ids: set[str] = set()
        self.edge_ends: dict[str, tuple[str | None, str | None]] = {}
        self.controllers: dict[str, list[tuple[str, int, str, int]]] = {}
        self._names: dict[str, str] = {}
        self._check = SumolibLoadCheck(with_programs=True)
        self.lane_counts = self._check.lane_counts
        self.unloaded_edges = self._check.unloaded_edges

    def _shared(self, value: str | None) -> str | None:
        return None if value is None else self._names.setdefault(value, value)

    def finish(self, root: ET.Element) -> None:
        self._check.finish(root)

    def add(self, element: ET.Element, root: ET.Element) -> None:
        self._check.add(element, root)
        attributes = element.attrib
        if element.tag == "edge":
            if attributes.get("function", "") == "":
                edge_id = attributes["id"]
                if edge_id not in self.edge_ends:
                    ends = (self._shared(attributes.get("from")), self._shared(attributes.get("to")))
                    self.edge_ends[edge_id] = ends
                    self.node_ids.update(node_id for node_id in ends if node_id is not None)
        elif element.tag == "junction":
            junction_id = attributes["id"]
            if junction_id[0] != ":":
                self.node_ids.add(self._shared(junction_id))
                if junction_id not in self.coordinates:
                    self.coordinates[junction_id] = (float(attributes["x"]), float(attributes["y"]))
        elif element.tag == "tlLogic":
            self.controllers.setdefault(attributes["id"], [])
        elif element.tag == "connection":
            first = attributes["from"]
            if first[0] == ":":
                return
            last = attributes["to"]
            if first in self.unloaded_edges or last in self.unloaded_edges:
                return
            first_lane = _sumolib_lane_position(self.lane_counts, first, attributes["fromLane"])
            last_lane = _sumolib_lane_position(self.lane_counts, last, attributes["toLane"])
            tl_id = attributes.get("tl", "")
            if tl_id:
                int(attributes["linkIndex"])
                self.controllers.setdefault(tl_id, []).append((first, first_lane, last, last_lane))
        elif element.tag == "location":
            self.location = dict(attributes)


def _streamed_tls_network(net_file: Path) -> tuple[SumolibTlsView, dict[str, str]]:
    """Read the sumolib traffic-light view and the edge names of a network in one streamed pass."""
    view = SumolibTlsView()
    names: dict[str, str] = {}
    stream = DirectChildren(binary_blocks(net_file))
    for element in stream:
        if element.tag == "edge":
            _record_edge_name(names, element)
        view.add(element, stream.root)
    view.finish(stream.root)
    return view, names


def _streamed_lane_shapes(net_file: Path, wanted: set[tuple[str, int]]) -> dict[tuple[str, int], list[tuple[float, float]]]:
    """The 2D shapes sumolib loads for the wanted ``(plain edge, lane position)`` lanes, read in one pass."""
    import sumolib.net  # type: ignore

    if not wanted:
        return {}
    wanted_edges = {edge_id for edge_id, _ in wanted}
    positions: dict[str, int] = {}
    shapes: dict[tuple[str, int], list[tuple[float, float]]] = {}
    for element in DirectChildren(binary_blocks(net_file)):
        if element.tag != "edge" or element.attrib.get("function", "") != "":
            continue
        edge_id = element.attrib["id"]
        if edge_id not in wanted_edges:
            continue
        for lane in element.findall("lane"):
            position = positions.get(edge_id, 0)
            positions[edge_id] = position + 1
            if (edge_id, position) in wanted:
                shapes[(edge_id, position)] = [
                    (x, y) for x, y, _z in sumolib.net.convertShape(lane.attrib.get("shape", ""))
                ]
    return shapes


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

    # A streamed pass replaces the sumolib object model of the whole network; the projection is sumolib's,
    # from the network location alone.
    network, edge_names = _streamed_tls_network(net_file)
    net = sumolib.net.Net()
    location = network.location
    if location is not None:
        net.setLocation(location["netOffset"], location["convBoundary"], location["origBoundary"], location["projParameter"])
    nodes = network.coordinates
    controllers = {
        tls_id: connections
        for tls_id, connections in network.controllers.items()
        if len(connections) >= min_connections
    }
    del network
    signals = _iter_osm_signal_nodes(osm_file) if osm_file is not None else []
    # Lane shapes are read only for controllers without a junction of their own id.
    shapes = _streamed_lane_shapes(
        net_file,
        {
            lane
            for tls_id, connections in controllers.items()
            if tls_id not in nodes
            for first, first_lane, last, last_lane in connections
            for lane in ((first, first_lane), (last, last_lane))
        },
    )
    rows = []
    for tls_id, connections in controllers.items():
        try:
            x, y = nodes[tls_id]
            lat, lon = _net_xy_to_latlon(net, x, y)
        except Exception:  # noqa: BLE001 - malformed third-party TLS objects use lane-shape fallback.
            lanes = [lane for first, first_lane, last, last_lane in connections for lane in ((first, first_lane), (last, last_lane))]
            if any(lane not in shapes for lane in lanes):
                shapes.update(_streamed_lane_shapes(net_file, set(lanes)))
            points = []
            for lane in lanes:
                shape = shapes[lane]
                if shape:
                    points.extend([shape[0], shape[-1]])
            if not points:
                continue
            x = sum(point[0] for point in points) / len(points)
            y = sum(point[1] for point in points) / len(points)
            lat, lon = _net_xy_to_latlon(net, x, y)
        signal_id, signal_distance = _nearest_signal(lat, lon, signals)
        incoming_edge_ids = sorted({first for first, _, _, _ in connections})
        outgoing_edge_ids = sorted({last for _, _, last, _ in connections})
        incoming_names = sorted({edge_names.get(edge_id, "") for edge_id in incoming_edge_ids if edge_names.get(edge_id, "")})
        outgoing_names = sorted({edge_names.get(edge_id, "") for edge_id in outgoing_edge_ids if edge_names.get(edge_id, "")})
        has_osm_signal = signal_distance is not None and signal_distance <= 35.0
        rows.append(
            {
                "tls_id": tls_id,
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
                **regional_map_fields(lat, lon, label=f"SUMO TLS {tls_id}"),
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
    """Read the plain edges of a network and the edges each one connects to, one top-level element at a time.

    A connection counts when both of its edges are plain edges of the network. When an edge follows a
    connection in the file, the connections are read again once every edge is known. An edge without an id
    raises ``KeyError`` after the whole file has parsed, as for a parsed tree.
    """
    edges: dict[str, EdgeInfo] = {}
    connections: dict[str, set[str]] = {}

    def connect(connection: ET.Element) -> None:
        from_edge = connection.attrib.get("from")
        to_edge = connection.attrib.get("to")
        if from_edge in edges and to_edge in edges:
            connections.setdefault(from_edge, set()).add(to_edge)

    failure: KeyError | None = None
    connection_seen = edge_after_connection = False
    for element in DirectChildren(binary_blocks(path)):
        if failure is not None:
            continue
        if element.tag == "connection":
            connection_seen = True
            if not edge_after_connection:
                connect(element)
            continue
        if element.tag != "edge":
            continue
        edge_after_connection |= connection_seen
        try:
            edge_id = element.attrib["id"]
        except KeyError as exc:
            failure = exc
            continue
        if element.attrib.get("function") or edge_id.startswith(":"):
            continue
        params = [
            param.attrib.get("value", "")
            for param in element.findall("param")
            if param.attrib.get("key") in {"name", "bridge:name", "loc_name", "alt_name", "destination"}
            and param.attrib.get("value")
        ]
        if element.attrib.get("name"):
            params.append(element.attrib["name"])
        lanes = element.findall("lane")
        edges[edge_id] = EdgeInfo(
            edge_id=edge_id,
            from_node=element.attrib.get("from", ""),
            to_node=element.attrib.get("to", ""),
            name_values=tuple(params),
            allows_passenger=any(_lane_allows_passenger(lane) for lane in lanes),
        )
    if failure is not None:
        raise failure
    if edge_after_connection:
        connections.clear()
        for element in DirectChildren(binary_blocks(path)):
            if element.tag == "connection":
                connect(element)
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
