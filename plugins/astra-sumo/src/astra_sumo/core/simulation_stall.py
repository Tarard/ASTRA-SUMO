"""Stop a native replay after continuous standstill of all running vehicles."""

import json
import math
import os
from pathlib import Path
import subprocess
import sys

from .artifact_io import write_json_atomic
from .candidate_contracts import file_sha256
from .command_runner import _terminate_process_tree


def stopped_vehicle_report(connection, identities):
    """Retain native leader and yield relations, including candidate waiting cycles."""
    from scipy.sparse import csr_matrix
    from scipy.sparse.csgraph import connected_components

    vehicles, relations = {}, []
    indices = {identity: index for index, identity in enumerate(sorted(identities))}
    for identity in indices:
        vehicle = connection.vehicle
        leader = vehicle.getLeader(identity, 10)
        foes = vehicle.getJunctionFoes(identity, 10)
        vehicles[identity] = {"lane": vehicle.getLaneID(identity), "position_m": vehicle.getLanePosition(identity),
            "speed_m_s": vehicle.getSpeed(identity), "leader": leader,
            "junction_foes": foes, "next_links": vehicle.getNextLinks(identity), "next_signals": vehicle.getNextTLS(identity)}
        if leader and leader[0] in indices and leader[1] <= vehicle.getMinGap(identity) + 1:
            relations.append({"vehicle": identity, "waits_for": leader[0], "kind": "near_leader"})
        for foe in foes:
            if foe[0] in indices and foe[7] and foe[2] <= 10:
                relations.append({"vehicle": identity, "waits_for": foe[0], "kind": "junction_yield"})
    rows = [indices[row["vehicle"]] for row in relations]
    columns = [indices[row["waits_for"]] for row in relations]
    graph = csr_matrix(([1] * len(rows), (rows, columns)), shape=(len(indices), len(indices)))
    count, labels = connected_components(graph, directed=True, connection="strong")
    cycles = []
    for label in range(count):
        members = [identity for identity, index in indices.items() if labels[index] == label]
        if len(members) > 1:
            cycles.append({"vehicles": members, "lanes": sorted({vehicles[v]["lane"] for v in members})})
    return {"vehicles": vehicles, "blocked_lanes": sorted({row["lane"] for row in vehicles.values()}),
        "waiting_relations": relations, "candidate_wait_cycles": cycles,
        "cycle_claim": "Native near-leader and yield relations suggest cycles. They do not prove the cause of standstill."}


