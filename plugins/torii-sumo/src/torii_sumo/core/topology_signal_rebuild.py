"""Protected generic signals for testing an already-built road topology.

The current SUMO request/foes tables define conflicts. These signals are not
historical timing or an estimate of field signal groups.
"""

from __future__ import annotations

import json
import math
import shutil
import xml.etree.ElementTree as ET
from collections.abc import Sequence
from copy import deepcopy
from pathlib import Path
from typing import Any

from .candidate_contracts import file_sha256
from .connection_mode_audit import audit_network_connection_mode
from .hamburg_aerial_signal import allocate_stage_green_seconds, build_protected_signal_stages, _tls_link_flows


def build_topology_test_signal_plan(
    root: ET.Element,
    *,
    target_junction_ids: Sequence[str],
    green_seconds: float = 15.0,
    yellow_seconds: float = 3.0,
    route_file: Path | None = None,
) -> dict[str, Any]:
    """Color actual request conflicts, including controllers shared by nodes."""
    if not math.isfinite(green_seconds) or green_seconds < 5:
        raise ValueError("generic topology tests require green_seconds >= 5")
    if not math.isfinite(yellow_seconds) or yellow_seconds < 3:
        raise ValueError("generic topology tests require yellow_seconds >= 3")
    if route_file and (not float(green_seconds).is_integer() or not float(yellow_seconds).is_integer()):
        raise ValueError("Route-flow allocation requires whole-second green and yellow durations.")
    requested = set(map(str, target_junction_ids))
    known = {row.get("id", "") for row in root.findall("junction")}
    if not requested or requested - known:
        raise ValueError("target_junction_ids must name existing junctions")
    audit = audit_network_connection_mode(root, endpoint_tolerance_m=0.1)
    records = {row["junction_id"]: row for row in audit["junctions"]}
    expanded = set(requested)
    target_tls: set[str] = set()
    while True:
        target_tls.update(tls for node in expanded for tls in records.get(node, {}).get("controller_ids", []))
        additions = {node for node, row in records.items() if target_tls.intersection(row["controller_ids"])}
        if additions <= expanded:
            break
        expanded.update(additions)
    scoped = [records[node] for node in sorted(expanded) if node in records]
    for row in scoped:
        structural = row["connection_mode_audit"]["structural_failures"]
        if structural:
            raise ValueError(f"junction {row['junction_id']} has invalid movement/request structure: {structural}")

    connections = root.findall("connection")
    keys = set()
    for connection in connections:
        if connection.get("tl") not in target_tls:
            continue
        if connection.get("linkIndex2") is not None:
            raise ValueError("dual-index signal links require an explicit signal binding")
        index = int(connection.get("linkIndex", "-1"))
        if index < 0:
            raise ValueError("controlled connections require a nonnegative linkIndex")
        keys.add((connection.attrib["tl"], index))
    ordered_keys = sorted(keys)
    key_indices = {key: index for index, key in enumerate(ordered_keys)}
    conflicts: set[tuple[int, int]] = set()
    mapped = set()
    clearance_by_key: dict[tuple[str, int], float] = {}
    lanes = {lane.attrib["id"]: lane for edge in root.findall("edge") for lane in edge.findall("lane")}
    for row in scoped:
        junction = root.find(f"junction[@id='{row['junction_id']}']")
        requests = {int(request.attrib["index"]): request for request in junction.findall("request")}
        bindings = row["connection_mode_audit"]["request_foe_audit"]["request_bindings"]
        by_request = {}
        for binding in bindings:
            key = (str(binding.get("tl", "")), binding.get("linkIndex"))
            if key in keys:
                by_request[int(binding["request_index"])] = key
                mapped.add(key)
        for first, first_key in by_request.items():
            for second, second_key in by_request.items():
                if first >= second:
                    continue
                if requests[first].attrib["foes"][-1 - second] != "1" and requests[second].attrib["foes"][-1 - first] != "1":
                    continue
                if first_key == second_key:
                    raise ValueError(f"one signal index controls conflicting requests: {first_key}")
                conflicts.add(tuple(sorted((key_indices[first_key], key_indices[second_key]))))
        for movement in row["connection_mode_audit"]["movement_checks"]:
            connection = connections[movement["connection_index"]]
            key = (connection.get("tl", ""), int(connection.get("linkIndex", "-1")))
            if key not in keys:
                continue
            chain = [lanes[lane_id] for lane_id in movement["internal_path"]["internal_lane_chain"]]
            speeds = [float(lane.attrib["speed"]) for lane in chain]
            if any(not math.isfinite(speed) or speed <= 0 for speed in speeds):
                raise ValueError("internal lane speeds must be finite and positive")
            travel = sum(float(lane.attrib["length"]) / speed for lane, speed in zip(chain, speeds))
            # Nominal passenger tail clearance, not a field intergreen certificate.
            travel += 5.0 / min(speeds) if speeds else 0.0
            clearance_by_key[key] = max(clearance_by_key.get(key, 0.0), travel)
    if keys - mapped:
        raise ValueError(f"controlled links lack physical request bindings: {sorted(keys - mapped)}")
    if not ordered_keys:
        return {"target_tls_ids": [], "expanded_junction_ids": sorted(expanded), "controllers": [], "source_protected_green_conflict_count": 0, "conflict_pairs": []}
    # Only controllers with physical conflicts need common phase boundaries.
    # Unrelated junctions must not inherit each other's unused red stages.
    controller_groups = [{tls} for tls in sorted(target_tls)]
    for first, second in sorted(conflicts):
        pair = {ordered_keys[first][0], ordered_keys[second][0]}
        joined = set().union(*(group for group in controller_groups if group & pair))
        controller_groups = [group for group in controller_groups if not group & pair] + [joined]
    link_flows = _tls_link_flows(root, route_file, sorted(target_tls)) if route_file else {}
    controllers = []
    for group in sorted(controller_groups, key=lambda value: sorted(value)):
        indices = [index for index, key in enumerate(ordered_keys) if key[0] in group]
        local_index = {index: local for local, index in enumerate(indices)}
        stages = build_protected_signal_stages(
            [{"status": "active", "official_signal_group": f"generic-link-{index:06d}", "sumo_link_index": local_index[index]} for index in indices],
            conflict_pairs={(local_index[a], local_index[b]) for a, b in conflicts if a in local_index and b in local_index},
            link_count=len(indices),
        )
        common_phases = []
        for state in stages:
            common_phases.append({"duration": green_seconds, "state": state})
            if len(stages) > 1:
                clearance = max(2, math.ceil(max((clearance_by_key.get(ordered_keys[index], 0.0) for local, index in enumerate(indices) if state[local] == "G"), default=0.0)))
                common_phases.append({"duration": yellow_seconds, "state": state.replace("G", "y")})
                common_phases.append({"duration": clearance, "state": "r" * len(state)})
        if route_file:
            transitions = [round(sum(phase["duration"] for phase in common_phases[3*i+1:3*i+3])) for i in range(len(stages))] if len(stages) > 1 else [0]
            weights = {local: link_flows.get(ordered_keys[index][0], {}).get(ordered_keys[index][1], 0.0) for local, index in enumerate(indices)}
            green_values = allocate_stage_green_seconds(stages, link_flows=weights,
                cycle_seconds=round(sum(phase["duration"] for phase in common_phases)),
                transition_seconds=transitions)
            for phase, duration in zip((phase for phase in common_phases if "G" in phase["state"]), green_values):
                phase["duration"] = duration
        for tls_id in sorted(group):
            width = max(index for controller, index in ordered_keys if controller == tls_id) + 1
            phases = []
            for common in common_phases:
                state = ["r"] * width
                for local, global_index in enumerate(indices):
                    controller, index = ordered_keys[global_index]
                    if controller == tls_id:
                        state[index] = common["state"][local]
                phases.append({"duration": common["duration"], "state": "".join(state)})
            controllers.append({"tls_id": tls_id, "state_length": width, "phases": phases})
    unsafe = 0
    for first, second in conflicts:
        first_tls, first_index = ordered_keys[first]
        second_tls, second_index = ordered_keys[second]
        if first_tls != second_tls:
            continue
        if any(len(phase.get("state", "")) > max(first_index, second_index) and phase.attrib["state"][first_index] == phase.attrib["state"][second_index] == "G" for logic in root.findall("tlLogic") if logic.get("id") == first_tls for phase in logic.findall("phase")):
            unsafe += 1
    return {
        "target_tls_ids": sorted(target_tls),
        "expanded_junction_ids": sorted(expanded),
        "controllers": controllers,
        "conflict_pairs": [[list(ordered_keys[a]), list(ordered_keys[b])] for a, b in sorted(conflicts)],
        "source_protected_green_conflict_count": unsafe,
        "green_seconds": green_seconds,
        "yellow_seconds": yellow_seconds,
        "clearance_basis": "sum of full internal-chain length/speed plus a 5m passenger tail; minimum 2 seconds",
        "coordination": "common phase boundaries and zero offset only within physically conflicting controller groups",
        "coordinated_controller_groups": [sorted(group) for group in controller_groups],
        "green_allocation": "declared_route_link_flows" if route_file else "equal_test_greens",
    }


