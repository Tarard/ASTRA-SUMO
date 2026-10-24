"""Freeze one completed quantile OD proposal and its audit; never start SUMO."""
import argparse
from collections import Counter
import gzip
import hashlib
from importlib.util import module_from_spec, spec_from_file_location
import io
import json
from pathlib import Path
import tempfile
import xml.etree.ElementTree as ET

import numpy as np

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
SOURCE = REPO/'outputs/hamburg-healthy-joint-od-feedback-20261024-v1'
AUDIT = REPO/'outputs/hamburg-healthy-joint-od-audit-20261024-v1'
GS = REPO/'benchmarks/hamburg_ring1_119_receiving_priority_20261023_v1'
BASELINE = GS/'full-priority'
CLASSIC = REPO/'benchmarks/hamburg_ring1_classical_od_response_20261023_v1'
spec = spec_from_file_location('joint_archive_helpers', GS/'freeze_evidence.py')
helper = module_from_spec(spec)
spec.loader.exec_module(helper)
raw, load, sha, raw_sha = helper.raw, helper.load, helper.sha, helper.raw_sha
safe_relative, manifest_paths, save = helper.safe_relative, helper.manifest_paths, helper.save
verify_manifest, audit_measurement = helper.verify_manifest, helper.audit_measurement
AUDIT_MANIFEST_SHA = 'aed4c5bce1c0f270e4d97266b19f91fef67622f2fcd4490d9de710bedf343520'
INDEPENDENT_MANIFEST_SHA = '8e63fd771c98ee14a73a09c5f98e592a65608606133437a13d4d9a3e83d06205'
OLD_SOLVER_SHA = 'e9335099a4f5ed2fc68d7231abdb6c1c1e710596a2cf657703ea89a362c3a7da'
LOCAL = {'instant-e1.xml', 'queues.xml'}


def snapshot_relative(name):
    path = safe_relative(str(name))
    return Path('study-source')/path if len(path.parts) == 1 else path


def selected_files():
    assert sha(AUDIT/'manifest.json') == AUDIT_MANIFEST_SHA
    assert sha(SOURCE/'independent/manifest.json') == INDEPENDENT_MANIFEST_SHA
    selected = {Path('audit')/p: AUDIT/p for p in manifest_paths(AUDIT)}
    selected.update({Path('independent')/p: SOURCE/'independent'/p for p in manifest_paths(SOURCE/'independent')})
    for name in ('prepare.py', 'run_candidate.py', 'runner-reuse.json', 'check-only.json', 'joint-128.execution.log'):
        selected[snapshot_relative(name)] = SOURCE/name
    for name in ('preflight.json', 'scope-proof.json', 'verdict.json', 'executed-driver.py', 'od-vector.json', 'failure.log'):
        if (SOURCE/'joint-128'/name).is_file():
            selected[Path('joint-128')/name] = SOURCE/'joint-128'/name
    for folder, names in {
        'prepared': ('network.net.xml','control.add.xml','demand.rou.xml','lane-intentions.json','field-loops.add.xml','expected-counts.csv','initialization-counts.csv','scenario.json'),
        'evaluation': ('demand.rou.xml','lane-intentions.json','od-vector.json','evaluation.json','station-comparison.csv','initialization52-comparison.csv','all182-comparison.csv','535-all56.csv','native208-comparison.csv','contributing-passages.json.gz'),
        'evaluation/native': ('network.net.xml','demand.rou.xml','source-e1.add.xml','signal-programs.add.xml','lane-intentions.json','expected-counts.csv','e1.add.xml','instant.add.xml','field-counts.xml','field-comparison.csv','summary.xml','tripinfo.xml','vehicle-routes.xml','collisions.xml','run.json','native-request.json','command.json','stall-monitor.json','sumo.log','sumo-errors.log','instant-e1.xml','queues.xml'),
    }.items():
        for name in names:
            p = Path('joint-128')/folder/name
            selected[p] = SOURCE/p
    for p in sorted((SOURCE/'joint-128/implementation').glob('*.py')):
        selected[p.relative_to(SOURCE)] = p
    selected[Path('source-code/od_assignment_feedback.py')] = SOURCE/'source-code/od_assignment_feedback.py'
    assert sha(selected[Path('source-code/od_assignment_feedback.py')]) == OLD_SOLVER_SHA
    # Capture unchanged declared modules, rather than comparing an old run with future live bytes.
    for declared, expected in load(AUDIT/'audit.json')['source_hashes'].items():
        p = Path(declared)
        if '/plugins/' in p.as_posix() and p.name != 'od_assignment_feedback.py':
            assert sha(p) == expected
            selected[Path('source-code')/p.name] = p
    replay = load(SOURCE/'independent/fixed-helper-offline-replay.json')
    fixed = REPO/'plugins/astra-sumo/src/astra_sumo/core/od_assignment_feedback.py'
    assert sha(fixed) == replay['live_core_sha256']
    selected[Path('source-code/od_assignment_feedback-boundary-fixed.py')] = fixed
    return selected


