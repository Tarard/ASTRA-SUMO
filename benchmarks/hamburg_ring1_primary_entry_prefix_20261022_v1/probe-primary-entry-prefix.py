"""Test the declared entry redistribution on the complete initialization traffic."""

import json
from pathlib import Path
import shutil
import subprocess

import sumo

from astra_sumo.core.artifact_io import write_json_atomic
from astra_sumo.core.candidate_contracts import file_sha256
from astra_sumo.core.detector_demand_replay import run_point_detector_demand
from astra_sumo.core.hamburg_entry_prior import prepare_primary_entry_prior

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'benchmarks/hamburg_ring1_three_node_body_full_20261022_v1'
P = ROOT / 'outputs/ring1-primary-entry-prior-inputs-20261022-v2'
OUT = ROOT / 'outputs/ring1-primary-entry-prior-prefix-20261022-v1'
prepared = prepare_primary_entry_prior(repository=ROOT, case_dir=SOURCE, output_dir=P)
assert prepared['changed_by_population'] == {'initialization': 73, 'main': 221}
models = ['plugins/astra-sumo/src/astra_sumo/core/' + name for name in (
    'hamburg_entry_prior.py', 'lane_section_hold.py', 'simulation_stall.py', 'detector_demand_replay.py')]
for name in models:
    target = P / 'implementation-at-execution' / name
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(ROOT / name, target)
write_json_atomic(P / 'executed-implementation-sha256.json', {name: file_sha256(ROOT / name) for name in models})
(P / 'source-commit.txt').write_text(subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True), encoding='utf-8', newline='\n')
write_json_atomic(P / 'prefix-proposal.json', {'formal_result': False, 'end_s': 3600,
    'source_complete_case': str(SOURCE), 'baseline': 'Reuse the completed source run before 3600 s. All baseline initialization traffic and detector intervals are retained.',
    'change': 'Move 73 initialization and 221 future-main Z12 requests to the existing primary entry according to the declared 3:1 proxy. Only the initialization change is exercised here.',
    'fixed': 'All 8640 vehicle IDs, request times, target fields, destinations, post-merge routes, network, permissions, signals and three-node driver scope.',
    'targets': 'Measure all 52 initialization rows. Retain all 130 main field and 40 derived station targets, with main observations unknown before their window.',
    'evaluation': 'Native completion, insertion and E1 changes for all 13 fields, including other-field losses. No collision/teleport or target deletion. The prefix cannot establish main-window calibration.'})
result = run_point_detector_demand(net_file=P / 'network.net.xml', route_file=P / 'demand.rou.xml',
    e1_additional_file=P / 'field-loops.add.xml', expected_counts_csv=P / 'initialization-counts.csv', output_dir=OUT,
    end_s=3600, sumo_binary=str(Path(sumo.__file__).parent / 'bin/sumo.exe'), timeout_seconds=900,
    state_times_s=(3599.9,), signal_program_file=P / 'control.add.xml', lane_intentions_file=P / 'lane-intentions.json',
    junction_body_hold=True, junction_body_hold_nodes=('14398547', '205069138', '18653534'))
print(json.dumps({k: result.get(k) for k in ('status', 'native_status', 'healthy', 'completion', 'observer_complete', 'exact_field_bins', 'missing_field_bins')}), flush=True)
