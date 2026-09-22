from __future__ import annotations

import hashlib
from datetime import datetime
from pathlib import Path

from pydantic import model_validator

from .base import ContractModel, Sha256, StableToken
from .candidates import CandidateGraph, Hypothesis
from .calibration import ConnectionAuditCalibration
from .canonicalizer import CanonicalNetworkSnapshot
from .conflict_graph import IndependentSafetyReport
from .enums import ArtifactRole, GateStatus
from .evidence import EvidenceRecord, Finding, InvariantResult
from .exact_diff import ExactSemanticDiffReport
from .ids import require_stable_id, stable_id
from .review import ReviewCase, ReviewDecision, ReviewTask
from .scope import ScopeSpec
from .toolchain import ToolchainLock
from .workflow import NetworkQualityVectorV1, StageOutcome, WorkflowExecution


class ArtifactIdentity(ContractModel):
    artifact_schema: str
    artifact_id: StableToken
    logical_name: str
    role: ArtifactRole
    path: str
    sha256: Sha256
    producer: str
    toolchain_id: StableToken

    @classmethod
    def from_file(
        cls,
        path: Path,
        *,
        logical_name: str,
        role: ArtifactRole,
        artifact_schema: str,
        producer: str,
        toolchain_id: str,
    ) -> ArtifactIdentity:
        """Build a stable identity from an artifact that already exists."""

        resolved = Path(path).expanduser().resolve(strict=True)
        sha256 = hashlib.sha256(resolved.read_bytes()).hexdigest()
        return cls(
            artifact_schema=artifact_schema,
            artifact_id=stable_id(
                "artifact",
                {
                    "logical_name": logical_name,
                    "role": role.value,
                    "sha256": sha256,
                    "artifact_schema": artifact_schema,
                },
            ),
            logical_name=logical_name,
            role=role,
            path=str(resolved),
            sha256=sha256,
            producer=producer,
            toolchain_id=toolchain_id,
        )

    @model_validator(mode="after")
    def validate_artifact(self) -> ArtifactIdentity:
        require_stable_id(self.artifact_id, kind="artifact")
        require_stable_id(self.toolchain_id, kind="toolchain")
        if not self.logical_name:
            raise ValueError("Artifacts require a logical_name.")
        if not self.path:
            raise ValueError("Artifacts require a path.")
        return self


class ArtifactDependency(ContractModel):
    parent_artifact_id: StableToken
    child_artifact_id: StableToken
    relation: str

    @model_validator(mode="after")
    def validate_dependency(self) -> ArtifactDependency:
        require_stable_id(self.parent_artifact_id, kind="artifact")
        require_stable_id(self.child_artifact_id, kind="artifact")
        if self.parent_artifact_id == self.child_artifact_id:
            raise ValueError("Artifact dependencies cannot be self-referential.")
        return self


class ArtifactManifestV1(ContractModel):
    schema_id: str = "torii.corridor.artifact-manifest/v1"
    manifest_id: StableToken
    created_at: datetime
    toolchain_id: StableToken
    source_artifact_id: StableToken
    candidate_artifact_ids: tuple[StableToken, ...] = ()
    artifacts: tuple[ArtifactIdentity, ...]
    dependencies: tuple[ArtifactDependency, ...] = ()
    gate_trace: dict[str, GateStatus]
    source_mutated: bool = False

    @model_validator(mode="after")
    def validate_manifest(self) -> ArtifactManifestV1:
        require_stable_id(self.manifest_id, kind="manifest")
        require_stable_id(self.toolchain_id, kind="toolchain")
        require_stable_id(self.source_artifact_id, kind="artifact")
        if self.created_at.tzinfo is None:
            raise ValueError("Artifact manifests require a timezone-aware created_at.")
        artifacts_by_id = {artifact.artifact_id: artifact for artifact in self.artifacts}
        if len(artifacts_by_id) != len(self.artifacts):
            raise ValueError("Artifact IDs must be unique.")
        if len({artifact.path for artifact in self.artifacts}) != len(self.artifacts):
            raise ValueError("Artifact paths must be unique.")
        if self.source_artifact_id not in artifacts_by_id:
            raise ValueError("Manifest source artifact is missing.")
        source = artifacts_by_id[self.source_artifact_id]
        if source.role is not ArtifactRole.SOURCE_NET:
            raise ValueError("Manifest source_artifact_id must reference a source-net.")
        for candidate_id in self.candidate_artifact_ids:
            require_stable_id(candidate_id, kind="artifact")
            candidate = artifacts_by_id.get(candidate_id)
            if candidate is None or candidate.role is not ArtifactRole.CANDIDATE_NET:
                raise ValueError("Candidate artifact references must resolve to candidate-net artifacts.")
            if candidate.path == source.path or candidate.sha256 == source.sha256:
                raise ValueError("Source and candidate artifacts must have distinct paths and hashes.")
        for dependency in self.dependencies:
            if (
                dependency.parent_artifact_id not in artifacts_by_id
                or dependency.child_artifact_id not in artifacts_by_id
            ):
                raise ValueError("Artifact dependency references are not closed.")
        if self.source_mutated:
            raise ValueError("A corridor research manifest can never certify source mutation.")
        return self

    def assert_artifact_files_unchanged(self) -> None:
        """Fail closed when a manifest path is missing or its bytes changed."""

        for artifact in self.artifacts:
            resolved = Path(artifact.path).expanduser().resolve(strict=True)
            actual_sha256 = hashlib.sha256(resolved.read_bytes()).hexdigest()
            if actual_sha256 != artifact.sha256:
                raise ValueError(
                    "Artifact bytes no longer match the manifest: "
                    f"{artifact.logical_name} ({resolved})."
                )


class CorridorResearchBundle(ContractModel):
    """Aggregate schema root for every frozen stage-0 contract."""

    schema_id: str = "torii.corridor.research-bundle/v1"
    toolchain: ToolchainLock
    scope: ScopeSpec
    canonical_source: CanonicalNetworkSnapshot | None = None
    canonical_candidate: CanonicalNetworkSnapshot | None = None
    connection_audit_calibration: ConnectionAuditCalibration | None = None
    exact_semantic_diff: ExactSemanticDiffReport | None = None
    independent_safety: IndependentSafetyReport | None = None
    evidence: tuple[EvidenceRecord, ...] = ()
    findings: tuple[Finding, ...] = ()
    hypotheses: tuple[Hypothesis, ...] = ()
    candidates: CandidateGraph
    invariants: tuple[InvariantResult, ...] = ()
    quality: NetworkQualityVectorV1
    stage_outcomes: tuple[StageOutcome, ...] = ()
    workflow: WorkflowExecution
    review_tasks: tuple[ReviewTask, ...] = ()
    review_cases: tuple[ReviewCase, ...] = ()
    review_decisions: tuple[ReviewDecision, ...] = ()
    artifact_manifest: ArtifactManifestV1
