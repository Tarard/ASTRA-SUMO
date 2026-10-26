"""M4 fragility metrics (twin/fragility.py): episodes, the wrong-lane rule under usable links, site attribution, the
gridlock root, the primary metrics and the exact unpaired tests, on small synthetic native directories; plus a
store-backed check that reproduces the 106-run M2 census on cached runs (skipped when the store is absent).

No test starts SUMO.
"""

from __future__ import annotations

import gzip
import hashlib
import itertools
import json
import math
import shutil
from fractions import Fraction
from pathlib import Path

import pytest

from astra_sumo.twin import fragility

PILOT = Path(r"E:\twin-store\m2-pilot")

NET = b"""<net version="1.20">
  <edge id=":J_0" function="internal"><lane id=":J_0_0" index="0" length="5" shape="0,0 5,0"/></edge>
  <edge id=":J_1" function="internal"><lane id=":J_1_0" index="0" length="5" shape="0,0 5,0"/></edge>
  <edge id=":J_2" function="internal"><lane id=":J_2_0" index="0" length="5" shape="0,0 5,0"/></edge>
  <edge id=":J_3" function="internal"><lane id=":J_3_0" index="0" length="5" shape="0,0 5,0"/></edge>
  <edge id="A" from="n0" to="J"><lane id="A_0" index="0" length="100" shape="0,0 100,0"/>
    <lane id="A_1" index="1" length="100" shape="0,3 100,3"/></edge>
  <edge id="B" from="J" to="n1"><lane id="B_0" index="0" length="100" shape="0,0 100,0"/></edge>
  <edge id="C" from="J" to="n2"><lane id="C_0" index="0" length="100" shape="0,0 100,0"/></edge>
  <edge id="D" from="n2" to="n3"><lane id="D_0" index="0" length="10" shape="0,0 10,0"/></edge>
  <tlLogic id="J" type="static" programID="0" offset="0">
    <phase duration="30" state="GGG"/><phase duration="30" state="rrr"/></tlLogic>
  <junction id="n0" type="dead_end" x="0" y="0"/>
  <junction id="J" type="traffic_light" x="100" y="0" intLanes=":J_0_0 :J_1_0 :J_2_0"/>
  <junction id="n1" type="dead_end" x="200" y="0"/>
  <junction id="n2" type="priority" x="200" y="50"/>
  <junction id="n3" type="dead_end" x="210" y="50"/>
  <connection from="A" to="B" fromLane="0" toLane="0" via=":J_0_0" tl="J" linkIndex="0" dir="s" state="O"/>
  <connection from="A" to="C" fromLane="1" toLane="0" via=":J_1_0" tl="J" linkIndex="1" dir="l" state="O"/>
  <connection from="A" to="C" fromLane="0" toLane="0" via=":J_2_0" tl="J" linkIndex="2" dir="l" state="O"/>
  <connection from=":J_0" to="B" fromLane="0" toLane="0" dir="s" state="M"/>
  <connection from=":J_1" to="C" fromLane="0" toLane="0" dir="l" state="M"/>
  <connection from=":J_2" to="C" fromLane="0" toLane="0" via=":J_3_0" dir="l" state="m"/>
  <connection from=":J_3" to="C" fromLane="0" toLane="0" dir="l" state="M"/>
  <connection from="C" to="D" fromLane="0" toLane="0" dir="s" state="M"/>
</net>
"""
# The executed control never greens link 2 (A_0 -> C_0), so A_0 reaches only B through a usable link.
CONTROL = b"""<additional><tlLogic id="J" type="static" programID="readme_fixed_A" offset="0">
  <phase duration="30" state="GGr"/><phase duration="30" state="rrr"/></tlLogic></additional>
"""


def _queues(series: dict[str, dict[float, float]], end: float) -> str:
    """queue-output lines: one data step per second, each lane present while it has a value."""
    lines = ['<queue-export>']
    for step in range(int(end) + 1):
        lines.append(f'    <data timestep="{step:.2f}">')
        lines.append("        <lanes>")
        for lane, values in series.items():
            if float(step) in values:
                lines.append(f'            <lane id="{lane}" queueing_time="{values[float(step)]:.2f}" '
                             'queueing_length="7.50" queueing_length_experimental="7.50"/>')
        lines.append("        </lanes>")
        lines.append("    </data>")
    lines.append("</queue-export>")
    return "\n".join(lines) + "\n"


def _ramp(start: int, peak: int, *, then: float | None = None) -> dict[float, float]:
    values = {float(t): float(t - start) for t in range(start, peak + 1)}
    if then is not None:
        values[float(peak + 1)] = then
    return values


