"""M2 protocol v2 offline helpers: exact sign rules, the CRPS split, the K3 material-effect screen, canonical cell
shifts and timing DoF, plus the judge's measurement-code and core-cleanliness checks. No test starts SUMO."""

from __future__ import annotations

import itertools
import json
from collections import defaultdict
from fractions import Fraction
from pathlib import Path

import pytest

from astra_sumo.twin import count_profile, gate_rules, goal, k3, noise, perturb, runner, scenario_ops

from test_twin_judge import CASE, PACKET, ROOT, fake_runner

W = k3.Windows(0.0, 900.0, 3600.0, 12600.0)


@pytest.fixture(scope="module")
def reader():
    return goal.case_reader(ROOT, PACKET, CASE)


@pytest.fixture(scope="module")
def prepared(reader) -> dict[str, bytes]:
    return {name: reader.read(f"prepared/{name}") for name in goal.PREPARED_FILES}


@pytest.fixture(scope="module")
def demand(prepared) -> perturb.Demand:
    return perturb.parse_demand(prepared["demand.rou.xml"])


# --- exact sign rules ----------------------------------------------------------------------------------------------

def test_binomial_requirements_are_exact():
    assert gate_rules.binomial_tail(5, 5) == Fraction(1, 32)
    assert gate_rules.binomial_tail(7, 8) == Fraction(9, 256)
    assert gate_rules.required_count(4, 0.05) is None and gate_rules.minimum_draws(0.05) == 5
    assert [gate_rules.required_count(k, 0.05) for k in (5, 6, 8, 10, 12, 16)] == [5, 6, 7, 9, 10, 12]
    assert gate_rules.power(5, 0.05, 0.9) == pytest.approx(0.9 ** 5)
    assert gate_rules.power(4, 0.05, 1.0) == 0.0


@pytest.mark.parametrize("support", [((-20, 0.5), (30, 0.5)), ((-11, 0.5), (400, 0.5)), ((-10, 0.5), (-9, 0.5))])
def test_sign_rule_size_is_at_most_the_binomial_tail_for_any_shape(support):
    """Exact enumeration: whatever the values (skewed, heavy, tied at -g), P(accept | P(d <= -g) = 1/2) <= 1/32."""
    accept = Fraction(0)
    for draw in itertools.product(support, repeat=5):
        values = [value for value, _ in draw]
        probability = Fraction(1)
        for _, p in draw:
            probability *= Fraction(p)
        if gate_rules.improvement(values, 10, alpha=0.05)["passes"]:
            accept += probability
    assert accept <= Fraction(1, 32)


def test_improvement_and_noninferiority_branches():
    passed = gate_rules.improvement([-30, -12, -10, -40, -11], 10, alpha=0.05)
    assert passed["passes"] and passed["hits"] == 5 and passed["size_bound"] == pytest.approx(1 / 32)
    assert not gate_rules.improvement([-30, -12, -9, -40, -11], 10, alpha=0.05)["passes"]
    assert not gate_rules.improvement([-30, -30, -30, -30], 10, alpha=0.05)["assessable"]
    eight = gate_rules.improvement([-30, -30, -30, -30, -30, -30, -30, 50], 10, alpha=0.05)
    assert eight["passes"] and eight["required"] == 7
    assert not gate_rules.improvement([-11, -11, -11, -11, -11, -11, -11, 500], 10, alpha=0.05)["passes"]
    assert gate_rules.noninferiority([0, 4, -3, 1, 2], 5, alpha=0.05)["passes"]
    assert not gate_rules.noninferiority([0, 5, -3, 1, 2], 5, alpha=0.05)["passes"]


def test_comparison_statistics():
    assert gate_rules.sign_flip_p_value([-20, -15, -30, -11, -12], 10) == pytest.approx(1 / 32)
    assert gate_rules.sign_flip_p_value([0, 0, 0, 0], 10) == 1.0
    values = [3.0, -5.0, 7.0, 1.0]
    assert gate_rules.t_upper_bound(values) == pytest.approx(noise.t_upper_bound(values, 2.353))
    assert gate_rules.t_upper_bound([1.0]) is None


