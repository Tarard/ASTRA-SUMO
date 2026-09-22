from __future__ import annotations

import copy
import hashlib
import json
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

import pytest

from torii_sumo.road_network.adapters.hamburg_hh_sib import read_hamburg_hh_sib_snapshot
from torii_sumo.road_network.official_plainxml import (
    OFFICIAL_CORRIDOR_SCOPE_SCHEMA,
    OfficialPlainXmlError,
    materialize_hamburg_hh_sib_plainxml_candidate,
    materialize_hamburg_hh_sib_snapshot_plainxml_candidate,
)


REQUEST_URL = (
    "https://api.hamburg.de/datasets/v1/strassen_und_wegenetz/"
    "collections/strassennetz_gesamt/items?bbox=9.98,53.54,10.00,53.55&f=json&limit=100"
)
TARGET_TIME = datetime(2026, 7, 19, 10, 0, tzinfo=timezone.utc)
VALID_FROM = datetime(2026, 7, 1, tzinfo=timezone.utc)
VALID_TO = datetime(2026, 8, 1, tzinfo=timezone.utc)


def _feature(
    feature_id: int,
    *,
    station_from: int,
    station_to: int,
    coordinates: list[list[float]],
    lanes_with: int,
    lanes_against: int,
    lanes_both: int = 0,
    speed: str = "50 km/h",
) -> dict[str, object]:
    return {
        "type": "Feature",
        "id": feature_id,
        "geometry": {"type": "LineString", "coordinates": coordinates},
        "properties": {
            "von_netzknoten": "node-west",
            "nach_netzknoten": "node-east",
            "von_station": station_from,
            "bis_station": station_to,
            "klasse": "G",
            "strassenschluessel": "A326",
            "wegenummer": "295",
            "strassenname": "Am Sandtorkai",
            "landesschluessel": 2,
            "kreisschluessel": 1,
            "gemeindeschluessel": 103,
            "abschnittslaenge": 200,
            "ast": 0,
            "fahrstreifenanzahl_in_stationierungsrichtung": lanes_with,
            "fahrstreifenanzahl_in_beide_richtungen": lanes_both,
            "fahrstreifenanzahl_gegen_stationierungsrichtung": lanes_against,
            "bahnigkeit": 2,
            "geschwindigkeit": speed,
            "wegeart": "Stadtstraße",
        },
    }


def _snapshot_payload() -> dict[str, object]:
    # HH-SIB geometry in this fixture is deliberately ordered against
    # stationing.  Ordered interval continuity is enough to infer the reversal.
    return {
        "type": "FeatureCollection",
        "numberMatched": 2,
        "numberReturned": 2,
        "timeStamp": "2026-07-19T10:01:00Z",
        "features": [
            _feature(
                1001,
                station_from=0,
                station_to=100,
                coordinates=[[9.9815, 53.542], [9.98, 53.542]],
                lanes_with=2,
                lanes_against=1,
            ),
            _feature(
                1002,
                station_from=100,
                station_to=200,
                coordinates=[[9.983, 53.542], [9.9815, 53.542]],
                lanes_with=3,
                lanes_against=2,
            ),
        ],
    }


def _write_snapshot(path: Path, payload: dict[str, object] | None = None) -> str:
    content = json.dumps(payload or _snapshot_payload(), ensure_ascii=False, sort_keys=True)
    path.write_text(content, encoding="utf-8")
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _read_report(snapshot: Path) -> dict[str, object]:
    return read_hamburg_hh_sib_snapshot(
        snapshot,
        request_url=REQUEST_URL,
        bbox=(9.98, 53.54, 10.0, 53.55),
        target_time=TARGET_TIME,
        retrieved_at=datetime(2026, 7, 19, 10, 1, tzinfo=timezone.utc),
        valid_from=VALID_FROM,
        valid_to=VALID_TO,
    )


def _scope(*, geometry_order: str = "auto", station_from: int = 0, station_to: int = 200) -> dict[str, object]:
    return {
        "schema": OFFICIAL_CORRIDOR_SCOPE_SCHEMA,
        "scope_id": "am-sandtorkai-two-interval-test",
        "links": [
            {
                "road_key": "A326",
                "from_network_node": "node-west",
                "to_network_node": "node-east",
                "branch_code": 0,
                "station_from_m": station_from,
                "station_to_m": station_to,
                "geometry_order": geometry_order,
            }
        ],
    }


