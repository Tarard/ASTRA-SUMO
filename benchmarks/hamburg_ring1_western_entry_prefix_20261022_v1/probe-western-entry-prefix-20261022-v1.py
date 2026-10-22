"""Measure the declared western-entry half-share in the initialization hour."""

import hashlib
import json
from pathlib import Path
import shutil
import subprocess

import sumo

from astra_sumo.core.artifact_io import write_json_atomic
from astra_sumo.core.detector_demand_replay import run_point_detector_demand
from astra_sumo.core.hamburg_entry_prior import prepare_western_entry_prior

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'benchmarks/hamburg_ring1_primary_entry_full_20261022_v1'
PROBE = ROOT / 'benchmarks/hamburg_ring1_western_entry_prefix_20261022_v1/route-probe'
P = ROOT / 'outputs/ring1-western-primary-entry-inputs-20261022-v2'
N = ROOT / 'outputs/ring1-western-primary-entry-prefix-20261022-v1'
prepared = prepare_western_entry_prior(repository=ROOT, case_dir=SOURCE, probe_dir=PROBE, output_dir=P)
assert prepared['changed_by_population'] == {'initialization': 95, 'main': 427}
models = ['plugins/astra-sumo/src/astra_sumo/core/' + name for name in (
    'hamburg_entry_prior.py', 'detector_demand_replay.py', 'lane_section_hold.py', 'simulation_stall.py', 'route_sensor_matrix.py')]
binary = Path(sumo.__file__).parent / 'bin/sumo.exe'
for name in models:
    target = P / 'implementation-at-execution' / name
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(ROOT / name, target)
write_json_atomic(P / 'executed-implementation-sha256.json', {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in models}
    | {str(binary): hashlib.sha256(binary.read_bytes()).hexdigest()})
write_json_atomic(P / 'prefix-proposal.json', {'formal_result': False, 'end_s': 3600,
    'source_commit': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
    'requests_due': 1953, 'future_main_requests_retained': 6687, 'changed_initialization_requests': 95,
    'changed_main_requests': 427, 'driver': 'Same first-field intention and three-node body hold',
    'change': 'Half-share of the original residential/unclassified western source groups to existing 304/305 primary-road profiles. Same requests, times and road suffix after 234423652#1.',
    'fixed': 'All real field/station targets, network, controls, legal permissions and turnarounds, service/tertiary entries, vehicle type and driver rules.',
    'claim_boundary': 'Exploratory entry allocation. The 40-vehicle check is not evidence about dense traffic or real OD. The complete main window remains untested.'})
run = run_point_detector_demand(net_file=P / 'network.net.xml', route_file=P / 'demand.rou.xml',
    e1_additional_file=P / 'field-loops.add.xml', expected_counts_csv=P / 'initialization-counts.csv', output_dir=N,
    end_s=3600, sumo_binary=str(binary), timeout_seconds=1200, signal_program_file=P / 'control.add.xml',
    lane_intentions_file=P / 'lane-intentions.json', junction_body_hold=True,
    junction_body_hold_nodes=('14398547', '205069138', '18653534'))
print(json.dumps({k: run.get(k) for k in ('status', 'native_status', 'healthy', 'completion', 'observer_complete', 'missing_field_bins', 'exact_field_bins')}), flush=True)
