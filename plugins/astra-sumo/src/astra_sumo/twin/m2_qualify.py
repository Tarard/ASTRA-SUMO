"""Positive qualification (protocol v3 section 6), confirmation size and the frozen C3 plan of M2 stage C2. Pure.

Qualification of a twin-truth op on at least 6 panel keys, each paired with the cached parent draw on the same key,
on the primary objective (ms4):

- Q1: the paired L1 energy distance is at least g, with its exact within-pair swap p <= alpha
  (``ensemble.paired_energy_test``);
- Q2: every leave-one-key-out ED is at least g/2;
- Q3: the mechanistic check C8 holds on at least 5/6 of the keys: the signed count change on the op's free-flow
  planned rows (``k3.shift_effect``) is at least half their L1 on that key (v2 stage 3);
- Q4: a truth ensemble of at least 8 fresh draws of the op exists on keys disjoint from every panel key, each run
  completed and scored; power is then measured against it (``subsample_power``, ``approximate_power``).

These are exploratory screens on exposed keys (A1.8): they qualify an op as power evidence, they do not establish a
population ED of g. ``subsample_power`` runs the production evaluator (``m2_stage.evaluate_stage``, screen mode) on
every disjoint K-subset of the op's panel draws and of independent parent draws, against the truth ensemble: an
observed acceptance frequency over dependent subsets, not a rate. ``approximate_power`` extrapolates to the
confirmation K by a normal approximation with delete-one jackknife variances (approximate, plug-in).

The confirmation size follows decision 4 as C1 fixed it: rho = sd(d) / re-key sd on ms4, with d_k the per-key ms4
TAE difference of the op and the parent against the real counts, and K = 8 iff rho <= 0.35, else 16.
Explore evidence over simulated counts under the supplied model: no field truth, no promotion authority.
"""

from __future__ import annotations

import hashlib
import itertools
import math
import statistics
from collections import Counter
from collections.abc import Mapping, Sequence
from fractions import Fraction

from . import ensemble, k3, m2_stage

QUALIFY_SCHEMA = "astra.twin-m2-qualification/v1"
FREEZE_SCHEMA = "astra.twin-m2-c3-freeze/v1"
MIN_PANEL_KEYS = 6
MIN_TRUTH_DRAWS = 8
C8_SHARE = Fraction(5, 6)
REKEY_SD_MS4_C1 = 158.074  # C1: re-key sd of ms4 PR_k over the 13 healthy parent draws (all13_healthy)
GUARD_STEPS = ("H2", "H3", "N1", "N2", "N3", "N4")

Counts = Mapping[str, int]


# --- Q1 to Q3 on the panel keys --------------------------------------------------------------------------------------

def mechanistic_check(op: Mapping[str, Counts], parent: Mapping[str, Counts], planned: Mapping[str, int]) -> dict:
    """C8: per key, sum_r sign(planned_r) (op_r - parent_r) over the planned rows, against half the planned L1."""
    keys = _paired(op, parent)
    l1 = sum(abs(value) for value in planned.values())
    signed = {key: sum((1 if value > 0 else -1) * (op[key][row] - parent[key][row]) for row, value in planned.items())
              for key in keys}
    hits = sum(2 * value >= l1 for value in signed.values()) if l1 else 0
    needed = math.ceil(C8_SHARE * len(keys))
    return {"rule": "signed change on the planned rows >= L1/2 on >= 5/6 of the keys", "planned_l1": l1,
            "signed_change": signed, "hits": hits, "needed": needed, "keys": len(keys),
            "passes": bool(l1) and hits >= needed}


def _paired(op: Mapping[str, Counts], parent: Mapping[str, Counts]) -> list[str]:
    if set(op) != set(parent):
        raise ValueError(f"The op and the parent need the same panel keys: {sorted(set(op) ^ set(parent))}")
    return sorted(op)


