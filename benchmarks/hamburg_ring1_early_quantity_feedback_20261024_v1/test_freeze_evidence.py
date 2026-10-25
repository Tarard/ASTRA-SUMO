"""Offline guards for the two rejected early-quantity responses."""

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import pytest


HERE = Path(__file__).resolve().parent
spec = spec_from_file_location("early_quantity_freeze", HERE / "freeze_evidence.py")
freeze = module_from_spec(spec)
spec.loader.exec_module(freeze)


def test_final_parent_and_actual_populations():
    assert freeze.PARENT_SHA == "9141f590930dec43aa7384ba4fb1c46dac9c631a452d87b660c97407f1a8bdd8"
    assert freeze.RUNS == {"window0": (8604, 10, 356), "window0_remove3": (8594, 0, 448)}


@pytest.mark.skipif(not (HERE / "manifest.json").exists(), reason="Wait for final independent evidence and seal.")
def test_complete_frozen_packet():
    freeze.verify(HERE)
