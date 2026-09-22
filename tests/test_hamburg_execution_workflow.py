from __future__ import annotations

import json
from pathlib import Path

import pytest

from torii_sumo.core.candidate_contracts import file_sha256
from torii_sumo.core.hamburg_execution_workflow import (
    HAMBURG_EXECUTION_CONFIG_SCHEMA,
    HAMBURG_EXECUTION_WORKFLOW_SCHEMA,
    HamburgExecutionWorkflowError,
    materialize_hamburg_execution_plan,
    materialize_hamburg_execution_plan_from_config,
    materialize_hamburg_w1_topology_handoff,
)


_STAGE_SCHEMAS = {
    "W0": "torii.hamburg-named-corridor-scope/v1",
    "W1": "torii.hamburg-official-corridor-geometry/v1",
    "W2": "torii.hamburg-named-signal-binding/v1",
    "W3a": "torii.hamburg-named-corridor-count-scope/v1",
    "W3b": "torii.hamburg-named-detector-binding/v1",
    "W4": "torii.hamburg-named-replay/v2",
}


def _write_manifest(
    path: Path,
    stage_id: str,
    *,
    status: str = "pass",
    gate: str = "pass",
    execution_gate: str | None = None,
    network_file: Path | None = None,
) -> None:
    payload: dict[str, object] = {
        "schema": _STAGE_SCHEMAS[stage_id],
        "status": status,
        "automatic_promotion_gate": gate,
    }
    if execution_gate is not None:
        payload["execution_gate"] = execution_gate
    count_streams = path.parent / "count_streams.raw.json"
    simulation_counts = path.parent / "counts.simulation.15min.csv"
    detector_mapping = path.parent / "detector_mapping.csv"
    if stage_id == "W3a":
        if not count_streams.exists():
            count_streams.write_text('{"streams":[]}\n', encoding="utf-8")
        if not simulation_counts.exists():
            simulation_counts.write_text("stream_id,begin,end,total\n", encoding="utf-8")
        payload["artifacts"] = {
            "count_streams_raw": {
                "path": str(count_streams.resolve()),
                "sha256": file_sha256(count_streams),
            },
            "counts_simulation_15min": {
                "path": str(simulation_counts.resolve()),
                "sha256": file_sha256(simulation_counts),
            },
        }
    elif stage_id == "W3b":
        if not detector_mapping.exists():
            detector_mapping.write_text("stream_id,sumo_lane\n", encoding="utf-8")
        payload["gates"] = {"sensor_aggregation_semantics": "pass"}
        payload["artifacts"] = {
            "detector_mapping": {
                "path": str(detector_mapping.resolve()),
                "sha256": file_sha256(detector_mapping),
            }
        }
    if stage_id in {"W1", "W2", "W3b", "W4"}:
        network = network_file or path.parent / "candidate.net.xml"
        if not network.exists():
            network.write_text("<net/>\n", encoding="utf-8")
        binding = {"path": str(network.resolve()), "sha256": file_sha256(network)}
        if stage_id == "W1":
            payload["network"] = binding
        elif stage_id in {"W2", "W3b"}:
            source = {"candidate_net": binding}
            if stage_id == "W3b" and count_streams.is_file():
                source["count_stream_snapshot"] = {
                    "path": str(count_streams.resolve()),
                    "sha256": file_sha256(count_streams),
                }
            payload["source"] = source
        else:
            source = {"net": binding}
            if count_streams.is_file():
                source["count_stream_snapshot"] = {
                    "path": str(count_streams.resolve()),
                    "sha256": file_sha256(count_streams),
                }
            if simulation_counts.is_file():
                source["canonical_count_file"] = {
                    "path": str(simulation_counts.resolve()),
                    "sha256": file_sha256(simulation_counts),
                }
            for name, dependency in (
                ("signal_binding_manifest", "W2"),
                ("detector_binding_manifest", "W3b"),
                ("count_scope_manifest", "W3a"),
            ):
                dependency_path = path.with_name(f"{dependency}.json")
                if dependency_path.is_file():
                    source[name] = {
                        "path": str(dependency_path.resolve()),
                        "sha256": file_sha256(dependency_path),
                    }
            payload["source"] = source
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_w1_topology_handoff_is_hash_bound_and_non_promoting(tmp_path: Path) -> None:
    candidate = tmp_path / "candidate.net.xml"
    candidate.write_text("<net/>", encoding="utf-8")
    candidate_hash = file_sha256(candidate)
    source = tmp_path / "source.net.xml"
    source.write_text("<net/>", encoding="utf-8")

    def write(name: str, payload: dict[str, object]) -> Path:
        path = tmp_path / name
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    topology = write(
        "topology.json",
        {
            "schema": "torii.junction-aggregation-preservation/v1",
            "status": "pass",
            "source_net_file": str(source.resolve()),
            "source_sha256": file_sha256(source),
            "variant_net_file": str(candidate.resolve()),
            "variant_sha256": candidate_hash,
            "unexpected_removed_normal_edge_count": 0,
            "lost_shared_connection_count": 0,
            "new_dangling_shared_normal_edge_count": 0,
            "boundary_movement_preservation": {
                "status": "pass",
                "lost_boundary_movement_count": 0,
                "added_boundary_movement_count": 0,
                "groups": [
                    {
                        "variant_boundary_movement_count": 2,
                        "variant_boundary_movements": ["in|0|out|0", "in|1|out|1"],
                    }
                ],
            },
        },
    )
    surface = write(
        "surface.json",
        {
            "schema": "torii.sumo-surface-overlap-audit/v1",
            "audit_engine": "torii.bevel-strip-and-junction-polygon-area/v2",
            "status": "pass",
            "source_net_file": str(candidate.resolve()),
            "source_sha256": candidate_hash,
            "source_network_mutation": False,
            "geometry_error_count": 0,
            "junction_junction_overlap_count": 0,
            "external_lane_non_owner_junction_overlap_count": 0,
        },
    )
    connection = write(
        "connection.json",
        {
            "schema": "torii.connection_mode_regression_manifest.v1",
            "status": "pass",
            "gate_status": "pass",
            "automatic_promotion_gate": "pass",
            "candidate_net_file": str(candidate.resolve()),
            "candidate_sha256": candidate_hash,
            "source_network_mutation": False,
        },
    )
    load = write(
        "load.json",
        {
            "schema": "torii.sumo-load-audit/v1",
            "status": "pass",
            "source_net_file": str(candidate.resolve()),
            "source_sha256": candidate_hash,
            "source_network_mutation": False,
        },
    )
    route = tmp_path / "smoke.rou.xml"
    summary = tmp_path / "summary.xml"
    tripinfo = tmp_path / "tripinfo.xml"
    for path in (route, summary, tripinfo):
        path.write_text("evidence", encoding="utf-8")
    smoke = write(
        "smoke.json",
        {
            "schema": "torii.hamburg-2403-movement-smoke/v1",
            "status": "pass",
            "candidate_net_file": str(candidate.resolve()),
            "candidate_sha256": candidate_hash,
            "inputs": {
                "route": {"path": str(route), "sha256": file_sha256(route)},
                "preservation_audit": {"path": str(topology), "sha256": file_sha256(topology)},
            },
            "outputs": {
                "summary": {"path": str(summary), "sha256": file_sha256(summary)},
                "tripinfo": {"path": str(tripinfo), "sha256": file_sha256(tripinfo)},
            },
            "vehicle_count": 2,
            "movement_count": 2,
            "movement_keys": ["in|0|out|0", "in|1|out|1"],
            "movement_keys_unique": True,
            "movement_keys_match_preservation": True,
            "loaded": 2,
            "inserted": 2,
            "ended": 2,
            "running": 0,
            "waiting": 0,
            "teleports": 0,
            "collisions": 0,
            "inspection": {
                "status": "pass",
                "summary": {
                    "loaded": 2,
                    "inserted": 2,
                    "arrived": 2,
                    "running": 0,
                    "waiting": 0,
                    "teleports": 0,
                    "collisions": 0,
                },
                "tripinfo": {"trip_count": 2},
            },
        },
    )
    review_files = []
    for junction_id in ("a", "b"):
        review_files.append(
            write(
                f"review-{junction_id}.json",
                {
                    "schema": "torii.netedit-background-review.direct/v1",
                    "status": "review_material_ready",
                    "automatic_promotion_gate": "blocked",
                    "candidate_file": str(candidate.resolve()),
                    "candidate_sha256_before": candidate_hash,
                    "candidate_sha256_after": candidate_hash,
                    "candidate_unchanged": True,
                    "target_junction": {"id": junction_id},
                    "mode_images_distinct": True,
                    "global_keyboard_or_mouse_input_used": False,
                    "foreground_context_restored": True,
                },
            )
        )

    bad = json.loads(review_files[0].read_text(encoding="utf-8"))
    bad["candidate_sha256_after"] = "wrong"
    review_files[0].write_text(json.dumps(bad), encoding="utf-8")
    with pytest.raises(HamburgExecutionWorkflowError, match="W1 topology handoff rejected"):
        materialize_hamburg_w1_topology_handoff(
            output_dir=tmp_path / "bad",
            candidate_net_file=candidate,
            topology_audit_file=topology,
            surface_comparison_file=surface,
            connection_mode_manifest_file=connection,
            sumo_load_report_file=load,
            movement_smoke_file=smoke,
            netedit_review_files=review_files,
            expected_review_junction_ids=("a", "b"),
        )

    bad["candidate_sha256_after"] = candidate_hash
    review_files[0].write_text(json.dumps(bad), encoding="utf-8")
    with pytest.raises(HamburgExecutionWorkflowError, match="NetEdit review owners are empty"):
        materialize_hamburg_w1_topology_handoff(
            output_dir=tmp_path / "empty-review",
            candidate_net_file=candidate,
            topology_audit_file=topology,
            surface_comparison_file=surface,
            connection_mode_manifest_file=connection,
            sumo_load_report_file=load,
            movement_smoke_file=smoke,
            netedit_review_files=(),
            expected_review_junction_ids=(),
        )
    report = materialize_hamburg_w1_topology_handoff(
        output_dir=tmp_path / "good",
        candidate_net_file=candidate,
        topology_audit_file=topology,
        surface_comparison_file=surface,
        connection_mode_manifest_file=connection,
        sumo_load_report_file=load,
        movement_smoke_file=smoke,
        netedit_review_files=review_files,
        expected_review_junction_ids=("a", "b"),
    )

    assert report["status"] == "review_ready"
    assert report["execution_gate"] == "pass"
    assert report["automatic_promotion_gate"] == "blocked"
    assert report["netedit_review"]["junction_ids"] == ["a", "b"]
    assert report["routeability"]["movement_count"] == 2


