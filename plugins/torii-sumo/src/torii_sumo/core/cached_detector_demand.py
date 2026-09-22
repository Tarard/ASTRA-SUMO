from __future__ import annotations

import csv
import json
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

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
from .digital_twin import (
    CanonicalCount,
    CountStream,
    MapLane,
    SignalStream,
    parse_iso_datetime,
    parse_mapem,
)
from .digital_twin_mapping import (
    MapLaneBinding,
    aggregate_virtual_counts_to_complete_edge_sections,
    bind_map_lanes_to_network,
    bind_count_streams_to_network,
    bind_signal_streams_to_tls,
    build_virtual_sensor_aggregation,
    write_detector_mapping,
    write_edge_constraint_audit,
    write_route_sampler_edge_counts,
    write_tls_bindings,
    write_virtual_detector_mapping,
    write_virtual_e2_additional,
    write_virtual_expected_counts,
)
from .hamburg_official import parse_hamburg_count_streams, sha256_file, write_json
from .route_sampler import apply_departure_lane_targets, route_source_edges, run_route_sampler


def read_hamburg_count_stream_snapshot(path: Path) -> list[CountStream]:
    """Load an immutable ``fetch_hamburg_count_streams`` raw snapshot.

    The snapshot keeps the complete SensorThings pages for auditability.  This
    loader deliberately reuses the production Hamburg metadata parser instead
    of introducing a second interpretation of the official fields.
    """

    payload = json.loads(path.read_text(encoding="utf-8"))
    pages_by_node = payload.get("pages_by_node")
    if not isinstance(pages_by_node, Mapping) or not pages_by_node:
        raise ValueError("count stream snapshot has no pages_by_node mapping")
    values: list[Mapping[str, Any]] = []
    for node_id, pages in pages_by_node.items():
        if not isinstance(pages, list):
            raise ValueError(f"count stream pages for node {node_id!r} are not a list")
        for page in pages:
            if not isinstance(page, Mapping) or not isinstance(page.get("value"), list):
                raise ValueError(f"count stream snapshot page for node {node_id!r} has no value list")
            for value in page["value"]:
                if not isinstance(value, Mapping):
                    raise ValueError(f"count stream value for node {node_id!r} is not an object")
                values.append(value)
    streams = parse_hamburg_count_streams(values)
    by_id = {stream.stream_id: stream for stream in streams}
    if len(by_id) != len(streams):
        raise ValueError("count stream snapshot contains duplicate datastream ids")
    return sorted(by_id.values(), key=lambda item: (item.node_id, item.asset_id, item.stream_id))


def read_canonical_count_file(path: Path) -> list[CanonicalCount]:
    """Read Torii's canonical 15-minute count CSV without losing provenance."""

    with path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    if not rows:
        raise ValueError("canonical count file is empty")
    result: list[CanonicalCount] = []
    seen: set[tuple[int, int, int]] = set()
    for index, row in enumerate(rows, start=2):
        begin = _required_int(row, "begin", index)
        end = _required_int(row, "end", index)
        interval = _required_int(row, "interval_seconds", index)
        if end <= begin or end - begin != interval:
            raise ValueError(f"canonical count row {index} has an invalid interval {begin}-{end}")
        stream_id = _required_int(row, "stream_id", index)
        key = (stream_id, begin, end)
        if key in seen:
            raise ValueError(f"canonical count file repeats stream/bin {key}")
        seen.add(key)
        count_text = str(row.get("expected_total", row.get("count", ""))).strip()
        try:
            count = int(count_text)
        except ValueError as exc:
            raise ValueError(f"canonical count row {index} has invalid expected_total") from exc
        if count < 0:
            raise ValueError(f"canonical count row {index} has a negative expected_total")
        result.append(
            CanonicalCount(
                detector_id=_required_text(row, "detector_id", index),
                stream_id=stream_id,
                node_id=_required_text(row, "node_id", index),
                asset_id=_required_text(row, "asset_id", index),
                direction=str(row.get("direction", "")),
                lane_use=str(row.get("lane_use", "")),
                longitude=_required_float(row, "longitude", index),
                latitude=_required_float(row, "latitude", index),
                source_begin_utc=parse_iso_datetime(_required_text(row, "source_begin_utc", index)),
                source_end_utc=parse_iso_datetime(_required_text(row, "source_end_utc", index)),
                begin=begin,
                end=end,
                count=count,
                source_observation_count=_required_int(row, "source_observation_count", index),
                expected_source_observation_count=_required_int(
                    row, "expected_source_observation_count", index
                ),
                quality_status=_required_text(row, "quality_status", index),
            )
        )
    return sorted(result, key=lambda item: (item.stream_id, item.begin, item.end))


def read_map_lane_bindings(path: Path) -> list[MapLaneBinding]:
    """Load a frozen, already-audited MAP-to-SUMO lane contract."""

    with path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    if not rows:
        raise ValueError("MAP lane binding file is empty")
    result: list[MapLaneBinding] = []
    seen: set[tuple[str, str]] = set()
    for index, row in enumerate(rows, start=2):
        node_id = _required_text(row, "node_id", index)
        map_lane_id = _required_text(row, "map_lane_id", index)
        key = (_normalize_node(node_id), map_lane_id)
        if key in seen:
            raise ValueError(f"MAP lane binding file repeats normalized key {key}")
        seen.add(key)
        heading = str(row.get("heading_error_deg", "")).strip()
        result.append(
            MapLaneBinding(
                node_id=node_id,
                map_lane_id=map_lane_id,
                map_lane_type=_required_text(row, "map_lane_type", index),
                map_role=_required_text(row, "map_role", index),
                sumo_edge=_required_text(row, "sumo_edge", index),
                sumo_lane=_required_text(row, "sumo_lane", index),
                lane_position=_required_float(row, "lane_position", index),
                distance_m=_required_float(row, "distance_m", index),
                heading_error_deg=float(heading) if heading else None,
                mapping_confidence=_required_text(row, "mapping_confidence", index),
                mapping_status=_required_text(row, "mapping_status", index),
            )
        )
    return result


