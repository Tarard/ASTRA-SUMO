from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field, model_validator

from .base import ContractModel, Sha256, StableToken
from .enums import GateStatus, TrafficSide
from .held_out_corpus_contracts import HeldOutCorridorSelection, Morphology
from .ids import require_stable_id, stable_id
from .run_identity import CodeProducerIdentity


AttentionLabel = Literal["attention-required", "acceptable", "ambiguous"]
ReplacementFailureReason = Literal[
    "source-artifact-unavailable",
    "bbox-outside-frozen-extract",
    "source-extraction-failed",
    "netconvert-build-failed",
    "semantic-replay-invalid",
    "artifact-closure-incomplete",
]

_REQUIRED_REPLACEMENT_MATCH_FIELDS = (
    "city_source_id",
    "morphology",
    "traffic_side",
    "mode_features",
)
_PROHIBITED_REPLACEMENT_SIGNALS = (
    "machine_label",
    "finding_count",
    "finding_severity",
    "human_decision",
    "reviewer_visibility",
)
_ALLOWED_REPLACEMENT_FAILURE_REASONS = (
    "source-artifact-unavailable",
    "bbox-outside-frozen-extract",
    "source-extraction-failed",
    "netconvert-build-failed",
    "semantic-replay-invalid",
    "artifact-closure-incomplete",
)


class ReplacementStratumV2(ContractModel):
    city_source_id: str
    morphology: Morphology
    traffic_side: TrafficSide
    mode_features: tuple[str, ...]

    @model_validator(mode="after")
    def validate_stratum(self) -> ReplacementStratumV2:
        if self.traffic_side is TrafficSide.UNKNOWN:
            raise ValueError("Replacement strata require an explicit traffic side.")
        if not self.city_source_id or not self.mode_features:
            raise ValueError("Replacement strata require source and mode features.")
        if len(self.mode_features) != len(set(self.mode_features)):
            raise ValueError("Replacement mode features must be unique.")
        return self


class ReserveCorridorCandidateV2(ContractModel):
    selection: HeldOutCorridorSelection
    traffic_side: TrafficSide
    preregistered_before_machine_execution: Literal[True] = True
    selection_uses_machine_output: Literal[False] = False

    @model_validator(mode="after")
    def validate_candidate(self) -> ReserveCorridorCandidateV2:
        if self.traffic_side is TrafficSide.UNKNOWN:
            raise ValueError("Reserve candidates require an explicit traffic side.")
        return self


class ReserveReplacementSlotV2(ContractModel):
    invalid_corridor_key: str
    invalid_selection_id: StableToken
    invalid_reason: Literal["semantic-replay-invalid"]
    required_stratum: ReplacementStratumV2
    candidates: tuple[ReserveCorridorCandidateV2, ...]

    @model_validator(mode="after")
    def validate_slot(self) -> ReserveReplacementSlotV2:
        require_stable_id(self.invalid_selection_id, kind="scope")
        if len(self.candidates) < 2:
            raise ValueError("Every replacement slot requires a reserve queue.")
        keys = [candidate.selection.corridor_key for candidate in self.candidates]
        ids = [candidate.selection.selection_id for candidate in self.candidates]
        if len(keys) != len(set(keys)) or len(ids) != len(set(ids)):
            raise ValueError("Reserve candidates must have unique identities.")
        for candidate in self.candidates:
            selection = candidate.selection
            if selection.city_source_id != self.required_stratum.city_source_id:
                raise ValueError("Reserve city source does not match replacement slot.")
            if selection.morphology != self.required_stratum.morphology:
                raise ValueError("Reserve morphology does not match replacement slot.")
            if candidate.traffic_side is not self.required_stratum.traffic_side:
                raise ValueError("Reserve traffic side does not match replacement slot.")
            if selection.preregistered_feature_targets != self.required_stratum.mode_features:
                raise ValueError("Reserve mode features do not match replacement slot.")
        return self


class HeldOutReserveCorpusV2(ContractModel):
    schema_id: str = "torii.corridor.held-out-reserve-corpus/v2"
    reserve_corpus_id: StableToken
    parent_corpus_sha256: Sha256
    frozen_before_replacement_execution: Literal[True] = True
    selection_independent_of_machine_results: Literal[True] = True
    provider_attribution: Literal["© OpenStreetMap contributors"]
    slots: tuple[ReserveReplacementSlotV2, ...]
    automatic_promotion_gate: GateStatus = GateStatus.BLOCKED

    def identity_payload(self) -> dict[str, object]:
        return self.model_dump(
            mode="json",
            by_alias=True,
            exclude={"reserve_corpus_id"},
        )

    @model_validator(mode="after")
    def validate_corpus(self) -> HeldOutReserveCorpusV2:
        require_stable_id(self.reserve_corpus_id, kind="manifest")
        if self.automatic_promotion_gate is not GateStatus.BLOCKED:
            raise ValueError("A reserve corpus cannot authorize promotion.")
        keys = [slot.invalid_corridor_key for slot in self.slots]
        if not keys or keys != sorted(keys) or len(keys) != len(set(keys)):
            raise ValueError("Reserve slots must be non-empty, sorted, and unique.")
        candidate_ids = [candidate.selection.selection_id for slot in self.slots for candidate in slot.candidates]
        if len(candidate_ids) != len(set(candidate_ids)):
            raise ValueError("Reserve candidate IDs must be globally unique.")
        if self.reserve_corpus_id != stable_id("manifest", self.identity_payload()):
            raise ValueError("Reserve corpus ID does not match frozen content.")
        return self


class HeldOutReplacementPolicyV2(ContractModel):
    schema_id: str = "torii.corridor.held-out-replacement-policy/v2"
    policy_id: StableToken
    parent_corpus_sha256: Sha256
    reserve_corpus_sha256: Sha256
    public_selection_seed: str = Field(min_length=32)
    ranking_algorithm: Literal["sha256(seed|invalid-corridor-key|selection-id)-ascending"]
    required_match_fields: tuple[str, ...]
    allowed_technical_failure_reasons: tuple[ReplacementFailureReason, ...]
    prohibited_selection_signals: tuple[str, ...]
    failed_attempts_are_retained: Literal[True] = True
    fallback_requires_prior_failure_record: Literal[True] = True
    replay_invalid_excluded_from_quality_denominator: Literal[True] = True
    minimum_complete_replacement_count: Literal[3] = 3
    automatic_promotion_gate: GateStatus = GateStatus.BLOCKED

    def identity_payload(self) -> dict[str, object]:
        return self.model_dump(mode="json", by_alias=True, exclude={"policy_id"})

    @model_validator(mode="after")
    def validate_policy(self) -> HeldOutReplacementPolicyV2:
        require_stable_id(self.policy_id, kind="policy")
        if self.required_match_fields != _REQUIRED_REPLACEMENT_MATCH_FIELDS:
            raise ValueError("Replacement matching fields are not the frozen tuple.")
        if self.allowed_technical_failure_reasons != _ALLOWED_REPLACEMENT_FAILURE_REASONS:
            raise ValueError("Replacement failure reasons are not the frozen tuple.")
        if self.prohibited_selection_signals != _PROHIBITED_REPLACEMENT_SIGNALS:
            raise ValueError("Replacement prohibited signals are not frozen.")
        if self.automatic_promotion_gate is not GateStatus.BLOCKED:
            raise ValueError("Replacement selection cannot authorize promotion.")
        if self.policy_id != stable_id("policy", self.identity_payload()):
            raise ValueError("Replacement policy ID does not match frozen content.")
        return self


