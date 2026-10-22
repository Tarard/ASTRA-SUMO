"""Test the existing western Ring 1 entry on the two 119 fields."""

from collections import Counter
from copy import deepcopy
import gzip
import hashlib
import json
from pathlib import Path
import xml.etree.ElementTree as ET

import sumo

from astra_sumo.core.artifact_io import write_json_atomic
from astra_sumo.core.detector_demand import read_net, write_csv
from astra_sumo.core.detector_demand_replay import run_point_detector_demand
from astra_sumo.core.demand_route_pool import junction_visit_audit
from astra_sumo.core.hamburg_collision_trace import read_trace_source
from astra_sumo.core.local_network_conversion import loaded_turn_permissions
from astra_sumo.core.route_sensor_matrix import read_contributing_e1_passages

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'benchmarks/hamburg_ring1_primary_entry_full_20261022_v1'
OUT = ROOT / 'outputs/ring1-western-primary-entry-route-probe-20261022-v1'
OUT.mkdir(exist_ok=False)
for name in ('network.net.xml', 'control.add.xml'):
    (OUT / name).write_bytes(read_trace_source(SOURCE / 'inputs', name))
profile_file = ROOT / 'benchmarks/hamburg_ring1_m1_2_demand_pool_20261021_v1/base/ideal-lane-profiles.json.gz'
binding_file = ROOT / 'benchmarks/hamburg_ring1_m2_control_20261021_v1/link-bindings.json.gz'
profiles = json.loads(gzip.decompress(profile_file.read_bytes()))['profiles']
bindings = {tuple(r['physical_key']): r for r in json.loads(gzip.decompress(binding_file.read_bytes()))['rows']}
network = ET.parse(OUT / 'network.net.xml').getroot()
permissions = loaded_turn_permissions(network)
edges, links = read_net(OUT / 'network.net.xml')
native = {(c.get('from'), int(c.get('fromLane')), c.get('to'), int(c.get('toLane'))): c for c in network.findall('connection')}
owners = {j.get('id'): j.get('id').rsplit('_part', 1)[0] for j in network.findall('junction') if j.get('id', '').startswith('LSA')}
loops = ET.fromstring(read_trace_source(SOURCE / 'inputs', 'field-loops.add.xml'))
fields = {304: 'hh_0119_Z_4_29437', 305: 'hh_0119_Z_5_30494'}
for loop in loops:
    loop.set('period', '1800')
    loop.attrib.pop('freq', None)
ET.ElementTree(loops).write(OUT / 'artificial-loops.add.xml', encoding='utf-8', xml_declaration=True)
full = ET.fromstring(read_trace_source(SOURCE / 'inputs', 'demand.rou.xml'))
intent = {r['vehicle_id']: r for r in json.loads(read_trace_source(SOURCE / 'inputs', 'lane-intentions.json'))['vehicles']}
templates = {index: next(v for v in full.findall('vehicle') if intent[v.get('id')]['profile_index'] == old)
    for index, old in ((304, 55), (305, 56))}
audits = {}
for index in fields:
    p = profiles[index]
    turns = [(a, p['exit_lanes'][i], b, p['entry_lanes'][i + 1]) for i, (a, b) in enumerate(zip(p['edges'], p['edges'][1:]))]
    audit = junction_visit_audit(p['edges'], edges, links, owners)
    assert audit['allowed'] and p['edges'][0] == '963776077#0'
    assert all('passenger' in permissions.get(k, ()) and native[k].get('dir') not in {'t', 'T'} for k in turns)
    assert all(bindings.get(k, {}).get('groups') for k in turns if native[k].get('tl', '').startswith('LSA'))
    assert not any(network.find(f"edge[@id='{edge}']/param[@key='access'][@value='customers']") is not None for edge in p['edges'])
    audits[str(index)] = audit
demand = ET.Element('routes')
demand.extend(deepcopy(v) for v in full.findall('vType'))
intentions, expected = [], {}
for i in range(40):
    index = 304 + i % 2
    vehicle = deepcopy(templates[index])
    identity = f'artificial_west_primary_p{index}_{i}'
    vehicle.set('id', identity)
    vehicle.set('depart', str(100 + 4 * i))
    vehicle.find('route').set('edges', ' '.join(profiles[index]['edges']))
    demand.append(vehicle)
    intentions.append({'vehicle_id': identity, 'field_id': fields[index]})
    expected[identity] = fields[index]
ET.ElementTree(demand).write(OUT / 'demand.rou.xml', encoding='utf-8', xml_declaration=True)
write_json_atomic(OUT / 'intentions.json', {'vehicles': intentions})
targets = [{'detector_id': r.get('id'), 'begin': 0, 'end': 1800,
    'expected_total': 20 if r.get('id') in fields.values() else 0} for r in loops]
write_csv(OUT / 'artificial-targets.csv', targets, list(targets[0]))
write_json_atomic(OUT / 'proposal.json', {'formal_result': False, 'profiles': [304, 305],
    'vehicles': 40, 'expected_fields_by_vehicle': expected, 'route_audits': audits,
    'source_sha256': {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in (profile_file, binding_file, SOURCE / 'manifest.json')},
    'driver': 'Existing first-field waypoint and three-node body hold. Same network, permissions, signals and vehicle type.',
    'claim_boundary': 'Artificial route-response check only. The service driveway is not assigned extra demand. Dense entry allocation and real 130/40 count calibration remain untested.'})
binary = Path(sumo.__file__).parent / 'bin/sumo.exe'
run = run_point_detector_demand(net_file=OUT / 'network.net.xml', route_file=OUT / 'demand.rou.xml',
    e1_additional_file=OUT / 'artificial-loops.add.xml', expected_counts_csv=OUT / 'artificial-targets.csv', output_dir=OUT / 'native',
    end_s=1800, sumo_binary=str(binary), timeout_seconds=240, signal_program_file=OUT / 'control.add.xml',
    lane_intentions_file=OUT / 'intentions.json', junction_body_hold=True,
    junction_body_hold_nodes=('14398547', '205069138', '18653534'))
passage = read_contributing_e1_passages(instant_file=OUT / 'native/instant-e1.xml', periodic_file=OUT / 'native/field-counts.xml',
    detector_ids=run['instant_to_periodic_ids'], step_length=.1)
frequency = Counter((r['vehicle_id'], r['detector_id']) for r in passage['passages'])
trips = ET.parse(OUT / 'native/tripinfo.xml').getroot().findall('tripinfo')
report = {'formal_result': False, 'native_version': run['stall_monitor']['native_version'],
    'completion': run['completion'], 'healthy': run['healthy'], 'observer_complete': run['observer_complete'],
    'native_E1_reconciled': passage['reconciled'], 'native_contributions': len(passage['passages']),
    'vehicle_signatures_matched': frequency == Counter((identity, field) for identity, field in expected.items()),
    'native_field_totals': dict(Counter(r['detector_id'] for r in passage['passages'])),
    'sum_native_stop_time_s': sum(float(t.get('stopTime')) for t in trips),
    'claim_boundary': 'Only this sparse route check. No true OD, larger-demand capacity or main-window count fit is established.'}
write_json_atomic(OUT / 'route-response-check.json', report)
print(json.dumps(report), flush=True)
