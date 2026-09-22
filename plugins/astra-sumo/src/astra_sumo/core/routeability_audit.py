from __future__ import annotations

import os
import math
import random
import shutil
import sys
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence
import xml.etree.ElementTree as ET

from .command_runner import run_command
from .candidate_contracts import file_sha256
from .artifact_io import write_json_atomic, write_text_atomic
from .sumo_commands import discover_binaries
from ..evidence.output_inspection import inspect_run_outputs


CommandRunner = Callable[..., Any]


def build_routeability_od_policy(net_files: Sequence[Path]) -> dict[str, Any]:
    """Use the first network's OD roles and shared physical lane locations."""
    from .source_movement_support import _modes

    networks, fingerprints, frames = [], [], []
    for value in net_files:
        path = Path(value).resolve(strict=True)
        root = ET.parse(path).getroot()
        edges = {edge.get("id"): edge for edge in root.findall("edge") if not edge.get("id", "").startswith(":") and edge.get("function") != "internal"}
        incoming = {row.get("to") for row in root.findall("connection")}
        outgoing = {row.get("from") for row in root.findall("connection")}
        fits, reasons = set(), {}
        geometry = {}
        for key, edge in edges.items():
            lanes = [lane for lane in edge.findall("lane") if "passenger" in _modes(lane)]
            lengths = [float(lane.get("length", "0")) for lane in lanes]
            if not lengths:
                reasons[key] = "no_passenger_lane"
            elif any(not math.isfinite(length) or length < 5.0 for length in lengths):
                reasons[key] = "shorter_than_standard_vehicle"
            else:
                fits.add(key)
            geometry[key] = (len(edge.findall("lane")), tuple((lane.get("id"), lane.get("index"), float(lane.get("length", "0")), tuple(tuple(map(float, point.split(","))) for point in lane.get("shape", "").split())) for lane in lanes)) if lanes and all(lane.get("shape") for lane in lanes) else None
        location = root.find("location")
        frames.append((location.get("projParameter"), tuple(map(float, location.get("netOffset", "0,0").split(",")))) if location is not None else None)
        fingerprints.append(geometry)
        networks.append({"path": str(path), "sha256": file_sha256(path), "physical_edge_ids": sorted(fits), "source_edge_ids": sorted(fits & outgoing), "destination_edge_ids": sorted(fits & incoming), "excluded_edges": reasons, "source_excluded_edges": {key: reasons.get(key, "no_outgoing_connection") for key in edges.keys() - (fits & outgoing)}, "destination_excluded_edges": {key: reasons.get(key, "no_incoming_connection") for key in edges.keys() - (fits & incoming)}})
    if not networks:
        raise ValueError("OD qualification requires at least one network")
    physical = set.intersection(*(set(row["physical_edge_ids"]) for row in networks))
    source = set(networks[0]["source_edge_ids"]) & physical
    destination = set(networks[0]["destination_edge_ids"]) & physical
    geometry_exclusions = {}
    if len(networks) > 1:
        if frames[0] is None or any(frame != frames[0] for frame in frames):
            raise ValueError("shared OD requires matching declared coordinate frames")
        for key in source | destination:
            if any(geometry.get(key) is None or geometry.get(key) != fingerprints[0].get(key) for geometry in fingerprints):
                geometry_exclusions[key] = "geometry_or_lane_identity_changed"
        source -= geometry_exclusions.keys()
        destination -= geometry_exclusions.keys()
    differences = [{"network": row["path"], "source_edges_outside_common_pool": sorted(set(row["source_edge_ids"]) - source), "destination_edges_outside_common_pool": sorted(set(row["destination_edge_ids"]) - destination)} for row in networks]
    for row in differences:
        row["reasons"] = {key: [{"network": other["path"], "reason": geometry_exclusions.get(key, other[f"{role}_excluded_edges"].get(key, "missing_edge"))} for other in networks if key not in other[f"{role}_edge_ids"] or key in geometry_exclusions] for role in ("source", "destination") for key in row[f"{role}_edges_outside_common_pool"]}
    connection_differences = [{"network": row["path"], "source_edges_missing_connections": sorted(source - set(row["source_edge_ids"])), "destination_edges_missing_connections": sorted(destination - set(row["destination_edge_ids"]))} for row in networks[1:]]
    review = any(row["source_edges_outside_common_pool"] or row["destination_edges_outside_common_pool"] for row in differences) or any(row["source_edges_missing_connections"] or row["destination_edges_missing_connections"] for row in connection_differences)
    return {"mode": "shared_pool_generated" if len(networks) > 1 else "independent_generated", "vehicle_class": "passenger", "minimum_edge_length_m": 5.0, "source_edge_ids": sorted(source), "destination_edge_ids": sorted(destination), "networks": networks, "eligibility_differences": differences, "common_geometry_exclusions": geometry_exclusions, "connection_eligibility_differences": connection_differences, "connectivity_reference_network": networks[0]["path"], "decision": "review_required" if review else "pass", "policy": "5m is the default passenger vehicle length. Shared OD also requires identical lane identities, passenger-lane shapes and declared lengths in the same coordinate frame. Only the first, original network determines connection eligibility. Candidate connection losses do not remove requests. Short or changed roads remain available to route through. The shared pool does not validate lost road or endpoint eligibility. No route-success resampling is permitted.", "source": "https://sumo.dlr.de/docs/Definition_of_Vehicles%2C_Vehicle_Types%2C_and_Routes.html"}


