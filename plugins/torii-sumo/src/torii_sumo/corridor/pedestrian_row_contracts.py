from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from .base import ContractModel, Sha256, StableToken
from .enums import GateStatus, TrafficSide
from .ids import require_stable_id, stable_id


PedestrianROWClass = Literal[
    "signalized",
    "priority-unsignalized",
    "unprioritized-unsignalized",
    "unknown-unsignalized",
    "shared-space-or-unsupported",
]
ModelROWClaimClass = Literal[
    "signalized",
    "priority-unsignalized",
    "unprioritized-unsignalized",
    "ambiguous",
    "unmapped",
]
ObservedYieldBehavior = Literal[
    "vehicle-yielded",
    "pedestrian-yielded",
    "no-interaction",
    "unsafe-overlap",
    "unresolved",
]


class SourceROWObservation(ContractModel):
    schema_id: str = "torii.corridor.source-row-observation/v1"
    evidence_id: StableToken
    source_kind: Literal[
        "plain-crossing-priority",
        "plain-node-control",
        "osm-tag",
        "builder-parameter",
        "licensed-map-observation",
    ]
    source_sha256: Sha256
    subject: str
    observed_value: str
    expected_answer_eligible: bool

    @model_validator(mode="after")
    def validate_observation(self) -> SourceROWObservation:
        require_stable_id(self.evidence_id, kind="evidence")
        expected = stable_id(
            "evidence",
            {
                "source_kind": self.source_kind,
                "source_sha256": self.source_sha256,
                "subject": self.subject,
                "observed_value": self.observed_value,
                "expected_answer_eligible": self.expected_answer_eligible,
            },
        )
        if self.evidence_id != expected:
            raise ValueError("Source ROW observation identity does not close.")
        return self


class SourceROWBundle(ContractModel):
    schema_id: str = "torii.corridor.source-row-bundle/v1"
    source_bundle_id: StableToken
    crossing_signature: StableToken
    crossing_node_id: str
    crossing_edge_ids: tuple[str, ...]
    traffic_side: TrafficSide
    crossing_stage_count: int = Field(ge=1)
    junction_control_kind: Literal[
        "traffic-light",
        "unsignalized",
        "unknown",
        "shared-space-or-unsupported",
    ]
    explicit_crossing_priority: bool | None
    source_status: Literal[
        "complete",
        "incomplete",
        "contradictory",
        "unsupported",
    ]
    observations: tuple[SourceROWObservation, ...]

    def identity_payload(self) -> dict[str, object]:
        return {
            "crossing_signature": self.crossing_signature,
            "crossing_node_id": self.crossing_node_id,
            "crossing_edge_ids": self.crossing_edge_ids,
            "traffic_side": self.traffic_side,
            "crossing_stage_count": self.crossing_stage_count,
            "junction_control_kind": self.junction_control_kind,
            "explicit_crossing_priority": self.explicit_crossing_priority,
            "source_status": self.source_status,
            "observation_ids": [
                observation.evidence_id for observation in self.observations
            ],
        }

    @model_validator(mode="after")
    def validate_bundle(self) -> SourceROWBundle:
        require_stable_id(self.source_bundle_id, kind="evidence")
        require_stable_id(self.crossing_signature, kind="signature")
        if self.crossing_edge_ids != tuple(
            sorted(set(self.crossing_edge_ids))
        ):
            raise ValueError("Source crossing edges must be sorted and unique.")
        observation_ids = tuple(
            observation.evidence_id for observation in self.observations
        )
        if observation_ids != tuple(sorted(set(observation_ids))):
            raise ValueError("Source observations must be sorted and unique.")
        if self.source_bundle_id != stable_id(
            "evidence",
            self.identity_payload(),
        ):
            raise ValueError("Source ROW bundle identity does not close.")
        return self


