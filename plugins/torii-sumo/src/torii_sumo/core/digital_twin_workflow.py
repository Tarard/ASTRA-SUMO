from __future__ import annotations

import csv
from dataclasses import asdict
from datetime import date
from pathlib import Path
from typing import Any, Sequence

from .detector_demand import (
    Detector,
    active_detectors,
    boundary_edges,
    build_boundary_routes,
    build_detector_anchored_routes,
    merge_routes,
    read_net,
    read_net_lanes,
    route_detector_incidence,
    route_rows,
    source_sink_rows,
    write_csv,
    write_e1_additional,
)
from .corridor_scope import build_compact_corridor_variant
from .digital_twin import (
    MapConnection,
    MapLane,
    SignalStream,
    parse_mapem,
    recent_completed_saturdays,
    write_canonical_counts,
    write_map_connections,
)
from .digital_twin_timeline import (
    SimulationWindow,
    aggregate_simulation_counts,
    rank_complete_simulation_windows,
    select_comparison_count_rows,
)
from .digital_twin_mapping import (
    aggregate_virtual_counts_to_complete_edge_sections,
    bind_count_streams_to_network,
    bind_map_lanes_to_network,
    bind_signal_streams_to_tls,
    build_virtual_sensor_aggregation,
    select_active_signal_streams,
    write_detector_mapping,
    write_edge_constraint_audit,
    write_route_sampler_edge_counts,
    write_tls_bindings,
    write_tls_link_events,
    write_virtual_detector_mapping,
    write_virtual_e2_additional,
    write_virtual_expected_counts,
)
from .hamburg_official import (
    HAMBURG_SANDTORKAI_SIGNAL_SNAPSHOT_DATE,
    OFFICIAL_COUNT_METADATA_URL,
    OFFICIAL_SIGNAL_CATALOG_API_URL,
    OFFICIAL_SIGNAL_CATALOG_URL,
    OFFICIAL_SIGNAL_METADATA_URL,
    SANDTORKAI_THREE_INTERSECTIONS,
    HamburgCatalogUnavailableError,
    HamburgCorridorPreset,
    SensorThingsClient,
    Transport,
    download_hamburg_signal_assets,
    download_hamburg_traffic_light_catalog,
    fetch_hamburg_count_observations,
    fetch_hamburg_count_streams,
    fetch_hamburg_signal_observations,
    fetch_hamburg_signal_streams,
    hamburg_sandtorkai_primary_signal_snapshot,
    sha256_file,
    write_json,
)
from .ocit_c import (
    build_vehicle_topology_inventory,
    parse_ocit_c,
    topology_control_index_by_node,
    validate_primary_signal_groups,
)
from .official_tls_workflow import rebuild_hamburg_sandtorkai_official_tls
from .route_sampler import run_route_sampler


