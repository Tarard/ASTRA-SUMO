"""M2 protocol v3 statistics (twin/ensemble.py, guards.py, ensemble_profile.py, panel.py) with zero SUMO runs.

Values are pinned on the committed B0 panel fixture (tests/fixtures/twin_m2/b0_panel.json, read from the cached
E:\\twin-store\\m2-pilot runs by ``astra_twin.py panel-fixture``) and are exact Fractions unless compared with
pytest.approx. Only the store-parity test reads the store, and it is skipped with its reason when the store is absent.
"""

from __future__ import annotations

import hashlib
import itertools
import json
import os
import random
from fractions import Fraction
from pathlib import Path

import pytest

from astra_sumo.core.benchmark_references import csv_rows
from astra_sumo.twin import count_profile, ensemble, ensemble_profile, gate_rules, goal, guards, panel, runner

from test_twin_judge import CASE, PACKET, ROOT

FIXTURE = ROOT / "tests" / "fixtures" / "twin_m2" / "b0_panel.json"
STORE = Path(os.environ.get("ASTRA_TWIN_M2_STORE", r"E:\twin-store\m2-pilot"))
KEYS = [f"k{i}" for i in range(1, 7)]
OPS = ("L", "M", "N")
G, ALPHA = 10, 0.05
# build_calibrate_profile's canonical output (profile + details) on the tracked packet at a24b92e, before the
# profile_core extraction.
M1_PROFILE_SHA256 = "9d25c713bc6cfb164d0b3aeb74676fb58668b00c314f0bee4845005fcb2901b2"


@pytest.fixture(scope="module")
def b0() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _arm(b0: dict, arm: str, objective: str) -> dict[str, dict[str, int]]:
    return {key: ensemble.objective_values(b0["arms"][arm][key], b0["main_rows"], objective) for key in KEYS}


def _real(b0: dict, objective: str) -> tuple[dict[str, int], list[str]]:
    values = ensemble.objective_values(b0["target"], b0["main_rows"], objective)
    return values, list(values)


def _members(by_key: dict[str, dict], keys: list[str]) -> list[dict]:
    return [by_key[key] for key in keys]


def _swapped(candidate: list[dict], reference: list[dict], signs) -> tuple[list[dict], list[dict]]:
    return ([c if s == 1 else p for c, p, s in zip(candidate, reference, signs)],
            [p if s == 1 else c for c, p, s in zip(candidate, reference, signs)])


# --- T1 objectives -------------------------------------------------------------------------------------------------

def test_fixture_rows_and_provenance(b0):
    assert len(b0["main_rows"]) == 130 and len(b0["target"]) == 182
    assert b0["main_rows"] == ensemble.main_rows(list(b0["target"]))
    assert sorted(b0["arms"]) == ["L", "M", "N", "P"] and all(sorted(b0["arms"][a]) == KEYS for a in b0["arms"])
    assert b0["provenance"]["runs"]["P:k1"]["id"] == "P3-jitter-k1-s104"
    assert b0["provenance"]["runs"]["nominal"]["id"] == panel.B0_NOMINAL
    assert len(b0["provenance"]["runs"]) == 25 and b0["provenance"]["code_commit"].startswith("a24b92ec")
    tae = [sum(abs(b0["arms"]["P"][k][r] - b0["target"][r]) for r in b0["main_rows"]) for k in KEYS]
    assert tae == [339, 345, 399, 329, 343, 377] and b0["nominal"]["tae_main"] == 275


def test_objective_transforms_on_the_real_counts(b0):
    rows, target = b0["main_rows"], b0["target"]
    assert [len(ensemble.objective_values(target, rows, name)) for name in ensemble.OBJECTIVES] == [130, 91, 104, 13]
    ms4 = ensemble.objective_values(target, rows, "ms4")
    assert ms4["W:0535Z4@3600+3600"] == 136 and ms4["W:0119Z12@9000+3600"] == 432
    totals = ensemble.field_totals(target, rows)
    assert (totals["T:0119Z4"], totals["T:0119Z5"], totals["T:0535Z4"]) == (600, 522, 390)
    assert list(totals) == sorted(totals)
    assert all(b0["field_totals"][a][k] == ensemble.field_totals(b0["arms"][a][k], rows)
               for a in b0["arms"] for k in KEYS)
    assert ensemble.parse_row("R:0119Z11@3600") == ("0119Z11", 3600.0)
    for bad in ("W:0119Z11@3600", "R:0119Z11", "R:@3600", "R:x@soon"):
        with pytest.raises(ValueError):
            ensemble.parse_row(bad)
    with pytest.raises(ValueError, match="Unknown objective"):
        ensemble.objective_values(target, rows, "ms5")


def test_rolling_sums_refuse_gaps_missing_rows_and_short_fields():
    counts = {f"R:A@{b}": i for i, b in enumerate((0, 900, 1800, 2700))}
    rows = list(counts)
    assert ensemble.rolling_sums(counts, rows, width_bins=2) == {"W:A@0+1800": 1, "W:A@900+1800": 3,
                                                                  "W:A@1800+1800": 5}
    with pytest.raises(ValueError, match="non-contiguous"):
        ensemble.rolling_sums(counts, [r for r in rows if r != "R:A@900"], width_bins=2)
    with pytest.raises(ValueError, match="no count"):
        ensemble.rolling_sums({k: v for k, v in counts.items() if k != "R:A@1800"}, rows, width_bins=2)
    with pytest.raises(ValueError, match="fewer than the width"):
        ensemble.rolling_sums(counts, rows, width_bins=5)
    with pytest.raises(ValueError, match="no count"):
        ensemble.field_totals({}, rows)


