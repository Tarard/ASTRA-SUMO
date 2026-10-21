"""Run explicit demand with fixed network control and strict point-detector comparisons."""

from pathlib import Path
import json
import math
import sys
import xml.etree.ElementTree as ET

from .artifact_io import copy_file_atomic, write_json_atomic
from .candidate_contracts import file_sha256
from .command_runner import run_command
from .detector_demand import audit_expected_to_e1_strict, compare_expected_to_e1_strict, read_csv_rows, summarize_comparison, write_csv


def recover_point_detector_measurements(*, detector_xml: Path, expected_counts_csv: Path, output_dir: Path) -> dict:
    """Retain completed, strictly validated E1 records as diagnostics after a failed run."""
    sources = [Path(p).resolve(strict=True) for p in (detector_xml, expected_counts_csv)]
    output = Path(output_dir).resolve()
    if output.exists() or any(path.is_relative_to(output) for path in sources):
        raise ValueError("Use a new diagnostic directory separate from its inputs.")
    expected = read_csv_rows(sources[1])
    comparisons = compare_expected_to_e1_strict(expected, {}, count_attribute="nVehContrib")
    if not comparisons:
        raise ValueError("Provide at least one expected detector interval.")
    inputs = [{"path": str(path), "sha256": file_sha256(path)} for path in sources]
    output.mkdir(parents=True)
    comparison_file = output / "field-comparison.csv"
    write_csv(comparison_file, comparisons, list(comparisons[0]))
    report = {"schema": "astra.partial-point-detector-measurements/v1", "status": "invalid",
              "formal_result": False, "native_completion_established": False,
              "inputs": inputs, "errors": [], "source_parse_error": None, "completed_records": 0,
              "expected_field_bins": len(comparisons), "recovered_field_bins": 0,
              "missing_field_bins": len(comparisons), "exact_field_bins": 0,
              "field_metrics": None, "exact_count_fit": False, "field_comparison": str(comparison_file)}
    root = ET.Element("detector")
    try:
        try:
            for _, element in ET.iterparse(sources[0], events=("end",)):
                if element.tag == "interval":
                    root.append(ET.Element("interval", dict(element.attrib)))
                element.clear()
        except ET.ParseError as error:
            report["source_parse_error"] = str(error)
        recovered = output / "completed-e1-records.xml"
        ET.ElementTree(root).write(recovered, encoding="utf-8", xml_declaration=True)
        report["completed_records"] = len(root)
        report["completed_records_file"] = str(recovered)
        # Recovery does not relax count, identifier, or duplicate-interval validation.
        comparisons = audit_expected_to_e1_strict(expected, recovered, count_attribute="nVehContrib")
        present = sum(row["measured_nVehContrib"] is not None for row in comparisons)
        write_csv(comparison_file, comparisons, list(comparisons[0]))
        report.update(status="measured" if present else "no_effect", recovered_field_bins=present,
                      missing_field_bins=len(comparisons)-present,
                      exact_field_bins=sum(row["diff_nVehContrib_minus_expected"] == 0 for row in comparisons))
    except (OSError, ValueError, KeyError) as error:
        report["errors"].append(str(error))
    report["inputs_unchanged"] = all(file_sha256(Path(row["path"])) == row["sha256"] for row in inputs)
    report["claim_boundary"] = "Completed E1 records only. The native run remains unverified. No accepted fit metric or exact calibration result is supplied."
    report["report_file"] = str(output / "report.json")
    write_json_atomic(output / "report.json", report)
    return report


