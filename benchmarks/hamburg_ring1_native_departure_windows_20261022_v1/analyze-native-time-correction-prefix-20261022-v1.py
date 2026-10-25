"""Retain every initialization comparison and unknown main-window target."""

from collections import Counter
from io import BytesIO
import json
from pathlib import Path
import statistics
import xml.etree.ElementTree as ET

from astra_sumo.core.artifact_io import write_json_atomic
from astra_sumo.core.detector_demand import audit_expected_to_e1_strict, read_csv_rows, write_csv
from astra_sumo.core.hamburg_collision_trace import read_trace_source
from astra_sumo.core.hamburg_readme_results import STATIONS
from astra_sumo.core.route_sensor_matrix import read_contributing_e1_passages

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / 'benchmarks/hamburg_ring1_primary_entry_full_20261022_v1'
P = ROOT / 'outputs/ring1-native-time-correction-inputs-20261022-v2'
N = ROOT / 'outputs/ring1-native-time-correction-prefix-20261022-v1'
OUT = ROOT / 'outputs/ring1-native-time-correction-prefix-analysis-20261022-v1'
run = json.loads((N / 'run.json').read_bytes())
assert run['stall_monitor']['status'] == 'completed' and run['stall_monitor']['simulation_time_s'] == 3600
assert run['stall_monitor']['native_version'] == [22, 'SUMO 1.27.1']
OUT.mkdir(exist_ok=False)
expected = read_csv_rows(P / 'initialization-counts.csv')
source = ET.fromstring(read_trace_source(BASE / 'native', 'field-counts.xml'))
closed = ET.Element(source.tag)
closed.extend(row for row in source if float(row.get('end')) <= 3600)
ET.ElementTree(closed).write(OUT / 'baseline-closed-e1.xml', encoding='utf-8', xml_declaration=True)
old = audit_expected_to_e1_strict(expected, OUT / 'baseline-closed-e1.xml')
new = audit_expected_to_e1_strict(expected, N / 'field-counts.xml')
rows = [{**target, 'baseline_nVehContrib': a['measured_nVehContrib'], 'candidate_nVehContrib': b['measured_nVehContrib'],
    'change': b['measured_nVehContrib'] - a['measured_nVehContrib'],
    'baseline_residual': a['diff_nVehContrib_minus_expected'], 'candidate_residual': b['diff_nVehContrib_minus_expected']}
    for target, a, b in zip(expected, old, new)]
assert len(rows) == 52
write_csv(OUT / 'all-52-initialization-comparisons.csv', rows, list(rows[0]))
main_source = read_csv_rows(P / 'expected-counts.csv')
main = [{**a, **b} for a, b in zip(main_source, audit_expected_to_e1_strict(main_source, N / 'field-counts.xml'))]
assert len(main) == 130 and all(r['measured_nVehContrib'] is None for r in main)
write_csv(OUT / 'all-130-main-rows.csv', main, list(main[0]))
stations = []
for station, (node, assets) in STATIONS.items():
    for begin in sorted({r['begin'] for r in main}, key=float):
        selected = [r for r in main if r['node_id'] == node and r['asset_id'] in assets and r['begin'] == begin]
        assert len(selected) == len(assets)
        stations.append({'station_stream_id': station, 'begin': begin, 'end': selected[0]['end'],
            'expected_total': sum(int(r['expected_total']) for r in selected), 'measured_nVehContrib': None,
            'residual': None, 'independent_evidence': False})
assert len(stations) == 40
write_csv(OUT / 'all-40-main-stations.csv', stations, list(stations[0]))
baseline_step = None
for _, element in ET.iterparse(BytesIO(read_trace_source(BASE / 'native', 'summary.xml'))):
    if element.tag == 'step' and float(element.get('time')) < 3600:
        baseline_step = dict(element.attrib)
    element.clear()
baseline_completion = {key: int(baseline_step[key]) for key in ('loaded', 'inserted', 'arrived', 'running', 'waiting', 'collisions', 'teleports')}
planned = {v.get('id'): v for v in ET.parse(P / 'demand.rou.xml').getroot().findall('vehicle')}
due = {identity for identity, v in planned.items() if float(v.get('depart')) < 3600}
trips = {v.get('id') for v in ET.parse(N / 'tripinfo.xml').getroot().findall('tripinfo')}
assert len(due - trips) == run['completion']['waiting']
intent = {r['vehicle_id']: r for r in json.loads((P / 'lane-intentions.json').read_bytes())['vehicles']}
init = {identity for identity, row in intent.items() if row['population_role'] == 'initialization'}
assert len(init) == 1953
passage = read_contributing_e1_passages(instant_file=N / 'instant-e1.xml', periodic_file=N / 'field-counts.xml',
    detector_ids=run['instant_to_periodic_ids'], step_length=.1)
