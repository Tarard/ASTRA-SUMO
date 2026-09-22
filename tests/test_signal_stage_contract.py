from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from torii_sumo.core.digital_twin import SignalObservation, SignalStream
from torii_sumo.core.digital_twin_mapping import TlsBinding
from torii_sumo.core.hamburg_official import sha256_file
from torii_sumo.core.signal_stage_contract import (
    SIGNAL_STAGE_SCHEMA,
    LiveCaptureSpec,
    SignalEvidenceArtifact,
    SignalLayerRequirement,
    SignalLinkTarget,
    build_signal_stage_manifest,
    write_signal_stage_manifest,
)


UTC = timezone.utc


def _stream(stream_id: int, layer_name: str) -> SignalStream:
    return SignalStream(
        stream_id=stream_id,
        thing_id=stream_id + 1000,
        node_id="0228",
        connection_id="17",
        ingress_lane_id="19",
        egress_lane_id="25",
        lane_type="KFZ",
        signal_group="K8",
        layer_name=layer_name,
        name=f"{layer_name}-{stream_id}",
    )


def _binding(stream_id: int = 101, *, link_index: int = 3) -> TlsBinding:
    return TlsBinding(
        stream_id=stream_id,
        node_id="228",
        connection_id="17",
        signal_group="K8",
        official_ingress_lane="19",
        official_egress_lane="25",
        sumo_from_lane="in_0",
        sumo_to_lane="out_0",
        sumo_tls_id="tls-228",
        sumo_link_index=link_index,
        mapping_confidence="high",
        mapping_status="active",
        mapping_reason="test",
    )


def _observation(stream_id: int, at: datetime, state: str = "red") -> SignalObservation:
    return SignalObservation(
        stream_id=stream_id,
        observation_id=stream_id * 10,
        phenomenon_time_utc=at,
        result=state,
    )


def _requirements(*, secondary_stream_id: int | None = 102) -> list[SignalLayerRequirement]:
    common = {
        "target_id": "0228:17:K8",
        "node_id": "228",
        "connection_id": "17",
        "signal_group": "K8",
    }
    return [
        SignalLayerRequirement(layer_name="primary_signal", stream_id=101, **common),
        SignalLayerRequirement(layer_name="secondary_signal", stream_id=secondary_stream_id, **common),
    ]


def _artifacts(tmp_path: Path) -> list[SignalEvidenceArtifact]:
    input_file = tmp_path / "catalog.json"
    cache_file = tmp_path / "observations.json"
    input_file.write_text('{"catalog": 1}\n', encoding="utf-8")
    cache_file.write_text('{"observations": 2}\n', encoding="utf-8")
    return [
        SignalEvidenceArtifact("catalog", "input", input_file, sha256_file(input_file)),
        SignalEvidenceArtifact("history-cache", "cache", cache_file, sha256_file(cache_file)),
    ]


def _exact_manifest(tmp_path: Path, **overrides: object) -> dict[str, object]:
    begin = datetime(2026, 7, 11, 14, 15, tzinfo=UTC)
    arguments: dict[str, object] = {
        "signal_mode": "exact_history",
        "begin_utc": begin,
        "end_utc": begin + timedelta(hours=2),
        "signal_streams": [_stream(101, "primary_signal"), _stream(102, "secondary_signal")],
        "observations": {
            101: [_observation(101, begin - timedelta(seconds=1))],
            102: [_observation(102, begin - timedelta(seconds=2), "dark")],
        },
        "tls_bindings": [_binding()],
        "expected_active_links": [SignalLinkTarget("tls-228", 3)],
        "stream_statuses": {101: "complete", 102: "complete"},
        "layer_requirements": _requirements(),
        "evidence_artifacts": _artifacts(tmp_path),
        "demand_stage": {"status": "pass", "claim_status": "demand-ready"},
    }
    arguments.update(overrides)
    return build_signal_stage_manifest(**arguments)  # type: ignore[arg-type]


def test_exact_history_passes_only_with_full_layers_bindings_t0_and_hashes(tmp_path: Path) -> None:
    manifest = _exact_manifest(tmp_path)

    assert manifest["schema"] == SIGNAL_STAGE_SCHEMA
    assert manifest["status"] == "pass"
    assert manifest["claim_status"] == "exact-history-ready"
    assert manifest["exact_history"] is True
    assert manifest["stages"]["demand"] == {"status": "pass", "claim_status": "demand-ready"}
    signal_stage = manifest["stages"]["signal"]
    assert signal_stage["gaps"] == []
    assert all(gate["status"] == "pass" for gate in signal_stage["gates"].values())
    stream_gate = signal_stage["gates"]["stream_history_completeness"]
    assert stream_gate["initialized_active_link_count"] == 1
    assert stream_gate["missing_t0_active_links"] == []


def test_missing_secondary_is_a_structured_blocking_gap(tmp_path: Path) -> None:
    manifest = _exact_manifest(
        tmp_path,
        signal_streams=[_stream(101, "primary_signal")],
        observations={101: [_observation(101, datetime(2026, 7, 11, 14, 14, tzinfo=UTC))]},
        stream_statuses={101: "complete"},
        layer_requirements=_requirements(secondary_stream_id=None),
    )

    assert manifest["status"] == "blocked"
    assert manifest["exact_history"] is False
    signal_stage = manifest["stages"]["signal"]
    assert signal_stage["gates"]["primary_secondary_completeness"]["status"] == "blocked"
    assert {
        (gap["code"], gap.get("layer_name")) for gap in signal_stage["gaps"]
    } >= {("missing_layer_stream", "secondary_signal")}
    # A signal gap is not allowed to erase an independently valid demand result.
    assert manifest["stages"]["demand"]["status"] == "pass"


