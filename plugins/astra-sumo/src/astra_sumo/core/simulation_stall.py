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
    """Observe speed at every SUMO step without changing vehicle or signal control."""
    import traci
    from traci import constants as tc
    from sumolib.miscutils import getFreeSocketPort

    limit = request["all_stopped_timeout_s"]
    if not math.isfinite(limit) or limit <= 0:
        raise ValueError("Use a positive finite standstill limit.")
    output = Path(request["monitor_file"])
    command = [*request["command"], "--remote-port", str(getFreeSocketPort())]
    report = {"status": "running", "all_stopped_timeout_s": limit, "speed_tolerance_m_s": 1e-6,
        "command": command, "traci_module": traci.__file__, "stop": None}
    write_json_atomic(output, report)
    process = subprocess.Popen(command, stdin=subprocess.DEVNULL, start_new_session=os.name != "nt",
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    connection = None
    try:
        connection = traci.connect(int(command[-1]), numRetries=3, proc=process)
        report["native_version"] = connection.getVersion()
        connection.simulation.subscribe([tc.VAR_TIME, tc.VAR_DEPARTED_VEHICLES_IDS])
        now = connection.simulation.getTime()
        stopped_since = None
        while now < request["end_s"]:
            connection.simulationStep()
            simulation = connection.simulation.getSubscriptionResults()
            now = simulation[tc.VAR_TIME]
            departed = simulation[tc.VAR_DEPARTED_VEHICLES_IDS]
            for identity in departed:
                connection.vehicle.subscribe(identity, [tc.VAR_SPEED])
            speeds = connection.vehicle.getAllSubscriptionResults()
            if departed or not speeds or any(row[tc.VAR_SPEED] > 1e-6 for row in speeds.values()):
                stopped_since = None
            elif stopped_since is None:
                stopped_since = now
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
                connection.close(wait=False)
        finally:
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                _terminate_process_tree(process)
            report["native_returncode"] = process.returncode
            write_json_atomic(output, report)
    if process.returncode != 0:
        raise RuntimeError(f"SUMO exited with code {process.returncode}.")


if __name__ == "__main__":
    monitor_sumo(json.loads(Path(sys.argv[1]).read_text(encoding="utf-8")))
