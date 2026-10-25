"""Reproduce frozen Hamburg collisions with native observation outputs."""

import gzip
import json
import math
from io import BytesIO
from pathlib import Path
import sys
import xml.etree.ElementTree as ET

from .artifact_io import write_json_atomic
from .candidate_contracts import file_sha256
from .command_runner import run_command
from .detector_demand import read_csv_rows


def read_trace_source(folder, name):
    path = Path(folder) / name
    if not path.exists():
        path = path.with_name(path.name + ".gz")
    data = path.read_bytes()
    return gzip.decompress(data) if path.suffix == ".gz" else data


def prepare_collision_trace(*, case_dir, output_dir, sumo_binary, end_s=None):
    case, out = Path(case_dir).resolve(strict=True), Path(output_dir).resolve()
    if out.exists() or case.is_relative_to(out):
        raise ValueError("Use a new collision-trace directory separate from its source.")
    source = case / "native"
    request = json.loads(read_trace_source(source, "native-request.json"))
    source_end = request["end_s"]
    end = source_end if end_s is None else float(end_s)
    step = float(request["command"][request["command"].index("--step-length") + 1])
    if not math.isfinite(end) or not 0 < end <= source_end or abs(round(end / step) * step - end) > 1e-6:
        raise ValueError("Use a positive native-step-aligned end within the source run.")
    events = [e for e in ET.fromstring(read_trace_source(source, "collisions.xml")).findall("collision") if float(e.get("time")) < end]
    identities = sorted({e.get(k) for e in events for k in ("collider", "victim")})
    if not identities or request["monitor_interval_s"] is not None:
        raise ValueError("Use a source collision before the trace end with per-step lane holding.")
    old = request["route_file"].replace("\\", "/").rsplit("/", 1)[0]

    def relocate(value):
        return value.replace("\\", "/").replace(old, out.as_posix())

    command = [relocate(value) for value in request["command"]]
    command[0] = str(Path(sumo_binary).resolve(strict=True))
    if end != source_end:
        command[command.index("--end") + 1] = f"{end:g}"
        if "--save-state.times" in command:
            command[command.index("--save-state.times") + 1] = f"{end - step:g}"
    command.extend(["--fcd-output", str(out / "collision-fcd.xml"),
                    "--device.fcd.probability", "0", "--device.fcd.explicit", ",".join(identities),
                    "--fcd-output.skip-empty", "true", "--lanechange-output", str(out / "lanechanges.xml")])
    out.mkdir(parents=True)
    (out / "states").mkdir()
    inputs = {}
    for name in ("network.net.xml", "demand.rou.xml", "signal-programs.add.xml", "e1.add.xml", "instant.add.xml", "source-e1.add.xml", "lane-intentions.json", "expected-counts.csv"):
        data = read_trace_source(source, name)
        if name in {"e1.add.xml", "instant.add.xml"}:
            data = relocate(data.decode("utf-8")).encode("utf-8")
            if name == "e1.add.xml" and end != source_end:
                detector_root = ET.fromstring(data)
                for detector in detector_root:
                    detector.set("end", f"{end:g}")
                data = ET.tostring(detector_root, encoding="utf-8", xml_declaration=True)
        (out / name).write_bytes(data)
        inputs[name] = file_sha256(out / name)
    request = {k: relocate(v) if isinstance(v, str) else v for k, v in request.items()}
    request["command"] = command
    request["end_s"] = end
    write_json_atomic(out / "native-request.json", request)
    report = {"status": "prepared", "formal_result": False, "source_case": str(case),
              "source_end_s": source_end, "trace_end_s": end,
              "observed_vehicles": identities, "input_sha256": inputs,
              "change": "Native FCD on collision participants and lane-change output only. No traffic input or controller change.",
              "claim_boundary": "Instrumented reproduction. Observation neutrality must be checked against native counts, collisions, completion and lane-hold events."}
    write_json_atomic(out / "trace-proposal.json", report)
    return report


