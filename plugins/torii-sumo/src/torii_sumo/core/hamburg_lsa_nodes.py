from __future__ import annotations

import hashlib
import json
import math
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence
from urllib.parse import parse_qs, urlencode, urlparse
from urllib.request import Request, urlopen

from .artifact_io import write_json_atomic, write_text_atomic


HAMBURG_LSA_NODE_ITEMS_ENDPOINT = (
    "https://api.hamburg.de/datasets/v1/lichtsignalanlagen/"
    "collections/lsa_knotengrunddaten/items"
)
HAMBURG_LSA_NODE_COLLECTION_ID = "lsa_knotengrunddaten"
HAMBURG_LSA_NODE_PROPERTY_KEYS = ("knoten", "art", "LSA_Name")
HAMBURG_LSA_NODE_SOURCE_SCHEMA: Mapping[str, Any] = {
    "collection_type": "FeatureCollection",
    "feature_type": "Feature",
    "feature_id_type": "integer",
    "geometry_type": "MultiPoint",
    "geometry_coordinate_count": 1,
    "coordinate_reference_system": "OGC:CRS84",
    "coordinate_order": ("longitude", "latitude"),
    "properties": {
        "knoten": {
            "json_type": "integer",
            "meaning": "official Hamburg LSA node number",
            "public_aliases": ("node_id", "lsanr"),
        },
        "art": {
            "json_type": "string",
            "meaning": "official LSA type; source value may be space padded",
        },
        "LSA_Name": {
            "json_type": "string",
            "meaning": "official slash-delimited LSA name; source value may be space padded",
        },
    },
}

AUTONOMOUS_ABSTENTION_ACTION = "autonomous_abstention_no_materialization"
_ALLOWED_QUERY_KEYS = frozenset({"bbox", "f", "limit"})
_EXPECTED_HOST = "api.hamburg.de"
_EXPECTED_PATH = (
    "/datasets/v1/lichtsignalanlagen/collections/lsa_knotengrunddaten/items"
)


Transport = Callable[[Request, float], bytes]


@dataclass(frozen=True)
class GeographicBBox:
    west: float
    south: float
    east: float
    north: float

    def __post_init__(self) -> None:
        values = (self.west, self.south, self.east, self.north)
        if not all(math.isfinite(value) for value in values):
            raise ValueError("bbox coordinates must be finite")
        if not (-180.0 <= self.west < self.east <= 180.0):
            raise ValueError("bbox west/east coordinates are invalid")
        if not (-90.0 <= self.south < self.north <= 90.0):
            raise ValueError("bbox south/north coordinates are invalid")

    @classmethod
    def parse(cls, value: GeographicBBox | str | Sequence[float]) -> GeographicBBox:
        if isinstance(value, cls):
            return value
        if isinstance(value, str):
            parts: Sequence[str | float] = value.split(",")
        else:
            parts = value
        if len(parts) != 4:
            raise ValueError("bbox must contain west,south,east,north")
        try:
            coordinates = tuple(float(part) for part in parts)
        except (TypeError, ValueError) as exc:
            raise ValueError("bbox coordinates must be numeric") from exc
        return cls(*coordinates)

    def as_query_value(self) -> str:
        return ",".join(format(value, ".15g") for value in self.as_tuple())

    def as_tuple(self) -> tuple[float, float, float, float]:
        return (self.west, self.south, self.east, self.north)

    def contains(self, longitude: float, latitude: float) -> bool:
        return self.west <= longitude <= self.east and self.south <= latitude <= self.north


@dataclass(frozen=True)
class HamburgLsaNode:
    feature_id: int
    node_id: str
    lsanr: str
    source_knoten: int
    official_name: str
    normalized_road_name_components: tuple[str, ...]
    signal_type: str
    longitude: float
    latitude: float
    source_name_raw: str
    source_signal_type_raw: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "feature_id": self.feature_id,
            "node_id": self.node_id,
            "lsanr": self.lsanr,
            "official_name": self.official_name,
            "normalized_road_name_components": list(self.normalized_road_name_components),
            "signal_type": self.signal_type,
            "point_geometry": {
                "type": "Point",
                "coordinates": [self.longitude, self.latitude],
            },
            "source_geometry_type": "MultiPoint",
            "source_properties": {
                "knoten": self.source_knoten,
                "art": self.source_signal_type_raw,
                "LSA_Name": self.source_name_raw,
            },
        }


@dataclass(frozen=True)
class HamburgLsaNodeSnapshot:
    request_url: str
    bbox: GeographicBBox
    raw_sha256: str
    time_stamp: str
    number_returned: int
    number_matched: int
    nodes: tuple[HamburgLsaNode, ...]

    def node_by_id(self, node_id: str | int) -> HamburgLsaNode | None:
        key = str(node_id).strip()
        return next((node for node in self.nodes if node.node_id == key), None)