# --- T2 parent against the real counts -----------------------------------------------------------------------------

@pytest.mark.parametrize(("objective", "crps", "gmd", "distance", "median"), [
    ("main130", 171, Fraction(1106, 3), Fraction(1066, 3), 260),
    ("ms4", 312, Fraction(1109, 3), Fraction(2981, 6), 425),
    ("totals13", Fraction(847, 15), Fraction(686, 15), Fraction(238, 3), Fraction(149, 2))])
def test_parent_ensemble_against_the_real_counts(b0, objective, crps, gmd, distance, median):
    y, keys = _real(b0, objective)
    parent = _members(_arm(b0, "P", objective), KEYS)
    assert ensemble.fair_crps(parent, y, keys) == crps
    assert ensemble.gini_mean_difference(parent, keys) == gmd
    assert ensemble.mean_distance(parent, y, keys) == distance
    assert ensemble.ensemble_median_distance(parent, y, keys) == median


def test_small_ensembles_and_truth_ensembles():
    keys = ["a"]
    ensemble_ = [{"a": 0}, {"a": 2}]
    assert ensemble.fair_crps(ensemble_, {"a": 1}, keys) == 0
    assert ensemble.mean_distance(ensemble_, [{"a": 1}, {"a": 3}], keys) == Fraction(3, 2)
    assert ensemble.ensemble_median_distance([{"a": 0}, {"a": 3}], {"a": 0}, keys) == Fraction(3, 2)
    with pytest.raises(ValueError, match="two members"):
        ensemble.gini_mean_difference([{"a": 1}], keys)
    with pytest.raises(ValueError, match="one target"):
        ensemble.ensemble_median_distance(ensemble_, [{"a": 1}], keys)


# --- T3 the linear swap identity -----------------------------------------------------------------------------------

@pytest.mark.parametrize("objective", ["main130", "ms4"])
@pytest.mark.parametrize("op", OPS)
def test_swap_identity_matches_recomputed_crps_on_every_pattern(b0, op, objective):
    y, keys = _real(b0, objective)
    candidate, reference = _members(_arm(b0, op, objective), KEYS), _members(_arm(b0, "P", objective), KEYS)
    numerators, denominator = ensemble.crps_contributions(candidate, reference, y, keys)
    assert Fraction(sum(numerators), denominator) == (ensemble.fair_crps(candidate, y, keys)
                                                      - ensemble.fair_crps(reference, y, keys))
    for signs in itertools.product((1, -1), repeat=6):
        c, p = _swapped(candidate, reference, signs)
        assert Fraction(sum(s * v for s, v in zip(signs, numerators)), denominator) == (
            ensemble.fair_crps(c, y, keys) - ensemble.fair_crps(p, y, keys))
    result = ensemble.paired_crps_test(_arm(b0, op, objective), _arm(b0, "P", objective), y, keys, g=G, alpha=ALPHA)
    assert result["d_mean_tae"] == result["T"] + result["d_half_gmd"]


def test_swap_identity_holds_against_a_truth_ensemble(b0):
    y = [_arm(b0, "P", "ms4")[k] for k in ("k5", "k6")]
    keys = list(y[0])
    candidate = _members(_arm(b0, "M", "ms4"), KEYS[:4])
    reference = _members(_arm(b0, "P", "ms4"), KEYS[:4])
    numerators, denominator = ensemble.crps_contributions(candidate, reference, y, keys)
    assert denominator == 2 * 4 * 3 * 2
    for signs in itertools.product((1, -1), repeat=4):
        c, p = _swapped(candidate, reference, signs)
        assert Fraction(sum(s * v for s, v in zip(signs, numerators)), denominator) == (
            ensemble.fair_crps(c, y, keys) - ensemble.fair_crps(p, y, keys))
    with pytest.raises(ValueError, match="equal size"):
        ensemble.crps_contributions(candidate, reference[:3], y, keys)


# --- T4 the paired CRPS test against the real counts ---------------------------------------------------------------

@pytest.mark.parametrize(("objective", "expected"), [
    ("main130", {"L": (Fraction(217, 15), Fraction(59, 64), Fraction(3, 32)),
                 "M": (Fraction(14, 3), Fraction(19, 32), Fraction(27, 64)),
                 "N": (Fraction(76, 3), Fraction(57, 64), Fraction(1, 8))}),
    ("ms4", {"L": (Fraction(31, 5), Fraction(3, 4), Fraction(17, 64)),
             "M": (Fraction(128, 15), Fraction(41, 64), Fraction(3, 8)),
             "N": (Fraction(312, 5), Fraction(61, 64), Fraction(5, 64))})])