class RankedReserveCandidateV2(ContractModel):
    rank: int = Field(ge=1)
    corridor_key: str
    selection_id: StableToken
    ranking_digest: Sha256

    @model_validator(mode="after")
    def validate_candidate(self) -> RankedReserveCandidateV2:
        require_stable_id(self.selection_id, kind="scope")
        return self


class ReplacementSlotPlanV2(ContractModel):
    invalid_corridor_key: str
    invalid_selection_id: StableToken
    ordered_candidates: tuple[RankedReserveCandidateV2, ...]

    @model_validator(mode="after")
    def validate_plan(self) -> ReplacementSlotPlanV2:
        require_stable_id(self.invalid_selection_id, kind="scope")
        ranks = [candidate.rank for candidate in self.ordered_candidates]
        if not ranks or ranks != list(range(1, len(ranks) + 1)):
            raise ValueError("Replacement candidate ranks must be contiguous.")
        digests = [candidate.ranking_digest for candidate in self.ordered_candidates]
        if digests != sorted(digests) or len(digests) != len(set(digests)):
            raise ValueError("Replacement candidates must follow unique digest order.")
        return self


class HeldOutReplacementPlanV2(ContractModel):
    schema_id: str = "torii.corridor.held-out-replacement-plan/v2"
    replacement_plan_id: StableToken
    reserve_corpus_sha256: Sha256
    replacement_policy_sha256: Sha256
    slots: tuple[ReplacementSlotPlanV2, ...]
    machine_labels_consulted: Literal[False] = False
    human_decisions_consulted: Literal[False] = False
    automatic_promotion_gate: GateStatus = GateStatus.BLOCKED

    def identity_payload(self) -> dict[str, object]:
        return self.model_dump(
            mode="json",
            by_alias=True,
            exclude={"replacement_plan_id"},
        )

    @model_validator(mode="after")
    def validate_plan(self) -> HeldOutReplacementPlanV2:
        require_stable_id(self.replacement_plan_id, kind="manifest")
        keys = [slot.invalid_corridor_key for slot in self.slots]
        if not keys or keys != sorted(keys) or len(keys) != len(set(keys)):
            raise ValueError("Replacement plan slots must be sorted and unique.")
        if self.automatic_promotion_gate is not GateStatus.BLOCKED:
            raise ValueError("Replacement plans cannot authorize promotion.")
        if self.replacement_plan_id != stable_id("manifest", self.identity_payload()):
            raise ValueError("Replacement plan ID does not match its queues.")
        return self


class HeldOutSourceSnapshotProtocolV2(ContractModel):
    schema_id: str = "torii.corridor.held-out-source-snapshot-protocol/v2"
    protocol_id: StableToken
    reference_complete_writer: Literal[True] = True
    referenced_node_tags_retained: Literal[True] = True
    closed_restriction_relations_only: Literal[True] = True
    touched_roundabout_component_closure: Literal[True] = True
    roundabout_component_connectivity: Literal["shared-osm-node"] = "shared-osm-node"
    disconnected_roundabout_components_excluded: Literal[True] = True
    frozen_v1_snapshots_rewritten: Literal[False] = False
    automatic_promotion_gate: GateStatus = GateStatus.BLOCKED

    def identity_payload(self) -> dict[str, object]:
        return self.model_dump(mode="json", by_alias=True, exclude={"protocol_id"})

    @model_validator(mode="after")
    def validate_protocol(self) -> HeldOutSourceSnapshotProtocolV2:
        require_stable_id(self.protocol_id, kind="policy")
        if self.automatic_promotion_gate is not GateStatus.BLOCKED:
            raise ValueError("Source snapshot protocols cannot authorize promotion.")
        if self.protocol_id != stable_id("policy", self.identity_payload()):
            raise ValueError("Source snapshot protocol ID does not match its content.")
        return self


class ReplacementEvidenceArtifactV2(ContractModel):
    role: Literal[
        "source-snapshot",
        "run-identity",
        "machine-report",
        "net-replay-report",
        "machine-manifest",
    ]
    path: str
    sha256: Sha256

    @model_validator(mode="after")
    def validate_path(self) -> ReplacementEvidenceArtifactV2:
        normalized = self.path.replace("\\", "/")
        if not normalized or normalized.startswith("/") or ".." in normalized.split("/"):
            raise ValueError("Replacement evidence paths must be safe and relative.")
        return self


class ReplacementAttemptV2(ContractModel):
    attempt_id: StableToken
    invalid_corridor_key: str
    rank: int = Field(ge=1)
    corridor_key: str
    selection_id: StableToken
    source_snapshot_protocol_sha256: Sha256
    producer: CodeProducerIdentity
    snapshot_status: GateStatus
    semantic_replay_status: GateStatus
    artifact_closure_complete: bool
    technical_outcome: Literal["eligible", "failed"]
    failure_reason: ReplacementFailureReason | None = None
    artifacts: tuple[ReplacementEvidenceArtifactV2, ...]
    machine_labels_consulted: Literal[False] = False
    finding_counts_consulted: Literal[False] = False
    finding_severity_consulted: Literal[False] = False
    human_decisions_consulted: Literal[False] = False
    reviewer_visibility_consulted: Literal[False] = False

    def identity_payload(self) -> dict[str, object]:
        return self.model_dump(mode="json", by_alias=True, exclude={"attempt_id"})

    @model_validator(mode="after")
    def validate_attempt(self) -> ReplacementAttemptV2:
        require_stable_id(self.attempt_id, kind="manifest")
        require_stable_id(self.selection_id, kind="scope")
        roles = [item.role for item in self.artifacts]
        required_roles = {
            "source-snapshot",
            "run-identity",
            "machine-report",
            "net-replay-report",
            "machine-manifest",
        }
        if set(roles) != required_roles or len(roles) != len(set(roles)):
            raise ValueError("Replacement attempts require one artifact per frozen role.")
        if self.technical_outcome == "eligible":
            if (
                self.snapshot_status is not GateStatus.PASS
                or self.semantic_replay_status is not GateStatus.PASS
                or not self.artifact_closure_complete
                or self.failure_reason is not None
            ):
                raise ValueError("Eligible replacements must close every technical gate.")
        elif self.failure_reason is None:
            raise ValueError("Failed replacement attempts require a technical reason.")
        if self.attempt_id != stable_id("manifest", self.identity_payload()):
            raise ValueError("Replacement attempt ID does not match frozen evidence.")
        return self


class ReplacementSlotAttemptLedgerV2(ContractModel):
    invalid_corridor_key: str
    invalid_selection_id: StableToken
    attempts: tuple[ReplacementAttemptV2, ...]
    selected_rank: int = Field(ge=1)
    selected_corridor_key: str
    selected_selection_id: StableToken
    resolution_status: Literal["technical-pass"] = "technical-pass"

    @model_validator(mode="after")
    def validate_slot(self) -> ReplacementSlotAttemptLedgerV2:
        require_stable_id(self.invalid_selection_id, kind="scope")
        require_stable_id(self.selected_selection_id, kind="scope")
        ranks = [item.rank for item in self.attempts]
        if not ranks or ranks != list(range(1, len(ranks) + 1)):
            raise ValueError("Replacement attempts must be a contiguous rank prefix.")
        if self.selected_rank != len(self.attempts):
            raise ValueError("Selection must be the final attempted rank.")
        selected = self.attempts[-1]
        if selected.technical_outcome != "eligible":
            raise ValueError("A resolved replacement slot must end in an eligible attempt.")
        if any(item.technical_outcome != "failed" for item in self.attempts[:-1]):
            raise ValueError("Every fallback requires all prior ranks to fail.")
        if selected.corridor_key != self.selected_corridor_key or selected.selection_id != self.selected_selection_id:
            raise ValueError("Selected replacement identity does not match final attempt.")
        if any(item.invalid_corridor_key != self.invalid_corridor_key for item in self.attempts):
            raise ValueError("Replacement slot attempts target different invalid cases.")
        return self


