from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.request import Request

import pytest

from torii_sumo.core.digital_twin import (
    CanonicalCount,
    CountObservation,
    CountStream,
    aggregate_window_counts,
    parse_mapem,
    parse_phenomenon_interval,
    select_busiest_complete_window,
)
from torii_sumo.core.digital_twin_mapping import (
    DetectorMapping,
    aggregate_canonical_counts_to_edges,
    write_route_sampler_edge_counts,
)
from torii_sumo.core.hamburg_official import (
    SensorThingsClient,
    fetch_hamburg_count_streams,
    fetch_hamburg_count_station_streams,
    parse_hamburg_count_streams,
)


UTC = timezone.utc


def _stream(stream_id: int, node: str = "0228") -> CountStream:
    return CountStream(
        stream_id=stream_id,
        thing_id=stream_id + 100,
        node_id=node,
        asset_id=f"Z.{stream_id}",
        direction="Richtung 1",
        lane_use="Geradeaus",
        longitude=9.98,
        latitude=53.54,
    )


def test_window_selection_requires_every_source_cell_and_keeps_busiest() -> None:
    streams = [_stream(1), _stream(2)]
    day = date(2026, 7, 11)
    day_begin = datetime(2026, 7, 10, 22, tzinfo=UTC)
    observations: dict[int, list[CountObservation]] = {1: [], 2: []}
    for stream in streams:
        for index in range(288):
            begin = day_begin + timedelta(minutes=5 * index)
            value = 100 if 117 <= index < 141 else 1
            observations[stream.stream_id].append(
                CountObservation(stream.stream_id, index, begin, begin + timedelta(minutes=5), value)
            )
    # A high-value but incomplete period must never win.
    observations[2] = [item for item in observations[2] if item.begin_utc != day_begin + timedelta(minutes=30)]

    selection = select_busiest_complete_window(
        streams,
        observations,
        local_date=day,
        timezone_name="Europe/Berlin",
        duration_seconds=7200,
        source_bin_seconds=300,
    )

    assert selection is not None
    assert selection.complete
    assert selection.begin_utc == day_begin + timedelta(minutes=5 * 117)
    assert selection.end_utc - selection.begin_utc == timedelta(hours=2)
    assert selection.expected_cells == 48

    canonical = aggregate_window_counts(streams, observations, selection, output_bin_seconds=900)
    assert len(canonical) == 16
    assert all(row.quality_status == "complete" for row in canonical)
    assert all(row.count == 300 for row in canonical)


def test_parse_hamburg_interval_converts_inclusive_last_second() -> None:
    begin, end = parse_phenomenon_interval("2026-07-11T10:00:00Z/2026-07-11T10:04:59Z")
    assert begin == datetime(2026, 7, 11, 10, 0, tzinfo=UTC)
    assert end == datetime(2026, 7, 11, 10, 5, tzinfo=UTC)


def test_parse_mapem_preserves_lane_connection_and_signal_group(tmp_path: Path) -> None:
    map_file = tmp_path / "map.xml"
    map_file.write_text(
        """<MAPEM><map><intersections><IntersectionGeometry>
        <id><region>3</region><id>228</id></id>
        <refPoint><lat>535444634</lat><long>99833706</long></refPoint>
        <laneSet><GenericLane><laneID>1</laneID><ingressApproach>4</ingressApproach>
        <laneAttributes><laneType><vehicle>00000000</vehicle></laneType></laneAttributes>
        <nodeList><nodes>
          <NodeXY><delta><node-XY5><x>-7000</x><y>-3000</y></node-XY5></delta></NodeXY>
          <NodeXY><delta><node-XY2><x>-1000</x><y>0</y></node-XY2></delta></NodeXY>
        </nodes></nodeList>
        <connectsTo><Connection><connectingLane><lane>8</lane><maneuver>100000000000</maneuver></connectingLane>
        <signalGroup>3</signalGroup><connectionID>1</connectionID></Connection></connectsTo>
        </GenericLane></laneSet></IntersectionGeometry></intersections></map></MAPEM>""",
        encoding="utf-8",
    )

    lanes, connections = parse_mapem(map_file)

    assert lanes[0].node_id == "228"
    assert lanes[0].points_m == ((-70.0, -30.0), (-80.0, -30.0))
    assert lanes[0].is_vehicle and lanes[0].is_ingress
    assert connections[0].connection_id == "1"
    assert connections[0].ingress_lane_id == "1"
    assert connections[0].egress_lane_id == "8"
    assert connections[0].signal_group == "3"


