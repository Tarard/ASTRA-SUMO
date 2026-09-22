"""Write rebuild reports, queue files and staged artifact paths."""

from __future__ import annotations

import json
from typing import Any
from pathlib import Path
import csv
from .network import _stable_digest


def _command_report(result: Any) -> dict[str, object]:
    if hasattr(result, "to_dict"):
        payload = result.to_dict()
    elif isinstance(result, dict):
        payload = dict(result)
    else:
        payload = {
            "status": getattr(result, "status", "fail"),
            "returncode": getattr(result, "returncode", None),
        }
    if "status" not in payload:
        payload["status"] = "pass" if payload.get("returncode") == 0 else "fail"
    return payload


def _write_teacher_guided_report(path: Path, report: dict[str, object]) -> dict[str, object]:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    report["report_file"] = str(path)
    return report


def _failure(error: str) -> dict[str, object]:
    return {
        "status": "fail",
        "claim_status": "construction-invalid",
        "error": error,
    }


def _variant_exception_report(exc: Exception, junction_id: str) -> dict[str, object]:
    return {
        "status": "fail",
        "claim_status": "construction-invalid",
        "junction_id": junction_id,
        "reason": str(exc),
        "error": str(exc),
        "exception_type": type(exc).__name__,
    }


def _queue_candidate_dir(index: int, junction_id: str) -> str:
    return f"candidate_{index + 1:03d}_{_stable_digest(junction_id)}"


def _safe_stage_name(value: str, max_len: int = 64) -> str:
    safe = "".join(char if char.isascii() and (char.isalnum() or char in "._-") else "_" for char in value.strip())
    safe = safe.strip("._-")
    safe = safe or "candidate"
    if len(safe) <= max_len:
        return safe
    head_len = max(1, max_len - 9)
    return f"{safe[:head_len]}_{_stable_digest(safe)}"


def _write_teacher_guided_promotion_gate(
    *,
    output_file: Path,
    status: str,
    claim_status: str,
    parity_gate_status: str,
    approach_integrity_status: str,
    variant_reports: list[dict[str, object]],
    context_gate_status: str = "skipped",
    connection_mode_regression_status: str = "skipped",
) -> dict[str, object]:
    applied_reports = [report for report in variant_reports if report.get("composite_applied")]
    global_candidate_reports = [
        report for report in variant_reports if bool(report.get("global_candidate_eligible", True))
    ]
    gate_reports = applied_reports or [
        report for report in global_candidate_reports if not report.get("expanded_scope_followup_emitted")
    ] or global_candidate_reports or [
        report for report in variant_reports if not report.get("expanded_scope_followup_emitted")
    ]
    items = [
        {
            "junction_id": str(report.get("junction_id", "")),
            "teacher_junction_id": str(report.get("teacher_junction_id", "")),
            "status": str(report.get("status", "")),
            "parity_gate_status": str(report.get("parity_gate_status", "")),
            "connection_mode_regression_status": str(
                report.get("connection_mode_regression", {}).get("status", "not_run")
            )
            if isinstance(report.get("connection_mode_regression"), dict)
            else "not_run",
            "final_net_file": str(report.get("final_net_file", "")),
            "candidate_scope_status": str(report.get("candidate_scope_status") or "full_network"),
            "global_candidate_eligible": bool(report.get("global_candidate_eligible", True)),
            "semantic_layer_gates": report.get("semantic_layer_gates", {})
            if isinstance(report.get("semantic_layer_gates"), dict)
            else {},
        }
        for report in gate_reports
    ]
    gate_status = (
        "pass"
        if status == "pass"
        and parity_gate_status == "pass"
        and context_gate_status != "fail"
        and connection_mode_regression_status != "fail"
        and approach_integrity_status == "pass"
        and items
        and all(item["status"] == "pass" and item["parity_gate_status"] == "pass" for item in items)
        else ("blocked" if not items else "fail")
    )
    report = {
        "status": gate_status,
        "claim_status": claim_status,
        "parity_gate_status": parity_gate_status,
        "context_gate_status": context_gate_status,
        "connection_mode_regression_status": connection_mode_regression_status,
        "approach_integrity_status": approach_integrity_status,
        "candidate_count": len(items),
        "pass_candidate_count": sum(1 for item in items if item["status"] == "pass"),
        "items": items,
    }
    output_file.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    return report


def _queue_path(value: object, base_dir: Path | None) -> Path:
    path = Path(str(value))
    if path.is_absolute() or base_dir is None:
        return path
    return base_dir / path


