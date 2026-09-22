from __future__ import annotations

import html
import json
from pathlib import Path
import shutil
from typing import Any, Mapping
from xml.etree import ElementTree as ET

from torii_sumo.corridor.audit_pipeline import (
    build_exact_semantic_regression_artifacts,
)
from torii_sumo.corridor.enums import TrafficSide
from torii_sumo.corridor.netxml import normalized_net_sha256
from torii_sumo.intersection.autodiscovery import (
    discover_teacher_free_intersections,
)
from torii_sumo.intersection.candidate_binding import (
    bind_materialized_candidate_to_dag,
)
from torii_sumo.intersection.materialization_experiment import (
    build_preregistered_materialization_contract,
    write_preregistered_join_patch,
)

from .artifact_io import (
    relative_or_absolute_path,
    write_json_atomic,
    write_text_atomic,
)
from .candidate_contracts import file_sha256
from .command_runner import run_command
from .movement_routeability import run_all_turn_movement_smoke
from .sumo_commands import discover_binaries, run_sumo_load_audit
from .tls_ownership import audit_tls_ownership_rebuild


_OWNER_SCHEMA = "torii.teacher-free-materialization-owner/v1"


def run_teacher_free_materialization_workflow(
    *,
    osm_file: Path,
    output_dir: Path,
    traffic_side: str,
    toolchain_lock_file: Path,
    binaries: Mapping[str, str | None] | None = None,
    timeout_seconds: float = 120.0,
) -> dict[str, Any]:
    """Run the v3 teacher-free experimental materialization closure.

    Discovery and preflight complete before binary lookup or candidate writes.
    A generated network is an immutable experimental variant.  It remains
    promotion-blocked even if every machine evidence gate passes.
    """

    source_osm = osm_file.resolve(strict=True)
    toolchain_lock = toolchain_lock_file.resolve(strict=True)
    destination = output_dir.resolve()
    if destination in source_osm.parents or destination == source_osm.parent:
        raise ValueError(
            "The frozen source OSM must not be stored inside the generated output directory."
        )
    source_osm_sha256 = file_sha256(source_osm)
    _reset_owned_directory(destination)

    discovery = discover_teacher_free_intersections(
        source_osm,
        traffic_side=traffic_side,
    )
    discovery_file = destination / "teacher-free-discovery.json"
    write_json_atomic(discovery_file, discovery, sort_keys=True)
    contract = build_preregistered_materialization_contract(discovery)
    contract_file = destination / "materialization-contract.json"
    write_json_atomic(contract_file, contract, sort_keys=True)

    if contract["status"] != "ready":
        return _write_terminal_bundle(
            destination=destination,
            status=str(contract["status"]),
            source_osm=source_osm,
            source_osm_sha256=source_osm_sha256,
            toolchain_lock=toolchain_lock,
            discovery=discovery,
            contract=contract,
            details={
                "terminal_stage": "pre_materialization",
                "reason": (
                    "no applicable vehicle cell"
                    if contract["status"] == "not_applicable"
                    else "pre-materialization gates blocked candidate writing"
                ),
            },
        )

    selected_binaries = dict(binaries or discover_binaries())
    missing_binaries = [
        name for name in ("netconvert", "sumo") if not selected_binaries.get(name)
    ]
    if missing_binaries:
        return _write_terminal_bundle(
            destination=destination,
            status="blocked",
            source_osm=source_osm,
            source_osm_sha256=source_osm_sha256,
            toolchain_lock=toolchain_lock,
            discovery=discovery,
            contract=contract,
            details={
                "terminal_stage": "toolchain_resolution",
                "reason": "required SUMO binaries are unavailable",
                "missing_binaries": missing_binaries,
            },
        )

    plan = dict(contract["candidate_plan"])
    discovered_candidate = _candidate_by_id(
        discovery,
        str(plan["discovered_candidate_id"]),
    )
    hypothesis = discovered_candidate["hypothesis"]
    candidate_dag = hypothesis["candidate_dag"]
    candidate_dag_file = destination / "candidate-dag.json"
    write_json_atomic(candidate_dag_file, candidate_dag, sort_keys=True)

    join_patch = destination / "candidate-join.nod.xml"
    write_preregistered_join_patch(join_patch, contract=contract)
    if file_sha256(source_osm) != source_osm_sha256:
        raise RuntimeError("The frozen source OSM changed before materialization.")

    source_net = destination / "source.net.xml"
    candidate_net = destination / "candidate.net.xml"
    netconvert = str(selected_binaries["netconvert"])
    sumo = str(selected_binaries["sumo"])
    profile = contract["experiment_profile"]
    options = profile["netconvert"]
    relative_osm = relative_or_absolute_path(source_osm, destination)
    common_options = [
        "--osm-files",
        relative_osm,
        "--proj.utm",
        "--no-turnarounds",
        "--osm.all-attributes",
        "--tls.join",
        "--tls.join-dist",
        str(options["tls_join_distance_m"]),
        "--verbose",
    ]
    source_command = [
        netconvert,
        *common_options,
        "--output-file",
        source_net.name,
    ]
    candidate_command = [
        netconvert,
        "--osm-files",
        relative_osm,
        "--node-files",
        join_patch.name,
        *common_options[2:],
        "--output-file",
        candidate_net.name,
    ]
    source_result = run_command(
        source_command,
        cwd=destination,
        timeout_seconds=timeout_seconds,
    ).to_dict()
    candidate_result = run_command(
        candidate_command,
        cwd=destination,
        timeout_seconds=timeout_seconds,
    ).to_dict()
    build_report = {
        "schema": "torii.teacher-free-netconvert-build/v1",
        "status": (
            "pass"
            if _command_built_file(source_result, source_net)
            and _command_built_file(candidate_result, candidate_net)
            else "fail"
        ),
        "source": _build_record(source_result, source_net),
        "candidate": _build_record(candidate_result, candidate_net),
        "declared_candidate_delta": {
            "operation": "plainxml_join_patch_plus_netconvert_regeneration",
            "join_patch_file": str(join_patch),
            "join_patch_sha256": file_sha256(join_patch),
            "bound_candidate_dag_id": candidate_dag["candidate_dag_id"],
            "bound_candidate_id": plan["merge_experiment_candidate_id"],
            "selection_is_topology_truth_claim": False,
        },
        "source_command": source_command,
        "candidate_command": candidate_command,
        "source_osm_mutation": file_sha256(source_osm) != source_osm_sha256,
    }
    build_file = destination / "netconvert-build.json"
    write_json_atomic(build_file, build_report, sort_keys=True)
    if build_report["status"] != "pass" or build_report["source_osm_mutation"]:
        return _write_terminal_bundle(
            destination=destination,
            status="blocked",
            source_osm=source_osm,
            source_osm_sha256=source_osm_sha256,
            toolchain_lock=toolchain_lock,
            discovery=discovery,
            contract=contract,
            details={
                "terminal_stage": "materialization",
                "reason": "netconvert build or source immutability gate failed",
                "build_report_file": str(build_file),
            },
        )

    source_scope = tuple(map(str, plan["source_junction_ids"]))
    target_junction_id = str(plan["target_junction_id"])
    target_controller_id = str(plan["target_controller_id"])
    movement_metrics = plan["movement_metrics"]
    guard_source_ids = _derive_guard_junction_ids(source_net, source_scope)
    candidate_junction_ids = _junction_ids(candidate_net)
    guard_candidate_ids = tuple(
        item for item in guard_source_ids if item in candidate_junction_ids
    )
    scope_derivation = {
        "schema": "torii.teacher-free-scope-derivation/v1",
        "method": "source_net_external_edge_boundary_adjacency",
        "target_source_junction_ids": list(source_scope),
        "target_candidate_junction_ids": [target_junction_id],
        "guard_source_junction_ids": list(guard_source_ids),
        "guard_candidate_junction_ids": list(guard_candidate_ids),
        "manual_scope_input": False,
    }
    scope_file = destination / "scope-derivation.json"
    write_json_atomic(scope_file, scope_derivation, sort_keys=True)

    rollback = {
        "schema": "torii.teacher-free-materialization-rollback/v1",
        "status": "available",
        "source_osm": {
            "path": str(source_osm),
            "sha256": source_osm_sha256,
        },
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
        "contract_id": contract["contract_id"],
        "candidate_dag_id": candidate_dag["candidate_dag_id"],
        "materialized_candidate_id": plan["merge_experiment_candidate_id"],
        "forward_operation": {
            "type": "plainxml_join_patch_plus_netconvert_regeneration",
            "patch_path": str(join_patch),
            "patch_sha256": file_sha256(join_patch),
        },
        "inverse_operation": {
            "type": "rebuild_without_candidate_patch",
            "command": source_command,
            "source_mutation": False,
        },
        "automatic_promotion_gate": "blocked",
    }
    rollback_file = destination / "rollback.json"
    write_json_atomic(rollback_file, rollback, sort_keys=True)

    tls_ownership = audit_tls_ownership_rebuild(
        source_net=source_net,
        candidate_net=candidate_net,
        target_source_junction_ids=source_scope,
        target_candidate_junction_id=target_junction_id,
        expected_controller_ids=(target_controller_id,),
        expected_controlled_connection_count=int(movement_metrics["movement_count"]),
        report_schema="torii.teacher-free-tls-ownership/v1",
    )
    tls_ownership_file = destination / "tls-ownership.json"
    write_json_atomic(tls_ownership_file, tls_ownership, sort_keys=True)

    candidate_binding = bind_materialized_candidate_to_dag(
        candidate_net=candidate_net,
        target_junction_id=target_junction_id,
        expected_controller_ids=(target_controller_id,),
        physical_cell=hypothesis["physical_cell"],
        movement_hypotheses=hypothesis["vehicle_movement_hypotheses"],
        candidate_dag=candidate_dag,
        tls_ownership=tls_ownership,
    )
    candidate_binding["preregistered_candidate_id_matches"] = (
        candidate_binding.get("bound_candidate_id")
        == plan["merge_experiment_candidate_id"]
    )
    candidate_binding_file = destination / "candidate-dag-binding.json"
    write_json_atomic(candidate_binding_file, candidate_binding, sort_keys=True)

    exact = build_exact_semantic_regression_artifacts(
        source_net,
        candidate_net,
        output_dir=destination / "exact-audit",
        toolchain_lock_file=toolchain_lock,
        traffic_side=TrafficSide(traffic_side),
        target_source_junction_ids=source_scope,
        target_candidate_junction_ids=(target_junction_id,),
        guard_source_junction_ids=guard_source_ids,
        guard_candidate_junction_ids=guard_candidate_ids,
        endpoint_tolerance_m=float(profile["audit"]["endpoint_tolerance_m"]),
        normalized_lane_rank_tolerance=float(
            profile["audit"]["normalized_lane_rank_tolerance"]
        ),
        prefix="teacher-free-v3",
    )
    exact_diff = _read_json(Path(exact["files"]["exact_diff"]))
    candidate_safety = _read_json(Path(exact["files"]["candidate_safety"]))
    candidate_connection = _read_json(
        Path(exact["files"]["candidate_connection_audit"])
    )
    target_connection = _target_connection_summary(
        candidate_connection,
        target_junction_id=target_junction_id,
    )

    sumo_load = run_sumo_load_audit(
        net_file=candidate_net,
        output_dir=destination / "sumo-load",
        sumo_binary=sumo,
        timeout_seconds=timeout_seconds,
    )
    routeability = run_all_turn_movement_smoke(
        net_file=candidate_net,
        target_junction_id=target_junction_id,
        output_dir=destination / "all-movement-routeability",
        sumo_binary=sumo,
        expected_movement_count=int(movement_metrics["movement_count"]),
        expected_incoming_approach_count=int(
            movement_metrics["incoming_approach_count"]
        ),
        expected_outgoing_approach_count=int(
            movement_metrics["outgoing_approach_count"]
        ),
        expected_turn_counts={
            str(key): int(value)
            for key, value in movement_metrics["turn_counts"].items()
        },
        expected_controller_ids=(target_controller_id,),
        departure_interval_s=int(profile["runtime"]["departure_interval_s"]),
        end_time_s=int(profile["runtime"]["end_time_s"]),
        timeout_seconds=timeout_seconds,
    )

    tls_policy = {
        "schema": "torii.teacher-free-tls-policy/v1",
        "generated_controller_id": target_controller_id,
        "controller_semantics": "netconvert_generated_experimental_program",
        "custom_phase_topology_status": "blocked",
        "field_timing_reconstruction_status": "blocked",
        "automatic_topology_selection_status": "blocked",
        "automatic_promotion_gate": "blocked",
        "claim_boundary": (
            "Runtime and binding checks exercise the generated controller but "
            "do not certify NEMA topology, field timing, or real-world control intent."
        ),
    }
    tls_policy_file = destination / "tls-policy.json"
    write_json_atomic(tls_policy_file, tls_policy, sort_keys=True)

    outside_zero = not exact_diff.get("outside_scope_delta_ids") and not exact_diff.get(
        "outside_scope_added_finding_ids"
    )
    gates = {
        "pre_materialization_contract": "pass",
        "source_osm_immutable": (
            "pass" if file_sha256(source_osm) == source_osm_sha256 else "fail"
        ),
        "source_netconvert": build_report["source"]["status"],
        "candidate_netconvert": build_report["candidate"]["status"],
        "tls_ownership_rebuild": str(tls_ownership["status"]),
        "materialized_candidate_dag_binding": (
            "pass"
            if candidate_binding.get("binding_status") == "pass"
            and candidate_binding["preregistered_candidate_id_matches"]
            else "fail"
        ),
        "movement_semantics_posthoc_binding": (
            "pass"
            if candidate_binding.get("semantic_disposition") == "suggest"
            else "review"
        ),
        "target_connection_mode": str(target_connection["status"]),
        "independent_conflict_safety": str(candidate_safety["status"]),
        "outside_scope_exact_zero_delta": "pass" if outside_zero else "fail",
        "sumo_load": str(sumo_load["status"]),
        "all_movement_routeability": str(routeability["status"]),
    }
    machine_ready = all(status == "pass" for status in gates.values())
    status = "review_ready" if machine_ready else "blocked"
    overlay_file = destination / "review.add.xml"
    _write_review_overlay(
        overlay_file,
        candidate_net=candidate_net,
        target_junction_id=target_junction_id,
        guard_junction_ids=guard_candidate_ids,
        contract_id=str(contract["contract_id"]),
    )
    summary = {
        "schema": "torii.teacher-free-materialization-workflow/v3",
        "workflow_state": "REVIEW_PENDING" if machine_ready else "BLOCKED",
        "status": status,
        "automatic_promotion_gate": "blocked",
        "automatic_topology_selection": False,
        "field_timing_reconstruction": False,
        "source_mutation": file_sha256(source_osm) != source_osm_sha256,
        "discovery_id": discovery["discovery_id"],
        "contract_id": contract["contract_id"],
        "materialized_candidate_id": plan["merge_experiment_candidate_id"],
        "candidate_binding_id": candidate_binding["binding_id"],
        "target_junction_id": target_junction_id,
        "target_controller_id": target_controller_id,
        "source_osm": {
            "path": str(source_osm),
            "sha256": source_osm_sha256,
        },
        "source_net": rollback["source_net"],
        "candidate_net": rollback["candidate_net"],
        "movement_metrics": movement_metrics,
        "gates": gates,
        "target_connection": target_connection,
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
            "discovery": str(discovery_file),
            "materialization_contract": str(contract_file),
            "candidate_dag": str(candidate_dag_file),
            "join_patch": str(join_patch),
            "build_report": str(build_file),
            "scope_derivation": str(scope_file),
            "tls_ownership": str(tls_ownership_file),
            "candidate_binding": str(candidate_binding_file),
            "exact_audit_manifest": exact["files"]["manifest"],
            "rollback": str(rollback_file),
            "review_overlay": str(overlay_file),
            "tls_policy": str(tls_policy_file),
        },
        "claim_boundary": (
            "Review-ready means this preregistered experimental variant passed "
            "the listed machine gates. It does not prove the merge is the real "
            "topology and never opens automatic promotion."
        ),
    }
    summary_file = destination / "summary.json"
    write_json_atomic(summary_file, summary, sort_keys=True)
    review_file = destination / "review.html"
    write_text_atomic(review_file, _review_html(summary))
    manifest_file = destination / "manifest.json"
    _write_manifest(
        manifest_file,
        destination=destination,
        status=status,
        source_osm=source_osm,
        source_osm_sha256=source_osm_sha256,
        toolchain_lock=toolchain_lock,
        contract=contract,
        gates=gates,
    )
    return {
        **summary,
        "summary_file": str(summary_file),
        "review_file": str(review_file),
        "manifest_file": str(manifest_file),
    }


