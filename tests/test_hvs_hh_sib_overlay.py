from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from torii_sumo.road_network.hvs_hh_sib_overlay import (
    HVS_HH_SIB_OVERLAY_SCHEMA,
    build_hamburg_hvs_hh_sib_corridor_overlay,
    write_hamburg_hvs_hh_sib_corridor_overlay,
)


BBOX = "9.978,53.539,10.0005,53.5475"
TIMESTAMP = "2026-07-19T19:54:05Z"


def _feature(
    feature_id: str,
    *,
    road_key: str,
    road_name: str,
    station_from: int,
    station_to: int,
    coordinates: list[list[float]],
    from_node: str = "100",
    to_node: str = "200",
    way_number: str = "7",
    branch: int = 0,
    length: int = 100,
) -> dict[str, Any]:
    return {
        "type": "Feature",
        "id": feature_id,
        "geometry": {"type": "LineString", "coordinates": coordinates},
        "properties": {
            "von_netzknoten": from_node,
            "nach_netzknoten": to_node,
            "von_station": station_from,
            "bis_station": station_to,
            "klasse": "G",
            "strassenschluessel": road_key,
            "wegenummer": way_number,
            "strassenname": road_name,
            "abschnittslaenge": length,
            "ast": branch,
            "fahrstreifenanzahl_in_stationierungsrichtung": 1,
            "fahrstreifenanzahl_in_beide_richtungen": 0,
            "fahrstreifenanzahl_gegen_stationierungsrichtung": 1,
        },
    }


def _collection(features: list[dict[str, Any]], *, source: str) -> dict[str, Any]:
    if source == "hh_sib":
        endpoint = (
            "https://api.hamburg.de/datasets/v1/strassen_und_wegenetz/collections/"
            "strassennetz_gesamt/items"
        )
    else:
        endpoint = (
            "https://api.hamburg.de/datasets/v1/hauptverkehrsstrassen/collections/"
            "hauptverkehrsstrassen/items"
        )
    return {
        "type": "FeatureCollection",
        "numberReturned": len(features),
        "numberMatched": len(features),
        "timeStamp": TIMESTAMP,
        "links": [
            {
                "href": f"{endpoint}?limit=1000&bbox={BBOX}&f=json",
                "rel": "self",
                "type": "application/geo+json",
            }
        ],
        "features": features,
    }


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _manifest(hh_path: Path, features: list[dict[str, Any]]) -> dict[str, Any]:
    selected_links = []
    for feature in features:
        properties = feature["properties"]
        selected_links.append(
            {
                "link_object_id": f"link:{feature['id']}",
                "feature_ids": [feature["id"]],
                "from_network_node": properties["von_netzknoten"],
                "to_network_node": properties["nach_netzknoten"],
                "road_key": properties["strassenschluessel"],
                "road_name": properties["strassenname"],
                "branch_code": properties["ast"],
                "length_m": properties["abschnittslaenge"],
                "selected_station_range_m": [
                    properties["von_station"],
                    properties["bis_station"],
                ],
            }
        )
    return {
        "schema": "test-corridor/v1",
        "source": {
            "kind": "hamburg_hh_sib_official_snapshot",
            "sha256": hashlib.sha256(hh_path.read_bytes()).hexdigest(),
        },
        "selected_links": selected_links,
    }


def _build(
    tmp_path: Path,
    *,
    hh_features: list[dict[str, Any]],
    hvs_features: list[dict[str, Any]],
    hvs_collection: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], tuple[Path, Path, Path]]:
    hh_path = tmp_path / "hh.geojson"
    hvs_path = tmp_path / "hvs.geojson"
    manifest_path = tmp_path / "corridor.manifest.json"
    _write_json(hh_path, _collection(hh_features, source="hh_sib"))
    _write_json(
        hvs_path,
        hvs_collection if hvs_collection is not None else _collection(hvs_features, source="hvs"),
    )
    _write_json(manifest_path, _manifest(hh_path, hh_features))
    report = build_hamburg_hvs_hh_sib_corridor_overlay(
        hh_sib_snapshot_file=hh_path,
        hvs_snapshot_file=hvs_path,
        corridor_manifest_file=manifest_path,
    )
    return report, (hh_path, hvs_path, manifest_path)


def test_exact_official_identity_and_geometry_produce_positive_and_negative_overlay(
    tmp_path: Path,
) -> None:
    hh_hvs = _feature(
        "hh-a",
        road_key="A326",
        road_name="Am Sandtorkai",
        station_from=0,
        station_to=50,
        coordinates=[[9.99, 53.54], [9.991, 53.54]],
    )
    hh_not_hvs = _feature(
        "hh-b",
        road_key="G485",
        road_name="Großer Grasbrook",
        station_from=0,
        station_to=40,
        coordinates=[[9.991, 53.54], [9.991, 53.541]],
        from_node="300",
        to_node="400",
        way_number="8",
        length=40,
    )
    hvs = {**hh_hvs, "id": "hvs-a"}

    report, paths = _build(
        tmp_path,
        hh_features=[hh_hvs, hh_not_hvs],
        hvs_features=[hvs],
    )

    assert report["schema"] == HVS_HH_SIB_OVERLAY_SCHEMA
    assert report["status"] == "pass"
    assert report["decision"] == "exact_overlay_complete"
    assert report["counts"]["hvs_interval_count"] == 1
    assert report["counts"]["not_hvs_interval_count"] == 1
    assert report["matching_contract"]["name_only_match_allowed"] is False
    assert report["matching_contract"]["osm_used"] is False
    assert report["authority_boundary"]["geometry"] == "hh_sib_unchanged"
    assert report["authority_boundary"]["lane_count"] == "hh_sib_unchanged"
    assert report["mutations"] == []
    by_feature = {item["hh_sib_feature_id"]: item for item in report["intervals"]}
    assert by_feature["hh-a"]["membership"] == "hvs"
    assert by_feature["hh-a"]["hvs_feature_ids"] == ["hvs-a"]
    assert by_feature["hh-b"]["membership"] == "not_hvs"
    assert len(report["classification_assignments"]) == 1
    assert all(path.is_file() for path in paths)