def test_counts_and_detector_binding_are_independent_real_stages(tmp_path: Path) -> None:
    network = tmp_path / "candidate.net.xml"
    network.write_text("<net/>\n", encoding="utf-8")
    manifests = {}
    for stage_id in ("W0", "W1"):
        path = tmp_path / f"{stage_id}.json"
        _write_manifest(path, stage_id, network_file=network)
        manifests[stage_id] = path
    w2 = tmp_path / "W2.json"
    _write_manifest(
        w2,
        "W2",
        status="blocked",
        gate="blocked",
        execution_gate="blocked",
        network_file=network,
    )
    manifests["W2"] = w2

    first = materialize_hamburg_execution_plan(
        output_dir=tmp_path / "plan",
        stage_manifests=manifests,
    )
    assert first["stages"]["W3a"]["readiness"] == "ready"
    assert first["stages"]["W3b"]["readiness"] == "blocked"
    assert first["next_action"]["stage_id"] == "W3a"

    w3a = tmp_path / "W3a.json"
    _write_manifest(w3a, "W3a")
    manifests["W3a"] = w3a
    second = materialize_hamburg_execution_plan(
        output_dir=tmp_path / "plan",
        stage_manifests=manifests,
    )
    assert second["stages"]["W3a"]["readiness"] == "complete"
    assert second["stages"]["W3b"]["readiness"] == "ready"
    assert second["next_action"]["stage_id"] == "W3b"

    w3b = tmp_path / "W3b.json"
    _write_manifest(w3b, "W3b", network_file=network)
    manifests["W3b"] = w3b
    third = materialize_hamburg_execution_plan(
        output_dir=tmp_path / "plan",
        stage_manifests=manifests,
    )
    assert third["next_action"]["stage_id"] == "W2"
    assert third["next_action"]["action"] == "resolve_stage_gate"


