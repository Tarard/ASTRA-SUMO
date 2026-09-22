from __future__ import annotations

import json
import subprocess
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from torii_sumo.core.artifact_io import write_json_atomic
from torii_sumo.core.candidate_contracts import file_sha256

from .enums import GateStatus
from .held_out_corpus_contracts import (
    HeldOutCorpusMachineManifest,
    HeldOutCorpusMachineReport,
    HeldOutCorpusSnapshotReport,
)
from .held_out_review_v2_contracts import (
    BlindedAttentionDatasetV2R2,
    HeldOutReplacementAttemptLedgerV2,
    HeldOutReviewExecutionParentV2R2,
    HeldOutReviewPackageManifestV2R2,
    HeldOutReviewTrialInstanceV2R2,
    ReviewSamplingLedgerV2R2,
    ReviewStudySamplingPolicyV2R2,
)
from .ids import stable_id
from .net_replay import NetReplayReport
from .pedestrian_control_census import ControlledPedestrianBindingCensus
from .pedestrian_row_contracts import ROWExperimentReport
from .review import PedestrianCoverageGap
from .run_identity import (
    HeldOutMachineRunIdentity,
    capture_code_producer_identity,
)
from .stage1_review_ready_contracts import (
    Stage1CoverageGapEvidence,
    Stage1CoverageSummary,
    Stage1EvidenceArtifact,
    Stage1EvidenceDependency,
    Stage1GateEvidence,
    Stage1MachineReviewReadyProvenance,
    Stage1MachineSummary,
    Stage1PCBSummary,
    Stage1ReviewPackageSummary,
    Stage1RWCReplacementContribution,
    Stage1RWCSummary,
    Stage1ROWSummary,
    Stage1SnapshotSummary,
)


_RUNTIME_SPECIAL_TYPES = frozenset({"rail_crossing", "rail_signal"})


