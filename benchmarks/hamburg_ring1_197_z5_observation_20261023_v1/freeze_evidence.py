"""Freeze the sealed 54-ID observation and analyses; never run SUMO."""
import argparse
import gzip
import hashlib
from importlib.util import module_from_spec, spec_from_file_location
import json
from pathlib import Path
import xml.etree.ElementTree as ET


HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
SOURCE = REPO / 'outputs/hamburg-197-through-support-20261023-v1'
FAMILY = REPO / 'benchmarks/hamburg_ring1_197_through_support_20261023_v1'
OLD_OBSERVER = REPO / 'benchmarks/hamburg_ring1_multifield_delay_trace_20261023_v1'
spec = spec_from_file_location('family_evidence', FAMILY / 'freeze_evidence.py')
family = module_from_spec(spec)
spec.loader.exec_module(family)
raw, load, rows, sha, raw_sha = family.raw, family.load, family.rows, family.sha, family.raw_sha
verify_manifest = family.verify_manifest
spec = spec_from_file_location('observer_helpers', OLD_OBSERVER / 'freeze_evidence.py')
observer = module_from_spec(spec)
spec.loader.exec_module(observer)
elements, event_digest, xml_semantics = observer.elements, observer.event_digest, observer.xml_semantics
NATIVE = FAMILY / 'full/evaluation/native'
LARGE = {'cohort-fcd.xml', 'tls-switch-states.xml', 'tls-recorded-programs.add.xml', 'queues.xml'}
NATIVE_FILES = ('cohort-fcd.xml', 'tls-switch-states.xml', 'tls-recorded-programs.add.xml', 'queues.xml',
    'collisions.xml', 'demand.rou.xml', 'e1.add.xml', 'expected-counts.csv', 'field-counts.xml', 'instant-e1.xml', 'instant.add.xml',
    'lane-intentions.json', 'lanechanges.xml', 'native-request.json', 'network.net.xml', 'observed-vehicle-ids.json', 'preflight.json',
    'record-result.json', 'record_tls.snapshot.py', 'signal-programs.add.xml', 'source-e1.add.xml', 'stall-monitor.json', 'summary.xml',
    'sumo-errors.log', 'sumo.log', 'tls-observer.add.xml', 'tripinfo.xml', 'vehicle-routes.xml')


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')


def parity_gate(result):
    assert result['status'] == 'pass' and len(result['checks']) == 12 and all(result['checks'].values())
    assert [result[k] for k in ('closed_e1_rows', 'summary_steps_checked', 'instant_e1_events_checked', 'arrived_routes_checked')] == [208, 144000, 154367, 8606]


def audit_parity(trace, *, historical_aux=True):
    result, preflight = load(trace / 'record-result.json'), load(trace / 'preflight.json')
    parity_gate(result)
    assert raw_sha(trace / 'record_tls.snapshot.py') == preflight['script_sha256'] and raw_sha(trace / 'tls-observer.add.xml') == preflight['observer_sha256']
    for name, expected in preflight['source_hashes'].items():
        if historical_aux or name not in ('native-request.json', 'e1.add.xml', 'instant.add.xml'):
            assert raw_sha(NATIVE / name) == expected
        if name not in ('native-request.json', 'e1.add.xml', 'instant.add.xml'):
            assert raw_sha(trace / name) == expected
    actual_counts = [r.attrib.copy() for r in elements(trace / 'field-counts.xml', 'interval')]
    assert actual_counts == [r.attrib.copy() for r in elements(NATIVE / 'field-counts.xml', 'interval')] and len(actual_counts) == 208
    old, count = iter(elements(NATIVE / 'summary.xml', 'step')), 0
    for row in elements(trace / 'summary.xml', 'step'):
        reference = next(old)
        assert {k: v for k, v in row.attrib.items() if k != 'duration'} == {k: v for k, v in reference.attrib.items() if k != 'duration'}
        count += 1
    assert count == 144000 and next(old, None) is None
    def routes(path):
        return {r.get('id'): ET.canonicalize(ET.tostring(r, encoding='unicode'), strip_text=True) for r in elements(path, 'vehicle')}
    assert routes(trace / 'vehicle-routes.xml') == routes(NATIVE / 'vehicle-routes.xml')
    assert not ET.fromstring(raw(trace / 'collisions.xml')).findall('collision')
    monitor = load(trace / 'stall-monitor.json')
    assert monitor['status'] == 'completed' and monitor['simulation_time_s'] == 14400 and monitor['library_completed']
    return dict(status='pass', parity_checks=12, E1_rows=208, summary_steps=count, arrived_routes=8606, instant=event_digest(trace / 'instant-e1.xml'))


