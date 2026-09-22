from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping

import pytest

from torii_sumo.core.hamburg_named_signal_observations import (
    HamburgSignalObservationError,
    census_hamburg_named_signal_stream_coverage,
    materialize_hamburg_named_signal_observations,
    screen_hamburg_named_signal_windows,
)
from torii_sumo.core.candidate_contracts import file_sha256


UTC = timezone.utc


class _Client:
    base_url = "https://example.test/v1.0/"

    def __init__(self, values_by_stream: Mapping[int, list[dict[str, Any]]]) -> None:
        self.values_by_stream = values_by_stream

    def collection(self, entity_path: str, *, params: Mapping[str, str | int] | None = None, record_limit: int | None = None):  # type: ignore[no-untyped-def]
        stream_id = int(entity_path.split("(")[1].split(")")[0])
        values = list(self.values_by_stream.get(stream_id, []))
        if params and str(params.get("$orderby", "")).endswith("desc"):
            values = values[:1]
        return values, [{"value": values}], [f"https://example.test/{stream_id}"]


def _binding_fixture(tmp_path: Path, *, streams: tuple[int, ...] = (101, 102)) -> Path:
    bindings = []
    for index, stream_id in enumerate(streams):
        bindings.append(
            {
                "stream_id": stream_id,
                "node_id": "2349",
                "connection_id": str(index + 1),
                "ingress_lane_id": str(index + 1),
                "egress_lane_id": str(index + 8),
                "signal_group": "K1",
                "controller_id": "hh-map-2349",
                "link_index": index,
                "candidate_connections": [
                    {
                        "from": f"edge-{index}",
                        "fromLane": 0,
                        "to": f"out-{index}",
                        "toLane": 0,
                        "linkIndex": index,
                    }
                ],
            }
        )
    artifact = tmp_path / "bindings.json"
    artifact.write_text(
        json.dumps({"schema": "torii.hamburg-named-signal-binding/v1", "bindings": bindings}),
        encoding="utf-8",
    )
    manifest = tmp_path / "binding.manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "schema": "torii.hamburg-named-signal-binding/v1",
                "execution_gate": "pass",
                "automatic_promotion_gate": "blocked",
                "missing_official_signal_node_ids": ["2403"],
                "binding_artifact": {"path": str(artifact), "sha256": file_sha256(artifact)},
            }
        ),
        encoding="utf-8",
    )
    return manifest


def test_signal_history_rejects_non_executable_or_mutated_binding_evidence(tmp_path: Path) -> None:
    begin = datetime(2026, 7, 18, 14, 30, tzinfo=UTC)
    manifest = _binding_fixture(tmp_path, streams=(91,))
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    payload["execution_gate"] = "blocked"
    manifest.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(HamburgSignalObservationError, match="not execution-ready"):
        materialize_hamburg_named_signal_observations(
            binding_manifest=manifest,
            output_dir=tmp_path / "blocked-out",
            begin_utc=begin,
            end_utc=begin + timedelta(hours=2),
            client=_Client({}),  # type: ignore[arg-type]
        )

    payload["execution_gate"] = "pass"
    manifest.write_text(json.dumps(payload), encoding="utf-8")
    Path(payload["binding_artifact"]["path"]).write_text("{}", encoding="utf-8")
    with pytest.raises(HamburgSignalObservationError, match="SHA-256 mismatch"):
        materialize_hamburg_named_signal_observations(
            binding_manifest=manifest,
            output_dir=tmp_path / "mutated-out",
            begin_utc=begin,
            end_utc=begin + timedelta(hours=2),
            client=_Client({}),  # type: ignore[arg-type]
        )


def _observation(observation_id: int, timestamp: str, result: str) -> dict[str, Any]:
    return {
        "@iot.id": observation_id,
        "phenomenonTime": timestamp,
        "result": result,
        "resultTime": timestamp,
    }


def test_materialize_signal_history_writes_events_and_keeps_missing_node_blocked(tmp_path: Path) -> None:
    begin = datetime(2026, 7, 18, 14, 30, tzinfo=UTC)
    end = begin + timedelta(hours=2)
    manifest = _binding_fixture(tmp_path)
    client = _Client(
        {
            101: [
                _observation(1, "2026-07-18T14:29:00Z", "2"),
                _observation(2, "2026-07-18T14:45:00Z", "3"),
            ],
            102: [
                _observation(3, "2026-07-18T14:29:30Z", "1"),
            ],
        }
    )

    report = materialize_hamburg_named_signal_observations(
        binding_manifest=manifest,
        output_dir=tmp_path / "out",
        begin_utc=begin,
        end_utc=end,
        client=client,  # type: ignore[arg-type]
        max_workers=1,
    )

    assert report["status"] == "partial"
    assert report["execution_gate"] == "pass"
    assert report["automatic_promotion_gate"] == "blocked"
    assert report["event_stats"]["event_count"] == 3
    event_file = Path(report["artifacts"]["tls_link_events"])
    assert event_file.is_file()
    assert report["artifact_identities"]["tls_link_events"] == {
        "path": str(event_file),
        "sha256": file_sha256(event_file),
    }
    assert "simulation_time" in event_file.read_text(encoding="utf-8").splitlines()[0]
    assert Path(report["manifest_file"]).is_file()


