from __future__ import annotations

import hashlib
from collections import defaultdict
from typing import Any

from .canonicalizer import CanonicalEntity, CanonicalNetworkSnapshot
from .conflict_graph import IndependentSafetyReport
from .enums import GateStatus, TrafficSide
from .ids import require_stable_id, stable_id
from .review_compression_contracts import (
    RWC1_FROZEN_SAMPLING_SEED as RWC1_FROZEN_SAMPLING_SEED,
    _ROW_FINDING_CATEGORIES,
    _UNSIGNALIZED_CONTROL_KINDS,
    AtomicConflictLedger,
    AtomicConflictWitness,
    ConflictClusterKey,
    ConflictPopulationStratum,
    ConflictReviewCluster,
    ConflictSiteReviewCase,
    LosslessReviewCompressionReport,
    NegativePairSample,
    NegativePairStratum,
    ReviewCompressionPolicy,
    RightOfWayEvidenceClass,
    _merkle_root,
)

def build_lossless_review_compression(
    snapshot: CanonicalNetworkSnapshot,
    safety_report: IndependentSafetyReport,
    *,
    source_osm_sha256: str,
    candidate_net_sha256: str,
    toolchain_id: str,
    sampling_seed: str,
    corridor_morphology: str = "unassessed",
    policy: ReviewCompressionPolicy | None = None,
) -> LosslessReviewCompressionReport:
    if not sampling_seed:
        raise ValueError("RWC sampling seed cannot be empty.")
    require_stable_id(toolchain_id, kind="toolchain")
    if (
        snapshot.source_sha256 is not None
        and snapshot.source_sha256 != candidate_net_sha256
    ):
        raise ValueError("RWC candidate hash contradicts canonical snapshot.")
    effective_policy = policy or ReviewCompressionPolicy.build_default()
    seed_sha256 = hashlib.sha256(sampling_seed.encode("utf-8")).hexdigest()
    witnesses = _build_atomic_witnesses(
        snapshot,
        safety_report,
        source_osm_sha256=source_osm_sha256,
        candidate_net_sha256=candidate_net_sha256,
        toolchain_id=toolchain_id,
    )
    source_finding_count = sum(
        finding.category in _ROW_FINDING_CATEGORIES
        for finding in safety_report.findings
    )
    witness_set_signature = stable_id(
        "signature",
        [
            (witness.atomic_witness_id, witness.witness_signature)
            for witness in witnesses
        ],
    )
    ledger_identity = {
        "source_osm_sha256": source_osm_sha256,
        "candidate_net_sha256": candidate_net_sha256,
        "toolchain_id": toolchain_id,
        "witness_set_signature": witness_set_signature,
    }
    ledger = AtomicConflictLedger(
        ledger_id=stable_id("manifest", ledger_identity),
        source_osm_sha256=source_osm_sha256,
        candidate_net_sha256=candidate_net_sha256,
        toolchain_id=toolchain_id,
        witness_count=len(witnesses),
        confirmed_count=sum(
            witness.certainty == "confirmed" for witness in witnesses
        ),
        potential_count=sum(
            witness.certainty == "potential" for witness in witnesses
        ),
        source_finding_count=source_finding_count,
        witness_set_signature=witness_set_signature,
        witnesses=witnesses,
        coverage_gate=(
            GateStatus.PASS
            if source_finding_count == len(witnesses)
            else GateStatus.BLOCKED
        ),
        automatic_promotion_gate=GateStatus.BLOCKED,
    )
    initial_clusters = _build_clusters(witnesses, seed_sha256=seed_sha256)
    sampled_clusters, strata = _build_population_strata(
        initial_clusters,
        seed_sha256=seed_sha256,
        corridor_morphology=corridor_morphology,
        policy=effective_policy,
    )
    sites = _build_site_review_cases(
        sampled_clusters,
        witnesses_by_id={
            witness.atomic_witness_id: witness for witness in witnesses
        },
    )
    negative_strata = _build_negative_pair_strata(
        snapshot,
        safety_report,
        seed_sha256=seed_sha256,
        policy=effective_policy,
    )
    member_ids = [
        witness_id
        for cluster in sampled_clusters
        for witness_id in cluster.atomic_witness_ids
    ]
    ledger_ids = {witness.atomic_witness_id for witness in witnesses}
    member_set = set(member_ids)
    report_identity = {
        "ledger_id": ledger.ledger_id,
        "policy_id": effective_policy.policy_id,
        "sampling_seed_sha256": seed_sha256,
        "cluster_ids": [cluster.cluster_id for cluster in sampled_clusters],
        "site_review_case_ids": [site.site_review_case_id for site in sites],
        "stratum_ids": [stratum.stratum_id for stratum in strata],
        "negative_stratum_ids": [
            stratum.stratum_id for stratum in negative_strata
        ],
    }
    hard_key_violations = _count_mixed_hard_key_violations(
        sampled_clusters,
        witnesses_by_id={
            witness.atomic_witness_id: witness for witness in witnesses
        },
    )
    machine_gate = (
        GateStatus.PASS
        if ledger.coverage_gate is GateStatus.PASS
        and ledger_ids == member_set
        and len(member_ids) == len(member_set)
        and not (member_set - ledger_ids)
        and hard_key_violations == 0
        else GateStatus.BLOCKED
    )
    return LosslessReviewCompressionReport(
        report_id=stable_id("manifest", report_identity),
        ledger=ledger,
        policy=effective_policy,
        sampling_seed_sha256=seed_sha256,
        clusters=sampled_clusters,
        site_review_cases=sites,
        population_strata=strata,
        negative_pair_strata=negative_strata,
        atomic_membership_coverage=(
            len(ledger_ids & member_set) / len(ledger_ids)
            if ledger_ids
            else 1.0
        ),
        lost_witness_count=len(ledger_ids - member_set),
        duplicate_membership_count=len(member_ids) - len(member_set),
        extraneous_membership_count=len(member_set - ledger_ids),
        mixed_hard_key_violation_count=hard_key_violations,
        machine_review_ready_gate=machine_gate,
        automatic_promotion_gate=GateStatus.BLOCKED,
    )


