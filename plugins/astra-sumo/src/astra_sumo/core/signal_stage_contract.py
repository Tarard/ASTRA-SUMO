from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, Mapping, Sequence

from .digital_twin import SignalObservation, SignalStream
from .digital_twin_mapping import TlsBinding
from .hamburg_official import sha256_file, write_json


UTC = timezone.utc
SIGNAL_STAGE_SCHEMA = "torii.digital_twin.signal_stage.v1"
SignalMode = Literal["exact_history", "live_capture", "official_ocit_proxy"]

_SIGNAL_MODES = frozenset({"exact_history", "live_capture", "official_ocit_proxy"})
_REQUIRED_SIGNAL_LAYERS = frozenset({"primary_signal", "secondary_signal"})
_COMPLETE_STREAM_STATUS = "complete"


@dataclass(frozen=True, order=True)
class SignalLinkTarget:
    """One SUMO TLS link whose state must be represented at simulation time zero."""

    sumo_tls_id: str
    sumo_link_index: int


@dataclass(frozen=True)
class SignalLayerRequirement:
    """An explicit expected official stream for one semantic signal target/layer.

    ``stream_id=None`` is deliberate: it records a catalog gap instead of letting
    the caller silently omit a missing secondary signal from the contract.
    """

    target_id: str
    layer_name: str
    stream_id: int | None
    node_id: str = ""
    connection_id: str = ""
    signal_group: str = ""


@dataclass(frozen=True)
class SignalEvidenceArtifact:
    """Hash-pinned input used to certify an exact historical signal stage."""

    name: str
    role: Literal["input", "cache"]
    path: Path
    expected_sha256: str


@dataclass(frozen=True)
class LiveCaptureSpec:
    """Durable append-only checkpoint contract for a future live capture."""

    capture_id: str
    event_log_path: Path
    checkpoint_path: Path
    dedupe_key_fields: tuple[str, ...] = (
        "stream_id",
        "observation_id",
        "phenomenon_time_utc",
        "result",
    )
    resume_cursor_field: str = "phenomenon_time_utc"
    checkpoint_sha256: str = ""


