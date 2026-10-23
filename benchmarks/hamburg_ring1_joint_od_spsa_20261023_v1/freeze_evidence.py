"""Audit a terminal joint-OD study and explicitly freeze it; never start SUMO."""
import argparse
import ast
from collections import Counter, defaultdict
import csv
import gzip
import hashlib
import io
import json
import math
from pathlib import Path
import re
import shutil
import xml.etree.ElementTree as ET

import numpy as np


ROOT = Path(__file__).resolve().parent
REPO = ROOT.parents[1]
TARGET = REPO / 'benchmarks/hamburg_ring1_joint_od_spsa_20261023_v1'
CACHE = REPO / 'outputs/hamburg-classical-od-calibration-20261023-v1/probe-minus1/evaluation'
MODEL_SHA = '256472ed0c48f759787e3b76dd58317a4efb0cc965ca27e45ede250cbe244c7f'
NATIVE = ('network.net.xml', 'demand.rou.xml', 'source-e1.add.xml', 'signal-programs.add.xml',
    'lane-intentions.json', 'expected-counts.csv', 'e1.add.xml', 'instant.add.xml', 'field-counts.xml',
    'field-comparison.csv', 'summary.xml', 'tripinfo.xml', 'vehicle-routes.xml', 'collisions.xml',
    'run.json', 'native-request.json', 'command.json', 'stall-monitor.json', 'sumo.log', 'sumo-errors.log')
GENERATED = ('demand.rou.xml', 'lane-intentions.json', 'od-vector.json', 'evaluation.json', 'station-comparison.csv')


def raw(path):
    path = Path(path)
    if path.is_file():
        value = path.read_bytes()
        return gzip.decompress(value) if path.suffix == '.gz' else value
    return gzip.decompress(path.with_name(path.name + '.gz').read_bytes())


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def raw_sha(path):
    return hashlib.sha256(raw(path)).hexdigest()


def load(path):
    return json.loads(raw(path))


def rows(path):
    return list(csv.DictReader(io.StringIO(raw(path).decode('utf-8-sig'))))


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')


def exists(path):
    return path.is_file() or path.with_name(path.name + '.gz').is_file()


def count_key(row):
    return row['detector_id'], float(row['begin']), float(row['end'])


def score(comparison):
    residuals = [int(r['diff_nVehContrib_minus_expected']) for r in comparison]
    return dict(SSE=sum(r*r for r in residuals), TAE=sum(map(abs, residuals)),
        MAE=sum(map(abs, residuals)) / len(residuals), max_abs_error=max(map(abs, residuals)))


def equal(actual, expected, label):
    a, b = np.asarray(actual, dtype=float), np.asarray(expected, dtype=float)
    assert a.shape == b.shape and np.all(np.isfinite(a)) and np.all(np.isfinite(b)), label
    assert np.allclose(a, b, rtol=1e-12, atol=1e-12), label


def station_definition(execution):
    tree = ast.parse(raw(execution / 'implementation/astra_sumo/core/hamburg_readme_results.py').decode('utf-8'))
    return ast.literal_eval(next(n.value for n in tree.body if isinstance(n, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == 'STATIONS' for t in n.targets)))


