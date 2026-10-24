"""Offline parity and manifest refusal; no observer launch."""
import hashlib
import json
from pathlib import Path
import runpy

import pytest


audit = runpy.run_path(str(Path(__file__).with_name('freeze_evidence.py')))


def test_incomplete_parity_and_changed_manifest_bytes_are_rejected(tmp_path):
    result = dict(status='pass', checks={str(i): True for i in range(12)}, closed_e1_rows=208,
        summary_steps_checked=144000, instant_e1_events_checked=154367, arrived_routes_checked=8606)
    audit['parity_gate'](result)
    result['checks']['1'] = False
    with pytest.raises(AssertionError):
        audit['parity_gate'](result)
    p = tmp_path / 'fixed.json'
    p.write_bytes(b'fixed')
    (tmp_path / 'manifest.json').write_text(json.dumps(dict(files=[dict(path=p.name, bytes=5,
        sha256=hashlib.sha256(b'fixed').hexdigest())])), encoding='utf-8')
    audit['verify_manifest'](tmp_path)
    p.write_bytes(b'wrong')
    with pytest.raises(AssertionError):
        audit['verify_manifest'](tmp_path)
