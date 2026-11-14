"""The project record of one OSM cleanup run: a one-screen workflow manifest and a network handoff.

The OSM workflow returns one large report. This module reads that report and writes what a reviewer needs first:
the inputs with their hashes, one row per stage with its status, reason and artifact hash, the first stage that is
not complete, the next action and the network to hand on. It decides nothing; every status comes from the gates
the workflow already computed.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .artifact_io import write_json_atomic
from .candidate_contracts import file_sha256

MANIFEST_SCHEMA = "astra.osm-workflow-manifest/v1"
HANDOFF_SCHEMA = "astra.network-handoff/v1"
CLAIM_BOUNDARY = ("The record repeats the gates of this run. A pass means the check ran and found nothing; it does not "
                  "prove topology, demand, control or field correctness.")
# Stage order of the workflow and the artifact (key of the report's artifact_hashes) that carries each result.
_STAGES: tuple[tuple[str, str | None], ...] = (
    ("area_confirmation", None), ("road_level_scope", None), ("network_build", "raw_net_file"),
    ("network_layers", None), ("master_tls_cycles", None), ("connectivity", "connected_core_file"),
    ("tls_reality_audit", "tls_review_file"), ("tls_aggregation", None), ("topology_audit", "topology_audit_report_file"),
    ("connection_mode_audit", "connection_mode_audit_report_file"), ("standard_nema_scan", "standard_nema_report_file"),
    ("routeability_audit", "routeability_audit_report_file"),
    ("boundary_completeness", "boundary_completeness_report_file"), ("tls_scoped_cell_batch", None),
    ("netedit", None), ("sumo_gui", None))
_COMPLETE = {"pass", "not_applicable"}


def _identity(path: Path) -> dict[str, Any] | None:
    return {"path": str(path), "sha256": file_sha256(path), "bytes": path.stat().st_size} if path.is_file() else None


def _stage_status(name: str, value: str, review_reasons: Mapping[str, str], report: Mapping[str, Any]) -> tuple[str, str]:
    """The gate value in the record's four status words, with the reason the workflow gave."""
    if name in review_reasons:
        return "review_required", review_reasons[name]
    if value == "pass":
        return "pass", ""
    if value in {"skipped", "not_applicable"}:
        return "not_applicable", "not_run"
    if name in {"netedit", "sumo_gui"} and report.get(f"{name}_status") == "skipped":
        return "not_applicable", "launch_not_requested"
    if name == "tls_aggregation" and report.get("tls_aggregation_status") == "variant_created_for_review":
        return "review_required", "variant_created_for_review"
    if value == "review_required":
        return "review_required", "review_required"
    return "blocked", str(value)


def _changes(build: Mapping[str, Any]) -> dict[str, Any]:
    grouping = build.get("netconvert_signal_grouping") or (build.get("netconvert") or {}).get("signal_grouping") or {}
    if not grouping:
        return {}
    joins = grouping.get("split_junction_joins") or {}
    widening = grouping.get("lane_widening") or {}
    return {"signal_centres": (grouping.get("stop_line_centres") or {}).get("centre_count"),
            "demoted_signal_nodes": grouping.get("demoted_signal_node_count"),
            "demoted_crossing_signal_nodes": grouping.get("demoted_crossing_signal_node_count"),
            "joined_junctions": joins.get("join_count"), "dropped_joins": len(joins.get("dropped_joins") or []),
            "deleted_turns": joins.get("deleted_turn_count", 0), "restriction_changes": joins.get("restriction_change_count"),
            "widened_lanes": widening.get("lane_count"),
            "restored_lane_connections": (grouping.get("lane_repair") or {}).get("dead_lane_count"),
            "master_rebuilds": grouping.get("master_rebuild_count"),
            "node_file": {"path": grouping.get("node_file"), "sha256": grouping.get("node_file_sha256")}}


