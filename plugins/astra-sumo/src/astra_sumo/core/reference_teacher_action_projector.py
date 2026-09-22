"""Project existing reference audits into non-executable teacher review actions.

This module deliberately does not compare or mutate SUMO networks.  It reads
already-produced, hash-bound audit artifacts and turns their findings into a
small typed review contract.  Every projected action remains blocked for
target-city transfer: a human-cleaned reference is evidence about a modelling
choice, not authority to repeat that choice in another city.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .artifact_io import write_json_atomic
from .candidate_contracts import file_sha256


REFERENCE_TEACHER_ACTION_CONTRACTS_V2_SCHEMA = "torii.reference_teacher_action_contracts.v2"

_CONTROLLER_REVIEW_TYPES = {
    "bind_or_downgrade_uncontrolled_traffic_light_junction",
    "downgrade_low_vehicle_approach_tls",
    "restore_reference_multi_junction_tls_scope",
    "restore_tls_controlled_connections",
    "split_multi_junction_tls",
}
_LINKINDEX_REVIEW_TYPES = {
    "inspect_reference_sparse_linkindex_programs",
    "inspect_sparse_linkindex",
    "restore_shared_linkindex_groups",
}

_COMMON_TARGET_REQUIREMENTS = [
    "independent current target-city evidence must authorize the edit",
    "the target SUMO source and every referenced edge, lane, junction, and controller must be hash-bound",
    "the candidate must pass outside-scope parity, Connection Mode, SUMO-load, and routeability gates",
]


def project_reference_teacher_actions_v2(
    *,
    reference_join_audit_file: Path,
    join_action_contracts_file: Path | None = None,
    reference_hierarchy_audit_file: Path | None = None,
    reference_scope_audit_file: Path | None = None,
    output_file: Path | None = None,
) -> dict[str, Any]:
    """Return typed, review-only actions from existing reference artifacts.

    ``reference_join_audit_file`` is the required estimator artifact.  The
    other inputs are optional evidence layers.  Supplying a missing, malformed,
    or incompatible optional artifact blocks the complete projection rather
    than silently omitting one action family.  Omitting an optional layer is
    recorded as ``not_supplied`` and never causes evidence to be inferred.

    The function only writes ``output_file`` when explicitly requested.  It
    never writes or normalizes a SUMO network and it never authorizes teacher
    actions for another city.
    """

    specifications = [
        ("reference_join_audit", Path(reference_join_audit_file), True, _validate_reference_join_audit),
        (
            "join_action_contracts_v1",
            Path(join_action_contracts_file) if join_action_contracts_file is not None else None,
            False,
            _validate_join_action_contracts,
        ),
        (
            "reference_hierarchy_audit",
            Path(reference_hierarchy_audit_file) if reference_hierarchy_audit_file is not None else None,
            False,
            _validate_reference_hierarchy_audit,
        ),
        (
            "reference_scope_audit",
            Path(reference_scope_audit_file) if reference_scope_audit_file is not None else None,
            False,
            _validate_reference_scope_audit,
        ),
    ]
    loaded: dict[str, dict[str, Any]] = {}
    artifacts: dict[str, dict[str, Any]] = {}
    blockers: list[str] = []
    for role, path, required, validator in specifications:
        record, document, error = _load_artifact(role=role, path=path, required=required, validator=validator)
        artifacts[role] = record
        if error:
            blockers.append(error)
        elif document is not None:
            loaded[role] = document

    if blockers:
        report = _blocked_report(artifacts=artifacts, blockers=blockers)
        if output_file is not None:
            write_json_atomic(Path(output_file), report, sort_keys=True)
        return report

    join_audit = loaded["reference_join_audit"]
    actions: list[dict[str, Any]] = []
    actions.extend(_project_movement_gap_actions(join_audit, artifacts["reference_join_audit"]))
    actions.extend(_project_tls_actions(join_audit, artifacts["reference_join_audit"]))
    actions.extend(_project_junction_pattern_actions(join_audit, artifacts["reference_join_audit"]))
    actions.extend(_project_global_pedestrian_action(join_audit, artifacts["reference_join_audit"]))

    join_contracts = loaded.get("join_action_contracts_v1")
    if join_contracts is not None:
        actions.extend(_project_join_actions(join_contracts, artifacts["join_action_contracts_v1"]))
    hierarchy = loaded.get("reference_hierarchy_audit")
    if hierarchy is not None:
        actions.extend(_project_edge_type_actions(hierarchy, artifacts["reference_hierarchy_audit"]))
    scope = loaded.get("reference_scope_audit")
    if scope is not None:
        actions.extend(_project_scope_pruning_actions(scope, artifacts["reference_scope_audit"]))

    actions = sorted(actions, key=lambda action: (str(action["action_type"]), str(action["action_id"])))
    type_counts = dict(sorted(Counter(str(action["action_type"]) for action in actions).items()))
    report = {
        "schema": REFERENCE_TEACHER_ACTION_CONTRACTS_V2_SCHEMA,
        "status": "pass",
        "claim_status": "diagnostic-demo",
        "projection_mode": "read_only",
        "projection_policy": (
            "project only findings already present in supplied audit artifacts; do not inspect or mutate a network"
        ),
        "promotion_gate_status": "blocked",
        "promotion_gate_reason": (
            "a human-cleaned reference is estimator evidence only; every target-city edit requires independent evidence"
        ),
        "input_artifacts": artifacts,
        "action_count": len(actions),
        "action_type_counts": type_counts,
        "actions": actions,
        "blockers": [],
    }
    if output_file is not None:
        write_json_atomic(Path(output_file), report, sort_keys=True)
    return report


def _load_artifact(
    *,
    role: str,
    path: Path | None,
    required: bool,
    validator: Any,
) -> tuple[dict[str, Any], dict[str, Any] | None, str]:
    if path is None:
        if required:
            return {"status": "missing", "path": "", "sha256": ""}, None, f"{role} is required"
        return {"status": "not_supplied", "path": "", "sha256": ""}, None, ""
    resolved = path.resolve()
    if not resolved.is_file():
        return (
            {"status": "missing", "path": str(resolved), "sha256": ""},
            None,
            f"{role} does not exist: {resolved}",
        )
    sha256 = file_sha256(resolved)
    record = {"status": "supplied", "path": str(resolved), "sha256": sha256}
    try:
        document = json.loads(resolved.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return record, None, f"{role} is not valid JSON: {type(exc).__name__}: {exc}"
    if not isinstance(document, dict):
        return record, None, f"{role} JSON root must be an object"
    validation_error = validator(document)
    if validation_error:
        return record, None, f"{role} is incompatible: {validation_error}"
    return record, document, ""


def _validate_reference_join_audit(document: Mapping[str, Any]) -> str:
    if document.get("status") != "pass":
        return "status is not pass"
    for key in ("tls_controller_alignment", "network_structural_missing_counts", "network_structural_extra_counts"):
        if key in document and not isinstance(document[key], dict):
            return f"{key} must be an object"
    for key in ("tls_control_review_queue", "junction_pattern_comparisons"):
        if key in document and not isinstance(document[key], list):
            return f"{key} must be an array"
        if isinstance(document.get(key), list) and any(not isinstance(item, dict) for item in document[key]):
            return f"{key} entries must be objects"
    alignment = document.get("tls_controller_alignment", {})
    if isinstance(alignment, dict):
        for key in ("high_confidence_movement_gap_queue", "controller_groups"):
            if key in alignment and not isinstance(alignment[key], list):
                return f"tls_controller_alignment.{key} must be an array"
            if isinstance(alignment.get(key), list) and any(
                not isinstance(item, dict) for item in alignment[key]
            ):
                return f"tls_controller_alignment.{key} entries must be objects"
    return ""


def _validate_join_action_contracts(document: Mapping[str, Any]) -> str:
    if document.get("schema") != "torii.reference_teacher_action_contracts.v1":
        return "schema is not torii.reference_teacher_action_contracts.v1"
    if document.get("status") != "pass":
        return "status is not pass"
    if not isinstance(document.get("actions"), list):
        return "actions must be an array"
    if any(not isinstance(item, dict) for item in document["actions"]):
        return "actions entries must be objects"
    return ""


def _validate_reference_hierarchy_audit(document: Mapping[str, Any]) -> str:
    if document.get("status") not in {"pass", "blocked"}:
        return "status must be pass or blocked"
    if not isinstance(document.get("candidate_cases"), list):
        return "candidate_cases must be an array"
    if any(not isinstance(item, dict) for item in document["candidate_cases"]):
        return "candidate_cases entries must be objects"
    return ""


def _validate_reference_scope_audit(document: Mapping[str, Any]) -> str:
    if document.get("status") not in {"pass", "blocked"}:
        return "status must be pass or blocked"
    if not isinstance(document.get("prune_candidates"), list):
        return "prune_candidates must be an array"
    if any(not isinstance(item, dict) for item in document["prune_candidates"]):
        return "prune_candidates entries must be objects"
    return ""


def _project_movement_gap_actions(
    audit: Mapping[str, Any], artifact: Mapping[str, Any]
) -> list[dict[str, Any]]:
    alignment = audit.get("tls_controller_alignment", {})
    queue = alignment.get("high_confidence_movement_gap_queue", []) if isinstance(alignment, dict) else []
    actions = []
    for index, finding in enumerate(queue):
        if not isinstance(finding, dict):
            continue
        actions.append(
            _action(
                action_type="movement_gap_review",
                artifact=artifact,
                evidence_field=f"/tls_controller_alignment/high_confidence_movement_gap_queue/{index}",
                target_scope={
                    "candidate_tl_id": str(finding.get("candidate_tl_id", "")),
                    "candidate_edge_id": str(finding.get("candidate_edge_id", "")),
                    "reference_tl_id": str(finding.get("reference_tl_id", "")),
                    "reference_edge_id": str(finding.get("reference_edge_id", "")),
                },
                expected_delta={
                    "missing_direction_counts": _json_value(finding.get("missing_direction_counts", {})),
                    "missing_direction_instance_count": int(
                        finding.get("missing_direction_instance_count", 0) or 0
                    ),
                },
                requirements=[
                    "the target MAP or lane-marking evidence must prove every missing direction",
                    "each destination edge and fromLane-toLane movement must map uniquely",
                    "the target conflict, request, and TLS-link semantics must be regenerated from the mapped movements",
                ],
            )
        )
    return actions


def _project_tls_actions(audit: Mapping[str, Any], artifact: Mapping[str, Any]) -> list[dict[str, Any]]:
    actions = []
    queue = audit.get("tls_control_review_queue", [])
    for index, finding in enumerate(queue if isinstance(queue, list) else []):
        if not isinstance(finding, dict):
            continue
        review_type = str(finding.get("review_type", ""))
        repair_category = str(finding.get("repair_category", ""))
        if review_type in _LINKINDEX_REVIEW_TYPES or repair_category == "tls_linkindex_phase_repair":
            action_type = "tls_linkindex_review"
            requirements = [
                "the target signal-group-to-linkIndex mapping and phase-state width must be authoritative",
                "shared linkIndex groups must contain only movements proven to share one target signal state",
                "the target movement conflict matrix must be checked before any phase program is emitted",
            ]
        elif review_type in _CONTROLLER_REVIEW_TYPES or repair_category == "tls_controller_cardinality_repair":
            action_type = "tls_controller_scope_review"
            requirements = [
                "the target controller identity and complete controlled-junction set must be authoritative",
                "controller split or merge must be proven independently from physical junction joining",
                "every target controlled movement must map to one controller and one valid signal group",
            ]
        else:
            continue
        actions.append(
            _action(
                action_type=action_type,
                artifact=artifact,
                evidence_field=f"/tls_control_review_queue/{index}",
                target_scope={
                    "review_type": review_type,
                    "tl_id": str(finding.get("tl_id", "")),
                    "junction_id": str(finding.get("junction_id", "")),
                    "junction_ids": _json_value(finding.get("junction_ids", [])),
                },
                expected_delta=_json_value(finding),
                requirements=requirements,
            )
        )

    alignment = audit.get("tls_controller_alignment", {})
    groups = alignment.get("controller_groups", []) if isinstance(alignment, dict) else []
    for index, group in enumerate(groups):
        if not isinstance(group, dict):
            continue
        reference_count = int(group.get("reference_controller_count", 0) or 0)
        candidate_count = int(group.get("candidate_controller_count", 0) or 0)
        if reference_count == candidate_count:
            continue
        actions.append(
            _action(
                action_type="tls_controller_scope_review",
                artifact=artifact,
                evidence_field=f"/tls_controller_alignment/controller_groups/{index}",
                target_scope={
                    "reference_tl_ids": _json_value(group.get("reference_tl_ids", [])),
                    "candidate_tl_ids": _json_value(group.get("candidate_tl_ids", [])),
                },
                expected_delta={
                    "reference_controller_count": reference_count,
                    "candidate_controller_count": candidate_count,
                    "controlled_connection_delta": int(group.get("controlled_connection_delta", 0) or 0),
                },
                requirements=[
                    "the target controller identity and complete controlled-junction set must be authoritative",
                    "controller grouping must be proven by target signal evidence rather than centroid proximity",
                    "physical junction joining must be evaluated separately from controller grouping",
                ],
            )
        )
    return actions


def _project_junction_pattern_actions(
    audit: Mapping[str, Any], artifact: Mapping[str, Any]
) -> list[dict[str, Any]]:
    actions = []
    comparisons = audit.get("junction_pattern_comparisons", [])
    for index, comparison in enumerate(comparisons if isinstance(comparisons, list) else []):
        if not isinstance(comparison, dict) or comparison.get("status") == "pass":
            continue
        mismatch_fields = [str(value) for value in comparison.get("mismatch_fields", [])]
        actions.append(
            _action(
                action_type="junction_pattern_review",
                artifact=artifact,
                evidence_field=f"/junction_pattern_comparisons/{index}",
                target_scope={"junction_id": str(comparison.get("junction_id", ""))},
                expected_delta={
                    "mismatch_fields": mismatch_fields,
                    "reference": _json_value(comparison.get("teacher", {})),
                    "candidate": _json_value(comparison.get("candidate", {})),
                },
                requirements=[
                    "the target approach arms and their edge lineage must be complete",
                    "target lane-level movements must be mapped before replaying request or TLS semantics",
                    "the target junction type and physical conflict area must be independently classified",
                ],
            )
        )
        if "internal_function_counts" in mismatch_fields:
            teacher = comparison.get("teacher", {})
            candidate = comparison.get("candidate", {})
            actions.append(
                _action(
                    action_type="pedestrian_internal_delta_review",
                    artifact=artifact,
                    evidence_field=f"/junction_pattern_comparisons/{index}",
                    target_scope={"junction_id": str(comparison.get("junction_id", ""))},
                    expected_delta={
                        "reference_internal_function_counts": _json_value(
                            teacher.get("internal_function_counts", {}) if isinstance(teacher, dict) else {}
                        ),
                        "candidate_internal_function_counts": _json_value(
                            candidate.get("internal_function_counts", {}) if isinstance(candidate, dict) else {}
                        ),
                    },
                    requirements=[
                        "current target crossing, kerb, island, and pedestrian-signal evidence must be present",
                        "every crossing must map to explicit crossed target edges",
                        "pedestrian, bicycle, and vehicle modal connectivity must remain non-regressing",
                    ],
                )
            )
    return actions


def _project_global_pedestrian_action(
    audit: Mapping[str, Any], artifact: Mapping[str, Any]
) -> list[dict[str, Any]]:
    missing = audit.get("network_structural_missing_counts", {})
    extra = audit.get("network_structural_extra_counts", {})
    if not isinstance(missing, dict) or not isinstance(extra, dict):
        return []
    keys = ("crossing_edge_count", "walkingarea_edge_count")
    missing_pedestrian = {key: int(missing.get(key, 0) or 0) for key in keys if int(missing.get(key, 0) or 0)}
    extra_pedestrian = {key: int(extra.get(key, 0) or 0) for key in keys if int(extra.get(key, 0) or 0)}
    if not missing_pedestrian and not extra_pedestrian:
        return []
    return [
        _action(
            action_type="pedestrian_internal_delta_review",
            artifact=artifact,
            evidence_field="/network_structural_missing_counts",
            target_scope={"scope": "same_bbox_network"},
            expected_delta={
                "missing_reference_pedestrian_functions": missing_pedestrian,
                "extra_candidate_pedestrian_functions": extra_pedestrian,
            },
            requirements=[
                "local target junction ownership must be established before a global count delta is localized",
                "current target crossing, kerb, island, and pedestrian-signal evidence must be present",
                "modal connectivity and non-target junction semantics must remain unchanged",
            ],
        )
    ]


def _project_join_actions(
    contracts: Mapping[str, Any], artifact: Mapping[str, Any]
) -> list[dict[str, Any]]:
    actions = []
    for index, finding in enumerate(contracts.get("actions", [])):
        if not isinstance(finding, dict):
            continue
        teacher_action = finding.get("teacher_action", {})
        if not isinstance(teacher_action, dict):
            teacher_action = {}
        actions.append(
            _action(
                action_type="junction_join_review",
                artifact=artifact,
                evidence_field=f"/actions/{index}",
                target_scope={
                    "reference_id": str(finding.get("reference_id", "")),
                    "action_family": str(finding.get("action_family", "")),
                    "absorbed_source_node_ids": _json_value(
                        teacher_action.get("absorbed_source_node_ids", [])
                    ),
                    "retained_boundary_edge_ids": _json_value(
                        teacher_action.get("retained_boundary_edge_ids", [])
                    ),
                },
                expected_delta={
                    "absorbed_internal_edge_ids": _json_value(
                        teacher_action.get("absorbed_internal_edge_ids", [])
                    ),
                    "reference_approach_edge_ids": _json_value(
                        teacher_action.get("reference_approach_edge_ids", [])
                    ),
                },
                requirements=[
                    "independent target geometry must prove one physical conflict core",
                    "target movement and retained-boundary evidence must authorize every absorbed node and edge",
                    "surviving approach lane geometry and length must be preserved or explicitly bounded by a splice contract",
                    "shared TLS ownership must not be used as proof that physical junctions should be joined",
                ],
            )
        )
    return actions


def _project_edge_type_actions(
    audit: Mapping[str, Any], artifact: Mapping[str, Any]
) -> list[dict[str, Any]]:
    actions = []
    for index, finding in enumerate(audit.get("candidate_cases", [])):
        if not isinstance(finding, dict) or finding.get("hierarchy_decision") == "aligned":
            continue
        actions.append(
            _action(
                action_type="edge_type_review",
                artifact=artifact,
                evidence_field=f"/candidate_cases/{index}",
                target_scope={"candidate_edge_id": str(finding.get("candidate_edge_id", ""))},
                expected_delta={
                    "candidate_edge_type": str(finding.get("candidate_edge_type", "")),
                    "same_id_reference_edge_type": str(finding.get("same_id_reference_edge_type", "")),
                    "nearest_reference_edge_type": str(finding.get("nearest_any_reference_edge_type", "")),
                    "hierarchy_decision": str(finding.get("hierarchy_decision", "")),
                    "recommended_action": str(finding.get("recommended_action", "")),
                },
                requirements=[
                    "current target road class, direction, access, and functional hierarchy must be authoritative",
                    "same-edge identity or an independently reviewed target corridor mapping must be available",
                    "type changes must not silently change lane permissions, speed, or routing priority",
                ],
            )
        )
    return actions


def _project_scope_pruning_actions(
    audit: Mapping[str, Any], artifact: Mapping[str, Any]
) -> list[dict[str, Any]]:
    actions = []
    for index, finding in enumerate(audit.get("prune_candidates", [])):
        if not isinstance(finding, dict):
            continue
        actions.append(
            _action(
                action_type="scope_pruning_review",
                artifact=artifact,
                evidence_field=f"/prune_candidates/{index}",
                target_scope={"candidate_edge_id": str(finding.get("edge_id", ""))},
                expected_delta={
                    "operation": "delete_edge_review_only",
                    "edge_type": str(finding.get("edge_type", "")),
                    "scope_decision": str(finding.get("scope_decision", "")),
                    "length_m": finding.get("length_m"),
                },
                requirements=[
                    "current target evidence must prove the edge absent or outside the declared corridor scope",
                    "the edge must carry no target TLS-controlled movement",
                    "vehicle, pedestrian, bicycle, bridge, tunnel, rail, and route connectivity must remain non-regressing",
                ],
            )
        )
    return actions


def _action(
    *,
    action_type: str,
    artifact: Mapping[str, Any],
    evidence_field: str,
    target_scope: Mapping[str, Any],
    expected_delta: Any,
    requirements: list[str],
) -> dict[str, Any]:
    identity = {
        "action_type": action_type,
        "input_sha256": str(artifact.get("sha256", "")),
        "evidence_field": evidence_field,
        "target_scope": _json_value(target_scope),
        "expected_delta": _json_value(expected_delta),
    }
    digest = hashlib.sha256(
        json.dumps(identity, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")
    ).hexdigest()[:20]
    return {
        "action_id": f"teacher-v2-{digest}",
        "action_type": action_type,
        "status": "review_required",
        "input_artifact": {
            "path": str(artifact.get("path", "")),
            "sha256": str(artifact.get("sha256", "")),
        },
        "evidence_field": evidence_field,
        "target_scope": _json_value(target_scope),
        "expected_delta": _json_value(expected_delta),
        "materialization_status": "not_authorized",
        "transfer_gate_status": "blocked",
        "target_city_requirements": [*_COMMON_TARGET_REQUIREMENTS, *requirements],
    }


def _blocked_report(*, artifacts: Mapping[str, Any], blockers: list[str]) -> dict[str, Any]:
    return {
        "schema": REFERENCE_TEACHER_ACTION_CONTRACTS_V2_SCHEMA,
        "status": "blocked",
        "claim_status": "blocked",
        "projection_mode": "read_only",
        "promotion_gate_status": "blocked",
        "promotion_gate_reason": "one or more supplied evidence artifacts failed validation",
        "input_artifacts": dict(artifacts),
        "action_count": 0,
        "action_type_counts": {},
        "actions": [],
        "blockers": blockers,
    }


def _json_value(value: Any) -> Any:
    """Return a deterministic JSON-compatible copy of an artifact value."""

    return json.loads(json.dumps(value, ensure_ascii=False, sort_keys=True, default=str))
