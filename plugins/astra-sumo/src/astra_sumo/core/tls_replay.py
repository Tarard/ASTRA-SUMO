from __future__ import annotations

import csv
import json
import math
import xml.etree.ElementTree as ET
from collections import defaultdict
from pathlib import Path
from typing import Any, Mapping, Sequence

import traci

from .detector_demand import (
    audit_expected_to_e1_strict,
    lane_allows_passenger,
    read_csv_rows,
    summarize_comparison,
    write_csv,
)
from ..evidence.output_inspection import inspect_summary, inspect_tripinfo
from .hamburg_official import sha256_file


def write_replay_sumocfg(
    path: Path,
    *,
    net_file: Path,
    route_file: Path,
    additional_files: Sequence[Path],
    begin: float,
    end: float,
    summary_file: Path,
    tripinfo_file: Path,
) -> None:
    root = ET.Element("configuration")
    input_element = ET.SubElement(root, "input")
    ET.SubElement(input_element, "net-file", value=str(net_file.resolve()))
    ET.SubElement(input_element, "route-files", value=str(route_file.resolve()))
    ET.SubElement(
        input_element,
        "additional-files",
        value=",".join(str(item.resolve()) for item in additional_files),
    )
    time_element = ET.SubElement(root, "time")
    ET.SubElement(time_element, "begin", value=f"{begin:g}")
    ET.SubElement(time_element, "end", value=f"{end:g}")
    output_element = ET.SubElement(root, "output")
    ET.SubElement(output_element, "summary-output", value=str(summary_file.resolve()))
    ET.SubElement(output_element, "tripinfo-output", value=str(tripinfo_file.resolve()))
    ET.SubElement(output_element, "tripinfo-output.write-unfinished", value="true")
    path.parent.mkdir(parents=True, exist_ok=True)
    tree = ET.ElementTree(root)
    ET.indent(tree, space="    ")
    tree.write(path, encoding="utf-8", xml_declaration=True)