def _write_source_reachable_trips(reference_net: Path, policy: Mapping[str, Any], trip_file: Path, *, vehicle_count: int, seed: int) -> dict[str, Any]:
    """Sample original-network reachable pairs once, without candidate routing."""
    import sumolib.net

    if vehicle_count <= 0 or not isinstance(seed, int) or isinstance(seed, bool) or seed < 0:
        raise ValueError("vehicle_count must be positive and seed must be a nonnegative integer")
    if trip_file.resolve() == reference_net.resolve() or trip_file.exists() and trip_file.samefile(reference_net):
        raise ValueError("trip output must not replace the reference network")
    reference_hash = file_sha256(reference_net)
    net = sumolib.net.readNet(str(reference_net), maxcache=1)
    destinations = set(policy["destination_edge_ids"])
    choices = {}
    for source in policy["source_edge_ids"]:
        start = net.getEdge(source)
        reachable = {edge.getID() for edge in net.getReachable(start, vclass="passenger")} & destinations - {source}
        # getReachable ignores connection-only restrictions in SUMO 1.27.1.
        # Reuse the native cached router before choosing any requests.
        choices[source] = [target for target in sorted(reachable) if net.getShortestPath(start, net.getEdge(target), vClass="passenger")[0] is not None]
    sources = sorted(source for source, targets in choices.items() if targets)
    if not sources:
        raise ValueError("the original network has no reachable distinct-edge diagnostic OD pair")
    generator = random.Random(seed)
    root = ET.Element("routes")
    sampled_sources, sampled_destinations, sampled_pairs = set(), set(), set()
    for number in range(vehicle_count):
        source = generator.choice(sources)
        destination = generator.choice(choices[source])
        ET.SubElement(root, "trip", id=str(number), depart=str(number), **{"from": source, "to": destination})
        sampled_sources.add(source)
        sampled_destinations.add(destination)
        sampled_pairs.add((source, destination))
    if file_sha256(reference_net) != reference_hash:
        raise ValueError("reference network changed during OD preparation")
    ET.indent(root)
    write_text_atomic(trip_file, ET.tostring(root, encoding="unicode", xml_declaration=True))
    usable_destinations = set().union(*(set(targets) for targets in choices.values()))
    return {"status": "pass", "method": "source_reachable_uniform_origin_then_destination/v1", "reference_network": {"path": str(reference_net), "sha256": reference_hash},
            "reachability_method": "SUMO Net.getReachable prefilter followed by cached Net.getShortestPath(vClass=passenger), including connection permissions",
            "seed": seed, "vehicle_count": vehicle_count, "departures": "one vehicle per second at 0..vehicle_count-1", "same_edge_trips": 0,
            "reachable_od_pair_count": sum(map(len, choices.values())), "reachable_destination_count_by_source": {source: len(targets) for source, targets in choices.items()},
            "sources_without_reachable_destinations": sorted(set(choices) - set(sources)), "destinations_without_reachable_sources": sorted(destinations - usable_destinations),
            "sampled_source_edge_ids": sorted(sampled_sources), "sampled_destination_edge_ids": sorted(sampled_destinations), "sampled_unique_od_pairs": len(sampled_pairs),
            "eligible_sources_not_sampled": sorted(set(sources) - sampled_sources), "eligible_destinations_not_sampled": sorted(usable_destinations - sampled_destinations),
            "source": "https://sumo.dlr.de/pydoc/sumolib/net.html#Net.getReachable",
            "claim_boundary": "Synthetic source-reachable diagnostic OD, not historical demand. Candidate reachability never selects requests. Native duarouter still checks every request; no failed request is replaced. Unused endpoints and unsampled roads are not claimed covered."}


def _write_od_weights(policy: Mapping[str, Any], prefix: Path) -> list[Path]:
    paths = []
    for suffix, role in (("src", "source"), ("dst", "destination")):
        root = ET.Element("edgedata")
        interval = ET.SubElement(root, "interval", begin="0", end="86400")
        for edge in policy[f"{role}_edge_ids"]:
            ET.SubElement(interval, "edge", id=edge, value="1")
        # randomTrips uses parse_fast: it reads at most one edge per line.
        ET.indent(root)
        path = Path(str(prefix) + f".{suffix}.xml")
        write_text_atomic(path, ET.tostring(root, encoding="unicode", xml_declaration=True))
        paths.append(path)
    return paths