def _native(tmp_path: Path, *, stalled: bool = False) -> Path:
    native = tmp_path / "native"
    native.mkdir()
    (native / "network.net.xml").write_bytes(NET)
    (native / "signal-programs.add.xml").write_bytes(CONTROL)
    series = {"A_0": {**_ramp(10, 80, then=0.5), **_ramp(300, 365)},  # 70 s, then 65 s open at the end
              "A_1": _ramp(110, 200),  # 90 s, then the lane leaves the output
              "B_0": _ramp(120, 150)}  # 30 s: below the episode minimum
    (native / "queues.xml").write_text(_queues(series, 365), encoding="utf-8")
    (native / "vehicle-routes.xml").write_text(
        '<routes>\n'
        '  <vehicle id="v1" depart="0.00"><route edges="A C" exitTimes="82.00 90.00"/></vehicle>\n'
        '  <vehicle id="v2" depart="105.00"><route edges="A C" exitTimes="202.00 210.00"/></vehicle>\n'
        '  <vehicle id="v3" depart="100.00"><route edges="A B" exitTimes="203.00 215.00"/></vehicle>\n'
        '  <vehicle id="v6" depart="5.00"><route edges="A B" exitTimes="9.00 20.00"/></vehicle>\n'
        '</routes>\n', encoding="utf-8")
    (native / "sumo-errors.log").write_text(
        "Warning: Vehicle 'v1' performs emergency braking on lane ':J_3_0' with decel=9.00, wished=4.50, "
        "severity=1.00, time=50.00.\n"
        "Warning: Vehicle 'v2' performs emergency braking on lane ':J_0_0' with decel=9.00, wished=4.50, "
        "severity=1.00, time=60.00.\n"
        "Warning: Teleporting vehicle 'v4'; side collision with vehicle 'v5', lane='C_0', gap=-1.00, time=70.00, "
        "stage=laneChange.\n", encoding="utf-8")
    (native / "collisions.xml").write_text(
        '<?xml version="1.0"?>\n<!-- header -->\n<collisions>\n  <collision time="70.00" type="side" lane="C_0" '
        'pos="5.00" collider="v4" victim="v5" colliderSpeed="1.00" victimSpeed="0.00"/>\n</collisions>\n',
        encoding="utf-8")
    stall = {"status": "completed", "simulation_time_s": 365.0, "stop": None}
    if stalled:
        stall = {"status": "all_stopped", "simulation_time_s": 365.0,
                 "stop": {"vehicles": {"v9": {"lane": "A_0"}, "v8": {"lane": "C_0"}}}}
        state = ('<snapshot time="365.00">\n'
                 '  <route id="!v9" state="0" edges="A C"/>\n  <route id="!v8" state="0" edges="C D"/>\n'
                 '  <vehicle id="v9" route="!v9" state="1 290000 0 5.10 65000 1.0 0"/>\n'
                 '  <vehicle id="v8" route="!v8" state="1 300000 0 5.10 20000 1.0 0"/>\n'
                 '  <lane id="A_0"><vehicles value="v9"/></lane>\n  <lane id="C_0"><vehicles value="v8"/></lane>\n'
                 '</snapshot>\n')
        (native / "all-stopped-state.xml.gz").write_bytes(gzip.compress(state.encode("utf-8"), mtime=0))
    (native / "stall-monitor.json").write_text(json.dumps(stall), encoding="utf-8")
    return native


def test_episodes_follow_the_census_rule():
    lines = _queues({"A_0": {**_ramp(10, 80, then=0.5), **_ramp(300, 365)}, "A_1": _ramp(110, 200),
                     "B_0": _ramp(120, 150)}, 365).splitlines()
    episodes = fragility.standstill_episodes(lines)
    assert [(row["lane"], row["start"], row["peak_at"], row["halt_s"], row["open_at_end"]) for row in episodes] == [
        ("A_0", 10.0, 80.0, 70.0, False), ("A_1", 110.0, 200.0, 90.0, False), ("A_0", 300.0, 365.0, 65.0, True)]


def test_usable_links_reach_via_chains_and_sites():
    network = fragility.Network(NET, CONTROL)
    assert network.never_open == {("J", 2)}
    assert network.reach("A_0") == {"B"} and network.reach("A_1") == {"C"}
    assert fragility.Network(NET).reach("A_0") == {"B", "C"}  # the embedded program greens link 2
    assert network.via_chain("A_0", "C_0") == [":J_2_0", ":J_3_0"]
    assert network.site(":J_1_0") == "new:J" and network.site("C_0") == "new:n2" and network.site("D_0") == "new:n2"
    assert fragility.lane_status("A_0", ["C"], network) == "wrong_lane"
    assert fragility.lane_status("A_0", ["B", None], network) == "correct_lane"
    assert fragility.lane_status("A_1", ["C", "B"], network) == "ambiguous"
    assert fragility.lane_status("A_1", [], network) == "unmatched"
    assert fragility.lane_status(":J_0_0", ["B"], network) == "internal"


