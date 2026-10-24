import collections
import gzip
import json
from pathlib import Path
import shutil
import xml.etree.ElementTree as ET

from astra_sumo.core.artifact_io import write_json_atomic
from astra_sumo.core.hamburg_collision_trace import read_trace_source

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'outputs/ring1-late-exit-body-hold-probe-20261022-v1'
CASE = ROOT / 'benchmarks/hamburg_ring1_late_exit_body_probe_20261022_v1'
CASE.mkdir(exist_ok=False)
for path in SOURCE.rglob('*'):
    if not path.is_file():
        continue
    target = CASE / path.relative_to(SOURCE)
    target.parent.mkdir(parents=True, exist_ok=True)
    if path.suffix in {'.xml', '.json', '.csv'} and path.stat().st_size > 200000:
        target = target.with_name(target.name + '.gz')
        target.write_bytes(gzip.compress(path.read_bytes(), mtime=0))
        assert gzip.decompress(target.read_bytes()) == path.read_bytes()
    else:
        shutil.copyfile(path, target)
for name in ('probe-late-exit-body-hold', 'freeze-late-exit-body-probe'):
    shutil.copyfile(ROOT / 'outputs' / (name + '-20261022-v1.py'), CASE / (name + '.py'))
shutil.copyfile(ROOT / 'outputs/probe-late-exit-body-hold-20261022-v1.log', CASE / 'execution.log')
shutil.copyfile(ROOT / 'benchmarks/hamburg_ring1_late_collision_trace_20261021_v1/.gitattributes', CASE / '.gitattributes')
parent = ROOT / 'benchmarks/hamburg_ring1_localized_junction_body_full_20261021_v1'
net = ET.fromstring(read_trace_source(parent / 'inputs', 'network.net.xml'))
lanes = {lane.get('id'): lane for edge in net.findall('edge') for lane in edge.findall('lane')}


def passenger(row):
    allowed = row.get('allow')
    excluded = row.get('disallow', '').split()
    return (allowed is None or 'passenger' in allowed.split() or 'all' in allowed.split()) and 'passenger' not in excluded and 'all' not in excluded


links = collections.defaultdict(list)
for link in net.findall('connection'):
    if link.get('via', '').startswith(':18653534_') and not link.get('from').startswith(':'):
        outlet = link.get('to') + '_' + link.get('toLane')
        if passenger(link) and passenger(lanes[outlet]) and passenger(lanes[link.get('via')]):
            links[link.get('from'), link.get('to')].append(link)
used = collections.Counter()
routes = ET.fromstring(read_trace_source(parent / 'inputs', 'demand.rou.xml'))
for vehicle in routes.findall('vehicle'):
    roads = vehicle.find('route').get('edges').split()
    for pair in zip(roads, roads[1:]):
        if pair in links:
            used[pair] += 1
movements = [{'from': pair[0], 'to': pair[1], 'model_requests': number,
              'allowed_passenger_outlet_lanes': sorted({link.get('toLane') for link in links[pair]}),
              'outlet_length_m': sorted({float(lanes[pair[1] + '_' + link.get('toLane')].get('length')) for link in links[pair]})}
             for pair, number in sorted(used.items())]
exit_lanes = sorted({link.get('fromLane') for link in net.findall('connection')
    if link.get('from') == '139083723#0' and link.get('to') == '234423652#1'
    and passenger(link) and passenger(lanes[link.get('from') + '_' + link.get('fromLane')]) and passenger(lanes[link.get('via')])})
assert exit_lanes == ['2'] and sum(used.values()) == 1082
assert min(length for row in movements for length in row['outlet_length_m']) == 38.38
assert not passenger(lanes['139083723#0_1'])
write_json_atomic(CASE / 'passenger-route-audit.json', {'formal_result': False, 'model_requests': sum(used.values()),
    'movements': movements, 'passenger_route_exit_lanes': exit_lanes,
    'bicycle_only_lane': '139083723#0_1', 'lane_permissions_changed': False,
    'claim_boundary': 'The earlier unfiltered connection list included lane 1. Passenger permissions leave only lane 2. All 1082 planned movements at this junction lead to the 38.38 m outlet. This is a static route and permission check, not field capacity or full-demand clearance.'})
print(CASE)
