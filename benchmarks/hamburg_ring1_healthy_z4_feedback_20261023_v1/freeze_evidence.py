"""Freeze two terminal explicit Z4 departure feedback cases; never start SUMO."""
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
SOURCE = REPO / 'outputs/hamburg-healthy-z4-feedback-20261023-v1'
TARGET = REPO / 'benchmarks/hamburg_ring1_healthy_z4_feedback_20261023_v1'
FAMILY = REPO / 'benchmarks/hamburg_ring1_119_receiving_priority_20261023_v1'
BASELINE = FAMILY / 'full-priority'
RUNS = {'batch-1': 1, 'batch-4': 3}
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


def snapshot_relative(name):
    return Path('study-source/README.md') if Path(name) == Path('README.md') else Path(name)


def selected_paths(source):
    plan = load(HERE / 'freeze-whitelist.json')
    selected = set(map(Path, plan['explicit_files']))
    selected.update(Path('independent') / p for p in manifest_paths(source / 'independent'))
    for name in RUNS:
        for item in plan['case_root_names']:
            if (source / name / item).is_file():
                selected.add(Path(name) / item)
        for folder, names in [('prepared', plan['prepared_names']), ('evaluation', plan['evaluation_names']), ('evaluation/native', plan['native_names']), ('implementation', plan['implementation_names'])]:
            for item in names:
                if (source / name / folder / item).is_file():
                    selected.add(Path(name) / folder / item)
    return sorted(selected)


