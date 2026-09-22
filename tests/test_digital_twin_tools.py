from __future__ import annotations

import inspect
import json
from pathlib import Path

from torii_sumo.core.candidate_contracts import file_sha256
from torii_sumo.tools import digital_twin_tools
from torii_sumo.tools.digital_twin_tools import _torii_network_gate


def test_surface_overlap_tool_is_read_only_and_writes_optional_report(tmp_path: Path) -> None:
    net_file = tmp_path / "candidate.net.xml"
    net_file.write_text(
        '<net><junction id="only" type="priority" shape="0,0 2,0 2,2 0,2"/></net>',
        encoding="utf-8",
    )
    original = net_file.read_bytes()
    report_file = tmp_path / "audit" / "surface.json"

    report = digital_twin_tools.sumo_network_surface_overlap_audit(
        net_file=str(net_file),
        report_file=str(report_file),
    )

    assert report["status"] == "pass"
    assert report["source_network_mutation"] is False
    assert report["junction_junction_overlap_count"] == 0
    assert report_file.is_file()
    assert net_file.read_bytes() == original


def test_surface_overlap_comparison_preserves_global_and_bounded_semantics(tmp_path: Path) -> None:
    baseline_file = tmp_path / "baseline.net.xml"
    candidate_file = tmp_path / "candidate.net.xml"
    baseline_file.write_text(
        '<net><junction id="focus_a" type="priority" shape="0,0 2,0 2,2 0,2"/>'
        '<junction id="focus_b" type="priority" shape="1,1 3,1 3,3 1,3"/>'
        '<junction id="outside_a" type="priority" shape="10,0 12,0 12,2 10,2"/>'
        '<junction id="outside_b" type="priority" shape="11,1 13,1 13,3 11,3"/></net>',
        encoding="utf-8",
    )
    candidate_file.write_text(
        '<net><junction id="focus_joined" type="priority" shape="0,0 3,0 3,3 0,3"/>'
        '<junction id="outside_a" type="priority" shape="10,0 12,0 12,2 10,2"/>'
        '<junction id="outside_b" type="priority" shape="11,1 13,1 13,3 11,3"/></net>',
        encoding="utf-8",
    )
    original_baseline = baseline_file.read_bytes()
    original_candidate = candidate_file.read_bytes()
    output_dir = tmp_path / "comparison"

    report = digital_twin_tools.sumo_network_surface_overlap_comparison(
        baseline_net_file=str(baseline_file),
        candidate_net_file=str(candidate_file),
        focus_junction_ids=["focus_a", "focus_b", "focus_joined"],
        output_dir=str(output_dir),
    )

    assert report["status"] == "pass"
    assert report["baseline_audit"]["status"] == "fail"
    assert report["candidate_audit"]["status"] == "fail"
    assert report["comparison"]["introduced_finding_count"] == 0
    assert report["comparison"]["candidate_focus_finding_count"] == 0
    assert report["comparison"]["inherited_out_of_scope_finding_count"] == 1
    assert (output_dir / "baseline_surface_overlap_audit.json").is_file()
    assert (output_dir / "candidate_surface_overlap_audit.json").is_file()
    assert (output_dir / "bounded_surface_overlap_comparison.json").is_file()
    assert baseline_file.read_bytes() == original_baseline
    assert candidate_file.read_bytes() == original_candidate