def test_run_metrics_on_a_synthetic_run(tmp_path, monkeypatch):
    monkeypatch.setattr(fragility, "S01_EDGE", "A")
    monkeypatch.setattr(fragility, "S03_LANE", "A_1")
    monkeypatch.setattr(fragility, "S03_LINK7", ("A_0", "C_0"))
    monkeypatch.setattr(fragility, "SITES", {"S01": ("J",)})
    metrics = fragility.run_metrics(_native(tmp_path))
    rows = {(row["lane"], row["start"]): row for row in metrics["standstills"]["wrong_lane"]}
    assert set(rows) == {("A_0", 10.0)}  # the open A_0 episode has no state file and no covering vehicle
    assert rows[("A_0", 10.0)]["next_edges"] == ["C"] and rows[("A_0", 10.0)]["site"] == "S01"
    assert metrics["standstills"]["status"] == {"wrong_lane": 1, "ambiguous": 1, "unmatched": 1}
    assert metrics["primary"] == {"S01_wrong_lane_episodes": 1, "S01_max_wrong_lane_halt_s": 70.0,
                                  "S03_trap_episodes": 0, "S03_link7_eb": 1, "S03_metric": 1}
    assert metrics["link7_lanes"] == [":J_2_0", ":J_3_0"] and metrics["never_open_links"] == [["J", 2]]
    assert metrics["emergency_brakings"]["by_site"] == {"S01": 2}
    assert [row["site"] for row in metrics["collisions"]] == ["new:n2"]
    assert metrics["teleports"][0]["reason"] == "collision:side" and metrics["teleports"][0]["site"] == "new:n2"
    assert metrics["new_sites"] == ["new:n2"] and metrics["gridlock"] is None
    assert metrics["by_site"]["S01"]["wrong_lane_standstills"] == 1


def test_a_gridlock_reads_the_saved_state(tmp_path, monkeypatch):
    monkeypatch.setattr(fragility, "S01_EDGE", "A")
    monkeypatch.setattr(fragility, "SITES", {"S01": ("J",)})
    metrics = fragility.run_metrics(_native(tmp_path, stalled=True))
    assert metrics["gridlock"] == {"root_vehicle": "v9", "root_lane": "A_0", "root_site": "S01",
                                   "root_next_edge": "C", "root_status": "wrong_lane", "root_halted_since": 300.0,
                                   "stopped": 2, "stop_time_s": 365.0}
    opened = [row for row in metrics["standstills"]["wrong_lane"] if row["open_at_end"]]
    assert [(row["lane"], row["basis"]) for row in opened] == [("A_0", "all_stopped_state")]
    assert metrics["primary"]["S01_wrong_lane_episodes"] == 2


# The candidate's control greens link 2 (A_0 -> C_0): on its own links A_0 reaches C.
CANDIDATE_CONTROL = CONTROL.replace(b'state="GGr"', b'state="GGG"')


def test_primary_metrics_judge_wrong_lanes_by_the_parent_links(tmp_path, monkeypatch):
    """Arm-neutral: an edit that opens the way out must not make the event impossible by definition."""
    monkeypatch.setattr(fragility, "S01_EDGE", "A")
    monkeypatch.setattr(fragility, "SITES", {"S01": ("J",)})
    native = _native(tmp_path)
    (native / "signal-programs.add.xml").write_bytes(CANDIDATE_CONTROL)
    parent = fragility.Network(NET, CONTROL)
    own = fragility.run_metrics(native)
    neutral = fragility.run_metrics(native, parent=parent)
    assert own["primary"]["S01_wrong_lane_episodes"] == 0  # its own links make the A_0 halt bound for C correct
    assert neutral["primary"]["S01_wrong_lane_episodes"] == 1
    assert neutral["secondary"]["S01_wrong_lane_episodes_own_links"] == 0
    assert neutral["secondary"]["S01_standstills_by_status"] == {"correct_lane": 1, "ambiguous": 1, "unmatched": 1}
    basis = neutral["primary_basis"]
    assert basis["parent_control_sha256"] == hashlib.sha256(CONTROL).hexdigest() and not basis["own_links"]
    assert own["primary_basis"]["own_links"]
    with pytest.raises(ValueError, match="different parent links"):
        fragility.primary_tests("F2", [neutral], [own])
    with pytest.raises(ValueError, match="not this run's net and control"):
        fragility.run_metrics(native, network=parent)