class SupersededReplacementExecutionV2(ContractModel):
    source_snapshot_protocol: Literal["bbox-intersection-without-roundabout-closure"]
    reason: Literal["source-snapshot-protocol-superseded"]
    machine_report_sha256s: tuple[Sha256, ...]
    eligible_for_current_selection: Literal[False] = False


class HeldOutReplacementAttemptLedgerV2(ContractModel):
    schema_id: str = "torii.corridor.held-out-replacement-attempt-ledger/v2"
    ledger_id: StableToken
    base_corpus_sha256: Sha256
    reserve_corpus_sha256: Sha256
    replacement_plan_sha256: Sha256
    source_snapshot_protocol_sha256: Sha256
    effective_corpus_sha256: Sha256
    slots: tuple[ReplacementSlotAttemptLedgerV2, ...]
    superseded_executions: tuple[SupersededReplacementExecutionV2, ...] = ()
    minimum_complete_replacement_count: Literal[3] = 3
    machine_labels_consulted: Literal[False] = False
    human_decisions_consulted: Literal[False] = False
    automatic_promotion_gate: GateStatus = GateStatus.BLOCKED

    def identity_payload(self) -> dict[str, object]:
        return self.model_dump(mode="json", by_alias=True, exclude={"ledger_id"})

    @model_validator(mode="after")
    def validate_ledger(self) -> HeldOutReplacementAttemptLedgerV2:
        require_stable_id(self.ledger_id, kind="manifest")
        keys = [slot.invalid_corridor_key for slot in self.slots]
        if (
            len(self.slots) < self.minimum_complete_replacement_count
            or keys != sorted(keys)
            or len(keys) != len(set(keys))
        ):
            raise ValueError("Replacement ledger does not close its frozen slots.")
        if self.automatic_promotion_gate is not GateStatus.BLOCKED:
            raise ValueError("Replacement evidence cannot authorize promotion.")
        if self.ledger_id != stable_id("manifest", self.identity_payload()):
            raise ValueError("Replacement ledger ID does not match frozen attempts.")
        return self


class ReviewWitnessSamplingPolicyV2(ContractModel):
    schema_id: str = "torii.corridor.review-witness-sampling-policy/v2"
    policy_id: StableToken
    rwc_policy_id: StableToken
    rwc_sampling_seed_sha256: Sha256
    atomic_membership_coverage_required: Literal[1.0] = 1.0
    lost_witness_maximum: Literal[0] = 0
    duplicate_witness_maximum: Literal[0] = 0
    hard_key_mixing_maximum: Literal[0] = 0
    rare_hard_ood_census_required: Literal[True] = True
    negative_pair_sampling_required: Literal[True] = True
    inclusion_probability_required: Literal[True] = True
    visible_medoid_and_extrema_required: Literal[True] = True
    hidden_random_member_required: Literal[True] = True
    cluster_decision_propagation_requires_purity: Literal[True] = True
    estimator: Literal["horvitz-thompson"] = "horvitz-thompson"
    confidence_method: Literal["wilson-one-sided"] = "wilson-one-sided"
    confidence_level: Literal[0.95] = 0.95
    maximum_hidden_member_disagreement_upper_bound: Literal[0.05] = 0.05
    maximum_safety_critical_hidden_heterogeneity: Literal[0] = 0
    automatic_promotion_gate: GateStatus = GateStatus.BLOCKED

    def identity_payload(self) -> dict[str, object]:
        return self.model_dump(mode="json", by_alias=True, exclude={"policy_id"})

    @model_validator(mode="after")
    def validate_policy(self) -> ReviewWitnessSamplingPolicyV2:
        require_stable_id(self.policy_id, kind="policy")
        require_stable_id(self.rwc_policy_id, kind="policy")
        if self.automatic_promotion_gate is not GateStatus.BLOCKED:
            raise ValueError("Sampling policy cannot authorize promotion.")
        if self.policy_id != stable_id("policy", self.identity_payload()):
            raise ValueError("Sampling policy ID does not match frozen content.")
        return self


class HeldOutReviewParentV2(ContractModel):
    schema_id: str = "torii.corridor.held-out-review-parent/v2"
    parent_id: StableToken
    base_benchmark_sha256: Sha256
    held_out_corpus_v1_sha256: Sha256
    reserve_corpus_sha256: Sha256
    replacement_policy_sha256: Sha256
    sampling_policy_sha256: Sha256
    lossless_compression_schema_sha256: Sha256
    frozen: Literal[True] = True
    review_ready_is_not_stage1_exit: Literal[True] = True
    automatic_promotion_gate: GateStatus = GateStatus.BLOCKED

    def identity_payload(self) -> dict[str, object]:
        return self.model_dump(mode="json", by_alias=True, exclude={"parent_id"})

    @model_validator(mode="after")
    def validate_parent(self) -> HeldOutReviewParentV2:
        require_stable_id(self.parent_id, kind="manifest")
        if self.automatic_promotion_gate is not GateStatus.BLOCKED:
            raise ValueError("Review parent cannot authorize promotion.")
        if self.parent_id != stable_id("manifest", self.identity_payload()):
            raise ValueError("Review parent ID does not match frozen content.")
        return self


class AuditAttentionCohortPolicyV2(ContractModel):
    cohort: Literal["audit-attention"] = "audit-attention"
    minimum_valid_corridor_packages: Literal[30] = 30
    minimum_raw_agreement: Literal[0.8] = 0.8
    minimum_cohen_kappa: Literal[0.6] = 0.6
    minimum_weighted_attention_precision: Literal[0.9] = 0.9
    minimum_weighted_attention_recall: Literal[0.95] = 0.95
    maximum_median_review_seconds: Literal[300.0] = 300.0
    maximum_safety_critical_false_negatives: Literal[0] = 0
    auto_precision_status: Literal["not-applicable"] = "not-applicable"
    finding_cluster_decisions_required: Literal[True] = True
    negative_units_required_for_recall: Literal[True] = True


class ProspectiveSafePassCohortPolicyV2(ContractModel):
    cohort: Literal["prospective-safe-pass"] = "prospective-safe-pass"
    enrollment: Literal["all-consecutive-machine-acceptable"] = "all-consecutive-machine-acceptable"
    retrospective_enrollment_forbidden: Literal[True] = True
    current_defect_only_corpus_eligible: Literal[False] = False
    minimum_machine_acceptable_count: Literal[600] = 600
    minimum_auto_precision_point_estimate: Literal[0.99] = 0.99
    minimum_auto_precision_one_sided_lower_bound: Literal[0.99] = 0.99
    confidence_method: Literal["wilson-one-sided"] = "wilson-one-sided"
    confidence_level: Literal[0.95] = 0.95
    auto_coverage_minimum: None = None
    maximum_safety_critical_false_negatives: Literal[0] = 0


