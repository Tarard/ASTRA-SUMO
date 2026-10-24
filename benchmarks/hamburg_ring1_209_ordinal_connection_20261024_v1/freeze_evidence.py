"""Freeze the completed209 source, observation, construction, smoke and full family."""
import argparse
import gzip
import hashlib
from importlib.util import module_from_spec, spec_from_file_location
import json
from pathlib import Path
import tempfile
import xml.etree.ElementTree as ET

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
OFFSET = REPO / 'benchmarks/hamburg_ring1_119_common_offset_20261024_v1'
spec = spec_from_file_location('ordinal_archive_helpers', OFFSET / 'freeze_evidence.py')
prior = module_from_spec(spec)
spec.loader.exec_module(prior)
raw, load, sha, raw_sha = prior.raw, prior.load, prior.sha, prior.raw_sha
read, jread, rows = prior.read, prior.jread, prior.rows
save, safe_relative, snapshot_relative = prior.save, prior.safe_relative, prior.snapshot_relative
verify_manifest, manifest_paths, audit_measurement = prior.verify_manifest, prior.manifest_paths, prior.audit_measurement
PREPARED = prior.PREPARED
CONSTRUCTOR = REPO / 'outputs/hamburg-209-ordinal-connection-20261024-v1'
FULL_SOURCE = REPO / 'outputs/hamburg-209-ordinal-full-evaluation-20261024-v1'
PLAN = load(CONSTRUCTOR / 'unified-freeze-plan.json')
SEALED = {label: (REPO / row['path'], row['sha256']) for label, row in PLAN['sealed_source_manifests'].items()}
SEALED['full-independent'] = (FULL_SOURCE / 'independent/manifest.json', '4be99930063b56cc3dd69556e6371037bd7e78819c0559c6def0e165ac233cc7')


def tree(selected, prefix, source):
    selected.update({Path(prefix) / p.relative_to(source): p for p in source.rglob('*') if p.is_file() and '__pycache__' not in p.parts})


def selected_files():
    selected = {}
    for label, (manifest, expected) in SEALED.items():
        assert sha(manifest) == expected
        selected.update({Path('sealed') / label / p: manifest.parent / p for p in manifest_paths(manifest.parent)})
    for prefix, folder in [('service60', 'hamburg-119-saturated-service-20261024-v1'),
                           ('observer4500', 'hamburg-119-zero-discharge-observer-20261024-v1'),
                           ('construction', 'hamburg-209-ordinal-connection-20261024-v1')]:
        tree(selected, prefix, REPO / 'outputs' / folder)
    tree(selected, 'full', FULL_SOURCE / 'full')
    for p in FULL_SOURCE.iterdir():
        if p.is_file():
            selected[Path('full-study-source') / p.name] = p
    return selected


def physical(case, name):
    p = case / name
    if p.is_file() or Path(str(p) + '.gz').is_file():
        return p
    row = load(case / 'byte-references.json')['aliases'][str(name).replace('\\', '/')]
    return physical(case, row['target']) if row['scope'] == 'packet' else REPO / safe_relative(row['target'])


def reference_catalog():
    result = prior.reference_catalog()
    for row in load(OFFSET / 'source-provenance.json')['snapshots']:
        p = physical(OFFSET, row['relative'])
        assert raw_sha(p) == row['raw_sha256']
        result[row['raw_sha256']] = p
    # Source facts may cite compressed official assets or old uncompressed logical files.
    for case in (prior.BASE, OFFSET, REPO / 'benchmarks/hamburg_ring1_primary_branch_repair_20261023_v1',
                 REPO / 'benchmarks/hamburg_ring1_readme_v1_zero_20261023_v1',
                 REPO / 'benchmarks/hamburg_ring1_end_to_end_20261021_v1'):
        for p in case.rglob('*'):
            if p.is_file() and '__pycache__' not in p.parts:
                result.setdefault(sha(p), p)
                if p.suffix == '.gz':
                    result.setdefault(hashlib.sha256(gzip.decompress(p.read_bytes())).hexdigest(), Path(str(p)[:-3]))
    return result