def read_signal_stream_snapshot(path: Path) -> list[SignalStream]:
    """Load the frozen official primary-signal metadata CSV."""

    with path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    if not rows:
        raise ValueError("signal stream snapshot is empty")
    result: list[SignalStream] = []
    seen: set[int] = set()
    for index, row in enumerate(rows, start=2):
        try:
            stream_id = int(str(row.get("stream_id", "")).strip())
        except ValueError as exc:
            raise ValueError(f"signal stream row {index} has an invalid stream_id") from exc
        if stream_id in seen:
            raise ValueError(f"signal stream snapshot repeats stream_id {stream_id}")
        seen.add(stream_id)
        result.append(
            SignalStream(
                stream_id=stream_id,
                thing_id=(
                    int(str(row["thing_id"]).strip())
                    if str(row.get("thing_id", "")).strip()
                    else None
                ),
                node_id=_required_text(row, "node_id", index),
                connection_id=_required_text(row, "connection_id", index),
                ingress_lane_id=_required_text(row, "ingress_lane_id", index),
                egress_lane_id=_required_text(row, "egress_lane_id", index),
                lane_type=_required_text(row, "lane_type", index),
                signal_group=_required_text(row, "signal_group", index),
                layer_name=_required_text(row, "layer_name", index),
                name=str(row.get("name", "")),
            )
        )
    return sorted(result, key=lambda item: item.stream_id)


def _read_official_endpoint_link_indices(
    path: Path | None,
) -> dict[tuple[str, str], tuple[int, ...]]:
    """Read the official movement-to-link-index disambiguator when supplied.

    A joined complex intersection can expose several controlled links along
    one MAP lane path.  The frozen Hamburg movement-endpoint artifact records
    which link index represents each official connection.  The generic binder
    remains topology-only when this optional evidence is absent.
    """

    if path is None:
        return {}
    source = Path(path).resolve(strict=True)
    payload = json.loads(source.read_text(encoding="utf-8"))
    rows = payload.get("movements") if isinstance(payload, Mapping) else None
    if not isinstance(rows, list):
        raise ValueError("movement endpoints artifact has no movements list")
    result: dict[tuple[str, str], tuple[int, ...]] = {}
    for index, row in enumerate(rows):
        if not isinstance(row, Mapping):
            raise ValueError(f"movement endpoint row {index} is not an object")
        node = _normalize_node(str(row.get("node_id", "")))
        connection_id = str(row.get("connection_id", "")).strip()
        if not node or not connection_id:
            continue
        allowed = row.get("allowed_replay_link_indices")
        values: list[int] = []
        if isinstance(allowed, list):
            values.extend(int(value) for value in allowed)
        elif row.get("sumo_link_index") is not None:
            values.append(int(row["sumo_link_index"]))
        if values:
            result[(node, connection_id)] = tuple(sorted(set(values)))
    return result


