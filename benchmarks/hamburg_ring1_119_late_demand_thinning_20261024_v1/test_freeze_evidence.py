"""Offline guards for the three completed variable-N responses."""

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import pytest


HERE = Path(__file__).resolve().parent
spec = spec_from_file_location("thinning_freeze", HERE / "freeze_evidence.py")
freeze = module_from_spec(spec)
spec.loader.exec_module(freeze)


def test_parent_uses_final_scope_corrected_seal():
    assert freeze.PARENT_SHA == "ce28186c2dd46ba46041fb3eea9c74991fd9301839cc4e1196621ac2aa4020c9"
    assert freeze.RUNS == {"remove28": 8612, "remove30": 8610, "remove43": 8597}


def test_active_case_prevents_freeze(monkeypatch, tmp_path):
    monkeypatch.setattr(freeze, "SOURCE", tmp_path)
    with pytest.raises(AssertionError, match="three closed verdicts"):
        freeze.require_terminal_cases()


@pytest.mark.skipif(not (HERE / "manifest.json").exists(), reason="Wait for remove43 actual response and both independent seals.")
def test_complete_frozen_packet():
    freeze.verify(HERE)