class SourceROWOracleDecision(ContractModel):
    schema_id: str = "torii.corridor.source-row-oracle-decision/v1"
    decision_id: StableToken
    source_bundle_id: StableToken
    traffic_side: TrafficSide
    crossing_stage_count: int = Field(ge=1)
    expected_class: PedestrianROWClass
    status: GateStatus
    abstained: bool
    reasons: tuple[str, ...]
    expected_answer_channels: tuple[Literal["source-evidence"], ...] = (
        "source-evidence",
    )
    model_claim_fields_read: tuple[()] = ()
    automatic_promotion_gate: GateStatus = GateStatus.BLOCKED

    @model_validator(mode="after")
    def validate_decision(self) -> SourceROWOracleDecision:
        require_stable_id(self.decision_id, kind="evidence")
        require_stable_id(self.source_bundle_id, kind="evidence")
        if self.automatic_promotion_gate is not GateStatus.BLOCKED:
            raise ValueError("A source ROW oracle cannot authorize promotion.")
        if self.abstained != (
            self.expected_class
            in {
                "unknown-unsignalized",
                "shared-space-or-unsupported",
            }
        ):
            raise ValueError("Source ROW abstention is inconsistent.")
        expected_status = (
            GateStatus.PASS
            if not self.abstained
            else GateStatus.REVIEW
        )
        if self.status is not expected_status:
            raise ValueError("Source ROW decision status is inconsistent.")
        identity = {
            "source_bundle_id": self.source_bundle_id,
            "traffic_side": self.traffic_side,
            "crossing_stage_count": self.crossing_stage_count,
            "expected_class": self.expected_class,
            "status": self.status,
            "abstained": self.abstained,
            "reasons": self.reasons,
        }
        if self.decision_id != stable_id("evidence", identity):
            raise ValueError("Source ROW decision identity does not close.")
        return self


class ROWGeometryEvidence(ContractModel):
    schema_id: str = "torii.corridor.row-geometry-evidence/v1"
    geometry_evidence_id: StableToken
    candidate_net_sha256: Sha256
    crossing_edge_id: str
    vehicle_from_edge_id: str
    vehicle_to_edge_id: str
    centerline_intersects: bool
    minimum_centerline_distance_m: float = Field(ge=0.0)
    crossing_angle_deg: float | None
    right_of_way_inference: Literal["not-inferred"] = "not-inferred"
    request_foes_fields_read: tuple[()] = ()

    @model_validator(mode="after")
    def validate_geometry(self) -> ROWGeometryEvidence:
        require_stable_id(self.geometry_evidence_id, kind="evidence")
        identity = self.model_dump(
            mode="json",
            by_alias=True,
            exclude={"schema_id", "geometry_evidence_id"},
        )
        if self.geometry_evidence_id != stable_id("evidence", identity):
            raise ValueError("ROW geometry evidence identity does not close.")
        return self


class ROWRequestRelation(ContractModel):
    pedestrian_request_index: int = Field(ge=0)
    vehicle_request_index: int = Field(ge=0)
    pedestrian_response_to_vehicle: bool
    vehicle_response_to_pedestrian: bool
    pedestrian_foe_to_vehicle: bool
    vehicle_foe_to_pedestrian: bool
    pedestrian_cont: bool
    vehicle_cont: bool


class ROWModelClaimEvidence(ContractModel):
    schema_id: str = "torii.corridor.row-model-claim-evidence/v1"
    model_claim_id: StableToken
    candidate_net_sha256: Sha256
    junction_id: str
    crossing_edge_id: str
    vehicle_from_edge_id: str
    vehicle_to_edge_id: str
    inferred_class: ModelROWClaimClass
    relation: ROWRequestRelation | None
    controller_ids: tuple[str, ...]
    source_connection_states: tuple[str, ...]
    bit_order: Literal["rightmost-bit-is-index-zero"] = (
        "rightmost-bit-is-index-zero"
    )
    ground_truth_authority: Literal[False] = False
    limitations: tuple[str, ...]

    @model_validator(mode="after")
    def validate_claim(self) -> ROWModelClaimEvidence:
        require_stable_id(self.model_claim_id, kind="evidence")
        if self.controller_ids != tuple(sorted(set(self.controller_ids))):
            raise ValueError("Controller IDs must be sorted and unique.")
        identity = self.model_dump(
            mode="json",
            by_alias=True,
            exclude={"schema_id", "model_claim_id"},
        )
        if self.model_claim_id != stable_id("evidence", identity):
            raise ValueError("ROW model-claim identity does not close.")
        return self


class ROWStaticAssessment(ContractModel):
    schema_id: str = "torii.corridor.row-static-assessment/v1"
    assessment_id: StableToken
    source_decision_id: StableToken
    geometry_evidence_id: StableToken
    model_claim_id: StableToken
    status: GateStatus
    source_model_consistent: bool | None
    geometry_applicable: bool
    contradictions: tuple[str, ...]
    limitations: tuple[str, ...]
    expected_answer_source_bundle_only: Literal[True] = True
    automatic_promotion_gate: GateStatus = GateStatus.BLOCKED

    @model_validator(mode="after")
    def validate_assessment(self) -> ROWStaticAssessment:
        require_stable_id(self.assessment_id, kind="finding")
        require_stable_id(self.source_decision_id, kind="evidence")
        require_stable_id(self.geometry_evidence_id, kind="evidence")
        require_stable_id(self.model_claim_id, kind="evidence")
        if self.automatic_promotion_gate is not GateStatus.BLOCKED:
            raise ValueError("ROW static assessment cannot authorize promotion.")
        if self.status is GateStatus.PASS and (
            self.source_model_consistent is not True
            or not self.geometry_applicable
            or self.contradictions
        ):
            raise ValueError("Passing ROW assessment lacks closed evidence.")
        identity = self.model_dump(
            mode="json",
            by_alias=True,
            exclude={"schema_id", "assessment_id"},
        )
        if self.assessment_id != stable_id("finding", identity):
            raise ValueError("ROW static assessment identity does not close.")
        return self


