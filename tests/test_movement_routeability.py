import importlib
import json
import shutil
import subprocess

import pytest

from astra_sumo import cli
from astra_sumo.core.candidate_contracts import file_sha256


@pytest.mark.skipif(not shutil.which("netconvert") or not shutil.which("sumo"), reason="SUMO binaries required")
def test_context_paths_are_probed_separately_and_bound_to_the_construction_source(tmp_path):
    from astra_sumo.core.movement_routeability import run_candidate_movement_probes

    nodes, edges, network = tmp_path / 'nodes.xml', tmp_path / 'edges.xml', tmp_path / 'net.xml'
    nodes.write_text('<nodes><node id="w" x="0" y="0"/><node id="j" x="50" y="0"/><node id="n" x="50" y="50"/><node id="e" x="100" y="0"/></nodes>', encoding='utf-8')
    edges.write_text('<edges><edge id="a" from="w" to="j"/><edge id="b" from="n" to="j"/><edge id="out" from="j" to="e"/></edges>', encoding='utf-8')
    subprocess.run(['netconvert', '-n', str(nodes), '-e', str(edges), '-o', str(network)], check=True, capture_output=True, timeout=30)
    identity = {'path': str(network), 'sha256': file_sha256(network)}
    manifest, context = tmp_path / 'manifest.json', tmp_path / 'context.json'
    manifest.write_text(json.dumps({'schema': 'torii.hamburg-aerial-corridor-candidate/v1',
        'artifacts': {'network': identity}, 'inputs': {'source_net': identity},
        'counts': {'official_vehicle_movements': 1},
        'official_connection_audit': {'required': [{'sumo_connection': ['a', 0, 'out', 0]}]}}), encoding='utf-8')
    record = {'schema': 'torii.junction-boundary-rebuild/v1', 'status': 'pass', 'candidate_network': identity,
              'plan': {'movements': [{'join_id': 'j', 'connection': ['b', 0, 'out', 0], 'vehicle_classes': ['passenger']}]}}
    context.write_text(json.dumps(record), encoding='utf-8')
    result = run_candidate_movement_probes(candidate_manifest=manifest, context_manifest=context, output_dir=tmp_path / 'probes')
    assert result['status'] == 'pass'
    assert result['official_total'] == 1
    assert result['context_boundary_probe_count'] == 1
    assert result['probe_count'] == 2
    assert all(row['connection_chain_proof']['pass'] for row in result['results'])
    record['candidate_network'] = {'path': str(network), 'sha256': '0' * 64}
    context.write_text(json.dumps(record), encoding='utf-8')
    with pytest.raises(ValueError, match='construction source'):
        run_candidate_movement_probes(candidate_manifest=manifest, context_manifest=context, output_dir=tmp_path / 'wrong-source')


def test_complete_chain_rejects_a_wrong_internal_lane_even_with_correct_endpoints():
    module = importlib.import_module("astra_sumo.core.movement_routeability")
    movement = {"sumo_connection": ["in", 0, "out", 0]}
    correct = [{"lane": value} for value in ("in_0", ":j_0_0", ":j_1_0", "out_0")]
    wrong = [{"lane": value} for value in ("in_0", ":j_9_0", "out_0")]
    missing = [{"lane": value} for value in ("in_0", "out_0")]
    expected = [":j_0_0", ":j_1_0"]
    assert module.verify_lane_chain(movement, correct, expected)["pass"]
    assert not module.verify_lane_chain(movement, wrong, expected)["pass"]
    assert not module.verify_lane_chain(movement, missing, expected)["pass"]