def run_point_detector_demand(*, net_file: Path, route_file: Path, e1_additional_file: Path,
                              expected_counts_csv: Path, output_dir: Path, end_s: float,
                              sumo_binary: str = "sumo", step_length: float = .1,
                              seed: int = 104, timeout_seconds: float = 240,
                              state_times_s: tuple[float, ...] = (),
                              all_stopped_timeout_s: float = 300) -> dict:
    """Measure every declared field bin without requiring historical signal events.

    The network supplies control. Instantaneous identities are simulator
    instrumentation for offline proposals, not information from field sensors.
    """
    if (not all(math.isfinite(value) and value > 0 for value in (end_s, step_length, timeout_seconds, all_stopped_timeout_s))
            or not isinstance(seed, int) or isinstance(seed, bool) or seed < 0):
        raise ValueError("Use positive finite simulation limits and a nonnegative integer seed.")
    if (len(set(state_times_s)) != len(state_times_s)
            or any(not math.isfinite(value) or value < 0 or value >= end_s for value in state_times_s)):
        raise ValueError("State times must be unique, finite, and inside the simulation period.")
    paths = [Path(path).resolve(strict=True) for path in (net_file, route_file, e1_additional_file, expected_counts_csv)]
    output_dir = Path(output_dir).resolve()
    if output_dir.exists() or any(path.is_relative_to(output_dir) for path in paths):
        raise ValueError("Use a new replay directory separate from its inputs.")
    routes = ET.parse(paths[1]).getroot()
    vehicles = routes.findall("vehicle")
    identities = {row.get("id") for row in vehicles}
    if (routes.tag != "routes" or not vehicles or None in identities or "" in identities
            or len(identities) != len(vehicles) or any(row.tag in {"flow", "trip", "person", "personFlow"} for row in routes)):
        raise ValueError("Provide a frozen explicit-vehicle demand without other traffic generators.")
    expected = read_csv_rows(paths[3])
    if not expected or any(not math.isfinite(float(row["begin"])) or not math.isfinite(float(row["end"]))
                           or float(row["begin"]) < 0 or float(row["end"]) > end_s for row in expected):
        raise ValueError("Expected detector intervals must lie inside the simulation period.")
    missing = compare_expected_to_e1_strict(expected, {}, count_attribute="nVehContrib")
    e1 = ET.parse(paths[2]).getroot()
    fields = e1.findall("inductionLoop")
    field_ids = {row.get("id") for row in fields}
    if (e1.tag != "additional" or not fields or len(fields) != len(e1) or None in field_ids or "" in field_ids
            or len(field_ids) != len(fields) or any(float(row.get("length", "0")) != 0 for row in fields)):
        raise ValueError("Provide unique point E1 detectors without other simulation objects.")
    inputs = [{"path": str(path), "sha256": file_sha256(path)} for path in paths]
    output_dir.mkdir(parents=True)
    snapshots = []
    for path, name in zip(paths, ("network.net.xml", "demand.rou.xml", "source-e1.add.xml", "expected-counts.csv")):
        target = output_dir / name
        copy_file_atomic(path, target)
        snapshots.append({"path": str(target), "sha256": file_sha256(target)})
    instant, mapping = ET.Element("additional"), {}
    for index, loop in enumerate(fields):
        loop.set("file", str(output_dir / "field-counts.xml"))
        loop.set("begin", "0")
        loop.set("end", f"{end_s:g}")
        trace_id = f"astra_trace_{index}"
        while trace_id in field_ids or trace_id in mapping:
            trace_id += "_"
        mapping[trace_id] = loop.get("id")
        attrs = {key: loop.get(key) for key in ("lane", "pos", "vTypes", "nextEdges", "detectPersons", "friendlyPos")
                 if loop.get(key) is not None}
        ET.SubElement(instant, "instantInductionLoop", id=trace_id, file=str(output_dir / "instant-e1.xml"), **attrs)
    ET.ElementTree(e1).write(output_dir / "e1.add.xml", encoding="utf-8", xml_declaration=True)
    ET.ElementTree(instant).write(output_dir / "instant.add.xml", encoding="utf-8", xml_declaration=True)
    command = [str(sumo_binary), "--net-file", str(output_dir / "network.net.xml"), "--route-files", str(output_dir / "demand.rou.xml"),
        "--additional-files", f"{output_dir / 'e1.add.xml'},{output_dir / 'instant.add.xml'}", "--end", f"{end_s:g}",
        "--step-length", f"{step_length:g}", "--seed", str(seed), "--precision", "12", "--time-to-teleport", "-1",
        "--collision.check-junctions", "true", "--summary-output", str(output_dir / "summary.xml"),
        "--tripinfo-output", str(output_dir / "tripinfo.xml"), "--tripinfo-output.write-unfinished", "true",
        "--vehroute-output", str(output_dir / "vehicle-routes.xml"), "--vehroute-output.exit-times", "true",
        "--collision-output", str(output_dir / "collisions.xml"), "--log", str(output_dir / "sumo.log"),
        "--error-log", str(output_dir / "sumo-errors.log"), "--duration-log.statistics", "true", "--no-step-log", "true"]
    if state_times_s:
        (output_dir / "states").mkdir()
        command.extend(["--save-state.times", ",".join(f"{t:g}" for t in sorted(state_times_s)),
            "--save-state.prefix", str(output_dir / "states/state"), "--save-state.suffix", ".xml.gz",
            "--save-state.rng", "true", "--save-state.precision", "12"])
    report = {"schema": "astra.point-detector-demand-replay/v1", "status": "running", "inputs": inputs,
        "snapshots": snapshots, "seed": seed, "step_length_s": step_length, "end_s": end_s,
        "vehicle_count": len(vehicles), "measurement_attribute": "nVehContrib", "instant_to_periodic_ids": mapping,
        "signal_treatment": "network_programs_unchanged", "formal_result": False, "errors": [],
        "expected_field_bins": len(expected), "observer_complete": False, "missing_field_bins": len(expected),
        "exact_field_bins": 0, "exact_count_fit": False, "field_metrics": None,
        "field_comparison": str(output_dir / "field-comparison.csv")}
    report.update(state_times_s=sorted(state_times_s), state_snapshots=[])
    write_csv(output_dir / "field-comparison.csv", missing, list(missing[0]))
    write_json_atomic(output_dir / "run.json", report)
    monitor_file = output_dir / "stall-monitor.json"
    request_file = output_dir / "native-request.json"
    write_json_atomic(request_file, {"command": command, "end_s": end_s,
        "all_stopped_timeout_s": all_stopped_timeout_s, "monitor_file": str(monitor_file)})
    executed = run_command([sys.executable, "-m", "astra_sumo.core.simulation_stall", str(request_file)],
        cwd=output_dir, timeout_seconds=timeout_seconds)
    write_json_atomic(output_dir / "command.json", executed.to_dict())
    report["status"] = "invalid"
    report["native_status"] = executed.status
    report["native_returncode"] = executed.returncode
    report["state_snapshots"] = [{"path": str(path), "sha256": file_sha256(path)}
                                 for path in sorted((output_dir / "states").glob("*.xml.gz"))]
    if executed.status != "pass":
        report["errors"].append(f"Native execution ended with status {executed.status}. Inspect command.json.")
    report["stall_monitor"] = json.loads(monitor_file.read_text(encoding="utf-8")) if monitor_file.is_file() else None
    if report["stall_monitor"] and report["stall_monitor"]["status"] == "all_stopped":
        report["errors"].append("All running vehicles exceeded the standstill limit. Inspect stall-monitor.json.")
    try:
        final = ET.parse(output_dir / "summary.xml").getroot()[-1]
        completion = {key: int(final.get(key, "0")) for key in
                      ("loaded", "inserted", "arrived", "running", "waiting", "teleports", "collisions", "discarded")}
        trips = ET.parse(output_dir / "tripinfo.xml").getroot().findall("tripinfo")
        healthy = (executed.status == "pass" and report["stall_monitor"] is not None
            and report["stall_monitor"]["status"] == "completed"
            and all(completion[key] == len(vehicles) for key in ("loaded", "inserted", "arrived"))
            and not any(completion[key] for key in ("running", "waiting", "teleports", "collisions", "discarded"))
            and len(trips) == len(vehicles) and {row.get("id") for row in trips} == identities
            and all(row.get("vaporized", "") == "" for row in trips))
        comparisons = audit_expected_to_e1_strict(expected, output_dir / "field-counts.xml", count_attribute="nVehContrib")
        write_csv(output_dir / "field-comparison.csv", comparisons, list(comparisons[0]))
        complete = all(row["measured_nVehContrib"] is not None for row in comparisons)
        # The legacy summary's column label is adapted explicitly. Counts still come from nVehContrib.
        metrics = summarize_comparison([{"expected_total": row["expected_total"], "measured_nVehEntered": row["measured_nVehContrib"]}
                                       for row in comparisons]) if healthy and complete else None
        if metrics is not None:
            metrics["field_bins"] = metrics.pop("edge_rows")
        report.update(status="measured" if healthy and complete else "invalid", completion=completion, healthy=healthy,
            observer_complete=complete, expected_field_bins=len(comparisons),
            missing_field_bins=sum(row["measured_nVehContrib"] is None for row in comparisons),
            exact_field_bins=sum(row["diff_nVehContrib_minus_expected"] == 0 for row in comparisons),
            field_metrics=metrics, exact_count_fit=healthy and complete and all(row["diff_nVehContrib_minus_expected"] == 0 for row in comparisons),
            field_comparison=str(output_dir / "field-comparison.csv"))
    except (OSError, ValueError, KeyError, IndexError, ET.ParseError) as error:
        report["errors"].append(str(error))
        try:
            partial = recover_point_detector_measurements(detector_xml=output_dir / "field-counts.xml",
                expected_counts_csv=output_dir / "expected-counts.csv", output_dir=output_dir / "partial-measurements")
            report["partial_measurements"] = partial
            if partial["status"] in {"measured", "no_effect"}:
                copy_file_atomic(Path(partial["field_comparison"]), output_dir / "field-comparison.csv")
                report.update(missing_field_bins=partial["missing_field_bins"], exact_field_bins=partial["exact_field_bins"],
                    observer_complete=partial["missing_field_bins"] == 0)
        except (OSError, ValueError) as recovery_error:
            report["errors"].append(f"Detector recovery: {recovery_error}")
    report["inputs_unchanged"] = all(file_sha256(Path(row["path"])) == row["sha256"] for row in inputs)
    report["claim_boundary"] = "Native completion and all declared detector bins under the supplied model. No field timing, surveyed detector truth, or prediction accuracy is established."
    write_json_atomic(output_dir / "run.json", report)
    return report
