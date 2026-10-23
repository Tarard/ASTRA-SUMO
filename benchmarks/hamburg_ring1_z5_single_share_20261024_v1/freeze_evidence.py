"""Freeze the one completed Z5 field-share test; never start SUMO."""
import argparse
from collections import Counter
from copy import deepcopy
import gzip
import hashlib
from importlib.util import module_from_spec, spec_from_file_location
import json
from pathlib import Path
import tempfile
import xml.etree.ElementTree as ET

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
SOURCE = REPO/'outputs/hamburg-z5-single-share-20261024-v1'
SUPPORT = REPO/'outputs/hamburg-z5-single-share-support-20261024-v1'
BOUND = REPO/'outputs/hamburg-healthy-field-total-bound-20261024-v1'
FAILED = REPO/'outputs/hamburg-healthy-z4-z5-share-support-20261024-v1'
LINEAGE = REPO/'outputs/hamburg-z4-z5-stock-lineage-20261024-v1'
PREVIOUS = REPO/'benchmarks/hamburg_ring1_healthy_joint_od_stable_20261024_v1'
spec = spec_from_file_location('single_share_archive_helpers',PREVIOUS/'freeze_evidence.py')
prior = module_from_spec(spec)
spec.loader.exec_module(prior)
GS,BASELINE,CLASSIC = prior.GS,prior.BASELINE,prior.CLASSIC
raw,load,sha,raw_sha = prior.raw,prior.load,prior.sha,prior.raw_sha
safe_relative,manifest_paths,save = prior.safe_relative,prior.manifest_paths,prior.save
read,jread,snapshot_relative = prior.read,prior.jread,prior.snapshot_relative
verify_manifest,audit_measurement = prior.verify_manifest,prior.audit_measurement
DONOR,WITNESS = 'od_0209_v00026','od_0209_v00027'
Z4,Z5 = 'hh_0119_Z_4_29437','hh_0119_Z_5_30494'
SUPPORT_SHA = 'c5be6bc58bb642a230a606fe643b545d94da72d7ef81862d82dee057bb700353'
BOUND_SHA = 'd9d51057bb60b392c8aab78eef5acf1b8a8024f1c713a0c123f493aa401d8ef7'
FAILURE_SHA = '02ee083390f93df10088b0272f01bdea67b2371e9ec241b9d94761e6f6a6a84f'
LINEAGE_SHA = 'c43437bddc721affd16993ff5122709185e73e1440ec5b8d6a8dea4186ec69a4'
INDEPENDENT_MANIFEST_SHA = '4a39efb6e33424537283adf5c18763283be328a7a7816bdd9daf580146149580'
LOCAL = {'instant-e1.xml','queues.xml'}


def selected_files():
    assert INDEPENDENT_MANIFEST_SHA and sha(SOURCE/'independent/manifest.json') == INDEPENDENT_MANIFEST_SHA, 'Wait for the independently sealed terminal run.'
    assert (SOURCE/'one-share/verdict.json').is_file(), 'Never read active native files.'
    selected = {Path('independent')/p:SOURCE/'independent'/p for p in manifest_paths(SOURCE/'independent')}
    for prefix,folder,expected in [('field-total-bound',BOUND,BOUND_SHA),('helper-failure',FAILED,FAILURE_SHA),('stock-lineage',LINEAGE,LINEAGE_SHA)]:
        assert sha(folder/'manifest.json') == expected
        if prefix=='helper-failure':
            # This immutable historical manifest declares SHA only, not a byte count.
            entries=load(folder/'manifest.json')['files']
            assert len(entries)==1 and entries[0]['path']=='helper-attempt.json'
            assert sha(folder/'helper-attempt.json')==entries[0]['sha256']
            selected[Path(prefix)/'helper-attempt.json']=folder/'helper-attempt.json'
            selected[Path(prefix)/'manifest.json']=folder/'manifest.json'
        else:
            selected.update({Path(prefix)/p:folder/p for p in manifest_paths(folder)})
    for name in ('prepare.py','run_candidate.py','runner-reuse.json','check-only.json','one-share.execution.log'):
        selected[snapshot_relative(name)] = SOURCE/name
    for name in ('executed-driver.py','executed-prepare.py','generation-audit.json','od-vector.json','preflight.json','scope-proof.json','verdict.json','changed-vehicle-native.json','failure.log'):
        p = Path('one-share')/name
        if (SOURCE/p).is_file():
            selected[p] = SOURCE/p
    for folder,names in {
        'prepared': ('network.net.xml','control.add.xml','demand.rou.xml','lane-intentions.json','field-loops.add.xml','expected-counts.csv','initialization-counts.csv','scenario.json'),
        'evaluation': ('demand.rou.xml','lane-intentions.json','od-vector.json','evaluation.json','station-comparison.csv','initialization52-comparison.csv','all182-comparison.csv','535-all56.csv','native208-comparison.csv','contributing-passages.json.gz'),
        'evaluation/native': ('network.net.xml','demand.rou.xml','source-e1.add.xml','signal-programs.add.xml','lane-intentions.json','expected-counts.csv','e1.add.xml','instant.add.xml','field-counts.xml','field-comparison.csv','summary.xml','tripinfo.xml','vehicle-routes.xml','collisions.xml','run.json','native-request.json','command.json','stall-monitor.json','sumo.log','sumo-errors.log','instant-e1.xml','queues.xml'),
    }.items():
        for name in names:
            p = Path('one-share')/folder/name
            selected[p] = SOURCE/p
    for p in sorted((SOURCE/'one-share/implementation').glob('*.py')):
        selected[p.relative_to(SOURCE)] = p
    return selected