def reference_catalog():
    paths = [BASELINE/'prepared'/n for n in ('network.net.xml','control.add.xml','demand.rou.xml','lane-intentions.json','field-loops.add.xml','expected-counts.csv','initialization-counts.csv','scenario.json')]
    paths += [BASELINE/'evaluation'/n for n in ('evaluation.json','contributing-passages.json.gz')]
    paths += [BASELINE/'evaluation/native'/n for n in ('field-counts.xml','tripinfo.xml')]
    paths += [CLASSIC/'prepared/od-model.json']
    return {raw_sha(p): p for p in paths}


def read(case, name):
    """Resolve only declared byte-identical packet/repository references."""
    relative = safe_relative(str(name)).as_posix()
    if (case/relative).is_file() or (case/(relative+'.gz')).is_file():
        return raw(case/relative)
    row = load(case/'byte-references.json')['aliases'][relative]
    target = safe_relative(row['target'])
    assert row['scope'] in ('packet', 'repository')
    value = read(case, target) if row['scope'] == 'packet' else raw(REPO/target)
    assert hashlib.sha256(value).hexdigest() == row['sha256']
    return value


def jread(case, name):
    return json.loads(read(case, name))


def key(row):
    return row['detector_id'], float(row['begin']), float(row['end'])


def audit_scope(case):
    old_root = ET.fromstring(raw(BASELINE/'prepared/demand.rou.xml'))
    new_root = ET.fromstring(read(case,'joint-128/prepared/demand.rou.xml'))
    old, new = [{v.get('id'):v for v in root.findall('vehicle')} for root in (old_root,new_root)]
    old_intents = {r['vehicle_id']:r for r in load(BASELINE/'prepared/lane-intentions.json')['vehicles']}
    new_intents = {r['vehicle_id']:r for r in jread(case,'joint-128/prepared/lane-intentions.json')['vehicles']}
    assert len(old) == len(new) == 8640 and old.keys() == old_intents.keys() and new.keys() == new_intents.keys()
    assert len(new.keys()-old.keys()) == len(old.keys()-new.keys()) == 64
    common = old.keys() & new.keys()
    def canon(v):
        return ET.canonicalize(ET.tostring(v,encoding='unicode'),strip_text=True)
    assert [canon(v) for v in old_root if v.tag != 'vehicle'] == [canon(v) for v in new_root if v.tag != 'vehicle']
    changes = {name:0 for name in ('departure','route','stop','field')}
    for identity in common:
        before, after = old[identity], new[identity]
        assert {k:v for k,v in before.attrib.items() if k != 'depart'} == {k:v for k,v in after.attrib.items() if k != 'depart'}
        changes['departure'] += before.get('depart') != after.get('depart')
        changes['route'] += canon(before.find('route')) != canon(after.find('route'))
        changes['stop'] += [canon(v) for v in before.findall('stop')] != [canon(v) for v in after.findall('stop')]
        changes['field'] += old_intents[identity]['field_id'] != new_intents[identity]['field_id']
        if old_intents[identity]['od_cell'] < 84:
            assert canon(before) == canon(after) and old_intents[identity] == new_intents[identity]
    assert changes == {'departure':1102,'route':179,'stop':284,'field':284}
    def inventory(vehicles):
        return Counter((v.find('route').get('edges').split()[0],v.find('route').get('edges').split()[-1]) for v in vehicles.values())
    assert inventory(old) == inventory(new) and len(inventory(new)) == 21
    vector = jread(case,'joint-128/od-vector.json')
    proposal = jread(case,'audit/bounded-128/proposal.json')
    assert vector['integer_counts'] == vector['values'] == proposal['solution']
    assert [sum(r['od_cell']==i for r in new_intents.values()) for i in range(294)] == proposal['solution']
    for name in ('network.net.xml','control.add.xml','field-loops.add.xml','expected-counts.csv','initialization-counts.csv','scenario.json'):
        assert read(case,Path('joint-128/prepared')/name) == raw(BASELINE/'prepared'/name)
    return changes


