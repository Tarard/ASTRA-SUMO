"""Select a road scope with source-supported approaches, connections and traffic boundaries."""
from collections import Counter, defaultdict
from collections.abc import Mapping
import heapq
import math
from pathlib import Path
import re
import xml.etree.ElementTree as ET

from .artifact_io import copy_file_atomic, write_json_atomic
from .candidate_contracts import file_sha256
from .command_runner import run_command
from .corridor_edit_ledger import _compare_tls_logic_semantics
from .junction_rebuild.restoration import restore_off_scope_netconvert_artifacts
from .local_network_conversion import loaded_turn_permissions, prepare_permission_preserving_input, with_geometry_precision
from .osm_access import _permission_set
from .network_geometry_freeze import compare_frozen_geometry
from .road_scope import ROAD_HIERARCHY_TIERS, road_hierarchy_tier
from .sumo_commands import run_sumo_load_audit
from .tls_reference_cleanup import build_tls_reference_cleanup_variant


def _network(root, vehicle_class):
    edges = {row.get("id"): row for row in root.findall("edge") if not row.get("function") and not row.get("id", "").startswith(":")}
    if not edges or None in edges or len(edges) != sum(not row.get("function") and not row.get("id", "").startswith(":") for row in root.findall("edge")):
        raise ValueError("Use a SUMO network with unique ordinary-road IDs.")
    active, lengths = set(), {}
    for key, edge in edges.items():
        lanes = edge.findall("lane")
        lengths[key] = max((float(row.get("length", "0")) for row in lanes), default=0)
        if not math.isfinite(lengths[key]) or lengths[key] < 0:
            raise ValueError("Road lengths must be finite and nonnegative.")
        if any(vehicle_class in _permission_set(row.attrib, network_version=root.get("version", "0.0")) for row in lanes):
            active.add(key)
    graph = {key: set() for key in active}
    for (first, _, last, _), modes in loaded_turn_permissions(root).items():
        if first in active and last in active and vehicle_class in modes:
            graph[first].add(last)
    return edges, graph, lengths


def _paths(graph, lengths, start, allowed):
    distances, previous = {start: lengths[start]}, {}
    pending = [(lengths[start], start)]
    while pending:
        distance, key = heapq.heappop(pending)
        if distance != distances[key]:
            continue
        for target in sorted(graph[key] & allowed):
            candidate = distance + lengths[target]
            if candidate < distances.get(target, math.inf):
                distances[target], previous[target] = candidate, key
                heapq.heappush(pending, (candidate, target))
    return distances, previous


def _path(previous, start, target):
    result = [target]
    while result[-1] != start:
        result.append(previous[result[-1]])
    return list(reversed(result))


def _boundaries(root, edges, graph, selected):
    incoming = defaultdict(set)
    for first, targets in graph.items():
        for last in targets:
            incoming[last].add(first)
    nodes = {row.get("id"): row for row in root.findall("junction")}
    zones = {}
    for key in sorted(selected & graph.keys()):
        for side, neighbors, endpoint in (("entry_edges", incoming[key], "from"), ("exit_edges", graph[key], "to")):
            cut = neighbors - selected
            if neighbors and not cut:
                continue
            owner = edges[key].get(endpoint)
            zone = zones.setdefault(owner, {"junction_id": owner, "entry_edges": [], "exit_edges": [],
                "cut_roads": [], "basis": [], "position": {name: nodes.get(owner, ET.Element("junction")).get(name) for name in ("x", "y")}})
            zone[side].append(key)
            zone["cut_roads"] = sorted(set(zone["cut_roads"]) | cut)
            zone["basis"] = sorted(set(zone["basis"]) | {"scope_cut" if cut else "source_model_boundary"})
    return [zones[key] for key in sorted(zones)]


