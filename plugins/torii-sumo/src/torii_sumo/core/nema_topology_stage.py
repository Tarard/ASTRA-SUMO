from __future__ import annotations

import hashlib
import json
import shutil
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from torii_sumo.corridor.audit_pipeline import (
    build_exact_semantic_regression_artifacts,
)
from torii_sumo.corridor.enums import TrafficSide
from torii_sumo.corridor.netxml import normalized_net_sha256
from torii_sumo.intersection.candidate_binding import (
    bind_materialized_candidate_to_dag,
)

from .artifact_io import write_json_atomic
from .candidate_contracts import file_sha256
from .movement_routeability import run_all_turn_movement_smoke
from .standard_nema_binding import build_standard_nema_phase_binding
from .tls_ownership import audit_tls_ownership_rebuild


_STAGE_OWNER_SCHEMA = "torii.nema-topology-stage-owner/v1"


def clear_owned_nema_topology_stage(output_dir: Path) -> None:
    """Remove only a stage directory carrying Torii ownership metadata."""

    destination = output_dir.resolve()
    if not destination.exists():
        return
    owner = destination / "stage-owner.json"
    if not owner.is_file():
        raise ValueError("Refusing to remove a NEMA stage directory without Torii ownership metadata.")
    payload = _read_json(owner)
    if payload.get("schema") != _STAGE_OWNER_SCHEMA:
        raise ValueError("NEMA stage ownership metadata is invalid.")
    shutil.rmtree(destination)


