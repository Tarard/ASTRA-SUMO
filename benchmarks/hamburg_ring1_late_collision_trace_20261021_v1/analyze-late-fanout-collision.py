"""Reconstruct native vehicle placement without inferring field geometry."""

import json
from pathlib import Path
import xml.etree.ElementTree as ET

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from shapely.geometry import LineString, Point

from astra_sumo.core.artifact_io import write_json_atomic
from astra_sumo.core.detector_demand import write_csv

ROOT = Path(__file__).resolve().parents[1]
TRACE = ROOT / 'outputs/ring1-late-fanout-collision-trace-20261021-v1'
OUT = ROOT / 'outputs/ring1-late-fanout-collision-analysis-20261021-v1'
OUT.mkdir(exist_ok=False)
check = json.loads((TRACE / 'trace-check.json').read_bytes())
assert check['reproduction_matched'] and not check['run_valid']
event = ET.parse(TRACE / 'collisions.xml').getroot().find('collision')
time = float(event.get('time'))
ids = {event.get('collider'), event.get('victim')}
samples = [dict(v.attrib, time_s=float(step.get('time')))
           for step in ET.parse(TRACE / 'collision-fcd.xml').getroot() for v in step
           if v.get('id') in ids and time - 25 <= float(step.get('time')) <= time + 15]
changes = [v.attrib for v in ET.parse(TRACE / 'lanechanges.xml').getroot() if v.get('id') in ids]
assert len(changes) == 1
change = changes[0]
assert change['id'] == event.get('victim') and float(change['time']) == time
assert change['from'] == '139083723#0_3' and change['to'] == '139083723#0_2'
net = ET.parse(TRACE / 'network.net.xml').getroot()
lanes = {v.get('id'): v for e in net.findall('edge') for v in e.findall('lane')}


def shape(lane):
    return LineString([tuple(map(float, p.split(','))) for p in lanes[lane].get('shape').split()])


def point(lane, position):
    return shape(lane).interpolate(position / float(lanes[lane].get('length')), normalized=True)


victim_samples = [s for s in samples if s['id'] == event.get('victim')]
last_internal = [s for s in victim_samples if s['lane'].startswith(':') and s['time_s'] < time][-1]['lane']
types = {v.get('id'): v for v in ET.parse(TRACE / 'demand.rou.xml').getroot().findall('vType')}
length = float(types[victim_samples[-1]['type']].get('length'))
front_position = float(change['pos'])
rear_position = float(lanes[last_internal].get('length')) + front_position - length
assert 0 < rear_position < float(lanes[last_internal].get('length')) and front_position < length
predicted = point(last_internal, rear_position)
actual_rear = Point(tuple(map(float, event.get('victimBack').split(','))))
actual_front = Point(tuple(map(float, event.get('victimFront').split(','))))
assert predicted.distance(actual_rear) < 1e-6
assert point(change['to'], front_position).distance(actual_front) < 1e-6
front_back_distance = actual_front.distance(actual_rear)
routes = {v.get('id'): v.find('route').get('edges').split() for v in ET.parse(TRACE / 'demand.rou.xml').getroot().findall('vehicle') if v.get('id') in ids}
road = '139083723#0'
following = {identity: route[route.index(road) + 1] for identity, route in routes.items()}
audit = {identity: {'next_road': following[identity],
                  'outlet_lane_length_m': float(lanes[road + '_3'].get('length')),
                  'route_compatible_exit_lanes': sorted({c.get('fromLane') for c in net.findall('connection') if c.get('from') == road and c.get('to') == following[identity]})}
         for identity in ids}
report = {'formal_result': False, 'reproduction_matched': True, 'native_event': event.attrib,
          'observed_lane_changes': changes, 'victim_last_internal_lane': last_internal,
          'victim_front_position_m': front_position, 'vehicle_length_m': length,
          'victim_rear_still_internal': True, 'reconstructed_rear_xy': list(predicted.coords)[0],
          'native_rear_xy': list(actual_rear.coords)[0], 'rear_position_error_m': predicted.distance(actual_rear),
          'front_back_straight_distance_m': front_back_distance, 'outlet_route_audit': audit,
          'claim_boundary': 'Exact trace correspondence and actual exit lane change with the rear still internal are verified. The collider label does not identify which car changed lane. No unique SUMO source defect, field geometry, or repaired full-traffic safety is established.'}
write_json_atomic(OUT / 'geometry-check.json', report)
write_csv(OUT / 'event-samples.csv', samples, list(samples[0]))
fig, ax = plt.subplots(figsize=(8, 7))
for lane in lanes:
    if lane.startswith(':18653534_') or lane.startswith('139083723#0_'):
        xs, ys = shape(lane).xy
        ax.plot(xs, ys, color='#aaaaaa', linewidth=.8)
for lane, color in [(last_internal, '#dc8b13'), (change['from'], '#dc8b13'), (change['to'], '#b33b3b'), (':18653534_21_1', '#275f9b')]:
    xs, ys = shape(lane).xy
    ax.plot(xs, ys, color=color, linewidth=2, label=lane)
for role, color in [('collider', '#275f9b'), ('victim', '#b33b3b')]:
    front = tuple(map(float, event.get(role + 'Front').split(',')))
    back = tuple(map(float, event.get(role + 'Back').split(',')))
    ax.plot([back[0], front[0]], [back[1], front[1]], 'o-', color=color, linewidth=3, label=role + ' native endpoints')
ax.set(xlim=(274, 302), ylim=(605, 635), xlabel='Native network x (m)', ylabel='Native network y (m)',
       title='Invalid run: 9402.6 s, exit lane change before rear clearance')
ax.set_aspect('equal')
ax.legend(fontsize=7)
fig.tight_layout()
fig.savefig(OUT / 'native-collision-positions.png', dpi=180)
plt.close(fig)
print(json.dumps({k: v for k, v in report.items() if k not in {'native_event', 'claim_boundary'}}, indent=2))
