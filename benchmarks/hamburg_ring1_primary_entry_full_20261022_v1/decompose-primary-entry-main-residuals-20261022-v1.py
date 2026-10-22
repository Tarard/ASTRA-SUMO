"""Account for every main-window residual using fixed requests and native E1."""

from collections import defaultdict
import json
import math
from pathlib import Path
import xml.etree.ElementTree as ET

from astra_sumo.core.artifact_io import write_json_atomic
from astra_sumo.core.detector_demand import read_csv_rows, write_csv

ROOT=Path(__file__).resolve().parents[1]
PREP=ROOT/'outputs/ring1-primary-entry-prior-full-inputs-20261022-v1'
NATIVE=ROOT/'outputs/ring1-primary-entry-prior-full-native-20261022-v1'
REPORT=ROOT/'outputs/ring1-primary-entry-prior-full-report-20261022-v1'
OUT=ROOT/'outputs/ring1-primary-entry-prior-full-data-analysis-20261022-v1'
intent={r['vehicle_id']:r for r in json.loads((PREP/'lane-intentions.json').read_bytes())['vehicles']}
vehicles={r.get('id'):r for r in ET.parse(PREP/'demand.rou.xml').getroot().findall('vehicle')}
trips={r.get('id'):r for r in ET.parse(NATIVE/'tripinfo.xml').getroot().findall('tripinfo')}
reconciliation=json.loads((REPORT/'passage-reconciliation.json').read_bytes())
if not reconciliation.get('reconciled'):
    raise ValueError('Retain all native records. Incomplete identity reconciliation cannot support this decomposition.')
passages=reconciliation['passages']
actual={r['vehicle_id']:r for r in passages}
if len(actual)!=len(passages) or any(r['detector_id']!=intent[v]['field_id'] for v,r in actual.items()):
    raise ValueError('This one-field decomposition does not apply to additional or repeated field passages.')
planned,measured=defaultdict(set),defaultdict(set)
for v,r in intent.items():
    planned[r['field_id'],float(r['target_begin_s']),float(r['target_end_s'])].add(v)
for v,r in actual.items():
    measured[r['detector_id'],float(r['interval_begin_s']),float(r['interval_end_s'])].add(v)
rows=[]
for r in read_csv_rows(REPORT/'field-comparison.csv'):
    key=(r['detector_id'],float(r['begin']),float(r['end']))
    wanted,seen=planned[key],measured[key]
    if r['measured_nVehContrib'] in ('','None'):
        raise ValueError('Missing native count rows remain unknown; do not decompose them as zeros.')
    assert len(wanted)==int(r['expected_total']) and len(seen)==int(r['measured_nVehContrib'])
    incoming=seen-wanted
    outgoing=(wanted&actual.keys())-seen
    unentered=wanted-trips.keys()
    unpassed=(wanted&trips.keys())-actual.keys()
    assert len(incoming)-len(outgoing)-len(unentered)-len(unpassed)==int(r['diff_nVehContrib_minus_expected'])
    rows.append({**r,'ontime_planned':len(wanted&seen),
        'in_from_initialization':sum(intent[v]['population_role']=='initialization' for v in incoming),
        'in_from_other_main_bins':sum(intent[v]['population_role']=='main' for v in incoming),
        'out_before_main_window':sum(actual[v]['interval_begin_s']<3600 for v in outgoing),
        'out_after_main_window':sum(actual[v]['interval_begin_s']>=12600 for v in outgoing),
        'out_to_other_main_bins':sum(3600<=actual[v]['interval_begin_s']<12600 for v in outgoing),
        'not_entered_by_native_end':len(unentered),'entered_without_complete_field_passage_by_native_end':len(unpassed)})
assert len(rows)==130
OUT.mkdir(exist_ok=False)
write_csv(OUT/'row-decomposition.csv',rows,list(rows[0]))
end=float(ET.parse(NATIVE/'summary.xml').getroot().findall('step')[-1].get('time'))+.1
entries=defaultdict(list)
for identity,vehicle in vehicles.items():
    request=math.floor(float(vehicle.get('depart'))*1000+.5)/1000
    if request>end:
        continue
    entry=vehicle.find('route').get('edges').split()[0]
    entered=identity in trips
    delay=max(0,float(trips[identity].get('depart'))-request) if entered else None
    entries[entry].append({'vehicle_id':identity,'field':intent[identity]['field_id'],
        'population_role':intent[identity]['population_role'],'native_requested_depart_s':request,
        'inserted':entered,'insertion_delay_s':delay,
        'unentered_wait_lower_bound_s':max(0,end-request) if not entered else None,
        'assigned_complete_passage':identity in actual})
entry_summary=[]
for entry,members in sorted(entries.items()):
    waits=[r['unentered_wait_lower_bound_s'] for r in members if not r['inserted']]
    delays=[r['insertion_delay_s'] for r in members if r['inserted']]
    entry_summary.append({'entry':entry,'requests':len(members),'inserted':sum(r['inserted'] for r in members),
        'not_inserted':sum(not r['inserted'] for r in members),'complete_passages':sum(r['assigned_complete_passage'] for r in members),
        'max_observed_insertion_delay_s':max(delays,default=None),'max_censored_wait_lower_bound_s':max(waits,default=None)})
write_csv(OUT/'entry-pressure.csv',entry_summary,list(entry_summary[0]))
write_json_atomic(OUT/'entry-vehicles.json',dict(entries))
keys=('in_from_initialization','in_from_other_main_bins','out_before_main_window','out_after_main_window',
      'out_to_other_main_bins','not_entered_by_native_end','entered_without_complete_field_passage_by_native_end')
fields={}
for field in sorted({r['detector_id'] for r in rows}):
    selected=[r for r in rows if r['detector_id']==field]
    fields[field]={key:sum(int(r[key]) for r in selected) for key in ('expected_total','measured_nVehContrib',*keys)}
write_json_atomic(OUT/'decomposition.json',{'fields':fields,'totals':{key:sum(int(r[key]) for r in rows) for key in keys},
    'all_130_residual_identities_verified':True,'new_simulations':0,
    'arrivals_without_complete_assigned_passage':[v for v,t in trips.items() if float(t.get('arrival'))>=0 and v not in actual],
    'claim_boundary':'This replay is decomposed exactly. Bin transfers are separated from unentered and not-yet-passed requests. Censored waits remain lower bounds. These identities neither establish a unique cause nor predict changed demand.'})
print(json.dumps({'all_130_residual_identities_verified':True,'totals':{key:sum(int(r[key]) for r in rows) for key in keys}}))