def freeze_stage1_machine_review_ready_provenance(
    *,
    repository_root: Path,
    snapshot_root: Path,
    machine_root: Path,
    review_package_root: Path,
    review_package_repeat_root: Path,
    row_report_file: Path,
    row_repeat_report_file: Path,
    effective_corpus_file: Path,
    replacement_attempt_ledger_file: Path,
    execution_parent_file: Path,
    trial_instance_file: Path,
    study_sampling_policy_file: Path,
    output_file: Path,
    recorded_at: datetime,
) -> Stage1MachineReviewReadyProvenance:
    """Freeze the complete machine-side Stage 1-M evidence without promoting it."""

    if recorded_at.tzinfo is None:
        raise ValueError("Stage 1-M provenance timestamps must include a timezone.")
    root = repository_root.resolve(strict=True)
    snapshots = snapshot_root.resolve(strict=True)
    machine = machine_root.resolve(strict=True)
    review_package = review_package_root.resolve(strict=True)
    review_repeat = review_package_repeat_root.resolve(strict=True)
    row_path = row_report_file.resolve(strict=True)
    row_repeat_path = row_repeat_report_file.resolve(strict=True)
    effective_path = effective_corpus_file.resolve(strict=True)
    replacement_path = replacement_attempt_ledger_file.resolve(strict=True)
    parent_path = execution_parent_file.resolve(strict=True)
    trial_path = trial_instance_file.resolve(strict=True)
    sampling_policy_path = study_sampling_policy_file.resolve(strict=True)

    producer = capture_code_producer_identity(root)
    tracked_paths = _tracked_paths(root)

    snapshot_report_path = snapshots / "held_out_corpus.snapshot-report.json"
    snapshot_manifest_path = snapshots / "held_out_corpus.snapshot-manifest.json"
    snapshot_report = _load_model(snapshot_report_path, HeldOutCorpusSnapshotReport)
    snapshot_manifest = _read_json(snapshot_manifest_path)
    snapshot_missing, snapshot_hash_failures = _verify_absolute_manifest(
        snapshot_manifest.get("artifacts", ())
    )
    reference_complete_count = sum(item.reference_complete for item in snapshot_report.corridors)
    unconfirmed_feature_count = sum(
        len(item.unconfirmed_preregistered_features) for item in snapshot_report.corridors
    )
    snapshot_summary = Stage1SnapshotSummary(
        status=snapshot_report.status,
        city_extract_count=len(snapshot_report.city_extracts),
        corridor_count=len(snapshot_report.corridors),
        reference_complete_corridor_count=reference_complete_count,
        unconfirmed_feature_count=unconfirmed_feature_count,
        manifest_artifact_count=len(snapshot_manifest.get("artifacts", ())),
        manifest_missing_artifact_count=snapshot_missing,
        manifest_hash_failure_count=snapshot_hash_failures,
    )

    machine_report_path = machine / "held_out_corpus.machine-report.json"
    machine_manifest_path = machine / "held_out_corpus.machine-manifest.json"
    machine_report = _load_model(machine_report_path, HeldOutCorpusMachineReport)
    machine_manifest = _load_model(machine_manifest_path, HeldOutCorpusMachineManifest)
    run_identity_path = Path(machine_report.run_identity_path).resolve(strict=True)
    run_identity = _load_model(run_identity_path, HeldOutMachineRunIdentity)
    if machine_report.run_identity_sha256 != file_sha256(run_identity_path):
        raise ValueError("Machine report run-identity hash does not close.")
    if machine_manifest.producer != run_identity.producer:
        raise ValueError("Machine manifest and run-identity producers differ.")
    machine_missing, machine_hash_failures = _verify_absolute_manifest(
        tuple(item.model_dump(mode="json") for item in machine_manifest.artifacts)
    )
    replay_pass_count = 0
    for result in machine_report.results:
        replay = _load_model(
            machine / "cases" / result.corridor_key / "net-replay.json",
            NetReplayReport,
        )
        replay_pass_count += replay.status is GateStatus.PASS
    machine_labels = dict(sorted(Counter(item.machine_label for item in machine_report.results).items()))
    machine_summary = Stage1MachineSummary(
        expected_case_count=machine_report.expected_case_count,
        processed_case_count=machine_report.processed_case_count,
        pipeline_pass_count=sum(item.pipeline_status is GateStatus.PASS for item in machine_report.results),
        semantic_replay_pass_count=replay_pass_count,
        source_osm_immutable_count=sum(item.source_osm_immutable for item in machine_report.results),
        machine_label_counts=machine_labels,
        manifest_artifact_count=len(machine_manifest.artifacts),
        manifest_missing_artifact_count=machine_missing,
        manifest_hash_failure_count=machine_hash_failures,
        report_sha256=file_sha256(machine_report_path),
        manifest_sha256=file_sha256(machine_manifest_path),
        run_identity_sha256=file_sha256(run_identity_path),
        evidence_build_status=machine_report.evidence_build_status,
        automatic_promotion_gate=machine_report.automatic_promotion_gate,
    )

    replacement_ledger = _load_model(replacement_path, HeldOutReplacementAttemptLedgerV2)
    replacement_keys = tuple(sorted(slot.selected_corridor_key for slot in replacement_ledger.slots))
    pcb_summary = _summarize_pcb(machine, machine_report, replacement_keys)
    rwc_summary = _summarize_rwc(machine, machine_report, replacement_keys)
    coverage_summary = _summarize_coverage(machine, machine_report)

    row_report = _load_model(row_path, ROWExperimentReport)
    row_repeat_report = _load_model(row_repeat_path, ROWExperimentReport)
    row_sha256 = file_sha256(row_path)
    row_repeat_sha256 = file_sha256(row_repeat_path)
    if row_report != row_repeat_report:
        raise ValueError("ROW-1 repeat reports differ semantically.")
    row_summary = Stage1ROWSummary(
        report_id=row_report.report_id,
        report_sha256=row_sha256,
        independent_repeat_sha256=row_repeat_sha256,
        exact_repeat_match=row_sha256 == row_repeat_sha256,
        case_count=len(row_report.cases),
        runtime_probe_count=row_report.runtime_probe_count,
        failed_case_count=row_report.failed_case_count,
        unsafe_false_pass_count=row_report.unsafe_false_pass_count,
        source_insufficient_forced_decision_count=row_report.source_insufficient_forced_decision_count,
        expected_answer_model_claim_read_count=row_report.expected_answer_model_claim_read_count,
        status=row_report.status,
        field_truth_claimed=False,
        automatic_promotion_gate=row_report.automatic_promotion_gate,
    )

    parent = _load_model(parent_path, HeldOutReviewExecutionParentV2R2)
    trial = _load_model(trial_path, HeldOutReviewTrialInstanceV2R2)
    sampling_policy = _load_model(sampling_policy_path, ReviewStudySamplingPolicyV2R2)
    if trial.execution_parent_sha256 != file_sha256(parent_path):
        raise ValueError("Review trial does not bind the execution parent.")
    if trial.study_sampling_policy_sha256 != file_sha256(sampling_policy_path):
        raise ValueError("Review trial does not bind the study sampling policy.")
    if sampling_policy.automatic_promotion_gate is not GateStatus.BLOCKED:
        raise ValueError("Review sampling policy unexpectedly authorizes promotion.")
    if parent.machine_manifest_sha256 != file_sha256(machine_manifest_path):
        raise ValueError("Review execution parent does not bind the machine manifest.")
    if parent.effective_corpus_sha256 != file_sha256(effective_path):
        raise ValueError("Review execution parent does not bind the effective corpus.")

    package_manifest_path = review_package / "held_out_review.package-manifest.v2-r2.json"
    package_manifest = _load_model(package_manifest_path, HeldOutReviewPackageManifestV2R2)
    package_missing, package_hash_failures = _verify_relative_manifest(
        review_package, package_manifest.artifacts
    )
    if package_missing or package_hash_failures:
        raise ValueError("Formal review-package artifact closure failed.")
    dataset_path = review_package / package_manifest.dataset_path
    sampling_ledger_path = review_package / package_manifest.sampling_ledger_path
    evaluation_key_path = review_package / package_manifest.evaluation_key_path
    dataset = _load_model(dataset_path, BlindedAttentionDatasetV2R2)
    sampling_ledger = _load_model(sampling_ledger_path, ReviewSamplingLedgerV2R2)
    if package_manifest.dataset_sha256 != file_sha256(dataset_path):
        raise ValueError("Review package dataset hash does not close.")
    if package_manifest.sampling_ledger_sha256 != file_sha256(sampling_ledger_path):
        raise ValueError("Review package sampling-ledger hash does not close.")
    if package_manifest.evaluation_key_sha256 != file_sha256(evaluation_key_path):
        raise ValueError("Restricted review key hash does not close.")
    package_hashes = _relative_file_hashes(review_package)
    repeat_hashes = _relative_file_hashes(review_repeat)
    repeat_differences = sum(
        package_hashes.get(path) != repeat_hashes.get(path)
        for path in set(package_hashes) | set(repeat_hashes)
    )
    review_summary = Stage1ReviewPackageSummary(
        trial_id=package_manifest.trial_id,
        corridor_package_count=len(dataset.cases),
        review_unit_count=sum(len(case.units) for case in dataset.cases),
        atomic_witness_population_count=sampling_ledger.atomic_witness_population_count,
        selected_conflict_site_count=sampling_ledger.selected_conflict_site_count,
        selected_negative_pair_count=sampling_ledger.selected_negative_pair_count,
        controlled_binding_hard_census_count=sampling_ledger.controlled_binding_hard_count,
        pedestrian_coverage_gap_census_count=sampling_ledger.pedestrian_coverage_gap_count,
        independent_hidden_witness_count=sampling_ledger.independent_hidden_witness_count,
        manifest_artifact_count=len(package_manifest.artifacts),
        package_file_count=len(package_hashes),
        independent_repeat_file_count=len(repeat_hashes),
        repeat_hash_difference_count=repeat_differences,
        reviewer_visible_machine_label_count=package_manifest.reviewer_visible_machine_label_count,
        reviewer_visible_hidden_role_count=package_manifest.reviewer_visible_hidden_role_count,
        reviewer_visible_attention_role_count=package_manifest.reviewer_visible_attention_role_count,
        dataset_sha256=package_manifest.dataset_sha256,
        sampling_ledger_sha256=package_manifest.sampling_ledger_sha256,
        restricted_evaluation_key_sha256=package_manifest.evaluation_key_sha256,
        package_manifest_sha256=file_sha256(package_manifest_path),
        human_decisions_present=package_manifest.human_decisions_present,
        automatic_promotion_gate=package_manifest.automatic_promotion_gate,
    )

    artifacts = _build_artifacts(
        root=root,
        tracked_paths=tracked_paths,
        snapshots=snapshots,
        machine=machine,
        review_package=review_package,
        review_repeat=review_repeat,
        row_path=row_path,
        row_repeat_path=row_repeat_path,
        effective_path=effective_path,
        replacement_path=replacement_path,
        parent_path=parent_path,
        trial_path=trial_path,
        sampling_policy_path=sampling_policy_path,
        package_manifest=package_manifest,
    )
    artifact_path = {artifact.logical_name: artifact.path for artifact in artifacts}
    dependencies = _build_dependencies(artifact_path)
    gates = _build_gates(artifact_path)

    payload: dict[str, Any] = {
        "recorded_at": recorded_at,
        "status": "review-ready",
        "stage_1m_machine_review_ready_gate": GateStatus.PASS,
        "stage_1h_human_validation_gate": GateStatus.BLOCKED,
        "automatic_promotion_gate": GateStatus.BLOCKED,
        "human_decisions_present": False,
        "review_ready_is_not_stage1_exit": True,
        "producer": producer,
        "machine_evidence_producer": machine_manifest.producer,
        "review_package_producer": package_manifest.producer,
        "trial_id": trial.trial_id,
        "artifacts": artifacts,
        "dependencies": dependencies,
        "gates": gates,
        "snapshot": snapshot_summary,
        "machine": machine_summary,
        "pcb": pcb_summary,
        "rwc": rwc_summary,
        "coverage": coverage_summary,
        "row_1": row_summary,
        "review_package": review_summary,
    }
    provisional = Stage1MachineReviewReadyProvenance.model_construct(
        provenance_id=stable_id("manifest", {"pending": True}),
        **payload,
    )
    provenance = Stage1MachineReviewReadyProvenance(
        provenance_id=stable_id("manifest", provisional.identity_payload()),
        **payload,
    )
    write_json_atomic(
        output_file.resolve(),
        provenance.model_dump(mode="json", by_alias=True),
        sort_keys=True,
    )
    return provenance


