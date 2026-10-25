"""Freeze one terminal observed-service substitution using the previous audits."""
import argparse
import gzip
import hashlib
from importlib.util import module_from_spec, spec_from_file_location
import json
from pathlib import Path
import xml.etree.ElementTree as ET


HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
SOURCE = REPO / 'outputs/hamburg-multifield-service-pair-20261023-v1'
PREVIOUS = REPO / 'benchmarks/hamburg_ring1_multifield_substitution_20261023_v1'
spec = spec_from_file_location('substitution_evidence', PREVIOUS / 'freeze_evidence.py')
prior = module_from_spec(spec)
spec.loader.exec_module(prior)
raw, load, rows, sha, raw_sha = prior.raw, prior.load, prior.rows, prior.sha, prior.raw_sha
verify_manifest, audit_measurement = prior.verify_manifest, prior.audit_measurement
NAME = 'substitute-1pair'


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')


def check_sealed(directory):
    manifest = load(directory / 'manifest.json')
    for row in manifest['files']:
        value = raw(directory / row['path'])
        assert len(value) == row.get('size_bytes', row.get('bytes')) and hashlib.sha256(value).hexdigest() == row['sha256']


def audit(case):
    plan = load(case / 'proposal/plan.json')
    assert len(plan['pairs']) == 1 and plan['pairs'][0]['retained_vehicle_id'] == 'od_0266_v00020'
    assert plan['pairs'][0]['removed_vehicle_id'] == 'od_0271_v00019'
    witnesses = {r['id']: r for r in load(PREVIOUS / 'proposal/source-witnesses.json')}
    report = prior.audit_run(case / NAME, PREVIOUS / 'baseline', plan, witnesses)
    baseline = prior.helper.periodic_counts(PREVIOUS / 'baseline/native/field-counts.xml')
    candidate = prior.helper.periodic_counts(case / NAME / 'evaluation/native/field-counts.xml')
    before_attributes = {tuple(r.get(k) for k in ('id', 'begin', 'end')): r.attrib
        for r in ET.fromstring(raw(PREVIOUS / 'baseline/native/field-counts.xml')).findall('interval')}
    after_attributes = {tuple(r.get(k) for k in ('id', 'begin', 'end')): r.attrib
        for r in ET.fromstring(raw(case / NAME / 'evaluation/native/field-counts.xml')).findall('interval')}
    report['all208_nVehContrib_equal_to_reference'] = candidate == baseline
    report['all208_full_interval_attributes_equal_to_reference'] = after_attributes == before_attributes
    report['changed_nVehContrib_rows'] = sum(candidate[k] != baseline[k] for k in baseline)
    report['changed_full_attribute_rows'] = sum(after_attributes[k] != before_attributes[k] for k in before_attributes)
    return report