assert passage['reconciled']
counts = Counter(r['vehicle_id'] for r in passage['passages'])
unexpected = [r for r in passage['passages'] if intent[r['vehicle_id']]['field_id'] != r['detector_id']]
repeated = {identity: count for identity, count in counts.items() if count > 1}
write_json_atomic(OUT / 'passage-reconciliation.json', passage)
old_passages = {r['vehicle_id']: r for r in json.loads(read_trace_source(BASE, 'passage-reconciliation.json'))['passages']}
changes = {r['vehicle_id']: r for r in json.loads((P / 'time-correction-proposal.json').read_bytes())['changed_requests']}
timing_rows = []
for observed in passage['passages']:
    identity = observed['vehicle_id']
    if identity in changes:
        old_time = old_passages[identity]['notification_time_s']
        predicted = old_time + changes[identity]['native_step_shift_s']
        timing_rows.append({'vehicle_id': identity, 'field': observed['detector_id'], 'source_native_time_s': old_time,
            'frozen_lag_predicted_time_s': predicted, 'new_native_time_s': observed['notification_time_s'],
            'new_minus_predicted_s': observed['notification_time_s'] - predicted})
write_csv(OUT / 'observed-correction-timing-errors.csv', timing_rows, list(timing_rows[0]) if timing_rows else ['vehicle_id'])
entry_due = Counter(v.find('route').get('edges').split()[0] for identity, v in planned.items() if identity in due)
entry_inserted = Counter(planned[identity].find('route').get('edges').split()[0] for identity in trips)
entry_rows = [{'entry': edge, 'due_requests': count, 'inserted': entry_inserted[edge],
    'not_inserted': count - entry_inserted[edge]} for edge, count in sorted(entry_due.items())]
write_csv(OUT / 'entry-loads.csv', entry_rows, list(entry_rows[0]))
report = {'formal_result': False, 'main_window_completed': False, 'native_end_s': 3600,
    'total_model_requests': 8640, 'changed_requests': len(changes), 'main_field_rows_retained_unknown': 130,
    'derived_station_rows_retained_unknown': 40, 'baseline_model_requests_due': 1953, 'candidate_model_requests_due': len(due),
    'baseline_completion': baseline_completion, 'candidate_completion': run['completion'],
    'initialization_target_vehicles': len(init), 'initialization_not_inserted': len(init - trips),
    'initialization_native_contributions': sum(identity in init for identity in counts),
    'prewindow_contributions_targeting_main': sum(identity not in init for identity in counts),
    'baseline_E1_total': sum(r['baseline_nVehContrib'] for r in rows),
    'candidate_E1_total': sum(r['candidate_nVehContrib'] for r in rows),
    'baseline_diagnostic_MAE': sum(abs(r['baseline_residual']) for r in rows) / 52,
    'candidate_diagnostic_MAE': sum(abs(r['candidate_residual']) for r in rows) / 52,
    'baseline_max_abs': max(abs(r['baseline_residual']) for r in rows),
    'candidate_max_abs': max(abs(r['candidate_residual']) for r in rows),
    'baseline_exact_rows': sum(r['baseline_residual'] == 0 for r in rows),
    'candidate_exact_rows': sum(r['candidate_residual'] == 0 for r in rows),
    'native_E1_reconciled': True, 'unexpected_fields': unexpected, 'repeated_same_field': repeated,
    'observed_changed_vehicle_timing_errors': {'vehicles': len(timing_rows),
        'min_s': min(r['new_minus_predicted_s'] for r in timing_rows) if timing_rows else None,
        'median_s': statistics.median(r['new_minus_predicted_s'] for r in timing_rows) if timing_rows else None,
        'max_s': max(r['new_minus_predicted_s'] for r in timing_rows) if timing_rows else None},
    'claim_boundary': 'Initialization measurement only. Earlier requests change the number due in this hour. Counts remain native E1 contributions. Frozen source lags are not invariant queue responses. Main-window measurements remain unknown.'}
write_json_atomic(OUT / 'prefix-check.json', report)
print(json.dumps(report), flush=True)
