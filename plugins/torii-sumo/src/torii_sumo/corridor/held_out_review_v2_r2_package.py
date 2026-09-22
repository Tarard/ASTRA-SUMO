from __future__ import annotations

import hashlib
import html
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Literal
from urllib.parse import urlencode
from xml.sax.saxutils import quoteattr

from torii_sumo.core.artifact_io import (
    copy_file_atomic,
    write_json_atomic,
    write_text_atomic,
)
from torii_sumo.core.candidate_contracts import file_sha256

from .canonicalizer import CanonicalEntity, CanonicalNetworkSnapshot
from .conflict_graph import IndependentSafetyReport
from .enums import GateStatus
from .held_out_corpus_contracts import (
    HeldOutCorpusMachineReport,
    HeldOutCorpusSpec,
)
from .held_out_review_v2_contracts import (
    AttentionEvaluationKeyV2R2,
    BlindedAttentionCaseV2R2,
    BlindedAttentionDatasetV2R2,
    BlindedReviewUnitV2R2,
    HeldOutReviewExecutionParentV2R2,
    HeldOutReviewPackageManifestV2R2,
    HeldOutReviewTrialInstanceV2R2,
    ReviewPackageArtifactV2R2,
    ReviewSamplingCorridorV2R2,
    ReviewSamplingLedgerV2R2,
    ReviewStudySamplingPolicyV2R2,
    ReviewUnitMachineAssessmentV2R2,
    ReviewUnitUnblindingKeyV2R2,
)
from .held_out_review_v2_r2_sampling import (
    select_conflict_sites,
    select_negative_pairs,
    select_presented_site_witnesses,
)
from .ids import stable_id
from .pedestrian_control_census import (
    ControlledPedestrianBindingAssessment,
    ControlledPedestrianBindingCensus,
)
from .review import PedestrianCoverageGap
from .review_compression_contracts import (
    AtomicConflictWitness,
    ConflictReviewCluster,
    ConflictSiteReviewCase,
    LosslessReviewCompressionReport,
    NegativePairSample,
)
from .run_identity import capture_code_producer_identity


_PATH_RELATION_QUESTION = (
    "Review the displayed pedestrian and vehicle paths. Is the represented "
    "right-of-way relationship complete and appropriate for the mapped facility?"
)
_PATH_RELATION_OBSERVATIONS = (
    "whether the paths share an at-grade occupancy area",
    "crossing control and pedestrian priority visible in the source map",
    "vehicle approach and turn behavior",
    "median, waiting-area, bridge, tunnel, or separated-grade structure",
)
_CONTROL_QUESTION = (
    "Does the effective signal configuration provide a valid executable signal "
    "group for the displayed pedestrian movement?"
)
_CONTROL_OBSERVATIONS = (
    "controller ownership and controller type",
    "effective program source and active program coverage",
    "link index range and phase-state lengths",
    "shared signal-index movements",
    "pedestrian path and mapped signal location",
)
_COVERAGE_QUESTION = (
    "Is this pedestrian facility represented by a complete and valid SUMO path, "
    "or does it require a supported alternative model?"
)
_COVERAGE_OBSERVATIONS = (
    "crossing and walking-area continuity",
    "lane permissions and destination ownership",
    "boundary connection and staged-crossing structure",
    "shared-space or other supported alternative representation",
    "agreement with the mapped pedestrian facility",
)


@dataclass(frozen=True)
class _PresentedEvidence:
    evidence_id: str
    visible_payload: dict[str, Any]
    path_shapes: tuple[tuple[str, tuple[tuple[float, float], ...]], ...] = ()


@dataclass(frozen=True)
class _PreparedUnit:
    review_unit_id: str
    unit_kind: Literal[
        "conflict-site",
        "negative-pair",
        "controlled-binding",
        "pedestrian-coverage-gap",
    ]
    review_domain: Literal[
        "pedestrian-path-relation",
        "signal-control-configuration",
        "pedestrian-facility-coverage",
    ]
    inclusion_probability: float
    membership_root: str
    exact_question: str
    required_observations: tuple[str, ...]
    position_xy: tuple[float, float]
    presented_evidence: tuple[_PresentedEvidence, ...]
    hidden_evidence_id: str | None
    visible_details: dict[str, Any]
    finding_categories: tuple[str, ...]
    safety_critical: bool


