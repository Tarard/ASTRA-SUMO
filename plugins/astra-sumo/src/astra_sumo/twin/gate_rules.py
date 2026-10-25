"""Exact small-K decision rules on paired candidate-minus-parent differences (M2 protocol v2).

d_k = S_c,k - S_p,k is the candidate's score minus the parent's on draw key k (common random numbers). When the keys
are drawn independently and after the candidate is fixed, d_1..d_K are i.i.d. given the candidate and the target.

The improvement rule is a one-sided sign test of H0: P(d <= -g) <= 1/2, i.e. the candidate does not beat the parent by
at least g on a majority of draws. It accepts when at least c(K) of the K differences are <= -g, with c(K) the
smallest count whose binomial tail P(Bin(K, 1/2) >= c) is <= alpha. Under H0 the acceptance probability is at most
that tail whatever the distribution of d (discrete, tied, skewed or heavy-tailed) and however the candidate was
produced, because the bound only uses the independence of fresh keys. On reused keys (a panel the proposer saw) the
same rule is a screening statistic without that bound. Non-inferiority (simplification) is the same test with
threshold d < delta.

The t upper bound and the sign-flip permutation p-value are computed for comparison only: the first needs
approximate normality of d, the second symmetry of d about its mean, and neither is checked with K <= 16 draws.
"""

from __future__ import annotations

import itertools
import math
from fractions import Fraction
from statistics import mean, stdev


def binomial_tail(count: int, draws: int, p: Fraction = Fraction(1, 2)) -> Fraction:
    """P(Bin(draws, p) >= count), exact."""
    if draws < 0 or not 0 <= p <= 1:
        raise ValueError("Use a nonnegative number of draws and a probability.")
    count = max(count, 0)
    return sum((Fraction(math.comb(draws, j)) * p ** j * (1 - p) ** (draws - j)
                for j in range(count, draws + 1)), Fraction(0))


def required_count(draws: int, alpha: float) -> int | None:
    """Smallest c with P(Bin(draws, 1/2) >= c) <= alpha, or None when even c = draws is too likely under H0."""
    for count in range(draws + 1):
        if binomial_tail(count, draws) <= Fraction(alpha).limit_denominator(10 ** 9):
            return count
    return None


def minimum_draws(alpha: float) -> int:
    """Fewest fresh draws for which the all-draws rule has size <= alpha (2^-K <= alpha)."""
    return max(1, math.ceil(math.log2(1.0 / alpha) - 1e-12))


def power(draws: int, alpha: float, success_probability: float) -> float:
    """P(accept) of the sign rule for a candidate whose per-draw P(d <= threshold) is ``success_probability``."""
    count = required_count(draws, alpha)
    if count is None:
        return 0.0
    return float(binomial_tail(count, draws, Fraction(success_probability).limit_denominator(10 ** 9)))


def sign_test(differences: list[float], threshold: float, *, alpha: float, strict: bool = False) -> dict:
    """Count of d_k <= threshold (d_k < threshold when strict) against the exact binomial requirement."""
    draws = len(differences)
    hits = sum((d < threshold) if strict else (d <= threshold) for d in differences)
    required = required_count(draws, alpha)
    return {"rule": "exact one-sided sign test", "draws": draws, "threshold": threshold,
            "comparison": "<" if strict else "<=", "hits": hits, "required": required,
            "p_value": float(binomial_tail(hits, draws)), "size_bound": None if required is None
            else float(binomial_tail(required, draws)), "passes": required is not None and hits >= required,
            "assessable": required is not None}


def improvement(differences: list[float], g: float, *, alpha: float) -> dict:
    """Branch I: at least c(K) draws improve by >= g, and the mean difference is <= -g (a point check)."""
    test = sign_test(differences, -g, alpha=alpha)
    point = mean(differences) <= -g if differences else False
    return {**test, "hypothesis": "H0: P(d <= -g) <= 1/2", "g": g, "mean": mean(differences) if differences else None,
            "mean_le_minus_g": point, "passes": test["passes"] and point}


