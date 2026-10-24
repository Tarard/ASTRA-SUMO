"""Measure a shared-demand alternative without dropping any observation targets."""

import json
from pathlib import Path
import shutil
import subprocess

import sumo

from astra_sumo.core.artifact_io import write_json_atomic
from astra_sumo.core.candidate_contracts import file_sha256
from astra_sumo.core.detector_demand_replay import run_point_detector_demand
from astra_sumo.core.hamburg_shared_population import prepare_shared_population

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'benchmarks/hamburg_ring1_primary_entry_full_20261022_v1'
P = ROOT / 'outputs/ring1-shared-population-inputs-20261022-v2'
OUT = ROOT / 'outputs/ring1-shared-population-prefix-20261022-v1'
prepared = prepare_shared_population(repository=ROOT, case_dir=SOURCE,
    probe_dir=ROOT / 'benchmarks/hamburg_ring1_shared_field_probe_20261022_v1', output_dir=P)
assert prepared['shared_pairs'] == 455 and prepared['model_vehicles_after'] == 8185
assert prepared['field_observation_totals'] == {'initialization': 1953, 'main': 6687}
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
write_json_atomic(P / 'prefix-proposal.json', {'formal_result': False, 'end_s': 3600,
    'model_vehicles': 8185, 'model_vehicles_by_first_target': prepared['model_roles_after'],
    'field_observation_targets': prepared['field_observation_totals'], 'shared_pairs': 455,
    'source_complete_case': str(SOURCE),
    'change': 'Use 455 native-supported shared routes. Parents already traversed the second observed road in the original model; keep their road prefix, request time and first-field waypoint. Keep 40 unmatched independent second-field requests.',
    'pairing_prior': 'Original native road-front exit plus sparse rear-clearance offset. A 90 s target-edge margin and 450 s unmatched cost are declared priors, not traffic-delay bounds.',
    'fixed': 'All 182 initialization/main field quotas and all 40 derived station targets, network, permissions, control programs, vehicle type and three-node driver.',
    'from_empty_initialization': True, 'restart_validated': False,
    'evaluation': 'Compare every initialization field, unique entered vehicles, E1 contributions, repeated/unexpected fields and native safety. Do not compare vehicle counts as if one vehicle still equaled one field contribution. Keep the 130/40 main rows unknown.'})
result = run_point_detector_demand(net_file=P / 'network.net.xml', route_file=P / 'demand.rou.xml',
    e1_additional_file=P / 'field-loops.add.xml', expected_counts_csv=P / 'initialization-counts.csv', output_dir=OUT,
    end_s=3600, sumo_binary=str(binary), timeout_seconds=900, state_times_s=(3599.9,),
    signal_program_file=P / 'control.add.xml', lane_intentions_file=P / 'lane-intentions.json',
    junction_body_hold=True, junction_body_hold_nodes=('14398547', '205069138', '18653534'))
print(json.dumps({k: result.get(k) for k in ('status', 'native_status', 'healthy', 'completion', 'observer_complete', 'exact_field_bins', 'missing_field_bins')}), flush=True)
