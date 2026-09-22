from __future__ import annotations

import hashlib
import math
from typing import Any, Literal

from pydantic import Field, model_validator

from .base import ContractModel, Sha256, StableToken
from .enums import GateStatus, TrafficSide
from .ids import require_stable_id, stable_id


ConflictCertainty = Literal["confirmed", "potential"]
RightOfWayEvidenceClass = Literal[
    "source-priority",
    "source-unprioritized",
    "unknown",
    "unsupported",
]
RWC1_FROZEN_SAMPLING_SEED = "torii-stage1m-rwc1-v1"

_UNSIGNALIZED_CONTROL_KINDS = frozenset(
    {
        "priority-unsignalized",
        "unprioritized-unsignalized",
        "unknown-unsignalized",
    }
)
_ROW_FINDING_CATEGORIES = frozenset(
    {
        "pedestrian_vehicle_conflict_requires_right_of_way_review",
        "pedestrian_vehicle_envelope_requires_right_of_way_review",
    }
)


class AtomicConflictWitness(ContractModel):
    schema_id: str = "torii.corridor.atomic-conflict-witness/v1"
    atomic_witness_id: StableToken
    witness_signature: StableToken
    pedestrian_movement_id: StableToken
    conflicting_movement_id: StableToken
    pedestrian_physical_cell_id: StableToken
    conflicting_physical_cell_ids: tuple[StableToken, ...]
    crossing_signature: StableToken
    conflict_reason: str
    certainty: ConflictCertainty
    minimum_centerline_distance_m: float = Field(ge=0.0)
    crossing_angle_deg: float | None
    envelope_margin_m: float = Field(ge=0.0)
    conflicting_approach_ids: tuple[StableToken, ...]
    conflicting_source_lane_role_ids: tuple[StableToken, ...]
    conflicting_lane_roles: tuple[str, ...]
    conflicting_turn_classes: tuple[str, ...]
    conflicting_mode_classes: tuple[str, ...]
    conflicting_speed_mps: float | None
    road_classes: tuple[str, ...]
    speed_band: str
    control_class: str
    right_of_way_evidence_class: RightOfWayEvidenceClass
    request_foes_relation: StableToken
    request_foes_mapping_status: Literal[
        "mapped",
        "unmapped",
        "not-applicable",
    ]
    traffic_side: TrafficSide
    crossing_morphology: str
    grade_relation: str
    grade_evidence_signature: StableToken
    hard_safety_finding: bool
    source_osm_sha256: Sha256
    candidate_net_sha256: Sha256
    toolchain_id: StableToken
    evidence_refs: tuple[StableToken, ...]

    def signature_payload(self) -> dict[str, Any]:
        return self.model_dump(
            mode="json",
            by_alias=True,
            exclude={"witness_signature"},
        )

    @model_validator(mode="after")
    def validate_witness(self) -> AtomicConflictWitness:
        require_stable_id(self.atomic_witness_id, kind="conflict")
        require_stable_id(self.witness_signature, kind="signature")
        require_stable_id(self.pedestrian_movement_id, kind="movement")
        require_stable_id(self.conflicting_movement_id, kind="movement")
        require_stable_id(self.pedestrian_physical_cell_id, kind="cell")
        require_stable_id(self.crossing_signature, kind="signature")
        require_stable_id(self.request_foes_relation, kind="signature")
        require_stable_id(self.grade_evidence_signature, kind="signature")
        require_stable_id(self.toolchain_id, kind="toolchain")
        for cell_id in self.conflicting_physical_cell_ids:
            require_stable_id(cell_id, kind="cell")
        for approach_id in self.conflicting_approach_ids:
            require_stable_id(approach_id, kind="approach")
        for lane_role_id in self.conflicting_source_lane_role_ids:
            require_stable_id(lane_role_id, kind="lane_role")
        for evidence_ref in self.evidence_refs:
            require_stable_id(evidence_ref, kind="evidence")
        expected_id = stable_id(
            "conflict",
            {
                "movement_ids": sorted(
                    (
                        self.pedestrian_movement_id,
                        self.conflicting_movement_id,
                    )
                ),
                "reason": self.conflict_reason,
            },
        )
        if self.atomic_witness_id != expected_id:
            raise ValueError(
                "Atomic witness ID does not match the source conflict identity."
            )
        expected_signature = stable_id(
            "signature",
            self.signature_payload(),
        )
        if self.witness_signature != expected_signature:
            raise ValueError(
                "Atomic witness signature does not match its full evidence payload."
            )
        if self.crossing_angle_deg is not None and not math.isfinite(
            self.crossing_angle_deg
        ):
            raise ValueError("Conflict crossing angle must be finite.")
        if self.conflicting_speed_mps is not None and (
            not math.isfinite(self.conflicting_speed_mps)
            or self.conflicting_speed_mps < 0
        ):
            raise ValueError("Conflict speed must be finite and non-negative.")
        return self


