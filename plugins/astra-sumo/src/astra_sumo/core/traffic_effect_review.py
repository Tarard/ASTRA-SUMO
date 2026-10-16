"""Order development review by measured model effects and unresolved evidence."""

from __future__ import annotations

from collections import Counter, defaultdict
import math
from pathlib import Path
from xml.etree import ElementTree as ET

from .artifact_io import write_json_atomic
from .candidate_contracts import file_sha256


_CONFLICTS = {
    "protected_green_movement_conflict",
    "permissive_conflict_without_yield_relation",
}
_GEOMETRY = {
    "path_endpoint_gap", "path_width_transition_centerline_offset", "lane_rank_jump",
    "crossed_lane_mapping", "turn_lane_order_inversion",
    "left_turn_not_innermost_lane", "right_turn_not_outermost_lane",
}
_SUPPLY = {"destination_lane_merge", "ambiguous_target_lane_fanout"}


def rank_traffic_effects(connection, *, safety=None, owners=None, changes=(), parent_safety=None):
    """Keep every input finding as a reference in one deterministic review group."""
    members = []
    owners = owners or {}
    tls_sites = defaultdict(set)

    def add(audit, pointer, category, site, severity, *, priority=4,
            effect="unknown", basis="unresolved", witness=None, parent_relation="not_compared"):
        members.append({"audit": audit, "pointer": pointer, "category": category,
                        "site_ids": sorted(site), "original_severity": severity,
                        "priority": priority, "effect": effect, "basis": basis,
                        "witness": witness, "parent_relation": parent_relation})

    def connection_rows(record, base, site):
        for key in ("structural_failures", "review_findings"):
            for index, text in enumerate(record.get(key, [])):
                category = text.split(":")[1]
                priority, effect, basis = 4, "unknown", "unresolved"
                if category == "internal_path_mode_permission_empty":
                    priority, effect, basis = 2, "movement_access", "model_permission_intersection_empty"
                elif category in _GEOMETRY:
                    priority, effect, basis = 5, "geometry", "geometry_only_route_effect_unmeasured"
                elif category in _SUPPLY:
                    effect, basis = "lane_supply", "lane_assignment_requires_reference"
                add("connection", f"{base}/{key}/{index}", category, site, key,
                    priority=priority, effect=effect, basis=basis, witness=text)

    for index, junction in enumerate(connection.get("junctions", [])):
        connection_rows(junction.get("connection_mode_audit", {}),
                        f"/junctions/{index}/connection_mode_audit", [junction["junction_id"]])
        for key in ("structural_failures", "review_findings"):
            for text in junction.get("tls_link_binding_audit", {}).get(key, []):
                tls_sites[text].add(junction["junction_id"])
    # Global TLS lists already contain the per-junction TLS findings.
    connection_rows(connection.get("tls_link_binding_audit", {}), "/tls_link_binding_audit", [])
    for member in members:
        if member["pointer"].startswith("/tls_link_binding_audit/"):
            member["site_ids"] = sorted(tls_sites[member["witness"]])
    for index, text in enumerate(connection.get("configuration_failures", [])):
        add("connection", f"/configuration_failures/{index}", text.split(":")[1], [],
            "structural_failures", witness=text)

    if safety is not None:
        parent_signatures = {row["witness_signature"] for row in parent_safety["findings"]} if parent_safety else set()
        conflicts = {row["conflict_id"]: row for row in safety["conflict_graph"]["conflicts"]}
        for index, row in enumerate(safety["findings"]):
            category, witness = row["category"], row["witness"]
            conflict = conflicts.get(witness.get("conflict_id"))
            sites = set() if conflict else set(owners.get(row["subject_id"], ()))
            if conflict:
                for key in ("movement_a_id", "movement_b_id"):
                    sites.update(owners.get(conflict[key], ()))
            priority, effect, basis = 4, "unknown", "coverage_or_control_unresolved"
            if conflict:
                effect, basis = "conflict", f"{conflict['certainty']}_geometry_control_requires_review"
                if category in _CONFLICTS and conflict["certainty"] == "confirmed":
                    priority, basis = 1, "model_geometry_and_signal_conflict"
            elif category == "movement_path_permission_empty":
                priority, effect, basis = 2, "movement_access", "model_permission_intersection_empty"
            add("safety", f"/findings/{index}", category, sites, row["severity"],
                priority=priority, effect=effect, basis=basis, witness=witness,
                parent_relation=("present_in_parent" if row["witness_signature"] in parent_signatures
                                 else "not_present_in_parent") if parent_safety is not None else "not_compared")

    for index, change in enumerate(changes):
        add("comparison", f"/changes/{index}", change["category"], change["site_ids"], "change",
            priority=change["priority"], effect=change["effect"], basis=change["basis"], witness=change)

    groups = defaultdict(list)
    for member in members:
        location = tuple(member["site_ids"]) or (member["audit"], member["pointer"])
        key = (member["priority"], member["effect"], member["basis"],
               location, member["category"], member["original_severity"], member["parent_relation"])
        groups[key].append(member)
    ranked = []
    for key, rows in sorted(groups.items()):
        ranked.append({"priority": key[0], "effect": key[1], "basis": key[2],
                       "site_ids": rows[0]["site_ids"], "category": key[4], "original_severity": key[5],
                       "parent_relation": key[6],
                       "member_count": len(rows), "representative": rows[0],
                       "members": [{"audit": row["audit"], "pointer": row["pointer"]} for row in rows]})
    expected = (connection.get("structural_failure_count", 0) + connection.get("review_finding_count", 0)
                + len(safety["findings"] if safety is not None else []) + len(changes))
    if len(members) != expected:
        raise ValueError("Traffic review finding counts do not match the original audits")
    blocked = connection.get("status") in {"fail", "blocked"} or (
        safety is not None and safety.get("status") in {"fail", "blocked"})
    return {"schema": "astra.traffic-effect-review/v1",
            "status": "blocked" if blocked else "review_required" if members else "pass",
            "automatic_promotion_gate": "blocked", "ranking_role": "development_review_order_only",
            "input_finding_count": expected, "grouped_finding_count": len(members),
            "review_group_count": len(ranked), "lost_finding_count": 0,
            "priority_counts": dict(sorted(Counter(row["priority"] for row in members).items())),
            "effect_counts": dict(sorted(Counter(row["effect"] for row in members).items())),
            "parent_relation_counts": dict(sorted(Counter(row["parent_relation"] for row in members).items())),
            "false_alarm_rate": None, "route_effect": "not_measured",
            "capacity_veh_per_hour": None, "ranked_groups": ranked,
            "limitations": ["Priority is review order, not a safety rating or field defect label.",
                            "Geometry and envelope overlap do not prove a route loss or collision.",
                            "Lane supply is a capacity proxy. No saturation flow is estimated.",
                            "Unknown effects and original audit blockers remain unresolved.",
                            "No human labels are supplied with this review to estimate a false-alarm rate."]}


