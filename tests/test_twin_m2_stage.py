"""M2 stage evaluator (twin/m2_stage.py) and the exact unpaired permutation test, with zero SUMO runs.

Pins the decisions and amendments A1 (before C1) and A2 (after C1): the unpaired exact test over all C(2K, K)
relabelings gates ``improved`` (A1.3) while the paired CRN sign-flip p is reported only; S is report-only and the
L-type backlog a flag (A1.5, decisions 5 and 6); N1 is enforced on all 182 rows inside the one production evaluator;
health compares the arms (A2.1) and unhealthy draws stay scored (A2.2), with spares for crashed or timed-out runs
only, checked against the ledger. A confirmation needs committed bindings, fresh secret 64-hex keys, truth draws on
other runs and one measurement-code identity. Synthetic draws use the real row ids and counts of the committed B0
fixture, and synthetic ledgers stand in for the store; the store test is skipped with its reason when the store is
absent.
"""

from __future__ import annotations

import hashlib
import itertools
import json
import math
import os
import random
import subprocess
import sys
from collections import Counter
from fractions import Fraction
from pathlib import Path

import pytest

from astra_sumo.twin import ensemble, ensemble_profile, guards, m2_stage, panel

from test_twin_judge import ROOT

FIXTURE = ROOT / "tests" / "fixtures" / "twin_m2" / "b0_panel.json"
STORE = Path(os.environ.get("ASTRA_TWIN_M2_STORE", r"E:\twin-store\m2-pilot"))
B0 = json.loads(FIXTURE.read_text(encoding="utf-8"))
REAL = dict(B0["target"])
ROWS = sorted(REAL)
MAIN = ensemble.main_rows(ROWS)
INIT = [row for row in ROWS if ensemble.parse_row(row)[1] < ensemble.MAIN_BEGIN_S]
X = "R:0535Z4@3600"  # M's row: the first main bin of its field, so it enters exactly one ms4 window
KEYS6 = [f"k{i}" for i in range(1, 7)]
BY_FIELD: dict[str, list[str]] = {}
for _row in MAIN:
    BY_FIELD.setdefault(ensemble.parse_row(_row)[0], []).append(_row)
# The judge health records of C1's c1-parent-k13 (healthy) and c1-parent-k14 (1 side collision, 1 teleport).
K13_HEALTH = {"status": "measured", "healthy": True, "native_status": "pass",
              "completion": {"loaded": 8589, "inserted": 8589, "arrived": 8589, "running": 0, "waiting": 0,
                             "teleports": 0, "collisions": 0, "discarded": 0},
              "observer_complete": True, "missing_field_bins": 0, "vehicle_count": 8589, "stall_status": "completed",
              "simulation_time_s": 14400.0}
K14_HEALTH = {**K13_HEALTH, "status": "invalid", "healthy": False,
              "completion": {**K13_HEALTH["completion"], "teleports": 1, "collisions": 1}}
IDENTITY = {"measurement_code_sha256": "de7747aadc218dc1bf50e87447e89e34d5bf89ae7d0b4fe0754225e9b3987dc1",
            "runtime_sha256": "3c7d6e80ca310413e2f1c8075b282fac615eefb5fb8fea243f719ee551e3a9ec"}
# Arm bindings as panel.stage_draw records them: the candidate op, the parent, and the op behind a truth ensemble.
BINDINGS = {arm: {"goal_sha256": hashlib.sha256(b"goal").hexdigest(), "scenario_op": op,
                  "base_demand_sha256": hashlib.sha256(b"demand").hexdigest(), "family": "depart_jitter", "seed": 104}
            for arm, op in (("candidate", {"op": "cell_depart_shift", "cell": "0119Z12", "shift_s": -60.0}),
                            ("reference", None),
                            ("truth", {"op": "cell_depart_shift", "cell": "0535Z4", "shift_s": 60.0}))}
AUTO = object()
# gate_sha256 of the frozen gates: any change to a margin, a rule or the basis changes these (A1.7).
FROZEN_GATE_SHA256 = {8: "2ca2039fa178d47f3b56de8e039340d96f8b40addacf0b17af2c51734c9fbdf7",
                      16: "cd7db25ef9725956b4d83c56a5b5902f87c01044c83cb68af1e661cfe35e363a"}


def _fp(name: str) -> str:
    return hashlib.sha256(name.encode()).hexdigest()


def _stats(**changes) -> dict:
    entry = {edge: (0.1 if kind == "quiet" else 50.0) for edge, kind in m2_stage.ENTRY_CLASSES.items()}
    entry[guards.NETWORK] = 300.0
    entry.update(changes.pop("entry", {}))
    return {"entry_p95": entry, "brakings": 8, "post_window": 40, **changes}


def _draw(counts: dict, key: str, arm: str, *, health: dict | None = None, stats: dict | None = None,
          public: bool | None = False, identity: dict | None = None) -> dict:
    return {"counts": dict(counts), "health": dict(health or K13_HEALTH), "guard_stats": stats or _stats(),
            "run": {"id": f"{arm}-{key}", "key_sha256": _fp(key), "key_public": public, "binding": dict(BINDINGS[arm]),
                    "identity": dict(identity or IDENTITY)}}


def _counts(**changes) -> dict:
    """The real counts with ``row -> delta`` changes (row ids with ':' and '@' are passed through a dict)."""
    return {row: REAL[row] + changes.get(row, 0) for row in ROWS}


def _shift(rows, delta: int) -> dict:
    return {row: REAL[row] + (delta if row in set(rows) else 0) for row in ROWS}


def _arms(candidate: list[dict], reference: list[dict], *, shared: bool = False, health=None, stats=None,
          public: bool | None = False) -> tuple[dict, dict]:
    keys_c = [f"k{i:02d}" for i in range(len(candidate))] if shared else [f"c{i:02d}" for i in range(len(candidate))]
    keys_r = keys_c if shared else [f"r{i:02d}" for i in range(len(reference))]
    health, stats = health or {}, stats or {}
    cand = {key: _draw(c, key, "candidate", health=health.get(("c", i)), stats=stats.get(("c", i)), public=public)
            for i, (key, c) in enumerate(zip(keys_c, candidate))}
    ref = {key: _draw(r, key, "reference", health=health.get(("r", i)), stats=stats.get(("r", i)), public=public)
           for i, (key, r) in enumerate(zip(keys_r, reference))}
    return cand, ref


def _ledger_row(run: dict, outcome: str = "scored", **changes) -> dict:
    return {"id": run["id"], "request_sha256": _fp(f"request:{run['id']}"), "key_sha256": run["key_sha256"],
            "public": run.get("key_public"), "outcome": outcome,
            "binding": {name: run["binding"].get(name) for name in m2_stage.LEDGER_BINDING_FIELDS}, **changes}


def _ledger(*arms: dict) -> list[dict]:
    """A synthetic ledger: one scored row per run of the given arms."""
    return [_ledger_row(draw["run"]) for arm in arms for draw in arm.values()]


def _commitment(candidate: dict, reference: dict, gate: dict, truth: dict | None = None, *,
                spares: dict | None = None) -> dict:
    spares = spares or {}
    return {"gate_sha256": m2_stage.gate_sha256(gate), "target_sha256": m2_stage.target_sha256(REAL, truth),
            "candidate_keys_sha256": [d["run"]["key_sha256"] for d in candidate.values()],
            "reference_keys_sha256": [d["run"]["key_sha256"] for d in reference.values()],
            "candidate_spare_keys_sha256": list(spares.get("candidate", [])),
            "reference_spare_keys_sha256": list(spares.get("reference", [])),
            "candidate_binding": dict(BINDINGS["candidate"]), "reference_binding": dict(BINDINGS["reference"])}


def _stage(candidate: dict, reference: dict, *, size: int = 8, mode: str = "screen", truth=None, gate=None,
           commitment=None, truth_runs=None, ledger_runs=AUTO) -> dict:
    gate = gate or m2_stage.frozen_gate(size)
    if mode == "confirm" and commitment is None:
        commitment = _commitment(candidate, reference, gate, truth)
    if ledger_runs is AUTO:
        ledger_runs = _ledger(candidate, reference) if mode == "confirm" else None
    return m2_stage.evaluate_stage(candidate, reference, real=REAL, gate=gate, truth=truth, mode=mode,
                                   commitment=commitment, truth_runs=truth_runs, ledger_runs=ledger_runs)


def _truth(counts: list[dict], *, arm: str = "truth") -> tuple[dict, dict]:
    """A truth ensemble by key and its run records (other runs on other keys than the arms')."""
    keys = [f"t{i}" for i in range(len(counts))]
    draws = {key: _draw(c, key, arm) for key, c in zip(keys, counts)}
    return ({key: draw["counts"] for key, draw in draws.items()},
            {key: {"run": draw["run"], "health": draw["health"]} for key, draw in draws.items()})


def _ms4(counts: dict) -> dict:
    return ensemble.objective_values(counts, MAIN, "ms4")


