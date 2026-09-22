from __future__ import annotations

from typing import Any

from pydantic import Field, model_validator

from .base import ContractModel, StableToken
from .enums import (
    AutomationAction,
    GateStatus,
    QualityDimensionName,
    WorkflowStage,
)
from .evidence import InvariantResult
from .ids import require_stable_id, stable_id


_ALLOWED_TRANSITIONS: dict[WorkflowStage, frozenset[WorkflowStage]] = {
    WorkflowStage.INGESTED: frozenset({WorkflowStage.CANONICALIZED, WorkflowStage.BLOCKED}),
    WorkflowStage.CANONICALIZED: frozenset({WorkflowStage.FINDINGS_READY, WorkflowStage.BLOCKED}),
    WorkflowStage.FINDINGS_READY: frozenset({WorkflowStage.HYPOTHESES_READY, WorkflowStage.BLOCKED}),
    WorkflowStage.HYPOTHESES_READY: frozenset({WorkflowStage.CANDIDATE_PLANNED, WorkflowStage.BLOCKED}),
    WorkflowStage.CANDIDATE_PLANNED: frozenset({WorkflowStage.MATERIALIZED, WorkflowStage.BLOCKED}),
    WorkflowStage.MATERIALIZED: frozenset({WorkflowStage.STRUCTURALLY_VERIFIED, WorkflowStage.BLOCKED}),
    WorkflowStage.STRUCTURALLY_VERIFIED: frozenset({WorkflowStage.SAFETY_VERIFIED, WorkflowStage.BLOCKED}),
    WorkflowStage.SAFETY_VERIFIED: frozenset({WorkflowStage.DIFFERENTIALLY_VERIFIED, WorkflowStage.BLOCKED}),
    WorkflowStage.DIFFERENTIALLY_VERIFIED: frozenset({WorkflowStage.RUNTIME_VERIFIED, WorkflowStage.BLOCKED}),
    WorkflowStage.RUNTIME_VERIFIED: frozenset(
        {
            WorkflowStage.REVIEW_PENDING,
            WorkflowStage.AUTO_CERTIFIED,
            WorkflowStage.BLOCKED,
        }
    ),
    WorkflowStage.REVIEW_PENDING: frozenset(
        {WorkflowStage.ACCEPTED, WorkflowStage.REJECTED, WorkflowStage.BLOCKED}
    ),
    WorkflowStage.AUTO_CERTIFIED: frozenset({WorkflowStage.ACCEPTED, WorkflowStage.BLOCKED}),
    WorkflowStage.BLOCKED: frozenset(),
    WorkflowStage.ACCEPTED: frozenset(),
    WorkflowStage.REJECTED: frozenset(),
}


class QualityDimension(ContractModel):
    name: QualityDimensionName
    status: GateStatus
    metrics: dict[str, Any] = Field(default_factory=dict)
    witnesses: tuple[StableToken, ...] = ()


class NetworkQualityVectorV1(ContractModel):
    dimensions: tuple[QualityDimension, ...]

    @model_validator(mode="after")
    def validate_dimensions(self) -> NetworkQualityVectorV1:
        names = [dimension.name for dimension in self.dimensions]
        required = set(QualityDimensionName)
        if set(names) != required or len(names) != len(required):
            raise ValueError("Quality vector must contain every dimension exactly once.")
        return self


class StageOutcome(ContractModel):
    stage: WorkflowStage
    status: GateStatus
    input_artifact_ids: tuple[StableToken, ...] = ()
    output_artifact_ids: tuple[StableToken, ...] = ()
    invariant_results: tuple[InvariantResult, ...] = ()
    unresolved_review_task_ids: tuple[StableToken, ...] = ()
    notes: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_outcome(self) -> StageOutcome:
        for artifact_id in self.input_artifact_ids + self.output_artifact_ids:
            require_stable_id(artifact_id, kind="artifact")
        for task_id in self.unresolved_review_task_ids:
            require_stable_id(task_id, kind="review")
        if any(
            result.hard_gate and result.status is not GateStatus.PASS
            for result in self.invariant_results
        ) and self.status is GateStatus.PASS:
            raise ValueError("A stage cannot pass while a hard invariant is not pass.")
        return self

    @property
    def has_hard_failure(self) -> bool:
        return self.status in {GateStatus.FAIL, GateStatus.BLOCKED} or any(
            result.hard_gate and result.status is not GateStatus.PASS
            for result in self.invariant_results
        )


