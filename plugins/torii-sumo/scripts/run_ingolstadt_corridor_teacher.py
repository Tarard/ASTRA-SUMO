from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any


DEFAULT_BBOX = "11.413800,48.755391,11.433800,48.775391"
DEFAULT_JUNCTION_ID = "267517510"
WORKFLOW_MODES = ("bounded-slice", "reference-matched")
VISUAL_HIGHWAYS = {
    "cycleway",
    "footway",
    "living_street",
    "path",
    "pedestrian",
    "primary",
    "residential",
    "secondary",
    "secondary_link",
    "service",
    "steps",
    "tertiary",
    "tertiary_link",
    "track",
    "unclassified",
}


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Download the Ingolstadt reference bbox (or use an explicit candidate) and build one "
            "candidate-bound teacher-corridor review package."
        )
    )
    parser.add_argument("--output-dir", type=Path, default=None)
    parser.add_argument(
        "--workflow-mode",
        choices=WORKFLOW_MODES,
        default="bounded-slice",
        help=(
            "Keep the original one-junction comparison (bounded-slice), or delegate the same-bbox "
            "OSM/reference comparison to Torii's full reference-matched cleanup workflow."
        ),
    )
    parser.add_argument("--teacher-net", type=Path, default=None)
    parser.add_argument("--candidate-net", type=Path, default=None)
    parser.add_argument(
        "--source-osm",
        type=Path,
        default=None,
        help=(
            "Optional hash-fixed OSM evidence for offline replay. In reference-matched mode this "
            "is rebuilt inside the same bbox instead of downloading OSM again."
        ),
    )
    parser.add_argument("--bbox", default=DEFAULT_BBOX)
    parser.add_argument("--junction-id", default=DEFAULT_JUNCTION_ID)
    parser.add_argument("--historical-date", default=None)
    parser.add_argument("--map-temporal-scope", choices=("current", "historical"), default="current")
    parser.add_argument("--map-target-date", default=None)
    parser.add_argument("--overpass-url", default="https://overpass-api.de/api/interpreter")
    parser.add_argument("--timeout-seconds", type=float, default=240.0)
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--skip-runtime-audits", action="store_true")
    parser.add_argument(
        "--materialize-teacher-candidates",
        action="store_true",
        help=(
            "Removed for reference-matched cleanup. Use a dedicated reviewed materialization step "
            "after the audit."
        ),
    )
    return parser.parse_args()


def _reference_matched_workflow_kwargs(
    args: argparse.Namespace,
    *,
    output_dir: Path,
    teacher_net: Path,
) -> dict[str, Any]:
    """Build the seven-field audit-only cleanup contract."""

    return {
        "output_dir": str(output_dir / "reference_matched"),
        "bbox": args.bbox,
        "profile": "reference_matched",
        "source_osm_path": str(args.source_osm.resolve()) if args.source_osm is not None else None,
        "traffic_layers": None,
        "reference_net_file": str(teacher_net),
        "timeout_seconds": args.timeout_seconds,
    }


def _select_reference_matched_comparison_net(workflow: dict[str, Any]) -> str:
    """Expose the comparison layer without claiming that it was promoted."""

    for field in ("reference_visual_detail_comparison_net_file", "net_file"):
        value = str(workflow.get(field, "")).strip()
        if value and Path(value).is_file():
            return str(Path(value).resolve())
    return ""


def _reference_matched_summary(workflow: dict[str, Any]) -> dict[str, Any]:
    fields = (
        "status",
        "claim_status",
        "profile",
        "network_profile",
        "filtered_osm_file",
        "raw_net_file",
        "net_file",
        "reference_visual_detail_net_file",
        "reference_visual_detail_comparison_net_file",
        "reference_visual_detail_comparison_selection_reason",
        "reference_join_audit_status",
        "reference_join_audit_mode",
        "reference_join_reference_case_count",
        "reference_join_matched_case_count",
        "reference_join_unmatched_case_count",
        "reference_join_audit_report_file",
        "reference_join_audit_cases_file",
        "routeability_audit_status",
        "routeability_audit_report_file",
        "connection_mode_audit_status",
        "connection_mode_audit_report_file",
        "workflow_review_html_status",
        "artifact_hash_gate_status",
        "workflow_report_file",
        "review_manifest_file",
        "workflow_review_html_file",
        "gate_status",
        "warnings",
    )
    return {field: workflow.get(field) for field in fields if field in workflow}


