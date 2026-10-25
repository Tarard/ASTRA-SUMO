"""Prepare one passive fixed-reference prefix; only --run starts native SUMO."""

import argparse
from decimal import Decimal
import gzip
import itertools
import json
from pathlib import Path
import runpy
import shutil
import time
import xml.etree.ElementTree as ET


ROOT = Path(__file__).resolve().parent
REPO = ROOT.parents[1]
FIXTURE = REPO / "outputs/hamburg-119-saturated-service-20261024-v1/run_fixture.py"
REUSED = runpy.run_path(str(FIXTURE))
digest, save = REUSED["digest"], REUSED["save"]
SOURCE = REPO / "outputs/hamburg-275-fixed-history-20261024-v1/reference"
REUSED_OBSERVER = REPO / "outputs/hamburg-119-zero-discharge-observer-20261024-v1/record_prefix.py"
PREPARED, NATIVE = ROOT / "prepared", ROOT / "native"
OLD_PARSER = REPO / "outputs/hamburg-healthy-z4-control-response-20261023-v1/record_tls.py"
END, BEGIN_TICK, LAST_TICK = 4500, 44250, 44700
UPSTREAM_TLS = ("6390683114", "244203524", "244203525")
TLS = ("LSA119_part0",) + UPSTREAM_TLS
HEAD_LANES = tuple(f"199500674#{edge}_{lane}" for edge in range(3) for lane in range(3)) + (
    "24483192_1", "24483192_2", "43866090#0_0", "43866090#0_1", "486864294_0", "486864294_1")
EDGES = {"199500674#0", "199500674#1", "199500674#2", "24483192", "43866090#0", "486864294"}



def planned_request():
    original = SOURCE / "evaluation/native"
    request = json.loads((original / "native-request.json").read_bytes())
    command = [part.replace(str(original), str(NATIVE)) for part in request["command"]]
    for flag, value in {"--net-file": SOURCE / "prepared/network.net.xml", "--route-files": SOURCE / "prepared/demand.rou.xml",
        "--additional-files": ",".join(map(str, [SOURCE / "prepared/control.add.xml", PREPARED / "e1.add.xml", PREPARED / "instant.add.xml"])),
        "--end": END}.items():
        command[command.index(flag) + 1] = str(value)
    request.update(command=command, end_s=END, monitor_file=str(NATIVE / "stall-monitor.json"),
        lane_intentions_file=str(SOURCE / "prepared/lane-intentions.json"), route_file=str(SOURCE / "prepared/demand.rou.xml"),
        e1_file=str(SOURCE / "prepared/field-loops.add.xml"))
    return request


