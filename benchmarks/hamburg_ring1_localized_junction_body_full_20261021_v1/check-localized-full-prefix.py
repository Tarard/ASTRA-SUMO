"""Check only sealed intervals of the still-running localized full replay."""

import json
from pathlib import Path
import xml.etree.ElementTree as ET

from astra_sumo.core.artifact_io import write_json_atomic
from astra_sumo.core.hamburg_collision_trace import read_trace_source

ROOT=Path(__file__).resolve().parents[1]
PREFIX=ROOT/'benchmarks/hamburg_ring1_localized_junction_body_20261021_v1/native'
NATIVE=ROOT/'outputs/ring1-localized-junction-body-full-native-20261021-v1'
expected={(r.get('id'),float(r.get('begin')),float(r.get('end'))):int(r.get('nVehContrib')) for r in ET.fromstring(read_trace_source(PREFIX,'field-counts.xml')).findall('interval') if float(r.get('end'))<=5400}
actual={}
try:
    for _,r in ET.iterparse(NATIVE/'field-counts.xml'):
        if r.tag=='interval' and float(r.get('end'))<=5400:
            actual[r.get('id'),float(r.get('begin')),float(r.get('end'))]=int(r.get('nVehContrib'))
        r.clear()
except ET.ParseError:
    pass
different=[{'key':k,'expected':expected.get(k),'actual':v} for k,v in actual.items() if expected.get(k)!=v]
assert len(expected)==78
result={'available_closed_intervals':len(actual),'expected_closed_intervals':78,
    'all_prefix_intervals_match':len(actual)==78 and not different,'different_intervals':different,
    'full_native_completion_established':False,'claim_boundary':'First 5400 s only. Open-file parsing and prefix agreement do not establish full-window completion, safety or count fit.'}
write_json_atomic(NATIVE.parent/'ring1-localized-full-prefix-check-20261021-v1.json',result)
print(json.dumps(result))
