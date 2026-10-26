"""Queue-aware screen and selection rule for one hour-scale corridor op (M2 stage C2), offline and without SUMO.

B0's delay-preserving screen failed on the saturated corridor: L's 60 vehicles (cell 301, -900 s) were absorbed by
the insertion queue on their entry edge and displaced other vehicles in the same fields (C8-L 0/6, ED -0.47). This
screen models both effects from the parent panel alone, draw by draw, for an op ``cell_depart_shift(cell, s)``:

- **Insertion.** A member scheduled at t + s waits the draw's insertion delay at that time, D_k(E, t + s): the median
  departDelay of the entry edge's other vehicles scheduled in the same 900-s bin (0 when the bin has none). Its
  post-insertion travel time is kept, so each passage moves by t + s + D_k(E, t + s) - u_v, with u_v the member's
  measured insertion time on draw k.
- **Displacement.** An insertion bin is saturated on draw k when the median departDelay of the entry's other vehicles
  inserted in it is at least ``saturation_s``. A saturated entry discharges a fixed number of vehicles per bin (FIFO),
  so a member inserted in a saturated bin takes one slot from the other vehicles inserted in that bin, and a member
  leaving a saturated bin frees one: the others' counts change by minus (plus) the empirical row distribution of
  their passages on that draw.

The predicted draw is S_k = P_k + Delta_k on the 130 main rows (fractional by the displacement term). Scores use the
primary objective ms4: the predicted real-count gain mean_k [L1(P_k, y) - L1(S_k, y)] and the predicted paired
energy distance between {S_k} and {P_k}; the predicted C8 is the signed change on the op's free-flow planned rows
(``k3.shift_effect``, the C8 definition) on every draw. The model ignores congestion feedback beyond the entry and
the downstream chaos, so a run confirms or refutes it; it selects, it never scores a gate.

``select_corridor_op`` applies the rule declared for C2 mechanically (see its docstring). Explore only: simulated
counts under the supplied model, no field truth, no promotion authority.
"""

from __future__ import annotations

import hashlib
import io
import itertools
import math
import statistics
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from . import ensemble, k3

SCREEN_SCHEMA = "astra.twin-m2-corridor-screen/v1"
HOUR_SHIFTS_S = (-3600.0, -2700.0, -1800.0, 1800.0, 2700.0, 3600.0)
SATURATION_S = 60.0
SELECTION_SEED = "astra-m2/c2/corridor-op/v1"
WINDOWS = k3.Windows(e1_begin_s=0.0, period_s=900.0, init_end_s=ensemble.MAIN_BEGIN_S,
                     main_end_s=ensemble.MAIN_END_S)

Counts = Mapping[str, float]


# --- inputs ----------------------------------------------------------------------------------------------------------

def tripinfo_insertions(data: bytes) -> dict[str, tuple[str, float, float]]:
    """vehicle -> (entry edge, insertion time s, departDelay s) from a SUMO tripinfo file.

    The entry edge is the departLane id without its trailing ``_<index>`` (``runner.tripinfo_departures``); a row
    without an id, a departLane, a depart or a departDelay is refused rather than read as zero.
    """
    found = {}
    for _, element in ET.iterparse(io.BytesIO(data)):
        if element.tag != "tripinfo":
            continue
        vehicle, lane = element.get("id"), element.get("departLane")
        depart, delay = element.get("depart"), element.get("departDelay")
        if not vehicle or not lane or "_" not in lane or depart is None or delay is None:
            raise ValueError(f"tripinfo row {vehicle!r} needs an id, a departLane <edge>_<index>, a depart and a "
                             "departDelay")
        if vehicle in found:
            raise ValueError(f"tripinfo lists {vehicle!r} twice")
        found[vehicle] = (lane.rsplit("_", 1)[0], float(depart), float(delay))
        element.clear()
    return found


@dataclass(frozen=True)
class QueueDraw:
    """One parent draw: main-row counts, each vehicle's insertion and its passages (detector, notification time)."""

    key: str
    counts: Mapping[str, int]
    insertions: Mapping[str, tuple[str, float, float]]
    passages: Mapping[str, tuple[tuple[str, float], ...]]


