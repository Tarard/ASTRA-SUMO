import numpy as np
import pytest

from astra_sumo.core.od_assignment_feedback import propose_bounded_od_assignment


def problem():
    return dict(contributions=np.diag([5, 5, 3, 3]), prior=[5, 5, 3, 3],
                observations=[3, 7, 2, 4], od_groups=[0, 0, 1, 1], lower=[0] * 4,
                upper=[10] * 4, exact_rows=[False, False, True, True], change_budget=4)


def test_budget_and_each_od_total_survive_joint_integer_fit():
    result = propose_bounded_od_assignment(**problem())
    assert result["status"] == "candidate" and not result["formal_result"]
    assert result["solution"] == [4, 6, 2, 4]
    assert result["od_group_totals"] == [10, 6]
    assert result["l1_change"] == 4 and result["predicted_TAE"] == 2
    assert result["prior_TAE"] == 6


def test_frozen_cells_cannot_supply_other_od_changes():
    inputs = problem()
    inputs.update(lower=[5, 5, 0, 0], upper=[5, 5, 6, 6], change_budget=10)
    result = propose_bounded_od_assignment(**inputs)
    assert result["solution"] == [5, 5, 2, 4]
    assert result["l1_change"] == 2


def test_fractional_assignment_is_not_rounded_to_integer_response():
    result = propose_bounded_od_assignment(contributions=[[3, 0, 0], [1, 2, 2]],
        prior=[4, 2, 2], observations=[4, 4], od_groups=[0, 0, 0], lower=[2, 0, 0],
        upper=[6, 4, 4], exact_rows=[False, False], change_budget=4)
    assert result["solution"][0] == 5
    assert result["predicted_counts"] == [3.75, 4.25]
    assert result["predicted_TAE"] == .5 and result["l1_change"] == 2


def test_impossible_protected_fit_is_not_silently_relaxed():
    inputs = problem()
    inputs["change_budget"] = 0
    result = propose_bounded_od_assignment(**inputs)
    assert result["status"] == "blocked" and result["solution"] is None


def test_rejects_total_that_float_group_equality_cannot_preserve():
    # Both elements are representable, but their sum loses the final vehicle.
    with pytest.raises(ValueError, match="total prior"):
        propose_bounded_od_assignment(contributions=[[0, 1]], prior=[2**53, 1],
            observations=[0], od_groups=[0, 0], lower=[2**53, 0], upper=[2**53, 1],
            exact_rows=[False], change_budget=2)


@pytest.mark.parametrize("changes", [
    {"prior": [0, 5, 3, 3]},
    {"contributions": [[1.5] * 4] * 4},
    {"observations": [3, 7, 2]},
    {"exact_rows": [0, 0, 1, 1]},
    {"lower": [6, 0, 0, 0]},
    {"upper": [4, 10, 10, 10]},
    {"od_groups": [0, 0, 1]},
    {"change_budget": -1},
    {"change_budget": True},
    {"time_limit_seconds": float("nan")},
])
def test_invalid_observed_support_and_constraints_fail(changes):
    inputs = problem()
    inputs.update(changes)
    with pytest.raises(ValueError):
        propose_bounded_od_assignment(**inputs)