def _write_terminal_bundle(
    *,
    destination: Path,
    status: str,
    source_osm: Path,
    source_osm_sha256: str,
    toolchain_lock: Path,
    discovery: Mapping[str, Any],
    contract: Mapping[str, Any],
    details: Mapping[str, Any],
) -> dict[str, Any]:
    summary = {
        "schema": "torii.teacher-free-materialization-workflow/v3",
        "workflow_state": "BLOCKED" if status == "blocked" else "HYPOTHESES_READY",
        "status": status,
        "automatic_promotion_gate": "blocked",
        "automatic_topology_selection": False,
        "field_timing_reconstruction": False,
        "source_mutation": file_sha256(source_osm) != source_osm_sha256,
        "discovery_id": discovery["discovery_id"],
        "contract_id": contract["contract_id"],
        "candidate_written": False,
        "details": dict(details),
        "materialization_blockers": contract["materialization_blockers"],
        "claim_boundary": (
            "No candidate network was written. This is a successful fail-closed "
            "policy outcome only when the stated pre-materialization evidence is unresolved."
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
        toolchain_lock=toolchain_lock,
        contract=contract,
        gates={"pre_materialization_contract": str(contract["status"])},
    )
    return {
        **summary,
        "summary_file": str(summary_file),
        "manifest_file": str(manifest_file),
    }


def _write_manifest(
    path: Path,
    *,
    destination: Path,
    status: str,
    source_osm: Path,
    source_osm_sha256: str,
    toolchain_lock: Path,
    contract: Mapping[str, Any],
    gates: Mapping[str, str],
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
            "schema": "torii.teacher-free-materialization-manifest/v1",
            "status": status,
            "automatic_promotion_gate": "blocked",
            "automatic_topology_selection": False,
            "field_timing_reconstruction": False,
            "source_mutation": file_sha256(source_osm) != source_osm_sha256,
            "contract_id": contract["contract_id"],
            "inputs": [
                {
                    "role": "frozen_osm_bbox",
                    "path": str(source_osm),
                    "sha256": source_osm_sha256,
                },
                {
                    "role": "toolchain_lock",
                    "path": str(toolchain_lock),
                    "sha256": file_sha256(toolchain_lock),
                },
            ],
            "gates": dict(gates),
            "artifacts": artifacts,
        },
        sort_keys=True,
    )