def _summarize_pcb(
    machine_root: Path,
    report: HeldOutCorpusMachineReport,
    replacement_keys: tuple[str, ...],
) -> Stage1PCBSummary:
    class_counts: Counter[str] = Counter()
    retained_count = 0
    exact_positions = 0
    effective_program_misclassified = 0
    runtime_special_misclassified = 0
    all_assessment_ids: list[str] = []
    for result in report.results:
        census = _load_model(
            machine_root / "cases" / result.corridor_key / "controlled-pedestrian-binding-census.json",
            ControlledPedestrianBindingCensus,
        )
        class_counts.update(census.class_counts)
        if result.corridor_key not in replacement_keys:
            retained_count += census.unresolved_binding_count
        for assessment in census.assessments:
            all_assessment_ids.append(assessment.assessment_id)
            exact_positions += assessment.review_position_xy is not None
            if assessment.primary_class == "ordinary-program-truly-absent":
                effective_program_misclassified += bool(assessment.program_evidence_ids)
                runtime_special_misclassified += bool(
                    set(assessment.owner_junction_types) & _RUNTIME_SPECIAL_TYPES
                )
    if retained_count != 453:
        raise ValueError(f"Frozen PCB-453 retained population changed: {retained_count}.")
    if len(all_assessment_ids) != len(set(all_assessment_ids)):
        raise ValueError("PCB assessment stable IDs are not globally unique.")
    total = len(all_assessment_ids)
    return Stage1PCBSummary(
        frozen_retained_corridor_count=27,
        frozen_unresolved_binding_count=retained_count,
        effective_corridor_count=30,
        effective_unresolved_binding_count=total,
        replacement_contribution_count=total - retained_count,
        class_counts=dict(sorted(class_counts.items())),
        hard_structural_count=(
            class_counts["ordinary-program-truly-absent"]
            + class_counts["program-present-link-invalid"]
        ),
        ambiguous_count=class_counts["stale-or-ambiguous-control-reference"],
        exact_review_position_count=exact_positions,
        effective_program_misclassified_as_missing_count=effective_program_misclassified,
        runtime_special_misclassified_as_ordinary_count=runtime_special_misclassified,
        unique_primary_classification_count=total,
        automatic_promotion_gate=GateStatus.BLOCKED,
    )