def verify(case):
    verify_manifest(case)
    prior.verify_manifest(PREVIOUS)
    results = load(case / 'results.json')
    assert sha(PREVIOUS / 'manifest.json') == results['previous_manifest_sha256']
    assert audit(case) == results['evaluations'][NAME]
    for folder in ('proposal', 'response-audit'):
        check_sealed(case / folder)
    reviewed = load(case / 'response-audit/complete/scan.json')
    assert reviewed['status'] == 'pass' and reviewed['native_runs'] == 0
    report = results['evaluations'][NAME]
    actual = reviewed['new_service_pair']
    assert actual['status'] == 'pass' and actual['raw_verdict'] == report['status']
    assert actual['metrics130']['SSE'] == report['main130']['SSE'] and actual['metrics182']['TAE'] == report['all182']['TAE']
    assert actual['completion'] == report['completion'] and actual['all_modified_dual_complete'] == report['dual_complete']
    proof = load(case / 'response-audit/complete/exact-count-equality-proof.json')
    assert proof['status'] == 'pass' and proof['new_native_runs'] == 0
    assert report['all208_nVehContrib_equal_to_reference'] and report['changed_nVehContrib_rows'] == 0
    for name, expected in (('main130', 130), ('initial52', 52), ('all182', 182), ('native208', 208)):
        assert proof['exact_row_count_equality'][name] == dict(rows=expected, nVehContrib_differences=[])
    assert proof['all40_derived_count_rows_identical'] and rows(case / NAME / 'evaluation/station-comparison.csv') == rows(PREVIOUS / 'baseline/evaluation/station-comparison.csv')
    assert len(proof['other_E1_attribute_differences']) == report['changed_full_attribute_rows'] == 1
    for source, expected in load(case / 'response-audit/reused-analysis-implementation.json')['source_helpers'].items():
        assert raw_sha(PREVIOUS / 'response-audit' / prior.basename(source)) == expected
    for row in load(case / 'source-provenance.json')['snapshots']:
        value = raw(case / row['snapshot']) if not row['snapshot'].endswith('.gz') else gzip.decompress((case / row['snapshot']).read_bytes())
        assert len(value) == row['raw_bytes'] and hashlib.sha256(value).hexdigest() == row['raw_sha256']
    preflight = load(case / NAME / 'preflight.json')
    for source, expected in preflight['source_hashes'].items():
        name = prior.basename(source)
        if name in {'demand.rou.xml', 'lane-intentions.json', 'od-vector.json', 'evaluation.json'}:
            path = PREVIOUS / 'baseline/evaluation' / name
        elif name in {'network.net.xml', 'control.add.xml', 'field-loops.add.xml', 'expected-counts.csv', 'initialization-counts.csv', 'scenario.json'}:
            path = PREVIOUS / 'baseline/prepared' / name
        else:
            path = {'plan.json': case / 'proposal/plan.json', 'multi-field-witnesses.json': PREVIOUS / 'proposal/source-witnesses.json', 'measure.py': case / 'measure.py'}[name]
        assert raw_sha(path) == expected
    compact = json.loads(gzip.decompress(raw(case / NAME / 'evaluation/contributing-passages.json.gz')))
    stats = next(r for r in proof['complete_event_stats'] if r['case'] == 'service_pair')
    assert stats['complete_events'] == len(compact['passages']) == report['event_count'] == 8626
    assert stats['unique_complete_vehicles'] == len({r['vehicle_id'] for r in compact['passages']}) == 8625
    local = load(case / 'local-only-large-traces.json')['files']
    assert next(r['sha256'] for r in local if prior.basename(r['path']) == 'instant-e1.xml') in {r['sha256'] for r in compact['inputs']}
    print('Manifest, exact input scope, native 208/130/40/52/182, completion, compact events and verdict: pass', flush=True)


