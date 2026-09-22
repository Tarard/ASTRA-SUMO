from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping
from xml.etree import ElementTree as ET

from torii_sumo.corridor.audit_pipeline import (
    build_exact_semantic_regression_artifacts,
)
from torii_sumo.corridor.enums import TrafficSide
from torii_sumo.corridor.netxml import normalized_net_sha256
from torii_sumo.intersection.cell_candidate_binding import (
    bind_topology_candidate_to_dag,
)
from torii_sumo.intersection.topology_discrimination_experiment import (
    write_topology_node_patch,
)

from .artifact_io import (
    relative_or_absolute_path,
    write_json_atomic,
    write_text_atomic,
)
from .candidate_contracts import file_sha256
from .cell_movement_routeability import run_bound_cell_movement_smoke
from .command_runner import run_command
from .sumo_commands import run_sumo_load_audit
from .tls_ownership import (
    audit_topology_variant_tls_ownership,
    tls_scope_inventory,
)


def run_topology_variant(
    *,
    source_osm: Path,
    source_osm_sha256: str,
    source_net: Path,
    source_scope: tuple[str, ...],
    guard_source_ids: tuple[str, ...],
    candidate_plan: Mapping[str, Any],
    contract: Mapping[str, Any],
    physical_cell: Mapping[str, Any],
    movement_hypotheses: Mapping[str, Any],
    candidate_dag: Mapping[str, Any],
    output_dir: Path,
    toolchain_lock_file: Path,
    netconvert_binary: str,
    sumo_binary: str,
    traffic_side: str,
    timeout_seconds: float,
    producer: Mapping[str, Any],
) -> dict[str, Any]:
    """Materialize and audit one immutable topology arm."""

    destination = output_dir.resolve()
    destination.mkdir(parents=True, exist_ok=True)
    topology = str(candidate_plan["topology_hypothesis"])
    patch_file = destination / "topology.nod.xml"
    candidate_net = destination / "candidate.net.xml"
    build_file = destination / "netconvert-build.json"
    write_topology_node_patch(
        patch_file,
        contract=contract,
        candidate_plan_id=str(candidate_plan["candidate_plan_id"]),
    )
    if file_sha256(source_osm) != source_osm_sha256:
        raise RuntimeError("The frozen source OSM changed before variant materialization.")

    relative_osm = relative_or_absolute_path(source_osm, destination)
    command = [
        netconvert_binary,
        "--osm-files",
        relative_osm,
        "--node-files",
        patch_file.name,
        "--proj.utm",
        "--no-turnarounds",
        "--osm.all-attributes",
        "--verbose",
        "--output-file",
        candidate_net.name,
    ]
    command_result = run_command(
        command,
        cwd=destination,
        timeout_seconds=timeout_seconds,
    ).to_dict()
    build_pass = _command_built_file(command_result, candidate_net)
    build_report = {
        "schema": "torii.topology-variant-netconvert-build/v1",
        "status": "pass" if build_pass else "fail",
        "topology_hypothesis": topology,
        "candidate_plan_id": candidate_plan["candidate_plan_id"],
        "candidate_dag_node_id": candidate_plan["candidate_dag_node_id"],
        "declared_operation": candidate_plan["declared_operation"],
        "producer": dict(producer),
        "selection_is_topology_truth_claim": False,
        "source_osm_mutation": file_sha256(source_osm) != source_osm_sha256,
        "command": command,
        "command_result": command_result,
        "patch": {
            "path": str(patch_file),
            "sha256": file_sha256(patch_file),
        },
        "candidate": {
            "path": str(candidate_net),
            "sha256": file_sha256(candidate_net) if candidate_net.is_file() else None,
            "normalized_sha256": (
                normalized_net_sha256(candidate_net) if build_pass else None
            ),
        },
    }
    write_json_atomic(build_file, build_report, sort_keys=True)
    if not build_pass or build_report["source_osm_mutation"]:
        return _blocked_variant(
            candidate_plan=candidate_plan,
            destination=destination,
            stage="materialization",
            reason="netconvert build or source immutability gate failed",
            producer=producer,
            artifacts={
                "node_patch": str(patch_file),
                "build_report": str(build_file),
            },
        )

    target_candidate_ids = tuple(map(str, candidate_plan["target_junction_ids"]))
    candidate_junction_ids = _junction_ids(candidate_net)
    guard_candidate_ids = tuple(
        item for item in guard_source_ids if item in candidate_junction_ids
    )
    source_inventory = tls_scope_inventory(
        source_net,
        scope_junction_ids=source_scope,
    )
    if topology == "preserve_split_shared_controller":
        expected_controlled_count = int(
            source_inventory["scope_controlled_connection_count"]
        )
    else:
        expected_controlled_count = int(
            candidate_plan["movement_metrics"]["movement_count"]
        )

    tls_ownership = audit_topology_variant_tls_ownership(
        source_net=source_net,
        candidate_net=candidate_net,
        target_source_junction_ids=source_scope,
        target_candidate_junction_ids=target_candidate_ids,
        expected_tls_junction_ids=tuple(
            map(str, candidate_plan["expected_tls_junction_ids"])
        ),
        expected_controller_ids=(str(candidate_plan["target_controller_id"]),),
        retained_source_junction_ids=tuple(
            map(str, candidate_plan["retained_source_junction_ids"])
        ),
        removed_source_junction_ids=tuple(
            map(str, candidate_plan["removed_source_junction_ids"])
        ),
        expected_controlled_connection_count=expected_controlled_count,
    )
    tls_file = destination / "tls-ownership.json"
    write_json_atomic(tls_file, tls_ownership, sort_keys=True)

    binding = bind_topology_candidate_to_dag(
        candidate_net=candidate_net,
        candidate_plan=candidate_plan,
        physical_cell=physical_cell,
        movement_hypotheses=movement_hypotheses,
        candidate_dag=candidate_dag,
        tls_ownership=tls_ownership,
    )
    binding_file = destination / "cell-movement-binding.json"
    write_json_atomic(binding_file, binding, sort_keys=True)

    profile = contract["experiment_profile"]
    exact = build_exact_semantic_regression_artifacts(
        source_net,
        candidate_net,
        output_dir=destination / "exact-audit",
        toolchain_lock_file=toolchain_lock_file,
        traffic_side=TrafficSide(traffic_side),
        target_source_junction_ids=source_scope,
        target_candidate_junction_ids=target_candidate_ids,
        guard_source_junction_ids=guard_source_ids,
        guard_candidate_junction_ids=guard_candidate_ids,
        endpoint_tolerance_m=float(profile["audit"]["endpoint_tolerance_m"]),
        normalized_lane_rank_tolerance=float(
            profile["audit"]["normalized_lane_rank_tolerance"]
        ),
        prefix=f"topology-v4-{_topology_token(topology)}",
    )
    exact_diff = _read_json(Path(exact["files"]["exact_diff"]))
    candidate_safety = _read_json(Path(exact["files"]["candidate_safety"]))
    candidate_connection = _read_json(
        Path(exact["files"]["candidate_connection_audit"])
    )
    target_connection = _target_connection_summary(
        candidate_connection,
        target_junction_ids=target_candidate_ids,
    )

    sumo_load = run_sumo_load_audit(
        net_file=candidate_net,
        output_dir=destination / "sumo-load",
        sumo_binary=sumo_binary,
        timeout_seconds=timeout_seconds,
    )
    routeability = run_bound_cell_movement_smoke(
        net_file=candidate_net,
        movement_binding=binding,
        output_dir=destination / "cell-movement-routeability",
        sumo_binary=sumo_binary,
        departure_interval_s=int(profile["runtime"]["departure_interval_s"]),
        end_time_s=int(profile["runtime"]["end_time_s"]),
        timeout_seconds=timeout_seconds,
    )
    outside_zero = not exact_diff.get(
        "outside_scope_delta_ids"
    ) and not exact_diff.get("outside_scope_added_finding_ids")
    gates = {
        "candidate_netconvert": "pass",
        "source_osm_immutable": (
            "pass" if file_sha256(source_osm) == source_osm_sha256 else "fail"
        ),
        "tls_ownership": str(tls_ownership["status"]),
        "cell_movement_binding": str(binding["binding_status"]),
        "movement_semantics_exact": (
            "pass" if binding.get("semantic_disposition") == "suggest" else "review"
        ),
        "target_connection_mode": str(target_connection["status"]),
        "independent_conflict_safety": str(candidate_safety["status"]),
        "outside_scope_exact_zero_delta": "pass" if outside_zero else "fail",
        "sumo_load": str(sumo_load["status"]),
        "all_movement_routeability": str(routeability["status"]),
    }
    machine_feasible = all(status == "pass" for status in gates.values())
    status = "review_ready" if machine_feasible else "blocked"
    rollback = {
        "schema": "torii.topology-variant-rollback/v1",
        "status": "available",
        "candidate_plan_id": candidate_plan["candidate_plan_id"],
        "topology_hypothesis": topology,
        "source_osm": {"path": str(source_osm), "sha256": source_osm_sha256},
        "source_net": {
            "path": str(source_net),
            "sha256": file_sha256(source_net),
            "normalized_sha256": normalized_net_sha256(source_net),
        },
        "candidate_net": {
            "path": str(candidate_net),
            "sha256": file_sha256(candidate_net),
            "normalized_sha256": normalized_net_sha256(candidate_net),
        },
        "forward_operation": {
            "type": candidate_plan["declared_operation"],
            "patch_path": str(patch_file),
            "patch_sha256": file_sha256(patch_file),
        },
        "inverse_operation": {
            "type": "rebuild_without_topology_patch",
            "source_mutation": False,
        },
        "producer": dict(producer),
        "automatic_topology_selection": False,
        "automatic_promotion_gate": "blocked",
        "scope_expansion_allowed": False,
    }
    rollback_file = destination / "rollback.json"
    write_json_atomic(rollback_file, rollback, sort_keys=True)
    overlay_file = destination / "review.add.xml"
    _write_review_overlay(
        overlay_file,
        candidate_net=candidate_net,
        target_junction_ids=target_candidate_ids,
        candidate_plan_id=str(candidate_plan["candidate_plan_id"]),
    )
    summary = {
        "schema": "torii.teacher-free-topology-variant/v1",
        "status": status,
        "machine_feasible": machine_feasible,
        "workflow_state": "REVIEW_PENDING" if machine_feasible else "BLOCKED",
        "topology_hypothesis": topology,
        "candidate_plan_id": candidate_plan["candidate_plan_id"],
        "candidate_dag_node_id": candidate_plan["candidate_dag_node_id"],
        "binding_id": binding["binding_id"],
        "automatic_topology_selection": False,
        "automatic_promotion_gate": "blocked",
        "field_timing_reconstruction": False,
        "scope_expansion_allowed": False,
        "producer": dict(producer),
        "source_mutation": file_sha256(source_osm) != source_osm_sha256,
        "target_junction_ids": list(target_candidate_ids),
        "target_controller_id": candidate_plan["target_controller_id"],
        "candidate_net": rollback["candidate_net"],
        "gates": gates,
        "target_connection": target_connection,
        "cell_movement_binding": {
            "status": binding["binding_status"],
            "movement_count": binding["movement_count"],
            "exact_movement_variant_ids": binding["exact_movement_variant_ids"],
            "structural_findings": binding["structural_findings"],
            "semantic_findings": binding["semantic_findings"],
        },
        "independent_conflict_safety": {
            "status": candidate_safety["status"],
            "conflict_count": len(
                candidate_safety.get("conflict_graph", {}).get("conflicts", ())
            ),
            "finding_count": len(candidate_safety.get("findings", ())),
        },
        "outside_scope_exact_diff": {
            "status": "pass" if outside_zero else "fail",
            "outside_scope_delta_ids": exact_diff.get("outside_scope_delta_ids", []),
            "outside_scope_added_finding_ids": exact_diff.get(
                "outside_scope_added_finding_ids", []
            ),
        },
        "sumo_load": {
            "status": sumo_load["status"],
            "report_file": sumo_load["report_file"],
        },
        "all_movement_routeability": {
            "status": routeability["status"],
            "movement_count": routeability["movement_count"],
            "arrived_vehicle_count": len(routeability["arrived_vehicle_ids"]),
            "report_file": routeability["report_file"],
        },
        "artifacts": {
            "node_patch": str(patch_file),
            "build_report": str(build_file),
            "tls_ownership": str(tls_file),
            "cell_movement_binding": str(binding_file),
            "exact_audit_manifest": exact["files"]["manifest"],
            "rollback": str(rollback_file),
            "review_overlay": str(overlay_file),
        },
        "claim_boundary": (
            "Machine-feasible means one preregistered topology arm passed the "
            "listed gates. It is not a real-world topology or timing decision."
        ),
    }
    summary_file = destination / "summary.json"
    write_json_atomic(summary_file, summary, sort_keys=True)
    manifest_file = destination / "manifest.json"
    _write_manifest(
        manifest_file,
        destination=destination,
        status=status,
        source_osm=source_osm,
        source_osm_sha256=source_osm_sha256,
        toolchain_lock_file=toolchain_lock_file,
        candidate_plan=candidate_plan,
        gates=gates,
        producer=producer,
    )
    return {
        **summary,
        "summary_file": str(summary_file),
        "manifest_file": str(manifest_file),
    }


