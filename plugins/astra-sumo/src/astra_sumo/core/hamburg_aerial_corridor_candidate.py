"""Build Hamburg corridor candidates through focused junction domain modules."""

from __future__ import annotations

from .hamburg_junctions.movements import _lane_motorized_modes as _lane_motorized_modes
import json
import re
import shutil
import xml.etree.ElementTree as ET
from collections.abc import Mapping
from pathlib import Path
from typing import Any
from .candidate_contracts import file_sha256
from .command_runner import run_command
from .connection_mode_audit import audit_network_connection_mode
from .hamburg_aerial_approach import build_hamburg_aerial_approach_candidate
from .routeability_audit import run_routeability_audit
from .topology_signal_rebuild import rebuild_topology_test_signals
from .surface_overlap_audit import audit_sumo_lane_junction_surface_overlaps
from .hamburg_junctions.boundaries import (
    _adjust_native_port_conflicts as _adjust_native_port_conflicts,
    _boundary_distance as _boundary_distance,
    _classify_boundary_ports as _classify_boundary_ports,
    _official_boundary_sections as _official_boundary_sections,
    _port_section as _port_section,
    _portal_boundary_polygon as _portal_boundary_polygon,
    _rebuild_join_boundaries as _rebuild_join_boundaries,
    _resolved_boundary_polygon as _resolved_boundary_polygon,
    _safe_native_cut_port as _safe_native_cut_port,
    _section_touches_polygon as _section_touches_polygon,
    _strip_longitudinal_interval as _strip_longitudinal_interval,
)
from .hamburg_junctions.geometry import (
    Point as Point,
    _distance_to_lines as _distance_to_lines,
    _marching_loop as _marching_loop,
    _movement_centroid as _movement_centroid,
    _net_offset as _net_offset,
    _parse_shape as _parse_shape,
    _point_segment_distance as _point_segment_distance,
    _polygon_area as _polygon_area,
    _segment_projection as _segment_projection,
    _shape as _shape,
    _simplify_closed as _simplify_closed,
    fit_movement_shape_to_anchors as fit_movement_shape_to_anchors,
    movement_surface_polygon as movement_surface_polygon,
    reanchor_movement_shape as reanchor_movement_shape,
)
from .hamburg_junctions.groups import (
    _movements_by_part as _movements_by_part,
    _prepare_context_joins as _prepare_context_joins,
    _select_join_groups as _select_join_groups,
    _write_join_file as _write_join_file,
)
from .hamburg_junctions.lanes import (
    _approach_lane_identities as _approach_lane_identities,
    _bind_official_lanes as _bind_official_lanes,
    _common_section_lane_order as _common_section_lane_order,
    _keep_boundary_lane_identity as _keep_boundary_lane_identity,
    _keep_constructed_lane_identity as _keep_constructed_lane_identity,
    _lane_overlap_error as _lane_overlap_error,
    _lane_section_lateral_offset as _lane_section_lateral_offset,
    _ordered_approach_lane_bindings as _ordered_approach_lane_bindings,
)
from .hamburg_junctions.movements import (
    _compare_contour_compilation as _compare_contour_compilation,
    _compile_junction_contours as _compile_junction_contours,
    _connection_key as _connection_key,
    _contour_curve_difference as _contour_curve_difference,
    _write_movement_candidate as _write_movement_candidate,
)
from .hamburg_junctions.source import (
    REQUEST_SCHEMA as REQUEST_SCHEMA,
    _load_json as _load_json,
    _load_movement_plans as _load_movement_plans,
    _official_boundary_widths as _official_boundary_widths,
    _official_internal_paths as _official_internal_paths,
    _official_lane_boundary_points as _official_lane_boundary_points,
    _project_plan_to_network as _project_plan_to_network,
    _read_request as _read_request,
    _verified_artifact as _verified_artifact,
)


CANDIDATE_SCHEMA = "torii.hamburg-aerial-corridor-candidate/v1"


