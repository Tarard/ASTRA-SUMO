"""Freeze the two sealed passive TLS records; no native run is started."""
import argparse
from collections import Counter
import contextlib
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
SOURCE = REPO / 'outputs/hamburg-healthy-z4-control-response-20261023-v1'
GS = REPO / 'benchmarks/hamburg_ring1_119_receiving_priority_20261023_v1'
FEEDBACK = REPO / 'benchmarks/hamburg_ring1_healthy_z4_feedback_20261023_v1'
SOURCES = {'reference': GS / 'full-priority', 'one-car': FEEDBACK / 'batch-1'}
INVENTORY_SHA = '659cdd60de16bc70716cb2c1ed79f770a62015c2077fff688ef340b2d50c7217'
INPUTS = {'network.net.xml', 'demand.rou.xml', 'lane-intentions.json', 'source-e1.add.xml', 'expected-counts.csv', 'signal-programs.add.xml'}
LOCAL = {'instant-e1.xml', 'queues.xml'}
MOVEMENT_SOURCE = REPO / 'outputs/hamburg-healthy-z4-movement-control-difference-20261024-v1'
MOVEMENT_SHA = 'c13335079170c88bb83ce2cd62175ed87ab3fa19dc01f8852091ea81af195537'
spec = spec_from_file_location('GS_freeze_helpers', GS / 'freeze_evidence.py')
prior = module_from_spec(spec)
spec.loader.exec_module(prior)
raw, load, sha, raw_sha = prior.raw, prior.load, prior.sha, prior.raw_sha
verify_manifest = prior.verify_manifest


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')


def safe_relative(name):
    path = Path(name)
    assert not path.anchor and '..' not in path.parts and name not in ('', '.')
    return path


def snapshot_relative(name):
    name = safe_relative(str(name))
    return Path('study-source') / name if len(name.parts) == 1 else name


def manifest_paths(directory):
    manifest = load(directory / 'manifest.json')
    paths = []
    for row in manifest['files']:
        name = safe_relative(row['path'])
        path = directory/name
        if path.is_file():
            assert path.stat().st_size == row.get('bytes', row.get('size_bytes')) and sha(path) == row['sha256']
        else:
            value = raw(path)
            assert len(value) == row.get('bytes', row.get('size_bytes')) and hashlib.sha256(value).hexdigest() == row['sha256']
        paths.append(name)
    return [*paths, Path('manifest.json')]


def elements(path, tag):
    for _, row in ET.iterparse(io.BytesIO(raw(path))):
        if row.tag == tag:
            yield row
            row.clear()


def original(variant, name):
    return SOURCES[variant] / 'evaluation/native' / name