def prepare_hamburg_corridor_digital_twin(
    *,
    net_file: Path,
    output_dir: Path,
    preset: HamburgCorridorPreset = SANDTORKAI_THREE_INTERSECTIONS,
    saturday_date: date | None = None,
    count_client: SensorThingsClient | None = None,
    signal_client: SensorThingsClient | None = None,
    primary_signal_client: SensorThingsClient | None = None,
    catalog_transport: Transport | None = None,
    route_sampler_script: Path | None = None,
    run_route_sampler_step: bool = True,
    timeout_seconds: float = 300.0,
    max_saturdays_to_try: int = 8,
    warmup_seconds: int = 1800,
    compact_corridor_scope: bool = True,
    corridor_buffer_m: float = 25.0,
    intersection_stub_radius_m: float = 80.0,
    max_scope_bridge_length_m: float = 300.0,
    rebuild_official_tls: bool = True,
    netconvert_binary: str = "netconvert",
    sumo_binary: str = "sumo",
) -> dict[str, Any]:
    if not net_file.is_file():
        raise ValueError(f"net_file does not exist: {net_file}")
    output_dir.mkdir(parents=True, exist_ok=True)
    official_dir = output_dir / "official"
    count_dir = official_dir / "counts"
    signal_dir = official_dir / "signals"
    catalog_dir = signal_dir / "catalog"
    demand_dir = output_dir / "demand"
    detector_dir = output_dir / "detectors"
    audit_dir = output_dir / "audit"
    for path in (count_dir, signal_dir, catalog_dir, demand_dir, detector_dir, audit_dir):
        path.mkdir(parents=True, exist_ok=True)

    catalog_artifact_paths: list[Path]
    try:
        signal_catalog, catalog_resolution = download_hamburg_traffic_light_catalog(
            catalog_dir,
            timeout_seconds=min(timeout_seconds, 30.0),
            transport=catalog_transport,
        )
        primary_signal_api_base = signal_catalog.primary_signal_api_base
        auxiliary_signal_api_base = signal_catalog.auxiliary_signal_api_base
        map_asset_base = signal_catalog.map_asset_base
        ocit_c_asset_base = signal_catalog.ocit_c_asset_base
        catalog_artifact_paths = [
            Path(str(catalog_resolution["raw_catalog"]["path"])),
            Path(str(catalog_resolution["selected_snapshot"]["path"])),
            Path(str(catalog_resolution["usage_guide_download"]["path"])),
        ]
    except HamburgCatalogUnavailableError as exc:
        primary_signal_api_base = preset.primary_signal_api_base
        auxiliary_signal_api_base = preset.signal_api_base
        map_asset_base = preset.signal_asset_base
        ocit_c_asset_base = f"{preset.signal_asset_base.rstrip('/')}/OCIT-C/"
        fallback_path = catalog_dir / "traffic-lights-data-hamburg6.catalog.fallback.json"
        catalog_resolution = {
            "status": "preset_fallback",
            "source": "preset_fallback",
            "catalog_api_url": OFFICIAL_SIGNAL_CATALOG_API_URL,
            "reason": str(exc),
            "selected_resources": None,
            "fallback_bases": {
                "primary_signal_api": primary_signal_api_base,
                "auxiliary_signal_api": auxiliary_signal_api_base,
                "map_assets": map_asset_base,
                "ocit_c_assets": ocit_c_asset_base,
            },
        }
        write_json(fallback_path, catalog_resolution)
        catalog_resolution["fallback_snapshot"] = {
            "path": str(fallback_path),
            "sha256": sha256_file(fallback_path),
            "bytes": fallback_path.stat().st_size,
        }
        catalog_artifact_paths = [fallback_path]

    count_client = count_client or SensorThingsClient(preset.count_api_base, timeout_seconds=timeout_seconds)
    injected_signal_client = signal_client
    signal_client = signal_client or SensorThingsClient(
        auxiliary_signal_api_base,
        timeout_seconds=min(timeout_seconds, 30.0),
    )
    # Preserve the historical single-client injection contract for tests and custom callers.
    # Default production runs keep the catalog's primary-signal v1.0 resource isolated from
    # the auxiliary v1.1 layers.
    primary_signal_client = primary_signal_client or injected_signal_client or SensorThingsClient(
        primary_signal_api_base,
        timeout_seconds=min(timeout_seconds, 30.0),
    )
    streams, stream_raw = fetch_hamburg_count_streams(count_client, preset.count_node_ids)
    stream_snapshot = count_dir / "count_streams.raw.json"
    write_json(stream_snapshot, stream_raw)
    stream_nodes = {stream.node_id for stream in streams}
    missing_nodes = sorted(set(preset.count_node_ids) - stream_nodes)
    if missing_nodes:
        return _write_blocked_manifest(
            output_dir,
            preset,
            net_file,
            reason=f"official count API returned no streams for nodes {missing_nodes}",
            artifacts={
                "count_stream_snapshot": str(stream_snapshot),
                **{
                    f"signal_catalog_artifact_{index}": str(path)
                    for index, path in enumerate(catalog_artifact_paths, start=1)
                },
            },
        )

    selected_date: date | None = None
    selected_observations = None
    selected_raw = None
    selection = None
    simulation_window: SimulationWindow | None = None
    coverage_attempts: list[dict[str, Any]] = []
    candidate_dates = [saturday_date] if saturday_date else recent_completed_saturdays(limit=max_saturdays_to_try)
    for candidate_date in candidate_dates:
        observations, observation_raw = fetch_hamburg_count_observations(
            count_client,
            streams,
            local_date=candidate_date,
            timezone_name=preset.timezone_name,
        )
        ranked_windows = rank_complete_simulation_windows(
            streams,
            observations,
            local_date=candidate_date,
            timezone_name=preset.timezone_name,
            formal_duration_seconds=7200,
            warmup_seconds=warmup_seconds,
            source_bin_seconds=300,
            output_bin_seconds=900,
        )
        if not ranked_windows:
            coverage_attempts.append(
                {
                    "date": candidate_date.isoformat(),
                    "status": "no_complete_simulation_window",
                    "warmup_seconds": warmup_seconds,
                    "required_interval_seconds": warmup_seconds + 7200,
                }
            )
            continue
        candidate_window = ranked_windows[0]
        candidate_selection = candidate_window.formal_window
        coverage_attempts.append(
            {
                "date": candidate_date.isoformat(),
                "status": "complete",
                "complete_candidate_count": len(ranked_windows),
                "warmup_seconds": warmup_seconds,
                "best_score": candidate_selection.score,
                "best_begin_utc": candidate_selection.begin_utc.isoformat(),
                "simulation_begin_utc": candidate_window.simulation_begin_utc.isoformat(),
                "simulation_end_utc": candidate_window.simulation_end_utc.isoformat(),
            }
        )
        selected_date = candidate_date
        selected_observations = observations
        selected_raw = observation_raw
        selection = candidate_selection
        simulation_window = candidate_window
        break
    coverage_attempt_file = audit_dir / "count_coverage_attempts.json"
    write_json(coverage_attempt_file, coverage_attempts)
    if (
        selected_date is None
        or selected_observations is None
        or selected_raw is None
        or selection is None
        or simulation_window is None
    ):
        return _write_blocked_manifest(
            output_dir,
            preset,
            net_file,
            reason=(
                "no Saturday contained a strict complete two-hour window plus the declared warm-up "
                "across every official count field"
            ),
            artifacts={
                "count_stream_snapshot": str(stream_snapshot),
                "coverage_attempts": str(coverage_attempt_file),
                **{
                    f"signal_catalog_artifact_{index}": str(path)
                    for index, path in enumerate(catalog_artifact_paths, start=1)
                },
            },
        )

    count_observation_snapshot = count_dir / f"{selected_date.isoformat()}_observations.raw.json"
    write_json(count_observation_snapshot, selected_raw)
    canonical_counts = aggregate_simulation_counts(
        streams,
        selected_observations,
        simulation_window,
        require_complete=True,
    )
    comparison_canonical_counts = select_comparison_count_rows(canonical_counts, simulation_window)
    canonical_count_file = count_dir / "canonical_counts_15min.csv"
    write_canonical_counts(canonical_count_file, canonical_counts)
    comparison_canonical_count_file = count_dir / "canonical_counts_comparison_15min.csv"
    write_canonical_counts(comparison_canonical_count_file, comparison_canonical_counts)

    signal_asset_paths, signal_asset_manifest = download_hamburg_signal_assets(
        count_client,
        preset,
        signal_dir / "assets",
        map_asset_base=map_asset_base,
        ocit_c_asset_base=ocit_c_asset_base,
    )
    signal_asset_manifest_file = signal_dir / "signal_assets.manifest.json"
    write_json(signal_asset_manifest_file, signal_asset_manifest)
    map_lanes: list[MapLane] = []
    map_connections: list[MapConnection] = []
    ocit_configs = []
    for node_id, paths in signal_asset_paths.items():
        lanes, connections = parse_mapem(Path(paths["map_xml"]))
        map_lanes.extend(lanes)
        map_connections.extend(connections)
        ocit_configs.append(parse_ocit_c(Path(paths["ocit_xml"])))
    map_connection_file = signal_dir / "official_map_connections.csv"
    write_map_connections(map_connection_file, map_connections)

    source_map_lane_bindings = bind_map_lanes_to_network(net_file, map_lanes)
    source_map_lane_binding_file = audit_dir / "official_map_lane_to_sumo_source.csv"
    _write_dataclass_csv(source_map_lane_binding_file, source_map_lane_bindings)
    scoped_net_file = net_file
    compact_scope_report: dict[str, Any] = {
        "status": "skipped",
        "reason": "compact_corridor_scope=false",
    }
    compact_scope_manifest_file = output_dir / "network" / "compact_corridor" / (
        f"{preset.preset_id}_compact.manifest.json"
    )
    if compact_corridor_scope:
        if preset.preset_id != SANDTORKAI_THREE_INTERSECTIONS.preset_id:
            raise ValueError(
                "automatic compact corridor selection currently requires the versioned "
                "Sandtorkai preset"
            )
        vehicle_lane_keys = {
            (_normalize_node_id(lane.node_id), lane.lane_id)
            for lane in map_lanes
            if lane.is_vehicle
        }
        required_vehicle_lane_ids = sorted(
            {
                binding.sumo_lane
                for binding in source_map_lane_bindings
                if binding.mapping_status == "active"
                and binding.sumo_lane
                and (_normalize_node_id(binding.node_id), binding.map_lane_id)
                in vehicle_lane_keys
            }
        )
        expected_vehicle_lane_count = len(vehicle_lane_keys)
        active_vehicle_binding_count = sum(
            binding.mapping_status == "active"
            and bool(binding.sumo_lane)
            and (_normalize_node_id(binding.node_id), binding.map_lane_id) in vehicle_lane_keys
            for binding in source_map_lane_bindings
        )
        if active_vehicle_binding_count != expected_vehicle_lane_count:
            return _write_blocked_manifest(
                output_dir,
                preset,
                net_file,
                reason=(
                    "compact corridor selection requires every official MAP vehicle lane to have "
                    f"one active source binding; active={active_vehicle_binding_count}, "
                    f"expected={expected_vehicle_lane_count}"
                ),
                artifacts={
                    "map_connections": str(map_connection_file),
                    "source_map_lane_bindings": str(source_map_lane_binding_file),
                },
            )
        compact_scope_report = build_compact_corridor_variant(
            source_net_file=net_file,
            output_dir=output_dir / "network" / "compact_corridor",
            centers_lonlat=tuple(
                preset.node_centers[node_id] for node_id in preset.count_node_ids
            ),
            required_lane_ids=required_vehicle_lane_ids,
            prefix=f"{preset.preset_id}_compact",
            corridor_buffer_m=corridor_buffer_m,
            intersection_stub_radius_m=intersection_stub_radius_m,
            max_bridge_length_m=max_scope_bridge_length_m,
            netconvert_binary=netconvert_binary,
            timeout_seconds=timeout_seconds,
        )
        compact_scope_manifest_file = Path(
            str(compact_scope_report.get("manifest_file", compact_scope_manifest_file))
        )
        if compact_scope_report.get("status") != "pass":
            return _write_blocked_manifest(
                output_dir,
                preset,
                net_file,
                reason=(
                    "compact three-intersection corridor build failed: "
                    f"{compact_scope_report.get('error', 'one or more scope gates failed')}"
                ),
                artifacts={
                    "compact_scope_manifest": str(compact_scope_manifest_file),
                    "source_map_lane_bindings": str(source_map_lane_binding_file),
                },
            )
        scoped_net_file = Path(str(compact_scope_report["output_net_file"]))

    base_map_lane_bindings = bind_map_lanes_to_network(scoped_net_file, map_lanes)
    base_map_lane_binding_file = audit_dir / "official_map_lane_to_sumo_base.csv"
    _write_dataclass_csv(base_map_lane_binding_file, base_map_lane_bindings)
    official_tld_observation_streams = hamburg_sandtorkai_primary_signal_snapshot()
    ocit_group_validation = validate_primary_signal_groups(
        official_tld_observation_streams,
        ocit_configs,
    )
    ocit_group_validation_file = audit_dir / "official_ocit_group_validation.json"
    write_json(ocit_group_validation_file, ocit_group_validation)
    vehicle_topology_inventory = build_vehicle_topology_inventory(
        ocit_configs,
        map_lanes,
        map_connections,
        official_tld_observation_streams,
    )
    official_movement_inventory_file = audit_dir / "official_ocit_vehicle_movements.json"
    write_json(official_movement_inventory_file, asdict(vehicle_topology_inventory))
    movement_index_by_node = topology_control_index_by_node(vehicle_topology_inventory)

    effective_net_file = scoped_net_file
    official_tls_derivation: dict[str, Any] = {
        "status": "skipped",
        "reason": "rebuild_official_tls=false",
    }
    official_tls_rebuild_report: dict[str, Any] = {
        "status": "skipped",
        "reason": "rebuild_official_tls=false",
    }
    official_tls_derivation_file = audit_dir / "official_tls_native_teacher_derivation.json"
    if rebuild_official_tls:
        if preset.preset_id != SANDTORKAI_THREE_INTERSECTIONS.preset_id:
            raise ValueError(
                "automatic official TLS rebuilding currently requires the versioned Sandtorkai preset"
            )
        official_tls_rebuild_report = rebuild_hamburg_sandtorkai_official_tls(
            source_net_file=scoped_net_file,
            signal_asset_dir=signal_dir / "assets",
            output_dir=output_dir / "network" / "official_tls_rebuild",
            netconvert_binary=netconvert_binary,
            sumo_binary=sumo_binary,
            timeout_seconds=timeout_seconds,
        )
        official_tls_derivation = dict(
            official_tls_rebuild_report.get(
                "native_teacher_derivation",
                {
                    "status": official_tls_rebuild_report.get("derivation_status", "blocked"),
                    "visual_review_required_count": official_tls_rebuild_report.get(
                        "visual_review_required_count", 0
                    ),
                },
            )
        )
        reported_derivation_file = next(
            (
                Path(str(artifact["path"]))
                for artifact in official_tls_rebuild_report.get("artifacts", [])
                if artifact.get("role") == "official_tls_native_teacher_derivation"
                and artifact.get("path")
            ),
            None,
        )
        if reported_derivation_file is not None:
            official_tls_derivation_file = reported_derivation_file
        else:
            write_json(official_tls_derivation_file, official_tls_derivation)
        if official_tls_rebuild_report.get("status") != "pass":
            return _write_blocked_manifest(
                output_dir,
                preset,
                net_file,
                reason=(
                    "official MAP/OCIT TLS network rebuild failed: "
                    f"{official_tls_rebuild_report.get('error', 'one or more native replay gates failed')}"
                ),
                artifacts={
                    "official_tls_rebuild_manifest": str(
                        official_tls_rebuild_report.get("manifest_file", "")
                    ),
                    "official_tls_derivation": str(official_tls_derivation_file),
                    "ocit_group_validation": str(ocit_group_validation_file),
                    "ocit_vehicle_movements": str(official_movement_inventory_file),
                },
            )
        effective_net_file = Path(str(official_tls_rebuild_report["rebuilt_net_file"]))

    map_lane_bindings = bind_map_lanes_to_network(effective_net_file, map_lanes)
    map_lane_binding_file = audit_dir / "official_map_lane_to_sumo.csv"
    _write_dataclass_csv(map_lane_binding_file, map_lane_bindings)
    detector_mappings = bind_count_streams_to_network(
        effective_net_file,
        streams,
        map_lanes,
        map_lane_bindings,
        period=900,
    )
    detector_mapping_file = detector_dir / "detector_mapping.csv"
    write_detector_mapping(detector_mapping_file, detector_mappings)
    active_mapping_count = sum(mapping.mapping_status == "active" for mapping in detector_mappings)
    mapping_complete = active_mapping_count == len(streams)

    e1_additional_file = detector_dir / "e1_detectors.add.xml"
    e2_additional_file = detector_dir / "e2_queue_detectors.add.xml"
    virtual_mapping_file = detector_dir / "virtual_detector_mapping.csv"
    virtual_expected_file = detector_dir / "virtual_expected_counts_15min.csv"
    virtual_expected_simulation_file = detector_dir / "virtual_expected_counts_simulation_15min.csv"
    edge_constraint_audit_file = audit_dir / "route_sampler_edge_constraints.csv"
    route_support: dict[str, Any] = {"status": "blocked", "reason": "detector mappings are incomplete"}
    edge_flow_notes: list[str] = []
    virtual_detector_count = 0
    edge_count_file = demand_dir / "official_edge_counts_15min.xml"
    route_sampler_report: dict[str, Any] = {"status": "blocked", "reason": "detector mappings are incomplete"}
    if mapping_complete:
        virtual_aggregation = build_virtual_sensor_aggregation(
            detector_mappings,
            canonical_counts,
            bin_seconds=900,
            expected_begin=0,
            expected_end=simulation_window.simulation_duration_seconds,
        )
        comparison_virtual_aggregation = build_virtual_sensor_aggregation(
            detector_mappings,
            comparison_canonical_counts,
            bin_seconds=900,
            expected_begin=simulation_window.comparison_begin,
            expected_end=simulation_window.comparison_end,
        )
        detectors = list(virtual_aggregation.detectors)
        virtual_detector_count = len(detectors)
        write_virtual_detector_mapping(virtual_mapping_file, virtual_aggregation.groups)
        write_virtual_expected_counts(
            virtual_expected_simulation_file,
            virtual_aggregation.expected_counts,
        )
        write_virtual_expected_counts(
            virtual_expected_file,
            comparison_virtual_aggregation.expected_counts,
        )
        write_e1_additional(
            e1_additional_file,
            detectors,
            lanes=read_net_lanes(effective_net_file),
            period=900,
            output_file="e1_15min.xml",
        )
        write_virtual_e2_additional(
            e2_additional_file,
            virtual_aggregation.groups,
            output_file="e2_15min.xml",
            period=900,
        )
        edge_flows, edge_constraint_audits = aggregate_virtual_counts_to_complete_edge_sections(
            effective_net_file,
            virtual_aggregation,
        )
        write_edge_constraint_audit(edge_constraint_audit_file, edge_constraint_audits)
        edge_flow_notes = [
            f"edge {row.edge_id}: {row.constraint_reason}"
            for row in edge_constraint_audits
            if row.constraint_status != "active"
        ]
        write_route_sampler_edge_counts(edge_count_file, edge_flows)
        route_support = _write_route_support(
            effective_net_file,
            detectors,
            demand_dir,
            prefix=preset.preset_id,
        )
        if route_support.get("status") == "pass" and run_route_sampler_step:
            route_sampler_report = run_route_sampler(
                candidate_manifest_csv=Path(str(route_support["route_candidate_manifest"])),
                edge_data_file=edge_count_file,
                output_dir=demand_dir,
                prefix=preset.preset_id,
                begin=0,
                end=simulation_window.simulation_duration_seconds,
                interval=900,
                seed=42,
                route_sampler_script=route_sampler_script,
                timeout_seconds=timeout_seconds,
            )
        elif route_support.get("status") == "pass":
            route_sampler_report = {
                "status": "skipped",
                "reason": "run_route_sampler_step=false",
                "claim_status": "construction-incomplete",
            }

    primary_signal_streams, primary_signal_raw = fetch_hamburg_signal_streams(
        primary_signal_client,
        preset.signal_node_ids,
        layers=("primary_signal",),
        motor_vehicle_only=True,
        max_retries=1,
        max_workers=3,
        cache_dir=signal_dir / "metadata_cache" / "primary_signal",
    )
    auxiliary_signal_streams, auxiliary_signal_raw = fetch_hamburg_signal_streams(
        signal_client,
        preset.signal_node_ids,
        layers=("signal_program", "cycle_second"),
        motor_vehicle_only=True,
        max_retries=1,
        max_workers=3,
        cache_dir=signal_dir / "metadata_cache" / "auxiliary",
    )
    stream_index: dict[int, SignalStream] = {}
    for stream in [*primary_signal_streams, *auxiliary_signal_streams]:
        previous = stream_index.get(stream.stream_id)
        if previous is not None and previous != stream:
            raise ValueError(f"signal datastream {stream.stream_id} differs across official API resources")
        stream_index[stream.stream_id] = stream
    signal_streams = sorted(
        stream_index.values(),
        key=lambda item: (int(item.node_id), item.connection_id, item.layer_name),
    )
    signal_stream_raw = {
        # Preserve the old top-level primary audit keys while making the endpoint split explicit.
        "request_urls": [
            *primary_signal_raw.get("request_urls", []),
            *auxiliary_signal_raw.get("request_urls", []),
        ],
        "pages_by_node": primary_signal_raw.get("pages_by_node", {}),
        "node_results": primary_signal_raw.get("node_results", {}),
        "failed_node_ids": primary_signal_raw.get("failed_node_ids", []),
        "partial_node_ids": primary_signal_raw.get("partial_node_ids", []),
        "primary_signal": primary_signal_raw,
        "auxiliary": auxiliary_signal_raw,
        "api_sources": {
            "primary_signal": primary_signal_raw.get("api_base_url"),
            "signal_program": auxiliary_signal_raw.get("api_base_url"),
            "cycle_second": auxiliary_signal_raw.get("api_base_url"),
        },
    }
    live_primary_stream_count = len(primary_signal_streams)
    primary_unavailability_reason = _primary_metadata_circuit_breaker_reason(
        primary_signal_raw,
        preset.signal_node_ids,
    )
    metadata_snapshot_used = False
    if (
        live_primary_stream_count == 0
        and primary_unavailability_reason is not None
        and preset.preset_id == SANDTORKAI_THREE_INTERSECTIONS.preset_id
    ):
        snapshot_streams = hamburg_sandtorkai_primary_signal_snapshot()
        signal_streams = sorted(
            [*snapshot_streams, *auxiliary_signal_streams],
            key=lambda item: (int(item.node_id), item.connection_id, item.layer_name),
        )
        metadata_snapshot_used = True
        signal_stream_raw["snapshot_fallback"] = {
            "used": True,
            "snapshot_date": HAMBURG_SANDTORKAI_SIGNAL_SNAPSHOT_DATE,
            "scope": "primary_signal movement identifiers for mapping only",
            "history_substitution_allowed": False,
            "reason": primary_unavailability_reason,
        }
    signal_stream_snapshot = signal_dir / "signal_streams.raw.json"
    write_json(signal_stream_snapshot, signal_stream_raw)
    signal_stream_file = signal_dir / "signal_streams.csv"
    _write_signal_streams(signal_stream_file, signal_streams)
    tls_bindings = bind_signal_streams_to_tls(
        effective_net_file,
        signal_streams,
        map_lane_bindings,
    )
    tls_binding_file = signal_dir / "tls_bindings.csv"
    write_tls_bindings(tls_binding_file, tls_bindings)
    replay_signal_streams = select_active_signal_streams(signal_streams, tls_bindings)
    if metadata_snapshot_used and live_primary_stream_count == 0:
        signal_observations = {stream.stream_id: [] for stream in replay_signal_streams}
        signal_observation_raw = {
            "query_begin_utc": simulation_window.simulation_begin_utc.isoformat().replace("+00:00", "Z"),
            "query_end_utc": simulation_window.simulation_end_utc.isoformat().replace("+00:00", "Z"),
            "api_base_url": primary_signal_client.base_url,
            "request_urls": [],
            "pages_by_stream": {str(stream.stream_id): {"preceding": [], "window": []} for stream in replay_signal_streams},
            "stream_results": {
                str(stream.stream_id): {
                    "stream_id": stream.stream_id,
                    "node_id": stream.node_id,
                    "status": "error",
                    "errors": [
                        {
                            "stage": "live_metadata_outage_circuit_breaker",
                            "exception_type": "UpstreamUnavailable",
                            "message": (
                                "history fetch skipped because all live primary metadata node queries "
                                "failed or were unavailable"
                            ),
                        }
                    ],
                }
                for stream in replay_signal_streams
            },
            "failed_stream_ids": [stream.stream_id for stream in replay_signal_streams],
            "partial_stream_ids": [],
            "policy": {
                "history_substitution_allowed": False,
                "outage_circuit_breaker": True,
            },
        }
    else:
        signal_observations, signal_observation_raw = fetch_hamburg_signal_observations(
            primary_signal_client,
            replay_signal_streams,
            begin_utc=simulation_window.simulation_begin_utc,
            end_utc=simulation_window.simulation_end_utc,
            include_preceding_state=True,
            max_retries=1,
            max_workers=4,
            cache_dir=signal_dir / "observation_cache" / "primary_signal",
        )
    signal_observation_snapshot = signal_dir / "signal_observations.raw.json"
    write_json(signal_observation_snapshot, signal_observation_raw)
    tls_event_file = signal_dir / "tls_link_events.csv"
    tls_event_stats = write_tls_link_events(
        tls_event_file,
        signal_streams,
        signal_observations,
        tls_bindings,
        begin_utc=simulation_window.simulation_begin_utc,
        end_utc=simulation_window.simulation_end_utc,
    )
    active_tls_bindings = [binding for binding in tls_bindings if binding.mapping_status == "active"]
    primary_stream_ids = {
        stream.stream_id for stream in signal_streams if stream.layer_name == "primary_signal"
    }
    primary_stream_count = len(primary_stream_ids)
    accounted_tls_bindings = [
        binding for binding in tls_bindings if binding.mapping_status in {"active", "redundant"}
    ]
    active_tls_stream_ids = {binding.stream_id for binding in active_tls_bindings}
    accounted_tls_stream_ids = {binding.stream_id for binding in accounted_tls_bindings}
    failed_signal_nodes = list(signal_stream_raw.get("failed_node_ids", []))
    partial_signal_nodes = list(signal_stream_raw.get("partial_node_ids", []))
    failed_auxiliary_signal_nodes = list(auxiliary_signal_raw.get("failed_node_ids", []))
    partial_auxiliary_signal_nodes = list(auxiliary_signal_raw.get("partial_node_ids", []))
    failed_signal_streams = list(signal_observation_raw.get("failed_stream_ids", []))
    partial_signal_streams = list(signal_observation_raw.get("partial_stream_ids", []))
    tls_mapping_complete = (
        bool(active_tls_bindings)
        and live_primary_stream_count > 0
        and not metadata_snapshot_used
        and accounted_tls_stream_ids == primary_stream_ids
        and not failed_signal_nodes
        and not partial_signal_nodes
    )
    tls_initialization_complete = bool(active_tls_bindings) and (
        tls_event_stats["active_binding_count"] == tls_event_stats["initialized_binding_count"]
    )
    signal_history_complete = (
        bool(replay_signal_streams) and not failed_signal_streams and not partial_signal_streams
    )

    gaps: list[str] = []
    if not compact_corridor_scope:
        gaps.append(
            "compact three-intersection corridor scoping was disabled by the caller"
        )
    if not rebuild_official_tls:
        gaps.append("official MAP/OCIT TLS rebuilding was disabled by the caller")
    elif official_tls_derivation.get("status") != "pass":
        gaps.append(
            "the official TLS topology was rebuilt, but one or more uncontrolled-path stop-line choices "
            "still require MAP/KML/Netedit visual acceptance"
        )
    if not mapping_complete:
        gaps.append(
            f"only {active_mapping_count}/{len(streams)} count fields have active MAP-confirmed SUMO lane mappings"
        )
    if route_sampler_report.get("status") != "pass":
        gaps.append(f"routeSampler demand is not complete: {route_sampler_report.get('reason', 'not run successfully')}")
    if not tls_mapping_complete:
        gaps.append("one or more official motor-vehicle signal connections are not bound to a SUMO tl/linkIndex")
    if metadata_snapshot_used:
        gaps.append(
            "the dated primary-signal snapshot is for movement mapping only and cannot prove live metadata "
            "or historical signal completeness"
        )
    if not tls_initialization_complete:
        gaps.append("one or more active signal bindings lack a preceding state at simulation time zero")
    if failed_signal_nodes or partial_signal_nodes:
        gaps.append(
            f"official signal metadata is incomplete: failed nodes={failed_signal_nodes}, "
            f"partial nodes={partial_signal_nodes}"
        )
    if not signal_history_complete:
        gaps.append(
            f"official primary-signal history is incomplete: failed streams={failed_signal_streams}, "
            f"partial streams={partial_signal_streams}"
        )
    mixed_control_movements = [
        movement
        for movement in vehicle_topology_inventory.movements
        if movement.primary_motor_groups and movement.secondary_motor_groups
    ]
    if mixed_control_movements:
        gaps.append(
            f"{len(mixed_control_movements)} official vehicle movements reference both primary and "
            "secondary signal heads; exact state composition remains fail-closed until the official "
            "combination semantics and both histories are available"
        )

    artifacts = _artifact_manifest(
        [
            *catalog_artifact_paths,
            net_file,
            scoped_net_file,
            effective_net_file,
            stream_snapshot,
            coverage_attempt_file,
            count_observation_snapshot,
            canonical_count_file,
            comparison_canonical_count_file,
            signal_asset_manifest_file,
            map_connection_file,
            source_map_lane_binding_file,
            base_map_lane_binding_file,
            map_lane_binding_file,
            ocit_group_validation_file,
            official_movement_inventory_file,
            official_tls_derivation_file,
            compact_scope_manifest_file,
            Path(str(official_tls_rebuild_report.get("manifest_file", ""))),
            detector_mapping_file,
            virtual_mapping_file,
            virtual_expected_file,
            virtual_expected_simulation_file,
            edge_constraint_audit_file,
            e1_additional_file,
            e2_additional_file,
            edge_count_file,
            signal_stream_snapshot,
            signal_stream_file,
            signal_observation_snapshot,
            tls_binding_file,
            tls_event_file,
        ]
    )
    manifest = {
        "schema_id": "torii.corridor-digital-twin.v1",
        "status": "pass" if not gaps else "partial",
        "claim_status": (
            "detector-and-signal-replay-input-package" if not gaps else "construction-incomplete"
        ),
        "preset": {
            "id": preset.preset_id,
            "display_name": preset.display_name,
            "bbox": preset.bbox,
            "count_node_ids": list(preset.count_node_ids),
            "signal_node_ids": list(preset.signal_node_ids),
        },
        "network": {
            "source_net_file": str(net_file),
            "source_net_sha256": sha256_file(net_file),
            "compact_corridor_scope_enabled": compact_corridor_scope,
            "compact_corridor_scope_status": compact_scope_report.get("status"),
            "compact_corridor_scope_claim_status": compact_scope_report.get("claim_status"),
            "compact_corridor_scope_manifest": compact_scope_report.get("manifest_file", ""),
            "scoped_net_file": str(scoped_net_file),
            "scoped_net_sha256": sha256_file(scoped_net_file),
            "scoped_external_edge_count": compact_scope_report.get(
                "output_external_edge_count"
            ),
            "scope_reduction_fraction": compact_scope_report.get(
                "scope_reduction_fraction"
            ),
            "effective_net_file": str(effective_net_file),
            "effective_net_sha256": sha256_file(effective_net_file),
            "official_tls_rebuild_enabled": rebuild_official_tls,
            "official_tls_rebuild_status": official_tls_rebuild_report.get("status"),
            "official_tls_rebuild_claim_status": official_tls_rebuild_report.get("claim_status"),
            "official_tls_rebuild_manifest": official_tls_rebuild_report.get("manifest_file", ""),
            "official_tls_derivation_status": official_tls_derivation.get("status"),
            "official_tls_visual_review_required_count": official_tls_derivation.get(
                "visual_review_required_count", 0
            ),
            "source_osm_tls_retirement_policy": (
                "retire every source TLS controller inside the explicitly compact three-node "
                "scope, then install only the three official HH_<node> controllers"
            ),
            "control_model": (
                "official node-level joined TLS with one deterministic linkIndex per distinct full "
                "primary/secondary control expression; movements share an index only when both "
                "roles are identical"
            ),
            "placeholder_program": (
                "all-red topology placeholder; exact official event replay or an explicitly labeled OCIT "
                "fallback is required for operational simulation"
            ),
        },
        "official_catalog": catalog_resolution,
        "signal_topology": {
            "source": "official OCIT-C TrafficStreamConfigData joined to official MAP connection IDs",
            "ocit_motor_group_referenced_movement_count": (
                vehicle_topology_inventory.source_movement_count
            ),
            "excluded_non_vehicle_movement_count": (
                vehicle_topology_inventory.excluded_non_vehicle_movement_count
            ),
            "movement_count": vehicle_topology_inventory.movement_count,
            "observed_tld_snapshot_match_count": vehicle_topology_inventory.observed_match_count,
            "control_index_policy": vehicle_topology_inventory.group_resolution_policy,
            "control_indices_by_node": movement_index_by_node,
            "mixed_primary_secondary_movement_count": len(mixed_control_movements),
            "mixed_control_policy": (
                "topology is represented, but operational signal replay remains incomplete until "
                "the official primary/secondary state-composition rule is established"
            ),
            "inventory_file": str(official_movement_inventory_file),
        },
        "window": {
            **asdict(selection),
            "local_date": selection.local_date.isoformat(),
            "begin_utc": selection.begin_utc.isoformat().replace("+00:00", "Z"),
            "end_utc": selection.end_utc.isoformat().replace("+00:00", "Z"),
            "warning": (
                "score sums all lane fields at all three nodes and therefore repeats vehicles that traverse multiple nodes; "
                "it ranks activity but is not a unique-vehicle total"
            ),
        },
        "simulation_window": {
            "warmup_seconds": simulation_window.warmup_seconds,
            "simulation_begin_utc": simulation_window.simulation_begin_utc.isoformat().replace(
                "+00:00", "Z"
            ),
            "simulation_end_utc": simulation_window.simulation_end_utc.isoformat().replace(
                "+00:00", "Z"
            ),
            "simulation_duration_seconds": simulation_window.simulation_duration_seconds,
            "comparison_begin": simulation_window.comparison_begin,
            "comparison_end": simulation_window.comparison_end,
            "policy": (
                "demand and signal replay cover warm-up plus the formal window; detector fit is reported "
                "only for the formal two-hour interval"
            ),
        },
        "counts": {
            "stream_count": len(streams),
            "canonical_15min_rows": len(canonical_counts),
            "comparison_15min_rows": len(comparison_canonical_counts),
            "active_mapping_count": active_mapping_count,
            "virtual_detector_count": virtual_detector_count,
            "aggregation_policy": (
                "all lane fields stay in the virtual E1 expected-count file; routeSampler edgeData sums only "
                "complete passenger-lane cross-sections within one physical node and 15-minute bin"
            ),
            "virtual_detector_policy": (
                "one E1/E2 per (physical node, SUMO lane); multiple official fields collapsed by OSM lanes "
                "are summed with source membership retained"
            ),
            "aggregation_notes": edge_flow_notes,
            "route_sampler_complete_section_count": sum(
                row.constraint_status == "active" for row in edge_constraint_audits
            )
            if mapping_complete
            else 0,
        },
        "signals": {
            "stream_count": len(signal_streams),
            "live_primary_stream_count": live_primary_stream_count,
            "metadata_source": (
                f"bundled official TLD snapshot dated {HAMBURG_SANDTORKAI_SIGNAL_SNAPSHOT_DATE}"
                if metadata_snapshot_used
                else "live official TLD API or atomic live cache"
            ),
            "metadata_snapshot_used": metadata_snapshot_used,
            "layer_stream_counts": {
                layer: sum(stream.layer_name == layer for stream in signal_streams)
                for layer in ("primary_signal", "signal_program", "cycle_second")
            },
            "primary_stream_count": primary_stream_count,
            "active_tls_binding_count": len(active_tls_bindings),
            "active_tls_stream_count": len(active_tls_stream_ids),
            "redundant_tls_binding_count": sum(
                binding.mapping_status == "redundant" for binding in tls_bindings
            ),
            "tls_mapping_complete": tls_mapping_complete,
            "tls_initialization_complete": tls_initialization_complete,
            "signal_history_complete": signal_history_complete,
            "failed_metadata_nodes": failed_signal_nodes,
            "partial_metadata_nodes": partial_signal_nodes,
            "failed_auxiliary_metadata_nodes": failed_auxiliary_signal_nodes,
            "partial_auxiliary_metadata_nodes": partial_auxiliary_signal_nodes,
            "failed_history_streams": failed_signal_streams,
            "partial_history_streams": partial_signal_streams,
            "ocit_group_validation": asdict(ocit_group_validation),
            "ocit_nodes": [
                {
                    "node_id": config.node_id,
                    "motor_signal_groups": list(config.motor_group_ids),
                    "saturday_program_ids": list(config.saturday_program_ids),
                    "has_vehicle_actuated_control": config.has_vehicle_actuated_control,
                    "saturday_vehicle_actuated": config.saturday_vehicle_actuated,
                    "saturday_plan_semantics": config.saturday_plan_semantics,
                }
                for config in sorted(ocit_configs, key=lambda item: item.node_id)
            ],
            **tls_event_stats,
            "state_semantics": (
                "primary_signal changes plus a bounded preceding state are replayed with forward hold; "
                "signal_program/cycle_second are metadata-only best effort; flashing/unknown conversions are non-exact"
            ),
        },
        "demand": {"route_support": route_support, "route_sampler": route_sampler_report},
        "initial_state": {
            "observed_queue_state_available": False,
            "simulation_policy": (
                f"start with an empty network at t=0, run {simulation_window.warmup_seconds}s of observed "
                "count-constrained demand and official signal control, then begin formal comparison"
            ),
            "claim_boundary": (
                "Hamburg exposes count and signal events but no measured queue snapshot for this window; "
                "the warm-up forms a simulated queue at comparison start and does not prove the real queue length"
            ),
        },
        "identifiability": {
            "turn_flows_directly_observed": False,
            "od_matrix_directly_observed": False,
            "policy": (
                "routeSampler returns one detector-constrained plausible realization from explicit candidate routes; "
                "it is not a uniquely identified OD matrix or individual trajectory reconstruction"
            ),
        },
        "sources": {
            "count_api": preset.count_api_base,
            "count_metadata": OFFICIAL_COUNT_METADATA_URL,
            "primary_signal_api": primary_signal_client.base_url,
            "signal_program_api": signal_client.base_url,
            "cycle_second_api": signal_client.base_url,
            "signal_catalog_api": OFFICIAL_SIGNAL_CATALOG_API_URL,
            "signal_catalog_page": OFFICIAL_SIGNAL_CATALOG_URL,
            "signal_metadata": OFFICIAL_SIGNAL_METADATA_URL,
            "signal_assets": map_asset_base,
            "signal_assets_ocit_c": ocit_c_asset_base,
            "license": "Datenlizenz Deutschland Namensnennung 2.0",
            "attribution": "Freie und Hansestadt Hamburg, Behoerde fuer Verkehr und Mobilitaetswende",
        },
        "artifacts": artifacts,
        "gaps": gaps,
        "next_gate": "run SUMO with route demand, E1/E2 additions and TLS events, then audit E1 nVehContrib and completion",
    }
    manifest_file = output_dir / "digital_twin.manifest.json"
    write_json(manifest_file, manifest)
    manifest["manifest_file"] = str(manifest_file)
    manifest["manifest_sha256"] = sha256_file(manifest_file)
    return manifest


