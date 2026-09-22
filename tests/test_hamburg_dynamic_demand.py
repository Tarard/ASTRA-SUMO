import numpy as np

from torii_sumo.core.hamburg_dynamic_demand import (
    build_dynamic_assignment_evidence,
    solve_joint_regularized_profiles,
    solve_regularized_departure_profile,
)


def test_dynamic_assignment_uses_scheduled_departure_and_edge_exit_time(tmp_path) -> None:
    demand = tmp_path / "demand.rou.xml"
    demand.write_text(
        '<routes><vehicle id="v" depart="10"><route edges="a measured b"/></vehicle></routes>',
        encoding="utf-8",
    )
    vehroute = tmp_path / "vehroute.xml"
    vehroute.write_text(
        '<routes><vehicle id="v" depart="12" arrival="110">'
        '<route edges="a measured b" exitTimes="20 100 110"/></vehicle></routes>',
        encoding="utf-8",
    )

    result = build_dynamic_assignment_evidence(
        demand,
        vehroute,
        measured_edge="measured",
        begin=0,
        end=1800,
        bin_seconds=900,
    )

    assert result.assignment.tolist() == [[1.0, 0.0], [0.0, 0.0]]
    assert result.departure_counts.tolist() == [1.0, 0.0]


def test_dynamic_departure_solver_compensates_one_bin_propagation_delay() -> None:
    assignment = np.array(
        [
            [0.0, 0.0, 0.0],
            [1.0, 0.0, 0.0],
            [0.0, 1.0, 0.0],
        ]
    )
    target = np.array([0.0, 10.0, 20.0])
    prior = np.array([10.0, 20.0, 0.0])

    result = solve_regularized_departure_profile(
        assignment,
        target,
        prior,
        prior_weight=0.01,
        smoothness_weight=0.0,
    )

    assert result.tolist() == [10, 20, 0]
    assert result.sum() == 30


def test_joint_solver_preserves_each_detector_group_total() -> None:
    assignment = np.array(
        [
            [0.0, 0.0, 0.0, 0.0],
            [1.0, 0.0, 0.0, 0.0],
            [0.0, 0.0, 0.0, 0.0],
            [0.0, 0.0, 1.0, 0.0],
        ]
    )
    target = np.array([0.0, 10.0, 0.0, 20.0])
    prior = np.array([10.0, 0.0, 20.0, 0.0])

    result = solve_joint_regularized_profiles(
        assignment,
        target,
        prior,
        group_count=2,
        bins_per_group=2,
        prior_weight=0.01,
        smoothness_weight=0.0,
    )

    assert result.tolist() == [10, 0, 20, 0]
    assert result[:2].sum() == 10
    assert result[2:].sum() == 20