def test_the_trap_counts_only_next_edges_behind_a_never_open_link(tmp_path, monkeypatch):
    """S03_trap_episodes: a wrong-lane halt counts only when every covering vehicle is bound for an edge the lane
    reaches through a never-green link alone (LSA535 link 5 on the parent)."""
    monkeypatch.setattr(fragility, "S03_LANE", "A_0")
    native = _native(tmp_path)
    routes = (native / "vehicle-routes.xml").read_text(encoding="utf-8").replace(
        "</routes>", '  <vehicle id="v7" depart="299.00"><route edges="A D" exitTimes="370.00 380.00"/></vehicle>\n'
                     "</routes>")
    (native / "vehicle-routes.xml").write_text(routes, encoding="utf-8")
    metrics = fragility.run_metrics(native)
    assert metrics["primary_basis"]["S03_trap_edges"] == ["C"]
    assert metrics["secondary"]["S03_wrong_lane_episodes_own_links"] == 2  # bound for C at 10 s, for D at 300 s
    assert metrics["primary"]["S03_trap_episodes"] == 1
    assert [row["start"] for row in metrics["secondary"]["S03_trap_episode_rows"]] == [10.0]
    assert metrics["secondary"]["S03_lane_episodes_by_parent_status"] == {"wrong_lane": 2}
    # A candidate without the never-green link: judged by the parent's links, the same halt is still a trap.
    edited = fragility.Network(NET.replace(b'<connection from="A" to="C" fromLane="0" toLane="0" via=":J_2_0" '
                                           b'tl="J" linkIndex="2" dir="l" state="O"/>', b""), CONTROL)
    assert edited.closed_only("A_0") == set() and fragility.Network(NET, CONTROL).closed_only("A_0") == {"C"}


def _brute(candidate, reference, statistic):
    pooled = [*candidate, *reference]
    scores = statistic(pooled)
    observed = sum(scores[: len(candidate)])
    subsets = list(itertools.combinations(range(len(pooled)), len(candidate)))
    return Fraction(sum(sum(scores[i] for i in subset) <= observed for subset in subsets), len(subsets))


@pytest.mark.parametrize("candidate, reference", [
    ([0, 0, 1], [1, 1, 0, 2, 1]), ([0, 0, 0, 0], [1, 0, 1, 1, 0, 1]), ([2, 3], [0, 1, 1, 0]),
    ([1, 1, 1], [1, 1, 1, 1]), ([0, 4, 0, 1], [3, 2, 0, 5, 1, 0])])
def test_exact_tests_equal_full_enumeration(candidate, reference):
    rank = fragility.exact_rank_test(candidate, reference)
    mean = fragility.exact_mean_test(candidate, reference)
    assert Fraction(rank["p_exact"]) == _brute(candidate, reference, fragility.midranks)
    assert Fraction(mean["p_exact"]) == _brute(candidate, reference, lambda values: list(values))
    assert rank["relabelings"] == mean["relabelings"] == math.comb(len(candidate) + len(reference), len(candidate))


def test_exact_tests_at_the_registered_sizes():
    """16 candidate zeros against 46 reference draws with 24 ones: p = C(38, 16) / C(62, 16) for both statistics."""
    reference = [1] * 24 + [0] * 22
    expected = Fraction(math.comb(38, 16), math.comb(62, 16))
    assert Fraction(fragility.exact_rank_test([0] * 16, reference)["p_exact"]) == expected
    assert Fraction(fragility.exact_mean_test([0] * 16, reference)["p_exact"]) == expected
    assert fragility.exact_rank_test([1] * 16, reference)["p"] == 1.0
    with pytest.raises(ValueError, match="integer"):
        fragility.exact_mean_test([0.5], [1])


def test_holm_adjusts_step_down():
    assert fragility.holm({"S01": 0.01, "S03": 0.04}) == {"S01": 0.02, "S03": 0.04}
    assert fragility.holm({"S01": 0.03, "S03": 0.02}) == {"S03": 0.04, "S01": 0.04}


# ---- store-backed: the census numbers of cached M2 runs ------------------------------------------------------------

CENSUS = {
    # run id: (S01 wrong-lane episodes with (start, halt), S03 link-7 EB, EB by site, collisions, gridlock root)
    "P3-jitter-k1-s104": ([(7943.6, 65.4)], 1, {"S01": 2, "S02": 2, "S03": 1}, [], None),
    "c1-parent-k14": ([(6140.4, 65.6)], 1, {"S02": 5, "S03": 1}, [(8481.1, ":315029332_0_2", "S02")], None),
    "s3-M-k6": ([(7940.6, 61.4), (10641.0, 225.0)], 2, {"S02": 8, "S03": 2}, [], None),
    "c3-reference-03": ([(6141.7, 6722.3)], 0, {"S02": 5}, [], ("od_0131_v00005", "603103444_1", 6141.8)),
}


