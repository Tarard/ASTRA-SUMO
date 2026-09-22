from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from typing import Any
import json
import xml.etree.ElementTree as ET

from torii_sumo.core.cached_detector_demand import (
    materialize_hamburg_corridor_candidate_map_bindings,
    materialize_hamburg_corridor_candidate_signal_bindings,
    prepare_cached_detector_demand_package,
    prepare_hamburg_corridor_candidate_package,
    prepare_corridor_candidate_detector_demand_package,
)
from torii_sumo.core.artifact_io import write_json_atomic
from torii_sumo.core.digital_twin_workflow import prepare_hamburg_corridor_digital_twin
from torii_sumo.core.hamburg_compound_plainxml import (
    materialize_hamburg_2394_compound_geometry_first_pass,
)
from torii_sumo.core.hamburg_2394_tls_materializer import (
    materialize_hamburg_2394_tls_topology_candidate,
)
from torii_sumo.core.hamburg_corridor_tls_materializer import (
    materialize_hamburg_sandtorkai_corridor_tls_candidate,
)
from torii_sumo.core.hamburg_corridor_geometry_materializer import (
    materialize_hamburg_sandtorkai_geometry_safe_candidate,
)
from torii_sumo.core.hamburg_mainline_scope_materializer import (
    materialize_hamburg_sandtorkai_mainline_scope_candidate,
)
from torii_sumo.core.hamburg_named_count_scope import (
    load_lsa_node_references,
    materialize_hamburg_named_count_scope,
)
from torii_sumo.core.hamburg_named_detector_bindings import (
    materialize_hamburg_named_detector_bindings,
)
from torii_sumo.core.hamburg_named_replay import materialize_hamburg_named_replay
from torii_sumo.core.hamburg_named_signal_observations import (
    materialize_hamburg_named_signal_observations,
)
from torii_sumo.core.digital_twin import parse_iso_datetime
from torii_sumo.core.hamburg_execution_workflow import (
    materialize_hamburg_execution_plan,
)
from torii_sumo.core.hamburg_official import SensorThingsClient
from torii_sumo.core.hamburg_corridor_workflow import (
    prepare_hamburg_sandtorkai_geometry_safe_corridor_package,
)
from torii_sumo.core.hamburg_official import SANDTORKAI_THREE_INTERSECTIONS
from torii_sumo.core.official_tls_workflow import rebuild_hamburg_sandtorkai_official_tls
from torii_sumo.core.route_sampler import run_route_sampler
from torii_sumo.core.surface_overlap_audit import (
    audit_sumo_lane_junction_surface_overlaps,
    compare_sumo_surface_overlap_reports,
)
from torii_sumo.core.tls_replay import run_tls_detector_replay
from torii_sumo.intersection.composable_archetype import build_hamburg_2394_archetype_profile

from .osm_tools import sumo_osm_cleanup_workflow


def sumo_hamburg_2394_archetype_classify(
    map_file: str,
    ocit_file: str,
    source_net_file: str,
    output_file: str | None = None,
) -> dict[str, Any]:
    """Classify Hamburg 2394 before any channelization or TLS reconstruction.

    The result separates the finite composable archetype from the proposed SUMO
    owner layout, and proves the logical controller from the supplied official
    OCIT file. Its local join groups are review-only and never mutate the source
    network.
    """

    try:
        map_path = _existing_file(map_file, "map_file")
        ocit_path = _existing_file(ocit_file, "ocit_file")
        net_path = _existing_file(source_net_file, "source_net_file")
        report = build_hamburg_2394_archetype_profile(
            map_file=map_path,
            ocit_file=ocit_path,
            source_net_file=net_path,
        )
        if output_file:
            artifact_path = Path(output_file).resolve()
            if artifact_path in {
                map_path.resolve(),
                ocit_path.resolve(),
                net_path.resolve(),
            }:
                raise ValueError("output_file must not overwrite a source evidence file")
            write_json_atomic(artifact_path, report, sort_keys=True)
            return {**report, "artifact_file": str(artifact_path)}
        return report
    except (OSError, ValueError, ET.ParseError) as exc:
        return {
            "status": "fail",
            "claim_status": "classification-invalid",
            "automatic_promotion_gate": "blocked",
            "error": str(exc),
        }


def sumo_hamburg_2394_compound_geometry_first_pass(
    source_net_file: str,
    classification_file: str,
    accepted_classification_id: str,
    expected_source_sha256: str,
    expected_classification_sha256: str,
    output_dir: str,
    netconvert_binary: str = "netconvert",
    timeout_seconds: float = 240.0,
) -> dict[str, Any]:
    """Materialize only the accepted, hash-bound 2394 compound geometry pass.

    The classification artifact and frozen source network remain explicit
    evidence inputs.  The wrapped core rejects a missing or mismatched accepted
    classification id, either SHA-256, and any deviation from the exact 2394
    join/owner contract.  This stage deliberately does not restore official TLS
    programs and never opens the automatic-promotion gate.
    """

    try:
        if not output_dir.strip():
            raise ValueError("output_dir is required")
        return materialize_hamburg_2394_compound_geometry_first_pass(
            source_net_file=_existing_file(source_net_file, "source_net_file"),
            classification_report=_existing_file(
                classification_file,
                "classification_file",
            ),
            accepted_classification_id=accepted_classification_id,
            expected_source_sha256=expected_source_sha256,
            expected_classification_sha256=expected_classification_sha256,
            output_dir=Path(output_dir),
            netconvert_binary=netconvert_binary,
            timeout_seconds=timeout_seconds,
        )
    except (OSError, ValueError, ET.ParseError) as exc:
        return {
            "status": "fail",
            "claim_status": "construction-invalid",
            "automatic_promotion_gate": "blocked",
            "official_tls_restoration": "not_run",
            "error": str(exc),
        }


