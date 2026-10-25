"""Freeze five completed900s prefixes and one rejected full validation; no SUMO."""
import argparse
from collections import Counter
from copy import deepcopy
import csv
import gzip
import hashlib
from importlib.util import module_from_spec,spec_from_file_location
import io
import json
from pathlib import Path
import tempfile
import xml.etree.ElementTree as ET

HERE=Path(__file__).resolve().parent
REPO=HERE.parents[1]
SOURCE=REPO/'outputs/hamburg-causal-initial-prefix-20261024-v1'
PREVIOUS=REPO/'benchmarks/hamburg_ring1_normal_exit_prior_20261024_v1'
spec=spec_from_file_location('causal_archive_helpers',PREVIOUS/'freeze_evidence.py')
prior=module_from_spec(spec)
spec.loader.exec_module(prior)
ONE,GS=prior.ONE,prior.GS
raw,load,sha,raw_sha=prior.raw,prior.load,prior.sha,prior.raw_sha
read,jread,source_read=prior.read,prior.jread,prior.source_read
safe_relative,snapshot_relative,manifest_paths,save=prior.safe_relative,prior.snapshot_relative,prior.manifest_paths,prior.save
verify_manifest,audit_measurement=prior.verify_manifest,prior.audit_measurement
PREFIXES={'reference':22,'advance22':22,'advance225':7,'advance-first900':7,'advance-first900-120225':0}
SELECTED='advance-first900-120225'
FULL=Path(SELECTED)/'full-evaluation'
SEALED={'independent':'5d371f95cd54165ea2c9f30fb4f576f4e9d97f2b32d6593a8d4e9c8abbdbd79c',
    'advance225-independent':'5ccda29332bb1077f2a7ee72fe555e827a7caef042e197b6520e152f3ee83ba9',
    'advance-first900-independent':'ae9cbb0e012608a3614d326014f79cb271d96e57dd9a9a66f12984d0e345f8a5',
    'advance-first900-120225-independent':'bd332a4d1f3e986a889071484ec0d3d116cb8e6b7fe8cc21486c6a98fc1b9ed0',
    'full-independent':'c39bcc735769548b725bfda68761f152db581c598245fb204d500c5974dbf2ac'}
EXTERNAL={'service-evidence':('hamburg-119-cumulative-service-20261024-v1','b26491d24cf7dc01f7fd0588f032480429cf8c4934030df42feef0202614eacb'),
    'stock-evidence':('hamburg-first-stock-audit-20261024-v1','a436c3cd10b00e4f3203a67a23bd57dfe097f327d283ba7143ac2982627e9e5b')}
PREPARED=('network.net.xml','control.add.xml','demand.rou.xml','field-loops.add.xml','expected-counts.csv','initialization-counts.csv','lane-intentions.json','scenario.json')
NATIVE=('network.net.xml','demand.rou.xml','source-e1.add.xml','signal-programs.add.xml','lane-intentions.json','expected-counts.csv','e1.add.xml','instant.add.xml','field-counts.xml','field-comparison.csv','summary.xml','tripinfo.xml','vehicle-routes.xml','collisions.xml','run.json','native-request.json','command.json','stall-monitor.json','sumo.log','sumo-errors.log','instant-e1.xml','queues.xml')