def test_materializes_official_directed_intervals_without_osm_or_connections(tmp_path: Path) -> None:
    snapshot = tmp_path / "hh_sib.geojson"
    source_sha256 = _write_snapshot(snapshot)
    report = _read_report(snapshot)
    assert report["claim_status"] == "pass"

    result = materialize_hamburg_hh_sib_plainxml_candidate(
        hh_sib_source=report,
        corridor_scope=_scope(),
        output_dir=tmp_path / "candidate",
    )

    assert result["status"] == "blocked"
    assert result["human_map_review_required"] is False
    assert result["source"]["sha256"] == source_sha256
    assert result["gates"]["official_source_time_alignment"] == "pass"
    assert result["gates"]["official_interval_geometry"] == "pass"
    assert result["gates"]["official_map_lane_connections"] == "blocked"
    assert result["gates"]["netconvert_execution"] == "not_run"
    assert "OpenStreetMap" in result["excluded_inputs"]
    assert result["counts"] == {
        "selected_link_count": 1,
        "official_interval_count": 2,
        "node_count": 3,
        "directed_edge_count": 4,
    }

    candidate = Path(result["output_dir"])
    nodes = ET.parse(candidate / "hh_sib_official_corridor.nod.xml").getroot()
    edges = ET.parse(candidate / "hh_sib_official_corridor.edg.xml").getroot()
    config = ET.parse(candidate / "hh_sib_official_corridor.netccfg").getroot()
    assert len(nodes.findall("node")) == 3
    assert config.find(".//connection-files") is None
    projection = config.find("./projection")
    assert projection is not None
    assert projection.find("./proj").get("value") == (
        "+proj=utm +zone=32 +datum=WGS84 +units=m +no_defs"
    )
    assert projection.find("./proj.inverse").get("value") == "true"
    assert not (candidate / "hh_sib_official_corridor.net.xml").exists()

    edge_rows = edges.findall("edge")
    assert len(edge_rows) == 4
    assert {edge.get("spreadType") for edge in edge_rows} == {"right"}
    assert sorted(int(edge.get("numLanes", "0")) for edge in edge_rows) == [1, 2, 2, 3]
    with_edges = [edge for edge in edge_rows if edge.get("id", "").endswith(".with")]
    against_edges = [edge for edge in edge_rows if edge.get("id", "").endswith(".against")]
    assert [int(edge.get("numLanes", "0")) for edge in with_edges] == [2, 3]
    assert [int(edge.get("numLanes", "0")) for edge in against_edges] == [1, 2]
    assert all(edge.find("param[@key='origId']") is not None for edge in edge_rows)
    assert all(edge.find("param[@key='torii:connection_status']").get("value") == "unresolved_official_map_stage" for edge in edge_rows)

    orientation = result["selected_links"][0]["orientation"]
    assert orientation["basis"] == "minimum_gap_across_ordered_official_station_intervals"
    assert orientation["reverse_by_interval"] == [True, True]
    assert max(orientation["join_gaps_m"]) < 0.01


def test_named_scope_contract_is_recorded_when_supplied(tmp_path: Path) -> None:
    snapshot = tmp_path / "hh_sib.geojson"
    _write_snapshot(snapshot)
    report = _read_report(snapshot)
    scope_manifest = tmp_path / "named-scope.json"
    scope_manifest.write_text(
        json.dumps(
                {
                    "schema": "torii.hamburg-named-corridor-scope/v1",
                    "scope_id": "hamburg_sandtorkai_2349_2394_2403_named_entries_v1",
                    "decision": "blocked",
                    "nodes": [{"node_id": node_id} for node_id in ("2349", "2394", "2403")],
                "official_road_scope": {
                    "scope_id": "hamburg_sandtorkai_2349_2394_2403_named_entries_v1"
                },
                "signal_assets": {"decision": "blocked"},
                "sources": {},
            }
        ),
        encoding="utf-8",
    )

    result = materialize_hamburg_hh_sib_plainxml_candidate(
        hh_sib_source=report,
        corridor_scope=_scope(),
        output_dir=tmp_path / "candidate",
        named_scope_manifest_file=scope_manifest,
    )

    assert result["gates"]["named_scope_contract"] == "pass"
    assert result["named_scope_contract"]["scope_id"] == (
        "hamburg_sandtorkai_2349_2394_2403_named_entries_v1"
    )