def _improving() -> tuple[dict, dict]:
    """A candidate that fits every main row but X (+11, within the K = 8 N1 margin) and a reference 1 off on every
    other main row: T = 11 - 363, improved in confirmation once H1 holds."""
    return _counts(**{X: 11}), {row: REAL[row] + (1 if row in MAIN and row != X else 0) for row in ROWS}


def _blocked(verdict: dict, fragment: str) -> None:
    assert (verdict["status"], verdict["label"], verdict["rate"], verdict["report"]) == ("blocked", "blocked", "none",
                                                                                         None)
    assert any(fragment in failure for failure in verdict["steps"]["H1"]["failures"]), verdict["steps"]["H1"]


# --- the frozen gate -------------------------------------------------------------------------------------------------

def test_frozen_gate_holds_the_decisions_and_the_c1_margins():
    eight, sixteen = m2_stage.frozen_gate(8), m2_stage.frozen_gate(16)
    assert (eight["objective"], eight["g"], eight["alpha"], eight["init52_target"]) == ("ms4", 10, 0.05, "real")
    assert eight["health"] == {"parent_failure_classes": ["collision", "teleport"], "unhealthy_margin": 1,
                               "parent_unhealthy": {"k": 1, "n": 14}}
    assert sixteen["health"]["unhealthy_margin"] == 2
    assert (eight["n1_row_margin"], sixteen["n1_row_margin"]) == (11.62, 8.31)  # the frozen K = 16 N1 margin (A1.7)
    assert (eight["n2_init52_margin"], sixteen["n2_init52_margin"]) == (18.787, 13.284)
    # N3: max(5-vehicle floor, MC q99, exact K = 5 split q99 27.1 x sqrt(5/K)); N4 likewise from 40.2.
    assert (eight["n3_totals13_margin"], sixteen["n3_totals13_margin"]) == (21.424, 15.149)
    assert (eight["n4_main130_margin"], sixteen["n4_main130_margin"]) == (44.502, 31.077)
    assert eight["n3_floor"] == 5.0 and eight["guard_target"] == "stage"
    busy = sorted(edge for edge, kind in m2_stage.ENTRY_CLASSES.items() if kind == "busy")
    assert len(busy) == 6 and len(m2_stage.ENTRY_CLASSES) == 13
    # v3 section 5 declares blocking backstops on the network p95 and 44532531 only; the other busy edges report.
    assert eight["h3_margins"] == {"network_p95": 112.471, "entry_p95:44532531": 119.774, "brakings": 8.484,
                                   "post_window": 21.217}
    assert sixteen["h3_margins"] == {"network_p95": 79.529, "entry_p95:44532531": 84.693, "brakings": 5.999,
                                     "post_window": 15.002}
    assert eight["h3_report_margins"] == {"entry_p95:-142944318": 14.392, "entry_p95:189764301": 4.816,
                                          "entry_p95:25866659#1": 12.462, "entry_p95:31106387": 1.521,
                                          "entry_p95:61578529#1": 42.296}
    assert sorted(sixteen["h3_report_margins"]) == sorted(f"entry_p95:{e}" for e in busy if e != "44532531")
    assert (eight["rekey_sd_ms4"], eight["rho_threshold"], eight["delta_report"]) == (155.167, 0.35, 5)
    assert eight["entry_classes"] == m2_stage.ENTRY_CLASSES
    assert (eight["quiet_entry_floor_s"], eight["backlog_floor_s"]) == (10.0, 10.0)
    assert eight["ensemble_profile"]["groups"] == {"corridor:24483344": ["edge:24483344#0", "edge:24483344#1"]}
    assert eight["ensemble_profile"]["sha256"] == ensemble_profile.corridor_rule_sha256()
    assert eight["margin_panel_identity"] == m2_stage.MARGIN_PANEL_IDENTITY
    with pytest.raises(ValueError, match="no health margin"):
        m2_stage.frozen_gate(5)
    with pytest.raises(ValueError, match="Unknown mode"):
        m2_stage.evaluate_stage({}, {}, real=REAL, gate=eight, mode="release")


def test_frozen_gate_hashes_are_pinned():
    """Every margin, floor, rule and basis string of the frozen gates enters these hashes (A1.7)."""
    assert {size: m2_stage.gate_sha256(m2_stage.frozen_gate(size)) for size in (8, 16)} == FROZEN_GATE_SHA256
    assert m2_stage.gate_sha256(m2_stage.frozen_gate(8)) == m2_stage.gate_sha256(m2_stage.frozen_gate(8))


def test_malformed_gates_are_refused_before_any_step():
    eight = m2_stage.frozen_gate(8)
    for broken in ({**eight, "h3_margins": {k: v for k, v in eight["h3_margins"].items() if k != "brakings"}},
                   {**eight, "h3_margins": {**eight["h3_margins"], "entry_p95:26623877": 1.0}},  # a quiet edge
                   {**eight, "h3_report_margins": {**eight["h3_report_margins"], "entry_p95:44532531": 1.0}},
                   {**eight, "h3_report_margins": {"brakings": 1.0}}):
        with pytest.raises(ValueError, match="busy entry edges only"):
            m2_stage.evaluate_stage({}, {}, real=REAL, gate=broken)
    with pytest.raises(ValueError, match="guard_target"):
        m2_stage.evaluate_stage({}, {}, real=REAL, gate={**eight, "guard_target": "truth"})
    with pytest.raises(ValueError, match="lacks"):
        m2_stage.evaluate_stage({}, {}, real=REAL, gate={k: v for k, v in eight.items() if k != "guard_target"})


def test_the_corridor_merge_rule_is_a_frozen_constant():
    """A1.7: the ensemble profile merges 24483344#0 and #1 by a frozen constant, hashed into the frozen gate."""
    assert ensemble_profile.CORRIDOR_GROUPS == {"corridor:24483344": ("edge:24483344#0", "edge:24483344#1")}
    assert ensemble_profile.corridor_rule() == {"rule": ensemble_profile.CORRIDOR_RULE, "groups": {
        "corridor:24483344": ["edge:24483344#0", "edge:24483344#1"]}}
    assert ensemble_profile.corridor_rule_sha256() == hashlib.sha256(
        m2_stage.canonical_json(ensemble_profile.corridor_rule())).hexdigest()
    weights = {"edge:24483344#0": 30, "edge:24483344#1": 25, "entry:44532531": 78, "field:0119Z5": 40}
    assert ensemble_profile.merge_declared_corridors(weights) == Counter(
        {"corridor:24483344": 55, "entry:44532531": 78, "field:0119Z5": 40})


# --- N1 enforced by the stage evaluator (A1.5) -----------------------------------------------------------------------

def _n1_case(worsening: int, size: int, *, shared: bool = False) -> tuple[dict, dict]:
    """Every main row of the reference is 1 off the real counts except X; the candidate fits every row except X,
    which it worsens by ``worsening``. Arms are constant, so the unpaired p is its minimum 1 / C(2K, K)."""
    reference = {row: REAL[row] + (1 if row in MAIN and row != X else 0) for row in ROWS}
    candidate = _counts(**{X: worsening})
    return _arms([candidate] * size, [reference] * size, shared=shared)


@pytest.mark.parametrize("size", [8, 16])
def test_one_row_worsening_beyond_the_n1_margin_is_rejected_while_the_rest_improve(size):
    candidate, reference = _n1_case(20, size)
    verdict = _stage(candidate, reference, size=size, mode="confirm")
    steps = verdict["steps"]
    assert (verdict["status"], verdict["label"], verdict["first_failure"], verdict["rate"]) == (
        "measured", "row_regress", "N1", "none")
    assert (steps["N1"]["worst_row"], steps["N1"]["value"]) == (X, 20) and not steps["N1"]["passes"]
    assert steps["N1"]["rows"] == 182 and list(steps["N1"]["rows_above_margin"]) == [X]
    # Without N1 the improvement branch would accept: exact p at its minimum and T far below -g.
    assert steps["I"]["passes"] and steps["I"]["p"] == Fraction(1, math.comb(2 * size, size))
    assert steps["I"]["T"] == 20 - 363 and steps["I"]["splits"] == math.comb(2 * size, size)
    assert all(steps[name]["passes"] for name in ("H1", "H2", "H3", "N2", "N3", "N4"))
    assert steps["N3"]["T"] == steps["N4"]["T"] == 20 - 129


def test_the_n1_margin_is_the_frozen_value_at_each_k():
    candidate, reference = _n1_case(11, 8)  # 11 <= 11.62: N1 holds and the confirmation is improved at rate alpha
    verdict = _stage(candidate, reference, size=8, mode="confirm")
    assert (verdict["label"], verdict["rate"], verdict["first_failure"]) == ("improved", "alpha", None)
    candidate, reference = _n1_case(9, 16)  # 9 > 8.31 at K = 16
    assert _stage(candidate, reference, size=16, mode="confirm")["label"] == "row_regress"
    candidate, reference = _n1_case(8, 16)
    assert _stage(candidate, reference, size=16, mode="confirm")["label"] == "improved"


