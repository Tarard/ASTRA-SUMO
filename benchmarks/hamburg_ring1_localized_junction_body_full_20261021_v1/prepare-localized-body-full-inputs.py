import json
from pathlib import Path

from astra_sumo.core.artifact_io import write_json_atomic
from astra_sumo.core.candidate_contracts import file_sha256
from astra_sumo.core.hamburg_collision_trace import read_trace_source

ROOT=Path(__file__).resolve().parents[1]
SOURCE=ROOT/'benchmarks/hamburg_ring1_localized_junction_body_20261021_v1'
OUT=ROOT/'outputs/ring1-localized-junction-body-full-inputs-20261021-v1'
OUT.mkdir(exist_ok=False)
names=('network.net.xml','demand.rou.xml','field-loops.add.xml','expected-counts.csv','control.add.xml','lane-intentions.json','initialization-counts.csv','scenario.json')
for name in names:
    (OUT/name).write_bytes(read_trace_source(SOURCE/'inputs',name))
scenario=json.loads((OUT/'scenario.json').read_bytes())
assert scenario['junction_body_hold_nodes']==['14398547','205069138']
scenario['monitor_source_sha256']=file_sha256(ROOT/'plugins/astra-sumo/src/astra_sumo/core/simulation_stall.py')
write_json_atomic(OUT/'scenario.json',scenario)
write_json_atomic(OUT/'full-window-proposal.json',{'formal_result':False,'source_prefix_case':str(SOURCE),
    'model_change':'None after the localized 6000 s prefix. Extend the same two-node driver candidate to 14400 s from empty initialization.',
    'fixed':'All vehicles, routes, request times, fields, real targets, network, lane permissions and signal inputs retained.',
    'end_s':14400,'main_window_s':[3600,12600],'timeout_seconds':2400,'diagnostic_state_times_s':[14399.9],
    'restart_validated':False,'driver_nodes':scenario['junction_body_hold_nodes'],
    'input_sha256':{n:file_sha256(OUT/n) for n in names},
    'claim_boundary':'A new complete native measurement is required. The prefix does not establish later safety, complete insertion or zero counts. Do not change demand or controller settings during this run.'})
print(OUT)