class AtomicConflictLedger(ContractModel):
    schema_id: str = "torii.corridor.atomic-conflict-ledger/v1"
    ledger_id: StableToken
    source_osm_sha256: Sha256
    candidate_net_sha256: Sha256
    toolchain_id: StableToken
    witness_count: int = Field(ge=0)
    confirmed_count: int = Field(ge=0)
    potential_count: int = Field(ge=0)
    source_finding_count: int = Field(ge=0)
    witness_set_signature: StableToken
    witnesses: tuple[AtomicConflictWitness, ...]
    coverage_gate: GateStatus
    automatic_promotion_gate: GateStatus = GateStatus.BLOCKED

    @model_validator(mode="after")
    def validate_ledger(self) -> AtomicConflictLedger:
        require_stable_id(self.ledger_id, kind="manifest")
        require_stable_id(self.toolchain_id, kind="toolchain")
        require_stable_id(self.witness_set_signature, kind="signature")
        if self.automatic_promotion_gate is not GateStatus.BLOCKED:
            raise ValueError("Conflict ledgers cannot authorize promotion.")
        if self.witness_count != len(self.witnesses):
            raise ValueError("Atomic witness count does not close.")
        if self.confirmed_count + self.potential_count != self.witness_count:
            raise ValueError("Atomic certainty counts do not close.")
        witness_ids = [witness.atomic_witness_id for witness in self.witnesses]
        if witness_ids != sorted(witness_ids):
            raise ValueError("Atomic witnesses must use stable sorted order.")
        if len(witness_ids) != len(set(witness_ids)):
            raise ValueError("Atomic witness IDs must be unique.")
        expected_set_signature = stable_id(
            "signature",
            [
                (witness.atomic_witness_id, witness.witness_signature)
                for witness in self.witnesses
            ],
        )
        if self.witness_set_signature != expected_set_signature:
            raise ValueError("Atomic witness-set signature does not close.")
        expected_gate = (
            GateStatus.PASS
            if self.source_finding_count == self.witness_count
            else GateStatus.BLOCKED
        )
        if self.coverage_gate is not expected_gate:
            raise ValueError("Atomic source-finding coverage gate is inconsistent.")
        expected_id = stable_id(
            "manifest",
            {
                "source_osm_sha256": self.source_osm_sha256,
                "candidate_net_sha256": self.candidate_net_sha256,
                "toolchain_id": self.toolchain_id,
                "witness_set_signature": self.witness_set_signature,
            },
        )
        if self.ledger_id != expected_id:
            raise ValueError("Atomic ledger ID does not match its evidence.")
        return self


class ConflictClusterKey(ContractModel):
    pedestrian_physical_cell_id: StableToken
    conflicting_physical_cell_ids: tuple[StableToken, ...]
    crossing_signature: StableToken
    conflict_reason: str
    certainty: ConflictCertainty
    control_class: str
    right_of_way_evidence_class: RightOfWayEvidenceClass
    request_foes_relation: StableToken
    request_foes_mapping_status: Literal[
        "mapped",
        "unmapped",
        "not-applicable",
    ]
    conflicting_approach_ids: tuple[StableToken, ...]
    conflicting_turn_classes: tuple[str, ...]
    traffic_side: TrafficSide
    crossing_morphology: str
    road_classes: tuple[str, ...]
    speed_band: str
    grade_relation: str
    grade_evidence_signature: StableToken
    hard_safety_finding: bool