def test_hamburg_2394_archetype_tool_is_read_only_and_path_validated(
    tmp_path: Path,
    monkeypatch,
) -> None:
    map_file = tmp_path / "2394-map.xml"
    ocit_file = tmp_path / "2394-ocit.xml"
    net_file = tmp_path / "corridor.net.xml"
    map_file.write_text("<map/>", encoding="utf-8")
    ocit_file.write_text("<ocit/>", encoding="utf-8")
    net_file.write_text("<net/>", encoding="utf-8")
    captured: dict[str, object] = {}

    def fake_build(**kwargs):
        captured.update(kwargs)
        return {
            "status": "review_required",
            "classification": {"base_skeleton": "T3"},
            "execution_hint": {"classification_only": True},
        }

    monkeypatch.setattr(
        digital_twin_tools,
        "build_hamburg_2394_archetype_profile",
        fake_build,
    )

    output_file = tmp_path / "2394-archetype.json"
    report = digital_twin_tools.sumo_hamburg_2394_archetype_classify(
        map_file=str(map_file),
        ocit_file=str(ocit_file),
        source_net_file=str(net_file),
        output_file=str(output_file),
    )

    assert report["classification"] == {"base_skeleton": "T3"}
    assert report["execution_hint"]["classification_only"] is True
    assert report["artifact_file"] == str(output_file.resolve())
    assert output_file.is_file()
    assert captured == {
        "map_file": map_file,
        "ocit_file": ocit_file,
        "source_net_file": net_file,
    }


def test_hamburg_2394_geometry_tool_requires_and_forwards_acceptance_gates(
    tmp_path: Path,
    monkeypatch,
) -> None:
    net_file = tmp_path / "frozen.net.xml"
    classification_file = tmp_path / "2394-archetype.json"
    net_file.write_text("<net/>", encoding="utf-8")
    classification_file.write_text("{}", encoding="utf-8")
    captured: dict[str, object] = {}

    def fake_materialize(**kwargs):
        captured.update(kwargs)
        return {
            "status": "review_ready",
            "claim_status": "geometry_first_pass_only",
            "automatic_promotion_gate": "blocked",
        }

    monkeypatch.setattr(
        digital_twin_tools,
        "materialize_hamburg_2394_compound_geometry_first_pass",
        fake_materialize,
    )
    source_hash = "a" * 64
    classification_hash = "b" * 64
    report = digital_twin_tools.sumo_hamburg_2394_compound_geometry_first_pass(
        source_net_file=str(net_file),
        classification_file=str(classification_file),
        accepted_classification_id="intersection-archetype-reviewed",
        expected_source_sha256=source_hash,
        expected_classification_sha256=classification_hash,
        output_dir=str(tmp_path / "candidate"),
        netconvert_binary="netconvert-test",
        timeout_seconds=123.0,
    )

    assert report["status"] == "review_ready"
    assert report["automatic_promotion_gate"] == "blocked"
    assert captured == {
        "source_net_file": net_file,
        "classification_report": classification_file,
        "accepted_classification_id": "intersection-archetype-reviewed",
        "expected_source_sha256": source_hash,
        "expected_classification_sha256": classification_hash,
        "output_dir": tmp_path / "candidate",
        "netconvert_binary": "netconvert-test",
        "timeout_seconds": 123.0,
    }

    parameters = inspect.signature(
        digital_twin_tools.sumo_hamburg_2394_compound_geometry_first_pass
    ).parameters
    for name in (
        "source_net_file",
        "classification_file",
        "accepted_classification_id",
        "expected_source_sha256",
        "expected_classification_sha256",
        "output_dir",
    ):
        assert parameters[name].default is inspect.Parameter.empty


def test_hamburg_2394_geometry_tool_fails_closed_before_materialization(
    tmp_path: Path,
    monkeypatch,
) -> None:
    net_file = tmp_path / "frozen.net.xml"
    net_file.write_text("<net/>", encoding="utf-8")
    called = False

    def fake_materialize(**_kwargs):
        nonlocal called
        called = True
        return {"status": "unexpected"}

    monkeypatch.setattr(
        digital_twin_tools,
        "materialize_hamburg_2394_compound_geometry_first_pass",
        fake_materialize,
    )
    report = digital_twin_tools.sumo_hamburg_2394_compound_geometry_first_pass(
        source_net_file=str(net_file),
        classification_file=str(tmp_path / "missing-classification.json"),
        accepted_classification_id="intersection-archetype-reviewed",
        expected_source_sha256="a" * 64,
        expected_classification_sha256="b" * 64,
        output_dir=str(tmp_path / "candidate"),
    )

    assert called is False
    assert report["status"] == "fail"
    assert report["automatic_promotion_gate"] == "blocked"
    assert report["official_tls_restoration"] == "not_run"