def audit_analysis(case):
    cohort, controls = rows(case / 'next-observation-cohort/cohort.csv'), rows(case / 'next-observation-cohort/source-bin-all32.csv')
    diagnosis = load(case / 'z5-observation/analysis/diagnosis.json')
    assert len(cohort) == len({r['vehicle_id'] for r in cohort}) == 54 and len(controls) == len({r['vehicle_id'] for r in controls}) == 32
    assert sum(r['candidate_terminal'] == 'True' for r in cohort) == 34
    assert sum(r['selected_for_observation'] == 'True' for r in controls) == 20
    assert sum(float(r['e1_delay_s']) == 0 for r in controls) == 12
    assert all(float(r['reference_e1_bin_begin_s']) == 6300 for r in controls)
    lost = [r for r in controls if float(r['candidate_e1_bin_begin_s']) != 6300]
    assert len(lost) == 8 and all(float(r['candidate_e1_bin_begin_s']) == 7200 for r in lost)
    terminal = rows(case / 'z5-observation/analysis/terminal34-last-observed.csv')
    assert len(terminal) == 34 and all(float(r['time_s']) == 14399 for r in terminal)
    stopped = [r for r in terminal if float(r['speed_m_s']) <= .1]
    assert len(stopped) == 5 and all(r['lane'] == '538341584_2' for r in stopped)
    assert diagnosis['observed_vehicles'] == 54 and diagnosis['observed_fcd_samples'] == 105297
    assert [diagnosis[k] for k in ('original_target_6300_7200', 'reference_measured_6300_7200', 'new_measured_6300_7200', 'preexisting_target_deficit', 'additional_cross_bin_loss')] == [59, 32, 24, 27, 8]
    assert diagnosis['terminal34_moving_at_last_sample'] == 29 and diagnosis['terminal34_stopped_at_last_sample'] == 5
    assert all(v['first_actual_phase_or_state_difference_s'] is None and v['differing_point1s_samples'] == 0 for v in diagnosis['actual119_and_GS266_signals_unchanged_all14400s'].values())
    return dict(cohort54=54, source_bin_controls32=32, delayed20=20, unchanged12=12, additional_cross_bin8=8,
        preexisting_target_deficit27=27, target59=59, reference32=32, candidate24=24,
        terminal34_moving=29, terminal34_stopped=5, all_five_stopped_lane='538341584_2', observed_FCD_samples=105297)


def source32_proof(case):
    reference = REPO / 'outputs/hamburg-classical-od-calibration-20261023-v1/selected-next-diagnosis/selected-contributing-passages.json.gz'
    basis = load(case / 'next-observation-cohort/basis.json')
    expected = next(v for k, v in basis['source_hashes'].items() if k.endswith('selected-contributing-passages.json.gz'))
    assert sha(reference) == expected
    before = json.loads(gzip.decompress(reference.read_bytes()))['passages']
    after = json.loads(gzip.decompress(raw(FAMILY / 'full/evaluation/contributing-passages.json.gz')))['passages']
    field = 'hh_0119_Z_5_30494'
    before_by_id = {r['vehicle_id']: r for r in before if r['detector_id'] == field and r['interval_begin_s'] == 6300}
    after_by_id = {r['vehicle_id']: r for r in after if r['detector_id'] == field}
    comparisons = rows(case / 'next-observation-cohort/source-bin-all32.csv')
    assert len(before_by_id) == len(comparisons) == 32 and set(before_by_id) == {r['vehicle_id'] for r in comparisons}
    for row in comparisons:
        a, b = before_by_id[row['vehicle_id']], after_by_id[row['vehicle_id']]
        assert a['notification_time_s'] == float(row['reference_e1_time_s']) and b['notification_time_s'] == float(row['candidate_e1_time_s'])
        assert b['interval_begin_s'] == float(row['candidate_e1_bin_begin_s'])
    assert sum(r['detector_id'] == field and r['interval_begin_s'] == 6300 for r in after) == 24
    return dict(status='pass', native_runs=0, reference_gzip_sha256=expected,
        reference_bin32=[before_by_id[r['vehicle_id']] for r in comparisons], candidate_corresponding32=[after_by_id[r['vehicle_id']] for r in comparisons])


