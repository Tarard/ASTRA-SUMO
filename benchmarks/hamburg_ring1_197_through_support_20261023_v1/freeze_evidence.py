"""Freeze the completed 197 family; whitelist sealed evidence and never run SUMO."""
import argparse
import ast
import gzip
import hashlib
from importlib.metadata import version
from importlib.util import module_from_spec, spec_from_file_location
import json
from pathlib import Path
import re
import xml.etree.ElementTree as ET


HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
SOURCE = REPO / 'outputs/hamburg-197-through-support-20261023-v1'
SUBSTITUTION = REPO / 'outputs/hamburg-multifield-substitution-20261023-v1'
PREVIOUS = REPO / 'benchmarks/hamburg_ring1_multifield_substitution_20261023_v1'
spec = spec_from_file_location('frozen_substitution', PREVIOUS / 'freeze_evidence.py')
prior = module_from_spec(spec)
spec.loader.exec_module(prior)
raw, load, rows, sha, raw_sha = prior.raw, prior.load, prior.rows, prior.sha, prior.raw_sha
verify_manifest, audit_measurement = prior.verify_manifest, prior.audit_measurement
FOLDERS = ('candidate-review', 'candidate-v1', 'candidate-v2', 'candidate-v3', 'candidate-v4', 'candidate-v5', 'candidate-v5-review',
    'prototype-final-native', 'prototype-load-restored', 'prototype-pinned-walking', 'prototype-pinned-walking-v2', 'prototype-two',
    'prototype-two-pinned', 'prototype-two-pinned-crossings', 'prototype-two-pinned-lengths', 'prototype-uturn-yields',
    'full', 'offline-full', 'full-response-audit', 'import-semantics', 'native-baseline-diagnosis', 'smoke', 'review')
SOURCE_AUDITS = ('receiving-topology-generation-audit', 'receiving-through-source-audit')


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')


def check_sealed(directory):
    manifest = load(directory / 'manifest.json')
    if 'files' not in manifest:
        return
    for row in manifest['files']:
        value = raw(directory / row['path'])
        assert len(value) == row.get('size_bytes', row.get('bytes')) and hashlib.sha256(value).hexdigest() == row['sha256']


def audit_full(case):
    prepared, evaluation = case / 'full/prepared', case / 'full/evaluation'
    baseline = PREVIOUS / 'baseline'
    for name in ('demand.rou.xml', 'lane-intentions.json'):
        assert raw(prepared / name) == raw(baseline / 'evaluation' / name)
    for name in ('field-loops.add.xml', 'expected-counts.csv', 'initialization-counts.csv'):
        assert raw(prepared / name) == raw(baseline / 'prepared' / name)
    for name in ('network.net.xml', 'control.add.xml'):
        assert raw(prepared / name) == raw(case / 'candidate-v5' / name)
    signatures = {r['vehicle_id']: [r['field_id']] for r in load(prepared / 'lane-intentions.json')['vehicles']}
    assert len(signatures) == 8640
    report, actual, extra, repeats = audit_measurement(prepared, evaluation, signatures)
    verdict = load(case / 'full/verdict.json')
    assert verdict['after'] == load(evaluation / 'evaluation.json') and verdict['before'] == load(baseline / 'evaluation/evaluation.json')
    assert verdict['all182_TAE'] == report['all182']['TAE'] and verdict['source_inputs_unchanged'] and not verdict['formal_result']
    assert verdict['repeated_vehicle_fields'] == repeats and verdict['contributions_outside_prior_primary_intention'] == extra
    assert verdict['complete_contributions'] == report['event_count'] and verdict['contributing_vehicles'] == len(actual)
    assert verdict['status'] == ('diagnostic_candidate' if report['safe'] and not repeats else 'invalid')
    manifest = load(case / 'candidate-v5/manifest.json')
    assert manifest['status'] == 'review_required' and len(manifest['checks']) == 16 and all(manifest['checks'].values())
    assert raw_sha(case / 'candidate-v5/network.net.xml') == manifest['candidate_sha256']
    assert raw_sha(case / 'candidate-v5/control.add.xml') == manifest['control_sha256']
    for name, expected in load(case / 'full/preflight.json')['prepared_hashes'].items():
        assert raw_sha(prepared / name) == expected
    counts = prior.helper.periodic_counts(evaluation / 'native/field-counts.xml')
    targets = rows(prepared / 'initialization-counts.csv') + rows(prepared / 'expected-counts.csv')
    selected = [r for r in targets if r['detector_id'] in {'hh_0535_Z_5_30894', 'hh_0535_Z_6_33855', 'hh_0535_Z_7_29316', 'hh_0535_Z_8_34287'}]
    assert len(selected) == 56 and sum(int(r['expected_total']) for r in selected) == 1459
    assert all(counts[prior.helper.key(r)] == int(r['expected_total']) for r in selected)
    demand = {v.get('id'): v for v in ET.fromstring(raw(prepared / 'demand.rou.xml')).findall('vehicle')}
    trips = ET.fromstring(raw(evaluation / 'native/tripinfo.xml')).findall('tripinfo')
    unfinished = [r.get('id') for r in trips if float(r.get('arrival')) < 0]
    assert len(unfinished) == 34 and all(demand[i].find('route').get('edges').split()[0] == '963776077#0' and signatures[i] == ['hh_0119_Z_5_30494'] for i in unfinished)
    reviewed = load(case / 'full-response-audit/complete/scan.json')
    assert reviewed['status'] == 'pass' and reviewed['native_runs_by_audit'] == 0
    for key, other in (('main130', 'metrics130'), ('initial52', 'metrics52'), ('all182', 'metrics182')):
        assert all(reviewed['candidate'][other][k] == v for k, v in report[key].items())
    assert reviewed['candidate']['completion'] == report['completion']
    report.update(source535_real56_exact=True, source535_real56_expected_and_measured=1459, unfinished_origin='963776077#0', unfinished_primary_field='hh_0119_Z_5_30494',
        raw_verdict_status=verdict['status'], status='review_required', formal_result=False)
    return report