def _summarize_rwc(
    machine_root: Path,
    report: HeldOutCorpusMachineReport,
    replacement_keys: tuple[str, ...],
) -> Stage1RWCSummary:
    totals: Counter[str] = Counter()
    retained: Counter[str] = Counter()
    replacements: list[Stage1RWCReplacementContribution] = []
    for result in report.results:
        payload = _read_json(
            machine_root / "cases" / result.corridor_key / "lossless-review-compression.json"
        )
        ledger = payload["ledger"]
        values = {
            "confirmed": int(ledger["confirmed_count"]),
            "potential": int(ledger["potential_count"]),
            "atomic": int(ledger["witness_count"]),
            "clusters": len(payload["clusters"]),
            "sites": len(payload["site_review_cases"]),
            "lost": int(payload["lost_witness_count"]),
            "duplicate": int(payload["duplicate_membership_count"]),
            "extraneous": int(payload["extraneous_membership_count"]),
            "mixed": int(payload["mixed_hard_key_violation_count"]),
        }
        if values["atomic"] != values["confirmed"] + values["potential"]:
            raise ValueError(f"RWC witness counts do not close for {result.corridor_key}.")
        if payload["atomic_membership_coverage"] != 1.0:
            raise ValueError(f"RWC membership coverage failed for {result.corridor_key}.")
        if payload["machine_review_ready_gate"] != GateStatus.PASS.value:
            raise ValueError(f"RWC machine gate failed for {result.corridor_key}.")
        if payload["automatic_promotion_gate"] != GateStatus.BLOCKED.value:
            raise ValueError(f"RWC promotion gate changed for {result.corridor_key}.")
        totals.update(values)
        if result.corridor_key in replacement_keys:
            replacements.append(
                Stage1RWCReplacementContribution(
                    corridor_key=result.corridor_key,
                    confirmed_count=values["confirmed"],
                    potential_count=values["potential"],
                    atomic_witness_count=values["atomic"],
                    cluster_count=values["clusters"],
                    site_review_case_count=values["sites"],
                )
            )
        else:
            retained.update(values)
    if retained["confirmed"] != 34493 or retained["potential"] != 53930 or retained["atomic"] != 88423:
        raise ValueError(f"Frozen RWC-1 retained population changed: {dict(retained)}.")
    return Stage1RWCSummary(
        effective_corridor_count=30,
        effective_confirmed_count=totals["confirmed"],
        effective_potential_count=totals["potential"],
        effective_atomic_witness_count=totals["atomic"],
        effective_cluster_count=totals["clusters"],
        effective_site_review_case_count=totals["sites"],
        replacement_contributions=tuple(sorted(replacements, key=lambda item: item.corridor_key)),
        atomic_membership_coverage=1.0,
        lost_witness_count=totals["lost"],
        duplicate_membership_count=totals["duplicate"],
        extraneous_membership_count=totals["extraneous"],
        mixed_hard_key_violation_count=totals["mixed"],
        automatic_promotion_gate=GateStatus.BLOCKED,
    )


