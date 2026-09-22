from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field, model_validator

from .base import ContractModel, Sha256, StableToken
from .enums import GateStatus
from .ids import require_stable_id, stable_id
from .run_identity import CodeProducerIdentity


Stage1EvidenceVisibility = Literal[
    "tracked-public",
    "local-machine-evidence",
    "reviewer-visible",
    "restricted-hash-only",
]

_REQUIRED_GATE_IDS = (
    "contract-conformance",
    "provenance-identity-closure",
    "thirty-blinded-review-cases",
    "stable-coverage-gap-entities",
    "controlled-binding-census",
    "lossless-conflict-ledger",
    "sampling-and-hidden-member-protocol",
    "trial-and-statistics-frozen",
    "automatic-promotion-blocked",
    "review-ready-stage-semantics-explicit",
)

_CLAIMS_NOT_SUPPORTED = (
    "auto-precision-or-auto-coverage-has-been-measured",
    "machine-findings-are-human-confirmed-field-defects",
    "nema-pedestrian-bicycle-rail-or-multimodal-safety-is-certified",
    "request-foes-internal-geometry-or-tls-is-independently-proven-correct",
    "stage-1-has-exited",
    "stage-2-or-later-edit-classes-are-certified",
    "the-held-out-corpus-proves-cross-city-generalization",
    "the-row-1-development-oracle-proves-field-right-of-way-truth",
    "universal-osm-cleaning-to-expert-netedit-quality-is-achieved",
)


class Stage1EvidenceArtifact(ContractModel):
    logical_name: str
    role: str
    path: str
    sha256: Sha256
    visibility: Stage1EvidenceVisibility
    tracked_in_git: bool

    @model_validator(mode="after")
    def validate_artifact(self) -> Stage1EvidenceArtifact:
        if not self.logical_name or not self.role or not self.path:
            raise ValueError("Stage 1-M evidence artifacts require names, roles, and paths.")
        if self.path.startswith(("/", "\\")) or ":" in self.path:
            raise ValueError("Stage 1-M provenance paths must be repository-relative.")
        if self.visibility == "tracked-public" and not self.tracked_in_git:
            raise ValueError("Public tracked evidence must be recorded as tracked in Git.")
        if self.visibility == "restricted-hash-only" and self.tracked_in_git:
            raise ValueError("Restricted review material cannot be committed to Git.")
        return self


class Stage1EvidenceDependency(ContractModel):
    parent_path: str
    child_path: str
    relation: str

    @model_validator(mode="after")
    def validate_dependency(self) -> Stage1EvidenceDependency:
        if self.parent_path == self.child_path:
            raise ValueError("Stage 1-M evidence dependencies cannot be self-referential.")
        if not self.relation:
            raise ValueError("Stage 1-M evidence dependencies require a relation.")
        return self


class Stage1GateEvidence(ContractModel):
    gate_id: str
    status: Literal[GateStatus.PASS] = GateStatus.PASS
    evidence_paths: tuple[str, ...]
    conclusion: str

    @model_validator(mode="after")
    def validate_gate(self) -> Stage1GateEvidence:
        if self.gate_id not in _REQUIRED_GATE_IDS:
            raise ValueError(f"Unknown Stage 1-M gate: {self.gate_id}.")
        if not self.evidence_paths or not self.conclusion:
            raise ValueError("Passing Stage 1-M gates require evidence and a conclusion.")
        return self


class Stage1SnapshotSummary(ContractModel):
    status: Literal[GateStatus.PASS] = GateStatus.PASS
    city_extract_count: Literal[6] = 6
    corridor_count: Literal[30] = 30
    reference_complete_corridor_count: Literal[30] = 30
    unconfirmed_feature_count: Literal[0] = 0
    manifest_artifact_count: Literal[40] = 40
    manifest_missing_artifact_count: Literal[0] = 0
    manifest_hash_failure_count: Literal[0] = 0


