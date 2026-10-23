"""Freeze only the terminal GS266 lane-level foe comparison; never start SUMO."""
import argparse
import gzip
import hashlib
from importlib.util import module_from_spec, spec_from_file_location
import json
from pathlib import Path
import shutil
import xml.etree.ElementTree as ET

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2] if HERE.name == 'lane-foes-freeze' else HERE.parents[1]
SOURCE = REPO / 'outputs/hamburg-119-receiving-diagnosis-20261023-v1'
TARGET = REPO / 'benchmarks/hamburg_ring1_119_lane_foes_20261023_v1'
FAMILY = REPO / 'benchmarks/hamburg_ring1_119_receiving_priority_20261023_v1'
BASELINE = FAMILY / 'full-priority'
spec = spec_from_file_location('frozen_GS_priority', FAMILY / 'freeze_evidence.py')
family = module_from_spec(spec)
spec.loader.exec_module(family)
raw, load, rows, sha, raw_sha = family.raw, family.load, family.rows, family.sha, family.raw_sha
verify_manifest, audit_measurement = family.verify_manifest, family.audit_measurement
FIELDS535 = {'hh_0535_Z_5_30894', 'hh_0535_Z_6_33855', 'hh_0535_Z_7_29316', 'hh_0535_Z_8_34287'}


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')


def safe_relative(name):
    path = Path(name)
    assert not path.anchor and '..' not in path.parts and name not in ('', '.'), 'Unsafe manifest path'
    return path


def manifest_paths(directory):
    manifest = load(directory / 'manifest.json')
    result = []
    for item in manifest['files']:
        name = safe_relative(item['path'])
        value = raw(directory / name)
        assert len(value) == item.get('bytes', item.get('size_bytes')) and hashlib.sha256(value).hexdigest() == item['sha256']
        result.append(name)
    return [*result, Path('manifest.json')]


def selected_paths(source):
    plan = load(HERE / 'freeze-whitelist.json')
    selected = set(map(Path, plan['explicit_files']))
    for folder in plan['manifest_directories']:
        selected.update(Path(folder) / p for p in manifest_paths(source / folder))
    for folder in plan['candidate_directories']:
        for name in plan['candidate_file_names']:
            if (source / folder / name).is_file():
                selected.add(Path(folder) / name)
    selected.update(Path('full-lane-foes') / name for name in plan['full_root_names'] if (source / 'full-lane-foes' / name).is_file())
    for folder, names in [('prepared', plan['prepared_names']), ('evaluation', plan['evaluation_names']), ('evaluation/native', plan['native_names']), ('implementation', plan['implementation_names'])]:
        selected.update(Path('full-lane-foes') / folder / name for name in names)
    selected.update(Path('full-lane-foes/independent-audit') / p for p in manifest_paths(source / 'full-lane-foes/independent-audit'))
    return sorted(selected)


