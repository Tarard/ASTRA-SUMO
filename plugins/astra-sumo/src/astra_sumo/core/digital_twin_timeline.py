from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta
from typing import Mapping, Sequence
from zoneinfo import ZoneInfo

from .digital_twin import (
    UTC,
    CanonicalCount,
    CountObservation,
    CountStream,
    WindowSelection,
    aggregate_window_counts,
)


@dataclass(frozen=True)
class SimulationWindow:
    """Map a formal observation window onto a simulation timeline with warm-up."""

    formal_window: WindowSelection
    warmup_seconds: int
    output_bin_seconds: int
    simulation_begin_utc: datetime
    simulation_end_utc: datetime
    simulation_duration_seconds: int
    comparison_begin: int
    comparison_end: int

    def __post_init__(self) -> None:
        formal = self.formal_window
        _require_aware(formal.begin_utc, "formal_window.begin_utc")
        _require_aware(formal.end_utc, "formal_window.end_utc")
        if formal.duration_seconds <= 0 or formal.source_bin_seconds <= 0:
            raise ValueError("formal window duration and source bin must be positive")
        if formal.end_utc - formal.begin_utc != timedelta(seconds=formal.duration_seconds):
            raise ValueError("formal window timestamps do not match duration_seconds")
        if self.warmup_seconds < 0:
            raise ValueError("warmup_seconds must be non-negative")
        if self.warmup_seconds % formal.source_bin_seconds:
            raise ValueError("warmup_seconds must be a multiple of the source bin")
        if self.output_bin_seconds <= 0 or self.output_bin_seconds % formal.source_bin_seconds:
            raise ValueError("output_bin_seconds must be a positive multiple of the source bin")
        if self.warmup_seconds % self.output_bin_seconds:
            raise ValueError("warmup_seconds must be a multiple of output_bin_seconds")
        if formal.duration_seconds % self.output_bin_seconds:
            raise ValueError("formal duration must be a multiple of output_bin_seconds")
        expected_begin = formal.begin_utc - timedelta(seconds=self.warmup_seconds)
        expected_duration = self.warmup_seconds + formal.duration_seconds
        if self.simulation_begin_utc != expected_begin:
            raise ValueError("simulation_begin_utc does not match formal begin minus warm-up")
        if self.simulation_end_utc != formal.end_utc:
            raise ValueError("simulation_end_utc must equal the formal window end")
        if self.simulation_duration_seconds != expected_duration:
            raise ValueError("simulation_duration_seconds is inconsistent")
        if self.comparison_begin != self.warmup_seconds:
            raise ValueError("comparison_begin must equal warmup_seconds")
        if self.comparison_end != expected_duration:
            raise ValueError("comparison_end must equal simulation duration")


@dataclass(frozen=True)
class WarmupCountCoverage:
    complete: bool
    begin_utc: datetime
    end_utc: datetime
    source_bin_seconds: int
    expected_cells: int
    present_cells: int
    missing_cells: tuple[tuple[int, str], ...]


def build_simulation_window(
    formal_window: WindowSelection,
    *,
    warmup_seconds: int = 1800,
    output_bin_seconds: int = 900,
) -> SimulationWindow:
    """Build the 0-based simulation and comparison timeline for a formal window."""

    simulation_duration = warmup_seconds + formal_window.duration_seconds
    return SimulationWindow(
        formal_window=formal_window,
        warmup_seconds=warmup_seconds,
        output_bin_seconds=output_bin_seconds,
        simulation_begin_utc=formal_window.begin_utc - timedelta(seconds=warmup_seconds),
        simulation_end_utc=formal_window.end_utc,
        simulation_duration_seconds=simulation_duration,
        comparison_begin=warmup_seconds,
        comparison_end=simulation_duration,
    )


