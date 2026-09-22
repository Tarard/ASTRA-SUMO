from torii_sumo.road_network.vehicle_checked_connection import select_vehicle_checked_connection


def test_guide_selection_uses_full_vehicle_and_keeps_failed_space_check_unresolved():
    vehicle = dict(length_m=4.5, width_m=2.4, wheelbase_m=2.7, front_overhang_m=0.8, rear_overhang_m=1.0)
    common = dict(incoming_path=[(5, 0), (10, 0)], outgoing_path=[(15, 0), (20, 0)], vehicles=[vehicle], include_mirrors=False)
    narrow = select_vehicle_checked_connection(**common, allowed_polygon=[(0, -1), (25, -1), (25, 1), (0, 1)])
    assert narrow['status'] == 'review_required'
    assert all(row['checks'][0]['violating_cell_count'] > 0 for row in narrow['alternatives'])
    wide = select_vehicle_checked_connection(**common, allowed_polygon=[(0, -2), (25, -2), (25, 2), (0, 2)])
    assert wide['status'] == 'pass'
    assert wide['steering_feasibility'] == 'not_verified'
