from __future__ import annotations

from typing import Any

from pydantic import model_validator

from .base import ContractModel, StableToken
from .enums import (
    CandidateStatus,
    DeltaAction,
    HypothesisType,
    ScopeMembership,
)
from .ids import require_stable_id


class SemanticDelta(ContractModel):
    delta_id: StableToken
    entity_kind: str
    stable_entity_id: StableToken
    action: DeltaAction
    before_signature: StableToken | None = None
    after_signature: StableToken | None = None
    reason: str
    derived: bool = False
    evidence_refs: tuple[StableToken, ...] = ()
    scope_membership: ScopeMembership

    @model_validator(mode="after")
    def validate_delta(self) -> SemanticDelta:
        require_stable_id(self.delta_id, kind="delta")
        require_stable_id(self.stable_entity_id)
        if self.before_signature is not None:
            require_stable_id(self.before_signature)
        if self.after_signature is not None:
            require_stable_id(self.after_signature)
        if self.action is DeltaAction.ADDED:
            if self.before_signature is not None or self.after_signature is None:
                raise ValueError("Added deltas require only an after signature.")
        elif self.action is DeltaAction.REMOVED:
            if self.before_signature is None or self.after_signature is not None:
                raise ValueError("Removed deltas require only a before signature.")
        elif (
            self.before_signature is None
            or self.after_signature is None
            or self.before_signature == self.after_signature
        ):
            raise ValueError("Modified deltas require distinct before and after signatures.")
        for evidence_ref in self.evidence_refs:
            require_stable_id(evidence_ref, kind="evidence")
        return self


class PatchOperation(ContractModel):
    operation_id: StableToken
    operation_type: str
    target_ids: tuple[StableToken, ...]
    preconditions: tuple[dict[str, Any], ...]
    forward_patch: dict[str, Any]
    inverse_patch: dict[str, Any]
    evidence_refs: tuple[StableToken, ...] = ()

    @model_validator(mode="after")
    def validate_operation(self) -> PatchOperation:
        require_stable_id(self.operation_id, kind="operation")
        if not self.target_ids:
            raise ValueError("Patch operations require explicit stable targets.")
        for target_id in self.target_ids:
            require_stable_id(target_id)
        if not self.preconditions:
            raise ValueError("Patch operations require preconditions.")
        if not self.forward_patch or not self.inverse_patch:
            raise ValueError("Patch operations must be reversible.")
        if self.forward_patch == self.inverse_patch:
            raise ValueError("Forward and inverse patches must differ.")
        for evidence_ref in self.evidence_refs:
            require_stable_id(evidence_ref, kind="evidence")
        return self


class Hypothesis(ContractModel):
    hypothesis_id: StableToken
    hypothesis_type: HypothesisType
    scope_id: StableToken
    assumptions: tuple[str, ...]
    predicted_changes: tuple[str, ...]
    alternative_hypothesis_ids: tuple[StableToken, ...] = ()
    falsifiers: tuple[str, ...]
    evidence_refs: tuple[StableToken, ...] = ()

    @model_validator(mode="after")
    def validate_hypothesis(self) -> Hypothesis:
        require_stable_id(self.hypothesis_id, kind="hypothesis")
        require_stable_id(self.scope_id, kind="scope")
        if not self.assumptions or not self.falsifiers:
            raise ValueError("Hypotheses require assumptions and explicit falsifiers.")
        for alternative_id in self.alternative_hypothesis_ids:
            require_stable_id(alternative_id, kind="hypothesis")
            if alternative_id == self.hypothesis_id:
                raise ValueError("A hypothesis cannot list itself as an alternative.")
        for evidence_ref in self.evidence_refs:
            require_stable_id(evidence_ref, kind="evidence")
        return self


class CandidateVariant(ContractModel):
    variant_id: StableToken
    parent_variant_ids: tuple[StableToken, ...] = ()
    hypothesis_id: StableToken
    scope_id: StableToken
    operations: tuple[PatchOperation, ...]
    expected_delta: tuple[SemanticDelta, ...]
    status: CandidateStatus = CandidateStatus.PLANNED

    @model_validator(mode="after")
    def validate_variant(self) -> CandidateVariant:
        require_stable_id(self.variant_id, kind="candidate")
        require_stable_id(self.hypothesis_id, kind="hypothesis")
        require_stable_id(self.scope_id, kind="scope")
        for parent_id in self.parent_variant_ids:
            require_stable_id(parent_id, kind="candidate")
            if parent_id == self.variant_id:
                raise ValueError("A candidate cannot be its own parent.")
        operation_ids = [operation.operation_id for operation in self.operations]
        if len(operation_ids) != len(set(operation_ids)):
            raise ValueError("Candidate operation IDs must be unique.")
        delta_ids = [delta.delta_id for delta in self.expected_delta]
        if len(delta_ids) != len(set(delta_ids)):
            raise ValueError("Candidate delta IDs must be unique.")
        return self


class CandidateGraph(ContractModel):
    variants: tuple[CandidateVariant, ...]

    @model_validator(mode="after")
    def validate_dag(self) -> CandidateGraph:
        variants_by_id = {variant.variant_id: variant for variant in self.variants}
        if len(variants_by_id) != len(self.variants):
            raise ValueError("Candidate variant IDs must be unique.")
        for variant in self.variants:
            missing = set(variant.parent_variant_ids) - set(variants_by_id)
            if missing:
                raise ValueError(f"Candidate parents are missing: {sorted(missing)}")

        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(variant_id: str) -> None:
            if variant_id in visiting:
                raise ValueError(f"Candidate graph contains a cycle at {variant_id}.")
            if variant_id in visited:
                return
            visiting.add(variant_id)
            for parent_id in variants_by_id[variant_id].parent_variant_ids:
                visit(parent_id)
            visiting.remove(variant_id)
            visited.add(variant_id)

        for variant_id in variants_by_id:
            visit(variant_id)
        return self
