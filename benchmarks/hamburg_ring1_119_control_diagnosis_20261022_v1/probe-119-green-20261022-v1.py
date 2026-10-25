"""Replay the unchanged reference to two 119 K2 greens for read-only diagnosis."""

import json
from pathlib import Path
from unittest.mock import patch

import sumo

from astra_sumo.core.command_runner import run_command
from astra_sumo.core.detector_demand_replay import run_point_detector_demand
from astra_sumo.core.hamburg_collision_trace import read_trace_source

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / 'benchmarks/hamburg_ring1_primary_entry_full_20261022_v1'
P = ROOT / 'outputs/ring1-119-green-observation-inputs-20261022-v1'
N = ROOT / 'outputs/ring1-119-green-observation-native-20261022-v1'
P.mkdir(exist_ok=False)
for name in ('network.net.xml', 'demand.rou.xml', 'field-loops.add.xml', 'control.add.xml',
             'lane-intentions.json', 'initialization-counts.csv', 'expected-counts.csv', 'scenario.json'):
    (P / name).write_bytes(read_trace_source(BASE / 'inputs', name))


def observed_command(command, **kwargs):
    assert command[1:3] == ['-m', 'astra_sumo.core.simulation_stall']
    return run_command([command[0], str(ROOT / 'outputs/observe-119-green-20261022-v1.py'), command[-1]], **kwargs)


with patch('astra_sumo.core.detector_demand_replay.run_command', observed_command):
    result = run_point_detector_demand(net_file=P / 'network.net.xml', route_file=P / 'demand.rou.xml',
        e1_additional_file=P / 'field-loops.add.xml', expected_counts_csv=P / 'initialization-counts.csv', output_dir=N,
        end_s=3780, sumo_binary=str(Path(sumo.__file__).parent / 'bin/sumo.exe'), timeout_seconds=1200,
        signal_program_file=P / 'control.add.xml', lane_intentions_file=P / 'lane-intentions.json',
        junction_body_hold=True, junction_body_hold_nodes=('14398547', '205069138', '18653534'))
print(json.dumps({k: result.get(k) for k in ('status', 'completion', 'observer_complete', 'exact_field_bins', 'missing_field_bins')}), flush=True)