def sumo_hamburg_2394_tls_topology_materialize(
    source_net_file: str,
    map_file: str,
    ocit_file: str,
    classification_file: str,
    accepted_classification_id: str,
    expected_source_sha256: str,
    expected_map_sha256: str,
    expected_ocit_sha256: str,
    expected_classification_sha256: str,
    plain_source_dir: str,
    output_dir: str,
    netconvert_binary: str = "netconvert",
    sumo_binary: str = "sumo",
    timeout_seconds: float = 240.0,
) -> dict[str, Any]:
    """Materialize the hash-bound 2394 static topology review candidate.

    This applies only the already accepted channelization patch and installs
    one shared all-red ``HH_2394`` placeholder across the three official
    signal-bearing owners.  Historical Saturday timing remains blocked until
    a matching observation replay is available.
    """

    try:
        if not plain_source_dir.strip() or not output_dir.strip():
            raise ValueError("plain_source_dir and output_dir are required")
        return materialize_hamburg_2394_tls_topology_candidate(
            source_net_file=_existing_file(source_net_file, "source_net_file"),
            map_file=_existing_file(map_file, "map_file"),
            ocit_file=_existing_file(ocit_file, "ocit_file"),
            classification_file=_existing_file(classification_file, "classification_file"),
            accepted_classification_id=accepted_classification_id,
            expected_source_sha256=expected_source_sha256,
            expected_map_sha256=expected_map_sha256,
            expected_ocit_sha256=expected_ocit_sha256,
            expected_classification_sha256=expected_classification_sha256,
            plain_source_dir=_existing_dir(plain_source_dir, "plain_source_dir"),
            output_dir=_output_dir(output_dir),
            netconvert_binary=netconvert_binary,
            sumo_binary=sumo_binary,
            timeout_seconds=timeout_seconds,
        )
    except (OSError, ValueError, ET.ParseError) as exc:
        return {
            "status": "fail",
            "claim_status": "construction-invalid",
            "automatic_promotion_gate": "blocked",
            "historical_two_hour_replay": "not_run",
            "error": str(exc),
        }


def sumo_hamburg_sandtorkai_corridor_tls_materialize(
    source_net_file: str,
    movement_paths_file: str,
    movement_endpoints_file: str,
    expected_source_sha256: str,
    expected_movement_paths_sha256: str,
    expected_movement_endpoints_sha256: str,
    road_sumo_binding_file: str,
    expected_road_sumo_binding_sha256: str,
    output_dir: str,
    netconvert_binary: str = "netconvert",
    sumo_binary: str = "sumo",
    timeout_seconds: float = 240.0,
    selection_radius_m: float = 80.0,
) -> dict[str, Any]:
    """Materialize all three official shared controllers without geometry warping.

    The official MAP/OCIT-derived movement paths choose real source-network
    transitions, including explicit additions for the four missing lane
    connections.  The returned artifact remains static/all-red until the
    separate historical TLD replay is run.
    """

    try:
        if not output_dir.strip():
            raise ValueError("output_dir is required")
        return materialize_hamburg_sandtorkai_corridor_tls_candidate(
            source_net_file=_existing_file(source_net_file, "source_net_file"),
            movement_paths_file=_existing_file(movement_paths_file, "movement_paths_file"),
            movement_endpoints_file=_existing_file(
                movement_endpoints_file,
                "movement_endpoints_file",
            ),
            expected_source_sha256=expected_source_sha256,
            expected_movement_paths_sha256=expected_movement_paths_sha256,
            expected_movement_endpoints_sha256=expected_movement_endpoints_sha256,
            road_sumo_binding_file=_existing_file(
                road_sumo_binding_file,
                "road_sumo_binding_file",
            ),
            expected_road_sumo_binding_sha256=expected_road_sumo_binding_sha256,
            output_dir=_output_dir(output_dir),
            netconvert_binary=netconvert_binary,
            sumo_binary=sumo_binary,
            timeout_seconds=timeout_seconds,
            selection_radius_m=selection_radius_m,
        )
    except (OSError, ValueError, ET.ParseError) as exc:
        return {
            "status": "fail",
            "claim_status": "construction-invalid",
            "automatic_promotion_gate": "blocked",
            "historical_two_hour_replay": "not_run",
            "error": str(exc),
        }


def sumo_hamburg_sandtorkai_corridor_geometry_materialize(
    source_net_file: str,
    expected_source_sha256: str,
    output_dir: str,
    profile: str = "hamburg_sandtorkai_geometry_safe_v1",
    netconvert_binary: str = "netconvert",
    sumo_binary: str = "sumo",
    timeout_seconds: float = 240.0,
) -> dict[str, Any]:
    """Apply the review-gated topology-aware geometry profile before TLS binding.

    The profile joins only the confirmed sub-groups, protects the official
    0228 branch pair, and trims three inherited oversized junction faces.  It
    never overwrites the source network and does not install signal timing.
    """

    try:
        if not output_dir.strip():
            raise ValueError("output_dir is required")
        return materialize_hamburg_sandtorkai_geometry_safe_candidate(
            source_net_file=_existing_file(source_net_file, "source_net_file"),
            expected_source_sha256=expected_source_sha256,
            output_dir=_output_dir(output_dir),
            profile=profile,
            netconvert_binary=netconvert_binary,
            sumo_binary=sumo_binary,
            timeout_seconds=timeout_seconds,
        )
    except (OSError, ValueError, ET.ParseError, json.JSONDecodeError) as exc:
        return {
            "status": "fail",
            "claim_status": "construction-invalid",
            "automatic_promotion_gate": "blocked",
            "error": str(exc),
        }


