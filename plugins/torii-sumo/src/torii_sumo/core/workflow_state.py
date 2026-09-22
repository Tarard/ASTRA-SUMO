from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping


@dataclass(frozen=True)
class NetworkQualityVector:
    connectivity: Any = None
    routeability: Any = None
    topology_fragmentation: Any = None
    tls_semantic_delta: Any = None
    junction_pattern_delta: Any = None
    reference_scope_delta: Any = None
    manual_review_load: Any = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "connectivity": self.connectivity,
            "routeability": self.routeability,
            "topology_fragmentation": self.topology_fragmentation,
            "tls_semantic_delta": self.tls_semantic_delta,
            "junction_pattern_delta": self.junction_pattern_delta,
            "reference_scope_delta": self.reference_scope_delta,
            "manual_review_load": self.manual_review_load,
        }


@dataclass(frozen=True)
class StageResult:
    stage_name: str
    status: str
    input_artifacts: dict[str, str] = field(default_factory=dict)
    output_artifacts: dict[str, str] = field(default_factory=dict)
    before_quality: NetworkQualityVector = field(default_factory=NetworkQualityVector)
    after_quality: NetworkQualityVector = field(default_factory=NetworkQualityVector)
    delta_quality: dict[str, Any] = field(default_factory=dict)
    promotion_decision: str = ""
    claim_status: str = "diagnostic-demo"
    evidence_files: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "stage_name": self.stage_name,
            "status": self.status,
            "input_artifacts": dict(self.input_artifacts),
            "output_artifacts": dict(self.output_artifacts),
            "before_quality": self.before_quality.as_dict(),
            "after_quality": self.after_quality.as_dict(),
            "delta_quality": dict(self.delta_quality),
            "promotion_decision": self.promotion_decision,
            "claim_status": self.claim_status,
            "evidence_files": list(self.evidence_files),
            "warnings": list(self.warnings),
        }


@dataclass(frozen=True)
class WorkflowState:
    plan: dict[str, Any] = field(default_factory=dict)
    artifacts: dict[str, str] = field(default_factory=dict)
    quality: NetworkQualityVector = field(default_factory=NetworkQualityVector)
    review_items: list[dict[str, Any]] = field(default_factory=list)
    claim_status: str = "diagnostic-demo"
    warnings: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "plan": dict(self.plan),
            "artifacts": dict(self.artifacts),
            "quality": self.quality.as_dict(),
            "review_items": list(self.review_items),
            "claim_status": self.claim_status,
            "warnings": list(self.warnings),
        }


def build_promotion_trace(
    *,
    case_id: str,
    claim_status: str,
    stages: list[StageResult],
    source_artifact: str = "",
) -> dict[str, Any]:
    trace: dict[str, Any] = {
        "case_id": case_id,
        "claim_status": claim_status,
        "stages": [
            {
                "stage_id": stage.stage_name,
                "before_quality": stage.before_quality.as_dict(),
                "after_quality": stage.after_quality.as_dict(),
                "delta_quality": dict(stage.delta_quality),
                "promotion_decision": stage.promotion_decision or stage.status,
            }
            for stage in stages
        ],
    }
    if source_artifact:
        trace["source_artifact"] = source_artifact
    return trace