def _build_atomic_witnesses(
    snapshot: CanonicalNetworkSnapshot,
    safety_report: IndependentSafetyReport,
    *,
    source_osm_sha256: str,
    candidate_net_sha256: str,
    toolchain_id: str,
) -> tuple[AtomicConflictWitness, ...]:
    entities = snapshot.entity_index()
    movement_entities = {
        entity.stable_entity_id: entity
        for entity in snapshot.entities
        if entity.kind == "movement"
    }
    control_by_movement = {
        str(entity.payload.get("movement_id", "")): str(
            entity.payload.get("control_kind", "")
        )
        for entity in snapshot.entities
        if entity.kind == "pedestrian_control_binding"
    }
    request_claim_signature_by_cell = (
        _request_foes_claim_signatures_by_cell(entities)
    )
    grade_evidence_signature_by_cell = (
        _grade_evidence_signatures_by_cell(entities)
    )
    request_relation_cache: dict[tuple[str, ...], str] = {}
    grade_relation_cache: dict[tuple[str, ...], str] = {}
    finding_conflict_ids = {
        finding.subject_id
        for finding in safety_report.findings
        if finding.category in _ROW_FINDING_CATEGORIES
    }
    conflicts = {
        conflict.conflict_id: conflict
        for conflict in safety_report.conflict_graph.conflicts
        if conflict.conflict_id in finding_conflict_ids
    }
    if set(conflicts) != finding_conflict_ids:
        missing = sorted(finding_conflict_ids - set(conflicts))
        raise ValueError(
            "RWC findings do not close over conflict graph: "
            + ",".join(missing[:5])
        )
    witnesses: list[AtomicConflictWitness] = []
    for conflict_id in sorted(conflicts):
        conflict = conflicts[conflict_id]
        movement_ids = (conflict.movement_a_id, conflict.movement_b_id)
        pedestrian_ids = [
            movement_id
            for movement_id in movement_ids
            if control_by_movement.get(movement_id)
            in _UNSIGNALIZED_CONTROL_KINDS
        ]
        if len(pedestrian_ids) != 1:
            raise ValueError(
                "RWC conflict must contain exactly one unsignalized pedestrian movement."
            )
        pedestrian_id = pedestrian_ids[0]
        conflicting_id = next(
            movement_id
            for movement_id in movement_ids
            if movement_id != pedestrian_id
        )
        pedestrian = movement_entities[pedestrian_id]
        conflicting = movement_entities[conflicting_id]
        if len(pedestrian.owner_physical_cell_ids) != 1:
            raise ValueError(
                "Pedestrian conflict witnesses require one physical-cell owner."
            )
        pedestrian_variant = _first_variant(pedestrian)
        crossing_signature = str(
            pedestrian_variant.get("crossing_signature")
            or stable_id(
                "signature",
                {
                    "movement_id": pedestrian_id,
                    "movement_payload": pedestrian.payload,
                },
            )
        )
        metadata = _conflicting_movement_metadata(
            conflicting,
            entities=entities,
        )
        control_class = control_by_movement[pedestrian_id]
        row_class = _row_evidence_class(control_class)
        grade_relation = _grade_relation(
            pedestrian.owner_physical_cell_ids[0],
            conflicting.owner_physical_cell_ids,
            entities=entities,
        )
        relation_cells = tuple(
            sorted(
                {
                    pedestrian.owner_physical_cell_ids[0],
                    *conflicting.owner_physical_cell_ids,
                }
            )
        )
        grade_evidence_signature = grade_relation_cache.setdefault(
            relation_cells,
            stable_id(
                "signature",
                {
                    "cell_grade_evidence_signatures": [
                        grade_evidence_signature_by_cell.get(
                            cell_id,
                            stable_id(
                                "signature",
                                {
                                    "physical_cell_id": cell_id,
                                    "status": "missing",
                                },
                            ),
                        )
                        for cell_id in relation_cells
                    ]
                },
            ),
        )
        request_foes_relation = request_relation_cache.setdefault(
            relation_cells,
            stable_id(
                "signature",
                {
                    "mapping_status": "unmapped",
                    "cell_claim_signatures": [
                        request_claim_signature_by_cell.get(
                            cell_id,
                            stable_id(
                                "signature",
                                {
                                    "physical_cell_id": cell_id,
                                    "status": "missing",
                                },
                            ),
                        )
                        for cell_id in relation_cells
                    ],
                },
            ),
        )
        evidence_refs = (
            stable_id(
                "evidence",
                {"source_osm_sha256": source_osm_sha256},
            ),
            stable_id(
                "evidence",
                {"candidate_net_sha256": candidate_net_sha256},
            ),
            stable_id(
                "evidence",
                {"conflict_id": conflict.conflict_id},
            ),
            stable_id(
                "evidence",
                {
                    "request_foes_relation": request_foes_relation,
                    "mapping_status": "unmapped",
                },
            ),
            stable_id(
                "evidence",
                {"grade_evidence_signature": grade_evidence_signature},
            ),
        )
        payload = {
            "atomic_witness_id": conflict.conflict_id,
            "pedestrian_movement_id": pedestrian_id,
            "conflicting_movement_id": conflicting_id,
            "pedestrian_physical_cell_id": (
                pedestrian.owner_physical_cell_ids[0]
            ),
            "conflicting_physical_cell_ids": tuple(
                sorted(conflicting.owner_physical_cell_ids)
            ),
            "crossing_signature": crossing_signature,
            "conflict_reason": conflict.reason,
            "certainty": conflict.certainty,
            "minimum_centerline_distance_m": (
                conflict.minimum_centerline_distance_m
            ),
            "crossing_angle_deg": conflict.crossing_angle_deg,
            "envelope_margin_m": (
                safety_report.conflict_graph.envelope_margin_m
            ),
            **metadata,
            "control_class": control_class,
            "right_of_way_evidence_class": row_class,
            "request_foes_relation": request_foes_relation,
            "request_foes_mapping_status": "unmapped",
            "traffic_side": snapshot.traffic_side,
            "crossing_morphology": _crossing_morphology(
                pedestrian_variant
            ),
            "grade_relation": grade_relation,
            "grade_evidence_signature": grade_evidence_signature,
            "hard_safety_finding": False,
            "source_osm_sha256": source_osm_sha256,
            "candidate_net_sha256": candidate_net_sha256,
            "toolchain_id": toolchain_id,
            "evidence_refs": evidence_refs,
        }
        witness_without_signature = AtomicConflictWitness.model_construct(
            witness_signature=stable_id("signature", {"pending": True}),
            **payload,
        )
        signature = stable_id(
            "signature",
            witness_without_signature.signature_payload(),
        )
        witnesses.append(
            AtomicConflictWitness(
                witness_signature=signature,
                **payload,
            )
        )
    return tuple(witnesses)


