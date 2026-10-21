"""Keep a declared observation lane until the vehicle rear clears its point E1."""

import gzip
import json
import math
from pathlib import Path
import xml.etree.ElementTree as ET

from traci import constants as tc


def read_lane_intentions(intentions_file, e1_file, vehicle_ids):
    path = Path(intentions_file)
    raw = path.read_bytes()
    rows = json.loads(gzip.decompress(raw) if path.suffix == ".gz" else raw)["vehicles"]
    fields = {r.get("id"): (r.get("lane"), float(r.get("pos")))
              for r in ET.parse(e1_file).getroot().findall("inductionLoop")}
    assigned = {r["vehicle_id"]: r["field_id"] for r in rows}
    if len(assigned) != len(rows) or set(assigned) != set(vehicle_ids) or not set(assigned.values()) <= fields.keys():
        raise ValueError("Supply one existing detector intention for each explicit vehicle.")
    if any(not math.isfinite(fields[field][1]) or fields[field][1] < 0 for field in assigned.values()):
        raise ValueError("Use resolved nonnegative E1 positions for lane-section holding.")
    return {identity: fields[field] for identity, field in assigned.items()}


class LaneSectionHold:
    """Use native mode 512 locally, then restore each vehicle's previous mode."""

    def __init__(self, assignments, *, junction_lane_lengths=None):
        self.assignments = assignments
        self.held = {}
        self.finished = set()
        self.events = []
        self.previous_modes = {}
        self.junction_lane_lengths = junction_lane_lengths
        self.junction_held = {}
        self.junction_events = []
        # shortcut: Replay vehicle lengths are fixed; refresh this cache if runtime type changes are added.
        self.lengths = {}

    def _hold_mode(self, vehicle, identity):
        if identity not in self.previous_modes:
            self.previous_modes[identity] = vehicle.getLaneChangeMode(identity)
            vehicle.setLaneChangeMode(identity, 512)
        return self.previous_modes[identity]

    def _restore_mode(self, vehicle, identity, *, present):
        if identity not in self.held and identity not in self.junction_held:
            previous = self.previous_modes.pop(identity)
            if present:
                vehicle.setLaneChangeMode(identity, previous)

    def _junction_step(self, vehicle, samples, now):
        if self.junction_lane_lengths is None:
            return
        for identity, row in samples.items():
            lane = row[tc.VAR_LANE_ID]
            if lane not in self.junction_lane_lengths:
                continue
            if identity not in self.lengths:
                self.lengths[identity] = vehicle.getLength(identity)
            if identity not in self.junction_held:
                self._hold_mode(vehicle, identity)
                self.junction_events.append({"vehicle_id": identity, "event": "held", "time_s": now, "lane": lane})
            self.junction_held[identity] = row[tc.VAR_DISTANCE] + self.junction_lane_lengths[lane] - row[tc.VAR_LANEPOSITION] + self.lengths[identity] + 1e-6
        for identity, clear in list(self.junction_held.items()):
            row = samples.get(identity)
            if row is not None and row[tc.VAR_DISTANCE] < clear:
                continue
            del self.junction_held[identity]
            self._restore_mode(vehicle, identity, present=row is not None)
            self.junction_events.append({"vehicle_id": identity, "event": "released" if row is not None else "left_before_release", "time_s": now})

    def step(self, vehicle, samples, now):
        self._junction_step(vehicle, samples, now)
        for identity, hold in list(self.held.items()):
            row = samples.get(identity)
            if row is None:
                self.events.append({"vehicle_id": identity, "event": "left_before_release", "time_s": now})
            elif row[tc.VAR_DISTANCE] >= hold["clear_distance_m"]:
                self.events.append({"vehicle_id": identity, "event": "released", "time_s": now,
                                    "restored_mode": hold["previous_mode"] if identity not in self.junction_held else None,
                                    **({"pending_restore_mode": hold["previous_mode"]} if identity in self.junction_held else {})})
            else:
                continue
            del self.held[identity]
            self._restore_mode(vehicle, identity, present=row is not None)
            self.finished.add(identity)
        for identity, row in samples.items():
            if identity in self.finished or identity in self.held:
                continue
            lane, point = self.assignments[identity]
            if row[tc.VAR_LANE_ID] != lane:
                continue
            position = row[tc.VAR_LANEPOSITION]
            length = vehicle.getLength(identity)
            if position >= point + length:
                continue
            previous = self._hold_mode(vehicle, identity)
            clear = row[tc.VAR_DISTANCE] + point - position + length + 1e-6
            self.held[identity] = {"previous_mode": previous, "clear_distance_m": clear}
            self.events.append({"vehicle_id": identity, "event": "held", "time_s": now,
                                "lane": lane, "position_m": position, "previous_mode": previous,
                                "clear_distance_m": clear})

    def report(self):
        report = {"mechanism": "Native laneChangeMode=512 after reaching the assigned detector lane; restore the prior mode after rear clearance. No lane, position, speed, route or signal commands.",
                "safety_source": "https://sumo.dlr.de/docs/TraCI/Change_Vehicle_State.html#lane-change-mode-0xb6",
                "claim_boundary": "Declared local driver lane intention. No detector count feedback or measured natural driver behaviour.",
                "events": self.events, "still_held": self.held,
                "released_vehicles": sum(r["event"] == "released" for r in self.events),
                "left_before_release": sum(r["event"] == "left_before_release" for r in self.events)}
        if self.junction_lane_lengths is not None:
            report["junction_body_hold"] = {
                "mechanism": "Hold mode 512 from the first post-step internal-lane sample until the rear clears all subsequent internal lanes. Restore only when both detector and junction holds end.",
                "sampling_limit": "An entry-step lane change before the first internal-lane sample is not prevented.",
                "events": self.junction_events, "still_held": self.junction_held,
                "pending_original_modes": self.previous_modes,
                "claim_boundary": "Declared exploratory driver assumption on all internal lanes, not measured natural behavior or a network repair."}
        return report