def _extend_entry_context(root, edges, graph, lengths, selected, reasons, minimum, vehicle_class):
    reverse = {key: set() for key in graph}
    turnarounds = {(row.get("from"), row.get("to")) for row in root.findall("connection") if row.get("dir") == "t"}
    for first, targets in graph.items():
        for last in targets:
            if (first, last) not in turnarounds:
                reverse[last].add(first)
    available = {key: min(float(lane.get("length", "0")) for lane in edges[key].findall("lane")
                         if vehicle_class in _permission_set(lane.attrib, network_version=root.get("version", "0.0")))
                 for key in graph}
    extensions = []
    while True:
        before = set(selected)
        entries = {key for row in _boundaries(root, edges, graph, selected) for key in row["entry_edges"]}
        for entry in sorted(key for key in entries if available[key] < minimum):
            for predecessor in sorted(reverse[entry] - selected):
                if predecessor in selected:
                    continue
                distances, previous = _paths(reverse, lengths, predecessor, set(graph) - selected)
                candidates = []
                for key in distances:
                    if available[key] < minimum:
                        continue
                    path = _path(previous, predecessor, key) + [entry]
                    if any(value.startswith("-") and value[1:] in path for value in path):
                        continue
                    candidates.append(key)
                if not candidates:
                    continue
                first = min(candidates, key=lambda key: (distances[key], key))
                path = list(reversed(_path(previous, predecessor, first)))
                extensions.append({"entry_edge": entry, "source_path": path + [entry],
                                   "added_edges": path, "new_entry_lane_length_m": available[first]})
                for key in path:
                    selected.add(key)
                    reasons[key] = ["entry_insertion_context"]
        if selected == before:
            break
    entries = {key for row in _boundaries(root, edges, graph, selected) for key in row["entry_edges"]}
    unresolved = [{"edge_id": key, "minimum_lane_length_m": available[key],
                   "reason": "no_remaining_source_path_to_a_sufficiently_long_entry"}
                  for key in sorted(entries) if available[key] < minimum]
    return {"minimum_lane_length_m": minimum, "extensions": extensions, "unresolved_entries": unresolved,
            "claim_boundary": "Retained upstream source context only. Lane length does not certify queue storage, insertion throughput, or field demand."}


def _signal_scope_changes(source, candidate, comparison):
    edges = {row.get("id"): row for row in source.findall("edge") if not row.get("function")}
    nodes = {row.get("id"): row for row in candidate.findall("junction")}
    owners = defaultdict(set)
    for row in source.findall("connection"):
        if row.get("tl") and row.get("from") in edges:
            owners[row.get("tl")].add(edges[row.get("from")].get("to"))
    groups = {key: [] for key in ("removed_with_junction", "removed_at_dead_end", "changed_retained",
                                  "added", "removed_at_retained_junction", "unresolved_removed_owner")}
    for key, change in comparison["unexpected_changes"].items():
        owned = owners[key]
        if change.get("candidate"):
            kind = "changed_retained" if change.get("source") else "added"
        elif not owned:
            kind = "unresolved_removed_owner"
        elif not (owned & nodes.keys()):
            kind = "removed_with_junction"
        elif all(nodes[node].get("type") == "dead_end" for node in owned & nodes.keys()):
            kind = "removed_at_dead_end"
        else:
            kind = "removed_at_retained_junction"
        groups[kind].append(key)
    return {key: sorted(values) for key, values in groups.items()}


