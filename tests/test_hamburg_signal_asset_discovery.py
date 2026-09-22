from __future__ import annotations

from pathlib import Path
from urllib.request import Request

import pytest

from torii_sumo.core.hamburg_official import (
    HamburgCatalogResource,
    HamburgTrafficLightCatalog,
    audit_hamburg_signal_asset_directory_history,
    discover_hamburg_signal_assets,
    download_resolved_hamburg_signal_assets,
    resolve_hamburg_signal_asset_directory_indexes,
)


MAP_BASE = "https://daten-hamburg.de/tlf_public/"
OCIT_BASE = "https://daten-hamburg.de/tlf_public/OCIT-C/"


def _index(*filenames: str, body_text: str = "") -> bytes:
    links = "".join(f'<a href="{filename}">{filename}</a>' for filename in filenames)
    return f"<html><body>{body_text}{links}</body></html>".encode()


def _map_names(node_id: str, version: str, revision: str) -> tuple[str, str]:
    stem = f"MAP_ITS_23_{node_id}_{version}_{revision}_Quelle_ETRS89"
    return f"{stem}.xml", f"{stem}.kml"


def _catalog() -> HamburgTrafficLightCatalog:
    primary = HamburgCatalogResource(
        key="primary_signal",
        resource_id="primary",
        name="primary",
        format="JSON",
        url="https://tld.iot.hamburg.de/v1.0/Datastreams",
    )
    auxiliary = HamburgCatalogResource(
        key="signal_program",
        resource_id="program",
        name="program",
        format="JSON",
        url="https://tld.iot.hamburg.de/v1.1/Datastreams",
    )
    cycle = HamburgCatalogResource(
        key="cycle_second",
        resource_id="cycle",
        name="cycle",
        format="JSON",
        url="https://tld.iot.hamburg.de/v1.1/Datastreams",
    )
    map_resource = HamburgCatalogResource(
        key="map",
        resource_id="map-resource",
        name="MAP-Dateien",
        format="HTML",
        url=MAP_BASE,
    )
    ocit_resource = HamburgCatalogResource(
        key="ocit_c",
        resource_id="ocit-resource",
        name="OCIT-C Dateien",
        format="HTML",
        url=OCIT_BASE,
    )
    usage = HamburgCatalogResource(
        key="usage_guide",
        resource_id="guide",
        name="TLD Usage Guide",
        format="PDF",
        url="https://daten-hamburg.de/tlf_public/TLD_UsageGuide_V1.2.pdf",
    )
    return HamburgTrafficLightCatalog(
        dataset_id="traffic-lights-data-hamburg6",
        package_id="package",
        metadata_modified="2026-05-20T21:04:30.634036",
        resource_count=6,
        catalog_api_url=(
            "https://suche.transparenz.hamburg.de/api/3/action/package_show?"
            "id=traffic-lights-data-hamburg6"
        ),
        fetched_at_utc="2026-07-19T12:00:00Z",
        raw_sha256="a" * 64,
        primary_signal=primary,
        signal_program=auxiliary,
        cycle_second=cycle,
        map_files=map_resource,
        ocit_c_files=ocit_resource,
        usage_guide=usage,
        primary_signal_api_base="https://tld.iot.hamburg.de/v1.0/",
        auxiliary_signal_api_base="https://tld.iot.hamburg.de/v1.1/",
        map_asset_base=MAP_BASE,
        ocit_c_asset_base=OCIT_BASE,
    )