def build_held_out_review_package_v2_r2(
    *,
    effective_corpus_file: Path,
    trial_instance_file: Path,
    execution_parent_file: Path,
    study_sampling_policy_file: Path,
    restricted_seed_file: Path,
    machine_root: Path,
    base_review_package_dir: Path,
    output_dir: Path,
    repository_root: Path,
    created_at: datetime,
) -> tuple[
    BlindedAttentionDatasetV2R2,
    ReviewSamplingLedgerV2R2,
    HeldOutReviewPackageManifestV2R2,
]:
    """Execute the precommitted v2-R2 sample and build blinded review material."""

    if created_at.tzinfo is None:
        raise ValueError("Review package creation time must include a timezone.")
    corpus_path = effective_corpus_file.resolve(strict=True)
    trial_path = trial_instance_file.resolve(strict=True)
    parent_path = execution_parent_file.resolve(strict=True)
    policy_path = study_sampling_policy_file.resolve(strict=True)
    seed_path = restricted_seed_file.resolve(strict=True)
    machine_dir = machine_root.resolve(strict=True)
    base_package = base_review_package_dir.resolve(strict=True)
    producer = capture_code_producer_identity(repository_root)
    destination = output_dir.resolve()
    if destination.exists() and any(destination.iterdir()):
        raise ValueError("Review package output directory must be new or empty.")
    destination.mkdir(parents=True, exist_ok=True)

    corpus = HeldOutCorpusSpec.model_validate_json(corpus_path.read_text(encoding="utf-8"))
    trial = HeldOutReviewTrialInstanceV2R2.model_validate_json(trial_path.read_text(encoding="utf-8"))
    parent = HeldOutReviewExecutionParentV2R2.model_validate_json(parent_path.read_text(encoding="utf-8"))
    policy = ReviewStudySamplingPolicyV2R2.model_validate_json(policy_path.read_text(encoding="utf-8"))
    if trial.execution_parent_sha256 != file_sha256(parent_path):
        raise ValueError("Review trial does not bind the supplied execution parent.")
    if trial.study_sampling_policy_sha256 != file_sha256(policy_path):
        raise ValueError("Review trial does not bind the supplied study sampling policy.")
    if parent.effective_corpus_sha256 != file_sha256(corpus_path):
        raise ValueError("Execution parent does not bind the supplied effective corpus.")
    seed = _read_restricted_seed(seed_path, expected_sha256=trial.blinding_seed_sha256)

    machine_report_path = machine_dir / "held_out_corpus.machine-report.json"
    machine_manifest_path = machine_dir / "held_out_corpus.machine-manifest.json"
    machine_report = HeldOutCorpusMachineReport.model_validate_json(
        machine_report_path.read_text(encoding="utf-8")
    )
    if parent.machine_report_sha256 != file_sha256(machine_report_path):
        raise ValueError("Execution parent and machine report differ.")
    if parent.machine_manifest_sha256 != file_sha256(machine_manifest_path):
        raise ValueError("Execution parent and machine manifest differ.")
    if (
        machine_report.processed_case_count != 30
        or machine_report.blockers
        or any(result.pipeline_status is not GateStatus.PASS for result in machine_report.results)
    ):
        raise ValueError("V2-R2 package construction requires 30 complete machine cases.")

    base_dataset = _read_json(base_package / "held_out_review.blinded-dataset.json")
    base_key = _read_json(base_package / "held_out_review.unblinding-key.json")
    base_cases_by_code = {case["case_code"]: case for case in base_dataset["cases"]}
    base_key_by_review_id = {case["review_case_id"]: case for case in base_key["cases"]}
    result_by_corridor = {result.corridor_key: result for result in machine_report.results}
    selection_by_corridor = {selection.corridor_key: selection for selection in corpus.corridors}
    if set(result_by_corridor) != set(selection_by_corridor) or len(result_by_corridor) != 30:
        raise ValueError("Machine report and effective corpus corridor identities differ.")

    blinded_cases: list[BlindedAttentionCaseV2R2] = []
    unit_keys: list[ReviewUnitUnblindingKeyV2R2] = []
    corridor_summaries: list[ReviewSamplingCorridorV2R2] = []
    sampling_strata = []

    for corridor_key in sorted(selection_by_corridor):
        result = result_by_corridor[corridor_key]
        selection = selection_by_corridor[corridor_key]
        base_key_case = base_key_by_review_id.get(result.review_case_id)
        if base_key_case is None:
            raise ValueError(f"Base blinded package lacks corridor {corridor_key}.")
        base_case = base_cases_by_code[base_key_case["case_code"]]
        case_code = _blind_code("case", seed, selection.selection_id)
        case_dir = destination / "reviewer-visible" / case_code
        case_dir.mkdir(parents=True, exist_ok=True)
        net_source, candidate_sha256 = _base_candidate_net(base_package, base_case)
        net_destination = case_dir / "network.net.xml"
        copy_file_atomic(net_source, net_destination)
        if file_sha256(net_destination) != candidate_sha256:
            raise ValueError(f"Copied review network hash failed for {corridor_key}.")

        machine_case_dir = machine_dir / "cases" / corridor_key
        snapshot = CanonicalNetworkSnapshot.model_validate_json(
            (machine_case_dir / "canonical-network.json").read_text(encoding="utf-8")
        )
        compression = LosslessReviewCompressionReport.model_validate_json(
            (machine_case_dir / "lossless-review-compression.json").read_text(encoding="utf-8")
        )
        control_census = ControlledPedestrianBindingCensus.model_validate_json(
            (machine_case_dir / "controlled-pedestrian-binding-census.json").read_text(encoding="utf-8")
        )
        safety = IndependentSafetyReport.model_validate_json(
            (machine_case_dir / "independent-safety.json").read_text(encoding="utf-8")
        )
        _require_complete_machine_census(corridor_key, compression)
        entity_index = snapshot.entity_index()
        witness_by_id = {
            witness.atomic_witness_id: witness for witness in compression.ledger.witnesses
        }
        cluster_by_id = {cluster.cluster_id: cluster for cluster in compression.clusters}

        selected_sites, site_strata = select_conflict_sites(
            corridor_key=corridor_key,
            sites=compression.site_review_cases,
            witness_by_id=witness_by_id,
            target=policy.target_conflict_sites_per_corridor,
            seed=seed,
        )
        selected_negatives, negative_strata = select_negative_pairs(
            corridor_key=corridor_key,
            strata=compression.negative_pair_strata,
            target=policy.target_negative_pairs_per_corridor,
            seed=seed,
        )
        hard_controls = tuple(
            assessment
            for assessment in control_census.assessments
            if assessment.primary_class in policy.controlled_binding_hard_classes_census
        )
        coverage_gaps = safety.coverage.pedestrian_coverage_gaps
        sampling_strata.extend((*site_strata, *negative_strata))

        prepared_units: list[_PreparedUnit] = []
        independent_hidden_count = 0
        for site, probability in selected_sites:
            unit = _prepare_conflict_site_unit(
                site=site,
                inclusion_probability=probability,
                witness_by_id=witness_by_id,
                cluster_by_id=cluster_by_id,
                entity_index=entity_index,
                seed=seed,
            )
            prepared_units.append(unit)
            independent_hidden_count += unit.hidden_evidence_id is not None
        prepared_units.extend(
            _prepare_negative_pair_unit(
                sample=sample,
                inclusion_probability=probability,
                entity_index=entity_index,
                trial_id=trial.trial_id,
            )
            for sample, probability in selected_negatives
        )
        prepared_units.extend(
            _prepare_control_binding_unit(
                assessment=assessment,
                entity_index=entity_index,
                trial_id=trial.trial_id,
            )
            for assessment in hard_controls
        )
        prepared_units.extend(
            _prepare_coverage_gap_unit(
                gap=gap,
                trial_id=trial.trial_id,
            )
            for gap in coverage_gaps
        )
        prepared_units.sort(key=lambda item: item.review_unit_id)

        convert_to_lon_lat = _geo_converter(net_destination)
        blinded_units: list[BlindedReviewUnitV2R2] = []
        overlay_units: list[tuple[str, _PreparedUnit, dict[str, str]]] = []
        for prepared in prepared_units:
            unit_code = _blind_code("unit", seed, prepared.review_unit_id)
            evidence_id_by_code = {
                _blind_code("witness", seed, evidence.evidence_id): evidence.evidence_id
                for evidence in prepared.presented_evidence
            }
            hidden_witness_code = (
                _blind_code("witness", seed, prepared.hidden_evidence_id)
                if prepared.hidden_evidence_id is not None
                else None
            )
            position = _review_position(
                prepared.position_xy,
                convert_to_lon_lat=convert_to_lon_lat,
                fallback_lat=selection.center_lat,
                fallback_lon=selection.center_lon,
            )
            evidence_relative = f"reviewer-visible/{case_code}/units/{unit_code}.json"
            visible_records = []
            for evidence in prepared.presented_evidence:
                witness_code = _blind_code("witness", seed, evidence.evidence_id)
                visible_records.append(
                    {
                        "witness_code": witness_code,
                        **evidence.visible_payload,
                    }
                )
            visible_payload = {
                "schema": "torii.corridor.reviewer-visible-evidence/v2-r2",
                "case_code": case_code,
                "unit_code": unit_code,
                "review_domain": prepared.review_domain,
                "exact_question": prepared.exact_question,
                "required_observations": prepared.required_observations,
                "review_position": position,
                "map_evidence_urls": _map_evidence_urls(position["latitude"], position["longitude"]),
                "evidence_records": sorted(visible_records, key=lambda item: item["witness_code"]),
                "context": prepared.visible_details,
                "osm_attribution": "© OpenStreetMap contributors",
            }
            evidence_path = destination / evidence_relative
            write_json_atomic(evidence_path, visible_payload, sort_keys=True)

            assessment = ReviewUnitMachineAssessmentV2R2(
                review_unit_id=prepared.review_unit_id,
                unit_kind=prepared.unit_kind,
                machine_attention=prepared.unit_kind != "negative-pair",
                safety_critical=prepared.safety_critical,
                inclusion_probability=prepared.inclusion_probability,
                membership_root=prepared.membership_root,
                evidence_artifact_sha256=file_sha256(evidence_path),
                finding_categories=prepared.finding_categories,
            )
            assessment_relative = f"restricted/machine-assessments/{unit_code}.json"
            assessment_path = destination / assessment_relative
            write_json_atomic(
                assessment_path,
                assessment.model_dump(mode="json", by_alias=True),
                sort_keys=True,
            )
            unit_keys.append(
                ReviewUnitUnblindingKeyV2R2(
                    case_code=case_code,
                    unit_code=unit_code,
                    review_unit_id=prepared.review_unit_id,
                    evidence_id_by_witness_code=dict(sorted(evidence_id_by_code.items())),
                    hidden_witness_code=hidden_witness_code,
                    machine_assessment=assessment,
                    machine_assessment_artifact_path=assessment_relative,
                    machine_assessment_artifact_sha256=file_sha256(assessment_path),
                )
            )
            blinded_units.append(
                BlindedReviewUnitV2R2(
                    unit_code=unit_code,
                    review_domain=prepared.review_domain,
                    witness_codes=tuple(sorted(evidence_id_by_code)),
                    exact_question=prepared.exact_question,
                    required_observations=prepared.required_observations,
                    evidence_path=evidence_relative,
                )
            )
            overlay_units.append((unit_code, prepared, evidence_id_by_code))

        overlay_path = case_dir / "review.add.xml"
        write_text_atomic(
            overlay_path,
            _render_display_overlay(
                overlay_units,
                candidate_sha256=candidate_sha256,
                seed=seed,
            ),
        )
        write_text_atomic(case_dir / "review.sumocfg", _render_sumo_config())
        review_html_path = case_dir / "review.html"
        write_text_atomic(
            review_html_path,
            _render_review_html(
                case_code=case_code,
                units=tuple(sorted(blinded_units, key=lambda item: item.unit_code)),
            ),
        )

        stratum = base_key_case["stratum"]
        blinded_cases.append(
            BlindedAttentionCaseV2R2(
                case_code=case_code,
                city_group=stratum["city_group"],
                morphology=stratum["morphology"],
                traffic_side=stratum["traffic_side"],
                mode_features=tuple(stratum["mode_features"]),
                review_material_path=f"reviewer-visible/{case_code}/review.html",
                units=tuple(sorted(blinded_units, key=lambda item: item.unit_code)),
            )
        )
        corridor_summaries.append(
            ReviewSamplingCorridorV2R2(
                corridor_key=corridor_key,
                atomic_witness_population_count=compression.ledger.witness_count,
                conflict_site_population_count=len(compression.site_review_cases),
                selected_conflict_site_count=len(selected_sites),
                negative_pair_population_count=sum(
                    stratum.population_count for stratum in compression.negative_pair_strata
                ),
                selected_negative_pair_count=len(selected_negatives),
                controlled_binding_hard_count=len(hard_controls),
                selected_controlled_binding_count=len(hard_controls),
                pedestrian_coverage_gap_count=len(coverage_gaps),
                selected_pedestrian_coverage_gap_count=len(coverage_gaps),
                independent_hidden_witness_count=independent_hidden_count,
            )
        )

    dataset = BlindedAttentionDatasetV2R2(
        trial_id=trial.trial_id,
        created_at=created_at,
        cases=tuple(sorted(blinded_cases, key=lambda item: item.case_code)),
    )
    dataset_relative = "held_out_review.blinded-attention-dataset.v2-r2.json"
    dataset_path = destination / dataset_relative
    write_json_atomic(dataset_path, dataset.model_dump(mode="json", by_alias=True), sort_keys=True)

    evaluation_key = AttentionEvaluationKeyV2R2(
        trial_id=trial.trial_id,
        blinded_dataset_sha256=file_sha256(dataset_path),
        blinding_seed=seed,
        units=tuple(sorted(unit_keys, key=lambda item: item.unit_code)),
    )
    evaluation_relative = "restricted/held_out_review.attention-evaluation-key.v2-r2.json"
    evaluation_path = destination / evaluation_relative
    write_json_atomic(
        evaluation_path,
        evaluation_key.model_dump(mode="json", by_alias=True),
        sort_keys=True,
    )

    ledger_payload = {
        "trial_id": trial.trial_id,
        "execution_parent_sha256": file_sha256(parent_path),
        "study_sampling_policy_sha256": file_sha256(policy_path),
        "blinding_seed_sha256": trial.blinding_seed_sha256,
        "producer": producer,
        "corridors": tuple(sorted(corridor_summaries, key=lambda item: item.corridor_key)),
        "strata": tuple(sorted(sampling_strata, key=lambda item: item.stratum_id)),
        "atomic_witness_population_count": sum(
            item.atomic_witness_population_count for item in corridor_summaries
        ),
        "conflict_site_population_count": sum(
            item.conflict_site_population_count for item in corridor_summaries
        ),
        "selected_conflict_site_count": sum(
            item.selected_conflict_site_count for item in corridor_summaries
        ),
        "negative_pair_population_count": sum(
            item.negative_pair_population_count for item in corridor_summaries
        ),
        "selected_negative_pair_count": sum(
            item.selected_negative_pair_count for item in corridor_summaries
        ),
        "controlled_binding_hard_count": sum(
            item.controlled_binding_hard_count for item in corridor_summaries
        ),
        "pedestrian_coverage_gap_count": sum(
            item.pedestrian_coverage_gap_count for item in corridor_summaries
        ),
        "independent_hidden_witness_count": sum(
            item.independent_hidden_witness_count for item in corridor_summaries
        ),
        "automatic_promotion_gate": GateStatus.BLOCKED,
    }
    provisional_ledger = ReviewSamplingLedgerV2R2.model_construct(
        ledger_id=stable_id("manifest", {"pending": True}),
        **ledger_payload,
    )
    ledger = ReviewSamplingLedgerV2R2(
        ledger_id=stable_id("manifest", provisional_ledger.identity_payload()),
        **ledger_payload,
    )
    ledger_relative = "restricted/held_out_review.sampling-ledger.v2-r2.json"
    ledger_path = destination / ledger_relative
    write_json_atomic(ledger_path, ledger.model_dump(mode="json", by_alias=True), sort_keys=True)

    _verify_reviewer_visible_blinding(destination, seed=seed)
    artifacts = tuple(
        ReviewPackageArtifactV2R2(
            path=path.relative_to(destination).as_posix(),
            sha256=file_sha256(path),
            visibility=(
                "restricted"
                if path.relative_to(destination).as_posix().startswith("restricted/")
                else "reviewer-visible"
            ),
        )
        for path in sorted(
            (path for path in destination.rglob("*") if path.is_file()),
            key=lambda item: item.relative_to(destination).as_posix(),
        )
    )
    manifest = HeldOutReviewPackageManifestV2R2(
        trial_id=trial.trial_id,
        trial_instance_sha256=file_sha256(trial_path),
        execution_parent_sha256=file_sha256(parent_path),
        study_sampling_policy_sha256=file_sha256(policy_path),
        producer=producer,
        dataset_path=dataset_relative,
        dataset_sha256=file_sha256(dataset_path),
        evaluation_key_path=evaluation_relative,
        evaluation_key_sha256=file_sha256(evaluation_path),
        sampling_ledger_path=ledger_relative,
        sampling_ledger_sha256=file_sha256(ledger_path),
        artifacts=artifacts,
        automatic_promotion_gate=GateStatus.BLOCKED,
    )
    manifest_path = destination / "held_out_review.package-manifest.v2-r2.json"
    write_json_atomic(
        manifest_path,
        manifest.model_dump(mode="json", by_alias=True),
        sort_keys=True,
    )
    return dataset, ledger, manifest


