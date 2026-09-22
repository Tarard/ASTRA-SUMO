from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from torii_sumo.core.candidate_contracts import file_sha256
from torii_sumo.core.digital_twin_mapping import DetectorMapping, write_detector_mapping
from torii_sumo.core.hamburg_named_replay import (
    HamburgNamedReplayError,
    _audit_signal_history_scope,
    _load_binding_manifest,
    _load_detector_binding_manifest,
    _load_signal_observation_manifest,
    _read_sumo_quality,
    _run_dynamic_sumo,
    _summarize_e1,
    _validate_binding_network,
    _validate_count_scope_manifest,
)


def test_sumo_quality_gate_blocks_teleports_and_collisions(tmp_path: Path) -> None:
    summary = tmp_path / "summary.xml"
    summary.write_text(
        """<summary>
  <step time="0" teleports="0" collisions="0" loaded="2" inserted="2" running="2" waiting="0" ended="0" arrived="0" halting="0"/>
  <step time="1" teleports="3" collisions="1" loaded="2" inserted="2" running="1" waiting="0" ended="1" arrived="1" halting="0"/>
</summary>\n""",
        encoding="utf-8",
    )

    report = _read_sumo_quality(summary)

    assert report["quality_gate"] == "blocked"
    assert report["teleport_count"] == 3
    assert report["collision_count"] == 1
    assert report["summary_final"]["arrived"] == "1"


def test_replay_requires_signal_binding_for_the_same_network(tmp_path: Path) -> None:
    replay_net = tmp_path / "replay.net.xml"
    bound_net = tmp_path / "bound.net.xml"
    replay_net.write_text("<net id=\"replay\"/>\n", encoding="utf-8")
    bound_net.write_text("<net id=\"bound\"/>\n", encoding="utf-8")
    binding = {
        "source": {
            "candidate_net": {
                "path": str(bound_net),
                "sha256": file_sha256(bound_net),
            }
        }
    }

    with pytest.raises(HamburgNamedReplayError, match="does not match replay network"):
        _validate_binding_network(binding, replay_net)

    replay_net.write_bytes(bound_net.read_bytes())
    _validate_binding_network(binding, replay_net)
    bound_net.write_text("<net id=\"mutated\"/>\n", encoding="utf-8")
    with pytest.raises(HamburgNamedReplayError, match="SHA-256 mismatch"):
        _validate_binding_network(binding, replay_net)


def test_replay_rejects_non_executable_signal_binding(tmp_path: Path) -> None:
    manifest = tmp_path / "binding.json"
    manifest.write_text(
        '{"schema":"torii.hamburg-named-signal-binding/v1","execution_gate":"blocked"}',
        encoding="utf-8",
    )

    with pytest.raises(HamburgNamedReplayError, match="not execution-ready"):
        _load_binding_manifest(manifest)