def prepare():
    PREPARED.mkdir(parents=True, exist_ok=False)
    for name, tag, output in [("e1.add.xml", "inductionLoop", "field-counts.xml"),
                              ("instant.add.xml", "instantInductionLoop", "instant-e1.xml")]:
        tree = ET.parse(SOURCE / "evaluation/native" / name)
        for row in tree.getroot().findall(tag):
            row.set("file", str(NATIVE / output))
            if tag == "inductionLoop":
                row.set("end", str(END))
        tree.write(PREPARED / name, encoding="utf-8", xml_declaration=True)
    net = ET.parse(SOURCE / "prepared/network.net.xml").getroot()
    lanes = [lane.get("id") for edge in net.findall("edge")
        if edge.get("id") in EDGES or edge.get("id") in {":LSA119_part0_0", ":263002865_1"}
        or any(edge.get("id").startswith(":" + tls + "_") for tls in UPSTREAM_TLS)
        for lane in edge.findall("lane")]
    assert set(HEAD_LANES) <= set(lanes)
    save(PREPARED / "native-request.json", planned_request())
    paths = [SOURCE / "prepared" / name for name in ["network.net.xml", "control.add.xml", "demand.rou.xml", "field-loops.add.xml",
        "expected-counts.csv", "initialization-counts.csv", "lane-intentions.json", "scenario.json"]]
    paths += [SOURCE / "evaluation/native" / name for name in ["native-request.json", "field-counts.xml", "summary.xml", "instant-e1.xml",
        "vehicle-routes.xml", "tripinfo.xml", "stall-monitor.json", "e1.add.xml", "instant.add.xml"]]
    paths += [Path(__file__), REUSED_OBSERVER, FIXTURE, OLD_PARSER,
        SOURCE.parent / "independent.json", SOURCE.parent / "service-diagnosis/summary.json", REPO / "plugins/astra-sumo/src/astra_sumo/core/simulation_stall.py",
        REPO / "plugins/astra-sumo/src/astra_sumo/core/lane_section_hold.py"]
    save(ROOT / "plan.json", {"formal_result": False, "source_hashes": {str(p): digest(p) for p in paths},
        "prepared_hashes": {str(p): digest(p) for p in PREPARED.iterdir()}, "lanes": lanes, "tls_ids": TLS,
        "end_s": END, "native_window_s_inclusive": [4425, 4470], "expected_frames": LAST_TICK - BEGIN_TICK + 1,
        "scope": "Original fixed-reference full8589 future requests and original driver; only end4500, detector output paths/end and passive getters differ. No setters are added. No whole-network FCD is requested.",
        "time_boundary": "After native step, before unchanged LaneSectionHold.step. Native XML step label equals API time minus0.1s. Both labels are retained.",
        "claim_boundary": "Only after65 closed E1 rows and45000 summary steps pass native parity may the local observations explain this recorded episode. No unmeasured real target is replaced with zero."})


def check():
    plan = json.loads((ROOT / "plan.json").read_bytes())
    assert all(digest(path) == value for path, value in plan["source_hashes"].items())
    assert all(digest(path) == value for path, value in plan["prepared_hashes"].items())
    request = json.loads((PREPARED / "native-request.json").read_bytes())
    assert request == planned_request()
    assert len(ET.parse(request["route_file"]).getroot().findall("vehicle")) == 8589
    parent = json.loads((SOURCE.parent / "independent.json").read_bytes())
    assert parent["reference14_strict_parity_verified"] and parent["all50_actual_colors_programs_difference_ticks_zero"]
    assert digest(SOURCE / "prepared/control.add.xml") == digest(SOURCE / "evaluation/native/signal-programs.add.xml")
    source = json.loads((SOURCE / "evaluation/native/native-request.json").read_bytes())
    for key in ("backend", "all_stopped_timeout_s", "monitor_interval_s", "junction_body_hold", "junction_body_hold_nodes"):
        assert request[key] == source[key]
    assert request["backend"] == "libsumo"
    assert request["command"][request["command"].index("--step-length") + 1] == "0.1"
    assert not any(flag in request["command"] for flag in ("--fcd-output", "--lanechange-output"))
    assert len(ET.parse(PREPARED / "e1.add.xml").getroot()) == len(ET.parse(PREPARED / "instant.add.xml").getroot()) == 13
    runtime, _ = REUSED["check_runtime"]()
    return {"status": "pass", "native_runs": 0, "vehicles": 8589, "frames": plan["expected_frames"], "runtime": runtime}