def select_road_hierarchy(root, *, keep_tier="major", protected_edge_ids=(), protected_way_ids=(),
                          vehicle_class="passenger", required_movements=None, protected_edge_evidence=None,
                          entry_min_lane_length_m=None):
    if keep_tier not in ROAD_HIERARCHY_TIERS:
        raise ValueError("Choose major, collector, or local roads.")
    if not isinstance(vehicle_class, str) or _permission_set({"allow": vehicle_class}) != {vehicle_class}:
        raise ValueError("Choose one supported vehicle class.")
    if entry_min_lane_length_m is not None and (not math.isfinite(entry_min_lane_length_m) or entry_min_lane_length_m <= 0):
        raise ValueError("The entry lane-length requirement must be positive and finite.")
    edges, graph, lengths = _network(root, vehicle_class)
    protected = set(protected_edge_ids)
    declared_edges = set(protected)
    evidence = protected_edge_evidence or {}
    if not isinstance(evidence, Mapping) or any(not isinstance(value, Mapping) or not str(value.get("basis", "")).strip() for value in evidence.values()):
        raise ValueError("Protection evidence must map edges to records with a basis.")
    if set(evidence) - protected:
        raise ValueError("Protection evidence must address explicitly protected edges.")
    if protected - edges.keys():
        raise ValueError("A protected road is absent from the source network.")
    ways = set(map(str, protected_way_ids))
    matched_ways = set()
    reasons, tiers, protection = {}, {}, {}
    for key, edge in edges.items():
        tier = tiers[key] = road_hierarchy_tier(edge.get("type", ""))
        keep = ["class_threshold"] if tier in ROAD_HIERARCHY_TIERS[:ROAD_HIERARCHY_TIERS.index(keep_tier) + 1] else []
        if tier == "unknown":
            keep.append("unclassified_source_type_review")
        match = re.fullmatch(r"-?(\d+)(?:#\d+)?", key)
        if match and match[1] in ways:
            matched_ways.add(match[1])
            protected.add(key)
            protection.setdefault(key, []).append({"basis": "declared_OSM_way", "way_id": match[1]})
        if key in declared_edges:
            protection.setdefault(key, []).append(evidence.get(key, {"basis": "declared_edge"}))
        if key in protected:
            keep.append("protected_road")
        if keep:
            reasons[key] = keep
    if ways - matched_ways:
        raise ValueError("A protected OSM way is absent from the source network.")
    core = set(reasons) & graph.keys()
    seed_nodes = {edge.get(side) for key, edge in edges.items() if key in reasons for side in ("from", "to")}
    # Keep one original approach at a cut; vehicles then enter before the main junction.
    for key, edge in edges.items():
        if key not in reasons and {edge.get("from"), edge.get("to")} & seed_nodes:
            reasons[key] = ["boundary_approach"]
    selected = set(reasons)
    full_paths, repairs = {}, []
    boundaries = _boundaries(root, edges, graph, selected)
    explicit = required_movements is not None
    if explicit and any(len(pair) != 2 or any(key not in selected or key not in graph for key in pair) for pair in required_movements):
        raise ValueError("Required movements must address two retained roads serving the selected vehicle class.")
    unserved = []
    if not explicit:
        entries = sorted({key for row in boundaries for key in row["entry_edges"]})
        exits = sorted({key for row in boundaries for key in row["exit_edges"]})
        pairs = []
        for start in sorted(set(entries) & core):
            full_paths[start] = _paths(graph, lengths, start, set(graph))
            pairs.extend((start, last) for last in sorted(set(exits) & core) if last in full_paths[start][0])
        pairs.extend((first, last) for first in sorted(selected & graph.keys()) for last in sorted(graph[first] & selected))
        reverse = {key: set() for key in graph}
        for first, targets in graph.items():
            for last in targets:
                reverse[last].add(first)
        # Side approaches use retained local turns. Never repair them through excluded streets.
        for direction, endpoints, network in (("entry", entries, graph), ("exit", exits, reverse)):
            for endpoint in sorted(set(endpoints) - core):
                distances, _ = _paths(network, lengths, endpoint, selected)
                choices = core & distances.keys()
                if not choices:
                    unserved.append({"direction": direction, "edge_id": endpoint, "reason": "no_retained_core_access_local_turns_only"})
                    continue
                nearest = min(choices, key=lambda key: (distances[key], key))
                pairs.append((endpoint, nearest) if direction == "entry" else (nearest, endpoint))
    else:
        pairs = required_movements
    pairs = sorted(set(map(tuple, pairs)))
    turnarounds = {(row.get("from"), row.get("to")) for row in root.findall("connection") if row.get("dir") == "t"}
    for start in sorted({pair[0] for pair in pairs}):
        if start not in full_paths:
            full_paths[start] = _paths(graph, lengths, start, set(graph))
        old_distances, old_previous = full_paths[start]
        distances, _ = _paths(graph, lengths, start, selected)
        for _, target in (pair for pair in pairs if pair[0] == start):
            if target not in old_distances:
                raise ValueError("A required movement is not supported by the source network.")
            if target not in distances:
                path = _path(old_previous, start, target)
                added = set(path) - selected
                has_turnaround = any(pair in turnarounds for pair in zip(path, path[1:]))
                without_turnaround = True
                if has_turnaround:
                    straight_graph = {key: {last for last in targets if (key, last) not in turnarounds} for key, targets in graph.items()}
                    without_turnaround = target in _paths(straight_graph, lengths, start, set(graph))[0]
                repairs.append({"from": start, "to": target, "added_edges": sorted(added), "source_path": path,
                    "source_shortest_path_has_turnaround": has_turnaround,
                    "source_path_without_turnaround_exists": without_turnaround})
                for key in added:
                    reasons[key] = ["required_connection"]
                selected.update(added)
                distances, _ = _paths(graph, lengths, start, selected)
    entry_context = None
    if entry_min_lane_length_m is not None:
        entry_context = _extend_entry_context(root, edges, graph, lengths, selected, reasons,
                                              entry_min_lane_length_m, vehicle_class)
        boundaries = _boundaries(root, edges, graph, selected)
    movements = []
    for start in sorted({pair[0] for pair in pairs}):
        distances, previous = _paths(graph, lengths, start, selected)
        for first, last in pairs:
            if first != start:
                continue
            baseline = full_paths[start][0][last]
            source_path = _path(full_paths[start][1], start, last)
            selected_path = _path(previous, start, last)
            movements.append({"from": first, "to": last, "source_length_m": baseline,
                "selected_length_m": distances[last], "added_length_m": distances[last] - baseline,
                "detour_ratio": distances[last] / baseline if baseline else None,
                "source_has_turnaround": any(pair in turnarounds for pair in zip(source_path, source_path[1:])),
                "selected_has_turnaround": any(pair in turnarounds for pair in zip(selected_path, selected_path[1:]))})
    worst = max(movements, key=lambda row: row["detour_ratio"] or 0, default=None)
    return {"schema": "astra.road-hierarchy-selection/v1", "keep_tier": keep_tier, "vehicle_class": vehicle_class,
        "source_road_count": len(edges), "selected_road_count": len(selected),
        "source_tier_counts": dict(Counter(tiers.values())), "protected_edge_ids": sorted(protected),
        "selected_edge_ids": sorted(selected), "excluded_edge_ids": sorted(edges.keys() - selected),
        "roads": [{"edge_id": key, "type": edge.get("type"), "name": edge.get("name"), "tier": tiers[key],
                   "selected": key in selected, "reasons": reasons.get(key, ["below_selected_tier"]),
                   **({"protection_evidence": protection[key]} if key in protection else {})} for key, edge in sorted(edges.items())],
        "boundaries": boundaries, "final_cut_attachments": _boundaries(root, edges, graph, selected),
        "entry_insertion_context": entry_context,
        "unserved_source_boundaries": unserved, "connection_repairs": repairs, "movements": movements, "worst_detour": worst,
        "movement_basis": "explicit" if explicit else "fixed main-road boundary pairs and retained local turns",
        "length_basis": "ordinary-road logical lengths; internal junction paths are excluded",
        "claim_boundary": "Simulation scope and road-level reachability for the declared vehicle class. Boundary traffic rates, lane-level driving and field signal timing are not established."}