def test_complete_inputs_generate_internal_w5_package(tmp_path: Path) -> None:
    network = tmp_path / "candidate.net.xml"
    network.write_text("<net/>\n", encoding="utf-8")
    manifests = {}
    for stage_id in ("W0", "W1", "W3a", "W2", "W3b", "W4"):
        path = tmp_path / f"{stage_id}.json"
        _write_manifest(path, stage_id, network_file=network)
        manifests[stage_id] = path

    plan = materialize_hamburg_execution_plan(
        output_dir=tmp_path / "plan",
        stage_manifests=manifests,
    )

    assert plan["schema"] == HAMBURG_EXECUTION_WORKFLOW_SCHEMA
    assert plan["next_action"] == {"stage_id": None, "status": "complete", "action": "none"}
    assert plan["stages"]["W5"]["generated"] is True
    assert plan["stages"]["W5"]["status"] == "complete"
    assert plan["stages"]["W5"]["summarized_capabilities"] == list(plan["capabilities"])
    assert plan["promotion"]["decision"] == "pass"
    assert plan["capabilities"]["road_topology"]["status"] == "pass"
    assert list(plan["stages"]) == ["W0", "W1", "W3a", "W2", "W3b", "W4", "W5"]


def test_execution_complete_does_not_override_blocked_promotion_gates(tmp_path: Path) -> None:
    network = tmp_path / "candidate.net.xml"
    network.write_text("<net/>\n", encoding="utf-8")
    manifests = {}
    for stage_id in ("W0", "W1", "W3a", "W2", "W3b", "W4"):
        path = tmp_path / f"{stage_id}.json"
        _write_manifest(
            path,
            stage_id,
            status="partial",
            gate="blocked",
            execution_gate="pass",
            network_file=network,
        )
        manifests[stage_id] = path

    plan = materialize_hamburg_execution_plan(
        output_dir=tmp_path / "plan",
        stage_manifests=manifests,
    )

    assert plan["next_action"]["status"] == "complete"
    assert plan["promotion"] == {
        "decision": "blocked",
        "automatic": True,
        "execution_complete": True,
        "requires": (
            "W0, W1, W2, W3a, W3b, and W4 are materialized with "
            "execution_gate=pass, decision=pass, and automatic_promotion_gate=pass"
        ),
    }
    assert plan["stages"]["W5"]["status"] == "complete"
    assert plan["capabilities"]["field_faithful_digital_twin"]["status"] == "blocked"


