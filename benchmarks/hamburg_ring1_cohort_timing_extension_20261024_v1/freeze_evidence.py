"""Freeze one completed1800s prefix and its rejected full validation; no SUMO."""
import argparse
from collections import Counter
from copy import deepcopy
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
SOURCE = REPO / 'outputs/hamburg-cohort-timing-extension-20261024-v1'
STAGE2 = REPO / 'benchmarks/hamburg_ring1_causal_second_prefix_20261024_v1'
spec = spec_from_file_location('extension_archive_helpers', STAGE2 / 'freeze_evidence.py')
prior = module_from_spec(spec)
spec.loader.exec_module(prior)
stage1 = prior.prior
raw, load, sha, raw_sha = prior.raw, prior.load, prior.sha, prior.raw_sha
read, jread, rows = prior.read, prior.jread, prior.rows
safe_relative, snapshot_relative, manifest_paths, save = prior.safe_relative, prior.snapshot_relative, prior.manifest_paths, prior.save
verify_manifest, intervals, check_coverage = prior.verify_manifest, prior.intervals, prior.check_coverage
summary_prefix, audit_measurement = prior.summary_prefix, stage1.audit_measurement
PREPARED, NATIVE = prior.PREPARED, prior.NATIVE
SELECTED = 'extension'
FULL = Path(SELECTED) / 'full-evaluation'
SEALED = {'extension-independent': 'e22a5ef4c3c5021b33a2d67dc795bc688f30f179655e151a55874f1d5af745a8',
          'full-independent': '3cdc1bb222c6ecfa3f9625addca9037c03f3b608829b408ca6a1bc42e61e20c9'}
FEASIBILITY = REPO / 'outputs/hamburg-causal-extension-feasibility-20261024-v1'
FEASIBILITY_SHA = '2112f7784c71798399c035a34e5185d4f55d3f65002d4291d8c02b609616498a'


def source_read(name):
    return read(STAGE2, Path(prior.SELECTED) / name)


def selected_files():
    assert (SOURCE / FULL / 'verdict.json').is_file()
    selected = {}
    for folder, expected in SEALED.items():
        assert sha(SOURCE / folder / 'manifest.json') == expected
        selected.update({Path(folder) / p: SOURCE / folder / p for p in manifest_paths(SOURCE / folder)})
    assert sha(FEASIBILITY / 'manifest.json') == FEASIBILITY_SHA
    selected.update({Path('feasibility') / p: FEASIBILITY / p for p in manifest_paths(FEASIBILITY)})
    for name in ('prefix26.csv', 'prefix26-provenance.json', 'prepare_extension.py', 'run_prefix.py', 'run_full.py',
                 'extension.check-only.json', 'extension.selfcheck.json', 'extension.execution.log',
                 'full-extension.check-only.json', 'full-extension.execution.log'):
        selected[snapshot_relative(name)] = SOURCE / name
    for name in ('all182-coverage.json', 'cells294.json', 'contributing-passages.json.gz', 'core-result.json',
                 'crossing379.csv', 'departure-changes7739.csv', 'departure-proposals.json', 'executed-run.py',
                 'field-departure-stocks182.csv', 'od-cell-deltas.csv', 'od-vector.json', 'preflight.json',
                 'record-result.json', 'scope-proof.json', 'selfchecks.json', 'failure.json', 'failure.log'):
        p = Path(SELECTED) / name
        if (SOURCE / p).is_file():
            selected[p] = SOURCE / p
    for folder in ('implementation', 'source-snapshot'):
        selected.update({p.relative_to(SOURCE): p for p in (SOURCE / SELECTED / folder).rglob('*') if p.is_file() and '__pycache__' not in p.parts})
    for name in PREPARED:
        selected[Path(SELECTED) / 'prepared' / name] = SOURCE / SELECTED / 'prepared' / name
    for name in ('preflight.json', 'executed-run_full.py', 'executed-prefix-checker.py', 'core-result.json',
                 'verdict.json', 'prefix-parity.json', 'demand.rou.xml', 'lane-intentions.json', 'od-vector.json',
                 'evaluation.json', 'station-comparison.csv', 'initialization52-comparison.csv', 'all182-comparison.csv',
                 'native208-comparison.csv', '535-all56.csv', 'contributing-passages.json.gz', 'failure.log'):
        p = FULL / name
        if (SOURCE / p).is_file():
            selected[p] = SOURCE / p
    selected.update({p.relative_to(SOURCE): p for p in (SOURCE / FULL / 'implementation').glob('*.py')})
    for base in (Path(SELECTED), FULL):
        for name in NATIVE:
            selected[base / 'native' / name] = SOURCE / base / 'native' / name
    return selected