class HeldOutReviewPolicyV2(ContractModel):
    schema_id: str = "torii.corridor.held-out-review-policy/v2"
    trial_id: StableToken
    parent_review_benchmark_sha256: Sha256
    reserve_corpus_sha256: Sha256
    replacement_policy_sha256: Sha256
    sampling_policy_sha256: Sha256
    blinding_seed_sha256: Sha256
    reviewer_ids: tuple[StableToken, StableToken]
    adjudicator_id: StableToken
    replay_invalid_corridor_keys: tuple[str, ...]
    audit_attention: AuditAttentionCohortPolicyV2
    prospective_safe_pass: ProspectiveSafePassCohortPolicyV2
    stage_1m_machine_milestone_only: Literal[True] = True
    stage_1_exit_requires_human_validation: Literal[True] = True
    machine_assessments_are_immutable_after_freeze: Literal[True] = True
    automatic_promotion_gate: GateStatus = GateStatus.BLOCKED

    def identity_payload(self) -> dict[str, object]:
        return self.model_dump(mode="json", by_alias=True, exclude={"trial_id"})

    @model_validator(mode="after")
    def validate_policy(self) -> HeldOutReviewPolicyV2:
        require_stable_id(self.trial_id, kind="review")
        if len(set(self.reviewer_ids)) != 2:
            raise ValueError("V2 review requires two distinct reviewers.")
        for reviewer_id in self.reviewer_ids:
            require_stable_id(reviewer_id, kind="review")
        require_stable_id(self.adjudicator_id, kind="review")
        if self.adjudicator_id in self.reviewer_ids:
            raise ValueError("V2 adjudicator must be independent.")
        if not self.replay_invalid_corridor_keys or self.replay_invalid_corridor_keys != tuple(
            sorted(set(self.replay_invalid_corridor_keys))
        ):
            raise ValueError("Replay-invalid keys must be sorted and unique.")
        if self.automatic_promotion_gate is not GateStatus.BLOCKED:
            raise ValueError("Stage 1-M policy must keep promotion blocked.")
        if self.trial_id != stable_id("review", self.identity_payload()):
            raise ValueError("V2 trial ID does not match frozen policy content.")
        return self


class ClusterMachineAssessmentV2(ContractModel):
    schema_id: str = "torii.corridor.cluster-machine-assessment/v2"
    review_unit_id: StableToken
    unit_kind: Literal["conflict-cluster", "negative-pair"]
    machine_attention: bool
    safety_critical: bool
    inclusion_probability: float = Field(gt=0.0, le=1.0)
    membership_root: Sha256
    evidence_artifact_sha256: Sha256
    finding_categories: tuple[str, ...]

    @model_validator(mode="after")
    def validate_assessment(self) -> ClusterMachineAssessmentV2:
        require_stable_id(self.review_unit_id, kind="review")
        if self.unit_kind == "conflict-cluster" and not self.machine_attention:
            raise ValueError("Conflict clusters are machine-attention units.")
        if self.unit_kind == "negative-pair" and self.machine_attention:
            raise ValueError("Negative-pair samples are non-attention units.")
        return self


class BlindedClusterReviewUnitV2(ContractModel):
    unit_code: str = Field(pattern=r"^unit-[0-9a-f]{12}$")
    witness_codes: tuple[str, ...]
    exact_question: str
    required_observations: tuple[str, ...]
    evidence_path: str

    @model_validator(mode="after")
    def validate_unit(self) -> BlindedClusterReviewUnitV2:
        if not self.witness_codes or len(self.witness_codes) != len(set(self.witness_codes)):
            raise ValueError("Blinded witness codes must be non-empty and unique.")
        if any(not code.startswith("witness-") or len(code) != 20 for code in self.witness_codes):
            raise ValueError("Invalid blinded witness code.")
        if not self.exact_question or not self.required_observations:
            raise ValueError("Cluster review units require a question and observations.")
        normalized = self.evidence_path.replace("\\", "/")
        if normalized.startswith("/") or ".." in normalized.split("/"):
            raise ValueError("Cluster evidence path must be safe and relative.")
        return self


class BlindedAttentionCaseV2(ContractModel):
    case_code: str = Field(pattern=r"^case-[0-9a-f]{12}$")
    city_group: str
    morphology: Morphology
    traffic_side: TrafficSide
    mode_features: tuple[str, ...]
    review_material_path: str
    units: tuple[BlindedClusterReviewUnitV2, ...]

    @model_validator(mode="after")
    def validate_case(self) -> BlindedAttentionCaseV2:
        if self.traffic_side is TrafficSide.UNKNOWN:
            raise ValueError("Blinded attention cases require a traffic side.")
        if not self.city_group or not self.mode_features or not self.units:
            raise ValueError("Blinded attention cases require strata and units.")
        unit_codes = [unit.unit_code for unit in self.units]
        if len(unit_codes) != len(set(unit_codes)):
            raise ValueError("Blinded attention unit codes must be unique per case.")
        normalized = self.review_material_path.replace("\\", "/")
        if normalized.startswith("/") or ".." in normalized.split("/"):
            raise ValueError("Review material path must be safe and relative.")
        return self


class BlindedAttentionDatasetV2(ContractModel):
    schema_id: str = "torii.corridor.blinded-attention-dataset/v2"
    trial_id: StableToken
    created_at: datetime
    machine_labels_hidden: Literal[True] = True
    peer_decisions_hidden: Literal[True] = True
    hidden_member_roles_hidden: Literal[True] = True
    cases: tuple[BlindedAttentionCaseV2, ...]

    @model_validator(mode="after")
    def validate_dataset(self) -> BlindedAttentionDatasetV2:
        require_stable_id(self.trial_id, kind="review")
        if self.created_at.tzinfo is None:
            raise ValueError("Blinded attention dataset requires a timezone.")
        case_codes = [case.case_code for case in self.cases]
        if not case_codes or len(case_codes) != len(set(case_codes)):
            raise ValueError("Blinded attention case codes must be unique.")
        all_unit_codes = [unit.unit_code for case in self.cases for unit in case.units]
        if len(all_unit_codes) != len(set(all_unit_codes)):
            raise ValueError("Blinded attention unit codes must be globally unique.")
        return self


class ClusterUnitUnblindingKeyV2(ContractModel):
    case_code: str = Field(pattern=r"^case-[0-9a-f]{12}$")
    unit_code: str = Field(pattern=r"^unit-[0-9a-f]{12}$")
    review_unit_id: StableToken
    witness_id_by_code: dict[str, StableToken]
    hidden_witness_code: str | None
    machine_assessment: ClusterMachineAssessmentV2
    machine_assessment_artifact_path: str
    machine_assessment_artifact_sha256: Sha256

    @model_validator(mode="after")
    def validate_key(self) -> ClusterUnitUnblindingKeyV2:
        require_stable_id(self.review_unit_id, kind="review")
        if self.machine_assessment.review_unit_id != self.review_unit_id:
            raise ValueError("Cluster assessment and key unit IDs differ.")
        for code, witness_id in self.witness_id_by_code.items():
            if not code.startswith("witness-") or len(code) != 20:
                raise ValueError("Invalid blinded witness code in key.")
            require_stable_id(witness_id, kind="conflict")
        if self.hidden_witness_code is not None and self.hidden_witness_code not in self.witness_id_by_code:
            raise ValueError("Hidden witness code is absent from the unit key.")
        normalized = self.machine_assessment_artifact_path.replace("\\", "/")
        if normalized.startswith("/") or ".." in normalized.split("/"):
            raise ValueError("Assessment artifact path must be safe and relative.")
        return self


