from collections import Counter
import json
from pathlib import Path
import xml.etree.ElementTree as ET

from astra_sumo.core.artifact_io import write_json_atomic

ROOT=Path(__file__).resolve().parents[1]
NATIVE=ROOT/'outputs/ring1-junction-body-hold-native-20261021-v1'
OUT=ROOT/'outputs/ring1-junction-body-hold-analysis-20261021-v1'
monitor=json.loads((NATIVE/'stall-monitor.json').read_bytes())
trips={r.get('id'):r.attrib for r in ET.parse(NATIVE/'tripinfo.xml').getroot()}
events=[r for r in monitor['lane_section_hold']['junction_body_hold']['events'] if r['event']=='left_before_release']
rows=[]
for event in events:
    trip=trips[event['vehicle_id']]
    assert float(trip['arrival'])>=0 and float(trip['arrivalPos'])==.1
    assert abs(event['time_s']-float(trip['arrival'])-.1)<1e-8
    rows.append({**event,'native_arrival_s':float(trip['arrival']),'arrival_lane':trip['arrivalLane'],'arrival_position_m':float(trip['arrivalPos'])})
assert len(rows)==514
write_json_atomic(OUT/'terminal-holds.json',{'arrivals_removed_before_an_explicit_guard_release':len(rows),
    'by_terminal_lane':dict(Counter(r['arrival_lane'] for r in rows)), 'events':rows,
    'claim_boundary':'All missing guard samples correspond to native arrivals at the existing 0.1 m route terminals. No collision or teleport is hidden. These boundary removals do not prove rear clearance or mode restoration; the fixed terminal model remains a declared limitation.'})
print(json.dumps({'terminal_hold_exits':len(rows),'by_lane':dict(Counter(r['arrival_lane'] for r in rows))}))
