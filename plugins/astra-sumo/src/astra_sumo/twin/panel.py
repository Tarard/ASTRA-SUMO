"""Cached M2 panel and stage draws (I/O): judged runs of a pilot store turned into the inputs of the v3 statistics.

Thin by design. For every labelled ledger run it reads judge.json, the canonical measurement.json.gz and the native
outputs it needs, binds them to the judge's own digests and the ledger row, and calls the pure readers:
``runner.tripinfo_departures`` and ``guards.entry_p95`` for the entry guards, ``count_profile.profile_core`` for the
per-draw hotspots and field balance, ``ensemble.field_totals`` for the totals. The store is only read and no SUMO run
starts. Paths never enter the panel; run ids, request and judge hashes do.
"""

from __future__ import annotations

import gzip
import hashlib
import json
from collections.abc import Mapping
from pathlib import Path, PureWindowsPath

from ..core.benchmark_references import csv_rows
from . import corridor_screen, count_profile, ensemble, goal, guards, m2_stage, perturb, runner, scenario_ops

PANEL_SCHEMA = "astra.twin-m2-panel/v1"
REFERENCE_ARM = "P"
PROFILE_ARMS = ("P",)
DELTA_DECIMALS = 1
PASSAGE_FIELDS = ("vehicle_id", "detector_id", "interval_begin_s", "time_s", "notification_time_s", "occupancy_s")

# The B0 cached panel: the parent on depart_jitter keys k1..k6 and the three stage-3 ops on the same keys.
B0_LABELS = {**{f"P:k{k}": f"P3-jitter-k{k}-s104" for k in range(1, 5)},
             **{f"P:k{k}": f"s3-parent-k{k}" for k in (5, 6)},
             **{f"{arm}:k{k}": f"s3-{arm}-k{k}" for arm in ("L", "M", "N") for k in range(1, 7)}}
B0_NOMINAL = "P1-nominal-s104"


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def read_ledger(path: Path) -> dict[str, dict]:
    """Ledger rows by id; a repeated id must name the same request (a re-measure), and the last row is kept."""
    rows: dict[str, dict] = {}
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if row["id"] in rows and rows[row["id"]]["request_sha256"] != row["request_sha256"]:
            raise ValueError(f"Ledger id {row['id']} names two different requests")
        rows[row["id"]] = row
    return rows


def _split_label(label: str) -> tuple[str, str]:
    arm, sep, key = label.partition(":")
    if not sep or not arm or not key:
        raise ValueError(f"Panel labels are '<arm>:<draw key>', got {label!r}")
    return arm, key


def _run(store: Path, row: dict, *, healthy_only: bool = True) -> dict:
    """One ledger run's judge, canonical measurement and native directory, bound to the ledger row.

    The panel reads healthy runs only. A stage (``healthy_only=False``) also reads a run that completed unhealthy
    (status ``invalid``) when every bin is scored, because unhealthy draws stay in the ensemble (A2.2); a run without
    a complete score crashed or timed out and is replaced by a spare, never read.
    """
    if healthy_only and (not row.get("healthy") or row.get("status") != "measured"):
        raise ValueError(f"Run {row['id']} is not a healthy measured run")
    if row.get("status") not in ("measured", "invalid"):
        raise ValueError(f"Run {row['id']} has status {row.get('status')}: a crashed or timed-out run is replaced by "
                         "a spare, never scored")
    run_dir = store / "runs" / PureWindowsPath(row["run_dir"]).name
    judge_bytes = (run_dir / "judge.json").read_bytes()
    judge = json.loads(judge_bytes)
    if (judge.get("label"), judge.get("request_sha256")) != (row["id"], row["request_sha256"]):
        raise ValueError(f"{run_dir} judges {judge.get('label')}, not ledger run {row['id']}")
    if healthy_only and (not judge.get("healthy") or judge.get("status") != "measured"):
        raise ValueError(f"Judge {row['id']} is not a healthy scored run")
    if not judge.get("rows") or (judge.get("status"), judge.get("healthy")) != (row["status"], row.get("healthy")):
        raise ValueError(f"Judge {row['id']} has no complete score or disagrees with its ledger row")
    measurement = json.loads(gzip.decompress((run_dir / "measurement.json.gz").read_bytes()))
    if runner.digest(measurement["passages"]["full"]) != judge["digests"]["passage_full"]:
        raise ValueError(f"{row['id']}: measurement passages differ from the judge's digest")
    return {"row": row, "dir": run_dir, "judge": judge, "judge_sha256": sha256(judge_bytes),
            "measurement": measurement}