def freeze():
    assert not (HERE / 'manifest.json').exists(), 'Preserve an existing frozen packet.'
    assert (SOURCE / NAME / 'verdict.json').is_file(), 'Wait for the terminal verdict.'
    assert load(SOURCE / 'response-audit/complete/scan.json')['status'] == 'pass', 'Wait for the complete sealed response audit.'
    prior.verify_manifest(PREVIOUS)
    for folder in ('proposal', 'response-audit'):
        check_sealed(SOURCE / folder)
    snapshots, local = [], []

    def copy(p, relative):
        original = p.read_bytes()
        value = gzip.decompress(original) if p.suffix == '.gz' else original
        target = HERE / relative
        if p.suffix != '.gz':
            target = target.with_name(target.name + '.gz')
        assert not target.exists(), 'Preserve any failed partial freeze.'
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(original if p.suffix == '.gz' else gzip.compress(original, mtime=0))
        snapshots.append(dict(source=str(p), snapshot=target.relative_to(HERE).as_posix(), raw_bytes=len(value), raw_sha256=hashlib.sha256(value).hexdigest()))

    for folder in ('proposal', 'response-audit', 'offline-substitute-1pair', NAME):
        for p in sorted((SOURCE / folder).rglob('*')):
            if not p.is_file() or '__pycache__' in p.parts:
                continue
            if p.name in {'instant-e1.xml', 'queues.xml'}:
                local.append(dict(path=str(p), bytes=p.stat().st_size, sha256=sha(p)))
            else:
                copy(p, p.relative_to(SOURCE))
    for p in sorted(SOURCE.iterdir()):
        if p.is_file():
            copy(p, Path(p.name))
    report = audit(HERE)
    save(HERE / 'results.json', dict(formal_result=False, new_native_evaluations=1, previous_manifest_sha256=sha(PREVIOUS / 'manifest.json'),
        evaluations={NAME: report}, source_selected_retained=True, inherited_audit_and_replay_functions=True))
    save(HERE / 'source-provenance.json', dict(snapshots=snapshots, historical_paths_are_provenance_only=True))
    save(HERE / 'local-only-large-traces.json', dict(files=local))
    (HERE / '.gitattributes').write_text('* -text whitespace=blank-at-eol,blank-at-eof,space-before-tab,cr-at-eol\n*.gz binary\n', encoding='utf-8')
    text = f'''# 汉堡同队列服务配对反事实

本次从原selected输入出发，保留od_0266_v00020的原出发属性并延长合法尾段至535Z4，省去同h12的一辆独立535Z4请求od_0271_v00019。该车是上一已拒绝变体观察到的左转队列前车。此处是8640→8639辆的显式行程替换，预期断面事件库存按14个发车时段仍守恒；不是原OD模型的数量更新，也不证明真实行程对应或自然选道。

| 原生结果 | 原selected参考 | 本次服务配对 |
|---|---:|---:|
| 请求数 | 8640 | 8639 |
| 130 SSE / MAE / 最大误差 | 6898 / 5.092308 / 27 | 6898 / 5.092308 / 27 |
| 40派生站点MAE / 最大误差 | 14.8 / 48 | 14.8 / 48 |
| 182 TAE | 744 | 744 |
| 未入网 / 在途 | 7 / 14 | 7 / 14 |
| 已插入 / 已到达 | 8633 / 8619 | 8632 / 8618 |
| 碰撞 / 传送 / 丢弃 | 0 / 0 / 0 | 0 / 0 / 0 |

原verdict为rejected，严格计数改善条件未满足，原selected继续保留。全部208项nVehContrib与参考完全相同：{report['all208_nVehContrib_equal_to_reference']}；完整interval全部属性相同：{report['all208_full_interval_attributes_equal_to_reference']}。两者含义分开记录，不能用相同误差标量替代逐行核对。全部双断面完整贡献已实现，前缀156行保持，但healthy=false，主130项零误差目标仍未达到。

完整贡献事件仍为8626，来自不同车辆的数量由8626变为8625，其中一车贡献两个字段。末端21辆的身份、未插入/在途状态、请求入口和意向字段与参考相同。只有一个E1区间的speed、occupancy和harmonicMeanSpeed改变。该固定seed、控制与driver条件下出现了“请求数不同、计数向量相同”的实例；它位于扩展的显式路线支持，不证明原21OD模型内所有参数不可识别，也不表示全过程等价。

本包只冻结1次已完成原生测量，运行258.924秒，原步长0.1秒、终点14400秒、seed104、libsumo1.27.1、道路、控制、车型、原字段hold和4节点车身hold不变。完整分群贡献及末端身份见response-audit/complete。离线prepare记录不计作原生运行。此有限反事实不能识别真实OD、证明普遍等价或给出现场控制结论。

prepared、原请求与实现快照、原生208E1及130/40/52/182、summary/trips/routes/collisions/全部警告日志、完整compact贡献均保留。instant和queues仅存本机哈希。范围、计数与复跑直接复用前substitution冻结包函数；原参考、witness及函数包由前包manifest绑定，历史绝对路径只作来源。proposal中的“尚未物化”是原提案时的状态，与后续原生结果分开保留。

离线核验：`.venv/Scripts/python.exe benchmarks/hamburg_ring1_multifield_service_pair_20261023_v1/freeze_evidence.py --verify-only`。

只有显式调用reproduce.py并指定新目录才通过既有固定需求复跑入口启动1次完整原生运行：`.venv/Scripts/python.exe benchmarks/hamburg_ring1_multifield_service_pair_20261023_v1/reproduce.py --output-dir outputs/service-pair-reproduction`。不重建21OD模型。

OSM：© OpenStreetMap contributors，ODbL 1.0。
'''
    (HERE / 'README.md').write_text(text, encoding='utf-8')
    assert all(hashlib.sha256(gzip.decompress(Path(r['source']).read_bytes()) if Path(r['source']).suffix == '.gz' else Path(r['source']).read_bytes()).hexdigest() == r['raw_sha256'] for r in snapshots), 'A sealed source changed; preserve this partial freeze.'
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
