"""Resumable stage ledger for the Hamburg named-corridor digital twin.

The domain stages already live in focused modules.  This module is deliberately
small: it does not rebuild a network or reinterpret official data.  It records
which stage artifacts are present, verifies their bytes, derives dependency
readiness, and invalidates downstream stages when an upstream manifest changes.
That makes the Codex loop (plan -> run -> audit -> revise -> rerun) executable
instead of relying on conversational memory.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from .artifact_io import write_json_atomic
from .candidate_contracts import file_sha256


HAMBURG_EXECUTION_WORKFLOW_SCHEMA = "torii.hamburg-sandtorkai-execution-workflow/v2"
HAMBURG_EXECUTION_CONFIG_SCHEMA = "torii.hamburg-digital-twin-workflow-config/v2"
HAMBURG_EXECUTION_WORKFLOW_ID = "hamburg_sandtorkai_2349_2394_2403"

STAGE_DEPENDENCIES: Mapping[str, tuple[str, ...]] = {
    "W0": (),
    "W1": ("W0",),
    "W3a": ("W0",),
    "W2": ("W1",),
    "W3b": ("W1", "W3a"),
    # Route incidence and demand are still produced inside the existing W4
    # replay implementation. Split them only when they have real producers.
    "W4": ("W1", "W2", "W3a", "W3b"),
}

STAGE_NAMES: Mapping[str, str] = {
    "W0": "scope_and_evidence",
    "W1": "official_road_reconstruction",
    "W3a": "count_acquisition",
    "W2": "official_signal_binding",
    "W3b": "detector_binding",
    "W4": "route_demand_and_sumo_replay",
    "W5": "workflow_summary",
}

STAGE_SCHEMAS: Mapping[str, frozenset[str]] = {
    "W0": frozenset({"torii.hamburg-named-corridor-scope/v1"}),
    "W1": frozenset({"torii.hamburg-official-corridor-geometry/v1"}),
    "W3a": frozenset({"torii.hamburg-named-corridor-count-scope/v1"}),
    "W2": frozenset({"torii.hamburg-named-signal-binding/v1"}),
    "W3b": frozenset({"torii.hamburg-named-detector-binding/v1"}),
    "W4": frozenset({"torii.hamburg-named-replay/v2"}),
}

NETWORK_BOUND_STAGES = frozenset({"W1", "W2", "W3b", "W4"})
STAGE_ORDER = (*STAGE_DEPENDENCIES, "W5")
_STAGE_INDEX = {stage_id: index for index, stage_id in enumerate(STAGE_ORDER)}
_STAGE_ID_BY_CASEFOLD = {stage_id.casefold(): stage_id for stage_id in STAGE_ORDER}

# This is the small, stable design contract for the Codex loop.  It tells a
# resumed run where to look first and what a stage must verify before adding
# new code.  The actual artifacts remain caller-supplied and hash-bound.
STAGE_CONTRACTS: Mapping[str, Mapping[str, tuple[str, ...]]] = {
    "W0": {
        "code_surfaces": ("core/hamburg_named_scope.py", "core/hamburg_official.py"),
        "entrypoints": ("scripts/freeze_hamburg_named_scope.py",),
        "verification": ("official identity", "road-arm scope", "source hashes"),
    },
    "W1": {
        "code_surfaces": (
            "core/hamburg_official_corridor_geometry.py",
            "core/road_network/official_plainxml.py",
            "core/road_network/official_lane_stitch.py",
            "core/road_network/official_splice_plan.py",
            "core/connection_mode_audit.py",
        ),
        "entrypoints": ("scripts/build_hamburg_official_corridor_geometry.py",),
        "verification": (
            "official MAP/HH-SIB lane-axis match",
            "official merge-point splice plan",
            "netconvert",
            "SUMO load",
            "lane-surface overlap",
            "connection graph",
        ),
    },
    "W2": {
        "code_surfaces": (
            "core/hamburg_named_signal_binding.py",
            "core/hamburg_named_signal_observations.py",
            "core/ocit_c.py",
        ),
        "entrypoints": (
            "scripts/build_hamburg_named_signal_binding.py",
            "scripts/build_hamburg_named_signal_observations.py",
        ),
        "verification": ("MAP/OCIT identity", "TLS link coverage", "complete t=0/history"),
    },
    "W3a": {
        "code_surfaces": ("core/hamburg_named_count_scope.py",),
        "entrypoints": ("scripts/build_hamburg_named_counts.py",),
        "verification": ("complete official bins", "explicit time window", "source hashes"),
    },
    "W3b": {
        "code_surfaces": (
            "core/hamburg_named_detector_bindings.py",
            "core/digital_twin_mapping.py",
        ),
        "entrypoints": ("scripts/build_hamburg_named_detector_bindings.py",),
        "verification": (
            "W1 network SHA",
            "W3a count-stream SHA",
            "detector-mapping artifact SHA",
            "explicit CRS",
            "unique lane binding",
            "same-location E1/E2",
        ),
    },
    "W4": {
        "code_surfaces": (
            "core/hamburg_named_replay.py",
            "core/route_sampler.py",
            "core/route_sensor_matrix.py",
        ),
        "entrypoints": ("scripts/build_hamburg_named_replay.py",),
        "verification": (
            "W1 network SHA",
            "selected W2, W3a, and W3b manifest identities",
            "W3a simulation-count SHA",
            "approved detector aggregation semantics",
            "signal-event artifact SHA",
            "route incidence and demand",
            "routeability",
            "zero teleports",
            "zero collisions",
            "formal comparison",
        ),
    },
    "W5": {
        "code_surfaces": ("core/hamburg_execution_workflow.py", "server.py"),
        "entrypoints": ("scripts/run_hamburg_execution_plan.py",),
        "verification": ("derived capability matrix", "hash-bound rerun", "downstream invalidation"),
    },
}


class HamburgExecutionWorkflowError(ValueError):
    """Raised when a workflow plan cannot be made hash-safe."""


def _canonical_stage_id(value: object) -> str:
    raw = str(value).strip()
    if raw.casefold() == "w3":
        raise HamburgExecutionWorkflowError(
            "legacy stage W3 was split; provide W3a count acquisition and W3b detector binding manifests"
        )
    stage_id = _STAGE_ID_BY_CASEFOLD.get(raw.casefold())
    if stage_id is None:
        raise HamburgExecutionWorkflowError(f"unknown workflow stage {raw!r}")
    if stage_id == "W5":
        raise HamburgExecutionWorkflowError("W5 is generated automatically and does not accept a manifest")
    return stage_id


def materialize_hamburg_execution_plan_from_config(
    config_file: Path,
    *,
    output_dir: Path | None = None,
    resume: bool | None = None,
) -> dict[str, Any]:
    """Run the existing Hamburg stage ledger from one portable JSON configuration.

    Paths are resolved relative to the configuration file. Focused Hamburg
    modules remain responsible for producing each stage artifact; this facade
    only unifies their evidence, dependencies, and downstream invalidation.
    """

    config_path = Path(config_file).expanduser().resolve(strict=True)
    payload = _read_json_mapping(config_path)
    if payload.get("schema") != HAMBURG_EXECUTION_CONFIG_SCHEMA:
        raise HamburgExecutionWorkflowError(
            f"workflow config schema must be {HAMBURG_EXECUTION_CONFIG_SCHEMA!r}"
        )
    base_dir = config_path.parent
    configured_output = payload.get("output_dir")
    if output_dir is None:
        if not isinstance(configured_output, str) or not configured_output.strip():
            raise HamburgExecutionWorkflowError("workflow config requires output_dir")
        output_dir = _resolve_config_path(base_dir, configured_output)

    raw_stages = payload.get("stages", {})
    if not isinstance(raw_stages, Mapping):
        raise HamburgExecutionWorkflowError("workflow config stages must be an object")
    stage_manifests: dict[str, Path] = {}
    stage_feedback: dict[str, tuple[Path, ...]] = {}
    for raw_stage_id, raw_stage in raw_stages.items():
        stage_id = _canonical_stage_id(raw_stage_id)
        if not isinstance(raw_stage, Mapping):
            raise HamburgExecutionWorkflowError(f"workflow stage {stage_id} must be an object")
        manifest = raw_stage.get("manifest")
        if manifest is not None:
            if not isinstance(manifest, str) or not manifest.strip():
                raise HamburgExecutionWorkflowError(f"workflow stage {stage_id} manifest is invalid")
            if stage_id in stage_manifests:
                raise HamburgExecutionWorkflowError(f"duplicate workflow stage {stage_id}")
            stage_manifests[stage_id] = _resolve_config_path(base_dir, manifest)
        feedback = raw_stage.get("feedback", ())
        if not isinstance(feedback, Sequence) or isinstance(feedback, (str, bytes)):
            raise HamburgExecutionWorkflowError(f"workflow stage {stage_id} feedback must be a list")
        resolved_feedback = tuple(_resolve_config_path(base_dir, str(item)) for item in feedback)
        if resolved_feedback:
            stage_feedback[stage_id] = resolved_feedback

    configured_resume = payload.get("resume", True)
    if resume is None:
        if not isinstance(configured_resume, bool):
            raise HamburgExecutionWorkflowError("workflow config resume must be boolean")
        resume = configured_resume
    return materialize_hamburg_execution_plan(
        output_dir=Path(output_dir),
        stage_manifests=stage_manifests,
        stage_feedback=stage_feedback,
        resume=resume,
        workflow_id=str(payload.get("workflow_id", HAMBURG_EXECUTION_WORKFLOW_ID)),
    )


def _resolve_config_path(base_dir: Path, value: str) -> Path:
    path = Path(value).expanduser()
    return path if path.is_absolute() else (base_dir / path).resolve()


def materialize_hamburg_w1_topology_handoff(
    *,
    output_dir: Path,
    candidate_net_file: Path,
    topology_audit_file: Path,
    surface_comparison_file: Path,
    connection_mode_manifest_file: Path,
    sumo_load_report_file: Path,
    movement_smoke_file: Path,
    netedit_review_files: Sequence[Path],
    expected_review_junction_ids: Sequence[str],
) -> dict[str, Any]:
    """Adopt an audited OSM-derived topology as W1 without rebuilding it."""

    candidate = Path(candidate_net_file).expanduser().resolve(strict=True)
    candidate_hash = file_sha256(candidate)
    evidence_paths = {
        "topology_feedback": Path(topology_audit_file).expanduser().resolve(strict=True),
        "surface_comparison": Path(surface_comparison_file).expanduser().resolve(strict=True),
        "connection_mode": Path(connection_mode_manifest_file).expanduser().resolve(strict=True),
        "sumo_load": Path(sumo_load_report_file).expanduser().resolve(strict=True),
        "movement_smoke": Path(movement_smoke_file).expanduser().resolve(strict=True),
    }
    evidence = {name: _read_json_mapping(path) for name, path in evidence_paths.items()}
    errors: list[str] = []

    topology = evidence["topology_feedback"]
    if topology.get("schema") != "torii.junction-aggregation-preservation/v1" or topology.get("status") != "pass":
        errors.append("topology preservation is not pass")
    _check_candidate_binding(topology, candidate, candidate_hash, errors, prefix="variant")
    try:
        topology_source = Path(str(topology.get("source_net_file", ""))).resolve(strict=True)
    except (OSError, RuntimeError):
        errors.append("topology preservation source path is invalid")
    else:
        if topology.get("source_sha256") != file_sha256(topology_source):
            errors.append("topology preservation source hash mismatch")
    for key in (
        "unexpected_removed_normal_edge_count",
        "lost_shared_connection_count",
        "new_dangling_shared_normal_edge_count",
    ):
        if topology.get(key) != 0:
            errors.append(f"topology preservation {key} is not zero")
    boundary = topology.get("boundary_movement_preservation")
    expected_movement_keys: list[str] = []
    if (
        not isinstance(boundary, Mapping)
        or boundary.get("status") != "pass"
        or boundary.get("lost_boundary_movement_count") != 0
        or boundary.get("added_boundary_movement_count") != 0
    ):
        errors.append("topology boundary-movement preservation is not pass")
    else:
        groups = boundary.get("groups")
        if not isinstance(groups, list) or not groups:
            errors.append("topology boundary-movement groups are empty")
        else:
            for group in groups:
                if not isinstance(group, Mapping):
                    errors.append("topology boundary-movement group is invalid")
                    continue
                movements = group.get("variant_boundary_movements")
                if not isinstance(movements, list) or len(movements) != group.get("variant_boundary_movement_count"):
                    errors.append("topology boundary-movement group count mismatch")
                    continue
                expected_movement_keys.extend(map(str, movements))
    if not expected_movement_keys or len(set(expected_movement_keys)) != len(expected_movement_keys):
        errors.append("topology boundary-movement keys are empty or duplicated")

    surface = evidence["surface_comparison"]
    if (
        surface.get("schema") != "torii.sumo-surface-overlap-audit/v1"
        or surface.get("audit_engine") != "torii.bevel-strip-and-junction-polygon-area/v2"
        or surface.get("status") != "pass"
    ):
        errors.append("surface audit is not pass")
    _check_candidate_binding(surface, candidate, candidate_hash, errors, prefix="source")
    if surface.get("source_network_mutation") is not False:
        errors.append("surface audit did not preserve the candidate")
    for key in (
        "geometry_error_count",
        "junction_junction_overlap_count",
        "external_lane_non_owner_junction_overlap_count",
    ):
        if surface.get(key) != 0:
            errors.append(f"surface audit {key} is not zero")

    connection = evidence["connection_mode"]
    if (
        connection.get("schema") != "torii.connection_mode_regression_manifest.v1"
        or connection.get("status") != "pass"
        or connection.get("gate_status") != "pass"
        or connection.get("automatic_promotion_gate") != "pass"
    ):
        errors.append("connection-mode regression is not pass")
    _check_candidate_binding(connection, candidate, candidate_hash, errors, prefix="candidate")
    if connection.get("source_network_mutation") is not False:
        errors.append("connection-mode regression did not preserve the source")

    load = evidence["sumo_load"]
    if load.get("schema") != "torii.sumo-load-audit/v1" or load.get("status") != "pass":
        errors.append("SUMO load audit is not pass")
    _check_candidate_binding(load, candidate, candidate_hash, errors, prefix="source")
    if load.get("source_network_mutation") is not False:
        errors.append("SUMO load audit did not preserve the candidate")

    smoke = evidence["movement_smoke"]
    movement_keys = smoke.get("movement_keys")
    smoke_inputs = smoke.get("inputs")
    smoke_outputs = smoke.get("outputs")
    if (
        smoke.get("schema") != "torii.hamburg-2403-movement-smoke/v1"
        or smoke.get("status") != "pass"
        or smoke.get("vehicle_count") != len(expected_movement_keys)
        or smoke.get("movement_count") != len(expected_movement_keys)
        or smoke.get("vehicle_count") != smoke.get("ended")
        or smoke.get("vehicle_count") != smoke.get("inserted")
        or smoke.get("vehicle_count") != smoke.get("loaded")
        or smoke.get("running") != 0
        or smoke.get("waiting") != 0
        or smoke.get("teleports") != 0
        or smoke.get("collisions") != 0
        or smoke.get("movement_keys_unique") is not True
        or smoke.get("movement_keys_match_preservation") is not True
        or not isinstance(movement_keys, list)
        or set(map(str, movement_keys or ())) != set(expected_movement_keys)
    ):
        errors.append("movement smoke is not a complete zero-error preservation run")
    _check_candidate_binding(smoke, candidate, candidate_hash, errors, prefix="candidate")
    inspection = smoke.get("inspection")
    if not isinstance(inspection, Mapping) or inspection.get("status") != "pass":
        errors.append("movement smoke output inspection is not pass")
    else:
        inspected_summary = inspection.get("summary")
        inspected_tripinfo = inspection.get("tripinfo")
        if (
            not isinstance(inspected_summary, Mapping)
            or not isinstance(inspected_tripinfo, Mapping)
            or inspected_summary.get("loaded") != smoke.get("vehicle_count")
            or inspected_summary.get("inserted") != smoke.get("vehicle_count")
            or inspected_summary.get("arrived") != smoke.get("vehicle_count")
            or inspected_summary.get("running") != 0
            or inspected_summary.get("waiting") != 0
            or inspected_summary.get("teleports") != 0
            or inspected_summary.get("collisions") != 0
            or inspected_tripinfo.get("trip_count") != smoke.get("vehicle_count")
        ):
            errors.append("movement smoke inspection counts do not match the declared run")
    if not isinstance(smoke_inputs, Mapping) or not isinstance(smoke_outputs, Mapping):
        errors.append("movement smoke artifacts are missing")
    else:
        smoke_artifacts = {
            "route": smoke_inputs.get("route"),
            "preservation_audit": smoke_inputs.get("preservation_audit"),
            "summary": smoke_outputs.get("summary"),
            "tripinfo": smoke_outputs.get("tripinfo"),
        }
        for name, artifact in smoke_artifacts.items():
            if not isinstance(artifact, Mapping):
                errors.append(f"movement smoke artifact is missing: {name}")
                continue
            try:
                artifact_path = Path(str(artifact.get("path", ""))).resolve(strict=True)
            except (OSError, RuntimeError):
                errors.append(f"movement smoke artifact path is invalid: {name}")
                continue
            if artifact.get("sha256") != file_sha256(artifact_path):
                errors.append(f"movement smoke artifact hash mismatch: {name}")
        preservation_artifact = smoke_artifacts["preservation_audit"]
        if isinstance(preservation_artifact, Mapping):
            try:
                preservation_path = Path(str(preservation_artifact.get("path", ""))).resolve(strict=True)
            except (OSError, RuntimeError):
                preservation_path = Path()
            if (
                preservation_path != evidence_paths["topology_feedback"]
                or preservation_artifact.get("sha256") != file_sha256(evidence_paths["topology_feedback"])
            ):
                errors.append("movement smoke is not bound to the topology preservation audit")

    review_paths = tuple(Path(path).expanduser().resolve(strict=True) for path in netedit_review_files)
    expected_ids = {str(value) for value in expected_review_junction_ids if str(value)}
    if not review_paths or not expected_ids:
        errors.append("NetEdit review owners are empty")
    actual_review_ids: list[str] = []
    for path in review_paths:
        review = _read_json_mapping(path)
        target = review.get("target_junction")
        target_id = str(target.get("id", "")) if isinstance(target, Mapping) else ""
        actual_review_ids.append(target_id)
        if (
            review.get("schema") != "torii.netedit-background-review.direct/v1"
            or review.get("status") != "review_material_ready"
            or review.get("automatic_promotion_gate") != "blocked"
            or review.get("candidate_unchanged") is not True
            or review.get("candidate_sha256_before") != candidate_hash
            or review.get("candidate_sha256_after") != candidate_hash
            or review.get("mode_images_distinct") is not True
            or review.get("global_keyboard_or_mouse_input_used") is not False
            or review.get("foreground_context_restored") is not True
        ):
            errors.append(f"NetEdit review is not current and non-mutating: {path}")
        try:
            if Path(str(review.get("candidate_file", ""))).resolve() != candidate:
                errors.append(f"NetEdit review candidate path mismatch: {path}")
        except OSError:
            errors.append(f"NetEdit review candidate path invalid: {path}")
    if len(review_paths) != len(expected_ids) or set(actual_review_ids) != expected_ids:
        errors.append(
            f"NetEdit owner coverage mismatch: expected={sorted(expected_ids)}, actual={sorted(set(actual_review_ids))}"
        )
    if file_sha256(candidate) != candidate_hash:
        errors.append("candidate changed while W1 handoff evidence was checked")
    if errors:
        raise HamburgExecutionWorkflowError("W1 topology handoff rejected: " + "; ".join(errors))

    output = Path(output_dir).expanduser().resolve()
    output.mkdir(parents=True, exist_ok=True)
    manifest_file = output / "hamburg_official_corridor_geometry.manifest.json"
    if manifest_file.exists():
        raise HamburgExecutionWorkflowError(f"W1 topology handoff already exists: {manifest_file}")
    manifest: dict[str, Any] = {
        "schema": "torii.hamburg-official-corridor-geometry/v1",
        "status": "review_ready",
        "execution_gate": "pass",
        "execution_gate_reason": "hash-bound topology, SUMO, surface, connection, movement-smoke, and NetEdit-material checks pass",
        "claim_status": "osm-continuous-official-evidence-road-topology-review-candidate",
        "automatic_promotion_gate": "blocked",
        "network": {"path": str(candidate), "sha256": candidate_hash},
        "evidence": {
            name: {"path": str(path), "sha256": file_sha256(path)}
            for name, path in evidence_paths.items()
        },
        "netedit_review": {
            "status": "review_material_ready",
            "junction_ids": sorted(expected_ids),
            "report_count": len(review_paths),
            "reports": [{"path": str(path), "sha256": file_sha256(path)} for path in review_paths],
            "automatic_promotion_gate": "blocked",
        },
        "gates": {
            "topology_feedback": "pass",
            "surface_overlap": "pass",
            "connection_mode": "pass",
            "sumo_load": "pass",
            "movement_smoke": "pass",
            "netedit_review_material": "pass",
            "official_2403_signal_assets": "blocked",
            "automatic_promotion": "blocked",
        },
        "routeability": {
            "status": "pass",
            "scope": f"{len(expected_movement_keys)} source-authorized 2403 lane-level boundary movements only",
            "movement_count": len(expected_movement_keys),
            "teleports": 0,
            "collisions": 0,
        },
        "artifacts": {"network": str(candidate), "manifest": str(manifest_file)},
        "claim_boundary": {
            "proves": [
                "the exact candidate passes the recorded road-topology machine gates",
                "all declared physical review owners have current non-mutating NetEdit review material",
                "the candidate may feed fail-closed signal and detector diagnostics",
            ],
            "does_not_prove": [
                "official 2403 signal topology or historical signal timing",
                "that detector streams sharing a SUMO lane are additive or redundant",
                "full corridor demand calibration or final digital-twin promotion",
                "human approval from NetEdit screenshots alone",
            ],
        },
    }
    write_json_atomic(manifest_file, manifest, ensure_ascii=False, sort_keys=True)
    return {**manifest, "manifest_file": str(manifest_file)}


def _read_json_mapping(path: Path) -> Mapping[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise HamburgExecutionWorkflowError(f"cannot read W1 evidence: {path}") from exc
    if not isinstance(payload, Mapping):
        raise HamburgExecutionWorkflowError(f"W1 evidence is not an object: {path}")
    return payload


def _check_candidate_binding(
    payload: Mapping[str, Any],
    candidate: Path,
    candidate_hash: str,
    errors: list[str],
    *,
    prefix: str,
) -> None:
    try:
        bound_path = Path(str(payload.get(f"{prefix}_net_file", ""))).resolve()
    except OSError:
        bound_path = Path()
    if bound_path != candidate or payload.get(f"{prefix}_sha256") != candidate_hash:
        errors.append(f"{prefix} candidate binding mismatch")


def materialize_hamburg_execution_plan(
    *,
    output_dir: Path,
    stage_manifests: Mapping[str, Path],
    stage_feedback: Mapping[str, Path | Sequence[Path]] | None = None,
    workflow_id: str = HAMBURG_EXECUTION_WORKFLOW_ID,
    resume: bool = True,
) -> dict[str, Any]:
    """Write a hash-bound, resumable plan from existing stage manifests.

    ``stage_manifests`` is intentionally caller-supplied.  This keeps the
    planner reusable across dated runs and prevents it from guessing which
    generated network or official snapshot is authoritative.  Missing stages
    are represented as ``not_run``; a blocked dependency is never treated as a
    successful prerequisite.
    """

    if workflow_id != HAMBURG_EXECUTION_WORKFLOW_ID:
        raise HamburgExecutionWorkflowError(f"unsupported workflow_id: {workflow_id}")
    normalized = _normalize_stage_paths(stage_manifests)
    normalized_feedback = _normalize_feedback_paths(stage_feedback or {})
    output = Path(output_dir).expanduser().resolve()
    output.mkdir(parents=True, exist_ok=True)
    plan_file = output / "execution-plan.manifest.json"
    previous = _read_previous_plan(plan_file) if resume and plan_file.is_file() else None

    stage_records: dict[str, dict[str, Any]] = {}
    for stage_id in STAGE_DEPENDENCIES:
        path = normalized.get(stage_id)
        feedback_paths = normalized_feedback.get(stage_id, ())
        record = _read_stage_record(stage_id, path, feedback_paths=feedback_paths)
        record["contract"] = _stage_contract(stage_id)
        stage_records[stage_id] = record

    _apply_network_binding_contract(stage_records)
    _apply_stage_dependency_contract(stage_records)
    changed = _changed_stage_ids(previous, stage_records)
    invalidated = {
        stage_id
        for stage_id in _downstream_closure(changed)
        if _stage_was_materialized(previous, stage_id)
    }
    invalidated.update(_pending_invalidated_stage_ids(previous, changed))
    for stage_id in sorted(invalidated, key=_stage_sort_key):
        if stage_id not in stage_records:
            continue
        record = stage_records[stage_id]
        if record["status"] == "not_run":
            continue
        record["resume_decision"] = "invalidate_and_rerun"
        record["invalidation_reason"] = "upstream_stage_manifest_changed"
        record["effective_status"] = "not_run"

    for stage_id, record in stage_records.items():
        if "effective_status" not in record:
            record["effective_status"] = record["status"]
        dependencies = STAGE_DEPENDENCIES[stage_id]
        dependency_decisions = {
            dependency: _stage_gate(stage_records[dependency])
            for dependency in dependencies
        }
        record["dependencies"] = list(dependencies)
        record["dependency_gate"] = dependency_decisions
        failed = {
            dependency: decision
            for dependency, decision in dependency_decisions.items()
            if decision != "pass"
        }
        if record["effective_status"] == "not_run":
            if failed:
                record["readiness"] = "blocked"
                record["blocked_by"] = failed
            else:
                record["readiness"] = "ready"
        elif record.get("execution_gate") != "pass":
            record["readiness"] = "blocked"
            record["blocked_by"] = {stage_id: record.get("decision", "blocked")}
        elif failed:
            # An already-produced diagnostic artifact remains visible, but it
            # cannot make a downstream stage eligible for execution.
            record["readiness"] = "blocked"
            record["blocked_by"] = failed
        else:
            record["readiness"] = "complete"

    next_action = _next_action(stage_records)
    plan_revision = int(previous.get("plan_revision", 0)) + 1 if previous else 1
    first_invalid_stage = next_action.get("stage_id")
    replan = _replan_summary(
        stage_records,
        next_action=next_action,
        changed=changed,
        invalidated=invalidated,
        first_invalid_stage=first_invalid_stage,
    )
    capabilities = _capability_matrix(stage_records)
    promotion_pass = all(_stage_promotable(record) for record in stage_records.values())
    stage_records["W5"] = _summary_stage_record(stage_records, capabilities)
    plan: dict[str, Any] = {
        "schema": HAMBURG_EXECUTION_WORKFLOW_SCHEMA,
        "workflow_id": workflow_id,
        "plan_revision": plan_revision,
        "execution_mode": "resumable_fail_closed",
        "loop": [
            "plan",
            "inspect_existing_torii_code_and_sources",
            "implement_minimally",
            "unit_test",
            "run_real_inputs",
            "audit",
            "revise_plan_on_failure",
            "rerun_from_first_invalid_stage",
        ],
        "stages": stage_records,
        "next_action": next_action,
        "first_invalid_stage": first_invalid_stage,
        "replan": replan,
        "changed_stages": sorted(changed, key=_stage_sort_key),
        "invalidated_downstream_stages": sorted(invalidated, key=_stage_sort_key),
        "capabilities": capabilities,
        "promotion": {
            "decision": "pass" if promotion_pass else "blocked",
            "automatic": True,
            "execution_complete": next_action["status"] == "complete",
            "requires": (
                "W0, W1, W2, W3a, W3b, and W4 are materialized with "
                "execution_gate=pass, decision=pass, and automatic_promotion_gate=pass"
            ),
        },
        "claim_boundary": {
            "proves": [
                "which dated Torii stage artifacts are being reused",
                "that each referenced manifest and network binding is byte-hash stable",
                "which stage is eligible or blocked next",
            ],
            "does_not_prove": [
                "road, signal, demand, or field correctness by a manifest existing",
                "historical signal timing when official observations are absent",
                "a unique OD matrix from detector counts",
            ],
        },
    }
    write_json_atomic(plan_file, plan, ensure_ascii=False, sort_keys=True)
    plan["plan_file"] = str(plan_file)
    plan["plan_sha256"] = file_sha256(plan_file)
    return plan


def _normalize_stage_paths(stage_manifests: Mapping[str, Path]) -> dict[str, Path]:
    if not isinstance(stage_manifests, Mapping):
        raise HamburgExecutionWorkflowError("stage_manifests must be a mapping")
    result: dict[str, Path] = {}
    for raw_stage, raw_path in stage_manifests.items():
        stage_id = _canonical_stage_id(raw_stage)
        path = Path(raw_path).expanduser().resolve()
        if stage_id in result and result[stage_id] != path:
            raise HamburgExecutionWorkflowError(f"duplicate stage id: {stage_id}")
        result[stage_id] = path
    return result


def _stage_contract(stage_id: str) -> dict[str, list[str]]:
    contract = STAGE_CONTRACTS[stage_id]
    return {key: list(values) for key, values in contract.items()}


def _normalize_feedback_paths(
    stage_feedback: Mapping[str, Path | Sequence[Path]],
) -> dict[str, tuple[Path, ...]]:
    if not isinstance(stage_feedback, Mapping):
        raise HamburgExecutionWorkflowError("stage_feedback must be a mapping")
    result: dict[str, tuple[Path, ...]] = {}
    for raw_stage, raw_paths in stage_feedback.items():
        stage_id = _canonical_stage_id(raw_stage)
        if isinstance(raw_paths, (str, bytes, Path)):
            values = (Path(raw_paths),)
        else:
            try:
                values = tuple(Path(value) for value in raw_paths)
            except TypeError as exc:
                raise HamburgExecutionWorkflowError(
                    f"feedback paths for {stage_id} must be a path or sequence of paths"
                ) from exc
        if not values:
            raise HamburgExecutionWorkflowError(f"feedback paths for {stage_id} cannot be empty")
        result[stage_id] = tuple(path.expanduser().resolve() for path in values)
    return result


def _read_stage_record(
    stage_id: str,
    path: Path | None,
    *,
    feedback_paths: Sequence[Path] = (),
) -> dict[str, Any]:
    base: dict[str, Any] = {
        "stage_id": stage_id,
        "name": STAGE_NAMES[stage_id],
        "manifest": None,
        "status": "not_run",
        "automatic_promotion_gate": "blocked",
        "decision": "not_run",
        "execution_gate": "blocked",
    }
    if path is None:
        return base
    base["manifest"] = str(path)
    if not path.is_file():
        base.update({"status": "blocked", "decision": "blocked", "reason": "manifest_missing"})
        return base
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        base.update({"status": "blocked", "decision": "blocked", "reason": f"manifest_invalid: {exc}"})
        return base
    if not isinstance(payload, Mapping):
        base.update({"status": "blocked", "decision": "blocked", "reason": "manifest_not_object"})
        return base
    reported_schema = payload.get("schema") or payload.get("schema_id")
    raw_status = str(payload.get("status", "unknown"))
    gate = str(payload.get("automatic_promotion_gate", "blocked"))
    decision = _manifest_decision(raw_status, gate)
    declared_execution_gate = str(payload.get("execution_gate", "")).strip().lower()
    execution_gate = (
        declared_execution_gate
        if declared_execution_gate in {"pass", "blocked"}
        else _execution_gate(stage_id, payload, decision)
    )
    feedback = _manifest_feedback(payload)
    base.update(
        {
            "status": raw_status,
            "automatic_promotion_gate": gate,
            "decision": decision,
            "execution_gate": execution_gate,
            "manifest_sha256": file_sha256(path),
            "manifest_bytes": path.stat().st_size,
            "reported_schema": reported_schema,
            "reason": _manifest_reason(payload),
            "feedback": feedback,
        }
    )
    if stage_id == "W3b":
        gates = payload.get("gates")
        base["sensor_aggregation_semantics"] = (
            gates.get("sensor_aggregation_semantics")
            if isinstance(gates, Mapping)
            else None
        )
    if reported_schema not in STAGE_SCHEMAS[stage_id]:
        expected = ", ".join(sorted(STAGE_SCHEMAS[stage_id]))
        _block_stage_record(
            base,
            f"manifest_schema_mismatch: expected {expected}; got {reported_schema!r}",
        )
    else:
        if stage_id in NETWORK_BOUND_STAGES:
            binding, error = _network_binding_from_manifest(stage_id, payload, path)
            if binding:
                base["network_binding"] = binding
            if error:
                _block_stage_record(base, error)
        if stage_id in {"W3a", "W3b"}:
            bindings, errors = _stage_artifact_bindings_from_manifest(stage_id, payload, path)
            base["artifact_bindings"] = bindings
            if errors:
                _block_stage_record(base, "; ".join(errors))
        if stage_id in {"W3b", "W4"}:
            bindings, errors = _stage_dependency_bindings_from_manifest(stage_id, payload, path)
            base["stage_bindings"] = bindings
            if errors:
                _block_stage_record(base, "; ".join(errors))
    for feedback_path in feedback_paths:
        _merge_feedback_manifest(base, feedback_path)
    if not base["reason"]:
        base.pop("reason")
    if not base["feedback"]:
        base.pop("feedback")
    return base


def _network_binding_from_manifest(
    stage_id: str,
    payload: Mapping[str, Any],
    manifest_path: Path,
) -> tuple[dict[str, Any], str | None]:
    paths: Mapping[str, tuple[tuple[str, ...], ...]] = {
        "W1": (("network_binding",), ("network",)),
        "W2": (("network_binding",), ("source", "candidate_net")),
        "W3b": (("network_binding",), ("source", "candidate_net"), ("network",)),
        "W4": (("network_binding",), ("source", "net")),
    }
    raw_binding: Mapping[str, Any] | None = None
    for keys in paths[stage_id]:
        value: Any = payload
        for key in keys:
            value = value.get(key) if isinstance(value, Mapping) else None
        if isinstance(value, Mapping):
            raw_binding = value
            break
    if raw_binding is None:
        return {}, "network_binding_missing"

    raw_path = raw_binding.get("path")
    raw_sha256 = raw_binding.get("sha256")
    binding: dict[str, Any] = {
        "path": str(raw_path or ""),
        "sha256": str(raw_sha256 or "").lower(),
        "validation": "blocked",
    }
    if not isinstance(raw_path, str) or not raw_path.strip():
        return binding, "network_binding_path_missing"
    if (
        not isinstance(raw_sha256, str)
        or len(raw_sha256) != 64
        or any(character not in "0123456789abcdefABCDEF" for character in raw_sha256)
    ):
        return binding, "network_binding_sha256_invalid"
    candidate = Path(raw_path).expanduser()
    if not candidate.is_absolute():
        candidate = manifest_path.parent / candidate
    try:
        candidate = candidate.resolve(strict=True)
        actual_sha256 = file_sha256(candidate)
    except (OSError, RuntimeError) as exc:
        return binding, f"network_binding_unreadable: {exc}"
    binding.update(
        {
            "path": str(candidate),
            "actual_sha256": actual_sha256,
        }
    )
    if actual_sha256 != str(raw_sha256).lower():
        return binding, "network_binding_sha256_mismatch"
    binding["validation"] = "pass"
    return binding, None


def _apply_network_binding_contract(stages: Mapping[str, dict[str, Any]]) -> None:
    expected = stages["W1"].get("network_binding")
    if not isinstance(expected, Mapping) or expected.get("validation") != "pass":
        return
    expected_sha256 = str(expected["sha256"])
    for stage_id in ("W2", "W3b", "W4"):
        record = stages[stage_id]
        if record.get("status") == "not_run":
            continue
        binding = record.get("network_binding")
        if not isinstance(binding, dict) or binding.get("validation") != "pass":
            continue
        binding["expected_w1_sha256"] = expected_sha256
        if binding.get("sha256") != expected_sha256:
            binding["validation"] = "blocked"
            _block_stage_record(record, "network_binding_does_not_match_W1")


def _stage_dependency_bindings_from_manifest(
    stage_id: str,
    payload: Mapping[str, Any],
    manifest_path: Path,
) -> tuple[dict[str, Any], list[str]]:
    source = payload.get("source")
    if not isinstance(source, Mapping):
        return {}, ["stage_binding_source_missing"]
    bindings: dict[str, Any] = {}
    errors: list[str] = []
    required_names = {
        "W3b": ("count_stream_snapshot",),
        "W4": (
            "signal_binding_manifest",
            "detector_binding_manifest",
            "count_scope_manifest",
            "count_stream_snapshot",
            "canonical_count_file",
        ),
    }[stage_id]
    for name in required_names:
        binding, error = _file_identity_from_manifest(
            source.get(name),
            manifest_path,
            label=name,
        )
        if binding:
            bindings[name] = binding
        if error:
            errors.append(error)
    if stage_id == "W4":
        optional_names = ("signal_observation_manifest", "tls_link_events")
        present = [name for name in optional_names if source.get(name) is not None]
        if present and len(present) != len(optional_names):
            errors.append("stage_binding_signal_history_incomplete")
        for name in present:
            binding, error = _file_identity_from_manifest(
                source.get(name),
                manifest_path,
                label=name,
            )
            if binding:
                bindings[name] = binding
            if error:
                errors.append(error)
    return bindings, errors


def _stage_artifact_bindings_from_manifest(
    stage_id: str,
    payload: Mapping[str, Any],
    manifest_path: Path,
) -> tuple[dict[str, Any], list[str]]:
    artifacts = payload.get("artifacts")
    if not isinstance(artifacts, Mapping):
        return {}, ["artifact_binding_source_missing"]
    names = {
        "W3a": ("count_streams_raw", "counts_simulation_15min"),
        "W3b": ("detector_mapping",),
    }[stage_id]
    bindings: dict[str, Any] = {}
    errors: list[str] = []
    for name in names:
        binding, error = _file_identity_from_manifest(
            artifacts.get(name),
            manifest_path,
            label=name,
        )
        if binding:
            bindings[name] = binding
        if error:
            errors.append(error)
    return bindings, errors


def _file_identity_from_manifest(
    raw_identity: object,
    manifest_path: Path,
    *,
    label: str,
) -> tuple[dict[str, Any], str | None]:
    if not isinstance(raw_identity, Mapping):
        return {}, f"stage_binding_{label}_missing"
    raw_path = raw_identity.get("path")
    raw_sha256 = raw_identity.get("sha256")
    binding: dict[str, Any] = {
        "path": str(raw_path or ""),
        "sha256": str(raw_sha256 or "").lower(),
        "validation": "blocked",
    }
    if not isinstance(raw_path, str) or not raw_path.strip():
        return binding, f"stage_binding_{label}_path_missing"
    if (
        not isinstance(raw_sha256, str)
        or len(raw_sha256) != 64
        or any(character not in "0123456789abcdefABCDEF" for character in raw_sha256)
    ):
        return binding, f"stage_binding_{label}_sha256_invalid"
    path = Path(raw_path).expanduser()
    if not path.is_absolute():
        path = manifest_path.parent / path
    try:
        path = path.resolve(strict=True)
        actual_sha256 = file_sha256(path)
    except (OSError, RuntimeError) as exc:
        return binding, f"stage_binding_{label}_unreadable: {exc}"
    binding.update({"path": str(path), "actual_sha256": actual_sha256})
    if actual_sha256 != raw_sha256.lower():
        return binding, f"stage_binding_{label}_sha256_mismatch"
    binding["validation"] = "pass"
    return binding, None


def _apply_stage_dependency_contract(stages: Mapping[str, dict[str, Any]]) -> None:
    checks = (
        ("W3b", "count_stream_snapshot", "W3a", "artifact_bindings", "count_streams_raw"),
        ("W4", "signal_binding_manifest", "W2", None, None),
        ("W4", "detector_binding_manifest", "W3b", None, None),
        ("W4", "count_scope_manifest", "W3a", None, None),
        ("W4", "count_stream_snapshot", "W3a", "artifact_bindings", "count_streams_raw"),
        (
            "W4",
            "canonical_count_file",
            "W3a",
            "artifact_bindings",
            "counts_simulation_15min",
        ),
    )
    for record_id, name, expected_stage_id, expected_container, expected_name in checks:
        record = stages[record_id]
        if record.get("status") == "not_run":
            continue
        bindings = record.get("stage_bindings")
        if not isinstance(bindings, Mapping):
            continue
        expected_record = stages[expected_stage_id]
        if expected_container is None:
            expected_sha256 = expected_record.get("manifest_sha256")
        else:
            expected_bindings = expected_record.get(expected_container)
            expected_binding = (
                expected_bindings.get(expected_name)
                if isinstance(expected_bindings, Mapping)
                else None
            )
            expected_sha256 = (
                expected_binding.get("sha256")
                if isinstance(expected_binding, Mapping)
                else None
            )
        if not isinstance(expected_sha256, str):
            continue
        binding = bindings.get(name)
        if not isinstance(binding, dict) or binding.get("validation") != "pass":
            continue
        binding["expected_stage_id"] = expected_stage_id
        binding["expected_sha256"] = expected_sha256
        if binding.get("sha256") != expected_sha256:
            binding["validation"] = "blocked"
            _block_stage_record(
                record,
                f"stage_binding_{name}_does_not_match_{expected_stage_id}",
            )
    w4 = stages["W4"]
    w3b = stages["W3b"]
    if (
        w4.get("status") != "not_run"
        and isinstance(w3b.get("manifest_sha256"), str)
        and w3b.get("sensor_aggregation_semantics") != "pass"
    ):
        _block_stage_record(w4, "W3b_sensor_aggregation_semantics_not_pass")


def _block_stage_record(record: dict[str, Any], reason: str) -> None:
    existing = record.get("contract_error")
    if isinstance(existing, str) and existing and reason not in existing.split("; "):
        reason = f"{existing}; {reason}"
    record["decision"] = "blocked"
    record["execution_gate"] = "blocked"
    record["contract_error"] = reason
    record["reason"] = reason


def _merge_feedback_manifest(record: dict[str, Any], path: Path) -> None:
    """Attach an auxiliary diagnostic manifest without changing stage status."""

    metadata = {
        "path": str(path),
    }
    feedback_manifests = record.setdefault("feedback_manifests", [])
    if not isinstance(feedback_manifests, list):
        feedback_manifests = []
        record["feedback_manifests"] = feedback_manifests
    feedback_manifests.append(metadata)
    if len(feedback_manifests) == 1:
        record["feedback_manifest"] = metadata
    else:
        record.pop("feedback_manifest", None)
    if not path.is_file():
        record.setdefault("feedback", {})["feedback_manifest_error"] = "manifest_missing"
        return
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        record.setdefault("feedback", {})["feedback_manifest_error"] = f"manifest_invalid: {exc}"
        return
    if not isinstance(payload, Mapping):
        record.setdefault("feedback", {})["feedback_manifest_error"] = "manifest_not_object"
        return
    metadata.update(
        {
            "sha256": file_sha256(path),
            "bytes": path.stat().st_size,
        }
    )
    record["feedback"] = _merge_feedback(
        record.get("feedback", {}),
        _manifest_feedback(payload),
    )


def _merge_feedback(left: Any, right: Mapping[str, Any]) -> dict[str, Any]:
    merged = dict(left) if isinstance(left, Mapping) else {}
    for key, value in right.items():
        if isinstance(merged.get(key), Mapping) and isinstance(value, Mapping):
            nested = dict(merged[key])
            nested.update(value)
            merged[key] = nested
        else:
            merged[key] = value
    return merged


def _execution_gate(stage_id: str, payload: Mapping[str, Any], decision: str) -> str:
    """Return whether this artifact may feed its declared downstream stage.

    W0 deliberately contains signal-asset discovery as evidence, but its
    downstream scope contract is still usable when the discovery says 2403 is
    unresolved.  Signal completeness remains W2's promotion gate.
    """

    if stage_id == "W0" and payload.get("schema") == "torii.hamburg-named-corridor-scope/v1":
        nodes = payload.get("nodes")
        road_scope = payload.get("official_road_scope")
        if isinstance(nodes, list) and len(nodes) == 3 and isinstance(road_scope, Mapping):
            return "pass"
    return "pass" if decision == "pass" else "blocked"


def _manifest_decision(status: str, gate: str) -> str:
    if gate == "pass" and status in {"pass", "ready", "complete"}:
        return "pass"
    if status in {"blocked", "fail", "error"} or gate == "blocked":
        return "blocked"
    if status in {"partial", "review_ready", "review_required"}:
        return "review_required"
    return "blocked"


def _stage_gate(record: Mapping[str, Any]) -> str:
    return (
        "pass"
        if record.get("execution_gate") == "pass"
        and record.get("effective_status", record.get("status")) != "not_run"
        else "blocked"
    )


def _manifest_reason(payload: Mapping[str, Any]) -> str | None:
    for key in ("execution_gate_reason", "reason", "error", "next_stage", "next_action"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    missing_nodes = payload.get("missing_required_node_ids")
    if isinstance(missing_nodes, list) and missing_nodes:
        return f"missing_required_node_ids={missing_nodes}"
    incomplete_streams = payload.get("incomplete_stream_ids")
    if isinstance(incomplete_streams, list) and incomplete_streams:
        return f"incomplete_stream_ids={incomplete_streams}"
    publication_gap = payload.get("publication_gap")
    if isinstance(publication_gap, Mapping):
        decision = publication_gap.get("decision")
        if isinstance(decision, str) and decision.strip():
            return f"publication_gap.decision={decision.strip()}"
    for container_key in ("gates", "signal_assets", "scope_evidence"):
        container = payload.get(container_key)
        if isinstance(container, Mapping):
            for key in ("reason", "decision", "missing_count_node_ids", "unresolved_node_ids"):
                value = container.get(key)
                if value not in (None, [], {}, ""):
                    return f"{container_key}.{key}={value}"
    return None


def _manifest_feedback(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Extract bounded machine feedback for the next Codex re-plan.

    Stage manifests are deliberately not copied wholesale into the ledger:
    large event inventories and source payloads belong beside their stage.  The
    ledger keeps only the fields that tell the next run why it stopped and what
    evidence must be resolved.
    """

    feedback: dict[str, Any] = {}
    for key in (
        "execution_gate_reason",
        "reason",
        "error",
        "next_stage",
        "next_action",
        "missing_required_node_ids",
        "unresolved_node_ids",
        "resolved_node_ids",
        "incomplete_stream_ids",
        "failed_stream_ids",
    ):
        value = payload.get(key)
        if value not in (None, "", [], {}):
            feedback[key] = value
    for key in ("publication_gap", "gates", "quality_gate"):
        value = payload.get(key)
        if isinstance(value, Mapping):
            selected = {
                subkey: value[subkey]
                for subkey in ("decision", "reason", "next_action", "missing_required_node_ids", "status")
                if subkey in value and value[subkey] not in (None, "", [], {})
            }
            if selected:
                feedback[key] = selected
    selections = payload.get("selections")
    if isinstance(selections, list):
        selected_node_ids = [
            str(row.get("selected_node", {}).get("node_id"))
            for row in selections
            if isinstance(row, Mapping)
            and isinstance(row.get("selected_node"), Mapping)
            and row["selected_node"].get("node_id") not in (None, "")
        ]
        if selected_node_ids:
            feedback["official_node_identity"] = {
                "decision": payload.get("decision"),
                "human_action_required": payload.get("human_action_required"),
                "selected_node_ids": selected_node_ids,
            }
    return feedback


