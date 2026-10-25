"""Offline archive contracts; full measurements are read only after terminal seals."""

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import pytest


HERE = Path(__file__).resolve().parent
spec = spec_from_file_location("fanout_freeze", HERE / "freeze_evidence.py")
freeze = module_from_spec(spec)
spec.loader.exec_module(freeze)


def test_two_distinct_parent_demands():
    assert {k: v[3:] for k, v in freeze.PARENTS.items()} == {"retained8597": (8597, 324), "restored8640": (8640, 365)}


def test_no_terminal_record_prevents_reading_native(monkeypatch, tmp_path):
    monkeypatch.setattr(freeze, "SOURCE", tmp_path)
    with pytest.raises(AssertionError, match="both terminal verdicts"):
        freeze.require_terminal()


@pytest.mark.skipif(not (HERE / "manifest.json").exists(), reason="Wait for both final audit and response seals.")
def test_complete_packet():
    freeze.verify(HERE)
