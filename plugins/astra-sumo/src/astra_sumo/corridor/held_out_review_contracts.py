from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field, model_validator

from .base import ContractModel, Sha256, StableToken
from .enums import GateStatus, TrafficSide
from .ids import require_stable_id, stable_id


ReviewLabel = Literal["defect", "acceptable", "ambiguous"]


class HeldOutReviewPolicy(ContractModel):
    schema_id: str = "torii.corridor.held-out-review-policy/v1"
    trial_id: StableToken
    parent_benchmark_sha256: Sha256
    reviewer_ids: tuple[StableToken, StableToken]
    adjudicator_id: StableToken
    minimum_case_count: int = Field(ge=2)
    minimum_city_group_count: int = Field(ge=2)
    minimum_morphology_count: int = Field(ge=2)
    minimum_cases_per_city_group: int = Field(ge=1)
    required_traffic_sides: tuple[TrafficSide, ...]
    required_mode_features: tuple[str, ...]
    minimum_raw_agreement: float = Field(ge=0.0, le=1.0)
    minimum_cohen_kappa: float = Field(ge=-1.0, le=1.0)
    minimum_attention_precision: float = Field(ge=0.0, le=1.0)
    minimum_attention_recall: float = Field(ge=0.0, le=1.0)
    minimum_auto_precision: float = Field(ge=0.0, le=1.0)
    maximum_median_review_seconds: float = Field(gt=0.0)
    maximum_safety_critical_false_negatives: Literal[0] = 0

    def identity_payload(self) -> dict[str, object]:
        return {
            "parent_benchmark_sha256": self.parent_benchmark_sha256,
            "reviewer_ids": self.reviewer_ids,
            "adjudicator_id": self.adjudicator_id,
            "minimum_case_count": self.minimum_case_count,
            "minimum_city_group_count": self.minimum_city_group_count,
            "minimum_morphology_count": self.minimum_morphology_count,
            "minimum_cases_per_city_group": self.minimum_cases_per_city_group,
            "required_traffic_sides": self.required_traffic_sides,
            "required_mode_features": self.required_mode_features,
            "minimum_raw_agreement": self.minimum_raw_agreement,
            "minimum_cohen_kappa": self.minimum_cohen_kappa,
            "minimum_attention_precision": self.minimum_attention_precision,
            "minimum_attention_recall": self.minimum_attention_recall,
            "minimum_auto_precision": self.minimum_auto_precision,
            "maximum_median_review_seconds": self.maximum_median_review_seconds,
            "maximum_safety_critical_false_negatives": (
                self.maximum_safety_critical_false_negatives
            ),
        }

    @model_validator(mode="after")
    def validate_policy(self) -> HeldOutReviewPolicy:
        require_stable_id(self.trial_id, kind="review")
        if self.trial_id != stable_id("review", self.identity_payload()):
            raise ValueError("trial_id does not match the held-out review policy.")
        if len(set(self.reviewer_ids)) != 2:
            raise ValueError("Held-out review requires two distinct reviewer IDs.")
        for reviewer_id in self.reviewer_ids:
            require_stable_id(reviewer_id, kind="review")
        require_stable_id(self.adjudicator_id, kind="review")
        if self.adjudicator_id in self.reviewer_ids:
            raise ValueError("The adjudicator must be independent of both reviewers.")
        if not self.required_traffic_sides or TrafficSide.UNKNOWN in set(
            self.required_traffic_sides
        ):
            raise ValueError("Held-out review requires explicit traffic-side strata.")
        if len(set(self.required_traffic_sides)) != len(self.required_traffic_sides):
            raise ValueError("Held-out required traffic sides must be unique.")
        if not self.required_mode_features:
            raise ValueError("Held-out review requires preregistered mode strata.")
        if len(set(self.required_mode_features)) != len(self.required_mode_features):
            raise ValueError("Held-out required mode features must be unique.")
        return self