def audit_assignment(case):
    arrays = np.load(io.BytesIO(read(case, 'audit/assignment182.npz')))
    full = np.load(io.BytesIO(read(case, 'audit/assignment208-accounting.npz')))
    inputs = np.load(io.BytesIO(read(case, 'audit/bounded-128/solver-inputs.npz')))
    C, q, A = (arrays[n] for n in ('C', 'q', 'A'))
    proposal = jread(case, 'audit/bounded-128/proposal.json')
    x = np.array(proposal['solution'], dtype=np.int64)
    declaration = jread(case, 'audit/audit-inputs.json')
    targets = declaration['target_rows']
    keys = [key(r) for r in targets]
    y = np.array([int(r['expected_total']) for r in targets])
    assert C.shape == (182, 294) and q.shape == x.shape == (294,) and (q > 0).all()
    assert np.array_equal(C, inputs['C']) and np.array_equal(q, inputs['q']) and np.array_equal(y, inputs['y'])
    assert np.array_equal(A, C/q) and sum(q) == sum(x) == 8640
    assert np.count_nonzero(x-q) == proposal['changed_cells'] == 22 and abs(x-q).sum() == proposal['l1_change'] == 128
    assert np.array_equal(x[:84], q[:84]) and np.all(x >= inputs['lower']) and np.all(x <= inputs['upper'])
    assert np.array_equal(arrays['OD_totals']@x, arrays['OD_totals']@q)
    counts = {(r.get('id'), float(r.get('begin')), float(r.get('end'))): int(r.get('nVehContrib')) for r in ET.fromstring(raw(BASELINE/'evaluation/native/field-counts.xml'))}
    events = json.loads(gzip.decompress(raw(BASELINE/'evaluation/contributing-passages.json.gz')))['passages']
    intents = {r['vehicle_id']: r for r in load(BASELINE/'prepared/lane-intentions.json')['vehicles']}
    allkeys = sorted(counts, key=lambda r: (r[1], r[0]))
    index = {k:i for i,k in enumerate(allkeys)}
    actual = np.zeros((208, 294), dtype=np.int64)
    assert len(events) == len({r['vehicle_id'] for r in events}) == 8640
    for r in events:
        actual[index[r['detector_id'],r['interval_begin_s'],r['interval_end_s']],intents[r['vehicle_id']]['od_cell']] += 1
    assert np.array_equal(actual, full['contributions']) and np.array_equal(actual.sum(axis=0), q)
    assert np.array_equal(actual.sum(axis=1), [counts[k] for k in allkeys])
    assert np.array_equal(actual[[index[k] for k in keys]], C)
    tail = [i for i,k in enumerate(allkeys) if k not in set(keys)]
    assert len(tail) == 26 and actual[tail].sum() == 314
    predicted = A@x
    assert np.allclose(predicted, proposal['predicted_counts'], rtol=0, atol=1e-10)
    exact = inputs['exact_rows']
    assert exact.sum() == 56 and np.max(abs(predicted[exact]-y[exact])) < 1e-6
    assert not C[arrays['initial_mask'],84:].any()
    projection = float(abs(predicted-y).sum())
    assert projection == proposal['predicted_TAE']
    replay = jread(case, 'independent/fixed-helper-offline-replay.json')
    assert replay['status'] == 'pass' and replay['native_runs'] == 0 and replay['solution_exactly_same'] and replay['predicted_TAE_exactly_same']
    assert replay['new_result']['solution'] == proposal['solution'] and replay['new_result']['predicted_TAE'] == projection
    assert hashlib.sha256(read(case,'source-code/od_assignment_feedback.py')).hexdigest() == replay['old_source_snapshot_sha256'] == OLD_SOLVER_SHA
    assert hashlib.sha256(read(case,'source-code/od_assignment_feedback-boundary-fixed.py')).hexdigest() == replay['live_core_sha256']
    return {'source_events':8640,'real182_events':8326,'tail26_events_without_real_targets':314,'changed_cells':22,'cell_L1':128,'OD_totals':21,'first84_fixed':True,'predicted182_TAE':projection,'predicted130_TAE':projection-105,'description_is_not_Jacobian':True,'later_guard_offline_solution_same':True}