def _request_records(path: Path, *, routed: bool = False) -> dict[str, tuple[str, str, float]]:
    root = ET.parse(path).getroot()
    records = {}
    for row in root.findall("vehicle" if routed else "trip"):
        identity = row.get("id")
        if not identity or identity in records:
            raise ValueError("diagnostic vehicle IDs must be present and unique")
        route = row.find("route")
        edges = route.get("edges", "").split() if route is not None else []
        a, b = (edges[0], edges[-1]) if routed and edges else (row.get("from"), row.get("to"))
        depart = float(row.get("depart", "nan"))
        if not a or not b or not math.isfinite(depart) or depart < 0:
            raise ValueError("diagnostic trips require explicit edge OD and departure time")
        records[identity] = (a, b, depart)
    return records


def _hash_changes(expected: Mapping[Path, str]) -> list[dict[str, Any]]:
    changes = []
    for path, digest in expected.items():
        current = file_sha256(path) if path.is_file() else None
        if current != digest:
            changes.append({"path": str(path), "expected_sha256": digest, "actual_sha256": current})
    return changes


def _inspect_vehicle_routes(approved_file: Path, observed_file: Path, expected_count: int, end: int, tripinfo_file: Path) -> dict[str, Any]:
    """Check native route completion against the exact approved vehicle routes."""
    digest = file_sha256(observed_file) if observed_file.is_file() else None
    tripinfo_digest = file_sha256(tripinfo_file) if tripinfo_file.is_file() else None
    result: dict[str, Any] = {"status": "fail", "path": str(observed_file), "sha256": digest, "tripinfo_path": str(tripinfo_file), "tripinfo_sha256": tripinfo_digest}
    try:
        approved_rows = ET.parse(approved_file).getroot().findall("vehicle")
        expected = {row.get("id"): row.find("route").get("edges", "").split() for row in approved_rows}
        departures = {row.get("id"): float(row.get("depart", "nan")) for row in approved_rows}
        rows = ET.parse(observed_file).getroot().findall("vehicle")
        tripinfo_rows = ET.parse(tripinfo_file).getroot().findall("tripinfo")
        tripinfo = {row.get("id"): row for row in tripinfo_rows}
        identities = [row.get("id") for row in rows]
        missing, extra = sorted(set(expected) - set(identities)), sorted(set(identities) - set(expected), key=str)
        failures = []
        complete = []
        for row in rows:
            identity = row.get("id")
            routes = row.findall(".//route")
            depart = float(row.get("depart", "nan"))
            arrival = float(row.get("arrival", "nan"))
            if identity not in expected or len(routes) != 1 or any(route.get("replacedOnEdge") is not None or route.get("replacedAtTime") is not None for route in routes):
                failures.append({"vehicle_id": identity, "reason": "unknown_vehicle_or_replaced_route"})
                continue
            route = routes[0]
            exits = list(map(float, route.get("exitTimes", "").split()))
            trip = tripinfo.get(identity)
            if not math.isfinite(depart) or not math.isfinite(departures[identity]) or depart < max(0, departures[identity]) or not math.isfinite(arrival) or arrival < depart or arrival > end:
                failures.append({"vehicle_id": identity, "reason": "vehicle_did_not_arrive_in_window"})
            elif trip is None or trip.get("vaporized", "").strip().lower() not in {"", "false", "0"} or float(trip.get("depart", "nan")) != depart or float(trip.get("arrival", "nan")) != arrival:
                failures.append({"vehicle_id": identity, "reason": "tripinfo_completion_not_normal_or_inconsistent"})
            elif route.get("edges", "").split() != expected[identity]:
                failures.append({"vehicle_id": identity, "reason": "vehicle_route_changed"})
            elif len(exits) != len(expected[identity]) or not exits or any(not math.isfinite(value) or value < depart or value > arrival for value in exits) or any(a > b for a, b in zip(exits, exits[1:])) or exits[-1] != arrival:
                failures.append({"vehicle_id": identity, "reason": "vehicle_route_exit_times_incomplete"})
            else:
                complete.append(identity)
        changed = file_sha256(observed_file) != digest or file_sha256(tripinfo_file) != tripinfo_digest
        valid = len(expected) == expected_count and len(rows) == len(set(identities)) == expected_count and len(tripinfo_rows) == len(tripinfo) == expected_count and set(tripinfo) == set(expected) and not (missing or extra or failures or changed)
        result.update(status="pass" if valid else "fail", planned_count=len(expected), observed_count=len(rows), complete_count=len(complete), missing_vehicle_ids=missing, extra_vehicle_ids=extra, failures=failures, changed_during_read=changed)
    except (OSError, ET.ParseError, AttributeError, TypeError, ValueError) as error:
        result["error"] = f"{type(error).__name__}: {error}"
    return result


