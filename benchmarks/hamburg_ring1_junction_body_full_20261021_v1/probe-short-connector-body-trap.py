"""Artificial suffix probe of a witnessed lane choice, not a count fit."""

from copy import deepcopy
import json
from pathlib import Path
import xml.etree.ElementTree as ET

import sumo

from astra_sumo.core.artifact_io import write_json_atomic
from astra_sumo.core.detector_demand import write_csv
from astra_sumo.core.detector_demand_replay import run_point_detector_demand

ROOT=Path(__file__).resolve().parents[1]
SOURCE=ROOT/'outputs/ring1-junction-body-full-inputs-20261021-v1'
OUT=ROOT/'outputs/ring1-short-connector-body-trap-20261021-v1'
OUT.mkdir(exist_ok=False)
routes=ET.Element('routes')
routes.extend(deepcopy(ET.parse(SOURCE/'demand.rou.xml').getroot().findall('vType')))
vehicle=ET.SubElement(routes,'vehicle',id='artificial_post_field_suffix',type='readme_lane_intent',depart='100',departLane='0',departPos='5.1',departSpeed='max')
ET.SubElement(vehicle,'route',edges='199230912#0 199230912#1 1257937836#0')
ET.SubElement(vehicle,'stop',lane='199230912#1_0',endPos='0.1',speed='5.555556')
ET.ElementTree(routes).write(OUT/'suffix.rou.xml',encoding='utf-8',xml_declaration=True)
write_json_atomic(OUT/'intentions.json',{'vehicles':[{'vehicle_id':'artificial_post_field_suffix','field_id':'hh_0119_Z_11_28853'}]})
loops=ET.parse(SOURCE/'field-loops.add.xml').getroot()
targets=[{'detector_id':r.get('id'),'begin':0,'end':900,'expected_total':0} for r in loops]
write_csv(OUT/'artificial-targets.csv',targets,list(targets[0]))
write_json_atomic(OUT/'proposal.json',{'formal_result':False,
    'purpose':'Reproduce the route-suffix lane choice observed for readme_f0_b0_p169_v6. A positive-speed waypoint at 0.1 m on the short road selects lane 0. It is an artificial diagnostic, not an unchanged full-vehicle reproduction.',
    'same_between_cases':'Network, suffix route, explicit lane-0 choice, vehicle, departure, signals and standstill detector.',
    'difference':'Global junction-body hold enabled versus disabled. Observation-lane assignment lies upstream of this suffix, so it sends no local hold command here.',
    'measurement_boundary':'No real field lies on the suffix. Artificial zeros do not replace any of the 130 source targets. Early termination leaves unmatched 900 s detector rows missing.'})
results={}
for label,enabled in [('global_hold',True),('detector_only',False)]:
    run=run_point_detector_demand(net_file=SOURCE/'network.net.xml',route_file=OUT/'suffix.rou.xml',
        e1_additional_file=SOURCE/'field-loops.add.xml',expected_counts_csv=OUT/'artificial-targets.csv',
        output_dir=OUT/label,end_s=900,sumo_binary=str(Path(sumo.__file__).parent/'bin/sumo.exe'),timeout_seconds=180,
        signal_program_file=SOURCE/'control.add.xml',lane_intentions_file=OUT/'intentions.json',junction_body_hold=enabled)
    monitor=run['stall_monitor']
    results[label]={'native_version':monitor['native_version'],'monitor_status':monitor['status'],
        'native_end_s':monitor['simulation_time_s'],'completion':run.get('completion'),'healthy':run.get('healthy'),
        'observer_complete':run.get('observer_complete'),'stop':monitor.get('stop')}
assert results['global_hold']['monitor_status']=='all_stopped'
assert results['detector_only']['healthy']
write_json_atomic(OUT/'probe-check.json',{'formal_result':False,'results':results,
    'claim_boundary':'Artificial one-vehicle mechanism probe. It tests the short-road release conflict, not the full traffic pattern, a revised driver scope, or real-count calibration.'})
print(json.dumps(results))
