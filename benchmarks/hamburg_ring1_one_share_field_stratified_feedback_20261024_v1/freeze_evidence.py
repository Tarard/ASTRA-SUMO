"""Freeze the completed field-consistent direction pair; never start SUMO."""
import argparse
from collections import Counter
from copy import deepcopy
import gzip
import hashlib
from importlib.util import module_from_spec, spec_from_file_location
import io
import json
import math
from pathlib import Path
import tempfile
import xml.etree.ElementTree as ET

import numpy as np

HERE=Path(__file__).resolve().parent
REPO=HERE.parents[1]
SOURCE=REPO/'outputs/hamburg-one-share-field-stratified-feedback-20261024-v1'
ONE=REPO/'benchmarks/hamburg_ring1_z5_single_share_20261024_v1'
spec=spec_from_file_location('stratified_archive_helpers',ONE/'freeze_evidence.py')
prior=module_from_spec(spec)
spec.loader.exec_module(prior)
GS,CLASSIC=prior.GS,prior.CLASSIC
raw,load,sha,raw_sha=prior.raw,prior.load,prior.sha,prior.raw_sha
safe_relative,manifest_paths,save=prior.safe_relative,prior.manifest_paths,prior.save
read,jread,snapshot_relative=prior.read,prior.jread,prior.snapshot_relative
verify_manifest,audit_measurement=prior.verify_manifest,prior.audit_measurement
RUNS=('plus','minus')
LOCAL={'instant-e1.xml','queues.xml'}
INDEPENDENT_MANIFEST_SHA='769e33dd1919451e9232d3f61223ca88c797b7638ce9561d0ea62bae967aa0bc'


def source_read(name):
    return read(ONE,Path('one-share')/name)


def selected_files():
    assert INDEPENDENT_MANIFEST_SHA and sha(SOURCE/'independent/manifest.json')==INDEPENDENT_MANIFEST_SHA, 'Wait for the sealed independent pair.'
    assert all((SOURCE/name/'verdict.json').is_file() for name in RUNS), 'Never read active native files.'
    selected={Path('independent')/p:SOURCE/'independent'/p for p in manifest_paths(SOURCE/'independent')}
    for name in ('audit.json','audit_prepared.py','columns406.json','direction.json','prepare.py','README.md','rows182.json','runner-reuse.json','run_candidate.py','solver-inputs406.npz','solver-result.json','plus.check-only.json','minus.check-only.json','plus.execution.log','minus.execution.log'):
        selected[snapshot_relative(name)]=SOURCE/name
    for run in RUNS:
        for name in ('executed-driver.py','od-vector.json','preflight.json','retiming-proposals.json','scope-proof.json','source-snapshot-mapping.json','vector406.json','verdict.json','failure.log'):
            p=Path(run)/name
            if (SOURCE/p).is_file():
                selected[p]=SOURCE/p
        for folder,names in {
            'prepared':('network.net.xml','control.add.xml','demand.rou.xml','lane-intentions.json','field-loops.add.xml','expected-counts.csv','initialization-counts.csv','scenario.json'),
            'evaluation':('demand.rou.xml','lane-intentions.json','od-vector.json','evaluation.json','station-comparison.csv','initialization52-comparison.csv','all182-comparison.csv','535-all56.csv','native208-comparison.csv','contributing-passages.json.gz'),
            'evaluation/native':('network.net.xml','demand.rou.xml','source-e1.add.xml','signal-programs.add.xml','lane-intentions.json','expected-counts.csv','e1.add.xml','instant.add.xml','field-counts.xml','field-comparison.csv','summary.xml','tripinfo.xml','vehicle-routes.xml','collisions.xml','run.json','native-request.json','command.json','stall-monitor.json','sumo.log','sumo-errors.log','instant-e1.xml','queues.xml'),
        }.items():
            for name in names:
                p=Path(run)/folder/name
                selected[p]=SOURCE/p
        for p in sorted((SOURCE/run/'implementation').glob('*.py')):
            selected[p.relative_to(SOURCE)]=p
    return selected