def _write_teacher_guided_queue_csv(path: Path, rows: list[dict[str, object]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "reference_id",
                "junction_id",
                "candidate_status",
                "junction_pattern_delta_count",
                "junction_pattern_mismatch_fields",
                "netedit_review_actions",
                "review_priority",
                "teacher_pattern_key",
                "teacher_pattern_template_count",
                "vehicle_movement_matrix_missing_count",
                "missing_teacher_movement_plan_count",
                "turnaround_only_lane_gap_count",
                "edge_map_size",
                "missing_teacher_edge_ids",
                "copyable_missing_teacher_edge_ids",
                "uncopyable_missing_teacher_edge_ids",
                "matched_candidate_node_ids",
                "learned_rule",
                "error",
            ],
        )
        writer.writeheader()
        for row in rows:
            edge_map = row.get("edge_map", {})
            writer.writerow(
                {
                    "reference_id": row.get("reference_id", ""),
                    "junction_id": row.get("junction_id", ""),
                    "candidate_status": row.get("candidate_status", ""),
                    "junction_pattern_delta_count": row.get("junction_pattern_delta_count", 0),
                    "junction_pattern_mismatch_fields": ";".join(
                        str(item) for item in row.get("junction_pattern_mismatch_fields", []) or []
                    ),
                    "netedit_review_actions": ";".join(
                        str(item) for item in row.get("netedit_review_actions", []) or []
                    ),
                    "review_priority": row.get("review_priority", ""),
                    "teacher_pattern_key": row.get("teacher_pattern_key", ""),
                    "teacher_pattern_template_count": row.get("teacher_pattern_template_count", 0),
                    "vehicle_movement_matrix_missing_count": row.get("vehicle_movement_matrix_missing_count", 0),
                    "missing_teacher_movement_plan_count": row.get("missing_teacher_movement_plan_count", 0),
                    "turnaround_only_lane_gap_count": row.get("turnaround_only_lane_gap_count", 0),
                    "edge_map_size": len(edge_map) if isinstance(edge_map, dict) else 0,
                    "missing_teacher_edge_ids": ";".join(str(item) for item in row.get("missing_teacher_edge_ids", []) or []),
                    "copyable_missing_teacher_edge_ids": ";".join(
                        str(item) for item in row.get("copyable_missing_teacher_edge_ids", []) or []
                    ),
                    "uncopyable_missing_teacher_edge_ids": ";".join(
                        str(item) for item in row.get("uncopyable_missing_teacher_edge_ids", []) or []
                    ),
                    "matched_candidate_node_ids": ";".join(str(item) for item in row.get("matched_candidate_node_ids", []) or []),
                    "learned_rule": row.get("learned_rule", ""),
                    "error": row.get("error", ""),
                }
            )


def _command_path(path: Path, cwd: Path) -> str:
    try:
        return str(path.resolve().relative_to(cwd.resolve()))
    except ValueError:
        return str(path)


def _stage_file(output_dir: Path, prefix: str, suffix: str) -> Path:
    def candidate(name: str) -> Path | None:
        path = output_dir / name
        return path if len(str(path.resolve())) < 260 else None

    if path := candidate(f"{prefix}_{suffix}"):
        return path
    short_prefix = (prefix[:16].strip("_") or "tg")
    if path := candidate(f"{short_prefix}_{suffix}"):
        return path
    if path := candidate(suffix):
        return path
    suffix_aliases = {
        "nodes.nod.xml": "n.nod.xml",
        "connections.con.xml": "c.con.xml",
        "lanes.edg.xml": "e.edg.xml",
        "sidewalks.net.xml": "sw.net.xml",
        "pedring.net.xml": "pr.net.xml",
        "vehicle_attrs.net.xml": "va.net.xml",
        "target_internal_replay.net.xml": "tir.net.xml",
        "target_internal_normalized.net.xml": "tin.net.xml",
        "target_internal_normalized_unrestored.net.xml": "tin_raw.net.xml",
        "target_internal_pedring.net.xml": "tip.net.xml",
        "target_internal_vehicle_attrs.net.xml": "tva.net.xml",
        "teacher_guided.net.xml": "tg.net.xml",
        "teacher_guided_fallback.net.xml": "tgfb.net.xml",
        "teacher_guided_report.json": "tgr.json",
    }
    return output_dir / suffix_aliases.get(suffix, suffix)