def test_paired_crps_test_against_the_real_counts(b0, objective, expected):
    y, keys = _real(b0, objective)
    parent = _arm(b0, "P", objective)
    for op, (statistic, p_forward, p_reverse) in expected.items():
        forward = ensemble.paired_crps_test(_arm(b0, op, objective), parent, y, keys, g=G, alpha=ALPHA)
        reverse = ensemble.paired_crps_test(parent, _arm(b0, op, objective), y, keys, g=G, alpha=ALPHA)
        assert (forward["T"], forward["p"], reverse["T"], reverse["p"]) == (statistic, p_forward, -statistic,
                                                                            p_reverse)
        assert not forward["passes"] and not reverse["passes"] and forward["assessable"]
        assert forward["draws"] == 6 and forward["draw_keys"] == KEYS and forward["min_p"] == Fraction(1, 64)
        assert forward["t"] == float(statistic) and forward["p_value"] == float(p_forward)
        assert forward["d_crps"] == forward["T"] and not forward["tae_only"]
        if (objective, op) == ("main130", "L"):
            assert (forward["d_mean_tae"], forward["d_half_gmd"]) == (Fraction(7, 2), Fraction(-329, 30))


def test_paired_crps_test_refuses_unpaired_keys(b0):
    parent = _arm(b0, "P", "ms4")
    y, keys = _real(b0, "ms4")
    with pytest.raises(ValueError, match=r"Unpaired draw keys: \['k6'\]"):
        ensemble.paired_crps_test({k: parent[k] for k in KEYS[:5]}, parent, y, keys, g=G, alpha=ALPHA)


# --- T5 paired energy distance -------------------------------------------------------------------------------------

@pytest.mark.parametrize(("objective", "expected"), [
    ("main130", {"M": (Fraction(121, 5), Fraction(1, 32)), "L": (0, Fraction(3, 8)),
                 "N": (Fraction(13, 3), Fraction(3, 8))}),
    ("ms4", {"M": (Fraction(394, 15), Fraction(1, 32)), "L": (Fraction(-7, 15), Fraction(23, 32)),
             "N": (Fraction(-44, 5), Fraction(9, 16))})])
def test_paired_energy_test_and_its_quadratic_form(b0, objective, expected):
    _, keys = _real(b0, objective)
    parent = _arm(b0, "P", objective)
    for op, (ed, p_value) in expected.items():
        result = ensemble.paired_energy_test(_arm(b0, op, objective), parent, keys)
        assert (result["ed"], result["p"], result["min_p"], result["draws"]) == (ed, p_value, Fraction(2, 64), 6)
        candidate, reference = _members(_arm(b0, op, objective), KEYS), _members(parent, KEYS)
        terms, denominator = ensemble.energy_pair_terms(candidate, reference, keys)
        hits = 0
        for signs in itertools.product((1, -1), repeat=6):
            c, p = _swapped(candidate, reference, signs)
            direct = Fraction(2 * sum(ensemble.l1(c[i], p[j], keys) for i in range(6) for j in range(6) if i != j)
                              - sum(ensemble.l1(c[i], c[j], keys) + ensemble.l1(p[i], p[j], keys)
                                    for i in range(6) for j in range(6) if i != j), denominator)
            quadratic = Fraction(sum(signs[k] * signs[j] * terms[k][j]
                                     for k, j in itertools.combinations(range(6), 2)), denominator)
            assert direct == quadratic
            hits += quadratic >= ed
        assert Fraction(hits, 64) == p_value


def test_energy_swap_enumeration_matches_brute_force_on_random_terms():
    rng = random.Random(20261025)
    for size in (2, 3, 5, 7):
        members = [{"a": rng.randint(0, 40), "b": rng.randint(0, 40)} for _ in range(2 * size)]
        candidate = {f"k{i}": members[i] for i in range(size)}
        reference = {f"k{i}": members[size + i] for i in range(size)}
        result = ensemble.paired_energy_test(candidate, reference, ["a", "b"])
        terms, denominator = ensemble.energy_pair_terms([candidate[k] for k in sorted(candidate)],
                                                        [reference[k] for k in sorted(reference)], ["a", "b"])
        values = [sum(s[k] * s[j] * terms[k][j] for k, j in itertools.combinations(range(size), 2))
                  for s in itertools.product((1, -1), repeat=size)]
        observed = sum(terms[k][j] for k, j in itertools.combinations(range(size), 2))
        assert result["p"] == Fraction(sum(v >= observed for v in values), 2 ** size)
        assert result["ed"] == Fraction(observed, denominator)
    with pytest.raises(ValueError, match="2 to 16"):
        ensemble.paired_energy_test({"k1": {"a": 1}}, {"k1": {"a": 2}}, ["a"])


# --- T6 stage-3 folds ----------------------------------------------------------------------------------------------

def _folds(b0: dict, objective: str):
    """(op, truth key, role, result) for each op and truth key judged on the other five keys."""
    y, keys = _real(b0, objective)
    arms = {arm: _arm(b0, arm, objective) for arm in ("P", *OPS)}
    for op in OPS:
        for truth in KEYS:
            judged = [k for k in KEYS if k != truth]
            x, p = {k: arms[op][k] for k in judged}, {k: arms["P"][k] for k in judged}
            for role, candidate, reference, target in (("pos", x, p, arms[op][truth]), ("null", x, p,
                                                                                         arms["P"][truth]),
                                                       ("wrongS", p, x, arms[op][truth]),
                                                       ("revert", p, x, arms["P"][truth])):
                yield op, truth, role, ensemble.paired_crps_test(candidate, reference, target, keys, g=G, alpha=ALPHA)