def inspect_routeability_outputs(
    *,
    summary_path: Path,
    tripinfo_path: Path,
    expected_vehicle_count: int | None = None,
) -> dict[str, Any]:
    inspection = inspect_run_outputs(
        "routeability",
        summary_path=summary_path,
        tripinfo_path=tripinfo_path,
    ).model_dump(mode="json")
    summary = inspection.get("summary") or {}
    tripinfo = inspection.get("tripinfo") or {}
    warnings = list(inspection.get("warnings", []))

    loaded = _optional_int(summary.get("loaded"))
    inserted = _optional_int(summary.get("inserted"))
    arrived = _optional_int(summary.get("arrived"))
    running = _optional_int(summary.get("running")) or 0
    waiting = _optional_int(summary.get("waiting")) or 0
    teleports = _optional_int(summary.get("teleports")) or 0
    collisions = _optional_int(summary.get("collisions")) or 0
    discarded = _optional_int(summary.get("discarded")) or 0
    trip_count = _optional_int(tripinfo.get("trip_count")) or 0
    expected = expected_vehicle_count if expected_vehicle_count is not None else loaded

    status = "pass"
    routeability_status = "pass"
    if inspection.get("status") == "fail":
        status = "fail"
        routeability_status = "invalid-output"
    if loaded is None or arrived is None:
        status = "fail"
        routeability_status = "invalid-output"
        warnings.append("summary lacks loaded/arrived completion counts")
    if inserted is None:
        status = "fail"
        routeability_status = "invalid-output"
        warnings.append("summary lacks inserted completion count")
    if expected is not None and arrived is not None and arrived < expected:
        status = "fail"
        routeability_status = "incomplete"
        warnings.append(f"arrived {arrived}/{expected} vehicles at final summary step")
    if loaded is not None and arrived is not None and arrived < loaded:
        status = "fail"
        routeability_status = "incomplete"
        warnings.append(f"arrived {arrived}/{loaded} loaded vehicles at final summary step")
    if running > 0 or waiting > 0:
        status = "fail"
        routeability_status = "incomplete"
    if discarded > 0:
        status = "fail"
        routeability_status = "discard-failure"
    if teleports > 0:
        status = "fail"
        routeability_status = "teleport-failure"
    if collisions > 0:
        status = "fail"
        routeability_status = "collision-failure"
    if arrived is not None and trip_count != arrived:
        status = "fail"
        routeability_status = "output-mismatch"
        warnings.append(f"tripinfo has {trip_count} records but summary arrived count is {arrived}")

    return {
        "status": status,
        "claim_status": "diagnostic-demo" if status == "pass" else "construction-invalid",
        "routeability_status": routeability_status,
        "summary": summary,
        "tripinfo": tripinfo,
        "warnings": list(dict.fromkeys(warnings)),
    }


