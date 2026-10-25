"""Calibrate twin goal ``astra.twin-goal/v1``: frozen inputs, run block and observation contract.

The run block is copied from tracked machine fields (``run.json``, ``native-request.json`` and the executed shared
driver), never from prose. The baseline is recomputed from the tracked E1 output and must equal the recorded one.
"""

from __future__ import annotations

import hashlib
import json
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from ..core.artifact_io import write_text_atomic
from ..core.benchmark_references import (BenchmarkReferenceError, PacketCase, csv_rows, find_tracked_file,
                                         safe_relative)
from ..core.candidate_contracts import file_sha256
from . import ids, invariants
from .count_profile import (incomplete_rows, parse_field_loops, parse_intentions, parse_network, read_passage_report,
                            score_counts)

SCHEMA = "astra.twin-goal/v1"
PREPARED_FILES = ("network.net.xml", "control.add.xml", "demand.rou.xml", "field-loops.add.xml",
                  "expected-counts.csv", "initialization-counts.csv", "lane-intentions.json")
BASELINE_RUN_DIR = "evaluation/native"
BASELINE_RUN_FILES = ("run.json", "native-request.json", "command.json", "field-counts.xml", "vehicle-routes.xml",
                      "tripinfo.xml", "summary.xml", "sumo.log")
PARENT_PASSAGES = "evaluation/contributing-passages.json.gz"
SHARED_DRIVER = "measurement-implementation/run_case.py"
# The frozen count window each packet's main rows were cut from. A declared lineage must exist and equal every row.
COUNT_LINEAGE = {"benchmarks/hamburg_ring1_chronological_od_feedback_20261024_v1":
                 "benchmarks/hamburg_ring1_count_window_20261020_v1/field-counts-15min.csv"}
FLAGS_TEMPLATE = "core/detector_demand_replay.py:154-162"
_TIMEOUT = re.compile(r"\btimeout_seconds\s*=\s*(\d+(?:\.\d+)?)")
_UTC = "%Y-%m-%dT%H:%M:%SZ"
# goal_id names a directory: no trailing dot or hyphen (Windows drops trailing dots), at most 100 characters.
GOAL_ID_PATTERN = r"^[a-z0-9](?:[a-z0-9.-]{0,98}[a-z0-9])?$"
_WINDOWS_DEVICES = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)), *(f"lpt{i}" for i in range(1, 10))}


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Verifier(_Strict):
    python: str
    sync: str
    commit: str


class BaselineRun(_Strict):
    dir: str
    files: list[str]
    passages: str


class ParentPassages(_Strict):
    path: str
    reconciled: bool
    inputs_unchanged: bool
    instant_e1: str


class Inputs(_Strict):
    packet: str
    case: str
    resolver: str
    files: dict[str, str]
    baseline_run: BaselineRun
    parent_passages: ParentPassages


class RunBlock(_Strict):
    backend: str
    sumo: str
    seed: int
    step_s: float
    end_s: float
    flags_template: str
    recorded_options: dict[str, str]
    lane_intentions: bool
    junction_body_hold: bool
    junction_body_hold_nodes: list[str] = Field(min_length=1)
    all_stopped_timeout_s: float
    timeout_s: float
    provenance: dict[str, str]


class Lineage(_Strict):
    main: str
    main_sha256: str | None
    main_rows_equal: str | None
    init: str
    per_row: str


class Observation(_Strict):
    attribute: Literal["nVehContrib"]
    e1_period_s: float
    e1_begin_s: float
    sim_zero_utc: str
    init_utc: list[str]
    main_utc: list[str]
    sim_bin: str
    field_bin: str
    complete_rule: str
    complete: dict[str, str]
    time_alignment: str
    lineage: Lineage
    e1_before_lane_end_m: dict[str, float]
    uncounted: dict[str, int | str]


class Baseline(_Strict):
    tae_main: int
    sse: int
    mae: float
    max: int
    exact: int
    init52_tae: int
    all182_tae: int
    recorded: str


class VariableClass(_Strict):
    name: str
    ops: list[str]
    cells: dict[str, int]
    preserve: list[str]
    frozen: list[str]