def _conflicting_movement_metadata(
    movement: CanonicalEntity,
    *,
    entities: dict[tuple[str, str], CanonicalEntity],
) -> dict[str, Any]:
    variants = tuple(movement.payload.get("variants", ()))
    source_lane_role_ids = tuple(
        sorted(
            {
                str(value)
                for variant in variants
                if (value := variant.get("source_lane_role_id"))
            }
        )
    )
    lane_roles = [
        entities[("lane_role", lane_role_id)]
        for lane_role_id in source_lane_role_ids
        if ("lane_role", lane_role_id) in entities
    ]
    approach_ids = tuple(
        sorted(
            {
                str(entity.payload.get("approach_id"))
                for entity in lane_roles
                if entity.payload.get("approach_id")
            }
        )
    )
    lane_role_names = tuple(
        sorted(
            {
                str(entity.payload.get("role", "unknown"))
                for entity in lane_roles
            }
            or {"unknown"}
        )
    )
    speeds = [
        float(entity.payload["speed_mps"])
        for entity in lane_roles
        if entity.payload.get("speed_mps") is not None
    ]
    speed = max(speeds) if speeds else None
    port_ids = {
        str(entity.payload.get("boundary_port_id"))
        for entity in lane_roles
        if entity.payload.get("boundary_port_id")
    }
    road_classes = tuple(
        sorted(
            {
                str(
                    entities[("boundary_port", port_id)].payload.get(
                        "edge_semantics",
                        {},
                    ).get("type", "unknown")
                    or "unknown"
                )
                for port_id in port_ids
                if ("boundary_port", port_id) in entities
            }
            or {"unknown"}
        )
    )
    return {
        "conflicting_approach_ids": approach_ids,
        "conflicting_source_lane_role_ids": source_lane_role_ids,
        "conflicting_lane_roles": lane_role_names,
        "conflicting_turn_classes": tuple(
            sorted(
                {
                    str(variant.get("turn_class", "unknown"))
                    for variant in variants
                }
            )
        ),
        "conflicting_mode_classes": tuple(
            sorted(
                {
                    str(mode)
                    for variant in variants
                    for mode in variant.get("mode_classes", ())
                }
                or {"unspecified"}
            )
        ),
        "conflicting_speed_mps": speed,
        "road_classes": road_classes,
        "speed_band": _speed_band(speed),
    }


