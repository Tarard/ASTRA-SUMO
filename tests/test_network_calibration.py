from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
import xml.etree.ElementTree as ET

import pytest

from astra_sumo.core.network_calibration import NetworkFactTable, evaluate_network_calibration


def test_published_fact_schema_matches_the_input_validator():
    path = Path(__file__).resolve().parents[1] / "schemas/product/astra.network-facts.v1.schema.json"
    assert json.loads(path.read_text(encoding="utf-8")) == NetworkFactTable.model_json_schema(by_alias=True)


def _case(tmp_path: Path, facts: list[dict]) -> tuple[Path, Path, Path]:
    network = tmp_path / "network.net.xml"
    network.write_text('''<net><edge id="in" from="a" to="j">
      <lane id="in_0" index="0" speed="10" length="10" allow="passenger bus" shape="0,0 10,0"/>
      </edge><edge id="out" from="j" to="b">
      <lane id="out_0" index="0" speed="10" length="10" shape="11,0 21,0"/>
      </edge><junction id="j" shape="10,-1 11,-1 11,1 10,1"/>
      <connection from="in" fromLane="0" to="out" toLane="0" tl="controller" linkIndex="2"/>
      </net>''', encoding="utf-8")
    source = tmp_path / "survey.txt"
    source.write_text("Independent survey fixture", encoding="utf-8")
    table = {"schema": "astra.network-facts/v1", "site_id": "test", "target_date": "2026-10-15",
             "coordinate_system": {"type": "network_xy_m", "location": {}},
             "sources": [{"id": "survey", "path": source.name,
                          "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                          "source_date": "2026-10-15", "authority": "synthetic test survey", "license": "MIT"}],
             "facts": [{"id": f"fact-{i}", "source_ids": ["survey"], **fact} for i, fact in enumerate(facts)]}
    facts_file = tmp_path / "facts.json"
    facts_file.write_text(json.dumps(table), encoding="utf-8")
    return network, facts_file, tmp_path / "scorecard.json"


def _run(paths):
    return evaluate_network_calibration(net_file=paths[0], facts_file=paths[1], output_file=paths[2], mode="release")


def test_geometry_counts_permissions_control_and_inventory(tmp_path):
    paths = _case(tmp_path, [
        {"kind": "lane_centerline", "selector": {"lane_id": "in_0"},
         "expected": [[0, 3], [1, 3], [10, 3]], "sample_spacing_m": 1, "tolerance_m": 2},
        {"kind": "stop_line", "selector": {"lane_id": "in_0"}, "expected": [10, 0], "tolerance_m": 0},
        {"kind": "lane_count", "selector": {"edge_ids": ["in"], "vehicle_class": "passenger"}, "expected": 1},
        {"kind": "lane_permissions", "selector": {"lane_id": "in_0"}, "expected": ["bus", "passenger"]},
        {"kind": "movement_inventory", "selector": {"incoming_lane_ids": ["in_0"]},
         "expected": [["in_0", "out_0"]]},
        {"kind": "signal_group", "selector": {"lane_pair": ["in_0", "out_0"]},
         "expected": {"tls_id": "controller", "link_index": 2}},
        {"kind": "junction_surface", "selector": {"junction_id": "j"},
         "expected": [[10, -1], [11, -1], [11, 1], [10, 1]], "minimum_iou": 1},
    ])
    report = _run(paths)
    assert report["status"] == "review_required"
    assert report["facts"][0]["metrics"]["mean_reference_offset_m"] == pytest.approx(3)
    assert report["facts"][0]["metrics"]["max_sampled_reference_offset_m"] == pytest.approx(3)
    assert all(f["status"] == "matched" for f in report["facts"][1:])
    assert report["layers"]["geometry"]["matched_fraction"] == 0.5
    assert json.loads(paths[2].read_text(encoding="utf-8"))["inputs"][str(paths[0])] == hashlib.sha256(paths[0].read_bytes()).hexdigest()


def test_unknown_reference_never_counts_as_a_pass(tmp_path):
    report = _run(_case(tmp_path, [{"kind": "lane_count", "selector": {}, "expected": None}]))
    assert report["status"] == "blocked"
    assert report["layers"]["lanes"] == {"matched_facts": 0, "known_facts": 0, "unknown_facts": 1,
                                           "mismatched_facts": 0, "matched_fraction": None}


@pytest.mark.parametrize("expected,tolerance,status", [(3.25, 0.1, "matched"), (2.0, 0.1, "mismatched")])
def test_lane_width_uses_the_compiled_default_and_an_explicit_tolerance(tmp_path, expected, tolerance, status):
    report = _run(_case(tmp_path, [{"kind": "lane_width", "selector": {"lane_id": "in_0"},
                                  "expected": expected, "tolerance_m": tolerance}]))
    row = report["facts"][0]
    assert row["status"] == status
    assert row["observed"] == 3.2
    assert row["metrics"]["absolute_width_error_m"] == pytest.approx(abs(3.2 - expected))


def test_inventory_penalizes_unsupported_turns_and_missing_movements(tmp_path):
    report = _run(_case(tmp_path, [{"kind": "movement_inventory",
        "selector": {"incoming_lane_ids": ["in_0"]}, "expected": [["in_0", "another_0"]]}]))
    metrics = report["facts"][0]["metrics"]
    assert (metrics["precision"], metrics["recall"]) == (0, 0)
    assert metrics["extra_movements"] == [["in_0", "out_0"]]
    assert metrics["missing_movements"] == [["in_0", "another_0"]]


def test_empty_precision_denominator_is_undefined(tmp_path):
    paths = _case(tmp_path, [{"kind": "movement_inventory", "selector": {"incoming_lane_ids": ["out_0"]},
                             "expected": []}])
    row = _run(paths)["facts"][0]
    assert row["status"] == "matched"
    assert row["metrics"]["precision"] is None and row["metrics"]["recall"] is None


def test_multiple_lane_parts_do_not_hide_a_geometry_gap(tmp_path):
    report = _run(_case(tmp_path, [{"kind": "lane_centerline", "selector": {"lane_ids": ["in_0", "out_0"]},
                                  "expected": [[0, 0], [21, 0]], "sample_spacing_m": 0.5, "tolerance_m": 0}]))
    row = report["facts"][0]
    assert row["status"] == "mismatched"
    assert row["metrics"]["max_sampled_reference_offset_m"] == pytest.approx(0.5)


def test_missing_lane_remains_a_known_mismatch(tmp_path):
    report = _run(_case(tmp_path, [{"kind": "stop_line", "selector": {"lane_id": "missing"},
                                  "expected": [0, 0], "tolerance_m": 0}]))
    assert report["facts"][0]["reason"] == "network_lane_missing"
    assert report["layers"]["control"]["known_facts"] == 1


@pytest.mark.parametrize("extra", [{}, {"mapping_status": "unresolved", "tolerance_m": 1}])
def test_measurement_does_not_supply_missing_acceptance_or_mapping(tmp_path, extra):
    report = _run(_case(tmp_path, [{"kind": "stop_line", "selector": {"lane_id": "in_0"},
                                  "expected": [10, 0], **extra}]))
    row = report["facts"][0]
    assert row["status"] == "unknown" and row["metrics"]["offset_m"] == 0
    assert report["layers"]["control"]["known_facts"] == 1
    assert report["layers"]["control"]["matched_fraction"] == 0


def test_same_fact_table_exposes_parent_regressions(tmp_path):
    paths = _case(tmp_path, [{"kind": "stop_line", "selector": {"lane_id": "in_0"},
                             "expected": [10, 0], "tolerance_m": 0.1}])
    parent = tmp_path / "parent.net.xml"
    parent.write_bytes(paths[0].read_bytes())
    paths[0].write_text(paths[0].read_text(encoding="utf-8").replace("0,0 10,0", "0,0 10,2"), encoding="utf-8")
    report = evaluate_network_calibration(net_file=paths[0], facts_file=paths[1], output_file=paths[2], parent_net_file=parent)
    assert report["comparison"]["regressed_facts"] == ["fact-0"]
    assert report["comparison"]["transitions"][0]["metric_deltas"]["offset_m"] == pytest.approx(2)


def test_projected_coordinates_remove_network_offset(tmp_path):
    paths = _case(tmp_path, [{"kind": "stop_line", "selector": {"lane_id": "in_0"},
                             "expected": [10, 0], "tolerance_m": 0}])
    root = ET.parse(paths[0]).getroot()
    ET.SubElement(root, "location", {"netOffset": "-100,-200", "projParameter": "EPSG:25832"})
    root.find("edge/lane").set("shape", "-100,-200 -90,-200")
    ET.ElementTree(root).write(paths[0], encoding="utf-8")
    table = json.loads(paths[1].read_text(encoding="utf-8"))
    table["coordinate_system"] = {"type": "projected_crs", "crs": "EPSG:25832"}
    paths[1].write_text(json.dumps(table), encoding="utf-8")
    assert _run(paths)["status"] == "pass"


@pytest.mark.parametrize("change,match", [
    (lambda t: t["sources"][0].update(sha256="0" * 64), "Source hash mismatch"),
    (lambda t: t["facts"][0].update(source_ids=[]), "requires source evidence"),
    (lambda t: t["coordinate_system"].update(location={"netOffset": "1,2"}), "exact location"),
    (lambda t: t["facts"].append(t["facts"][0]), "must be unique"),
    (lambda t: t["facts"][0].update(tolerance_m=True), "valid number"),
])
def test_invalid_evidence_fails_before_writing(tmp_path, change, match):
    paths = _case(tmp_path, [{"kind": "stop_line", "selector": {"lane_id": "in_0"},
                             "expected": [10, 0], "tolerance_m": 1}])
    table = json.loads(paths[1].read_text(encoding="utf-8"))
    change(table)
    paths[1].write_text(json.dumps(table), encoding="utf-8")
    with pytest.raises(ValueError, match=match):
        _run(paths)
    assert not paths[2].exists()


def test_refuses_to_replace_existing_scorecard(tmp_path):
    paths = _case(tmp_path, [{"kind": "lane_count", "selector": {}, "expected": None}])
    paths[2].write_text("previous result", encoding="utf-8")
    with pytest.raises(ValueError, match="new scorecard path"):
        _run(paths)
    assert paths[2].read_text(encoding="utf-8") == "previous result"


def test_input_mutation_during_comparison_blocks_report(tmp_path, monkeypatch):
    from astra_sumo.core import network_calibration

    paths = _case(tmp_path, [{"kind": "stop_line", "selector": {"lane_id": "in_0"},
                             "expected": [10, 0], "tolerance_m": 1}])
    score = network_calibration._score

    def mutate_input(root, table):
        result = score(root, table)
        paths[1].write_text("mutated facts", encoding="utf-8")
        return result

    monkeypatch.setattr(network_calibration, "_score", mutate_input)
    with pytest.raises(ValueError, match="input changed"):
        _run(paths)
    assert not paths[2].exists()


def test_inventory_rejects_connections_to_nonexistent_lanes(tmp_path):
    paths = _case(tmp_path, [{"kind": "movement_inventory", "selector": {"incoming_lane_ids": ["in_0"]},
                             "expected": [["in_0", "out_9"]]}])
    paths[0].write_text(paths[0].read_text(encoding="utf-8").replace('toLane="0"', 'toLane="9"'), encoding="utf-8")
    with pytest.raises(ValueError, match="missing lane"):
        _run(paths)
    assert not paths[2].exists()


def test_relative_fact_sources_survive_a_package_move(tmp_path):
    package = tmp_path / "original"
    package.mkdir()
    _case(package, [{"kind": "lane_count", "selector": {"edge_ids": ["in"], "vehicle_class": "passenger"},
                     "expected": 1}])
    relocated = tmp_path / "relocated"
    shutil.copytree(package, relocated)
    (package / "survey.txt").write_text("Old machine copy changed", encoding="utf-8")
    assert _run((relocated / "network.net.xml", relocated / "facts.json", relocated / "scorecard.json"))["status"] == "pass"


def test_cli_writes_scorecard_and_preserves_inputs(tmp_path, capsys):
    from astra_sumo.cli import main

    paths = _case(tmp_path, [{"kind": "lane_count", "selector": {"edge_ids": ["in"], "vehicle_class": "passenger"},
                             "expected": 1}])
    before = paths[0].read_bytes(), paths[1].read_bytes()
    assert main(["network", "calibrate", *map(str, paths), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "measured"
    assert (paths[0].read_bytes(), paths[1].read_bytes()) == before
