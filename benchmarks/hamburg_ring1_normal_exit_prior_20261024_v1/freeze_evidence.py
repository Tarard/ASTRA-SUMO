"""Freeze the completed ordinary-exit sensitivity and its four-car support; no SUMO."""
import argparse
from collections import Counter
from copy import deepcopy
import gzip
import hashlib
from importlib.util import module_from_spec,spec_from_file_location
import json
from pathlib import Path
import tempfile
import xml.etree.ElementTree as ET

HERE=Path(__file__).resolve().parent
REPO=HERE.parents[1]
SOURCE=REPO/'outputs/hamburg-normal-exit-prior-20261024-v1'
PREVIOUS=REPO/'benchmarks/hamburg_ring1_one_share_field_stratified_feedback_20261024_v1'
spec=spec_from_file_location('normal_exit_archive_helpers',PREVIOUS/'freeze_evidence.py')
prior=module_from_spec(spec)
spec.loader.exec_module(prior)
ONE,GS=prior.ONE,prior.GS
raw,load,sha,raw_sha=prior.raw,prior.load,prior.sha,prior.raw_sha
read,jread,source_read=prior.read,prior.jread,prior.source_read
safe_relative,snapshot_relative,manifest_paths,save=prior.safe_relative,prior.snapshot_relative,prior.manifest_paths,prior.save
verify_manifest,audit_measurement=prior.verify_manifest,prior.audit_measurement
RUNS=('smoke','full')
LOCAL={'instant-e1.xml','queues.xml'}
PREPARE_SHA='dcdca8bb2c5ea71dc3893300fbb285e5e8ae226b577c327613d4d1d4892a8a72'
INDEPENDENT_MANIFEST_SHA='270c9ff4637710b58686d0964b7a6705afdf086a6270a9ea6ef35844398dc32c'
STATIC={'port-audit':('hamburg-route-port-boundary-audit-20261024-v1','04f6fa72e2743b401615fe06959049b1bf8476ae1af602c11e822645041a285b'),
    'parking-lineage':('hamburg-parking-exit-prior-lineage-20261024-v1','36d29ece667c57e1fb7d67aa8aa3e00ae34a1a8f4044736cef0e85fbecf7d022')}
ALTERNATIVE_SHA='16ff81e30eeee182e5ef14fa5c6d5b39c13a5992459b53e60890263dd120b48b'


def selected_files():
    assert INDEPENDENT_MANIFEST_SHA and sha(SOURCE/'independent/manifest.json')==INDEPENDENT_MANIFEST_SHA
    assert all((SOURCE/r/'verdict.json').is_file() for r in RUNS), 'Wait for terminal outputs.'
    assert sha(SOURCE/'prepare-manifest.json')==PREPARE_SHA
    selected={Path('independent')/p:SOURCE/'independent'/p for p in manifest_paths(SOURCE/'independent')}
    for row in load(SOURCE/'prepare-manifest.json')['files']:
        name=safe_relative(row['path'])
        assert sha(SOURCE/name)==row['sha256'] and (SOURCE/name).stat().st_size==row.get('bytes',row.get('size_bytes'))
        selected[snapshot_relative(name)]=SOURCE/name
    for prefix,(folder,expected) in STATIC.items():
        root=REPO/'outputs'/folder
        assert sha(root/'manifest.json')==expected
        selected.update({Path(prefix)/p:root/p for p in manifest_paths(root)})
    alternative=REPO/'outputs/hamburg-119-exit-prior-support-20261024-v1'
    assert sha(alternative/'manifest.json')==ALTERNATIVE_SHA
    declaration={r['path']:r for r in load(alternative/'manifest.json')['files']}
    for name in ('README.md','summary.json','manifest.json'):
        if name!='manifest.json':
            assert sha(alternative/name)==declaration[name]['sha256']
        selected[Path('119-alternative-limit')/name]=alternative/name
    for name in ('prepare-manifest.json','prepare.py','README.md','runner-reuse.json','run_candidate.py','run_candidate.pre-scope-adapter.py.txt','preflight-attempt-1.json','full.check-only.json','smoke.check-only.json','selfchecks.json','smoke.execution.log','full.execution.log'):
        selected[snapshot_relative(name)]=SOURCE/name
    for folder in ('preparation-notes','smoke/preflight-attempt-2'):
        selected.update({p.relative_to(SOURCE):p for p in (SOURCE/folder).rglob('*') if p.is_file() and '__pycache__' not in p.parts})
    for run in RUNS:
        for name in ('executed-driver.py','preflight.json','scope-proof.json','od-vector.json','cells294.json','route-changes.csv','metadata-index-changes.csv','od-coordinate-map.json','smoke-plan.json','verdict.json','changed-route-native.json','failure.log'):
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
        selected.update({p.relative_to(SOURCE):p for p in (SOURCE/run/'implementation').glob('*.py')})
    return selected