def reference_catalog():
    result = prior.prior.reference_catalog()
    for p in [BASELINE/'evaluation/native/stall-monitor.json',*(BASELINE/'implementation'/n for n in ('lane_section_hold.py','simulation_stall.py','detector_demand_replay.py'))]:
        result[raw_sha(p)] = p
    return result


def audit_scope(case):
    source = ET.fromstring(raw(BASELINE/'prepared/demand.rou.xml'))
    candidate = ET.fromstring(read(case,'one-share/prepared/demand.rou.xml'))
    old,new = [{v.get('id'):v for v in root.findall('vehicle')} for root in (source,candidate)]
    old_meta = load(BASELINE/'prepared/lane-intentions.json')
    new_meta = jread(case,'one-share/prepared/lane-intentions.json')
    assert len(old) == len(new) == 8640 and list(old) == list(new)
    assert [ET.tostring(v) for v in source if v.tag!='vehicle'] == [ET.tostring(v) for v in candidate if v.tag!='vehicle']
    for identity,before in old.items():
        after = deepcopy(new[identity])
        assert before.attrib == after.attrib
        if identity == DONOR:
            assert len(before.findall('stop')) == len(after.findall('stop')) == 1
            assert before.find('stop').get('lane') == '24483344#1_0' and after.find('stop').get('lane') == '24483344#1_1'
            after.find('stop').set('lane','24483344#1_0')
        assert ET.tostring(before) == ET.tostring(after)
    restored = deepcopy(new_meta)
    rows = {r['vehicle_id']:r for r in restored['vehicles']}
    assert rows[DONOR]['field_id'] == Z5
    rows[DONOR]['field_id'] = Z4
    assert restored == old_meta
    assert new[DONOR].find('route').attrib == old[WITNESS].find('route').attrib
    assert new[DONOR].find('stop').attrib == old[WITNESS].find('stop').attrib
    assert len(new[DONOR].find('route').get('edges').split()) == 19
    counts = Counter(r['od_cell'] for r in new_meta['vehicles'])
    assert counts == Counter(r['od_cell'] for r in old_meta['vehicles']) and set(counts)==set(range(294))
    vector = jread(case,'one-share/od-vector.json')
    assert vector['values'] == vector['integer_counts'] == [counts[i] for i in range(294)]
    totals = Counter(r['field_id'] for r in new_meta['vehicles'])
    assert totals[Z4] == 760 and totals[Z5] == 624
    for name in ('network.net.xml','control.add.xml','field-loops.add.xml','expected-counts.csv','initialization-counts.csv','scenario.json'):
        assert read(case,Path('one-share/prepared')/name) == raw(BASELINE/'prepared'/name)
    support = jread(case,'independent/source-support/source-proof.json')
    assert support['status']=='pass' and support['native_runs']==0
    assert support['donor_vehicle_id']==DONOR and support['witness_vehicle_id']==WITNESS
    assert support['route_edges']==new[DONOR].find('route').get('edges').split()
    assert len(support['legal_no_LC_lane_chain'])==37 and support['all_ordinary_and_internal_chain_lanes_allow_passenger']
    net = ET.fromstring(raw(BASELINE/'prepared/network.net.xml'))
    lanes = {r.get('id'):r for r in net.findall('edge/lane')}
    for lane,attributes in support['lane_permissions'].items():
        assert dict(lanes[lane].attrib)==attributes
        assert ('allow' not in attributes or 'passenger' in attributes['allow'].split()) and 'passenger' not in attributes.get('disallow','').split()
    return {'donor':DONOR,'witness':WITNESS,'only_stop_lane_and_field_changed':True,'unchanged_vehicles':8639,'all8640_IDs_departures_routes_order_fixed':True,'all294_q_fixed':True,'planned_Z4':760,'planned_Z5':624,'route_edges':19,'legal_lane_chain':37,'native_candidate_response_not_inferred_from_support':True}


