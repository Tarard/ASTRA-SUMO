"""Read back installed programs and compare a small cohort at two absolute start times."""

from collections import defaultdict
from copy import deepcopy
import gzip
import json
import os
from pathlib import Path
import subprocess
import xml.etree.ElementTree as ET

from .artifact_io import write_json_atomic
from .candidate_contracts import file_sha256
from .command_runner import run_command, _terminate_process_tree
from .ocit_c_programs import _intergreen_checks


def _readback(network, control, binary, output):
    import traci
    from sumolib.miscutils import getFreeSocketPort

    port = getFreeSocketPort()
    command = [
        str(binary),
        "-n",
        str(network),
        "-a",
        str(control),
        "--remote-port",
        str(port),
        "--no-step-log",
        "true",
        "--error-log",
        str(output / "readback.log"),
    ]
    process = subprocess.Popen(
        command,
        stdin=subprocess.DEVNULL,
        start_new_session=os.name != "nt",
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    connection = None
    result = {"command": command, "programs": {}}
    try:
        connection = traci.connect(port, numRetries=3, proc=process)
        result["native_version"] = connection.getVersion()
        if not result["native_version"][1].endswith("1.27.1"):
            raise ValueError("Use the declared SUMO 1.27.1 runtime.")
        expected = {logic.get("id"): logic for logic in ET.parse(control).getroot().findall("tlLogic")}
        for tls in connection.trafficlight.getIDList():
            program = connection.trafficlight.getProgram(tls)
            if program != expected[tls].get("programID"):
                raise ValueError(f"The installed control is not active at {tls}.")
            logic = next(item for item in connection.trafficlight.getAllProgramLogics(tls) if item.programID == program)
            assert logic.type == 0
            phases = [{"duration": p.duration, "state": p.state} for p in logic.phases]
            assert phases == [
                {"duration": float(p.get("duration")), "state": p.get("state")} for p in expected[tls].findall("phase")
            ]
            assert connection.trafficlight.getRedYellowGreenState(tls) == phases[0]["state"]
            result["programs"][tls] = {
                "active_program": program,
                "type": logic.type,
                "phases": phases,
                "controlled_links": connection.trafficlight.getControlledLinks(tls),
            }
    finally:
        try:
            if connection is not None:
                connection.close(wait=False)
        finally:
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                _terminate_process_tree(process)
    if process.returncode != 0:
        raise RuntimeError("SUMO control inspection failed.")
    return result


def _state(phases, time):
    elapsed = 0.0
    for phase in phases:
        elapsed += phase["duration"]
        if time < elapsed - 1e-8:
            return phase["state"]
    raise ValueError("Time lies outside the installed cycle.")


def compare_clock_outputs(first, second):
    """Require exact counts and relative trips; disclose floating point E1-statistic differences."""
    keys = ("id", "begin", "end", "nVehContrib", "nVehEntered")
    counts_equal = [[row[key] for key in keys] for row in first["counts"]] == [
        [row[key] for key in keys] for row in second["counts"]
    ]
    maximum = max(
        (
            abs(float(a[key]) - float(b[key]))
            for a, b in zip(first["counts"], second["counts"])
            for key in a
            if key not in keys
        ),
        default=0.0,
    )
    trips_equal = first["trips"] == second["trips"]
    return {
        "native_E1_counts_equal": counts_equal,
        "relative_trip_outputs_equal": trips_equal,
        "full_raw_statistics_equal": first["counts"] == second["counts"],
        "maximum_E1_statistic_difference": maximum,
        "continuous_statistic_tolerance": 1e-9,
        "cohort_outputs_equal": counts_equal and trips_equal and maximum <= 1e-9,
    }


def probe_hamburg_readme_control(*, control_dir, demand_dir, field_loops, output_dir, sumo_binary):
    control_dir, demand_dir = Path(control_dir).resolve(strict=True), Path(demand_dir).resolve(strict=True)
    field_loops = Path(field_loops).resolve(strict=True)
    output = Path(output_dir).resolve()
    if output.exists():
        raise ValueError("Use a new control-check directory.")
    output.mkdir(parents=True)
    network, control = control_dir / "inputs/network.xml", control_dir / "control-A.add.xml"
    readback = _readback(network, control, sumo_binary, output)
    write_json_atomic(output / "native-programs.json", readback)
    bindings = json.loads((control_dir / "link-bindings.json").read_bytes())["rows"]
    lanes = {
        lane.get("id"): (edge.get("id"), int(lane.get("index")))
        for edge in ET.parse(network).getroot().findall("edge")
        for lane in edge.findall("lane")
    }
    bound = defaultdict(list)
    for row in bindings:
        links = readback["programs"][row["tls"]]["controlled_links"][row["link_index"]]
        key = tuple(row["physical_key"])
        assert any((*lanes[a], *lanes[b]) == key and via == row["via"] for a, b, via in links)
        for node, group in row["groups"]:
            bound[node, group].append((row["tls"], row["link_index"]))
        if row["tls"] == "6390683126":
            bound["119", "K9"].append((row["tls"], row["link_index"]))
    pair_results = {}
    for node in ("104", "118", "119", "200", "535"):
        tls_ids = {tls for (n, g), members in bound.items() if n == node for tls, index in members}
        boundaries = {0.0, 90.0}
        for tls in tls_ids:
            clock = 0.0
            for phase in readback["programs"][tls]["phases"]:
                clock += phase["duration"]
                boundaries.add(clock)
            assert abs(clock - 90) < 1e-8
        groups = []
        for (n, group), members in bound.items():
            if n != node:
                continue
            intervals = []
            for begin, end in zip(sorted(boundaries), sorted(boundaries)[1:]):
                release = any(
                    _state(readback["programs"][tls]["phases"], begin)[index] in "GgO" for tls, index in members
                )
                intervals.append(
                    {"begin_s": begin, "end_s": end, "securing_states": ["release" if release else "stop"]}
                )
            groups.append({"group_id": group, "status": "measured", "intervals": intervals})
        result = _intergreen_checks(ET.parse(control_dir / f"inputs/ocit_{node}.xml").getroot(), groups, 90)
        bound_groups = {g["group_id"] for g in groups}
        result["bound_rows"] = [
            r for r in result["rows"] if r["clearing_group"] in bound_groups and r["entering_group"] in bound_groups
        ]
        pair_results[node] = result
    write_json_atomic(output / "native-intergreens.json", pair_results)
    profiles = json.loads(gzip.decompress((demand_dir / "ideal-lane-profiles.json.gz").read_bytes()))["profiles"]
    prototype = ET.fromstring(gzip.decompress((demand_dir / "provisional-demand.rou.xml.gz").read_bytes()))
    chosen = {}
    for field in range(13):
        candidates = [p for p in profiles if str(field) in p["field_offsets_s"]]
        p = min(candidates, key=lambda r: r["distance_m"])
        chosen[p["route_id"], tuple(p["entry_lanes"])] = p
    cohort = list(chosen.values())
    outputs = {}
    for begin in (0, 3600):
        case = output / f"begin-{begin}"
        case.mkdir()
        routes = ET.Element("routes")
        routes.append(deepcopy(prototype.find("vType")))
        for index, p in enumerate(cohort):
            vehicle = ET.SubElement(
                routes,
                "vehicle",
                id=f"cohort_{index}",
                type=routes[0].get("id"),
                depart=str(begin + index * 20),
                departLane=str(p["entry_lanes"][0]),
                departPos="5.1",
                departSpeed="max",
            )
            ET.SubElement(vehicle, "route", edges=" ".join(p["edges"]))
        ET.ElementTree(routes).write(case / "demand.rou.xml", encoding="utf-8", xml_declaration=True)
        loops = ET.parse(field_loops).getroot()
        for loop in loops:
            loop.set("file", str(case / "counts.xml"))
            loop.set("begin", str(begin))
            loop.set("end", str(begin + 1800))
        ET.ElementTree(loops).write(case / "e1.add.xml", encoding="utf-8", xml_declaration=True)
        command = [
            str(sumo_binary),
            "-n",
            str(network),
            "-r",
            str(case / "demand.rou.xml"),
            "-a",
            f"{control},{case / 'e1.add.xml'}",
            "--begin",
            str(begin),
            "--end",
            str(begin + 1800),
            "--step-length",
            "0.1",
            "--seed",
            "104",
            "--precision",
            "12",
            "--time-to-teleport",
            "-1",
            "--collision.check-junctions",
            "true",
            "--summary-output",
            str(case / "summary.xml"),
            "--tripinfo-output",
            str(case / "tripinfo.xml"),
            "--tripinfo-output.write-unfinished",
            "true",
            "--vehroute-output",
            str(case / "routes.xml"),
            "--vehroute-output.exit-times",
            "true",
            "--collision-output",
            str(case / "collisions.xml"),
            "--error-log",
            str(case / "errors.log"),
            "--no-step-log",
            "true",
        ]
        executed = run_command(command, cwd=case, timeout_seconds=180)
        write_json_atomic(case / "command.json", executed.to_dict())
        if executed.status != "pass":
            raise RuntimeError("Small control cohort did not finish; inspect its retained log.")
        outputs[begin] = {
            "trips": [dict(v.attrib) for v in ET.parse(case / "tripinfo.xml").getroot().findall("tripinfo")],
            "counts": [dict(v.attrib) for v in ET.parse(case / "counts.xml").getroot().findall("interval")],
            "summary": ET.parse(case / "summary.xml").getroot().findall("step")[-1].attrib,
        }
    for begin, record in outputs.items():
        for row in record["trips"]:
            for key in ("depart", "arrival"):
                row[key] = round(float(row[key]) - begin, 6)
        for row in record["counts"]:
            for key in ("begin", "end"):
                row[key] = round(float(row[key]) - begin, 6)
    equality = compare_clock_outputs(outputs[0], outputs[3600])
    baseline_warnings = {
        line
        for line in network.with_suffix(".load.log").read_text(encoding="utf-8").splitlines()
        if line.startswith("Warning:")
    }
    observed_warnings = {
        line
        for file in (output / "readback.log", output / "begin-0/errors.log", output / "begin-3600/errors.log")
        for line in file.read_text(encoding="utf-8").splitlines()
        if line.startswith("Warning:")
    }
    report = {
        "schema": "astra.hamburg-control-probe/v1",
        "status": "measured",
        "formal_result": False,
        "cohort_vehicles": len(cohort),
        "begin_shift_s": 3600,
        **equality,
        "new_warnings": sorted(observed_warnings - baseline_warnings),
        "cohort_valid": all(
            all(
                int(record["summary"][key]) == value
                for key, value in {
                    "loaded": len(cohort),
                    "inserted": len(cohort),
                    "arrived": len(cohort),
                    "running": 0,
                    "waiting": 0,
                    "collisions": 0,
                    "teleports": 0,
                    "discarded": 0,
                }.items()
            )
            for record in outputs.values()
        ),
        "network_sha256": file_sha256(network),
        "control_sha256": file_sha256(control),
        "bound_intergreen_violations": sum(
            r["status"] == "violated" for v in pair_results.values() for r in v["bound_rows"]
        ),
        "bound_intergreen_unresolved": sum(
            r["status"] == "unresolved" for v in pair_results.values() for r in v["bound_rows"]
        ),
        "cohorts": {str(begin): record["summary"] for begin, record in outputs.items()},
        "claim_boundary": "Native readback and a small clock-shift cohort only. Not a full-window count fit or field-control validation.",
    }
    write_json_atomic(output / "report.json", report)
    return report