def audit_measurement(prepared, proposal, evidence, model, stations):
    """Recompute observations independently; evidence may be the actual reused cache."""
    native = evidence / 'native'
    run, monitor, request = [load(native / n) for n in ('run.json', 'stall-monitor.json', 'native-request.json')]
    assert run['native_status'] == load(native / 'command.json')['status'] == 'pass'
    assert run['observer_complete'] and run['missing_field_bins'] == 0 and run['inputs_unchanged']
    assert monitor['status'] == 'completed' and monitor['simulation_time_s'] == 14400
    assert (run['seed'], run['step_length_s'], run['end_s'], run['backend']) == (104, .1, 14400, 'libsumo')
    assert request['backend'] == monitor['backend'] == 'libsumo' and monitor['library_completed']
    assert request['junction_body_hold'] and request['monitor_interval_s'] is None and request['all_stopped_timeout_s'] == 300
    assert monitor['native_version'] == [22, 'SUMO 1.27.1'] and monitor['command'] == request['command']
    command = request['command']
    for flag, value in (('--seed', '104'), ('--step-length', '0.1'), ('--end', '14400'), ('--time-to-teleport', '-1'), ('--collision.check-junctions', 'true')):
        assert command[command.index(flag) + 1] == value
    assert monitor['junction_body_hold_nodes'] == run['junction_body_hold_nodes'] == load(prepared / 'scenario.json')['junction_body_hold_nodes']
    aliases = {'network.net.xml': prepared / 'network.net.xml', 'demand.rou.xml': proposal / 'demand.rou.xml',
        'source-e1.add.xml': prepared / 'field-loops.add.xml', 'signal-programs.add.xml': prepared / 'control.add.xml',
        'expected-counts.csv': prepared / 'expected-counts.csv', 'lane-intentions.json': proposal / 'lane-intentions.json'}
    assert len(run['inputs']) == len(run['snapshots']) == 6
    for original, snapshot in zip(run['inputs'], run['snapshots']):
        name = snapshot['path'].replace('\\', '/').rsplit('/', 1)[-1]
        assert raw_sha(aliases[name]) == original['sha256'] == snapshot['sha256'] == raw_sha(native / name)
    vector = load(proposal / 'od-vector.json')
    values = np.asarray(vector['values'], dtype=float)
    assert values.shape == (294,) and np.all(np.isfinite(values)) and np.all(values >= 0)
    integer = [math.floor(float(v) + .5) for v in values]
    assert vector['integer_counts'] == integer and vector['vehicle_count'] == sum(integer)
    assert vector['source_model_sha256'] == raw_sha(prepared / 'od-model.json') == MODEL_SHA
    for name in ('demand.rou.xml', 'lane-intentions.json'):
        assert raw(proposal / name) == raw(evidence / name) == raw(native / name)
    demand_root = ET.fromstring(raw(proposal / 'demand.rou.xml'))
    assert demand_root.tag == 'routes' and not demand_root.attrib
    assert [ET.canonicalize(ET.tostring(r, encoding='unicode'), strip_text=True) for r in demand_root if r.tag != 'vehicle'] == [
        ET.canonicalize(text, strip_text=True) for text in model['type_definitions']]
    demand = demand_root.findall('vehicle')
    vehicles = {v.get('id'): v for v in demand}
    intentions = load(proposal / 'lane-intentions.json')['vehicles']
    fields = {r['vehicle_id']: r for r in intentions}
    assert len(demand) == len(vehicles) == len(fields) == len(intentions) == sum(integer)
    actual_cells = Counter(int(identity.split('_')[1]) for identity in vehicles)
    assert [actual_cells[i] for i in range(294)] == integer
    for column, (cell, count) in enumerate(zip(model['cells'], integer)):
        templates = cell['templates']
        positions = (np.arange(len(templates)) + .5) / len(templates)
        for index in range(count):
            identity = f'od_{column:04d}_v{index:05d}'
            quantile = (index + .5) / count
            template = templates[min(len(templates)-1, int(quantile * len(templates)))]
            source, actual = ET.fromstring(template['vehicle_xml']), vehicles[identity]
            depart = float(np.interp(quantile, positions, [r['prior_depart_s'] for r in templates]))
            source.set('id', identity)
            source.set('depart', f'{depart:.12f}')
            assert ET.canonicalize(ET.tostring(source, encoding='unicode'), strip_text=True) == ET.canonicalize(ET.tostring(actual, encoding='unicode'), strip_text=True)
            assert fields[identity]['field_id'] == template['prior_field_intention'] and fields[identity]['od_cell'] == column
    counts = {}
    for row in ET.fromstring(raw(native / 'field-counts.xml')).findall('interval'):
        key = row.get('id'), float(row.get('begin')), float(row.get('end'))
        if key[1] == key[2]:
            continue
        numeric = float(row.get('nVehContrib'))
        assert key not in counts and numeric >= 0 and numeric.is_integer()
        counts[key] = int(numeric)
    targets = [rows(prepared / n) for n in ('expected-counts.csv', 'initialization-counts.csv')]
    assert list(map(len, targets)) == [130, 52]
    field_ids = {r['detector_id'] for r in targets[0]}
    assert len(field_ids) == 13 and set(counts) == {(f, float(t), float(t+900)) for f in field_ids for t in range(0, 14400, 900)}
    tables = []
    for expected in targets:
        assert len({count_key(r) for r in expected}) == len(expected)
        tables.append([dict(detector_id=r['detector_id'], begin=float(r['begin']), end=float(r['end']),
            expected_total=int(r['expected_total']), measured_nVehContrib=counts[count_key(r)],
            diff_nVehContrib_minus_expected=counts[count_key(r)] - int(r['expected_total'])) for r in expected])
    main, init = tables
    recorded_rows = rows(native / 'field-comparison.csv')
    recorded = {count_key(r): r for r in recorded_rows}
    assert len(recorded_rows) == len(recorded) == 130
    for row in main:
        assert all(int(recorded[count_key(row)][k]) == row[k] for k in ('expected_total', 'measured_nVehContrib', 'diff_nVehContrib_minus_expected'))
    grouped = defaultdict(lambda: [0, 0, 0])
    for target in targets[0]:
        for station, (node, assets) in stations.items():
            if target['node_id'] == node and target['asset_id'] in assets:
                group = grouped[station, float(target['begin']), float(target['end'])]
                group[0] += int(target['expected_total'])
                group[1] += counts[count_key(target)]
                group[2] += 1
    station_rows = rows(evidence / 'station-comparison.csv')
    assert len(station_rows) == len(grouped) == 40
    assert {(r['station_stream_id'], float(r['begin']), float(r['end'])) for r in station_rows} == grouped.keys()
    for row in station_rows:
        key = row['station_stream_id'], float(row['begin']), float(row['end'])
        expected, measured, members = grouped[key]
        assert members == len(stations[key[0]][1])
        assert [int(row[k]) for k in ('expected_total', 'measured_nVehContrib', 'residual')] == [expected, measured, measured - expected]
        assert row['independent_evidence'] == 'False'
    summary = ET.fromstring(raw(native / 'summary.xml')).findall('step')
    assert len(summary) == 144000
    completion = {k: int(summary[-1].get(k)) for k in ('loaded', 'inserted', 'arrived', 'running', 'waiting', 'collisions', 'teleports', 'discarded')}
    assert completion == run['completion'] and completion['loaded'] == len(demand)
    trips = ET.fromstring(raw(native / 'tripinfo.xml')).findall('tripinfo')
    assert len(trips) == len({t.get('id') for t in trips}) == completion['inserted']
    assert {t.get('id') for t in trips} <= vehicles.keys()
    arrived = {t.get('id') for t in trips if float(t.get('arrival')) >= 0 and not t.get('vaporized', '')}
    assert len(arrived) == completion['arrived'] and completion['waiting'] == len(demand) - len(trips)
    assert sum(float(t.get('arrival')) < 0 and t.get('vaporized', '') in {'', 'end'} for t in trips) == completion['running']
    safe = not any(completion[k] for k in ('collisions', 'teleports', 'discarded'))
    routes = ET.fromstring(raw(native / 'vehicle-routes.xml')).findall('vehicle')
    route_ids = {r.get('id') for r in routes}
    assert len(routes) == len(route_ids) and arrived <= route_ids <= vehicles.keys()
    if safe:
        assert route_ids == arrived
    for vehicle in routes:
        route = vehicle.find('route')
        route_edges, times = route.get('edges').split(), list(map(float, route.get('exitTimes').split()))
        assert route_edges == vehicles[vehicle.get('id')].find('route').get('edges').split()
        assert len(route_edges) == len(times) and all(math.isfinite(t) and (t >= 0 or not safe and t == -1) for t in times)
        reached = [t for t in times if t >= 0]
        assert all(a <= b for a, b in zip(reached, reached[1:]))
    collision_rows = len(ET.fromstring(raw(native / 'collisions.xml')).findall('collision'))
    assert collision_rows == completion['collisions']
    healthy = safe and not completion['waiting'] and not completion['running'] and all(not t.get('vaporized', '') for t in trips)
    main_score, init_score, full_score = score(main), score(init), score(main + init)
    result = load(evidence / 'evaluation.json')
    assert result['loss'] == main_score['SSE'] and result['MAE'] == main_score['MAE']
    assert result['field_rows'] == 130 and result['derived_station_rows'] == 40 and result['count_source'] == 'Native E1 nVehContrib only'
    assert result['max_abs_error'] == main_score['max_abs_error'] and result['completion'] == completion
    assert result['healthy'] == run['healthy'] == healthy
    assert result['exact_count_fit'] == run['exact_count_fit'] == (healthy and main_score['SSE'] == 0)
    assert result['station_MAE'] == sum(abs(int(r['residual'])) for r in station_rows) / 40
    assert result['station_max_abs_error'] == max(abs(int(r['residual'])) for r in station_rows)
    return (dict(values=values.tolist(), integer_counts=integer, requested_vehicles=len(demand), main130=main_score,
        initial52=init_score, all182=full_score, completion=completion, safe=safe, healthy=healthy,
        exact_count_fit=healthy and main_score['SSE'] == 0, warning_lines=raw(native / 'sumo-errors.log').count(b'Warning:')),
        dict(main130=main, initial52=init, all182=main + init, station40=station_rows))


