"""Check the frozen observation operator without simulation or network access."""

import gzip
import json
from pathlib import Path
from datetime import timedelta

from astra_sumo.core.candidate_contracts import file_sha256
from astra_sumo.core.cached_detector_demand import read_hamburg_count_stream_snapshot
from astra_sumo.core.hamburg_official import parse_hamburg_count_streams, parse_hamburg_count_observations


def test_frozen_ring1_raw_field_compositions_reproduce_published_station_counts():
    base = Path(__file__).parents[1] / "benchmarks/hamburg_ring1_count_window_20261020_v1"
    manifest = json.loads((base / "manifest.json").read_text(encoding="utf-8"))
    for name, record in manifest["files"].items():
        assert file_sha256(base / name) == record["sha256"]
    streams = read_hamburg_count_stream_snapshot(base / "fields.raw.json")
    field_ids = {(str(int(stream.node_id)), stream.asset_id): stream.stream_id for stream in streams}
    responses = json.loads(gzip.decompress((base / "field-observations.json.gz").read_bytes()))
    fields = {
        record["stream_id"]: parse_hamburg_count_observations(
            record["stream_id"], [row for page in record["pages"] for row in page["value"]]
        )
        for record in responses
    }
    assert len(fields) == 13 and sum(map(len, fields.values())) == 390
    index = {(sid, row.begin_utc): row.count for sid, rows in fields.items() for row in rows}
    assert len(index) == 390
    stations = parse_hamburg_count_streams(json.loads((base / "stations.raw.json").read_text(encoding="utf-8")))
    published = json.loads(gzip.decompress((base / "station-observations.json.gz").read_bytes()))
    checked = 0
    for station in stations:
        members = [field_ids[str(int(key.partition("-")[0])), key.partition("-")[2]] for key in station.composition]
        targets = parse_hamburg_count_observations(station.stream_id, published[str(station.stream_id)])
        assert len(targets) == 10
        for target in targets:
            assert target.end_utc - target.begin_utc == timedelta(seconds=900)
            actual = sum(
                index[sid, target.begin_utc + timedelta(seconds=offset)] for sid in members for offset in (0, 300, 600)
            )
            assert actual == target.count
            checked += 1
    assert checked == 40 and manifest["exact_source_composition_bins"] == 40
