"""Freeze a completed observation replay and its sealed analyses; never run SUMO."""
import argparse
from decimal import Decimal
import gzip
import hashlib
import json
from pathlib import Path
import runpy
import xml.etree.ElementTree as ET


HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
SOURCE = REPO / 'outputs/hamburg-multifield-substitution-20261023-v1'
REFERENCE = REPO / 'benchmarks/hamburg_ring1_multifield_substitution_20261023_v1'
previous = runpy.run_path(str(REFERENCE / 'freeze_evidence.py'))
raw, load, sha, raw_sha = [previous[k] for k in ('raw', 'load', 'sha', 'raw_sha')]
NATIVE = REFERENCE / 'substitute-1pair/evaluation/native'
ANALYSES = ('trace-v2/independent-analysis', 'next-diagnosis', 'receiving-control-source-audit')


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def elements(path, tag):
    actual = path if path.is_file() else path.with_name(path.name + '.gz')
    with (gzip.open(actual, 'rb') if actual.suffix == '.gz' else actual.open('rb')) as stream:
        for _, row in ET.iterparse(stream, events=('end',)):
            if row.tag == tag:
                yield row
                row.clear()


def event_digest(path):
    digest, count = hashlib.sha256(), 0
    for row in elements(path, 'instantOut'):
        digest.update(json.dumps(row.attrib, sort_keys=True, separators=(',', ':')).encode('utf-8') + b'\n')
        count += 1
    return dict(events=count, attribute_sha256=digest.hexdigest())


def xml_semantics(path):
    actual = path if path.is_file() else path.with_name(path.name + '.gz')
    digest, count = hashlib.sha256(), 0
    with (gzip.open(actual, 'rb') if actual.suffix == '.gz' else actual.open('rb')) as stream:
        for event, row in ET.iterparse(stream, events=('start', 'end')):
            if event == 'start':
                digest.update(json.dumps([row.tag, row.attrib], sort_keys=True, separators=(',', ':')).encode('utf-8') + b'\n')
                count += 1
            else:
                row.clear()
    return dict(elements=count, attribute_sha256=digest.hexdigest())


def check_parity_gate(result):
    assert result['status'] == 'pass' and not result['formal_result']
    assert len(result['checks']) == 12 and all(result['checks'].values()), 'All twelve observer parity checks must pass.'
    assert [result[k] for k in ('closed_e1_rows', 'summary_steps_checked', 'instant_e1_events_checked', 'arrived_routes_checked')] == [208, 144000, 235746, 8596]


