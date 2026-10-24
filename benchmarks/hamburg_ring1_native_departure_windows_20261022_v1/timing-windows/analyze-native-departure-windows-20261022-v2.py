"""Check bounded departure corrections without changing or rerunning the source cases."""

from collections import Counter
import csv
import gzip
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import xml.etree.ElementTree as ET

from astra_sumo.core.artifact_io import write_json_atomic
from astra_sumo.core.detector_demand import write_csv
from astra_sumo.core.native_temporal_feedback import native_departure_windows

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'outputs/ring1-native-departure-windows-20261022-v2'
OUT.mkdir(exist_ok=False)
CASE_NAMES = {
    'primary': 'hamburg_ring1_primary_entry_full_20261022_v1',
    'shared': 'hamburg_ring1_shared_population_full_20261022_v1',
}
sources, summaries = {}, {}


def read(case, name):
    path = case / name
    if not path.is_file():
        path = path.with_name(path.name + '.gz')
    raw = path.read_bytes()
    sources[path.relative_to(ROOT).as_posix()] = hashlib.sha256(raw).hexdigest()
    return gzip.decompress(raw) if path.suffix == '.gz' else raw


for label, name in CASE_NAMES.items():
    case = ROOT / 'benchmarks' / name
    read(case, 'manifest.json')
    route = ET.fromstring(read(case, 'inputs/demand.rou.xml'))
    vehicles = {v.get('id'): v for v in route.findall('vehicle')}
    intentions = json.loads(read(case, 'inputs/lane-intentions.json'))['vehicles']
    assert len(vehicles) == len(intentions) == len({r['vehicle_id'] for r in intentions})
    native = json.loads(read(case, 'passage-reconciliation.json'))
    assert native['status'] == 'measured' and native['reconciled']
    periodic = ET.fromstring(read(case, 'native/field-counts.xml')).findall('interval')
    measured = {(r.get('id'), float(r.get('begin')), float(r.get('end'))): int(r.get('nVehContrib')) for r in periodic}
    passages = Counter((r['detector_id'], r['interval_begin_s'], r['interval_end_s']) for r in native['passages'])
    assert len(measured) == len(periodic) and not passages.keys() - measured.keys()
    assert all(passages[key] == value for key, value in measured.items())
    targets = Counter()
    requests = []
    for intention in intentions:
        identity = intention['vehicle_id']
        assigned = intention.get('target_observations', [intention])
        visits = [{'detector_id': r['field_id'], 'begin_s': float(r['target_begin_s']),
                   'end_s': float(r['target_end_s'])} for r in assigned]
        targets.update((t['detector_id'], t['begin_s'], t['end_s']) for t in visits)
        requests.append({'vehicle_id': identity, 'requested_depart_s': float(vehicles[identity].get('depart')),
            'entry': vehicles[identity].find('route').get('edges').split()[0],
            'population_role': intention['population_role'], 'targets': visits})
    for filename in ('initialization-counts.csv', 'expected-counts.csv'):
        rows = list(csv.DictReader(read(case, 'inputs/' + filename).decode('utf-8-sig').splitlines()))
        for row in rows:
            key = row['detector_id'], float(row['begin']), float(row['end'])
            assert targets.pop(key) == int(row['expected_total'])
    assert not targets
    out = OUT / label
    out.mkdir()
    for filename in ('field-comparison.csv', 'station-comparison.csv'):
        (out / filename).write_bytes(read(case, filename))
    summaries[label] = {}
    for guard in (0, 90):
        result = native_departure_windows(requests=requests, passages=native['passages'], max_shift_s=900, guard_s=guard)
        assert result['target_field_visits'] == 8640
        assert sum(result['status_counts'].values()) == len(vehicles)
        times = {(p['vehicle_id'], p['detector_id']): p['notification_time_s'] for p in native['passages']}
        for row in result['rows']:
            if row['proposed_shift_s'] is None:
                assert row['proposed_depart_s'] is None
                continue
            assert 0 <= row['proposed_depart_s'] and abs(row['proposed_shift_s']) <= 900
            for target in row['targets']:
                predicted = times[row['vehicle_id'], target['detector_id']] + row['proposed_shift_s']
                assert target['begin_s'] + guard - 1e-7 <= predicted < target['end_s'] - guard
        all_rows = result.pop('rows')
        (out / f'all-vehicle-windows-margin{guard}.json.gz').write_bytes(gzip.compress(
            json.dumps(all_rows, ensure_ascii=False).encode('utf-8'), mtime=0))
        compact = [{k: v for k, v in row.items() if k not in ('targets', 'missing_fields')}
                   | {'target_fields': '|'.join(t['detector_id'] for t in row['targets']),
                      'missing_fields': '|'.join(row['missing_fields'])} for row in all_rows]
        write_csv(out / f'all-vehicle-windows-margin{guard}.csv', compact, list(compact[0]))
        result['shared_status_counts'] = dict(Counter(r['status'] for r in all_rows if len(r['targets']) == 2))
        result['nonzero_proposals'] = sum(r['proposed_shift_s'] not in (None, 0) for r in all_rows)
        result['proposed_advances'] = sum(r['proposed_shift_s'] is not None and r['proposed_shift_s'] < 0 for r in all_rows)
        result['proposed_delays'] = sum(r['proposed_shift_s'] is not None and r['proposed_shift_s'] > 0 for r in all_rows)
        summaries[label][str(guard)] = result
        write_json_atomic(out / f'summary-margin{guard}.json', result)
    trips = {v.get('id') for v in ET.fromstring(read(case, 'native/tripinfo.xml')).findall('tripinfo')}
    observed_ids = {p['vehicle_id'] for p in native['passages']}
    summaries[label]['missing_passages'] = {'not_inserted_model_vehicles': len(vehicles.keys() - trips),
        'inserted_without_any_passage': len(trips - observed_ids)}

assert all(hashlib.sha256((ROOT / name).read_bytes()).hexdigest() == sha for name, sha in sources.items())
write_json_atomic(OUT / 'source-sha256.json', sources)
write_json_atomic(OUT / 'summary.json', {'formal_result': False, 'new_simulations': 0, 'sources_unchanged': True,
    'source_commit': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
    'cases': summaries, 'claim_boundary': 'All 130 native field and 40 derived station residuals remain unchanged. Feasible frozen-lag proposals are not simulated results. No requests, fields, periods, routes, controls or native counts are removed or changed.'})
shutil.copyfile(Path(__file__), OUT / Path(__file__).name)
print(json.dumps({label: {k: value['status_counts'] for k, value in values.items() if k != 'missing_passages'}
    for label, values in summaries.items()}), flush=True)