def _target_connection_summary(
    report: Mapping[str, Any],
    *,
    target_junction_ids: tuple[str, ...],
) -> dict[str, Any]:
    by_id = {
        str(item.get("junction_id")): item
        for item in report.get("junctions", ())
        if item.get("junction_id")
    }
    missing = sorted(set(target_junction_ids) - set(by_id))
    records = [by_id[item] for item in target_junction_ids if item in by_id]
    statuses = [
        str(item["connection_mode_audit"]["status"]) for item in records
    ]
    status = "pass" if records and not missing and all(item == "pass" for item in statuses) else "fail"
    return {
        "status": status,
        "target_junction_ids": list(target_junction_ids),
        "missing_junction_ids": missing,
        "audited_junction_count": len(records),
        "direct_movement_count": sum(
            int(item["connection_mode_audit"]["direct_movement_count"])
            for item in records
        ),
        "verified_internal_path_count": sum(
            int(item["connection_mode_audit"]["verified_internal_path_count"])
            for item in records
        ),
        "structural_failure_count": sum(
            len(item["connection_mode_audit"]["structural_failures"])
            for item in records
        ),
        "review_finding_count": sum(
            len(item["connection_mode_audit"]["review_findings"])
            for item in records
        ),
        "junction_statuses": {
            str(item["junction_id"]): str(item["connection_mode_audit"]["status"])
            for item in records
        },
    }


