from __future__ import annotations

from pathlib import Path
from typing import Any, Callable
import shutil
import subprocess
import xml.etree.ElementTree as ET


def _write_minimal_sumocfg(
    *,
    net_file: Path,
    output_dir: Path,
    prefix: str,
    end: int = 1,
) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    cfg_file = output_dir / f"{prefix}.sumocfg"
    configuration = ET.Element("configuration")
    input_node = ET.SubElement(configuration, "input")
    ET.SubElement(input_node, "net-file", {"value": str(net_file)})
    time_node = ET.SubElement(configuration, "time")
    ET.SubElement(time_node, "begin", {"value": "0"})
    ET.SubElement(time_node, "end", {"value": str(end)})
    ET.ElementTree(configuration).write(cfg_file, encoding="utf-8", xml_declaration=True)
    return cfg_file


def launch_sumo_gui(
    net_file: Path,
    *,
    output_dir: Path,
    prefix: str = "sumo_gui",
    sumo_gui_binary: str = "sumo-gui",
    which_func: Callable[[str], str | None] = shutil.which,
    popen_func: Callable[..., Any] = subprocess.Popen,
) -> dict[str, Any]:
    if not net_file.exists():
        return {
            "status": "fail",
            "claim_status": "construction-invalid",
            "sumo_gui_status": "failed",
            "sumo_gui_binary": sumo_gui_binary,
            "sumo_gui_process_id": None,
            "sumo_gui_config_file": "",
            "sumo_gui_network_file": str(net_file),
            "warnings": [f"network file not found: {net_file}"],
        }

    cfg_file = _write_minimal_sumocfg(net_file=net_file, output_dir=output_dir, prefix=prefix)
    resolved_binary = which_func(sumo_gui_binary)
    if resolved_binary is None:
        return {
            "status": "blocked",
            "claim_status": "diagnostic-demo",
            "sumo_gui_status": "unavailable",
            "sumo_gui_binary": None,
            "sumo_gui_process_id": None,
            "sumo_gui_config_file": str(cfg_file),
            "sumo_gui_network_file": str(net_file),
            "warnings": ["sumo-gui binary not found"],
        }

    command = [resolved_binary, "-c", str(cfg_file)]
    try:
        process = popen_func(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError as exc:
        return {
            "status": "fail",
            "claim_status": "diagnostic-demo",
            "sumo_gui_status": "failed",
            "sumo_gui_binary": resolved_binary,
            "sumo_gui_process_id": None,
            "sumo_gui_config_file": str(cfg_file),
            "sumo_gui_network_file": str(net_file),
            "warnings": [f"{type(exc).__name__}: {exc}"],
        }

    return {
        "status": "pass",
        "claim_status": "diagnostic-demo",
        "sumo_gui_status": "opened",
        "sumo_gui_binary": resolved_binary,
        "sumo_gui_process_id": process.pid,
        "sumo_gui_config_file": str(cfg_file),
        "sumo_gui_network_file": str(net_file),
        "warnings": [],
    }