def test_screening_improved_carries_no_rate():
    candidate, reference = _n1_case(11, 8)
    verdict = _stage(candidate, reference, size=8)
    assert (verdict["mode"], verdict["label"], verdict["rate"]) == ("screen", "improved", "none")


def test_row_growth_hidden_by_an_aggregate_gain_is_reported():
    """Review A1: 11 rows each 11 worse (121 vehicles) while every other main row improves by 1 passes N1 (11 <=
    11.62) and is improved; the count and sum of the row growths above g/2 are reported for the owner."""
    reference = _shift(MAIN, 1)
    bad = [BY_FIELD[field][3] for field in sorted(BY_FIELD)[:11]]
    candidate = {row: REAL[row] + (12 if row in bad else 0) for row in ROWS}
    verdict = _stage(*_arms([candidate] * 8, [reference] * 8), mode="confirm")
    assert (verdict["label"], verdict["rate"], verdict["steps"]["I"]["T"]) == ("improved", "alpha", -100)
    report = verdict["steps"]["N1"]["growth_report"]
    assert (report["threshold"], report["rows_above"], report["growth_sum_above"]) == (5.0, 11, 121)
    assert report["positive_growth_sum"] == 121 and report["never_gates"]
    assert verdict["steps"]["N1"]["value"] == 11 and verdict["steps"]["N1"]["passes"]


def test_codex_paired_n1_case_is_rejected_although_the_paired_rule_accepts():
    """Codex's original form: the arms on the same 8 CRN keys. The paired sign-flip p is 1/256 and the paired rule
    passes, but it is report only and N1 rejects; screening carries no rate."""
    candidate, reference = _n1_case(20, 8, shared=True)
    verdict = _stage(candidate, reference, size=8)
    paired = verdict["report"]["paired"]
    assert verdict["design"] == "shared" and paired["pairing"] == "crn" and paired["never_gates"]
    assert paired["crps_sign_flip"]["p"] == Fraction(1, 256) and paired["crps_sign_flip"]["passes"]
    assert paired["exchangeability"] == ensemble.PAIRED_EXCHANGEABILITY
    assert (verdict["label"], verdict["rate"]) == ("row_regress", "none")
    assert "not by construction" in verdict["steps"]["I"]["exchangeability"]


# --- dispersion-only gains: the spread_driven note --------------------------------------------------------------------

