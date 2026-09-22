"""Typed contracts for the Torii corridor human-modeling research pipeline."""

from .benchmark import BenchmarkLock, BenchmarkSpecV1
from .applicability import (
    CertificationApplicabilityReport,
    CertificationEnvelope,
    evaluate_certification_applicability,
    extract_network_applicability_features,
)
from .audit_pipeline import build_exact_semantic_regression_artifacts
from .calibration import (
    ConnectionAuditCalibration,
    ConnectionAuditCalibrationPolicy,
    build_connection_mode_calibration_artifact,
    calibrate_connection_mode_audit,
)
from .composite_benchmark_contracts import (
    CompositeFaultBenchmarkReport,
    CompositeFaultBenchmarkSpec,
)
from .composite_benchmark_runner import run_composite_fault_benchmark
from .canonicalizer import CanonicalEntity, CanonicalNetworkSnapshot, canonicalize_net_xml_file
from .conflict_graph import (
    IndependentSafetyReport,
    MovementConflictGraph,
    audit_independent_movement_safety,
    build_movement_conflict_graph,
)
from .candidates import CandidateGraph, CandidateVariant, Hypothesis, PatchOperation, SemanticDelta
from .enums import AutomationAction, GateStatus, TrafficSide, WorkflowStage
from .exact_diff import ExactSemanticDiffReport, build_finding, compare_canonical_snapshots
from .held_out_review_contracts import (
    BlindReviewDecision,
    BlindedReviewDataset,
    HeldOutAdjudication,
    HeldOutEvaluationKey,
    HeldOutReviewPolicy,
    HeldOutReviewReport,
)
from .held_out_review_runner import (
    build_blinded_review_artifacts,
    evaluate_held_out_review_trial,
)
from .held_out_review_v2 import (
    build_deterministic_replacement_plan_v2,
    freeze_replacement_execution_v2,
)
from .held_out_review_v2_contracts import (
    AttentionEvaluationKeyV2R2,
    AttentionEvaluationKeyV2,
    BlindedAttentionDatasetV2,
    BlindedAttentionDatasetV2R2,
    ClusterMachineAssessmentV2,
    ClusterReviewAdjudicationV2,
    ClusterReviewDecisionV2,
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
    ReviewSamplingStratumV2R2,
    ReviewWitnessSamplingPolicyV2,
    ReviewStudySamplingPolicyV2R2,
)
from .held_out_review_v2_r2 import freeze_review_trial_v2_r2
from .held_out_review_v2_r2_package import build_held_out_review_package_v2_r2
from .held_out_review_v2_r2_sampling import (
    allocate_stratified_sample_sizes,
    deterministic_sample,
    select_conflict_sites,
    select_negative_pairs,
    select_presented_site_witnesses,
)
from .held_out_review_v2_preregistration import (
    build_held_out_replacement_policy_v2,
    build_held_out_reserve_corpus_v2,
    build_held_out_review_parent_v2,
    build_held_out_review_policy_v2,
    build_held_out_source_snapshot_protocol_v2,
    build_review_witness_sampling_policy_v2,
)
from .ids import (
    canonical_json_bytes,
    make_approach_id,
    make_boundary_port_id,
    make_controller_program_signature,
    make_internal_path_signature,
    make_lane_role_id,
    make_movement_id,
    make_physical_cell_id,
    make_signal_group_id,
    stable_id,
)
from .manifest import ArtifactManifestV1, CorridorResearchBundle
from .official_sumo_benchmark_contracts import (
    OfficialSumoBenchmarkReport,
    OfficialSumoBenchmarkSpec,
)
from .official_sumo_benchmark_runner import run_official_sumo_benchmark
from .plainxml_normalization import (
    PlainXmlNormalizationReport,
    normalize_osm_plainxml_bundle,
)
from .pedestrian_control_census import (
    ControlledPedestrianBindingAssessment,
    ControlledPedestrianBindingCensus,
    EffectiveTLSProgramEvidence,
    EffectiveTLSProgramInventory,
    build_effective_tls_program_inventory,
    classify_controlled_pedestrian_bindings,
)
from .pedestrian_row_contracts import (
    ROWExperimentCaseResult,
    ROWExperimentReport,
    ROWGeometryEvidence,
    ROWModelClaimEvidence,
    ROWRuntimeProbe,
    ROWStaticAssessment,
    SourceROWBundle,
    SourceROWOracleDecision,
)
from .pedestrian_row_experiment import run_row1_experiment
from .pedestrian_row_oracle import (
    assess_row_static_consistency,
    build_row_geometry_evidence,
    build_row_model_claim_evidence,
    infer_source_row_class,
    parse_plain_source_row_bundle,
)
from .review import (
    PedestrianControlKind,
    PedestrianCoverageGap,
    build_pedestrian_coverage_gap,
)
from .review_compression import (
    AtomicConflictLedger,
    AtomicConflictWitness,
    ConflictPopulationStratum,
    ConflictReviewCluster,
    ConflictSiteReviewCase,
    LosslessReviewCompressionReport,
    ReviewCompressionPolicy,
    build_lossless_review_compression,
)
from .ood_benchmark_contracts import OODBenchmarkReport, OODBenchmarkSpec
from .ood_benchmark_runner import run_ood_benchmark
from .scope import BoundaryPort, ScopeSpec
from .stage1_review_ready import freeze_stage1_machine_review_ready_provenance
from .stage1_review_ready_contracts import Stage1MachineReviewReadyProvenance
from .synthetic_benchmark_contracts import (
    SyntheticFaultBenchmarkReport,
    SyntheticFaultBenchmarkSpec,
)
from .synthetic_benchmark_runner import run_synthetic_fault_benchmark
from .toolchain import ToolIdentity, ToolchainLock
from .workflow import NetworkQualityVectorV1, StageOutcome, WorkflowExecution

