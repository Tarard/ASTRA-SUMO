"""Compare fixed routed vehicles across networks without resampling requests."""

from collections import Counter
import math
from pathlib import Path
import re
import shutil
import xml.etree.ElementTree as ET

from .artifact_io import write_json_atomic
from .candidate_contracts import file_sha256
from .command_runner import run_command
from .routeability_audit import _hash_changes, _request_records, _run_attempt


def _request_observation(trip, depart, horizon, execution_status):
    if execution_status != "completed":
        return {"status": "unobserved_execution_failed", "right_censored": None, "time_since_requested_departure_s": None}
    if trip is not None:
        try:
            arrival = float(trip["arrival"])
            if not math.isfinite(arrival) or arrival < depart or arrival > horizon:
                raise ValueError("invalid arrival")
        except (KeyError, TypeError, ValueError):
            return {"status": "invalid_trip_record", "right_censored": None, "time_since_requested_departure_s": None}
        return {"status": "arrived", "right_censored": False, "time_since_requested_departure_s": arrival - depart}
    if depart >= horizon:
        return {"status": "not_requested_before_horizon", "right_censored": False, "time_since_requested_departure_s": None}
    return {"status": "unfinished_at_horizon", "right_censored": True, "time_since_requested_departure_s": horizon - depart}


def _driving_observation(case, identity, depart, horizon):
    observation = _request_observation(case["trips"].get(identity), depart, horizon, case["execution_status"])
    events = [row for row in case["diagnostics"].get("collisions", {}).get("records", [])
              if identity in (row.get("collider"), row.get("victim"))]
    observation.update(collision_events=events, collision_affected=bool(events),
                       teleported_after_collision=any(row.get("collider") == identity for row in events))
    if observation["status"] == "arrived" and events:
        observation["status"] = "arrived_with_recorded_collision"
    return observation