def _write_manifest(
    path: Path,
    *,
    destination: Path,
    status: str,
    source_osm: Path,
    source_osm_sha256: str,
    toolchain_lock_file: Path,
    candidate_plan: Mapping[str, Any],
    gates: Mapping[str, str],
    producer: Mapping[str, Any],
) -> None:
    artifacts = []
    for artifact in sorted(destination.rglob("*")):
        if not artifact.is_file() or artifact == path:
            continue
        artifacts.append(
            {
                "role": "generated",
                "path": str(artifact),
                "sha256": file_sha256(artifact),
            }
        )
    write_json_atomic(
        path,
        {
            "schema": "torii.topology-variant-manifest/v1",
            "status": status,
            "candidate_plan_id": candidate_plan["candidate_plan_id"],
            "candidate_dag_node_id": candidate_plan["candidate_dag_node_id"],
            "topology_hypothesis": candidate_plan["topology_hypothesis"],
            "automatic_topology_selection": False,
            "automatic_promotion_gate": "blocked",
            "scope_expansion_allowed": False,
            "producer": dict(producer),
            "inputs": [
                {
                    "role": "frozen_osm_bbox",
                    "path": str(source_osm),
                    "sha256": source_osm_sha256,
                },
                {
                    "role": "toolchain_lock",
                    "path": str(toolchain_lock_file),
                    "sha256": file_sha256(toolchain_lock_file),
                },
            ],
            "gates": dict(gates),
            "artifacts": artifacts,
        },
        sort_keys=True,
    )


