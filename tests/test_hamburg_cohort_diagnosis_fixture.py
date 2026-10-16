import importlib.util
from copy import deepcopy
import gzip
import hashlib
import json
from pathlib import Path
import shutil
import xml.etree.ElementTree as ET

import pytest


def test_recorded_cohort_accounting_and_immediate_lane_access():
    script = Path(__file__).resolve().parents[1] / "benchmarks/hamburg_cohort_diagnosis_20261016_v1/audit_cohort.py"
    spec = importlib.util.spec_from_file_location("frozen_hamburg_cohort_audit", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    report = module.audit_cohort()
    assert report["model_comparison"] == {"non_location_element_multisets_equal": True,
        "element_order_changed_tags": ["edge", "junction", "tlLogic"], "runtime_equivalence_established": False}
    expected = {"17": (64, 35, ["66"], 12), "42": (100, 0, [], 0), "104": (60, 39, ["66"], 13)}
    for seed, (arrived, active, pending, fronts) in expected.items():
        case = report["cases"][seed]
        assert case["requested"] == 100
        assert (case["arrived"], case["on_road"], case["pending_ids"]) == (arrived, active, pending)
        assert len(case["lane_front_candidates"]) == fronts
        assert all(row["next_route_edge"] and row["saved_path"] for row in case["lane_front_candidates"])
        assert case["fronts_without_immediate_path"] == []
    assert [report["cases"][seed]["collision_count"] for seed in ("17", "42", "104")] == [1, 1, 0]


def test_noop_local_merge_preserves_real_cohort_trajectories(tmp_path):
    from astra_sumo.core.local_network_conversion import local_conversion_input, merge_local_conversion
    from astra_sumo.core.paired_driving import compare_driving_cohort
    binary = shutil.which("sumo")
    if not binary:
        pytest.skip("SUMO required")
    folder = Path(__file__).resolve().parents[1] / "benchmarks/hamburg_cohort_diagnosis_20261016_v1"
    manifest = json.loads((folder / "fixture.json").read_text(encoding="utf-8"))
    inputs = []
    for key, name in (("recorded_model", "source.net.xml"), ("cohort", "cohort.rou.xml")):
        raw = gzip.decompress((folder / manifest[key]["path"]).read_bytes())
        assert hashlib.sha256(raw).hexdigest() == manifest[key]["uncompressed_sha256"]
        path = tmp_path / name
        path.write_bytes(raw)
        inputs.append(path)
    source, routes = inputs
    root = ET.parse(source).getroot()
    nodes = {"LSA2349_part0", "LSA2394_part0"}
    edges = {row.get("id") for row in root.findall("edge") if any(row.get(end) in nodes for end in ("from", "to"))}
    native, record = local_conversion_input(root, nodes, edges)
    assert record["status"] == "local"
    source_nodes = {row.get("id"): row for row in root.findall("junction")}
    native[:] = [deepcopy(source_nodes[row.get("id")]) if row.tag == "junction" else row for row in native]
    for tag in ("edge", "junction", "tlLogic"):
        reversed_rows = iter(reversed(native.findall(tag)))
        native[:] = [next(reversed_rows) if row.tag == tag else row for row in native]
    merged = merge_local_conversion(root, native, record)
    for tag in ("edge", "junction", "tlLogic"):
        assert [ET.tostring(row) for row in root.findall(tag)] == [ET.tostring(row) for row in merged.findall(tag)]
    candidate = tmp_path / "candidate.net.xml"
    ET.ElementTree(merged).write(candidate, encoding="utf-8", xml_declaration=True)
    report = compare_driving_cohort(networks={"source": source, "candidate": candidate}, reference="source",
        routes=routes, seeds=[17], horizon_s=2400, output_dir=tmp_path / "paired", sumo_binary=binary)
    assert report["execution_failure_count"] == 0
    a, b = [report["runs"]["17"][name] for name in ("source", "candidate")]
    assert a["trips"] == b["trips"]
    assert a["diagnostics"]["last_step_positions"]["vehicles"] == b["diagnostics"]["last_step_positions"]["vehicles"]
    assert a["diagnostics"]["collisions"]["records"] == b["diagnostics"]["collisions"]["records"]
