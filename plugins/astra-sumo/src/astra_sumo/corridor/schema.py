from __future__ import annotations

from .composite_benchmark_contracts import (
    CompositeFaultBenchmarkReport,
    CompositeFaultBenchmarkSpec,
)
from .applicability import (
    CertificationApplicabilityReport,
    CertificationEnvelope,
)
from .held_out_review_contracts import (
    HeldOutReviewContractBundle,
    HeldOutReviewPolicy,
    HeldOutReviewReport,
)
from .held_out_review_v2_contracts import (
    AttentionEvaluationKeyV2R2,
    BlindedAttentionDatasetV2R2,
    HeldOutReplacementAttemptLedgerV2,
    HeldOutReplacementPlanV2,
    HeldOutReplacementPolicyV2,
    HeldOutReserveCorpusV2,
    HeldOutReviewParentV2,
    HeldOutReviewExecutionParentV2R2,
    HeldOutReviewPackageManifestV2R2,
    HeldOutReviewPolicyV2,
    HeldOutReviewV2ContractBundle,
    HeldOutReviewV2Report,
    HeldOutReviewTrialInstanceV2R2,
    HeldOutSourceSnapshotProtocolV2,
    ReviewSamplingLedgerV2R2,
    ReviewUnitAdjudicationV2R2,
    ReviewUnitDecisionV2R2,
    ReviewWitnessSamplingPolicyV2,
    ReviewStudySamplingPolicyV2R2,
)
from .held_out_corpus_contracts import (
    HeldOutCorpusMachineManifest,
    HeldOutCorpusMachineReport,
    HeldOutCorpusSnapshotReport,
    HeldOutCorpusSpec,
)
from .manifest import CorridorResearchBundle
from .net_replay import NetReplayReport
from .plainxml_normalization import PlainXmlNormalizationReport
from .pedestrian_control_census import (
    ControlledPedestrianBindingCensus,
    EffectiveTLSProgramInventory,
)
from .pedestrian_row_contracts import ROWExperimentReport
from .review_compression import (
    AtomicConflictLedger,
    LosslessReviewCompressionReport,
)
from .official_sumo_benchmark_contracts import (
    OfficialSumoBenchmarkReport,
    OfficialSumoBenchmarkSpec,
)
from .ood_benchmark_contracts import OODBenchmarkReport, OODBenchmarkSpec
from .synthetic_benchmark_contracts import (
    SyntheticFaultBenchmarkReport,
    SyntheticFaultBenchmarkSpec,
)
from .run_identity import HeldOutMachineRunIdentity
from .stage1_review_ready_contracts import Stage1MachineReviewReadyProvenance


def build_corridor_schema() -> dict[str, object]:
    schema = CorridorResearchBundle.model_json_schema(by_alias=True)
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["$id"] = "https://github.com/Tarard/Torii-SUMO/schemas/torii.corridor.research-bundle.v1.schema.json"
    schema["x-torii-status"] = "frozen-stage-0-contract"
    return schema


def build_synthetic_fault_benchmark_schema() -> dict[str, object]:
    return _artifact_schema(
        SyntheticFaultBenchmarkSpec,
        "torii.corridor.synthetic-fault-benchmark.v1.schema.json",
        status="stage-1-gold-contract",
    )


def build_synthetic_fault_benchmark_report_schema() -> dict[str, object]:
    return _artifact_schema(
        SyntheticFaultBenchmarkReport,
        "torii.corridor.synthetic-fault-benchmark-report.v1.schema.json",
        status="stage-1-evidence-contract",
    )


def build_composite_fault_benchmark_schema() -> dict[str, object]:
    return _artifact_schema(
        CompositeFaultBenchmarkSpec,
        "torii.corridor.composite-fault-benchmark.v1.schema.json",
        status="stage-1-compound-fault-contract",
    )


def build_composite_fault_benchmark_report_schema() -> dict[str, object]:
    return _artifact_schema(
        CompositeFaultBenchmarkReport,
        "torii.corridor.composite-fault-benchmark-report.v1.schema.json",
        status="stage-1-compound-fault-evidence-contract",
    )