def build_signal_stage_manifest(
    *,
    signal_mode: SignalMode | str,
    begin_utc: datetime | None = None,
    end_utc: datetime | None = None,
    signal_streams: Sequence[SignalStream] = (),
    observations: Mapping[int, Sequence[SignalObservation]] | None = None,
    tls_bindings: Sequence[TlsBinding] = (),
    expected_active_links: Sequence[SignalLinkTarget] = (),
    stream_statuses: Mapping[int, str] | None = None,
    layer_requirements: Sequence[SignalLayerRequirement] = (),
    evidence_artifacts: Sequence[SignalEvidenceArtifact] = (),
    live_capture: LiveCaptureSpec | None = None,
    ocit_va_controlled: bool | None = None,
    ocit_has_per_second_semantics: bool | None = None,
    demand_stage: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a fail-closed signal-stage manifest without changing demand status.

    Only ``exact_history`` can produce an exact replay claim. ``live_capture``
    always describes future collection, while ``official_ocit_proxy`` remains a
    non-exact topology/program proxy and never masquerades as observed history.
    """

    mode = str(signal_mode).strip()
    if mode not in _SIGNAL_MODES:
        allowed = ", ".join(sorted(_SIGNAL_MODES))
        raise ValueError(f"unsupported signal_mode {mode!r}; expected one of: {allowed}")

    demand = dict(demand_stage or {"status": "not_evaluated"})
    if mode == "exact_history":
        signal_stage = _evaluate_exact_history(
            begin_utc=begin_utc,
            end_utc=end_utc,
            signal_streams=signal_streams,
            observations=observations or {},
            tls_bindings=tls_bindings,
            expected_active_links=expected_active_links,
            stream_statuses=stream_statuses or {},
            layer_requirements=layer_requirements,
            evidence_artifacts=evidence_artifacts,
        )
    elif mode == "live_capture":
        signal_stage = _build_live_capture_stage(
            live_capture,
            supplied_observation_count=sum(len(values) for values in (observations or {}).values()),
        )
    else:
        signal_stage = _build_ocit_proxy_stage(
            va_controlled=ocit_va_controlled,
            has_per_second_semantics=ocit_has_per_second_semantics,
        )

    return {
        "schema": SIGNAL_STAGE_SCHEMA,
        "status": signal_stage["status"],
        "claim": signal_stage["claim"],
        "claim_status": signal_stage["claim_status"],
        "next_action": signal_stage["next_action"],
        "signal_mode": mode,
        "exact_history": signal_stage["exact_history"],
        "stages": {
            # The demand result is evidence from another stage. A pending or
            # blocked signal stage must not rewrite an already-passed demand stage.
            "demand": demand,
            "signal": signal_stage,
        },
    }


def write_signal_stage_manifest(path: Path, manifest: Mapping[str, Any]) -> str:
    """Persist a signal-stage manifest and return the content SHA-256."""

    if manifest.get("schema") != SIGNAL_STAGE_SCHEMA:
        raise ValueError(f"signal stage manifest schema must be {SIGNAL_STAGE_SCHEMA!r}")
    write_json(path, dict(manifest))
    # ``write_json`` returns the hash of its pre-write text. On Windows the
    # text layer may normalize newlines, so bind the contract to actual bytes.
    return sha256_file(path)


def _evaluate_exact_history(
    *,
    begin_utc: datetime | None,
    end_utc: datetime | None,
    signal_streams: Sequence[SignalStream],
    observations: Mapping[int, Sequence[SignalObservation]],
    tls_bindings: Sequence[TlsBinding],
    expected_active_links: Sequence[SignalLinkTarget],
    stream_statuses: Mapping[int, str],
    layer_requirements: Sequence[SignalLayerRequirement],
    evidence_artifacts: Sequence[SignalEvidenceArtifact],
) -> dict[str, Any]:
    gaps: list[dict[str, Any]] = []
    window = _validate_utc_window(begin_utc, end_utc, gaps)
    layer_audit, required_stream_ids = _audit_layer_requirements(
        signal_streams,
        layer_requirements,
        gaps,
    )
    binding_audit, binding_stream_ids = _audit_bindings(
        tls_bindings,
        expected_active_links,
        required_stream_ids,
        signal_streams,
        gaps,
    )
    targeted_stream_ids = required_stream_ids | binding_stream_ids
    stream_audit = _audit_stream_history(
        targeted_stream_ids,
        observations,
        stream_statuses,
        begin_utc if not window["_failed"] else None,
        binding_audit["active_bindings"],
        gaps,
    )
    artifact_audit = _audit_evidence_artifacts(evidence_artifacts, gaps)

    gate_payloads = {
        "absolute_utc_window": window,
        "primary_secondary_completeness": layer_audit,
        "active_link_binding_coverage": {
            key: value for key, value in binding_audit.items() if key != "active_bindings"
        },
        "stream_history_completeness": stream_audit,
        "cache_and_input_hashes": artifact_audit,
    }
    for gate in gate_payloads.values():
        gate["status"] = "pass" if not gate.pop("_failed", False) else "blocked"

    status = "pass" if not gaps else "blocked"
    if status == "pass":
        claim = "exact historical signal replay is fully evidenced"
        claim_status = "exact-history-ready"
        next_action = "materialize the t=0 states and in-window changes into the SUMO TLS replay"
    else:
        claim = "exact historical signal replay is not certified"
        claim_status = "signal-history-blocked"
        next_action = _next_action_for_gap(gaps[0])

    return {
        "status": status,
        "claim": claim,
        "claim_status": claim_status,
        "next_action": next_action,
        "exact_history": status == "pass",
        "window": window,
        "gates": gate_payloads,
        "gaps": gaps,
    }


def _validate_utc_window(
    begin_utc: datetime | None,
    end_utc: datetime | None,
    gaps: list[dict[str, Any]],
) -> dict[str, Any]:
    failed = False
    if begin_utc is None or end_utc is None:
        gaps.append({"code": "absolute_utc_window_required"})
        failed = True
    elif not _is_explicit_utc(begin_utc) or not _is_explicit_utc(end_utc):
        gaps.append(
            {
                "code": "absolute_utc_window_required",
                "begin": begin_utc.isoformat(),
                "end": end_utc.isoformat(),
                "reason": "timestamps must be timezone-aware with a zero UTC offset",
            }
        )
        failed = True
    elif end_utc <= begin_utc:
        gaps.append(
            {
                "code": "invalid_utc_window_order",
                "begin": _utc_text(begin_utc),
                "end": _utc_text(end_utc),
            }
        )
        failed = True
    return {
        "_failed": failed,
        "begin_utc": _utc_text(begin_utc) if begin_utc is not None and _is_explicit_utc(begin_utc) else "",
        "end_utc": _utc_text(end_utc) if end_utc is not None and _is_explicit_utc(end_utc) else "",
        "half_open": True,
    }


def _audit_layer_requirements(
    streams: Sequence[SignalStream],
    requirements: Sequence[SignalLayerRequirement],
    gaps: list[dict[str, Any]],
) -> tuple[dict[str, Any], set[int]]:
    failed = False
    stream_index: dict[int, list[SignalStream]] = defaultdict(list)
    for stream in streams:
        stream_index[stream.stream_id].append(stream)

    by_target: dict[str, dict[str, list[SignalLayerRequirement]]] = defaultdict(lambda: defaultdict(list))
    for requirement in requirements:
        by_target[requirement.target_id][requirement.layer_name].append(requirement)

    if not by_target:
        gaps.append({"code": "primary_secondary_requirements_required"})
        failed = True

    required_stream_ids: set[int] = set()
    target_rows: list[dict[str, Any]] = []
    for target_id in sorted(by_target):
        target_layers = by_target[target_id]
        missing_contract_layers = sorted(_REQUIRED_SIGNAL_LAYERS - set(target_layers))
        if missing_contract_layers:
            gaps.append(
                {
                    "code": "required_layer_contract_incomplete",
                    "target_id": target_id,
                    "missing_layers": missing_contract_layers,
                }
            )
            failed = True

        layer_rows: list[dict[str, Any]] = []
        for layer_name in sorted(target_layers):
            entries = target_layers[layer_name]
            if len(entries) != 1:
                gaps.append(
                    {
                        "code": "ambiguous_layer_requirement",
                        "target_id": target_id,
                        "layer_name": layer_name,
                        "requirement_count": len(entries),
                    }
                )
                failed = True
                continue
            requirement = entries[0]
            stream_id = requirement.stream_id
            row: dict[str, Any] = {"layer_name": layer_name, "stream_id": stream_id}
            if stream_id is None:
                gaps.append(
                    {
                        "code": "missing_layer_stream",
                        "target_id": target_id,
                        "layer_name": layer_name,
                        "node_id": requirement.node_id,
                        "connection_id": requirement.connection_id,
                        "signal_group": requirement.signal_group,
                    }
                )
                row["status"] = "missing"
                failed = True
            elif len(stream_index.get(stream_id, ())) != 1:
                gaps.append(
                    {
                        "code": "required_stream_inventory_mismatch",
                        "target_id": target_id,
                        "layer_name": layer_name,
                        "stream_id": stream_id,
                        "inventory_count": len(stream_index.get(stream_id, ())),
                    }
                )
                row["status"] = "missing_or_duplicate"
                failed = True
            else:
                stream = stream_index[stream_id][0]
                identity_errors = _stream_identity_errors(stream, requirement)
                if identity_errors:
                    gaps.append(
                        {
                            "code": "required_stream_identity_mismatch",
                            "target_id": target_id,
                            "layer_name": layer_name,
                            "stream_id": stream_id,
                            "fields": identity_errors,
                        }
                    )
                    row["status"] = "identity_mismatch"
                    failed = True
                else:
                    row["status"] = "present"
                    required_stream_ids.add(stream_id)
            layer_rows.append(row)
        target_rows.append(
            {
                "target_id": target_id,
                "required_layers": sorted(_REQUIRED_SIGNAL_LAYERS),
                "layers": layer_rows,
            }
        )

    return {
        "_failed": failed,
        "target_count": len(by_target),
        "required_stream_count": len(required_stream_ids),
        "targets": target_rows,
    }, required_stream_ids


def _audit_bindings(
    bindings: Sequence[TlsBinding],
    expected_links: Sequence[SignalLinkTarget],
    required_stream_ids: set[int],
    streams: Sequence[SignalStream],
    gaps: list[dict[str, Any]],
) -> tuple[dict[str, Any], set[int]]:
    failed = False
    expected_counts: dict[SignalLinkTarget, int] = defaultdict(int)
    for target in expected_links:
        expected_counts[target] += 1
    for target, count in sorted(expected_counts.items()):
        if count != 1:
            gaps.append(
                {
                    "code": "duplicate_expected_active_link",
                    "sumo_tls_id": target.sumo_tls_id,
                    "sumo_link_index": target.sumo_link_index,
                    "count": count,
                }
            )
            failed = True
    expected = set(expected_counts)
    if not expected:
        gaps.append({"code": "expected_active_links_required"})
        failed = True

    stream_layers = {stream.stream_id: stream.layer_name for stream in streams}
    active_by_target: dict[SignalLinkTarget, list[TlsBinding]] = defaultdict(list)
    malformed_active: list[int] = []
    for binding in bindings:
        if binding.mapping_status != "active":
            continue
        if not binding.sumo_tls_id or binding.sumo_link_index is None or binding.sumo_link_index < 0:
            malformed_active.append(binding.stream_id)
            gaps.append(
                {
                    "code": "malformed_active_binding",
                    "stream_id": binding.stream_id,
                    "sumo_tls_id": binding.sumo_tls_id,
                    "sumo_link_index": binding.sumo_link_index,
                }
            )
            failed = True
            continue
        active_by_target[SignalLinkTarget(binding.sumo_tls_id, binding.sumo_link_index)].append(binding)

    binding_stream_ids: set[int] = set()
    for target in sorted(expected | set(active_by_target)):
        target_bindings = active_by_target.get(target, [])
        if target not in expected:
            gaps.append(
                {
                    "code": "unexpected_active_link_binding",
                    "sumo_tls_id": target.sumo_tls_id,
                    "sumo_link_index": target.sumo_link_index,
                    "stream_ids": sorted(binding.stream_id for binding in target_bindings),
                }
            )
            failed = True
        elif len(target_bindings) == 0:
            gaps.append(
                {
                    "code": "missing_active_link_binding",
                    "sumo_tls_id": target.sumo_tls_id,
                    "sumo_link_index": target.sumo_link_index,
                }
            )
            failed = True
        elif len(target_bindings) != 1:
            gaps.append(
                {
                    "code": "ambiguous_active_link_binding",
                    "sumo_tls_id": target.sumo_tls_id,
                    "sumo_link_index": target.sumo_link_index,
                    "stream_ids": sorted(binding.stream_id for binding in target_bindings),
                }
            )
            failed = True

        for binding in target_bindings:
            binding_stream_ids.add(binding.stream_id)
            if binding.stream_id not in required_stream_ids:
                gaps.append(
                    {
                        "code": "binding_stream_not_in_layer_contract",
                        "stream_id": binding.stream_id,
                        "sumo_tls_id": target.sumo_tls_id,
                        "sumo_link_index": target.sumo_link_index,
                    }
                )
                failed = True
            elif stream_layers.get(binding.stream_id) != "primary_signal":
                gaps.append(
                    {
                        "code": "active_link_requires_primary_stream",
                        "stream_id": binding.stream_id,
                        "actual_layer": stream_layers.get(binding.stream_id, ""),
                        "sumo_tls_id": target.sumo_tls_id,
                        "sumo_link_index": target.sumo_link_index,
                    }
                )
                failed = True

    serialized_bindings = [
        {
            "sumo_tls_id": target.sumo_tls_id,
            "sumo_link_index": target.sumo_link_index,
            "stream_ids": sorted(binding.stream_id for binding in active_by_target.get(target, [])),
        }
        for target in sorted(expected | set(active_by_target))
    ]
    return {
        "_failed": failed,
        "expected_active_link_count": len(expected),
        "bound_active_link_count": sum(1 for target in expected if len(active_by_target.get(target, ())) == 1),
        "malformed_active_binding_stream_ids": sorted(malformed_active),
        "bindings": serialized_bindings,
        "active_bindings": active_by_target,
    }, binding_stream_ids


def _audit_stream_history(
    stream_ids: set[int],
    observations: Mapping[int, Sequence[SignalObservation]],
    stream_statuses: Mapping[int, str],
    begin_utc: datetime | None,
    active_bindings: Mapping[SignalLinkTarget, Sequence[TlsBinding]],
    gaps: list[dict[str, Any]],
) -> dict[str, Any]:
    failed = False
    links_by_stream: dict[int, list[SignalLinkTarget]] = defaultdict(list)
    for target, bindings in active_bindings.items():
        for binding in bindings:
            links_by_stream[binding.stream_id].append(target)

    rows: list[dict[str, Any]] = []
    initialized_links: set[SignalLinkTarget] = set()
    for stream_id in sorted(stream_ids):
        status = str(stream_statuses.get(stream_id, "missing")).strip().lower()
        if status != _COMPLETE_STREAM_STATUS:
            gaps.append(
                {
                    "code": "stream_not_complete",
                    "stream_id": stream_id,
                    "stream_status": status,
                }
            )
            failed = True

        values = list(observations.get(stream_id, ()))
        valid_preceding: list[SignalObservation] = []
        invalid_observation_count = 0
        for observation in values:
            if observation.stream_id != stream_id:
                invalid_observation_count += 1
                gaps.append(
                    {
                        "code": "observation_stream_id_mismatch",
                        "stream_id": stream_id,
                        "observation_stream_id": observation.stream_id,
                    }
                )
                failed = True
                continue
            if not _is_explicit_utc(observation.phenomenon_time_utc):
                invalid_observation_count += 1
                gaps.append(
                    {
                        "code": "observation_timestamp_not_utc",
                        "stream_id": stream_id,
                        "observation_id": observation.observation_id,
                    }
                )
                failed = True
                continue
            if not str(observation.result).strip():
                invalid_observation_count += 1
                gaps.append(
                    {
                        "code": "observation_state_empty",
                        "stream_id": stream_id,
                        "observation_id": observation.observation_id,
                    }
                )
                failed = True
                continue
            if begin_utc is not None and observation.phenomenon_time_utc <= begin_utc:
                valid_preceding.append(observation)

        preceding_state = ""
        preceding_time = ""
        if begin_utc is not None:
            if not valid_preceding:
                gaps.append(
                    {
                        "code": "missing_preceding_state",
                        "stream_id": stream_id,
                        "active_links": [_serialize_link(target) for target in sorted(links_by_stream[stream_id])],
                    }
                )
                failed = True
            else:
                latest_time = max(item.phenomenon_time_utc for item in valid_preceding)
                latest_states = {str(item.result) for item in valid_preceding if item.phenomenon_time_utc == latest_time}
                if len(latest_states) != 1:
                    gaps.append(
                        {
                            "code": "conflicting_preceding_state",
                            "stream_id": stream_id,
                            "phenomenon_time_utc": _utc_text(latest_time),
                            "states": sorted(latest_states),
                        }
                    )
                    failed = True
                else:
                    preceding_state = next(iter(latest_states))
                    preceding_time = _utc_text(latest_time)
                    initialized_links.update(links_by_stream[stream_id])

        rows.append(
            {
                "stream_id": stream_id,
                "stream_status": status,
                "observation_count": len(values),
                "invalid_observation_count": invalid_observation_count,
                "preceding_state_time_utc": preceding_time,
                "preceding_state": preceding_state,
                "active_links": [_serialize_link(target) for target in sorted(links_by_stream[stream_id])],
            }
        )

    expected_active_links = set(active_bindings)
    missing_initialized = sorted(expected_active_links - initialized_links)
    if missing_initialized:
        # The per-stream gaps above explain the source failure; this aggregate
        # makes the t=0 link gate machine-checkable for downstream replay code.
        failed = True

    return {
        "_failed": failed,
        "targeted_stream_count": len(stream_ids),
        "initialized_active_link_count": len(initialized_links & expected_active_links),
        "expected_active_link_count": len(expected_active_links),
        "missing_t0_active_links": [_serialize_link(target) for target in missing_initialized],
        "streams": rows,
    }


def _audit_evidence_artifacts(
    artifacts: Sequence[SignalEvidenceArtifact],
    gaps: list[dict[str, Any]],
) -> dict[str, Any]:
    failed = False
    roles_present: set[str] = set()
    names: set[str] = set()
    rows: list[dict[str, Any]] = []
    for artifact in artifacts:
        expected = artifact.expected_sha256.strip().lower()
        path = artifact.path.resolve()
        row: dict[str, Any] = {
            "name": artifact.name,
            "role": artifact.role,
            "path": str(path),
            "expected_sha256": expected,
            "actual_sha256": "",
        }
        if artifact.name in names:
            gaps.append({"code": "duplicate_evidence_artifact_name", "name": artifact.name})
            row["status"] = "blocked"
            failed = True
        names.add(artifact.name)
        if artifact.role not in {"input", "cache"}:
            gaps.append(
                {
                    "code": "invalid_evidence_artifact_role",
                    "name": artifact.name,
                    "role": artifact.role,
                }
            )
            row["status"] = "blocked"
            failed = True
        else:
            roles_present.add(artifact.role)
        if len(expected) != 64 or any(character not in "0123456789abcdef" for character in expected):
            gaps.append({"code": "evidence_sha256_required", "name": artifact.name})
            row["status"] = "blocked"
            failed = True
        elif not path.is_file():
            gaps.append({"code": "evidence_artifact_missing", "name": artifact.name, "path": str(path)})
            row["status"] = "blocked"
            failed = True
        else:
            actual = sha256_file(path)
            row["actual_sha256"] = actual
            if actual != expected:
                gaps.append(
                    {
                        "code": "evidence_sha256_mismatch",
                        "name": artifact.name,
                        "expected_sha256": expected,
                        "actual_sha256": actual,
                    }
                )
                row["status"] = "blocked"
                failed = True
            else:
                row["status"] = "pass"
        rows.append(row)

    missing_roles = sorted({"input", "cache"} - roles_present)
    if missing_roles:
        gaps.append({"code": "evidence_artifact_roles_missing", "roles": missing_roles})
        failed = True
    return {
        "_failed": failed,
        "required_roles": ["input", "cache"],
        "missing_roles": missing_roles,
        "artifacts": rows,
    }


def _build_live_capture_stage(spec: LiveCaptureSpec | None, *, supplied_observation_count: int) -> dict[str, Any]:
    gaps: list[dict[str, Any]] = []
    if spec is None:
        gaps.append({"code": "live_capture_checkpoint_spec_required"})
        capture_id = ""
        event_log_path = ""
        checkpoint_path = ""
        dedupe_fields: tuple[str, ...] = ()
        cursor_field = ""
        checkpoint_sha256 = ""
    else:
        capture_id = spec.capture_id
        event_log_path = str(spec.event_log_path.resolve())
        checkpoint_path = str(spec.checkpoint_path.resolve())
        dedupe_fields = tuple(dict.fromkeys(spec.dedupe_key_fields))
        cursor_field = spec.resume_cursor_field
        checkpoint_sha256 = spec.checkpoint_sha256.strip().lower()
        if not capture_id.strip():
            gaps.append({"code": "live_capture_id_required"})
        required_dedupe = {"stream_id", "phenomenon_time_utc"}
        if not required_dedupe.issubset(dedupe_fields):
            gaps.append(
                {
                    "code": "live_capture_dedupe_key_incomplete",
                    "missing_fields": sorted(required_dedupe - set(dedupe_fields)),
                }
            )
        if not cursor_field.strip():
            gaps.append({"code": "live_capture_resume_cursor_required"})

    return {
        "status": "capture_pending",
        "claim": "future live signal capture is pending; no historical signal state is claimed",
        "claim_status": "capture-pending",
        "next_action": "start or resume the append-only capture until the requested future window is complete",
        "exact_history": False,
        "gaps": gaps,
        "capture_contract": {
            "capture_id": capture_id,
            "status": "capture_pending",
            "append_only": True,
            "event_log_path": event_log_path,
            "deduplication": {
                "key_fields": list(dedupe_fields),
                "conflict_policy": "reject_conflicting_duplicate",
            },
            "resume": {
                "checkpoint_required": True,
                "checkpoint_path": checkpoint_path,
                "cursor_field": cursor_field,
                "checkpoint_sha256": checkpoint_sha256,
            },
            "historical_window": None,
            "historical_data_claimed": False,
            "supplied_historical_observations_consumed": False,
            "supplied_historical_observation_count": supplied_observation_count,
        },
    }


def _build_ocit_proxy_stage(*, va_controlled: bool | None, has_per_second_semantics: bool | None) -> dict[str, Any]:
    reasons = ["OCIT topology/program metadata is a proxy, not observed second-by-second signal history"]
    if va_controlled is True:
        reasons.append("VA=true means the realized phases depend on traffic-actuated controller state")
    elif va_controlled is None:
        reasons.append("the VA actuation flag is unknown")
    if has_per_second_semantics is not True:
        reasons.append("official per-second realized-state semantics are absent or unverified")
    return {
        "status": "unsupported",
        "claim": "official OCIT metadata cannot certify exact historical signal replay",
        "claim_status": "official-ocit-proxy-non-exact",
        "next_action": "collect complete observed primary and secondary signal history for an absolute UTC window",
        "exact_history": False,
        "gaps": [{"code": "official_ocit_proxy_non_exact", "reasons": reasons}],
        "ocit_proxy_assessment": {
            "va_controlled": va_controlled,
            "has_per_second_semantics": has_per_second_semantics,
            "supported_for_exact_history": False,
            "reasons": reasons,
        },
    }


def _stream_identity_errors(stream: SignalStream, requirement: SignalLayerRequirement) -> list[str]:
    errors: list[str] = []
    if stream.layer_name != requirement.layer_name:
        errors.append("layer_name")
    if requirement.node_id and _canonical_node_id(stream.node_id) != _canonical_node_id(requirement.node_id):
        errors.append("node_id")
    if requirement.connection_id and stream.connection_id != requirement.connection_id:
        errors.append("connection_id")
    if requirement.signal_group and stream.signal_group != requirement.signal_group:
        errors.append("signal_group")
    return errors


def _canonical_node_id(value: str) -> str:
    text = value.strip()
    return str(int(text)) if text.isdigit() else text


def _is_explicit_utc(value: datetime) -> bool:
    return value.tzinfo is not None and value.utcoffset() is not None and value.utcoffset().total_seconds() == 0


def _utc_text(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _serialize_link(target: SignalLinkTarget) -> dict[str, Any]:
    return {"sumo_tls_id": target.sumo_tls_id, "sumo_link_index": target.sumo_link_index}


def _next_action_for_gap(gap: Mapping[str, Any]) -> str:
    code = str(gap.get("code", ""))
    if "layer" in code or "stream" in code:
        return "obtain complete primary and secondary stream inventory/history, then rebuild the contract"
    if "binding" in code or "active_link" in code:
        return "complete and disambiguate every expected active SUMO TLS-link binding"
    if "sha256" in code or "artifact" in code:
        return "restore the exact hash-pinned cache and input artifacts"
    if "window" in code:
        return "provide a non-empty half-open window using explicit UTC timestamps"
    if "preceding" in code:
        return "fetch a preceding state for every targeted stream before rebuilding the t=0 replay"
    return "resolve the first structured signal-stage gap and rebuild the contract"