class WorkflowTransition(ContractModel):
    transition_id: StableToken
    from_stage: WorkflowStage
    to_stage: WorkflowStage
    outcome: StageOutcome

    @model_validator(mode="after")
    def validate_transition(self) -> WorkflowTransition:
        require_stable_id(self.transition_id, kind="transition")
        if self.to_stage not in _ALLOWED_TRANSITIONS[self.from_stage]:
            raise ValueError(f"Illegal workflow transition: {self.from_stage} -> {self.to_stage}")
        if self.outcome.stage is not self.from_stage:
            raise ValueError("Stage outcome must describe the transition source stage.")
        if self.outcome.has_hard_failure and self.to_stage is not WorkflowStage.BLOCKED:
            raise ValueError("Hard failures may only transition to BLOCKED.")
        if self.to_stage is WorkflowStage.AUTO_CERTIFIED:
            if self.outcome.status is not GateStatus.PASS:
                raise ValueError("AUTO_CERTIFIED requires a passing runtime outcome.")
            if self.outcome.unresolved_review_task_ids:
                raise ValueError("AUTO_CERTIFIED cannot retain unresolved review tasks.")
            if any(result.status is not GateStatus.PASS for result in self.outcome.invariant_results):
                raise ValueError("AUTO_CERTIFIED requires every supplied invariant to pass.")
        return self


class WorkflowExecution(ContractModel):
    workflow_id: StableToken
    current_stage: WorkflowStage = WorkflowStage.INGESTED
    transitions: tuple[WorkflowTransition, ...] = ()
    action: AutomationAction = AutomationAction.BLOCK

    @model_validator(mode="after")
    def validate_execution(self) -> WorkflowExecution:
        require_stable_id(self.workflow_id, kind="manifest")
        stage = WorkflowStage.INGESTED
        for transition in self.transitions:
            if transition.from_stage is not stage:
                raise ValueError("Workflow transition history is not contiguous.")
            stage = transition.to_stage
        if stage is not self.current_stage:
            raise ValueError("current_stage must equal the end of transition history.")
        expected_action = _action_for_stage(self.current_stage)
        if self.action is not expected_action:
            raise ValueError(
                f"Action {self.action.value} is invalid for stage {self.current_stage.value}; "
                f"expected {expected_action.value}."
            )
        return self

    def advance(self, to_stage: WorkflowStage, outcome: StageOutcome) -> WorkflowExecution:
        transition = WorkflowTransition(
            transition_id=stable_id(
                "transition",
                {
                    "workflow_id": self.workflow_id,
                    "ordinal": len(self.transitions),
                    "from_stage": self.current_stage,
                    "to_stage": to_stage,
                    "outcome": outcome,
                },
            ),
            from_stage=self.current_stage,
            to_stage=to_stage,
            outcome=outcome,
        )
        return self.model_copy(
            update={
                "current_stage": to_stage,
                "transitions": (*self.transitions, transition),
                "action": _action_for_stage(to_stage),
            }
        )


def _action_for_stage(stage: WorkflowStage) -> AutomationAction:
    if stage is WorkflowStage.AUTO_CERTIFIED:
        return AutomationAction.AUTO_REPAIR
    if stage is WorkflowStage.REVIEW_PENDING:
        return AutomationAction.REVIEW
    if stage in {WorkflowStage.BLOCKED, WorkflowStage.REJECTED, WorkflowStage.INGESTED}:
        return AutomationAction.BLOCK
    return AutomationAction.SUGGEST
