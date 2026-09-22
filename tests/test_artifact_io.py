import os
import tempfile
from pathlib import Path

import pytest

from torii_sumo.core.artifact_io import (
    copy_file_atomic,
    relative_or_absolute_path,
    write_json_atomic,
    write_text_atomic,
)


def test_atomic_text_write_replaces_destination_without_temp_artifacts(tmp_path: Path) -> None:
    destination = tmp_path / "manifest.json"
    destination.write_text("old", encoding="utf-8")

    write_text_atomic(destination, "new")

    assert destination.read_text(encoding="utf-8") == "new"
    assert list(tmp_path.glob(".torii-*.tmp")) == []


def test_json_serialization_failure_preserves_existing_destination(tmp_path: Path) -> None:
    destination = tmp_path / "report.json"
    destination.write_text('{"status":"old"}', encoding="utf-8")

    with pytest.raises(TypeError):
        write_json_atomic(destination, {"invalid": {1, 2, 3}})

    assert destination.read_text(encoding="utf-8") == '{"status":"old"}'
    assert list(tmp_path.glob(".torii-*.tmp")) == []


def test_atomic_file_copy_replaces_destination_without_temp_artifacts(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.net.xml"
    destination = tmp_path / "candidate.net.xml"
    source.write_bytes(b"new-network\x00content")
    destination.write_bytes(b"old-network")

    copy_file_atomic(source, destination)

    assert destination.read_bytes() == source.read_bytes()
    assert list(tmp_path.glob(".torii-*.tmp")) == []


@pytest.mark.parametrize("operation", ["text", "json", "copy"])
def test_atomic_artifact_operations_use_short_target_independent_temp_names(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
) -> None:
    destination = tmp_path / f"descriptive-{operation}-artifact-name.json"
    observed_prefixes: list[str] = []
    real_named_temporary_file = tempfile.NamedTemporaryFile

    def capture_prefix(*args, **kwargs):
        observed_prefixes.append(kwargs["prefix"])
        return real_named_temporary_file(*args, **kwargs)

    monkeypatch.setattr(
        "torii_sumo.core.artifact_io.tempfile.NamedTemporaryFile",
        capture_prefix,
    )

    if operation == "text":
        write_text_atomic(destination, "new-text")
        assert destination.read_text(encoding="utf-8") == "new-text"
    elif operation == "json":
        write_json_atomic(destination, {"status": "new"})
        assert destination.read_text(encoding="utf-8") == '{\n  "status": "new"\n}'
    else:
        source = tmp_path / "source.bin"
        source.write_bytes(b"new-binary-content")
        copy_file_atomic(source, destination)
        assert destination.read_bytes() == b"new-binary-content"

    assert observed_prefixes == [".torii-"]
    assert destination.name not in observed_prefixes[0]
    assert [path for path in tmp_path.iterdir() if path.name.startswith(".")] == []


@pytest.mark.skipif(os.name != "nt", reason="Windows MAX_PATH regression")
@pytest.mark.parametrize("operation", ["text", "json", "copy"])
def test_atomic_artifact_operations_support_destination_near_windows_max_path(
    tmp_path: Path,
    operation: str,
) -> None:
    destination_name = "manifest.json"
    desired_destination_length = 248
    padding_length = (
        desired_destination_length
        - len(str(tmp_path.resolve()))
        - len(destination_name)
        - 2
    )
    if not 1 <= padding_length <= 255:
        pytest.skip("pytest temporary root cannot form a near-MAX_PATH destination")

    destination_dir = tmp_path / ("d" * padding_length)
    destination_dir.mkdir()
    destination = destination_dir / destination_name
    assert len(str(destination)) == desired_destination_length

    if operation == "text":
        write_text_atomic(destination, "new-text")
        assert destination.read_text(encoding="utf-8") == "new-text"
    elif operation == "json":
        write_json_atomic(destination, {"status": "new"})
        assert destination.read_text(encoding="utf-8") == '{\n  "status": "new"\n}'
    else:
        source = tmp_path / "source.bin"
        source.write_bytes(b"new-binary-content")
        copy_file_atomic(source, destination)
        assert destination.read_bytes() == b"new-binary-content"

    assert list(destination_dir.glob(".torii-*.tmp")) == []


def test_relative_path_falls_back_to_absolute_across_windows_drives(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "candidate.net.xml"
    target.write_text("<net/>", encoding="utf-8")

    def cross_drive_failure(*_args, **_kwargs):
        raise ValueError("path is on a different drive")

    monkeypatch.setattr(
        "torii_sumo.core.artifact_io.os.path.relpath",
        cross_drive_failure,
    )

    assert relative_or_absolute_path(target, tmp_path / "output") == (target.resolve().as_posix())