class ROWRuntimeProbe(ContractModel):
    schema_id: str = "torii.corridor.row-runtime-probe/v1"
    runtime_probe_id: StableToken
    candidate_net_sha256: Sha256
    route_sha256: Sha256
    sumo_binary_sha256: Sha256
    sumo_version: str
    crossing_edge_id: str
    vehicle_internal_lane_id: str
    arrival_schedule: Literal[
        "pedestrian-first",
        "vehicle-first",
        "simultaneous",
    ]
    pedestrian_depart_s: float = Field(ge=0.0)
    vehicle_depart_s: float = Field(ge=0.0)
    vehicle_speed_mps: float = Field(gt=0.0)
    simulation_step_s: float = Field(gt=0.0)
    simulation_end_s: float = Field(gt=0.0)
    random_seed: int = Field(ge=0)
    stop_speed_threshold_mps: float = Field(ge=0.0)
    yield_stop_threshold_s: float = Field(gt=0.0)
    pedestrian_wait_window_s: float = Field(gt=0.0)
    pedestrian_crossing_entry_s: float | None
    pedestrian_crossing_exit_s: float | None
    vehicle_internal_entry_s: float | None
    vehicle_internal_exit_s: float | None
    vehicle_stopped_before_conflict_s: float = Field(ge=0.0)
    pedestrian_stopped_before_crossing_s: float = Field(ge=0.0)
    observed_behavior: ObservedYieldBehavior
    collision_count: int = Field(ge=0)
    emergency_braking_count: int = Field(ge=0)
    completed: bool
    runtime_status: GateStatus
    proves_real_world_priority: Literal[False] = False

    def identity_payload(self) -> dict[str, object]:
        return self.model_dump(
            mode="json",
            by_alias=True,
            exclude={"schema_id", "runtime_probe_id"},
        )

    @model_validator(mode="after")
    def validate_runtime(self) -> ROWRuntimeProbe:
        require_stable_id(self.runtime_probe_id, kind="evidence")
        if self.runtime_probe_id != stable_id(
            "evidence",
            self.identity_payload(),
        ):
            raise ValueError("ROW runtime-probe identity does not close.")
        if self.runtime_status is GateStatus.PASS and (
            not self.completed
            or self.collision_count
            or self.emergency_braking_count
            or self.observed_behavior in {"unsafe-overlap", "unresolved"}
        ):
            raise ValueError("Passing ROW runtime probe is inconsistent.")
        return self


class ROWExperimentCaseResult(ContractModel):
    schema_id: str = "torii.corridor.row-experiment-case/v1"
    case_id: StableToken
    case_key: str
    case_kind: Literal["gold", "mutation", "ood"]
    mutation_kind: Literal[
        "none",
        "request-response-priority-reversed",
        "source-priority-reversed",
        "co-self-consistent-model-reversal",
        "internal-waiting-removed",
        "signal-state-g-g-reversed",
        "not-applicable",
    ]
    vehicle_turn_class: Literal[
        "straight",
        "left",
        "right",
        "not-applicable",
    ]
    source_bundle: SourceROWBundle
    source_decision: SourceROWOracleDecision
    geometry_evidence: ROWGeometryEvidence
    model_claim: ROWModelClaimEvidence
    static_assessment: ROWStaticAssessment
    runtime_probes: tuple[ROWRuntimeProbe, ...]
    expected_source_class: PedestrianROWClass
    expected_model_claim_class: ModelROWClaimClass
    expected_static_status: GateStatus
    expected_simultaneous_behavior: ObservedYieldBehavior | None
    blockers: tuple[str, ...]
    case_passed: bool

    @model_validator(mode="after")
    def validate_case(self) -> ROWExperimentCaseResult:
        require_stable_id(self.case_id, kind="evidence")
        if not self.case_key.strip():
            raise ValueError("ROW experiment case_key cannot be empty.")
        if self.source_decision.source_bundle_id != self.source_bundle.source_bundle_id:
            raise ValueError("ROW case source decision is not bound to its bundle.")
        if self.source_decision.traffic_side is not self.source_bundle.traffic_side:
            raise ValueError("ROW case traffic side does not close.")
        if (
            self.source_decision.crossing_stage_count
            != self.source_bundle.crossing_stage_count
        ):
            raise ValueError("ROW case crossing stage count does not close.")
        if self.case_passed != (not self.blockers):
            raise ValueError("ROW case pass flag does not close over blockers.")
        expected_id = stable_id(
            "evidence",
            {
                "case_key": self.case_key,
                "case_kind": self.case_kind,
                "mutation_kind": self.mutation_kind,
                "vehicle_turn_class": self.vehicle_turn_class,
                "source_bundle_id": self.source_bundle.source_bundle_id,
                "source_decision_id": self.source_decision.decision_id,
                "geometry_evidence_id": (
                    self.geometry_evidence.geometry_evidence_id
                ),
                "model_claim_id": self.model_claim.model_claim_id,
                "expected_source_class": self.expected_source_class,
                "expected_model_claim_class": self.expected_model_claim_class,
                "runtime_probe_ids": [
                    probe.runtime_probe_id for probe in self.runtime_probes
                ],
            },
        )
        if self.case_id != expected_id:
            raise ValueError("ROW experiment case identity does not close.")
        return self


