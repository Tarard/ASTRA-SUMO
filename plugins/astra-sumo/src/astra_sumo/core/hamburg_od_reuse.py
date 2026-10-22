"""Reuse a completed initial OD replay after checking its native correspondence."""

import ast
from collections import defaultdict
from importlib.metadata import version
import json
import math
from pathlib import Path
import xml.etree.ElementTree as ET

from .candidate_contracts import file_sha256
from .detector_demand import audit_expected_to_e1_strict, read_csv_rows
from .hamburg_readme_results import STATIONS


_NATIVE_MODULES = (
    "artifact_io.py", "candidate_contracts.py", "command_runner.py", "detector_demand.py",
    "detector_demand_replay.py", "lane_section_hold.py", "simulation_stall.py",
)
_BODY_NODES = ["14398547", "205069138", "18653534"]


def _read(path):
    return json.loads(path.read_bytes())


def _function_ast(path, name):
    return ast.dump(next(node for node in ast.parse(path.read_text(encoding="utf-8")).body
                         if isinstance(node, ast.FunctionDef) and node.name == name))


def _native_call_ast(path):
    function = next(node for node in ast.parse(path.read_text(encoding="utf-8")).body
                    if isinstance(node, ast.FunctionDef) and node.name == "measure_hamburg_od")
    calls = [node for node in ast.walk(function) if isinstance(node, ast.Call)
             and isinstance(node.func, ast.Name) and node.func.id == "run_point_detector_demand"]
    if len(calls) != 1:
        raise ValueError("Use one unchanged native OD command when reusing its measurement.")
    return ast.dump(calls[0])