def _native(run: dict, name: str) -> bytes:
    """A native output of the run, bound to the judge's canonical digest (the demand to the ledger's sha256)."""
    data = (run["dir"] / "native" / name).read_bytes()
    if name == count_profile.E1_OUTPUT:
        found = runner.digest([sorted(r.items()) for r in runner.e1_rows(data)])
        expected = run["judge"]["digests"]["e1_all_attributes"]
    elif name == "demand.rou.xml":
        found, expected = sha256(data), run["row"]["inputs_sha256"]
    else:
        found, expected = runner.trace_digest(name, data)["sha256"], run["judge"]["digests"][f"trace:{name}"]
    if found != expected:
        raise ValueError(f"{run['row']['id']}: native {name} differs from the judged run")
    return data


def guard_stats(run: dict) -> dict:
    """Entry p95 per edge and network-wide, emergency brakings, post-window passages and init52 of one run."""
    braking = run["judge"]["health"]["emergency_braking"]
    if braking["summary_count"] is None and braking["warnings"]:
        raise ValueError(f"{run['row']['id']}: braking warnings without a SUMO braking count")
    return {"entry_p95": guards.entry_p95(runner.tripinfo_departures(_native(run, "tripinfo.xml"))),
            "brakings": int(braking["summary_count"] or 0),
            "post_window": sum(row[2] >= ensemble.MAIN_END_S for row in run["measurement"]["passages"]["full"]),
            "init52": run["judge"]["score"]["init52_tae"]}


def stage_draw(run: dict) -> dict:
    """One judged run as a ``m2_stage.evaluate_stage`` draw: every scored row, health, guard stats and the run's
    draw key (fingerprint, publicity and derivation, as the judge and the ledger record them), binding (goal, scenario
    op, base demand, family, seed) and identity (the judge's measurement code and the request's runtime hashes)."""
    request = json.loads((run["dir"] / "request.json").read_bytes())
    if runner.digest(request) != run["row"]["request_sha256"]:
        raise ValueError(f"{run['row']['id']}: request.json differs from the ledger's request sha256")
    judge, perturbation = run["judge"], request.get("perturbation") or {}
    key = ((judge["provenance"].get("perturbation") or {}).get("key")) or {}
    ledger_key = run["row"].get("draw_key") or {}
    if {name: key.get(name) for name in ("key_sha256", "public", "derivation")} != {
            name: ledger_key.get(name) for name in ("key_sha256", "public", "derivation")} or (
            key.get("key_sha256") != perturbation.get("key_sha256")):
        raise ValueError(f"{run['row']['id']}: the judge, the request and the ledger record different draw keys")
    runtime = request.get("runtime") or {}
    return {"counts": {row: value["measured"] for row, value in sorted(judge["rows"].items())},
            "health": judge["health"], "guard_stats": guard_stats(run),
            "run": {"id": run["row"]["id"], "request_sha256": run["row"]["request_sha256"],
                    "judge_sha256": run["judge_sha256"], "key_sha256": perturbation.get("key_sha256"),
                    "key_label": key.get("label"), "key_public": key.get("public"),
                    "key_derivation": key.get("derivation"),
                    "binding": {"goal_sha256": judge["provenance"]["goal_sha256"],
                                "scenario_op": request.get("scenario_op"),
                                "base_demand_sha256": request.get("base_demand_sha256"),
                                "family": perturbation.get("family"), "seed": request["arguments"]["seed"]},
                    "identity": {"measurement_code_sha256": judge.get("measurement_code_sha256"),
                                 "runtime_sha256": runner.digest({name: value for name, value in runtime.items()
                                                                  if name != "verifier_commit"}),
                                 "verifier_commit": runtime.get("verifier_commit"),
                                 "verifier_clean": judge["provenance"].get("verifier_clean"),
                                 "plan_measurement_code_sha256": judge["provenance"].get("measurement_code_sha256")}}}


