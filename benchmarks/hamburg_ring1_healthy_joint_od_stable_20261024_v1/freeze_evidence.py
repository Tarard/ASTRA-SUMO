"""Freeze one terminal stable OD realization; never start SUMO."""
import argparse
from collections import Counter
from copy import deepcopy
import gzip
import hashlib
from importlib.util import module_from_spec, spec_from_file_location
import json
import math
from pathlib import Path
import statistics
import tempfile
import xml.etree.ElementTree as ET

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
SOURCE = REPO/'outputs/hamburg-healthy-joint-od-stable-20261024-v1'
JOINT = REPO/'benchmarks/hamburg_ring1_healthy_joint_od_feedback_20261024_v1'
spec = spec_from_file_location('stable_packet_helpers',JOINT/'freeze_evidence.py')
prior = module_from_spec(spec)
spec.loader.exec_module(prior)
GS, BASELINE, CLASSIC = prior.GS, prior.BASELINE, prior.CLASSIC
raw, load, sha, raw_sha = prior.raw, prior.load, prior.sha, prior.raw_sha
safe_relative, manifest_paths, save = prior.safe_relative, prior.manifest_paths, prior.save
read, jread, snapshot_relative = prior.read, prior.jread, prior.snapshot_relative
verify_manifest, audit_measurement = prior.verify_manifest, prior.audit_measurement
LOCAL = {'instant-e1.xml','queues.xml'}
CORE_SHA = '56ca5f4fabbe00ef796959a969f293afdd96b409fc18398f08ccc3c9e973a392'
INDEPENDENT_MANIFEST_SHA = 'efc52431c2a7a3e46ebc7f60aa7dae134ad4ce50b5598aeb722e06a045176923'


def selected_files():
    assert sha(SOURCE/'independent/manifest.json') == INDEPENDENT_MANIFEST_SHA
    selected = {Path('independent')/p:SOURCE/'independent'/p for p in manifest_paths(SOURCE/'independent')}
    for name in ('prepare.py','run_candidate.py','runner-reuse.json','check-only.json','stable-64.execution.log'):
        selected[snapshot_relative(name)] = SOURCE/name
    for name in ('executed-driver.py','executed-prepare.py','generation-audit.json','od-vector.json','preflight.json','proposal.json','scope-proof.json','source-snapshot-mapping.json','verdict.json','failure.log'):
        p = Path('stable-64')/name
        if (SOURCE/p).is_file():
            selected[p] = SOURCE/p
    for folder,names in {
        'prepared': ('network.net.xml','control.add.xml','demand.rou.xml','lane-intentions.json','field-loops.add.xml','expected-counts.csv','initialization-counts.csv','scenario.json'),
        'evaluation': ('demand.rou.xml','lane-intentions.json','od-vector.json','evaluation.json','station-comparison.csv','initialization52-comparison.csv','all182-comparison.csv','535-all56.csv','native208-comparison.csv','contributing-passages.json.gz'),
        'evaluation/native': ('network.net.xml','demand.rou.xml','source-e1.add.xml','signal-programs.add.xml','lane-intentions.json','expected-counts.csv','e1.add.xml','instant.add.xml','field-counts.xml','field-comparison.csv','summary.xml','tripinfo.xml','vehicle-routes.xml','collisions.xml','run.json','native-request.json','command.json','stall-monitor.json','sumo.log','sumo-errors.log','instant-e1.xml','queues.xml'),
    }.items():
        for name in names:
            p = Path('stable-64')/folder/name
            selected[p] = SOURCE/p
    for p in sorted((SOURCE/'stable-64/implementation').glob('*.py')):
        selected[p.relative_to(SOURCE)] = p
    assert sha(SOURCE/'stable-64/implementation/od_stable_realization.py') == CORE_SHA
    return selected


def reference_catalog():
    result = prior.reference_catalog()
    for name in ('audit/bounded-128/proposal.json','audit/bounded-128/generated/od-vector.json'):
        p = JOINT/name
        result[raw_sha(p)] = p
    return result


def native_ms(value):
    return math.floor(float(value)*1000+.5)