def run_routeability_audit(
    *,
    net_file: Path,
    output_dir: Path,
    prefix: str = "routeability_audit",
    vehicle_count: int = 100,
    seed: int = 42,
    initial_end: int = 300,
    max_end: int = 2400,
    timeout_seconds: float = 240.0,
    binaries: Mapping[str, str | None] | None = None,
    command_runner: CommandRunner = run_command,
    od_reference_net_file: Path | None = None,
    frozen_trip_file: Path | None = None,
    expected_frozen_trip_sha256: str | None = None,
) -> dict[str, Any]:
    """Test fixed requests or new source-reachable synthetic diagnostic OD.

    With a reference network, only that original network determines OD
    connectivity. Candidate lane geometry may exclude nonmatching physical
    endpoints, but candidate connection losses cannot remove requests.
    """
    if not prefix or Path(prefix).name != prefix or prefix in {".", ".."}:
        return _construction_invalid("prefix must be a single file-name component")
    output_dir = output_dir.resolve()
    net_file = net_file.resolve()
    reference = Path(od_reference_net_file).resolve() if od_reference_net_file is not None else None
    frozen = Path(frozen_trip_file).resolve() if frozen_trip_file is not None else None
    protected = {path for path in (net_file, reference, frozen) if path is not None}
    planned_outputs = {output_dir / f"{prefix}{suffix}" for suffix in (".trips.xml", ".rou.xml", "_routeability_audit.json", "_routeability_audit.manifest.json", ".od.src.xml", ".od.dst.xml")}
    if initial_end > 0 and max_end >= initial_end:
        planned_outputs.update(output_dir / f"{prefix}_end{end}{suffix}" for end in _horizon_sequence(initial_end, max_end) for suffix in (".sumocfg", "_summary.xml", "_tripinfo.xml", "_vehroute.xml"))
    if any(path.resolve() in protected or any(path.exists() and source.exists() and path.samefile(source) for source in protected) for path in planned_outputs):
        # Do not write even an error manifest over a protected input.
        return _construction_invalid("diagnostic output paths overlap a protected input file")
    input_hashes = {path: file_sha256(path) for path in protected if path.is_file()}
    approved_hashes: dict[Path, str] = {}
    observed_changes: list[dict[str, Any]] = []
    try:
        output_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return _construction_invalid(f"could not create routeability output directory: {type(exc).__name__}: {exc}")
    report_file = output_dir / f"{prefix}_routeability_audit.json"
    manifest_file = output_dir / f"{prefix}_routeability_audit.manifest.json"

    def finish(report: Mapping[str, Any]) -> dict[str, Any]:
        payload = dict(report)
        payload["completion_policy"] = {
            "mode": "bounded_natural_completion",
            "begin_s": 0,
            "max_end_s": max_end,
            "time_to_teleport_s": max_end,
            "claim_boundary": "Every attempt uses the same finite waiting-teleport threshold as the fixed maximum end. All requested vehicles must arrive normally on their complete approved routes within that window. Native vehicle-route output must retain every vehicle, route edge and exit time without replacement. Running, waiting, discarded, teleported or collided vehicles fail. Other recovery modes are not disabled; their events still fail. This is topology diagnostics, not traffic-performance or calibration evidence.",
        }
        changes = observed_changes + _hash_changes({**input_hashes, **approved_hashes})
        payload["input_identities"] = [{"path": str(path), "sha256": digest} for path, digest in input_hashes.items()]
        payload["approved_route_artifacts"] = [{"path": str(path), "sha256": digest} for path, digest in approved_hashes.items()]
        payload["input_changes"] = changes
        if net_file in input_hashes:
            payload["net_sha256"] = input_hashes[net_file]
        if changes:
            payload.update(status="fail", claim_status="construction-invalid", routeability_status="input-artifact-changed")
            payload["warnings"] = list(payload.get("warnings", [])) + ["An input or approved route changed during the diagnostic"]
        return _write_routeability_outcome(
            report=payload,
            report_file=report_file,
            manifest_file=manifest_file,
            net_file=net_file,
        )

    if vehicle_count <= 0:
        return finish(_construction_invalid("vehicle_count must be positive"))
    if initial_end <= 0 or max_end <= 0:
        return finish(_construction_invalid("initial_end and max_end must be positive"))
    if initial_end > max_end:
        return finish(_construction_invalid("initial_end must be <= max_end"))
    if not net_file.exists():
        return finish(_construction_invalid(f"net file does not exist: {net_file}"))
    if frozen is not None and (not frozen.is_file() or expected_frozen_trip_sha256 is None or file_sha256(frozen) != expected_frozen_trip_sha256.lower()):
        return finish(_construction_invalid("frozen trip SHA-256 does not match or is missing"))
    if reference is not None and not reference.is_file():
        return finish(_construction_invalid("OD reference network does not exist"))

    selected = dict(binaries or discover_binaries())
    missing = [
        name for name in ("duarouter", "sumo")
        if not selected.get(name)
    ]
    if missing:
        return finish({
            "status": "blocked",
            "claim_status": "blocked",
            "routeability_status": "blocked",
            "warnings": [f"missing required SUMO tool: {name}" for name in missing],
        })

    trip_file = output_dir / f"{prefix}.trips.xml"
    route_file = output_dir / f"{prefix}.rou.xml"
    cleanup_errors = _remove_stale_outputs(trip_file, route_file)
    if cleanup_errors:
        return finish(
            {
                "status": "fail",
                "claim_status": "construction-invalid",
                "routeability_status": "stale-output-cleanup-failed",
                "net_file": str(net_file.resolve()),
                "trip_file": str(trip_file),
                "route_file": str(route_file),
                "errors": cleanup_errors,
                "warnings": ["route generation was not run because stale outputs could not be removed"],
            }
        )
    od_policy: dict[str, Any] = {"mode": "frozen_requests", "eligibility_filter_applied": False, "frozen_trip_file": str(frozen), "frozen_trip_sha256": expected_frozen_trip_sha256} if frozen is not None else {}
    sampling: dict[str, Any] = {"status": "not_run", "reason": "frozen requests are not sampled or filtered"}
    route_generation: dict[str, Any] = {}
    try:
        if frozen is not None:
            shutil.copy2(frozen, trip_file)
            if file_sha256(trip_file) != expected_frozen_trip_sha256.lower():
                raise ValueError("frozen trip changed while being copied")
        else:
            original = reference or net_file
            od_policy = build_routeability_od_policy([original, *([net_file] if reference is not None else [])])
            if not od_policy["source_edge_ids"] or not od_policy["destination_edge_ids"]:
                raise ValueError("no qualifying diagnostic origin or destination edges")
            sampling = _write_source_reachable_trips(original, od_policy, trip_file, vehicle_count=vehicle_count, seed=seed)
            od_policy["mode"] = "source_reachable_shared_generated" if reference is not None else "source_reachable_independent_generated"
            od_policy["request_generation"] = sampling
        observed_changes.extend(_hash_changes(input_hashes))
        if observed_changes:
            raise ValueError("protected input changed during trip preparation")
        requested = _request_records(trip_file)
        if len(requested) != vehicle_count:
            raise ValueError(f"generated {len(requested)} requests, expected {vehicle_count}; no requests were replaced")
        planned_hash = file_sha256(trip_file)
        approved_hashes[trip_file] = planned_hash
        command = [str(selected["duarouter"]), "--net-file", str(net_file), "--route-files", str(trip_file), "--output-file", str(route_file), "--alternatives-output", os.devnull, "--seed", str(seed)]
        route_generation = _result_to_dict(command_runner(command, cwd=output_dir, timeout_seconds=timeout_seconds))
        if route_generation.get("returncode") != 0 or route_generation.get("status") != "pass" or not route_file.is_file():
            raise ValueError("strict route generation failed; original requests are retained")
        observed_changes.extend(_hash_changes({**input_hashes, **approved_hashes}))
        if observed_changes:
            raise ValueError("protected input or planned trips changed during routing")
        routed_hash = file_sha256(route_file)
        if _request_records(route_file, routed=True) != requested or file_sha256(route_file) != routed_hash:
            raise ValueError("routing changed or omitted diagnostic vehicle IDs, departures, or OD")
        approved_hashes[route_file] = routed_hash
        od_policy["request_file_sha256"] = planned_hash
        od_policy["request_count"] = len(requested)
    except (OSError, RuntimeError, TimeoutError, TypeError, ValueError, KeyError, ET.ParseError) as exc:
        route_generation = {
            **route_generation,
            "status": "fail",
            "error": f"{type(exc).__name__}: {exc}",
        }
    route_generation_pass = (
        route_generation.get("status") == "pass"
        and type(route_generation.get("returncode")) is int
        and route_generation.get("returncode") == 0
        and route_file.is_file()
        and trip_file.is_file()
    )
    if not route_generation_pass:
        return finish({
            "status": "fail",
            "claim_status": "construction-invalid",
            "routeability_status": "route-generation-failed",
            "net_file": str(net_file.resolve()),
            "net_sha256": file_sha256(net_file),
            "route_file": str(route_file),
            "trip_file": str(trip_file),
            "route_generation": route_generation,
            "sampling": sampling,
            "od_policy": od_policy,
            "warnings": [
                f"route generation output was not created: {path}"
                for path in (trip_file, route_file)
                if not path.is_file()
            ],
        })

    attempts: list[dict[str, Any]] = []
    final_attempt: dict[str, Any] | None = None
    for end in _horizon_sequence(initial_end, max_end):
        observed_changes.extend(_hash_changes({**input_hashes, **approved_hashes}))
        if observed_changes:
            return finish({"status": "fail", "route_file": str(route_file), "trip_file": str(trip_file), "od_policy": od_policy, "attempts": attempts})
        attempt = _run_attempt(
            sumo_binary=str(selected["sumo"]),
            net_file=net_file,
            route_file=route_file,
            output_dir=output_dir,
            prefix=prefix,
            end=end,
            time_to_teleport=max_end,
            seed=seed,
            vehicle_count=vehicle_count,
            timeout_seconds=timeout_seconds,
            command_runner=command_runner,
        )
        attempts.append(attempt)
        final_attempt = attempt
        vehicle_routes = attempt.get("vehicle_routes", {})
        if vehicle_routes.get("sha256"):
            approved_hashes[Path(vehicle_routes["path"])] = vehicle_routes["sha256"]
        if vehicle_routes.get("tripinfo_sha256"):
            approved_hashes[Path(vehicle_routes["tripinfo_path"])] = vehicle_routes["tripinfo_sha256"]
        observed_changes.extend(_hash_changes({**input_hashes, **approved_hashes}))
        if observed_changes:
            break
        if attempt["inspection"]["status"] == "pass":
            break

    assert final_attempt is not None
    status = "pass" if final_attempt["inspection"]["status"] == "pass" else "fail"
    warnings = []
    for attempt in attempts:
        warnings.extend(attempt["inspection"].get("warnings", []))
    if status != "pass":
        warnings.append(f"routeability audit did not complete by max_end={max_end}")

    report = {
        "status": status,
        "claim_status": "diagnostic-demo" if status == "pass" else "construction-invalid",
        "routeability_status": final_attempt["inspection"]["routeability_status"],
        "net_file": str(net_file.resolve()),
        "net_sha256": file_sha256(net_file),
        "output_dir": str(output_dir),
        "route_file": str(route_file),
        "trip_file": str(trip_file),
        "vehicle_count": vehicle_count,
        "seed": seed,
        "initial_end": initial_end,
        "max_end": max_end,
        "route_generation": route_generation,
        "sampling": sampling,
        "od_policy": od_policy,
        "attempts": attempts,
        "final_attempt": final_attempt,
        "warnings": list(dict.fromkeys(warnings)),
    }
    return finish(report)


