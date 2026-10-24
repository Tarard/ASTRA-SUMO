"""Small offline guards; whole-packet verification awaits all actual responses."""

from importlib.util import module_from_spec, spec_from_file_location
import json
from pathlib import Path

import pytest


HERE = Path(__file__).resolve().parent
spec = spec_from_file_location("late535_freeze", HERE / "freeze_evidence.py")
freeze = module_from_spec(spec)
spec.loader.exec_module(freeze)


def test_five_cases_include_both_extensions():
    assert set(freeze.RUNS) == {"compress29", "minus30", "plus30", "minus60", "minus90"}


def test_active_case_prevents_freezing(monkeypatch, tmp_path):
    monkeypatch.setattr(freeze, "FAMILIES", {name: tmp_path / name for name in freeze.FAMILIES})
    with pytest.raises(AssertionError, match="five terminal verdicts"):
        freeze.require_all_terminal()


def test_closed_invalid_result_is_preserved_without_zero_fill(monkeypatch, tmp_path):
    monkeypatch.setattr(freeze, "FAMILIES", {name: tmp_path / name for name in freeze.FAMILIES})
    for family, relative in freeze.RUNS.values():
        path = freeze.FAMILIES[family] / relative / "verdict.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"status": "invalid"}), encoding="utf-8")
    assert set(freeze.require_all_terminal().values()) == {"invalid"}


@pytest.mark.parametrize("kind", ["existing", "frozen"])
def test_native_reproduction_requires_fresh_unfrozen_output(tmp_path, kind):
    spec = spec_from_file_location("late535_reproduce", HERE / "reproduce.py")
    reproduce = module_from_spec(spec)
    spec.loader.exec_module(reproduce)
    with pytest.raises(AssertionError):
        reproduce.new_output(tmp_path if kind == "existing" else HERE / "new-native")


@pytest.mark.skipif(not (HERE / "manifest.json").exists(), reason="Wait for five actual responses and the final seal.")
def test_complete_frozen_packet():
    freeze.verify(HERE)
