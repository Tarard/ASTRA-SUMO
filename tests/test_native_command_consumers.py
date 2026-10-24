import importlib
from pathlib import Path

import pytest

from astra_sumo.core.command_runner import CommandResult
from astra_sumo.core.junction_teacher_model import _sumo_load_report
from astra_sumo.intersection.clean import build_intersection_ir
from astra_sumo.intersection.compile_plain import compile_intersection_to_plain
from astra_sumo.intersection.validate import validate_intersection


@pytest.mark.parametrize("consumer", ["teacher", "validate", "compile"])
@pytest.mark.parametrize("status,returncode", [("pass", 0), ("fail", 7), ("timeout", None), ("fail", 0)])
def test_native_checks_use_bounded_runner_and_keep_failures(monkeypatch, tmp_path, consumer, status, returncode):
    ir = build_intersection_ir(Path(__file__).parent / "intersection/fixtures/t3_priority.osm.xml", tmp_path)
    modules = {
        "teacher": "astra_sumo.core.junction_teacher_model",
        "validate": "astra_sumo.intersection.validate",
        "compile": "astra_sumo.intersection.compile_plain",
    }
    module = importlib.import_module(modules[consumer])
    seen = []
    error = "command timed out after 30 seconds" if status == "timeout" else "SUMO reported a fatal error." if status == "fail" else ""
    stderr = "partial native diagnostic" if status != "pass" else ""

    def fake_run(command, **kwargs):
        seen.append((command, kwargs))
        return CommandResult(command=command, cwd=str(kwargs.get("cwd")) if kwargs.get("cwd") else None,
                             status=status, returncode=returncode, stdout="native output", stderr=stderr, error=error)

    monkeypatch.setattr(module, "run_command", fake_run)
    if consumer != "teacher":
        monkeypatch.setattr(module.shutil, "which", lambda name: name)
    if consumer == "teacher":
        result = _sumo_load_report(tmp_path / "source.net.xml")
        assert set(result) == {"sumo_returncode", "sumo_stdout", "sumo_stderr"}
        assert (result["sumo_returncode"] == 0) == (status == "pass")
        assert result["sumo_stdout"] == "native output"
        messages = result["sumo_stderr"]
    elif consumer == "validate":
        artifacts = compile_intersection_to_plain(ir, tmp_path, "test", compile_net=False)
        net = tmp_path / "test.net.xml"
        net.write_text("<net/>", encoding="utf-8")
        result = validate_intersection(ir, artifacts.model_copy(update={"net_file": str(net)}), tmp_path)
        assert (result.sumo_load_status == "pass") == (status == "pass")
        if status != "pass":
            assert result.status == "blocked"
        messages = "\n".join(result.warnings)
    else:
        result = compile_intersection_to_plain(ir, tmp_path, "test", compile_net=True)
        assert bool(result.net_file) == (status == "pass")
        messages = "\n".join(result.netconvert_warnings)
    assert len(seen) == 1
    assert seen[0][1]["timeout_seconds"] == (60 if consumer == "teacher" else 30)
    if status != "pass":
        assert error in messages and stderr in messages


def test_teacher_load_missing_executable_keeps_diagnostic(monkeypatch, tmp_path):
    from astra_sumo.core import junction_teacher_model

    monkeypatch.setattr(junction_teacher_model, "run_command", lambda command, **kwargs: CommandResult(
        command=command, cwd=None, status="fail", returncode=None, error="FileNotFoundError: sumo not found"))
    result = _sumo_load_report(tmp_path / "source.net.xml")
    assert result["sumo_returncode"] == -1
    assert "sumo not found" in result["sumo_stderr"]
