"""Verify the complete unsafe run without replacing any native measurement."""

import json
import shutil
from pathlib import Path
import xml.etree.ElementTree as ET

from astra_sumo.core.artifact_io import write_json_atomic
from astra_sumo.core.candidate_contracts import file_sha256
from astra_sumo.core.hamburg_collision_trace import read_trace_source

ROOT = Path(__file__).resolve().parents[1]
CASE = ROOT / 'benchmarks/hamburg_ring1_localized_junction_body_full_20261021_v1'
SOURCE = ROOT / 'benchmarks/hamburg_ring1_localized_junction_body_20261021_v1'
result = json.loads(read_trace_source(CASE, 'results.json'))
monitor = json.loads(read_trace_source(CASE / 'native', 'stall-monitor.json'))
assert monitor['status'] == 'completed' and monitor['simulation_time_s'] == 14400
assert not result['valid_run'] and result['completion']['collisions'] == result['completion']['teleports'] == 1
fixed = ('network.net.xml', 'demand.rou.xml', 'field-loops.add.xml', 'expected-counts.csv', 'control.add.xml', 'lane-intentions.json', 'initialization-counts.csv')
assert all(read_trace_source(CASE / 'inputs', name) == read_trace_source(SOURCE / 'inputs', name) for name in fixed)
assert json.loads(read_trace_source(CASE / 'inputs', 'scenario.json'))['junction_body_hold_nodes'] == ['14398547', '205069138']
events = ET.fromstring(read_trace_source(CASE / 'native', 'collisions.xml')).findall('collision')
assert len(events) == 1 and float(events[0].get('time')) == 9402.6
snapshot = CASE / 'native/states/state_14399.900.xml.gz'
assert file_sha256(snapshot) == 'fc34787300e54a4613d93b353850216d4d273de69bf2a8d4014abacc6756a8b6'
figure_source = ROOT / 'benchmarks/hamburg_ring1_junction_body_full_20261021_v1/figure-provenance.json'
figure = json.loads(figure_source.read_bytes())
assert figure['current_network_sha256'] == file_sha256(CASE / 'inputs/network.net.xml') == result['network_sha256']
shutil.copyfile(figure_source, CASE / 'figure-provenance.json')
write_json_atomic(CASE / 'execution-outcome.json', {
    'native_reached_planned_end': True, 'native_end_s': 14400, 'all_vehicle_requests_completed': False,
    'native_command_status': 'pass', 'timeout': False, 'formal_result': False,
    'completion': result['completion'], 'all_fixed_traffic_inputs_match_6000s_prefix': True,
    'collision_time_s': 9402.6, 'diagnostic_state_sha256': file_sha256(snapshot),
    'claim_boundary': 'Complete unsafe native measurement. The state does not establish validated restart of the Python driver controller.'})
shutil.copyfile(Path(__file__), CASE / 'verify-localized-body-full.py')
print(json.dumps({'fixed_inputs_unchanged': True, 'native_completed': True, 'valid_run': False, 'figures_match_network': True}))