def _prepare_conflict_site_unit(
    *,
    site: ConflictSiteReviewCase,
    inclusion_probability: float,
    witness_by_id: dict[str, AtomicConflictWitness],
    cluster_by_id: dict[str, ConflictReviewCluster],
    entity_index: dict[tuple[str, str], CanonicalEntity],
    seed: str,
) -> _PreparedUnit:
    presented_ids, hidden_id = select_presented_site_witnesses(
        site=site,
        cluster_by_id=cluster_by_id,
        seed=seed,
    )
    records = tuple(
        _path_relation_evidence(witness_by_id[witness_id], entity_index)
        for witness_id in presented_ids
    )
    position = _cell_position(entity_index, site.pedestrian_physical_cell_id)
    witnesses = tuple(witness_by_id[witness_id] for witness_id in site.atomic_witness_ids)
    finding_categories = tuple(
        sorted(
            {
                f"pedestrian-path-relation:{witness.certainty}:{witness.conflict_reason}"
                for witness in witnesses
            }
        )
    )
    return _PreparedUnit(
        review_unit_id=site.site_review_case_id,
        unit_kind="conflict-site",
        review_domain="pedestrian-path-relation",
        inclusion_probability=inclusion_probability,
        membership_root=site.membership_merkle_root,
        exact_question=_PATH_RELATION_QUESTION,
        required_observations=_PATH_RELATION_OBSERVATIONS,
        position_xy=position,
        presented_evidence=records,
        hidden_evidence_id=hidden_id,
        visible_details={
            "displayed_path_set_count": len(records),
            "review_scope": "one pedestrian facility and its displayed vehicle path families",
        },
        finding_categories=finding_categories,
        safety_critical=any(witness.hard_safety_finding for witness in witnesses),
    )