def test_w1_change_invalidates_network_bound_descendants_not_counts(tmp_path: Path) -> None:
    network = tmp_path / "candidate.net.xml"
    network.write_text("<net/>\n", encoding="utf-8")
    manifests = {}
    for stage_id in ("W0", "W1", "W3a", "W2", "W3b", "W4"):
        path = tmp_path / f"{stage_id}.json"
        _write_manifest(path, stage_id, network_file=network)
        manifests[stage_id] = path
    plan_dir = tmp_path / "plan"
    materialize_hamburg_execution_plan(output_dir=plan_dir, stage_manifests=manifests)

    _write_manifest(
        manifests["W1"],
        "W1",
        status="review_ready",
        gate="blocked",
        execution_gate="pass",
        network_file=network,
    )
    second = materialize_hamburg_execution_plan(
        output_dir=plan_dir,
        stage_manifests=manifests,
    )

    assert second["changed_stages"] == ["W1"]
    assert second["invalidated_downstream_stages"] == ["W2", "W3b", "W4"]
    assert second["stages"]["W1"]["effective_status"] == "review_ready"
    assert second["stages"]["W3a"]["effective_status"] == "pass"
    assert second["stages"]["W3b"]["effective_status"] == "not_run"


def test_w3a_change_invalidates_detector_binding_and_replay(tmp_path: Path) -> None:
    network = tmp_path / "candidate.net.xml"
    network.write_text("<net/>\n", encoding="utf-8")
    manifests = {}
    for stage_id in ("W0", "W1", "W3a", "W2", "W3b", "W4"):
        path = tmp_path / f"{stage_id}.json"
        _write_manifest(path, stage_id, network_file=network)
        manifests[stage_id] = path
    plan_dir = tmp_path / "plan"
    materialize_hamburg_execution_plan(output_dir=plan_dir, stage_manifests=manifests)

    _write_manifest(
        manifests["W3a"],
        "W3a",
        status="partial",
        gate="blocked",
        execution_gate="pass",
    )
    second = materialize_hamburg_execution_plan(
        output_dir=plan_dir,
        stage_manifests=manifests,
    )

    assert second["changed_stages"] == ["W3a"]
    assert second["invalidated_downstream_stages"] == ["W3b", "W4"]

    third = materialize_hamburg_execution_plan(
        output_dir=plan_dir,
        stage_manifests=manifests,
    )

    assert third["changed_stages"] == []
    assert third["invalidated_downstream_stages"] == ["W3b", "W4"]
    assert third["stages"]["W3b"]["effective_status"] == "not_run"
    assert third["stages"]["W4"]["effective_status"] == "not_run"

    _write_manifest(
        manifests["W3b"],
        "W3b",
        status="partial",
        gate="blocked",
        execution_gate="pass",
        network_file=network,
    )
    _write_manifest(manifests["W4"], "W4", network_file=network)
    fourth = materialize_hamburg_execution_plan(
        output_dir=plan_dir,
        stage_manifests=manifests,
    )

    assert fourth["changed_stages"] == ["W3b", "W4"]
    assert fourth["invalidated_downstream_stages"] == []
    assert fourth["stages"]["W3b"]["effective_status"] == "partial"
    assert fourth["stages"]["W4"]["effective_status"] == "pass"


