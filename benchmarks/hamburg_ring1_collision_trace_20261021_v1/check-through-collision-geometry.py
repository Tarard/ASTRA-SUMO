"""Check positions against the native graph, not against field geometry."""

import json
import math
from pathlib import Path
import xml.etree.ElementTree as ET

from shapely.geometry import LineString, Point

from astra_sumo.core.artifact_io import write_json_atomic
from astra_sumo.core.hamburg_collision_trace import read_trace_source

ROOT = Path(__file__).resolve().parents[1]
CASE = ROOT / 'benchmarks/hamburg_ring1_through_route_coverage_20261021_v1'
TRACE = ROOT / 'outputs/ring1-through-collision-trace-20261021-v1'
OUT = ROOT / 'outputs/ring1-through-collision-analysis-20261021-v1'
network = ET.parse(TRACE / 'network.net.xml').getroot()
lanes = {r.get('id'): r for e in network.findall('edge') for r in e.findall('lane')}
context = json.loads((OUT / 'trajectory-context.json').read_bytes())['events']


def shape(identity):
    return LineString([tuple(map(float, p.split(','))) for p in lanes[identity].get('shape').split()])


def point(identity, position):
    return shape(identity).interpolate(position / float(lanes[identity].get('length')), normalized=True)


events = ET.parse(TRACE / 'collisions.xml').getroot().findall('collision')
first, second = events
victim = context[0]['vehicles']['victim']['last_samples_through_event'][-1]
collider_front = Point(tuple(map(float, first.get('colliderFront').split(','))))
first_expected = point(first.get('lane'), float(first.get('pos')))
assert collider_front.distance(first_expected) < 1e-6
assert point(victim['lane'], float(victim['pos'])).distance(Point(float(victim['x']), float(victim['y']))) < 1e-6
front_separation = collider_front.distance(Point(float(victim['x']), float(victim['y'])))
last = context[1]['vehicles']['victim']['last_samples_through_event'][-1]
vehicle_types = ET.fromstring(read_trace_source(CASE / 'native', 'demand.rou.xml')).findall('vType')
length = float(next(t.get('length') for t in vehicle_types if t.get('id') == last['type']))
rear_offset = float(lanes[':205069138_0_1'].get('length')) + float(last['pos']) - length
predicted_rear = point(':205069138_0_1', rear_offset)
native_rear = Point(tuple(map(float, second.get('victimBack').split(','))))
rear_error = predicted_rear.distance(native_rear)
assert rear_error < 1e-6
route_equal = [ET.tostring(v) for v in ET.parse(TRACE / 'vehicle-routes.xml').getroot().findall('vehicle')] == [
    ET.tostring(v) for v in ET.fromstring(read_trace_source(CASE / 'native', 'vehicle-routes.xml')).findall('vehicle')]
assert route_equal
report = {'same_all_vehicle_route_exit_times': route_equal,
          'event0': {'collider_internal_change': ':14398547_3_0 -> :14398547_3_1',
                     'victim_lane': victim['lane'], 'front_separation_m': front_separation,
                     'native_width_m': 1.8, 'collider_front_matches_target_lane_shape': True},
          'event1': {'victim_front_lane': last['lane'], 'victim_front_position_m': float(last['pos']),
                     'vehicle_length_m': length, 'rear_still_on_internal_lane': ':205069138_0_1',
                     'predicted_rear_xy': list(predicted_rear.coords)[0], 'native_rear_xy': list(native_rear.coords)[0],
                     'rear_position_error_m': rear_error,
                     'front_back_straight_distance_m': math.dist(tuple(map(float, second.get('victimFront').split(','))), list(native_rear.coords)[0])},
          'claim_boundary': 'Native graph and observation correspondence only. Instantaneous lateral lane changes while the body straddles an internal lane explain the observed vehicle placement. This does not identify a unique SUMO source defect or establish road geometry or driving behavior in the field.'}
write_json_atomic(OUT / 'geometry-check.json', report)
print(json.dumps(report, indent=2))
