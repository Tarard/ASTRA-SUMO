from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, replace
from datetime import date, datetime, time, timedelta
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence
from urllib.parse import parse_qs, unquote_plus, urlencode, urljoin, urlparse
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

from .digital_twin import (
    UTC,
    CountObservation,
    CountStream,
    SignalObservation,
    SignalStream,
    parse_iso_datetime,
    parse_phenomenon_interval,
)


HAMBURG_COUNT_SERVICE = "HH_STA_Verkehrsdaten_Kfz_Infrarotdetektoren"
HAMBURG_COUNT_LAYER = "Anzahl_Kfz_Zaehlfeld_5-Min"
HAMBURG_COUNT_STATION_LAYER = "Anzahl_Kfz_Zaehlstelle_15-Min"
HAMBURG_SIGNAL_SERVICE = "HH_STA_traffic_lights"
HAMBURG_SANDTORKAI_SIGNAL_SNAPSHOT_DATE = "2026-07-18"
OFFICIAL_COUNT_METADATA_URL = (
    "https://metaver.de/trefferanzeige?cmd=doShowDocument&docuuid=2936465E-C045-4F5D-8614-24C3FBB522E2"
)
OFFICIAL_SIGNAL_METADATA_URL = (
    "https://metaver.de/trefferanzeige?cmd=doShowDocument&docuuid=AB32CF78-389A-4579-9C5E-867EF31CA225"
)
OFFICIAL_SIGNAL_CATALOG_URL = (
    "https://suche.transparenz.hamburg.de/dataset/traffic-lights-data-hamburg6"
)
OFFICIAL_SIGNAL_CATALOG_API_URL = (
    "https://suche.transparenz.hamburg.de/api/3/action/package_show?"
    "id=traffic-lights-data-hamburg6"
)
HAMBURG_SIGNAL_CATALOG_DATASET_ID = "traffic-lights-data-hamburg6"

_HAMBURG_CATALOG_ALLOWED_HOSTS = frozenset(
    {
        "suche.transparenz.hamburg.de",
        "tld.iot.hamburg.de",
        "daten-hamburg.de",
    }
)
_HAMBURG_CATALOG_RESOURCE_NAMES = {
    "primary_signal": (
        "STA Layerabfrage: Primärsignal je Fahrspurbeziehung - letzte 10 "
        "Beobachtungswerte (JSON)"
    ),
    "signal_program": (
        "STA Layerabfrage: Signalprogrammstatus je Fahrspurbeziehung - letzte 10 "
        "Beobachtungswerte (JSON)"
    ),
    "cycle_second": (
        "STA Layerabfrage: Wellensekunde je Fahrspurbeziehung - letzte 10 "
        "Beobachtungswerte (JSON)"
    ),
    "map": "MAP-Dateien",
    "ocit_c": "OCIT-C Dateien",
    "usage_guide": "TLD Usage Guide",
}

_HAMBURG_MAP_ASSET_PATTERN = re.compile(
    r"^(?P<ocit_stem>MAP_ITS_(?P<district>\d{2})_(?P<node>\d+)_(?P<version>\d+\.\d+))"
    r"(?P<revision>_R\d+)?_Quelle_ETRS89\.(?P<extension>xml|kml)$",
    re.IGNORECASE,
)
_HAMBURG_OCIT_C_ASSET_PATTERN = re.compile(
    r"^(?P<ocit_stem>MAP_ITS_(?P<district>\d{2})_(?P<node>\d+)_(?P<version>\d+\.\d+))"
    r"\.xml$",
    re.IGNORECASE,
)

_SANDTORKAI_PRIMARY_SIGNAL_SNAPSHOT: tuple[tuple[int, str, str, str, str, str], ...] = (
    (71214, "228", "7", "19", "25", "K2"),
    (71217, "228", "1", "1", "8", "K3"),
    (71221, "228", "2", "2", "7", "K3"),
    (71224, "228", "8", "19", "26", "K2"),
    (71230, "228", "5", "9", "12", "K8"),
    (71231, "228", "9", "18", "27", "K2"),
    (71240, "228", "16", "41", "37", "K4"),
    (71243, "228", "17", "41", "38", "K4"),
    (71251, "228", "13", "34", "37", "K1"),
    (71253, "228", "14", "35", "38", "K1"),
    (71264, "228", "22", "9", "7", "K8"),
    (71266, "228", "20", "47", "50", "K7"),
    (71269, "228", "23", "9", "8", "K8"),
    (71272, "228", "19", "46", "49", "K7"),
    (81426, "228", "3", "3", "12", "K3"),
    (81439, "228", "10", "17", "24", "K6"),
    (67952, "2394", "5", "2", "14", "K1"),
    (68005, "2394", "7", "6", "4", "K4"),
    (68006, "2394", "2", "11", "4", "K7"),
    (68841, "2394", "9", "7", "14", "K5"),
    (68904, "2394", "8", "6", "5", "K4"),
    (69184, "2394", "3", "12", "5", "K7"),
    (71446, "2394", "1", "10", "9", "K7"),
    (71448, "2394", "6", "3", "9", "K2"),
    (87890, "2421", "8", "3", "11", "K3"),
    (87934, "2421", "2", "3", "6", "K3"),
    (87937, "2421", "4", "7", "2", "K1"),
)


@dataclass(frozen=True)
class HamburgSignalAsset:
    node_id: str
    map_xml: str
    map_kml: str
    ocit_xml: str


@dataclass(frozen=True)
class HamburgCorridorPreset:
    preset_id: str
    display_name: str
    bbox: str
    count_node_ids: tuple[str, ...]
    signal_node_ids: tuple[str, ...]
    node_names: Mapping[str, str]
    node_centers: Mapping[str, tuple[float, float]]
    signal_assets: tuple[HamburgSignalAsset, ...]
    timezone_name: str = "Europe/Berlin"
    count_api_base: str = "https://iot.hamburg.de/v1.1/"
    primary_signal_api_base: str = "https://tld.iot.hamburg.de/v1.0/"
    signal_api_base: str = "https://tld.iot.hamburg.de/v1.1/"
    signal_asset_base: str = "https://daten-hamburg.de/tlf_public/"


SANDTORKAI_THREE_INTERSECTIONS = HamburgCorridorPreset(
    preset_id="hamburg_sandtorkai_3_intersections",
    display_name="Am Sandtorkai: 0228 - 2421 - 2394",
    bbox="9.9780,53.5390,10.0005,53.5475",
    count_node_ids=("0228", "2421", "2394"),
    signal_node_ids=("228", "2421", "2394"),
    node_names={
        "0228": "Baumwall / Niederbaumbruecke / U-Bahnhof",
        "2421": "Am Sandtorkai / Am Kaiserkai",
        "2394": "Am Sandtorkai / Am Sandtorpark",
    },
    node_centers={
        "0228": (9.9820160581, 53.5442574511),
        "2421": (9.9850028399, 53.5427011625),
        "2394": (9.9951328727, 53.5435027113),
    },
    signal_assets=(
        HamburgSignalAsset(
            node_id="0228",
            map_xml="MAP_ITS_02_228_18.5_R5_Quelle_ETRS89.xml",
            map_kml="MAP_ITS_02_228_18.5_R5_Quelle_ETRS89.kml",
            ocit_xml="OCIT-C/MAP_ITS_02_228_18.5.xml",
        ),
        HamburgSignalAsset(
            node_id="2421",
            map_xml="MAP_ITS_24_2421_4.2_R2_Quelle_ETRS89.xml",
            map_kml="MAP_ITS_24_2421_4.2_R2_Quelle_ETRS89.kml",
            ocit_xml="OCIT-C/MAP_ITS_24_2421_4.2.xml",
        ),
        HamburgSignalAsset(
            node_id="2394",
            map_xml="MAP_ITS_23_2394_9.3_R3_Quelle_ETRS89.xml",
            map_kml="MAP_ITS_23_2394_9.3_R3_Quelle_ETRS89.kml",
            ocit_xml="OCIT-C/MAP_ITS_23_2394_9.3.xml",
        ),
    ),
)


Transport = Callable[[Request, float], bytes]


class HamburgCatalogUnavailableError(RuntimeError):
    """Raised only when the official catalog API cannot be reached."""


class HamburgSignalAssetDirectoryUnavailableError(RuntimeError):
    """Raised when a catalog-published official signal-asset index is unreachable."""


@dataclass(frozen=True)
class HamburgCatalogResource:
    key: str
    resource_id: str
    name: str
    format: str
    url: str


@dataclass(frozen=True)
class HamburgTrafficLightCatalog:
    dataset_id: str
    package_id: str
    metadata_modified: str
    resource_count: int
    catalog_api_url: str
    fetched_at_utc: str
    raw_sha256: str
    primary_signal: HamburgCatalogResource
    signal_program: HamburgCatalogResource
    cycle_second: HamburgCatalogResource
    map_files: HamburgCatalogResource
    ocit_c_files: HamburgCatalogResource
    usage_guide: HamburgCatalogResource
    primary_signal_api_base: str
    auxiliary_signal_api_base: str
    map_asset_base: str
    ocit_c_asset_base: str

    def selected_resources(self) -> dict[str, HamburgCatalogResource]:
        return {
            "primary_signal": self.primary_signal,
            "signal_program": self.signal_program,
            "cycle_second": self.cycle_second,
            "map": self.map_files,
            "ocit_c": self.ocit_c_files,
            "usage_guide": self.usage_guide,
        }


@dataclass
class _CollectionAttempt:
    ok: bool
    values: list[dict[str, Any]]
    pages: list[dict[str, Any]]
    request_urls: list[str]
    attempts: int
    attempt_errors: list[dict[str, Any]]
    exception: Exception | None = None


class _DirectoryHrefParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.hrefs: list[str] = []

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        if tag.casefold() != "a":
            return
        for name, value in attrs:
            if name.casefold() == "href" and value:
                self.hrefs.append(value)
                return


class SensorThingsClient:
    """Small, auditable OGC SensorThings JSON client.

    It follows only server-provided ``@iot.nextLink`` URLs on the same origin.  It
    intentionally does not scrape Hamburg web pages; the metadata pages above are
    provenance references, while all records come from the official REST service.
    """

    def __init__(
        self,
        base_url: str,
        *,
        timeout_seconds: float = 60.0,
        max_pages: int = 200,
        max_records: int = 200_000,
        transport: Transport | None = None,
    ) -> None:
        parsed = urlparse(base_url)
        if parsed.scheme != "https" or not parsed.netloc:
            raise ValueError("SensorThings base_url must be an absolute HTTPS URL")
        self.base_url = base_url.rstrip("/") + "/"
        self.timeout_seconds = timeout_seconds
        self.max_pages = max_pages
        self.max_records = max_records
        self.transport = transport or _urlopen_bytes
        self._origin = (parsed.scheme, parsed.netloc)

    def collection(
        self,
        entity_path: str,
        *,
        params: Mapping[str, str | int] | None = None,
        record_limit: int | None = None,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str]]:
        url = self._url(entity_path, params=params)
        values: list[dict[str, Any]] = []
        pages: list[dict[str, Any]] = []
        request_urls: list[str] = []
        for _page_index in range(self.max_pages):
            self._assert_same_origin(url)
            request = Request(url, headers={"Accept": "application/json", "User-Agent": "Torii-SUMO/1.0"})
            payload = json.loads(self.transport(request, self.timeout_seconds).decode("utf-8"))
            if not isinstance(payload, dict) or not isinstance(payload.get("value", []), list):
                raise ValueError(f"SensorThings response is not a collection: {url}")
            pages.append(payload)
            request_urls.append(url)
            values.extend(item for item in payload.get("value", []) if isinstance(item, dict))
            if record_limit is not None and len(values) >= record_limit:
                return values[:record_limit], pages, request_urls
            if len(values) > self.max_records:
                raise ValueError(f"SensorThings record limit exceeded ({self.max_records})")
            next_link = payload.get("@iot.nextLink")
            if not next_link:
                return values, pages, request_urls
            url = str(next_link)
        raise ValueError(f"SensorThings page limit exceeded ({self.max_pages})")

    def bytes(self, url: str) -> bytes:
        parsed = urlparse(url)
        if parsed.scheme != "https" or not parsed.netloc:
            raise ValueError("download URL must be absolute HTTPS")
        request = Request(url, headers={"Accept": "application/xml,text/xml,*/*", "User-Agent": "Torii-SUMO/1.0"})
        return self.transport(request, self.timeout_seconds)

    def _url(self, entity_path: str, *, params: Mapping[str, str | int] | None) -> str:
        url = urljoin(self.base_url, entity_path.lstrip("/"))
        return f"{url}?{urlencode(params)}" if params else url

    def _assert_same_origin(self, url: str) -> None:
        parsed = urlparse(url)
        if (parsed.scheme, parsed.netloc) != self._origin:
            raise ValueError(f"refusing cross-origin SensorThings pagination URL: {url}")


def _client_api_base_url(client: SensorThingsClient) -> str:
    base_url = getattr(client, "base_url", None)
    if not isinstance(base_url, str):
        raise ValueError("SensorThings client must expose its HTTPS base_url for provenance and cache isolation")
    parsed = urlparse(base_url)
    if parsed.scheme != "https" or not parsed.netloc:
        raise ValueError("SensorThings client base_url must be an absolute HTTPS URL")
    return base_url.rstrip("/") + "/"


