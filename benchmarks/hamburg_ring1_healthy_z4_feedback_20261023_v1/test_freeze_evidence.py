"""Offline rejection checks for the compact GS266 evidence packet."""
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
