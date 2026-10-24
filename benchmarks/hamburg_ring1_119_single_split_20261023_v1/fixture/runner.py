"""Four ordinary-road entry states with one measured desired-speed boundary."""

import argparse
import csv
import hashlib
import json
import math
import os
from pathlib import Path
import xml.etree.ElementTree as ET


def run_case(*, input_dir, net_file, output_dir):
    """Read immutable inputs and write one four-vehicle case to a new directory."""
    import libsumo
    import sumo
    from traci import constants as tc

    from astra_sumo.core.lane_section_hold import LaneSectionHold, read_lane_intentions

    if libsumo.__version__ != "1.27.1":
        raise ValueError("Use SUMO/Libsumo 1.27.1 for this native regression.")
    inputs, net = Path(input_dir).resolve(strict=True), Path(net_file).resolve(strict=True)
    out = Path(output_dir).resolve()
    if out.exists() or out.is_relative_to(inputs) or inputs.is_relative_to(out):
        raise ValueError("Use a new output directory separate from the frozen inputs.")
    out.mkdir(parents=True)
    case = json.loads((inputs / "case.json").read_bytes())
    names = (
        "case.json",
        "control.add.xml",
        "demand.rou.xml",
        "intents.json",
        "loops.add.xml",
        "leader-speed.csv",
        "reference-positions.csv",
    )
    paths = [inputs / name for name in names] + [net]
    before = {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}
    with (inputs / "leader-speed.csv").open(encoding="utf-8", newline="") as stream:
        desired = {round(float(row["time_s"]), 1): float(row["speed_m_s"]) for row in csv.DictReader(stream)}
    with (inputs / "reference-positions.csv").open(encoding="utf-8", newline="") as stream:
        reference = {round(float(row["time_s"]), 1): row for row in csv.DictReader(stream)}
    ids, leader = case["vehicle_ids"], case["leader_id"]
    assignments = read_lane_intentions(inputs / "intents.json", inputs / "loops.add.xml", ids)
    network = ET.parse(net).getroot()
    internal = {
        lane.get("id"): float(lane.get("length"))
        for edge in network.findall("edge")
        if edge.get("function") == "internal"
        and any(edge.get("id").startswith(":" + node + "_") for node in case["junction_body_hold_nodes"])
        for lane in edge.findall("lane")
    }
    driver = LaneSectionHold(assignments, junction_lane_lengths=internal)
    binary = Path(sumo.__file__).parent / "bin" / ("sumo.exe" if os.name == "nt" else "sumo")
    command = [
        str(binary),
        "--net-file",
        str(net),
        "--route-files",
        str(inputs / "demand.rou.xml"),
        "--additional-files",
        str(inputs / "control.add.xml"),
        "--begin",
        "0",
        "--end",
        str(case["end_s"]),
        "--step-length",
        str(case["step_s"]),
        "--seed",
        str(case["seed"]),
        "--precision",
        "12",
        "--time-to-teleport",
        "-1",
        "--collision.check-junctions",
        "true",
        "--no-step-log",
        "true",
        "--collision-output",
        str(out / "collisions.xml"),
        "--lanechange-output",
        str(out / "lanechanges.xml"),
        "--tripinfo-output",
        str(out / "tripinfo.xml"),
        "--tripinfo-output.write-unfinished",
        "true",
        "--error-log",
        str(out / "sumo-errors.log"),
        "--log",
        str(out / "sumo.log"),
    ]
    (out / "request.json").write_text(
        json.dumps(
            {"command": command, "input_sha256": before, "external_boundary": case["external_boundary"]}, indent=2
        )
        + "\n",
        encoding="utf-8",
    )
    trace, speed_modes, speed_errors, xy_errors, distance_errors = [], {}, [], [], []
    initial_distance = None
    try:
        libsumo.start(command)
        # Empty-time warm-up keeps the original absolute signal phase.
        libsumo.simulationStep(case["observe_begin_s"])
        assert set(libsumo.vehicle.getIDList()) == set(ids)
        assert libsumo.trafficlight.getRedYellowGreenState("LSA119_part0") == case["initial_signal"]
        assert libsumo.trafficlight.getRedYellowGreenState("GS_266199063") == case["initial_GS_signal"]
        initial_distance = libsumo.vehicle.getDistance(leader)
        while True:
            now = round(libsumo.simulation.getTime(), 1)
            present = libsumo.vehicle.getIDList()
            states = {
                identity: {
                    tc.VAR_LANE_ID: libsumo.vehicle.getLaneID(identity),
                    tc.VAR_LANEPOSITION: libsumo.vehicle.getLanePosition(identity),
                    tc.VAR_DISTANCE: libsumo.vehicle.getDistance(identity),
                    tc.VAR_SPEED: libsumo.vehicle.getSpeed(identity),
                }
                for identity in present
            }
            driver.step(libsumo.vehicle, states, now)
            row = {
                "time_s": now,
                "signal": libsumo.trafficlight.getRedYellowGreenState("LSA119_part0"),
                "GS_signal": libsumo.trafficlight.getRedYellowGreenState("GS_266199063"),
                "vehicles": {},
            }
            for identity in present:
                mode = libsumo.vehicle.getSpeedMode(identity)
                assert mode == 31
                speed_modes[identity] = mode
                row["vehicles"][identity] = {
                    "lane": libsumo.vehicle.getLaneID(identity),
                    "pos": libsumo.vehicle.getLanePosition(identity),
                    "speed": libsumo.vehicle.getSpeed(identity),
                    "xy": libsumo.vehicle.getPosition(identity),
                    "distance": libsumo.vehicle.getDistance(identity),
                    "lane_change_mode": libsumo.vehicle.getLaneChangeMode(identity),
                    "speed_mode": mode,
                }
            trace.append(row)
            if leader in row["vehicles"]:
                observed, actual = reference[now], row["vehicles"][leader]
                speed_errors.append(abs(actual["speed"] - float(observed["speed_m_s"])))
                xy_errors.append(math.dist(actual["xy"], (float(observed["x"]), float(observed["y"]))))
                travelled = actual["distance"] - initial_distance
                expected = float(observed["distance_m"]) - float(reference[case["observe_begin_s"]]["distance_m"])
                distance_errors.append(abs(travelled - expected))
            if now >= case["end_s"]:
                break
            if leader in present:
                # External measured desired speed; speedMode31 keeps native safety checks.
                libsumo.vehicle.setSpeed(leader, desired[round(now + case["step_s"], 1)])
            libsumo.simulationStep()
    finally:
        libsumo.close()
    (out / "samples.json").write_text(json.dumps(trace) + "\n", encoding="utf-8")
    collisions = [row.attrib for row in ET.parse(out / "collisions.xml").getroot()]
    result = {
        "status": "collision_reproduced" if collisions else "no_collision",
        "collision_rows": collisions,
        "native_safety_modes": speed_modes,
        "leader_max_speed_error_m_s": max(speed_errors),
        "leader_max_xy_error_m": max(xy_errors),
        "leader_max_travelled_distance_error_m": max(distance_errors),
        "samples": len(trace),
        "external_boundary": case["external_boundary"],
        "formal_result": False,
        "inputs_unchanged": all(
            hashlib.sha256(Path(path).read_bytes()).hexdigest() == digest for path, digest in before.items()
        ),
        "input_sha256": before,
    }
    assert result["inputs_unchanged"] and len(ids) == 4
    (out / "result.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", type=Path, required=True)
    parser.add_argument("--net-file", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(run_case(input_dir=args.input_dir, net_file=args.net_file, output_dir=args.output_dir)))
