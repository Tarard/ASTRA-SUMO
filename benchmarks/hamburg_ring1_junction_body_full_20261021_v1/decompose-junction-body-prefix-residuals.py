"""Separate time-bin transfers from unentered and unfinished requested vehicles."""

from collections import defaultdict
import json
from pathlib import Path
import xml.etree.ElementTree as ET

from astra_sumo.core.artifact_io import write_json_atomic
from astra_sumo.core.detector_demand import read_csv_rows, write_csv
from astra_sumo.core.hamburg_collision_trace import read_trace_source

ROOT=Path(__file__).resolve().parents[1]
CASE=ROOT/'benchmarks/hamburg_ring1_junction_body_hold_20261021_v1'
OUT=ROOT/'outputs/ring1-data-led-prefix-diagnosis-20261021-v1'
OUT.mkdir(exist_ok=False)
intent={r['vehicle_id']:r for r in json.loads(read_trace_source(CASE/'native','lane-intentions.json'))['vehicles']}
trips={r.get('id'):r for r in ET.fromstring(read_trace_source(CASE/'native','tripinfo.xml')).findall('tripinfo')}
passage=json.loads(read_trace_source(CASE/'native','passage-reconciliation.json'))
assert passage['reconciled']
actual={r['vehicle_id']:r for r in passage['passages']}
assert len(actual)==len(passage['passages']) and all(r['detector_id']==intent[v]['field_id'] for v,r in actual.items())
planned, measured=defaultdict(set),defaultdict(set)
for v,r in intent.items():
    planned[r['field_id'],float(r['target_begin_s']),float(r['target_end_s'])].add(v)
for v,r in actual.items():
    measured[r['detector_id'],float(r['interval_begin_s']),float(r['interval_end_s'])].add(v)
rows=[]
for row in read_csv_rows(CASE/'native/field-comparison.csv'):
    key=(row['detector_id'],float(row['begin']),float(row['end']))
    wanted,seen=planned[key],measured[key]
    assert len(wanted)==int(row['expected_total']) and len(seen)==int(row['measured_nVehContrib'])
    unentered=wanted-trips.keys()
    unfinished=(wanted&trips.keys())-actual.keys()
    out=(wanted&actual.keys())-seen
    inward=seen-wanted
    residual=len(inward)-len(out)-len(unentered)-len(unfinished)
    assert residual==int(row['diff_nVehContrib_minus_expected'])
    rows.append({**row,'ontime_planned':len(wanted&seen),'in_from_other_target_bins':len(inward),
        'out_to_other_measured_bins':len(out),'not_entered_by_end':len(unentered),'entered_without_complete_passage_by_end':len(unfinished),
        'residual_identity_verified':True})
write_csv(OUT/'row-decomposition.csv',rows,list(rows[0]))
fields={}
for field in sorted({r['detector_id'] for r in rows}):
    selected=[r for r in rows if r['detector_id']==field]
    fields[field]={key:sum(int(r[key]) for r in selected) for key in (
        'expected_total','measured_nVehContrib','in_from_other_target_bins','out_to_other_measured_bins',
        'not_entered_by_end','entered_without_complete_passage_by_end')}
summary={'fields':fields,'totals':{key:sum(int(r[key]) for r in rows) for key in (
    'in_from_other_target_bins','out_to_other_measured_bins','not_entered_by_end','entered_without_complete_passage_by_end')},
    'all_52_residual_identities_verified':True,
    'claim_boundary':'Exact accounting of this one completed native initialization replay. It separates bin transfers from unentered and not-yet-passed requests. It does not identify a unique physical cause, predict changed-demand effects, or require the initialization hour to be an independently accepted calibration.'}
write_json_atomic(OUT/'decomposition.json',summary)
print(json.dumps(summary))