@pytest.mark.skipif(not (PILOT / "runs.jsonl").is_file(), reason="the M2 pilot store E:\\twin-store\\m2-pilot is absent")
def test_the_module_reproduces_the_m2_census_on_cached_runs():
    rows = {json.loads(line)["id"]: json.loads(line) for line in (PILOT / "runs.jsonl").read_text(
        encoding="utf-8").splitlines() if line.strip()}
    network = None
    for run_id, (episodes, link7, by_site, collisions, root) in CENSUS.items():
        native = Path(rows[run_id]["run_dir"]) / "native"
        network = network or fragility.Network((native / "network.net.xml").read_bytes(),
                                               (native / "signal-programs.add.xml").read_bytes())
        metrics = fragility.run_metrics(native, network=network)
        s01 = [(row["start"], row["halt_s"]) for row in metrics["standstills"]["wrong_lane"]
               if row["lane"].startswith("603103444_")]
        assert s01 == episodes, run_id
        assert metrics["primary"]["S01_wrong_lane_episodes"] == len(episodes)
        assert metrics["primary"]["S03_link7_eb"] == link7 and metrics["emergency_brakings"]["by_site"] == by_site
        assert [(row["time"], row["lane"], row["site"]) for row in metrics["collisions"]] == collisions
        assert len(metrics["teleports"]) == len(collisions)
        if root is None:
            assert metrics["gridlock"] is None
        else:
            gridlock = metrics["gridlock"]
            assert (gridlock["root_vehicle"], gridlock["root_lane"], gridlock["root_halted_since"]) == root
            assert gridlock["root_site"] == "S01" and gridlock["root_status"] == "wrong_lane"
        assert metrics["never_open_links"] == [["LSA104_part0", 14], ["LSA535_part0", 5]]


def test_the_cli_prints_the_primary_metrics(tmp_path):
    import subprocess
    import sys

    root = Path(__file__).resolve().parents[1]
    native = _native(tmp_path)
    out = tmp_path / "metrics.json"
    done = subprocess.run([sys.executable, "-B", str(root / "plugins/astra-sumo/scripts/astra_twin.py"), "fragility",
                           "--native", str(native), "--out", str(out), "--json"], capture_output=True, text=True,
                          timeout=600)
    assert done.returncode == 0, done.stdout + done.stderr
    payload = json.loads(done.stdout)
    assert payload["status"] == "measured" and payload["collisions"] == 1 and payload["teleports"] == 1
    assert json.loads(out.read_text(encoding="utf-8"))["schema"] == fragility.SCHEMA


def _run(s01: int, s03: int, *, collisions=(), gridlock=None, new_sites=()) -> dict:
    return {"primary": {"S01_wrong_lane_episodes": s01, "S03_metric": s03},
            "collisions": [{"site": site} for site in collisions], "teleports": [{"site": site} for site in collisions],
            "gridlock": None if gridlock is None else {"root_site": gridlock}, "new_sites": list(new_sites)}


def test_primary_tests_new_failure_sites_and_the_decision_rule():
    reference = [_run(1, 2) for _ in range(24)] + [_run(0, 1, collisions=("S02",)) for _ in range(22)]
    candidate = [_run(0, 2) for _ in range(16)]
    f2 = fragility.primary_tests("F2", candidate, reference)
    assert f2["adjustment"] == "none" and f2["tests"]["S01_wrong_lane_episodes"]["rejected"]
    f3 = fragility.primary_tests("F3", candidate, reference)
    assert f3["adjustment"] == "holm" and not f3["tests"]["S03_metric"]["rejected"]
    clean = fragility.new_failure_sites(candidate, reference)
    assert not clean["any"]
    moved = fragility.new_failure_sites([_run(0, 0, collisions=("new:260305243",))], reference)
    assert moved["new"]["collision"] == ["new:260305243"] and moved["any"]
    quiet = {"negative_control": {"label": "not_significant"}, "c3_reference": {"label": "not_material"}}

    def decide(candidate="F2", *, screening="improved", tests=f2, stages=quiet, sites=clean, runs=16,
               comparable=True):
        return fragility.decide(candidate, static_screening=screening, tests=tests, stages=stages,
                                failure_sites=sites, runs=runs, comparable=comparable)

    decided = decide()
    assert decided["label"] == "fragility_reduced" and decided["conditions"]["calibration_no_regression_detected"]
    # No calibration regression must hold against every declared parent arm (the conjunction).
    one_worse = {**quiet, "c3_reference": {"label": "worse"}}
    regressed = decide(stages=one_worse)
    assert regressed["label"] == "fragility_reduced_calibration_regressed"
    assert regressed["conditions"]["calibration_regressed_against"] == ["c3_reference"]
    assert decide("F3", tests=f3)["label"] == "not_reduced"
    gated = decide(screening="dynamic_hard")
    assert gated["label"] == "diagnostic_only"
    assert gated["conditions"]["label_if_static_gate_had_passed"] == "fragility_reduced"
    assert decide(sites=moved)["label"] == "new_failure_site"
    assert decide(stages={**quiet, "negative_control": {"label": "unhealthy"}})["label"] == "unhealthy"
    assert decide(runs=15)["label"] == "blocked"
    # A request that fails the comparability check blocks the candidate (pre-registered rule 1), gate or not.
    assert decide(comparable=False)["label"] == "blocked"
    assert decide(comparable=False, screening="g0_rejected")["label"] == "blocked"
    assert decide(stages={**quiet, "c3_reference": None})["label"] == "blocked"
    assert decide(stages={**quiet, "c3_reference": {"label": "blocked"}})["label"] == "blocked"
    assert decide(stages={})["label"] == "blocked"
    assert decide(tests=None, sites=None)["label"] == "blocked"


