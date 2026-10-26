"""M2 stage C2 code (twin/corridor_screen.py, twin/m2_qualify.py) with zero SUMO runs.

Pins the queue-aware corridor screen (insertion delay at the shifted time, FIFO displacement in saturated insertion
bins), its mechanical selection rule, positive qualification per protocol v3 section 6 (Q1 paired ED, Q2 leave one
key out, Q3 C8, Q4 the fresh truth ensemble), the power screens, the C1 K rule and the frozen C3 plan. Real numbers
come from the committed B0 fixture: M qualifies (ED 394/15, swap p 1/32, C8 5/6), L and N do not, and the C1 K rule
gives L K = 8 and M K = 16.
"""

from __future__ import annotations

import hashlib
import json
import random
from fractions import Fraction

import pytest

from astra_sumo.twin import corridor_screen, ensemble, m2_qualify, m2_stage

from test_twin_judge import ROOT

FIXTURE = ROOT / "tests" / "fixtures" / "twin_m2" / "b0_panel.json"
B0 = json.loads(FIXTURE.read_text(encoding="utf-8"))
REAL = dict(B0["target"])
ROWS = sorted(REAL)
MAIN = ensemble.main_rows(ROWS)
ARMS = B0["arms"]
M_PLANNED = {"R:0535Z4@3600": -17, "R:0535Z4@4500": 17}  # M = cell_depart_shift:107:900, k3.shift_effect (stage 3)
Z12, Z13 = "hh_0119_Z_12_31546", "hh_0119_Z_13_34018"


# --- the queue-aware screen ------------------------------------------------------------------------------------------

def _tripinfo(rows: list[tuple[str, str, float, float]]) -> bytes:
    body = "".join(f'<tripinfo id="{v}" departLane="{edge}_0" depart="{depart}" departDelay="{delay}"/>'
                   for v, edge, depart, delay in rows)
    return f"<tripinfos>{body}</tripinfos>".encode()


def test_tripinfo_insertions_reads_the_entry_insertion_and_delay_and_refuses_gaps():
    found = corridor_screen.tripinfo_insertions(_tripinfo([("a", "44532531", 3820.5, 120.25)]))
    assert found == {"a": ("44532531", 3820.5, 120.25)}
    with pytest.raises(ValueError, match="needs an id"):
        corridor_screen.tripinfo_insertions(b'<tripinfos><tripinfo id="a" departLane="e_0" depart="1"/></tripinfos>')
    with pytest.raises(ValueError, match="twice"):
        corridor_screen.tripinfo_insertions(_tripinfo([("a", "e", 1.0, 0.0), ("a", "e", 2.0, 0.0)]))


def _queue(other_detector: str, *, delay: float) -> corridor_screen.QueueDraw:
    """Ten other vehicles inserted in each of bins 4 and 8 of one entry, and two members inserted in bin 8, every
    vehicle passing its detector 80 s after insertion."""
    trips, passages = [], []
    for name, inserted, detector in ([(f"o4_{i}", 3720.0 + i, other_detector) for i in range(10)]
                                     + [(f"o8_{i}", 7320.0 + i, other_detector) for i in range(10)]
                                     + [(f"m{i}", 7420.0 + i, Z12) for i in range(2)]):
        trips.append((name, "E", inserted, delay))
        time = inserted + 80.0
        passages.append([name, detector, corridor_screen.WINDOWS.bin_begin(time), time - 0.1, time, 0.5])
    counts = {row: 0 for row in MAIN}
    return corridor_screen.queue_draw("k", counts, _tripinfo(trips), passages)


def test_queue_delta_absorbs_a_move_inside_a_saturated_queue_of_the_same_field():
    index = corridor_screen._Index(_queue(Z12, delay=120.0))
    found = corridor_screen.queue_delta(index, ["m0", "m1"], -3600.0)
    # The members move from Z12@7200 to Z12@3600 and take two slots from the others there; the others refill
    # their old slots: every row change cancels (B0's L).
    assert all(abs(value) < 1e-9 for value in found["delta"].values())
    assert (found["saturated_old"], found["saturated_new"], found["crossings"], found["entry"]) == (2, 2, 0, "E")