class Stage1MachineSummary(ContractModel):
    expected_case_count: Literal[30] = 30
    processed_case_count: Literal[30] = 30
    pipeline_pass_count: Literal[30] = 30
    semantic_replay_pass_count: Literal[30] = 30
    source_osm_immutable_count: Literal[30] = 30
    machine_label_counts: dict[str, int]
    manifest_artifact_count: int = Field(ge=1)
    manifest_missing_artifact_count: Literal[0] = 0
    manifest_hash_failure_count: Literal[0] = 0
    report_sha256: Sha256
    manifest_sha256: Sha256
    run_identity_sha256: Sha256
    evidence_build_status: Literal[GateStatus.REVIEW] = GateStatus.REVIEW
    automatic_promotion_gate: Literal[GateStatus.BLOCKED] = GateStatus.BLOCKED

    @model_validator(mode="after")
    def validate_machine_summary(self) -> Stage1MachineSummary:
        if self.machine_label_counts != {"defect": 30}:
            raise ValueError("The frozen Stage 1-M corpus must retain its exact machine labels.")
        return self


class Stage1PCBSummary(ContractModel):
    frozen_experiment_name: Literal["PCB-453"] = "PCB-453"
    frozen_retained_corridor_count: Literal[27] = 27
    frozen_unresolved_binding_count: Literal[453] = 453
    effective_experiment_name: Literal["PCB-459"] = "PCB-459"
    effective_corridor_count: Literal[30] = 30
    effective_unresolved_binding_count: int = Field(ge=453)
    replacement_contribution_count: int = Field(ge=0)
    class_counts: dict[str, int]
    hard_structural_count: int = Field(ge=0)
    ambiguous_count: int = Field(ge=0)
    exact_review_position_count: int = Field(ge=0)
    effective_program_misclassified_as_missing_count: Literal[0] = 0
    runtime_special_misclassified_as_ordinary_count: Literal[0] = 0
    unique_primary_classification_count: int = Field(ge=0)
    automatic_promotion_gate: Literal[GateStatus.BLOCKED] = GateStatus.BLOCKED

    @model_validator(mode="after")
    def validate_pcb_summary(self) -> Stage1PCBSummary:
        if self.frozen_unresolved_binding_count + self.replacement_contribution_count != (
            self.effective_unresolved_binding_count
        ):
            raise ValueError("PCB retained and replacement populations do not close.")
        if sum(self.class_counts.values()) != self.effective_unresolved_binding_count:
            raise ValueError("PCB class counts do not close over the effective corpus.")
        if self.unique_primary_classification_count != self.effective_unresolved_binding_count:
            raise ValueError("Every PCB assessment requires one primary class.")
        if self.exact_review_position_count != self.effective_unresolved_binding_count:
            raise ValueError("Every PCB assessment requires an exact review position.")
        expected_hard = self.class_counts.get("ordinary-program-truly-absent", 0) + self.class_counts.get(
            "program-present-link-invalid", 0
        )
        if self.hard_structural_count != expected_hard:
            raise ValueError("PCB hard-structural total does not match its certified classes.")
        if self.ambiguous_count != self.class_counts.get("stale-or-ambiguous-control-reference", 0):
            raise ValueError("PCB ambiguous total does not match its class count.")
        return self


class Stage1RWCReplacementContribution(ContractModel):
    corridor_key: str
    confirmed_count: int = Field(ge=0)
    potential_count: int = Field(ge=0)
    atomic_witness_count: int = Field(ge=0)
    cluster_count: int = Field(ge=0)
    site_review_case_count: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_contribution(self) -> Stage1RWCReplacementContribution:
        if self.atomic_witness_count != self.confirmed_count + self.potential_count:
            raise ValueError("RWC replacement witness counts do not close.")
        return self