def test_select_runs_uses_spares_only_for_crashes_within_the_pool():
    keys = [(f"k{index}", f"f{index}") for index in (1, 2, 3)]
    spares = [("k4", "f4"), ("k5", "f5")]

    def row(run_id, fingerprint, outcome):
        return {"id": run_id, "key_sha256": fingerprint, "outcome": outcome}

    full = fragility.select_runs(keys, spares, [row("a", "f1", "scored"), row("b", "f2", "scored"),
                                                row("c", "f3", "scored")], pool=3)
    assert full["complete"] and [entry["id"] for entry in full["used"]] == ["a", "b", "c"] and not full["spares_used"]
    crashed = [row("a", "f1", "scored"), row("b", "f2", "crashed"), row("c", "f3", "scored"), row("d", "f4", "crashed"),
               row("e", "f5", "scored")]
    replaced = fragility.select_runs(keys, spares, crashed, pool=3)
    assert replaced["complete"] and [entry["label"] for entry in replaced["used"]] == ["k1", "k5", "k3"]
    assert replaced["spares_used"] == ["k4", "k5"] and replaced["extra_runs"] == []
    short = fragility.select_runs(keys, spares, crashed, pool=1)
    assert not short["complete"] and "no spare is left" in short["stopped"]
    unscored = fragility.select_runs(keys, spares, [row("a", "f1", "scored"), row("b", "f2", "unscored")], pool=3)
    assert not unscored["complete"] and "unscored" in unscored["stopped"]
    extra = fragility.select_runs(keys, spares, [row("a", "f1", "scored"), row("b", "f2", "scored"),
                                                 row("c", "f3", "scored"), row("x", "f4", "scored"),
                                                 row("y", "f9", "scored")], pool=3)
    assert extra["extra_runs"] == ["x", "y"]


def test_rare_events_report_counts_against_the_reference_rate():
    reference = [_run(0, 0, gridlock="S01")] + [_run(0, 0) for _ in range(45)]
    found = fragility.rare_events([_run(0, 0, gridlock="S01")] + [_run(0, 0) for _ in range(15)], reference)
    gridlock = found["events"]["gridlock"]
    assert gridlock["candidate_runs"] == 1 and gridlock["reference_runs"] == 1
    assert gridlock["tail_at_reference_rate"] == round(1 - (45 / 46) ** 16, 6)
    assert found["events"]["collision"]["tail_at_reference_rate"] == 1.0


def test_stage_arms_merges_two_ledgers_and_refuses_other_goals(monkeypatch, tmp_path):
    from astra_sumo.twin import panel

    def read(path, labels):
        goal = "g2" if "other" in str(path) else "g"
        return {"draws": {label: {"run": {"id": run}} for label, run in labels.items()}, "real": {"r": 1},
                "provenance": {"goal": goal, "goal_sha256": goal}}

    monkeypatch.setattr(panel, "read_stage_draws", read)
    arms = fragility.stage_arms((tmp_path / "f", {"k1": "F2/k1"}), (tmp_path / "p", {"c3-n-01": "c3-negative-01"}))
    assert arms["candidate"] == {"k1": {"run": {"id": "F2/k1"}}} and set(arms["reference"]) == {"c3-n-01"}
    with pytest.raises(ValueError, match="different goals"):
        fragility.stage_arms((tmp_path / "f", {"k1": "F2/k1"}), (tmp_path / "other", {"x": "y"}))


# ---- the analysis entry point on two synthetic ledgers -------------------------------------------------------------

