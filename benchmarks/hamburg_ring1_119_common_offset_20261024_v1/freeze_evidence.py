"""Freeze three completed common119 phase-origin sensitivities; never run SUMO."""
import argparse
import gzip
import hashlib
from importlib.util import module_from_spec, spec_from_file_location
import json
from pathlib import Path
import re
import tempfile
import xml.etree.ElementTree as ET

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
PREVIOUS = REPO / 'benchmarks/hamburg_ring1_cohort_timing_extension_20261024_v1'
spec = spec_from_file_location('offset_archive_helpers', PREVIOUS / 'freeze_evidence.py')
prior = module_from_spec(spec)
spec.loader.exec_module(prior)
BASE = prior.stage1.GS
PAIR = REPO / 'outputs/hamburg-119-common-offset-20261024-v1'
FORWARD = REPO / 'outputs/hamburg-119-common-offset-forward-20261024-v1'
PROVENANCE = REPO / 'outputs/hamburg-lsa119-offset-provenance-20261024-v1'
RUNS = {'plus15': (PAIR, 15), 'minus15': (PAIR, 75), 'plus30': (FORWARD, 30)}
GROUP = {'LSA119_part0', '6390683126'}
raw, load, sha, raw_sha = prior.raw, prior.load, prior.sha, prior.raw_sha
read, jread, rows = prior.read, prior.jread, prior.rows
safe_relative, manifest_paths, save = prior.safe_relative, prior.manifest_paths, prior.save
snapshot_relative, verify_manifest, audit_measurement = prior.snapshot_relative, prior.verify_manifest, prior.audit_measurement
PREPARED, NATIVE = prior.PREPARED, prior.NATIVE
SEALED = {
    'pair-independent': (PAIR / 'independent', 'fc3d8e5dce00a6521769a745323dfaec09cf3d460c872cc1aab8eec1dd1e0dc9'),
    'forward-independent': (FORWARD / 'independent', '63fd7394bbe6b405e7e84a9726c1914960f1ceccaa69e1e860fd8567e69f5647'),
    'offset-response-analysis': (PAIR / 'offset-response-analysis', 'e47cc24602093f2fd0b7a4d0d33ff678ffaf7150a92cdd8464f5f74a7238f73a'),
    'offset-provenance': (PROVENANCE, '6fac62d0d9597b1ae91af70c0c1a854166cf787d87fb2bee6b86609e67fc2e3b')}
EVALUATION = ('demand.rou.xml', 'lane-intentions.json', 'core-result.json', 'evaluation.json', 'station-comparison.csv',
              'initialization52-comparison.csv', 'all182-comparison.csv', 'native208-comparison.csv', '535-all56.csv', 'contributing-passages.json.gz')


def source_read(name):
    return read(BASE, Path('full-priority') / name)


def selected_files():
    selected = {}
    for prefix, (source, expected) in SEALED.items():
        assert sha(source / 'manifest.json') == expected
        selected.update({Path(prefix) / p: source / p for p in manifest_paths(source)})
    for family, source in [('pair', PAIR), ('forward', FORWARD)]:
        for name in ['measure.py', 'plan.json'] + [run + '.execution.log' for run, (root, _) in RUNS.items() if root == source]:
            selected[Path('study-source') / family / name] = source / name
    for run, (root, _) in RUNS.items():
        assert (root / run / 'verdict.json').is_file()
        for name in ('scope.json', 'preflight.json', 'executed-measure.py', 'verdict.json', 'failure.log', 'failure.json'):
            p = Path(run) / name
            if (root / p).is_file():
                selected[p] = root / p
        for folder, names in [('prepared', PREPARED), ('evaluation', EVALUATION), ('evaluation/native', NATIVE)]:
            selected.update({Path(run) / folder / name: root / run / folder / name for name in names})
    # Two source modules support the clock-origin audit; preserve them once, not from a future live file.
    for name in ('hamburg_readme_control.py', 'ocit_c_programs.py'):
        selected[Path('source-dependencies') / name] = REPO / 'plugins/astra-sumo/src/astra_sumo/core' / name
    return selected