def test_queue_delta_keeps_a_move_whose_field_the_queue_does_not_share():
    index = corridor_screen._Index(_queue(Z13, delay=120.0))
    found = corridor_screen.queue_delta(index, ["m0", "m1"], -3600.0)
    assert found["delta"] == pytest.approx({"R:0119Z12@3600": 2.0, "R:0119Z12@7200": -2.0,
                                            "R:0119Z13@3600": -2.0, "R:0119Z13@7200": 2.0})


def test_queue_delta_moves_members_freely_when_the_entry_is_not_saturated():
    index = corridor_screen._Index(_queue(Z12, delay=0.0))
    found = corridor_screen.queue_delta(index, ["m0", "m1"], -1800.0)
    assert found["delta"] == pytest.approx({"R:0119Z12@5400": 2.0, "R:0119Z12@7200": -2.0})
    assert (found["saturated_old"], found["saturated_new"]) == (0, 0)


def test_float_paired_energy_matches_the_exact_paired_energy_distance():
    rng = random.Random(7)
    keys = [f"v{i}" for i in range(5)]
    a = {f"k{j}": {key: rng.randint(0, 40) for key in keys} for j in range(6)}
    b = {f"k{j}": {key: rng.randint(0, 40) for key in keys} for j in range(6)}
    exact = ensemble.paired_energy_test(a, b, keys)["ed"]
    assert corridor_screen.float_paired_energy([a[k] for k in sorted(a)], [b[k] for k in sorted(b)],
                                               keys) == pytest.approx(float(exact))


def _row(op: str, gain: float, ed: float, *, hits: int = 14, crossings: int = 0, stay: bool = True) -> dict:
    return {"op": op, "planned": {"l1": 10, "crossings": {}, "members_stay_main": stay},
            "predicted": {"real_gain_ms4": gain, "ed_ms4": ed, "c8_hits": hits,
                          "crossings_per_draw": [crossings] + [0] * 13}}


def test_select_corridor_op_applies_the_declared_filters_and_order():
    rows = [_row("a", 30.0, 50.0), _row("b", 40.0, 21.0), _row("c", 99.0, 19.0), _row("d", 99.0, 80.0, hits=13),
            _row("e", 99.0, 80.0, crossings=1), _row("f", 99.0, 80.0, stay=False), _row("g", 40.0, 25.0)]
    chosen = corridor_screen.select_corridor_op(rows, g=10.0, draws=14)
    assert chosen["selected"]["op"] == "g" and chosen["ed_floor_g"] == 2.0 and chosen["eligible"] == 3
    assert chosen["runner_up"] == ["b", "a"]
    fallback = corridor_screen.select_corridor_op([_row("c", 99.0, 19.0), _row("h", 5.0, 9.0)], g=10.0, draws=14)
    assert (fallback["selected"]["op"], fallback["ed_floor_g"]) == ("c", 1.0)
    assert corridor_screen.select_corridor_op([_row("h", 5.0, 9.0)], g=10.0, draws=14)["selected"] is None


def test_timing_lag_is_positive_when_the_twin_flow_comes_later():
    real = {row: 0 for row in ROWS}
    twin = dict(real)
    real["R:0119Z11@3600"], twin["R:0119Z11@4500"] = 10, 10
    lag = corridor_screen.timing_lag([twin, twin], real, ["0119Z11"])
    assert lag["0119Z11"]["lag_s"] == 900.0 and lag["pooled"]["lag_s"] == 900.0


def test_ms4_excess_by_field_splits_pr_minus_pp():
    draws = [ARMS["P"][f"k{i}"] for i in range(1, 7)]
    found = corridor_screen.ms4_excess_by_field(draws, REAL)
    assert sum(row["excess"] for row in found.values()) == pytest.approx(127.167, abs=0.01)  # B0 ms4 excess


# --- qualification ----------------------------------------------------------------------------------------------------