def test_parse_mapem_uses_hamburg_asset_name_when_intersection_id_is_zero(tmp_path: Path) -> None:
    map_file = tmp_path / "hamburg_map.xml"
    map_file.write_text(
        r"""<MAPEM><map><intersections><IntersectionGeometry>
        <name>MAP_ITS_00\5\12.1</name>
        <id><region>0</region><id>0</id></id>
        <refPoint><lat>535588590</lat><long>100258340</long></refPoint>
        <laneSet><GenericLane><laneID>1</laneID><ingressApproach>2</ingressApproach>
        <laneAttributes><laneType><vehicle>00000000</vehicle></laneType></laneAttributes>
        <nodeList><nodes><NodeXY><delta><node-XY5><x>0</x><y>0</y></node-XY5></delta></NodeXY>
        <NodeXY><delta><node-XY5><x>100</x><y>0</y></node-XY5></delta></NodeXY></nodes></nodeList>
        </GenericLane></laneSet></IntersectionGeometry></intersections></map></MAPEM>""",
        encoding="utf-8",
    )

    lanes, _ = parse_mapem(map_file)

    assert lanes[0].node_id == "5"


@pytest.mark.parametrize("vehicle_bits,expected_classes,expected_status", [
    ("10010000", ["bus"], "restricted"),
    ("00001000", ["taxi"], "restricted"),
    ("00000000", None, "unrestricted"),
    ("00000100", None, "review_required"),
    ("00100000", None, "review_required"),
])
def test_mapem_vehicle_restrictions_are_not_lost(tmp_path, vehicle_bits, expected_classes, expected_status):
    source = tmp_path / "map.xml"
    source.write_text('<MAPEM><IntersectionGeometry><id><id>1862</id></id><refPoint><lat>534510000</lat><long>99720000</long></refPoint><laneSet><GenericLane><laneID>26</laneID><ingressApproach>5</ingressApproach><laneAttributes><directionalUse>10</directionalUse><sharedWith>0000100000</sharedWith><laneType><vehicle>' + vehicle_bits + '</vehicle></laneType></laneAttributes></GenericLane></laneSet></IntersectionGeometry></MAPEM>', encoding="utf-8")
    lanes, _ = parse_mapem(source)
    lane = lanes[0]
    assert lane.vehicle_attribute_bits == vehicle_bits
    assert lane.shared_with_bits == "0000100000"
    assert lane.permission_metadata["shared_with_flags"] == ["busVehicleTraffic"]
    assert lane.permission_metadata["allowed_vehicle_classes"] == expected_classes
    assert lane.permission_metadata["permission_status"] == expected_status
    assert lane.permission_metadata["revocable"] == vehicle_bits.startswith("1")
    if expected_classes == ["bus"]:
        assert lane.permission_metadata["vehicle_attribute_flags"] == ["isVehicleRevocableLane", "restrictedToBusUse"]


def test_mapem_rejects_malformed_vehicle_bitstring(tmp_path):
    source = tmp_path / "map.xml"
    source.write_text('<MAPEM><IntersectionGeometry><id><id>7</id></id><refPoint><lat>0</lat><long>0</long></refPoint><laneSet><GenericLane><laneID>1</laneID><laneAttributes><laneType><vehicle>1001x000</vehicle></laneType></laneAttributes></GenericLane></laneSet></IntersectionGeometry></MAPEM>')
    with pytest.raises(ValueError, match="bit string"):
        parse_mapem(source)


def test_sensorthings_client_follows_only_server_pagination() -> None:
    pages = {
        "https://iot.example/v1.1/Things?%24top=1": {
            "value": [{"@iot.id": 1}],
            "@iot.nextLink": "https://iot.example/v1.1/Things?$skip=1",
        },
        "https://iot.example/v1.1/Things?$skip=1": {"value": [{"@iot.id": 2}]},
    }
    seen: list[str] = []

    def transport(request: Request, _timeout: float) -> bytes:
        seen.append(request.full_url)
        return json.dumps(pages[request.full_url]).encode("utf-8")

    client = SensorThingsClient("https://iot.example/v1.1/", transport=transport)
    values, raw_pages, urls = client.collection("Things", params={"$top": 1})

    assert [value["@iot.id"] for value in values] == [1, 2]
    assert len(raw_pages) == 2
    assert urls == seen


