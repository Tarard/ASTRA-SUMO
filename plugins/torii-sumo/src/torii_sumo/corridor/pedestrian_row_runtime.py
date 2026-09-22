from __future__ import annotations

import re
from pathlib import Path
from typing import Literal
from xml.etree import ElementTree as ET

from torii_sumo.core.artifact_io import write_json_atomic, write_text_atomic
from torii_sumo.core.candidate_contracts import file_sha256
from torii_sumo.core.command_runner import run_command

from .enums import GateStatus
from .ids import stable_id
from .pedestrian_row_contracts import (
    ObservedYieldBehavior,
    ROWRuntimeProbe,
)


ArrivalSchedule = Literal[
    "pedestrian-first",
    "vehicle-first",
    "simultaneous",
]

_VERSION_RE = re.compile(r"\b(\d+\.\d+\.\d+)\b")
_EMERGENCY_BRAKING_RE = re.compile(r"emergency\s+braking", re.IGNORECASE)
_STOP_SPEED_MPS = 0.1
_MIN_YIELD_STOP_S = 0.3
_PEDESTRIAN_WAIT_WINDOW_S = 15.0
_SIMULATION_STEP_S = 0.1
_SIMULATION_END_S = 160.0
_RANDOM_SEED = 17


def run_row_runtime_probe(
    *,
    net_file: Path,
    route_file: Path,
    crossing_edge_id: str,
    vehicle_internal_lane_id: str,
    arrival_schedule: ArrivalSchedule,
    pedestrian_depart_s: float,
    vehicle_depart_s: float,
    vehicle_speed_mps: float,
    sumo_binary: Path,
    output_dir: Path,
    timeout_seconds: float = 60.0,
) -> ROWRuntimeProbe:
    """Run one deterministic two-subject behavioral probe.

    The result describes SUMO's execution of the candidate. It is deliberately
    not an oracle for real-world right-of-way.
    """

    net_path = net_file.resolve(strict=True)
    route_path = route_file.resolve(strict=True)
    binary = sumo_binary.resolve(strict=True)
    destination = output_dir.resolve()
    destination.mkdir(parents=True, exist_ok=True)
    _validate_route_schedule(
        route_path,
        pedestrian_depart_s=pedestrian_depart_s,
        vehicle_depart_s=vehicle_depart_s,
    )

    fcd_path = destination / "trace.fcd.xml"
    tripinfo_path = destination / "tripinfo.xml"
    collision_path = destination / "collisions.xml"
    command = [
        str(binary),
        "-n",
        str(net_path),
        "-r",
        str(route_path),
        "--begin",
        "0",
        "--end",
        f"{_SIMULATION_END_S:g}",
        "--step-length",
        f"{_SIMULATION_STEP_S:g}",
        "--seed",
        str(_RANDOM_SEED),
        "--fcd-output",
        str(fcd_path),
        "--fcd-output.attributes",
        "all",
        "--tripinfo-output",
        str(tripinfo_path),
        "--collision-output",
        str(collision_path),
        "--collision.action",
        "warn",
        "--collision.check-junctions",
        "true",
        "--time-to-teleport",
        "-1",
        "--no-step-log",
        "true",
        "--duration-log.disable",
        "true",
    ]
    command_result = run_command(
        command,
        cwd=destination,
        timeout_seconds=timeout_seconds,
    )
    write_json_atomic(
        destination / "sumo.command.json",
        command_result.to_dict(),
        sort_keys=True,
    )
    for path in (fcd_path, tripinfo_path, collision_path):
        if path.is_file():
            _canonicalize_xml_in_place(path)

    version_result = run_command([str(binary), "--version"])
    version_text = f"{version_result.stdout}\n{version_result.stderr}"
    match = _VERSION_RE.search(version_text)
    sumo_version = match.group(1) if match else "unknown"
    probe = build_row_runtime_probe_from_outputs(
        net_file=net_path,
        route_file=route_path,
        fcd_file=fcd_path if fcd_path.is_file() else None,
        tripinfo_file=(tripinfo_path if tripinfo_path.is_file() else None),
        collision_file=(collision_path if collision_path.is_file() else None),
        crossing_edge_id=crossing_edge_id,
        vehicle_internal_lane_id=vehicle_internal_lane_id,
        arrival_schedule=arrival_schedule,
        pedestrian_depart_s=pedestrian_depart_s,
        vehicle_depart_s=vehicle_depart_s,
        vehicle_speed_mps=vehicle_speed_mps,
        sumo_binary_sha256=file_sha256(binary),
        sumo_version=sumo_version,
        command_status=command_result.status,
        command_stderr=command_result.stderr,
    )
    write_json_atomic(
        destination / "runtime-probe.json",
        probe.model_dump(mode="json", by_alias=True),
        sort_keys=True,
    )
    return probe