class Objective(_Strict):
    primary: str
    report_only: list[str]


class Robustness(_Strict):
    family: str
    K: int
    K_p: int
    crn: bool
    status: str


class Provisional(_Strict):
    g_TAE: float
    m_row: int
    m_init: int
    k_post: int
    delta_NI: str


class K3(_Strict):
    family: str
    magnitude_grid_s: dict[str, int] = Field(min_length=1)
    positive_cells: str
    positives: int
    calibration_nulls: int
    validation_nulls: int
    truth: str


class GateProtocol(_Strict):
    alpha: float
    g_min: float
    provisional: Provisional
    provisional_use: str
    k3: K3
    rules: str
    calibrated: str


class Budget(_Strict):
    sumo_runs: int
    duration: str
    basis: str


class Stop(_Strict):
    stall_scored: int
    refuted: str
    budget: Budget


class CalibrateGoal(_Strict):
    schema_id: Literal["astra.twin-goal/v1"] = Field(alias="schema")
    goal_id: str = Field(pattern=GOAL_ID_PATTERN)
    capability: Literal["calibrate"]
    mode: Literal["explore"]
    verifier: Verifier
    inputs: Inputs
    run: RunBlock
    observation: Observation
    baseline: Baseline
    variable_class: VariableClass
    objective: Objective
    robustness: Robustness
    gate_protocol: GateProtocol
    stop: Stop
    claim_boundary: str

    @field_validator("goal_id")
    @classmethod
    def _not_a_device_name(cls, value: str) -> str:
        if value.split(".")[0] in _WINDOWS_DEVICES:
            raise ValueError(f"goal_id {value!r} is a reserved Windows device name")
        return value

    def to_json_bytes(self) -> bytes:
        text = json.dumps(self.model_dump(mode="json", by_alias=True), indent=2, ensure_ascii=False)
        return (text + "\n").encode("utf-8")


# Human-declared parts of the calibrate goal (design "CALIBRATE GOAL"); everything else is read from tracked files.
_DECLARED = {
    "verifier": {"python": "3.12.13", "sync": "uv sync --python 3.12.13 --frozen --extra dev --extra native",
                 "commit": "not a goal field: recorded per run, profile and cache key"},
    "objective": {"primary": "TAE_main130, candidate minus parent on identical draws",
                  "report_only": ["SSE", "max", "exact rows", "GEH raw and hourly", "station40 (derived)", "all182"]},
    "robustness": {"family": "per-vehicle departure jitter U(-0.5,0.5) s, keyed by HMAC(key_k, vehicle_id)", "K": 4,
                   "K_p": 8, "crn": True, "status": "declared by a human; calibrated in M2"},
    "gate_protocol": {
        "alpha": 0.05, "g_min": 3,
        "provisional": {"g_TAE": 10, "m_row": 3, "m_init": 2, "k_post": 5, "delta_NI": "g/2"},
        "provisional_use": "diagnostic gate evaluations before the freeze only; never a verdict that enters the "
                           "trial log",
        "k3": {"family": "cell_depart_shift", "magnitude_grid_s": {"moderate": 300, "large": 900},
               "positive_cells": "4 single-field cells with >= 10 vehicles, fixed sampler seed", "positives": 8,
               "calibration_nulls": 12, "validation_nulls": 12, "truth": "own key per case, outside the panel"},
        "rules": "Noise and acceptance section 4; hashed with the goal",
        "calibrated": "separate artifact <store>/gates/<goal_id>/gate-<sha8>.json; every verdict records its sha"},
    "stop": {"stall_scored": 5, "refuted": "3 refuted hypotheses since the last acceptance -> class_exhausted",
             "budget": {"sumo_runs": 100, "duration": "8h",
                        "basis": "2 promotions (2x16) + confirmatory comparison (16) + 10 scored hypotheses "
                                 "(10x5, parent cached) = 98 runs"}},
    "claim_boundary": "Explore only. Simulated counts under the supplied model and declared driver holds. No field "
                      "SPaT, no OD identification. Accounting identities locate counts, not causes.",
}