def _build_clusters(
    witnesses: tuple[AtomicConflictWitness, ...],
    *,
    seed_sha256: str,
) -> tuple[ConflictReviewCluster, ...]:
    grouped: dict[str, tuple[ConflictClusterKey, list[AtomicConflictWitness]]] = {}
    for witness in witnesses:
        key = _cluster_key(witness)
        key_id = stable_id(
            "signature",
            key.model_dump(mode="json", by_alias=True),
        )
        grouped.setdefault(key_id, (key, []))[1].append(witness)
    clusters: list[ConflictReviewCluster] = []
    for key_id in sorted(grouped):
        key, members = grouped[key_id]
        members.sort(key=lambda witness: witness.atomic_witness_id)
        witness_ids = tuple(witness.atomic_witness_id for witness in members)
        root = _merkle_root(witness_ids)
        cluster_id = stable_id(
            "review",
            {
                "key": key.model_dump(mode="json", by_alias=True),
                "membership_merkle_root": root,
            },
        )
        hidden, visible, roles = _select_representatives(
            members,
            cluster_id=cluster_id,
            seed_sha256=seed_sha256,
        )
        clusters.append(
            ConflictReviewCluster(
                cluster_id=cluster_id,
                key=key,
                atomic_witness_ids=witness_ids,
                membership_count=len(witness_ids),
                membership_merkle_root=root,
                visible_representative_witness_ids=visible,
                representative_roles_by_witness_id=roles,
                hidden_witness_id=hidden,
                hidden_witness_independent=hidden not in visible,
                inclusion_probability=1.0,
                selected_for_human_review=True,
                propagation_allowed=False,
            )
        )
    return tuple(clusters)


