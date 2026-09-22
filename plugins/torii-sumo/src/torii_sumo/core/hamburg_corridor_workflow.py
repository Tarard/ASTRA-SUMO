"""One-call orchestration for the reusable Hamburg corridor twin package."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any

from .artifact_io import write_json_atomic
from .candidate_contracts import file_sha256
from .cached_detector_demand import prepare_hamburg_corridor_candidate_package
from .hamburg_corridor_geometry_materializer import (
    materialize_hamburg_sandtorkai_geometry_safe_candidate,
)
from .hamburg_corridor_tls_materializer import (
    materialize_hamburg_sandtorkai_corridor_tls_candidate,
)


HAMBURG_CORRIDOR_WORKFLOW_SCHEMA = "torii.hamburg-sandtorkai-geometry-safe-digital-twin-package/v1"


def prepare_hamburg_sandtorkai_geometry_safe_corridor_package(
    *,
    source_net_file: Path,
    expected_source_sha256: str,
    movement_paths_file: Path,
    movement_endpoints_file: Path,
    expected_movement_paths_sha256: str,
    expected_movement_endpoints_sha256: str,
    map_xml_files: Sequence[Path],
    signal_stream_file: Path,
    count_stream_snapshot: Path,
    canonical_count_file: Path,
    output_dir: Path,
    road_sumo_binding_file: Path | None = None,
    expected_road_sumo_binding_sha256: str | None = None,
    prefix: str = "hamburg_sandtorkai_geometry_safe",
    simulation_begin: int = 0,
    simulation_end: int = 7200,
    comparison_begin: int = 1800,
    comparison_end: int = 7200,
    interval: int = 900,
    excluded_route_edges: Sequence[str] = (),
    route_sampler_optimize: str | None = "full",
    route_sampler_script: Path | None = None,
    netconvert_binary: str = "netconvert",
    sumo_binary: str = "sumo",
    timeout_seconds: float = 300.0,
    allow_detector_cross_section_boundaries: bool = False,
) -> dict[str, Any]:
    """Run the staged geometry, TLS, mapping, and demand candidate package.

    Geometry may be built without an arm-to-edge artifact because it does not
    write lane connections.  The following TLS materialization is deliberately
    stopped unless a binding for that exact geometry candidate is supplied.
    This makes the old one-call path an explicit two-pass hand-off rather than
    allowing it to bypass the road semantic layer.
    """

    destination = Path(output_dir).resolve()
    destination.mkdir(parents=True, exist_ok=True)
    if (road_sumo_binding_file is None) != (expected_road_sumo_binding_sha256 is None):
        raise ValueError(
            "road_sumo_binding_file and expected_road_sumo_binding_sha256 must be supplied together"
        )
    geometry = materialize_hamburg_sandtorkai_geometry_safe_candidate(
        source_net_file=source_net_file,
        expected_source_sha256=expected_source_sha256,
        output_dir=destination / "geometry",
        netconvert_binary=netconvert_binary,
        sumo_binary=sumo_binary,
        timeout_seconds=timeout_seconds,
    )
    geometry_net = Path(str(geometry["artifacts"]["network"])).resolve(strict=True)
    manifest_file = destination / "hamburg_sandtorkai_geometry_safe_digital_twin.manifest.json"
    if road_sumo_binding_file is None:
        manifest: dict[str, Any] = {
            "schema_id": HAMBURG_CORRIDOR_WORKFLOW_SCHEMA,
            "status": "blocked",
            "claim_status": "geometry-ready-road-sumo-binding-required",
            "automatic_promotion_gate": "blocked",
            "workflow": (
                "geometry-safe profile -> stop for exact-candidate road-arm/SUMO binding -> "
                "shared official TLS topology -> MAP reprojection -> endpoint-aware signal binding -> "
                "E1/E2/routeSampler demand"
            ),
            "stages": {
                "geometry": geometry,
                "road_sumo_binding": {
                    "status": "blocked",
                    "reason": (
                        "road_sumo_binding_file is required for the exact geometry candidate "
                        "before the TLS materializer may write any lane connection"
                    ),
                    "required_source_net_file": str(geometry_net),
                    "required_source_net_sha256": file_sha256(geometry_net),
                },
                "tls": {"status": "not_run"},
                "candidate": {"status": "not_run"},
            },
            "gates": {
                "geometry_status": geometry.get("status"),
                "geometry_surface_focus_status": geometry.get("surface_overlap_comparison", {}).get(
                    "status"
                ),
                "road_sumo_binding_status": "blocked",
                "tls_status": "not_run",
                "historical_signal_replay": "blocked_pending_official_observations",
            },
            "artifacts": {
                "manifest": str(manifest_file),
                "geometry_manifest": str(geometry["artifacts"]["manifest"]),
                "geometry_network": str(geometry_net),
                "geometry_network_sha256": file_sha256(geometry_net),
            },
            "evidence_boundary": (
                "The geometry candidate is review-only. No lane connection, TLS, sensor, demand, "
                "or digital-twin promotion was materialized before the road-arm intent gate."
            ),
        }
        write_json_atomic(manifest_file, manifest, sort_keys=True)
        manifest["manifest_file"] = str(manifest_file)
        manifest["manifest_sha256"] = file_sha256(manifest_file)
        return manifest
    tls = materialize_hamburg_sandtorkai_corridor_tls_candidate(
        source_net_file=geometry_net,
        movement_paths_file=movement_paths_file,
        movement_endpoints_file=movement_endpoints_file,
        expected_source_sha256=file_sha256(geometry_net),
        expected_movement_paths_sha256=expected_movement_paths_sha256,
        expected_movement_endpoints_sha256=expected_movement_endpoints_sha256,
        output_dir=destination / "tls",
        road_sumo_binding_file=road_sumo_binding_file,
        expected_road_sumo_binding_sha256=expected_road_sumo_binding_sha256,
        netconvert_binary=netconvert_binary,
        sumo_binary=sumo_binary,
        timeout_seconds=timeout_seconds,
    )
    tls_net = Path(str(tls["artifacts"]["network"])).resolve(strict=True)
    candidate = prepare_hamburg_corridor_candidate_package(
        candidate_manifest=Path(str(tls["artifacts"]["manifest"])).resolve(strict=True),
        candidate_net_file=tls_net,
        expected_candidate_net_sha256=file_sha256(tls_net),
        map_xml_files=map_xml_files,
        signal_stream_file=signal_stream_file,
        movement_endpoints_file=movement_endpoints_file,
        expected_movement_endpoints_sha256=expected_movement_endpoints_sha256,
        count_stream_snapshot=count_stream_snapshot,
        canonical_count_file=canonical_count_file,
        output_dir=destination / "candidate",
        prefix=prefix,
        simulation_begin=simulation_begin,
        simulation_end=simulation_end,
        comparison_begin=comparison_begin,
        comparison_end=comparison_end,
        interval=interval,
        excluded_route_edges=excluded_route_edges,
        route_sampler_optimize=route_sampler_optimize,
        route_sampler_script=route_sampler_script,
        timeout_seconds=timeout_seconds,
        allow_detector_cross_section_boundaries=allow_detector_cross_section_boundaries,
    )
    map_stage = candidate.get("stages", {}).get("map_binding", {})
    signal_stage = candidate.get("stages", {}).get("signal_binding", {})
    demand_stage = candidate.get("stages", {}).get("detector_demand", {})
    demand_gates = demand_stage.get("gates", {})
    route_sampler = demand_stage.get("route_sampler", {})
    manifest: dict[str, Any] = {
        "schema_id": HAMBURG_CORRIDOR_WORKFLOW_SCHEMA,
        "status": "partial",
        "claim_status": "geometry-safe-corridor-review-package",
        "automatic_promotion_gate": "blocked",
        "workflow": "geometry-safe profile -> shared official TLS topology -> MAP reprojection -> endpoint-aware signal binding -> E1/E2/routeSampler demand",
        "window": {
            "simulation_begin": simulation_begin,
            "simulation_end": simulation_end,
            "comparison_begin": comparison_begin,
            "comparison_end": comparison_end,
            "warmup_seconds": comparison_begin - simulation_begin,
            "interval": interval,
        },
        "stages": {
            "geometry": geometry,
            "tls": tls,
            "candidate": candidate,
        },
        "gates": {
            "geometry_status": geometry.get("status"),
            "geometry_surface_focus_status": geometry.get("surface_overlap_comparison", {}).get("status"),
            "road_sumo_binding_status": tls.get("road_sumo_materialization_gate", {}).get(
                "status"
            ),
            "tls_status": tls.get("status"),
            "tls_network_status": tls.get("network_audit", {}).get("status"),
            "tls_surface_focus_status": tls.get("surface_overlap_comparison", {}).get("status"),
            "map_status": map_stage.get("status"),
            "map_active_count": map_stage.get("mapping", {}).get("status_counts", {}).get("active", 0),
            "signal_status": signal_stage.get("status"),
            "signal_binding_counts": signal_stage.get("binding_counts", {}),
            "demand_status": demand_stage.get("status"),
            "active_detector_mapping_count": demand_gates.get("active_detector_mapping_count", 0),
            "virtual_detector_count": demand_gates.get("virtual_detector_count", 0),
            "canonical_row_count": demand_gates.get("canonical_row_count", 0),
            "complete_edge_section_count": demand_gates.get("complete_edge_section_count", 0),
            "route_candidate_count": route_sampler.get("candidate_route_count", 0),
            "route_sampler_constraint_match_fraction": route_sampler.get("constraint_match_fraction"),
            "lane_balance": demand_gates.get("lane_balance", "not_requested"),
            "boundary_policy": demand_stage.get("boundary_policy", "network_boundaries_only"),
            "edge_row_count": route_sampler.get("edge_row_count", 0),
            "total_count": route_sampler.get("total_count", 0),
            "historical_signal_replay": "blocked_pending_official_observations",
        },
        "artifacts": {
            "manifest": str(manifest_file),
            "geometry_manifest": str(geometry["artifacts"]["manifest"]),
            "tls_manifest": str(tls["artifacts"]["manifest"]),
            "road_sumo_binding": str(road_sumo_binding_file),
            "road_sumo_binding_sha256": expected_road_sumo_binding_sha256,
            "candidate_manifest": str(candidate["manifest_file"]),
            "network": str(tls_net),
            "network_sha256": file_sha256(tls_net),
        },
        "evidence_boundary": "review package only; static all-red programs and snapshot metadata are not historical signal replay",
    }
    write_json_atomic(manifest_file, manifest, sort_keys=True)
    manifest["manifest_file"] = str(manifest_file)
    manifest["manifest_sha256"] = file_sha256(manifest_file)
    return manifest