def selected_files():
    assert (SOURCE/FULL/'verdict.json').is_file()
    selected={}
    for folder,expected in SEALED.items():
        assert sha(SOURCE/folder/'manifest.json')==expected
        selected.update({Path(folder)/p:SOURCE/folder/p for p in manifest_paths(SOURCE/folder)})
    for prefix,(folder,expected) in EXTERNAL.items():
        root=REPO/'outputs'/folder
        assert sha(root/'manifest.json')==expected
        selected.update({Path(prefix)/p:root/p for p in manifest_paths(root)})
    names=['prefix13.csv','prefix13-provenance.json','prepare_reference.py','prepare_advance22.py','prepare_advance225.py','prepare_advance_first900.py','prepare_advance_first900_120225.py','advance225-cohort.csv','advance-first900-cohort.csv','run_prefix.py','run_full.py','freeze-plan.json','full-120225.check-only.json','full-120225.execution.log']
    names += [run+suffix for run in PREFIXES for suffix in ('.check-only.json','.selfcheck.json','.execution.log')]
    for name in names:
        selected[snapshot_relative(name)]=SOURCE/name
    for run in PREFIXES:
        for name in ('scope-proof.json','departure-proposals.json','metadata-changes.json','od-vector.json','record-result.json','preflight.json','executed-run.py','core-result.json','contributing-passages.json.gz','all182-coverage.json','selfchecks.json','failure.json','failure.log'):
            p=Path(run)/name
            if (SOURCE/p).is_file():
                selected[p]=SOURCE/p
        for name in PREPARED:
            selected[Path(run)/'prepared'/name]=SOURCE/run/'prepared'/name
        for name in NATIVE:
            selected[Path(run)/'native'/name]=SOURCE/run/'native'/name
        for folder in ('implementation','source-snapshot'):
            selected.update({p.relative_to(SOURCE):p for p in (SOURCE/run/folder).rglob('*') if p.is_file() and '__pycache__' not in p.parts})
    for name in ('preflight.json','executed-run_full.py','executed-prefix-checker.py','core-result.json','verdict.json','prefix-parity.json','demand.rou.xml','lane-intentions.json','od-vector.json','evaluation.json','station-comparison.csv','initialization52-comparison.csv','all182-comparison.csv','native208-comparison.csv','535-all56.csv','contributing-passages.json.gz','failure.log'):
        p=FULL/name
        if (SOURCE/p).is_file():
            selected[p]=SOURCE/p
    for name in NATIVE:
        selected[FULL/'native'/name]=SOURCE/FULL/'native'/name
    selected.update({p.relative_to(SOURCE):p for p in (SOURCE/FULL/'implementation').glob('*.py')})
    return selected


def reference_catalog():
    result=prior.reference_catalog()
    for name in ('field-counts.xml','summary.xml','vehicle-routes.xml','tripinfo.xml','stall-monitor.json'):
        p=ONE/'one-share/evaluation/native'/name
        assert raw(p)==source_read(Path('evaluation/native')/name)
        result[raw_sha(p)]=p
    p=REPO/'benchmarks/hamburg_ring1_healthy_joint_od_stable_20261024_v1/stable-64/implementation/hamburg_departure_probe.py'
    result[raw_sha(p)]=p
    return result


def rows(case,name):
    data=raw(SOURCE/Path(name).relative_to('study-source')) if case==SOURCE and str(name).replace('\\','/').startswith('study-source/') else read(case,name)
    return list(csv.DictReader(io.StringIO(data.decode('utf-8-sig'))))


def key(r):
    return r['detector_id'],float(r['begin']),float(r['end'])


def intervals(data):
    return {(r.get('id'),float(r.get('begin')),float(r.get('end'))):dict(r.attrib) for r in ET.fromstring(data) if r.tag=='interval' and float(r.get('begin'))<float(r.get('end'))}


def summary_prefix(data):
    return [{k:v for k,v in r.attrib.items() if k!='duration'} for r in ET.fromstring(data).findall('step') if float(r.get('time'))<900]


def check_coverage(coverage,native,expected):
    targets={key(r):int(r['expected_total']) for r in expected}
    assert len(coverage)==len({key(r) for r in coverage})==len(targets)==182
    assert {key(r) for r in coverage}==targets.keys()
    assert sum(r['measured_nVehContrib'] is None for r in coverage)==169
    for r in coverage:
        assert int(r['expected_total'])==targets[key(r)]
        if key(r) in native:
            assert int(r['measured_nVehContrib'])==int(native[key(r)]['nVehContrib'])
        else:
            assert r['measured_nVehContrib'] is None


