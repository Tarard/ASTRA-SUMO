from __future__ import annotations

import json
from pathlib import Path

import pytest

from torii_sumo.core.cached_detector_demand import (
    read_canonical_count_file,
    read_hamburg_count_stream_snapshot,
    read_map_lane_bindings,
    read_signal_stream_snapshot,
)


def test_read_hamburg_count_stream_snapshot_reuses_official_parser(tmp_path: Path) -> None:
    path = tmp_path / "streams.json"
    path.write_text(
        json.dumps(
            {
                "pages_by_node": {
                    "0228": [
                        {
                            "value": [
                                {
                                    "@iot.id": 33056,
                                    "properties": {
                                        "knotenName": "0228",
                                        "assetID": "Z.10",
                                        "fahrspur": "Geradeaus",
                                    },
                                    "Thing": {
                                        "@iot.id": 1,
                                        "properties": {
                                            "assetID": "Z.10",
                                            "richtung": "Richtung 1",
                                            "operationStart": "2020-01-01",
                                        },
                                        "Locations": [
                                            {
                                                    "location": {
                                                        "type": "Feature",
                                                        "geometry": {
                                                        "type": "Point",
                                                        "coordinates": [9.98, 53.54],
                                                    }
                                                }
                                            }
                                        ],
                                    },
                                }
                            ]
                        }
                    ]
                }
            }
        ),
        encoding="utf-8",
    )

    streams = read_hamburg_count_stream_snapshot(path)

    assert len(streams) == 1
    assert streams[0].detector_id == "hh_0228_Z_10_33056"
    assert streams[0].longitude == 9.98


def test_read_canonical_count_file_preserves_relative_and_absolute_time(tmp_path: Path) -> None:
    path = tmp_path / "counts.csv"
    path.write_text(
        "detector_id,stream_id,node_id,asset_id,direction,lane_use,longitude,latitude,"
        "source_begin_utc,source_end_utc,begin,end,interval_seconds,expected_total,"
        "source_observation_count,expected_source_observation_count,quality_status\n"
        "hh_0228_Z_10_33056,33056,0228,Z.10,Richtung 1,Geradeaus,9.98,53.54,"
        "2026-07-11T13:45:00Z,2026-07-11T14:00:00Z,0,900,900,70,3,3,complete\n",
        encoding="utf-8",
    )

    rows = read_canonical_count_file(path)

    assert len(rows) == 1
    assert rows[0].begin == 0
    assert rows[0].end == 900
    assert rows[0].count == 70
    assert rows[0].source_begin_utc.isoformat() == "2026-07-11T13:45:00+00:00"


def test_read_map_lane_bindings_rejects_leading_zero_node_collision(tmp_path: Path) -> None:
    path = tmp_path / "bindings.csv"
    header = (
        "node_id,map_lane_id,map_lane_type,map_role,sumo_edge,sumo_lane,lane_position,"
        "distance_m,heading_error_deg,mapping_confidence,mapping_status\n"
    )
    path.write_text(
        header
        + "0228,1,vehicle,ingress,a,a_0,1,0.1,0,high,active\n"
        + "228,1,vehicle,ingress,b,b_0,1,0.1,0,high,active\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="repeats normalized key"):
        read_map_lane_bindings(path)


def test_read_signal_stream_snapshot_rejects_duplicate_ids(tmp_path: Path) -> None:
    path = tmp_path / "signal-streams.csv"
    path.write_text(
        "stream_id,thing_id,node_id,connection_id,ingress_lane_id,egress_lane_id,lane_type,"
        "signal_group,layer_name,name\n"
        "1,,228,1,1,8,KFZ,K1,primary_signal,one\n"
        "1,,228,2,2,7,KFZ,K2,primary_signal,two\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="repeats stream_id"):
        read_signal_stream_snapshot(path)
