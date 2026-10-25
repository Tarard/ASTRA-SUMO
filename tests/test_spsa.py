import numpy as np
import pytest
from pathlib import Path
import importlib.util
import os
import subprocess
import xml.etree.ElementTree as ET

from astra_sumo.core.detector_demand_replay import run_point_detector_demand
from astra_sumo.core.spsa import calibrate_spsa


def test_two_calls_per_update_and_recovery_of_known_coupled_flows():
    # The two OD flows affect overlapping sensors. The solution is known independently.
    assignment = np.array([[1., 1.], [0., 1.], [1., 0.]])
    measured = assignment @ np.array([30., 70.])
    calls = []

    def loss(theta, name):
        calls.append(name)
        return np.sum((assignment @ theta - measured) ** 2)

    settings = dict(lower=[0, 0], upper=[200, 200], a=.15, A=10, c=2,
                    alpha=.602, gamma=.101, iterations=400, seed=104)
    result = calibrate_spsa(loss, [60, 40], **settings)
    assert len(calls) == result["loss_evaluations"] == 800
    assert np.max(np.abs(np.array(result["theta"]) - [30, 70])) < .05
    assert not result["final_vector_remeasured"]
    repeated = calibrate_spsa(loss, [60, 40], **settings)
    assert repeated["history"] == result["history"]


def test_projected_perturbations_keep_the_published_denominator():
    calls, saved = [], []

    def loss(theta, name):
        calls.append(theta.copy())
        return (theta[0] - 2) ** 2

    result = calibrate_spsa(loss, [0], lower=[0], upper=[1], a=1, A=0, c=2,
                            alpha=.602, gamma=.101, iterations=1, seed=104, on_iteration=saved.append)
    row = result["history"][0]
    assert all(0 <= theta[0] <= 1 for theta in calls)
    assert row["gradient"][0] == (row["loss_plus"] - row["loss_minus"]) / (4 * row["delta"][0])
    assert row["next_theta"] == [min(1, max(0, -row["gradient"][0]))]
    assert saved == result["history"]


@pytest.mark.parametrize("stability", [0, 3])
def test_initial_move_gain_uses_the_first_pair_without_extra_evaluations(stability):
    calls = []

    def loss(theta, name):
        calls.append(name)
        return float(np.sum((theta - [30, 65]) ** 2))

    result = calibrate_spsa(loss, [60, 40], lower=[0, 0], upper=[200, 200], a=None,
                            initial_step=1, A=stability, c=2, alpha=.602, gamma=.101, iterations=1, seed=104)
    assert len(calls) == result["loss_evaluations"] == 2
    row = result["history"][0]
    assert np.allclose(np.abs(np.array(row["next_theta"]) - row["theta"]), 1)
    assert result["a"] == pytest.approx((stability + 1) ** .602 / max(map(abs, row["gradient"])))
    with pytest.raises(ValueError, match="first SPSA gradient is zero"):
        calibrate_spsa(lambda theta, name: 1, [5], lower=[0], upper=[20], a=None,
                       initial_step=1, A=3, c=2, alpha=.602, gamma=.101, iterations=1, seed=104)


def test_failed_loss_and_invalid_initial_vector_are_not_zero():
    settings = dict(lower=[0], upper=[np.inf], a=.1, A=1, c=1,
                    alpha=.602, gamma=.101, iterations=1, seed=104)
    with pytest.raises(ValueError, match="finite"):
        calibrate_spsa(lambda theta, name: float("nan"), [1], **settings)
    with pytest.raises(ValueError, match="initial"):
        calibrate_spsa(lambda theta, name: 0, [-1], **settings)