def source_parity(case):
    reports = {}
    for variant in SOURCES:
        folder = case/variant
        record = load(folder/'record-result.json')
        preflight = load(folder/'preflight.json')
        assert record['status'] == 'pass' and len(record['checks']) == 20 and all(record['checks'].values())
        assert record['summary_steps_checked'] == 144000 and record['closed_e1_rows'] == 208
        assert Counter(record['tls_types'].values()) == {'actuated': 40, 'static': 10}
        for name in INPUTS:
            assert raw_sha(original(variant, name)) == record['source_hashes'][name]
        for name, expected in preflight['source_hashes'].items():
            assert raw_sha(original(variant, name)) == expected
        assert raw_sha(folder/'record_tls.snapshot.py') == preflight['script_sha256']
        assert raw_sha(folder/'tls-observer.add.xml') == preflight['observer_sha256']
        assert raw_sha(folder/'tls-switch-states.xml') == record['state_log']['sha256']
        assert raw_sha(folder/'tls-recorded-programs.add.xml') == record['program_export']['sha256']
        def intervals(path):
            rows = [dict(r.attrib) for r in elements(path, 'interval') if float(r.get('begin')) < float(r.get('end'))]
            result = {(r['id'], r['begin'], r['end']): r for r in rows}
            assert len(rows) == len(result) == 208
            return result
        assert intervals(folder/'field-counts.xml') == intervals(original(variant, 'field-counts.xml'))
        count, last = 0, None
        for before, after in itertools.zip_longest(elements(original(variant, 'summary.xml'), 'step'), elements(folder/'summary.xml', 'step')):
            assert before is not None and after is not None
            assert {k: v for k, v in before.attrib.items() if k != 'duration'} == {k: v for k, v in after.attrib.items() if k != 'duration'}
            count += 1
            last = dict(after.attrib)
        assert count == 144000 and last == record['completion']
        for name, tag in [('vehicle-routes.xml', 'vehicle'), ('tripinfo.xml', 'tripinfo')]:
            def records(path):
                return {r.get('id'): ET.canonicalize(ET.tostring(r, encoding='unicode'), strip_text=True) for r in elements(path, tag)}
            assert records(folder/name) == records(original(variant, name))
        assert not ET.fromstring(raw(folder/'collisions.xml')).findall('collision')
        monitor = load(folder/'stall-monitor.json')
        source_monitor = load(original(variant, 'stall-monitor.json'))
        assert monitor['status'] == 'completed' and monitor['simulation_time_s'] == 14400
        assert monitor.get('lane_section_hold') == source_monitor.get('lane_section_hold')
        reports[variant] = {'checks': 20, 'E1': 208, 'summary_steps': 144000, 'instant_events': record['instant_e1_events_checked'],
            'arrived': record['arrived_routes_checked'], 'completion': {k: int(last[k]) for k in ('loaded', 'inserted', 'arrived', 'waiting', 'running', 'collisions', 'teleports', 'discarded')}}
    assert reports['reference']['arrived'] == 8640 and reports['one-car']['arrived'] == 8634
    return reports


def recompute(case, output):
    output.mkdir(parents=True, exist_ok=False)
    for variant in SOURCES:
        folder = output/variant
        folder.mkdir()
        for name in ('record-result.json', 'tls-switch-states.xml', 'tls-recorded-programs.add.xml'):
            (folder/name).write_bytes(raw(case/variant/name))
    (output/'reference/network.net.xml').write_bytes(raw(original('reference', 'network.net.xml')))
    script = output/'compare_tls.py'
    script.write_bytes(raw(case/'study-source/compare_tls.py'))
    spec = spec_from_file_location('fixed_TLS_comparison', script)
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    reused = output/'reused-compare-tls.py'
    reused.write_bytes(raw(case/'code-source/reused-compare-tls.py'))
    module.REUSED = reused
    with contextlib.redirect_stdout(io.StringIO()):
        module.main()
    measured = load(output/'comparison.json')
    expected = load(case/'study-source/comparison.json')
    def normalized_hashes(values):
        return {'/'.join(k.replace('\\', '/').split('/')[-2:]): v for k, v in values.items()}
    assert normalized_hashes(measured.pop('source_hashes')) == normalized_hashes(expected.pop('source_hashes'))
    assert measured == expected
    for name in ('per-tls.csv', 'per-tls-900s-bins.csv', 'different-color-intervals.csv', 'common-emit-phase-differences.csv'):
        assert (output/name).read_bytes() == raw(case/'study-source'/name)
    assert measured['first_actual_color_difference']['begin_s'] == 8943.0
    assert measured['first_actual_color_difference']['tls_id'] == '18653534'
    assert measured['different_tls_count'] == 18 and measured['all10_static_color_program_histories_equal']
    assert not measured['any_color_difference_before8247_6']
    prefix = load(FEEDBACK/'independent/batch-1/prefix-consistency.json')
    traffic = float(prefix['summary']['first_traffic_excluding_duration_and_loaded']['native_time_s'])
    assert traffic == 8247.6
    return {'first_full_vector_colour_s': 8943.0, 'first_traffic_s': traffic, 'colour_later_s': round(8943.0-traffic, 1), 'different_actuated_TLS': 18,
        'static_TLS_equal': 10, 'all50_programs_equal': all(r['different_program_ticks'] == 0 for r in measured['per_tls']),
        'phase': 'Emitted events only; no forward filling. First common-emit phase difference8951.0 is not a full-history first phase difference.'}