@pytest.mark.parametrize("objective", ensemble.OBJECTIVES)
def test_stage3_folds_must_reject_and_the_few_accepts(b0, objective):
    folds = list(_folds(b0, objective))
    must_reject = [r for _, _, role, r in folds if role in ("null", "wrongS")]
    assert len(must_reject) == 36 and not any(r["passes"] for r in must_reject)
    accepted = {(op, truth, role): (r["T"], r["p"]) for op, truth, role, r in folds if r["passes"]}
    if objective == "main130":
        assert accepted == {("M", "k2", "pos"): (Fraction(-377, 10), Fraction(1, 32)),
                            ("N", "k2", "revert"): (Fraction(-359, 10), Fraction(1, 32))}
    if objective == "ms4":
        assert accepted == {("M", "k1", "pos"): (Fraction(-374, 5), Fraction(1, 32))}
    assert all(r["draws"] == 5 and r["assessable"] for *_, r in folds)


# --- T7 exactness of the sign-flip p -------------------------------------------------------------------------------

@pytest.mark.parametrize("objective", ["main130", "ms4"])
def test_exactly_floor_alpha_2k_swapped_arrangements_reach_alpha(b0, objective):
    y, keys = _real(b0, objective)
    parent = _members(_arm(b0, "P", objective), KEYS)
    for op in OPS:
        numerators, _ = ensemble.crps_contributions(_members(_arm(b0, op, objective), KEYS), parent, y, keys)
        hits = sum(ensemble.sign_flip_lower_p([s * v for s, v in zip(signs, numerators)]) <= Fraction(1, 20)
                   for signs in itertools.product((1, -1), repeat=6))
        assert hits == 3


def _brute_force(values: list[int]) -> Fraction:
    observed = sum(values)
    hits = sum(sum(s * v for s, v in zip(signs, values)) <= observed
               for signs in itertools.product((1, -1), repeat=len(values)))
    return Fraction(hits, 2 ** len(values))


def test_sign_flip_p_is_exact_with_ties_and_its_resolution():
    assert ensemble.sign_flip_lower_p([-1, -2, -3]) == Fraction(1, 8)
    assert ensemble.sign_flip_lower_p([-5] * 8) == Fraction(1, 256)
    assert ensemble.sign_flip_lower_p([0, 0, 0]) == 1 and ensemble.sign_flip_lower_p([4]) == 1
    assert ensemble.sign_flip_lower_p([-10, -10, -10, -10, -10]) == Fraction(1, 32)
    rng = random.Random(7)
    for size in range(1, 13):
        for _ in range(5):
            values = [rng.randint(-30, 30) for _ in range(size)]
            assert ensemble.sign_flip_lower_p(values) == _brute_force(values)
    assert ensemble.sign_flip_lower_p([-1] * 30) == Fraction(1, 2 ** 30)
    for bad in ([], [1] * 31, [1.5, 2]):
        with pytest.raises(ValueError):
            ensemble.sign_flip_lower_p(bad)


def test_size_bound_and_assessability_by_k():
    def case(size: int, gain: int = 50) -> dict:
        reference = {f"k{i:02d}": {"a": 100 + i % 3} for i in range(size)}
        candidate = {key: {"a": 100 + i % 3 - gain} for i, key in enumerate(sorted(reference))}
        return ensemble.paired_crps_test(candidate, reference, {"a": 0}, ["a"], g=G, alpha=ALPHA)

    eight, sixteen, four, five = case(8), case(16), case(4), case(5)
    assert eight["size_bound"] == Fraction(12, 256) and sixteen["size_bound"] == Fraction(3276, 65536)
    assert eight["passes"] and sixteen["passes"] and five["passes"] and five["p"] == Fraction(1, 32)
    assert not four["assessable"] and not four["passes"] and four["p"] == Fraction(1, 16)
    assert not case(8, gain=5)["passes"]  # p = 1/256 but T = -5 > -g


# --- T8 unpaired re-key enumeration --------------------------------------------------------------------------------

@pytest.mark.parametrize("objective", ensemble.OBJECTIVES)
def test_unpaired_three_plus_three_rekey_splits(b0, objective):
    y, keys = _real(b0, objective)
    parent = _members(_arm(b0, "P", objective), KEYS)
    results = [ensemble.unpaired_crps_test([parent[i] for i in chosen], [parent[i] for i in range(6) if i not in chosen],
                                           y, keys, g=G, alpha=ALPHA)
               for chosen in itertools.combinations(range(6), 3)]
    assert len(results) == 20 and all(r["splits"] == 20 for r in results)
    assert sum(r["passes"] for r in results) == 1 and min(r["p"] for r in results) == Fraction(1, 20)
    with pytest.raises(ValueError, match="no random fallback"):
        ensemble.unpaired_crps_test(parent[:3], parent[3:], y, keys, g=G, alpha=ALPHA, max_splits=19)


# --- T9 CRN regime --------------------------------------------------------------------------------------------------