def _blocked_variant(
    *,
    candidate_plan: Mapping[str, Any],
    destination: Path,
    stage: str,
    reason: str,
    producer: Mapping[str, Any],
    artifacts: Mapping[str, str],
) -> dict[str, Any]:
    summary = {
        "schema": "torii.teacher-free-topology-variant/v1",
        "status": "blocked",
        "machine_feasible": False,
        "workflow_state": "BLOCKED",
        "topology_hypothesis": candidate_plan["topology_hypothesis"],
        "candidate_plan_id": candidate_plan["candidate_plan_id"],
        "candidate_dag_node_id": candidate_plan["candidate_dag_node_id"],
        "automatic_topology_selection": False,
        "automatic_promotion_gate": "blocked",
        "field_timing_reconstruction": False,
        "scope_expansion_allowed": False,
        "producer": dict(producer),
        "terminal_stage": stage,
        "reason": reason,
        "artifacts": dict(artifacts),
    }
    summary_file = destination / "summary.json"
    write_json_atomic(summary_file, summary, sort_keys=True)
    return {**summary, "summary_file": str(summary_file)}


def _command_built_file(result: Mapping[str, Any], path: Path) -> bool:
    return (
        result.get("status") == "pass"
        and result.get("returncode") == 0
        and path.is_file()
        and path.stat().st_size > 0
    )


