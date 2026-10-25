"""Test a declared junction-body lane intention against the frozen demand."""

from collections import Counter
import json
from pathlib import Path
import sys
import xml.etree.ElementTree as ET

from .artifact_io import write_json_atomic
from .command_runner import run_command
from .detector_demand import audit_expected_to_e1_strict, read_csv_rows, write_csv
from .hamburg_collision_trace import prepare_collision_trace, read_trace_source
from .route_sensor_matrix import read_contributing_e1_passages


def probe_hamburg_junction_body_hold(*, case_dir, output_dir, sumo_binary):
    case, out = Path(case_dir).resolve(strict=True), Path(output_dir).resolve()
    prepare_collision_trace(case_dir=case, output_dir=out, sumo_binary=sumo_binary)
    request = json.loads((out / "native-request.json").read_bytes())
    request["junction_body_hold"] = True
    write_json_atomic(out / "native-request.json", request)
    write_json_atomic(out / "driver-proposal.json", {
        "formal_result": False, "source_case": str(case), "model_change": "junction_body_hold=true",
        "scope": "All native internal lanes, all model vehicles. Mode 512 from the first post-step internal sample through rear clearance. Detector and junction holds share one original mode.",
        "fixed": "All vehicles, routes, times, targets, network, permissions and signal inputs retained.",
        "sampling_limit": "The first native entry-step lane change is not prevented before its post-step observation.",
        "classification": "New exploratory driver assumption, not a literature-prescribed restriction, field law, natural behavior or geometry repair.",
        "evaluation": "Retain all 52 preceding-hour E1 residuals, collisions, teleports and insertion backlog. Inspect lane changes during active holds and the four original collision participants. No main-window claim."})
    executed = run_command([sys.executable, "-m", "astra_sumo.core.simulation_stall", str(out / "native-request.json")],
                           cwd=out, timeout_seconds=900)
    write_json_atomic(out / "command.json", executed.to_dict())
    if executed.status != "pass":
        raise RuntimeError("The junction-body probe did not finish. Retain native records and command.json.")
    monitor = json.loads((out / "stall-monitor.json").read_bytes())
    parent_run = json.loads(read_trace_source(case / "native", "run.json"))
    passage = read_contributing_e1_passages(instant_file=out / "instant-e1.xml", periodic_file=out / "field-counts.xml",
        detector_ids=parent_run["instant_to_periodic_ids"], step_length=.1)
    write_json_atomic(out / "passage-reconciliation.json", passage)
    rows = audit_expected_to_e1_strict(read_csv_rows(out / "expected-counts.csv"), out / "field-counts.xml")
    write_csv(out / "field-comparison.csv", rows, list(rows[0]))
    diffs = [r["diff_nVehContrib_minus_expected"] for r in rows]
    complete = all(v is not None for v in diffs)
    last = ET.parse(out / "summary.xml").getroot().findall("step")[-1]
    completion = {k: int(last.get(k)) for k in ("loaded", "inserted", "arrived", "running", "waiting", "collisions", "teleports", "discarded")}
    intentions = {r["vehicle_id"]: r for r in json.loads((out / "lane-intentions.json").read_bytes())["vehicles"]}
    repeated = Counter((r["vehicle_id"], r["detector_id"]) for r in passage["passages"])
    report = {"status": "measured" if complete and passage["reconciled"] else "incomplete", "formal_result": False, "main_window_run": False,
        "native_version": monitor["native_version"], "completion": completion,
        "native_monitor_status": monitor["status"], "native_end_s": monitor["simulation_time_s"],
        "native_E1_reconciled": passage["reconciled"], "available_initialization_bins": sum(v is not None for v in diffs),
        "exact_initialization_bins": sum(v == 0 for v in diffs),
        "diagnostic_MAE": sum(map(abs, diffs)) / len(diffs) if complete else None,
        "diagnostic_max_abs_error": max(map(abs, diffs)) if complete else None,
        "complete_field_passages": len(passage["passages"]),
        "other_complete_fields": sum(r["detector_id"] != intentions[r["vehicle_id"]]["field_id"] for r in passage["passages"]),
        "repeated_same_field_passages": sum(max(0, n-1) for n in repeated.values()),
        "native_safe_prefix": completion["collisions"] == completion["teleports"] == 0,
        "exact_calibration_established": False,
        "claim_boundary": "One exploratory driver perturbation in the initialization hour. No measured natural-behavior, source-code defect, geometry repair, main-window or exact-calibration claim."}
    write_json_atomic(out / "junction-body-check.json", report)
    return report