def _summarize_coverage(
    machine_root: Path,
    report: HeldOutCorpusMachineReport,
) -> Stage1CoverageSummary:
    gaps: list[Stage1CoverageGapEvidence] = []
    for result in report.results:
        payload = _read_json(machine_root / "cases" / result.corridor_key / "independent-safety.json")
        for raw_gap in payload["coverage"]["pedestrian_coverage_gaps"]:
            gap = PedestrianCoverageGap.model_validate(raw_gap)
            if gap.position_xy is None:
                raise ValueError(f"Coverage gap lacks an exact review position: {gap.coverage_gap_id}.")
            site_group = (
                "paris-porte-maillot-complex"
                if result.corridor_key == "paris-porte-maillot"
                else f"{result.corridor_key}-facility"
            )
            gaps.append(
                Stage1CoverageGapEvidence(
                    corridor_key=result.corridor_key,
                    coverage_gap_id=gap.coverage_gap_id,
                    crossing_signature=gap.crossing_signature,
                    position_xy=gap.position_xy,
                    primary_classification=gap.primary_classification,
                    secondary_classifications=gap.secondary_classifications,
                    rejection_reasons=gap.rejection_reasons,
                    certification_site_group=site_group,
                )
            )
    return Stage1CoverageSummary(
        effective_coverage_gap_count=len(gaps),
        paris_coverage_gap_count=sum(gap.corridor_key == "paris-porte-maillot" for gap in gaps),
        certification_site_group_count=len({gap.certification_site_group for gap in gaps}),
        gaps=tuple(sorted(gaps, key=lambda gap: gap.coverage_gap_id)),
        field_structural_defect_claimed=False,
    )