def _prepare_negative_pair_unit(
    *,
    sample: NegativePairSample,
    inclusion_probability: float,
    entity_index: dict[tuple[str, str], CanonicalEntity],
    trial_id: str,
) -> _PreparedUnit:
    review_unit_id = stable_id(
        "review",
        {"trial_id": trial_id, "path-relation-sample": sample.sample_id},
    )
    evidence = _path_relation_evidence_from_movements(
        evidence_id=sample.sample_id,
        pedestrian_movement_id=sample.pedestrian_movement_id,
        vehicle_movement_id=sample.conflicting_movement_id,
        entity_index=entity_index,
    )
    return _PreparedUnit(
        review_unit_id=review_unit_id,
        unit_kind="negative-pair",
        review_domain="pedestrian-path-relation",
        inclusion_probability=inclusion_probability,
        membership_root=_single_member_root(sample.sample_id),
        exact_question=_PATH_RELATION_QUESTION,
        required_observations=_PATH_RELATION_OBSERVATIONS,
        position_xy=_cell_position(entity_index, sample.physical_cell_id),
        presented_evidence=(evidence,),
        hidden_evidence_id=None,
        visible_details={
            "displayed_path_set_count": 1,
            "review_scope": "one pedestrian facility and one vehicle path",
        },
        finding_categories=("machine-path-relation-finding-absent",),
        safety_critical=False,
    )


