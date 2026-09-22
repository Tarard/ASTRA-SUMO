from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def _load_first(paths: list[Path]) -> dict[str, Any]:
    if not paths:
        return {}
    return json.loads(paths[0].read_text(encoding="utf-8"))


def _failure_count(layer_counts: dict[str, Any]) -> int:
    total = 0
    for value in layer_counts.values():
        if isinstance(value, dict):
            total += int(value.get("failure_count", 0) or 0)
            total += int(value.get("fail", 0) or 0)
    return total


def _gate(summary: dict[str, Any], run_report: dict[str, Any], connection_audit: dict[str, Any]) -> str:
    if summary.get("status") != "pass":
        return "fail"
    if summary.get("parity_gate_status") != "pass" or summary.get("promotion_gate_status") != "pass":
        return "fail"
    if run_report and (
        run_report.get("status") != "pass"
        or run_report.get("parity_gate_status") != "pass"
        or run_report.get("promotion_gate_status") != "pass"
        or _failure_count(run_report.get("semantic_layer_gate_counts", {}) or {}) > 0
    ):
        return "fail"
    if connection_audit and connection_audit.get("equal_signature") is False:
        return "fail"
    return "pass"


def summarize_probe_dir(probe_dir: Path) -> dict[str, Any]:
    probe_dir = probe_dir.resolve()
    summary = _load_first(sorted(probe_dir.glob("*summary.json")))
    run_report_paths = sorted((probe_dir / "run").glob("*run_report.json")) or sorted(probe_dir.glob("*run_report.json"))
    run_report = _load_first(run_report_paths)
    promotion_gate_paths = sorted((probe_dir / "run").glob("*promotion_gate.json")) or sorted(
        (probe_dir / "teacher_guided_repair_execution").glob("*promotion_gate.json")
    )
    connection_audit = _load_first(sorted((probe_dir / "connection_audit").glob("*connection_audit_summary.json")))
    variant_report_paths = sorted((probe_dir / "run").glob("candidate_*/*teacher_guided_report.json")) or sorted(
        (probe_dir / "teacher_guided_repair_execution").glob("candidate_*/*teacher_guided_report.json")
    )
    variant_report = _load_first(variant_report_paths)
    netedit_captures = sorted((probe_dir / "netedit_review").glob("**/netedit_connection_review.png"))
    target_junction = str(variant_report.get("junction_id") or "").strip()

    return {
        "probe_dir": str(probe_dir),
        "target_junction": target_junction,
        "claim_status": summary.get("claim_status", run_report.get("claim_status", "")),
        "summary_status": summary.get("status", ""),
        "run_status": run_report.get("status", ""),
        "parity_gate_status": summary.get("parity_gate_status", run_report.get("parity_gate_status", "")),
        "promotion_gate_status": summary.get("promotion_gate_status", run_report.get("promotion_gate_status", "")),
        "approach_integrity_status": summary.get("approach_integrity_status", ""),
        "attempted_candidate_count": int(summary.get("attempted_candidate_count", 0) or 0),
        "pass_candidate_count": int(summary.get("pass_candidate_count", 0) or 0),
        "failed_candidate_count": int(summary.get("failed_candidate_count", 0) or 0),
        "semantic_failure_counts": summary.get("semantic_failure_counts", {}) or {},
        "semantic_layer_gate_counts": run_report.get("semantic_layer_gate_counts", {}) or {},
        "promotion_gate_file": str(promotion_gate_paths[0]) if promotion_gate_paths else "",
        "connection_audit": {
            "status": "present" if connection_audit else "missing",
            "equal_signature": connection_audit.get("equal_signature") if connection_audit else None,
            "teacher_connections_by_dir": (connection_audit.get("teacher", {}) or {}).get("connections_by_dir", {}),
            "candidate_connections_by_dir": (connection_audit.get("candidate", {}) or {}).get("connections_by_dir", {}),
        },
        "netedit_connection_capture_gate": "pass" if netedit_captures else "missing",
        "netedit_connection_capture_files": [str(path) for path in netedit_captures],
        "small_probe_semantic_gate": _gate(summary, run_report, connection_audit),
    }


def build_report(probes: list[dict[str, Any]]) -> dict[str, Any]:
    semantic_pass = sum(1 for probe in probes if probe.get("small_probe_semantic_gate") == "pass")
    netedit_pass = sum(1 for probe in probes if probe.get("netedit_connection_capture_gate") == "pass")
    all_semantic = bool(probes) and semantic_pass == len(probes)
    all_netedit = bool(probes) and netedit_pass == len(probes)
    return {
        "status": "pass" if all_semantic and all_netedit else "partial" if all_semantic else "fail",
        "claim_status": "diagnostic-demo",
        "probe_count": len(probes),
        "semantic_pass_count": semantic_pass,
        "netedit_connection_capture_count": netedit_pass,
        "all_small_probe_semantic_gate_pass": all_semantic,
        "all_netedit_connection_capture_gate_pass": all_netedit,
        "probes": probes,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Summarize teacher-guided small-junction probe evidence.")
    parser.add_argument("probe_dirs", nargs="+", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    report = build_report([summarize_probe_dir(path) for path in args.probe_dirs])
    body = json.dumps(report, indent=2, ensure_ascii=False)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(body + "\n", encoding="utf-8")
    print(body)


if __name__ == "__main__":
    main()