def test_rekey_sd_crn_ratio_and_confirmation_size(b0):
    found = {}
    for objective in ("main130", "ms4"):
        y, keys = _real(b0, objective)
        scores = {arm: [ensemble.l1(_arm(b0, arm, objective)[k], y, keys) for k in KEYS] for arm in ("P", *OPS)}
        sd = ensemble.rekey_sd(scores["P"])
        found[objective] = sd, {op: [a - b for a, b in zip(scores[op], scores["P"])] for op in OPS}
    assert found["main130"][0] == pytest.approx(37.923, abs=1e-3)
    sd, d = found["ms4"]
    assert sd == pytest.approx(177.037, abs=1e-3)
    assert d["L"] == [4, -8, -5, 15, 0, -1] and d["M"] == [69, -64, 18, -146, 160, 22]
    rho = {op: ensemble.crn_ratio(d[op], sd) for op in OPS}
    assert rho == pytest.approx({"L": 0.046, "M": 0.598, "N": 0.656}, abs=1e-3)
    assert {op: ensemble.confirmation_draws(r) for op, r in rho.items()} == {"L": 8, "M": 16, "N": 16}
    assert ensemble.confirmation_draws(ensemble.CRN_EFFECTIVE_RHO) == 8
    case = ensemble.evaluate_case_v3(b0["arms"]["M"], b0["arms"]["P"], b0["target"], b0["main_rows"], g=G,
                                     alpha=ALPHA, delta=5)
    assert case["per_key_d"] == d["M"]


# --- T10 guards ----------------------------------------------------------------------------------------------------

def test_rekey_t_margins_from_the_parent_draws(b0):
    parent = b0["guard_inputs"]["P"]
    assert [parent[k]["brakings"] for k in KEYS] == [5, 9, 12, 10, 7, 10]
    assert [parent[k]["post_window"] for k in KEYS] == [52, 36, 81, 23, 27, 59]
    assert [parent[k]["init52"] for k in KEYS] == [54, 92, 65, 77, 65, 81]
    network = [parent[k]["entry_p95"][guards.NETWORK] for k in KEYS]
    assert network == [222.285, 397.209, 528.046, 435.401, 249.755, 359.524]
    margin = {name: [guards.rekey_t_margin([parent[k][name] for k in KEYS], draws) for draws in (5, 8)]
              for name in ("brakings", "post_window", "init52")}
    assert margin == {"brakings": pytest.approx([5.285, 4.178], abs=1e-3),
                      "post_window": pytest.approx([46.814, 37.010], abs=1e-3),
                      "init52": pytest.approx([28.983, 22.913], abs=1e-3)}
    assert [guards.rekey_t_margin(network, draws) for draws in (5, 8, 16)] == pytest.approx(
        [244.987, 193.679, 136.952], abs=1e-3)


def test_paired_and_kmean_guards_on_the_op_deltas(b0):
    deltas = b0["op_deltas"]
    assert deltas["L"]["network_p95"] == [22.2, 20.0, 29.6, 7.1, 26.0, 20.9]
    assert deltas["M"]["network_p95"] == [-12.3, -42.6, -90.0, 12.1, -13.3, -35.8]
    assert deltas["L"]["brakings"] == [0, 1, 1, 0, 0, 0]
    assert deltas["L"]["entry_p95:44532531"] == [20.9, 20.8, 38.1, 0.3, 21.0, 46.1]
    assert deltas["N"]["init52"] == [41, -29, -12, 11, -7, 2]
    fired = guards.paired_t_guard(deltas["L"]["network_p95"], 10)
    assert fired["fires"] and (fired["mean"], fired["lcb"]) == pytest.approx((20.967, 14.652), abs=1e-3)
    assert not guards.paired_t_guard(deltas["M"]["network_p95"], 10)["fires"]
    assert not guards.kmean_guard(deltas["L"]["brakings"], 5.3)["fires"]
    assert guards.kmean_guard([6, 6], 5.3)["fires"]
    assert guards.order_p95(range(1, 21)) == 19 and guards.order_p95([3.0]) == 3.0
    with pytest.raises(ValueError):
        guards.order_p95([])
    with pytest.raises(ValueError, match="two differences"):
        guards.paired_t_guard([1.0], 0)
    assert guards.T_ONE_SIDED[0.95][5] == gate_rules.T_95[5] == 2.015 and guards.T_ONE_SIDED[0.99][30] == 2.457
    assert all(len(table) == 30 for table in guards.T_ONE_SIDED.values())


def test_entry_p95_classes_and_the_evaluated_guard_set(b0):
    departures = [("v1", "a", 0.0), ("v2", "a", 2.0), ("v3", "b", 50.0)]
    assert guards.entry_p95(departures) == {"a": 2.0, "b": 50.0, guards.NETWORK: 50.0}
    parent = b0["guard_inputs"]["P"]
    edges = sorted(edge for edge in parent["k1"]["entry_p95"] if edge != guards.NETWORK)
    classes = guards.classify_entries({e: [parent[k]["entry_p95"][e] for k in KEYS] for e in edges})
    assert sum(kind == "quiet" for kind in classes.values()) == 7 and len(classes) == 13
    assert classes["44532531"] == "busy" and guards.NETWORK not in classes
    margins = {"network_p95": 245.0, "entry_p95:44532531": 256.0, "brakings": 5.3, "post_window": 47.0,
               "init52": 29.0}
    floors = {"paired_entry": 10.0, "quiet_entry": 10.0}
    first = ["k1", "k2", "k3", "k4", "k5"]

    def judged(arm: str) -> dict:
        return guards.evaluate_guards({k: b0["guard_inputs"][arm][k] for k in first},
                                      {k: parent[k] for k in first}, margins=margins, entry_classes=classes,
                                      floors=floors)

    lop, mop = judged("L"), judged("M")
    assert lop["label"] == "guard" and lop["fired"] == ["entry_paired:network"]
    assert lop["guards"]["entry_paired:network"]["lcb"] > 10 > lop["guards"]["entry_paired:44532531"]["lcb"]
    assert not lop["guards"]["entry_backstop:network"]["fires"]  # the no-CRN backstop misses L's paired backlog
    assert mop["label"] is None and mop["fired"] == []
    quiet = next(name for name in mop["guards"] if name.startswith("entry_quiet:"))
    assert mop["guards"][quiet]["mean"] < 1.0
    case = ensemble.evaluate_case_v3({k: b0["arms"]["N"][k] for k in first}, {k: b0["arms"]["P"][k] for k in first},
                                     b0["arms"]["P"]["k6"], b0["main_rows"], g=G, alpha=ALPHA, delta=5)
    with_case = guards.evaluate_guards({k: b0["guard_inputs"]["N"][k] for k in first}, {k: parent[k] for k in first},
                                       margins={**margins, "totals13": -1000.0, "main130": 1000.0},
                                       entry_classes=classes, floors=floors, case=case)
    assert with_case["guards"]["totals13"]["fires"] and with_case["guards"]["totals13"]["label"] == "level_regress"
    with pytest.raises(ValueError, match="Unpaired"):
        guards.evaluate_guards({"k1": parent["k1"]}, {"k2": parent["k2"]}, margins=margins, entry_classes=classes,
                               floors=floors)