def verify(case):
    verify_manifest(case)
    family.verify_manifest(FAMILY)
    result = load(case / 'results.json')
    assert sha(FAMILY / 'manifest.json') == result['family_manifest_sha256']
    assert sha(OLD_OBSERVER / 'freeze_evidence.py') == result['observer_helper_sha256']
    assert sha(OLD_OBSERVER / 'reproduce.py') == result['relocation_helper_sha256']
    assert audit_parity(case / 'z5-observation/baseline') == result['parity']
    assert audit_analysis(case) == result['analysis']
    for folder in ('z5-observation/analysis', 'next-observation-cohort'):
        family.check_sealed(case / folder)
    for row in load(case / 'source-provenance.json')['snapshots']:
        value = gzip.decompress((case / row['snapshot']).read_bytes())
        assert len(value) == row['raw_bytes'] and hashlib.sha256(value).hexdigest() == row['raw_sha256']
    local = load(case / 'local-only-large-traces.json')['files']
    trace_result = load(case / 'z5-observation/baseline/record-result.json')
    assert next(r['sha256'] for r in local if r['name'] == 'tls-switch-states.xml') == trace_result['state_log']['sha256']
    assert next(r['sha256'] for r in local if r['name'] == 'tls-recorded-programs.add.xml') == trace_result['program_export']['sha256']
    proof = load(case / 'large-trace-proof.json')
    assert proof['FCD_seen_ids'] == sorted(load(case / 'next-observation-cohort/basis.json')['vehicle_ids']) and proof['FCD_samples'] == 105297
    assert proof['native_terminal34_moving_stopped'] == [29, 5] and proof['old_selected119_GS_events_exact']
    assert proof['source_instant_semantics'] == proof['observer_instant_semantics'] == result['parity']['instant']
    full_local = load(FAMILY / 'local-only-large-traces.json')['files']
    assert proof['source_instant_sha256'] == next(r['sha256'] for r in full_local if r['scope'] == 'full' and family.prior.basename(r['path']) == 'instant-e1.xml')
    assert proof['observer_instant_sha256'] == raw_sha(case / 'z5-observation/baseline/instant-e1.xml')
    native32 = load(case / 'all32-native-event-proof.json')
    controls = rows(case / 'next-observation-cohort/source-bin-all32.csv')
    assert len(native32['reference_bin32']) == len(native32['candidate_corresponding32']) == len(controls) == 32
    for row, before, after in zip(controls, native32['reference_bin32'], native32['candidate_corresponding32'], strict=True):
        assert row['vehicle_id'] == before['vehicle_id'] == after['vehicle_id']
        assert float(row['reference_e1_time_s']) == before['notification_time_s'] and float(row['candidate_e1_time_s']) == after['notification_time_s']
    print('Manifest, 12 parity, cohort54/all32, additional8 versus original27, terminal29/5 and local-source bindings: pass', flush=True)


