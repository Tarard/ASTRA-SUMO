"""Offline checks for the complete chronological result and prefix boundaries."""

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import pytest


HERE = Path(__file__).resolve().parent
spec = spec_from_file_location("chronological_freeze", HERE / "freeze_evidence.py")
freeze = module_from_spec(spec)
spec.loader.exec_module(freeze)


def test_complete_case_and_independent_binding():
    assert freeze.CASE == "h4_counter_z11_minus5_z5"
    assert freeze.AUDIT_SHA == "9e7e9be3e7f49687b6e3ccaa3641c637cadb1b32a3efbfa12ee78844b44b011d"


@pytest.mark.skipif(not (HERE / "manifest.json").exists(), reason="Wait for the complete frozen packet.")
def test_complete_evidence_and_prefix_nulls():
    freeze.verify(HERE)