def test_m_qualifies_on_the_cached_panel_and_l_and_n_do_not():
    m = m2_qualify.qualify_panel(ARMS["M"], ARMS["P"], M_PLANNED)
    assert (m["ed"], m["swap_p"], m["min_p"]) == (Fraction(394, 15), Fraction(1, 32), Fraction(1, 32))
    assert round(m["loo_min"], 1) == 22.0 and round(m["loo_max"], 1) == 32.3
    assert m["c8"]["signed_change"] == {"k1": 25, "k2": 27, "k3": 12, "k4": 18, "k5": 23, "k6": 28}
    assert (m["c8"]["hits"], m["c8"]["needed"], m["passes"], m["class"]) == (5, 5, True, "moderate")
    for arm in ("L", "N"):
        other = m2_qualify.qualify_panel(ARMS[arm], ARMS["P"], M_PLANNED)
        assert not other["checks"]["Q1_energy"] and not other["passes"]
    assert round(m2_qualify.qualify_panel(ARMS["L"], ARMS["P"], M_PLANNED)["ed_float"], 2) == -0.47
    assert round(m2_qualify.qualify_panel(ARMS["N"], ARMS["P"], M_PLANNED)["ed_float"], 2) == -8.80
    with pytest.raises(ValueError, match="at least 6"):
        m2_qualify.qualify_panel({k: ARMS["M"][k] for k in ("k1", "k2")}, {k: ARMS["P"][k] for k in ("k1", "k2")},
                                 M_PLANNED)


def test_c8_needs_a_planned_change():
    assert not m2_qualify.mechanistic_check(ARMS["M"], ARMS["P"], {})["passes"]


def test_k_rule_reproduces_c1():
    m = m2_qualify.k_rule(ARMS["M"], ARMS["P"], REAL)
    assert list(m["d"].values()) == [69, -64, 18, -146, 160, 22]
    assert (m["sd_d"], round(m["rho"], 2), m["K"]) == (105.831, 0.67, 16)
    assert m2_qualify.k_rule(ARMS["L"], ARMS["P"], REAL)["K"] == 8


def _stage_draw(counts: dict, key: str, op: str | None) -> dict:
    full = {**{row: REAL[row] for row in ROWS if row not in counts}, **counts}
    stats = {"entry_p95": {**{edge: (0.1 if kind == "quiet" else 50.0) for edge, kind in
                              m2_stage.ENTRY_CLASSES.items()}, "__network__": 300.0},
             "brakings": 8, "post_window": 40}
    health = {"status": "measured", "healthy": True, "native_status": "pass", "stall_status": "completed",
              "completion": {"loaded": 1, "inserted": 1, "arrived": 1, "running": 0, "waiting": 0, "teleports": 0,
                             "collisions": 0, "discarded": 0}, "observer_complete": True, "missing_field_bins": 0,
              "vehicle_count": 1}
    return {"counts": full, "health": health, "guard_stats": stats,
            "run": {"id": f"{op}-{key}", "key_sha256": hashlib.sha256(key.encode()).hexdigest(), "key_public": True,
                    "binding": {"scenario_op": op}, "identity": {}}}


def test_subsample_power_runs_the_stage_evaluator_on_every_disjoint_subset_pair():
    candidate = {f"c{i}": _stage_draw(ARMS["M"][f"k{i}"], f"c{i}", "M") for i in range(1, 7)}
    reference = {f"r{i}": _stage_draw(ARMS["P"][f"k{i}"], f"r{i}", None) for i in range(1, 7)}
    truth = {f"t{i}": _stage_draw(ARMS["M"][f"k{i}"], f"t{i}", "M")["counts"] for i in (1, 2)}
    found = m2_qualify.subsample_power(candidate, reference, real=REAL, truth=truth, draws=5)
    assert found["cases"] == 36 and sum(found["labels"].values()) == 36 and "blocked" not in found["labels"]
    assert 0 <= found["improved"] <= found["test_I_passes"] <= 36
    with pytest.raises(ValueError, match="disjoint"):
        m2_qualify.subsample_power(candidate, candidate, real=REAL, truth=truth, draws=5)