def _build_teacher_action_contracts(
    workflow: dict[str, Any],
    *,
    bbox: str,
    source_osm: Path | None,
    teacher_net: Path,
    file_sha256_func: Any,
) -> dict[str, Any]:
    """Project the existing join audit into review-only cleanup actions."""

    report_file = Path(str(workflow.get("reference_join_audit_report_file", "")))
    if not report_file.is_file():
        return {
            "schema": "torii.reference_teacher_action_contracts.v1",
            "status": "blocked",
            "reason": "reference join audit report is unavailable",
            "promotion_gate_status": "blocked",
            "actions": [],
        }
    try:
        report = json.loads(report_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {
            "schema": "torii.reference_teacher_action_contracts.v1",
            "status": "blocked",
            "reason": f"reference join audit report is invalid: {type(exc).__name__}: {exc}",
            "promotion_gate_status": "blocked",
            "actions": [],
        }

    actions = []
    family_counts: dict[str, int] = {}
    for case in report.get("all_cases", []):
        teacher_nodes = sorted(str(value) for value in case.get("reference_joined_source_nodes", []))
        present_nodes = sorted(str(value) for value in case.get("matched_reference_source_node_ids", []))
        candidate_nodes = sorted(str(value) for value in case.get("matched_candidate_node_ids", []))
        missing_nodes = sorted(set(teacher_nodes) - set(present_nodes))
        extra_candidate_nodes = sorted(set(candidate_nodes) - set(teacher_nodes))
        identity_complete = bool(teacher_nodes) and not missing_nodes
        internal_edges = sorted(
            str(value) for value in case.get("matched_reference_source_internal_edge_ids", [])
        )
        boundary_edges = sorted(
            str(value) for value in case.get("matched_reference_source_boundary_edge_ids", [])
        )
        if case.get("match_status") != "matched":
            family = "abstain_unmatched_reference_case"
        elif not identity_complete:
            family = "abstain_incomplete_source_identity"
        elif internal_edges and len(boundary_edges) >= 2 and not extra_candidate_nodes:
            family = "bounded_conflict_core_join"
        else:
            family = "source_identity_join_review"

        blockers = []
        if not identity_complete:
            blockers.append("teacher source-node identity is incomplete in the same-bbox OSM network")
        if not internal_edges:
            blockers.append("no same-source internal OSM edge proves the absorbed core")
        if len(boundary_edges) < 2:
            blockers.append("fewer than two retained OSM boundary edges are proven")
        if extra_candidate_nodes:
            blockers.append("the topology cluster extends beyond the human teacher core")
        if case.get("match_status") != "matched":
            blockers.append("no same-bbox candidate cluster/source match was established")

        family_counts[family] = family_counts.get(family, 0) + 1
        actions.append(
            {
                "reference_id": str(case.get("reference_id", "")),
                "status": "review_required" if family.startswith(("bounded_", "source_")) else "blocked",
                "action_family": family,
                "teacher_action": {
                    "absorbed_source_node_ids": teacher_nodes,
                    "absorbed_internal_edge_ids": internal_edges,
                    "retained_boundary_edge_ids": boundary_edges,
                    "reference_approach_edge_ids": sorted(
                        str(value) for value in case.get("reference_approach_edge_ids", [])
                    ),
                },
                "applicability_evidence": {
                    "reference_type": str(case.get("reference_type", "")),
                    "source_identity_complete": identity_complete,
                    "source_node_match_ratio": case.get("reference_source_node_match_ratio", 0.0),
                    "same_source_internal_edge_count": len(internal_edges),
                    "retained_boundary_edge_count": len(boundary_edges),
                },
                "counterexample_evidence": {
                    "missing_teacher_source_node_ids": missing_nodes,
                    "candidate_nodes_outside_teacher_core": extra_candidate_nodes,
                    "candidate_risk_flags": sorted(
                        str(value) for value in case.get("matched_candidate_risk_flags", [])
                    ),
                    "blockers": blockers,
                },
                "transfer_gate_status": "blocked",
                "transfer_requirement": (
                    "independent target-city geometry, movement, retained-boundary, and audit evidence"
                ),
            }
        )

    source_evidence = source_osm
    if source_evidence is None:
        filtered_osm = str(workflow.get("filtered_osm_file", "")).strip()
        source_evidence = Path(filtered_osm) if filtered_osm else None
    provenance_paths = {
        "source_osm": source_evidence,
        "teacher_net": teacher_net,
        "reference_join_audit": report_file,
        "comparison_net": Path(str(report.get("candidate_net_file", ""))),
    }
    provenance: dict[str, Any] = {"bbox": bbox, "artifacts": {}}
    for role, path in provenance_paths.items():
        if path is None or not path.is_file():
            continue
        resolved = path.resolve()
        provenance["artifacts"][role] = {
            "path": str(resolved),
            "size_bytes": resolved.stat().st_size,
            "sha256": file_sha256_func(resolved),
        }

    return {
        "schema": "torii.reference_teacher_action_contracts.v1",
        "status": "pass" if actions else "blocked",
        "claim_status": "diagnostic-demo" if actions else "blocked",
        "reference_join_audit_report_file": str(report_file.resolve()),
        "reference_net_file": str(report.get("reference_net_file", "")),
        "candidate_net_file": str(report.get("candidate_net_file", "")),
        "action_count": len(actions),
        "action_family_counts": dict(sorted(family_counts.items())),
        "promotion_gate_status": "blocked",
        "promotion_gate_reason": (
            "Ingolstadt actions are teacher evidence only; target-city evidence must authorize every edit"
        ),
        "input_provenance": provenance,
        "actions": actions,
    }


def _collect_reference_matched_artifacts(
    *,
    workflow: dict[str, Any],
    aggregate_file: Path,
    teacher_net: Path,
    source_osm: Path | None,
    teacher_action_contracts_file: Path | None,
    file_sha256_func: Any,
) -> list[dict[str, Any]]:
    paths: list[Path] = [aggregate_file, teacher_net]
    if source_osm is not None:
        paths.append(source_osm)
    if teacher_action_contracts_file is not None:
        paths.append(teacher_action_contracts_file)
    # The inner review manifest already owns the exhaustive artifact inventory.
    # Bind only the layers this wrapper names directly instead of duplicating it.
    for field in (
        "raw_net_file",
        "net_file",
        "reference_visual_detail_net_file",
        "reference_visual_detail_comparison_net_file",
        "reference_join_audit_report_file",
        "reference_join_audit_cases_file",
        "routeability_audit_report_file",
        "connection_mode_audit_report_file",
        "workflow_report_file",
        "review_manifest_file",
    ):
        value = str(workflow.get(field, "")).strip()
        if value:
            paths.append(Path(value))
    artifacts: list[dict[str, Any]] = []
    seen: set[str] = set()
    for path in paths:
        if not path.is_file():
            continue
        resolved = str(path.resolve())
        if resolved in seen:
            continue
        seen.add(resolved)
        artifacts.append(
            {
                "path": resolved,
                "size_bytes": path.stat().st_size,
                "sha256": file_sha256_func(path),
            }
        )
    return artifacts


def _audit_reference_matched_input_parity(
    *,
    bbox: str,
    source_osm: Path | None,
    source_sha256_before: str,
    teacher_net: Path,
    teacher_sha256_before: str,
    workflow: dict[str, Any],
    file_sha256_func: Any,
) -> dict[str, Any]:
    """Bind the teacher estimate to immutable inputs and one exact bbox."""

    source_sha256_after = (
        file_sha256_func(source_osm) if source_osm is not None and source_osm.is_file() else ""
    )
    teacher_sha256_after = file_sha256_func(teacher_net) if teacher_net.is_file() else ""
    reference_scope = workflow.get("reference_bbox_scope")
    if not isinstance(reference_scope, dict):
        reference_scope = {}
    workflow_bbox = str(workflow.get("candidate_bbox", "")).strip()
    reference_bbox = str(reference_scope.get("candidate_bbox", "")).strip()
    source_unchanged = (
        source_osm is None
        or bool(source_sha256_before)
        and source_sha256_before == source_sha256_after
    )
    teacher_unchanged = bool(teacher_sha256_before) and (
        teacher_sha256_before == teacher_sha256_after
    )
    bbox_match = bool(bbox) and bbox == workflow_bbox == reference_bbox
    reference_scope_pass = reference_scope.get("status") == "pass"
    blockers = []
    if not source_unchanged:
        blockers.append("explicit source OSM changed during the teacher run")
    if not teacher_unchanged:
        blockers.append("human-cleaned teacher network changed during the teacher run")
    if not bbox_match:
        blockers.append("requested, candidate, and reference-scope bboxes are not identical")
    if not reference_scope_pass:
        blockers.append("reference bbox scope did not pass")
    return {
        "status": "pass" if not blockers else "blocked",
        "requested_bbox": bbox,
        "workflow_candidate_bbox": workflow_bbox,
        "reference_scope_candidate_bbox": reference_bbox,
        "reference_scope_status": str(reference_scope.get("status", "blocked")),
        "source_osm": {
            "path": str(source_osm) if source_osm is not None else "",
            "sha256_before": source_sha256_before,
            "sha256_after": source_sha256_after,
            "unchanged": source_unchanged,
            "mode": "explicit" if source_osm is not None else "downloaded_by_workflow",
        },
        "teacher_net": {
            "path": str(teacher_net),
            "sha256_before": teacher_sha256_before,
            "sha256_after": teacher_sha256_after,
            "unchanged": teacher_unchanged,
        },
        "blockers": blockers,
    }


def _run_reference_matched(
    args: argparse.Namespace,
    *,
    output_dir: Path,
    teacher_net: Path,
    binaries: dict[str, Any],
    workflow_func: Any | None = None,
) -> int:
    from torii_sumo.core.artifact_io import write_json_atomic
    from torii_sumo.core.candidate_contracts import file_sha256

    if workflow_func is None:
        from torii_sumo.tools.osm_tools import sumo_osm_cleanup_workflow

        workflow_func = sumo_osm_cleanup_workflow

    input_errors: list[str] = []
    if args.candidate_net is not None:
        input_errors.append(
            "--candidate-net is only valid for bounded-slice; reference-matched must rebuild from "
            "the OSM source so raw/aggregated/teacher layers remain distinct"
        )
    if not teacher_net.is_file():
        input_errors.append(f"teacher network does not exist: {teacher_net}")
    if args.materialize_teacher_candidates:
        input_errors.append(
            "--materialize-teacher-candidates was removed; reference-matched cleanup is audit-only"
        )
    source_osm = args.source_osm.resolve() if args.source_osm is not None else None
    if source_osm is not None and not source_osm.is_file():
        input_errors.append(f"source OSM does not exist: {source_osm}")

    source_sha256_before = (
        file_sha256(source_osm) if source_osm is not None and source_osm.is_file() else ""
    )
    teacher_sha256_before = file_sha256(teacher_net) if teacher_net.is_file() else ""
    if input_errors:
        workflow: dict[str, Any] = {
            "status": "fail",
            "claim_status": "construction-invalid",
            "network_profile": "reference_matched",
            "warnings": input_errors,
        }
    else:
        kwargs = _reference_matched_workflow_kwargs(
            args,
            output_dir=output_dir,
            teacher_net=teacher_net,
        )
        workflow = workflow_func(**kwargs)

    runtime_audit_status = (
        "skipped_by_user"
        if args.skip_runtime_audits
        else str(workflow.get("routeability_audit_status", "blocked"))
    )
    comparison_net = _select_reference_matched_comparison_net(workflow)
    raw_candidate_net = str(workflow.get("raw_net_file", "")).strip()
    runtime_audited_net = str(workflow.get("net_file", "")).strip()
    runtime_net_exists = bool(runtime_audited_net and Path(runtime_audited_net).is_file())
    comparison_net_exists = bool(comparison_net and Path(comparison_net).is_file())
    artifact_hash_gate_status = str(workflow.get("artifact_hash_gate_status", "blocked"))
    workflow_review_html_status = str(workflow.get("workflow_review_html_status", "blocked"))
    input_parity = _audit_reference_matched_input_parity(
        bbox=args.bbox,
        source_osm=source_osm,
        source_sha256_before=source_sha256_before,
        teacher_net=teacher_net,
        teacher_sha256_before=teacher_sha256_before,
        workflow=workflow,
        file_sha256_func=file_sha256,
    )
    execution_status = (
        "pass"
        if runtime_audit_status in {"pass", "skipped_by_user"}
        and runtime_net_exists
        and comparison_net_exists
        and artifact_hash_gate_status == "pass"
        and workflow_review_html_status == "pass"
        and input_parity["status"] == "pass"
        else "blocked"
    )
    evidence_status = (
        "pass"
        if execution_status == "pass" and workflow.get("status") == "pass"
        else "review_required"
        if execution_status == "pass"
        else "blocked"
    )
    claim_status = "diagnostic-demo" if execution_status == "pass" else "construction-invalid"
    aggregate_file = output_dir / "ingolstadt_corridor_teacher_run.json"
    manifest_file = output_dir / "ingolstadt_corridor_teacher_run.manifest.json"
    teacher_action_contracts_file = output_dir / "ingolstadt_teacher_action_contracts.json"
    teacher_action_contracts = _build_teacher_action_contracts(
        workflow,
        bbox=args.bbox,
        source_osm=source_osm,
        teacher_net=teacher_net,
        file_sha256_func=file_sha256,
    )
    write_json_atomic(teacher_action_contracts_file, teacher_action_contracts, sort_keys=True)
    aggregate = {
        "schema": "torii.ingolstadt_corridor_teacher_run.v2",
        "status": execution_status,
        "execution_status": execution_status,
        "evidence_status": evidence_status,
        "claim_status": claim_status,
        "workflow_mode": "reference-matched",
        "bbox": args.bbox,
        "junction_id": args.junction_id,
        "teacher_net_file": str(teacher_net),
        "raw_candidate_net_file": raw_candidate_net,
        "runtime_audited_net_file": runtime_audited_net,
        "comparison_net_file": comparison_net,
        "comparison_status": "review_ready" if comparison_net_exists else "blocked",
        "comparison_runtime_audit_status": "not_separately_run",
        "candidate_net_file": "",
        "promotion_gate_status": "blocked",
        "promotion_gate_reason": (
            "reference-matched cleanup is audit-only and never exposes a promoted candidate"
        ),
        "osm_source_mode": "explicit_osm_rebuild" if source_osm is not None else "downloaded_same_bbox",
        "historical_date": args.historical_date or "",
        "map_temporal_scope": args.map_temporal_scope,
        "map_target_date": args.map_target_date or "",
        "sumo_toolchain": binaries,
        "cleanup_workflow": _reference_matched_summary(workflow),
        "teacher_action_contracts": {
            "status": teacher_action_contracts["status"],
            "action_count": teacher_action_contracts.get("action_count", 0),
            "action_family_counts": teacher_action_contracts.get("action_family_counts", {}),
            "promotion_gate_status": teacher_action_contracts["promotion_gate_status"],
            "file": str(teacher_action_contracts_file),
        },
        "teacher_candidate_materialization": "not_available_in_cleanup_v2",
        "runtime_audit_status": runtime_audit_status,
        "artifact_hash_gate_status": artifact_hash_gate_status,
        "workflow_review_html_status": workflow_review_html_status,
        "input_parity": input_parity,
        "source_network_mutation": False,
        "manifest_file": str(manifest_file),
        "next_boundary": (
            "review reference cluster matches; use a dedicated materializer only when target-city "
            "geometry and movement evidence agree"
        ),
    }
    write_json_atomic(aggregate_file, aggregate, sort_keys=True)
    artifacts = _collect_reference_matched_artifacts(
        workflow=workflow,
        aggregate_file=aggregate_file,
        teacher_net=teacher_net,
        source_osm=source_osm,
        teacher_action_contracts_file=teacher_action_contracts_file,
        file_sha256_func=file_sha256,
    )
    write_json_atomic(
        manifest_file,
        {
            "schema": "torii.ingolstadt_corridor_teacher_manifest.v1",
            "status": execution_status,
            "execution_status": execution_status,
            "evidence_status": evidence_status,
            "claim_status": claim_status,
            "workflow_mode": "reference-matched",
            "bbox": args.bbox,
            "junction_id": args.junction_id,
            "source_overwrite_forbidden": True,
            "promotion_gate_status": "blocked",
            "artifact_hash_gate_status": artifact_hash_gate_status,
            "workflow_review_html_status": workflow_review_html_status,
            "input_parity": input_parity,
            "artifacts": artifacts,
        },
        sort_keys=True,
    )
    output = (
        {**aggregate, "aggregate_file": str(aggregate_file)}
        if args.verbose
        else {
            "status": execution_status,
            "execution_status": execution_status,
            "evidence_status": evidence_status,
            "workflow_mode": "reference-matched",
            "bbox": args.bbox,
            "junction_id": args.junction_id,
            "raw_candidate_net_file": raw_candidate_net,
            "runtime_audited_net_file": runtime_audited_net,
            "comparison_net_file": comparison_net,
            "candidate_net_file": "",
            "promotion_gate_status": "blocked",
            "artifact_hash_gate_status": artifact_hash_gate_status,
            "workflow_review_html_status": workflow_review_html_status,
            "reference_join_audit_status": workflow.get("reference_join_audit_status", "blocked"),
            "reference_join_matched_case_count": workflow.get("reference_join_matched_case_count", 0),
            "reference_join_unmatched_case_count": workflow.get("reference_join_unmatched_case_count", 0),
            "teacher_action_contracts_file": str(teacher_action_contracts_file),
            "teacher_action_contract_count": teacher_action_contracts.get("action_count", 0),
            "manifest_file": str(manifest_file),
            "aggregate_file": str(aggregate_file),
        }
    )
    print(json.dumps(output, indent=2, ensure_ascii=False))
    return 0 if execution_status == "pass" else 1


def main() -> int:
    args = _parse_args()
    repo_root = Path(__file__).resolve().parents[3]
    source_root = repo_root / "plugins" / "torii-sumo" / "src"
    if str(source_root) not in sys.path:
        sys.path.insert(0, str(source_root))

    from torii_sumo.core.artifact_io import write_json_atomic
    from torii_sumo.core.candidate_contracts import file_sha256
    from torii_sumo.core.command_runner import run_command
    from torii_sumo.core.osm_network import build_osm_network
    from torii_sumo.core.routeability_audit import run_routeability_audit
    from torii_sumo.core.sumo_commands import discover_binaries
    from torii_sumo.core.teacher_corridor import build_teacher_corridor_comparison
    from torii_sumo.core.tls_reference_cleanup import build_tls_reference_cleanup_variant

    default_output_name = (
        "reference_matched_current_osm"
        if args.workflow_mode == "reference-matched"
        else "current_osm"
    )
    output_dir = (
        args.output_dir.resolve()
        if args.output_dir is not None
        else repo_root / "outputs" / "ingolstadt_corridor_teacher_20260713" / default_output_name
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    teacher_net = (
        args.teacher_net.resolve()
        if args.teacher_net is not None
        else repo_root
        / "examples"
        / "02_one_prompt_osm_network"
        / "networks"
        / "tum_ingolstadt_center_reference.net.xml"
    )
    binaries = discover_binaries()
    netconvert = str(binaries.get("netconvert", ""))
    if not netconvert:
        raise RuntimeError("netconvert is not available from a consistent SUMO toolchain")
    sumo_bin_dir = Path(netconvert).resolve().parent
    os.environ["SUMO_HOME"] = str(sumo_bin_dir.parent)
    os.environ["PATH"] = f"{sumo_bin_dir}{os.pathsep}{os.environ.get('PATH', '')}"

    if args.workflow_mode == "reference-matched":
        return _run_reference_matched(
            args,
            output_dir=output_dir,
            teacher_net=teacher_net,
            binaries=binaries,
        )

    build_report: dict[str, Any]
    if args.candidate_net is not None:
        raw_candidate_net = args.candidate_net.resolve()
        explicit_osm = args.source_osm.resolve() if args.source_osm is not None else None
        explicit_inputs_exist = raw_candidate_net.is_file() and (
            explicit_osm is None or explicit_osm.is_file()
        )
        build_report = {
            "status": "pass" if explicit_inputs_exist else "blocked",
            "source": "explicit_candidate_net",
            "bbox": args.bbox,
            "net_file": str(raw_candidate_net),
            "source_osm_file": str(explicit_osm) if explicit_osm is not None else "",
            "warnings": [
                "OSM download was skipped because --candidate-net was supplied; supplied files are "
                "hash-bound into the replay manifest"
            ],
        }
        if not explicit_inputs_exist:
            build_report["error"] = "explicit candidate or optional source OSM does not exist"
    else:
        build_report = build_osm_network(
            bbox=args.bbox,
            output_dir=output_dir / "osm_build",
            prefix="ingolstadt_same_bbox_current_osm",
            source_osm_path=args.source_osm.resolve() if args.source_osm is not None else None,
            allowed_highways=set(VISUAL_HIGHWAYS),
            historical_date=args.historical_date,
            overpass_url=args.overpass_url,
            timeout_seconds=args.timeout_seconds,
            max_tile_area_km2=2500.0,
            max_retries=2,
            retry_pause_seconds=5.0,
            netconvert_profile="reference_visual_detail",
            include_railway=True,
            allowed_railways={"rail"},
            netconvert_binary=netconvert,
        )
        built_net_value = str(build_report.get("net_file", "")).strip()
        raw_candidate_net = (
            Path(built_net_value).resolve()
            if built_net_value
            else output_dir / "unavailable_candidate.net.xml"
        )
    build_report_file = output_dir / "same_bbox_osm_build.json"
    write_json_atomic(build_report_file, build_report, sort_keys=True)

    tls_reference_cleanup: dict[str, Any] = {
        "status": "not_run",
        "tls_reference_cleanup_status": "not_run",
    }
    candidate_net = raw_candidate_net
    if build_report.get("status") == "pass" and raw_candidate_net.is_file():
        tls_reference_cleanup = build_tls_reference_cleanup_variant(
            raw_candidate_net,
            output_dir=output_dir / "tls_reference_cleanup",
            prefix="ingolstadt_same_bbox",
        )
        effective_net = str(tls_reference_cleanup.get("effective_net_file", "")).strip()
        if tls_reference_cleanup.get("status") == "pass" and effective_net:
            candidate_net = Path(effective_net).resolve()

    sumo_load_report: dict[str, Any] = {"status": "not_run"}
    routeability_report: dict[str, Any] = {"status": "not_run"}
    if (
        build_report.get("status") == "pass"
        and tls_reference_cleanup.get("status") == "pass"
        and candidate_net.is_file()
        and not args.skip_runtime_audits
    ):
        sumo_load_log = output_dir / "sumo_load_errors.log"
        sumo_load_command = [
            str(binaries["sumo"]),
            "--net-file",
            str(candidate_net),
            "--quit-on-end",
            "--duration-log.disable",
            "--no-step-log",
            "--error-log",
            str(sumo_load_log),
        ]
        sumo_load_result = run_command(
            sumo_load_command,
            cwd=output_dir,
            timeout_seconds=args.timeout_seconds,
        ).to_dict()
        sumo_load_report = {
            "status": (
                "pass"
                if sumo_load_result.get("status") == "pass"
                and sumo_load_result.get("returncode") == 0
                else "blocked"
            ),
            "command": sumo_load_command,
            "result": sumo_load_result,
            "log_file": str(sumo_load_log),
        }
        routeability_report = run_routeability_audit(
            net_file=candidate_net,
            output_dir=output_dir / "routeability",
            prefix="ingolstadt_same_bbox",
            vehicle_count=10,
            initial_end=300,
            max_end=1200,
            timeout_seconds=args.timeout_seconds,
            binaries=binaries,
        )
    elif args.skip_runtime_audits:
        sumo_load_report = {"status": "skipped_by_user"}
        routeability_report = {"status": "skipped_by_user"}
    sumo_load_report_file = output_dir / "sumo_load_report.json"
    write_json_atomic(sumo_load_report_file, sumo_load_report, sort_keys=True)

    if (
        build_report.get("status") == "pass"
        and tls_reference_cleanup.get("status") == "pass"
        and candidate_net.is_file()
    ):
        comparison = build_teacher_corridor_comparison(
            teacher_net_file=teacher_net,
            candidate_net_file=candidate_net,
            junction_id=args.junction_id,
            output_dir=output_dir / "teacher_corridor",
            prefix=f"ingolstadt_{args.junction_id}",
            map_temporal_scope=args.map_temporal_scope,
            map_target_date=args.map_target_date,
            osm_file=(
                Path(str(build_report.get("source_osm_file", "")))
                if str(build_report.get("source_osm_file", "")).strip()
                else None
            ),
        )
    else:
        comparison = {
            "status": "blocked",
            "claim_status": "construction-invalid",
            "teacher_transfer_status": "not_started",
            "error": "same-bbox OSM build or bounded TLS-reference cleanup did not pass",
        }

    runtime_pass = args.skip_runtime_audits or (
        sumo_load_report.get("status") == "pass"
        and routeability_report.get("status") == "pass"
    )
    status = (
        "pass"
        if build_report.get("status") == "pass"
        and tls_reference_cleanup.get("status") == "pass"
        and comparison.get("status") == "pass"
        and runtime_pass
        else "blocked"
    )
    manifest_file = output_dir / "ingolstadt_corridor_teacher_run.manifest.json"
    aggregate = {
        "schema": "torii.ingolstadt_corridor_teacher_run.v2",
        "status": status,
        "claim_status": "diagnostic-demo" if status == "pass" else "construction-invalid",
        "workflow_mode": "bounded-slice",
        "bbox": args.bbox,
        "junction_id": args.junction_id,
        "teacher_net_file": str(teacher_net),
        "raw_candidate_net_file": str(raw_candidate_net),
        "candidate_net_file": str(candidate_net),
        "osm_source_mode": (
            "explicit_candidate_with_osm_evidence"
            if args.candidate_net is not None and args.source_osm is not None
            else "explicit_candidate"
            if args.candidate_net is not None
            else "explicit_osm_rebuild"
            if args.source_osm is not None
            else "downloaded_same_bbox"
        ),
        "historical_date": args.historical_date or "",
        "map_temporal_scope": args.map_temporal_scope,
        "map_target_date": args.map_target_date or "",
        "sumo_toolchain": binaries,
        "build_report_file": str(build_report_file),
        "build": build_report,
        "tls_reference_cleanup": tls_reference_cleanup,
        "sumo_load": sumo_load_report,
        "routeability": routeability_report,
        "teacher_corridor": comparison,
        "runtime_audit_status": "skipped_by_user" if args.skip_runtime_audits else (
            "pass" if runtime_pass else "blocked"
        ),
        "source_network_mutation": False,
        "manifest_file": str(manifest_file),
        "next_boundary": (
            "review the bounded structural TLS cleanup and teacher differences against current map evidence, "
            "then materialize one crossing or TLS/movement candidate at a time through the corridor contract"
        ),
    }
    aggregate_file = output_dir / "ingolstadt_corridor_teacher_run.json"
    write_json_atomic(aggregate_file, aggregate, sort_keys=True)
    artifact_paths = [
        build_report_file,
        raw_candidate_net,
        candidate_net,
        sumo_load_report_file,
        aggregate_file,
    ]
    for value in (
        build_report.get("source_osm_file"),
        build_report.get("filtered_osm_file"),
        tls_reference_cleanup.get("plan_file"),
        tls_reference_cleanup.get("report_file"),
        tls_reference_cleanup.get("manifest_file"),
        tls_reference_cleanup.get("review_overlay_file"),
        routeability_report.get("report_file"),
        routeability_report.get("manifest_file"),
        comparison.get("report_file"),
        comparison.get("manifest_file"),
        comparison.get("map_review_evidence_file"),
        comparison.get("review_overlay_file"),
        comparison.get("review_decision_template_file"),
        comparison.get("review_html_file"),
    ):
        if str(value or "").strip():
            artifact_paths.append(Path(str(value)))
    artifacts = []
    seen_paths: set[str] = set()
    for artifact in artifact_paths:
        if not artifact.is_file():
            continue
        resolved = str(artifact.resolve())
        if resolved in seen_paths:
            continue
        seen_paths.add(resolved)
        artifacts.append(
            {
                "path": resolved,
                "size_bytes": artifact.stat().st_size,
                "sha256": file_sha256(artifact),
            }
        )
    write_json_atomic(
        manifest_file,
        {
            "schema": "torii.ingolstadt_corridor_teacher_manifest.v1",
            "status": status,
            "claim_status": aggregate["claim_status"],
            "workflow_mode": "bounded-slice",
            "bbox": args.bbox,
            "junction_id": args.junction_id,
            "source_overwrite_forbidden": True,
            "artifacts": artifacts,
        },
        sort_keys=True,
    )
    output = (
        {**aggregate, "aggregate_file": str(aggregate_file)}
        if args.verbose
        else {
            "status": status,
            "workflow_mode": "bounded-slice",
            "bbox": args.bbox,
            "junction_id": args.junction_id,
            "osm_source_mode": aggregate["osm_source_mode"],
            "raw_candidate_net_file": str(raw_candidate_net),
            "candidate_net_file": str(candidate_net),
            "tls_reference_cleanup_status": tls_reference_cleanup.get(
                "tls_reference_cleanup_status",
                "blocked",
            ),
            "teacher_transfer_status": comparison.get("teacher_transfer_status", "blocked"),
            "mismatch_fields": comparison.get("comparison", {}).get("mismatch_fields", []),
            "map_review_readiness_status": comparison.get(
                "map_review_readiness_status",
                "blocked",
            ),
            "review_html_file": comparison.get("review_html_file", ""),
            "runtime_audit_status": aggregate["runtime_audit_status"],
            "manifest_file": str(manifest_file),
            "aggregate_file": str(aggregate_file),
        }
    )
    print(json.dumps(output, indent=2, ensure_ascii=False))
    return 0 if status == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