def _review_widening() -> tuple[list[dict], list[dict]]:
    """The reviewer's v3_widening b8_s8 case: the reference +8 +-1 on the 0119Z11-Z13 rows, the candidate the same
    centre +8 with a spread of {-8, -4, 4, 8} per row (the rng sequence of v3_widening.py)."""
    rng, rows = random.Random(3), [row for field in ("0119Z11", "0119Z12", "0119Z13") for row in BY_FIELD[field]]
    for bias, spread in ((10, 10), (12, 12), (8, 8)):
        reference = [_counts(**{r: bias + rng.choice((-1, 0, 1)) for r in rows}) for _ in range(8)]
        candidate = [_counts(**{r: bias + rng.choice((-spread, -spread // 2, spread // 2, spread)) for r in rows})
                     for _ in range(8)]
    return candidate, reference


def test_a_widening_that_passes_is_noted_spread_driven():
    """The fair CRPS rewards widening a biased, overconfident reference: the candidate's draws lie 35.6 farther from
    the real counts on average, yet T = -691/7 and the confirmation is improved. The note says so (report only)."""
    candidate, reference = _review_widening()
    verdict = _stage(*_arms(candidate, reference), mode="confirm")
    split = verdict["report"]["split"]
    assert (verdict["label"], verdict["rate"], verdict["note"]) == ("improved", "alpha", "spread_driven")
    assert verdict["steps"]["I"]["T"] == Fraction(-691, 7) and verdict["steps"]["I"]["p"] == Fraction(7, 1430)
    assert split["d_mean_tae"] == Fraction(285, 8) and split["d_half_gmd"] > 0
    assert verdict["note_detail"] == m2_stage.NOTES["spread_driven"]
    # A pure widening at K = 16 (review C4): mean TAE unchanged, T = -896/5, improved and spread_driven.
    z12 = BY_FIELD["0119Z12"]
    wide = [_shift(z12, 24), _shift(z12, 0)] * 8
    verdict16 = _stage(*_arms(wide, [_shift(z12, 12)] * 16), size=16, mode="confirm")
    assert (verdict16["label"], verdict16["note"], verdict16["steps"]["I"]["T"]) == (
        "improved", "spread_driven", Fraction(-896, 5))
    assert verdict16["report"]["split"]["d_mean_tae"] == 0
    # The same widening at K = 8 is not significant; the note still marks where T comes from.
    verdict8 = _stage(*_arms(wide[:8], [_shift(z12, 12)] * 8), mode="confirm")
    assert (verdict8["label"], verdict8["note"], verdict8["steps"]["I"]["T"]) == (
        "not_significant", "spread_driven", -192)


def test_no_effect_needs_shared_keys_and_tae_only_marks_a_sharpening():
    same = _stage(*_arms([_counts()] * 8, [_counts()] * 8, shared=True))
    assert same["label"] == "no_effect" and same["steps"]["no_effect"] == {
        "rule": "every scored row equals the reference's on every key", "applies": True, "value": True}
    apart = _stage(*_arms([_counts()] * 8, [_counts()] * 8))
    assert apart["label"] == "not_material" and not apart["steps"]["no_effect"]["applies"]
    assert apart["note"] is None
    spread = [_counts(**{X: 12 if i % 2 else -12}) for i in range(8)]
    sharp = _stage(*_arms([_counts()] * 8, spread))
    split = sharp["report"]["split"]
    assert (split["d_mean_tae"], split["d_half_gmd"]) == (-12, Fraction(-48, 7))
    assert split["d_mean_tae"] == split["d_crps"] + split["d_half_gmd"]
    assert sharp["steps"]["I"]["T"] == split["d_crps"] == Fraction(-36, 7)
    assert (sharp["label"], sharp["note"]) == ("not_material", "tae_only")


def test_a_worse_candidate_is_labelled_worse():
    """+1 on every main row of one field: ms4 T = 7 windows x 4 = 28 >= g while N1 (1), N3 (10) and N4 (10) hold."""
    worse = _stage(*_arms([_shift(BY_FIELD["0119Z5"], 1)] * 8, [_counts()] * 8))
    assert (worse["label"], worse["first_failure"], worse["rate"], worse["steps"]["I"]["T"]) == (
        "worse", None, "none", 28)
    assert worse["steps"]["N3"]["T"] == worse["steps"]["N4"]["T"] == 10 and worse["steps"]["N1"]["value"] == 1


# --- Codex counterexample 2: paired exchangeability (A1.3) ------------------------------------------------------------

def _codex_draws(rng: random.Random, size: int, *, paired: bool) -> tuple[list[dict], list[dict]]:
    """P = 20 U, C = 20 ((U - 1) mod 100), U uniform on 0..99: identical marginals. Paired: one U per key (CRN);
    unpaired: independent U for the candidate's disjoint keys."""
    u = [rng.randrange(100) for _ in range(size)]
    v = u if paired else [rng.randrange(100) for _ in range(size)]
    return [{"a": 20 * ((x - 1) % 100)} for x in v], [{"a": 20 * x} for x in u]


def test_paired_rule_accepts_the_codex_counterexample_while_the_unpaired_test_keeps_its_size():
    rng, target, keys = random.Random(20261026), {"a": 0}, ["a"]
    paired_accepts = 0
    for _ in range(1000):
        candidate, reference = _codex_draws(rng, 8, paired=True)
        result = ensemble.paired_crps_test(dict(zip(KEYS6 + ["k7", "k8"], candidate)),
                                           dict(zip(KEYS6 + ["k7", "k8"], reference)), target, keys, g=10, alpha=0.05)
        paired_accepts += result["passes"]
    assert paired_accepts / 1000 >= 0.88  # >= 0.99^8 = 0.9227 in expectation: the paired p is not valid here
    reps, rejects, accepts = 2000, 0, 0
    for _ in range(reps):
        candidate, reference = _codex_draws(rng, 8, paired=False)
        result = ensemble.unpaired_crps_test(candidate, reference, target, keys, g=10, alpha=0.05)
        rejects += result["p"] <= Fraction(1, 20)
        accepts += result["passes"]
    slack = 3 * math.sqrt(0.05 * 0.95 / reps)
    assert rejects / reps <= 0.05 + slack and accepts <= rejects  # exact size: <= floor(0.05 C(16, 8)) / C(16, 8)
    assert ensemble.unpaired_crps_test(candidate, reference, target, keys, g=10, alpha=0.05)["size_bound"] == \
        Fraction(643, 12870)


def test_the_stage_never_lets_the_paired_counterexample_through():
    """The counterexample on one row of real draws, on shared CRN keys: T = -20 and the paired p is 1/256, but the
    unpaired test gates and does not reject; the paired p is reported with its exchangeability label."""
    u = [10, 20, 30, 40, 50, 60, 70, 80]
    reference = [_counts(**{X: 20 * x}) for x in u]
    candidate = [_counts(**{X: 20 * ((x - 1) % 100)}) for x in u]
    verdict = _stage(*_arms(candidate, reference, shared=True))
    paired = verdict["report"]["paired"]["crps_sign_flip"]
    # The top-ranked pair contributes v = 0, so two sign patterns reach the minimum: p = 2/256 (Codex: p <= 2/256).
    assert paired["T"] == -20 and paired["p"] == Fraction(2, 256) and paired["passes"]
    assert verdict["steps"]["I"]["T"] == -20 and verdict["steps"]["I"]["p"] > Fraction(1, 20)
    assert verdict["label"] == "not_significant" and verdict["rate"] == "none"


# --- exactness of the unpaired p --------------------------------------------------------------------------------------

def _crps_from_matrix(members, to_y, pair, truths: int) -> Fraction:
    m = len(members)
    within = sum(pair[i][j] for i, j in itertools.combinations(members, 2))
    return Fraction(sum(to_y[i] for i in members), m * truths) - Fraction(within, m * (m - 1))


@pytest.mark.parametrize("truth", [False, True])
def test_unpaired_p_equals_the_full_enumeration_of_the_12870_relabelings(truth):
    labels = [("M", k) for k in KEYS6] + [("L", "k1"), ("L", "k2")] + [("P", k) for k in KEYS6] + [("N", "k1"),
                                                                                                    ("N", "k2")]
    pool = [_ms4(B0["arms"][arm][key]) for arm, key in labels]
    target = [_ms4(B0["arms"]["L"][k]) for k in ("k5", "k6")] if truth else _ms4(REAL)
    truths = target if truth else [target]
    keys = list(truths[0])
    candidate, reference = pool[:8], pool[8:]
    result = ensemble.unpaired_crps_test(candidate, reference, target, keys, g=10, alpha=0.05)
    to_y = [sum(ensemble.l1(m, t, keys) for t in truths) for m in pool]
    pair = [[ensemble.l1(a, b, keys) for b in pool] for a in pool]
    coefficients, denominator = ensemble.unpaired_crps_contributions(candidate, reference, target, keys)
    found = []
    for chosen in itertools.combinations(range(16), 8):
        rest = [i for i in range(16) if i not in chosen]
        value = (_crps_from_matrix(chosen, to_y, pair, len(truths))
                 - _crps_from_matrix(rest, to_y, pair, len(truths)))
        assert Fraction(2 * sum(coefficients[i] for i in chosen) - sum(coefficients), denominator) == value
        found.append(value)
    observed = ensemble.fair_crps(candidate, target, keys) - ensemble.fair_crps(reference, target, keys)
    assert len(found) == 12870 == result["splits"] and found[0] == observed == result["T"]
    assert result["p"] == Fraction(sum(value <= observed for value in found), 12870)
    assert result["min_p"] == Fraction(1, 12870)


def _dp_count(values: list[int], size: int, threshold: int) -> int:
    """#{size-subsets with sum <= threshold} by dynamic programming over (size, sum), independent of the bisect."""
    table = [Counter() for _ in range(size + 1)]
    table[0][0] = 1
    for value in values:
        for chosen in range(size, 0, -1):
            for total, count in list(table[chosen - 1].items()):
                table[chosen][total + value] += count
    return sum(count for total, count in table[size].items() if total <= threshold)


def test_unpaired_p_at_k16_is_exact():
    """C(32, 16) = 601080390 relabelings: the linear form matches the direct CRPS on random relabelings, and the
    meet-in-the-middle count matches an independent dynamic program and the hypergeometric closed form."""
    rng = random.Random(16)
    pool = [{"a": rng.randint(0, 3), "b": rng.randint(0, 2)} for _ in range(32)]
    target, keys = {"a": 1, "b": 2}, ["a", "b"]
    candidate, reference = pool[:16], pool[16:]
    coefficients, denominator = ensemble.unpaired_crps_contributions(candidate, reference, target, keys)
    for _ in range(200):
        chosen = set(rng.sample(range(32), 16))
        a, b = [pool[i] for i in sorted(chosen)], [pool[i] for i in range(32) if i not in chosen]
        assert Fraction(2 * sum(coefficients[i] for i in chosen) - sum(coefficients), denominator) == (
            ensemble.fair_crps(a, target, keys) - ensemble.fair_crps(b, target, keys))
    result = ensemble.unpaired_crps_test(candidate, reference, target, keys, g=10, alpha=0.05)
    assert result["splits"] == math.comb(32, 16) == 601080390
    assert result["hits"] == _dp_count(coefficients, 16, sum(coefficients[:16]))
    assert result["p"] == Fraction(result["hits"], 601080390)
    assert result["size_bound"] == Fraction(30054019, 601080390)
    for zeros in (10, 16, 22):
        values = [0] * zeros + [1] * (32 - zeros)
        for threshold in (0, 3, 8):
            closed = sum(math.comb(32 - zeros, m) * math.comb(zeros, 16 - m) for m in range(threshold + 1))
            assert ensemble.count_subsets_at_most(values, 16, threshold) == closed
    for _ in range(5):
        values = [rng.randint(-40, 40) for _ in range(20)]
        threshold = rng.randint(-60, 60)
        assert ensemble.count_subsets_at_most(values, 10, threshold) == _dp_count(values, 10, threshold)
    with pytest.raises(ValueError, match="integer"):
        ensemble.count_subsets_at_most([1.5, 2], 1, 0)
    with pytest.raises(ValueError, match="2 to 16"):
        ensemble.unpaired_crps_test(pool[:17], pool[15:], target, keys, g=10, alpha=0.05)


# --- the exchangeability label from the key evidence (A1.3) -----------------------------------------------------------

def test_the_exchangeability_label_follows_the_key_evidence():
    """By construction only for disjoint secret keys with valid fingerprints; public keys (review: parent against
    parent on the 14 public C1/B0 keys with a post-hoc split) or missing fingerprints give 'unverified'."""
    candidate, reference = _n1_case(11, 8)
    secret = _stage(candidate, reference)
    assert secret["steps"]["H1"]["keys"] == {"relation": "disjoint", "fingerprints_valid": True,
                                             "public_or_unknown": []}
    assert secret["steps"]["I"]["exchangeability"] == ensemble.UNPAIRED_EXCHANGEABILITY["disjoint"]
    public = _stage(*_arms([candidate["c00"]["counts"]] * 8, [reference["r00"]["counts"]] * 8, public=True))
    assert public["steps"]["H1"]["keys"]["relation"] == "unverified" and public["label"] == "improved"
    assert public["steps"]["I"]["exchangeability"].startswith("unverified") and public["rate"] == "none"
    assert len(public["steps"]["H1"]["keys"]["public_or_unknown"]) == 16
    blank = {key: {**draw, "run": {**draw["run"], "key_sha256": None}} for key, draw in candidate.items()}
    unknown = _stage(blank, reference)
    assert unknown["steps"]["H1"]["keys"]["relation"] == "unverified"
    assert unknown["steps"]["I"]["exchangeability"].startswith("unverified")
    assert unknown["report"]["sensitivity_unpaired"]["main130"]["T"] == 11 - 129


# --- confirmation commitments -----------------------------------------------------------------------------------------

def test_h1_blocks_before_any_statistic():
    base = [_counts()] * 8
    cand, ref = _arms(base, base)
    gate = m2_stage.frozen_gate(8)
    _blocked(_stage(dict(list(cand.items())[:7]), ref), "draws: the candidate arm has 7")
    partial = {**dict(list(cand.items())[:7]), "r00": ref["r00"]}
    _blocked(_stage(partial, ref), "design: the arms share only some keys")
    short = {**cand, "c00": {**cand["c00"], "counts": {k: v for k, v in cand["c00"]["counts"].items() if k != X}}}
    _blocked(_stage(short, ref), "rows: candidate:c00")
    _blocked(_stage(cand, ref, truth={"r00": _counts()}), "target: truth draws on judged keys")
    judged = {**cand, "c01": {**cand["c01"], "guard_stats": _stats(init52=1)}}
    _blocked(_stage(judged, ref), "init52 differs")
    edges = {**cand, "c02": {**cand["c02"], "guard_stats": {**_stats(), "entry_p95": {guards.NETWORK: 1.0}}}}
    _blocked(_stage(edges, ref), "entry edges differ")
    reused_run = {**cand["c03"]["run"], "key_sha256": ref["r05"]["run"]["key_sha256"]}
    reused = {**cand, "c03": {**cand["c03"], "run": reused_run}}
    _blocked(_stage(reused, ref), "reuse one draw key")
    twice = {**cand, "c04": {**cand["c04"], "run": {**cand["c04"]["run"], "id": ref["r02"]["run"]["id"]}}}
    _blocked(_stage(twice, ref), "one ledger run is used under several labels")
    shared_c, shared_r = _arms(base, base, shared=True)
    _blocked(_stage(shared_c, shared_r, mode="confirm"), "a confirmation runs the arms on disjoint fresh keys")
    _blocked(m2_stage.evaluate_stage(cand, ref, real=REAL, gate=gate, mode="confirm"), "needs the commitment")
    loose = {**gate, "n1_row_margin": 30.0}
    _blocked(_stage(cand, ref, mode="confirm", gate=loose, commitment=_commitment(cand, ref, loose)),
             "uses the frozen M2 gate")
    commitment = _commitment(cand, ref, gate)
    wrong_keys = {**commitment, "candidate_keys_sha256": commitment["candidate_keys_sha256"][:7] + [_fp("other")]}
    _blocked(_stage(cand, ref, mode="confirm", commitment=wrong_keys), "candidate committed key #7 is unrun")
    _blocked(_stage(cand, ref, mode="confirm", commitment={**commitment, "target_sha256": "0" * 64}),
             "the target differs")
    _blocked(_stage(cand, ref, mode="confirm", commitment={**commitment, "reference_binding": {"seed": 11}}),
             "bound to another reference")
    _blocked(_stage(cand, ref, mode="confirm", ledger_runs=None), "needs the ledger's runs")
    assert _stage(cand, ref, mode="confirm")["status"] == "measured"


@pytest.mark.parametrize("arm", ["candidate", "reference"])
def test_a_confirmation_without_committed_bindings_is_blocked(arm):
    """Review D6: without bindings several ops could run on the committed keys and the best one be reported."""
    candidate, reference = _n1_case(11, 8)
    commitment = _commitment(candidate, reference, m2_stage.frozen_gate(8))
    for missing in ({k: v for k, v in commitment.items() if k != f"{arm}_binding"},
                    {**commitment, f"{arm}_binding": None}, {**commitment, f"{arm}_binding": {}}):
        _blocked(_stage(candidate, reference, mode="confirm", commitment=missing), f"commits the {arm} binding")


def test_a_confirmation_on_public_keys_is_blocked():
    """Review v6: public (exposed) keys let a split be chosen after the draws are seen."""
    better, parent = _improving()
    for public in (True, None):
        candidate, reference = _arms([better] * 8, [parent] * 8, public=public)
        verdict = _stage(candidate, reference, mode="confirm")
        _blocked(verdict, "use a public key or one of unknown publicity")
        assert verdict["steps"]["H1"]["keys"]["relation"] == "unverified"


def test_malformed_commitments_and_fingerprints_are_refused_cleanly():
    candidate, reference = _n1_case(11, 8)
    gate = m2_stage.frozen_gate(8)
    commitment = _commitment(candidate, reference, gate)
    # Review D4: a key list holding None blocks with a named failure instead of raising TypeError.
    blank = {key: {**draw, "run": {**draw["run"], "key_sha256": None}} for key, draw in candidate.items()}
    _blocked(_stage(blank, reference, mode="confirm", commitment={**commitment, "candidate_keys_sha256": [None] * 8}),
             "candidate_keys_sha256 is not a list of 64-hex")
    # Review D9: one run without a fingerprint, committed as the empty string.
    one = {**candidate, "c00": {**candidate["c00"], "run": {**candidate["c00"]["run"], "key_sha256": None}}}
    keys = ["", *commitment["candidate_keys_sha256"][1:]]
    verdict = _stage(one, reference, mode="confirm", commitment={**commitment, "candidate_keys_sha256": keys})
    _blocked(verdict, "candidate runs ['c00'] lack a 64-hex lowercase key fingerprint")
    _blocked(verdict, "candidate_keys_sha256 is not a list of 64-hex")
    upper = {**candidate, "c01": {**candidate["c01"], "run": {**candidate["c01"]["run"],
                                                              "key_sha256": _fp("c01").upper()}}}
    _blocked(_stage(upper, reference, mode="confirm"), "candidate runs ['c01'] lack a 64-hex")
    _blocked(_stage(candidate, reference, mode="confirm", commitment={**commitment, "reference_keys_sha256": "x"}),
             "reference_keys_sha256 is not a list")
    _blocked(_stage(candidate, reference, mode="confirm",
                    commitment={k: v for k, v in commitment.items() if k != "candidate_spare_keys_sha256"}),
             "candidate_spare_keys_sha256 is not a list")
    _blocked(_stage(candidate, reference, mode="confirm", commitment={
        **commitment, "candidate_keys_sha256": commitment["candidate_keys_sha256"][:7]}), "lists 7 keys")
    twice = {**commitment, "reference_spare_keys_sha256": [commitment["candidate_keys_sha256"][0]]}
    _blocked(_stage(candidate, reference, mode="confirm", commitment=twice), "a key is committed twice")
    nameless = {**candidate, "c02": {**candidate["c02"], "run": {**candidate["c02"]["run"], "id": None}}}
    _blocked(_stage(nameless, reference, mode="confirm"), "lack a ledger run id")
    _blocked(_stage(candidate, reference, mode="confirm", commitment="not a mapping"), "needs the commitment")


def test_a_confirmation_needs_one_measurement_code_and_runtime_identity():
    candidate, reference = _n1_case(11, 8)
    other = {**IDENTITY, "measurement_code_sha256": "22d28e932ae07c2533431d8364004c45c6ea0866098058e14071dea2d6ed4cce"}
    mixed = {**candidate, "c05": {**candidate["c05"], "run": {**candidate["c05"]["run"], "identity": other}}}
    _blocked(_stage(mixed, reference, mode="confirm"), "measured or run by different code")
    screen = _stage(mixed, reference)  # screening reports the mix and labels as usual
    assert screen["label"] == "improved" and not screen["steps"]["H1"]["identity"]["consistent"]
    assert sorted(screen["steps"]["H1"]["identity"]["identities"]) == ["22d28e932ae0/3c7d6e80ca31",
                                                                      "de7747aadc21/3c7d6e80ca31"]
    unknown = {**candidate, "c05": {**candidate["c05"], "run": {**candidate["c05"]["run"], "identity": None}}}
    _blocked(_stage(unknown, reference, mode="confirm"), "lacks its measurement-code or runtime identity")


# --- key use against the ledger: spares for crashed or timed-out runs only (A2.2) -------------------------------------

def _spare_case(*, crashed: int = 1) -> tuple[dict, dict, dict, list[dict]]:
    """Candidate primary keys c00..c07 where the first ``crashed`` keys crashed and spares s0.. replaced them."""
    counts, parent = _improving()
    candidate, reference = _arms([counts] * 8, [parent] * 8)
    spares = [f"s{i}" for i in range(crashed + 1)]
    for i in range(crashed):  # the draw on a crashed key is replaced by the spare's draw
        del candidate[f"c{i:02d}"]
        candidate[spares[i]] = _draw(counts, spares[i], "candidate")
    gate = m2_stage.frozen_gate(8)
    commitment = {**_commitment(candidate, reference, gate),
                  "candidate_keys_sha256": [_fp(f"c{i:02d}") for i in range(8)],
                  "candidate_spare_keys_sha256": [_fp(key) for key in spares]}
    ledger = _ledger(candidate, reference) + [
        {"id": f"candidate-c{i:02d}", "request_sha256": _fp(f"request:c{i:02d}"), "key_sha256": _fp(f"c{i:02d}"),
         "public": False, "outcome": "crashed",
         "binding": {name: BINDINGS["candidate"][name] for name in m2_stage.LEDGER_BINDING_FIELDS}}
        for i in range(crashed)]
    return candidate, reference, commitment, ledger


def test_a_spare_replaces_a_crashed_run_in_order():
    candidate, reference, commitment, ledger = _spare_case(crashed=2)
    verdict = _stage(candidate, reference, mode="confirm", commitment=commitment, ledger_runs=ledger)
    use = verdict["steps"]["H1"]["key_use"]["candidate"]
    assert verdict["label"] == "improved" and use["passes"] and use["spares_used"] == 2
    assert [row["status"] for row in use["per_key"]] == ["crashed", "crashed", *["scored"] * 8, "unrun"]
    assert verdict["steps"]["H1"]["key_use"]["reference"]["spares_used"] == 0


def test_a_spare_needs_a_crash_or_timeout_before_it():
    candidate, reference, commitment, ledger = _spare_case(crashed=1)
    # The primary key c00 has no run at all: the spare was run instead of it.
    unrun = [row for row in ledger if row["outcome"] != "crashed"]
    _blocked(_stage(candidate, reference, mode="confirm", commitment=commitment, ledger_runs=unrun),
             "candidate committed key #0 is unrun")
    # A gridlock (or another unscored run) is a failure class, never a crash: no spare replaces it.
    unscored = [{**row, "outcome": "unscored"} if row["outcome"] == "crashed" else row for row in ledger]
    _blocked(_stage(candidate, reference, mode="confirm", commitment=commitment, ledger_runs=unscored),
             "candidate committed key #0 is unscored")
    # The skipped spare s0 has a scored run of its own, and s1 is used instead of it.
    candidate2 = {**{k: v for k, v in candidate.items() if k != "s0"},
                  "s1": _draw(candidate["s0"]["counts"], "s1", "candidate")}
    ledger2 = ledger + [_ledger_row(candidate2["s1"]["run"])]
    _blocked(_stage(candidate2, reference, mode="confirm", commitment=commitment, ledger_runs=ledger2),
             "are left out")


def test_an_unhealthy_scored_draw_cannot_be_swapped_for_a_spare():
    """Review B1 and A2.2: 2 unhealthy candidate draws against 0 fail H2; swapping one for a healthy run on a spare
    key passes H2, but the ledger shows the dropped scored run on its committed key."""
    counts, parent = _improving()
    candidate, reference = _arms([counts] * 8, [parent] * 8, health={("c", 0): K14_HEALTH, ("c", 1): K14_HEALTH})
    assert _stage(candidate, reference, mode="confirm")["label"] == "unhealthy"
    dropped = candidate.pop("c01")
    candidate["s0"] = _draw(counts, "s0", "candidate")
    gate = m2_stage.frozen_gate(8)
    commitment = {**_commitment(candidate, reference, gate),
                  "candidate_keys_sha256": [_fp(f"c{i:02d}") for i in range(8)],
                  "candidate_spare_keys_sha256": [_fp("s0")]}
    ledger = _ledger(candidate, reference) + [_ledger_row(dropped["run"])]
    verdict = _stage(candidate, reference, mode="confirm", commitment=commitment, ledger_runs=ledger)
    _blocked(verdict, "the candidate arm leaves out the scored draws on ['committed #1']")
    _blocked(verdict, "the candidate arm uses keys the ledger does not admit: ['spare #0']")
    # A commitment rewritten to list the spare as a committed key hides the dropped key from the audit; the dropped
    # secret run of the same binding is then reported (the evaluator cannot verify the commitment's timing).
    rewritten = {**commitment, "candidate_keys_sha256": [d["run"]["key_sha256"] for d in candidate.values()],
                 "candidate_spare_keys_sha256": []}
    hidden = _stage(candidate, reference, mode="confirm", commitment=rewritten, ledger_runs=ledger)
    assert hidden["label"] == "improved"
    assert hidden["steps"]["H1"]["key_use"]["candidate"]["uncommitted_secret_runs"] == ["candidate-c01"]


def test_key_use_refuses_other_bindings_and_two_requests_on_one_key():
    candidate, reference = _n1_case(11, 8)
    other = _ledger_row(candidate["c03"]["run"], id="other-op-c03", binding={"scenario_op": {"op": "x"}, "seed": None,
                                                                             "family": None})
    _blocked(_stage(candidate, reference, mode="confirm", ledger_runs=_ledger(candidate, reference) + [other]),
             "was also run under another binding")
    rerun = _ledger_row(candidate["c04"]["run"], id="candidate-c04-rerun", request_sha256=_fp("rerun"))
    _blocked(_stage(candidate, reference, mode="confirm", ledger_runs=_ledger(candidate, reference) + [rerun]),
             "has scored runs of two requests")
    swapped = {**candidate, "c05": {**candidate["c05"], "run": {**candidate["c05"]["run"], "id": "candidate-x"}}}
    _blocked(_stage(swapped, reference, mode="confirm", ledger_runs=_ledger(candidate, reference)),
             "is not a scored ledger run on its committed key")


def test_ledger_outcome_tells_a_crash_from_an_unscored_failure():
    scored = {"status": "invalid", "tae_main": 372}
    assert m2_stage.ledger_outcome(scored) == "scored" and m2_stage.ledger_outcome({**scored, "status": "measured"})
    assert m2_stage.ledger_outcome({"status": "failed", "tae_main": None}) == "crashed"
    unscored = {"status": "invalid", "tae_main": None}
    assert m2_stage.ledger_outcome(unscored, {"native_status": "timeout", "stall_status": None}) == "crashed"
    assert m2_stage.ledger_outcome(unscored, {"native_status": "failed", "stall_status": "all_stopped"}) == "unscored"
    assert m2_stage.ledger_outcome(unscored, {"native_status": "pass", "stall_status": "completed"}) == "unscored"
    assert m2_stage.ledger_outcome(unscored, None) == "unscored"


def test_the_reader_classifies_ledger_rows_and_refuses_one_run_under_two_labels(tmp_path):
    """Store-free: a synthetic store with a crashed, a timed-out, a gridlocked and a scored unhealthy run."""
    def row(name: str, status: str, tae: int | None, public: bool = False) -> dict:
        return {"id": name, "request_sha256": _fp("request:" + name), "run_dir": f"E:\\x\\runs\\{name}",
                "status": status, "tae_main": tae, "scenario_op": None, "seed": 104,
                "draw_key": {"family": "depart_jitter", "label": name, "key_sha256": _fp(name), "public": public,
                             "derivation": "caller-supplied bytes"}}
    rows = [row("crash", "failed", None), row("timeout", "invalid", None), row("gridlock", "invalid", None),
            row("k14", "invalid", 372), row("nominal", "measured", 275)]
    rows[-1]["draw_key"] = None
    for name, health in (("timeout", {"native_status": "timeout", "stall_status": None}),
                         ("gridlock", {"native_status": "pass", "stall_status": "all_stopped"})):
        (tmp_path / "runs" / name).mkdir(parents=True)
        (tmp_path / "runs" / name / "judge.json").write_text(json.dumps(
            {"label": name, "request_sha256": _fp("request:" + name), "health": health}), encoding="utf-8")
    found = panel.ledger_key_runs(tmp_path, rows)
    assert [(r["id"], r["outcome"]) for r in found] == [("crash", "crashed"), ("timeout", "crashed"),
                                                        ("gridlock", "unscored"), ("k14", "scored")]
    assert found[0]["binding"] == {"scenario_op": None, "seed": 104, "family": "depart_jitter"}
    assert found[0]["public"] is False
    ledger = tmp_path / "runs.jsonl"
    ledger.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    assert panel.read_ledger_rows(ledger) == rows
    with pytest.raises(ValueError, match="One ledger run under several labels"):
        panel.read_stage_draws(ledger, {"candidate:k1": "k14", "truth:t1": "k14"})


# --- the truth ensemble ---------------------------------------------------------------------------------------------

def test_a_twin_truth_target_scores_every_step_against_the_truth_ensemble():
    truth, truth_runs = _truth([_counts(**{X: -17})] * 8)  # an M-like truth: X 17 below the real counts
    candidate, reference = _arms([_counts(**{X: -17})] * 8, [_counts()] * 8)
    verdict = _stage(candidate, reference, mode="confirm", truth=truth, truth_runs=truth_runs)
    assert verdict["target"] == "truth" and verdict["label"] == "improved" and verdict["rate"] == "alpha"
    assert verdict["steps"]["N1"]["value"] == 0 and verdict["steps"]["N1"]["passes"]  # X itself grows by -17
    assert (verdict["steps"]["N1"]["target"], verdict["steps"]["N3"]["target"]) == ("truth", "truth")
    assert verdict["steps"]["N4"]["margin_basis"] == "calibrated against the real counts (C1)"
    assert verdict["steps"]["N2"]["value"] == 0
    assert verdict["steps"]["I"]["T"] == -17 and verdict["report"]["target_rank"] is None
    assert verdict["steps"]["H2"]["truth_health"]["t0"] == {"classes": [], "unhealthy": False, "teleports": 0,
                                                            "collisions": 0, "status": "measured"}
    real_target = _stage(candidate, reference)  # against the real counts the same op worsens X by 17
    assert real_target["label"] == "row_regress" and real_target["steps"]["N1"]["value"] == 17
    assert real_target["steps"]["N1"]["target"] == "real"
    # guard_target 'real' (an owner option): N1, N3 and N4 against the real counts, the I step against the truth.
    real_guards = {**m2_stage.frozen_gate(8), "guard_target": "real"}
    switched = _stage(candidate, reference, truth=truth, truth_runs=truth_runs, gate=real_guards)
    assert switched["label"] == "row_regress" and switched["steps"]["N1"]["target"] == "real"
    assert switched["steps"]["I"]["T"] == -17


def test_truth_draws_must_be_other_runs_with_their_records():
    """Review D5: a truth ensemble made of the candidate's own runs, relabelled, must not pass."""
    candidate, reference = _arms([_counts()] * 8, [_counts()] * 8)
    truth = {f"t{i}": draw["counts"] for i, draw in enumerate(candidate.values())}
    copied = {f"t{i}": {"run": draw["run"], "health": draw["health"]} for i, draw in enumerate(candidate.values())}
    verdict = _stage(candidate, reference, mode="confirm", truth=truth, truth_runs=copied)
    _blocked(verdict, "a truth draw runs on a judged draw key")
    _blocked(verdict, "one ledger run is used under several labels")
    _blocked(_stage(candidate, reference, mode="confirm", truth=truth), "needs the truth draws' run records")
    _blocked(_stage(candidate, reference, truth=truth, truth_runs=copied), "a truth draw runs on a judged draw key")
    own, own_runs = _truth([_counts()] * 8)
    _blocked(_stage(candidate, reference, truth=own, truth_runs=dict(list(own_runs.items())[:7])),
             "do not cover exactly the truth draws")
    bad = {**own_runs, "t0": {**own_runs["t0"], "run": {**own_runs["t0"]["run"], "key_sha256": "x"}}}
    _blocked(_stage(candidate, reference, mode="confirm", truth=own, truth_runs=bad),
             "truth runs ['t0'] lack a 64-hex key fingerprint")
    assert _stage(candidate, reference, mode="confirm", truth=own, truth_runs=own_runs)["status"] == "measured"


# --- health (A2.1) and unhealthy draws scored (A2.2) ------------------------------------------------------------------

def test_failure_classes_of_the_c1_runs_and_of_every_new_class():
    assert m2_stage.failure_classes(K13_HEALTH) == []
    assert m2_stage.failure_classes(K14_HEALTH) == ["collision", "teleport"]
    completion = K13_HEALTH["completion"]
    cases = {"gridlock": {"stall_status": "all_stopped"}, "stall": {"stall_status": "failed"},
             "native_failure": {"native_status": "timeout"},
             "discarded": {"completion": {**completion, "discarded": 2}},
             "not_inserted": {"completion": {**completion, "waiting": 3, "inserted": 8586}},
             "not_arrived": {"completion": {**completion, "running": 2, "arrived": 8587}},
             "unclassified": {"healthy": False}}
    for name, change in cases.items():
        assert m2_stage.failure_classes({**K13_HEALTH, **change}) == [name]
    assert m2_stage.failure_classes({"healthy": True}) == ["native_failure", "stall", "unclassified"]


@pytest.mark.parametrize(("size", "candidate_bad", "reference_bad", "passes"), [
    (8, 1, 0, True), (8, 2, 0, False), (8, 2, 1, True), (8, 0, 1, True), (8, 3, 1, False),
    (16, 2, 0, True), (16, 3, 0, False), (16, 3, 1, True)])
def test_health_compares_the_arms_with_the_declared_margin(size, candidate_bad, reference_bad, passes):
    health = {**{("c", i): K14_HEALTH for i in range(candidate_bad)},
              **{("r", i): K14_HEALTH for i in range(reference_bad)}}
    verdict = _stage(*_arms([_counts()] * size, [_counts()] * size, health=health), size=size)
    h2 = verdict["steps"]["H2"]
    assert h2["unhealthy_draws"] == {"candidate": candidate_bad, "reference": reference_bad}
    assert h2["excess"] == candidate_bad - reference_bad and h2["margin"] == {8: 1, 16: 2}[size]
    assert h2["passes"] is passes and h2["new_failure_classes"] == []
    assert verdict["label"] == ("not_material" if passes else "unhealthy")
    assert verdict["report"]["parent_unhealthy"] == {"k": 1, "n": 14}


def test_an_implausibly_unhealthy_reference_is_flagged_but_never_blocks():
    """Review B3: 8/8 unhealthy candidate draws against 7/8 pass the relative rule; the reference's 7/8 is
    implausible under the parent's 1/14 (one-sided 95% upper rate 0.297), which the report flags."""
    health = {**{("c", i): K14_HEALTH for i in range(8)}, **{("r", i): K14_HEALTH for i in range(7)}}
    better, parent = _improving()
    verdict = _stage(*_arms([better] * 8, [parent] * 8, health=health), mode="confirm")
    plausibility = verdict["steps"]["H2"]["plausibility"]
    assert verdict["label"] == "improved" and verdict["steps"]["H2"]["passes"]
    assert plausibility["reference_implausible"] and not plausibility["blocking"]
    assert plausibility["reference"]["parent_rate_upper95"] == pytest.approx(0.2967, abs=1e-4)
    assert plausibility["reference"]["tail_at_parent_upper95"] < 0.05 and plausibility["candidate"]["implausible"]
    usual = _stage(*_arms([_counts()] * 8, [_counts()] * 8, health={("r", 0): K14_HEALTH}))
    assert not usual["steps"]["H2"]["plausibility"]["reference_implausible"]
    assert usual["steps"]["H2"]["plausibility"]["reference"]["tail_at_parent_rate"] == pytest.approx(
        1 - (13 / 14) ** 8, abs=1e-6)


@pytest.mark.parametrize("change", [{"stall_status": "all_stopped"}, {"native_status": "timeout"},
                                    {"completion": {**K13_HEALTH["completion"], "discarded": 1}},
                                    {"completion": {**K13_HEALTH["completion"], "running": 1, "arrived": 8588}}])
def test_a_failure_class_the_parent_never_showed_rejects_in_either_arm(change):
    for arm in ("c", "r"):
        verdict = _stage(*_arms([_counts()] * 8, [_counts()] * 8, health={(arm, 3): {**K13_HEALTH, **change}}))
        assert verdict["label"] == "unhealthy" and verdict["steps"]["H2"]["excess"] in (-1, 0, 1)
        (found,) = verdict["steps"]["H2"]["new_failure_classes"]
        assert found.startswith(("candidate:c03:", "reference:r03:"))


def test_unhealthy_draws_stay_in_the_ensemble_and_are_scored():
    """A parent arm with one k14-like draw: H2 holds and every statistic includes that draw as it is."""
    corridor = [row for row in MAIN if row.startswith("R:0119Z12@")]
    reference = [_shift(corridor, d) for d in (0, 1, -1, 2, -2, 1, 0, 0)]
    reference[7] = {**_shift(corridor, 6), "R:0535Z6@8100": REAL["R:0535Z6@8100"] - 1}  # k14 skipped its 0535Z6 loop
    candidate = [_shift(corridor, d) for d in (1, 0, -1, 1, 0, 2, -1, 0)]
    cand, ref = _arms(candidate, reference, health={("r", 7): K14_HEALTH})
    verdict = _stage(cand, ref)
    h2 = verdict["steps"]["H2"]
    assert h2["passes"] and h2["unhealthy_draws"] == {"candidate": 0, "reference": 1}
    assert h2["per_draw"]["reference"]["r07"] == {"classes": ["collision", "teleport"], "unhealthy": True,
                                                  "teleports": 1, "collisions": 1, "status": "invalid"}
    target = _ms4(REAL)
    keys = list(target)
    with_k14 = (ensemble.fair_crps([_ms4(c) for c in candidate], target, keys)
                - ensemble.fair_crps([_ms4(r) for r in reference], target, keys))
    without = (ensemble.fair_crps([_ms4(c) for c in candidate], target, keys)
               - ensemble.fair_crps([_ms4(r) for r in reference[:7]], target, keys))
    assert verdict["steps"]["I"]["T"] == with_k14 != without
    assert verdict["steps"]["I"]["draws"] == [8, 8] and verdict["steps"]["N1"]["rows"] == 182


# --- H3 guards and the backlog flag -----------------------------------------------------------------------------------

def test_h3_blocks_on_the_declared_backstops_only_and_the_backlog_never_blocks():
    def run(candidate_stats: dict, *, shared: bool = False) -> dict:
        stats = {("c", i): _stats(**candidate_stats) for i in range(8)}
        return _stage(*_arms([_counts()] * 8, [_counts()] * 8, stats=stats, shared=shared))

    within = run({"entry": {"44532531": 50.0 + 119.0}})  # +119.0 <= 119.774
    assert within["steps"]["H3"]["passes"] and within["label"] == "not_material"
    backstop = run({"entry": {"44532531": 50.0 + 120.0}})
    assert backstop["label"] == "guard" and backstop["steps"]["H3"]["fired"] == ["entry:44532531"]
    network = run({"entry": {guards.NETWORK: 300.0 + 113.0}})
    assert network["steps"]["H3"]["fired"] == ["entry:network"]
    # The other busy edges' margins are reported only: v3 section 5 declares no blocking backstop for them.
    edge = run({"entry": {"-142944318": 50.0 + 15.0}})  # +15.0 > 14.392
    assert edge["label"] == "not_material" and edge["steps"]["H3"]["passes"]
    report = edge["report"]["h3_report_only"]
    assert report["fired"] == ["entry:-142944318"] and report["never_gates"]
    assert report["guards"]["entry:-142944318"]["margin"] == 14.392 and len(report["guards"]) == 5
    quiet = run({"entry": {"26623877": 11.0}})
    assert quiet["label"] == "guard" and quiet["steps"]["H3"]["fired"] == ["entry_quiet:26623877"]
    brakings = run({"brakings": 8 + 9})
    assert brakings["steps"]["H3"]["fired"] == ["brakings"] and brakings["first_failure"] == "H3"
    # L-type backlog: +25 s on the busy entry 44532531 on every CRN key, far below its re-key margin 119.774.
    backlog = {("c", i): _stats(entry={"44532531": 75.0 + (i % 2)}) for i in range(8)}
    verdict = _stage(*_arms([_counts()] * 8, [_counts()] * 8, stats=backlog, shared=True))
    flags = verdict["report"]["backlog"]
    assert flags["rule"] == "CRN-paired" and flags["flagged"] == ["44532531"] and not flags["blocking"]
    assert verdict["steps"]["H3"]["passes"] and verdict["label"] != "guard"
    disjoint = _stage(*_arms([_counts()] * 8, [_counts()] * 8, stats=backlog))
    assert disjoint["report"]["backlog"]["rule"] == "two-sample"
    assert disjoint["report"]["backlog"]["flagged"] == ["44532531"] and disjoint["label"] == "not_material"


def test_h2_labels_before_h3_when_both_fail():
    stats = {("c", i): _stats(brakings=8 + 9) for i in range(8)}
    health = {("c", 0): K14_HEALTH, ("c", 1): K14_HEALTH}
    verdict = _stage(*_arms([_counts()] * 8, [_counts()] * 8, health=health, stats=stats))
    assert not verdict["steps"]["H2"]["passes"] and not verdict["steps"]["H3"]["passes"]
    assert (verdict["label"], verdict["first_failure"]) == ("unhealthy", "H2")


# --- N2 to N4, the gate order and the report --------------------------------------------------------------------------

def test_n2_n3_n4_and_the_order_of_the_gate_steps():
    reference = [_counts()] * 8
    init = _stage(*_arms([_shift(INIT, 3)] * 8, reference))
    assert (init["label"], init["steps"]["N2"]["value"], init["steps"]["N2"]["target"]) == ("init", 156, "real")
    level = _stage(*_arms([_shift(MAIN, 3)] * 8, reference))  # +30 per field: N3 and N4 fire, N3 labels
    assert level["label"] == "level_regress" and level["steps"]["N3"]["T"] == 390
    assert not level["steps"]["N4"]["passes"] and level["steps"]["N1"]["passes"]
    alternating = {row: REAL[row] + (1 if int(ensemble.parse_row(row)[1] // 900) % 2 else -1) if row in MAIN
                   else REAL[row] for row in ROWS}  # field totals and ms4 windows unchanged, every main row off by 1
    fine = _stage(*_arms([alternating] * 8, reference))
    assert (fine["label"], fine["steps"]["N4"]["T"], fine["steps"]["N3"]["T"]) == ("fine_regress", 130, 0)
    assert fine["steps"]["I"]["T"] == 0
    both = _stage(*_arms([_counts(**{X: 15, **{row: 3 for row in INIT}})] * 8, reference))
    assert both["label"] == "row_regress" and not both["steps"]["N2"]["passes"]


def test_rho_and_the_k_rule_are_not_applicable_to_disjoint_keys():
    """Decision 4's rho rule assumes CRN pairing; A1.3's disjoint design has none (the K rule is an owner call)."""
    apart = _stage(*_arms([_counts(**{X: 1})] * 8, [_counts()] * 8))["report"]["paired"]
    assert (apart["rho"], apart["k_rule"], apart["kappa"]) == ("not applicable",) * 3
    assert apart["rho_note"].startswith("not applicable") and apart["pairing"] == "index"
    shared = _stage(*_arms([_counts(**{X: i % 3}) for i in range(8)], [_counts()] * 8, shared=True))
    paired = shared["report"]["paired"]
    assert isinstance(paired["rho"], float) and paired["k_rule"] == 8 and paired["rho_note"] is None


def test_s_rule_is_reported_without_a_simplified_verdict():
    near = [_counts(**{X: 4})] + [_counts()] * 7
    verdict = _stage(*_arms(near, [_counts()] * 8, shared=True))
    s_rule = verdict["report"]["paired"]["v2_s_rule"]
    assert s_rule["passes"] and s_rule["verdict"] is None and "report only" in s_rule["note"]
    assert verdict["label"] == "not_material" and "simplified" not in m2_stage.LABELS


# --- store reader and CLI ---------------------------------------------------------------------------------------------

def test_stage_cli_reads_scored_unhealthy_runs_from_the_store(tmp_path):
    ledger = STORE / "runs.jsonl"
    if not ledger.is_file():
        pytest.skip(f"the cached M2 pilot store is not on this machine ({ledger}); set ASTRA_TWIN_M2_STORE")
    gate = {**m2_stage.frozen_gate(8), "draws": 7, "name": "store-test-K7-screen"}
    reference = {f"k{k}": f"P3-jitter-k{k}-s104" for k in range(1, 5)} | {"k5": "s3-parent-k5", "k6": "s3-parent-k6",
                                                                         "k7": "c1-parent-k7"}
    candidate = {f"k{k}": f"c1-parent-k{k}" for k in range(8, 15)}
    spec = tmp_path / "spec.json"
    spec.write_text(json.dumps({"ledger": str(ledger), "candidate": candidate, "reference": reference,
                                "mode": "screen", "gate": gate}), encoding="utf-8")
    out = tmp_path / "verdict.json"
    done = subprocess.run([sys.executable, "-B", str(ROOT / "plugins/astra-sumo/scripts/astra_twin.py"), "stage",
                           "--spec", str(spec), "--out", str(out), "--json"], capture_output=True, text=True,
                          timeout=600)
    assert done.returncode == 0, done.stdout + done.stderr
    payload = json.loads(out.read_text(encoding="utf-8"))
    verdict = payload["verdict"]
    assert payload["status"] == "measured" and verdict["design"] == "disjoint"
    h2 = verdict["steps"]["H2"]
    assert h2["per_draw"]["candidate"]["k14"]["classes"] == ["collision", "teleport"] and h2["excess"] == 1
    assert h2["passes"] and payload["provenance"]["runs"]["candidate:k14"]["id"] == "c1-parent-k14"
    # Public C1/B0 keys: the unpaired test's exchangeability is unverified, and the margin panel's identity mix shows.
    assert verdict["steps"]["H1"]["keys"]["relation"] == "unverified"
    assert verdict["steps"]["I"]["exchangeability"].startswith("unverified")
    assert payload["provenance"]["runs"]["candidate:k14"]["key_public"] is True
    assert sorted(verdict["steps"]["H1"]["identity"]["identities"]) == ["22d28e932ae0/3c7d6e80ca31",
                                                                       "de7747aadc21/3c7d6e80ca31"]
    found = panel.read_stage_draws(ledger, {f"c:{k}": v for k, v in candidate.items()}
                                   | {f"r:{k}": v for k, v in reference.items()})
    assert found["real"] == REAL and found["draws"]["c:k14"]["guard_stats"]["brakings"] == 6
    assert found["draws"]["c:k14"]["guard_stats"]["init52"] == 70
    run = found["draws"]["c:k14"]["run"]
    assert run["key_sha256"] == "29a3dbf82c5aeac5038c6d842733e95e0a22babbcd1a9dc993bc148fcc7728c9"
    assert (run["key_public"], run["key_derivation"]) == (True, "sha256(domain || label)")
    assert run["identity"]["measurement_code_sha256"] in m2_stage.MARGIN_PANEL_IDENTITY["measurement_code_sha256"]
    assert run["identity"]["runtime_sha256"] in m2_stage.MARGIN_PANEL_IDENTITY["runtime_sha256"]
    outcomes = {row["id"]: row["outcome"] for row in found["ledger_runs"]}
    assert outcomes["c1-parent-k14"] == "scored" and "P1-nominal-s104" not in outcomes
    target = _ms4(REAL)
    keys = list(target)
    expected = (ensemble.fair_crps([_ms4(found["draws"][f"c:{k}"]["counts"]) for k in candidate], target, keys)
                - ensemble.fair_crps([_ms4(found["draws"][f"r:{k}"]["counts"]) for k in reference], target, keys))
    assert verdict["steps"]["I"]["T"] == str(expected)  # k14 (unhealthy) is in the candidate ensemble
    assert done.stdout and json.loads(done.stdout)["label"] == payload["label"]
    # The same store draws cannot confirm: public keys, mixed measurement code, no commitment.
    arms = {name: {k: found["draws"][f"{name[0]}:{k}"] for k in keys_} for name, keys_ in
            (("candidate", candidate), ("reference", reference))}
    blocked = m2_stage.evaluate_stage(arms["candidate"], arms["reference"], real=REAL, gate=gate, mode="confirm",
                                      commitment={}, ledger_runs=found["ledger_runs"])
    assert blocked["status"] == "blocked"
    assert any("public key" in failure for failure in blocked["steps"]["H1"]["failures"])
    with pytest.raises(ValueError, match="not a healthy measured run"):
        panel.read_panel(ledger, {"P:k1": "c1-parent-k14"}, repository_root=ROOT)
