"""Check the driver perturbation, every count residual and original participants."""

from collections import defaultdict
import json
from pathlib import Path
import xml.etree.ElementTree as ET

from astra_sumo.core.artifact_io import write_json_atomic
from astra_sumo.core.detector_demand import read_csv_rows, write_csv
from astra_sumo.core.hamburg_collision_trace import read_trace_source

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / 'benchmarks/hamburg_ring1_through_route_coverage_20261021_v1'
NATIVE = ROOT / 'outputs/ring1-junction-body-hold-native-20261021-v1'
OUT = ROOT / 'outputs/ring1-junction-body-hold-analysis-20261021-v1'
OUT.mkdir(exist_ok=False)
for name in ('network.net.xml', 'demand.rou.xml', 'signal-programs.add.xml', 'source-e1.add.xml', 'lane-intentions.json', 'expected-counts.csv'):
    assert (NATIVE/name).read_bytes() == read_trace_source(BASE/'native', name)
request = json.loads((NATIVE/'native-request.json').read_bytes())
assert request['junction_body_hold'] is True
monitor = json.loads((NATIVE/'stall-monitor.json').read_bytes())
assert monitor['native_version'] == [22, 'SUMO 1.27.1']
assert '/.venv/' in monitor['traci_module'].replace('\\', '/')
report = json.loads((NATIVE/'junction-body-check.json').read_bytes())
parent = json.loads(read_trace_source(BASE/'native', 'initialization-check.json'))
before = {(r['detector_id'], r['begin'], r['end']):r for r in read_csv_rows(BASE/'native/field-comparison.csv')}
rows = read_csv_rows(NATIVE/'field-comparison.csv')
assert len(rows) == len(before) == 52
comparison = []
for r in rows:
    old = before[r['detector_id'], r['begin'], r['end']]
    assert old['expected_total'] == r['expected_total']
    delta = None if r['diff_nVehContrib_minus_expected'] == '' else abs(int(r['diff_nVehContrib_minus_expected'])) - abs(int(old['diff_nVehContrib_minus_expected']))
    comparison.append({**r, 'baseline_nVehContrib':old['measured_nVehContrib'], 'absolute_error_change':delta})
write_csv(OUT/'field-change.csv', comparison, list(comparison[0]))
intervals, active = defaultdict(list), {}
events = monitor['lane_section_hold']['junction_body_hold']['events']
for event in events:
    identity = event['vehicle_id']
    if event['event'] == 'held':
        assert identity not in active
        active[identity] = event['time_s']
    else:
        intervals[identity].append((active.pop(identity), event['time_s']))
for identity, start in active.items():
    intervals[identity].append((start, float('inf')))
assert active.keys() == monitor['lane_section_hold']['junction_body_hold']['still_held'].keys()
changes = ET.parse(NATIVE/'lanechanges.xml').getroot().findall('change')
violations = [r.attrib for r in changes if any(start <= float(r.get('time')) < end-1e-9 for start,end in intervals[r.get('id')])]
unheld_internal = [r.attrib for r in changes if r.get('from').startswith(':') and not any(start <= float(r.get('time')) < end-1e-9 for start,end in intervals[r.get('id')])]
write_json_atomic(OUT/'hold-audit.json', {'held_intervals':sum(map(len, intervals.values())), 'changes_during_active_body_hold':violations,
    'internal_changes_outside_body_hold':unheld_internal,
    'remaining_original_modes':monitor['lane_section_hold']['junction_body_hold']['pending_original_modes'],
    'boundary': 'Native lane-change timestamps apply to the step after the preceding TraCI commands. The guard starts at the first post-step internal sample. First-entry-step changes remain possible.'})
original = ET.fromstring(read_trace_source(BASE/'native','collisions.xml')).findall('collision')
targets = {r.get(role):r.get('lane')[1:].rsplit('_',2)[0] for r in original for role in ('collider','victim')}
samples = defaultdict(list)
for _, element in ET.iterparse(NATIVE/'collision-fcd.xml'):
    if element.tag == 'timestep':
        for row in element:
            samples[row.get('id')].append({'time':float(element.get('time')),**row.attrib})
        element.clear()
network = ET.parse(NATIVE/'network.net.xml').getroot()
trips = {r.get('id'):r.attrib for r in ET.parse(NATIVE/'tripinfo.xml').getroot().findall('tripinfo')}
participants = {}
for identity, junction in targets.items():
    observed = samples[identity]
    inside = [r for r in observed if r['lane'].startswith(':'+junction+'_')]
    outlets = {r.get('to') for r in network.findall('connection') if r.get('via','').startswith(':'+junction+'_') and not r.get('from').startswith(':')}
    after = [r for r in observed if inside and r['time'] > inside[0]['time'] and any(r['lane'].startswith(edge+'_') for edge in outlets) and float(r['pos']) > 5]
    participants[identity] = {'junction':junction, 'entered_target_junction':bool(inside), 'rear_cleared_target':bool(after),
        'first_internal_sample':inside[0] if inside else None, 'first_rear_clear_sample':after[0] if after else None,
        'trip':trips.get(identity), 'claim_boundary':'Front/rear clearance of the target junction under this candidate, not the original simultaneous conflict or field behavior.'}
write_json_atomic(OUT/'original-participants.json', participants)
write_json_atomic(OUT/'independent-checks.json', {'all_fixed_traffic_inputs_unchanged':True, 'all_52_rows_retained':True,
    'version':'SUMO 1.27.1', 'no_changes_while_body_hold_active':not violations,
    'all_original_participants_rear_cleared_target':all(r['rear_cleared_target'] for r in participants.values()),
    'main_window_run':False, 'formal_result':False})
write_json_atomic(OUT/'comparison.json', {'baseline':parent,'candidate':report,
    'improved_bins':sum(r['absolute_error_change'] is not None and r['absolute_error_change']<0 for r in comparison),
    'worsened_bins':sum(r['absolute_error_change'] is not None and r['absolute_error_change']>0 for r in comparison),
    'unchanged_bins':sum(r['absolute_error_change']==0 for r in comparison), 'formal_result':False})
print(json.dumps({'candidate':report,'body_hold_violations':len(violations),'unheld_internal_changes':len(unheld_internal),
    'original_participants_rear_cleared':{k:v['rear_cleared_target'] for k,v in participants.items()}}))