@dataclass(frozen=True)
class HamburgLsaNodeRequest:
    expected_node_id: str
    road_name_components: tuple[str, ...]

    @classmethod
    def create(
        cls,
        *,
        expected_node_id: str | int,
        road_name_components: Sequence[str],
    ) -> HamburgLsaNodeRequest:
        node_id = str(expected_node_id).strip()
        if not node_id or not node_id.isdecimal():
            raise ValueError("expected_node_id must be a decimal Hamburg LSA node number")
        normalized = normalize_road_name_components(road_name_components)
        return cls(expected_node_id=node_id, road_name_components=normalized)


@dataclass(frozen=True)
class HamburgLsaNodeSelection:
    decision: str
    reason: str
    expected_node_id: str
    normalized_road_name_components: tuple[str, ...]
    matched_feature_ids: tuple[int, ...]
    matched_node_ids: tuple[str, ...]
    selected_node: HamburgLsaNode | None
    human_action_required: bool = False

    @property
    def automation_action(self) -> str:
        if self.decision == "pass":
            return "use_frozen_official_node_identity"
        return AUTONOMOUS_ABSTENTION_ACTION

    def to_dict(self) -> dict[str, Any]:
        return {
            "decision": self.decision,
            "reason": self.reason,
            "expected_node_id": self.expected_node_id,
            "normalized_road_name_components": list(self.normalized_road_name_components),
            "matched_feature_ids": list(self.matched_feature_ids),
            "matched_node_ids": list(self.matched_node_ids),
            "selected_node": self.selected_node.to_dict() if self.selected_node else None,
            "human_action_required": self.human_action_required,
            "automation_action": self.automation_action,
        }


def build_hamburg_lsa_node_items_url(
    bbox: GeographicBBox | str | Sequence[float],
    *,
    limit: int = 1000,
) -> str:
    parsed_bbox = GeographicBBox.parse(bbox)
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 10_000:
        raise ValueError("limit must be an integer from 1 through 10000")
    query = urlencode(
        {
            "f": "json",
            "limit": str(limit),
            "bbox": parsed_bbox.as_query_value(),
        }
    )
    return f"{HAMBURG_LSA_NODE_ITEMS_ENDPOINT}?{query}"


def validate_hamburg_lsa_node_items_url(url: str) -> GeographicBBox:
    parsed = urlparse(url)
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("invalid Hamburg LSA node request URL") from exc
    if (
        parsed.scheme != "https"
        or parsed.hostname != _EXPECTED_HOST
        or parsed.path != _EXPECTED_PATH
        or parsed.username is not None
        or parsed.password is not None
        or port not in (None, 443)
        or parsed.fragment
    ):
        raise ValueError("Hamburg LSA node request must use the exact official HTTPS items endpoint")
    query = parse_qs(parsed.query, keep_blank_values=True, strict_parsing=True)
    if set(query) != _ALLOWED_QUERY_KEYS or any(len(values) != 1 for values in query.values()):
        raise ValueError("Hamburg LSA node request requires exactly one bbox, f, and limit query")
    if query["f"] != ["json"]:
        raise ValueError("Hamburg LSA node request must use f=json")
    limit_text = query["limit"][0]
    if not limit_text.isdecimal() or not 1 <= int(limit_text) <= 10_000:
        raise ValueError("Hamburg LSA node request limit must be from 1 through 10000")
    return GeographicBBox.parse(query["bbox"][0])


