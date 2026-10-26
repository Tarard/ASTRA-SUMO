"""Reject-only guards of M2 protocol v3 (H3 entry, braking and post-window; N2 init52; N3 and N4 CRPS margins).

A guard only rejects, so it costs power and never validity; a guard that holds certifies no non-regression. Margins
are inputs (the frozen M2 values are in ``m2_stage``, recalibrated on the C1 parent panel); nothing here derives a
margin from the nominal run, which is excluded from every rule.

Entry delay is a departure's ``departDelay`` from tripinfo. An entry edge's p95 is the ceil(0.95 n)-th order
statistic of its departures. Quiet entries (every parent draw's p95 <= 1 s) are guarded by the candidate's K-mean
p95. The network p95 and every busy entry given a margin are guarded by the K-mean candidate-minus-reference
difference against the no-CRN re-key margin t_{0.99, n-1} s sqrt(2 / K) from n parent draws, per entry edge;
brakings and post-window passages likewise (which busy entries block is the gate's choice; the M2 gate declares
44532531 only and reports the others through ``kmean_margins``). These K-mean guards need no pairing, so they hold
for arms on disjoint keys.

The L-type backlog check (a CRN-paired, or for disjoint arms a two-sample, lower confidence bound on the busy-entry
p95 differences above a floor) is a flag that never blocks (decision 6): a blocking rule would be fitted to one case.
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


def kmean_difference_guard(candidate_values: Sequence[float], reference_values: Sequence[float],
                           margin: float) -> dict:
    """Fires when the candidate's K-mean minus the reference's K-mean exceeds ``margin``; no pairing is needed."""
    if not candidate_values or not reference_values:
        raise ValueError("The K-mean guard needs at least one draw per arm")
    candidate_mean, reference_mean = statistics.fmean(candidate_values), statistics.fmean(reference_values)
    value = candidate_mean - reference_mean
    return {"rule": "K-mean candidate - K-mean reference > margin", "draws": [len(candidate_values),
                                                                            len(reference_values)],
            "candidate_mean": candidate_mean, "reference_mean": reference_mean, "mean": value, "margin": margin,
            "fires": value > margin}


def paired_t_guard(deltas: Sequence[float], floor: float, *, level: float = 0.95) -> dict:
    """CRN-paired bound: fires when mean - t_{level, K-1} sd / sqrt(K) exceeds ``floor`` (K >= 2)."""
    size = len(deltas)
    if size < 2:
        raise ValueError("The paired guard needs at least two differences")
    value, spread = statistics.fmean(deltas), statistics.stdev(deltas)
    lcb = value - t_quantile(level, size - 1) * spread / math.sqrt(size)
    return {"rule": f"mean - t_{level} sd / sqrt(K) > floor", "draws": size, "mean": value, "sd": spread,
            "lcb": lcb, "floor": floor, "fires": lcb > floor}


def two_sample_t_guard(candidate_values: Sequence[float], reference_values: Sequence[float], floor: float, *,
                       level: float = 0.95) -> dict:
    """Two-sample bound for disjoint arms: fires when the mean difference minus t_{level, nC + nR - 2} s_pooled
    sqrt(1 / nC + 1 / nR) exceeds ``floor`` (at least two draws per arm)."""
    n_c, n_r = len(candidate_values), len(reference_values)
    if n_c < 2 or n_r < 2:
        raise ValueError("The two-sample guard needs at least two draws per arm")
    value = statistics.fmean(candidate_values) - statistics.fmean(reference_values)
    pooled = math.sqrt(((n_c - 1) * statistics.variance(candidate_values)
                        + (n_r - 1) * statistics.variance(reference_values)) / (n_c + n_r - 2))
    lcb = value - t_quantile(level, n_c + n_r - 2) * pooled * math.sqrt(1 / n_c + 1 / n_r)
    return {"rule": f"mean difference - t_{level} s_pooled sqrt(1/nC + 1/nR) > floor", "draws": [n_c, n_r],
            "mean": value, "sd_pooled": pooled, "lcb": lcb, "floor": floor, "fires": lcb > floor}


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


def _values(stats: Sequence[dict], name: str) -> list[float]:
    return [_stat(row, name) for row in stats]


def kmean_margins(candidate_stats: Sequence[dict], reference_stats: Sequence[dict],
                  margins: Mapping[str, float]) -> dict[str, dict]:
    """``kmean_difference_guard`` for every ``margins`` entry (a scalar stat or ``entry_p95:<edge>``), by name."""
    return {name: kmean_difference_guard(_values(candidate_stats, name), _values(reference_stats, name), margin)
            for name, margin in sorted(margins.items())}


def _check_classes(entry_classes: Mapping[str, str]) -> None:
    unknown = sorted(set(entry_classes.values()) - {"quiet", "busy"})
    if unknown:
        raise ValueError(f"Unknown entry classes {unknown}; use quiet or busy")
    if NETWORK in entry_classes:
        raise ValueError(f"{NETWORK} is not an entry edge")


def h3_guards(candidate_stats: Sequence[dict], reference_stats: Sequence[dict], *, margins: Mapping[str, float],
              entry_classes: Mapping[str, str], quiet_floor: float) -> dict:
    """The blocking H3 guards of two arms of draws (paired or on disjoint keys): label ``guard`` when one fires.

    Each draw's stats hold ``entry_p95`` (``entry_p95()`` output), ``brakings`` and ``post_window``. Quiet edges
    fire when the candidate's K-mean p95 exceeds ``quiet_floor``; ``network_p95``, every ``entry_p95:<edge>`` in
    ``margins``, ``brakings`` and ``post_window`` fire when the K-mean difference exceeds its re-key margin.
    """
    _check_classes(entry_classes)
    results: dict[str, dict] = {}
    for edge, kind in sorted(entry_classes.items()):
        if kind == "quiet":
            results[f"entry_quiet:{edge}"] = quiet_entry_guard(_values(candidate_stats, f"entry_p95:{edge}"),
                                                               floor=quiet_floor)
    checks = [("entry:network", f"entry_p95:{NETWORK}", margins["network_p95"]),
              *((f"entry:{name.removeprefix('entry_p95:')}", name, margin) for name, margin in sorted(margins.items())
                if name.startswith("entry_p95:")),
              ("brakings", "brakings", margins["brakings"]), ("post_window", "post_window", margins["post_window"])]
    for label, stat, margin in checks:
        results[label] = kmean_difference_guard(_values(candidate_stats, stat), _values(reference_stats, stat),
                                                margin)
    fired = [name for name, result in results.items() if result["fires"]]
    return {"guards": results, "fired": fired, "passes": not fired, "label": "guard" if fired else None}


def backlog_flags(candidate_stats: Mapping[str, dict], reference_stats: Mapping[str, dict], *,
                  entry_classes: Mapping[str, str], floor: float) -> dict:
    """L-type backlog flags on the busy entries and the network p95: report only, never a gate label.

    Arms on the same draw keys use the CRN-paired bound ``paired_t_guard`` on the per-key differences; arms on
    disjoint keys use ``two_sample_t_guard``.
    """
    _check_classes(entry_classes)
    paired = set(candidate_stats) == set(reference_stats)
    keys_c, keys_r = sorted(candidate_stats), sorted(reference_stats)
    flags = {}
    for edge in [*(e for e, kind in sorted(entry_classes.items()) if kind == "busy"), NETWORK]:
        name = f"entry_p95:{edge}"
        if paired:
            flag = paired_t_guard([_stat(candidate_stats[k], name) - _stat(reference_stats[k], name) for k in keys_c],
                                  floor)
        else:
            flag = two_sample_t_guard([_stat(candidate_stats[k], name) for k in keys_c],
                                      [_stat(reference_stats[k], name) for k in keys_r], floor)
        flags["network" if edge == NETWORK else edge] = flag
    return {"rule": "CRN-paired" if paired else "two-sample", "flags": flags,
            "flagged": [name for name, flag in flags.items() if flag["fires"]], "blocking": False,
            "claim": "A flag only (decision 6): it never blocks and never labels a case."}


def evaluate_guards(candidate_stats: Mapping[str, dict], reference_stats: Mapping[str, dict], *,
                    margins: Mapping[str, float], entry_classes: Mapping[str, str], floors: Mapping[str, float],
                    case: dict | None = None) -> dict:
    """The H3 guards, the init52 K-mean guard and the backlog flags of one paired case.

    Per draw key the stats hold ``entry_p95``, ``brakings``, ``post_window`` and ``init52``. ``margins`` holds
    ``network_p95``, ``brakings``, ``post_window``, ``init52``, optional per-edge ``entry_p95:<edge>`` and, when
    ``case`` (an ``evaluate_case_v3`` result) is given, ``totals13`` and ``main130`` bounds on its dCRPS.
    ``floors`` holds ``quiet_entry`` and ``paired_entry`` (the backlog flag's floor) in seconds. ``label`` is the
    first failing guard label; the backlog flags never set it. Row growth (N1) and every other gate step are in
    ``m2_stage.evaluate_stage``, the one production verdict.
    """
    if set(candidate_stats) != set(reference_stats):
        raise ValueError(f"Unpaired draw keys: {sorted(set(candidate_stats) ^ set(reference_stats))}")
    keys = sorted(candidate_stats)
    candidate, reference = [candidate_stats[k] for k in keys], [reference_stats[k] for k in keys]
    h3 = h3_guards(candidate, reference, margins=margins, entry_classes=entry_classes,
                   quiet_floor=floors["quiet_entry"])
    results = {name: {**result, "guard": "entry" if name.startswith("entry") else name}
               for name, result in h3["guards"].items()}  # entry_quiet:<edge>, entry:<edge>, entry:network
    results["init52"] = {**kmean_difference_guard(_values(candidate, "init52"), _values(reference, "init52"),
                                                   margins["init52"]), "guard": "init52"}
    if case is not None:
        for name in ("totals13", "main130"):
            value = case[name]["T"]
            results[name] = {"rule": "dCRPS > margin", "draws": case[name]["draws"], "T": value,
                             "t": float(value), "margin": margins[name], "fires": value > margins[name],
                             "guard": name}
    for result in results.values():
        result["label"] = LABELS[result["guard"]]
    fired = [name for name, result in results.items() if result["fires"]]
    order = list(dict.fromkeys(LABELS.values()))
    labels = sorted({results[name]["label"] for name in fired}, key=order.index)
    backlog = backlog_flags(candidate_stats, reference_stats, entry_classes=entry_classes,
                            floor=floors["paired_entry"])
    return {"draws": len(keys), "draw_keys": keys, "guards": results, "fired": fired,
            "label": labels[0] if labels else None, "flags": backlog["flags"], "flagged": backlog["flagged"],
            "claim": "Reject-only guards with provisional margins; a guard that does not fire certifies nothing. "
                     "The backlog flags never block (decision 6)."}
