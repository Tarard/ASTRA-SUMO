"""Test two distinct native detector contributions on simple supported routes."""

from collections import Counter, defaultdict
from copy import deepcopy
import gzip
import json
from pathlib import Path

import sumo
import xml.etree.ElementTree as ET

from astra_sumo.core.artifact_io import write_json_atomic
from astra_sumo.core.detector_demand import read_net, write_csv
from astra_sumo.core.detector_demand_replay import run_point_detector_demand
from astra_sumo.core.demand_route_pool import junction_visit_audit
from astra_sumo.core.hamburg_collision_trace import read_trace_source
from astra_sumo.core.local_network_conversion import loaded_turn_permissions
from astra_sumo.core.route_sensor_matrix import read_contributing_e1_passages

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'benchmarks/hamburg_ring1_primary_entry_full_20261022_v1'
OUT = ROOT / 'outputs/ring1-shared-field-route-probe-20261022-v1'
OUT.mkdir(exist_ok=False)
for name in ('network.net.xml', 'control.add.xml', 'expected-counts.csv'):
    (OUT / name).write_bytes(read_trace_source(SOURCE / 'inputs', name))
profiles = json.loads(gzip.decompress((ROOT / 'benchmarks/hamburg_ring1_m1_2_demand_pool_20261021_v1/base/ideal-lane-profiles.json.gz').read_bytes()))['profiles']
network = ET.parse(OUT / 'network.net.xml').getroot()
permissions = loaded_turn_permissions(network)
edges, links = read_net(OUT / 'network.net.xml')
native = {(c.get('from'), int(c.get('fromLane')), c.get('to'), int(c.get('toLane'))): c for c in network.findall('connection')}
bindings = {tuple(r['physical_key']): r for r in json.loads(gzip.decompress((ROOT / 'benchmarks/hamburg_ring1_m2_control_20261021_v1/link-bindings.json.gz').read_bytes()))['rows']}
owners = {j.get('id'): j.get('id').rsplit('_part', 1)[0] for j in network.findall('junction') if j.get('id', '').startswith('LSA')}
loops = ET.fromstring(read_trace_source(SOURCE / 'inputs', 'field-loops.add.xml'))
ordered_fields = [r.get('id') for r in loops]
for loop in loops:
    loop.set('period', '1800')
    loop.attrib.pop('freq', None)
ET.ElementTree(loops).write(OUT / 'artificial-loops.add.xml', encoding='utf-8', xml_declaration=True)
full = ET.fromstring(read_trace_source(SOURCE / 'inputs', 'demand.rou.xml'))
source_intent = {r['vehicle_id']: r for r in json.loads(read_trace_source(SOURCE / 'inputs', 'lane-intentions.json'))['vehicles']}
templates = {}
first_fields = {1: 'hh_0119_Z_12_31546', 2: 'hh_0119_Z_13_34018'}
second_field = 'hh_0535_Z_4_33055'
for vehicle in full.findall('vehicle'):
    field = source_intent[vehicle.get('id')]['field_id']
    if field in first_fields.values() and vehicle.find('route').get('edges').split()[0] == '44532531':
        templates.setdefault(field, vehicle)
assert set(templates) == set(first_fields.values())
demand = ET.Element('routes')
demand.extend(deepcopy(v) for v in full.findall('vType'))
intentions, expected_fields, audits = [], {}, {}
for index in (178, 179, 180, 181):
    profile = profiles[index]
    measured = [field for i, field in enumerate(ordered_fields) if profile['field_mask'] & (1 << i)]
    first = first_fields[1 if index in (178, 180) else 2]
    assert set(measured) == {first, second_field} and profile['edges'][0] == '44532531'
    turns = [(a, profile['exit_lanes'][i], b, profile['entry_lanes'][i + 1]) for i, (a, b) in enumerate(zip(profile['edges'], profile['edges'][1:]))]
    audit = junction_visit_audit(profile['edges'], edges, links, owners)
    assert audit['allowed'] and len(profile['edges']) == len(set(profile['edges']))
    assert all('passenger' in permissions.get(k, ()) and native[k].get('dir') not in {'t', 'T'} for k in turns)
    assert all(bindings.get(k, {}).get('groups') for k in turns if native[k].get('tl', '').startswith('LSA'))
    assert not any(network.find(f"edge[@id='{edge}']/param[@key='access'][@value='customers']") is not None for edge in profile['edges'])
    audits[str(index)] = audit