def qualify_panel(op: Mapping[str, Counts], parent: Mapping[str, Counts], planned: Mapping[str, int], *,
                  g: float = 10.0, alpha: float = 0.05, objective: str = ensemble.PRIMARY_OBJECTIVE) -> dict:
    """Q1 to Q3 of v3 section 6 for one op on its panel keys (op and parent counts on the same main rows)."""
    keys = _paired(op, parent)
    if len(keys) < MIN_PANEL_KEYS:
        raise ValueError(f"Qualification needs at least {MIN_PANEL_KEYS} panel keys, got {len(keys)}")
    rows = ensemble.main_rows(list(op[keys[0]]))
    op_values = {key: ensemble.objective_values(op[key], rows, objective) for key in keys}
    parent_values = {key: ensemble.objective_values(parent[key], rows, objective) for key in keys}
    value_keys = list(op_values[keys[0]])
    paired = ensemble.paired_energy_test(op_values, parent_values, value_keys)
    loo = {}
    for left_out in keys:
        rest = [key for key in keys if key != left_out]
        loo[left_out] = ensemble.paired_energy_test({k: op_values[k] for k in rest},
                                                    {k: parent_values[k] for k in rest}, value_keys)["ed"]
    c8 = mechanistic_check(op, parent, planned)
    g_q, alpha_q = ensemble._exact(g), ensemble._exact(alpha)
    checks = {"Q1_energy": paired["ed"] >= g_q and paired["p"] <= alpha_q,
              "Q2_leave_one_out": min(loo.values()) >= g_q / 2,
              "Q3_mechanistic_c8": c8["passes"]}
    return {"objective": objective, "keys": keys, "g": g, "alpha": alpha,
            "ed": paired["ed"], "ed_float": float(paired["ed"]), "swap_p": paired["p"],
            "swap_p_float": float(paired["p"]), "min_p": paired["min_p"],
            "loo_ed": loo, "loo_ed_float": {key: float(value) for key, value in loo.items()},
            "loo_min": float(min(loo.values())), "loo_max": float(max(loo.values())),
            "c8": c8, "checks": checks, "passes": all(checks.values()),
            "class": k3.classify(float(paired["ed"]), g)}


def truth_ensemble_check(truth: Mapping[str, Mapping], *, panel_fingerprints: Sequence[str],
                         rows: Sequence[str], required: int = MIN_TRUTH_DRAWS) -> dict:
    """Q4's precondition: at least ``required`` truth draws, each scored on every row, on distinct keys disjoint from
    every panel key (by fingerprint). ``truth`` maps a key to a stage draw (``panel.stage_draw``)."""
    prints = {key: (draw.get("run") or {}).get("key_sha256") for key, draw in truth.items()}
    repeated = sorted(fp for fp, n in Counter(prints.values()).items() if n > 1)
    overlap = sorted(key for key, fp in prints.items() if fp in set(panel_fingerprints))
    unscored = sorted(key for key, draw in truth.items() if set(draw.get("counts") or {}) != set(rows))
    missing = sorted(key for key, fp in prints.items() if not m2_stage.is_fingerprint(fp))
    failures = ([f"{len(truth)} truth draws, at least {required} needed"] if len(truth) < required else []) + (
        [f"truth draws share a key {repeated}"] if repeated else []) + (
        [f"truth draws on panel keys {overlap}"] if overlap else []) + (
        [f"truth draws not scored on every row {unscored}"] if unscored else []) + (
        [f"truth draws without a key fingerprint {missing}"] if missing else [])
    unhealthy = sorted(key for key, draw in truth.items() if m2_stage.failure_classes(draw.get("health") or {}))
    return {"rule": f">= {required} fresh truth draws on keys disjoint from the panel, every one scored",
            "draws": len(truth), "failures": failures, "passes": not failures,
            "unhealthy_draws": unhealthy, "note": "unhealthy truth draws stay in the ensemble (A2.2)"}


# --- power against the truth ensemble --------------------------------------------------------------------------------

