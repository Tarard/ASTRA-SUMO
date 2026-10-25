"""Compare observation contributions separately from the changed unique vehicle population."""

from collections import Counter, defaultdict
from io import BytesIO
import json
from pathlib import Path
import xml.etree.ElementTree as ET

from astra_sumo.core.artifact_io import write_json_atomic
from astra_sumo.core.detector_demand import audit_expected_to_e1_strict, read_csv_rows, write_csv
from astra_sumo.core.hamburg_collision_trace import read_trace_source
from astra_sumo.core.hamburg_readme_results import STATIONS, vehicle_field_passage_rows
from astra_sumo.core.route_sensor_matrix import read_contributing_e1_passages

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / 'benchmarks/hamburg_ring1_primary_entry_full_20261022_v1'
P = ROOT / 'outputs/ring1-shared-population-inputs-20261022-v2'
N = ROOT / 'outputs/ring1-shared-population-prefix-20261022-v1'
OUT = ROOT / 'outputs/ring1-shared-population-prefix-analysis-20261022-v1'
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
source_main = read_csv_rows(P / 'expected-counts.csv')
main = [{**a, **b} for a, b in zip(source_main, audit_expected_to_e1_strict(source_main, N / 'field-counts.xml'))]
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
for _, row in ET.iterparse(BytesIO(read_trace_source(BASE / 'native', 'summary.xml'))):
    if row.tag == 'step' and float(row.get('time')) < 3600:
        baseline_step = dict(row.attrib)
    row.clear()
baseline_completion = {key: int(baseline_step[key]) for key in ('loaded', 'inserted', 'arrived', 'running', 'waiting', 'collisions', 'teleports')}
planned = {v.get('id'): v for v in ET.parse(P / 'demand.rou.xml').getroot().findall('vehicle')}
due = {identity for identity, v in planned.items() if float(v.get('depart')) < 3600}
trips = {v.get('id'): v for v in ET.parse(N / 'tripinfo.xml').getroot().findall('tripinfo')}
assert len(due) == 1848 and len(due - trips.keys()) == run['completion']['waiting']
intentions = json.loads((P / 'lane-intentions.json').read_bytes())['vehicles']
by_id = {r['vehicle_id']: r for r in intentions}
passage = read_contributing_e1_passages(instant_file=N / 'instant-e1.xml', periodic_file=N / 'field-counts.xml',
    detector_ids=run['instant_to_periodic_ids'], step_length=.1)
assert passage['reconciled']
seen = defaultdict(dict)
unexpected, repeated = [], []
for row in passage['passages']:
    identity, field = row['vehicle_id'], row['detector_id']
    if field not in by_id[identity]['expected_field_ids']:
        unexpected.append(row)
    if field in seen[identity]:
        repeated.append(row)
    seen[identity][field] = row['time_s']
second_without_first, wrong_order = [], []
for identity, fields in ((v['vehicle_id'], v['expected_field_ids']) for v in intentions if len(v['expected_field_ids']) == 2):
    if fields[1] in seen[identity] and fields[0] not in seen[identity]:
        second_without_first.append(identity)
    elif all(field in seen[identity] for field in fields) and seen[identity][fields[0]] >= seen[identity][fields[1]]:
        wrong_order.append(identity)
observation_rows = vehicle_field_passage_rows(intentions, passage)
assert len(observation_rows) == 8640
write_csv(OUT / 'all-declared-observation-passages.csv', observation_rows, list(dict.fromkeys(k for r in observation_rows for k in r)))
field_totals = []
for field in sorted({r['detector_id'] for r in rows}):
    selected = [r for r in rows if r['detector_id'] == field]
    field_totals.append({'detector_id': field, **{key: sum(int(r[key]) for r in selected) for key in
        ('expected_total', 'baseline_nVehContrib', 'candidate_nVehContrib', 'change')}})
write_csv(OUT / 'all-13-field-totals.csv', field_totals, list(field_totals[0]))
entry = Counter(v.find('route').get('edges').split()[0] for identity, v in planned.items() if identity in due)
inserted = Counter(planned[identity].find('route').get('edges').split()[0] for identity in trips)
entry_rows = [{'entry': edge, 'due_model_vehicles': count, 'inserted': inserted[edge], 'not_inserted': count - inserted[edge]} for edge, count in sorted(entry.items())]
write_csv(OUT / 'entry-loads.csv', entry_rows, list(entry_rows[0]))
report = {'formal_result': False, 'main_window_completed': False, 'native_end_s': 3600,
    'baseline_model_requests_due': 1953, 'candidate_model_requests_due': len(due),
    'initialization_field_target_contributions': 1953, 'candidate_total_model_vehicles': len(planned),
    'baseline_completion': baseline_completion, 'candidate_completion': run['completion'],
    'baseline_E1_total': sum(r['baseline_nVehContrib'] for r in rows),
    'candidate_E1_total': sum(r['candidate_nVehContrib'] for r in rows),
    'baseline_diagnostic_MAE': sum(abs(r['baseline_residual']) for r in rows) / 52,
    'candidate_diagnostic_MAE': sum(abs(r['candidate_residual']) for r in rows) / 52,
    'baseline_max_abs': max(abs(r['baseline_residual']) for r in rows), 'candidate_max_abs': max(abs(r['candidate_residual']) for r in rows),
    'baseline_exact_rows': sum(r['baseline_residual'] == 0 for r in rows), 'candidate_exact_rows': sum(r['candidate_residual'] == 0 for r in rows),
    'field_totals': field_totals, 'native_E1_reconciled': True,
    'unexpected_fields': unexpected, 'repeated_same_field': repeated, 'second_without_first': second_without_first, 'wrong_field_order': wrong_order,
    'observed_vehicles_with_both_fields': sum(len(fields) == 2 for fields in seen.values()),
    'prewindow_contributions_targeting_main': sum(r['population_role'] == 'main' and r['actual_native_time_s'] is not None for r in observation_rows),
    'main_field_rows_retained_unknown': 130, 'derived_station_rows_retained_unknown': 40,
    'claim_boundary': 'Complete initialization prefix only. Vehicle counts differ by construction and must be separated from unchanged detector quotas. Native contributions, not the exact target ledger, evaluate the shared-population hypothesis. No main-window calibration or true OD is established.'}
write_json_atomic(OUT / 'prefix-check.json', report)
print(json.dumps({k: report[k] for k in ('baseline_completion', 'candidate_completion', 'baseline_E1_total', 'candidate_E1_total',
    'baseline_diagnostic_MAE', 'candidate_diagnostic_MAE', 'observed_vehicles_with_both_fields')}), flush=True)
