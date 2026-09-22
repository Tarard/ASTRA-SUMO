from __future__ import annotations

from pathlib import Path

from torii_sumo.core.sumo_commands import run_minimal_smoke, run_sumo_config


def sumo_run_config(config_path: str, sumo_binary: str = "sumo", timeout_seconds: float = 120.0) -> dict[str, object]:
    return run_sumo_config(
        config_path=Path(config_path),
        sumo_binary=sumo_binary,
        timeout_seconds=timeout_seconds,
    )


def sumo_run_minimal_smoke(
    work_dir: str,
    timeout_seconds: float = 120.0,
    require_real_sumo: bool = True,
) -> dict[str, object]:
    binaries = None
    if not require_real_sumo:
        binaries = {
            "netgenerate": None,
            "randomTrips": None,
            "duarouter": None,
            "sumo": None,
        }
    return run_minimal_smoke(
        work_dir=Path(work_dir),
        binaries=binaries,
        timeout_seconds=timeout_seconds,
    )