__all__ = [
    "ArtifactManifestV1",
    "AtomicConflictLedger",
    "AtomicConflictWitness",
    "AutomationAction",
    "BenchmarkLock",
    "BenchmarkSpecV1",
    "BoundaryPort",
    "CandidateGraph",
    "CandidateVariant",
    "CertificationApplicabilityReport",
    "CertificationEnvelope",
    "CanonicalEntity",
    "CanonicalNetworkSnapshot",
    "ConnectionAuditCalibration",
    "ConnectionAuditCalibrationPolicy",
    "CompositeFaultBenchmarkReport",
    "CompositeFaultBenchmarkSpec",
    "ControlledPedestrianBindingAssessment",
    "ControlledPedestrianBindingCensus",
    "ConflictPopulationStratum",
    "ConflictReviewCluster",
    "ConflictSiteReviewCase",
    "CorridorResearchBundle",
    "GateStatus",
    "BlindReviewDecision",
    "BlindedReviewDataset",
    "HeldOutAdjudication",
    "HeldOutEvaluationKey",
    "HeldOutReviewPolicy",
    "HeldOutReviewReport",
    "AttentionEvaluationKeyV2",
    "AttentionEvaluationKeyV2R2",
    "BlindedAttentionDatasetV2",
    "BlindedAttentionDatasetV2R2",
    "ClusterMachineAssessmentV2",
    "ClusterReviewAdjudicationV2",
    "ClusterReviewDecisionV2",
    "HeldOutReplacementPlanV2",
    "HeldOutReplacementAttemptLedgerV2",
    "HeldOutReplacementPolicyV2",
    "HeldOutReserveCorpusV2",
    "HeldOutReviewParentV2",
    "HeldOutReviewExecutionParentV2R2",
    "HeldOutReviewPackageManifestV2R2",
    "HeldOutReviewPolicyV2",
    "HeldOutReviewV2ContractBundle",
    "HeldOutReviewV2Report",
    "HeldOutReviewTrialInstanceV2R2",
    "HeldOutSourceSnapshotProtocolV2",
    "Hypothesis",
    "IndependentSafetyReport",
    "NetworkQualityVectorV1",
    "MovementConflictGraph",
    "LosslessReviewCompressionReport",
    "OfficialSumoBenchmarkReport",
    "OfficialSumoBenchmarkSpec",
    "PlainXmlNormalizationReport",
    "OODBenchmarkReport",
    "OODBenchmarkSpec",
    "PatchOperation",
    "PedestrianControlKind",
    "PedestrianCoverageGap",
    "ReviewCompressionPolicy",
    "ReviewSamplingLedgerV2R2",
    "ReviewSamplingStratumV2R2",
    "ReviewWitnessSamplingPolicyV2",
    "ReviewStudySamplingPolicyV2R2",
    "ROWExperimentCaseResult",
    "ROWExperimentReport",
    "ROWGeometryEvidence",
    "ROWModelClaimEvidence",
    "ROWRuntimeProbe",
    "ROWStaticAssessment",
    "ScopeSpec",
    "SemanticDelta",
    "StageOutcome",
    "Stage1MachineReviewReadyProvenance",
    "SyntheticFaultBenchmarkReport",
    "SyntheticFaultBenchmarkSpec",
    "SourceROWBundle",
    "SourceROWOracleDecision",
    "ToolIdentity",
    "ToolchainLock",
    "TrafficSide",
    "WorkflowExecution",
    "WorkflowStage",
    "ExactSemanticDiffReport",
    "EffectiveTLSProgramEvidence",
    "EffectiveTLSProgramInventory",
    "build_finding",
    "build_effective_tls_program_inventory",
    "build_pedestrian_coverage_gap",
    "build_blinded_review_artifacts",
    "build_deterministic_replacement_plan_v2",
    "build_held_out_replacement_policy_v2",
    "build_held_out_reserve_corpus_v2",
    "build_held_out_review_parent_v2",
    "build_held_out_review_policy_v2",
    "build_held_out_source_snapshot_protocol_v2",
    "build_review_witness_sampling_policy_v2",
    "build_held_out_review_package_v2_r2",
    "allocate_stratified_sample_sizes",
    "deterministic_sample",
    "freeze_replacement_execution_v2",
    "freeze_review_trial_v2_r2",
    "freeze_stage1_machine_review_ready_provenance",
    "select_conflict_sites",
    "select_negative_pairs",
    "select_presented_site_witnesses",
    "build_exact_semantic_regression_artifacts",
    "build_connection_mode_calibration_artifact",
    "calibrate_connection_mode_audit",
    "audit_independent_movement_safety",
    "build_movement_conflict_graph",
    "build_row_geometry_evidence",
    "build_row_model_claim_evidence",
    "build_lossless_review_compression",
    "canonicalize_net_xml_file",
    "canonical_json_bytes",
    "make_approach_id",
    "make_boundary_port_id",
    "make_controller_program_signature",
    "make_internal_path_signature",
    "make_lane_role_id",
    "make_movement_id",
    "make_physical_cell_id",
    "make_signal_group_id",
    "compare_canonical_snapshots",
    "classify_controlled_pedestrian_bindings",
    "evaluate_held_out_review_trial",
    "evaluate_certification_applicability",
    "extract_network_applicability_features",
    "infer_source_row_class",
    "run_synthetic_fault_benchmark",
    "run_composite_fault_benchmark",
    "run_official_sumo_benchmark",
    "normalize_osm_plainxml_bundle",
    "parse_plain_source_row_bundle",
    "run_ood_benchmark",
    "run_row1_experiment",
    "assess_row_static_consistency",
    "stable_id",
]