def default_goal_id(packet: str, case: str, sim_zero_utc: str) -> str:
    """``calib-<site>-<yyyymmdd>-<case tokens>-v1``; purely alphabetic case words are dropped."""
    tokens = Path(packet).name.split("_")
    site = next((t for t in tokens[1:] if re.search(r"[a-z]", t) and re.search(r"\d", t)
                 and not re.fullmatch(r"v\d+", t)), tokens[0])
    case_token = "".join(t.replace("minus", "m").replace("plus", "p") for t in case.split("_") if not t.isalpha())
    return f"calib-{site}-{sim_zero_utc[:10].replace('-', '')}-{case_token.lower()}-v1"


def recorded_options(command: list[str]) -> dict[str, str]:
    """Non-path SUMO options of a recorded command (binary first, then option/value pairs)."""
    return {option: value for option, value in zip(command[1::2], command[2::2])
            if option not in {"--log", "--error-log"} and not option.endswith(("-file", "-files", "-output"))}


def shared_driver_timeout(text: str) -> tuple[float, int]:
    hits = [(float(match[1]), number) for number, line in enumerate(text.splitlines(), start=1)
            for match in _TIMEOUT.finditer(line)]
    if len(hits) != 1:
        raise ValueError(f"The shared driver must set timeout_seconds exactly once, found {len(hits)}")
    return hits[0]


def case_reader(repository_root: Path, packet: str, case: str) -> PacketCase:
    return PacketCase(Path(repository_root) / safe_relative(packet), case, repository_root=repository_root)


def resolve_calibrate_case(repository_root: Path, packet: str, case: str) -> dict:
    """Resolve the prepared aliases and baseline run of one packet case and report every check."""
    return _resolve(case_reader(repository_root, packet, case), packet)[0]


def _resolve(reader: PacketCase, packet: str) -> tuple[dict, dict]:
    """The resolve report, plus the parsed run records that goal-create reuses instead of reading them again."""
    manifest_path = reader.packet_root / "manifest.json"
    manifest = ({row["path"]: row["sha256"] for row in json.loads(manifest_path.read_text("utf-8"))["files"]}
                if manifest_path.is_file() else {})
    aliases = []
    for name in PREPARED_FILES:
        key = reader.key(f"prepared/{name}")
        row = {"name": name, "alias": key, "declared_sha256": reader.aliases.get(key, {}).get("sha256")}
        try:
            reference = reader.resolve(f"prepared/{name}")
        except (FileNotFoundError, BenchmarkReferenceError) as error:
            aliases.append({**row, "match": False, "error": str(error)})
            continue
        aliases.append({**row, "scope": reference.scope, "target": reference.target,
                        "tracked": reader.tracked_name(reference), "sha256": reference.sha256,
                        "match": reference.scope != "direct" and reference.sha256 == row["declared_sha256"]})
    baseline = []
    for name in (*(f"{BASELINE_RUN_DIR}/{n}" for n in BASELINE_RUN_FILES), PARENT_PASSAGES):
        found = find_tracked_file(reader.packet_root / reader.key(name))
        if found is None:
            baseline.append({"file": name, "present": False})
            continue
        tracked = found[0].resolve().relative_to(reader.packet_root).as_posix()
        baseline.append({"file": name, "present": True, "tracked": reader.tracked_name(found[0]),
                         "gzip_layers": found[1], "manifest_match": manifest.get(tracked) == file_sha256(found[0])})
    run = reader.read_json(f"{BASELINE_RUN_DIR}/run.json")
    request = reader.read_json(f"{BASELINE_RUN_DIR}/native-request.json")
    scenario = reader.read_json("prepared/scenario.json")
    passages = read_passage_report(reader, PARENT_PASSAGES)
    driver = reader.resolve(SHARED_DRIVER)
    timeout_s, line = shared_driver_timeout(driver.data.decode("utf-8"))
    holds = invariants.hold_node_sources(run, request, scenario)
    hold_equal = bool(holds["run.json"]) and all(nodes == holds["run.json"] for nodes in holds.values())
    checks = {"aliases_resolved": len(aliases) == len(PREPARED_FILES) and all(row["match"] for row in aliases),
              "baseline_present": all(row["present"] for row in baseline),
              "baseline_manifest": all(row.get("manifest_match") for row in baseline),
              "passages_reconciled": passages.get("reconciled") is True and passages.get("inputs_unchanged") is True,
              "hold_nodes_equal": hold_equal, "request_hold": request.get("junction_body_hold") is True,
              "all_stopped_timeout": isinstance(request.get("all_stopped_timeout_s"), (int, float))}
    report = {"schema": "astra.twin-resolve/v1", "packet": safe_relative(packet).as_posix(), "case": reader.case,
              "resolver": f"packet-root byte-references.json (keys cases/<case>/..., rule {reader.rule!r})",
              "aliases": aliases, "baseline_run": baseline,
              "passages": {"file": PARENT_PASSAGES, "reconciled": passages.get("reconciled"),
                           "inputs_unchanged": passages.get("inputs_unchanged"),
                           "passages": len(passages.get("passages", []))},
              "hold_nodes": {"sources": holds, "equal": hold_equal, "count": len(holds["run.json"] or [])},
              "timeouts": {"timeout_s": timeout_s,
                           "timeout_source": {"alias": reader.key(SHARED_DRIVER), "target": driver.target,
                                              "line": line, "sha256": driver.sha256},
                           "all_stopped_timeout_s": request.get("all_stopped_timeout_s"),
                           "all_stopped_timeout_source": f"{BASELINE_RUN_DIR}/native-request.json"},
              "checks": checks, "pass": all(checks.values())}
    report["status"] = "pass" if report["pass"] else "blocked"
    return report, {"run": run, "request": request, "scenario": scenario, "passages": passages}