def audit_packet(case):
    independent = jread(case,'independent/summary.json')
    assert independent['schema'] == 'astra.hamburg.healthy-joint-od-independent/v1' and independent['audit_status'] == 'pass'
    verdict = jread(case,'joint-128/verdict.json')
    preflight = jread(case,'joint-128/preflight.json')
    proof = jread(case,'joint-128/scope-proof.json')
    assert verdict['status'] == independent['selection'] == 'rejected' and verdict['native_response_measured']
    assert verdict['before'] == independent['before']
    baseline = load(GS/'results.json')['audit']['full']
    for local, saved in [('main130','metrics130'),('initial52','metrics52'),('all182','metrics182')]:
        assert all(verdict['before'][saved][k] == v for k,v in baseline[local].items())
    assert verdict['before']['completion'] == baseline['completion']
    assert not verdict['formal_result'] and proof['status'] == 'pass' and proof['N'] == 8640
    assert preflight['source_proof_sha256'] == hashlib.sha256(read(case,'joint-128/scope-proof.json')).hexdigest()
    assert preflight['executed_driver_sha256'] == hashlib.sha256(read(case,'joint-128/executed-driver.py')).hexdigest() == hashlib.sha256(read(case,'study-source/run_candidate.py')).hexdigest()
    for name, expected in preflight['prepared_hashes'].items():
        assert hashlib.sha256(read(case,Path('joint-128/prepared')/name)).hexdigest() == expected
    for name, expected in preflight['implementation_sha256'].items():
        assert hashlib.sha256(read(case,Path('joint-128/implementation')/name)).hexdigest() == expected
    changes = jread(case,'audit/bounded-128/generation-audit.json')
    assert changes['demand_generation_changes']['common_request_changed'] == 1102
    assert changes['demand_generation_changes']['common_route_changed'] == 179
    assert changes['demand_generation_changes']['common_stop_changed'] == changes['demand_generation_changes']['common_field_intention_changed'] == 284
    assert changes['first84_cells_frozen'] and changes['OD_totals_fixed']
    signatures = {r['vehicle_id']:[r['field_id']] for r in jread(case,'joint-128/prepared/lane-intentions.json')['vehicles']}
    assert len(signatures) == 8640
    # Existing frozen reader consumes ordinary paths: expand only this run in an isolated temporary folder.
    paths = [r['relative'] for r in load(case/'source-provenance.json')['snapshots']] + list(load(case/'byte-references.json')['aliases'])
    with tempfile.TemporaryDirectory() as temporary:
        temp = Path(temporary)
        for name in paths:
            if name.startswith(('joint-128/prepared/','joint-128/evaluation/')):
                target = temp/safe_relative(name)
                target.parent.mkdir(parents=True,exist_ok=True)
                target.write_bytes(read(case,name))
        measured, _, extra, repeated = audit_measurement(temp/'joint-128/prepared',temp/'joint-128/evaluation',signatures)
    assert not extra and not repeated
    for local, saved, size in [('main130','metrics130',130),('initial52','metrics52',52),('all182','metrics182',182)]:
        assert verdict['after'][saved] == independent['after'][saved]
        assert verdict['after'][saved]['rows'] == size
        assert all(verdict['after'][saved][k] == v for k,v in measured[local].items())
    assert measured['completion'] == verdict['after']['completion'] == independent['after']['completion']
    assert measured['station40_MAE'] == verdict['after']['station40_MAE'] == independent['after']['station40_MAE']
    assert measured['event_count'] == verdict['after']['event_count'] == 8614
    assert measured['safe'] and not measured['healthy'] and measured['completion']['running'] == 34
    assert verdict['gates'] == independent['gates'] and not all(verdict['gates'].values())
    assert verdict['all53556']['TAE'] == independent['all53556']['TAE'] == 0
    assert audit_scope(case) == {k:v for k,v in independent['scope']['common_changes'].items() if k != 'non_departure_attributes'}
    assignment = audit_assignment(case)
    return {'status':'review_required','raw_verdict':'rejected','candidate_selected':False,'formal_result':False,'native_runs_by_freeze':0,'full':measured,'before':verdict['before'],'gates':verdict['gates'],'all53556':verdict['all53556'],'assignment':assignment,'scope':independent['scope'],'claim_boundary':'Saved empirical assignment is not a Jacobian or native prediction guarantee. Quantile regeneration changed many requests/routes/stops. Audit pass is not acceptance; retain healthy3.715 reference. No stable realization case is included.'}


