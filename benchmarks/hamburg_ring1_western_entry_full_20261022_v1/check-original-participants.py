"""Separate changed routes from unfinished original maneuver checks."""

import gzip
import json
from pathlib import Path
import xml.etree.ElementTree as ET

from astra_sumo.core.artifact_io import write_json_atomic
from astra_sumo.core.hamburg_collision_trace import read_trace_source

CASE = Path(__file__).resolve().parent
plans = {v.get('id'): v.find('route').get('edges').split()
    for v in ET.fromstring(read_trace_source(CASE / 'inputs', 'demand.rou.xml')).findall('vehicle')}
witnesses = json.loads((CASE / 'analysis/original-participant-check.json').read_bytes())['vehicles']
rows = []
for identity, witness in witnesses.items():
    applicable = witness['outlet'] in plans[identity]
    cleared = witness['first_rear_clear_sample'] is not None
    status = 'bypassed_original_site' if not applicable else 'rear_clear_observed' if cleared else 'original_site_not_demonstrated'
    rows.append({'vehicle_id': identity, 'original_node': witness['node'], 'original_outlet': witness['outlet'],
        'original_outlet_on_current_route': applicable, 'original_site_status': status,
        'native_arrival_s': witness['native_arrival_s']})
write_json_atomic(CASE / 'analysis/original-participant-route-correspondence.json', {
    'formal_result': False, 'vehicles': rows, 'applicable_original_sites': sum(r['original_outlet_on_current_route'] for r in rows),
    'bypassed_original_sites': sum(not r['original_outlet_on_current_route'] for r in rows),
    'claim_boundary': 'One changed road prefix bypasses its original collision site. Another historical ID has no completed arrival or demonstrated original maneuver. Missing samples do not diagnose a new clearance fault. The full run remains invalid.'})
source = CASE / 'native/participants-fcd.xml.gz'
last, time = {}, None
with gzip.open(source, 'rb') as stream:
    for event, row in ET.iterparse(stream, events=('start', 'end')):
        if event == 'start' and row.tag == 'timestep':
            time = float(row.get('time'))
        if event == 'end':
            if row.tag == 'vehicle' and row.get('id') == 'readme_f4_b4_p208_v5':
                last = dict(row.attrib, time_s=time)
            row.clear()
write_json_atomic(CASE / 'analysis/unfinished-historical-participant-sample.json', {'formal_result': False, 'sample': last,
    'claim_boundary': 'Native position and speed only. No causal blocking cycle or eventual maneuver safety is inferred.'})
print(json.dumps({'sites_applicable': sum(r['original_outlet_on_current_route'] for r in rows),
    'original_rear_clear_observed': sum(r['original_site_status'] == 'rear_clear_observed' for r in rows),
    'bypassed_original_sites': sum(not r['original_outlet_on_current_route'] for r in rows), 'unfinished_historical_vehicle': last}))
