"""Measure one bounded request-time correction in the initialization hour."""

from collections import Counter
import gzip
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import xml.etree.ElementTree as ET

import sumo

from astra_sumo.core.artifact_io import write_json_atomic
from astra_sumo.core.detector_demand_replay import run_point_detector_demand
from astra_sumo.core.hamburg_collision_trace import read_trace_source

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / 'benchmarks/hamburg_ring1_primary_entry_full_20261022_v1'
WINDOWS = ROOT / 'outputs/ring1-native-departure-windows-20261022-v2/primary/all-vehicle-windows-margin90.json.gz'
P = ROOT / 'outputs/ring1-native-time-correction-inputs-20261022-v1'
OUT = ROOT / 'outputs/ring1-native-time-correction-prefix-20261022-v1'
P.mkdir(exist_ok=False)
names = ('network.net.xml', 'demand.rou.xml', 'field-loops.add.xml', 'expected-counts.csv',
         'control.add.xml', 'lane-intentions.json', 'initialization-counts.csv', 'scenario.json')
original = {name: read_trace_source(BASE / 'inputs', name) for name in names}
for name, raw in original.items():
    (P / name).write_bytes(raw)
windows = json.loads(gzip.decompress(WINDOWS.read_bytes()))
shifts = {r['vehicle_id']: r for r in windows}
demand = ET.fromstring(original['demand.rou.xml'])
vehicles = demand.findall('vehicle')
intentions = json.loads(original['lane-intentions.json'])
intent = {r['vehicle_id']: r for r in intentions['vehicles']}
assert len(vehicles) == len(intent) == len(shifts) == 8640
changes = []
for vehicle in vehicles:
    identity = vehicle.get('id')
    correction = shifts[identity]
    if correction['proposed_shift_s'] in (None, 0):
        continue
    before = float(vehicle.get('depart'))
    assert -.900001 <= correction['proposed_shift_s'] / 1000 <= 0
    assert correction['proposed_depart_s'] >= 0
    vehicle.set('depart', f"{correction['proposed_depart_s']:.3f}")
    intent[identity]['depart_s'] = correction['proposed_depart_s']
    changes.append({'vehicle_id': identity, 'before_requested_depart_s': before,
        'after_requested_depart_s': correction['proposed_depart_s'],
        'native_step_shift_s': correction['proposed_shift_s']})
assert len(changes) == 2094
for vehicle in vehicles:
    demand.remove(vehicle)
for _, vehicle in sorted(enumerate(vehicles), key=lambda item: (float(item[1].get('depart')), item[0])):
    demand.append(vehicle)
ET.ElementTree(demand).write(P / 'demand.rou.xml', encoding='utf-8', xml_declaration=True)
write_json_atomic(P / 'lane-intentions.json', intentions)
scenario = json.loads(original['scenario.json'])
scenario.update(demand_sha256=hashlib.sha256((P / 'demand.rou.xml').read_bytes()).hexdigest(),
    time_prior='One bounded frozen-native-lag trial. Minimal whole-step advances within 900 s and all assigned target windows with a 90 s margin. Keep missing or blocked estimates unchanged. No count feedback during simulation. New queues and count response remain unverified.')
write_json_atomic(P / 'scenario.json', scenario)
old_vehicles = {v.get('id'): v for v in ET.fromstring(original['demand.rou.xml']).findall('vehicle')}
assert {v.get('id') for v in vehicles} == set(old_vehicles)
for vehicle in vehicles:
    old = old_vehicles[vehicle.get('id')]
    assert {k: v for k, v in vehicle.attrib.items() if k != 'depart'} == {k: v for k, v in old.attrib.items() if k != 'depart'}
    assert [ET.tostring(child) for child in vehicle] == [ET.tostring(child) for child in old]
assert all(read_trace_source(BASE / 'inputs', name) == raw for name, raw in original.items())
assert all((P / name).read_bytes() == original[name] for name in names if name not in ('demand.rou.xml', 'lane-intentions.json', 'scenario.json'))
models = ['plugins/astra-sumo/src/astra_sumo/core/' + name for name in (
    'native_temporal_feedback.py', 'detector_demand_replay.py', 'lane_section_hold.py', 'simulation_stall.py', 'route_sensor_matrix.py')]
binary = Path(sumo.__file__).parent / 'bin/sumo.exe'
for name in models:
    destination = P / 'implementation-at-execution' / name
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(ROOT / name, destination)
write_json_atomic(P / 'executed-implementation-sha256.json', {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in models}
    | {str(binary): hashlib.sha256(binary.read_bytes()).hexdigest()})
write_json_atomic(P / 'time-correction-proposal.json', {'formal_result': False, 'source_case': str(BASE),
    'source_commit': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
    'model_vehicles': 8640, 'target_field_visits': 8640, 'changed_requests': changes,
    'max_shift_s': 900, 'guard_s': 90, 'step_s': .1, 'end_s': 3600, 'new_full_window_runs': 0,
    'source_window_sha256': hashlib.sha256(WINDOWS.read_bytes()).hexdigest(),
    'unknown_or_blocked_requests_unchanged': True, 'unchanged_status_counts': dict(Counter(r['status'] for r in windows if r['proposed_shift_s'] is None)),
    'input_sha256': {name: hashlib.sha256((P / name).read_bytes()).hexdigest() for name in names},
    'claim_boundary': 'Only request-time input changes. Source native lags are frozen proposals, not measured responses to the changed traffic. One initialization trial, not zero-error calibration or proof against other schedules.'})
run = run_point_detector_demand(net_file=P / 'network.net.xml', route_file=P / 'demand.rou.xml',
    e1_additional_file=P / 'field-loops.add.xml', expected_counts_csv=P / 'expected-counts.csv', output_dir=OUT,
    end_s=3600, sumo_binary=str(binary), timeout_seconds=2400,
    signal_program_file=P / 'control.add.xml', lane_intentions_file=P / 'lane-intentions.json',
    junction_body_hold=True, junction_body_hold_nodes=('14398547', '205069138', '18653534'))
print(json.dumps({k: run.get(k) for k in ('status', 'native_status', 'healthy', 'completion', 'observer_complete', 'missing_field_bins', 'exact_field_bins')}), flush=True)