def audit_packet(case):
    independent = jread(case,'independent/summary.json')
    assert independent['schema']=='astra.hamburg.single-z5-share-independent/v1' and independent['audit_status']=='pass'
    verdict = jread(case,'one-share/verdict.json')
    assert verdict['native_response_measured'] and not verdict['formal_result']
    assert verdict['status']==independent['selection'] and verdict['before']==independent['before'] and verdict['after']==independent['after']
    assert verdict['gates']==independent['gates'] and verdict['all53556']==independent['all53556']
    proof,preflight = jread(case,'one-share/scope-proof.json'),jread(case,'one-share/preflight.json')
    assert preflight['backend']=='libsumo' and set(preflight['versions'].values())=={'1.27.1'}
    assert proof['status']=='pass' and proof['actual_changes']==1
    assert hashlib.sha256(read(case,'one-share/scope-proof.json')).hexdigest()==preflight['source_proof_sha256']
    for name,expected in preflight['prepared_hashes'].items():
        assert hashlib.sha256(read(case,Path('one-share/prepared')/name)).hexdigest()==expected
    for name,expected in preflight['implementation_sha256'].items():
        assert hashlib.sha256(read(case,Path('one-share/implementation')/name)).hexdigest()==expected
    assert hashlib.sha256(read(case,'one-share/executed-driver.py')).hexdigest()==preflight['executed_driver_sha256']
    assert read(case,'one-share/executed-driver.py')==read(case,'study-source/run_candidate.py')
    assert read(case,'one-share/executed-prepare.py')==read(case,'study-source/prepare.py')
    signatures={r['vehicle_id']:[r['field_id']] for r in jread(case,'one-share/prepared/lane-intentions.json')['vehicles']}
    paths=[r['relative'] for r in load(case/'source-provenance.json')['snapshots']]+list(load(case/'byte-references.json')['aliases'])
    with tempfile.TemporaryDirectory() as temporary:
        temp=Path(temporary)
        for name in paths:
            if name.startswith(('one-share/prepared/','one-share/evaluation/')):
                p=temp/safe_relative(name)
                p.parent.mkdir(parents=True,exist_ok=True)
                p.write_bytes(read(case,name))
        measured,_,outside,repeated=audit_measurement(temp/'one-share/prepared',temp/'one-share/evaluation',signatures)
        fields535={'hh_0535_Z_5_30894','hh_0535_Z_6_33855','hh_0535_Z_7_29316','hh_0535_Z_8_34287'}
        selected535=[r for r in prior.prior.helper.rows(temp/'one-share/evaluation/all182-comparison.csv') if r['detector_id'] in fields535]
        assert len(selected535)==56
        tae535=sum(abs(int(r['measured_nVehContrib'])-int(r['expected_total'])) for r in selected535)
    assert not outside and not repeated
    for local,saved,size in [('main130','metrics130',130),('initial52','metrics52',52),('all182','metrics182',182)]:
        assert verdict['after'][saved]['rows']==size and all(verdict['after'][saved][k]==v for k,v in measured[local].items())
    assert verdict['after']['completion']==measured['completion'] and verdict['after']['station40_MAE']==measured['station40_MAE']
    assert verdict['after']['event_count']==measured['event_count']
    baseline=load(GS/'results.json')['audit']['full']
    for local,saved in [('main130','metrics130'),('initial52','metrics52'),('all182','metrics182')]:
        assert all(verdict['before'][saved][k]==v for k,v in baseline[local].items())
    assert verdict['before']['completion']==baseline['completion']
    compact=json.loads(gzip.decompress(read(case,'one-share/evaluation/contributing-passages.json.gz')))
    events=[r for r in compact['passages'] if r['vehicle_id']==DONOR]
    monitor=jread(case,'one-share/evaluation/native/stall-monitor.json')
    holds=[r for r in monitor['lane_section_hold']['events'] if r['vehicle_id']==DONOR]
    trip=next((dict(r.attrib) for r in ET.fromstring(read(case,'one-share/evaluation/native/tripinfo.xml')).findall('tripinfo') if r.get('id')==DONOR),{})
    saved=jread(case,'one-share/changed-vehicle-native.json')
    assert saved=={'vehicle_id':DONOR,'events':events,'tripinfo':trip,'lane_section_hold':holds}
    donor_gates={'changed_vehicle_exactly_one_Z5':len(events)==1 and events[0]['detector_id']==Z5,
        'changed_vehicle_arrived':float(trip.get('arrival',-1))>=0 and not trip.get('vaporized',''),
        'changed_vehicle_lane1_hold_and_release':[r['event'] for r in holds]==['held','released'] and holds[0].get('lane')=='24483344#1_1'}
    expected_gates={'safe':measured['safe'],'healthy':measured['healthy'],'all53556_zero':tae535==0,
        'no_repeated_vehicle_fields':not repeated,'no_contributions_outside_primary_intention':not outside,
        'main130_MAE_improves':measured['main130']['MAE']<verdict['before']['metrics130']['MAE'],
        'main130_SSE_not_worse':measured['main130']['SSE']<=verdict['before']['metrics130']['SSE'],
        'all182_TAE_not_worse':measured['all182']['TAE']<=verdict['before']['metrics182']['TAE'],**donor_gates}
    assert verdict['gates']==expected_gates and verdict['all53556']['TAE']==tae535
    assert len(verdict['gates'])==11 and verdict['status']==('diagnostic_candidate' if all(verdict['gates'].values()) else 'rejected')
    scope=audit_scope(case)
    def periodic(data):
        return {(r.get('id'),float(r.get('begin')),float(r.get('end'))):int(r.get('nVehContrib')) for r in ET.fromstring(data)}
    before_counts=periodic(raw(BASELINE/'evaluation/native/field-counts.xml'))
    after_counts=periodic(read(case,'one-share/evaluation/native/field-counts.xml'))
    def keys(rows):
        return [(r['detector_id'],float(r['begin']),float(r['end'])) for r in rows]
    initial_keys=keys(prior.prior.helper.rows(BASELINE/'prepared/initialization-counts.csv'))
    main_keys=keys(prior.prior.helper.rows(BASELINE/'prepared/expected-counts.csv'))
    assert len(initial_keys)==52 and all(before_counts[k]==after_counts[k] for k in initial_keys)
    z5_initial=sum(after_counts[k] for k in initial_keys if k[0]==Z5)
    z5_main=sum(after_counts[k] for k in main_keys if k[0]==Z5)
    z5_total=sum(n for k,n in after_counts.items() if k[0]==Z5)
    z4_total=sum(n for k,n in after_counts.items() if k[0]==Z4)
    assert (z4_total,z5_total,z5_initial,z5_main)==(760,624,102,450)
    quantity={'actual_Z4_total':z4_total,'actual_Z5_total':z5_total,'actual_Z5_initial':z5_initial,'actual_Z5_main':z5_main,'actual_Z5_tail':z5_total-z5_initial-z5_main,'main_ceiling_with_fixed_initial':scope['planned_Z5']-z5_initial,'main_target':522,'ceiling_condition_only':True}
    return {'status':'review_required','formal_result':False,'raw_verdict':verdict['status'],'native_runs_by_freeze':0,'before':verdict['before'],'full':measured,'all53556':verdict['all53556'],'gates':verdict['gates'],'donor_native':saved,'scope':scope,'quantity_condition':quantity,'claim_boundary':'One physical stop-lane and field intention variation. Removing a necessary quantity gap does not predict count improvement or identify field lane choice/control. Raw gate verdict is retained; promotion is separate.'}


