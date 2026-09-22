from __future__ import annotations

import json
import re
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Lock
from typing import Any, Callable, Mapping

from torii_sumo.core.digital_twin import SignalStream
from torii_sumo.core.digital_twin_workflow import _primary_metadata_circuit_breaker_reason
from torii_sumo.core.hamburg_official import (
    HAMBURG_SIGNAL_SERVICE,
    OFFICIAL_SIGNAL_CATALOG_URL,
    SANDTORKAI_THREE_INTERSECTIONS,
    HamburgCorridorPreset,
    HamburgSignalAsset,
    SensorThingsClient,
    census_hamburg_signal_stream_coverage,
    download_hamburg_signal_assets,
    fetch_hamburg_signal_observations,
    fetch_hamburg_signal_streams,
    hamburg_sandtorkai_primary_signal_snapshot,
    probe_hamburg_signal_window_coverage,
)


UTC = timezone.utc
CollectionResult = tuple[list[dict[str, Any]], list[dict[str, Any]], list[str]]


def test_sandtorkai_signal_api_versions_match_official_catalog_resources() -> None:
    assert SANDTORKAI_THREE_INTERSECTIONS.primary_signal_api_base == (
        "https://tld.iot.hamburg.de/v1.0/"
    )
    assert SANDTORKAI_THREE_INTERSECTIONS.signal_api_base == "https://tld.iot.hamburg.de/v1.1/"
    assert OFFICIAL_SIGNAL_CATALOG_URL == (
        "https://suche.transparenz.hamburg.de/dataset/traffic-lights-data-hamburg6"
    )


def test_sandtorkai_primary_signal_snapshot_has_expected_official_movements() -> None:
    streams = hamburg_sandtorkai_primary_signal_snapshot()

    assert len(streams) == 27
    assert len({stream.stream_id for stream in streams}) == 27
    assert Counter(str(int(stream.node_id)).zfill(4) for stream in streams) == {
        "0228": 16,
        "2394": 8,
        "2421": 3,
    }
    assert {stream.layer_name for stream in streams} == {"primary_signal"}
    assert {stream.lane_type for stream in streams} == {"KFZ"}


def _stream(stream_id: int, *, layer_name: str = "primary_signal") -> SignalStream:
    return SignalStream(
        stream_id=stream_id,
        thing_id=stream_id + 1_000,
        node_id="0228",
        connection_id=str(stream_id),
        ingress_lane_id="1",
        egress_lane_id="2",
        lane_type="KFZ",
        signal_group="3",
        layer_name=layer_name,
        name=f"test stream {stream_id}",
    )


def _observation(observation_id: int, phenomenon_time: str, result: str) -> dict[str, Any]:
    return {
        "@iot.id": observation_id,
        "phenomenonTime": phenomenon_time,
        "result": result,
        "resultTime": phenomenon_time,
    }


def _response(*values: dict[str, Any], label: str = "request") -> CollectionResult:
    value_list = list(values)
    return value_list, [{"value": value_list}], [f"https://example.test/{label}"]


class _ScriptedClient:
    def __init__(
        self,
        handler: Callable[[dict[str, Any]], CollectionResult],
        *,
        base_url: str = "https://tld.iot.hamburg.de/v1.0/",
    ) -> None:
        self._handler = handler
        self._lock = Lock()
        self.base_url = base_url
        self.calls: list[dict[str, Any]] = []

    def collection(
        self,
        entity_path: str,
        *,
        params: Mapping[str, str | int] | None = None,
        record_limit: int | None = None,
    ) -> CollectionResult:
        call = {
            "entity_path": entity_path,
            "params": dict(params or {}),
            "record_limit": record_limit,
        }
        # Production code may fetch independent streams concurrently.  Keep the
        # fake deterministic while still exercising that public behavior.
        with self._lock:
            self.calls.append(call)
            return self._handler(call)


def _filter_bounds(call: Mapping[str, Any]) -> tuple[str, str]:
    filter_text = str(call["params"]["$filter"])
    match = re.search(r"phenomenonTime ge ([^ ]+) and phenomenonTime lt ([^ )]+)", filter_text)
    assert match is not None, filter_text
    return match.group(1), match.group(2)