def test_same_name_alone_is_not_a_match(tmp_path: Path) -> None:
    hh = _feature(
        "hh-a",
        road_key="A326",
        road_name="Am Sandtorkai",
        station_from=0,
        station_to=50,
        coordinates=[[9.99, 53.54], [9.991, 53.54]],
    )
    unrelated = _feature(
        "hvs-other",
        road_key="Z999",
        road_name="Am Sandtorkai",
        station_from=0,
        station_to=50,
        coordinates=[[9.98, 53.53], [9.981, 53.53]],
        from_node="900",
        to_node="901",
    )

    report, _ = _build(tmp_path, hh_features=[hh], hvs_features=[unrelated])

    assert report["status"] == "pass"
    assert report["intervals"][0]["membership"] == "not_hvs"
    assert report["classification_assignments"] == []


def test_exact_properties_with_different_geometry_abstain_without_human_fallback(
    tmp_path: Path,
) -> None:
    hh = _feature(
        "hh-a",
        road_key="A326",
        road_name="Am Sandtorkai",
        station_from=0,
        station_to=50,
        coordinates=[[9.99, 53.54], [9.991, 53.54]],
    )
    hvs = {**hh, "id": "hvs-a", "geometry": {"type": "LineString", "coordinates": [[9.99, 53.54], [9.992, 53.54]]}}

    report, _ = _build(tmp_path, hh_features=[hh], hvs_features=[hvs])

    assert report["status"] == "blocked"
    assert report["decision"] == "autonomous_abstention"
    assert report["human_review_required"] is False
    assert report["counts"]["abstained_interval_count"] == 1
    assert report["intervals"][0]["membership"] == "unknown"
    assert "exact_identity_geometry_hash_mismatch" in report["blocking_reasons"]


def test_overlapping_but_differently_segmented_hvs_feature_abstains(tmp_path: Path) -> None:
    hh = _feature(
        "hh-a",
        road_key="A326",
        road_name="Am Sandtorkai",
        station_from=0,
        station_to=50,
        coordinates=[[9.99, 53.54], [9.991, 53.54]],
    )
    hvs = _feature(
        "hvs-a",
        road_key="A326",
        road_name="Am Sandtorkai",
        station_from=0,
        station_to=60,
        coordinates=[[9.99, 53.54], [9.9912, 53.54]],
    )

    report, _ = _build(tmp_path, hh_features=[hh], hvs_features=[hvs])

    assert report["status"] == "blocked"
    assert report["intervals"][0]["decision"] == "abstain"
    assert (
        "overlapping_hvs_segment_without_exact_station_identity"
        in report["intervals"][0]["blocking_reasons"]
    )


def test_duplicate_exact_hvs_features_abstain(tmp_path: Path) -> None:
    hh = _feature(
        "hh-a",
        road_key="A326",
        road_name="Am Sandtorkai",
        station_from=0,
        station_to=50,
        coordinates=[[9.99, 53.54], [9.991, 53.54]],
    )
    first = {**hh, "id": "hvs-a"}
    second = {**hh, "id": "hvs-b"}

    report, _ = _build(tmp_path, hh_features=[hh], hvs_features=[first, second])

    assert report["status"] == "blocked"
    assert report["intervals"][0]["hvs_feature_ids"] == ["hvs-a", "hvs-b"]
    assert "duplicate_exact_hvs_identity" in report["blocking_reasons"]


def test_incomplete_hvs_collection_blocks_negative_membership_claim(tmp_path: Path) -> None:
    hh = _feature(
        "hh-a",
        road_key="A326",
        road_name="Am Sandtorkai",
        station_from=0,
        station_to=50,
        coordinates=[[9.99, 53.54], [9.991, 53.54]],
    )
    incomplete = _collection([], source="hvs")
    incomplete["numberMatched"] = 1

    report, _ = _build(
        tmp_path,
        hh_features=[hh],
        hvs_features=[],
        hvs_collection=incomplete,
    )

    assert report["status"] == "blocked"
    assert report["intervals"] == []
    assert "hvs_incomplete_feature_collection" in report["blocking_reasons"]


def test_writer_refuses_to_overwrite_existing_overlay(tmp_path: Path) -> None:
    hh = _feature(
        "hh-a",
        road_key="A326",
        road_name="Am Sandtorkai",
        station_from=0,
        station_to=50,
        coordinates=[[9.99, 53.54], [9.991, 53.54]],
    )
    _, (hh_path, hvs_path, manifest_path) = _build(
        tmp_path,
        hh_features=[hh],
        hvs_features=[{**hh, "id": "hvs-a"}],
    )
    output = tmp_path / "overlay.json"
    kwargs = {
        "hh_sib_snapshot_file": hh_path,
        "hvs_snapshot_file": hvs_path,
        "corridor_manifest_file": manifest_path,
    }

    written = write_hamburg_hvs_hh_sib_corridor_overlay(output, **kwargs)

    assert written["status"] == "pass"
    assert json.loads(output.read_text(encoding="utf-8"))["overlay_id"] == written["overlay_id"]
    with pytest.raises(FileExistsError, match="already exists"):
        write_hamburg_hvs_hh_sib_corridor_overlay(output, **kwargs)