def run_evidence_gated_nema_topology_stage(
    *,
    source_net: Path,
    materialized_candidate_net: Path,
    output_dir: Path,
    target_source_junction_ids: Sequence[str],
    target_candidate_junction_id: str,
    guard_junction_ids: Sequence[str],
    expected_controller_ids: Sequence[str],
    expected_movement_count: int,
    expected_incoming_approach_count: int,
    expected_outgoing_approach_count: int,
    expected_turn_counts: Mapping[str, int],
    traffic_side: str,
    endpoint_tolerance_m: float,
    normalized_lane_rank_tolerance: float,
    departure_interval_s: int,
    end_time_s: int,
    physical_cell: Mapping[str, Any],
    movement_hypotheses: Mapping[str, Any],
    candidate_dag: Mapping[str, Any],
    primary_candidate_binding: Mapping[str, Any],
    primary_tls_ownership: Mapping[str, Any],
    primary_target_connection: Mapping[str, Any],
    primary_independent_safety: Mapping[str, Any],
    primary_routeability: Mapping[str, Any],
    toolchain_lock_file: Path,
    netconvert_binary: str,
    sumo_binary: str,
    timeout_seconds: float = 120.0,
) -> dict[str, Any]:
    """Build one review-only NEMA topology after independent evidence closes.

    The stage deliberately sits after physical-cell, movement, connection,
    conflict, and runtime validation.  A precondition failure is a successful
    fail-closed abstention.  A generated NEMA network is independently audited
    again and never replaces the primary materialized candidate.
    """

    source = source_net.resolve(strict=True)
    materialized = materialized_candidate_net.resolve(strict=True)
    destination = output_dir.resolve()
    _reset_stage_directory(destination)
    source_normalized_sha256 = normalized_net_sha256(source)
    materialized_normalized_sha256 = normalized_net_sha256(materialized)
    stage_identity = {
        "source_normalized_sha256": source_normalized_sha256,
        "materialized_normalized_sha256": materialized_normalized_sha256,
        "physical_cell_hypothesis_id": physical_cell.get("hypothesis_id"),
        "movement_hypothesis_set_id": movement_hypotheses.get("hypothesis_set_id"),
        "candidate_dag_id": candidate_dag.get("candidate_dag_id"),
        "primary_candidate_binding_id": primary_candidate_binding.get("binding_id"),
        "target_candidate_junction_id": target_candidate_junction_id,
        "expected_controller_ids": sorted(map(str, expected_controller_ids)),
        "expected_movement_count": expected_movement_count,
        "traffic_side": traffic_side,
    }

    preconditions = _preconditions(
        target_candidate_junction_id=target_candidate_junction_id,
        expected_controller_ids=expected_controller_ids,
        expected_movement_count=expected_movement_count,
        expected_incoming_approach_count=(expected_incoming_approach_count),
        expected_outgoing_approach_count=(expected_outgoing_approach_count),
        traffic_side=traffic_side,
        physical_cell=physical_cell,
        movement_hypotheses=movement_hypotheses,
        primary_candidate_binding=primary_candidate_binding,
        primary_tls_ownership=primary_tls_ownership,
        primary_target_connection=primary_target_connection,
        primary_independent_safety=primary_independent_safety,
        primary_routeability=primary_routeability,
    )
    blockers = [name for name, evidence in preconditions.items() if evidence["status"] != "pass"]
    report: dict[str, Any] = {
        "schema": "torii.evidence-gated-nema-topology/v1",
        "stage_id": (f"nema-topology-{_stable_digest(stage_identity)[:20]}"),
        "stage_identity_basis": stage_identity,
        "status": "evaluating",
        "policy_gate": "pass",
        "automatic_promotion_gate": "blocked",
        "human_review_status": "required",
        "simulation_intent": "canonical_simulation_plan",
        "field_timing_claim": False,
        "timing_policy": ("generic_NEMA_parameters_are_executable_placeholders_not_observed_field_timing"),
        "source_network": {
            "path": str(source),
            "sha256": file_sha256(source),
            "normalized_sha256": source_normalized_sha256,
        },
        "materialized_candidate": {
            "path": str(materialized),
            "sha256": file_sha256(materialized),
            "normalized_sha256": materialized_normalized_sha256,
        },
        "target_candidate_junction_id": target_candidate_junction_id,
        "preconditions": preconditions,
        "precondition_blockers": blockers,
        "claim_boundary": (
            "A ready result proposes only a classic NEMA movement-to-phase "
            "topology for an already verified vehicle-only three/four-way "
            "candidate. It is not field timing, human validation, or "
            "automatic promotion."
        ),
    }
    if blockers:
        report.update(
            {
                "status": "abstained",
                "nema_topology_status": "not_proposed",
                "candidate_net_file": "",
                "candidate_sha256": "",
                "abstention_reason": ("upstream movement or safety evidence is not closed"),
            }
        )
        return report

    standard_dir = destination / "standard"
    standard = build_standard_nema_phase_binding(
        materialized,
        output_dir=standard_dir,
        prefix="nema-topology",
        junction_id=target_candidate_junction_id,
        run_runtime_checks=True,
        run_routeability=False,
        routeability_vehicle_count=expected_movement_count,
        netconvert_binary=netconvert_binary,
        sumo_binary=sumo_binary,
        timeout_seconds=timeout_seconds,
    )
    report["standard_builder"] = _standard_builder_summary(standard)
    standard_ready = (
        standard.get("status") == "pass"
        and standard.get("nema_binding_status") == "candidate_ready_for_review"
        and standard.get("promotion_status") == "review_required"
        and standard.get("source_preservation_status") == "pass"
        and standard.get("candidate_identity_status") == "distinct"
        and (standard.get("candidate_validation") or {}).get("status") == "pass"
        and (standard.get("runtime_validation") or {}).get("status") == "pass"
    )
    candidate_value = str(standard.get("candidate_net_file", ""))
    if not standard_ready or not candidate_value:
        report.update(
            {
                "status": "blocked",
                "policy_gate": "fail",
                "nema_topology_status": "builder_validation_failed",
                "candidate_net_file": candidate_value,
                "candidate_sha256": str(standard.get("candidate_sha256", "")),
            }
        )
        return report

    nema_candidate = Path(candidate_value).resolve(strict=True)
    nema_tls_ownership = audit_tls_ownership_rebuild(
        source_net=source,
        candidate_net=nema_candidate,
        target_source_junction_ids=tuple(target_source_junction_ids),
        target_candidate_junction_id=target_candidate_junction_id,
        expected_controller_ids=tuple(expected_controller_ids),
        expected_controlled_connection_count=expected_movement_count,
        report_schema="torii.nema-topology-tls-ownership/v1",
    )
    nema_tls_file = destination / "nema-tls-ownership.json"
    write_json_atomic(nema_tls_file, nema_tls_ownership, sort_keys=True)

    nema_binding = bind_materialized_candidate_to_dag(
        candidate_net=nema_candidate,
        target_junction_id=target_candidate_junction_id,
        expected_controller_ids=tuple(expected_controller_ids),
        physical_cell=physical_cell,
        movement_hypotheses=movement_hypotheses,
        candidate_dag=candidate_dag,
        tls_ownership=nema_tls_ownership,
    )
    nema_binding_file = destination / "nema-candidate-dag-binding.json"
    write_json_atomic(nema_binding_file, nema_binding, sort_keys=True)

    exact = build_exact_semantic_regression_artifacts(
        materialized,
        nema_candidate,
        output_dir=destination / "exact-audit",
        toolchain_lock_file=toolchain_lock_file,
        traffic_side=TrafficSide(traffic_side),
        target_source_junction_ids=(target_candidate_junction_id,),
        target_candidate_junction_ids=(target_candidate_junction_id,),
        guard_source_junction_ids=tuple(guard_junction_ids),
        guard_candidate_junction_ids=tuple(guard_junction_ids),
        endpoint_tolerance_m=endpoint_tolerance_m,
        normalized_lane_rank_tolerance=(normalized_lane_rank_tolerance),
        prefix="nema-topology",
    )
    exact_diff = _read_json(Path(exact["files"]["exact_diff"]))
    safety = _read_json(Path(exact["files"]["candidate_safety"]))
    connection = _read_json(Path(exact["files"]["candidate_connection_audit"]))
    target_connection = _target_connection_summary(
        connection,
        target_junction_id=target_candidate_junction_id,
    )

    all_turns = run_all_turn_movement_smoke(
        net_file=nema_candidate,
        target_junction_id=target_candidate_junction_id,
        output_dir=destination / "all-turn-smoke",
        sumo_binary=sumo_binary,
        expected_movement_count=expected_movement_count,
        expected_incoming_approach_count=(expected_incoming_approach_count),
        expected_outgoing_approach_count=(expected_outgoing_approach_count),
        expected_turn_counts={str(key): int(value) for key, value in expected_turn_counts.items()},
        expected_controller_ids=tuple(expected_controller_ids),
        departure_interval_s=departure_interval_s,
        end_time_s=end_time_s,
        timeout_seconds=timeout_seconds,
    )

    validation_gates = {
        "standard_builder": "pass" if standard_ready else "fail",
        "tls_owner_closure": str(nema_tls_ownership["status"]),
        "movement_dag_binding": str(nema_binding["binding_status"]),
        "movement_binding_preserved": (
            "pass"
            if nema_binding.get("bound_candidate_id") == primary_candidate_binding.get("bound_candidate_id")
            else "fail"
        ),
        "connection_mode": str(target_connection["status"]),
        "independent_conflict_closure": str(safety["status"]),
        "outside_scope_zero_delta": (
            "pass"
            if not exact_diff["outside_scope_delta_ids"] and not exact_diff["outside_scope_added_finding_ids"]
            else "fail"
        ),
        "all_turn_routeability": str(all_turns["status"]),
    }
    validation_pass = all(value == "pass" for value in validation_gates.values())
    selected = standard.get("selected_candidate") or {}
    report.update(
        {
            "status": ("candidate_ready_for_review" if validation_pass else "blocked"),
            "policy_gate": "pass" if validation_pass else "fail",
            "nema_topology_status": ("review_candidate" if validation_pass else "independent_validation_failed"),
            "candidate_net_file": str(nema_candidate),
            "candidate_sha256": file_sha256(nema_candidate),
            "candidate_normalized_sha256": normalized_net_sha256(nema_candidate),
            "validation_gates": validation_gates,
            "topology": {
                "layout_type": selected.get("layout_type"),
                "arm_count": selected.get("arm_count"),
                "movement_count": selected.get("direct_vehicle_movement_count"),
                "used_nema_phases": selected.get("used_nema_phases", []),
                "phase_by_arm": selected.get("phase_by_arm", {}),
                "main_pair": selected.get("main_pair", []),
                "minor_pair": selected.get("minor_pair", []),
                "stem_arm": selected.get("stem_arm", ""),
            },
            "nema_candidate_dag_binding": {
                "status": nema_binding["binding_status"],
                "binding_id": nema_binding["binding_id"],
                "bound_candidate_id": nema_binding["bound_candidate_id"],
                "exact_movement_variant_ids": nema_binding["exact_movement_variant_ids"],
                "artifact_file": str(nema_binding_file),
            },
            "tls_ownership": {
                "status": nema_tls_ownership["status"],
                "controller_ids": nema_tls_ownership["candidate"]["target_controller_ids"],
                "controlled_connection_count": nema_tls_ownership["candidate"]["target_controlled_connection_count"],
                "signal_group_count": nema_tls_ownership["candidate"]["target_signal_group_count"],
                "artifact_file": str(nema_tls_file),
            },
            "exact_diff": {
                "status": exact_diff["status"],
                "outside_scope_delta_count": len(exact_diff["outside_scope_delta_ids"]),
                "outside_scope_added_finding_count": len(exact_diff["outside_scope_added_finding_ids"]),
                "artifact_file": exact["files"]["exact_diff"],
            },
            "connection_mode": target_connection,
            "independent_safety": {
                "status": safety["status"],
                "finding_count": len(safety["findings"]),
                "protected_conflict_count": safety["protected_conflict_count"],
                "permissive_without_yield_count": safety["permissive_without_yield_count"],
                "potential_signal_conflict_count": safety["potential_signal_conflict_count"],
                "artifact_file": exact["files"]["candidate_safety"],
            },
            "all_turn_routeability": {
                "status": all_turns["status"],
                "movement_count": all_turns["movement_count"],
                "turn_counts": all_turns["turn_counts"],
                "arrived_vehicle_count": len(all_turns["arrived_vehicle_ids"]),
                "report_file": all_turns["report_file"],
            },
            "rollback": {
                "strategy": ("discard the NEMA candidate and retain the immutable materialized candidate"),
                "materialized_candidate_sha256": file_sha256(materialized),
                "standard_plan_file": standard.get("plan_file", ""),
            },
        }
    )
    return report