def audit_packet(case):
    proof = load(case / 'candidate-lane-foes-v1/manifest.json')
    assert proof['status'] == 'review_required' and len(proof['checks']) == 10 and all(proof['checks'].values())
    assert len(proof['lane_foe_checks']) == 5 and all(proof['lane_foe_checks'].values())
    assert raw_sha(FAMILY / 'build_priority_candidate.py') == proof['builder_hashes']['build_priority_candidate.py']
    assert raw_sha(case / 'build_lane_foes_candidate.py') == proof['builder_hashes']['build_lane_foes_candidate.py']
    full = case / 'full-lane-foes'
    assert load(full / 'runtime.json')['status'] == 'terminal'
    assert raw_sha(full / 'prepared/network.net.xml') == proof['candidate_sha256']
    assert raw_sha(full / 'prepared/control.add.xml') == proof['control_sha256'] == raw_sha(BASELINE / 'prepared/control.add.xml')
    for name in ('demand.rou.xml', 'lane-intentions.json', 'field-loops.add.xml', 'expected-counts.csv', 'initialization-counts.csv'):
        assert raw(full / 'prepared' / name) == raw(BASELINE / 'prepared' / name)
    old, new = [ET.fromstring(raw(p)) for p in (BASELINE / 'prepared/network.net.xml', full / 'prepared/network.net.xml')]
    target = next(j for j in new.findall('junction') if j.get('id') == '266199063')
    requests = target.findall('request')
    expected = {(0, 2), (1, 2), (1, 3)}
    assert len(requests) == 6 and [dict(r.attrib) for r in requests] == proof['requests']
    pairs = {(i, j) for i in (0, 1) for j in (2, 3, 4, 5) if requests[i].get('foes')[::-1][j] == '1'}
    assert pairs == expected
    def key(c):
        return c.get('from'), c.get('fromLane'), c.get('to'), c.get('toLane')
    old_connections = {key(c): c for c in old.findall('connection')}
    changes = []
    for c in new.findall('connection'):
        previous = old_connections[key(c)]
        if c.get('state') != previous.get('state'):
            changes.append(key(c))
            assert previous.get('state') == 'm' and c.get('state') == 'M'
            c.set('state', 'm')
    assert set(changes) == {('79014037#1', '1', '186713429', '2'), ('79014037#1', '1', '186713429', '3')}
    for tree in (old, new):
        junction = next(j for j in tree.findall('junction') if j.get('id') == '266199063')
        for row in junction.findall('request'):
            junction.remove(row)
    assert ET.canonicalize(ET.tostring(old, encoding='unicode'), strip_text=True) == ET.canonicalize(ET.tostring(new, encoding='unicode'), strip_text=True)
    signatures = {r['vehicle_id']: [r['field_id']] for r in load(full / 'prepared/lane-intentions.json')['vehicles']}
    assert len(signatures) == 8640
    report, _, extra, repeats = audit_measurement(full / 'prepared', full / 'evaluation', signatures)
    verdict = load(full / 'verdict.json')
    assert verdict['after'] == load(full / 'evaluation/evaluation.json') and verdict['before'] == load(BASELINE / 'evaluation/evaluation.json')
    assert verdict['all182_TAE'] == report['all182']['TAE']
    assert bool(verdict['contributions_outside_prior_primary_intention']) == bool(extra) and bool(verdict['repeated_vehicle_fields']) == bool(repeats)
    counts = family.family.prior.helper.periodic_counts(full / 'evaluation/native/field-counts.xml')
    real535 = [r for r in rows(full / 'prepared/initialization-counts.csv') + rows(full / 'prepared/expected-counts.csv') if r['detector_id'] in FIELDS535]
    assert len(real535) == 56
    error535 = sum(abs(counts[family.family.prior.helper.key(r)] - int(r['expected_total'])) for r in real535)
    assert verdict['535_all56']['candidate']['TAE'] == error535
    source_manifest = load(case / 'native-source/lane-foes-manifest.json')
    for row in source_manifest['files']:
        value = raw(case / 'native-source' / safe_relative(row['path']))
        assert len(value) == row['bytes'] and hashlib.sha256(value).hexdigest() == row['sha256']
    geometry = rows(case / 'native-source/lane-foes-geometry.csv')
    cross = [r for r in geometry if r['cross_incoming_pair'] == 'True']
    assert len(geometry) == 15 and len(cross) == 8
    assert {(int(r['request_a']), int(r['request_b'])) for r in cross if r['expected_lane_conflict_if_all_true'] == 'True'} == expected
    analysis = load(case / 'native-source/lane-foes-analysis.json')
    dependencies = []
    for item in analysis['sources']:
        if item['path'].startswith('outputs/hamburg-197-through-support-20261023-v1/'):
            relative = item['path'].replace('outputs/hamburg-197-through-support-20261023-v1/', '')
            p = REPO / 'benchmarks/hamburg_ring1_197_through_support_20261023_v1' / relative
            reference_name = p.relative_to(REPO).as_posix()
        else:
            p = case / 'native-source/NBFrame.cpp'
            reference_name = 'native-source/NBFrame.cpp'
        assert raw_sha(p) == item['sha256']
        dependencies.append({'path': reference_name, 'raw_sha256': item['sha256']})
    smoke = load(case / 'lane-foes-smoke/audit.json')
    assert smoke['status'] == 'pass' and smoke['native_runs'] == 3 and smoke['toy_vehicles'] == 6
    summary = load(case / 'full-lane-foes-independent/summary.json')
    assert summary['schema'] == 'astra.hamburg.119-lane-foes-independent/v1' and summary['audit_status'] == 'pass'
    for local_key, reviewed_key in [('main130', 'metrics130'), ('initial52', 'metrics52'), ('all182', 'metrics182')]:
        assert all(summary['after'][reviewed_key][k] == v for k, v in report[local_key].items())
    assert summary['after']['completion'] == report['completion']
    return {'status': 'review_required', 'formal_result': verdict['formal_result'], 'raw_verdict': verdict['status'],
        'selection_decision': 'not_adopted', 'retained_working_reference': '42a827e/full-priority:healthy-MAE3.7153846153846155',
        'selection_claim_boundary': 'Root rejected this paired full case. Preserve raw diagnostic status; this is not a claim that the physical option is wrong or zero error infeasible.',
        'reference': load(FAMILY / 'results.json')['audit']['full'], 'full': report, 'all535_real56_TAE': error535,
        'retained_conflicts': [list(p) for p in sorted(expected)], 'excluded_cross_pairs': [list(p) for p in sorted({(i,j) for i in (0,1) for j in (2,3,4,5)} - expected)],
        'connection_priority_changes': [list(p) for p in sorted(changes)], 'source_dependencies': dependencies,
        'candidate_network_sha256': proof['candidate_sha256'], 'candidate_control_sha256': proof['control_sha256'],
        'claim_boundary': 'Single lane-level foe precision factor within the same conditional GS priority model. Physical signal ownership and603-major ROW remain unknown. No keepClear/geometry/control/OD change. Sparse smoke does not establish saturation or reverse-arrival safety; no calibration completion claim.'}


