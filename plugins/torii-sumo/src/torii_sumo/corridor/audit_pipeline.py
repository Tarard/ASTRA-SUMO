from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

from torii_sumo.core.artifact_io import write_json_atomic
from torii_sumo.core.candidate_contracts import file_sha256
from torii_sumo.core.connection_mode_audit import audit_network_connection_mode

from .audit_adapter import (
    build_scope_from_junction_ids,
    canonicalize_connection_mode_findings,
)
from .calibration import ConnectionAuditCalibration
from .canonicalizer import canonicalize_net_xml_file
from .conflict_graph import audit_independent_movement_safety
from .enums import ArtifactRole, GateStatus, TrafficSide
from .exact_diff import compare_canonical_snapshots
from .ids import stable_id
from .manifest import (
    ArtifactDependency,
    ArtifactIdentity,
    ArtifactManifestV1,
)
from .toolchain import ToolchainLock


def build_exact_semantic_regression_artifacts(
    source_net_file: Path,
    candidate_net_file: Path,
    *,
    output_dir: Path,
    toolchain_lock_file: Path,
    traffic_side: TrafficSide,
    target_source_junction_ids: Sequence[str],
    target_candidate_junction_ids: Sequence[str],
    guard_source_junction_ids: Sequence[str] = (),
    guard_candidate_junction_ids: Sequence[str] = (),
    endpoint_tolerance_m: float | None = None,
    normalized_lane_rank_tolerance: float | None = None,
    calibration_file: Path | None = None,
    prefix: str = "exact_semantic_regression",
) -> dict[str, Any]:
    """Run the stage-1 read-only audit and emit a hash-closed artifact DAG."""

    source = source_net_file.resolve()
    candidate = candidate_net_file.resolve()
    destination = output_dir.resolve()
    lock_file = toolchain_lock_file.resolve()
    if source == candidate:
        raise ValueError("Source and candidate paths must be distinct.")
    source_sha256 = file_sha256(source)
    candidate_sha256 = file_sha256(candidate)
    if source_sha256 == candidate_sha256:
        raise ValueError("Source and candidate contents must be distinct.")
    if traffic_side is TrafficSide.UNKNOWN:
        raise ValueError("Exact semantic audit requires an explicit traffic side.")
    calibration: ConnectionAuditCalibration | None = None
    calibration_path: Path | None = None
    if calibration_file is not None:
        if endpoint_tolerance_m is not None:
            raise ValueError(
                "Use either calibration_file or endpoint_tolerance_m, not both."
            )
        calibration_path = calibration_file.resolve()
        calibration = ConnectionAuditCalibration.model_validate_json(
            calibration_path.read_text(encoding="utf-8")
        )
        if calibration.source_sha256 != source_sha256:
            raise ValueError("Calibration is not bound to the source network hash.")
        if calibration.traffic_side is not traffic_side:
            raise ValueError("Calibration traffic side does not match the audit.")
        if (
            calibration.status is not GateStatus.PASS
            or calibration.endpoint_tolerance_m is None
        ):
            raise ValueError("Only a passing calibration may drive a promotion-grade audit.")
        endpoint_tolerance_m = calibration.endpoint_tolerance_m
        calibrated_lane_rank_tolerance = (
            calibration.policy.normalized_lane_rank_tolerance
        )
        if (
            normalized_lane_rank_tolerance is not None
            and normalized_lane_rank_tolerance != calibrated_lane_rank_tolerance
        ):
            raise ValueError("Lane-rank tolerance contradicts the bound calibration.")
        normalized_lane_rank_tolerance = calibrated_lane_rank_tolerance
    if endpoint_tolerance_m is None or normalized_lane_rank_tolerance is None:
        raise ValueError(
            "Exact semantic audit requires either a passing calibration or explicit tolerances."
        )
    if endpoint_tolerance_m <= 0 or not 0 <= normalized_lane_rank_tolerance <= 1:
        raise ValueError("Audit tolerances are outside their valid ranges.")
    toolchain = ToolchainLock.model_validate_json(lock_file.read_text(encoding="utf-8"))
    destination.mkdir(parents=True, exist_ok=True)

    source_root = ET.parse(source).getroot()
    candidate_root = ET.parse(candidate).getroot()
    source_audit = audit_network_connection_mode(
        source_root,
        traffic_side=traffic_side.value,
        endpoint_tolerance_m=endpoint_tolerance_m,
        normalized_lane_rank_tolerance=normalized_lane_rank_tolerance,
    )
    candidate_audit = audit_network_connection_mode(
        candidate_root,
        traffic_side=traffic_side.value,
        endpoint_tolerance_m=endpoint_tolerance_m,
        normalized_lane_rank_tolerance=normalized_lane_rank_tolerance,
    )
    source_snapshot = canonicalize_net_xml_file(source, traffic_side=traffic_side)
    candidate_snapshot = canonicalize_net_xml_file(candidate, traffic_side=traffic_side)
    scope = build_scope_from_junction_ids(
        source_snapshot,
        candidate_snapshot,
        target_source_junction_ids=target_source_junction_ids,
        target_candidate_junction_ids=target_candidate_junction_ids,
        guard_source_junction_ids=guard_source_junction_ids,
        guard_candidate_junction_ids=guard_candidate_junction_ids,
    )
    source_safety = audit_independent_movement_safety(source_snapshot)
    candidate_safety = audit_independent_movement_safety(candidate_snapshot)
    source_findings = (
        *canonicalize_connection_mode_findings(
            source_audit,
            source_snapshot,
        ),
        *source_safety.findings,
    )
    candidate_findings = (
        *canonicalize_connection_mode_findings(
            candidate_audit,
            candidate_snapshot,
        ),
        *candidate_safety.findings,
    )
    exact_diff = compare_canonical_snapshots(
        source_snapshot,
        candidate_snapshot,
        scope=scope,
        source_findings=source_findings,
        candidate_findings=candidate_findings,
    )

    paths = {
        "source_connection_audit": destination / f"{prefix}.source.connection-mode.json",
        "candidate_connection_audit": destination / f"{prefix}.candidate.connection-mode.json",
        "source_snapshot": destination / f"{prefix}.source.canonical.json",
        "candidate_snapshot": destination / f"{prefix}.candidate.canonical.json",
        "source_safety": destination / f"{prefix}.source.independent-safety.json",
        "candidate_safety": destination / f"{prefix}.candidate.independent-safety.json",
        "scope": destination / f"{prefix}.scope.json",
        "exact_diff": destination / f"{prefix}.exact-diff.json",
        "manifest": destination / f"{prefix}.manifest.json",
    }
    write_json_atomic(paths["source_connection_audit"], source_audit, sort_keys=True)
    write_json_atomic(paths["candidate_connection_audit"], candidate_audit, sort_keys=True)
    write_json_atomic(
        paths["source_snapshot"],
        source_snapshot.model_dump(mode="json", by_alias=True),
        sort_keys=True,
    )
    write_json_atomic(
        paths["candidate_snapshot"],
        candidate_snapshot.model_dump(mode="json", by_alias=True),
        sort_keys=True,
    )
    write_json_atomic(
        paths["source_safety"],
        source_safety.model_dump(mode="json", by_alias=True),
        sort_keys=True,
    )
    write_json_atomic(
        paths["candidate_safety"],
        candidate_safety.model_dump(mode="json", by_alias=True),
        sort_keys=True,
    )
    write_json_atomic(
        paths["scope"],
        scope.model_dump(mode="json", by_alias=True),
        sort_keys=True,
    )
    write_json_atomic(
        paths["exact_diff"],
        exact_diff.model_dump(mode="json", by_alias=True),
        sort_keys=True,
    )
    if file_sha256(source) != source_sha256:
        raise RuntimeError("Source network changed while the read-only audit was running.")

    source_status = _legacy_status(source_audit.get("status"))
    candidate_status = _legacy_status(candidate_audit.get("status"))
    gate_trace = {
        "artifact_identity": GateStatus.PASS,
        "source_immutable": GateStatus.PASS,
        "source_connection_mode": source_status,
        "candidate_connection_mode": candidate_status,
        "source_independent_safety": source_safety.status,
        "candidate_independent_safety": candidate_safety.status,
        "exact_semantic_diff": exact_diff.status,
    }
    if calibration is not None:
        gate_trace["connection_audit_calibration"] = calibration.status
    artifacts = [
        _artifact(
            source,
            logical_name="source_net",
            role=ArtifactRole.SOURCE_NET,
            artifact_schema="sumo.net.xml",
            producer="locked-netconvert-input",
            toolchain_id=toolchain.toolchain_id,
        ),
        _artifact(
            candidate,
            logical_name="candidate_net",
            role=ArtifactRole.CANDIDATE_NET,
            artifact_schema="sumo.net.xml",
            producer="torii-candidate-materializer",
            toolchain_id=toolchain.toolchain_id,
        ),
        _artifact(
            lock_file,
            logical_name="toolchain_lock",
            role=ArtifactRole.TOOLCHAIN_LOCK,
            artifact_schema="torii.corridor.contracts/v1",
            producer="stage-0-freeze",
            toolchain_id=toolchain.toolchain_id,
        ),
    ]
    if calibration_path is not None:
        artifacts.append(
            _artifact(
                calibration_path,
                logical_name="connection_audit_calibration",
                role=ArtifactRole.PLAN,
                artifact_schema="torii.corridor.connection-audit-calibration/v1",
                producer="torii-stage-1-calibration",
                toolchain_id=toolchain.toolchain_id,
            )
        )
    report_specs = (
        ("source_connection_audit", "torii.network_connection_mode_audit.v1"),
        ("candidate_connection_audit", "torii.network_connection_mode_audit.v1"),
        ("source_snapshot", "torii.corridor.canonical-network/v1"),
        ("candidate_snapshot", "torii.corridor.canonical-network/v1"),
        ("source_safety", "torii.corridor.independent-safety-audit/v1"),
        ("candidate_safety", "torii.corridor.independent-safety-audit/v1"),
        ("scope", "torii.corridor.contracts/v1"),
        ("exact_diff", "torii.corridor.exact-semantic-diff/v1"),
    )
    for path_key, artifact_schema in report_specs:
        artifacts.append(
            _artifact(
                paths[path_key],
                logical_name=path_key,
                role=ArtifactRole.REPORT if path_key != "scope" else ArtifactRole.PLAN,
                artifact_schema=artifact_schema,
                producer="torii-stage-1-audit",
                toolchain_id=toolchain.toolchain_id,
            )
        )
    by_path = {Path(artifact.path): artifact for artifact in artifacts}
    source_artifact = by_path[source]
    candidate_artifact = by_path[candidate]
    dependency_list = [
        ArtifactDependency(
            parent_artifact_id=source_artifact.artifact_id,
            child_artifact_id=by_path[paths["source_connection_audit"]].artifact_id,
            relation="audited-as-source",
        ),
        ArtifactDependency(
            parent_artifact_id=candidate_artifact.artifact_id,
            child_artifact_id=by_path[paths["candidate_connection_audit"]].artifact_id,
            relation="audited-as-candidate",
        ),
        ArtifactDependency(
            parent_artifact_id=source_artifact.artifact_id,
            child_artifact_id=by_path[paths["source_snapshot"]].artifact_id,
            relation="canonicalized-from",
        ),
        ArtifactDependency(
            parent_artifact_id=candidate_artifact.artifact_id,
            child_artifact_id=by_path[paths["candidate_snapshot"]].artifact_id,
            relation="canonicalized-from",
        ),
        ArtifactDependency(
            parent_artifact_id=by_path[paths["source_snapshot"]].artifact_id,
            child_artifact_id=by_path[paths["exact_diff"]].artifact_id,
            relation="exact-diff-source",
        ),
        ArtifactDependency(
            parent_artifact_id=by_path[paths["candidate_snapshot"]].artifact_id,
            child_artifact_id=by_path[paths["exact_diff"]].artifact_id,
            relation="exact-diff-candidate",
        ),
        ArtifactDependency(
            parent_artifact_id=by_path[paths["scope"]].artifact_id,
            child_artifact_id=by_path[paths["exact_diff"]].artifact_id,
            relation="scope-contract",
        ),
        ArtifactDependency(
            parent_artifact_id=by_path[paths["source_snapshot"]].artifact_id,
            child_artifact_id=by_path[paths["source_safety"]].artifact_id,
            relation="independent-safety-source",
        ),
        ArtifactDependency(
            parent_artifact_id=by_path[paths["candidate_snapshot"]].artifact_id,
            child_artifact_id=by_path[paths["candidate_safety"]].artifact_id,
            relation="independent-safety-candidate",
        ),
        ArtifactDependency(
            parent_artifact_id=by_path[paths["source_safety"]].artifact_id,
            child_artifact_id=by_path[paths["exact_diff"]].artifact_id,
            relation="finding-diff-source",
        ),
        ArtifactDependency(
            parent_artifact_id=by_path[paths["candidate_safety"]].artifact_id,
            child_artifact_id=by_path[paths["exact_diff"]].artifact_id,
            relation="finding-diff-candidate",
        ),
    ]
    if calibration_path is not None:
        calibration_artifact = by_path[calibration_path]
        dependency_list.extend(
            (
                ArtifactDependency(
                    parent_artifact_id=calibration_artifact.artifact_id,
                    child_artifact_id=by_path[paths["source_connection_audit"]].artifact_id,
                    relation="calibrates-source-audit",
                ),
                ArtifactDependency(
                    parent_artifact_id=calibration_artifact.artifact_id,
                    child_artifact_id=by_path[paths["candidate_connection_audit"]].artifact_id,
                    relation="calibrates-candidate-audit",
                ),
            )
        )
    dependencies = tuple(dependency_list)
    manifest = ArtifactManifestV1(
        manifest_id=stable_id(
            "manifest",
            {
                "source_sha256": source_sha256,
                "candidate_sha256": candidate_sha256,
                "scope_id": scope.scope_id,
                "toolchain_id": toolchain.toolchain_id,
                "artifacts": sorted(artifact.artifact_id for artifact in artifacts),
            },
        ),
        created_at=datetime.now(timezone.utc),
        toolchain_id=toolchain.toolchain_id,
        source_artifact_id=source_artifact.artifact_id,
        candidate_artifact_ids=(candidate_artifact.artifact_id,),
        artifacts=tuple(artifacts),
        dependencies=dependencies,
        gate_trace=gate_trace,
        source_mutated=False,
    )
    write_json_atomic(
        paths["manifest"],
        manifest.model_dump(mode="json", by_alias=True),
        sort_keys=True,
    )
    overall_status = _overall_status(gate_trace)
    return {
        "schema": "torii.corridor.stage-1-audit/v1",
        "status": overall_status.value,
        "automatic_promotion_gate": (
            "pass" if overall_status is GateStatus.PASS else "blocked"
        ),
        "source_sha256": source_sha256,
        "candidate_sha256": candidate_sha256,
        "source_network_mutation": False,
        "scope_id": scope.scope_id,
        "entity_delta_count": len(exact_diff.entity_deltas),
        "new_finding_count": len(exact_diff.finding_delta.added),
        "resolved_finding_count": len(exact_diff.finding_delta.resolved),
        "candidate_conflict_count": len(candidate_safety.conflict_graph.conflicts),
        "candidate_safety_finding_count": len(candidate_safety.findings),
        "tolerance_provenance": (
            "hash_bound_calibration" if calibration is not None else "explicit_parameters"
        ),
        "endpoint_tolerance_m": endpoint_tolerance_m,
        "normalized_lane_rank_tolerance": normalized_lane_rank_tolerance,
        "files": {
            **{key: str(path) for key, path in paths.items()},
            **(
                {"connection_audit_calibration": str(calibration_path)}
                if calibration_path is not None
                else {}
            ),
        },
    }


def _artifact(
    path: Path,
    *,
    logical_name: str,
    role: ArtifactRole,
    artifact_schema: str,
    producer: str,
    toolchain_id: str,
) -> ArtifactIdentity:
    return ArtifactIdentity.from_file(
        path,
        logical_name=logical_name,
        role=role,
        artifact_schema=artifact_schema,
        producer=producer,
        toolchain_id=toolchain_id,
    )


def _legacy_status(value: object) -> GateStatus:
    normalized = str(value or "").lower()
    if normalized == "pass":
        return GateStatus.PASS
    if normalized == "review_required":
        return GateStatus.REVIEW
    if normalized == "fail":
        return GateStatus.FAIL
    return GateStatus.BLOCKED


def _overall_status(gates: dict[str, GateStatus]) -> GateStatus:
    if any(status in {GateStatus.FAIL, GateStatus.BLOCKED} for status in gates.values()):
        return GateStatus.BLOCKED
    if any(status is GateStatus.REVIEW for status in gates.values()):
        return GateStatus.REVIEW
    return GateStatus.PASS