def reference_catalog():
    catalog = prior.reference_catalog()
    for row in load(PREVIOUS / 'source-provenance.json')['snapshots']:
        p = prior.prior.physical_reference(PREVIOUS, row['relative'])
        assert raw_sha(p) == row['raw_sha256']
        catalog[row['raw_sha256']] = p
    names = ['verdict.json', 'evaluation/native/field-counts.xml', 'evaluation/native/stall-monitor.json']
    names += ['prepared/' + n for n in PREPARED]
    names += ['implementation/' + p.name for p in (REPO / 'outputs/hamburg-119-receiving-diagnosis-20261023-v1/full-priority/implementation').glob('*.py')]
    for name in names:
        p = BASE / 'full-priority' / name
        catalog[raw_sha(p)] = p
    for original, digest in load(PROVENANCE / 'source-hashes.json').items():
        name = safe_relative(original.replace('\\', '/'))
        if name.parts[0] == 'benchmarks':
            p = REPO / name
            assert raw_sha(p) == digest
            catalog[digest] = p
    return catalog


def control_scope(source, candidate, offset):
    restored = candidate
    for identity in GROUP:
        pattern = rb'(<tlLogic\b[^>]*\bid="' + identity.encode() + rb'"[^>]*\boffset=")' + str(offset).encode() + rb'(")'
        restored, count = re.subn(pattern, lambda m: m[1] + b'0' + m[2], restored)
        assert count == 1
    assert restored == source, 'Only the two common offset attributes may differ.'
    programs = ET.fromstring(candidate).findall('tlLogic')
    assert len(programs) == 50
    for program in programs:
        if program.get('id') in GROUP:
            assert program.get('type') == 'static' and program.get('programID') == 'readme_fixed_A'
            assert sum(float(p.get('duration')) for p in program.findall('phase')) == 90
    return True


def audit_scope(case, run):
    offset = RUNS[run][1]
    control_scope(source_read('prepared/control.add.xml'), read(case, Path(run) / 'prepared/control.add.xml'), offset)
    for name in PREPARED:
        if name not in {'control.add.xml', 'scenario.json'}:
            assert source_read(Path('prepared') / name) == read(case, Path(run) / 'prepared' / name)
    a, b = json.loads(source_read('prepared/scenario.json')), jread(case, Path(run) / 'prepared/scenario.json')
    assert {k for k in a.keys() | b.keys() if a.get(k) != b.get(k)} == {'control_sha256', 'control_variant', 'control_assumptions'}
    assert b['control_assumptions'].startswith(a['control_assumptions'])
    assert b['control_sha256'] == hashlib.sha256(read(case, Path(run) / 'prepared/control.add.xml')).hexdigest()
    p = jread(case, Path(run) / 'preflight.json')
    assert p['prepared_hashes'] == jread(case, Path(run) / 'scope.json')['prepared_hashes']
    for name, expected in p['prepared_hashes'].items():
        assert hashlib.sha256(read(case, Path(run) / 'prepared' / name)).hexdigest() == expected
    assert p['runner_sha256'] == hashlib.sha256(read(case, Path(run) / 'executed-measure.py')).hexdigest()
    assert p['source_verdict_sha256'] == hashlib.sha256(source_read('verdict.json')).hexdigest()
    assert p['provenance_sha256'] == SEALED['offset-provenance'][1]
    for name, expected in p['implementation_sha256'].items():
        assert hashlib.sha256(source_read(Path('implementation') / name)).hexdigest() == expected
    assert set(p['versions'].values()) == {'1.27.1'}
    original_venv = Path(p['python']).parents[1]
    assert all(Path(path).is_relative_to(original_venv) for path in p['module_paths'].values())
    return dict(offset_xml_s=offset, common_controllers=sorted(GROUP), only_two_offsets_and_declared_scenario_metadata_changed=True,
                all8640_explicit_requests_and_real_observations_byte_identical=True, runtime_parameters=p['parameters'],
                binaries_sha256=p['binaries'], implementation_sha256=p['implementation_sha256'], actual_module_paths=p['module_paths'])


def materialize_run(case, run, out):
    for folder, names in [('prepared', PREPARED), ('evaluation', EVALUATION), ('evaluation/native', tuple(n for n in NATIVE if n not in {'instant-e1.xml', 'queues.xml'}))]:
        for name in names:
            p = out / folder / name
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(read(case, Path(run) / folder / name))


