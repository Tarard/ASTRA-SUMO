"""K3 known-answer planning for the demand-timing family, offline and without SUMO (M2 protocol v2).

Material-effect screen. For an op ``cell_depart_shift(cell, s)`` every member's free-flow arrival a_v moves by exactly
s, so its planned E1 row (field, 15-min bin) is a deterministic function of a_v + s. ``shift_effect`` gives the change
Delta P of planned counts per main row, its L1 norm, the members whose planned main row changes and the window
crossings. It reads the inputs only. L1 = 0 means no direct effect on the main rows; an indirect effect through
congestion is still possible and only a run measures it (Codex MP3: cell 0 +900 s has L1 = 0).

Materiality is predicted from the parent alone, never from the op's own runs or the real-count residuals:
- noise-aware free flow: sum_r mean_e (|Delta P_r + e| - |e|), with e the row's differences between independent
  parent draws (exactly symmetric), i.e. the expected TAE gain of the true structure over the parent in a twin case
  when the effect adds to the parent's own noise;
- delay-preserving: the members' measured passages on each parent draw moved by s (their delays kept), scored as a
  twin case over ordered draw pairs.
The class of an op is taken from min(both) in units of the materiality threshold g. Both ignore congestion feedback,
so a run confirms or refutes them.

Timing degrees of freedom. DoF(D; B) is the number of OD-period cells whose (vehicle id, depart) multiset in demand D
differs from the declared base demand B. A candidate must be built canonically from B
(``scenario_ops.cell_depart_shifts``), so DoF 0 coincides with byte identity.
"""

from __future__ import annotations

import hashlib
import itertools
import math
from collections import Counter, defaultdict
from dataclasses import dataclass
from decimal import Decimal
from statistics import mean, pstdev

from . import ids
from .perturb import Demand

CLASSES = ("none", "immaterial", "small", "moderate", "large")


@dataclass(frozen=True)
class Windows:
    e1_begin_s: float
    period_s: float
    init_end_s: float
    main_end_s: float

    def bin_begin(self, time_s: float) -> float:
        return self.e1_begin_s + math.floor((time_s - self.e1_begin_s) / self.period_s) * self.period_s

    def window(self, begin_s: float) -> str:
        return "init" if begin_s < self.init_end_s else "main" if begin_s < self.main_end_s else "post"


def planned_row(detector_id: str, time_s: float, windows: Windows) -> tuple[str, str]:
    """(row id, window) of a passage at ``time_s`` on ``detector_id``."""
    begin = windows.bin_begin(time_s)
    return ids.row_id(detector_id, begin), windows.window(begin)


def shift_effect(members: list[str], arrivals: dict[str, float], intended: dict[str, str], shift_s: float,
                 windows: Windows) -> dict:
    """Free-flow planned-row change of one cell shift: Delta P over main rows, L1, movers and window crossings."""
    delta: Counter = Counter()
    crossings: Counter = Counter()
    moved_main = 0
    for vehicle in members:
        old, old_window = planned_row(intended[vehicle], arrivals[vehicle], windows)
        new, new_window = planned_row(intended[vehicle], arrivals[vehicle] + shift_s, windows)
        if old == new:
            continue
        if old_window == "main":
            delta[old] -= 1
        if new_window == "main":
            delta[new] += 1
        if "main" in (old_window, new_window):
            moved_main += 1
        if old_window != new_window:
            crossings[f"{old_window}->{new_window}"] += 1
    changed = {row: value for row, value in sorted(delta.items()) if value}
    return {"delta": changed, "l1": sum(map(abs, changed.values())), "moved_main": moved_main,
            "crossings": dict(sorted(crossings.items())), "rows_touched": len(changed)}


def classify(value: float, g: float) -> str:
    """Effect class in units of the materiality threshold g; never by vehicle count or by seconds of shift."""
    if value <= 0:
        return "none"
    return "immaterial" if value < g else "small" if value < 2 * g else "moderate" if value < 4 * g else "large"


def pairwise_row_noise(panel: list[dict[str, int]], rows: list[str]) -> dict[str, tuple[int, ...]]:
    """Per row, c_i - c_j over the ordered pairs of distinct parent draws: exactly symmetric by construction."""
    if len(panel) < 2:
        raise ValueError("Pairwise noise needs at least two parent draws.")
    return {row: tuple(a[row] - b[row] for a, b in itertools.permutations(panel, 2)) for row in rows}


def noisy_detectors(panel: list[dict[str, int]], rows: list[str], *, threshold: float = 1.0) -> dict[str, float]:
    """Detectors whose mean per-row standard deviation across the parent draws is >= threshold."""
    spread: dict[str, list[float]] = defaultdict(list)
    for row in rows:
        spread[row[2:].rsplit("@", 1)[0]].append(pstdev([draw[row] for draw in panel]))
    return {name: round(mean(values), 3) for name, values in sorted(spread.items()) if mean(values) >= threshold}


