"""Judge harness: one full SUMO run of a Calibrate goal's run block, cached by its effective execution request.

The call mirrors the executed shared driver. ``measurement-implementation/run_case.py`` is read through the packet's
byte references and parsed, never imported: its ``run_point_detector_demand`` keywords must equal the ones used here,
its literal values must equal the goal's run block, and its input expressions and child PYTHONPATH rule (the venv's
site-packages, then the clone's src) are reproduced. Only the seed may differ from the goal, explicitly.

The cache key is the SHA-256 of the canonical effective execution request: every result-relevant argument of
run_point_detector_demand, including the junction-body hold nodes, the backend and the driver settings that enter
native-request.json outside the SUMO command; the SHA-256 of every input file; the scenario operation and the
perturbation with its key fingerprint; the SUMO binary and libsumo library SHA-256s; the executed implementation
modules' SHA-256s and the verifier commit. Paths never enter the key.

A candidate-network scenario (``candidate_scenario``, M4 fragility fixes) replaces the prepared network and control with
hash-bound candidate files and adds a ``candidate`` block (parent and candidate SHA-256 of both) to the request, the
judge record and the ledger line; everything else equals the parent request. A candidate whose manifest names another
parent than the goal's prepared net and control is refused. Without a candidate the request has no such block, so
every existing cache key is unchanged.

Measurements are compared by canonical content, never raw bytes: per-row nVehContrib counts and every native E1
interval attribute, contributing passages by vehicle/detector/interval (and with their times), completion and health
fields, and canonical digests of the summary, tripinfo, vehroute and lane-hold traces without XML headers, paths or
CPU durations. A judge.json also records the SHA-256 of the measurement code (canonical measurement, passage
reconciliation, scoring); a judge measured by other code is stale and is re-measured from its finished native run
without a new SUMO run. Planning refuses a clone whose core package has uncommitted changes outside the hashed
executed modules, because the commit in the key stands for every other imported core module.
Explore only: a judge measures a run under the supplied model and grants no promotion.
"""

from __future__ import annotations

import ast
import gzip
import hashlib
import importlib.metadata
import io
import json
import os
import re
import subprocess
import sys
import threading
import time
import xml.etree.ElementTree as ET
from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from importlib.util import find_spec
from pathlib import Path

from ..core.artifact_io import write_json_atomic
from ..core.benchmark_references import PacketCase
from ..core.candidate_contracts import file_sha256
from . import doctor, goal, perturb, scenario_ops
from .count_profile import E1_OUTPUT, INSTANT_E1, passage_report, score_counts

REQUEST_SCHEMA = "astra.twin-judge-request/v1"
JUDGE_SCHEMA = "astra.twin-judge/v1"
MEASUREMENT_SCHEMA = "astra.twin-measurement/v1"
ENTRY = "astra_sumo.core.detector_demand_replay.run_point_detector_demand"
MAX_PARALLEL = 3
# keyword -> prepared file the shared driver passes; output_dir and the two expressions below are checked separately.
INPUT_KEYWORDS = {"net_file": "network.net.xml", "route_file": "demand.rou.xml",
                  "e1_additional_file": "field-loops.add.xml", "expected_counts_csv": "expected-counts.csv",
                  "signal_program_file": "control.add.xml", "lane_intentions_file": "lane-intentions.json"}
# keyword -> goal.run field that must equal the driver's literal.
LITERAL_KEYWORDS = {"end_s": "end_s", "timeout_seconds": "timeout_s", "step_length": "step_s", "seed": "seed",
                    "backend": "backend", "all_stopped_timeout_s": "all_stopped_timeout_s",
                    "junction_body_hold": "junction_body_hold"}
CALL_KEYWORDS = frozenset({*INPUT_KEYWORDS, *LITERAL_KEYWORDS, "output_dir", "sumo_binary",
                           "junction_body_hold_nodes"})
SUMO_BINARY = ".venv/Lib/site-packages/sumo/bin/sumo.exe"
EXECUTED_MODULES = ("detector_demand_replay.py", "detector_demand.py", "simulation_stall.py", "lane_section_hold.py",
                    "route_sensor_matrix.py", "command_runner.py", "artifact_io.py", "candidate_contracts.py")
DISTRIBUTIONS = ("eclipse-sumo", "libsumo", "traci", "sumolib")
# Code that turns a finished native directory into judge.json: canonical measurement, passages and scores.
MEASUREMENT_MODULES = ("twin/runner.py", "twin/count_profile.py", "twin/ids.py", "core/route_sensor_matrix.py",
                       "core/detector_demand.py")
PACKAGE_DIR = "plugins/astra-sumo/src/astra_sumo"
TRACE_FILES = ("summary.xml", "tripinfo.xml", "vehicle-routes.xml")
# Wall-clock attributes a trace row may carry: never part of a canonical digest.
WALL_CLOCK_ATTRIBUTES = {"summary.xml": {"duration"}}
_BRAKING = re.compile(r"Warning: Vehicle '([^']+)' performs emergency braking on lane '([^']+)'.*time=([0-9]+(?:\.[0-9]+)?)")
_STAT = re.compile(r"^\s+([A-Za-z ]+):\s*(-?[0-9.]+)\s*$")