def verify(case):
    verify_manifest(case)
    family.verify_manifest(FAMILY)
    result = load(case / 'results.json')
    assert sha(FAMILY / 'manifest.json') == result['family_manifest_sha256']
    for row in load(case / 'source-provenance.json')['snapshots']:
        value = raw(case / row['relative'])
        assert len(value) == row['raw_bytes'] and hashlib.sha256(value).hexdigest() == row['raw_sha256']
    assert audit_packet(case) == result['audit']
    print('Manifest, single ROW factor, fixed8640/native208/130/40/52/182/53556, source and terminal audits: pass', flush=True)


def freeze():
    assert not TARGET.exists(), 'Preserve any existing frozen or partial packet.'
    assert load(SOURCE / 'full-lane-foes/runtime.json')['status'] == 'terminal', 'Wait for terminal; never read live native output.'
    family.verify_manifest(FAMILY)
    chosen = selected_paths(SOURCE)
    audit = audit_packet(SOURCE)
    TARGET.mkdir()
    provenance, local = [], []
    local_names = set(load(HERE / 'freeze-whitelist.json')['local_only_names'])
    for name in chosen:
        p = SOURCE / name
        assert p.is_file()
        if p.name in local_names:
            local.append({'relative': name.as_posix(), 'source': str(p), 'bytes': p.stat().st_size, 'sha256': sha(p)})
            continue
        value = p.read_bytes()
        snapshot = TARGET / (name.as_posix()+'.gz')
        snapshot.parent.mkdir(parents=True, exist_ok=True)
        snapshot.write_bytes(gzip.compress(value, mtime=0))
        provenance.append({'relative': name.as_posix(), 'snapshot': snapshot.relative_to(TARGET).as_posix(), 'source': str(p), 'raw_bytes': len(value), 'raw_sha256': hashlib.sha256(value).hexdigest()})
    for name in ('freeze_evidence.py', 'freeze-whitelist.json', 'reproduce.py', 'test_freeze_evidence.py'):
        shutil.copyfile(HERE / name, TARGET / name)
    save(TARGET / 'source-provenance.json', {'snapshots': provenance, 'single_lane_foes_factor_only': True})
    save(TARGET / 'local-only-large-traces.json', {'files': local, 'old_GS_chain_referenced_not_duplicated': True})
    save(TARGET / 'results.json', {'formal_result': False, 'family_manifest_sha256': sha(FAMILY / 'manifest.json'), 'audit': audit})
    save(TARGET / 'source-references.json', {'family': str(FAMILY.relative_to(REPO)), 'family_commit': '42a827e', 'manifest_sha256': sha(FAMILY / 'manifest.json'),
        'source_network_sha256': raw_sha(BASELINE / 'prepared/network.net.xml'), 'source_control_sha256': raw_sha(BASELINE / 'prepared/control.add.xml'),
        'demand_sha256': raw_sha(BASELINE / 'prepared/demand.rou.xml'), 'portable_requirement': 'Use this repository with its frozen197 benchmark and declared project .venv runtime. Historical absolute source paths are provenance, not relocation instructions.'})
    (TARGET / '.gitattributes').write_text('* -text whitespace=blank-at-eol,blank-at-eof,space-before-tab,cr-at-eol\n*.gz binary\n', encoding='utf-8')
    score, baseline = audit['full'], audit['reference']
    (TARGET / 'README.md').write_text(f'''# 119接收段lane-level foes单因素对照

本包只冻结GS266既有priority条件模型中的lane-level foe精度因素。基准为42a827e冻结的GS包（主MAE3.7153846，8640辆健康完成），旧observer、source-audit和full-priority均按固定包及哈希引用，不重复复制。

原生转换开启check-lane-foes.all，仅改变GS的6行ROW请求表和790 lane1→186 lanes2/3两项m→M。控制原始字节、全部几何/长度/道路/权限/合法转向、8640需求与意图、真实130主窗和52初始化计数不变。保留3个真实共享出口/交叉对(0,2)/(1,2)/(1,3)，排除其余5个跨入口对；该精度因素仍继承独立GS控制归属及603优先的未知现场依据，不是现场SPaT。

原生记录原判定 `{audit['raw_verdict']}`，本轮选用结论为**不采用**；保留健康的3.7153846基准。主130项MAE由 {baseline['main130']['MAE']} 变为 {score['main130']['MAE']}，SSE由 {baseline['main130']['SSE']} 变为 {score['main130']['SSE']}。初始化52项TAE由 {baseline['initial52']['TAE']} 变为 {score['initial52']['TAE']}；全182项TAE由 {baseline['all182']['TAE']} 变为 {score['all182']['TAE']}。waiting/running为 {score['completion']['waiting']}/{score['completion']['running']}，535指定56项TAE为 {audit['all535_real56_TAE']}。完整130/40/52/182/208、每字段退步及未完成车辆都保留。初始化改善和无碰撞不能代替主130、全182及未完成情况的整体判定。本配对输入和控制下结果恶化；不据此断言物理选项错误或零误差不可行。

静态CSV保留全部15对，其中8对跨入口。三次共6车smoke只按封存README解释：Z5/603车身同时处于GS范围的不同车道，不是车身重叠；REQ0/3尚未覆盖同时内部占用，只证明这次opened/foe状态和安全到达。RQ5、反向到达、饱和tiny仍未覆盖，不能外推全场景安全。历史静态分析中的未构造/未测字样属于当时阶段，不改写为后续结果。

源码依赖及基础builder按固定哈希引用旧包；新增builder、candidate、说明、微试验和本次完整输入/关键native/独立audit按原字节压缩。大queues/instant等仅local-only哈希，不以哈希代替逐事件数据；完整贡献JSON保留。原始绝对路径只作溯源。离线verify和7项测试不启动SUMO。显式reproduce.py需新output-dir、同仓库固定包、项目.venv/Libsumo1.27.1及固定源实现；只复跑本候选，不生成OD或另改控制。

离线核验：`.venv/Scripts/python.exe benchmarks/hamburg_ring1_119_lane_foes_20261023_v1/freeze_evidence.py --verify-only`。
''', encoding='utf-8')
    assert all(sha(Path(r['source'])) == r['raw_sha256'] for r in provenance)
    save(TARGET / 'manifest.json', {'formal_result': False, 'files': [{'path': p.relative_to(TARGET).as_posix(), 'bytes': p.stat().st_size, 'sha256': sha(p)} for p in sorted(TARGET.rglob('*')) if p.is_file() and p != TARGET / 'manifest.json' and '__pycache__' not in p.parts]})
    verify(TARGET)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--freeze', action='store_true')
    mode.add_argument('--verify-only', action='store_true')
    args = parser.parse_args()
    freeze() if args.freeze else verify(TARGET)
