"""Read each installed active program before the unchanged native monitor starts."""

import json
from pathlib import Path
import sys

import traci

from astra_sumo.core.artifact_io import write_json_atomic
from astra_sumo.core.simulation_stall import monitor_sumo

request = json.loads(Path(sys.argv[1]).read_bytes())
original_connect = traci.connect


def inspected_connect(*args, **kwargs):
    connection = original_connect(*args, **kwargs)
    before = connection.simulation.getTime()
    rows = []
    for identity in connection.trafficlight.getIDList():
        active = connection.trafficlight.getProgram(identity)
        logic = next(p for p in connection.trafficlight.getAllProgramLogics(identity) if p.programID == active)
        rows.append({'tls': identity, 'active_program': active, 'native_type': logic.type,
                     'cycle_s': sum(p.duration for p in logic.phases)})
    assert connection.simulation.getTime() == before
    assert len(rows) == 68 and all(r['native_type'] == 0 and r['cycle_s'] == 90 for r in rows)
    write_json_atomic(Path(request['monitor_file']).parent / 'installed-programs.json', {
        'formal_result': False, 'time_before_s': before, 'time_after_s': connection.simulation.getTime(),
        'all_active_programs_static': True, 'rows': rows,
        'claim_boundary': 'Native installation correspondence only. It does not establish field control truth or calibrated traffic.'})
    return connection


traci.connect = inspected_connect
monitor_sumo(request)
