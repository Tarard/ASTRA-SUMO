"""Test one declared buffer-lane intention without changing routes or signals."""

from collections import Counter
from copy import deepcopy
import json
from pathlib import Path
from unittest.mock import patch
import xml.etree.ElementTree as ET

import sumo

from astra_sumo.core.artifact_io import write_json_atomic
from astra_sumo.core.command_runner import run_command
from astra_sumo.core.detector_demand import write_csv
from astra_sumo.core.detector_demand_replay import run_point_detector_demand
from astra_sumo.core.hamburg_collision_trace import read_trace_source
from astra_sumo.core.route_sensor_matrix import read_contributing_e1_passages

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'benchmarks/hamburg_ring1_primary_entry_full_20261022_v1'
OUT = ROOT / 'outputs/ring1-z15-buffer-lane-probe-20261022-v1'
OUT.mkdir(exist_ok=False)
field = 'hh_0119_Z_15_29375'
for name in ('network.net.xml', 'control.add.xml', 'expected-counts.csv'):
    (OUT / name).write_bytes(read_trace_source(SOURCE / 'inputs', name))
network = ET.parse(OUT / 'network.net.xml').getroot()
lane = network.find("edge[@id='142944325#0']/lane[@id='142944325#0_1']")
assert float(lane.get('length')) == 20.377749
full = ET.fromstring(read_trace_source(SOURCE / 'inputs', 'demand.rou.xml'))
assigned = {r['vehicle_id']: r for r in json.loads(read_trace_source(SOURCE / 'inputs', 'lane-intentions.json'))['vehicles']}
templates = {}
for vehicle in full.findall('vehicle'):
    route = vehicle.find('route').get('edges').split()
    if assigned[vehicle.get('id')]['field_id'] == field and '142944325#0' in route:
        templates.setdefault(route[0], vehicle)
assert set(templates) == {'189764301', '61578529#1'}
loops = ET.fromstring(read_trace_source(SOURCE / 'inputs', 'field-loops.add.xml'))
for loop in loops:
    loop.set('freq', '1800')
ET.ElementTree(loops).write(OUT / 'artificial-loops.add.xml', encoding='utf-8', xml_declaration=True)
targets = [{'detector_id': r.get('id'), 'begin': 0, 'end': 1800, 'expected_total': 40 if r.get('id') == field else 0} for r in loops]
write_csv(OUT / 'artificial-targets.csv', targets, list(targets[0]))
write_json_atomic(OUT / 'proposal.json', {'formal_result': False, 'vehicles': 40, 'end_s': 1800,
    'source_model': str(SOURCE), 'entry_share': '27 existing Ballindamm and 13 existing Brandsende model profiles, spread across the same 40 request times.',
    'baseline': 'Existing field waypoint only.',
    'candidate': 'Add one positive-speed waypoint on buffer lane 1 for 20 alternating vehicles, 0.1 m before its end. No extra lane-change mode, route, signal, permission or collision option.',
    'observation': 'Native FCD and lane changes in both cases. Artificial E1 aggregation is 1800 s in both cases.',
    'claim_boundary': 'Sparse artificial mechanism test, not the original dense queue or real-count calibration. All 130 real main targets stay retained separately, and 40 station targets remain in the source case.'})