def test_newly_supplied_stage_is_not_invalidated_by_its_own_change(tmp_path: Path) -> None:
    w0 = tmp_path / "W0.json"
    _write_manifest(w0, "W0")
    plan_dir = tmp_path / "plan"
    materialize_hamburg_execution_plan(
        output_dir=plan_dir,
        stage_manifests={"W0": w0},
    )
    network = tmp_path / "candidate.net.xml"
    network.write_text("<net/>\n", encoding="utf-8")
    w1 = tmp_path / "W1.json"
    _write_manifest(w1, "W1", network_file=network)

    second = materialize_hamburg_execution_plan(
        output_dir=plan_dir,
        stage_manifests={"W0": w0, "W1": w1},
    )

    assert second["changed_stages"] == ["W1"]
    assert second["invalidated_downstream_stages"] == []
    assert second["stages"]["W1"]["effective_status"] == "pass"
    assert second["stages"]["W1"]["readiness"] == "complete"
    assert second["next_action"]["stage_id"] == "W3a"


def test_network_binding_must_match_w1_and_current_bytes(tmp_path: Path) -> None:
    w1_network = tmp_path / "w1.net.xml"
    stale_network = tmp_path / "stale.net.xml"
    w1_network.write_text("<net id=\"w1\"/>\n", encoding="utf-8")
    stale_network.write_text("<net id=\"stale\"/>\n", encoding="utf-8")
    manifests = {}
    for stage_id in ("W0", "W1", "W3a"):
        path = tmp_path / f"{stage_id}.json"
        _write_manifest(path, stage_id, network_file=w1_network)
        manifests[stage_id] = path
    w3b = tmp_path / "W3b.json"
    _write_manifest(w3b, "W3b", network_file=stale_network)
    manifests["W3b"] = w3b

    mismatch = materialize_hamburg_execution_plan(
        output_dir=tmp_path / "mismatch-plan",
        stage_manifests=manifests,
    )
    assert mismatch["stages"]["W3b"]["contract_error"] == "network_binding_does_not_match_W1"
    assert mismatch["stages"]["W3b"]["execution_gate"] == "blocked"

    w1_network.write_text("<net id=\"mutated\"/>\n", encoding="utf-8")
    mutated = materialize_hamburg_execution_plan(
        output_dir=tmp_path / "mutated-plan",
        stage_manifests={"W0": manifests["W0"], "W1": manifests["W1"]},
    )
    assert mutated["stages"]["W1"]["contract_error"] == "network_binding_sha256_mismatch"


def test_w4_must_reference_the_selected_w3b_manifest(tmp_path: Path) -> None:
    network = tmp_path / "candidate.net.xml"
    network.write_text("<net/>\n", encoding="utf-8")
    manifests = {}
    for stage_id in ("W0", "W1", "W3a", "W2"):
        path = tmp_path / f"{stage_id}.json"
        _write_manifest(path, stage_id, network_file=network)
        manifests[stage_id] = path
    stale_w3b = tmp_path / "W3b.json"
    _write_manifest(stale_w3b, "W3b", network_file=network)
    selected_w3b = tmp_path / "selected-W3b.json"
    _write_manifest(
        selected_w3b,
        "W3b",
        status="partial",
        gate="blocked",
        execution_gate="pass",
        network_file=network,
    )
    manifests["W3b"] = selected_w3b
    w4 = tmp_path / "W4.json"
    _write_manifest(w4, "W4", network_file=network)
    manifests["W4"] = w4

    plan = materialize_hamburg_execution_plan(
        output_dir=tmp_path / "plan",
        stage_manifests=manifests,
    )

    assert plan["stages"]["W4"]["contract_error"] == (
        "stage_binding_detector_binding_manifest_does_not_match_W3b"
    )
    assert plan["stages"]["W4"]["execution_gate"] == "blocked"


