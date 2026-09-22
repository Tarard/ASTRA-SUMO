from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from .base import ContractModel, Sha256, StableToken
from .enums import GateStatus, TrafficSide
from .ids import require_stable_id, stable_id
from .synthetic_benchmark_contracts import GoldObservation, ObservationMatch


class CompositeFaultComponent(ContractModel):
    fault_family: str
    mutation_id: str
    expected_observations: tuple[GoldObservation, ...]

    @model_validator(mode="after")
    def validate_component(self) -> CompositeFaultComponent:
        if not self.fault_family.strip() or not self.mutation_id.strip():
            raise ValueError("Composite fault component identities cannot be empty.")
        if not self.expected_observations:
            raise ValueError("Composite fault components require gold observations.")
        witnesses = [
            (observation.channel, observation.value)
            for observation in self.expected_observations
        ]
        if len(witnesses) != len(set(witnesses)):
            raise ValueError("Composite fault component observations must be unique.")
        return self


class CompositeFaultCase(ContractModel):
    case_id: str
    fixture_id: str
    traffic_side: TrafficSide
    interaction_class: str
    components: tuple[CompositeFaultComponent, ...]
    expected_abstention: Literal[True] = True

    @model_validator(mode="after")
    def validate_case(self) -> CompositeFaultCase:
        if (
            not self.case_id.strip()
            or not self.fixture_id.strip()
            or not self.interaction_class.strip()
        ):
            raise ValueError("Composite case identities cannot be empty.")
        if self.traffic_side is TrafficSide.UNKNOWN:
            raise ValueError("Composite cases require an explicit traffic side.")
        if len(self.components) < 2:
            raise ValueError("Composite cases require at least two fault components.")
        families = [component.fault_family for component in self.components]
        mutations = [component.mutation_id for component in self.components]
        if len(families) != len(set(families)):
            raise ValueError("Composite fault families must be unique within a case.")
        if len(mutations) != len(set(mutations)):
            raise ValueError("Composite mutations must be unique within a case.")
        witness_sets = [
            {
                (observation.channel, observation.value)
                for observation in component.expected_observations
            }
            for component in self.components
        ]
        for index, witnesses in enumerate(witness_sets):
            other_witnesses = set().union(
                *(candidate for offset, candidate in enumerate(witness_sets) if offset != index)
            )
            if not witnesses - other_witnesses:
                raise ValueError(
                    "Every composite fault component requires at least one exclusive "
                    "gold witness."
                )
        return self


class CompositeFaultBenchmarkSpec(ContractModel):
    schema_id: str = "torii.corridor.composite-fault-benchmark/v1"
    benchmark_id: StableToken
    parent_benchmark_sha256: Sha256
    single_fault_benchmark_sha256: Sha256
    cases: tuple[CompositeFaultCase, ...]

    def identity_payload(self) -> dict[str, object]:
        return {
            "parent_benchmark_sha256": self.parent_benchmark_sha256,
            "single_fault_benchmark_sha256": self.single_fault_benchmark_sha256,
            "cases": [case.model_dump(mode="json", by_alias=True) for case in self.cases],
        }

    @model_validator(mode="after")
    def validate_spec(self) -> CompositeFaultBenchmarkSpec:
        require_stable_id(self.benchmark_id, kind="manifest")
        if self.benchmark_id != stable_id("manifest", self.identity_payload()):
            raise ValueError("benchmark_id does not match the composite payload.")
        case_ids = [case.case_id for case in self.cases]
        if not case_ids or len(case_ids) != len(set(case_ids)):
            raise ValueError("Composite benchmark case IDs must be non-empty and unique.")
        return self


class CompositeFaultCaseResult(ContractModel):
    case_id: str
    interaction_class: str
    component_fault_families: tuple[str, ...]
    mutation_ids: tuple[str, ...]
    status: GateStatus
    source_sha256: Sha256
    mutant_sha256: Sha256
    source_immutable: bool
    connection_status: str
    independent_safety_status: GateStatus
    exact_delta_count: int = Field(ge=0)
    component_observation_matches: dict[str, tuple[ObservationMatch, ...]]
    component_coverage: dict[str, bool]
    component_recall: float = Field(ge=0.0, le=1.0)
    total_observation_count: int = Field(ge=0)
    matched_observation_count: int = Field(ge=0)
    observation_recall: float = Field(ge=0.0, le=1.0)
    observed: dict[str, dict[str, int]]
    abstention_proven: bool
    blockers: tuple[str, ...]
    source_net_path: str
    mutant_net_path: str

    @model_validator(mode="after")
    def validate_result(self) -> CompositeFaultCaseResult:
        families = set(self.component_fault_families)
        if families != set(self.component_coverage) or families != set(
            self.component_observation_matches
        ):
            raise ValueError("Composite result component coverage is incomplete.")
        if len(self.component_fault_families) != len(self.mutation_ids):
            raise ValueError("Composite result mutations do not align with components.")
        if self.source_sha256 == self.mutant_sha256:
            raise ValueError("A composite fault case must change the fixture content.")
        computed_coverage = {
            family: all(match.matched for match in matches)
            for family, matches in self.component_observation_matches.items()
        }
        if self.component_coverage != computed_coverage:
            raise ValueError("Composite component coverage does not match its witnesses.")
        expected_observation_count = sum(
            len(matches) for matches in self.component_observation_matches.values()
        )
        expected_matched_count = sum(
            sum(match.matched for match in matches)
            for matches in self.component_observation_matches.values()
        )
        if self.total_observation_count != expected_observation_count:
            raise ValueError("Composite observation totals do not close.")
        if self.matched_observation_count != expected_matched_count:
            raise ValueError("Composite matched observation totals do not close.")
        if self.status is GateStatus.PASS and (
            not self.source_immutable
            or not self.abstention_proven
            or not all(self.component_coverage.values())
            or self.blockers
        ):
            raise ValueError(
                "Passing composite cases must expose every component and abstain."
            )
        return self


class CompositeFaultBenchmarkReport(ContractModel):
    schema_id: str = "torii.corridor.composite-fault-benchmark-report/v1"
    benchmark_id: StableToken
    benchmark_spec_sha256: Sha256
    status: GateStatus
    total_case_count: int
    passed_case_count: int
    failed_case_count: int
    total_component_count: int
    covered_component_count: int
    component_recall: float = Field(ge=0.0, le=1.0)
    total_observation_count: int = Field(ge=0)
    matched_observation_count: int = Field(ge=0)
    observation_recall: float = Field(ge=0.0, le=1.0)
    interaction_class_case_counts: dict[str, int]
    clean_fixture_statuses: dict[str, GateStatus]
    cases: tuple[CompositeFaultCaseResult, ...]
    blockers: tuple[str, ...]

    @model_validator(mode="after")
    def validate_report(self) -> CompositeFaultBenchmarkReport:
        require_stable_id(self.benchmark_id, kind="manifest")
        if self.total_case_count != len(self.cases):
            raise ValueError("Composite benchmark case counts do not close.")
        if self.passed_case_count + self.failed_case_count != self.total_case_count:
            raise ValueError("Composite benchmark pass/fail counts do not close.")
        if self.covered_component_count > self.total_component_count:
            raise ValueError("Covered composite components exceed the total.")
        if self.matched_observation_count > self.total_observation_count:
            raise ValueError("Matched composite observations exceed the total.")
        if sum(self.interaction_class_case_counts.values()) != self.total_case_count:
            raise ValueError("Composite interaction class counts do not close.")
        if self.status is GateStatus.PASS and (self.failed_case_count or self.blockers):
            raise ValueError("Passing composite benchmark cannot contain failures.")
        return self
