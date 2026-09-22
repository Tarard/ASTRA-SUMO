from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from urllib.request import Request

import pytest

from torii_sumo.core.hamburg_official import (
    OFFICIAL_SIGNAL_CATALOG_API_URL,
    HamburgCatalogUnavailableError,
    download_hamburg_traffic_light_catalog,
    parse_hamburg_traffic_light_catalog,
)


def _resource(resource_id: str, name: str, resource_format: str, url: str) -> dict[str, str]:
    return {
        "id": resource_id,
        "name": name,
        "format": resource_format,
        "url": url,
    }


def _catalog_payload() -> dict[str, object]:
    resources = [
        _resource(
            "primary",
            "STA Layerabfrage: Primärsignal je Fahrspurbeziehung - letzte 10 "
            "Beobachtungswerte (JSON)",
            "JSON",
            "https://tld.iot.hamburg.de/v1.0/Datastreams?"
            "$filter=properties/serviceName%20eq%20%27HH_STA_traffic_lights%27%20and%20"
            "properties/layerName%20eq%20%27primary_signal%27",
        ),
        _resource(
            "program",
            "STA Layerabfrage: Signalprogrammstatus je Fahrspurbeziehung - letzte 10 "
            "Beobachtungswerte (JSON)",
            "JSON",
            "https://tld.iot.hamburg.de/v1.1/Datastreams?"
            "$filter=properties/serviceName%20eq%20%27HH_STA_traffic_lights%27%20and%20"
            "properties/layerName%20eq%20%27signal_program%27",
        ),
        _resource(
            "cycle",
            "STA Layerabfrage: Wellensekunde je Fahrspurbeziehung - letzte 10 "
            "Beobachtungswerte (JSON)",
            "JSON",
            "https://tld.iot.hamburg.de/v1.1/Datastreams?"
            "$filter=properties/serviceName%20eq%20%27HH_STA_traffic_lights%27%20and%20"
            "properties/layerName%20eq%20%27cycle_second%27",
        ),
        _resource(
            "map",
            "MAP-Dateien",
            "HTML",
            "https://daten-hamburg.de/tlf_public/",
        ),
        _resource(
            "ocit",
            "OCIT-C Dateien",
            "HTML",
            "https://daten-hamburg.de/tlf_public/OCIT-C/",
        ),
        _resource(
            "guide",
            "TLD Usage Guide",
            "PDF",
            "https://daten-hamburg.de/tlf_public/TLD_UsageGuide_V1.2.pdf",
        ),
    ]
    resources.extend(
        _resource(
            f"other-{index}",
            f"Unselected official resource {index}",
            "JSON",
            "https://tld.iot.hamburg.de/v1.1/Things",
        )
        for index in range(12)
    )
    return {
        "success": True,
        "result": {
            "id": "247b868e-b947-488f-8bc5-ac902b00976f",
            "name": "traffic-lights-data-hamburg6",
            "metadata_modified": "2026-05-20T21:04:30.634036",
            "resources": resources,
        },
    }


class _CatalogTransport:
    def __init__(self, raw_catalog: bytes) -> None:
        self.raw_catalog = raw_catalog
        self.urls: list[str] = []

    def __call__(self, request: Request, timeout_seconds: float) -> bytes:
        assert timeout_seconds > 0
        self.urls.append(request.full_url)
        if request.full_url == OFFICIAL_SIGNAL_CATALOG_API_URL:
            return self.raw_catalog
        if request.full_url.endswith(".pdf"):
            return b"%PDF-1.7\nfake official usage guide\n"
        raise AssertionError(f"unexpected URL: {request.full_url}")