def build_road_hierarchy_variant(*, net_file, output_dir, keep_tier="major", protected_edge_ids=(), protected_way_ids=(),
                                 vehicle_class="passenger", netconvert_binary="netconvert", sumo_binary="sumo", protected_edge_evidence=None,
                                 entry_min_lane_length_m=None):
    source = Path(net_file).resolve(strict=True)
    output = Path(output_dir).resolve()
    if output.exists() or source.is_relative_to(output):
        raise ValueError("Use a new output directory separate from the source network.")
    before_hash = file_sha256(source)
    root = ET.parse(source).getroot()
    plan = select_road_hierarchy(root, keep_tier=keep_tier, protected_edge_ids=protected_edge_ids,
        protected_way_ids=protected_way_ids, vehicle_class=vehicle_class, protected_edge_evidence=protected_edge_evidence,
        entry_min_lane_length_m=entry_min_lane_length_m)
    output.mkdir(parents=True)
    write_json_atomic(output / "selection.json", plan)
    write_json_atomic(output / "boundaries.json", {"zones": plan["boundaries"], "traffic_rates": "not_assigned"})
    keep = output / "selected-roads.txt"
    keep.write_text("\n".join(plan["selected_edge_ids"]) + "\n", encoding="utf-8")
    candidate = output / "selected.net.xml"
    if not plan["selected_edge_ids"] or not plan["excluded_edge_ids"]:
        report = {"schema": "astra.road-hierarchy-variant/v1", "status": "no_effect", "promotion_authority": False,
            "source": {"path": str(source), "sha256": before_hash}, "source_unchanged": file_sha256(source) == before_hash,
            "selection_file": str(output / "selection.json"), "boundaries_file": str(output / "boundaries.json"),
            "source_road_count": plan["source_road_count"], "selected_road_count": plan["selected_road_count"],
            "native_conversion_count": 0, "reason": "no_roads_selected" if not plan["selected_edge_ids"] else "all_roads_retained",
            "report_file": str(output / "report.json"), "claim_boundary": plan["claim_boundary"]}
        write_json_atomic(output / "report.json", report)
        return report
    cleanup = build_tls_reference_cleanup_variant(source, output_dir=output / "tls-reference-cleanup")
    if cleanup["status"] != "pass":
        report = {"schema": "astra.road-hierarchy-variant/v1", "status": "blocked", "promotion_authority": False,
            "source": {"path": str(source), "sha256": before_hash}, "source_unchanged": file_sha256(source) == before_hash,
            "selection_file": str(output / "selection.json"), "boundaries_file": str(output / "boundaries.json"),
            "source_road_count": plan["source_road_count"], "selected_road_count": plan["selected_road_count"],
            "source_signal_cleanup": cleanup, "native_conversion_count": 0,
            "reason": "source_has_unresolved_signal_references", "report_file": str(output / "report.json")}
        write_json_atomic(output / "report.json", report)
        return report
    effective = Path(cleanup["effective_net_file"])
    prepared = prepare_permission_preserving_input(effective, output / "permission-input.net.xml")
    command = with_geometry_precision([str(netconvert_binary), "--sumo-net-file", prepared["path"],
        "--keep-edges.input-file", str(keep), "--offset.disable-normalization", "true",
        "--output-file", str(candidate)])
    # Preload pruning avoids the known postload crash or stall on the frozen Ring1 source.
    proposal = output / "preload-proposal.net.xml"
    command[command.index("--output-file") + 1] = str(proposal)
    native = run_command(command, cwd=output, timeout_seconds=240)
    attempt = {"command": native.to_dict()}
    successful = native.status == "pass" and native.returncode == 0
    if proposal.is_file():
        attempt["output"] = {"path": str(proposal), "sha256": file_sha256(proposal),
            "status": "complete" if successful else "incomplete_native_output"}
        if successful:
            try:
                if ET.parse(proposal).getroot().tag != "net":
                    raise ValueError("Native output root is not a SUMO network.")
            except (ET.ParseError, ValueError) as error:
                successful = False
                attempt["output"].update(status="invalid_native_output", error=str(error))
    attempts = [attempt]
    if successful and proposal.is_file():
        copy_file_atomic(proposal, candidate)
    report = {"schema": "astra.road-hierarchy-variant/v1", "status": "blocked", "promotion_authority": False,
        "source": {"path": str(source), "sha256": before_hash}, "selection_file": str(output / "selection.json"),
        "source_road_count": plan["source_road_count"], "selected_road_count": plan["selected_road_count"],
        "boundaries_file": str(output / "boundaries.json"), "permission_input": prepared, "command": native.to_dict(),
        "native_attempts": attempts, "native_conversion_count": len(attempts),
        "source_signal_cleanup": cleanup,
        "normalized_full_network": {"path": str(effective), "sha256": file_sha256(effective)},
        "claim_boundary": plan["claim_boundary"]}
    if native.status == "pass" and native.returncode == 0 and candidate.is_file():
        removed = set(plan["excluded_edge_ids"])
        old_edges = {row.get("id"): row for row in root.findall("edge") if not row.get("function")}
        mutable_nodes = {old_edges[key].get(side) for key in removed for side in ("from", "to")}
        mutable_edges = removed | {key for key, edge in old_edges.items() if {edge.get("from"), edge.get("to")} & mutable_nodes}
        restoration = restore_off_scope_netconvert_artifacts(source_file=effective, target_file=candidate,
            mutable_junction_ids=mutable_nodes, mutable_edge_ids=mutable_edges, expand_mutable_edge_endpoints=False)
        after = ET.parse(candidate).getroot()
        edges, graph, lengths = _network(after, vehicle_class)
        _, source_graph, _ = _network(root, vehicle_class)
        original_local_turns = {(first, last) for first, targets in source_graph.items() for last in targets
                                if first in edges and last in edges}
        candidate_local_turns = {(first, last) for first, targets in graph.items() for last in targets}
        local_turns = {"lost": sorted(original_local_turns - candidate_local_turns),
                       "added": sorted(candidate_local_turns - original_local_turns)}
        path_cache = {}
        lost = []
        for row in plan["movements"]:
            start, target = row["from"], row["to"]
            if start in graph and start not in path_cache:
                path_cache[start] = _paths(graph, lengths, start, set(graph))[0]
            if target not in path_cache.get(start, {}):
                lost.append(row)
        old_lanes = {lane.get("id"): lane for edge in old_edges.values() for lane in edge.findall("lane")}
        changed_permissions = [lane.get("id") for edge in edges.values() for lane in edge.findall("lane")
            if lane.get("id") not in old_lanes or _permission_set(lane.attrib, network_version=after.get("version", "0.0")) !=
            _permission_set(old_lanes[lane.get("id")].attrib, network_version=root.get("version", "0.0"))]
        load = run_sumo_load_audit(net_file=candidate, output_dir=output / "sumo-load", sumo_binary=str(sumo_binary))
        checks = {"source_unchanged": file_sha256(source) == before_hash,
                  "selected_roads_realized": set(edges) == set(plan["selected_edge_ids"]),
                  "required_movements_kept": not lost, "retained_lane_permissions": not changed_permissions,
                  "retained_local_turns": not local_turns["lost"],
                  "retained_road_priorities": all(edge.get("priority") == old_edges[key].get("priority") for key, edge in edges.items() if key in old_edges),
                  "sumo_load": load["status"] == "pass"}
        report.update(status=("measured" if removed else "no_effect") if all(checks.values()) else "blocked",
            candidate={"path": str(candidate), "sha256": file_sha256(candidate)}, checks=checks,
            lost_movements=lost, retained_local_turn_changes=local_turns,
            changed_lane_permissions=changed_permissions, restoration=restoration, sumo_load=load,
            source_road_count=plan["source_road_count"], selected_road_count=len(edges),
            warnings=[] if restoration["status"] == "pass" else ["Inspect the recorded off-scope restoration findings."])
        report["retained_road_geometry"] = compare_frozen_geometry(source_file=source, candidate_file=candidate,
            mutable_junction_ids={row.get("id") for row in root.findall("junction")}, mutable_edge_ids=removed)
        report["signal_program_changes"] = _compare_tls_logic_semantics(root, after)
        report["signal_scope_changes"] = _signal_scope_changes(root, after, report["signal_program_changes"])
        if any(values for key, values in report["signal_scope_changes"].items() if key not in {"removed_with_junction", "removed_at_dead_end"}):
            report["warnings"].append("Scope extraction changed retained control or has unresolved controller ownership. Inspect signal_scope_changes before calibration.")
        if plan["source_tier_counts"].get("unknown"):
            report["warnings"].append("Unknown source road types were retained. Inspect their recorded classification before further pruning.")
        if plan["unserved_source_boundaries"]:
            report["warnings"].append("Some side approaches serve only retained local turns and have no retained core access. Inspect their boundary roles before assigning traffic.")
        if local_turns["added"]:
            report["warnings"].append("Scope extraction added local turns. Inspect their legality before calibration.")
        if entry_min_lane_length_m is not None:
            entry_ids = {key for zone in plan["boundaries"] for key in zone["entry_edges"]}
            inventory = []
            for key in sorted(entry_ids):
                lane_lengths = [float(lane.get("length")) for lane in edges[key].findall("lane")
                    if vehicle_class in _permission_set(lane.attrib, network_version=after.get("version", "0.0"))]
                minimum = min(lane_lengths, default=None)
                inventory.append({"edge_id": key, "minimum_lane_length_m": minimum,
                    "meets_requested_length": minimum is not None and minimum >= entry_min_lane_length_m})
            report["entry_insertion_geometry"] = inventory
            if any(not row["meets_requested_length"] for row in inventory):
                report["warnings"].append("Some retained entries remain below the requested lane length. Inspect entry_insertion_geometry before assigning demand.")
    report["report_file"] = str(output / "report.json")
    report["source_unchanged"] = file_sha256(source) == before_hash
    if not candidate.is_file():
        report["reason"] = "native_scope_conversion_failed"
        report["failed_outputs"] = [row["output"] for row in attempts if row.get("output")]
    write_json_atomic(output / "report.json", report)
    return report