def verify(case):
    verify_manifest(case)
    verify_manifest(GS)
    refs = load(case/'source-references.json')
    assert sha(GS/'manifest.json') == refs['healthy_manifest_sha256']
    assert sha(CLASSIC/'manifest.json') == refs['model_package_manifest_sha256']
    for prefix, expected in [('audit',AUDIT_MANIFEST_SHA),('independent',INDEPENDENT_MANIFEST_SHA)]:
        assert hashlib.sha256(read(case,Path(prefix)/'manifest.json')).hexdigest() == expected
        for row in jread(case,Path(prefix)/'manifest.json')['files']:
            value = read(case,Path(prefix)/safe_relative(row['path']))
            assert len(value) == row.get('bytes',row.get('size_bytes')) and hashlib.sha256(value).hexdigest() == row['sha256']
    for row in load(case/'source-provenance.json')['snapshots']:
        value = read(case,row['relative'])
        assert len(value) == row['raw_bytes'] and hashlib.sha256(value).hexdigest() == row['raw_sha256']
    for row in load(case/'historical-source-bindings.json')['bindings']:
        value = read(case,row['target']) if row['scope'] == 'packet' else raw(REPO/safe_relative(row['target']))
        assert hashlib.sha256(value).hexdigest() == row['sha256']
    compact = json.loads(gzip.decompress(read(case,'joint-128/evaluation/contributing-passages.json.gz')))
    instant = next(r for r in load(case/'local-only-large-traces.json')['files'] if Path(r['path']).name == 'instant-e1.xml')
    assert instant['sha256'] in {r['sha256'] for r in compact['inputs']}
    assert audit_packet(case) == load(case/'results.json')['audit']
    print('Joint128 hashes, old-solver mapping, empirical208 accounting,130/40/52/182 and rejected native result: pass',flush=True)


def freeze():
    assert not (HERE/'manifest.json').exists(), 'Preserve frozen evidence.'
    selected = selected_files()
    catalog, aliases, seen, provenance, local = reference_catalog(), {}, {}, [], []
    locations = {h:{'scope':'repository','target':p.relative_to(REPO).as_posix()} for h,p in catalog.items()}
    for name, source in sorted(selected.items()):
        assert source.is_file()
        digest = sha(source)
        if source.name in LOCAL:
            local.append({'path':str(source),'bytes':source.stat().st_size,'sha256':digest})
            continue
        relative = name.as_posix()
        if digest in catalog or digest in seen:
            pointer = locations[digest]
            aliases[relative] = {**pointer,'sha256':digest}
        else:
            value = source.read_bytes()
            target = HERE/(relative+'.gz')
            target.parent.mkdir(parents=True,exist_ok=True)
            encoded = gzip.compress(value,mtime=0)
            if target.exists():
                assert target.read_bytes() == encoded, 'Never replace conflicting partial snapshots.'
            else:
                target.write_bytes(encoded)
            seen[digest] = relative
            locations[digest] = {'scope':'packet','target':relative}
        provenance.append({'source':str(source),'relative':relative,'raw_bytes':source.stat().st_size,'raw_sha256':digest})
    source_provenance = {'snapshots':provenance,'original_sources_unchanged':True}
    if (HERE/'source-provenance.json').exists():
        assert load(HERE/'source-provenance.json') == source_provenance, 'Resume only the same exact source set.'
    save(HERE/'source-provenance.json',source_provenance)
    save(HERE/'byte-references.json',{'aliases':aliases,'rule':'Every reference is byte-identical and SHA-bound; no historical source hash is rewritten.'})
    bindings = []
    declaration = load(AUDIT/'audit.json')['source_hashes']
    declaration.update(load(SOURCE/'joint-128/scope-proof.json')['source_hashes'])
    for declared, digest in declaration.items():
        assert digest in locations, (declared,digest)
        bindings.append({'declared_path':declared,'sha256':digest,**locations[digest]})
    save(HERE/'historical-source-bindings.json',{'bindings':bindings,'old_solver_declared_live_path_resolves_to_saved_e933_snapshot':True,'historical_hashes_unchanged':True})
    save(HERE/'local-only-large-traces.json',{'files':local,'limitation':'Original instant/queues remain local; portable compact contributions and208 counts are retained.'})
    save(HERE/'source-references.json',{'healthy_commit':'42a827e','healthy_manifest_sha256':sha(GS/'manifest.json'),'model_package_manifest_sha256':sha(CLASSIC/'manifest.json'),'audit_manifest_sha256':AUDIT_MANIFEST_SHA,'independent_manifest_sha256':INDEPENDENT_MANIFEST_SHA,'stable64_included':False,'old_solver_sha256':OLD_SOLVER_SHA})
    report = audit_packet(HERE)
    save(HERE/'results.json',{'audit':report,'formal_result':False,'status':'review_required'})
    (HERE/'.gitattributes').write_text('* -text whitespace=blank-at-eol,blank-at-eof,space-before-tab,cr-at-eol\n*.gz binary\n',encoding='utf-8')
    (HERE/'README.md').write_text(README_TEXT,encoding='utf-8')
    assert all(sha(Path(r['source'])) == r['raw_sha256'] for r in provenance)
    save(HERE/'manifest.json',{'formal_result':False,'status':'review_required','files':[{'path':p.relative_to(HERE).as_posix(),'bytes':p.stat().st_size,'sha256':sha(p)} for p in sorted(HERE.rglob('*')) if p.is_file() and p != HERE/'manifest.json' and '__pycache__' not in p.parts]})
    verify(HERE)