def _stream_id(call: Mapping[str, Any]) -> int:
    match = re.search(r"Datastreams\((\d+)\)", str(call["entity_path"]))
    assert match is not None
    return int(match.group(1))


def test_primary_signal_uses_finite_preceding_lookback_and_merges_sorted_events() -> None:
    begin = datetime(2026, 7, 11, 10, 0, tzinfo=UTC)
    end = begin + timedelta(minutes=20)

    def handler(call: dict[str, Any]) -> CollectionResult:
        if call["params"].get("$orderby") == "phenomenonTime desc":
            return _response(_observation(1, "2026-07-11T09:55:00Z", "red"), label="preceding")
        # Deliberately return the window events out of order.  The fetcher owns
        # the stable chronological representation consumed by TLS replay.
        return _response(
            _observation(3, "2026-07-11T10:10:00Z", "green"),
            _observation(2, "2026-07-11T10:05:00Z", "amber"),
            label="window",
        )

    client = _ScriptedClient(handler)
    observations, raw = fetch_hamburg_signal_observations(
        client,  # type: ignore[arg-type]
        [_stream(101)],
        begin_utc=begin,
        end_utc=end,
        preceding_lookback=timedelta(minutes=30),
        chunk_duration=timedelta(minutes=30),
        max_retries=1,
        max_workers=1,
    )

    assert [item.phenomenon_time_utc for item in observations[101]] == [
        datetime(2026, 7, 11, 9, 55, tzinfo=UTC),
        datetime(2026, 7, 11, 10, 5, tzinfo=UTC),
        datetime(2026, 7, 11, 10, 10, tzinfo=UTC),
    ]
    assert [item.result for item in observations[101]] == ["red", "amber", "green"]

    preceding_call = next(
        call for call in client.calls if call["params"].get("$orderby") == "phenomenonTime desc"
    )
    assert preceding_call["record_limit"] == 1
    assert _filter_bounds(preceding_call) == (
        "2026-07-11T09:30:00Z",
        "2026-07-11T10:00:00Z",
    )
    assert isinstance(raw["pages_by_stream"]["101"]["preceding"], list)
    assert isinstance(raw["pages_by_stream"]["101"]["window"], list)


def test_window_timeout_falls_back_to_chunks_and_retries_only_failed_chunk() -> None:
    begin = datetime(2026, 7, 11, 10, 0, tzinfo=UTC)
    end = begin + timedelta(minutes=20)
    attempts: dict[tuple[str, str], int] = {}

    def handler(call: dict[str, Any]) -> CollectionResult:
        bounds = _filter_bounds(call)
        attempts[bounds] = attempts.get(bounds, 0) + 1
        if bounds == ("2026-07-11T10:00:00Z", "2026-07-11T10:20:00Z"):
            raise TimeoutError("whole-window timeout")
        if bounds == ("2026-07-11T10:00:00Z", "2026-07-11T10:10:00Z"):
            if attempts[bounds] == 1:
                raise TimeoutError("first chunk attempt timed out")
            return _response(_observation(10, "2026-07-11T10:05:00Z", "2"), label="chunk-1")
        if bounds == ("2026-07-11T10:10:00Z", "2026-07-11T10:20:00Z"):
            return _response(_observation(11, "2026-07-11T10:15:00Z", "3"), label="chunk-2")
        raise AssertionError(f"unexpected query bounds: {bounds}")

    client = _ScriptedClient(handler)
    observations, raw = fetch_hamburg_signal_observations(
        client,  # type: ignore[arg-type]
        [_stream(202)],
        begin_utc=begin,
        end_utc=end,
        include_preceding_state=False,
        chunk_duration=timedelta(minutes=10),
        max_retries=1,
        max_workers=1,
    )

    assert attempts == {
        ("2026-07-11T10:00:00Z", "2026-07-11T10:20:00Z"): 1,
        ("2026-07-11T10:00:00Z", "2026-07-11T10:10:00Z"): 2,
        ("2026-07-11T10:10:00Z", "2026-07-11T10:20:00Z"): 1,
    }
    assert [item.observation_id for item in observations[202]] == [10, 11]
    assert len(raw["pages_by_stream"]["202"]["window"]) == 2
    assert isinstance(raw["pages_by_stream"]["202"]["preceding"], list)