class Stage1RWCSummary(ContractModel):
    frozen_experiment_name: Literal["RWC-1"] = "RWC-1"
    frozen_retained_corridor_count: Literal[27] = 27
    frozen_confirmed_count: Literal[34493] = 34493
    frozen_potential_count: Literal[53930] = 53930
    frozen_atomic_witness_count: Literal[88423] = 88423
    effective_corridor_count: Literal[30] = 30
    effective_confirmed_count: int = Field(ge=0)
    effective_potential_count: int = Field(ge=0)
    effective_atomic_witness_count: int = Field(ge=0)
    effective_cluster_count: int = Field(ge=0)
    effective_site_review_case_count: int = Field(ge=0)
    replacement_contributions: tuple[Stage1RWCReplacementContribution, ...]
    atomic_membership_coverage: Literal[1.0] = 1.0
    lost_witness_count: Literal[0] = 0
    duplicate_membership_count: Literal[0] = 0
    extraneous_membership_count: Literal[0] = 0
    mixed_hard_key_violation_count: Literal[0] = 0
    automatic_promotion_gate: Literal[GateStatus.BLOCKED] = GateStatus.BLOCKED

    @model_validator(mode="after")
    def validate_rwc_summary(self) -> Stage1RWCSummary:
        if self.frozen_atomic_witness_count != self.frozen_confirmed_count + self.frozen_potential_count:
            raise ValueError("The frozen RWC-1 witness population does not close.")
        if self.effective_atomic_witness_count != self.effective_confirmed_count + self.effective_potential_count:
            raise ValueError("The effective RWC witness population does not close.")
        keys = [item.corridor_key for item in self.replacement_contributions]
        if len(keys) != 3 or keys != sorted(set(keys)):
            raise ValueError("RWC requires three sorted deterministic replacement contributions.")
        if self.effective_atomic_witness_count != self.frozen_atomic_witness_count + sum(
            item.atomic_witness_count for item in self.replacement_contributions
        ):
            raise ValueError("RWC retained and replacement witness populations do not close.")
        if self.effective_confirmed_count != self.frozen_confirmed_count + sum(
            item.confirmed_count for item in self.replacement_contributions
        ):
            raise ValueError("RWC confirmed witness populations do not close.")
        if self.effective_potential_count != self.frozen_potential_count + sum(
            item.potential_count for item in self.replacement_contributions
        ):
            raise ValueError("RWC potential witness populations do not close.")
        return self


class Stage1CoverageGapEvidence(ContractModel):
    corridor_key: str
    coverage_gap_id: StableToken
    crossing_signature: StableToken
    position_xy: tuple[float, float]
    primary_classification: Literal["safety-coverage-blocker"]
    secondary_classifications: tuple[str, ...]
    rejection_reasons: tuple[str, ...]
    certification_site_group: str

    @model_validator(mode="after")
    def validate_gap(self) -> Stage1CoverageGapEvidence:
        require_stable_id(self.coverage_gap_id, kind="coverage")
        require_stable_id(self.crossing_signature, kind="signature")
        if not self.rejection_reasons or not self.certification_site_group:
            raise ValueError("Coverage-gap evidence requires reasons and a site group.")
        return self


class Stage1CoverageSummary(ContractModel):
    effective_coverage_gap_count: Literal[3] = 3
    paris_coverage_gap_count: Literal[2] = 2
    certification_site_group_count: Literal[2] = 2
    gaps: tuple[Stage1CoverageGapEvidence, ...]
    field_structural_defect_claimed: Literal[False] = False

    @model_validator(mode="after")
    def validate_coverage(self) -> Stage1CoverageSummary:
        ids = [gap.coverage_gap_id for gap in self.gaps]
        if len(ids) != self.effective_coverage_gap_count or ids != sorted(set(ids)):
            raise ValueError("Coverage-gap identities must be complete, sorted, and unique.")
        if sum(gap.corridor_key == "paris-porte-maillot" for gap in self.gaps) != self.paris_coverage_gap_count:
            raise ValueError("The two frozen Paris coverage gaps must remain explicit.")
        if len({gap.certification_site_group for gap in self.gaps}) != self.certification_site_group_count:
            raise ValueError("Coverage gaps must not be over-counted as independent sites.")
        return self