def sumo_hamburg_sandtorkai_mainline_scope_materialize(
    source_net_file: str,
    expected_source_sha256: str,
    output_dir: str,
    profile: str = "hamburg_sandtorkai_mainline_scope_v1",
    include_short_approaches: bool = True,
    netconvert_binary: str = "netconvert",
    sumo_binary: str = "sumo",
    timeout_seconds: float = 240.0,
) -> dict[str, Any]:
    """Keep the bounded Am Sandtorkai corridor and its entry approaches.

    ``hamburg_sandtorkai_mainline_scope_v1`` keeps the mainline and the two
    signal approaches used by the original compact candidate.  The
    ``hamburg_sandtorkai_entry_scope_v1`` profile additionally retains Großer
    Grasbrook and Singapurstraße so demand can enter the corridor.  Both
    profiles are hash-bound, never overwrite the source, remove the oversized
    Baumwall branch, and leave a review candidate with all three controllers.
    """

    try:
        if not output_dir.strip():
            raise ValueError("output_dir is required")
        return materialize_hamburg_sandtorkai_mainline_scope_candidate(
            source_net_file=_existing_file(source_net_file, "source_net_file"),
            expected_source_sha256=expected_source_sha256,
            output_dir=_output_dir(output_dir),
            profile=profile,
            include_short_approaches=include_short_approaches,
            netconvert_binary=netconvert_binary,
            sumo_binary=sumo_binary,
            timeout_seconds=timeout_seconds,
        )
    except (OSError, ValueError, ET.ParseError, json.JSONDecodeError) as exc:
        return {
            "status": "fail",
            "claim_status": "construction-invalid",
            "automatic_promotion_gate": "blocked",
            "error": str(exc),
        }


def sumo_network_surface_overlap_audit(
    net_file: str,
    report_file: str | None = None,
    minimum_overlap_area_m2: float = 0.01,
    default_lane_width_m: float = 3.2,
) -> dict[str, Any]:
    """Audit junction polygons and external-lane faces without mutating the net.

    This complements SUMO's edge-overlap warning with exact polygon-area checks
    for junction-to-junction overlap and reconstructed external lane faces
    entering non-owner junctions.  Expected owner endpoint contact, internal
    lanes, and internal junctions are explicitly excluded.
    """

    try:
        source = _existing_file(net_file, "net_file")
        destination = Path(report_file) if report_file else None
        return audit_sumo_lane_junction_surface_overlaps(
            source,
            minimum_overlap_area_m2=minimum_overlap_area_m2,
            default_lane_width_m=default_lane_width_m,
            report_file=destination,
        )
    except (OSError, ValueError) as exc:
        return {
            "status": "fail",
            "claim_status": "surface-overlap-audit-invalid",
            "source_network_mutation": False,
            "error": str(exc),
        }


def sumo_network_surface_overlap_comparison(
    baseline_net_file: str,
    candidate_net_file: str,
    focus_junction_ids: list[str],
    output_dir: str,
    minimum_overlap_area_m2: float = 0.01,
    default_lane_width_m: float = 3.2,
) -> dict[str, Any]:
    """Compare a bounded rebuild with a baseline while retaining global defects.

    A bounded comparison can pass only with zero introduced findings and zero
    candidate findings touching ``focus_junction_ids``.  The baseline and
    candidate audits retain their independent global status, so inherited
    out-of-scope overlap is never converted into a global-clean claim.
    """

    try:
        baseline_path = _existing_file(baseline_net_file, "baseline_net_file").resolve()
        candidate_path = _existing_file(candidate_net_file, "candidate_net_file").resolve()
        if baseline_path == candidate_path:
            raise ValueError("baseline_net_file and candidate_net_file must be distinct")
        destination = _output_dir(output_dir).resolve()
        baseline_report_file = destination / "baseline_surface_overlap_audit.json"
        candidate_report_file = destination / "candidate_surface_overlap_audit.json"
        comparison_report_file = destination / "bounded_surface_overlap_comparison.json"
        source_paths = {baseline_path, candidate_path}
        if {
            baseline_report_file.resolve(),
            candidate_report_file.resolve(),
            comparison_report_file.resolve(),
        } & source_paths:
            raise ValueError("output_dir report paths must not overwrite either source network")

        baseline = audit_sumo_lane_junction_surface_overlaps(
            baseline_path,
            minimum_overlap_area_m2=minimum_overlap_area_m2,
            default_lane_width_m=default_lane_width_m,
            report_file=baseline_report_file,
        )
        candidate = audit_sumo_lane_junction_surface_overlaps(
            candidate_path,
            minimum_overlap_area_m2=minimum_overlap_area_m2,
            default_lane_width_m=default_lane_width_m,
            report_file=candidate_report_file,
        )
        comparison = compare_sumo_surface_overlap_reports(
            baseline,
            candidate,
            focus_junction_ids=focus_junction_ids,
            report_file=comparison_report_file,
        )
        return {
            "status": comparison["status"],
            "claim_status": comparison["claim_status"],
            "baseline_audit": baseline,
            "candidate_audit": candidate,
            "comparison": comparison,
        }
    except (OSError, ValueError) as exc:
        return {
            "status": "fail",
            "claim_status": "bounded-surface-overlap-comparison-invalid",
            "source_network_mutation": False,
            "error": str(exc),
        }


def sumo_hamburg_official_tls_rebuild(
    source_net_file: str,
    signal_asset_dir: str,
    output_dir: str,
    netconvert_binary: str = "netconvert",
    sumo_binary: str = "sumo",
    timeout_seconds: float = 240.0,
) -> dict[str, Any]:
    """Rebuild Sandtorkai TLS only after cached TLD groups cover the full OCIT inventory."""

    try:
        return rebuild_hamburg_sandtorkai_official_tls(
            source_net_file=_existing_file(source_net_file, "source_net_file"),
            signal_asset_dir=_existing_dir(signal_asset_dir, "signal_asset_dir"),
            output_dir=_output_dir(output_dir),
            netconvert_binary=netconvert_binary,
            sumo_binary=sumo_binary,
            timeout_seconds=timeout_seconds,
        )
    except (OSError, ValueError) as exc:
        return {
            "status": "fail",
            "claim_status": "construction-invalid",
            "error": str(exc),
        }