def test_w4_requires_approved_w3b_aggregation_semantics(tmp_path: Path) -> None:
    network = tmp_path / "candidate.net.xml"
    network.write_text("<net/>\n", encoding="utf-8")
    manifests = {}
    for stage_id in ("W0", "W1", "W3a", "W2", "W3b"):
        path = tmp_path / f"{stage_id}.json"
        _write_manifest(path, stage_id, network_file=network)
        manifests[stage_id] = path
    w3b_payload = json.loads(manifests["W3b"].read_text(encoding="utf-8"))
    w3b_payload["gates"]["sensor_aggregation_semantics"] = "blocked"
    manifests["W3b"].write_text(json.dumps(w3b_payload), encoding="utf-8")
    w4 = tmp_path / "W4.json"
    _write_manifest(w4, "W4", network_file=network)
    manifests["W4"] = w4

    plan = materialize_hamburg_execution_plan(
        output_dir=tmp_path / "plan",
        stage_manifests=manifests,
    )

    assert plan["stages"]["W4"]["contract_error"] == (
        "W3b_sensor_aggregation_semantics_not_pass"
    )
    assert plan["stages"]["W4"]["execution_gate"] == "blocked"


def test_w3b_must_use_the_w3a_count_stream_snapshot(tmp_path: Path) -> None:
    network = tmp_path / "candidate.net.xml"
    network.write_text("<net/>\n", encoding="utf-8")
    manifests = {}
    for stage_id in ("W0", "W1", "W3a"):
        path = tmp_path / f"{stage_id}.json"
        _write_manifest(path, stage_id, network_file=network)
        manifests[stage_id] = path
    other_streams = tmp_path / "other-count-streams.json"
    other_streams.write_text('{"streams":[1]}\n', encoding="utf-8")
    w3b = tmp_path / "W3b.json"
    _write_manifest(w3b, "W3b", network_file=network)
    payload = json.loads(w3b.read_text(encoding="utf-8"))
    payload["source"]["count_stream_snapshot"] = {
        "path": str(other_streams.resolve()),
        "sha256": file_sha256(other_streams),
    }
    w3b.write_text(json.dumps(payload), encoding="utf-8")
    manifests["W3b"] = w3b

    plan = materialize_hamburg_execution_plan(
        output_dir=tmp_path / "plan",
        stage_manifests=manifests,
    )

    assert plan["stages"]["W3b"]["contract_error"] == (
        "stage_binding_count_stream_snapshot_does_not_match_W3a"
    )
    assert plan["stages"]["W3b"]["execution_gate"] == "blocked"


def test_w4_must_use_w3a_count_values(tmp_path: Path) -> None:
    network = tmp_path / "candidate.net.xml"
    network.write_text("<net/>\n", encoding="utf-8")
    manifests = {}
    for stage_id in ("W0", "W1", "W3a", "W2", "W3b", "W4"):
        path = tmp_path / f"{stage_id}.json"
        _write_manifest(path, stage_id, network_file=network)
        manifests[stage_id] = path
    other_counts = tmp_path / "other-counts.csv"
    other_counts.write_text("stream_id,begin,end,total\n1,0,900,1\n", encoding="utf-8")
    payload = json.loads(manifests["W4"].read_text(encoding="utf-8"))
    payload["source"]["canonical_count_file"] = {
        "path": str(other_counts.resolve()),
        "sha256": file_sha256(other_counts),
    }
    manifests["W4"].write_text(json.dumps(payload), encoding="utf-8")

    plan = materialize_hamburg_execution_plan(
        output_dir=tmp_path / "plan",
        stage_manifests=manifests,
    )

    assert plan["stages"]["W4"]["contract_error"] == (
        "stage_binding_canonical_count_file_does_not_match_W3a"
    )


