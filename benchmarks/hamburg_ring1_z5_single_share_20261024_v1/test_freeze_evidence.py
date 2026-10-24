"""Offline rejection checks for the compact one-share evidence packet."""
import gzip
import hashlib
from importlib.util import module_from_spec, spec_from_file_location
import json
from pathlib import Path
import xml.etree.ElementTree as ET

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


@pytest.mark.parametrize('tamper',['extra_departure','wrong_donor_stop','wrong_field'])
def test_one_share_scope_rejects_extra_input_changes(tmp_path,tamper):
    packet=Path(__file__).resolve().parent
    root=ET.fromstring(freeze.read(packet,'one-share/prepared/demand.rou.xml'))
    metadata=json.loads(freeze.read(packet,'one-share/prepared/lane-intentions.json'))
    vehicles={v.get('id'):v for v in root.findall('vehicle')}
    if tamper=='extra_departure':
        vehicles[freeze.WITNESS].set('depart','8330.0')
    elif tamper=='wrong_donor_stop':
        vehicles[freeze.DONOR].find('stop').set('lane','24483344#1_0')
    else:
        next(r for r in metadata['vehicles'] if r['vehicle_id']==freeze.DONOR)['field_id']=freeze.Z4
    prepared=tmp_path/'one-share/prepared'
    prepared.mkdir(parents=True)
    (prepared/'demand.rou.xml').write_bytes(ET.tostring(root,encoding='utf-8'))
    (prepared/'lane-intentions.json').write_text(json.dumps(metadata),encoding='utf-8')
    with pytest.raises(AssertionError):
        freeze.audit_scope(tmp_path)
