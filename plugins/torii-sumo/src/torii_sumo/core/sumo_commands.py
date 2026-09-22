from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path
from typing import Any, Protocol

from .command_runner import run_command
from .artifact_io import write_json_atomic
from .candidate_contracts import file_sha256


class CommandRunner(Protocol):
    def __call__(
        self,
        command: list[str],
        *,
        cwd: Path | None = None,
        timeout_seconds: float = 60.0,
    ) -> Any: ...


def build_sumo_config_command(config_path: Path, *, sumo_binary: str = "sumo") -> list[str]:
    return [sumo_binary, "-c", str(config_path)]


def run_sumo_config(
    *,
    config_path: Path,
    sumo_binary: str = "sumo",
    timeout_seconds: float = 120.0,
) -> dict[str, Any]:
    result = run_command(
        build_sumo_config_command(Path(config_path.name), sumo_binary=sumo_binary),
        cwd=config_path.parent,
        timeout_seconds=timeout_seconds,
    )
    payload = result.to_dict()
    payload["claim_status"] = (
        "diagnostic-demo" if result.status == "pass" else "construction-invalid"
    )
    return payload


def run_sumo_load_audit(
    *,
    net_file: Path,
    output_dir: Path,
    sumo_binary: str = "sumo",
    timeout_seconds: float = 120.0,
    command_runner: CommandRunner = run_command,
) -> dict[str, Any]:
    """Load one immutable network in SUMO without normalizing or repairing it."""

    source = net_file.resolve()
    destination = output_dir.resolve()
    destination.mkdir(parents=True, exist_ok=True)
    report_file = destination / "sumo-load-audit.json"
    manifest_file = destination / "sumo-load-audit.manifest.json"
    if not source.is_file():
        report = {
            "schema": "torii.sumo-load-audit/v1",
            "status": "fail",
            "source_net_file": str(source),
            "error": "network file does not exist",
            "source_network_mutation": False,
        }
    else:
        source_sha256 = file_sha256(source)
        command = [
            sumo_binary,
            "-n",
            str(source),
            "--no-step-log",
            "true",
            "--duration-log.disable",
            "true",
            "--begin",
            "0",
            "--end",
            "1",
        ]
        try:
            result = command_runner(
                command,
                cwd=destination,
                timeout_seconds=timeout_seconds,
            )
            command_report = (
                result.to_dict() if hasattr(result, "to_dict") else dict(result)
            )
        except (OSError, RuntimeError, TimeoutError, TypeError, ValueError) as exc:
            command_report = {
                "status": "fail",
                "returncode": None,
                "error": f"{type(exc).__name__}: {exc}",
            }
        source_immutable = file_sha256(source) == source_sha256
        status = (
            "pass"
            if command_report.get("status") == "pass"
            and command_report.get("returncode") == 0
            and source_immutable
            else "fail"
        )
        report = {
            "schema": "torii.sumo-load-audit/v1",
            "status": status,
            "source_net_file": str(source),
            "source_sha256": source_sha256,
            "source_network_mutation": not source_immutable,
            "command": command,
            "command_result": command_report,
        }
    write_json_atomic(report_file, report, sort_keys=True)
    artifacts = [{"path": str(report_file), "sha256": file_sha256(report_file)}]
    if source.is_file():
        artifacts.insert(0, {"path": str(source), "sha256": file_sha256(source)})
    write_json_atomic(
        manifest_file,
        {
            "schema": "torii.sumo-load-audit-manifest/v1",
            "status": report["status"],
            "source_network_mutation": report["source_network_mutation"],
            "artifacts": artifacts,
        },
        sort_keys=True,
    )
    return {
        **report,
        "report_file": str(report_file),
        "manifest_file": str(manifest_file),
    }