def test_crps_split_flags_a_gain_from_lower_spread_only():
    rows = ["a"]
    parent = [{"a": 0}, {"a": 2}]
    sharp = [{"a": 1}, {"a": 1}]
    split = gate_rules.crps_decomposition(sharp, parent, {"a": 1}, rows, g=1)
    assert split["d_mean_tae"] == -1 and split["d_crps"] == 0 and split["d_half_gmd"] == -1
    assert split["dispersion_driven"]
    assert split["d_mean_tae"] == pytest.approx(split["d_crps"] + split["d_half_gmd"])
    shifted = gate_rules.crps_decomposition([{"a": 3}, {"a": 5}], parent, {"a": 4}, rows, g=1)
    assert shifted["d_crps"] == -2 and not shifted["dispersion_driven"]
    assert not gate_rules.crps_decomposition([{"a": 1}], parent, {"a": 1}, rows, g=1)["assessable"]


def test_evaluate_case_pairs_keys_and_reports_no_effect():
    target = {"a": 10, "b": 5}
    reference = {f"k{i}": {"a": 10 + i, "b": 5} for i in range(5)}
    better = {key: {"a": 10, "b": 5} for key in reference}
    case = gate_rules.evaluate_case(better, reference, target, ["a", "b"], g=1, delta=1, alpha=0.05)
    assert case["d"] == [0, -1, -2, -3, -4] and not case["improvement"]["passes"] and not case["no_effect"]
    same = gate_rules.evaluate_case(reference, reference, target, ["a", "b"], g=1, delta=1, alpha=0.05)
    assert same["no_effect"] and same["noninferiority"]["passes"] and same["paired_counts_distance"]["l1"] == [0] * 5
    with pytest.raises(ValueError, match="Unpaired"):
        gate_rules.evaluate_case(better, {"k0": reference["k0"]}, target, ["a"], g=1, delta=1, alpha=0.05)


def test_verdict_follows_the_gate_table_order():
    target = {"a": 0}
    reference = {f"k{i}": {"a": 20 + i} for i in range(5)}

    def case(values):
        candidate = {key: {"a": value} for key, value in zip(sorted(reference), values)}
        return gate_rules.evaluate_case(candidate, reference, target, ["a"], g=10, delta=5, alpha=0.05)

    improved = case([5, 6, 7, 8, 9])
    assert gate_rules.verdict(improved, g=10, dof_lower=False)["label"] == "improved"
    assert gate_rules.verdict(improved, g=10, dof_lower=False, guard_failure="row_regress")["label"] == "row_regress"
    assert gate_rules.verdict(case([20, 21, 22, 23, 24]), g=10, dof_lower=False)["label"] == "no_effect"
    near = case([22, 23, 22, 23, 24])
    assert gate_rules.verdict(near, g=10, dof_lower=True)["label"] == "simplified"
    assert gate_rules.verdict(near, g=10, dof_lower=False)["label"] == "not_material"
    assert gate_rules.verdict(case([5, 6, 7, 8, 30]), g=10, dof_lower=False)["label"] == "not_robust"
    assert gate_rules.verdict(case([40, 41, 42, 43, 44]), g=10, dof_lower=True)["label"] == "worse"
    with pytest.raises(ValueError, match="Unknown guard"):
        gate_rules.verdict(improved, g=10, dof_lower=False, guard_failure="blocked?")


# --- material-effect screen ----------------------------------------------------------------------------------------

def test_shift_effect_counts_planned_row_changes_and_crossings():
    arrivals = {"v1": 3500.0, "v2": 3700.0, "v3": 12500.0}
    intended = {v: "hh_0119_Z_13_34018" for v in arrivals}
    effect = k3.shift_effect(list(arrivals), arrivals, intended, 300.0, W)
    assert effect["delta"] == {"R:0119Z13@11700": -1, "R:0119Z13@3600": 1}
    assert effect["crossings"] == {"init->main": 1, "main->post": 1} and effect["moved_main"] == 2
    assert effect["l1"] == 2 and k3.classify(effect["l1"], 10) == "immaterial"
    assert [k3.classify(x, 10) for x in (0, 9, 10, 20, 40)] == ["none", "immaterial", "small", "moderate", "large"]


def test_noise_aware_effect_is_bounded_by_l1_and_absorbed_by_row_noise():
    panel = [{"r": 5}, {"r": 10}, {"r": 0}]
    samples = k3.pairwise_row_noise(panel, ["r"])
    assert sorted(samples["r"]) == sorted(-x for x in samples["r"])
    assert k3.noise_aware_effect({"r": 2}, {"r": (5, -5)}) == 0
    assert k3.noise_aware_effect({"r": 4}, {}) == 4
    assert 0 <= k3.noise_aware_effect({"r": 4}, samples) <= 4
    assert k3.noisy_detectors([{"R:A@0": 1, "R:B@0": 3}, {"R:A@0": 1, "R:B@0": 7}], ["R:A@0", "R:B@0"]) == {"B": 2.0}


