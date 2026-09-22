from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping, Protocol
from xml.etree import ElementTree as ET

from torii_sumo.evidence.output_inspection import inspect_run_outputs

from .artifact_io import write_json_atomic, write_text_atomic
from .candidate_contracts import file_sha256
from .command_runner import run_command


class CommandRunner(Protocol):
    def __call__(
        self,
        command: list[str],
        *,
        cwd: Path | None = None,
        timeout_seconds: float = 60.0,
    ) -> Any: ...


def run_bound_cell_movement_smoke(
    *,
    net_file: Path,
    movement_binding: Mapping[str, Any],
    output_dir: Path,
    sumo_binary: str,
    departure_interval_s: int = 8,
    end_time_s: int = 900,
    disable_traffic_lights: bool = False,
    timeout_seconds: float = 120.0,
    command_runner: CommandRunner = run_command,
) -> dict[str, Any]:
    """Run one vehicle over every hash-bound cell movement lane path."""

    source = net_file.resolve(strict=True)
    destination = output_dir.resolve()
    destination.mkdir(parents=True, exist_ok=True)
    route_file = destination / "cell-movements.rou.xml"
    config_file = destination / "cell-movements.sumocfg"
    summary_file = destination / "summary.xml"
    tripinfo_file = destination / "tripinfo.xml"
    vehroute_file = destination / "vehroute.xml"
    report_file = destination / "cell-movements.report.json"
    manifest_file = destination / "cell-movements.manifest.json"
    source_sha256 = file_sha256(source)
    movements = list(movement_binding.get("movement_records", ()))

    route_root = ET.Element("routes")
    ET.SubElement(
        route_root,
        "vType",
        id="cell_smoke_passenger",
        vClass="passenger",
        accel="2.6",
        decel="4.5",
        sigma="0",
        length="5",
        maxSpeed="13.9",
    )
    expected_vehicle_ids = []
    route_construction_findings = []
    for index, movement in enumerate(movements):
        vehicle_id = f"cell_{index:02d}_{movement['stable_movement_id']}"
        edge_ids = list(map(str, movement.get("edge_ids", ())))
        if len(edge_ids) < 2:
            route_construction_findings.append(
                {
                    "stable_movement_id": movement.get("stable_movement_id"),
                    "reason": "movement_path_has_fewer_than_two_external_edges",
                }
            )
            continue
        expected_vehicle_ids.append(vehicle_id)
        vehicle = ET.SubElement(
            route_root,
            "vehicle",
            id=vehicle_id,
            type="cell_smoke_passenger",
            depart=str(index * departure_interval_s),
            departLane=str(movement["from_lane_index"]),
            arrivalLane=str(movement["to_lane_index"]),
        )
        ET.SubElement(vehicle, "route", edges=" ".join(edge_ids))
    _write_xml(route_file, route_root)

    config_root = ET.Element("configuration")
    inputs = ET.SubElement(config_root, "input")
    ET.SubElement(inputs, "net-file", value=str(source))
    ET.SubElement(inputs, "route-files", value=route_file.name)
    time = ET.SubElement(config_root, "time")
    ET.SubElement(time, "begin", value="0")
    ET.SubElement(time, "end", value=str(end_time_s))
    output = ET.SubElement(config_root, "output")
    ET.SubElement(output, "summary-output", value=summary_file.name)
    ET.SubElement(output, "tripinfo-output", value=tripinfo_file.name)
    ET.SubElement(output, "vehroute-output", value=vehroute_file.name)
    _write_xml(config_file, config_root)

    cleanup_errors = []
    for stale in (summary_file, tripinfo_file, vehroute_file):
        try:
            stale.unlink(missing_ok=True)
        except OSError as exc:
            cleanup_errors.append(f"{stale.name}:{type(exc).__name__}:{exc}")
    command = [
        sumo_binary,
        "-c",
        config_file.name,
        "--collision.check-junctions",
        "true",
        "--duration-log.statistics",
        "--quit-on-end",
    ]
    if disable_traffic_lights:
        command.extend(["--tls.all-off", "true"])
    if cleanup_errors or route_construction_findings:
        command_result: Mapping[str, Any] = {
            "status": "fail",
            "returncode": None,
            "error": "pre-runtime movement route construction failed",
        }
    else:
        result = command_runner(
            command,
            cwd=destination,
            timeout_seconds=timeout_seconds,
        )
        command_result = (
            result.to_dict() if hasattr(result, "to_dict") else dict(result)
        )

    inspection = inspect_run_outputs(
        "bound-cell-movement-smoke",
        summary_path=summary_file,
        tripinfo_path=tripinfo_file,
    ).model_dump(mode="json")
    arrived_ids = _tripinfo_ids(tripinfo_file)
    expected_ids = tuple(sorted(expected_vehicle_ids))
    checks = {
        "movement_binding_pass": movement_binding.get("binding_status") == "pass",
        "movement_set_nonempty": bool(movements),
        "route_count_matches_binding": len(expected_vehicle_ids) == len(movements),
        "all_paths_controller_bound": all(
            item.get("controller_binding_status") == "pass" for item in movements
        ),
        "sumo_command": command_result.get("status") == "pass"
        and command_result.get("returncode") == 0,
        "all_expected_vehicles_arrived": tuple(sorted(arrived_ids)) == expected_ids,
        "runtime_outputs": inspection.get("status") == "pass",
        "source_immutable": file_sha256(source) == source_sha256,
    }
    status = "pass" if all(checks.values()) else "fail"
    report = {
        "schema": "torii.bound-cell-movement-smoke/v1",
        "status": status,
        "topology_hypothesis": movement_binding.get("topology_hypothesis"),
        "candidate_plan_id": movement_binding.get("candidate_plan_id"),
        "binding_id": movement_binding.get("binding_id"),
        "net_file": str(source),
        "net_sha256": source_sha256,
        "movement_count": len(movements),
        "stable_movement_ids": sorted(
            str(item["stable_movement_id"]) for item in movements
        ),
        "expected_vehicle_ids": expected_ids,
        "arrived_vehicle_ids": arrived_ids,
        "checks": checks,
        "command": command,
        "command_result": command_result,
        "traffic_light_policy": (
            "all_off_topology_smoke"
            if disable_traffic_lights
            else "candidate_program"
        ),
        "inspection": inspection,
        "route_construction_findings": route_construction_findings,
        "cleanup_errors": cleanup_errors,
        "route_file": str(route_file),
        "config_file": str(config_file),
        "summary_file": str(summary_file),
        "tripinfo_file": str(tripinfo_file),
        "automatic_topology_selection": False,
        "automatic_promotion_gate": "blocked",
    }
    write_json_atomic(report_file, report, sort_keys=True)
    artifacts = []
    for kind, path in (
        ("candidate_net", source),
        ("route_file", route_file),
        ("sumo_config", config_file),
        ("summary", summary_file),
        ("tripinfo", tripinfo_file),
        ("vehroute", vehroute_file),
        ("report", report_file),
    ):
        if path.is_file():
            artifacts.append(
                {"kind": kind, "path": str(path), "sha256": file_sha256(path)}
            )
    write_json_atomic(
        manifest_file,
        {
            "schema": "torii.bound-cell-movement-smoke-manifest/v1",
            "status": status,
            "source_network_mutation": not checks["source_immutable"],
            "automatic_promotion_gate": "blocked",
            "artifacts": artifacts,
        },
        sort_keys=True,
    )
    return {
        **report,
        "report_file": str(report_file),
        "manifest_file": str(manifest_file),
    }


def _tripinfo_ids(path: Path) -> tuple[str, ...]:
    if not path.is_file():
        return ()
    try:
        root = ET.parse(path).getroot()
    except (OSError, ET.ParseError):
        return ()
    return tuple(
        sorted(
            element.attrib["id"]
            for element in root.findall("tripinfo")
            if element.attrib.get("id")
        )
    )


def _write_xml(path: Path, root: ET.Element) -> None:
    ET.indent(root, space="  ")
    payload = ET.tostring(root, encoding="unicode")
    write_text_atomic(
        path,
        f"<?xml version='1.0' encoding='utf-8'?>\n{payload}\n",
    )