def audit_trace(trace, *, historical_aux=True):
    result, preflight = load(trace / 'record-result.json'), load(trace / 'preflight.json')
    check_parity_gate(result)
    assert raw_sha(trace / 'record_tls.snapshot.py') == preflight['script_sha256']
    assert raw_sha(trace / 'tls-observer.add.xml') == preflight['observer_sha256']
    for name, expected in preflight['source_hashes'].items():
        if historical_aux or name not in {'e1.add.xml', 'instant.add.xml', 'native-request.json'}:
            assert raw_sha(NATIVE / name) == expected
        if name not in {'e1.add.xml', 'instant.add.xml', 'native-request.json'}:
            assert raw_sha(trace / name) == expected
    actual = {tuple(r.get(k) for k in ('id', 'begin', 'end')): dict(r.attrib) for r in elements(trace / 'field-counts.xml', 'interval')}
    reference = {tuple(r.get(k) for k in ('id', 'begin', 'end')): dict(r.attrib) for r in elements(NATIVE / 'field-counts.xml', 'interval')}
    assert len(actual) == 208 and actual == reference
    count = 0
    old = iter(elements(NATIVE / 'summary.xml', 'step'))
    for current in elements(trace / 'summary.xml', 'step'):
        reference_step = next(old)
        assert {k: v for k, v in current.attrib.items() if k != 'duration'} == {k: v for k, v in reference_step.attrib.items() if k != 'duration'}
        count += 1
    assert count == 144000 and next(old, None) is None
    def canonical(v):
        return ET.canonicalize(ET.tostring(v, encoding='unicode'), strip_text=True)
    before_routes = {v.get('id'): canonical(v) for v in elements(NATIVE / 'vehicle-routes.xml', 'vehicle')}
    after_routes = {v.get('id'): canonical(v) for v in elements(trace / 'vehicle-routes.xml', 'vehicle')}
    assert len(after_routes) == 8596 and before_routes == after_routes
    assert not ET.fromstring(raw(trace / 'collisions.xml')).findall('collision')
    monitor, request = load(trace / 'stall-monitor.json'), load(trace / 'native-request.json')
    assert monitor['status'] == 'completed' and monitor['library_completed'] and monitor['simulation_time_s'] == 14400
    assert monitor['backend'] == request['backend'] == 'libsumo' and monitor['command'] == request['command']
    assert request['junction_body_hold_nodes'] == load(NATIVE / 'native-request.json')['junction_body_hold_nodes']
    command = request['command']
    assert command[command.index('--step-length')+1] == '0.1' and command[command.index('--seed')+1] == '104'
    for filename, slot in (('tls-switch-states.xml', 'state_log'), ('tls-recorded-programs.add.xml', 'program_export')):
        assert raw_sha(trace / filename) == result[slot]['sha256']
    control = ET.fromstring(raw(trace / 'signal-programs.add.xml')).findall('tlLogic')
    widths = {r.get('id'): len(r.find('phase').get('state')) for r in control}
    initial = {r.get('id'): r.get('state') for r in elements(trace / 'tls-switch-states.xml', 'tlsState') if Decimal(r.get('time')) == 0}
    assert len(initial) == 51 and initial.keys() == widths.keys()
    for r in elements(trace / 'tls-switch-states.xml', 'tlsState'):
        assert len(r.get('state')) == widths[r.get('id')] and 0 <= Decimal(r.get('time')) < 14400
        assert Decimal(r.get('time')) * 10 == int(Decimal(r.get('time')) * 10)
    exported = ET.fromstring(raw(trace / 'tls-recorded-programs.add.xml'))
    assert exported.tag == 'additional' and len(exported) == 51 and {r.get('id') for r in exported} == widths.keys()
    for r in exported:
        assert r.tag == 'tlLogic' and r.get('type') == 'static'
        assert sum(Decimal(p.get('duration')) for p in r) == 14400
        assert all(p.tag == 'phase' and len(p.get('state')) == widths[r.get('id')] for p in r)
    declaration = load(trace / 'observed-vehicle-ids.json')
    assert declaration['sha256'] == raw_sha(trace.parent / 'observed-vehicles.csv')
    cohort = declaration['vehicles']
    planned = {v.get('id') for v in ET.fromstring(raw(trace / 'demand.rou.xml')).findall('vehicle')}
    trips = ET.fromstring(raw(trace / 'tripinfo.xml')).findall('tripinfo')
    inserted = {v.get('id') for v in trips}
    arrived = {v.get('id') for v in trips if float(v.get('arrival')) >= 0 and not v.get('vaporized', '')}
    never_inserted = planned - inserted
    assert len(never_inserted) == 27 and len(planned - arrived) == 43
    assert len(cohort) == len(set(cohort)) == 44 and set(cohort) == (planned - arrived) | {'od_0266_v00020'}
    seen = {v.get('id') for v in elements(trace / 'cohort-fcd.xml', 'vehicle')}
    assert len(seen) == 17 and seen == set(cohort) - never_inserted
    return dict(status='pass', parity_checks=12, E1_rows=208, summary_steps=count, arrived_routes=len(after_routes),
        declared_FCD_vehicles=44, observed_FCD_vehicles=len(seen), never_inserted_without_FCD=27,
        TLS=51, instant=event_digest(trace / 'instant-e1.xml'), new_demand_or_controller=False,
        xml_semantics={n: xml_semantics(trace / n) for n in ('cohort-fcd.xml', 'lanechanges.xml', 'tls-switch-states.xml', 'tls-recorded-programs.add.xml')})


def check_directory(directory):
    manifest = load(directory / 'manifest.json')
    for row in manifest['files']:
        value = raw(directory / row['path'])
        assert len(value) == row.get('bytes', row.get('size_bytes')) and hashlib.sha256(value).hexdigest() == row['sha256']