def test_delay_preserving_shift_moves_measured_passages():
    passages = k3.index_passages([["v1", "hh_0535_Z_8_34287", 3600.0, 3650.0, 3650.1, 0.3],
                                  ["v2", "hh_0535_Z_8_34287", 3600.0, 4000.0, 4000.1, 0.3]])
    rows = {"R:0535Z8@3600", "R:0535Z8@4500"}
    assert k3.shifted_delta(passages, {"v1"}, 900.0, W, rows) == {"R:0535Z8@3600": -1, "R:0535Z8@4500": 1}
    move = {"R:0535Z8@3600": -1, "R:0535Z8@4500": 1}
    target = {"R:0535Z8@3600": 1, "R:0535Z8@4500": 1}
    quiet = [{"R:0535Z8@3600": 2, "R:0535Z8@4500": 0}] * 3
    prediction = k3.delay_preserving_prediction(quiet, [k3.apply_delta(c, move) for c in quiet], target, sorted(rows))
    assert prediction["twin_gain_mean"] == 2 and prediction["no_chaos_d_sd"] == 0 and prediction["real_d"] == [-2] * 3
    noisy = [{"R:0535Z8@3600": 2, "R:0535Z8@4500": 0}, {"R:0535Z8@3600": 1, "R:0535Z8@4500": 1}]
    prediction = k3.delay_preserving_prediction(noisy, [k3.apply_delta(c, move) for c in noisy], target, sorted(rows))
    assert prediction["twin_gain_mean"] == 0 and prediction["real_d"] == [-2, 2]
    with pytest.raises(ValueError, match="recorded bin"):
        k3.shifted_delta(k3.index_passages([["v1", "d", 0.0, 950.0, 950.0, 0.1]]), {"v1"}, 1.0, W, rows)


def test_screen_on_the_packet_finds_codex_cell_0_immaterial(reader, prepared, demand):
    network = count_profile.parse_network(prepared["network.net.xml"], prepared["control.add.xml"])
    requests = count_profile.parse_demand(prepared["demand.rou.xml"])
    intended = count_profile.parse_intentions(prepared["lane-intentions.json"])
    arrivals = count_profile.free_flow_arrivals(network, requests, intended,
                                                count_profile.parse_field_loops(prepared["field-loops.add.xml"]))
    table = scenario_ops.cell_table(demand, prepared["lane-intentions.json"], od_pairs=22, periods=14)
    members = defaultdict(list)
    for vehicle, cell in scenario_ops.od_cells(prepared["lane-intentions.json"]).items():
        members[cell].append(vehicle)
    departs = {v: r.depart_s for v, r in requests.items()}
    rows = k3.screen({c: table[c] for c in (0, 107, 301)}, members, arrivals, intended, departs,
                     shifts=[-900.0, 900.0], windows=W, horizon_s=14400.0, g=10.0)
    found = {row["op"]: row for row in rows}
    assert found["cell_depart_shift:0:900"]["l1"] == 0 and found["cell_depart_shift:0:900"]["vehicles"] == 13
    assert "cell_depart_shift:0:-900" not in found
    assert found["cell_depart_shift:107:900"]["delta"] == {"R:0535Z4@3600": -17, "R:0535Z4@4500": 17}
    late = found["cell_depart_shift:301:-900"]
    assert late["l1"] == 120 and late["l1_class"] == "large" and late["earliest_change_s"] == 10881.304
    chosen = k3.select_ops(rows, wanted="large", count=5, seed="s", single_field=False, class_key="l1_class")
    assert len({row["cell"] for row in chosen}) == len(chosen) >= 1
    assert chosen == k3.select_ops(list(reversed(rows)), wanted="large", count=5, seed="s", single_field=False,
                                   class_key="l1_class")
    assert k3.confusable({"a": 1}, {"a": 1, "b": 1}, 2) and not k3.confusable({"a": 3}, {"b": 3}, 6)


# --- canonical shifts and DoF ---------------------------------------------------------------------------------------

