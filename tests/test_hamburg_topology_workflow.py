from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from astra_sumo.core import hamburg_topology_workflow as workflow


def _write(path: Path, value) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value if isinstance(value, str) else json.dumps(value), encoding="utf-8")
    return path


def _artifact(path: Path) -> dict:
    return {"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


def _request(tmp_path: Path) -> Path:
    osm = _write(tmp_path / "raw.osm.xml", '<osm version="0.6"/>')
    identity = _write(tmp_path / "lsa.json", {"type": "FeatureCollection", "features": []})
    sources = {key: _artifact(_write(tmp_path / key, key)) for key in ("map_xml", "map_kml", "aerial_image")}
    return _write(tmp_path / "request.json", {
        "schema": workflow.REQUEST_SCHEMA,
        "source_osm": _artifact(osm), "lsa_identity": _artifact(identity),
        "osm_build": {"bbox": "9.98,53.54,10.00,53.56", "highway_classes": "full_vehicle"},
        "road_names": ["Example road"],
        "intersections": [{"node_id": node_id, "aerial_year": 2024,
                           "bbox_epsg25832": [1, 2, 101, 102], **sources} for node_id in ("1", "2")],
        "construction": {"seed": 104, "vehicle_count": 100, "simulation_end": 600, "simulation_max_end": 2400},
    })


def _fake_stages(monkeypatch, *, topology_complete=True, geometry_status="pass"):
    observed = {}

    def build(**kwargs):
        observed["source"] = kwargs
        return {"status": "pass", "net_file": str(_write(kwargs["output_dir"] / "raw.net.xml", "<net/>")),
                "filtered_osm_file": str(_write(kwargs["output_dir"] / "raw.osm.xml", "<osm/>"))}

    def tls(**kwargs):
        return {"status": "pass", "clusters_file": str(_write(kwargs["output_dir"] / "clusters.csv", "cluster_id\n1\n"))}

    def bind(**kwargs):
        observed["binding"] = kwargs
        result = {"status": "pass", "ordered_node_ids": ["1", "2"], "bindings": [{"node_id": "1"}, {"node_id": "2"}]}
        _write(kwargs["output_file"], result)
        return {**result, "output_file": str(kwargs["output_file"])}

    def movements(**kwargs):
        request = json.loads(Path(kwargs["request_file"]).read_text(encoding="utf-8"))
        observed["movements"] = request
        plans = []
        for row in request["intersections"]:
            plan = _write(kwargs["output_dir"] / f"new-plan-{row['node_id']}.json", {"fresh_source": row["map_xml"]})
            plans.append({"node_id": row["node_id"], "plan_file": str(plan), "plan_sha256": _artifact(plan)["sha256"]})
        result = {"status": "pass", "intersections": plans,
                  "totals": {"official_vehicle_movements": 2, "accepted_aerial_traces": 2, "official_map_fallbacks": 0}}
        _write(kwargs["output_dir"] / "corridor-summary.json", result)
        return result

    def combine(**kwargs):
        request = json.loads(Path(kwargs["request_file"]).read_text(encoding="utf-8"))
        observed["combine"] = request
        assert Path(request["movement_summary"]["path"]).is_relative_to(kwargs["output_dir"].parent)
        net = _write(kwargs["output_dir"] / "candidate.net.xml", "<net/>")
        result = {"status": "pass", "topology_complete": topology_complete,
                  "counts": {"official_vehicle_movements": 2, "materialized_movement_count": 2 * int(topology_complete)},
                  "artifacts": {"network": _artifact(net)}, "inputs": {"source_net": request["source_net"]}}
        _write(kwargs["output_dir"] / "manifest.json", result)
        return result

    def probes(**kwargs):
        result = {"status": "pass" if topology_complete else "review_required", "official_movement_count": 2,
                  "passed": 2 * int(topology_complete)}
        return {**result, "report_file": str(_write(kwargs["output_dir"] / "summary.json", result))}

    def audit(manifest_file, output_dir, **kwargs):
        result = {"status": geometry_status, "geometry_review": {"status": geometry_status}}
        return {**result, "report_file": str(_write(output_dir / "audit.json", result))}

    def routes(candidate_manifest, output_dir, **kwargs):
        observed["route_checks"] = kwargs
        result = {"status": "pass", "mainline": {"status": "pass"}, "pocket_access": {"status": "not_applicable"}}
        return {**result, "report_file": str(_write(output_dir / "summary.json", result))}

    for name, function in {"build_osm_network": build, "audit_tls": tls,
        "bind_hamburg_corridor_tls_clusters": bind, "build_hamburg_aerial_corridor_plan": movements,
        "build_hamburg_aerial_combined_candidate": combine, "run_candidate_movement_probes": probes,
        "audit_hamburg_topology_candidate": audit, "run_hamburg_topology_route_checks": routes}.items():
        monkeypatch.setattr(workflow, name, function)
    return observed


@pytest.mark.parametrize("mode", ["guarded", "fused", "preserve", True, "smooth_anything"])
def test_contour_choice_reaches_construction_and_invalid_choices_stop_early(tmp_path, monkeypatch, mode):
    request = _request(tmp_path)
    value = json.loads(request.read_text(encoding="utf-8"))
    value["construction"]["junction_contours"] = mode
    value["construction"]["junction_corner_radius_m"] = 6.0
    _write(request, value)
    observed = _fake_stages(monkeypatch)
    output = tmp_path / "run"
    if mode is True or mode == "smooth_anything":
        with pytest.raises(ValueError, match="junction_contours"):
            workflow.build_hamburg_topology_workflow(request_file=request, output_dir=output)
        assert not output.exists()
    else:
        result = workflow.build_hamburg_topology_workflow(request_file=request, output_dir=output)
        assert result["status"] == "pass"
        assert observed["combine"]["junction_contours"] == mode
        assert observed["combine"]["junction_corner_radius_m"] == 6.0


def test_rebuilds_every_stage_from_raw_inputs_and_keeps_calibration_separate(tmp_path, monkeypatch):
    request = _request(tmp_path)
    observed = _fake_stages(monkeypatch)
    result = workflow.build_hamburg_topology_workflow(request_file=request, output_dir=tmp_path / "run")
    assert result["status"] == "pass"
    assert result["purpose"] == "network_construction"
    assert observed["combine"]["junction_contours"] == "fused"
    assert observed["combine"]["junction_corner_radius_m"] == 8.0
    assert result["calibration"]["status"] == "not_run"
    assert result["calibration"]["required_for_network_construction"] is False
    assert result["network_handoff"]["construction_decision"] == "pass"
    assert observed["movements"]["intersections"][0]["map_xml"]["sha256"]
    assert observed["combine"]["seed"] == 104
    assert observed["route_checks"]["road_names"] == ["Example road"]
    assert len(result["stages"]) == 10
    assert result["stages"]["road_geometry"]["status"] == "not_applicable"
    assert all(row["status"] in {"pass", "not_applicable"} for row in result["stages"].values())
    assert result["inputs_unchanged"] is True
    assert Path(result["report_file"]).is_file()
    handoff = json.loads(Path(result["handoff_file"]).read_text(encoding="utf-8"))
    assert handoff["workflow"] == _artifact(Path(result["report_file"]))
    assert handoff["network"] == result["network_handoff"]["network"]
    before = Path(result["report_file"]).read_bytes()
    with pytest.raises(ValueError, match="already exist"):
        workflow.build_hamburg_topology_workflow(request_file=request, output_dir=tmp_path / "run")
    assert Path(result["report_file"]).read_bytes() == before


def test_reviewed_road_geometry_reaches_normal_hamburg_construction(tmp_path, monkeypatch):
    request = _request(tmp_path)
    value = json.loads(request.read_text(encoding='utf-8'))
    value['construction']['context_lane_change_policy'] = 'permitted_interior'
    _write(request, value)
    observed = _fake_stages(monkeypatch)

    def repair(**kwargs):
        network = _write(kwargs['output_dir'] / 'repaired.net.xml', '<net><junction id="road-repaired"/></net>')
        return {'status': 'pass', 'applied': True, 'candidate_network': _artifact(network)}

    monkeypatch.setattr(workflow, 'build_hamburg_road_geometry_candidate', repair, raising=False)
    result = workflow.build_hamburg_topology_workflow(request_file=request, output_dir=tmp_path / 'run')
    assert result['status'] == 'pass'
    assert result['stages']['road_geometry']['status'] == 'pass'
    assert Path(observed['combine']['source_net']['path']).name == 'repaired.net.xml'
    assert result['checks']['road_geometry'] == 'pass'
    assert observed['combine']['context_lane_change_policy'] == 'permitted_interior'


def test_reviewed_context_reconstruction_is_used_by_the_normal_workflow(tmp_path, monkeypatch):
    path = _request(tmp_path)
    request = json.loads(path.read_text(encoding='utf-8'))
    request['context_intersections'] = [{'node_id': '3', 'source_node_ids': ['a', 'b'],
        'signal_policy': 'rebuild_diagnostic', 'review_basis': 'Reviewed source geometry',
        'aerial_year': 2024, 'bbox_epsg25832': [1, 2, 101, 102],
        'aerial_image': request['intersections'][0]['aerial_image']}]
    _write(path, request)
    observed = _fake_stages(monkeypatch)

    def rebuild(**kwargs):
        network = _write(kwargs['output_dir'] / 'context.net.xml', '<net/>')
        return {'status': 'pass', 'candidate_network': _artifact(network), 'field_geometry_review': 'review_required'}

    monkeypatch.setattr(workflow, 'rebuild_context_intersections', rebuild, raising=False)
    result = workflow.build_hamburg_topology_workflow(request_file=path, output_dir=tmp_path / 'run')
    assert result['status'] == 'review_required'
    assert result['stages']['context_intersections']['status'] == 'pass'
    assert Path(observed['combine']['source_net']['path']).name == 'context.net.xml'


def test_road_references_reach_the_movement_stage_with_exact_image_identity(tmp_path, monkeypatch):
    request = _request(tmp_path)
    value = json.loads(request.read_text(encoding="utf-8"))
    reference = _artifact(_write(tmp_path / 'road-reference.json', {'type': 'FeatureCollection', 'features': []}))
    value['intersections'][0]['road_reference'] = {'topology': reference, 'cross_sections': reference}
    _write(request, value)
    observed = _fake_stages(monkeypatch)
    def edges(**kwargs):
        assert kwargs['references']['topology']['sha256'] == reference['sha256']
        prior = _write(kwargs['output_dir'] / 'prior.npy', 'fake prior')
        report = dict(status='pass', decision='review_required', prior=_artifact(prior),
                      bbox_epsg25832=kwargs['bbox'], supported_boundary_chain_count=0)
        report_file = _write(kwargs['output_dir'] / 'road-edge-evidence.json', report)
        return {**report, 'report_file': str(report_file)}
    monkeypatch.setattr(workflow, 'build_hamburg_road_edge_evidence', edges, raising=False)
    result = workflow.build_hamburg_topology_workflow(request_file=request, output_dir=tmp_path / 'run')
    prior = observed['movements']['intersections'][0]['road_prior']
    assert prior['image_sha256'] == value['intersections'][0]['aerial_image']['sha256']
    assert prior['bbox_epsg25832'] == value['intersections'][0]['bbox_epsg25832']
    assert result['stages']['road_edges.1']['status'] == 'pass'
    assert result['checks']['road_edge_interpretation'] == 'review_required'
    assert result['status'] == 'review_required'


@pytest.mark.parametrize("change", ["source_hash", "old_plan", "calibration", "not_osm", "bad_horizon", "unknown_build_option", "one_intersection"])
def test_rejects_invalid_or_wrong_workflow_inputs_before_creating_output(tmp_path, change):
    request = _request(tmp_path)
    value = json.loads(request.read_text(encoding="utf-8"))
    if change == "source_hash":
        value["source_osm"]["sha256"] = "0" * 64
    elif change == "old_plan":
        value["movement_summary"] = _artifact(request)
    elif change == "calibration":
        value["counts_csv"] = "counts.csv"
    elif change == "not_osm":
        value["source_osm"] = _artifact(_write(tmp_path / "old.net.xml", "<net/>"))
    elif change == "bad_horizon":
        value["construction"]["simulation_max_end"] = 1
    elif change == "one_intersection":
        value["intersections"] = value["intersections"][:1]
    else:
        value["osm_build"]["unrecognized"] = True
    _write(request, value)
    with pytest.raises(ValueError):
        workflow.build_hamburg_topology_workflow(request_file=request, output_dir=tmp_path / "run")
    assert not (tmp_path / "run").exists()


@pytest.mark.parametrize(("complete", "geometry", "expected"), [(False, "pass", "review_required"), (True, "review_required", "review_required"), (True, "blocked", "blocked")])
def test_separates_connectivity_and_geometry_before_handoff(tmp_path, monkeypatch, complete, geometry, expected):
    _fake_stages(monkeypatch, topology_complete=complete, geometry_status=geometry)
    result = workflow.build_hamburg_topology_workflow(request_file=_request(tmp_path), output_dir=tmp_path / "run")
    assert result["status"] == expected
    assert result["topology_complete"] is complete
    assert result["network_handoff"]["construction_decision"] == expected
    assert result["calibration"]["status"] == "not_run"


def test_failure_records_first_failed_stage_and_does_not_reuse_previous_output(tmp_path, monkeypatch):
    observed = _fake_stages(monkeypatch)

    def failed(**kwargs):
        raise ValueError("official image does not cover the declared movement")

    monkeypatch.setattr(workflow, "build_hamburg_aerial_corridor_plan", failed)
    result = workflow.build_hamburg_topology_workflow(request_file=_request(tmp_path), output_dir=tmp_path / "run")
    assert result["status"] == "blocked"
    assert result["first_failed_stage"] == "movement_geometry"
    assert "combine" not in observed
    assert result["network_handoff"] is None
    assert "official image" in result["stages"]["movement_geometry"]["error"]
    assert Path(result["report_file"]).is_file()


def test_source_change_during_execution_blocks_handoff(tmp_path, monkeypatch):
    _fake_stages(monkeypatch)
    request = _request(tmp_path)
    original = workflow.run_candidate_movement_probes

    def changes_source(**kwargs):
        result = original(**kwargs)
        _write(tmp_path / "raw.osm.xml", '<osm version="changed"/>')
        return result

    monkeypatch.setattr(workflow, "run_candidate_movement_probes", changes_source)
    result = workflow.build_hamburg_topology_workflow(request_file=request, output_dir=tmp_path / "run")
    assert result["status"] == "blocked"
    assert result["inputs_unchanged"] is False
    assert result["network_handoff"]["construction_decision"] == "blocked"


def test_cli_exposes_separate_network_construction(monkeypatch, capsys):
    from astra_sumo import cli

    received = []

    def build(**kwargs):
        received.append(kwargs)
        return {"status": "review_required", "purpose": "network_construction"}

    monkeypatch.setattr(workflow, "build_hamburg_topology_workflow", build)
    assert cli.main(["hamburg", "build-network", "raw.json", "new-run", "--json"]) == 1
    assert received == [{"request_file": "raw.json", "output_dir": "new-run"}]
    assert json.loads(capsys.readouterr().out)["purpose"] == "network_construction"


def test_generated_source_network_stays_bound_to_its_first_identity(tmp_path, monkeypatch):
    observed = _fake_stages(monkeypatch)
    original = workflow.audit_tls

    def changes_generated_source(**kwargs):
        result = original(**kwargs)
        _write(kwargs["net_file"], '<net changed="after-build"/>')
        return result

    monkeypatch.setattr(workflow, "audit_tls", changes_generated_source)
    result = workflow.build_hamburg_topology_workflow(request_file=_request(tmp_path), output_dir=tmp_path / "run")
    assert result["status"] == "blocked"
    assert result["generated_inputs_unchanged"] is False
    assert "combine" not in observed


def test_later_audit_error_retains_the_constructed_network_facts(tmp_path, monkeypatch):
    _fake_stages(monkeypatch)

    def unavailable(*args, **kwargs):
        raise OSError("review binary unavailable")

    monkeypatch.setattr(workflow, "audit_hamburg_topology_candidate", unavailable)
    result = workflow.build_hamburg_topology_workflow(request_file=_request(tmp_path), output_dir=tmp_path / "run")
    assert result["status"] == "blocked"
    assert result["first_failed_stage"] == "construction_audit"
    assert result["topology_complete"] is True
    assert result["counts"]["official_vehicle_movements"] == 2
    assert result["network_handoff"]["construction_decision"] == "blocked"
def test_historical_source_period_reaches_movement_selection_without_new_aerial_prior(tmp_path, monkeypatch):
    request = _request(tmp_path)
    data = json.loads(request.read_text(encoding='utf-8'))
    data['source_osm']['data_year'] = 2013
    for row in data['intersections']:
        row['map_xml']['data_year'] = 2013
        row['aerial_year'] = 2026
    reference = _artifact(_write(tmp_path / 'reference.json', {}))
    data['intersections'][0]['road_reference'] = {'topology': reference, 'cross_sections': reference}
    _write(request, data)
    observed = _fake_stages(monkeypatch)
    def forbidden(**kwargs):
        raise AssertionError('A 2026 image must not supply a road prior for the 2013 scene')
    monkeypatch.setattr(workflow, 'build_hamburg_road_edge_evidence', forbidden)
    result = workflow.build_hamburg_topology_workflow(request_file=request, output_dir=tmp_path / 'run')
    assert result['source_policy']['target_year'] == 2013
    assert observed['movements']['target_year'] == 2013
    assert 'road_prior' not in observed['movements']['intersections'][0]