def _prepare_control_binding_unit(
    *,
    assessment: ControlledPedestrianBindingAssessment,
    entity_index: dict[tuple[str, str], CanonicalEntity],
    trial_id: str,
) -> _PreparedUnit:
    review_unit_id = stable_id(
        "review",
        {"trial_id": trial_id, "controlled-binding-assessment": assessment.assessment_id},
    )
    movement_shapes = _movement_shapes(entity_index, assessment.movement_id)
    visible_payload = {
        "controller_references": assessment.raw_controller_ids,
        "link_indices": assessment.link_indices,
        "link_index2_present": assessment.link_index2_present,
        "owner_junction_types": assessment.owner_junction_types,
        "program_sources": tuple(str(source) for source in assessment.program_sources),
        "phase_state_length_sets": tuple(assessment.phase_state_lengths_by_program.values()),
        "indexed_state_sets": tuple(assessment.indexed_states_by_program.values()),
        "shared_signal_index_movement_count": len(assessment.shared_index_movement_ids),
        "controller_physical_cell_count": len(assessment.shared_controller_physical_cell_ids),
        "raw_connection_xml": assessment.raw_connection_xml,
        "paths": _visible_paths(("pedestrian", movement_shapes)),
    }
    path_shapes = tuple(("pedestrian", shape) for shape in movement_shapes)
    position = assessment.review_position_xy or _movement_position(entity_index, assessment.movement_id)
    return _PreparedUnit(
        review_unit_id=review_unit_id,
        unit_kind="controlled-binding",
        review_domain="signal-control-configuration",
        inclusion_probability=1.0,
        membership_root=_single_member_root(assessment.assessment_id),
        exact_question=_CONTROL_QUESTION,
        required_observations=_CONTROL_OBSERVATIONS,
        position_xy=position,
        presented_evidence=(
            _PresentedEvidence(
                evidence_id=assessment.assessment_id,
                visible_payload=visible_payload,
                path_shapes=path_shapes,
            ),
        ),
        hidden_evidence_id=None,
        visible_details={"review_scope": "one pedestrian signal-control binding"},
        finding_categories=(
            f"controlled-pedestrian-binding:{assessment.primary_class}",
            *assessment.secondary_flags,
        ),
        safety_critical=assessment.hard_structural_error,
    )


