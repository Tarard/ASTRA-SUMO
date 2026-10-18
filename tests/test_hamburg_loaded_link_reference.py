import gzip
import hashlib
import json
from pathlib import Path
import shutil
from collections import Counter
import xml.etree.ElementTree as ET

import pytest

from astra_sumo.core.network_link_semantics import _compare, compare_loaded_link_semantics


def test_complete_hamburg_references_use_the_adopted_native_stage(tmp_path):
    binary = shutil.which("sumo")
    if not binary:
        pytest.skip("SUMO required")
    folder = Path(__file__).resolve().parents[1] / "benchmarks/hamburg_loaded_links_20261018_v1"
    manifest = json.loads((folder / "fixture.json").read_text(encoding="utf-8"))
    files = {}
    for name, record in manifest["models"].items():
        compressed = (folder / record["path"]).read_bytes()
        assert hashlib.sha256(compressed).hexdigest() == record["sha256"]
        raw = gzip.decompress(compressed)
        assert len(raw) == record["uncompressed_bytes"]
        assert hashlib.sha256(raw).hexdigest() == record["uncompressed_sha256"]
        path = tmp_path / f"{name}.net.xml"
        path.write_bytes(raw)
        files[name] = path
    native = {node: {"path": str(files[name]), "sha256": manifest["models"][name]["uncompressed_sha256"]}
        for node, name in manifest["native_junction_references"].items()}
    result = compare_loaded_link_semantics(source_net_file=files["source"], candidate_net_file=files["candidate"],
        native_net_file=native, mutable_junction_ids=manifest["mutable_junction_ids"],
        boundary_edge_map=manifest["boundary_edge_map"], output_dir=tmp_path / "measured", sumo_binary=binary)
    assert result["status"] == "pass", result
    assert result["compared_junction_counts"] == manifest["expected"]["compared_junction_counts"]
    assert result["differences"] == []
    observed = json.loads(Path(result["observations"]["path"]).read_text())
    wrong = dict(result["native_junction_references"])
    wrong["LSA2403_part0"] = wrong["LSA2349_part0"]
    assert _compare(observed["snapshots"], set(manifest["mutable_junction_ids"]), wrong)["status"] != "pass"


def test_signal_index_swap_is_detected_without_changing_saved_movement_paths(tmp_path):
    binary = shutil.which("sumo")
    if not binary:
        pytest.skip("SUMO required")
    folder = Path(__file__).resolve().parents[1] / "benchmarks/hamburg_loaded_links_20261018_v1"
    source = tmp_path / "source.net.xml"
    source.write_bytes(gzip.decompress((folder / "candidate.net.xml.gz").read_bytes()))
    root = ET.parse(source).getroot()
    connections = root.findall("connection[@tl='77777617']")
    counts = Counter(row.get("linkIndex") for row in connections)
    first, second = [row for row in connections if counts[row.get("linkIndex")] == 1][:2]
    a, b = first.get("linkIndex"), second.get("linkIndex")
    first.set("linkIndex", b)
    second.set("linkIndex", a)
    candidate = tmp_path / "swapped.net.xml"
    ET.ElementTree(root).write(candidate, encoding="utf-8", xml_declaration=True)
    result = compare_loaded_link_semantics(source_net_file=source, candidate_net_file=candidate,
        native_net_file={}, mutable_junction_ids=set(), output_dir=tmp_path / "measured", sumo_binary=binary)
    assert result["status"] == "fail"
    assert {row["junction_id"] for row in result["differences"]} == {"77777617"}
    change = result["differences"][0]
    def without_controls(node):
        return {**node, "lane_links": {lane: [{key: value for key, value in row.items() if key != "controls"} for row in links]
            for lane, links in node["lane_links"].items()}}
    assert without_controls(change["expected"]) == without_controls(change["actual"])