def _build_population_strata(
    clusters: tuple[ConflictReviewCluster, ...],
    *,
    seed_sha256: str,
    corridor_morphology: str,
    policy: ReviewCompressionPolicy,
) -> tuple[
    tuple[ConflictReviewCluster, ...],
    tuple[ConflictPopulationStratum, ...],
]:
    grouped: dict[str, tuple[dict[str, Any], list[ConflictReviewCluster]]] = {}
    for cluster in clusters:
        key_payload = _stratum_key_payload(
            cluster,
            corridor_morphology=corridor_morphology,
        )
        stratum_id = stable_id("scope", key_payload)
        grouped.setdefault(stratum_id, (key_payload, []))[1].append(cluster)
    updated_by_id: dict[str, ConflictReviewCluster] = {}
    strata: list[ConflictPopulationStratum] = []
    for stratum_id in sorted(grouped):
        key_payload, members = grouped[stratum_id]
        members.sort(key=lambda cluster: cluster.cluster_id)
        census_required = (
            len(members) <= policy.rare_cluster_threshold
            or key_payload["control_class"]
            in policy.census_control_classes
            or any(
                cluster.key.grade_relation
                in policy.census_grade_relations
                or cluster.key.hard_safety_finding
                for cluster in members
            )
        )
        selected_count = (
            len(members)
            if census_required
            else min(policy.target_clusters_per_stratum, len(members))
        )
        probability = selected_count / len(members)
        selected_ids = {
            cluster.cluster_id
            for cluster in sorted(
                members,
                key=lambda cluster: _selection_score(
                    seed_sha256,
                    stratum_id,
                    cluster.cluster_id,
                ),
            )[:selected_count]
        }
        for cluster in members:
            updated_by_id[cluster.cluster_id] = (
                ConflictReviewCluster.model_validate(
                    {
                        **cluster.model_dump(mode="python", by_alias=True),
                        "inclusion_probability": probability,
                        "selected_for_human_review": (
                            cluster.cluster_id in selected_ids
                        ),
                    }
                )
            )
        site_ids = {
            stable_id(
                "review",
                {
                    "pedestrian_physical_cell_id": (
                        cluster.key.pedestrian_physical_cell_id
                    ),
                    "crossing_signature": cluster.key.crossing_signature,
                },
            )
            for cluster in members
        }
        witness_count = sum(cluster.membership_count for cluster in members)
        strata.append(
            ConflictPopulationStratum(
                stratum_id=stratum_id,
                **key_payload,
                cluster_ids=tuple(
                    cluster.cluster_id for cluster in members
                ),
                site_review_case_ids=tuple(sorted(site_ids)),
                witness_count=witness_count,
                inclusion_probability=probability,
                census_required=census_required,
            )
        )
    return (
        tuple(updated_by_id[cluster.cluster_id] for cluster in clusters),
        tuple(strata),
    )


def _build_site_review_cases(
    clusters: tuple[ConflictReviewCluster, ...],
    *,
    witnesses_by_id: dict[str, AtomicConflictWitness],
) -> tuple[ConflictSiteReviewCase, ...]:
    grouped: dict[
        tuple[str, str],
        list[ConflictReviewCluster],
    ] = defaultdict(list)
    for cluster in clusters:
        grouped[
            (
                cluster.key.pedestrian_physical_cell_id,
                cluster.key.crossing_signature,
            )
        ].append(cluster)
    sites: list[ConflictSiteReviewCase] = []
    for (cell_id, crossing_signature), site_clusters in sorted(
        grouped.items()
    ):
        site_clusters.sort(key=lambda cluster: cluster.cluster_id)
        witness_ids = tuple(
            sorted(
                witness_id
                for cluster in site_clusters
                for witness_id in cluster.atomic_witness_ids
            )
        )
        family_signatures = tuple(
            sorted(
                {
                    stable_id(
                        "signature",
                        {
                            "approach_ids": witness.conflicting_approach_ids,
                            "turn_classes": witness.conflicting_turn_classes,
                            "lane_roles": witness.conflicting_lane_roles,
                            "right_of_way_evidence_class": (
                                witness.right_of_way_evidence_class
                            ),
                        },
                    )
                    for witness_id in witness_ids
                    for witness in (witnesses_by_id[witness_id],)
                }
            )
        )
        site_identity = {
            "pedestrian_physical_cell_id": cell_id,
            "crossing_signature": crossing_signature,
        }
        sites.append(
            ConflictSiteReviewCase(
                site_review_case_id=stable_id("review", site_identity),
                pedestrian_physical_cell_id=cell_id,
                crossing_signature=crossing_signature,
                cluster_ids=tuple(
                    cluster.cluster_id for cluster in site_clusters
                ),
                atomic_witness_ids=witness_ids,
                witness_count=len(witness_ids),
                membership_merkle_root=_merkle_root(witness_ids),
                vehicle_movement_family_signatures=family_signatures,
                control_classes=tuple(
                    sorted({cluster.key.control_class for cluster in site_clusters})
                ),
                right_of_way_evidence_classes=tuple(
                    sorted(
                        {
                            cluster.key.right_of_way_evidence_class
                            for cluster in site_clusters
                        }
                    )
                ),
                machine_question=(
                    "Is this crossing facility's pedestrian right-of-way "
                    "correctly modeled against every listed vehicle movement family?"
                ),
                required_observations=(
                    "crossing control class",
                    "pedestrian priority evidence",
                    "vehicle approach and turn family",
                    "median or waiting-area structure",
                    "grade separation",
                ),
            )
        )
    return tuple(sites)


