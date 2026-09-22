from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

from torii_sumo.core import official_tls_workflow as workflow
from torii_sumo.core import digital_twin_workflow
from torii_sumo.core.digital_twin import MapConnection, MapLane, SignalStream
from torii_sumo.core.digital_twin_mapping import MapLaneBinding, TlsBinding
from torii_sumo.core.ocit_c import (
    OcitCConfig,
    OcitMotorSignalGroup,
    OcitVehicleTopologyInventory,
    OcitVehicleTopologyMovement,
    PrimarySignalGroupValidation,
)
from torii_sumo.core.hamburg_teacher_cell import HamburgOfficialMovementPath


def test_product_runtime_does_not_expose_legacy_plan_rebuild_entrypoints() -> None:
    for module in (workflow, digital_twin_workflow):
        assert not hasattr(module, "derive_official_tls_plan")
        assert not hasattr(module, "build_official_tls_rebuild_variant")


def test_cached_official_tls_workflow_requires_all_six_assets(tmp_path: Path) -> None:
    source_net = tmp_path / "frozen.net.xml"
    source_net.write_text("<net/>", encoding="utf-8")
    asset_dir = tmp_path / "assets"
    asset_dir.mkdir()

    report = workflow.rebuild_hamburg_sandtorkai_official_tls(
        source_net_file=source_net,
        signal_asset_dir=asset_dir,
        output_dir=tmp_path / "output",
    )

    assert report["status"] == "fail"
    assert report["stage"] == "input_validation"
    assert "required cached Hamburg signal assets are missing" in report["error"]
    assert Path(report["manifest_file"]).is_file()


def test_cached_official_tls_workflow_uses_33_topology_movements_and_27_observations(
    tmp_path: Path,
    monkeypatch,
) -> None:
    source_net, asset_dir = _write_inputs(tmp_path)
    _install_success_fakes(monkeypatch)

    report = workflow.rebuild_hamburg_sandtorkai_official_tls(
        source_net_file=source_net,
        signal_asset_dir=asset_dir,
        output_dir=tmp_path / "output",
    )

    assert report["status"] == "pass"
    assert report["claim_status"] == "official-tls-topology-ready"
    assert report["automatic_promotion_gate"] == "blocked"
    assert report["source_net_unchanged"] is True
    binding_audit = report["primary_stream_tls_binding_audit"]
    assert binding_audit["status"] == "pass"
    assert binding_audit["expected_stream_count"] == 27
    assert binding_audit["validated_active_or_redundant_count"] == 27
    assert binding_audit["expected_official_group_count"] == 14
    assert binding_audit["active_official_group_count"] == 14
    assert binding_audit["expected_observed_movement_count"] == 27
    assert binding_audit["validated_observed_movement_count"] == 27
    assert binding_audit["expected_observed_control_count"] == 18
    assert binding_audit["active_observed_control_count"] == 18
    assert set(binding_audit["status_counts"]) == {"active", "redundant"}
    topology_audit = report["vehicle_topology_inventory_audit"]
    assert topology_audit["status"] == "pass"
    assert topology_audit["movement_count"] == 33
    assert topology_audit["control_index_count"] == 21
    assert topology_audit["movement_counts_by_node"] == {
        "0228": 16,
        "2394": 8,
        "2421": 9,
    }
    assert report["inventory_counts"]["vehicle_topology_movement_count"] == 33
    assert report["inventory_counts"]["observation_stream_count"] == 27
    replacement_policy = report["source_tls_replacement_policy"]
    assert replacement_policy["value"] == "replace_all_source_tls_in_compact_scope"
    assert replacement_policy["source_tls_controller_count"] == 1
    assert replacement_policy["source_tls_controller_ids"] == ["osm_tls"]
    assert replacement_policy["status"] == "pass"
    assert replacement_policy["official_controller_ids"] == [
        "HH_0228",
        "HH_2394",
        "HH_2421",
    ]
    assert replacement_policy["retired_controller_ids"] == ["osm_tls"]
    retirement = report["compact_scope_tls_retirement"]
    assert retirement["status"] == "pass"
    assert retirement["before_controller_count"] == 4
    assert retirement["after_controller_count"] == 3
    assert retirement["remaining_non_official_controller_ids"] == []
    assert Path(report["rebuilt_net_file"]).is_file()
    assert report["network_rebuild"]["final_net_file"] == report["rebuilt_net_file"]
    assert (
        report["network_rebuild"]["native_replay_final_net_file"]
        != report["rebuilt_net_file"]
    )
    manifest = json.loads(Path(report["manifest_file"]).read_text(encoding="utf-8"))
    artifact_roles = {row["role"] for row in manifest["artifacts"]}
    assert {
        "official_map_connections",
        "official_ocit_vehicle_movements",
        "official_tld_observed_group_subset",
        "base_map_lane_bindings",
        "effective_map_lane_bindings",
        "official_movement_lane_paths",
        "official_tls_native_teacher_derivation",
        "official_tls_native_replay_report",
        "official_tls_native_geometry_continuity",
        "official_tls_compact_scope_retirement",
        "official_movement_physical_endpoints",
        "official_primary_signal_tls_bindings",
        "rebuilt_net_file",
    } <= artifact_roles


