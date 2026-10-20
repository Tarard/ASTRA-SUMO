"""Check exact offline source joins before using the Ring1 assets for construction."""
import gzip
import hashlib
import json
from pathlib import Path

import pytest

from astra_sumo.core.digital_twin import parse_mapem
from astra_sumo.core.hamburg_map_kml import parse_hamburg_map_kml, bind_hamburg_map_kml_to_mapem


FIXTURE = Path(__file__).parents[1] / "benchmarks/hamburg_ring1_official_sources_20261020_v1"


def test_official_metadata_bytes_and_license_are_retained():
    manifest = json.loads((FIXTURE / "manifest.json").read_text(encoding="utf-8"))
    for name, identity in manifest["metadata_files"].items():
        raw = (FIXTURE / name).read_bytes()
        assert hashlib.sha256(raw).hexdigest() == identity["sha256"]
        assert len(raw) == identity["bytes"]
    catalog = json.loads((FIXTURE / "catalog.json").read_text(encoding="utf-8"))["result"]
    assert manifest["license"] == catalog["license_id"] == "dl-de-by-2.0"


@pytest.mark.parametrize("node,counts", [("104", (47, 33)), ("118", (80, 67)), ("119", (75, 58)),
    ("200", (108, 72)), ("535", (65, 45))])
def test_frozen_official_lane_and_movement_ids_join_exactly(tmp_path, node, counts):
    manifest = json.loads((FIXTURE / "manifest.json").read_text(encoding="utf-8"))
    files = {}
    for row in manifest["files"]:
        if row["node_id"] != node:
            continue
        compressed = (FIXTURE / row["path"]).read_bytes()
        assert hashlib.sha256(compressed).hexdigest() == row["compressed_sha256"]
        assert len(compressed) == row["compressed_bytes"]
        raw = gzip.decompress(compressed)
        assert hashlib.sha256(raw).hexdigest() == row["sha256"]
        assert len(raw) == row["bytes"]
        files[row["kind"]] = tmp_path / row["source_filename"]
        files[row["kind"]].write_bytes(raw)
    assert set(files) == {"map_xml", "map_kml", "ocit_xml"}
    lanes, connections = parse_mapem(files["map_xml"])
    bound = bind_hamburg_map_kml_to_mapem(parse_hamburg_map_kml(files["map_kml"]), lanes, connections, expected_node_id=node)
    assert bound["status"] == "pass"
    assert (len(lanes), len(connections)) == counts