def read_ledger_rows(path: Path) -> list[dict]:
    """Every ledger row in file order, repeated ids included (``read_ledger`` keeps the last row per id)."""
    return [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip()]


def ledger_key_runs(store: Path, rows: list[dict]) -> list[dict]:
    """Every ledger row with a draw key as ``m2_stage.key_use_step`` reads it: id, request, key fingerprint, outcome
    (``m2_stage.ledger_outcome``; an unscored run's judge health tells a crash or timeout from a gridlock) and the
    ledger's part of the binding."""
    found = []
    for row in rows:
        key = row.get("draw_key") or {}
        if not key.get("key_sha256"):
            continue
        health = None
        if row.get("tae_main") is None and row.get("status") != "failed":
            path = Path(store) / "runs" / PureWindowsPath(row["run_dir"]).name / "judge.json"
            if path.is_file():
                judge = json.loads(path.read_bytes())
                if (judge.get("label"), judge.get("request_sha256")) == (row["id"], row["request_sha256"]):
                    health = judge.get("health")
        found.append({"id": row["id"], "request_sha256": row["request_sha256"], "key_sha256": key["key_sha256"],
                      "public": key.get("public"), "outcome": m2_stage.ledger_outcome(row, health),
                      "binding": {"scenario_op": row.get("scenario_op"), "seed": row.get("seed"),
                                  "family": key.get("family")}})
    return found


def read_stage_draws(ledger_path: Path, labels: Mapping[str, str]) -> dict:
    """``stage_draw`` of every labelled ledger run (healthy or scored unhealthy), plus the real counts they share.

    Labels are free-form (e.g. ``candidate:<key>``); one ledger run under two labels is refused. The real counts are
    each run's measured - diff and must agree across runs, and every run must be judged against one goal.
    ``ledger_runs`` lists every ledger row with a draw key (``ledger_key_runs``), so a confirmation can check each
    committed key's use. The store is only read.
    """
    if not labels:
        raise ValueError("No labelled runs")
    by_run: dict[str, list[str]] = {}
    for label, run_id in sorted(labels.items()):
        by_run.setdefault(run_id, []).append(label)
    repeated = {run_id: found for run_id, found in sorted(by_run.items()) if len(found) > 1}
    if repeated:
        raise ValueError(f"One ledger run under several labels: {repeated}")
    ledger_path = Path(ledger_path)
    ledger_bytes = ledger_path.read_bytes()
    ledger = read_ledger(ledger_path)
    missing = sorted(run_id for run_id in labels.values() if run_id not in ledger)
    if missing:
        raise ValueError(f"Ledger runs not found: {missing}")
    draws, real, goals = {}, None, set()
    for label, run_id in sorted(labels.items()):
        run = _run(ledger_path.parent, ledger[run_id], healthy_only=False)
        found = {row: value["measured"] - value["diff"] for row, value in sorted(run["judge"]["rows"].items())}
        real = found if real is None else real
        if found != real:
            raise ValueError(f"The real counts differ in run {run_id}")
        goals.add((run["judge"]["provenance"]["goal"], run["judge"]["provenance"]["goal_sha256"]))
        draws[label] = stage_draw(run)
    if len(goals) != 1:
        raise ValueError("The labelled runs were judged against different goals")
    (goal_id, goal_sha256), = goals
    return {"draws": draws, "real": real,
            "ledger_runs": ledger_key_runs(ledger_path.parent, read_ledger_rows(ledger_path)),
            "provenance": {"ledger_sha256": sha256(ledger_bytes), "goal": goal_id, "goal_sha256": goal_sha256,
                           "runs": {label: {key: draw["run"][key] for key in ("id", "request_sha256", "judge_sha256",
                                                                               "key_sha256", "key_public")}
                                    for label, draw in draws.items()}}}


