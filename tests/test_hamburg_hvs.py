from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path

from torii_sumo.road_network.adapters.hamburg_hvs import (
    HVS_CLASSIFICATION_SCHEME,
    HVS_SOURCE_ID,
    read_hamburg_hvs_snapshot,
)


FIXTURE = Path(__file__).parent / "fixtures" / "road_network" / "hamburg_hvs_sample.geojson"
REQUEST_URL = (
    "https://api.hamburg.de/datasets/v1/hauptverkehrsstrassen/collections/"
    "hauptverkehrsstrassen/items?f=json&limit=100"
)
VALID_FROM = datetime(2026, 7, 19, tzinfo=UTC)
VALID_TO = datetime(2026, 7, 20, tzinfo=UTC)
TARGET_TIME = datetime(2026, 7, 19, 15, 35, 27, tzinfo=UTC)
RETRIEVED_AT = datetime(2026, 7, 19, 15, 40, 32, tzinfo=UTC)
BBOX = (9.978, 53.539, 10.0005, 53.5475)


def _read(**overrides: object) -> dict[str, object]:
    args: dict[str, object] = {
        "request_url": REQUEST_URL,
        "bbox": BBOX,
        "target_time": TARGET_TIME,
        "retrieved_at": RETRIEVED_AT,
        "valid_from": VALID_FROM,
        "valid_to": VALID_TO,
    }
    args.update(overrides)
    return read_hamburg_hvs_snapshot(FIXTURE, **args)  # type: ignore[arg-type]


def test_hvs_adapter_preserves_direct_collection_membership_without_mapping_or_mutation() -> None:
    before = FIXTURE.read_bytes()
    report = _read()

    assert report["status"] == "pass"
    assert report["classification_only"] is True
    assert report["automatic_promotion_gate"] == "blocked"
    assert report["source_id"] == HVS_SOURCE_ID
    assert report["source_snapshot"]["sha256"] == hashlib.sha256(before).hexdigest()  # type: ignore[index]
    assert report["source_snapshot"]["time_alignment_status"] == "pass"  # type: ignore[index]
    assert report["counts"] == {"feature_count": 2, "membership_assertion_count": 2}

    assertions = report["membership_assertions"]
    assert {item["value"] for item in assertions} == {"hvs"}  # type: ignore[union-attr]
    assert {item["classification_scheme"] for item in assertions} == {HVS_CLASSIFICATION_SCHEME}  # type: ignore[union-attr]
    assert {item["mapping_status"] for item in assertions} == {"unmapped"}  # type: ignore[union-attr]
    assert all(item["source_ref"]["namespace"] == "official.hamburg_hvs" for item in assertions)  # type: ignore[union-attr]

    raw_feature = next(
        item for item in report["raw_feature_assertions"] if item["feature_id"] == "hvs-am-sandtorkai-001"  # type: ignore[union-attr]
    )
    assert raw_feature["properties"]["klasse"] == "G"
    assert raw_feature["membership_assertion_id"] in {item["assertion_id"] for item in assertions}  # type: ignore[union-attr]

    source = report["official_category_source"]
    assert source["source_id"] == HVS_SOURCE_ID  # type: ignore[index]
    assert source["status"] == "pass"  # type: ignore[index]
    assert source["source_snapshot"]["sha256"] == hashlib.sha256(before).hexdigest()  # type: ignore[index]
    assert source["automatic_promotion_gate"] == "blocked"  # type: ignore[index]
    template = report["reviewed_membership_assignment_template"]
    assert template["requires_reviewed_official_conflation"] is True  # type: ignore[index]
    assert template["example"]["target_ref"] == "<reviewed HH-SIB road_link_assertion ref>"  # type: ignore[index]
    assert FIXTURE.read_bytes() == before


def test_hvs_adapter_blocks_wrong_official_collection_endpoint() -> None:
    report = _read(
        request_url=(
            "https://api.hamburg.de/datasets/v1/strassen_und_wegenetz/collections/"
            "strassennetz_gesamt/items?f=json"
        )
    )

    assert report["status"] == "blocked"
    assert report["membership_assertions"] == []
    assert "hvs_collection_request_url_required" in report["blocking_reasons"]  # type: ignore[operator]
    assert report["official_category_source"]["status"] == "blocked"  # type: ignore[index]


def test_hvs_adapter_blocks_expected_hash_mismatch() -> None:
    report = _read(expected_sha256="0" * 64)

    assert report["status"] == "blocked"
    assert report["raw_feature_assertions"] == []
    assert report["membership_assertions"] == []
    assert "source_sha256_mismatch" in report["blocking_reasons"]  # type: ignore[operator]
    assert report["official_category_source"]["source_snapshot"]["sha256"] == hashlib.sha256(  # type: ignore[index]
        FIXTURE.read_bytes()
    ).hexdigest()


def test_hvs_adapter_requires_declared_validity_for_target_time_claim() -> None:
    report = _read(valid_from=None, valid_to=None)

    assert report["status"] == "review_required"
    assert report["source_snapshot"]["time_alignment_status"] == "review_required"  # type: ignore[index]
    assert "source_validity_not_declared" in report["review_reasons"]  # type: ignore[operator]
    assert {item["status"] for item in report["membership_assertions"]} == {"review_required"}  # type: ignore[union-attr]