def recompute_movement(case, output):
    output.mkdir(parents=True, exist_ok=False)
    record = output/'record'
    for variant in SOURCES:
        folder = record/variant
        folder.mkdir(parents=True)
        for name in ('record-result.json', 'tls-switch-states.xml'):
            (folder/name).write_bytes(raw(case/variant/name))
    for name in ('network.net.xml', 'signal-programs.add.xml'):
        (record/'reference'/name).write_bytes(raw(original('reference', name)))
    (record/'compare_tls.py').write_bytes(raw(case/'study-source/compare_tls.py'))
    (record/'control-inventory').mkdir()
    (record/'control-inventory/tls-inventory.csv').write_bytes(raw(case/'control-inventory/tls-inventory.csv'))
    expected = load(case/'movement-control-difference/results.json')
    core_names = {'local_network_conversion.py', 'osm_access.py', 'connection_mode_audit.py'}
    for name in core_names:
        expected_hash = next(h for p, h in expected['source_hashes'].items() if p.replace('\\', '/').endswith('/'+name))
        assert sha(REPO/'plugins/astra-sumo/src/astra_sumo/core'/name) == raw_sha(case/'movement-control-difference/code-source'/name) == expected_hash
    script = output/'analyze.py'
    script.write_bytes(raw(case/'movement-control-difference/analyze.py'))
    spec = spec_from_file_location('fixed_movement_classification', script)
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    module.ROOT, module.REPO, module.RECORD, module.NET = output, REPO, record, record/'reference/network.net.xml'
    with contextlib.redirect_stdout(io.StringIO()):
        module.main()
    measured = load(output/'results.json')
    def normalized_hashes(values):
        result = {}
        for path, value in values.items():
            parts = path.replace('\\', '/').split('/')
            key = '/'.join(parts[-2:]) if parts[-2] in {'reference', 'one-car', 'control-inventory'} else parts[-1]
            assert key not in result
            result[key] = value
        return result
    assert normalized_hashes(measured.pop('source_hashes')) == normalized_hashes(expected.pop('source_hashes'))
    assert measured == expected
    for name in ('controlled-bits.csv', 'controlled-connections.csv', 'difference-intervals.csv', 'per-tls.csv', 'per-tls-900s.csv'):
        assert (output/name).read_bytes() == raw(case/'movement-control-difference'/name)
    full = measured['first_all_color_difference']
    passenger, motor = measured['first_passenger_difference'], measured['first_motor_difference']
    assert full['begin_s'] == 8943.0 and full['changed_indices'] == [22, 24] and not full['passenger_indices'] and not full['motor_indices']
    assert passenger['begin_s'] == motor['begin_s'] == 8948.0 and passenger['tls_id'] == '18653534'
    assert passenger['selected_indices'] == [1, 2, 4, 12, 13, 15, 16]
    assert len(measured['passenger_different_controllers']) == 17
    assert measured['controlled_bit_count'] == 431 and measured['categories']['unknown'] == 59
    assert measured['unbound_or_unresolved_bit_count'] == 58 and measured['taxonomy_unknown_but_passenger_proven_count'] == 1
    return {'full_vector_first_s': 8943.0, 'full_vector_first_changed_indices': [22, 24], 'full_vector_first_role': 'ped-only',
        'first_proven_passenger_s': 8948.0, 'first_proven_motor_s': 8948.0, 'first_TLS': '18653534',
        'first_passenger_indices': passenger['selected_indices'], 'passenger_differing_controllers': 17,
        'passenger_later_than_traffic_s': 700.4, 'taxonomy_unknown': 59, 'unbound_or_unresolved_blank_availability': 58,
        'taxonomy_unknown_but_passenger_true': 1, 'claim_boundary': 'Loaded model class permissions, not observed vehicle passage or field SPaT. Unknown availability is not false; phase remains emit-only.'}