def _compact_hotspot(hotspot: dict) -> dict:
    compact = {key: hotspot[key] for key in ("id", "kind", "class", "weight")}
    if hotspot["kind"] == "BAL":
        compact["rows"] = [{"field": hotspot["rows"][0]["field"]}]
    else:
        compact["lead"] = {"row_id": hotspot["lead"]["row_id"],
                           "coanchors": [{"element": c["element"]} for c in hotspot["lead"]["coanchors"]]}
    return compact


def _static(store: Path, goal_id: str, goal_sha256: str, repository_root: Path) -> dict:
    """The goal's prepared inputs through its packet references, each checked against the goal's sha256."""
    path = store / "goals" / goal_id / "goal.json"
    loaded, goal_bytes = goal.load_goal(path)
    if sha256(goal_bytes) != goal_sha256:
        raise ValueError(f"{path} differs from the goal the runs were judged against")
    reader = goal.case_reader(repository_root, loaded.inputs.packet, loaded.inputs.case)
    data = {name: reader.read(f"prepared/{name}") for name in loaded.inputs.files}
    for name, expected in loaded.inputs.files.items():
        if sha256(data[name]) != expected:
            raise ValueError(f"prepared {name} differs from the goal")
    return {"network": count_profile.parse_network(data["network.net.xml"], data["control.add.xml"]),
            "loops": count_profile.parse_field_loops(data["field-loops.add.xml"]),
            "intended": count_profile.parse_intentions(data["lane-intentions.json"]),
            "main_rows": csv_rows(data["expected-counts.csv"]), "init_rows": csv_rows(data["initialization-counts.csv"]),
            "hold_nodes": list(loaded.run.junction_body_hold_nodes), "e1_begin_s": loaded.observation.e1_begin_s,
            "e1_period_s": loaded.observation.e1_period_s}


def profile_summary(run: dict, static: dict) -> dict:
    """``profile_core`` on one run (its own staged demand, vehicle routes, E1 counts and passages), compacted."""
    passages = [dict(zip(PASSAGE_FIELDS, row)) for row in run["measurement"]["passages"]["full"]]
    core = count_profile.profile_core(
        **static, requests=count_profile.parse_demand(_native(run, "demand.rou.xml")),
        routes=count_profile.parse_vehicle_routes(_native(run, "vehicle-routes.xml")),
        field_counts=_native(run, count_profile.E1_OUTPUT), passages=passages)
    if core["score"]["tae_main"] != run["judge"]["score"]["tae_main"]:
        raise ValueError(f"{run['row']['id']}: the profiled score differs from the judge's")
    return {"tae_main": core["score"]["tae_main"], "init52_tae": core["score"]["init52_tae"],
            "hotspots": [_compact_hotspot(h) for h in core["hotspots"]],
            "field_balance": [{key: row[key] for key in ("field", "r_main", "r_init", "post_window", "deficit",
                                                          "m_main")} for row in core["balance_rows"]]}


def _deltas(stats: dict[str, dict], reference: dict[str, dict]) -> dict[str, list[float]]:
    keys = sorted(stats)
    names = ["network_p95", *(f"entry_p95:{edge}" for edge in sorted(reference[keys[0]]["entry_p95"])
                              if edge != guards.NETWORK), "brakings", "post_window", "init52"]

    def value(row: dict, name: str) -> float:
        if name == "network_p95":
            return row["entry_p95"][guards.NETWORK]
        if name.startswith("entry_p95:"):
            return row["entry_p95"][name.removeprefix("entry_p95:")]
        return row[name]

    return {name: [round(value(stats[k], name) - value(reference[k], name), DELTA_DECIMALS) for k in keys]
            for name in names}