def _run_attempt(
    *,
    sumo_binary: str,
    net_file: Path,
    route_file: Path,
    output_dir: Path,
    prefix: str,
    end: int,
    time_to_teleport: int,
    seed: int,
    vehicle_count: int,
    timeout_seconds: float,
    command_runner: CommandRunner,
) -> dict[str, Any]:
    summary_file = output_dir / f"{prefix}_end{end}_summary.xml"
    tripinfo_file = output_dir / f"{prefix}_end{end}_tripinfo.xml"
    vehicle_route_file = output_dir / f"{prefix}_end{end}_vehroute.xml"
    config_file = output_dir / f"{prefix}_end{end}.sumocfg"
    outputs = (summary_file, tripinfo_file, vehicle_route_file, config_file)
    if any(path.resolve() == source.resolve() or path.exists() and path.samefile(source) for path in outputs for source in (net_file, route_file)):
        return _failed_attempt(end=end, config_file=config_file, summary_file=summary_file, tripinfo_file=tripinfo_file,
            status="output-input-overlap", warnings=["simulation output paths overlap a protected network or approved route"])
    cleanup_errors = _remove_stale_outputs(summary_file, tripinfo_file, vehicle_route_file)
    if cleanup_errors:
        return _failed_attempt(
            end=end,
            config_file=config_file,
            summary_file=summary_file,
            tripinfo_file=tripinfo_file,
            status="stale-output-cleanup-failed",
            warnings=cleanup_errors,
        )
    try:
        _write_sumocfg(
            config_file,
            net_file=net_file,
            route_file=route_file,
            summary_file=summary_file,
            tripinfo_file=tripinfo_file,
            vehicle_route_file=vehicle_route_file,
            end=end,
            seed=seed,
        )
    except OSError as exc:
        return _failed_attempt(
            end=end,
            config_file=config_file,
            summary_file=summary_file,
            tripinfo_file=tripinfo_file,
            status="configuration-write-failed",
            warnings=[f"{type(exc).__name__}: {exc}"],
        )
    command = [
        sumo_binary,
        "-c",
        config_file.name,
        "--quit-on-end",
        "--duration-log.statistics",
        "--collision.check-junctions",
        "true",
        "--time-to-teleport",
        str(time_to_teleport),
    ]
    try:
        command_result = _result_to_dict(
            command_runner(command, cwd=output_dir, timeout_seconds=timeout_seconds)
        )
    except (OSError, RuntimeError, TimeoutError, TypeError, ValueError) as exc:
        command_result = {
            "status": "fail",
            "returncode": None,
            "error": f"{type(exc).__name__}: {exc}",
        }
    inspection = inspect_routeability_outputs(
        summary_path=summary_file,
        tripinfo_path=tripinfo_file,
        expected_vehicle_count=vehicle_count,
    )
    vehicle_routes = _inspect_vehicle_routes(route_file, vehicle_route_file, vehicle_count, end, tripinfo_file)
    if vehicle_routes["status"] != "pass":
        if inspection["status"] == "pass":
            inspection.update(status="fail", claim_status="construction-invalid", routeability_status="vehicle-route-completion-failed")
        inspection["warnings"] = list(inspection.get("warnings", [])) + ["Native vehicle routes do not show every approved vehicle completing its unchanged route"]
    command_pass = (
        command_result.get("status") == "pass"
        and type(command_result.get("returncode")) is int
        and command_result.get("returncode") == 0
    )
    if not command_pass:
        inspection["status"] = "fail"
        inspection["claim_status"] = "construction-invalid"
        inspection["routeability_status"] = "sumo-run-failed"
        inspection["warnings"] = list(inspection.get("warnings", [])) + ["SUMO routeability run failed"]
    return {
        "end": end,
        "time_to_teleport_s": time_to_teleport,
        "sumocfg_file": str(config_file),
        "summary_file": str(summary_file),
        "tripinfo_file": str(tripinfo_file),
        "vehicle_route_file": str(vehicle_route_file),
        "vehicle_routes": vehicle_routes,
        "command": command_result,
        "inspection": inspection,
    }