def write_osm_workflow_record(report: Mapping[str, Any], *, output_dir: Path) -> dict[str, Any]:
    """Write ``workflow.manifest.json`` and ``network-handoff.json`` under ``output_dir`` and return their summary."""
    output_dir = Path(output_dir).resolve()
    gates = dict(report.get("gate_status") or {})
    review_reasons = {str(row.get("gate")): str(row.get("reason", "")) for row in report.get("review_required_gates") or []
                      if isinstance(row, Mapping)}
    hashes = report.get("artifact_hashes") or {}
    review_dir = output_dir / "review"
    order = [name for name, _ in _STAGES if name in gates] + sorted(set(gates) - {name for name, _ in _STAGES})
    artifact_keys = dict(_STAGES)
    stages = []
    for name in order:
        status, reason = _stage_status(name, str(gates[name]), review_reasons, report)
        row: dict[str, Any] = {"stage": name, "status": status}
        if reason:
            row["reason"] = reason
        entry = hashes.get(artifact_keys.get(name) or "")
        if isinstance(entry, Mapping) and entry.get("sha256"):
            row["artifact"] = {"path": str((review_dir / str(entry.get("path", ""))).resolve()), "sha256": entry["sha256"]}
        stages.append(row)
    first = next((row for row in stages if row["status"] not in _COMPLETE), None)
    status = str(report.get("status") or "blocked")
    if first is None:
        next_action = "hand_the_network_on"
    elif first["status"] == "blocked":
        next_action = f"return_to_{first['stage']}: {first.get('reason', '')}"
    else:
        next_action = f"review_{first['stage']}: {first.get('reason', '')}"
    build = report.get("build") if isinstance(report.get("build"), Mapping) else {}
    inputs = [{"role": role, **identity} for role, key in (("source_osm", "source_osm_file"),
                                                         ("netconvert_input_osm", "filtered_osm_file"))
              if build.get(key) and (identity := _identity(Path(str(build[key])).resolve()))]
    build_manifest = _identity(Path(str(build["build_manifest_file"])).resolve()) if build.get("build_manifest_file") else None
    manifest_file, handoff_file = output_dir / "workflow.manifest.json", output_dir / "network-handoff.json"
    manifest = {"schema": MANIFEST_SCHEMA, "workflow": "sumo_osm_cleanup_workflow", "status": status,
                "claim_status": report.get("claim_status"), "claim_boundary": CLAIM_BOUNDARY, "inputs": inputs, "stages": stages,
                "stage_counts": {word: sum(row["status"] == word for row in stages)
                                 for word in ("pass", "review_required", "blocked", "not_applicable")},
                "first_incomplete_stage": first["stage"] if first else None, "next_action": next_action,
                "changes": _changes(build), "build_manifest": build_manifest,
                "warnings": list(report.get("warnings") or [])[:20]}
    output_dir.mkdir(parents=True, exist_ok=True)
    write_json_atomic(manifest_file, manifest, ensure_ascii=False)
    networks = {}
    for role, key in (("passenger", "net_file"), ("vehicle_layer", "passenger_gate_net_file")):
        value = report.get(key)
        identity = _identity(Path(str(value)).resolve()) if value else None
        if identity:
            networks[role] = identity
    master = ((report.get("network_layers") or {}).get("multimodal") or {}) if isinstance(report.get("network_layers"), Mapping) else {}
    if master.get("net_file") and (identity := _identity(Path(str(master["net_file"])).resolve())):
        networks["multimodal_master"] = identity
    write_json_atomic(handoff_file, {"schema": HANDOFF_SCHEMA, "networks": networks, "construction_decision": status,
                                     "first_incomplete_stage": manifest["first_incomplete_stage"], "next_action": next_action,
                                     "workflow": _identity(manifest_file), "calibration_status": "not_run",
                                     "claim_boundary": CLAIM_BOUNDARY}, ensure_ascii=False)
    return {"workflow_record_file": str(manifest_file), "network_handoff_file": str(handoff_file),
            "first_incomplete_stage": manifest["first_incomplete_stage"], "next_action": next_action}