def _observation(reader: PacketCase, packet: str, files: dict[str, str], passages: dict) -> dict:
    main_rows = reader.read_csv("prepared/expected-counts.csv")
    init_rows = reader.read_csv("prepared/initialization-counts.csv")
    loops = parse_field_loops(reader.read("prepared/field-loops.add.xml"))
    run_e1 = list(ET.fromstring(reader.read(f"{BASELINE_RUN_DIR}/e1.add.xml")).iter("inductionLoop"))
    periods = {loop.period_s for loop in loops.values()}
    begins = {float(loop.get("begin", "0")) for loop in run_e1}
    if len(periods) != 1 or len(begins) != 1 or {loop.get("id") for loop in run_e1} != set(loops):
        raise ValueError("goal-create refused: E1 periods, begins or ids are not uniform with the run")
    first = min(float(row["begin"]) for row in init_rows)
    sim_zero = min(datetime.strptime(r["source_begin_utc"], _UTC) for r in init_rows if float(r["begin"]) == first)
    aligned = sum(datetime.strptime(r["source_begin_utc"], _UTC) == sim_zero + timedelta(seconds=float(r["begin"]))
                  for r in main_rows + init_rows)
    incomplete = incomplete_rows(main_rows + init_rows)
    if incomplete or aligned != len(main_rows) + len(init_rows):
        raise ValueError(f"goal-create refused: {len(incomplete)} rows with incomplete source observations, "
                         f"{len(main_rows) + len(init_rows) - aligned} rows off the simulation clock")
    lineage = COUNT_LINEAGE.get(packet)
    lineage_sha = lineage_equal = None
    if lineage is not None:
        lineage_path = reader.repository_root / lineage
        if not lineage_path.is_file():
            raise ValueError(f"goal-create refused: the declared count-window lineage {lineage} is missing")
        data = lineage_path.read_bytes()
        lineage_sha = hashlib.sha256(data).hexdigest()
        source = {(r["detector_id"], r["source_begin_utc"]): r["expected_total"] for r in csv_rows(data)}
        equal = sum(source.get((r["detector_id"], r["source_begin_utc"])) == r["expected_total"] for r in main_rows)
        if equal != len(main_rows):
            raise ValueError(f"goal-create refused: {len(main_rows) - equal} main rows differ from {lineage}")
        lineage_equal = f"{equal}/{len(main_rows)}"
    network = parse_network(reader.read("prepared/network.net.xml"))
    intended = parse_intentions(reader.read("prepared/lane-intentions.json"))
    early = passages["early_departures"]
    return {
        "attribute": "nVehContrib", "e1_period_s": periods.pop(), "e1_begin_s": begins.pop(),
        "sim_zero_utc": sim_zero.strftime(_UTC),
        "init_utc": [min(r["source_begin_utc"] for r in init_rows), max(r["source_end_utc"] for r in init_rows)],
        "main_utc": [min(r["source_begin_utc"] for r in main_rows), max(r["source_end_utc"] for r in main_rows)],
        "sim_bin": "[begin,end) on the step-ceiling of the instant-E1 leave time (core/route_sensor_matrix.py)",
        "field_bin": "three SensorThings 5-min observations per 15-min row",
        "complete_rule": "score a row only if source_observation_count == expected_source_observation_count; "
                         "otherwise a new goal, never imputation",
        "complete": {"main": f"{len(main_rows)}/{len(main_rows)}", "init": f"{len(init_rows)}/{len(init_rows)}"},
        "time_alignment": f"{aligned}/{len(main_rows) + len(init_rows)}",
        "lineage": {"main": lineage or "not declared for this packet", "main_sha256": lineage_sha,
                    "main_rows_equal": lineage_equal,
                    "init": f"prepared/initialization-counts.csv sha256 {files['initialization-counts.csv']}",
                    "per_row": "stream_id, source_begin_utc, source_end_utc, quality_status, aggregation_method "
                               "in expected-counts.csv"},
        "e1_before_lane_end_m": {ids.detector_alias(name): round(network.lanes[loop.lane][0] - loop.pos_m, 3)
                                 for name, loop in sorted(loops.items())},
        "uncounted": {"rule": "leave events without occupancy never count (nVehContrib)",
                      "early_departures": len(early),
                      "intended": sum(intended.get(e["vehicle_id"]) == e["detector_id"] for e in early)}}


