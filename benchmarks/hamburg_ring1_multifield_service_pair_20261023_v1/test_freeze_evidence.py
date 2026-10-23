"""Offline inherited-audit refusal check; no native execution."""
import hashlib
import json
from pathlib import Path
import runpy

import pytest


audit = runpy.run_path(str(Path(__file__).with_name('freeze_evidence.py')))


def test_inherited_manifest_check_rejects_tampering(tmp_path):
    path = tmp_path / 'record.json'
    path.write_bytes(b'fixed')
    (tmp_path / 'manifest.json').write_text(json.dumps(dict(files=[dict(
        path='record.json', bytes=5, sha256=hashlib.sha256(b'fixed').hexdigest())])), encoding='utf-8')
    audit['verify_manifest'](tmp_path)
    path.write_bytes(b'wrong')
    with pytest.raises(AssertionError):
        audit['verify_manifest'](tmp_path)