def test_hamburg_2394_tls_topology_tool_forwards_hash_bound_inputs(
    tmp_path: Path,
    monkeypatch,
) -> None:
    files = []
    for name in ("source.net.xml", "map.xml", "ocit.xml", "classification.json"):
        path = tmp_path / name
        path.write_text("{}", encoding="utf-8")
        files.append(path)
    plain_dir = tmp_path / "plain"
    plain_dir.mkdir()
    captured: dict[str, object] = {}

    def fake_materialize(**kwargs):
        captured.update(kwargs)
        return {"status": "review_ready", "automatic_promotion_gate": "blocked"}

    monkeypatch.setattr(
        digital_twin_tools,
        "materialize_hamburg_2394_tls_topology_candidate",
        fake_materialize,
    )
    hashes = {name: name[0] * 64 for name in ("source", "map", "ocit", "classification")}
    report = digital_twin_tools.sumo_hamburg_2394_tls_topology_materialize(
        source_net_file=str(files[0]),
        map_file=str(files[1]),
        ocit_file=str(files[2]),
        classification_file=str(files[3]),
        accepted_classification_id="intersection-archetype-reviewed",
        expected_source_sha256=hashes["source"],
        expected_map_sha256=hashes["map"],
        expected_ocit_sha256=hashes["ocit"],
        expected_classification_sha256=hashes["classification"],
        plain_source_dir=str(plain_dir),
        output_dir=str(tmp_path / "candidate"),
        netconvert_binary="netconvert-test",
        sumo_binary="sumo-test",
        timeout_seconds=123.0,
    )
    assert report["status"] == "review_ready"
    assert captured == {
        "source_net_file": files[0],
        "map_file": files[1],
        "ocit_file": files[2],
        "classification_file": files[3],
        "accepted_classification_id": "intersection-archetype-reviewed",
        "expected_source_sha256": hashes["source"],
        "expected_map_sha256": hashes["map"],
        "expected_ocit_sha256": hashes["ocit"],
        "expected_classification_sha256": hashes["classification"],
        "plain_source_dir": plain_dir,
        "output_dir": tmp_path / "candidate",
        "netconvert_binary": "netconvert-test",
        "sumo_binary": "sumo-test",
        "timeout_seconds": 123.0,
    }


def test_torii_network_gate_allows_only_structurally_sound_review_pending_build(tmp_path: Path) -> None:
    net_file = tmp_path / "corridor.net.xml"
    net_file.write_text("<net/>", encoding="utf-8")
    report = {
        "status": "fail",
        "net_file": str(net_file),
        "gate_status": {
            "area_confirmation": "pass",
            "road_level_scope": "pass",
            "network_build": "pass",
            "connectivity": "pass",
            "routeability_audit": "pass",
            "tls_reality_audit": "blocked",
            "topology_audit": "blocked",
        },
    }

    gate = _torii_network_gate(report)

    assert gate["status"] == "provisional"
    assert gate["delegated_review_gates"] == {
        "tls_reality_audit": "blocked",
        "topology_audit": "blocked",
    }


def test_torii_network_gate_blocks_a_failed_routeability_gate(tmp_path: Path) -> None:
    net_file = tmp_path / "corridor.net.xml"
    net_file.write_text("<net/>", encoding="utf-8")
    report = {
        "status": "fail",
        "net_file": str(net_file),
        "gate_status": {
            "area_confirmation": "pass",
            "road_level_scope": "pass",
            "network_build": "pass",
            "connectivity": "pass",
            "routeability_audit": "fail",
        },
    }

    gate = _torii_network_gate(report)

    assert gate["status"] == "blocked"
    assert gate["failed_required_gates"] == {"routeability_audit": "fail"}


