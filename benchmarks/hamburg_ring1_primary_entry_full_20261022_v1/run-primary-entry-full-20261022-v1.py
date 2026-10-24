"""Measure the same tested entry prior through the complete real-count window."""

import json
from pathlib import Path
import shutil
import subprocess
from unittest.mock import patch

import sumo

from astra_sumo.core.artifact_io import write_json_atomic
from astra_sumo.core.candidate_contracts import file_sha256
from astra_sumo.core.command_runner import run_command
from astra_sumo.core.detector_demand_replay import run_point_detector_demand

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'outputs/ring1-primary-entry-prior-inputs-20261022-v2'
CHECK = ROOT / 'outputs/ring1-primary-entry-prior-analysis-20261022-v1/prefix-check.json'
P = ROOT / 'outputs/ring1-primary-entry-prior-full-inputs-20261022-v1'
OUT = ROOT / 'outputs/ring1-primary-entry-prior-full-native-20261022-v1'
checked = json.loads(CHECK.read_bytes())
assert checked['candidate_completion']['collisions'] == checked['candidate_completion']['teleports'] == 0
assert checked['native_E1_reconciled'] and checked['all_observed_vehicles_once_at_assigned_field']
assert checked['candidate_E1_total'] > checked['baseline_E1_total']
P.mkdir(exist_ok=False)
names = ('network.net.xml', 'demand.rou.xml', 'field-loops.add.xml', 'expected-counts.csv',
         'control.add.xml', 'lane-intentions.json', 'initialization-counts.csv', 'scenario.json', 'entry-prior.json')
for name in names:
    shutil.copyfile(SOURCE / name, P / name)
shutil.copyfile(CHECK, P / 'initialization-prefix-check.json')
models = ['plugins/astra-sumo/src/astra_sumo/core/' + name for name in (
    'hamburg_entry_prior.py', 'lane_section_hold.py', 'simulation_stall.py', 'detector_demand_replay.py')]
for name in models:
    target = P / 'implementation-at-execution' / name
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(ROOT / name, target)
binary = Path(sumo.__file__).parent / 'bin/sumo.exe'
write_json_atomic(P / 'executed-implementation-sha256.json', {n: file_sha256(ROOT / n) for n in models} | {str(binary): file_sha256(binary)})
(P / 'source-commit.txt').write_text(subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True), encoding='utf-8', newline='\n')
old_proposal = json.loads((ROOT / 'benchmarks/hamburg_ring1_three_node_body_full_20261022_v1/inputs/full-window-proposal.json').read_bytes())
participants = old_proposal['observed_vehicles']
write_json_atomic(P / 'full-window-proposal.json', {'formal_result': False,
    'source_prefix': str(SOURCE), 'change': 'Only the tested 3:1 Z12 entry prior: 73 initialization and 221 main requests use the existing primary route prefix.',
    'fixed': 'All 8640 identities, request times, target fields, destinations and post-merge paths. Same network, lane permissions, control inputs and three-node driver.',
    'end_s': 14400, 'window_s': [3600, 12600], 'timeout_seconds': 3600,
    'from_empty_initialization': True, 'restart_validated': False, 'observed_vehicles': participants,
    'known_prefix_tradeoff': 'Z4 loses 3 passages, and maximum initialization-bin error rises 48 to 50. All other field effects are retained in initialization-prefix-check.json.',
    'input_sha256': {n: file_sha256(P / n) for n in names},
    'claim_boundary': 'The initialization result does not establish main-window safety, insertion, count improvement or exact calibration. Preserve every real target and all residuals.'})


def observed_command(command, **kwargs):
    request_file = Path(command[-1])
    request = json.loads(request_file.read_bytes())
    request['command'].extend(['--lanechange-output', str(OUT / 'lanechanges.xml'),
                              '--fcd-output', str(OUT / 'participants-fcd.xml'),
                              '--device.fcd.probability', '0', '--device.fcd.explicit', ','.join(participants),
                              '--fcd-output.skip-empty', 'true'])
    write_json_atomic(request_file, request)
    return run_command(command, **kwargs)


with patch('astra_sumo.core.detector_demand_replay.run_command', observed_command):
    result = run_point_detector_demand(net_file=P / 'network.net.xml', route_file=P / 'demand.rou.xml',
        e1_additional_file=P / 'field-loops.add.xml', expected_counts_csv=P / 'expected-counts.csv', output_dir=OUT,
        end_s=14400, sumo_binary=str(binary), timeout_seconds=3600, state_times_s=(14399.9,),
        signal_program_file=P / 'control.add.xml', lane_intentions_file=P / 'lane-intentions.json',
        junction_body_hold=True, junction_body_hold_nodes=('14398547', '205069138', '18653534'))
print(json.dumps({k: result.get(k) for k in ('status', 'native_status', 'healthy', 'completion', 'observer_complete', 'exact_field_bins', 'missing_field_bins')}), flush=True)
