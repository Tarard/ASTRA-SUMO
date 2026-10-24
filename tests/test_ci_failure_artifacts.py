from pathlib import Path
from types import SimpleNamespace

import pytest

import conftest


@pytest.mark.parametrize("failed,has_directory", [(True, True), (False, True), (True, False)])
def test_failed_test_inputs_are_retained_without_changing_the_source(tmp_path_factory, monkeypatch, failed, has_directory):
    root = tmp_path_factory.mktemp("failure-artifact-hook")
    source = root / "source"
    source.mkdir()
    (source / "native.log").write_text("native diagnostic\n", encoding="utf-8")
    destination = root / "retained"
    monkeypatch.setattr(conftest, "FAILURE_ARTIFACTS", destination)
    report = SimpleNamespace(failed=failed, when="call", sections=[])
    item = SimpleNamespace(funcargs={"tmp_path": source} if has_directory else {}, nodeid="test_native[case]")
    hook = conftest.pytest_runtest_makereport(item, None)
    next(hook)
    with pytest.raises(StopIteration):
        hook.send(SimpleNamespace(get_result=lambda: report))
    assert (source / "native.log").read_text(encoding="utf-8") == "native diagnostic\n"
    if failed and has_directory:
        assert (destination / "source-call/native.log").read_bytes() == (source / "native.log").read_bytes()
        assert (destination / "source-call/pytest-nodeid.txt").read_text(encoding="utf-8") == item.nodeid
    else:
        assert not destination.exists()


def test_artifact_copy_failure_does_not_replace_the_test_failure(monkeypatch):
    def cannot_copy(*args, **kwargs):
        raise OSError("output unavailable")
    monkeypatch.setattr(conftest.shutil, "copytree", cannot_copy)
    report = SimpleNamespace(failed=True, when="call", sections=[])
    item = SimpleNamespace(funcargs={"tmp_path": Path("case")}, nodeid="test_native")
    hook = conftest.pytest_runtest_makereport(item, None)
    next(hook)
    with pytest.raises(StopIteration):
        hook.send(SimpleNamespace(get_result=lambda: report))
    assert report.failed and report.sections == [("Failure artifact copy", "output unavailable")]