def _candidate_by_id(
    discovery: Mapping[str, Any],
    candidate_id: str,
) -> Mapping[str, Any]:
    matches = [
        item
        for item in discovery.get("candidates", ())
        if item.get("candidate_id") == candidate_id
    ]
    if len(matches) != 1:
        raise ValueError("Materialization contract does not bind one discovered candidate.")
    return matches[0]


def _command_built_file(result: Mapping[str, Any], path: Path) -> bool:
    return (
        result.get("status") == "pass"
        and result.get("returncode") == 0
        and path.is_file()
        and path.stat().st_size > 0
    )


def _build_record(result: Mapping[str, Any], path: Path) -> dict[str, Any]:
    passed = _command_built_file(result, path)
    return {
        "status": "pass" if passed else "fail",
        "command_result": dict(result),
        "path": str(path),
        "sha256": file_sha256(path) if path.is_file() else None,
        "normalized_sha256": normalized_net_sha256(path) if passed else None,
    }


def _junction_ids(net_file: Path) -> set[str]:
    return {
        str(item.attrib["id"])
        for item in ET.parse(net_file).getroot().findall("junction")
        if item.attrib.get("id")
    }


def _derive_guard_junction_ids(
    net_file: Path,
    target_junction_ids: tuple[str, ...],
) -> tuple[str, ...]:
    target = set(target_junction_ids)
    guards: set[str] = set()
    for edge in ET.parse(net_file).getroot().findall("edge"):
        if edge.attrib.get("function") == "internal" or str(
            edge.attrib.get("id", "")
        ).startswith(":"):
            continue
        source = str(edge.attrib.get("from", ""))
        destination = str(edge.attrib.get("to", ""))
        if source in target and destination and destination not in target:
            guards.add(destination)
        if destination in target and source and source not in target:
            guards.add(source)
    return tuple(sorted(guards))