def check_prefix_coverage(coverage, native):
    def key(r):
        return r['detector_id'], float(r['begin']), float(r['end'])
    assert len(coverage) == len({key(r) for r in coverage}) == 182
    assert len(native) == 65
    for row in coverage:
        if key(row) in native:
            assert int(row['simulated_count']) == int(native[key(row)]['nVehContrib'])
        else:
            assert row['simulated_count'] is None and row['difference'] is None


def audit_context(case):
    assets = jread(case, 'sealed/official209/assets.json')
    for row in assets['files']:
        if row.get('http_status') == 200:
            payload = gzip.decompress(read(case, Path('sealed/official209') / row['snapshot']))
            assert len(payload) == row['bytes'] and hashlib.sha256(payload).hexdigest() == row['sha256']
    fixture = jread(case, 'service60/native/result.json')
    assert fixture['status'] == 'pass' and all(fixture['checks'].values())
    assert len(ET.fromstring(read(case, 'service60/native/tripinfo.xml')).findall('tripinfo')) == 60
    observer = jread(case, 'observer4500/native/record-result.json')
    assert observer['status'] == 'pass' and all(observer['checks'].values())
    coverage = jread(case, 'observer4500/native/target182-prefix-measurements.json')
    assert len(coverage) == 182 and sum(r['simulated_count'] is None for r in coverage) == 117
    check_prefix_coverage(coverage, prior.prior.intervals(read(case, 'observer4500/native/field-counts.xml')))
    frames = gzip.decompress(read(case, 'observer4500/native/read-only-samples.jsonl.gz')).decode().splitlines()
    assert len(frames) == 301
    smoke = jread(case, 'construction/smoke/smoke-verdict.json')
    assert smoke['status'] == 'pass' and all(smoke['checks'].values()) and smoke['native_runs'] == 1
    assert {r['movement'] for r in smoke['movement_coverage'] if r['covered']} == {531, 532, 533, 534, 625, 626}
    assert jread(case, 'construction/smoke/native-coverage/fcd-lane-crosscheck.json')['status'] == 'pass'
    return dict(service_fixture60_pass=True, source_observer4500_parity_pass=True, source_getter_frames=301,
                source_prefix_measured_rows=65, source_future117_null=True, smoke5cars6movements_pass=True,
                old4500_prefix_is_not_new_network_full_parity_gate=True)


def materialize_full(case, out):
    for folder in ('prepared', 'evaluation'):
        for row in load(case / 'source-provenance.json')['snapshots']:
            name = Path(row['relative'])
            if name.is_relative_to(Path('full') / folder):
                target = out / name.relative_to('full')
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(read(case, name))