def audit_input(case,run):
    proof=jread(case,Path(run)/'scope-proof.json')
    assert proof['N']==8640 and proof['status']=='pass'
    for name,expected in proof['prepared_hashes'].items():
        assert hashlib.sha256(read(case,Path(run)/'prepared'/name)).hexdigest()==expected
    old_root=ET.fromstring(source_read('prepared/demand.rou.xml'))
    root=ET.fromstring(read(case,Path(run)/'prepared/demand.rou.xml'))
    old,new=[{v.get('id'):v for v in r.findall('vehicle')} for r in (old_root,root)]
    old_meta={r['vehicle_id']:r for r in json.loads(source_read('prepared/lane-intentions.json'))['vehicles']}
    meta={r['vehicle_id']:r for r in jread(case,Path(run)/'prepared/lane-intentions.json')['vehicles']}
    moves={r['vehicle_id']:r for r in jread(case,Path(run)/'departure-proposals.json')}
    assert old.keys()==new.keys()==meta.keys() and len(new)==8640
    assert len(moves)=={'reference':0,'advance22':22,'advance225':225,'advance-first900':434,SELECTED:434}[run]
    assert [i for i in old if i not in moves]==[i for i in new if i not in moves]
    assert [ET.tostring(v) for v in old_root if v.tag!='vehicle']==[ET.tostring(v) for v in root if v.tag!='vehicle']
    for identity,v in new.items():
        restored=deepcopy(v)
        restored.set('depart',old[identity].get('depart'))
        assert ET.tostring(restored)==ET.tostring(old[identity])
        if identity in moves:
            m=moves[identity]
            assert abs(float(v.get('depart'))-m['proposed_depart_s'])<1e-8 and abs(float(old[identity].get('depart'))-m['source_depart_s'])<1e-8
            assert 0<=prior.prior.native_ms(v.get('depart'))<900000
            assert 0<=prior.prior.native_ms(old[identity].get('depart'))<900000
            r=dict(meta[identity])
            assert abs(r.pop('depart_s')-float(v.get('depart')))<1e-8 and r==old_meta[identity]
        else:
            assert ET.tostring(v)==ET.tostring(old[identity]) and meta[identity]==old_meta[identity]
    for name in ('network.net.xml','control.add.xml','field-loops.add.xml','expected-counts.csv','initialization-counts.csv'):
        assert read(case,Path(run)/'prepared'/name)==source_read(Path('prepared')/name)
    return {'full_future_requests':8640,'changed_departures':len(moves),'routes_stops_fields_and_native_departure_bins_fixed':True}


def audit_prefix(case,run):
    record=jread(case,Path(run)/'record-result.json')
    p=jread(case,Path(run)/'preflight.json')
    assert record['status']=='pass' and all(record['checks'].values()) and all(record['reference_parity'].values())
    assert hashlib.sha256(read(case,Path(run)/'preflight.json')).hexdigest()==record['preflight_sha256']
    runner=hashlib.sha256(read(case,Path(run)/'executed-run.py')).hexdigest()
    assert p['runner_sha256']==runner
    assert p['scope_sha256']==hashlib.sha256(read(case,Path(run)/'scope-proof.json')).hexdigest()
    assert p['prepared_hashes']==jread(case,Path(run)/'scope-proof.json')['prepared_hashes']
    for original,expected in record['native_output_hashes'].items():
        name=original.replace('\\','/').split('/')[-1]
        assert hashlib.sha256(read(case,Path(run)/'native'/name)).hexdigest()==expected
    for name,expected in p['implementation_sha256'].items():
        assert hashlib.sha256(read(case,Path(run)/'implementation'/name)).hexdigest()==expected
    native=intervals(read(case,Path(run)/'native/field-counts.xml'))
    targets=rows(case,'study-source/prefix13.csv')
    assert len(native)==len(targets)==13 and set(native)=={key(r) for r in targets}
    errors=[int(native[key(r)]['nVehContrib'])-int(r['expected_total']) for r in targets]
    metrics={'rows':13,'TAE':sum(abs(v) for v in errors),'SSE':sum(v*v for v in errors),'MAE':sum(abs(v) for v in errors)/13,'max_abs_error':max(abs(v) for v in errors)}
    assert record['prefix13_metrics']==metrics and metrics['TAE']==PREFIXES[run]
    coverage=jread(case,Path(run)/'all182-coverage.json')
    check_coverage(coverage,native,rows(case,Path(run)/'prepared/initialization-counts.csv')+rows(case,Path(run)/'prepared/expected-counts.csv'))
    assert record['full130_metrics'] is None and record['remaining39_initial_metrics'] is None and record['combined182_metrics'] is None
    assert record['full_health_not_claimed'] and not record['full130_calibration_acceptance']
    compact=json.loads(gzip.decompress(read(case,Path(run)/'contributing-passages.json.gz')))
    assert compact['reconciled'] and Counter((e['detector_id'],e['interval_begin_s'],e['interval_end_s']) for e in compact['passages'])==Counter({k:int(r['nVehContrib']) for k,r in native.items()})
    assert all(abs(e['notification_time_s']-900)<1e-8 for e in compact['outside_periodic_intervals'])
    assignments={r['vehicle_id']:r['field_id'] for r in jread(case,Path(run)/'prepared/lane-intentions.json')['vehicles']}
    complete=compact['passages']+compact['outside_periodic_intervals']
    assert len({(e['vehicle_id'],e['detector_id']) for e in complete})==len(complete)
    assert all(assignments[e['vehicle_id']]==e['detector_id'] for e in complete)
    assert hashlib.sha256(read(case,Path(run)/'native/instant-e1.xml')).hexdigest() in {r['sha256'] for r in compact['inputs']}
    steps=summary_prefix(read(case,Path(run)/'native/summary.xml'))
    assert len(steps)==9000 and float(steps[-1]['time'])==899.9
    assert not any(int(steps[-1][n]) for n in ('collisions','teleports','discarded'))
    return {'validity_status':'pass','measured_initialization_rows':13,'prefix13':metrics,'future169_measured_values':None,'full_completion_not_claimed':True,'executed_runner_sha256':runner,'input':audit_input(case,run)}


