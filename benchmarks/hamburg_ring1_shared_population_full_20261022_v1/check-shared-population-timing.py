"""Measure changed-flow timing errors without treating them as physical infeasibility."""

import json
from pathlib import Path
import shutil
import statistics
import xml.etree.ElementTree as ET

from astra_sumo.core.artifact_io import write_json_atomic
from astra_sumo.core.detector_demand import write_csv
from astra_sumo.core.hamburg_collision_trace import read_trace_source

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / 'benchmarks/hamburg_ring1_primary_entry_full_20261022_v1'
CASE = ROOT / 'benchmarks/hamburg_ring1_shared_population_full_20261022_v1'
intent = {v['vehicle_id']: v for v in json.loads(read_trace_source(CASE / 'inputs', 'lane-intentions.json'))['vehicles']}
pairs = json.loads(read_trace_source(CASE / 'inputs', 'shared-population.json'))['pairings']
actual = {(r['vehicle_id'], r['detector_id']): r for r in json.loads(read_trace_source(CASE, 'passage-reconciliation.json'))['passages']}
old_trip = {v.get('id'): v.attrib for v in ET.fromstring(read_trace_source(BASE / 'native', 'tripinfo.xml')).findall('tripinfo')}
new_trip = {v.get('id'): v.attrib for v in ET.fromstring(read_trace_source(CASE / 'native', 'tripinfo.xml')).findall('tripinfo')}
rows = []
for pair in pairs:
    identity = pair['parent_vehicle_id']
    targets = intent[identity]['target_observations']
    first, second = [actual[identity, t['field_id']]['time_s'] for t in targets]
    bounds = [(t['target_begin_s'] + 90 - time, t['target_end_s'] - 90 - time) for t, time in zip(targets, (first, second))]
    rows.append({'vehicle_id': identity, 'first_response_change_s': first - pair['source_first_time_s'],
        'second_prediction_error_s': second - pair['second_time_proxy_s'], 'actual_transfer_s': second - first,
        'first_in_target_bin': targets[0]['target_begin_s'] <= first < targets[0]['target_end_s'],
        'second_in_target_bin': targets[1]['target_begin_s'] <= second < targets[1]['target_end_s'],
        'frozen_uniform_shift_window_nonempty': max(x[0] for x in bounds) <= min(x[1] for x in bounds)})
write_csv(CASE / 'analysis/pair-timing-errors.csv', rows, list(rows[0]))


def summary(values):
    return {'min': min(values), 'median': statistics.median(values), 'max': max(values)}


removed = {p['removed_model_vehicle_id'] for p in pairs}
retained = set(intent)
report = {'formal_result': False, 'pairs': len(pairs),
    'second_prediction_error_s': summary([r['second_prediction_error_s'] for r in rows]),
    'first_response_change_s': summary([r['first_response_change_s'] for r in rows]),
    'actual_transfer_s': summary([r['actual_transfer_s'] for r in rows]),
    'pairs_first_within_own_target': sum(r['first_in_target_bin'] for r in rows),
    'pairs_second_within_own_target': sum(r['second_in_target_bin'] for r in rows),
    'frozen_uniform_shift_incompatible_pairs': sum(not r['frozen_uniform_shift_window_nonempty'] for r in rows),
    'baseline_retained_ids_inserted': sum(v in retained for v in old_trip), 'candidate_retained_ids_inserted': len(new_trip),
    'baseline_retained_ids_arrived': sum(v in retained and float(r['arrival']) >= 0 for v, r in old_trip.items()),
    'candidate_retained_ids_arrived': sum(float(r['arrival']) >= 0 for r in new_trip.values()),
    'removed_synthetic_ids_baseline_inserted': sum(v in old_trip for v in removed),
    'removed_synthetic_ids_baseline_arrived': sum(v in old_trip and float(old_trip[v]['arrival']) >= 0 for v in removed),
    'claim_boundary': 'Timing errors describe this replay. Uniform-shift incompatibility holds only for frozen measured times and a 90 s margin. Synthetic target associations are not observed vehicle identities; this is not physical infeasibility or a proof against changed schedules.'}
write_json_atomic(CASE / 'analysis/pair-timing-check.json', report)
shutil.copyfile(Path(__file__), CASE / 'check-shared-population-timing.py')
print(json.dumps({k: report[k] for k in ('pairs', 'second_prediction_error_s', 'frozen_uniform_shift_incompatible_pairs')}))
