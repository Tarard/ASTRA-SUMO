"""Build and verify one teacher-guided junction variant."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any
from ..command_runner import run_command
from ..junction_connection_audit import compare_pedestrian_crossing_signatures, compare_tls_movement_signatures
from ..junction_teacher_model import extract_teacher_junction_model
from .artifacts import _command_path, _command_report, _failure, _stage_file, _write_teacher_guided_report
from .connections import write_teacher_connection_plan, write_teacher_vehicle_connection_attrs_net
from .edge_mapping import _approach_endpoint_rebuild_plan, _valid_edge_map
from .geometry import _model_shape_delta
from .lane_inputs import (
    write_missing_edge_type_patch,
    write_teacher_endpoint_patch_nodes,
    write_teacher_lane_patch_edges,
)
from .network import _net_junction_ids, _target_internal_replay_input_file
from .parity import (
    _compare_teacher_models,
    _hybrid_osm_approach_authority_policy,
    _non_target_internal_restore_changed,
    _semantic_layer_gates,
    _teacher_guided_semantics_gate,
)
from .pedestrians import write_teacher_pedestrian_ring_net
from .restoration import _restore_replayed_geometry_attrs, restore_off_scope_netconvert_artifacts
from .scope import _endpoint_rewrite_old_endpoint_ids, _joined_source_node_ids
from .signatures import _model_tls_id
from .target_replay import write_teacher_target_internal_replay_net
from .tls import _restore_false_traffic_light_junction_types, write_teacher_tllogic_net


def build_teacher_guided_junction_variant(
    *,
    raw_node_file: Path,
    raw_edge_file: Path,
    raw_connection_file: Path,
    teacher_net_file: Path,
    candidate_net_file: Path,
    junction_id: str,
    output_dir: Path,
    edge_map: dict[str, str],
    prefix: str = "teacher_guided_junction",
    teacher_junction_id: str | None = None,
    raw_type_file: Path | None = None,
    raw_tllogic_file: Path | None = None,
    crossing_edge_overrides: dict[str, str | list[str]] | None = None,
    approach_endpoint_rebuild_plan: object | None = None,
    source_conflict_core_node_ids: list[str] | None = None,
    replay_target_internal_subgraph: bool = False,
    preserve_teacher_lane_shapes: bool = True,
    emit_teacher_crossings: bool = True,
    prune_unmapped_boundary_edges: bool = False,
    netconvert_binary: str = "netconvert",
    sumo_binary: str = "sumo",
    timeout_seconds: float = 240.0,
    command_runner: Any = run_command,
) -> dict[str, object]:
    teacher_junction_id = teacher_junction_id or junction_id
    missing = [
        str(path)
        for path in (raw_node_file, raw_edge_file, raw_connection_file, teacher_net_file, candidate_net_file)
        if not path.exists()
    ]
    if raw_type_file is not None and not raw_type_file.exists():
        missing.append(str(raw_type_file))
    if raw_tllogic_file is not None and not raw_tllogic_file.exists():
        missing.append(str(raw_tllogic_file))
    if missing:
        return _failure(f"missing input file(s): {', '.join(missing)}")

    # Downstream netconvert stages run from candidate-specific working
    # directories.  Keep every caller-supplied path absolute so a valid plain
    # XML input cannot become unreachable after the working directory changes.
    teacher_net_file = teacher_net_file.resolve()
    candidate_net_file = candidate_net_file.resolve()
    raw_node_file = raw_node_file.resolve()
    raw_edge_file = raw_edge_file.resolve()
    raw_connection_file = raw_connection_file.resolve()
    raw_type_file = raw_type_file.resolve() if raw_type_file is not None else None
    raw_tllogic_file = raw_tllogic_file.resolve() if raw_tllogic_file is not None else None
    output_dir = output_dir.resolve()

    output_dir.mkdir(parents=True, exist_ok=True)
    teacher_model = extract_teacher_junction_model(teacher_net_file, teacher_junction_id)
    candidate_model = extract_teacher_junction_model(candidate_net_file, junction_id)
    inferred_joined_source_node_ids = _joined_source_node_ids(raw_node_file, junction_id)
    joined_source_node_ids = (
        {str(node_id) for node_id in source_conflict_core_node_ids if str(node_id)}
        if source_conflict_core_node_ids is not None
        else inferred_joined_source_node_ids
    )
    source_conflict_core_source = (
        "declared_estimator_evidence"
        if source_conflict_core_node_ids is not None
        else "plain_join_definition"
    )

    lane_shape_delta = _model_shape_delta(teacher_model, candidate_model)
    patched_node_file = _stage_file(output_dir, prefix, "nodes.nod.xml")
    patched_edge_file = _stage_file(output_dir, prefix, "lanes.edg.xml")
    patched_type_file = _stage_file(output_dir, prefix, "types.typ.xml")
    connection_file = _stage_file(output_dir, prefix, "connections.con.xml")
    sidewalks_net_file = _stage_file(output_dir, prefix, "sidewalks.net.xml")
    pedring_net_file = _stage_file(output_dir, prefix, "pedring.net.xml")
    vehicle_attrs_net_file = _stage_file(output_dir, prefix, "vehicle_attrs.net.xml")
    target_internal_replay_file = _stage_file(output_dir, prefix, "target_internal_replay.net.xml")
    target_internal_normalized_net_file = _stage_file(output_dir, prefix, "target_internal_normalized.net.xml")
    target_internal_normalized_unrestored_net_file = _stage_file(
        output_dir, prefix, "target_internal_normalized_unrestored.net.xml"
    )
    target_internal_pedring_net_file = _stage_file(output_dir, prefix, "target_internal_pedring.net.xml")
    target_internal_vehicle_attrs_net_file = _stage_file(output_dir, prefix, "target_internal_vehicle_attrs.net.xml")
    final_net_file = _stage_file(output_dir, prefix, "teacher_guided.net.xml")
    teacher_guided_normalized_net_file = _stage_file(output_dir, "tg", "norm.net.xml")
    fallback_net_file = _stage_file(output_dir, prefix, "teacher_guided_fallback.net.xml")
    report_file = _stage_file(output_dir, prefix, "teacher_guided_report.json")

    lane_patch_report = write_teacher_lane_patch_edges(
        raw_edge_file=raw_edge_file,
        teacher_edge_file=teacher_net_file,
        output_file=patched_edge_file,
        edge_map=edge_map,
        junction_id=junction_id,
        teacher_junction_id=teacher_junction_id,
        boundary_node_ids=joined_source_node_ids,
        prune_unmapped_boundary_edges=prune_unmapped_boundary_edges,
        approach_endpoint_rebuild_plan=approach_endpoint_rebuild_plan,
        lane_shape_delta=lane_shape_delta,
        preserve_lane_shapes=preserve_teacher_lane_shapes,
    )
    internal_restore_exclude_junction_ids = {
        junction_id,
        *_endpoint_rewrite_old_endpoint_ids(lane_patch_report),
    }
    node_patch_report = write_teacher_endpoint_patch_nodes(
        raw_node_file=raw_node_file,
        teacher_net_file=teacher_net_file,
        edge_file=patched_edge_file,
        output_file=patched_node_file,
        lane_shape_delta=lane_shape_delta,
    )
    type_patch_report = write_missing_edge_type_patch(
        raw_type_file=raw_type_file,
        edge_file=patched_edge_file,
        output_file=patched_type_file,
    )
    if type_patch_report.get("status") != "pass":
        return _write_teacher_guided_report(
            report_file,
            {
                "status": "fail",
                "claim_status": "construction-invalid",
                "junction_id": junction_id,
                "teacher_net_file": str(teacher_net_file),
                "candidate_net_file": str(candidate_net_file),
                "node_patch": node_patch_report,
                "lane_patch": lane_patch_report,
                "type_patch": type_patch_report,
            },
        )
    connection_report = write_teacher_connection_plan(
        raw_connection_file=raw_connection_file,
        output_file=connection_file,
        junction_id=junction_id,
        teacher_model=teacher_model,
        candidate_model=candidate_model,
        edge_map=edge_map,
        crossing_edge_overrides=crossing_edge_overrides,
        candidate_edge_file=patched_edge_file,
        crossing_node_ids=joined_source_node_ids,
        emit_crossings=emit_teacher_crossings and not replay_target_internal_subgraph,
        teacher_internal_scope_id=teacher_junction_id if replay_target_internal_subgraph else None,
    )

    netconvert_command = [
        netconvert_binary,
        "--node-files",
        str(patched_node_file),
        "--edge-files",
        _command_path(patched_edge_file, output_dir),
        "--connection-files",
        _command_path(connection_file, output_dir),
        "--output-file",
        _command_path(sidewalks_net_file, output_dir),
        "--walkingareas",
        "true",
        "--tls.ignore-internal-junction-jam",
    ]
    type_file = Path(str(type_patch_report.get("type_file", ""))) if type_patch_report.get("type_file") else None
    if type_file is not None:
        netconvert_command[5:5] = ["--type-files", _command_path(type_file, output_dir)]
    netconvert_result = command_runner(netconvert_command, cwd=output_dir, timeout_seconds=timeout_seconds)
    netconvert_report = _command_report(netconvert_result)
    if netconvert_report.get("status") != "pass":
        return _write_teacher_guided_report(
            report_file,
            {
                "status": "fail",
                "claim_status": "construction-invalid",
                "junction_id": junction_id,
                "teacher_net_file": str(teacher_net_file),
                "candidate_net_file": str(candidate_net_file),
                "netconvert": netconvert_report,
                "node_patch": node_patch_report,
                "lane_patch": lane_patch_report,
                "type_patch": type_patch_report,
                "connection_plan": connection_report,
            },
        )

    non_target_internal_restore_report = restore_off_scope_netconvert_artifacts(
        source_file=candidate_net_file,
        target_file=sidewalks_net_file,
        mutable_junction_ids=internal_restore_exclude_junction_ids,
        mutable_edge_ids=set(edge_map.values()),
        expand_mutable_edge_endpoints=False,
    )
    if non_target_internal_restore_report.get("status") != "pass":
        return _write_teacher_guided_report(
            report_file,
            {
                "status": "fail",
                "claim_status": "construction-invalid",
                "junction_id": junction_id,
                "teacher_net_file": str(teacher_net_file),
                "candidate_net_file": str(candidate_net_file),
                "netconvert": netconvert_report,
                "node_patch": node_patch_report,
                "lane_patch": lane_patch_report,
                "type_patch": type_patch_report,
                "connection_plan": connection_report,
                "non_target_internal_restore": non_target_internal_restore_report,
            },
        )

    pedestrian_ring_report = write_teacher_pedestrian_ring_net(
        candidate_net_file=sidewalks_net_file,
        output_file=pedring_net_file,
        junction_id=junction_id,
        teacher_model=teacher_model,
        edge_map=edge_map,
        teacher_junction_id=teacher_junction_id,
        crossing_edge_overrides=crossing_edge_overrides,
    )
    vehicle_attrs_report = write_teacher_vehicle_connection_attrs_net(
        candidate_net_file=pedring_net_file,
        output_file=vehicle_attrs_net_file,
        junction_id=junction_id,
        teacher_model=teacher_model,
        edge_map=edge_map,
    )
    target_internal_replay_report = None
    target_internal_replay_fallback = False
    target_internal_replay_fallback_tl_logic_report = None
    target_internal_replay_fallback_sumo_report = None
    target_internal_normalize_report = None
    teacher_guided_normalize_report = None
    target_internal_pedestrian_ring_report = None
    target_internal_vehicle_attrs_report = None
    tl_logic_input_file = vehicle_attrs_net_file
    target_internal_replay_input_file = vehicle_attrs_net_file
    if replay_target_internal_subgraph:
        target_internal_replay_input_file = _target_internal_replay_input_file(
            vehicle_attrs_net_file=vehicle_attrs_net_file,
            candidate_net_file=candidate_net_file,
            junction_id=junction_id,
        )
        target_internal_replay_report = write_teacher_target_internal_replay_net(
            candidate_net_file=target_internal_replay_input_file,
            teacher_net_file=teacher_net_file,
            output_file=target_internal_replay_file,
            junction_id=junction_id,
            edge_map=edge_map,
            teacher_junction_id=teacher_junction_id,
            # A joined OSM cell may keep real remote endpoints that do not
            # coincide with the teacher network.  In that case teacher lane
            # geometry is only a semantic template: retain the candidate
            # boundary shapes so every lane still reaches its real endpoint.
            # Single-junction replay keeps teacher geometry for parity.
            geometry_anchor_edge_file=(
                candidate_net_file if len(joined_source_node_ids) > 1 else None
            ),
            blend_geometry_anchor_at_target=len(joined_source_node_ids) > 1,
            copy_unmapped_boundary_edges=False,
            preserve_mapped_boundary_endpoints=True,
        )
        if target_internal_replay_report.get("status") != "pass":
            return _write_teacher_guided_report(
                report_file,
                {
                    "status": "fail",
                    "claim_status": "construction-invalid",
                    "junction_id": junction_id,
                    "teacher_net_file": str(teacher_net_file),
                    "candidate_net_file": str(candidate_net_file),
                    "netconvert": netconvert_report,
                    "node_patch": node_patch_report,
                    "lane_patch": lane_patch_report,
                    "type_patch": type_patch_report,
                    "connection_plan": connection_report,
                    "pedestrian_ring": pedestrian_ring_report,
                    "vehicle_connection_attrs": vehicle_attrs_report,
                    "target_internal_replay_input_file": str(target_internal_replay_input_file),
                    "target_internal_replay": target_internal_replay_report,
                    "target_internal_replay_fallback": target_internal_replay_fallback,
                },
            )
        tl_logic_input_file = target_internal_replay_file

    tl_logic_report = write_teacher_tllogic_net(
        candidate_net_file=tl_logic_input_file,
        output_file=final_net_file,
        junction_id=junction_id,
        teacher_model=teacher_model,
    )
    if tl_logic_report.get("status") != "pass":
        return _write_teacher_guided_report(
            report_file,
            {
                "status": "fail",
                "claim_status": "construction-invalid",
                "junction_id": junction_id,
                "teacher_net_file": str(teacher_net_file),
                "candidate_net_file": str(candidate_net_file),
                "netconvert": netconvert_report,
                "node_patch": node_patch_report,
                "lane_patch": lane_patch_report,
                "type_patch": type_patch_report,
                "connection_plan": connection_report,
                "pedestrian_ring": pedestrian_ring_report,
                "vehicle_connection_attrs": vehicle_attrs_report,
                "target_internal_replay": target_internal_replay_report,
                "target_internal_replay_fallback": target_internal_replay_fallback,
                "target_internal_normalize": target_internal_normalize_report,
                "target_internal_pedestrian_ring": target_internal_pedestrian_ring_report,
                "target_internal_vehicle_connection_attrs": target_internal_vehicle_attrs_report,
                "tl_logic": tl_logic_report,
            },
        )

    sumo_command = [
        sumo_binary,
        "-n",
        _command_path(final_net_file, output_dir),
        "--no-step-log",
        "true",
        "--duration-log.disable",
        "true",
        "--begin",
        "0",
        "--end",
        "1",
    ]
    sumo_report = _command_report(command_runner(sumo_command, cwd=output_dir, timeout_seconds=timeout_seconds))
    if (
        sumo_report.get("status") != "pass"
        and replay_target_internal_subgraph
        and isinstance(target_internal_replay_report, dict)
        and target_internal_replay_report.get("status") == "pass"
    ):
        target_internal_normalize_command = [
            netconvert_binary,
            "--sumo-net-file",
            _command_path(target_internal_replay_file, output_dir),
            "--output-file",
            _command_path(target_internal_normalized_net_file, output_dir),
        ]
        target_internal_normalize_report = _command_report(
            command_runner(target_internal_normalize_command, cwd=output_dir, timeout_seconds=timeout_seconds)
        )
        if target_internal_normalize_report.get("status") == "pass":
            shutil.copyfile(target_internal_normalized_net_file, target_internal_normalized_unrestored_net_file)
            target_internal_normalize_report["unrestored_net_file"] = str(target_internal_normalized_unrestored_net_file)
            target_internal_normalize_report["non_target_internal_restore"] = restore_off_scope_netconvert_artifacts(
                source_file=target_internal_replay_file,
                target_file=target_internal_normalized_net_file,
                mutable_junction_ids=internal_restore_exclude_junction_ids,
                mutable_edge_ids=set(edge_map.values()),
                expand_mutable_edge_endpoints=False,
            )
        if (
            target_internal_normalize_report.get("status") == "pass"
            and target_internal_normalize_report["non_target_internal_restore"].get("status") == "pass"
        ):
            target_internal_normalize_report["false_traffic_light_type_restore"] = (
                _restore_false_traffic_light_junction_types(
                    source_file=target_internal_replay_file,
                    target_file=target_internal_normalized_net_file,
                    fallback_node_file=raw_node_file,
                    exclude_junction_ids=internal_restore_exclude_junction_ids,
                )
            )
            target_internal_normalize_report["geometry_restore"] = _restore_replayed_geometry_attrs(
                source_file=target_internal_replay_file,
                target_file=target_internal_normalized_net_file,
                junction_id=junction_id,
            )
            normalized_tl_logic_report = write_teacher_tllogic_net(
                candidate_net_file=target_internal_normalized_net_file,
                output_file=final_net_file,
                junction_id=junction_id,
                teacher_model=teacher_model,
            )
            target_internal_normalize_report["tl_logic"] = normalized_tl_logic_report
            if normalized_tl_logic_report.get("status") == "pass":
                normalized_sumo_report = _command_report(
                    command_runner(sumo_command, cwd=output_dir, timeout_seconds=timeout_seconds)
                )
                target_internal_normalize_report["sumo_load"] = normalized_sumo_report
                if normalized_sumo_report.get("status") == "pass":
                    tl_logic_report = normalized_tl_logic_report
                    sumo_report = normalized_sumo_report
                elif _non_target_internal_restore_changed(
                    target_internal_normalize_report["non_target_internal_restore"]
                ):
                    target_internal_normalize_report["unrestored_false_traffic_light_type_restore"] = (
                        _restore_false_traffic_light_junction_types(
                            source_file=target_internal_replay_file,
                            target_file=target_internal_normalized_unrestored_net_file,
                            fallback_node_file=raw_node_file,
                            exclude_junction_ids=internal_restore_exclude_junction_ids,
                        )
                    )
                    target_internal_normalize_report["unrestored_geometry_restore"] = _restore_replayed_geometry_attrs(
                        source_file=target_internal_replay_file,
                        target_file=target_internal_normalized_unrestored_net_file,
                        junction_id=junction_id,
                    )
                    unrestored_tl_logic_report = write_teacher_tllogic_net(
                        candidate_net_file=target_internal_normalized_unrestored_net_file,
                        output_file=final_net_file,
                        junction_id=junction_id,
                        teacher_model=teacher_model,
                    )
                    target_internal_normalize_report["unrestored_tl_logic"] = unrestored_tl_logic_report
                    if unrestored_tl_logic_report.get("status") == "pass":
                        unrestored_sumo_report = _command_report(
                            command_runner(sumo_command, cwd=output_dir, timeout_seconds=timeout_seconds)
                        )
                        target_internal_normalize_report["unrestored_sumo_load"] = unrestored_sumo_report
                        if unrestored_sumo_report.get("status") == "pass":
                            tl_logic_report = unrestored_tl_logic_report
                            sumo_report = unrestored_sumo_report
    if (
        sumo_report.get("status") != "pass"
        and replay_target_internal_subgraph
        and isinstance(target_internal_replay_report, dict)
        and target_internal_replay_report.get("status") == "pass"
    ):
        teacher_guided_normalize_command = [
            netconvert_binary,
            "--sumo-net-file",
            _command_path(final_net_file, output_dir),
            "--output-file",
            _command_path(teacher_guided_normalized_net_file, output_dir),
        ]
        teacher_guided_normalize_report = _command_report(
            command_runner(teacher_guided_normalize_command, cwd=output_dir, timeout_seconds=timeout_seconds)
        )
        if teacher_guided_normalize_report.get("status") == "pass":
            teacher_guided_normalize_report["non_target_internal_restore"] = restore_off_scope_netconvert_artifacts(
                source_file=final_net_file,
                target_file=teacher_guided_normalized_net_file,
                mutable_junction_ids=internal_restore_exclude_junction_ids,
                mutable_edge_ids=set(edge_map.values()),
                expand_mutable_edge_endpoints=False,
            )
        if (
            teacher_guided_normalize_report.get("status") == "pass"
            and teacher_guided_normalize_report["non_target_internal_restore"].get("status") == "pass"
        ):
            teacher_guided_normalize_report["false_traffic_light_type_restore"] = (
                _restore_false_traffic_light_junction_types(
                    source_file=final_net_file,
                    target_file=teacher_guided_normalized_net_file,
                    fallback_node_file=raw_node_file,
                    exclude_junction_ids=internal_restore_exclude_junction_ids,
                )
            )
            teacher_guided_normalize_report["geometry_restore"] = _restore_replayed_geometry_attrs(
                source_file=final_net_file,
                target_file=teacher_guided_normalized_net_file,
                junction_id=junction_id,
            )
            normalized_final_sumo_command = [
                sumo_binary,
                "-n",
                _command_path(teacher_guided_normalized_net_file, output_dir),
                "--no-step-log",
                "true",
                "--duration-log.disable",
                "true",
                "--begin",
                "0",
                "--end",
                "1",
            ]
            normalized_final_sumo_report = _command_report(
                command_runner(normalized_final_sumo_command, cwd=output_dir, timeout_seconds=timeout_seconds)
            )
            teacher_guided_normalize_report["sumo_load"] = normalized_final_sumo_report
            if normalized_final_sumo_report.get("status") == "pass":
                final_net_file = teacher_guided_normalized_net_file
                sumo_report = normalized_final_sumo_report
    if (
        sumo_report.get("status") != "pass"
        and replay_target_internal_subgraph
        and isinstance(target_internal_replay_report, dict)
        and target_internal_replay_report.get("status") == "pass"
    ):
        target_internal_replay_fallback_tl_logic_report = write_teacher_tllogic_net(
            candidate_net_file=vehicle_attrs_net_file,
            output_file=fallback_net_file,
            junction_id=junction_id,
            teacher_model=teacher_model,
        )
        if target_internal_replay_fallback_tl_logic_report.get("status") == "pass":
            fallback_sumo_command = [
                sumo_binary,
                "-n",
                _command_path(fallback_net_file, output_dir),
                "--no-step-log",
                "true",
                "--duration-log.disable",
                "true",
                "--begin",
                "0",
                "--end",
                "1",
            ]
            target_internal_replay_fallback_sumo_report = _command_report(
                command_runner(fallback_sumo_command, cwd=output_dir, timeout_seconds=timeout_seconds)
            )
            if target_internal_replay_fallback_sumo_report.get("status") == "pass":
                target_internal_replay_fallback = True
                final_net_file = fallback_net_file
                tl_logic_report = target_internal_replay_fallback_tl_logic_report
                sumo_report = target_internal_replay_fallback_sumo_report
    final_model = extract_teacher_junction_model(final_net_file, junction_id)
    comparison_edge_map = edge_map
    if (
        replay_target_internal_subgraph
        and not target_internal_replay_fallback
        and isinstance(target_internal_replay_report, dict)
    ):
        comparison_edge_map = _valid_edge_map(target_internal_replay_report.get("effective_edge_map", {})) or edge_map
    parity = _compare_teacher_models(
        teacher_model,
        final_model,
        edge_map=comparison_edge_map,
        teacher_junction_id=teacher_junction_id,
        candidate_junction_id=junction_id,
    )
    target_internal_replay_gate_report = None if target_internal_replay_fallback else target_internal_replay_report
    approach_endpoint_rebuild_plan = _approach_endpoint_rebuild_plan(
        teacher_model,
        final_model,
        edge_map=comparison_edge_map,
        teacher_junction_id=teacher_junction_id,
        candidate_junction_id=junction_id,
        candidate_junction_ids=_net_junction_ids(final_net_file),
    )
    semantic_gate = _teacher_guided_semantics_gate(
        parity,
        pedestrian_ring=pedestrian_ring_report,
        vehicle_connection_attrs=vehicle_attrs_report,
        target_internal_replay=target_internal_replay_gate_report,
        target_internal_pedestrian_ring=target_internal_pedestrian_ring_report,
        target_internal_vehicle_connection_attrs=target_internal_vehicle_attrs_report,
    )
    teacher_tls_id = _model_tls_id(teacher_model, fallback=teacher_junction_id)
    candidate_tls_id = _model_tls_id(final_model, fallback=junction_id)
    tls_movement_parity = compare_tls_movement_signatures(
        teacher_net_file,
        final_net_file,
        teacher_tls_id,
        candidate_tls_id,
        teacher_edge_map=comparison_edge_map,
        teacher_internal_scope_id=teacher_junction_id if replay_target_internal_subgraph else None,
        candidate_internal_scope_id=junction_id if replay_target_internal_subgraph else None,
    )
    pedestrian_crossing_parity = compare_pedestrian_crossing_signatures(
        teacher_net_file,
        final_net_file,
        teacher_junction_id,
        junction_id,
        teacher_edge_map=comparison_edge_map,
    )
    approach_authority_policy = _hybrid_osm_approach_authority_policy(
        semantic_gate,
        replay_target_internal_subgraph=replay_target_internal_subgraph,
        preserve_teacher_lane_shapes=preserve_teacher_lane_shapes,
        edge_map=comparison_edge_map,
        lane_patch=lane_patch_report,
        target_internal_replay=target_internal_replay_gate_report,
        tls_movement_parity=tls_movement_parity,
        pedestrian_crossing_parity=pedestrian_crossing_parity,
    )
    effective_semantic_gate = approach_authority_policy["effective_semantic_gate"]
    semantic_layer_gates = _semantic_layer_gates(
        effective_semantic_gate,
        tls_movement_parity,
        pedestrian_crossing_parity,
    )
    parity_gate_status = (
        "pass"
        if effective_semantic_gate["status"] == "pass"
        and tls_movement_parity["status"] == "pass"
        and pedestrian_crossing_parity["status"] == "pass"
        else "fail"
    )
    status = "pass" if sumo_report.get("status") == "pass" else "fail"
    return _write_teacher_guided_report(
        report_file,
        {
            "status": status,
            "claim_status": "diagnostic-demo" if status == "pass" else "construction-invalid",
            "parity_gate_status": parity_gate_status,
            "junction_id": junction_id,
            "source_conflict_core_node_ids": sorted(joined_source_node_ids),
            "source_conflict_core_source": source_conflict_core_source,
            "teacher_net_file": str(teacher_net_file),
            "candidate_net_file": str(candidate_net_file),
            "final_net_file": str(final_net_file),
            "patched_node_file": str(patched_node_file),
            "patched_edge_file": str(patched_edge_file),
            "connection_file": str(connection_file),
            "sidewalks_net_file": str(sidewalks_net_file),
            "pedring_net_file": str(pedring_net_file),
            "vehicle_attrs_net_file": str(vehicle_attrs_net_file),
            "target_internal_replay_input_file": str(target_internal_replay_input_file)
            if replay_target_internal_subgraph
            else "",
            "target_internal_replay_file": str(target_internal_replay_file) if replay_target_internal_subgraph else "",
            "target_internal_replay_fallback": target_internal_replay_fallback,
            "target_internal_replay_fallback_net_file": str(fallback_net_file) if target_internal_replay_fallback else "",
            "target_internal_normalized_net_file": str(target_internal_normalized_net_file)
            if target_internal_normalize_report
            else "",
            "teacher_guided_normalized_net_file": str(teacher_guided_normalized_net_file)
            if teacher_guided_normalize_report
            else "",
            "target_internal_pedring_net_file": str(target_internal_pedring_net_file)
            if target_internal_pedestrian_ring_report
            else "",
            "target_internal_vehicle_attrs_net_file": str(target_internal_vehicle_attrs_net_file)
            if target_internal_vehicle_attrs_report
            else "",
            "report_file": str(report_file),
            "node_patch": node_patch_report,
            "lane_patch": lane_patch_report,
            "type_patch": type_patch_report,
            "connection_plan": connection_report,
            "netconvert": netconvert_report,
            "non_target_internal_restore": non_target_internal_restore_report,
            "pedestrian_ring": pedestrian_ring_report,
            "vehicle_connection_attrs": vehicle_attrs_report,
            "target_internal_replay": target_internal_replay_report,
            "target_internal_replay_fallback_tl_logic": target_internal_replay_fallback_tl_logic_report,
            "target_internal_replay_fallback_sumo": target_internal_replay_fallback_sumo_report,
            "target_internal_normalize": target_internal_normalize_report,
            "teacher_guided_normalize": teacher_guided_normalize_report,
            "target_internal_pedestrian_ring": target_internal_pedestrian_ring_report,
            "target_internal_vehicle_connection_attrs": target_internal_vehicle_attrs_report,
            "tl_logic": tl_logic_report,
            "sumo_load": sumo_report,
            "parity": parity,
            "approach_endpoint_rebuild_plan": approach_endpoint_rebuild_plan,
            "semantic_replay_gate": semantic_gate,
            "semantic_replay_effective_gate": effective_semantic_gate,
            "approach_authority_policy": approach_authority_policy,
            "tls_movement_parity": tls_movement_parity,
            "pedestrian_crossing_parity": pedestrian_crossing_parity,
            "semantic_layer_gates": semantic_layer_gates,
            "review_policy": "diagnostic teacher-guided variant; inspect in NetEdit connection mode before adoption",
        },
    )