def _target_connection_summary(
    report: Mapping[str, Any],
    *,
    target_junction_id: str,
) -> dict[str, Any]:
    record = next(
        (
            item
            for item in report.get("junctions", ())
            if item.get("junction_id") == target_junction_id
        ),
        None,
    )
    if record is None:
        return {"status": "fail", "reason": "target junction audit missing"}
    audit = record["connection_mode_audit"]
    return {
        "status": str(audit["status"]),
        "direct_movement_count": audit["direct_movement_count"],
        "verified_internal_path_count": audit["verified_internal_path_count"],
        "structural_failure_count": len(audit["structural_failures"]),
        "review_finding_count": len(audit["review_findings"]),
        "request_foes_status": audit["request_foe_audit"]["status"],
        "tls_binding_status": record["tls_link_binding_audit"]["status"],
        "incoming_motorized_lane_count": audit["connection_completeness_audit"][
            "incoming_motorized_lane_count"
        ],
    }


def _write_review_overlay(
    path: Path,
    *,
    candidate_net: Path,
    target_junction_id: str,
    guard_junction_ids: tuple[str, ...],
    contract_id: str,
) -> None:
    junctions = {
        str(item.attrib.get("id", "")): item
        for item in ET.parse(candidate_net).getroot().findall("junction")
    }
    root = ET.Element("additional")
    target = junctions.get(target_junction_id)
    if target is not None:
        polygon = ET.SubElement(
            root,
            "poly",
            id="teacher_free_v3_target_cell",
            color="0,210,120,120",
            fill="true",
            layer="100",
            shape=target.attrib.get("shape", ""),
        )
        ET.SubElement(polygon, "param", key="contract_id", value=contract_id)
        ET.SubElement(
            polygon,
            "param",
            key="automatic_promotion_gate",
            value="blocked",
        )
    for index, junction_id in enumerate(guard_junction_ids, start=1):
        junction = junctions.get(junction_id)
        if junction is None:
            continue
        poi = ET.SubElement(
            root,
            "poi",
            id=f"teacher_free_v3_guard_{index}",
            color="255,180,0,255",
            layer="101",
            x=junction.attrib.get("x", "0"),
            y=junction.attrib.get("y", "0"),
        )
        ET.SubElement(poi, "param", key="junction_id", value=junction_id)
    ET.indent(root, space="  ")
    xml = ET.tostring(root, encoding="unicode")
    write_text_atomic(
        path,
        f"<?xml version='1.0' encoding='utf-8'?>\n{xml}\n",
    )