def test_plainxml_and_candidate_identity_are_deterministic_across_directories(tmp_path: Path) -> None:
    snapshot = tmp_path / "hh_sib.geojson"
    _write_snapshot(snapshot)
    report = _read_report(snapshot)

    first = materialize_hamburg_hh_sib_plainxml_candidate(
        hh_sib_source=report,
        corridor_scope=_scope(),
        output_dir=tmp_path / "candidate-a",
    )
    second = materialize_hamburg_hh_sib_plainxml_candidate(
        hh_sib_source=report,
        corridor_scope=_scope(),
        output_dir=tmp_path / "candidate-b",
    )

    assert first["candidate_id"] == second["candidate_id"]
    assert first["source"]["portable_parsed_report_sha256"] == second["source"]["portable_parsed_report_sha256"]
    assert first["scope"]["sha256"] == second["scope"]["sha256"]
    assert first["artifacts"] == second["artifacts"]


def test_frozen_snapshot_entry_accepts_explicit_single_interval_orientation(tmp_path: Path) -> None:
    source = Path("tests/fixtures/road_network/hh_sib_sample.geojson").resolve()
    expected_sha256 = hashlib.sha256(source.read_bytes()).hexdigest()
    output = tmp_path / "snapshot-candidate"
    scope = {
        "schema": OFFICIAL_CORRIDOR_SCOPE_SCHEMA,
        "scope_id": "single-official-link",
        "links": [
            {
                "road_key": "A326",
                "from_network_node": "official-west",
                "to_network_node": "official-east",
                "branch_code": 0,
                "station_from_m": 0,
                "station_to_m": 132,
                "geometry_order": "source_order_is_with_stationing",
            }
        ],
    }

    result = materialize_hamburg_hh_sib_snapshot_plainxml_candidate(
        snapshot_file=source,
        request_url=REQUEST_URL,
        bbox=(9.98, 53.54, 10.0, 53.55),
        target_time=TARGET_TIME,
        retrieved_at=datetime(2026, 7, 19, 10, 1, tzinfo=timezone.utc),
        valid_from=VALID_FROM,
        valid_to=VALID_TO,
        expected_sha256=expected_sha256,
        corridor_scope=scope,
        output_dir=output,
        prefix="single",
    )

    assert result["source"]["sha256"] == expected_sha256
    assert result["counts"]["directed_edge_count"] == 2
    assert result["selected_links"][0]["orientation"]["basis"] == "explicit_corridor_scope"
    assert ET.parse(output / "single.edg.xml").getroot().findall("edge")


def test_auto_orientation_rejects_single_interval_without_evidence(tmp_path: Path) -> None:
    payload = _snapshot_payload()
    payload["features"] = [payload["features"][0]]
    payload["numberMatched"] = 1
    payload["numberReturned"] = 1
    feature = payload["features"][0]
    feature["properties"]["bis_station"] = 200
    snapshot = tmp_path / "single.geojson"
    _write_snapshot(snapshot, payload)
    report = _read_report(snapshot)

    with pytest.raises(OfficialPlainXmlError, match="one-interval link"):
        materialize_hamburg_hh_sib_plainxml_candidate(
            hh_sib_source=report,
            corridor_scope=_scope(),
            output_dir=tmp_path / "candidate",
        )


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda payload: payload["features"][0]["properties"].__setitem__("geschwindigkeit", "30 km/h (Mo-Fr)"), "speed is missing or conditional"),
        (lambda payload: payload["features"][0]["properties"].__setitem__("fahrstreifenanzahl_in_beide_richtungen", 1), "shared both-direction lanes"),
    ],
)
def test_unresolved_official_lane_or_speed_semantics_fail_before_writing(
    tmp_path: Path,
    mutation: object,
    message: str,
) -> None:
    payload = copy.deepcopy(_snapshot_payload())
    mutation(payload)
    snapshot = tmp_path / "invalid.geojson"
    _write_snapshot(snapshot, payload)
    report = _read_report(snapshot)

    with pytest.raises(OfficialPlainXmlError, match=message):
        materialize_hamburg_hh_sib_plainxml_candidate(
            hh_sib_source=report,
            corridor_scope=_scope(),
            output_dir=tmp_path / "candidate",
        )
    assert not (tmp_path / "candidate").exists()


def test_partial_station_cut_must_follow_official_feature_boundaries(tmp_path: Path) -> None:
    snapshot = tmp_path / "hh_sib.geojson"
    _write_snapshot(snapshot)
    report = _read_report(snapshot)

    with pytest.raises(OfficialPlainXmlError, match="align exactly"):
        materialize_hamburg_hh_sib_plainxml_candidate(
            hh_sib_source=report,
            corridor_scope=_scope(station_from=25, station_to=200),
            output_dir=tmp_path / "candidate",
        )