def build_row_runtime_probe_from_outputs(
    *,
    net_file: Path,
    route_file: Path,
    fcd_file: Path | None,
    tripinfo_file: Path | None,
    collision_file: Path | None,
    crossing_edge_id: str,
    vehicle_internal_lane_id: str,
    arrival_schedule: ArrivalSchedule,
    pedestrian_depart_s: float,
    vehicle_depart_s: float,
    vehicle_speed_mps: float,
    sumo_binary_sha256: str,
    sumo_version: str,
    command_status: str,
    command_stderr: str,
) -> ROWRuntimeProbe:
    net_path = net_file.resolve(strict=True)
    route_path = route_file.resolve(strict=True)
    fcd_path = fcd_file.resolve(strict=True) if fcd_file is not None else None
    tripinfo_path = (
        tripinfo_file.resolve(strict=True)
        if tripinfo_file is not None
        else None
    )
    collision_path = (
        collision_file.resolve(strict=True)
        if collision_file is not None
        else None
    )

    pedestrian_samples: list[tuple[float, str, float]] = []
    vehicle_samples: list[tuple[float, str, float]] = []
    time_values: list[float] = []
    if fcd_path is not None:
        root = ET.parse(fcd_path).getroot()
        for timestep in root.findall("timestep"):
            time = _number(timestep.attrib.get("time"))
            if time is None:
                continue
            time_values.append(time)
            person = next(
                (
                    item
                    for item in timestep.findall("person")
                    if item.attrib.get("id") == "ped"
                ),
                None,
            )
            if person is not None:
                pedestrian_samples.append(
                    (
                        time,
                        person.attrib.get("edge", ""),
                        _number(person.attrib.get("speed")) or 0.0,
                    )
                )
            vehicle = next(
                (
                    item
                    for item in timestep.findall("vehicle")
                    if item.attrib.get("id") == "veh"
                ),
                None,
            )
            if vehicle is not None:
                vehicle_samples.append(
                    (
                        time,
                        vehicle.attrib.get("lane", ""),
                        _number(vehicle.attrib.get("speed")) or 0.0,
                    )
                )
    step = _simulation_step(time_values)
    pedestrian_times = [
        time
        for time, edge, _speed in pedestrian_samples
        if edge == crossing_edge_id
    ]
    vehicle_times = [
        time
        for time, lane, _speed in vehicle_samples
        if lane.startswith(":")
    ]
    pedestrian_entry, pedestrian_exit = _entry_exit(pedestrian_times, step)
    vehicle_entry, vehicle_exit = _entry_exit(vehicle_times, step)
    pedestrian_stopped = _pedestrian_stop_duration(
        pedestrian_samples,
        crossing_edge_id=crossing_edge_id,
        crossing_entry_s=pedestrian_entry,
        step_s=step,
    )
    vehicle_stopped = _vehicle_stop_duration(
        vehicle_samples,
        internal_exit_s=vehicle_exit,
        vehicle_depart_s=vehicle_depart_s,
        step_s=step,
    )
    collision_count = _collision_count(collision_path)
    emergency_count = len(_EMERGENCY_BRAKING_RE.findall(command_stderr))
    completed = _trip_completed(tripinfo_path)
    observed = _observed_behavior(
        completed=completed,
        collision_count=collision_count,
        pedestrian_entry_s=pedestrian_entry,
        vehicle_entry_s=vehicle_entry,
        pedestrian_stopped_s=pedestrian_stopped,
        vehicle_stopped_s=vehicle_stopped,
    )
    runtime_status = (
        GateStatus.PASS
        if command_status == "pass"
        and completed
        and collision_count == 0
        and emergency_count == 0
        and observed not in {"unsafe-overlap", "unresolved"}
        else GateStatus.BLOCKED
    )
    payload = {
        "candidate_net_sha256": file_sha256(net_path),
        "route_sha256": file_sha256(route_path),
        "sumo_binary_sha256": sumo_binary_sha256,
        "sumo_version": sumo_version,
        "crossing_edge_id": crossing_edge_id,
        "vehicle_internal_lane_id": vehicle_internal_lane_id,
        "arrival_schedule": arrival_schedule,
        "pedestrian_depart_s": round(pedestrian_depart_s, 3),
        "vehicle_depart_s": round(vehicle_depart_s, 3),
        "vehicle_speed_mps": round(vehicle_speed_mps, 3),
        "simulation_step_s": _SIMULATION_STEP_S,
        "simulation_end_s": _SIMULATION_END_S,
        "random_seed": _RANDOM_SEED,
        "stop_speed_threshold_mps": _STOP_SPEED_MPS,
        "yield_stop_threshold_s": _MIN_YIELD_STOP_S,
        "pedestrian_wait_window_s": _PEDESTRIAN_WAIT_WINDOW_S,
        "pedestrian_crossing_entry_s": _rounded(pedestrian_entry),
        "pedestrian_crossing_exit_s": _rounded(pedestrian_exit),
        "vehicle_internal_entry_s": _rounded(vehicle_entry),
        "vehicle_internal_exit_s": _rounded(vehicle_exit),
        "vehicle_stopped_before_conflict_s": round(vehicle_stopped, 3),
        "pedestrian_stopped_before_crossing_s": round(
            pedestrian_stopped,
            3,
        ),
        "observed_behavior": observed,
        "collision_count": collision_count,
        "emergency_braking_count": emergency_count,
        "completed": completed,
        "runtime_status": runtime_status.value,
        "proves_real_world_priority": False,
    }
    draft_payload = dict(payload)
    draft_payload["runtime_status"] = runtime_status
    draft = ROWRuntimeProbe.model_construct(
        runtime_probe_id="evidence_000000000000000000000000",
        **draft_payload,
    )
    return ROWRuntimeProbe(
        runtime_probe_id=stable_id("evidence", draft.identity_payload()),
        **payload,
    )


