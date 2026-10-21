"""Keep full-window accounting and the independently reproduced short-road trap."""

from collections import Counter
import json
from pathlib import Path
import xml.etree.ElementTree as ET

import numpy as np

from astra_sumo.core.artifact_io import write_json_atomic
from astra_sumo.core.candidate_contracts import file_sha256
from astra_sumo.core.detector_demand import write_csv

ROOT=Path(__file__).resolve().parents[1]
CASE=ROOT/'benchmarks/hamburg_ring1_junction_body_full_20261021_v1'
P=ROOT/'outputs/ring1-junction-body-full-inputs-20261021-v1'
N=ROOT/'outputs/ring1-junction-body-full-native-20261021-v1'
A=ROOT/'outputs/ring1-junction-body-full-data-analysis-20261021-v1'
net=ET.parse(N/'network.net.xml').getroot()
routes={v.get('id'):v for v in ET.parse(N/'demand.rou.xml').getroot().findall('vehicle')}
trips={t.get('id'):t for t in ET.parse(N/'tripinfo.xml').getroot().findall('tripinfo')}
monitor=json.loads((N/'stall-monitor.json').read_bytes())
run=json.loads((N/'run.json').read_bytes())
assert monitor['status']=='completed' and monitor['simulation_time_s']==14400 and not run['healthy']
assert run['completion']['waiting']==2771 and run['completion']['running']==329
assert run['completion']['collisions']==run['completion']['teleports']==0
def entry(v):
    return routes[v].find('route').get('edges').split()[0]
north=[t for v,t in trips.items() if entry(v)=='44532531']
last_north=max(float(t.get('depart')) for t in north)
assert last_north==5299.7
hist=Counter(int(float(t.get('depart'))//900) for t in north)
write_csv(A/'north-insertions-by-time.csv',[{'begin_s':900*i,'end_s':900*(i+1),'native_insertions':hist[i]} for i in range(16)],['begin_s','end_s','native_insertions'])
identity='readme_f0_b0_p169_v6'
body=monitor['lane_section_hold']['junction_body_hold']
event=[e for e in body['events'] if e['vehicle_id']==identity and e['event']=='held'][-1]
assert identity in body['still_held'] and float(trips[identity].get('arrival'))<0
via=event['lane']
link=next(c for c in net.findall('connection') if c.get('via')==via and not c.get('from').startswith(':'))
road=link.get('to')
lane=net.find(f"edge[@id='{road}']/lane[@index='{link.get('toLane')}']")
sequence=routes[identity].find('route').get('edges').split()
next_road=sequence[sequence.index(road)+1]
exit_lanes={c.get('fromLane') for c in net.findall('connection') if c.get('from')==road and c.get('to')==next_road}
assert road=='199230912#1' and link.get('toLane')=='0' and exit_lanes=={'1'} and float(lane.get('length'))==2.099729
probe=json.loads((ROOT/'outputs/ring1-short-connector-body-trap-20261021-v1/probe-check.json').read_bytes())['results']
stopped=probe['global_hold']['stop']['vehicles']['artificial_post_field_suffix']
assert stopped['lane']=='199230912#1_0' and stopped['speed_m_s']==0 and not stopped['leader'] and not stopped['junction_foes'] and not stopped['next_signals']
assert probe['detector_only']['completion']['arrived']==1 and probe['detector_only']['healthy']
write_json_atomic(A/'short-connector-witness.json',{'vehicle':identity,'native_hold_entry':event,
    'outgoing_road':road,'selected_lane':'0','road_length_m':float(lane.get('length')),'vehicle_length_m':5,
    'next_route_road':next_road,'route_compatible_exit_lanes':sorted(exit_lanes),
    'held_vehicle_has_no_arrival_in_full_run':True,'native_trip_waiting_s':float(trips[identity].get('waitingTime')),
    'suffix_probe':probe,'global_driver_candidate_rejected':True,
    'claim_boundary':'The hold blocks a required lane change on a road shorter than rear clearance. The isolated suffix reproduces this mechanism, with an explicit lane-0 waypoint. This does not attribute every full-run residual to one vehicle or replay the original vehicle unchanged.'})
response=np.load(ROOT/'outputs/ring1-junction-body-full-feedback-20261021-v1/response.npz')
rows=json.loads((ROOT/'outputs/ring1-junction-body-full-feedback-20261021-v1/rows.json').read_bytes())['rows']
zero=[r for i,r in enumerate(rows) if int(r['expected_total'])>0 and not np.any(response['numerators'][i])]
assert len(zero)==17
write_json_atomic(A/'zero-observed-response-rows.json',{'rows':zero,'count':len(zero),
    'claim_boundary':'No sampled request cell contributed to these positive-target rows in this blocked run. Rescaling this same fixed response matrix cannot fit them. This is not a physical infeasibility proof or a prediction under changed behavior or demand.'})
figure_source=ROOT/'outputs/ring1-readme-end-to-end-figures-20261021-v1'
review=json.loads((figure_source/'junctions-final/report.json').read_bytes())
netedit=json.loads((figure_source/'netedit-after/review-8adb1de57e.json').read_bytes())
assert review['inputs']['candidate_net']['sha256']==file_sha256(P/'network.net.xml')==netedit['candidate_sha256_after']
assert file_sha256(figure_source/'aerial-2026.png')==review['inputs']['aerial']['sha256']
write_json_atomic(CASE/'figure-provenance.json',{'review_inputs':review['inputs'],'aerial_bbox':review['aerial'],
    'current_network_sha256':file_sha256(P/'network.net.xml'),'netedit_after_sha256':netedit['candidate_sha256_after'],
    'network_unchanged_from_rendered_candidate':True,'claim_boundary':'Existing renderer and actual NetEdit figures are hash-bound to this same network. Images do not validate lane mapping or field behavior.'})
write_json_atomic(CASE/'execution-outcome.json',{'native_reached_planned_end':True,'native_end_s':14400,
    'all_vehicle_requests_completed':False,'native_command_status':'pass','timeout':False,
    'completion':run['completion'],'formal_result':False,'global_driver_candidate_rejected':True})
print(json.dumps({'north_last_insertion_s':last_north,'short_connector_trap_reproduced':True,'zero_observed_response_rows':len(zero),'figures_match_current_network':True}))