def materialize_full(case,out):
    for name in PREPARED:
        p=out/'prepared'/name
        p.parent.mkdir(parents=True,exist_ok=True)
        p.write_bytes(read(case,Path(SELECTED)/'prepared'/name))
    names=[r['relative'] for r in load(case/'source-provenance.json')['snapshots']]+list(load(case/'byte-references.json')['aliases'])
    for name in names:
        if name.startswith(FULL.as_posix()+'/'):
            p=out/'evaluation'/Path(name).relative_to(FULL)
            p.parent.mkdir(parents=True,exist_ok=True)
            p.write_bytes(read(case,name))


def prefix_full_parity(case,evaluation):
    original=intervals(read(case,Path(SELECTED)/'native/field-counts.xml'))
    current={k:r for k,r in intervals((evaluation/'native/field-counts.xml').read_bytes()).items() if k[1:]==(0,900)}
    return dict(all13_attributes=original==current,
        summary9000_except_CPU_duration=summary_prefix(read(case,Path(SELECTED)/'native/summary.xml'))==summary_prefix((evaluation/'native/summary.xml').read_bytes()))


def audit_full(case):
    verdict=jread(case,FULL/'verdict.json')
    independent=jread(case,'full-independent/summary.json')
    assert independent['schema']=='astra.hamburg.causal-initial-full-independent/v1' and independent['audit_status']=='pass'
    assert verdict['status']==independent['selection']=='rejected'
    assert verdict['after']==independent['after'] and verdict['before']==independent['reference'] and verdict['global_best']==independent['global_best']
    assert verdict['gates']==independent['gates'] and verdict['global_best_gates']==independent['global_best_gates']
    assert verdict['prefix_parity_pass'] and all(jread(case,FULL/'prefix-parity.json')['checks'].values()) and all(independent['first900_strict_parity'].values())
    assert jread(case,FULL/'core-result.json')['errors']==[]
    p=jread(case,FULL/'preflight.json')
    assert hashlib.sha256(read(case,FULL/'executed-run_full.py')).hexdigest()==p['full_runner_sha256']
    assert hashlib.sha256(read(case,FULL/'executed-prefix-checker.py')).hexdigest()==p['prefix_checker_sha256']
    for name,expected in p['prepared_hashes'].items():
        assert hashlib.sha256(read(case,Path(SELECTED)/'prepared'/name)).hexdigest()==expected
    for name,expected in p['implementation_sha256'].items():
        assert hashlib.sha256(read(case,FULL/'implementation'/name)).hexdigest()==expected
    with tempfile.TemporaryDirectory() as temporary:
        temp=Path(temporary)
        materialize_full(case,temp)
        signatures={r['vehicle_id']:[r['field_id']] for r in json.loads((temp/'prepared/lane-intentions.json').read_bytes())['vehicles']}
        measured,_,outside,repeated=audit_measurement(temp/'prepared',temp/'evaluation',signatures)
        assert all(prefix_full_parity(case,temp/'evaluation').values())
    assert not outside and not repeated
    for local,saved in [('main130','metrics130'),('initial52','metrics52'),('all182','metrics182')]:
        assert all(verdict['after'][saved][k]==v for k,v in measured[local].items())
    assert measured['completion']==verdict['after']['completion'] and measured['station40_MAE']==verdict['after']['station40_MAE']
    assert verdict['all53556']==independent['all53556'] and verdict['all53556']['TAE']==0
    return {'raw_verdict':'rejected','full':measured,'prefix13_still_exact':True,'full_prefix_parity_pass':True,'all53556':verdict['all53556'],'gates':verdict['gates'],'global_best_gates':verdict['global_best_gates'],'claim_boundary':'Only13initializationrowszero. Main130regresses and133unfinished; neither full calibration nor full completion achieved.'}


