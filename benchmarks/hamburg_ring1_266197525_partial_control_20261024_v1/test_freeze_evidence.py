"""Small offline guards; the complete packet test waits for the actual seal."""

import gzip
import json
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import pytest


HERE = Path(__file__).resolve().parent
spec = spec_from_file_location("partial_control_freeze", HERE / "freeze_evidence.py")
freeze = module_from_spec(spec)
spec.loader.exec_module(freeze)


def test_unrun_family_is_not_a_completed_measurement(tmp_path):
    with pytest.raises(AssertionError, match="completed full verdict"):
        freeze.require_terminal(tmp_path)


def test_original_crlf_bytes_survive_gzip(tmp_path):
    original = "原始记录\r\n".encode()
    (tmp_path / "source.txt.gz").write_bytes(gzip.compress(original, mtime=0))
    assert freeze.raw(tmp_path / "source.txt") == original


@pytest.mark.parametrize("kind", ["invalid", "unreviewed"])
def test_incomplete_or_unreviewed_full_is_not_frozen(tmp_path, kind):
    (tmp_path / "full").mkdir()
    (tmp_path / "full/verdict.json").write_text(json.dumps({"status": "invalid" if kind == "invalid" else "diagnostic_measurement_complete"}), encoding="utf-8")
    with pytest.raises(AssertionError):
        freeze.require_terminal(tmp_path)


@pytest.mark.parametrize("kind", ["existing", "frozen"])
def test_native_requires_fresh_output_outside_frozen_packet(tmp_path, kind):
    spec = spec_from_file_location("partial_reproducer", HERE / "reproduce.py")
    reproduce = module_from_spec(spec)
    spec.loader.exec_module(reproduce)
    with pytest.raises(AssertionError):
        reproduce.new_output(tmp_path if kind == "existing" else HERE / "new-native")


@pytest.mark.skipif(not (HERE / "manifest.json").exists(), reason="The actual full result has not been frozen.")
def test_complete_frozen_packet():
    freeze.verify(HERE)