def test_real_client_timeout_uses_bounded_recent_desc_fallback() -> None:
    begin = datetime(2026, 7, 11, 10, 0, tzinfo=UTC)
    end = begin + timedelta(minutes=20)
    values = [
        _observation(40, "2026-07-11T09:55:00Z", "2"),
        _observation(41, "2026-07-11T10:05:00Z", "3"),
    ]

    def transport(request, _timeout):  # type: ignore[no-untyped-def]
        if "%24filter" in request.full_url:
            raise TimeoutError("bounded historical filter timed out")
        return json.dumps({"value": values}).encode()

    client = SensorThingsClient(
        "https://tld.iot.hamburg.de/v1.0/",
        timeout_seconds=1,
        transport=transport,
    )
    observations, raw = fetch_hamburg_signal_observations(
        client,
        [_stream(505)],
        begin_utc=begin,
        end_utc=end,
        preceding_lookback=timedelta(minutes=30),
        max_retries=0,
        max_workers=1,
    )

    assert [item.result for item in observations[505]] == ["2", "3"]
    stream_raw = raw["stream_results"]["505"]
    assert stream_raw["status"] == "ok"
    assert stream_raw["preceding"]["strategy"] == "recent_desc_after_timeout"
    assert stream_raw["window"]["strategy"] == "recent_desc_after_timeout"


def test_signal_window_screen_is_positive_hint_and_keeps_missing_streams_explicit() -> None:
    begin = datetime(2026, 7, 18, 14, 30, tzinfo=UTC)
    end = begin + timedelta(hours=2)

    def handler(call: dict[str, Any]) -> CollectionResult:
        stream_id = _stream_id(call)
        if stream_id == 601:
            return _response(_observation(61, "2026-07-18T14:31:00Z", "3"), label="present")
        return _response(label="empty")

    client = _ScriptedClient(handler)
    report = probe_hamburg_signal_window_coverage(
        client,  # type: ignore[arg-type]
        [_stream(601), _stream(602)],
        {"saturday": (begin, end)},
        max_retries=0,
        max_workers=1,
    )

    candidate = report["candidates"][0]
    assert report["screening_only"] is True
    assert candidate["screen_status"] == "incomplete_candidate"
    assert candidate["present_stream_count"] == 1
    assert candidate["missing_stream_ids"] == [602]
    assert candidate["stream_results"][0]["status"] == "present"
    assert candidate["stream_results"][1]["status"] == "empty"


def test_signal_coverage_census_keeps_empty_streams_and_endpoint_hints_explicit() -> None:
    def handler(call: dict[str, Any]) -> CollectionResult:
        stream_id = _stream_id(call)
        if stream_id == 603:
            if str(call["params"].get("$orderby", "")).endswith("asc"):
                return _response(_observation(63, "2026-06-23T22:00:00Z", "2"), label="earliest")
            return _response(_observation(64, "2026-07-20T05:00:00Z", "3"), label="latest")
        return _response(label="empty")

    report = census_hamburg_signal_stream_coverage(
        _ScriptedClient(handler),  # type: ignore[arg-type]
        [_stream(603), _stream(604)],
        max_retries=0,
        max_workers=1,
    )

    assert report["screening_only"] is True
    assert report["range_available_count"] == 1
    assert report["empty_stream_ids"] == [604]
    row = report["streams"][0]
    assert row["status"] == "range_available"
    assert row["range_begin_hint_utc"] == "2026-06-23T22:00:00Z"
    assert row["range_end_hint_utc"] == "2026-07-20T05:00:00Z"
    assert any("continuous history" in item for item in report["claim_boundary"]["does_not_prove"])