def _build_artifacts(
    *,
    root: Path,
    tracked_paths: set[str],
    snapshots: Path,
    machine: Path,
    review_package: Path,
    review_repeat: Path,
    row_path: Path,
    row_repeat_path: Path,
    effective_path: Path,
    replacement_path: Path,
    parent_path: Path,
    trial_path: Path,
    sampling_policy_path: Path,
    package_manifest: HeldOutReviewPackageManifestV2R2,
) -> tuple[Stage1EvidenceArtifact, ...]:
    benchmark = root / "benchmarks" / "corridor_human_modeling_v1"
    specifications = (
        ("contract_workflow", "contract-ci", root / ".github/workflows/corridor-contracts.yml", "tracked-public"),
        (
            "provenance_schema",
            "json-schema",
            root / "schemas/torii.corridor.stage1m-machine-review-ready-provenance.v3.schema.json",
            "tracked-public",
        ),
        ("toolchain_lock", "toolchain-lock", benchmark / "toolchain.lock.json", "tracked-public"),
        ("effective_corpus", "benchmark-spec", effective_path, "tracked-public"),
        ("replacement_ledger", "replacement-ledger", replacement_path, "tracked-public"),
        (
            "source_snapshot_protocol",
            "source-protocol",
            benchmark / "held_out_source_snapshot_protocol.v2.json",
            "tracked-public",
        ),
        (
            "base_review_policy",
            "review-policy",
            benchmark / "held_out_review_preregistration.v2.json",
            "tracked-public",
        ),
        (
            "witness_sampling_policy",
            "sampling-policy",
            benchmark / "review_witness_sampling_policy.v2.json",
            "tracked-public",
        ),
        ("study_sampling_policy", "sampling-policy", sampling_policy_path, "tracked-public"),
        ("execution_parent", "review-parent", parent_path, "tracked-public"),
        ("trial_instance", "review-trial", trial_path, "tracked-public"),
        (
            "prior_retained_provenance",
            "historical-provenance",
            benchmark / "evidence/held_out_machine_run_all_pedestrian_provenance_20260714.v3.json",
            "tracked-public",
        ),
        (
            "review_ready_plan",
            "status-document",
            root / "docs/stage1-machine-review-ready-plan.md",
            "tracked-public",
        ),
        (
            "blind_review_protocol",
            "review-protocol",
            root / "docs/held-out-corridor-blind-review-protocol-v2.md",
            "tracked-public",
        ),
        (
            "implementation_status",
            "status-document",
            root / "docs/torii-corridor-human-modeling-implementation-status.md",
            "tracked-public",
        ),
        (
            "snapshot_report",
            "snapshot-report",
            snapshots / "held_out_corpus.snapshot-report.json",
            "local-machine-evidence",
        ),
        (
            "snapshot_manifest",
            "snapshot-manifest",
            snapshots / "held_out_corpus.snapshot-manifest.json",
            "local-machine-evidence",
        ),
        (
            "machine_run_identity",
            "run-identity",
            machine / "held_out_corpus.run-identity.json",
            "local-machine-evidence",
        ),
        (
            "machine_report",
            "machine-report",
            machine / "held_out_corpus.machine-report.json",
            "local-machine-evidence",
        ),
        (
            "machine_manifest",
            "machine-manifest",
            machine / "held_out_corpus.machine-manifest.json",
            "local-machine-evidence",
        ),
        (
            "paris_coverage_evidence",
            "independent-safety-report",
            machine / "cases/paris-porte-maillot/independent-safety.json",
            "local-machine-evidence",
        ),
        (
            "london_coverage_evidence",
            "independent-safety-report",
            machine / "cases/london-liverpool-street/independent-safety.json",
            "local-machine-evidence",
        ),
        ("row_1_report", "row-experiment-report", row_path, "local-machine-evidence"),
        (
            "row_1_repeat_report",
            "row-experiment-report",
            row_repeat_path,
            "local-machine-evidence",
        ),
        (
            "review_dataset",
            "blinded-review-dataset",
            review_package / package_manifest.dataset_path,
            "reviewer-visible",
        ),
        (
            "review_sampling_ledger",
            "sampling-ledger",
            review_package / package_manifest.sampling_ledger_path,
            "restricted-hash-only",
        ),
        (
            "review_evaluation_key",
            "evaluation-key",
            review_package / package_manifest.evaluation_key_path,
            "restricted-hash-only",
        ),
        (
            "review_package_manifest",
            "review-package-manifest",
            review_package / "held_out_review.package-manifest.v2-r2.json",
            "local-machine-evidence",
        ),
        (
            "review_repeat_manifest",
            "review-package-repeat-manifest",
            review_repeat / "held_out_review.package-manifest.v2-r2.json",
            "local-machine-evidence",
        ),
    )
    artifacts = []
    for logical_name, role, path, visibility in specifications:
        resolved = path.resolve(strict=True)
        relative = _relative_path(root, resolved)
        artifacts.append(
            Stage1EvidenceArtifact(
                logical_name=logical_name,
                role=role,
                path=relative,
                sha256=file_sha256(resolved),
                visibility=visibility,
                tracked_in_git=relative in tracked_paths,
            )
        )
    return tuple(sorted(artifacts, key=lambda artifact: artifact.path))