def noise_aware_effect(delta: dict[str, int], noise: dict[str, tuple[int, ...]]) -> float:
    """sum_r mean_e (|delta_r + e| - |e|); a row without noise samples counts |delta_r|. Never above L1(delta)."""
    total = 0.0
    for row, value in delta.items():
        samples = noise.get(row) or (0,)
        total += mean(abs(value + e) - abs(e) for e in samples)
    return total


def index_passages(passages: list[list]) -> dict[str, list[list]]:
    """Canonical measurement passages [vehicle, detector, interval_begin_s, time_s, notification_s, ...] by vehicle."""
    found: dict[str, list[list]] = defaultdict(list)
    for passage in passages:
        found[passage[0]].append(passage)
    return found


def shifted_delta(passages: dict[str, list[list]], members: set[str], shift_s: float, windows: Windows,
                  rows: set[str]) -> dict[str, int]:
    """Delay-preserving model: change of E1 counts on ``rows`` when the members' measured passages move by shift_s.

    A passage's bin is the bin of its notification time, which must reproduce the recorded interval.
    """
    delta = Counter()
    for vehicle in sorted(members):
        for _, detector, begin, _, notification, *_ in passages.get(vehicle, ()):
            if windows.bin_begin(notification) != float(begin):
                raise ValueError(f"{vehicle}: notification {notification} is not in its recorded bin {begin}")
            old, _ = planned_row(detector, notification, windows)
            new, _ = planned_row(detector, notification + shift_s, windows)
            if old != new:
                if old in rows:
                    delta[old] -= 1
                if new in rows:
                    delta[new] += 1
    return {row: value for row, value in sorted(delta.items()) if value}


def apply_delta(counts: dict[str, int], delta: dict[str, int]) -> dict[str, int]:
    return {row: value + delta.get(row, 0) for row, value in counts.items()}


def tae(counts: dict[str, int], target: dict[str, int], rows: list[str]) -> int:
    return sum(abs(counts[row] - target[row]) for row in rows)


def delay_preserving_prediction(panel_counts: list[dict[str, int]], shifted: list[dict[str, int]],
                                target: dict[str, int], rows: list[str]) -> dict:
    """Twin and real-count predictions of one op from the parent draws and their delay-preserving shifted counts.

    Twin: truth = shifted draw t, candidate = shifted draw k, parent = draw k, over ordered pairs k != t; the gain is
    TAE(parent) - TAE(candidate). Real: d_k = TAE(shifted_k, y) - TAE(draw_k, y). ``no_chaos_d_sd`` is the spread of
    the twin d over judge draws for a fixed truth, averaged over truths: the part of sd(d) that this model explains.
    """
    gains, per_truth = [], defaultdict(list)
    for k, t in itertools.permutations(range(len(panel_counts)), 2):
        gain = tae(panel_counts[k], shifted[t], rows) - tae(shifted[k], shifted[t], rows)
        gains.append(gain)
        per_truth[t].append(-gain)
    real = [tae(s, target, rows) - tae(p, target, rows) for p, s in zip(panel_counts, shifted)]
    sds = [pstdev(values) for values in per_truth.values() if len(values) > 1]
    return {"twin_gain_mean": round(mean(gains), 3) if gains else None,
            "no_chaos_d_sd": round(mean(sds), 3) if sds else None,
            "real_d": real, "real_d_mean": round(mean(real), 3) if real else None}


def op_label(cell: int, shift_s: float) -> str:
    return f"cell_depart_shift:{cell}:{int(shift_s) if float(shift_s).is_integer() else shift_s}"


@dataclass(frozen=True)
class Panel:
    """Parent draws used only to predict materiality: counts on the main rows and indexed passages per draw."""
    counts: tuple[dict[str, int], ...]
    passages: tuple[dict[str, list[list]], ...]
    target: dict[str, int]
    rows: tuple[str, ...]


