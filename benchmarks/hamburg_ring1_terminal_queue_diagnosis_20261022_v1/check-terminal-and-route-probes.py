"""Check snapshot correspondence, observation neutrality and distinct-field signatures."""

from collections import Counter
from copy import deepcopy
import gzip
import hashlib
import json
from pathlib import Path
import statistics
import xml.etree.ElementTree as ET

from astra_sumo.core.artifact_io import write_json_atomic
from astra_sumo.core.detector_demand import write_csv
from astra_sumo.core.hamburg_collision_trace import read_trace_source
from astra_sumo.core.route_sensor_matrix import read_contributing_e1_passages

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / 'benchmarks/hamburg_ring1_primary_entry_full_20261022_v1'
QUERY = ROOT / 'outputs/ring1-primary-entry-terminal-state-20261022-v2'
LANES = ROOT / 'outputs/ring1-z15-buffer-lane-probe-20261022-v2'
SHARED = ROOT / 'outputs/ring1-shared-field-route-probe-20261022-v1'
OUT = ROOT / 'outputs/ring1-terminal-and-route-probe-checks-20261022-v1'
OUT.mkdir(exist_ok=False)
original = ET.fromstring(read_trace_source(BASE / 'native/states', 'state_14399.900.xml'))
inspection = ET.fromstring(gzip.decompress((QUERY / 'inspection-state.xml.gz').read_bytes()))
expected = deepcopy(original)
empty = []
for row in expected.findall('tlLogic'):
    if row.get('state') == '':
        empty.append([row.get('id'), row.get('programID')])
        del row.attrib['state']
assert ET.tostring(expected) == ET.tostring(inspection)
active = {identity for lane in original.findall('lane') for identity in lane.find('vehicles').get('value', '').split()}
saved = {v.get('id') for v in original.findall('vehicle')}
trips = {v.get('id') for v in ET.fromstring(read_trace_source(BASE / 'native', 'tripinfo.xml')).findall('tripinfo')}
full = {v.get('id') for v in ET.fromstring(read_trace_source(BASE / 'inputs', 'demand.rou.xml')).findall('vehicle')}
assert saved - active == full - trips and len(saved - active) == 644
query = json.loads((QUERY / 'native-state-query.json').read_bytes())
assert query['snapshot_time_s'] == query['time_after_query_s'] == 14399.9 and query['simulation_steps'] == 0
assert not query['state_mismatches'] and not query['tls_program_or_phase_mismatches']
assert len(query['loaded_mode_mismatches_with_recorded_controller']) == 24
mode_types = Counter(tuple(query['vehicles'][v]['recorded_hold_reasons']) for v in query['loaded_mode_mismatches_with_recorded_controller'])
write_json_atomic(OUT / 'snapshot-correspondence.json', {'formal_result': False,
    'snapshot_vehicles': len(saved), 'active_vehicles_verified': len(active), 'original_unentered_vehicle_count': 644,
    'loaded_pending_api_count': len(query['pending_vehicle_ids']),
    'loaded_pending_api_is_not_the_original_insertion_queue': True,
    'only_private_copy_changes': empty, 'time_unchanged': True, 'simulation_steps': 0,
    'mode_mismatches': 24, 'mode_mismatches_by_recorded_hold': {'+'.join(k): v for k, v in mode_types.items()},
    'original_approaching_link_records': len(original.findall('.//link')),
    'claim_boundary': 'Original pending IDs are reconstructed from original vehicle/lane records and independently match full-run uninserted IDs. Loaded API pending=0 and missing influencer modes do not replace the original counts. Query wait-cycle and foe results do not prove absence of original-runtime deadlock or establish restart fidelity.'})


def fcd_signature(path):
    digest = hashlib.sha256()
    for _, row in ET.iterparse(path):
        if row.tag == 'timestep':
            value = [row.get('time'), sorted((v.get('id'), sorted(v.attrib.items())) for v in row)]
            digest.update(json.dumps(value, separators=(',', ':')).encode('utf-8'))
            row.clear()
    return digest.hexdigest()


