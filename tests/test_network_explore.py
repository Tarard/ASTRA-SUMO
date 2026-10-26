"""Daily measurements must remain usable before release evidence is complete."""

import json
import shutil
import subprocess
import xml.etree.ElementTree as ET

import pytest
from shapely.geometry import LineString

from astra_sumo.core.network_calibration import centerline_overlap_metrics, evaluate_network_calibration
from astra_sumo.core.network_edit_applicability import verify_network_edit
from astra_sumo.core.network_edit_operations import driving_integrity
from astra_sumo.core.candidate_contracts import file_sha256
from test_network_calibration import _case
from test_network_edit_operations import _source, _params


def test_explore_measures_uncertain_binding_and_stale_source_without_release_acceptance(tmp_path):
    net, facts, output = _case(tmp_path, [{"kind": "stop_line", "selector": {"lane_id": "in_0"},
        "expected": [10, 1], "tolerance_m": 2, "mapping_status": "unresolved", "mapping_confidence": "high"}])
    table = json.loads(facts.read_text(encoding="utf-8"))
    table["sources"][0]["sha256"] = "0" * 64
    facts.write_text(json.dumps(table), encoding="utf-8")
    report = evaluate_network_calibration(net_file=net, facts_file=facts, output_file=output)
    assert report["status"] == "measured" and report["mode"] == "explore"
    assert report["warnings"] == ["Source hash mismatch: survey"]
    row = report["facts"][0]
    assert row["metrics"]["offset_m"] == 1 and row["status"] == "matched"
    assert row["mapping_confidence"] == "high" and row["mapping_status"] == "unresolved"
    assert report["layers"]["control"]["measured_facts"] == 1
    assert report["layers"]["control"]["provisional_facts"] == 1
    with pytest.raises(ValueError, match="Source hash mismatch"):
        evaluate_network_calibration(net_file=net, facts_file=facts, output_file=tmp_path / "release.json", mode="release")


def test_explore_reports_missing_threshold_and_keeps_other_measurements_after_bad_reference(tmp_path):
    net, facts, output = _case(tmp_path, [
        {"kind": "stop_line", "selector": {"lane_id": "in_0"}, "expected": [10, 1]},
        {"kind": "lane_count", "selector": {"wrong": "in"}, "expected": 1},
        {"kind": "speed", "selector": {"lane_id": "in_0"}, "expected": 8, "tolerance_m_s": 0.5}])
    report = evaluate_network_calibration(net_file=net, facts_file=facts, output_file=output)
    assert [row["status"] for row in report["facts"]] == ["measured", "unknown", "mismatched"]
    assert report["facts"][2]["metrics"]["absolute_speed_error_m_s"] == 2


def test_explore_records_a_source_hash_when_none_was_supplied(tmp_path):
    net, facts, output = _case(tmp_path, [{"kind": "stop_line", "selector": {"lane_id": "in_0"}, "expected": [10, 0]}])
    table = json.loads(facts.read_text(encoding="utf-8"))
    table["sources"][0].pop("sha256")
    facts.write_text(json.dumps(table), encoding="utf-8")
    report = evaluate_network_calibration(net_file=net, facts_file=facts, output_file=output)
    assert report["status"] == "measured" and report["facts"][0]["metrics"]["offset_m"] == 0
    assert report["sources"][0]["observed_sha256"]
    assert report["warnings"]
    with pytest.raises(ValueError, match="Source hash mismatch"):
        evaluate_network_calibration(net_file=net, facts_file=facts, output_file=tmp_path / "release.json", mode="release")


