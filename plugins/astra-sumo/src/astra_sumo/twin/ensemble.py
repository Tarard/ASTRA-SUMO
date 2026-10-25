"""Ensemble statistics of M2 protocol v3: objective transforms, the fair CRPS and its exact paired sign-flip test.

An ensemble is a sequence of count dicts over one list of value ids (``keys``). The objective transforms map the 130
main-row counts (15-min rows ``R:<field>@<begin>``) to those values: ``main130`` keeps the rows, ``ms4`` and ``ms3``
sum 4 or 3 consecutive bins per field at a stride of one bin (``W:<field>@<begin>+<width_s>``), and ``totals13`` sums
each field over the main window (``T:<field>``). A target is one count dict (the real counts) or a truth ensemble.

The fair CRPS summed over values (the L1 energy score) is CRPS(E, y) = mean_k |E_k - y|_1 - GMD(E) / 2, with the
Gini mean difference GMD(E) = sum_{k != l} |E_k - E_l|_1 / (m (m - 1)). The rate-bearing statistic is
T = CRPS(candidate, y) - CRPS(reference, y) on paired draw keys (common random numbers). Under a within-pair swap
pattern s in {+1, -1}^K the swapped difference is linear in s, T(s) = sum_k s_k v_k (``crps_contributions``), so the
one-sided p = #{s : T(s) <= T} / 2^K is an exact Fisher-Pitman sign-flip p-value on integer numerators. It is exact
for the sharp null (within each pair the candidate's and the reference's values are exchangeable) when the keys are
fresh, secret and drawn after the candidate, reference, target, objective, g, K and alpha are frozen. On panel keys
the proposer has seen it is a screening statistic with no rate. "Different but not better by g" is a composite null
that the test does not control exactly.

Everything here is explore evidence over simulated counts under the supplied model: no field truth, no promotion.
"""

from __future__ import annotations

import bisect
import itertools
import math
import statistics
from collections.abc import Mapping, Sequence
from fractions import Fraction
from typing import Literal, Union

from . import gate_rules

BIN_S = 900.0
MAIN_BEGIN_S = 3600.0
MAIN_END_S = 12600.0
OBJECTIVES = ("main130", "ms4", "ms3", "totals13")
PRIMARY_OBJECTIVE = "ms4"
CRN_EFFECTIVE_RHO = 0.35
MAX_FLIP_DRAWS = 30
MAX_SWAP_DRAWS = 16

Counts = Mapping[str, int]
Target = Union[Counts, Sequence[Counts]]

CLAIM = ("Explore only: simulated counts under the supplied model. The paired sign-flip p is exact under the sharp "
         "null (within-pair exchangeability) only on fresh secret keys drawn after the freeze; on panel keys it is a "
         "screening statistic with no rate. No field truth, no composite-null control, no promotion authority.")


def _exact(value: float | int | Fraction) -> Fraction:
    """A threshold as an exact fraction; a float such as 0.05 is read as the decimal it denotes."""
    if isinstance(value, float):
        return Fraction(value).limit_denominator(10 ** 9)
    return Fraction(value)


# --- objective transforms ------------------------------------------------------------------------------------------

def parse_row(row_id: str) -> tuple[str, float]:
    """``R:0119Z11@3600`` -> ``("0119Z11", 3600.0)``; anything else is refused."""
    if not isinstance(row_id, str) or not row_id.startswith("R:") or "@" not in row_id:
        raise ValueError(f"Not a count row id: {row_id!r}")
    field, begin = row_id[2:].rsplit("@", 1)
    try:
        begin_s = float(begin)
    except ValueError:
        raise ValueError(f"Not a count row id: {row_id!r}") from None
    if not field or not math.isfinite(begin_s):
        raise ValueError(f"Not a count row id: {row_id!r}")
    return field, begin_s


def main_rows(row_ids: Sequence[str]) -> list[str]:
    """The row ids whose bin starts in [MAIN_BEGIN_S, MAIN_END_S), sorted as strings."""
    return sorted(row for row in row_ids if MAIN_BEGIN_S <= parse_row(row)[1] < MAIN_END_S)


def _count(counts: Counts, row: str) -> int:
    if row not in counts:
        raise ValueError(f"Row {row} has no count; a missing row is unknown, never zero")
    return counts[row]