def test_hamburg_product_defaults_to_a_30_minute_warmup(
    tmp_path: Path,
    monkeypatch,
) -> None:
    net_file = tmp_path / "corridor.net.xml"
    net_file.write_text("<net/>", encoding="utf-8")
    captured: dict[str, object] = {}

    def fake_prepare(**kwargs):
        captured.update(kwargs)
        return {"status": "partial", "gaps": []}

    monkeypatch.setattr(digital_twin_tools, "prepare_hamburg_corridor_digital_twin", fake_prepare)

    report = digital_twin_tools.sumo_hamburg_sandtorkai_digital_twin(
        output_dir=str(tmp_path / "output"),
        net_file=str(net_file),
        run_route_sampler_step=False,
    )

    assert report["status"] == "partial"
    assert captured["warmup_seconds"] == 1800
    assert captured["compact_corridor_scope"] is True
    assert captured["corridor_buffer_m"] == 25.0
    assert captured["intersection_stub_radius_m"] == 80.0


def test_official_tls_rebuild_tool_uses_frozen_net_and_cached_assets(
    tmp_path: Path,
    monkeypatch,
) -> None:
    net_file = tmp_path / "frozen.net.xml"
    net_file.write_text("<net/>", encoding="utf-8")
    asset_dir = tmp_path / "assets"
    asset_dir.mkdir()
    captured: dict[str, object] = {}

    def fake_rebuild(**kwargs):
        captured.update(kwargs)
        return {"status": "pass", "validated_primary_stream_count": 27}

    monkeypatch.setattr(
        digital_twin_tools,
        "rebuild_hamburg_sandtorkai_official_tls",
        fake_rebuild,
    )

    report = digital_twin_tools.sumo_hamburg_official_tls_rebuild(
        source_net_file=str(net_file),
        signal_asset_dir=str(asset_dir),
        output_dir=str(tmp_path / "output"),
        netconvert_binary="netconvert-test",
        timeout_seconds=123.0,
    )

    assert report["status"] == "pass"
    assert captured == {
        "source_net_file": net_file,
        "signal_asset_dir": asset_dir,
        "output_dir": tmp_path / "output",
        "netconvert_binary": "netconvert-test",
        "sumo_binary": "sumo",
        "timeout_seconds": 123.0,
    }


def test_cached_detector_demand_tool_reuses_frozen_evidence(
    tmp_path: Path,
    monkeypatch,
) -> None:
    manifest = tmp_path / "official-tls.manifest.json"
    streams = tmp_path / "count-streams.json"
    counts = tmp_path / "canonical-counts.csv"
    for path in (manifest, streams, counts):
        path.write_text("{}", encoding="utf-8")
    captured: dict[str, object] = {}

    def fake_prepare(**kwargs):
        captured.update(kwargs)
        return {"status": "partial", "demand_generation_status": "pass"}

    monkeypatch.setattr(
        digital_twin_tools,
        "prepare_cached_detector_demand_package",
        fake_prepare,
    )

    report = digital_twin_tools.sumo_hamburg_cached_detector_demand(
        official_tls_manifest=str(manifest),
        count_stream_snapshot=str(streams),
        canonical_count_file=str(counts),
        output_dir=str(tmp_path / "output"),
        excluded_route_edges=["158068424"],
        route_sampler_script=str(tmp_path / "routeSampler.py"),
        allow_detector_cross_section_boundaries=True,
    )

    assert report["demand_generation_status"] == "pass"
    assert captured["official_tls_manifest"] == manifest
    assert captured["count_stream_snapshot"] == streams
    assert captured["canonical_count_file"] == counts
    assert captured["output_dir"] == tmp_path / "output"
    assert captured["excluded_route_edges"] == ["158068424"]
    assert captured["route_sampler_script"] == tmp_path / "routeSampler.py"
    assert captured["comparison_begin"] == 1800
    assert captured["comparison_end"] == 9000
    assert captured["allow_detector_cross_section_boundaries"] is True


