from __future__ import annotations

from pathlib import Path
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
import json
import math
from typing import Any, Mapping, Protocol
from xml.etree import ElementTree as ET

import sumolib

from torii_sumo.evidence.output_inspection import inspect_run_outputs

from .artifact_io import write_json_atomic, write_text_atomic
from .candidate_contracts import file_sha256
from .command_runner import run_command
from .connection_mode_audit import audit_network_connection_mode


class CommandRunner(Protocol):
    def __call__(
        self,
        command: list[str],
        *,
        cwd: Path | None = None,
        timeout_seconds: float = 60.0,
    ) -> Any: ...


def verify_lane_chain(
    movement: Mapping[str, Any], states: list[dict], expected_chain: list[str], *,
    terminal_tripinfo: Mapping[str, Any] | None = None, expected_vehicle_id: str = "probe",
    completed_without_collision_or_teleport: bool = False, step_length_s: float | None = None,
) -> dict:
    """Verify the observed inlet, complete internal chain, and first outlet lane."""
    source, source_lane, target, target_lane = movement["sumo_connection"]
    starts = [i for i, row in enumerate(states) if row["lane"].rsplit("_", 1)[0] == source]
    ends = [i for i, row in enumerate(states) if row["lane"].rsplit("_", 1)[0] == target]
    if not starts or ends and starts[-1] >= ends[0]:
        return {"pass": False, "reason": "ordered_inlet_and_outlet_not_observed"}
    outlet_source = "fcd"
    if not ends:
        terminal = terminal_tripinfo or {}
        try:
            depart, arrival = float(terminal.get("depart", "nan")), float(terminal.get("arrival", "nan"))
            times = [(float(row["time"]), float(row["last_time"])) for row in states]
            ordered = all(math.isfinite(value) for span in times for value in span) and all(a <= b for a, b in times) and all(a[1] < b[0] for a, b in zip(times, times[1:]))
            timing = ordered and math.isfinite(depart) and math.isfinite(arrival) and 0 <= depart <= times[0][0] and step_length_s is not None and math.isfinite(step_length_s) and step_length_s > 0 and 0 < arrival - times[-1][1] <= step_length_s + 1e-6
        except (KeyError, TypeError, ValueError):
            timing = False
        if not (completed_without_collision_or_teleport and terminal.get("id") == expected_vehicle_id
                and all(row.get("id") == expected_vehicle_id for row in states)
                and terminal.get("vaporized") == "" and terminal.get("arrivalLane") == f"{target}_{target_lane}" and timing):
            return {"pass": False, "reason": "terminal_arrival_missing_or_inconsistent"}
        outlet_source = "tripinfo"
    start, end = starts[-1], ends[0] if ends else len(states)
    observed = [row["lane"] for row in states[start + 1:end]]
    passed = bool(expected_chain) and observed == expected_chain and states[start]["lane"] == f"{source}_{source_lane}" and (not ends or states[end]["lane"] == f"{target}_{target_lane}")
    return {"pass": passed, "expected_internal_lane_chain": expected_chain,
            "observed_internal_lane_chain": observed,
            "outlet_evidence_source": outlet_source, "internal_evidence_source": "fcd",
            "reason": ("complete_internal_chain_and_terminal_arrival_observed" if outlet_source == "tripinfo" else "complete_requested_connection_observed") if passed else "lane_chain_mismatch_or_unobserved_segment"}