def audit_packet(case):
    baseline = load(FAMILY / 'results.json')['audit']['full']
    source_vehicles = {v.get('id'): v for v in ET.fromstring(raw(BASELINE / 'prepared/demand.rou.xml')).findall('vehicle')}
    source_intents = load(BASELINE / 'prepared/lane-intentions.json')
    old_intents = {r['vehicle_id']: r for r in source_intents['vehicles']}
    assert len(source_vehicles) == len(old_intents) == 8640
    source_vector = [sum(r['od_cell'] == c for r in old_intents.values()) for c in range(294)]
    true_z4 = rows(case / 'basis/z4-real14.csv')
    assert len(true_z4) == 14 and len({(r['detector_id'], r['begin'], r['end']) for r in true_z4}) == 14
    assert all(r['detector_id'] == 'hh_0119_Z_4_29437' for r in true_z4)
    helper = family.family.prior.helper
    real_source = {helper.key(r):r for r in rows(BASELINE / 'prepared/initialization-counts.csv') + rows(BASELINE / 'prepared/expected-counts.csv') if r['detector_id'] == 'hh_0119_Z_4_29437'}
    measured_source = helper.periodic_counts(BASELINE / 'evaluation/native/field-counts.xml')
    assert {helper.key(r) for r in true_z4} == real_source.keys()
    assert all(int(r['expected_total']) == int(real_source[helper.key(r)]['expected_total']) and int(r['measured_nVehContrib']) == measured_source[helper.key(r)] for r in true_z4)
    requests = load(case / 'basis/z4-requests.json')
    assert len(requests) == len({r['vehicle_id'] for r in requests}) == 761
    assert {r['vehicle_id'] for r in requests} == {i for i,r in old_intents.items() if r['field_id'] == 'hh_0119_Z_4_29437'}
    assert all(old_intents[r['vehicle_id']]['field_id'] == r['detector_id'] == 'hh_0119_Z_4_29437' and float(source_vehicles[r['vehicle_id']].get('depart')) == r['requested_depart_s'] for r in requests)
    cases = {}
    for name, actual_count in RUNS.items():
        folder, prepared = case / name, case / name / 'prepared'
        assert load(folder / 'scope-proof.json')['actual_changes'] == actual_count
        proposal = load(folder / 'proposal.json')
        moves = {r['vehicle_id']:r for r in proposal['proposals']}
        assert proposal['requested_cap'] == (1 if name == 'batch-1' else 4)
        assert proposal['actual_change_count'] == len(proposal['proposals']) == actual_count
        proof, preflight = load(folder / 'scope-proof.json'), load(folder / 'preflight.json')
        assert proof['status'] == 'pass' and proof['N'] == 8640
        assert all(raw_sha(prepared / p) == h for p,h in proof['prepared_hashes'].items())
        assert raw_sha(folder / 'scope-proof.json') == preflight['source_proof_sha256']
        assert raw_sha(folder / 'executed-driver.py') == preflight['executed_driver_sha256'] == raw_sha(case / 'run_candidate.py')
        for item in ('network.net.xml','control.add.xml','field-loops.add.xml','expected-counts.csv','initialization-counts.csv'):
            assert raw(prepared / item) == raw(BASELINE / 'prepared' / item)
        new_tree = ET.fromstring(raw(prepared / 'demand.rou.xml'))
        old_tree = ET.fromstring(raw(BASELINE / 'prepared/demand.rou.xml'))
        assert [ET.canonicalize(ET.tostring(v,encoding='unicode'),strip_text=True) for v in new_tree if v.tag != 'vehicle'] == [ET.canonicalize(ET.tostring(v,encoding='unicode'),strip_text=True) for v in old_tree if v.tag != 'vehicle']
        vehicles = {v.get('id'):v for v in new_tree.findall('vehicle')}
        assert vehicles.keys() == source_vehicles.keys()
        changed = set()
        for identity, current in vehicles.items():
            original = source_vehicles[identity]
            if current.get('depart') != original.get('depart'):
                changed.add(identity)
                assert abs(float(current.get('depart')) - moves[identity]['proposed_depart_s']) <= 1e-9
            current.set('depart', original.get('depart'))
            assert ET.canonicalize(ET.tostring(current, encoding='unicode'), strip_text=True) == ET.canonicalize(ET.tostring(original, encoding='unicode'), strip_text=True)
        assert changed == set(moves) and len(changed) == len(moves) == actual_count
        intents = load(prepared / 'lane-intentions.json')
        assert {k:v for k,v in intents.items() if k not in {'vehicles','mechanism'}} == {k:v for k,v in source_intents.items() if k not in {'vehicles','mechanism'}}
        assert intents['mechanism'].startswith(source_intents['mechanism'])
        assigned = {r['vehicle_id']:r for r in intents['vehicles']}
        assert assigned.keys() == old_intents.keys()
        assert all(r['field_id'] == old_intents[i]['field_id'] for i,r in assigned.items())
        assert all(r == old_intents[i] for i,r in assigned.items() if i not in changed)
        for identity in changed:
            row = assigned[identity]
            restored = dict(row)
            for key in ('depart_s','od_cell'):
                if key in old_intents[identity]:
                    restored[key] = old_intents[identity][key]
                else:
                    restored.pop(key,None)
            assert restored == old_intents[identity]
            assert abs(row['depart_s'] - moves[identity]['proposed_depart_s']) <= 1e-9
            assert row['od_cell'] == int(row['depart_s'] // 900) * 21 + old_intents[identity]['od_cell'] % 21
        vector = load(folder / 'od-vector.json')
        assert vector['values'] == vector['integer_counts'] == source_vector and sum(source_vector) == 8640 and len(source_vector) == 294
        assert raw(folder / 'od-vector.json') == raw(folder / 'evaluation/od-vector.json')
        verdict = load(folder / 'verdict.json')
        summary = load(case / 'independent' / name / 'summary.json')
        assert summary['schema'] == 'astra.hamburg.healthy-z4-independent/v1' and summary['audit_status'] == 'pass'
        if verdict['status'] == 'invalid':
            cases[name] = {'raw_verdict': 'invalid', 'actual_changes': actual_count, 'partial_outputs_preserved': True, 'error': verdict['error']}
            continue
        signatures = {i:[r['field_id']] for i,r in assigned.items()}
        report, _, extra, repeats = audit_measurement(prepared, folder / 'evaluation', signatures)
        score = verdict['after']
        for local_key, measured_key in [('main130','metrics130'),('initial52','metrics52'),('all182','metrics182')]:
            assert all(score[measured_key][k] == v for k,v in report[local_key].items())
        assert score['completion'] == report['completion']
        for local_key, measured_key in [('main130','metrics130'),('initial52','metrics52'),('all182','metrics182')]:
            assert all(summary['after'][measured_key][k] == v for k,v in report[local_key].items())
        assert summary['after']['completion'] == report['completion']
        assert bool(extra) == bool(verdict['contributions_outside_prior_primary_intention']) and bool(repeats) == bool(verdict['repeated_vehicle_fields'])
        stats535 = verdict['all53556']
        counts = family.family.prior.helper.periodic_counts(folder / 'evaluation/native/field-counts.xml')
        true535 = [r for r in rows(prepared / 'initialization-counts.csv') + rows(prepared / 'expected-counts.csv') if r['detector_id'] in FIELDS535]
        assert len(true535) == 56
        error535 = sum(abs(counts[family.family.prior.helper.key(r)] - int(r['expected_total'])) for r in true535)
        assert error535 == stats535['TAE']
        gates = {'safe': report['safe'], 'healthy': report['healthy'], 'all53556_zero': error535 == 0,
            'no_repeated_vehicle_fields': not repeats, 'no_contributions_outside_primary_intention': not extra,
            'main130_MAE_improves': report['main130']['MAE'] < baseline['main130']['MAE'],
            'main130_SSE_not_worse': report['main130']['SSE'] <= baseline['main130']['SSE'],
            'all182_TAE_not_worse': report['all182']['TAE'] <= baseline['all182']['TAE']}
        assert verdict['gates'] == gates and verdict['status'] == ('diagnostic_candidate' if all(gates.values()) else 'rejected')
        cases[name] = {'raw_verdict': verdict['status'], 'formal_result': verdict['formal_result'], 'actual_changes':actual_count,
            'requested_cap':proposal['requested_cap'], 'measurement':report, 'gates':gates, 'all53556_TAE':error535}
    return {'status':'review_required', 'formal_result':False, 'reference':baseline, 'cases':cases, 'selection_decision':'not_adopted' if all(r['raw_verdict'] in {'rejected','invalid'} for r in cases.values()) else 'report_runner_gates_without_promotion',
        'claim_boundary':'Bounded explicit Z4 departure changes, actual1 and3 vehicles. Projection is not native prediction; no quantile regeneration. All fields/windows and completion retained. Diagnostic fit does not identify trueOD or validate field control.'}


def verify(case):
    verify_manifest(case)
    family.verify_manifest(FAMILY)
    result = load(case / 'results.json')
    assert sha(FAMILY / 'manifest.json') == result['family_manifest_sha256']
    for row in load(case / 'source-provenance.json')['snapshots']:
        value = raw(case / row['relative'])
        assert len(value) == row['raw_bytes'] and hashlib.sha256(value).hexdigest() == row['raw_sha256']
    assert audit_packet(case) == result['audit']
    print('Manifest, actual1/3 explicit departures, native208/130/40/52/182/53556, runner gates and independent audits: pass', flush=True)


def freeze():
    assert not TARGET.exists(), 'Preserve any existing frozen or partial packet.'
    assert all((SOURCE / name / 'verdict.json').is_file() for name in RUNS), 'Wait for both terminal verdicts; never read active native output.'
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
        relative = snapshot_relative(name)
        snapshot = TARGET / (relative.as_posix()+'.gz')
        snapshot.parent.mkdir(parents=True, exist_ok=True)
        snapshot.write_bytes(gzip.compress(value, mtime=0))
        provenance.append({'relative': relative.as_posix(), 'snapshot': snapshot.relative_to(TARGET).as_posix(), 'source': str(p), 'raw_bytes': len(value), 'raw_sha256': hashlib.sha256(value).hexdigest()})
    scope = load(SOURCE / 'batch-1/scope-proof.json')
    for name in ('native_temporal_feedback.py','hamburg_departure_probe.py'):
        source = REPO / 'plugins/astra-sumo/src/astra_sumo/core' / name
        expected = next(h for p,h in scope['source_hashes'].items() if p.endswith(name))
        value = source.read_bytes()
        assert hashlib.sha256(value).hexdigest() == expected
        relative = Path('code-source') / name
        snapshot = TARGET / (relative.as_posix()+'.gz')
        snapshot.parent.mkdir(parents=True, exist_ok=True)
        snapshot.write_bytes(gzip.compress(value, mtime=0))
        provenance.append({'relative':relative.as_posix(),'snapshot':snapshot.relative_to(TARGET).as_posix(),'source':str(source),'raw_bytes':len(value),'raw_sha256':expected})
    review = REPO / 'outputs/hamburg-current-count-review-20261023-v1'
    if review.is_dir():
        figure_proof = load(review / 'source-proof.json')
        sources = [REPO / 'benchmarks/hamburg_ring1_197_through_support_20261023_v1/full-response-audit/complete/main-comparison.csv', BASELINE / 'independent-audit/main-comparison.csv']
        assert set(figure_proof['source_hashes'].values()) == {raw_sha(p) for p in sources}
        for side, path in enumerate(sources):
            errors = {(r['detector_id'], float(r['begin'])): int(r['diff_nVehContrib_minus_expected']) for r in rows(path)}
            assert figure_proof['native_residuals'][side] == [[errors[field,float(t)] for t in figure_proof['interval_starts_s']] for field in figure_proof['fields']]
        for name in ('plot_counts.py','source-proof.json','all130-before-after.png','all130-before-after.svg'):
            source, relative = review / name, Path('baseline-count-review') / name
            value = source.read_bytes()
            snapshot = TARGET / (relative.as_posix() if source.suffix in ('.png','.svg') else relative.as_posix()+'.gz')
            snapshot.parent.mkdir(parents=True, exist_ok=True)
            snapshot.write_bytes(value if source.suffix in ('.png','.svg') else gzip.compress(value, mtime=0))
            provenance.append({'relative':relative.as_posix(),'snapshot':snapshot.relative_to(TARGET).as_posix(),'source':str(source),'raw_bytes':len(value),'raw_sha256':hashlib.sha256(value).hexdigest()})
        save(TARGET / 'baseline-count-review/context.json', {'scope':'Previously completed197 versus healthy3.715 GS reference. These figures are not results of batch-1 or batch-4.','new_demand_trial_results':False})
    for name in ('freeze_evidence.py', 'freeze-whitelist.json', 'reproduce.py', 'test_freeze_evidence.py'):
        shutil.copyfile(HERE / name, TARGET / name)
    save(TARGET / 'source-provenance.json', {'snapshots': provenance, 'two_explicit_Z4_cases_only': True})
    save(TARGET / 'local-only-large-traces.json', {'files': local, 'old_source_native_referenced_not_duplicated': True})
    save(TARGET / 'results.json', {'formal_result': False, 'family_manifest_sha256': sha(FAMILY / 'manifest.json'), 'audit': audit})
    save(TARGET / 'source-references.json', {'family': str(FAMILY.relative_to(REPO)), 'family_commit':'42a827e', 'manifest_sha256': sha(FAMILY / 'manifest.json'),
        'source_network_sha256': raw_sha(BASELINE / 'prepared/network.net.xml'), 'source_control_sha256': raw_sha(BASELINE / 'prepared/control.add.xml'),
        'demand_sha256': raw_sha(BASELINE / 'prepared/demand.rou.xml'), 'portable_requirement': 'Use this repository with the fixed GS-priority benchmark, its recorded dependencies and the declared project .venv runtime. Historical absolute source paths are provenance, not relocation instructions.'})
    (TARGET / '.gitattributes').write_text('* -text whitespace=blank-at-eol,blank-at-eof,space-before-tab,cr-at-eol\n*.gz binary\n', encoding='utf-8')
    comparisons = '|批次|cap/实际车辆|MAE130|SSE130|初始化TAE|TAE182|等待/在途|原判定|\n|---|---:|---:|---:|---:|---:|---:|---|\n'
    for name, row in audit['cases'].items():
        if 'measurement' in row:
            m = row['measurement']
            comparisons += f"|{name}|{row['requested_cap']}/{row['actual_changes']}|{m['main130']['MAE']:.6f}|{m['main130']['SSE']}|{m['initial52']['TAE']}|{m['all182']['TAE']}|{m['completion']['waiting']}/{m['completion']['running']}|{row['raw_verdict']}|\n"
        else:
            comparisons += f"|{name}|{name[-1]}/{row['actual_changes']}|缺完整测量|缺完整测量|缺完整测量|缺完整测量|未知|invalid|\n"
    (TARGET / 'README.md').write_text(f'''# 健康基准上的Z4有界发车反馈

本包冻结两个完整原生对照，源为42a827e已冻结的健康GS条件模型：主130项MAE3.7153846、SSE6709，8640辆全部到达。batch-1只改1辆，batch-4的cap为4但实际只改3辆；没有补造第四辆。只调整已完整通过Z4车辆的显式depart及对应元数据，字段/路线/stops/总人口/network/control保持；294向量为保存请求的元数据计数，没有quantile重生成。

{comparisons}

本轮两批未采用，健康3.7153846工作基准不变。原runner预设gates原样保留，并从实际native重算：safe、healthy、535指定56项仍零、无重复/其他字段贡献、主130MAE严格改善、SSE及全182TAE不恶化。静态Z4投影只作提案依据，不能声称原生时移成立。完整130/40/52/182/208、健康及所有失败均保留，不能按单项改善宣布完成标定。

basis保留14项Z4真实观测及源761车请求、选中变化/提案/scope/294元数据；旧8640源模型与native按固定GS包hash引用，不复制旧完整passages。新输入、关键native和compact完整贡献按原字节保存。大instant/queues只local-only哈希，不当作逐事件原件。现有独立GS归属、603优先及车道意向假设仍未知，计数改善不会赋予现场正确性。

若附baseline-count-review，两幅图仅表示健康3.715基准与此前197结果的比较，不表示本次两个需求试验。离线verify和8项测试不启动仿真；显式reproduce.py须选择batch-1或batch-4及新output-dir，使用项目.venv/固定源hash/Libsumo1.27.1，只复跑保存的输入。
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