class AttentionEvaluationKeyV2(ContractModel):
    schema_id: str = "torii.corridor.attention-evaluation-key/v2"
    trial_id: StableToken
    blinded_dataset_sha256: Sha256
    blinding_seed: str = Field(min_length=32)
    units: tuple[ClusterUnitUnblindingKeyV2, ...]

    @model_validator(mode="after")
    def validate_key(self) -> AttentionEvaluationKeyV2:
        require_stable_id(self.trial_id, kind="review")
        unit_codes = [unit.unit_code for unit in self.units]
        if not unit_codes or len(unit_codes) != len(set(unit_codes)):
            raise ValueError("Attention evaluation unit codes must be unique.")
        return self


class ClusterReviewDecisionV2(ContractModel):
    schema_id: str = "torii.corridor.cluster-review-decision/v2"
    decision_id: StableToken
    trial_id: StableToken
    case_code: str = Field(pattern=r"^case-[0-9a-f]{12}$")
    unit_code: str = Field(pattern=r"^unit-[0-9a-f]{12}$")
    reviewer_id: StableToken
    label: AttentionLabel
    witness_labels: dict[str, AttentionLabel]
    cluster_purity_supported: bool
    started_at: datetime
    decided_at: datetime
    observed_facts: tuple[str, ...]
    rationale: str
    machine_label_was_hidden: Literal[True] = True
    peer_decision_was_hidden: Literal[True] = True
    hidden_member_role_was_hidden: Literal[True] = True

    @property
    def duration_seconds(self) -> float:
        return (self.decided_at - self.started_at).total_seconds()

    @model_validator(mode="after")
    def validate_decision(self) -> ClusterReviewDecisionV2:
        require_stable_id(self.decision_id, kind="review")
        require_stable_id(self.trial_id, kind="review")
        require_stable_id(self.reviewer_id, kind="review")
        if self.started_at.tzinfo is None or self.decided_at.tzinfo is None:
            raise ValueError("Cluster review timestamps require timezones.")
        if self.decided_at <= self.started_at:
            raise ValueError("Cluster review end must follow start.")
        if not self.witness_labels or not self.observed_facts or not self.rationale:
            raise ValueError("Cluster review requires witness decisions and rationale.")
        return self


class ClusterReviewAdjudicationV2(ContractModel):
    schema_id: str = "torii.corridor.cluster-review-adjudication/v2"
    adjudication_id: StableToken
    trial_id: StableToken
    case_code: str = Field(pattern=r"^case-[0-9a-f]{12}$")
    unit_code: str = Field(pattern=r"^unit-[0-9a-f]{12}$")
    reviewer_decision_ids: tuple[StableToken, StableToken]
    adjudicator_id: StableToken
    final_label: AttentionLabel
    witness_labels: dict[str, AttentionLabel]
    cluster_purity_supported: bool
    decided_at: datetime
    observed_facts: tuple[str, ...]
    rationale: str

    @model_validator(mode="after")
    def validate_adjudication(self) -> ClusterReviewAdjudicationV2:
        require_stable_id(self.adjudication_id, kind="review")
        require_stable_id(self.trial_id, kind="review")
        require_stable_id(self.adjudicator_id, kind="review")
        if len(set(self.reviewer_decision_ids)) != 2:
            raise ValueError("Cluster adjudication requires two decisions.")
        for decision_id in self.reviewer_decision_ids:
            require_stable_id(decision_id, kind="review")
        if self.decided_at.tzinfo is None:
            raise ValueError("Cluster adjudication requires a timezone.")
        if not self.witness_labels or not self.observed_facts or not self.rationale:
            raise ValueError("Cluster adjudication requires evidence and rationale.")
        return self


class HeldOutReviewV2Metrics(ContractModel):
    valid_corridor_package_count: int = Field(ge=0)
    reproducibility_only_case_count: int = Field(ge=0)
    completed_cluster_review_count: int = Field(ge=0)
    raw_agreement: float | None
    cohen_kappa: float | None
    median_review_seconds: float | None
    weighted_attention_precision: float | None
    weighted_attention_recall: float | None
    safety_critical_false_negative_count: int = Field(ge=0)
    hidden_member_disagreement_count: int = Field(ge=0)
    hidden_member_disagreement_upper_bound: float | None
    auto_precision_status: Literal["not-applicable", "prospective"]
    auto_precision: float | None
    auto_precision_one_sided_lower_bound: float | None
    auto_coverage: float | None


class HeldOutReviewV2Report(ContractModel):
    schema_id: str = "torii.corridor.held-out-review-report/v2"
    trial_id: StableToken
    policy_sha256: Sha256
    parent_review_benchmark_sha256: Sha256
    status: GateStatus
    stage_1m_machine_review_ready_gate: GateStatus
    stage_1h_human_validation_gate: GateStatus
    automatic_promotion_gate: GateStatus = GateStatus.BLOCKED
    metrics: HeldOutReviewV2Metrics
    blockers: tuple[str, ...]

    @model_validator(mode="after")
    def validate_report(self) -> HeldOutReviewV2Report:
        require_stable_id(self.trial_id, kind="review")
        if self.automatic_promotion_gate is not GateStatus.BLOCKED:
            raise ValueError("V2 reports cannot authorize automatic promotion.")
        if self.stage_1h_human_validation_gate is GateStatus.PASS and self.status is not GateStatus.PASS:
            raise ValueError("Human validation cannot pass a blocked trial.")
        return self


class HeldOutReviewV2ContractBundle(ContractModel):
    schema_id: str = "torii.corridor.held-out-review-contract-bundle/v2"
    parent: HeldOutReviewParentV2
    reserve_corpus: HeldOutReserveCorpusV2
    replacement_policy: HeldOutReplacementPolicyV2
    replacement_plan: HeldOutReplacementPlanV2
    sampling_policy: ReviewWitnessSamplingPolicyV2
    review_policy: HeldOutReviewPolicyV2
    blinded_dataset: BlindedAttentionDatasetV2 | None = None
    evaluation_key: AttentionEvaluationKeyV2 | None = None
    decisions: tuple[ClusterReviewDecisionV2, ...] = ()
    adjudications: tuple[ClusterReviewAdjudicationV2, ...] = ()
    report: HeldOutReviewV2Report | None = None


class ReviewStudySamplingPolicyV2R2(ContractModel):
    schema_id: str = "torii.corridor.review-study-sampling-policy/v2-r2"
    policy_id: StableToken
    parent_sampling_policy_sha256: Sha256
    review_unit: Literal["pedestrian-conflict-site"] = "pedestrian-conflict-site"
    conflict_site_stratum_fields: tuple[str, ...] = (
        "certainty-profile",
        "grade-risk-profile",
    )
    target_conflict_sites_per_corridor: Literal[8] = 8
    target_negative_pairs_per_corridor: Literal[4] = 4
    controlled_binding_hard_classes_census: tuple[str, ...] = (
        "ordinary-program-truly-absent",
        "program-present-link-invalid",
    )
    pedestrian_coverage_gaps_census: Literal[True] = True
    atomic_witness_machine_census_required: Literal[True] = True
    rare_hard_ood_machine_census_required: Literal[True] = True
    unknown_control_population_must_be_retained: Literal[True] = True
    unknown_control_does_not_force_human_member_census: Literal[True] = True
    unselected_population_remains_unresolved: Literal[True] = True
    site_sampling_algorithm: Literal["sha256-stratified-with-minimum-one-per-observed-stratum"] = (
        "sha256-stratified-with-minimum-one-per-observed-stratum"
    )
    negative_sampling_algorithm: Literal["sha256-round-robin-over-existing-negative-strata"] = (
        "sha256-round-robin-over-existing-negative-strata"
    )
    inclusion_probability_required: Literal[True] = True
    estimator: Literal["horvitz-thompson"] = "horvitz-thompson"
    automatic_promotion_gate: GateStatus = GateStatus.BLOCKED

    def identity_payload(self) -> dict[str, object]:
        return self.model_dump(mode="json", by_alias=True, exclude={"policy_id"})

    @model_validator(mode="after")
    def validate_policy(self) -> ReviewStudySamplingPolicyV2R2:
        require_stable_id(self.policy_id, kind="policy")
        if self.automatic_promotion_gate is not GateStatus.BLOCKED:
            raise ValueError("Study sampling cannot authorize promotion.")
        if self.policy_id != stable_id("policy", self.identity_payload()):
            raise ValueError("Study sampling policy ID does not match content.")
        return self