def _prepare_coverage_gap_unit(
    *,
    gap: PedestrianCoverageGap,
    trial_id: str,
) -> _PreparedUnit:
    review_unit_id = stable_id(
        "review",
        {"trial_id": trial_id, "pedestrian-coverage-gap": gap.coverage_gap_id},
    )
    shape = tuple(gap.crossing_shape_xy)
    position = gap.position_xy or _shape_midpoint((shape,))
    return _PreparedUnit(
        review_unit_id=review_unit_id,
        unit_kind="pedestrian-coverage-gap",
        review_domain="pedestrian-facility-coverage",
        inclusion_probability=1.0,
        membership_root=_single_member_root(gap.coverage_gap_id),
        exact_question=_COVERAGE_QUESTION,
        required_observations=_COVERAGE_OBSERVATIONS,
        position_xy=position,
        presented_evidence=(
            _PresentedEvidence(
                evidence_id=gap.coverage_gap_id,
                visible_payload={
                    "crossing_shape_xy": shape,
                    "crossing_width_m": gap.crossing_width_m,
                    "permission_contract": gap.permission_contract,
                    "owner_candidate_count": len(gap.owner_candidate_physical_cell_ids),
                    "walkingarea_chain_candidate_count": len(
                        gap.walkingarea_chain_candidate_signatures
                    ),
                    "boundary_port_candidate_count": len(gap.boundary_port_candidate_ids),
                    "affected_movement_candidate_count": len(gap.affected_movement_candidate_ids),
                },
                path_shapes=(("pedestrian", shape),) if shape else (),
            ),
        ),
        hidden_evidence_id=None,
        visible_details={"review_scope": "one mapped pedestrian facility"},
        finding_categories=(
            f"pedestrian-coverage:{gap.primary_classification}",
            *gap.secondary_classifications,
            *(f"ood:{dimension}" for dimension in gap.ood_dimensions),
        ),
        safety_critical=gap.primary_classification in {
            "safety-coverage-blocker",
            "structural-blocker",
        },
    )


def _path_relation_evidence(
    witness: AtomicConflictWitness,
    entity_index: dict[tuple[str, str], CanonicalEntity],
) -> _PresentedEvidence:
    return _path_relation_evidence_from_movements(
        evidence_id=witness.atomic_witness_id,
        pedestrian_movement_id=witness.pedestrian_movement_id,
        vehicle_movement_id=witness.conflicting_movement_id,
        entity_index=entity_index,
    )


def _path_relation_evidence_from_movements(
    *,
    evidence_id: str,
    pedestrian_movement_id: str,
    vehicle_movement_id: str,
    entity_index: dict[tuple[str, str], CanonicalEntity],
) -> _PresentedEvidence:
    pedestrian_shapes = _movement_shapes(entity_index, pedestrian_movement_id)
    vehicle_shapes = _movement_shapes(entity_index, vehicle_movement_id)
    return _PresentedEvidence(
        evidence_id=evidence_id,
        visible_payload={
            "paths": (
                *_visible_paths(("pedestrian", pedestrian_shapes)),
                *_visible_paths(("vehicle", vehicle_shapes)),
            )
        },
        path_shapes=tuple(
            [*(('pedestrian', shape) for shape in pedestrian_shapes), *(('vehicle', shape) for shape in vehicle_shapes)]
        ),
    )


def _movement_shapes(
    entity_index: dict[tuple[str, str], CanonicalEntity],
    movement_id: str,
) -> tuple[tuple[tuple[float, float], ...], ...]:
    movement = entity_index.get(("movement", movement_id))
    if movement is None:
        return ()
    internal_path_id = movement.payload.get("internal_path_id")
    path = entity_index.get(("internal_path", str(internal_path_id)))
    if path is None:
        return ()
    shapes: list[tuple[tuple[float, float], ...]] = []
    for variant in path.payload.get("path_variants", ()):
        for segment in variant.get("path", {}).get("segments", ()):
            shape = tuple((float(point[0]), float(point[1])) for point in segment.get("shape_xy", ()))
            if len(shape) >= 2:
                shapes.append(shape)
    return tuple(shapes)


