from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from .base import ContractModel, Sha256, StableToken
from .enums import GateStatus, TrafficSide
from .ids import require_stable_id, stable_id


ConnectionStatus = Literal["pass", "review_required", "fail"]
InternalLinkMode = Literal[
    "internal-links",
    "no-internal-links",
    "mixed",
    "undetermined",
]


class OfficialSumoSourceFile(ContractModel):
    source_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]*$")
    vendored_path: str
    vendored_sha256: Sha256
    upstream_path: str
    upstream_sha256: Sha256

    @model_validator(mode="after")
    def validate_paths(self) -> OfficialSumoSourceFile:
        for label, path in (
            ("vendored_path", self.vendored_path),
            ("upstream_path", self.upstream_path),
        ):
            normalized = path.replace("\\", "/")
            if normalized.startswith("/") or ".." in normalized.split("/"):
                raise ValueError(f"{label} must be a repository-relative path.")
        return self


class OfficialSumoScenarioCase(ContractModel):
    case_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]*$")
    normative_feature: str
    traffic_side: TrafficSide
    source_file_ids: tuple[str, ...]
    netconvert_arguments: tuple[str, ...]
    expected_internal_link_mode: InternalLinkMode
    expected_connection_status: ConnectionStatus
    expected_connection_category_counts: dict[str, int]
    expected_independent_safety_status: GateStatus
    expected_safety_category_counts: dict[str, int]
    expected_movement_count: int = Field(ge=0)
    expected_conflict_count: int = Field(ge=0)
    expected_abstention: Literal[True] = True

    @model_validator(mode="after")
    def validate_case(self) -> OfficialSumoScenarioCase:
        if self.traffic_side is TrafficSide.UNKNOWN:
            raise ValueError("Official SUMO scenarios require an explicit traffic side.")
        if not self.source_file_ids:
            raise ValueError("Official SUMO scenarios require source files.")
        if self.netconvert_arguments.count("@output-net") != 1:
            raise ValueError("netconvert arguments require exactly one @output-net token.")
        referenced_sources = {
            argument.removeprefix("@source:")
            for argument in self.netconvert_arguments
            if argument.startswith("@source:")
        }
        if referenced_sources != set(self.source_file_ids):
            raise ValueError(
                "netconvert @source tokens must exactly match source_file_ids."
            )
        if any(count < 0 for count in self.expected_connection_category_counts.values()):
            raise ValueError("Expected connection finding counts must be non-negative.")
        if any(count < 0 for count in self.expected_safety_category_counts.values()):
            raise ValueError("Expected safety finding counts must be non-negative.")
        return self


class OfficialSumoBenchmarkSpec(ContractModel):
    schema_id: str = "torii.corridor.official-sumo-benchmark/v1"
    benchmark_id: StableToken
    parent_benchmark_sha256: Sha256
    toolchain_lock_sha256: Sha256
    upstream_repository: str
    upstream_tag: str
    upstream_commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    license_expression: Literal["EPL-2.0 OR GPL-2.0-or-later"]
    source_files: tuple[OfficialSumoSourceFile, ...]
    cases: tuple[OfficialSumoScenarioCase, ...]

    def identity_payload(self) -> dict[str, object]:
        return {
            "parent_benchmark_sha256": self.parent_benchmark_sha256,
            "toolchain_lock_sha256": self.toolchain_lock_sha256,
            "upstream_repository": self.upstream_repository,
            "upstream_tag": self.upstream_tag,
            "upstream_commit": self.upstream_commit,
            "license_expression": self.license_expression,
            "source_files": [
                source.model_dump(mode="json", by_alias=True)
                for source in self.source_files
            ],
            "cases": [
                case.model_dump(mode="json", by_alias=True) for case in self.cases
            ],
        }

    @model_validator(mode="after")
    def validate_spec(self) -> OfficialSumoBenchmarkSpec:
        require_stable_id(self.benchmark_id, kind="manifest")
        if self.benchmark_id != stable_id("manifest", self.identity_payload()):
            raise ValueError("benchmark_id does not match the official benchmark payload.")
        source_ids = [source.source_id for source in self.source_files]
        if len(source_ids) != len(set(source_ids)):
            raise ValueError("Official SUMO source IDs must be unique.")
        case_ids = [case.case_id for case in self.cases]
        if len(case_ids) != len(set(case_ids)):
            raise ValueError("Official SUMO case IDs must be unique.")
        available = set(source_ids)
        for case in self.cases:
            missing = set(case.source_file_ids) - available
            if missing:
                raise ValueError(
                    f"Official SUMO case {case.case_id} references missing sources: "
                    f"{sorted(missing)}"
                )
        return self


class OfficialSumoCaseResult(ContractModel):
    case_id: str
    normative_feature: str
    status: GateStatus
    source_immutable: bool
    netconvert_status: str
    replay_netconvert_status: str
    sumo_load_status: str
    generated_net_sha256: Sha256
    replay_net_sha256: Sha256
    normalized_net_sha256: Sha256
    replay_normalized_net_sha256: Sha256
    reproducible_semantics: bool
    internal_link_mode: str
    connection_status: str
    connection_category_counts: dict[str, int]
    independent_safety_status: GateStatus
    safety_category_counts: dict[str, int]
    movement_count: int
    conflict_count: int
    abstention_proven: bool
    blockers: tuple[str, ...]
    generated_net_path: str

    @model_validator(mode="after")
    def validate_result(self) -> OfficialSumoCaseResult:
        if self.status is GateStatus.PASS and (
            not self.source_immutable
            or not self.reproducible_semantics
            or not self.abstention_proven
            or self.netconvert_status != "pass"
            or self.replay_netconvert_status != "pass"
            or self.sumo_load_status != "pass"
            or self.blockers
        ):
            raise ValueError(
                "A passing official SUMO case must prove identity, reproducibility, "
                "runtime load, and fail-closed abstention."
            )
        return self


class OfficialSumoBenchmarkReport(ContractModel):
    schema_id: str = "torii.corridor.official-sumo-benchmark-report/v1"
    benchmark_id: StableToken
    benchmark_spec_sha256: Sha256
    parent_benchmark_sha256: Sha256
    toolchain_lock_sha256: Sha256
    status: GateStatus
    total_case_count: int
    passed_case_count: int
    failed_case_count: int
    source_immutable: bool
    runtime_tools: dict[str, dict[str, object]]
    cases: tuple[OfficialSumoCaseResult, ...]
    blockers: tuple[str, ...]

    @model_validator(mode="after")
    def validate_report(self) -> OfficialSumoBenchmarkReport:
        require_stable_id(self.benchmark_id, kind="manifest")
        if self.total_case_count != len(self.cases):
            raise ValueError("Official benchmark case counts do not close.")
        if self.passed_case_count + self.failed_case_count != self.total_case_count:
            raise ValueError("Official benchmark pass/fail counts do not close.")
        if self.status is GateStatus.PASS and (
            self.failed_case_count or not self.source_immutable or self.blockers
        ):
            raise ValueError("A passing official benchmark cannot contain failures.")
        return self