def append_movement(case):
    assert sha(MOVEMENT_SOURCE/'manifest.json') == MOVEMENT_SHA
    provenance = load(case/'source-provenance.json')
    for name in manifest_paths(MOVEMENT_SOURCE):
        source = MOVEMENT_SOURCE/name
        relative = Path('movement-control-difference')/name
        value = source.read_bytes()
        snapshot = case/(relative.as_posix()+'.gz')
        assert not snapshot.exists()
        snapshot.parent.mkdir(parents=True, exist_ok=True)
        snapshot.write_bytes(gzip.compress(value, mtime=0))
        provenance['snapshots'].append({'source': str(source), 'relative': relative.as_posix(), 'raw_bytes': len(value), 'raw_sha256': hashlib.sha256(value).hexdigest()})
    data = load(MOVEMENT_SOURCE/'results.json')
    for name in ('local_network_conversion.py', 'osm_access.py', 'connection_mode_audit.py'):
        source = REPO/'plugins/astra-sumo/src/astra_sumo/core'/name
        expected = next(h for p, h in data['source_hashes'].items() if p.replace('\\', '/').endswith('/'+name))
        assert sha(source) == expected
        relative = Path('movement-control-difference/code-source')/name
        snapshot = case/(relative.as_posix()+'.gz')
        snapshot.parent.mkdir(parents=True, exist_ok=True)
        value = source.read_bytes()
        snapshot.write_bytes(gzip.compress(value, mtime=0))
        provenance['snapshots'].append({'source': str(source), 'relative': relative.as_posix(), 'raw_bytes': len(value), 'raw_sha256': expected})
    save(case/'source-provenance.json', provenance)
    seals = load(case/'source-manifests.json')
    seals.update(additional_movement_manifest_sha256=MOVEMENT_SHA, additional_movement_analysis_not_in_original74=True)
    save(case/'source-manifests.json', seals)


def verify(case):
    verify_manifest(case)
    prior.verify_manifest(GS)
    prior.verify_manifest(FEEDBACK)
    sources = load(case/'source-references.json')
    assert sha(GS/'manifest.json') == sources['GS_manifest_sha256'] and sha(FEEDBACK/'manifest.json') == sources['feedback_manifest_sha256']
    for row in load(case/'source-provenance.json')['snapshots']:
        value = raw(case/row['relative'])
        assert len(value) == row['raw_bytes'] and hashlib.sha256(value).hexdigest() == row['raw_sha256']
    seal = load(case/'source-manifests.json')
    stage = load(case/'study-source/manifest.json')
    assert len(stage['files']) == seal['original_file_count'] == 74
    assert not any(r['path'] == 'control-inventory/manifest.json' for r in stage['files'])
    assert raw_sha(case/'study-source/manifest.json') == seal['original_manifest_sha256']
    assert raw_sha(case/'control-inventory/manifest.json') == seal['additional_inventory_manifest_sha256'] == INVENTORY_SHA
    stage_hashes = {r['path']: r['sha256'] for r in stage['files']}
    for row in load(case/'external-input-references.json')['files']:
        assert raw_sha(REPO/row['reference']) == row['sha256'] == stage_hashes[row['source_relative']]
    excluded = {r['source_relative']: r for r in load(case/'external-input-references.json')['files']}
    local = {r['source_relative']: r for r in load(case/'local-only-large-traces.json')['files']}
    for row in stage['files']:
        if row['path'] in excluded:
            assert excluded[row['path']]['sha256'] == row['sha256']
        elif row['path'] in local:
            assert local[row['path']]['sha256'] == row['sha256'] and local[row['path']]['bytes'] == row['bytes']
        else:
            value = raw(case/snapshot_relative(row['path']))
            assert len(value) == row['bytes'] and hashlib.sha256(value).hexdigest() == row['sha256']
    manifest_paths(case/'control-inventory')
    assert raw_sha(case/'movement-control-difference/manifest.json') == seal['additional_movement_manifest_sha256'] == MOVEMENT_SHA
    assert len(manifest_paths(case/'movement-control-difference')) == 9
    result = load(case/'results.json')
    assert source_parity(case) == result['parity']
    with tempfile.TemporaryDirectory() as temporary:
        assert recompute(case, Path(temporary)/'comparison') == result['comparison']
        assert recompute_movement(case, Path(temporary)/'movement') == result['movement_analysis']
    print('Frozen hashes, original74+separate inventory seal, both20checks, raw50TLS exports/states, exact full-vector and passenger-bit comparisons: pass', flush=True)