def test_resolver_uses_exact_filename_node_and_abstains_for_missing_node() -> None:
    map_2349 = _map_names("2349", "7.4", "R4")
    map_2394 = _map_names("2394", "9.3", "R3")
    distractor = _map_names("12349", "1.1", "R1")
    map_html = _index(
        *map_2349,
        *map_2394,
        *distractor,
        body_text="2403 is only page text, not a published asset",
    )
    ocit_html = _index(
        "MAP_ITS_23_2349_7.4.xml",
        "MAP_ITS_23_2394_9.3.xml",
        "MAP_ITS_23_12349_1.1.xml",
    )

    assets, report = resolve_hamburg_signal_asset_directory_indexes(
        ("2349", "2394", "2403"),
        map_index_html=map_html,
        ocit_c_index_html=ocit_html,
        map_asset_base=MAP_BASE,
        ocit_c_asset_base=OCIT_BASE,
    )

    assert [asset.node_id for asset in assets] == ["2349", "2394"]
    assert assets[0].map_xml == map_2349[0]
    assert assets[0].map_kml == map_2349[1]
    assert assets[0].ocit_xml == "OCIT-C/MAP_ITS_23_2349_7.4.xml"
    assert report["decision"] == "review_required"
    assert report["resolved_node_ids"] == ["2349", "2394"]
    assert report["unresolved_node_ids"] == ["2403"]
    resolution_2403 = report["resolutions"][2]
    assert resolution_2403["reason_codes"] == [
        "official_directory_has_no_map_assets",
        "official_directory_has_no_ocit_c_asset",
    ]
    assert resolution_2403["autonomous_action"] == (
        "abstain_no_signal_materialization"
    )


def test_resolver_fails_closed_when_two_map_revisions_are_published() -> None:
    map_r3 = _map_names("2349", "7.4", "R3")
    map_r4 = _map_names("2349", "7.4", "R4")

    assets, report = resolve_hamburg_signal_asset_directory_indexes(
        ("2349",),
        map_index_html=_index(*map_r3, *map_r4),
        ocit_c_index_html=_index("MAP_ITS_23_2349_7.4.xml"),
        map_asset_base=MAP_BASE,
        ocit_c_asset_base=OCIT_BASE,
    )

    assert assets == ()
    assert report["resolutions"][0]["complete_triplet_count"] == 2
    assert report["resolutions"][0]["reason_codes"] == [
        "official_directory_asset_triplet_ambiguous"
    ]


def test_history_audit_keeps_historical_only_assets_from_substituting_current() -> None:
    map_files = _map_names("2349", "7.4", "R4")
    current = {
        "label": "current",
        "map_index_html": _index(*map_files),
        "ocit_c_index_html": _index("MAP_ITS_23_2349_7.4.xml"),
        "map_index_url": MAP_BASE,
        "ocit_c_index_url": OCIT_BASE,
    }
    historical = {
        "label": "archive",
        "map_index_html": _index(*map_files, *_map_names("2394", "9.3", "R3")),
        "ocit_c_index_html": _index(
            "MAP_ITS_23_2349_7.4.xml",
            "MAP_ITS_23_2394_9.3.xml",
        ),
        "map_index_url": "https://archiv.transparenz.hamburg.de/map.html",
        "ocit_c_index_url": "https://archiv.transparenz.hamburg.de/ocit.html",
    }

    report = audit_hamburg_signal_asset_directory_history(
        ("2349", "2394", "2403"),
        snapshots=(current, historical),
        current_snapshot_label="current",
    )

    assert report["decision"] == "review_required"
    assert report["resolved_node_ids"] == ["2349"]
    assert report["unresolved_node_ids"] == ["2394", "2403"]
    assert report["publication_gap"]["unresolved_current_node_ids"] == ["2394", "2403"]
    by_node = {item["node_id"]: item for item in report["nodes"]}
    assert by_node["2349"]["status"] == "current_published"
    assert by_node["2394"]["status"] == "historical_only"
    assert by_node["2394"]["autonomous_action"] == (
        "abstain_do_not_substitute_historical_asset"
    )
    assert by_node["2403"]["status"] == "not_found_in_checked_directories"


