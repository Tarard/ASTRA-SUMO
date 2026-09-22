from __future__ import annotations

import subprocess
import sys
import tomllib
from pathlib import Path

import anyio
import pytest

from astra_sumo import __version__
from astra_sumo.server import _profile_from, create_server


ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("profile", ["default", "netedit", "legacy"])
def test_old_mcp_names_preserve_tool_contracts(profile: str) -> None:
    async def compare() -> None:
        new = await create_server(profile).list_tools()
        old = await create_server(profile, torii_names=True).list_tools()
        assert len(new) == len(old)
        for current, previous in zip(new, old, strict=True):
            expected = current.name.replace("astra.", "torii.", 1).replace("astra_auto_workflow", "torii_auto_workflow")
            assert previous.name == expected
            assert previous.inputSchema == current.inputSchema
            assert previous.outputSchema == current.outputSchema
            assert previous.annotations == current.annotations

    anyio.run(compare)


def test_old_profile_variable_is_a_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ASTRA_MCP_PROFILE", raising=False)
    monkeypatch.setenv("TORII_MCP_PROFILE", "legacy")
    assert _profile_from(None) == "legacy"
    monkeypatch.setenv("ASTRA_MCP_PROFILE", "netedit")
    assert _profile_from(None) == "netedit"
    assert _profile_from("default") == "default"


@pytest.mark.parametrize("command", ["astra", "torii", "astra-netedit", "torii-netedit"])
def test_installed_commands_start(command: str) -> None:
    executable = Path(sys.executable).parent / f"{command}.exe"
    result = subprocess.run([str(executable), "--help"], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr


def test_lock_and_distribution_versions_agree() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    lock = tomllib.loads((ROOT / "uv.lock").read_text(encoding="utf-8"))
    package = next(item for item in lock["package"] if item["name"] == "astra-sumo")
    assert package["version"] == project["version"]
    assert __version__ == project["version"]
