"""Account for 119 K2 service and verify the unchanged diagnostic replay."""

from collections import Counter
import gzip
import hashlib
import json
from pathlib import Path
import statistics
import xml.etree.ElementTree as ET

from astra_sumo.core.artifact_io import write_json_atomic
from astra_sumo.core.detector_demand import audit_expected_to_e1_strict, read_csv_rows, write_csv
from astra_sumo.core.hamburg_collision_trace import read_trace_source
from astra_sumo.core.hamburg_route_delay import censored_route_intervals

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / 'benchmarks/hamburg_ring1_primary_entry_full_20261022_v1'
P = ROOT / 'outputs/ring1-119-green-observation-inputs-20261022-v1'
N = ROOT / 'outputs/ring1-119-green-observation-native-20261022-v1'
OUT = ROOT / 'outputs/ring1-119-green-observation-analysis-20261022-v1'
OUT.mkdir(exist_ok=False)
sources = {}


def read(name):
    path = BASE / name
    if not path.is_file():
        path = path.with_name(path.name + '.gz')
    raw = path.read_bytes()
    sources[str(path)] = hashlib.sha256(raw).hexdigest()
    return gzip.decompress(raw) if path.suffix == '.gz' else raw


def xml(name):
    return ET.fromstring(read(name))


run = json.loads((N / 'run.json').read_bytes())
assert run['stall_monitor']['status'] == 'completed' and run['stall_monitor']['simulation_time_s'] == 3780
assert run['stall_monitor']['native_version'] == [22, 'SUMO 1.27.1']
for name in ('network.net.xml', 'demand.rou.xml', 'field-loops.add.xml', 'control.add.xml',
             'lane-intentions.json', 'initialization-counts.csv', 'expected-counts.csv', 'scenario.json'):
    assert (P / name).read_bytes() == read('inputs/' + name)
old = {(r.get('id'), r.get('begin'), r.get('end')): dict(r.attrib)
       for r in xml('native/field-counts.xml') if float(r.get('end')) <= 3600}
new = {(r.get('id'), r.get('begin'), r.get('end')): dict(r.attrib)
       for r in ET.parse(N / 'field-counts.xml').getroot() if float(r.get('end')) <= 3600}
assert len(old) == len(new) == 52 and old == new
main = audit_expected_to_e1_strict(read_csv_rows(P / 'expected-counts.csv'), N / 'field-counts.xml')
assert len(main) == 130 and all(r['measured_nVehContrib'] is None for r in main)
write_csv(OUT / 'all-130-probe-main-rows-unknown.csv', main, list(main[0]))
for name in ('field-comparison.csv', 'station-comparison.csv'):
    (OUT / ('reference-' + name)).write_bytes(read(name))
intent = {r['vehicle_id']: r for r in json.loads(read('inputs/lane-intentions.json'))['vehicles']}
fields = ('hh_0119_Z_4_29437', 'hh_0119_Z_5_30494')
selected = {v for v, r in intent.items() if r['field_id'] in fields}
population, intervals = censored_route_intervals(demand=xml('inputs/demand.rou.xml'),
    arrived_routes=xml('native/vehicle-routes.xml'), snapshot=xml('native/states/state_14399.900.xml'),
    trips=xml('native/tripinfo.xml'), selected_ids=selected)
front = [r for r in intervals if r['road'] == '24483344#1' and not r['right_censored']]
control = xml('inputs/control.add.xml').find("tlLogic[@id='LSA119_part0']")
clock, phases = 0., []
for phase in control.findall('phase'):
    end = clock + float(phase.get('duration'))
    assert phase.get('state')[12] == phase.get('state')[13]
    phases.append((clock, end, phase.get('state')[12]))
    clock = end
assert clock == 90 and [(a, b) for a, b, s in phases if s in 'Gg'] == [(75, 89)]
queue = {}
with gzip.open(BASE / 'native/queues.xml.gz', 'rb') as stream:
    for _, node in ET.iterparse(stream, events=('end',)):
        if node.tag == 'data':
            time = float(node.get('timestep'))
            if 3600 <= time < 12600 and abs(time % 90 - 75) < 1e-6:
                queue[time] = {r.get('id'): dict(r.attrib) for r in node.findall('lanes/lane')}
            node.clear()
sources[str(BASE / 'native/queues.xml.gz')] = hashlib.sha256((BASE / 'native/queues.xml.gz').read_bytes()).hexdigest()
rows, summaries = [], {}
for field, lane in zip(fields, ('24483344#1_0', '24483344#1_1')):
    local, headways = [], []
    for cycle in range(40, 140):
        begin = cycle * 90 + 75
        exits = sorted(r['end_s'] for r in front if intent[r['vehicle_id']]['field_id'] == field
                       and begin - .01 <= r['end_s'] < begin + 17.11)
        q = queue.get(float(begin), {}).get(lane)
        positive = q is not None and float(q['queueing_length']) > 0
        local.append({'detector_id': field, 'lane': lane, 'cycle': cycle, 'green_begin_s': begin,
            'green_end_s': begin + 14, 'green_and_yellow_front_exits': len(exits),
            'reported_queue_at_green_start': q is not None, 'positive_reported_queue': positive,
            'reported_queue_length_m': float(q['queueing_length']) if q else None})
        headways.extend(b - a for a, b in zip(exits, exits[1:]))
    rows.extend(local)
    summaries[field] = {'cycles': len(local), 'reported_positive_queue_cycles': sum(r['positive_reported_queue'] for r in local),
        'no_front_exit_cycles': sum(r['green_and_yellow_front_exits'] == 0 for r in local),
        'queued_but_no_front_exit_cycles': sum(r['positive_reported_queue'] and r['green_and_yellow_front_exits'] == 0 for r in local),
        'median_within_cycle_observed_front_headway_s': statistics.median(headways),
        'minimum_within_cycle_observed_front_headway_s': min(headways)}
write_csv(OUT / 'all-200-field-green-cycles.csv', rows, list(rows[0]))
snapshots = [json.loads(path.read_bytes()) for path in sorted((N / 'green-observations').glob('*.json'))]
assert len(snapshots) == 5 and all(s['native_time_before_queries_s'] == s['native_time_after_queries_s'] for s in snapshots)
summary = {'formal_result': False, 'reference_run_valid': False, 'new_full_window_runs': 0,
    'new_diagnostic_replays': 1, 'probe_end_s': 3780, 'reference_prefix_52_attributes_match': True,
    'all_130_probe_main_rows_unknown': True, 'reference_130_fields_and_40_derived_stations_retained': True,
    'population_status_for_two_fields': dict(Counter(r['status'] for r in population)),
    'field_green_service': summaries, 'snapshot_times_s': [s['time_s'] for s in snapshots],
    'snapshots_advance_no_native_time': True, 'probe_completion': run['completion'],
    'waypoint_speed_interpretation': 'A positive waypoint speed is an upper bound, not a required minimum. The unrun retirement prototype was reverted.',
    'documentation': 'https://sumo.dlr.de/docs/Definition_of_Vehicles%2C_Vehicle_Types%2C_and_Routes.html#waypoints',
    'source_sha256': sources, 'sources_unchanged': all(hashlib.sha256(Path(name).read_bytes()).hexdigest() == sha for name, sha in sources.items()),
    'claim_boundary': 'Front exits and reported queues are diagnostics. Counts remain native E1 nVehContrib. These observations do not prove a capacity upper bound, physical infeasibility, a deadlock cycle or correct field control timing.'}
write_json_atomic(OUT / 'report.json', summary)
print(json.dumps(summary), flush=True)