def audit_run(case, run):
    checked = audit_scope(case, run)
    verdict = jread(case, Path(run) / 'verdict.json')
    independent_folder = 'forward-independent' if run == 'plus30' else 'pair-independent'
    independent = jread(case, Path(independent_folder) / 'summary.json')['cases'][run]
    baseline = json.loads(source_read('verdict.json'))['candidate']
    assert independent['audit_status'] == 'pass' and independent['raw_verdict'] == verdict['status'] == 'rejected'
    assert verdict['before'] == baseline and verdict['after'] == independent['measured'] and verdict['gates'] == independent['gates']
    assert jread(case, Path(run) / 'evaluation/core-result.json')['errors'] == []
    with tempfile.TemporaryDirectory() as temporary:
        temp = Path(temporary)
        materialize_run(case, run, temp)
        signatures = {r['vehicle_id']: [r['field_id']] for r in json.loads((temp / 'prepared/lane-intentions.json').read_bytes())['vehicles']}
        measured, _, outside, repeats = audit_measurement(temp / 'prepared', temp / 'evaluation', signatures)
        comparisons = rows(temp, 'evaluation/all182-comparison.csv')
    for local, saved in [('main130', 'metrics130'), ('initial52', 'metrics52'), ('all182', 'metrics182')]:
        assert all(verdict['after'][saved][k] == v for k, v in measured[local].items())
    assert measured['completion'] == verdict['after']['completion'] and measured['station40_MAE'] == verdict['after']['station40_MAE']
    assert not outside and not repeats and independent['all53556']['TAE'] == 0
    metrics535 = [r for r in comparisons if r['detector_id'] in {'hh_0535_Z_5_30894', 'hh_0535_Z_6_33855', 'hh_0535_Z_7_29316', 'hh_0535_Z_8_34287'}]
    assert len(metrics535) == 56 and all(int(r['diff_nVehContrib_minus_expected']) == 0 for r in metrics535)
    gates = dict(safe=measured['safe'], healthy=measured['healthy'], no_repeated_fields=not repeats, no_extra_fields=not outside,
                 all53556_zero=True, MAE_improves=measured['main130']['MAE'] < baseline['metrics130']['MAE'],
                 SSE_not_worse=measured['main130']['SSE'] <= baseline['metrics130']['SSE'], all182_TAE_not_worse=measured['all182']['TAE'] <= baseline['metrics182']['TAE'])
    assert gates == verdict['gates'] and not all(gates.values())
    main = [r for r in comparisons if float(r['begin']) >= 3600]
    saved_fields = {r['field_id']: r for r in rows(case, Path(independent_folder) / 'field13-main.csv') if r['case'] == run}
    field_scores = {}
    for field in sorted({r['detector_id'] for r in main}):
        errors = [int(r['diff_nVehContrib_minus_expected']) for r in main if r['detector_id'] == field]
        assert len(errors) == 10 and sum(map(abs, errors)) == int(saved_fields[field]['after_TAE']) and sum(errors) == int(saved_fields[field]['after_signed'])
        field_scores[field] = dict(TAE=sum(map(abs, errors)), signed=sum(errors))
    assert len(field_scores) == 13 and sum(r['TAE'] for r in field_scores.values()) == measured['main130']['TAE']
    return dict(raw_verdict='rejected', measurement=measured, all53556=independent['all53556'], gates=gates, scope=checked,
                failed_gates=[k for k, v in gates.items() if not v], field13_main=field_scores)


def audit_packet(case):
    result = {run: audit_run(case, run) for run in RUNS}
    provenance = jread(case, 'offset-provenance/evidence.json')
    assert provenance['absolute_field_phase_anchor']['status'] == 'unknown' and not provenance['absolute_field_phase_anchor']['current_SUMO_offset_zero_is_measured_SPAT']
    assert provenance['source_cycle_s'] == 90 and set(provenance['common_phase_origin_group']) == GROUP
    response = jread(case, 'offset-response-analysis/summary.json')
    assert response['evidence_points_s'] == [-15, 0, 15] and not response['forward30_results_read']
    assert result['plus15']['failed_gates'] == ['all182_TAE_not_worse'] and result['plus30']['failed_gates'] == ['SSE_not_worse']
    return dict(status='review_required', formal_result=False, native_runs_recorded=3, native_runs_by_freeze=0,
                runs=result, lowest_healthy_main_MAE=result['plus15']['measurement']['main130']['MAE'],
                original_offset0_reference_MAE=3.7153846153846155, auto_promotion=False, exact130=False,
                source_field_clock_unknown=True, smoothness_or_optimum_claimed=False)