def test_permanent_error_is_isolated_to_one_stream_and_reported() -> None:
    begin = datetime(2026, 7, 11, 10, 0, tzinfo=UTC)
    end = begin + timedelta(minutes=20)

    def handler(call: dict[str, Any]) -> CollectionResult:
        stream_id = _stream_id(call)
        if stream_id == 303:
            raise RuntimeError("permanent upstream failure")
        assert stream_id == 304
        return _response(_observation(20, "2026-07-11T10:06:00Z", "green"), label="healthy")

    client = _ScriptedClient(handler)
    observations, raw = fetch_hamburg_signal_observations(
        client,  # type: ignore[arg-type]
        [
            _stream(303, layer_name="signal_program"),
            _stream(304, layer_name="signal_program"),
        ],
        begin_utc=begin,
        end_utc=end,
        chunk_duration=timedelta(minutes=10),
        max_retries=1,
        max_workers=2,
    )

    assert observations[303] == []
    assert [item.result for item in observations[304]] == ["green"]

    failed = raw["stream_results"]["303"]
    assert "status" in failed
    assert failed["status"] not in {"success", "ok", "complete"}
    assert failed["errors"]
    assert "permanent upstream failure" in str(failed["errors"])
    assert isinstance(raw["pages_by_stream"]["303"]["preceding"], list)
    assert isinstance(raw["pages_by_stream"]["303"]["window"], list)
    assert raw["stream_results"]["304"]["status"] in {"success", "ok", "complete"}


def test_cache_reuses_same_stream_and_window_without_client_calls(tmp_path: Path) -> None:
    begin = datetime(2026, 7, 11, 10, 0, tzinfo=UTC)
    end = begin + timedelta(minutes=20)
    cache_dir = tmp_path / "signal-cache"

    def handler(call: dict[str, Any]) -> CollectionResult:
        assert _stream_id(call) == 404
        return _response(_observation(30, "2026-07-11T10:07:00Z", "2"), label="cached")

    client = _ScriptedClient(handler)
    kwargs = {
        "begin_utc": begin,
        "end_utc": end,
        "chunk_duration": timedelta(minutes=30),
        "max_retries": 1,
        "max_workers": 1,
        "cache_dir": cache_dir,
    }

    first_observations, first_raw = fetch_hamburg_signal_observations(
        client,  # type: ignore[arg-type]
        [_stream(404, layer_name="signal_program")],
        **kwargs,
    )
    calls_after_first_fetch = len(client.calls)
    assert calls_after_first_fetch == 1
    assert cache_dir.is_dir() and any(cache_dir.iterdir())

    second_observations, second_raw = fetch_hamburg_signal_observations(
        client,  # type: ignore[arg-type]
        [_stream(404, layer_name="signal_program")],
        **kwargs,
    )

    assert len(client.calls) == calls_after_first_fetch
    assert second_observations == first_observations
    assert isinstance(first_raw["pages_by_stream"]["404"]["window"], list)
    assert isinstance(second_raw["pages_by_stream"]["404"]["window"], list)


def test_observation_cache_does_not_cross_api_versions(tmp_path: Path) -> None:
    begin = datetime(2026, 7, 11, 10, 0, tzinfo=UTC)
    end = begin + timedelta(minutes=20)
    cache_dir = tmp_path / "versioned-observation-cache"

    def handler(_call: dict[str, Any]) -> CollectionResult:
        return _response(_observation(31, "2026-07-11T10:07:00Z", "2"), label="versioned")

    v11_client = _ScriptedClient(handler, base_url="https://tld.iot.hamburg.de/v1.1/")
    _, v11_raw = fetch_hamburg_signal_observations(
        v11_client,  # type: ignore[arg-type]
        [_stream(406, layer_name="signal_program")],
        begin_utc=begin,
        end_utc=end,
        cache_dir=cache_dir,
    )
    v10_client = _ScriptedClient(handler, base_url="https://tld.iot.hamburg.de/v1.0/")
    _, first_v10_raw = fetch_hamburg_signal_observations(
        v10_client,  # type: ignore[arg-type]
        [_stream(406, layer_name="signal_program")],
        begin_utc=begin,
        end_utc=end,
        cache_dir=cache_dir,
    )
    calls_after_first_v10_fetch = len(v10_client.calls)
    _, second_v10_raw = fetch_hamburg_signal_observations(
        v10_client,  # type: ignore[arg-type]
        [_stream(406, layer_name="signal_program")],
        begin_utc=begin,
        end_utc=end,
        cache_dir=cache_dir,
    )

    assert len(v11_client.calls) == 1
    assert calls_after_first_v10_fetch == 1
    assert len(v10_client.calls) == calls_after_first_v10_fetch
    assert v11_raw["api_base_url"].endswith("/v1.1/")
    assert first_v10_raw["api_base_url"].endswith("/v1.0/")
    assert second_v10_raw["stream_results"]["406"]["cache_hit"] is True
    payloads = [json.loads(path.read_text(encoding="utf-8")) for path in cache_dir.glob("*.json")]
    assert len(payloads) == 2
    assert {payload["api_base_url"] for payload in payloads} == {
        "https://tld.iot.hamburg.de/v1.0/",
        "https://tld.iot.hamburg.de/v1.1/",
    }
    assert {payload["schema_version"] for payload in payloads} == {2}
    assert all(payload["captured_at_utc"].endswith("Z") for payload in payloads)
    v10_payload = next(payload for payload in payloads if payload["api_base_url"].endswith("/v1.0/"))
    assert second_v10_raw["stream_results"]["406"]["cache_captured_at_utc"] == (
        v10_payload["captured_at_utc"]
    )


