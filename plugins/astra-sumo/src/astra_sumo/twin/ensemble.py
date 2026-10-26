"""Ensemble statistics of M2 protocol v3: objective transforms, the fair CRPS and its exact paired sign-flip test.

An ensemble is a sequence of count dicts over one list of value ids (``keys``). The objective transforms map the 130
main-row counts (15-min rows ``R:<field>@<begin>``) to those values: ``main130`` keeps the rows, ``ms4`` and ``ms3``
sum 4 or 3 consecutive bins per field at a stride of one bin (``W:<field>@<begin>+<width_s>``), and ``totals13`` sums
each field over the main window (``T:<field>``). A target is one count dict (the real counts) or a truth ensemble.

The fair CRPS summed over values (the L1 energy score) is CRPS(E, y) = mean_k |E_k - y|_1 - GMD(E) / 2, with the
Gini mean difference GMD(E) = sum_{k != l} |E_k - E_l|_1 / (m (m - 1)). The statistic is
T = CRPS(candidate, y) - CRPS(reference, y).

The rate-bearing test (amendment A1.3) is unpaired: candidate and reference run on DISJOINT fresh keys, K each, and
the one-sided p = #{A : T(A) <= T} / C(2K, K) counts every relabeling A of the pooled 2K draws, the observed one
included. With equal arms T(A) is linear in the labels (``unpaired_crps_contributions``), so the count is an exact
subset-sum count by meet in the middle, for K = 16 as well. Under the sharp null that both arms have the same
distribution the pooled draws are exchangeable by construction when the keys are disjoint, secret and fresh, so the
p-value is exact given the frozen target. Exposed (public or reused) keys let a split be chosen after the draws are
seen, which voids that; the stage labels the exchangeability from the run records' key evidence.

The paired common-random-numbers test is reported only. Under a within-pair swap pattern s in {+1, -1}^K the swapped
difference is linear in s, T(s) = sum_k s_k v_k (``crps_contributions``), and p = #{s : T(s) <= T} / 2^K is an exact
sign-flip p-value only if the paired values are exchangeable within each pair. Equal marginals under CRN do not give
that: P = 20 U, C = 20 ((U - 1) mod 100) has identical marginals and the paired rule accepts it at about 92%.
"Different but not better by g" is a composite null that neither test controls. The gate verdict itself lives in
``m2_stage``.

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
MAX_UNPAIRED_DRAWS = 16

Counts = Mapping[str, int]
Target = Union[Counts, Sequence[Counts]]

CLAIM = ("Explore only: simulated counts under the supplied model. The paired sign-flip p is reported only: it "
         "assumes within-pair exchangeability under CRN, which fresh keys do not establish (amendment A1.3). The "
         "rate-bearing test is the unpaired permutation on disjoint fresh keys (m2_stage). No field truth, no "
         "composite-null control, no promotion authority.")
PAIRED_EXCHANGEABILITY = ("assumed, not established: within-pair swap invariance of the paired draws; equal "
                          "marginals under CRN do not imply it (report only, A1.3)")


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
    """The v3 paired rule, reported only since A1.3: ``passes`` iff 2^-K <= alpha, sign-flip p <= alpha, T <= -g.

    ``candidate`` and ``reference`` map the same draw keys to count dicts over ``keys``. The p-value is exact only
    under within-pair exchangeability (``exchangeability``), which CRN pairing does not establish, so ``passes``
    never gates. ``d_mean_tae`` = ``d_crps`` + ``d_half_gmd`` splits the mean TAE change; ``tae_only`` marks a TAE
    gain of g that the CRPS does not carry (a sharpening, which the CRPS does not reward), and ``spread_driven`` a
    CRPS gain of g that the mean TAE does not carry (a widening, which the fair CRPS rewards when the reference is
    biased and overconfident).
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
    return {"rule": "one-sided paired sign-flip test on T = fairCRPS(candidate) - fairCRPS(reference) (report only)",
            "exchangeability": PAIRED_EXCHANGEABILITY, "draws": size, "draw_keys": draw_keys, "g": g, "alpha": alpha,
            "T": statistic, "t": float(statistic), "numerators": numerators, "denominator": denominator,
            "p": p_value, "p_value": float(p_value), "min_p": min_p,
            "size_bound": Fraction(math.floor(alpha_q * 2 ** size), 2 ** size), "assessable": assessable,
            "passes": assessable and p_value <= alpha_q and statistic <= -g_q,
            "d_mean_tae": d_mean_tae, "d_half_gmd": d_half_gmd, "d_crps": statistic,
            "tae_only": d_mean_tae <= -g_q and statistic > -g_q,
            "spread_driven": statistic <= -g_q and d_mean_tae > -g_q}