def sumo_hamburg_cached_detector_demand(
    official_tls_manifest: str,
    count_stream_snapshot: str,
    canonical_count_file: str,
    output_dir: str,
    prefix: str = "hamburg_cached_detector_demand",
    simulation_begin: int = 0,
    simulation_end: int = 9000,
    comparison_begin: int = 1800,
    comparison_end: int = 9000,
    interval: int = 900,
    excluded_route_edges: list[str] | None = None,
    route_sampler_optimize: str | None = "full",
    route_sampler_script: str | None = None,
    timeout_seconds: float = 300.0,
    allow_detector_cross_section_boundaries: bool = False,
) -> dict[str, Any]:
    """Resume detector-demand construction from hash-pinned official evidence.

    Unlike the discovery workflow, this path does not download OSM, rebuild the
    network, or repeat a nearest-lane assignment.  It consumes the exact
    MAP-to-SUMO binding contract recorded by the official TLS rebuild manifest.
    """

    try:
        return prepare_cached_detector_demand_package(
            official_tls_manifest=_existing_file(official_tls_manifest, "official_tls_manifest"),
            count_stream_snapshot=_existing_file(count_stream_snapshot, "count_stream_snapshot"),
            canonical_count_file=_existing_file(canonical_count_file, "canonical_count_file"),
            output_dir=_output_dir(output_dir),
            prefix=prefix,
            simulation_begin=simulation_begin,
            simulation_end=simulation_end,
            comparison_begin=comparison_begin,
            comparison_end=comparison_end,
            interval=interval,
            excluded_route_edges=excluded_route_edges or (),
            route_sampler_optimize=route_sampler_optimize,
            route_sampler_script=Path(route_sampler_script) if route_sampler_script else None,
            timeout_seconds=timeout_seconds,
            allow_detector_cross_section_boundaries=allow_detector_cross_section_boundaries,
        )
    except (OSError, ValueError) as exc:
        return {
            "status": "fail",
            "claim_status": "construction-invalid",
            "error": str(exc),
        }


def sumo_hamburg_corridor_candidate_map_bindings(
    candidate_manifest: str,
    candidate_net_file: str,
    expected_candidate_net_sha256: str,
    map_xml_files: list[str],
    output_dir: str,
) -> dict[str, Any]:
    """Recompute the official MAP-to-SUMO lane contract on a corridor candidate."""

    try:
        return materialize_hamburg_corridor_candidate_map_bindings(
            candidate_manifest=_existing_file(candidate_manifest, "candidate_manifest"),
            candidate_net_file=_existing_file(candidate_net_file, "candidate_net_file"),
            expected_candidate_net_sha256=expected_candidate_net_sha256,
            map_xml_files=[_existing_file(path, "map_xml_file") for path in map_xml_files],
            output_dir=_output_dir(output_dir),
        )
    except (OSError, ValueError, json.JSONDecodeError, ET.ParseError) as exc:
        return {
            "status": "fail",
            "claim_status": "construction-invalid",
            "error": str(exc),
        }


def sumo_hamburg_corridor_candidate_signal_bindings(
    candidate_manifest: str,
    candidate_net_file: str,
    expected_candidate_net_sha256: str,
    map_lane_binding_file: str,
    expected_map_lane_binding_sha256: str,
    signal_stream_file: str,
    output_dir: str,
    movement_endpoints_file: str | None = None,
    expected_movement_endpoints_sha256: str | None = None,
) -> dict[str, Any]:
    """Bind frozen primary-signal metadata to the candidate controller links."""

    try:
        return materialize_hamburg_corridor_candidate_signal_bindings(
            candidate_manifest=_existing_file(candidate_manifest, "candidate_manifest"),
            candidate_net_file=_existing_file(candidate_net_file, "candidate_net_file"),
            expected_candidate_net_sha256=expected_candidate_net_sha256,
            map_lane_binding_file=_existing_file(map_lane_binding_file, "map_lane_binding_file"),
            expected_map_lane_binding_sha256=expected_map_lane_binding_sha256,
            signal_stream_file=_existing_file(signal_stream_file, "signal_stream_file"),
            movement_endpoints_file=(
                _existing_file(movement_endpoints_file, "movement_endpoints_file")
                if movement_endpoints_file
                else None
            ),
            expected_movement_endpoints_sha256=expected_movement_endpoints_sha256,
            output_dir=_output_dir(output_dir),
        )
    except (OSError, ValueError, json.JSONDecodeError, ET.ParseError) as exc:
        return {
            "status": "fail",
            "claim_status": "construction-invalid",
            "error": str(exc),
        }


def sumo_hamburg_sandtorkai_corridor_candidate_package(
    candidate_manifest: str,
    candidate_net_file: str,
    expected_candidate_net_sha256: str,
    map_xml_files: list[str],
    signal_stream_file: str,
    count_stream_snapshot: str,
    canonical_count_file: str,
    output_dir: str,
    movement_endpoints_file: str | None = None,
    expected_movement_endpoints_sha256: str | None = None,
    prefix: str = "hamburg_sandtorkai_corridor_candidate",
    simulation_begin: int = 0,
    simulation_end: int = 7200,
    comparison_begin: int = 1800,
    comparison_end: int = 7200,
    interval: int = 900,
    excluded_route_edges: list[str] | None = None,
    route_sampler_optimize: str | None = "full",
    route_sampler_script: str | None = None,
    timeout_seconds: float = 300.0,
    allow_detector_cross_section_boundaries: bool = False,
) -> dict[str, Any]:
    """Run candidate MAP, signal, and detector-demand stages as one package."""

    try:
        return prepare_hamburg_corridor_candidate_package(
            candidate_manifest=_existing_file(candidate_manifest, "candidate_manifest"),
            candidate_net_file=_existing_file(candidate_net_file, "candidate_net_file"),
            expected_candidate_net_sha256=expected_candidate_net_sha256,
            map_xml_files=[_existing_file(path, "map_xml_file") for path in map_xml_files],
            signal_stream_file=_existing_file(signal_stream_file, "signal_stream_file"),
            movement_endpoints_file=(
                _existing_file(movement_endpoints_file, "movement_endpoints_file")
                if movement_endpoints_file
                else None
            ),
            expected_movement_endpoints_sha256=expected_movement_endpoints_sha256,
            count_stream_snapshot=_existing_file(count_stream_snapshot, "count_stream_snapshot"),
            canonical_count_file=_existing_file(canonical_count_file, "canonical_count_file"),
            output_dir=_output_dir(output_dir),
            prefix=prefix,
            simulation_begin=simulation_begin,
            simulation_end=simulation_end,
            comparison_begin=comparison_begin,
            comparison_end=comparison_end,
            interval=interval,
            excluded_route_edges=excluded_route_edges or (),
            route_sampler_optimize=route_sampler_optimize,
            route_sampler_script=Path(route_sampler_script) if route_sampler_script else None,
            timeout_seconds=timeout_seconds,
            allow_detector_cross_section_boundaries=allow_detector_cross_section_boundaries,
        )
    except (OSError, ValueError, json.JSONDecodeError, ET.ParseError) as exc:
        return {
            "status": "fail",
            "claim_status": "construction-invalid",
            "error": str(exc),
        }


