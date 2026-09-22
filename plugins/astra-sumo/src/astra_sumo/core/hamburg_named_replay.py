"""Build a diagnostic SUMO replay from the frozen Hamburg count window."""

from __future__ import annotations

import csv
import json
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Mapping

from .artifact_io import write_json_atomic
from .cached_detector_demand import (
    _write_route_support,
    read_canonical_count_file,
    read_hamburg_count_stream_snapshot,
)
from .candidate_contracts import file_sha256
from .command_runner import run_command
from .detector_demand import audit_expected_to_e1_strict, write_e1_additional
from .digital_twin_mapping import (
    DetectorMapping,
    aggregate_virtual_counts_to_complete_edge_sections,
    build_virtual_sensor_aggregation,
    write_detector_mapping,
    write_edge_constraint_audit,
    write_route_sampler_edge_counts,
    write_virtual_detector_mapping,
    write_virtual_e2_additional,
    write_virtual_expected_counts,
)
from .route_sampler import apply_departure_lane_targets, route_source_edges, run_route_sampler
from .tls_replay import run_tls_detector_replay


NAMED_REPLAY_SCHEMA = "torii.hamburg-named-replay/v2"


class HamburgNamedReplayError(ValueError):
    """Raised when a diagnostic replay cannot be constructed safely."""