def build_certification_envelope_schema() -> dict[str, object]:
    return _artifact_schema(
        CertificationEnvelope,
        "torii.corridor.certification-envelope.v1.schema.json",
        status="stage-1-selective-domain-contract",
    )


def build_certification_applicability_report_schema() -> dict[str, object]:
    return _artifact_schema(
        CertificationApplicabilityReport,
        "torii.corridor.certification-applicability-report.v1.schema.json",
        status="stage-1-selective-domain-evidence-contract",
    )


def build_ood_benchmark_schema() -> dict[str, object]:
    return _artifact_schema(
        OODBenchmarkSpec,
        "torii.corridor.ood-benchmark.v1.schema.json",
        status="stage-1-ood-contract",
    )


def build_ood_benchmark_report_schema() -> dict[str, object]:
    return _artifact_schema(
        OODBenchmarkReport,
        "torii.corridor.ood-benchmark-report.v1.schema.json",
        status="stage-1-ood-evidence-contract",
    )


def build_official_sumo_benchmark_schema() -> dict[str, object]:
    return _artifact_schema(
        OfficialSumoBenchmarkSpec,
        "torii.corridor.official-sumo-benchmark.v1.schema.json",
        status="stage-1-normative-contract",
    )


def build_official_sumo_benchmark_report_schema() -> dict[str, object]:
    return _artifact_schema(
        OfficialSumoBenchmarkReport,
        "torii.corridor.official-sumo-benchmark-report.v1.schema.json",
        status="stage-1-normative-evidence-contract",
    )


def build_held_out_review_policy_schema() -> dict[str, object]:
    return _artifact_schema(
        HeldOutReviewPolicy,
        "torii.corridor.held-out-review-policy.v1.schema.json",
        status="stage-1-preregistered-human-review-contract",
    )


def build_held_out_review_contract_bundle_schema() -> dict[str, object]:
    return _artifact_schema(
        HeldOutReviewContractBundle,
        "torii.corridor.held-out-review-contract-bundle.v1.schema.json",
        status="stage-1-blinded-human-review-contract",
    )


def build_held_out_review_report_schema() -> dict[str, object]:
    return _artifact_schema(
        HeldOutReviewReport,
        "torii.corridor.held-out-review-report.v1.schema.json",
        status="stage-1-held-out-evidence-contract",
    )


def build_held_out_reserve_corpus_v2_schema() -> dict[str, object]:
    return _artifact_schema(
        HeldOutReserveCorpusV2,
        "torii.corridor.held-out-reserve-corpus.v2.schema.json",
        status="stage-1m-frozen-reserve-corpus-contract",
    )


def build_held_out_replacement_policy_v2_schema() -> dict[str, object]:
    return _artifact_schema(
        HeldOutReplacementPolicyV2,
        "torii.corridor.held-out-replacement-policy.v2.schema.json",
        status="stage-1m-deterministic-replacement-contract",
    )


def build_held_out_replacement_plan_v2_schema() -> dict[str, object]:
    return _artifact_schema(
        HeldOutReplacementPlanV2,
        "torii.corridor.held-out-replacement-plan.v2.schema.json",
        status="stage-1m-deterministic-replacement-plan",
    )


def build_held_out_source_snapshot_protocol_v2_schema() -> dict[str, object]:
    return _artifact_schema(
        HeldOutSourceSnapshotProtocolV2,
        "torii.corridor.held-out-source-snapshot-protocol.v2.schema.json",
        status="stage-1m-source-semantic-closure-contract",
    )


def build_held_out_replacement_attempt_ledger_v2_schema() -> dict[str, object]:
    return _artifact_schema(
        HeldOutReplacementAttemptLedgerV2,
        "torii.corridor.held-out-replacement-attempt-ledger.v2.schema.json",
        status="stage-1m-deterministic-replacement-evidence",
    )


