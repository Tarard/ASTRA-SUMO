"""Freeze the completed fixed simulated-color pair; never start SUMO."""
import argparse
import gzip
import hashlib
from importlib.util import module_from_spec, spec_from_file_location
import io
import itertools
import json
from pathlib import Path
import tempfile
import xml.etree.ElementTree as ET

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
SOURCE = REPO/'outputs/hamburg-healthy-z4-fixed-history-20261024-v1'
GS = REPO/'benchmarks/hamburg_ring1_119_receiving_priority_20261023_v1'
FEEDBACK = REPO/'benchmarks/hamburg_ring1_healthy_z4_feedback_20261023_v1'
RECORD = REPO/'benchmarks/hamburg_ring1_healthy_z4_control_response_20261024_v1'
SOURCES = {'reference': GS/'full-priority', 'one-car': FEEDBACK/'batch-1'}
CONTROL_SHA = '74bff7513926a6b62a772439c058d47c318765791538e8bbc077702f70a39dc2'
spec = spec_from_file_location('GS_fixed_history_helpers', GS/'freeze_evidence.py')
helper = module_from_spec(spec)
spec.loader.exec_module(helper)
raw, load, sha, raw_sha = helper.raw, helper.load, helper.sha, helper.raw_sha
verify_manifest, audit_measurement = helper.verify_manifest, helper.audit_measurement
LOCAL = {'instant-e1.xml', 'queues.xml'}


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')


def safe_relative(name):
    path = Path(name)
    assert not path.anchor and '..' not in path.parts and name not in ('', '.')
    return path


def snapshot_relative(name):
    name = safe_relative(str(name))
    return Path('study-source')/name if len(name.parts) == 1 else name


def manifest_paths(folder):
    manifest = load(folder/'manifest.json')
    paths = []
    for row in manifest['files']:
        p = safe_relative(row['path'])
        if (folder/p).is_file():
            assert (folder/p).stat().st_size == row.get('bytes', row.get('size_bytes')) and sha(folder/p) == row['sha256']
        else:
            value = raw(folder/p)
            assert len(value) == row.get('bytes', row.get('size_bytes')) and hashlib.sha256(value).hexdigest() == row['sha256']
        paths.append(p)
    return [*paths, Path('manifest.json')]


def selected_paths():
    paths = set(manifest_paths(SOURCE))
    paths.add(Path('paired-result.json'))
    paths.update(Path('independent')/p for p in manifest_paths(SOURCE/'independent'))
    native_names = ('network.net.xml','demand.rou.xml','source-e1.add.xml','signal-programs.add.xml','lane-intentions.json','expected-counts.csv','e1.add.xml','instant.add.xml','field-counts.xml','field-comparison.csv','summary.xml','tripinfo.xml','vehicle-routes.xml','collisions.xml','run.json','native-request.json','command.json','stall-monitor.json','sumo.log','sumo-errors.log','tls-switch-states.xml','tls-observer.add.xml','instant-e1.xml','queues.xml')
    evaluation_names = ('demand.rou.xml','lane-intentions.json','evaluation.json','station-comparison.csv','initialization52-comparison.csv','all182-comparison.csv','535-all56-comparison.csv','contributing-passages.json.gz')
    for variant in SOURCES:
        for name in ('native-result.json','parity.json','observer-adapter.json','execution.json','execution.log','executed-run.py','failure.log'):
            if (SOURCE/variant/name).is_file():
                paths.add(Path(variant)/name)
        paths.update(Path(variant)/'evaluation'/name for name in evaluation_names)
        paths.update(Path(variant)/'evaluation/native'/name for name in native_names)
    return sorted(paths)


def elements(path, tag):
    for _, row in ET.iterparse(io.BytesIO(raw(path))):
        if row.tag == tag:
            yield row
            row.clear()


def keyed(path, tag):
    return {r.get('id'): ET.canonicalize(ET.tostring(r, encoding='unicode'), strip_text=True) for r in elements(path, tag)}