def _request(net: bytes, control: bytes, key: str, *, seed: int = 104, candidate: bool = False) -> dict:
    sha = {name: hashlib.sha256(data).hexdigest() for name, data in (("net", net), ("control", control))}
    request = {"arguments": {"seed": seed}, "scenario_op": None, "perturbation": {"family": "depart_jitter",
                                                                                "key_sha256": key},
               "inputs_sha256": {"net_file": sha["net"], "signal_program_file": sha["control"], "route_file": f"d{key}"},
               "runtime": {"verifier_commit": "c1" if candidate else "c0"}}
    if candidate:
        request["candidate"] = {
            "net_file": {"parent_sha256": hashlib.sha256(NET).hexdigest(), "candidate_sha256": sha["net"]},
            "signal_program_file": {"parent_sha256": hashlib.sha256(CONTROL).hexdigest(),
                                    "candidate_sha256": sha["control"]}}
    return request


def _ledger_run(store: Path, run_id: str, request: dict, native: Path, *, candidate: str | None = None) -> dict:
    from astra_sumo.twin import runner

    run_dir = store / "runs" / run_id.replace("/", "-")
    run_dir.mkdir(parents=True)
    (run_dir / "request.json").write_text(json.dumps(request), encoding="utf-8")
    shutil.copytree(native, run_dir / "native")
    row = {"id": run_id, "request_sha256": runner.digest(request), "run_dir": str(run_dir), "status": "measured",
           "healthy": True, "tae_main": 1.0, "seed": request["arguments"]["seed"], "scenario_op": None,
           "draw_key": {"family": "depart_jitter", "key_sha256": request["perturbation"]["key_sha256"],
                        "public": True}, **({"candidate": {"id": candidate}} if candidate else {})}
    with (store / "runs.jsonl").open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(row) + "\n")
    return row


def _analysis_stores(tmp_path: Path, *, extra_key: str | None = None, seed: int = 104) -> tuple[dict, list]:
    from astra_sumo.twin import perturb

    (tmp_path / "source").mkdir(parents=True)
    source = _native(tmp_path / "source")
    candidate_native = tmp_path / "candidate-native"
    shutil.copytree(source, candidate_native)
    candidate_net = NET + b"<!-- candidate -->\n"
    (candidate_native / "network.net.xml").write_bytes(candidate_net)
    (candidate_native / "signal-programs.add.xml").write_bytes(CANDIDATE_CONTROL)
    reference_store, candidate_store = tmp_path / "pilot", tmp_path / "fragility"
    reference = [_ledger_run(reference_store, f"r{index}", _request(NET, CONTROL, f"{index:064x}"), source)
                 for index in (1, 2, 3)]
    # A run of the parent on another seed is not a reference draw.
    _ledger_run(reference_store, "other-seed", _request(NET, CONTROL, f"{9:064x}", seed=11), source)
    labels = ["fragility/F2/k1", "fragility/F2/k2", *([extra_key] if extra_key else [])]
    for position, label in enumerate(labels):
        key = perturb.DrawKey.public_key(label).fingerprint
        _ledger_run(candidate_store, label, _request(candidate_net, CANDIDATE_CONTROL, key, candidate=True,
                                                     seed=seed if position == 0 else 104),
                    candidate_native, candidate="F2")
    manifest = candidate_store / "candidates" / "F2" / "candidate.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(json.dumps({
        "id": "F2", "net": {"path": "network.net.xml", "sha256": hashlib.sha256(candidate_net).hexdigest()},
        "control": {"path": "control.add.xml", "sha256": hashlib.sha256(CANDIDATE_CONTROL).hexdigest()},
        "parent": {"net_sha256": hashlib.sha256(NET).hexdigest(),
                   "control_sha256": hashlib.sha256(CONTROL).hexdigest()}}), encoding="utf-8")
    design = {"analysis": {
        "parent": {"net_sha256": hashlib.sha256(NET).hexdigest(), "control_sha256": hashlib.sha256(CONTROL).hexdigest(),
                   "family": "depart_jitter", "seed": 104},
        "draws": 2, "reference_ledger": str(reference_store / "runs.jsonl"),
        "candidate_ledger": str(candidate_store / "runs.jsonl"),
        "reference_arm": [{"id": row["id"], "request_sha256": row["request_sha256"]} for row in reference],
        "ni_arms": {"a_negative_control": ["r1", "r2"], "b_c3_reference": ["r2", "r3"]}, "spare_pool": 1,
        "order": ["F2"],
        "candidates": {"F2": {"manifest": {"path": str(manifest),
                                           "sha256": hashlib.sha256(manifest.read_bytes()).hexdigest()},
                              "static_screening": "improved", "keys": labels[:2],
                              "spare_keys": ["fragility/F2/k3"]}}}}
    return design, reference


def _fake_stage(calls: list):
    def stage(candidate_ledger, candidate_labels, reference_ledger, reference_labels, *, draws):
        calls.append((dict(candidate_labels), sorted(reference_labels), draws))
        return {"label": "not_significant", "status": "measured", "first_failure": None, "gate_sha256": "g",
                "provenance": {}, "verdict": {"label": "not_significant"}}
    return stage