@pytest.mark.parametrize("parts,coverage,offset", [
    ([[[0, 3], [10, 3]]], 0.5, 3),
    ([[[0, 0], [8, 0]], [[12, 0], [20, 0]]], 0.8, 0),
    ([[[0, 0], [10, 0]], [[10, 0], [20, 0]]], 1, 0),
    ([[[30, 0], [40, 0]]], 0, None),
    ([[[10, -10], [10, 10]]], 0, None),
])
def test_lateral_error_excludes_missing_tails_gaps_and_crossing_segments(parts, coverage, offset):
    metrics = centerline_overlap_metrics(LineString([[0, 0], [20, 0]]), parts, 0.25)
    assert metrics["reference_coverage"] == pytest.approx(coverage)
    assert metrics["covered_reference_length_m"] == pytest.approx(20 * coverage)
    assert metrics["mean_lateral_offset_m"] == (pytest.approx(offset) if offset is not None else None)


def test_missing_internal_path_is_a_physical_failure():
    root = ET.fromstring('<net><edge id="a"><lane id="a_0"/></edge><edge id="b"><lane id="b_0"/></edge>'
        '<connection from="a" fromLane="0" to="b" toLane="0" via=":missing_0"/></net>')
    assert driving_integrity(root)["status"] == "blocked"


@pytest.mark.skipif(not shutil.which("netconvert") or not shutil.which("sumo"), reason="SUMO binaries required")
def test_explore_blocks_a_lost_route_and_preserves_the_original_requests(tmp_path):
    source = _source(tmp_path, "add_lane_connection")
    ledger = {"source_net_file": str(source), "operations": [{"id": "remove-only-path", "operation": "delete_lane_connection",
        "params": {"from": "in", "fromLane": 0, "to": "middle", "toLane": 0}}]}
    path = tmp_path / "ledger.json"
    path.write_text(json.dumps(ledger), encoding="utf-8")
    report = verify_network_edit(ledger_file=path, output_dir=tmp_path / "daily", netconvert_binary=shutil.which("netconvert"))
    assert report["status"] == "blocked"
    assert report["candidate_routeability"]["route_generation"]["status"] == "fail"
    from pathlib import Path
    assert Path(report["candidate_routeability"]["trip_file"]).read_bytes() == Path(report["source_routeability"]["trip_file"]).read_bytes()


