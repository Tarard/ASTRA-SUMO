from collections import Counter
import json
from pathlib import Path
import xml.etree.ElementTree as ET

from astra_sumo.core.artifact_io import write_json_atomic
from astra_sumo.core.detector_demand import read_csv_rows

ROOT = Path(__file__).resolve().parents[1]
NATIVE = ROOT / 'outputs/ring1-junction-body-hold-native-20261021-v1'
OUT = ROOT / 'outputs/ring1-junction-body-hold-analysis-20261021-v1'
routes = ET.parse(NATIVE/'demand.rou.xml').getroot().findall('vehicle')
requested = {r.get('id') for r in routes if float(r.get('depart'))<3600}
trips = ET.parse(NATIVE/'tripinfo.xml').getroot().findall('tripinfo')
entered = {r.get('id') for r in trips}
arrived = {r.get('id') for r in trips if float(r.get('arrival'))>=0}
passages = json.loads((NATIVE/'passage-reconciliation.json').read_bytes())['passages']
passed = {r['vehicle_id'] for r in passages}
assert len(routes)==8640 and len(requested)==1953 and len(entered)==1806 and len(arrived)==1661
assert arrived<=passed and len(passed)==len(passages)==1703
assert len(requested-entered)==147 and len(entered-passed)==103 and len(passed-arrived)==42
audit = json.loads((OUT/'hold-audit.json').read_bytes())
events = json.loads((NATIVE/'stall-monitor.json').read_bytes())['lane_section_hold']['junction_body_hold']['events']
starts = {}
for event in events:
    if event['event']=='held':
        starts.setdefault(event['vehicle_id'], []).append(event['time_s'])
boundary=[]
for change in audit['internal_changes_outside_body_hold']:
    time=float(change['time'])
    next_start=min((s for s in starts[change['id']] if s>time), default=None)
    boundary.append({'change':change, 'first_post_step_hold_s':next_start,
        'matches_one_step_observation_delay':next_start is not None and abs(next_start-time-.1)<1e-8})
assert len(boundary)==21 and all(r['matches_one_step_observation_delay'] for r in boundary)
rows = read_csv_rows(NATIVE/'field-comparison.csv')
signed=sum(int(r['diff_nVehContrib_minus_expected']) for r in rows)
positive=sum(max(0,int(r['diff_nVehContrib_minus_expected'])) for r in rows)
negative=sum(max(0,-int(r['diff_nVehContrib_minus_expected'])) for r in rows)
assert signed==-250 and positive==46 and negative==296 and positive+negative==342
write_json_atomic(OUT/'population-and-boundary-check.json', {'total_requests':8640,'initialization_requests':1953,
    'future_main_requests':6687,'entered':1806,'arrived':1661,'not_entered':147,
    'entered_without_complete_field_passage':103,'running_after_complete_field_passage':42,
    'all_arrivals_have_one_assigned_field_passage':True,'complete_field_passages':1703,
    'absolute_residual_sum':342,'positive_residual_sum':46,'negative_residual_magnitude':296,
    'unheld_internal_changes':boundary,'junction_hold_event_counts':dict(Counter(r['event'] for r in events)),
    'claim_boundary':'Initialization-hour accounting, not new field observations or a formal fit. The first-step limitation is measured and remains declared.'})
print(json.dumps({'population_reconciled':True,'all_21_internal_changes_match_entry_step_delay':True,'absolute_residual_sum':342}))
