"""Freeze three completed1800s initialization prefixes, without a full-run claim."""
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
import xml.etree.ElementTree as ET

HERE=Path(__file__).resolve().parent
REPO=HERE.parents[1]
SOURCE=REPO/'outputs/hamburg-causal-second-prefix-20261024-v1'
STAGE1=REPO/'benchmarks/hamburg_ring1_causal_initial_prefix_20261024_v1'
spec=spec_from_file_location('stage2_archive_helpers',STAGE1/'freeze_evidence.py')
prior=module_from_spec(spec)
spec.loader.exec_module(prior)
raw,load,sha,raw_sha=prior.raw,prior.load,prior.sha,prior.raw_sha
read,jread=prior.read,prior.jread
safe_relative,snapshot_relative,manifest_paths,save=prior.safe_relative,prior.snapshot_relative,prior.manifest_paths,prior.save
verify_manifest=prior.verify_manifest
intervals=prior.intervals
PREPARED,NATIVE=prior.PREPARED,prior.NATIVE
RUNS={'reference':30,'advance-second900-120232':3,'advance-second900-126232':0}
SELECTED='advance-second900-126232'
SEALED={'independent':'922684495f75a983996b264d70b1386f95d950e785761f3df20efc1315b68e66',
    'independent-126232':'828d73da50c0b705948b718309c4ef77e81814f0a9c03849dbe3b2d89344ed76',
    'agent-backlog-boundary-evidence':'5f34f054eaf62684a70ed26300f56434eaec559a8f92b04477c07ea45495f389'}


def source_read(name):
    return read(STAGE1,Path(prior.SELECTED)/name)


def summary_prefix(data,end=1800):
    return [{k:v for k,v in r.attrib.items() if k!='duration'} for r in ET.fromstring(data).findall('step') if float(r.get('time'))<end]


def rows(case,name):
    data=raw(SOURCE/Path(name).relative_to('study-source')) if case==SOURCE and str(name).replace('\\','/').startswith('study-source/') else read(case,name)
    return list(csv.DictReader(io.StringIO(data.decode('utf-8-sig'))))


def selected_files():
    selected={}
    for folder,expected in SEALED.items():
        assert sha(SOURCE/folder/'manifest.json')==expected
        selected.update({Path(folder)/p:SOURCE/folder/p for p in manifest_paths(SOURCE/folder)})
    names=['prefix26.csv','prefix26-provenance.json','prepare_reference.py','prepare_second900.py','prepare_second900_126232.py','run_prefix.py','second900-cohort.csv']
    names += [run+suffix for run in RUNS for suffix in ('.check-only.json','.execution.log','.selfcheck.json')]
    for name in names:
        selected[snapshot_relative(name)]=SOURCE/name
    for run in RUNS:
        assert (SOURCE/run/'record-result.json').is_file()
        for name in ('all182-coverage.json','contributing-passages.json.gz','core-result.json','departure-proposals.json','executed-run.py','metadata-changes.json','od-vector.json','preflight.json','record-result.json','scope-proof.json','selfchecks.json','failure.json','failure.log'):
            p=Path(run)/name
            if (SOURCE/p).is_file():
                selected[p]=SOURCE/p
        for name in PREPARED:
            selected[Path(run)/'prepared'/name]=SOURCE/run/'prepared'/name
        for name in NATIVE:
            selected[Path(run)/'native'/name]=SOURCE/run/'native'/name
        for folder in ('implementation','source-snapshot'):
            selected.update({p.relative_to(SOURCE):p for p in (SOURCE/run/folder).rglob('*') if p.is_file() and '__pycache__' not in p.parts})
    return selected


def physical_reference(case,name):
    relative=safe_relative(str(name))
    p=case/relative
    if p.is_file() or Path(str(p)+'.gz').is_file():
        return p
    row=load(case/'byte-references.json')['aliases'][relative.as_posix()]
    return physical_reference(case,row['target']) if row['scope']=='packet' else REPO/safe_relative(row['target'])


def reference_catalog():
    result=prior.reference_catalog()
    for row in load(STAGE1/'source-provenance.json')['snapshots']:
        p=physical_reference(STAGE1,row['relative'])
        assert raw_sha(p)==row['raw_sha256']
        result[row['raw_sha256']]=p
    return result


def check_coverage(coverage,native,expected):
    targets={prior.key(r):int(r['expected_total']) for r in expected}
    assert len(coverage)==len({prior.key(r) for r in coverage})==len(targets)==182
    assert {prior.key(r) for r in coverage}==targets.keys() and sum(r['measured_nVehContrib'] is None for r in coverage)==156
    for r in coverage:
        assert int(r['expected_total'])==targets[prior.key(r)]
        if prior.key(r) in native:
            assert int(r['measured_nVehContrib'])==int(native[prior.key(r)]['nVehContrib'])
        else:
            assert r['measured_nVehContrib'] is None