README_TEXT = '''# 健康基准的联合分时OD：唯一quantile提议原生对照

本包保存一次离线分配审计、唯一joint-128提议及其完整原生结果。原始判定为**rejected，不采用**；独立audit的pass只表示证据对账通过。健康工作基准仍为42a827e冻结的full-priority，MAE3.7153846，8640辆全部到达。没有纳入后续stable64或其他候选。

| 结果 | 主130 MAE / SSE / TAE | 初始化52 TAE | 全182 TAE | 40派生站点MAE | 到达/等待/在途 |
|---|---|---:|---:|---:|---|
| 健康基准 | 3.7153846 / 6709 / 483 | 105 | 588 | 10.975 | 8640/0/0 |
| joint-128原生 | 3.8769231 / 8858 / 504 | 105 | 609 | 11.45 | 8606/0/34 |

两份零碰撞/传送/discard，535四个指定字段的全部56行仍零残差。新130最大误差44；34辆在途含30辆119 Z4、4辆535 Z4，其中8辆已贡献、26辆尚未完整过点。Z4、Z15等退步与Z12/Z13/Z11改善均保留，不挑选改善字段替代主目标。完整130/40/52/182/208、逐字段和未完成ID可在independent与原生记录复核。真实计数与原生E1 nVehContrib比较；40站点只是派生合计，不是独立观测。

分配C的182×294整数表来自健康源实际完整贡献，A=C/q仅描述同一次运行。C/q×q回算原生计数不等于Jacobian，也不保证改变需求后的响应。全部208行包括8326个真实目标窗事件和314个无真实目标的尾部事件；26个尾部行没有填假零。8个182零贡献列都有正q及尾部事件，不能称零需求。矩阵/整数界限/真实targets/预测/原生对账全部保留。

提议保持21个OD的跨14时段总量和8640人口，固定首84 cells，在294变量中改22 cells、L1=128。描述投影主130 TAE为368.413577，全182为473.413577；这些是未成立的平移分配预测，不能写成实测拟合。原quantile生成保留：64新增、64移除ID，共同ID中1102毫秒出发时间、179条路线、284个stop及field意向改变；不是只移动64辆，也不是纯发车时移。未变cell输入逐项一致。网络、控制、真实targets、探头、四bodyhold节点及Libsumo1.27.1/0.1s/14400s/seed104保持健康源。

原求解器e9335099…按原run保存。历史绝对live路径通过historical-source-bindings明确解析到该快照，旧preflight和sourcehash不改写。后来2**53聚合边界修复c09c4a7…另存；独立离线求解得到同一q和完全相同预测，不能称修复改善原生结果。原准备阶段文本仍按原字节保存，不当作终态。

健康源输入/旧native和model256472按固定旧包及SHA引用，不重复复制；byte-references逐项声明本包去重关系。大instant/queues只留localhash，迁机后缺原件不能逐事件重读，完整compact贡献则已保存。冻结与verify/reproduce只做离线复核，不运行SUMO。复核需同仓库固定旧包和项目.venv；原始绝对路径只供溯源。未改核心、公共docs或提交；仍未达到130零误差，也未证明真实OD或现场控制。
'''


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--freeze',action='store_true')
    mode.add_argument('--verify-only',action='store_true')
    args = parser.parse_args()
    freeze() if args.freeze else verify(HERE)
