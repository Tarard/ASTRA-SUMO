from pathlib import Path
import sys
from typing import Any

from torii_sumo.core import sumo_commands
from torii_sumo.core.sumo_commands import (
    build_sumo_config_command,
    run_minimal_smoke,
)


def test_build_sumo_config_command() -> None:
    command = build_sumo_config_command(Path("scenario.sumocfg"), sumo_binary="sumo")

    assert command == ["sumo", "-c", "scenario.sumocfg"]


def test_run_minimal_smoke_blocks_when_binaries_missing(tmp_path: Path) -> None:
    report = run_minimal_smoke(
        work_dir=tmp_path,
        binaries={
            "netgenerate": None,
            "randomTrips": None,
            "duarouter": None,
            "sumo": None,
        },
    )

    assert report["status"] == "blocked"
    assert report["claim_status"] == "blocked"
    assert "sumo binary not found" in report["warnings"]


def _pass_result(command: list[str], cwd: Path | None) -> dict[str, Any]:
    return {
        "command": command,
        "cwd": str(cwd) if cwd else None,
        "status": "pass",
        "returncode": 0,
        "stdout": "",
        "stderr": "",
        "error": "",
    }


def test_run_minimal_smoke_passes_when_commands_and_outputs_succeed(
    tmp_path: Path,
) -> None:
    calls: list[tuple[list[str], Path | None]] = []

    class FakeResult:
        def __init__(self, command: list[str], cwd: Path | None) -> None:
            self.command = command
            self.cwd = cwd

        def to_dict(self) -> dict[str, Any]:
            return _pass_result(self.command, self.cwd)

    def fake_runner(
        command: list[str],
        *,
        cwd: Path | None = None,
        timeout_seconds: float = 120.0,
    ) -> FakeResult:
        calls.append((command, cwd))
        if command[0] == "netgenerate":
            (tmp_path / "smoke.net.xml").write_text("<net/>", encoding="utf-8")
        elif command[0] == sys.executable:
            (tmp_path / "smoke.trips.xml").write_text("<trips/>", encoding="utf-8")
        elif command[0] == "duarouter":
            (tmp_path / "smoke.rou.xml").write_text("<routes/>", encoding="utf-8")
        elif command[0] == "sumo":
            (tmp_path / "summary.xml").write_text("<summary/>", encoding="utf-8")
            (tmp_path / "tripinfo.xml").write_text("<tripinfos/>", encoding="utf-8")
        return FakeResult(command, cwd)

    report = run_minimal_smoke(
        work_dir=tmp_path,
        binaries={
            "netgenerate": "netgenerate",
            "randomTrips": "randomTrips.py",
            "duarouter": "duarouter",
            "sumo": "sumo",
        },
        command_runner=fake_runner,
    )

    assert report["status"] == "pass"
    assert report["claim_status"] == "diagnostic-demo"
    assert report["summary_path"] is not None
    assert report["tripinfo_path"] is not None
    assert [call[0][0] for call in calls] == [
        "netgenerate",
        sys.executable,
        "duarouter",
        "sumo",
    ]
    assert all(cwd == tmp_path for _, cwd in calls)