def screen_gate(draws: int) -> dict:
    """The frozen K = 8 gate with its draw count set to ``draws`` for a screen (margins not rescaled: tighter than a
    K < 8 calibration would give, so guards fire more easily)."""
    gate = dict(m2_stage.frozen_gate(8))
    gate.update(name=f"m2-c2-screen-K{draws}", draws=draws)
    return gate


def subsample_power(candidate: Mapping[str, Mapping], reference: Mapping[str, Mapping], *, real: Counts,
                    truth: Mapping[str, Counts], draws: int, max_cases: int = 2000) -> dict:
    """Every pair of ``draws``-subsets (candidate panel draws, independent parent draws on other keys) judged by
    ``m2_stage.evaluate_stage`` in screen mode against the truth ensemble, with the screen gate of that size.

    Reports how often the verdict is ``improved``, how often test I alone passes (p <= alpha and T <= -g), how often
    every guard step H2 to N4 holds, and the T and p values. The subsets overlap, so the frequencies are dependent
    observations on these pools, not rates.
    """
    if set(candidate) & set(reference):
        raise ValueError("The power subsets need disjoint candidate and reference keys")
    gate = screen_gate(draws)
    cases = list(itertools.product(itertools.combinations(sorted(candidate), draws),
                                   itertools.combinations(sorted(reference), draws)))
    if len(cases) > max_cases:
        raise ValueError(f"{len(cases)} subset pairs exceed max_cases={max_cases}")
    labels: Counter = Counter()
    test_i = guards_hold = 0
    statistics_t, p_values = [], []
    for chosen_c, chosen_r in cases:
        verdict = m2_stage.evaluate_stage({k: candidate[k] for k in chosen_c}, {k: reference[k] for k in chosen_r},
                                          real=real, gate=gate, truth=dict(truth), mode="screen")
        labels[verdict["label"]] += 1
        if verdict["label"] == "blocked":
            continue
        steps = verdict["steps"]
        test_i += steps["I"]["passes"]
        guards_hold += all(steps[name]["passes"] for name in GUARD_STEPS)
        statistics_t.append(float(steps["I"]["T"]))
        p_values.append(float(steps["I"]["p"]))
    n = len(cases)
    return {"draws": draws, "cases": n, "labels": dict(sorted(labels.items())),
            "improved": labels["improved"], "improved_fraction": round(labels["improved"] / n, 4),
            "test_I_passes": test_i, "test_I_fraction": round(test_i / n, 4),
            "guards_hold": guards_hold, "guards_hold_fraction": round(guards_hold / n, 4),
            "T": _summary(statistics_t), "p": _summary(p_values), "gate_sha256": m2_stage.gate_sha256(gate),
            "note": "observed frequencies over overlapping subsets of fixed pools; no rate"}


def _summary(values: Sequence[float]) -> dict | None:
    if not values:
        return None
    return {"min": round(min(values), 3), "median": round(statistics.median(values), 3),
            "max": round(max(values), 3), "mean": round(statistics.fmean(values), 3)}