def audit_packet(case):
    prefixes={name:audit_prefix(case,name) for name in PREFIXES}
    versions=sorted({r['executed_runner_sha256'] for r in prefixes.values()})
    assert len(versions)==4
    return {'status':'review_required','formal_result':False,'native_runs_recorded':6,'native_runs_by_freeze':0,'prefixes':prefixes,'distinct_executed_runner_versions':versions,'selected_full':audit_full(case),'global_best_MAE_unchanged':3.7153846153846155,'no_second_prefix_study_included':True}


def verify(case):
    verify_manifest(case)
    verify_manifest(ONE)
    verify_manifest(GS)
    refs=load(case/'source-references.json')
    assert sha(ONE/'manifest.json')==refs['source_manifest_sha256'] and sha(GS/'manifest.json')==refs['best_manifest_sha256']
    for prefix,expected in {**SEALED,**{k:v[1] for k,v in EXTERNAL.items()}}.items():
        assert hashlib.sha256(read(case,Path(prefix)/'manifest.json')).hexdigest()==expected
        for row in jread(case,Path(prefix)/'manifest.json')['files']:
            value=read(case,Path(prefix)/safe_relative(row['path']))
            assert len(value)==row.get('bytes',row.get('size_bytes')) and hashlib.sha256(value).hexdigest()==row['sha256']
    for row in load(case/'source-provenance.json')['snapshots']:
        value=read(case,row['relative'])
        assert len(value)==row['raw_bytes'] and hashlib.sha256(value).hexdigest()==row['raw_sha256']
    for row in load(case/'historical-source-bindings.json')['bindings']:
        value=read(case,row['target']) if row['scope']=='packet' else raw(REPO/safe_relative(row['target']))
        assert hashlib.sha256(value).hexdigest()==row['sha256']
    assert audit_packet(case)==load(case/'results.json')['audit']
    print('Fiveprefix13/null169,sixnativecalls,fourrunnerversions,full130/40/52/182/208 and rejectedfull: pass',flush=True)


def freeze():
    assert not (HERE/'manifest.json').exists() and not (HERE/'source-provenance.json').exists()
    chosen=selected_files()
    locations={h:{'scope':'repository','target':p.relative_to(REPO).as_posix()} for h,p in reference_catalog().items()}
    aliases,provenance,local={ },[],[]
    for name,source in sorted(chosen.items()):
        digest=sha(source)
        if source.name=='queues.xml' or (name.is_relative_to(FULL) and source.name=='instant-e1.xml'):
            local.append({'path':str(source),'bytes':source.stat().st_size,'sha256':digest})
            continue
        relative=name.as_posix()
        if digest in locations:
            aliases[relative]={**locations[digest],'sha256':digest}
        else:
            p=HERE/(relative+'.gz')
            p.parent.mkdir(parents=True,exist_ok=True)
            p.write_bytes(gzip.compress(source.read_bytes(),mtime=0))
            locations[digest]={'scope':'packet','target':relative}
        provenance.append({'source':str(source),'relative':relative,'raw_bytes':source.stat().st_size,'raw_sha256':digest})
    save(HERE/'source-provenance.json',{'snapshots':provenance,'source_files_unchanged':True})
    save(HERE/'byte-references.json',{'aliases':aliases,'rule':'Onlybyte-identicalSHA-boundoldsourceorduplicatefilesreferenced.'})
    bindings=[]
    for run in PREFIXES:
        for original,digest in load(SOURCE/run/'scope-proof.json')['source_hashes'].items():
            assert digest in locations,(original,digest)
            bindings.append({'declared_path':original,'sha256':digest,**locations[digest]})
    save(HERE/'historical-source-bindings.json',{'bindings':bindings,'original_hashes_not_rewritten':True})
    save(HERE/'local-only-large-traces.json',{'files':local,'prefix_instant_files_gzip_included':True,'full_instant_and_all_queues_local_only':True})
    save(HERE/'source-references.json',{'source_commit':'a889d72','source_manifest_sha256':sha(ONE/'manifest.json'),'best_commit':'42a827e','best_manifest_sha256':sha(GS/'manifest.json'),'second_prefix_study_included':False})
    result=audit_packet(HERE)
    save(HERE/'results.json',{'status':'review_required','formal_result':False,'audit':result})
    (HERE/'.gitattributes').write_text('* -text whitespace=blank-at-eol,blank-at-eof,space-before-tab,cr-at-eol\n*.gz binary\n',encoding='utf-8')
    (HERE/'README.md').write_text(README,encoding='utf-8')
    assert all(sha(Path(r['source']))==r['raw_sha256'] for r in provenance)
    save(HERE/'manifest.json',{'status':'review_required','formal_result':False,'files':[{'path':p.relative_to(HERE).as_posix(),'bytes':p.stat().st_size,'sha256':sha(p)} for p in sorted(HERE.rglob('*')) if p.is_file() and p!=HERE/'manifest.json' and '__pycache__' not in p.parts]})
    verify(HERE)