def test_count_stream_parser_uses_official_thing_location_and_fields() -> None:
    streams = parse_hamburg_count_streams(
        [
            {
                "@iot.id": 33056,
                "properties": {"knotenName": "0228", "assetID": "Z.10", "fahrspur": "Geradeaus"},
                "Thing": {
                    "@iot.id": 15154,
                    "properties": {"richtung": "Richtung 1", "operationStart": "2026-02-28T23:00:00Z"},
                    "Locations": [
                        {
                            "location": {
                                "type": "Feature",
                                "geometry": {"type": "Point", "coordinates": [9.982568439, 53.544474622]},
                            }
                        }
                    ],
                },
            }
        ]
    )
    assert streams[0].stream_id == 33056
    assert streams[0].node_id == "0228"
    assert streams[0].asset_id == "Z.10"
    assert streams[0].longitude == 9.982568439


def test_count_fetch_maps_unpadded_lsa_id_to_official_count_id() -> None:
    seen: list[str] = []
    page = {
        "value": [
            {
                "@iot.id": 1,
                "properties": {"knotenName": "0104", "assetID": "Z.1", "fahrspur": "Geradeaus"},
                "Thing": {
                    "@iot.id": 2,
                    "properties": {"richtung": "Richtung 1"},
                    "Locations": [
                        {
                            "location": {
                                "type": "Feature",
                                "geometry": {"type": "Point", "coordinates": [9.98, 53.55]},
                            }
                        }
                    ],
                },
            }
        ]
    }

    def transport(request: Request, _timeout: float) -> bytes:
        seen.append(request.full_url)
        return json.dumps(page).encode("utf-8")

    client = SensorThingsClient("https://iot.example/v1.1/", transport=transport)
    streams, _raw = fetch_hamburg_count_streams(client, ["104"])

    assert "knotenName+eq+%270104%27" in seen[0]
    assert [stream.node_id for stream in streams] == ["104"]


def test_count_station_parser_uses_composition_node_and_thing_asset() -> None:
    streams = parse_hamburg_count_streams(
        [
            {
                "@iot.id": 27143,
                "properties": {
                    "assetID": "0408",
                    "layerName": "Anzahl_Kfz_Zaehlstelle_15-Min",
                    "direction": 2,
                    "knotenarm": 7,
                    "zusammensetzung": "2394-Z.9,2394-Z.8,2394-Z.4",
                },
                "Thing": {
                    "@iot.id": 12614,
                    "properties": {"assetID": "0408972", "richtung": "Richtung 2"},
                    "Locations": [
                        {
                            "location": {
                                "type": "Feature",
                                "geometry": {"type": "Point", "coordinates": [9.997405, 53.543923]},
                            }
                        }
                    ],
                },
            }
        ]
    )

    assert streams[0].node_id == "2394"
    assert streams[0].asset_id == "0408972"
    assert streams[0].direction_code == "2"
    assert streams[0].station_arm == "7"
    assert streams[0].composition == ("2394-Z.9", "2394-Z.8", "2394-Z.4")


def test_count_station_parser_rejects_cross_node_composition() -> None:
    with pytest.raises(ValueError, match="spans multiple official nodes"):
        parse_hamburg_count_streams(
            [
                {
                    "@iot.id": 1,
                    "properties": {
                        "direction": 1,
                        "knotenarm": 7,
                        "zusammensetzung": "2394-Z.1,2403-Z.2",
                    },
                    "Thing": {
                        "properties": {"assetID": "station"},
                        "Locations": [
                            {
                                "location": {
                                    "type": "Feature",
                                    "geometry": {"type": "Point", "coordinates": [9.99, 53.54]},
                                }
                            }
                        ],
                    },
                }
            ]
        )


def _station_value(stream_id: int, composition: str) -> dict[str, object]:
    return {
        "@iot.id": stream_id,
        "properties": {
            "layerName": "Anzahl_Kfz_Zaehlstelle_15-Min",
            "direction": 1,
            "knotenarm": 7,
            "zusammensetzung": composition,
        },
        "Thing": {
            "@iot.id": stream_id + 100,
            "properties": {"assetID": f"station-{stream_id}", "richtung": "Richtung 1"},
            "Locations": [
                {
                    "location": {
                        "type": "Feature",
                        "geometry": {"type": "Point", "coordinates": [9.99, 53.54]},
                    }
                }
            ],
        },
    }


