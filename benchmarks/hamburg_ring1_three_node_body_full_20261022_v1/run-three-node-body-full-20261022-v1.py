"""Extend only the declared driver scope after the paired maneuver check."""

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
SOURCE = ROOT / 'benchmarks/hamburg_ring1_localized_junction_body_full_20261021_v1'
P = ROOT / 'outputs/ring1-three-node-body-full-inputs-20261022-v1'
OUT = ROOT / 'outputs/ring1-three-node-body-full-native-20261022-v1'
probe = json.loads((ROOT / 'outputs/ring1-late-exit-body-hold-probe-20261022-v1/paired-check.json').read_bytes())
assert all(v['healthy'] and v['completion']['arrived'] == 40 and not v['changes_during_body_hold'] for v in probe['results'].values())
assert min(float(v['pos']) for v in probe['results']['three_nodes']['exit_changes']) >= 5
P.mkdir(exist_ok=False)
names = ('network.net.xml', 'demand.rou.xml', 'field-loops.add.xml', 'expected-counts.csv',
         'control.add.xml', 'lane-intentions.json', 'initialization-counts.csv', 'scenario.json')
for name in names:
    (P / name).write_bytes(read_trace_source(SOURCE / 'inputs', name))
nodes = ('14398547', '205069138', '18653534')
scenario = json.loads((P / 'scenario.json').read_bytes())
scenario.update(junction_body_hold_nodes=list(nodes),
    lane_intention_assumptions='Retain all observation-lane rear-clear holds. Apply the existing junction-body hold at 14398547, 205069138 and 18653534, each with an independently traced collision. Other junctions retain native lane choice. This is an exploratory driver assumption, not field-validated behavior.')
write_json_atomic(P / 'scenario.json', scenario)
participants = sorted({e.get(role) for case in (
    ROOT / 'benchmarks/hamburg_ring1_collision_trace_20261021_v1', SOURCE)
    for e in ET.fromstring(read_trace_source(case / 'native', 'collisions.xml')).findall('collision')
    for role in ('collider', 'victim')})
models = ['plugins/astra-sumo/src/astra_sumo/core/' + name for name in (
    'lane_section_hold.py', 'simulation_stall.py', 'detector_demand_replay.py', 'hamburg_readme_pipeline.py')]
for name in models:
    target = P / 'implementation-at-execution' / name
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(ROOT / name, target)
binary = Path(sumo.__file__).parent / 'bin/sumo.exe'
write_json_atomic(P / 'executed-implementation-sha256.json', {n: file_sha256(ROOT / n) for n in models} | {str(binary): file_sha256(binary)})
(P / 'source-commit.txt').write_text(subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True), encoding='utf-8', newline='\n')
write_json_atomic(P / 'full-window-proposal.json', {
    'formal_result': False, 'source_case': str(SOURCE), 'nodes': nodes,
    'model_change': 'Only add 18653534 to the existing two-node junction-body hold. Keep all traffic input files and all 130/40 targets.',
    'end_s': 14400, 'window_s': [3600, 12600], 'timeout_seconds': 3600,
    'timeout_reason': 'The preceding complete native run took 2299 seconds. Allow margin for the added declared hold and native observation outputs.',
    'observation': 'Native lane-change output for all vehicles and FCD only for the six original collision participants.',
    'observed_vehicles': participants, 'restart_validated': False, 'from_empty_initialization': True,
    'input_sha256': {n: file_sha256(P / n) for n in names},
    'claim_boundary': 'The paired artificial maneuver result does not establish full-demand safety, insertion or exact native counts. Retain all residuals and unsafe results.'})


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
        e1_additional_file=P / 'field-loops.add.xml', expected_counts_csv=P / 'expected-counts.csv',
        output_dir=OUT, end_s=14400, sumo_binary=str(binary), timeout_seconds=3600, state_times_s=(14399.9,),
        signal_program_file=P / 'control.add.xml', lane_intentions_file=P / 'lane-intentions.json',
        junction_body_hold=True, junction_body_hold_nodes=nodes)
print(json.dumps({k: result.get(k) for k in ('status', 'healthy', 'native_status', 'completion',
      'observer_complete', 'missing_field_bins', 'exact_field_bins', 'field_metrics')}), flush=True)