def _visible_paths(
    item: tuple[str, tuple[tuple[tuple[float, float], ...], ...]],
) -> tuple[dict[str, Any], ...]:
    role, shapes = item
    return tuple({"role": role, "shape_xy": shape} for shape in shapes)


def _movement_position(
    entity_index: dict[tuple[str, str], CanonicalEntity],
    movement_id: str,
) -> tuple[float, float]:
    shapes = _movement_shapes(entity_index, movement_id)
    if shapes:
        return _shape_midpoint(shapes)
    movement = entity_index.get(("movement", movement_id))
    if movement is not None and movement.owner_physical_cell_ids:
        return _cell_position(entity_index, movement.owner_physical_cell_ids[0])
    raise ValueError(f"Movement {movement_id} has no reviewable position.")


def _cell_position(
    entity_index: dict[tuple[str, str], CanonicalEntity],
    cell_id: str,
) -> tuple[float, float]:
    cell = entity_index.get(("physical_cell", cell_id))
    if cell is None:
        raise ValueError(f"Physical cell {cell_id} is absent from canonical evidence.")
    position = cell.payload.get("position_xy")
    if not isinstance(position, (list, tuple)) or len(position) != 2:
        raise ValueError(f"Physical cell {cell_id} has no review position.")
    return float(position[0]), float(position[1])


def _shape_midpoint(
    shapes: tuple[tuple[tuple[float, float], ...], ...],
) -> tuple[float, float]:
    points = [point for shape in shapes for point in shape]
    if not points:
        raise ValueError("Review geometry cannot be empty.")
    return (
        sum(point[0] for point in points) / len(points),
        sum(point[1] for point in points) / len(points),
    )


def _geo_converter(net_file: Path) -> Callable[[float, float], tuple[float, float]]:
    from sumolib.net import readNet

    network = readNet(str(net_file), withInternal=True)

    def convert(x: float, y: float) -> tuple[float, float]:
        longitude, latitude = network.convertXY2LonLat(x, y)
        return float(longitude), float(latitude)

    return convert


def _review_position(
    position_xy: tuple[float, float],
    *,
    convert_to_lon_lat: Callable[[float, float], tuple[float, float]],
    fallback_lat: float,
    fallback_lon: float,
) -> dict[str, float]:
    try:
        longitude, latitude = convert_to_lon_lat(*position_xy)
    except (ArithmeticError, AttributeError, RuntimeError, ValueError):
        latitude, longitude = fallback_lat, fallback_lon
    return {
        "x": round(position_xy[0], 6),
        "y": round(position_xy[1], 6),
        "latitude": round(latitude, 7),
        "longitude": round(longitude, 7),
    }


def _map_evidence_urls(latitude: float, longitude: float) -> tuple[str, str]:
    osm = (
        f"https://www.openstreetmap.org/?mlat={latitude:.7f}&mlon={longitude:.7f}"
        f"#map=19/{latitude:.7f}/{longitude:.7f}"
    )
    google = "https://www.google.com/maps/search/?" + urlencode(
        {"api": "1", "query": f"{latitude:.7f},{longitude:.7f}"}
    )
    return osm, google


def _render_display_overlay(
    units: list[tuple[str, _PreparedUnit, dict[str, str]]],
    *,
    candidate_sha256: str,
    seed: str,
) -> str:
    lines = ["<additional>"]
    for unit_code, prepared, evidence_id_by_code in sorted(units, key=lambda item: item[0]):
        x, y = prepared.position_xy
        lines.extend(
            (
                f"  <poi id={quoteattr(unit_code)} x={quoteattr(f'{x:.6f}')} "
                f"y={quoteattr(f'{y:.6f}')} color=\"64,128,255\" layer=\"100\">",
                "    <param key=\"display_only\" value=\"true\"/>",
                f"    <param key=\"review_unit_code\" value={quoteattr(unit_code)}/>",
                f"    <param key=\"candidate_sha256\" value={quoteattr(candidate_sha256)}/>",
                "  </poi>",
            )
        )
        for evidence in prepared.presented_evidence:
            witness_code = _blind_code("witness", seed, evidence.evidence_id)
            if evidence_id_by_code.get(witness_code) != evidence.evidence_id:
                raise ValueError("Overlay witness code does not match the blinded unit.")
            for index, (role, shape) in enumerate(evidence.path_shapes):
                if len(shape) < 2:
                    continue
                color = "0,122,204" if role == "pedestrian" else "230,126,34"
                shape_text = " ".join(f"{point[0]:.6f},{point[1]:.6f}" for point in shape)
                poly_id = f"{unit_code}-{witness_code}-{role}-{index}"
                lines.append(
                    f"  <poly id={quoteattr(poly_id)} color={quoteattr(color)} "
                    f"fill=\"false\" layer=\"99\" lineWidth=\"2\" shape={quoteattr(shape_text)}/>"
                )
    lines.append("</additional>")
    return "\n".join(lines) + "\n"


def _render_sumo_config() -> str:
    return """<configuration>
  <input>
    <net-file value="network.net.xml"/>
    <additional-files value="review.add.xml"/>
  </input>
  <time>
    <begin value="0"/>
    <end value="1"/>
  </time>
</configuration>
"""