def prepare_cached_detector_demand_package(
    *,
    official_tls_manifest: Path,
    count_stream_snapshot: Path,
    canonical_count_file: Path,
    output_dir: Path,
    prefix: str,
    simulation_begin: int = 0,
    simulation_end: int = 9000,
    comparison_begin: int = 1800,
    comparison_end: int = 9000,
    interval: int = 900,
    excluded_route_edges: Iterable[str] = (),
    route_sampler_optimize: str | None = "full",
    route_sampler_script: Path | None = None,
    timeout_seconds: float = 300.0,
    allow_detector_cross_section_boundaries: bool = False,
    _candidate_contract: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build SUMO detector and route inputs from frozen Torii/Hamburg evidence.

    This is the deterministic resume path for a network that has already passed
    topology/TLS review.  It never downloads data, rebuilds the network, or
    re-runs nearest-lane assignment when an audited binding contract is supplied.
    """

    # The normal resume path consumes the strict, promotion-ready TLS
    # contract.  A geometry-preserving corridor candidate is deliberately a
    # separate review-pending contract, but it can use the exact same demand
    # builder once its hash-bound network and MAP lane bindings are supplied.
    contract = (
        dict(_candidate_contract)
        if _candidate_contract is not None
        else _read_official_tls_contract(official_tls_manifest)
    )
    net_file = contract["net_file"]
    map_xml_files = contract["map_xml_files"]
    map_lane_binding_file = contract["map_lane_binding_file"]
    tls_binding_file = contract.get("tls_binding_file")
    inputs = [
        official_tls_manifest,
        net_file,
        count_stream_snapshot,
        canonical_count_file,
        map_lane_binding_file,
        *map_xml_files,
    ]
    if tls_binding_file is not None:
        inputs.append(Path(tls_binding_file))
    missing = [str(path) for path in inputs if not path.is_file()]
    if missing:
        raise ValueError(f"cached demand inputs do not exist: {missing}")
    if not prefix.strip():
        raise ValueError("prefix is required")
    if interval <= 0 or simulation_end <= simulation_begin:
        raise ValueError("simulation interval must be positive")
    if (simulation_end - simulation_begin) % interval:
        raise ValueError("simulation window must contain whole intervals")
    if not simulation_begin <= comparison_begin < comparison_end <= simulation_end:
        raise ValueError("comparison window must be inside the simulation window")
    if (comparison_begin - simulation_begin) % interval or (comparison_end - comparison_begin) % interval:
        raise ValueError("comparison window must align with the detector interval")

    excluded = frozenset(edge.strip() for edge in excluded_route_edges if edge.strip())
    output_dir.mkdir(parents=True, exist_ok=True)
    detector_dir = output_dir / "detectors"
    demand_dir = output_dir / "demand"
    audit_dir = output_dir / "audit"
    for path in (detector_dir, demand_dir, audit_dir):
        path.mkdir(parents=True, exist_ok=True)

    streams = read_hamburg_count_stream_snapshot(count_stream_snapshot)
    canonical_counts = read_canonical_count_file(canonical_count_file)
    canonical_stream_ids = {row.stream_id for row in canonical_counts}
    metadata_stream_ids = {stream.stream_id for stream in streams}
    if canonical_stream_ids != metadata_stream_ids:
        raise ValueError(
            "canonical count streams differ from the metadata snapshot: "
            f"missing={sorted(metadata_stream_ids - canonical_stream_ids)}, "
            f"unexpected={sorted(canonical_stream_ids - metadata_stream_ids)}"
        )
    map_lanes: list[MapLane] = []
    for map_xml in map_xml_files:
        lanes, _connections = parse_mapem(map_xml)
        map_lanes.extend(lanes)
    bindings = read_map_lane_bindings(map_lane_binding_file)
    detector_mappings = bind_count_streams_to_network(
        net_file,
        streams,
        map_lanes,
        bindings,
        period=interval,
    )
    mapping_file = detector_dir / "detector_mapping.csv"
    write_detector_mapping(mapping_file, detector_mappings)
    active_mapping_count = sum(row.mapping_status == "active" for row in detector_mappings)
    if active_mapping_count != len(streams):
        inactive = [
            {"stream_id": row.stream_id, "status": row.mapping_status, "reason": row.mapping_reason}
            for row in detector_mappings
            if row.mapping_status != "active"
        ]
        raise ValueError(
            f"cached detector mapping is incomplete: active={active_mapping_count}/{len(streams)}; "
            f"inactive={inactive}"
        )

    aggregation = build_virtual_sensor_aggregation(
        detector_mappings,
        canonical_counts,
        bin_seconds=interval,
        expected_begin=simulation_begin,
        expected_end=simulation_end,
    )
    comparison_counts = [
        row for row in canonical_counts if row.begin >= comparison_begin and row.end <= comparison_end
    ]
    comparison_aggregation = build_virtual_sensor_aggregation(
        detector_mappings,
        comparison_counts,
        bin_seconds=interval,
        expected_begin=comparison_begin,
        expected_end=comparison_end,
    )
    detectors = list(aggregation.detectors)
    forbidden_detectors = [row.detector_id for row in detectors if row.edge_id in excluded]
    if forbidden_detectors:
        raise ValueError(f"excluded edges contain active detectors: {forbidden_detectors}")

    virtual_mapping_file = detector_dir / "virtual_detector_mapping.csv"
    simulation_expected_file = detector_dir / "virtual_expected_counts_simulation_15min.csv"
    comparison_expected_file = detector_dir / "virtual_expected_counts_comparison_15min.csv"
    e1_file = detector_dir / "e1_detectors.add.xml"
    e2_file = detector_dir / "e2_queue_detectors.add.xml"
    write_virtual_detector_mapping(virtual_mapping_file, aggregation.groups)
    write_virtual_expected_counts(simulation_expected_file, aggregation.expected_counts)
    write_virtual_expected_counts(comparison_expected_file, comparison_aggregation.expected_counts)
    write_e1_additional(
        e1_file,
        detectors,
        lanes=read_net_lanes(net_file),
        period=interval,
        output_file="e1_15min.xml",
    )
    write_virtual_e2_additional(
        e2_file,
        aggregation.groups,
        output_file="e2_15min.xml",
        period=interval,
    )

    edge_flows, edge_audits = aggregate_virtual_counts_to_complete_edge_sections(net_file, aggregation)
    edge_audit_file = audit_dir / "route_sampler_edge_constraints.csv"
    edge_count_file = demand_dir / "official_edge_counts_15min.xml"
    write_edge_constraint_audit(edge_audit_file, edge_audits)
    write_route_sampler_edge_counts(edge_count_file, edge_flows)
    route_support = _write_route_support(
        net_file,
        detectors,
        demand_dir,
        prefix=prefix,
        excluded_edges=excluded,
        allow_detector_cross_section_boundaries=allow_detector_cross_section_boundaries,
    )
    if route_support["status"] != "pass":
        raise ValueError(f"candidate route support failed: {route_support}")
    route_sampler = run_route_sampler(
        candidate_manifest_csv=Path(str(route_support["route_candidate_manifest"])),
        edge_data_file=edge_count_file,
        output_dir=demand_dir,
        prefix=prefix,
        begin=simulation_begin,
        end=simulation_end,
        interval=interval,
        seed=42,
        optimize=route_sampler_optimize,
        route_sampler_script=route_sampler_script,
        timeout_seconds=timeout_seconds,
    )
    lane_balance: dict[str, Any] = {"status": "not_requested"}
    if allow_detector_cross_section_boundaries and route_sampler.get("status") == "pass":
        lane_targets: dict[tuple[str, int], dict[str, int]] = {}
        lane_positions = {
            (group.sumo_edge, group.sumo_lane): group.lane_position
            for group in aggregation.groups
        }
        for expected in aggregation.expected_counts:
            lane_counts = lane_targets.setdefault((expected.sumo_edge, expected.begin), {})
            lane_counts[expected.sumo_lane] = lane_counts.get(expected.sumo_lane, 0) + expected.expected_total
        lane_balance = apply_departure_lane_targets(
            Path(str(route_sampler["demand_route_file"])),
            lane_targets,
            interval=interval,
            source_edges=route_source_edges(Path(str(route_sampler["demand_route_file"]))),
            lane_positions=lane_positions,
        )
        route_sampler["lane_balance"] = lane_balance
        if lane_balance.get("status") in {"pass", "review_required"}:
            route_sampler["demand_route_sha256"] = sha256_file(Path(str(route_sampler["demand_route_file"])))
            if lane_balance.get("status") == "review_required":
                route_sampler["claim_status"] = "lane-level-review-required"
        else:
            route_sampler["status"] = "partial"
            route_sampler["claim_status"] = "construction-incomplete"

    demand_path_text = str(route_sampler.get("demand_route_file", ""))
    demand_path = Path(demand_path_text) if demand_path_text else None
    candidate_route_file = Path(str(route_support["route_candidate_manifest"]))
    candidate_excluded_hits = _candidate_manifest_edge_hits(candidate_route_file, excluded)
    demand_excluded_hits = (
        _xml_route_edge_hits(demand_path, excluded)
        if demand_path is not None and demand_path.is_file()
        else sorted(excluded)
    )
    gates = {
        "metadata_stream_count": len(streams),
        "canonical_row_count": len(canonical_counts),
        "canonical_streams_equal_metadata": canonical_stream_ids == metadata_stream_ids,
        "active_detector_mapping_count": active_mapping_count,
        "all_detector_mappings_active": active_mapping_count == len(streams),
        "virtual_detector_count": len(detectors),
        "complete_edge_section_count": sum(row.constraint_status == "active" for row in edge_audits),
        "candidate_route_support": route_support.get("status"),
        "route_sampler_status": route_sampler.get("status"),
        "route_sampler_constraint_structure": route_sampler.get("constraint_structure", {}).get("status", "not_evaluated")
        if isinstance(route_sampler.get("constraint_structure"), Mapping)
        else "not_evaluated",
        "route_sampler_constraint_match_fraction": route_sampler.get("constraint_match_fraction"),
        "lane_balance": lane_balance.get("status", "not_requested"),
        "excluded_route_edges": sorted(excluded),
        "excluded_route_edge_hits_in_candidates": candidate_excluded_hits,
        "excluded_route_edge_hits_in_demand": demand_excluded_hits,
    }
    demand_generation_status = (
        "review_required"
        if lane_balance.get("status") == "review_required"
        else "pass"
        if route_sampler.get("status") == "pass"
        and route_sampler.get("constraint_match_fraction") == 1.0
        and (
            not isinstance(route_sampler.get("constraint_structure"), Mapping)
            or route_sampler.get("constraint_structure", {}).get("status") == "pass"
        )
        and not candidate_excluded_hits
        and not demand_excluded_hits
        else "fail"
    )
    artifact_paths = [
        official_tls_manifest,
        net_file,
        count_stream_snapshot,
        canonical_count_file,
        map_lane_binding_file,
        *map_xml_files,
        mapping_file,
        virtual_mapping_file,
        simulation_expected_file,
        comparison_expected_file,
        e1_file,
        e2_file,
        edge_audit_file,
        edge_count_file,
        Path(str(route_support["source_sink_manifest"])),
        Path(str(route_support["route_candidate_manifest"])),
        Path(str(route_support["route_detector_incidence"])),
    ]
    if tls_binding_file is not None:
        artifact_paths.append(Path(tls_binding_file))
    for key in ("candidate_route_file", "demand_route_file", "mismatch_file", "command_manifest"):
        value = str(route_sampler.get(key, "")).strip()
        if value:
            artifact_paths.append(Path(value))
    unique_artifact_paths = list(dict.fromkeys(path.resolve() for path in artifact_paths if path.is_file()))
    manifest = {
        "schema_id": "torii.cached-detector-demand.v1",
        "status": "partial" if demand_generation_status in {"pass", "review_required"} else "fail",
        "claim_status": (
            "detector-demand-inputs-ready-lane-review-pending"
            if demand_generation_status == "review_required"
            else "detector-demand-inputs-ready-topology-review-pending"
            if demand_generation_status == "pass"
            else "construction-invalid"
        ),
        "demand_generation_status": demand_generation_status,
        "network": {"path": str(net_file), "sha256": sha256_file(net_file)},
        "official_tls_contract": contract["audit"] if _candidate_contract is None else None,
        "candidate_topology_contract": contract["audit"] if _candidate_contract is not None else None,
        "window": {
            "simulation_begin": simulation_begin,
            "simulation_end": simulation_end,
            "comparison_begin": comparison_begin,
            "comparison_end": comparison_end,
            "interval": interval,
            "warmup_seconds": comparison_begin - simulation_begin,
        },
        "gates": gates,
        "route_support": route_support,
        "boundary_policy": (
            "official_detector_cross_sections_as_open_source_sink_ports"
            if allow_detector_cross_section_boundaries
            else "network_boundaries_only"
        ),
        "route_sampler": route_sampler,
        "artifacts": [
            {"path": str(path), "sha256": sha256_file(path), "bytes": path.stat().st_size}
            for path in unique_artifact_paths
        ],
        "identifiability": {
            "od_matrix_directly_observed": False,
            "turn_flows_directly_observed": False,
            "open_detector_cross_section_boundaries": allow_detector_cross_section_boundaries,
            "policy": (
                "routeSampler produces one detector-constrained plausible realization; "
                "it is not a uniquely identified OD matrix or trajectory reconstruction. "
                + (
                    "When detector cross-sections are enabled, measured edges are explicit "
                    "local entry/exit ports; this reproduces the observed cut, not upstream demand."
                    if allow_detector_cross_section_boundaries
                    else ""
                )
            ),
        },
        "next_gate": (
            "accept the existing Netedit/background visual review for the v9 topology, then run the "
            "full signal-history replay and real-vs-virtual detector comparison"
        ),
    }
    manifest_file = output_dir / "cached_detector_demand.manifest.json"
    write_json(manifest_file, manifest)
    manifest["manifest_file"] = str(manifest_file)
    manifest["manifest_sha256"] = sha256_file(manifest_file)
    return manifest


def materialize_hamburg_corridor_candidate_map_bindings(
    *,
    candidate_manifest: Path,
    candidate_net_file: Path,
    expected_candidate_net_sha256: str,
    map_xml_files: Sequence[Path],
    output_dir: Path,
) -> dict[str, Any]:
    """Reproject official MAP lanes onto the exact corridor candidate network.

    This stage is intentionally separate from the older frozen binding CSV:
    geometry-preserving TLS materialization can change which OSM edge is the
    nearest lane even when the MAP evidence is unchanged.  The resulting CSV
    is hash-bound and is the only binding contract accepted by the candidate
    demand builder.
    """

    manifest_path = Path(candidate_manifest).resolve(strict=True)
    net_path = Path(candidate_net_file).resolve(strict=True)
    maps = [Path(path).resolve(strict=True) for path in map_xml_files]
    if not maps:
        raise ValueError("at least one official MAP XML file is required")
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    if payload.get("schema_id") != "torii.hamburg-sandtorkai-corridor-tls-materializer/v1":
        raise ValueError("candidate manifest is not the Hamburg corridor TLS materializer schema")
    artifact_network = Path(str(payload.get("artifacts", {}).get("network", ""))).resolve()
    if artifact_network != net_path:
        raise ValueError("candidate manifest network artifact does not match candidate_net_file")
    _require_hash_bound_file(net_path, expected_candidate_net_sha256, "candidate network")
    map_lanes: list[MapLane] = []
    for map_file in maps:
        lanes, _connections = parse_mapem(map_file)
        map_lanes.extend(lanes)
    bindings = bind_map_lanes_to_network(net_path, map_lanes)
    status_counts: dict[str, int] = {}
    for binding in bindings:
        status_counts[binding.mapping_status] = status_counts.get(binding.mapping_status, 0) + 1
    output_dir = Path(output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    binding_file = output_dir / "official_map_lane_to_sumo_candidate.csv"
    _write_dataclass_rows(binding_file, bindings)
    report = {
        "schema_id": "torii.hamburg-corridor-candidate-map-binding/v1",
        "status": "pass" if status_counts == {"active": len(bindings)} else "blocked",
        "claim_status": "candidate-map-lane-binding-review",
        "candidate_manifest": {"path": str(manifest_path), "sha256": sha256_file(manifest_path)},
        "network": {"path": str(net_path), "sha256": sha256_file(net_path)},
        "map_evidence": [
            {"path": str(path), "sha256": sha256_file(path), "bytes": path.stat().st_size}
            for path in maps
        ],
        "mapping": {
            "lane_count": len(bindings),
            "status_counts": status_counts,
            "required_vehicle_lane_count": 46,
            "all_required_vehicle_lanes_active": len(bindings) == 46 and status_counts == {"active": 46},
            "policy": "recompute nearest compatible SUMO lane on the candidate; never reuse a pre-materialization edge assignment",
        },
        "artifact": {"path": str(binding_file), "sha256": sha256_file(binding_file)},
    }
    manifest_file = output_dir / "candidate_map_binding.manifest.json"
    write_json(manifest_file, report)
    report["manifest_file"] = str(manifest_file)
    report["manifest_sha256"] = sha256_file(manifest_file)
    return report


def _write_dataclass_rows(path: Path, rows: Sequence[Any]) -> None:
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


def materialize_hamburg_corridor_candidate_signal_bindings(
    *,
    candidate_manifest: Path,
    candidate_net_file: Path,
    expected_candidate_net_sha256: str,
    map_lane_binding_file: Path,
    expected_map_lane_binding_sha256: str,
    signal_stream_file: Path,
    movement_endpoints_file: Path | None = None,
    expected_movement_endpoints_sha256: str | None = None,
    output_dir: Path,
) -> dict[str, Any]:
    """Bind frozen primary-signal metadata to the candidate's controlled links."""

    manifest_path = Path(candidate_manifest).resolve(strict=True)
    net_path = Path(candidate_net_file).resolve(strict=True)
    binding_path = Path(map_lane_binding_file).resolve(strict=True)
    stream_path = Path(signal_stream_file).resolve(strict=True)
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    if payload.get("schema_id") != "torii.hamburg-sandtorkai-corridor-tls-materializer/v1":
        raise ValueError("candidate manifest is not the Hamburg corridor TLS materializer schema")
    artifact_network = Path(str(payload.get("artifacts", {}).get("network", ""))).resolve()
    if artifact_network != net_path:
        raise ValueError("candidate manifest network artifact does not match candidate_net_file")
    _require_hash_bound_file(net_path, expected_candidate_net_sha256, "candidate network")
    _require_hash_bound_file(binding_path, expected_map_lane_binding_sha256, "MAP lane bindings")
    streams = read_signal_stream_snapshot(stream_path)
    endpoint_path = Path(movement_endpoints_file).resolve(strict=True) if movement_endpoints_file else None
    if endpoint_path is not None:
        if not expected_movement_endpoints_sha256:
            raise ValueError("expected_movement_endpoints_sha256 is required with movement_endpoints_file")
        _require_hash_bound_file(
            endpoint_path,
            expected_movement_endpoints_sha256,
            "movement endpoints",
        )
    endpoint_indices = _read_official_endpoint_link_indices(endpoint_path)
    bindings = bind_signal_streams_to_tls(
        net_path,
        streams,
        read_map_lane_bindings(binding_path),
        official_link_indices=endpoint_indices,
    )
    status_counts: dict[str, int] = {}
    for binding in bindings:
        status_counts[binding.mapping_status] = status_counts.get(binding.mapping_status, 0) + 1
    output_dir = Path(output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    binding_file = output_dir / "tls_bindings_candidate.csv"
    write_tls_bindings(binding_file, bindings)
    report = {
        "schema_id": "torii.hamburg-corridor-candidate-signal-binding/v1",
        "status": "pass" if status_counts == {"active": 18, "redundant": 9} else "blocked",
        "claim_status": "candidate-signal-binding-review",
        "candidate_manifest": {"path": str(manifest_path), "sha256": sha256_file(manifest_path)},
        "network": {"path": str(net_path), "sha256": sha256_file(net_path)},
        "map_lane_bindings": {"path": str(binding_path), "sha256": sha256_file(binding_path)},
        "signal_stream_metadata": {"path": str(stream_path), "sha256": sha256_file(stream_path), "count": len(streams)},
        "movement_endpoints": (
            {
                "path": str(endpoint_path),
                "sha256": sha256_file(endpoint_path),
                "sha256_expected": expected_movement_endpoints_sha256,
                "used_for_ambiguous_shared-controller_paths": bool(endpoint_indices),
            }
            if movement_endpoints_file is not None
            else None
        ),
        "binding_counts": status_counts,
        "artifact": {"path": str(binding_file), "sha256": sha256_file(binding_file)},
        "historical_observations": "not_available_due_primary_api_outage",
        "historical_replay": "blocked",
    }
    manifest_file = output_dir / "candidate_signal_binding.manifest.json"
    write_json(manifest_file, report)
    report["manifest_file"] = str(manifest_file)
    report["manifest_sha256"] = sha256_file(manifest_file)
    return report


def prepare_hamburg_corridor_candidate_package(
    *,
    candidate_manifest: Path,
    candidate_net_file: Path,
    expected_candidate_net_sha256: str,
    map_xml_files: Sequence[Path],
    signal_stream_file: Path,
    movement_endpoints_file: Path | None = None,
    expected_movement_endpoints_sha256: str | None = None,
    count_stream_snapshot: Path,
    canonical_count_file: Path,
    output_dir: Path,
    prefix: str = "hamburg_sandtorkai_corridor_candidate",
    simulation_begin: int = 0,
    simulation_end: int = 7200,
    comparison_begin: int = 1800,
    comparison_end: int = 7200,
    interval: int = 900,
    excluded_route_edges: Iterable[str] = (),
    route_sampler_optimize: str | None = "full",
    route_sampler_script: Path | None = None,
    timeout_seconds: float = 300.0,
    allow_detector_cross_section_boundaries: bool = False,
) -> dict[str, Any]:
    """Run candidate MAP, signal-binding, and demand stages as one Torii package."""

    output_dir = Path(output_dir).resolve()
    map_report = materialize_hamburg_corridor_candidate_map_bindings(
        candidate_manifest=candidate_manifest,
        candidate_net_file=candidate_net_file,
        expected_candidate_net_sha256=expected_candidate_net_sha256,
        map_xml_files=map_xml_files,
        output_dir=output_dir / "map_binding",
    )
    map_file = Path(str(map_report["artifact"]["path"]))
    signal_report = materialize_hamburg_corridor_candidate_signal_bindings(
        candidate_manifest=candidate_manifest,
        candidate_net_file=candidate_net_file,
        expected_candidate_net_sha256=expected_candidate_net_sha256,
        map_lane_binding_file=map_file,
        expected_map_lane_binding_sha256=str(map_report["artifact"]["sha256"]),
        signal_stream_file=signal_stream_file,
        movement_endpoints_file=movement_endpoints_file,
        expected_movement_endpoints_sha256=expected_movement_endpoints_sha256,
        output_dir=output_dir / "signal_binding",
    )
    demand_report = prepare_corridor_candidate_detector_demand_package(
        candidate_manifest=candidate_manifest,
        candidate_net_file=candidate_net_file,
        expected_candidate_net_sha256=expected_candidate_net_sha256,
        map_xml_files=map_xml_files,
        map_lane_binding_file=map_file,
        expected_map_lane_binding_sha256=str(map_report["artifact"]["sha256"]),
        count_stream_snapshot=count_stream_snapshot,
        canonical_count_file=canonical_count_file,
        output_dir=output_dir / "demand",
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
    status = (
        "partial"
        if map_report.get("status") == "pass"
        and signal_report.get("status") == "pass"
        and demand_report.get("status") == "partial"
        else "blocked"
    )
    package = {
        "schema_id": "torii.hamburg-sandtorkai-corridor-candidate-package/v1",
        "status": status,
        "claim_status": "candidate-corridor-package-review",
        "candidate_topology": {
            "manifest": str(Path(candidate_manifest).resolve(strict=True)),
            "network": str(Path(candidate_net_file).resolve(strict=True)),
            "network_sha256": expected_candidate_net_sha256,
            "promotion_gate": "blocked",
        },
        "stages": {
            "map_binding": map_report,
            "signal_binding": signal_report,
            "detector_demand": demand_report,
        },
        "historical_signal_replay": "blocked_pending_official_observations",
        "policy": (
            "This package makes a blocked geometry/TLS candidate reproducible; it never upgrades the "
            "candidate topology or substitutes snapshot metadata for historical signal observations."
        ),
    }
    manifest_file = output_dir / "corridor_candidate_package.manifest.json"
    write_json(manifest_file, package)
    package["manifest_file"] = str(manifest_file)
    package["manifest_sha256"] = sha256_file(manifest_file)
    return package


def prepare_corridor_candidate_detector_demand_package(
    *,
    candidate_manifest: Path,
    candidate_net_file: Path,
    expected_candidate_net_sha256: str,
    map_xml_files: Sequence[Path],
    map_lane_binding_file: Path,
    expected_map_lane_binding_sha256: str,
    count_stream_snapshot: Path,
    canonical_count_file: Path,
    output_dir: Path,
    prefix: str,
    simulation_begin: int = 0,
    simulation_end: int = 9000,
    comparison_begin: int = 1800,
    comparison_end: int = 9000,
    interval: int = 900,
    excluded_route_edges: Iterable[str] = (),
    route_sampler_optimize: str | None = "full",
    route_sampler_script: Path | None = None,
    timeout_seconds: float = 300.0,
    allow_detector_cross_section_boundaries: bool = False,
) -> dict[str, Any]:
    """Build detector/E1/E2/route inputs for a review-pending corridor net.

    The candidate manifest and network are hash-bound, while MAP lane bindings
    and official count files remain frozen evidence from the Hamburg workflow.
    This path intentionally does not pretend that a blocked geometry review is
    promotion-ready; it only makes the candidate's sensors and demand
    reproducible so the visual/topology gate can be evaluated with real data.
    """

    manifest_path = Path(candidate_manifest).resolve(strict=True)
    net_path = Path(candidate_net_file).resolve(strict=True)
    binding_path = Path(map_lane_binding_file).resolve(strict=True)
    maps = [Path(path).resolve(strict=True) for path in map_xml_files]
    if not maps:
        raise ValueError("at least one official MAP XML file is required")
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    if payload.get("schema_id") != "torii.hamburg-sandtorkai-corridor-tls-materializer/v1":
        raise ValueError("candidate manifest is not the Hamburg corridor TLS materializer schema")
    if str(payload.get("claim_status", "")) != "official-static-topology-candidate":
        raise ValueError("candidate manifest has an unexpected claim status")
    artifact_network = Path(str(payload.get("artifacts", {}).get("network", ""))).resolve()
    if artifact_network != net_path:
        raise ValueError("candidate manifest network artifact does not match candidate_net_file")
    _require_hash_bound_file(net_path, expected_candidate_net_sha256, "candidate network")
    _require_hash_bound_file(binding_path, expected_map_lane_binding_sha256, "MAP lane bindings")
    contract = {
        "net_file": net_path,
        "map_xml_files": maps,
        "map_lane_binding_file": binding_path,
        "tls_binding_file": None,
        "audit": {
            "manifest_file": str(manifest_path),
            "manifest_sha256": sha256_file(manifest_path),
            "status": "pass" if payload.get("status") in {"review_ready", "blocked"} else "fail",
            "candidate_status": payload.get("status"),
            "claim_status": payload.get("claim_status"),
            "topology_review_gate": payload.get("automatic_promotion_gate", "blocked"),
            "policy": (
                "candidate demand is valid only as review evidence; it does not promote a blocked "
                "surface-overlap or historical-signal gate"
            ),
        },
    }
    return prepare_cached_detector_demand_package(
        official_tls_manifest=manifest_path,
        count_stream_snapshot=count_stream_snapshot,
        canonical_count_file=canonical_count_file,
        output_dir=output_dir,
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
        _candidate_contract=contract,
    )


def _require_hash_bound_file(path: Path, expected: str, label: str) -> None:
    if not expected or sha256_file(path).lower() != expected.lower():
        raise ValueError(f"{label} SHA-256 mismatch: expected={expected}, actual={sha256_file(path)}")


def _write_route_support(
    net_file: Path,
    detectors: Sequence[Detector],
    output_dir: Path,
    *,
    prefix: str,
    excluded_edges: frozenset[str],
    allow_detector_cross_section_boundaries: bool = False,
) -> dict[str, Any]:
    edges, connections = read_net(net_file)
    usable_edges = {edge_id: edge for edge_id, edge in edges.items() if edge_id not in excluded_edges}
    usable_connections = {
        edge_id: {target for target in targets if target not in excluded_edges}
        for edge_id, targets in connections.items()
        if edge_id not in excluded_edges
    }
    active = active_detectors(list(detectors))
    network_sources, network_sinks = boundary_edges(usable_edges, usable_connections)
    measured_edges = (
        sorted({detector.edge_id for detector in active if detector.edge_id in usable_edges})
        if allow_detector_cross_section_boundaries
        else []
    )
    # In the opt-in local-twin mode, the measured cross-sections are the
    # declared boundary of the model.  Do not silently mix them with the
    # network's distant fringe: that would re-introduce unobserved feeder
    # demand and make lane-level sensor calibration underdetermined.
    if allow_detector_cross_section_boundaries and measured_edges:
        sources = measured_edges
        sinks = measured_edges
    else:
        sources = sorted(network_sources)
        sinks = sorted(network_sinks)
    anchored = build_detector_anchored_routes(active, sources, sinks, usable_connections, max_hops=80)
    boundary = build_boundary_routes(
        sources,
        sinks,
        usable_connections,
        max_routes=500,
        max_hops=80,
        min_edges=1 if allow_detector_cross_section_boundaries else 2,
    )
    routes = [
        route
        for route in merge_routes(anchored, boundary, max_routes=500)
        if not excluded_edges.intersection(route.edges)
    ]
    incidence = route_detector_incidence(routes, active)
    covered = {str(row["detector_id"]) for row in incidence if int(row["incidence"]) == 1}
    source_sink_file = output_dir / f"{prefix}_source_sink_manifest.csv"
    route_file = output_dir / f"{prefix}_route_candidate_manifest.csv"
    incidence_file = output_dir / f"{prefix}_route_detector_incidence.csv"
    write_csv(
        source_sink_file,
        source_sink_rows(usable_edges, sources, sinks, measured_edge_ids=measured_edges),
        ["role", "edge_id", "from_node", "to_node", "length", "reason"],
    )
    write_csv(
        route_file,
        route_rows(routes, usable_edges),
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
    return {
        "status": "pass" if routes and len(covered) == len(active) else "fail",
        "source_count": len(sources),
        "sink_count": len(sinks),
        "network_source_count": len(network_sources),
        "network_sink_count": len(network_sinks),
        "measured_detector_edge_count": len(measured_edges),
        "measured_detector_edges": measured_edges,
        "boundary_policy": (
            "official_detector_cross_sections_as_open_source_sink_ports"
            if allow_detector_cross_section_boundaries
            else "network_boundaries_only"
        ),
        "route_count": len(routes),
        "active_detector_count": len(active),
        "covered_detector_count": len(covered),
        "excluded_edges": sorted(excluded_edges),
        "excluded_edge_route_count": sum(
            bool(excluded_edges.intersection(route.edges)) for route in routes
        ),
        "source_sink_manifest": str(source_sink_file),
        "route_candidate_manifest": str(route_file),
        "route_detector_incidence": str(incidence_file),
    }


def _read_official_tls_contract(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise ValueError(f"official TLS manifest does not exist: {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("status") != "pass" or payload.get("claim_status") != "official-tls-topology-ready":
        raise ValueError("official TLS manifest is not an accepted topology-ready result")
    artifacts = payload.get("artifacts")
    if not isinstance(artifacts, list):
        raise ValueError("official TLS manifest has no artifact list")
    by_role: dict[str, Mapping[str, Any]] = {}
    for row in artifacts:
        if not isinstance(row, Mapping):
            raise ValueError("official TLS manifest artifact is not an object")
        role = str(row.get("role", "")).strip()
        if not role:
            raise ValueError("official TLS manifest artifact has no role")
        if role in by_role:
            raise ValueError(f"official TLS manifest repeats artifact role {role!r}")
        by_role[role] = row
    required_roles = {
        "rebuilt_net_file",
        "effective_map_lane_bindings",
        "official_primary_signal_tls_bindings",
        "official_map_lane_contract_projection",
    }
    missing_roles = sorted(required_roles - set(by_role))
    if missing_roles:
        raise ValueError(f"official TLS manifest lacks required artifact roles: {missing_roles}")

    verified_roles: dict[str, dict[str, Any]] = {}
    for role in sorted(required_roles):
        row = by_role[role]
        artifact_path = Path(str(row.get("path", "")))
        expected_hash = str(row.get("sha256", "")).strip()
        if not artifact_path.is_file() or not expected_hash:
            raise ValueError(f"official TLS artifact {role!r} is missing or unhashed: {artifact_path}")
        actual_hash = sha256_file(artifact_path)
        if actual_hash != expected_hash:
            raise ValueError(
                f"official TLS artifact {role!r} hash mismatch: expected={expected_hash}, actual={actual_hash}"
            )
        verified_roles[role] = {
            "path": str(artifact_path),
            "sha256": actual_hash,
            "bytes": artifact_path.stat().st_size,
        }

    net_file = Path(str(by_role["rebuilt_net_file"]["path"]))
    reported_net = Path(str(payload.get("rebuilt_net_file", "")))
    if net_file.resolve() != reported_net.resolve():
        raise ValueError("official TLS rebuilt_net_file disagrees with its hash-bound artifact role")
    map_audit = payload.get("effective_map_lane_binding_audit")
    if not isinstance(map_audit, Mapping) or map_audit.get("status") != "pass":
        raise ValueError("official TLS effective MAP lane binding audit did not pass")
    if int(map_audit.get("required_lane_count", -1)) != 44 or int(
        map_audit.get("active_required_lane_count", -1)
    ) != 44:
        raise ValueError("official TLS effective MAP lane binding audit is not 44/44 active")
    if any(map_audit.get(key) for key in ("missing", "inactive", "duplicate_or_non_unique")):
        raise ValueError("official TLS effective MAP lane binding audit contains non-empty errors")
    tls_audit = payload.get("primary_stream_tls_binding_audit")
    if not isinstance(tls_audit, Mapping) or tls_audit.get("status") != "pass":
        raise ValueError("official primary-signal TLS binding audit did not pass")
    status_counts = tls_audit.get("status_counts")
    if (
        not isinstance(status_counts, Mapping)
        or int(status_counts.get("active", -1)) != 18
        or int(status_counts.get("redundant", -1)) != 9
        or int(tls_audit.get("binding_row_count", -1)) != 27
    ):
        raise ValueError("official primary-signal TLS binding audit is not 18 active + 9 redundant")
    if tls_audit.get("errors"):
        raise ValueError("official primary-signal TLS binding audit contains errors")
    endpoint_audit = payload.get("official_movement_physical_endpoint_audit")
    if not isinstance(endpoint_audit, Mapping) or endpoint_audit.get("status") != "pass":
        raise ValueError("official movement physical endpoint audit did not pass")
    if (
        int(endpoint_audit.get("expected_movement_count", -1)) != 33
        or int(endpoint_audit.get("movement_count", -1)) != 33
        or int(endpoint_audit.get("unique_physical_endpoint_count", -1)) != 33
        or int(endpoint_audit.get("validated_movement_count", -1)) != 33
    ):
        raise ValueError("official movement endpoint audit is not 33/33 unique and validated")
    if endpoint_audit.get("errors"):
        raise ValueError("official movement endpoint audit contains errors")
    automatic_promotion_gate = str(payload.get("automatic_promotion_gate", "missing"))

    asset_inventory = payload.get("official_asset_inventory")
    if not isinstance(asset_inventory, list):
        raise ValueError("official TLS manifest has no official asset inventory")
    map_xml_files: list[Path] = []
    verified_assets: list[dict[str, Any]] = []
    inventory_keys: set[tuple[str, str]] = set()
    for row in asset_inventory:
        if not isinstance(row, Mapping):
            raise ValueError("official asset inventory row is not an object")
        key = (str(row.get("node_id", "")), str(row.get("kind", "")))
        if key in inventory_keys:
            raise ValueError(f"official asset inventory repeats {key}")
        inventory_keys.add(key)
        asset_path = Path(str(row.get("path", "")))
        expected_hash = str(row.get("sha256", ""))
        if not asset_path.is_file() or sha256_file(asset_path) != expected_hash:
            raise ValueError(f"official asset inventory hash check failed for {key}: {asset_path}")
        verified_assets.append(
            {
                "node_id": key[0],
                "kind": key[1],
                "path": str(asset_path),
                "sha256": expected_hash,
                "bytes": asset_path.stat().st_size,
            }
        )
        if key[1] == "map_xml":
            map_xml_files.append(asset_path)
    expected_inventory = {
        (node_id, kind) for node_id in ("0228", "2421", "2394") for kind in ("map_xml", "ocit_xml")
    }
    if inventory_keys != expected_inventory:
        raise ValueError(
            "official asset inventory is not exactly the three MAP/OCIT pairs: "
            f"missing={sorted(expected_inventory - inventory_keys)}, "
            f"unexpected={sorted(inventory_keys - expected_inventory)}"
        )

    tls_binding_file = Path(str(by_role["official_primary_signal_tls_bindings"]["path"]))
    with tls_binding_file.open(encoding="utf-8", newline="") as handle:
        tls_rows = list(csv.DictReader(handle))
    tls_statuses = {"active": 0, "redundant": 0}
    tls_stream_ids: set[str] = set()
    for row in tls_rows:
        status = str(row.get("mapping_status", ""))
        if status not in tls_statuses:
            raise ValueError(f"hash-bound TLS binding contains unexpected status {status!r}")
        tls_statuses[status] += 1
        stream_id = str(row.get("stream_id", "")).strip()
        if not stream_id or stream_id in tls_stream_ids:
            raise ValueError(f"hash-bound TLS binding has missing/duplicate stream id {stream_id!r}")
        tls_stream_ids.add(stream_id)
    if tls_statuses != {"active": 18, "redundant": 9}:
        raise ValueError(f"hash-bound TLS binding CSV is not 18 active + 9 redundant: {tls_statuses}")

    net_root = ET.parse(net_file).getroot()
    lane_by_edge_index = {
        (str(edge.get("id", "")), str(lane.get("index", ""))): str(lane.get("id", ""))
        for edge in net_root.findall("edge")
        for lane in edge.findall("lane")
    }
    tls_widths: dict[str, int] = {}
    for logic in net_root.findall("tlLogic"):
        tls_id = str(logic.get("id", ""))
        widths = {len(str(phase.get("state", ""))) for phase in logic.findall("phase")}
        if len(widths) != 1:
            raise ValueError(f"SUMO TLS {tls_id!r} has inconsistent phase widths: {sorted(widths)}")
        tls_widths[tls_id] = next(iter(widths))
    controlled_targets: set[tuple[str, int, str, str]] = set()
    for connection in net_root.findall("connection"):
        tls_id = str(connection.get("tl", ""))
        link_text = str(connection.get("linkIndex", ""))
        if not tls_id or not link_text:
            continue
        from_lane = lane_by_edge_index.get(
            (str(connection.get("from", "")), str(connection.get("fromLane", ""))),
            "",
        )
        to_lane = lane_by_edge_index.get(
            (str(connection.get("to", "")), str(connection.get("toLane", ""))),
            "",
        )
        controlled_targets.add((tls_id, int(link_text), from_lane, to_lane))
    tls_target_errors: list[dict[str, Any]] = []
    validated_targets: set[tuple[str, int, str, str]] = set()
    for row in tls_rows:
        tls_id = str(row.get("sumo_tls_id", "")).strip()
        try:
            link_index = int(str(row.get("sumo_link_index", "")))
        except ValueError:
            tls_target_errors.append({"stream_id": row.get("stream_id"), "error": "invalid_link_index"})
            continue
        target = (
            tls_id,
            link_index,
            str(row.get("sumo_from_lane", "")).strip(),
            str(row.get("sumo_to_lane", "")).strip(),
        )
        width = tls_widths.get(tls_id)
        if width is None:
            tls_target_errors.append({"stream_id": row.get("stream_id"), "error": "missing_tls"})
        elif not 0 <= link_index < width:
            tls_target_errors.append({"stream_id": row.get("stream_id"), "error": "link_out_of_range"})
        elif target not in controlled_targets:
            tls_target_errors.append({"stream_id": row.get("stream_id"), "error": "missing_controlled_arc"})
        else:
            validated_targets.add(target)
    if tls_target_errors:
        raise ValueError(f"hash-bound TLS binding targets do not exist in the v9 network: {tls_target_errors}")

    return {
        "net_file": net_file,
        "map_xml_files": sorted(map_xml_files, key=str),
        "map_lane_binding_file": Path(str(by_role["effective_map_lane_bindings"]["path"])),
        "tls_binding_file": tls_binding_file,
        "audit": {
            "manifest_file": str(path),
            "manifest_sha256": sha256_file(path),
            "status": "pass",
            "verified_roles": verified_roles,
            "verified_assets": verified_assets,
            "effective_map_lane_binding_count": 44,
            "official_tls_binding_status_counts": tls_statuses,
            "official_tls_binding_target_row_count": len(tls_rows),
            "official_tls_binding_unique_target_count": len(validated_targets),
            "official_tls_binding_target_errors": [],
            "official_movement_physical_endpoint_count": 33,
            "official_movement_unique_endpoint_count": 33,
            "automatic_promotion_gate": automatic_promotion_gate,
            "errors": [],
            "package_claim_policy": (
                "demand inputs may be generated while the topology automatic-promotion gate remains "
                "blocked, but the whole twin stays review-pending"
            ),
            "policy": (
                "consume only hash-bound v9 official lane/TLS artifacts; never replace them with "
                "generic nearest-lane or generic TLS rebinding"
            ),
        },
    }


def _required_text(row: Mapping[str, str], field: str, index: int) -> str:
    value = str(row.get(field, "")).strip()
    if not value:
        raise ValueError(f"row {index} is missing {field}")
    return value


def _required_int(row: Mapping[str, str], field: str, index: int) -> int:
    value = _required_text(row, field, index)
    try:
        return int(value)
    except ValueError as exc:
        raise ValueError(f"row {index} has invalid integer {field}") from exc


def _required_float(row: Mapping[str, str], field: str, index: int) -> float:
    value = _required_text(row, field, index)
    try:
        return float(value)
    except ValueError as exc:
        raise ValueError(f"row {index} has invalid number {field}") from exc


def _normalize_node(value: str) -> str:
    text = value.strip()
    return str(int(text)) if text.isdigit() else text


def _candidate_manifest_edge_hits(path: Path, excluded_edges: frozenset[str]) -> list[str]:
    with path.open(encoding="utf-8", newline="") as handle:
        routes = csv.DictReader(handle)
        hits = {
            edge
            for row in routes
            for edge in str(row.get("edges", "")).split()
            if edge in excluded_edges
        }
    return sorted(hits)


def _xml_route_edge_hits(path: Path, excluded_edges: frozenset[str]) -> list[str]:
    hits = {
        edge
        for element in ET.parse(path).getroot().iter()
        for edge in str(element.attrib.get("edges", "")).split()
        if edge in excluded_edges
    }
    return sorted(hits)