def test_replay_consumes_hash_bound_w3b_detector_mapping(tmp_path: Path) -> None:
    network = tmp_path / "candidate.net.xml"
    network.write_text("<net/>\n", encoding="utf-8")
    streams = tmp_path / "streams.json"
    streams.write_text('{"streams":[1]}\n', encoding="utf-8")
    mapping_file = tmp_path / "detector_mapping.csv"
    write_detector_mapping(
        mapping_file,
        [
            DetectorMapping(
                detector_id="detector-1",
                stream_id=1,
                node_id="2394",
                asset_id="asset-1",
                real_direction="Richtung 1",
                lane_use="all",
                longitude=10.0,
                latitude=53.0,
                official_map_lane="",
                official_map_distance_m=None,
                sumo_edge="edge",
                sumo_lane="edge_0",
                lane_position=10.0,
                distance_m=1.0,
                heading_error_deg=None,
                period=900,
                mapping_confidence="high",
                mapping_status="active",
                mapping_reason="fixture",
            )
        ],
    )
    manifest = tmp_path / "detector-binding.json"
    manifest.write_text(
        json.dumps(
            {
                "schema": "torii.hamburg-named-detector-binding/v1",
                "execution_gate": "pass",
                "gates": {"sensor_aggregation_semantics": "pass"},
                "source": {
                    "candidate_net": {
                        "path": str(network),
                        "sha256": file_sha256(network),
                    },
                    "count_stream_snapshot": {
                        "path": str(streams),
                        "sha256": file_sha256(streams),
                    },
                },
                "artifacts": {
                    "detector_mapping": {
                        "path": str(mapping_file),
                        "sha256": file_sha256(mapping_file),
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    _, mappings = _load_detector_binding_manifest(
        manifest,
        replay_net=network,
        count_stream_snapshot=streams,
        expected_stream_ids={1},
    )

    assert mappings[0].sumo_lane == "edge_0"
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    payload["gates"]["sensor_aggregation_semantics"] = "blocked"
    manifest.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(HamburgNamedReplayError, match="no approved shared-lane aggregation"):
        _load_detector_binding_manifest(
            manifest,
            replay_net=network,
            count_stream_snapshot=streams,
            expected_stream_ids={1},
        )
    payload["gates"]["sensor_aggregation_semantics"] = "pass"
    manifest.write_text(json.dumps(payload), encoding="utf-8")
    mapping_file.write_text("changed\n", encoding="utf-8")
    with pytest.raises(HamburgNamedReplayError, match="detector mapping SHA-256 mismatch"):
        _load_detector_binding_manifest(
            manifest,
            replay_net=network,
            count_stream_snapshot=streams,
            expected_stream_ids={1},
        )


def test_replay_count_values_are_bound_to_w3a(tmp_path: Path) -> None:
    streams = tmp_path / "streams.json"
    streams.write_text('{"streams":[]}\n', encoding="utf-8")
    counts = tmp_path / "counts.csv"
    counts.write_text("stream_id,begin,end,total\n", encoding="utf-8")
    other_counts = tmp_path / "other-counts.csv"
    other_counts.write_text("stream_id,begin,end,total\n1,0,900,1\n", encoding="utf-8")
    manifest = tmp_path / "count-scope.json"
    manifest.write_text(
        json.dumps(
            {
                "schema": "torii.hamburg-named-corridor-count-scope/v1",
                "execution_gate": "pass",
                "artifacts": {
                    "count_streams_raw": {
                        "path": str(streams),
                        "sha256": file_sha256(streams),
                    },
                    "counts_simulation_15min": {
                        "path": str(counts),
                        "sha256": file_sha256(counts),
                    },
                },
            }
        ),
        encoding="utf-8",
    )

    _validate_count_scope_manifest(
        manifest,
        count_stream_snapshot=streams,
        canonical_count_file=counts,
    )

    with pytest.raises(HamburgNamedReplayError, match="does not match the selected replay input"):
        _validate_count_scope_manifest(
            manifest,
            count_stream_snapshot=streams,
            canonical_count_file=other_counts,
        )


def test_signal_observations_are_bound_to_binding_and_event_bytes(tmp_path: Path) -> None:
    binding = tmp_path / "binding.json"
    binding.write_text('{"binding":1}\n', encoding="utf-8")
    other_binding = tmp_path / "other-binding.json"
    other_binding.write_text('{"binding":2}\n', encoding="utf-8")
    events = tmp_path / "tls-link-events.csv"
    events.write_text("time,tls_id,link_index,state\n", encoding="utf-8")
    manifest = tmp_path / "observations.json"
    manifest.write_text(
        json.dumps(
            {
                "schema": "torii.hamburg-named-signal-observations/v2",
                "execution_gate": "pass",
                "source": {
                    "binding_manifest": {
                        "path": str(binding),
                        "sha256": file_sha256(binding),
                    }
                },
                "artifacts": {"tls_link_events": str(events)},
                "artifact_identities": {
                    "tls_link_events": {
                        "path": str(events),
                        "sha256": file_sha256(events),
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    loaded = _load_signal_observation_manifest(manifest, binding_manifest=binding)

    assert loaded is not None
    assert loaded["validated_tls_link_events"] == str(events.resolve())
    with pytest.raises(HamburgNamedReplayError, match="does not match the selected replay input"):
        _load_signal_observation_manifest(manifest, binding_manifest=other_binding)
    events.write_text("changed\n", encoding="utf-8")
    with pytest.raises(HamburgNamedReplayError, match="TLS link events SHA-256 mismatch"):
        _load_signal_observation_manifest(manifest, binding_manifest=binding)


def test_sumo_quality_gate_passes_only_clean_summary(tmp_path: Path) -> None:
    summary = tmp_path / "summary.xml"
    summary.write_text(
        "<summary><step time=\"1\" teleports=\"0\" collisions=\"0\"/></summary>\n",
        encoding="utf-8",
    )

    report = _read_sumo_quality(summary)

    assert report["quality_gate"] == "pass"
    assert report["teleport_count"] == 0
    assert report["collision_count"] == 0


def test_dynamic_replay_does_not_promote_partial_inner_result(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    route_file = tmp_path / "demand.rou.xml"
    route_file.write_text("<routes/>", encoding="utf-8")
    summary = tmp_path / "summary.xml"
    summary.write_text(
        '<summary><step time="1" teleports="0" collisions="0"/></summary>',
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "torii_sumo.core.hamburg_named_replay.run_tls_detector_replay",
        lambda **_kwargs: {
            "status": "partial",
            "claim_status": "validation-incomplete",
            "artifacts": {"summary_file": str(summary)},
        },
    )

    report = _run_dynamic_sumo(
        net_path=tmp_path / "network.net.xml",
        route_file=route_file,
        e1_file=tmp_path / "e1.add.xml",
        e2_file=tmp_path / "e2.add.xml",
        tls_events_csv=tmp_path / "events.csv",
        expected_counts_csv=tmp_path / "counts.csv",
        output_dir=tmp_path / "output",
        begin=0,
        end=900,
        comparison_begin=0,
        comparison_end=900,
        sumo_binary="sumo",
    )

    assert report["status"] == "blocked"
    assert report["quality_gate"] == "blocked"


def test_e1_summary_exposes_missing_bins() -> None:
    report = _summarize_e1(
        [
            {
                "measurement_status": "matched",
                "expected_total": 10,
                "measured_nVehContrib": 8,
                "diff_nVehContrib_minus_expected": -2,
            },
            {
                "measurement_status": "missing",
                "expected_total": 7,
                "measured_nVehContrib": None,
                "diff_nVehContrib_minus_expected": None,
            },
        ]
    )

    assert report["matched"] == 1
    assert report["missing"] == 1
    assert report["total"] == 2
    assert report["expected"] == 10
    assert report["measured"] == 8


def test_signal_history_scope_blocks_short_official_window() -> None:
    report = _audit_signal_history_scope(
        {"window": {"begin_utc": "2026-07-18T06:00:00Z", "end_utc": "2026-07-18T08:00:00Z"}},
        simulation_begin=0,
        simulation_end=9000,
    )

    assert report["status"] == "review_required"
    assert report["history_window_seconds"] == 7200
    assert report["replay_window_seconds"] == 9000


def test_signal_history_scope_blocks_shifted_utc_window() -> None:
    report = _audit_signal_history_scope(
        {"window": {"begin_utc": "2026-07-18T06:00:00Z", "end_utc": "2026-07-18T08:00:00Z"}},
        counts=[
            SimpleNamespace(
                begin=0,
                end=900,
                source_begin_utc=datetime(2026, 7, 18, 8, tzinfo=timezone.utc),
                source_end_utc=datetime(2026, 7, 18, 8, 15, tzinfo=timezone.utc),
            )
        ],
        simulation_begin=0,
        simulation_end=7200,
    )

    assert report["status"] == "review_required"
    assert report["reason"] == "official signal history does not cover the replay count UTC window"