def run():
    checked = check()
    _, api = REUSED["check_runtime"]()
    from astra_sumo.core.simulation_stall import monitor_sumo

    NATIVE.mkdir(exist_ok=False)
    shutil.copyfile(__file__, NATIVE / "executed-observer.py")
    save(NATIVE / "runtime.json", checked["runtime"])
    request = json.loads((PREPARED / "native-request.json").read_bytes())
    plan = json.loads((ROOT / "plan.json").read_bytes())
    errors, ticks = [], []
    handle = gzip.open(NATIVE / "read-only-samples.jsonl.gz", "wt", encoding="utf-8")
    original = api.simulationStep

    def get(label, function, *args):
        try:
            return function(*args)
        except Exception as error:
            errors.append({"api_time_s": api.simulation.getTime(), "getter": label, "args": args,
                "error": f"{type(error).__name__}: {error}"})
            return None

    def observed_step(*args, **kwargs):
        result = original(*args, **kwargs)
        api_time = api.simulation.getTime()
        tick = round(api_time * 10) - 1
        if BEGIN_TICK <= tick <= LAST_TICK:
            ticks.append(tick)
            lanes, heads = {}, {}
            for lane in plan["lanes"]:
                identities = get("lane.getLastStepVehicleIDs", api.lane.getLastStepVehicleIDs, lane)
                members = []
                for identity in identities or []:
                    members.append({"id": identity, "pos_m": get("vehicle.getLanePosition", api.vehicle.getLanePosition, identity),
                        "speed_m_s": get("vehicle.getSpeed", api.vehicle.getSpeed, identity)})
                members.sort(key=lambda row: row["pos_m"] if row["pos_m"] is not None else -1, reverse=True)
                lanes[lane] = {"getter_ok": identities is not None, "vehicles": members}
                if lane in HEAD_LANES and members:
                    identity = members[0]["id"]
                    heads[lane] = {"id": identity, "leader": get("vehicle.getLeader", api.vehicle.getLeader, identity, 200),
                        "next_tls": get("vehicle.getNextTLS", api.vehicle.getNextTLS, identity),
                        "stop_state": get("vehicle.getStopState", api.vehicle.getStopState, identity),
                        "stops": [str(row) for row in (get("vehicle.getStops", api.vehicle.getStops, identity) or [])],
                        "lane_change_mode": get("vehicle.getLaneChangeMode", api.vehicle.getLaneChangeMode, identity),
                        "speed_mode": get("vehicle.getSpeedMode", api.vehicle.getSpeedMode, identity)}
            frame = {"api_time_s": api_time, "native_step_time_s": tick / 10, "lanes": lanes, "heads": heads,
                "tls": {tls: {"state": get("tls.state", api.trafficlight.getRedYellowGreenState, tls),
                    "phase": get("tls.phase", api.trafficlight.getPhase, tls), "program": get("tls.program", api.trafficlight.getProgram, tls)} for tls in TLS}}
            handle.write(json.dumps(frame, separators=(",", ":")) + "\n")
        return result

    api.simulationStep = observed_step
    started = time.perf_counter()
    try:
        monitor_sumo(request)
    finally:
        api.simulationStep = original
        handle.close()
        save(NATIVE / "observer-coverage.json", {"status": "pass" if ticks == list(range(BEGIN_TICK, LAST_TICK + 1)) and not errors else "review_required",
            "frames": len(ticks), "expected_frames": LAST_TICK - BEGIN_TICK + 1, "getter_errors": errors,
            "hook_restored": api.simulationStep == original, "elapsed_seconds": time.perf_counter() - started})
    audit()


