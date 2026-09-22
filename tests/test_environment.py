from pathlib import Path

from torii_sumo.core.environment import collect_environment_report


def test_collect_environment_report_uses_runner_and_reports_tools(tmp_path: Path) -> None:
    calls: list[list[str]] = []

    def fake_which(name: str) -> str | None:
        if name in {"sumo", "netconvert", "netgenerate", "duarouter"}:
            return f"C:/SUMO/bin/{name}.exe"
        return None

    def fake_runner(command: list[str], timeout_seconds: float = 10.0):
        calls.append(command)
        return {
            "command": command,
            "status": "pass",
            "returncode": 0,
            "stdout": f"{command[0]} version 1.20.0",
            "stderr": "",
            "error": "",
        }

    def fake_package_finder(name: str) -> object | None:
        if name in {"traci", "sumolib"}:
            return object()
        return None

    report = collect_environment_report(
        which_func=fake_which,
        version_runner=fake_runner,
        package_finder=fake_package_finder,
    )

    assert report.status == "pass"
    assert report.sumo_binary == "C:/SUMO/bin/sumo.exe"
    assert report.netconvert_binary == "C:/SUMO/bin/netconvert.exe"
    assert report.netgenerate_binary == "C:/SUMO/bin/netgenerate.exe"
    assert report.duarouter_binary == "C:/SUMO/bin/duarouter.exe"
    assert ["C:/SUMO/bin/sumo.exe", "--version"] in calls


def test_collect_environment_report_blocks_when_sumo_missing() -> None:
    report = collect_environment_report(which_func=lambda name: None)

    assert report.status == "blocked"
    assert "sumo binary not found" in report.warnings


def test_collect_environment_report_blocks_when_python_packages_missing() -> None:
    def fake_which(name: str) -> str | None:
        if name in {"sumo", "netconvert", "netgenerate", "duarouter"}:
            return f"C:/SUMO/bin/{name}.exe"
        return None

    def fake_runner(command: list[str], timeout_seconds: float = 10.0):
        return {
            "command": command,
            "status": "pass",
            "returncode": 0,
            "stdout": f"{command[0]} version 1.20.0",
            "stderr": "",
            "error": "",
        }

    report = collect_environment_report(
        which_func=fake_which,
        version_runner=fake_runner,
        package_finder=lambda name: None,
    )

    assert report.status == "blocked"
    assert report.traci_available is False
    assert report.sumolib_available is False
    assert "traci Python package not importable" in report.warnings
    assert "sumolib Python package not importable" in report.warnings
