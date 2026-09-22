"""Materialize official Hamburg signal history for a named corridor.

The binding stage identifies *which* TLD stream controls which SUMO link.  This
stage supplies the observed states for a fixed UTC window.  It intentionally
reuses the existing SensorThings client, bounded query/cache implementation and
TLS event writer; it never fabricates phases when an official stream is absent.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from .artifact_io import write_json_atomic
from .candidate_contracts import file_sha256
from .digital_twin import SignalObservation, SignalStream
from .digital_twin_mapping import TlsBinding, write_tls_link_events
from .hamburg_official import (
    SensorThingsClient,
    census_hamburg_signal_stream_coverage,
    fetch_hamburg_signal_observations,
    probe_hamburg_signal_window_coverage,
)


UTC = timezone.utc
NAMED_SIGNAL_OBSERVATIONS_SCHEMA = "torii.hamburg-named-signal-observations/v2"
NAMED_SIGNAL_WINDOW_SCREEN_SCHEMA = "torii.hamburg-signal-window-screen/v1"
NAMED_SIGNAL_COVERAGE_CENSUS_SCHEMA = "torii.hamburg-named-signal-coverage-census/v1"
_BINDING_SCHEMA = "torii.hamburg-named-signal-binding/v1"


class HamburgSignalObservationError(ValueError):
    """Raised when an observation package cannot be built safely."""


def screen_hamburg_named_signal_windows(
    *,
    binding_manifest: Path,
    output_dir: Path,
    candidate_windows: Mapping[str, tuple[datetime, datetime]],
    api_base_url: str = "https://tld.iot.hamburg.de/v1.0/",
    max_retries: int = 0,
    max_workers: int = 1,
    timeout_seconds: float = 15.0,
    client: SensorThingsClient | None = None,
    client_factory: Callable[[str, float], SensorThingsClient] | None = None,
) -> dict[str, Any]:
    """Screen several candidate UTC windows before a full history fetch.

    This is the W2a workflow step.  It is intentionally cheaper than the
    history materializer and its manifest says so: a positive screen only
    authorizes the next full fetch; it never becomes historical evidence by
    itself.
    """

    binding_path = Path(binding_manifest).expanduser().resolve(strict=True)
    destination = Path(output_dir).expanduser().resolve()
    destination.mkdir(parents=True, exist_ok=True)
    if any(destination.iterdir()):
        raise HamburgSignalObservationError("output_dir must be empty; choose a new versioned screen")
    if not candidate_windows:
        raise HamburgSignalObservationError("at least one candidate signal window is required")
    for label, (begin_utc, end_utc) in candidate_windows.items():
        _validate_window(begin_utc, end_utc)

    payload = _load_binding_manifest(binding_path)
    binding_rows = _active_binding_rows(payload)
    streams, _ = _build_runtime_bindings(binding_rows)
    if not streams:
        raise HamburgSignalObservationError("binding manifest contains no active signal bindings")
    if client is None:
        factory = client_factory or (lambda base, timeout: SensorThingsClient(base, timeout_seconds=timeout))
        client = factory(api_base_url, timeout_seconds)

    screen = probe_hamburg_signal_window_coverage(
        client,
        streams,
        candidate_windows,
        max_retries=max_retries,
        max_workers=max_workers,
    )
    complete_candidates = [
        candidate for candidate in screen["candidates"] if candidate["screen_status"] == "complete_candidate"
    ]
    missing_required_nodes = list(payload.get("missing_official_signal_node_ids", []))
    screen_status = "pass" if complete_candidates else "blocked"
    manifest_path = destination / "signal-window-screen.manifest.json"
    manifest: dict[str, Any] = {
        "schema": NAMED_SIGNAL_WINDOW_SCREEN_SCHEMA,
        "status": screen_status,
        "execution_gate": screen_status,
        "execution_gate_reason": (
            "at least one candidate returned an observation for every active binding"
            if screen_status == "pass"
            else "no candidate returned an observation for every active binding"
        ),
        "automatic_promotion_gate": "blocked",
        "claim_status": "screening_only",
        "source": {
            "binding_manifest": {"path": str(binding_path), "sha256": file_sha256(binding_path)},
            "api_base_url": api_base_url,
        },
        "required_node_ids": list(payload.get("required_node_ids", [])),
        "missing_required_node_ids": missing_required_nodes,
        "screen": screen,
        "next_action": (
            "run_full_signal_history_for_candidate_window"
            if complete_candidates
            else "resolve_official_stream_coverage_or_change_scope"
        ),
        "claim_boundary": {
            "proves": [
                "which candidate windows have at least one official primary observation per active binding",
                "which stream queries were empty or errored",
            ],
            "does_not_prove": [
                "two-hour history completeness",
                "time-zero state coverage",
                "three-node signal completeness while required assets are missing",
            ],
        },
    }
    write_json_atomic(manifest_path, manifest, sort_keys=True)
    return {**manifest, "manifest_file": str(manifest_path)}


def census_hamburg_named_signal_stream_coverage(
    *,
    binding_manifest: Path,
    output_dir: Path,
    lsa_identity_manifest: Path | None = None,
    api_base_url: str = "https://tld.iot.hamburg.de/v1.0/",
    max_retries: int = 1,
    max_workers: int = 1,
    timeout_seconds: float = 15.0,
    client: SensorThingsClient | None = None,
    client_factory: Callable[[str, float], SensorThingsClient] | None = None,
) -> dict[str, Any]:
    """Write a cheap first/last-record coverage census for active bindings."""

    binding_path = Path(binding_manifest).expanduser().resolve(strict=True)
    destination = Path(output_dir).expanduser().resolve()
    destination.mkdir(parents=True, exist_ok=True)
    if any(destination.iterdir()):
        raise HamburgSignalObservationError("output_dir must be empty; choose a new versioned census")
    payload = _load_binding_manifest(binding_path)
    binding_rows = _active_binding_rows(payload)
    streams, _ = _build_runtime_bindings(binding_rows)
    if not streams:
        raise HamburgSignalObservationError("binding manifest contains no active signal bindings")
    if client is None:
        factory = client_factory or (lambda base, timeout: SensorThingsClient(base, timeout_seconds=timeout))
        client = factory(api_base_url, timeout_seconds)

    census = census_hamburg_signal_stream_coverage(
        client,
        streams,
        max_retries=max_retries,
        max_workers=max_workers,
    )
    missing_required_nodes = list(payload.get("missing_official_signal_node_ids", []))
    identity_evidence = (
        _load_official_lsa_identity_evidence(
            Path(lsa_identity_manifest).expanduser().resolve(strict=True),
            missing_required_nodes,
        )
        if lsa_identity_manifest is not None
        else None
    )
    publication_gap_confirmed = bool(missing_required_nodes and identity_evidence)
    coverage_complete = census["range_available_count"] == census["stream_count"]
    execution_gate = "pass" if coverage_complete else "blocked"
    status = "pass" if coverage_complete and not missing_required_nodes else "partial" if coverage_complete else "blocked"
    manifest_path = destination / "signal-coverage-census.manifest.json"
    manifest: dict[str, Any] = {
        "schema": NAMED_SIGNAL_COVERAGE_CENSUS_SCHEMA,
        "status": status,
        "execution_gate": execution_gate,
        "execution_gate_reason": (
            "every active binding returned bounded earliest and latest records; "
            "a required official node is absent from the published binding"
            if execution_gate == "pass" and missing_required_nodes
            else "every active binding returned bounded earliest and latest records"
            if execution_gate == "pass"
            else "one or more active bindings returned an empty or errored endpoint query"
        ),
        "automatic_promotion_gate": "blocked",
        "claim_status": "coverage_hint_only",
        "source": {
            "binding_manifest": {"path": str(binding_path), "sha256": file_sha256(binding_path)},
            "api_base_url": api_base_url,
        },
        "official_node_identity": identity_evidence,
        "stream_count": len(streams),
        "missing_required_node_ids": missing_required_nodes,
        "coverage": census,
        "publication_gap": {
            "decision": (
                "confirmed_official_node_without_published_tld_binding"
                if publication_gap_confirmed
                else "unconfirmed"
            ),
            "node_ids": missing_required_nodes,
            "claim": (
                "official LSA identity is present, but this node has no active "
                "TLD binding in the supplied official binding artifact"
                if publication_gap_confirmed
                else "no official LSA identity evidence was supplied for the missing node"
            ),
        },
        "next_action": (
            "resolve_official_signal_publication_gap_or_change_scope"
            if missing_required_nodes
            else "screen_candidate_weekend_windows_then_run_full_history"
            if execution_gate == "pass"
            else "resolve_stream_endpoint_coverage_or_change_scope"
        ),
        "claim_boundary": {
            "proves": [
                "which active streams have bounded endpoint records",
                "which active streams are empty or errored at the time of the census",
                *(
                    ["a required official LSA node exists while its TLD binding is absent"]
                    if publication_gap_confirmed
                    else []
                ),
            ],
            "does_not_prove": [
                "continuous history between endpoint hints",
                "a complete Saturday window",
                "time-zero state coverage or three-node signal completeness",
            ],
        },
    }
    write_json_atomic(manifest_path, manifest, sort_keys=True)
    return {**manifest, "manifest_file": str(manifest_path)}


def materialize_hamburg_named_signal_observations(
    *,
    binding_manifest: Path,
    output_dir: Path,
    begin_utc: datetime,
    end_utc: datetime,
    api_base_url: str = "https://tld.iot.hamburg.de/v1.0/",
    cache_dir: Path | None = None,
    preceding_lookback: timedelta = timedelta(days=7),
    chunk_duration: timedelta = timedelta(minutes=10),
    max_retries: int = 1,
    max_workers: int = 1,
    timeout_seconds: float = 60.0,
    retry_incomplete_cache: bool = False,
    allow_signal_group_projection: bool = False,
    client: SensorThingsClient | None = None,
    client_factory: Callable[[str, float], SensorThingsClient] | None = None,
) -> dict[str, Any]:
    """Fetch and audit official primary signal history for active bindings.

    ``execution_gate`` covers all active bindings present in the binding
    artifact.  ``automatic_promotion_gate`` additionally requires that the
    binding artifact itself is complete for every required node.  Thus a
    two-node diagnostic can be replayed when 2403 is genuinely unavailable,
    while the three-node product claim remains blocked.
    """

    binding_path = Path(binding_manifest).expanduser().resolve(strict=True)
    destination = Path(output_dir).expanduser().resolve()
    destination.mkdir(parents=True, exist_ok=True)
    if any(destination.iterdir()):
        raise HamburgSignalObservationError("output_dir must be empty; choose a new versioned run")
    _validate_window(begin_utc, end_utc)
    payload = _load_binding_manifest(binding_path)
    binding_rows = _active_binding_rows(payload)
    streams, tls_bindings = _build_runtime_bindings(binding_rows)
    if not streams:
        raise HamburgSignalObservationError("binding manifest contains no active signal bindings")

    observation_cache = Path(cache_dir).expanduser().resolve() if cache_dir is not None else destination / "observation_cache"
    observation_cache.mkdir(parents=True, exist_ok=True)
    if client is None:
        factory = client_factory or (lambda base, timeout: SensorThingsClient(base, timeout_seconds=timeout))
        client = factory(api_base_url, timeout_seconds)
    observations, raw = fetch_hamburg_signal_observations(
        client,
        streams,
        begin_utc=begin_utc,
        end_utc=end_utc,
        include_preceding_state=True,
        preceding_lookback=preceding_lookback,
        chunk_duration=chunk_duration,
        max_retries=max_retries,
        max_workers=max_workers,
        cache_dir=observation_cache,
        retry_incomplete_cache=retry_incomplete_cache,
    )

    stream_audit = _audit_streams(streams, observations, raw, begin_utc, end_utc)
    projection = _project_signal_group_observations(
        streams,
        observations,
        stream_audit,
        begin_utc=begin_utc,
        end_utc=end_utc,
        enabled=allow_signal_group_projection,
    )
    stream_audit = _audit_streams(streams, observations, raw, begin_utc, end_utc)
    for row in stream_audit:
        derived = projection["by_stream_id"].get(str(row["stream_id"]))
        if derived is not None:
            row["source_status"] = "derived_from_signal_group"
            row["derived_from_stream_id"] = derived["source_stream_id"]
    complete = [row for row in stream_audit if row["status"] == "complete"]
    incomplete = [row for row in stream_audit if row["status"] != "complete"]
    event_path = destination / "tls-link-events.csv"
    event_stats = write_tls_link_events(
        event_path,
        streams,
        observations,
        tls_bindings,
        begin_utc=begin_utc,
        end_utc=end_utc,
    )

    raw_path = destination / "signal-observations.raw.json"
    normalized_path = destination / "signal-observations.normalized.json"
    write_json_atomic(raw_path, raw, sort_keys=True)
    normalized = {
        "schema": NAMED_SIGNAL_OBSERVATIONS_SCHEMA,
        "query_begin_utc": _utc_text(begin_utc),
        "query_end_utc": _utc_text(end_utc),
        "observations": {
            str(stream_id): [_serialize_observation(item) for item in values]
            for stream_id, values in sorted(observations.items())
        },
        "signal_group_projection": projection["derived_streams"],
    }
    write_json_atomic(normalized_path, normalized, sort_keys=True)

    missing_required_nodes = list(payload.get("missing_official_signal_node_ids", []))
    execution_gate = "pass" if not incomplete else "blocked"
    promotion_gate = "pass" if execution_gate == "pass" and not missing_required_nodes and payload.get("automatic_promotion_gate") == "pass" else "blocked"
    if execution_gate == "pass" and not missing_required_nodes:
        status = "pass"
    elif execution_gate == "pass":
        status = "partial"
    else:
        status = "blocked"
    manifest_path = destination / "signal-observations.manifest.json"
    manifest: dict[str, Any] = {
        "schema": NAMED_SIGNAL_OBSERVATIONS_SCHEMA,
        "status": status,
        "execution_gate": execution_gate,
        "execution_gate_reason": (
            "every active binding has an official response and a preceding state for t=0"
            if execution_gate == "pass"
            else "one or more active bindings lack a complete official response or t=0 state"
        ),
        "automatic_promotion_gate": promotion_gate,
        "claim_status": (
            "official-primary-signal-history-bound-for-available-nodes"
            if execution_gate == "pass"
            else "official-primary-signal-history-incomplete"
        ),
        "source": {
            "binding_manifest": {"path": str(binding_path), "sha256": file_sha256(binding_path)},
            "api_base_url": api_base_url,
            "observation_cache": str(observation_cache),
        },
        "window": {
            "begin_utc": _utc_text(begin_utc),
            "end_utc": _utc_text(end_utc),
            "preceding_lookback_seconds": int(preceding_lookback.total_seconds()),
            "chunk_duration_seconds": int(chunk_duration.total_seconds()),
        },
        "cache_policy": {
            "retry_incomplete_cache": retry_incomplete_cache,
        },
        "signal_group_projection": {
            "enabled": allow_signal_group_projection,
            "derived_stream_count": len(projection["derived_streams"]),
            "derived_streams": projection["derived_streams"],
            "blocked_streams": projection["blocked_streams"],
        },
        "stream_count": len(streams),
        "direct_complete_stream_count": sum(
            1
            for row in stream_audit
            if row["status"] == "complete" and str(row["stream_id"]) not in projection["by_stream_id"]
        ),
        "projected_complete_stream_count": len(projection["derived_streams"]),
        "complete_stream_count": len(complete),
        "incomplete_stream_ids": [row["stream_id"] for row in incomplete],
        "stream_audit": stream_audit,
        "missing_required_node_ids": missing_required_nodes,
        "event_stats": event_stats,
        "upstream_fetch": {
            "failed_stream_ids": list(raw.get("failed_stream_ids", [])),
            "partial_stream_ids": list(raw.get("partial_stream_ids", [])),
            "request_count": len(raw.get("request_urls", [])),
        },
        "gates": {
            "official_observation_completeness": execution_gate,
            "time_zero_state_coverage": "pass" if all(row["preceding_count"] > 0 for row in stream_audit) else "blocked",
            "signal_group_projection": (
                "pass"
                if projection["derived_streams"] and not projection["blocked_streams"]
                else "not_used"
                if not allow_signal_group_projection
                else "blocked"
                if projection["blocked_streams"]
                else "pass"
            ),
            "required_node_scope": "pass" if not missing_required_nodes else "blocked",
            "automatic_promotion": promotion_gate,
        },
        "artifacts": {
            "raw_observations": str(raw_path),
            "normalized_observations": str(normalized_path),
            "tls_link_events": str(event_path),
        },
        "artifact_identities": {
            "raw_observations": {"path": str(raw_path), "sha256": file_sha256(raw_path)},
            "normalized_observations": {
                "path": str(normalized_path),
                "sha256": file_sha256(normalized_path),
            },
            "tls_link_events": {"path": str(event_path), "sha256": file_sha256(event_path)},
        },
        "claim_boundary": {
            "proves": [
                "which official primary streams answered for the UTC window",
                "which state initializes each active SUMO link at simulation time zero",
                "that failed or partial streams remain visible in the manifest",
                *(
                    [
                        "that a missing connection stream was projected only from a direct sibling with the same official node and signalGroupID"
                    ]
                    if projection["derived_streams"]
                    else []
                ),
            ],
            "does_not_prove": [
                "a complete three-node signal stage while a required node is missing",
                *(
                    ["a direct per-connection TLD observation for a projected stream"]
                    if projection["derived_streams"]
                    else ["signal timing for streams with an incomplete upstream response"]
                ),
            ],
        },
    }
    write_json_atomic(manifest_path, manifest, sort_keys=True)
    return {**manifest, "manifest_file": str(manifest_path)}


def _load_binding_manifest(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise HamburgSignalObservationError(f"cannot parse binding manifest: {path}") from exc
    if not isinstance(payload, Mapping) or payload.get("schema") != _BINDING_SCHEMA:
        raise HamburgSignalObservationError("signal binding manifest schema mismatch")
    if payload.get("execution_gate") != "pass":
        raise HamburgSignalObservationError("signal binding manifest is not execution-ready")
    return dict(payload)


def _load_official_lsa_identity_evidence(
    path: Path,
    missing_required_nodes: Sequence[str],
) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise HamburgSignalObservationError(f"cannot parse official LSA identity manifest: {path}") from exc
    if not isinstance(payload, Mapping) or payload.get("schema") != "torii.hamburg-lsa-node-identity-evidence/v1":
        raise HamburgSignalObservationError("official LSA identity manifest schema mismatch")
    if payload.get("decision") != "pass":
        raise HamburgSignalObservationError("official LSA identity manifest is not a passing frozen snapshot")
    selections = payload.get("selections")
    if not isinstance(selections, list):
        raise HamburgSignalObservationError("official LSA identity manifest lacks selections")
    selected_ids = {
        str(item.get("selected_node", {}).get("node_id", "")).strip()
        for item in selections
        if isinstance(item, Mapping) and isinstance(item.get("selected_node"), Mapping)
    }
    missing_identity = sorted(set(str(node).strip() for node in missing_required_nodes) - selected_ids)
    if missing_identity:
        raise HamburgSignalObservationError(
            "official LSA identity manifest does not cover missing required node(s): "
            + ", ".join(missing_identity)
        )
    return {
        "path": str(path),
        "sha256": file_sha256(path),
        "schema": payload["schema"],
        "decision": payload["decision"],
        "selected_node_ids": sorted(selected_ids),
    }


def _project_signal_group_observations(
    streams: Sequence[SignalStream],
    observations: dict[int, list[SignalObservation]],
    stream_audit: Sequence[Mapping[str, Any]],
    *,
    begin_utc: datetime,
    end_utc: datetime,
    enabled: bool,
) -> dict[str, Any]:
    """Project a direct signal-group state onto a silent sibling movement.

    A TLD ``signalGroupID`` is the controller identity for the signal state,
    while a stream is only one MAP movement in that group.  Projection is
    therefore allowed only when a sibling in the same official node/group has
    a complete response and an unambiguous preceding state.  No phase or time
    is synthesized; the source observations are copied with a new stream id
    and are recorded as derived evidence.
    """

    result: dict[str, Any] = {"derived_streams": [], "blocked_streams": [], "by_stream_id": {}}
    if not enabled:
        return result

    audit_by_id = {int(row["stream_id"]): row for row in stream_audit}
    streams_by_group: dict[tuple[str, str], list[SignalStream]] = {}
    for stream in streams:
        streams_by_group.setdefault((stream.node_id, stream.signal_group), []).append(stream)

    for target in streams:
        target_audit = audit_by_id.get(target.stream_id, {})
        if target_audit.get("status") == "complete":
            continue
        siblings = [
            sibling
            for sibling in streams_by_group.get((target.node_id, target.signal_group), [])
            if sibling.stream_id != target.stream_id
            and audit_by_id.get(sibling.stream_id, {}).get("status") == "complete"
        ]
        if not siblings:
            result["blocked_streams"].append(
                {
                    "stream_id": target.stream_id,
                    "node_id": target.node_id,
                    "signal_group": target.signal_group,
                    "reason": "no_complete_sibling_in_same_official_signal_group",
                }
            )
            continue
        preceding_states = {
            str(audit_by_id[sibling.stream_id].get("preceding_state", ""))
            for sibling in siblings
        }
        if len(preceding_states) != 1 or "" in preceding_states:
            result["blocked_streams"].append(
                {
                    "stream_id": target.stream_id,
                    "node_id": target.node_id,
                    "signal_group": target.signal_group,
                    "sibling_stream_ids": sorted(sibling.stream_id for sibling in siblings),
                    "reason": "same_signal_group_siblings_have_conflicting_or_missing_t0_state",
                }
            )
            continue
        source = max(
            siblings,
            key=lambda sibling: (
                int(audit_by_id[sibling.stream_id].get("window_count", 0)),
                -sibling.stream_id,
            ),
        )
        source_values = list(observations.get(source.stream_id, ()))
        if not source_values:
            result["blocked_streams"].append(
                {
                    "stream_id": target.stream_id,
                    "node_id": target.node_id,
                    "signal_group": target.signal_group,
                    "source_stream_id": source.stream_id,
                    "reason": "complete_sibling_has_no_observation_values",
                }
            )
            continue
        source_preceding = [value for value in source_values if value.phenomenon_time_utc <= begin_utc]
        source_window = [
            value for value in source_values if begin_utc < value.phenomenon_time_utc < end_utc
        ]
        projected_values = [
            SignalObservation(
                stream_id=target.stream_id,
                observation_id=None,
                phenomenon_time_utc=value.phenomenon_time_utc,
                result=value.result,
                result_time=value.result_time,
            )
            for value in ([source_preceding[-1]] if source_preceding else []) + source_window
        ]
        if not projected_values:
            result["blocked_streams"].append(
                {
                    "stream_id": target.stream_id,
                    "node_id": target.node_id,
                    "signal_group": target.signal_group,
                    "source_stream_id": source.stream_id,
                    "reason": "complete_sibling_has_no_values_in_requested_window",
                }
            )
            continue
        observations[target.stream_id] = projected_values
        evidence = {
            "stream_id": target.stream_id,
            "source_stream_id": source.stream_id,
            "node_id": target.node_id,
            "signal_group": target.signal_group,
            "projection_rule": "same_official_node_and_signalGroupID",
            "source_sibling_stream_ids": sorted(sibling.stream_id for sibling in siblings),
            "projected_observation_count": len(projected_values),
            "source_preceding_state": next(iter(preceding_states)),
        }
        result["derived_streams"].append(evidence)
        result["by_stream_id"][str(target.stream_id)] = evidence
    return result


def _active_binding_rows(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    artifact = payload.get("binding_artifact")
    if not isinstance(artifact, Mapping):
        raise HamburgSignalObservationError("binding manifest lacks a hash-bound binding artifact")
    path = Path(str(artifact.get("path", ""))).expanduser().resolve()
    if not path.is_file():
        raise HamburgSignalObservationError(f"binding artifact is missing: {path}")
    expected_sha256 = str(artifact.get("sha256", "")).strip()
    if not expected_sha256 or file_sha256(path) != expected_sha256:
        raise HamburgSignalObservationError("binding artifact SHA-256 mismatch")
    try:
        content = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise HamburgSignalObservationError(f"cannot parse binding artifact: {path}") from exc
    if not isinstance(content, Mapping) or content.get("schema") != _BINDING_SCHEMA:
        raise HamburgSignalObservationError("binding artifact schema mismatch")
    rows = content.get("bindings")
    if not isinstance(rows, list):
        raise HamburgSignalObservationError("binding artifact does not contain a bindings list")
    active: list[dict[str, Any]] = []
    seen: set[tuple[int, str, int]] = set()
    for raw in rows:
        if not isinstance(raw, Mapping):
            raise HamburgSignalObservationError("binding row is not an object")
        try:
            row = dict(raw)
            key = (int(row["stream_id"]), str(row["controller_id"]), int(row["link_index"]))
        except (KeyError, TypeError, ValueError) as exc:
            raise HamburgSignalObservationError("binding row has invalid identity") from exc
        if key in seen:
            raise HamburgSignalObservationError(f"duplicate active binding: {key}")
        seen.add(key)
        active.append(row)
    return sorted(active, key=lambda row: (str(row.get("node_id", "")), int(row["stream_id"])))


def _build_runtime_bindings(rows: Sequence[Mapping[str, Any]]) -> tuple[list[SignalStream], list[TlsBinding]]:
    streams: list[SignalStream] = []
    bindings: list[TlsBinding] = []
    for row in rows:
        stream_id = int(row["stream_id"])
        streams.append(
            SignalStream(
                stream_id=stream_id,
                thing_id=None,
                node_id=str(row["node_id"]),
                connection_id=str(row["connection_id"]),
                ingress_lane_id=str(row["ingress_lane_id"]),
                egress_lane_id=str(row["egress_lane_id"]),
                lane_type="KFZ",
                signal_group=str(row["signal_group"]),
                layer_name="primary_signal",
                name=f"Official TLD primary signal {row['node_id']}_{row['connection_id']}",
            )
        )
        candidates = row.get("candidate_connections")
        candidate = next(
            (item for item in candidates if isinstance(item, Mapping) and int(item.get("linkIndex", -1)) == int(row["link_index"])),
            candidates[0] if isinstance(candidates, list) and candidates and isinstance(candidates[0], Mapping) else {},
        )
        bindings.append(
            TlsBinding(
                stream_id=stream_id,
                node_id=str(row["node_id"]),
                connection_id=str(row["connection_id"]),
                signal_group=str(row["signal_group"]),
                official_ingress_lane=str(row["ingress_lane_id"]),
                official_egress_lane=str(row["egress_lane_id"]),
                sumo_from_lane=f"{candidate.get('from', '')}_{candidate.get('fromLane', 0)}",
                sumo_to_lane=f"{candidate.get('to', '')}_{candidate.get('toLane', 0)}",
                sumo_tls_id=str(row["controller_id"]),
                sumo_link_index=int(row["link_index"]),
                mapping_confidence="high",
                mapping_status="active",
                mapping_reason="official W2 MAP/TLD binding",
            )
        )
    return streams, bindings


def _audit_streams(
    streams: Sequence[SignalStream],
    observations: Mapping[int, Sequence[SignalObservation]],
    raw: Mapping[str, Any],
    begin_utc: datetime,
    end_utc: datetime,
) -> list[dict[str, Any]]:
    results = raw.get("stream_results", {})
    rows: list[dict[str, Any]] = []
    for stream in streams:
        values = list(observations.get(stream.stream_id, ()))
        preceding = [item for item in values if item.phenomenon_time_utc <= begin_utc]
        window = [item for item in values if begin_utc < item.phenomenon_time_utc < end_utc]
        result = results.get(str(stream.stream_id), {}) if isinstance(results, Mapping) else {}
        source_status = str(result.get("status", "missing"))
        status = "complete" if source_status == "ok" and preceding else "incomplete"
        rows.append(
            {
                "stream_id": stream.stream_id,
                "node_id": stream.node_id,
                "connection_id": stream.connection_id,
                "signal_group": stream.signal_group,
                "source_status": source_status,
                "status": status,
                "preceding_count": len(preceding),
                "window_count": len(window),
                "preceding_state": preceding[-1].result if preceding else "",
                "preceding_time_utc": _utc_text(preceding[-1].phenomenon_time_utc) if preceding else "",
                "source_errors": list(result.get("errors", [])) if isinstance(result, Mapping) else [],
            }
        )
    return rows


def _validate_window(begin_utc: datetime, end_utc: datetime) -> None:
    if begin_utc.tzinfo is None or end_utc.tzinfo is None:
        raise HamburgSignalObservationError("begin_utc and end_utc must be timezone-aware")
    if begin_utc.utcoffset() != timedelta(0) or end_utc.utcoffset() != timedelta(0):
        raise HamburgSignalObservationError("begin_utc and end_utc must use an explicit zero UTC offset")
    begin = begin_utc.astimezone(UTC)
    end = end_utc.astimezone(UTC)
    if end <= begin:
        raise HamburgSignalObservationError("signal observation window must be an ordered explicit UTC interval")


def _serialize_observation(observation: SignalObservation) -> dict[str, Any]:
    return {
        "stream_id": observation.stream_id,
        "observation_id": observation.observation_id,
        "phenomenon_time_utc": _utc_text(observation.phenomenon_time_utc),
        "result": observation.result,
        "result_time": observation.result_time,
    }


def _utc_text(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


__all__ = [
    "HamburgSignalObservationError",
    "NAMED_SIGNAL_COVERAGE_CENSUS_SCHEMA",
    "NAMED_SIGNAL_OBSERVATIONS_SCHEMA",
    "NAMED_SIGNAL_WINDOW_SCREEN_SCHEMA",
    "census_hamburg_named_signal_stream_coverage",
    "materialize_hamburg_named_signal_observations",
    "screen_hamburg_named_signal_windows",
]