def run_candidate_movement_probes(
    *, candidate_manifest: Path | str, output_dir: Path | str, sumo_binary: str = "sumo",
    seed: int = 104, end_time_s: int = 600, step_length_s: float = 0.1,
    timeout_seconds: float = 120.0,
    junction_movements_only: bool = False,
    context_manifest: Path | str | None = None,
) -> dict[str, Any]:
    """Exercise declared MAP and context movements separately without detector data."""
    manifest_path = Path(candidate_manifest).resolve(strict=True)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    plan_based = manifest.get('schema') == 'torii.engineering-topology-build/v1'
    if not plan_based and manifest.get("schema") != "torii.hamburg-aerial-corridor-candidate/v1":
        raise ValueError("candidate manifest schema is invalid")
    network_record = manifest["artifacts"]["network"]
    network_path = Path(network_record["path"])
    if not network_path.is_absolute():
        network_path = manifest_path.parent / network_path
    network_path = network_path.resolve(strict=True)
    network_hash = file_sha256(network_path)
    if network_hash != str(network_record["sha256"]).lower():
        raise ValueError("candidate network SHA-256 does not match its manifest")
    destination = Path(output_dir).resolve()
    if destination.exists():
        raise ValueError("output_dir must not already exist")
    if any(not math.isfinite(value) or value <= 0 for value in (end_time_s, step_length_s, timeout_seconds)):
        raise ValueError("run duration, step, and timeout must be finite and positive")
    if isinstance(seed, bool) or not isinstance(seed, int) or seed < 0:
        raise ValueError("seed must be a nonnegative integer")
    if plan_based:
        topology_record = manifest['sources']['topology']
        topology_path = Path(topology_record['path'])
        topology_path = (topology_path if topology_path.is_absolute() else manifest_path.parent / topology_path).resolve(strict=True)
        if file_sha256(topology_path) != topology_record['sha256']:
            raise ValueError('The interpreted construction plan changed.')
        topology = json.loads(topology_path.read_text(encoding='utf-8'))
        if topology != manifest.get('declared_topology'):
            raise ValueError('The declared topology differs from its bound source.')
        required = [{'sumo_connection': [c['from'], c['fromLane'], c['to'], c['toLane']],
                     'evidence': c.get('evidence'), 'movement_authority': 'construction_plan'} for c in topology['connections']
                    if not (junction_movements_only and c.get('role') == 'continuous_road_boundary')]
        total, unresolved, connection_audit = len(required), [], {}
    else:
        connection_audit = manifest['official_connection_audit']
        required = connection_audit['required']
        total = int(manifest['counts']['official_vehicle_movements'])
        unresolved = connection_audit.get('ambiguous', [])
    if len(required) + len(unresolved) != total or total < 1:
        raise ValueError("official movement coverage counts do not agree")
    requested_pairs = {tuple(row["sumo_connection"]) for row in required}
    extra_probes = [row for row in connection_audit.get("boundary_connections", []) if tuple(row["sumo_connection"]) not in requested_pairs]
    context_record = None
    context_probe_count = 0
    if context_manifest is not None:
        context_path = Path(context_manifest).resolve(strict=True)
        context_record = {"path": str(context_path), "sha256": file_sha256(context_path)}
        context = json.loads(context_path.read_text(encoding="utf-8"))
        expected_source = manifest.get("inputs", {}).get("source_net", {}).get("sha256")
        if (plan_based or context.get("schema") != "torii.junction-boundary-rebuild/v1" or context.get("status") != "pass"
                or not expected_source or context.get("candidate_network", {}).get("sha256") != expected_source
                or file_sha256(Path(context["candidate_network"]["path"]).resolve(strict=True)) != expected_source):
            raise ValueError("context boundary evidence does not match the candidate construction source")
        seen = requested_pairs | {tuple(row["sumo_connection"]) for row in extra_probes}
        for row in context["plan"]["movements"]:
            if tuple(row["connection"]) not in seen:
                extra_probes.append({"sumo_connection": row["connection"], "allowed_vehicle_classes": row["vehicle_classes"],
                    "movement_authority": "source_boundary_path", "context_junction_id": row["join_id"]})
                seen.add(tuple(row["connection"]))
                context_probe_count += 1
    probes = [{**row, "probe_role": "plan_declared_movement" if plan_based else "official_movement"} for row in required] + [{**row, "probe_role": "additional_composed_boundary_connection"} for row in extra_probes]
    network = sumolib.net.readNet(str(network_path), withInternal=True)
    structural = audit_network_connection_mode(ET.parse(network_path).getroot(), endpoint_tolerance_m=0.1)
    chains = {}
    for junction in structural["junctions"]:
        for row in junction["connection_mode_audit"]["movement_checks"]:
            path = row.get("internal_path", {})
            if path.get("status") == "pass":
                key = (row["from"], int(row["fromLane"]), row["to"], int(row["toLane"]))
                chains[key] = path["internal_lane_chain"]
    owners = Counter(tuple(chain) for chain in chains.values())
    chains = {key: chain for key, chain in chains.items() if chain and owners[tuple(chain)] == 1}
    destination.mkdir(parents=True)

    def probe(index_and_row):
        index, row = index_and_row
        directory = destination / f"movement-{index:04d}"
        directory.mkdir()
        pair = (str(row["sumo_connection"][0]), int(row["sumo_connection"][1]), str(row["sumo_connection"][2]), int(row["sumo_connection"][3]))
        chain = chains.get(pair, [])
        record = {**row, "network_sha256": network_hash, "directory": str(directory), "status": "review_required"}
        if not chain:
            record["reason"] = "no_unique_structurally_valid_internal_chain"
        else:
            lanes = [network.getLane(name) for name in [f"{pair[0]}_{pair[1]}", *chain, f"{pair[2]}_{pair[3]}"]]
            restriction = row.get("allowed_vehicle_classes")
            modes = ("passenger", "delivery", "bus", "taxi", "truck", "motorcycle", "private", "coach", "emergency") + (("bicycle",) if plan_based else ())
            vehicle_class = next((mode for mode in modes if (restriction is None or mode in restriction) and all(lane.allows(mode) for lane in lanes)), None)
            record["vehicle_class"] = vehicle_class
            if vehicle_class is None:
                record["reason"] = "no_common_motor_vehicle_permission"
            else:
                routes = ET.Element("routes")
                ET.SubElement(routes, "vType", id="permitted", vClass=vehicle_class,
                    laneChangeModel="LC2013", lcStrategic="-1", lcCooperative="-1", lcSpeedGain="0", lcKeepRight="0")
                vehicle = ET.SubElement(routes, "vehicle", id="probe", type="permitted", depart="0", departLane=str(pair[1]), arrivalLane=str(pair[3]))
                ET.SubElement(vehicle, "route", edges=f"{pair[0]} {pair[2]}")
                route_path = directory / "probe.rou.xml"
                _write_xml(route_path, routes)
                summary_path, tripinfo_path, fcd_path = (directory / name for name in ("summary.xml", "tripinfo.xml", "fcd.xml"))
                command = [sumo_binary, "--net-file", str(network_path), "--route-files", str(route_path), "--begin", "0", "--end", str(end_time_s), "--step-length", str(step_length_s), "--seed", str(seed), "--summary-output", str(summary_path), "--tripinfo-output", str(tripinfo_path), "--fcd-output", str(fcd_path), "--collision.check-junctions", "true", "--no-step-log", "true"]
                result = run_command(command, cwd=directory, timeout_seconds=timeout_seconds)
                write_text_atomic(directory / "stdout.txt", result.stdout)
                write_text_atomic(directory / "stderr.txt", result.stderr)
                states, parse_error, terminal = [], None, None
                try:
                    fcd_root = ET.parse(fcd_path).getroot()
                    for step in fcd_root:
                        for car in step:
                            if car.get("id") == "probe":
                                time = float(step.get("time"))
                                if not states or states[-1]["lane"] != car.get("lane"):
                                    states.append({"id": car.get("id"), "time": time, "last_time": time, "lane": car.get("lane", "")})
                                else:
                                    states[-1]["last_time"] = time
                    summary_root = ET.parse(summary_path).getroot()
                    summary = dict(summary_root[-1].attrib) if len(summary_root) else {}
                    trips = ET.parse(tripinfo_path).getroot().findall("tripinfo")
                    terminal = dict(trips[0].attrib) if len(trips) == 1 else None
                except (OSError, ET.ParseError, ValueError) as error:
                    summary, parse_error = {}, str(error)
                completed = result.returncode == 0 and summary.get("arrived") == "1" and summary.get("collisions") == "0" and summary.get("teleports") == "0" and terminal is not None and terminal.get("id") == "probe" and terminal.get("vaporized") == ""
                proof = verify_lane_chain(row, states, chain, terminal_tripinfo=terminal,
                    expected_vehicle_id="probe", completed_without_collision_or_teleport=completed, step_length_s=step_length_s)
                record.update({"command": result.to_dict(), "summary": summary, "parse_error": parse_error,
                    "lane_sequence": states, "connection_chain_proof": proof, "completed_without_collision_or_teleport": completed,
                    "fcd_sha256": file_sha256(fcd_path) if fcd_path.is_file() else None,
                    "terminal_tripinfo": terminal, "tripinfo_sha256": file_sha256(tripinfo_path) if tripinfo_path.is_file() else None,
                    "status": "pass" if completed and proof["pass"] else "review_required"})
        write_json_atomic(directory / "result.json", record, sort_keys=True)
        return record

    with ThreadPoolExecutor(max_workers=4) as pool:
        records = list(pool.map(probe, enumerate(probes)))
    official_records = records[:len(required)]
    passed = sum(record["status"] == "pass" for record in official_records)
    composed = [row for row in official_records if "composition_geometry_status" in row]
    composed_passed = sum(row["status"] == "pass" and row["composition_geometry_status"] == "pass" for row in composed)
    boundary_reviews = connection_audit.get("composed_boundary_path_reviews", [])
    unchanged = file_sha256(network_path) == network_hash and (context_record is None or file_sha256(context_path) == context_record["sha256"])
    report = {"schema": "torii.official-movement-routeability/v1", "status": "pass" if passed == total and composed_passed == len(composed) and all(row["status"] == "pass" for row in records) and not boundary_reviews and unchanged else "review_required",
        "official_total": total, "mapped_and_tested": len(required), "passed_exact_lane_transition": passed,
        "probe_count": len(records), "additional_boundary_probe_count": len(extra_probes),
        "context_boundary_probe_count": context_probe_count, "context_manifest": context_record,
        "composed_official_movement_count": len(composed), "passed_composed_spatial_movements": composed_passed,
        "composed_boundary_path_review_count": len(boundary_reviews),
        "unmapped_not_tested": len(unresolved), "not_tested": unresolved, "results": records,
        "vehicle_class_counts": dict(Counter(record.get("vehicle_class", "not_run") for record in records)),
        "outlet_evidence_counts": dict(Counter(record.get("connection_chain_proof", {}).get("outlet_evidence_source", "not_proved") for record in records)),
        "network": str(network_path), "network_sha256": network_hash, "source_immutable": unchanged,
        "candidate_manifest": str(manifest_path), "candidate_manifest_sha256": file_sha256(manifest_path),
        "seed": seed, "horizon_s": end_time_s, "step_length_s": step_length_s,
        "lane_change_policy": "Autonomous lane changes disabled only for isolated connection probes; source permissions, car following, signals and collision checks remain active.",
        "lane_change_source": "https://sumo.dlr.de/docs/Definition_of_Vehicles%2C_Vehicle_Types%2C_and_Routes.html#lane-changing_models",
        "claim_boundary": "Each official movement is exercised alone using a permitted motor vehicle. The full internal chain must be observed in FCD. Only an unsampled terminal exit may use a matching normal tripinfo arrival within one simulation step of the last FCD frame. This does not validate multi-vehicle signal control or field timing."}
    if plan_based:
        report['schema'] = 'torii.engineering-plan-movement-routeability/v1'
        report['declared_total'] = report.pop('official_total')
        report['composed_declared_movement_count'] = report.pop('composed_official_movement_count')
        report['movement_authority'] = 'construction_plan'
        report['junction_movements_only'] = junction_movements_only
        report['continuous_boundaries_checked_separately'] = [
            [c['from'], c['fromLane'], c['to'], c['toLane']] for c in topology['connections']
            if junction_movements_only and c.get('role') == 'continuous_road_boundary']
        report['claim_boundary'] = ('Each selected declared plan movement is tested alone with a permitted native SUMO vehicle, '
                                   'including bicycle-only lanes. Exact FCD lane chains do not verify drawing interpretation, '
                                   'full-body clearance, field implementation, or historical signal timing.')
    report_file = destination / "summary.json"
    write_json_atomic(report_file, report, sort_keys=True)
    return {**report, "report_file": str(report_file)}