def verify(case):
    verify_manifest(case)
    verify_manifest(GS)
    refs=load(case/'source-references.json')
    assert sha(GS/'manifest.json')==refs['healthy_manifest_sha256']
    for prefix,expected in [('independent',INDEPENDENT_MANIFEST_SHA),('independent/source-support',SUPPORT_SHA),('field-total-bound',BOUND_SHA),('helper-failure',FAILURE_SHA),('stock-lineage',LINEAGE_SHA)]:
        assert hashlib.sha256(read(case,Path(prefix)/'manifest.json')).hexdigest()==expected
        for row in jread(case,Path(prefix)/'manifest.json')['files']:
            value=read(case,Path(prefix)/safe_relative(row['path']))
            size=row.get('bytes',row.get('size_bytes'))
            assert (size is None and prefix=='helper-failure') or len(value)==size
            assert hashlib.sha256(value).hexdigest()==row['sha256']
    for row in load(case/'source-provenance.json')['snapshots']:
        value=read(case,row['relative'])
        assert len(value)==row['raw_bytes'] and hashlib.sha256(value).hexdigest()==row['raw_sha256']
    for row in load(case/'historical-source-bindings.json')['bindings']:
        value=read(case,row['target']) if row['scope']=='packet' else raw(REPO/safe_relative(row['target']))
        assert hashlib.sha256(value).hexdigest()==row['sha256']
    assert audit_packet(case)==load(case/'results.json')['audit']
    print('Single-share source/support hashes,1lane scope,donor native,130/40/52/182/208 and raw verdict: pass',flush=True)


