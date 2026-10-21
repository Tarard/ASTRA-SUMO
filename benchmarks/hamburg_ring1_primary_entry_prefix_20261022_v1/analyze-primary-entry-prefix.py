"""Compare every initialization field and retain every unmeasured main target."""

from collections import Counter, defaultdict
from io import BytesIO
import json
from pathlib import Path
import xml.etree.ElementTree as ET

from astra_sumo.core.artifact_io import write_json_atomic
from astra_sumo.core.detector_demand import audit_expected_to_e1_strict, read_csv_rows, write_csv
from astra_sumo.core.hamburg_collision_trace import read_trace_source
from astra_sumo.core.hamburg_readme_results import STATIONS
from astra_sumo.core.route_sensor_matrix import read_contributing_e1_passages

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / 'benchmarks/hamburg_ring1_three_node_body_full_20261022_v1'
P = ROOT / 'outputs/ring1-primary-entry-prior-inputs-20261022-v2'
N = ROOT / 'outputs/ring1-primary-entry-prior-prefix-20261022-v1'
OUT = ROOT / 'outputs/ring1-primary-entry-prior-analysis-20261022-v1'
run = json.loads((N / 'run.json').read_bytes())
assert run['stall_monitor']['status'] == 'completed' and run['stall_monitor']['simulation_time_s'] == 3600
assert run['stall_monitor']['native_version'] == [22, 'SUMO 1.27.1']
OUT.mkdir(exist_ok=False)
expected = read_csv_rows(P / 'initialization-counts.csv')
source = ET.fromstring(read_trace_source(BASE / 'native', 'field-counts.xml'))
closed = ET.Element(source.tag)
closed.extend(row for row in source if float(row.get('end')) <= 3600)
ET.ElementTree(closed).write(OUT / 'baseline-closed-e1.xml', encoding='utf-8', xml_declaration=True)
baseline = audit_expected_to_e1_strict(expected, OUT / 'baseline-closed-e1.xml')
candidate = audit_expected_to_e1_strict(expected, N / 'field-counts.xml')
rows = []
for target, a, b in zip(expected, baseline, candidate):
    assert a['measured_nVehContrib'] is not None and b['measured_nVehContrib'] is not None
    rows.append({**target, 'baseline_nVehContrib': a['measured_nVehContrib'], 'candidate_nVehContrib': b['measured_nVehContrib'],
                 'change': b['measured_nVehContrib'] - a['measured_nVehContrib'],
                 'baseline_residual': a['diff_nVehContrib_minus_expected'], 'candidate_residual': b['diff_nVehContrib_minus_expected']})
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
old_planned = {v.get('id'): v for v in ET.fromstring(read_trace_source(BASE / 'inputs', 'demand.rou.xml')).findall('vehicle')}
new_planned = {v.get('id'): v for v in ET.parse(P / 'demand.rou.xml').getroot().findall('vehicle')}
old_trip = {v.get('id'): v for v in ET.fromstring(read_trace_source(BASE / 'native', 'tripinfo.xml')).findall('tripinfo')}
new_trip = {v.get('id'): v for v in ET.parse(N / 'tripinfo.xml').getroot().findall('tripinfo')}
due = {identity for identity, v in old_planned.items() if float(v.get('depart')) < 3600}
assert len(due) == 1953 and old_planned.keys() == new_planned.keys()
original = None
for _, row in ET.iterparse(BytesIO(read_trace_source(BASE / 'native', 'summary.xml'))):
    if row.tag == 'step' and float(row.get('time')) < 3600:
        original = dict(row.attrib)
    row.clear()
baseline_completion = {k: int(original[k]) for k in ('loaded', 'inserted', 'arrived', 'running', 'waiting', 'collisions', 'teleports')}
records = []
for identity in sorted(due):
    row = {'vehicle_id': identity, 'requested_depart_s': float(old_planned[identity].get('depart'))}
    for label, plans, trips in [('baseline', old_planned, old_trip), ('candidate', new_planned, new_trip)]:
        trip = trips.get(identity)
        entered = trip is not None and float(trip.get('depart')) < 3600
        row[label + '_entry'] = plans[identity].find('route').get('edges').split()[0]
        row[label + '_inserted'] = entered
        row[label + '_actual_depart_s'] = float(trip.get('depart')) if entered else None
        row[label + '_arrival_s'] = float(trip.get('arrival')) if entered and 0 <= float(trip.get('arrival')) < 3600 else None
        row[label + '_unentered_wait_lower_bound_s'] = None if entered else 3600 - row['requested_depart_s']
    records.append(row)