def read_panel(ledger_path: Path, labels: Mapping[str, str], *, repository_root: Path,
               nominal: str | None = None, profile_arms: tuple[str, ...] = PROFILE_ARMS) -> dict:
    """The panel of ``labels`` (``"<arm>:<draw key>"`` -> ledger id) from the store that holds ``ledger_path``.

    Returns the main rows, the real target (measured - diff, identical in every run), the main-row counts per arm
    and key (each run's TAE_main checked against the ledger and the judge), the guard inputs per run, the
    op-vs-reference paired guard deltas (rounded to 0.1), per-draw field totals, and the compact ``profile_core``
    summary of every draw of ``profile_arms`` and of the ``nominal`` run (reported, never part of a statistic).
    """
    ledger_path = Path(ledger_path)
    store, ledger_bytes = ledger_path.parent, ledger_path.read_bytes()
    ledger = read_ledger(ledger_path)
    wanted = dict(labels)
    if nominal is not None:
        wanted["nominal"] = nominal
    missing = sorted(run_id for run_id in wanted.values() if run_id not in ledger)
    if missing:
        raise ValueError(f"Ledger runs not found: {missing}")
    runs = {label: _run(store, ledger[run_id]) for label, run_id in wanted.items()}
    goals = {(run["judge"]["provenance"]["goal"], run["judge"]["provenance"]["goal_sha256"]) for run in runs.values()}
    if len(goals) != 1:
        raise ValueError("The labelled runs were judged against different goals")
    (goal_id, goal_sha256), = goals
    target, rows = None, None
    arms: dict[str, dict[str, dict[str, int]]] = {}
    stats: dict[str, dict[str, dict]] = {}
    for label in sorted(labels):
        arm, key = _split_label(label)
        run = runs[label]
        judged = run["judge"]["rows"]
        found = {row: value["measured"] - value["diff"] for row, value in judged.items()}
        if target is None:
            target, rows = found, ensemble.main_rows(list(found))
        if found != target:
            raise ValueError(f"The target differs in run {run['row']['id']}")
        counts = {row: judged[row]["measured"] for row in rows}
        tae = sum(abs(counts[row] - target[row]) for row in rows)
        if not tae == run["row"]["tae_main"] == run["judge"]["score"]["tae_main"]:
            raise ValueError(f"Run {run['row']['id']}: TAE_main {tae} differs from the ledger or the judge")
        arms.setdefault(arm, {})[key] = counts
        stats.setdefault(arm, {})[key] = guard_stats(run)
    if target is None:
        raise ValueError("No labelled runs")
    if REFERENCE_ARM not in arms:
        raise ValueError(f"The panel needs the reference arm {REFERENCE_ARM}")
    unpaired = sorted(arm for arm in arms if set(arms[arm]) != set(arms[REFERENCE_ARM]))
    if unpaired:
        raise ValueError(f"Arms {unpaired} do not share the reference arm's draw keys")
    static = _static(store, goal_id, goal_sha256, Path(repository_root))
    profiles = {arm: {key: profile_summary(runs[f"{arm}:{key}"], static) for key in sorted(arms[arm])}
                for arm in profile_arms}
    panel = {"schema": PANEL_SCHEMA, "main_rows": rows, "target": dict(sorted(target.items())), "arms": arms,
             "field_totals": {arm: {key: ensemble.field_totals(counts, rows) for key, counts in by_key.items()}
                              for arm, by_key in arms.items()},
             "guard_inputs": stats,
             "op_deltas": {arm: _deltas(stats[arm], stats[REFERENCE_ARM]) for arm in sorted(arms)
                           if arm != REFERENCE_ARM},
             "profiles": profiles,
             "nominal": None if nominal is None else {"run_id": nominal,
                                                      **profile_summary(runs["nominal"], static)},
             "provenance": {"ledger_sha256": sha256(ledger_bytes), "goal": goal_id, "goal_sha256": goal_sha256,
                            "runs": {label: {"id": run["row"]["id"], "request_sha256": run["row"]["request_sha256"],
                                             "judge_sha256": run["judge_sha256"]}
                                     for label, run in sorted(runs.items())}},
             "claim": "Explore only: cached simulated runs under the supplied model; panel keys are reused, so any "
                      "statistic on them is a screening or negative-control value with no rate."}
    return panel


