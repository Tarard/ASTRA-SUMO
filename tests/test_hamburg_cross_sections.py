from __future__ import annotations

import copy
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from torii_sumo.road_network.adapters.hamburg_cross_sections import (
    HAMBURG_CROSS_SECTION_CRS,
    read_hamburg_cross_section_snapshot,
)


BBOX = (564630.0, 5934770.0, 565347.0, 5935520.0)
REQUEST_URL = (
    "https://api.hamburg.de/datasets/v1/querschnitte/collections/querschnitte/items"
    "?f=json&limit=2&bbox=564630,5934770,565347,5935520"
    "&bbox-crs=http%3A%2F%2Fwww.opengis.net%2Fdef%2Fcrs%2FEPSG%2F0%2F25832"
    "&crs=http%3A%2F%2Fwww.opengis.net%2Fdef%2Fcrs%2FEPSG%2F0%2F25832"
)
TARGET_TIME = datetime(2026, 7, 18, 12, 30, tzinfo=UTC)
RETRIEVED_AT = datetime(2026, 7, 20, 19, 34, 18, tzinfo=UTC)
VALID_FROM = datetime(2026, 7, 1, tzinfo=UTC)
VALID_TO = datetime(2026, 8, 1, tzinfo=UTC)


def _feature(feature_id: int, side: str, strip_number: int, x_offset: float) -> dict[str, object]:
    properties = {
        "von_netzknoten": "242500103",
        "nach_netzknoten": "242500104",
        "von_station": 16,
        "bis_station": 20,
        "streifen": side,
        "streifennr": strip_number,
        "breite": 1070,
        "bis_breite": 790,
        "art": "210",
        "art_klartext": "Gehweg, Z 241 - 30 / Z 241 - 31",
        "art_oberflaeche": "11",
        "art_oberflaeche_klartext": "befestigt, versiegelt",
        "color": "#aee169",
    }
    x = 564699.0 + x_offset
    return {
        "type": "Feature",
        "id": feature_id,
        "geometry": {
            "type": "Polygon",
            "coordinates": [[[x, 5934765.0], [x + 2, 5934765.0], [x + 2, 5934767.0], [x, 5934765.0]]],
        },
        "properties": properties,
    }


def _payload() -> dict[str, object]:
    return {
        "type": "FeatureCollection",
        "numberReturned": 2,
        "numberMatched": 2,
        "timeStamp": "2026-07-20T19:34:18Z",
        "features": [_feature(3289178, "R", 6, 0.0), _feature(3289179, "M", 0, 3.0)],
        "links": [{"rel": "self", "href": REQUEST_URL}],
    }


def _write(tmp_path: Path, payload: dict[str, object]) -> Path:
    path = tmp_path / "querschnitte.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def _read(path: Path, **overrides: object) -> dict[str, object]:
    arguments = {
        "request_url": REQUEST_URL,
        "bbox": BBOX,
        "target_time": TARGET_TIME,
        "retrieved_at": RETRIEVED_AT,
        "valid_from": VALID_FROM,
        "valid_to": VALID_TO,
    }
    arguments.update(overrides)
    return read_hamburg_cross_section_snapshot(path, **arguments)  # type: ignore[arg-type]


def test_cross_section_adapter_preserves_complete_official_oaf_snapshot_without_promotion(tmp_path: Path) -> None:
    payload = _payload()
    path = _write(tmp_path, payload)
    before = path.read_bytes()

    report = _read(path)

    assert report["status"] == "review_required"
    assert report["acquisition_status"] == "pass"
    assert report["claim_status"] == "review_required"
    assert report["review_reasons"] == ["source_validity_is_caller_declared"]
    assert report["classification_only"] is True
    assert report["automatic_promotion_gate"] == "blocked"
    assert report["counts"] == {"feature_count": 2, "profile_count": 1}
    assert report["source_snapshot"]["sha256"] == hashlib.sha256(before).hexdigest()
    assert report["source_snapshot"]["bbox_crs"] == HAMBURG_CROSS_SECTION_CRS
    assert report["source_snapshot"]["output_crs"] == HAMBURG_CROSS_SECTION_CRS
    assert report["source_snapshot"]["server_timestamp_is_validity"] is False
    assert path.read_bytes() == before

    assertion = report["raw_feature_assertions"][0]
    expected = next(item for item in payload["features"] if str(item["id"]) == assertion["feature_id"])
    assert assertion["geometry"] == expected["geometry"]
    assert assertion["properties"] == expected["properties"]
    assert assertion["source_ref"]["namespace"] == "official.hamburg_querschnitte"
    assert assertion["source_ref"]["source_sha256"] == hashlib.sha256(before).hexdigest()
    assert assertion["strip_side_raw"] in {"M", "R"}
    assert {item["strip_number_raw"] for item in report["raw_feature_assertions"]} == {0, 6}
    assert "station_direction" not in assertion
    assert "motor_vehicle" not in assertion
    assert "sumo_lane" not in assertion
    assert set(report["not_applicable_claims"]) >= {
        "station_direction",
        "motor_vehicle_lane_class",
        "sumo_edge_or_lane",
        "lane_transition",
    }


