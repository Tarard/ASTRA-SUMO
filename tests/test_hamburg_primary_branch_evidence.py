"""Check the frozen local repair without starting the full demand experiment."""
import gzip
import csv
import hashlib
import io
import json
from pathlib import Path
import xml.etree.ElementTree as ET

import pytest

from astra_sumo.core.hamburg_readme_results import STATIONS
from astra_sumo.core.local_network_conversion import loaded_turn_permissions


CASE = Path(__file__).parents[1] / 'benchmarks/hamburg_ring1_primary_branch_repair_20261023_v1'
LOCAL = {'266197525', '6390683113', '1562406613'}
ADDED = {'1108564798#0', '1108564798#1'}


def raw(name):
    return gzip.decompress((CASE / (name + '.gz')).read_bytes())


def test_frozen_primary_branch_files_and_original_bytes_match_manifest():
    manifest = json.loads((CASE / 'manifest.json').read_bytes())
    assert not manifest['formal_result']
    assert {row['path'] for row in manifest['files']} == {
        p.relative_to(CASE).as_posix() for p in CASE.rglob('*')
        if p.is_file() and p.name != 'manifest.json' and '__pycache__' not in p.parts}
    for row in manifest['files']:
        data = (CASE / row['path']).read_bytes()
        assert hashlib.sha256(data).hexdigest() == row['sha256']
        if row['path'].endswith('.gz'):
            assert hashlib.sha256(gzip.decompress(data)).hexdigest() == row['raw_sha256']


def test_frozen_repair_preserves_236_roads_683_turns_and_other_controllers():
    old, new, source = [ET.fromstring(raw(name)) for name in (
        'parent-prepared/network.net.xml', 'prepared/network.net.xml', 'source/network.net.xml')]
    roads = [{r.get('id'): r for r in root.findall('edge') if not r.get('function')}
             for root in (old, new, source)]
    assert len(roads[0]) == 236 and roads[1].keys() == roads[0].keys() | ADDED
    assert all(ET.tostring(r) == ET.tostring(roads[1][key]) for key, r in roads[0].items())
    assert all(ET.tostring(roads[1][key]) == ET.tostring(roads[2][key]) for key in ADDED)
    old_turns, new_turns = [loaded_turn_permissions(root) for root in (old, new)]
    assert len(old_turns) == 683 and len(new_turns) == 691
    assert all(modes == new_turns.get(key) for key, modes in old_turns.items())
    assert not [key for key in new_turns.keys() - old_turns.keys()
                if key[0] in roads[0] and key[2] in roads[0]]
    old_control, new_control = [ET.fromstring(raw(name)) for name in (
        'parent-prepared/control.add.xml', 'prepared/control.add.xml')]
    assert [ET.tostring(r) for r in old_control if r.get('id') not in LOCAL] == [
        ET.tostring(r) for r in new_control if r.get('id') not in LOCAL]


def test_frozen_8640_requests_change_only_the_2589_declared_tails():
    old, new = [ET.fromstring(raw(name)) for name in ('parent-evaluation/demand.rou.xml', 'prepared/demand.rou.xml')]
    before, after = [{v.get('id'): v for v in root.findall('vehicle')} for root in (old, new)]
    assert before.keys() == after.keys() and len(before) == 8640
    changed = 0
    for identity, row in before.items():
        candidate = after[identity]
        a, b = [v.find('route') for v in (row, candidate)]
        first, second = a.get('edges'), b.get('edges')
        if first != second:
            assert first.endswith('73474895 27064186#0 1073054122#1 1345098540 24483197 199500676')
            assert second == first.replace('27064186#0 1073054122#1 1345098540', '1108564798#0 1108564798#1')
            b.set('edges', first)
            changed += 1
        assert ET.tostring(row) == ET.tostring(candidate)
    assert changed == 2589
    assert raw('parent-evaluation/lane-intentions.json') == raw('prepared/lane-intentions.json')
    for name in ('field-loops.add.xml', 'expected-counts.csv', 'initialization-counts.csv'):
        assert raw('parent-prepared/' + name) == raw('prepared/' + name)


def test_five_sparse_vehicles_really_traversed_both_primary_lanes_without_faults():
    trips = ET.fromstring(raw('sparse-probe/tripinfo.xml')).findall('tripinfo')
    assert len(trips) == 5 and all(float(t.get('arrival')) >= 0 for t in trips)
    final = ET.fromstring(raw('sparse-probe/summary.xml'))[-1]
    assert int(final.get('arrived')) == 5
    assert not any(int(final.get(k)) for k in ('running', 'waiting', 'collisions', 'teleports', 'discarded'))
    assert not ET.fromstring(raw('sparse-probe/collisions.xml')).findall('collision')
    for vehicle in ET.fromstring(raw('sparse-probe/vehicle-routes.xml')).findall('vehicle'):
        route = vehicle.find('route')
        edges, times = route.get('edges').split(), list(map(float, route.get('exitTimes').split()))
        assert all(times[edges.index(edge)] >= 0 for edge in ADDED | {'24483197'})
    observed = {v.get('lane') for v in ET.fromstring(raw('sparse-probe/fcd.xml')).iter('vehicle')
                if v.get('id', '').startswith('primary_lane_')}
    assert {'1108564798#0_1', '1108564798#0_2'} <= observed