def test_corridor_candidate_detector_demand_tool_reuses_hash_bound_inputs(
    tmp_path: Path,
    monkeypatch,
) -> None:
    candidate_manifest = tmp_path / "candidate.manifest.json"
    candidate_net = tmp_path / "candidate.net.xml"
    map_files = [tmp_path / "0228.xml", tmp_path / "2421.xml", tmp_path / "2394.xml"]
    binding = tmp_path / "bindings.csv"
    streams = tmp_path / "count-streams.json"
    counts = tmp_path / "canonical-counts.csv"
    for path in (candidate_manifest, candidate_net, binding, streams, counts, *map_files):
        path.write_text("{}", encoding="utf-8")
    captured: dict[str, object] = {}

    def fake_prepare(**kwargs):
        captured.update(kwargs)
        return {"status": "partial", "demand_generation_status": "pass"}

    monkeypatch.setattr(
        digital_twin_tools,
        "prepare_corridor_candidate_detector_demand_package",
        fake_prepare,
    )

    report = digital_twin_tools.sumo_hamburg_corridor_candidate_detector_demand(
        candidate_manifest=str(candidate_manifest),
        candidate_net_file=str(candidate_net),
        expected_candidate_net_sha256="candidate-sha",
        map_xml_files=[str(path) for path in map_files],
        map_lane_binding_file=str(binding),
        expected_map_lane_binding_sha256="binding-sha",
        count_stream_snapshot=str(streams),
        canonical_count_file=str(counts),
        output_dir=str(tmp_path / "output"),
        route_sampler_script=str(tmp_path / "routeSampler.py"),
    )

    assert report["demand_generation_status"] == "pass"
    assert captured["candidate_manifest"] == candidate_manifest
    assert captured["candidate_net_file"] == candidate_net
    assert captured["map_xml_files"] == map_files
    assert captured["map_lane_binding_file"] == binding
    assert captured["count_stream_snapshot"] == streams
    assert captured["canonical_count_file"] == counts
    assert captured["output_dir"] == tmp_path / "output"
    assert captured["route_sampler_script"] == tmp_path / "routeSampler.py"


def test_corridor_candidate_map_binding_tool_reprojects_frozen_maps(
    tmp_path: Path,
    monkeypatch,
) -> None:
    manifest = tmp_path / "candidate.manifest.json"
    net = tmp_path / "candidate.net.xml"
    maps = [tmp_path / "0228.xml", tmp_path / "2421.xml", tmp_path / "2394.xml"]
    for path in (manifest, net, *maps):
        path.write_text("{}", encoding="utf-8")
    captured: dict[str, object] = {}

    def fake_materialize(**kwargs):
        captured.update(kwargs)
        return {"status": "pass", "claim_status": "candidate-map-lane-binding-review"}

    monkeypatch.setattr(
        digital_twin_tools,
        "materialize_hamburg_corridor_candidate_map_bindings",
        fake_materialize,
    )

    report = digital_twin_tools.sumo_hamburg_corridor_candidate_map_bindings(
        candidate_manifest=str(manifest),
        candidate_net_file=str(net),
        expected_candidate_net_sha256="candidate-sha",
        map_xml_files=[str(path) for path in maps],
        output_dir=str(tmp_path / "output"),
    )

    assert report["status"] == "pass"
    assert captured["candidate_manifest"] == manifest
    assert captured["candidate_net_file"] == net
    assert captured["map_xml_files"] == maps
    assert captured["output_dir"] == tmp_path / "output"


def test_corridor_candidate_signal_binding_tool_uses_candidate_map_contract(
    tmp_path: Path,
    monkeypatch,
) -> None:
    manifest = tmp_path / "candidate.manifest.json"
    net = tmp_path / "candidate.net.xml"
    bindings = tmp_path / "candidate-bindings.csv"
    streams = tmp_path / "signal-streams.csv"
    for path in (manifest, net, bindings, streams):
        path.write_text("{}", encoding="utf-8")
    captured: dict[str, object] = {}

    def fake_materialize(**kwargs):
        captured.update(kwargs)
        return {"status": "pass", "claim_status": "candidate-signal-binding-review"}

    monkeypatch.setattr(
        digital_twin_tools,
        "materialize_hamburg_corridor_candidate_signal_bindings",
        fake_materialize,
    )

    report = digital_twin_tools.sumo_hamburg_corridor_candidate_signal_bindings(
        candidate_manifest=str(manifest),
        candidate_net_file=str(net),
        expected_candidate_net_sha256="candidate-sha",
        map_lane_binding_file=str(bindings),
        expected_map_lane_binding_sha256="binding-sha",
        signal_stream_file=str(streams),
        output_dir=str(tmp_path / "output"),
    )

    assert report["status"] == "pass"
    assert captured["candidate_manifest"] == manifest
    assert captured["candidate_net_file"] == net
    assert captured["map_lane_binding_file"] == bindings
    assert captured["signal_stream_file"] == streams
    assert captured["output_dir"] == tmp_path / "output"