def audit_input(case,run):
    proof=jread(case,Path(run)/'scope-proof.json')
    assert proof['status']=='pass' and proof['N']==8640
    for name,expected in proof['prepared_hashes'].items():
        assert hashlib.sha256(read(case,Path(run)/'prepared'/name)).hexdigest()==expected
    old_root=ET.fromstring(source_read('prepared/demand.rou.xml'))
    root=ET.fromstring(read(case,Path(run)/'prepared/demand.rou.xml'))
    old,new=[{v.get('id'):v for v in r.findall('vehicle')} for r in (old_root,root)]
    old_meta={r['vehicle_id']:r for r in json.loads(source_read('prepared/lane-intentions.json'))['vehicles']}
    meta={r['vehicle_id']:r for r in jread(case,Path(run)/'prepared/lane-intentions.json')['vehicles']}
    moves={r['vehicle_id']:r for r in jread(case,Path(run)/'departure-proposals.json')}
    assert len(moves)==(0 if run=='reference' else 467) and old.keys()==new.keys()==meta.keys() and len(new)==8640
    assert [i for i in old if i not in moves]==[i for i in new if i not in moves]
    assert [ET.tostring(v) for v in old_root if v.tag!='vehicle']==[ET.tostring(v) for v in root if v.tag!='vehicle']
    for identity,v in new.items():
        restored=deepcopy(v)
        restored.set('depart',old[identity].get('depart'))
        assert ET.tostring(restored)==ET.tostring(old[identity])
        if identity in moves:
            m=moves[identity]
            assert abs(float(v.get('depart'))-m['proposed_depart_s'])<1e-8 and abs(float(old[identity].get('depart'))-m['source_depart_s'])<1e-8
            assert 900000<=prior.prior.prior.native_ms(v.get('depart'))<1800000
            assert 900000<=prior.prior.prior.native_ms(old[identity].get('depart'))<1800000
            restored_meta=dict(meta[identity])
            assert abs(restored_meta.pop('depart_s')-float(v.get('depart')))<1e-8 and restored_meta==old_meta[identity]
        else:
            assert ET.tostring(v)==ET.tostring(old[identity]) and meta[identity]==old_meta[identity]
    for name in ('network.net.xml','control.add.xml','field-loops.add.xml','expected-counts.csv','initialization-counts.csv'):
        assert read(case,Path(run)/'prepared'/name)==source_read(Path('prepared')/name)
    if run=='reference':
        assert all(read(case,Path(run)/'prepared'/name)==source_read(Path('prepared')/name) for name in PREPARED)
    return {'full_future_requests':8640,'changed_second_bin_departures':len(moves),'native_bins_routes_stops_fields_fixed':True,'prior_first434_requests_unchanged':True,'future1800_plus7739_requests_unchanged':True}


