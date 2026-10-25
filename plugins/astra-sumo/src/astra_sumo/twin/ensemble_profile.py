"""M1 ensemble profile (M2 protocol v3): what of a single-run Calibrate profile survives across panel draws.

Pure functions over per-draw profile dicts (``count_profile.profile_core`` output, or its compact form: hotspots with
``id``, ``kind``, ``weight`` and the lead co-anchor or BAL field, plus field-balance rows). Hotspots are keyed by
their lead element, because co-anchor hotspot ids are realization-specific; the ids themselves go to a per-draw
annex. The panel draws are the ensemble; the nominal run is reported beside it and never enters a statistic.
Accounting over simulated passages under the supplied model: a stable element is a stable accounting location, not
a cause.
"""

from __future__ import annotations

import math
import statistics
from collections import Counter
from collections.abc import Mapping, Sequence

ABSENT_RANK = 99


def lead_element(hotspot: Mapping) -> str:
    """``field:<field>`` for a field-balance hotspot, otherwise the element of the lead row's top co-anchor."""
    if hotspot["kind"] == "BAL":
        return "field:" + hotspot["rows"][0]["field"]
    return hotspot["lead"]["coanchors"][0]["element"]


def element_weights(profile: Mapping) -> Counter:
    """Hotspot weights summed by lead element."""
    weights: Counter = Counter()
    for hotspot in profile["hotspots"]:
        weights[lead_element(hotspot)] += hotspot["weight"]
    return weights


def _ranking(weights: Mapping[str, int]) -> dict[str, int]:
    ordered = sorted((element for element, weight in weights.items() if weight > 0),
                     key=lambda element: (-weights[element], element))
    return {element: rank for rank, element in enumerate(ordered, 1)}


def _binomial_upper_tail(k: int, n: int, p: float) -> float:
    return sum(math.comb(n, j) * p ** j * (1 - p) ** (n - j) for j in range(k, n + 1))


def clopper_pearson_lower(k: int, n: int, *, confidence: float = 0.95) -> float:
    """One-sided Clopper-Pearson lower bound p_L with P(Bin(n, p_L) >= k) = 1 - confidence (0.0 when k = 0).

    Bisection on the exact binomial tail, which increases with p.
    """
    if n < 1 or not 0 <= k <= n or not 0 < confidence < 1:
        raise ValueError("Use 0 <= k <= n, n >= 1 and a confidence in (0, 1)")
    if k == 0:
        return 0.0
    low, high = 0.0, 1.0
    for _ in range(100):
        mid = (low + high) / 2
        if _binomial_upper_tail(k, n, mid) < 1 - confidence:
            low = mid
        else:
            high = mid
    return (low + high) / 2


def robustness_label(top_draws: int, draws: int) -> str:
    """``robust`` in the top on all but at most one draw, ``realization_specific`` on at most a third, else
    ``unsettled``."""
    if top_draws >= draws - 1:
        return "robust"
    if top_draws <= draws // 3:
        return "realization_specific"
    return "unsettled"


def ensemble_hotspots(profiles: Sequence[Mapping], *, top: int = 5, nominal: Mapping | None = None,
                      min_present: int = 2, limit: int = 15) -> list[dict]:
    """Lead elements across the panel draws: top-``top`` frequency with its Clopper-Pearson lower bound, weight
    median [min, max] and median rank over all draws (an absent element weighs 0 and ranks 99).

    Ranks are per draw by descending element weight, ties broken by element name. ``nominal`` is reported only.
    Elements present (weight > 0) on fewer than ``min_present`` draws are left out; at most ``limit`` are returned.
    """
    if not profiles:
        raise ValueError("The ensemble profile needs at least one draw")
    draws = len(profiles)
    weights = [element_weights(profile) for profile in profiles]
    ranks = [_ranking(w) for w in weights]
    nominal_weights = element_weights(nominal) if nominal is not None else None
    nominal_ranks = _ranking(nominal_weights) if nominal_weights is not None else None
    rows = []
    for element in sorted({element for w in weights for element in w}):
        values = [w.get(element, 0) for w in weights]
        placed = [r.get(element, ABSENT_RANK) for r in ranks]
        top_draws = sum(rank <= top for rank in placed)
        rows.append({"element": element, "top_draws": top_draws,
                     "top_freq_lower95": clopper_pearson_lower(top_draws, draws),
                     "present_draws": sum(value > 0 for value in values),
                     "weight_median": statistics.median(values), "weight_min": min(values),
                     "weight_max": max(values), "median_rank": statistics.median(placed),
                     "nominal_weight": nominal_weights.get(element, 0) if nominal_weights is not None else None,
                     "nominal_rank": nominal_ranks.get(element) if nominal_ranks is not None else None,
                     "robustness": robustness_label(top_draws, draws)})
    rows.sort(key=lambda row: (-row["top_draws"], -row["weight_median"], row["element"]))
    return [row for row in rows if row["present_draws"] >= min_present][:limit]


