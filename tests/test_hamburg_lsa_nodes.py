from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from urllib.request import Request

import pytest

from torii_sumo.core.hamburg_lsa_nodes import (
    AUTONOMOUS_ABSTENTION_ACTION,
    HAMBURG_LSA_NODE_PROPERTY_KEYS,
    GeographicBBox,
    HamburgLsaNodeRequest,
    build_hamburg_lsa_node_items_url,
    freeze_hamburg_lsa_node_evidence,
    load_frozen_hamburg_lsa_node_snapshot,
    normalize_road_name_components,
    parse_hamburg_lsa_node_feature_collection,
    select_hamburg_lsa_node,
    validate_hamburg_lsa_node_items_url,
)


BBOX = GeographicBBox(9.978, 53.539, 10.0005, 53.5475)


def _feature(
    feature_id: int,
    node_id: int,
    name: str,
    longitude: float,
    latitude: float,
) -> dict[str, object]:
    return {
        "type": "Feature",
        "id": feature_id,
        "geometry": {
            "type": "MultiPoint",
            "coordinates": [[longitude, latitude]],
        },
        "properties": {
            "knoten": node_id,
            "art": "K-LSA     ",
            "LSA_Name": name,
        },
    }


def _payload(*, features: list[dict[str, object]] | None = None) -> dict[str, object]:
    request_url = build_hamburg_lsa_node_items_url(BBOX)
    items = features or [
        _feature(
            17251,
            2349,
            "Am Sandtorkai/Großer Grasbrook          ",
            9.993227061979422,
            53.543320797153804,
        ),
        _feature(
            17277,
            2394,
            "Am Sandtorkai/Am Sandtorpark            ",
            9.995132872689311,
            53.54350271129165,
        ),
        _feature(
            17285,
            2403,
            "Am Sandtorkai/Osakaallee",
            9.997839159986002,
            53.54409500839008,
        ),
    ]
    return {
        "type": "FeatureCollection",
        "numberReturned": len(items),
        "numberMatched": len(items),
        "timeStamp": "2026-07-19T21:01:52Z",
        "features": items,
        "links": [
            {
                "href": request_url,
                "rel": "self",
                "type": "application/geo+json",
                "title": "Dieses Dokument",
            },
            {
                "href": request_url.replace("f=json", "f=html"),
                "rel": "alternate",
                "type": "text/html",
                "title": "Dieses Dokument als HTML",
            },
        ],
    }