def audit_scope(case):
    proposal = jread(case,'stable-64/proposal.json')
    source = ET.fromstring(raw(BASELINE/'prepared/demand.rou.xml'))
    candidate = ET.fromstring(read(case,'stable-64/prepared/demand.rou.xml'))
    old,new = [{v.get('id'):v for v in root.findall('vehicle')} for root in (source,candidate)]
    old_meta = {r['vehicle_id']:r for r in load(BASELINE/'prepared/lane-intentions.json')['vehicles']}
    new_meta = {r['vehicle_id']:r for r in jread(case,'stable-64/prepared/lane-intentions.json')['vehicles']}
    selected = {r['vehicle_id']:r for r in proposal['proposals']}
    assert len(old) == len(new) == 8640 and old.keys() == new.keys() == old_meta.keys() == new_meta.keys()
    assert len(selected) == len(proposal['proposals']) == proposal['changed_vehicles'] == 64
    assert [ET.tostring(v) for v in source if v.tag != 'vehicle'] == [ET.tostring(v) for v in candidate if v.tag != 'vehicle']
    assert [i for i in old if i not in selected] == [i for i in new if i not in selected]
    model = load(CLASSIC/'prepared/od-model.json')
    indices = {(c['origin'],c['destination'],int(c['begin_s'])):i for i,c in enumerate(model['cells'])}
    assert len(indices) == 294
    inventories = [Counter(),Counter()]
    initial, shifts = [], []
    for identity,before in old.items():
        after = new[identity]
        restored = deepcopy(after)
        restored.set('depart',before.get('depart'))
        assert ET.tostring(restored) == ET.tostring(before)
        for side,row,meta in [(0,before,old_meta[identity]),(1,after,new_meta[identity])]:
            edges = row.find('route').get('edges').split()
            cell = indices[edges[0],edges[-1],native_ms(row.get('depart'))//900000*900]
            assert meta['od_cell'] == cell
            inventories[side][cell] += 1
        assert old_meta[identity]['field_id'] == new_meta[identity]['field_id']
        if native_ms(before.get('depart')) < 3600000:
            initial.append(identity)
            assert ET.tostring(before) == ET.tostring(after) and identity not in selected
        if identity not in selected:
            assert ET.tostring(before) == ET.tostring(after) and old_meta[identity] == new_meta[identity]
        else:
            row = selected[identity]
            assert float(before.get('depart')) == row['source_depart_s'] and float(after.get('depart')) == row['proposed_depart_s']
            assert native_ms(before.get('depart')) != native_ms(after.get('depart'))
            restored_meta = dict(new_meta[identity])
            assert restored_meta.pop('depart_s') == float(after.get('depart'))
            restored_meta['od_cell'] = old_meta[identity]['od_cell']
            assert restored_meta == old_meta[identity]
            shifts.append(abs(native_ms(after.get('depart'))-native_ms(before.get('depart')))/1000)
    assert len(initial) == 1953 and max(shifts) == 5408.089 and statistics.median(shifts) == 2709.244
    q, x = [[inventory[i] for i in range(294)] for inventory in inventories]
    target = prior.jread(JOINT,'audit/bounded-128/proposal.json')['solution']
    assert x == target == proposal['target'] and q == proposal['prior']
    assert q[:84] == x[:84] and sum(abs(a-b) for a,b in zip(q,x)) == proposal['l1_cell_change'] == 128
    def od_totals(vector):
        result = Counter()
        for c,n in zip(model['cells'],vector):
            result[c['origin'],c['destination']] += n
        return result
    assert od_totals(q) == od_totals(x) and len(od_totals(q)) == 21
    vector = jread(case,'stable-64/od-vector.json')
    assert vector['values'] == vector['integer_counts'] == x
    for name in ('network.net.xml','control.add.xml','field-loops.add.xml','expected-counts.csv','initialization-counts.csv'):
        assert read(case,Path('stable-64/prepared')/name) == raw(BASELINE/'prepared'/name)
    before,after = [load(BASELINE/'prepared/scenario.json'),jread(case,'stable-64/prepared/scenario.json')]
    assert after['control_assumptions'] == before['control_assumptions'] and after['junction_body_hold_nodes'] == before['junction_body_hold_nodes']
    return {'IDs':8640,'changed_departures':64,'unchanged_XML_intents_relative_order':8576,'initial_requests_fixed':1953,'same_q_as_quantile':True,'OD_totals':21,'cell_L1':128,'native_max_shift_s':max(shifts),'native_median_shift_s':statistics.median(shifts),'departure_shift_cap':None}


def audit_packet(case):
    independent = jread(case,'independent/summary.json')
    assert independent['schema'] == 'astra.hamburg.stable-od-independent/v1' and independent['audit_status'] == 'pass'
    verdict = jread(case,'stable-64/verdict.json')
    assert verdict['status'] == independent['selection'] == 'rejected' and verdict['native_response_measured'] and not verdict['formal_result']
    proof = jread(case,'stable-64/scope-proof.json')
    preflight = jread(case,'stable-64/preflight.json')
    assert proof['status'] == 'pass' and proof['actual_changes'] == 64 and proof['no_departure_shift_cap']
    assert preflight['source_proof_sha256'] == hashlib.sha256(read(case,'stable-64/scope-proof.json')).hexdigest()
    for name,expected in preflight['prepared_hashes'].items():
        assert hashlib.sha256(read(case,Path('stable-64/prepared')/name)).hexdigest() == expected
    for name,expected in preflight['implementation_sha256'].items():
        assert hashlib.sha256(read(case,Path('stable-64/implementation')/name)).hexdigest() == expected
    assert preflight['implementation_sha256']['od_stable_realization.py'] == CORE_SHA
    assert preflight['executed_driver_sha256'] == hashlib.sha256(read(case,'stable-64/executed-driver.py')).hexdigest() == hashlib.sha256(read(case,'study-source/run_candidate.py')).hexdigest()
    assert read(case,'stable-64/executed-prepare.py') == read(case,'study-source/prepare.py')
    signatures = {r['vehicle_id']:[r['field_id']] for r in jread(case,'stable-64/prepared/lane-intentions.json')['vehicles']}
    paths = [r['relative'] for r in load(case/'source-provenance.json')['snapshots']] + list(load(case/'byte-references.json')['aliases'])
    with tempfile.TemporaryDirectory() as temporary:
        temp = Path(temporary)
        for name in paths:
            if name.startswith(('stable-64/prepared/','stable-64/evaluation/')):
                target = temp/safe_relative(name)
                target.parent.mkdir(parents=True,exist_ok=True)
                target.write_bytes(read(case,name))
        measured,_,outside,repeated = audit_measurement(temp/'stable-64/prepared',temp/'stable-64/evaluation',signatures)
    assert not outside and not repeated and verdict['after'] == independent['after'] and verdict['before'] == independent['before']
    for local,saved,rows in [('main130','metrics130',130),('initial52','metrics52',52),('all182','metrics182',182)]:
        assert verdict['after'][saved]['rows'] == rows and all(verdict['after'][saved][k] == v for k,v in measured[local].items())
    assert measured['completion'] == verdict['after']['completion'] and measured['station40_MAE'] == verdict['after']['station40_MAE']
    assert measured['event_count'] == verdict['after']['event_count'] == 8540
    assert measured['safe'] and not measured['healthy'] and measured['completion']['running'] == 130
    assert verdict['gates'] == independent['gates'] and not all(verdict['gates'].values())
    assert verdict['all53556']['TAE'] == independent['all53556']['TAE'] == 0
    scope = audit_scope(case)
    def periodic(data):
        return {(r.get('id'),float(r.get('begin')),float(r.get('end'))):int(r.get('nVehContrib')) for r in ET.fromstring(data)}
    before_counts = periodic(raw(BASELINE/'evaluation/native/field-counts.xml'))
    after_counts = periodic(read(case,'stable-64/evaluation/native/field-counts.xml'))
    initial_keys = [(r['detector_id'],float(r['begin']),float(r['end'])) for r in prior.helper.rows(BASELINE/'prepared/initialization-counts.csv')]
    assert len(initial_keys) == 52 and all(before_counts[k] == after_counts[k] for k in initial_keys)
    field = 'hh_0119_Z_5_30494'
    intended = sum(fields == [field] for fields in signatures.values())
    initial_count = sum(before_counts[k] for k in initial_keys if k[0] == field)
    target = sum(int(r['expected_total']) for r in prior.helper.rows(BASELINE/'prepared/expected-counts.csv') if r['detector_id'] == field)
    assert intended == 623 and initial_count == 102 and intended-initial_count == 521 < target == 522
    condition = independent['conditional_Z5_capacity']
    assert condition['global_fixed_intended_vehicles'] == intended and condition['fixed_initial_complete_events'] == initial_count
    assert condition['main_maximum_if_at_most_one_intended_contribution'] == intended-initial_count and condition['main_target_total'] == target
    return {'status':'review_required','formal_result':False,'raw_verdict':'rejected','candidate_selected':False,'native_runs_by_freeze':0,'before':verdict['before'],'full':measured,'all53556':verdict['all53556'],'gates':verdict['gates'],'scope':scope,'conditional_Z5_capacity':condition,'claim_boundary':'Only64 explicit departures changed. Same q does not imply same realization or transfer the earlier empirical C/q prediction. Audit pass is not acceptance; healthy3.715 remains the reference. Conditional same-input bounds do not contradict real targets.'}


def verify(case):
    verify_manifest(case)
    verify_manifest(GS)
    verify_manifest(JOINT)
    refs = load(case/'source-references.json')
    for name,folder in [('healthy',GS),('joint',JOINT),('model',CLASSIC)]:
        assert sha(folder/'manifest.json') == refs[name+'_manifest_sha256']
    assert hashlib.sha256(read(case,'independent/manifest.json')).hexdigest() == refs['independent_manifest_sha256'] == INDEPENDENT_MANIFEST_SHA
    for row in jread(case,'independent/manifest.json')['files']:
        value = read(case,Path('independent')/safe_relative(row['path']))
        assert len(value) == row.get('bytes',row.get('size_bytes')) and hashlib.sha256(value).hexdigest() == row['sha256']
    for row in load(case/'source-provenance.json')['snapshots']:
        value = read(case,row['relative'])
        assert len(value) == row['raw_bytes'] and hashlib.sha256(value).hexdigest() == row['raw_sha256']
    for row in load(case/'historical-source-bindings.json')['bindings']:
        value = read(case,row['target']) if row['scope']=='packet' else raw(REPO/safe_relative(row['target']))
        assert hashlib.sha256(value).hexdigest() == row['sha256']
    compact = json.loads(gzip.decompress(read(case,'stable-64/evaluation/contributing-passages.json.gz')))
    instant = next(r for r in load(case/'local-only-large-traces.json')['files'] if Path(r['path']).name == 'instant-e1.xml')
    assert instant['sha256'] in {r['sha256'] for r in compact['inputs']}
    assert audit_packet(case) == load(case/'results.json')['audit']
    print('Stable64 hashes, original implementation,64departure scope,130/40/52/182/208 and rejected native result: pass',flush=True)


def freeze():
    assert not (HERE/'manifest.json').exists() and not (HERE/'source-provenance.json').exists(), 'Preserve frozen or partial evidence.'
    selected = selected_files()
    catalog,aliases,seen,provenance,local = reference_catalog(),{},{},[],[]
    locations = {h:{'scope':'repository','target':p.relative_to(REPO).as_posix()} for h,p in catalog.items()}
    for name,source in sorted(selected.items()):
        assert source.is_file()
        digest = sha(source)
        if source.name in LOCAL:
            local.append({'path':str(source),'bytes':source.stat().st_size,'sha256':digest})
            continue
        relative = name.as_posix()
        if digest in catalog or digest in seen:
            aliases[relative] = {**locations[digest],'sha256':digest}
        else:
            target = HERE/(relative+'.gz')
            target.parent.mkdir(parents=True,exist_ok=True)
            target.write_bytes(gzip.compress(source.read_bytes(),mtime=0))
            seen[digest] = relative
            locations[digest] = {'scope':'packet','target':relative}
        provenance.append({'source':str(source),'relative':relative,'raw_bytes':source.stat().st_size,'raw_sha256':digest})
    save(HERE/'source-provenance.json',{'snapshots':provenance,'source_files_unchanged':True})
    save(HERE/'byte-references.json',{'aliases':aliases,'rule':'Only byte-identical SHA-bound fixed inputs or duplicate snapshots are referenced.'})
    bindings=[]
    for declared,digest in load(SOURCE/'stable-64/scope-proof.json')['source_hashes'].items():
        assert digest in locations,(declared,digest)
        bindings.append({'declared_path':declared,'sha256':digest,**locations[digest]})
    save(HERE/'historical-source-bindings.json',{'bindings':bindings,'original_hashes_unchanged':True,'historical_live_paths_resolve_to_raw_saved_implementation':True})
    save(HERE/'local-only-large-traces.json',{'files':local,'limitation':'Raw instant/queues remain local; full compact contributions and208 counts are portable.'})
    save(HERE/'source-references.json',{'healthy_commit':'42a827e','joint_commit':'3b26c18','healthy_manifest_sha256':sha(GS/'manifest.json'),'joint_manifest_sha256':sha(JOINT/'manifest.json'),'model_manifest_sha256':sha(CLASSIC/'manifest.json'),'independent_manifest_sha256':sha(SOURCE/'independent/manifest.json'),'separate_capacity_or_share_study_included':False,'native_ms_implementation_sha256':CORE_SHA})
    save(HERE/'results.json',{'status':'review_required','formal_result':False,'audit':audit_packet(HERE)})
    (HERE/'.gitattributes').write_text('* -text whitespace=blank-at-eol,blank-at-eof,space-before-tab,cr-at-eol\n*.gz binary\n',encoding='utf-8')
    (HERE/'README.md').write_text(README_TEXT,encoding='utf-8')
    assert all(sha(Path(r['source'])) == r['raw_sha256'] for r in provenance)
    save(HERE/'manifest.json',{'status':'review_required','formal_result':False,'files':[{'path':p.relative_to(HERE).as_posix(),'bytes':p.stat().st_size,'sha256':sha(p)} for p in sorted(HERE.rglob('*')) if p.is_file() and p!=HERE/'manifest.json' and '__pycache__' not in p.parts]})
    verify(HERE)


README_TEXT = '''# 同一分时OD计数的64辆稳定实现对照

本包只保存stable-64这一次完整原生运行，原判定为**rejected，不采用**。独立audit的pass表示证据对账通过，不是候选接受。健康工作基准仍为42a827e的full-priority：MAE3.7153846、8640辆全部到达。量化joint-128另见3b26c18固定包；未加入新的capacity/share研究或候选。

| 结果 | 主130 MAE / SSE / TAE | 初始化52 TAE | 全182 TAE | 40派生站点MAE | 到达/等待/在途 |
|---|---|---:|---:|---:|---|
| 健康基准 | 3.7153846 / 6709 / 483 | 105 | 588 | 10.975 | 8640/0/0 |
| stable-64原生 | 5.1 / 12467 / 663 | 105 | 768 | 14.975 | 8510/0/130 |

全部8640辆进入，130辆仍在途，零碰撞/传送/discard，535指定四字段的56行仍零残差。130最大误差41；剩余车辆全源自963入口，80辆119 Z4、50辆119 Z5，其中30辆已经贡献，100辆未完整过点。完整130/40/52/182/208、逐字段误差、选中64辆实际事件和所有未完成ID保留；40站点由字段合计，不是新的独立观测。真实计数与原生E1 nVehContrib比较，未把尾部无真值行填成零。

此次294-cell整数q与joint-128完全相同：22cells改变、L1=128、21个OD跨时段总量与8640人口保持、首84cells固定。实现方法不同：只改现有64辆显式depart，全部ID/路线/stop/field/vType保持，其余8576辆XML和意向及相对输入顺序不变。1953个初始化请求逐项固定。原生毫秒时移最大5408.089秒、中位2709.244秒；本例**没有900秒上限**，不能称小幅时移。相同q不意味着相同交通或相同经验分配；不能把quantile下的C/q投影当成本例预测，更不能当Jacobian。

原准备前已使用native-millisecond初始cell归属，执行实现56ca5f4…及全部依赖按原字节保存；历史绝对live路径通过明确快照mapping解析，不要求未来live源码相同，也不改写旧hash。网络/控制/真实targets/探头/四bodyhold及Libsumo1.27.1、step0.1、seed104、14400秒保持健康源。控制归属与条件priority假设不因拟合或无碰撞而成为现场SPaT。

independent中的同输入必要容量条件原样保留：固定field总量和原生初始化计数、且无额外或重复贡献的子类中，Z5主窗上界521小于目标522。它只限制该固定子类，合法fieldshare可解除该限制；不构成真实观测矛盾，也不判断其他实现或零误差目标不可行。未打包另立的新capacity/share研究。

健康旧输入/native、model256472及joint-128的q按固定包/SHA引用，不重复复制；byte-references列出全部去重。大instant/queues只留localhash，缺原文件不能迁机重读逐事件数据，完整compact贡献已保存。冻结、verify和reproduce均只离线复核，未启动新SUMO。复核需要同仓库固定包和项目.venv。未改公共docs或提交；仍未完成130零误差。
'''


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    mode=parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--freeze',action='store_true')
    mode.add_argument('--verify-only',action='store_true')
    args=parser.parse_args()
    freeze() if args.freeze else verify(HERE)
