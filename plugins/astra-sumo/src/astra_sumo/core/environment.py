from __future__ import annotations

import os
import platform
import shutil
import sys
from collections.abc import Callable
from importlib.util import find_spec
from typing import Any

from pydantic import BaseModel

from .command_runner import run_command


class EnvironmentReport(BaseModel):
    status: str
    python_version: str
    platform: str
    sumo_home: str | None
    sumo_binary: str | None
    netconvert_binary: str | None = None
    netgenerate_binary: str | None
    duarouter_binary: str | None
    traci_available: bool
    sumolib_available: bool
    versions: dict[str, dict[str, Any]]
    warnings: list[str]
    claim_status: str

    def to_dict(self) -> dict[str, object]:
        return self.model_dump(mode="json")


def _default_version_runner(command: list[str], timeout_seconds: float = 10.0) -> dict[str, Any]:
    return run_command(command, timeout_seconds=timeout_seconds).to_dict()


def collect_environment_report(
    *,
    which_func: Callable[[str], str | None] = shutil.which,
    version_runner: Callable[[list[str], float], dict[str, Any]] = _default_version_runner,
    package_finder: Callable[[str], object | None] = find_spec,
) -> EnvironmentReport:
    sumo_binary = which_func("sumo")
    netconvert_binary = which_func("netconvert")
    netgenerate_binary = which_func("netgenerate")
    duarouter_binary = which_func("duarouter")

    warnings: list[str] = []
    if sumo_binary is None:
        warnings.append("sumo binary not found")
    if netconvert_binary is None:
        warnings.append("netconvert binary not found")
    if netgenerate_binary is None:
        warnings.append("netgenerate binary not found")
    if duarouter_binary is None:
        warnings.append("duarouter binary not found")

    versions: dict[str, dict[str, Any]] = {}
    for name, binary in {
        "sumo": sumo_binary,
        "netconvert": netconvert_binary,
        "netgenerate": netgenerate_binary,
        "duarouter": duarouter_binary,
    }.items():
        if binary:
            versions[name] = version_runner([binary, "--version"], 10.0)
            if versions[name].get("status") != "pass" or versions[name].get("returncode") != 0:
                warnings.append(f"{name} version command failed")

    traci_available = package_finder("traci") is not None
    sumolib_available = package_finder("sumolib") is not None
    if not traci_available:
        warnings.append("traci Python package not importable")
    if not sumolib_available:
        warnings.append("sumolib Python package not importable")

    status = "pass" if not warnings else "blocked"
    return EnvironmentReport(
        status=status,
        python_version=sys.version,
        platform=platform.platform(),
        sumo_home=os.environ.get("SUMO_HOME"),
        sumo_binary=sumo_binary,
        netconvert_binary=netconvert_binary,
        netgenerate_binary=netgenerate_binary,
        duarouter_binary=duarouter_binary,
        traci_available=traci_available,
        sumolib_available=sumolib_available,
        versions=versions,
        warnings=warnings,
        claim_status="construction-check" if status == "pass" else "blocked",
    )
