from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from .base import ContractModel, Sha256, StableToken
from .enums import GateStatus, TrafficSide
from .ids import require_stable_id, stable_id


ObservationChannel = Literal[
    "connection-structural",
    "connection-review",
    "independent-safety",
    "independent-review",
    "exact-delta",
]
CertificationExpectation = Literal["must-detect", "must-abstain"]


class GoldObservation(ContractModel):
    channel: ObservationChannel
    value: str
    minimum_count: int = Field(default=1, ge=1)


class SyntheticFaultCase(ContractModel):
    case_id: str
    fault_family: str
    fixture_id: str
    mutation_id: str
    traffic_side: TrafficSide
    certification_expectation: CertificationExpectation
    expected_observations: tuple[GoldObservation, ...]
    expected_abstention: Literal[True] = True

    @model_validator(mode="after")
    def validate_case(self) -> SyntheticFaultCase:
        if self.traffic_side is TrafficSide.UNKNOWN:
            raise ValueError("Synthetic benchmark cases require an explicit traffic side.")
        if not self.expected_observations:
            raise ValueError("Synthetic benchmark cases require gold observations.")
        return self


class SyntheticFaultBenchmarkSpec(ContractModel):
    schema_id: str = "torii.corridor.synthetic-fault-benchmark/v1"
    benchmark_id: StableToken
    parent_benchmark_sha256: Sha256
    cases: tuple[SyntheticFaultCase, ...]

    def identity_payload(self) -> dict[str, object]:
        return {
            "parent_benchmark_sha256": self.parent_benchmark_sha256,
            "cases": [case.model_dump(mode="json", by_alias=True) for case in self.cases],
        }

    @model_validator(mode="after")
    def validate_spec(self) -> SyntheticFaultBenchmarkSpec:
        require_stable_id(self.benchmark_id, kind="manifest")
        if self.benchmark_id != stable_id("manifest", self.identity_payload()):
            raise ValueError("benchmark_id does not match the synthetic benchmark payload.")
        case_ids = [case.case_id for case in self.cases]
        if len(case_ids) != len(set(case_ids)):
            raise ValueError("Synthetic benchmark case IDs must be unique.")
        fault_families = [case.fault_family for case in self.cases]
        if len(fault_families) != len(set(fault_families)):
            raise ValueError("Synthetic benchmark v1 requires one gold case per fault family.")
        return self


class ObservationMatch(ContractModel):
    channel: ObservationChannel
    value: str
    expected_minimum_count: int
    observed_count: int
    matched: bool


class SyntheticFaultCaseResult(ContractModel):
    case_id: str
    fault_family: str
    certification_expectation: CertificationExpectation
    status: GateStatus
    source_sha256: Sha256
    mutant_sha256: Sha256
    source_immutable: bool
    connection_status: str
    independent_safety_status: GateStatus
    exact_delta_count: int
    observation_matches: tuple[ObservationMatch, ...]
    observed: dict[ObservationChannel, dict[str, int]]
    abstention_proven: bool
    blockers: tuple[str, ...]
    source_net_path: str
    mutant_net_path: str

    @model_validator(mode="after")
    def validate_result(self) -> SyntheticFaultCaseResult:
        if self.source_sha256 == self.mutant_sha256:
            raise ValueError("A synthetic fault case must change the fixture content.")
        if self.status is GateStatus.PASS and (
            not self.source_immutable
            or not self.abstention_proven
            or any(not match.matched for match in self.observation_matches)
        ):
            raise ValueError("Passing benchmark cases must prove identity, abstention, and gold observations.")
        return self


class SyntheticFaultBenchmarkReport(ContractModel):
    schema_id: str = "torii.corridor.synthetic-fault-benchmark-report/v1"
    benchmark_id: StableToken
    benchmark_spec_sha256: Sha256
    status: GateStatus
    total_case_count: int
    passed_case_count: int
    failed_case_count: int
    must_detect_case_count: int
    must_detect_passed_count: int
    must_abstain_case_count: int
    must_abstain_passed_count: int
    clean_fixture_statuses: dict[str, GateStatus]
    cases: tuple[SyntheticFaultCaseResult, ...]
    blockers: tuple[str, ...]

    @model_validator(mode="after")
    def validate_report(self) -> SyntheticFaultBenchmarkReport:
        require_stable_id(self.benchmark_id, kind="manifest")
        if self.total_case_count != len(self.cases):
            raise ValueError("Benchmark total_case_count does not match its case results.")
        if self.passed_case_count + self.failed_case_count != self.total_case_count:
            raise ValueError("Benchmark pass/fail counts do not close.")
        if self.status is GateStatus.PASS and self.failed_case_count:
            raise ValueError("A passing benchmark cannot contain failed cases.")
        return self