def _build_negative_pair_strata(
    snapshot: CanonicalNetworkSnapshot,
    safety_report: IndependentSafetyReport,
    *,
    seed_sha256: str,
    policy: ReviewCompressionPolicy,
) -> tuple[NegativePairStratum, ...]:
    movements = {
        entity.stable_entity_id: entity
        for entity in snapshot.entities
        if entity.kind == "movement"
    }
    control_by_pedestrian = {
        str(entity.payload.get("movement_id", "")): str(
            entity.payload.get("control_kind", "")
        )
        for entity in snapshot.entities
        if entity.kind == "pedestrian_control_binding"
        and str(entity.payload.get("control_kind", ""))
        in _UNSIGNALIZED_CONTROL_KINDS
    }
    conflicts = {
        frozenset((conflict.movement_a_id, conflict.movement_b_id))
        for conflict in safety_report.conflict_graph.conflicts
    }
    movement_ids_by_cell: dict[str, set[str]] = defaultdict(set)
    for movement_id, movement in movements.items():
        for cell_id in movement.owner_physical_cell_ids:
            movement_ids_by_cell[cell_id].add(movement_id)
    populations: dict[
        tuple[str, tuple[str, ...], str],
        list[tuple[str, str, str]],
    ] = defaultdict(list)
    for pedestrian_id, control_class in sorted(
        control_by_pedestrian.items()
    ):
        pedestrian = movements.get(pedestrian_id)
        if pedestrian is None:
            continue
        for cell_id in pedestrian.owner_physical_cell_ids:
            for conflicting_id in sorted(movement_ids_by_cell[cell_id]):
                if conflicting_id == pedestrian_id:
                    continue
                conflicting = movements[conflicting_id]
                if _is_pedestrian_movement(conflicting):
                    continue
                pair = frozenset((pedestrian_id, conflicting_id))
                if pair in conflicts:
                    continue
                turn_classes = tuple(
                    sorted(
                        {
                            str(variant.get("turn_class", "unknown"))
                            for variant in conflicting.payload.get("variants", ())
                        }
                    )
                )
                populations[
                    (control_class, turn_classes, snapshot.traffic_side.value)
                ].append((pedestrian_id, conflicting_id, cell_id))
    strata: list[NegativePairStratum] = []
    for key in sorted(populations):
        control_class, turn_classes, traffic_side_value = key
        population = populations[key]
        population.sort(
            key=lambda pair: _selection_score(
                seed_sha256,
                "negative-pair",
                "|".join(pair),
            )
        )
        selected_count = min(
            policy.target_negative_pairs_per_stratum,
            len(population),
        )
        probability = selected_count / len(population)
        stratum_payload = {
            "control_class": control_class,
            "conflicting_turn_classes": turn_classes,
            "traffic_side": traffic_side_value,
            "population_kind": "same-physical-cell-no-conflict-finding",
        }
        stratum_id = stable_id("scope", stratum_payload)
        samples = tuple(
            sorted(
                (
                    NegativePairSample(
                sample_id=stable_id(
                    "evidence",
                    {
                        "stratum_id": stratum_id,
                        "pedestrian_movement_id": pedestrian_id,
                        "conflicting_movement_id": conflicting_id,
                        "physical_cell_id": cell_id,
                    },
                ),
                stratum_id=stratum_id,
                pedestrian_movement_id=pedestrian_id,
                conflicting_movement_id=conflicting_id,
                physical_cell_id=cell_id,
                control_class=control_class,
                conflicting_turn_classes=turn_classes,
                traffic_side=TrafficSide(traffic_side_value),
                inclusion_probability=probability,
                machine_finding_absent=True,
            )
            for pedestrian_id, conflicting_id, cell_id in population[
                :selected_count
            ]
                ),
                key=lambda sample: sample.sample_id,
            )
        )
        strata.append(
            NegativePairStratum(
                stratum_id=stratum_id,
                population_kind="same-physical-cell-no-conflict-finding",
                control_class=control_class,
                conflicting_turn_classes=turn_classes,
                traffic_side=TrafficSide(traffic_side_value),
                population_count=len(population),
                selected_count=selected_count,
                inclusion_probability=probability,
                samples=samples,
            )
        )
    return tuple(sorted(strata, key=lambda stratum: stratum.stratum_id))