def audit_full(case):
    verdict = jread(case, 'full/verdict.json')
    independent = jread(case, 'sealed/full-independent/summary.json')
    assert independent['audit_status'] == 'pass' and independent['schema'] == 'astra.hamburg.209-ordinal-full-independent/v1'
    assert verdict['status'] == independent['raw_verdict'] == 'diagnostic_measurement_complete'
    assert verdict['after'] == independent['after']
    assert verdict['source_plus15'] == independent['references']['plus15'] and verdict['offset0_reference'] == independent['references']['offset0']
    p = jread(case, 'full/preflight.json')
    assert p['scope_sha256'] == hashlib.sha256(read(case, 'full/scope-proof.json')).hexdigest()
    scope = jread(case, 'full/scope-proof.json')
    assert scope['N'] == 8640 and scope['status'] == 'pass'
    for name, expected in scope['prepared_hashes'].items():
        assert hashlib.sha256(read(case, Path('full/prepared') / name)).hexdigest() == expected
    for name, expected in p['implementation_sha256'].items():
        assert hashlib.sha256(read(case, Path('full/implementation') / name)).hexdigest() == expected
    assert p['smoke']['sha256'] == hashlib.sha256(read(case, 'construction/smoke/smoke-verdict.json')).hexdigest()
    for name in ('demand.rou.xml', 'lane-intentions.json', 'field-loops.add.xml', 'expected-counts.csv', 'initialization-counts.csv'):
        assert read(case, Path('full/prepared') / name) == read(OFFSET, Path('plus15/prepared') / name)
    for name in ('network.net.xml', 'control.add.xml'):
        assert read(case, Path('full/prepared') / name) == read(case, Path('construction/candidate-v3') / name)
    with tempfile.TemporaryDirectory() as temporary:
        out = Path(temporary)
        materialize_full(case, out)
        signatures = {r['vehicle_id']: [r['field_id']] for r in json.loads((out / 'prepared/lane-intentions.json').read_bytes())['vehicles']}
        measured, _, extra, repeats = audit_measurement(out / 'prepared', out / 'evaluation', signatures)
        data = rows(out, 'evaluation/all182-comparison.csv')
    assert not extra and not repeats and measured['safe'] and measured['healthy'] and measured['event_count'] == 8640
    for local, saved in [('main130', 'metrics130'), ('initial52', 'metrics52'), ('all182', 'metrics182')]:
        assert all(verdict['after'][saved][k] == v for k, v in measured[local].items())
    assert measured['completion'] == verdict['after']['completion'] and measured['station40_MAE'] == 9.875
    assert verdict['all53556'] == independent['all53556'] and verdict['all53556']['TAE'] == 0
    field13 = {}
    for field in sorted({r['detector_id'] for r in data}):
        errors = [int(r['diff_nVehContrib_minus_expected']) for r in data if r['detector_id'] == field and float(r['begin']) >= 3600]
        assert len(errors) == 10
        field13[field] = dict(TAE=sum(map(abs, errors)), signed=sum(errors))
    assert field13['hh_0119_Z_5_30494']['TAE'] == 79 and field13['hh_0535_Z_4_33055']['TAE'] == 63
    return dict(raw_verdict=verdict['status'], measurement=measured, all53556=verdict['all53556'], field13_main=field13,
                comparisons=verdict['comparison'], physical_applicability='review_required', exact130=False,
                source_plus15_main_and_initial_and_all182_aggregate_improve=True, offset0_initial_TAE_regresses_105_to136=True)


def audit_packet(case):
    return dict(status='review_required', formal_result=False, new_native_calls_recorded=4, native_runs_by_freeze=0,
                context=audit_context(case), full=audit_full(case), lowest_measured_healthy_main_MAE=421/130,
                field_SPAT_or_dated_geometry_certified=False, calibration_complete=False)


def check_local_trace(row, recorded):
    assert Path(row['path']).name in {'instant-e1.xml', 'queues.xml'}
    assert row.get('bytes', row.get('size_bytes')) == recorded['bytes'] and row['sha256'] == recorded['sha256']


def verify(case):
    verify_manifest(case)
    verify_manifest(OFFSET)
    assert sha(OFFSET / 'manifest.json') == load(case / 'source-references.json')['offset_family_manifest_sha256']
    local = {r['relative']: r for r in load(case / 'local-only-large-traces.json')['files']}
    for label, (_, expected) in SEALED.items():
        assert hashlib.sha256(read(case, Path('sealed') / label / 'manifest.json')).hexdigest() == expected
        for row in jread(case, Path('sealed') / label / 'manifest.json')['files']:
            name = Path('sealed') / label / safe_relative(row['path'])
            if name.as_posix() in local:
                check_local_trace(row, local[name.as_posix()])
                continue
            data = read(case, name)
            assert len(data) == row.get('bytes', row.get('size_bytes')) and hashlib.sha256(data).hexdigest() == row['sha256']
    for row in load(case / 'source-provenance.json')['snapshots']:
        data = read(case, row['relative'])
        assert len(data) == row['raw_bytes'] and hashlib.sha256(data).hexdigest() == row['raw_sha256']
    for row in load(case / 'historical-source-bindings.json')['bindings']:
        data = read(case, row['target']) if row['scope'] == 'packet' else raw(REPO / safe_relative(row['target']))
        assert hashlib.sha256(data).hexdigest() == row['sha256']
    assert audit_packet(case) == load(case / 'results.json')['audit']
    print('Unified209 sources/60car/4500observer/5car/full130+40+52+182+208: pass', flush=True)