def _by_field(counts: Counts, rows: Sequence[str]) -> dict[str, dict[float, int]]:
    found: dict[str, dict[float, int]] = {}
    for row in rows:
        field, begin = parse_row(row)
        bins = found.setdefault(field, {})
        if begin in bins:
            raise ValueError(f"Row {row} is listed twice")
        bins[begin] = _count(counts, row)
    return found


def rolling_sums(counts: Counts, rows: Sequence[str], *, width_bins: int, bin_s: float = BIN_S) -> dict[str, int]:
    """Per field (sorted), the sums of ``width_bins`` consecutive bins at a stride of one bin.

    Ids are ``W:<field>@<begin>+<width_bins * bin_s>``. A gap between bins, fewer bins than the width or a row
    without a count is refused: nothing is zero-filled.
    """
    if width_bins < 1:
        raise ValueError("Use a width of at least one bin")
    out: dict[str, int] = {}
    for field, bins in sorted(_by_field(counts, rows).items()):
        begins = sorted(bins)
        if any(abs(b - a - bin_s) > 1e-9 for a, b in zip(begins, begins[1:])):
            raise ValueError(f"Field {field} has non-contiguous bins")
        if len(begins) < width_bins:
            raise ValueError(f"Field {field} has {len(begins)} bins, fewer than the width {width_bins}")
        for i in range(len(begins) - width_bins + 1):
            window = begins[i:i + width_bins]
            out[f"W:{field}@{window[0]:g}+{width_bins * bin_s:g}"] = sum(bins[b] for b in window)
    return out


def field_totals(counts: Counts, rows: Sequence[str]) -> dict[str, int]:
    """``T:<field>`` sums over ``rows``, sorted by field."""
    return {f"T:{field}": sum(bins.values()) for field, bins in sorted(_by_field(counts, rows).items())}


def objective_values(counts: Counts, rows: Sequence[str], objective: str) -> dict[str, int]:
    """The values one objective scores, from the counts on ``rows`` (the 130 main rows)."""
    if objective == "main130":
        return {row: _count(counts, row) for row in rows}
    if objective == "ms4":
        return rolling_sums(counts, rows, width_bins=4)
    if objective == "ms3":
        return rolling_sums(counts, rows, width_bins=3)
    if objective == "totals13":
        return field_totals(counts, rows)
    raise ValueError(f"Unknown objective {objective!r}; use one of {OBJECTIVES}.")


# --- distances and the fair CRPS -----------------------------------------------------------------------------------

def l1(a: Counts, b: Counts, keys: Sequence[str]) -> int:
    return sum(abs(a[key] - b[key]) for key in keys)


def _truths(target: Target) -> list[Counts]:
    truths = [target] if isinstance(target, Mapping) else list(target)
    if not truths:
        raise ValueError("A truth ensemble needs at least one member")
    return truths


def mean_distance(ensemble: Sequence[Counts], target: Target, keys: Sequence[str]) -> Fraction:
    """Mean L1 distance of the members to the target (and over the truth members when it is an ensemble)."""
    truths = _truths(target)
    if not ensemble:
        raise ValueError("An ensemble needs at least one member")
    return Fraction(sum(l1(member, truth, keys) for member in ensemble for truth in truths),
                    len(ensemble) * len(truths))


def gini_mean_difference(ensemble: Sequence[Counts], keys: Sequence[str]) -> Fraction:
    """sum_{i != j} |E_i - E_j|_1 / (m (m - 1))."""
    m = len(ensemble)
    if m < 2:
        raise ValueError("The Gini mean difference needs at least two members")
    pairs = sum(l1(ensemble[i], ensemble[j], keys) for i, j in itertools.combinations(range(m), 2))
    return Fraction(2 * pairs, m * (m - 1))


def fair_crps(ensemble: Sequence[Counts], target: Target, keys: Sequence[str]) -> Fraction:
    """Summed fair CRPS (the L1 energy score): mean distance to the target minus half the GMD."""
    return mean_distance(ensemble, target, keys) - gini_mean_difference(ensemble, keys) / 2