def _review_html(summary: Mapping[str, Any]) -> str:
    gate_rows = "\n".join(
        "<tr>"
        f"<td>{html.escape(str(name))}</td>"
        f"<td>{html.escape(str(status))}</td>"
        "</tr>"
        for name, status in summary["gates"].items()
    )
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<title>Torii teacher-free materialization v3</title>
<style>body{{font-family:system-ui,sans-serif;margin:2rem;max-width:70rem}}table{{border-collapse:collapse;width:100%}}th,td{{border:1px solid #ccc;padding:.45rem;text-align:left}}code{{font-size:.85em}}</style>
</head><body>
<h1>Teacher-free experimental materialization v3</h1>
<p>Status: <strong>{html.escape(str(summary["status"]))}</strong>. Automatic promotion, automatic topology selection, and field timing remain blocked.</p>
<p>Contract <code>{html.escape(str(summary["contract_id"]))}</code> materialized DAG candidate <code>{html.escape(str(summary["materialized_candidate_id"]))}</code>.</p>
<table><thead><tr><th>Machine gate</th><th>Status</th></tr></thead><tbody>{gate_rows}</tbody></table>
<p>{html.escape(str(summary["claim_boundary"]))}</p>
</body></html>"""


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _reset_owned_directory(destination: Path) -> None:
    owner = destination / "teacher-free-materialization.owner.json"
    if destination.exists() and any(destination.iterdir()):
        if not owner.is_file():
            raise ValueError(
                "Refusing to clear a non-empty materialization directory without Torii ownership metadata."
            )
        payload = json.loads(owner.read_text(encoding="utf-8"))
        if payload.get("schema") != _OWNER_SCHEMA:
            raise ValueError("Teacher-free materialization ownership metadata is invalid.")
        if payload.get("owned_root") != str(destination):
            raise ValueError(
                "Teacher-free materialization ownership root does not match the output directory."
            )
        shutil.rmtree(destination)
    destination.mkdir(parents=True, exist_ok=True)
    write_json_atomic(
        owner,
        {
            "schema": _OWNER_SCHEMA,
            "purpose": "generated teacher-free experimental materialization artifacts",
            "owned_root": str(destination),
        },
        sort_keys=True,
    )