def reuse_hamburg_od_initial(*, prepared_dir, proposal_dir, cached_dir):
    """Return fresh E1 metrics and reuse provenance without changing cached evidence."""
    import sumo

    prepared, proposal, cached = (Path(p).resolve(strict=True) for p in
                                   (prepared_dir, proposal_dir, cached_dir))
    native = cached / "native"
    run = _read(native / "run.json")
    monitor = _read(native / "stall-monitor.json")
    if (run.get("native_status") != "pass" or not run.get("observer_complete")
            or run.get("missing_field_bins") != 0 or run.get("expected_field_bins") != 130
            or not run.get("inputs_unchanged") or monitor.get("status") != "completed"
            or _read(native / "command.json").get("status") != "pass"):
        raise ValueError("Reuse requires a completed native replay with all 130 E1 observations.")
    if (run.get("seed") != 104 or run.get("step_length_s") != .1 or run.get("end_s") != 14400
            or run.get("measurement_attribute") != "nVehContrib"
            or run.get("driver_treatment") != "local_observation_and_junction_body_hold"
            or run.get("signal_treatment") != "declared_additional_programs"
            or run.get("junction_body_hold_nodes") != _BODY_NODES or run.get("state_times_s") != []):
        raise ValueError("The cached native settings differ from the OD objective.")
    request = _read(native / "native-request.json")
    if (request.get("end_s") != 14400 or request.get("all_stopped_timeout_s") != 300
            or request.get("monitor_interval_s") is not None or request.get("junction_body_hold") is not True
            or request.get("junction_body_hold_nodes") != _BODY_NODES):
        raise ValueError("The cached native driver request differs from the OD objective.")
    command = request["command"]
    for flag, expected in (("--seed", "104"), ("--step-length", "0.1"), ("--end", "14400"),
                           ("--time-to-teleport", "-1"), ("--collision.check-junctions", "true")):
        if command.count(flag) != 1 or command[command.index(flag) + 1] != expected:
            raise ValueError("The cached SUMO command differs from the OD objective.")
    if (monitor.get("command", [])[:-2] != command or monitor.get("native_returncode") != 0
            or monitor.get("simulation_time_s") != 14400
            or monitor.get("native_version") != [22, "SUMO 1.27.1"]):
        raise ValueError("The native monitor does not confirm the declared command and SUMO completion.")

    pairs = ((prepared / "network.net.xml", "network.net.xml"),
             (proposal / "demand.rou.xml", "demand.rou.xml"),
             (prepared / "field-loops.add.xml", "source-e1.add.xml"),
             (prepared / "expected-counts.csv", "expected-counts.csv"),
             (prepared / "control.add.xml", "signal-programs.add.xml"),
             (proposal / "lane-intentions.json", "lane-intentions.json"))
    if len(run.get("inputs", [])) != 6 or len(run.get("snapshots", [])) != 6:
        raise ValueError("The cached replay must retain all six source and snapshot hashes.")
    input_hashes = {}
    for (path, name), source, snapshot in zip(pairs, run["inputs"], run["snapshots"]):
        digest = file_sha256(path)
        if digest != source["sha256"] or digest != snapshot["sha256"] or digest != file_sha256(native / name):
            raise ValueError(f"The cached replay input differs: {name}.")
        input_hashes[name] = digest
    vector = _read(proposal / "od-vector.json")
    initial = _read(prepared / "initial-od-vector.json")
    if (vector != _read(cached / "od-vector.json") or vector["values"] != initial["values"]
            or vector["integer_counts"] != [math.floor(v + .5) for v in vector["values"]]
            or vector["vehicle_count"] != sum(vector["integer_counts"])
            or vector["vehicle_count"] != initial["vehicle_count"]
            or vector["source_model_sha256"] != file_sha256(prepared / "od-model.json")):
        raise ValueError("The cached OD vector differs from the generated initial vector.")

    execution_dir = cached.with_name(cached.name + "-execution")
    execution = _read(execution_dir / "execution.json")
    binary = Path(sumo.__file__).parent / "bin/sumo.exe"
    if (execution.get("versions") != {name: "1.27.1" for name in ("eclipse-sumo", "sumolib", "traci")}
            or any(version(name) != "1.27.1" for name in ("eclipse-sumo", "sumolib", "traci"))
            or execution.get("sumo_sha256") != file_sha256(binary)
            or file_sha256(Path(command[0])) != file_sha256(binary)):
        raise ValueError("Reuse requires matching SUMO, traci and sumolib 1.27.1.")
    recorded = {key.replace("\\", "/"): value for key, value in execution["implementation_sha256"].items()}
    source_dir = Path(__file__).parent
    frozen_dir = execution_dir / "implementation/astra_sumo/core"
    implementation_hashes = {}
    for name in (*_NATIVE_MODULES, "hamburg_od_spsa.py"):
        frozen = frozen_dir / name
        digest = file_sha256(frozen)
        if recorded.get(f"astra_sumo/core/{name}") != digest:
            raise ValueError(f"The frozen native implementation hash differs: {name}.")
        if name == "hamburg_od_spsa.py":
            if _function_ast(frozen, "write_hamburg_od_demand") != _function_ast(source_dir / name, "write_hamburg_od_demand"):
                raise ValueError("The OD generation changed after the cached replay.")
            if _native_call_ast(frozen) != _native_call_ast(source_dir / name):
                raise ValueError("The native OD command changed after the cached replay.")
        elif file_sha256(source_dir / name) != digest:
            raise ValueError(f"The native implementation changed after the cached replay: {name}.")
        implementation_hashes[name] = digest

    expected = read_csv_rows(prepared / "expected-counts.csv")
    rows = audit_expected_to_e1_strict(expected, native / "field-counts.xml", count_attribute="nVehContrib")
    if len(rows) != 130 or any(row["measured_nVehContrib"] is None for row in rows):
        raise ValueError("All 130 expected rows require actual native E1 observations.")
    groups = defaultdict(list)
    for metadata, row in zip(expected, rows):
        for station, (node, assets) in STATIONS.items():
            if metadata["node_id"] == node and metadata["asset_id"] in assets:
                groups[station, row["begin"], row["end"]].append(row["diff_nVehContrib_minus_expected"])
    if len(groups) != 40 or any(len(values) != len(STATIONS[key[0]][1]) for key, values in groups.items()):
        raise ValueError("All 40 station rows require every constituent field.")
    residuals = [row["diff_nVehContrib_minus_expected"] for row in rows]
    station_residuals = [sum(group) for group in groups.values()]
    final = ET.parse(native / "summary.xml").getroot()[-1]
    completion = {key: int(final.get(key, "0")) for key in
                  ("loaded", "inserted", "arrived", "running", "waiting", "teleports", "collisions", "discarded")}
    vehicles = ET.parse(proposal / "demand.rou.xml").getroot().findall("vehicle")
    trips = ET.parse(native / "tripinfo.xml").getroot().findall("tripinfo")
    healthy = (all(completion[k] == len(vehicles) for k in ("loaded", "inserted", "arrived"))
               and not any(completion[k] for k in ("running", "waiting", "teleports", "collisions", "discarded"))
               and len(trips) == len(vehicles)
               and {trip.get("id") for trip in trips} == {vehicle.get("id") for vehicle in vehicles}
               and all(not trip.get("vaporized", "") for trip in trips))
    if completion != run.get("completion") or healthy != run.get("healthy"):
        raise ValueError("The cached native completion claim differs from its raw outputs.")
    evaluation = {"loss": sum(r * r for r in residuals), "MAE": sum(map(abs, residuals)) / 130,
                  "max_abs_error": max(map(abs, residuals)), "healthy": healthy,
                  "station_MAE": sum(map(abs, station_residuals)) / 40,
                  "station_max_abs_error": max(map(abs, station_residuals)), "completion": completion,
                  "exact_count_fit": healthy and not any(residuals),
                  "count_source": "Native E1 nVehContrib only", "field_rows": 130, "derived_station_rows": 40}
    evaluation_file = cached / "evaluation.json"
    if evaluation_file.exists() and _read(evaluation_file) != evaluation:
        raise ValueError("The cached metrics differ from the recomputed native E1 metrics.")
    evidence = [native / name for name in ("run.json", "command.json", "native-request.json", "stall-monitor.json",
                                           "field-counts.xml", "summary.xml", "tripinfo.xml")]
    evidence += [cached / "od-vector.json", execution_dir / "execution.json"]
    provenance = {"cached_dir": str(cached), "initial_loss_recomputed_from_native_e1": True,
                  "healthy": healthy, "input_sha256": input_hashes,
                  "native_implementation_sha256": implementation_hashes,
                  "od_functions_compared_as_ast": ["write_hamburg_od_demand"],
                  "native_call_compared_as_ast": "measure_hamburg_od:run_point_detector_demand",
                  "postprocessing": "Metrics are recomputed from raw native E1; metadata formatting changes do not require another simulation.",
                  "evidence_sha256": {str(path): file_sha256(path) for path in evidence}}
    return evaluation, provenance
