from __future__ import annotations

import math
import os
import signal
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Mapping

from pydantic import BaseModel


class CommandResult(BaseModel):
    command: list[str]
    cwd: str | None
    status: str
    returncode: int | None
    stdout: str = ""
    stderr: str = ""
    error: str = ""

    def to_dict(self) -> dict[str, object]:
        return self.model_dump(mode="json")


def _is_sumo_command(command: list[str]) -> bool:
    return Path(command[0]).stem.casefold() in {
        "sumo", "sumo-gui", "netconvert", "netgenerate", "duarouter", "jtrrouter",
        "marouter", "dfrouter", "od2trips", "polyconvert", "activitygen",
        "emissionsmap", "emissionsdrivingcycle",
    }


def _fatal_output(paths: tuple[Path, Path]) -> bool:
    for path in paths:
        with path.open("rb") as stream:
            stream.seek(max(0, path.stat().st_size - 8192))
            if any(line.strip() in {b"Quitting (on error).", b"Quitting (on error)"} for line in stream.read().splitlines()):
                return True
    return False


def _terminate_process_tree(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    if os.name == "nt":
        try:
            subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=3, check=False,
                creationflags=subprocess.CREATE_NO_WINDOW)
        except (OSError, subprocess.TimeoutExpired):
            pass
    else:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    if process.poll() is None:
        process.kill()
    process.wait(timeout=3)


def _read_output(path: Path) -> str:
    return path.read_bytes().decode("utf-8", errors="replace").replace("\r\n", "\n").replace("\r", "\n")


def run_command(
    command: list[str],
    *,
    cwd: Path | None = None,
    timeout_seconds: float = 60.0,
    env: Mapping[str, str] | None = None,
) -> CommandResult:
    """Capture UTF-8 output from a finite foreground command without pipe draining."""
    # shortcut: callers must wait for their children; use job tracking before supporting detached launchers.
    if not command:
        return CommandResult(
            command=[],
            cwd=str(cwd) if cwd else None,
            status="fail",
            returncode=None,
            error="command must contain at least one token",
        )

    if not math.isfinite(timeout_seconds) or timeout_seconds < 0:
        return CommandResult(command=command, cwd=str(cwd) if cwd else None, status="fail", returncode=None,
                             error="timeout_seconds must be finite and nonnegative")
    command = list(command)
    deadline = time.monotonic() + timeout_seconds
    monitor_fatal = _is_sumo_command(command)
    temp_parent = Path(tempfile.gettempdir()).resolve()
    try:
        with tempfile.TemporaryDirectory(prefix="astra-command-", dir=temp_parent, ignore_cleanup_errors=True) as directory:
            folder = Path(directory).resolve()
            assert folder.parent == temp_parent
            paths = folder / "stdout.log", folder / "stderr.log"
            fatal_at = None
            timed_out = False
            with paths[0].open("wb") as stdout, paths[1].open("wb") as stderr:
                process = subprocess.Popen(command, cwd=str(cwd) if cwd else None,
                    env=dict(env) if env is not None else None, stdin=subprocess.DEVNULL, stdout=stdout, stderr=stderr,
                    start_new_session=os.name != "nt", creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
                try:
                    while True:
                        now = time.monotonic()
                        if monitor_fatal and fatal_at is None and _fatal_output(paths):
                            fatal_at = now
                        if process.poll() is not None:
                            break
                        timed_out = now >= deadline
                        if timed_out or (fatal_at is not None and now - fatal_at >= 1):
                            _terminate_process_tree(process)
                            break
                        try:
                            process.wait(timeout=min(.05, max(0, deadline - now)))
                        except subprocess.TimeoutExpired:
                            pass
                finally:
                    if process.poll() is None:
                        _terminate_process_tree(process)
            output, errors = (_read_output(path) for path in paths)
            fatal = fatal_at is not None or (monitor_fatal and _fatal_output(paths))
            return CommandResult(command=command, cwd=str(cwd) if cwd else None,
                status="fail" if fatal else "timeout" if timed_out else "pass" if process.returncode == 0 else "fail",
                returncode=None if timed_out and not fatal else process.returncode, stdout=output, stderr=errors,
                error="SUMO reported a fatal error." if fatal else
                      f"command timed out after {timeout_seconds} seconds" if timed_out else
                      f"command exited with return code {process.returncode}" if process.returncode else "")
    except (OSError, subprocess.TimeoutExpired) as exc:
        return CommandResult(
            command=command,
            cwd=str(cwd) if cwd else None,
            status="fail",
            returncode=None,
            error=f"{type(exc).__name__}: {exc}",
        )