def queue_draw(key: str, counts: Mapping[str, int], tripinfo: bytes, passages_full: Sequence[Sequence]) -> QueueDraw:
    """A ``QueueDraw`` from a judged run's main-row counts, tripinfo bytes and canonical full passages
    ``[vehicle, detector, interval_begin_s, time_s, notification_s, ...]``; a passage whose notification time is not
    in its recorded bin is refused (``k3.shifted_delta``'s convention)."""
    found: dict[str, list[tuple[str, float]]] = defaultdict(list)
    for vehicle, detector, begin, _, notification, *_ in passages_full:
        if WINDOWS.bin_begin(notification) != float(begin):
            raise ValueError(f"{vehicle}: notification {notification} is not in its recorded bin {begin}")
        found[vehicle].append((detector, float(notification)))
    return QueueDraw(key, dict(counts), tripinfo_insertions(tripinfo),
                     {vehicle: tuple(rows) for vehicle, rows in sorted(found.items())})


class _Index:
    """Per draw: the entry's vehicles by scheduled and by insertion bin, and each vehicle's passage rows."""

    def __init__(self, draw: QueueDraw) -> None:
        self.draw = draw
        self.by_schedule: dict[tuple[str, int], list[tuple[str, float]]] = defaultdict(list)
        self.by_insertion: dict[tuple[str, int], list[tuple[str, float]]] = defaultdict(list)
        for vehicle, (edge, inserted, delay) in draw.insertions.items():
            self.by_schedule[(edge, _bin(inserted - delay))].append((vehicle, delay))
            self.by_insertion[(edge, _bin(inserted))].append((vehicle, delay))
        self.rows = {vehicle: [k3.planned_row(detector, time, WINDOWS) for detector, time in rows]
                     for vehicle, rows in draw.passages.items()}

    def delay(self, edge: str, schedule_bin: int, members: frozenset[str]) -> float:
        delays = [delay for vehicle, delay in self.by_schedule.get((edge, schedule_bin), ()) if vehicle not in members]
        return statistics.median(delays) if delays else 0.0

    def saturated(self, edge: str, insertion_bin: int, members: frozenset[str], threshold: float) -> bool:
        delays = [delay for vehicle, delay in self.by_insertion.get((edge, insertion_bin), ())
                  if vehicle not in members]
        return bool(delays) and statistics.median(delays) >= threshold

    def distribution(self, edge: str, insertion_bin: int, members: frozenset[str]) -> dict[str, float]:
        others = [vehicle for vehicle, _ in self.by_insertion.get((edge, insertion_bin), ()) if vehicle not in members]
        rows: Counter = Counter()
        for vehicle in others:
            rows.update(row for row, window in self.rows.get(vehicle, ()) if window == "main")
        return {row: count / len(others) for row, count in rows.items()} if others else {}


def _bin(time_s: float) -> int:
    return math.floor(time_s / WINDOWS.period_s)


# --- the per-draw prediction -----------------------------------------------------------------------------------------

def queue_delta(index: _Index, members: Sequence[str], shift_s: float, *, saturation_s: float = SATURATION_S) -> dict:
    """Predicted main-row change Delta_k of one op on one draw, with its window crossings and saturation counts."""
    group = frozenset(members)
    edges = {index.draw.insertions[vehicle][0] for vehicle in group}
    if len(edges) != 1:
        raise ValueError(f"The op's members enter on {sorted(edges)}; one cell keeps one entry edge")
    edge, = edges
    delays: dict[int, float] = {}
    shares: dict[int, dict[str, float] | None] = {}

    def wait(schedule_bin: int) -> float:
        if schedule_bin not in delays:
            delays[schedule_bin] = index.delay(edge, schedule_bin, group)
        return delays[schedule_bin]

    def displaced(insertion_bin: int) -> dict[str, float] | None:
        if insertion_bin not in shares:
            shares[insertion_bin] = (index.distribution(edge, insertion_bin, group)
                                     if index.saturated(edge, insertion_bin, group, saturation_s) else None)
        return shares[insertion_bin]

    delta: Counter = Counter()
    crossings, saturated_old, saturated_new = 0, 0, 0
    for vehicle in sorted(group):
        _, inserted, own_delay = index.draw.insertions[vehicle]
        scheduled = inserted - own_delay + shift_s
        moved_to = scheduled + wait(_bin(scheduled))
        move = moved_to - inserted
        for detector, time in index.draw.passages.get(vehicle, ()):
            old, old_window = k3.planned_row(detector, time, WINDOWS)
            new, new_window = k3.planned_row(detector, time + move, WINDOWS)
            crossings += not old_window == new_window == "main"
            if old_window == "main":
                delta[old] -= 1
            if new_window == "main":
                delta[new] += 1
        for sign, at in ((1, inserted), (-1, moved_to)):
            found = displaced(_bin(at))
            if found is not None:
                saturated_old += sign == 1
                saturated_new += sign == -1
                for row, share in found.items():
                    delta[row] += sign * share
    return {"delta": {row: value for row, value in sorted(delta.items()) if abs(value) > 1e-12},
            "crossings": crossings, "saturated_old": saturated_old, "saturated_new": saturated_new,
            "entry": edge, "members": len(group)}