def _replan_summary(
    stages: Mapping[str, Mapping[str, Any]],
    *,
    next_action: Mapping[str, Any],
    changed: set[str],
    invalidated: set[str],
    first_invalid_stage: str | None,
) -> dict[str, Any]:
    """Describe the smallest safe resume action after the current audit."""

    feedback = stages.get(first_invalid_stage, {}) if first_invalid_stage else {}
    return {
        "required": bool(first_invalid_stage or changed or invalidated),
        "first_invalid_stage": first_invalid_stage,
        "resume_action": next_action.get("action"),
        "changed_stages": sorted(changed, key=_stage_sort_key),
        "invalidated_downstream_stages": sorted(invalidated, key=_stage_sort_key),
        "feedback": feedback.get("feedback", {}),
    }


def _read_previous_plan(path: Path) -> Mapping[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise HamburgExecutionWorkflowError(f"cannot read previous execution plan: {path}") from exc
    if not isinstance(payload, Mapping) or payload.get("schema") != HAMBURG_EXECUTION_WORKFLOW_SCHEMA:
        raise HamburgExecutionWorkflowError("previous execution plan has an incompatible schema")
    return payload


def _stage_was_materialized(previous: Mapping[str, Any] | None, stage_id: str) -> bool:
    if previous is None:
        return False
    stages = previous.get("stages")
    record = stages.get(stage_id) if isinstance(stages, Mapping) else None
    return (
        isinstance(record, Mapping)
        and record.get("manifest") is not None
        and record.get("effective_status", record.get("status")) != "not_run"
    )


def _pending_invalidated_stage_ids(
    previous: Mapping[str, Any] | None,
    changed: set[str],
) -> set[str]:
    """Keep invalidation active until the downstream manifest identity changes."""

    if previous is None:
        return set()
    stages = previous.get("stages")
    if not isinstance(stages, Mapping):
        return set()
    return {
        stage_id
        for stage_id in STAGE_DEPENDENCIES
        if stage_id not in changed
        and isinstance(stages.get(stage_id), Mapping)
        and stages[stage_id].get("resume_decision") == "invalidate_and_rerun"
    }


def _changed_stage_ids(previous: Mapping[str, Any] | None, current: Mapping[str, Mapping[str, Any]]) -> set[str]:
    if previous is None:
        return set()
    old_stages = previous.get("stages")
    if not isinstance(old_stages, Mapping):
        return set(current)
    changed: set[str] = set()
    for stage_id, record in current.items():
        old = old_stages.get(stage_id)
        if not isinstance(old, Mapping):
            if record.get("status") != "not_run":
                changed.add(stage_id)
            continue
        if (
            old.get("manifest_sha256") != record.get("manifest_sha256")
            or _feedback_manifest_sha256(old) != _feedback_manifest_sha256(record)
            or (
                stage_id == "W1"
                and old.get("network_binding") != record.get("network_binding")
            )
            or old.get("artifact_bindings") != record.get("artifact_bindings")
            or _w4_signal_history_binding_state(old)
            != _w4_signal_history_binding_state(record)
        ):
            changed.add(stage_id)
    return changed


def _w4_signal_history_binding_state(
    record: Mapping[str, Any],
) -> tuple[object, object] | None:
    if record.get("stage_id") != "W4":
        return None
    bindings = record.get("stage_bindings")
    if not isinstance(bindings, Mapping):
        return (None, None)
    return (
        bindings.get("signal_observation_manifest"),
        bindings.get("tls_link_events"),
    )


def _feedback_manifest_sha256(
    record: Mapping[str, Any],
) -> tuple[tuple[str, str | None], ...] | None:
    """Return the hash identity of every auxiliary feedback manifest.

    A stage may collect several independent audits (for example, a historical
    asset census and an official-node identity refresh).  Comparing only the
    legacy singular ``feedback_manifest`` silently ignored changes once the
    second audit was attached.  Keep the input order deterministic and include
    missing-file entries as ``None`` so that a later materialization also
    invalidates the plan.
    """

    manifests = record.get("feedback_manifests")
    if isinstance(manifests, list):
        identities: list[tuple[str, str | None]] = []
        for manifest in manifests:
            if not isinstance(manifest, Mapping) or not manifest.get("path"):
                continue
            value = manifest.get("sha256")
            identities.append((str(manifest["path"]), str(value) if value else None))
        if identities:
            return tuple(identities)

    # Backwards compatibility for plans written before the plural field was
    # introduced.
    feedback_manifest = record.get("feedback_manifest")
    if not isinstance(feedback_manifest, Mapping) or not feedback_manifest.get("path"):
        return None
    value = feedback_manifest.get("sha256")
    return ((str(feedback_manifest["path"]), str(value) if value else None),)


def _capability_matrix(stages: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    stage_by_capability = {
        "road_topology": "W1",
        "official_signal_control": "W2",
        "official_counts": "W3a",
        "detector_binding": "W3b",
        "demand_inference_and_replay": "W4",
    }
    capabilities = {
        capability: {
            "stage_id": stage_id,
            "status": stages[stage_id].get("effective_status", stages[stage_id].get("status")),
            "decision": stages[stage_id].get("decision"),
            "execution_gate": stages[stage_id].get("execution_gate"),
            "automatic_promotion_gate": stages[stage_id].get("automatic_promotion_gate"),
        }
        for capability, stage_id in stage_by_capability.items()
    }
    capabilities["field_faithful_digital_twin"] = {
        "status": "pass" if all(_stage_promotable(record) for record in stages.values()) else "blocked"
    }
    return capabilities


def _stage_promotable(record: Mapping[str, Any]) -> bool:
    return (
        record.get("effective_status", record.get("status")) != "not_run"
        and record.get("execution_gate") == "pass"
        and record.get("automatic_promotion_gate") == "pass"
        and record.get("decision") == "pass"
    )


def _summary_stage_record(
    stages: Mapping[str, Mapping[str, Any]],
    capabilities: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "stage_id": "W5",
        "name": STAGE_NAMES["W5"],
        "generated": True,
        "manifest": None,
        "status": "complete",
        "effective_status": "complete",
        "automatic_promotion_gate": "pass",
        "decision": "pass",
        "execution_gate": "pass",
        "readiness": "complete",
        "dependencies": list(STAGE_DEPENDENCIES),
        "dependency_gate": {
            stage_id: _stage_gate(record)
            for stage_id, record in stages.items()
        },
        "summarized_capabilities": list(capabilities),
        "contract": _stage_contract("W5"),
    }


def _downstream_closure(changed: set[str]) -> set[str]:
    invalidated: set[str] = set()
    changed_again = True
    while changed_again:
        changed_again = False
        for stage_id, dependencies in STAGE_DEPENDENCIES.items():
            if (
                stage_id not in changed
                and stage_id not in invalidated
                and any(dependency in changed or dependency in invalidated for dependency in dependencies)
            ):
                invalidated.add(stage_id)
                changed_again = True
    return invalidated


def _next_action(stages: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    for stage_id in STAGE_DEPENDENCIES:
        record = stages[stage_id]
        if record.get("effective_status") == "not_run" and record.get("readiness") == "ready":
            return {
                "stage_id": stage_id,
                "status": "ready",
                "action": f"run_{STAGE_NAMES[stage_id]}",
            }
    for stage_id in STAGE_DEPENDENCIES:
        record = stages[stage_id]
        if record.get("execution_gate") != "pass" and record.get("effective_status") != "not_run":
            return {
                "stage_id": stage_id,
                "status": "blocked",
                "action": "resolve_stage_gate",
                "blocked_by": record.get("blocked_by", {stage_id: record.get("decision")}),
            }
        if record.get("effective_status") == "not_run":
            return {
                "stage_id": stage_id,
                "status": "blocked",
                "action": "resolve_dependency_gate",
                "blocked_by": record.get("blocked_by", {}),
            }
    return {"stage_id": None, "status": "complete", "action": "none"}


def _stage_sort_key(stage_id: str) -> tuple[int, str]:
    return (_STAGE_INDEX.get(stage_id, len(_STAGE_INDEX)), stage_id)


__all__ = [
    "HAMBURG_EXECUTION_CONFIG_SCHEMA",
    "HAMBURG_EXECUTION_WORKFLOW_ID",
    "HAMBURG_EXECUTION_WORKFLOW_SCHEMA",
    "HamburgExecutionWorkflowError",
    "materialize_hamburg_w1_topology_handoff",
    "materialize_hamburg_execution_plan",
    "materialize_hamburg_execution_plan_from_config",
]