def history_gate(case, output):
    output.mkdir(parents=True, exist_ok=False)
    script = output/'compare_tls.py'
    script.write_bytes(raw(RECORD/'study-source/compare_tls.py'))
    spec = spec_from_file_location('saved_color_reader', script)
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    widths = {}
    for c in ET.fromstring(raw(GS/'full-priority/prepared/network.net.xml')).findall('connection'):
        if c.get('tl'):
            widths[c.get('tl')] = max(widths.get(c.get('tl'), 0), int(c.get('linkIndex'))+1)
    assert len(widths) == 50
    for variant in SOURCES:
        state, control = output/(variant+'-states.xml'), output/(variant+'-control.xml')
        state.write_bytes(raw(case/variant/'evaluation/native/tls-switch-states.xml'))
        control.write_bytes(raw(case/variant/'prepared/control.add.xml'))
        actual = module.read_states(state, widths)
        assert module.audit_program(control, widths, actual)['status'] == 'pass'
        assert raw_sha(control) == CONTROL_SHA
    return {'status': 'pass', 'TLS': 50, 'horizon_s': 14400, 'state_and_program_ID_exact': True, 'phase_ordinal_equality_required': False}


def audit_packet(case):
    pair = load(case/'study-source/paired-result.json') if case != SOURCE else load(SOURCE/'paired-result.json')
    assert pair['status'] == 'pass' and pair['reference_parity_passed'] and pair['conditional_comparison_interpretable']
    independent = load(case/'independent/summary.json')
    assert independent['schema'] == 'astra.hamburg.healthy-z4-fixed-history-independent/v1' and independent['audit_status'] == 'pass'
    results = {}
    for variant, original in SOURCES.items():
        folder, prepared = case/variant, case/variant/'prepared'
        p = load(folder/'preflight.json')
        assert not p['post_horizon_extrapolation'] and p['status'] == 'prepared'
        assert all(raw_sha(prepared/name) == value for name, value in p['prepared_hashes'].items())
        for name in ('network.net.xml','demand.rou.xml','lane-intentions.json','field-loops.add.xml','expected-counts.csv','initialization-counts.csv'):
            assert raw(prepared/name) == raw(original/'prepared'/name)
        assert raw_sha(prepared/'control.add.xml') == raw_sha(RECORD/'reference/tls-recorded-programs.add.xml') == CONTROL_SHA
        executed_source = SOURCE/'run.py' if case == SOURCE else case/'study-source/run.py'
        assert raw_sha(folder/'executed-run.py') == raw_sha(executed_source)
        signatures = {r['vehicle_id']: [r['field_id']] for r in load(prepared/'lane-intentions.json')['vehicles']}
        assert len(signatures) == 8640
        measurement, _, extra, repeats = audit_measurement(prepared, folder/'evaluation', signatures)
        parity = load(folder/'parity.json')
        assert parity == pair['results'][variant] and parity['status'] == 'pass' and all(parity['checks'].values())
        assert len(parity['checks']) == (14 if variant == 'reference' else 7)
        for a,b in [('main130','metrics130'),('initial52','metrics52'),('all182','metrics182')]:
            assert all(parity[b][k] == v for k,v in measurement[a].items())
            assert all(independent['results'][variant][b][k] == v for k,v in measurement[a].items())
        assert parity['evaluation'] == load(folder/'evaluation/evaluation.json') and parity['completion'] == measurement['completion']
        assert independent['results'][variant]['completion'] == measurement['completion']
        assert not extra and not repeats and measurement['healthy'] and measurement['completion']['arrived'] == 8640
        assert not parity['phase_index_equality_required'] and not parity['color_differences']
        results[variant] = {'measurement': measurement, 'required_checks': len(parity['checks']), 'raw_status': parity['status']}
    assert independent['all50_colour_and_program_timelines_equal_every_point1s'] and not independent['phase_ordinal_equality_required']
    assert independent['reference_strict_parity']['instant']['same'] and independent['reference_strict_parity']['instant']['rows'] == 143159
    assert independent['one_vehicle_departure_change']['request_millisecond_delta_s'] == '31.000'
    original = SOURCES['reference']/'evaluation/native'
    current = case/'reference/evaluation/native'
    def intervals(path):
        return [dict(r.attrib) for r in elements(path,'interval') if float(r.get('begin')) < float(r.get('end'))]
    assert intervals(current/'field-counts.xml') == intervals(original/'field-counts.xml')
    steps = 0
    for before, after in itertools.zip_longest(elements(original/'summary.xml','step'), elements(current/'summary.xml','step')):
        assert before is not None and after is not None
        assert {k:v for k,v in before.attrib.items() if k!='duration'} == {k:v for k,v in after.attrib.items() if k!='duration'}
        steps += 1
    assert steps == 144000
    for name,tag in [('tripinfo.xml','tripinfo'),('vehicle-routes.xml','vehicle')]:
        assert keyed(current/name,tag) == keyed(original/name,tag)
    assert load(current/'stall-monitor.json')['lane_section_hold'] == load(original/'stall-monitor.json')['lane_section_hold']
    with tempfile.TemporaryDirectory() as temporary:
        colors = history_gate(case, Path(temporary)/'history')
    return {'status':'review_required','formal_result':False,'reference_strict_output_parity':True,'history_gate':colors,'cases':results,
        'mixed_one_car_reference':load(FEEDBACK/'results.json')['audit']['cases']['batch-1'],
        'optimization_success':False,'claim_boundary':'Conditional simulated reference-color history, not field SPaT or successful optimization. One-car3.746 remains worse than reference3.715; improved completion relative to mixed source is conditional and does not identify exclusive signal causation.'}