def _ms4(counts: Counts, main: Sequence[str]) -> dict[str, float]:
    return ensemble.objective_values(counts, main, "ms4")


def _l1(a: Counts, b: Counts, keys: Sequence[str]) -> float:
    return sum(abs(a[key] - b[key]) for key in keys)


def float_paired_energy(shifted: Sequence[Counts], parent: Sequence[Counts], keys: Sequence[str]) -> float:
    """The paired L1 energy distance of ``ensemble.energy_pair_terms`` on real-valued draws (prediction only)."""
    size = len(shifted)
    if size != len(parent) or size < 2:
        raise ValueError("Use two paired ensembles of equal size, at least two draws each")
    total = 0.0
    for k, j in itertools.combinations(range(size), 2):
        total += (2 * (_l1(shifted[k], parent[j], keys) + _l1(shifted[j], parent[k], keys))
                  - 2 * _l1(shifted[k], shifted[j], keys) - 2 * _l1(parent[k], parent[j], keys))
    return total / (2 * size * (size - 1))


# --- the screen ------------------------------------------------------------------------------------------------------

def screen(cells: Mapping[int, dict], members: Mapping[int, Sequence[str]], *, draws: Sequence[QueueDraw],
           real: Mapping[str, int], arrivals: Mapping[str, float], intended: Mapping[str, str],
           nominal_departs: Mapping[str, float], corridor_fields: Sequence[str], horizon_s: float,
           shifts: Sequence[float] = HOUR_SHIFTS_S, g: float = 10.0,
           saturation_s: float = SATURATION_S) -> list[dict]:
    """Every corridor op on the shift grid with its free-flow plan, its queue-aware prediction and its eligibility.

    A corridor cell has every member intending a field of ``corridor_fields``; a shift is on the grid when every
    member's nominal departure stays in [0, horizon). ``draws`` are the parent panel (main-row counts) and ``real``
    holds the real counts on the main rows.
    """
    main = ensemble.main_rows(list(real))
    target = _ms4(real, main)
    keys = list(target)
    indices = [_Index(draw) for draw in draws]
    parent_ms4 = [_ms4(draw.counts, main) for draw in draws]
    parent_l1 = [_l1(values, target, keys) for values in parent_ms4]
    rows = []
    for cell, info in sorted(cells.items()):
        if not set(info["fields"]) <= set(corridor_fields):
            continue
        group = list(members[cell])
        first, last = min(nominal_departs[v] for v in group), max(nominal_departs[v] for v in group)
        for shift in shifts:
            if first + shift < 0 or last + shift >= horizon_s:
                continue
            planned = k3.shift_effect(group, dict(arrivals), dict(intended), shift, WINDOWS)
            free_main = all(k3.planned_row(intended[v], arrivals[v] + offset, WINDOWS)[1] == "main"
                            for v in group for offset in (0.0, shift))
            predicted = [queue_delta(index, group, shift, saturation_s=saturation_s) for index in indices]
            shifted = [{row: draw.counts[row] + found["delta"].get(row, 0.0) for row in main}
                       for draw, found in zip(draws, predicted)]
            shifted_ms4 = [_ms4(counts, main) for counts in shifted]
            gains = [before - _l1(values, target, keys) for before, values in zip(parent_l1, shifted_ms4)]
            planned_l1 = planned["l1"]
            c8 = [sum((1 if value > 0 else -1) * found["delta"].get(row, 0.0)
                      for row, value in planned["delta"].items()) for found in predicted]
            ed = float_paired_energy(shifted_ms4, parent_ms4, keys)
            record = {
                "op": k3.op_label(cell, shift), "cell": cell, "shift_s": shift, "pair": info["pair"],
                "period": info["period"], "vehicles": info["vehicles"], "fields": info["fields"],
                "entry": predicted[0]["entry"] if predicted else None,
                "planned": {"delta": planned["delta"], "l1": planned_l1, "crossings": planned["crossings"],
                            "members_stay_main": free_main},
                "predicted": {"real_gain_ms4": round(statistics.fmean(gains), 3),
                              "real_gain_ms4_per_draw": [round(x, 3) for x in gains],
                              "ed_ms4": round(ed, 3), "ed_class": k3.classify(ed, g),
                              "c8_per_draw": [round(x, 3) for x in c8],
                              "c8_hits": sum(2 * x >= planned_l1 for x in c8) if planned_l1 else 0,
                              "crossings_per_draw": [found["crossings"] for found in predicted],
                              "saturated_old": sum(found["saturated_old"] for found in predicted),
                              "saturated_new": sum(found["saturated_new"] for found in predicted),
                              "member_draws": sum(found["members"] for found in predicted)}}
            rows.append(record)
    return rows