def test_cached_official_tls_workflow_fails_if_one_stream_needs_review(
    tmp_path: Path,
    monkeypatch,
) -> None:
    source_net, asset_dir = _write_inputs(tmp_path)
    first_stream_id = workflow.hamburg_sandtorkai_primary_signal_snapshot()[0].stream_id
    _install_success_fakes(monkeypatch, invalid_stream_id=first_stream_id)

    report = workflow.rebuild_hamburg_sandtorkai_official_tls(
        source_net_file=source_net,
        signal_asset_dir=asset_dir,
        output_dir=tmp_path / "output",
    )

    assert report["status"] == "fail"
    assert report["stage"] == "primary_stream_tls_binding"
    assert report["primary_stream_tls_binding_audit"]["status"] == "fail"
    assert report["primary_stream_tls_binding_audit"]["validated_active_or_redundant_count"] == 26
    assert "strict 27-stream TLS binding gate failed" in report["error"]


def test_retired_link_demotion_is_blocked_until_ocit_map_topology_has_33_movements(
    tmp_path: Path,
    monkeypatch,
) -> None:
    source_net, asset_dir = _write_inputs(tmp_path)
    _install_success_fakes(monkeypatch)
    movement_path_called = False
    base_build_inventory = workflow.build_vehicle_topology_inventory

    def build_incomplete_inventory(*args) -> OcitVehicleTopologyInventory:
        inventory = base_build_inventory(*args)
        return replace(
            inventory,
            movement_count=32,
            movements=inventory.movements[:-1],
            topology_streams=inventory.topology_streams[:-1],
        )

    def forbidden_movement_path(**_kwargs):
        nonlocal movement_path_called
        movement_path_called = True
        raise AssertionError("movement paths must not run with an incomplete OCIT inventory")

    monkeypatch.setattr(
        workflow,
        "build_vehicle_topology_inventory",
        build_incomplete_inventory,
    )
    monkeypatch.setattr(
        workflow,
        "derive_hamburg_official_movement_paths",
        forbidden_movement_path,
    )

    report = workflow.rebuild_hamburg_sandtorkai_official_tls(
        source_net_file=source_net,
        signal_asset_dir=asset_dir,
        output_dir=tmp_path / "output",
    )

    assert report["status"] == "fail"
    assert movement_path_called is False
    assert report["stage"] == "ocit_vehicle_topology_inventory"
    assert "movement_count=32, expected 33" in report["error"]


def test_compact_scope_tls_retirement_is_blocked_without_three_replay_controllers(
    tmp_path: Path,
) -> None:
    replayed = tmp_path / "replayed.net.xml"
    replayed.write_text(
        '<net><tlLogic id="official_a"/>'
        '<connection from="a" to="b" tl="official_a" linkIndex="0"/></net>',
        encoding="utf-8",
    )
    output = tmp_path / "cleaned.net.xml"

    report = workflow.retire_compact_scope_non_official_tls(
        rebuilt_net_file=replayed,
        output_net_file=output,
        replay_report={
            "status": "pass",
            "stage_reports": [_passing_replay_stage("HH_2421", "official_a")],
        },
        topology_inventory_audit=_complete_topology_audit(),
    )

    assert report["status"] == "fail"
    assert output.exists() is False
    assert any("native replay stage count=1, expected 3" in row for row in report["errors"])
    assert any("unique official controller count=1, expected 3" in row for row in report["errors"])


def test_compact_scope_tls_retirement_rejects_residual_controller_after_demotion(
    tmp_path: Path,
    monkeypatch,
) -> None:
    replayed = tmp_path / "replayed.net.xml"
    replayed.write_text(
        "<net>"
        '<tlLogic id="official_a"/><tlLogic id="official_b"/>'
        '<tlLogic id="official_c"/><tlLogic id="osm_residual"/>'
        '<connection from="a" to="b" tl="official_a" linkIndex="0"/>'
        '<connection from="c" to="d" tl="official_b" linkIndex="0"/>'
        '<connection from="e" to="f" tl="official_c" linkIndex="0"/>'
        "</net>",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        workflow,
        "demote_tls_ids",
        lambda *_args, **_kwargs: {"tls_demotion_selected_controller_count": 1},
    )

    report = workflow.retire_compact_scope_non_official_tls(
        rebuilt_net_file=replayed,
        output_net_file=tmp_path / "cleaned.net.xml",
        replay_report={
            "status": "pass",
            "stage_reports": [
                _passing_replay_stage("HH_2421", "official_a"),
                _passing_replay_stage("HH_2394", "official_b"),
                _passing_replay_stage("HH_0228", "official_c", shared=True),
            ],
        },
        topology_inventory_audit=_complete_topology_audit(),
        source_tls_controller_ids=("osm_residual",),
    )

    assert report["status"] == "fail"
    assert report["after_controller_count"] == 4
    assert report["remaining_non_official_controller_ids"] == ["osm_residual"]
    assert any("non-official controllers remain" in row for row in report["errors"])


def test_native_geometry_gate_rejects_shifted_external_edge(tmp_path: Path) -> None:
    source = tmp_path / "source.net.xml"
    candidate = tmp_path / "candidate.net.xml"
    source.write_text(
        '<net><location netOffset="0,0"/><edge id="road" from="a" to="b">'
        '<lane id="road_0" index="0" shape="0,0 10,0"/></edge></net>',
        encoding="utf-8",
    )
    candidate.write_text(
        '<net><location netOffset="0,0"/><edge id="road" from="a" to="b">'
        '<lane id="road_0" index="0" shape="0,0 100,0"/></edge></net>',
        encoding="utf-8",
    )

    report = workflow.audit_hamburg_native_replay_geometry(
        source,
        candidate,
        endpoint_tolerance_m=10.0,
    )

    assert report["status"] == "fail"
    assert report["external_edge_id_delta_count"] == 0
    assert report["geometry_mismatch_count"] == 1
    assert report["geometry_mismatches"][0]["edge_id"] == "road"


