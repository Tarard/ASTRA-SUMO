from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

from torii_sumo.road_network.adapters.hamburg_hh_sib import read_hamburg_hh_sib_snapshot


FIXTURE = Path(__file__).parent / "fixtures" / "hamburg_hh_sib_am_sandtorkai_2026-07-19.json"
MISSING_ROAD_KEY_FIXTURE = Path(__file__).parent / "fixtures" / "road_network" / "hh_sib_missing_strassenschluessel.geojson"
REQUEST_URL = (
    "https://api.hamburg.de/datasets/v1/strassen_und_wegenetz/collections/"
    "strassennetz_gesamt/items?f=json&limit=100&strassenname=Am%20Sandtorkai"
)
VALID_FROM = datetime(2026, 7, 19, tzinfo=UTC)
VALID_TO = datetime(2026, 7, 20, tzinfo=UTC)
TARGET_TIME = datetime(2026, 7, 19, 15, 35, 27, tzinfo=UTC)
RETRIEVED_AT = datetime(2026, 7, 19, 15, 40, 32, tzinfo=UTC)


def test_hh_sib_adapter_parses_real_am_sandtorkai_linear_properties_without_mutation() -> None:
    before = FIXTURE.read_bytes()

    report = read_hamburg_hh_sib_snapshot(
        FIXTURE,
        request_url=REQUEST_URL,
        bbox=(9.978, 53.539, 10.0005, 53.5475),
        target_time=TARGET_TIME,
        retrieved_at=RETRIEVED_AT,
        valid_from=VALID_FROM,
        valid_to=VALID_TO,
    )

    assert report["status"] == "pass"
    assert report["source_snapshot"]["sha256"] == hashlib.sha256(before).hexdigest()
    assert report["source_snapshot"]["time_alignment_status"] == "pass"
    assert report["source_snapshot"]["retrieved_at"] == "2026-07-19T15:40:32+00:00"
    assert report["counts"] == {
        "feature_count": 12,
        "road_link_assertion_count": 5,
        "motor_road_link_assertion_count": 2,
        "pedestrian_road_link_assertion_count": 3,
        "property_assignment_count": 107,
        "gap_count": 0,
        "overlap_count": 0,
    }
    assert report["classification_only"] is True
    assert report["automatic_promotion_gate"] == "blocked"
    assert report["acquisition_status"] == "pass"
    assert report["claim_status"] == "pass"
    assert FIXTURE.read_bytes() == before

    main = next(item for item in report["road_link_assertions"] if item["length_m"] == 986.0)
    assert main["road_name"] == "Am Sandtorkai"
    assert main["from_network_node"] == "242500211"
    assert main["to_network_node"] == "242500071"
    assert main["station_coverage"] == {
        "begin_m": 0.0,
        "end_m": 986.0,
        "gap_count": 0,
        "overlap_count": 0,
        "status": "pass",
    }

    main_ref = main["source_ref"]["object_id"]
    lane_with = [
        item
        for item in report["property_assignments"]
        if item["target_ref"]["object_id"] == main_ref and item["property_name"] == "lane_count_with_stationing"
    ]
    assert [(item["s_from_m"], item["s_to_m"], item["value"]) for item in lane_with] == [
        (0.0, 170.0, 2),
        (170.0, 412.0, 2),
        (412.0, 474.0, 2),
        (474.0, 660.0, 2),
        (660.0, 698.0, 2),
        (698.0, 723.0, 3),
        (723.0, 811.0, 3),
        (811.0, 986.0, 3),
    ]
    assert len(report["raw_feature_assertions"]) == 12
    assert len(report["motor_road_link_assertions"]) == 2
    assert len(report["pedestrian_road_link_assertions"]) == 3
    assert all(item["derived_transport_role"] == "pedestrian" for item in report["pedestrian_road_link_assertions"])


def test_hh_sib_adapter_does_not_infer_hvs_or_rin_from_raw_class_g() -> None:
    report = read_hamburg_hh_sib_snapshot(
        FIXTURE,
        request_url=REQUEST_URL,
        bbox=(9.978, 53.539, 10.0005, 53.5475),
        target_time=TARGET_TIME,
        retrieved_at=RETRIEVED_AT,
        valid_from=VALID_FROM,
        valid_to=VALID_TO,
    )

    names = {item["property_name"] for item in report["property_assignments"]}
    assert "hh_sib_class_raw" in names
    assert "hamburg_membership" not in names
    assert "rin_category" not in names
    assert "osm_highway" not in names
    assert "sumo_type" not in names
    assert report["source_snapshot"]["server_timestamp"] == "2026-07-19T15:40:31Z"
    assert report["source_snapshot"]["server_timestamp_is_validity"] is False


def test_hh_sib_adapter_retains_rows_without_strassenschluessel_without_false_link_merge() -> None:
    report = read_hamburg_hh_sib_snapshot(
        MISSING_ROAD_KEY_FIXTURE,
        request_url=REQUEST_URL,
        bbox=(9.978, 53.539, 10.0005, 53.5475),
        target_time=TARGET_TIME,
        retrieved_at=RETRIEVED_AT,
        valid_from=VALID_FROM,
        valid_to=VALID_TO,
    )

    assert report["status"] == "review_required"
    assert report["blocking_reasons"] == []
    assert report["counts"]["feature_count"] == 2
    assert report["counts"]["road_link_assertion_count"] == 2
    assert report["counts"]["motor_road_link_assertion_count"] == 2
    assert all("strassenschluessel" not in item["properties"] for item in report["raw_feature_assertions"])

    links = report["road_link_assertions"]
    assert {item["source_ref"]["object_id"] for item in links} == {
        "strassennetz_gesamt:unresolved-road-key:feature-missing-key-41",
        "strassennetz_gesamt:unresolved-road-key:feature-missing-key-42",
    }
    assert all(item["road_key"] is None for item in links)
    assert all(item["road_key_status"] == "missing_in_source" for item in links)
    assert all(item["road_link_identity_status"] == "blocked" for item in links)
    assert all(item["road_link_identity_basis"] == "feature_local_only_missing_strassenschluessel" for item in links)
    assert all(item["status"] == "blocked" for item in links)
    assert all(len(item["feature_refs"]) == 1 for item in links)
    assert "road_key" not in {item["property_name"] for item in report["property_assignments"]}
    identity_reasons = [
        reason for reason in report["review_reasons"] if reason.startswith("road_link_identity_unresolved:")
    ]
    assert len(identity_reasons) == 2