@pytest.mark.parametrize('variant', ['parent-evaluation', 'evaluation'])
def test_full_130_fields_40_stations_and_completion_are_native_counts(variant):
    measured = {(r.get('id'), float(r.get('begin')), float(r.get('end'))): int(r.get('nVehContrib'))
                for r in ET.fromstring(raw(variant+'/native/field-counts.xml'))}
    rows = list(csv.DictReader(io.StringIO(raw(variant+'/native/field-comparison.csv').decode('utf-8'))))
    metadata = {(r['detector_id'], float(r['begin']), float(r['end'])): r for r in csv.DictReader(
        io.StringIO(raw('prepared/expected-counts.csv').decode('utf-8')))}
    assert len(rows) == len(metadata) == 130
    residuals, groups = [], {}
    for row in rows:
        key = row['detector_id'], float(row['begin']), float(row['end'])
        source = metadata[key]
        count, target = measured[key], int(source['expected_total'])
        assert count == int(row['measured_nVehContrib'])
        assert count-target == int(row['diff_nVehContrib_minus_expected'])
        residuals.append(count-target)
        for station, (node, assets) in STATIONS.items():
            if source['node_id'] == node and source['asset_id'] in assets:
                group = groups.setdefault((station, key[1], key[2]), [0, 0, 0])
                group[0] += target
                group[1] += count
                group[2] += count-target
    result = json.loads(raw(variant+'/evaluation.json'))
    assert sum(map(abs, residuals))/130 == result['MAE']
    assert sum(r*r for r in residuals) == result['loss']
    assert max(map(abs, residuals)) == result['max_abs_error']
    stations = list(csv.DictReader(io.StringIO(raw(variant+'/station-comparison.csv').decode('utf-8'))))
    assert len(stations) == len(groups) == 40
    for row in stations:
        key = row['station_stream_id'], float(row['begin']), float(row['end'])
        assert groups[key] == [int(row[k]) for k in ('expected_total', 'measured_nVehContrib', 'residual')]
        assert row['independent_evidence'] == 'False'
    final = ET.fromstring(raw(variant+'/native/summary.xml'))[-1]
    assert result['completion'] == {k: int(final.get(k)) for k in result['completion']}
    assert not any(result['completion'][k] for k in ('collisions', 'teleports', 'discarded'))
    assert not result['healthy'] and not result['exact_count_fit']


def test_all182_and_stage_goal_preserve_unfinished_and_warning_evidence():
    measured = {(r.get('id'), float(r.get('begin')), float(r.get('end'))): int(r.get('nVehContrib'))
                for r in ET.fromstring(raw('evaluation/native/field-counts.xml'))}
    rows = list(csv.DictReader(io.StringIO(raw('evaluation/all182-comparison.csv').decode('utf-8'))))
    assert len(rows) == 182 and sum(float(r['begin']) < 3600 for r in rows) == 52
    for row in rows:
        value = measured[row['detector_id'], float(row['begin']), float(row['end'])]
        assert value == int(row['measured_nVehContrib'])
        assert value-int(row['expected_total']) == int(row['residual'])
    verdict = json.loads(raw('full-verdict.json'))
    assert sum(abs(int(r['residual'])) for r in rows) == verdict['all182_TAE'] == 1489
    assert verdict['stage_goal_met'] and not verdict['formal_result']
    assert verdict['after']['MAE'] <= 10 and verdict['after']['completion']['waiting'] <= 690
    assert verdict['after']['completion']['waiting'] == 178 and verdict['after']['completion']['running'] == 227
    warnings = raw('evaluation/native/sumo-errors.log').decode('utf-8')
    assert "Missing green phase in tlLogic 'LSA535_part0'" in warnings
    assert warnings.count('Warning:') == 28
    assert warnings.count('has no controlling detector') == 18
    assert warnings.count('Unused states in tlLogic') == 3
    assert warnings.count('Missing green phase') == 2
    assert warnings.count('performs emergency braking') == 5
    for frozen, native in [('network.net.xml', 'network.net.xml'), ('control.add.xml', 'signal-programs.add.xml'),
                           ('demand.rou.xml', 'demand.rou.xml'), ('lane-intentions.json', 'lane-intentions.json')]:
        assert raw('prepared/'+frozen) == raw('evaluation/native/'+native)


def test_plot_keeps_every_native_field_and_aerial_review_stays_unresolved():
    plot = json.loads((CASE/'plot-data.json').read_bytes())
    before, after = [list(csv.DictReader(io.StringIO(raw(v+'/native/field-comparison.csv').decode('utf-8'))))
                     for v in ('parent-evaluation', 'evaluation')]
    native = [{(r['detector_id'], float(r['begin'])): r for r in rows} for rows in (before, after)]
    assert set(plot['source_csv_sha256'].values()) == {
        hashlib.sha256(raw(v+'/native/field-comparison.csv')).hexdigest()
        for v in ('parent-evaluation', 'evaluation')}
    assert len(plot['rows']) == len({(r['field'], r['begin']) for r in plot['rows']}) == 130
    for row in plot['rows']:
        key = row['field'], row['begin']
        assert row['observed'] == int(native[1][key]['expected_total'])
        assert row['before'] == int(native[0][key]['measured_nVehContrib'])
        assert row['after'] == int(native[1][key]['measured_nVehContrib'])
    z5 = [r for r in plot['rows'] if r['field'] == 'hh_0119_Z_5_30494']
    assert sum(abs(r['before']-r['observed']) for r in z5) == 137
    assert sum(abs(r['after']-r['observed']) for r in z5) == 335
    assert json.loads(raw('aerial-review/report.json'))['status'] == 'review_required'


def test_local_reproduction_rebuilds_identical_inputs_and_passes_five_vehicle_probe(tmp_path):
    import importlib.util
    import sumolib

    if not Path(sumolib.checkBinary('netconvert')).is_file():
        pytest.skip('The native SUMO runtime is unavailable.')
    spec = importlib.util.spec_from_file_location('primary_branch_frozen_replay', CASE/'reproduce.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    result = module.reproduce(tmp_path/'replay')
    assert result['candidate_hashes_identical'] and result['sparse_vehicles_arrived'] == 5
    assert not result['full_demand_run']