class ConflictReviewCluster(ContractModel):
    schema_id: str = "torii.corridor.conflict-review-cluster/v1"
    cluster_id: StableToken
    key: ConflictClusterKey
    atomic_witness_ids: tuple[StableToken, ...]
    membership_count: int = Field(ge=1)
    membership_merkle_root: Sha256
    visible_representative_witness_ids: tuple[StableToken, ...]
    representative_roles_by_witness_id: dict[StableToken, tuple[str, ...]]
    hidden_witness_id: StableToken
    hidden_witness_independent: bool
    inclusion_probability: float = Field(ge=0.0, le=1.0)
    selected_for_human_review: bool
    propagation_allowed: Literal[False] = False

    @model_validator(mode="after")
    def validate_cluster(self) -> ConflictReviewCluster:
        require_stable_id(self.cluster_id, kind="review")
        witness_ids = tuple(sorted(set(self.atomic_witness_ids)))
        if witness_ids != self.atomic_witness_ids:
            raise ValueError("Cluster witness IDs must be sorted and unique.")
        for witness_id in witness_ids:
            require_stable_id(witness_id, kind="conflict")
        if self.membership_count != len(witness_ids):
            raise ValueError("Cluster membership count does not close.")
        if self.membership_merkle_root != _merkle_root(witness_ids):
            raise ValueError("Cluster Merkle root does not match membership.")
        if self.hidden_witness_id not in witness_ids:
            raise ValueError("Hidden witness must belong to its cluster.")
        visible_ids = tuple(self.visible_representative_witness_ids)
        if not visible_ids:
            raise ValueError("Every cluster requires visible representatives.")
        if visible_ids != tuple(sorted(set(visible_ids))):
            raise ValueError("Visible representatives must be sorted and unique.")
        if not set(self.visible_representative_witness_ids) <= set(
            witness_ids
        ):
            raise ValueError("Visible representatives must belong to the cluster.")
        if set(self.representative_roles_by_witness_id) != set(
            self.visible_representative_witness_ids
        ):
            raise ValueError("Every visible representative requires role labels.")
        if any(not roles for roles in self.representative_roles_by_witness_id.values()):
            raise ValueError("Representative role labels cannot be empty.")
        if self.hidden_witness_independent == (
            self.hidden_witness_id in self.visible_representative_witness_ids
        ):
            raise ValueError("Hidden-witness independence flag is inconsistent.")
        expected_id = stable_id(
            "review",
            {
                "key": self.key.model_dump(mode="json", by_alias=True),
                "membership_merkle_root": self.membership_merkle_root,
            },
        )
        if self.cluster_id != expected_id:
            raise ValueError("Conflict cluster ID does not match key and membership.")
        return self


class ConflictSiteReviewCase(ContractModel):
    schema_id: str = "torii.corridor.conflict-site-review-case/v1"
    site_review_case_id: StableToken
    pedestrian_physical_cell_id: StableToken
    crossing_signature: StableToken
    cluster_ids: tuple[StableToken, ...]
    atomic_witness_ids: tuple[StableToken, ...]
    witness_count: int = Field(ge=1)
    membership_merkle_root: Sha256
    vehicle_movement_family_signatures: tuple[StableToken, ...]
    control_classes: tuple[str, ...]
    right_of_way_evidence_classes: tuple[RightOfWayEvidenceClass, ...]
    machine_question: str
    required_observations: tuple[str, ...]

    @model_validator(mode="after")
    def validate_site(self) -> ConflictSiteReviewCase:
        require_stable_id(self.site_review_case_id, kind="review")
        require_stable_id(self.pedestrian_physical_cell_id, kind="cell")
        require_stable_id(self.crossing_signature, kind="signature")
        if self.cluster_ids != tuple(sorted(set(self.cluster_ids))):
            raise ValueError("Site cluster IDs must be sorted and unique.")
        for cluster_id in self.cluster_ids:
            require_stable_id(cluster_id, kind="review")
        for signature in self.vehicle_movement_family_signatures:
            require_stable_id(signature, kind="signature")
        witness_ids = tuple(sorted(set(self.atomic_witness_ids)))
        if witness_ids != self.atomic_witness_ids:
            raise ValueError("Site witness IDs must be sorted and unique.")
        for witness_id in witness_ids:
            require_stable_id(witness_id, kind="conflict")
        if self.witness_count != len(witness_ids):
            raise ValueError("Site witness count does not close.")
        if self.membership_merkle_root != _merkle_root(witness_ids):
            raise ValueError("Site membership root does not close.")
        expected_id = stable_id(
            "review",
            {
                "pedestrian_physical_cell_id": (
                    self.pedestrian_physical_cell_id
                ),
                "crossing_signature": self.crossing_signature,
            },
        )
        if self.site_review_case_id != expected_id:
            raise ValueError("Site review-case ID does not match its facility.")
        if not self.required_observations:
            raise ValueError("Site review cases require observations.")
        if self.control_classes != tuple(sorted(set(self.control_classes))):
            raise ValueError("Site control classes must be sorted and unique.")
        if self.right_of_way_evidence_classes != tuple(
            sorted(set(self.right_of_way_evidence_classes))
        ):
            raise ValueError(
                "Site right-of-way evidence classes must be sorted and unique."
            )
        return self