def normalize_road_name_component(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError("road-name components must be strings")
    normalized = unicodedata.normalize("NFKC", value)
    normalized = " ".join(normalized.strip().split()).casefold()
    if not normalized or "/" in normalized:
        raise ValueError("each road-name component must be non-empty and must not contain '/'")
    return normalized


def normalize_road_name_components(values: Sequence[str]) -> tuple[str, ...]:
    normalized = tuple(sorted(normalize_road_name_component(value) for value in values))
    if not normalized:
        raise ValueError("at least one road-name component is required")
    if len(normalized) != len(set(normalized)):
        raise ValueError("road-name components must be unique after normalization")
    return normalized


def road_name_components_from_official_name(name: str) -> tuple[str, ...]:
    if not isinstance(name, str) or not name.strip():
        raise ValueError("official LSA name must be a non-empty string")
    parts = name.strip().split("/")
    return normalize_road_name_components(parts)


def parse_hamburg_lsa_node_feature_collection(
    raw_json: bytes,
    *,
    request_url: str,
    expected_sha256: str | None = None,
) -> HamburgLsaNodeSnapshot:
    bbox = validate_hamburg_lsa_node_items_url(request_url)
    raw_sha256 = hashlib.sha256(raw_json).hexdigest()
    if expected_sha256 is not None:
        normalized_expected_hash = _validate_sha256(expected_sha256)
        if raw_sha256 != normalized_expected_hash:
            raise ValueError(
                "Hamburg LSA node snapshot SHA-256 mismatch: "
                f"expected {normalized_expected_hash}, got {raw_sha256}"
            )
    try:
        payload = json.loads(raw_json.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("Hamburg LSA node response is not valid UTF-8 JSON") from exc
    if not isinstance(payload, Mapping) or payload.get("type") != "FeatureCollection":
        raise ValueError("Hamburg LSA node response must be a GeoJSON FeatureCollection")

    number_returned = _required_nonnegative_integer(payload, "numberReturned")
    number_matched = _required_nonnegative_integer(payload, "numberMatched")
    features = payload.get("features")
    if not isinstance(features, list):
        raise ValueError("Hamburg LSA node FeatureCollection.features must be a list")
    if number_returned != len(features):
        raise ValueError("Hamburg LSA node numberReturned does not equal the feature count")
    if number_matched < number_returned:
        raise ValueError("Hamburg LSA node numberMatched is smaller than numberReturned")

    time_stamp = payload.get("timeStamp")
    if not isinstance(time_stamp, str):
        raise ValueError("Hamburg LSA node FeatureCollection.timeStamp must be a string")
    _validate_utc_timestamp(time_stamp)
    _validate_self_link(payload.get("links"), request_url=request_url)

    nodes = tuple(_parse_node(feature, bbox=bbox) for feature in features)
    feature_ids = [node.feature_id for node in nodes]
    node_ids = [node.node_id for node in nodes]
    if len(feature_ids) != len(set(feature_ids)):
        raise ValueError("Hamburg LSA node response contains duplicate feature ids")
    if len(node_ids) != len(set(node_ids)):
        raise ValueError("Hamburg LSA node response contains duplicate official node ids")

    return HamburgLsaNodeSnapshot(
        request_url=request_url,
        bbox=bbox,
        raw_sha256=raw_sha256,
        time_stamp=time_stamp,
        number_returned=number_returned,
        number_matched=number_matched,
        nodes=nodes,
    )


def load_frozen_hamburg_lsa_node_snapshot(
    path: Path,
    *,
    request_url: str,
    expected_sha256: str,
) -> HamburgLsaNodeSnapshot:
    return parse_hamburg_lsa_node_feature_collection(
        path.resolve(strict=True).read_bytes(),
        request_url=request_url,
        expected_sha256=expected_sha256,
    )


def select_hamburg_lsa_node(
    snapshot: HamburgLsaNodeSnapshot,
    request: HamburgLsaNodeRequest,
) -> HamburgLsaNodeSelection:
    matches = tuple(
        node
        for node in snapshot.nodes
        if snapshot.bbox.contains(node.longitude, node.latitude)
        and node.normalized_road_name_components == request.road_name_components
    )
    feature_ids = tuple(node.feature_id for node in matches)
    node_ids = tuple(node.node_id for node in matches)
    if not matches:
        return HamburgLsaNodeSelection(
            decision="review_required",
            reason="no_exact_normalized_road_component_match",
            expected_node_id=request.expected_node_id,
            normalized_road_name_components=request.road_name_components,
            matched_feature_ids=feature_ids,
            matched_node_ids=node_ids,
            selected_node=None,
        )
    if len(matches) > 1:
        return HamburgLsaNodeSelection(
            decision="review_required",
            reason="ambiguous_exact_normalized_road_component_match",
            expected_node_id=request.expected_node_id,
            normalized_road_name_components=request.road_name_components,
            matched_feature_ids=feature_ids,
            matched_node_ids=node_ids,
            selected_node=None,
        )
    match = matches[0]
    if match.node_id != request.expected_node_id:
        return HamburgLsaNodeSelection(
            decision="review_required",
            reason="exact_name_match_has_unexpected_official_node_id",
            expected_node_id=request.expected_node_id,
            normalized_road_name_components=request.road_name_components,
            matched_feature_ids=feature_ids,
            matched_node_ids=node_ids,
            selected_node=None,
        )
    return HamburgLsaNodeSelection(
        decision="pass",
        reason="unique_bbox_name_and_node_id_match",
        expected_node_id=request.expected_node_id,
        normalized_road_name_components=request.road_name_components,
        matched_feature_ids=feature_ids,
        matched_node_ids=node_ids,
        selected_node=match,
    )


def freeze_hamburg_lsa_node_evidence(
    output_dir: Path,
    *,
    bbox: GeographicBBox | str | Sequence[float],
    requests: Sequence[HamburgLsaNodeRequest],
    limit: int = 1000,
    timeout_seconds: float = 60.0,
    transport: Transport | None = None,
    expected_sha256: str | None = None,
) -> dict[str, Any]:
    if not requests:
        raise ValueError("at least one Hamburg LSA node request is required")
    request_url = build_hamburg_lsa_node_items_url(bbox, limit=limit)
    validate_hamburg_lsa_node_items_url(request_url)
    request = Request(
        request_url,
        headers={"Accept": "application/geo+json,application/json", "User-Agent": "Torii-SUMO/1.1"},
    )
    raw_json = (transport or _urlopen_bytes)(request, timeout_seconds)
    if not raw_json:
        raise ValueError("official Hamburg LSA node API returned an empty response")
    snapshot = parse_hamburg_lsa_node_feature_collection(
        raw_json,
        request_url=request_url,
        expected_sha256=expected_sha256,
    )
    selections = tuple(select_hamburg_lsa_node(snapshot, item) for item in requests)
    digest_prefix = snapshot.raw_sha256[:12]
    output_dir = output_dir.resolve()
    raw_path = output_dir / f"lsa_knotengrunddaten.{digest_prefix}.geojson"
    manifest_path = output_dir / f"lsa-node-identity.{digest_prefix}.json"
    try:
        raw_text = raw_json.decode("utf-8")
    except UnicodeDecodeError as exc:  # pragma: no cover - parser already rejects this
        raise ValueError("Hamburg LSA node response is not valid UTF-8 JSON") from exc
    write_text_atomic(raw_path, raw_text)
    overall_decision = "pass" if all(item.decision == "pass" for item in selections) else "review_required"
    manifest: dict[str, Any] = {
        "schema": "torii.hamburg-lsa-node-identity-evidence/v1",
        "decision": overall_decision,
        "human_action_required": False,
        "automation_action": (
            "use_frozen_official_node_identities"
            if overall_decision == "pass"
            else AUTONOMOUS_ABSTENTION_ACTION
        ),
        "claim_boundary": {
            "proves": [
                "official_lsa_node_identity",
                "official_lsa_name",
                "official_lsa_point_geometry",
            ],
            "does_not_prove": [
                "map_asset_availability_or_node_binding",
                "ocit_c_asset_availability_or_node_binding",
                "tld_datastream_availability_or_node_binding",
                "sumo_lane_or_signal_group_binding",
            ],
            "downstream_signal_materialization_requires": [
                "unique_node_bound_official_map_asset",
                "unique_node_bound_official_ocit_c_asset",
                "nonempty_node_bound_official_tld_datastream_inventory",
            ],
        },
        "source": {
            "authority": "Free and Hanseatic City of Hamburg",
            "dataset": "lichtsignalanlagen",
            "collection": HAMBURG_LSA_NODE_COLLECTION_ID,
            "request_url": snapshot.request_url,
            "bbox": list(snapshot.bbox.as_tuple()),
            "response_time_stamp": snapshot.time_stamp,
            "raw_path": raw_path.name,
            "raw_sha256": snapshot.raw_sha256,
            "raw_bytes": len(raw_json),
            "number_returned": snapshot.number_returned,
            "number_matched": snapshot.number_matched,
            "source_schema": _json_safe(HAMBURG_LSA_NODE_SOURCE_SCHEMA),
        },
        "selections": [selection.to_dict() for selection in selections],
    }
    write_json_atomic(manifest_path, manifest, ensure_ascii=False, sort_keys=True)
    manifest["manifest_path"] = str(manifest_path)
    manifest["manifest_sha256"] = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
    manifest["raw_path"] = str(raw_path)
    return manifest


def _parse_node(feature: Any, *, bbox: GeographicBBox) -> HamburgLsaNode:
    if not isinstance(feature, Mapping) or feature.get("type") != "Feature":
        raise ValueError("Hamburg LSA node item must be a GeoJSON Feature")
    feature_id = feature.get("id")
    if isinstance(feature_id, bool) or not isinstance(feature_id, int) or feature_id < 0:
        raise ValueError("Hamburg LSA node feature id must be a non-negative integer")
    geometry = feature.get("geometry")
    if not isinstance(geometry, Mapping) or geometry.get("type") != "MultiPoint":
        raise ValueError("Hamburg LSA node geometry must use the published MultiPoint schema")
    coordinates = geometry.get("coordinates")
    if not isinstance(coordinates, list) or len(coordinates) != 1:
        raise ValueError("Hamburg LSA node MultiPoint must contain exactly one point")
    point = coordinates[0]
    if not isinstance(point, list) or len(point) != 2:
        raise ValueError("Hamburg LSA node point must contain longitude and latitude")
    longitude = _finite_coordinate(point[0], "longitude")
    latitude = _finite_coordinate(point[1], "latitude")
    if not (-180.0 <= longitude <= 180.0 and -90.0 <= latitude <= 90.0):
        raise ValueError("Hamburg LSA node point is outside geographic coordinate bounds")
    if not bbox.contains(longitude, latitude):
        raise ValueError("Hamburg LSA node point is outside the request bbox")

    properties = feature.get("properties")
    if not isinstance(properties, Mapping):
        raise ValueError("Hamburg LSA node feature.properties must be an object")
    if set(properties) != set(HAMBURG_LSA_NODE_PROPERTY_KEYS):
        raise ValueError(
            "Hamburg LSA node properties do not match the published schema "
            f"{HAMBURG_LSA_NODE_PROPERTY_KEYS!r}"
        )
    source_knoten = properties.get("knoten")
    if isinstance(source_knoten, bool) or not isinstance(source_knoten, int) or source_knoten < 0:
        raise ValueError("Hamburg LSA node properties.knoten must be a non-negative integer")
    source_signal_type_raw = properties.get("art")
    source_name_raw = properties.get("LSA_Name")
    if not isinstance(source_signal_type_raw, str) or not source_signal_type_raw.strip():
        raise ValueError("Hamburg LSA node properties.art must be a non-empty string")
    if not isinstance(source_name_raw, str) or not source_name_raw.strip():
        raise ValueError("Hamburg LSA node properties.LSA_Name must be a non-empty string")
    node_id = str(source_knoten)
    return HamburgLsaNode(
        feature_id=feature_id,
        node_id=node_id,
        lsanr=node_id,
        source_knoten=source_knoten,
        official_name=source_name_raw.strip(),
        normalized_road_name_components=road_name_components_from_official_name(source_name_raw),
        signal_type=source_signal_type_raw.strip(),
        longitude=longitude,
        latitude=latitude,
        source_name_raw=source_name_raw,
        source_signal_type_raw=source_signal_type_raw,
    )


def _required_nonnegative_integer(payload: Mapping[str, Any], key: str) -> int:
    value = payload.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"Hamburg LSA node FeatureCollection.{key} must be a non-negative integer")
    return value


def _validate_utc_timestamp(value: str) -> None:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("Hamburg LSA node timeStamp must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        raise ValueError("Hamburg LSA node timeStamp must use UTC")


def _validate_self_link(links: Any, *, request_url: str) -> None:
    if not isinstance(links, list):
        raise ValueError("Hamburg LSA node FeatureCollection.links must be a list")
    self_links = [item for item in links if isinstance(item, Mapping) and item.get("rel") == "self"]
    if len(self_links) != 1:
        raise ValueError("Hamburg LSA node FeatureCollection must contain exactly one self link")
    href = self_links[0].get("href")
    if not isinstance(href, str):
        raise ValueError("Hamburg LSA node self link must contain a URL")
    self_bbox = validate_hamburg_lsa_node_items_url(href)
    request_bbox = validate_hamburg_lsa_node_items_url(request_url)
    if self_bbox != request_bbox or _normalized_query(href) != _normalized_query(request_url):
        raise ValueError("Hamburg LSA node self link does not identify the exact request")
    if self_links[0].get("type") != "application/geo+json":
        raise ValueError("Hamburg LSA node self link must declare application/geo+json")


def _normalized_query(url: str) -> tuple[tuple[str, str], ...]:
    query = parse_qs(urlparse(url).query, keep_blank_values=True, strict_parsing=True)
    return tuple(sorted((key, values[0]) for key, values in query.items()))


def _validate_sha256(value: str) -> str:
    normalized = value.strip().lower()
    if len(normalized) != 64 or any(character not in "0123456789abcdef" for character in normalized):
        raise ValueError("expected_sha256 must be a 64-character hexadecimal SHA-256")
    return normalized


def _finite_coordinate(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"Hamburg LSA node {label} must be numeric")
    coordinate = float(value)
    if not math.isfinite(coordinate):
        raise ValueError(f"Hamburg LSA node {label} must be finite")
    return coordinate


def _json_safe(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_json_safe(item) for item in value]
    return value


def _urlopen_bytes(request: Request, timeout_seconds: float) -> bytes:
    with urlopen(request, timeout=timeout_seconds) as response:
        return response.read()
