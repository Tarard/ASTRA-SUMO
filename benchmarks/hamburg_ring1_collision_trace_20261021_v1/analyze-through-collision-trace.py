"""Inspect native trajectories without inferring maneuvers from collision types."""

from collections import defaultdict
import json
from pathlib import Path
import xml.etree.ElementTree as ET

from astra_sumo.core.artifact_io import write_json_atomic
from astra_sumo.core.detector_demand import write_csv

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'outputs/ring1-through-collision-trace-20261021-v1'
ANALYSIS = ROOT / 'outputs/ring1-through-collision-analysis-20261021-v1'
ANALYSIS.mkdir(exist_ok=False)
checked = json.loads((OUT / 'trace-check.json').read_bytes())
assert checked['reproduction_matched']
events = ET.parse(OUT / 'collisions.xml').getroot().findall('collision')
samples = defaultdict(list)
for _, element in ET.iterparse(OUT / 'collision-fcd.xml'):
    if element.tag == 'timestep':
        for row in element:
            samples[row.get('id')].append({'time': float(element.get('time')), **row.attrib})
        element.clear()
changes = ET.parse(OUT / 'lanechanges.xml').getroot().findall('change')
report = []
for index, event in enumerate(events):
    time = float(event.get('time'))
    vehicles = {}
    window = []
    for role in ('collider', 'victim'):
        identity = event.get(role)
        observed = [r for r in samples[identity] if time - 10 <= r['time'] <= time + 1]
        transitions = [r for i, r in enumerate(observed) if i == 0 or r['lane'] != observed[i-1]['lane']]
        lane_changes = [r.attrib for r in changes if r.get('id') == identity and time-10 <= float(r.get('time')) <= time+1]
        vehicles[role] = {'id': identity, 'last_samples_through_event': [r for r in observed if r['time'] <= time][-3:],
                          'lane_transitions': transitions, 'native_lane_changes': lane_changes}
        window.extend({'role': role, **r} for r in observed)
    write_csv(ANALYSIS / f'event-{index}-samples.csv', window, list(window[0]))
    report.append({'event': event.attrib, 'vehicles': vehicles})
write_json_atomic(ANALYSIS / 'trajectory-context.json', {'events': report, 'reproduction_matched': True,
    'claim_boundary': 'Native FCD samples and lane-change events. Collision checks and FCD output occur at different stages, so equal time labels do not establish an identical vehicle state.'})
print(json.dumps(report, indent=2))