period_checks = {}
field_times = {}
for label in ('baseline', 'two_lane_intent'):
    old = ROOT / 'outputs/ring1-z15-buffer-lane-probe-20261022-v1' / label / 'native'
    new = LANES / label / 'native'
    a = [r.attrib for r in ET.parse(old / 'tripinfo.xml').getroot()]
    b = [r.attrib for r in ET.parse(new / 'tripinfo.xml').getroot()]
    assert a == b and fcd_signature(old / 'fcd.xml') == fcd_signature(new / 'fcd.xml')
    run = json.loads((new / 'run.json').read_bytes())
    assert run['observer_complete'] and run['exact_field_bins'] == 13
    passage = read_contributing_e1_passages(instant_file=new / 'instant-e1.xml', periodic_file=new / 'field-counts.xml', detector_ids=run['instant_to_periodic_ids'], step_length=.1)
    assert passage['reconciled'] and len(passage['passages']) == 40
    field_times[label] = {r['vehicle_id']: r['time_s'] for r in passage['passages']}
    period_checks[label] = {'same_tripinfo_attributes': True, 'same_every_FCD_vehicle_sample': True,
                            'corrected_observer_complete': True, 'native_contributions': 40}
deltas = [field_times['two_lane_intent'][v] - time for v, time in field_times['baseline'].items()]
write_json_atomic(OUT / 'buffer-probe-check.json', {'formal_result': False,
    'period_correction': 'Version 1 added freq=1800 but retained period=900. Its requested aggregate rows were missing. Version 2 sets period=1800 and removes freq. All actual traffic samples match exactly between versions.',
    'observation_neutrality': period_checks, 'candidate_minus_baseline_E1_time_s': {
        'min': min(deltas), 'median': statistics.median(deltas), 'max': max(deltas), 'mean': statistics.mean(deltas)},
    'baseline_last_E1_time_s': max(field_times['baseline'].values()),
    'candidate_last_E1_time_s': max(field_times['two_lane_intent'].values()),
    'promoted_as_capacity_fix': False,
    'claim_boundary': 'A lane-use change is observed, but no additional E1 contributions or faster final arrival are established by this sparse test. It does not establish the capacity or cause of the dense original queue.'})
shared = json.loads((SHARED / 'joint-response-check.json').read_bytes())
assert shared['healthy'] and shared['observer_complete'] and shared['exact_artificial_rows'] == 13
assert shared['native_contributions'] == 80 and shared['vehicle_signatures_matched'] and shared['first_then_second_order_verified']
assert shared['completion']['arrived'] == 40 and shared['completion']['collisions'] == shared['completion']['teleports'] == 0
proposal = json.loads((SHARED / 'proposal.json').read_bytes())
latencies = []
rows = []
for identity, fields in proposal['expected_fields_by_vehicle'].items():
    times = shared['passage_times_by_vehicle'][identity]
    delta = times[fields[1]] - times[fields[0]]
    assert delta > 0
    latencies.append(delta)
    rows.append({'vehicle_id': identity, 'first_field': fields[0], 'first_time_s': times[fields[0]],
                 'second_field': fields[1], 'second_time_s': times[fields[1]], 'transfer_time_s': delta})
write_csv(OUT / 'shared-vehicle-passages.csv', rows, list(rows[0]))
write_json_atomic(OUT / 'shared-route-check.json', {'formal_result': False, 'vehicles': 40, 'distinct_field_contributions': 80,
    'first_field_totals': {'hh_0119_Z_12_31546': 20, 'hh_0119_Z_13_34018': 20},
    'second_field_total': {'hh_0535_Z_4_33055': 40}, 'sum_native_stop_time_s': shared['sum_native_stop_time_s'],
    'transfer_time_s': {'min': min(latencies), 'median': statistics.median(latencies), 'max': max(latencies)},
    'driver_schema_changed': False, 'larger_shared_population_validated': False,
    'claim_boundary': 'The existing first-field driver suffices for these four tested supported routes. One vehicle may contribute once at each of two different fields without turnarounds, repeated roads or repeated physical junctions. This does not identify observed real vehicle totals, prove a capacity gain or calibrate the 130 real rows.'})
print(json.dumps({'snapshot_modes_not_restored': 24, 'source_unentered': 644,
    'buffer_E1_time_delta_mean_s': statistics.mean(deltas), 'shared_transfer_s': [min(latencies), statistics.median(latencies), max(latencies)],
    'shared_distinct_contributions': 80}), flush=True)