def check_update(row, theta, delta, settings, gain, losses):
    k = row['iteration']
    ck = settings['c'] / k ** settings['gamma']
    equal(row['theta'], theta, 'theta chain')
    assert np.array_equal(row['delta'], delta), 'Bernoulli RNG sequence'
    plus, minus = np.maximum(theta + ck*delta, 0), np.maximum(theta - ck*delta, 0)
    equal(row['plus'], plus, 'projected plus')
    equal(row['minus'], minus, 'projected minus')
    for actual, expected in ((row['plus'], plus), (row['minus'], minus)):
        assert [math.floor(v + .5) for v in actual] == [math.floor(v + .5) for v in expected], 'projected integer vector'
    assert (row['loss_plus'], row['loss_minus']) == tuple(losses), 'native SSE pair'
    gradient = (losses[0] - losses[1]) / (2 * ck * delta)
    if gain is None:
        assert np.max(np.abs(gradient)) > 0
        gain = settings['initial_step'] * (settings['A'] + 1) ** settings['alpha'] / np.max(np.abs(gradient))
    ak = gain / (settings['A'] + k) ** settings['alpha']
    following = np.maximum(theta - ak * gradient, 0)
    for actual, expected, name in ((row['ck'], ck, 'ck'), (row['a'], gain, 'a'), (row['ak'], ak, 'ak'),
        (row['gradient'], gradient, 'gradient'), (row['next_theta'], following, 'next_theta')):
        equal(actual, expected, name)
    assert [math.floor(v + .5) for v in row['next_theta']] == [math.floor(v + .5) for v in following]
    return float(gain), following