def reference_catalog():
    result = prior.reference_catalog()
    for row in load(STAGE2 / 'source-provenance.json')['snapshots']:
        p = prior.physical_reference(STAGE2, row['relative'])
        assert raw_sha(p) == row['raw_sha256']
        result[row['raw_sha256']] = p
    return result


def audit_input(case):
    proof = jread(case, Path(SELECTED) / 'scope-proof.json')
    assert proof['N'] == 8640 and proof['status'] == 'pass' and all(proof['checks'].values())
    for name, expected in proof['prepared_hashes'].items():
        assert hashlib.sha256(read(case, Path(SELECTED) / 'prepared' / name)).hexdigest() == expected
    original = ET.fromstring(source_read('prepared/demand.rou.xml'))
    generated = ET.fromstring(read(case, Path(SELECTED) / 'prepared/demand.rou.xml'))
    old, new = [{v.get('id'): v for v in root.findall('vehicle')} for root in (original, generated)]
    old_meta = {r['vehicle_id']: r for r in json.loads(source_read('prepared/lane-intentions.json'))['vehicles']}
    meta = {r['vehicle_id']: r for r in jread(case, Path(SELECTED) / 'prepared/lane-intentions.json')['vehicles']}
    moves = {r['vehicle_id']: r for r in jread(case, Path(SELECTED) / 'departure-proposals.json')}
    assert old.keys() == new.keys() == meta.keys() and len(new) == 8640 and len(moves) == 7739
    assert [i for i in old if i not in moves] == [i for i in new if i not in moves] == list(new)[:901]
    old_blocks, new_blocks = [{ET.fromstring(b).get('id'): b for b in re.findall(rb'<vehicle\b[^>]*>.*?</vehicle>', data, re.S)}
                              for data in (source_read('prepared/demand.rou.xml'), read(case, Path(SELECTED) / 'prepared/demand.rou.xml'))]
    assert all(old_blocks[i] == new_blocks[i] for i in old if i not in moves)
    assert [ET.tostring(v) for v in original if v.tag != 'vehicle'] == [ET.tostring(v) for v in generated if v.tag != 'vehicle']
    cells = jread(case, Path(SELECTED) / 'cells294.json')['cells']
    indices = {(c['origin'], c['destination'], int(c['begin_s'])): c['index'] for c in cells}
    assert len(indices) == 294 and set(indices.values()) == set(range(294))
    q0, q1, shifts = Counter(), Counter(), Counter()
    crossing = 0
    native_ms = stage1.prior.prior.native_ms
    for identity, v in new.items():
        before = old[identity]
        restored = deepcopy(v)
        restored.set('depart', before.get('depart'))
        assert ET.tostring(restored) == ET.tostring(before)
        route = before.find('route').get('edges').split()
        h0, h1 = [native_ms(r.get('depart')) // 900000 for r in (before, v)]
        c0, c1 = [indices[route[0], route[-1], h * 900] for h in (h0, h1)]
        q0[c0] += 1
        q1[c1] += 1
        assert old_meta[identity]['od_cell'] == c0 and meta[identity]['od_cell'] == c1
        if identity in moves:
            m = moves[identity]
            assert abs(float(v.get('depart')) - m['proposed_depart_s']) < 1e-8 and abs(float(before.get('depart')) - m['source_depart_s']) < 1e-8
            assert native_ms(v.get('depart')) >= 1800000 and h0 - h1 in (0, 1)
            shift = native_ms(v.get('depart')) - native_ms(before.get('depart'))
            assert shift == int(m['shift_s']) * 1000
            shifts[shift // 1000] += 1
            crossing += c0 != c1
            restored_meta = dict(meta[identity])
            assert abs(restored_meta['depart_s'] - float(v.get('depart'))) < 1e-8
            restored_meta.pop('depart_s')
            if 'depart_s' in old_meta[identity]:
                restored_meta['depart_s'] = old_meta[identity]['depart_s']
            restored_meta['od_cell'] = c0
            assert restored_meta == old_meta[identity]
        else:
            assert ET.tostring(v) == ET.tostring(before) and meta[identity] == old_meta[identity]
            assert native_ms(v.get('depart')) < 1800000
    vector = jread(case, Path(SELECTED) / 'od-vector.json')
    assert vector['values'] == vector['integer_counts'] == [q1[i] for i in range(294)]
    assert vector['source_vector'] == [q0[i] for i in range(294)] and not vector['inventory_unchanged']
    assert sum(q0.values()) == sum(q1.values()) == 8640 and crossing == 379 and shifts == {-126: 3253, -90: 4486}
    delta = [q1[i] - q0[i] for i in range(294)]
    assert sum(v != 0 for v in delta) == 53 and sum(abs(v) for v in delta) == 114
    assert all(sum(q0[i] for i in range(j, 294, 21)) == sum(q1[i] for i in range(j, 294, 21)) for j in range(21))
    assert sum(q0[i] for i in range(84)) == 1953 and sum(q1[i] for i in range(84)) == 1989
    for name in ('network.net.xml', 'control.add.xml', 'field-loops.add.xml', 'expected-counts.csv', 'initialization-counts.csv'):
        assert read(case, Path(SELECTED) / 'prepared' / name) == source_read(Path('prepared') / name)
    return dict(requests=8640, inherited_requests_unchanged=901, future_departures_changed=7739,
                shifts_s={'-126': 3253, '-90': 4486}, native_bin_crossing_vehicles=379,
                changed_q_cells=53, q_L1=114, source_initial_main_requests=[1953, 6687],
                candidate_initial_main_requests=[1989, 6651], routes_stops_fields_and_global21OD_totals_preserved=True,
                same_q=False, quantile_resampling=False)


def audit_prefix(case):
    record = jread(case, Path(SELECTED) / 'record-result.json')
    p = jread(case, Path(SELECTED) / 'preflight.json')
    assert record['schema'] == 'astra.hamburg.cohort-timing-extension-prefix/v1' and record['status'] == 'pass' and all(record['checks'].values())
    assert record['preflight_sha256'] == hashlib.sha256(read(case, Path(SELECTED) / 'preflight.json')).hexdigest()
    assert p['runner_sha256'] == hashlib.sha256(read(case, Path(SELECTED) / 'executed-run.py')).hexdigest()
    assert p['scope_sha256'] == hashlib.sha256(read(case, Path(SELECTED) / 'scope-proof.json')).hexdigest()
    for original, expected in record['native_output_hashes'].items():
        name = original.replace('\\', '/').split('/')[-1]
        assert hashlib.sha256(read(case, Path(SELECTED) / 'native' / name)).hexdigest() == expected
    for name, expected in p['implementation_sha256'].items():
        assert hashlib.sha256(read(case, Path(SELECTED) / 'implementation' / name)).hexdigest() == expected
    native = intervals(read(case, Path(SELECTED) / 'native/field-counts.xml'))
    targets = rows(case, 'study-source/prefix26.csv')
    assert len(native) == len(targets) == 26 and set(native) == {stage1.key(r) for r in targets}
    errors = [int(native[stage1.key(r)]['nVehContrib']) - int(r['expected_total']) for r in targets]
    metrics = dict(rows=26, TAE=sum(abs(v) for v in errors), SSE=sum(v*v for v in errors), MAE=sum(abs(v) for v in errors)/26, max_abs_error=max(abs(v) for v in errors))
    assert metrics == record['prefix26_metrics'] and metrics['TAE'] == record['frozen_first13_TAE'] == 0
    coverage = jread(case, Path(SELECTED) / 'all182-coverage.json')
    check_coverage(coverage, native, rows(case, Path(SELECTED) / 'prepared/initialization-counts.csv') + rows(case, Path(SELECTED) / 'prepared/expected-counts.csv'))
    assert record['measured_real_rows'] == 26 and record['unmeasured_real_rows'] == 156 and record['full_health_not_claimed']
    assert record['full130_metrics'] is None and record['remaining26_initial_metrics'] is None and record['combined182_metrics'] is None
    compact = json.loads(gzip.decompress(read(case, Path(SELECTED) / 'contributing-passages.json.gz')))
    assert compact['reconciled'] and Counter((r['detector_id'], r['interval_begin_s'], r['interval_end_s']) for r in compact['passages']) == Counter({k: int(v['nVehContrib']) for k, v in native.items()})
    assert all(abs(r['notification_time_s'] - 1800) < 1e-8 for r in compact['outside_periodic_intervals'])
    complete = compact['passages'] + compact['outside_periodic_intervals']
    assignments = {r['vehicle_id']: r['field_id'] for r in jread(case, Path(SELECTED) / 'prepared/lane-intentions.json')['vehicles']}
    assert len(compact['passages']) == len({r['vehicle_id'] for r in compact['passages']}) == 901
    assert len({(r['vehicle_id'], r['detector_id']) for r in complete}) == len(complete) and all(assignments[r['vehicle_id']] == r['detector_id'] for r in complete)
    assert hashlib.sha256(read(case, Path(SELECTED) / 'native/instant-e1.xml')).hexdigest() in {r['sha256'] for r in compact['inputs']}
    steps = summary_prefix(read(case, Path(SELECTED) / 'native/summary.xml'))
    assert len(steps) == 18000 and float(steps[-1]['time']) == 1799.9 and not any(int(steps[-1][k]) for k in ('collisions', 'teleports', 'discarded'))
    independent = jread(case, 'extension-independent/summary.json')
    assert independent['audit_status'] == 'pass' and independent['metrics26'] == metrics and independent['complete_unique_frozen901']
    return dict(validity_status='pass', initial26=metrics, future156_values=None, complete_unique_events=901,
                executed_runner_sha256=p['runner_sha256'], source_summary_equal_observed=independent['source_summary_equal'],
                source_summary_equality_is_blanket_gate=False, input=audit_input(case))


def materialize_full(case, out):
    for name in PREPARED:
        p = out / 'prepared' / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(read(case, Path(SELECTED) / 'prepared' / name))
    for row in load(case / 'source-provenance.json')['snapshots']:
        name = row['relative']
        if name.startswith(FULL.as_posix() + '/'):
            p = out / 'evaluation' / Path(name).relative_to(FULL)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(read(case, name))


def prefix_full_parity(case, evaluation):
    original = intervals(read(case, Path(SELECTED) / 'native/field-counts.xml'))
    current = {k: v for k, v in intervals((evaluation / 'native/field-counts.xml').read_bytes()).items() if k[2] <= 1800}
    return dict(all26_attributes=original == current,
                summary18000_except_CPU_duration=summary_prefix(read(case, Path(SELECTED) / 'native/summary.xml')) == summary_prefix((evaluation / 'native/summary.xml').read_bytes()))


def audit_full(case):
    verdict = jread(case, FULL / 'verdict.json')
    independent = jread(case, 'full-independent/summary.json')
    assert independent['schema'] == 'astra.hamburg.cohort-extension-full-independent/v1' and independent['audit_status'] == 'pass'
    assert verdict['status'] == independent['selection'] == 'rejected'
    assert verdict['after'] == independent['after'] and verdict['before'] == independent['reference'] and verdict['global_best'] == independent['global_best']
    assert verdict['gates'] == independent['gates'] and verdict['global_best_gates'] == independent['global_best_gates']
    assert verdict['prefix_parity_pass'] and all(jread(case, FULL / 'prefix-parity.json')['checks'].values()) and all(independent['first1800_strict_parity'].values())
    assert jread(case, FULL / 'core-result.json')['errors'] == []
    for name in ('demand.rou.xml', 'lane-intentions.json'):
        assert read(case, FULL / name) == read(case, Path(SELECTED) / 'prepared' / name)
    assert read(case, FULL / 'od-vector.json') == read(case, Path(SELECTED) / 'od-vector.json')
    p = jread(case, FULL / 'preflight.json')
    for name, key in [('executed-run_full.py', 'full_runner_sha256'), ('executed-prefix-checker.py', 'prefix_checker_sha256')]:
        assert hashlib.sha256(read(case, FULL / name)).hexdigest() == p[key]
    for name, expected in p['prepared_hashes'].items():
        assert hashlib.sha256(read(case, Path(SELECTED) / 'prepared' / name)).hexdigest() == expected
    for name, expected in p['implementation_sha256'].items():
        assert hashlib.sha256(read(case, FULL / 'implementation' / name)).hexdigest() == expected
    for original, expected in p['prefix_evidence_hashes'].items():
        name = original.replace('\\', '/').split('/hamburg-cohort-timing-extension-20261024-v1/')[1]
        assert hashlib.sha256(read(case, snapshot_relative(name))).hexdigest() == expected
    with tempfile.TemporaryDirectory() as temporary:
        temp = Path(temporary)
        materialize_full(case, temp)
        signatures = {r['vehicle_id']: [r['field_id']] for r in json.loads((temp / 'prepared/lane-intentions.json').read_bytes())['vehicles']}
        measured, _, outside, repeated = audit_measurement(temp / 'prepared', temp / 'evaluation', signatures)
        assert all(prefix_full_parity(case, temp / 'evaluation').values())
    assert not outside and not repeated
    for local, saved in [('main130', 'metrics130'), ('initial52', 'metrics52'), ('all182', 'metrics182')]:
        assert all(verdict['after'][saved][k] == v for k, v in measured[local].items())
    assert measured['completion'] == verdict['after']['completion'] and measured['station40_MAE'] == verdict['after']['station40_MAE']
    assert verdict['all53556'] == independent['all53556'] and verdict['all53556']['TAE'] == 0
    assert not independent['full_stage2_parent_comparison_available'] and independent['terminal_count'] == 35
    return dict(raw_verdict='rejected', full=measured, initial26_still_exact=True, own1800_prefix_parity_pass=True,
                all53556=verdict['all53556'], gates=verdict['gates'], global_best_gates=verdict['global_best_gates'],
                full_stage2_parent_comparison_available=False, claim_boundary='Main130 regresses against healthy references. No full stage2 parent exists for an extension-only causal comparison; 35 vehicles remain running.')


def audit_packet(case):
    return dict(status='review_required', formal_result=False, native_runs_recorded=2, native_runs_by_freeze=0,
                prefix=audit_prefix(case), selected_full=audit_full(case), best_main_MAE_unchanged=3.7153846153846155,
                later_service_studies_included=False)


def verify(case):
    verify_manifest(case)
    verify_manifest(STAGE2)
    assert sha(STAGE2 / 'manifest.json') == load(case / 'source-references.json')['stage2_manifest_sha256']
    for folder, expected in {**SEALED, 'feasibility': FEASIBILITY_SHA}.items():
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
    print('Prefix26/null156,changed294inventory,full130/40/52/182/208 and rejected full: pass', flush=True)


def freeze():
    assert not (HERE / 'manifest.json').exists() and not (HERE / 'source-provenance.json').exists()
    locations = {h: {'scope': 'repository', 'target': p.relative_to(REPO).as_posix()} for h, p in reference_catalog().items()}
    aliases, provenance, local = {}, [], []
    for name, source in sorted(selected_files().items()):
        digest = sha(source)
        if source.name == 'queues.xml' or (name.is_relative_to(FULL) and source.name == 'instant-e1.xml'):
            local.append(dict(path=str(source), bytes=source.stat().st_size, sha256=digest))
            continue
        relative = name.as_posix()
        if digest in locations:
            aliases[relative] = {**locations[digest], 'sha256': digest}
        else:
            p = HERE / (relative + '.gz')
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(gzip.compress(source.read_bytes(), mtime=0))
            locations[digest] = dict(scope='packet', target=relative)
        provenance.append(dict(source=str(source), relative=relative, raw_bytes=source.stat().st_size, raw_sha256=digest))
    save(HERE / 'source-provenance.json', dict(snapshots=provenance, source_files_unchanged=True))
    save(HERE / 'byte-references.json', dict(aliases=aliases, rule='Exact SHA-bound old sources and duplicates only.'))
    bindings = []
    for original, digest in load(SOURCE / SELECTED / 'scope-proof.json')['source_hashes'].items():
        assert digest in locations, (original, digest)
        bindings.append(dict(declared_path=original, sha256=digest, **locations[digest]))
    save(HERE / 'historical-source-bindings.json', dict(bindings=bindings, source_hashes_not_rewritten=True))
    save(HERE / 'local-only-large-traces.json', dict(files=local, short_prefix_instant_gzip_included=True, full_instant_and_all_queues_local_only=True))
    save(HERE / 'source-references.json', dict(stage2_commit='3e21a8b7602704e6ec4c3c171503011f35d6e50f', stage2_manifest_sha256=sha(STAGE2 / 'manifest.json'), stage2_full_was_not_run=True, original_source_commit='a889d72', best_commit='42a827e'))
    save(HERE / 'results.json', dict(status='review_required', formal_result=False, audit=audit_packet(HERE)))
    (HERE / '.gitattributes').write_text('* -text whitespace=blank-at-eol,blank-at-eof,space-before-tab,cr-at-eol\n*.gz binary\n', encoding='utf-8')
    (HERE / 'README.md').write_text(README, encoding='utf-8')
    assert all(sha(Path(row['source'])) == row['raw_sha256'] for row in provenance)
    save(HERE / 'manifest.json', dict(status='review_required', formal_result=False, files=[dict(path=p.relative_to(HERE).as_posix(), bytes=p.stat().st_size, sha256=sha(p)) for p in sorted(HERE.rglob('*')) if p.is_file() and p != HERE / 'manifest.json' and '__pycache__' not in p.parts]))
    verify(HERE)


README = '''# 全时域cohort时刻延伸：初始化26行零与完整验证退步

本包保留一次0–1800秒prefix和一次14400秒full，共两次原生调用。prefix的26项初始化观测全部零残差，901个完整贡献均为唯一预期字段；其余156项当时为null。完整运行保持同一新输入的26个E1区间全属性和18000条非CPU summary，并通过独立事件、到达及hold核对。初始化26零不等于主130归零。

full原判rejected：主130 TAE562、SSE9896、MAE4.3230769、最大误差39；52初始化TAE42/SSE516；全182 TAE604；40派生站点MAE13.15。8640插入、8605到达、35在途、0等待，零碰撞/传送/discard；8622个完整预期字段贡献、无重复或额外字段，535指定56行仍零。主计数与完整健康均未满足选用条件。全局最好3.7153846不变。

输入从已冻结stage2的126232候选延伸：前901请求原字节与意图保持，其余7739个depart中3253个提前126秒、4486个提前90秒；不重采样quantile，不改ID、路线、stop、field、全局21OD总量、网络、控制或真实观测。379辆跨一个实际请求bin，重算294数量后有53个cell改变、L1=114，不能称same-q。实际初始化/主窗请求为1989/6651；真实观测的初始化/主窗贡献仍为1953/6687。departure库存不是passage计数。

source stage2只测过1800秒，没有同输入full父对照。本次full与健康one-share3.938和全局最好3.715分别比较；不能把差异单独归因于7739辆延伸。独立208cohort表以one-share为reference，继承901辆相对它也曾改时。35个在途均属extended_future7739，其中17个已计字段；unfinished trip记录不能定位末端lane或解释堵塞原因。控制及lane先验仍为条件模型，不是现场SPaT。未包括后续服务试验。

原脚本、预检、短prefix原生、完整130/40/52/182/208、compact贡献和独立审计均按原字节gzip或固定旧包SHA保存。独立prefix原文中“379 changed OD-period cells”是措辞误写；保留原文，实际为379辆跨bin、53个数量cell改变。完整instant及两份queues只保留本地大小/SHA，短prefix instant保留gzip；不能迁机重读未打包的完整instant。

离线核验：`.venv\\Scripts\\python.exe benchmarks/hamburg_ring1_cohort_timing_extension_20261024_v1/reproduce.py --mode verify`，不启动SUMO。

显式完整原生复现：使用本项目.venv与记录的SUMO/Libsumo1.27.1，运行`.venv\\Scripts\\python.exe benchmarks/hamburg_ring1_cohort_timing_extension_20261024_v1/reproduce.py --mode native-full --output-dir outputs/reproduce-cohort-timing-extension`。它解压本包及固定旧包的显式8640输入，以当前仓库路径调用core，不生成OD、不依赖历史outputs绝对路径；输出目录必须新建。本次冻结没有额外SUMO。离线核验不等于重新原生复现，复现rejected结果不表示标定完成。
'''


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--freeze', action='store_true')
    mode.add_argument('--verify-only', action='store_true')
    args = parser.parse_args()
    freeze() if args.freeze else verify(HERE)
