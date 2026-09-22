from pathlib import Path

from torii_sumo.core.command_runner import CommandResult
from torii_sumo.core.sumo_commands import discover_binaries, run_sumo_load_audit


def test_discover_binaries_prefers_one_sumo_home_toolchain(monkeypatch, tmp_path: Path) -> None:
    sumo_home = tmp_path / "sumo"
    bin_dir = sumo_home / "bin"
    tools_dir = sumo_home / "tools"
    bin_dir.mkdir(parents=True)
    tools_dir.mkdir()
    for name in ("sumo", "netconvert", "netgenerate", "duarouter"):
        (bin_dir / f"{name}.exe").write_text("", encoding="utf-8")
    (tools_dir / "randomTrips.py").write_text("", encoding="utf-8")
    monkeypatch.setenv("SUMO_HOME", str(sumo_home))

    discovered = discover_binaries()

    assert Path(str(discovered["sumo"])).parent == bin_dir
    assert Path(str(discovered["netconvert"])).parent == bin_dir
    assert Path(str(discovered["netgenerate"])).parent == bin_dir
    assert Path(str(discovered["duarouter"])).parent == bin_dir
    assert Path(str(discovered["randomTrips"])).parent == tools_dir


def test_sumo_load_audit_is_read_only_and_hash_bound(tmp_path: Path) -> None:
    net_file = tmp_path / "case.net.xml"
    net_file.write_text("<net/>", encoding="utf-8")
    calls: list[list[str]] = []

    def fake_runner(
        command: list[str],
        *,
        cwd: Path | None = None,
        timeout_seconds: float = 60.0,
    ) -> CommandResult:
        calls.append(command)
        return CommandResult(
            command=command,
            cwd=str(cwd),
            status="pass",
            returncode=0,
        )

    report = run_sumo_load_audit(
        net_file=net_file,
        output_dir=tmp_path / "load",
        sumo_binary="sumo-test",
        command_runner=fake_runner,
    )

    assert report["status"] == "pass"
    assert report["source_network_mutation"] is False
    assert calls[0][0] == "sumo-test"
    assert str(net_file.resolve()) in calls[0]
    assert Path(report["report_file"]).is_file()
    assert Path(report["manifest_file"]).is_file()