def verify(case):
    verify_manifest(case)
    for source in (GS, FEEDBACK, RECORD):
        verify_manifest(source)
    source_meta = load(case/'source-references.json')
    for name,folder in [('GS',GS),('feedback',FEEDBACK),('record',RECORD)]:
        assert sha(folder/'manifest.json') == source_meta[name+'_manifest_sha256']
    for row in load(case/'source-provenance.json')['snapshots']:
        value = raw(case/row['relative'])
        assert len(value) == row['raw_bytes'] and hashlib.sha256(value).hexdigest() == row['raw_sha256']
    assert audit_packet(case) == load(case/'results.json')['audit']
    print('Fixed pair hashes,130/40/52/182/208, reference output parity,50color histories and independent audit: pass',flush=True)


def freeze():
    assert not (HERE/'manifest.json').exists() and not (HERE/'source-provenance.json').exists(), 'Preserve any frozen or partial packet.'
    selected = selected_paths()
    provenance, local = [], []
    for name in selected:
        source = SOURCE/name
        if source.name in LOCAL:
            local.append({'source_relative':name.as_posix(),'path':str(source),'bytes':source.stat().st_size,'sha256':sha(source)})
            continue
        relative = snapshot_relative(name)
        target = HERE/(relative.as_posix()+'.gz')
        target.parent.mkdir(parents=True,exist_ok=True)
        value = source.read_bytes()
        target.write_bytes(gzip.compress(value,mtime=0))
        provenance.append({'source':str(source),'relative':relative.as_posix(),'raw_bytes':len(value),'raw_sha256':hashlib.sha256(value).hexdigest()})
    save(HERE/'source-provenance.json',{'snapshots':provenance,'source_inputs_and_results_unchanged':True})
    save(HERE/'local-only-large-traces.json',{'files':local,'limitation':'Raw instant/queues require local files to re-read. Recorded reference14gate includes original strict instant parity; packaged compact full contributions remain available.'})
    audit = audit_packet(HERE)
    save(HERE/'results.json',{'status':'review_required','formal_result':False,'native_runs_by_freeze':0,'audit':audit})
    save(HERE/'source-references.json',{'GS_commit':'42a827e','GS_manifest_sha256':sha(GS/'manifest.json'),
        'feedback_manifest_sha256':sha(FEEDBACK/'manifest.json'),'record_commit':'03897adece909a0e94de3bd968823a874484467c',
        'record_manifest_sha256':sha(RECORD/'manifest.json'),'preparation_manifest_files':len(load(SOURCE/'manifest.json')['files']),
        'preparation_manifest_not_a_complete_native_inventory':True,'joint128_proposal_included':False})
    (HERE/'.gitattributes').write_text('* -text whitespace=blank-at-eol,blank-at-eof,space-before-tab,cr-at-eol\n*.gz binary\n',encoding='utf-8')
    (HERE/'README.md').write_text(README_TEXT,encoding='utf-8')
    assert all(sha(Path(r['source'])) == r['raw_sha256'] for r in provenance)
    save(HERE/'manifest.json',{'status':'review_required','formal_result':False,'files':[{'path':p.relative_to(HERE).as_posix(),'bytes':p.stat().st_size,'sha256':sha(p)} for p in sorted(HERE.rglob('*')) if p.is_file() and p!=HERE/'manifest.json' and '__pycache__' not in p.parts]})
    verify(HERE)