def verify_manifest(case):
    actual = {p.relative_to(case).as_posix() for p in case.rglob('*') if p.is_file() and p != case / 'manifest.json' and '__pycache__' not in p.parts}
    manifest = load(case / 'manifest.json')
    assert actual == {r['path'] for r in manifest['files']}
    for row in manifest['files']:
        p = (case / row['path']).resolve(strict=True)
        assert p.is_relative_to(case) and p.stat().st_size == row['bytes'] and sha(p) == row['sha256']


def verify(case):
    verify_manifest(case)
    previous['verify_manifest'](REFERENCE)
    result = load(case / 'results.json')
    assert sha(REFERENCE / 'manifest.json') == result['reference_manifest_sha256']
    for row in load(case / 'source-provenance.json')['snapshots']:
        value = gzip.decompress((case / row['snapshot']).read_bytes())
        assert len(value) == row['raw_bytes'] and hashlib.sha256(value).hexdigest() == row['raw_sha256']
    assert audit_trace(case / 'trace-v2/baseline') == result['observation']
    for folder in ANALYSES:
        check_directory(case / folder)
    preflight = load(case / 'trace-v2/baseline/preflight.json')
    for name, expected in preflight['driver_hashes'].items():
        assert raw_sha(case / 'code-source' / name) == expected
    assert load(case / 'failed-preparation/preparation-failure.json')['status'] == 'failed_before_native'
    proof = load(case / 'instant-parity-proof.json')
    assert proof['source_event_digest'] == proof['observer_event_digest'] == result['observation']['instant']
    assert raw_sha(case / 'trace-v2/baseline/instant-e1.xml') == proof['observer_raw_sha256']
    local = load(REFERENCE / 'local-only-large-traces.json')['files']
    assert proof['source_raw_sha256'] == next(r['sha256'] for r in local if r['case'] == 'substitute-1pair' and previous['basename'](r['path']) == 'instant-e1.xml')
    print('Manifest, source hashes, 12 parity checks, full FCD/LC/TLS/programs and sealed analyses: pass', flush=True)


