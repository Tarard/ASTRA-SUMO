from __future__ import annotations

from collections.abc import Callable, Mapping
import json
import math
import re
from typing import Any
from urllib import error, parse, request


DEFAULT_NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
DEFAULT_USER_AGENT = "Torii-SUMO/1.0 (+https://github.com/Tarard/Torii-SUMO)"


def osm_preview_url(place_name: str) -> str:
    return "https://www.openstreetmap.org/search?" + parse.urlencode({"query": place_name})


def osm_map_url_bbox(value: str, *, min_radius_m: float = 250.0) -> str:
    cleaned = value.strip()
    parsed = parse.urlparse(cleaned)
    if not parsed.netloc.lower().endswith("openstreetmap.org"):
        url_match = re.search(r"https?://(?:www\.)?openstreetmap\.org/\S+", cleaned)
        if not url_match:
            return ""
        parsed = parse.urlparse(url_match.group(0).rstrip(").,;"))
    match = re.search(
        r"(?:^|&)map=(?P<zoom>\d+(?:\.\d+)?)/(?P<lat>-?\d+(?:\.\d+)?)/(?P<lon>-?\d+(?:\.\d+)?)",
        parse.unquote(parsed.fragment),
    )
    if not match:
        return ""

    zoom = float(match.group("zoom"))
    lat = float(match.group("lat"))
    lon = float(match.group("lon"))
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return ""

    meters_per_pixel = 156543.03392 * max(math.cos(math.radians(lat)), 0.01) / (2**zoom)
    radius_m = max(min_radius_m, meters_per_pixel * 450.0)
    lat_delta = radius_m / 111_320.0
    lon_delta = radius_m / (111_320.0 * max(math.cos(math.radians(lat)), 0.01))
    west = max(-180.0, lon - lon_delta)
    south = max(-90.0, lat - lat_delta)
    east = min(180.0, lon + lon_delta)
    north = min(90.0, lat + lat_delta)
    return f"{west:.7f},{south:.7f},{east:.7f},{north:.7f}"


def _candidate_osm_url(osm_type: str, osm_id: str) -> str:
    normalized = osm_type.strip().lower()
    if normalized not in {"node", "way", "relation"} or not osm_id:
        return ""
    return f"https://www.openstreetmap.org/{normalized}/{osm_id}"


def _format_nominatim_bbox(candidate: Mapping[str, Any]) -> str:
    bbox = candidate.get("boundingbox")
    if not isinstance(bbox, list) or len(bbox) != 4:
        return ""
    south, north, west, east = (str(value) for value in bbox)
    return f"{west},{south},{east},{north}"


def _fetch_json(*, url: str, headers: dict[str, str], timeout_seconds: float) -> list[Mapping[str, Any]]:
    req = request.Request(url, headers=headers)
    with request.urlopen(req, timeout=timeout_seconds) as response:
        payload = response.read().decode("utf-8")
    value = json.loads(payload)
    if not isinstance(value, list):
        raise ValueError("Nominatim response was not a JSON list")
    return value


def resolve_osm_place(
    place_name: str,
    *,
    limit: int = 1,
    nominatim_url: str = DEFAULT_NOMINATIM_URL,
    user_agent: str = DEFAULT_USER_AGENT,
    timeout_seconds: float = 30.0,
    fetch_json: Callable[..., list[Mapping[str, Any]]] = _fetch_json,
) -> dict[str, Any]:
    cleaned = place_name.strip()
    preview = osm_preview_url(cleaned)
    if not cleaned:
        return {
            "status": "blocked",
            "claim_status": "blocked",
            "area_input": place_name,
            "area_resolution_status": "blocked",
            "candidate_display_name": "",
            "candidate_osm_type": "",
            "candidate_osm_id": "",
            "candidate_bbox": "",
            "candidate_lat": "",
            "candidate_lon": "",
            "osm_preview_url": preview,
            "candidate_osm_url": "",
            "warnings": ["place_name is required"],
        }

    query = parse.urlencode(
        {
            "q": cleaned,
            "format": "jsonv2",
            "addressdetails": "1",
            "limit": str(max(1, limit)),
            "polygon_geojson": "0",
        }
    )
    url = f"{nominatim_url}?{query}"
    try:
        candidates = fetch_json(
            url=url,
            headers={"User-Agent": user_agent},
            timeout_seconds=timeout_seconds,
        )
    except (OSError, error.URLError, TimeoutError, ValueError, json.JSONDecodeError) as exc:
        return {
            "status": "blocked",
            "claim_status": "blocked",
            "area_input": cleaned,
            "area_resolution_status": "resolution_failed",
            "candidate_display_name": "",
            "candidate_osm_type": "",
            "candidate_osm_id": "",
            "candidate_bbox": "",
            "candidate_lat": "",
            "candidate_lon": "",
            "osm_preview_url": preview,
            "candidate_osm_url": "",
            "warnings": [f"{type(exc).__name__}: {exc}"],
        }

    if not candidates:
        return {
            "status": "blocked",
            "claim_status": "blocked",
            "area_input": cleaned,
            "area_resolution_status": "no_candidate",
            "candidate_display_name": "",
            "candidate_osm_type": "",
            "candidate_osm_id": "",
            "candidate_bbox": "",
            "candidate_lat": "",
            "candidate_lon": "",
            "osm_preview_url": preview,
            "candidate_osm_url": "",
            "warnings": ["no OSM/Nominatim candidate found for place_name"],
        }

    candidate = candidates[0]
    osm_type = str(candidate.get("osm_type", ""))
    osm_id = str(candidate.get("osm_id", ""))
    bbox = _format_nominatim_bbox(candidate)
    status = "pass" if bbox else "blocked"
    warnings = [] if bbox else ["OSM/Nominatim candidate did not include a boundingbox"]
    return {
        "status": status,
        "claim_status": "diagnostic-demo" if status == "pass" else "blocked",
        "area_input": cleaned,
        "area_resolution_status": "candidate_found" if bbox else "candidate_missing_bbox",
        "candidate_display_name": str(candidate.get("display_name", "")),
        "candidate_osm_type": osm_type,
        "candidate_osm_id": osm_id,
        "candidate_bbox": bbox,
        "candidate_lat": str(candidate.get("lat", "")),
        "candidate_lon": str(candidate.get("lon", "")),
        "osm_preview_url": preview,
        "candidate_osm_url": _candidate_osm_url(osm_type, osm_id),
        "warnings": warnings,
    }