results = {}
for label in ('baseline', 'two_lane_intent'):
    folder = OUT / label
    folder.mkdir()
    demand = ET.Element('routes')
    demand.extend(deepcopy(v) for v in full.findall('vType'))
    intentions = []
    for i in range(40):
        entry = '189764301' if (i + 1) * 27 // 40 > i * 27 // 40 else '61578529#1'
        vehicle = deepcopy(templates[entry])
        identity = f'artificial_z15_buffer_{i}'
        vehicle.set('id', identity)
        vehicle.set('depart', str(100 + 3 * i))
        if label == 'two_lane_intent' and i % 2 == 0:
            waypoint = ET.Element('stop', lane=lane.get('id'), endPos=f"{float(lane.get('length')) - .1:.6f}", speed=lane.get('speed'))
            vehicle.insert(1, waypoint)
        demand.append(vehicle)
        intentions.append({'vehicle_id': identity, 'field_id': field})
    ET.ElementTree(demand).write(folder / 'demand.rou.xml', encoding='utf-8', xml_declaration=True)
    write_json_atomic(folder / 'intentions.json', {'vehicles': intentions})
    native = folder / 'native'

    def observed_command(command, **kwargs):
        request_file = Path(command[-1])
        request = json.loads(request_file.read_bytes())
        request['command'].extend(['--fcd-output', str(native / 'fcd.xml'), '--lanechange-output', str(native / 'lanechanges.xml'), '--fcd-output.skip-empty', 'true'])
        write_json_atomic(request_file, request)
        return run_command(command, **kwargs)

    with patch('astra_sumo.core.detector_demand_replay.run_command', observed_command):
        result = run_point_detector_demand(net_file=OUT / 'network.net.xml', route_file=folder / 'demand.rou.xml',
            e1_additional_file=OUT / 'artificial-loops.add.xml', expected_counts_csv=OUT / 'artificial-targets.csv',
            output_dir=native, end_s=1800, sumo_binary=str(Path(sumo.__file__).parent / 'bin/sumo.exe'), timeout_seconds=240,
            signal_program_file=OUT / 'control.add.xml', lane_intentions_file=folder / 'intentions.json',
            junction_body_hold=True, junction_body_hold_nodes=('14398547', '205069138', '18653534'))
    passage = read_contributing_e1_passages(instant_file=native / 'instant-e1.xml', periodic_file=native / 'field-counts.xml',
        detector_ids=result['instant_to_periodic_ids'], step_length=.1)
    first_entry, exit_lane, exit_time = {}, {}, {}
    previous = {}
    for step in ET.parse(native / 'fcd.xml').getroot():
        time = float(step.get('time'))
        for row in step:
            identity, current = row.get('id'), row.get('lane')
            if current.startswith('142944325#0_'):
                first_entry.setdefault(identity, {'lane': current, 'time_s': time})
            if current.startswith(':LSA200_part0_28_') and previous.get(identity, '').startswith('142944325#0_'):
                exit_lane[identity], exit_time[identity] = previous[identity], time
            previous[identity] = current
    trips = ET.parse(native / 'tripinfo.xml').getroot().findall('tripinfo')
    field_passes = Counter((r['vehicle_id'], r['detector_id']) for r in passage['passages'])
    results[label] = {'completion': result['completion'], 'healthy': result['healthy'],
        'native_version': result['stall_monitor']['native_version'], 'native_E1_reconciled': passage['reconciled'],
        'field_passages': len(passage['passages']), 'every_vehicle_once_at_assigned_field': len(field_passes) == 40 and all(n == 1 and f == field for (_, f), n in field_passes.items()),
        'first_buffer_entry_lanes': dict(Counter(r['lane'] for r in first_entry.values())),
        'buffer_exit_lanes': dict(Counter(exit_lane.values())), 'buffer_exit_times_s': exit_time,
        'last_buffer_exit_s': max(exit_time.values(), default=None),
        'last_native_arrival_s': max((float(t.get('arrival')) for t in trips), default=None),
        'sum_native_stop_time_s': sum(float(t.get('stopTime')) for t in trips)}
    write_json_atomic(folder / 'maneuver-check.json', results[label])
    print(json.dumps({k: v for k, v in results[label].items() if k != 'buffer_exit_times_s'}), flush=True)
write_json_atomic(OUT / 'paired-check.json', {'formal_result': False, 'results': results,
    'claim_boundary': 'Compare actual buffer use and native safety before considering a larger demand test. Sparse results cannot establish the cause of the original queue or main-window count fit.'})