def run_all_turn_movement_smoke(
    *,
    net_file: Path,
    target_junction_id: str,
    output_dir: Path,
    sumo_binary: str,
    expected_movement_count: int = 12,
    expected_incoming_approach_count: int = 4,
    expected_outgoing_approach_count: int = 4,
    expected_turn_counts: Mapping[str, int] | None = None,
    expected_controller_ids: tuple[str, ...] | None = None,
    departure_interval_s: int = 8,
    end_time_s: int = 600,
    timeout_seconds: float = 120.0,
    command_runner: CommandRunner = run_command,
) -> dict[str, Any]:
    """Run one vehicle through every direct movement of one physical junction."""

    expected_turn_distribution = dict(
        expected_turn_counts or {"r": 4, "s": 4, "l": 4}
    )
    allowed_controller_ids = set(
        expected_controller_ids or (target_junction_id,)
    )

    source = net_file.resolve(strict=True)
    destination = output_dir.resolve()
    destination.mkdir(parents=True, exist_ok=True)
    route_file = destination / "all-turns.rou.xml"
    config_file = destination / "all-turns.sumocfg"
    summary_file = destination / "summary.xml"
    tripinfo_file = destination / "tripinfo.xml"
    vehroute_file = destination / "vehroute.xml"
    report_file = destination / "all-turns.report.json"
    manifest_file = destination / "all-turns.manifest.json"
    source_sha256 = file_sha256(source)

    root = ET.parse(source).getroot()
    external_edges = {
        edge.attrib["id"]: edge
        for edge in root.findall("edge")
        if edge.attrib.get("id")
        and not edge.attrib["id"].startswith(":")
        and edge.attrib.get("function", "")
        not in {"internal", "crossing", "walkingarea"}
    }
    movements = []
    for connection_index, connection in enumerate(root.findall("connection")):
        source_edge = external_edges.get(connection.attrib.get("from", ""))
        target_edge = external_edges.get(connection.attrib.get("to", ""))
        if source_edge is None or target_edge is None:
            continue
        if (
            source_edge.attrib.get("to") != target_junction_id
            or target_edge.attrib.get("from") != target_junction_id
        ):
            continue
        movements.append(
            {
                "connection_index": connection_index,
                "from": connection.attrib.get("from", ""),
                "from_lane": connection.attrib.get("fromLane", ""),
                "to": connection.attrib.get("to", ""),
                "to_lane": connection.attrib.get("toLane", ""),
                "via": connection.attrib.get("via", ""),
                "turn": connection.attrib.get("dir", ""),
                "controller_id": connection.attrib.get("tl", ""),
                "link_index": connection.attrib.get("linkIndex", ""),
            }
        )

    expected_vehicle_ids = tuple(
        f"movement_{index:02d}_{movement['turn'] or 'unknown'}"
        for index, movement in enumerate(movements)
    )
    route_root = ET.Element("routes")
    ET.SubElement(
        route_root,
        "vType",
        id="junction_smoke_passenger",
        vClass="passenger",
        accel="2.6",
        decel="4.5",
        sigma="0",
        length="5",
        maxSpeed="13.9",
    )
    for index, (vehicle_id, movement) in enumerate(
        zip(expected_vehicle_ids, movements, strict=True)
    ):
        vehicle = ET.SubElement(
            route_root,
            "vehicle",
            id=vehicle_id,
            type="junction_smoke_passenger",
            depart=str(index * departure_interval_s),
            departLane=str(movement["from_lane"]),
            arrivalLane=str(movement["to_lane"]),
        )
        ET.SubElement(
            vehicle,
            "route",
            edges=f"{movement['from']} {movement['to']}",
        )
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
    if cleanup_errors:
        command_result: Mapping[str, Any] = {
            "status": "fail",
            "returncode": None,
            "error": "stale output cleanup failed",
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
        "all-turn-movement-smoke",
        summary_path=summary_file,
        tripinfo_path=tripinfo_file,
    ).model_dump(mode="json")
    arrived_ids = _tripinfo_ids(tripinfo_file)
    turn_counts = {
        turn: sum(movement["turn"] == turn for movement in movements)
        for turn in sorted(
            {item["turn"] for item in movements}
            | set(expected_turn_distribution)
        )
    }
    incoming_approach_ids = tuple(sorted({item["from"] for item in movements}))
    outgoing_approach_ids = tuple(sorted({item["to"] for item in movements}))
    checks = {
        "movement_count": len(movements) == expected_movement_count,
        "incoming_approach_count": len(incoming_approach_ids)
        == expected_incoming_approach_count,
        "outgoing_approach_count": len(outgoing_approach_ids)
        == expected_outgoing_approach_count,
        "turn_distribution": turn_counts == expected_turn_distribution,
        "all_movements_tls_bound": all(
            item["controller_id"] in allowed_controller_ids
            and str(item["link_index"]).isdigit()
            for item in movements
        ),
        "sumo_command": command_result.get("status") == "pass"
        and command_result.get("returncode") == 0,
        "all_expected_vehicles_arrived": set(arrived_ids)
        == set(expected_vehicle_ids),
        "runtime_outputs": inspection.get("status") == "pass",
        "source_immutable": file_sha256(source) == source_sha256,
    }
    status = "pass" if all(checks.values()) else "fail"
    report = {
        "schema": "torii.all-turn-movement-smoke/v2",
        "status": status,
        "target_junction_id": target_junction_id,
        "net_file": str(source),
        "net_sha256": source_sha256,
        "expected_movement_count": expected_movement_count,
        "movement_count": len(movements),
        "expected_incoming_approach_count": expected_incoming_approach_count,
        "incoming_approach_ids": incoming_approach_ids,
        "expected_outgoing_approach_count": expected_outgoing_approach_count,
        "outgoing_approach_ids": outgoing_approach_ids,
        "expected_turn_counts": expected_turn_distribution,
        "turn_counts": turn_counts,
        "expected_controller_ids": sorted(allowed_controller_ids),
        "movements": movements,
        "expected_vehicle_ids": expected_vehicle_ids,
        "arrived_vehicle_ids": arrived_ids,
        "checks": checks,
        "command": command,
        "command_result": command_result,
        "inspection": inspection,
        "cleanup_errors": cleanup_errors,
        "route_file": str(route_file),
        "config_file": str(config_file),
        "summary_file": str(summary_file),
        "tripinfo_file": str(tripinfo_file),
    }
    write_json_atomic(report_file, report, sort_keys=True)
    artifacts = []
    for kind, path in (
        ("candidate_net", source),
        ("route_file", route_file),
        ("sumo_config", config_file),
        ("summary", summary_file),
        ("tripinfo", tripinfo_file),
        ("report", report_file),
    ):
        if path.is_file():
            artifacts.append(
                {"kind": kind, "path": str(path), "sha256": file_sha256(path)}
            )
    write_json_atomic(
        manifest_file,
        {
            "schema": "torii.all-turn-movement-smoke-manifest/v1",
            "status": status,
            "source_network_mutation": not checks["source_immutable"],
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