def noninferiority(differences: list[float], delta: float, *, alpha: float) -> dict:
    """Branch S: at least c(K) draws are worse by less than delta, and the mean difference is < delta."""
    test = sign_test(differences, delta, alpha=alpha, strict=True)
    point = mean(differences) < delta if differences else False
    return {**test, "hypothesis": "H0: P(d < delta) <= 1/2", "delta": delta,
            "mean": mean(differences) if differences else None, "mean_lt_delta": point,
            "passes": test["passes"] and point}


def sign_flip_p_value(differences: list[float], g: float) -> float:
    """Exact one-sided Fisher-Pitman p-value for H0: mean(d) >= -g, valid only if d is symmetric about its mean.

    Fraction of the 2^K sign patterns s with sum(s_k (d_k + g)) <= sum(d_k + g); K is limited to 16.
    """
    shifted = [d + g for d in differences]
    if not shifted or len(shifted) > 16:
        raise ValueError("Use 1 to 16 differences.")
    observed = sum(shifted)
    hits = sum(sum(s * x for s, x in zip(signs, shifted)) <= observed + 1e-9
               for signs in itertools.product((1, -1), repeat=len(shifted)))
    return hits / 2 ** len(shifted)


# One-sided 95% Student t quantiles by degrees of freedom (K - 1).
T_95 = {1: 6.314, 2: 2.920, 3: 2.353, 4: 2.132, 5: 2.015, 6: 1.943, 7: 1.895, 8: 1.860, 9: 1.833, 10: 1.812,
        11: 1.796, 12: 1.782, 13: 1.771, 14: 1.761, 15: 1.753}


def t_upper_bound(differences: list[float]) -> float | None:
    """mean + t_{0.95, K-1} sd / sqrt(K) (design v2's UCB); None outside 2 <= K <= 16."""
    draws = len(differences)
    if not 2 <= draws <= 16:
        return None
    return mean(differences) + T_95[draws - 1] * stdev(differences) / math.sqrt(draws)


def paired_differences(candidate: dict[str, float], parent: dict[str, float]) -> dict[str, float]:
    """d_k = candidate_k - parent_k on the keys both arms share; refuses unpaired keys."""
    if set(candidate) != set(parent):
        raise ValueError(f"Unpaired draw keys: {sorted(set(candidate) ^ set(parent))}")
    return {key: candidate[key] - parent[key] for key in sorted(candidate)}


def row_tae(counts: dict[str, int], target: dict[str, int], rows: list[str]) -> int:
    """Sum over rows of |counts - target|; a missing row is an error, never zero."""
    missing = [row for row in rows if row not in counts or row not in target]
    if missing:
        raise ValueError(f"{len(missing)} rows have no count or target, e.g. {missing[0]}")
    return sum(abs(counts[row] - target[row]) for row in rows)


def _gini_mean_difference(draws: list[dict[str, int]], rows: list[str]) -> float:
    pairs = list(itertools.permutations(range(len(draws)), 2))
    return sum(abs(draws[a][row] - draws[b][row]) for row in rows for a, b in pairs) / len(pairs)


def crps_decomposition(candidate: list[dict[str, int]], parent: list[dict[str, int]], target: dict[str, int],
                       rows: list[str], *, g: float) -> dict:
    """mean TAE = CRPS + GMD/2 per arm (fair ensemble estimator, summed over rows), and the split of d into them.

    CRPS is a proper score: when the target is a draw of the parent, no candidate has a lower expected CRPS, while
    a candidate with less spread than the parent can have a lower expected TAE. ``dispersion_driven`` marks a mean
    TAE gain of at least g whose CRPS part alone is not a gain of g.
    """
    if len(candidate) < 2 or len(parent) < 2:
        return {"assessable": False, "reason": "the dispersion term needs at least two draws per arm"}
    tae_c = mean(row_tae(draw, target, rows) for draw in candidate)
    tae_p = mean(row_tae(draw, target, rows) for draw in parent)
    gmd_c, gmd_p = _gini_mean_difference(candidate, rows), _gini_mean_difference(parent, rows)
    crps_c, crps_p = tae_c - gmd_c / 2, tae_p - gmd_p / 2
    d_tae, d_crps = tae_c - tae_p, crps_c - crps_p
    return {"assessable": True, "mean_tae": [tae_c, tae_p], "gmd": [gmd_c, gmd_p], "crps": [crps_c, crps_p],
            "d_mean_tae": d_tae, "d_crps": d_crps, "d_half_gmd": (gmd_c - gmd_p) / 2,
            "dispersion_driven": d_tae <= -g and d_crps > -g}


