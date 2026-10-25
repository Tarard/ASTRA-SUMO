"""Twin goals ``astra.twin-goal/v1``: the Calibrate goal (M1) and the Build goal (M3).

Calibrate: the run block is copied from tracked machine fields (``run.json``, ``native-request.json`` and the
executed shared driver), never from prose. The baseline is recomputed from the tracked E1 output and must equal the
recorded one.

Build: goal-create scores the source net against the reference facts in explore mode and freezes the scored set with
each fact's kind, class and acceptance; escalated facts (COR, SRC, OPX, UNK) are excluded with their reason. The fact
table is stored by content hash next to the goal. GOAL LINT refuses the goal before anything is written.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import tempfile
import xml.etree.ElementTree as ET
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from ..core.artifact_io import write_text_atomic
from ..core.benchmark_references import (LOCAL_ONLY_FILE, BenchmarkReferenceError, PacketCase, csv_rows,
                                         find_tracked_file, local_only_row, safe_relative)
from ..core.candidate_contracts import file_sha256
from . import build_gate, build_profile, ids, invariants
from .count_profile import (INSTANT_E1, incomplete_rows, parse_field_loops, parse_intentions, parse_network,
                            read_passage_report, score_counts)
from .direction_check import RULE as DIRECTION_RULE, direction_baseline

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


def _not_a_device(value: str) -> str:
    if value.split(".")[0] in _WINDOWS_DEVICES:
        raise ValueError(f"goal_id {value!r} is a reserved Windows device name")
    return value


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
        return _not_a_device(value)

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
        baseline.append({"file": name, "present": True, "tracked": reader.tracked_name(found[0]),
                         "gzip_layers": found[1],
                         "manifest_match": reader.manifest_sha256(found[0]) == file_sha256(found[0])})
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
    try:
        instant = local_only_row(reader.resolve_root(LOCAL_ONLY_FILE).data,
                                 reader.key(f"{BASELINE_RUN_DIR}/{INSTANT_E1}"))
    except FileNotFoundError:
        instant = None
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


def write_goal(goal: CalibrateGoal | BuildGoal, store: Path) -> tuple[Path, str]:
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


# ---- Build goal (M3) ---------------------------------------------------------------------------------------------

DEFAULT_SEEDS = (17, 42, 104, 7, 23, 37, 59, 71, 83, 97)
DEFAULT_G0_SHAPE = {"max_vertex_delta": 8, "max_heading_change_deg": 35.0, "max_directed_hausdorff_m": 3.0,
                    "status": "declared, not calibrated"}
CATEGORICAL_KINDS = ("lane_count", "lane_permissions", "movement_inventory", "signal_group")
BUILD_CLAIM = "Model-relative known-answer or reference goal; explore only; no field claim."
REFERENCE_PRODUCER = Path(__file__).resolve().parents[1] / "core" / "reference_net_facts.py"


class _Aliased(_Strict):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class FileRef(_Strict):
    path: str
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class BuildSource(_Strict):
    net: str
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    lane_elements: int


class Sigma(_Strict):
    source_m: float = Field(ge=0)
    registration_m: float = Field(ge=0)
    comb_m: float = Field(ge=0)
    speed_m_s: float = Field(ge=0)
    status: str


class BuildReference(_Strict):
    facts: str = Field(pattern=r"^cas:sha256:[0-9a-f]{64}$")
    producer: str
    correspondence: str
    site_id: str
    target_date: str
    reference_net: FileRef | None = None
    sigma: Sigma
    lock: str | None = None


class ScoredFact(_Aliased):
    id: str
    kind: str
    class_: str = Field(alias="class")
    accept: str
    anchor: str
    tol_eff: float | None = None
    unit: str | None = None
    tol_floor: float | None = None
    stop_line_mode: Literal["longitudinal", "distance"] | None = None
    minimum_coverage: float | None = None
    maximum_mean_m: float | None = None
    minimum_iou: float | None = None


class ExcludedFact(_Aliased):
    kind: str
    class_: str = Field(alias="class")
    reason: str
    anchor: str
    source_status: str
    note: str
    probe: list[dict[str, Any]] | None = None


class SourceScorecard(_Strict):
    ref: str = Field(pattern=r"^cas:sha256:[0-9a-f]{64}$")
    scorer: str


class Frozen(_Strict):
    scored_facts: list[ScoredFact] = Field(min_length=1)
    excluded: dict[str, ExcludedFact]
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    counts: dict[str, int]
    source_scorecard: SourceScorecard


class IntBaseline(_Strict):
    basis: Literal["reference_net", "source"]
    net_sha256: str | None = None
    direction_rule: str
    direction_sha256: str | None = None
    junction_ids: list[str]
    finding_ids: list[str]
    measures: dict[str, float]


class BuildScope(_Strict):
    mutable_edge_ids: list[str]
    mutable_junction_ids: list[str]
    ops: list[str] = Field(min_length=1)
    max_ops_per_round: Literal[1]


class Materiality(_Strict):
    min_excess_decrease: float = Field(gt=0)
    max_worsening_tol_fraction: float = Field(gt=0)


class BuildObjective(_Strict):
    order: list[str]
    tie: Literal["reject"]
    materiality: Materiality
    rule: str
    excess: dict[str, str]


class G0Shape(_Strict):
    max_vertex_delta: int = Field(ge=0)
    max_heading_change_deg: float = Field(gt=0, le=180)
    max_directed_hausdorff_m: float = Field(gt=0)
    status: str


class TripRecord(_Strict):
    seed: int
    status: Literal["generated", "not_generated"]
    ref: str | None = Field(default=None, pattern=r"^cas:sha256:[0-9a-f]{64}$")
    sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    requests: int | None = None
    reason: str | None = None


class Routeability(_Strict):
    seeds: list[int]
    vehicle_count: int
    frozen_trips: str
    trips: list[TripRecord]
    hard: str
    dirty_source: str
    reported_only: list[str]


class Guards(_Strict):
    movement_probes: str
    routeability: Routeability
    lane_artifacts: list[FileRef]


class BuildGoal(_Strict):
    schema_id: Literal["astra.twin-goal/v1"] = Field(alias="schema")
    goal_id: str = Field(pattern=GOAL_ID_PATTERN)
    capability: Literal["build"]
    mode: Literal["explore"]
    source: BuildSource
    reference: BuildReference
    frozen: Frozen
    int_baseline: IntBaseline
    scope: BuildScope
    objective: BuildObjective
    g0_shape: G0Shape
    guards: Guards
    claim_boundary: str

    @field_validator("goal_id")
    @classmethod
    def _not_a_device_name(cls, value: str) -> str:
        return _not_a_device(value)

    def to_json_bytes(self) -> bytes:
        text = json.dumps(self.model_dump(mode="json", by_alias=True, exclude_none=True), indent=2,
                          ensure_ascii=False)
        return (text + "\n").encode("utf-8")


def _scope_elements(scope: dict) -> set[str]:
    return ({ids.element_id("edge", edge) for edge in scope.get("mutable_edge_ids") or []}
            | {ids.element_id("junction", node) for node in scope.get("mutable_junction_ids") or []})


def _in_scope(anchor: str, scope: dict) -> bool:
    if anchor in _scope_elements(scope):
        return True
    kind, _, value = anchor.partition(":")
    return kind == "lane" and value.rpartition("_")[0] in set(scope.get("mutable_edge_ids") or [])


def lint_build_goal(document: dict) -> list[str]:
    """GOAL LINT for a Build goal document; an empty list means the goal may be written."""
    errors = []
    if not isinstance(document.get("g0_shape"), dict):
        errors.append("a Build goal needs a g0_shape block")
    sigma = ((document.get("reference") or {}).get("sigma") or {})
    scored = (document.get("frozen") or {}).get("scored_facts") or []
    if not scored:
        errors.append("a Build goal needs at least one frozen scored fact")
    seen: set[str] = set()
    for entry in scored:
        fact, kind, cls = entry.get("id"), entry.get("kind"), entry.get("class")
        if fact in seen:
            errors.append(f"{fact}: duplicate scored fact")
        seen.add(fact)
        if cls not in build_profile.TARGET_CLASSES:
            errors.append(f"{fact}: frozen class {cls} is not a target (COR, SRC, OPX and UNK are never tasks)")
        if kind in build_profile.TOLERANCE_KINDS:
            tolerance, floor = entry.get("tol_eff"), build_profile.tolerance_floor(
                "m/s" if kind == "speed" else "m", sigma)
            if type(tolerance) not in (int, float) or not math.isfinite(tolerance) or tolerance <= 0:
                errors.append(f"{fact}: numeric target without a tolerance (tol_eff {tolerance})")
            elif tolerance < floor - 1e-12:
                errors.append(f"{fact}: tol_eff {tolerance:g} below max(2 sigma, representation step) {floor:g}")
            if kind == "lane_centerline" and not 0 < (entry.get("minimum_coverage") or 0) <= 1:
                errors.append(f"{fact}: lane_centerline needs a minimum_coverage in (0, 1]")
        elif kind == "junction_surface":
            minimum = entry.get("minimum_iou")
            if type(minimum) not in (int, float) or not 0 < minimum:
                errors.append(f"{fact}: junction_surface without a minimum_iou")
            elif minimum >= 1:
                errors.append(f"{fact}: junction_surface minimum_iou {minimum:g} >= 1 can never be met with excess")
        elif kind in CATEGORICAL_KINDS:
            if any(entry.get(key) is not None for key in ("tol_eff", "minimum_iou", "minimum_coverage",
                                                          "maximum_mean_m", "unit")):
                errors.append(f"{fact}: categorical target given a numeric tolerance")
        else:
            errors.append(f"{fact}: unknown fact kind {kind}")
    scope = document.get("scope") or {}
    if not _scope_elements(scope) or not scope.get("ops"):
        errors.append("scope with no target: it declares no mutable edge or junction, or no op")
    elif not any(_in_scope(str(entry.get("anchor")), scope) for entry in scored):
        errors.append("scope with no target: no frozen scored fact is anchored on a mutable edge, lane or junction")
    unknown_ops = sorted(set(scope.get("ops") or []) - set(_scoped_edit_types()))
    if unknown_ops:
        errors.append(f"scope ops are not typed edit ops: {unknown_ops}")
    return errors


def _scoped_edit_types() -> tuple[str, ...]:
    from ..core.network_edit_operations import SCOPED_EDIT_TYPES
    return SCOPED_EDIT_TYPES


def _stored_path(path: Path, repository_root: Path) -> str:
    resolved = Path(path).resolve()
    try:
        return resolved.relative_to(Path(repository_root).resolve()).as_posix()
    except ValueError:
        return str(resolved)


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:40] or "site"


TRIP_VEHICLES = 100
TRIP_SUFFIX = ".trips.xml"


def frozen_trips(net: Path, seeds: list[int], scratch_dir: Path | None = None) -> tuple[list[dict], dict[str, bytes]]:
    """Per seed, the routeability requests generated from ``net`` alone, exactly as verify-edit explore's source
    audit generates them (``core/routeability_audit.py``: source-reachable OD, 100 vehicles); returns the records and
    the trip bytes by cas reference. The files are written in a transient directory under ``scratch_dir``."""
    from ..core.routeability_audit import _write_source_reachable_trips, build_routeability_od_policy

    records, payloads = [], {}
    source = Path(net).resolve()
    if scratch_dir is not None:
        Path(scratch_dir).mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".twin-trips-", dir=scratch_dir) as scratch:
        try:
            policy, failure = build_routeability_od_policy([source]), None
            if not policy["source_edge_ids"] or not policy["destination_edge_ids"]:
                failure = "no_diagnostic_od_pool"
        except (OSError, ValueError, ET.ParseError) as error:
            policy, failure = None, f"od_policy_failed: {error}"
        for seed in seeds:
            if failure is not None:
                records.append({"seed": seed, "status": "not_generated", "reason": failure})
                continue
            trip_file = Path(scratch) / f"seed{seed}{TRIP_SUFFIX}"
            try:
                _write_source_reachable_trips(source, policy, trip_file, vehicle_count=TRIP_VEHICLES, seed=seed)
            except (RuntimeError, ValueError) as error:
                records.append({"seed": seed, "status": "not_generated", "reason": str(error)})
                continue
            data = trip_file.read_bytes()
            digest = hashlib.sha256(data).hexdigest()
            payloads[ids.cas_reference(digest)] = data
            records.append({"seed": seed, "status": "generated", "ref": ids.cas_reference(digest), "sha256": digest,
                            "requests": data.count(b"<trip ")})
    return records, payloads


def create_build_goal(repository_root: Path, *, net: Path, facts: Path | None = None,
                      reference_net: Path | None = None, site_id: str | None = None, target_date: str | None = None,
                      goal_id: str | None = None, mutable_edge_ids: list[str] | tuple[str, ...] = (),
                      mutable_junction_ids: list[str] | tuple[str, ...] = (), ops: list[str] | None = None,
                      seeds: list[int] | None = None, lane_artifacts: list[Path] | tuple[Path, ...] = (),
                      sigma_m: float = 0.0, g0_shape: dict | None = None,
                      scratch_dir: Path | None = None) -> tuple[BuildGoal, dict[str, bytes], dict]:
    """Freeze a Build goal; returns ``(goal, content-addressed payloads by cas reference, summary)``. The payloads
    are the fact table, the source scorecard and the per-seed frozen trips, all bound by the goal; paths in the goal
    are repository-relative when inside the repository, so equal inputs give equal goals. Lint failures refuse the
    goal; ``scratch_dir`` holds the transient trip generation (default: the system temp directory)."""
    from ..core.network_calibration import _network_root
    from ..core.reference_net_facts import build_reference_net_facts

    if facts is None and reference_net is None:
        raise ValueError("goal-create refused: a Build goal needs --facts or --reference-net")
    if type(sigma_m) not in (int, float) or not math.isfinite(sigma_m) or sigma_m < 0:
        raise ValueError("goal-create refused: sigma_m must be finite and nonnegative")
    net_bytes = Path(net).read_bytes()
    root = _network_root(net_bytes)
    reference_bytes = Path(reference_net).read_bytes() if reference_net is not None else None
    if facts is not None:
        facts_bytes = Path(facts).read_bytes()
        producer = "supplied fact table"
    else:
        if not target_date:
            raise ValueError("goal-create refused: --reference-net needs --target-date")
        table_dict, _ = build_reference_net_facts(reference_net, site_id=site_id or Path(reference_net).stem,
                                                  target_date=target_date,
                                                  source_path=_stored_path(reference_net, repository_root))
        facts_bytes = (json.dumps(table_dict, indent=2, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8")
        producer = f"core.reference_net_facts@{hashlib.sha256(REFERENCE_PRODUCER.read_bytes()).hexdigest()[:12]}"
    table = build_profile.parse_table(facts_bytes)
    sigma = {"source_m": float(sigma_m), "registration_m": 0.0, "comb_m": float(sigma_m), "speed_m_s": 0.0,
             "status": "declared" if sigma_m else "zero: model truth or undeclared (explore only)"}
    card = build_profile.score_root(root, table)
    scored, excluded = build_profile.freeze_scored_set(table, card, root, sigma=sigma)
    facts_sha = hashlib.sha256(facts_bytes).hexdigest()
    scorecard = build_profile.scorecard_cache(card, net_sha256=hashlib.sha256(net_bytes).hexdigest(),
                                              facts_sha256=facts_sha)
    seed_list = list(DEFAULT_SEEDS if seeds is None else seeds)
    edges, junctions = sorted(set(mutable_edge_ids)), sorted(set(mutable_junction_ids))
    # INT rows are differential: against the clean reference net when one is given, else the source itself.
    basis_bytes = reference_bytes if reference_bytes is not None else net_bytes
    basis_root = _network_root(basis_bytes)
    scope_nodes = build_profile.scope_junctions(basis_root, edges, junctions)
    found, direction = build_profile.int_findings(basis_root, scope_nodes)
    basis_sha = hashlib.sha256(basis_bytes).hexdigest()
    baseline = {"basis": "reference_net" if reference_bytes is not None else "source", "net_sha256": basis_sha,
                "direction_rule": DIRECTION_RULE,
                "direction_sha256": direction_baseline(direction, basis_sha)["sha256"],
                "junction_ids": scope_nodes, "finding_ids": [row["id"] for row in found],
                "measures": {row["id"]: row["measure"] for row in found if row["measure"] is not None}}
    mappings = sorted({(fact.mapping_status, fact.mapping_confidence) for fact in table.facts})
    counts = {**{f"scored_{cls}": sum(row["class"] == cls for row in scored) for cls in build_profile.TARGET_CLASSES},
              **{f"excluded_{cls}": n for cls, n in sorted(Counter(row["class"] for row in excluded.values()).items())}}
    document = {
        "schema": SCHEMA, "goal_id": "pending", "capability": "build", "mode": "explore",
        "source": {"net": _stored_path(net, repository_root), "sha256": hashlib.sha256(net_bytes).hexdigest(),
                   "lane_elements": sum(1 for _ in root.iter("lane"))},
        "reference": {"facts": ids.cas_reference(facts_sha), "producer": producer,
                      "correspondence": ("same ids, confirmed" if mappings == [("confirmed", "confirmed")] else
                                         "; ".join(f"{status}/{confidence}" for status, confidence in mappings)),
                      "site_id": table.site_id, "target_date": table.target_date.isoformat(),
                      **({"reference_net": {"path": _stored_path(reference_net, repository_root),
                                            "sha256": hashlib.sha256(reference_bytes).hexdigest()}}
                         if reference_bytes is not None else {}),
                      "sigma": sigma},
        "frozen": {"scored_facts": scored, "excluded": excluded,
                   "sha256": build_profile.frozen_sha256(scored, excluded), "counts": counts,
                   "source_scorecard": {"ref": ids.cas_reference(hashlib.sha256(scorecard).hexdigest()),
                                        "scorer": build_profile.scorer_fingerprint()}},
        "int_baseline": baseline,
        "scope": {"mutable_edge_ids": edges, "mutable_junction_ids": junctions,
                  "ops": sorted(set(ops)) if ops else list(_scoped_edit_types()), "max_ops_per_round": 1},
        "objective": {"order": list(build_gate.ORDER), "tie": "reject", "materiality": dict(build_gate.MATERIALITY),
                      "rule": build_gate.V_RULE, "excess": dict(build_gate.EXCESS_RULE)},
        "g0_shape": dict(DEFAULT_G0_SHAPE if g0_shape is None else g0_shape),
        "guards": {"movement_probes": "manifest_if_supported_else_all_turn_smoke (L2, not run by the M3 profiler)",
                   "routeability": {
                       "seeds": seed_list, "vehicle_count": TRIP_VEHICLES,
                       "frozen_trips": "per seed, generated from the goal source alone at goal-create; every judged "
                                       "seed must run exactly these requests",
                       "trips": [],
                       "hard": "on every seed, the candidate's failing requests must be a subset of the source's",
                       "dirty_source": "a subset on a dirty source passes the screen but is not promotable without "
                                       "a human; collisions and teleports without per-request identities never pass",
                       "reported_only": ["travel time", "arrival time"]},
                   "lane_artifacts": [{"path": _stored_path(path, repository_root), "sha256": file_sha256(Path(path))}
                                      for path in lane_artifacts]},
        "claim_boundary": BUILD_CLAIM}
    errors = lint_build_goal(document)
    if errors:
        raise ValueError(f"goal-create refused: lint failed ({len(errors)}): " + "; ".join(errors[:5]))
    trips, trip_payloads = frozen_trips(net, seed_list, scratch_dir)
    document["guards"]["routeability"]["trips"] = trips
    content = json.dumps({key: value for key, value in document.items() if key != "goal_id"}, sort_keys=True)
    document["goal_id"] = goal_id or f"build-{_slug(table.site_id)}-{ids.short_hash(content)}"
    goal = BuildGoal.model_validate(document)
    summary = {"scored": len(scored), "counts": counts, "source_V": _source_v(scored, card),
               "int_baseline": {"basis": baseline["basis"], "findings": len(baseline["finding_ids"])},
               "frozen_trips": {str(row["seed"]): row["status"] for row in trips}}
    payloads = {goal.reference.facts: facts_bytes, goal.frozen.source_scorecard.ref: scorecard, **trip_payloads}
    return goal, payloads, summary


def _source_v(scored: list[dict], card: dict) -> list:
    by_id = {row["id"]: row for row in card["facts"]}
    return build_gate.v_vector({entry["id"]: build_profile.fact_row(entry, by_id[entry["id"]]) for entry in scored})


def cas_path(store: Path, reference: str, suffix: str = ".json") -> Path:
    return Path(store) / "cas" / "sha256" / f"{reference.removeprefix('cas:sha256:')}{suffix}"


def _write_cas(path: Path, data: bytes) -> None:
    """Write one content-addressed file atomically; an existing file is kept only when its bytes still hash to its
    name, and rewritten otherwise (an interrupted or tampered entry is repaired, never trusted)."""
    if path.exists() and hashlib.sha256(path.read_bytes()).hexdigest() == path.name.split(".", 1)[0]:
        return
    # write_text_atomic opens with newline="", so the UTF-8 bytes round-trip unchanged.
    write_text_atomic(path, data.decode("utf-8"))


def write_build_goal(goal: BuildGoal, payloads: dict[str, bytes], store: Path) -> tuple[Path, str, Path]:
    """Store every payload the goal binds (fact table, source scorecard, frozen trips) by content hash, then write
    the goal like any other goal; returns ``(goal path, created|unchanged, fact table path)``."""
    trips = {row.ref for row in goal.guards.routeability.trips if row.ref}
    required = {goal.reference.facts, goal.frozen.source_scorecard.ref} | trips
    if required - set(payloads):
        raise ValueError(f"goal-create refused: missing content for {sorted(required - set(payloads))}")
    for reference, data in sorted(payloads.items()):
        if ids.cas_reference(hashlib.sha256(data).hexdigest()) != reference:
            raise ValueError(f"goal-create refused: content does not match its cas reference {reference}")
        _write_cas(cas_path(store, reference, TRIP_SUFFIX if reference in trips else ".json"), data)
    path, written = write_goal(goal, store)
    return path, written, cas_path(store, goal.reference.facts)


def load_build_goal(path: Path) -> tuple[BuildGoal, bytes, bytes, Path]:
    """(goal, goal bytes, fact table bytes, fact table path) of ``<store>/goals/<goal_id>/goal.json``."""
    data = Path(path).read_bytes()
    goal = BuildGoal.model_validate_json(data)
    errors = lint_build_goal(json.loads(data))
    if errors:
        raise ValueError(f"profile refused: the goal fails lint: {'; '.join(errors[:3])}")
    facts_file = cas_path(Path(path).resolve().parents[2], goal.reference.facts)
    facts_bytes = facts_file.read_bytes()
    if ids.cas_reference(hashlib.sha256(facts_bytes).hexdigest()) != goal.reference.facts:
        raise ValueError(f"profile refused: {facts_file} does not match the goal's cas reference")
    return goal, data, facts_bytes, facts_file
