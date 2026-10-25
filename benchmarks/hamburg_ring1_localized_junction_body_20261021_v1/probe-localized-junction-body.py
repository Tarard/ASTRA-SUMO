"""Test the two recorded collision sites without the global short-road lock."""

import json
import shutil
from pathlib import Path

import sumo

from astra_sumo.core.artifact_io import write_json_atomic
from astra_sumo.core.candidate_contracts import file_sha256
from astra_sumo.core.detector_demand_replay import run_point_detector_demand

ROOT=Path(__file__).resolve().parents[1]
SOURCE=ROOT/'outputs/ring1-junction-body-full-inputs-20261021-v1'
P=ROOT/'outputs/ring1-localized-junction-body-inputs-20261021-v1'
OUT=ROOT/'outputs/ring1-localized-junction-body-prefix-20261021-v1'
P.mkdir(exist_ok=False)
for name in ('network.net.xml','demand.rou.xml','field-loops.add.xml','expected-counts.csv','control.add.xml','lane-intentions.json','initialization-counts.csv','scenario.json'):
    shutil.copyfile(SOURCE/name,P/name)
nodes=('14398547','205069138')
scenario=json.loads((P/'scenario.json').read_bytes())
scenario.update(junction_body_hold_nodes=list(nodes),lane_intention_assumptions='Retain observation-lane rear-clear holds. Apply the junction-body hold only at 14398547 and 205069138, the two previously recorded collision sites. Other junctions retain native lane choice. No demand or count feedback. This is a new local driver candidate, not field-verified behavior.')
write_json_atomic(P/'scenario.json',scenario)
write_json_atomic(P/'prefix-proposal.json',{'formal_result':False,'end_s':6000,'junction_body_hold_nodes':nodes,
    'change':'Localize the existing driver guard to the two recorded collision sites; restore native behavior on other junctions, including the witnessed short connector.',
    'fixed':'Every vehicle, route, request time, target field, network, lane permission and signal input retained.',
    'source_sha256':{n:file_sha256(P/n) for n in ('network.net.xml','demand.rou.xml','field-loops.add.xml','expected-counts.csv','control.add.xml','lane-intentions.json')},
    'evaluation':'Check no collision/teleport in this prefix, continued North entry after 5299.7, completion of readme_f0_b0_p169_v6, and all original collision participants reaching their target junctions. Keep all original 130/40 targets, with unmeasured later rows unknown. This is not a main-window calibration result.'})
run=run_point_detector_demand(net_file=P/'network.net.xml',route_file=P/'demand.rou.xml',e1_additional_file=P/'field-loops.add.xml',
    expected_counts_csv=P/'initialization-counts.csv',output_dir=OUT,end_s=6000,sumo_binary=str(Path(sumo.__file__).parent/'bin/sumo.exe'),
    timeout_seconds=1200,state_times_s=(5999.9,),signal_program_file=P/'control.add.xml',lane_intentions_file=P/'lane-intentions.json',
    junction_body_hold=True,junction_body_hold_nodes=nodes)
print(json.dumps({k:run.get(k) for k in ('status','native_status','completion','observer_complete','missing_field_bins','exact_field_bins')}))