def freeze():
    assert not (HERE / 'source-provenance.json').exists() and not (HERE / 'manifest.json').exists()
    locations = {h: dict(scope='repository', target=p.relative_to(REPO).as_posix()) for h, p in reference_catalog().items()}
    aliases, provenance, local = {}, [], []
    for name, source in sorted(selected_files().items()):
        digest = sha(source)
        if source.name in {'instant-e1.xml', 'queues.xml'}:
            local.append(dict(path=str(source), relative=name.as_posix(), bytes=source.stat().st_size, sha256=digest))
            continue
        relative = name.as_posix()
        if digest in locations:
            aliases[relative] = dict(**locations[digest], sha256=digest)
        else:
            target = HERE / (relative + '.gz')
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(gzip.compress(source.read_bytes(), mtime=0))
            locations[digest] = dict(scope='packet', target=relative)
        provenance.append(dict(source=str(source), relative=relative, raw_bytes=source.stat().st_size, raw_sha256=digest))
    save(HERE / 'source-provenance.json', dict(snapshots=provenance, source_files_unchanged=True))
    save(HERE / 'byte-references.json', dict(aliases=aliases, rule='Exact raw SHA aliases only; no semantic JSON substitution.'))
    bindings = []
    for original, digest in load(FULL_SOURCE / 'full/scope-proof.json')['source_hashes'].items():
        assert digest in locations, (original, digest)
        bindings.append(dict(declared_path=original, sha256=digest, **locations[digest]))
    for row in load(CONSTRUCTOR / 'candidate-v3/implementation-mapping.json'):
        assert row['sha256'] in locations
        bindings.append(dict(declared_path=row['historical_source_path'], sha256=row['sha256'], **locations[row['sha256']]))
    save(HERE / 'historical-source-bindings.json', dict(bindings=bindings, historical_hashes_not_rewritten=True))
    save(HERE / 'local-only-large-traces.json', dict(files=local, prefix_getter301_gzip_and_fixture_smoke_FCD_included=True))
    save(HERE / 'source-references.json', dict(offset_family_commit='4caff53687b5587059d7bbf1563ac6abc159e845', offset_family_manifest_sha256=sha(OFFSET / 'manifest.json'), current_core_commit='0cf711b', baseline_offset0_commit='42a827e'))
    save(HERE / 'results.json', dict(status='review_required', formal_result=False, audit=audit_packet(HERE)))
    (HERE / '.gitattributes').write_text('* -text whitespace=blank-at-eol,blank-at-eof,space-before-tab,cr-at-eol\n*.gz binary\n', encoding='utf-8')
    (HERE / 'README.md').write_text(README, encoding='utf-8')
    assert all(sha(Path(r['source'])) == r['raw_sha256'] for r in provenance)
    save(HERE / 'manifest.json', dict(status='review_required', formal_result=False, files=[dict(path=p.relative_to(HERE).as_posix(), bytes=p.stat().st_size, sha256=sha(p)) for p in sorted(HERE.rglob('*')) if p.is_file() and p != HERE / 'manifest.json' and '__pycache__' not in p.parts]))
    verify(HERE)