def test_cache_reuses_structured_stream_failure(tmp_path: Path) -> None:
    begin = datetime(2026, 7, 11, 10, 0, tzinfo=UTC)
    end = begin + timedelta(minutes=20)

    def handler(_call: dict[str, Any]) -> CollectionResult:
        raise RuntimeError("stable upstream failure")

    client = _ScriptedClient(handler)
    kwargs = {
        "begin_utc": begin,
        "end_utc": end,
        "max_retries": 0,
        "cache_dir": tmp_path / "failed-cache",
    }
    first_observations, first_raw = fetch_hamburg_signal_observations(
        client,  # type: ignore[arg-type]
        [_stream(405, layer_name="signal_program")],
        **kwargs,
    )
    calls_after_first_fetch = len(client.calls)
    second_observations, second_raw = fetch_hamburg_signal_observations(
        client,  # type: ignore[arg-type]
        [_stream(405, layer_name="signal_program")],
        **kwargs,
    )

    assert first_observations[405] == second_observations[405] == []
    assert len(client.calls) == calls_after_first_fetch == 1
    assert first_raw["stream_results"]["405"]["status"] == "error"
    assert second_raw["stream_results"]["405"]["status"] == "error"
    assert second_raw["stream_results"]["405"]["cache_hit"] is True


def test_retry_incomplete_cache_refetches_primary_stream_without_t0_state(tmp_path: Path) -> None:
    begin = datetime(2026, 7, 11, 10, 0, tzinfo=UTC)
    end = begin + timedelta(minutes=20)
    cache_dir = tmp_path / "retryable-signal-cache"
    calls = 0

    def handler(call: dict[str, Any]) -> CollectionResult:
        nonlocal calls
        calls += 1
        if calls <= 2:
            return _response(label=f"empty-{calls}")
        if call["params"].get("$orderby") == "phenomenonTime desc":
            return _response(_observation(50, "2026-07-11T09:59:00Z", "1"), label="preceding")
        return _response(_observation(51, "2026-07-11T10:05:00Z", "3"), label="window")

    client = _ScriptedClient(handler)
    kwargs = {
        "begin_utc": begin,
        "end_utc": end,
        "chunk_duration": timedelta(minutes=30),
        "max_retries": 0,
        "max_workers": 1,
        "cache_dir": cache_dir,
    }
    first_observations, _ = fetch_hamburg_signal_observations(
        client, [_stream(407)], **kwargs  # type: ignore[arg-type]
    )
    assert first_observations[407] == []
    calls_after_first_fetch = calls

    second_observations, second_raw = fetch_hamburg_signal_observations(
        client,
        [_stream(407)],
        retry_incomplete_cache=True,
        **kwargs,  # type: ignore[arg-type]
    )

    assert calls_after_first_fetch == 2
    assert calls == 4
    assert [item.result for item in second_observations[407]] == ["1", "3"]
    assert second_raw["stream_results"]["407"]["cache_hit"] is False