def create_calibrate_goal(repository_root: Path, packet: str, case: str, *,
                          goal_id: str | None = None) -> CalibrateGoal:
    """Build the calibrate goal of one packet case from tracked machine fields; refuse if any resolve check fails."""
    reader = case_reader(repository_root, packet, case)
    resolution, records = _resolve(reader, packet)
    if not resolution["pass"]:
        failed = sorted(name for name, ok in resolution["checks"].items() if not ok)
        raise ValueError(f"goal-create refused: resolve checks failed: {failed}")
    run, request, scenario, passages = (records[key] for key in ("run", "request", "scenario", "passages"))
    score, _ = score_counts(reader.read_csv("prepared/expected-counts.csv"),
                            reader.read_csv("prepared/initialization-counts.csv"),
                            reader.read(f"{BASELINE_RUN_DIR}/field-counts.xml"))
    recorded = run["field_metrics"]
    if (score["mae"] != round(recorded["MAE"], 9) or score["max"] != recorded["max_abs_error"]
            or score["exact"] != run["exact_field_bins"] or score["rows_main"] != run["expected_field_bins"]):
        raise ValueError("goal-create refused: the recomputed baseline does not reproduce run.json field_metrics")
    if run["measurement_attribute"] != "nVehContrib" or passages.get("measurement_attribute") != "nVehContrib":
        raise ValueError("goal-create refused: the counted attribute is not nVehContrib")
    files = {row["name"]: row["sha256"] for row in resolution["aliases"]}
    observation = _observation(reader, resolution["packet"], files, passages)
    cells: dict[int, set[str]] = {}
    for row in json.loads(reader.read("prepared/lane-intentions.json"))["vehicles"]:
        cells.setdefault(row["od_cell"], set()).add(row["field_id"])
    od = scenario.get("OD_coordinates", {})
    local_only = reader.packet_root / "local-only-large-traces.json"
    instant = next((row for row in json.loads(local_only.read_text("utf-8"))["files"]
                    if row.get("relative") == reader.key(f"{BASELINE_RUN_DIR}/instant-e1.xml")),
                   None) if local_only.is_file() else None
    timeout = resolution["timeouts"]["timeout_source"]
    document = {
        "schema": SCHEMA, "goal_id": goal_id or default_goal_id(packet, case, observation["sim_zero_utc"]),
        "capability": "calibrate", "mode": "explore", "verifier": _DECLARED["verifier"],
        "inputs": {"packet": resolution["packet"], "case": case,
                   "resolver": resolution["resolver"] + "; tracked targets read through a direct .gz/.gz.gz fallback",
                   "files": files,
                   "baseline_run": {"dir": BASELINE_RUN_DIR, "files": list(BASELINE_RUN_FILES),
                                    "passages": PARENT_PASSAGES},
                   "parent_passages": {"path": PARENT_PASSAGES, "reconciled": passages["reconciled"],
                                       "inputs_unchanged": passages["inputs_unchanged"],
                                       "instant_e1": (f"local-only ({instant['bytes']} bytes, sha256 "
                                                      f"{instant['sha256']})") if instant else "not tracked"}},
        "run": {"backend": run["backend"], "sumo": run["stall_monitor"]["native_version"][1].split()[-1],
                "seed": run["seed"], "step_s": run["step_length_s"], "end_s": run["end_s"],
                "flags_template": FLAGS_TEMPLATE, "recorded_options": recorded_options(request["command"]),
                "lane_intentions": request.get("lane_intentions_file") is not None,
                "junction_body_hold": request.get("junction_body_hold") is True,
                "junction_body_hold_nodes": list(run["junction_body_hold_nodes"]),
                "all_stopped_timeout_s": request["all_stopped_timeout_s"],
                "timeout_s": resolution["timeouts"]["timeout_s"],
                "provenance": {
                    "seed, step_s, end_s, backend, junction_body_hold_nodes": f"{BASELINE_RUN_DIR}/run.json",
                    "sumo": f"{BASELINE_RUN_DIR}/run.json stall_monitor.native_version",
                    "recorded_options, lane_intentions, junction_body_hold, all_stopped_timeout_s":
                        f"{BASELINE_RUN_DIR}/native-request.json",
                    "timeout_s": f"{timeout['alias']} -> {timeout['target']}:{timeout['line']}"}},
        "observation": observation,
        "baseline": {"tae_main": score["tae_main"], "sse": score["sse"], "mae": score["mae"], "max": score["max"],
                     "exact": score["exact"], "init52_tae": score["init52_tae"], "all182_tae": score["all182_tae"],
                     "recorded": f"recomputed from {BASELINE_RUN_DIR}/field-counts.xml; equals run.json field_metrics"},
        "variable_class": {"name": "demand_timing", "ops": ["cell_depart_shift"],
                           "cells": {"od_pairs": od.get("pairs", 0), "periods": od.get("time_bins", 0),
                                     "cells": od.get("cells", 0), "non_empty": len(cells),
                                     "multi_field": sum(len(fields) > 1 for fields in cells.values())},
                           "preserve": ["request totals per field", "routes", "intentions", "vehicle ids"],
                           "frozen": ["network", "control", "detectors", "vtypes", "hold nodes"]},
        **{key: _DECLARED[key] for key in ("objective", "robustness", "gate_protocol", "stop", "claim_boundary")},
    }
    return CalibrateGoal.model_validate(document)


def write_goal(goal: CalibrateGoal, store: Path) -> tuple[Path, str]:
    """Write ``<store>/goals/<goal_id>/goal.json``; an existing goal with other bytes is refused."""
    path = Path(store) / "goals" / goal.goal_id / "goal.json"
    data = goal.to_json_bytes()
    if path.exists():
        if path.read_bytes() != data:
            raise FileExistsError(f"{path} exists with different content; a changed goal needs a new goal_id")
        return path, "unchanged"
    write_text_atomic(path, data.decode("utf-8"))
    return path, "created"


def load_goal(path: Path) -> tuple[CalibrateGoal, bytes]:
    data = Path(path).read_bytes()
    return CalibrateGoal.model_validate_json(data), data