def test_run_minimal_smoke_uses_cwd_local_paths_for_relative_work_dir(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    work_dir = Path("runs") / "minimal-smoke"
    artifact_dir = tmp_path / work_dir
    calls: list[tuple[list[str], Path | None]] = []

    class FakeResult:
        def __init__(self, command: list[str], cwd: Path | None) -> None:
            self.command = command
            self.cwd = cwd

        def to_dict(self) -> dict[str, Any]:
            return _pass_result(self.command, self.cwd)

    def fake_runner(
        command: list[str],
        *,
        cwd: Path | None = None,
        timeout_seconds: float = 120.0,
    ) -> FakeResult:
        assert cwd == work_dir
        disallowed_prefixes = (str(work_dir), work_dir.as_posix())
        for arg in command[1:]:
            assert not any(prefix in str(arg) for prefix in disallowed_prefixes)

        calls.append((command, cwd))
        if command[0] == "netgenerate":
            (artifact_dir / "smoke.net.xml").write_text("<net/>", encoding="utf-8")
        elif command[0] == sys.executable:
            (artifact_dir / "smoke.trips.xml").write_text("<trips/>", encoding="utf-8")
        elif command[0] == "duarouter":
            (artifact_dir / "smoke.rou.xml").write_text("<routes/>", encoding="utf-8")
        elif command[0] == "sumo":
            (artifact_dir / "summary.xml").write_text("<summary/>", encoding="utf-8")
            (artifact_dir / "tripinfo.xml").write_text("<tripinfos/>", encoding="utf-8")
        return FakeResult(command, cwd)

    report = run_minimal_smoke(
        work_dir=work_dir,
        binaries={
            "netgenerate": "netgenerate",
            "randomTrips": "randomTrips.py",
            "duarouter": "duarouter",
            "sumo": "sumo",
        },
        command_runner=fake_runner,
    )

    assert report["status"] == "pass"
    assert [command for command, _ in calls] == [
        ["netgenerate", "--grid", "--grid.number", "2", "--output-file", "smoke.net.xml"],
        [
            sys.executable,
            "randomTrips.py",
            "-n",
            "smoke.net.xml",
            "-o",
            "smoke.trips.xml",
            "-e",
            "60",
            "--seed",
            "1",
        ],
        [
            "duarouter",
            "-n",
            "smoke.net.xml",
            "--route-files",
            "smoke.trips.xml",
            "-o",
            "smoke.rou.xml",
        ],
        ["sumo", "-c", "smoke.sumocfg"],
    ]


def test_run_sumo_config_uses_cwd_local_config_name(monkeypatch) -> None:
    calls: list[tuple[list[str], Path | None, float]] = []

    class FakeResult:
        status = "pass"

        def __init__(self, command: list[str], cwd: Path | None) -> None:
            self.command = command
            self.cwd = cwd

        def to_dict(self) -> dict[str, Any]:
            return _pass_result(self.command, self.cwd)

    def fake_run_command(
        command: list[str],
        *,
        cwd: Path | None = None,
        timeout_seconds: float = 60.0,
    ) -> FakeResult:
        calls.append((command, cwd, timeout_seconds))
        return FakeResult(command, cwd)

    monkeypatch.setattr(sumo_commands, "run_command", fake_run_command)

    report = sumo_commands.run_sumo_config(
        config_path=Path("scenario") / "smoke.sumocfg",
        sumo_binary="sumo",
        timeout_seconds=7.0,
    )

    assert calls == [(["sumo", "-c", "smoke.sumocfg"], Path("scenario"), 7.0)]
    assert report["status"] == "pass"
    assert report["claim_status"] == "diagnostic-demo"


def test_run_minimal_smoke_fails_when_commands_pass_but_outputs_missing(
    tmp_path: Path,
) -> None:
    class FakeResult:
        def __init__(self, command: list[str], cwd: Path | None) -> None:
            self.command = command
            self.cwd = cwd

        def to_dict(self) -> dict[str, Any]:
            return _pass_result(self.command, self.cwd)

    def fake_runner(
        command: list[str],
        *,
        cwd: Path | None = None,
        timeout_seconds: float = 120.0,
    ) -> FakeResult:
        return FakeResult(command, cwd)

    report = run_minimal_smoke(
        work_dir=tmp_path,
        binaries={
            "netgenerate": "netgenerate",
            "randomTrips": "randomTrips.py",
            "duarouter": "duarouter",
            "sumo": "sumo",
        },
        command_runner=fake_runner,
    )

    assert report["status"] == "fail"
    assert report["claim_status"] == "construction-invalid"
    assert "minimal smoke missing output artifacts: summary.xml, tripinfo.xml" in report[
        "warnings"
    ]
