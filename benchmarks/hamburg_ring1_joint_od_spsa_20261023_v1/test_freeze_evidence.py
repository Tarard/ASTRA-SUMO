"""Offline audits only; no native execution and no reading an active study."""
import gzip
import hashlib
import json
from pathlib import Path
import runpy

import numpy as np
import pytest

from astra_sumo.core.hamburg_readme_results import STATIONS
from astra_sumo.core.spsa import calibrate_spsa


HERE = Path(__file__).resolve().parent
AUDIT = runpy.run_path(str(HERE / 'freeze_evidence.py'))


def test_gzip_data_and_frozen_station_definition_are_portable(tmp_path):
    path = tmp_path / 'record.json'
    value = b'{"fixed":true}\r\n'
    path.with_name(path.name + '.gz').write_bytes(gzip.compress(value, mtime=0))
    assert AUDIT['raw'](path) == AUDIT['raw'](path.with_name(path.name + '.gz')) == value
    definition = tmp_path / 'implementation/astra_sumo/core/hamburg_readme_results.py.gz'
    definition.parent.mkdir(parents=True)
    definition.write_bytes(gzip.compress(('STATIONS = ' + repr(STATIONS)).encode('utf-8'), mtime=0))
    assert AUDIT['station_definition'](tmp_path) == STATIONS


def test_terminal_gate_rejects_unfinished_directory_before_reading_run(tmp_path):
    with pytest.raises(AssertionError, match='no terminal'):
        AUDIT['audit_study'](tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_manifest_detects_corrupted_file_before_any_evaluation(tmp_path):
    payload = tmp_path / 'fixed.json'
    payload.write_bytes(b'fixed')
    row = dict(path=payload.name, bytes=5, sha256=hashlib.sha256(b'fixed').hexdigest())
    (tmp_path / 'manifest.json').write_text(json.dumps({'files': [row]}), encoding='utf-8')
    payload.write_bytes(b'wrong')
    with pytest.raises(AssertionError):
        AUDIT['verify_manifest'](tmp_path)


def test_static_support_checks_raw_hash_and_does_not_claim_native_validation(tmp_path):
    directory = tmp_path / 'support-audit'
    directory.mkdir()
    payload = b'fixed static support\r\n'
    source = directory / 'report.json.gz'
    source.write_bytes(gzip.compress(payload, mtime=0))
    manifest = dict(native_runs=0, raw_sources_immutable=True, files=[dict(
        path='report.json', size_bytes=len(payload), sha256=hashlib.sha256(payload).hexdigest())])
    (directory / 'manifest.json').write_text(json.dumps(manifest), encoding='utf-8')
    result = AUDIT['audit_static_support'](tmp_path)
    assert result['native_runs'] == 0 and result['files'] == 1 and 'no native validation' in result['claim']
    source.write_bytes(gzip.compress(payload + b'changed', mtime=0))
    with pytest.raises(AssertionError):
        AUDIT['audit_static_support'](tmp_path)
    source.write_bytes(gzip.compress(payload, mtime=0))
    manifest['native_runs'] = 1
    (directory / 'manifest.json').write_text(json.dumps(manifest), encoding='utf-8')
    with pytest.raises(AssertionError):
        AUDIT['audit_static_support'](tmp_path)


def test_arithmetic_chain_detects_loss_and_next_vector_tampering():
    settings = dict(a=None, initial_step=1, A=0, c=1, alpha=.602, gamma=.101, iterations=1, seed=104)
    result = calibrate_spsa(lambda theta, name: float(np.sum((theta - [2, 4]) ** 2)), [5, 6],
        lower=[0, 0], upper=[np.inf, np.inf], **settings)
    row = result['history'][0]
    delta = np.random.default_rng(104).choice((-1, 1), size=2)
    loss = (row['loss_plus'], row['loss_minus'])
    gain, following = AUDIT['check_update'](row, np.array([5., 6.]), delta, settings, None, loss)
    assert gain == result['a'] and np.array_equal(following, row['next_theta'])
    broken = {**row, 'next_theta': [999, 999]}
    with pytest.raises(AssertionError, match='next_theta'):
        AUDIT['check_update'](broken, np.array([5., 6.]), delta, settings, None, loss)
    with pytest.raises(AssertionError, match='native SSE'):
        AUDIT['check_update'](row, np.array([5., 6.]), delta, settings, None, (loss[0] + 1, loss[1]))


def test_measurement_checks_against_frozen_completed_prior_case(tmp_path):
    # This is an old immutable completed case, not the running joint study.
    repo = HERE.parents[1]
    frozen = repo / 'benchmarks/hamburg_ring1_classical_od_response_20261023_v1'
    if not frozen.exists():
        pytest.skip('Repository frozen prior case required')
    prepared, evidence = tmp_path / 'prepared', tmp_path / 'evidence'
    prepared.mkdir()
    (evidence / 'native').mkdir(parents=True)
    for source in (frozen / 'prepared').glob('*.gz'):
        (prepared / source.stem).write_bytes(gzip.decompress(source.read_bytes()))
    for directory, target in [('evaluation', evidence), ('native', evidence / 'native')]:
        for source in (frozen / 'probe-minus1' / directory).glob('*.gz'):
            (target / source.stem).write_bytes(gzip.decompress(source.read_bytes()))
    model = json.loads((prepared / 'od-model.json').read_bytes())
    report, tables = AUDIT['audit_measurement'](prepared, evidence, evidence, model, STATIONS)
    assert report['main130']['SSE'] == 6898 and report['all182']['TAE'] == 744
    assert [len(tables[k]) for k in ('main130', 'station40', 'initial52', 'all182')] == [130, 40, 52, 182]
    assert report['completion']['waiting'] == 7 and report['completion']['running'] == 14
    path = evidence / 'native/field-comparison.csv'
    value = path.read_text(encoding='utf-8')
    path.write_text(value.replace(',12,-1', ',13,0', 1), encoding='utf-8')
    assert path.read_text(encoding='utf-8') != value
    with pytest.raises(AssertionError):
        AUDIT['audit_measurement'](prepared, evidence, evidence, model, STATIONS)