@pytest.mark.parametrize("automatic_gain", [False, True])
def test_pair_callback_preserves_serial_rng_clipping_losses_history_and_theta(automatic_gain):
    assignment = np.array([[1., 2., 0.], [0., 1., 1.], [2., 0., 1.]])
    target = np.array([18., 9., 12.])
    calls = {"serial": [], "paired": []}

    def loss(theta, name, *, mode):
        calls[mode].append((theta.tolist(), name))
        value = float(np.sum((assignment @ theta - target) ** 2))
        theta[:] = -999  # Both interfaces must isolate the algorithm from callback mutation.
        return value

    def pair(plus, minus):
        return loss(*plus, mode="paired"), loss(*minus, mode="paired")

    settings = dict(lower=[0, 0, 0], upper=[12, 12, 12], a=None if automatic_gain else .1,
                    initial_step=.7 if automatic_gain else None, A=3, c=3,
                    alpha=.602, gamma=.101, iterations=5, seed=104)
    saved_serial, saved_pair = [], []
    serial = calibrate_spsa(lambda theta, name: loss(theta, name, mode="serial"), [0, 12, 5],
                            on_iteration=saved_serial.append, **settings)
    paired = calibrate_spsa(lambda *args: pytest.fail("Pair evaluation must replace objective calls."),
                            [0, 12, 5], evaluate_pair=pair, on_iteration=saved_pair.append, **settings)
    assert paired == serial and saved_pair == saved_serial == serial["history"]
    assert calls["paired"] == calls["serial"] and len(calls["paired"]) == paired["loss_evaluations"] == 10
    assert calls["paired"][0][1:] == ("iteration-0001-plus",)
    assert calls["paired"][1][1:] == ("iteration-0001-minus",)


def test_pair_callback_automatic_gain_uses_its_actual_first_two_losses():
    calls = []

    def pair(plus, minus):
        calls.append((plus[1], minus[1]))
        return 1000., 916.

    result = calibrate_spsa(lambda *args: pytest.fail("No serial loss call is allowed."), [5, 6],
        lower=[0, 0], upper=[20, 20], a=None, initial_step=.75, A=3, c=2,
        alpha=.602, gamma=.101, iterations=1, seed=104, evaluate_pair=pair)
    row = result["history"][0]
    assert (row["loss_plus"], row["loss_minus"]) == (1000, 916)
    assert max(map(abs, row["gradient"])) == 21
    assert row["a"] == pytest.approx(.75 * 4 ** .602 / 21)
    assert np.allclose(np.abs(np.asarray(row["next_theta"]) - row["theta"]), .75)
    assert len(calls) == 1 and result["loss_evaluations"] == 2


@pytest.mark.parametrize("returned", [None, [], [1], [1, 2, 3], 1.0])
def test_pair_callback_rejects_wrong_return_length_without_update(returned):
    calls, updates = [], []

    def pair(plus, minus):
        calls.append((plus[1], minus[1]))
        return returned

    with pytest.raises(ValueError, match="exactly two losses"):
        calibrate_spsa(lambda *args: pytest.fail("No serial fallback is allowed."), [5],
            lower=[0], upper=[20], a=.1, A=0, c=1, alpha=.602, gamma=.101,
            iterations=3, seed=104, evaluate_pair=pair, on_iteration=updates.append)
    assert len(calls) == 1 and updates == []


@pytest.mark.parametrize("losses", [(float("nan"), 1), (1, float("inf")), (-float("inf"), 1)])
def test_pair_callback_rejects_nonfinite_loss_without_update(losses):
    updates = []
    with pytest.raises(ValueError, match="losses must be finite"):
        calibrate_spsa(lambda *args: pytest.fail("No serial fallback is allowed."), [5],
            lower=[0], upper=[20], a=.1, A=0, c=1, alpha=.602, gamma=.101,
            iterations=3, seed=104, evaluate_pair=lambda *args: losses, on_iteration=updates.append)
    assert updates == []


def test_pair_callback_failure_after_completed_update_does_not_continue():
    calls, updates = [], []
    initial = np.array([5.])

    def pair(plus, minus):
        calls.append((plus[1], minus[1]))
        if len(calls) == 2:
            raise RuntimeError("native pair failed; preserve both outputs")
        return 2., 1.

    with pytest.raises(RuntimeError, match="native pair failed"):
        calibrate_spsa(lambda *args: pytest.fail("No serial fallback is allowed."), initial,
            lower=[0], upper=[20], a=.1, A=0, c=1, alpha=.602, gamma=.101,
            iterations=3, seed=104, evaluate_pair=pair, on_iteration=updates.append)
    assert len(calls) == 2 and len(updates) == 1
    assert calls[-1] == ("iteration-0002-plus", "iteration-0002-minus")
    assert initial.tolist() == [5.]