def approximate_power(candidate: Sequence[Counts], reference: Sequence[Counts], truth: Sequence[Counts], *,
                      g: float = 10.0, alpha: float = 0.05, sizes: Sequence[int] = (5, 6, 8, 16),
                      objective: str = ensemble.PRIMARY_OBJECTIVE) -> dict:
    """Normal approximation of the confirmation's power at each K of ``sizes``.

    T = fairCRPS(candidate pool) - fairCRPS(reference pool) against the truth ensemble estimates the population
    difference without bias. Each arm's variance at its pool size is the delete-one jackknife variance of its fair
    CRPS, scaled to K draws by n / K. Under the alternative T_K ~ N(T, v_c / K + v_r / K); the permutation critical
    value is approximated by z_{1-alpha} sd_0(K) with sd_0(K)^2 = 2 v_r / K (both arms parent), and the acceptance
    needs T <= -max(g, that value): power(K) = Phi((-max(g, z sd_0) - T) / sd(K)). Approximate and plug-in.
    """
    rows = ensemble.main_rows(list(truth[0]))
    c = [ensemble.objective_values(x, rows, objective) for x in candidate]
    r = [ensemble.objective_values(x, rows, objective) for x in reference]
    y = [ensemble.objective_values(x, rows, objective) for x in truth]
    keys = list(y[0])
    if len(c) < 3 or len(r) < 3:
        raise ValueError("The jackknife needs at least three draws per arm")
    statistic = float(ensemble.fair_crps(c, y, keys) - ensemble.fair_crps(r, y, keys))

    def unit_variance(arm: list[dict]) -> float:
        loo = [float(ensemble.fair_crps(arm[:i] + arm[i + 1:], y, keys)) for i in range(len(arm))]
        mean = statistics.fmean(loo)
        return (len(arm) - 1) / len(arm) * sum((x - mean) ** 2 for x in loo) * len(arm)

    v_c, v_r = unit_variance(c), unit_variance(r)
    z = statistics.NormalDist().inv_cdf(1 - alpha)
    found = {}
    for size in sizes:
        sd = math.sqrt((v_c + v_r) / size)
        sd_0 = math.sqrt(2 * v_r / size)
        critical = max(g, z * sd_0)
        found[str(size)] = {"sd_T": round(sd, 3), "sd_null": round(sd_0, 3), "critical": round(critical, 3),
                            "power": round(statistics.NormalDist().cdf((-critical - statistic) / sd), 4) if sd else
                            float(statistic <= -critical)}
    return {"rule": "normal approximation, delete-one jackknife variances scaled by n/K (approximate, plug-in)",
            "objective": objective, "T_pools": round(statistic, 3), "pools": [len(c), len(r)],
            "truth_draws": len(y), "unit_variance": {"candidate": round(v_c, 3), "reference": round(v_r, 3)},
            "by_K": found}


# --- confirmation size (decision 4, as fixed in C1) ------------------------------------------------------------------

def k_rule(op: Mapping[str, Counts], parent: Mapping[str, Counts], real: Counts, *,
           rekey_sd: float = REKEY_SD_MS4_C1, objective: str = ensemble.PRIMARY_OBJECTIVE) -> dict:
    """rho = sd(d) / re-key sd with d_k = TAE_obj(op_k, real) - TAE_obj(parent_k, real) on the shared panel keys;
    K = 8 iff rho <= 0.35, else 16 (``ensemble.confirmation_draws``)."""
    keys = _paired(op, parent)
    rows = ensemble.main_rows(list(real))
    target = ensemble.objective_values(real, rows, objective)
    value_keys = list(target)

    def tae(counts: Counts) -> int:
        return ensemble.l1(ensemble.objective_values(counts, rows, objective), target, value_keys)

    d = [tae(op[key]) - tae(parent[key]) for key in keys]
    rho = ensemble.crn_ratio(d, rekey_sd)
    return {"rule": "K = 8 iff rho = sd(d) / re-key sd <= 0.35 on ms4, else 16 (decision 4, C1 re-key sd)",
            "objective": objective, "d": dict(zip(keys, d)), "sd_d": round(statistics.stdev(d), 3),
            "rekey_sd": rekey_sd, "rho": round(rho, 4), "threshold": ensemble.CRN_EFFECTIVE_RHO,
            "K": ensemble.confirmation_draws(rho)}


# --- the C3 candidate and its frozen plan ----------------------------------------------------------------------------