class Stage1ROWSummary(ContractModel):
    experiment_name: Literal["ROW-1"] = "ROW-1"
    report_id: StableToken
    report_sha256: Sha256
    independent_repeat_sha256: Sha256
    exact_repeat_match: Literal[True] = True
    case_count: Literal[15] = 15
    runtime_probe_count: Literal[12] = 12
    failed_case_count: Literal[0] = 0
    unsafe_false_pass_count: Literal[0] = 0
    source_insufficient_forced_decision_count: Literal[0] = 0
    expected_answer_model_claim_read_count: Literal[0] = 0
    status: Literal[GateStatus.PASS] = GateStatus.PASS
    field_truth_claimed: Literal[False] = False
    automatic_promotion_gate: Literal[GateStatus.BLOCKED] = GateStatus.BLOCKED

    @model_validator(mode="after")
    def validate_row(self) -> Stage1ROWSummary:
        require_stable_id(self.report_id, kind="manifest")
        if self.report_sha256 != self.independent_repeat_sha256:
            raise ValueError("ROW-1 repeat reports must match exactly.")
        return self


class Stage1ReviewPackageSummary(ContractModel):
    trial_id: StableToken
    corridor_package_count: Literal[30] = 30
    review_unit_count: Literal[384] = 384
    atomic_witness_population_count: int = Field(ge=0)
    selected_conflict_site_count: Literal[240] = 240
    selected_negative_pair_count: Literal[120] = 120
    controlled_binding_hard_census_count: Literal[21] = 21
    pedestrian_coverage_gap_census_count: Literal[3] = 3
    independent_hidden_witness_count: Literal[217] = 217
    manifest_artifact_count: Literal[891] = 891
    package_file_count: Literal[892] = 892
    independent_repeat_file_count: Literal[892] = 892
    repeat_hash_difference_count: Literal[0] = 0
    reviewer_visible_machine_label_count: Literal[0] = 0
    reviewer_visible_hidden_role_count: Literal[0] = 0
    reviewer_visible_attention_role_count: Literal[0] = 0
    dataset_sha256: Sha256
    sampling_ledger_sha256: Sha256
    restricted_evaluation_key_sha256: Sha256
    package_manifest_sha256: Sha256
    human_decisions_present: Literal[False] = False
    automatic_promotion_gate: Literal[GateStatus.BLOCKED] = GateStatus.BLOCKED

    @model_validator(mode="after")
    def validate_package(self) -> Stage1ReviewPackageSummary:
        require_stable_id(self.trial_id, kind="review")
        expected_units = (
            self.selected_conflict_site_count
            + self.selected_negative_pair_count
            + self.controlled_binding_hard_census_count
            + self.pedestrian_coverage_gap_census_count
        )
        if self.review_unit_count != expected_units:
            raise ValueError("Review-package unit counts do not close.")
        if self.package_file_count != self.manifest_artifact_count + 1:
            raise ValueError("Review-package file and manifest counts do not close.")
        return self