def canonical_json(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def digest(value: object) -> str:
    return hashlib.sha256(canonical_json(value)).hexdigest()


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# --- the executed shared driver, read and parsed -------------------------------------------------------------------

def _expression(node: ast.AST) -> object:
    try:
        return ast.literal_eval(node)
    except ValueError:
        return {"expression": ast.unparse(node)}


def shared_driver_call(reader: PacketCase) -> dict:
    """The keywords of the driver's single run_point_detector_demand call and its child PYTHONPATH entries."""
    reference = reader.resolve(goal.SHARED_DRIVER)
    tree = ast.parse(reference.data)
    calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)
             and isinstance(node.func, ast.Name) and node.func.id == "run_point_detector_demand"]
    if len(calls) != 1:
        raise ValueError(f"The shared driver must call run_point_detector_demand once, found {len(calls)}")
    keywords = {kw.arg: _expression(kw.value) for kw in calls[0].keywords}
    pythonpath = None
    for node in ast.walk(tree):
        if (isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Subscript)
                and ast.unparse(node.targets[0]) == "os.environ['PYTHONPATH']"):
            pythonpath = [item.right.value for item in ast.walk(node.value)
                          if isinstance(item, ast.BinOp) and isinstance(item.op, ast.Div)
                          and isinstance(item.left, ast.Name) and item.left.id == "REPO"
                          and isinstance(item.right, ast.Constant)]
    return {"alias": reader.key(goal.SHARED_DRIVER), "target": reference.target, "sha256": reference.sha256,
            "line": calls[0].lineno, "keywords": keywords, "child_pythonpath": pythonpath}


def check_driver(run: goal.RunBlock, driver: dict) -> list[str]:
    """Mismatches between the driver's call and the goal's run block; empty when the judge mirrors it exactly."""
    keywords, problems = driver["keywords"], []
    if set(keywords) != CALL_KEYWORDS:
        problems.append(f"keywords differ: {sorted(set(keywords) ^ CALL_KEYWORDS)}")
    for keyword, name in LITERAL_KEYWORDS.items():
        if keywords.get(keyword) != getattr(run, name):
            problems.append(f"{keyword}={keywords.get(keyword)!r} but the goal's run.{name}={getattr(run, name)!r}")
    for keyword, name in INPUT_KEYWORDS.items():
        if keywords.get(keyword) != {"expression": f"prepared / '{name}'"}:
            problems.append(f"{keyword} is {keywords.get(keyword)!r}, not prepared / '{name}'")
    if keywords.get("junction_body_hold_nodes") != {"expression": "_body_hold_nodes(prepared)"}:
        problems.append("junction_body_hold_nodes is not read from the prepared scenario")
    if keywords.get("sumo_binary") != {"expression": f"str(REPO / '{SUMO_BINARY}')"}:
        problems.append(f"sumo_binary is {keywords.get('sumo_binary')!r}")
    if driver["child_pythonpath"] != [".venv/Lib/site-packages", "plugins/astra-sumo/src"]:
        problems.append(f"child PYTHONPATH rule is {driver['child_pythonpath']!r}")
    return problems


# --- runtime identity -----------------------------------------------------------------------------------------------