def test_pair_callback_zero_first_gradient_preserves_gain_failure():
    from astra_sumo.core.spsa import InitialSPSAGainUnavailable

    updates = []
    with pytest.raises(InitialSPSAGainUnavailable, match="first SPSA gradient is zero"):
        calibrate_spsa(lambda *args: pytest.fail("No serial fallback is allowed."), [5],
            lower=[0], upper=[20], a=None, initial_step=1, A=3, c=1, alpha=.602, gamma=.101,
            iterations=3, seed=104, evaluate_pair=lambda *args: (3., 3.), on_iteration=updates.append)
    assert updates == []


@pytest.mark.skipif(importlib.util.find_spec("sumo") is None, reason="Repository SUMO runtime required")
def test_spsa_loss_from_native_e1_and_zero_od_demand(tmp_path):
    import sumo
    from importlib.metadata import version

    assert version("eclipse-sumo") == version("traci") == version("sumolib") == "1.27.1"
    binaries = Path(sumo.__file__).parent / "bin"
    suffix = ".exe" if os.name == "nt" else ""
    netconvert, binary = binaries / ("netconvert" + suffix), binaries / ("sumo" + suffix)
    nodes, edges, net = [tmp_path / n for n in ("nodes.xml", "edges.xml", "net.xml")]
    nodes.write_text('<nodes><node id="a" x="0" y="0"/><node id="b" x="100" y="0"/><node id="c" x="200" y="0"/></nodes>')
    edges.write_text('<edges><edge id="in" from="a" to="b"/><edge id="out" from="b" to="c"/></edges>')
    subprocess.run([str(netconvert), "-n", str(nodes), "-e", str(edges), "-o", str(net)],
                   capture_output=True, check=True, timeout=30)
    loops, expected = tmp_path / "loops.xml", tmp_path / "expected.csv"
    loops.write_text('<additional><inductionLoop id="first" lane="in_0" pos="20" period="60" file="unused.xml"/><inductionLoop id="second" lane="out_0" pos="20" period="60" file="unused.xml"/></additional>')
    expected.write_text("detector_id,edge_id,begin,end,expected_total\nfirst,in,0,60,10\nsecond,out,0,60,10\n")
    evaluated = []

    def native_loss(theta, name):
        demand = ET.Element("routes")
        for i in range(int(np.floor(theta[0] + .5))):
            vehicle = ET.SubElement(demand, "vehicle", id=f"v{i}", depart=str(i * .6))
            ET.SubElement(vehicle, "route", edges="in out")
        route = tmp_path / f"{name}.rou.xml"
        ET.ElementTree(demand).write(route, encoding="utf-8")
        result = run_point_detector_demand(net_file=net, route_file=route, e1_additional_file=loops,
            expected_counts_csv=expected, output_dir=tmp_path / name, end_s=60, sumo_binary=str(binary))
        assert result["healthy"] and result["observer_complete"]
        assert result["stall_monitor"]["native_version"] == [22, "SUMO 1.27.1"]
        periodic = ET.parse(Path(tmp_path / name / "field-counts.xml")).getroot().findall("interval")
        counts = [int(row.get("nVehContrib")) for row in periodic]
        assert counts == [int(np.floor(theta[0] + .5))] * 2
        evaluated.append(result)
        return sum((n - 10) ** 2 for n in counts)

    result = calibrate_spsa(native_loss, [9], lower=[0], upper=[100], a=None, initial_step=1, A=0, c=2,
                            alpha=.602, gamma=.101, iterations=1, seed=104)
    assert result["loss_evaluations"] == 2 and len(evaluated) == 2
    assert native_loss(result["theta"], "final") == 0
    assert evaluated[-1]["exact_count_fit"]
    assert native_loss([0], "zero") == 200
    assert not evaluated[-1]["exact_count_fit"]