class Stage1MachineReviewReadyProvenance(ContractModel):
    schema_id: str = "torii.corridor.stage1m-machine-review-ready-provenance/v3"
    provenance_id: StableToken
    recorded_at: datetime
    status: Literal["review-ready"] = "review-ready"
    stage_1m_machine_review_ready_gate: Literal[GateStatus.PASS] = GateStatus.PASS
    stage_1h_human_validation_gate: Literal[GateStatus.BLOCKED] = GateStatus.BLOCKED
    automatic_promotion_gate: Literal[GateStatus.BLOCKED] = GateStatus.BLOCKED
    human_decisions_present: Literal[False] = False
    review_ready_is_not_stage1_exit: Literal[True] = True
    producer: CodeProducerIdentity
    machine_evidence_producer: CodeProducerIdentity
    review_package_producer: CodeProducerIdentity
    trial_id: StableToken
    artifacts: tuple[Stage1EvidenceArtifact, ...]
    dependencies: tuple[Stage1EvidenceDependency, ...]
    gates: tuple[Stage1GateEvidence, ...]
    snapshot: Stage1SnapshotSummary
    machine: Stage1MachineSummary
    pcb: Stage1PCBSummary
    rwc: Stage1RWCSummary
    coverage: Stage1CoverageSummary
    row_1: Stage1ROWSummary
    review_package: Stage1ReviewPackageSummary
    claims_not_supported: tuple[str, ...] = _CLAIMS_NOT_SUPPORTED

    def identity_payload(self) -> dict[str, object]:
        return self.model_dump(mode="json", by_alias=True, exclude={"provenance_id"})

    @model_validator(mode="after")
    def validate_provenance(self) -> Stage1MachineReviewReadyProvenance:
        require_stable_id(self.provenance_id, kind="manifest")
        require_stable_id(self.trial_id, kind="review")
        if self.recorded_at.tzinfo is None:
            raise ValueError("Stage 1-M provenance requires a timezone-aware timestamp.")
        if self.trial_id != self.review_package.trial_id:
            raise ValueError("Stage 1-M trial and review-package identities differ.")
        paths = [artifact.path for artifact in self.artifacts]
        names = [artifact.logical_name for artifact in self.artifacts]
        if paths != sorted(set(paths)) or len(names) != len(set(names)):
            raise ValueError("Stage 1-M artifacts must have sorted unique paths and names.")
        path_set = set(paths)
        edges: list[tuple[str, str]] = []
        for dependency in self.dependencies:
            if dependency.parent_path not in path_set or dependency.child_path not in path_set:
                raise ValueError("Stage 1-M dependency paths must resolve in the artifact DAG.")
            edges.append((dependency.parent_path, dependency.child_path))
        if len(edges) != len(set(edges)):
            raise ValueError("Stage 1-M artifact dependencies must be unique.")
        _require_acyclic(path_set, edges)
        gate_ids = tuple(gate.gate_id for gate in self.gates)
        if gate_ids != _REQUIRED_GATE_IDS:
            raise ValueError("Stage 1-M provenance requires the ten frozen gates in order.")
        for gate in self.gates:
            if not set(gate.evidence_paths) <= path_set:
                raise ValueError(f"Stage 1-M gate evidence is not in the artifact DAG: {gate.gate_id}.")
        if self.review_package.atomic_witness_population_count != self.rwc.effective_atomic_witness_count:
            raise ValueError("Review sampling does not bind the full RWC witness population.")
        if self.review_package.controlled_binding_hard_census_count != self.pcb.hard_structural_count:
            raise ValueError("Review sampling does not census all hard PCB findings.")
        if self.review_package.pedestrian_coverage_gap_census_count != self.coverage.effective_coverage_gap_count:
            raise ValueError("Review sampling does not census all coverage gaps.")
        if self.claims_not_supported != _CLAIMS_NOT_SUPPORTED:
            raise ValueError("Stage 1-M provenance cannot omit frozen non-claims.")
        if self.provenance_id != stable_id("manifest", self.identity_payload()):
            raise ValueError("Stage 1-M provenance ID does not match its frozen contents.")
        return self


def _require_acyclic(nodes: set[str], edges: list[tuple[str, str]]) -> None:
    children: dict[str, set[str]] = {node: set() for node in nodes}
    indegree = {node: 0 for node in nodes}
    for parent, child in edges:
        if child not in children[parent]:
            children[parent].add(child)
            indegree[child] += 1
    ready = sorted(node for node, degree in indegree.items() if degree == 0)
    visited = 0
    while ready:
        node = ready.pop(0)
        visited += 1
        for child in sorted(children[node]):
            indegree[child] -= 1
            if indegree[child] == 0:
                ready.append(child)
                ready.sort()
    if visited != len(nodes):
        raise ValueError("Stage 1-M artifact dependencies must form a DAG.")