# --- the paired energy distance (report only) ----------------------------------------------------------------------

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


def energy_distance(candidate: Sequence[Counts], reference: Sequence[Counts], keys: Sequence[str]) -> Fraction:
    """Unbiased L1 energy distance of two independent ensembles: 2 mean D(C, P) - mean D(C, C') - mean D(P, P').

    Every cross pair enters, so use it for arms on disjoint keys; same-key CRN pairs belong to ``paired_energy_test``.
    An estimate, so it can be negative; 0 does not prove equal distributions (report only).
    """
    if len(candidate) < 2 or len(reference) < 2:
        raise ValueError("Each arm needs at least two draws")
    cross = Fraction(sum(l1(c, p, keys) for c in candidate for p in reference), len(candidate) * len(reference))
    return 2 * cross - gini_mean_difference(candidate, keys) - gini_mean_difference(reference, keys)


# --- the unpaired permutation test (rate-bearing on disjoint fresh keys, A1.3) --------------------------------------

UNPAIRED_EXCHANGEABILITY = {
    "disjoint": "by construction: the run records show disjoint secret keys (every fingerprint present, none public), "
                "so under the sharp null (equal distributions) the pooled 2K draws are exchangeable given the frozen "
                "target; a confirmation also checks the keys' use against the ledger",
    "unverified": "unverified: the arms run under disjoint labels, but the run records do not show disjoint secret "
                  "keys (a fingerprint is missing or malformed, or a key is public or of unknown publicity), so a "
                  "split chosen after seeing exposed draws cannot be excluded; a screening statistic with no rate",
    "shared": "not by construction: the arms share CRN keys, so the pooled draws are dependent in pairs; a screening "
              "statistic with no rate"}


def unpaired_crps_contributions(candidate: Sequence[Counts], reference: Sequence[Counts], target: Target,
                                keys: Sequence[str]) -> tuple[list[int], int]:
    """Integer coefficients c_i of the pooled draws (candidate first) and Q = 2K(K - 1)T, for two arms of K draws.

    c_i = 2(K - 1) sum_t D(X_i, y_t) - T sum_j D(X_i, X_j), with D the L1 distance and T truth members. For every
    relabeling A of K pooled draws as the candidate (B the rest), fairCRPS(A) - fairCRPS(B) =
    (sum_{i in A} c_i - sum_{i in B} c_i) / Q: the within-arm sums satisfy W_A - W_B = (sum_A r - sum_B r) / 2 with
    r_i = sum_j D(X_i, X_j), which makes the statistic linear in the labels when the arms have equal size.
    """
    size, truths = len(candidate), _truths(target)
    if size != len(reference) or size < 2:
        raise ValueError("Use two arms of equal size, at least two draws each")
    pooled = list(candidate) + list(reference)
    to_y = [sum(l1(member, truth, keys) for truth in truths) for member in pooled]
    pair = [[0] * len(pooled) for _ in pooled]
    for i, j in itertools.combinations(range(len(pooled)), 2):
        pair[i][j] = pair[j][i] = l1(pooled[i], pooled[j], keys)
    members = len(truths)
    return ([2 * (size - 1) * to_y[i] - members * sum(pair[i]) for i in range(len(pooled))],
            2 * size * (size - 1) * members)


def _sums_by_size(values: Sequence[int]) -> list[list[int]]:
    """The subset sums of ``values`` grouped by subset size (all 2^n subsets)."""
    groups: list[list[int]] = [[0]] + [[] for _ in values]
    for count, value in enumerate(values, 1):
        for size in range(count, 0, -1):
            groups[size].extend(total + value for total in groups[size - 1])
    return groups


def count_subsets_at_most(values: Sequence[int], size: int, threshold: int) -> int:
    """#{A subset of the indices : |A| = size, sum_{i in A} values_i <= threshold}, exact.

    Meet in the middle: the subset sums of each half are grouped by size, one half is sorted and counted with
    bisect, so 2K = 32 values need 2 x 2^16 sums rather than C(32, 16) = 601080390 subsets.
    """
    values = list(values)
    if any(isinstance(v, bool) or not isinstance(v, int) for v in values):
        raise ValueError("The subset count takes integer values")
    if not 0 <= size <= len(values):
        return 0
    half = len(values) // 2
    left, right = _sums_by_size(values[:half]), [sorted(group) for group in _sums_by_size(values[half:])]
    total = 0
    for chosen, sums in enumerate(left):
        rest = size - chosen
        if 0 <= rest < len(right):
            ordered = right[rest]
            total += sum(bisect.bisect_right(ordered, threshold - value) for value in sums)
    return total


