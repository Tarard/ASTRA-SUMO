from __future__ import annotations

import subprocess
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


def run_command(
    command: list[str],
    *,
    cwd: Path | None = None,
    timeout_seconds: float = 60.0,
    env: Mapping[str, str] | None = None,
) -> CommandResult:
    if not command:
        return CommandResult(
            command=[],
            cwd=str(cwd) if cwd else None,
            status="fail",
            returncode=None,
            error="command must contain at least one token",
        )

    try:
        completed = subprocess.run(
            command,
            cwd=str(cwd) if cwd else None,
            env=dict(env) if env is not None else None,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        return CommandResult(
            command=command,
            cwd=str(cwd) if cwd else None,
            status="timeout",
            returncode=None,
            stdout=exc.stdout or "",
            stderr=exc.stderr or "",
            error=f"command timed out after {timeout_seconds} seconds",
        )
    except OSError as exc:
        return CommandResult(
            command=command,
            cwd=str(cwd) if cwd else None,
            status="fail",
            returncode=None,
            error=f"{type(exc).__name__}: {exc}",
        )

    return CommandResult(
        command=command,
        cwd=str(cwd) if cwd else None,
        status="pass" if completed.returncode == 0 else "fail",
        returncode=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
    )