def reference_catalog():
    result=prior.reference_catalog()
    for name in ('evaluation/demand.rou.xml','evaluation/lane-intentions.json','evaluation/contributing-passages.json.gz','evaluation/native/field-counts.xml','verdict.json'):
        # These source artifacts are physically stored in ONE, not unresolved aliases.
        p=ONE/'one-share'/name
        assert raw(p)==source_read(name)
        result[raw_sha(p)]=p
    return result


def native_ms(value):
    return math.floor(float(value)*1000+.5)


def static_read(case,name):
    return read(case,snapshot_relative(name)) if case!=SOURCE else raw(case/name)


def audit_source(case):
    inputs=np.load(io.BytesIO(static_read(case,'solver-inputs406.npz')))
    columns=json.loads(static_read(case,'columns406.json'))
    rows=json.loads(static_read(case,'rows182.json'))
    direction=json.loads(static_read(case,'direction.json'))
    C,q,y=(inputs[n] for n in ('C','q','y'))
    assert C.shape==(182,406) and q.shape==(406,) and np.all(q>0) and sum(q)==8640
    def keys(r):
        return r['detector_id'],float(r['begin']),float(r['end'])
    row_index={keys(r):i for i,r in enumerate(rows)}
    column_index={(r['origin'],r['destination'],r['field'],r['begin']):r['index'] for r in columns}
    assert len(column_index)==406 and len(row_index)==182
    vehicles={v.get('id'):v for v in ET.fromstring(source_read('prepared/demand.rou.xml')).findall('vehicle')}
    metadata={r['vehicle_id']:r for r in json.loads(source_read('prepared/lane-intentions.json'))['vehicles']}
    assigned={}
    for identity,v in vehicles.items():
        edges=v.find('route').get('edges').split()
        assigned[identity]=column_index[edges[0],edges[-1],metadata[identity]['field_id'],native_ms(v.get('depart'))//900000*900]
    assert np.array_equal(q,[sum(c==i for c in assigned.values()) for i in range(406)])
    events=json.loads(gzip.decompress(source_read('evaluation/contributing-passages.json.gz')))['passages']
    actual=np.zeros((182,406),dtype=np.int64)
    outside=0
    for event in events:
        key=event['detector_id'],event['interval_begin_s'],event['interval_end_s']
        if key in row_index:
            actual[row_index[key],assigned[event['vehicle_id']]]+=1
        else:
            outside+=1
    assert len(events)==len({r['vehicle_id'] for r in events})==8640 and outside==241
    assert np.array_equal(actual,C) and np.array_equal(y,[int(r['expected_total']) for r in rows])
    counts={(r.get('id'),float(r.get('begin')),float(r.get('end'))):int(r.get('nVehContrib')) for r in ET.fromstring(source_read('evaluation/native/field-counts.xml'))}
    assert Counter((r['detector_id'],r['interval_begin_s'],r['interval_end_s']) for r in events)==Counter(counts)
    assert np.array_equal(C.sum(axis=1),[counts[keys(r)] for r in rows])
    main=np.array([float(r['begin'])>=3600 for r in rows])
    A=C/q
    assert int(np.linalg.matrix_rank(A[main]))==130 and int(np.linalg.matrix_rank(A))==182
    assert inputs['frozen_columns'].sum()==116 and len(set(inputs['od_groups']))==29
    d=np.array(direction['direction406'],dtype=np.int64)
    assert np.array_equal(q,direction['prior406']) and abs(d).sum()==direction['l1']==32 and np.count_nonzero(d)==direction['changed_columns']==19
    radius=np.maximum(1,q//4)
    assert np.array_equal(inputs['lower'][116:],np.maximum(0,q-radius)[116:]) and np.array_equal(inputs['upper'][116:],(q+radius)[116:])
    for name,sign,expected in [('plus',1,480),('minus',-1,544)]:
        x=q+sign*d
        assert np.array_equal(x,direction[name+'406']) and np.all(x>=inputs['lower']) and np.all(x<=inputs['upper'])
        assert np.array_equal(x[inputs['frozen_columns']],q[inputs['frozen_columns']])
        for group in set(inputs['od_groups']):
            mask=inputs['od_groups']==group
            assert sum(x[mask])==sum(q[mask])
        prediction=A@x
        loss=float(abs(prediction[main]-y[main]).sum())
        assert loss==direction['predicted_main130_TAE'][name] and math.isclose(loss,expected,rel_tol=0,abs_tol=1e-9)
        assert np.max(abs((prediction-y)[inputs['exact_rows']]))<1e-6
    assert np.count_nonzero(q+d==0)==1 and not np.any(q-d==0)
    return {'C_shape':[182,406],'groups':29,'all_prior_positive':True,'minimum_prior':int(min(q)),'rank130':130,'rank182':182,'source_events':8640,'tail_without_real_targets':241,'direction_L1':32,'changed_columns':19,'predicted_main_TAE':direction['predicted_main130_TAE'],'prediction_is_not_native':True,'rank_is_not_zero_feasibility':True}


def audit_scope(case,run):
    old_root=ET.fromstring(source_read('prepared/demand.rou.xml'))
    new_root=ET.fromstring(read(case,Path(run)/'prepared/demand.rou.xml'))
    old,new=[{r.get('id'):r for r in root.findall('vehicle')} for root in (old_root,new_root)]
    old_meta={r['vehicle_id']:r for r in json.loads(source_read('prepared/lane-intentions.json'))['vehicles']}
    new_meta={r['vehicle_id']:r for r in jread(case,Path(run)/'prepared/lane-intentions.json')['vehicles']}
    moves={r['vehicle_id']:r for r in jread(case,Path(run)/'retiming-proposals.json')}
    assert len(moves)==16 and old.keys()==new.keys()==old_meta.keys()==new_meta.keys() and len(new)==8640
    assert [i for i in old if i not in moves]==[i for i in new if i not in moves]
    assert [ET.tostring(v) for v in old_root if v.tag!='vehicle']==[ET.tostring(v) for v in new_root if v.tag!='vehicle']
    model=load(CLASSIC/'prepared/od-model.json')
    indices={(c['origin'],c['destination'],int(c['begin_s'])):i for i,c in enumerate(model['cells'])}
    columns=json.loads(static_read(case,'columns406.json'))
    index406={(c['origin'],c['destination'],c['field'],c['begin']):c['index'] for c in columns}
    inventory294,inventory406,initial=Counter(),Counter(),0
    groups=[Counter(),Counter()]
    for identity,before in old.items():
        after=new[identity]
        restored=deepcopy(after)
        restored.set('depart',before.get('depart'))
        assert ET.tostring(restored)==ET.tostring(before) and old_meta[identity]['field_id']==new_meta[identity]['field_id']
        for side,v in [(0,before),(1,after)]:
            edges=v.find('route').get('edges').split()
            field=old_meta[identity]['field_id']
            groups[side][edges[0],edges[-1],field]+=1
        edges=after.find('route').get('edges').split()
        begin=native_ms(after.get('depart'))//900000*900
        col=indices[edges[0],edges[-1],begin]
        assert new_meta[identity]['od_cell']==col
        inventory294[col]+=1
        inventory406[index406[edges[0],edges[-1],new_meta[identity]['field_id'],begin]]+=1
        if native_ms(before.get('depart'))<3600000:
            initial+=1
            assert identity not in moves and ET.tostring(before)==ET.tostring(after)
        if identity not in moves:
            assert ET.tostring(before)==ET.tostring(after) and old_meta[identity]==new_meta[identity]
        else:
            row=moves[identity]
            assert float(before.get('depart'))==row['source_depart_s'] and float(after.get('depart'))==row['proposed_depart_s']
            assert native_ms(before.get('depart'))!=native_ms(after.get('depart'))
            meta=dict(new_meta[identity])
            assert meta.pop('depart_s')==float(after.get('depart'))
            meta['od_cell']=old_meta[identity]['od_cell']
            assert meta==old_meta[identity]
    assert initial==1953 and groups[0]==groups[1] and len(groups[0])==29
    assert len({k[:2] for k in groups[0]})==21 and len({k[2] for k in groups[0]})==13
    assert ET.tostring(old['od_0209_v00026'])==ET.tostring(new['od_0209_v00026']) and new_meta['od_0209_v00026']['field_id']=='hh_0119_Z_5_30494'
    vector=jread(case,Path(run)/'od-vector.json')
    target=jread(case,Path(run)/'vector406.json')
    assert vector['values']==vector['integer_counts']==[inventory294[i] for i in range(294)]
    assert target['values']==[inventory406[i] for i in range(406)]==json.loads(static_read(case,'direction.json'))[run+'406']
    for name in ('network.net.xml','control.add.xml','field-loops.add.xml','expected-counts.csv','initialization-counts.csv'):
        assert read(case,Path(run)/'prepared'/name)==source_read(Path('prepared')/name)
    before,after=json.loads(source_read('prepared/scenario.json')),jread(case,Path(run)/'prepared/scenario.json')
    assert before['control_assumptions']==after['control_assumptions'] and before['junction_body_hold_nodes']==after['junction_body_hold_nodes']
    return {'departures_changed':16,'other8624_XML_intent_order_fixed':True,'initial1953_fixed':True,'OD_field29_totals_fixed':True,'OD21_field13_totals_fixed':True,'metadata294_and_vector406_separate':True,'new_Z5_donor_not_reset':True,'zero_target406':sum(n==0 for n in target['values'])}


def audit_packet(case):
    independent=jread(case,'independent/summary.json')
    assert independent['schema']=='astra.hamburg.field-stratified-pair-independent/v1' and independent['audit_status']=='pass'
    results={}
    assert not ({r['vehicle_id'] for r in jread(case,'plus/retiming-proposals.json')}&{r['vehicle_id'] for r in jread(case,'minus/retiming-proposals.json')})
    for run in RUNS:
        verdict=jread(case,Path(run)/'verdict.json')
        endpoint=jread(case,Path('independent')/run/'summary.json')
        assert endpoint['schema']=='astra.hamburg.field-stratified-endpoint-independent/v1' and endpoint['audit_status']=='pass'
        assert endpoint['case']==run and endpoint['selection']==verdict['status']
        assert endpoint['reference']==verdict['before'] and endpoint['after']==verdict['after'] and endpoint['global_best']==verdict['global_best']
        assert endpoint['search_reference_gates']==verdict['gates'] and endpoint['global_best_gates']==verdict['global_best_gates']
        assert verdict['native_response_measured'] and not verdict['formal_result']
        assert verdict['before']==json.loads(source_read('verdict.json'))['after']
        p=jread(case,Path(run)/'preflight.json')
        assert p['backend']=='libsumo' and set(p['versions'].values())=={'1.27.1'}
        assert p['search_reference_verdict_sha256']==hashlib.sha256(source_read('verdict.json')).hexdigest()
        assert p['global_best_verdict_sha256']==raw_sha(GS/'full-priority/verdict.json')
        assert hashlib.sha256(read(case,Path(run)/'scope-proof.json')).hexdigest()==p['source_proof_sha256']
        for name,expected in p['prepared_hashes'].items():
            assert hashlib.sha256(read(case,Path(run)/'prepared'/name)).hexdigest()==expected
        for name,expected in p['implementation_sha256'].items():
            assert hashlib.sha256(read(case,Path(run)/'implementation'/name)).hexdigest()==expected
        assert read(case,Path(run)/'executed-driver.py')==static_read(case,'run_candidate.py')
        assert hashlib.sha256(read(case,Path(run)/'executed-driver.py')).hexdigest()==p['executed_driver_sha256']
        signatures={r['vehicle_id']:[r['field_id']] for r in jread(case,Path(run)/'prepared/lane-intentions.json')['vehicles']}
        paths=[r['relative'] for r in load(case/'source-provenance.json')['snapshots']]+list(load(case/'byte-references.json')['aliases'])
        with tempfile.TemporaryDirectory() as temporary:
            temp=Path(temporary)
            for name in paths:
                if name.startswith((run+'/prepared/',run+'/evaluation/')):
                    target=temp/safe_relative(name)
                    target.parent.mkdir(parents=True,exist_ok=True)
                    target.write_bytes(read(case,name))
            measured,_,outside,repeated=audit_measurement(temp/run/'prepared',temp/run/'evaluation',signatures)
            rows=prior.prior.prior.helper.rows(temp/run/'evaluation/all182-comparison.csv')
            fields={'hh_0535_Z_5_30894','hh_0535_Z_6_33855','hh_0535_Z_7_29316','hh_0535_Z_8_34287'}
            selected=[r for r in rows if r['detector_id'] in fields]
            assert len(selected)==56
            tae535=sum(abs(int(r['measured_nVehContrib'])-int(r['expected_total'])) for r in selected)
        assert not outside and not repeated
        for local,saved,size in [('main130','metrics130',130),('initial52','metrics52',52),('all182','metrics182',182)]:
            assert verdict['after'][saved]['rows']==size and all(verdict['after'][saved][k]==v for k,v in measured[local].items())
        assert verdict['after']['completion']==measured['completion'] and verdict['after']['station40_MAE']==measured['station40_MAE']
        gates={'safe':measured['safe'],'healthy':measured['healthy'],'all53556_zero':tae535==0,'no_repeated_vehicle_fields':not repeated,'no_contributions_outside_primary_intention':not outside}
        def reference_gates(reference):
            return {**gates,'main130_MAE_improves':measured['main130']['MAE']<reference['metrics130']['MAE'],'main130_SSE_not_worse':measured['main130']['SSE']<=reference['metrics130']['SSE'],'all182_TAE_not_worse':measured['all182']['TAE']<=reference['metrics182']['TAE']}
        search=reference_gates(verdict['before'])
        global_best=reference_gates(verdict['global_best'])
        baseline=load(GS/'results.json')['audit']['full']
        for local,saved in [('main130','metrics130'),('initial52','metrics52'),('all182','metrics182')]:
            assert all(verdict['global_best'][saved][k]==v for k,v in baseline[local].items())
        assert search==verdict['gates'] and global_best==verdict['global_best_gates']
        expected='working_best_improved_candidate' if all(global_best.values()) else 'search_reference_improved_candidate' if all(search.values()) else 'rejected'
        assert verdict['status']==expected and verdict['all53556']['TAE']==tae535
        results[run]={'raw_verdict':expected,'full':measured,'search_gates':search,'global_best_gates':global_best,'all53556':verdict['all53556'],'scope':audit_scope(case,run)}
    return {'status':'review_required','formal_result':False,'native_runs_by_freeze':0,'search_reference_MAE':3.9384615384615387,'global_best_comparison_MAE':3.7153846153846155,'source_assignment':audit_source(case),'cases':results,'claim_boundary':'C/q and its full row rank are descriptive. Both native direction signs and two reference gates are retained. Improving3.938 does not imply improving3.715, zero fit, true OD or field control.'}


def verify(case):
    verify_manifest(case)
    verify_manifest(ONE)
    verify_manifest(GS)
    refs=load(case/'source-references.json')
    assert sha(ONE/'manifest.json')==refs['search_reference_manifest_sha256'] and sha(GS/'manifest.json')==refs['global_best_manifest_sha256']
    assert hashlib.sha256(read(case,'independent/manifest.json')).hexdigest()==INDEPENDENT_MANIFEST_SHA
    for row in jread(case,'independent/manifest.json')['files']:
        value=read(case,Path('independent')/safe_relative(row['path']))
        assert len(value)==row.get('bytes',row.get('size_bytes')) and hashlib.sha256(value).hexdigest()==row['sha256']
    for row in load(case/'source-provenance.json')['snapshots']:
        value=read(case,row['relative'])
        assert len(value)==row['raw_bytes'] and hashlib.sha256(value).hexdigest()==row['raw_sha256']
    for row in load(case/'historical-source-bindings.json')['bindings']:
        value=read(case,row['target']) if row['scope']=='packet' else raw(REPO/safe_relative(row['target']))
        assert hashlib.sha256(value).hexdigest()==row['sha256']
    for run in RUNS:
        compact=json.loads(gzip.decompress(read(case,Path(run)/'evaluation/contributing-passages.json.gz')))
        instant=next(r for r in load(case/'local-only-large-traces.json')['files'] if r['path'].replace('\\','/').endswith('/'+run+'/evaluation/native/instant-e1.xml'))
        assert instant['sha256'] in {r['sha256'] for r in compact['inputs']}
    assert audit_packet(case)==load(case/'results.json')['audit']
    print('Field-stratified pair hashes,C182x406,scope16each,130/40/52/182/208 and both reference gates: pass',flush=True)


def freeze():
    assert not (HERE/'manifest.json').exists() and not (HERE/'source-provenance.json').exists(), 'Preserve frozen or partial evidence.'
    selected=selected_files()
    catalog,aliases,seen,provenance,local=reference_catalog(),{},{},[],[]
    locations={h:{'scope':'repository','target':p.relative_to(REPO).as_posix()} for h,p in catalog.items()}
    for name,source in sorted(selected.items()):
        assert source.is_file()
        digest=sha(source)
        if source.name in LOCAL:
            local.append({'path':str(source),'bytes':source.stat().st_size,'sha256':digest})
            continue
        relative=name.as_posix()
        if digest in catalog or digest in seen:
            aliases[relative]={**locations[digest],'sha256':digest}
        else:
            target=HERE/(relative+'.gz')
            target.parent.mkdir(parents=True,exist_ok=True)
            target.write_bytes(gzip.compress(source.read_bytes(),mtime=0))
            seen[digest]=relative
            locations[digest]={'scope':'packet','target':relative}
        provenance.append({'source':str(source),'relative':relative,'raw_bytes':source.stat().st_size,'raw_sha256':digest})
    save(HERE/'source-provenance.json',{'snapshots':provenance,'source_files_unchanged':True})
    save(HERE/'byte-references.json',{'aliases':aliases,'rule':'Only byte-identical SHA-bound fixed inputs or duplicate snapshots are referenced.'})
    bindings=[]
    for run in RUNS:
        for original,digest in load(SOURCE/run/'scope-proof.json')['source_hashes'].items():
            assert digest in locations,(original,digest)
            bindings.append({'declared_path':original,'sha256':digest,**locations[digest]})
    save(HERE/'historical-source-bindings.json',{'bindings':bindings,'original_hashes_unchanged':True})
    save(HERE/'local-only-large-traces.json',{'files':local,'limitation':'Raw instant/queues remain local; full compact contributions and208 counts remain portable.'})
    save(HERE/'source-references.json',{'search_reference_commit':'a889d72','search_reference_manifest_sha256':sha(ONE/'manifest.json'),'global_best_commit':'42a827e','global_best_manifest_sha256':sha(GS/'manifest.json'),'independent_manifest_sha256':INDEPENDENT_MANIFEST_SHA,'later_candidates_included':False})
    result=audit_packet(HERE)
    save(HERE/'results.json',{'status':'review_required','formal_result':False,'audit':result})
    (HERE/'.gitattributes').write_text('* -text whitespace=blank-at-eol,blank-at-eof,space-before-tab,cr-at-eol\n*.gz binary\n',encoding='utf-8')
    (HERE/'README.md').write_text(readme(result),encoding='utf-8')
    assert all(sha(Path(r['source']))==r['raw_sha256'] for r in provenance)
    save(HERE/'manifest.json',{'status':'review_required','formal_result':False,'files':[{'path':p.relative_to(HERE).as_posix(),'bytes':p.stat().st_size,'sha256':sha(p)} for p in sorted(HERE.rglob('*')) if p.is_file() and p!=HERE/'manifest.json' and '__pycache__' not in p.parts]})
    verify(HERE)


def readme(result):
    lines=[]
    for run in RUNS:
        r=result['cases'][run]
        m=r['full']
        lines.append(f"| {run} | {r['raw_verdict']} | {m['main130']['MAE']} / {m['main130']['SSE']} / {m['main130']['TAE']} | {m['initial52']['TAE']} | {m['all182']['TAE']} | {m['station40_MAE']} | {m['completion']['arrived']}/{m['completion']['waiting']}/{m['completion']['running']} |")
    return '''# 保字段的OD时段方向：同源正负两侧原生对照

搜索参考是a889d72的健康one-share（主MAE3.9384615、8640全到达），全局最好对照仍是42a827e的3.7153846。本包只保存同一方向的plus/minus两次完整原生运行。search-reference改善与global-best改善分别保留8项gates；仅胜3.938不能称超过3.715。audit pass不等于候选采用或标定完成。

| 侧 | 原始判定 | 主130 MAE / SSE / TAE | 初始化52 TAE | 全182 TAE | 派生40 MAE | 到达/等待/在途 |
|---|---|---|---:|---:|---:|---|
'''+ '\n'.join(lines)+'''

当前8640实例按29个(OD,field)×14时段分成406列。C182×406由source实际完整贡献重建，原生208行完整对账；其中241个尾部事件没有真实targets，不填假零。130/182行数值秩为130/182，均只是同次经验分配诊断。C/q不是Jacobian，满行秩既不识别406个未知量，也不证明整数、守恒和初始化限制下零误差可行。

只求一个19列、L1=32方向，两侧各改16辆depart，保持29组、21OD、13field总量，固定1953初始化请求与其余8624辆XML/意向及相对顺序。逐field调用原stable helper，使优化分组和实际field约束一致；不重新生成OD。metadata od_cell保持实际294索引，406只在单独vector406保存；v26新增Z5 stop/field未重置。

半径明确为max(1,floor(q×0.25))、下界截0，允许小q改1。源406 prior全正，plus有1个target列到0，minus无零target；没有把零target当零source。主130描述预测plus480/minus544，不能当原生结果。实际时移没有900秒限幅，最大6291.912秒。两侧各自完整130/40/52/182/208、健康、53556、重复/额外意图、执行实现和独立审计全保留。

两侧各自不同16辆，L1=32表示数量预算，不表示小时间扰动。paired-native208-response保留逐行半差(plus−minus)/2与对称偏差plus+minus−2source；这只是该有限方向的实测总响应，不称导数，也不作控制反馈的因果分离。两侧原判均rejected，搜索参考与全局最好对照均不变。

网络/控制/真实targets/探头/所有ID、route、stop、field及四bodyhold保持来源，Libsumo1.27.1/0.1s/seed104/14400秒一致。field先验和控制仍是条件模型，不是现场SPaT、真实OD或真实lane份额。预备阶段source README中的“未运行”是原样历史快照，不是终态。本包不包含后续方向或结果。

大型旧source按固定包/SHA引用，同字节snapshot由byte-references去重。大instant/queues仅localhash，完整compact贡献保存；缺原件不能迁机逐事件重读。freeze、verify及reproduce只离线核算，不启动SUMO，需同仓库固定包和项目.venv。未改公共docs或提交，未宣称130零误差。
'''


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    mode=parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--freeze',action='store_true')
    mode.add_argument('--verify-only',action='store_true')
    args=parser.parse_args()
    freeze() if args.freeze else verify(HERE)