def discover_binaries() -> dict[str, str | None]:
    sumo_home_value = os.environ.get("SUMO_HOME", "").strip()
    sumo_home = Path(sumo_home_value).resolve() if sumo_home_value else None
    random_trips = None
    if sumo_home:
        candidate = sumo_home / "tools" / "randomTrips.py"
        if candidate.exists():
            random_trips = str(candidate)
    return {
        "netgenerate": _sumo_home_binary(sumo_home, "netgenerate") or shutil.which("netgenerate"),
        "netconvert": _sumo_home_binary(sumo_home, "netconvert") or shutil.which("netconvert"),
        "randomTrips": random_trips,
        "duarouter": _sumo_home_binary(sumo_home, "duarouter") or shutil.which("duarouter"),
        "sumo": _sumo_home_binary(sumo_home, "sumo") or shutil.which("sumo"),
    }


def _sumo_home_binary(sumo_home: Path | None, name: str) -> str | None:
    if sumo_home is None:
        return None
    for candidate in (sumo_home / "bin" / name, sumo_home / "bin" / f"{name}.exe"):
        if candidate.is_file():
            return str(candidate)
    return None


def run_minimal_smoke(
    *,
    work_dir: Path,
    binaries: dict[str, str | None] | None = None,
    timeout_seconds: float = 120.0,
    command_runner: CommandRunner = run_command,
) -> dict[str, Any]:
    selected = binaries or discover_binaries()
    warnings: list[str] = []
    for name in ("netgenerate", "randomTrips", "duarouter", "sumo"):
        if not selected.get(name):
            if name == "randomTrips":
                warnings.append("randomTrips.py script not found under SUMO_HOME/tools")
            else:
                warnings.append(f"{name} binary not found")
    if warnings:
        return {
            "status": "blocked",
            "claim_status": "blocked",
            "work_dir": str(work_dir),
            "warnings": warnings,
            "commands": [],
            "artifacts": [],
        }

    work_dir.mkdir(parents=True, exist_ok=True)
    net_path = work_dir / "smoke.net.xml"
    trips_path = work_dir / "smoke.trips.xml"
    route_path = work_dir / "smoke.rou.xml"
    config_path = work_dir / "smoke.sumocfg"
    summary_path = work_dir / "summary.xml"
    tripinfo_path = work_dir / "tripinfo.xml"

    config_path.write_text(
        f"""<configuration>
  <input>
    <net-file value="{net_path.name}"/>
    <route-files value="{route_path.name}"/>
  </input>
  <time>
    <begin value="0"/>
    <end value="60"/>
  </time>
  <output>
    <summary-output value="{summary_path.name}"/>
    <tripinfo-output value="{tripinfo_path.name}"/>
  </output>
</configuration>
""",
        encoding="utf-8",
    )

    commands = [
        [
            selected["netgenerate"],
            "--grid",
            "--grid.number",
            "2",
            "--output-file",
            net_path.name,
        ],
        [
            sys.executable,
            selected["randomTrips"],
            "-n",
            net_path.name,
            "-o",
            trips_path.name,
            "-e",
            "60",
            "--seed",
            "1",
        ],
        [
            selected["duarouter"],
            "-n",
            net_path.name,
            "--route-files",
            trips_path.name,
            "-o",
            route_path.name,
        ],
        [selected["sumo"], "-c", config_path.name],
    ]
    results = [
        command_runner(command, cwd=work_dir, timeout_seconds=timeout_seconds).to_dict()
        for command in commands
    ]
    missing_outputs = [
        path.name for path in (summary_path, tripinfo_path) if not path.exists()
    ]
    warnings = []
    if missing_outputs:
        warnings.append(
            f"minimal smoke missing output artifacts: {', '.join(missing_outputs)}"
        )
    status = (
        "pass"
        if all(result["status"] == "pass" for result in results) and not missing_outputs
        else "fail"
    )
    artifacts = [
        str(path)
        for path in (
            net_path,
            trips_path,
            route_path,
            config_path,
            summary_path,
            tripinfo_path,
        )
        if path.exists()
    ]
    return {
        "status": status,
        "claim_status": "diagnostic-demo" if status == "pass" else "construction-invalid",
        "work_dir": str(work_dir),
        "warnings": warnings,
        "commands": results,
        "artifacts": artifacts,
        "summary_path": str(summary_path) if summary_path.exists() else None,
        "tripinfo_path": str(tripinfo_path) if tripinfo_path.exists() else None,
    }
