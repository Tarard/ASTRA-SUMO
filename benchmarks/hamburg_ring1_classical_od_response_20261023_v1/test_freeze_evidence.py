"""Small offline checks of evidence rejection; no SUMO calls."""

import importlib.util
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location("freeze_evidence", Path(__file__).with_name("freeze_evidence.py"))
freeze = importlib.util.module_from_spec(spec)
spec.loader.exec_module(freeze)


def test_duplicate_native_intervals_are_rejected(tmp_path):
    path = tmp_path / "counts.xml"
    interval = '<interval id="f" begin="0" end="900" nVehContrib="3"/>'
    path.write_text("<detector>" + interval * 2 + "</detector>", encoding="utf-8")
    with pytest.raises(AssertionError, match="duplicate native E1"):
        freeze.periodic_counts(path)


def test_missing_native_rows_cannot_become_zero():
    expected = [{"detector_id": "f", "begin": "0", "end": "900", "expected_total": "0"}]
    comparison = [{**expected[0], "measured_nVehContrib": "0", "residual": "0"}]
    with pytest.raises(AssertionError, match="missing native E1"):
        freeze.check_comparisons(comparison, expected, {})


def test_changed_reported_residual_is_rejected():
    expected = [{"detector_id": "f", "begin": "0", "end": "900", "expected_total": "2"}]
    comparison = [{**expected[0], "measured_nVehContrib": "3", "residual": "0"}]
    with pytest.raises(AssertionError, match="residual differs"):
        freeze.check_comparisons(comparison, expected, {("f", 0.0, 900.0): 3})