def test_catalog_download_snapshots_selected_resources_and_usage_guide(
    tmp_path: Path,
) -> None:
    raw = json.dumps(_catalog_payload(), ensure_ascii=False).encode("utf-8")
    transport = _CatalogTransport(raw)

    catalog, audit = download_hamburg_traffic_light_catalog(
        tmp_path,
        transport=transport,
    )
    _, cached_audit = download_hamburg_traffic_light_catalog(
        tmp_path,
        transport=transport,
    )

    assert catalog.primary_signal_api_base == "https://tld.iot.hamburg.de/v1.0/"
    assert catalog.auxiliary_signal_api_base == "https://tld.iot.hamburg.de/v1.1/"
    assert catalog.map_asset_base == "https://daten-hamburg.de/tlf_public/"
    assert catalog.ocit_c_asset_base == "https://daten-hamburg.de/tlf_public/OCIT-C/"
    assert catalog.metadata_modified == "2026-05-20T21:04:30.634036"
    assert catalog.resource_count == 18
    assert set(audit["selected_resources"]) == {
        "primary_signal",
        "signal_program",
        "cycle_second",
        "map",
        "ocit_c",
        "usage_guide",
    }
    raw_path = Path(audit["raw_catalog"]["path"])
    assert raw_path.read_bytes() == raw
    assert audit["raw_catalog"]["sha256"] == hashlib.sha256(raw).hexdigest()
    usage_path = Path(audit["usage_guide_download"]["path"])
    assert usage_path.read_bytes().startswith(b"%PDF-")
    assert audit["usage_guide_download"]["cache_hit"] is False
    assert cached_audit["usage_guide_download"]["cache_hit"] is True
    assert Path(audit["selected_snapshot"]["path"]).is_file()
    assert transport.urls.count(OFFICIAL_SIGNAL_CATALOG_API_URL) == 2
    assert sum(url.endswith(".pdf") for url in transport.urls) == 1


@pytest.mark.parametrize("key_name", ["missing", "duplicate"])
def test_catalog_parser_rejects_missing_or_duplicate_required_resource(
    key_name: str,
) -> None:
    payload = _catalog_payload()
    resources = payload["result"]["resources"]  # type: ignore[index]
    assert isinstance(resources, list)
    primary = resources[0]
    if key_name == "missing":
        resources.pop(0)
    else:
        resources.append(copy.deepcopy(primary))

    with pytest.raises(ValueError, match="exactly one 'primary_signal' resource"):
        parse_hamburg_traffic_light_catalog(payload)


@pytest.mark.parametrize(
    "bad_url",
    [
        "http://tld.iot.hamburg.de/v1.0/Datastreams?"
        "$filter=HH_STA_traffic_lights%20primary_signal",
        "https://example.org/v1.0/Datastreams?"
        "$filter=HH_STA_traffic_lights%20primary_signal",
        "https://tld.iot.hamburg.de/v1.1/Datastreams?"
        "$filter=HH_STA_traffic_lights%20primary_signal",
    ],
)
def test_catalog_parser_rejects_unofficial_or_wrong_version_primary_url(
    bad_url: str,
) -> None:
    payload = _catalog_payload()
    resources = payload["result"]["resources"]  # type: ignore[index]
    assert isinstance(resources, list)
    resources[0]["url"] = bad_url

    with pytest.raises(ValueError):
        parse_hamburg_traffic_light_catalog(payload)


def test_catalog_downloader_marks_only_transport_failure_as_unavailable(
    tmp_path: Path,
) -> None:
    def unavailable(_request: Request, _timeout_seconds: float) -> bytes:
        raise TimeoutError("catalog timed out")

    with pytest.raises(HamburgCatalogUnavailableError, match="catalog API is unavailable"):
        download_hamburg_traffic_light_catalog(tmp_path, transport=unavailable)


def test_catalog_parser_does_not_treat_invalid_available_json_as_unavailable(
    tmp_path: Path,
) -> None:
    def invalid_json(_request: Request, _timeout_seconds: float) -> bytes:
        return b"not json"

    with pytest.raises(ValueError, match="invalid UTF-8 JSON"):
        download_hamburg_traffic_light_catalog(tmp_path, transport=invalid_json)