def _signal_datastream(stream_id: int, node_id: str) -> dict[str, Any]:
    return {
        "@iot.id": stream_id,
        "name": f"Primary signal heads at {node_id}_1",
        "properties": {
            "serviceName": HAMBURG_SIGNAL_SERVICE,
            "layerName": "primary_signal",
            "signalGroupID": "K1",
        },
        "Thing": {
            "@iot.id": stream_id + 10_000,
            "properties": {
                "trafficLightsID": node_id,
                "connectionID": "1",
                "ingressLaneID": "1",
                "egressLaneID": "2",
                "laneType": "KFZ",
            },
            "Locations": [],
        },
    }


class _SignalMetadataClient:
    def __init__(self, *, base_url: str = "https://tld.iot.hamburg.de/v1.0/") -> None:
        self.base_url = base_url
        self.pending_node = ""
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def collection(
        self,
        entity_path: str,
        *,
        params: Mapping[str, str | int] | None = None,
        record_limit: int | None = None,
    ) -> CollectionResult:
        del record_limit
        request_params = dict(params or {})
        self.calls.append((entity_path, request_params))
        if entity_path == "Datastreams":
            filter_text = str(request_params["$filter"])
            if "trafficLightsID eq '228'" in filter_text:
                return _response(_signal_datastream(501, "228"), label="direct-228")
            if "trafficLightsID eq '2421'" in filter_text:
                self.pending_node = "2421"
                raise TimeoutError("navigation filter timed out")
            if "trafficLightsID eq '2394'" in filter_text:
                self.pending_node = "2394"
                raise TimeoutError("navigation filter timed out")
        if entity_path == "Locations" and self.pending_node == "2421":
            datastream = _signal_datastream(502, "2421")
            thing = dict(datastream["Thing"])
            thing["Datastreams"] = [{key: value for key, value in datastream.items() if key != "Thing"}]
            self.pending_node = ""
            return _response({"Things": [thing]}, label="spatial-2421")
        if entity_path == "Locations" and self.pending_node == "2394":
            raise RuntimeError("spatial fallback unavailable")
        raise AssertionError((entity_path, request_params))


def test_signal_stream_metadata_keeps_successful_nodes_and_uses_spatial_fallback() -> None:
    client = _SignalMetadataClient()

    streams, raw = fetch_hamburg_signal_streams(
        client,  # type: ignore[arg-type]
        ["228", "2421", "2394"],
        layers=("primary_signal",),
        max_retries=1,
        max_workers=1,
    )

    assert [(stream.node_id, stream.stream_id) for stream in streams] == [
        ("228", 501),
        ("2421", 502),
    ]
    assert raw["node_results"]["228"]["strategy"] == "datastream_filter"
    assert raw["node_results"]["2421"]["strategy"] == "locations_spatial_fallback"
    assert raw["node_results"]["2394"]["status"] == "error"
    assert raw["failed_node_ids"] == ["2394"]
    assert raw["pages_by_node"]["228"]
    assert raw["pages_by_node"]["2421"]


def test_signal_stream_metadata_cache_avoids_second_api_call(tmp_path: Path) -> None:
    client = _SignalMetadataClient()
    kwargs = {
        "layers": ("primary_signal",),
        "max_retries": 0,
        "max_workers": 1,
        "cache_dir": tmp_path / "metadata-cache",
    }

    first_streams, first_raw = fetch_hamburg_signal_streams(
        client,  # type: ignore[arg-type]
        ["228"],
        **kwargs,
    )
    first_call_count = len(client.calls)
    second_streams, second_raw = fetch_hamburg_signal_streams(
        client,  # type: ignore[arg-type]
        ["228"],
        **kwargs,
    )

    assert second_streams == first_streams
    assert len(client.calls) == first_call_count == 1
    assert first_raw["node_results"]["228"]["cache_hit"] is False
    assert second_raw["node_results"]["228"]["cache_hit"] is True