# --- T11 ensemble hotspots -----------------------------------------------------------------------------------------

def test_ensemble_hotspots_on_the_parent_draws(b0):
    profiles = [b0["profiles"]["P"][k] for k in KEYS]
    rows = {row["element"]: row for row in ensemble_profile.ensemble_hotspots(profiles, nominal=b0["nominal"])}
    entry = rows["entry:44532531"]
    assert (entry["top_draws"], entry["weight_median"], entry["weight_min"], entry["weight_max"],
            entry["median_rank"], entry["robustness"]) == (6, 78, 53, 140, 1, "robust")
    assert entry["top_freq_lower95"] == pytest.approx(0.607, abs=1e-3)
    assert (entry["nominal_weight"], entry["nominal_rank"]) == (58, 1)
    for element, (top, median, low, high) in {"edge:24483344#0": (6, 41.5, 17, 66),
                                              "field:0119Z5": (6, 24.5, 17, 36),
                                              "edge:24483344#1": (6, 23.5, 15, 45)}.items():
        row = rows[element]
        assert (row["top_draws"], row["weight_median"], row["weight_min"], row["weight_max"]) == (top, median, low,
                                                                                                 high)
    assert rows["field:0119Z13"]["top_draws"] == 3 and rows["field:0119Z13"]["robustness"] == "unsettled"
    assert rows["field:0119Z13"]["top_freq_lower95"] == pytest.approx(0.153, abs=1e-3)
    assert rows["edge:551303424"]["top_draws"] == 1
    assert rows["edge:551303424"]["robustness"] == "realization_specific"
    assert list(rows)[:4] == ["entry:44532531", "edge:24483344#0", "field:0119Z5", "edge:24483344#1"]
    frequency = ensemble_profile.hotspot_id_frequency(profiles)
    assert frequency.get("H:LOC:co:27d1dde6", 0) == 0 and frequency["H:LOC:entry:44532531"] == 6
    assert ensemble_profile.clopper_pearson_lower(5, 6) == pytest.approx(0.418, abs=1e-3)
    assert ensemble_profile.clopper_pearson_lower(1, 6) == pytest.approx(0.0085, abs=1e-4)
    assert ensemble_profile.clopper_pearson_lower(0, 6) == 0
    merged = ensemble_profile.merge_corridor(
        ensemble_profile.element_weights(profiles[0]),
        {"corridor:24483344": ["edge:24483344#0", "edge:24483344#1"]})
    weights = ensemble_profile.element_weights(profiles[0])
    assert merged["corridor:24483344"] == weights["edge:24483344#0"] + weights["edge:24483344#1"]
    assert "edge:24483344#0" not in merged and sum(merged.values()) == sum(weights.values())
    with pytest.raises(ValueError, match="two groups"):
        ensemble_profile.merge_corridor(weights, {"a": ["edge:x"], "b": ["edge:x"]})
    assert [ensemble_profile.robustness_label(k, 6) for k in (6, 5, 4, 3, 2, 0)] == [
        "robust", "robust", "unsettled", "unsettled", "realization_specific", "realization_specific"]


# --- T12 ensemble field balance ------------------------------------------------------------------------------------

def test_ensemble_field_balance_flags_the_three_fields_above_every_draw(b0):
    real = {key.removeprefix("T:"): value for key, value in ensemble.field_totals(b0["target"],
                                                                                   b0["main_rows"]).items()}
    draws = [{key.removeprefix("T:"): value for key, value in b0["field_totals"]["P"][k].items()} for k in KEYS]
    balance = ensemble_profile.ensemble_field_balance([b0["profiles"]["P"][k]["field_balance"] for k in KEYS],
                                                      real_main_totals=real, draw_main_totals=draws)
    outside = {field: (row["real_main_total"], row["draw_main_total"]["min"], row["draw_main_total"]["max"])
               for field, row in balance.items() if row["real_outside"]}
    assert outside == {"0119Z4": (600, 577, 591), "0119Z5": (522, 486, 505), "0535Z4": (390, 371, 379)}
    assert all(balance[f]["real_outside"] == "above" for f in outside)
    quiet = [f for f, row in balance.items()
             if row["draw_main_total"]["min"] == row["draw_main_total"]["max"] == row["real_main_total"]]
    assert len(quiet) == 7 and all(balance[f]["r_main"] == {"median": 0, "min": 0, "max": 0} for f in quiet)
    assert balance["0119Z5"]["sign_stable"] and not balance["0119Z13"]["sign_stable"]


