"""Freeze only the terminal GS266 hypothesis chain; never start SUMO."""
import argparse
import gzip
import hashlib
from importlib.util import module_from_spec, spec_from_file_location
import json
from pathlib import Path
import shutil
import xml.etree.ElementTree as ET

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
SOURCE = REPO / 'outputs/hamburg-119-receiving-diagnosis-20261023-v1'
TARGET = REPO / 'benchmarks/hamburg_ring1_119_receiving_priority_20261023_v1'
FAMILY = REPO / 'benchmarks/hamburg_ring1_197_through_support_20261023_v1'
spec = spec_from_file_location('frozen197', FAMILY / 'freeze_evidence.py')
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
    selected = set(map(Path, plan['explicit_files'] + plan['failed_preparation_files']))
    for folder in plan['manifest_directories']:
        selected.update(Path(folder) / p for p in manifest_paths(source / folder))
    for folder in plan['candidate_directories']:
        for name in plan['candidate_file_names']:
            if (source / folder / name).is_file():
                selected.add(Path(folder) / name)
        assert source.joinpath(folder, 'manifest.json').is_file()
    selected.update(Path('full-priority') / name for name in plan['full_root_names'] if (source / 'full-priority' / name).is_file())
    for folder, names in [('prepared', plan['prepared_names']), ('evaluation', plan['evaluation_names']), ('evaluation/native', plan['native_names']), ('implementation', plan['implementation_names'])]:
        selected.update(Path('full-priority') / folder / name for name in names)
    selected.update(Path('full-priority/independent-audit') / p for p in manifest_paths(source / 'full-priority/independent-audit'))
    selected.update(Path(name) for name in plan['optional_top_files'] if (source / name).is_file())
    assert set(map(Path, plan['always_archive'])) <= selected
    return sorted(selected)


def audit_packet(case):
    proof = load(case / 'candidate-priority-v3/manifest.json')
    assert proof['status'] == 'review_required' and len(proof['checks']) == 10 and all(proof['checks'].values())
    full = case / 'full-priority'
    assert load(full / 'runtime.json')['status'] == 'terminal'
    assert raw_sha(full / 'prepared/network.net.xml') == proof['candidate_sha256']
    assert raw_sha(full / 'prepared/control.add.xml') == proof['control_sha256']
    for name in ('demand.rou.xml', 'lane-intentions.json', 'field-loops.add.xml', 'expected-counts.csv', 'initialization-counts.csv'):
        assert raw(full / 'prepared' / name) == raw(FAMILY / 'full/prepared' / name)
    def lengths(path):
        tree = ET.fromstring(raw(path))
        return {lane.get('id'): float(lane.get('length')) for edge in tree.findall('edge') for lane in edge.findall('lane')}
    assert lengths(full / 'prepared/network.net.xml') == lengths(FAMILY / 'full/prepared/network.net.xml')
    assert lengths(full / 'prepared/network.net.xml')['79014037#1_1'] == 3.46
    old, new = [load(p) for p in (FAMILY / 'full/prepared/scenario.json', full / 'prepared/scenario.json')]
    assert new['control_assumptions'].startswith(old['control_assumptions']+' Additional GS266 hypothesis: ')
    signatures = {r['vehicle_id']: [r['field_id']] for r in load(full / 'prepared/lane-intentions.json')['vehicles']}
    assert len(signatures) == 8640
    report, _, extra, repeats = audit_measurement(full / 'prepared', full / 'evaluation', signatures)
    verdict = load(full / 'verdict.json')
    assert verdict['after'] == load(full / 'evaluation/evaluation.json')
    assert verdict['before'] == load(FAMILY / 'full/evaluation/evaluation.json')
    assert verdict['all182_TAE'] == report['all182']['TAE']
    assert bool(verdict['contributions_outside_prior_primary_intention']) == bool(extra)
    assert bool(verdict['repeated_vehicle_fields']) == bool(repeats)
    counts = family.prior.helper.periodic_counts(full / 'evaluation/native/field-counts.xml')
    real535 = [r for r in rows(full / 'prepared/initialization-counts.csv') + rows(full / 'prepared/expected-counts.csv') if r['detector_id'] in FIELDS535]
    assert len(real535) == 56
    error535 = sum(abs(counts[family.prior.helper.key(r)] - int(r['expected_total'])) for r in real535)
    assert verdict['535_all56']['candidate']['TAE'] == error535
    record = load(case / 'observer/baseline/record-result.json')
    assert record['status'] == 'pass' and len(record['checks']) == 13 and all(record['checks'].values())
    assert [record[k] for k in ('closed_e1_rows', 'summary_steps_checked', 'instant_events_checked', 'arrived_routes_checked')] == [104, 72000, 69526, 4341]
    samples = raw(case / 'observer/baseline/read-only-samples.jsonl')
    assert len(samples.splitlines()) == record['coverage']['captured_frames'] == 502
    analysis = load(case / 'analysis/analysis-proof.json')
    sample_hash = next(v for k, v in analysis['source_hashes'].items() if k.endswith('read-only-samples.jsonl'))
    assert hashlib.sha256(samples).hexdigest() == sample_hash
    assert analysis['status'] == 'pass' and analysis['frames'] == 502
    native_source = load(case / 'native-source/source.json')
    assert raw_sha(case / 'native-source/MSVehicle.cpp') == native_source['sha256']
    independent = []
    for p in manifest_paths(case / 'full-priority-independent'):
        if p.suffix == '.json':
            value = load(case / 'full-priority-independent' / p)
            if isinstance(value, dict) and value.get('schema') == 'astra.hamburg.119-priority-independent/v1':
                independent.append(value)
    assert len(independent) == 1 and independent[0]['audit_status'] == 'pass'
    for local_key, reviewed_key in [('main130', 'metrics130'), ('initial52', 'metrics52'), ('all182', 'metrics182')]:
        assert all(independent[0]['after'][reviewed_key][k] == v for k, v in report[local_key].items())
    assert independent[0]['after']['completion'] == report['completion']
    assert independent[0]['53556_TAE_after'] == error535
    smoke = load(case / 'merge-smoke/manifest.json')
    assert smoke['status'] == 'pass' and smoke['new_native_runs'] == 2
    return {'status': 'review_required', 'formal_result': verdict['formal_result'], 'raw_verdict': verdict['status'],
        'reference': load(FAMILY / 'results.json')['full'], 'full': report, 'all535_real56_TAE': error535, 'prefix': {'checks': 13, 'frames': 502, 'closed_E1': 104, 'summary_steps': 72000, 'instant_events': 69526, 'arrived_routes': 4341},
        'candidate_network_sha256': proof['candidate_sha256'], 'candidate_control_sha256': proof['control_sha256'],
        'claim_boundary': 'Conditional inferred GS signal to priority operation, including unknown603-major/790-minor field ROW. Count fit and sparse smoke do not establish physical signal ownership or field correctness.'}