def test_discovery_fetches_only_catalog_published_directory_urls(
    tmp_path: Path,
) -> None:
    map_files = _map_names("2349", "7.4", "R4")
    responses = {
        MAP_BASE: _index(*map_files),
        OCIT_BASE: _index("MAP_ITS_23_2349_7.4.xml"),
    }
    requested_urls: list[str] = []

    def transport(request: Request, timeout_seconds: float) -> bytes:
        assert timeout_seconds == 12.0
        requested_urls.append(request.full_url)
        return responses[request.full_url]

    assets, report = discover_hamburg_signal_assets(
        _catalog(),
        ("2349",),
        output_dir=tmp_path,
        timeout_seconds=12.0,
        transport=transport,
    )

    assert requested_urls == [MAP_BASE, OCIT_BASE]
    assert [asset.node_id for asset in assets] == ["2349"]
    assert report["decision"] == "pass"
    assert report["catalog"]["map_resource_id"] == "map-resource"
    assert report["catalog"]["ocit_c_resource_id"] == "ocit-resource"
    assert len(report["directory_snapshots"]["map"]["sha256"]) == 64
    assert Path(report["directory_snapshots"]["map"]["path"]).read_bytes() == (
        responses[MAP_BASE]
    )
    assert Path(report["directory_snapshots"]["ocit_c"]["path"]).read_bytes() == (
        responses[OCIT_BASE]
    )
    discovery_path = Path(report["discovery_snapshot"]["path"])
    assert discovery_path.is_file()
    assert report["discovery_snapshot"]["bytes"] == discovery_path.stat().st_size

    map_snapshot = Path(report["directory_snapshots"]["map"]["path"])
    map_snapshot.write_bytes(b"corrupt")
    with pytest.raises(ValueError, match="content-addressed filename"):
        discover_hamburg_signal_assets(
            _catalog(),
            ("2349",),
            output_dir=tmp_path,
            timeout_seconds=12.0,
            transport=transport,
        )


class _AssetClient:
    def __init__(self) -> None:
        self.urls: list[str] = []

    def bytes(self, url: str) -> bytes:
        self.urls.append(url)
        return f"official:{url}".encode()


def test_resolved_downloader_uses_url_keyed_cache_and_exact_urls(tmp_path: Path) -> None:
    map_files = _map_names("2349", "7.4", "R4")
    assets, report = resolve_hamburg_signal_asset_directory_indexes(
        ("2349",),
        map_index_html=_index(*map_files),
        ocit_c_index_html=_index("MAP_ITS_23_2349_7.4.xml"),
        map_asset_base=MAP_BASE,
        ocit_c_asset_base=OCIT_BASE,
    )
    assert report["decision"] == "pass"
    client = _AssetClient()

    paths, first_manifest = download_resolved_hamburg_signal_assets(
        client,  # type: ignore[arg-type]
        assets,
        tmp_path,
        map_asset_base=MAP_BASE,
        ocit_c_asset_base=OCIT_BASE,
    )
    _, second_manifest = download_resolved_hamburg_signal_assets(
        client,  # type: ignore[arg-type]
        assets,
        tmp_path,
        map_asset_base=MAP_BASE,
        ocit_c_asset_base=OCIT_BASE,
    )

    assert client.urls == [
        f"{MAP_BASE}{map_files[0]}",
        f"{MAP_BASE}{map_files[1]}",
        f"{OCIT_BASE}MAP_ITS_23_2349_7.4.xml",
    ]
    assert all(item["cache_hit"] is False for item in first_manifest)
    assert all(item["cache_hit"] is True for item in second_manifest)
    assert all(item["url_cache_key"] for item in first_manifest)
    assert all(Path(path).name.startswith("2349_") for path in paths["2349"].values())


def test_resolver_rejects_nonofficial_directory_base() -> None:
    with pytest.raises(ValueError, match="allowlisted HTTPS"):
        resolve_hamburg_signal_asset_directory_indexes(
            ("2349",),
            map_index_html=_index(),
            ocit_c_index_html=_index(),
            map_asset_base="https://example.org/tlf_public/",
            ocit_c_asset_base=OCIT_BASE,
        )