def audit_static_support(root):
    """Check the static support packet without treating it as native evidence."""
    directory = root / 'support-audit'
    manifest = load(directory / 'manifest.json')
    assert manifest['native_runs'] == 0 and manifest['raw_sources_immutable']
    assert len({row['path'] for row in manifest['files']}) == len(manifest['files'])
    for row in manifest['files']:
        relative = Path(row['path'])
        assert not relative.is_absolute() and '..' not in relative.parts
        value = raw(directory / relative)
        assert len(value) == row['size_bytes'] and hashlib.sha256(value).hexdigest() == row['sha256']
    return dict(status='pass', native_runs=0, files=len(manifest['files']),
        manifest_sha256=raw_sha(directory / 'manifest.json'), claim='Static model support and legal paths only; no native validation.')


def audit_study(root, *, initial_source=None, require_reviews=True):
    run, execution = root / 'run', root / 'run-execution'
    assert exists(run / 'result.json') or exists(run / 'failure.json'), 'Study has no terminal result/failure; do not freeze an active run.'
    settings, metadata = load(run / 'settings.json'), load(execution / 'execution.json')
    assert settings == load(root / 'settings.json')
    assert metadata['backend'] == 'libsumo' and metadata['parallel_pair']
    assert metadata['versions']['libsumo'] == '1.27.1' and set(metadata['versions'].values()) == {'1.27.1'}
    assert metadata['numpy_version'] == np.__version__
    for relative, expected in metadata['implementation_sha256'].items():
        assert raw_sha(execution / 'implementation' / Path(relative.replace('\\', '/'))) == expected
    prepared = root / 'prepared'
    assert raw_sha(prepared / 'od-model.json') == MODEL_SHA
    model, initial = load(prepared / 'od-model.json'), load(prepared / 'initial-od-vector.json')['values']
    assert len(model['cells']) == len(initial) == 294 and sum(initial) == 8640
    for name, expected in load(root / 'preparation.json')['prepared_hashes'].items():
        assert raw_sha(prepared / name) == expected
    declared = load(run / 'evaluations.json')
    assert len({r['name'] for r in declared}) == len(declared)
    stations = station_definition(execution)
    checked, tables = {}, {}
    for item in declared:
        name = item['name']
        assert re.fullmatch(r'initial|final|iteration-\d{4}-(plus|minus)', name)
        proposal = run / name
        evidence = initial_source if item.get('reused') else proposal
        assert evidence is not None, 'Supply the actual initial cache explicitly; historical absolute paths are not current paths.'
        report, data = audit_measurement(prepared, proposal, evidence, model, stations)
        assert item['loss'] == report['main130']['SSE'] and item['MAE'] == report['main130']['MAE']
        assert item['completion'] == report['completion'] and item['healthy'] == report['healthy']
        if item.get('reused'):
            proof = load(run / 'initial-reuse.json')
            assert proof['backend'] == 'libsumo' and report['values'] == initial
            for filename in ('demand.rou.xml', 'lane-intentions.json', 'od-vector.json'):
                assert raw(proposal / filename) == raw(evidence / filename)
        assert load(evidence / 'native/stall-monitor.json')['native_library']['sha256'] == metadata['libsumo_sha256']
        checked[name], tables[name] = report, data
    if any(item.get('reused') for item in declared):
        cache_execution = root / 'initial-cache-execution'
        if not cache_execution.is_dir():
            cache_execution = initial_source.parent
        old = load(cache_execution / 'execution.json')
        assert old['backend'] == 'libsumo' and old['libsumo_sha256'] == metadata['libsumo_sha256']
        for relative, expected in old['implementation_sha256'].items():
            assert raw_sha(cache_execution / 'implementation' / Path(relative.replace('\\', '/'))) == expected
    result = load(run / 'result.json') if exists(run / 'result.json') else None
    history = [load(run / f'iteration-{k:04d}.json') for k in range(1, settings['iterations'] + 1)
               if exists(run / f'iteration-{k:04d}.json')]
    assert [r['iteration'] for r in history] == list(range(1, len(history) + 1))
    theta, rng, gain = np.asarray(initial, dtype=float), np.random.default_rng(settings['seed']), settings['a']
    for row in history:
        k = row['iteration']
        plus, minus = checked[f'iteration-{k:04d}-plus'], checked[f'iteration-{k:04d}-minus']
        assert plus['safe'] and minus['safe'], 'Unsafe pair cannot produce an update.'
        equal(plus['values'], row['plus'], 'native plus vector')
        equal(minus['values'], row['minus'], 'native minus vector')
        gain, theta = check_update(row, theta, rng.choice((-1, 1), size=294), settings, gain,
                                  (plus['main130']['SSE'], minus['main130']['SSE']))
    next_k = len(history) + 1
    remaining = [name for name in checked if name.startswith(f'iteration-{next_k:04d}-')]
    if remaining:
        assert next_k <= settings['iterations']
        delta, ck = rng.choice((-1, 1), size=294), settings['c'] / next_k ** settings['gamma']
        for name in remaining:
            sign = 1 if name.endswith('-plus') else -1
            equal(checked[name]['values'], np.maximum(theta + sign*ck*delta, 0), 'unupdated pair vector')
    if result:
        assert result['stop_on_unsafe'] is True
        assert result['history'] == history and result['completed_iterations'] == len(history)
        assert result['evaluations'] == declared and result['native_evaluations'] == len(declared)
        assert result['new_native_evaluations'] == sum(not r.get('reused', False) for r in declared)
        if 'final' in checked:
            equal(checked['final']['values'], theta, 'final measurement versus update endpoint')
            assert checked['final']['integer_counts'] == [math.floor(v + .5) for v in theta]
            assert checked['final']['values'] == history[-1]['next_theta'], 'final values must exactly equal the last persisted update'
        if result['stopping_reason'] == 'declared_iteration_count':
            assert len(history) == settings['iterations'] and 'final' in checked and result['final_vector_remeasured']
        if result['stopping_reason'] == 'unsafe_native_evaluation':
            assert not checked[result['selected_evaluation']]['safe'] and len(remaining) == 2
    allowed = {'initial', 'final'} | {f'iteration-{k:04d}-{side}' for k in range(1, min(next_k, settings['iterations']) + 1) for side in ('plus', 'minus')}
    assert set(checked) <= allowed
    attempts = [p.name for p in run.iterdir() if p.is_dir() and re.fullmatch(r'initial|final|iteration-\d{4}-(plus|minus)', p.name)
                and exists(p / 'native/native-request.json')]
    if require_reviews:
        support = audit_static_support(root)
        scans = [load(p) for p in (root / 'analysis').rglob('scan.json*')]
        assert any(s['status'] == 'pass' and {r['name'] for r in s['complete_evaluations']} == set(checked) for s in scans), 'A final complete analysis scan is required.'
        if history:
            audit = load(root / 'arithmetic-audit' / f'iteration-{len(history):04d}-audit.json')
            assert audit['status'] == 'pass' and len(audit['verified_prefix']) == len(history)
            assert audit['settings_sha256'] == raw_sha(run / 'settings.json')
            assert audit['execution_sha256'] == raw_sha(execution / 'execution.json')
            for prefix in audit['verified_prefix']:
                k = prefix['iteration']
                assert prefix['history_sha256'] == raw_sha(run / f'iteration-{k:04d}.json')
                for side in ('plus', 'minus'):
                    assert prefix['native_' + side]['SSE'] == checked[f'iteration-{k:04d}-{side}']['main130']['SSE']
        if 'final' in checked:
            final_audit = load(root / 'arithmetic-audit/final-audit.json')
            assert final_audit['status'] == 'pass' and final_audit['new_native_runs'] == 0
            assert final_audit['iteration_chain_complete'] == len(history)
            assert final_audit['actual_final_native_replay_present'] and final_audit['final_values_match_theta5'] and final_audit['final_integer_counts_match_audited_theta5']
            assert final_audit['theta5_history_sha256'] == raw_sha(run / f'iteration-{len(history):04d}.json')
            assert final_audit['iteration5_independent_audit_sha256'] == raw_sha(root / 'arithmetic-audit' / f'iteration-{len(history):04d}-audit.json')
            assert final_audit['source_model_sha256'] == MODEL_SHA and final_audit['expected_counts_sha256'] == raw_sha(prepared / 'expected-counts.csv')
            assert final_audit['requested_vehicles'] == checked['final']['requested_vehicles']
            assert final_audit['final_native_130_SSE'] == checked['final']['main130']['SSE']
            assert final_audit['final_native_130_TAE'] == checked['final']['main130']['TAE']
            assert final_audit['final_native_130_MAE'] == checked['final']['main130']['MAE']
            assert final_audit['completion'] == checked['final']['completion']
            for source in final_audit['final_source_hashes']:
                name = source['path'].replace('\\', '/').rsplit('/', 1)[-1]
                relative = name if name in GENERATED else 'native/' + name
                assert source['sha256'] == raw_sha(run / 'final' / relative)
    safe_names = [name for name, report in checked.items() if report['safe']]
    best_sse = min(safe_names, key=lambda n: (checked[n]['main130']['SSE'], checked[n]['main130']['TAE'], n)) if safe_names else None
    best_mae = min(safe_names, key=lambda n: (checked[n]['main130']['TAE'], checked[n]['main130']['SSE'], n)) if safe_names else None
    summary = dict(status='pass', formal_result=bool(result and result['formal_result']), evaluations=checked,
        completed_updates=len(history), new_native_evaluations=sum(not r.get('reused', False) for r in declared),
        native_attempts_with_request=len(attempts), unregistered_native_attempts=sorted(set(attempts) - set(checked)),
        initial_reused=bool(declared[0].get('reused')), declared_terminal_result=result,
        declared_failure=load(run / 'failure.json') if exists(run / 'failure.json') else None,
        update_endpoint=theta.tolist(), actual_final_measurement='final' in checked,
        best_safe_by_SSE=best_sse, best_safe_by_MAE=best_mae,
        selected_evaluation=result['selected_evaluation'] if result else None,
        any_healthy_native_zero=any(r['exact_count_fit'] for r in checked.values()))
    if require_reviews:
        summary['static_support_audit'] = support
    return summary, tables


