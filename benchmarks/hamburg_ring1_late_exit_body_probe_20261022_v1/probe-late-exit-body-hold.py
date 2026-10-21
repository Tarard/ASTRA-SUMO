"""Paired artificial traffic tests an observed exit maneuver, not count calibration."""

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

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'benchmarks/hamburg_ring1_localized_junction_body_full_20261021_v1'
OUT = ROOT / 'outputs/ring1-late-exit-body-hold-probe-20261022-v1'
OUT.mkdir(exist_ok=False)
for name in ('network.net.xml', 'field-loops.add.xml', 'control.add.xml'):
    (OUT / name).write_bytes(read_trace_source(SOURCE / 'inputs', name))
source_routes = ET.fromstring(read_trace_source(SOURCE / 'inputs', 'demand.rou.xml'))
templates = {v.get('id'): v for v in source_routes.findall('vehicle')}
pairs = [('readme_f4_b4_p208_v5', 'hh_0119_Z_4_29437'),
         ('readme_f5_b4_p56_v25', 'hh_0119_Z_5_30494')]
routes = ET.Element('routes')
routes.extend(deepcopy(v) for v in source_routes.findall('vType'))
intentions = []
for index in range(20):
    for approach, (identity, field) in enumerate(pairs):
        v = deepcopy(templates[identity])
        v.set('id', f'artificial_a{approach}_{index}')
        v.set('depart', str(100 + 8 * index + approach * 2))
        routes.append(v)
        intentions.append({'vehicle_id': v.get('id'), 'field_id': field})
ET.ElementTree(routes).write(OUT / 'demand.rou.xml', encoding='utf-8', xml_declaration=True)
write_json_atomic(OUT / 'lane-intentions.json', {'vehicles': intentions})
fields = ET.parse(OUT / 'field-loops.add.xml').getroot()
targets = [{'detector_id': f.get('id'), 'begin': 0, 'end': 900,
            'expected_total': 20 if f.get('id') in {p[1] for p in pairs} else 0} for f in fields]
write_csv(OUT / 'artificial-counts.csv', targets, list(targets[0]))
write_json_atomic(OUT / 'proposal.json', {
    'formal_result': False, 'source_case': str(SOURCE), 'vehicles': 40,
    'purpose': 'Test the original two whole-route profiles under a sparse artificial cohort. This is not the original collision or whole-demand reproduction.',
    'fixed_between_cases': 'Network, roads, lane permissions, signals, vehicle type, 40 routes, request times, detector intentions and artificial count targets.',
    'change': 'Add only node 18653534 to the existing body-hold scope at 14398547 and 205069138.',
    'evaluation': 'Native completion and E1 20 per assigned field, no collisions/teleports/standstill, actual exit lane-change positions and absence of lane changes during active body holds.',
    'claim_boundary': 'Artificial mechanism and route-clearance test only. All original 130/40 real targets stay in the source case and are not replaced by these 13 artificial rows.'})
results = {}
for label, nodes in [('two_nodes', ('14398547', '205069138')),
                     ('three_nodes', ('14398547', '205069138', '18653534'))]:
    folder = OUT / label

    def observed_command(command, **kwargs):
        request_file = Path(command[-1])
        request = json.loads(request_file.read_bytes())
        request['command'].extend(['--lanechange-output', str(folder / 'lanechanges.xml'),
                                   '--fcd-output', str(folder / 'fcd.xml'), '--fcd-output.skip-empty', 'true'])
        write_json_atomic(request_file, request)
        return run_command(command, **kwargs)

    with patch('astra_sumo.core.detector_demand_replay.run_command', observed_command):
        result = run_point_detector_demand(net_file=OUT / 'network.net.xml', route_file=OUT / 'demand.rou.xml',
            e1_additional_file=OUT / 'field-loops.add.xml', expected_counts_csv=OUT / 'artificial-counts.csv',
            output_dir=folder, end_s=900, sumo_binary=str(Path(sumo.__file__).parent / 'bin/sumo.exe'),
            timeout_seconds=180, signal_program_file=OUT / 'control.add.xml',
            lane_intentions_file=OUT / 'lane-intentions.json', junction_body_hold=True, junction_body_hold_nodes=nodes)
    changes = [v.attrib for v in ET.parse(folder / 'lanechanges.xml').getroot()]
    holds = result['stall_monitor']['lane_section_hold']['junction_body_hold']['events']
    active = {}
    intervals = []
    for event in holds:
        if event['event'] == 'held':
            active[event['vehicle_id']] = event
        elif event['vehicle_id'] in active:
            start = active.pop(event['vehicle_id'])
            intervals.append((start['vehicle_id'], start['time_s'], event['time_s'], start['lane']))
    during = [v for v in changes if any(v['id'] == identity and begin <= float(v['time']) < end
              for identity, begin, end, _ in intervals)]
    exits = [v for v in changes if v['from'] == '139083723#0_3' and v['to'] == '139083723#0_2']
    results[label] = {k: result.get(k) for k in ('healthy', 'completion', 'observer_complete', 'exact_field_bins', 'missing_field_bins')}
    results[label].update(native_version=result['stall_monitor']['native_version'], exit_changes=exits,
                          changes_during_body_hold=during,
                          target_node_holds=sum(lane.startswith(':18653534_') for _, _, _, lane in intervals))
    write_json_atomic(folder / 'maneuver-check.json', results[label])
    print(json.dumps({k: v for k, v in results[label].items() if k != 'exit_changes'}), flush=True)
write_json_atomic(OUT / 'paired-check.json', {'formal_result': False, 'results': results})
assert all(r['healthy'] and r['completion']['arrived'] == 40 and r['exact_field_bins'] == 13
           and r['native_version'] == [22, 'SUMO 1.27.1'] and not r['changes_during_body_hold'] for r in results.values())
assert results['two_nodes']['exit_changes'] and min(float(v['pos']) for v in results['two_nodes']['exit_changes']) < 5
assert results['three_nodes']['exit_changes'] and min(float(v['pos']) for v in results['three_nodes']['exit_changes']) >= 5
assert results['three_nodes']['target_node_holds'] == 40
print('Paired artificial maneuver checks passed.', flush=True)