class ConflictPopulationStratum(ContractModel):
    schema_id: str = "torii.corridor.conflict-population-stratum/v1"
    stratum_id: StableToken
    control_class: str
    right_of_way_evidence_class: RightOfWayEvidenceClass
    conflict_reason: str
    certainty: ConflictCertainty
    conflicting_turn_classes: tuple[str, ...]
    traffic_side: TrafficSide
    crossing_morphology: str
    road_classes: tuple[str, ...]
    speed_band: str
    corridor_morphology: str
    cluster_ids: tuple[StableToken, ...]
    site_review_case_ids: tuple[StableToken, ...]
    witness_count: int = Field(ge=1)
    inclusion_probability: float = Field(gt=0.0, le=1.0)
    census_required: bool

    @model_validator(mode="after")
    def validate_stratum(self) -> ConflictPopulationStratum:
        require_stable_id(self.stratum_id, kind="scope")
        if self.cluster_ids != tuple(sorted(set(self.cluster_ids))):
            raise ValueError("Stratum cluster IDs must be sorted and unique.")
        if self.site_review_case_ids != tuple(
            sorted(set(self.site_review_case_ids))
        ):
            raise ValueError("Stratum site IDs must be sorted and unique.")
        for cluster_id in self.cluster_ids:
            require_stable_id(cluster_id, kind="review")
        for site_id in self.site_review_case_ids:
            require_stable_id(site_id, kind="review")
        expected_id = stable_id(
            "scope",
            self.key_payload(),
        )
        if self.stratum_id != expected_id:
            raise ValueError("Conflict stratum ID does not match its key.")
        return self

    def key_payload(self) -> dict[str, Any]:
        return {
            "control_class": self.control_class,
            "right_of_way_evidence_class": (
                self.right_of_way_evidence_class
            ),
            "conflict_reason": self.conflict_reason,
            "certainty": self.certainty,
            "conflicting_turn_classes": self.conflicting_turn_classes,
            "traffic_side": self.traffic_side,
            "crossing_morphology": self.crossing_morphology,
            "road_classes": self.road_classes,
            "speed_band": self.speed_band,
            "corridor_morphology": self.corridor_morphology,
        }


class NegativePairSample(ContractModel):
    sample_id: StableToken
    stratum_id: StableToken
    pedestrian_movement_id: StableToken
    conflicting_movement_id: StableToken
    physical_cell_id: StableToken
    control_class: str
    conflicting_turn_classes: tuple[str, ...]
    traffic_side: TrafficSide
    inclusion_probability: float = Field(gt=0.0, le=1.0)
    machine_finding_absent: Literal[True] = True

    @model_validator(mode="after")
    def validate_sample(self) -> NegativePairSample:
        require_stable_id(self.sample_id, kind="evidence")
        require_stable_id(self.stratum_id, kind="scope")
        require_stable_id(self.pedestrian_movement_id, kind="movement")
        require_stable_id(self.conflicting_movement_id, kind="movement")
        require_stable_id(self.physical_cell_id, kind="cell")
        expected_id = stable_id(
            "evidence",
            {
                "stratum_id": self.stratum_id,
                "pedestrian_movement_id": self.pedestrian_movement_id,
                "conflicting_movement_id": self.conflicting_movement_id,
                "physical_cell_id": self.physical_cell_id,
            },
        )
        if self.sample_id != expected_id:
            raise ValueError("Negative-pair sample ID does not close.")
        return self


