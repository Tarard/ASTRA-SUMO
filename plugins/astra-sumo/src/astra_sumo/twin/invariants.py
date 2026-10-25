"""Calibrate invariants, part 1 (M1): INV-INPUTS, INV-OBS, INV-HOLD and INV-PASS.

Each check returns ``{"id", "pass", "witness_ids", "reason"}``; the witness ids name what violates the rule and the
reason says what was compared.
"""

from __future__ import annotations

import math
from collections import Counter
from pathlib import PureWindowsPath
from typing import TYPE_CHECKING

from . import ids

if TYPE_CHECKING:
    from .count_profile import FieldLoop, Route

TIME_TOLERANCE_S = 1e-6


def _result(invariant: str, witnesses: list[str], reason: str) -> dict:
    return {"id": invariant, "pass": not witnesses, "witness_ids": sorted(set(witnesses)), "reason": reason}


def inv_inputs(expected_sha256: dict[str, str], resolved_sha256: dict[str, str | None],
               run_files: dict[str, bool] | None = None, committed: dict[str, bool] | None = None) -> dict:
    """Every goal input alias resolves to the raw SHA-256 the goal froze, and every run artifact read is pinned.

    ``run_files`` maps each baseline-run artifact the profile reads to whether its bytes are pinned by the packet
    (``PacketCase.pinned``: the manifest SHA-256 of a tracked file, or the declared raw SHA-256 of an alias); an
    artifact that cannot be read or decoded is unpinned. The manifest is itself a packet file, so ``committed`` maps
    the repository path of each artifact read to whether its tracked bytes equal the blob committed at HEAD
    (``PacketCase.committed``): a coherent edit of an artifact and its manifest row fails here.
    """
    run_files, committed = run_files or {}, committed or {}
    changed = [name for name, digest in expected_sha256.items() if resolved_sha256.get(name) != digest]
    unpinned = [f"manifest:{name}" for name, pinned in run_files.items() if not pinned]
    uncommitted = [f"git:{name}" for name, equal in committed.items() if not equal]
    return _result("INV-INPUTS", changed + unpinned + uncommitted,
                   f"{len(expected_sha256) - len(changed)}/{len(expected_sha256)} goal inputs resolve to the goal's "
                   f"sha256; {len(run_files) - len(unpinned)}/{len(run_files)} run artifacts match the packet "
                   f"manifest or their byte reference; {len(committed) - len(uncommitted)}/{len(committed)} equal their "
                   "blob committed at HEAD")


def inv_obs(*, goal_loops: dict[str, FieldLoop], run_loops: dict[str, FieldLoop], goal_attribute: str,
            run_attributes: list[str], goal_loops_sha256: str, run_loops_sha256: str | None) -> dict:
    """Counted attribute, E1 ids, lanes, positions and periods, and the field-loops hash are unchanged."""
    witnesses = [f"attribute:{value}" for value in run_attributes if value != goal_attribute]
    witnesses += [f"detector:{name}" for name in set(goal_loops) ^ set(run_loops)]
    witnesses += [f"detector:{name}" for name in set(goal_loops) & set(run_loops)
                  if goal_loops[name] != run_loops[name]]
    if run_loops_sha256 != goal_loops_sha256:
        witnesses.append("file:field-loops.add.xml")
    return _result("INV-OBS", witnesses,
                   f"{goal_attribute} against {len(run_attributes)} run records; {len(goal_loops)} goal E1 (id, lane, "
                   "pos, period) against the run's E1 file; field-loops sha256 against the run's recorded inputs")


def hold_node_sources(run: dict, request: dict, scenario: dict | None = None) -> dict[str, list[str] | None]:
    """The junction-body hold nodes as each run record (and optionally the prepared scenario) states them."""
    sources = {"run.json": run.get("junction_body_hold_nodes"),
               "native-request.json": request.get("junction_body_hold_nodes"),
               "run.json stall_monitor": (run.get("stall_monitor") or {}).get("junction_body_hold_nodes")}
    if scenario is not None:
        sources["prepared/scenario.json"] = scenario.get("junction_body_hold_nodes")
    return sources


def inv_hold(goal_nodes: list[str], recorded: dict[str, list[str] | None]) -> dict:
    """The junction-body hold nodes equal the goal list in every run record."""
    witnesses = []
    for source, nodes in recorded.items():
        if nodes is None:
            witnesses.append(f"missing:{source}")
        else:
            witnesses += [f"node:{node}" for node in set(nodes) ^ set(goal_nodes)]
    return _result("INV-HOLD", witnesses, f"the goal's {len(goal_nodes)} hold nodes against {', '.join(recorded)}")


