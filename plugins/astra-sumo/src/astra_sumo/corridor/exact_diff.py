from __future__ import annotations

from collections import Counter
from typing import Any, Iterable

from pydantic import model_validator

from .base import ContractModel, Sha256, StableToken
from .candidates import SemanticDelta
from .canonicalizer import CanonicalEntity, CanonicalNetworkSnapshot
from .enums import (
    DeltaAction,
    FindingSeverity,
    GateStatus,
    ScopeMembership,
)
from .evidence import Finding
from .ids import require_stable_id, stable_id
from .scope import ScopeSpec


class FindingSetDelta(ContractModel):
    added: tuple[Finding, ...] = ()
    resolved: tuple[Finding, ...] = ()
    unchanged_finding_ids: tuple[StableToken, ...] = ()


class ExactSemanticDiffReport(ContractModel):
    schema_id: str = "torii.corridor.exact-semantic-diff/v1"
    scope_id: StableToken
    source_sha256: Sha256 | None = None
    candidate_sha256: Sha256 | None = None
    status: GateStatus
    automatic_promotion_gate: GateStatus
    entity_deltas: tuple[SemanticDelta, ...]
    finding_delta: FindingSetDelta
    counts_by_scope: dict[ScopeMembership, int]
    counts_by_entity_kind: dict[str, int]
    outside_scope_delta_ids: tuple[StableToken, ...]
    outside_scope_added_finding_ids: tuple[StableToken, ...]
    blockers: tuple[str, ...]

    @model_validator(mode="after")
    def validate_gate(self) -> ExactSemanticDiffReport:
        require_stable_id(self.scope_id, kind="scope")
        if (
            self.outside_scope_delta_ids
            or self.outside_scope_added_finding_ids
        ) and self.automatic_promotion_gate is GateStatus.PASS:
            raise ValueError("Outside-scope exact changes cannot pass automatic promotion.")
        return self


def build_finding(
    *,
    category: str,
    severity: FindingSeverity,
    subject_id: str,
    witness: dict[str, Any],
    confidence: float,
    evidence_refs: tuple[str, ...] = (),
) -> Finding:
    require_stable_id(subject_id)
    witness_signature = stable_id(
        "signature",
        {
            "category": category,
            "subject_id": subject_id,
            "witness": witness,
        },
    )
    return Finding(
        finding_id=stable_id(
            "finding",
            {
                "category": category,
                "subject_id": subject_id,
                "witness_signature": witness_signature,
            },
        ),
        category=category,
        severity=severity,
        subject_id=subject_id,
        witness=witness,
        witness_signature=witness_signature,
        confidence=confidence,
        evidence_refs=evidence_refs,
    )