def rank_complete_simulation_windows(
    streams: Sequence[CountStream],
    observations: Mapping[int, Sequence[CountObservation]],
    *,
    local_date: date,
    timezone_name: str = "Europe/Berlin",
    formal_duration_seconds: int = 7200,
    warmup_seconds: int = 1800,
    source_bin_seconds: int = 300,
    output_bin_seconds: int = 900,
) -> list[SimulationWindow]:
    """Rank count-complete daily candidates for subsequent signal preflight.

    Completeness covers warm-up plus the formal interval. The activity score
    covers only the formal interval, so warm-up traffic cannot change which
    complete two-hour period is considered busiest.
    """

    if not streams:
        raise ValueError("at least one count stream is required")
    if formal_duration_seconds <= 0 or source_bin_seconds <= 0:
        raise ValueError("formal duration and source bin must be positive")
    if formal_duration_seconds % source_bin_seconds:
        raise ValueError("formal duration must be a multiple of source_bin_seconds")
    if warmup_seconds < 0 or warmup_seconds % source_bin_seconds:
        raise ValueError("warmup_seconds must be a non-negative multiple of the source bin")
    if output_bin_seconds <= 0 or output_bin_seconds % source_bin_seconds:
        raise ValueError("output_bin_seconds must be a positive multiple of the source bin")
    if warmup_seconds % output_bin_seconds or formal_duration_seconds % output_bin_seconds:
        raise ValueError("warm-up and formal duration must align with output_bin_seconds")
    stream_ids = [stream.stream_id for stream in streams]
    if len(stream_ids) != len(set(stream_ids)):
        raise ValueError("count stream ids must be unique")

    zone = ZoneInfo(timezone_name)
    day_begin_local = datetime.combine(local_date, datetime.min.time(), tzinfo=zone)
    day_end_local = day_begin_local + timedelta(days=1)
    day_begin_utc = day_begin_local.astimezone(UTC)
    day_end_utc = day_end_local.astimezone(UTC)
    cells: dict[tuple[int, datetime], int] = {}
    for stream_id in stream_ids:
        for observation in observations.get(stream_id, ()):
            _require_aware(observation.begin_utc, "observation.begin_utc")
            timestamp = observation.begin_utc.astimezone(UTC)
            if not (day_begin_utc <= timestamp < day_end_utc):
                continue
            offset_seconds = int((timestamp - day_begin_utc).total_seconds())
            if offset_seconds % source_bin_seconds:
                continue
            key = (stream_id, timestamp)
            if key in cells:
                raise ValueError(
                    f"duplicate observation cell for stream {stream_id} at {timestamp.isoformat()}"
                )
            if observation.count < 0:
                raise ValueError(
                    f"negative observation count for stream {stream_id} at {timestamp.isoformat()}"
                )
            cells[key] = observation.count

    simulation_duration = warmup_seconds + formal_duration_seconds
    simulation_bins = simulation_duration // source_bin_seconds
    formal_bins = formal_duration_seconds // source_bin_seconds
    first_formal_begin = day_begin_utc + timedelta(seconds=warmup_seconds)
    last_formal_begin = day_end_utc - timedelta(seconds=formal_duration_seconds)
    if first_formal_begin > last_formal_begin:
        return []

    ranked: list[SimulationWindow] = []
    formal_begin = first_formal_begin
    while formal_begin <= last_formal_begin:
        simulation_begin = formal_begin - timedelta(seconds=warmup_seconds)
        complete = True
        for stream_id in stream_ids:
            for index in range(simulation_bins):
                timestamp = simulation_begin + timedelta(seconds=index * source_bin_seconds)
                if (stream_id, timestamp) not in cells:
                    complete = False
                    break
            if not complete:
                break
        if complete:
            score = sum(
                cells[
                    (
                        stream_id,
                        formal_begin + timedelta(seconds=index * source_bin_seconds),
                    )
                ]
                for stream_id in stream_ids
                for index in range(formal_bins)
            )
            expected_formal_cells = len(stream_ids) * formal_bins
            formal = WindowSelection(
                local_date=local_date,
                timezone_name=timezone_name,
                begin_utc=formal_begin,
                end_utc=formal_begin + timedelta(seconds=formal_duration_seconds),
                duration_seconds=formal_duration_seconds,
                source_bin_seconds=source_bin_seconds,
                score=score,
                complete=True,
                completeness_ratio=1.0,
                expected_cells=expected_formal_cells,
                present_cells=expected_formal_cells,
                missing_cells=(),
            )
            ranked.append(
                build_simulation_window(
                    formal,
                    warmup_seconds=warmup_seconds,
                    output_bin_seconds=output_bin_seconds,
                )
            )
        formal_begin += timedelta(seconds=source_bin_seconds)

    return sorted(
        ranked,
        key=lambda window: (
            -window.formal_window.score,
            window.formal_window.begin_utc,
        ),
    )


def validate_warmup_count_completeness(
    streams: Sequence[CountStream],
    observations: Mapping[int, Sequence[CountObservation]],
    window: SimulationWindow,
) -> WarmupCountCoverage:
    """Audit every five-minute source cell in the warm-up interval."""

    coverage = _count_coverage(
        streams,
        observations,
        begin_utc=window.simulation_begin_utc,
        end_utc=window.formal_window.begin_utc,
        source_bin_seconds=window.formal_window.source_bin_seconds,
    )
    return WarmupCountCoverage(
        complete=coverage.complete,
        begin_utc=window.simulation_begin_utc,
        end_utc=window.formal_window.begin_utc,
        source_bin_seconds=window.formal_window.source_bin_seconds,
        expected_cells=coverage.expected_cells,
        present_cells=coverage.present_cells,
        missing_cells=coverage.missing_cells,
    )