README_TEXT = '''# 固定健康源灯色历史的条件对照

本包只冻结两次已完成原生运行。共享的50条static历史来自健康源的模拟SaveTLSProgram，control SHA74bff751…，每条恰覆盖14400秒。两份新actual SwitchStates均与该完整state/program历史一致；不是现场SPaT，不按原始phase index作比较，也不在14400秒后重复外推。

| 条件 | MAE130 | SSE130 | TAE130 | TAE52 | TAE182 | 到达/等待/在途 |
|---|---:|---:|---:|---:|---:|---|
| reference历史自回放 | 3.715385 | 6709 | 483 | 105 | 588 | 8640/0/0 |
| 同一单车+31s，固定reference历史 | 3.746154 | 6929 | 487 | 105 | 592 | 8640/0/0 |
| 原mixed控制下同一单车改动（引用旧包） | 4.092308 | 8814 | 532 | 105 | 637 | 8634/0/6 |

reference14项严格输出复核通过：208行E1全属性、144000行summary（仅排CPU duration）、完整instant、tripinfo、arrived routes/stops/exitTimes、driver holds及期末状态；颜色/program逐区间一致。one-car7项完整性/安全/历史执行检查通过，不要求它匹配原mixed交通，也不把pass状态当优化接受。两份均零碰撞/传送/discard、535指定56项零残差。全部130/40/52/182及compact完整贡献保留。

两份原始8640需求、route/stops/vTypes/lane-intentions、网络、真实targets、探头、四bodyhold节点、Libsumo1.27.1/0.1s/seed104/空网起点保持各自来源，未重新生成OD。唯一控制处理为共同的模拟灯色历史，观察adapter只添加SwitchStates输出，不增加驾驶/TLS setter。counterpart比原mixed条件完成情况改善，只能作此历史下的条件对照；3.746仍高于健康3.715，不能称成功优化、真实OD或现场控制证明。独立逐车记录中，该车虽然实际出发延后31秒，Z4完整贡献通知仍为9899秒、同9000–9900窗，不能把请求时移当作观测平移。健康工作基准不变。

source-study的README/manifest为准备阶段快照，原样保存在study-source，不能把其中“未运行”文字当成当前状态。新原生、paired-result、原比较中的失败差异、独立审计及所有存在的failure记录均保存；大instant/queues仅local-only哈希，迁机后无原件就不能逐事件重读。未纳入后续128 jointOD提议/试验，也未改公共docs。

离线verify会重算计数、reference输出parity和50色史，不启动SUMO。reproduce.py只离线重算本包证据。原执行脚本/计划/请求均保留，若另行显式复跑原生需项目.venv及固定来源输入、原instant大文件；本次冻结没有新仿真。
'''


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    mode=parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--freeze',action='store_true')
    mode.add_argument('--verify-only',action='store_true')
    args=parser.parse_args()
    freeze() if args.freeze else verify(HERE)