def read_queue_draws(ledger_path: Path, labels: Mapping[str, str]) -> dict:
    """``corridor_screen.QueueDraw`` of every labelled ledger run (healthy or scored unhealthy, A2.3): main-row
    counts, tripinfo insertions (bound to the judge's trace digest) and canonical full passages, plus the real counts
    on every row. Labels are draw keys; the store is only read."""
    ledger_path = Path(ledger_path)
    ledger = read_ledger(ledger_path)
    missing = sorted(run_id for run_id in labels.values() if run_id not in ledger)
    if missing:
        raise ValueError(f"Ledger runs not found: {missing}")
    draws, real, provenance = [], None, {}
    for key, run_id in sorted(labels.items()):
        run = _run(ledger_path.parent, ledger[run_id], healthy_only=False)
        found = {row: value["measured"] - value["diff"] for row, value in sorted(run["judge"]["rows"].items())}
        real = found if real is None else real
        if found != real:
            raise ValueError(f"The real counts differ in run {run_id}")
        counts = {row: run["judge"]["rows"][row]["measured"] for row in ensemble.main_rows(list(found))}
        draws.append(corridor_screen.queue_draw(key, counts, _native(run, "tripinfo.xml"),
                                                run["measurement"]["passages"]["full"]))
        provenance[key] = {"id": run_id, "request_sha256": run["row"]["request_sha256"],
                           "judge_sha256": run["judge_sha256"], "healthy": run["row"].get("healthy")}
    return {"draws": draws, "real": real, "provenance": {"ledger_sha256": sha256(ledger_path.read_bytes()),
                                                        "runs": provenance}}


def read_demand_schedule(goal_path: Path, repository_root: Path) -> dict:
    """The goal's demand schedule for a cell screen: per vehicle the nominal depart, free-flow arrival a_v
    (``count_profile.free_flow_arrivals``) and intended field, the cell table and members, and the run horizon."""
    loaded, goal_bytes = goal.load_goal(Path(goal_path))
    reader = goal.case_reader(repository_root, loaded.inputs.packet, loaded.inputs.case)
    data = {name: reader.read(f"prepared/{name}") for name in goal.PREPARED_FILES}
    wrong = [name for name, value in data.items() if sha256(value) != loaded.inputs.files[name]]
    if wrong:
        raise ValueError(f"Prepared inputs differ from the goal: {wrong}")
    network = count_profile.parse_network(data["network.net.xml"], data["control.add.xml"])
    requests = count_profile.parse_demand(data["demand.rou.xml"])
    intended = count_profile.parse_intentions(data["lane-intentions.json"])
    arrivals = count_profile.free_flow_arrivals(network, requests, intended,
                                                count_profile.parse_field_loops(data["field-loops.add.xml"]))
    demand = perturb.parse_demand(data["demand.rou.xml"])
    cells = loaded.variable_class.cells
    table = scenario_ops.cell_table(demand, data["lane-intentions.json"], od_pairs=cells["od_pairs"],
                                    periods=cells["periods"])
    members: dict[int, list[str]] = {}
    for vehicle, cell in sorted(scenario_ops.od_cells(data["lane-intentions.json"]).items()):
        members.setdefault(cell, []).append(vehicle)
    return {"goal": loaded.goal_id, "goal_sha256": sha256(goal_bytes), "horizon_s": loaded.run.end_s,
            "nominal_departs": {v.vehicle_id: float(v.depart) for v in demand.vehicles},
            "arrivals": arrivals, "intended": intended, "cells": table, "members": members}


def write_panel_fixture(panel: dict, path: Path, *, code_commit: str | None) -> dict:
    """Write the panel as canonical JSON (``runner.canonical_json``) plus a final newline to a NEW file.

    The provenance gains the code commit that read it; the ledger sha256, run ids, request and judge.json sha256s
    are already in the panel. An existing file is refused.
    """
    path = Path(path)
    if path.exists():
        raise FileExistsError(f"{path} exists; write the fixture to a new path")
    data = runner.canonical_json({**panel, "provenance": {**panel["provenance"], "code_commit": code_commit}}) + b"\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return {"path": str(path), "bytes": len(data), "sha256": sha256(data)}