for i in range(40):
    index = (178, 179, 180, 181)[i % 4]
    first = first_fields[1 if index in (178, 180) else 2]
    vehicle = deepcopy(templates[first])
    identity = f'artificial_shared_p{index}_{i}'
    vehicle.set('id', identity)
    vehicle.set('depart', str(100 + 4 * i))
    vehicle.find('route').set('edges', ' '.join(profiles[index]['edges']))
    assert all(stop.get('lane').rsplit('_', 1)[0] in profiles[index]['edges'] for stop in vehicle.findall('stop'))
    demand.append(vehicle)
    intentions.append({'vehicle_id': identity, 'field_id': first})
    expected_fields[identity] = [first, second_field]
ET.ElementTree(demand).write(OUT / 'demand.rou.xml', encoding='utf-8', xml_declaration=True)
write_json_atomic(OUT / 'intentions.json', {'vehicles': intentions})
targets = [{'detector_id': r.get('id'), 'begin': 0, 'end': 1800,
    'expected_total': 20 if r.get('id') in first_fields.values() else 40 if r.get('id') == second_field else 0} for r in loops]
write_csv(OUT / 'artificial-targets.csv', targets, list(targets[0]))
write_json_atomic(OUT / 'proposal.json', {'formal_result': False, 'vehicles': 40,
    'profiles': [178, 179, 180, 181], 'expected_fields_by_vehicle': expected_fields, 'route_audits': audits,
    'driver': 'Existing first-field intention and the same three-node body hold only. No second-field hold, extra waypoint or signal change.',
    'expected_native_contributions': 80,
    'claim_boundary': 'Artificial route-signature test, not calibrated OD, observed unique-vehicle totals, a native capacity proof or the real 130-row fit. Different fields may share one vehicle; no road, junction or detector repeats are allowed. All original real targets remain in the source case.'})
run = run_point_detector_demand(net_file=OUT / 'network.net.xml', route_file=OUT / 'demand.rou.xml',
    e1_additional_file=OUT / 'artificial-loops.add.xml', expected_counts_csv=OUT / 'artificial-targets.csv',
    output_dir=OUT / 'native', end_s=1800, sumo_binary=str(Path(sumo.__file__).parent / 'bin/sumo.exe'), timeout_seconds=240,
    signal_program_file=OUT / 'control.add.xml', lane_intentions_file=OUT / 'intentions.json',
    junction_body_hold=True, junction_body_hold_nodes=('14398547', '205069138', '18653534'))
passage = read_contributing_e1_passages(instant_file=OUT / 'native/instant-e1.xml', periodic_file=OUT / 'native/field-counts.xml',
    detector_ids=run['instant_to_periodic_ids'], step_length=.1)
seen = defaultdict(Counter)
times = defaultdict(dict)
for row in passage['passages']:
    seen[row['vehicle_id']][row['detector_id']] += 1
    times[row['vehicle_id']][row['detector_id']] = row['time_s']
signature = set(seen) == set(expected_fields) and all(seen[identity] == Counter(fields) for identity, fields in expected_fields.items())
ordered = signature and all(times[identity][fields[0]] < times[identity][fields[1]] for identity, fields in expected_fields.items())
trips = ET.parse(OUT / 'native/tripinfo.xml').getroot().findall('tripinfo')
report = {'formal_result': False, 'native_version': run['stall_monitor']['native_version'],
    'completion': run['completion'], 'healthy': run['healthy'], 'observer_complete': run['observer_complete'],
    'exact_artificial_rows': run['exact_field_bins'], 'native_E1_reconciled': passage['reconciled'],
    'native_contributions': len(passage['passages']), 'vehicle_signatures_matched': signature,
    'first_then_second_order_verified': ordered, 'native_field_totals': dict(Counter(r['detector_id'] for r in passage['passages'])),
    'sum_native_stop_time_s': sum(float(t.get('stopTime')) for t in trips),
    'passage_times_by_vehicle': dict(times),
    'claim_boundary': 'A larger shared-population demand model remains a separate untested hypothesis. No claim about the real main-window counts or true vehicle identity is established.'}
write_json_atomic(OUT / 'joint-response-check.json', report)
print(json.dumps({k: v for k, v in report.items() if k != 'passage_times_by_vehicle'}), flush=True)
