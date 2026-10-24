"""Offline manifest refusal; never invoke conversion or simulation."""
import hashlib
import json
from pathlib import Path
import runpy

import pytest


audit = runpy.run_path(str(Path(__file__).with_name('freeze_evidence.py')))


def test_inherited_manifest_verifier_rejects_corrupted_bytes(tmp_path):
    path = tmp_path / 'frozen.json'
    path.write_bytes(b'fixed')
    (tmp_path / 'manifest.json').write_text(json.dumps(dict(files=[dict(path=path.name, bytes=5,
        sha256=hashlib.sha256(b'fixed').hexdigest())])), encoding='utf-8')
    audit['verify_manifest'](tmp_path)
    path.write_bytes(b'wrong')
    with pytest.raises(AssertionError):
        audit['verify_manifest'](tmp_path)