def parse_hamburg_traffic_light_catalog(
    payload: Mapping[str, Any],
    *,
    catalog_api_url: str = OFFICIAL_SIGNAL_CATALOG_API_URL,
    fetched_at_utc: str | None = None,
    raw_sha256: str | None = None,
) -> HamburgTrafficLightCatalog:
    """Parse the official CKAN/ODG JSON record without crawling its HTML page."""

    _validate_catalog_api_url(catalog_api_url)
    if payload.get("success") is not True:
        raise ValueError("Hamburg traffic-light catalog response did not report success=true")
    result = payload.get("result")
    if not isinstance(result, Mapping):
        raise ValueError("Hamburg traffic-light catalog response has no result object")
    if result.get("name") != HAMBURG_SIGNAL_CATALOG_DATASET_ID:
        raise ValueError(
            "Hamburg traffic-light catalog returned an unexpected dataset: "
            f"{result.get('name')!r}"
        )
    resources = result.get("resources")
    if not isinstance(resources, list):
        raise ValueError("Hamburg traffic-light catalog result.resources must be a list")
    metadata_modified = str(result.get("metadata_modified", "")).strip()
    if not metadata_modified:
        raise ValueError("Hamburg traffic-light catalog has no metadata_modified version marker")
    try:
        datetime.fromisoformat(metadata_modified.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(
            "Hamburg traffic-light catalog metadata_modified is not an ISO timestamp"
        ) from exc

    selected: dict[str, HamburgCatalogResource] = {}
    for key, expected_name in _HAMBURG_CATALOG_RESOURCE_NAMES.items():
        matches = [
            item
            for item in resources
            if isinstance(item, Mapping) and item.get("name") == expected_name
        ]
        if len(matches) != 1:
            raise ValueError(
                f"Hamburg traffic-light catalog requires exactly one {key!r} resource "
                f"named {expected_name!r}; found {len(matches)}"
            )
        selected[key] = _parse_catalog_resource(key, matches[0])

    resource_ids = [resource.resource_id for resource in selected.values()]
    if len(resource_ids) != len(set(resource_ids)):
        raise ValueError("Hamburg traffic-light catalog selected resources have duplicate ids")

    primary_base = _sensorthings_base_from_catalog_resource(
        selected["primary_signal"],
        expected_version="v1.0",
        expected_layer="primary_signal",
    )
    signal_program_base = _sensorthings_base_from_catalog_resource(
        selected["signal_program"],
        expected_version="v1.1",
        expected_layer="signal_program",
    )
    cycle_second_base = _sensorthings_base_from_catalog_resource(
        selected["cycle_second"],
        expected_version="v1.1",
        expected_layer="cycle_second",
    )
    if signal_program_base != cycle_second_base:
        raise ValueError(
            "Hamburg signal_program and cycle_second resources resolve to different SensorThings bases"
        )

    map_base = _catalog_asset_base(selected["map"], expected_path="/tlf_public/")
    ocit_c_base = _catalog_asset_base(
        selected["ocit_c"],
        expected_path="/tlf_public/OCIT-C/",
    )
    _validate_usage_guide_resource(selected["usage_guide"])
    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return HamburgTrafficLightCatalog(
        dataset_id=HAMBURG_SIGNAL_CATALOG_DATASET_ID,
        package_id=str(result.get("id", "")),
        metadata_modified=metadata_modified,
        resource_count=len(resources),
        catalog_api_url=catalog_api_url,
        fetched_at_utc=fetched_at_utc or _odata_timestamp(datetime.now(UTC)),
        raw_sha256=raw_sha256 or hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
        primary_signal=selected["primary_signal"],
        signal_program=selected["signal_program"],
        cycle_second=selected["cycle_second"],
        map_files=selected["map"],
        ocit_c_files=selected["ocit_c"],
        usage_guide=selected["usage_guide"],
        primary_signal_api_base=primary_base,
        auxiliary_signal_api_base=signal_program_base,
        map_asset_base=map_base,
        ocit_c_asset_base=ocit_c_base,
    )


def download_hamburg_traffic_light_catalog(
    output_dir: Path,
    *,
    catalog_api_url: str = OFFICIAL_SIGNAL_CATALOG_API_URL,
    timeout_seconds: float = 30.0,
    transport: Transport | None = None,
) -> tuple[HamburgTrafficLightCatalog, dict[str, Any]]:
    """Download, validate, and snapshot the official catalog plus its usage guide."""

    _validate_catalog_api_url(catalog_api_url)
    transport = transport or _urlopen_bytes
    request = Request(
        catalog_api_url,
        headers={"Accept": "application/json", "User-Agent": "Torii-SUMO/1.1"},
    )
    try:
        raw_json = transport(request, timeout_seconds)
    except Exception as exc:
        raise HamburgCatalogUnavailableError(
            f"official Hamburg catalog API is unavailable: {type(exc).__name__}: {exc}"
        ) from exc
    if not raw_json:
        raise ValueError("official Hamburg catalog API returned an empty response")

    output_dir.mkdir(parents=True, exist_ok=True)
    raw_path = output_dir / "traffic-lights-data-hamburg6.catalog.raw.json"
    _atomic_write_bytes(raw_path, raw_json)
    raw_sha256 = hashlib.sha256(raw_json).hexdigest()
    try:
        payload = json.loads(raw_json.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("official Hamburg catalog API returned invalid UTF-8 JSON") from exc
    if not isinstance(payload, Mapping):
        raise ValueError("official Hamburg catalog API returned a non-object JSON value")

    fetched_at_utc = _odata_timestamp(datetime.now(UTC))
    catalog = parse_hamburg_traffic_light_catalog(
        payload,
        catalog_api_url=catalog_api_url,
        fetched_at_utc=fetched_at_utc,
        raw_sha256=raw_sha256,
    )
    usage_url_hash = hashlib.sha256(catalog.usage_guide.url.encode("utf-8")).hexdigest()[:12]
    usage_path = output_dir / f"TLD_UsageGuide_{usage_url_hash}.pdf"
    usage_cache_hit = usage_path.is_file() and usage_path.stat().st_size > 0
    if not usage_cache_hit:
        usage_request = Request(
            catalog.usage_guide.url,
            headers={"Accept": "application/pdf", "User-Agent": "Torii-SUMO/1.1"},
        )
        usage_bytes = transport(usage_request, timeout_seconds)
        if not usage_bytes.startswith(b"%PDF-"):
            raise ValueError("official TLD Usage Guide resource did not return a PDF document")
        _atomic_write_bytes(usage_path, usage_bytes)

    selected_resources = {
        key: _catalog_resource_payload(resource)
        for key, resource in catalog.selected_resources().items()
    }
    audit: dict[str, Any] = {
        "status": "official_catalog_api",
        "source": "official_catalog_api",
        "catalog_api_url": catalog.catalog_api_url,
        "dataset_id": catalog.dataset_id,
        "package_id": catalog.package_id,
        "metadata_modified": catalog.metadata_modified,
        "resource_count": catalog.resource_count,
        "fetched_at_utc": catalog.fetched_at_utc,
        "raw_catalog": {
            "path": str(raw_path),
            "sha256": raw_sha256,
            "bytes": raw_path.stat().st_size,
        },
        "selected_resources": selected_resources,
        "derived_bases": {
            "primary_signal_api": catalog.primary_signal_api_base,
            "auxiliary_signal_api": catalog.auxiliary_signal_api_base,
            "map_assets": catalog.map_asset_base,
            "ocit_c_assets": catalog.ocit_c_asset_base,
        },
        "usage_guide_download": {
            "url": catalog.usage_guide.url,
            "path": str(usage_path),
            "sha256": sha256_file(usage_path),
            "bytes": usage_path.stat().st_size,
            "cache_hit": usage_cache_hit,
        },
    }
    selected_path = output_dir / "traffic-lights-data-hamburg6.catalog.selected.json"
    _atomic_write_json(selected_path, audit)
    audit["selected_snapshot"] = {
        "path": str(selected_path),
        "sha256": sha256_file(selected_path),
        "bytes": selected_path.stat().st_size,
    }
    return catalog, audit


def _parse_catalog_resource(
    key: str,
    payload: Mapping[str, Any],
) -> HamburgCatalogResource:
    resource_id = str(payload.get("id", "")).strip()
    name = str(payload.get("name", "")).strip()
    resource_format = str(payload.get("format", "")).strip().upper()
    url = str(payload.get("url", "")).strip()
    if not resource_id:
        raise ValueError(f"Hamburg catalog {key!r} resource has no id")
    expected_formats = {
        "primary_signal": "JSON",
        "signal_program": "JSON",
        "cycle_second": "JSON",
        "map": "HTML",
        "ocit_c": "HTML",
        "usage_guide": "PDF",
    }
    if resource_format != expected_formats[key]:
        raise ValueError(
            f"Hamburg catalog {key!r} resource has format {resource_format!r}; "
            f"expected {expected_formats[key]!r}"
        )
    _validate_official_catalog_resource_url(url)
    return HamburgCatalogResource(
        key=key,
        resource_id=resource_id,
        name=name,
        format=resource_format,
        url=url,
    )


def _validate_catalog_api_url(url: str) -> None:
    parsed = _validate_official_catalog_resource_url(url)
    if (
        parsed.hostname != "suche.transparenz.hamburg.de"
        or parsed.path != "/api/3/action/package_show"
    ):
        raise ValueError("Hamburg catalog API URL must use the official package_show endpoint")
    if parse_qs(parsed.query).get("id") != [HAMBURG_SIGNAL_CATALOG_DATASET_ID]:
        raise ValueError(
            "Hamburg catalog API URL must select traffic-lights-data-hamburg6 exactly"
        )


def _validate_official_catalog_resource_url(url: str):
    parsed = urlparse(url)
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError(f"invalid Hamburg catalog resource URL: {url}") from exc
    if (
        parsed.scheme != "https"
        or parsed.hostname not in _HAMBURG_CATALOG_ALLOWED_HOSTS
        or parsed.username is not None
        or parsed.password is not None
        or port not in (None, 443)
    ):
        raise ValueError(f"Hamburg catalog resource URL is not allowlisted HTTPS: {url}")
    if parsed.fragment:
        raise ValueError(f"Hamburg catalog resource URL must not contain a fragment: {url}")
    return parsed


def _sensorthings_base_from_catalog_resource(
    resource: HamburgCatalogResource,
    *,
    expected_version: str,
    expected_layer: str,
) -> str:
    parsed = _validate_official_catalog_resource_url(resource.url)
    if parsed.hostname != "tld.iot.hamburg.de":
        raise ValueError(f"Hamburg {resource.key} resource must use tld.iot.hamburg.de")
    if parsed.path.rstrip("/") != f"/{expected_version}/Datastreams":
        raise ValueError(
            f"Hamburg {resource.key} resource must use /{expected_version}/Datastreams"
        )
    decoded_query = unquote_plus(parsed.query)
    if HAMBURG_SIGNAL_SERVICE not in decoded_query or expected_layer not in decoded_query:
        raise ValueError(
            f"Hamburg {resource.key} resource URL does not select layer {expected_layer!r}"
        )
    return f"https://tld.iot.hamburg.de/{expected_version}/"


def _catalog_asset_base(
    resource: HamburgCatalogResource,
    *,
    expected_path: str,
) -> str:
    try:
        return _validate_hamburg_signal_asset_directory_base(
            resource.url,
            expected_path=expected_path,
        )
    except ValueError as exc:
        raise ValueError(
            f"Hamburg {resource.key} resource must resolve to "
            f"https://daten-hamburg.de{expected_path}"
        ) from exc


def _validate_hamburg_signal_asset_directory_base(
    url: str,
    *,
    expected_path: str,
) -> str:
    parsed = _validate_official_catalog_resource_url(url)
    if (
        parsed.hostname != "daten-hamburg.de"
        or parsed.path != expected_path
        or parsed.query
    ):
        raise ValueError(
            "Hamburg signal-asset directory must resolve to "
            f"https://daten-hamburg.de{expected_path}"
        )
    return f"https://daten-hamburg.de{expected_path}"


def _validate_usage_guide_resource(resource: HamburgCatalogResource) -> None:
    parsed = _validate_official_catalog_resource_url(resource.url)
    if (
        parsed.hostname != "daten-hamburg.de"
        or not parsed.path.startswith("/tlf_public/")
        or not parsed.path.lower().endswith(".pdf")
        or parsed.query
    ):
        raise ValueError("Hamburg TLD Usage Guide resource must be an official PDF")


def _catalog_resource_payload(resource: HamburgCatalogResource) -> dict[str, str]:
    return {
        "id": resource.resource_id,
        "name": resource.name,
        "format": resource.format,
        "url": resource.url,
    }


def fetch_hamburg_count_streams(
    client: SensorThingsClient,
    node_ids: Sequence[str],
) -> tuple[list[CountStream], dict[str, Any]]:
    streams: list[CountStream] = []
    raw_pages: dict[str, Any] = {}
    request_urls: list[str] = []
    requested, _requested_by_api_id = _hamburg_count_node_ids(node_ids)
    for node_id, api_node_id in requested:
        values, pages, urls = client.collection(
            "Datastreams",
            params={
                "$top": 100,
                "$expand": "Thing($expand=Locations)",
                "$filter": (
                    f"properties/knotenName eq '{api_node_id}' and "
                    f"properties/serviceName eq '{HAMBURG_COUNT_SERVICE}' and "
                    f"properties/layerName eq '{HAMBURG_COUNT_LAYER}'"
                ),
            },
        )
        raw_pages[node_id] = pages
        request_urls.extend(urls)
        for stream in parse_hamburg_count_streams(values):
            returned_api_id = _hamburg_count_api_node_id(stream.node_id)
            if returned_api_id != api_node_id:
                raise ValueError(
                    f"Hamburg count metadata returned node {stream.node_id!r} for requested node {node_id!r}"
                )
            streams.append(replace(stream, node_id=node_id))
    unique = {stream.stream_id: stream for stream in streams}
    if len(unique) != len(streams):
        raise ValueError("Hamburg count metadata returned duplicate datastream ids")
    return sorted(unique.values(), key=lambda item: (item.node_id, item.asset_id, item.stream_id)), {
        "request_urls": request_urls,
        "pages_by_node": raw_pages,
    }


def fetch_hamburg_count_station_streams(
    client: SensorThingsClient,
    node_ids: Sequence[str],
) -> tuple[list[CountStream], dict[str, Any]]:
    """Fetch official 15-minute cross-section streams for declared signal nodes."""

    requested_pairs, requested_by_api_id = _hamburg_count_node_ids(node_ids)
    requested = tuple(node_id for node_id, _api_node_id in requested_pairs)
    values, pages, request_urls = client.collection(
        "Datastreams",
        params={
            "$top": 1000,
            "$expand": "Thing($expand=Locations)",
            "$filter": (
                f"properties/serviceName eq '{HAMBURG_COUNT_SERVICE}' and "
                f"properties/layerName eq '{HAMBURG_COUNT_STATION_LAYER}'"
            ),
        },
    )
    requested_api_ids = set(requested_by_api_id)
    selected_values = []
    excluded_off_scope_unsupported_streams = []
    for value in values:
        properties = _mapping(value.get("properties"))
        composition = str(properties.get("zusammensetzung", ""))
        composition_nodes = {
            member.partition("-")[0].strip()
            for member in composition.split(",")
            if "-" in member
        }
        composition_api_ids = {
            _hamburg_count_api_node_id(node_id)
            for node_id in composition_nodes
            if node_id.isdecimal()
        }
        if not composition_nodes or composition_api_ids & requested_api_ids:
            selected_values.append(value)
            continue
        try:
            parse_hamburg_count_streams([value])
        except ValueError as exc:
            excluded_off_scope_unsupported_streams.append(
                {
                    "stream_id": int(value["@iot.id"]),
                    "composition": composition,
                    "composition_node_hints": sorted(composition_nodes),
                    "reason": str(exc),
                }
            )
    parsed = parse_hamburg_count_streams(selected_values)
    selected = [
        replace(stream, node_id=requested_by_api_id[api_node_id])
        for stream in parsed
        if (api_node_id := _hamburg_count_api_node_id(stream.node_id)) in requested_by_api_id
    ]
    unique = {stream.stream_id: stream for stream in selected}
    if len(unique) != len(selected):
        raise ValueError("Hamburg count station metadata returned duplicate datastream ids")
    selected_ids = set(unique)
    raw_by_id = {int(value["@iot.id"]): value for value in selected_values}
    pages_by_node = {
        node_id: [{"value": [raw_by_id[stream.stream_id] for stream in selected if stream.node_id == node_id]}]
        for node_id in requested
    }
    return sorted(unique.values(), key=lambda item: (item.node_id, item.station_arm, item.direction_code)), {
        "request_urls": request_urls,
        "inventory_pages": pages,
        "pages_by_node": pages_by_node,
        "selected_stream_ids": sorted(selected_ids),
        "excluded_off_scope_unsupported_streams": excluded_off_scope_unsupported_streams,
        "requested_node_ids": list(requested),
        "layer": HAMBURG_COUNT_STATION_LAYER,
    }


def _hamburg_count_node_ids(
    node_ids: Sequence[str],
) -> tuple[tuple[tuple[str, str], ...], dict[str, str]]:
    requested: list[tuple[str, str]] = []
    requested_by_api_id: dict[str, str] = {}
    for raw_node_id in node_ids:
        node_id = str(raw_node_id).strip()
        api_node_id = _hamburg_count_api_node_id(node_id)
        if api_node_id in requested_by_api_id:
            raise ValueError(f"duplicate Hamburg count node id: {node_id!r}")
        requested.append((node_id, api_node_id))
        requested_by_api_id[api_node_id] = node_id
    if not requested:
        raise ValueError("Hamburg count node_ids must be non-empty")
    return tuple(requested), requested_by_api_id


def _hamburg_count_api_node_id(node_id: str) -> str:
    value = str(node_id).strip()
    if not value or not value.isdecimal():
        raise ValueError("Hamburg count node ids must be decimal")
    return str(int(value)).zfill(4)


def parse_hamburg_count_streams(values: Iterable[Mapping[str, Any]]) -> list[CountStream]:
    streams: list[CountStream] = []
    for value in values:
        properties = _mapping(value.get("properties"))
        thing = _mapping(value.get("Thing"))
        thing_properties = _mapping(thing.get("properties"))
        locations = thing.get("Locations") if isinstance(thing.get("Locations"), list) else []
        point = _first_point(locations) or _coordinate_pair(value.get("observedArea"))
        if point is None:
            raise ValueError(f"count datastream {value.get('@iot.id')} has no point location")
        stream_id = int(value["@iot.id"])
        composition, composition_node = _parse_count_station_composition(properties, stream_id=stream_id)
        declared_node = str(properties.get("knotenName", "")).strip()
        if declared_node and composition_node and declared_node != composition_node:
            raise ValueError(
                f"count datastream {stream_id} node {declared_node!r} conflicts with composition node "
                f"{composition_node!r}"
            )
        node_id = declared_node or composition_node
        if not node_id:
            raise ValueError(f"count datastream {stream_id} has no node identity")
        station_arm = str(properties.get("knotenarm", "")).strip()
        direction_code = str(properties.get("direction", "")).strip()
        if composition and (not station_arm or direction_code not in {"0", "1", "2"}):
            raise ValueError(f"count station datastream {stream_id} has invalid arm or direction metadata")
        asset_id = str(
            (thing_properties.get("assetID") or "")
            if composition
            else properties.get("assetID") or thing_properties.get("assetID") or ""
        ).strip()
        if not asset_id:
            raise ValueError(f"count datastream {stream_id} has no asset identity")
        streams.append(
            CountStream(
                stream_id=stream_id,
                thing_id=_optional_int(thing.get("@iot.id")),
                node_id=node_id,
                asset_id=asset_id,
                direction=str(thing_properties.get("richtung", "")),
                lane_use=str(properties.get("fahrspur", "")),
                longitude=point[0],
                latitude=point[1],
                operation_start=str(thing_properties.get("operationStart", "")),
                layer_name=str(properties.get("layerName", "")),
                direction_code=direction_code,
                station_arm=station_arm,
                composition=composition,
            )
        )
    return streams


def _parse_count_station_composition(
    properties: Mapping[str, Any],
    *,
    stream_id: int,
) -> tuple[tuple[str, ...], str]:
    raw = str(properties.get("zusammensetzung", "")).strip()
    if not raw:
        return (), ""
    members = tuple(part.strip() for part in raw.split(",") if part.strip())
    if len(members) != len(set(members)):
        raise ValueError(f"count station datastream {stream_id} repeats a composition member")
    matches = [re.fullmatch(r"([0-9]+)-(Z\.[0-9]+(?:_[0-9]+)*)", member) for member in members]
    if not members or any(match is None for match in matches):
        raise ValueError(f"count station datastream {stream_id} has an invalid composition")
    nodes = {match.group(1) for match in matches if match is not None}
    if len(nodes) != 1:
        raise ValueError(f"count station datastream {stream_id} spans multiple official nodes")
    return members, next(iter(nodes))


def fetch_hamburg_count_observations(
    client: SensorThingsClient,
    streams: Sequence[CountStream],
    *,
    local_date: date,
    timezone_name: str = "Europe/Berlin",
) -> tuple[dict[int, list[CountObservation]], dict[str, Any]]:
    zone = ZoneInfo(timezone_name)
    day_begin = datetime.combine(local_date, time.min, tzinfo=zone).astimezone(UTC)
    day_end = (datetime.combine(local_date, time.min, tzinfo=zone) + timedelta(days=1)).astimezone(UTC)
    observations: dict[int, list[CountObservation]] = {}
    raw_pages: dict[str, Any] = {}
    request_urls: list[str] = []
    filter_text = f"phenomenonTime ge {_odata_timestamp(day_begin)} and phenomenonTime lt {_odata_timestamp(day_end)}"
    for stream in streams:
        values, pages, urls = client.collection(
            f"Datastreams({stream.stream_id})/Observations",
            params={"$top": 1000, "$orderby": "phenomenonTime asc", "$filter": filter_text},
        )
        observations[stream.stream_id] = parse_hamburg_count_observations(stream.stream_id, values)
        raw_pages[str(stream.stream_id)] = pages
        request_urls.extend(urls)
    return observations, {
        "local_date": local_date.isoformat(),
        "timezone": timezone_name,
        "query_begin_utc": _odata_timestamp(day_begin),
        "query_end_utc": _odata_timestamp(day_end),
        "request_urls": request_urls,
        "pages_by_stream": raw_pages,
    }


def parse_hamburg_count_observations(
    stream_id: int,
    values: Iterable[Mapping[str, Any]],
) -> list[CountObservation]:
    observations: list[CountObservation] = []
    for value in values:
        begin, end = parse_phenomenon_interval(str(value.get("phenomenonTime", "")), default_seconds=300)
        result = value.get("result")
        if isinstance(result, bool) or not isinstance(result, (int, float)) or not float(result).is_integer():
            raise ValueError(f"count observation {value.get('@iot.id')} has a non-integer result")
        count = int(result)
        if count < 0:
            raise ValueError(f"count observation {value.get('@iot.id')} has a negative result")
        observations.append(
            CountObservation(
                stream_id=stream_id,
                observation_id=_optional_int(value.get("@iot.id")),
                begin_utc=begin,
                end_utc=end,
                count=count,
                result_time=str(value.get("resultTime", "")),
            )
        )
    return sorted(observations, key=lambda item: (item.begin_utc, item.observation_id or -1))


def fetch_hamburg_signal_streams(
    client: SensorThingsClient,
    signal_node_ids: Sequence[str],
    *,
    layers: Sequence[str] = ("primary_signal", "signal_program"),
    motor_vehicle_only: bool = True,
    max_retries: int = 1,
    max_workers: int = 1,
    spatial_fallback: bool = True,
    cache_dir: Path | None = None,
) -> tuple[list[SignalStream], dict[str, Any]]:
    if not layers:
        raise ValueError("at least one signal layer is required")
    if not 0 <= max_retries <= 5:
        raise ValueError("max_retries must be between 0 and 5")
    if not 1 <= max_workers <= 8:
        raise ValueError("max_workers must be between 1 and 8")

    api_base_url = _client_api_base_url(client)
    indexed_nodes = list(enumerate(signal_node_ids))
    fetched: dict[int, dict[str, Any]] = {}
    layer_filter = " or ".join(f"properties/layerName eq '{layer}'" for layer in layers)

    def fetch_node(index: int, node_id: str) -> tuple[int, dict[str, Any]]:
        cache_path = _signal_metadata_cache_path(
            cache_dir,
            node_id=node_id,
            layers=layers,
            motor_vehicle_only=motor_vehicle_only,
            api_base_url=api_base_url,
        )
        cached = _load_signal_metadata_cache(
            cache_path,
            node_id=node_id,
            layers=layers,
            motor_vehicle_only=motor_vehicle_only,
            api_base_url=api_base_url,
        )
        if cached is not None:
            cached["raw"]["cache_hit"] = True
            return index, cached

        def finish(result: dict[str, Any]) -> tuple[int, dict[str, Any]]:
            if cache_path is not None and result["values"]:
                result["raw"]["cache_hit"] = False
                _write_signal_metadata_cache(
                    cache_path,
                    result,
                    node_id=node_id,
                    layers=layers,
                    motor_vehicle_only=motor_vehicle_only,
                    api_base_url=api_base_url,
                )
            return index, result

        clauses = [
            f"Thing/properties/trafficLightsID eq '{node_id}'",
            f"properties/serviceName eq '{HAMBURG_SIGNAL_SERVICE}'",
            f"({layer_filter})",
        ]
        if motor_vehicle_only:
            clauses.append("Thing/properties/laneType eq 'KFZ'")
        direct = _collection_with_retries(
            client,
            "Datastreams",
            params={
                "$top": 100,
                "$expand": "Thing($expand=Locations)",
                "$filter": " and ".join(clauses),
            },
            # A repeated broad navigation-property timeout is expensive. The
            # bounded spatial fallback below owns the configured retry budget.
            max_retries=0,
        )
        center = _hamburg_signal_node_center(node_id) if spatial_fallback else None
        if direct.ok and (direct.values or center is None):
            return finish({
                "values": direct.values,
                "pages": direct.pages,
                "request_urls": direct.request_urls,
                "raw": {
                    "node_id": node_id,
                    "status": "ok" if direct.values else "empty",
                    "strategy": "datastream_filter",
                    "attempts": direct.attempts,
                    "retry_errors": direct.attempt_errors,
                    "errors": [],
                },
            })

        if center is None:
            error = _collection_error("node_datastreams", direct)
            return index, _failed_signal_node_result(node_id, error)

        direct_trigger = (
            _collection_error("node_datastreams", direct)
            if not direct.ok
            else {
                "stage": "node_datastreams_empty",
                "exception_type": "EmptyCollection",
                "message": "node Datastream filter returned no records",
                "attempts": direct.attempts,
            }
        )

        spatial_filter = _spatial_point_filter(center)
        fallback = _collection_with_retries(
            client,
            "Locations",
            params={
                "$top": 200,
                "$expand": "Things($expand=Datastreams)",
                "$filter": spatial_filter,
            },
            max_retries=max_retries,
        )
        if not fallback.ok:
            result = _failed_signal_node_result(
                node_id,
                _collection_error("node_spatial_fallback", fallback),
            )
            result["raw"]["fallback_trigger"] = direct_trigger
            return index, result

        values = _flatten_spatial_signal_datastreams(
            fallback.values,
            node_id=node_id,
            layers=layers,
            motor_vehicle_only=motor_vehicle_only,
        )
        return finish({
            "values": values,
            "pages": fallback.pages,
            "request_urls": fallback.request_urls,
            "raw": {
                "node_id": node_id,
                "status": "ok" if values else "empty",
                "strategy": "locations_spatial_fallback",
                "attempts": fallback.attempts,
                "retry_errors": fallback.attempt_errors,
                "fallback_trigger": direct_trigger,
                "errors": [],
            },
        })

    if max_workers == 1 or len(indexed_nodes) <= 1:
        for index, node_id in indexed_nodes:
            try:
                _, fetched[index] = fetch_node(index, node_id)
            except Exception as exc:
                fetched[index] = _failed_signal_node_result(
                    node_id,
                    _exception_error("node_fetch", exc),
                )
    else:
        worker_count = min(max_workers, len(indexed_nodes))
        with ThreadPoolExecutor(max_workers=worker_count, thread_name_prefix="hamburg-signal-meta") as executor:
            futures = {
                executor.submit(fetch_node, index, node_id): (index, node_id)
                for index, node_id in indexed_nodes
            }
            for future in as_completed(futures):
                index, node_id = futures[future]
                try:
                    _, fetched[index] = future.result()
                except Exception as exc:
                    fetched[index] = _failed_signal_node_result(
                        node_id,
                        _exception_error("node_fetch", exc),
                    )

    streams: list[SignalStream] = []
    raw_pages: dict[str, Any] = {}
    node_results: dict[str, Any] = {}
    request_urls: list[str] = []
    for index, node_id in indexed_nodes:
        result = fetched[index]
        raw_pages[node_id] = result["pages"]
        request_urls.extend(result["request_urls"])
        parsed_for_node: list[SignalStream] = []
        for value in result["values"]:
            try:
                parsed_for_node.extend(parse_hamburg_signal_streams([value]))
            except Exception as exc:
                result["raw"]["errors"].append(_exception_error("parse_datastream", exc))
        streams.extend(parsed_for_node)
        if result["raw"]["errors"]:
            result["raw"]["status"] = "partial" if parsed_for_node else "error"
        result["raw"]["stream_count"] = len(parsed_for_node)
        result["raw"]["api_base_url"] = api_base_url
        node_results[node_id] = result["raw"]
    unique = {stream.stream_id: stream for stream in streams}
    return sorted(unique.values(), key=lambda item: (int(item.node_id), item.connection_id, item.layer_name)), {
        "request_urls": request_urls,
        "pages_by_node": raw_pages,
        "node_results": node_results,
        "failed_node_ids": [
            node_id for node_id, result in node_results.items() if result.get("status") == "error"
        ],
        "partial_node_ids": [
            node_id for node_id, result in node_results.items() if result.get("status") == "partial"
        ],
        "api_base_url": api_base_url,
    }


def _signal_metadata_cache_path(
    cache_dir: Path | None,
    *,
    node_id: str,
    layers: Sequence[str],
    motor_vehicle_only: bool,
    api_base_url: str,
) -> Path | None:
    if cache_dir is None:
        return None
    identity = json.dumps(
        {
            "node_id": node_id,
            "layers": sorted(set(layers)),
            "motor_vehicle_only": motor_vehicle_only,
            "api_base_url": api_base_url,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:12]
    return Path(cache_dir) / f"signal_metadata_{node_id}_{digest}.json"


def _load_signal_metadata_cache(
    path: Path | None,
    *,
    node_id: str,
    layers: Sequence[str],
    motor_vehicle_only: bool,
    api_base_url: str,
) -> dict[str, Any] | None:
    if path is None or not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if (
        payload.get("schema") != "torii.hamburg-signal-metadata-cache.v2"
        or payload.get("node_id") != node_id
        or payload.get("layers") != sorted(set(layers))
        or payload.get("motor_vehicle_only") is not motor_vehicle_only
        or payload.get("api_base_url") != api_base_url
        or not isinstance(payload.get("captured_at_utc"), str)
        or not isinstance(payload.get("result"), dict)
    ):
        return None
    result = payload["result"]
    if not result.get("values") or not isinstance(result.get("raw"), dict):
        return None
    result["raw"]["cache_captured_at_utc"] = payload["captured_at_utc"]
    return result


def _write_signal_metadata_cache(
    path: Path,
    result: Mapping[str, Any],
    *,
    node_id: str,
    layers: Sequence[str],
    motor_vehicle_only: bool,
    api_base_url: str,
) -> None:
    _atomic_write_json(
        path,
        {
            "schema": "torii.hamburg-signal-metadata-cache.v2",
            "node_id": node_id,
            "layers": sorted(set(layers)),
            "motor_vehicle_only": motor_vehicle_only,
            "api_base_url": api_base_url,
            "captured_at_utc": _odata_timestamp(datetime.now(UTC)),
            "result": dict(result),
        },
    )


def _hamburg_signal_node_center(node_id: str) -> tuple[float, float] | None:
    count_node_id = "0228" if node_id == "228" else node_id
    return SANDTORKAI_THREE_INTERSECTIONS.node_centers.get(count_node_id)


def _spatial_point_filter(center: tuple[float, float]) -> str:
    longitude, latitude = center
    longitude_radius = 0.0025
    latitude_radius = 0.0018
    west, east = longitude - longitude_radius, longitude + longitude_radius
    south, north = latitude - latitude_radius, latitude + latitude_radius
    return (
        "st_within(location, geography'POLYGON(("
        f"{west:.7f} {south:.7f},{east:.7f} {south:.7f},"
        f"{east:.7f} {north:.7f},{west:.7f} {north:.7f},"
        f"{west:.7f} {south:.7f}))')"
    )


def _flatten_spatial_signal_datastreams(
    locations: Iterable[Mapping[str, Any]],
    *,
    node_id: str,
    layers: Sequence[str],
    motor_vehicle_only: bool,
) -> list[dict[str, Any]]:
    values: list[dict[str, Any]] = []
    allowed_layers = set(layers)
    for location in locations:
        things = location.get("Things") if isinstance(location.get("Things"), list) else []
        for thing_value in things:
            if not isinstance(thing_value, Mapping):
                continue
            thing_properties = _mapping(thing_value.get("properties"))
            if str(thing_properties.get("trafficLightsID", "")) != node_id:
                continue
            if motor_vehicle_only and str(thing_properties.get("laneType", "")) != "KFZ":
                continue
            datastreams = (
                thing_value.get("Datastreams") if isinstance(thing_value.get("Datastreams"), list) else []
            )
            thing = {key: value for key, value in thing_value.items() if key != "Datastreams"}
            for datastream_value in datastreams:
                if not isinstance(datastream_value, Mapping):
                    continue
                properties = _mapping(datastream_value.get("properties"))
                if (
                    str(properties.get("serviceName", "")) != HAMBURG_SIGNAL_SERVICE
                    or str(properties.get("layerName", "")) not in allowed_layers
                ):
                    continue
                datastream = dict(datastream_value)
                datastream["Thing"] = thing
                values.append(datastream)
    return values


def _failed_signal_node_result(node_id: str, error: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "values": [],
        "pages": [],
        "request_urls": [],
        "raw": {
            "node_id": node_id,
            "status": "error",
            "strategy": "failed",
            "errors": [dict(error)],
            "stream_count": 0,
        },
    }


def parse_hamburg_signal_streams(values: Iterable[Mapping[str, Any]]) -> list[SignalStream]:
    streams: list[SignalStream] = []
    for value in values:
        properties = _mapping(value.get("properties"))
        thing = _mapping(value.get("Thing"))
        thing_properties = _mapping(thing.get("properties"))
        streams.append(
            SignalStream(
                stream_id=int(value["@iot.id"]),
                thing_id=_optional_int(thing.get("@iot.id")),
                node_id=str(thing_properties.get("trafficLightsID", "")),
                connection_id=str(thing_properties.get("connectionID", "")),
                ingress_lane_id=str(thing_properties.get("ingressLaneID", "")),
                egress_lane_id=str(thing_properties.get("egressLaneID", "")),
                lane_type=str(thing_properties.get("laneType", "")),
                signal_group=str(properties.get("signalGroupID", "")),
                layer_name=str(properties.get("layerName", "")),
                name=str(value.get("name", "")),
            )
        )
    return streams


def hamburg_sandtorkai_primary_signal_snapshot() -> list[SignalStream]:
    """Return a dated official-TLD metadata snapshot for outage-safe mapping only.

    The snapshot supplies stable public datastream and movement identifiers observed from the
    official endpoint on :data:`HAMBURG_SANDTORKAI_SIGNAL_SNAPSHOT_DATE`.  It never substitutes for
    live/historical observations and must leave metadata/history completeness gates unsatisfied
    when the official API is unavailable.
    """

    return [
        SignalStream(
            stream_id=stream_id,
            thing_id=None,
            node_id=node_id,
            connection_id=connection_id,
            ingress_lane_id=ingress_lane_id,
            egress_lane_id=egress_lane_id,
            lane_type="KFZ",
            signal_group=signal_group,
            layer_name="primary_signal",
            name=f"Official TLD metadata snapshot {node_id}_{connection_id}",
        )
        for stream_id, node_id, connection_id, ingress_lane_id, egress_lane_id, signal_group in (
            _SANDTORKAI_PRIMARY_SIGNAL_SNAPSHOT
        )
    ]


def fetch_hamburg_signal_observations(
    client: SensorThingsClient,
    streams: Sequence[SignalStream],
    *,
    begin_utc: datetime,
    end_utc: datetime,
    include_preceding_state: bool = True,
    preceding_lookback: timedelta = timedelta(days=7),
    chunk_duration: timedelta = timedelta(minutes=10),
    max_retries: int = 1,
    max_workers: int = 1,
    cache_dir: Path | None = None,
    retry_incomplete_cache: bool = False,
) -> tuple[dict[int, list[SignalObservation]], dict[str, Any]]:
    _require_aware_datetime(begin_utc, "begin_utc")
    _require_aware_datetime(end_utc, "end_utc")
    query_begin = begin_utc.astimezone(UTC)
    query_end = end_utc.astimezone(UTC)
    if query_end <= query_begin:
        raise ValueError("end_utc must be later than begin_utc")
    if preceding_lookback <= timedelta(0):
        raise ValueError("preceding_lookback must be positive")
    if chunk_duration <= timedelta(0):
        raise ValueError("chunk_duration must be positive")
    if not 0 <= max_retries <= 5:
        raise ValueError("max_retries must be between 0 and 5")
    if not 1 <= max_workers <= 8:
        raise ValueError("max_workers must be between 1 and 8")

    api_base_url = _client_api_base_url(client)
    indexed_streams = list(enumerate(streams))
    fetched: dict[int, dict[str, Any]] = {}

    def fetch_one(index: int, stream: SignalStream) -> tuple[int, dict[str, Any]]:
        try:
            result = _fetch_hamburg_signal_stream_observations(
                client,
                stream,
                begin_utc=query_begin,
                end_utc=query_end,
                include_preceding_state=include_preceding_state,
                preceding_lookback=preceding_lookback,
                chunk_duration=chunk_duration,
                max_retries=max_retries,
                cache_dir=cache_dir,
                api_base_url=api_base_url,
                retry_incomplete_cache=retry_incomplete_cache,
            )
        except Exception as exc:  # Defensive isolation: one stream must not abort the corridor.
            result = _failed_signal_stream_result(stream, exc, stage="stream_fetch")
        result["raw"].setdefault("api_base_url", api_base_url)
        return index, result

    if max_workers == 1 or len(indexed_streams) <= 1:
        for index, stream in indexed_streams:
            _, fetched[index] = fetch_one(index, stream)
    else:
        worker_count = min(max_workers, len(indexed_streams))
        with ThreadPoolExecutor(max_workers=worker_count, thread_name_prefix="hamburg-signal") as executor:
            futures = {
                executor.submit(fetch_one, index, stream): index for index, stream in indexed_streams
            }
            for future in as_completed(futures):
                index, result = future.result()
                fetched[index] = result

    observations: dict[int, list[SignalObservation]] = {}
    raw_pages: dict[str, Any] = {}
    stream_results: dict[str, Any] = {}
    request_urls: list[str] = []
    for index, stream in indexed_streams:
        result = fetched[index]
        stream_key = str(stream.stream_id)
        observations[stream.stream_id] = result["observations"]
        # Keep the pre-existing audit shape intact for callers and saved snapshots.
        raw_pages[stream_key] = result["pages"]
        stream_results[stream_key] = result["raw"]
        request_urls.extend(result["request_urls"])

    failed_stream_ids = [
        int(stream_id)
        for stream_id, result in stream_results.items()
        if result.get("status") == "error"
    ]
    partial_stream_ids = [
        int(stream_id)
        for stream_id, result in stream_results.items()
        if result.get("status") == "partial"
    ]
    return observations, {
        "query_begin_utc": _odata_timestamp(query_begin),
        "query_end_utc": _odata_timestamp(query_end),
        "request_urls": request_urls,
        "pages_by_stream": raw_pages,
        "stream_results": stream_results,
        "failed_stream_ids": failed_stream_ids,
        "partial_stream_ids": partial_stream_ids,
        "api_base_url": api_base_url,
        "policy": {
            "primary_signal_preceding_lookback_seconds": int(preceding_lookback.total_seconds()),
            "primary_signal_timeout_chunk_seconds": int(chunk_duration.total_seconds()),
            "max_retries_per_bounded_query": max_retries,
            "max_workers": max_workers,
            "non_primary_history_completeness_claimed": False,
        },
    }


def probe_hamburg_signal_window_coverage(
    client: SensorThingsClient,
    streams: Sequence[SignalStream],
    candidate_windows: Mapping[str, tuple[datetime, datetime]],
    *,
    max_retries: int = 0,
    max_workers: int = 1,
) -> dict[str, Any]:
    """Screen candidate windows before fetching full signal histories.

    Each candidate/stream pair performs one bounded official observation query.
    The result is deliberately a *screen*, not a completeness proof: a present
    event only says that the stream answered inside the interval.  A candidate
    may be passed to :func:`fetch_hamburg_signal_observations` only after this
    screen, which keeps a long-running workflow from spending minutes on a
    window that is visibly missing whole streams.
    """

    if not streams:
        raise ValueError("at least one signal stream is required")
    if not candidate_windows:
        raise ValueError("at least one candidate signal window is required")
    if not 0 <= max_retries <= 5:
        raise ValueError("max_retries must be between 0 and 5")
    if not 1 <= max_workers <= 8:
        raise ValueError("max_workers must be between 1 and 8")

    normalized_windows: list[tuple[str, datetime, datetime]] = []
    for raw_label, raw_bounds in candidate_windows.items():
        label = str(raw_label).strip()
        if not label:
            raise ValueError("candidate window labels must not be empty")
        if len(raw_bounds) != 2:
            raise ValueError(f"candidate window {label!r} must contain begin and end")
        begin_utc, end_utc = raw_bounds
        _require_aware_datetime(begin_utc, f"candidate_windows[{label!r}].begin")
        _require_aware_datetime(end_utc, f"candidate_windows[{label!r}].end")
        begin_utc = begin_utc.astimezone(UTC)
        end_utc = end_utc.astimezone(UTC)
        if end_utc <= begin_utc:
            raise ValueError(f"candidate window {label!r} must be ordered")
        normalized_windows.append((label, begin_utc, end_utc))

    api_base_url = _client_api_base_url(client)
    jobs = [
        (label, begin_utc, end_utc, stream)
        for label, begin_utc, end_utc in normalized_windows
        for stream in streams
    ]

    def probe_one(
        job: tuple[str, datetime, datetime, SignalStream],
    ) -> tuple[str, int, dict[str, Any]]:
        label, begin_utc, end_utc, stream = job
        entity_path = f"Datastreams({stream.stream_id})/Observations"
        attempt = _collection_with_retries(
            client,
            entity_path,
            params={
                "$top": 1,
                "$orderby": "phenomenonTime asc",
                "$filter": _phenomenon_filter(begin_utc, end_utc),
            },
            record_limit=1,
            max_retries=max_retries,
        )
        strategy = "bounded_window"
        values = attempt.values
        if not attempt.ok and _is_timeout_exception(attempt.exception) and isinstance(client, SensorThingsClient):
            # Keep the probe cheap and bounded on servers that reject historical
            # filters.  This is only a positive hint; an empty crop remains
            # inconclusive rather than being promoted as a complete window.
            recent = _recent_signal_values(
                client,
                entity_path,
                begin_utc=begin_utc,
                end_utc=end_utc,
                max_retries=max_retries,
            )
            if recent.ok:
                values = recent.values[:1]
                strategy = "recent_desc_after_timeout"
                attempt = _CollectionAttempt(
                    ok=True,
                    values=values,
                    pages=[*attempt.pages, *recent.pages],
                    request_urls=[*attempt.request_urls, *recent.request_urls],
                    attempts=attempt.attempts + recent.attempts,
                    attempt_errors=[*attempt.attempt_errors, *recent.attempt_errors],
                )
            else:
                attempt = _CollectionAttempt(
                    ok=False,
                    values=[],
                    pages=[*attempt.pages, *recent.pages],
                    request_urls=[*attempt.request_urls, *recent.request_urls],
                    attempts=attempt.attempts + recent.attempts,
                    attempt_errors=[*attempt.attempt_errors, *recent.attempt_errors],
                    exception=recent.exception or attempt.exception,
                )
                strategy = "bounded_window_timeout"
        status = "present" if attempt.ok and values else "empty" if attempt.ok else "error"
        result: dict[str, Any] = {
            "stream_id": stream.stream_id,
            "node_id": stream.node_id,
            "connection_id": stream.connection_id,
            "signal_group": stream.signal_group,
            "status": status,
            "strategy": strategy,
            "observation_count": len(values),
            "attempts": attempt.attempts,
            "retry_errors": attempt.attempt_errors,
            "request_urls": attempt.request_urls,
        }
        if values:
            result["first_phenomenon_time_utc"] = str(values[0].get("phenomenonTime", ""))
        if not attempt.ok:
            result["error"] = {
                "exception_type": type(attempt.exception).__name__ if attempt.exception else "UnknownError",
                "message": str(attempt.exception or "window probe failed"),
            }
        return label, stream.stream_id, result

    probed: dict[tuple[str, int], dict[str, Any]] = {}
    if max_workers == 1 or len(jobs) <= 1:
        for job in jobs:
            label, stream_id, result = probe_one(job)
            probed[(label, stream_id)] = result
    else:
        worker_count = min(max_workers, len(jobs))
        with ThreadPoolExecutor(max_workers=worker_count, thread_name_prefix="hamburg-signal-probe") as executor:
            futures = {executor.submit(probe_one, job): job for job in jobs}
            for future in as_completed(futures):
                label, stream_id, result = future.result()
                probed[(label, stream_id)] = result

    stream_count = len(streams)
    candidates: list[dict[str, Any]] = []
    for label, begin_utc, end_utc in normalized_windows:
        rows = [probed[(label, stream.stream_id)] for stream in streams]
        missing = [row["stream_id"] for row in rows if row["status"] != "present"]
        errors = [row["stream_id"] for row in rows if row["status"] == "error"]
        candidates.append(
            {
                "label": label,
                "begin_utc": _odata_timestamp(begin_utc),
                "end_utc": _odata_timestamp(end_utc),
                "stream_count": stream_count,
                "present_stream_count": stream_count - len(missing),
                "missing_stream_ids": missing,
                "error_stream_ids": errors,
                "screen_status": "complete_candidate" if not missing else "incomplete_candidate",
                "stream_results": rows,
            }
        )

    complete_candidates = [row for row in candidates if row["screen_status"] == "complete_candidate"]
    return {
        "schema": "torii.hamburg-signal-window-screen/v1",
        "api_base_url": api_base_url,
        "stream_count": stream_count,
        "candidate_count": len(candidates),
        "complete_candidate_count": len(complete_candidates),
        "candidates": candidates,
        "screening_only": True,
        "claim_boundary": {
            "proves": ["which streams returned at least one official observation inside each candidate window"],
            "does_not_prove": [
                "complete two-hour history",
                "a preceding t=0 state",
                "historical signal timing for a candidate with missing streams",
            ],
        },
    }


def census_hamburg_signal_stream_coverage(
    client: SensorThingsClient,
    streams: Sequence[SignalStream],
    *,
    max_retries: int = 1,
    max_workers: int = 1,
) -> dict[str, Any]:
    """Fetch bounded first/last observations for each active primary stream.

    This is a cheap coverage hint used before candidate-window screening.  It
    intentionally queries only one ascending and one descending record per
    stream; the result cannot prove continuity, weekend availability, or a
    complete replay window.  Those claims still require the full W2 history
    fetch and its preceding-state audit.
    """

    if not streams:
        raise ValueError("at least one signal stream is required")
    if len({stream.stream_id for stream in streams}) != len(streams):
        raise ValueError("signal stream ids must be unique for a coverage census")
    if not 0 <= max_retries <= 5:
        raise ValueError("max_retries must be between 0 and 5")
    if not 1 <= max_workers <= 8:
        raise ValueError("max_workers must be between 1 and 8")

    api_base_url = _client_api_base_url(client)

    def probe_one(stream: SignalStream) -> tuple[int, dict[str, Any]]:
        entity_path = f"Datastreams({stream.stream_id})/Observations"
        results: dict[str, dict[str, Any]] = {}
        for label, order in (("earliest", "phenomenonTime asc"), ("latest", "phenomenonTime desc")):
            attempt = _collection_with_retries(
                client,
                entity_path,
                params={"$top": 1, "$orderby": order},
                record_limit=1,
                max_retries=max_retries,
            )
            values = attempt.values
            row: dict[str, Any] = {
                "status": "present" if attempt.ok and values else "empty" if attempt.ok else "error",
                "observation_count": len(values),
                "attempts": attempt.attempts,
                "retry_errors": attempt.attempt_errors,
                "request_urls": attempt.request_urls,
            }
            if values:
                row["phenomenon_time_utc"] = str(values[0].get("phenomenonTime", ""))
            if not attempt.ok:
                row["error"] = {
                    "exception_type": type(attempt.exception).__name__ if attempt.exception else "UnknownError",
                    "message": str(attempt.exception or "coverage census query failed"),
                }
            results[label] = row

        earliest = results["earliest"]
        latest = results["latest"]
        if earliest["status"] == "error" or latest["status"] == "error":
            status = "error"
        elif earliest["status"] == "present" and latest["status"] == "present":
            status = "range_available"
        elif earliest["status"] == "empty" and latest["status"] == "empty":
            status = "empty"
        else:
            status = "partial_range"
        row = {
            "stream_id": stream.stream_id,
            "node_id": stream.node_id,
            "connection_id": stream.connection_id,
            "signal_group": stream.signal_group,
            "status": status,
            "earliest": earliest,
            "latest": latest,
        }
        if earliest.get("phenomenon_time_utc"):
            row["range_begin_hint_utc"] = earliest["phenomenon_time_utc"]
        if latest.get("phenomenon_time_utc"):
            row["range_end_hint_utc"] = latest["phenomenon_time_utc"]
        return stream.stream_id, row

    rows_by_id: dict[int, dict[str, Any]] = {}
    ordered_streams = sorted(streams, key=lambda item: (str(item.node_id), item.stream_id))
    if max_workers == 1 or len(ordered_streams) <= 1:
        for stream in ordered_streams:
            stream_id, row = probe_one(stream)
            rows_by_id[stream_id] = row
    else:
        worker_count = min(max_workers, len(ordered_streams))
        with ThreadPoolExecutor(max_workers=worker_count, thread_name_prefix="hamburg-signal-census") as executor:
            futures = {executor.submit(probe_one, stream): stream for stream in ordered_streams}
            for future in as_completed(futures):
                stream_id, row = future.result()
                rows_by_id[stream_id] = row

    rows = [rows_by_id[stream.stream_id] for stream in ordered_streams]
    return {
        "schema": "torii.hamburg-signal-coverage-census/v1",
        "api_base_url": api_base_url,
        "stream_count": len(rows),
        "range_available_count": sum(row["status"] == "range_available" for row in rows),
        "partial_range_stream_ids": [row["stream_id"] for row in rows if row["status"] == "partial_range"],
        "empty_stream_ids": [row["stream_id"] for row in rows if row["status"] == "empty"],
        "error_stream_ids": [row["stream_id"] for row in rows if row["status"] == "error"],
        "streams": rows,
        "screening_only": True,
        "claim_boundary": {
            "proves": [
                "whether each active stream returned a bounded earliest and latest record",
                "the first and latest timestamps returned by those bounded queries",
            ],
            "does_not_prove": [
                "continuous history between the endpoint hints",
                "Saturday or other weekday availability",
                "a complete two-hour replay window or preceding t=0 state",
            ],
        },
    }


def _fetch_hamburg_signal_stream_observations(
    client: SensorThingsClient,
    stream: SignalStream,
    *,
    begin_utc: datetime,
    end_utc: datetime,
    include_preceding_state: bool,
    preceding_lookback: timedelta,
    chunk_duration: timedelta,
    max_retries: int,
    cache_dir: Path | None,
    api_base_url: str,
    retry_incomplete_cache: bool,
) -> dict[str, Any]:
    api_digest = hashlib.sha256(api_base_url.encode("utf-8")).hexdigest()[:12]
    cache_path = (
        Path(cache_dir) / f"signal_stream_{stream.stream_id}_{api_digest}.json"
        if cache_dir is not None
        else None
    )
    if cache_path is not None:
        cached = _load_signal_observation_cache(
            cache_path,
            stream,
            begin_utc=begin_utc,
            end_utc=end_utc,
            include_preceding_state=include_preceding_state,
            api_base_url=api_base_url,
            retry_incomplete_cache=retry_incomplete_cache,
        )
        if cached is not None:
            return cached

    entity_path = f"Datastreams({stream.stream_id})/Observations"
    pages: dict[str, list[dict[str, Any]]] = {"preceding": [], "window": []}
    request_urls: list[str] = []
    errors: list[dict[str, Any]] = []
    raw: dict[str, Any] = {
        "stream_id": stream.stream_id,
        "node_id": stream.node_id,
        "layer_name": stream.layer_name,
        "api_base_url": api_base_url,
        "status": "ok",
        "cache_hit": False,
        "errors": errors,
        "preceding": {
            "requested": bool(include_preceding_state and stream.layer_name == "primary_signal"),
            "status": "not_requested",
        },
        "window": {"strategy": "full", "chunks": []},
    }

    preceding_values: list[dict[str, Any]] = []
    if include_preceding_state and stream.layer_name == "primary_signal":
        lookback_begin = begin_utc - preceding_lookback
        preceding = _collection_with_retries(
            client,
            entity_path,
            params={
                "$top": 1,
                "$orderby": "phenomenonTime desc",
                "$filter": (
                    f"phenomenonTime ge {_odata_timestamp(lookback_begin)} and "
                    f"phenomenonTime lt {_odata_timestamp(begin_utc)}"
                ),
            },
            record_limit=1,
            max_retries=max_retries,
        )
        pages["preceding"].extend(preceding.pages)
        request_urls.extend(preceding.request_urls)
        raw["preceding"] = {
            "requested": True,
            "lookback_begin_utc": _odata_timestamp(lookback_begin),
            "lookback_end_utc": _odata_timestamp(begin_utc),
            "attempts": preceding.attempts,
            "retry_errors": preceding.attempt_errors,
            "status": "ok" if preceding.ok and preceding.values else "empty" if preceding.ok else "error",
        }
        if preceding.ok:
            preceding_values = preceding.values
        elif _is_timeout_exception(preceding.exception) and isinstance(client, SensorThingsClient):
            # Hamburg's service can time out on a bounded historical filter even
            # when the same stream answers a recent descending query quickly.
            # Fetch a finite recent page and crop it locally.  This remains an
            # official observation query and is only a timeout fallback; failed
            # coverage is still represented as empty/blocked downstream.
            recent = _recent_signal_values(
                client,
                entity_path,
                begin_utc=lookback_begin,
                end_utc=begin_utc,
                max_retries=max_retries,
            )
            pages["preceding"].extend(recent.pages)
            request_urls.extend(recent.request_urls)
            raw["preceding"].update(
                {
                    "strategy": "recent_desc_after_timeout",
                    "fallback_attempts": recent.attempts,
                    "fallback_retry_errors": recent.attempt_errors,
                    "fallback_status": "ok" if recent.ok else "error",
                }
            )
            if recent.ok:
                preceding_values = recent.values
                raw["preceding"]["status"] = "ok" if preceding_values else "empty"
            else:
                errors.append(
                    _collection_error(
                        "preceding_state",
                        preceding,
                        begin_utc=lookback_begin,
                        end_utc=begin_utc,
                    )
                )
                errors.append(
                    _collection_error(
                        "preceding_state_recent_fallback",
                        recent,
                        begin_utc=lookback_begin,
                        end_utc=begin_utc,
                    )
                )
        else:
            errors.append(
                _collection_error(
                    "preceding_state",
                    preceding,
                    begin_utc=lookback_begin,
                    end_utc=begin_utc,
                )
            )

    window_filter = _phenomenon_filter(begin_utc, end_utc)
    window_values: list[dict[str, Any]] = []
    if stream.layer_name == "primary_signal":
        # A single broad ordered request is cheapest when the server can answer it.
        # On timeout only, switch to bounded chunks that can be retried independently.
        full_window = _collection_with_retries(
            client,
            entity_path,
            params={"$top": 1000, "$orderby": "phenomenonTime asc", "$filter": window_filter},
            max_retries=0,
        )
        pages["window"].extend(full_window.pages)
        request_urls.extend(full_window.request_urls)
        raw["window"].update(
            {
                "attempts": full_window.attempts,
                "retry_errors": full_window.attempt_errors,
            }
        )
        if full_window.ok:
            window_values = full_window.values
            raw["window"]["status"] = "ok"
        elif _is_timeout_exception(full_window.exception) and isinstance(client, SensorThingsClient):
            recent = _recent_signal_values(
                client,
                entity_path,
                begin_utc=begin_utc,
                end_utc=end_utc,
                max_retries=max_retries,
            )
            pages["window"].extend(recent.pages)
            request_urls.extend(recent.request_urls)
            if recent.ok:
                window_values = recent.values
                raw["window"].update(
                    {
                        "strategy": "recent_desc_after_timeout",
                        "fallback_attempts": recent.attempts,
                        "fallback_retry_errors": recent.attempt_errors,
                        "fallback_status": "ok",
                        "status": "ok",
                    }
                )
            else:
                raw["window"]["strategy"] = "chunked_after_timeout"
                raw["window"]["fallback_trigger"] = _collection_error(
                    "full_window_timeout",
                    full_window,
                    begin_utc=begin_utc,
                    end_utc=end_utc,
                )
                raw["window"]["recent_fallback"] = _collection_error(
                    "recent_window_fallback",
                    recent,
                    begin_utc=begin_utc,
                    end_utc=end_utc,
                )
                _collect_signal_window_chunks(
                    client,
                    entity_path,
                    begin_utc=begin_utc,
                    end_utc=end_utc,
                    chunk_duration=chunk_duration,
                    max_retries=max_retries,
                    pages=pages,
                    request_urls=request_urls,
                    errors=errors,
                    raw_window=raw["window"],
                    window_values=window_values,
                )
        elif _is_timeout_exception(full_window.exception):
            raw["window"]["strategy"] = "chunked_after_timeout"
            raw["window"]["fallback_trigger"] = _collection_error(
                "full_window_timeout",
                full_window,
                begin_utc=begin_utc,
                end_utc=end_utc,
            )
            _collect_signal_window_chunks(
                client,
                entity_path,
                begin_utc=begin_utc,
                end_utc=end_utc,
                chunk_duration=chunk_duration,
                max_retries=max_retries,
                pages=pages,
                request_urls=request_urls,
                errors=errors,
                raw_window=raw["window"],
                window_values=window_values,
            )
            raw["window"]["status"] = (
                "partial" if any(chunk.get("status") == "error" for chunk in raw["window"].get("chunks", [])) else "ok"
            )
        else:
            errors.append(
                _collection_error(
                    "full_window",
                    full_window,
                    begin_utc=begin_utc,
                    end_utc=end_utc,
                )
            )
            raw["window"]["status"] = "error"
    else:
        # Non-primary layers remain best-effort inputs. A successful request is not
        # presented as proof that the historical signal program/cycle is complete.
        full_window = _collection_with_retries(
            client,
            entity_path,
            params={"$top": 1000, "$orderby": "phenomenonTime asc", "$filter": window_filter},
            max_retries=max_retries,
        )
        pages["window"].extend(full_window.pages)
        request_urls.extend(full_window.request_urls)
        raw["window"].update(
            {
                "attempts": full_window.attempts,
                "retry_errors": full_window.attempt_errors,
                "status": "ok" if full_window.ok else "error",
                "historical_completeness_claimed": False,
            }
        )
        if full_window.ok:
            window_values = full_window.values
        else:
            errors.append(
                _collection_error(
                    "full_window",
                    full_window,
                    begin_utc=begin_utc,
                    end_utc=end_utc,
                )
            )

    preceding_parsed = _parse_signal_observation_values(
        stream.stream_id,
        preceding_values,
        errors=errors,
        stage="parse_preceding",
    )
    window_parsed = _parse_signal_observation_values(
        stream.stream_id,
        window_values,
        errors=errors,
        stage="parse_window",
    )
    parsed = _deduplicate_signal_observations([*preceding_parsed, *window_parsed])
    raw["preceding_observation_count"] = len(preceding_parsed)
    raw["window_observation_count"] = len(window_parsed)
    raw["observation_count"] = len(parsed)
    raw["status"] = _signal_stream_status(
        errors,
        window_succeeded=raw["window"].get("status") in {"ok", "partial"},
    )

    result = {
        "observations": parsed,
        "pages": pages,
        "request_urls": request_urls,
        "raw": raw,
    }
    if cache_path is not None:
        try:
            _write_signal_observation_cache(
                cache_path,
                stream,
                result,
                begin_utc=begin_utc,
                end_utc=end_utc,
                include_preceding_state=include_preceding_state,
                api_base_url=api_base_url,
            )
            raw["cache_path"] = str(cache_path)
        except Exception as exc:
            errors.append(_exception_error("cache_write", exc))
            raw["status"] = _signal_stream_status(
                errors,
                window_succeeded=raw["window"].get("status") in {"ok", "partial"},
            )
    return result


def _recent_signal_values(
    client: SensorThingsClient,
    entity_path: str,
    *,
    begin_utc: datetime,
    end_utc: datetime,
    max_retries: int,
) -> _CollectionAttempt:
    """Fetch a finite recent descending page and crop it to a target interval."""

    recent = _collection_with_retries(
        client,
        entity_path,
        params={"$top": 5000, "$orderby": "phenomenonTime desc"},
        record_limit=5000,
        max_retries=max_retries,
    )
    if not recent.ok:
        return recent
    selected: list[dict[str, Any]] = []
    for value in recent.values:
        try:
            timestamp = parse_iso_datetime(str(value.get("phenomenonTime", "")).split("/", maxsplit=1)[0])
        except Exception:
            continue
        if begin_utc <= timestamp < end_utc:
            selected.append(value)
    return _CollectionAttempt(
        ok=True,
        values=selected,
        pages=recent.pages,
        request_urls=recent.request_urls,
        attempts=recent.attempts,
        attempt_errors=recent.attempt_errors,
    )


def _collect_signal_window_chunks(
    client: SensorThingsClient,
    entity_path: str,
    *,
    begin_utc: datetime,
    end_utc: datetime,
    chunk_duration: timedelta,
    max_retries: int,
    pages: dict[str, list[dict[str, Any]]],
    request_urls: list[str],
    errors: list[dict[str, Any]],
    raw_window: dict[str, Any],
    window_values: list[dict[str, Any]],
) -> None:
    for chunk_begin, chunk_end in _time_chunks(begin_utc, end_utc, chunk_duration):
        chunk = _collection_with_retries(
            client,
            entity_path,
            params={
                "$top": 1000,
                "$orderby": "phenomenonTime asc",
                "$filter": _phenomenon_filter(chunk_begin, chunk_end),
            },
            max_retries=max_retries,
        )
        pages["window"].extend(chunk.pages)
        request_urls.extend(chunk.request_urls)
        chunk_raw = {
            "begin_utc": _odata_timestamp(chunk_begin),
            "end_utc": _odata_timestamp(chunk_end),
            "attempts": chunk.attempts,
            "retry_errors": chunk.attempt_errors,
            "status": "ok" if chunk.ok else "error",
        }
        raw_window.setdefault("chunks", []).append(chunk_raw)
        if chunk.ok:
            window_values.extend(chunk.values)
        else:
            errors.append(
                _collection_error(
                    "window_chunk",
                    chunk,
                    begin_utc=chunk_begin,
                    end_utc=chunk_end,
                )
            )


def _collection_with_retries(
    client: SensorThingsClient,
    entity_path: str,
    *,
    params: Mapping[str, str | int],
    max_retries: int,
    record_limit: int | None = None,
) -> _CollectionAttempt:
    attempt_errors: list[dict[str, Any]] = []
    last_exception: Exception | None = None
    for attempt in range(1, max_retries + 2):
        try:
            values, pages, urls = client.collection(
                entity_path,
                params=params,
                record_limit=record_limit,
            )
            return _CollectionAttempt(
                ok=True,
                values=values,
                pages=pages,
                request_urls=urls,
                attempts=attempt,
                attempt_errors=attempt_errors,
            )
        except Exception as exc:
            last_exception = exc
            attempt_errors.append(
                {
                    "attempt": attempt,
                    "exception_type": type(exc).__name__,
                    "message": str(exc),
                }
            )
    return _CollectionAttempt(
        ok=False,
        values=[],
        pages=[],
        request_urls=[],
        attempts=max_retries + 1,
        attempt_errors=attempt_errors,
        exception=last_exception,
    )


def _parse_signal_observation_values(
    stream_id: int,
    values: Iterable[Mapping[str, Any]],
    *,
    errors: list[dict[str, Any]],
    stage: str,
) -> list[SignalObservation]:
    parsed: list[SignalObservation] = []
    for value in values:
        try:
            phenomenon = str(value.get("phenomenonTime", ""))
            timestamp = parse_iso_datetime(phenomenon.split("/", maxsplit=1)[0])
            parsed.append(
                SignalObservation(
                    stream_id=stream_id,
                    observation_id=_optional_int(value.get("@iot.id")),
                    phenomenon_time_utc=timestamp,
                    result=str(value.get("result", "")),
                    result_time=str(value.get("resultTime", "")),
                )
            )
        except Exception as exc:
            error = _exception_error(stage, exc)
            error["observation_id"] = value.get("@iot.id")
            errors.append(error)
    return parsed


def _deduplicate_signal_observations(
    values: Iterable[SignalObservation],
) -> list[SignalObservation]:
    unique: dict[tuple[Any, ...], SignalObservation] = {}
    for value in values:
        key = (
            ("id", value.observation_id)
            if value.observation_id is not None
            else (
                "value",
                value.phenomenon_time_utc,
                value.result,
                value.result_time,
            )
        )
        unique[key] = value
    return sorted(
        unique.values(),
        key=lambda item: (item.phenomenon_time_utc, item.observation_id or -1),
    )


def _collection_error(
    stage: str,
    attempt: _CollectionAttempt,
    *,
    begin_utc: datetime | None = None,
    end_utc: datetime | None = None,
) -> dict[str, Any]:
    error = _exception_error(stage, attempt.exception or RuntimeError("collection request failed"))
    error["attempts"] = attempt.attempts
    error["attempt_errors"] = attempt.attempt_errors
    if begin_utc is not None:
        error["begin_utc"] = _odata_timestamp(begin_utc)
    if end_utc is not None:
        error["end_utc"] = _odata_timestamp(end_utc)
    return error


def _exception_error(stage: str, exc: Exception) -> dict[str, Any]:
    return {
        "stage": stage,
        "exception_type": type(exc).__name__,
        "message": str(exc),
    }


def _failed_signal_stream_result(
    stream: SignalStream,
    exc: Exception,
    *,
    stage: str,
) -> dict[str, Any]:
    error = _exception_error(stage, exc)
    return {
        "observations": [],
        "pages": {"preceding": [], "window": []},
        "request_urls": [],
        "raw": {
            "stream_id": stream.stream_id,
            "node_id": stream.node_id,
            "layer_name": stream.layer_name,
            "status": "error",
            "cache_hit": False,
            "errors": [error],
        },
    }


def _signal_stream_status(
    errors: Sequence[Mapping[str, Any]],
    *,
    window_succeeded: bool,
) -> str:
    if not errors:
        return "ok"
    return "partial" if window_succeeded else "error"


def _phenomenon_filter(begin_utc: datetime, end_utc: datetime) -> str:
    return (
        f"phenomenonTime ge {_odata_timestamp(begin_utc)} "
        f"and phenomenonTime lt {_odata_timestamp(end_utc)}"
    )


def _time_chunks(
    begin_utc: datetime,
    end_utc: datetime,
    duration: timedelta,
) -> Iterable[tuple[datetime, datetime]]:
    cursor = begin_utc
    while cursor < end_utc:
        chunk_end = min(cursor + duration, end_utc)
        yield cursor, chunk_end
        cursor = chunk_end


def _is_timeout_exception(exc: Exception | None) -> bool:
    if exc is None:
        return False
    if isinstance(exc, TimeoutError):
        return True
    if getattr(exc, "code", None) in {408, 504}:
        return True
    text = f"{type(exc).__name__}: {exc}".lower()
    return "timeout" in text or "timed out" in text or "time-out" in text


def _load_signal_observation_cache(
    path: Path,
    stream: SignalStream,
    *,
    begin_utc: datetime,
    end_utc: datetime,
    include_preceding_state: bool,
    api_base_url: str,
    retry_incomplete_cache: bool,
) -> dict[str, Any] | None:
    try:
        if not path.is_file() or path.stat().st_size <= 0:
            return None
        payload = json.loads(path.read_text(encoding="utf-8"))
        if (
            payload.get("schema_version") != 2
            or int(payload.get("stream_id")) != stream.stream_id
            or payload.get("query_begin_utc") != _odata_timestamp(begin_utc)
            or payload.get("query_end_utc") != _odata_timestamp(end_utc)
            or bool(payload.get("include_preceding_state")) != include_preceding_state
            or payload.get("api_base_url") != api_base_url
            or not isinstance(payload.get("captured_at_utc"), str)
        ):
            return None
        observation_payload = payload.get("observations")
        pages = payload.get("pages")
        request_urls = payload.get("request_urls")
        raw = payload.get("raw")
        if (
            not isinstance(observation_payload, list)
            or not isinstance(pages, dict)
            or not isinstance(pages.get("preceding"), list)
            or not isinstance(pages.get("window"), list)
            or not isinstance(request_urls, list)
            or not isinstance(raw, dict)
        ):
            return None
        # A retryable workflow must not treat a previously captured timeout,
        # partial response, or missing t=0 state as authoritative.  The
        # default remains backward-compatible and reuses structured failures;
        # callers that explicitly request a retry get a real upstream attempt.
        if retry_incomplete_cache:
            if raw.get("status") != "ok":
                return None
            if include_preceding_state and stream.layer_name == "primary_signal":
                try:
                    preceding_count = int(raw.get("preceding_observation_count", 0))
                except (TypeError, ValueError):
                    preceding_count = 0
                if preceding_count <= 0:
                    return None
        observations = [
            SignalObservation(
                stream_id=stream.stream_id,
                observation_id=_optional_int(item.get("observation_id")),
                phenomenon_time_utc=parse_iso_datetime(str(item.get("phenomenon_time_utc", ""))),
                result=str(item.get("result", "")),
                result_time=str(item.get("result_time", "")),
            )
            for item in observation_payload
            if isinstance(item, Mapping)
        ]
        raw["cache_hit"] = True
        raw["cache_path"] = str(path)
        raw["cache_captured_at_utc"] = payload["captured_at_utc"]
        return {
            "observations": observations,
            "pages": pages,
            "request_urls": [str(url) for url in request_urls],
            "raw": raw,
        }
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return None


def _write_signal_observation_cache(
    path: Path,
    stream: SignalStream,
    result: Mapping[str, Any],
    *,
    begin_utc: datetime,
    end_utc: datetime,
    include_preceding_state: bool,
    api_base_url: str,
) -> None:
    observations = result.get("observations") if isinstance(result.get("observations"), list) else []
    payload = {
        "schema_version": 2,
        "stream_id": stream.stream_id,
        "layer_name": stream.layer_name,
        "api_base_url": api_base_url,
        "captured_at_utc": _odata_timestamp(datetime.now(UTC)),
        "query_begin_utc": _odata_timestamp(begin_utc),
        "query_end_utc": _odata_timestamp(end_utc),
        "include_preceding_state": include_preceding_state,
        "observations": [
            {
                "observation_id": observation.observation_id,
                "phenomenon_time_utc": _odata_timestamp(observation.phenomenon_time_utc),
                "result": observation.result,
                "result_time": observation.result_time,
            }
            for observation in observations
            if isinstance(observation, SignalObservation)
        ],
        "pages": result.get("pages", {"preceding": [], "window": []}),
        "request_urls": result.get("request_urls", []),
        "raw": result.get("raw", {}),
    }
    _atomic_write_json(path, payload)


def resolve_hamburg_signal_asset_directory_indexes(
    node_ids: Sequence[str],
    *,
    map_index_html: bytes,
    ocit_c_index_html: bytes,
    map_asset_base: str,
    ocit_c_asset_base: str,
) -> tuple[tuple[HamburgSignalAsset, ...], dict[str, Any]]:
    """Resolve MAP/KML/OCIT-C triplets from catalog-published directory indexes.

    Filenames are treated as evidence, not guessed.  A node resolves only when
    there is exactly one complete MAP XML/KML pair whose pre-revision stem has
    exactly one matching OCIT-C XML file.  Missing or ambiguous nodes remain an
    explicit autonomous-abstention scope.
    """

    _validate_hamburg_signal_asset_directory_base(
        map_asset_base,
        expected_path="/tlf_public/",
    )
    _validate_hamburg_signal_asset_directory_base(
        ocit_c_asset_base,
        expected_path="/tlf_public/OCIT-C/",
    )
    requested = _normalized_requested_signal_nodes(node_ids)
    map_filenames = _directory_index_filenames(map_index_html, label="MAP")
    ocit_c_filenames = _directory_index_filenames(ocit_c_index_html, label="OCIT-C")

    map_variants: dict[
        str,
        dict[tuple[str, str], dict[str, set[str]]],
    ] = {}
    for filename in map_filenames:
        matched = _HAMBURG_MAP_ASSET_PATTERN.fullmatch(filename)
        if matched is None:
            continue
        normalized_node = str(int(matched.group("node")))
        key = (
            matched.group("ocit_stem").casefold(),
            (matched.group("revision") or "").casefold(),
        )
        extensions = map_variants.setdefault(normalized_node, {}).setdefault(
            key,
            {"xml": set(), "kml": set()},
        )
        extensions[matched.group("extension").casefold()].add(filename)

    ocit_by_node: dict[str, dict[str, set[str]]] = {}
    for filename in ocit_c_filenames:
        matched = _HAMBURG_OCIT_C_ASSET_PATTERN.fullmatch(filename)
        if matched is None:
            continue
        normalized_node = str(int(matched.group("node")))
        ocit_by_node.setdefault(normalized_node, {}).setdefault(
            matched.group("ocit_stem").casefold(),
            set(),
        ).add(filename)

    assets: list[HamburgSignalAsset] = []
    resolutions: list[dict[str, Any]] = []
    for requested_node, normalized_node in requested:
        node_map_variants = map_variants.get(normalized_node, {})
        node_ocit_files = ocit_by_node.get(normalized_node, {})
        complete_triplets: list[dict[str, str]] = []
        for (stem, revision), extensions in sorted(node_map_variants.items()):
            map_xml_files = sorted(extensions["xml"])
            map_kml_files = sorted(extensions["kml"])
            ocit_files = sorted(node_ocit_files.get(stem, set()))
            if len(map_xml_files) != 1 or len(map_kml_files) != 1 or len(ocit_files) != 1:
                continue
            complete_triplets.append(
                {
                    "map_xml": map_xml_files[0],
                    "map_kml": map_kml_files[0],
                    "ocit_xml": ocit_files[0],
                    "revision": revision,
                }
            )

        resolution: dict[str, Any] = {
            "node_id": requested_node,
            "normalized_node_id": normalized_node,
            "directory_matches": {
                "map": sorted(
                    filename
                    for extensions in node_map_variants.values()
                    for filenames in extensions.values()
                    for filename in filenames
                ),
                "ocit_c": sorted(
                    filename
                    for filenames in node_ocit_files.values()
                    for filename in filenames
                ),
            },
            "complete_triplet_count": len(complete_triplets),
        }
        if len(complete_triplets) == 1:
            triplet = complete_triplets[0]
            asset = HamburgSignalAsset(
                node_id=requested_node,
                map_xml=triplet["map_xml"],
                map_kml=triplet["map_kml"],
                ocit_xml=f"OCIT-C/{triplet['ocit_xml']}",
            )
            assets.append(asset)
            resolution.update(
                {
                    "decision": "pass",
                    "reason_codes": ["official_directory_asset_triplet_resolved"],
                    "asset": {
                        "map_xml": asset.map_xml,
                        "map_kml": asset.map_kml,
                        "ocit_xml": asset.ocit_xml,
                        "urls": {
                            "map_xml": urljoin(map_asset_base, asset.map_xml),
                            "map_kml": urljoin(map_asset_base, asset.map_kml),
                            "ocit_xml": urljoin(
                                ocit_c_asset_base,
                                Path(asset.ocit_xml).name,
                            ),
                        },
                    },
                }
            )
        else:
            if len(complete_triplets) > 1:
                reason_codes = ["official_directory_asset_triplet_ambiguous"]
            else:
                reason_codes = []
                if not node_map_variants:
                    reason_codes.append("official_directory_has_no_map_assets")
                if not node_ocit_files:
                    reason_codes.append("official_directory_has_no_ocit_c_asset")
                if not reason_codes:
                    reason_codes.append(
                        "official_directory_has_no_complete_asset_triplet"
                    )
            resolution.update(
                {
                    "decision": "review_required",
                    "reason_codes": reason_codes,
                    "autonomous_action": "abstain_no_signal_materialization",
                    "complete_triplets": complete_triplets,
                }
            )
        resolutions.append(resolution)

    resolved_node_ids = [asset.node_id for asset in assets]
    unresolved_node_ids = [
        str(item["node_id"])
        for item in resolutions
        if item["decision"] != "pass"
    ]
    report: dict[str, Any] = {
        "schema": "torii.hamburg-signal-asset-discovery/v1",
        "decision": "pass" if not unresolved_node_ids else "review_required",
        "source": "official_hamburg_catalog_directory_resources",
        "insufficient_evidence_action": "autonomous_abstention_no_materialization",
        "requested_node_ids": [node_id for node_id, _normalized in requested],
        "resolved_node_ids": resolved_node_ids,
        "unresolved_node_ids": unresolved_node_ids,
        "directory_snapshots": {
            "map": {
                "url": map_asset_base,
                "sha256": hashlib.sha256(map_index_html).hexdigest(),
                "bytes": len(map_index_html),
                "href_count": len(map_filenames),
            },
            "ocit_c": {
                "url": ocit_c_asset_base,
                "sha256": hashlib.sha256(ocit_c_index_html).hexdigest(),
                "bytes": len(ocit_c_index_html),
                "href_count": len(ocit_c_filenames),
            },
        },
        "resolutions": resolutions,
    }
    return tuple(assets), report


def audit_hamburg_signal_asset_directory_history(
    node_ids: Sequence[str],
    *,
    snapshots: Sequence[Mapping[str, Any]],
    current_snapshot_label: str,
) -> dict[str, Any]:
    """Audit current and archived official MAP/OCIT directory snapshots.

    Every snapshot is passed through :func:`resolve_hamburg_signal_asset_directory_indexes`;
    this helper only compares the resulting evidence across catalog versions.  A
    historical match is reported as evidence, but it never substitutes for a
    current published triplet.  This keeps the signal-materialization gate
    fail-closed while making the archive check deterministic and reusable.
    """

    if not snapshots:
        raise ValueError("at least one signal-asset directory snapshot is required")
    current_label = str(current_snapshot_label).strip()
    if not current_label:
        raise ValueError("current_snapshot_label must not be empty")

    snapshot_reports: list[dict[str, Any]] = []
    seen_labels: set[str] = set()
    current_report: dict[str, Any] | None = None
    resolved_by_node: dict[str, list[str]] = {}
    requested_pairs = _normalized_requested_signal_nodes(node_ids)

    for raw_snapshot in snapshots:
        if not isinstance(raw_snapshot, Mapping):
            raise ValueError("signal-asset directory snapshots must be mappings")
        label = str(raw_snapshot.get("label", "")).strip()
        if not label:
            raise ValueError("signal-asset directory snapshot has no label")
        if label in seen_labels:
            raise ValueError(f"duplicate signal-asset directory snapshot label: {label!r}")
        seen_labels.add(label)
        map_index_html = raw_snapshot.get("map_index_html")
        ocit_c_index_html = raw_snapshot.get("ocit_c_index_html")
        if not isinstance(map_index_html, bytes) or not isinstance(ocit_c_index_html, bytes):
            raise ValueError(
                f"signal-asset directory snapshot {label!r} must provide byte HTML indexes"
            )
        map_asset_base = str(
            raw_snapshot.get("map_asset_base", "https://daten-hamburg.de/tlf_public/")
        )
        ocit_c_asset_base = str(
            raw_snapshot.get(
                "ocit_c_asset_base",
                "https://daten-hamburg.de/tlf_public/OCIT-C/",
            )
        )
        _assets, report = resolve_hamburg_signal_asset_directory_indexes(
            node_ids,
            map_index_html=map_index_html,
            ocit_c_index_html=ocit_c_index_html,
            map_asset_base=map_asset_base,
            ocit_c_asset_base=ocit_c_asset_base,
        )
        resolved = [str(node_id) for node_id in report["resolved_node_ids"]]
        for resolution in report["resolutions"]:
            if resolution.get("decision") != "pass":
                continue
            normalized = str(resolution["normalized_node_id"])
            resolved_by_node.setdefault(normalized, []).append(label)
        snapshot_report = {
            "label": label,
            "kind": "current" if label == current_label else "historical",
            "index_urls": {
                "map": str(raw_snapshot.get("map_index_url", "")),
                "ocit_c": str(raw_snapshot.get("ocit_c_index_url", "")),
            },
            "resolved_node_ids": resolved,
            "unresolved_node_ids": [
                str(node_id) for node_id in report["unresolved_node_ids"]
            ],
            "directory_snapshots": report["directory_snapshots"],
            "resolutions": report["resolutions"],
        }
        snapshot_reports.append(snapshot_report)
        if label == current_label:
            current_report = snapshot_report

    if current_report is None:
        raise ValueError(
            f"current signal-asset directory snapshot {current_label!r} was not supplied"
        )

    current_resolved = {
        str(resolution["normalized_node_id"])
        for resolution in current_report["resolutions"]
        if resolution.get("decision") == "pass"
    }
    node_reports: list[dict[str, Any]] = []
    for requested_node, normalized_node in requested_pairs:
        evidence = resolved_by_node.get(normalized_node, [])
        if normalized_node in current_resolved:
            status = "current_published"
            action = "eligible_for_current_asset_materialization"
        elif evidence:
            status = "historical_only"
            action = "abstain_do_not_substitute_historical_asset"
        else:
            status = "not_found_in_checked_directories"
            action = "abstain_no_signal_materialization"
        node_reports.append(
            {
                "node_id": requested_node,
                "normalized_node_id": normalized_node,
                "status": status,
                "resolved_in_snapshots": evidence,
                "autonomous_action": action,
            }
        )

    unresolved_current = [
        item["node_id"]
        for item in node_reports
        if item["status"] != "current_published"
    ]
    resolved_current = [
        item["node_id"]
        for item in node_reports
        if item["status"] == "current_published"
    ]
    publication_gap = {
        "decision": (
            "pass"
            if not unresolved_current
            else "current_official_directory_missing_required_signal_assets"
        ),
        "current_snapshot_label": current_label,
        "unresolved_current_node_ids": unresolved_current,
        "historical_matches_do_not_substitute_current_assets": True,
    }
    return {
        "schema": "torii.hamburg-signal-asset-history-audit/v1",
        "decision": "pass" if not unresolved_current else "review_required",
        "source": "official_hamburg_catalog_current_and_archived_directory_indexes",
        "requested_node_ids": [node_id for node_id, _normalized in requested_pairs],
        "resolved_node_ids": resolved_current,
        "unresolved_node_ids": unresolved_current,
        "resolved_current_node_ids": resolved_current,
        "unresolved_current_node_ids": unresolved_current,
        "current_snapshot_label": current_label,
        "snapshots": snapshot_reports,
        "nodes": node_reports,
        "publication_gap": publication_gap,
        "insufficient_evidence_action": "autonomous_abstention_no_materialization",
    }


def discover_hamburg_signal_assets(
    catalog: HamburgTrafficLightCatalog,
    node_ids: Sequence[str],
    *,
    output_dir: Path | None = None,
    timeout_seconds: float = 30.0,
    transport: Transport | None = None,
) -> tuple[tuple[HamburgSignalAsset, ...], dict[str, Any]]:
    """Fetch current official directory indexes and resolve exact node assets."""

    transport = transport or _urlopen_bytes
    snapshots: dict[str, bytes] = {}
    for key, url in (
        ("map", catalog.map_asset_base),
        ("ocit_c", catalog.ocit_c_asset_base),
    ):
        request = Request(
            url,
            headers={"Accept": "text/html", "User-Agent": "Torii-SUMO/1.1"},
        )
        try:
            snapshots[key] = transport(request, timeout_seconds)
        except Exception as exc:
            raise HamburgSignalAssetDirectoryUnavailableError(
                f"official Hamburg {key} signal-asset directory is unavailable: "
                f"{type(exc).__name__}: {exc}"
            ) from exc

    assets, report = resolve_hamburg_signal_asset_directory_indexes(
        node_ids,
        map_index_html=snapshots["map"],
        ocit_c_index_html=snapshots["ocit_c"],
        map_asset_base=catalog.map_asset_base,
        ocit_c_asset_base=catalog.ocit_c_asset_base,
    )
    report["fetched_at_utc"] = _odata_timestamp(datetime.now(UTC))
    report["catalog"] = {
        "dataset_id": catalog.dataset_id,
        "package_id": catalog.package_id,
        "metadata_modified": catalog.metadata_modified,
        "catalog_api_url": catalog.catalog_api_url,
        "raw_sha256": catalog.raw_sha256,
        "map_resource_id": catalog.map_files.resource_id,
        "ocit_c_resource_id": catalog.ocit_c_files.resource_id,
    }
    if output_dir is not None:
        output_dir.mkdir(parents=True, exist_ok=True)
        for key, content in snapshots.items():
            digest = hashlib.sha256(content).hexdigest()
            path = output_dir / f"hamburg-signal-{key}-directory.{digest[:12]}.html"
            if path.is_file() and sha256_file(path) != digest:
                raise ValueError(
                    "Hamburg signal directory snapshot cache conflicts with its "
                    f"content-addressed filename: {path}"
                )
            if not path.is_file():
                _atomic_write_bytes(path, content)
            report["directory_snapshots"][key]["path"] = str(path)
        canonical = json.dumps(
            report,
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        report_path = output_dir / (
            "hamburg-signal-asset-discovery."
            f"{hashlib.sha256(canonical).hexdigest()[:12]}.json"
        )
        _atomic_write_json(report_path, report)
        report["discovery_snapshot"] = {
            "path": str(report_path),
            "sha256": sha256_file(report_path),
            "bytes": report_path.stat().st_size,
        }
    return assets, report


def download_resolved_hamburg_signal_assets(
    client: SensorThingsClient,
    signal_assets: Sequence[HamburgSignalAsset],
    output_dir: Path,
    *,
    map_asset_base: str,
    ocit_c_asset_base: str,
) -> tuple[dict[str, dict[str, str]], list[dict[str, Any]]]:
    """Download resolver-proven assets with URL-keyed immutable cache names."""

    _validate_hamburg_signal_asset_directory_base(
        map_asset_base,
        expected_path="/tlf_public/",
    )
    _validate_hamburg_signal_asset_directory_base(
        ocit_c_asset_base,
        expected_path="/tlf_public/OCIT-C/",
    )
    seen_nodes: set[str] = set()
    for asset in signal_assets:
        normalized_node = _normalized_hamburg_signal_node_id(asset.node_id)
        if normalized_node in seen_nodes:
            raise ValueError(
                f"duplicate resolved Hamburg signal node id: {asset.node_id!r}"
            )
        seen_nodes.add(normalized_node)
        _validate_resolved_hamburg_signal_asset(asset)
    return _download_hamburg_signal_asset_set(
        client,
        signal_assets,
        output_dir,
        map_asset_base=map_asset_base,
        ocit_c_asset_base=ocit_c_asset_base,
        cache_by_url=True,
    )


def download_hamburg_signal_assets(
    client: SensorThingsClient,
    preset: HamburgCorridorPreset,
    output_dir: Path,
    *,
    map_asset_base: str | None = None,
    ocit_c_asset_base: str | None = None,
) -> tuple[dict[str, dict[str, str]], list[dict[str, Any]]]:
    map_asset_base = map_asset_base or preset.signal_asset_base
    ocit_c_asset_base = ocit_c_asset_base or urljoin(preset.signal_asset_base, "OCIT-C/")
    return _download_hamburg_signal_asset_set(
        client,
        preset.signal_assets,
        output_dir,
        map_asset_base=map_asset_base,
        ocit_c_asset_base=ocit_c_asset_base,
        cache_by_url=False,
    )


def _download_hamburg_signal_asset_set(
    client: SensorThingsClient,
    signal_assets: Sequence[HamburgSignalAsset],
    output_dir: Path,
    *,
    map_asset_base: str,
    ocit_c_asset_base: str,
    cache_by_url: bool,
) -> tuple[dict[str, dict[str, str]], list[dict[str, Any]]]:
    output_dir.mkdir(parents=True, exist_ok=True)
    paths: dict[str, dict[str, str]] = {}
    manifest: list[dict[str, Any]] = []
    for asset in signal_assets:
        node_paths: dict[str, str] = {}
        for kind, relative in (
            ("map_xml", asset.map_xml),
            ("map_kml", asset.map_kml),
            ("ocit_xml", asset.ocit_xml),
        ):
            url = (
                urljoin(ocit_c_asset_base, Path(relative).name)
                if kind == "ocit_xml"
                else urljoin(map_asset_base, relative)
            )
            url_cache_key = hashlib.sha256(url.encode("utf-8")).hexdigest()[:12]
            cache_suffix = f"_{url_cache_key}" if cache_by_url else ""
            filename = f"{asset.node_id}_{kind}{cache_suffix}{Path(relative).suffix}"
            path = output_dir / filename
            cache_hit = path.is_file() and path.stat().st_size > 0
            if not cache_hit:
                content = client.bytes(url)
                if not content:
                    raise ValueError(f"Hamburg signal asset download returned no bytes: {url}")
                _atomic_write_bytes(path, content)
            digest = sha256_file(path)
            node_paths[kind] = str(path)
            manifest.append(
                {
                    "node_id": asset.node_id,
                    "kind": kind,
                    "source_filename": Path(relative).name,
                    "url": url,
                    "url_cache_key": url_cache_key if cache_by_url else None,
                    "path": str(path),
                    "sha256": digest,
                    "bytes": path.stat().st_size,
                    "cache_hit": cache_hit,
                }
            )
        paths[asset.node_id] = node_paths
    return paths, manifest


def _normalized_requested_signal_nodes(
    node_ids: Sequence[str],
) -> list[tuple[str, str]]:
    if not node_ids:
        raise ValueError("at least one Hamburg signal node id is required")
    requested: list[tuple[str, str]] = []
    seen: set[str] = set()
    for node_id in node_ids:
        requested_node = str(node_id).strip()
        normalized_node = _normalized_hamburg_signal_node_id(requested_node)
        if normalized_node in seen:
            raise ValueError(
                "Hamburg signal node ids must be unique after zero-padding normalization"
            )
        seen.add(normalized_node)
        requested.append((requested_node, normalized_node))
    return requested


def _normalized_hamburg_signal_node_id(node_id: str) -> str:
    if re.fullmatch(r"[0-9]{1,6}", node_id) is None:
        raise ValueError(f"invalid Hamburg signal node id: {node_id!r}")
    return str(int(node_id))


def _directory_index_filenames(index_html: bytes, *, label: str) -> tuple[str, ...]:
    if not index_html:
        raise ValueError(f"official Hamburg {label} directory index is empty")
    try:
        text = index_html.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError(
            f"official Hamburg {label} directory index is not UTF-8 HTML"
        ) from exc
    parser = _DirectoryHrefParser()
    parser.feed(text)
    filenames: set[str] = set()
    for href in parser.hrefs:
        parsed = urlparse(href)
        if parsed.scheme or parsed.netloc or parsed.query or parsed.fragment:
            continue
        filename = parsed.path
        if not filename or "/" in filename or "\\" in filename:
            continue
        filenames.add(filename)
    return tuple(sorted(filenames))


def _validate_resolved_hamburg_signal_asset(asset: HamburgSignalAsset) -> None:
    map_xml = _HAMBURG_MAP_ASSET_PATTERN.fullmatch(asset.map_xml)
    map_kml = _HAMBURG_MAP_ASSET_PATTERN.fullmatch(asset.map_kml)
    ocit_c = _HAMBURG_OCIT_C_ASSET_PATTERN.fullmatch(Path(asset.ocit_xml).name)
    if map_xml is None or map_xml.group("extension").casefold() != "xml":
        raise ValueError(f"invalid resolved Hamburg MAP XML filename: {asset.map_xml!r}")
    if map_kml is None or map_kml.group("extension").casefold() != "kml":
        raise ValueError(f"invalid resolved Hamburg MAP KML filename: {asset.map_kml!r}")
    if ocit_c is None:
        raise ValueError(f"invalid resolved Hamburg OCIT-C filename: {asset.ocit_xml!r}")
    normalized_node = _normalized_hamburg_signal_node_id(asset.node_id)
    filename_nodes = {
        str(int(map_xml.group("node"))),
        str(int(map_kml.group("node"))),
        str(int(ocit_c.group("node"))),
    }
    map_identity = (
        map_xml.group("ocit_stem").casefold(),
        (map_xml.group("revision") or "").casefold(),
    )
    kml_identity = (
        map_kml.group("ocit_stem").casefold(),
        (map_kml.group("revision") or "").casefold(),
    )
    if filename_nodes != {normalized_node}:
        raise ValueError(
            f"resolved Hamburg signal asset filenames do not match node {asset.node_id!r}"
        )
    if map_identity != kml_identity:
        raise ValueError("resolved Hamburg MAP XML/KML filenames do not identify one revision")
    if map_identity[0] != ocit_c.group("ocit_stem").casefold():
        raise ValueError("resolved Hamburg MAP and OCIT-C filenames do not share one asset stem")


def write_json(path: Path, value: Any) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False, default=_json_default) + "\n"
    path.write_text(text, encoding="utf-8")
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _odata_timestamp(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _require_aware_datetime(value: datetime, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must include a UTC offset")


def _atomic_write_json(path: Path, value: Any) -> None:
    text = json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False, default=_json_default) + "\n"
    _atomic_write_bytes(path, text.encode("utf-8"))


def _atomic_write_bytes(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary_path = Path(handle.name)
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
        temporary_path = None
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def _urlopen_bytes(request: Request, timeout_seconds: float) -> bytes:
    with urlopen(request, timeout=timeout_seconds) as response:
        return response.read()


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _optional_int(value: Any) -> int | None:
    return None if value in (None, "") else int(value)


def _coordinate_pair(value: Any) -> tuple[float, float] | None:
    geometry = _mapping(value)
    if geometry.get("type") == "Feature":
        geometry = _mapping(geometry.get("geometry"))
    coordinates = geometry.get("coordinates")
    if geometry.get("type") == "Point" and isinstance(coordinates, list) and len(coordinates) >= 2:
        return float(coordinates[0]), float(coordinates[1])
    return None


def _first_point(locations: Sequence[Any]) -> tuple[float, float] | None:
    for location in locations:
        point = _coordinate_pair(_mapping(location).get("location"))
        if point is not None:
            return point
    return None


def _json_default(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if hasattr(value, "__dict__"):
        return value.__dict__
    raise TypeError(f"cannot serialize {type(value).__name__}")
