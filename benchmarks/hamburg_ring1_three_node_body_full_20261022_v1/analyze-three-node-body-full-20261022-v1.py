"""Analyze only a terminal native run, keeping all real count targets."""

from collections import Counter, defaultdict
import gzip
import json
from pathlib import Path
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET

from astra_sumo.core.artifact_io import write_json_atomic
from astra_sumo.core.candidate_contracts import file_sha256
from astra_sumo.core.hamburg_collision_trace import compare_collision_trace, read_trace_source
from astra_sumo.core.hamburg_readme_results import summarize_hamburg_readme

ROOT = Path(__file__).resolve().parents[1]
P = ROOT / 'outputs/ring1-three-node-body-full-inputs-20261022-v1'
N = ROOT / 'outputs/ring1-three-node-body-full-native-20261022-v1'
REPORT = ROOT / 'outputs/ring1-three-node-body-full-report-20261022-v1'
A = ROOT / 'outputs/ring1-three-node-body-full-analysis-20261022-v1'
CASE = ROOT / 'benchmarks/hamburg_ring1_three_node_body_full_20261022_v1'
monitor = json.loads((N / 'stall-monitor.json').read_bytes())
run = json.loads((N / 'run.json').read_bytes())
assert monitor['status'] == 'completed' and monitor['simulation_time_s'] == 14400
assert json.loads((N / 'command.json').read_bytes())['status'] == 'pass'
assert monitor['junction_body_hold_nodes'] == ['14398547', '205069138', '18653534']
parent = ROOT / 'benchmarks/hamburg_ring1_localized_junction_body_full_20261021_v1'
for name in ('network.net.xml', 'demand.rou.xml', 'field-loops.add.xml', 'expected-counts.csv', 'control.add.xml', 'lane-intentions.json', 'initialization-counts.csv'):
    assert (P / name).read_bytes() == read_trace_source(parent / 'inputs', name)
for name, sha in json.loads((P / 'executed-implementation-sha256.json').read_bytes()).items():
    assert file_sha256(ROOT / name) == sha
result = summarize_hamburg_readme(repository=ROOT, prepared_dir=P, native_dir=N,
    figure_dir=ROOT / 'outputs/ring1-readme-end-to-end-figures-20261021-v1', output_dir=REPORT)
source_script = ROOT / 'outputs/decompose-localized-junction-body-main-residuals-20261021-v1.py'
decompose = ROOT / 'outputs/decompose-three-node-body-main-residuals-20261022-v1.py'
text = source_script.read_text(encoding='utf-8').replace('ring1-localized-junction-body-full-', 'ring1-three-node-body-full-').replace('20261021-v1', '20261022-v1')
decompose.write_text(text, encoding='utf-8', newline='\n')
subprocess.run([sys.executable, str(decompose)], cwd=ROOT, check=True)
A.mkdir(exist_ok=False)
source_events = [(case, e) for case in (
    ROOT / 'benchmarks/hamburg_ring1_collision_trace_20261021_v1',
    ROOT / 'benchmarks/hamburg_ring1_localized_junction_body_full_20261021_v1')
    for e in ET.fromstring(read_trace_source(case / 'native', 'collisions.xml')).findall('collision')]
nodes = {}
for _, event in source_events:
    node = event.get('lane').split('_')[0][1:]
    for role in ('collider', 'victim'):
        nodes[event.get(role)] = node
net = ET.parse(P / 'network.net.xml').getroot()
trips = {v.get('id'): v for v in ET.parse(N / 'tripinfo.xml').getroot().findall('tripinfo')}
routes = {v.get('id'): v.find('route').get('edges').split() for v in ET.parse(P / 'demand.rou.xml').getroot().findall('vehicle') if v.get('id') in nodes}
outlets = {}
for identity, node in nodes.items():
    pairs = set(zip(routes[identity], routes[identity][1:]))
    possible = {c.get('to') for c in net.findall('connection') if c.get('via', '').startswith(':' + node + '_')
                and (c.get('from'), c.get('to')) in pairs}
    assert len(possible) == 1
    outlets[identity] = possible.pop()
samples = defaultdict(list)
for step in ET.parse(N / 'participants-fcd.xml').getroot():
    for vehicle in step:
        samples[vehicle.get('id')].append(dict(vehicle.attrib, time_s=float(step.get('time'))))
witnesses = {}
for identity, node in nodes.items():
    at_node = [s for s in samples[identity] if s['lane'].startswith(':' + node + '_')]
    clear = [s for s in samples[identity] if s['lane'].startswith(outlets[identity] + '_') and float(s['pos']) >= 5]
    trip = trips.get(identity)
    witnesses[identity] = {'node': node, 'outlet': outlets[identity],
        'first_target_internal_sample': at_node[0] if at_node else None,
        'first_rear_clear_sample': clear[0] if clear else None,
        'native_arrival_s': float(trip.get('arrival')) if trip is not None else None}
changes = [v.attrib for v in ET.parse(N / 'lanechanges.xml').getroot()]
active = {}
intervals = defaultdict(list)
body = monitor['lane_section_hold']['junction_body_hold']
for event in body['events']:
    identity = event['vehicle_id']
    if event['event'] == 'held':
        active[identity] = event
    elif identity in active:
        start = active.pop(identity)
        intervals[identity].append((start['time_s'], event['time_s'], start['lane'], event['event']))