def ensemble_median_distance(ensemble: Sequence[Counts], target: Counts, keys: Sequence[str]) -> Fraction:
    """sum_v |median_k E_k,v - y_v| with the mean of the two middle values when m is even (report only)."""
    if not isinstance(target, Mapping):
        raise ValueError("The ensemble-median distance takes one target count dict")
    if not ensemble:
        raise ValueError("An ensemble needs at least one member")
    return sum((abs(statistics.median([Fraction(member[key]) for member in ensemble]) - target[key])
                for key in keys), Fraction(0))


# --- the paired CRPS sign-flip test --------------------------------------------------------------------------------

def crps_contributions(candidate: Sequence[Counts], reference: Sequence[Counts], target: Target,
                       keys: Sequence[str]) -> tuple[list[int], int]:
    """Integer numerators V_k and the common denominator Q = 2K(K - 1)T of the per-pair contributions v_k = V_k / Q.

    V_k = 2(K - 1) sum_t [D(C_k, y_t) - D(P_k, y_t)]
          - T sum_{l != k} [D(C_k, C_l) + D(C_k, P_l) - D(P_k, P_l) - D(P_k, C_l)],
    with D the L1 distance and T the number of truth members. sum_k V_k / Q equals
    fair_crps(C) - fair_crps(P), and for every swap pattern s, sum_k s_k V_k / Q equals the CRPS difference of the
    ensembles with C_k and P_k exchanged wherever s_k = -1.
    """
    size, truths = len(candidate), _truths(target)
    if size != len(reference) or size < 2:
        raise ValueError("Use two paired ensembles of equal size, at least two draws each")
    members = len(truths)
    to_c = [sum(l1(c, truth, keys) for truth in truths) for c in candidate]
    to_p = [sum(l1(p, truth, keys) for truth in truths) for p in reference]
    cc = [[l1(a, b, keys) for b in candidate] for a in candidate]
    cp = [[l1(a, b, keys) for b in reference] for a in candidate]
    pp = [[l1(a, b, keys) for b in reference] for a in reference]
    numerators = []
    for k in range(size):
        within = sum(cc[k][j] + cp[k][j] - pp[k][j] - cp[j][k] for j in range(size) if j != k)
        numerators.append(2 * (size - 1) * (to_c[k] - to_p[k]) - members * within)
    return numerators, 2 * size * (size - 1) * members


def _signed_sums(values: Sequence[int]) -> list[int]:
    sums = [0]
    for value in values:
        sums = [s + value for s in sums] + [s - value for s in sums]
    return sums


def sign_flip_lower_p(numerators: Sequence[int]) -> Fraction:
    """Exact one-sided p = #{s in {+1, -1}^K : sum_k s_k V_k <= sum_k V_k} / 2^K.

    The identity pattern is included and ties count against the candidate. Meet in the middle: the signed sums of
    each half are enumerated, one half is sorted and counted with bisect, so 1 <= K <= 30 is exact.
    """
    values = list(numerators)
    if not 1 <= len(values) <= MAX_FLIP_DRAWS:
        raise ValueError(f"Exact enumeration supports 1 to {MAX_FLIP_DRAWS} pairs")
    if any(isinstance(v, bool) or not isinstance(v, int) for v in values):
        raise ValueError("The sign-flip test takes integer numerators")
    observed = sum(values)
    half = len(values) // 2
    right = sorted(_signed_sums(values[half:]))
    hits = sum(bisect.bisect_right(right, observed - left) for left in _signed_sums(values[:half]))
    return Fraction(hits, 2 ** len(values))


def _paired(candidate: Mapping[str, Counts], reference: Mapping[str, Counts]) -> list[str]:
    if set(candidate) != set(reference):
        raise ValueError(f"Unpaired draw keys: {sorted(set(candidate) ^ set(reference))}")
    return sorted(candidate)