def freeze():
    assert not (HERE / 'manifest.json').exists(), 'Preserve a frozen or partial packet.'
    trace = SOURCE / 'z5-observation/baseline'
    parity = audit_parity(trace)
    analysis = audit_analysis(SOURCE)
    for folder in ('z5-observation/analysis', 'next-observation-cohort'):
        family.check_sealed(SOURCE / folder)
    source_instant = SOURCE / 'full/evaluation/native/instant-e1.xml'
    assert event_digest(source_instant) == parity['instant']
    samples, seen, final = 0, set(), {}
    for timestep in elements(trace / 'cohort-fcd.xml', 'timestep'):
        samples += len(timestep)
        seen.update(r.get('id') for r in timestep)
        if float(timestep.get('time')) == 14399:
            final = {r.get('id'): r.attrib.copy() for r in timestep}
    assert samples == 105297 and seen == set(load(SOURCE / 'next-observation-cohort/basis.json')['vehicle_ids'])
    assert len(final) == 34 and sum(float(r['speed']) > .1 for r in final.values()) == 29
    assert all(r['lane'] == '538341584_2' for r in final.values() if float(r['speed']) <= .1)
    for r in rows(SOURCE / 'z5-observation/analysis/terminal34-last-observed.csv'):
        assert final[r['vehicle_id']]['lane'] == r['lane'] and float(final[r['vehicle_id']]['pos']) == float(r['pos_m'])
    old_tls = REPO / 'benchmarks/hamburg_ring1_z5_upstream_delay_20261023_v1/trace/baseline/tls-switch-states.xml'
    def target_states(path):
        return [r.attrib.copy() for r in elements(path, 'tlsState') if r.get('id') in ('LSA119_part0', 'GS_266199063')]
    assert target_states(trace / 'tls-switch-states.xml') == target_states(old_tls)
    proof = dict(FCD_seen_ids=sorted(seen), FCD_samples=samples, source_instant_semantics=parity['instant'], observer_instant_semantics=parity['instant'],
        source_instant_sha256=sha(source_instant), observer_instant_sha256=sha(trace / 'instant-e1.xml'),
        native_terminal34_moving_stopped=[29, 5], old_selected119_GS_events_exact=True, old_selected_TLS_raw_sha256=raw_sha(old_tls),
        xml_semantics={name: xml_semantics(trace / name) for name in ('cohort-fcd.xml', 'tls-switch-states.xml', 'tls-recorded-programs.add.xml', 'lanechanges.xml')},
        source_inputs_unchanged=True, native_runs_by_freeze=0)
    provenance, local = [], []
    def copy(source, relative):
        value = source.read_bytes()
        target = HERE / (relative.as_posix()+'.gz')
        assert not target.exists(), 'Preserve partial evidence.'
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(gzip.compress(value, mtime=0))
        provenance.append(dict(source=str(source), snapshot=target.relative_to(HERE).as_posix(), raw_bytes=len(value), raw_sha256=hashlib.sha256(value).hexdigest()))
    for folder in ('z5-observation/analysis', 'next-observation-cohort'):
        manifest = load(SOURCE / folder / 'manifest.json')
        for name in [r['path'] for r in manifest['files']] + ['manifest.json']:
            copy(SOURCE / folder / name, Path(folder) / name)
    for name in ('record_z5.py', 'preparation.json', 'launch.json', 'stdout.log', 'stderr.log'):
        copy(SOURCE / 'z5-observation' / name, Path('z5-observation') / name)
    for name in NATIVE_FILES:
        p = trace / name
        if p.name in LARGE:
            local.append(dict(name=p.name, path=str(p), bytes=p.stat().st_size, sha256=sha(p)))
        else:
            copy(p, Path('z5-observation/baseline') / p.name)
    preflight = load(trace / 'preflight.json')
    for name, expected in preflight['driver_hashes'].items():
        p = REPO / 'plugins/astra-sumo/src/astra_sumo/core' / name
        assert sha(p) == expected
        copy(p, Path('code-source') / name)
    save(HERE / 'large-trace-proof.json', proof)
    save(HERE / 'all32-native-event-proof.json', source32_proof(SOURCE))
    save(HERE / 'local-only-large-traces.json', dict(files=local))
    save(HERE / 'source-provenance.json', dict(snapshots=provenance, later119_prefix_excluded=True))
    save(HERE / 'failure-inventory.json', dict(explicit_failure_files_in_sealed_source=[], stdout_stderr_preserved=True,
        no_failure_record_invented=True, later_in_progress_prefix_not_read=True))
    save(HERE / 'results.json', dict(status='review_required', formal_result=False, observation_runs=1, new_calibration_candidates=0,
        family_commit='13f59de', family_manifest_sha256=sha(FAMILY / 'manifest.json'),
        observer_helper_sha256=sha(OLD_OBSERVER / 'freeze_evidence.py'), relocation_helper_sha256=sha(OLD_OBSERVER / 'reproduce.py'), parity=parity, analysis=analysis))
    (HERE / '.gitattributes').write_text('* -text whitespace=blank-at-eol,blank-at-eof,space-before-tab,cr-at-eol\n*.gz binary\n', encoding='utf-8')
    (HERE / 'README.md').write_text('''# 197工作候选后的119 Z5只读观察

本包独立冻结已完成的54车观察和诊断，不含后续119接收段prefix。只有一次同输入观察回放，没有新增标定候选或拟合改善主张。原197包13f59de及其full输入保持不变；其MAE4.653846、SSE9473、最大误差35、34辆仍在途并未满足零误差和健康完成。

12项观察一致性检查全部通过：208行E1全属性、144000个summary步骤（排CPU duration）、154367个instant事件、8606条到达路线与full相同。54车=34辆终端车+全部20辆早窗延后车，105297个1秒FCD样本。32辆原6300–7200秒完整贡献的全量对照单独保留，没有只选恶化者：12辆时刻未变，20辆延后中12辆仍同窗、8辆转入7200–8100秒。

真实目标59、原selected32、v5为24：本次只解释新增32→24的8个跨窗，不能用它解释原59→32的27个差额。8辆完整事件延迟171.7–264.0秒。它们路线与实际出发未变，首次逐边退出时差在244#0或#1；相邻普通道路exitTimes差含路口，不是纯道路耗时。119与GS266为既有static控制，实际phase/state全14400秒逐0.1秒对照相同，不能归因于这两处实际切换历史变化。

14399秒末样本中34辆为29移动、5停车，5辆均在538341584_2；25辆未完整通过Z5，9辆已过点仍在下游。不能称为34辆死锁。原生leader受路线查询与200米范围限制，未记录ID的位置不能补造；绿灯及缺队列行也不证明下游无约束。尚缺队首foe/response、接收及冲突车道身份、pending stop等运行信息，当前不据此改路网或信号。

本包时间使用原生XML。若与步后TraCI记录对照，后者比原生FCD标时晚0.1秒；不混用两者。E1连续离开时刻与0.1秒网格的完整贡献通知时刻也分开保留。全部22份分析文件、54车名单、32全量对照、sourcehash、原脚本/请求/12parity/原生E1-summary-trips-routes/换道/警告日志按原字节压缩保存。当前批准来源中没有额外独立失败文件；stdout/stderr原样保留，不虚构失败；不纳入后续119接收段prefix。

大FCD、TLS states/导出程序及queues仅存local-only清单和冻结时计算的语义摘要；没有将它们从磁盘删除。完整instant已压缩保存并逐事件绑定原full，便于显式同输入复跑。完整大文件不随Git包分发，不应把语义摘要当逐样本原始数据。

离线核验：`.venv/Scripts/python.exe benchmarks/hamburg_ring1_197_z5_observation_20261023_v1/freeze_evidence.py --verify-only`。

只有显式运行reproduce.py并给新output-dir才重跑冻结observer。它复用既有monitor与原固定输入，仅重定位路径，不改OD、控制、驾驶模型或相位；复跑须核12parity及大输出语义摘要。冻结/自检不启动仿真。数据和源代码依赖同仓库已冻结197及既有observer包，哈希在results.json与清单绑定。
''', encoding='utf-8')
    assert all(sha(Path(r['source'])) == r['raw_sha256'] for r in provenance), 'A sealed source changed; preserve the failed freeze.'
    save(HERE / 'manifest.json', dict(formal_result=False, files=[dict(path=p.relative_to(HERE).as_posix(), bytes=p.stat().st_size, sha256=sha(p))
        for p in sorted(HERE.rglob('*')) if p.is_file() and p != HERE / 'manifest.json' and '__pycache__' not in p.parts]))
    verify(HERE)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument('--freeze', action='store_true')
    action.add_argument('--verify-only', action='store_true')
    args = parser.parse_args()
    freeze() if args.freeze else verify(HERE)


if __name__ == '__main__':
    main()
