"""Retain the complete entry-prior replay, every residual and the measured tradeoffs."""

import gzip
import json
from pathlib import Path
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET

from astra_sumo.core.artifact_io import write_json_atomic
from astra_sumo.core.candidate_contracts import file_sha256
from astra_sumo.core.detector_demand import read_csv_rows, write_csv
from astra_sumo.core.hamburg_collision_trace import read_trace_source
from astra_sumo.core.hamburg_readme_results import summarize_hamburg_readme

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / 'benchmarks/hamburg_ring1_three_node_body_full_20261022_v1'
PREFIX = ROOT / 'benchmarks/hamburg_ring1_primary_entry_prefix_20261022_v1'
P = ROOT / 'outputs/ring1-primary-entry-prior-full-inputs-20261022-v1'
N = ROOT / 'outputs/ring1-primary-entry-prior-full-native-20261022-v1'
REPORT = ROOT / 'outputs/ring1-primary-entry-prior-full-report-20261022-v1'
A = ROOT / 'outputs/ring1-primary-entry-prior-full-analysis-20261022-v1'
CASE = ROOT / 'benchmarks/hamburg_ring1_primary_entry_full_20261022_v1'
monitor = json.loads((N / 'stall-monitor.json').read_bytes())
run = json.loads((N / 'run.json').read_bytes())
assert monitor['status'] == 'completed' and monitor['simulation_time_s'] == 14400
assert monitor['native_version'] == [22, 'SUMO 1.27.1']
assert json.loads((N / 'command.json').read_bytes())['status'] == 'pass'
assert monitor['junction_body_hold_nodes'] == ['14398547', '205069138', '18653534']
for name, sha in json.loads((P / 'executed-implementation-sha256.json').read_bytes()).items():
    assert file_sha256(ROOT / name) == sha
for name in ('network.net.xml', 'demand.rou.xml', 'field-loops.add.xml', 'expected-counts.csv',
             'control.add.xml', 'lane-intentions.json', 'initialization-counts.csv', 'scenario.json', 'entry-prior.json'):
    assert (P / name).read_bytes() == read_trace_source(PREFIX / 'inputs', name)
result = summarize_hamburg_readme(repository=ROOT, prepared_dir=P, native_dir=N,
    figure_dir=ROOT / 'outputs/ring1-readme-end-to-end-figures-20261021-v1', output_dir=REPORT)
source_script = ROOT / 'outputs/decompose-three-node-body-main-residuals-20261022-v1.py'
decompose = ROOT / 'outputs/decompose-primary-entry-main-residuals-20261022-v1.py'
decompose.write_text(source_script.read_text(encoding='utf-8').replace('ring1-three-node-body-full-', 'ring1-primary-entry-prior-full-'), encoding='utf-8', newline='\n')
subprocess.run([sys.executable, str(decompose)], cwd=ROOT, check=True)
A.mkdir(exist_ok=False)
before = read_csv_rows(BASE / 'field-comparison.csv')
after = read_csv_rows(REPORT / 'field-comparison.csv')
paired = []
for old, new in zip(before, after):
    assert all(old[k] == new[k] for k in ('detector_id', 'begin', 'end', 'expected_total'))
    paired.append({**new, 'baseline_nVehContrib': old['measured_nVehContrib'],
                   'baseline_residual': old['diff_nVehContrib_minus_expected'],
                   'count_change': int(new['measured_nVehContrib']) - int(old['measured_nVehContrib'])})
assert len(paired) == 130
write_csv(A / 'all-130-baseline-comparisons.csv', paired, list(paired[0]))
source_intervals = {tuple(v.get(k) for k in ('id', 'begin', 'end')): v.attrib
                    for v in ET.fromstring(read_trace_source(PREFIX / 'native', 'field-counts.xml'))}
full_prefix = {tuple(v.get(k) for k in ('id', 'begin', 'end')): v.attrib
               for v in ET.parse(N / 'field-counts.xml').getroot() if float(v.get('end')) <= 3600}