def summarize_workflow_stages(report: Mapping[str, Any]) -> list[StageResult]:
    claim_status = str(report.get("claim_status") or "diagnostic-demo")
    stages: list[StageResult] = []

    if _has_any(
        report,
        (
            "tls_aggregation_status",
            "reference_visual_detail_tls_aggregation_status",
        ),
    ):
        stages.append(
            StageResult(
                stage_name="tls_reality",
                status=_first_value(
                    report,
                    (
                        "reference_visual_detail_tls_aggregation_status",
                        "tls_aggregation_status",
                    ),
                    default="unknown",
                ),
                output_artifacts=_artifacts(
                    report,
                    {
                        "variant": "tls_aggregation_variant_file",
                        "plan": "tls_aggregation_plan_file",
                        "representatives": "tls_aggregation_representatives_file",
                    },
                ),
                after_quality=NetworkQualityVector(
                    topology_fragmentation={
                        "tls_aggregation_status": _first_value(
                            report,
                            (
                                "reference_visual_detail_tls_aggregation_status",
                                "tls_aggregation_status",
                            ),
                            default="unknown",
                        )
                    }
                ),
                claim_status=claim_status,
            )
        )

    if _has_any(
        report,
        (
            "reference_join_audit_status",
            "reference_hierarchy_audit_status",
            "reference_scope_audit_status",
            "reference_join_post_teacher_audit_status",
        ),
    ):
        stages.append(
            StageResult(
                stage_name="reference_comparison",
                status=_first_value(
                    report,
                    (
                        "reference_join_post_teacher_audit_status",
                        "reference_join_audit_status",
                        "reference_hierarchy_audit_status",
                        "reference_scope_audit_status",
                    ),
                    default="unknown",
                ),
                after_quality=NetworkQualityVector(
                    reference_scope_delta={
                        key: report[key]
                        for key in (
                            "reference_join_audit_status",
                            "reference_hierarchy_audit_status",
                            "reference_scope_audit_status",
                            "reference_join_post_teacher_audit_status",
                        )
                        if key in report
                    }
                ),
                claim_status=claim_status,
            )
        )

    if _has_any(
        report,
        (
            "teacher_guided_repair_run_status",
            "teacher_guided_repair_queue_status",
            "teacher_guided_repair_promotion_gate_status",
        ),
    ):
        stages.append(
            StageResult(
                stage_name="teacher_guided_repair",
                status=_first_value(
                    report,
                    ("teacher_guided_repair_run_status", "teacher_guided_repair_queue_status"),
                    default="unknown",
                ),
                output_artifacts=_artifacts(
                    report,
                    {
                        "best_variant": "teacher_guided_repair_best_variant_file",
                        "run_report": "teacher_guided_repair_run_report_file",
                        "promotion_gate": "teacher_guided_repair_promotion_gate_file",
                    },
                ),
                after_quality=NetworkQualityVector(
                    tls_semantic_delta=report.get("teacher_guided_repair_semantic_layer_gate_counts")
                ),
                promotion_decision=_first_value(
                    report,
                    (
                        "teacher_guided_repair_promotion_gate_status",
                        "teacher_guided_repair_parity_gate_status",
                    ),
                    default="",
                ),
                claim_status=claim_status,
            )
        )

    if _has_any(
        report,
        (
            "road_connectivity_replay_status",
            "road_connectivity_replay_gate_status",
            "road_connectivity_replay_best_variant_file",
        ),
    ):
        stages.append(
            StageResult(
                stage_name="road_connectivity",
                status=_first_value(report, ("road_connectivity_replay_status",), default="unknown"),
                output_artifacts=_artifacts(
                    report,
                    {
                        "best_variant": "road_connectivity_replay_best_variant_file",
                        "run_report": "road_connectivity_replay_run_report_file",
                    },
                ),
                after_quality=NetworkQualityVector(
                    connectivity={
                        key: report[key]
                        for key in (
                            "road_connectivity_replay_status",
                            "road_connectivity_replay_gate_status",
                            "road_connectivity_replay_sumo_load_status",
                        )
                        if key in report
                    }
                ),
                promotion_decision=str(report.get("road_connectivity_replay_gate_status") or ""),
                claim_status=claim_status,
            )
        )

    if _has_any(report, ("routeability_audit_status", "routeability_audit_report_file")):
        stages.append(
            StageResult(
                stage_name="routeability",
                status=_first_value(report, ("routeability_audit_status",), default="unknown"),
                output_artifacts=_artifacts(report, {"report": "routeability_audit_report_file"}),
                after_quality=NetworkQualityVector(
                    routeability={"status": _first_value(report, ("routeability_audit_status",), default="unknown")}
                ),
                claim_status=claim_status,
            )
        )

    if _has_any(
        report,
        (
            "workflow_review_html_status",
            "workflow_review_html_file",
            "review_manifest_file",
            "human_review_required_count",
        ),
    ):
        stages.append(
            StageResult(
                stage_name="review_html",
                status=_first_value(report, ("workflow_review_html_status",), default="unknown"),
                output_artifacts=_artifacts(
                    report,
                    {
                        "html": "workflow_review_html_file",
                        "manifest": "review_manifest_file",
                    },
                ),
                after_quality=NetworkQualityVector(
                    manual_review_load=report.get("human_review_required_count")
                ),
                claim_status=claim_status,
            )
        )

    return stages


def _has_any(report: Mapping[str, Any], keys: tuple[str, ...]) -> bool:
    return any(report.get(key) not in (None, "") for key in keys)


def _first_value(report: Mapping[str, Any], keys: tuple[str, ...], *, default: str) -> str:
    for key in keys:
        value = report.get(key)
        if value not in (None, ""):
            return str(value)
    return default


def _artifacts(report: Mapping[str, Any], mapping: Mapping[str, str]) -> dict[str, str]:
    artifacts: dict[str, str] = {}
    for artifact_name, report_key in mapping.items():
        value = report.get(report_key)
        if value not in (None, ""):
            artifacts[artifact_name] = str(value)
    return artifacts