def sumo_hamburg_sandtorkai_geometry_safe_digital_twin(
    source_net_file: str,
    expected_source_sha256: str,
    movement_paths_file: str,
    movement_endpoints_file: str,
    expected_movement_paths_sha256: str,
    expected_movement_endpoints_sha256: str,
    map_xml_files: list[str],
    signal_stream_file: str,
    count_stream_snapshot: str,
    canonical_count_file: str,
    output_dir: str,
    road_sumo_binding_file: str | None = None,
    expected_road_sumo_binding_sha256: str | None = None,
    prefix: str = "hamburg_sandtorkai_geometry_safe",
    simulation_begin: int = 0,
    simulation_end: int = 7200,
    comparison_begin: int = 1800,
    comparison_end: int = 7200,
    interval: int = 900,
    excluded_route_edges: list[str] | None = None,
    route_sampler_optimize: str | None = "full",
    route_sampler_script: str | None = None,
    netconvert_binary: str = "netconvert",
    sumo_binary: str = "sumo",
    timeout_seconds: float = 300.0,
    allow_detector_cross_section_boundaries: bool = False,
) -> dict[str, Any]:
    """Run the staged geometry-safe corridor twin workflow.

    Without the optional binding pair the workflow materializes only its
    geometry review candidate and returns a blocked hand-off.  The caller must
    then bind road arms to that exact candidate SUMO snapshot before a later
    invocation is allowed to create lane connections or controllers.
    """

    try:
        if bool(road_sumo_binding_file) != bool(expected_road_sumo_binding_sha256):
            raise ValueError(
                "road_sumo_binding_file and expected_road_sumo_binding_sha256 must be supplied together"
            )
        return prepare_hamburg_sandtorkai_geometry_safe_corridor_package(
            source_net_file=_existing_file(source_net_file, "source_net_file"),
            expected_source_sha256=expected_source_sha256,
            movement_paths_file=_existing_file(movement_paths_file, "movement_paths_file"),
            movement_endpoints_file=_existing_file(movement_endpoints_file, "movement_endpoints_file"),
            expected_movement_paths_sha256=expected_movement_paths_sha256,
            expected_movement_endpoints_sha256=expected_movement_endpoints_sha256,
            map_xml_files=[_existing_file(path, "map_xml_file") for path in map_xml_files],
            signal_stream_file=_existing_file(signal_stream_file, "signal_stream_file"),
            count_stream_snapshot=_existing_file(count_stream_snapshot, "count_stream_snapshot"),
            canonical_count_file=_existing_file(canonical_count_file, "canonical_count_file"),
            output_dir=_output_dir(output_dir),
            road_sumo_binding_file=(
                _existing_file(road_sumo_binding_file, "road_sumo_binding_file")
                if road_sumo_binding_file
                else None
            ),
            expected_road_sumo_binding_sha256=expected_road_sumo_binding_sha256,
            prefix=prefix,
            simulation_begin=simulation_begin,
            simulation_end=simulation_end,
            comparison_begin=comparison_begin,
            comparison_end=comparison_end,
            interval=interval,
            excluded_route_edges=excluded_route_edges or (),
            route_sampler_optimize=route_sampler_optimize,
            route_sampler_script=Path(route_sampler_script) if route_sampler_script else None,
            netconvert_binary=netconvert_binary,
            sumo_binary=sumo_binary,
            timeout_seconds=timeout_seconds,
            allow_detector_cross_section_boundaries=allow_detector_cross_section_boundaries,
        )
    except (OSError, ValueError, json.JSONDecodeError, ET.ParseError) as exc:
        return {
            "status": "fail",
            "claim_status": "construction-invalid",
            "automatic_promotion_gate": "blocked",
            "error": str(exc),
        }


def sumo_hamburg_corridor_candidate_detector_demand(
    candidate_manifest: str,
    candidate_net_file: str,
    expected_candidate_net_sha256: str,
    map_xml_files: list[str],
    map_lane_binding_file: str,
    expected_map_lane_binding_sha256: str,
    count_stream_snapshot: str,
    canonical_count_file: str,
    output_dir: str,
    prefix: str = "hamburg_sandtorkai_corridor_candidate",
    simulation_begin: int = 0,
    simulation_end: int = 9000,
    comparison_begin: int = 1800,
    comparison_end: int = 9000,
    interval: int = 900,
    excluded_route_edges: list[str] | None = None,
    route_sampler_optimize: str | None = "full",
    route_sampler_script: str | None = None,
    timeout_seconds: float = 300.0,
    allow_detector_cross_section_boundaries: bool = False,
) -> dict[str, Any]:
    """Generate sensors and detector-constrained routes for a blocked TLS candidate.

    This is the review-stage counterpart to ``sumo_hamburg_cached_detector_demand``:
    it accepts the corridor materializer's hash-bound candidate, never upgrades
    its topology claim, and keeps all official MAP/count evidence immutable.
    """

    try:
        return prepare_corridor_candidate_detector_demand_package(
            candidate_manifest=_existing_file(candidate_manifest, "candidate_manifest"),
            candidate_net_file=_existing_file(candidate_net_file, "candidate_net_file"),
            expected_candidate_net_sha256=expected_candidate_net_sha256,
            map_xml_files=[_existing_file(path, "map_xml_file") for path in map_xml_files],
            map_lane_binding_file=_existing_file(map_lane_binding_file, "map_lane_binding_file"),
            expected_map_lane_binding_sha256=expected_map_lane_binding_sha256,
            count_stream_snapshot=_existing_file(count_stream_snapshot, "count_stream_snapshot"),
            canonical_count_file=_existing_file(canonical_count_file, "canonical_count_file"),
            output_dir=_output_dir(output_dir),
            prefix=prefix,
            simulation_begin=simulation_begin,
            simulation_end=simulation_end,
            comparison_begin=comparison_begin,
            comparison_end=comparison_end,
            interval=interval,
            excluded_route_edges=excluded_route_edges or (),
            route_sampler_optimize=route_sampler_optimize,
            route_sampler_script=Path(route_sampler_script) if route_sampler_script else None,
            timeout_seconds=timeout_seconds,
            allow_detector_cross_section_boundaries=allow_detector_cross_section_boundaries,
        )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return {
            "status": "fail",
            "claim_status": "construction-invalid",
            "error": str(exc),
        }


