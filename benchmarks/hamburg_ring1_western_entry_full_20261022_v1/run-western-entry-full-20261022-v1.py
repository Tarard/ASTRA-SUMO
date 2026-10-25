"""Measure the exact western-entry candidate through the complete count window."""

from collections import defaultdict
import json
from pathlib import Path
import shutil
import subprocess
from unittest.mock import patch
import xml.etree.ElementTree as ET

import sumo

from astra_sumo.core.artifact_io import write_json_atomic
from astra_sumo.core.candidate_contracts import file_sha256
from astra_sumo.core.command_runner import run_command
from astra_sumo.core.detector_demand_replay import run_point_detector_demand
from astra_sumo.core.hamburg_collision_trace import read_trace_source

ROOT = Path(__file__).resolve().parents[1]
PREFIX = ROOT / 'benchmarks/hamburg_ring1_western_entry_prefix_20261022_v1'
BASE = ROOT / 'benchmarks/hamburg_ring1_primary_entry_full_20261022_v1'
P = ROOT / 'outputs/ring1-western-entry-full-inputs-20261022-v1'
N = ROOT / 'outputs/ring1-western-entry-full-native-20261022-v1'
check = json.loads(read_trace_source(PREFIX / 'analysis', 'prefix-check.json'))
assert check['candidate_completion']['collisions'] == check['candidate_completion']['teleports'] == 0
assert check['native_E1_reconciled'] and check['all_observed_vehicles_once_at_assigned_field']
assert check['requests_due'] == 1953 and check['future_requests_retained'] == 6687
P.mkdir(exist_ok=False)
names = ('network.net.xml', 'demand.rou.xml', 'field-loops.add.xml', 'expected-counts.csv',
         'control.add.xml', 'lane-intentions.json', 'initialization-counts.csv', 'scenario.json', 'western-entry-prior.json')
for name in names:
    (P / name).write_bytes(read_trace_source(PREFIX / 'inputs', name))
(P / 'initialization-prefix-check.json').write_bytes(read_trace_source(PREFIX / 'analysis', 'prefix-check.json'))
for name in ('network.net.xml', 'control.add.xml', 'field-loops.add.xml', 'expected-counts.csv', 'initialization-counts.csv'):
    assert (P / name).read_bytes() == read_trace_source(BASE / 'inputs', name)
vehicles = {v.get('id'): v for v in ET.parse(P / 'demand.rou.xml').getroot().findall('vehicle')}
old = {v.get('id'): v for v in ET.fromstring(read_trace_source(BASE / 'inputs', 'demand.rou.xml')).findall('vehicle')}
assert len(vehicles) == 8640 and vehicles.keys() == old.keys()
assert all(v.attrib == old[identity].attrib for identity, v in vehicles.items())
prior = json.loads((P / 'western-entry-prior.json').read_bytes())
assert prior['changed_by_population'] == {'initialization': 95, 'main': 427}
groups = defaultdict(list)
for row in prior['changed_vehicles']:
    groups[row['new_profile_index'], row['population_role'], row['target_begin_s']].append(row['vehicle_id'])
historical = json.loads(read_trace_source(BASE / 'inputs', 'full-window-proposal.json'))['observed_vehicles']
participants = sorted(set(historical) | {min(ids, key=lambda i: (float(vehicles[i].get('depart')), i)) for ids in groups.values()})
models = ['plugins/astra-sumo/src/astra_sumo/core/' + name for name in (
    'hamburg_entry_prior.py', 'hamburg_readme_results.py', 'lane_section_hold.py', 'simulation_stall.py',
    'detector_demand_replay.py', 'route_sensor_matrix.py')]
binary = Path(sumo.__file__).parent / 'bin/sumo.exe'
for name in models:
    target = P / 'implementation-at-execution' / name
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(ROOT / name, target)
write_json_atomic(P / 'executed-implementation-sha256.json', {name: file_sha256(ROOT / name) for name in models} | {str(binary): file_sha256(binary)})
(P / 'source-commit.txt').write_text(subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True), encoding='utf-8', newline='\n')
write_json_atomic(P / 'full-window-proposal.json', {'formal_result': False, 'end_s': 14400,
    'window_s': [3600, 12600], 'model_vehicles': 8640, 'timeout_seconds': 5400,
    'source_prefix': str(PREFIX), 'from_empty_initialization': True, 'restart_validated': False,
    'change': 'Only the exact tested western half-share: 95 initialization and 427 main road prefixes. All request times, fields, periods, destinations and road suffixes after 234423652#1 remain.',
    'fixed': 'Network, legal permissions and turnarounds, controls, vehicle type, first-field waypoint and three-node driver.',
    'known_prefix_tradeoff': 'E1 contributions fall 1716 to 1670; field MAE rises 4.635 to 6.596; Z15 loses 31 and Z4 loses 13 contributions. All losses are retained.',
    'reason_for_complete_window': 'The 427 changed main requests and later local-entry backlog are not evaluated by the initialization prefix. Keep this exact share for one complete test.',
    'observed_vehicles': participants, 'historical_participants': historical,
    'input_sha256': {name: file_sha256(P / name) for name in names},
    'claim_boundary': 'Safe sparse/prefix responses do not establish main-window safety, useful entry allocation, correct OD or exact count calibration. Saved states are inspection evidence, not validated restarts.'})


def observed_command(command, **kwargs):
    request_file = Path(command[-1])
    request = json.loads(request_file.read_bytes())
    request['command'].extend(['--lanechange-output', str(N / 'lanechanges.xml'),
        '--fcd-output', str(N / 'participants-fcd.xml'), '--device.fcd.probability', '0',
        '--device.fcd.explicit', ','.join(participants), '--fcd-output.skip-empty', 'true'])
    write_json_atomic(request_file, request)
    return run_command(command, **kwargs)


with patch('astra_sumo.core.detector_demand_replay.run_command', observed_command):
    result = run_point_detector_demand(net_file=P / 'network.net.xml', route_file=P / 'demand.rou.xml',
        e1_additional_file=P / 'field-loops.add.xml', expected_counts_csv=P / 'expected-counts.csv', output_dir=N,
        end_s=14400, sumo_binary=str(binary), timeout_seconds=5400, state_times_s=(14399.9,),
        signal_program_file=P / 'control.add.xml', lane_intentions_file=P / 'lane-intentions.json',
        junction_body_hold=True, junction_body_hold_nodes=('14398547', '205069138', '18653534'))
print(json.dumps({k: result.get(k) for k in ('status', 'native_status', 'healthy', 'completion', 'observer_complete', 'exact_field_bins', 'missing_field_bins')}), flush=True)
