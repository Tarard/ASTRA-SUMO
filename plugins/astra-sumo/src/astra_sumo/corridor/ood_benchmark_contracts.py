from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from .applicability import DomainDecision
from .base import ContractModel, Sha256, StableToken
from .enums import GateStatus, TrafficSide
from .ids import require_stable_id, stable_id


class OODBenchmarkCase(ContractModel):
    case_id: str
    fixture_id: str
    traffic_side: TrafficSide
    mutation_ids: tuple[str, ...] = ()
    expected_decision: Literal["in-domain", "out-of-domain"]
    expected_reason_categories: tuple[str, ...] = ()
    expected_independent_safety_status: GateStatus

    @model_validator(mode="after")
    def validate_case(self) -> OODBenchmarkCase:
        if not self.case_id.strip() or not self.fixture_id.strip():
            raise ValueError("OOD case identities cannot be empty.")
        if self.traffic_side is TrafficSide.UNKNOWN:
            raise ValueError("OOD cases require a known traffic side.")
        if len(self.mutation_ids) != len(set(self.mutation_ids)):
            raise ValueError("OOD case mutations must be unique.")
        if len(self.expected_reason_categories) != len(
            set(self.expected_reason_categories)
        ):
            raise ValueError("OOD reason categories must be unique.")
        if self.expected_decision == "in-domain" and self.expected_reason_categories:
            raise ValueError("In-domain OOD cases cannot expect blocking reasons.")
        if self.expected_decision == "out-of-domain" and not self.expected_reason_categories:
            raise ValueError("Out-of-domain OOD cases require precise reasons.")
        return self


class OODBenchmarkSpec(ContractModel):
    schema_id: str = "torii.corridor.ood-benchmark/v1"
    benchmark_id: StableToken
    parent_benchmark_sha256: Sha256
    certification_envelope_sha256: Sha256
    cases: tuple[OODBenchmarkCase, ...]

    def identity_payload(self) -> dict[str, object]:
        return {
            "parent_benchmark_sha256": self.parent_benchmark_sha256,
            "certification_envelope_sha256": self.certification_envelope_sha256,
            "cases": [case.model_dump(mode="json", by_alias=True) for case in self.cases],
        }

    @model_validator(mode="after")
    def validate_spec(self) -> OODBenchmarkSpec:
        require_stable_id(self.benchmark_id, kind="manifest")
        if self.benchmark_id != stable_id("manifest", self.identity_payload()):
            raise ValueError("benchmark_id does not match the OOD benchmark payload.")
        case_ids = [case.case_id for case in self.cases]
        if not case_ids or len(case_ids) != len(set(case_ids)):
            raise ValueError("OOD case IDs must be non-empty and unique.")
        if not any(case.expected_decision == "in-domain" for case in self.cases):
            raise ValueError("OOD benchmark requires in-domain negative controls.")
        if not any(case.expected_decision == "out-of-domain" for case in self.cases):
            raise ValueError("OOD benchmark requires out-of-domain cases.")
        return self


class OODBenchmarkCaseResult(ContractModel):
    case_id: str
    status: GateStatus
    expected_decision: Literal["in-domain", "out-of-domain"]
    observed_decision: DomainDecision
    expected_reason_categories: tuple[str, ...]
    observed_reason_categories: tuple[str, ...]
    decision_matched: bool
    reasons_matched_exactly: bool
    source_sha256: Sha256
    evaluated_sha256: Sha256
    source_immutable: bool
    mutation_ids: tuple[str, ...]
    connection_status: str
    independent_safety_status: GateStatus
    expected_independent_safety_status: GateStatus
    certification_gate: GateStatus
    applicability_report_path: str
    blockers: tuple[str, ...]

    @model_validator(mode="after")
    def validate_result(self) -> OODBenchmarkCaseResult:
        if self.mutation_ids and self.source_sha256 == self.evaluated_sha256:
            raise ValueError("Mutated OOD cases must change fixture content.")
        if self.status is GateStatus.PASS and (
            not self.source_immutable
            or not self.decision_matched
            or not self.reasons_matched_exactly
            or self.independent_safety_status
            is not self.expected_independent_safety_status
            or self.blockers
        ):
            raise ValueError("Passing OOD cases must close every preregistered claim.")
        return self


class OODBenchmarkReport(ContractModel):
    schema_id: str = "torii.corridor.ood-benchmark-report/v1"
    benchmark_id: StableToken
    benchmark_spec_sha256: Sha256
    certification_envelope_sha256: Sha256
    status: GateStatus
    total_case_count: int = Field(ge=0)
    passed_case_count: int = Field(ge=0)
    failed_case_count: int = Field(ge=0)
    expected_out_of_domain_count: int = Field(ge=0)
    detected_out_of_domain_count: int = Field(ge=0)
    out_of_domain_recall: float = Field(ge=0.0, le=1.0)
    expected_in_domain_count: int = Field(ge=0)
    accepted_in_domain_count: int = Field(ge=0)
    in_domain_acceptance_rate: float = Field(ge=0.0, le=1.0)
    overconfident_case_count: int = Field(ge=0)
    false_ood_case_count: int = Field(ge=0)
    clean_fixture_statuses: dict[str, GateStatus]
    cases: tuple[OODBenchmarkCaseResult, ...]
    blockers: tuple[str, ...]

    @model_validator(mode="after")
    def validate_report(self) -> OODBenchmarkReport:
        require_stable_id(self.benchmark_id, kind="manifest")
        if self.total_case_count != len(self.cases):
            raise ValueError("OOD benchmark case totals do not close.")
        if self.passed_case_count + self.failed_case_count != self.total_case_count:
            raise ValueError("OOD benchmark pass/fail totals do not close.")
        if (
            self.expected_out_of_domain_count + self.expected_in_domain_count
            != self.total_case_count
        ):
            raise ValueError("OOD expected class totals do not close.")
        if self.status is GateStatus.PASS and (
            self.failed_case_count
            or self.overconfident_case_count
            or self.false_ood_case_count
            or self.blockers
        ):
            raise ValueError("Passing OOD benchmarks cannot hide classification errors.")
        return self