assert sum(r['baseline_inserted'] for r in records) == baseline_completion['inserted']
assert sum(r['candidate_inserted'] for r in records) == run['completion']['inserted']
assert sum(r['baseline_arrival_s'] is not None for r in records) == baseline_completion['arrived']
write_csv(OUT / 'all-due-vehicles.csv', records, list(records[0]))
entries = []
for label in ('baseline', 'candidate'):
    grouped = defaultdict(list)
    for row in records:
        grouped[row[label + '_entry']].append(row)
    for entry, members in sorted(grouped.items()):
        entries.append({'case': label, 'entry': entry, 'due_requests': len(members),
                        'inserted': sum(r[label + '_inserted'] for r in members),
                        'not_inserted': sum(not r[label + '_inserted'] for r in members)})
write_csv(OUT / 'entry-comparison.csv', entries, list(entries[0]))
passage = read_contributing_e1_passages(instant_file=N / 'instant-e1.xml', periodic_file=N / 'field-counts.xml',
    detector_ids=run['instant_to_periodic_ids'], step_length=.1)
assigned = {r['vehicle_id']: r['field_id'] for r in json.loads((P / 'lane-intentions.json').read_bytes())['vehicles']}
counts = Counter(r['vehicle_id'] for r in passage['passages'])
assert passage['reconciled'] and all(n == 1 for n in counts.values())
assert all(r['detector_id'] == assigned[r['vehicle_id']] for r in passage['passages'])
field_totals = []
for identity in sorted({r['detector_id'] for r in rows}):
    selected = [r for r in rows if r['detector_id'] == identity]
    field_totals.append({'detector_id': identity, **{key: sum(int(r[key]) for r in selected) for key in
        ('expected_total', 'baseline_nVehContrib', 'candidate_nVehContrib', 'change')}})
write_csv(OUT / 'all-13-field-totals.csv', field_totals, list(field_totals[0]))
report = {'formal_result': False, 'main_window_completed': False, 'native_end_s': 3600,
    'baseline_completion': baseline_completion, 'candidate_completion': run['completion'], 'requests_due': len(due),
    'future_requests_retained': 8640 - len(due), 'changed_initialization_requests': 73,
    'available_initialization_rows': 52, 'field_totals': field_totals,
    'baseline_E1_total': sum(r['baseline_nVehContrib'] for r in rows),
    'candidate_E1_total': sum(r['candidate_nVehContrib'] for r in rows),
    'baseline_diagnostic_MAE': sum(abs(r['baseline_residual']) for r in rows) / 52,
    'candidate_diagnostic_MAE': sum(abs(r['candidate_residual']) for r in rows) / 52,
    'baseline_max_abs': max(abs(r['baseline_residual']) for r in rows),
    'candidate_max_abs': max(abs(r['candidate_residual']) for r in rows),
    'baseline_exact_initialization_rows': sum(r['baseline_residual'] == 0 for r in rows),
    'candidate_exact_initialization_rows': sum(r['candidate_residual'] == 0 for r in rows),
    'main_field_rows_retained_unknown': 130, 'derived_station_rows_retained_unknown': 40,
    'native_E1_reconciled': True, 'all_observed_vehicles_once_at_assigned_field': True,
    'claim_boundary': 'Complete initialization-prefix comparison only. No main-window count improvement, measured OD, field capacity, full-demand safety or exact calibration is established. All unentered vehicles remain censored, and all future real targets remain unknown.'}
write_json_atomic(OUT / 'prefix-check.json', report)
print(json.dumps({k: report[k] for k in ('baseline_completion', 'candidate_completion', 'baseline_E1_total', 'candidate_E1_total', 'baseline_diagnostic_MAE', 'candidate_diagnostic_MAE')}), flush=True)