def test_signal_metadata_cache_does_not_cross_api_versions(tmp_path: Path) -> None:
    cache_dir = tmp_path / "versioned-metadata-cache"
    kwargs = {
        "layers": ("primary_signal",),
        "max_retries": 0,
        "max_workers": 1,
        "cache_dir": cache_dir,
    }
    v11_client = _SignalMetadataClient(base_url="https://tld.iot.hamburg.de/v1.1/")
    _, v11_raw = fetch_hamburg_signal_streams(
        v11_client,  # type: ignore[arg-type]
        ["228"],
        **kwargs,
    )
    v10_client = _SignalMetadataClient(base_url="https://tld.iot.hamburg.de/v1.0/")
    _, first_v10_raw = fetch_hamburg_signal_streams(
        v10_client,  # type: ignore[arg-type]
        ["228"],
        **kwargs,
    )
    calls_after_first_v10_fetch = len(v10_client.calls)
    _, second_v10_raw = fetch_hamburg_signal_streams(
        v10_client,  # type: ignore[arg-type]
        ["228"],
        **kwargs,
    )

    assert len(v11_client.calls) == 1
    assert calls_after_first_v10_fetch == 1
    assert len(v10_client.calls) == calls_after_first_v10_fetch
    assert v11_raw["api_base_url"].endswith("/v1.1/")
    assert first_v10_raw["api_base_url"].endswith("/v1.0/")
    assert second_v10_raw["node_results"]["228"]["cache_hit"] is True
    payloads = [json.loads(path.read_text(encoding="utf-8")) for path in cache_dir.glob("*.json")]
    assert len(payloads) == 2
    assert {payload["api_base_url"] for payload in payloads} == {
        "https://tld.iot.hamburg.de/v1.0/",
        "https://tld.iot.hamburg.de/v1.1/",
    }
    assert {payload["schema"] for payload in payloads} == {
        "torii.hamburg-signal-metadata-cache.v2"
    }
    assert all(payload["captured_at_utc"].endswith("Z") for payload in payloads)
    v10_payload = next(payload for payload in payloads if payload["api_base_url"].endswith("/v1.0/"))
    assert second_v10_raw["node_results"]["228"]["cache_captured_at_utc"] == (
        v10_payload["captured_at_utc"]
    )


def test_primary_metadata_circuit_breaker_is_not_timeout_specific() -> None:
    raw = {
        "node_results": {
            "228": {"status": "error", "errors": [{"message": "HTTP 503"}]},
            "2421": {"status": "empty"},
            "2394": {"status": "error", "errors": [{"message": "connection reset"}]},
        }
    }

    reason = _primary_metadata_circuit_breaker_reason(raw, ("228", "2421", "2394"))

    assert reason == "all live primary metadata node queries failed or were unavailable"
    assert "timed out" not in reason
    raw["node_results"]["228"] = {"status": "ok"}
    assert _primary_metadata_circuit_breaker_reason(raw, ("228", "2421", "2394")) is None


class _AssetClient:
    def __init__(self) -> None:
        self.urls: list[str] = []

    def bytes(self, url: str) -> bytes:
        self.urls.append(url)
        return f"asset:{url}".encode()


def test_signal_asset_download_reuses_nonempty_files(tmp_path: Path) -> None:
    preset = HamburgCorridorPreset(
        preset_id="test",
        display_name="test",
        bbox="0,0,1,1",
        count_node_ids=("0001",),
        signal_node_ids=("1",),
        node_names={"0001": "test"},
        node_centers={"0001": (0.5, 0.5)},
        signal_assets=(
            HamburgSignalAsset(
                node_id="0001",
                map_xml="MAP_version_1.xml",
                map_kml="MAP_version_1.kml",
                ocit_xml="OCIT-C/MAP_version_1.xml",
            ),
        ),
        signal_asset_base="https://assets.example/",
    )
    client = _AssetClient()

    paths, first_manifest = download_hamburg_signal_assets(
        client,  # type: ignore[arg-type]
        preset,
        tmp_path,
    )
    first_call_count = len(client.urls)
    _, second_manifest = download_hamburg_signal_assets(
        client,  # type: ignore[arg-type]
        preset,
        tmp_path,
    )

    assert first_call_count == 3
    assert len(client.urls) == first_call_count
    assert all(item["cache_hit"] is False for item in first_manifest)
    assert all(item["cache_hit"] is True for item in second_manifest)
    assert all(Path(path).stat().st_size > 0 for path in paths["0001"].values())
    assert not list(tmp_path.glob("*.tmp"))