def sumo_hamburg_sandtorkai_digital_twin(
    output_dir: str,
    net_file: str | None = None,
    saturday_date: str | None = None,
    source_osm_path: str | None = None,
    prefix: str = "hamburg_sandtorkai_twin",
    route_sampler_script: str | None = None,
    run_route_sampler_step: bool = True,
    timeout_seconds: float = 300.0,
    warmup_seconds: int = 1800,
    compact_corridor_scope: bool = True,
    corridor_buffer_m: float = 25.0,
    intersection_stub_radius_m: float = 80.0,
    max_scope_bridge_length_m: float = 300.0,
    netconvert_binary: str = "netconvert",
    sumo_binary: str = "sumo",
) -> dict[str, Any]:
    """Build the reusable three-intersection Sandtorkai digital-twin input package.

    When ``net_file`` is omitted, Torii first downloads/reuses OSM for the fixed
    corridor bbox and runs its cleanup gates.  Supplying an already frozen Torii
    network makes the official-data package deterministic and faster to rebuild.
    """

    try:
        out_dir = _output_dir(output_dir)
        network_path: Path
        network_report: dict[str, Any]
        if net_file:
            network_path = _existing_file(net_file, "net_file")
            network_report = {
                "status": "reused",
                "claim_status": "caller-supplied-frozen-network",
                "net_file": str(network_path),
            }
        else:
            network_dir = out_dir / "network"
            network_report = sumo_osm_cleanup_workflow(
                output_dir=str(network_dir),
                bbox=SANDTORKAI_THREE_INTERSECTIONS.bbox,
                profile="standard",
                source_osm_path=source_osm_path,
                traffic_layers="passenger,bicycle,pedestrian,bus",
                timeout_seconds=timeout_seconds,
            )
            network_gate = _torii_network_gate(network_report)
            if network_gate["status"] == "blocked":
                return {
                    "status": "blocked",
                    "claim_status": "construction-incomplete",
                    "stage": "torii_osm_cleanup",
                    "network": network_report,
                    "network_gate": network_gate,
                }
            network_path = _existing_file(str(network_gate["net_file"]), "Torii cleanup net_file")

        parsed_date = None
        if saturday_date:
            parsed_date = date.fromisoformat(saturday_date)
            if parsed_date.weekday() != 5:
                raise ValueError(f"saturday_date is not a Saturday: {saturday_date}")
        report = prepare_hamburg_corridor_digital_twin(
            net_file=network_path,
            output_dir=out_dir / "twin",
            preset=SANDTORKAI_THREE_INTERSECTIONS,
            saturday_date=parsed_date,
            route_sampler_script=Path(route_sampler_script) if route_sampler_script else None,
            run_route_sampler_step=run_route_sampler_step,
            timeout_seconds=timeout_seconds,
            warmup_seconds=warmup_seconds,
            compact_corridor_scope=compact_corridor_scope,
            corridor_buffer_m=corridor_buffer_m,
            intersection_stub_radius_m=intersection_stub_radius_m,
            max_scope_bridge_length_m=max_scope_bridge_length_m,
            netconvert_binary=netconvert_binary,
            sumo_binary=sumo_binary,
        )
        report["network"] = network_report
        if not net_file:
            report["network_gate"] = network_gate
            if network_gate["status"] == "provisional":
                gaps = list(report.get("gaps", []))
                gaps.append(
                    "Torii structural build is usable, but its map-review gates remain provisional; "
                    "the Hamburg MAP/OCIT bindings are recorded separately and do not silently waive those gates"
                )
                report["gaps"] = gaps
                if report.get("status") == "pass":
                    report["status"] = "partial"
                    report["claim_status"] = "official-data-package-on-provisional-torii-network"
        report["workflow"] = (
            "Torii OSM cleanup -> compact three-intersection scope -> Hamburg official "
            "counts/signals -> SUMO replay inputs"
        )
        return report
    except (OSError, ValueError) as exc:
        return {
            "status": "fail",
            "claim_status": "construction-invalid",
            "error": str(exc),
        }


def sumo_detector_route_sampler_calibrate(
    candidate_manifest_csv: str,
    edge_data_file: str,
    output_dir: str,
    prefix: str = "detector_demand",
    begin: int = 0,
    end: int = 7200,
    interval: int = 900,
    seed: int = 42,
    optimize: str | None = None,
    route_sampler_script: str | None = None,
    timeout_seconds: float = 300.0,
) -> dict[str, Any]:
    try:
        return run_route_sampler(
            candidate_manifest_csv=_existing_file(candidate_manifest_csv, "candidate_manifest_csv"),
            edge_data_file=_existing_file(edge_data_file, "edge_data_file"),
            output_dir=_output_dir(output_dir),
            prefix=prefix,
            begin=begin,
            end=end,
            interval=interval,
            seed=seed,
            optimize=optimize,
            route_sampler_script=Path(route_sampler_script) if route_sampler_script else None,
            timeout_seconds=timeout_seconds,
        )
    except (OSError, ValueError) as exc:
        return {
            "status": "fail",
            "claim_status": "construction-invalid",
            "error": str(exc),
        }