def _cluster_key(witness: AtomicConflictWitness) -> ConflictClusterKey:
    return ConflictClusterKey(
        pedestrian_physical_cell_id=witness.pedestrian_physical_cell_id,
        conflicting_physical_cell_ids=witness.conflicting_physical_cell_ids,
        crossing_signature=witness.crossing_signature,
        conflict_reason=witness.conflict_reason,
        certainty=witness.certainty,
        control_class=witness.control_class,
        right_of_way_evidence_class=witness.right_of_way_evidence_class,
        request_foes_relation=witness.request_foes_relation,
        request_foes_mapping_status=witness.request_foes_mapping_status,
        conflicting_approach_ids=witness.conflicting_approach_ids,
        conflicting_turn_classes=witness.conflicting_turn_classes,
        traffic_side=witness.traffic_side,
        crossing_morphology=witness.crossing_morphology,
        road_classes=witness.road_classes,
        speed_band=witness.speed_band,
        grade_relation=witness.grade_relation,
        grade_evidence_signature=witness.grade_evidence_signature,
        hard_safety_finding=witness.hard_safety_finding,
    )


def _stratum_key_payload(
    cluster: ConflictReviewCluster,
    *,
    corridor_morphology: str,
) -> dict[str, Any]:
    return {
        "control_class": cluster.key.control_class,
        "right_of_way_evidence_class": (
            cluster.key.right_of_way_evidence_class
        ),
        "conflict_reason": cluster.key.conflict_reason,
        "certainty": cluster.key.certainty,
        "conflicting_turn_classes": cluster.key.conflicting_turn_classes,
        "traffic_side": cluster.key.traffic_side,
        "crossing_morphology": cluster.key.crossing_morphology,
        "road_classes": cluster.key.road_classes,
        "speed_band": cluster.key.speed_band,
        "corridor_morphology": corridor_morphology,
    }


def _select_representatives(
    members: list[AtomicConflictWitness],
    *,
    cluster_id: str,
    seed_sha256: str,
) -> tuple[str, tuple[str, ...], dict[str, tuple[str, ...]]]:
    hidden = min(
        members,
        key=lambda witness: _selection_score(
            seed_sha256,
            cluster_id,
            witness.atomic_witness_id,
        ),
    )
    visible_pool = [
        witness
        for witness in members
        if witness.atomic_witness_id != hidden.atomic_witness_id
    ] or list(members)
    roles: dict[str, set[str]] = defaultdict(set)

    def add(role: str, witness: AtomicConflictWitness) -> None:
        roles[witness.atomic_witness_id].add(role)

    medoid = min(
        visible_pool,
        key=lambda candidate: (
            sum(
                abs(
                    candidate.minimum_centerline_distance_m
                    - other.minimum_centerline_distance_m
                )
                + abs(
                    (candidate.crossing_angle_deg or 0.0)
                    - (other.crossing_angle_deg or 0.0)
                )
                / 180.0
                for other in members
            ),
            candidate.atomic_witness_id,
        ),
    )
    add("geometry-medoid", medoid)
    add(
        "minimum-centerline-distance",
        min(
            visible_pool,
            key=lambda witness: (
                witness.minimum_centerline_distance_m,
                witness.atomic_witness_id,
            ),
        ),
    )
    add(
        "maximum-speed-or-road-risk",
        max(
            visible_pool,
            key=lambda witness: (
                witness.conflicting_speed_mps or -1.0,
                witness.road_classes,
                witness.atomic_witness_id,
            ),
        ),
    )
    with_angles = [
        witness
        for witness in visible_pool
        if witness.crossing_angle_deg is not None
    ]
    if with_angles:
        add(
            "minimum-crossing-angle",
            min(
                with_angles,
                key=lambda witness: (
                    witness.crossing_angle_deg,
                    witness.atomic_witness_id,
                ),
            ),
        )
        add(
            "maximum-crossing-angle",
            max(
                with_angles,
                key=lambda witness: (
                    witness.crossing_angle_deg,
                    witness.atomic_witness_id,
                ),
            ),
        )
    add("movement-family", visible_pool[0])
    add("request-foes-relation", visible_pool[0])
    return (
        hidden.atomic_witness_id,
        tuple(sorted(roles)),
        {
            witness_id: tuple(sorted(role_names))
            for witness_id, role_names in sorted(roles.items())
        },
    )


def _count_mixed_hard_key_violations(
    clusters: tuple[ConflictReviewCluster, ...],
    *,
    witnesses_by_id: dict[str, AtomicConflictWitness],
) -> int:
    return sum(
        any(
            _cluster_key(witnesses_by_id[witness_id]) != cluster.key
            for witness_id in cluster.atomic_witness_ids
        )
        for cluster in clusters
    )


def _first_variant(entity: CanonicalEntity) -> dict[str, Any]:
    variants = entity.payload.get("variants", ())
    return dict(variants[0]) if variants else {}


def _is_pedestrian_movement(entity: CanonicalEntity) -> bool:
    return any(
        variant.get("movement_kind") == "pedestrian-crossing-occupancy"
        or "pedestrian" in variant.get("mode_classes", ())
        for variant in entity.payload.get("variants", ())
    )


