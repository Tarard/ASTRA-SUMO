"""Offline refusal checks; no simulation starts."""
import hashlib
import json
from pathlib import Path
import runpy

import pytest


audit = runpy.run_path(str(Path(__file__).with_name('freeze_evidence.py')))


def test_manifest_rejects_tampering_and_active_case_is_not_read(tmp_path):
    with pytest.raises(AssertionError, match='terminal verdict'):
        audit['audit_run'](tmp_path / 'substitute-1pair', tmp_path / 'missing-baseline', {}, {})
    path = tmp_path / 'record.json'
    path.write_bytes(b'fixed')
    (tmp_path / 'manifest.json').write_text(json.dumps(dict(files=[dict(
        path=path.name, bytes=5, sha256=hashlib.sha256(b'fixed').hexdigest())])), encoding='utf-8')
    audit['verify_manifest'](tmp_path)
    path.write_bytes(b'wrong')
    with pytest.raises(AssertionError):
        audit['verify_manifest'](tmp_path)