def sumo_hamburg_named_count_scope(
    lsa_identity_file: str,
    count_node_ids: list[str],
    scope_id: str,
    output_dir: str,
    saturday_date: str | None = None,
    max_saturdays_to_try: int = 8,
    warmup_seconds: int = 1800,
    formal_duration_seconds: int = 7200,
    source_bin_seconds: int = 300,
    output_bin_seconds: int = 900,
    max_distance_m: float = 250.0,
    api_base: str = "https://iot.hamburg.de/v1.1/",
    timeout_seconds: float = 60.0,
) -> dict[str, Any]:
    """Fetch a declared official Hamburg count scope for the named corridor.

    This is the W3a hand-off. It records missing detectors and never infers a
    lane binding or silently substitutes a nearby node.
    """

    try:
        if not count_node_ids:
            raise ValueError("count_node_ids must contain at least one node")
        references = load_lsa_node_references(
            _existing_file(lsa_identity_file, "lsa_identity_file"),
            expected_node_ids=count_node_ids,
        )
        return materialize_hamburg_named_count_scope(
            output_dir=_output_dir(output_dir),
            client=SensorThingsClient(api_base, timeout_seconds=timeout_seconds),
            signal_nodes=references,
            requested_count_node_ids=count_node_ids,
            scope_id=scope_id,
            saturday_date=date.fromisoformat(saturday_date) if saturday_date else None,
            max_saturdays_to_try=max_saturdays_to_try,
            warmup_seconds=warmup_seconds,
            formal_duration_seconds=formal_duration_seconds,
            source_bin_seconds=source_bin_seconds,
            output_bin_seconds=output_bin_seconds,
            max_distance_m=max_distance_m,
        )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return {
            "status": "fail",
            "claim_status": "count-scope-invalid",
            "automatic_promotion_gate": "blocked",
            "error": str(exc),
        }


def sumo_hamburg_named_detector_bindings(
    net_file: str,
    count_stream_file: str,
    output_dir: str,
    network_projection: str = "EPSG:25832",
    period: int = 900,
    max_distance_m: float = 5.0,
    ambiguity_margin_m: float = 1.0,
    map_files: list[str] | None = None,
    movement_evidence_file: str | None = None,
) -> dict[str, Any]:
    """Bind a frozen Hamburg count inventory to nearby lanes in one candidate network."""

    try:
        return materialize_hamburg_named_detector_bindings(
            net_file=_existing_file(net_file, "net_file"),
            count_stream_file=_existing_file(count_stream_file, "count_stream_file"),
            output_dir=_output_dir(output_dir),
            network_projection=network_projection,
            period=period,
            max_distance_m=max_distance_m,
            ambiguity_margin_m=ambiguity_margin_m,
            map_files=tuple(_existing_file(path, "map_file") for path in map_files or []),
            movement_evidence_file=(
                _existing_file(movement_evidence_file, "movement_evidence_file")
                if movement_evidence_file
                else None
            ),
        )
    except (OSError, ValueError, json.JSONDecodeError, ET.ParseError) as exc:
        return {
            "status": "fail",
            "claim_status": "detector-binding-invalid",
            "automatic_promotion_gate": "blocked",
            "error": str(exc),
        }


def sumo_hamburg_sandtorkai_execution_plan(
    output_dir: str,
    stage_manifests: list[str] | None = None,
    stage_feedback: list[str] | None = None,
    resume: bool = True,
) -> dict[str, Any]:
    """Record and resume the named-corridor execution plan.

    Each entry in ``stage_manifests`` uses ``W0=/absolute/path/manifest.json``
    syntax; W3a count acquisition and W3b detector binding are separate.
    Optional entries in ``stage_feedback`` use the same syntax and attach
    bounded diagnostic evidence without changing a stage gate. The W5
    capability summary is derived automatically. The operation writes only the plan ledger.
    """

    try:
        mapping: dict[str, Path] = {}
        for entry in stage_manifests or []:
            stage, separator, path = str(entry).partition("=")
            if not separator or not stage.strip() or not path.strip():
                raise ValueError("stage_manifests entries must use STAGE=PATH")
            stage_id = stage.strip().upper()
            if stage_id in mapping:
                raise ValueError(f"duplicate stage manifest: {stage_id}")
            mapping[stage_id] = _existing_file(path.strip(), f"{stage_id} manifest")
        feedback_mapping: dict[str, Path] = {}
        for entry in stage_feedback or []:
            stage, separator, path = str(entry).partition("=")
            if not separator or not stage.strip() or not path.strip():
                raise ValueError("stage_feedback entries must use STAGE=PATH")
            stage_id = stage.strip().upper()
            if stage_id in feedback_mapping:
                raise ValueError(f"duplicate stage feedback: {stage_id}")
            feedback_mapping[stage_id] = _existing_file(path.strip(), f"{stage_id} feedback")
        return materialize_hamburg_execution_plan(
            output_dir=_output_dir(output_dir),
            stage_manifests=mapping,
            stage_feedback=feedback_mapping,
            resume=resume,
        )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return {
            "status": "fail",
            "claim_status": "execution-plan-invalid",
            "automatic_promotion_gate": "blocked",
            "error": str(exc),
        }


def sumo_hamburg_sandtorkai_named_replay(
    net_file: str,
    signal_binding_manifest: str,
    detector_binding_manifest: str,
    count_scope_manifest: str,
    count_stream_snapshot: str,
    canonical_count_file: str,
    output_dir: str,
    signal_observation_manifest: str | None = None,
    route_sampler_script: str | None = None,
    sumo_binary: str = "sumo",
    timeout_seconds: float = 300.0,
    simulation_begin: int = 0,
    simulation_end: int = 9000,
    comparison_begin: int = 1800,
    comparison_end: int = 9000,
    interval: int = 900,
    allow_detector_cross_section_boundaries: bool = False,
) -> dict[str, Any]:
    """Build W4 from the exact W2 signal, W3a count, and W3b detector inputs."""

    try:
        return materialize_hamburg_named_replay(
            net_file=_existing_file(net_file, "net_file"),
            signal_binding_manifest=_existing_file(signal_binding_manifest, "signal_binding_manifest"),
            detector_binding_manifest=_existing_file(
                detector_binding_manifest,
                "detector_binding_manifest",
            ),
            count_scope_manifest=_existing_file(count_scope_manifest, "count_scope_manifest"),
            count_stream_snapshot=_existing_file(count_stream_snapshot, "count_stream_snapshot"),
            canonical_count_file=_existing_file(canonical_count_file, "canonical_count_file"),
            output_dir=_output_dir(output_dir),
            signal_observation_manifest=(
                _existing_file(signal_observation_manifest, "signal_observation_manifest")
                if signal_observation_manifest
                else None
            ),
            route_sampler_script=(
                _existing_file(route_sampler_script, "route_sampler_script")
                if route_sampler_script
                else None
            ),
            sumo_binary=sumo_binary,
            timeout_seconds=timeout_seconds,
            simulation_begin=simulation_begin,
            simulation_end=simulation_end,
            comparison_begin=comparison_begin,
            comparison_end=comparison_end,
            interval=interval,
            allow_detector_cross_section_boundaries=allow_detector_cross_section_boundaries,
        )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return {
            "status": "fail",
            "claim_status": "named-replay-invalid",
            "automatic_promotion_gate": "blocked",
            "error": str(exc),
        }


