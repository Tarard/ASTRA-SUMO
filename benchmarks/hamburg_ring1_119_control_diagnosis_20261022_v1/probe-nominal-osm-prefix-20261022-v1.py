"""Measure the fixed nominal OSM control alternative with unchanged reference demand."""

import hashlib
import json
from pathlib import Path
import shutil
import subprocess
from unittest.mock import patch

import sumo

from astra_sumo.core.artifact_io import write_json_atomic
from astra_sumo.core.command_runner import run_command
from astra_sumo.core.detector_demand_replay import run_point_detector_demand
from astra_sumo.core.hamburg_readme_control import prepare_nominal_osm_control

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / 'benchmarks/hamburg_ring1_primary_entry_full_20261022_v1'
P = ROOT / 'outputs/ring1-nominal-osm-control-inputs-20261022-v1'
N = ROOT / 'outputs/ring1-nominal-osm-control-prefix-native-20261022-v1'
prepared = prepare_nominal_osm_control(case_dir=BASE, output_dir=P)
assert len(prepared['changed_programs']) == 58
models = ['plugins/astra-sumo/src/astra_sumo/core/' + name for name in (
    'hamburg_readme_control.py', 'lane_section_hold.py', 'simulation_stall.py', 'detector_demand_replay.py', 'route_sensor_matrix.py')]
binary = Path(sumo.__file__).parent / 'bin/sumo.exe'
for name in models:
    target = P / 'implementation-at-execution' / name
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(ROOT / name, target)
write_json_atomic(P / 'executed-implementation-sha256.json', {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in models}
    | {str(binary): hashlib.sha256(binary.read_bytes()).hexdigest()})
(P / 'source-commit.txt').write_text(subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True), encoding='utf-8')


def observed_command(command, **kwargs):
    assert command[1:3] == ['-m', 'astra_sumo.core.simulation_stall']
    return run_command([command[0], str(ROOT / 'outputs/observe-nominal-osm-installation-20261022-v1.py'), command[-1]], **kwargs)


with patch('astra_sumo.core.detector_demand_replay.run_command', observed_command):
    result = run_point_detector_demand(net_file=P / 'network.net.xml', route_file=P / 'demand.rou.xml',
        e1_additional_file=P / 'field-loops.add.xml', expected_counts_csv=P / 'initialization-counts.csv', output_dir=N,
        end_s=3600, sumo_binary=str(binary), timeout_seconds=1200, signal_program_file=P / 'control.add.xml',
        lane_intentions_file=P / 'lane-intentions.json', junction_body_hold=True,
        junction_body_hold_nodes=('14398547', '205069138', '18653534'))
print(json.dumps({k: result.get(k) for k in ('status', 'completion', 'observer_complete', 'exact_field_bins', 'missing_field_bins')}), flush=True)