def _preconditions(
    *,
    target_candidate_junction_id: str,
    expected_controller_ids: Sequence[str],
    expected_movement_count: int,
    expected_incoming_approach_count: int,
    expected_outgoing_approach_count: int,
    traffic_side: str,
    physical_cell: Mapping[str, Any],
    movement_hypotheses: Mapping[str, Any],
    primary_candidate_binding: Mapping[str, Any],
    primary_tls_ownership: Mapping[str, Any],
    primary_target_connection: Mapping[str, Any],
    primary_independent_safety: Mapping[str, Any],
    primary_routeability: Mapping[str, Any],
) -> dict[str, dict[str, Any]]:
    approaches = list(physical_cell.get("physical_approaches", ()))
    physical_cell_risks = list(physical_cell.get("risks", ()))
    comparison = movement_hypotheses.get("variant_comparison") or {}
    consensus_id = str(movement_hypotheses.get("consensus_variant_id") or "")
    exact_variants = set(
        map(
            str,
            primary_candidate_binding.get("exact_movement_variant_ids", ()),
        )
    )
    nested_restrictions = list(movement_hypotheses.get("nested_restriction_ids", ()))
    unresolved = list(movement_hypotheses.get("unresolved_reasons", ()))
    tls_candidate = primary_tls_ownership.get("candidate") or {}
    routeability_complete = (
        primary_routeability.get("status") == "pass"
        and int(primary_routeability.get("movement_count", -1)) == expected_movement_count
        and len(primary_routeability.get("arrived_vehicle_ids", ())) == expected_movement_count
    )
    safety_counts = {
        key: int(primary_independent_safety.get(key, -1))
        for key in (
            "protected_conflict_count",
            "permissive_without_yield_count",
            "potential_signal_conflict_count",
        )
    }
    expected_approaches = (
        expected_incoming_approach_count == expected_outgoing_approach_count == len(approaches)
        and len(approaches) in {3, 4}
        and physical_cell.get("generation_status") == "pass"
        and not physical_cell_risks
        and all(approach.get("grouping_status") == "pass" for approach in approaches)
    )
    return {
        "explicit_right_hand_traffic": {
            "status": "pass" if traffic_side == "right" else "blocked",
            "observed": traffic_side,
            "required": "right",
        },
        "standard_physical_cell": {
            "status": "pass" if expected_approaches else "blocked",
            "physical_approach_count": len(approaches),
            "physical_cell_generation_status": physical_cell.get("generation_status"),
            "physical_cell_risks": physical_cell_risks,
            "expected_incoming_approach_count": (expected_incoming_approach_count),
            "expected_outgoing_approach_count": (expected_outgoing_approach_count),
        },
        "movement_hypothesis_consensus": {
            "status": (
                "pass"
                if comparison.get("status") == "exact" and consensus_id and not unresolved and not nested_restrictions
                else "blocked"
            ),
            "comparison_status": comparison.get("status"),
            "consensus_variant_id": consensus_id,
            "unresolved_reasons": unresolved,
            "nested_restriction_ids": nested_restrictions,
        },
        "materialized_movement_binding": {
            "status": (
                "pass"
                if primary_candidate_binding.get("binding_status") == "pass"
                and primary_candidate_binding.get("semantic_disposition") == "suggest"
                and consensus_id in exact_variants
                and int(primary_candidate_binding.get("target_connection_count", -1)) == expected_movement_count
                else "blocked"
            ),
            "binding_status": primary_candidate_binding.get("binding_status"),
            "semantic_disposition": primary_candidate_binding.get("semantic_disposition"),
            "exact_movement_variant_ids": sorted(exact_variants),
            "target_connection_count": primary_candidate_binding.get("target_connection_count"),
        },
        "controller_owner_closure": {
            "status": (
                "pass"
                if primary_tls_ownership.get("status") == "pass"
                and primary_tls_ownership.get("target_candidate_junction_id") == target_candidate_junction_id
                and set(
                    map(
                        str,
                        tls_candidate.get("target_controller_ids", ()),
                    )
                )
                == set(map(str, expected_controller_ids))
                and int(tls_candidate.get("target_tls_junction_count", -1)) == 1
                else "blocked"
            ),
            "tls_ownership_status": primary_tls_ownership.get("status"),
            "target_controller_ids": tls_candidate.get("target_controller_ids", []),
            "target_tls_junction_count": tls_candidate.get("target_tls_junction_count"),
        },
        "connection_mode_closure": {
            "status": (
                "pass"
                if primary_target_connection.get("status") == "pass"
                and int(primary_target_connection.get("direct_movement_count", -1)) == expected_movement_count
                and int(primary_target_connection.get("structural_failure_count", -1)) == 0
                and int(primary_target_connection.get("review_finding_count", -1)) == 0
                else "blocked"
            ),
            "evidence": dict(primary_target_connection),
        },
        "independent_conflict_closure": {
            "status": (
                "pass"
                if primary_independent_safety.get("status") == "pass"
                and all(value == 0 for value in safety_counts.values())
                else "blocked"
            ),
            **safety_counts,
        },
        "all_turn_runtime_closure": {
            "status": "pass" if routeability_complete else "blocked",
            "movement_count": primary_routeability.get("movement_count"),
            "arrived_vehicle_count": len(primary_routeability.get("arrived_vehicle_ids", ())),
            "expected_movement_count": expected_movement_count,
        },
    }