def _row_evidence_class(control_class: str) -> RightOfWayEvidenceClass:
    if control_class == "priority-unsignalized":
        return "source-priority"
    if control_class == "unprioritized-unsignalized":
        return "source-unprioritized"
    if control_class == "unknown-unsignalized":
        return "unknown"
    return "unsupported"


def _crossing_morphology(variant: dict[str, Any]) -> str:
    crossed_ports = tuple(variant.get("crossed_boundary_port_ids", ()))
    if len(crossed_ports) <= 1:
        return "single-stage-or-unresolved"
    return "multi-carriageway-or-split"


def _grade_relation(
    pedestrian_cell_id: str,
    conflicting_cell_ids: tuple[str, ...],
    *,
    entities: dict[tuple[str, str], CanonicalEntity],
) -> str:
    junction_types = {
        str(entities[("physical_cell", cell_id)].payload.get("junction_type", ""))
        for cell_id in (pedestrian_cell_id, *conflicting_cell_ids)
        if ("physical_cell", cell_id) in entities
    }
    if junction_types & {"rail_crossing", "rail_signal"}:
        return "rail-or-runtime-special"
    if set(conflicting_cell_ids) == {pedestrian_cell_id}:
        return "same-physical-cell"
    return "cross-cell-unresolved"


def _request_foes_claim_signatures_by_cell(
    entities: dict[tuple[str, str], CanonicalEntity],
) -> dict[str, str]:
    cell_ids = {
        entity.stable_entity_id
        for entity in entities.values()
        if entity.kind == "physical_cell"
    }
    claims_by_cell: dict[str, list[dict[str, Any]]] = {
        cell_id: [] for cell_id in cell_ids
    }
    for entity in entities.values():
        if entity.kind != "request_foes":
            continue
        for cell_id in entity.owner_physical_cell_ids:
            claims_by_cell.setdefault(cell_id, []).append(
                {
                    "entity_id": entity.stable_entity_id,
                    "semantic_signature": entity.semantic_signature,
                    "request_rows": entity.payload.get("request_rows", ()),
                }
            )
    return {
        cell_id: stable_id(
            "signature",
            {
                "physical_cell_id": cell_id,
                "claims": sorted(
                    claims,
                    key=lambda value: str(value["entity_id"]),
                ),
            },
        )
        for cell_id, claims in claims_by_cell.items()
    }


def _grade_evidence_signatures_by_cell(
    entities: dict[tuple[str, str], CanonicalEntity],
) -> dict[str, str]:
    features_by_cell: dict[str, dict[str, Any]] = {
        entity.stable_entity_id: {
            "physical_cell_id": entity.stable_entity_id,
            "junction_type": str(
                entity.payload.get("junction_type", "unknown")
            ),
            "ports": [],
        }
        for entity in entities.values()
        if entity.kind == "physical_cell"
    }
    for entity in entities.values():
        if entity.kind != "boundary_port":
            continue
        edge_semantics = entity.payload.get("edge_semantics", {})
        params = edge_semantics.get("params", {})
        port_feature = {
            "boundary_port_id": entity.stable_entity_id,
            "layer": str(params.get("layer", "0")),
            "bridge": str(params.get("bridge", "")),
            "tunnel": str(params.get("tunnel", "")),
            "railway": str(params.get("railway", "")),
        }
        for cell_id in entity.owner_physical_cell_ids:
            features_by_cell.setdefault(
                cell_id,
                {
                    "physical_cell_id": cell_id,
                    "junction_type": "missing",
                    "ports": [],
                },
            )["ports"].append(port_feature)
    return {
        cell_id: stable_id(
            "signature",
            {
                **features,
                "ports": sorted(
                    features["ports"],
                    key=lambda value: str(value["boundary_port_id"]),
                ),
            },
        )
        for cell_id, features in features_by_cell.items()
    }


def _speed_band(speed_mps: float | None) -> str:
    if speed_mps is None:
        return "unknown"
    if speed_mps < 8.34:
        return "lt-30-kph"
    if speed_mps < 13.89:
        return "30-49-kph"
    if speed_mps < 22.23:
        return "50-79-kph"
    return "ge-80-kph"


def _selection_score(seed_sha256: str, *values: str) -> str:
    digest = hashlib.sha256()
    digest.update(seed_sha256.encode("ascii"))
    for value in values:
        digest.update(b"\0")
        digest.update(value.encode("utf-8"))
    return digest.hexdigest()