def _signature(element: ET.Element) -> tuple:
    return (element.tag, tuple(sorted(element.attrib.items())), (element.text or "").strip(), tuple(_signature(child) for child in element))


def rebuild_topology_test_signals(
    *,
    net_file: Path | str,
    output_dir: Path | str,
    target_junction_ids: Sequence[str],
    expected_source_sha256: str | None = None,
    green_seconds: float = 15.0,
    yellow_seconds: float = 3.0,
    route_file: Path | str | None = None,
) -> dict[str, Any]:
    """Replace only target programs; keep the freshly-built topology untouched."""
    source = Path(net_file).expanduser().resolve(strict=True)
    destination = Path(output_dir).expanduser().resolve()
    if destination.exists():
        raise ValueError("output_dir must not already exist")
    source_hash = file_sha256(source)
    if expected_source_sha256 is not None and source_hash != expected_source_sha256.lower():
        raise ValueError("source network SHA-256 does not match")
    original = ET.parse(source).getroot()
    routes = Path(route_file).expanduser().resolve(strict=True) if route_file else None
    route_hash = file_sha256(routes) if routes else None
    plan = build_topology_test_signal_plan(original, target_junction_ids=target_junction_ids, green_seconds=green_seconds, yellow_seconds=yellow_seconds, route_file=routes)
    candidate = deepcopy(original)
    targets = set(plan["target_tls_ids"])
    for controller in plan["controllers"]:
        programs = [row for row in candidate.findall("tlLogic") if row.get("id") == controller["tls_id"]]
        if not programs:
            raise ValueError(f"target controller has no static program to replace: {controller['tls_id']}")
        for logic in programs:
            for child in list(logic):
                logic.remove(child)
            logic.set("type", "static")
            logic.set("offset", "0")
            for phase in controller["phases"]:
                ET.SubElement(logic, "phase", duration=f"{phase['duration']:g}", state=phase["state"])
    outside_before = [_signature(row) for row in original.findall("tlLogic") if row.get("id") not in targets]
    outside_after = [_signature(row) for row in candidate.findall("tlLogic") if row.get("id") not in targets]
    topology_before = [_signature(row) for row in original if row.tag != "tlLogic"]
    topology_after = [_signature(row) for row in candidate if row.tag != "tlLogic"]
    audit = audit_network_connection_mode(candidate, junction_ids=plan["expanded_junction_ids"], endpoint_tolerance_m=0.1)
    protected_conflicts = sum(value for key, value in audit["finding_category_counts"].items() if "protected_green_foes" in key)
    gates = {
        "source_immutable": "pass" if file_sha256(source) == source_hash else "blocked",
        "routes_immutable": "pass" if routes is None or file_sha256(routes) == route_hash else "blocked",
        "non_target_programs_unchanged": "pass" if outside_before == outside_after else "blocked",
        "all_geometry_and_connections_unchanged": "pass" if topology_before == topology_after else "blocked",
        "target_structure": "pass" if audit["structural_failure_count"] == 0 and audit["status"] != "fail" else "blocked",
        "no_concurrent_protected_foes": "pass" if protected_conflicts == 0 else "blocked",
    }
    destination.mkdir(parents=True)
    output = destination / "topology-test-signals.net.xml"
    if targets:
        ET.indent(candidate, space="    ")
        ET.ElementTree(candidate).write(output, encoding="utf-8", xml_declaration=True)
    else:
        shutil.copy2(source, output)
    report = {
        "schema": "torii.topology-test-signals/v1",
        "status": "pass" if all(value == "pass" for value in gates.values()) else "blocked",
        "signal_role": "generic_topology_test",
        "claim_status": "diagnostic-demo",
        "source_network": {"path": str(source), "sha256": source_hash},
        "route_file": {"path": str(routes), "sha256": route_hash} if routes else None,
        "candidate_network": {"path": str(output), "sha256": file_sha256(output)},
        "artifacts": {"network": {"path": str(output), "sha256": file_sha256(output)}},
        "plan": plan,
        "expanded_junction_ids": plan["expanded_junction_ids"],
        "gates": gates,
        "protected_green_conflict_count": protected_conflicts,
        "claim_boundary": "Protected generic test signals from the current SUMO conflict graph. Optional green allocation uses declared route flows. This is not historical or field signal timing. Vehicle routes and departures are unchanged. Runtime collision and completion tests remain required.",
        "sources": ["https://sumo.dlr.de/docs/Simulation/Traffic_Lights.html", "https://sumo.dlr.de/docs/Networks/SUMO_Road_Networks.html"],
    }
    manifest = destination / "manifest.json"
    manifest.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    report["manifest_file"] = str(manifest)
    return report