def audit_prefix(case,run):
    record=jread(case,Path(run)/'record-result.json')
    p=jread(case,Path(run)/'preflight.json')
    assert record['status']=='pass' and all(record['checks'].values()) and all(record['reference_parity'].values())
    assert record['schema']=='astra.hamburg.causal-second-prefix/v1'
    assert record['preflight_sha256']==hashlib.sha256(read(case,Path(run)/'preflight.json')).hexdigest()
    assert p['runner_sha256']==hashlib.sha256(read(case,Path(run)/'executed-run.py')).hexdigest()
    assert p['prepared_hashes']==jread(case,Path(run)/'scope-proof.json')['prepared_hashes']
    for original,expected in record['native_output_hashes'].items():
        name=original.replace('\\','/').split('/')[-1]
        assert hashlib.sha256(read(case,Path(run)/'native'/name)).hexdigest()==expected
    for name,expected in p['implementation_sha256'].items():
        assert hashlib.sha256(read(case,Path(run)/'implementation'/name)).hexdigest()==expected
    native=prior.intervals(read(case,Path(run)/'native/field-counts.xml'))
    targets=rows(case,'study-source/prefix26.csv')
    assert len(native)==len(targets)==26 and set(native)=={prior.key(r) for r in targets}
    errors=[int(native[prior.key(r)]['nVehContrib'])-int(r['expected_total']) for r in targets]
    metrics={'rows':26,'TAE':sum(abs(v) for v in errors),'SSE':sum(v*v for v in errors),'MAE':sum(abs(v) for v in errors)/26,'max_abs_error':max(abs(v) for v in errors)}
    assert metrics==record['prefix26_metrics'] and metrics['TAE']==RUNS[run] and record['frozen_first13_TAE']==0
    coverage=jread(case,Path(run)/'all182-coverage.json')
    check_coverage(coverage,native,rows(case,Path(run)/'prepared/initialization-counts.csv')+rows(case,Path(run)/'prepared/expected-counts.csv'))
    assert record['measured_real_rows']==26 and record['unmeasured_real_rows']==156 and record['full_health_not_claimed']
    assert record['full130_metrics'] is None and record['remaining26_initial_metrics'] is None and record['combined182_metrics'] is None
    compact=json.loads(gzip.decompress(read(case,Path(run)/'contributing-passages.json.gz')))
    assert compact['reconciled'] and Counter((r['detector_id'],r['interval_begin_s'],r['interval_end_s']) for r in compact['passages'])==Counter({k:int(v['nVehContrib']) for k,v in native.items()})
    assert all(abs(r['notification_time_s']-1800)<1e-8 for r in compact['outside_periodic_intervals'])
    complete=compact['passages']+compact['outside_periodic_intervals']
    assignments={r['vehicle_id']:r['field_id'] for r in jread(case,Path(run)/'prepared/lane-intentions.json')['vehicles']}
    assert len({(r['vehicle_id'],r['detector_id']) for r in complete})==len(complete) and all(assignments[r['vehicle_id']]==r['detector_id'] for r in complete)
    assert hashlib.sha256(read(case,Path(run)/'native/instant-e1.xml')).hexdigest() in {r['sha256'] for r in compact['inputs']}
    steps=summary_prefix(read(case,Path(run)/'native/summary.xml'))
    assert len(steps)==18000 and float(steps[-1]['time'])==1799.9 and not any(int(steps[-1][k]) for k in ('collisions','teleports','discarded'))
    first={k:v for k,v in native.items() if k[1:]==(0,900)}
    source_first=prior.intervals(read(STAGE1,Path(prior.SELECTED)/'native/field-counts.xml'))
    assert first==source_first and summary_prefix(read(case,Path(run)/'native/summary.xml'),900)==prior.summary_prefix(read(STAGE1,Path(prior.SELECTED)/'native/summary.xml'))
    return {'validity_status':'pass','initial26':metrics,'observed_initial_rows':26,'future156_values':None,'main130_metrics':None,'full_health_not_claimed':True,'complete_events':len(compact['passages']),'first13_attributes_and_summary_fixed':True,'executed_runner_sha256':p['runner_sha256'],'input':audit_input(case,run)}


def audit_packet(case):
    results={name:audit_prefix(case,name) for name in RUNS}
    for folder,run in [('independent','advance-second900-120232'),('independent-126232','advance-second900-126232')]:
        j=jread(case,Path(folder)/'summary.json')
        assert j['audit_status']=='pass' and all(j['reference_strict_parity'].values())
        assert j['candidate']['metrics26']==results[run]['initial26'] and j['reference']['metrics26']==results['reference']['initial26']
        assert j['candidate_first900_interval_attributes_equal'] and j['candidate_first900_summary_equal'] and j['main130_metrics'] is None
    green=jread(case,'agent-backlog-boundary-evidence/evidence.json')
    assert not green['actual_candidate_TLS_recorded'] and green['declared_static119_link0_1_green']==[906,935]
    assert [r['predecessor24483192_front_exit_s'] for r in green['earliest_h1_fields']]==[935.3,936.1]
    return {'status':'review_required','formal_result':False,'native_runs_recorded':3,'native_runs_by_freeze':0,'prefixes':results,'stage2_full_not_run':True,'main130_unmeasured':True,'best_main_MAE_unchanged':3.7153846153846155,'global_extension_not_included':True}


def verify(case):
    verify_manifest(case)
    verify_manifest(STAGE1)
    assert sha(STAGE1/'manifest.json')==load(case/'source-references.json')['stage1_manifest_sha256']
    for folder,expected in SEALED.items():
        assert hashlib.sha256(read(case,Path(folder)/'manifest.json')).hexdigest()==expected
        for r in jread(case,Path(folder)/'manifest.json')['files']:
            data=read(case,Path(folder)/safe_relative(r['path']))
            assert len(data)==r.get('bytes',r.get('size_bytes')) and hashlib.sha256(data).hexdigest()==r['sha256']
    for r in load(case/'source-provenance.json')['snapshots']:
        data=read(case,r['relative'])
        assert len(data)==r['raw_bytes'] and hashlib.sha256(data).hexdigest()==r['raw_sha256']
    for r in load(case/'historical-source-bindings.json')['bindings']:
        data=read(case,r['target']) if r['scope']=='packet' else raw(REPO/safe_relative(r['target']))
        assert hashlib.sha256(data).hexdigest()==r['sha256']
    assert audit_packet(case)==load(case/'results.json')['audit']
    print('Three1800s prefixes,26initial rows/null156,first13parity and declared-green provenance: pass',flush=True)


