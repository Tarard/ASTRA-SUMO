"""Account for field visits separately from unique model vehicles in the full replay."""

from collections import Counter, defaultdict
import gzip
import json
from pathlib import Path
import shutil
import xml.etree.ElementTree as ET

from astra_sumo.core.artifact_io import write_json_atomic
from astra_sumo.core.candidate_contracts import file_sha256
from astra_sumo.core.detector_demand import read_csv_rows, write_csv
from astra_sumo.core.hamburg_collision_trace import read_trace_source
from astra_sumo.core.hamburg_readme_results import summarize_hamburg_readme

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / 'benchmarks/hamburg_ring1_primary_entry_full_20261022_v1'
PREFIX = ROOT / 'benchmarks/hamburg_ring1_shared_population_prefix_20261022_v1'
P = ROOT / 'outputs/ring1-shared-population-full-inputs-20261022-v1'
N = ROOT / 'outputs/ring1-shared-population-full-native-20261022-v1'
REPORT = ROOT / 'outputs/ring1-shared-population-full-report-20261022-v1'
A = ROOT / 'outputs/ring1-shared-population-full-analysis-20261022-v1'
CASE = ROOT / 'benchmarks/hamburg_ring1_shared_population_full_20261022_v1'
monitor = json.loads((N / 'stall-monitor.json').read_bytes())
run = json.loads((N / 'run.json').read_bytes())
assert monitor['status'] == 'completed' and monitor['simulation_time_s'] == 14400
assert monitor['native_version'] == [22, 'SUMO 1.27.1']
assert json.loads((N / 'command.json').read_bytes())['status'] == 'pass'
for name, sha in json.loads((P / 'executed-implementation-sha256.json').read_bytes()).items():
    assert file_sha256(ROOT / name) == sha
for name in ('network.net.xml', 'demand.rou.xml', 'field-loops.add.xml', 'expected-counts.csv', 'control.add.xml',
             'lane-intentions.json', 'initialization-counts.csv', 'scenario.json', 'shared-population.json', 'target-ledger.csv'):
    assert (P / name).read_bytes() == read_trace_source(PREFIX / 'inputs', name)
result = summarize_hamburg_readme(repository=ROOT, prepared_dir=P, native_dir=N,
    figure_dir=ROOT / 'outputs/ring1-readme-end-to-end-figures-20261021-v1', output_dir=REPORT)
A.mkdir(exist_ok=False)
intentions = json.loads((P / 'lane-intentions.json').read_bytes())['vehicles']
intent = {v['vehicle_id']: v for v in intentions}
planned = {v.get('id'): v for v in ET.parse(P / 'demand.rou.xml').getroot().findall('vehicle')}
trips = {v.get('id'): v for v in ET.parse(N / 'tripinfo.xml').getroot().findall('tripinfo')}
targets = {(v['vehicle_id'], t['field_id']): t for v in intentions for t in v['target_observations']}
assert len(targets) == 8640 and len(intent) == len(planned) == 8185
passage = json.loads((REPORT / 'passage-reconciliation.json').read_bytes())
assert passage['reconciled']
frequency = Counter((r['vehicle_id'], r['detector_id']) for r in passage['passages'])
repeated = [{'vehicle_id': v, 'detector_id': f, 'count': n} for (v, f), n in frequency.items() if n > 1]
unexpected = [r for r in passage['passages'] if (r['vehicle_id'], r['detector_id']) not in targets]
actual = {(r['vehicle_id'], r['detector_id']): r for r in passage['passages']}
missing_arrived = [{'vehicle_id': v, 'detector_id': f} for v, f in targets
                   if v in trips and float(trips[v].get('arrival')) >= 0 and (v, f) not in actual]
wrong_order, second_without_first = [], []
both = 0
for vehicle in intentions:
    fields = vehicle['expected_field_ids']
    if len(fields) != 2:
        continue
    first, second = [(vehicle['vehicle_id'], f) for f in fields]
    if second in actual and first not in actual:
        second_without_first.append(vehicle['vehicle_id'])
    if first in actual and second in actual:
        both += 1
        if actual[first]['time_s'] >= actual[second]['time_s']:
            wrong_order.append(vehicle['vehicle_id'])
signature = {'formal_result': False, 'model_vehicles': len(planned), 'declared_field_visits': len(targets),
    'native_field_contributions_all_times': len(passage['passages']), 'shared_vehicles_observed_at_both_fields': both,
    'repeated_same_field': repeated, 'unexpected_fields': unexpected, 'arrived_missing_declared_fields': missing_arrived,
    'wrong_field_order': wrong_order, 'second_without_first': second_without_first,
    'claim_boundary': 'Unique vehicles, expected field visits and measured contributions are different quantities. Missing targets remain missing, not zero. This signature does not identify real OD.'}
