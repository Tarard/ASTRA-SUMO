from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import shutil
import tomllib
from types import ModuleType
from typing import Any

import anyio
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugins" / "torii-sumo"
BOOTSTRAP = PLUGIN / "scripts" / "bootstrap_mcp.py"
RUNNER = PLUGIN / "scripts" / "run_torii_sumo.py"
RUNNER_LOCK = Path(f"{RUNNER}.lock")


def _load_bootstrap() -> ModuleType:
    spec = importlib.util.spec_from_file_location("torii_mcp_bootstrap_regression", BOOTSTRAP)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _mcp_server_config(plugin: Path = PLUGIN) -> dict[str, Any]:
    config = json.loads((plugin / ".mcp.json").read_text(encoding="utf-8"))
    return config["mcpServers"]["torii-sumo"]


def _script_metadata(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    start = text.index("# /// script\n") + len("# /// script\n")
    end = text.index("# ///\n", start)
    return tomllib.loads("\n".join(line[2:] for line in text[start:end].splitlines()))


async def _list_tools(plugin: Path) -> list[str]:
    server_config = _mcp_server_config(plugin)
    environment = os.environ.copy()
    environment.pop("TORII_MCP_PROFILE", None)
    parameters = StdioServerParameters(
        command=server_config["command"],
        args=server_config.get("args", []),
        cwd=plugin,
        env=environment,
    )
    with anyio.fail_after(90):
        async with stdio_client(parameters) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                result = await session.list_tools()
                return [tool.name for tool in result.tools]


def test_runner_dependencies_match_the_root_project() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))

    assert _script_metadata(RUNNER)["dependencies"] == project["project"]["dependencies"]


def test_bootstrap_resolves_plugin_local_runner_and_lock() -> None:
    bootstrap = _load_bootstrap()

    assert bootstrap.PLUGIN_ROOT == PLUGIN
    assert bootstrap.RUNNER == RUNNER
    assert bootstrap.RUNNER_LOCK == RUNNER_LOCK
    assert bootstrap.RUNNER_LOCK.is_file()


def test_mcp_config_does_not_launch_with_bare_python() -> None:
    server_config = _mcp_server_config()
    configured_executable = Path(server_config["command"]).stem.casefold()

    assert configured_executable != "python"


def test_bootstrap_uses_the_plugin_local_script_lock() -> None:
    bootstrap = _load_bootstrap()
    command = bootstrap._plugin_start_command()
    assert command is not None
    assert "--frozen" in command
    assert "--script" in command
    assert "--project" not in command

    script_index = command.index("--script")
    assert Path(command[script_index + 1]).resolve() == RUNNER


def test_mcp_config_starts_default_profile_with_exactly_ten_tools() -> None:
    tool_names = anyio.run(_list_tools, PLUGIN)

    assert len(tool_names) == 10
    assert len(set(tool_names)) == 10


def test_cached_plugin_copy_starts_without_repository_parents(tmp_path: Path) -> None:
    cached_plugin = tmp_path / "cache" / "marketplace" / "torii-sumo" / "local"
    shutil.copytree(PLUGIN, cached_plugin, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    assert not (cached_plugin / ".." / "..").resolve().joinpath("pyproject.toml").exists()

    tool_names = anyio.run(_list_tools, cached_plugin)

    assert len(tool_names) == 10
    assert len(set(tool_names)) == 10