class HeldOutReviewExecutionParentV2R2(ContractModel):
    schema_id: str = "torii.corridor.held-out-review-execution-parent/v2-r2"
    parent_id: StableToken
    base_review_parent_sha256: Sha256
    base_review_policy_sha256: Sha256
    effective_corpus_sha256: Sha256
    replacement_attempt_ledger_sha256: Sha256
    source_snapshot_protocol_sha256: Sha256
    snapshot_report_sha256: Sha256
    snapshot_manifest_sha256: Sha256
    machine_run_identity_sha256: Sha256
    machine_report_sha256: Sha256
    machine_manifest_sha256: Sha256
    producer: CodeProducerIdentity
    valid_corridor_package_count: Literal[30] = 30
    pipeline_pass_count: Literal[30] = 30
    semantic_replay_pass_count: Literal[30] = 30
    machine_assessments_frozen: Literal[True] = True
    human_decisions_present: Literal[False] = False
    review_ready_is_not_stage1_exit: Literal[True] = True
    automatic_promotion_gate: GateStatus = GateStatus.BLOCKED

    def identity_payload(self) -> dict[str, object]:
        return self.model_dump(mode="json", by_alias=True, exclude={"parent_id"})

    @model_validator(mode="after")
    def validate_parent(self) -> HeldOutReviewExecutionParentV2R2:
        require_stable_id(self.parent_id, kind="manifest")
        if self.automatic_promotion_gate is not GateStatus.BLOCKED:
            raise ValueError("Review execution parents cannot authorize promotion.")
        if self.parent_id != stable_id("manifest", self.identity_payload()):
            raise ValueError("Review execution parent ID does not match evidence.")
        return self


class HeldOutReviewTrialInstanceV2R2(ContractModel):
    schema_id: str = "torii.corridor.held-out-review-trial-instance/v2-r2"
    trial_id: StableToken
    base_review_policy_sha256: Sha256
    execution_parent_sha256: Sha256
    study_sampling_policy_sha256: Sha256
    blinding_seed_sha256: Sha256
    predecessor_trial_id: StableToken
    successor_reason: Literal["predecessor-seed-preimage-unavailable-and-source-protocol-corrected"]
    predecessor_trial_executed: Literal[False] = False
    thresholds_inherited_without_change: Literal[True] = True
    seed_generated_once_before_sampling: Literal[True] = True
    sampling_not_executed_before_freeze: Literal[True] = True
    machine_labels_consulted_for_sampling: Literal[False] = False
    finding_counts_consulted_for_sampling: Literal[False] = False
    human_decisions_present: Literal[False] = False
    stage_1m_machine_milestone_only: Literal[True] = True
    stage_1_exit_requires_human_validation: Literal[True] = True
    automatic_promotion_gate: GateStatus = GateStatus.BLOCKED

    def identity_payload(self) -> dict[str, object]:
        return self.model_dump(mode="json", by_alias=True, exclude={"trial_id"})

    @model_validator(mode="after")
    def validate_trial(self) -> HeldOutReviewTrialInstanceV2R2:
        require_stable_id(self.trial_id, kind="review")
        require_stable_id(self.predecessor_trial_id, kind="review")
        if self.automatic_promotion_gate is not GateStatus.BLOCKED:
            raise ValueError("Review trial instances cannot authorize promotion.")
        if self.trial_id != stable_id("review", self.identity_payload()):
            raise ValueError("Review trial instance ID does not match content.")
        return self


ReviewUnitKindV2R2 = Literal[
    "conflict-site",
    "negative-pair",
    "controlled-binding",
    "pedestrian-coverage-gap",
]

ReviewDomainV2R2 = Literal[
    "pedestrian-path-relation",
    "signal-control-configuration",
    "pedestrian-facility-coverage",
]


class BlindedReviewUnitV2R2(ContractModel):
    unit_code: str = Field(pattern=r"^unit-[0-9a-f]{12}$")
    review_domain: ReviewDomainV2R2
    witness_codes: tuple[str, ...]
    exact_question: str
    required_observations: tuple[str, ...]
    evidence_path: str

    @model_validator(mode="after")
    def validate_unit(self) -> BlindedReviewUnitV2R2:
        if (
            not self.witness_codes
            or len(self.witness_codes) != len(set(self.witness_codes))
            or any(not code.startswith("witness-") or len(code) != 20 for code in self.witness_codes)
        ):
            raise ValueError("Blinded review units require unique witness codes.")
        if not self.exact_question or not self.required_observations:
            raise ValueError("Blinded review units require a question and observations.")
        _require_safe_relative_path(self.evidence_path)
        return self


class BlindedAttentionCaseV2R2(ContractModel):
    case_code: str = Field(pattern=r"^case-[0-9a-f]{12}$")
    city_group: str
    morphology: Morphology
    traffic_side: TrafficSide
    mode_features: tuple[str, ...]
    review_material_path: str
    units: tuple[BlindedReviewUnitV2R2, ...]

    @model_validator(mode="after")
    def validate_case(self) -> BlindedAttentionCaseV2R2:
        if self.traffic_side is TrafficSide.UNKNOWN:
            raise ValueError("Blinded attention cases require a traffic side.")
        if not self.city_group or not self.mode_features or not self.units:
            raise ValueError("Blinded attention cases require strata and units.")
        codes = [unit.unit_code for unit in self.units]
        if len(codes) != len(set(codes)):
            raise ValueError("Blinded unit codes must be unique per case.")
        _require_safe_relative_path(self.review_material_path)
        return self


class BlindedAttentionDatasetV2R2(ContractModel):
    schema_id: str = "torii.corridor.blinded-attention-dataset/v2-r2"
    trial_id: StableToken
    created_at: datetime
    machine_labels_hidden: Literal[True] = True
    peer_decisions_hidden: Literal[True] = True
    hidden_member_roles_hidden: Literal[True] = True
    cases: tuple[BlindedAttentionCaseV2R2, ...]

    @model_validator(mode="after")
    def validate_dataset(self) -> BlindedAttentionDatasetV2R2:
        require_stable_id(self.trial_id, kind="review")
        if self.created_at.tzinfo is None:
            raise ValueError("Blinded datasets require a timezone.")
        case_codes = [case.case_code for case in self.cases]
        unit_codes = [unit.unit_code for case in self.cases for unit in case.units]
        if len(self.cases) != 30 or len(case_codes) != len(set(case_codes)):
            raise ValueError("V2-R2 datasets require 30 unique corridor packages.")
        if len(unit_codes) != len(set(unit_codes)):
            raise ValueError("Blinded unit codes must be globally unique.")
        return self