def paired_crps_test(candidate: Mapping[str, Counts], reference: Mapping[str, Counts], target: Target,
                     keys: Sequence[str], *, g: float, alpha: float) -> dict:
    """The rate-bearing test: accept iff 2^-K <= alpha, the exact sign-flip p <= alpha and T <= -g.

    ``candidate`` and ``reference`` map the same draw keys to count dicts over ``keys``. ``d_mean_tae`` =
    ``d_crps`` + ``d_half_gmd`` splits the mean TAE change; ``tae_only`` marks a TAE gain of g that the CRPS does not
    carry (a sharpening, which the CRPS does not reward).
    """
    draw_keys = _paired(candidate, reference)
    c, p = [candidate[key] for key in draw_keys], [reference[key] for key in draw_keys]
    numerators, denominator = crps_contributions(c, p, target, keys)
    size = len(draw_keys)
    statistic = Fraction(sum(numerators), denominator)
    p_value = sign_flip_lower_p(numerators)
    alpha_q, g_q = _exact(alpha), _exact(g)
    min_p = Fraction(1, 2 ** size)
    d_mean_tae = mean_distance(c, target, keys) - mean_distance(p, target, keys)
    d_half_gmd = (gini_mean_difference(c, keys) - gini_mean_difference(p, keys)) / 2
    assessable = min_p <= alpha_q
    return {"rule": "exact one-sided paired sign-flip test on T = fairCRPS(candidate) - fairCRPS(reference)",
            "draws": size, "draw_keys": draw_keys, "g": g, "alpha": alpha,
            "T": statistic, "t": float(statistic), "numerators": numerators, "denominator": denominator,
            "p": p_value, "p_value": float(p_value), "min_p": min_p,
            "size_bound": Fraction(math.floor(alpha_q * 2 ** size), 2 ** size), "assessable": assessable,
            "passes": assessable and p_value <= alpha_q and statistic <= -g_q,
            "d_mean_tae": d_mean_tae, "d_half_gmd": d_half_gmd, "d_crps": statistic,
            "tae_only": d_mean_tae <= -g_q and statistic > -g_q}


# --- the paired energy distance and the unpaired test (report only) ------------------------------------------------

def energy_pair_terms(candidate: Sequence[Counts], reference: Sequence[Counts],
                      keys: Sequence[str]) -> tuple[list[list[int]], int]:
    """Symmetric A with A_kl = 2[D(C_k, P_l) + D(C_l, P_k)] - 2 D(C_k, C_l) - 2 D(P_k, P_l), and 2K(K - 1).

    The paired energy distance (same-key cross terms excluded) is sum_{k<l} A_kl / (2K(K - 1)); under a swap pattern
    s it is sum_{k<l} s_k s_l A_kl / (2K(K - 1)), because swapping one member of a pair turns A_kl into -A_kl.
    """
    size = len(candidate)
    if size != len(reference) or size < 2:
        raise ValueError("Use two paired ensembles of equal size, at least two draws each")
    terms = [[0] * size for _ in range(size)]
    for k, j in itertools.combinations(range(size), 2):
        value = (2 * (l1(candidate[k], reference[j], keys) + l1(candidate[j], reference[k], keys))
                 - 2 * l1(candidate[k], candidate[j], keys) - 2 * l1(reference[k], reference[j], keys))
        terms[k][j] = terms[j][k] = value
    return terms, 2 * size * (size - 1)


def _swap_hits(terms: list[list[int]]) -> int:
    """#{s : sum_{k<l} s_k s_l A_kl >= observed} over all 2^K patterns, by a Gray code with s_0 fixed."""
    size = len(terms)
    observed = sum(terms[k][j] for k, j in itertools.combinations(range(size), 2))
    signs = [1] * size
    field = [sum(terms[k][j] for j in range(size) if j != k) for k in range(size)]
    value, hits = observed, 1
    for step in range(1, 2 ** (size - 1)):
        flip = (step & -step).bit_length()
        old = signs[flip]
        value -= 2 * old * field[flip]
        for j in range(size):
            if j != flip:
                field[j] -= 2 * old * terms[j][flip]
        signs[flip] = -old
        hits += value >= observed
    return 2 * hits  # the form is even in s


def paired_energy_test(candidate: Mapping[str, Counts], reference: Mapping[str, Counts],
                       keys: Sequence[str]) -> dict:
    """Paired L1 energy distance and its exact within-pair swap p = #{s : ED(s) >= ED} / 2^K (2 <= K <= 16).

    A direction-free "differs from the reference" diagnostic, used for positive qualification; never a gate.
    """
    draw_keys = _paired(candidate, reference)
    size = len(draw_keys)
    if not 2 <= size <= MAX_SWAP_DRAWS:
        raise ValueError(f"The swap enumeration supports 2 to {MAX_SWAP_DRAWS} pairs")
    terms, denominator = energy_pair_terms([candidate[k] for k in draw_keys], [reference[k] for k in draw_keys],
                                           keys)
    ed = Fraction(sum(terms[k][j] for k, j in itertools.combinations(range(size), 2)), denominator)
    p_value = Fraction(_swap_hits(terms), 2 ** size)
    return {"rule": "paired L1 energy distance, exact within-pair swap p (direction-free, report only)",
            "draws": size, "draw_keys": draw_keys, "ed": ed, "ed_float": float(ed), "p": p_value,
            "p_value": float(p_value), "min_p": Fraction(2, 2 ** size)}