def test_analyze_composes_the_registered_rule_from_both_ledgers(tmp_path, monkeypatch):
    calls: list = []
    monkeypatch.setattr(fragility, "ni_stage", _fake_stage(calls))
    design, _ = _analysis_stores(tmp_path)
    result = fragility.analyze(design)
    row = result["candidates"]["F2"]
    assert result["reference_arm"]["ids"] == ["r1", "r2", "r3"] and result["spare_pool"] == {"size": 1, "left": 1}
    assert [entry["label"] for entry in row["selection"]["used"]] == ["fragility/F2/k1", "fragility/F2/k2"]
    assert row["comparability"]["comparable"] and row["comparability"]["problems"] == []
    assert "perturbation.key_sha256" in row["comparability"]["differing_union"]
    assert sorted(call[1] for call in calls) == [["r1", "r2"], ["r2", "r3"]] and all(call[2] == 2 for call in calls)
    assert row["decision"]["label"] == "not_reduced" and row["decision"]["conditions"]["comparable"]
    assert row["tests"]["tests"]["S01_wrong_lane_episodes"]["rank"]["n_reference"] == 3
    assert row["rare_events"]["events"]["collision"]["candidate_runs"] == 2


def test_analyze_blocks_an_incomparable_request_or_an_extra_run(tmp_path, monkeypatch):
    monkeypatch.setattr(fragility, "ni_stage", _fake_stage([]))
    design, _ = _analysis_stores(tmp_path / "seed", seed=11)
    row = fragility.analyze(design)["candidates"]["F2"]
    assert not row["comparability"]["comparable"] and "arguments.seed" in row["comparability"]["differing_union"]
    assert row["decision"]["label"] == "blocked"
    design, _ = _analysis_stores(tmp_path / "extra", extra_key="fragility/F2/k9")
    row = fragility.analyze(design)["candidates"]["F2"]
    assert row["selection"]["extra_runs"] == ["fragility/F2/k9"] and row["decision"]["label"] == "blocked"
    design, reference = _analysis_stores(tmp_path / "frozen")
    design["analysis"]["reference_arm"] = design["analysis"]["reference_arm"][:2]
    with pytest.raises(ValueError, match="differs from the frozen reference arm"):
        fragility.analyze(design)


def test_the_analysis_cli_blocks_every_candidate_before_any_run(tmp_path):
    import subprocess
    import sys

    root = Path(__file__).resolve().parents[1]
    design, _ = _analysis_stores(tmp_path)
    Path(design["analysis"]["candidate_ledger"]).unlink()  # no candidate run yet
    path = tmp_path / "design.json"
    path.write_text(json.dumps(design), encoding="utf-8")
    out = tmp_path / "analysis.json"
    command = [sys.executable, "-B", str(root / "plugins/astra-sumo/scripts/astra_twin.py"), "fragility-analyze",
               "--design", str(path), "--out", str(out), "--json"]
    done = subprocess.run(command, capture_output=True, text=True, timeout=600)
    assert done.returncode == 0, done.stdout + done.stderr
    assert json.loads(done.stdout)["labels"] == {"F2": "blocked"}
    written = json.loads(out.read_text(encoding="utf-8"))
    assert written["design"]["sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
    assert written["candidates"]["F2"]["selection"]["stopped"] == "fragility/F2/k1: no run"
    assert subprocess.run(command, capture_output=True, text=True, timeout=600).returncode != 0  # never overwritten


def test_request_differences_allow_only_the_candidate_files_the_key_and_the_commit():
    reference = {"arguments": {"seed": 104}, "inputs_sha256": {"net_file": "n", "route_file": "d1",
                                                               "signal_program_file": "c", "e1_additional_file": "e"},
                 "perturbation": {"family": "depart_jitter", "key_sha256": "k1"},
                 "runtime": {"verifier_commit": "a", "sumo_binary_sha256": "s"}, "scenario_op": None}
    candidate = {**reference, "inputs_sha256": {**reference["inputs_sha256"], "net_file": "n2", "route_file": "d2",
                                                "signal_program_file": "c2"},
                 "perturbation": {"family": "depart_jitter", "key_sha256": "k2"},
                 "runtime": {**reference["runtime"], "verifier_commit": "b"}, "candidate": {"net_file": {}}}
    assert fragility.request_differences(candidate, reference)["comparable"]
    other = {**candidate, "runtime": {**candidate["runtime"], "sumo_binary_sha256": "t"}, "arguments": {"seed": 11}}
    found = fragility.request_differences(other, reference)
    assert not found["comparable"] and {"arguments.seed", "runtime.sumo_binary_sha256"} <= set(found["differing"])