def _primary_metadata_circuit_breaker_reason(
    raw: dict[str, Any],
    expected_node_ids: Sequence[str],
) -> str | None:
    if not expected_node_ids:
        return None
    node_results = raw.get("node_results")
    if not isinstance(node_results, dict):
        return "all live primary metadata node queries failed or were unavailable"
    unavailable_statuses = {"empty", "error"}
    if all(
        not isinstance(node_results.get(node_id), dict)
        or node_results[node_id].get("status") in unavailable_statuses
        for node_id in expected_node_ids
    ):
        return "all live primary metadata node queries failed or were unavailable"
    return None


def _write_route_support(
    net_file: Path,
    detectors: Sequence[Detector],
    output_dir: Path,
    *,
    prefix: str,
) -> dict[str, Any]:
    edges, connections = read_net(net_file)
    sources, sinks = boundary_edges(edges, connections)
    detectors = active_detectors(list(detectors))
    anchored = build_detector_anchored_routes(detectors, sources, sinks, connections, max_hops=80)
    boundary = build_boundary_routes(sources, sinks, connections, max_routes=500, max_hops=80, min_edges=2)
    routes = merge_routes(anchored, boundary, max_routes=500)
    incidence = route_detector_incidence(routes, detectors)
    covered = {str(row["detector_id"]) for row in incidence if int(row["incidence"]) == 1}
    source_sink_file = output_dir / f"{prefix}_source_sink_manifest.csv"
    route_file = output_dir / f"{prefix}_route_candidate_manifest.csv"
    incidence_file = output_dir / f"{prefix}_route_detector_incidence.csv"
    write_csv(
        source_sink_file,
        source_sink_rows(edges, sources, sinks),
        ["role", "edge_id", "from_node", "to_node", "length", "reason"],
    )
    write_csv(
        route_file,
        route_rows(routes, edges),
        ["route_id", "source_edge", "sink_edge", "edge_count", "route_length", "edges"],
    )
    write_csv(
        incidence_file,
        incidence,
        [
            "route_id",
            "source_edge",
            "sink_edge",
            "detector_id",
            "detector_edge",
            "detector_direction",
            "incidence",
        ],
    )
    status = "pass" if routes and len(covered) == len(detectors) else "fail"
    return {
        "status": status,
        "source_count": len(sources),
        "sink_count": len(sinks),
        "route_count": len(routes),
        "active_detector_count": len(detectors),
        "covered_detector_count": len(covered),
        "source_sink_manifest": str(source_sink_file),
        "route_candidate_manifest": str(route_file),
        "route_detector_incidence": str(incidence_file),
    }