class BlindedReviewCase(ContractModel):
    case_code: str = Field(pattern=r"^case-[0-9a-f]{12}$")
    source_sha256: Sha256
    candidate_sha256_by_variant_code: dict[str, Sha256]
    exact_question: str
    required_observations: tuple[str, ...]
    candidate_artifact_path_by_variant_code: dict[str, str] = Field(
        default_factory=dict
    )
    review_config_path_by_variant_code: dict[str, str] = Field(default_factory=dict)
    review_html_path: str | None = None
    review_overlay_path: str | None = None
    map_evidence_urls: tuple[str, ...] = ()
    osm_attribution: str | None = None

    @model_validator(mode="after")
    def validate_case(self) -> BlindedReviewCase:
        if not self.candidate_sha256_by_variant_code:
            raise ValueError("Blinded review cases require at least one candidate.")
        for code in self.candidate_sha256_by_variant_code:
            if not _is_variant_code(code):
                raise ValueError(f"Invalid blind variant code: {code}")
        for path_mapping in (
            self.candidate_artifact_path_by_variant_code,
            self.review_config_path_by_variant_code,
        ):
            if path_mapping and set(path_mapping) != set(
                self.candidate_sha256_by_variant_code
            ):
                raise ValueError("Blinded artifact paths must exactly cover variants.")
            for code, path in path_mapping.items():
                if not _is_variant_code(code) or not _is_safe_relative_path(path):
                    raise ValueError("Blinded artifact paths must be safe and coded.")
        for path in (self.review_html_path, self.review_overlay_path):
            if path is not None and not _is_safe_relative_path(path):
                raise ValueError("Blinded review paths must be safe and relative.")
        if any(not value.startswith("https://") for value in self.map_evidence_urls):
            raise ValueError("Blinded map evidence URLs must use HTTPS.")
        material_fields = (
            bool(self.candidate_artifact_path_by_variant_code),
            bool(self.review_config_path_by_variant_code),
            self.review_html_path is not None,
            self.review_overlay_path is not None,
            bool(self.map_evidence_urls),
            self.osm_attribution is not None,
        )
        if any(material_fields) and not all(material_fields):
            raise ValueError("Reviewer-visible material identity must be complete.")
        if not self.required_observations:
            raise ValueError("Blinded cases require explicit observations.")
        return self


class HeldOutReviewMaterial(ContractModel):
    candidate_artifact_path_by_variant: dict[StableToken, str]
    review_overlay_path: str
    map_evidence_urls: tuple[str, ...]
    osm_attribution: Literal["© OpenStreetMap contributors"] = (
        "© OpenStreetMap contributors"
    )

    @model_validator(mode="after")
    def validate_material(self) -> HeldOutReviewMaterial:
        if not self.candidate_artifact_path_by_variant:
            raise ValueError("Review material requires candidate artifacts.")
        for variant_id, path in self.candidate_artifact_path_by_variant.items():
            require_stable_id(variant_id, kind="candidate")
            if not path.strip():
                raise ValueError("Review candidate artifact path cannot be empty.")
        if not self.review_overlay_path.strip() or not self.map_evidence_urls:
            raise ValueError("Review material requires an overlay and map evidence.")
        if any(not value.startswith("https://") for value in self.map_evidence_urls):
            raise ValueError("Review material map URLs must use HTTPS.")
        return self


class BlindedReviewDataset(ContractModel):
    schema_id: str = "torii.corridor.blinded-review-dataset/v1"
    trial_id: StableToken
    created_at: datetime
    machine_recommendation_hidden: Literal[True] = True
    peer_decisions_hidden: Literal[True] = True
    cases: tuple[BlindedReviewCase, ...]

    @model_validator(mode="after")
    def validate_dataset(self) -> BlindedReviewDataset:
        require_stable_id(self.trial_id, kind="review")
        if self.created_at.tzinfo is None:
            raise ValueError("Blinded dataset timestamps require a timezone.")
        case_codes = [case.case_code for case in self.cases]
        if not case_codes or len(case_codes) != len(set(case_codes)):
            raise ValueError("Blinded dataset case codes must be non-empty and unique.")
        return self


class MachineAssessment(ContractModel):
    machine_label: ReviewLabel
    machine_report_sha256: Sha256
    finding_categories: tuple[str, ...]
    safety_critical: bool