def compare_driving_cohort(*, networks, reference, routes, seeds, horizon_s, output_dir,
                          sumo_binary="sumo", timeout_seconds=240):
    """Keep every requested vehicle, including failed runs and missing arrivals."""
    if len(networks) < 2 or reference not in networks:
        raise ValueError("Supply a reference and at least two named networks.")
    if any(not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", name) for name in networks):
        raise ValueError("Network names must use letters, digits, underscores, or hyphens.")
    if not seeds or any(type(seed) is not int or seed < 0 for seed in seeds) or len(set(seeds)) != len(seeds):
        raise ValueError("Seeds must be distinct nonnegative integers.")
    if type(horizon_s) is not int or horizon_s <= 0:
        raise ValueError("The driving horizon must be a positive integer.")
    if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
        raise ValueError("The command timeout must be positive and finite.")
    routes = Path(routes).resolve(strict=True)
    networks = {name: Path(path).resolve(strict=True) for name, path in networks.items()}
    expected = _request_records(routes, routed=True)
    if not expected:
        raise ValueError("The route file must contain explicit routed vehicles.")
    identities = {path: file_sha256(path) for path in [routes, *networks.values()]}
    executable = shutil.which(str(sumo_binary))
    if executable:
        sumo_binary = str(Path(executable).resolve())
    output = Path(output_dir).resolve()
    output.mkdir(parents=True, exist_ok=False)
    plan = {"schema": "astra.paired-driving/v1", "reference": reference, "seeds": list(seeds),
            "horizon_s": horizon_s, "time_to_teleport_s": horizon_s, "timeout_seconds": timeout_seconds,
            "simulation_settings": {"step_length_s": 1, "collision_check_junctions": True, "collision_action": "teleport"},
            "position_output_begin_s": horizon_s - 1,
            "vehicle_route_output": {"edge_exit_times": True, "unfinished_routes": True},
            "sumo_binary": str(sumo_binary), "cohort": {"path": str(routes), "sha256": identities[routes]},
            "sumo_binary_sha256": file_sha256(Path(executable)) if executable else None,
            "vehicle_ids": sorted(expected),
            "networks": {name: {"path": str(path), "sha256": identities[path]} for name, path in networks.items()},
            "implementation": {"paired_driving_sha256": file_sha256(Path(__file__)),
                               "routeability_audit_sha256": file_sha256(Path(__file__).with_name("routeability_audit.py"))},
            "purpose": "Synthetic driving diagnosis with unchanged routes and departures. No field-performance claim."}
    write_json_atomic(output / "plan.json", plan)
    results, pairs = {}, []
    for seed in seeds:
        cases = {}
        for name, network in networks.items():
            folder = output / f"{name}-{seed}"
            folder.mkdir()
            result = _run_attempt(sumo_binary=str(sumo_binary), net_file=network, route_file=routes,
                output_dir=folder, prefix="driving", end=horizon_s, time_to_teleport=horizon_s,
                seed=seed, vehicle_count=len(expected), timeout_seconds=timeout_seconds, command_runner=run_command,
                diagnostic_outputs=True)
            result_path = folder / "result.json"
            write_json_atomic(result_path, result)
            trips, issues = {}, []
            trip_file = folder / f"driving_end{horizon_s}_tripinfo.xml"
            try:
                for row in ET.parse(trip_file).getroot().findall("tripinfo"):
                    identity = row.get("id")
                    if identity in trips:
                        issues.append(f"Duplicate trip record: {identity}")
                    trips[identity] = dict(row.attrib)
                    try:
                        duration = float(row.get("duration", "nan"))
                        if not math.isfinite(duration) or duration < 0:
                            raise ValueError("invalid duration")
                    except ValueError:
                        issues.append(f"Invalid trip duration: {identity}")
            except (OSError, ET.ParseError) as error:
                issues.append(f"Cannot read trip records: {error}")
            unexpected = sorted(str(identity) for identity in trips.keys() - expected.keys())
            if unexpected:
                issues.append("Unexpected vehicle IDs: " + ", ".join(unexpected))
            missing = sorted(expected.keys() - trips.keys())
            if missing:
                issues.append("Missing arrival records: " + ", ".join(missing))
            inspection = result["inspection"]
            command = result.get("command", {})
            execution_status = "completed" if command.get("status") == "pass" and command.get("returncode") == 0 else "failed"
            cases[name] = {"result": {"path": str(result_path), "sha256": file_sha256(result_path)},
                           "valid": execution_status == "completed" and inspection["status"] == "pass" and not issues,
                           "execution_status": execution_status,
                           "summary": inspection.get("summary", {}), "trips": trips, "tripinfo_issues": issues,
                           "tripinfo_sha256": file_sha256(trip_file) if trip_file.is_file() else None}
            diagnostics = {}
            for kind, value in result.get("diagnostic_outputs", {}).items():
                path = Path(value)
                diagnostics[kind] = {"path": str(path), "sha256": file_sha256(path) if path.is_file() else None}
                try:
                    root = ET.parse(path).getroot()
                    if kind == "collisions":
                        diagnostics[kind]["records"] = [dict(row.attrib) for row in root.findall("collision")]
                    elif kind == "last_step_positions":
                        steps = root.findall("timestep")
                        diagnostics[kind]["recorded_time_s"] = float(steps[-1].get("time")) if steps else None
                        diagnostics[kind]["vehicles"] = [dict(row.attrib) for row in steps[-1].findall("vehicle")] if steps else []
                except (OSError, ET.ParseError, TypeError, ValueError) as error:
                    diagnostics[kind]["error"] = str(error)
            cases[name]["diagnostics"] = diagnostics
        results[str(seed)] = cases
        source = cases[reference]
        for name, candidate in cases.items():
            if name == reference:
                continue
            valid = source["valid"] and candidate["valid"]
            rows = []
            for identity in sorted(expected):
                a, b = source["trips"].get(identity), candidate["trips"].get(identity)
                try:
                    delta = float(b["duration"]) - float(a["duration"]) if a and b else None
                    if delta is not None and not math.isfinite(delta):
                        delta = None
                except (KeyError, TypeError, ValueError):
                    delta = None
                depart = expected[identity][2]
                source_observation = _driving_observation(source, identity, depart, horizon_s)
                candidate_observation = _driving_observation(candidate, identity, depart, horizon_s)
                collision_affected = source_observation["collision_affected"] or candidate_observation["collision_affected"]
                rows.append({"vehicle_id": identity, "requested_departure_s": depart,
                             "source": a, "candidate": b,
                             "recorded_duration_delta_s": delta,
                             "duration_delta_s": None if collision_affected else delta,
                             "duration_comparison_status": "collision_affected" if collision_affected else
                                "incomplete_or_invalid_trip_pair" if delta is None else "recorded_arrivals",
                             "source_observation": source_observation, "candidate_observation": candidate_observation})
            pairs.append({"seed": seed, "candidate": name, "valid": valid, "trips": rows})
    changes = _hash_changes(identities)
    execution_failures = sum(case["execution_status"] == "failed" for cases in results.values() for case in cases.values())
    for pair in pairs:
        pair["valid"] = pair["valid"] and not changes
        pair["comparison_status"] = "valid_pair" if pair["valid"] else "diagnostic_invalid_pair"
    report = {"status": "execution_failed" if execution_failures else "measured", "execution_failure_count": execution_failures,
              "plan": plan, "runs": results, "pairs": pairs, "input_changes": changes,
              "pair_validity_counts": dict(Counter("valid" if pair["valid"] else "invalid" for pair in pairs)),
              "performance_mean": None,
              "claim_boundary": "All original requests remain in every pair. Invalid pairs provide no performance average."}
    write_json_atomic(output / "comparison.json", report)
    return report