def test_station_fetch_isolates_a_proven_off_scope_inventory_error() -> None:
    page = {
        "value": [
            _station_value(1, "2394-Z.1"),
            _station_value(2, "2078-Z.1,0810-Z.2"),
        ]
    }
    client = SensorThingsClient(
        "https://iot.example/v1.1/",
        transport=lambda _request, _timeout: json.dumps(page).encode("utf-8"),
    )

    streams, raw = fetch_hamburg_count_station_streams(client, ["2394"])

    assert [stream.stream_id for stream in streams] == [1]
    assert raw["excluded_off_scope_unsupported_streams"] == [
        {
            "stream_id": 2,
            "composition": "2078-Z.1,0810-Z.2",
            "composition_node_hints": ["0810", "2078"],
            "reason": "count station datastream 2 spans multiple official nodes",
        }
    ]


def test_station_fetch_keeps_target_related_inventory_errors_fail_closed() -> None:
    page = {"value": [_station_value(1, "2394-Z.1,0810-Z.2")]}
    client = SensorThingsClient(
        "https://iot.example/v1.1/",
        transport=lambda _request, _timeout: json.dumps(page).encode("utf-8"),
    )

    with pytest.raises(ValueError, match="spans multiple official nodes"):
        fetch_hamburg_count_station_streams(client, ["2394"])


def test_station_fetch_maps_unpadded_lsa_id() -> None:
    page = {"value": [_station_value(1, "0104-Z.1")]}
    client = SensorThingsClient(
        "https://iot.example/v1.1/",
        transport=lambda _request, _timeout: json.dumps(page).encode("utf-8"),
    )

    streams, _raw = fetch_hamburg_count_station_streams(client, ["104"])

    assert [stream.node_id for stream in streams] == ["104"]


def test_count_station_parser_accepts_official_suffix_field_ids() -> None:
    streams = parse_hamburg_count_streams([_station_value(1, "2394-Z.1_1,2394-Z.1_2")])

    assert streams[0].composition == ("2394-Z.1_1", "2394-Z.1_2")


def test_edge_aggregation_keeps_time_bins_and_uses_count_attribute(tmp_path: Path) -> None:
    rows = [
        CanonicalCount(
            detector_id="d1",
            stream_id=1,
            node_id="0228",
            asset_id="Z.1",
            direction="Richtung 1",
            lane_use="Geradeaus",
            longitude=9.98,
            latitude=53.54,
            source_begin_utc=datetime(2026, 7, 11, 8, tzinfo=UTC),
            source_end_utc=datetime(2026, 7, 11, 8, 15, tzinfo=UTC),
            begin=0,
            end=900,
            count=12,
            source_observation_count=3,
            expected_source_observation_count=3,
            quality_status="complete",
        ),
        CanonicalCount(
            detector_id="d2",
            stream_id=2,
            node_id="0228",
            asset_id="Z.2",
            direction="Richtung 1",
            lane_use="Links",
            longitude=9.98,
            latitude=53.54,
            source_begin_utc=datetime(2026, 7, 11, 8, tzinfo=UTC),
            source_end_utc=datetime(2026, 7, 11, 8, 15, tzinfo=UTC),
            begin=0,
            end=900,
            count=5,
            source_observation_count=3,
            expected_source_observation_count=3,
            quality_status="complete",
        ),
    ]
    mappings = [
        DetectorMapping(
            detector_id=f"d{stream_id}",
            stream_id=stream_id,
            node_id="0228",
            asset_id=f"Z.{stream_id}",
            real_direction="Richtung 1",
            lane_use="",
            longitude=9.98,
            latitude=53.54,
            official_map_lane=str(stream_id),
            official_map_distance_m=1.0,
            sumo_edge="west_in",
            sumo_lane=f"west_in_{stream_id - 1}",
            lane_position=95.0,
            distance_m=1.0,
            heading_error_deg=2.0,
            period=900,
            mapping_confidence="high",
            mapping_status="active",
            mapping_reason="test",
        )
        for stream_id in (1, 2)
    ]

    flows, warnings = aggregate_canonical_counts_to_edges(rows, mappings)
    output = tmp_path / "counts.xml"
    write_route_sampler_edge_counts(output, flows)
    edge = ET.parse(output).getroot().find("interval/edge")

    assert warnings == []
    assert len(flows) == 1 and flows[0].count == 17
    assert edge is not None and edge.attrib["count"] == "17"
    assert "entered" not in edge.attrib
