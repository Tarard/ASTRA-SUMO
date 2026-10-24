"""Retain all main-window rows while checking the localized driver prefix."""

import json
from pathlib import Path
import xml.etree.ElementTree as ET

from astra_sumo.core.artifact_io import write_json_atomic
from astra_sumo.core.detector_demand import audit_expected_to_e1_strict, read_csv_rows, write_csv
from astra_sumo.core.hamburg_readme_results import STATIONS

ROOT=Path(__file__).resolve().parents[1]
P=ROOT/'outputs/ring1-localized-junction-body-inputs-20261021-v1'
N=ROOT/'outputs/ring1-localized-junction-body-prefix-20261021-v1'
BASE=ROOT/'outputs/ring1-junction-body-full-native-20261021-v1'
OUT=ROOT/'outputs/ring1-localized-junction-body-analysis-20261021-v1'
OUT.mkdir(exist_ok=False)
run=json.loads((N/'run.json').read_bytes())
monitor=run['stall_monitor']
assert monitor['status']=='completed' and monitor['simulation_time_s']==6000
assert monitor['junction_body_hold_nodes']==['14398547','205069138']
assert run['completion']['collisions']==run['completion']['teleports']==0
source=read_csv_rows(P/'expected-counts.csv')
comp=audit_expected_to_e1_strict(source,N/'field-counts.xml')
rows=[{**a,**b} for a,b in zip(source,comp)]
assert len(rows)==130 and sum(r['measured_nVehContrib'] is not None for r in rows)==26
write_csv(OUT/'all-130-main-rows.csv',rows,list(rows[0]))
stations=[]
for station,(node,assets) in STATIONS.items():
    for begin in sorted({r['begin'] for r in rows},key=float):
        selected=[r for r in rows if r['node_id']==node and r['asset_id'] in assets and r['begin']==begin]
        assert len(selected)==len(assets)
        measured=sum(r['measured_nVehContrib'] for r in selected) if all(r['measured_nVehContrib'] is not None for r in selected) else None
        expected=sum(int(r['expected_total']) for r in selected)
        stations.append({'station_stream_id':station,'begin':begin,'end':selected[0]['end'],
            'expected_total':expected,'measured_nVehContrib':measured,'residual':None if measured is None else measured-expected,'independent_evidence':False})
assert len(stations)==40 and sum(r['measured_nVehContrib'] is not None for r in stations)==8
write_csv(OUT/'all-40-main-stations.csv',stations,list(stations[0]))
planned={v.get('id'):v for v in ET.parse(P/'demand.rou.xml').getroot().findall('vehicle')}
trip={v.get('id'):v.attrib for v in ET.parse(N/'tripinfo.xml').getroot().findall('tripinfo')}
baseline={v.get('id'):v.attrib for v in ET.parse(BASE/'tripinfo.xml').getroot().findall('tripinfo')}
due={v for v,r in planned.items() if float(r.get('depart'))<6000}
assert len(due-trip.keys())==run['completion']['waiting']
in_north=[r for v,r in trip.items() if planned[v].find('route').get('edges').split()[0]=='44532531']
old_north=[r for v,r in baseline.items() if planned[v].find('route').get('edges').split()[0]=='44532531' and float(r['depart'])<6000]
after=sum(float(r['depart'])>5299.7 for r in in_north)
assert after>0 and float(trip['readme_f0_b0_p169_v6']['arrival'])>=0
original=['initialization_hh_0119_Z_13_34018_b1_p175_v113','initialization_hh_0119_Z_13_34018_b1_p175_v118','initialization_hh_0119_Z_12_31546_b1_p98_v16','initialization_hh_0535_Z_4_33055_b2_p281_v28']
participants={v:trip.get(v) for v in original}
assert all(r is not None and float(r['arrival'])>=0 for r in participants.values())
report={'formal_result':False,'main_window_completed':False,'native_end_s':6000,'requests_due':len(due),
    'future_requests_retained':len(planned)-len(due),'completion':run['completion'],
    'driver_nodes':monitor['junction_body_hold_nodes'],'known_trapped_vehicle':trip['readme_f0_b0_p169_v6'],
    'original_collision_participants':participants,
    'north_entries_through_6000':len(in_north),'baseline_north_entries_through_6000':len(old_north),
    'north_entries_after_original_stoppage':after,'last_north_actual_depart_s':max(float(r['depart']) for r in in_north),
    'main_field_rows_retained':130,'main_field_rows_measured':26,'main_field_rows_unknown':104,
    'station_rows_retained':40,'station_rows_measured':8,'station_rows_unknown':32,
    'claim_boundary':'Targeted 6000 s diagnostic, not full-window calibration. All later rows remain unknown. Releasing the witnessed trap and retaining zero collisions in this prefix does not establish full-demand safety or zero residuals.'}
write_json_atomic(OUT/'prefix-check.json',report)
print(json.dumps({k:report[k] for k in ('requests_due','future_requests_retained','completion','north_entries_through_6000','baseline_north_entries_through_6000','north_entries_after_original_stoppage','last_north_actual_depart_s')}))