def test_corridor_candidate_package_tool_runs_hash_bound_stages(
    tmp_path: Path,
    monkeypatch,
) -> None:
    manifest = tmp_path / "candidate.manifest.json"
    net = tmp_path / "candidate.net.xml"
    maps = [tmp_path / "0228.xml", tmp_path / "2421.xml", tmp_path / "2394.xml"]
    signal_streams = tmp_path / "signal-streams.csv"
    counts = tmp_path / "counts.json"
    canonical = tmp_path / "counts.csv"
    for path in (manifest, net, signal_streams, counts, canonical, *maps):
        path.write_text("{}", encoding="utf-8")
    captured: dict[str, object] = {}

    def fake_prepare(**kwargs):
        captured.update(kwargs)
        return {"status": "partial", "claim_status": "candidate-corridor-package-review"}

    monkeypatch.setattr(
        digital_twin_tools,
        "prepare_hamburg_corridor_candidate_package",
        fake_prepare,
    )

    report = digital_twin_tools.sumo_hamburg_sandtorkai_corridor_candidate_package(
        candidate_manifest=str(manifest),
        candidate_net_file=str(net),
        expected_candidate_net_sha256="candidate-sha",
        map_xml_files=[str(path) for path in maps],
        signal_stream_file=str(signal_streams),
        count_stream_snapshot=str(counts),
        canonical_count_file=str(canonical),
        output_dir=str(tmp_path / "output"),
        route_sampler_script=str(tmp_path / "routeSampler.py"),
    )

    assert report["status"] == "partial"
    assert captured["candidate_manifest"] == manifest
    assert captured["candidate_net_file"] == net
    assert captured["map_xml_files"] == maps
    assert captured["signal_stream_file"] == signal_streams
    assert captured["count_stream_snapshot"] == counts
    assert captured["canonical_count_file"] == canonical
    assert captured["output_dir"] == tmp_path / "output"


def test_named_count_scope_tool_validates_identity_and_forwards_window(
    tmp_path: Path,
    monkeypatch,
) -> None:
    identity = tmp_path / "lsa.json"
    identity.write_text("{}", encoding="utf-8")
    captured: dict[str, object] = {}

    def fake_load(path, *, expected_node_ids):
        captured["identity"] = path
        captured["expected_node_ids"] = expected_node_ids
        return {"2349": object()}

    def fake_materialize(**kwargs):
        captured.update(kwargs)
        return {"status": "partial", "automatic_promotion_gate": "blocked"}

    monkeypatch.setattr(digital_twin_tools, "load_lsa_node_references", fake_load)
    monkeypatch.setattr(digital_twin_tools, "materialize_hamburg_named_count_scope", fake_materialize)

    report = digital_twin_tools.sumo_hamburg_named_count_scope(
        lsa_identity_file=str(identity),
        count_node_ids=["2349"],
        scope_id="named-scope",
        output_dir=str(tmp_path / "output"),
        saturday_date="2026-07-18",
        warmup_seconds=1800,
    )

    assert report["status"] == "partial"
    assert captured["identity"] == identity
    assert captured["expected_node_ids"] == ["2349"]
    assert captured["requested_count_node_ids"] == ["2349"]
    assert captured["scope_id"] == "named-scope"
    assert captured["output_dir"] == tmp_path / "output"
    assert captured["saturday_date"].isoformat() == "2026-07-18"