@pytest.mark.skipif(not shutil.which("netconvert") or not shutil.which("sumo"), reason="SUMO binaries required")
def test_valid_single_road_without_distinct_edge_od_still_allows_daily_speed_edit(tmp_path):
    source, nodes, edges = tmp_path / "source.net.xml", tmp_path / "nodes.xml", tmp_path / "edges.xml"
    nodes.write_text('<nodes><node id="a" x="0" y="0"/><node id="b" x="100" y="0"/></nodes>', encoding="utf-8")
    edges.write_text('<edges><edge id="road" from="a" to="b"/></edges>', encoding="utf-8")
    result = subprocess.run([shutil.which("netconvert"), "-n", str(nodes), "-e", str(edges), "-o", str(source)],
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    ledger = {"source_net_file": str(source), "operations": [{"id": "speed", "operation": "set_speed",
        "params": {"lane_id": "road_0", "speed_m_s": 8}}]}
    path = tmp_path / "ledger.json"
    path.write_text(json.dumps(ledger), encoding="utf-8")
    report = verify_network_edit(ledger_file=path, output_dir=tmp_path / "daily", netconvert_binary=shutil.which("netconvert"))
    assert report["status"] == "measured"
    assert "candidate_routeability" not in report
    assert report["source_baseline"]["status"] == "not_run"
    assert not any("Source driving checks failed" in warning for warning in report["warnings"])
    assert any("Route preservation was not checked" in warning for warning in report["warnings"])


@pytest.mark.skipif(not shutil.which("netconvert") or not shutil.which("sumo"), reason="SUMO binaries required")
@pytest.mark.parametrize("kind", ["set_edge_shape", "set_lane_count", "move_stop_line", "set_speed", "set_junction_shape"])
def test_explore_materializes_unapproved_edits_and_tests_original_requests(tmp_path, kind):
    source = _source(tmp_path, kind)
    original = source.read_bytes()
    ledger = {"source_net_file": str(source), "source_sha256": "0" * 64,
              "operations": [{"id": "daily", "operation": kind, "status": "candidate", "params": _params(kind, source)}]}
    path = tmp_path / "ledger.json"
    path.write_text(json.dumps(ledger), encoding="utf-8")
    report = verify_network_edit(ledger_file=path, output_dir=tmp_path / "daily", netconvert_binary=shutil.which("netconvert"))
    assert report["status"] == "measured", report
    assert report["mode"] == "explore" and report["release_gates"] == "not_run"
    assert report["construction"]["requested_effect_applied"], report["construction"]
    assert report["candidate_routeability"]["od_policy"]["request_count"] == 100
    if kind == "set_lane_count":
        expected = {str(old): str(new) for old, new in report["construction"]["lane_index_mapping"].items()}
        mapping = report["candidate_routeability"]["od_policy"]["endpoint_mapping"]
        assert mapping["source_lane_indices"] == {ledger["operations"][0]["params"]["edge_id"]: expected}
    assert report["source_immutable"] and source.read_bytes() == original
    assert report["warnings"]
    if kind == "set_speed":
        assert report["command_result"]["netconvert_rebuild_count"] == 0
        assert report["construction"]["direct_speed_edit"]["all_other_bytes_preserved"]
        assert report["construction"]["lane_coordinate_artifacts"]["status"] == "not_applicable"
    if kind == "move_stop_line":
        binding = report["construction"]["lane_coordinate_artifacts"]
        assert binding["status"] == "not_checked"
        assert "middle_0" in binding["affected_lane_ids"]
        assert any("Recheck detectors" in warning for warning in report["warnings"])


def test_calibration_cli_defaults_to_explore(tmp_path, capsys):
    from astra_sumo.cli import main
    net, facts, output = _case(tmp_path, [{"kind": "stop_line", "selector": {"lane_id": "in_0"},
        "expected": [10, 0], "mapping_status": "unresolved"}])
    assert main(["network", "calibrate", str(net), str(facts), str(output), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["facts"][0]["status"] == "measured"


@pytest.mark.skipif(not shutil.which("netconvert") or not shutil.which("sumo"), reason="SUMO binaries required")
def test_daily_width_edit_reports_fact_changes_and_keeps_a_remote_fact_unchanged(tmp_path):
    source = _source(tmp_path, "set_lane_width")
    tree = ET.parse(source)
    root = tree.getroot()
    edge = ET.Element("edge", id="remote", **{"from": "R", "to": "S", "priority": "1"})
    ET.SubElement(edge, "lane", id="remote_0", index="0", speed="10", length="50", shape="-500,0 -450,0")
    root.insert(next(i for i, row in enumerate(root) if row.tag == "junction"), edge)
    for name, x, incoming in (("R", "-500", ""), ("S", "-450", "remote_0")):
        node = ET.Element("junction", id=name, type="dead_end", x=x, y="0", incLanes=incoming, intLanes="", shape="")
        root.insert(next(i for i, row in enumerate(root) if row.tag == "connection"), node)
    tree.write(source, encoding="utf-8")
    survey = tmp_path / "survey.txt"
    survey.write_text("Synthetic width and remote-speed observations.", encoding="utf-8")
    facts = {"schema": "astra.network-facts/v1", "site_id": "width-test", "target_date": "2026-10-15",
        "coordinate_system": {"type": "network_xy_m", "location": dict(root.find("location").attrib)},
        "sources": [{"id": "survey", "path": str(survey), "sha256": file_sha256(survey),
                     "source_date": None, "authority": "synthetic", "license": "MIT"}],
        "facts": [{"id": "width-position", "kind": "lane_centerline", "selector": {"lane_id": "middle_0"},
                   "expected": [[0, -1.7], [20, -1.7]], "sample_spacing_m": 0.25, "source_ids": ["survey"]},
                  {"id": "remote-speed", "kind": "speed", "selector": {"lane_id": "remote_0"},
                   "expected": 10, "tolerance_m_s": 0, "source_ids": ["survey"]}]}
    facts_path = tmp_path / "facts.json"
    facts_path.write_text(json.dumps(facts), encoding="utf-8")
    path = tmp_path / "ledger.json"
    path.write_text(json.dumps({"source_net_file": str(source), "operations": [{"id": "width", "operation": "set_lane_width",
        "params": {"lane_id": "middle_0", "width_m": 3.4}}]}), encoding="utf-8")
    report = verify_network_edit(ledger_file=path, output_dir=tmp_path / "daily", facts_file=facts_path,
        netconvert_binary=shutil.which("netconvert"))
    assert report["status"] == "measured", report
    assert report["fact_deltas"][0]["metric_deltas"]["mean_lateral_offset_m"] < 0
    assert report["outside_fact_changes"] == []
    assert report["outside_fact_change_count"] == 0
    assert report["construction"]["geometry_preservation"]["outside_lane_count_moved_over_0_01_m"] == 0


@pytest.mark.skipif(not shutil.which("netconvert") or not shutil.which("sumo"), reason="SUMO binaries required")
def test_an_ineffective_edit_is_not_reported_as_applied(tmp_path, monkeypatch):
    from astra_sumo.core import network_edit_operations
    source = _source(tmp_path, "set_speed")
    path = tmp_path / "ledger.json"
    path.write_text(json.dumps({"source_net_file": str(source), "operations": [{"id": "speed", "operation": "set_speed",
        "params": {"lane_id": "middle_0", "speed_m_s": 8}}]}), encoding="utf-8")
    monkeypatch.setattr(network_edit_operations, "write_direct_lane_edit", lambda source, candidate, operation: candidate.write_bytes(source.read_bytes()))
    report = verify_network_edit(ledger_file=path, output_dir=tmp_path / "daily", netconvert_binary=shutil.which("netconvert"))
    assert report["status"] == "no_effect"
    assert report["construction"]["requested_effect_applied"] is False
    assert report["candidate_routeability"]["route_generation"]["status"] == "pass"


@pytest.mark.skipif(not shutil.which("netconvert") or not shutil.which("sumo"), reason="SUMO binaries required")
def test_daily_edit_retains_measurements_when_link_observation_is_unavailable(tmp_path, monkeypatch):
    from astra_sumo.core import network_link_semantics
    source = _source(tmp_path, "set_speed")
    ledger = tmp_path / "ledger.json"
    ledger.write_text(json.dumps({"source_net_file": str(source), "operations": [{"id": "speed", "operation": "set_speed",
        "params": {"lane_id": "middle_0", "speed_m_s": 8}}]}), encoding="utf-8")
    original = network_link_semantics.compare_loaded_link_semantics

    def unavailable(**kwargs):
        kwargs["sumo_binary"] = str(tmp_path / "missing-observer-sumo")
        return original(**kwargs)

    monkeypatch.setattr(network_link_semantics, "compare_loaded_link_semantics", unavailable)
    report = verify_network_edit(ledger_file=ledger, output_dir=tmp_path / "daily", netconvert_binary=shutil.which("netconvert"))
    assert report["status"] == "measured"
    assert report["construction"]["loaded_link_preservation"]["status"] == "unavailable"
    assert not report["construction"]["release_checks_passed"]
    assert any("Loaded link comparison: unavailable" in warning for warning in report["warnings"])
    assert any("FileNotFoundError" in warning for warning in report["warnings"])
    assert report["construction"]["direct_speed_edit"]["all_other_bytes_preserved"]


@pytest.mark.skipif(not shutil.which("netconvert") or not shutil.which("sumo"), reason="SUMO binaries required")
def test_an_unchanged_stop_line_is_no_effect_and_preserves_the_source_bytes(tmp_path):
    source = _source(tmp_path, "move_stop_line")
    path = tmp_path / "ledger.json"
    path.write_text(json.dumps({"source_net_file": str(source), "operations": [{"id": "same-stop-line", "operation": "move_stop_line",
        "params": {"lane_id": "middle_0", "end_offset_m": 0}}]}), encoding="utf-8")
    report = verify_network_edit(ledger_file=path, output_dir=tmp_path / "daily", netconvert_binary=shutil.which("netconvert"))
    assert report["status"] == "no_effect"
    assert report["construction"]["requested_effect_applied"] is False
    assert report["source"]["sha256"] == report["candidate_sha256"]
    assert report["command_result"]["netconvert_rebuild_count"] == 0


@pytest.mark.skipif(not shutil.which("netconvert") or not shutil.which("sumo"), reason="SUMO binaries required")
def test_explore_routeability_seeds_skip_or_report_one_pair_per_seed(tmp_path):
    from pathlib import Path
    source = _source(tmp_path, "set_speed")
    path = tmp_path / "ledger.json"
    path.write_text(json.dumps({"source_net_file": str(source), "operations": [{"id": "speed", "operation": "set_speed",
        "params": {"lane_id": "middle_0", "speed_m_s": 8}}]}), encoding="utf-8")
    netconvert = shutil.which("netconvert")
    skipped = verify_network_edit(ledger_file=path, output_dir=tmp_path / "skip", netconvert_binary=netconvert,
                                  routeability_seeds=())
    assert skipped["status"] == "measured" and skipped["routeability_seeds"] == []
    assert "source_routeability" not in skipped and "candidate_routeability" not in skipped
    assert skipped["source_baseline"] == {"status": "not_run", "scope": "synthetic_passenger_driving_diagnostic",
                                          "reason": "routeability_seeds_empty"}
    assert any("Routeability was skipped" in warning for warning in skipped["warnings"])
    report = verify_network_edit(ledger_file=path, output_dir=tmp_path / "two", netconvert_binary=netconvert,
                                 routeability_seeds=(17, 42))
    assert report["status"] == "measured" and report["routeability_seeds"] == [17, 42]
    assert "candidate_routeability" not in report and report["source_baseline"]["status"] == "clean"
    for entry, seed in zip(report["routeability_by_seed"], (17, 42)):
        assert entry["seed"] == seed and entry["source_baseline"]["status"] == "clean"
        assert entry["source_routeability"]["seed"] == entry["candidate_routeability"]["seed"] == seed
        assert Path(entry["source_routeability"]["output_dir"]).name == f"source-routes-seed{seed}"
        assert entry["candidate_routeability"]["od_policy"]["frozen_trip_sha256"] == file_sha256(
            Path(entry["source_routeability"]["trip_file"]))
    with pytest.raises(ValueError, match="during exploration"):
        verify_network_edit(ledger_file=path, output_dir=tmp_path / "release", netconvert_binary=netconvert,
                            mode="release", routeability_seeds=(17,))
    for seeds in ((True,), (-1,), (5, 5), "42"):
        with pytest.raises(ValueError, match="distinct nonnegative"):
            verify_network_edit(ledger_file=path, output_dir=tmp_path / "bad", netconvert_binary=netconvert,
                                routeability_seeds=seeds)
    assert not (tmp_path / "bad").exists()


@pytest.mark.skipif(not shutil.which("netconvert") or not shutil.which("sumo"), reason="SUMO binaries required")
def test_explore_source_audits_run_supplied_frozen_trips_per_seed(tmp_path):
    # Twin review finding: explore regenerated each seed's requests from the ledger source, so a chained step could
    # not run the goal's frozen trips. A supplied trip file is the source audit's input; the default is unchanged.
    from pathlib import Path
    from astra_sumo.core.routeability_audit import _write_source_reachable_trips, build_routeability_od_policy

    source = _source(tmp_path, "set_speed")
    path = tmp_path / "ledger.json"
    path.write_text(json.dumps({"source_net_file": str(source), "operations": [{"id": "speed", "operation": "set_speed",
        "params": {"lane_id": "middle_0", "speed_m_s": 8}}]}), encoding="utf-8")
    frozen = tmp_path / "frozen" / "seed17.trips.xml"
    frozen.parent.mkdir()
    _write_source_reachable_trips(source, build_routeability_od_policy([source]), frozen, vehicle_count=100, seed=17)
    netconvert = shutil.which("netconvert")
    report = verify_network_edit(ledger_file=path, output_dir=tmp_path / "fixed", netconvert_binary=netconvert,
                                 routeability_seeds=(17, 42), routeability_trip_files={17: frozen})
    by_seed = {entry["seed"]: entry for entry in report["routeability_by_seed"]}
    fixed = by_seed[17]["source_routeability"]["od_policy"]
    assert fixed["mode"] == "frozen_requests" and fixed["frozen_trip_sha256"] == file_sha256(frozen)
    assert fixed["request_file_sha256"] == file_sha256(frozen)
    assert by_seed[17]["candidate_routeability"]["od_policy"]["frozen_trip_sha256"] == file_sha256(frozen)
    assert by_seed[42]["source_routeability"]["od_policy"]["mode"] == "source_reachable_independent_generated"
    assert Path(by_seed[17]["source_routeability"]["trip_file"]).read_bytes() == frozen.read_bytes()
    for trips in ({5: frozen}, {17: tmp_path / "fixed" / "x.xml"}):
        with pytest.raises((ValueError, FileNotFoundError)):
            verify_network_edit(ledger_file=path, output_dir=tmp_path / "bad", netconvert_binary=netconvert,
                                routeability_seeds=(17,), routeability_trip_files=trips)
    with pytest.raises(ValueError, match="during exploration"):
        verify_network_edit(ledger_file=path, output_dir=tmp_path / "release", netconvert_binary=netconvert,
                            mode="release", routeability_trip_files={42: frozen})


@pytest.mark.skipif(not shutil.which("netconvert") or not shutil.which("sumo"), reason="SUMO binaries required")
def test_explore_checks_supplied_lane_artifacts_on_the_candidate_lane(tmp_path):
    source = _source(tmp_path, "move_stop_line")
    length = float(ET.parse(source).getroot().find("edge[@id='middle']/lane").get("length"))
    path = tmp_path / "ledger.json"
    path.write_text(json.dumps({"source_net_file": str(source), "operations": [{"id": "stop",
        "operation": "move_stop_line", "params": {"lane_id": "middle_0", "end_offset_m": 2}}]}), encoding="utf-8")
    early, late = tmp_path / "early.add.xml", tmp_path / "late.add.xml"
    early.write_text('<additional><inductionLoop id="early" lane="middle_0" pos="1" period="60" file="x.xml"/>'
                     '</additional>', encoding="utf-8")
    late.write_text('<additional>'
                    f'<inductionLoop id="late" lane="middle_0" pos="{length - 0.5}" period="60" file="x.xml"/>'
                    '</additional>', encoding="utf-8")
    netconvert = shutil.which("netconvert")
    report = verify_network_edit(ledger_file=path, output_dir=tmp_path / "early", netconvert_binary=netconvert,
                                 routeability_seeds=(), lane_artifact_files=[early])
    artifacts = report["construction"]["lane_coordinate_artifacts"]
    assert report["status"] == "measured" and artifacts["status"] == "pass"
    assert artifacts["artifacts"][0]["lane_coordinates_changed"] and "middle_0" in artifacts["affected_lane_ids"]
    report = verify_network_edit(ledger_file=path, output_dir=tmp_path / "late", netconvert_binary=netconvert,
                                 lane_artifact_files=[early, late])
    artifacts = report["construction"]["lane_coordinate_artifacts"]
    assert report["status"] == "blocked" and artifacts["status"] == "blocked" and artifacts["outside_count"] == 1
    assert artifacts["artifacts"][1]["lane_length_m"] == pytest.approx(length - 2, abs=0.01)
    assert any("lie outside their candidate lane length" in warning for warning in report["warnings"])