def _build_random_trips_command(
    *,
    random_trips: str,
    net_file: Path,
    trip_file: Path,
    route_file: Path,
    cwd: Path,
    vehicle_count: int,
    seed: int,
    weights_prefix: Path | None = None,
) -> list[str]:
    return [
        sys.executable,
        random_trips,
        "-n",
        str(net_file.resolve()),
        "-o",
        trip_file.name,
        "-e",
        str(vehicle_count),
        "--seed",
        str(seed),
        "--no-validate",
        *(["--weights-prefix", str(weights_prefix.resolve())] if weights_prefix is not None else []),
    ]


def _write_sumocfg(
    path: Path,
    *,
    net_file: Path,
    route_file: Path,
    summary_file: Path,
    tripinfo_file: Path,
    vehicle_route_file: Path,
    end: int,
    seed: int,
) -> None:
    root = ET.Element("configuration")
    input_node = ET.SubElement(root, "input")
    ET.SubElement(input_node, "net-file", value=str(net_file.resolve()))
    ET.SubElement(input_node, "route-files", value=_relpath(route_file, path.parent))
    output_node = ET.SubElement(root, "output")
    ET.SubElement(output_node, "summary-output", value=summary_file.name)
    ET.SubElement(output_node, "tripinfo-output", value=tripinfo_file.name)
    ET.SubElement(output_node, "vehroute-output", value=vehicle_route_file.name)
    ET.SubElement(output_node, "vehroute-output.exit-times", value="true")
    time_node = ET.SubElement(root, "time")
    ET.SubElement(time_node, "begin", value="0")
    ET.SubElement(time_node, "end", value=str(end))
    random_node = ET.SubElement(root, "random_number")
    ET.SubElement(random_node, "seed", value=str(seed))
    write_text_atomic(path, ET.tostring(root, encoding="unicode", xml_declaration=True))


