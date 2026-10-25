from fractions import Fraction

import numpy as np

from astra_sumo.core.hamburg_capacity_screen import nominal_capacity, rational_service_upper_bound


def test_nominal_capacity_is_one_lane_and_keeps_transition_time_out_of_green():
    assert nominal_capacity("29", "90", Fraction(4, 5)) == 116
    assert nominal_capacity("5", "90", Fraction(4, 5)) == 20
    assert nominal_capacity("90", "90", Fraction(1)) == 450


def test_rational_service_bound_is_verified_after_rounding_the_lp_dual():
    matrix = np.array([[1, 1], [1, 0], [0, 1]])
    result = rational_service_upper_bound(matrix, np.array([3, 10, 10]), [-0.9999999999, 0, 0])
    assert result["integer_service_upper_bound"] == 3
    assert result["all_column_inequalities_verified"]