def verify(case):
    verify_manifest(case)
    family.verify_manifest(FAMILY)
    result = load(case / 'results.json')
    assert sha(FAMILY / 'manifest.json') == result['family_manifest_sha256']
    for row in load(case / 'source-provenance.json')['snapshots']:
        value = raw(case / row['relative'])
        assert len(value) == row['raw_bytes'] and hashlib.sha256(value).hexdigest() == row['raw_sha256']
    assert audit_packet(case) == result['audit']
    print('Manifest, fixed8640/source lengths, native208/130/40/52/182/53556, prefix getter502 and terminal audits: pass', flush=True)


def freeze():
    assert not TARGET.exists(), 'Preserve any existing frozen or partial packet.'
    assert load(SOURCE / 'full-priority/runtime.json')['status'] == 'terminal', 'Wait for terminal; never read live native output.'
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
    save(TARGET / 'source-provenance.json', {'snapshots': provenance, 'single_GS_hypothesis_only': True})
    save(TARGET / 'local-only-large-traces.json', {'files': local, 'getter_JSONL_archived': True})
    save(TARGET / 'results.json', {'formal_result': False, 'family_manifest_sha256': sha(FAMILY / 'manifest.json'), 'audit': audit})
    save(TARGET / 'source-references.json', {'family': str(FAMILY.relative_to(REPO)), 'manifest_sha256': sha(FAMILY / 'manifest.json'),
        'source_network_sha256': raw_sha(FAMILY / 'full/prepared/network.net.xml'), 'source_control_sha256': raw_sha(FAMILY / 'full/prepared/control.add.xml'),
        'demand_sha256': raw_sha(FAMILY / 'full/prepared/demand.rou.xml'), 'portable_requirement': 'Use this repository with its frozen197 benchmark and declared project .venv runtime. Historical absolute source paths are provenance, not relocation instructions.'})
    (TARGET / '.gitattributes').write_text('* -text whitespace=blank-at-eol,blank-at-eof,space-before-tab,cr-at-eol\n*.gz binary\n', encoding='utf-8')
    score, baseline = audit['full'], audit['reference']
    (TARGET / 'README.md').write_text(f'''# 119接收段GS266条件priority对照

本包只冻结一个GS控制归属假设链：来源审查、7200秒只读前缀、两车机制比较、保留六movement的两次共四车合流smoke、v1/v2范围失败与v3、首次准备错误和一次完整8640需求对照。没有把独立推断控制的改变称为现场修复。独立GS灯头/停线、603优先/790让行的现场依据仍未知；保留119及上游真实信号、全部道路和长度、六个合法movement及权限。

完整运行原判定 `{audit['raw_verdict']}`；主130项MAE由 {baseline['main130']['MAE']} 变为 {score['main130']['MAE']}、SSE由 {baseline['main130']['SSE']} 变为 {score['main130']['SSE']}，全182项TAE由 {baseline['all182']['TAE']} 变为 {score['all182']['TAE']}。初始化52项TAE由 {baseline['initial52']['TAE']} 增至 {score['initial52']['TAE']}，该退步随结果保留。119 Z4主窗TAE也由91增至128，未隐藏该字段恶化。waiting/running分别为 {score['completion']['waiting']}/{score['completion']['running']}，535指定四字段全部56项TAE {audit['all535_real56_TAE']}。完整130/40/52/182/208行、全部终端车辆和事故信息均保存，不按单一改善选用。正式零误差与健康完成不能由来源假设或smoke通过替代。

前缀13项一致性检查通过，502帧原生getter JSONL按原字节gzip入Git。getter标时、原生XML标时、opened/foe/priority与实际储车能力的边界见analysis与原生源码记录。没有私有DriveProcessItem观测，不把绿灯与停车单独当作根因证明。大queues、instant、TLS全记录及FCD只存local-only原始哈希，未删除本地原件；不能把本地哈希当逐样本数据。

本包依赖同仓库已冻结197包；原始源输入通过source-references绑定。全部当前输入和必要原生输出均按原字节压缩。历史绝对路径只作溯源。离线核验用项目 `.venv/Scripts/python.exe benchmarks/hamburg_ring1_119_receiving_priority_20261023_v1/freeze_evidence.py --verify-only`。冻结和测试不启动SUMO。仅显式运行reproduce.py并给全新output-dir才复测一次完整原生；它校验项目.venv、固定实现/Libsumo1.27.1及源哈希，不再生成或调整OD、不改变候选/相位。
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