def aggregate_simulation_counts(
    streams: Sequence[CountStream],
    observations: Mapping[int, Sequence[CountObservation]],
    window: SimulationWindow,
    *,
    require_complete: bool = True,
) -> list[CanonicalCount]:
    """Aggregate warm-up and formal counts on the 0-based simulation timeline."""

    coverage = _count_coverage(
        streams,
        observations,
        begin_utc=window.simulation_begin_utc,
        end_utc=window.simulation_end_utc,
        source_bin_seconds=window.formal_window.source_bin_seconds,
    )
    if require_complete and not coverage.complete:
        raise ValueError(
            "simulation count interval is incomplete: "
            f"{coverage.present_cells}/{coverage.expected_cells} source cells"
        )
    simulation_selection = replace(
        window.formal_window,
        begin_utc=window.simulation_begin_utc,
        end_utc=window.simulation_end_utc,
        duration_seconds=window.simulation_duration_seconds,
        complete=coverage.complete,
        completeness_ratio=(
            coverage.present_cells / coverage.expected_cells
            if coverage.expected_cells
            else 0.0
        ),
        expected_cells=coverage.expected_cells,
        present_cells=coverage.present_cells,
        missing_cells=coverage.missing_cells,
    )
    return aggregate_window_counts(
        streams,
        observations,
        simulation_selection,
        output_bin_seconds=window.output_bin_seconds,
    )


def select_comparison_count_rows(
    rows: Sequence[CanonicalCount],
    window: SimulationWindow,
) -> list[CanonicalCount]:
    """Select complete detector bins in the formal comparison interval."""

    selected: list[CanonicalCount] = []
    for row in rows:
        if row.begin < 0 or row.end > window.simulation_duration_seconds:
            raise ValueError(
                f"count row {row.detector_id!r} lies outside the simulation window: "
                f"[{row.begin}, {row.end})"
            )
        overlaps = row.end > window.comparison_begin and row.begin < window.comparison_end
        contained = row.begin >= window.comparison_begin and row.end <= window.comparison_end
        if overlaps and not contained:
            raise ValueError(
                f"count row {row.detector_id!r} crosses a comparison boundary: "
                f"[{row.begin}, {row.end})"
            )
        if contained:
            selected.append(row)
    if rows and not selected:
        raise ValueError("count rows contain no formal comparison intervals")
    return selected


@dataclass(frozen=True)
class _CountCoverage:
    complete: bool
    expected_cells: int
    present_cells: int
    missing_cells: tuple[tuple[int, str], ...]


def _count_coverage(
    streams: Sequence[CountStream],
    observations: Mapping[int, Sequence[CountObservation]],
    *,
    begin_utc: datetime,
    end_utc: datetime,
    source_bin_seconds: int,
) -> _CountCoverage:
    _require_aware(begin_utc, "begin_utc")
    _require_aware(end_utc, "end_utc")
    if not streams:
        raise ValueError("at least one count stream is required")
    if end_utc < begin_utc:
        raise ValueError("end_utc must not be earlier than begin_utc")
    duration_seconds = int((end_utc - begin_utc).total_seconds())
    if source_bin_seconds <= 0 or duration_seconds % source_bin_seconds:
        raise ValueError("coverage duration must be a multiple of source_bin_seconds")
    stream_ids = [stream.stream_id for stream in streams]
    if len(stream_ids) != len(set(stream_ids)):
        raise ValueError("count stream ids must be unique")

    indexed: set[tuple[int, datetime]] = set()
    for stream_id in stream_ids:
        for observation in observations.get(stream_id, ()):
            _require_aware(observation.begin_utc, "observation.begin_utc")
            timestamp = observation.begin_utc.astimezone(UTC)
            if not (begin_utc <= timestamp < end_utc):
                continue
            key = (stream_id, timestamp)
            if key in indexed:
                raise ValueError(
                    f"duplicate observation cell for stream {stream_id} at {timestamp.isoformat()}"
                )
            indexed.add(key)

    expected_per_stream = duration_seconds // source_bin_seconds
    missing: list[tuple[int, str]] = []
    present = 0
    for stream_id in stream_ids:
        for index in range(expected_per_stream):
            timestamp = begin_utc + timedelta(seconds=index * source_bin_seconds)
            if (stream_id, timestamp.astimezone(UTC)) in indexed:
                present += 1
            else:
                missing.append(
                    (
                        stream_id,
                        timestamp.astimezone(UTC).isoformat().replace("+00:00", "Z"),
                    )
                )
    expected = len(stream_ids) * expected_per_stream
    return _CountCoverage(
        complete=not missing,
        expected_cells=expected,
        present_cells=present,
        missing_cells=tuple(missing),
    )


def _require_aware(value: datetime, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must include a UTC offset")
