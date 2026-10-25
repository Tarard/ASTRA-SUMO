"""Calibrate invariants, part 1 (M1): INV-INPUTS, INV-OBS, INV-HOLD and INV-PASS.

Each check returns ``{"id", "pass", "witness_ids"}``; the witness ids name what violates the rule.
"""

from __future__ import annotations

from collections import Counter
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .count_profile import FieldLoop


def _result(invariant: str, witnesses: list[str]) -> dict:
    return {"id": invariant, "pass": not witnesses, "witness_ids": sorted(set(witnesses))}


def inv_inputs(expected_sha256: dict[str, str], resolved_sha256: dict[str, str | None]) -> dict:
    """Every goal input alias resolves to the raw SHA-256 the goal froze."""
    return _result("INV-INPUTS", [name for name, digest in expected_sha256.items()
                                  if resolved_sha256.get(name) != digest])


def inv_obs(*, goal_loops: dict[str, FieldLoop], run_loops: dict[str, FieldLoop], goal_attribute: str,
            run_attributes: list[str], goal_loops_sha256: str, run_loops_sha256: str | None) -> dict:
    """Counted attribute, E1 ids, lanes, positions and periods, and the field-loops hash are unchanged."""
    witnesses = [f"attribute:{value}" for value in run_attributes if value != goal_attribute]
    witnesses += [f"detector:{name}" for name in set(goal_loops) ^ set(run_loops)]
    witnesses += [f"detector:{name}" for name in set(goal_loops) & set(run_loops)
                  if goal_loops[name] != run_loops[name]]
    if run_loops_sha256 != goal_loops_sha256:
        witnesses.append("file:field-loops.add.xml")
    return _result("INV-OBS", witnesses)


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
    return _result("INV-HOLD", witnesses)


def inv_pass(report: dict, intended: dict[str, str]) -> dict:
    """Reconciled passage report, at most one passage per vehicle per field, none off-intention."""
    witnesses = [f"report:{flag}" for flag in ("reconciled", "inputs_unchanged") if report.get(flag) is not True]
    passages = report.get("passages", [])
    repeated = Counter((p["vehicle_id"], p["detector_id"]) for p in passages)
    witnesses += [f"repeated:{vehicle}@{detector}" for (vehicle, detector), n in repeated.items() if n > 1]
    witnesses += [f"off_intention:{p['vehicle_id']}@{p['detector_id']}" for p in passages
                  if intended.get(p["vehicle_id"]) != p["detector_id"]]
    return _result("INV-PASS", witnesses)
