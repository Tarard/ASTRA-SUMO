from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from typing import Callable

import pytest

from torii_sumo.core.digital_twin import CountObservation, CountStream, WindowSelection
from torii_sumo.core.digital_twin_timeline import (
    aggregate_simulation_counts,
    build_simulation_window,
    rank_complete_simulation_windows,
    select_comparison_count_rows,
    validate_warmup_count_completeness,
)


UTC = timezone.utc


def _stream(stream_id: int) -> CountStream:
    return CountStream(
        stream_id=stream_id,
        thing_id=stream_id + 100,
        node_id="0228",
        asset_id=f"Z.{stream_id}",
        direction="east",
        lane_use="straight",
        longitude=9.98,
        latitude=53.54,
    )


def _formal_window(*, stream_count: int = 1) -> WindowSelection:
    begin = datetime(2026, 7, 11, 8, tzinfo=UTC)
    return WindowSelection(
        local_date=date(2026, 7, 11),
        timezone_name="Europe/Berlin",
        begin_utc=begin,
        end_utc=begin + timedelta(hours=2),
        duration_seconds=7200,
        source_bin_seconds=300,
        score=1000,
        complete=True,
        completeness_ratio=1.0,
        expected_cells=stream_count * 24,
        present_cells=stream_count * 24,
        missing_cells=(),
    )


def _full_simulation_observations(
    streams: list[CountStream],
    simulation_begin: datetime,
) -> dict[int, list[CountObservation]]:
    result: dict[int, list[CountObservation]] = {}
    for stream in streams:
        rows: list[CountObservation] = []
        for index in range(30):
            begin = simulation_begin + timedelta(minutes=5 * index)
            rows.append(
                CountObservation(
                    stream_id=stream.stream_id,
                    observation_id=stream.stream_id * 1000 + index,
                    begin_utc=begin,
                    end_utc=begin + timedelta(minutes=5),
                    count=1 if index < 6 else 2,
                )
            )
        result[stream.stream_id] = rows
    return result


def _daily_observations(
    stream: CountStream,
    day_begin: datetime,
    value_at_index: Callable[[int], int],
) -> dict[int, list[CountObservation]]:
    rows: list[CountObservation] = []
    for index in range(288):
        begin = day_begin + timedelta(minutes=5 * index)
        count = value_at_index(index)
        rows.append(
            CountObservation(
                stream_id=stream.stream_id,
                observation_id=index,
                begin_utc=begin,
                end_utc=begin + timedelta(minutes=5),
                count=count,
            )
        )
    return {stream.stream_id: rows}


def test_builds_30_minute_warmup_around_unchanged_formal_window() -> None:
    formal = _formal_window()

    simulation = build_simulation_window(formal)

    assert simulation.formal_window is formal
    assert simulation.simulation_begin_utc == formal.begin_utc - timedelta(minutes=30)
    assert simulation.simulation_end_utc == formal.end_utc
    assert simulation.simulation_duration_seconds == 9000
    assert simulation.comparison_begin == 1800
    assert simulation.comparison_end == 9000
    assert formal.duration_seconds == 7200


def test_rejects_warmup_that_cannot_align_with_15_minute_outputs() -> None:
    with pytest.raises(ValueError, match="multiple of output_bin_seconds"):
        build_simulation_window(_formal_window(), warmup_seconds=600)


def test_formal_count_completeness_does_not_hide_missing_warmup_cell() -> None:
    streams = [_stream(1), _stream(2)]
    formal = _formal_window(stream_count=2)
    simulation = build_simulation_window(formal)
    observations = _full_simulation_observations(streams, simulation.simulation_begin_utc)
    missing_timestamp = simulation.simulation_begin_utc + timedelta(minutes=10)
    observations[2] = [
        row for row in observations[2] if row.begin_utc != missing_timestamp
    ]

    coverage = validate_warmup_count_completeness(streams, observations, simulation)

    assert formal.complete is True
    assert coverage.complete is False
    assert coverage.expected_cells == 12
    assert coverage.present_cells == 11
    assert coverage.missing_cells == ((2, missing_timestamp.isoformat().replace("+00:00", "Z")),)
    with pytest.raises(ValueError, match="simulation count interval is incomplete"):
        aggregate_simulation_counts(streams, observations, simulation)