def _write_signal_streams(path: Path, streams: Sequence[SignalStream]) -> None:
    _write_dataclass_csv(path, streams)


def _normalize_node_id(value: str) -> str:
    text = str(value).strip()
    return str(int(text)) if text.isdigit() else text


def _write_dataclass_csv(path: Path, rows: Sequence[Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("\n", encoding="utf-8")
        return
    fieldnames = list(rows[0].__dataclass_fields__)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row.__dict__)


def _artifact_manifest(paths: Sequence[Path]) -> list[dict[str, Any]]:
    return [
        {"path": str(path), "sha256": sha256_file(path), "bytes": path.stat().st_size}
        for path in paths
        if path.is_file()
    ]


def _write_blocked_manifest(
    output_dir: Path,
    preset: HamburgCorridorPreset,
    net_file: Path,
    *,
    reason: str,
    artifacts: dict[str, str],
) -> dict[str, Any]:
    report = {
        "schema_id": "torii.corridor-digital-twin.v1",
        "status": "blocked",
        "claim_status": "construction-incomplete",
        "preset_id": preset.preset_id,
        "net_file": str(net_file),
        "reason": reason,
        "artifacts": artifacts,
    }
    path = output_dir / "digital_twin.manifest.json"
    write_json(path, report)
    report["manifest_file"] = str(path)
    return report
