"""Read fixed-program opportunities; do not infer capacity from green alone."""

from collections import defaultdict
import json
from pathlib import Path
import xml.etree.ElementTree as ET

from astra_sumo.core.artifact_io import write_json_atomic

ROOT=Path(__file__).resolve().parents[1]
P=ROOT/'outputs/ring1-junction-body-full-inputs-20261021-v1'
OUT=ROOT/'outputs/ring1-data-led-green-inspection-20261021-v1'
OUT.mkdir(exist_ok=False)
net=ET.parse(P/'network.net.xml').getroot()
programs={r.get('id'):r for r in ET.parse(P/'control.add.xml').getroot().findall('tlLogic')}
loops={r.get('id'):r for r in ET.parse(P/'field-loops.add.xml').getroot()}
intent={r['vehicle_id']:r for r in json.loads((P/'lane-intentions.json').read_bytes())['vehicles']}
planned=defaultdict(set)
for v in ET.parse(P/'demand.rou.xml').getroot().findall('vehicle'):
    field=intent[v.get('id')]['field_id']
    road=loops[field].get('lane').rsplit('_',1)[0]
    edges=v.find('route').get('edges').split()
    if road in edges and edges.index(road)+1<len(edges):
        planned[field].add(edges[edges.index(road)+1])
rows=[]
for field,loop in loops.items():
    road,index=loop.get('lane').rsplit('_',1)
    for c in net.findall('connection'):
        if c.get('from')!=road or c.get('fromLane')!=index or c.get('to') not in planned[field]:
            continue
        program=programs.get(c.get('tl'))
        if program is None or program.get('type')!='static':
            rows.append({'field':field,'connection':c.attrib,'fixed_green_per_cycle_s':None,'reason':'No installed static program on this immediate exit.'})
            continue
        link=int(c.get('linkIndex'))
        cycle=sum(float(p.get('duration')) for p in program.findall('phase'))
        green=sum(float(p.get('duration')) for p in program.findall('phase') if p.get('state')[link] in 'Gg')
        yellow=sum(float(p.get('duration')) for p in program.findall('phase') if p.get('state')[link] in 'yY')
        rows.append({'field':field,'connection':c.attrib,'cycle_s':cycle,'fixed_green_per_cycle_s':green,
            'yellow_per_cycle_s':yellow,'green_per_900s_if_cycle_divides_bin':900/cycle*green if 900%cycle==0 else None})
write_json_atomic(OUT/'installed-green-opportunities.json',{'rows':rows,'claim_boundary':'Fixed immediate-exit signal opportunities only. This is not a discharge-capacity bound, field timing validation or a causal residual explanation.'})
print(json.dumps(rows,indent=2))