changed = [k for k in source_intervals if source_intervals[k] != full_prefix.get(k)]
write_json_atomic(A / 'prefix-reproduction-check.json', {'compared_intervals': len(source_intervals),
    'all_initialization_intervals_match': source_intervals == full_prefix, 'different_intervals': changed,
    'claim_boundary': 'Compare every E1 attribute through 3600 s. Prefix correspondence does not establish full-window calibration.'})
witness_source = json.loads(read_trace_source(BASE / 'analysis', 'driver-and-participant-check.json'))['original_participants']
trips = {v.get('id'): v for v in ET.parse(N / 'tripinfo.xml').getroot().findall('tripinfo')}
clear = {}
for step in ET.parse(N / 'participants-fcd.xml').getroot():
    for vehicle in step:
        identity = vehicle.get('id')
        if identity in witness_source and vehicle.get('lane').startswith(witness_source[identity]['outlet'] + '_') and float(vehicle.get('pos')) >= 5:
            clear.setdefault(identity, dict(vehicle.attrib, time_s=float(step.get('time'))))
witnesses = {identity: {'node': row['node'], 'outlet': row['outlet'], 'first_rear_clear_sample': clear.get(identity),
    'native_arrival_s': float(trips[identity].get('arrival')) if identity in trips else None} for identity, row in witness_source.items()}
write_json_atomic(A / 'original-participant-check.json', {'formal_result': False, 'vehicles': witnesses,
    'all_six_rear_cleared': len(clear) == 6,
    'all_six_arrived': all(row['native_arrival_s'] is not None and row['native_arrival_s'] >= 0 for row in witnesses.values()),
    'claim_boundary': 'Native participant clearance and arrivals under this complete entry-prior replay. No field driving or other-seed safety is established.'})
CASE.mkdir(exist_ok=False)
for label, source in [('inputs', P), ('native', N), ('', REPORT), ('analysis', A),
                       ('data-analysis', ROOT / 'outputs/ring1-primary-entry-prior-full-data-analysis-20261022-v1'),
                       ('live-observations', ROOT / 'outputs/ring1-primary-entry-prior-full-live-notes-20261022-v1')]:
    for path in source.rglob('*'):
        if not path.is_file():
            continue
        target = CASE / label / path.relative_to(source)
        target.parent.mkdir(parents=True, exist_ok=True)
        if path.suffix in {'.xml', '.json', '.csv'} and path.stat().st_size > 200000 and not (label == 'inputs' and path.name == 'network.net.xml'):
            target = target.with_name(target.name + '.gz')
            target.write_bytes(gzip.compress(path.read_bytes(), mtime=0))
            assert gzip.decompress(target.read_bytes()) == path.read_bytes()
        else:
            shutil.copyfile(path, target)
shutil.copyfile(CASE / 'README.md', CASE / 'workflow-report.md')
shutil.copyfile(BASE / '.gitattributes', CASE / '.gitattributes')
for path in (Path(__file__), ROOT / 'outputs/run-primary-entry-full-20261022-v1.py', decompose):
    shutil.copyfile(path, CASE / path.name)
figure_source = BASE / 'figure-provenance.json'
assert json.loads(figure_source.read_bytes())['current_network_sha256'] == result['network_sha256']
shutil.copyfile(figure_source, CASE / 'figure-provenance.json')
write_json_atomic(CASE / 'execution-outcome.json', {'formal_result': False, 'native_reached_planned_end': True,
    'native_end_s': 14400, 'timeout': False, 'completion': result['completion'],
    'entry_only_model_change': True, 'all_vehicle_requests_completed': result['completion']['waiting'] == result['completion']['running'] == 0})
print(json.dumps({'case': str(CASE), 'completion': result['completion'], 'exact_fields': result['exact_field_rows'],
      'diagnostic_mae': result['diagnostic_field_MAE'], 'diagnostic_max_abs_error': result['diagnostic_max_abs_error']}), flush=True)