def build_review_witness_sampling_policy_v2_schema() -> dict[str, object]:
    return _artifact_schema(
        ReviewWitnessSamplingPolicyV2,
        "torii.corridor.review-witness-sampling-policy.v2.schema.json",
        status="stage-1m-lossless-human-sampling-contract",
    )


def build_held_out_review_parent_v2_schema() -> dict[str, object]:
    return _artifact_schema(
        HeldOutReviewParentV2,
        "torii.corridor.held-out-review-parent.v2.schema.json",
        status="stage-1m-frozen-parent-benchmark-contract",
    )


def build_held_out_review_policy_v2_schema() -> dict[str, object]:
    return _artifact_schema(
        HeldOutReviewPolicyV2,
        "torii.corridor.held-out-review-policy.v2.schema.json",
        status="stage-1m-preregistered-attention-and-safe-pass-contract",
    )


def build_held_out_review_v2_contract_bundle_schema() -> dict[str, object]:
    return _artifact_schema(
        HeldOutReviewV2ContractBundle,
        "torii.corridor.held-out-review-contract-bundle.v2.schema.json",
        status="stage-1m-finding-cluster-human-review-contract",
    )


def build_held_out_review_v2_report_schema() -> dict[str, object]:
    return _artifact_schema(
        HeldOutReviewV2Report,
        "torii.corridor.held-out-review-report.v2.schema.json",
        status="stage-1h-human-validation-evidence-contract",
    )


def build_review_study_sampling_policy_v2_r2_schema() -> dict[str, object]:
    return _artifact_schema(
        ReviewStudySamplingPolicyV2R2,
        "torii.corridor.review-study-sampling-policy.v2-r2.schema.json",
        status="stage-1m-reviewable-validation-sampling-contract",
    )


def build_held_out_review_execution_parent_v2_r2_schema() -> dict[str, object]:
    return _artifact_schema(
        HeldOutReviewExecutionParentV2R2,
        "torii.corridor.held-out-review-execution-parent.v2-r2.schema.json",
        status="stage-1m-frozen-machine-evidence-parent",
    )


def build_held_out_review_trial_instance_v2_r2_schema() -> dict[str, object]:
    return _artifact_schema(
        HeldOutReviewTrialInstanceV2R2,
        "torii.corridor.held-out-review-trial-instance.v2-r2.schema.json",
        status="stage-1m-pre-sampling-trial-commitment",
    )


def build_blinded_attention_dataset_v2_r2_schema() -> dict[str, object]:
    return _artifact_schema(
        BlindedAttentionDatasetV2R2,
        "torii.corridor.blinded-attention-dataset.v2-r2.schema.json",
        status="stage-1m-reviewer-visible-blinded-dataset",
    )


def build_attention_evaluation_key_v2_r2_schema() -> dict[str, object]:
    return _artifact_schema(
        AttentionEvaluationKeyV2R2,
        "torii.corridor.attention-evaluation-key.v2-r2.schema.json",
        status="stage-1m-restricted-unblinding-key",
    )


def build_review_sampling_ledger_v2_r2_schema() -> dict[str, object]:
    return _artifact_schema(
        ReviewSamplingLedgerV2R2,
        "torii.corridor.review-sampling-ledger.v2-r2.schema.json",
        status="stage-1m-restricted-sampling-evidence",
    )


def build_review_unit_decision_v2_r2_schema() -> dict[str, object]:
    return _artifact_schema(
        ReviewUnitDecisionV2R2,
        "torii.corridor.review-unit-decision.v2-r2.schema.json",
        status="stage-1h-independent-review-decision-contract",
    )


def build_review_unit_adjudication_v2_r2_schema() -> dict[str, object]:
    return _artifact_schema(
        ReviewUnitAdjudicationV2R2,
        "torii.corridor.review-unit-adjudication.v2-r2.schema.json",
        status="stage-1h-third-reviewer-adjudication-contract",
    )


def build_held_out_review_package_manifest_v2_r2_schema() -> dict[str, object]:
    return _artifact_schema(
        HeldOutReviewPackageManifestV2R2,
        "torii.corridor.held-out-review-package-manifest.v2-r2.schema.json",
        status="stage-1m-review-package-provenance",
    )


