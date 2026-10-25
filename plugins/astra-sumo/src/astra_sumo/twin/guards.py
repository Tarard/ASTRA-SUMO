"""Reject-only guards of M2 protocol v3 (H3 entry, braking and post-window; N2 init52; N3 and N4 CRPS margins).

A guard only rejects, so it costs power and never validity. Margins are inputs: the provisional values are those of
protocol v3 section 5 (re-key t-margins from the parent panel, recomputed on a 14-draw panel before any standard
tier); nothing here derives a margin from the nominal run, which is excluded from every rule.

Entry delay is a departure's ``departDelay`` from tripinfo. An entry edge's p95 is the ceil(0.95 n)-th order
statistic of its departures. Quiet entries (every parent draw's p95 <= 1 s) are guarded by the candidate's K-mean
p95; busy entries and the network p95 by a CRN-paired lower confidence bound on the candidate-minus-reference
differences, so the margin adapts to the case's own pairing. The K-mean backstops and the scalar guards use the
no-CRN re-key margin t_{0.99, n-1} s sqrt(2 / K) from n parent draws.
"""

from __future__ import annotations

import math
import statistics
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence

from . import gate_rules

NETWORK = "__network__"

# One-sided Student t quantiles by degrees of freedom, at 3 decimals; the 0.95 column extends gate_rules.T_95.
T_ONE_SIDED: dict[float, dict[int, float]] = {
    0.95: {**gate_rules.T_95, 16: 1.746, 17: 1.740, 18: 1.734, 19: 1.729, 20: 1.725, 21: 1.721, 22: 1.717,
           23: 1.714, 24: 1.711, 25: 1.708, 26: 1.706, 27: 1.703, 28: 1.701, 29: 1.699, 30: 1.697},
    0.99: {1: 31.821, 2: 6.965, 3: 4.541, 4: 3.747, 5: 3.365, 6: 3.143, 7: 2.998, 8: 2.896, 9: 2.821, 10: 2.764,
           11: 2.718, 12: 2.681, 13: 2.650, 14: 2.624, 15: 2.602, 16: 2.583, 17: 2.567, 18: 2.552, 19: 2.539,
           20: 2.528, 21: 2.518, 22: 2.508, 23: 2.500, 24: 2.492, 25: 2.485, 26: 2.479, 27: 2.473, 28: 2.467,
           29: 2.462, 30: 2.457}}

# Guard -> gate label, in the order of the v3 gate table (H3, then N2, then N3 and N4).
LABELS = {"entry": "guard", "brakings": "guard", "post_window": "guard", "init52": "init",
          "totals13": "level_regress", "main130": "fine_regress"}


def t_quantile(level: float, df: int) -> float:
    if level not in T_ONE_SIDED or df not in T_ONE_SIDED[level]:
        raise ValueError(f"No one-sided t quantile for level {level} and {df} degrees of freedom (1 to 30)")
    return T_ONE_SIDED[level][df]


def order_p95(values: Iterable[float]) -> float:
    """The ceil(0.95 n)-th order statistic (the stage-3 entry p95 convention)."""
    ordered = sorted(values)
    if not ordered:
        raise ValueError("The p95 needs at least one value")
    return float(ordered[math.ceil(0.95 * len(ordered)) - 1])


def entry_p95(departures: Iterable[tuple[str, str, float]]) -> dict[str, float]:
    """p95 departure delay per entry edge (sorted), plus ``__network__`` over all departures."""
    by_edge: dict[str, list[float]] = defaultdict(list)
    for _, edge, delay in departures:
        by_edge[edge].append(delay)
    if not by_edge:
        raise ValueError("No departures")
    if NETWORK in by_edge:
        raise ValueError(f"An entry edge is named {NETWORK}")
    found = {edge: order_p95(delays) for edge, delays in sorted(by_edge.items())}
    found[NETWORK] = order_p95(delay for delays in by_edge.values() for delay in delays)
    return found


def rekey_t_margin(parent_values: Sequence[float], draws: int, *, level: float = 0.99) -> float:
    """t_{level, n-1} s sqrt(2 / draws): one-sided no-CRN margin for a K-mean difference between independent keys."""
    if len(parent_values) < 2 or draws < 1:
        raise ValueError("The re-key margin needs at least two parent draws and one candidate draw")
    return t_quantile(level, len(parent_values) - 1) * statistics.stdev(parent_values) * math.sqrt(2 / draws)


def kmean_guard(deltas: Sequence[float], margin: float) -> dict:
    """Fires when the K-mean candidate-minus-reference difference exceeds ``margin``."""
    if not deltas:
        raise ValueError("The K-mean guard needs at least one difference")
    value = statistics.fmean(deltas)
    return {"rule": "K-mean difference > margin", "draws": len(deltas), "mean": value, "margin": margin,
            "fires": value > margin}


def paired_t_guard(deltas: Sequence[float], floor: float, *, level: float = 0.95) -> dict:
    """CRN-paired guard: fires when mean - t_{level, K-1} sd / sqrt(K) exceeds ``floor`` (K >= 2)."""
    size = len(deltas)
    if size < 2:
        raise ValueError("The paired guard needs at least two differences")
    value, spread = statistics.fmean(deltas), statistics.stdev(deltas)
    lcb = value - t_quantile(level, size - 1) * spread / math.sqrt(size)
    return {"rule": f"mean - t_{level} sd / sqrt(K) > floor", "draws": size, "mean": value, "sd": spread,
            "lcb": lcb, "floor": floor, "fires": lcb > floor}


