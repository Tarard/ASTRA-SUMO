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

    def __init__(self, assignments):
        self.assignments = assignments
        self.held = {}
        self.finished = set()
        self.events = []

    def step(self, vehicle, samples, now):
        for identity, hold in list(self.held.items()):
            row = samples.get(identity)
            if row is None:
                self.events.append({"vehicle_id": identity, "event": "left_before_release", "time_s": now})
            elif row[tc.VAR_DISTANCE] >= hold["clear_distance_m"]:
                vehicle.setLaneChangeMode(identity, hold["previous_mode"])
                self.events.append({"vehicle_id": identity, "event": "released", "time_s": now,
                                    "restored_mode": hold["previous_mode"]})
            else:
                continue
            del self.held[identity]
            self.finished.add(identity)
        for identity, row in samples.items():
            if identity in self.finished or identity in self.held:
                continue
            lane, point = self.assignments[identity]
            if row[tc.VAR_LANE_ID] != lane:
                continue
            position = row[tc.VAR_LANEPOSITION]
            if position > point:
                continue
            previous = vehicle.getLaneChangeMode(identity)
            clear = row[tc.VAR_DISTANCE] + point - position + vehicle.getLength(identity) + 1e-6
            vehicle.setLaneChangeMode(identity, 512)
            self.held[identity] = {"previous_mode": previous, "clear_distance_m": clear}
            self.events.append({"vehicle_id": identity, "event": "held", "time_s": now,
                                "lane": lane, "position_m": position, "previous_mode": previous,
                                "clear_distance_m": clear})

    def report(self):
        return {"mechanism": "Native laneChangeMode=512 after reaching the assigned detector lane; restore the prior mode after rear clearance. No lane, position, speed, route or signal commands.",
                "safety_source": "https://sumo.dlr.de/docs/TraCI/Change_Vehicle_State.html#lane-change-mode-0xb6",
                "claim_boundary": "Declared local driver lane intention. No detector count feedback or measured natural driver behaviour.",
                "events": self.events, "still_held": self.held,
                "released_vehicles": sum(r["event"] == "released" for r in self.events),
                "left_before_release": sum(r["event"] == "left_before_release" for r in self.events)}
