from __future__ import annotations

from pathlib import Path
import xml.etree.ElementTree as ET

from pydantic import BaseModel


class SummaryMetrics(BaseModel):
    path: str
    exists: bool
    valid_xml: bool
    last_time: float | None
    loaded: int | None
    inserted: int | None
    arrived: int | None
    running: int | None
    waiting: int | None
    teleports: int | None
    collisions: int | None
    discarded: int | None
    completion_ratio: float | None
    warnings: list[str]


class TripinfoMetrics(BaseModel):
    path: str
    exists: bool
    valid_xml: bool
    trip_count: int
    mean_duration: float | None
    mean_waiting_time: float | None
    mean_time_loss: float | None
    warnings: list[str]


class RunOutputInspection(BaseModel):
    role: str
    status: str
    summary: SummaryMetrics | None
    tripinfo: TripinfoMetrics | None
    warnings: list[str]


class PairOutputInspectionReport(BaseModel):
    status: str
    baseline: RunOutputInspection
    variant: RunOutputInspection
    paired_warnings: list[str]
    claim_status: str

    def to_dict(self) -> dict[str, object]:
        return self.model_dump(mode="json")


def inspect_run_outputs(
    role: str,
    *,
    summary_path: Path | None,
    tripinfo_path: Path | None,
) -> RunOutputInspection:
    summary = inspect_summary(summary_path) if summary_path else None
    tripinfo = inspect_tripinfo(tripinfo_path) if tripinfo_path else None
    warnings: list[str] = []
    if summary is None:
        warnings.append("summary.xml path not provided")
    elif summary.warnings:
        warnings.extend(summary.warnings)
    if tripinfo is None:
        warnings.append("tripinfo.xml path not provided")
    elif tripinfo.warnings:
        warnings.extend(tripinfo.warnings)

    status = "pass" if not warnings else "warn"
    if summary is not None and (not summary.exists or not summary.valid_xml):
        status = "fail"
    if tripinfo is not None and (not tripinfo.exists or not tripinfo.valid_xml):
        status = "fail"

    return RunOutputInspection(
        role=role,
        status=status,
        summary=summary,
        tripinfo=tripinfo,
        warnings=warnings,
    )


def inspect_output_pair(
    *,
    baseline_summary: Path | None,
    baseline_tripinfo: Path | None,
    variant_summary: Path | None,
    variant_tripinfo: Path | None,
) -> PairOutputInspectionReport:
    baseline = inspect_run_outputs(
        "baseline",
        summary_path=baseline_summary,
        tripinfo_path=baseline_tripinfo,
    )
    variant = inspect_run_outputs(
        "variant",
        summary_path=variant_summary,
        tripinfo_path=variant_tripinfo,
    )
    paired_warnings = _paired_warnings(baseline, variant)
    statuses = {baseline.status, variant.status}
    if "fail" in statuses:
        status = "fail"
    elif paired_warnings or "warn" in statuses:
        status = "warn"
    else:
        status = "pass"
    return PairOutputInspectionReport(
        status=status,
        baseline=baseline,
        variant=variant,
        paired_warnings=paired_warnings,
        claim_status=(
            "diagnostic-demo"
            if status in {"pass", "warn"} and _has_required_summary_evidence(baseline, variant)
            else "construction-invalid"
        ),
    )