def freeze():
    assert not (HERE / 'manifest.json').exists(), 'Preserve an existing frozen or failed packet.'
    check_parity_gate(load(SOURCE / 'trace-v2/baseline/record-result.json'))
    previous['verify_manifest'](REFERENCE)
    for folder in ANALYSES:
        check_directory(SOURCE / folder)
    snapshots, local = [], []

    def copy(source, relative):
        value = source.read_bytes()
        target = HERE / (relative.as_posix() + '.gz')
        assert not target.exists(), 'Preserve partial frozen evidence.'
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(gzip.compress(value, mtime=0))
        snapshots.append(dict(source=str(source), snapshot=target.relative_to(HERE).as_posix(), raw_bytes=len(value), raw_sha256=hashlib.sha256(value).hexdigest()))

    for directory in ('trace-v2', 'next-diagnosis', 'receiving-control-source-audit'):
        for p in sorted((SOURCE / directory).rglob('*')):
            if not p.is_file() or '__pycache__' in p.parts:
                continue
            if p.name == 'queues.xml':
                local.append(dict(path=str(p), bytes=p.stat().st_size, sha256=sha(p)))
            else:
                copy(p, p.relative_to(SOURCE))
    for p in sorted((SOURCE / 'trace').iterdir()):
        assert p.is_file(), 'The failed preparation must not contain a native run.'
        copy(p, Path('failed-preparation') / p.name)
    preflight = load(SOURCE / 'trace-v2/baseline/preflight.json')
    for n, expected in preflight['driver_hashes'].items():
        p = REPO / 'plugins/astra-sumo/src/astra_sumo/core' / n
        assert sha(p) == expected
        copy(p, Path('code-source') / n)
    trace = HERE / 'trace-v2/baseline'
    observed = audit_trace(trace)
    original_instant = SOURCE / 'substitute-1pair/evaluation/native/instant-e1.xml'
    original_digest = event_digest(original_instant)
    assert original_digest == observed['instant']
    save(HERE / 'instant-parity-proof.json', dict(source_event_digest=original_digest, observer_event_digest=observed['instant'],
        source_raw_sha256=sha(original_instant), observer_raw_sha256=raw_sha(trace / 'instant-e1.xml'), new_native_run=False))
    save(HERE / 'results.json', dict(formal_result=False, status='review_required', reference_manifest_sha256=sha(REFERENCE / 'manifest.json'),
        reference_commit='4b3cb18', source_case='substitute-1pair', observation_native_runs=1, new_calibration_candidates=0,
        preparation_failure_native_runs=0, observation=observed))
    save(HERE / 'source-provenance.json', dict(snapshots=snapshots, historical_paths_are_provenance_only=True))
    save(HERE / 'local-only-large-traces.json', dict(files=local))
    (HERE / '.gitattributes').write_text('* -text whitespace=blank-at-eol,blank-at-eof,space-before-tab,cr-at-eol\n*.gz binary\n', encoding='utf-8')
    text = '''# 汉堡单对替换后的观察记录

本包只保存一次同输入观察回放，没有新增需求候选或控制处理，也没有字段或MAE改善主张。源为前包substitute-1pair（提交4b3cb18）：MAE5.092308、SSE6964，未入网27辆、在途16辆，原判定rejected继续保留。主130项零误差目标未达到。

全部12项一致性检查通过：208行E1、144000个交通步骤（只排除CPU duration）、235746条instant事件及8596辆已到达车辆的完整路线/退出时刻与源运行一致。记录耗时263.9078575秒；0.1秒步长、14400秒、seed104和原libsumo 1.27.1驾驶逻辑保持。完整44车声明包含保留双断面车与43个末端未完成请求；其中27辆未插入，没有FCD位置。实际FCD含17辆，每1秒采样并保留200米leader信息。

焦点车已较早选到正确的合法车道链，随后排在未修改的535Z4前车后。实际许可左转窗口与轨迹支持“旧through退出时刻未包含新左转队列服务顺序”的诊断；第一字段hold已释放。该记录不支持继续添加提前换道waypoint，也不能单靠leader与许可绿灯推定完整动态冲突或前车等待原因。完整换道、车身hold、三个服务窗口及TLS差异见trace-v2/independent-analysis/README.md.gz。1秒FCD不是连续轨迹，模型信号也不是现场SPaT。

next-diagnosis保留观察前的窄诊断，其关于缺少FCD/TLS记录的历史表述由本次观察补充。receiving-control-source-audit按原字节保存，仍为review_required：未证实197是重复或独立现场控制。programID为readme_fixed_A不改变其actuated类型；现有航片元数据的不一致也原样保留，不据此删除、合并或修改信号。

完整FCD、lanechanges、51TLS实际state及51份程序、observer请求与原脚本、原生E1/summary/trips/routes/collisions/日志均压缩保留。queues仅列本机哈希；完整instant已入包并以逐事件属性摘要绑定前包本机原始文件。最初cohort准备断言在SUMO启动前失败，日志单列failed-preparation，计0次原生运行。独立分析的review_required与观察一致性pass分开保留。

离线核验不启动SUMO：`.venv/Scripts/python.exe benchmarks/hamburg_ring1_multifield_delay_trace_20261023_v1/freeze_evidence.py --verify-only`。

只有显式调用reproduce.py并指定新output-dir才运行一次同输入观察：`.venv/Scripts/python.exe benchmarks/hamburg_ring1_multifield_delay_trace_20261023_v1/reproduce.py --output-dir outputs/multifield-delay-trace-reproduction`。入口使用冻结observer与前包固定输入，仅重定位文件路径，不恢复历史绝对目录、不生成OD或改控制。观察程序导出只用于本次记录，不外推14400秒之后的状态。

前包manifest及源文件哈希、失败和分析记录均保留。OSM：© OpenStreetMap contributors，ODbL 1.0。
'''
    (HERE / 'README.md').write_text(text, encoding='utf-8')
    assert all(sha(Path(r['source'])) == r['raw_sha256'] for r in snapshots), 'A sealed source changed; preserve the failed freeze.'
    save(HERE / 'manifest.json', dict(formal_result=False, files=[dict(path=p.relative_to(HERE).as_posix(), bytes=p.stat().st_size, sha256=sha(p))
        for p in sorted(HERE.rglob('*')) if p.is_file() and p != HERE / 'manifest.json' and '__pycache__' not in p.parts]))
    verify(HERE)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument('--freeze', action='store_true')
    action.add_argument('--verify-only', action='store_true')
    args = parser.parse_args()
    freeze() if args.freeze else verify(HERE)


if __name__ == '__main__':
    main()