def materialize_hamburg_named_replay(
    *,
    net_file: Path,
    signal_binding_manifest: Path,
    detector_binding_manifest: Path,
    count_scope_manifest: Path,
    count_stream_snapshot: Path,
    canonical_count_file: Path,
    output_dir: Path,
    signal_observation_manifest: Path | None = None,
    route_sampler_script: Path | None = None,
    sumo_binary: str = "sumo",
    simulation_begin: int = 0,
    simulation_end: int = 9000,
    comparison_begin: int = 1800,
    comparison_end: int = 9000,
    interval: int = 900,
    timeout_seconds: float = 300.0,
    allow_detector_cross_section_boundaries: bool = False,
    command_runner: Callable[..., object] = run_command,
) -> dict[str, Any]:
    """Write replay artifacts from selected W2 signal, W3a count, and W3b detector inputs."""

    net_path = Path(net_file).expanduser().resolve(strict=True)
    binding_path = Path(signal_binding_manifest).expanduser().resolve(strict=True)
    detector_binding_path = Path(detector_binding_manifest).expanduser().resolve(strict=True)
    count_scope_path = Path(count_scope_manifest).expanduser().resolve(strict=True)
    stream_path = Path(count_stream_snapshot).expanduser().resolve(strict=True)
    count_path = Path(canonical_count_file).expanduser().resolve(strict=True)
    if simulation_end <= simulation_begin or interval <= 0 or (simulation_end - simulation_begin) % interval:
        raise HamburgNamedReplayError("simulation window must contain whole positive intervals")
    if not simulation_begin <= comparison_begin < comparison_end <= simulation_end:
        raise HamburgNamedReplayError("comparison window must be inside the simulation window")
    destination = Path(output_dir).expanduser().resolve()
    destination.mkdir(parents=True, exist_ok=True)
    if any(destination.iterdir()):
        raise HamburgNamedReplayError("output_dir must be empty; choose a new versioned run")

    binding = _load_binding_manifest(binding_path)
    _validate_binding_network(binding, net_path)
    signal_history = (
        _load_signal_observation_manifest(
            Path(signal_observation_manifest).expanduser().resolve(strict=True),
            binding_manifest=binding_path,
        )
        if signal_observation_manifest is not None
        else None
    )
    _validate_count_scope_manifest(
        count_scope_path,
        count_stream_snapshot=stream_path,
        canonical_count_file=count_path,
    )
    streams = read_hamburg_count_stream_snapshot(stream_path)
    counts = read_canonical_count_file(count_path)
    detector_binding, mappings = _load_detector_binding_manifest(
        detector_binding_path,
        replay_net=net_path,
        count_stream_snapshot=stream_path,
        expected_stream_ids={stream.stream_id for stream in streams},
    )
    signal_history_scope = _audit_signal_history_scope(
        signal_history,
        counts=counts,
        simulation_begin=simulation_begin,
        simulation_end=simulation_end,
    )
    mapping_evidence = [
        {
            "stream_id": mapping.stream_id,
            "status": mapping.mapping_status,
            "edge": mapping.sumo_edge,
            "lane": mapping.sumo_lane,
            "distance_m": mapping.distance_m,
            "confidence": mapping.mapping_confidence,
            "source": "W3b detector binding",
        }
        for mapping in mappings
    ]
    detector_dir = destination / "detectors"
    demand_dir = destination / "demand"
    audit_dir = destination / "audit"
    sumo_dir = destination / "sumo"
    for path in (detector_dir, demand_dir, audit_dir, sumo_dir):
        path.mkdir(parents=True, exist_ok=True)
    mapping_file = detector_dir / "detector-mapping.csv"
    write_detector_mapping(mapping_file, mappings)

    active = [mapping for mapping in mappings if mapping.mapping_status == "active"]
    if len(active) != len(streams):
        raise HamburgNamedReplayError(
            f"point-to-lane mapping incomplete: active={len(active)}/{len(streams)}"
        )
    aggregation = build_virtual_sensor_aggregation(
        mappings,
        counts,
        bin_seconds=interval,
        expected_begin=simulation_begin,
        expected_end=simulation_end,
    )
    comparison_counts = [row for row in counts if comparison_begin <= row.begin and row.end <= comparison_end]
    comparison_aggregation = build_virtual_sensor_aggregation(
        mappings,
        comparison_counts,
        bin_seconds=interval,
        expected_begin=comparison_begin,
        expected_end=comparison_end,
    )
    detectors = list(aggregation.detectors)
    virtual_mapping_file = detector_dir / "virtual-detector-mapping.csv"
    expected_file = detector_dir / "virtual-expected-counts-simulation.csv"
    expected_comparison_file = detector_dir / "virtual-expected-counts-comparison.csv"
    e1_file = detector_dir / "e1-detectors.add.xml"
    e2_file = detector_dir / "e2-queue-detectors.add.xml"
    # SUMO resolves a relative detector ``file`` against the additional-file's directory,
    # not the process cwd.  Use an absolute output path so the manifest and comparison
    # always refer to the artifact that SUMO actually writes.
    e1_output = (sumo_dir / "e1-15min.xml").resolve()
    e2_output = (sumo_dir / "e2-15min.xml").resolve()
    write_virtual_detector_mapping(virtual_mapping_file, aggregation.groups)
    write_virtual_expected_counts(expected_file, aggregation.expected_counts)
    write_virtual_expected_counts(expected_comparison_file, comparison_aggregation.expected_counts)
    write_e1_additional(
        e1_file,
        detectors,
        lanes=_read_net_lanes_for_detectors(net_path),
        period=interval,
        output_file=str(e1_output),
    )
    write_virtual_e2_additional(e2_file, aggregation.groups, output_file=str(e2_output), period=interval)

    edge_flows, edge_audits = aggregate_virtual_counts_to_complete_edge_sections(net_path, aggregation)
    edge_audit_file = audit_dir / "edge-constraint-audit.csv"
    edge_count_file = demand_dir / "official-edge-counts-15min.xml"
    write_edge_constraint_audit(edge_audit_file, edge_audits)
    write_route_sampler_edge_counts(edge_count_file, edge_flows)
    route_support = _write_route_support(
        net_path,
        detectors,
        demand_dir,
        prefix="hamburg_sandtorkai",
        excluded_edges=frozenset(),
        allow_detector_cross_section_boundaries=allow_detector_cross_section_boundaries,
    )
    route_sampler = (
        run_route_sampler(
            candidate_manifest_csv=Path(route_support["route_candidate_manifest"]),
            edge_data_file=edge_count_file,
            output_dir=demand_dir,
            prefix="hamburg_sandtorkai",
            begin=simulation_begin,
            end=simulation_end,
            interval=interval,
            seed=42,
            optimize="full",
            route_sampler_script=route_sampler_script,
            timeout_seconds=timeout_seconds,
            command_runner=command_runner,
        )
        if route_support.get("status") == "pass"
        else {"status": "blocked", "reason": "route candidate coverage failed"}
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
            route_sampler["demand_route_sha256"] = file_sha256(Path(str(route_sampler["demand_route_file"])))
            if lane_balance.get("status") == "review_required":
                route_sampler["claim_status"] = "lane-level-review-required"
        else:
            route_sampler["status"] = "partial"
            route_sampler["claim_status"] = "construction-incomplete"
    if route_sampler.get("status") != "pass":
        simulation = {"status": "blocked", "reason": "routeSampler did not produce demand"}
    elif signal_observation_manifest is not None and signal_history is None:
        simulation = {
            "status": "blocked",
            "reason": "official signal observation manifest is not execution-ready",
        }
    elif signal_history is not None:
        simulation = _run_dynamic_sumo(
            net_path=net_path,
            route_file=Path(str(route_sampler.get("demand_route_file", ""))),
            e1_file=e1_file,
            e2_file=e2_file,
            tls_events_csv=Path(signal_history["validated_tls_link_events"]),
            expected_counts_csv=expected_file,
            output_dir=sumo_dir / "dynamic_tls_replay",
            begin=simulation_begin,
            end=simulation_end,
            comparison_begin=comparison_begin,
            comparison_end=comparison_end,
            sumo_binary=sumo_binary,
        )
    else:
        simulation = _run_sumo(
            net_path=net_path,
            route_file=Path(str(route_sampler.get("demand_route_file", ""))),
            additional_files=(e1_file, e2_file),
            output_dir=sumo_dir,
            begin=simulation_begin,
            end=simulation_end,
            sumo_binary=sumo_binary,
            timeout_seconds=timeout_seconds,
            command_runner=command_runner,
        )

    comparison_rows: list[dict[str, object]] = []
    comparison_summary: dict[str, object] = {"matched": 0, "missing": 0, "total": 0, "expected": 0, "measured": 0}
    e1_output = Path(str(simulation.get("e1_output", "")))
    comparison_status = "blocked"
    if simulation.get("status") == "pass" and e1_output.is_file():
        expected_rows = _csv_rows(expected_comparison_file, begin=comparison_begin, end=comparison_end)
        comparison_rows = audit_expected_to_e1_strict(expected_rows, e1_output)
        comparison_summary = _summarize_e1(comparison_rows)
        comparison_status = (
            "pass"
            if expected_rows and comparison_summary["matched"] == comparison_summary["total"]
            else "blocked"
        )
    comparison_file = audit_dir / "e1-real-vs-virtual-comparison.csv"
    _write_rows(comparison_file, comparison_rows)
    low_confidence = sum(item.mapping_confidence == "low" for item in mappings)
    manifest_file = destination / "hamburg_named_replay.manifest.json"
    gate_reasons: list[str] = []
    if route_sampler.get("status") != "pass":
        gate_reasons.append("routeSampler did not complete")
    if lane_balance.get("status") == "review_required":
        gate_reasons.append("detector lane evidence does not control every generated source vehicle")
    elif lane_balance.get("status") == "fail":
        gate_reasons.append("departure-lane assignment did not satisfy measured source bins")
    if simulation.get("status") != "pass":
        gate_reasons.append("SUMO process did not complete")
    if simulation.get("quality_gate") != "pass":
        gate_reasons.append("SUMO quality gate detected teleportation or collisions")
    if comparison_status != "pass":
        gate_reasons.append("E1 comparison is missing one or more expected bins")
    if signal_history is not None and signal_history_scope["status"] != "pass":
        gate_reasons.append(str(signal_history_scope["reason"]))
    execution_gate = "pass" if not gate_reasons else "blocked"
    claim_does_not_prove = [
        "a unique OD matrix or observed trajectories",
        "official lane identity where the point snap is low confidence",
        "historical signal replay while official observations are unavailable",
    ]
    if allow_detector_cross_section_boundaries:
        claim_does_not_prove.insert(
            1,
            "upstream or downstream demand outside the measured detector cross-sections",
        )
    manifest: dict[str, Any] = {
        "schema": NAMED_REPLAY_SCHEMA,
        "status": "partial" if execution_gate == "pass" else "blocked",
        "execution_gate": execution_gate,
        "execution_gate_reason": (
            "routeSampler, SUMO quality, and all E1 comparison bins passed"
            if execution_gate == "pass"
            else "; ".join(gate_reasons)
        ),
        "automatic_promotion_gate": "blocked",
        "claim_status": "detector-constrained-diagnostic-replay",
        "source": {
            "net": {"path": str(net_path), "sha256": file_sha256(net_path)},
            "signal_binding_manifest": {"path": str(binding_path), "sha256": file_sha256(binding_path)},
            "detector_binding_manifest": {
                "path": str(detector_binding_path),
                "sha256": file_sha256(detector_binding_path),
            },
            "count_scope_manifest": {
                "path": str(count_scope_path),
                "sha256": file_sha256(count_scope_path),
            },
            "signal_observation_manifest": (
                {"path": str(signal_observation_manifest), "sha256": file_sha256(signal_observation_manifest)}
                if signal_observation_manifest is not None
                else None
            ),
            "tls_link_events": (
                {
                    "path": signal_history["validated_tls_link_events"],
                    "sha256": file_sha256(Path(signal_history["validated_tls_link_events"])),
                }
                if signal_history is not None
                else None
            ),
            "count_stream_snapshot": {"path": str(stream_path), "sha256": file_sha256(stream_path)},
            "canonical_count_file": {"path": str(count_path), "sha256": file_sha256(count_path)},
        },
        "window": {
            "simulation_begin": simulation_begin,
            "simulation_end": simulation_end,
            "comparison_begin": comparison_begin,
            "comparison_end": comparison_end,
            "interval": interval,
            "warmup_seconds": comparison_begin - simulation_begin,
        },
        "mapping": {
            "stream_count": len(streams),
            "active_count": len(active),
            "virtual_detector_count": len(detectors),
            "low_confidence_count": low_confidence,
            "method": "hash_bound_W3b_detector_mapping",
            "evidence": mapping_evidence,
        },
        "route_support": route_support,
        "signal_history_scope": signal_history_scope,
        "boundary_policy": (
            "official_detector_cross_sections_as_open_source_sink_ports"
            if allow_detector_cross_section_boundaries
            else "network_boundaries_only"
        ),
        "route_sampler": route_sampler,
        "simulation": simulation,
        "comparison": {"status": comparison_status, "summary": comparison_summary, "file": str(comparison_file)},
        "gates": {
            "signal_binding_execution": "pass" if binding.get("execution_gate") == "pass" else "blocked",
            "detector_binding_execution": (
                "pass" if detector_binding.get("execution_gate") == "pass" else "blocked"
            ),
            "detector_aggregation_semantics": "pass",
            "detector_mapping": "pass" if len(active) == len(streams) else "blocked",
            "route_sampler": route_sampler.get("status", "blocked"),
            "lane_balance": lane_balance.get("status", "not_requested"),
            "sumo_run": simulation.get("status", "blocked"),
            "sumo_quality": simulation.get("quality_gate", "blocked"),
            "e1_comparison": comparison_status,
            "historical_signal_replay": (
                "pass"
                if signal_history is not None and signal_history_scope["status"] == "pass"
                and not signal_history.get("missing_required_node_ids")
                else "partial_missing_required_node"
                if signal_history is not None and signal_history_scope["status"] == "pass"
                and signal_history.get("missing_required_node_ids")
                else "partial_history_window"
                if signal_history is not None
                else "blocked_signal_observation_manifest"
                if signal_observation_manifest is not None
                else "blocked_pending_official_observations"
            ),
            "automatic_promotion": "blocked_low_confidence_or_incomplete_official_scope",
        },
        "artifacts": {
            "detector_mapping": str(mapping_file),
            "virtual_detector_mapping": str(virtual_mapping_file),
            "e1_additional": str(e1_file),
            "e2_additional": str(e2_file),
            "edge_data": str(edge_count_file),
            "comparison": str(comparison_file),
        },
        "claim_boundary": {
            "proves": [
                "the declared official count bins were aggregated into same-location SUMO E1/E2 inputs",
                "routeSampler produced a detector-constrained plausible route realization when its gate passes",
                "SUMO E1 output was compared with the virtual expected bins",
            ],
            "does_not_prove": claim_does_not_prove,
        },
    }
    write_json_atomic(manifest_file, manifest, sort_keys=True)
    return {**manifest, "manifest_file": str(manifest_file)}


def _load_binding_manifest(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("schema") != "torii.hamburg-named-signal-binding/v1":
        raise HamburgNamedReplayError("signal binding manifest schema mismatch")
    if payload.get("execution_gate") != "pass":
        raise HamburgNamedReplayError("signal binding manifest is not execution-ready")
    return payload


def _validate_binding_network(binding: Mapping[str, Any], replay_net: Path) -> None:
    source = binding.get("source")
    candidate = source.get("candidate_net") if isinstance(source, Mapping) else None
    if not isinstance(candidate, Mapping):
        raise HamburgNamedReplayError("signal binding manifest has no candidate network binding")
    try:
        candidate_path = Path(str(candidate["path"])).expanduser().resolve(strict=True)
        declared_sha256 = str(candidate["sha256"]).lower()
    except (KeyError, OSError, RuntimeError) as exc:
        raise HamburgNamedReplayError("signal binding candidate network binding is invalid") from exc
    actual_sha256 = file_sha256(candidate_path)
    if actual_sha256 != declared_sha256:
        raise HamburgNamedReplayError("signal binding candidate network SHA-256 mismatch")
    if actual_sha256 != file_sha256(replay_net):
        raise HamburgNamedReplayError("signal binding network does not match replay network")


def _load_detector_binding_manifest(
    path: Path,
    *,
    replay_net: Path,
    count_stream_snapshot: Path,
    expected_stream_ids: set[int],
) -> tuple[dict[str, Any], list[DetectorMapping]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("schema") != "torii.hamburg-named-detector-binding/v1":
        raise HamburgNamedReplayError("detector binding manifest schema mismatch")
    if payload.get("execution_gate") != "pass":
        raise HamburgNamedReplayError("detector binding manifest is not execution-ready")
    gates = payload.get("gates")
    if not isinstance(gates, Mapping) or gates.get("sensor_aggregation_semantics") != "pass":
        raise HamburgNamedReplayError(
            "detector binding has no approved shared-lane aggregation semantics"
        )
    source = payload.get("source")
    if not isinstance(source, Mapping):
        raise HamburgNamedReplayError("detector binding manifest has no source identities")
    _resolve_file_identity(
        source.get("candidate_net"),
        base_dir=path.parent,
        label="detector binding candidate network",
        expected_file=replay_net,
    )
    _resolve_file_identity(
        source.get("count_stream_snapshot"),
        base_dir=path.parent,
        label="detector binding count stream snapshot",
        expected_file=count_stream_snapshot,
    )
    artifacts = payload.get("artifacts")
    mapping_identity = artifacts.get("detector_mapping") if isinstance(artifacts, Mapping) else None
    mapping_path = _resolve_file_identity(
        mapping_identity,
        base_dir=path.parent,
        label="detector mapping",
    )
    mappings = _read_detector_mapping(mapping_path)
    actual_stream_ids = {mapping.stream_id for mapping in mappings}
    if len(actual_stream_ids) != len(mappings):
        raise HamburgNamedReplayError("detector mapping repeats a stream identity")
    if actual_stream_ids != expected_stream_ids:
        raise HamburgNamedReplayError(
            "detector mapping stream identities do not match the selected count snapshot"
        )
    return payload, mappings


def _validate_count_scope_manifest(
    path: Path,
    *,
    count_stream_snapshot: Path,
    canonical_count_file: Path,
) -> None:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("schema") != "torii.hamburg-named-corridor-count-scope/v1":
        raise HamburgNamedReplayError("count scope manifest schema mismatch")
    if payload.get("execution_gate") != "pass":
        raise HamburgNamedReplayError("count scope manifest is not execution-ready")
    artifacts = payload.get("artifacts")
    if not isinstance(artifacts, Mapping):
        raise HamburgNamedReplayError("count scope manifest has no artifact identities")
    _resolve_file_identity(
        artifacts.get("count_streams_raw"),
        base_dir=path.parent,
        label="count scope stream snapshot",
        expected_file=count_stream_snapshot,
    )
    _resolve_file_identity(
        artifacts.get("counts_simulation_15min"),
        base_dir=path.parent,
        label="count scope simulation counts",
        expected_file=canonical_count_file,
    )


def _read_detector_mapping(path: Path) -> list[DetectorMapping]:
    try:
        with path.open(encoding="utf-8", newline="") as handle:
            rows = list(csv.DictReader(handle))
        return [
            DetectorMapping(
                detector_id=row["detector_id"],
                stream_id=int(row["stream_id"]),
                node_id=row["node_id"],
                asset_id=row["asset_id"],
                real_direction=row["real_direction"],
                lane_use=row["lane_use"],
                longitude=float(row["longitude"]),
                latitude=float(row["latitude"]),
                official_map_lane=row["official_map_lane"],
                official_map_distance_m=_optional_float(row["official_map_distance_m"]),
                sumo_edge=row["sumo_edge"],
                sumo_lane=row["sumo_lane"],
                lane_position=float(row["lane_position"]),
                distance_m=_optional_float(row["distance_m"]),
                heading_error_deg=_optional_float(row["heading_error_deg"]),
                period=int(row["period"]),
                mapping_confidence=row["mapping_confidence"],
                mapping_status=row["mapping_status"],
                mapping_reason=row["mapping_reason"],
            )
            for row in rows
        ]
    except (KeyError, TypeError, ValueError) as exc:
        raise HamburgNamedReplayError("detector mapping CSV is invalid") from exc


def _optional_float(value: str) -> float | None:
    return float(value) if value.strip() else None


def _resolve_file_identity(
    identity: object,
    *,
    base_dir: Path,
    label: str,
    expected_file: Path | None = None,
) -> Path:
    if not isinstance(identity, Mapping):
        raise HamburgNamedReplayError(f"{label} identity is missing")
    raw_path = identity.get("path")
    declared_sha256 = identity.get("sha256")
    if not isinstance(raw_path, str) or not raw_path.strip() or not isinstance(declared_sha256, str):
        raise HamburgNamedReplayError(f"{label} identity is invalid")
    candidate = Path(raw_path).expanduser()
    if not candidate.is_absolute():
        candidate = base_dir / candidate
    try:
        candidate = candidate.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise HamburgNamedReplayError(f"{label} file is unreadable") from exc
    actual_sha256 = file_sha256(candidate)
    if actual_sha256 != declared_sha256.lower():
        raise HamburgNamedReplayError(f"{label} SHA-256 mismatch")
    if expected_file is not None and actual_sha256 != file_sha256(expected_file):
        raise HamburgNamedReplayError(f"{label} does not match the selected replay input")
    return candidate


def _load_signal_observation_manifest(
    path: Path,
    *,
    binding_manifest: Path,
) -> dict[str, Any] | None:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("schema") != "torii.hamburg-named-signal-observations/v2":
        raise HamburgNamedReplayError("signal observation manifest schema mismatch")
    if payload.get("execution_gate") != "pass":
        return None
    source = payload.get("source")
    binding_identity = source.get("binding_manifest") if isinstance(source, Mapping) else None
    _resolve_file_identity(
        binding_identity,
        base_dir=path.parent,
        label="signal observation binding manifest",
        expected_file=binding_manifest,
    )
    artifacts = payload.get("artifacts")
    event_path = artifacts.get("tls_link_events") if isinstance(artifacts, dict) else None
    identities = payload.get("artifact_identities")
    event_identity = identities.get("tls_link_events") if isinstance(identities, Mapping) else None
    validated_event_path = _resolve_file_identity(
        event_identity,
        base_dir=path.parent,
        label="TLS link events",
    )
    if not isinstance(event_path, str) or not event_path.strip():
        raise HamburgNamedReplayError("execution-ready signal observation manifest has no TLS event file")
    declared_event_path = Path(event_path).expanduser()
    if not declared_event_path.is_absolute():
        declared_event_path = path.parent / declared_event_path
    if declared_event_path.resolve() != validated_event_path:
        raise HamburgNamedReplayError("signal observation TLS event artifact identity mismatch")
    return {**payload, "validated_tls_link_events": str(validated_event_path)}


def _audit_signal_history_scope(
    manifest: dict[str, Any] | None,
    *,
    counts: list[Any] | None = None,
    simulation_begin: int,
    simulation_end: int,
) -> dict[str, Any]:
    """Fail closed unless official history covers the replay's UTC count window."""

    replay_window_seconds = simulation_end - simulation_begin
    if manifest is None:
        return {
            "status": "blocked",
            "reason": "official signal history is unavailable",
            "history_window_seconds": None,
            "replay_window_seconds": replay_window_seconds,
        }
    window = manifest.get("window")
    begin_text = window.get("begin_utc") if isinstance(window, dict) else None
    end_text = window.get("end_utc") if isinstance(window, dict) else None
    try:
        history_window_seconds = (
            datetime.fromisoformat(str(end_text).replace("Z", "+00:00"))
            - datetime.fromisoformat(str(begin_text).replace("Z", "+00:00"))
        ).total_seconds()
    except (TypeError, ValueError):
        return {
            "status": "blocked",
            "reason": "official signal history manifest has no valid UTC window",
            "history_window_seconds": None,
            "replay_window_seconds": replay_window_seconds,
        }
    required_counts = [
        row
        for row in counts or []
        if simulation_begin <= row.begin and row.end <= simulation_end
    ]
    required_begin = min((row.source_begin_utc for row in required_counts), default=None)
    required_end = max((row.source_end_utc for row in required_counts), default=None)
    history_begin = datetime.fromisoformat(str(begin_text).replace("Z", "+00:00"))
    history_end = datetime.fromisoformat(str(end_text).replace("Z", "+00:00"))
    if required_begin is not None and required_end is not None and not (
        history_begin <= required_begin and history_end >= required_end
    ):
        return {
            "status": "review_required",
            "reason": "official signal history does not cover the replay count UTC window",
            "history_begin_utc": history_begin.isoformat(),
            "history_end_utc": history_end.isoformat(),
            "required_begin_utc": required_begin.isoformat(),
            "required_end_utc": required_end.isoformat(),
            "history_window_seconds": history_window_seconds,
            "replay_window_seconds": replay_window_seconds,
        }
    if history_window_seconds < replay_window_seconds:
        return {
            "status": "review_required",
            "reason": (
                "official signal history window is shorter than the replay window "
                f"({history_window_seconds:g}s < {replay_window_seconds:g}s)"
            ),
            "history_window_seconds": history_window_seconds,
            "replay_window_seconds": replay_window_seconds,
        }
    return {
        "status": "pass",
        "reason": "official signal history window covers the replay window",
        "history_window_seconds": history_window_seconds,
        "replay_window_seconds": replay_window_seconds,
    }


def _read_net_lanes_for_detectors(net_file: Path) -> dict[str, Any]:
    import sumolib

    net = sumolib.net.readNet(str(net_file), withInternal=True)
    result: dict[str, Any] = {}
    from .detector_demand import LaneInfo

    for edge in net.getEdges(withInternal=True):
        if edge.getID().startswith(":") or getattr(edge, "getFunction", lambda: "")():
            continue
        for lane in edge.getLanes():
            if lane.allows("passenger") or lane.allows("private"):
                result[lane.getID()] = LaneInfo(lane.getID(), edge.getID(), float(lane.getLength()))
    return result


def _run_sumo(*, net_path: Path, route_file: Path, additional_files: tuple[Path, ...], output_dir: Path, begin: int, end: int, sumo_binary: str, timeout_seconds: float, command_runner: Callable[..., object]) -> dict[str, Any]:
    if not route_file.is_file():
        return {"status": "blocked", "reason": "demand route file is missing"}
    additional = ",".join(str(path.resolve()) for path in additional_files if path.is_file())
    e1_output = output_dir / "e1-15min.xml"
    summary_output = output_dir / "summary.xml"
    tripinfo_output = output_dir / "tripinfo.xml"
    error_log = output_dir / "sumo.log"
    command = [
        str(sumo_binary),
        "--net-file", str(net_path),
        "--route-files", str(route_file.resolve()),
        "--additional-files", additional,
        "--begin", str(begin),
        "--end", str(end),
        "--seed", "42",
        "--summary-output", str(summary_output),
        "--tripinfo-output", str(tripinfo_output),
        "--error-log", str(error_log),
        "--no-step-log", "true",
        "--duration-log.disable", "true",
    ]
    result = command_runner(command, cwd=output_dir, timeout_seconds=timeout_seconds)
    payload = result if isinstance(result, dict) else getattr(result, "__dict__", {})
    status = str(payload.get("status", "pass" if getattr(result, "returncode", 1) == 0 else "blocked"))
    quality = _read_sumo_quality(summary_output) if status == "pass" else {
        "quality_gate": "blocked",
        "teleport_count": None,
        "collision_count": None,
        "reason": "SUMO did not produce a successful process result",
    }
    return {
        "status": "pass" if status == "pass" else "blocked",
        **quality,
        "command": command,
        "result": payload,
        "e1_output": str(e1_output),
        "summary_output": str(summary_output),
        "tripinfo_output": str(tripinfo_output),
        "error_log": str(error_log),
    }


def _run_dynamic_sumo(
    *,
    net_path: Path,
    route_file: Path,
    e1_file: Path,
    e2_file: Path,
    tls_events_csv: Path,
    expected_counts_csv: Path,
    output_dir: Path,
    begin: int,
    end: int,
    comparison_begin: int,
    comparison_end: int,
    sumo_binary: str,
) -> dict[str, Any]:
    """Run the existing TraCI TLS replay when exact history passed its gate."""

    if not route_file.is_file():
        return {"status": "blocked", "reason": "demand route file is missing"}
    report = run_tls_detector_replay(
        net_file=net_path,
        route_file=route_file,
        e1_additional_file=e1_file,
        e2_additional_file=e2_file,
        tls_events_csv=tls_events_csv,
        expected_counts_csv=expected_counts_csv,
        output_dir=output_dir,
        prefix="hamburg_sandtorkai_dynamic",
        replay_end=float(end),
        completion_end=float(end),
        sumo_binary=sumo_binary,
        comparison_begin=float(comparison_begin),
        comparison_end=float(comparison_end),
    )
    summary_path = Path(str(report.get("artifacts", {}).get("summary_file", "")))
    quality = _read_sumo_quality(summary_path)
    artifacts = report.get("artifacts", {}) if isinstance(report.get("artifacts"), dict) else {}
    replay_passed = report.get("status") == "pass"
    return {
        "status": "pass" if replay_passed else "blocked",
        "quality_gate": quality.get("quality_gate", "blocked") if replay_passed else "blocked",
        "teleport_count": quality.get("teleport_count"),
        "collision_count": quality.get("collision_count"),
        "summary_final": quality.get("summary_final"),
        "replay_report": report,
        "e1_output": str(artifacts.get("e1_output", "")),
        "summary_output": str(summary_path),
        "tripinfo_output": str(artifacts.get("tripinfo_file", "")),
    }


def _csv_rows(path: Path, *, begin: int, end: int) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        return [row for row in csv.DictReader(handle) if int(row["begin"]) >= begin and int(row["end"]) <= end]


def _write_rows(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("\n", encoding="utf-8")
        return
    fields = list(rows[0])
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def _summarize_e1(rows: list[dict[str, object]]) -> dict[str, object]:
    matched = [row for row in rows if row["measurement_status"] == "matched"]
    missing = [row for row in rows if row["measurement_status"] == "missing"]
    expected = sum(int(row["expected_total"]) for row in matched)
    measured = sum(int(row["measured_nVehContrib"]) for row in matched)
    errors = [abs(int(row["diff_nVehContrib_minus_expected"])) for row in matched]
    return {
        "matched": len(matched),
        "missing": len(missing),
        "total": len(rows),
        "expected": expected,
        "measured": measured,
        "MAE": sum(errors) / len(errors) if errors else None,
        "max_abs_error": max(errors) if errors else None,
    }


def _read_sumo_quality(summary_path: Path) -> dict[str, Any]:
    """Turn SUMO process output into an automatic replay-quality gate."""

    if not summary_path.is_file():
        return {
            "quality_gate": "blocked",
            "teleport_count": None,
            "collision_count": None,
            "reason": "SUMO summary output is missing",
        }
    try:
        root = ET.parse(summary_path).getroot()
        steps = root.findall("step")
        if not steps:
            raise ValueError("SUMO summary has no step records")
        final = steps[-1]
        teleports = max(int(float(step.attrib.get("teleports", "0"))) for step in steps)
        collisions = max(int(float(step.attrib.get("collisions", "0"))) for step in steps)
    except (ET.ParseError, OSError, TypeError, ValueError) as exc:
        return {
            "quality_gate": "blocked",
            "teleport_count": None,
            "collision_count": None,
            "reason": f"invalid SUMO summary output: {exc}",
        }
    quality_gate = "pass" if teleports == 0 and collisions == 0 else "blocked"
    return {
        "quality_gate": quality_gate,
        "teleport_count": teleports,
        "collision_count": collisions,
        "summary_final": {
            key: final.attrib.get(key)
            for key in ("time", "loaded", "inserted", "running", "waiting", "ended", "arrived", "halting")
            if key in final.attrib
        },
        "quality_reason": "no teleports or collisions" if quality_gate == "pass" else "teleports or collisions detected",
    }