class NegativePairStratum(ContractModel):
    stratum_id: StableToken
    population_kind: Literal["same-physical-cell-no-conflict-finding"]
    control_class: str
    conflicting_turn_classes: tuple[str, ...]
    traffic_side: TrafficSide
    population_count: int = Field(ge=1)
    selected_count: int = Field(ge=1)
    inclusion_probability: float = Field(gt=0.0, le=1.0)
    samples: tuple[NegativePairSample, ...]

    @model_validator(mode="after")
    def validate_stratum(self) -> NegativePairStratum:
        require_stable_id(self.stratum_id, kind="scope")
        identity = {
            "control_class": self.control_class,
            "conflicting_turn_classes": self.conflicting_turn_classes,
            "traffic_side": self.traffic_side,
            "population_kind": self.population_kind,
        }
        if self.stratum_id != stable_id("scope", identity):
            raise ValueError("Negative-pair stratum ID does not close.")
        if self.selected_count != len(self.samples):
            raise ValueError("Negative-pair selected count does not close.")
        if self.selected_count > self.population_count:
            raise ValueError("Negative-pair sample exceeds its population.")
        expected_probability = self.selected_count / self.population_count
        if abs(self.inclusion_probability - expected_probability) > 1e-12:
            raise ValueError("Negative-pair inclusion probability is incorrect.")
        sample_ids = tuple(sample.sample_id for sample in self.samples)
        if sample_ids != tuple(sorted(set(sample_ids))):
            raise ValueError("Negative-pair sample IDs must be sorted and unique.")
        if any(
            sample.stratum_id != self.stratum_id
            or sample.control_class != self.control_class
            or sample.conflicting_turn_classes != self.conflicting_turn_classes
            or sample.traffic_side is not self.traffic_side
            or abs(sample.inclusion_probability - self.inclusion_probability)
            > 1e-12
            for sample in self.samples
        ):
            raise ValueError("Negative-pair sample evidence contradicts its stratum.")
        return self


class ReviewCompressionPolicy(ContractModel):
    schema_id: str = "torii.corridor.review-compression-policy/v1"
    policy_id: StableToken
    target_clusters_per_stratum: int = Field(ge=1)
    target_negative_pairs_per_stratum: int = Field(ge=1)
    rare_cluster_threshold: int = Field(ge=1)
    census_control_classes: tuple[str, ...]
    census_grade_relations: tuple[str, ...]

    @classmethod
    def build_default(cls) -> ReviewCompressionPolicy:
        payload = {
            "target_clusters_per_stratum": 30,
            "target_negative_pairs_per_stratum": 30,
            "rare_cluster_threshold": 5,
            "census_control_classes": (
                "unknown-unsignalized",
                "runtime-special",
                "shared-space-or-unsupported",
            ),
            "census_grade_relations": (
                "cross-cell-unresolved",
                "rail-or-runtime-special",
            ),
        }
        return cls(policy_id=stable_id("policy", payload), **payload)

    @model_validator(mode="after")
    def validate_policy(self) -> ReviewCompressionPolicy:
        require_stable_id(self.policy_id, kind="policy")
        payload = self.model_dump(
            mode="json",
            by_alias=True,
            exclude={"schema_id", "policy_id"},
        )
        if self.policy_id != stable_id("policy", payload):
            raise ValueError("Review compression policy ID does not close.")
        return self


