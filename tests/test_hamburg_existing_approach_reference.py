"""Keep the real source comparison independent of local generated run folders."""

import gzip
import hashlib
import json
from pathlib import Path

import pytest

from astra_sumo.core.hamburg_aerial_approach import build_hamburg_aerial_approach_candidate


def test_frozen_hamburg_existing_approaches_and_remaining_transition_offsets(tmp_path, monkeypatch):
    folder = Path(__file__).resolve().parents[1] / "benchmarks/hamburg_approach_reference_20261016_v1"
    fixture = json.loads((folder / "fixture.json").read_text(encoding="utf-8"))

    def unpack(record, name):
        compressed = (folder / record["path"]).read_bytes()
        assert hashlib.sha256(compressed).hexdigest() == record["sha256"]
        raw = gzip.decompress(compressed)
        assert len(raw) == record["uncompressed_bytes"]
        assert hashlib.sha256(raw).hexdigest() == record["uncompressed_sha256"]
        target = tmp_path / name
        target.write_bytes(raw)
        return {"path": str(target), "sha256": record["uncompressed_sha256"]}

    source = unpack(fixture["source_model"], "source.net.xml")
    plans, inputs = {}, [source]
    for node, site in fixture["sites"].items():
        plan = {key: site[key] for key in ("node_id", "lanes", "movements")}
        plan["inputs"] = {role: unpack(record, f"{node}.{role}.{'kml' if role == 'map_kml' else 'xml'}")
                          for role, record in site["sources"].items()}
        inputs.extend(plan["inputs"].values())
        plans[node] = plan
    bindings = {(row["node_id"], row["intersection_part"]): row["junction_id"] for row in fixture["junction_bindings"]}
    monkeypatch.setattr("astra_sumo.core.command_runner.run_command", lambda *args, **kwargs: pytest.fail("Existing source lanes need no rebuild."))
    result = build_hamburg_aerial_approach_candidate(source_net=source["path"], plans=plans,
        junction_bindings=bindings, output_dir=tmp_path / "result", **fixture["parameters"])
    assert result["status"] == "review_required"
    assert result["candidate_network"] is None and not result["approaches"]
    assert sorted([row["node_id"], row["approach"]] for row in result["not_applicable"]) == fixture["expected_existing_approaches"]
    transitions = {row["node_id"] + ":" + row["approach"]: {"junction_id": row["source_transition_junction_id"],
        "before": row["source_motor_lane_count_before"], "after": row["source_motor_lane_count_after"]}
        for row in result["not_applicable"] if "source_transition_junction_id" in row}
    assert transitions == fixture["expected_source_transitions"]
    offsets = {row["node_id"] + ":" + row["approach"]: row["merge_to_source_node_longitudinal_m"] for row in result["reviews"]}
    assert offsets == pytest.approx(fixture["expected_transition_offsets_m"], abs=0.01)
    assert all(row["reason"] == "existing_transition_geometry_observation" for row in result["reviews"])
    taper = {row["node_id"]: row["reference_taper_comparison"] for row in result["reviews"]}
    assert taper["2349"]["reference_length_after_centerline_separation_m"]["3.0"] == pytest.approx(49.52, abs=0.1)
    assert taper["2349"]["source_ordinary_pocket_logical_length_m"] == pytest.approx(51.05, abs=0.01)
    assert taper["2394"]["reference_length_after_centerline_separation_m"]["3.0"] == pytest.approx(75.53, abs=0.1)
    assert taper["2394"]["source_ordinary_pocket_logical_length_m"] == pytest.approx(81.97, abs=0.01)
    for record in inputs:
        assert hashlib.sha256(Path(record["path"]).read_bytes()).hexdigest() == record["sha256"]