def _finite(value: object) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _in_interval(passage: dict, *, e1_begin_s: float, e1_period_s: float, step_s: float) -> bool:
    """The recorded interval is one E1 period on the goal's grid, holds the notification time, and the notification
    time is the step ceiling of ``time_s`` (``core/route_sensor_matrix.py`` bins a leave event that way)."""
    time_s, notified, begin, end = (_finite(passage.get(key)) for key in (
        "time_s", "notification_time_s", "interval_begin_s", "interval_end_s"))
    if time_s is None or notified is None or begin is None or end is None:
        return False
    slot = (begin - e1_begin_s) / e1_period_s
    ceiling = math.ceil(math.nextafter(time_s / step_s, -math.inf)) * step_s
    return (abs(end - begin - e1_period_s) <= TIME_TOLERANCE_S and abs(slot - round(slot)) <= TIME_TOLERANCE_S
            and begin <= notified < end and abs(notified - ceiling) <= TIME_TOLERANCE_S)


def _stay(route: Route | None, edge: str | None) -> tuple[float, float] | None:
    """When a vehicle's recorded route enters ``edge`` (its insertion when the route starts there) and its arrival."""
    if route is None or edge is None or edge not in route.edges:
        return None
    index = route.edges.index(edge)
    return (route.exit_s[index - 1] if index else route.depart_s), route.exit_s[-1]


def inv_pass(report: dict, intended: dict[str, str], *, e1_counts: dict[tuple[str, float], int],
             bindings: dict[str, tuple[str | None, str]], routes: dict[str, Route], field_edges: dict[str, str],
             e1_begin_s: float, e1_period_s: float, step_s: float) -> dict:
    """The passage report belongs to the profiled E1 output and reproduces its count on every scored row.

    The report's own ``reconciled`` and ``inputs_unchanged`` flags describe the run that wrote it, so they are
    required but never trusted alone. ``bindings`` maps each input file name the report must record to the SHA-256
    it must record for it and where that SHA-256 comes from; a missing, different or unexpected recorded input
    fails. ``e1_counts`` holds the profiled E1 nVehContrib per (detector, interval begin) of every scored row, which
    the report's passages must equal. At most one passage per vehicle per field, none off-intention.

    Each passage must also sit where its own fields and the run's tracked vehicle routes put it: its interval is one
    E1 period on the goal's grid holding the step ceiling of ``time_s`` (``interval:`` witnesses), and ``time_s`` lies
    within one step of its vehicle's recorded stay, from entering the field edge (``field_edges``) to arrival
    (``route:`` witnesses). A passage moved in time, or handed another vehicle's id, fails even when every row count
    still matches.
    """
    witnesses = [f"report:{flag}" for flag in ("reconciled", "inputs_unchanged") if report.get(flag) is not True]
    recorded: dict[str, list[str | None]] = {}
    for row in report.get("inputs", []):
        recorded.setdefault(PureWindowsPath(str(row.get("path", ""))).name, []).append(row.get("sha256"))
    unbound = sorted(name for name in set(recorded) | set(bindings)
                     if bindings.get(name, (None,))[0] is None or recorded.get(name) != [bindings[name][0]])
    witnesses += [f"report_input:{name}" for name in unbound]
    passages = report.get("passages", [])
    counted = Counter((p["detector_id"], float(p["interval_begin_s"])) for p in passages)
    differ = [ids.row_id(detector, begin) for (detector, begin), count in e1_counts.items()
              if counted[(detector, begin)] != count]
    witnesses += [f"count:{row}" for row in differ]
    repeated = Counter((p["vehicle_id"], p["detector_id"]) for p in passages)
    witnesses += [f"repeated:{vehicle}@{detector}" for (vehicle, detector), n in repeated.items() if n > 1]
    witnesses += [f"off_intention:{p['vehicle_id']}@{p['detector_id']}" for p in passages
                  if intended.get(p["vehicle_id"]) != p["detector_id"]]
    outside = [f"interval:{p['vehicle_id']}@{p['detector_id']}" for p in passages
               if not _in_interval(p, e1_begin_s=e1_begin_s, e1_period_s=e1_period_s, step_s=step_s)]
    away = []
    for p in passages:
        stay, time_s = _stay(routes.get(p["vehicle_id"]), field_edges.get(p["detector_id"])), _finite(p.get("time_s"))
        if stay is None or time_s is None or not stay[0] - step_s <= time_s <= stay[1] + step_s:
            away.append(f"route:{p['vehicle_id']}@{p['detector_id']}")
    witnesses += outside + away
    inputs = "; ".join(f"{name} sha256 {(digest or 'undeclared')[:8]} "
                       f"{'is not what the report records:' if name in unbound else 'recorded, equals'} {source}"
                       for name, (digest, source) in sorted(bindings.items()))
    extra = sorted(set(recorded) - set(bindings))
    return _result("INV-PASS", witnesses,
                   f"report inputs: {inputs}" + (f"; unexpected inputs {', '.join(extra)}" if extra else "")
                   + f"; passages equal E1 nVehContrib on {len(e1_counts) - len(differ)}/{len(e1_counts)} scored "
                   f"rows; {len(passages) - len(outside)}/{len(passages)} passages inside their recorded E1 interval "
                   f"at the step ceiling of time_s; {len(passages) - len(away)}/{len(passages)} within their vehicle's "
                   "stay on the field edge in vehicle-routes.xml; reconciled and inputs_unchanged as the report "
                   "recorded them")
