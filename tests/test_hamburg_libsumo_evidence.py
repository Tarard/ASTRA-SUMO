"""Verify the frozen full-window backend comparison without rerunning SUMO."""
import hashlib
import importlib.util
import json
from pathlib import Path

CASE = Path(__file__).resolve().parents[1] / "benchmarks/hamburg_ring1_libsumo_parity_20261023_v1"


def test_frozen_full_libsumo_traffic_matches_socket_baseline():
    spec = importlib.util.spec_from_file_location("hamburg_libsumo_verify", CASE / "verify.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    actual = module.verify(CASE)
    assert actual["correspondence"]
    assert actual == json.loads((CASE / "traffic-correspondence.json").read_bytes())
    assert actual["traffic_digests"]["summary.xml.gz"]["libsumo"]["rows"] == 144000
    assert actual["traffic_digests"]["field-counts.xml.gz"]["libsumo"]["rows"] == 208
    evaluation = json.loads((CASE / "evaluation.json").read_bytes())
    assert evaluation["MAE"] == 2175 / 130 and not evaluation["healthy"] and not evaluation["exact_count_fit"]


def test_libsumo_evidence_hashes_and_timing_claims():
    manifest = json.loads((CASE / "manifest.json").read_bytes())
    assert not manifest["formal_result"]
    listed = {row["path"] for row in manifest["files"]}
    assert listed == {p.relative_to(CASE).as_posix() for p in CASE.rglob("*")
                      if p.is_file() and p.name != "manifest.json" and "__pycache__" not in p.parts}
    for row in manifest["files"]:
        raw = (CASE / row["path"]).read_bytes()
        assert len(raw) == row["bytes"] and hashlib.sha256(raw).hexdigest() == row["sha256"]
    timing = json.loads((CASE / "timing.json").read_bytes())
    assert timing["paired_runs"] == 1
    assert timing["native_speed_ratio"] == timing["socket_native_duration_s"] / timing["libsumo_native_duration_s"]


def test_event_comparison_keeps_each_vehicle_order_and_values():
    spec = importlib.util.spec_from_file_location("hamburg_hold_verify", CASE / "verify.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    a = {"vehicle_id": "a", "event": "held", "time_s": 1}
    b = {"vehicle_id": "b", "event": "held", "time_s": 1}
    released = {"vehicle_id": "a", "event": "released", "time_s": 2}
    before = {"events": [a, b, released], "still_held": {}}
    assert module.lane_hold_correspondence(before, {"events": [b, a, released], "still_held": {}})
    assert not module.lane_hold_correspondence(before, {"events": [released, a, b], "still_held": {}})
    assert not module.lane_hold_correspondence(before, {"events": [a, b, {**released, "time_s": 3}], "still_held": {}})