def build_held_out_corpus_schema() -> dict[str, object]:
    return _artifact_schema(
        HeldOutCorpusSpec,
        "torii.corridor.held-out-corpus.v1.schema.json",
        status="stage-1-held-out-corpus-contract",
    )


def build_held_out_corpus_snapshot_report_schema() -> dict[str, object]:
    return _artifact_schema(
        HeldOutCorpusSnapshotReport,
        "torii.corridor.held-out-corpus-snapshot-report.v1.schema.json",
        status="stage-1-held-out-corpus-evidence-contract",
    )


def build_held_out_corpus_machine_report_schema() -> dict[str, object]:
    return _artifact_schema(
        HeldOutCorpusMachineReport,
        "torii.corridor.held-out-corpus-machine-report.v2.schema.json",
        status="stage-1-held-out-corpus-machine-evidence-contract",
    )


def build_held_out_machine_run_identity_schema() -> dict[str, object]:
    return _artifact_schema(
        HeldOutMachineRunIdentity,
        "torii.corridor.held-out-machine-run-identity.v1.schema.json",
        status="stage-1-held-out-producer-toolchain-identity-contract",
    )


def build_held_out_corpus_machine_manifest_schema() -> dict[str, object]:
    return _artifact_schema(
        HeldOutCorpusMachineManifest,
        "torii.corridor.held-out-corpus-machine-manifest.v2.schema.json",
        status="stage-1-held-out-artifact-closure-contract",
    )


def build_net_replay_report_schema() -> dict[str, object]:
    return _artifact_schema(
        NetReplayReport,
        "torii.corridor.net-replay-report.v1.schema.json",
        status="stage-1-reproducibility-evidence-contract",
    )


def build_plainxml_normalization_report_schema() -> dict[str, object]:
    return _artifact_schema(
        PlainXmlNormalizationReport,
        "torii.corridor.plainxml-normalization-report.v1.schema.json",
        status="stage-1-experimental-deterministic-ingest-contract",
    )


def build_effective_tls_program_inventory_schema() -> dict[str, object]:
    return _artifact_schema(
        EffectiveTLSProgramInventory,
        "torii.corridor.effective-tls-program-inventory.v1.schema.json",
        status="stage-1m-pcb-effective-program-evidence-contract",
    )


def build_controlled_pedestrian_binding_census_schema() -> dict[str, object]:
    return _artifact_schema(
        ControlledPedestrianBindingCensus,
        "torii.corridor.controlled-pedestrian-binding-census.v1.schema.json",
        status="stage-1m-pcb-453-census-contract",
    )


def build_atomic_conflict_ledger_schema() -> dict[str, object]:
    return _artifact_schema(
        AtomicConflictLedger,
        "torii.corridor.atomic-conflict-ledger.v1.schema.json",
        status="stage-1m-rwc-1-lossless-atomic-witness-contract",
    )


def build_lossless_review_compression_schema() -> dict[str, object]:
    return _artifact_schema(
        LosslessReviewCompressionReport,
        "torii.corridor.lossless-review-compression.v1.schema.json",
        status="stage-1m-rwc-1-review-compression-contract",
    )


def build_row1_experiment_report_schema() -> dict[str, object]:
    return _artifact_schema(
        ROWExperimentReport,
        "torii.corridor.row-1-experiment-report.v1.schema.json",
        status="stage-1m-row-1-independent-right-of-way-contract",
    )


def build_stage1_machine_review_ready_provenance_schema() -> dict[str, object]:
    return _artifact_schema(
        Stage1MachineReviewReadyProvenance,
        "torii.corridor.stage1m-machine-review-ready-provenance.v3.schema.json",
        status="stage-1m-machine-review-ready-provenance-contract",
    )


def _artifact_schema(model: type, filename: str, *, status: str) -> dict[str, object]:
    schema = model.model_json_schema(by_alias=True)
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["$id"] = f"https://github.com/Tarard/Torii-SUMO/schemas/{filename}"
    schema["x-torii-status"] = status
    return schema