for identity, start in active.items():
    intervals[identity].append((start['time_s'], monitor['simulation_time_s'], start['lane'], 'still_held'))
during = [c for c in changes if any(begin <= float(c['time']) < end for begin, end, _, _ in intervals[c['id']])]
exits = [c for c in changes if c['from'] == '139083723#0_3' and c['to'] == '139083723#0_2']
write_json_atomic(A / 'driver-and-participant-check.json', {'formal_result': False,
    'nodes': monitor['junction_body_hold_nodes'], 'native_version': monitor['native_version'],
    'original_participants': witnesses, 'all_six_rear_cleared': all(w['first_rear_clear_sample'] is not None for w in witnesses.values()),
    'all_six_arrived': all(w['native_arrival_s'] is not None and w['native_arrival_s'] >= 0 for w in witnesses.values()),
    'changes_during_active_body_hold': during, 'observed_target_exit_changes': len(exits),
    'target_exit_changes_before_rear_clear': [c for c in exits if float(c['pos']) < 5],
    'hold_intervals_by_node': dict(Counter(lane.split('_')[0][1:] for rows in intervals.values() for _, _, lane, _ in rows)),
    'still_held': len(body['still_held']), 'left_before_release': sum(e['event'] == 'left_before_release' for e in body['events']),
    'completion': result['completion'],
    'claim_boundary': 'One complete fixed-seed replay under declared driver and control assumptions. Zero collisions alone does not establish valid demand, natural driving, other-seed safety or exact calibration.'})
old = ROOT / 'benchmarks/hamburg_ring1_late_collision_trace_20261021_v1'
old_command_sha = file_sha256(old / 'native/command.json')
compared = compare_collision_trace(case_dir=ROOT / 'benchmarks/hamburg_ring1_localized_junction_body_full_20261021_v1', prepared_dir=old / 'native')
assert compared['reproduction_matched'] and file_sha256(old / 'native/command.json') == old_command_sha
write_json_atomic(A / 'trace-command-record-note.json', {
    'affected_case': old.name, 'original_wrapper_summary_retained': False,
    'reason': 'The preceding after-the-fact comparison replaced command.json with a non-execution placeholder. That original wrapper summary is unavailable. The placeholder remains preserved, not reconstructed as an original run record.',
    'actual_native_command_preserved_in': 'native/stall-monitor.json', 'native_input_request_preserved_in': 'native/native-request.json',
    'native_counts_and_outputs_overwritten': False, 'read_only_recomparison': compared,
    'fix': 'Saved-observation comparison no longer runs the wrapper or writes any files.', 'placeholder_sha256': old_command_sha})
CASE.mkdir(exist_ok=False)
for label, source in [('inputs', P), ('native', N), ('', REPORT), ('analysis', A),
                       ('data-analysis', ROOT / 'outputs/ring1-three-node-body-full-data-analysis-20261022-v1'),
                       ('live-observations', ROOT / 'outputs/ring1-three-node-body-full-live-notes-20261022-v1')]:
    for path in source.rglob('*'):
        if not path.is_file():
            continue
        target = CASE / label / path.relative_to(source)
        target.parent.mkdir(parents=True, exist_ok=True)
        if path.suffix in {'.xml', '.json', '.csv'} and path.stat().st_size > 200000 and not (label == 'inputs' and path.name == 'network.net.xml'):
            target = target.with_name(target.name + '.gz')
            target.write_bytes(gzip.compress(path.read_bytes(), mtime=0))
            assert gzip.decompress(target.read_bytes()) == path.read_bytes()
        else:
            shutil.copyfile(path, target)
shutil.copyfile(CASE / 'README.md', CASE / 'workflow-report.md')
shutil.copyfile(ROOT / 'benchmarks/hamburg_ring1_late_exit_body_probe_20261022_v1/.gitattributes', CASE / '.gitattributes')
for path in (Path(__file__), ROOT / 'outputs/run-three-node-body-full-20261022-v1.py', decompose):
    shutil.copyfile(path, CASE / path.name)
figure_source = ROOT / 'benchmarks/hamburg_ring1_localized_junction_body_full_20261021_v1/figure-provenance.json'
assert json.loads(figure_source.read_bytes())['current_network_sha256'] == result['network_sha256']
shutil.copyfile(figure_source, CASE / 'figure-provenance.json')
write_json_atomic(CASE / 'execution-outcome.json', {'formal_result': False, 'native_reached_planned_end': True,
    'native_end_s': 14400, 'timeout': False, 'completion': result['completion'],
    'driver_only_change_from_two_node_full': True, 'all_vehicle_requests_completed': result['completion']['waiting'] == result['completion']['running'] == 0})
print(json.dumps({'case': str(CASE), 'completion': result['completion'], 'exact_fields': result['exact_field_rows'],
      'diagnostic_mae': result['diagnostic_field_MAE'], 'diagnostic_max_abs_error': result['diagnostic_max_abs_error']}), flush=True)
