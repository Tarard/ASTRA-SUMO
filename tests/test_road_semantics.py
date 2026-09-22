from __future__ import annotations

import pytest

from torii_sumo.road_semantics import filtered_osm_modes, is_osm_passenger_way


@pytest.mark.parametrize(
    ("tags", "base_modes", "expected"),
    [
        ({"vehicle": "no"}, {"passenger"}, set()),
        ({"vehicle": "no"}, {"passenger", "bicycle", "pedestrian"}, {"pedestrian"}),
        ({"motor_vehicle": "no"}, {"passenger"}, set()),
        ({"motor_vehicle": "no"}, {"passenger", "bicycle", "pedestrian"}, {"bicycle", "pedestrian"}),
        ({"access": "no"}, {"passenger", "bicycle", "pedestrian"}, set()),
        ({"bicycle": "no"}, {"bicycle", "pedestrian"}, {"pedestrian"}),
        ({"foot": "no"}, {"bicycle", "pedestrian"}, {"bicycle"}),
    ],
)
def test_filtered_osm_modes_applies_negative_access_tags(
    tags: dict[str, str],
    base_modes: set[str],
    expected: set[str],
) -> None:
    assert filtered_osm_modes(tags, base_modes) == expected


@pytest.mark.parametrize(
    ("tags", "base_modes", "expected"),
    [
        ({"access": "no", "motor_vehicle": "permissive"}, {"passenger", "bicycle", "pedestrian"}, {"passenger"}),
        ({"access": "no", "bicycle": "designated"}, {"passenger", "bicycle", "pedestrian"}, {"bicycle"}),
        ({"vehicle": "no", "bicycle": "yes"}, {"passenger", "bicycle", "pedestrian"}, {"bicycle", "pedestrian"}),
        ({"access": "no", "foot": "yes"}, {"passenger", "bicycle", "pedestrian"}, {"pedestrian"}),
        ({"access": "no", "motor_vehicle": "yes"}, {"bicycle", "pedestrian"}, set()),
        ({"access": "no", "bicycle": "yes"}, {"passenger", "pedestrian"}, set()),
        ({"access": "no", "foot": "yes"}, {"passenger", "bicycle"}, set()),
    ],
)
def test_filtered_osm_modes_applies_specific_positive_overrides_within_base_modes(
    tags: dict[str, str],
    base_modes: set[str],
    expected: set[str],
) -> None:
    assert filtered_osm_modes(tags, base_modes) == expected


@pytest.mark.parametrize(
    ("tags", "expected"),
    [
        ({"highway": "secondary"}, True),
        ({"highway": "service", "motor_vehicle": "yes"}, True),
        ({"highway": "residential", "motor_vehicle": "no"}, False),
        ({"highway": "platform"}, False),
        ({"highway": "footway", "motor_vehicle": "yes"}, False),
        ({"highway": "construction"}, False),
        ({"highway": "cycleway"}, False),
    ],
)
def test_osm_passenger_way_requires_road_class_and_access(
    tags: dict[str, str],
    expected: bool,
) -> None:
    assert is_osm_passenger_way(tags) is expected