def runtime_identity(repository_root: Path) -> dict:
    """Hashes of what executes a judge: SUMO binary, libsumo library, implementation modules and the commit."""
    root = Path(repository_root).resolve()
    binary = root / SUMO_BINARY
    spec = find_spec("libsumo")
    libraries = sorted(Path(spec.submodule_search_locations[0]).glob("_libsumo*")) if spec else []
    core = root / "plugins/astra-sumo/src/astra_sumo/core"
    versions = {}
    for name in DISTRIBUTIONS:
        try:
            versions[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            versions[name] = None
    state = doctor.repository_state(root)
    return {"sumo_binary_sha256": file_sha256(binary) if binary.is_file() else None,
            "libsumo_library_sha256": file_sha256(libraries[0]) if len(libraries) == 1 else None,
            "distributions": versions, "python": sys.version.split()[0],
            "implementation_sha256": {name: file_sha256(core / name) for name in EXECUTED_MODULES},
            "verifier_commit": state["commit"], "_clean": state["clean"],
            "_core_unhashed_dirty": core_unhashed_dirty(root)}


def core_unhashed_dirty(repository_root: Path) -> list[str]:
    """Core files with uncommitted changes that the key does not hash (anything but EXECUTED_MODULES).

    The key pins the other core modules the run imports (e.g. map_review through candidate_contracts) only through
    the verifier commit, which is true only while they are unmodified.
    """
    root = Path(repository_root).resolve()
    code, out, _ = doctor._capture(["git", "-C", str(root), "status", "--porcelain", "--", f"{PACKAGE_DIR}/core"],
                                   timeout_s=60)
    if code != 0:
        return ["<git status failed>"]
    names = [line[3:].strip().strip('"').rsplit("/", 1)[-1] for line in out.splitlines() if line.strip()]
    return sorted(name for name in names if name not in EXECUTED_MODULES)


def measurement_code_sha256(repository_root: Path) -> str:
    """SHA-256 over the measurement modules' SHA-256s: a judge measured by other code must be re-measured."""
    package = Path(repository_root).resolve() / PACKAGE_DIR
    return digest({name: file_sha256(package / name) for name in MEASUREMENT_MODULES})


# --- plans ----------------------------------------------------------------------------------------------------------

@dataclass
class JudgePlan:
    label: str
    purpose: str
    key: str
    request: dict
    run_dir: str
    staged: dict[str, str]
    sumo_binary: str
    child_pythonpath: list[str]
    provenance: dict = field(default_factory=dict)

    def to_json(self) -> dict:
        return asdict(self)


def stage_bytes(store: Path, name: str, data: bytes) -> Path:
    """Content-addressed copy ``<store>/inputs/<sha256[:24]>/<name>``; an existing copy must hold the same bytes."""
    sha = hashlib.sha256(data).hexdigest()
    path = Path(store) / "inputs" / sha[:24] / name
    if path.is_file():
        if file_sha256(path) != sha:
            raise ValueError(f"{path} exists with other bytes")
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_name(f".{name}.{os.getpid()}.partial")
    partial.write_bytes(data)
    os.replace(partial, path)
    return path


def effective_request(run: goal.RunBlock, *, seed: int, inputs_sha256: dict[str, str], scenario_op: dict | None,
                      perturbation: dict | None, base_demand_sha256: str, driver: dict, runtime: dict,
                      candidate: dict | None = None) -> dict:
    """Everything that can change a judge's result, without paths; its canonical SHA-256 is the cache key.

    ``candidate`` (a candidate-network scenario, ``candidate_scenario``) adds a ``candidate`` block with the replaced
    inputs' parent and candidate SHA-256s; without one the request has no such key, so every existing key is
    unchanged."""
    request = {
        "schema": REQUEST_SCHEMA, "entry": ENTRY,
        "arguments": {"end_s": run.end_s, "timeout_seconds": run.timeout_s, "step_length": run.step_s, "seed": seed,
                      "backend": run.backend, "all_stopped_timeout_s": run.all_stopped_timeout_s,
                      "junction_body_hold": run.junction_body_hold,
                      "junction_body_hold_nodes": list(run.junction_body_hold_nodes),
                      "monitor_interval_s": None, "state_times_s": []},
        "inputs_sha256": dict(inputs_sha256),
        "scenario_op": None if scenario_op is None else {k: scenario_op[k] for k in ("op", "cell", "shift_s")},
        "perturbation": None if perturbation is None else {"family": perturbation["family"],
                                                           "key_sha256": perturbation["key"]["key_sha256"]},
        "base_demand_sha256": base_demand_sha256,
        "driver": {"sha256": driver["sha256"], "child_pythonpath": driver["child_pythonpath"]},
        "runtime": {key: value for key, value in runtime.items() if not key.startswith("_")},
    }
    if candidate is not None:
        request["candidate"] = {keyword: {"parent_sha256": candidate["replaces"][keyword],
                                          "candidate_sha256": candidate["inputs_sha256"][keyword]}
                                for keyword in sorted(CANDIDATE_KEYWORDS)}
    return request


# A candidate-network scenario replaces exactly these prepared inputs; everything else equals the parent request.
CANDIDATE_KEYWORDS = {"net_file": "network.net.xml", "signal_program_file": "control.add.xml"}


# The manifest's parent record per replaced input: the parent file the candidate was built from.
CANDIDATE_PARENT_KEYS = {"net_file": "net_sha256", "signal_program_file": "control_sha256"}


def candidate_scenario(manifest: dict, *, base: Path | None = None) -> dict:
    """The hash-bound candidate files of a candidate-network scenario: ``manifest`` names ``id``, ``net`` and
    ``control`` (each ``{"path", "sha256"}``, a relative path read against ``base``), the ``parent`` it was built
    from (``net_sha256``, ``control_sha256``: ``plan_judge`` refuses a goal whose prepared net or control differs)
    and optionally the records that made them (``goal``, ``profile``, ``control_reindex``). Each file must hash to
    its declared SHA-256."""
    parent = manifest.get("parent") or {}
    if not all(re.fullmatch(r"[0-9a-f]{64}", str(parent.get(key) or "")) for key in CANDIDATE_PARENT_KEYS.values()):
        raise ValueError(f"candidate {manifest.get('id')!r}: the manifest names no parent net_sha256 and "
                         "control_sha256")
    files, records = {}, {}
    for keyword, name in (("net_file", "net"), ("signal_program_file", "control")):
        row = manifest.get(name) or {}
        path = Path(row.get("path", ""))
        if base is not None and not path.is_absolute():
            path = Path(base) / path
        if not path.is_file():
            raise ValueError(f"candidate {manifest.get('id')!r}: the {name} file {path} is missing")
        data = path.read_bytes()
        if hashlib.sha256(data).hexdigest() != row.get("sha256"):
            raise ValueError(f"candidate {manifest.get('id')!r}: the {name} file does not hash to its declared sha256")
        files[keyword], records[name] = data, {"path": str(path.resolve()), "sha256": row["sha256"]}
    if not str(manifest.get("id") or ""):
        raise ValueError("a candidate scenario needs an id")
    return {"id": str(manifest["id"]), "files": files, "records": records,
            "parent": {keyword: parent[key] for keyword, key in CANDIDATE_PARENT_KEYS.items()},
            "provenance": {key: manifest[key] for key in ("goal", "profile", "control_reindex", "parent")
                           if key in manifest}}


def plan_judge(goal_path: Path, store: Path, *, label: str, purpose: str, repository_root: Path,
               demand: bytes | None = None, op: str | None = None, family: str | None = None,
               draw: perturb.DrawKey | None = None, seed: int | None = None,
               runtime: dict | None = None, candidate: dict | None = None) -> JudgePlan:
    """Resolve, perturb and stage one judge run without executing it; the plan carries its cache key.

    ``candidate`` (``candidate_scenario``) replaces the prepared network and control with its hash-bound files; the
    request then carries both files' parent and candidate SHA-256s, and the plan's provenance the candidate's id and
    records."""
    runtime = runtime_identity(repository_root) if runtime is None else runtime
    if runtime.get("_core_unhashed_dirty"):
        raise ValueError(f"Core modules changed but not hashed by the key: {runtime['_core_unhashed_dirty']}; "
                         "commit them or add them to EXECUTED_MODULES.")
    loaded, goal_bytes = goal.load_goal(Path(goal_path))
    reader = goal.case_reader(repository_root, loaded.inputs.packet, loaded.inputs.case)
    driver = shared_driver_call(reader)
    problems = check_driver(loaded.run, driver)
    if problems:
        raise ValueError(f"The judge cannot mirror the shared driver: {problems}")
    prepared = {name: reader.read(f"prepared/{name}") for name in goal.PREPARED_FILES}
    wrong = [n for n, data in prepared.items() if hashlib.sha256(data).hexdigest() != loaded.inputs.files[n]]
    if wrong:
        raise ValueError(f"Resolved inputs differ from the goal: {wrong}")
    base = perturb.parse_demand(prepared["demand.rou.xml"] if demand is None else demand)
    current, op_record, draw_record = base, None, None
    if op is not None:
        _, arguments = scenario_ops.parse_op(op)
        current, op_record = scenario_ops.cell_depart_shift(
            current, prepared["lane-intentions.json"], horizon_s=loaded.run.end_s,
            od_pairs=loaded.variable_class.cells.get("od_pairs"), **arguments)
    if draw is not None:
        current, draw_record = perturb.perturb(current, family or "depart_jitter", draw)
    elif family is not None:
        raise ValueError("A perturbation family needs a draw key.")
    if candidate is not None:
        replaces = {keyword: loaded.inputs.files[name] for keyword, name in CANDIDATE_KEYWORDS.items()}
        if candidate.get("parent") != replaces:
            raise ValueError(f"candidate {candidate.get('id')!r} was built from another parent than this goal's "
                             f"prepared net and control: {candidate.get('parent')} != {replaces}")
    store = Path(store).resolve()
    replaced = {} if candidate is None else candidate["files"]
    staged = {keyword: stage_bytes(store, name, current.to_bytes() if name == "demand.rou.xml"
                                   else replaced.get(keyword, prepared[name]))
              for keyword, name in INPUT_KEYWORDS.items()}
    inputs_sha256 = {keyword: file_sha256(path) for keyword, path in staged.items()}
    scenario = None if candidate is None else {
        "replaces": {keyword: loaded.inputs.files[name] for keyword, name in CANDIDATE_KEYWORDS.items()},
        "inputs_sha256": inputs_sha256}
    request = effective_request(loaded.run, seed=loaded.run.seed if seed is None else seed,
                                inputs_sha256=inputs_sha256, scenario_op=op_record, perturbation=draw_record,
                                base_demand_sha256=base.sha256, driver=driver, runtime=runtime, candidate=scenario)
    key = digest(request)
    root = Path(repository_root).resolve()
    return JudgePlan(label=label, purpose=purpose, key=key, request=request,
                     run_dir=str(store / "runs" / key[:24]), staged={k: str(v) for k, v in staged.items()},
                     sumo_binary=str(root / SUMO_BINARY),
                     child_pythonpath=[str(root / entry) for entry in driver["child_pythonpath"]],
                     provenance={"goal": loaded.goal_id, "goal_sha256": hashlib.sha256(goal_bytes).hexdigest(),
                                 "goal_path": str(Path(goal_path).resolve()), "repository_root": str(root),
                                 "seed_source": "goal" if seed is None else "explicit override",
                                 "driver": {k: driver[k] for k in ("alias", "target", "line", "sha256")},
                                 "scenario_op": op_record, "perturbation": draw_record,
                                 "verifier_clean": runtime.get("_clean"),
                                 "measurement_code_sha256": measurement_code_sha256(root),
                                 **({} if candidate is None else {"candidate": {
                                     "id": candidate["id"], **candidate["records"], **candidate["provenance"]}})})


# --- canonical measurement ------------------------------------------------------------------------------------------

def _xml_rows(data: bytes, tag: str) -> Iterable[ET.Element]:
    for _, element in ET.iterparse(io.BytesIO(data), events=("end",)):
        if element.tag == tag:
            yield element


def _subtree(element: ET.Element, dropped: set[str]) -> list:
    return [element.tag, sorted((k, v) for k, v in element.attrib.items() if k not in dropped),
            [_subtree(child, dropped) for child in element]]


def trace_digest(name: str, data: bytes) -> dict:
    """Digest of a trace's rows without its XML header (generation time, paths) or wall-clock attributes."""
    tag = {"summary.xml": "step", "tripinfo.xml": "tripinfo", "vehicle-routes.xml": "vehicle"}[name]
    dropped = WALL_CLOCK_ATTRIBUTES.get(name, set())
    hasher, rows = hashlib.sha256(), 0
    try:
        for element in _xml_rows(data, tag):
            hasher.update(canonical_json(_subtree(element, dropped)) + b"\n")
            rows += 1
            element.clear()
    except ET.ParseError as error:  # a run that ended early can leave a truncated trace
        return {"rows": rows, "sha256": None, "error": str(error), "dropped_attributes": sorted(dropped)}
    return {"rows": rows, "sha256": hasher.hexdigest(), "dropped_attributes": sorted(dropped)}


def e1_rows(field_counts: bytes) -> list[dict]:
    return sorted(({**element.attrib} for element in _xml_rows(field_counts, "interval")),
                  key=lambda row: (row["id"], float(row["begin"]), float(row["end"])))


def tripinfo_departures(data: bytes) -> list[tuple[str, str, float]]:
    """(vehicle id, entry edge, departDelay s) per tripinfo row, in file order.

    The entry edge is the departLane id without its trailing ``_<index>``. A row without a departLane or a
    departDelay is refused rather than read as a zero delay.
    """
    rows = []
    for element in _xml_rows(data, "tripinfo"):
        vehicle, lane, delay = element.get("id"), element.get("departLane"), element.get("departDelay")
        if not vehicle or not lane or "_" not in lane or delay is None:
            raise ValueError(f"tripinfo row {vehicle!r} needs an id, a departLane <edge>_<index> and a departDelay")
        rows.append((vehicle, lane.rsplit("_", 1)[0], float(delay)))
        element.clear()
    return rows


def stdout_statistics(stdout: str) -> dict:
    """SUMO's Vehicles and Statistics blocks; the Performance block (wall time, speed) is left out."""
    section, values = None, {}
    for line in stdout.splitlines():
        if not line.startswith(" "):
            section = line.split(" (")[0].rstrip(":") if line.rstrip().endswith(":") else None
            continue
        match = _STAT.match(line)
        if match and section in {"Vehicles", "Statistics"}:
            values[f"{section}.{match[1].strip()}"] = match[2]
    return values


def canonical_measurement(*, field_counts: bytes, passages: dict | None, run: dict, command: dict,
                          stall: dict | None, traces: dict[str, bytes]) -> dict:
    """Canonical content of one run: comparable across paths, hosts and wall time."""
    try:
        rows = e1_rows(field_counts)
    except ET.ParseError:  # a truncated or missing E1 output has no canonical rows; health says why
        rows = []
    counts = [[r["id"], float(r["begin"]), float(r["end"]), int(float(r["nVehContrib"]))] for r in rows]
    braking = sorted([m[1], m[2], m[3]] for m in _BRAKING.finditer(command.get("stderr", "")))
    statistics = stdout_statistics(command.get("stdout", ""))
    health = {"status": run.get("status"), "healthy": run.get("healthy"), "native_status": run.get("native_status"),
              "completion": run.get("completion"), "observer_complete": run.get("observer_complete"),
              "missing_field_bins": run.get("missing_field_bins"), "vehicle_count": run.get("vehicle_count"),
              "stall_status": (stall or {}).get("status"), "simulation_time_s": (stall or {}).get("simulation_time_s"),
              "emergency_braking": {"summary_count": statistics.get("Vehicles.Emergency Braking"),
                                    "warnings": braking}}
    measurement = {"schema": MEASUREMENT_SCHEMA, "e1_counts": counts, "health": health, "statistics": statistics,
                   "traces": {name: trace_digest(name, data) for name, data in sorted(traces.items())}}
    holds = (stall or {}).get("lane_section_hold")
    if holds is not None:
        junction = holds.get("junction_body_hold") or {}
        measurement["traces"]["lane_section_hold"] = {
            "events": len(holds.get("events", [])), "junction_events": len(junction.get("events", [])),
            "sha256": digest([holds.get("events"), holds.get("still_held"), junction.get("events"),
                              junction.get("still_held")])}
    if passages is not None:
        full = sorted(([p["vehicle_id"], p["detector_id"], p["interval_begin_s"], p["time_s"],
                        p["notification_time_s"], p["occupancy_s"]] for p in passages["passages"]))
        measurement["passages"] = {
            "reconciled": passages.get("reconciled"), "count": len(full), "full": full,
            "early_departures": sorted([e["vehicle_id"], e["detector_id"], e["time_s"]]
                                       for e in passages.get("early_departures", [])),
            "outside_periodic_intervals": len(passages.get("outside_periodic_intervals", []))}
    measurement["digests"] = measurement_digests(measurement, rows)
    return measurement


def measurement_digests(measurement: dict, rows: list[dict] | None = None) -> dict:
    found = {"e1_counts": digest(measurement["e1_counts"]), "health": digest(measurement["health"]),
             "statistics": digest(measurement["statistics"])}
    if rows is not None:
        found["e1_all_attributes"] = digest([sorted(row.items()) for row in rows])
    if "passages" in measurement:
        passages = measurement["passages"]
        found["passage_bins"] = digest([row[:3] for row in passages["full"]])
        found["passage_full"] = digest(passages["full"])
        found["early_departures"] = digest(passages["early_departures"])
    for name, trace in measurement["traces"].items():
        found[f"trace:{name}"] = trace["sha256"]
    return found


def compare_measurements(left: dict, right: dict) -> dict:
    """Level-by-level equality of two canonical measurements plus the differing E1 rows and passage bins."""
    a, b = left["digests"], right["digests"]
    levels = {name: a.get(name) == b.get(name) for name in sorted(a.keys() & b.keys())}
    counts_a = {tuple(row[:3]): row[3] for row in left["e1_counts"]}
    counts_b = {tuple(row[:3]): row[3] for row in right["e1_counts"]}
    rows = [{"row": list(key), "left": counts_a.get(key), "right": counts_b.get(key)}
            for key in sorted(counts_a.keys() | counts_b.keys()) if counts_a.get(key) != counts_b.get(key)]
    result = {"levels": levels, "only_left": sorted(a.keys() - b.keys()), "only_right": sorted(b.keys() - a.keys()),
              "e1_rows_differing": rows}
    if "passages" in left and "passages" in right:
        bins_a = {tuple(row[:2]): row[2] for row in left["passages"]["full"]}
        bins_b = {tuple(row[:2]): row[2] for row in right["passages"]["full"]}
        result["vehicles_changing_bin"] = sum(bins_a.get(k) != bins_b.get(k) for k in bins_a.keys() | bins_b.keys())
    return result


def _read_run_file(directory: Path, name: str) -> bytes:
    path = directory / name
    return path.read_bytes() if path.is_file() else b""


def measure_run(native_dir: Path, *, step_length: float) -> tuple[dict, dict | None]:
    """Canonical measurement of a judge's native output directory, with the driver's passage reconciliation."""
    from ..core.route_sensor_matrix import read_contributing_e1_passages

    native_dir = Path(native_dir)
    run = json.loads(_read_run_file(native_dir, "run.json") or b"{}")
    command = json.loads(_read_run_file(native_dir, "command.json") or b"{}")
    stall = json.loads(_read_run_file(native_dir, "stall-monitor.json") or b"null")
    passages = None
    if (native_dir / INSTANT_E1).is_file() and (native_dir / E1_OUTPUT).is_file():
        try:
            passages = read_contributing_e1_passages(instant_file=native_dir / INSTANT_E1,
                                                     periodic_file=native_dir / E1_OUTPUT,
                                                     detector_ids=run["instant_to_periodic_ids"],
                                                     step_length=step_length)
        except (ET.ParseError, ValueError, KeyError):  # an incomplete run is measured without passages
            passages = None
    measurement = canonical_measurement(
        field_counts=_read_run_file(native_dir, E1_OUTPUT), passages=passages, run=run, command=command,
        stall=stall, traces={name: _read_run_file(native_dir, name) for name in TRACE_FILES
                             if (native_dir / name).is_file()})
    return measurement, passages


def baseline_measurement(reader: PacketCase) -> dict:
    """The tracked parent run's canonical measurement, read through the packet references."""
    directory = goal.BASELINE_RUN_DIR
    return canonical_measurement(
        field_counts=reader.read(f"{directory}/{E1_OUTPUT}"),
        passages=passage_report(reader.read(goal.PARENT_PASSAGES)),
        run=reader.read_json(f"{directory}/run.json"), command=reader.read_json(f"{directory}/command.json"),
        stall=reader.read_json(f"{directory}/stall-monitor.json"),
        traces={name: reader.read(f"{directory}/{name}") for name in TRACE_FILES})


def score_measurement(measurement: dict, main_rows: list[dict], init_rows: list[dict]) -> tuple[dict, dict] | None:
    """TAE scores and the 182 per-row counts from the canonical E1 rows; None when a bin is missing."""
    root = ET.Element("detector")
    for detector, begin, end, count in measurement["e1_counts"]:
        ET.SubElement(root, "interval", id=detector, begin=f"{begin:g}", end=f"{end:g}", nVehContrib=str(count))
    try:
        score, rows = score_counts(main_rows, init_rows, ET.tostring(root))
    except ValueError:
        return None
    return score, {row["row_id"]: {"measured": row["measured"], "diff": row["diff"]} for row in rows}


# --- execution ------------------------------------------------------------------------------------------------------

class MemoryWatch:
    """Peak memory of this process and its descendants through a Windows job object (None elsewhere)."""

    def __init__(self, interval_s: float = 1.0) -> None:
        self.interval_s, self.peaks, self.names, self.error = interval_s, {}, {}, None
        self._stop = threading.Event()
        self.job = None
        try:
            import win32api
            import win32job

            self.job = win32job.CreateJobObject(None, "")
            win32job.AssignProcessToJobObject(self.job, win32api.GetCurrentProcess())
        except Exception as error:  # noqa: BLE001 - memory capture is optional evidence, never a run condition.
            self.error = f"{type(error).__name__}: {error}"
        self._thread = threading.Thread(target=self._loop, daemon=True)
        if self.job is not None:
            self._thread.start()

    def _sample(self) -> None:
        import win32api
        import win32job
        import win32process

        for pid in win32job.QueryInformationJobObject(self.job, win32job.JobObjectBasicProcessIdList):
            try:
                handle = win32api.OpenProcess(0x1000 | 0x0010, False, pid)
                try:
                    info = win32process.GetProcessMemoryInfo(handle)
                    self.peaks[pid] = max(self.peaks.get(pid, 0), info["PeakWorkingSetSize"])
                    self.names.setdefault(pid, Path(win32process.GetModuleFileNameEx(handle, 0)).name)
                finally:
                    win32api.CloseHandle(handle)
            except Exception:  # noqa: BLE001 - a process may exit between listing and opening.
                continue

    def _loop(self) -> None:
        while not self._stop.wait(self.interval_s):
            self._sample()

    def result(self) -> dict:
        if self.job is None:
            return {"available": False, "error": self.error}
        self._sample()
        self._stop.set()
        import win32job

        extended = win32job.QueryInformationJobObject(self.job, win32job.JobObjectExtendedLimitInformation)
        largest = max(self.peaks.items(), key=lambda item: item[1], default=(None, 0))
        return {"available": True, "sampling_s": self.interval_s,
                "peak_working_set_bytes": largest[1], "peak_working_set_process": self.names.get(largest[0]),
                "peak_working_set_by_process": {self.names.get(pid, str(pid)) + f"#{pid}": peak
                                                for pid, peak in sorted(self.peaks.items())},
                "peak_process_commit_bytes": extended["PeakProcessMemoryUsed"],
                "peak_job_commit_bytes": extended["PeakJobMemoryUsed"],
                "basis": "PeakWorkingSetSize sampled per process; commit peaks from the job object"}


def read_judge(plan: JudgePlan) -> dict | None:
    """A completed judge for this plan's exact request and measurement code, or None.

    A partial directory is not a result. A judge measured by other code (or without a recorded code hash) is stale:
    None, so the caller re-measures it from native-done.json without a new native run.
    """
    path = Path(plan.run_dir) / "judge.json"
    if not path.is_file():
        return None
    result = json.loads(path.read_text(encoding="utf-8"))
    if result.get("request_sha256") != plan.key:
        raise ValueError(f"{path} records request {result.get('request_sha256')}, expected {plan.key}")
    expected = plan.provenance.get("measurement_code_sha256")
    if expected is not None and result.get("measurement_code_sha256") != expected:
        return None
    return result


def _set_aside(run_dir: Path) -> str | None:
    if not run_dir.exists():
        return None
    for index in range(1, 1000):
        target = run_dir.with_name(f"{run_dir.name}.partial-{index}")
        if not target.exists():
            os.replace(run_dir, target)
            return str(target)
    raise RuntimeError(f"Too many partial directories beside {run_dir}")


def _native_record(run_dir: Path, plan: JudgePlan) -> dict | None:
    """The record of a finished native run of exactly this request, so measurement can resume without a rerun."""
    done, request = run_dir / "native-done.json", run_dir / "request.json"
    if not (done.is_file() and request.is_file()) or json.loads(request.read_bytes()) != plan.request:
        return None
    return json.loads(done.read_bytes())


def run_native(plan: JudgePlan, runner=None, *, watch_memory: bool = True) -> dict:
    """Execute the plan's native run once and record its wall time, memory and report in native-done.json."""
    from ..core.detector_demand_replay import run_point_detector_demand

    runner = runner or run_point_detector_demand
    run_dir = Path(plan.run_dir)
    set_aside = _set_aside(run_dir)
    run_dir.mkdir(parents=True)
    write_json_atomic(run_dir / "request.json", plan.request)
    write_json_atomic(run_dir / "plan.json", plan.to_json())
    os.environ["PYTHONPATH"] = os.pathsep.join(plan.child_pythonpath)
    arguments = plan.request["arguments"]
    watch, started_utc, started = MemoryWatch() if watch_memory else None, utc_now(), time.perf_counter()
    native = runner(net_file=Path(plan.staged["net_file"]), route_file=Path(plan.staged["route_file"]),
                    e1_additional_file=Path(plan.staged["e1_additional_file"]),
                    expected_counts_csv=Path(plan.staged["expected_counts_csv"]), output_dir=run_dir / "native",
                    end_s=arguments["end_s"], timeout_seconds=arguments["timeout_seconds"],
                    step_length=arguments["step_length"], seed=arguments["seed"], backend=arguments["backend"],
                    all_stopped_timeout_s=arguments["all_stopped_timeout_s"], sumo_binary=plan.sumo_binary,
                    signal_program_file=Path(plan.staged["signal_program_file"]),
                    lane_intentions_file=Path(plan.staged["lane_intentions_file"]),
                    junction_body_hold=arguments["junction_body_hold"],
                    junction_body_hold_nodes=tuple(arguments["junction_body_hold_nodes"]))
    record = {"started_utc": started_utc, "finished_utc": utc_now(), "wall_s": round(time.perf_counter() - started, 3),
              "memory": watch.result() if watch else {"available": False, "error": "not watched"},
              "status": native.get("status"), "healthy": native.get("healthy"),
              "errors": native.get("errors", []), "set_aside": set_aside}
    write_json_atomic(run_dir / "native-done.json", record)
    return record


def execute_plan(plan: JudgePlan, *, main_rows: list[dict], init_rows: list[dict], runner=None,
                 watch_memory: bool = True) -> dict:
    """Run one plan in this process (no cache lookup), or resume a finished native run, then write judge.json."""
    run_dir = Path(plan.run_dir)
    record = _native_record(run_dir, plan)
    resumed = record is not None
    if record is None:
        record = run_native(plan, runner, watch_memory=watch_memory)
    step = plan.request["arguments"]["step_length"]
    code = measurement_code_sha256(Path(plan.provenance["repository_root"]))
    measurement, _ = measure_run(run_dir / "native", step_length=step)
    (run_dir / "measurement.json.gz").write_bytes(gzip.compress(canonical_json(measurement), mtime=0))
    # An unhealthy run is still scored when every bin exists; its status says it cannot support a verdict.
    scored = score_measurement(measurement, main_rows, init_rows)
    stall = run_dir / "native/stall-monitor.json"
    library = (json.loads(stall.read_bytes()) if stall.is_file() else {}).get("native_library") or {}
    result = {"schema": JUDGE_SCHEMA, "label": plan.label, "purpose": plan.purpose, "request_sha256": plan.key,
              "status": record["status"], "healthy": record["healthy"], "native_run": not resumed,
              "started_utc": record["started_utc"], "finished_utc": record["finished_utc"],
              "wall_s": record["wall_s"], "memory": record["memory"],
              "score": None if scored is None else {k: scored[0][k] for k in ("tae_main", "sse", "max", "exact",
                                                                                "init52_tae", "all182_tae")},
              "rows": None if scored is None else scored[1], "digests": measurement["digests"],
              "health": measurement["health"], "native_library_sha256": library.get("sha256"),
              "native_errors": record["errors"], "set_aside": record["set_aside"],
              "provenance": plan.provenance, "seed": plan.request["arguments"]["seed"],
              **({} if "candidate" not in plan.request else {"candidate": plan.request["candidate"]}),
              "measurement_code_sha256": code,
              "claim_boundary": "Explore only: one simulated run under the supplied model and declared driver holds. "
                                "No field timing, OD identification or promotion authority."}
    write_json_atomic(run_dir / "judge.json", result)
    return result


def ledger_line(plan: JudgePlan, result: dict | None, error: str | None = None) -> dict:
    draw = (plan.provenance.get("perturbation") or {})
    return {"id": plan.label, "purpose": plan.purpose, "request_sha256": plan.key,
            "inputs_sha256": plan.request["inputs_sha256"]["route_file"],
            "inputs_digest": digest(plan.request["inputs_sha256"]),
            "draw_key": None if not draw else {"family": draw["family"], **draw["key"]},
            "scenario_op": plan.request["scenario_op"], "seed": plan.request["arguments"]["seed"],
            **({} if "candidate" not in plan.request else {"candidate": {
                "id": plan.provenance["candidate"]["id"],
                **{keyword: row["candidate_sha256"] for keyword, row in plan.request["candidate"].items()}}}),
            "wall_s": None if result is None else result["wall_s"],
            "peak_rss_bytes": None if result is None else (result["memory"] or {}).get("peak_working_set_bytes"),
            "peak_job_commit_bytes": None if result is None else (result["memory"] or {}).get("peak_job_commit_bytes"),
            "status": "failed" if result is None else result["status"],
            "native_run": True if result is None else result["native_run"],
            "healthy": None if result is None else result["healthy"],
            "tae_main": None if result is None or result["score"] is None else result["score"]["tae_main"],
            "error": error, "run_dir": plan.run_dir, "logged_utc": utc_now()}


def _run_worker(plan: JudgePlan, store: Path, ledger: Path, lock: threading.Lock) -> dict:
    job = Path(plan.run_dir).with_name(Path(plan.run_dir).name + ".job.json")
    write_json_atomic(job, plan.to_json())
    temp = Path(store) / "tmp"
    temp.mkdir(parents=True, exist_ok=True)
    env = {**os.environ, "PYTHONPATH": os.pathsep.join(plan.child_pythonpath), "TEMP": str(temp), "TMP": str(temp),
           "TMPDIR": str(temp), "PYTHONDONTWRITEBYTECODE": "1"}
    done = subprocess.run([sys.executable, "-B", "-m", "astra_sumo.twin.runner", str(job)], env=env,
                          stdin=subprocess.DEVNULL, capture_output=True, text=True,
                          timeout=plan.request["arguments"]["timeout_seconds"] + 900,
                          creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    try:
        result = read_judge(plan)
    except ValueError:
        result = None
    error = None if result is not None else (done.stderr[-2000:] or f"worker exited {done.returncode}")
    with lock:
        with Path(ledger).open("a", encoding="utf-8", newline="\n") as stream:
            stream.write(json.dumps(ledger_line(plan, result, error), ensure_ascii=False) + "\n")
    return result if result is not None else {"label": plan.label, "status": "failed", "error": error}


def judge_many(plans: list[JudgePlan], *, store: Path, ledger: Path, workers: int = MAX_PARALLEL) -> list[dict]:
    """Run uncached plans in at most ``MAX_PARALLEL`` worker processes; cached plans start no run."""
    if len({plan.key for plan in plans}) != len(plans):
        raise ValueError("Two plans share one execution request; judge it once.")
    workers = max(1, min(workers, MAX_PARALLEL))
    results: dict[str, dict] = {}
    pending = []
    for plan in plans:
        cached = read_judge(plan)
        if cached is not None:
            results[plan.key] = {**cached, "cache": "hit"}
        else:
            pending.append(plan)
    lock = threading.Lock()
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {plan.key: pool.submit(_run_worker, plan, store, ledger, lock) for plan in pending}
        for key, future in futures.items():
            results[key] = {**future.result(), "cache": "miss"}
    return [results[plan.key] for plan in plans]


def _main(job_path: str) -> int:
    plan = JudgePlan(**json.loads(Path(job_path).read_text(encoding="utf-8")))
    loaded, _ = goal.load_goal(Path(plan.provenance["goal_path"]))
    reader = goal.case_reader(Path(plan.provenance["repository_root"]), loaded.inputs.packet, loaded.inputs.case)
    result = execute_plan(plan, main_rows=reader.read_csv("prepared/expected-counts.csv"),
                          init_rows=reader.read_csv("prepared/initialization-counts.csv"))
    print(json.dumps({"label": result["label"], "status": result["status"], "wall_s": result["wall_s"]}))
    return 0 if result["status"] == "measured" else 1


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv[1]))
