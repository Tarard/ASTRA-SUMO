"""Reproduce frozen Hamburg collisions with native observation outputs."""

import gzip
import json
from pathlib import Path
import sys
import xml.etree.ElementTree as ET

from .artifact_io import write_json_atomic
from .candidate_contracts import file_sha256
from .command_runner import run_command


def read_trace_source(folder, name):
    path = Path(folder) / name
    if not path.exists():
        path = path.with_name(path.name + ".gz")
    data = path.read_bytes()
    return gzip.decompress(data) if path.suffix == ".gz" else data


def prepare_collision_trace(*, case_dir, output_dir, sumo_binary):
    case, out = Path(case_dir).resolve(strict=True), Path(output_dir).resolve()
    if out.exists() or case.is_relative_to(out):
        raise ValueError("Use a new collision-trace directory separate from its source.")
    source = case / "native"
    request = json.loads(read_trace_source(source, "native-request.json"))
    events = ET.fromstring(read_trace_source(source, "collisions.xml")).findall("collision")
    identities = sorted({e.get(k) for e in events for k in ("collider", "victim")})
    if not identities or request["end_s"] != 3600 or request["monitor_interval_s"] is not None:
        raise ValueError("Use a frozen complete initialization collision case with per-step lane holding.")
    old = request["route_file"].replace("\\", "/").rsplit("/", 1)[0]

    def relocate(value):
        return value.replace("\\", "/").replace(old, out.as_posix())

    command = [relocate(value) for value in request["command"]]
    command[0] = str(Path(sumo_binary).resolve(strict=True))
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
        (out / name).write_bytes(data)
        inputs[name] = file_sha256(out / name)
    request = {k: relocate(v) if isinstance(v, str) else v for k, v in request.items()}
    request["command"] = command
    write_json_atomic(out / "native-request.json", request)
    report = {"status": "prepared", "formal_result": False, "source_case": str(case),
              "observed_vehicles": identities, "input_sha256": inputs,
              "change": "Native FCD on collision participants and lane-change output only. No traffic input or controller change.",
              "claim_boundary": "Instrumented reproduction. Observation neutrality must be checked against native counts, collisions, completion and lane-hold events."}
    write_json_atomic(out / "trace-proposal.json", report)
    return report


def run_collision_trace(*, case_dir, prepared_dir):
    case, out = Path(case_dir).resolve(strict=True), Path(prepared_dir).resolve(strict=True)
    executed = run_command([sys.executable, "-m", "astra_sumo.core.simulation_stall", str(out / "native-request.json")],
                           cwd=out, timeout_seconds=900)
    write_json_atomic(out / "command.json", executed.to_dict())
    if executed.status != "pass":
        raise RuntimeError("Collision tracing did not finish. Retain command.json and native outputs.")
    source = case / "native"
    before = json.loads(read_trace_source(source, "stall-monitor.json"))
    after = json.loads((out / "stall-monitor.json").read_bytes())
    collisions = ET.parse(out / "collisions.xml").getroot().findall("collision")
    original = ET.fromstring(read_trace_source(source, "collisions.xml")).findall("collision")
    def counts(data):
        return [r.attrib for r in ET.fromstring(data).findall("interval")]
    old_summary = ET.fromstring(read_trace_source(source, "summary.xml")).findall("step")[-1]
    new_summary = ET.parse(out / "summary.xml").getroot().findall("step")[-1]
    completion_names = ("time", "loaded", "inserted", "running", "waiting", "ended", "arrived", "collisions", "teleports")
    checks = {
        "same_native_collisions": [r.attrib for r in collisions] == [r.attrib for r in original],
        "same_periodic_E1_intervals": counts((out / "field-counts.xml").read_bytes()) == counts(read_trace_source(source, "field-counts.xml")),
        "same_completion": {k: old_summary.get(k) for k in completion_names} == {k: new_summary.get(k) for k in completion_names},
        "same_lane_hold_events": before["lane_section_hold"] == after["lane_section_hold"],
        "same_vehicle_route_exit_times": [ET.tostring(v) for v in ET.parse(out / "vehicle-routes.xml").getroot().findall("vehicle")] ==
            [ET.tostring(v) for v in ET.fromstring(read_trace_source(source, "vehicle-routes.xml")).findall("vehicle")],
        "same_native_version": before["native_version"] == after["native_version"] == [22, "SUMO 1.27.1"],
        "completed": after["status"] == "completed" and after["simulation_time_s"] == 3600,
    }
    report = {"status": "measured", "formal_result": False, "checks": checks,
              "reproduction_matched": all(checks.values()), "collisions": len(collisions), "run_valid": False,
              "claim_boundary": "Matching reproduction verifies trace correspondence only. Collisions remain unsafe and their cause needs trajectory and connection analysis."}
    write_json_atomic(out / "trace-check.json", report)
    return report
