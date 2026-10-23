import gzip
import hashlib
import json
from pathlib import Path
import subprocess
import xml.etree.ElementTree as ET

import pytest

from astra_sumo.core.junction_split_fanout import materialize_unsplit_fanout


REPO = Path(__file__).resolve().parents[1]
CASE = REPO / "benchmarks/hamburg_ring1_od_spsa_completed_20261023_v3/evaluations/final"
KEYS = [("139083723#0", 2, "234423652#1", 0), ("139083723#0", 2, "234423652#1", 1)]


def _source(tmp_path):
    source = tmp_path / "source.net.xml"
    source.write_bytes(gzip.decompress((CASE / "network.net.xml.gz").read_bytes()))
    return source


def _run(tmp_path, network, binary):
    tmp_path.mkdir()
    control = tmp_path / "control.add.xml"
    control.write_bytes(gzip.decompress((CASE / "signal-programs.add.xml.gz").read_bytes()))
    routes = tmp_path / "demand.rou.xml"
    routes.write_bytes((REPO / "benchmarks/hamburg_ring1_104_split_fanout_20261023_v1/cohort.rou.xml").read_bytes())
    result = subprocess.run([str(binary), "-n", str(network), "-r", str(routes), "-a", str(control),
        "--step-length", "0.1", "--seed", "104", "--end", "300", "--collision.check-junctions", "true",
        "--collision-output", str(tmp_path / "collisions.xml"), "--summary-output", str(tmp_path / "summary.xml"),
        "--no-step-log", "true"], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    return ET.parse(tmp_path / "collisions.xml").getroot(), ET.parse(tmp_path / "summary.xml").getroot().findall("step")[-1]


def test_native_hamburg_split_sibling_continuations_reproduce_then_clear_without_road_changes(tmp_path):
    sumo = pytest.importorskip("sumo")
    binaries = Path(sumo.__file__).parent / "bin"
    source = _source(tmp_path)
    original = source.read_bytes()
    collisions, _ = _run(tmp_path / "before", source, binaries / "sumo.exe")
    assert len(collisions) == 1 and collisions[0].get("lane") == ":LSA104_part0_12_0"
    result = materialize_unsplit_fanout(source_file=source, junction_id="LSA104_part0", connection_keys=KEYS,
        output_dir=tmp_path / "candidate", netconvert_binary=binaries / "netconvert.exe")
    collisions, final = _run(tmp_path / "after", Path(result["candidate_file"]), binaries / "sumo.exe")
    assert len(collisions) == 0
    assert [int(final.get(k)) for k in ("inserted", "arrived", "running", "waiting", "collisions", "teleports")] == [2, 2, 0, 0, 0, 0]
    assert source.read_bytes() == original
    assert result["ordinary_roads_unchanged"] and result["turn_permissions_unchanged"]
    assert result["signal_bindings_unchanged"] and result["signal_programs_unchanged"]
    assert result["maximum_curve_offset_m"] < 1e-4
    assert result["status"] == "review_required"
    before = ET.fromstring(original)
    after = ET.parse(result["candidate_file"]).getroot()
    def inventory(root, tag):
        return {r.get("id"): ET.canonicalize(ET.tostring(r, encoding="unicode"), strip_text=True)
                for r in root.findall(tag) if tag != "edge" or not r.get("function")}
    assert inventory(before, "edge") == inventory(after, "edge")
    assert inventory(before, "tlLogic") == inventory(after, "tlLogic")
    assert before.find("junction[@id='LSA104_part0']").get("shape") == after.find("junction[@id='LSA104_part0']").get("shape")


def test_unsplit_requires_one_existing_split_fanout_and_new_output(tmp_path):
    source = _source(tmp_path)
    with pytest.raises(ValueError, match="same incoming lane"):
        materialize_unsplit_fanout(source_file=source, junction_id="LSA104_part0", connection_keys=[KEYS[0], ("139083723#0", 3, "234423652#1", 1)], output_dir=tmp_path / "invalid")
    with pytest.raises(ValueError, match="new output"):
        materialize_unsplit_fanout(source_file=source, junction_id="LSA104_part0", connection_keys=KEYS, output_dir=tmp_path)


def test_frozen_lsa104_records_keep_all_native_measurements_and_file_hashes(tmp_path):
    import csv
    from astra_sumo.core.detector_demand import audit_expected_to_e1_strict
    packet = REPO / "benchmarks/hamburg_ring1_104_split_fanout_20261023_v1"
    manifest = json.loads((packet / "manifest.json").read_bytes())
    for row in manifest["files"]:
        data = (packet / row["path"]).read_bytes()
        assert len(data) == row["bytes"] and hashlib.sha256(data).hexdigest() == row["sha256"]
    expected = list(csv.DictReader(gzip.decompress((CASE / "expected-counts.csv.gz").read_bytes()).decode().splitlines()))
    scores = json.loads((packet / "results.json").read_bytes())
    for label in ("native_only", "body_hold"):
        folder = packet / label
        xml = tmp_path / (label + ".xml")
        xml.write_bytes(gzip.decompress((folder / "field-counts.xml.gz").read_bytes()))
        measured = audit_expected_to_e1_strict(expected, xml, count_attribute="nVehContrib")
        assert len(measured) == 130 and all(r["measured_nVehContrib"] is not None for r in measured)
        stored = list(csv.DictReader((folder / "field-comparison.csv").open(encoding="utf-8")))
        assert [int(r["measured_nVehContrib"]) for r in stored] == [r["measured_nVehContrib"] for r in measured]
        errors = [r["diff_nVehContrib_minus_expected"] for r in measured]
        assert sum(map(abs, errors)) / 130 == scores[label]["field_metrics"]["diagnostic_MAE"]
        assert scores[label]["healthy"] is False and scores[label]["exact_count_fit"] is False