# --- T13 verdict_v3 ------------------------------------------------------------------------------------------------

def _synthetic(values: list[int], *, reference_values: tuple[int, ...] = (20, 21, 22, 23, 24)) -> dict:
    rows = ["R:A@3600", "R:A@4500", "R:A@5400", "R:A@6300"]
    reference = {f"k{i}": {row: v for row in rows} for i, v in enumerate(reference_values)}
    candidate = {f"k{i}": {row: v for row in rows} for i, v in enumerate(values)}
    return ensemble.evaluate_case_v3(candidate, reference, {row: 0 for row in rows}, rows, g=10, alpha=0.05,
                                     delta=5, objective="ms4")


def test_verdict_v3_follows_the_gate_table_order():
    improved = _synthetic([5, 6, 7, 8, 9])
    assert improved["primary"]["passes"] and improved["values"] == 1 and improved["energy"]["draws"] == 5
    assert ensemble.verdict_v3(improved, g=10, delta=5, dof_lower=False)["label"] == "improved"
    screen = ensemble.verdict_v3(improved, g=10, delta=5, dof_lower=False)
    confirm = ensemble.verdict_v3(improved, g=10, delta=5, dof_lower=False, mode="confirm")
    assert (screen["rate"], confirm["rate"]) == ("none", "alpha")
    guarded = ensemble.verdict_v3(improved, g=10, delta=5, dof_lower=False, guard_failure="fine_regress",
                                  mode="confirm")
    assert (guarded["label"], guarded["rate"]) == ("fine_regress", "none")
    same = _synthetic([20, 21, 22, 23, 24])
    assert same["no_effect"] and ensemble.verdict_v3(same, g=10, delta=5, dof_lower=True)["label"] == "no_effect"
    near = _synthetic([21, 21, 22, 23, 24])
    assert near["per_key_d"] == [4, 0, 0, 0, 0] and near["noninferiority"]["passes"] and near["primary"]["T"] < 5
    assert ensemble.verdict_v3(near, g=10, delta=5, dof_lower=True)["label"] == "simplified"
    assert ensemble.verdict_v3(near, g=10, delta=5, dof_lower=False)["label"] == "not_material"
    noisy = _synthetic([5, 6, 7, 8, 30])
    assert noisy["primary"]["T"] <= -10 and not noisy["primary"]["passes"]
    assert ensemble.verdict_v3(noisy, g=10, delta=5, dof_lower=False)["label"] == "not_significant"
    worse = _synthetic([40, 41, 42, 43, 44])
    assert ensemble.verdict_v3(worse, g=10, delta=5, dof_lower=True)["label"] == "worse"
    with pytest.raises(ValueError, match="Unknown guard"):
        ensemble.verdict_v3(improved, g=10, delta=5, dof_lower=False, guard_failure="blocked?")
    with pytest.raises(ValueError, match="Unknown mode"):
        ensemble.verdict_v3(improved, g=10, delta=5, dof_lower=False, mode="release")


def test_tae_only_note_marks_a_sharpening_that_crps_does_not_reward():
    rows = ["R:A@3600", "R:A@4500", "R:A@5400", "R:A@6300"]
    spread = [0, 20, 0, 20, 0, 20]
    reference = {f"k{i}": {row: v for row in rows} for i, v in enumerate(spread)}
    sharp = {f"k{i}": {row: 10 for row in rows} for i in range(6)}
    case = ensemble.evaluate_case_v3(sharp, reference, {row: 7 for row in rows}, rows, g=10, alpha=0.05, delta=5,
                                     objective="main130")
    primary = case["primary"]
    assert (primary["d_mean_tae"], primary["T"], primary["d_half_gmd"]) == (-28, -4, -24) and primary["tae_only"]
    verdict = ensemble.verdict_v3(case, g=10, delta=5, dof_lower=False)
    assert verdict["label"] != "improved" and verdict["note"] == "tae_only"
    assert case["v2_sign_rule"]["passes"]  # v2's TAE sign rule would have accepted the sharpening


def test_evaluate_case_v3_reports_every_v3_statistic(b0):
    case = ensemble.evaluate_case_v3(b0["arms"]["M"], b0["arms"]["P"], b0["target"], b0["main_rows"], g=G,
                                     alpha=ALPHA, delta=5)
    assert case["objective"] == "ms4" and case["values"] == 91 and case["draw_keys"] == KEYS
    assert case["primary"]["T"] == Fraction(128, 15) and case["energy"]["ed"] == Fraction(394, 15)
    assert case["main130"]["T"] == Fraction(14, 3) and case["totals13"]["T"] == Fraction(-7, 15)
    assert case["median_distance"]["ms4"]["reference"] == 425 and not case["no_effect"]
    assert case["v2_sign_rule"]["hits"] == 2 and not case["v2_sign_rule"]["passes"]
    truth = ensemble.evaluate_case_v3(b0["arms"]["M"], b0["arms"]["P"], [b0["arms"]["P"][k] for k in ("k1", "k2")],
                                      b0["main_rows"], g=G, alpha=ALPHA, delta=5)
    assert truth["median_distance"] is None and truth["primary"]["denominator"] == 2 * 6 * 5 * 2
    assert ensemble.verdict_v3(case, g=G, delta=5, dof_lower=False)["label"] == "not_material"


