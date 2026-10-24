from collections import Counter
import json
from pathlib import Path
import shutil
import xml.etree.ElementTree as ET

from astra_sumo.core.artifact_io import write_json_atomic
from astra_sumo.core.hamburg_collision_trace import read_trace_source
from astra_sumo.core.route_sensor_matrix import read_contributing_e1_passages

ROOT = Path(__file__).resolve().parents[1]
CASE = ROOT / 'benchmarks/hamburg_ring1_late_exit_body_probe_20261022_v1'
results = {}
for label in ('two_nodes', 'three_nodes'):
    folder = CASE / label
    run = json.loads(read_trace_source(folder, 'run.json'))
    passage = read_contributing_e1_passages(instant_file=folder / 'instant-e1.xml',
        periodic_file=folder / 'field-counts.xml', detector_ids=run['instant_to_periodic_ids'], step_length=.1)
    assigned = {r['vehicle_id']: r['field_id'] for r in json.loads(read_trace_source(folder, 'lane-intentions.json'))['vehicles']}
    counts = Counter(r['vehicle_id'] for r in passage['passages'])
    assert passage['reconciled'] and set(counts) == set(assigned) and set(counts.values()) == {1}
    assert all(r['detector_id'] == assigned[r['vehicle_id']] for r in passage['passages'])
    trips = ET.fromstring(read_trace_source(folder, 'tripinfo.xml')).findall('tripinfo')
    assert len(trips) == 40 and all(float(t.get('arrival')) >= 0 and float(t.get('stopTime')) == 0 for t in trips)
    results[label] = {'native_E1_reconciled': True, 'unique_assigned_passages': len(counts),
                      'repeated_or_other_fields': 0, 'arrived': 40, 'sum_native_stop_time_s': 0}
for name in ('network.net.xml', 'demand.rou.xml', 'signal-programs.add.xml', 'source-e1.add.xml', 'expected-counts.csv', 'lane-intentions.json'):
    assert read_trace_source(CASE / 'two_nodes', name) == read_trace_source(CASE / 'three_nodes', name)
write_json_atomic(CASE / 'identity-check.json', {'formal_result': False, 'fixed_inputs_equal': True, 'results': results,
    'claim_boundary': 'All 40 artificial vehicles pass the assigned E1 exactly once in both paired runs. This is not the 130-row real-count calibration.'})
shutil.copyfile(Path(__file__), CASE / 'verify-late-exit-body-probe.py')
print(json.dumps(results))