class LosslessReviewCompressionReport(ContractModel):
    schema_id: str = "torii.corridor.lossless-review-compression/v1"
    report_id: StableToken
    ledger: AtomicConflictLedger
    policy: ReviewCompressionPolicy
    sampling_seed_sha256: Sha256
    clusters: tuple[ConflictReviewCluster, ...]
    site_review_cases: tuple[ConflictSiteReviewCase, ...]
    population_strata: tuple[ConflictPopulationStratum, ...]
    negative_pair_strata: tuple[NegativePairStratum, ...]
    atomic_membership_coverage: float
    lost_witness_count: int = Field(ge=0)
    duplicate_membership_count: int = Field(ge=0)
    extraneous_membership_count: int = Field(ge=0)
    mixed_hard_key_violation_count: int = Field(ge=0)
    machine_review_ready_gate: GateStatus
    automatic_promotion_gate: GateStatus = GateStatus.BLOCKED

    @model_validator(mode="after")
    def validate_report(self) -> LosslessReviewCompressionReport:
        require_stable_id(self.report_id, kind="manifest")
        if self.automatic_promotion_gate is not GateStatus.BLOCKED:
            raise ValueError("Review compression cannot authorize promotion.")
        ledger_ids = {
            witness.atomic_witness_id for witness in self.ledger.witnesses
        }
        cluster_members = [
            witness_id
            for cluster in self.clusters
            for witness_id in cluster.atomic_witness_ids
        ]
        member_set = set(cluster_members)
        expected_lost = len(ledger_ids - member_set)
        expected_duplicate = len(cluster_members) - len(member_set)
        expected_extraneous = len(member_set - ledger_ids)
        if self.lost_witness_count != expected_lost:
            raise ValueError("Reported lost-witness count is incorrect.")
        if self.duplicate_membership_count != expected_duplicate:
            raise ValueError("Reported duplicate membership count is incorrect.")
        if self.extraneous_membership_count != expected_extraneous:
            raise ValueError("Reported extraneous membership count is incorrect.")
        expected_coverage = (
            len(ledger_ids & member_set) / len(ledger_ids)
            if ledger_ids
            else 1.0
        )
        if abs(self.atomic_membership_coverage - expected_coverage) > 1e-12:
            raise ValueError("Atomic membership coverage is incorrect.")
        cluster_by_id = {cluster.cluster_id: cluster for cluster in self.clusters}
        if len(cluster_by_id) != len(self.clusters):
            raise ValueError("Conflict cluster IDs must be unique.")
        cluster_ids = set(cluster_by_id)
        site_by_id = {
            site.site_review_case_id: site for site in self.site_review_cases
        }
        if len(site_by_id) != len(self.site_review_cases):
            raise ValueError("Conflict site-review IDs must be unique.")
        site_cluster_ids = [
            cluster_id
            for site in self.site_review_cases
            for cluster_id in site.cluster_ids
        ]
        if set(site_cluster_ids) != cluster_ids or len(site_cluster_ids) != len(
            set(site_cluster_ids)
        ):
            raise ValueError(
                "Every cluster must belong to exactly one site review case."
            )
        for site in self.site_review_cases:
            expected_site_witnesses = tuple(
                sorted(
                    witness_id
                    for cluster_id in site.cluster_ids
                    for witness_id in cluster_by_id[
                        cluster_id
                    ].atomic_witness_ids
                )
            )
            if site.atomic_witness_ids != expected_site_witnesses:
                raise ValueError(
                    "Site membership does not equal its cluster membership."
                )
        stratum_cluster_ids = [
            cluster_id
            for stratum in self.population_strata
            for cluster_id in stratum.cluster_ids
        ]
        if set(stratum_cluster_ids) != cluster_ids or len(
            stratum_cluster_ids
        ) != len(set(stratum_cluster_ids)):
            raise ValueError("Every cluster must belong to exactly one stratum.")
        for stratum in self.population_strata:
            expected_witness_count = sum(
                cluster_by_id[cluster_id].membership_count
                for cluster_id in stratum.cluster_ids
            )
            if stratum.witness_count != expected_witness_count:
                raise ValueError("Stratum witness count does not close.")
            expected_site_ids = tuple(
                sorted(
                    {
                        stable_id(
                            "review",
                            {
                                "pedestrian_physical_cell_id": (
                                    cluster_by_id[
                                        cluster_id
                                    ].key.pedestrian_physical_cell_id
                                ),
                                "crossing_signature": cluster_by_id[
                                    cluster_id
                                ].key.crossing_signature,
                            },
                        )
                        for cluster_id in stratum.cluster_ids
                    }
                )
            )
            if stratum.site_review_case_ids != expected_site_ids:
                raise ValueError("Stratum site membership does not close.")
            if not set(expected_site_ids) <= set(site_by_id):
                raise ValueError("Stratum references an unknown site review case.")
        negative_ids = [
            stratum.stratum_id for stratum in self.negative_pair_strata
        ]
        if negative_ids != sorted(set(negative_ids)):
            raise ValueError("Negative-pair strata must be sorted and unique.")
        expected_gate = (
            GateStatus.PASS
            if self.ledger.coverage_gate is GateStatus.PASS
            and expected_lost == 0
            and expected_duplicate == 0
            and expected_extraneous == 0
            and self.mixed_hard_key_violation_count == 0
            else GateStatus.BLOCKED
        )
        if self.machine_review_ready_gate is not expected_gate:
            raise ValueError("RWC machine gate is inconsistent.")
        expected_id = stable_id(
            "manifest",
            {
                "ledger_id": self.ledger.ledger_id,
                "policy_id": self.policy.policy_id,
                "sampling_seed_sha256": self.sampling_seed_sha256,
                "cluster_ids": [cluster.cluster_id for cluster in self.clusters],
                "site_review_case_ids": [
                    site.site_review_case_id for site in self.site_review_cases
                ],
                "stratum_ids": [
                    stratum.stratum_id for stratum in self.population_strata
                ],
                "negative_stratum_ids": negative_ids,
            },
        )
        if self.report_id != expected_id:
            raise ValueError("RWC report ID does not close.")
        return self



def _merkle_root(values: tuple[str, ...]) -> str:
    if not values:
        return hashlib.sha256(b"").hexdigest()
    level = [hashlib.sha256(value.encode("utf-8")).digest() for value in values]
    while len(level) > 1:
        if len(level) % 2:
            level.append(level[-1])
        level = [
            hashlib.sha256(level[index] + level[index + 1]).digest()
            for index in range(0, len(level), 2)
        ]
    return level[0].hex()