def compare_canonical_snapshots(
    source: CanonicalNetworkSnapshot,
    candidate: CanonicalNetworkSnapshot,
    *,
    scope: ScopeSpec,
    source_findings: Iterable[Finding] = (),
    candidate_findings: Iterable[Finding] = (),
) -> ExactSemanticDiffReport:
    if source.traffic_side is not candidate.traffic_side:
        raise ValueError("Source and candidate traffic-side contracts differ.")
    source_entities = source.entity_index()
    candidate_entities = candidate.entity_index()
    entity_deltas: list[SemanticDelta] = []
    all_keys = sorted(set(source_entities) | set(candidate_entities))
    for key in all_keys:
        before = source_entities.get(key)
        after = candidate_entities.get(key)
        if before is not None and after is not None:
            if before.semantic_signature == after.semantic_signature:
                continue
            action = DeltaAction.MODIFIED
            stable_entity_id = before.stable_entity_id
            before_signature = before.semantic_signature
            after_signature = after.semantic_signature
            membership = _entity_scope(before, scope)
        elif before is not None:
            action = DeltaAction.REMOVED
            stable_entity_id = before.stable_entity_id
            before_signature = before.semantic_signature
            after_signature = None
            membership = _entity_scope(before, scope)
        else:
            assert after is not None
            action = DeltaAction.ADDED
            stable_entity_id = after.stable_entity_id
            before_signature = None
            after_signature = after.semantic_signature
            membership = _entity_scope(after, scope)
        entity_deltas.append(
            SemanticDelta(
                delta_id=stable_id(
                    "delta",
                    {
                        "kind": key[0],
                        "entity_id": stable_entity_id,
                        "action": action,
                        "before": before_signature,
                        "after": after_signature,
                    },
                ),
                entity_kind=key[0],
                stable_entity_id=stable_entity_id,
                action=action,
                before_signature=before_signature,
                after_signature=after_signature,
                reason="exact canonical source/candidate semantic comparison",
                scope_membership=membership,
            )
        )

    finding_delta = compare_finding_sets(source_findings, candidate_findings)
    entity_lookup = {
        entity.stable_entity_id: entity
        for entity in (*source.entities, *candidate.entities)
    }
    outside_added_findings = tuple(
        finding.finding_id
        for finding in finding_delta.added
        if _subject_scope(finding.subject_id, entity_lookup, scope)
        is ScopeMembership.OUTSIDE
    )
    outside_deltas = tuple(
        delta.delta_id
        for delta in entity_deltas
        if delta.scope_membership is ScopeMembership.OUTSIDE
    )
    blockers: list[str] = []
    if outside_deltas:
        blockers.append("outside_scope_exact_semantic_delta")
    if outside_added_findings:
        blockers.append("outside_scope_exact_finding_added")
    if any(
        finding.severity in {FindingSeverity.STRUCTURAL, FindingSeverity.SAFETY}
        for finding in finding_delta.added
    ):
        blockers.append("new_structural_or_safety_finding")
    counts_by_scope = Counter(delta.scope_membership for delta in entity_deltas)
    counts_by_kind = Counter(delta.entity_kind for delta in entity_deltas)
    status = GateStatus.BLOCKED if blockers else GateStatus.PASS
    return ExactSemanticDiffReport(
        scope_id=scope.scope_id,
        source_sha256=source.source_sha256,
        candidate_sha256=candidate.source_sha256,
        status=status,
        automatic_promotion_gate=status,
        entity_deltas=tuple(entity_deltas),
        finding_delta=finding_delta,
        counts_by_scope={
            membership: counts_by_scope[membership]
            for membership in ScopeMembership
        },
        counts_by_entity_kind=dict(sorted(counts_by_kind.items())),
        outside_scope_delta_ids=outside_deltas,
        outside_scope_added_finding_ids=outside_added_findings,
        blockers=tuple(blockers),
    )


def compare_finding_sets(
    source_findings: Iterable[Finding],
    candidate_findings: Iterable[Finding],
) -> FindingSetDelta:
    source_by_id = _findings_by_id(source_findings)
    candidate_by_id = _findings_by_id(candidate_findings)
    return FindingSetDelta(
        added=tuple(
            candidate_by_id[finding_id]
            for finding_id in sorted(set(candidate_by_id) - set(source_by_id))
        ),
        resolved=tuple(
            source_by_id[finding_id]
            for finding_id in sorted(set(source_by_id) - set(candidate_by_id))
        ),
        unchanged_finding_ids=tuple(sorted(set(source_by_id) & set(candidate_by_id))),
    )


def _findings_by_id(findings: Iterable[Finding]) -> dict[str, Finding]:
    indexed: dict[str, Finding] = {}
    for finding in findings:
        if finding.finding_id in indexed:
            raise ValueError(f"Duplicate finding ID: {finding.finding_id}")
        indexed[finding.finding_id] = finding
    return indexed


def _entity_scope(entity: CanonicalEntity, scope: ScopeSpec) -> ScopeMembership:
    if entity.stable_entity_id in scope.target_entity_ids:
        return ScopeMembership.TARGET
    if entity.stable_entity_id in scope.guard_entity_ids:
        return ScopeMembership.GUARD
    if set(entity.boundary_port_ids) & {
        port.boundary_port_id for port in scope.boundary_ports
    }:
        return ScopeMembership.BOUNDARY
    if set(entity.owner_physical_cell_ids) & set(scope.physical_cell_ids):
        return ScopeMembership.TARGET
    if set(entity.owner_physical_cell_ids) & set(scope.guard_entity_ids):
        return ScopeMembership.GUARD
    return ScopeMembership.OUTSIDE


def _subject_scope(
    subject_id: str,
    entities: dict[str, CanonicalEntity],
    scope: ScopeSpec,
) -> ScopeMembership:
    entity = entities.get(subject_id)
    if entity is None:
        if subject_id in scope.target_entity_ids:
            return ScopeMembership.TARGET
        if subject_id in scope.guard_entity_ids:
            return ScopeMembership.GUARD
        return ScopeMembership.OUTSIDE
    return _entity_scope(entity, scope)