def test_missing_binding_and_missing_t0_state_fail_closed(tmp_path: Path) -> None:
    begin = datetime(2026, 7, 11, 14, 15, tzinfo=UTC)
    manifest = _exact_manifest(
        tmp_path,
        tls_bindings=[],
        observations={
            101: [_observation(101, begin + timedelta(minutes=1))],
            102: [_observation(102, begin - timedelta(seconds=1), "dark")],
        },
    )

    gaps = manifest["stages"]["signal"]["gaps"]
    assert manifest["status"] == "blocked"
    assert any(gap["code"] == "missing_active_link_binding" for gap in gaps)
    assert any(gap["code"] == "missing_preceding_state" and gap["stream_id"] == 101 for gap in gaps)


def test_partial_stream_non_utc_window_and_hash_mismatch_all_block(tmp_path: Path) -> None:
    artifacts = _artifacts(tmp_path)
    bad_artifacts = [
        artifacts[0],
        SignalEvidenceArtifact(
            artifacts[1].name,
            artifacts[1].role,
            artifacts[1].path,
            "0" * 64,
        ),
    ]
    local_offset = timezone(timedelta(hours=2))
    manifest = _exact_manifest(
        tmp_path,
        begin_utc=datetime(2026, 7, 11, 16, 15, tzinfo=local_offset),
        end_utc=datetime(2026, 7, 11, 18, 15, tzinfo=local_offset),
        stream_statuses={101: "partial", 102: "complete"},
        evidence_artifacts=bad_artifacts,
    )

    gaps = manifest["stages"]["signal"]["gaps"]
    assert manifest["status"] == "blocked"
    assert {gap["code"] for gap in gaps} >= {
        "absolute_utc_window_required",
        "stream_not_complete",
        "evidence_sha256_mismatch",
    }


def test_live_capture_only_emits_append_only_checkpoint_contract(tmp_path: Path) -> None:
    supplied = _observation(101, datetime(2026, 7, 1, tzinfo=UTC))
    manifest = build_signal_stage_manifest(
        signal_mode="live_capture",
        observations={101: [supplied]},
        live_capture=LiveCaptureSpec(
            capture_id="hamburg-future-1",
            event_log_path=tmp_path / "events.jsonl",
            checkpoint_path=tmp_path / "capture.checkpoint.json",
        ),
        demand_stage={"status": "pass"},
    )

    assert manifest["status"] == "capture_pending"
    assert manifest["exact_history"] is False
    assert manifest["stages"]["demand"]["status"] == "pass"
    capture = manifest["stages"]["signal"]["capture_contract"]
    assert capture["append_only"] is True
    assert capture["deduplication"]["key_fields"] == [
        "stream_id",
        "observation_id",
        "phenomenon_time_utc",
        "result",
    ]
    assert capture["resume"]["checkpoint_required"] is True
    assert capture["historical_window"] is None
    assert capture["historical_data_claimed"] is False
    assert capture["supplied_historical_observations_consumed"] is False
    assert capture["supplied_historical_observation_count"] == 1


@pytest.mark.parametrize(
    ("va_controlled", "per_second_semantics"),
    [(True, True), (False, False), (True, False), (None, None)],
)
def test_official_ocit_proxy_is_unsupported_and_never_exact(
    va_controlled: bool | None,
    per_second_semantics: bool | None,
) -> None:
    manifest = build_signal_stage_manifest(
        signal_mode="official_ocit_proxy",
        ocit_va_controlled=va_controlled,
        ocit_has_per_second_semantics=per_second_semantics,
        demand_stage={"status": "pass"},
    )

    assert manifest["status"] == "unsupported"
    assert manifest["claim_status"] == "official-ocit-proxy-non-exact"
    assert manifest["exact_history"] is False
    assessment = manifest["stages"]["signal"]["ocit_proxy_assessment"]
    assert assessment["supported_for_exact_history"] is False
    assert manifest["stages"]["demand"]["status"] == "pass"


def test_unknown_signal_mode_is_rejected_instead_of_falling_back() -> None:
    with pytest.raises(ValueError, match="unsupported signal_mode"):
        build_signal_stage_manifest(signal_mode="pretend_history")


def test_manifest_writer_uses_content_hash_and_rejects_wrong_schema(tmp_path: Path) -> None:
    manifest = build_signal_stage_manifest(
        signal_mode="live_capture",
        live_capture=LiveCaptureSpec(
            capture_id="future",
            event_log_path=tmp_path / "events.jsonl",
            checkpoint_path=tmp_path / "checkpoint.json",
        ),
    )
    destination = tmp_path / "signal-stage.manifest.json"

    digest = write_signal_stage_manifest(destination, manifest)

    assert digest == sha256_file(destination)
    with pytest.raises(ValueError, match="schema"):
        write_signal_stage_manifest(tmp_path / "bad.json", {"schema": "wrong"})