def _junction_ids(net_file: Path) -> set[str]:
    return {
        str(item.attrib["id"])
        for item in ET.parse(net_file).getroot().findall("junction")
        if item.attrib.get("id")
    }


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _topology_token(topology: str) -> str:
    return {
        "preserve_split_shared_controller": "hs",
        "merge_physical_cell": "hm",
        "partial_internal_repair": "hp",
    }[topology]


def _write_review_overlay(
    path: Path,
    *,
    candidate_net: Path,
    target_junction_ids: tuple[str, ...],
    candidate_plan_id: str,
) -> None:
    junctions = {
        str(item.attrib.get("id", "")): item
        for item in ET.parse(candidate_net).getroot().findall("junction")
        if item.attrib.get("id")
    }
    root = ET.Element("additional")
    for index, junction_id in enumerate(target_junction_ids, start=1):
        junction = junctions.get(junction_id)
        if junction is None:
            continue
        shape = str(junction.attrib.get("shape", "")).strip()
        if shape:
            marker = ET.SubElement(
                root,
                "poly",
                id=f"topology_v4_target_{index}",
                color="0,210,120,110",
                fill="true",
                layer="100",
                shape=shape,
            )
        else:
            marker = ET.SubElement(
                root,
                "poi",
                id=f"topology_v4_target_{index}",
                color="0,210,120,255",
                layer="100",
                x=junction.attrib.get("x", "0"),
                y=junction.attrib.get("y", "0"),
            )
        ET.SubElement(marker, "param", key="candidate_plan_id", value=candidate_plan_id)
        ET.SubElement(marker, "param", key="junction_id", value=junction_id)
        ET.SubElement(
            marker,
            "param",
            key="automatic_promotion_gate",
            value="blocked",
        )
    ET.indent(root, space="  ")
    payload = ET.tostring(root, encoding="unicode")
    write_text_atomic(
        path,
        f"<?xml version='1.0' encoding='utf-8'?>\n{payload}\n",
    )
