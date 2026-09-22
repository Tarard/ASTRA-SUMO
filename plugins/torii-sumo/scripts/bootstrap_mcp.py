"""Bootstrap the Torii MCP stdio server from the self-contained plugin."""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
from pathlib import Path


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
RUNNER = PLUGIN_ROOT / "scripts" / "run_torii_sumo.py"
RUNNER_LOCK = Path(f"{RUNNER}.lock")


def _plugin_start_command() -> list[str] | None:
    if not RUNNER.is_file() or not RUNNER_LOCK.is_file():
        return None
    uv = shutil.which("uv")
    if not uv:
        return None
    return [
        uv,
        "run",
        "--isolated",
        "--frozen",
        "--script",
        str(RUNNER),
    ]


def _check() -> int:
    command = _plugin_start_command()
    if command is None:
        print("torii MCP bootstrap failed: uv, the plugin runner, and its lock file are required")
        return 2
    print("torii MCP bootstrap: " + subprocess.list2cmdline(command))
    return 0


def _start() -> int:
    command = _plugin_start_command()
    if command is None:
        return _check()
    env = os.environ.copy()
    env.setdefault("TORII_MCP_PROFILE", "default")
    return subprocess.call(command, cwd=PLUGIN_ROOT, env=env)


def main() -> int:
    parser = argparse.ArgumentParser(description="Bootstrap the Torii MCP server.")
    parser.add_argument(
        "--check",
        action="store_true",
        help="Validate the bootstrap path without starting the MCP server.",
    )
    args = parser.parse_args()
    return _check() if args.check else _start()


if __name__ == "__main__":
    raise SystemExit(main())