def choose_c3_candidate(entries: Sequence[Mapping], *, preference: Sequence[str]) -> dict:
    """The pre-registered choice among the qualified ops.

    ``entries`` hold per op: ``name``, ``qualified``, ``K`` (its rho rule), ``approximate_power`` (by K as str),
    ``guards_hold_fraction`` and ``improved_fraction`` (its K = 6 subsample screen). Pool: the qualified ops; when at
    least one of them holds every guard step in at least half of its K = 6 subsamples, only those. Choice: the
    larger approximate power at the op's own K (3 decimals), then the larger K = 6 improved fraction, then the order
    of ``preference``. No qualified op means no candidate (the stage gate fails).
    """
    qualified = [entry for entry in entries if entry["qualified"]]
    if not qualified:
        return {"candidate": None, "pool": [], "reason": "no op qualifies"}
    guarded = [entry for entry in qualified if entry["guards_hold_fraction"] >= 0.5]
    pool = guarded or qualified
    order = {name: i for i, name in enumerate(preference)}
    ranked = sorted(pool, key=lambda entry: (-round(entry["approximate_power"][str(entry["K"])], 3),
                                             -entry["improved_fraction"], order.get(entry["name"], len(order))))
    return {"candidate": ranked[0]["name"], "pool": [entry["name"] for entry in pool],
            "guard_filter_applied": bool(guarded), "ranking": [entry["name"] for entry in ranked]}


SPARES_PER_ARM = {8: 2, 16: 1}
C3_CAPS = {8: 36, 16: 52}


def c3_freeze(*, candidate: Mapping, target: Mapping, k_rule_result: Mapping, code: Mapping, c2: Mapping,
              goal: Mapping, family: str = "depart_jitter", seed: int = 104) -> dict:
    """The frozen C3 plan: candidate op, target ensemble, K, the three arms of K fresh secret keys each (generated with
    os.urandom at C3 time and committed by SHA-256 before the first C3 run), the gate hash and the code identity."""
    draws = k_rule_result["K"]
    gate = m2_stage.frozen_gate(draws)
    spares = SPARES_PER_ARM[draws]
    arms = {}
    for arm, op in (("reference", None), ("candidate", candidate["op"]), ("negative_control", None)):
        arms[arm] = {"op": op, "draws": draws, "spares": spares, "label_prefix": f"c3-{arm.replace('_', '-')}-",
                     "keys": f"{draws} fresh secret keys, os.urandom(32) each, generated at C3 time; only their "
                             "sha256 fingerprints are committed, in order, before the first C3 run",
                     "spare_keys": f"{spares} further fresh secret keys, committed with the arm, used in order only "
                                   "to replace a run that crashed or timed out (A2.2)"}
    total = 3 * draws + 3 * spares
    if total > C3_CAPS[draws]:
        raise ValueError(f"C3 needs {total} runs including spares, above the cap {C3_CAPS[draws]}")
    return {"schema": FREEZE_SCHEMA,
            "stage": "M2 C3 confirmation plan, frozen after C2 and before any C3 run (no C3 key exists yet)",
            "candidate": dict(candidate), "target": dict(target), "K": draws, "k_rule": dict(k_rule_result),
            "arms": arms,
            "evaluations": [
                {"name": "positive", "candidate_arm": "candidate", "reference_arm": "reference", "mode": "confirm",
                 "gate_C3": "label improved (every guard step holds, A1.3 unpaired exact test)"},
                {"name": "negative_control", "candidate_arm": "negative_control", "reference_arm": "reference",
                 "mode": "confirm", "gate_C3": "label is not improved"}],
            "gate": {"name": gate["name"], "draws": draws, "sha256": m2_stage.gate_sha256(gate),
                     "objective": gate["objective"], "g": gate["g"], "alpha": gate["alpha"]},
            "commitment_fields": ["gate_sha256", "target_sha256", "candidate_keys_sha256",
                                  "reference_keys_sha256", "candidate_spare_keys_sha256",
                                  "reference_spare_keys_sha256", "candidate_binding", "reference_binding"],
            "runs": {"planned": 3 * draws, "spares": 3 * spares, "cap": C3_CAPS[draws],
                     "spare_rule": "crashed or timed-out runs only; unhealthy complete runs are kept and scored "
                                   "(A2.2)"},
            "family": family, "seed": seed, "goal": dict(goal), "code": dict(code), "c2": dict(c2),
            "claim": m2_stage.CLAIM}


def freeze_sha256(freeze: Mapping) -> str:
    return hashlib.sha256(m2_stage.canonical_json(freeze)).hexdigest()
