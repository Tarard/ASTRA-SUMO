from torii_sumo.intersection.geometry import (
    abs_angle_between,
    bearing_between_xy,
    classify_angle_relation,
    euclidean_distance,
    normalize_signed_angle,
    segment_distance,
    segment_intersection,
)


def test_angle_helpers_match_intersection_thresholds() -> None:
    assert normalize_signed_angle(190) == -170
    assert normalize_signed_angle(-190) == 170
    assert abs_angle_between(350, 10) == 20
    assert classify_angle_relation(10) == "same_direction"
    assert classify_angle_relation(45) == "acute_merge"
    assert classify_angle_relation(90) == "right_angle"
    assert classify_angle_relation(140) == "obtuse_merge"
    assert classify_angle_relation(170) == "opposite_direction"


def test_xy_geometry_helpers_cover_bearing_distance_and_crossing() -> None:
    assert bearing_between_xy((0, 0), (0, 1)) == 0
    assert bearing_between_xy((0, 0), (1, 0)) == 90
    assert euclidean_distance((0, 0), (3, 4)) == 5
    assert segment_intersection((0, 0), (2, 2), (0, 2), (2, 0)) == (1.0, 1.0)
    assert segment_intersection((0, 0), (1, 0), (0, 1), (1, 1)) is None
    assert segment_distance((0, 0), (1, 0), (2, 0), (3, 0)) == 1
