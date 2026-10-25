"""Spread of judged runs that share one parent: per-row and TAE spread across seeds or keyed draws (M2 pilot).

Pure functions over judge results (``score`` and ``rows`` with measured counts and residuals) and canonical
measurements. They describe how much the measured counts move under a declared perturbation. They do not say that
a perturbation is a statistical null, and they set no gate threshold: values derived here are diagnostics until a
protocol freezes them.
"""

from __future__ import annotations

import math
from statistics import mean, stdev

SCORES = ("tae_main", "init52_tae", "all182_tae", "sse", "max", "exact")


def _summary(values: list[float]) -> dict:
    return {"values": values, "mean": round(mean(values), 6), "sd": round(stdev(values), 6) if len(values) > 1 else 0.0,
            "min": min(values), "max": max(values), "range": max(values) - min(values)}


def score_spread(reference: dict, draws: list[dict]) -> dict:
    """Each score across draws, and its difference from the reference run (d_k = draw_k - reference)."""
    result = {}
    for name in SCORES:
        values = [draw["score"][name] for draw in draws]
        differences = [value - reference["score"][name] for value in values]
        result[name] = {"reference": reference["score"][name], **_summary(values),
                        "differences": differences, "max_abs_difference": max(map(abs, differences))}
    return result


def row_spread(reference: dict, draws: list[dict], *, init_rows: frozenset[str] = frozenset()) -> dict:
    """Per-row movement of the measured counts against the reference run and across the draws themselves."""
    rows = sorted(reference["rows"])
    if any(sorted(draw["rows"]) != rows for draw in draws):
        raise ValueError("Every run must score the same rows.")
    moved, between, growth = {}, set(), []
    for row in rows:
        base = reference["rows"][row]
        measured = [draw["rows"][row]["measured"] for draw in draws]
        growth += [abs(draw["rows"][row]["diff"]) - abs(base["diff"]) for draw in draws]
        if len(set(measured)) > 1:
            between.add(row)
        if any(value != base["measured"] for value in measured):
            moved[row] = {"reference": base["measured"], "draws": measured,
                          "max_move": max(abs(value - base["measured"]) for value in measured)}

    def split(names) -> dict:
        names = set(names)
        return {"all": len(names), "main": len(names - init_rows), "init": len(names & init_rows)}

    return {"rows": split(rows), "changed_vs_reference": split(moved), "changed_between_draws": split(between),
            "max_abs_residual_growth": max(growth, default=0),
            "max_count_move": max((row["max_move"] for row in moved.values()), default=0),
            "moved": dict(sorted(moved.items(), key=lambda item: (-item[1]["max_move"], item[0])))}


def _quantile(ordered: list[float], share: float) -> float:
    return round(ordered[min(len(ordered) - 1, int(share * len(ordered)))], 3)


def passage_spread(reference: dict, draws: list[dict]) -> dict:
    """Per draw: vehicles that change counted bin or detector or gain or lose a passage, and how far each counted
    passage moves in time against the reference (the amplification of the input perturbation)."""
    def passages(measurement: dict) -> dict:
        return {row[0]: (row[1], row[2], row[3]) for row in measurement["passages"]["full"]}

    base, rows = passages(reference), []
    for draw in draws:
        other = passages(draw)
        moved = sorted(abs(other[v][2] - base[v][2]) for v in base.keys() & other.keys())
        rows.append({"bin_changed": sum(base.get(v, (None,) * 2)[:2] != other.get(v, (None,) * 2)[:2]
                                        for v in base.keys() | other.keys()),
                     "detector_changed": sum(v in other and base[v][0] != other[v][0] for v in base),
                     "lost": len(base.keys() - other.keys()), "gained": len(other.keys() - base.keys()),
                     "early_departures": len(draw["passages"]["early_departures"]),
                     "time_shift_s": {"p50": _quantile(moved, .5), "p90": _quantile(moved, .9),
                                      "p99": _quantile(moved, .99), "max": round(moved[-1], 3),
                                      "share_le_0.5": round(sum(x <= .5 + 1e-9 for x in moved) / len(moved), 4),
                                      "share_gt_10": round(sum(x > 10 for x in moved) / len(moved), 4)}
                     if moved else None})
    return {"reference_passages": len(base), "per_draw": rows}


def provisional_margins(reference: dict, draws: list[dict]) -> dict:
    """The design's M2a formulas evaluated on a panel, for diagnosis only (no calibration nulls, nothing frozen)."""
    growth_all = [abs(draw["rows"][row]["diff"]) - abs(reference["rows"][row]["diff"])
                  for draw in draws for row in reference["rows"]]
    tae = [abs(draw["score"]["tae_main"] - reference["score"]["tae_main"]) for draw in draws]
    init = [draw["score"]["init52_tae"] - reference["score"]["init52_tae"] for draw in draws]
    return {"g_panel_term": 1 + max(tae), "m_row": max(2, 1 + max(growth_all)), "m_init": max(2, 1 + max(init)),
            "rule": "design_v2 M2a: g = max(g_min, 1 + max_k |dTAE_k|, 1 + max over nulls ...); m_row = max(2, 1 + "
                    "max |r_k| - |r_0|); m_init = max(2, 1 + max dTAE_init); panel terms only"}


def t_upper_bound(differences: list[float], t_critical: float) -> float:
    """mean + t * sd / sqrt(K), the design's UCB; equals the mean when sd is 0."""
    if len(differences) < 2:
        raise ValueError("The t-bound needs at least two draws.")
    return mean(differences) + t_critical * stdev(differences) / math.sqrt(len(differences))