def unpaired_crps_test(candidate: Sequence[Counts], reference: Sequence[Counts], target: Target,
                       keys: Sequence[str], *, g: float, alpha: float, max_splits: int = 20000) -> dict:
    """Label permutation of T = fairCRPS(A) - fairCRPS(B) over all C(nA + nB, nA) splits, the identity included.

    Valid only when every draw comes from a fresh, independent key and the reference ensemble is one the proposer
    has not seen; use it only when pairing is impossible. More than ``max_splits`` splits are refused: there is no
    random fallback.
    """
    pooled = list(candidate) + list(reference)
    n_a, n = len(candidate), len(pooled)
    if n_a < 2 or n - n_a < 2:
        raise ValueError("Each arm needs at least two draws")
    splits = math.comb(n, n_a)
    if splits > max_splits:
        raise ValueError(f"{splits} splits exceed max_splits={max_splits}; there is no random fallback")
    truths = _truths(target)
    to_y = [sum(l1(member, truth, keys) for truth in truths) for member in pooled]
    pair = [[l1(a, b, keys) for b in pooled] for a in pooled]

    def crps(members: Sequence[int]) -> Fraction:
        m = len(members)
        within = sum(pair[i][j] for i, j in itertools.combinations(members, 2))
        return Fraction(sum(to_y[i] for i in members), m * len(truths)) - Fraction(within, m * (m - 1))

    def split_statistic(chosen: tuple[int, ...]) -> Fraction:
        members = set(chosen)
        return crps(chosen) - crps(tuple(i for i in range(n) if i not in members))

    observed = split_statistic(tuple(range(n_a)))
    hits = sum(split_statistic(chosen) <= observed for chosen in itertools.combinations(range(n), n_a))
    p_value = Fraction(hits, splits)
    return {"rule": "unpaired label permutation of the fair-CRPS difference (fresh independent keys, unseen "
                    "reference only)",
            "draws": [n_a, n - n_a], "T": observed, "t": float(observed), "splits": splits, "p": p_value,
            "p_value": float(p_value), "min_p": Fraction(1, splits),
            "passes": p_value <= _exact(alpha) and observed <= -_exact(g)}


# --- CRN regime and confirmation size ------------------------------------------------------------------------------

def rekey_sd(scores: Sequence[float]) -> float:
    """Population sd of the n(n - 1) ordered differences s_i - s_j between independent parent draws (no CRN)."""
    values = list(scores)
    if len(values) < 2:
        raise ValueError("The re-key sd needs at least two draws")
    return statistics.pstdev([a - b for a, b in itertools.permutations(values, 2)])


def crn_ratio(differences: Sequence[float], rekey_sd: float) -> float:
    """rho = sd(d) / re-key sd: how much of the no-CRN spread survives in the paired differences."""
    if rekey_sd <= 0:
        raise ValueError("The re-key sd must be positive")
    return statistics.stdev(differences) / rekey_sd


def confirmation_draws(rho: float) -> int:
    """K for a confirmation: 8 fresh keys when the panel shows effective CRN (rho <= 0.35), otherwise 16."""
    return 8 if rho <= CRN_EFFECTIVE_RHO else 16


# --- one v3 case and its gate label --------------------------------------------------------------------------------

def _transformed(counts_by_key: Mapping[str, Counts], rows: Sequence[str], objective: str) -> dict[str, dict]:
    return {key: objective_values(counts, rows, objective) for key, counts in counts_by_key.items()}


def _transformed_target(target: Target, rows: Sequence[str], objective: str) -> Target:
    if isinstance(target, Mapping):
        return objective_values(target, rows, objective)
    return [objective_values(member, rows, objective) for member in _truths(target)]


def _objective_keys(target: Target) -> list[str]:
    return list(_truths(target)[0])