class ReviewUnitMachineAssessmentV2R2(ContractModel):
    review_unit_id: StableToken
    unit_kind: ReviewUnitKindV2R2
    machine_attention: bool
    safety_critical: bool
    inclusion_probability: float = Field(gt=0.0, le=1.0)
    membership_root: Sha256
    evidence_artifact_sha256: Sha256
    finding_categories: tuple[str, ...]

    @model_validator(mode="after")
    def validate_assessment(self) -> ReviewUnitMachineAssessmentV2R2:
        require_stable_id(self.review_unit_id, kind="review")
        if self.machine_attention != (self.unit_kind != "negative-pair"):
            raise ValueError("Machine attention contradicts the review-unit kind.")
        return self


class ReviewUnitUnblindingKeyV2R2(ContractModel):
    case_code: str = Field(pattern=r"^case-[0-9a-f]{12}$")
    unit_code: str = Field(pattern=r"^unit-[0-9a-f]{12}$")
    review_unit_id: StableToken
    evidence_id_by_witness_code: dict[str, StableToken]
    hidden_witness_code: str | None
    machine_assessment: ReviewUnitMachineAssessmentV2R2
    machine_assessment_artifact_path: str
    machine_assessment_artifact_sha256: Sha256

    @model_validator(mode="after")
    def validate_key(self) -> ReviewUnitUnblindingKeyV2R2:
        require_stable_id(self.review_unit_id, kind="review")
        if self.machine_assessment.review_unit_id != self.review_unit_id:
            raise ValueError("Unit key and machine assessment identities differ.")
        if not self.evidence_id_by_witness_code:
            raise ValueError("Unit keys require evidence mappings.")
        for code in self.evidence_id_by_witness_code:
            if not code.startswith("witness-") or len(code) != 20:
                raise ValueError("Invalid witness code in unit key.")
        if self.hidden_witness_code is not None and self.hidden_witness_code not in self.evidence_id_by_witness_code:
            raise ValueError("Hidden witness code is absent from the unit key.")
        _require_safe_relative_path(self.machine_assessment_artifact_path)
        return self


class AttentionEvaluationKeyV2R2(ContractModel):
    schema_id: str = "torii.corridor.attention-evaluation-key/v2-r2"
    trial_id: StableToken
    blinded_dataset_sha256: Sha256
    blinding_seed: str = Field(min_length=32)
    units: tuple[ReviewUnitUnblindingKeyV2R2, ...]

    @model_validator(mode="after")
    def validate_key(self) -> AttentionEvaluationKeyV2R2:
        require_stable_id(self.trial_id, kind="review")
        codes = [unit.unit_code for unit in self.units]
        if not codes or len(codes) != len(set(codes)):
            raise ValueError("Evaluation unit codes must be unique.")
        return self


class ReviewUnitDecisionV2R2(ContractModel):
    schema_id: str = "torii.corridor.review-unit-decision/v2-r2"
    decision_id: StableToken
    trial_id: StableToken
    case_code: str = Field(pattern=r"^case-[0-9a-f]{12}$")
    unit_code: str = Field(pattern=r"^unit-[0-9a-f]{12}$")
    reviewer_id: StableToken
    label: AttentionLabel
    witness_labels: dict[str, AttentionLabel]
    within_unit_homogeneity_supported: bool | None
    started_at: datetime
    decided_at: datetime
    observed_facts: tuple[str, ...]
    rationale: str
    machine_label_was_hidden: Literal[True] = True
    peer_decision_was_hidden: Literal[True] = True
    hidden_member_role_was_hidden: Literal[True] = True

    @model_validator(mode="after")
    def validate_decision(self) -> ReviewUnitDecisionV2R2:
        require_stable_id(self.decision_id, kind="review")
        require_stable_id(self.trial_id, kind="review")
        require_stable_id(self.reviewer_id, kind="review")
        if self.started_at.tzinfo is None or self.decided_at.tzinfo is None:
            raise ValueError("Review-unit decisions require timezone-aware timestamps.")
        if self.decided_at <= self.started_at:
            raise ValueError("Review-unit decision end must follow its start.")
        if (
            not self.witness_labels
            or any(not code.startswith("witness-") or len(code) != 20 for code in self.witness_labels)
            or not self.observed_facts
            or not self.rationale
        ):
            raise ValueError("Review-unit decisions require witness labels and rationale.")
        return self


class ReviewUnitAdjudicationV2R2(ContractModel):
    schema_id: str = "torii.corridor.review-unit-adjudication/v2-r2"
    adjudication_id: StableToken
    trial_id: StableToken
    case_code: str = Field(pattern=r"^case-[0-9a-f]{12}$")
    unit_code: str = Field(pattern=r"^unit-[0-9a-f]{12}$")
    reviewer_decision_ids: tuple[StableToken, StableToken]
    adjudicator_id: StableToken
    final_label: AttentionLabel
    witness_labels: dict[str, AttentionLabel]
    within_unit_homogeneity_supported: bool | None
    decided_at: datetime
    observed_facts: tuple[str, ...]
    rationale: str

    @model_validator(mode="after")
    def validate_adjudication(self) -> ReviewUnitAdjudicationV2R2:
        require_stable_id(self.adjudication_id, kind="review")
        require_stable_id(self.trial_id, kind="review")
        require_stable_id(self.adjudicator_id, kind="review")
        if len(set(self.reviewer_decision_ids)) != 2:
            raise ValueError("Review-unit adjudication requires two independent decisions.")
        for decision_id in self.reviewer_decision_ids:
            require_stable_id(decision_id, kind="review")
        if self.decided_at.tzinfo is None:
            raise ValueError("Review-unit adjudications require a timezone.")
        if (
            not self.witness_labels
            or any(not code.startswith("witness-") or len(code) != 20 for code in self.witness_labels)
            or not self.observed_facts
            or not self.rationale
        ):
            raise ValueError("Review-unit adjudications require evidence and rationale.")
        return self


class ReviewPackageArtifactV2R2(ContractModel):
    path: str
    sha256: Sha256
    visibility: Literal["reviewer-visible", "restricted"]

    @model_validator(mode="after")
    def validate_artifact(self) -> ReviewPackageArtifactV2R2:
        _require_safe_relative_path(self.path)
        return self


class ReviewSamplingStratumV2R2(ContractModel):
    stratum_id: StableToken
    corridor_key: str
    unit_kind: Literal["conflict-site", "negative-pair"]
    stratum_key: tuple[str, ...]
    selection_source: Literal[
        "full-population",
        "preselected-probability-sample",
    ]
    population_count: int = Field(ge=1)
    available_sample_count: int = Field(ge=1)
    selected_count: int = Field(ge=1)
    inclusion_probability: float = Field(gt=0.0, le=1.0)

    @model_validator(mode="after")
    def validate_stratum(self) -> ReviewSamplingStratumV2R2:
        require_stable_id(self.stratum_id, kind="scope")
        if not self.corridor_key or not self.stratum_key:
            raise ValueError("Review sampling strata require a corridor and key.")
        if self.available_sample_count > self.population_count:
            raise ValueError("Available review sample exceeds its population.")
        if self.selected_count > self.available_sample_count:
            raise ValueError("Selected review sample exceeds available evidence.")
        expected_probability = self.selected_count / self.population_count
        if abs(self.inclusion_probability - expected_probability) > 1e-12:
            raise ValueError("Review sampling inclusion probability is incorrect.")
        expected_id = stable_id(
            "scope",
            {
                "corridor_key": self.corridor_key,
                "unit_kind": self.unit_kind,
                "stratum_key": self.stratum_key,
                "selection_source": self.selection_source,
                "population_count": self.population_count,
                "available_sample_count": self.available_sample_count,
            },
        )
        if self.stratum_id != expected_id:
            raise ValueError("Review sampling stratum ID does not match its population.")
        return self