def test_hh_sib_adapter_blocks_duplicate_feature_identity_in_missing_road_key_rows(tmp_path: Path) -> None:
    payload = json.loads(MISSING_ROAD_KEY_FIXTURE.read_text(encoding="utf-8"))
    payload["features"][1]["id"] = payload["features"][0]["id"]
    ambiguous = tmp_path / "duplicate-feature-id.geojson"
    ambiguous.write_text(json.dumps(payload), encoding="utf-8")

    report = read_hamburg_hh_sib_snapshot(
        ambiguous,
        request_url=REQUEST_URL,
        bbox=(9.978, 53.539, 10.0005, 53.5475),
        target_time=TARGET_TIME,
        retrieved_at=RETRIEVED_AT,
        valid_from=VALID_FROM,
        valid_to=VALID_TO,
    )

    assert report["status"] == "blocked"
    assert report["road_link_assertions"] == []
    assert report["blocking_reasons"] == ["duplicate_feature_id:missing-key-41"]


def test_hh_sib_adapter_blocks_historical_target_outside_declared_validity() -> None:
    report = read_hamburg_hh_sib_snapshot(
        FIXTURE,
        request_url=REQUEST_URL,
        bbox=(9.978, 53.539, 10.0005, 53.5475),
        target_time=datetime(2024, 7, 19, tzinfo=UTC),
        retrieved_at=RETRIEVED_AT,
        valid_from=VALID_FROM,
        valid_to=VALID_TO,
    )

    assert report["status"] == "blocked"
    assert report["source_snapshot"]["time_alignment_status"] == "blocked"
    assert "target_time_outside_declared_validity" in report["blocking_reasons"]


def test_hh_sib_adapter_requires_explicit_validity_for_authoritative_use() -> None:
    report = read_hamburg_hh_sib_snapshot(
        FIXTURE,
        request_url=REQUEST_URL,
        bbox=(9.978, 53.539, 10.0005, 53.5475),
        target_time=TARGET_TIME,
        retrieved_at=RETRIEVED_AT,
    )

    assert report["status"] == "review_required"
    assert report["source_snapshot"]["time_alignment_status"] == "review_required"
    assert "source_validity_not_declared" in report["review_reasons"]


def test_hh_sib_adapter_blocks_expected_hash_mismatch() -> None:
    report = read_hamburg_hh_sib_snapshot(
        FIXTURE,
        request_url=REQUEST_URL,
        bbox=(9.978, 53.539, 10.0005, 53.5475),
        target_time=TARGET_TIME,
        retrieved_at=RETRIEVED_AT,
        valid_from=VALID_FROM,
        valid_to=VALID_TO,
        expected_sha256="0" * 64,
    )

    assert report["status"] == "blocked"
    assert report["road_link_assertions"] == []
    assert report["property_assignments"] == []
    assert "source_sha256_mismatch" in report["blocking_reasons"]


def test_hh_sib_adapter_blocks_incomplete_frozen_pagination(tmp_path: Path) -> None:
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    payload["numberReturned"] = 11
    incomplete = tmp_path / "incomplete.geojson"
    incomplete.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    report = read_hamburg_hh_sib_snapshot(
        incomplete,
        request_url=REQUEST_URL,
        bbox=(9.978, 53.539, 10.0005, 53.5475),
        target_time=TARGET_TIME,
        retrieved_at=RETRIEVED_AT,
        valid_from=VALID_FROM,
        valid_to=VALID_TO,
    )

    assert report["acquisition_status"] == "blocked"
    assert "incomplete_or_inconsistent_feature_collection" in report["blocking_reasons"]


def test_hh_sib_adapter_preserves_conflicting_raw_link_values_and_requires_review(
    tmp_path: Path,
) -> None:
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    payload["features"][1]["properties"]["klasse"] = "A"
    conflicting = tmp_path / "conflicting.geojson"
    conflicting.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    report = read_hamburg_hh_sib_snapshot(
        conflicting,
        request_url=REQUEST_URL,
        bbox=(9.978, 53.539, 10.0005, 53.5475),
        target_time=TARGET_TIME,
        retrieved_at=RETRIEVED_AT,
        valid_from=VALID_FROM,
        valid_to=VALID_TO,
    )

    main = next(item for item in report["road_link_assertions"] if item["length_m"] == 986.0)
    assert report["acquisition_status"] == "pass"
    assert report["claim_status"] == "review_required"
    assert main["status"] == "review_required"
    assert main["link_property_conflicts"] == ["klasse"]
    assert any(reason.endswith(":klasse") for reason in report["review_reasons"])
    raw_classes = {
        item["properties"]["klasse"]
        for item in report["raw_feature_assertions"]
        if item["properties"]["abschnittslaenge"] == 986
    }
    assert raw_classes == {"A", "G"}
