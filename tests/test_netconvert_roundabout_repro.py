from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "roundabout_repro", ROOT / "benchmarks/netconvert_roundabout_repro_v1/reproduce.py"
)
assert SPEC and SPEC.loader
REPRO = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(REPRO)


def test_comparison_ignores_serialization_but_keeps_grouping_and_lane_changes(tmp_path: Path) -> None:
    first = tmp_path / "first.xml"
    second = tmp_path / "second.xml"
    first.write_text('<net><!-- one --><edge id="a"><lane length="10"/></edge>'
                     '<roundabout nodes="1 2" edges="a b"/></net>', encoding="utf-8")
    second.write_text('<net><!-- two --><roundabout edges="b a" nodes="2 1"/>\n'
                      '<edge id="a"><lane length="10"/></edge></net>', encoding="utf-8")
    assert REPRO.semantic_snapshot(first) == REPRO.semantic_snapshot(second)
    second.write_text('<net><edge id="a"><lane length="10"/></edge>'
                      '<roundabout nodes="1" edges="a"/>'
                      '<roundabout nodes="2" edges="b"/></net>', encoding="utf-8")
    before, split = REPRO.semantic_snapshot(first), REPRO.semantic_snapshot(second)
    assert before["roundabouts"] != split["roundabouts"]
    assert before["other_elements"] == split["other_elements"]
    second.write_text(second.read_text().replace('length="10"', 'length="11"'), encoding="utf-8")
    assert REPRO.semantic_snapshot(second)["other_elements"] != split["other_elements"]


@pytest.mark.parametrize("failed", [False, True])
def test_fixed_command_preserves_inputs_and_retains_failed_processes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failed: bool,
) -> None:
    binary, source = tmp_path / "netconvert.exe", tmp_path / "source.xml"
    binary.write_bytes(b"binary identity")
    source.write_bytes(b"<osm/>")
    commands = []

    def run(command: list[str], **kwargs) -> subprocess.CompletedProcess:
        if command[-1] == "--version":
            return subprocess.CompletedProcess(command, 0, "test version", "")
        commands.append(command)
        output = Path(command[command.index("--output-file") + 1])
        group = '<roundabout edges="a b" nodes="1 2"/>' if len(commands) == 1 else (
            '<roundabout edges="a" nodes="1"/><roundabout edges="b" nodes="2"/>'
        )
        output.write_text(f'<net><edge id="a"/>{group}</net>', encoding="utf-8")
        return subprocess.CompletedProcess(command, int(failed and len(commands) == 2), "", "log")

    monkeypatch.setattr(REPRO.subprocess, "run", run)
    output = tmp_path / "results"
    report = REPRO.reproduce(binary, source, output, 2)
    assert commands[0] == commands[1]
    assert report["inputs_unchanged"]
    assert source.read_bytes() == b"<osm/>"
    assert report["status"] == ("failed" if failed else "reproduced")
    assert json.loads((output / "report.json").read_text()) == report
    assert (output / "run-001.net.xml").exists() is not failed
    assert (output / "run-001.log").read_text() == "log"
    with pytest.raises(FileExistsError):
        REPRO.reproduce(binary, source, output, 2)


def test_reproduction_rejects_single_process(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="at least two"):
        REPRO.reproduce(tmp_path / "binary", tmp_path / "source", tmp_path / "out", 1)