class ROWExperimentReport(ContractModel):
    schema_id: str = "torii.corridor.row-1-experiment-report/v1"
    report_id: StableToken
    fixture_sha256: dict[str, Sha256]
    netconvert_binary_sha256: Sha256
    netconvert_version: str
    sumo_binary_sha256: Sha256
    sumo_version: str
    cases: tuple[ROWExperimentCaseResult, ...]
    gold_case_count: int = Field(ge=0)
    mutation_case_count: int = Field(ge=0)
    ood_case_count: int = Field(ge=0)
    runtime_probe_count: int = Field(ge=0)
    failed_case_count: int = Field(ge=0)
    unsafe_false_pass_count: int = Field(ge=0)
    source_insufficient_forced_decision_count: int = Field(ge=0)
    expected_answer_model_claim_read_count: int = Field(ge=0)
    status: GateStatus
    automatic_promotion_gate: GateStatus = GateStatus.BLOCKED

    @model_validator(mode="after")
    def validate_report(self) -> ROWExperimentReport:
        require_stable_id(self.report_id, kind="manifest")
        if self.automatic_promotion_gate is not GateStatus.BLOCKED:
            raise ValueError("ROW-1 cannot authorize automatic promotion.")
        case_ids = tuple(case.case_id for case in self.cases)
        if case_ids != tuple(sorted(set(case_ids))):
            raise ValueError("ROW-1 cases must be sorted and unique.")
        if self.gold_case_count != sum(
            case.case_kind == "gold" for case in self.cases
        ):
            raise ValueError("ROW-1 gold-case count does not close.")
        if self.mutation_case_count != sum(
            case.case_kind == "mutation" for case in self.cases
        ):
            raise ValueError("ROW-1 mutation count does not close.")
        if self.ood_case_count != sum(
            case.case_kind == "ood" for case in self.cases
        ):
            raise ValueError("ROW-1 OOD count does not close.")
        if self.runtime_probe_count != sum(
            len(case.runtime_probes) for case in self.cases
        ):
            raise ValueError("ROW-1 runtime-probe count does not close.")
        if self.failed_case_count != sum(
            not case.case_passed for case in self.cases
        ):
            raise ValueError("ROW-1 failed-case count does not close.")
        expected_status = (
            GateStatus.PASS
            if self.failed_case_count == 0
            and self.unsafe_false_pass_count == 0
            and self.source_insufficient_forced_decision_count == 0
            and self.expected_answer_model_claim_read_count == 0
            else GateStatus.BLOCKED
        )
        if self.status is not expected_status:
            raise ValueError("ROW-1 report status is inconsistent.")
        expected_id = stable_id(
            "manifest",
            {
                "fixture_sha256": self.fixture_sha256,
                "netconvert_binary_sha256": self.netconvert_binary_sha256,
                "netconvert_version": self.netconvert_version,
                "sumo_binary_sha256": self.sumo_binary_sha256,
                "sumo_version": self.sumo_version,
                "case_ids": case_ids,
                "status": self.status,
                "automatic_promotion_gate": self.automatic_promotion_gate,
            },
        )
        if self.report_id != expected_id:
            raise ValueError("ROW-1 report identity does not close.")
        return self