@pytest.mark.parametrize("fault", [None, "wrong_lane", "wrong_id", "wrong_fcd_id", "early_arrival", "late_arrival", "late_departure", "missing_internal", "wrong_internal", "vaporized", "unclean_completion", "wrong_observed_exit"])
def test_terminal_arrival_can_prove_only_the_exit_not_missing_internal_lanes(fault):
    module = importlib.import_module("astra_sumo.core.movement_routeability")
    movement = {"sumo_connection": ["in", 0, "out", 0]}
    expected = [":j_0_0", ":j_1_0"]
    states = [{"id": "probe", "lane": lane, "time": time, "last_time": last} for lane, time, last in (
        ("in_0", 1.0, 1.9), (":j_0_0", 2.0, 2.9), (":j_1_0", 3.0, 3.9))]
    terminal = {"id": "probe", "depart": "1.0", "arrival": "4.0", "arrivalLane": "out_0", "vaporized": ""}
    if fault == "wrong_lane":
        terminal["arrivalLane"] = "out_1"
    elif fault == "wrong_id":
        terminal["id"] = "another"
    elif fault == "wrong_fcd_id":
        states[-1]["id"] = "another"
    elif fault == "early_arrival":
        terminal["arrival"] = "3.0"
    elif fault == "late_arrival":
        terminal["arrival"] = "4.2"
    elif fault == "late_departure":
        terminal["depart"] = "2.0"
    elif fault == "missing_internal":
        states.pop(1)
    elif fault == "wrong_internal":
        states[1]["lane"] = ":j_9_0"
    elif fault == "vaporized":
        terminal["vaporized"] = "true"
    elif fault == "wrong_observed_exit":
        states.append({"id": "probe", "lane": "out_1", "time": 3.95, "last_time": 3.95})
    proof = module.verify_lane_chain(movement, states, expected, terminal_tripinfo=terminal,
        expected_vehicle_id="probe", completed_without_collision_or_teleport=fault != "unclean_completion", step_length_s=.1)
    assert proof["pass"] is (fault is None)
    if fault is None:
        assert proof["outlet_evidence_source"] == "tripinfo"
        assert proof["observed_internal_lane_chain"] == expected


@pytest.mark.skipif(not shutil.which("netconvert") or not shutil.which("sumo"), reason="SUMO binaries required")
def test_public_movement_probe_runs_a_permitted_vehicle_and_checks_full_chain(tmp_path, capsys):
    nodes, edges = tmp_path / "nodes.xml", tmp_path / "edges.xml"
    nodes.write_text('<nodes><node id="w" x="0" y="0"/><node id="j" x="50" y="0"/><node id="n" x="50" y="50"/></nodes>', encoding="utf-8")
    edges.write_text('<edges><edge id="in" from="w" to="j" allow="delivery"/><edge id="out" from="j" to="n" allow="delivery"/></edges>', encoding="utf-8")
    network = tmp_path / "net.xml"
    subprocess.run(["netconvert", "-n", str(nodes), "-e", str(edges), "-o", str(network)], check=True, capture_output=True, timeout=30)
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"schema": "torii.hamburg-aerial-corridor-candidate/v1", "artifacts": {"network": {"path": str(network), "sha256": file_sha256(network)}}, "counts": {"official_vehicle_movements": 1}, "official_connection_audit": {"required": [{"node_id": "1", "movement_id": "1", "sumo_connection": ["in", 0, "out", 0]}], "ambiguous": []}}), encoding="utf-8")
    assert cli.main(["network", "movement-probes", str(manifest), str(tmp_path / "probes"), "--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["passed_exact_lane_transition"] == result["official_total"] == 1
    assert result["vehicle_class_counts"] == {"delivery": 1}
    assert result["results"][0]["connection_chain_proof"]["pass"]
    assert result["source_immutable"]
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    payload["official_connection_audit"]["required"][0]["allowed_vehicle_classes"] = ["bus"]
    manifest.write_text(json.dumps(payload), encoding="utf-8")
    assert cli.main(["network", "movement-probes", str(manifest), str(tmp_path / "bus-required"), "--json"]) == 1
    restricted = json.loads(capsys.readouterr().out)
    assert restricted["passed_exact_lane_transition"] == 0
    assert restricted["results"][0]["reason"] == "no_common_motor_vehicle_permission"
    payload["official_connection_audit"]["required"][0]["allowed_vehicle_classes"] = ["delivery"]
    payload["official_connection_audit"]["required"][0]["composition_geometry_status"] = "review_required"
    manifest.write_text(json.dumps(payload), encoding="utf-8")
    assert cli.main(["network", "movement-probes", str(manifest), str(tmp_path / "geometry-unproved"), "--json"]) == 1
    geometric = json.loads(capsys.readouterr().out)
    assert geometric["results"][0]["completed_without_collision_or_teleport"]
    assert geometric["passed_composed_spatial_movements"] == 0
    payload["official_connection_audit"]["required"][0]["composition_geometry_status"] = "pass"
    payload["official_connection_audit"]["boundary_connections"] = [{"movement_id": "second-boundary", "sumo_connection": ["in", 0, "missing-exit", 0]}]
    manifest.write_text(json.dumps(payload), encoding="utf-8")
    assert cli.main(["network", "movement-probes", str(manifest), str(tmp_path / "second-boundary-missing"), "--json"]) == 1
    missing = json.loads(capsys.readouterr().out)
    assert missing["mapped_and_tested"] == missing["passed_exact_lane_transition"] == 1
    assert missing["probe_count"] == 2
    assert missing["additional_boundary_probe_count"] == 1
    assert missing["results"][1]["status"] == "review_required"