def _standard_builder_summary(report: Mapping[str, Any]) -> dict[str, Any]:
    selected = report.get("selected_candidate") or {}
    runtime = report.get("runtime_validation") or {}
    return {
        "status": report.get("status"),
        "nema_binding_status": report.get("nema_binding_status"),
        "promotion_status": report.get("promotion_status"),
        "source_preservation_status": report.get("source_preservation_status"),
        "candidate_identity_status": report.get("candidate_identity_status"),
        "candidate_validation_status": (report.get("candidate_validation") or {}).get("status"),
        "runtime_status": runtime.get("status"),
        "netconvert_status": runtime.get("netconvert_status"),
        "netconvert_semantic_status": runtime.get("netconvert_semantic_status"),
        "sumo_load_status": runtime.get("sumo_load_status"),
        "layout_type": selected.get("layout_type"),
        "arm_count": selected.get("arm_count"),
        "movement_count": selected.get("direct_vehicle_movement_count"),
        "report_file": report.get("report_file"),
        "plan_file": report.get("plan_file"),
        "manifest_file": report.get("manifest_file"),
        "review_html_file": report.get("review_html_file"),
        "review_overlay_file": report.get("review_overlay_file"),
    }


def _target_connection_summary(
    report: Mapping[str, Any],
    *,
    target_junction_id: str,
) -> dict[str, Any]:
    record = next(
        (item for item in report.get("junctions", ()) if item.get("junction_id") == target_junction_id),
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
    }


def _reset_stage_directory(destination: Path) -> None:
    owner = destination / "stage-owner.json"
    if destination.exists() and any(destination.iterdir()):
        clear_owned_nema_topology_stage(destination)
    destination.mkdir(parents=True, exist_ok=True)
    write_json_atomic(
        owner,
        {
            "schema": _STAGE_OWNER_SCHEMA,
            "purpose": "generated evidence-gated NEMA topology artifacts",
        },
        sort_keys=True,
    )


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _stable_digest(payload: Any) -> str:
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()