# --- T14 store parity ----------------------------------------------------------------------------------------------

def test_read_panel_reproduces_the_committed_fixture(tmp_path):
    ledger = STORE / "runs.jsonl"
    if not ledger.is_file():
        pytest.skip(f"the cached M2 pilot store is not on this machine ({ledger}); the committed fixture is the "
                    "record, and this parity check runs where the store is (set ASTRA_TWIN_M2_STORE)")
    committed = FIXTURE.read_bytes()
    recorded = json.loads(committed)["provenance"]
    found = panel.read_panel(ledger, panel.B0_LABELS, repository_root=ROOT, nominal=panel.B0_NOMINAL)
    written = panel.write_panel_fixture(found, tmp_path / "b0_panel.json", code_commit=recorded["code_commit"])
    rebuilt = (tmp_path / "b0_panel.json").read_bytes()
    if found["provenance"]["ledger_sha256"] == recorded["ledger_sha256"]:
        assert rebuilt == committed and written["sha256"] == hashlib.sha256(committed).hexdigest()
    else:  # runs were appended to the ledger since the fixture was built: every other byte must still agree
        again = json.loads(rebuilt)
        again["provenance"]["ledger_sha256"] = recorded["ledger_sha256"]
        assert again == json.loads(committed)
    with pytest.raises(FileExistsError):
        panel.write_panel_fixture(found, tmp_path / "b0_panel.json", code_commit=None)


def test_tripinfo_departures_and_panel_label_checks(tmp_path):
    data = (b'<tripinfos><tripinfo id="v1" departLane="44532531_0" departDelay="12.50"/>'
            b'<tripinfo id="v2" departLane="-142944318_2" departDelay="0.10"/></tripinfos>')
    assert runner.tripinfo_departures(data) == [("v1", "44532531", 12.5), ("v2", "-142944318", 0.1)]
    with pytest.raises(ValueError, match="departDelay"):
        runner.tripinfo_departures(b'<tripinfos><tripinfo id="v1" departLane="e_0"/></tripinfos>')
    assert len(panel.B0_LABELS) == 24 and panel.B0_LABELS["P:k5"] == "s3-parent-k5"
    ledger = tmp_path / "runs.jsonl"
    ledger.write_text(json.dumps({"id": "a", "request_sha256": "1"}) + "\n" + json.dumps({"id": "a", "request_sha256":
                                                                                           "2"}) + "\n",
                      encoding="utf-8")
    with pytest.raises(ValueError, match="two different requests"):
        panel.read_ledger(ledger)
    ledger.write_text(json.dumps({"id": "a", "request_sha256": "1"}) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="not found"):
        panel.read_panel(ledger, {"P:k1": "b"}, repository_root=ROOT)


# --- T15 M1 parity -------------------------------------------------------------------------------------------------

def test_profile_core_reproduces_the_m1_baseline_profile(tmp_path):
    reader = goal.case_reader(ROOT, PACKET, CASE)
    path, _ = goal.write_goal(goal.create_calibrate_goal(ROOT, PACKET, CASE), tmp_path / "goals")
    loaded, data = goal.load_goal(path)
    profile, details = count_profile.build_calibrate_profile(loaded, data, reader, run_dir="evaluation/native",
                                                             verifier={"commit": "0" * 40, "clean": True})
    assert hashlib.sha256(runner.canonical_json({"profile": profile, "details": details})).hexdigest() == \
        M1_PROFILE_SHA256
    prepared = {name: reader.read(f"prepared/{name}") for name in goal.PREPARED_FILES}
    core = count_profile.profile_core(
        network=count_profile.parse_network(prepared["network.net.xml"], prepared["control.add.xml"]),
        loops=count_profile.parse_field_loops(prepared["field-loops.add.xml"]),
        intended=count_profile.parse_intentions(prepared["lane-intentions.json"]),
        main_rows=csv_rows(prepared["expected-counts.csv"]), init_rows=csv_rows(prepared["initialization-counts.csv"]),
        hold_nodes=list(loaded.run.junction_body_hold_nodes), e1_begin_s=loaded.observation.e1_begin_s,
        e1_period_s=loaded.observation.e1_period_s, requests=count_profile.parse_demand(prepared["demand.rou.xml"]),
        routes=count_profile.parse_vehicle_routes(reader.read("evaluation/native/vehicle-routes.xml")),
        field_counts=reader.read("evaluation/native/field-counts.xml"),
        passages=count_profile.read_passage_report(reader, goal.PARENT_PASSAGES)["passages"])
    assert core["score"] == profile["score"] and core["decomp"] == profile["decomp"]
    assert core["hotspots"] == profile["hotspots"] and core["score_rows"] == details["rows.csv"]
    assert core["balance_rows"] == details["field-balance.csv"]
    assert [{k: v for k, v in loc.items() if k != "anchors_all"} for loc in core["localized"]] == \
        profile["localization"]["rows"]