def quiet_entry_guard(candidate_p95s: Sequence[float], *, floor: float = 10.0) -> dict:
    """A quiet entry: fires when the candidate's K-mean p95 exceeds ``floor`` seconds."""
    if not candidate_p95s:
        raise ValueError("The quiet-entry guard needs at least one draw")
    value = statistics.fmean(candidate_p95s)
    return {"rule": "candidate K-mean p95 > floor", "draws": len(candidate_p95s), "mean": value, "floor": floor,
            "fires": value > floor}


def classify_entries(parent_p95_by_edge: Mapping[str, Sequence[float]], *, quiet_s: float = 1.0) -> dict[str, str]:
    """``quiet`` when every parent draw's p95 on the edge is <= ``quiet_s``, otherwise ``busy``."""
    if any(not values for values in parent_p95_by_edge.values()):
        raise ValueError("Every entry needs at least one parent draw")
    return {edge: "quiet" if max(values) <= quiet_s else "busy" for edge, values in sorted(parent_p95_by_edge.items())
            if edge != NETWORK}


def _stat(stats: dict, name: str) -> float:
    """A scalar stat, or ``entry_p95:<edge>`` from the draw's entry p95 table; a missing value is refused."""
    if name.startswith("entry_p95:"):
        table, name = stats["entry_p95"], name.removeprefix("entry_p95:")
    else:
        table = stats
    if name not in table:
        raise ValueError(f"No {name} in the draw stats; a missing value is unknown, never zero")
    return table[name]


def _deltas(candidate: Mapping[str, dict], reference: Mapping[str, dict], keys: list[str], name: str) -> list[float]:
    return [_stat(candidate[key], name) - _stat(reference[key], name) for key in keys]


def evaluate_guards(candidate_stats: Mapping[str, dict], reference_stats: Mapping[str, dict], *,
                    margins: Mapping[str, float], entry_classes: Mapping[str, str], floors: Mapping[str, float],
                    case: dict | None = None) -> dict:
    """Every v3 guard of one paired case and the first failing gate label (None when none fires).

    Per draw key the stats hold ``entry_p95`` (``entry_p95()`` output), ``brakings``, ``post_window`` and
    ``init52``. ``entry_classes`` comes from ``classify_entries`` on the parent panel. ``margins`` holds the K-mean
    margins ``network_p95``, ``brakings``, ``post_window`` and ``init52``, optional per-edge backstops
    ``entry_p95:<edge>``, and, when ``case`` (an ``evaluate_case_v3`` result) is given, ``totals13`` (N3) and
    ``main130`` (N4) bounds on its paired dCRPS. ``floors`` holds ``paired_entry`` and ``quiet_entry`` in seconds.
    N1 (max row growth) is not evaluated here; a caller that checks it passes ``row_regress`` to ``verdict_v3``.
    """
    if set(candidate_stats) != set(reference_stats):
        raise ValueError(f"Unpaired draw keys: {sorted(set(candidate_stats) ^ set(reference_stats))}")
    keys = sorted(candidate_stats)
    unknown = sorted(set(entry_classes.values()) - {"quiet", "busy"})
    if unknown:
        raise ValueError(f"Unknown entry classes {unknown}; use quiet or busy")
    results: dict[str, dict] = {}
    network = f"entry_p95:{NETWORK}"
    for edge, kind in sorted(entry_classes.items()):
        name = f"entry_p95:{edge}"
        if kind == "quiet":
            values = [_stat(candidate_stats[key], name) for key in keys]
            results[f"entry_quiet:{edge}"] = {**quiet_entry_guard(values, floor=floors["quiet_entry"]),
                                              "guard": "entry"}
        else:
            results[f"entry_paired:{edge}"] = {**paired_t_guard(_deltas(candidate_stats, reference_stats, keys, name),
                                                                floors["paired_entry"]), "guard": "entry"}
    results["entry_paired:network"] = {**paired_t_guard(_deltas(candidate_stats, reference_stats, keys, network),
                                                        floors["paired_entry"]), "guard": "entry"}
    results["entry_backstop:network"] = {**kmean_guard(_deltas(candidate_stats, reference_stats, keys, network),
                                                       margins["network_p95"]), "guard": "entry"}
    for name, margin in sorted(margins.items()):
        if name.startswith("entry_p95:"):
            results[f"entry_backstop:{name.removeprefix('entry_p95:')}"] = {
                **kmean_guard(_deltas(candidate_stats, reference_stats, keys, name), margin), "guard": "entry"}
    for name in ("brakings", "post_window", "init52"):
        results[name] = {**kmean_guard(_deltas(candidate_stats, reference_stats, keys, name), margins[name]),
                         "guard": name}
    if case is not None:
        for name in ("totals13", "main130"):
            value = case[name]["T"]
            results[name] = {"rule": "paired dCRPS > margin", "draws": case[name]["draws"], "T": value,
                             "t": float(value), "margin": margins[name], "fires": value > margins[name],
                             "guard": name}
    for result in results.values():
        result["label"] = LABELS[result["guard"]]
    fired = [name for name, result in results.items() if result["fires"]]
    order = list(dict.fromkeys(LABELS.values()))
    labels = sorted({results[name]["label"] for name in fired}, key=order.index)
    return {"draws": len(keys), "draw_keys": keys, "guards": results, "fired": fired,
            "label": labels[0] if labels else None,
            "claim": "Reject-only guards with provisional margins (protocol v3 section 5); a guard that does not "
                     "fire certifies nothing."}
