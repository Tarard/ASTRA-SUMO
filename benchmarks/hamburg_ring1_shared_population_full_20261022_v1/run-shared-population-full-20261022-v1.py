"""Measure the same shared-demand alternative over the complete real count window."""

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

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'outputs/ring1-shared-population-inputs-20261022-v2'
CHECK = ROOT / 'outputs/ring1-shared-population-prefix-analysis-20261022-v1/prefix-check.json'
P = ROOT / 'outputs/ring1-shared-population-full-inputs-20261022-v1'
OUT = ROOT / 'outputs/ring1-shared-population-full-native-20261022-v1'
checked = json.loads(CHECK.read_bytes())
assert checked['candidate_completion']['collisions'] == checked['candidate_completion']['teleports'] == 0
assert checked['native_E1_reconciled'] and not any(checked[k] for k in ('unexpected_fields', 'repeated_same_field', 'second_without_first', 'wrong_field_order'))
assert checked['observed_vehicles_with_both_fields'] == 105
P.mkdir(exist_ok=False)
names = ('network.net.xml', 'demand.rou.xml', 'field-loops.add.xml', 'expected-counts.csv',
         'control.add.xml', 'lane-intentions.json', 'initialization-counts.csv', 'scenario.json', 'shared-population.json', 'target-ledger.csv')
for name in names:
    shutil.copyfile(SOURCE / name, P / name)
shutil.copyfile(CHECK, P / 'initialization-prefix-check.json')
models = ['plugins/astra-sumo/src/astra_sumo/core/' + name for name in (
    'hamburg_shared_population.py', 'hamburg_readme_results.py', 'lane_section_hold.py',
    'simulation_stall.py', 'detector_demand_replay.py', 'route_sensor_matrix.py')]
for name in models:
    target = P / 'implementation-at-execution' / name
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(ROOT / name, target)
binary = Path(sumo.__file__).parent / 'bin/sumo.exe'
write_json_atomic(P / 'executed-implementation-sha256.json', {name: file_sha256(ROOT / name) for name in models} | {str(binary): file_sha256(binary)})
(P / 'source-commit.txt').write_text(subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True), encoding='utf-8', newline='\n')
vehicles = {v.get('id'): v for v in ET.parse(P / 'demand.rou.xml').getroot().findall('vehicle')}
intentions = json.loads((P / 'lane-intentions.json').read_bytes())['vehicles']
groups = defaultdict(list)
for row in intentions:
    if len(row['expected_field_ids']) == 2:
        groups[row['field_id'], row['target_begin_s']].append(row['vehicle_id'])
representatives = {min(values, key=lambda v: (float(vehicles[v].get('depart')), v)) for values in groups.values()}
baseline = ROOT / 'benchmarks/hamburg_ring1_primary_entry_full_20261022_v1'
historical = json.loads((baseline / 'inputs/full-window-proposal.json').read_bytes())['observed_vehicles']
representatives.update(identity for identity in historical if identity in vehicles)
pairings = json.loads((P / 'shared-population.json').read_bytes())['pairings']
removed_mapping = {r['removed_model_vehicle_id']: r['parent_vehicle_id'] for r in pairings if r['removed_model_vehicle_id'] in historical}
write_json_atomic(P / 'full-window-proposal.json', {'formal_result': False, 'end_s': 14400, 'window_s': [3600, 12600],
    'model_vehicles': 8185, 'shared_pairs': 455, 'target_field_contributions': {'initialization': 1953, 'main': 6687},
    'timeout_seconds': 3600, 'from_empty_initialization': True, 'restart_validated': False,
    'change': 'Only the tested distinct-field shared demand. Keep all real observation rows and every retained vehicle request time, road prefix through the second observed road and first-field waypoint. Shared trips adopt their second target destination.',
    'fixed': 'Network, legal permissions and turnarounds, control inputs, vehicle type, native E1 locations, three-node body hold and absolute count window.',
    'known_prefix_tradeoff': '119 Z4 loses four contributions and 535 Z6 loses one; exact initialization rows fall 34 to 32. All losses retained in initialization-prefix-check.json.',
    'observed_vehicles': sorted(representatives), 'historical_removed_model_ids_target_mapping': removed_mapping,
    'input_sha256': {name: file_sha256(P / name) for name in names},
    'claim_boundary': 'Exact target-ledger conservation and the initialization prefix are insufficient for main-window calibration. Synthetic source IDs removed by pairing keep their observation targets; they are not real identified vehicles. All new native counts, safety and insertion must be measured.'})


def observed_command(command, **kwargs):
    request_file = Path(command[-1])
    request = json.loads(request_file.read_bytes())
    request['command'].extend(['--lanechange-output', str(OUT / 'lanechanges.xml'),
        '--fcd-output', str(OUT / 'representatives-fcd.xml'), '--device.fcd.probability', '0',
        '--device.fcd.explicit', ','.join(sorted(representatives)), '--fcd-output.skip-empty', 'true'])
    write_json_atomic(request_file, request)
    return run_command(command, **kwargs)


with patch('astra_sumo.core.detector_demand_replay.run_command', observed_command):
    run = run_point_detector_demand(net_file=P / 'network.net.xml', route_file=P / 'demand.rou.xml',
        e1_additional_file=P / 'field-loops.add.xml', expected_counts_csv=P / 'expected-counts.csv', output_dir=OUT,
        end_s=14400, sumo_binary=str(binary), timeout_seconds=3600, state_times_s=(14399.9,),
        signal_program_file=P / 'control.add.xml', lane_intentions_file=P / 'lane-intentions.json',
        junction_body_hold=True, junction_body_hold_nodes=('14398547', '205069138', '18653534'))
print(json.dumps({k: run.get(k) for k in ('status', 'native_status', 'healthy', 'completion', 'observer_complete', 'missing_field_bins', 'exact_field_bins')}), flush=True)
