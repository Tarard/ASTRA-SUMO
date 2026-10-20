import gzip
from pathlib import Path
import json
import shutil

import pytest

from astra_sumo.core.artifact_io import write_json_atomic
from astra_sumo.core.candidate_contracts import file_sha256
from astra_sumo.core.hamburg_scope_anchors import write_hamburg_approach_retention
from astra_sumo.core.hamburg_network_calibration import evaluate_hamburg_calibration_snapshot
from astra_sumo.core.network_calibration import evaluate_network_calibration
from test_network_calibration import _case


def _snapshot(tmp_path):
    network, facts, score = _case(tmp_path, [
        {"kind": "lane_centerline", "traffic_mode": "motor", "selector": {"lane_ids": ["in_0", "out_0"]},
         "expected": [[0, 0], [21, 0]], "sample_spacing_m": .25, "mapping_confidence": "low", "mapping_status": "unresolved"},
        {"kind": "lane_centerline", "traffic_mode": "motor", "selector": {}, "mapping_status": "unresolved",
         "expected": [[0, 0], [21, 0]], "sample_spacing_m": .25},
        {"kind": "lane_centerline", "traffic_mode": "motor", "selector": {"lane_id": "in_0"},
         "expected": [[10, 0], [0, 0]], "reference_direction": 1, "sample_spacing_m": .25}])
    evaluate_network_calibration(net_file=network, facts_file=facts, output_file=score, mode="explore")
    run = tmp_path / "run.json"
    write_json_atomic(run, {"schema": "astra.hamburg-network-calibration-run/v1",
        "network": {"path": str(network), "sha256": file_sha256(network)},
        "sites": {"demo": {"scorecard": str(score), "facts_file": str(facts)}}})
    return run, network


def test_retention_keeps_low_confidence_but_separates_road_and_whole_lane_coverage(tmp_path):
    run, network = _snapshot(tmp_path)
    digest = file_sha256(network)
    first = write_hamburg_approach_retention(calibration_run_file=run, output_file=tmp_path / "first.json")
    write_hamburg_approach_retention(calibration_run_file=run, output_file=tmp_path / "second.json")
    assert (tmp_path / "first.json").read_bytes() == (tmp_path / "second.json").read_bytes()
    assert first["protected_edge_ids"] == ["in", "out"]
    assert first["edge_evidence"]["in"]["mapping_confidences"] == ["low"]
    assert not first["edge_evidence"]["in"]["has_high_confidence_fact_support"]
    assert all(row["scorecard_lane_reference_coverage"] == pytest.approx(20/21) for row in first["anchors"])
    assert all(row["edge_reference_coverage"] == pytest.approx(10/21) for row in first["anchors"])
    assert first["motor_lane_counts"]["demo"] == {"reference_motor_lanes": 3, "source_lane_parts_available": 2,
        "usable_overlap_retention_anchors": 1, "unmapped_source_lanes": 1, "source_lateral_offset_unavailable": 1}
    assert file_sha256(network) == digest and first["source_unchanged"]


def test_stale_snapshot_is_reported_without_blocking_retention_measurement(tmp_path):
    run, network = _snapshot(tmp_path)
    network.write_text(network.read_text(encoding="utf-8").replace('speed="10"', 'speed="11"'), encoding="utf-8")
    result = write_hamburg_approach_retention(calibration_run_file=run, output_file=tmp_path / "anchors.json")
    assert result["status"] == "measured" and result["warnings"]
    assert result["protected_edge_ids"] == ["in", "out"]
    assert result["inputs"]["measured_network_sha256"] != result["inputs"]["snapshot_network_sha256"]


def test_retention_never_overwrites_existing_output(tmp_path):
    run, _ = _snapshot(tmp_path)
    output = tmp_path / "anchors.json"
    output.write_text("retained source", encoding="utf-8")
    with pytest.raises(ValueError, match="new"):
        write_hamburg_approach_retention(calibration_run_file=run, output_file=output)
    assert output.read_text(encoding="utf-8") == "retained source"


@pytest.mark.skipif(not shutil.which("netconvert") or not shutil.which("sumo"), reason="SUMO binaries required")
def test_native_ring1_approach_generation_from_frozen_sources(tmp_path):
    from astra_sumo.core.osm_network import build_osm_network
    repo = Path(__file__).parents[1]
    fixture_dir = repo / "benchmarks/hamburg_ring1_road_scope_20261020_v1"
    fixture = json.loads((fixture_dir / "fixture.json").read_text(encoding="utf-8"))
    built = build_osm_network(bbox=fixture["bbox"], source_osm_path=fixture_dir / "source.osm.xml.gz",
        historical_date=fixture["osm_snapshot_date"], output_dir=tmp_path / "source", prefix="ring1-full",
        allowed_highways=set(fixture["road_classes"]), tls_join_distance_m=fixture["tls_join_distance_m"],
        netconvert_profile=fixture["native_profile"], output_precision=fixture["precision"], netconvert_binary=shutil.which("netconvert"))
    assert built["netconvert"]["status"] == "pass" and built["netconvert"]["returncode"] == 0, built
    assert built["osm_access_correction"]["load_audit"]["status"] == "pass"
    assert built["osm_access_correction"]["source_immutable"]
    official = repo / "benchmarks/hamburg_ring1_official_sources_20261020_v1"
    metadata = json.loads((official / "manifest.json").read_text(encoding="utf-8"))
    paths, downloads = {}, []
    for row in metadata["files"]:
        if row["node_id"] != "535":
            continue
        path = tmp_path / row["source_filename"]
        path.write_bytes(gzip.decompress((official / row["path"]).read_bytes()))
        assert file_sha256(path) == row["sha256"]
        paths[row["kind"]] = str(path)
        downloads.append({"node_id": "535", "kind": row["kind"], "path": str(path), "sha256": row["sha256"]})
    manifest = tmp_path / "sources.json"
    write_json_atomic(manifest, {"paths": {"535": paths}, "downloads": downloads})
    evaluate_hamburg_calibration_snapshot(network=Path(built["net_file"]), official_sources=manifest,
        output_dir=tmp_path / "calibration", target_date=fixture["osm_snapshot_date"][:10], sample_spacing_m=.5)
    result = write_hamburg_approach_retention(calibration_run_file=tmp_path / "calibration/run.json", output_file=tmp_path / "retention.json")
    expected = json.loads((fixture_dir / fixture["official_approach_anchors_file"]).read_text(encoding="utf-8"))
    wanted = {r["edge_id"] for r in expected["anchors"] if r["official_fact"].startswith("535:")}
    assert set(result["protected_edge_ids"]) == wanted
    assert result["warnings"] == [] and result["source_unchanged"]
    assert result["motor_lane_counts"]["535"]["usable_overlap_retention_anchors"] == 21