def test_canonical_shifts_make_dof_zero_equal_to_the_base_bytes(demand, prepared):
    intentions = prepared["lane-intentions.json"]
    cells = scenario_ops.od_cells(intentions)
    one, _ = scenario_ops.cell_depart_shift(demand, intentions, cell=1, shift_s=300)
    canonical, record = scenario_ops.cell_depart_shifts(demand, intentions, {1: 300})
    assert canonical.to_bytes() == one.to_bytes() and record["vehicles"] == sum(c == 1 for c in cells.values())
    assert k3.timing_dof(canonical, demand, cells)["dof"] == 1
    zero, _ = scenario_ops.cell_depart_shifts(demand, intentions, {1: 0, 5: "0"})
    assert zero.to_bytes() == demand.to_bytes() and k3.timing_dof(zero, demand, cells)["byte_identical"]
    two, record = scenario_ops.cell_depart_shifts(demand, intentions, {1: 300, 107: -600})
    assert k3.timing_dof(two, demand, cells)["cells"] == [1, 107] and record["net_shift_s"] == {"1": "300",
                                                                                                "107": "-600"}
    # One op after another: the multiset is restored, the file order of cross-cell ties is not.
    back, _ = scenario_ops.cell_depart_shift(one, intentions, cell=1, shift_s=-300)
    sequential = k3.timing_dof(back, demand, cells)
    assert sequential["dof"] == 0 and not sequential["byte_identical"]
    with pytest.raises(ValueError, match="outside"):
        scenario_ops.cell_depart_shifts(demand, intentions, {0: -100000})


def test_folds_and_jobs():
    folds = k3.loo_folds(["k1", "k2", "k3"])
    assert folds[0] == {"truth": "k1", "judge": ["k2", "k3"]} and len(folds) == 3
    jobs = k3.judge_jobs({"parent": None, "L": "cell_depart_shift:301:-900"}, ["m2-pilot/k5"], purpose="p")
    assert jobs[0] == {"label": "parent-k5", "purpose": "p: parent on m2-pilot/k5", "family": "depart_jitter",
                       "public_key": "m2-pilot/k5"}
    assert jobs[1]["op"] == "cell_depart_shift:301:-900" and jobs[1]["label"] == "L-k5"


# --- judge cache: measurement code and core cleanliness ------------------------------------------------------------

def test_a_judge_measured_by_other_code_is_re_measured_without_a_native_run(reader, tmp_path):
    goal_file, _ = goal.write_goal(goal.create_calibrate_goal(ROOT, PACKET, CASE), tmp_path / "goals")
    replay, calls = fake_runner(reader)
    plan = runner.plan_judge(goal_file, tmp_path / "store", label="P1", purpose="test", repository_root=ROOT)
    main = reader.read_csv("prepared/expected-counts.csv")
    init = reader.read_csv("prepared/initialization-counts.csv")
    first = runner.execute_plan(plan, main_rows=main, init_rows=init, runner=replay, watch_memory=False)
    assert first["measurement_code_sha256"] == plan.provenance["measurement_code_sha256"] == \
        runner.measurement_code_sha256(ROOT)
    assert runner.read_judge(plan) is not None
    path = Path(plan.run_dir) / "judge.json"
    stale = json.loads(path.read_text(encoding="utf-8"))
    stale["measurement_code_sha256"] = "0" * 64
    path.write_text(json.dumps(stale), encoding="utf-8")
    assert runner.read_judge(plan) is None
    again = runner.execute_plan(plan, main_rows=main, init_rows=init, runner=replay, watch_memory=False)
    assert len(calls) == 1 and not again["native_run"] and again["digests"] == first["digests"]
    assert runner.read_judge(plan)["measurement_code_sha256"] == first["measurement_code_sha256"]


def test_planning_refuses_unhashed_dirty_core_modules_and_the_flag_stays_out_of_the_key(tmp_path):
    goal_file, _ = goal.write_goal(goal.create_calibrate_goal(ROOT, PACKET, CASE), tmp_path / "goals")
    runtime = runner.runtime_identity(ROOT)
    assert runtime["_core_unhashed_dirty"] == []
    clean = runner.plan_judge(goal_file, tmp_path / "s", label="a", purpose="t", repository_root=ROOT,
                              runtime=runtime)
    with pytest.raises(ValueError, match="not hashed"):
        runner.plan_judge(goal_file, tmp_path / "s", label="a", purpose="t", repository_root=ROOT,
                          runtime={**runtime, "_core_unhashed_dirty": ["map_review.py"]})
    assert "_core_unhashed_dirty" not in clean.request["runtime"]