def _raw(payload: dict[str, object] | None = None) -> bytes:
    return json.dumps(payload or _payload(), ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def _request(node_id: int, *roads: str) -> HamburgLsaNodeRequest:
    return HamburgLsaNodeRequest.create(
        expected_node_id=node_id,
        road_name_components=roads,
    )


def test_official_items_url_is_canonical_and_validated() -> None:
    url = build_hamburg_lsa_node_items_url("9.978,53.539,10.0005,53.5475")
    parsed = urlparse(url)
    query = parse_qs(parsed.query)

    assert parsed.scheme == "https"
    assert parsed.hostname == "api.hamburg.de"
    assert parsed.path.endswith("/lichtsignalanlagen/collections/lsa_knotengrunddaten/items")
    assert query == {
        "f": ["json"],
        "limit": ["1000"],
        "bbox": ["9.978,53.539,10.0005,53.5475"],
    }
    assert validate_hamburg_lsa_node_items_url(url) == BBOX


@pytest.mark.parametrize(
    "url",
    [
        "http://api.hamburg.de/datasets/v1/lichtsignalanlagen/collections/"
        "lsa_knotengrunddaten/items?f=json&limit=1000&bbox=9.978,53.539,10.0005,53.5475",
        "https://example.org/datasets/v1/lichtsignalanlagen/collections/"
        "lsa_knotengrunddaten/items?f=json&limit=1000&bbox=9.978,53.539,10.0005,53.5475",
        "https://api.hamburg.de/datasets/v1/lichtsignalanlagen/collections/"
        "other/items?f=json&limit=1000&bbox=9.978,53.539,10.0005,53.5475",
        "https://api.hamburg.de/datasets/v1/lichtsignalanlagen/collections/"
        "lsa_knotengrunddaten/items?f=json&limit=1000&bbox=9.978,53.539,10.0005,53.5475&x=1",
    ],
)
def test_official_items_url_rejects_non_exact_source(url: str) -> None:
    with pytest.raises(ValueError, match="official HTTPS|exactly one"):
        validate_hamburg_lsa_node_items_url(url)


def test_parser_exposes_exact_source_schema_ids_names_and_point_geometry() -> None:
    raw = _raw()
    request_url = build_hamburg_lsa_node_items_url(BBOX)
    snapshot = parse_hamburg_lsa_node_feature_collection(
        raw,
        request_url=request_url,
        expected_sha256=hashlib.sha256(raw).hexdigest().upper(),
    )

    assert snapshot.raw_sha256 == hashlib.sha256(raw).hexdigest()
    assert snapshot.number_returned == 3
    assert [node.node_id for node in snapshot.nodes] == ["2349", "2394", "2403"]
    assert [node.lsanr for node in snapshot.nodes] == ["2349", "2394", "2403"]
    node_2403 = snapshot.node_by_id(2403)
    assert node_2403 is not None
    assert node_2403.feature_id == 17285
    assert node_2403.official_name == "Am Sandtorkai/Osakaallee"
    assert node_2403.normalized_road_name_components == ("am sandtorkai", "osakaallee")
    assert node_2403.longitude == pytest.approx(9.997839159986002)
    assert node_2403.latitude == pytest.approx(53.54409500839008)
    assert tuple(node_2403.to_dict()["source_properties"]) == HAMBURG_LSA_NODE_PROPERTY_KEYS
    assert node_2403.to_dict()["point_geometry"] == {
        "type": "Point",
        "coordinates": [9.997839159986002, 53.54409500839008],
    }


def test_parser_rejects_hash_or_published_property_schema_change() -> None:
    raw = _raw()
    request_url = build_hamburg_lsa_node_items_url(BBOX)
    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        parse_hamburg_lsa_node_feature_collection(
            raw,
            request_url=request_url,
            expected_sha256="0" * 64,
        )

    changed = _payload()
    properties = changed["features"][0]["properties"]  # type: ignore[index]
    properties["unexpected"] = "silent schema drift"  # type: ignore[index]
    with pytest.raises(ValueError, match="published schema"):
        parse_hamburg_lsa_node_feature_collection(
            _raw(changed),
            request_url=request_url,
        )

    non_utc = _payload()
    non_utc["timeStamp"] = "2026-07-19T23:01:52+02:00"
    with pytest.raises(ValueError, match="must use UTC"):
        parse_hamburg_lsa_node_feature_collection(
            _raw(non_utc),
            request_url=request_url,
        )


def test_unique_exact_normalized_name_and_expected_id_is_selected() -> None:
    snapshot = parse_hamburg_lsa_node_feature_collection(
        _raw(),
        request_url=build_hamburg_lsa_node_items_url(BBOX),
    )
    selection = select_hamburg_lsa_node(
        snapshot,
        _request(2349, "  GROSSER   GRASBROOK ", "Am Sandtorkai"),
    )

    assert normalize_road_name_components(("Großer Grasbrook", "AM SANDTORKAI")) == (
        "am sandtorkai",
        "grosser grasbrook",
    )
    assert selection.decision == "pass"
    assert selection.reason == "unique_bbox_name_and_node_id_match"
    assert selection.selected_node is not None
    assert selection.selected_node.node_id == "2349"
    assert selection.human_action_required is False
    assert selection.automation_action == "use_frozen_official_node_identity"


@pytest.mark.parametrize(
    ("node_request", "reason"),
    [
        (
            _request(9999, "Am Sandtorkai", "Missing Road"),
            "no_exact_normalized_road_component_match",
        ),
        (
            _request(9999, "Am Sandtorkai", "Osakaallee"),
            "exact_name_match_has_unexpected_official_node_id",
        ),
    ],
)
def test_missing_or_wrong_node_id_abstains_without_human_action(
    node_request: HamburgLsaNodeRequest,
    reason: str,
) -> None:
    snapshot = parse_hamburg_lsa_node_feature_collection(
        _raw(),
        request_url=build_hamburg_lsa_node_items_url(BBOX),
    )

    selection = select_hamburg_lsa_node(snapshot, node_request)

    assert selection.decision == "review_required"
    assert selection.reason == reason
    assert selection.selected_node is None
    assert selection.human_action_required is False
    assert selection.automation_action == AUTONOMOUS_ABSTENTION_ACTION


def test_ambiguous_exact_name_abstains_without_using_expected_id_to_break_tie() -> None:
    payload = _payload()
    payload["features"].append(  # type: ignore[union-attr]
        _feature(
            99999,
            9999,
            "Osakaallee/Am Sandtorkai",
            9.9979,
            53.5441,
        )
    )
    payload["numberReturned"] = 4
    payload["numberMatched"] = 4
    snapshot = parse_hamburg_lsa_node_feature_collection(
        _raw(payload),
        request_url=build_hamburg_lsa_node_items_url(BBOX),
    )

    selection = select_hamburg_lsa_node(
        snapshot,
        _request(2403, "Am Sandtorkai", "Osakaallee"),
    )

    assert selection.decision == "review_required"
    assert selection.reason == "ambiguous_exact_normalized_road_component_match"
    assert selection.matched_node_ids == ("2403", "9999")
    assert selection.selected_node is None
    assert selection.human_action_required is False
    assert selection.automation_action == AUTONOMOUS_ABSTENTION_ACTION


def test_parser_rejects_out_of_bbox_or_non_single_point_geometry() -> None:
    outside = _payload(features=[_feature(1, 1, "A/B", 10.1, 53.54)])
    with pytest.raises(ValueError, match="outside the request bbox"):
        parse_hamburg_lsa_node_feature_collection(
            _raw(outside),
            request_url=build_hamburg_lsa_node_items_url(BBOX),
        )

    multiple = _payload(features=[_feature(1, 1, "A/B", 9.99, 53.54)])
    multiple["features"][0]["geometry"]["coordinates"].append([9.991, 53.541])  # type: ignore[index]
    with pytest.raises(ValueError, match="exactly one point"):
        parse_hamburg_lsa_node_feature_collection(
            _raw(multiple),
            request_url=build_hamburg_lsa_node_items_url(BBOX),
        )


def test_freezer_persists_hash_bound_raw_response_and_three_exact_selections(
    tmp_path: Path,
) -> None:
    raw = _raw()
    seen_requests: list[Request] = []

    def transport(request: Request, timeout_seconds: float) -> bytes:
        assert timeout_seconds == 12.0
        assert request.get_header("Accept") == "application/geo+json,application/json"
        seen_requests.append(request)
        return raw

    manifest = freeze_hamburg_lsa_node_evidence(
        tmp_path,
        bbox=BBOX,
        requests=(
            _request(2349, "Am Sandtorkai", "Großer Grasbrook"),
            _request(2394, "Am Sandtorpark", "Am Sandtorkai"),
            _request(2403, "Osakaallee", "Am Sandtorkai"),
        ),
        timeout_seconds=12.0,
        transport=transport,
        expected_sha256=hashlib.sha256(raw).hexdigest(),
    )

    assert len(seen_requests) == 1
    assert manifest["decision"] == "pass"
    assert manifest["human_action_required"] is False
    assert manifest["source"]["raw_sha256"] == hashlib.sha256(raw).hexdigest()
    assert manifest["claim_boundary"]["proves"] == [
        "official_lsa_node_identity",
        "official_lsa_name",
        "official_lsa_point_geometry",
    ]
    assert "tld_datastream_availability_or_node_binding" in manifest["claim_boundary"]["does_not_prove"]
    assert [item["selected_node"]["node_id"] for item in manifest["selections"]] == [
        "2349",
        "2394",
        "2403",
    ]
    raw_path = Path(manifest["raw_path"])
    manifest_path = Path(manifest["manifest_path"])
    assert raw_path.name == f"lsa_knotengrunddaten.{hashlib.sha256(raw).hexdigest()[:12]}.geojson"
    assert raw_path.read_bytes() == raw
    assert manifest_path.is_file()
    frozen = load_frozen_hamburg_lsa_node_snapshot(
        raw_path,
        request_url=manifest["source"]["request_url"],
        expected_sha256=manifest["source"]["raw_sha256"],
    )
    assert frozen.node_by_id("2394").official_name == "Am Sandtorkai/Am Sandtorpark"  # type: ignore[union-attr]


def test_freezer_persists_autonomous_abstention_for_missing_identity(tmp_path: Path) -> None:
    raw = _raw()

    manifest = freeze_hamburg_lsa_node_evidence(
        tmp_path,
        bbox=BBOX,
        requests=(_request(9999, "Am Sandtorkai", "Missing Road"),),
        transport=lambda _request_value, _timeout: raw,
    )

    assert manifest["decision"] == "review_required"
    assert manifest["human_action_required"] is False
    assert manifest["automation_action"] == AUTONOMOUS_ABSTENTION_ACTION


def test_duplicate_official_node_id_is_rejected_as_source_integrity_failure() -> None:
    payload = _payload()
    duplicate = copy.deepcopy(payload["features"][0])  # type: ignore[index]
    duplicate["id"] = 99999
    payload["features"].append(duplicate)  # type: ignore[union-attr]
    payload["numberReturned"] = 4
    payload["numberMatched"] = 4

    with pytest.raises(ValueError, match="duplicate official node ids"):
        parse_hamburg_lsa_node_feature_collection(
            _raw(payload),
            request_url=build_hamburg_lsa_node_items_url(BBOX),
        )