def eligibility(row: Mapping, *, g: float, draws: int, ed_floor_g: float = 2.0) -> dict[str, bool]:
    """The declared filters: members stay in the main window (free flow and every predicted draw), the predicted ms4
    paired ED is at least ``ed_floor_g`` g, and the predicted C8 holds on every draw."""
    predicted, planned = row["predicted"], row["planned"]
    return {"window": planned["members_stay_main"] and not planned["crossings"]
            and not any(predicted["crossings_per_draw"]),
            "ed": predicted["ed_ms4"] >= ed_floor_g * g,
            "c8": planned["l1"] > 0 and predicted["c8_hits"] == draws}


def select_corridor_op(rows: Sequence[Mapping], *, g: float, draws: int, seed: str = SELECTION_SEED) -> dict:
    """The C2 rule, applied mechanically: among the screened corridor ops that pass every filter of
    ``eligibility``, the largest predicted ms4 real-count gain (3 decimals), then the larger predicted ED, then
    sha256(seed, op). If none passes, the ED floor drops from 2 g to g once; if still none, no op is selected."""
    for floor in (2.0, 1.0):
        pool = [row for row in rows if all(eligibility(row, g=g, draws=draws, ed_floor_g=floor).values())]
        if pool:
            ranked = sorted(pool, key=lambda row: (-row["predicted"]["real_gain_ms4"], -row["predicted"]["ed_ms4"],
                                                   hashlib.sha256(f"{seed}\x00{row['op']}".encode()).hexdigest()))
            return {"selected": dict(ranked[0]), "ed_floor_g": floor, "eligible": len(pool),
                    "runner_up": [row["op"] for row in ranked[1:4]], "screened": len(rows), "seed": seed}
    return {"selected": None, "ed_floor_g": None, "eligible": 0, "runner_up": [], "screened": len(rows),
            "seed": seed}


# --- report-only context ---------------------------------------------------------------------------------------------

def timing_lag(panel: Sequence[Mapping[str, int]], real: Mapping[str, int], fields: Sequence[str]) -> dict:
    """Per field and pooled: the count-weighted mean main-bin midpoint of the parent's mean counts minus the real
    counts' (s). Positive means the twin's flow comes later than the real flow (report only)."""
    main = ensemble.main_rows(list(real))
    found = {}
    for name in [*fields, "pooled"]:
        chosen = [row for row in main if name == "pooled" and ensemble.parse_row(row)[0] in fields
                  or ensemble.parse_row(row)[0] == name]
        mid = {row: ensemble.parse_row(row)[1] + WINDOWS.period_s / 2 for row in chosen}
        twin = {row: statistics.fmean(draw[row] for draw in panel) for row in chosen}
        centroid_twin = sum(mid[row] * twin[row] for row in chosen) / sum(twin.values())
        centroid_real = sum(mid[row] * real[row] for row in chosen) / sum(real[row] for row in chosen)
        found[name] = {"lag_s": round(centroid_twin - centroid_real, 1), "twin_total": round(sum(twin.values()), 2),
                       "real_total": sum(real[row] for row in chosen)}
    return found


def ms4_excess_by_field(panel: Sequence[Mapping[str, int]], real: Mapping[str, int]) -> dict[str, dict]:
    """Per field: PR = mean_k L1(P_k, y), PP = mean_{i<j} L1(P_i, P_j) on that field's ms4 values, and PR - PP."""
    main = ensemble.main_rows(list(real))
    target = _ms4(real, main)
    values = [_ms4(draw, main) for draw in panel]
    by_field: dict[str, list[str]] = defaultdict(list)
    for key in target:
        by_field[key[2:].split("@", 1)[0]].append(key)
    found = {}
    for name, keys in sorted(by_field.items()):
        pr = statistics.fmean(_l1(v, target, keys) for v in values)
        pp = statistics.fmean(_l1(a, b, keys) for a, b in itertools.combinations(values, 2))
        found[name] = {"PR": round(pr, 3), "PP": round(pp, 3), "excess": round(pr - pp, 3)}
    return found