def reference_catalog():
    result=prior.reference_catalog()
    p=ONE/'one-share/evaluation/native/native-request.json'
    result[raw_sha(p)]=p
    # Existing gzip files are also immutable inputs of the preparation manifest.
    for folder in (ONE,GS):
        for p in folder.rglob('*.gz'):
            result.setdefault(sha(p),p)
    return result


def audit_scope(case,run):
    data=read(case,Path(run)/'prepared/demand.rou.xml')
    root=ET.fromstring(data)
    source=ET.fromstring(source_read('prepared/demand.rou.xml'))
    old={v.get('id'):v for v in source.findall('vehicle')}
    new={v.get('id'):v for v in root.findall('vehicle')}
    original_meta={r['vehicle_id']:r for r in json.loads(source_read('prepared/lane-intentions.json'))['vehicles']}
    metadata={r['vehicle_id']:r for r in jread(case,Path(run)/'prepared/lane-intentions.json')['vehicles']}
    assert new.keys()==metadata.keys() and new.keys()<=old.keys()
    assert len(new)==(8640 if run=='full' else 4)
    assert [i for i in old if i in new]==list(new)
    changed,reindexed,reindexed_unchanged,initial=0,0,0,0
    cells=jread(case,Path(run)/'cells294.json')['cells']
    indices={(c['origin'],c['destination'],int(c['begin_s'])):c['index'] for c in cells}
    assert len(indices)==294
    counts=Counter()
    for identity,v in new.items():
        before=old[identity]
        assert before.attrib==v.attrib
        previous=before.find('route').get('edges').split()
        current=v.find('route').get('edges').split()
        restored=deepcopy(v)
        if previous[-1]=='28665128#0':
            changed+=1
            initial+=prior.native_ms(v.get('depart'))<3600000
            assert current==previous[:-1]+['186821028','186821026','142944318'] and len(current)==len(set(current))
            restored.find('route').set('edges',before.find('route').get('edges'))
        else:
            assert current==previous
        assert ET.tostring(restored)==ET.tostring(before)
        column=indices[current[0],current[-1],prior.native_ms(v.get('depart'))//900000*900]
        assert metadata[identity]['od_cell']==column
        counts[column]+=1
        restored_meta=dict(metadata[identity])
        different=restored_meta['od_cell']!=original_meta[identity]['od_cell']
        reindexed+=different
        reindexed_unchanged+=different and current==previous
        restored_meta['od_cell']=original_meta[identity]['od_cell']
        assert restored_meta==original_meta[identity]
    vector=jread(case,Path(run)/'od-vector.json')
    assert vector['values']==[counts[i] for i in range(294)] and sum(vector['values'])==len(new)
    if run=='full':
        assert changed==882 and reindexed==332 and reindexed_unchanged==81 and initial==172
        assert data.replace(b'186821028 186821026 142944318',b'28665128#0')==source_read('prepared/demand.rou.xml')
        assert [ET.tostring(r) for r in root if r.tag!='vehicle']==[ET.tostring(r) for r in source if r.tag!='vehicle']
        assert Counter(r['field_id'] for r in metadata.values())==Counter(r['field_id'] for r in original_meta.values())
    else:
        assert changed==4 and all(prior.native_ms(v.get('depart'))>=3600000 for v in new.values())
    for name in ('network.net.xml','control.add.xml','field-loops.add.xml','expected-counts.csv','initialization-counts.csv'):
        assert read(case,Path(run)/'prepared'/name)==source_read(Path('prepared')/name)
    return {'N':len(new),'route_tail_changes':changed,'actual_destination_changes':changed,'metadata_reindexed':reindexed,'unchanged_route_metadata_reindexed':reindexed_unchanged,'initial_route_changes_without_retiming':initial,'IDs_departures_stops_fields_fixed':True,'old_q_not_claimed_identical':True,'new_exit_is_existing_paired_graph_cut_not_field_trip_end':True}


def audit_packet(case):
    independent=jread(case,'independent/summary.json')
    assert independent['schema']=='astra.hamburg.normal-exit-prior-independent/v1' and independent['audit_status']=='pass'
    results={}
    for run in RUNS:
        verdict=jread(case,Path(run)/'verdict.json')
        endpoint=jread(case,Path('independent')/run/'summary.json')
        assert endpoint['schema']=='astra.hamburg.normal-exit-endpoint-independent/v1' and endpoint['audit_status']=='pass'
        assert endpoint['case']==run and endpoint['selection']==verdict['status'] and endpoint['calibration_selection_applicable']==verdict['calibration_selection_applicable']
        assert endpoint['reference']==verdict['before'] and endpoint['after']==verdict['after'] and endpoint['global_best']==verdict['global_best']
        assert endpoint['search_reference_gates']==verdict['gates'] and endpoint['global_best_gates']==verdict['global_best_gates']
        assert endpoint['route_support_gates']==verdict['route_support_gates']
        assert verdict['native_response_measured'] and not verdict['formal_result']
        p=jread(case,Path(run)/'preflight.json')
        assert p['backend']=='libsumo' and set(p['versions'].values())=={'1.27.1'}
        for name,expected in p['prepared_hashes'].items():
            assert hashlib.sha256(read(case,Path(run)/'prepared'/name)).hexdigest()==expected
        for name,expected in p['implementation_sha256'].items():
            assert hashlib.sha256(read(case,Path(run)/'implementation'/name)).hexdigest()==expected
        assert hashlib.sha256(read(case,Path(run)/'scope-proof.json')).hexdigest()==p['source_proof_sha256']
        assert read(case,Path(run)/'executed-driver.py')==read(case,'study-source/run_candidate.py')
        assert hashlib.sha256(read(case,Path(run)/'executed-driver.py')).hexdigest()==p['executed_driver_sha256']
        assert p['search_reference_verdict_sha256']==hashlib.sha256(source_read('verdict.json')).hexdigest()
        assert p['global_best_verdict_sha256']==raw_sha(GS/'full-priority/verdict.json')
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
            rows=prior.prior.prior.prior.helper.rows(temp/run/'evaluation/all182-comparison.csv')
            fields535={'hh_0535_Z_5_30894','hh_0535_Z_6_33855','hh_0535_Z_7_29316','hh_0535_Z_8_34287'}
            selected=[r for r in rows if r['detector_id'] in fields535]
            assert len(selected)==56
            tae535=sum(abs(int(r['measured_nVehContrib'])-int(r['expected_total'])) for r in selected)
        assert not outside and not repeated
        for local,saved,size in [('main130','metrics130',130),('initial52','metrics52',52),('all182','metrics182',182)]:
            assert verdict['after'][saved]['rows']==size and all(verdict['after'][saved][k]==v for k,v in measured[local].items())
        assert measured['completion']==verdict['after']['completion'] and measured['station40_MAE']==verdict['after']['station40_MAE']
        assert verdict['before']==json.loads(source_read('verdict.json'))['after']
        common={'safe':measured['safe'],'healthy':measured['healthy'],'all53556_zero':tae535==0,'no_repeated_vehicle_fields':not repeated,'no_contributions_outside_primary_intention':not outside}
        def gates(reference):
            return {**common,'main130_MAE_improves':measured['main130']['MAE']<reference['metrics130']['MAE'],'main130_SSE_not_worse':measured['main130']['SSE']<=reference['metrics130']['SSE'],'all182_TAE_not_worse':measured['all182']['TAE']<=reference['metrics182']['TAE']}
        assert verdict['gates']==gates(verdict['before']) and verdict['global_best_gates']==gates(verdict['global_best'])
        assert verdict['all53556']['TAE']==tae535
        changed=jread(case,Path(run)/'changed-route-native.json')
        assert changed['route_support_gates']==verdict['route_support_gates']
        compact=json.loads(gzip.decompress(read(case,Path(run)/'evaluation/contributing-passages.json.gz')))
        events={identity:[e for e in compact['passages'] if e['vehicle_id']==identity] for identity in changed['passages']}
        affected={identity for identity,fields in signatures.items() if fields[0] in {'hh_0535_Z_4_33055','hh_0535_Z_9_29038','hh_0535_Z_14_31892'}}
        assert set(events)==affected and len(events)==changed['actual_changed_vehicles']==(4 if run=='smoke' else 882)
        trips={r.get('id'):dict(r.attrib) for r in ET.fromstring(read(case,Path(run)/'evaluation/native/tripinfo.xml')).findall('tripinfo')}
        assert events==changed['passages'] and {i:trips.get(i,{}) for i in events}==changed['arrivals']
        support={'safe':measured['safe'],'healthy':measured['healthy'],'one_expected_field_per_changed_vehicle':all(len(e)==1 and e[0]['detector_id']==signatures[i][0] for i,e in events.items()),'all_changed_vehicles_arrive_at_declared_road_cut':all(float(trips.get(i,{}).get('arrival',-1))>=0 and trips[i].get('arrivalLane','').startswith('142944318_') and not trips[i].get('vaporized','') for i in events),'no_extra_or_repeated_fields':not outside and not repeated}
        assert support==verdict['route_support_gates']
        if run=='smoke':
            assert not verdict['calibration_selection_applicable'] and verdict['status']==('route_support_pass' if all(support.values()) else 'route_support_failed')
        else:
            assert verdict['calibration_selection_applicable']
            expected='working_best_improved_candidate' if all(verdict['global_best_gates'].values()) else 'search_reference_improved_candidate' if all(verdict['gates'].values()) else 'rejected'
            assert verdict['status']==(expected if all(support.values()) else 'rejected')
        results[run]={'raw_verdict':verdict['status'],'calibration_selection_applicable':verdict['calibration_selection_applicable'],'full':measured,'search_reference_gates':verdict['gates'],'global_best_gates':verdict['global_best_gates'],'route_support_gates':support,'scope':audit_scope(case,run)}
    return {'status':'review_required','formal_result':False,'native_runs_by_freeze':0,'source_reference_MAE':3.9384615384615387,'global_best_comparison_MAE':3.7153846153846155,'cases':results,'claim_boundary':'100percent through-destination sensitivity for882 trips, not observed OD/parking activity. Existing142 cut is not a real road-end claim.332 metadata indices change; no q equality claim. Smoke fit is not applicable. Full count and health evidence are separate from source truth.'}


def verify(case):
    verify_manifest(case)
    verify_manifest(ONE)
    verify_manifest(GS)
    refs=load(case/'source-references.json')
    assert sha(ONE/'manifest.json')==refs['source_manifest_sha256']
    assert sha(GS/'manifest.json')==refs['global_best_manifest_sha256']
    assert hashlib.sha256(read(case,'independent/manifest.json')).hexdigest()==INDEPENDENT_MANIFEST_SHA
    assert hashlib.sha256(read(case,'study-source/prepare-manifest.json')).hexdigest()==PREPARE_SHA
    assert hashlib.sha256(read(case,'119-alternative-limit/manifest.json')).hexdigest()==ALTERNATIVE_SHA
    for row in jread(case,'119-alternative-limit/manifest.json')['files']:
        if row['path'] in ('README.md','summary.json'):
            value=read(case,Path('119-alternative-limit')/row['path'])
            assert len(value)==row['bytes'] and hashlib.sha256(value).hexdigest()==row['sha256']
    for prefix,manifest in [('independent',jread(case,'independent/manifest.json')),('',jread(case,'study-source/prepare-manifest.json'))]:
        for row in manifest['files']:
            name=Path(prefix)/row['path'] if prefix else snapshot_relative(row['path'])
            value=read(case,name)
            assert len(value)==row.get('bytes',row.get('size_bytes')) and hashlib.sha256(value).hexdigest()==row['sha256']
    for prefix,(_,expected) in STATIC.items():
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
    print('Normal-exit hashes,882tails/332metadata,smoke support/full130/40/52/182/208 and scope: pass',flush=True)


def freeze():
    assert not (HERE/'manifest.json').exists() and not (HERE/'source-provenance.json').exists()
    selected=selected_files()
    catalog,aliases,seen,provenance,local=reference_catalog(),{},{},[],[]
    locations={h:{'scope':'repository','target':p.relative_to(REPO).as_posix()} for h,p in catalog.items()}
    for name,source in sorted(selected.items()):
        digest=sha(source)
        if source.name in LOCAL:
            local.append({'path':str(source),'bytes':source.stat().st_size,'sha256':digest})
            continue
        relative=name.as_posix()
        if digest in locations:
            aliases[relative]={**locations[digest],'sha256':digest}
        else:
            target=HERE/(relative+'.gz')
            target.parent.mkdir(parents=True,exist_ok=True)
            target.write_bytes(gzip.compress(source.read_bytes(),mtime=0))
            seen[digest]=relative
            locations[digest]={'scope':'packet','target':relative}
        provenance.append({'source':str(source),'relative':relative,'raw_bytes':source.stat().st_size,'raw_sha256':digest})
    save(HERE/'source-provenance.json',{'snapshots':provenance,'source_files_unchanged':True})
    save(HERE/'byte-references.json',{'aliases':aliases,'rule':'Only exact SHA-bound inputs or duplicate snapshots are referenced.'})
    bindings=[]
    for run in RUNS:
        for original,digest in load(SOURCE/run/'scope-proof.json')['source_hashes'].items():
            assert digest in locations,(original,digest)
            bindings.append({'declared_path':original,'sha256':digest,**locations[digest]})
    save(HERE/'historical-source-bindings.json',{'bindings':bindings,'original_hashes_unchanged':True})
    save(HERE/'local-only-large-traces.json',{'files':local,'limitation':'Raw instant/queues remain local; portable compact contributions are retained.'})
    save(HERE/'source-references.json',{'source_commit':'a889d72','source_manifest_sha256':sha(ONE/'manifest.json'),'global_best_commit':'42a827e','global_best_manifest_sha256':sha(GS/'manifest.json'),'preparation_manifest_sha256':PREPARE_SHA,'preparation49_is_not_full_native_inventory':True,'independent_manifest_sha256':INDEPENDENT_MANIFEST_SHA,'119_alternative_manifest_sha256':ALTERNATIVE_SHA,'119_alternative_captured_subset':['README.md','summary.json','manifest.json'],'119_alternative_full_lane_paths_not_copied':True,'later_candidates_included':False})
    result=audit_packet(HERE)
    save(HERE/'results.json',{'status':'review_required','formal_result':False,'audit':result})
    (HERE/'.gitattributes').write_text('* -text whitespace=blank-at-eol,blank-at-eof,space-before-tab,cr-at-eol\n*.gz binary\n',encoding='utf-8')
    (HERE/'README.md').write_text(readme(result),encoding='utf-8')
    assert all(sha(Path(r['source']))==r['raw_sha256'] for r in provenance)
    save(HERE/'manifest.json',{'status':'review_required','formal_result':False,'files':[{'path':p.relative_to(HERE).as_posix(),'bytes':p.stat().st_size,'sha256':sha(p)} for p in sorted(HERE.rglob('*')) if p.is_file() and p!=HERE/'manifest.json' and '__pycache__' not in p.parts]})
    verify(HERE)


def readme(result):
    full=result['cases']['full']
    m=full['full']
    return f'''# 882辆普通道路出口先验敏感性

本包只保存一份4车路线支持smoke与一次8640辆完整对照。full原判 **{full['raw_verdict']}**。主130 MAE/SSE/TAE为{m['main130']['MAE']}/{m['main130']['SSE']}/{m['main130']['TAE']}，52TAE{m['initial52']['TAE']}、182TAE{m['all182']['TAE']}、40派生MAE{m['station40_MAE']}，到达/等待/在途{m['completion']['arrived']}/{m['completion']['waiting']}/{m['completion']['running']}。完整130/40/52/182/208、健康、两套reference gates、全部882实际字段/出口到达和独立审计均保留。audit通过不等于现场先验正确或130零误差完成。

两辆期末仍在途的记录不称死锁或滞留：它们晚插入、已完成各自字段贡献并释放detector hold，未见active hold。期末halting/stopped均0、meanSpeed5.2是aggregate记录，不给出逐车精确速度。arrival=-1/vaporized=end表示SUMO在固定结束时刻输出未完成记录，不是人工删车；没有FCD就不推断其精确lane/TLS状态。

健康one-share源为3.9384615，全局对照最好仍3.7153846。仅把原882 parking-ending请求（535Z4=495、Z9=313、Z14=74）的最终28665128#0换成既有186821028→186821026→142944318，其余7758车辆XML不变；全体ID/depart/stops/fields/网络/控制/真实targets不变。逆向字符串替换恢复原需求字节。原生初始请求不改时刻，但172条初始化路线尾段也改变，不能宣称初始化交通不受影响。

142944318是现存paired road graph-cut，原源路仍续桥；不是现场真实trip终点或road-end证明。100%through是缺停车活动输入时的一项敏感性，真实OD/share未知。4prefix静态既有自然最短通路无repeat/extra、增加37.739348m，来源port/parking-lineage原字节保存；不以支持或拟合推断真实份额。OD destination实际改882辆，294坐标重排使332个metadata索引变化，其中251同时改route、81未改route仅重排。od-coordinate-map保留两套坐标，绝不称q不变或重新生成OD。

119替代出口审查只保留README、summary及原manifest作为限制引用，未复制大型lane-paths，也未选择其候选；不能把这份局部引用称为完整替代出口证据包。

smoke的4个既有主窗ID用原出发时刻，全部13字段和130主窗+52初始化真实targets原样保留。它只检验预期字段、142lane2到达与安全；calibration_selection_applicable=false。其计数误差没有标定意义，不合成四车假观测或当成拟合失败/成功。full计数则与全部真实观测独立重测。

preparation49文件manifest仅覆盖准备阶段。preparation-notes及两次native前reader适配失败（driver_scope字典、actual_changes键）保留原脚本/日志/partial-copy；它们native=0，prepared输入字节未改，不算原生失败。固定source和重复原件按SHA引用，原始路径只供溯源；大instant/queues localhash，完整compact贡献保留。verify/reproduce均离线，无新增SUMO，未写后续结果。
'''


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    mode=parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--freeze',action='store_true')
    mode.add_argument('--verify-only',action='store_true')
    args=parser.parse_args()
    freeze() if args.freeze else verify(HERE)
