"""Read one native snapshot without advancing the simulation or changing source files."""

import gzip
import json
import os
from pathlib import Path
import subprocess
import xml.etree.ElementTree as ET

import sumo
import traci
from sumolib.miscutils import getFreeSocketPort

from astra_sumo.core.artifact_io import write_json_atomic
from astra_sumo.core.candidate_contracts import file_sha256
from astra_sumo.core.command_runner import _terminate_process_tree
from astra_sumo.core.hamburg_collision_trace import read_trace_source
from astra_sumo.core.simulation_stall import stopped_vehicle_report

ROOT = Path(__file__).resolve().parents[1]
CASE = ROOT / 'benchmarks/hamburg_ring1_primary_entry_full_20261022_v1'
OUT = ROOT / 'outputs/ring1-primary-entry-terminal-state-20261022-v1'
OUT.mkdir(exist_ok=False)
state_file = CASE / 'native/states/state_14399.900.xml.gz'
net_file, control_file = CASE / 'inputs/network.net.xml', CASE / 'inputs/control.add.xml'
sources = {str(p): file_sha256(p) for p in (state_file, net_file, control_file, CASE / 'native/field-counts.xml')}
state = ET.fromstring(gzip.decompress(state_file.read_bytes()))
saved_vehicles = {v.get('id'): v for v in state.findall('vehicle')}
saved_routes = {v.get('id'): v.get('edges').split() for v in state.findall('route')}
saved_lanes = {identity: lane.get('id') for lane in state.findall('lane')
               for identity in lane.find('vehicles').get('value', '').split()}
saved_active_tls = {row.get('id'): row.attrib for row in state.findall('tlLogic') if row.get('active') == '1'}
assignments = {r['vehicle_id']: r for r in json.loads(read_trace_source(CASE / 'inputs', 'lane-intentions.json'))['vehicles']}
monitor = json.loads(read_trace_source(CASE / 'native', 'stall-monitor.json'))
snapshot_time = float(state.get('time'))
active_holds = {}
for name, events in [('field', monitor['lane_section_hold']['events']),
                     ('junction', monitor['lane_section_hold']['junction_body_hold']['events'])]:
    held = set()
    for event in events:
        if event['time_s'] > snapshot_time:
            continue
        if event['event'] == 'held':
            held.add(event['vehicle_id'])
        else:
            held.discard(event['vehicle_id'])
    active_holds[name] = held
port = getFreeSocketPort()
command = [str(Path(sumo.__file__).parent / 'bin/sumo.exe'), '--net-file', str(net_file),
    '--additional-files', str(control_file), '--load-state', str(state_file), '--step-length', '.1',
    '--precision', '12', '--time-to-teleport', '-1', '--collision.check-junctions', 'true',
    '--no-step-log', 'true', '--log', str(OUT / 'sumo.log'), '--error-log', str(OUT / 'sumo-errors.log'),
    '--remote-port', str(port)]
write_json_atomic(OUT / 'query-proposal.json', {'formal_result': False, 'source_case': str(CASE),
    'command': command, 'source_sha256': sources, 'planned_simulation_steps': 0,
    'claim_boundary': 'Loaded native state inspection only. No simulationStep, driver commands, detector remeasurement or calibrated restart. Python hold ownership is reconstructed from recorded events, not restarted.'})
connection = None
with (OUT / 'process.log').open('w', encoding='utf-8') as stream:
    process = subprocess.Popen(command, stdout=stream, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
    try:
        connection = traci.connect(port, numRetries=3, proc=process)
        before = connection.simulation.getTime()
        assert abs(before - snapshot_time) < 1e-6
        identities = set(connection.vehicle.getIDList())
        assert identities == set(saved_lanes)
        observation = stopped_vehicle_report(connection, identities)
        mode_mismatches, state_mismatches = [], []
        for identity, row in observation['vehicles'].items():
            vehicle = connection.vehicle
            row.update(waiting_time_s=vehicle.getWaitingTime(identity), lane_change_mode=vehicle.getLaneChangeMode(identity),
                route_index=vehicle.getRouteIndex(identity), route=list(vehicle.getRoute(identity)),
                best_lanes=vehicle.getBestLanes(identity), stop_state=vehicle.getStopState(identity),
                assigned_field=assignments[identity]['field_id'],
                recorded_hold_reasons=[name for name, held in active_holds.items() if identity in held])
            row['controller_expected_mode'] = 512 if row['recorded_hold_reasons'] else 1621
            if row['lane_change_mode'] != row['controller_expected_mode']:
                mode_mismatches.append(identity)
            saved = saved_vehicles[identity]
            if (row['lane'] != saved_lanes[identity]
                    or abs(row['position_m'] - float(saved.get('pos').split()[0])) > 1e-6
                    or abs(row['speed_m_s'] - float(saved.get('speed').split()[0])) > 1e-6
                    or row['route'] != saved_routes[saved.get('route')]):
                state_mismatches.append(identity)
        assert not state_mismatches
        traffic_lights = {}
        for identity in connection.trafficlight.getIDList():
            tls = connection.trafficlight
            traffic_lights[identity] = {'program': tls.getProgram(identity), 'phase': tls.getPhase(identity),
                'state': tls.getRedYellowGreenState(identity), 'next_switch_s': tls.getNextSwitch(identity),
                'controlled_links': tls.getControlledLinks(identity)}
        tls_mismatches = [identity for identity, saved in saved_active_tls.items()
            if traffic_lights[identity]['program'] != saved['programID'] or traffic_lights[identity]['phase'] != int(saved['phase'])]
        pending = list(connection.simulation.getPendingVehicles())
        after = connection.simulation.getTime()
        assert before == after and not tls_mismatches
        observation.update(formal_result=False, snapshot_time_s=before, time_after_query_s=after,
            simulation_steps=0, native_version=connection.getVersion(), traci_module=traci.__file__,
            running_vehicles=len(identities), pending_vehicle_ids=pending, traffic_lights=traffic_lights,
            state_mismatches=state_mismatches, loaded_mode_mismatches_with_recorded_controller=mode_mismatches,
            tls_program_or_phase_mismatches=tls_mismatches,
            claim_boundary='Lane, position, speed, routes, active TLS program/phase and time checked against the snapshot. Loaded driver modes and internal approach caches may differ from the original live controller. These queries do not establish absence of an original-runtime conflict, causation, new counts, or restart validity.')
        write_json_atomic(OUT / 'native-state-query.json', observation)
    finally:
        if connection is not None:
            connection.close(wait=False)
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            _terminate_process_tree(process)
assert process.returncode == 0
assert all(file_sha256(Path(p)) == sha for p, sha in sources.items())
print(json.dumps({'time_s': before, 'running': len(identities), 'pending': len(pending),
    'steps': 0, 'state_mismatches': len(state_mismatches), 'loaded_mode_mismatches': len(mode_mismatches),
    'candidate_wait_cycles': len(observation['candidate_wait_cycles']), 'sources_unchanged': True}), flush=True)