def run_collision_trace(*, case_dir, prepared_dir):
    case, out = Path(case_dir).resolve(strict=True), Path(prepared_dir).resolve(strict=True)
    end = json.loads((out / "native-request.json").read_bytes())["end_s"]
    executed = run_command([sys.executable, "-m", "astra_sumo.core.simulation_stall", str(out / "native-request.json")],
                           cwd=out, timeout_seconds=2400 if end > 3600 else 900)
    write_json_atomic(out / "command.json", executed.to_dict())
    if executed.status != "pass":
        raise RuntimeError("Collision tracing did not finish. Retain command.json and native outputs.")
    report = compare_collision_trace(case_dir=case, prepared_dir=out)
    write_json_atomic(out / "trace-check.json", report)
    return report


def compare_collision_trace(*, case_dir, prepared_dir):
    """Compare saved observations without running SUMO or changing its records."""
    case, out = Path(case_dir).resolve(strict=True), Path(prepared_dir).resolve(strict=True)
    end = json.loads(read_trace_source(out, "native-request.json"))["end_s"]
    source = case / "native"
    before = json.loads(read_trace_source(source, "stall-monitor.json"))
    after = json.loads(read_trace_source(out, "stall-monitor.json"))
    collisions = ET.fromstring(read_trace_source(out, "collisions.xml")).findall("collision")
    original = [r for r in ET.fromstring(read_trace_source(source, "collisions.xml")).findall("collision") if float(r.get("time")) < end]
    def counts(data):
        return {(r.get("id"), float(r.get("begin")), float(r.get("end"))): r.attrib
                for r in ET.fromstring(data).findall("interval") if float(r.get("end")) <= end}
    source_counts = counts(read_trace_source(source, "field-counts.xml"))
    traced_counts = {k: v for k, v in counts(read_trace_source(out, "field-counts.xml")).items() if k in source_counts}
    old_summary = None
    for _, row in ET.iterparse(BytesIO(read_trace_source(source, "summary.xml"))):
        if row.tag == "step" and float(row.get("time")) < end:
            old_summary = dict(row.attrib)
        row.clear()
    if old_summary is None:
        raise ValueError("Retain a complete source summary step before the trace end.")
    new_summary = ET.fromstring(read_trace_source(out, "summary.xml")).findall("step")[-1]
    completion_names = ("time", "loaded", "inserted", "running", "waiting", "ended", "arrived", "collisions", "teleports")
    def events_through(hold):
        return [r for r in hold.get("events", []) if r["time_s"] <= end]
    source_hold, trace_hold = before["lane_section_hold"], after["lane_section_hold"]
    same_holds = (source_hold == trace_hold if end == before["simulation_time_s"] else
                  events_through(source_hold) == events_through(trace_hold) and
                  events_through(source_hold.get("junction_body_hold", {})) == events_through(trace_hold.get("junction_body_hold", {})))
    source_routes = {v.get("id"): ET.tostring(v).rstrip() for v in ET.fromstring(read_trace_source(source, "vehicle-routes.xml")).findall("vehicle") if float(v.get("arrival")) < end}
    trace_routes = {v.get("id"): ET.tostring(v).rstrip() for v in ET.fromstring(read_trace_source(out, "vehicle-routes.xml")).findall("vehicle")}
    checks = {
        "same_native_collisions": [r.attrib for r in collisions] == [r.attrib for r in original],
        "same_periodic_E1_intervals": traced_counts == source_counts,
        "same_completion": {k: old_summary.get(k) for k in completion_names} == {k: new_summary.get(k) for k in completion_names},
        "same_lane_hold_events": same_holds,
        "same_vehicle_route_exit_times": source_routes == trace_routes,
        "same_native_version": before["native_version"] == after["native_version"] == [22, "SUMO 1.27.1"],
        "completed": after["status"] == "completed" and after["simulation_time_s"] == end,
    }
    report = {"status": "measured", "formal_result": False, "checks": checks,
              "trace_end_s": end, "compared_E1_intervals": len(source_counts),
              "source_target_rows_total": len(read_csv_rows(out / "expected-counts.csv")),
              "source_target_rows_available": sum((r["detector_id"], float(r["begin"]), float(r["end"])) in source_counts for r in read_csv_rows(out / "expected-counts.csv")),
              "reproduction_matched": all(checks.values()), "collisions": len(collisions), "run_valid": False,
              "claim_boundary": "Matching reproduction verifies trace correspondence only. Collisions remain unsafe and their cause needs trajectory and connection analysis."}
    return report