def hotspot_id_frequency(profiles: Sequence[Mapping], *, top: int = 5) -> dict[str, int]:
    """For the per-draw annex: how many draws list each hotspot id (weight > 0) among their first ``top``.

    Every id with weight > 0 on some draw is listed, so an id that never reaches the top counts 0.
    """
    counts: Counter = Counter()
    seen: set[str] = set()
    for profile in profiles:
        listed = [hotspot["id"] for hotspot in profile["hotspots"] if hotspot["weight"] > 0]
        seen.update(listed)
        counts.update(listed[:top])
    return {hotspot: counts[hotspot] for hotspot in sorted(seen, key=lambda h: (-counts[h], h))}


def merge_corridor(weights: Mapping[str, int], groups: Mapping[str, Sequence[str]]) -> Counter:
    """Element weights with each declared group (e.g. consecutive route edges) summed under the group's name."""
    member_of: dict[str, str] = {}
    for name, members in groups.items():
        for member in members:
            if member in member_of:
                raise ValueError(f"{member} is declared in two groups")
            member_of[member] = name
    clash = sorted(set(groups) & (set(weights) - set(member_of)))
    if clash:
        raise ValueError(f"Group names {clash} collide with unmerged elements")
    merged: Counter = Counter()
    for element, weight in weights.items():
        merged[member_of.get(element, element)] += weight
    return merged


def _range(values: Sequence[float]) -> dict:
    return {"median": statistics.median(values), "min": min(values), "max": max(values)}


def ensemble_field_balance(balance_rows_by_draw: Sequence[Sequence[Mapping]], *,
                           real_main_totals: Mapping[str, int],
                           draw_main_totals: Sequence[Mapping[str, int]]) -> dict:
    """Per field: median [min, max] of r_main, r_init and post_window over the draws, r_main sign stability, and
    whether the real main-window total lies above or below every draw's total (``real_outside``).

    Fields are keyed by their alias (``0119Z4``); ``draw_main_totals`` holds each draw's E1 main-window totals.
    """
    if not balance_rows_by_draw or len(balance_rows_by_draw) != len(draw_main_totals):
        raise ValueError("Give balance rows and main totals for the same, non-empty set of draws")
    by_draw = [{row["field"]: row for row in rows} for rows in balance_rows_by_draw]
    fields = sorted(by_draw[0])
    if any(sorted(rows) != fields for rows in by_draw):
        raise ValueError("Every draw must balance the same fields")
    result = {}
    for field in fields:
        r_main = [rows[field]["r_main"] for rows in by_draw]
        totals = [totals[field] for totals in draw_main_totals]
        real = real_main_totals[field]
        result[field] = {"r_main": _range(r_main), "r_init": _range([rows[field]["r_init"] for rows in by_draw]),
                         "post_window": _range([rows[field]["post_window"] for rows in by_draw]),
                         "sign_stable": all(x < 0 for x in r_main) or all(x > 0 for x in r_main)
                         or all(x == 0 for x in r_main),
                         "real_main_total": real, "draw_main_total": {"min": min(totals), "max": max(totals)},
                         "real_outside": "above" if real > max(totals) else "below" if real < min(totals) else None}
    return result