README='''# 首13初始化计数归零与完整运行回退

本包保留六次已完成原生调用：5次0–900秒prefix及唯一14400秒full验证。首13行零误差只属于初始化第一窗，不能写成130主窗归零。full原判rejected：主130 MAE5.46923077、TAE711、SSE11917、max31；52初始化TAE86/SSE1088、全182TAE797、40派生MAE15.675。8594插入、8507到达、87在途、46等待，零碰撞/传送/discard，8522完整事件，535指定56行仍零。全局最好主MAE3.7153846不变。

| Prefix | 首13 TAE | SSE | 范围 |
|---|---:|---:|---|
| reference |22|234|13初始化行|
| advance22 |22|244|13初始化行|
| advance225 |7|17|13初始化行|
| advance-first900 |7|25|13初始化行|
| advance-first900-120225 |0|0|13初始化行|

每个prefix保留全部8640未来请求和全部130+52真实观测；169个未测值显式null，不填0，也不计算未测主130成绩。短时核心的完整人口完成状态不适用于prefix；18项原生/安全/贡献检查另列，不把正常未出发或跨900秒车辆叫作失败。全部未归零候选保留。实际执行有4种脚本字节：三份旧127/577/cd09及最后303，历史版本不会被当前checker覆盖。

434辆最终候选的首13实测零、434唯一预期贡献、无unsafe。完整运行从同一输入由0连续执行，13全E1属性与9000条非CPU summary的5项原parity均过；独立审计另保留事件、到达及hold语义的7项parity。然而后续主窗和完成情况回退，133未完成均为未改8206车辆。不能把前缀分窗独立优化或用旧lag预测未来。初始化累计900/1800/3600、全部130/40/52/182/208、未完成ID、cohort及两套reference gates完整保存。

service-evidence保留首900漏1/8/13个119 Z11/Z12/Z13贡献的原生服务诊断；stock-evidence保留182项计划库存审计及仅4项不符表。请求departure库存不是实际passage计数，不据此推断观测错误或零可行性。控制、OD与lane先验仍是条件模型，不是现场SPaT。未包括新的1800秒分支或任何未完成方向。

离线核验：`.venv\\Scripts\\python.exe benchmarks/hamburg_ring1_causal_initial_prefix_20261024_v1/reproduce.py --mode verify`，不启动SUMO。

显式重新原生运行：同项目.venv及记录的Libsumo/SUMO1.27.1实现下，运行`.venv\\Scripts\\python.exe benchmarks/hamburg_ring1_causal_initial_prefix_20261024_v1/reproduce.py --mode native-full --output-dir outputs/reproduce-causal-initial-full`。它从本包及固定旧包解压显式输入，以现仓库路径调用core，不依赖历史outputs绝对路径、不生成OD。必须使用新目录，重新保存完整原生及误差对照。该模式供另行显式执行；本次冻结没有额外SUMO。离线检查不等于重新原生复现。

5份短prefix instant均gzip保存，full instant及所有queues仅localhash；完整贡献JSON保存。缺原大文件不能迁机重读旧full逐事件轨迹。重复source按固定包/SHA引用，原始绝对路径仅溯源。初始13零与full失败、有效性与采用、prefix与主130始终分开。
'''


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    mode=parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--freeze',action='store_true')
    mode.add_argument('--verify-only',action='store_true')
    args=parser.parse_args()
    freeze() if args.freeze else verify(HERE)
