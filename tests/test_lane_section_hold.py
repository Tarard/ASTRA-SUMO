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


class Modes:
    def __init__(self):
        self.mode = 1621
        self.changes = []

    def getLength(self, identity):
        return 5

    def getLaneChangeMode(self, identity):
        return self.mode

    def setLaneChangeMode(self, identity, mode):
        self.mode = mode
        self.changes.append((identity, mode))


def body_sample(lane, position, distance):
    return {"v": {tc.VAR_LANE_ID: lane, tc.VAR_LANEPOSITION: position, tc.VAR_DISTANCE: distance}}


def test_detector_release_waits_for_junction_rear_clearance():
    vehicle = Modes()
    driver = LaneSectionHold({"v": ("in_0", 29.9)}, junction_lane_lengths={":j_0_0": 10})
    driver.step(vehicle, body_sample("in_0", 20, 110), 0)
    driver.step(vehicle, body_sample(":j_0_0", .2, 120.2), 1)
    driver.step(vehicle, body_sample(":j_0_0", 4.91, 124.91), 2)
    assert vehicle.changes == [("v", 512)] and "v" in driver.finished
    assert driver.events[-1]["restored_mode"] is None and driver.events[-1]["pending_restore_mode"] == 1621
    driver.step(vehicle, body_sample("out_0", 4.3, 134.3), 3)
    assert vehicle.mode == 512
    driver.step(vehicle, body_sample("out_0", 5.01, 135.01), 4)
    assert vehicle.changes == [("v", 512), ("v", 1621)]
    assert not driver.junction_held and not driver.previous_modes


def test_junction_release_waits_for_detector_rear_clearance():
    vehicle = Modes()
    driver = LaneSectionHold({"v": ("out_0", 1)}, junction_lane_lengths={":j_0_0": 10})
    driver.step(vehicle, body_sample(":j_0_0", 9, 29), 0)
    driver.step(vehicle, body_sample("out_0", .1, 30.1), 1)
    driver.step(vehicle, body_sample("out_0", 5.01, 35.01), 2)
    assert not driver.junction_held and "v" in driver.held and vehicle.mode == 512
    driver.step(vehicle, body_sample("out_0", 6.01, 36.01), 3)
    assert vehicle.changes == [("v", 512), ("v", 1621)] and not driver.previous_modes


def test_successive_internal_lanes_extend_hold_and_missing_vehicles_clear_both_reasons():
    vehicle = Modes()
    driver = LaneSectionHold({"v": ("field_0", 9.9)}, junction_lane_lengths={":a_0_0": 2, ":b_0_0": 10})
    driver.step(vehicle, body_sample(":a_0_0", 1, 11), 0)
    driver.step(vehicle, body_sample(":b_0_0", 9, 21), 1)
    driver.step(vehicle, body_sample("field_0", 4.3, 26.3), 2)
    assert vehicle.mode == 512 and "v" in driver.held
    driver.step(vehicle, {}, 3)
    assert not driver.held and not driver.junction_held and not driver.previous_modes
    assert vehicle.changes == [("v", 512)]
    assert driver.report()["junction_body_hold"]["events"][-1]["event"] == "left_before_release"


def test_junction_hold_request_cannot_silently_run_without_a_driver():
    from astra_sumo.core.simulation_stall import monitor_sumo

    with pytest.raises(ValueError, match="Provide lane intentions"):
        monitor_sumo({"all_stopped_timeout_s": 300, "junction_body_hold": True})