def verify(case):
    verify_manifest(case)
    verify_manifest(BASE)
    refs = load(case / 'source-references.json')
    assert sha(BASE / 'manifest.json') == refs['offset0_source_manifest_sha256']
    for folder, (_, expected) in SEALED.items():
        assert hashlib.sha256(read(case, Path(folder) / 'manifest.json')).hexdigest() == expected
        for row in jread(case, Path(folder) / 'manifest.json')['files']:
            data = read(case, Path(folder) / safe_relative(row['path']))
            assert len(data) == row.get('bytes', row.get('size_bytes')) and hashlib.sha256(data).hexdigest() == row['sha256']
    for row in load(case / 'source-provenance.json')['snapshots']:
        data = read(case, row['relative'])
        assert len(data) == row['raw_bytes'] and hashlib.sha256(data).hexdigest() == row['raw_sha256']
    for row in load(case / 'historical-source-bindings.json')['bindings']:
        data = read(case, row['target']) if row['scope'] == 'packet' else raw(REPO / safe_relative(row['target']))
        assert hashlib.sha256(data).hexdigest() == row['sha256']
    assert audit_packet(case) == load(case / 'results.json')['audit']
    print('Three common-offset runs,130/40/52/182/208,scope/runtime and unchanged rejected gates: pass', flush=True)


def freeze():
    assert not (HERE / 'manifest.json').exists() and not (HERE / 'source-provenance.json').exists()
    locations = {h: dict(scope='repository', target=p.relative_to(REPO).as_posix()) for h, p in reference_catalog().items()}
    aliases, provenance, local = {}, [], []
    for name, source in sorted(selected_files().items()):
        digest = sha(source)
        if source.name in {'instant-e1.xml', 'queues.xml'}:
            local.append(dict(path=str(source), bytes=source.stat().st_size, sha256=digest))
            continue
        relative = name.as_posix()
        if digest in locations:
            aliases[relative] = dict(**locations[digest], sha256=digest)
        else:
            p = HERE / (relative + '.gz')
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(gzip.compress(source.read_bytes(), mtime=0))
            locations[digest] = dict(scope='packet', target=relative)
        provenance.append(dict(source=str(source), relative=relative, raw_bytes=source.stat().st_size, raw_sha256=digest))
    save(HERE / 'source-provenance.json', dict(snapshots=provenance, source_files_unchanged=True))
    save(HERE / 'byte-references.json', dict(aliases=aliases, rule='Exact SHA-bound old sources and duplicate files only.'))
    bindings = []
    for folder in ('offset-provenance', 'offset-response-analysis'):
        for original, digest in jread(HERE, Path(folder) / 'source-hashes.json').items():
            assert digest in locations, (original, digest)
            bindings.append(dict(declared_path=original, sha256=digest, **locations[digest]))
    save(HERE / 'historical-source-bindings.json', dict(bindings=bindings, original_hashes_not_rewritten=True))
    save(HERE / 'local-only-large-traces.json', dict(files=local, all3_full_instant_and_queues_local_only=True, compact_complete_contributions_included=True))
    save(HERE / 'source-references.json', dict(offset0_source_commit='42a827e', offset0_source_manifest_sha256=sha(BASE / 'manifest.json'), no_demand_composition_from_plus15=True))
    save(HERE / 'results.json', dict(status='review_required', formal_result=False, audit=audit_packet(HERE)))
    (HERE / '.gitattributes').write_text('* -text whitespace=blank-at-eol,blank-at-eof,space-before-tab,cr-at-eol\n*.gz binary\n', encoding='utf-8')
    (HERE / 'README.md').write_text(README, encoding='utf-8')
    assert all(sha(Path(row['source'])) == row['raw_sha256'] for row in provenance)
    save(HERE / 'manifest.json', dict(status='review_required', formal_result=False, files=[dict(path=p.relative_to(HERE).as_posix(), bytes=p.stat().st_size, sha256=sha(p)) for p in sorted(HERE.rglob('*')) if p.is_file() and p != HERE / 'manifest.json' and '__pycache__' not in p.parts]))
    verify(HERE)