def _build_dependencies(paths: dict[str, str]) -> tuple[Stage1EvidenceDependency, ...]:
    edges = (
        ("source_snapshot_protocol", "effective_corpus", "defines-source-closure"),
        ("replacement_ledger", "effective_corpus", "selects-effective-corpus"),
        ("effective_corpus", "snapshot_report", "materializes"),
        ("snapshot_report", "snapshot_manifest", "is-closed-by"),
        ("toolchain_lock", "machine_run_identity", "locks-runtime"),
        ("effective_corpus", "machine_run_identity", "selects-corridors"),
        ("snapshot_report", "machine_report", "supplies-source-evidence"),
        ("machine_run_identity", "machine_report", "binds-producer-and-toolchain"),
        ("machine_report", "machine_manifest", "is-closed-by"),
        ("machine_manifest", "execution_parent", "is-frozen-by"),
        ("base_review_policy", "execution_parent", "defines-trial-policy"),
        ("study_sampling_policy", "trial_instance", "defines-sampling"),
        ("execution_parent", "trial_instance", "defines-machine-parent"),
        ("trial_instance", "review_dataset", "blinds"),
        ("machine_manifest", "review_sampling_ledger", "supplies-machine-population"),
        ("trial_instance", "review_sampling_ledger", "commits-seed-hash"),
        ("review_dataset", "review_package_manifest", "is-closed-by"),
        ("review_sampling_ledger", "review_package_manifest", "is-closed-by"),
        ("review_evaluation_key", "review_package_manifest", "is-closed-by-hash"),
        ("trial_instance", "review_repeat_manifest", "replays"),
        ("prior_retained_provenance", "machine_report", "defines-retained-baseline"),
    )
    return tuple(
        Stage1EvidenceDependency(
            parent_path=paths[parent],
            child_path=paths[child],
            relation=relation,
        )
        for parent, child, relation in edges
    )


