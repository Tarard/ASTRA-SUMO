import math

import pytest

from torii_sumo.road_network.rigid_vehicle_sweep import rigid_vehicle_sweep


CAR = dict(length_m=4.5, width_m=1.8, wheelbase_m=2.7, front_overhang_m=0.8, rear_overhang_m=1.0)


def test_straight_front_bumper_path_has_correct_axle_and_complete_body():
    result = rigid_vehicle_sweep([(5, 0), (15, 0)], CAR, initial_heading_rad=0)
    last = result['poses'][-1]
    assert last['rear_axle_xy'] == pytest.approx([11.5, 0])
    assert min(p[0] for p in last['body_polygon']) == pytest.approx(10.5)
    assert max(p[0] for p in last['body_polygon']) == pytest.approx(15)
    assert result['maximum_required_steering_deg'] == pytest.approx(0)


def test_circular_front_path_matches_steady_rigid_bicycle_geometry():
    radius = 15.0
    distance = CAR['wheelbase_m'] + CAR['front_overhang_m']
    alpha = math.asin(distance / radius)
    points = [(radius * math.cos(t), radius * math.sin(t)) for t in [i * 0.002 for i in range(501)]]
    result = rigid_vehicle_sweep(points, CAR, initial_heading_rad=math.pi / 2 - alpha)
    rear = result['poses'][-1]['rear_axle_xy']
    assert math.hypot(*rear) == pytest.approx(math.sqrt(radius**2 - distance**2), abs=0.03)
    assert result['maximum_required_steering_deg'] > 0
    assert result['steering_feasibility'] == 'not_verified'


def test_vehicle_corners_detect_an_intrusion_that_centerline_misses():
    road = [(0, -1), (30, -1), (30, 1), (0, 1)]
    wide_vehicle = {**CAR, 'width_m': 2.4}
    result = rigid_vehicle_sweep([(5, 0), (15, 0)], wide_vehicle, initial_heading_rad=0, allowed_polygon=road)
    assert result['space_check'] == 'outside_declared_space'
    assert result['violating_cell_count'] > 0
    assert result['first_violation']['outside_area_m2'] > 0
    assert rigid_vehicle_sweep([(5, 0), (15, 0)], CAR, initial_heading_rad=0, allowed_polygon=road)['space_check'] == 'inside_declared_space'


def test_forbidden_island_and_vehicle_dimension_mismatch_are_not_ignored():
    road = [(0, -5), (30, -5), (30, 5), (0, 5)]
    island = [(9, 0.5), (10, 0.5), (10, 1.5), (9, 1.5)]
    result = rigid_vehicle_sweep([(5, 0), (15, 0)], CAR, initial_heading_rad=0,
                                allowed_polygon=road, forbidden_polygons=[island])
    assert result['space_check'] == 'outside_declared_space'
    with pytest.raises(ValueError):
        rigid_vehicle_sweep([(5, 0), (15, 0)], {**CAR, 'length_m': 10}, initial_heading_rad=0)


def test_complete_sweep_catches_an_island_between_sparse_endpoint_poses():
    island = [(7, -0.1), (8, -0.1), (8, 0.1), (7, 0.1)]
    result = rigid_vehicle_sweep([(5, 0), (15, 0)], CAR, initial_heading_rad=0,
                                forbidden_polygons=[island], max_step_m=20, max_heading_step_deg=360)
    assert result['cell_count'] == 1
    assert result['violating_cell_count'] == 1


def test_cell_contains_intermediate_rotating_body_from_independent_rk4_integration():
    result = rigid_vehicle_sweep([(0, 0), (2, 0)], CAR, initial_heading_rad=0.8,
                                max_step_m=3, max_heading_step_deg=90)
    assert result['cell_count'] == 1
    cell = result['swept_cells'][0]
    theta, ds = 0.8, 0.01
    distance = CAR['wheelbase_m'] + CAR['front_overhang_m']
    def derivative(angle):
        return -math.sin(angle) / distance
    for step in range(201):
        x = step * ds
        for longitudinal in (0, -CAR['length_m']):
            for lateral in (-CAR['width_m'] / 2, CAR['width_m'] / 2):
                point = (x + longitudinal * math.cos(theta) - lateral * math.sin(theta),
                         longitudinal * math.sin(theta) + lateral * math.cos(theta))
                assert all((b[0] - a[0]) * (point[1] - a[1]) - (b[1] - a[1]) * (point[0] - a[0]) >= -1e-9
                           for a, b in zip(cell, cell[1:] + cell[:1]))
        k1 = derivative(theta)
        k2 = derivative(theta + ds * k1 / 2)
        k3 = derivative(theta + ds * k2 / 2)
        k4 = derivative(theta + ds * k3)
        theta += ds * (k1 + 2*k2 + 2*k3 + k4) / 6


def test_large_finite_heading_is_equivalent_to_its_trigonometric_orientation():
    angle = 1e20
    normalized = math.atan2(math.sin(angle), math.cos(angle))
    direction = normalized + 0.4
    path = [(0, 0), (2 * math.cos(direction), 2 * math.sin(direction))]
    large = rigid_vehicle_sweep(path, CAR, initial_heading_rad=angle)
    ordinary = rigid_vehicle_sweep(path, CAR, initial_heading_rad=normalized)
    assert large['poses'][-1]['rear_axle_xy'] == pytest.approx(ordinary['poses'][-1]['rear_axle_xy'], abs=1e-9)


@pytest.mark.parametrize('changes', [dict(max_step_m=5e-324), dict(max_heading_step_deg=5e-324),
                                    dict(initial_heading_rad=10**400)])
def test_unrepresentable_sampling_or_numbers_raise_explicit_value_errors(changes):
    with pytest.raises(ValueError):
        rigid_vehicle_sweep([(0, 0), (10, 0)], CAR, **(dict(initial_heading_rad=0) | changes))


@pytest.mark.parametrize('path,vehicle', [([(0, 0), None], CAR), ([(0, 0), (1, 0)], None),
                                       ([(0, 0), (1, 0)], {}),
                                       ([(0, 0), (10**400, 0)], CAR),
                                       ([(0, 0), (1, 0)], {**CAR, 'width_m': 1e308})])
def test_invalid_path_vehicle_and_overflowing_body_geometry_are_rejected(path, vehicle):
    with pytest.raises(ValueError):
        rigid_vehicle_sweep(path, vehicle, initial_heading_rad=0)


def test_numpy_coordinate_pairs_keep_the_existing_path_contract():
    import numpy as np
    result = rigid_vehicle_sweep(np.array([(5.0, 0.0), (6.0, 0.0)]), CAR, initial_heading_rad=0)
    assert result['poses'][-1]['front_bumper_xy'] == pytest.approx([6, 0])


def test_finite_sampling_request_is_bounded_before_building_a_large_result():
    with pytest.raises(ValueError, match='100,000'):
        rigid_vehicle_sweep([(0, 0), (10, 0)], CAR, initial_heading_rad=0, max_step_m=1e-8)