def monitor_sumo(request):
    """Monitor standstill and apply any explicitly declared local lane holds."""
    import traci
    from traci import constants as tc
    from sumolib.miscutils import getFreeSocketPort

    backend = request.get("backend", "traci")
    if backend not in {"traci", "libsumo"}:
        raise ValueError("Use the traci or libsumo backend.")
    limit = request["all_stopped_timeout_s"]
    interval = request.get("monitor_interval_s")
    driver = None
    if request.get("junction_body_hold") and not request.get("lane_intentions_file"):
        raise ValueError("Provide lane intentions for the junction-body hold.")
    if request.get("lane_intentions_file"):
        from .lane_section_hold import LaneSectionHold, read_lane_intentions
        import xml.etree.ElementTree as ET
        identities = {r.get("id") for r in ET.parse(request["route_file"]).getroot().findall("vehicle")}
        internal_lengths = None
        if request.get("junction_body_hold", False):
            network = ET.parse(request["command"][request["command"].index("--net-file") + 1]).getroot()
            nodes = request.get("junction_body_hold_nodes")
            if nodes is not None and (not nodes or not set(nodes) <= {j.get("id") for j in network.findall("junction")}):
                raise ValueError("Use existing junction IDs for the declared body-hold scope.")
            internal_lengths = {lane.get("id"): float(lane.get("length"))
                for edge in network.findall("edge") if edge.get("function") == "internal"
                and (nodes is None or any(edge.get("id").startswith(":" + node + "_") for node in nodes))
                for lane in edge.findall("lane")}
        driver = LaneSectionHold(read_lane_intentions(request["lane_intentions_file"], request["e1_file"], identities),
                                 junction_lane_lengths=internal_lengths)
        if interval is not None:
            raise ValueError("Use every native simulation step for a lane-section hold.")
    if interval is not None and (not math.isfinite(interval) or interval <= 0 or interval > limit):
        raise ValueError("Use a positive monitoring interval within the standstill limit.")
    if not math.isfinite(limit) or limit <= 0:
        raise ValueError("Use a positive finite standstill limit.")
    output = Path(request["monitor_file"])
    command = list(request["command"])
    if backend == "traci":
        command.extend(["--remote-port", str(getFreeSocketPort())])
    report = {"status": "running", "all_stopped_timeout_s": limit, "speed_tolerance_m_s": 1e-6,
        "command": command, "backend": backend, "traci_module": traci.__file__, "stop": None,
        "monitor_interval_s": interval, "position_changes_reset_standstill": interval is not None or driver is not None}
    if request.get("junction_body_hold_nodes") is not None:
        report["junction_body_hold_nodes"] = request["junction_body_hold_nodes"]
    write_json_atomic(output, report)
    process = None
    connection = None
    try:
        if backend == "libsumo":
            import libsumo
            from libsumo import _libsumo

            library = Path(_libsumo.__file__).resolve()
            if not library.is_relative_to(Path(sys.prefix).resolve()) or libsumo.__version__ != traci.__version__:
                raise ValueError("Use matching project TraCI and Libsumo installations.")
            report.update(api_module=libsumo.__file__, native_library={"path": str(library), "sha256": file_sha256(library)})
            libsumo.start(command)
            connection = libsumo
        else:
            process = subprocess.Popen(command, stdin=subprocess.DEVNULL, start_new_session=os.name != "nt",
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
            connection = traci.connect(int(command[-1]), numRetries=3, proc=process)
        report["native_version"] = connection.getVersion()
        connection.simulation.subscribe([tc.VAR_TIME, tc.VAR_DEPARTED_VEHICLES_IDS])
        now = connection.simulation.getTime()
        stopped_since = None
        previous_positions = {}
        while now < request["end_s"]:
            connection.simulationStep(min(request["end_s"], now + interval) if interval is not None else 0)
            simulation = connection.simulation.getSubscriptionResults()
            now = simulation[tc.VAR_TIME]
            departed = simulation[tc.VAR_DEPARTED_VEHICLES_IDS]
            if interval is not None:
                departed = set(connection.vehicle.getIDList()) - previous_positions.keys()
            for identity in departed:
                variables = ([tc.VAR_SPEED, tc.VAR_LANE_ID, tc.VAR_LANEPOSITION, tc.VAR_DISTANCE] if driver else
                             [tc.VAR_SPEED, tc.VAR_LANE_ID, tc.VAR_LANEPOSITION] if interval is not None else [tc.VAR_SPEED])
                connection.vehicle.subscribe(identity, variables)
            speeds = connection.vehicle.getAllSubscriptionResults()
            if driver:
                driver.step(connection.vehicle, speeds, now)
            positions = ({identity: (row[tc.VAR_LANE_ID], row[tc.VAR_LANEPOSITION]) for identity, row in speeds.items()}
                         if interval is not None or driver else previous_positions)
            if departed or positions != previous_positions or not speeds or any(row[tc.VAR_SPEED] > 1e-6 for row in speeds.values()):
                stopped_since = None
            elif stopped_since is None:
                stopped_since = now
            previous_positions = positions
            if stopped_since is not None and now - stopped_since >= limit - 1e-9:
                state = output.with_name("all-stopped-state.xml.gz")
                connection.simulation.saveState(str(state))
                report.update(status="all_stopped", stop={"time_s": now, "stopped_since_s": stopped_since,
                    "duration_s": now - stopped_since, "state": {"path": str(state), "sha256": file_sha256(state)},
                    **stopped_vehicle_report(connection, speeds)})
                write_json_atomic(output, report)
                break
        else:
            report["status"] = "completed"
        report["simulation_time_s"] = now
    except Exception as error:
        report.update(status="failed", error=f"{type(error).__name__}: {error}")
        raise
    finally:
        try:
            if connection is not None:
                if backend == "libsumo":
                    connection.close()
                else:
                    connection.close(wait=False)
        finally:
            if process is not None:
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    _terminate_process_tree(process)
            report["native_returncode"] = process.returncode if process is not None else None
            report["library_completed"] = backend == "libsumo" and report["status"] == "completed"
            if driver:
                report["lane_section_hold"] = driver.report()
            write_json_atomic(output, report)
    if process is not None and process.returncode != 0:
        raise RuntimeError(f"SUMO exited with code {process.returncode}.")


if __name__ == "__main__":
    monitor_sumo(json.loads(Path(sys.argv[1]).read_text(encoding="utf-8")))