def evaluate_case(candidate: dict[str, dict[str, int]], reference: dict[str, dict[str, int]],
                  target: dict[str, int], rows: list[str], *, g: float, delta: float, alpha: float) -> dict:
    """One paired case: the candidate and its reference on the same draw keys against one target (real counts or a
    twin truth). Reports both branches' exact sign tests and, for comparison, the t bound and sign-flip p-value."""
    if set(candidate) != set(reference):
        raise ValueError(f"Unpaired draw keys: {sorted(set(candidate) ^ set(reference))}")
    keys = sorted(candidate)
    d = [row_tae(candidate[k], target, rows) - row_tae(reference[k], target, rows) for k in keys]
    l1 = [sum(abs(candidate[k][row] - reference[k][row]) for row in rows) for k in keys]
    l2 = [math.sqrt(sum((candidate[k][row] - reference[k][row]) ** 2 for row in rows)) for k in keys]
    return {"keys": keys, "d": d, "mean": mean(d), "sd": stdev(d) if len(d) > 1 else 0.0,
            "no_effect": all(candidate[k][row] == reference[k][row] for k in keys for row in rows),
            "improvement": improvement(d, g, alpha=alpha), "noninferiority": noninferiority(d, delta, alpha=alpha),
            "t_ucb": t_upper_bound(d), "sign_flip_p": sign_flip_p_value(d, g) if 1 <= len(d) <= 16 else None,
            "paired_counts_distance": {"l1": l1, "l2": [round(x, 3) for x in l2]},
            "crps": crps_decomposition([candidate[k] for k in keys], [reference[k] for k in keys], target, rows,
                                       g=g)}


GUARD_LABELS = ("unhealthy", "guard", "row_regress", "init")


def verdict(case: dict, *, g: float, dof_lower: bool, guard_failure: str | None = None) -> dict:
    """Protocol v2 gate label of one ``evaluate_case`` result, in the order of the gate table.

    A failed hard step or guard (``guard_failure``, one of GUARD_LABELS) labels the case first, because guards can
    only reject. Then ``no_effect`` (every scored row equals the reference's on every key), branch I (``improved``,
    noted ``dispersion_driven`` when its CRPS part alone is not a gain of g), branch S (``simplified``, only when the
    candidate has fewer timing DoF than the reference) and the descriptive rest: ``not_robust`` (mean d <= -g but
    the sign rule fails), ``worse`` (mean d >= g) or ``not_material``.
    """
    if guard_failure is not None and guard_failure not in GUARD_LABELS:
        raise ValueError(f"Unknown guard label {guard_failure!r}; use one of {GUARD_LABELS}.")
    if guard_failure is not None:
        label = guard_failure
    elif case["no_effect"]:
        label = "no_effect"
    elif case["improvement"]["passes"]:
        label = "improved"
    elif dof_lower and case["noninferiority"]["passes"]:
        label = "simplified"
    elif case["mean"] <= -g:
        label = "not_robust"
    elif case["mean"] >= g:
        label = "worse"
    else:
        label = "not_material"
    note = "dispersion_driven" if label == "improved" and case["crps"].get("dispersion_driven") else None
    return {"label": label, "note": note, "guard_failure": guard_failure, "dof_lower": dof_lower}