def sumo_hamburg_sandtorkai_signal_observations(
    binding_manifest: str,
    begin_utc: str,
    end_utc: str,
    output_dir: str,
    api_base_url: str = "https://tld.iot.hamburg.de/v1.0/",
    cache_dir: str | None = None,
    preceding_lookback_hours: float = 1.0,
    chunk_minutes: float = 10.0,
    max_retries: int = 0,
    max_workers: int = 1,
    timeout_seconds: float = 60.0,
    retry_incomplete_cache: bool = True,
    allow_signal_group_projection: bool = False,
) -> dict[str, Any]:
    """Fetch official Saturday signal history and materialize auditable TLS events."""

    try:
        if preceding_lookback_hours <= 0 or chunk_minutes <= 0:
            raise ValueError("preceding_lookback_hours and chunk_minutes must be positive")
        return materialize_hamburg_named_signal_observations(
            binding_manifest=_existing_file(binding_manifest, "binding_manifest"),
            begin_utc=parse_iso_datetime(begin_utc),
            end_utc=parse_iso_datetime(end_utc),
            output_dir=_output_dir(output_dir),
            api_base_url=api_base_url,
            cache_dir=Path(cache_dir) if cache_dir else None,
            preceding_lookback=timedelta(hours=preceding_lookback_hours),
            chunk_duration=timedelta(minutes=chunk_minutes),
            max_retries=max_retries,
            max_workers=max_workers,
            timeout_seconds=timeout_seconds,
            retry_incomplete_cache=retry_incomplete_cache,
            allow_signal_group_projection=allow_signal_group_projection,
        )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return {
            "status": "fail",
            "claim_status": "signal-observations-invalid",
            "execution_gate": "blocked",
            "automatic_promotion_gate": "blocked",
            "error": str(exc),
        }


def sumo_digital_twin_replay_validate(
    net_file: str,
    route_file: str,
    e1_additional_file: str,
    e2_additional_file: str,
    tls_events_csv: str,
    expected_counts_csv: str,
    output_dir: str,
    prefix: str = "digital_twin_replay",
    replay_end: float = 7200.0,
    completion_end: float = 10800.0,
    step_length: float = 1.0,
    sumo_binary: str = "sumo",
    comparison_begin: float = 0.0,
    comparison_end: float | None = None,
) -> dict[str, Any]:
    try:
        return run_tls_detector_replay(
            net_file=_existing_file(net_file, "net_file"),
            route_file=_existing_file(route_file, "route_file"),
            e1_additional_file=_existing_file(e1_additional_file, "e1_additional_file"),
            e2_additional_file=_existing_file(e2_additional_file, "e2_additional_file"),
            tls_events_csv=_existing_file(tls_events_csv, "tls_events_csv"),
            expected_counts_csv=_existing_file(expected_counts_csv, "expected_counts_csv"),
            output_dir=_output_dir(output_dir),
            prefix=prefix,
            replay_end=replay_end,
            completion_end=completion_end,
            step_length=step_length,
            sumo_binary=sumo_binary,
            comparison_begin=comparison_begin,
            comparison_end=comparison_end,
        )
    except (OSError, ValueError) as exc:
        return {
            "status": "fail",
            "claim_status": "construction-invalid",
            "error": str(exc),
        }


def _existing_file(value: str, field_name: str) -> Path:
    path = Path(value)
    if not value or not path.is_file():
        raise ValueError(f"{field_name} must point to an existing file: {value}")
    return path


def _existing_dir(value: str, field_name: str) -> Path:
    path = Path(value)
    if not value or not path.is_dir():
        raise ValueError(f"{field_name} must point to an existing directory: {value}")
    return path


def _output_dir(value: str) -> Path:
    if not value.strip():
        raise ValueError("output_dir is required")
    path = Path(value)
    path.mkdir(parents=True, exist_ok=True)
    return path


def _torii_network_gate(report: dict[str, Any]) -> dict[str, Any]:
    """Allow official MAP/OCIT validation to follow a sound but review-pending Torii build."""
    net_file = str(report.get("net_file", "")).strip()
    if report.get("status") == "pass" and net_file and Path(net_file).is_file():
        return {"status": "pass", "net_file": net_file, "delegated_review_gates": []}

    gates = report.get("gate_status") if isinstance(report.get("gate_status"), dict) else {}
    required = {
        "area_confirmation": {"pass"},
        "road_level_scope": {"pass"},
        "network_build": {"pass"},
        "connectivity": {"pass", "partial"},
        "routeability_audit": {"pass"},
    }
    failed_required = {
        name: gates.get(name, "missing")
        for name, accepted in required.items()
        if gates.get(name) not in accepted
    }
    if failed_required or not net_file or not Path(net_file).is_file():
        return {
            "status": "blocked",
            "net_file": net_file,
            "failed_required_gates": failed_required,
            "reason": "Torii base network is not structurally ready for Hamburg official-data validation",
        }

    delegated = {
        name: value
        for name, value in sorted(gates.items())
        if value not in {"pass", "skipped"} and name not in required
    }
    return {
        "status": "provisional",
        "net_file": net_file,
        "failed_required_gates": {},
        "delegated_review_gates": delegated,
        "policy": (
            "continue only to produce official MAP/OCIT/count/signal bindings; retain partial status "
            "until every delegated Torii review gate is resolved"
        ),
    }
