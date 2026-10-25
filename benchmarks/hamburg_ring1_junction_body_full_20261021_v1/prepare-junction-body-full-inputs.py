"""Retain the frozen initialized demand and declare the already-tested driver."""

import json
from pathlib import Path
import xml.etree.ElementTree as ET

from astra_sumo.core.artifact_io import write_json_atomic
from astra_sumo.core.candidate_contracts import file_sha256
from astra_sumo.core.detector_demand import read_csv_rows
from astra_sumo.core.hamburg_collision_trace import read_trace_source

ROOT=Path(__file__).resolve().parents[1]
SOURCE=ROOT/'benchmarks/hamburg_ring1_through_route_coverage_20261021_v1'
PREFIX=ROOT/'benchmarks/hamburg_ring1_junction_body_hold_20261021_v1'
OUT=ROOT/'outputs/ring1-junction-body-full-inputs-20261021-v1'
OUT.mkdir(exist_ok=False)
names=('network.net.xml','demand.rou.xml','field-loops.add.xml','expected-counts.csv','control.add.xml','lane-intentions.json','initialization-counts.csv','scenario.json','through-prior.json')
for name in names:
    (OUT/name).write_bytes(read_trace_source(SOURCE/'inputs',name))
for name, parent_name in {'network.net.xml':'network.net.xml','demand.rou.xml':'demand.rou.xml','control.add.xml':'signal-programs.add.xml','lane-intentions.json':'lane-intentions.json'}.items():
    assert (OUT/name).read_bytes()==read_trace_source(PREFIX/'native',parent_name)
scenario=json.loads((OUT/'scenario.json').read_bytes())
scenario.update(junction_body_hold_enabled=True,driver_source_sha256=file_sha256(ROOT/'plugins/astra-sumo/src/astra_sumo/core/lane_section_hold.py'),
    lane_intention_assumptions='Keep localized moving waypoints and detector rear-clear holding. Also hold mode 512 from the first post-step internal-lane observation until rear clearance; restore only after both holds end. The first entry-step and existing 0.1 m arrival boundary remain limitations. No count feedback. Natural driving remains unverified.')
write_json_atomic(OUT/'scenario.json',scenario)
expected=read_csv_rows(OUT/'expected-counts.csv')
assert len(expected)==130 and sum(int(r['expected_total']) for r in expected)==6687
assert len(ET.parse(OUT/'demand.rou.xml').getroot().findall('vehicle'))==8640
write_json_atomic(OUT/'full-window-proposal.json',{'formal_result':False,'change':'Extend the already-declared initialized driver candidate to the full observation window. No demand, topology, control, permission or target change.',
    'prefix_case':str(PREFIX),'input_sha256':{name:file_sha256(OUT/name) for name in names},
    'end_s':14400,'formal_window_s':[3600,12600],'timeout_seconds':2400,
    'time_origin_utc':'2026-10-19T04:30:00Z','time_origin_local':'2026-10-19T06:30:00+02:00',
    'field_rows':130,'station_rows':40,'station_values_are_independent':False,
    'claim_boundary':'One full native measurement of the declared candidate, not an integer correction or accepted calibration. A timeout is an execution limit, not evidence of physical infeasibility. Preserve incomplete records and do not restart the same candidate only to alter already-sealed counts.'})
print(OUT)