def _build_gates(paths: dict[str, str]) -> tuple[Stage1GateEvidence, ...]:
    return (
        Stage1GateEvidence(
            gate_id="contract-conformance",
            evidence_paths=(paths["contract_workflow"], paths["provenance_schema"]),
            conclusion="The frozen contract workflow regenerates schemas and executes the full contract suite.",
        ),
        Stage1GateEvidence(
            gate_id="provenance-identity-closure",
            evidence_paths=(
                paths["toolchain_lock"],
                paths["machine_run_identity"],
                paths["machine_manifest"],
                paths["review_package_manifest"],
            ),
            conclusion="Clean producers, toolchain identity, and machine/review artifact closure are bound.",
        ),
        Stage1GateEvidence(
            gate_id="thirty-blinded-review-cases",
            evidence_paths=(paths["effective_corpus"], paths["review_dataset"], paths["review_package_manifest"]),
            conclusion="Thirty complete blinded corridor packages are frozen; replay-invalid cases remain separate.",
        ),
        Stage1GateEvidence(
            gate_id="stable-coverage-gap-entities",
            evidence_paths=(paths["paris_coverage_evidence"], paths["london_coverage_evidence"]),
            conclusion="All effective-corpus pedestrian coverage gaps have stable IDs, reasons, and review positions.",
        ),
        Stage1GateEvidence(
            gate_id="controlled-binding-census",
            evidence_paths=(paths["machine_manifest"], paths["review_sampling_ledger"]),
            conclusion="PCB-453 is retained and all 459 effective-corpus assessments have one technical class.",
        ),
        Stage1GateEvidence(
            gate_id="lossless-conflict-ledger",
            evidence_paths=(paths["machine_manifest"], paths["review_sampling_ledger"]),
            conclusion="Every atomic witness is retained with zero loss, duplication, or hard-key mixing.",
        ),
        Stage1GateEvidence(
            gate_id="sampling-and-hidden-member-protocol",
            evidence_paths=(
                paths["witness_sampling_policy"],
                paths["study_sampling_policy"],
                paths["review_sampling_ledger"],
                paths["review_repeat_manifest"],
            ),
            conclusion="Bounded stratified sampling, probabilities, hidden members, and deterministic replay are frozen.",
        ),
        Stage1GateEvidence(
            gate_id="trial-and-statistics-frozen",
            evidence_paths=(
                paths["base_review_policy"],
                paths["execution_parent"],
                paths["trial_instance"],
                paths["blind_review_protocol"],
            ),
            conclusion="The v2-R2 attention and prospective safe-pass design is frozen before human decisions.",
        ),
        Stage1GateEvidence(
            gate_id="automatic-promotion-blocked",
            evidence_paths=(
                paths["machine_report"],
                paths["trial_instance"],
                paths["review_package_manifest"],
                paths["row_1_report"],
            ),
            conclusion="Every machine, experiment, trial, and review-package promotion gate remains blocked.",
        ),
        Stage1GateEvidence(
            gate_id="review-ready-stage-semantics-explicit",
            evidence_paths=(paths["review_ready_plan"], paths["implementation_status"]),
            conclusion="Project status explicitly states that REVIEW_READY is not Stage 1 exit or certification.",
        ),
    )


def _verify_absolute_manifest(items: Iterable[dict[str, Any]]) -> tuple[int, int]:
    missing = 0
    hash_failures = 0
    for item in items:
        path = Path(str(item["path"]))
        if not path.is_file():
            missing += 1
        elif file_sha256(path) != str(item["sha256"]):
            hash_failures += 1
    return missing, hash_failures


def _verify_relative_manifest(root: Path, items: Iterable[Any]) -> tuple[int, int]:
    missing = 0
    hash_failures = 0
    for item in items:
        path = root / item.path
        if not path.is_file():
            missing += 1
        elif file_sha256(path) != item.sha256:
            hash_failures += 1
    return missing, hash_failures


def _relative_file_hashes(root: Path) -> dict[str, str]:
    return {
        path.relative_to(root).as_posix(): file_sha256(path)
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _tracked_paths(root: Path) -> set[str]:
    completed = subprocess.run(
        ["git", "-C", str(root), "ls-files", "-z"],
        capture_output=True,
        check=False,
        timeout=30,
    )
    if completed.returncode:
        raise ValueError("Unable to enumerate tracked Stage 1-M inputs.")
    return {
        raw.decode("utf-8").replace("\\", "/")
        for raw in completed.stdout.split(b"\0")
        if raw
    }


def _relative_path(root: Path, path: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError as exc:
        raise ValueError(f"Stage 1-M artifact is outside the repository: {path}.") from exc


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _load_model(path: Path, model: Any) -> Any:
    return model.model_validate_json(path.read_text(encoding="utf-8"))
