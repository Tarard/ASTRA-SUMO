import json

import pytest
from traci import constants as tc

from astra_sumo.core.lane_section_hold import LaneSectionHold, read_lane_intentions


def test_hold_starts_only_on_target_lane_and_restores_after_rear_clearance():
    class Vehicle:
        def __init__(self):
            self.changes = []

        def getLaneChangeMode(self, identity):
            return 1233

        def getLength(self, identity):
            return 5

        def setLaneChangeMode(self, identity, mode):
            self.changes.append((identity, mode))

    vehicle = Vehicle()
    driver = LaneSectionHold({"v": ("field_1", 29.9)})

    def sample(lane, position, distance):
        return {"v": {tc.VAR_LANE_ID: lane, tc.VAR_LANEPOSITION: position, tc.VAR_DISTANCE: distance}}

    driver.step(vehicle, sample("field_0", 10, 100), 0)
    assert vehicle.changes == []
    driver.step(vehicle, sample("field_1", 20, 110), 1)
    assert vehicle.changes == [("v", 512)]
    driver.step(vehicle, sample(":junction_1", 4.7, 124.8), 2)
    assert vehicle.changes == [("v", 512)]
    driver.step(vehicle, sample(":junction_1", 4.9, 124.91), 3)
    assert vehicle.changes == [("v", 512), ("v", 1233)]
    driver.step(vehicle, sample("outlet_0", 10, 135), 4)
    assert len(vehicle.changes) == 2
    assert driver.report()["released_vehicles"] == 1
    assert not driver.report()["still_held"]
    late = LaneSectionHold({"v": ("field_1", 29.9)})
    late.step(vehicle, sample("field_1", 31, 121), 5)
    assert late.held["v"]["clear_distance_m"] == pytest.approx(124.9)
    late.step(vehicle, sample(":junction_1", 4.91, 124.91), 6)
    assert late.report()["released_vehicles"] == 1


def test_intention_reader_requires_one_known_field_per_vehicle(tmp_path):
    loops, intentions = tmp_path / "loops.xml", tmp_path / "intentions.json"
    loops.write_text('<additional><inductionLoop id="f" lane="road_1" pos="20"/></additional>', encoding="utf-8")
    row = {"vehicle_id": "v", "field_id": "f"}
    intentions.write_text(json.dumps({"vehicles": [row]}), encoding="utf-8")
    assert read_lane_intentions(intentions, loops, {"v"}) == {"v": ("road_1", 20)}
    intentions.write_text(json.dumps({"vehicles": [row, row]}), encoding="utf-8")
    with pytest.raises(ValueError, match="one existing detector"):
        read_lane_intentions(intentions, loops, {"v"})