write_json_atomic(A / 'native-signature-check.json', signature)
if not repeated:
    desired_by_cell, actual_by_cell = defaultdict(set), defaultdict(set)
    for (vehicle, field), target in targets.items():
        desired_by_cell[field, float(target['target_begin_s']), float(target['target_end_s'])].add(vehicle)
    for (vehicle, field), measured in actual.items():
        actual_by_cell[field, measured['interval_begin_s'], measured['interval_end_s']].add(vehicle)
    rows = []
    for row in read_csv_rows(REPORT / 'field-comparison.csv'):
        field = row['detector_id']
        key = field, float(row['begin']), float(row['end'])
        wanted, seen = desired_by_cell[key], actual_by_cell[key]
        assert len(wanted) == int(row['expected_total']) and len(seen) == int(row['measured_nVehContrib'])
        incoming = seen - wanted
        outgoing = {v for v in wanted - seen if (v, field) in actual}
        unentered = wanted - trips.keys()
        unpassed = {v for v in wanted & trips.keys() if (v, field) not in actual}
        assert len(incoming) - len(outgoing) - len(unentered) - len(unpassed) == int(row['diff_nVehContrib_minus_expected'])
        rows.append({**row, 'on_time_target_visits': len(wanted & seen),
            'in_from_initialization_targets': sum(targets.get((v, field), {}).get('population_role') == 'initialization' for v in incoming),
            'in_from_other_main_targets': sum(targets.get((v, field), {}).get('population_role') == 'main' for v in incoming),
            'in_from_undeclared_fields': sum((v, field) not in targets for v in incoming),
            'out_before_main_window': sum(actual[v, field]['interval_begin_s'] < 3600 for v in outgoing),
            'out_after_main_window': sum(actual[v, field]['interval_begin_s'] >= 12600 for v in outgoing),
            'out_to_other_main_bins': sum(3600 <= actual[v, field]['interval_begin_s'] < 12600 for v in outgoing),
            'not_entered_target_visits': len(unentered), 'entered_missing_target_visits': len(unpassed)})
    assert len(rows) == 130
    write_csv(A / 'all-130-target-visit-decomposition.csv', rows, list(rows[0]))
    terms = list(rows[0])[list(rows[0]).index('on_time_target_visits'):]
    write_json_atomic(A / 'target-visit-decomposition.json', {'formal_result': False,
        'all_130_native_residual_identities_verified': True, 'main_target_visits': 6687,
        'totals_in_field_visits': {k: sum(int(r[k]) for r in rows) for k in terms},
        'unique_native_unentered_vehicles': result['completion']['waiting'],
        'claim_boundary': 'Terms are field visits, not unique vehicles. A shared unentered vehicle may represent two missing targets. The arithmetic describes this native replay and does not predict a changed demand.'})
else:
    write_json_atomic(A / 'target-visit-decomposition.json', {'all_130_native_residual_identities_verified': False,
        'reason': 'Repeated same-field contributions cannot be collapsed into one observation per vehicle.', 'formal_result': False})
before = read_csv_rows(BASE / 'field-comparison.csv')
after = read_csv_rows(REPORT / 'field-comparison.csv')
paired = []
for old, new in zip(before, after):
    assert all(old[k] == new[k] for k in ('detector_id', 'begin', 'end', 'expected_total'))
    paired.append({**new, 'baseline_nVehContrib': old['measured_nVehContrib'], 'baseline_residual': old['diff_nVehContrib_minus_expected'],
                   'count_change': int(new['measured_nVehContrib']) - int(old['measured_nVehContrib'])})
assert len(paired) == 130
write_csv(A / 'all-130-baseline-comparisons.csv', paired, list(paired[0]))
prefix = {tuple(r.get(k) for k in ('id', 'begin', 'end')): r.attrib for r in ET.fromstring(read_trace_source(PREFIX / 'native', 'field-counts.xml'))}
full_prefix = {tuple(r.get(k) for k in ('id', 'begin', 'end')): r.attrib for r in ET.parse(N / 'field-counts.xml').getroot() if float(r.get('end')) <= 3600}
write_json_atomic(A / 'prefix-reproduction-check.json', {'compared_intervals': len(prefix),
    'all_initialization_intervals_match': prefix == full_prefix, 'full_count_fit_established': False})
entry_counts = Counter(v.find('route').get('edges').split()[0] for v in planned.values())
entered_counts = Counter(planned[v].find('route').get('edges').split()[0] for v in trips)
entry_rows = [{'entry': edge, 'model_requests': n, 'inserted': entered_counts[edge], 'not_inserted': n - entered_counts[edge]}
              for edge, n in sorted(entry_counts.items())]
write_csv(A / 'entry-loads.csv', entry_rows, list(entry_rows[0]))
CASE.mkdir(exist_ok=False)
for label, source in [('inputs', P), ('native', N), ('', REPORT), ('analysis', A),
                     ('live-observations', ROOT / 'outputs/ring1-shared-population-full-live-notes-20261022-v1')]:
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
for path in (Path(__file__), ROOT / 'outputs/run-shared-population-full-20261022-v1.py'):
    shutil.copyfile(path, CASE / path.name)
figure_source = BASE / 'figure-provenance.json'
assert json.loads(figure_source.read_bytes())['current_network_sha256'] == result['network_sha256']
shutil.copyfile(figure_source, CASE / 'figure-provenance.json')
write_json_atomic(CASE / 'execution-outcome.json', {'formal_result': False, 'native_reached_planned_end': True,
    'native_end_s': 14400, 'timeout': False, 'completion': result['completion'], 'shared_pairs': 455,
    'all_vehicle_requests_completed': result['completion']['waiting'] == result['completion']['running'] == 0,
    'native_signature_flags_empty': not any(signature[k] for k in ('repeated_same_field', 'unexpected_fields', 'arrived_missing_declared_fields', 'wrong_field_order', 'second_without_first'))})
print(json.dumps({'case': str(CASE), 'completion': result['completion'], 'exact_fields': result['exact_field_rows'],
    'diagnostic_mae': result['diagnostic_field_MAE'], 'max_abs': result['diagnostic_max_abs_error'],
    'shared_observed_at_both': both, 'arrived_missing_fields': len(missing_arrived)}), flush=True)