def test_screen_gate_keeps_the_frozen_margins():
    gate = m2_qualify.screen_gate(6)
    assert gate["draws"] == 6 and gate["h3_margins"] == m2_stage.frozen_gate(8)["h3_margins"]


def test_approximate_power_grows_with_k_for_a_strong_effect_and_stays_low_for_none():
    parent = [ARMS["P"][f"k{i}"] for i in range(1, 7)]
    far = [{row: value + (40 if row.startswith("R:0119Z11@") else 0) for row, value in draw.items()} for draw in parent]
    strong = m2_qualify.approximate_power(far, parent, far)
    assert strong["T_pools"] < -10
    assert strong["by_K"]["5"]["power"] <= strong["by_K"]["8"]["power"] <= strong["by_K"]["16"]["power"]
    none = m2_qualify.approximate_power(parent, parent, far)
    assert none["T_pools"] == 0 and none["by_K"]["16"]["power"] <= 0.05


def test_truth_ensemble_check_needs_eight_fresh_scored_draws():
    draws = {f"t{i}": _stage_draw({}, f"t{i}", "M") for i in range(8)}
    assert m2_qualify.truth_ensemble_check(draws, panel_fingerprints=[], rows=ROWS)["passes"]
    panel_print = draws["t0"]["run"]["key_sha256"]
    found = m2_qualify.truth_ensemble_check(draws, panel_fingerprints=[panel_print], rows=ROWS)
    assert not found["passes"] and "panel keys" in found["failures"][0]
    seven = {key: draws[key] for key in list(draws)[:7]}
    assert not m2_qualify.truth_ensemble_check(seven, panel_fingerprints=[], rows=ROWS)["passes"]


def _entry(name: str, *, qualified=True, k=16, power=0.5, guards=1.0, improved=0.0) -> dict:
    return {"name": name, "qualified": qualified, "K": k, "approximate_power": {str(k): power},
            "guards_hold_fraction": guards, "improved_fraction": improved}


def test_choose_c3_candidate_follows_the_declared_order():
    pick = m2_qualify.choose_c3_candidate
    assert pick([_entry("M", qualified=False)], preference=["X", "M"])["candidate"] is None
    assert pick([_entry("M", power=0.6), _entry("X", power=0.5)], preference=["X", "M"])["candidate"] == "M"
    assert pick([_entry("M", power=0.5), _entry("X", power=0.5)], preference=["X", "M"])["candidate"] == "X"
    assert pick([_entry("M", power=0.5, improved=0.2), _entry("X", power=0.5)],
                preference=["X", "M"])["candidate"] == "M"
    guarded = pick([_entry("M", power=0.9, guards=0.2), _entry("X", power=0.1)], preference=["X", "M"])
    assert guarded["candidate"] == "X" and guarded["guard_filter_applied"]


def test_c3_freeze_allocates_three_arms_of_k_secret_keys_within_the_cap():
    for draws, spares, total in ((8, 2, 30), (16, 1, 51)):
        freeze = m2_qualify.c3_freeze(candidate={"name": "M", "op": "cell_depart_shift:107:900"},
                                      target={"kind": "truth"}, k_rule_result={"K": draws}, code={}, c2={}, goal={})
        assert freeze["K"] == draws and set(freeze["arms"]) == {"reference", "candidate", "negative_control"}
        assert all(arm["draws"] == draws and arm["spares"] == spares for arm in freeze["arms"].values())
        assert freeze["runs"]["planned"] + freeze["runs"]["spares"] == total <= freeze["runs"]["cap"]
        assert freeze["gate"]["sha256"] == m2_stage.gate_sha256(m2_stage.frozen_gate(draws))
        assert freeze["arms"]["candidate"]["op"] == "cell_depart_shift:107:900"
        assert freeze["arms"]["negative_control"]["op"] is None
        assert m2_qualify.freeze_sha256(freeze) == m2_qualify.freeze_sha256(json.loads(json.dumps(freeze)))