class HeldOutCaseStratum(ContractModel):
    city_group: str
    morphology: Literal[
        "grid",
        "historic-core",
        "suburban-arterial",
        "divided-arterial",
        "ramp-interchange",
        "tram-rail",
        "bridge-tunnel",
        "multimodal",
    ]
    traffic_side: TrafficSide
    osm_completeness: Literal["high", "medium", "low", "unassessed"]
    mode_features: tuple[str, ...]
    development_set_overlap: Literal[False] = False

    @model_validator(mode="after")
    def validate_stratum(self) -> HeldOutCaseStratum:
        if self.traffic_side is TrafficSide.UNKNOWN:
            raise ValueError("Held-out strata require an explicit traffic side.")
        if not self.city_group or not self.mode_features:
            raise ValueError("Held-out strata require city and mode coverage.")
        if len(set(self.mode_features)) != len(self.mode_features):
            raise ValueError("Held-out mode features must be unique.")
        return self


class UnblindingCaseKey(ContractModel):
    case_code: str = Field(pattern=r"^case-[0-9a-f]{12}$")
    review_case_id: StableToken
    variant_id_by_code: dict[str, StableToken]
    machine_assessment: MachineAssessment
    machine_assessment_artifact_path: str
    machine_assessment_artifact_sha256: Sha256
    stratum: HeldOutCaseStratum

    @model_validator(mode="after")
    def validate_key(self) -> UnblindingCaseKey:
        require_stable_id(self.review_case_id, kind="review")
        for code, variant_id in self.variant_id_by_code.items():
            if not _is_variant_code(code):
                raise ValueError(f"Invalid blind variant code: {code}")
            require_stable_id(variant_id, kind="candidate")
        normalized = self.machine_assessment_artifact_path.replace("\\", "/")
        if normalized.startswith("/") or ".." in normalized.split("/"):
            raise ValueError("Machine-assessment artifact path must be relative.")
        return self


class HeldOutEvaluationKey(ContractModel):
    schema_id: str = "torii.corridor.held-out-evaluation-key/v1"
    trial_id: StableToken
    blinded_dataset_sha256: Sha256
    blinding_seed: str = Field(min_length=32)
    cases: tuple[UnblindingCaseKey, ...]

    @model_validator(mode="after")
    def validate_evaluation_key(self) -> HeldOutEvaluationKey:
        require_stable_id(self.trial_id, kind="review")
        case_codes = [case.case_code for case in self.cases]
        if not case_codes or len(case_codes) != len(set(case_codes)):
            raise ValueError("Evaluation-key case codes must be non-empty and unique.")
        return self


class BlindReviewDecision(ContractModel):
    schema_id: str = "torii.corridor.blind-review-decision/v1"
    decision_id: StableToken
    trial_id: StableToken
    case_code: str = Field(pattern=r"^case-[0-9a-f]{12}$")
    reviewer_id: StableToken
    label: ReviewLabel
    selected_variant_code: str | None = None
    started_at: datetime
    decided_at: datetime
    observed_facts: tuple[str, ...]
    rationale: str
    machine_recommendation_was_hidden: Literal[True] = True
    peer_decisions_were_hidden: Literal[True] = True
    independent_review_attested: Literal[True] = True

    @property
    def duration_seconds(self) -> float:
        return (self.decided_at - self.started_at).total_seconds()

    @model_validator(mode="after")
    def validate_decision(self) -> BlindReviewDecision:
        require_stable_id(self.decision_id, kind="review")
        require_stable_id(self.trial_id, kind="review")
        require_stable_id(self.reviewer_id, kind="review")
        if self.started_at.tzinfo is None or self.decided_at.tzinfo is None:
            raise ValueError("Blind-review timestamps require timezones.")
        if self.decided_at <= self.started_at:
            raise ValueError("Blind-review decision time must follow its start time.")
        if self.selected_variant_code is not None and not _is_variant_code(
            self.selected_variant_code
        ):
            raise ValueError("Blind-review decision contains an invalid variant code.")
        if self.label == "defect" and self.selected_variant_code is not None:
            raise ValueError("A defect decision cannot select a candidate variant.")
        if not self.observed_facts or not self.rationale:
            raise ValueError("Blind-review decisions require facts and rationale.")
        return self