def test_warmup_completeness_rejects_duplicate_source_cell() -> None:
    streams = [_stream(1)]
    simulation = build_simulation_window(_formal_window())
    observations = _full_simulation_observations(streams, simulation.simulation_begin_utc)
    observations[1].append(observations[1][0])

    with pytest.raises(ValueError, match="duplicate observation cell"):
        validate_warmup_count_completeness(streams, observations, simulation)


def test_aggregates_ten_simulation_bins_and_selects_eight_formal_bins() -> None:
    streams = [_stream(1)]
    simulation = build_simulation_window(_formal_window())
    observations = _full_simulation_observations(streams, simulation.simulation_begin_utc)

    rows = aggregate_simulation_counts(streams, observations, simulation)
    comparison_rows = select_comparison_count_rows(rows, simulation)

    assert len(rows) == 10
    assert [(row.begin, row.end) for row in rows] == [
        (begin, begin + 900) for begin in range(0, 9000, 900)
    ]
    assert rows[0].source_begin_utc == simulation.simulation_begin_utc
    assert [row.count for row in rows[:2]] == [3, 3]
    assert all(row.count == 6 for row in rows[2:])
    assert len(comparison_rows) == 8
    assert comparison_rows[0].begin == 1800
    assert comparison_rows[-1].end == 9000
    assert sum(row.count for row in comparison_rows) == 48


def test_comparison_filter_rejects_a_bin_crossing_warmup_boundary() -> None:
    streams = [_stream(1)]
    simulation = build_simulation_window(_formal_window())
    observations = _full_simulation_observations(streams, simulation.simulation_begin_utc)
    rows = aggregate_simulation_counts(streams, observations, simulation)
    rows[1] = replace(rows[1], begin=1500, end=2400)

    with pytest.raises(ValueError, match="crosses a comparison boundary"):
        select_comparison_count_rows(rows, simulation)


def test_zero_warmup_preserves_the_legacy_zero_based_two_hour_timeline() -> None:
    streams = [_stream(1)]
    formal = _formal_window()
    simulation = build_simulation_window(formal, warmup_seconds=0)
    observations = _full_simulation_observations(streams, formal.begin_utc)
    observations[1] = observations[1][:24]

    coverage = validate_warmup_count_completeness(streams, observations, simulation)
    rows = aggregate_simulation_counts(streams, observations, simulation)

    assert coverage.complete is True
    assert coverage.expected_cells == 0
    assert simulation.simulation_begin_utc == formal.begin_utc
    assert simulation.simulation_duration_seconds == 7200
    assert len(rows) == 8
    assert select_comparison_count_rows(rows, simulation) == rows


def test_ranking_falls_back_when_busiest_formal_window_lacks_a_warmup_cell() -> None:
    stream = _stream(1)
    day_begin = datetime(2026, 7, 10, 22, tzinfo=UTC)

    def value_at_index(index: int) -> int:
        if 24 <= index < 48:
            return 100
        if 72 <= index < 96:
            return 80
        return 1

    observations = _daily_observations(stream, day_begin, value_at_index)
    missing_warmup_time = day_begin + timedelta(hours=1, minutes=55)
    observations[1] = [
        row for row in observations[1] if row.begin_utc != missing_warmup_time
    ]

    ranked = rank_complete_simulation_windows(
        [stream],
        observations,
        local_date=date(2026, 7, 11),
    )

    rejected_busiest_begin = day_begin + timedelta(hours=2)
    assert all(
        window.formal_window.begin_utc != rejected_busiest_begin for window in ranked
    )
    assert ranked[0].formal_window.begin_utc == day_begin + timedelta(hours=6)
    assert ranked[0].formal_window.score == 24 * 80
    assert ranked[0].simulation_begin_utc == day_begin + timedelta(hours=5, minutes=30)


def test_ranking_ignores_warmup_volume_and_breaks_score_ties_by_start_time() -> None:
    stream = _stream(1)
    day_begin = datetime(2026, 7, 10, 22, tzinfo=UTC)
    observations = _daily_observations(
        stream,
        day_begin,
        lambda index: 1000 if index < 6 else 1,
    )

    ranked = rank_complete_simulation_windows(
        [stream],
        observations,
        local_date=date(2026, 7, 11),
    )

    assert ranked
    assert {window.formal_window.score for window in ranked} == {24}
    assert ranked[0].simulation_begin_utc == day_begin
    assert ranked[0].formal_window.begin_utc == day_begin + timedelta(minutes=30)
    assert [
        window.formal_window.begin_utc for window in ranked[:3]
    ] == sorted(window.formal_window.begin_utc for window in ranked[:3])