def build_hamburg_aerial_combined_candidate(
    *,
    request_file: Path | str,
    output_dir: Path | str,
) -> dict[str, Any]:
    """Build and test a corridor from the intersections declared in the plan."""
    request_path = Path(request_file).expanduser().resolve()
    request = _read_request(request_path)
    destination = Path(output_dir).expanduser().resolve()
    if destination.exists():
        raise ValueError("output_dir must not already exist")
    source_net = _verified_artifact(request["source_net"], "source_net")
    cluster_binding = _verified_artifact(
        request["cluster_binding"],
        "cluster_binding",
    )
    movement_summary = _verified_artifact(
        request["movement_summary"],
        "movement_summary",
    )
    binding = _load_json(cluster_binding, "cluster_binding")
    summary = _load_json(movement_summary, "movement_summary")
    if binding.get("schema") != "torii.hamburg-five-corridor-tls-cluster-binding/v1":
        raise ValueError("cluster binding schema is invalid")
    if binding.get("status") != "pass":
        raise ValueError("a passing cluster binding is required before network construction")
    if summary.get("schema") != "torii.hamburg-aerial-corridor-plan/v1":
        raise ValueError("movement summary schema is invalid")
    if str(binding.get("inputs", {}).get("network", {}).get("sha256", "")).lower() != file_sha256(source_net):
        raise ValueError("cluster binding does not match source_net")
    plans = _load_movement_plans(summary, base_dir=movement_summary.parent)
    source_root = ET.parse(source_net).getroot()
    plans = {node_id: _project_plan_to_network(plan, source_root) for node_id, plan in plans.items()}
    groups = _select_join_groups(source_root, binding, plans,
        maximum_lane_projection_error_m=float(request["maximum_lane_projection_error_m"]),
        minimum_lane_match_margin_m=float(request["minimum_lane_match_margin_m"]))
    destination.mkdir(parents=True)
    context = _prepare_context_joins(
        source_net=source_net, context_groups=request["context_joins"], official_groups=groups,
        output_dir=destination / "context-joins",
        adjacent_geometry_junction_ids=request["context_geometry_neighbors"],
        interior_lane_change_policy=request["context_lane_change_policy"],
        junction_contours=request["junction_contours"],
        junction_corner_radius_m=request["junction_corner_radius_m"],
        netconvert_binary=str(request.get("netconvert_binary", "netconvert")),
        sumo_binary=str(request.get("sumo_binary", "sumo")),
        timeout_seconds=float(request["timeout_seconds"]), seed=int(request["seed"]),
    ) if request["context_joins"] else None
    construction_net = Path(context["candidate_network"]["path"]) if context else source_net
    join_file = destination / "physical-part-joins.nod.xml"
    _write_join_file(join_file, groups)
    joined_auto = destination / "joined-auto.net.xml"
    joined_net = destination / "joined-base.net.xml"
    joined_output = destination / "joined-output.nod.xml"
    netconvert = run_command(
        [
            str(request.get("netconvert_binary", "netconvert")),
            "--junctions.internal-link-detail",
            "25",
            "--sumo-net-file",
            str(construction_net),
            "--node-files",
            str(join_file),
            "--output-file",
            str(joined_auto),
            "--junctions.join-output",
            str(joined_output),
            "--seed",
            str(int(request["seed"])),
            "--offset.disable-normalization",
            "true",
        ],
        cwd=destination,
        timeout_seconds=float(request["timeout_seconds"]),
    )
    (destination / "netconvert.log").write_text(
        netconvert.stdout + netconvert.stderr,
        encoding="utf-8",
    )
    if netconvert.returncode != 0 or not joined_auto.is_file():
        raise ValueError("netconvert could not build the physical-part candidate")
    boundary_rebuild = _rebuild_join_boundaries(
        joined_auto, joined_net, groups,
        netconvert_binary=str(request.get("netconvert_binary", "netconvert")),
        timeout_seconds=float(request["timeout_seconds"]),
    )
    approach_rebuild = build_hamburg_aerial_approach_candidate(
        source_net=joined_net,
        plans=plans,
        junction_bindings={(str(row["node_id"]), str(row["intersection_part"])): str(row["join_id"]) for row in groups},
        output_dir=destination / "approach-sections",
        netconvert_binary=str(request.get("netconvert_binary", "netconvert")),
        timeout_seconds=float(request["timeout_seconds"]),
        maximum_lane_error_m=float(request["maximum_lane_projection_error_m"]),
        minimum_match_margin_m=float(request["minimum_lane_match_margin_m"]),
    )
    if approach_rebuild["status"] == "blocked":
        raise ValueError("official approach reconstruction failed its preservation or connection checks")
    movement_base = (
        Path(approach_rebuild["candidate_network"]["path"])
        if approach_rebuild.get("candidate_network") else joined_net
    )
    constructed_ingress, lane_origins, changed_edge_ids = _approach_lane_identities(source_root, approach_rebuild, groups)

    skipped_join_ids: set[str] = set()
    attempts = []
    selected_candidate = None
    selected_materialization = None
    routeability = None
    signal_rebuilds = []
    source_lane_change_evidence = []
    frozen_probe_trips = None
    frozen_probe_sha256 = None
    for attempt_index in range(3):
        candidate = destination / f"candidate-attempt-{attempt_index}.net.xml"
        materialization = _write_movement_candidate(
            joined_net=movement_base,
            plans=plans,
            groups=groups,
            skipped_join_ids=skipped_join_ids,
            output_file=candidate,
            maximum_anchor_projection_error_m=float(
                request["maximum_anchor_projection_error_m"]
            ),
            maximum_lane_projection_error_m=float(request["maximum_lane_projection_error_m"]),
            minimum_lane_match_margin_m=float(request["minimum_lane_match_margin_m"]),
            netconvert_binary=str(request.get("netconvert_binary", "netconvert")),
            timeout_seconds=float(request["timeout_seconds"]),
            original_net=source_net,
            constructed_ingress=constructed_ingress,
            lane_origins=lane_origins,
            identity_changed_edges=changed_edge_ids,
            source_lane_change_evidence=source_lane_change_evidence,
            sumo_binary=str(request.get("sumo_binary", "sumo")),
            seed=int(request["seed"]),
            junction_contours=request["junction_contours"],
            junction_corner_radius_m=request["junction_corner_radius_m"],
        )
        signal_report = rebuild_topology_test_signals(
            net_file=candidate,
            output_dir=destination / f"test-signals-attempt-{attempt_index}",
            target_junction_ids=sorted(node.get("id") for node in ET.parse(candidate).getroot().findall("junction")
                                       if node.get("type", "").startswith("traffic_light")),
            expected_source_sha256=file_sha256(candidate),
        ) if groups else None
        if signal_report is not None:
            if signal_report["status"] != "pass":
                raise ValueError("generic topology signal rebuilding failed its conflict or preservation checks")
            signal_rebuilds.append(signal_report)
            candidate = Path(signal_report["candidate_network"]["path"])
        routeability = run_routeability_audit(
            net_file=candidate,
            output_dir=destination / f"routeability-attempt-{attempt_index}",
            prefix="hamburg_five_combined",
            vehicle_count=int(request["vehicle_count"]),
            seed=int(request["seed"]),
            initial_end=int(request["simulation_end"]),
            max_end=int(request["simulation_max_end"]),
            timeout_seconds=float(request["timeout_seconds"]),
            od_reference_net_file=source_net,
            frozen_trip_file=frozen_probe_trips,
            expected_frozen_trip_sha256=frozen_probe_sha256,
        )
        if frozen_probe_trips is None and routeability.get("trip_file"):
            planned_trips = Path(routeability["trip_file"])
            if planned_trips.is_file():
                frozen_probe_trips, frozen_probe_sha256 = planned_trips, file_sha256(planned_trips)
        attempts.append(
            {
                "attempt": attempt_index,
                "candidate": str(candidate),
                "candidate_sha256": file_sha256(candidate),
                "skipped_join_ids": sorted(skipped_join_ids),
                "materialized_movement_count": materialization["materialized_movement_count"],
                "routeability_status": routeability["status"],
                "routeability_report": routeability["report_file"],
            }
        )
        selected_candidate = candidate
        selected_materialization = materialization
        if routeability["status"] == "pass":
            break
        affected = _collision_join_ids(routeability)
        new_affected = affected - skipped_join_ids
        if not new_affected:
            break
        skipped_join_ids.update(new_affected)

    assert selected_candidate is not None
    assert selected_materialization is not None
    assert routeability is not None
    source_routeability = run_routeability_audit(
        net_file=source_net, output_dir=destination / "source-routeability",
        prefix="same_od_source", vehicle_count=int(request["vehicle_count"]), seed=int(request["seed"]),
        initial_end=int(request["simulation_end"]), max_end=int(request["simulation_max_end"]),
        timeout_seconds=float(request["timeout_seconds"]), frozen_trip_file=frozen_probe_trips,
        expected_frozen_trip_sha256=frozen_probe_sha256,
    ) if frozen_probe_trips is not None else None
    final_net = destination / "hamburg-five-aerial-combined.net.xml"
    shutil.copy2(selected_candidate, final_net)
    load = run_command(
        [
            str(request.get("sumo_binary", "sumo")),
            "-n",
            str(final_net),
            "--begin",
            "0",
            "--end",
            "1",
            "--no-step-log",
            "true",
        ],
        cwd=destination,
        timeout_seconds=float(request["timeout_seconds"]),
    )
    (destination / "sumo-load.log").write_text(
        load.stdout + load.stderr,
        encoding="utf-8",
    )
    source_surface = audit_sumo_lane_junction_surface_overlaps(
        source_net,
        report_file=destination / "source-surface-overlap.json",
    )
    candidate_surface = audit_sumo_lane_junction_surface_overlaps(
        final_net,
        report_file=destination / "candidate-surface-overlap.json",
    )
    source_findings = _surface_finding_count(source_surface)
    candidate_findings = _surface_finding_count(candidate_surface)
    connection_audit = audit_network_connection_mode(ET.parse(final_net).getroot(), endpoint_tolerance_m=0.1)
    connection_report = destination / "connection-mode-audit.json"
    connection_report.write_text(json.dumps(connection_audit, ensure_ascii=False, indent=2), encoding="utf-8")
    topology = selected_materialization["official_connection_audit"]
    structural_pass = connection_audit["structural_failure_count"] == 0 and connection_audit["status"] != "fail"
    source_unchanged = file_sha256(source_net) == str(request["source_net"]["sha256"]).lower()
    status = (
        "pass"
        if load.returncode == 0
        and routeability["status"] == "pass"
        and candidate_findings <= source_findings
        and structural_pass
        and source_unchanged
        and topology["status"] != "blocked"
        else "blocked"
    )
    plan_hashes = {
        node_id: str(row["plan_sha256"]).lower()
        for node_id, row in {
            str(item["node_id"]): item for item in summary["intersections"]
        }.items()
    }
    manifest = {
        "schema": CANDIDATE_SCHEMA,
        "status": status,
        "decision": "review_required" if status == "pass" else "blocked",
        "topology_complete": status == "pass" and topology["status"] == "pass",
        "claim_status": "diagnostic-demo" if status == "pass" else "construction-invalid",
        "inputs": {
            "request": {"path": str(request_path), "sha256": file_sha256(request_path)},
            "source_net": {"path": str(source_net), "sha256": file_sha256(source_net)},
            "cluster_binding": {
                "path": str(cluster_binding),
                "sha256": file_sha256(cluster_binding),
            },
            "movement_summary": {
                "path": str(movement_summary),
                "sha256": file_sha256(movement_summary),
            },
            "movement_plans": plan_hashes,
        },
        "counts": {
            "official_vehicle_movements": int(summary["totals"]["official_vehicle_movements"]),
            "physical_part_count": sum(len(_movements_by_part(plan)) for plan in plans.values()),
            "joined_part_count": sum(len(group["source_node_ids"]) > 1 for group in groups),
            "materialized_movement_count": selected_materialization["materialized_movement_count"],
            "unmaterialized_official_movement_count": (
                int(summary["totals"]["official_vehicle_movements"])
                - selected_materialization["materialized_movement_count"]
            ),
            "runtime_fallback_part_count": len(skipped_join_ids),
            "source_surface_finding_count": source_findings,
            "candidate_surface_finding_count": candidate_findings,
        },
        "parameters": {
            "seed": int(request["seed"]),
            "vehicle_count": int(request["vehicle_count"]),
            "simulation_end": int(request["simulation_end"]),
            "simulation_max_end": int(request["simulation_max_end"]),
            "junction_contours": request["junction_contours"],
            "junction_corner_radius_m": request["junction_corner_radius_m"],
            "maximum_anchor_projection_error_m": float(
                request["maximum_anchor_projection_error_m"]
            ),
        },
        "physical_parts": groups,
        "context_rebuild": context,
        "boundary_rebuild": boundary_rebuild,
        "approach_rebuild": approach_rebuild,
        "generic_test_signals": signal_rebuilds,
        "movement_materialization": selected_materialization,
        "official_connection_audit": topology,
        "connection_mode": {"status": connection_audit["status"], "structural_failure_count": connection_audit["structural_failure_count"], "report": str(connection_report)},
        "runtime_fallback_join_ids": sorted(skipped_join_ids),
        "attempts": attempts,
        "netconvert": {
            "returncode": netconvert.returncode,
            "warning_count": sum(
                line.startswith("Warning:")
                for line in (netconvert.stdout + netconvert.stderr).splitlines()
            ),
            "log": str(destination / "netconvert.log"),
        },
        "sumo_load": {
            "returncode": load.returncode,
            "stderr": load.stderr,
            "log": str(destination / "sumo-load.log"),
        },
        "routeability": routeability,
        "source_routeability_same_requests": source_routeability,
        "routeability_pairing": {
            "trip_file": str(frozen_probe_trips) if frozen_probe_trips is not None else None,
            "trip_sha256": frozen_probe_sha256,
            "source_status": source_routeability.get("status") if source_routeability else "not_run",
            "candidate_status": routeability["status"],
            "basis": "Generate one declared eligible OD set, then retain the same requests across candidate attempts and the original source run. Eligibility changes and all routing failures remain visible.",
            "claim_boundary": "This is synthetic topology diagnostics, not calibrated demand or a field traffic-performance comparison.",
        },
        "surface": {
            "source": {
                "status": source_surface["status"],
                "finding_count": source_findings,
                "report": str(destination / "source-surface-overlap.json"),
            },
            "candidate": {
                "status": candidate_surface["status"],
                "finding_count": candidate_findings,
                "report": str(destination / "candidate-surface-overlap.json"),
            },
        },
        "gates": {
            "source_hashes": "pass" if source_unchanged else "blocked",
            "netconvert": "pass" if netconvert.returncode == 0 else "blocked",
            "sumo_load": "pass" if load.returncode == 0 else "blocked",
            "routeability": "pass" if routeability["status"] == "pass" else "blocked",
            "surface_regression": "pass" if candidate_findings <= source_findings else "blocked",
            "connection_structure": "pass" if structural_pass else "blocked",
            "official_lane_connections": topology["status"],
            "all_official_movements_materialized": (
                "pass"
                if selected_materialization["materialized_movement_count"]
                == int(summary["totals"]["official_vehicle_movements"])
                else "review_required"
            ),
            "official_signal_timing": "not_run",
        },
        "artifacts": {
            "network": {"path": str(final_net), "sha256": file_sha256(final_net)},
            "movement_base": {"path": str(movement_base), "sha256": file_sha256(movement_base)},
            "construction_net": {"path": str(construction_net), "sha256": file_sha256(construction_net)},
            "join_file": {"path": str(join_file), "sha256": file_sha256(join_file)},
        },
        "claim_boundary": (
            "This candidate combines physical-part node joins and a bounded subset of selected movement curves. "
            "Unmatched movements retain source SUMO geometry. Signal timing remains source-derived or generic. "
            "The candidate is diagnostic and requires visual review before any promotion."
        ),
    }
    manifest_file = destination / "manifest.json"
    manifest_file.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    manifest["artifacts"]["manifest"] = {
        "path": str(manifest_file),
        "sha256": file_sha256(manifest_file),
    }
    return manifest


def _collision_join_ids(routeability: Mapping[str, Any]) -> set[str]:
    stderr = str(routeability.get("final_attempt", {}).get("command", {}).get("stderr", ""))
    return set(re.findall(r"lane=':(LSA\d+_part\d+)_", stderr))


def _surface_finding_count(report: Mapping[str, Any]) -> int:
    return int(report.get("junction_junction_overlap_count", 0)) + int(
        report.get("external_lane_non_owner_junction_overlap_count", 0)
    )


__all__ = [
    "CANDIDATE_SCHEMA",
    "REQUEST_SCHEMA",
    "build_hamburg_aerial_combined_candidate",
    "fit_movement_shape_to_anchors",
    "movement_surface_polygon",
    "reanchor_movement_shape",
]