def freeze():
    assert not (HERE/'manifest.json').exists(), 'Preserve frozen evidence.'
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
            encoded=gzip.compress(source.read_bytes(),mtime=0)
            if target.exists():
                assert target.read_bytes()==encoded, 'Never replace conflicting partial snapshots.'
            else:
                target.write_bytes(encoded)
            seen[digest]=relative
            locations[digest]={'scope':'packet','target':relative}
        provenance.append({'source':str(source),'relative':relative,'raw_bytes':source.stat().st_size,'raw_sha256':digest})
    source_provenance={'snapshots':provenance,'source_files_unchanged':True}
    if (HERE/'source-provenance.json').exists():
        assert load(HERE/'source-provenance.json')==source_provenance, 'Resume only the same exact source set.'
    save(HERE/'source-provenance.json',source_provenance)
    save(HERE/'byte-references.json',{'aliases':aliases,'rule':'Only byte-identical SHA-bound fixed inputs or duplicate snapshots are referenced.'})
    bindings=[]
    declared=load(SOURCE/'one-share/scope-proof.json')['source_hashes']
    declared.update(load(SUPPORT/'source-proof.json')['source_hashes'])
    for original,digest in declared.items():
        assert digest in locations,(original,digest)
        bindings.append({'declared_path':original,'sha256':digest,**locations[digest]})
    save(HERE/'historical-source-bindings.json',{'bindings':bindings,'original_hashes_unchanged':True})
    save(HERE/'local-only-large-traces.json',{'files':local,'limitation':'Original instant/queues remain local; full compact contributions and208 counts are portable.'})
    save(HERE/'source-references.json',{'healthy_commit':'42a827e','healthy_manifest_sha256':sha(GS/'manifest.json'),'independent_manifest_sha256':INDEPENDENT_MANIFEST_SHA,'support_manifest_sha256':SUPPORT_SHA,'bound_manifest_sha256':BOUND_SHA,'helper_failure_manifest_sha256':FAILURE_SHA,'stock_lineage_manifest_sha256':LINEAGE_SHA,'later_candidates_included':False})
    audit=audit_packet(HERE)
    save(HERE/'results.json',{'status':'review_required','formal_result':False,'audit':audit})
    (HERE/'.gitattributes').write_text('* -text whitespace=blank-at-eol,blank-at-eof,space-before-tab,cr-at-eol\n*.gz binary\n',encoding='utf-8')
    (HERE/'README.md').write_text(readme(audit),encoding='utf-8')
    assert all(sha(Path(r['source']))==r['raw_sha256'] for r in provenance)
    save(HERE/'manifest.json',{'status':'review_required','formal_result':False,'files':[{'path':p.relative_to(HERE).as_posix(),'bytes':p.stat().st_size,'sha256':sha(p)} for p in sorted(HERE.rglob('*')) if p.is_file() and p!=HERE/'manifest.json' and '__pycache__' not in p.parts]})
    verify(HERE)