def test_incomplete_or_missing_preceding_state_blocks_execution(tmp_path: Path) -> None:
    begin = datetime(2026, 7, 18, 14, 30, tzinfo=UTC)
    end = begin + timedelta(hours=2)
    report = materialize_hamburg_named_signal_observations(
        binding_manifest=_binding_fixture(tmp_path, streams=(201,)),
        output_dir=tmp_path / "out",
        begin_utc=begin,
        end_utc=end,
        client=_Client({201: []}),  # type: ignore[arg-type]
        max_workers=1,
    )
    assert report["status"] == "blocked"
    assert report["execution_gate"] == "blocked"
    assert report["incomplete_stream_ids"] == [201]


def test_signal_group_projection_recovers_silent_sibling_without_guessing_phase(tmp_path: Path) -> None:
    begin = datetime(2026, 7, 18, 14, 30, tzinfo=UTC)
    end = begin + timedelta(hours=2)
    report = materialize_hamburg_named_signal_observations(
        binding_manifest=_binding_fixture(tmp_path, streams=(211, 212)),
        output_dir=tmp_path / "projected-out",
        begin_utc=begin,
        end_utc=end,
        client=_Client(
            {
                211: [
                    _observation(1, "2026-07-18T14:29:00Z", "1"),
                    _observation(2, "2026-07-18T14:45:00Z", "3"),
                ],
                212: [],
            }
        ),  # type: ignore[arg-type]
        allow_signal_group_projection=True,
        max_workers=1,
    )

    assert report["status"] == "partial"
    assert report["execution_gate"] == "pass"
    assert report["incomplete_stream_ids"] == []
    assert report["projected_complete_stream_count"] == 1
    assert report["signal_group_projection"]["derived_streams"][0]["source_stream_id"] == 211
    assert report["signal_group_projection"]["derived_streams"][0]["signal_group"] == "K1"


def test_window_requires_explicit_zero_utc_offset(tmp_path: Path) -> None:
    with pytest.raises(HamburgSignalObservationError, match="zero UTC offset"):
        materialize_hamburg_named_signal_observations(
            binding_manifest=_binding_fixture(tmp_path, streams=(301,)),
            output_dir=tmp_path / "out",
            begin_utc=datetime(2026, 7, 18, 16, 30, tzinfo=timezone(timedelta(hours=2))),
            end_utc=datetime(2026, 7, 18, 18, 30, tzinfo=timezone(timedelta(hours=2))),
            client=_Client({301: []}),  # type: ignore[arg-type]
        )


def test_signal_window_screen_writes_fail_closed_manifest(tmp_path: Path) -> None:
    begin = datetime(2026, 7, 18, 14, 30, tzinfo=UTC)
    end = begin + timedelta(hours=2)
    report = screen_hamburg_named_signal_windows(
        binding_manifest=_binding_fixture(tmp_path, streams=(401, 402)),
        output_dir=tmp_path / "screen",
        candidate_windows={"saturday": (begin, end)},
        client=_Client(
            {
                401: [_observation(1, "2026-07-18T14:31:00Z", "2")],
                402: [],
            }
        ),  # type: ignore[arg-type]
        max_workers=1,
    )

    assert report["status"] == "blocked"
    assert report["execution_gate"] == "blocked"
    assert report["automatic_promotion_gate"] == "blocked"
    assert report["screen"]["candidates"][0]["missing_stream_ids"] == [402]
    assert Path(report["manifest_file"]).is_file()


def test_signal_coverage_census_writes_endpoint_hints_without_promoting_history(tmp_path: Path) -> None:
    manifest = _binding_fixture(tmp_path, streams=(501, 502))
    report = census_hamburg_named_signal_stream_coverage(
        binding_manifest=manifest,
        output_dir=tmp_path / "census",
        client=_Client(
            {
                501: [
                    _observation(1, "2026-06-23T22:00:00Z", "2"),
                    _observation(2, "2026-07-20T05:00:00Z", "3"),
                ],
                502: [],
            }
        ),  # type: ignore[arg-type]
        max_workers=1,
    )

    assert report["status"] == "blocked"
    assert report["execution_gate"] == "blocked"
    assert report["automatic_promotion_gate"] == "blocked"
    assert report["coverage"]["empty_stream_ids"] == [502]
    assert report["coverage"]["streams"][0]["range_begin_hint_utc"] == "2026-06-23T22:00:00Z"
    assert report["next_action"] == "resolve_official_signal_publication_gap_or_change_scope"
    assert Path(report["manifest_file"]).is_file()


def test_signal_coverage_census_confirms_official_node_publication_gap(tmp_path: Path) -> None:
    manifest = _binding_fixture(tmp_path, streams=(601, 602))
    identity = tmp_path / "lsa-identity.json"
    identity.write_text(
        json.dumps(
            {
                "schema": "torii.hamburg-lsa-node-identity-evidence/v1",
                "decision": "pass",
                "selections": [
                    {"selected_node": {"node_id": "2403"}},
                ],
            }
        ),
        encoding="utf-8",
    )
    report = census_hamburg_named_signal_stream_coverage(
        binding_manifest=manifest,
        output_dir=tmp_path / "census-with-identity",
        lsa_identity_manifest=identity,
        client=_Client(
            {
                601: [_observation(1, "2026-06-23T22:00:00Z", "2")],
                602: [_observation(2, "2026-07-20T05:00:00Z", "3")],
            }
        ),  # type: ignore[arg-type]
        max_workers=1,
    )

    assert report["execution_gate"] == "pass"
    assert report["publication_gap"]["decision"] == "confirmed_official_node_without_published_tld_binding"
    assert report["official_node_identity"]["selected_node_ids"] == ["2403"]