def unpaired_crps_test(candidate: Sequence[Counts], reference: Sequence[Counts], target: Target,
                       keys: Sequence[str], *, g: float, alpha: float, max_splits: int = 20000,
                       keys_relation: Literal["disjoint", "unverified", "shared"] = "disjoint") -> dict:
    """Exact one-sided permutation p of T = fairCRPS(candidate) - fairCRPS(reference) over all relabelings.

    p = #{A : T(A) <= T} / C(nA + nB, nA), ties against the candidate and the observed labeling included. Equal arms
    (the A1.3 design, 2 to MAX_UNPAIRED_DRAWS draws each) use the exact linear form and count every one of the
    C(2K, K) relabelings by meet in the middle. Unequal arms are enumerated directly, and more than ``max_splits``
    splits are refused: there is no random fallback. ``passes`` iff the smallest attainable p is <= alpha,
    p <= alpha and T <= -g. ``keys_relation`` only labels the exchangeability, from the caller's key evidence: by
    construction for disjoint secret keys (``disjoint``), unverified for disjoint labels whose keys the run records
    do not show as secret and distinct (``unverified``), and not by construction for shared CRN keys (``shared``).
    """
    n_a, n_b = len(candidate), len(reference)
    if n_a < 2 or n_b < 2:
        raise ValueError("Each arm needs at least two draws")
    if keys_relation not in UNPAIRED_EXCHANGEABILITY:
        raise ValueError(f"Unknown keys relation {keys_relation!r}; use {sorted(UNPAIRED_EXCHANGEABILITY)}")
    splits = math.comb(n_a + n_b, n_a)
    if n_a == n_b:
        if n_a > MAX_UNPAIRED_DRAWS:
            raise ValueError(f"Exact relabeling supports 2 to {MAX_UNPAIRED_DRAWS} draws per arm")
        coefficients, denominator = unpaired_crps_contributions(candidate, reference, target, keys)
        chosen = sum(coefficients[:n_a])
        observed = Fraction(2 * chosen - sum(coefficients), denominator)
        hits, method = count_subsets_at_most(coefficients, n_a, chosen), "linear form, meet-in-the-middle count"
    else:
        if splits > max_splits:
            raise ValueError(f"{splits} splits exceed max_splits={max_splits}; there is no random fallback")
        observed, hits = _enumerate_unequal(candidate, reference, target, keys)
        method = "direct enumeration (unequal arms)"
    alpha_q, p_value = _exact(alpha), Fraction(hits, splits)
    assessable = Fraction(1, splits) <= alpha_q
    return {"rule": "exact one-sided permutation of the fair-CRPS difference over all relabelings of the pooled draws",
            "method": method, "exchangeability": UNPAIRED_EXCHANGEABILITY[keys_relation],
            "draws": [n_a, n_b], "g": g, "alpha": alpha, "T": observed, "t": float(observed), "splits": splits,
            "hits": hits, "p": p_value, "p_value": float(p_value), "min_p": Fraction(1, splits),
            "size_bound": Fraction(math.floor(alpha_q * splits), splits), "assessable": assessable,
            "passes": assessable and p_value <= alpha_q and observed <= -_exact(g)}


def _enumerate_unequal(candidate: Sequence[Counts], reference: Sequence[Counts], target: Target,
                       keys: Sequence[str]) -> tuple[Fraction, int]:
    pooled = list(candidate) + list(reference)
    n_a, n = len(candidate), len(pooled)
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
    return observed, sum(split_statistic(chosen) <= observed for chosen in itertools.combinations(range(n), n_a))


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


# --- one paired v3 case (report only; the gate verdict is m2_stage.evaluate_stage) --------------------------------

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
    """The paired statistics of one case on the counts of ``rows``, all report only (A1.3, A1.5).

    ``primary``, ``main130`` and ``totals13`` are paired CRPS sign-flip results on draws paired by key (CRN) or by
    another declared pairing; their ``passes`` never gates. ``v2_sign_rule`` and ``noninferiority`` are v2's exact
    sign rules on the per-key TAE on the objective; the second is v2's S rule, reported without any
    ``simplified`` verdict (decision 5). ``energy`` and the median distances are diagnostics. The gate verdict, with
    its unpaired test and every guard, is ``m2_stage.evaluate_stage``.
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
