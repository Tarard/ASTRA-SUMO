"""Offline contracts for the current simulated-history pair."""

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import pytest


HERE = Path(__file__).resolve().parent
spec = spec_from_file_location("history_pair_freeze", HERE / "freeze_evidence.py")
freeze = module_from_spec(spec)
spec.loader.exec_module(freeze)


def test_current_parent_and_distinct_actual_populations():
    assert freeze.PARENT_SHA == "f1ca7bd2db9a482bc55a6b7bed3653370e5d5b6cf8659b1c8a2fface8088df6c"
    assert freeze.CASES == {"reference": (8589, 275), "warmup9": (8598, 286)}


@pytest.mark.skipif(not (HERE / "manifest.json").exists(), reason="Wait for the sealed pair.")
def test_complete_pair():
    freeze.verify(HERE)