def _render_review_html(
    *,
    case_code: str,
    units: tuple[BlindedReviewUnitV2R2, ...],
) -> str:
    rows = []
    domain_labels = {
        "pedestrian-path-relation": "Pedestrian / vehicle path relation",
        "signal-control-configuration": "Signal-control configuration",
        "pedestrian-facility-coverage": "Pedestrian facility representation",
    }
    for unit in units:
        relative_evidence = Path(unit.evidence_path).name
        rows.append(
            "<tr>"
            f"<td><code>{html.escape(unit.unit_code)}</code></td>"
            f"<td>{html.escape(domain_labels[unit.review_domain])}</td>"
            f"<td>{html.escape(unit.exact_question)}</td>"
            f"<td><a href=\"units/{html.escape(relative_evidence)}\">evidence</a></td>"
            "</tr>"
        )
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <title>Torii blinded corridor review {html.escape(case_code)}</title>
  <style>
    body {{ font-family: system-ui, sans-serif; margin: 2rem; color: #1d2733; }}
    table {{ border-collapse: collapse; width: 100%; }}
    th, td {{ border: 1px solid #ccd4dc; padding: .55rem; text-align: left; vertical-align: top; }}
    th {{ background: #eef3f7; }}
    code {{ white-space: nowrap; }}
    .notice {{ border-left: 4px solid #3678a8; padding: .75rem 1rem; background: #f2f7fb; }}
  </style>
</head>
<body>
  <h1>Blinded corridor review <code>{html.escape(case_code)}</code></h1>
  <p class="notice">Machine classifications, sampling roles, peer decisions, and hidden-member roles are not shown. Review every item independently.</p>
  <p>Network context: <a href="network.net.xml">SUMO network</a>, <a href="review.add.xml">display-only overlay</a>, <a href="review.sumocfg">SUMO GUI configuration</a>.</p>
  <p>The blue paths are pedestrian paths; orange paths are vehicle paths. Overlay markers are visual indices only and do not modify network behavior.</p>
  <table>
    <thead><tr><th>Unit</th><th>Domain</th><th>Question</th><th>Material</th></tr></thead>
    <tbody>{''.join(rows)}</tbody>
  </table>
  <p>Map data and derived network context: © OpenStreetMap contributors, ODbL.</p>
</body>
</html>
"""


def _base_candidate_net(base_package: Path, base_case: dict[str, Any]) -> tuple[Path, str]:
    paths = base_case["candidate_artifact_path_by_variant_code"]
    hashes = base_case["candidate_sha256_by_variant_code"]
    if not paths or set(paths) != set(hashes):
        raise ValueError("Base review case has incomplete candidate artifacts.")
    variant_code = sorted(paths)[0]
    source = (base_package / paths[variant_code]).resolve(strict=True)
    if not source.is_relative_to(base_package):
        raise ValueError("Base candidate path escapes the review package.")
    expected = hashes[variant_code]
    if file_sha256(source) != expected:
        raise ValueError("Base review candidate hash failed.")
    return source, expected


def _require_complete_machine_census(
    corridor_key: str,
    compression: LosslessReviewCompressionReport,
) -> None:
    if (
        compression.ledger.coverage_gate is not GateStatus.PASS
        or compression.machine_review_ready_gate is not GateStatus.PASS
        or compression.atomic_membership_coverage != 1.0
        or compression.lost_witness_count
        or compression.duplicate_membership_count
        or compression.extraneous_membership_count
        or compression.mixed_hard_key_violation_count
    ):
        raise ValueError(f"Corridor {corridor_key} lacks a complete lossless machine census.")


def _read_restricted_seed(path: Path, *, expected_sha256: str) -> str:
    payload = _read_json(path)
    seed = str(payload.get("blinding_seed", ""))
    digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()
    if len(seed) < 32 or digest != expected_sha256 or payload.get("blinding_seed_sha256") != digest:
        raise ValueError("Restricted v2-R2 blinding seed does not match its commitment.")
    return seed


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.resolve(strict=True).read_text(encoding="utf-8"))


def _blind_code(prefix: Literal["case", "unit", "witness"], seed: str, identity: str) -> str:
    digest = hashlib.sha256(f"{seed}\x1f{prefix}\x1f{identity}".encode("utf-8")).hexdigest()
    return f"{prefix}-{digest[:12]}"


def _single_member_root(identity: str) -> str:
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()


def _verify_reviewer_visible_blinding(root: Path, *, seed: str) -> None:
    forbidden = (
        seed,
        '"machine_label"',
        '"machine_attention"',
        '"finding_categories"',
        '"hidden_witness"',
        '"hidden_member"',
        '"unit_kind"',
        '"inclusion_probability"',
        '"negative-pair"',
        '"conflict-site"',
    )
    visible_paths = [
        root / "held_out_review.blinded-attention-dataset.v2-r2.json",
        *(path for path in (root / "reviewer-visible").rglob("*") if path.is_file()),
    ]
    for path in visible_paths:
        text = path.read_text(encoding="utf-8", errors="ignore")
        leaked = [token for token in forbidden if token in text]
        if leaked:
            raise ValueError(f"Reviewer-visible artifact leaks restricted roles: {path}: {leaked}")