def _validate_route_schedule(
    route_file: Path,
    *,
    pedestrian_depart_s: float,
    vehicle_depart_s: float,
) -> None:
    root = ET.parse(route_file).getroot()
    person = root.find("person[@id='ped']")
    vehicle = root.find("vehicle[@id='veh']")
    if person is None or vehicle is None:
        raise ValueError("ROW runtime route requires ped and veh subjects.")
    observed_pedestrian = _number(person.attrib.get("depart"))
    observed_vehicle = _number(vehicle.attrib.get("depart"))
    if observed_pedestrian != pedestrian_depart_s:
        raise ValueError("ROW pedestrian departure does not match the probe.")
    if observed_vehicle != vehicle_depart_s:
        raise ValueError("ROW vehicle departure does not match the probe.")


def _canonicalize_xml_in_place(path: Path) -> None:
    canonical = ET.canonicalize(
        from_file=str(path.resolve(strict=True)),
        with_comments=False,
        strip_text=True,
    )
    write_text_atomic(path, canonical)


def _simulation_step(times: list[float]) -> float:
    differences = sorted(
        {
            round(second - first, 6)
            for first, second in zip(times, times[1:])
            if second > first
        }
    )
    return differences[0] if differences else 0.1


def _entry_exit(
    times: list[float],
    step_s: float,
) -> tuple[float | None, float | None]:
    if not times:
        return None, None
    return min(times), max(times) + step_s


def _pedestrian_stop_duration(
    samples: list[tuple[float, str, float]],
    *,
    crossing_edge_id: str,
    crossing_entry_s: float | None,
    step_s: float,
) -> float:
    if crossing_entry_s is None:
        return 0.0
    window_start = crossing_entry_s - _PEDESTRIAN_WAIT_WINDOW_S
    return sum(
        step_s
        for time, edge, speed in samples
        if window_start <= time < crossing_entry_s
        and edge.startswith(":")
        and edge != crossing_edge_id
        and speed <= _STOP_SPEED_MPS
    )


def _vehicle_stop_duration(
    samples: list[tuple[float, str, float]],
    *,
    internal_exit_s: float | None,
    vehicle_depart_s: float,
    step_s: float,
) -> float:
    if internal_exit_s is None:
        return 0.0
    return sum(
        step_s
        for time, _lane, speed in samples
        if vehicle_depart_s < time < internal_exit_s
        and speed <= _STOP_SPEED_MPS
    )


def _collision_count(path: Path | None) -> int:
    if path is None:
        return 0
    return len(ET.parse(path).getroot().findall(".//collision"))


def _trip_completed(path: Path | None) -> bool:
    if path is None:
        return False
    root = ET.parse(path).getroot()
    return (
        root.find("tripinfo[@id='veh']") is not None
        and root.find("personinfo[@id='ped']") is not None
    )


def _observed_behavior(
    *,
    completed: bool,
    collision_count: int,
    pedestrian_entry_s: float | None,
    vehicle_entry_s: float | None,
    pedestrian_stopped_s: float,
    vehicle_stopped_s: float,
) -> ObservedYieldBehavior:
    if collision_count:
        return "unsafe-overlap"
    if not completed or pedestrian_entry_s is None or vehicle_entry_s is None:
        return "unresolved"
    vehicle_yielded = vehicle_stopped_s >= _MIN_YIELD_STOP_S
    pedestrian_yielded = pedestrian_stopped_s >= _MIN_YIELD_STOP_S
    if vehicle_yielded and not pedestrian_yielded:
        return "vehicle-yielded"
    if pedestrian_yielded and not vehicle_yielded:
        return "pedestrian-yielded"
    if vehicle_yielded and pedestrian_yielded:
        return "unresolved"
    return "no-interaction"


def _number(value: str | None) -> float | None:
    try:
        return float(str(value))
    except (TypeError, ValueError):
        return None


def _rounded(value: float | None) -> float | None:
    return round(value, 3) if value is not None else None