class ReviewSamplingCorridorV2R2(ContractModel):
    corridor_key: str
    atomic_witness_population_count: int = Field(ge=0)
    conflict_site_population_count: int = Field(ge=0)
    selected_conflict_site_count: int = Field(ge=0)
    negative_pair_population_count: int = Field(ge=0)
    selected_negative_pair_count: int = Field(ge=0)
    controlled_binding_hard_count: int = Field(ge=0)
    selected_controlled_binding_count: int = Field(ge=0)
    pedestrian_coverage_gap_count: int = Field(ge=0)
    selected_pedestrian_coverage_gap_count: int = Field(ge=0)
    independent_hidden_witness_count: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_corridor(self) -> ReviewSamplingCorridorV2R2:
        if not self.corridor_key:
            raise ValueError("Review sampling corridor keys cannot be empty.")
        if self.selected_conflict_site_count > self.conflict_site_population_count:
            raise ValueError("Selected conflict sites exceed the corridor population.")
        if self.selected_negative_pair_count > self.negative_pair_population_count:
            raise ValueError("Selected negative pairs exceed the corridor population.")
        if self.selected_controlled_binding_count != self.controlled_binding_hard_count:
            raise ValueError("Hard controlled bindings require census selection.")
        if self.selected_pedestrian_coverage_gap_count != self.pedestrian_coverage_gap_count:
            raise ValueError("Pedestrian coverage gaps require census selection.")
        if self.independent_hidden_witness_count > self.selected_conflict_site_count:
            raise ValueError("Hidden witness count exceeds selected conflict sites.")
        return self


class ReviewSamplingLedgerV2R2(ContractModel):
    schema_id: str = "torii.corridor.review-sampling-ledger/v2-r2"
    ledger_id: StableToken
    trial_id: StableToken
    execution_parent_sha256: Sha256
    study_sampling_policy_sha256: Sha256
    blinding_seed_sha256: Sha256
    producer: CodeProducerIdentity
    corridors: tuple[ReviewSamplingCorridorV2R2, ...]
    strata: tuple[ReviewSamplingStratumV2R2, ...]
    atomic_witness_population_count: int = Field(ge=0)
    conflict_site_population_count: int = Field(ge=0)
    selected_conflict_site_count: int = Field(ge=0)
    negative_pair_population_count: int = Field(ge=0)
    selected_negative_pair_count: int = Field(ge=0)
    controlled_binding_hard_count: int = Field(ge=0)
    pedestrian_coverage_gap_count: int = Field(ge=0)
    independent_hidden_witness_count: int = Field(ge=0)
    atomic_witness_machine_census_complete: Literal[True] = True
    unselected_population_retained_and_unresolved: Literal[True] = True
    machine_labels_consulted_for_sampling: Literal[False] = False
    finding_counts_consulted_for_sampling: Literal[False] = False
    human_decisions_present: Literal[False] = False
    automatic_promotion_gate: GateStatus = GateStatus.BLOCKED

    def identity_payload(self) -> dict[str, object]:
        return self.model_dump(mode="json", by_alias=True, exclude={"ledger_id"})

    @model_validator(mode="after")
    def validate_ledger(self) -> ReviewSamplingLedgerV2R2:
        require_stable_id(self.ledger_id, kind="manifest")
        require_stable_id(self.trial_id, kind="review")
        corridor_keys = [corridor.corridor_key for corridor in self.corridors]
        if len(self.corridors) != 30 or corridor_keys != sorted(set(corridor_keys)):
            raise ValueError("Review sampling ledgers require 30 sorted unique corridors.")
        stratum_ids = [stratum.stratum_id for stratum in self.strata]
        if stratum_ids != sorted(set(stratum_ids)):
            raise ValueError("Review sampling strata must be sorted and unique.")
        totals = {
            "atomic_witness_population_count": sum(
                corridor.atomic_witness_population_count for corridor in self.corridors
            ),
            "conflict_site_population_count": sum(
                corridor.conflict_site_population_count for corridor in self.corridors
            ),
            "selected_conflict_site_count": sum(
                corridor.selected_conflict_site_count for corridor in self.corridors
            ),
            "negative_pair_population_count": sum(
                corridor.negative_pair_population_count for corridor in self.corridors
            ),
            "selected_negative_pair_count": sum(
                corridor.selected_negative_pair_count for corridor in self.corridors
            ),
            "controlled_binding_hard_count": sum(
                corridor.controlled_binding_hard_count for corridor in self.corridors
            ),
            "pedestrian_coverage_gap_count": sum(
                corridor.pedestrian_coverage_gap_count for corridor in self.corridors
            ),
            "independent_hidden_witness_count": sum(
                corridor.independent_hidden_witness_count for corridor in self.corridors
            ),
        }
        for field, expected in totals.items():
            if getattr(self, field) != expected:
                raise ValueError(f"Review sampling ledger total does not close: {field}.")
        if self.automatic_promotion_gate is not GateStatus.BLOCKED:
            raise ValueError("Review sampling ledgers cannot authorize promotion.")
        if self.ledger_id != stable_id("manifest", self.identity_payload()):
            raise ValueError("Review sampling ledger ID does not match its contents.")
        return self


class HeldOutReviewPackageManifestV2R2(ContractModel):
    schema_id: str = "torii.corridor.held-out-review-package-manifest/v2-r2"
    trial_id: StableToken
    trial_instance_sha256: Sha256
    execution_parent_sha256: Sha256
    study_sampling_policy_sha256: Sha256
    producer: CodeProducerIdentity
    dataset_path: str
    dataset_sha256: Sha256
    evaluation_key_path: str
    evaluation_key_sha256: Sha256
    sampling_ledger_path: str
    sampling_ledger_sha256: Sha256
    reviewer_visible_machine_label_count: Literal[0] = 0
    reviewer_visible_hidden_role_count: Literal[0] = 0
    reviewer_visible_attention_role_count: Literal[0] = 0
    machine_labels_consulted_for_sampling: Literal[False] = False
    finding_counts_consulted_for_sampling: Literal[False] = False
    human_decisions_present: Literal[False] = False
    artifacts: tuple[ReviewPackageArtifactV2R2, ...]
    automatic_promotion_gate: GateStatus = GateStatus.BLOCKED

    @model_validator(mode="after")
    def validate_manifest(self) -> HeldOutReviewPackageManifestV2R2:
        require_stable_id(self.trial_id, kind="review")
        _require_safe_relative_path(self.dataset_path)
        _require_safe_relative_path(self.evaluation_key_path)
        _require_safe_relative_path(self.sampling_ledger_path)
        by_path = {item.path: item.sha256 for item in self.artifacts}
        if len(by_path) != len(self.artifacts):
            raise ValueError("Review package artifact paths must be unique.")
        if by_path.get(self.dataset_path) != self.dataset_sha256:
            raise ValueError("Review package does not bind its dataset.")
        if by_path.get(self.evaluation_key_path) != self.evaluation_key_sha256:
            raise ValueError("Review package does not bind its evaluation key.")
        if by_path.get(self.sampling_ledger_path) != self.sampling_ledger_sha256:
            raise ValueError("Review package does not bind its sampling ledger.")
        if self.automatic_promotion_gate is not GateStatus.BLOCKED:
            raise ValueError("Review packages cannot authorize promotion.")
        return self


def _require_safe_relative_path(value: str) -> None:
    normalized = value.replace("\\", "/")
    if not normalized or normalized.startswith("/") or ".." in normalized.split("/"):
        raise ValueError("Artifact paths must be safe and relative.")