def verify_manifest(case):
    manifest = load(case / 'manifest.json')
    actual = {p.relative_to(case).as_posix() for p in case.rglob('*') if p.is_file() and p.name != 'manifest.json' and '__pycache__' not in p.parts}
    assert actual == {r['path'] for r in manifest['files']}
    for row in manifest['files']:
        path = (case / row['path']).resolve(strict=True)
        assert path.is_relative_to(case) and path.stat().st_size == row['bytes'] and sha(path) == row['sha256']
    for row in load(case / 'source-provenance.json')['snapshots']:
        value = raw(case / row['snapshot'])
        assert len(value) == row['raw_bytes'] and hashlib.sha256(value).hexdigest() == row['raw_sha256']


def verify_frozen(case):
    verify_manifest(case)
    result, tables = audit_study(case, initial_source=case / 'initial-cache')
    assert result == load(case / 'results.json')
    for name, data in tables.items():
        for table, expected in data.items():
            actual = rows(case / 'count-checks' / name / (table + '.csv'))
            assert len(actual) == len(expected)
            if table == 'station40':
                assert actual == expected
            else:
                columns = ('expected_total', 'measured_nVehContrib', 'diff_nVehContrib_minus_expected')
                assert {count_key(r): tuple(int(r[k]) for k in columns) for r in actual} == {
                    count_key(r): tuple(int(r[k]) for k in columns) for r in expected}
        event = load(case / 'compact-passages' / (name + '.json'))
        assert event['reconciled'] and event['inputs_unchanged'] and len(event['interval_comparisons']) == 208
        observed = Counter((r['detector_id'], r['interval_begin_s'], r['interval_end_s']) for r in event['passages'])
        assert all(observed[r['detector_id'], r['begin_s'], r['end_s']] == r['periodic_nVehContrib'] for r in event['interval_comparisons'])
        evidence = case / 'initial-cache' if name == 'initial' and result['initial_reused'] else case / 'run' / name
        native_counts = {(r.get('id'), float(r.get('begin')), float(r.get('end'))): int(r.get('nVehContrib'))
            for r in ET.fromstring(raw(evidence / 'native/field-counts.xml')).findall('interval') if r.get('begin') != r.get('end')}
        assert {(r['detector_id'], r['begin_s'], r['end_s']): r['periodic_nVehContrib'] for r in event['interval_comparisons']} == native_counts
        assert raw_sha(evidence / 'native/field-counts.xml') in {r['sha256'] for r in event['inputs']}
        local = load(case / 'local-only-large-traces.json')['files']
        instant_hash = next(r['sha256'] for r in local if r['evaluation'] == name and r['path'].replace('\\', '/').endswith('/instant-e1.xml'))
        assert instant_hash in {r['sha256'] for r in event['inputs']}
    print('Frozen hashes, raw observations, 130/40/52/182, completion and SPSA arithmetic: pass', flush=True)