def compare_model_access(before, after, *, vehicle_class="passenger"):
    """Compare explicit lane identities and local permission-aware paths, without exterior detours."""
    from sumolib.net.lane import SUMO_VEHICLE_CLASSES

    if vehicle_class not in SUMO_VEHICLE_CLASSES:
        raise ValueError("Unknown SUMO vehicle class")

    def permits(element):
        allowed, denied = set(element.get("allow", "").split()), set(element.get("disallow", "").split())
        return (not allowed or "all" in allowed or vehicle_class in allowed) and not (
            "all" in denied or vehicle_class in denied)

    def inventory(root):
        edges = {row.get("id"): row for row in root.findall("edge")}
        lanes, lane_keys, internal = {}, {}, set()
        supply, endpoints = {}, {}
        for edge_id, edge in edges.items():
            is_internal = edge.get("function") == "internal" or edge_id.startswith(":")
            for lane in edge.findall("lane"):
                lane_id = lane.attrib["id"]
                lanes[lane_id] = lane
                lane_keys[(edge_id, lane.attrib["index"])] = lane_id
                if is_internal:
                    internal.add(lane_id)
            if not is_internal and edge.get("function", "normal") == "normal":
                supply[edge_id] = sorted(row.attrib["id"] for row in edge.findall("lane") if permits(row))
                endpoints[edge_id] = [edge.get("from", ""), edge.get("to", "")]
        graph, movements = defaultdict(set), []
        for row in root.findall("connection"):
            source = lane_keys.get((row.get("from"), row.get("fromLane")))
            target = lane_keys.get((row.get("to"), row.get("toLane")))
            if not source or not target or not permits(row):
                continue
            via = row.get("via") or target
            if via not in lanes or not all(permits(lanes[key]) for key in (source, via, target)):
                continue
            graph[source].add(via)
            if row.get("from") in supply and row.get("to") in supply:
                movements.append((row.get("from"), row.get("to"), source, target, via))
        access = defaultdict(set)
        for from_edge, to_edge, source, target, via in movements:
            pending, seen = [via], set()
            while pending:
                lane = pending.pop()
                if lane in seen:
                    continue
                seen.add(lane)
                if lane == target:
                    access[(from_edge, to_edge)].add(source)
                    break
                if lane in internal:
                    pending.extend(graph[lane] - seen)
        return supply, endpoints, access

    old_supply, old_ends, old_access = inventory(before)
    new_supply, new_ends, new_access = inventory(after)
    changes = []
    for edge in sorted(old_supply.keys() | new_supply.keys()):
        old, new = old_supply.get(edge, []), new_supply.get(edge, [])
        if old != new:
            comparable = (edge in old_supply and edge in new_supply and old_ends[edge] == new_ends[edge]
                          and len(old) != len(new))
            changes.append({"category": "permitted_lane_supply_changed" if comparable else "lane_identity_correspondence_required",
                            "effect": "lane_supply" if comparable else "unknown", "priority": 3 if comparable else 4,
                            "basis": "shared_edge_identity_capacity_proxy_only" if comparable else "changed_lane_or_edge_identity",
                            "site_ids": new_ends.get(edge, old_ends.get(edge)),
                            "edge_id": edge, "before_lane_ids": old, "after_lane_ids": new,
                            "before_lane_count": len(old), "after_lane_count": len(new)})
    for pair in sorted(old_access.keys() | new_access.keys()):
        old, new = sorted(old_access.get(pair, ())), sorted(new_access.get(pair, ()))
        if old != new:
            comparable = all(edge in old_supply and edge in new_supply and old_ends[edge] == new_ends[edge] for edge in pair)
            changes.append({"category": "local_movement_access_changed" if comparable else "movement_correspondence_required",
                            "effect": "movement_access" if comparable else "unknown", "priority": 2 if comparable else 4,
                            "basis": "permission_aware_local_lane_paths_no_exterior_detour" if comparable else "changed_edge_or_junction_identity",
                            "site_ids": [new_ends.get(pair[0], old_ends.get(pair[0], ["", ""]))[1]],
                            "from_edge": pair[0], "to_edge": pair[1], "before_source_lanes": old,
                            "after_source_lanes": new, "route_effect": "not_measured"})
    old_lanes = {row.get("id"): row for edge in before.findall("edge") if edge.get("id") in old_supply
                 for row in edge.findall("lane")}
    new_lanes = {row.get("id"): row for edge in after.findall("edge") if edge.get("id") in new_supply
                 for row in edge.findall("lane")}
    permitted = {lane for lanes in [*old_supply.values(), *new_supply.values()] for lane in lanes}

    def dimension(lane, key):
        value = lane.get(key)
        if value is None:
            return None
        number = float(value)
        if not math.isfinite(number) or number <= 0:
            raise ValueError("Lane operating dimensions must be finite and positive")
        return number

    for lane in sorted(old_lanes.keys() & new_lanes.keys() & permitted):
        old = {key: dimension(old_lanes[lane], key) for key in ("width", "speed", "length")}
        new = {key: dimension(new_lanes[lane], key) for key in old}
        if old != new:
            changes.append({"category": "lane_operating_dimensions_changed", "effect": "lane_supply", "priority": 3,
                            "basis": "operating_dimensions_capacity_proxy_only", "site_ids": [],
                            "lane_id": lane, "before": old, "after": new})
    return changes