def test_workflow_rehashes_mapping_and_signal_event_artifacts(tmp_path: Path) -> None:
    network = tmp_path / "candidate.net.xml"
    network.write_text("<net/>\n", encoding="utf-8")
    manifests = {}
    for stage_id in ("W0", "W1", "W3a", "W2", "W3b", "W4"):
        path = tmp_path / f"{stage_id}.json"
        _write_manifest(path, stage_id, network_file=network)
        manifests[stage_id] = path

    detector_mapping = tmp_path / "detector_mapping.csv"
    original_mapping = detector_mapping.read_text(encoding="utf-8")
    mapping_plan_dir = tmp_path / "mapping-plan"
    materialize_hamburg_execution_plan(
        output_dir=mapping_plan_dir,
        stage_manifests=manifests,
    )
    detector_mapping.write_text("mutated\n", encoding="utf-8")
    mapping_plan = materialize_hamburg_execution_plan(
        output_dir=mapping_plan_dir,
        stage_manifests=manifests,
    )
    assert mapping_plan["changed_stages"] == ["W3b"]
    assert mapping_plan["invalidated_downstream_stages"] == ["W4"]
    assert mapping_plan["stages"]["W3b"]["contract_error"] == (
        "stage_binding_detector_mapping_sha256_mismatch"
    )

    detector_mapping.write_text(original_mapping, encoding="utf-8")
    observation_manifest = tmp_path / "signal-observations.json"
    observation_manifest.write_text('{"schema":"fixture"}\n', encoding="utf-8")
    tls_events = tmp_path / "tls-link-events.csv"
    tls_events.write_text("time,tls_id,link_index,state\n", encoding="utf-8")
    w4_payload = json.loads(manifests["W4"].read_text(encoding="utf-8"))
    w4_payload["source"]["signal_observation_manifest"] = {
        "path": str(observation_manifest.resolve()),
        "sha256": file_sha256(observation_manifest),
    }
    w4_payload["source"]["tls_link_events"] = {
        "path": str(tls_events.resolve()),
        "sha256": file_sha256(tls_events),
    }
    manifests["W4"].write_text(json.dumps(w4_payload), encoding="utf-8")
    event_plan_dir = tmp_path / "event-plan"
    materialize_hamburg_execution_plan(
        output_dir=event_plan_dir,
        stage_manifests=manifests,
    )
    tls_events.write_text("mutated\n", encoding="utf-8")
    event_plan = materialize_hamburg_execution_plan(
        output_dir=event_plan_dir,
        stage_manifests=manifests,
    )
    assert event_plan["changed_stages"] == ["W4"]
    assert event_plan["stages"]["W4"]["contract_error"] == (
        "stage_binding_tls_link_events_sha256_mismatch"
    )


def test_manifest_schema_is_stage_specific(tmp_path: Path) -> None:
    w0 = tmp_path / "W0.json"
    w0.write_text(
        json.dumps(
            {
                "schema": "wrong-for-every-stage",
                "status": "pass",
                "automatic_promotion_gate": "pass",
            }
        ),
        encoding="utf-8",
    )

    plan = materialize_hamburg_execution_plan(
        output_dir=tmp_path / "plan",
        stage_manifests={"W0": w0},
    )

    assert plan["stages"]["W0"]["decision"] == "blocked"
    assert plan["stages"]["W0"]["contract_error"].startswith("manifest_schema_mismatch")


def test_scope_can_feed_geometry_and_counts_without_signal_promotion(tmp_path: Path) -> None:
    w0 = tmp_path / "W0.json"
    w0.write_text(
        json.dumps(
            {
                "schema": "torii.hamburg-named-corridor-scope/v1",
                "status": "partial",
                "automatic_promotion_gate": "blocked",
                "signal_assets": {"decision": "blocked"},
                "nodes": [{"node_id": "2349"}, {"node_id": "2394"}, {"node_id": "2403"}],
                "official_road_scope": {"link_count": 7},
            }
        ),
        encoding="utf-8",
    )

    plan = materialize_hamburg_execution_plan(
        output_dir=tmp_path / "plan",
        stage_manifests={"W0": w0},
    )

    assert plan["stages"]["W0"]["execution_gate"] == "pass"
    assert plan["stages"]["W1"]["readiness"] == "ready"
    assert plan["stages"]["W3a"]["readiness"] == "ready"
    assert plan["stages"]["W3b"]["readiness"] == "blocked"
    assert plan["promotion"]["decision"] == "blocked"


def test_machine_feedback_is_hash_bound_without_changing_gate(tmp_path: Path) -> None:
    network = tmp_path / "candidate.net.xml"
    network.write_text("<net/>\n", encoding="utf-8")
    manifests = {}
    for stage_id in ("W0", "W1", "W3a", "W3b"):
        path = tmp_path / f"{stage_id}.json"
        _write_manifest(path, stage_id, network_file=network)
        manifests[stage_id] = path
    w2 = tmp_path / "W2.json"
    _write_manifest(
        w2,
        "W2",
        status="blocked",
        gate="blocked",
        execution_gate="blocked",
        network_file=network,
    )
    payload = json.loads(w2.read_text(encoding="utf-8"))
    payload.update(
        {
            "execution_gate_reason": "one or more active bindings lack a complete response",
            "missing_required_node_ids": ["2403"],
            "incomplete_stream_ids": [72940],
        }
    )
    w2.write_text(json.dumps(payload), encoding="utf-8")
    manifests["W2"] = w2
    history = tmp_path / "history.json"
    history.write_text(
        json.dumps(
            {
                "resolved_node_ids": ["2349", "2394"],
                "unresolved_node_ids": ["2403"],
                "publication_gap": {
                    "decision": "confirmed_official_node_without_published_tld_binding",
                    "next_action": "resolve_official_signal_publication_gap_or_change_scope",
                },
            }
        ),
        encoding="utf-8",
    )
    identity = tmp_path / "identity.json"
    identity.write_text(
        json.dumps(
            {
                "decision": "pass",
                "human_action_required": False,
                "selections": [
                    {"selected_node": {"node_id": node_id}}
                    for node_id in ("2349", "2394", "2403")
                ],
            }
        ),
        encoding="utf-8",
    )

    plan = materialize_hamburg_execution_plan(
        output_dir=tmp_path / "plan",
        stage_manifests=manifests,
        stage_feedback={"W2": (history, identity)},
    )

    assert plan["first_invalid_stage"] == "W2"
    assert plan["stages"]["W2"]["execution_gate"] == "blocked"
    assert len(plan["stages"]["W2"]["feedback_manifests"]) == 2
    assert plan["replan"]["feedback"]["resolved_node_ids"] == ["2349", "2394"]
    assert plan["replan"]["feedback"]["official_node_identity"]["selected_node_ids"] == [
        "2349",
        "2394",
        "2403",
    ]