def test_execution_plan_tool_parses_stage_manifest_entries(tmp_path: Path) -> None:
    stage = tmp_path / "w0.json"
    stage.write_text(
        '{"schema":"torii.hamburg-named-corridor-scope/v1","status":"pass",'
        '"automatic_promotion_gate":"pass"}',
        encoding="utf-8",
    )

    report = digital_twin_tools.sumo_hamburg_sandtorkai_execution_plan(
        output_dir=str(tmp_path / "plan"),
        stage_manifests=[f"W0={stage}"],
    )

    assert report["workflow_id"] == "hamburg_sandtorkai_2349_2394_2403"
    assert report["stages"]["W0"]["decision"] == "pass"
    assert report["next_action"]["stage_id"] == "W1"
    assert report["next_action"]["status"] == "ready"


def test_execution_plan_tool_attaches_stage_feedback_without_promoting(tmp_path: Path) -> None:
    network = tmp_path / "candidate.net.xml"
    network.write_text("<net/>\n", encoding="utf-8")
    network_binding = {"path": str(network.resolve()), "sha256": file_sha256(network)}
    w0 = tmp_path / "w0.json"
    w0.write_text(
        '{"schema":"torii.hamburg-named-corridor-scope/v1","status":"pass",'
        '"automatic_promotion_gate":"pass"}',
        encoding="utf-8",
    )
    w1 = tmp_path / "w1.json"
    w1.write_text(
        json.dumps(
            {
                "schema": "torii.hamburg-official-corridor-geometry/v1",
                "status": "pass",
                "automatic_promotion_gate": "pass",
                "network": network_binding,
            }
        ),
        encoding="utf-8",
    )
    w3a = tmp_path / "w3a.json"
    count_streams = tmp_path / "count_streams.raw.json"
    count_streams.write_text('{"streams":[]}\n', encoding="utf-8")
    count_stream_binding = {
        "path": str(count_streams.resolve()),
        "sha256": file_sha256(count_streams),
    }
    simulation_counts = tmp_path / "counts.simulation.15min.csv"
    simulation_counts.write_text("stream_id,begin,end,total\n", encoding="utf-8")
    simulation_count_binding = {
        "path": str(simulation_counts.resolve()),
        "sha256": file_sha256(simulation_counts),
    }
    w3a.write_text(
        json.dumps(
            {
                "schema": "torii.hamburg-named-corridor-count-scope/v1",
                "status": "pass",
                "automatic_promotion_gate": "pass",
                "artifacts": {
                    "count_streams_raw": count_stream_binding,
                    "counts_simulation_15min": simulation_count_binding,
                },
            }
        ),
        encoding="utf-8",
    )
    detector_mapping = tmp_path / "detector_mapping.csv"
    detector_mapping.write_text("stream_id,sumo_lane\n", encoding="utf-8")
    w3b = tmp_path / "w3b.json"
    w3b.write_text(
        json.dumps(
            {
                "schema": "torii.hamburg-named-detector-binding/v1",
                "status": "pass",
                "automatic_promotion_gate": "pass",
                "source": {
                    "candidate_net": network_binding,
                    "count_stream_snapshot": count_stream_binding,
                },
                "artifacts": {
                    "detector_mapping": {
                        "path": str(detector_mapping.resolve()),
                        "sha256": file_sha256(detector_mapping),
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    stage = tmp_path / "w2.json"
    stage.write_text(
        json.dumps(
            {
                "schema": "torii.hamburg-named-signal-binding/v1",
                "status": "blocked",
                "execution_gate": "blocked",
                "automatic_promotion_gate": "blocked",
                "source": {"candidate_net": network_binding},
            }
        ),
        encoding="utf-8",
    )
    feedback = tmp_path / "w2b.json"
    feedback.write_text(
        '{"publication_gap":{"decision":"confirmed_official_node_without_published_tld_binding"}}',
        encoding="utf-8",
    )

    report = digital_twin_tools.sumo_hamburg_sandtorkai_execution_plan(
        output_dir=str(tmp_path / "plan"),
        stage_manifests=[
            f"W0={w0}",
            f"W1={w1}",
            f"W3a={w3a}",
            f"W2={stage}",
            f"W3b={w3b}",
        ],
        stage_feedback=[f"W2={feedback}"],
    )

    assert report["stages"]["W2"]["execution_gate"] == "blocked"
    assert report["replan"]["feedback"]["publication_gap"]["decision"] == (
        "confirmed_official_node_without_published_tld_binding"
    )


def test_named_replay_tool_forwards_hash_bound_inputs(tmp_path: Path, monkeypatch) -> None:
    files = {}
    for name in (
        "net.xml",
        "binding.json",
        "detector-binding.json",
        "count-scope.json",
        "streams.json",
        "counts.csv",
        "routeSampler.py",
    ):
        path = tmp_path / name
        path.write_text("fixture", encoding="utf-8")
        files[name] = path
    captured: dict[str, object] = {}

    def fake_materialize(**kwargs):
        captured.update(kwargs)
        return {"status": "blocked", "execution_gate": "blocked"}

    monkeypatch.setattr(digital_twin_tools, "materialize_hamburg_named_replay", fake_materialize)

    report = digital_twin_tools.sumo_hamburg_sandtorkai_named_replay(
        net_file=str(files["net.xml"]),
        signal_binding_manifest=str(files["binding.json"]),
        detector_binding_manifest=str(files["detector-binding.json"]),
        count_scope_manifest=str(files["count-scope.json"]),
        count_stream_snapshot=str(files["streams.json"]),
        canonical_count_file=str(files["counts.csv"]),
        output_dir=str(tmp_path / "replay"),
        route_sampler_script=str(files["routeSampler.py"]),
        simulation_begin=0,
        simulation_end=7200,
        comparison_begin=0,
        comparison_end=7200,
        interval=900,
        allow_detector_cross_section_boundaries=True,
    )

    assert report["execution_gate"] == "blocked"
    assert captured["net_file"] == files["net.xml"]
    assert captured["signal_binding_manifest"] == files["binding.json"]
    assert captured["detector_binding_manifest"] == files["detector-binding.json"]
    assert captured["count_scope_manifest"] == files["count-scope.json"]
    assert captured["count_stream_snapshot"] == files["streams.json"]
    assert captured["canonical_count_file"] == files["counts.csv"]
    assert captured["route_sampler_script"] == files["routeSampler.py"]
    assert captured["output_dir"] == tmp_path / "replay"
    assert captured["simulation_begin"] == 0
    assert captured["simulation_end"] == 7200
    assert captured["comparison_begin"] == 0
    assert captured["comparison_end"] == 7200
    assert captured["interval"] == 900
    assert captured["allow_detector_cross_section_boundaries"] is True


def test_signal_observation_tool_forwards_utc_window_and_fetch_options(tmp_path: Path, monkeypatch) -> None:
    binding = tmp_path / "binding.json"
    binding.write_text("{}", encoding="utf-8")
    captured: dict[str, object] = {}

    def fake_materialize(**kwargs):
        captured.update(kwargs)
        return {"status": "blocked", "execution_gate": "blocked"}

    monkeypatch.setattr(digital_twin_tools, "materialize_hamburg_named_signal_observations", fake_materialize)

    report = digital_twin_tools.sumo_hamburg_sandtorkai_signal_observations(
        binding_manifest=str(binding),
        begin_utc="2026-07-18T14:30:00Z",
        end_utc="2026-07-18T16:30:00Z",
        output_dir=str(tmp_path / "signals"),
        cache_dir=str(tmp_path / "cache"),
        preceding_lookback_hours=2.0,
        chunk_minutes=5.0,
        max_retries=1,
        max_workers=2,
        timeout_seconds=12.0,
    )

    assert report["execution_gate"] == "blocked"
    assert captured["binding_manifest"] == binding
    assert captured["output_dir"] == tmp_path / "signals"
    assert captured["cache_dir"] == tmp_path / "cache"
    assert captured["begin_utc"].isoformat() == "2026-07-18T14:30:00+00:00"
    assert captured["end_utc"].isoformat() == "2026-07-18T16:30:00+00:00"
    assert captured["retry_incomplete_cache"] is True