def freeze():
    assert not (HERE/'manifest.json').exists() and not (HERE/'source-provenance.json').exists(), 'Preserve existing frozen or draft evidence.'
    stage = load(SOURCE/'manifest.json')
    assert len(stage['files']) == 74
    assert not any(r['path'] == 'control-inventory/manifest.json' for r in stage['files'])
    assert sha(SOURCE/'control-inventory/manifest.json') == INVENTORY_SHA
    paths = manifest_paths(SOURCE)+[Path('control-inventory/manifest.json')]
    provenance, local, references = [], [], []
    for name in paths:
        p = SOURCE/name
        relative = snapshot_relative(name)
        if name.parts[0] in SOURCES and p.name in LOCAL:
            local.append({'source_relative': name.as_posix(), 'path': str(p), 'bytes': p.stat().st_size, 'sha256': sha(p)})
            continue
        value = p.read_bytes()
        if name.parts[0] in SOURCES and p.name in INPUTS:
            reference = original(name.parts[0], p.name)
            assert hashlib.sha256(value).hexdigest() == raw_sha(reference)
            references.append({'source_relative': name.as_posix(), 'reference': reference.relative_to(REPO).as_posix(), 'sha256': hashlib.sha256(value).hexdigest()})
            continue
        target = HERE/(relative.as_posix()+'.gz')
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(gzip.compress(value, mtime=0))
        provenance.append({'source': str(p), 'relative': relative.as_posix(), 'raw_bytes': len(value), 'raw_sha256': hashlib.sha256(value).hexdigest()})
    ancestor = REPO/'outputs/hamburg-z5-native-feedback-20261023-v1/tls-feedback-diagnosis/compare_tls.py'
    assert sha(ancestor) == load(SOURCE/'comparison.json')['reused_comparison_source_sha256']
    value = ancestor.read_bytes()
    target = HERE/'code-source/reused-compare-tls.py.gz'
    target.parent.mkdir()
    target.write_bytes(gzip.compress(value, mtime=0))
    provenance.append({'source': str(ancestor), 'relative': 'code-source/reused-compare-tls.py', 'raw_bytes': len(value), 'raw_sha256': hashlib.sha256(value).hexdigest()})
    save(HERE/'source-provenance.json', {'snapshots': provenance, 'source_files_unchanged': True})
    save(HERE/'external-input-references.json', {'files': references, 'source_inputs_not_duplicated': True})
    save(HERE/'local-only-large-traces.json', {'files': local, 'portable_limitation': 'Omitted queues/instant cannot be independently re-read on another machine without these local files. Original20check records and hashes are retained.'})
    save(HERE/'source-manifests.json', {'original_manifest_sha256': sha(SOURCE/'manifest.json'), 'original_file_count': 74,
        'inventory_four_files_already_in_original74': True, 'additional_inventory_manifest_sha256': INVENTORY_SHA,
        'additional_inventory_manifest_was_not_in_original74': True, 'comparison_ancestor_snapshot_is_additional': True})
    save(HERE/'source-references.json', {'GS_commit': '42a827e', 'GS_manifest_sha256': sha(GS/'manifest.json'),
        'feedback_commit': 'f0934b3;current checkout cae62ad', 'feedback_manifest_sha256': sha(FEEDBACK/'manifest.json'),
        'source_study_name_preserved': 'hamburg-healthy-z4-control-response-20261023-v1', 'fixed_history_study_included': False})
    append_movement(HERE)
    parity = source_parity(HERE)
    with tempfile.TemporaryDirectory() as temporary:
        comparison = recompute(HERE, Path(temporary)/'comparison')
        movement = recompute_movement(HERE, Path(temporary)/'movement')
    save(HERE/'results.json', {'status': 'review_required', 'formal_result': False, 'observation_runs': 2, 'native_runs_by_freeze': 0,
        'new_demand_or_control_candidates': 0, 'parity': parity, 'comparison': comparison, 'movement_analysis': movement})
    (HERE/'.gitattributes').write_text('* -text whitespace=blank-at-eol,blank-at-eof,space-before-tab,cr-at-eol\n*.gz binary\n', encoding='utf-8')
    (HERE/'README.md').write_text(README_TEXT, encoding='utf-8')
    assert all(sha(Path(r['source'])) == r['raw_sha256'] for r in provenance)
    save(HERE/'manifest.json', {'formal_result': False, 'files': [{'path': p.relative_to(HERE).as_posix(), 'bytes': p.stat().st_size, 'sha256': sha(p)} for p in sorted(HERE.rglob('*')) if p.is_file() and p != HERE/'manifest.json' and '__pycache__' not in p.parts]})
    verify(HERE)