def freeze():
    assert not (HERE/'manifest.json').exists() and not (HERE/'source-provenance.json').exists()
    selected=selected_files()
    locations={h:{'scope':'repository','target':p.relative_to(REPO).as_posix()} for h,p in reference_catalog().items()}
    aliases,provenance,local={},[],[]
    for name,source in sorted(selected.items()):
        digest=sha(source)
        if source.name=='queues.xml':
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
    save(HERE/'byte-references.json',{'aliases':aliases,'rule':'ExactSHA-boundoldsourceorduplicatesonly.'})
    bindings=[]
    for run in RUNS:
        for original,digest in load(SOURCE/run/'scope-proof.json')['source_hashes'].items():
            assert digest in locations,(original,digest)
            bindings.append({'declared_path':original,'sha256':digest,**locations[digest]})
    save(HERE/'historical-source-bindings.json',{'bindings':bindings,'source_hashes_not_rewritten':True})
    save(HERE/'local-only-large-traces.json',{'files':local,'all3prefix_instant_gzip_included':True})
    save(HERE/'source-references.json',{'stage1_commit':'dfa505665cb2faf9ae4945c5f420fe56f09c79e0','stage1_manifest_sha256':sha(STAGE1/'manifest.json'),'no_stage2_full':'No full validation of these stage2 inputs is recorded.','global_extension_included':False})
    result=audit_packet(HERE)
    save(HERE/'results.json',{'status':'review_required','formal_result':False,'audit':result})
    (HERE/'.gitattributes').write_text('* -text whitespace=blank-at-eol,blank-at-eof,space-before-tab,cr-at-eol\n*.gz binary\n',encoding='utf-8')
    (HERE/'README.md').write_text(README,encoding='utf-8')
    assert all(sha(Path(r['source']))==r['raw_sha256'] for r in provenance)
    save(HERE/'manifest.json',{'status':'review_required','formal_result':False,'files':[{'path':p.relative_to(HERE).as_posix(),'bytes':p.stat().st_size,'sha256':sha(p)} for p in sorted(HERE.rglob('*')) if p.is_file() and p!=HERE/'manifest.json' and '__pycache__' not in p.parts]})
    verify(HERE)


README='''# 前26初始化计数归零：三次1800秒原生对照

本包只包含3次0–1800秒原生运行，不含stage2 full或另一个global timing extension。reference首26 TAE30/SSE318，120232候选TAE3/SSE5，126232候选TAE0/SSE0；最后901个完整事件均为唯一预期字段贡献、零unsafe。零误差仅属于前26个初始化观测，剩余156行（26初始化+130主窗）显式null，主130未测、完整健康未证明。主窗最好3.7153846不变。

输入来源是固定dfa505 stage1包的434辆初始时刻候选。三次均保留全部8640未来请求和全部真实182观测。两候选只改第二900秒窗的467个depart：232个119 Z11/Z12/Z13请求由−120改为−126秒，其他235个均−90；实际requested bin和294数量不变，原第一窗434个请求不动，1800秒后7739个请求不动。全部ID、路线、stop、field、vType、网络和控制不变。18k summary与26个原生E1窗口保留，第一13 E1全属性及前9000条非CPU summary保持stage1实际前缀。

声明的119 link0/1绿窗906–935秒，与120232原生车辆前端退出前序路段的935.3/936.1秒证据一并保留。它们不是检测器进入时刻；后续内道行程和车尾离开仍需时间。该材料没有记录本候选实际TLS历史，也不是现场SPaT，不能把声明green解释为新采集的actual state。原证据中的“下一步未测”描述属于当时阶段，后来的126232结果另列。

短时core完整人口状态不适用于前缀。原19项检查与独立证据另存，未到达车辆和end记录不表示人工删车；未完成/未测保留，不把未来填0。较差的reference和120232结果不丢弃。所有来源与历史执行脚本按原字节gzip或固定stage1包SHA引用；queues仅localhash，3份短instant完整gzip保存。

离线核验：`.venv\\Scripts\\python.exe benchmarks/hamburg_ring1_causal_second_prefix_20261024_v1/reproduce.py --mode verify`，不运行SUMO。

显式原生prefix复现：同项目.venv及记录的SUMO/Libsumo1.27.1实现，运行`.venv\\Scripts\\python.exe benchmarks/hamburg_ring1_causal_second_prefix_20261024_v1/reproduce.py --mode native-prefix --case 126232 --output-dir outputs/reproduce-causal-second-prefix`。可选reference/120232/126232，只到1800秒。它从当前仓库的冻结显式输入调用core，不生成OD，不取历史outputs绝对路径输入。必须新目录；本次冻结未新增原生。离线核验不等于重新原生复现。
'''


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    mode=parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--freeze',action='store_true')
    mode.add_argument('--verify-only',action='store_true')
    args=parser.parse_args()
    freeze() if args.freeze else verify(HERE)