def _horizon_sequence(initial_end: int, max_end: int) -> list[int]:
    values = [initial_end]
    current = initial_end
    while current < max_end:
        current = min(current * 2, max_end)
        if current != values[-1]:
            values.append(current)
    return values


def _remove_stale_outputs(*paths: Path) -> list[str]:
    errors: list[str] = []
    for path in paths:
        try:
            if path.exists():
                path.unlink()
        except OSError as exc:
            errors.append(f"could not remove stale output {path}: {type(exc).__name__}: {exc}")
    return errors


def _failed_attempt(
    *,
    end: int,
    config_file: Path,
    summary_file: Path,
    tripinfo_file: Path,
    status: str,
    warnings: list[str],
) -> dict[str, Any]:
    return {
        "end": end,
        "sumocfg_file": str(config_file),
        "summary_file": str(summary_file),
        "tripinfo_file": str(tripinfo_file),
        "command": {"status": "fail", "returncode": None, "error": "; ".join(warnings)},
        "inspection": {
            "status": "fail",
            "claim_status": "construction-invalid",
            "routeability_status": status,
            "summary": {},
            "tripinfo": {},
            "warnings": warnings,
        },
    }


def _write_routeability_outcome(
    *,
    report: Mapping[str, Any],
    report_file: Path,
    manifest_file: Path,
    net_file: Path,
) -> dict[str, Any]:
    persisted = dict(report)
    persisted.setdefault("schema", "torii.routeability_audit.v2")
    persisted.setdefault("net_file", str(net_file.resolve()))
    if net_file.is_file():
        persisted.setdefault("net_sha256", file_sha256(net_file))
    persisted["report_file"] = str(report_file)
    persisted["manifest_file"] = str(manifest_file)
    write_json_atomic(report_file, persisted)

    artifact_candidates: list[tuple[Path, str]] = []
    if net_file.is_file():
        artifact_candidates.append((net_file, "routeability_net"))
    for key, kind in (("trip_file", "random_trips"), ("route_file", "route_file")):
        value = persisted.get(key)
        if value:
            artifact_candidates.append((Path(str(value)), kind))
    for attempt in persisted.get("attempts", []):
        if not isinstance(attempt, Mapping):
            continue
        for key, kind in (
            ("sumocfg_file", "sumo_config"),
            ("summary_file", "sumo_summary"),
            ("tripinfo_file", "sumo_tripinfo"),
            ("vehicle_route_file", "sumo_vehicle_routes"),
        ):
            value = attempt.get(key)
            if value:
                artifact_candidates.append((Path(str(value)), kind))
    artifact_candidates.append((report_file, "routeability_report"))

    artifacts: list[dict[str, Any]] = []
    seen_paths: set[str] = set()
    for path, kind in artifact_candidates:
        if not path.is_file():
            continue
        resolved = path.resolve()
        if str(resolved) in seen_paths:
            continue
        seen_paths.add(str(resolved))
        artifacts.append(
            {
                "kind": kind,
                "path": str(resolved),
                "size_bytes": resolved.stat().st_size,
                "sha256": file_sha256(resolved),
            }
        )
    manifest = {
        "schema": "torii.routeability_manifest.v2",
        "status": persisted.get("status", "fail"),
        "claim_status": persisted.get("claim_status", "construction-invalid"),
        "routeability_status": persisted.get("routeability_status", "construction-invalid"),
        "net_file": persisted.get("net_file", ""),
        "net_sha256": persisted.get("net_sha256", ""),
        "artifacts": artifacts,
    }
    write_json_atomic(manifest_file, manifest)
    return persisted


def _result_to_dict(result: Any) -> dict[str, Any]:
    if hasattr(result, "to_dict"):
        return result.to_dict()
    if isinstance(result, Mapping):
        return dict(result)
    return {
        "status": "fail",
        "returncode": None,
        "stdout": "",
        "stderr": "",
        "error": f"unexpected command result type: {type(result).__name__}",
    }


def _optional_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _relpath(target: Path, start: Path) -> str:
    return Path(os.path.relpath(target.resolve(), start=start.resolve())).as_posix()


def _construction_invalid(error: str) -> dict[str, Any]:
    return {
        "status": "fail",
        "claim_status": "construction-invalid",
        "routeability_status": "construction-invalid",
        "error": error,
    }