def inspect_summary(path: Path) -> SummaryMetrics:
    if not path.exists():
        return SummaryMetrics(
            path=str(path),
            exists=False,
            valid_xml=False,
            last_time=None,
            loaded=None,
            inserted=None,
            arrived=None,
            running=None,
            waiting=None,
            teleports=None,
            collisions=None,
            discarded=None,
            completion_ratio=None,
            warnings=[f"summary file does not exist: {path}"],
        )

    try:
        root = ET.parse(path).getroot()
    except ET.ParseError as exc:
        return SummaryMetrics(
            path=str(path),
            exists=True,
            valid_xml=False,
            last_time=None,
            loaded=None,
            inserted=None,
            arrived=None,
            running=None,
            waiting=None,
            teleports=None,
            collisions=None,
            discarded=None,
            completion_ratio=None,
            warnings=[f"invalid summary XML: {exc}"],
        )
    except OSError as exc:
        return SummaryMetrics(
            path=str(path),
            exists=True,
            valid_xml=False,
            last_time=None,
            loaded=None,
            inserted=None,
            arrived=None,
            running=None,
            waiting=None,
            teleports=None,
            collisions=None,
            discarded=None,
            completion_ratio=None,
            warnings=[f"could not read summary XML: {exc}"],
        )

    steps = [element for element in root.iter() if _local_name(element.tag) == "step"]
    if not steps:
        return SummaryMetrics(
            path=str(path),
            exists=True,
            valid_xml=True,
            last_time=None,
            loaded=None,
            inserted=None,
            arrived=None,
            running=None,
            waiting=None,
            teleports=None,
            collisions=None,
            discarded=None,
            completion_ratio=None,
            warnings=["summary has no step elements"],
        )

    last = steps[-1]
    loaded = _int_attr(last, "loaded")
    arrived = _int_attr(last, "arrived")
    completion_ratio = None
    if loaded is not None and loaded > 0 and arrived is not None:
        completion_ratio = arrived / loaded

    warnings: list[str] = []
    running = _int_attr(last, "running")
    waiting = _int_attr(last, "waiting")
    teleports = _int_attr(last, "teleports")
    collisions = _int_attr(last, "collisions")
    discarded = _int_attr(last, "discarded")
    if running and running > 0:
        warnings.append(f"{running} vehicles still running at final summary step")
    if waiting and waiting > 0:
        warnings.append(f"{waiting} vehicles waiting for insertion at final summary step")
    if teleports and teleports > 0:
        warnings.append(f"{teleports} teleports reported at final summary step")
    if collisions and collisions > 0:
        warnings.append(f"{collisions} collisions reported at final summary step")
    if discarded and discarded > 0:
        warnings.append(f"{discarded} vehicles discarded at final summary step")

    return SummaryMetrics(
        path=str(path),
        exists=True,
        valid_xml=True,
        last_time=_float_attr(last, "time"),
        loaded=loaded,
        inserted=_int_attr(last, "inserted"),
        arrived=arrived,
        running=running,
        waiting=waiting,
        teleports=teleports,
        collisions=collisions,
        discarded=discarded,
        completion_ratio=completion_ratio,
        warnings=warnings,
    )


def inspect_tripinfo(path: Path) -> TripinfoMetrics:
    if not path.exists():
        return TripinfoMetrics(
            path=str(path),
            exists=False,
            valid_xml=False,
            trip_count=0,
            mean_duration=None,
            mean_waiting_time=None,
            mean_time_loss=None,
            warnings=[f"tripinfo file does not exist: {path}"],
        )

    try:
        root = ET.parse(path).getroot()
    except ET.ParseError as exc:
        return TripinfoMetrics(
            path=str(path),
            exists=True,
            valid_xml=False,
            trip_count=0,
            mean_duration=None,
            mean_waiting_time=None,
            mean_time_loss=None,
            warnings=[f"invalid tripinfo XML: {exc}"],
        )
    except OSError as exc:
        return TripinfoMetrics(
            path=str(path),
            exists=True,
            valid_xml=False,
            trip_count=0,
            mean_duration=None,
            mean_waiting_time=None,
            mean_time_loss=None,
            warnings=[f"could not read tripinfo XML: {exc}"],
        )

    trips = [element for element in root.iter() if _local_name(element.tag) == "tripinfo"]
    durations = [_float_attr(trip, "duration") for trip in trips]
    waits = [_float_attr(trip, "waitingTime") for trip in trips]
    losses = [_float_attr(trip, "timeLoss") for trip in trips]
    return TripinfoMetrics(
        path=str(path),
        exists=True,
        valid_xml=True,
        trip_count=len(trips),
        mean_duration=_mean_present(durations),
        mean_waiting_time=_mean_present(waits),
        mean_time_loss=_mean_present(losses),
        warnings=[] if trips else ["tripinfo has no tripinfo elements"],
    )


def _paired_warnings(
    baseline: RunOutputInspection,
    variant: RunOutputInspection,
) -> list[str]:
    warnings: list[str] = []
    b_summary = baseline.summary
    v_summary = variant.summary
    if b_summary is None or v_summary is None:
        return warnings
    if b_summary.completion_ratio != v_summary.completion_ratio:
        warnings.append(
            "completion differs between baseline and variant; interpret arrived-only metrics cautiously"
        )
    if b_summary.loaded != v_summary.loaded:
        warnings.append("loaded vehicle counts differ between baseline and variant")
    return warnings


def _has_required_summary_evidence(
    baseline: RunOutputInspection,
    variant: RunOutputInspection,
) -> bool:
    return _has_completion_counts(baseline.summary) and _has_completion_counts(variant.summary)


def _has_completion_counts(summary: SummaryMetrics | None) -> bool:
    return (
        summary is not None
        and summary.exists
        and summary.valid_xml
        and summary.loaded is not None
        and summary.arrived is not None
        and summary.completion_ratio is not None
    )


def _int_attr(element: ET.Element, name: str) -> int | None:
    value = element.attrib.get(name)
    if value is None:
        return None
    try:
        return int(float(value))
    except ValueError:
        return None


def _float_attr(element: ET.Element, name: str) -> float | None:
    value = element.attrib.get(name)
    if value is None:
        return None
    try:
        return float(value)
    except ValueError:
        return None


def _mean_present(values: list[float | None]) -> float | None:
    present = [value for value in values if value is not None]
    if not present:
        return None
    return sum(present) / len(present)


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]