def readme(audit):
    full=audit['full']
    donor=audit['donor_native']
    return f'''# 一辆既有合法lane模板的Z4→Z5份额对照

本包仅冻结one-share一次已完成原生运行。原始判定为 **{audit['raw_verdict']}**；独立audit的pass只表示证据对账通过，不是现场正确性或标定完成。原健康基准主MAE3.7153846，8640辆全部到达。

唯一输入变化为od_0209_v00026的moving-stop lane从24483344#1_0改成_1，并把本地field意向Z4改成Z5。全部8640 ID/depart/路线/294-cell q及车辆属性不变，donor保留原19-edge路线，其他8639辆XML/意向和输入顺序逐项相同，网络/控制/真实targets不变。planned Z4/Z5由761/623变为760/624；这只解除固定field总量/初始化子类的一个必要缺口，不预测MAE改善。原19-edge同属性witness v00027已有Z5原生事件；37-lane合法链只支持可达性，不等于新donor实际路径或现场lane份额。

新主130 MAE/SSE/TAE为 {full['main130']['MAE']} / {full['main130']['SSE']} / {full['main130']['TAE']}，初始化52 TAE {full['initial52']['TAE']}，全182 TAE {full['all182']['TAE']}，40派生站点MAE {full['station40_MAE']}。到达/等待/在途为 {full['completion']['arrived']}/{full['completion']['waiting']}/{full['completion']['running']}。全部208原生、130/40/52/182、健康、重复/额外字段、donor实际事件与lane1 hold/release均保存并核原11gates；原8gates未删除。真实计数与原生E1 nVehContrib比较，派生40不是独立观测。

donor只在Z5完整贡献一次，通知时刻{donor['events'][0]['notification_time_s']}秒；lane1 hold/release为{donor['lane_section_hold'][0]['time_s']}/{donor['lane_section_hold'][1]['time_s']}秒，到达{donor['tripinfo']['arrival']}秒。这三项实现检查通过，但主130和全182误差回退，不能把合法份额实现和健康运行写成拟合改善。最佳对照仍为3.7153846。

全时域实际Z4/Z5为760/624；Z5初始化102、主窗450、尾部72。固定初始化后的主窗数量上界624−102=522恰与目标总量522相同，但实际主窗仍未达到目标。数量上界可满足不等于逐bin拟合或真实份额已经确定。

source-support原字节及13字段必要bound小研究保留，基准大型输入/native按42a827e包和SHA引用。helper-failure仅记录无关全网_internal_paths失败；该失败发生在别的转向，不是本19-edge路线失败，也未启动仿真或改候选。原准备阶段“尚未测量”文字原样保存，不能当成终态。

stock-lineage另保留所核冻结链的先验来源：model初值及±6的planned Z4/Z5是760/624，改善原生误差的minus1通过quantile模板重采样变为761/623；该minus1需求/意向后来与健康基准原字节相同。后续锁定这份field先验及初始化计数才出现条件性数量缺口。这不是SUMO错误，不解释全部MAE，也不把当时minus1说成完成标定；时间边界只限所核冻结链。

候选仅改变既有物理stop-lane和对应本地hold意向，未新增控制setter或改权限。Libsumo1.27.1/0.1s/seed104/14400秒/四bodyhold与健康源保持。控制及lane-prior仍是条件模型，不是现场SPaT、真实OD或真实lane选择。移除必要数量缺口不证明130零误差可达或已达。

大instant/queues只留localhash，完整compact贡献保留；缺原大文件不能迁机逐事件重读。byte-references逐项声明去重。verify/reproduce只做离线检查，不新增SUMO。本次未纳入后续候选，未改公共docs或提交。
'''


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    mode=parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--freeze',action='store_true')
    mode.add_argument('--verify-only',action='store_true')
    args=parser.parse_args()
    freeze() if args.freeze else verify(HERE)