def test_cross_section_adapter_requires_declared_validity_for_time_claim(tmp_path: Path) -> None:
    report = _read(_write(tmp_path, _payload()), valid_from=None, valid_to=None)

    assert report["status"] == "review_required"
    assert report["acquisition_status"] == "pass"
    assert report["review_reasons"] == ["source_validity_not_declared"]


def test_cross_section_adapter_blocks_hash_mismatch(tmp_path: Path) -> None:
    report = _read(_write(tmp_path, _payload()), expected_sha256="0" * 64)

    assert report["status"] == "blocked"
    assert report["raw_feature_assertions"] == []
    assert report["blocking_reasons"] == ["source_sha256_mismatch"]


@pytest.mark.parametrize("failure", ["count", "next"])
def test_cross_section_adapter_blocks_incomplete_oaf_pagination(tmp_path: Path, failure: str) -> None:
    payload = _payload()
    if failure == "count":
        payload["numberMatched"] = 3
    else:
        payload["links"].append({"rel": "next", "href": f"{REQUEST_URL}&offset=2"})

    report = _read(_write(tmp_path, payload))

    assert report["acquisition_status"] == "blocked"
    expected = "incomplete_or_inconsistent_feature_collection" if failure == "count" else "next_page_link_present"
    assert expected in report["blocking_reasons"]


@pytest.mark.parametrize(
    ("request_url", "reason"),
    [
        (REQUEST_URL.replace("api.hamburg.de", "example.org"), "official_hamburg_https_request_url_required"),
        (REQUEST_URL.replace("565347", "565348"), "request_bbox_mismatch"),
        (REQUEST_URL.replace("25832", "4326"), "epsg_25832_request_crs_required"),
    ],
)
def test_cross_section_adapter_blocks_non_authoritative_request_identity(
    tmp_path: Path,
    request_url: str,
    reason: str,
) -> None:
    report = _read(_write(tmp_path, _payload()), request_url=request_url)

    assert report["acquisition_status"] == "blocked"
    assert reason in report["blocking_reasons"]


@pytest.mark.parametrize("failure", ["feature_id", "strip_key"])
def test_cross_section_adapter_blocks_duplicate_source_identity(tmp_path: Path, failure: str) -> None:
    payload = _payload()
    first, second = payload["features"]
    if failure == "feature_id":
        second["id"] = first["id"]
    else:
        second["properties"]["streifen"] = first["properties"]["streifen"]
        second["properties"]["streifennr"] = first["properties"]["streifennr"]

    report = _read(_write(tmp_path, payload))

    assert report["acquisition_status"] == "blocked"
    prefix = "duplicate_feature_id:" if failure == "feature_id" else "duplicate_cross_section_strip:"
    assert any(reason.startswith(prefix) for reason in report["blocking_reasons"])


@pytest.mark.parametrize("failure", ["geometry", "side", "station", "strip_number"])
def test_cross_section_adapter_blocks_invalid_official_strip(tmp_path: Path, failure: str) -> None:
    payload = copy.deepcopy(_payload())
    feature = payload["features"][0]
    if failure == "geometry":
        feature["geometry"]["coordinates"][0][-1] = [564700.0, 5934766.0]
    elif failure == "side":
        feature["properties"]["streifen"] = "X"
    elif failure == "station":
        feature["properties"]["bis_station"] = 16
    else:
        feature["properties"]["streifennr"] = -1

    report = _read(_write(tmp_path, payload))

    assert report["acquisition_status"] == "blocked"
    expected = {
        "geometry": "polygon_ring_not_closed:0",
        "side": "unsupported_strip_side:0:X",
        "station": "invalid_station_interval:0",
        "strip_number": "invalid_strip_number:0",
    }[failure]
    assert expected in report["blocking_reasons"]


def test_cross_section_adapter_blocks_target_outside_declared_validity(tmp_path: Path) -> None:
    report = _read(
        _write(tmp_path, _payload()),
        target_time=datetime(2025, 7, 18, tzinfo=UTC),
    )

    assert report["status"] == "blocked"
    assert report["acquisition_status"] == "pass"
    assert report["blocking_reasons"] == ["target_time_outside_declared_validity"]
