from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import Field, model_validator

from .base import ContractModel, Sha256, StableToken
from .enums import (
    EvidenceReliability,
    EvidenceSourceType,
    FindingSeverity,
    GateStatus,
)
from .ids import require_stable_id


class EvidenceRecord(ContractModel):
    evidence_id: StableToken
    source_type: EvidenceSourceType
    provider: str
    observed_at: datetime
    reviewed_at: datetime | None = None
    spatial_scope: str
    license: str
    content_sha256: Sha256 | None = None
    reliability: EvidenceReliability
    observed_fact: str
    reproducible: bool
    artifact_refs: tuple[StableToken, ...] = ()

    @model_validator(mode="after")
    def validate_evidence(self) -> EvidenceRecord:
        require_stable_id(self.evidence_id, kind="evidence")
        if self.observed_at.tzinfo is None:
            raise ValueError("Evidence timestamps must include a timezone.")
        if self.reviewed_at is not None and self.reviewed_at.tzinfo is None:
            raise ValueError("Evidence review timestamps must include a timezone.")
        if self.source_type is EvidenceSourceType.HUMAN_MAP_OBSERVATION and not self.reviewed_at:
            raise ValueError("Human map observations require reviewed_at.")
        for artifact_ref in self.artifact_refs:
            require_stable_id(artifact_ref, kind="artifact")
        return self


class Finding(ContractModel):
    finding_id: StableToken
    category: str
    severity: FindingSeverity
    subject_id: StableToken
    witness: dict[str, Any]
    witness_signature: StableToken
    confidence: float = Field(ge=0.0, le=1.0)
    evidence_refs: tuple[StableToken, ...] = ()

    @model_validator(mode="after")
    def validate_finding(self) -> Finding:
        require_stable_id(self.finding_id, kind="finding")
        require_stable_id(self.subject_id)
        require_stable_id(self.witness_signature, kind="signature")
        for evidence_ref in self.evidence_refs:
            require_stable_id(evidence_ref, kind="evidence")
        if not self.witness:
            raise ValueError("Findings require a machine-readable witness.")
        return self


class InvariantResult(ContractModel):
    invariant_id: StableToken
    rule_id: str
    subject_id: StableToken
    status: GateStatus
    hard_gate: bool
    witness: dict[str, Any]
    evidence_refs: tuple[StableToken, ...] = ()

    @model_validator(mode="after")
    def validate_invariant(self) -> InvariantResult:
        require_stable_id(self.invariant_id, kind="invariant")
        require_stable_id(self.subject_id)
        for evidence_ref in self.evidence_refs:
            require_stable_id(evidence_ref, kind="evidence")
        if self.hard_gate and self.status is GateStatus.REVIEW:
            raise ValueError("Hard gates cannot be downgraded to review.")
        return self