def test_feedback_change_invalidates_only_materialized_downstream_stage(tmp_path: Path) -> None:
    network = tmp_path / "candidate.net.xml"
    network.write_text("<net/>\n", encoding="utf-8")
    manifests = {}
    for stage_id in ("W0", "W1", "W3a", "W2", "W3b", "W4"):
        path = tmp_path / f"{stage_id}.json"
        _write_manifest(path, stage_id, network_file=network)
        manifests[stage_id] = path
    feedback = tmp_path / "feedback.json"
    feedback.write_text(json.dumps({"resolved_node_ids": ["2349"]}), encoding="utf-8")
    plan_dir = tmp_path / "plan"
    materialize_hamburg_execution_plan(
        output_dir=plan_dir,
        stage_manifests=manifests,
        stage_feedback={"W2": feedback},
    )

    feedback.write_text(json.dumps({"resolved_node_ids": ["2349", "2394"]}), encoding="utf-8")
    second = materialize_hamburg_execution_plan(
        output_dir=plan_dir,
        stage_manifests=manifests,
        stage_feedback={"W2": feedback},
    )

    assert second["changed_stages"] == ["W2"]
    assert second["invalidated_downstream_stages"] == ["W4"]


def test_external_w5_and_legacy_w3_are_rejected(tmp_path: Path) -> None:
    manifest = tmp_path / "stage.json"
    manifest.write_text("{}", encoding="utf-8")
    with pytest.raises(HamburgExecutionWorkflowError, match="generated automatically"):
        materialize_hamburg_execution_plan(
            output_dir=tmp_path / "w5-plan",
            stage_manifests={"W5": manifest},
        )
    with pytest.raises(HamburgExecutionWorkflowError, match="was split"):
        materialize_hamburg_execution_plan(
            output_dir=tmp_path / "w3-plan",
            stage_manifests={"W3": manifest},
        )


def test_portable_workflow_config_resolves_stage_paths_relative_to_itself(tmp_path: Path) -> None:
    evidence_dir = tmp_path / "evidence"
    evidence_dir.mkdir()
    _write_manifest(evidence_dir / "W0.json", "W0")
    _write_manifest(evidence_dir / "W1.json", "W1")
    config = tmp_path / "hamburg-workflow.json"
    config.write_text(
        json.dumps(
            {
                "schema": HAMBURG_EXECUTION_CONFIG_SCHEMA,
                "output_dir": "run",
                "resume": False,
                "stages": {
                    "W0": {"manifest": "evidence/W0.json"},
                    "W1": {"manifest": "evidence/W1.json", "feedback": []},
                },
            }
        ),
        encoding="utf-8",
    )

    plan = materialize_hamburg_execution_plan_from_config(config)

    assert plan["stages"]["W0"]["decision"] == "pass"
    assert plan["stages"]["W1"]["decision"] == "pass"
    assert plan["next_action"]["stage_id"] == "W3a"
    assert (tmp_path / "run" / "execution-plan.manifest.json").is_file()


def test_portable_workflow_config_rejects_unknown_stage(tmp_path: Path) -> None:
    config = tmp_path / "hamburg-workflow.json"
    config.write_text(
        json.dumps(
            {
                "schema": HAMBURG_EXECUTION_CONFIG_SCHEMA,
                "output_dir": "run",
                "stages": {"W9": {}},
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(HamburgExecutionWorkflowError, match="unknown workflow stage"):
        materialize_hamburg_execution_plan_from_config(config)