def audit():
    # Reuse the sealed streaming parser and the prior observer's strict prefix comparisons.
    elements = runpy.run_path(str(OLD_PARSER))["elements"]
    source = SOURCE / "evaluation/native"
    differences, sizes = {}, {}
    for name, tag, time_key, closed, ignored in [("field-counts.xml", "interval", "end", True, ()),
        ("summary.xml", "step", "time", False, ("duration",)), ("instant-e1.xml", "instantOut", "time", False, ())]:
        def rows(path):
            for element in elements(path, tag):
                t = Decimal(element.get(time_key))
                if tag == "interval" and Decimal(element.get("begin")) >= t:
                    continue
                if t <= END if closed else t < END:
                    yield {k: v for k, v in element.attrib.items() if k not in ignored}
        bad, count = [], 0
        for index, (before, after) in enumerate(itertools.zip_longest(rows(source / name), rows(NATIVE / name)), 1):
            count = index
            if before != after and len(bad) < 10:
                bad.append({"row": index, "source": before, "observer": after})
        differences[name], sizes[name] = bad, count
    route_sets, trip_sets = [], []
    for folder in (source, NATIVE):
        route_sets.append({v.get("id"): ET.canonicalize(ET.tostring(v, encoding="unicode"), strip_text=True)
            for v in elements(folder / "vehicle-routes.xml", "vehicle") if 0 <= Decimal(v.get("arrival")) < END})
        trip_sets.append({v.get("id"): ET.canonicalize(ET.tostring(v, encoding="unicode"), strip_text=True)
            for v in elements(folder / "tripinfo.xml", "tripinfo") if 0 <= Decimal(v.get("arrival")) < END})
    old_hold, new_hold = [json.loads((folder / "stall-monitor.json").read_bytes())["lane_section_hold"] for folder in (source, NATIVE)]
    holds_same = [r for r in old_hold["events"] if r["time_s"] <= END] == new_hold["events"]
    body_same = [r for r in old_hold["junction_body_hold"]["events"] if r["time_s"] <= END] == new_hold["junction_body_hold"]["events"]
    monitor = json.loads((NATIVE / "stall-monitor.json").read_bytes())
    last = [dict(row.attrib) for row in elements(NATIVE / "summary.xml", "step")][-1]
    coverage = json.loads((NATIVE / "observer-coverage.json").read_bytes())
    checks = {"65_closed_e1_all_attributes_identical": sizes["field-counts.xml"] == 65 and not differences["field-counts.xml"],
        "45000_summary_rows_except_duration_identical": sizes["summary.xml"] == 45000 and not differences["summary.xml"],
        "all_strict_prefix_instant_events_identical": not differences["instant-e1.xml"],
        "arrived_routes_exitTimes_identical": route_sets[0] == route_sets[1], "arrived_trips_identical": trip_sets[0] == trip_sets[1],
        "lane_and_body_hold_events_identical": holds_same and body_same, "coverage451_and_zero_getter_errors": coverage["status"] == "pass" and coverage["frames"] == coverage["expected_frames"] == 451,
        "monitor_completed4500": monitor["status"] == "completed" and monitor["simulation_time_s"] == END,
        "zero_unsafe": not ET.parse(NATIVE / "collisions.xml").getroot().findall("collision")
            and all(int(last[key]) == 0 for key in ("collisions", "teleports", "discarded")),
        "source_hashes_unchanged": all(digest(path) == value for path, value in json.loads((ROOT / "plan.json").read_bytes())["source_hashes"].items())}
    actual = {(r.get("id"), Decimal(r.get("begin")), Decimal(r.get("end"))): int(r.get("nVehContrib")) for r in elements(NATIVE / "field-counts.xml", "interval")}
    import csv
    targets = []
    for name in ("initialization-counts.csv", "expected-counts.csv"):
        with (SOURCE / "prepared" / name).open(encoding="utf-8", newline="") as handle:
            for row in csv.DictReader(handle):
                key = (row["detector_id"], Decimal(row["begin"]), Decimal(row["end"]))
                value = actual.get(key)
                targets.append({"detector_id": key[0], "begin": row["begin"], "end": row["end"],
                    "expected_total": int(row["expected_total"]), "simulated_count": value,
                    "difference": value - int(row["expected_total"]) if value is not None else None})
    save(NATIVE / "target182-prefix-measurements.json", targets)
    report = {"status": "pass" if all(checks.values()) else "blocked", "formal_result": False, "checks": checks,
        "rows_checked": sizes, "differences": differences, "arrived_routes_checked": len(route_sets[1]),
        "completion": last, "scope": "Passive4500s prefix only.65 closed field intervals measured; other117 real target rows remain null. Native parity is separate from a mechanism claim."}
    save(NATIVE / "record-result.json", report)
    print(json.dumps(report, ensure_ascii=False))
    assert report["status"] == "pass"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--prepare-only", action="store_true")
    action.add_argument("--check-only", action="store_true")
    action.add_argument("--run", action="store_true")
    args = parser.parse_args()
    if args.prepare_only:
        prepare()
    if args.prepare_only or args.check_only:
        checked = check()
        save(ROOT / "selfchecks.json", checked)
        print(json.dumps({k: v for k, v in checked.items() if k != "runtime"}))
    else:
        run()