def test_native_geometry_gate_accepts_only_replay_scoped_cell_absorption(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.net.xml"
    candidate = tmp_path / "candidate.net.xml"
    source.write_text(
        '<net><location netOffset="0,0"/>'
        '<edge id="approach" from="remote" to="local_a">'
        '<lane id="approach_0" index="0" length="10" shape="0,0 10,0"/></edge>'
        '<edge id="micro" from="local_a" to="local_b">'
        '<lane id="micro_0" index="0" length="1" shape="10,0 11,0"/></edge>'
        '<junction id="remote" x="0" y="0"/>'
        '<junction id="local_a" x="10" y="0"/>'
        '<junction id="local_b" x="11" y="0"/>'
        '</net>',
        encoding="utf-8",
    )
    candidate.write_text(
        '<net><location netOffset="0,0"/>'
        '<edge id=":controller_0" function="internal">'
        '<lane id=":controller_0_0" index="0" length="20" shape="10,0 20,0"/></edge>'
        '<edge id="approach" from="remote" to="local_a">'
        '<lane id="approach_0" index="0" length="10" shape="0,0 10,0"/></edge>'
        '<junction id="remote" x="0" y="0"/>'
        '<junction id="local_a" x="10" y="0"/>'
        '</net>',
        encoding="utf-8",
    )

    report = workflow.audit_hamburg_native_replay_geometry(
        source,
        candidate,
        replay_report=_passing_geometry_replay_report(),
    )

    assert report["status"] == "pass"
    assert report["authorized_absorbed_external_edge_ids"] == ["micro"]
    assert report["unauthorized_edge_id_delta_count"] == 0
    assert report["mapped_boundary_geometry_failure_count"] == 0
    assert report["internal_lane_length_audit"]["maximum_length_m"] == 20.0
    assert report["torii_parity_status"] == "pass"
    assert report["external_lane_shape_length_audit"]["status"] == "pass"


def test_native_geometry_gate_cannot_pass_when_torii_parity_failed(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.net.xml"
    candidate = tmp_path / "candidate.net.xml"
    source.write_text(
        '<net><location netOffset="0,0"/>'
        '<edge id="approach" from="remote" to="local_a">'
        '<lane id="approach_0" index="0" length="10" shape="0,0 10,0"/></edge>'
        '<edge id="micro" from="local_a" to="local_b">'
        '<lane id="micro_0" index="0" length="1" shape="10,0 11,0"/></edge>'
        '<junction id="remote" x="0" y="0"/>'
        '<junction id="local_a" x="10" y="0"/>'
        '<junction id="local_b" x="11" y="0"/>'
        '</net>',
        encoding="utf-8",
    )
    candidate.write_text(
        '<net><location netOffset="0,0"/>'
        '<edge id=":controller_0" function="internal">'
        '<lane id=":controller_0_0" index="0" length="25" shape="10,0 25,0"/></edge>'
        '<edge id="approach" from="remote" to="controller">'
        '<lane id="approach_0" index="0" length="25" shape="0,0 25,0"/></edge>'
        '<junction id="remote" x="0" y="0"/>'
        '<junction id="controller" x="25" y="0"/>'
        '</net>',
        encoding="utf-8",
    )

    report = workflow.audit_hamburg_native_replay_geometry(
        source,
        candidate,
        replay_report=_passing_geometry_replay_report(),
    )

    assert report["torii_parity_status"] == "fail"
    assert report["status"] == "fail"


def test_native_geometry_gate_rejects_stale_external_lane_length(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.net.xml"
    candidate = tmp_path / "candidate.net.xml"
    source.write_text(
        '<net><location netOffset="0,0"/>'
        '<edge id="approach" from="remote" to="local_a">'
        '<lane id="approach_0" index="0" length="10" shape="0,0 10,0"/></edge>'
        '<edge id="micro" from="local_a" to="local_b">'
        '<lane id="micro_0" index="0" length="1" shape="10,0 11,0"/></edge>'
        '<junction id="remote" x="0" y="0"/>'
        '<junction id="local_a" x="10" y="0"/>'
        '<junction id="local_b" x="11" y="0"/>'
        '</net>',
        encoding="utf-8",
    )
    candidate.write_text(
        '<net><location netOffset="0,0"/>'
        '<edge id=":controller_0" function="internal">'
        '<lane id=":controller_0_0" index="0" length="20" shape="10,0 20,0"/></edge>'
        '<edge id="approach" from="remote" to="local_a">'
        '<lane id="approach_0" index="0" length="110" shape="0,0 10,0"/></edge>'
        '<junction id="remote" x="0" y="0"/>'
        '<junction id="local_a" x="10" y="0"/>'
        '</net>',
        encoding="utf-8",
    )

    report = workflow.audit_hamburg_native_replay_geometry(
        source,
        candidate,
        replay_report=_passing_geometry_replay_report(),
    )

    length_audit = report["external_lane_shape_length_audit"]
    assert report["torii_parity_status"] == "pass"
    assert report["status"] == "fail"
    assert length_audit["failure_count"] == 1
    assert length_audit["failures"][0]["lane_id"] == "approach_0"
    assert length_audit["failures"][0]["absolute_delta_m"] == 100.0


def test_sumo_rendered_overlap_audit_fails_on_netconvert_warning(
    tmp_path: Path,
) -> None:
    source = tmp_path / "candidate.net.xml"
    source.write_text("<net/>", encoding="utf-8")

    def fake_runner(command, *, cwd, timeout_seconds):
        Path(command[4]).write_text("<net/>", encoding="utf-8")
        return {
            "status": "pass",
            "returncode": 0,
            "stdout": "Success.\n",
            "stderr": "Warning: Edge 'a' overlaps with edge 'b' by 2.62.\n",
        }

    report = workflow.audit_sumo_rendered_edge_overlaps(
        source,
        output_dir=tmp_path / "audit",
        command_runner=fake_runner,
    )

    assert report["status"] == "fail"
    assert report["source_network_mutation"] is False
    assert report["overlap_warning_count"] == 1
    assert report["overlap_warnings"] == [
        {"first_edge_id": "a", "second_edge_id": "b", "overlap_m": 2.62}
    ]


def test_sumo_rendered_overlap_audit_passes_only_with_clean_probe(
    tmp_path: Path,
) -> None:
    source = tmp_path / "candidate.net.xml"
    source.write_text("<net/>", encoding="utf-8")

    def fake_runner(command, *, cwd, timeout_seconds):
        Path(command[4]).write_text("<net/>", encoding="utf-8")
        return {"status": "pass", "returncode": 0, "stdout": "Success.", "stderr": ""}

    report = workflow.audit_sumo_rendered_edge_overlaps(
        source,
        output_dir=tmp_path / "audit",
        command_runner=fake_runner,
    )

    assert report["status"] == "pass"
    assert report["overlap_warning_count"] == 0


def test_native_geometry_gate_rejects_unscoped_edge_loss_and_long_shortcut(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.net.xml"
    candidate = tmp_path / "candidate.net.xml"
    source.write_text(
        '<net><location netOffset="0,0"/>'
        '<edge id="approach" from="remote" to="local_a">'
        '<lane id="approach_0" index="0" length="10" shape="0,0 10,0"/></edge>'
        '<edge id="micro" from="local_a" to="local_b">'
        '<lane id="micro_0" index="0" length="1" shape="10,0 11,0"/></edge>'
        '<edge id="outside" from="outside_a" to="outside_b">'
        '<lane id="outside_0" index="0" length="10" shape="30,0 40,0"/></edge>'
        '<junction id="remote" x="0" y="0"/>'
        '<junction id="local_a" x="10" y="0"/>'
        '<junction id="local_b" x="11" y="0"/>'
        '<junction id="outside_a" x="30" y="0"/>'
        '<junction id="outside_b" x="40" y="0"/>'
        '</net>',
        encoding="utf-8",
    )
    candidate.write_text(
        '<net><location netOffset="0,0"/>'
        '<edge id=":controller_0" function="internal">'
        '<lane id=":controller_0_0" index="0" length="120" shape="10,0 130,0"/></edge>'
        '<edge id="approach" from="remote" to="controller">'
        '<lane id="approach_0" index="0" length="20" shape="0,0 20,0"/></edge>'
        '<junction id="remote" x="0" y="0"/>'
        '<junction id="controller" x="20" y="0"/>'
        '</net>',
        encoding="utf-8",
    )

    report = workflow.audit_hamburg_native_replay_geometry(
        source,
        candidate,
        replay_report=_passing_geometry_replay_report(),
    )

    assert report["status"] == "fail"
    assert report["unauthorized_missing_external_edge_ids"] == ["outside"]
    assert report["internal_lane_length_audit"]["over_limit_count"] == 1


def test_replay_aware_movement_and_observation_binding_uses_numeric_tls_owner(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(workflow, "HAMBURG_SANDTORKAI_VEHICLE_TOPOLOGY_MOVEMENT_COUNT", 2)
    monkeypatch.setattr(
        workflow,
        "HAMBURG_SANDTORKAI_VEHICLE_TOPOLOGY_COUNTS_BY_NODE",
        {"2421": 2},
    )
    monkeypatch.setattr(workflow, "HAMBURG_SANDTORKAI_PRIMARY_STREAM_COUNT", 2)
    net = tmp_path / "numeric.net.xml"
    net.write_text(
        "<net>"
        '<edge id="in1" from="a" to="j"><lane id="in1_0" index="0" length="10"/></edge>'
        '<edge id="out1" from="j" to="b"><lane id="out1_0" index="0" length="10"/></edge>'
        '<edge id="in2" from="c" to="j"><lane id="in2_0" index="0" length="10"/></edge>'
        '<edge id="out2" from="j" to="d"><lane id="out2_0" index="0" length="10"/></edge>'
        '<tlLogic id="759714713" type="static" programID="0" offset="0">'
        '<phase duration="10" state="G"/></tlLogic>'
        '<connection from="in1" to="out1" fromLane="0" toLane="0" '
        'tl="759714713" linkIndex="0"/>'
        '<connection from="in2" to="out2" fromLane="0" toLane="0" '
        'tl="759714713" linkIndex="0"/>'
        "</net>",
        encoding="utf-8",
    )
    movements = tuple(
        OcitVehicleTopologyMovement(
            node_id="2421",
            connection_id=str(index),
            ingress_lane_id=f"I{index}",
            egress_lane_id=f"E{index}",
            map_signal_group="K1",
            primary_motor_groups=("K1",),
            secondary_motor_groups=(),
            topology_control_key="P_K1__S_NONE",
            observed_stream_ids=(index,),
            observed_signal_groups=("K1",),
        )
        for index in (1, 2)
    )
    streams = tuple(
        SignalStream(
            stream_id=index,
            thing_id=None,
            node_id="2421",
            connection_id=str(index),
            ingress_lane_id=f"I{index}",
            egress_lane_id=f"E{index}",
            lane_type="KFZ",
            signal_group="K1",
            layer_name="primary_signal",
            name=f"stream-{index}",
        )
        for index in (1, 2)
    )
    inventory = OcitVehicleTopologyInventory(
        status="pass",
        source_movement_count=2,
        excluded_non_vehicle_movement_count=0,
        movement_count=2,
        observed_stream_count=2,
        observed_match_count=2,
        group_resolution_policy="test",
        movements=movements,
        topology_streams=streams,
    )
    lane_bindings = [
        MapLaneBinding(
            node_id="2421",
            map_lane_id=f"{role}{index}",
            map_lane_type="vehicle",
            map_role="ingress" if role == "I" else "egress",
            sumo_edge=f"{'in' if role == 'I' else 'out'}{index}",
            sumo_lane=f"{'in' if role == 'I' else 'out'}{index}_0",
            lane_position=0.0,
            distance_m=0.0,
            heading_error_deg=0.0,
            mapping_confidence="high",
            mapping_status="active",
        )
        for index in (1, 2)
        for role in ("I", "E")
    ]
    contract = SimpleNamespace(
        ir=SimpleNamespace(
            control=SimpleNamespace(tls_id="HH_2421"),
            core=SimpleNamespace(core_id="HH_2421"),
        ),
        expression_index_by_key={"P_K1__S_NONE": 0},
        lane_indices=tuple(
            SimpleNamespace(
                direction="ingress" if role == "I" else "egress",
                official_lane_id=f"{role}{index}",
                candidate_edge_id=f"{'in' if role == 'I' else 'out'}{index}",
                teacher_lane_index=0,
            )
            for index in (1, 2)
            for role in ("I", "E")
        ),
    )
    replay = {
        "stage_reports": [
            {
                "status": "pass",
                "controller_id": "HH_2421",
                "teacher_materialization": {
                    "tls_signal_grouping_report": {
                        "status": "pass",
                        "tls_signal_grouping_control_key_to_link_indices": {
                            "HH_2421": {"P_K1__S_NONE": [0]}
                        },
                    }
                },
                "native_teacher_replay": {
                    "variant_reports": [
                        {
                            "status": "pass",
                            "target_internal_replay": {
                                "status": "pass",
                                "junction_id": "759714713",
                            },
                        }
                    ]
                },
            }
        ]
    }

    movement_audit = workflow.audit_hamburg_official_movement_endpoints(
        net_file=net,
        topology_inventory=inventory,
        lane_bindings=lane_bindings,
        contracts=(contract,),
        replay_report=replay,
    )
    assert movement_audit["status"] == "pass"
    assert movement_audit["validated_movement_count"] == 2
    assert movement_audit["unique_physical_endpoint_count"] == 2
    assert movement_audit["replay_control_evidence"]["official_controller_by_node"] == {
        "2421": "759714713"
    }

    tls_bindings = workflow.bind_observed_streams_to_replayed_tls(
        streams=streams,
        topology_inventory=inventory,
        movement_endpoint_audit=movement_audit,
    )
    assert [binding.mapping_status for binding in tls_bindings] == ["active", "redundant"]
    assert {binding.sumo_tls_id for binding in tls_bindings} == {"759714713"}
    assert {binding.sumo_link_index for binding in tls_bindings} == {0}
    binding_audit = workflow._audit_primary_tls_bindings(
        streams,
        tls_bindings,
        inventory,
        movement_audit,
    )
    assert binding_audit["status"] == "pass"
    assert binding_audit["active_observed_control_count"] == 1


def test_movement_endpoint_audit_rejects_controller_not_owned_by_replay(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(workflow, "HAMBURG_SANDTORKAI_VEHICLE_TOPOLOGY_MOVEMENT_COUNT", 1)
    monkeypatch.setattr(
        workflow,
        "HAMBURG_SANDTORKAI_VEHICLE_TOPOLOGY_COUNTS_BY_NODE",
        {"2421": 1},
    )
    net = tmp_path / "wrong-owner.net.xml"
    net.write_text(
        '<net><edge id="i"><lane id="i_0" index="0" length="1"/></edge>'
        '<edge id="e"><lane id="e_0" index="0" length="1"/></edge>'
        '<tlLogic id="111" type="static" programID="0" offset="0">'
        '<phase duration="1" state="G"/></tlLogic>'
        '<connection from="i" to="e" fromLane="0" toLane="0" tl="111" linkIndex="0"/>'
        "</net>",
        encoding="utf-8",
    )
    movement = OcitVehicleTopologyMovement(
        node_id="2421", connection_id="1", ingress_lane_id="I", egress_lane_id="E",
        map_signal_group="K1", primary_motor_groups=("K1",), secondary_motor_groups=(),
        topology_control_key="P_K1__S_NONE", observed_stream_ids=(), observed_signal_groups=(),
    )
    inventory = OcitVehicleTopologyInventory(
        status="pass", source_movement_count=1, excluded_non_vehicle_movement_count=0,
        movement_count=1, observed_stream_count=0, observed_match_count=0,
        group_resolution_policy="test", movements=(movement,), topology_streams=(),
    )
    contract = SimpleNamespace(
        ir=SimpleNamespace(control=SimpleNamespace(tls_id="HH_2421"), core=SimpleNamespace(core_id="HH_2421")),
        expression_index_by_key={"P_K1__S_NONE": 0},
        lane_indices=(
            SimpleNamespace(direction="ingress", official_lane_id="I", candidate_edge_id="i", teacher_lane_index=0),
            SimpleNamespace(direction="egress", official_lane_id="E", candidate_edge_id="e", teacher_lane_index=0),
        ),
    )
    bindings = [
        MapLaneBinding("2421", "I", "vehicle", "ingress", "i", "i_0", 0, 0, 0, "high", "active"),
        MapLaneBinding("2421", "E", "vehicle", "egress", "e", "e_0", 0, 0, 0, "high", "active"),
    ]
    replay = {
        "stage_reports": [{
            "status": "pass", "controller_id": "HH_2421",
            "teacher_materialization": {"tls_signal_grouping_report": {
                "status": "pass", "tls_signal_grouping_control_key_to_link_indices": {
                    "HH_2421": {"P_K1__S_NONE": [0]}
                }}},
            "native_teacher_replay": {"variant_reports": [{
                "status": "pass", "target_internal_replay": {
                    "status": "pass", "junction_id": "222"
                }}]},
        }]
    }
    report = workflow.audit_hamburg_official_movement_endpoints(
        net_file=net, topology_inventory=inventory, lane_bindings=bindings,
        contracts=(contract,), replay_report=replay,
    )
    assert report["status"] == "fail"
    assert report["validated_movement_count"] == 0
    assert any("controller '111' != replay controller '222'" in error for error in report["movements"][0]["errors"])


def _passing_geometry_replay_report() -> dict[str, object]:
    return {
        "stage_reports": [
            {
                "status": "pass",
                "native_teacher_replay": {
                    "variant_reports": [
                        {
                            "status": "pass",
                            "target_internal_replay": {
                                "status": "pass",
                                "collapse_junction_ids": ["local_a", "local_b"],
                                "preserved_mapped_boundary_geometry_edge_ids": ["approach"],
                                "boundary_geometry_preservation_failure_count": 0,
                                "unanchored_boundary_edge_count": 0,
                                "dangling_connection_count": 0,
                                "invalid_connection_count": 0,
                                "invalid_controlled_connection_count": 0,
                            },
                            "tls_via_path_semantics": {
                                "status": "pass",
                                "via_path_failure_count": 0,
                            },
                            "routeability_smoke": {"status": "pass"},
                        }
                    ]
                },
            }
        ]
    }


def _write_inputs(tmp_path: Path) -> tuple[Path, Path]:
    source_net = tmp_path / "frozen.net.xml"
    source_net.write_text('<net><tlLogic id="osm_tls"/></net>', encoding="utf-8")
    asset_dir = tmp_path / "assets"
    asset_dir.mkdir()
    for node_id in workflow.HAMBURG_SANDTORKAI_NODE_IDS:
        (asset_dir / f"{node_id}_map_xml.xml").write_text("<map/>", encoding="utf-8")
        (asset_dir / f"{node_id}_ocit_xml.xml").write_text("<ocit/>", encoding="utf-8")
    return source_net, asset_dir


def _complete_topology_audit() -> dict[str, object]:
    return {
        "status": "pass",
        "movement_count": 33,
        "control_index_count": 21,
        "movement_counts_by_node": {"0228": 16, "2421": 9, "2394": 8},
    }


def _passing_replay_stage(
    logical_controller_id: str,
    physical_controller_id: str,
    *,
    shared: bool = False,
) -> dict[str, object]:
    target_key = "candidate_controller_id" if shared else "junction_id"
    return {
        "status": "pass",
        "controller_id": logical_controller_id,
        "native_teacher_replay": {
            "status": "pass",
            "variant_reports": [
                {
                    "status": "pass",
                    "target_internal_replay": {
                        "status": "pass",
                        target_key: physical_controller_id,
                    },
                }
            ],
        },
    }


def _install_success_fakes(monkeypatch, *, invalid_stream_id: int | None = None) -> None:
    streams = workflow.hamburg_sandtorkai_primary_signal_snapshot()

    topology_movements: list[OcitVehicleTopologyMovement] = []
    topology_streams: list[SignalStream] = []
    control_occurrences: dict[tuple[str, str], int] = {}
    control_split_counts = {
        ("0228", "K2"): 2,
        ("0228", "K3"): 2,
        ("2394", "K7"): 2,
        ("2421", "K3"): 5,
    }

    def topology_control_key(stream: SignalStream) -> str:
        node_id = _normalize_node(stream.node_id)
        group = _normalize_group(stream.signal_group)
        owner = (node_id, group)
        occurrence = control_occurrences.get(owner, 0)
        control_occurrences[owner] = occurrence + 1
        split_count = control_split_counts.get(owner, 1)
        slot = occurrence % split_count + 1
        suffix = f"_{slot}" if split_count > 1 else ""
        return f"P_{group}{suffix}__S_NONE"

    for index, stream in enumerate(streams, start=1):
        control_key = topology_control_key(stream)
        topology_movements.append(
            OcitVehicleTopologyMovement(
                node_id=stream.node_id,
                connection_id=stream.connection_id,
                ingress_lane_id=stream.ingress_lane_id,
                egress_lane_id=stream.egress_lane_id,
                map_signal_group=stream.signal_group,
                primary_motor_groups=(stream.signal_group,),
                secondary_motor_groups=(),
                topology_control_key=control_key,
                observed_stream_ids=(stream.stream_id,),
                observed_signal_groups=(stream.signal_group,),
            )
        )
        topology_streams.append(
            SignalStream(
                stream_id=-index,
                thing_id=None,
                node_id=stream.node_id,
                connection_id=stream.connection_id,
                ingress_lane_id=stream.ingress_lane_id,
                egress_lane_id=stream.egress_lane_id,
                lane_type="KFZ",
                signal_group=control_key,
                layer_name="primary_signal",
                name=f"topology-{stream.connection_id}",
            )
        )
    template_2421 = next(
        stream for stream in streams if _normalize_node(stream.node_id) == "2421"
    )
    for extra_index in range(1, 7):
        connection_id = f"9{extra_index}"
        control_key = topology_control_key(template_2421)
        topology_movements.append(
            OcitVehicleTopologyMovement(
                node_id=template_2421.node_id,
                connection_id=connection_id,
                ingress_lane_id=template_2421.ingress_lane_id,
                egress_lane_id=template_2421.egress_lane_id,
                map_signal_group="",
                primary_motor_groups=(template_2421.signal_group,),
                secondary_motor_groups=(),
                topology_control_key=control_key,
                observed_stream_ids=(),
                observed_signal_groups=(),
            )
        )
        topology_streams.append(
            SignalStream(
                stream_id=-(len(streams) + extra_index),
                thing_id=None,
                node_id=template_2421.node_id,
                connection_id=connection_id,
                ingress_lane_id=template_2421.ingress_lane_id,
                egress_lane_id=template_2421.egress_lane_id,
                lane_type="KFZ",
                signal_group=control_key,
                layer_name="primary_signal",
                name=f"topology-{connection_id}",
            )
        )
    topology_inventory = OcitVehicleTopologyInventory(
        status="pass",
        source_movement_count=43,
        excluded_non_vehicle_movement_count=10,
        movement_count=33,
        observed_stream_count=27,
        observed_match_count=27,
        group_resolution_policy="test",
        movements=tuple(topology_movements),
        topology_streams=tuple(topology_streams),
    )

    def fake_parse_mapem(path: Path) -> tuple[list[MapLane], list[MapConnection]]:
        node_id = path.name[:4]
        node_streams = [
            stream for stream in streams if _normalize_node(stream.node_id) == node_id
        ]
        roles: dict[str, set[str]] = {}
        for stream in node_streams:
            roles.setdefault(stream.ingress_lane_id, set()).add("ingress")
            roles.setdefault(stream.egress_lane_id, set()).add("egress")
        lanes = [
            MapLane(
                node_id=node_id,
                lane_id=lane_id,
                lane_type="vehicle",
                ingress_approach="1" if "ingress" in lane_roles else "",
                egress_approach="1" if "egress" in lane_roles else "",
                ref_longitude=10.0,
                ref_latitude=53.0,
                points_m=((0.0, 0.0), (1.0, 0.0)),
            )
            for lane_id, lane_roles in sorted(roles.items())
        ]
        connections = [
            MapConnection(
                node_id=node_id,
                connection_id=stream.connection_id,
                ingress_lane_id=stream.ingress_lane_id,
                egress_lane_id=stream.egress_lane_id,
                signal_group=stream.signal_group,
                maneuver_bits="",
            )
            for stream in node_streams
        ]
        return lanes, connections

    def fake_parse_ocit(path: Path) -> OcitCConfig:
        node_id = path.name[:4]
        groups = sorted(
            {
                _normalize_group(stream.signal_group)
                for stream in streams
                if _normalize_node(stream.node_id) == node_id
            }
        )
        return OcitCConfig(
            node_id=node_id,
            node_name=f"node {node_id}",
            motor_signal_groups=tuple(
                OcitMotorSignalGroup(group, str(index), ())
                for index, group in enumerate(groups, start=1)
            ),
            phases=(),
            signal_program_ids=(),
            saturday_plans=(),
            has_vehicle_actuated_control=True,
            saturday_vehicle_actuated=True,
            saturday_plan_semantics="test",
            source_path=str(path),
        )

    def fake_validate(stream_rows, configs) -> PrimarySignalGroupValidation:
        assert len(stream_rows) == 27
        assert len(configs) == 3
        groups = {
            f"{_normalize_node(stream.node_id)}/{_normalize_group(stream.signal_group)}"
            for stream in stream_rows
        }
        return PrimarySignalGroupValidation(
            status="pass",
            primary_stream_count=27,
            checked_group_count=len(groups),
            checked_groups=tuple(sorted(groups)),
        )

    def fake_build_vehicle_topology(
        configs,
        lanes,
        connections,
        observed_streams,
    ) -> OcitVehicleTopologyInventory:
        assert len(configs) == 3
        assert lanes
        assert connections
        assert len(observed_streams) == 27
        return topology_inventory

    def fake_bind_map(_net_file: Path, lanes: list[MapLane]) -> list[MapLaneBinding]:
        return [
            MapLaneBinding(
                node_id=lane.node_id,
                map_lane_id=lane.lane_id,
                map_lane_type=lane.lane_type,
                map_role="ingress" if lane.is_ingress else "egress",
                sumo_edge=f"edge_{lane.node_id}_{lane.lane_id}",
                sumo_lane=f"edge_{lane.node_id}_{lane.lane_id}_0",
                lane_position=5.0,
                distance_m=0.1,
                heading_error_deg=0.0,
                mapping_confidence="high",
                mapping_status="active",
            )
            for lane in lanes
        ]

    def fake_movement_paths(**kwargs):
        assert len(kwargs["official_movements"]) == 33
        assert kwargs["connection_evidence"]
        return tuple(
            HamburgOfficialMovementPath(
                node_id=movement.node_id,
                connection_id=movement.connection_id,
                ingress_lane_id=movement.ingress_lane_id,
                egress_lane_id=movement.egress_lane_id,
                lane_ids=(
                    f"edge_{movement.node_id}_{movement.ingress_lane_id}_0",
                    f"edge_{movement.node_id}_{movement.egress_lane_id}_0",
                ),
            )
            for movement in kwargs["official_movements"]
        )

    def fake_contract(*, node_id: str, **kwargs):
        assert kwargs["movement_paths"]
        return SimpleNamespace(
            node_id=node_id,
            topology_status="ready_for_scoped_teacher_replay",
            review_gates=(),
        )

    def fake_native_replay(*, source_net_file: Path, output_dir: Path, contracts, **_kwargs):
        assert [contract.node_id for contract in contracts] == ["2421", "2394", "0228"]
        output_dir.mkdir(parents=True, exist_ok=True)
        rebuilt = output_dir / "official_tls_native.net.xml"
        rebuilt.write_text(
            "<net>"
            '<tlLogic id="osm_tls"/>'
            '<tlLogic id="HH_2421"/>'
            '<tlLogic id="HH_2394"/>'
            '<tlLogic id="HH_0228"/>'
            '<connection from="a" to="b" tl="HH_2421" linkIndex="0"/>'
            '<connection from="c" to="d" tl="HH_2394" linkIndex="0"/>'
            '<connection from="e" to="f" tl="HH_0228" linkIndex="0"/>'
            "</net>",
            encoding="utf-8",
        )
        manifest = output_dir / "official_tls_native_report.json"
        manifest.write_text('{"status":"pass"}\n', encoding="utf-8")
        return {
            "status": "pass",
            "claim_status": "diagnostic-demo",
            "final_net_file": str(rebuilt),
            "report_file": str(manifest),
            "controller_count": 3,
            "stage_reports": [
                _passing_replay_stage("HH_2421", "HH_2421"),
                _passing_replay_stage("HH_2394", "HH_2394"),
                _passing_replay_stage("HH_0228", "HH_0228", shared=True),
            ],
        }

    def fake_geometry(_source: Path, _candidate: Path, **_kwargs):
        return {
            "status": "pass",
            "external_edge_id_delta_count": 0,
            "geometry_mismatch_count": 0,
        }

    def fake_overlap(_candidate: Path, **_kwargs):
        return {
            "status": "pass",
            "overlap_warning_count": 0,
            "overlap_warnings": [],
            "normalized_probe_net_file": "",
        }

    def fake_endpoint_audit(**_kwargs) -> dict[str, object]:
        topology_indices = {
            _normalize_node(node_id): indices
            for node_id, indices in workflow.topology_control_index_by_node(
                topology_inventory
            ).items()
        }
        rows = []
        for movement in topology_inventory.movements:
            node_id = _normalize_node(movement.node_id)
            stream_suffix = (
                str(movement.observed_stream_ids[0])
                if movement.observed_stream_ids
                else f"extra_{movement.connection_id}"
            )
            rows.append(
                {
                    "status": "pass",
                    "node_id": node_id,
                    "connection_id": movement.connection_id,
                    "official_ingress_lane": movement.ingress_lane_id,
                    "official_egress_lane": movement.egress_lane_id,
                    "topology_control_key": movement.topology_control_key,
                    "observed_stream_ids": list(movement.observed_stream_ids),
                    "sumo_controlled_from_lane": f"from_{stream_suffix}",
                    "sumo_controlled_to_lane": f"to_{stream_suffix}",
                    "sumo_tls_id": f"HH_{node_id}",
                    "sumo_link_index": topology_indices[node_id][
                        movement.topology_control_key
                    ],
                    "errors": [],
                }
            )
        return {
            "status": "pass",
            "movement_count": 33,
            "validated_movement_count": 33,
            "unique_physical_endpoint_count": 33,
            "movements": rows,
            "errors": [],
        }

    real_replay_binder = workflow.bind_observed_streams_to_replayed_tls

    def fake_bind_tls(**kwargs) -> list[TlsBinding]:
        bindings = real_replay_binder(**kwargs)
        return [
            replace(binding, mapping_status="needs_review")
            if binding.stream_id == invalid_stream_id
            else binding
            for binding in bindings
        ]

    monkeypatch.setattr(workflow, "parse_mapem", fake_parse_mapem)
    monkeypatch.setattr(workflow, "parse_ocit_c", fake_parse_ocit)
    monkeypatch.setattr(workflow, "validate_primary_signal_groups", fake_validate)
    monkeypatch.setattr(
        workflow,
        "build_vehicle_topology_inventory",
        fake_build_vehicle_topology,
    )
    monkeypatch.setattr(workflow, "bind_map_lanes_to_network", fake_bind_map)
    monkeypatch.setattr(
        workflow,
        "derive_hamburg_official_movement_paths",
        fake_movement_paths,
    )
    monkeypatch.setattr(workflow, "build_hamburg_teacher_cell_contract", fake_contract)
    monkeypatch.setattr(workflow, "run_hamburg_teacher_replay_workflow", fake_native_replay)
    monkeypatch.setattr(workflow, "audit_hamburg_native_replay_geometry", fake_geometry)
    monkeypatch.setattr(workflow, "audit_sumo_rendered_edge_overlaps", fake_overlap)
    monkeypatch.setattr(
        workflow,
        "run_sumo_load_audit",
        lambda **_kwargs: {"status": "pass"},
    )
    monkeypatch.setattr(
        workflow,
        "_project_contract_lane_bindings",
        lambda **kwargs: (
            list(kwargs["nearest_bindings"]),
            {"status": "pass", "errors": []},
        ),
    )
    monkeypatch.setattr(
        workflow,
        "audit_hamburg_official_movement_endpoints",
        fake_endpoint_audit,
    )
    monkeypatch.setattr(workflow, "bind_observed_streams_to_replayed_tls", fake_bind_tls)


def _normalize_node(value: str) -> str:
    digits = "".join(character for character in str(value) if character.isdigit())
    return digits.zfill(4)


def _normalize_group(value: str) -> str:
    text = value.strip().upper()
    digits = "".join(character for character in text[1:] if character.isdigit())
    letters = "".join(character for character in text[1:] if character.isalpha())
    return f"K{int(digits)}{letters}"