README = '''# 209车道连接：统一来源、观测与完整原生验证

当前最低已测健康主130 MAE为3.238461538，TAE421、SSE5131、最大误差21。8640请求全部插入并到达，零等待、在途、碰撞、传送和discard；8640个唯一预期字段贡献，无重复或额外字段。535指定56行仍全部零。主130尚未归零，物理适用仍为review_required，不把拟合改善当现场模型认证。

| 指标 | 原offset0 | +15源 | 209候选 |
|---|---:|---:|---:|
| 主130 MAE / SSE |3.715385 / 6709|3.538462 / 5818|3.238462 / 5131|
| 初始52 TAE / SSE |105 / 1005|168 / 2116|136 / 1894|
| 全182 TAE / SSE |588 / 7714|628 / 7934|557 / 7025|
| 派生40站点MAE |10.975|10.55|9.875|

相对+15源，所有这些总体误差均下降；字段并未全改善。119 Z5主TAE从67升到79，535 Z4从41升到63。相对offset0，初始化TAE仍从105升到136。全部130/40/52/182/208行、逐字段及最大残差保留。原full状态diagnostic_measurement_complete与源offset试验的原rejected标签不改；预设门不是用户主130归零目标的替代。

本包只收同一问题链的四次原生调用：60车局部服务fixture、原+15输入的4500秒被动观察、一次5车movement smoke，以及唯一8640全时域验证。60车没有603交叉流，但不证明现场饱和容量。4500观察保留65个完整E1区间、45000条summary、4405–4435秒301帧getter及episode/独立分析；未来117行显式null。它证实原源前缀，不作为新连接full必须不变的门。

当前OSM与2022官方209 MAP/KML支持核查完整五lane序：603的两right与三forward，及734lane0的primary续行。候选保留道路/车道几何、长度、权限、全部合法住宅支路、8640需求、lane意图、真实计数与驾驶规则；只调整声明的连接和新movement的相邻同向颜色投影，phase/type/duration/offset不变。734/110三lane均为机动车，未放宽自行车权限。MAP14→egress5/SG6属于未保留的东北Alsterufer，不是保留的270 Warburg住宅支路。

官方209/15.1 MAP/KML于2026-10-24重新取得，HTTP Last-Modified为2022-08-15；其日期不证明2026-10-19实施。209 OCIT目录无条目，一次同版本路径404，未猜SP1、intergreen或实际SPaT。既有2026航片分辨率与遮挡不足以独立确认逐lane箭头。V1对照、原OSM构建、剪枝/主路恢复lineage及六灯归属缺口均保留，不把猜灯直接叫冗余或bug，未撤灯。

candidate-v1/v2的离线失败、v3原materializer和后续guard-only快照均保存。原构建README中的“尚未traffic运行”是当时状态，不覆盖后来的smoke/full。5车smoke实际观察五个新movement及住宅保留的unique via→receiving lane并完整到达边界，mode31、原生moving waypoints，无setSpeed/position/remove/phase操作；它不认证拥堵或普遍安全。全TLS/FCD/LC及原警告保留。完整运行使用同项目Libsumo1.27.1、0.1秒、seed104、原四处bodyhold。

共用旧网络/需求/观测/core按固定包SHA引用；旧声明路径绑定实际快照，不要求后来的live guard与旧materializer相同。301帧JSONL、fixture/smoke FCD和compact完整贡献可迁移。大queues/instant仅保留本地大小/SHA；原生复现会另生成它们。没有新render、NetEdit或后续校准试验。本次冻结没有额外SUMO。

离线核验：`.venv\\Scripts\\python.exe benchmarks/hamburg_ring1_209_ordinal_connection_20261024_v1/reproduce.py --mode verify`。

显式完整原生复现从仓库根目录运行，必须使用新目录：

```powershell
$env:PYTHONPATH = (Join-Path $PWD '.venv/Lib/site-packages') + ';' + (Join-Path $PWD 'plugins/astra-sumo/src')
$env:SUMO_HOME = Join-Path $PWD '.venv/Lib/site-packages/sumo'
.\\.venv\\Scripts\\python.exe benchmarks/hamburg_ring1_209_ordinal_connection_20261024_v1/reproduce.py --mode native-full --output-dir outputs/reproduce-209-ordinal-full
```

入口检查实际模块路径、版本及二进制/core哈希，从冻结显式输入调用原生core，不重新生成OD。离线verify不等于重新原生运行，复现这些结果不表示130零误差或现场SPaT已确定。
'''


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--freeze', action='store_true')
    mode.add_argument('--verify-only', action='store_true')
    args = parser.parse_args()
    freeze() if args.freeze else verify(HERE)