def run_tls_detector_replay(
    *,
    net_file: Path,
    route_file: Path,
    e1_additional_file: Path,
    e2_additional_file: Path,
    tls_events_csv: Path,
    expected_counts_csv: Path,
    output_dir: Path,
    prefix: str = "digital_twin_replay",
    replay_end: float = 7200.0,
    completion_end: float = 10800.0,
    step_length: float = 1.0,
    sumo_binary: str = "sumo",
    traci_api: Any = traci,
    comparison_begin: float = 0.0,
    comparison_end: float | None = None,
) -> dict[str, Any]:
    inputs = [
        net_file,
        route_file,
        e1_additional_file,
        e2_additional_file,
        tls_events_csv,
        expected_counts_csv,
    ]
    missing = [str(path) for path in inputs if not path.is_file()]
    if missing:
        raise ValueError(f"replay inputs do not exist: {missing}")
    comparison_begin, comparison_end = _resolve_comparison_window(
        comparison_begin=comparison_begin,
        comparison_end=comparison_end,
        replay_end=replay_end,
        completion_end=completion_end,
    )
    if not math.isfinite(step_length) or step_length <= 0:
        raise ValueError("step_length must be finite and positive")
    expected_rows = read_csv_rows(expected_counts_csv)
    comparison_expected_rows = _select_expected_rows_for_comparison(
        expected_rows,
        comparison_begin=comparison_begin,
        comparison_end=comparison_end,
    )
    comparison_window = {
        "begin": comparison_begin,
        "end": comparison_end,
        "source_expected_row_count": len(expected_rows),
        "selected_expected_row_count": len(comparison_expected_rows),
    }

    events = _read_tls_events(tls_events_csv, step_length=step_length, replay_end=replay_end)
    coverage = _validate_passenger_link_coverage(net_file, events)
    if coverage["status"] != "pass":
        return {
            "status": "blocked",
            "claim_status": "construction-incomplete",
            "stage": "tls_passenger_link_coverage",
            "coverage": coverage,
            "comparison_window": comparison_window,
        }

    output_dir.mkdir(parents=True, exist_ok=True)
    summary_file = output_dir / f"{prefix}_summary.xml"
    tripinfo_file = output_dir / f"{prefix}_tripinfo.xml"
    e1_output = output_dir / f"{prefix}_e1_15min.xml"
    e2_output = output_dir / f"{prefix}_e2_15min.xml"
    prepared_e1 = output_dir / f"{prefix}_e1.add.xml"
    prepared_e2 = output_dir / f"{prefix}_e2.add.xml"
    _prepare_detector_additional(
        e1_additional_file,
        prepared_e1,
        output_file=e1_output,
        detector_tags={"inductionLoop"},
    )
    _prepare_detector_additional(
        e2_additional_file,
        prepared_e2,
        output_file=e2_output,
        detector_tags={"laneAreaDetector"},
    )
    config_file = output_dir / f"{prefix}.sumocfg"
    write_replay_sumocfg(
        config_file,
        net_file=net_file,
        route_file=route_file,
        additional_files=[prepared_e1, prepared_e2],
        begin=0.0,
        end=completion_end,
        summary_file=summary_file,
        tripinfo_file=tripinfo_file,
    )
    command = [
        sumo_binary,
        "-c",
        str(config_file),
        "--step-length",
        f"{step_length:g}",
        "--no-step-log",
        "true",
    ]
    replay_error = ""
    original_programs: dict[str, str] = {}
    restored_programs = False
    try:
        traci_api.start(command)
        tls_ids = sorted({row["sumo_tls_id"] for rows in events.values() for row in rows})
        for tls_id in tls_ids:
            original_programs[tls_id] = traci_api.trafficlight.getProgram(tls_id)
        current_states = {
            tls_id: ["r"] * len(traci_api.trafficlight.getRedYellowGreenState(tls_id)) for tls_id in tls_ids
        }
        _apply_event_rows(traci_api, current_states, events.get(0.0, ()))
        while traci_api.simulation.getTime() < completion_end:
            current_time = float(traci_api.simulation.getTime())
            if current_time < replay_end:
                _apply_event_rows(traci_api, current_states, events.get(current_time, ()))
            elif not restored_programs:
                for tls_id, program_id in original_programs.items():
                    traci_api.trafficlight.setProgram(tls_id, program_id)
                restored_programs = True
            traci_api.simulationStep()
    except Exception as exc:  # TraCI raises several binary/protocol-specific exception types.
        replay_error = f"{type(exc).__name__}: {exc}"
    finally:
        try:
            traci_api.close()
        except Exception:
            pass

    command_manifest = output_dir / f"{prefix}_command.json"
    command_manifest.write_text(
        json.dumps(
            {
                "command": command,
                "original_programs": original_programs,
                "restored_original_programs_after_replay_window": restored_programs,
                "replay_error": replay_error,
                "passenger_link_coverage": coverage,
                "comparison_window": comparison_window,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    if replay_error:
        return {
            "status": "fail",
            "claim_status": "construction-invalid",
            "error": replay_error,
            "config_file": str(config_file),
            "command_manifest": str(command_manifest),
            "comparison_window": comparison_window,
        }

    comparison_file = output_dir / f"{prefix}_real_vs_virtual_counts.csv"
    comparison_rows = audit_expected_to_e1_strict(
        comparison_expected_rows,
        e1_output,
        count_attribute="nVehContrib",
    )
    comparison_fields = [
        "detector_id",
        "edge_id",
        "begin",
        "end",
        "expected_total",
        "measurement_attribute",
        "measured_nVehContrib",
        "diff_nVehContrib_minus_expected",
        "measurement_status",
    ]
    # Keep test doubles and older integrations readable while using the
    # canonical strict-comparison field emitted by ``audit_expected_to_e1_strict``.
    if any("diff_contrib_minus_expected" in row for row in comparison_rows):
        comparison_fields.insert(-1, "diff_contrib_minus_expected")
    write_csv(
        comparison_file,
        comparison_rows,
        comparison_fields,
    )
    comparable_rows = [
        {
            "expected_total": row["expected_total"],
            "measured_nVehEntered": row["measured_nVehContrib"],
        }
        for row in comparison_rows
        if row["measurement_status"] == "matched"
    ]
    count_metrics = summarize_comparison(comparable_rows)
    missing_measurements = sum(row["measurement_status"] == "missing" for row in comparison_rows)
    summary_metrics = inspect_summary(summary_file).model_dump(mode="json")
    tripinfo_metrics = inspect_tripinfo(tripinfo_file).model_dump(mode="json")
    completed = _summary_is_complete(summary_metrics)
    status = "pass" if missing_measurements == 0 and completed else "partial"
    report = {
        "status": status,
        "claim_status": "validated-detector-replay" if status == "pass" else "validation-incomplete",
        "count_metrics": count_metrics,
        "missing_measurement_rows": missing_measurements,
        "summary": summary_metrics,
        "tripinfo": tripinfo_metrics,
        "passenger_link_coverage": coverage,
        "comparison_window": comparison_window,
        "artifacts": {
            "config_file": str(config_file),
            "command_manifest": str(command_manifest),
            "summary_file": str(summary_file),
            "tripinfo_file": str(tripinfo_file),
            "e1_output": str(e1_output),
            "e2_output": str(e2_output),
            "prepared_e1_additional": str(prepared_e1),
            "prepared_e2_additional": str(prepared_e2),
            "real_vs_virtual_counts": str(comparison_file),
        },
    }
    report_file = output_dir / f"{prefix}_validation.json"
    report_file.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    report["report_file"] = str(report_file)
    report["report_sha256"] = sha256_file(report_file)
    return report


def _summary_is_complete(summary_metrics: Mapping[str, Any]) -> bool:
    loaded = summary_metrics.get("loaded")
    return (
        summary_metrics.get("valid_xml") is True
        and loaded is not None
        and loaded == summary_metrics.get("inserted") == summary_metrics.get("arrived")
        and all(
            summary_metrics.get(field) == 0
            for field in ("running", "waiting", "teleports", "collisions", "discarded")
        )
    )


def _resolve_comparison_window(
    *,
    comparison_begin: float,
    comparison_end: float | None,
    replay_end: float,
    completion_end: float,
) -> tuple[float, float]:
    begin = float(comparison_begin)
    end = float(replay_end if comparison_end is None else comparison_end)
    replay = float(replay_end)
    completion = float(completion_end)
    if not all(math.isfinite(value) for value in (begin, end, replay, completion)) or not (
        0 <= begin < end <= replay <= completion
    ):
        raise ValueError(
            "replay timing must satisfy 0 <= comparison_begin < comparison_end <= "
            "replay_end <= completion_end with finite values"
        )
    return begin, end


def _select_expected_rows_for_comparison(
    expected_rows: Sequence[dict[str, str]],
    *,
    comparison_begin: float,
    comparison_end: float,
) -> list[dict[str, str]]:
    selected: list[dict[str, str]] = []
    for row_index, row in enumerate(expected_rows, start=1):
        try:
            interval_begin = float(row.get("begin", ""))
            interval_end = float(row.get("end", ""))
        except (TypeError, ValueError) as exc:
            raise ValueError(f"expected row {row_index} begin/end must be finite numbers") from exc
        if not math.isfinite(interval_begin) or not math.isfinite(interval_end):
            raise ValueError(f"expected row {row_index} begin/end must be finite numbers")
        if interval_end <= interval_begin:
            raise ValueError(f"expected row {row_index} end must be greater than begin")
        overlaps_window = interval_end > comparison_begin and interval_begin < comparison_end
        contained_in_window = (
            interval_begin >= comparison_begin and interval_end <= comparison_end
        )
        if overlaps_window and not contained_in_window:
            raise ValueError(
                f"expected row {row_index} interval [{interval_begin:g}, {interval_end:g}] "
                f"crosses comparison window boundary [{comparison_begin:g}, {comparison_end:g}]"
            )
        if contained_in_window:
            selected.append(row)
    if not selected:
        raise ValueError(
            f"no complete expected count bins fall within comparison window "
            f"[{comparison_begin:g}, {comparison_end:g}]"
        )
    return selected


def _prepare_detector_additional(
    source: Path,
    destination: Path,
    *,
    output_file: Path,
    detector_tags: set[str],
) -> None:
    """Copy detector definitions while pinning SUMO output to an absolute run path."""
    tree = ET.parse(source)
    detectors = [element for element in tree.getroot().iter() if element.tag in detector_tags]
    if not detectors:
        expected = ", ".join(sorted(detector_tags))
        raise ValueError(f"{source} contains no supported detector elements ({expected})")
    resolved_output = str(output_file.resolve())
    for detector in detectors:
        detector.set("file", resolved_output)
    destination.parent.mkdir(parents=True, exist_ok=True)
    ET.indent(tree, space="    ")
    tree.write(destination, encoding="utf-8", xml_declaration=True)


def _read_tls_events(
    path: Path,
    *,
    step_length: float,
    replay_end: float,
) -> dict[float, list[dict[str, Any]]]:
    by_time: dict[float, list[dict[str, Any]]] = defaultdict(list)
    initial_indices: set[tuple[str, int]] = set()
    event_states: dict[tuple[float, str, int], str] = {}
    with path.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            event_time = float(row["simulation_time"])
            rounded = round(event_time / step_length) * step_length
            if not math.isclose(event_time, rounded, abs_tol=1e-6):
                raise ValueError(
                    f"TLS event at {event_time:g}s is not representable with step_length={step_length:g}s"
                )
            if event_time < 0 or event_time >= replay_end:
                continue
            parsed = {
                "sumo_tls_id": row["sumo_tls_id"],
                "sumo_link_index": int(row["sumo_link_index"]),
                "sumo_state": row["sumo_state"],
                "source_state": row["source_state"],
            }
            if len(parsed["sumo_state"]) != 1:
                raise ValueError(f"invalid SUMO TLS state: {parsed['sumo_state']}")
            event_key = (float(rounded), parsed["sumo_tls_id"], parsed["sumo_link_index"])
            previous_state = event_states.get(event_key)
            if previous_state is not None and previous_state != parsed["sumo_state"]:
                raise ValueError(
                    "conflicting TLS events for "
                    f"{parsed['sumo_tls_id']}[{parsed['sumo_link_index']}] at {rounded:g}s"
                )
            if previous_state is None:
                event_states[event_key] = parsed["sumo_state"]
                by_time[float(rounded)].append(parsed)
            if math.isclose(rounded, 0.0, abs_tol=1e-9):
                initial_indices.add((parsed["sumo_tls_id"], parsed["sumo_link_index"]))
    if not by_time:
        raise ValueError("TLS event file has no replay-window events")
    by_time[0.0] = sorted(by_time.get(0.0, []), key=lambda row: (row["sumo_tls_id"], row["sumo_link_index"]))
    return dict(by_time)


def _validate_passenger_link_coverage(
    net_file: Path,
    events: Mapping[float, Sequence[Mapping[str, Any]]],
) -> dict[str, Any]:
    root = ET.parse(net_file).getroot()
    edges = {edge.attrib.get("id", ""): edge for edge in root.findall("edge")}
    passenger_indices: dict[str, set[int]] = defaultdict(set)
    for connection in root.findall("connection"):
        tls_id = connection.attrib.get("tl", "")
        link_index = connection.attrib.get("linkIndex")
        source_edge = edges.get(connection.attrib.get("from", ""))
        if not tls_id or link_index in (None, "") or source_edge is None:
            continue
        lane_index = connection.attrib.get("fromLane", "")
        lane = next((item for item in source_edge.findall("lane") if item.attrib.get("index") == lane_index), None)
        if lane is not None and lane_allows_passenger(lane):
            passenger_indices[tls_id].add(int(link_index))
    event_indices: dict[str, set[int]] = defaultdict(set)
    initial_indices: dict[str, set[int]] = defaultdict(set)
    for event_time, rows in events.items():
        for row in rows:
            tls_id = str(row["sumo_tls_id"])
            link_index = int(row["sumo_link_index"])
            event_indices[tls_id].add(link_index)
            if math.isclose(event_time, 0.0, abs_tol=1e-9):
                initial_indices[tls_id].add(link_index)
    missing_bindings: dict[str, list[int]] = {}
    missing_initial: dict[str, list[int]] = {}
    target_tls_ids = set(event_indices)
    for tls_id in sorted(target_tls_ids):
        required = passenger_indices.get(tls_id, set())
        bound = event_indices.get(tls_id, set())
        initialized = initial_indices.get(tls_id, set())
        if required - bound:
            missing_bindings[tls_id] = sorted(required - bound)
        if required - initialized:
            missing_initial[tls_id] = sorted(required - initialized)
    unknown_tls_ids = sorted(target_tls_ids - set(passenger_indices))
    status = "pass" if target_tls_ids and not unknown_tls_ids and not missing_bindings and not missing_initial else "fail"
    return {
        "status": status,
        "policy": (
            "all passenger-controlled links on each replay-targeted TLS must have an official binding and a "
            "time-zero state; non-target TLS keep their original SUMO programs"
        ),
        "target_tls_ids": sorted(target_tls_ids),
        "unknown_target_tls_ids": unknown_tls_ids,
        "non_target_tls_ids": sorted(set(passenger_indices) - target_tls_ids),
        "passenger_link_count": sum(len(passenger_indices.get(tls_id, ())) for tls_id in target_tls_ids),
        "bound_link_count": sum(len(value) for value in event_indices.values()),
        "initialized_link_count": sum(len(value) for value in initial_indices.values()),
        "missing_bindings": missing_bindings,
        "missing_initial_states": missing_initial,
    }


def _apply_event_rows(
    traci_api: Any,
    current_states: dict[str, list[str]],
    rows: Sequence[Mapping[str, Any]],
) -> None:
    changed: set[str] = set()
    for row in rows:
        tls_id = str(row["sumo_tls_id"])
        link_index = int(row["sumo_link_index"])
        state = str(row["sumo_state"])
        if tls_id not in current_states or not (0 <= link_index < len(current_states[tls_id])):
            raise ValueError(f"TLS event references invalid link {tls_id}[{link_index}]")
        current_states[tls_id][link_index] = state
        changed.add(tls_id)
    for tls_id in sorted(changed):
        traci_api.trafficlight.setRedYellowGreenState(tls_id, "".join(current_states[tls_id]))
