"""Freeze two terminal explicit substitutions; never start a native simulation."""
import argparse
import ast
from collections import Counter, defaultdict
import gzip
import hashlib
import importlib.util
import json
from pathlib import Path
import xml.etree.ElementTree as ET


HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
SOURCE = REPO / 'outputs/hamburg-multifield-substitution-20261023-v1'
CLASSIC = REPO / 'benchmarks/hamburg_ring1_classical_od_response_20261023_v1'
spec = importlib.util.spec_from_file_location('classical_counts', CLASSIC / 'freeze_evidence.py')
helper = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helper)
raw, load, rows = helper.raw, helper.read_json, helper.rows
sha, raw_sha = helper.digest, helper.sha_raw
RUNS = ('substitute-1pair', 'substitute-20pair')


def basename(path):
    return str(path).replace('\\', '/').rsplit('/', 1)[-1]


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')


def check_scope(before, prepared, preflight, pairs, witnesses):
    """Only selected tails/second waypoints and the paired removals may change."""
    old_root, new_root = [ET.fromstring(raw(p / 'demand.rou.xml')) for p in (before, prepared)]
    old, new = [{v.get('id'): v for v in tree.findall('vehicle')} for tree in (old_root, new_root)]
    kept, removed = [{p[key] for p in pairs} for key in ('retained_vehicle_id', 'removed_vehicle_id')]
    assert len(kept) == len(removed) == len(pairs) and not kept & removed
    assert len(old) == 8640 and new.keys() == old.keys() - removed and len(new) == 8640 - len(pairs)
    assert [v.get('id') for v in new_root.findall('vehicle')] == [v.get('id') for v in old_root.findall('vehicle') if v.get('id') not in removed]
    assert [ET.tostring(v) for v in old_root if v.tag != 'vehicle'] == [ET.tostring(v) for v in new_root if v.tag != 'vehicle']
    old_intentions, intentions = [load(p / 'lane-intentions.json') for p in (before, prepared)]
    assert intentions == {**old_intentions, 'vehicles': [v for v in old_intentions['vehicles'] if v['vehicle_id'] not in removed]}
    assigned = {v['vehicle_id']: v['field_id'] for v in old_intentions['vehicles']}
    assert assigned.keys() == old.keys()
    signatures = {v: [f] for v, f in assigned.items() if v not in removed}
    for pair in pairs:
        keep, drop = pair['retained_vehicle_id'], pair['removed_vehicle_id']
        witness = witnesses[pair['witness_id']]
        assert witness['junction_audit']['allowed'] and not witness['uturn_connections']
        assert pair['expected_fields'] == [assigned[keep], assigned[drop]] == witness['fields']
        assert old[keep].attrib == new[keep].attrib
        assert int(float(old[keep].get('depart')) // 900) == pair['retained_departure_bin'] == pair['removed_departure_bin'] == int(float(old[drop].get('depart')) // 900)
        previous = old[keep].find('route').get('edges').split()
        route = new[keep].find('route').get('edges').split()
        assert route == witness['edges'] == pair['replacement_route_edges'] and len(route) == len(set(route))
        prefix = pair['common_prefix_edge_count']
        assert previous[:prefix] == route[:prefix] and previous[prefix-1] == '186821034#0'
        restored = ET.fromstring(ET.tostring(new[keep]))
        assert len(old[keep].findall('stop')) == 1 and len(restored.findall('stop')) == 2
        extra = restored.findall('stop')[-1]
        assert extra.attrib == dict(lane='186821034#0_5', endPos='15.120000', speed='13.888889')
        restored.remove(extra)
        restored.find('route').set('edges', old[keep].find('route').get('edges'))
        assert ET.tostring(restored) == ET.tostring(old[keep])
        signatures[keep] = pair['expected_fields']
    assert all(ET.tostring(new[v]) == ET.tostring(old[v]) for v in new.keys() - kept)
    before_inventory = Counter((int(float(old[v].get('depart')) // 900), f) for v, f in assigned.items())
    after_inventory = Counter((int(float(new[v].get('depart')) // 900), f) for v, fields in signatures.items() for f in fields)
    assert before_inventory == after_inventory and sum(after_inventory.values()) == 8640
    assert signatures == preflight['signatures'] and preflight['pairs'] == pairs
    assert preflight['candidate_vehicle_count'] == len(new) and preflight['source_vehicle_count'] == 8640
    return signatures


def audit_measurement(prepared, evaluation, signatures):
    native = evaluation / 'native'
    counts = helper.periodic_counts(native / 'field-counts.xml')
    main, initial = [rows(prepared / n) for n in ('expected-counts.csv', 'initialization-counts.csv')]
    assert len(counts) == 208 and len(main) == 130 and len(initial) == 52
    fields = {r['detector_id'] for r in main}
    assert len(fields) == 13 and set(counts) == {(f, float(t), float(t+900)) for f in fields for t in range(0, 14400, 900)}
    main_score = helper.check_comparisons(rows(native / 'field-comparison.csv'), main, counts)
    full = rows(evaluation / 'all182-comparison.csv')
    full_score = helper.check_comparisons(full, initial + main, counts)
    initial_keys = {helper.key(r) for r in initial}
    initial_score = helper.check_comparisons([r for r in full if helper.key(r) in initial_keys], initial, counts)
    definition = ast.parse(raw(HERE / 'code-source/hamburg_readme_results.py').decode('utf-8'))
    stations = ast.literal_eval(next(n.value for n in definition.body if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'STATIONS' for t in n.targets)))
    groups = defaultdict(lambda: [0, 0, 0])
    for r in main:
        for station, (node, assets) in stations.items():
            if r['node_id'] == node and r['asset_id'] in assets:
                g = groups[station, float(r['begin']), float(r['end'])]
                g[0] += int(r['expected_total'])
                g[1] += counts[helper.key(r)]
                g[2] += 1
    station_rows = rows(evaluation / 'station-comparison.csv')
    assert len(station_rows) == len(groups) == 40
    assert {(r['station_stream_id'], float(r['begin']), float(r['end'])) for r in station_rows} == groups.keys()
    for r in station_rows:
        expected, actual, members = groups[r['station_stream_id'], float(r['begin']), float(r['end'])]
        assert [int(r[k]) for k in ('expected_total', 'measured_nVehContrib', 'residual')] == [expected, actual, actual-expected]
        assert members == len(stations[r['station_stream_id']][1]) and r['independent_evidence'] == 'False'
    run, monitor, request = [load(native / n) for n in ('run.json', 'stall-monitor.json', 'native-request.json')]
    assert run['native_status'] == load(native / 'command.json')['status'] == 'pass'
    assert run['observer_complete'] and run['inputs_unchanged'] and run['missing_field_bins'] == 0
    assert monitor['status'] == 'completed' and monitor['simulation_time_s'] == 14400 and monitor['library_completed']
    assert (run['seed'], run['step_length_s'], run['end_s'], run['backend']) == (104, .1, 14400, 'libsumo')
    assert monitor['backend'] == request['backend'] == 'libsumo' and monitor['command'] == request['command']
    assert run['junction_body_hold_nodes'] == request['junction_body_hold_nodes'] == load(prepared / 'scenario.json')['junction_body_hold_nodes']
    aliases = {'network.net.xml': 'network.net.xml', 'demand.rou.xml': 'demand.rou.xml', 'source-e1.add.xml': 'field-loops.add.xml',
        'signal-programs.add.xml': 'control.add.xml', 'expected-counts.csv': 'expected-counts.csv', 'lane-intentions.json': 'lane-intentions.json'}
    assert len(run['inputs']) == len(run['snapshots']) == 6
    for source, snapshot in zip(run['inputs'], run['snapshots']):
        name = basename(snapshot['path'])
        assert raw_sha(native / name) == raw_sha(prepared / aliases[name]) == source['sha256'] == snapshot['sha256']
    planned = {v.get('id'): v for v in ET.fromstring(raw(prepared / 'demand.rou.xml')).findall('vehicle')}
    assert len(planned) == len(signatures) and planned.keys() == signatures.keys()
    for n in ('demand.rou.xml', 'lane-intentions.json'):
        assert raw(prepared / n) == raw(evaluation / n) == raw(native / n)
    summary = ET.fromstring(raw(native / 'summary.xml')).findall('step')
    assert len(summary) == 144000
    completion = {k: int(summary[-1].get(k)) for k in ('loaded', 'inserted', 'arrived', 'waiting', 'running', 'collisions', 'teleports', 'discarded')}
    trips = ET.fromstring(raw(native / 'tripinfo.xml')).findall('tripinfo')
    assert completion['loaded'] == len(planned) and completion['inserted'] == len(trips) == len({v.get('id') for v in trips})
    assert {v.get('id') for v in trips} <= planned.keys()
    arrived = {v.get('id') for v in trips if float(v.get('arrival')) >= 0 and not v.get('vaporized', '')}
    assert completion['arrived'] == len(arrived) and completion['waiting'] == len(planned) - len(trips)
    assert completion['running'] == sum(float(v.get('arrival')) < 0 and v.get('vaporized', '') in {'', 'end'} for v in trips)
    assert len(ET.fromstring(raw(native / 'collisions.xml')).findall('collision')) == completion['collisions']
    safe = not any(completion[k] for k in ('collisions', 'teleports', 'discarded'))
    routes = ET.fromstring(raw(native / 'vehicle-routes.xml')).findall('vehicle')
    assert len(routes) == len({v.get('id') for v in routes}) and {v.get('id') for v in routes} <= planned.keys()
    if safe:
        assert {v.get('id') for v in routes} == arrived
    for v in routes:
        route = v.find('route')
        assert route.get('edges') == planned[v.get('id')].find('route').get('edges')
        assert len(route.get('exitTimes').split()) == len(route.get('edges').split())
    healthy = safe and not completion['waiting'] and not completion['running'] and all(not v.get('vaporized', '') for v in trips)
    measured = load(evaluation / 'evaluation.json')
    assert measured['loss'] == main_score['SSE'] and measured['MAE'] == main_score['MAE'] and measured['max_abs_error'] == main_score['max_abs_error']
    assert measured['completion'] == run['completion'] == completion and measured['healthy'] == run['healthy'] == healthy
    assert measured['exact_count_fit'] == run['exact_count_fit'] == (healthy and main_score['SSE'] == 0)
    assert measured['field_rows'] == 130 and measured['derived_station_rows'] == 40 and measured['count_source'] == 'Native E1 nVehContrib only'
    station_mae = sum(abs(int(r['residual'])) for r in station_rows)/40
    assert measured['station_MAE'] == station_mae
    compact = json.loads(gzip.decompress(raw(evaluation / 'contributing-passages.json.gz')))
    assert compact['reconciled'] and compact['inputs_unchanged'] and len(compact['interval_comparisons']) == 208
    events = compact['passages']
    assert Counter((r['detector_id'], r['interval_begin_s'], r['interval_end_s']) for r in events) == Counter(counts)
    assert raw_sha(native / 'field-counts.xml') in {r['sha256'] for r in compact['inputs']}
    assert {r['vehicle_id'] for r in events} <= signatures.keys()
    actual = defaultdict(list)
    for event in sorted(events, key=lambda r: r['time_s']):
        actual[event['vehicle_id']].append(event['detector_id'])
    unexpected = [r for r in events if r['detector_id'] not in signatures[r['vehicle_id']]]
    repeated = [list(k) for k, n in Counter((r['vehicle_id'], r['detector_id']) for r in events).items() if n > 1]
    return dict(main130=main_score, initial52=initial_score, all182=full_score, station40_MAE=station_mae,
        completion=completion, healthy=healthy, safe=safe, event_count=len(events), unexpected_contributions=len(unexpected),
        repeated_vehicle_fields=len(repeated), warning_lines=raw(native / 'sumo-errors.log').count(b'Warning:')), actual, unexpected, repeated


def audit_run(case, baseline, plan, witnesses):
    assert (case / 'verdict.json').exists() or (case / 'verdict.json.gz').exists(), 'Wait for the terminal verdict; do not read active native output.'
    prepared, evaluation = case / 'prepared', case / 'evaluation'
    preflight, verdict = load(case / 'preflight.json'), load(case / 'verdict.json')
    count = 1 if case.name == RUNS[0] else 20
    signatures = check_scope(baseline / 'evaluation', prepared, preflight, plan['pairs'][:count], witnesses)
    for n, expected in preflight['input_hashes'].items():
        assert raw_sha(prepared / n) == expected
    for n in ('network.net.xml', 'control.add.xml', 'field-loops.add.xml', 'expected-counts.csv', 'initialization-counts.csv'):
        assert raw(prepared / n) == raw(baseline / 'prepared' / n)
    report, actual, unexpected, repeated = audit_measurement(prepared, evaluation, signatures)
    assert verdict['before'] == load(baseline / 'evaluation/evaluation.json') and verdict['after'] == load(evaluation / 'evaluation.json')
    assert verdict['source_inputs_unchanged'] and not verdict['formal_result']
    assert verdict['all182_TAE'] == report['all182']['TAE'] and verdict['unexpected_contributions'] == unexpected and verdict['repeated_vehicle_fields'] == repeated
    retained = {p['retained_vehicle_id']: actual[p['retained_vehicle_id']] for p in preflight['pairs']}
    assert verdict['substituted_actual_signatures'] == retained
    dual = all(actual[v] == signatures[v] for v in retained)
    assert verdict['dual_contributions_complete'] == dual
    before = helper.periodic_counts(baseline / 'native/field-counts.xml')
    after = helper.periodic_counts(evaluation / 'native/field-counts.xml')
    original = {v.get('id'): v for v in ET.fromstring(raw(baseline / 'evaluation/demand.rou.xml')).findall('vehicle')}
    ids = [p[k] for p in preflight['pairs'] for k in ('retained_vehicle_id', 'removed_vehicle_id')]
    first = min(float(original[v].get('depart')) for v in ids)
    prefix = [helper.key(r) for r in rows(prepared / 'initialization-counts.csv') + rows(prepared / 'expected-counts.csv') if float(r['end']) <= first]
    assert len(prefix) == verdict['unchanged_prefix_rows'] and all(before[k] == after[k] for k in prefix)
    b = verdict['before']
    accepted = report['safe'] and not unexpected and not repeated and dual and report['main130']['SSE'] < b['loss'] and report['main130']['MAE'] < b['MAE'] and report['completion']['waiting'] <= 7 and report['completion']['waiting'] + report['completion']['running'] <= 21
    assert verdict['status'] == ('diagnostic_candidate' if accepted else 'rejected')
    return dict(**report, status=verdict['status'], substituted_pairs=count, dual_complete=dual, unchanged_prefix_rows=len(prefix))


def verify_manifest(case):
    manifest = load(case / 'manifest.json')
    actual = {p.relative_to(case).as_posix() for p in case.rglob('*') if p.is_file() and p != case / 'manifest.json' and '__pycache__' not in p.parts}
    assert actual == {r['path'] for r in manifest['files']}
    for row in manifest['files']:
        p = (case / row['path']).resolve(strict=True)
        assert p.is_relative_to(case) and p.stat().st_size == row['bytes'] and sha(p) == row['sha256']


def verify(case):
    verify_manifest(case)
    result = load(case / 'results.json')
    assert sha(CLASSIC / 'freeze_evidence.py') == result['classic_helper_sha256']
    assert sha(CLASSIC / 'manifest.json') == result['baseline_manifest_sha256']
    for row in load(case / 'source-provenance.json')['snapshots']:
        value = gzip.decompress((case / row['snapshot']).read_bytes())
        assert len(value) == row['raw_bytes'] and hashlib.sha256(value).hexdigest() == row['raw_sha256']
    plan = load(case / 'proposal/plan.json')
    witnesses = {r['id']: r for r in load(case / 'proposal/source-witnesses.json')}
    audit = load(case / 'response-audit/complete/scan.json')
    assert audit['status'] == 'pass' and audit['native_runs'] == 0 and not audit['skipped_without_verdict']
    assert {r['case'] for r in audit['complete_cases']} == set(RUNS)
    for name in RUNS:
        source = case / name
        report = audit_run(source, case / 'baseline', plan, witnesses)
        assert report == result['evaluations'][name]
        reviewed = load(case / 'response-audit/complete' / name / 'audit.json')
        assert reviewed['status'] == 'pass' and reviewed['raw_verdict'] == report['status']
        assert reviewed['metrics130']['SSE'] == report['main130']['SSE'] and reviewed['metrics182']['TAE'] == report['all182']['TAE']
        assert reviewed['completion'] == report['completion']
        for row in load(case / 'source-bindings.json')[name]:
            assert raw_sha(case / row['snapshot']) == row['sha256']
        compact = json.loads(gzip.decompress(raw(source / 'evaluation/contributing-passages.json.gz')))
        instant = next(r for r in load(case / 'local-only-large-traces.json')['files'] if r['case'] == name and basename(r['path']) == 'instant-e1.xml')
        assert instant['sha256'] in {r['sha256'] for r in compact['inputs']}
    print('Manifest, source bytes, input scope, native 208/130/40/52/182 and verdicts: pass', flush=True)


def freeze():
    assert not (HERE / 'manifest.json').exists(), 'Keep the frozen packet immutable.'
    for name in RUNS:
        assert (SOURCE / name / 'verdict.json').is_file(), 'Wait for both terminal verdicts.'
    response = load(SOURCE / 'response-audit/complete/scan.json')
    assert response['status'] == 'pass' and not response['skipped_without_verdict'] and len(response['complete_cases']) == 2
    for folder in ('proposal', 'response-audit'):
        manifest = load(SOURCE / folder / 'manifest.json')
        for row in manifest['files']:
            p = SOURCE / folder / row['path']
            assert sha(p) == row['sha256'] and p.stat().st_size == row.get('size_bytes', row.get('bytes'))
    provenance, local = [], []

    def copy(source, relative):
        data = source.read_bytes()
        target = HERE / relative
        if source.suffix == '.gz':
            decoded = gzip.decompress(data)
        else:
            decoded = data
            target = target.with_name(target.name + '.gz')
            data = gzip.compress(data, mtime=0)
        assert not target.exists(), 'Preserve partial/failed freezes for inspection.'
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        provenance.append(dict(source=str(source), snapshot=target.relative_to(HERE).as_posix(), raw_bytes=len(decoded), raw_sha256=hashlib.sha256(decoded).hexdigest()))

    for path in sorted((CLASSIC / 'probe-minus1').rglob('*.gz')):
        copy(path, Path('baseline') / path.relative_to(CLASSIC / 'probe-minus1'))
    for folder in ('proposal', 'response-audit', 'offline-substitute-1pair', 'offline-substitute-20pair'):
        for p in sorted((SOURCE / folder).rglob('*')):
            if p.is_file() and '__pycache__' not in p.parts:
                copy(p, p.relative_to(SOURCE))
    witness = REPO / 'outputs/hamburg-joint-od-spsa-20261023-v1/support-audit/multi-field-witnesses.json'
    copy(witness, Path('proposal/source-witnesses.json'))
    copy(REPO / 'plugins/astra-sumo/src/astra_sumo/core/hamburg_readme_results.py', Path('code-source/hamburg_readme_results.py'))
    for p in SOURCE.iterdir():
        if p.is_file():
            copy(p, Path(p.name))
    for name in RUNS:
        for p in sorted((SOURCE / name).rglob('*')):
            if not p.is_file() or '__pycache__' in p.parts:
                continue
            if p.name in ('instant-e1.xml', 'queues.xml'):
                local.append(dict(case=name, path=str(p), bytes=p.stat().st_size, sha256=sha(p)))
            else:
                copy(p, p.relative_to(SOURCE))
    bindings = {}
    plan = load(HERE / 'proposal/plan.json')
    witnesses = {r['id']: r for r in load(HERE / 'proposal/source-witnesses.json')}
    reports = {}
    for name in RUNS:
        preflight = load(HERE / name / 'preflight.json')
        mapping = {n: Path('baseline/prepared') / n for n in ('network.net.xml', 'control.add.xml', 'field-loops.add.xml', 'expected-counts.csv', 'initialization-counts.csv', 'scenario.json')}
        mapping.update({n: Path('baseline/evaluation') / n for n in ('demand.rou.xml', 'lane-intentions.json', 'od-vector.json', 'evaluation.json')})
        mapping.update({'plan.json': Path('proposal/plan.json'), 'multi-field-witnesses.json': Path('proposal/source-witnesses.json'), 'measure.py': Path('measure.py')})
        bindings[name] = [dict(historical_source=p, snapshot=mapping[basename(p)].as_posix(), sha256=h) for p, h in preflight['source_hashes'].items()]
        reports[name] = audit_run(HERE / name, HERE / 'baseline', plan, witnesses)
    save(HERE / 'source-bindings.json', bindings)
    save(HERE / 'source-provenance.json', dict(snapshots=provenance, historical_absolute_paths_are_provenance_only=True))
    save(HERE / 'local-only-large-traces.json', dict(files=local))
    save(HERE / 'results.json', dict(formal_result=False, new_native_evaluations=2, baseline_new_native_run=False,
        classic_helper_sha256=sha(CLASSIC / 'freeze_evidence.py'), baseline_manifest_sha256=sha(CLASSIC / 'manifest.json'), evaluations=reports))
    (HERE / '.gitattributes').write_text('* -text whitespace=blank-at-eol,blank-at-eof,space-before-tab,cr-at-eol\n*.gz binary\n', encoding='utf-8')
    text = ['# 汉堡多断面行程替换诊断', '',
        '原参考仍为selected probe-minus1：8640辆，130项MAE5.092308、SSE6898、TAE662；182项TAE744，未入网7辆、在途14辆。',
        '本轮只测试1对和20对显式行程替换：延长原119 Z12车辆的合法尾段，使其再经过535 Z4，并删除同发车时段的一辆独立535 Z4请求。保留车辆的发车时刻和属性不变，新增第二检测车道的原生moving-stop。',
        '道路、控制、全部130项主观测、52项初始化观测及40项派生站点求和保持不变。请求数分别为8639与8620；按发车时段的预期断面事件库存仍为8640，不等于8640个独立行程。',
        '这不是固定OD模型的数量更新，也不表示识别了真实分流、车辆对应或自然选道。', '',
        '| 方案 | 130 SSE | 130 MAE | 最大误差 | 182 TAE | 未入网 | 在途 | 碰撞/传送 | 判定 |',
        '|---|---:|---:|---:|---:|---:|---:|---:|---|']
    for name, r in reports.items():
        c, m = r['completion'], r['main130']
        text.append(f"| {name} | {m['SSE']} | {m['MAE']:.6f} | {m['max_abs_error']} | {r['all182']['TAE']} | {c['waiting']} | {c['running']} | {c['collisions']}/{c['teleports']} | {r['status']} |")
    text += ['', '两项原始判定均为rejected，原参考保留。实际双断面贡献与副作用见response-audit/complete；不因合法路径存在或全部替换车完成双断面贡献而认定标定成功。',
        '原生步长0.1秒、终点14400秒、seed104、libsumo 1.27.1，保留原4节点车身hold和原40感应/11固定控制。两项为独立原生运行。',
        '所有拒绝结果、原生警告、请求及输入快照均保留。prepared候选与完整contributing-passages.json.gz按原字节保存，未双重压缩。instant-e1与queues仅保留本地SHA-256清单。',
        '离线准备目录offline-substitute-*不含原生运行，不计入2次实测。历史绝对路径只作来源；复跑直接读取冻结候选，不重新生成21OD模型。',
        '主130项零误差目标尚未达到。计数、积压和未改车辆的连锁变化共同决定本次拒绝；有限试验不证明该方法普遍有效或无效。', '',
        '离线核验：`.venv/Scripts/python.exe benchmarks/hamburg_ring1_multifield_substitution_20261023_v1/freeze_evidence.py --verify-only`。',
        '显式原生复跑：`.venv/Scripts/python.exe benchmarks/hamburg_ring1_multifield_substitution_20261023_v1/reproduce.py --case substitute-1pair --output-dir outputs/multifield-reproduction`。',
        '只有显式调用reproduce.py才启动一次完整仿真。冻结与核验不启动仿真。',
        '计数核验复用同仓库经典OD包的冻结函数；其哈希及原基线包manifest哈希见results.json。OSM：© OpenStreetMap contributors，ODbL 1.0。']
    (HERE / 'README.md').write_text('\n'.join(text) + '\n', encoding='utf-8')
    assert all(raw_sha(Path(r['source'])) == r['raw_sha256'] if Path(r['source']).suffix != '.gz' else hashlib.sha256(gzip.decompress(Path(r['source']).read_bytes())).hexdigest() == r['raw_sha256'] for r in provenance), 'Source changed during freezing; preserve this failed packet.'
    save(HERE / 'manifest.json', dict(formal_result=False, files=[dict(path=p.relative_to(HERE).as_posix(), bytes=p.stat().st_size, sha256=sha(p))
        for p in sorted(HERE.rglob('*')) if p.is_file() and p != HERE / 'manifest.json' and '__pycache__' not in p.parts]))
    verify(HERE)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument('--freeze', action='store_true')
    action.add_argument('--verify-only', action='store_true')
    args = parser.parse_args()
    if args.freeze:
        freeze()
    else:
        verify(HERE)


if __name__ == '__main__':
    main()
