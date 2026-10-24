"""Offline checks for exact offset scope, immutable hashes and native output guards."""
import gzip
import hashlib
from importlib.util import module_from_spec, spec_from_file_location
import json
from pathlib import Path

import pytest

spec = spec_from_file_location('receiving_freeze', Path(__file__).with_name('freeze_evidence.py'))
freeze = module_from_spec(spec)
spec.loader.exec_module(freeze)


@pytest.mark.parametrize('name', ['../outside.json', '/outside.json', 'C:/outside.json', 'C:outside.json', '.'])
def test_manifest_path_rejects_escape(name):
    with pytest.raises(AssertionError):
        freeze.safe_relative(name)


def test_source_manifest_rejects_wrong_hash(tmp_path):
    (tmp_path / 'one.json').write_bytes(b'{}\r\n')
    value = {'files': [{'path': 'one.json', 'bytes': 4, 'sha256': '0'*64}]}
    (tmp_path / 'manifest.json').write_text(json.dumps(value), encoding='utf-8')
    with pytest.raises(AssertionError):
        freeze.manifest_paths(tmp_path)


def test_gzip_snapshot_preserves_raw_crlf(tmp_path):
    raw = '字段\r\n'.encode()
    (tmp_path / 'one.json.gz').write_bytes(gzip.compress(raw, mtime=0))
    value = {'files': [{'path': 'one.json', 'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()}]}
    (tmp_path / 'manifest.json').write_text(json.dumps(value), encoding='utf-8')
    assert freeze.manifest_paths(tmp_path) == [Path('one.json'), Path('manifest.json')]


def test_source_readme_snapshot_does_not_read_generated_packet_readme(tmp_path):
    source_bytes = b'original source README\r\n'
    (tmp_path / 'README.md').write_bytes(b'generated packet README\n')
    relative = freeze.snapshot_relative('README.md')
    target = tmp_path / (relative.as_posix()+'.gz')
    target.parent.mkdir()
    target.write_bytes(gzip.compress(source_bytes, mtime=0))
    assert freeze.raw(tmp_path / relative) == source_bytes


def test_byte_alias_rejects_changed_saved_solver(tmp_path):
    (tmp_path / 'solver.py.gz').write_bytes(gzip.compress(b'changed source\r\n',mtime=0))
    (tmp_path / 'byte-references.json').write_text(json.dumps({'aliases':{'old/live/solver.py':{
        'scope':'packet','target':'solver.py','sha256':hashlib.sha256(b'original source\r\n').hexdigest()}}}),encoding='utf-8')
    with pytest.raises(AssertionError):
        freeze.read(tmp_path,'old/live/solver.py')


def test_byte_alias_preserves_existing_compact_gzip(tmp_path):
    value = gzip.compress(b'{"passages":[]}\r\n',mtime=0)
    (tmp_path/'compact.json.gz.gz').write_bytes(gzip.compress(value,mtime=0))
    (tmp_path/'byte-references.json').write_text(json.dumps({'aliases':{'duplicate.json.gz':{
        'scope':'packet','target':'compact.json.gz','sha256':hashlib.sha256(value).hexdigest()}}}),encoding='utf-8')
    assert freeze.read(tmp_path,'duplicate.json.gz') == value


def test_byte_alias_rejects_repository_escape(tmp_path):
    (tmp_path/'byte-references.json').write_text(json.dumps({'aliases':{'input':{
        'scope':'repository','target':'../outside','sha256':'0'*64}}}),encoding='utf-8')
    with pytest.raises(AssertionError):
        freeze.read(tmp_path,'input')


@pytest.mark.parametrize('kind',['existing','frozen'])
def test_native_reproduction_rejects_existing_or_frozen_output(tmp_path,kind):
    reproduction=module_from_spec(spec_from_file_location('causal_reproducer',Path(__file__).with_name('reproduce.py')))
    reproduction.__spec__.loader.exec_module(reproduction)
    target=tmp_path if kind=='existing' else Path(__file__).parent/'new-native'
    with pytest.raises(AssertionError):
        reproduction.new_output(target)


@pytest.mark.parametrize('location',['project','system'])
def test_native_runtime_checks_actual_module_paths(location):
    reproduction=module_from_spec(spec_from_file_location('offset_reproducer',Path(__file__).with_name('reproduce.py')))
    reproduction.__spec__.loader.exec_module(reproduction)
    folder=reproduction.REPO/'.venv/Lib/site-packages' if location=='project' else reproduction.REPO.parent/'system-SUMO/tools'
    paths={name:str(folder/name/'__init__.py') for name in ('libsumo','traci','sumolib')}
    if location=='system':
        with pytest.raises(AssertionError):
            reproduction.assert_project_module_paths(paths)
    else:
        reproduction.assert_project_module_paths(paths)


@pytest.mark.parametrize('tamper',['future_zero','missing_row'])
def test_prefix117_missing_values_cannot_be_filled_or_dropped(tamper):
    packet=Path(__file__).resolve().parent
    coverage=freeze.jread(packet,'observer4500/native/target182-prefix-measurements.json')
    native=freeze.prior.prior.intervals(freeze.read(packet,'observer4500/native/field-counts.xml'))
    if tamper=='future_zero':
        next(r for r in coverage if r['simulated_count'] is None)['simulated_count']=0
    else:
        coverage.pop()
    with pytest.raises(AssertionError):
        freeze.check_prefix_coverage(coverage,native)


@pytest.mark.parametrize('kind',['instant','counts','wrong_hash'])
def test_local_only_exception_cannot_hide_missing_counts_or_bad_hash(kind):
    name='instant-e1.xml' if kind!='counts' else 'field-counts.xml'
    row=dict(path=name,bytes=12,sha256='a'*64)
    recorded=dict(bytes=12,sha256='b'*64 if kind=='wrong_hash' else 'a'*64)
    if kind=='instant':
        freeze.check_local_trace(row,recorded)
    else:
        with pytest.raises(AssertionError):
            freeze.check_local_trace(row,recorded)