def evaluate_case_v3(candidate: Mapping[str, Counts], reference: Mapping[str, Counts], target: Target,
                     rows: Sequence[str], *, g: float, alpha: float, delta: float,
                     objective: str = PRIMARY_OBJECTIVE) -> dict:
    """One paired case on the counts of ``rows``: the primary test on ``objective`` plus everything v3 reports.

    ``main130`` and ``totals13`` are the paired CRPS results that guards N4 and N3 read. ``v2_sign_rule`` and
    ``noninferiority`` are v2's exact sign rules on the per-key TAE on the objective (branch S reads the second).
    ``energy`` and the median distances are report only.
    """
    draw_keys = _paired(candidate, reference)
    results = {}
    for name in dict.fromkeys((objective, "main130", "totals13")):
        target_values = _transformed_target(target, rows, name)
        keys = _objective_keys(target_values)
        c, p = _transformed(candidate, rows, name), _transformed(reference, rows, name)
        results[name] = (c, p, target_values, keys, paired_crps_test(c, p, target_values, keys, g=g, alpha=alpha))
    c, p, target_values, keys, primary = results[objective]
    truths = _truths(target_values)
    per_key_d = [Fraction(sum(l1(c[key], truth, keys) - l1(p[key], truth, keys) for truth in truths), len(truths))
                 for key in draw_keys]
    medians = None
    if isinstance(target, Mapping):
        medians = {name: {"candidate": ensemble_median_distance([cc[k] for k in draw_keys], y, ks),
                          "reference": ensemble_median_distance([pp[k] for k in draw_keys], y, ks)}
                   for name, (cc, pp, y, ks, _) in results.items()}
    energy = (paired_energy_test(c, p, keys) if 2 <= len(draw_keys) <= MAX_SWAP_DRAWS else None)
    return {"objective": objective, "values": len(keys), "draw_keys": draw_keys, "g": g, "alpha": alpha,
            "delta": delta, "primary": primary, "energy": energy, "per_key_d": per_key_d,
            "v2_sign_rule": gate_rules.improvement(per_key_d, g, alpha=alpha),
            "noninferiority": gate_rules.noninferiority(per_key_d, delta, alpha=alpha),
            "main130": results["main130"][4], "totals13": results["totals13"][4], "median_distance": medians,
            "no_effect": all(_count(candidate[key], row) == _count(reference[key], row)
                             for key in draw_keys for row in rows),
            "claim": CLAIM}


GUARD_LABELS_V3 = ("unhealthy", "guard", "row_regress", "init", "level_regress", "fine_regress")


def verdict_v3(case: dict, *, g: float, delta: float, dof_lower: bool, guard_failure: str | None = None,
               mode: Literal["screen", "confirm"] = "screen") -> dict:
    """Protocol v3 gate label of one ``evaluate_case_v3`` result, in the order of the gate table.

    A failed guard (one of GUARD_LABELS_V3) labels the case first, because guards only reject. Then ``no_effect``,
    ``improved`` (the primary test passes), ``simplified`` (fewer timing DoF, v2's non-inferiority rule and
    T < delta), and the descriptive rest: ``not_significant`` (T <= -g without the test), ``worse`` (T >= g) or
    ``not_material``. A rate (alpha) is attached only to an ``improved`` label in confirmation mode.
    """
    if mode not in ("screen", "confirm"):
        raise ValueError(f"Unknown mode {mode!r}; use 'screen' or 'confirm'.")
    if guard_failure is not None and guard_failure not in GUARD_LABELS_V3:
        raise ValueError(f"Unknown guard label {guard_failure!r}; use one of {GUARD_LABELS_V3}.")
    primary = case["primary"]
    statistic, g_q, delta_q = primary["T"], _exact(g), _exact(delta)
    if guard_failure is not None:
        label = guard_failure
    elif case["no_effect"]:
        label = "no_effect"
    elif primary["passes"]:
        label = "improved"
    elif dof_lower and case["noninferiority"]["passes"] and statistic < delta_q:
        label = "simplified"
    elif statistic <= -g_q:
        label = "not_significant"
    elif statistic >= g_q:
        label = "worse"
    else:
        label = "not_material"
    return {"label": label, "note": "tae_only" if primary["tae_only"] and label != "improved" else None,
            "rate": "alpha" if mode == "confirm" and label == "improved" else "none", "mode": mode,
            "guard_failure": guard_failure, "dof_lower": dof_lower}