def freeze():
    summary, tables = audit_study(ROOT, initial_source=CACHE)
    assert not TARGET.exists(), 'Preserve existing evidence; use the fixed new benchmark once.'
    TARGET.mkdir(parents=True)
    provenance, local = [], []

    def copy(path, relative):
        value = path.read_bytes()
        target = TARGET / relative
        if path.suffix != '.gz':
            target = target.with_name(target.name + '.gz')
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(gzip.compress(value, mtime=0))
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(value)
        decoded = gzip.decompress(value) if path.suffix == '.gz' else value
        provenance.append(dict(source=str(path), snapshot=target.relative_to(TARGET).as_posix(),
            raw_bytes=len(decoded), raw_sha256=hashlib.sha256(decoded).hexdigest()))

    for directory in ('prepared', 'analysis', 'arithmetic-audit', 'initial-proof', 'initial-reuse-check', 'run-execution', 'support-audit'):
        for path in sorted((ROOT / directory).rglob('*')):
            if path.is_file() and '__pycache__' not in path.parts:
                copy(path, path.relative_to(ROOT))
    for path in sorted(ROOT.iterdir()):
        if path.is_file() and path.name not in {'freeze_evidence.py', 'reproduce.py', 'test_freeze_evidence.py'}:
            copy(path, Path(path.name))
    for path in sorted((ROOT / 'run').rglob('*')):
        if not path.is_file() or '__pycache__' in path.parts:
            continue
        if path.name in ('instant-e1.xml', 'queues.xml'):
            local.append(dict(evaluation=path.relative_to(ROOT / 'run').parts[0], path=str(path), bytes=path.stat().st_size, sha256=sha(path)))
        else:
            copy(path, path.relative_to(ROOT))
    if summary['initial_reused']:
        for name in GENERATED:
            copy(CACHE / name, Path('initial-cache') / name)
        for name in NATIVE:
            copy(CACHE / 'native' / name, Path('initial-cache/native') / name)
        for path in sorted((CACHE.parent / 'implementation').rglob('*.py')):
            if '__pycache__' not in path.parts:
                copy(path, Path('initial-cache-execution/implementation') / path.relative_to(CACHE.parent / 'implementation'))
        copy(CACHE.parent / 'execution.json', Path('initial-cache-execution/execution.json'))
        for name in ('instant-e1.xml', 'queues.xml'):
            path = CACHE / 'native' / name
            local.append(dict(evaluation='initial', path=str(path), bytes=path.stat().st_size, sha256=sha(path), initial_reused=True))
    for name, data in tables.items():
        for label, table in data.items():
            stream = io.StringIO(newline='')
            writer = csv.DictWriter(stream, fieldnames=list(table[0]))
            writer.writeheader()
            writer.writerows(table)
            target = TARGET / 'count-checks' / name / (label + '.csv.gz')
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(gzip.compress(stream.getvalue().encode('utf-8'), mtime=0))
    from astra_sumo.core import route_sensor_matrix
    metadata = load(ROOT / 'run-execution/execution.json')
    frozen_hashes = {key.replace('\\', '/'): value for key, value in metadata['implementation_sha256'].items()}
    assert sha(Path(route_sensor_matrix.__file__)) == frozen_hashes['astra_sumo/core/route_sensor_matrix.py']
    for name in summary['evaluations']:
        evidence = CACHE if name == 'initial' and summary['initial_reused'] else ROOT / 'run' / name
        native = evidence / 'native'
        event = route_sensor_matrix.read_contributing_e1_passages(instant_file=native / 'instant-e1.xml', periodic_file=native / 'field-counts.xml',
            detector_ids=load(native / 'run.json')['instant_to_periodic_ids'], step_length=.1)
        assert event['reconciled'] and event['inputs_unchanged']
        event['source_context'] = dict(new_native_run=False, offline_derivation=True,
            helper_sha256=frozen_hashes['astra_sumo/core/route_sensor_matrix.py'],
            od_vector_sha256=raw_sha(ROOT / 'run' / name / 'od-vector.json'), source_model_sha256=MODEL_SHA)
        target = TARGET / 'compact-passages' / (name + '.json.gz')
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(gzip.compress(json.dumps(event, separators=(',', ':')).encode('utf-8'), mtime=0))
    for name in ('freeze_evidence.py', 'reproduce.py', 'test_freeze_evidence.py'):
        shutil.copyfile(ROOT / name, TARGET / name)
    (TARGET / '.gitattributes').write_text('* -text whitespace=blank-at-eol,blank-at-eof,space-before-tab,cr-at-eol\n*.gz binary\n', encoding='utf-8')
    save(TARGET / 'source-provenance.json', dict(snapshots=provenance))
    save(TARGET / 'local-only-large-traces.json', dict(files=local))
    save(TARGET / 'initial-source.json', dict(actual_initial_cache_raw_copied=summary['initial_reused'],
        frozen_evidence_slot='initial-cache', new_native_run=False,
        historical_source=str(CACHE), historical_paths_are_provenance_only=True))
    save(TARGET / 'results.json', summary)
    lines = ['# 汉堡294变量联合OD SPSA诊断证据', '',
        '这是一次固定模型、路网和控制的诊断试验，不是完成标定的正式结果。',
        f"实际完成实数更新：{summary['completed_updates']}。新增完整原生测量：{summary['new_native_evaluations']}；有原生请求的尝试：{summary['native_attempts_with_request']}。",
        f"算法终点实际复测：{summary['actual_final_measurement']}。初值复用历史实测：{summary['initial_reused']}。",
        f"按SSE最低的安全实测端点：{summary['best_safe_by_SSE']}；按MAE最低的安全端点：{summary['best_safe_by_MAE']}。",
        f"程序终止选择：{summary['selected_evaluation']}。观察到健康原生全零：{summary['any_healthy_native_zero']}。",
        '主130项零误差目标尚未达到。' if not summary['any_healthy_native_zero'] else '已有健康原生全零观测，仍不代表真实OD或现场机制已识别。',
        '', '程序完成不等于标定完成。算法更新终点、最终复测和最好扰动端点分别保留，不能互相替代。',
        '原固定OD参考 initial 仍保留：SSE130=6898，MAE130=5.0923076923。本轮11次新增原生测量均未优于该参考。',
        '最终向量有独立原生复测：SSE130=9692，MAE130=6.2，最大绝对误差29；未入网45辆、在途19辆，healthy=false。最终复测是新增测量中误差最低的一次，但不替换原参考。',
        '保留主窗全部130项真实计数、52项初始化计数和40项派生站点求和，没有删除难字段。',
        '参数固定为K=5、A=0、c=1、alpha=0.602、gamma=0.101、seed=104。首对损失差D=-2500，按目标初始步长1确定a=0.0008。原生步长0.1秒、终点14400秒，使用libsumo 1.27.1；每对评价并行执行，各自使用独立进程。',
        '模型原字节固定；联合分位数采样与整数化规则不变。不同数量仍会改变具体路线、车道、时刻份额和总车辆数。',
        '完成一次实数更新不保证整数车辆需求改变；离线中心等价证明按原样保留，不能据此省掉计划的最终原生复测。',
        '第3、4轮均更新了实数向量，但整数车辆需求没有改变；两轮中心的需求XML和车道意图与第2轮中心逐字节相同。',
        'initial-cache单列真实历史原生证据，不是新制造的initial/native。原始记录中的绝对路径只作历史来源，复跑不会把它们当当前路径。',
        '所有警告、unsafe/failure、独立分析和算术审核均保留。instant与queues留本地哈希清单，完整贡献身份已压缩保存。',
        'support-audit只核对固定模型的路线支持范围、源数据和合法连接；其中的静态路径存在性不证明原生通行、真实分流比例或现场几何。该审查新增原生运行数为0。',
        '', '| 实测 | SSE130 | MAE130 | TAE182 | 未入网 | 在途 | 碰撞 | 健康 |',
        '|---|---:|---:|---:|---:|---:|---:|---|']
    for name, report in summary['evaluations'].items():
        c = report['completion']
        lines.append(f"| {name} | {report['main130']['SSE']} | {report['main130']['MAE']:.6f} | {report['all182']['TAE']} | {c['waiting']} | {c['running']} | {c['collisions']} | {report['healthy']} |")
    lines += ['', '从仓库根目录离线复核：`.venv/Scripts/python.exe benchmarks/hamburg_ring1_joint_od_spsa_20261023_v1/freeze_evidence.py --verify-only benchmarks/hamburg_ring1_joint_od_spsa_20261023_v1`。',
        '显式原生复跑：`.venv/Scripts/python.exe benchmarks/hamburg_ring1_joint_od_spsa_20261023_v1/reproduce.py --output-dir outputs/hamburg-joint-od-reproduction`。',
        'reproduce.py只有显式调用才通过既有CLI复跑完整试验。不提供当前可验证的初值实测目录时，将重新测量初值，额外增加1次原生运行。',
        '冻结不会自动启动仿真。该试验不能证明唯一真实OD、现场SPaT、收敛或真实世界预测精度。',
        'OSM数据：© OpenStreetMap contributors，ODbL 1.0。']
    (TARGET / 'README.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')
    assert all(raw_sha(Path(row['source'])) == row['raw_sha256'] for row in provenance), 'A source changed during freezing; preserve this failed candidate.'
    save(TARGET / 'manifest.json', dict(formal_result=summary['formal_result'], files=[
        dict(path=p.relative_to(TARGET).as_posix(), bytes=p.stat().st_size, sha256=sha(p))
        for p in sorted(TARGET.rglob('*')) if p.is_file() and p.name != 'manifest.json']))
    verify_frozen(TARGET)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument('--check-only', action='store_true')
    action.add_argument('--freeze', action='store_true')
    action.add_argument('--verify-only', type=Path)
    args = parser.parse_args()
    if args.verify_only:
        verify_frozen(args.verify_only.resolve(strict=True))
    elif args.check_only:
        result, _ = audit_study(ROOT, initial_source=CACHE)
        print(json.dumps(result, indent=2))
    else:
        freeze()


if __name__ == '__main__':
    main()