class HeldOutAdjudication(ContractModel):
    schema_id: str = "torii.corridor.held-out-adjudication/v1"
    adjudication_id: StableToken
    trial_id: StableToken
    case_code: str = Field(pattern=r"^case-[0-9a-f]{12}$")
    reviewer_decision_ids: tuple[StableToken, StableToken]
    adjudicator_id: StableToken
    final_label: ReviewLabel
    selected_variant_code: str | None = None
    decided_at: datetime
    observed_facts: tuple[str, ...]
    rationale: str

    @model_validator(mode="after")
    def validate_adjudication(self) -> HeldOutAdjudication:
        require_stable_id(self.adjudication_id, kind="review")
        require_stable_id(self.trial_id, kind="review")
        require_stable_id(self.adjudicator_id, kind="review")
        if len(set(self.reviewer_decision_ids)) != 2:
            raise ValueError("Adjudication requires two distinct review decisions.")
        for decision_id in self.reviewer_decision_ids:
            require_stable_id(decision_id, kind="review")
        if self.decided_at.tzinfo is None:
            raise ValueError("Adjudication timestamps require a timezone.")
        if self.selected_variant_code is not None and not _is_variant_code(
            self.selected_variant_code
        ):
            raise ValueError("Adjudication contains an invalid variant code.")
        if self.final_label == "defect" and self.selected_variant_code is not None:
            raise ValueError("A defect adjudication cannot select a candidate variant.")
        if not self.observed_facts or not self.rationale:
            raise ValueError("Adjudication requires facts and rationale.")
        return self


class HeldOutReviewMetrics(ContractModel):
    case_count: int
    completed_review_count: int
    adjudicated_case_count: int
    raw_agreement: float | None
    cohen_kappa: float | None
    median_review_seconds: float | None
    attention_precision: float | None
    attention_recall: float | None
    auto_precision: float | None
    auto_coverage: float
    abstention_rate: float
    safety_critical_false_negative_count: int
    adjudicated_label_counts: dict[str, int]
    city_group_case_counts: dict[str, int]
    morphology_case_counts: dict[str, int]
    traffic_side_case_counts: dict[str, int]
    mode_feature_case_counts: dict[str, int]


class HeldOutReviewReport(ContractModel):
    schema_id: str = "torii.corridor.held-out-review-report/v1"
    trial_id: StableToken
    policy_sha256: Sha256
    blinded_dataset_sha256: Sha256
    evaluation_key_sha256: Sha256
    status: GateStatus
    automatic_promotion_gate: GateStatus
    metrics: HeldOutReviewMetrics
    blockers: tuple[str, ...]

    @model_validator(mode="after")
    def validate_report(self) -> HeldOutReviewReport:
        require_stable_id(self.trial_id, kind="review")
        if self.status is GateStatus.PASS:
            if self.blockers or self.automatic_promotion_gate is not GateStatus.PASS:
                raise ValueError("A passing held-out review report cannot have blockers.")
        elif self.automatic_promotion_gate is GateStatus.PASS:
            raise ValueError("Incomplete held-out review cannot pass promotion.")
        return self


class HeldOutReviewContractBundle(ContractModel):
    schema_id: str = "torii.corridor.held-out-review-contract-bundle/v1"
    policy: HeldOutReviewPolicy
    blinded_dataset: BlindedReviewDataset | None = None
    evaluation_key: HeldOutEvaluationKey | None = None
    decisions: tuple[BlindReviewDecision, ...] = ()
    adjudications: tuple[HeldOutAdjudication, ...] = ()
    report: HeldOutReviewReport | None = None


def _is_variant_code(value: str) -> bool:
    if not value.startswith("variant-") or len(value) != 16:
        return False
    try:
        int(value.removeprefix("variant-"), 16)
    except ValueError:
        return False
    return True


def _is_safe_relative_path(value: str) -> bool:
    normalized = value.replace("\\", "/")
    return bool(
        normalized
        and not normalized.startswith("/")
        and ":" not in normalized.split("/", 1)[0]
        and ".." not in normalized.split("/")
    )