README = '''# 119协调组公共相位原点：三个完整原生响应

本包保留三个新14400秒原生运行，均以原健康full-priority输入为源，只共同改变`LSA119_part0`与`6390683126`的offset。+30直接使用原源，不叠加+15需求或控制。8640显式请求、路线、stop、field、数量、网络、真实130+52观测和四处bodyhold不变；相位状态、时长、类型和两控制器间相对时序不变。没有重采样或新需求生成。

| 公共offset | 主130 MAE / SSE / TAE | 初始52 TAE | 全182 TAE | 完成情况 | 原判定 |
|---|---|---:|---:|---|---|
| 原源0 |3.715385 / 6709 / 483|105|588|8640到达|原offset=0参考|
| +15 |3.538462 / 5818 / 460|168|628|8640到达|rejected，仅182门失败|
| −15（XML75） |4.630769 / 9974 / 602|110|712|27等待、62在途|rejected|
| +30 |3.569231 / 6942 / 464|91|555|8640到达|rejected，仅SSE门失败|

三次均零碰撞/传送/discard、无重复或额外字段，535指定56行仍全部零。40派生站点MAE依次为原源10.975、+15为10.55、−15为13.1、+30为10.55。全部130/52/182/208、完成情况、逐字段TAE和signed残差、原8门及独立核对均保留。audit pass只表示对账通过，不等于候选被采用。

+15是当前实测健康主窗误差最低点，同时初始化更差；它是有条件的Pareto点，不能把原rejected改成原判据已通过。+30初始化与全182较低，但主窗SSE高于原源，原rejected也不改。原offset=0参考3.715、最低主窗健康+15及+30的取舍分列，不自动promotion。原8门保留为本轮预设条件，不能替代用户主130归零目标；没有主130零误差，也不从这些单seed有限点推断平滑、最优或全局可达性。

官方119/SP1配置支持TU90和源相对`SignalzeitenVersatz=0`，并不确证计数日期的绝对灯色。当地午夜周期零点及Z15/K9物理映射仍为声明。仅这两个119来源投影控制器共同平移，保留当前K9→K2及反向间隔；不能因此证明现场协调或普遍安全。网络内另存的95秒program0不能混作外部90秒程序。本研究未新增逐时刻getProgram/getPhase历史，不是field SPaT。原GS priority、197优先关系及lane先验的现场不确定性仍在。

offset-response-analysis按其原封口只分析−15/0/+15，不读后来+30；其“下一次检验”描述是当时记录。四点表额外列入+30实测，不覆盖旧分析。source-control来源、两独立reader、两runner及执行预检按原字节保存；共用network/demand/intents/targets/core按固定旧包SHA引用。三个完整instant和queues仅本地大小/SHA，compact完整贡献保存。未纳入后续fixture或其他方向。

离线核验：`.venv\\Scripts\\python.exe benchmarks/hamburg_ring1_119_common_offset_20261024_v1/reproduce.py --mode verify`，不启动SUMO。

显式完整原生复现须从仓库根目录先设置本项目环境，避免ambient SUMO_HOME/PYTHONPATH导入系统SUMO。裸venv命令不能保证实际模块来源：

```powershell
$env:PYTHONPATH = Join-Path $PWD 'plugins/astra-sumo/src'
$env:SUMO_HOME = Join-Path $PWD '.venv/Lib/site-packages/sumo'
.\\.venv\\Scripts\\python.exe benchmarks/hamburg_ring1_119_common_offset_20261024_v1/reproduce.py --mode native-full --case plus15 --output-dir outputs/reproduce-119-offset-plus15
```

入口在任何native调用前检查并记录实际libsumo/traci/sumolib路径、1.27.1版本、二进制及core哈希，拒绝项目.venv外的模块；失败路径也保留。case可选plus15/minus15/plus30；必须新目录。输入从冻结包解压，按当前仓库路径调用core，不生成OD、不依赖历史outputs绝对路径。本次冻结未新增SUMO；离线核验不等于重新原生复现，复现rejected结果不表示标定完成。
'''


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--freeze', action='store_true')
    mode.add_argument('--verify-only', action='store_true')
    args = parser.parse_args()
    freeze() if args.freeze else verify(HERE)