README_TEXT = '''# 健康Z4基准与单车候选的被动TLS记录

本包按当地24日命名；源study的23日目录名及原始字节不改。只有两次原样只读record，没有新需求、冻结控制或健康候选提升。reference仍是健康3.7153846基准；one-car仍是被拒绝的4.0923077候选，8634辆到达、6辆在途。没有把记录通过当作候选接受或标定完成。

两份均20/20 checks通过：各208行E1全属性、144000步summary（只排CPU duration）、全部instant事件、到达完整route/exitTimes、tripinfo、driver hold及期末状态与原运行一致。两份50个实际programme和SwitchStates原始XML均gzip保存，每个programme恰覆盖14400秒并与switch颜色时间线对应。原来源输入和旧完整native固定引用42a827e的GS包及f0934b3/cae62ad的batch-1包，不重复源network/demand。大instant/queues仅本地哈希；迁机后无这些大文件就不能逐事件重读其原件，不能把摘要当原始数据。

首次全state向量颜色差为8943.0秒、18653534，仅pedestrian crossing idx22/24由r→G，idx0–20字符均未变，passenger/motor可用位也未变；不能称此刻车行绿改变。首个已证明passenger/motor可用位颜色差为8948.0秒，同一TLS的idx1/2/4/12/13/15/16由y→g/G，晚于首次traffic差8247.6秒700.4秒。全向量有18个actuated控制器有差，已证明passenger位差为17个；10个static颜色和program历史均相同，全部50个programID均未不同。readme_fixed_A只是programID名，不等于static。phase只在真实emit时刻有值；8951.0秒是共同emit事件中的首phase差，不能当完整phase历史首差或前填。

独立movement分析作为原74项之外的新来源单列：431控制位中59个taxonomy unknown，58位无绑定/未解决且可用性保留空值；另1位虽然类别unknown，passenger明确true，未把unknown当不可用。类别来自当前模型完整通路权限，说明允许类，不是该刻车辆实际通过或现场SPaT，也不证明全部计数损失来自信号。

逐TLS、900秒分段、颜色差区间、共同emit phase差及原脚本/计划/准备失败在study-source按原字节留档。control-inventory含原5文件与其原manifest；source-manifests明确原顶清单74项只含inventory的4数据文件，后来独立封口的inventory manifest（659cdd…）另计，未假称它属于原74。

离线核验：项目`.venv/Scripts/python.exe benchmarks/hamburg_ring1_healthy_z4_control_response_20261024_v1/freeze_evidence.py --verify-only`，会从原始50TLS导出/switches重算比较并逐CSV核对，不启动SUMO。`reproduce.py --output-dir <新目录>`仅离线复算全向量与movement两层比较；原record脚本也保留，但完整原生20check复跑还需按固定引用重定位源输入及本地instant原件。独立movement原manifest及所有8文件见movement-control-difference；c1333507…单独绑定，不伪称属于原74。未纳入任何fixed-history试验。
'''


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--freeze', action='store_true')
    mode.add_argument('--verify-only', action='store_true')
    args = parser.parse_args()
    freeze() if args.freeze else verify(HERE)