def code_closure():
    core = REPO / 'plugins/astra-sumo/src/astra_sumo/core'
    package = core.parent
    pending = [core / 'hamburg_197_through_support.py']
    found = set()
    while pending:
        path = pending.pop()
        if path in found or not path.is_file():
            continue
        found.add(path)
        parent = path.parent
        while parent.is_relative_to(package):
            pending.append(parent / '__init__.py')
            if parent == package:
                break
            parent = parent.parent
        for row in ast.walk(ast.parse(path.read_text(encoding='utf-8'))):
            if not isinstance(row, ast.ImportFrom):
                continue
            if row.module and row.module.startswith('astra_sumo.core.'):
                target = core.joinpath(*row.module.removeprefix('astra_sumo.core.').split('.'))
                pending.extend((target.with_suffix('.py'), target / '__init__.py'))
            if not row.level:
                continue
            parent = path.parent
            for _ in range(row.level-1):
                parent = parent.parent
            if row.module:
                target = parent.joinpath(*row.module.split('.'))
                pending.extend((target.with_suffix('.py'), target / '__init__.py'))
            else:
                pending.extend(parent / (r.name+'.py') for r in row.names)
    for p in (SOURCE / 'full/implementation').glob('*.py'):
        found.add(core / p.name)
    return sorted(found)


def check_links(review):
    for html in review.rglob('*.html'):
        for link in re.findall(r'(?:href|src)=[\"\']([^\"\']+)', html.read_text(encoding='utf-8')):
            if link.startswith(('http:', 'https:', 'data:', '#', 'javascript:')):
                continue
            assert (html.parent / link.split('#')[0].split('?')[0]).is_file(), (html, link)


def verify(case):
    verify_manifest(case)
    prior.verify_manifest(PREVIOUS)
    assert sha(PREVIOUS / 'manifest.json') == load(case / 'results.json')['previous_manifest_sha256']
    assert audit_full(case) == load(case / 'results.json')['full']
    for row in load(case / 'source-provenance.json')['snapshots']:
        p = case / row['snapshot']
        value = p.read_bytes() if row['plain'] else gzip.decompress(p.read_bytes())
        assert len(value) == row['raw_bytes'] and hashlib.sha256(value).hexdigest() == row['raw_sha256']
    for name in (*SOURCE_AUDITS, 'full-response-audit', 'import-semantics', 'native-baseline-diagnosis', 'candidate-review', 'candidate-v5-review', 'smoke', 'review'):
        check_sealed(case / name)
    check_links(case / 'review')
    compact = json.loads(gzip.decompress(raw(case / 'full/evaluation/contributing-passages.json.gz')))
    instant = next(r for r in load(case / 'local-only-large-traces.json')['files'] if r['scope'] == 'full' and prior.basename(r['path']) == 'instant-e1.xml')
    assert instant['sha256'] in {r['sha256'] for r in compact['inputs']}
    assert load(case / 'smoke/audit.json')['new_native_runs'] == 10
    print('Family hashes, fixed8640, raw208/130/40/52/182, completion, preserved failures and review links: pass', flush=True)