def build_network_traffic_effect_review(*, net_file, output_dir, parent_net_file=None, vehicle_class="passenger"):
    """Create a fresh development review. Frozen research artifacts remain separate."""
    from ..corridor.canonicalizer import canonicalize_net_xml_file
    from ..corridor.conflict_graph import audit_independent_movement_safety
    from ..corridor.enums import TrafficSide
    from .connection_mode_audit import audit_network_connection_mode
    from sumolib.net.lane import SUMO_VEHICLE_CLASSES

    if vehicle_class not in SUMO_VEHICLE_CLASSES:
        raise ValueError("Unknown SUMO vehicle class")
    candidate = Path(net_file).resolve(strict=True)
    parent = Path(parent_net_file).resolve(strict=True) if parent_net_file else None
    destination = Path(output_dir).resolve()
    if any(path.is_relative_to(destination) for path in (candidate, parent) if path is not None):
        raise ValueError("Review output directory must not contain an input network")
    if destination.exists():
        raise FileExistsError("Use a new traffic review output directory")
    hashes = {path: file_sha256(path) for path in (candidate, parent) if path is not None}
    root = ET.parse(candidate).getroot()
    connection = audit_network_connection_mode(root)
    snapshot = canonicalize_net_xml_file(candidate, traffic_side=TrafficSide(connection["traffic_side"]))
    safety = audit_independent_movement_safety(snapshot).model_dump(mode="json", by_alias=True)
    parent_safety = None
    if parent is not None:
        parent_root = ET.parse(parent).getroot()
        side = TrafficSide.LEFT if parent_root.get("lefthand", "false").lower() in {"true", "1", "yes"} else TrafficSide.RIGHT
        parent_snapshot = canonicalize_net_xml_file(parent, traffic_side=side)
        parent_safety = audit_independent_movement_safety(parent_snapshot).model_dump(mode="json", by_alias=True)
    raw_sites = defaultdict(list)
    for junction, cell in snapshot.raw_id_maps["junction_to_physical_cell"].items():
        raw_sites[cell].append(junction)
    owners = {row.stable_entity_id: sorted({node for cell in row.owner_physical_cell_ids
                                          for node in raw_sites.get(cell, [cell])}) for row in snapshot.entities}
    changes = compare_model_access(ET.parse(parent).getroot(), root, vehicle_class=vehicle_class) if parent else []
    report = rank_traffic_effects(connection, safety=safety, owners=owners, changes=changes, parent_safety=parent_safety)
    report.update(vehicle_class=vehicle_class, parent_comparison="shared_edge_and_lane_ids_only" if parent else "not_run",
                  original_audit_statuses={"connection": connection["status"], "safety": safety["status"]},
                  conflict_geometry_counts=dict(Counter(row["certainty"] for row in safety["conflict_graph"]["conflicts"])))
    if any(file_sha256(path) != digest for path, digest in hashes.items()):
        raise ValueError("Input network changed during traffic review")
    destination.mkdir(parents=True)
    artifacts = {}
    payloads = {"connection": connection, "safety": safety, "comparison": {"changes": changes}}
    if parent_safety is not None:
        payloads["parent_safety"] = parent_safety
    for name, payload in payloads.items():
        path = destination / f"{name}.json"
        write_json_atomic(path, payload, sort_keys=True)
        artifacts[name] = {"path": str(path), "sha256": file_sha256(path)}
    report["inputs"] = {role: {"path": str(path), "sha256": hashes[path]}
                        for role, path in (("candidate", candidate), ("parent", parent)) if path is not None}
    report["audits"] = artifacts
    report_file = destination / "traffic-effect-review.json"
    write_json_atomic(report_file, report, sort_keys=True)
    manifest = {"schema": "astra.traffic-effect-review-manifest/v1", "inputs": report["inputs"],
                "audits": artifacts, "report": {"path": str(report_file), "sha256": file_sha256(report_file)}}
    write_json_atomic(destination / "manifest.json", manifest, sort_keys=True)
    return {**report, "report_file": str(report_file), "manifest_file": str(destination / "manifest.json")}