def screen(cells: dict[int, dict], members: dict[int, list[str]], arrivals: dict[str, float],
           intended: dict[str, str], departs: dict[str, float], *, shifts: list[float], windows: Windows,
           horizon_s: float, g: float, panel: Panel | None = None) -> list[dict]:
    """Every (cell, shift) on the grid whose shifted departures stay inside [0, horizon), with its free-flow effect
    and, given a parent panel, its noise-aware and delay-preserving materiality and class."""
    noise = pairwise_row_noise(list(panel.counts), list(panel.rows)) if panel else None
    row_set = set(panel.rows) if panel else set()
    found = []
    for cell, info in sorted(cells.items()):
        first = min(departs[v] for v in members[cell])
        last = max(departs[v] for v in members[cell])
        for shift in shifts:
            if first + shift < 0 or last + shift >= horizon_s:
                continue
            effect = shift_effect(members[cell], arrivals, intended, shift, windows)
            row = {"op": op_label(cell, shift), "cell": cell, "shift_s": shift, "vehicles": info["vehicles"],
                   "fields": info["fields"], "single_field": len(info["fields"]) == 1, "period": info["period"],
                   "earliest_change_s": round(min(first, first + shift), 3), **effect,
                   "l1_class": classify(effect["l1"], g)}
            if panel is not None:
                shifted = [apply_delta(draw, shifted_delta(passages, set(members[cell]), shift, windows, row_set))
                           for draw, passages in zip(panel.counts, panel.passages)]
                predicted = delay_preserving_prediction(list(panel.counts), shifted, panel.target, list(panel.rows))
                row.update(noise_aware_effect=round(noise_aware_effect(effect["delta"], noise), 3),
                           dp_twin_gain=predicted["twin_gain_mean"], dp_no_chaos_d_sd=predicted["no_chaos_d_sd"],
                           dp_real_d_mean=predicted["real_d_mean"])
                row["materiality"] = round(min(row["noise_aware_effect"], row["dp_twin_gain"]), 3)
                row["class"] = classify(row["materiality"], g) if effect["l1"] > 0 else "none"
            found.append(row)
    return found


def _rank(seed: str, op: str) -> str:
    return hashlib.sha256(f"{seed}\x00{op}".encode("utf-8")).hexdigest()


def select_ops(rows: list[dict], *, wanted: str, count: int, seed: str, single_field: bool = True,
               exclude_cells: frozenset[int] = frozenset(), class_key: str = "class",
               where=None) -> list[dict]:
    """``count`` ops whose ``class_key`` is ``wanted`` (and that pass ``where``), on distinct cells, ordered by
    sha256(seed, op): declared before any run of the ops and blind to the real-count residuals."""
    pool = sorted((row for row in rows if row[class_key] == wanted and (row["single_field"] or not single_field)
                   and row["cell"] not in exclude_cells and (where is None or where(row))),
                  key=lambda row: _rank(seed, row["op"]))
    chosen, cells = [], set(exclude_cells)
    for row in pool:
        if row["cell"] not in cells:
            chosen.append(row)
            cells.add(row["cell"])
        if len(chosen) == count:
            break
    return chosen


def confusable(delta_a: dict[str, int], delta_b: dict[str, int], g: float) -> bool:
    """Two ops are confusable at materiality g when their planned changes differ by less than g in L1."""
    return sum(abs(delta_a.get(row, 0) - delta_b.get(row, 0)) for row in set(delta_a) | set(delta_b)) < g


def timing_dof(demand: Demand, base: Demand, cells: dict[str, int]) -> dict:
    """Cells whose (vehicle id, depart) multiset differs from the base; vehicle sets must be equal."""
    if {v.vehicle_id for v in demand.vehicles} != {v.vehicle_id for v in base.vehicles}:
        raise ValueError("DoF compares demands with the same vehicles; quantity changes are outside this class.")

    def by_cell(value: Demand) -> dict[int, list[tuple[str, Decimal]]]:
        grouped: dict[int, list[tuple[str, Decimal]]] = defaultdict(list)
        for vehicle in value.vehicles:
            grouped[cells[vehicle.vehicle_id]].append((vehicle.vehicle_id, vehicle.depart))
        return {cell: sorted(rows) for cell, rows in grouped.items()}

    left, right = by_cell(demand), by_cell(base)
    changed = sorted(cell for cell in left.keys() | right.keys() if left.get(cell) != right.get(cell))
    return {"dof": len(changed), "cells": changed, "byte_identical": demand.to_bytes() == base.to_bytes(),
            "base_sha256": base.sha256, "sha256": demand.sha256}


def loo_folds(keys: list[str]) -> list[dict]:
    """Leave-one-out twin folds: each key in turn is the truth key and the others are the judge keys. Folds share
    runs, so they are dependent and are never counted as independent cases."""
    return [{"truth": truth, "judge": [key for key in keys if key != truth]} for truth in keys]


def judge_jobs(arms: dict[str, str | None], keys: list[str], *, family: str = "depart_jitter",
               purpose: str) -> list[dict]:
    """Batch jobs for ``astra_twin.py judge --batch``: every arm (label -> op, None for the parent) on every key."""
    return [{"label": f"{arm}-{key.rsplit('/', 1)[-1]}", "purpose": f"{purpose}: {arm} on {key}",
             **({"op": op} if op else {}), "family": family, "public_key": key}
            for arm, op in arms.items() for key in keys]