def freeze():
    assert not (HERE / 'manifest.json').exists(), 'Preserve frozen evidence or a partial failure.'
    assert (SOURCE / 'full/verdict.json').is_file() and load(SOURCE / 'full-response-audit/complete/scan.json')['status'] == 'pass'
    for directory in (SOURCE / 'review', SOURCE / 'smoke', SOURCE / 'full-response-audit', SOURCE / 'candidate-v5-review', *(SUBSTITUTION / n for n in SOURCE_AUDITS)):
        check_sealed(directory)
    prior.verify_manifest(PREVIOUS)
    provenance, local = [], []

    def copy(source, relative, *, plain=False):
        data = source.read_bytes()
        decoded = data if source.suffix != '.gz' else gzip.decompress(data)
        target = HERE / relative
        if plain:
            decoded = data
        elif source.suffix != '.gz':
            target = target.with_name(target.name+'.gz')
        target.parent.mkdir(parents=True, exist_ok=True)
        encoded = data if plain or source.suffix == '.gz' else gzip.compress(data, mtime=0)
        if target.exists():
            assert target.read_bytes() == encoded, 'Retain a conflicting partial freeze without replacing it.'
        else:
            target.write_bytes(encoded)
        provenance.append(dict(source=str(source), snapshot=target.relative_to(HERE).as_posix(), plain=plain, raw_bytes=len(decoded), raw_sha256=hashlib.sha256(decoded).hexdigest()))

    for directory in FOLDERS:
        for p in sorted((SOURCE / directory).rglob('*')):
            if not p.is_file() or '__pycache__' in p.parts:
                continue
            if directory == 'full' and p.name in {'instant-e1.xml', 'queues.xml'}:
                local.append(dict(scope='full' if directory == 'full' else directory, path=str(p), bytes=p.stat().st_size, sha256=sha(p)))
            else:
                copy(p, p.relative_to(SOURCE), plain=directory == 'review' or p.suffix.lower() in {'.png', '.svg', '.html', '.css', '.js'})
    for name in SOURCE_AUDITS:
        for p in sorted((SUBSTITUTION / name).rglob('*')):
            if p.is_file() and '__pycache__' not in p.parts:
                copy(p, Path(name) / p.relative_to(SUBSTITUTION / name))
    for name in ('measure_full.py', 'full-launch.json', 'full.stdout.log', 'full.stderr.log'):
        copy(SOURCE / name, Path(name))
    code, core = code_closure(), REPO / 'plugins/astra-sumo/src/astra_sumo/core'
    for path in code:
        copy(path, Path('code-source') / path.relative_to(core.parent))
    copy(REPO / 'tests/test_hamburg_197_through_support.py', Path('code-source/tests/test_hamburg_197_through_support.py'))
    for path in (SOURCE / 'full/implementation').glob('*.py'):
        assert sha(path) == sha(core / path.name)
    licenses = REPO / '.venv/Lib/site-packages/eclipse_sumo-1.27.1.dist-info/licenses'
    for name in ('LICENSE', 'NOTICE.md', 'AUTHORS'):
        copy(licenses / name, Path('upstream-sumo') / name, plain=True)
    for name in ('network.net.xml', 'control.add.xml'):
        p = PREVIOUS / 'baseline/prepared' / (name+'.gz')
        copy(p, Path('source-input') / p.name)
    source_refs = {name: load(SUBSTITUTION / name / 'source-hashes.json') for name in SOURCE_AUDITS}
    save(HERE / 'source-and-legacy-references.json', dict(frozen_source_audits=source_refs, previous_manifest_sha256=sha(PREVIOUS / 'manifest.json'), paths_are_provenance=True))
    dispositions = {name: dict(accepted_for_full=False, decision='blocked' if name.startswith('candidate-v') else 'review_required',
        original_status=load(SOURCE / name / 'manifest.json').get('status') if (SOURCE / name / 'manifest.json').is_file() else None,
        boundary='Exploration or rejected artifact; do not infer acceptance from conversion or zero-vehicle load.') for name in FOLDERS if name.startswith('prototype-') or name in ('candidate-v1', 'candidate-v2', 'candidate-v3', 'candidate-v4')}
    dispositions['candidate-v5'] = dict(decision='review_required', selected_working_source_corrected_candidate=True, formal_result=False)
    save(HERE / 'candidate-dispositions.json', dispositions)
    full = audit_full(HERE)
    import sumo
    runtime = dict(versions={n: version(n) for n in ('eclipse-sumo', 'libsumo', 'traci', 'sumolib', 'numpy', 'scipy', 'shapely')},
        netconvert_sha256=sha(Path(sumo.__file__).parent / 'bin/netconvert.exe'), implementation_sha256={p.relative_to(core.parent).as_posix(): sha(p) for p in code})
    save(HERE / 'runtime.json', runtime)
    save(HERE / 'source-provenance.json', dict(snapshots=provenance, whitelisted_directories=FOLDERS, later_trace_and_next_stage_excluded=True))
    save(HERE / 'local-only-large-traces.json', dict(files=local))
    save(HERE / 'results.json', dict(status='review_required', formal_result=False, previous_manifest_sha256=sha(PREVIOUS / 'manifest.json'),
        new_full_native_runs=1, toy_native_runs=10, source_supported_candidate_not_field_SPaT=True, full=full))
    (HERE / '.gitattributes').write_text('* -text whitespace=blank-at-eol,blank-at-eof,space-before-tab,cr-at-eol\n*.gz binary\n*.png binary\n', encoding='utf-8')
    (HERE / 'README.md').write_text('''# 汉堡197直行支持修正：工作候选与完整证据

本包冻结源证据支持的局部工作候选，不是正式零误差结果或现场SPaT。只增加197两条passenger延续3→4、4→5，保留原普通道路、完整旧内部曲线/长度、权限和旧合法转向；旧掉头9的phase0/1及停车左转2的phase3由G改为许可g，新14/15继承原直行1。全部时间参数和type不变，感应实际历史仍会随交通改变。

| 完整14400秒原生结果 | 原selected | v5 |
|---|---:|---:|
| 130 MAE / TAE | 5.092308 / 662 | 4.653846 / 605 |
| 130 SSE / 最大绝对误差 | 6898 / 27 | 9473 / 35 |
| 40派生站点MAE | 14.8 | 12.825 |
| 52 TAE / 182 TAE | 82 / 744 | 78 / 683 |
| 请求 / 未入网 / 在途 | 8640 / 7 / 14 | 8640 / 0 / 34 |
| 碰撞 / 传送 / 丢弃 | 0 / 0 / 0 | 0 / 0 / 0 |

MAE下降，但SSE和最大误差变差，34辆仍在途，healthy=false。不能只用一个改善指标宣称成功。535 Z5–8的56项真实初始化及主窗观测全部精确匹配，合计1459；主窗这四字段TAE从223降到0，其他字段退步。全部34辆未完成请求来自963776077#0、意向119 Z5。原verdict diagnostic_candidate保留；formal_result=false，130项全零目标未完成。

full保存相同8640请求的原字节、全部13字段208周期原生E1、130主计数、52初始化和40派生站点、182对账、summary/trips/routes/collisions/警告、完整贡献身份与实际执行快照。步长0.1秒、seed104、libsumo1.27.1、原moving-stop及4节点车身hold保持；不按新OD模型重新生成需求。instant及queues本机大文件哈希另列，原生计数无缺测补零或删难字段。

candidate-dispositions.json明确保留全部拒绝原型和v1–v4；v5的network.net.xml是最终二次原生转换产物，candidate.net.xml仅是中间件。核心16项保护、原生request与g/G让行、独立静态审查、源码/测试副本均保留。10次小探针共24请求，9pass与1个字段/路线不兼容的坏solo均保留；全部0unsafe不代表所有人车冲突或完整需求的普遍安全保证。

source audits、SUMO1.27.1官方源码及原版权头、导入语义、原生晚窗诊断和独立完整分群审查均按原字节保存。旧箭头导入机制可以解释被推导的单车道直行支持，不能单独证明现场禁转或SUMO执行缺陷。停车支路、掉头、自行车、步行和信号未删除。原source/legacy引用由既有包及哈希绑定；SUMO随附LICENSE/NOTICE/AUTHORS原文另列upstream-sumo。

[固定全局与197局部航片、NetEdit](review/README.md)保留全部图和可用相对链接。渲染pass与最终review_required分开；图像不证明现场控制或零误差。航片© Freie und Hansestadt Hamburg, LGV，Datenlizenz Deutschland Namensnennung 2.0；OSM© OpenStreetMap contributors，ODbL1.0。

离线核验：`.venv/Scripts/python.exe benchmarks/hamburg_ring1_197_through_support_20261023_v1/freeze_evidence.py --verify-only`。

只有显式调用reproduce.py并指定新output-dir才用既有core重做局部materialization，再以冻结selected需求执行一次完整原生测量。它核源码/版本/hash及全部原生计数，不恢复旧缓存hash、不改OD、不外推14400秒之后的控制。冻结不运行仿真；后续观察与下一阶段目录未纳入。
''', encoding='utf-8')
    for row in provenance:
        p = Path(row['source'])
        value = p.read_bytes() if row['plain'] or p.suffix != '.gz' else gzip.decompress(p.read_bytes())
        assert hashlib.sha256(value).hexdigest() == row['raw_sha256'], 'A sealed source changed; retain this partial freeze.'
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
