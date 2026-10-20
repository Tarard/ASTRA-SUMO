"""Build road facts from official Hamburg MAP/KML and a fixed lane association."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict
from datetime import date
import hashlib
import json
from pathlib import Path
from typing import Sequence
import xml.etree.ElementTree as ET

from pyproj import Transformer
from shapely.geometry import LineString

from .artifact_io import relative_or_absolute_path, write_json_atomic
from .candidate_contracts import file_sha256
from .digital_twin import parse_mapem
from .digital_twin_mapping import MapLaneBinding, bind_map_lanes_to_network
from .hamburg_map_kml import bind_hamburg_map_kml_to_mapem, parse_hamburg_map_kml
from .network_calibration import (NetworkFactTable, centerline_overlap_metrics, evaluate_network_calibration,
                                  _coordinate_transform, _shape)
from .lane_correspondence import assign_nonmotor_lanes, diagnose_lane_match, duplicate_lane_assignments
from .hamburg_junctions.source import _official_boundary_widths


def evaluate_hamburg_calibration_snapshot(*, network: Path, official_sources: Path, output_dir: Path,
                                          target_date: str, sample_spacing_m: float, mode: str = "explore",
                                          tolerance_m: float | None = None) -> dict:
    """Run the same reference comparison for every downloaded official site."""
    manifest_bytes = official_sources.read_bytes()
    manifest = json.loads(manifest_bytes)
    if not isinstance(manifest.get("paths"), dict) or not manifest["paths"]:
        raise ValueError("The official source manifest must contain at least one site.")
    manifest_digest = hashlib.sha256(manifest_bytes).hexdigest()
    network_digest = file_sha256(network)
    verified = {}
    warnings = []
    for record in manifest["downloads"]:
        if file_sha256(Path(record["path"])) != record["sha256"]:
            if mode == "release":
                raise ValueError(f"Official source hash mismatch: {record['path']}")
            warnings.append(f"Official source hash mismatch: {record['path']}")
        verified[(record["node_id"], record["kind"], str(Path(record["path"]).resolve()))] = record["sha256"]
    for node, paths in manifest["paths"].items():
        for role in ("map_xml", "map_kml", "ocit_xml"):
            if (node, role, str(Path(paths[role]).resolve())) not in verified:
                raise ValueError(f"Site source is not bound to the download manifest: {node}/{role}")
    output_dir.mkdir(parents=True, exist_ok=False)
    reports = {}
    for node, paths in manifest["paths"].items():
        map_file, kml_file = Path(paths["map_xml"]), Path(paths["map_kml"])
        lanes, _ = parse_mapem(map_file)
        if {lane.node_id for lane in lanes} != {node}:
            raise ValueError(f"Official MAP content does not match requested node {node}.")
        bindings = bind_map_lanes_to_network(network, lanes)
        serialized = []
        for binding in bindings:
            row = asdict(binding)
            if not binding.sumo_lane:
                row["distance_m"] = None
            serialized.append(row)
        write_json_atomic(output_dir / f"{node}-automatic-correspondence.json", serialized)
        facts_file = output_dir / f"{node}-facts.json"
        # Keep one source-bound association for this comparison, including its connected lane parts.
        table = write_hamburg_network_facts(map_xml_file=map_file, map_kml_file=kml_file,
            output_file=facts_file, target_date=target_date, lane_bindings=bindings,
            sample_spacing_m=sample_spacing_m, network_file=network, tolerance_m=tolerance_m)
        report = evaluate_network_calibration(net_file=network, facts_file=facts_file,
                                             output_file=output_dir / f"{node}-scorecard.json", mode=mode)
        measured = [f for f in report["facts"] if f["kind"] == "lane_centerline"
                    and f["metrics"].get("mean_lateral_offset_m") is not None]
        coverage_measured = [f for f in report["facts"] if f["kind"] == "lane_centerline"
                             and f["metrics"].get("reference_coverage") is not None]
        reliable = [f for f in measured if f["traffic_mode"] == "motor" and f["mapping_confidence"] in {"high", "confirmed"}]
        reports[node] = {"status": report["status"], "layers": report["layers"],
                         "scorecard": report["output_file"], "facts_file": str(facts_file.resolve()), "measured_centerlines": len(measured),
                         "coverage_measured_centerlines": len(coverage_measured),
                         "motor_centerlines": report["motor_centerlines"],
                         "largest_sampled_lateral_offset_m": max((f["metrics"]["max_sampled_lateral_offset_m"]
                             for f in reliable), default=None),
                         "mean_reference_coverage": sum(f["metrics"]["reference_coverage"] for f in coverage_measured) / len(coverage_measured) if coverage_measured else None,
                         "fact_count": len(table["facts"])}
    if (file_sha256(network) != network_digest or file_sha256(official_sources) != manifest_digest
            or any(file_sha256(Path(path)) != digest for (_, _, path), digest in verified.items())):
        if mode == "release":
            raise ValueError("A frozen source changed during calibration.")
        warnings.append("A source changed during calibration. Retain the recorded input identities.")
    summary = {"schema": "astra.hamburg-network-calibration-run/v1", "status": "measured" if mode == "explore" else "review_required",
               "mode": mode, "warnings": warnings,
               "network": {"path": str(network.resolve()), "sha256": network_digest},
               "official_sources": {"path": str(official_sources.resolve()), "sha256": manifest_digest},
               "sites": reports,
               "claim_boundary": "Automatic lane associations are unreviewed. This run is diagnostic only."}
    write_json_atomic(output_dir / "run.json", summary, sort_keys=True)
    return summary


def write_hamburg_network_facts(*, map_xml_file: Path, map_kml_file: Path, output_file: Path,
                               target_date: str, lane_bindings: Sequence[MapLaneBinding],
                               sample_spacing_m: float,
                               confirmed_map_lane_ids: frozenset[str] = frozenset(),
                               tolerance_m: float | None = None, network_file: Path | None = None) -> dict:
    """Keep automatic associations unresolved until correspondence is reviewed.

    The caller supplies one fixed association for parent/candidate comparisons.
    This function does not rerun matching on a changed candidate.
    """
    if output_file.exists():
        raise ValueError("Use a new road-fact table path.")
    lanes, connections = parse_mapem(map_xml_file)
    geometry = bind_hamburg_map_kml_to_mapem(parse_hamburg_map_kml(map_kml_file), lanes, connections)
    node_id = geometry["node_id"]
    binding_by_id = {b.map_lane_id: b for b in lane_bindings if b.node_id == node_id}
    if len(binding_by_id) != sum(b.node_id == node_id for b in lane_bindings):
        raise ValueError("Lane bindings contain duplicate official lane IDs.")
    official_ids = {lane.lane_id for lane in lanes}
    if set(binding_by_id) - official_ids or confirmed_map_lane_ids - set(binding_by_id):
        raise ValueError("Lane associations or confirmations reference unknown official lanes.")
    geo_by_id = {row["lane_id"]: row for row in geometry["lanes"]}
    transform = Transformer.from_crs("EPSG:4326", "EPSG:25832", always_xy=True)
    sources = [{"id": role, "path": relative_or_absolute_path(path, output_file.parent), "sha256": file_sha256(path),
                "source_date": None, "authority": "Hamburg official MAP snapshot; field-validity date unresolved",
                "license": "Datenlizenz Deutschland - Namensnennung - 2.0"}
               for role, path in (("map_xml", map_xml_file), ("map_kml", map_kml_file))]
    widths = _official_boundary_widths({"inputs": {"map_xml": {"path": str(map_xml_file.resolve()),
                                                           "sha256": file_sha256(map_xml_file)}}})
    facts = []
    selected_lanes = {}
    network_root = ET.parse(network_file).getroot() if network_file is not None else None
    network_lanes = {}
    if network_root is not None:
        to_reference = _coordinate_transform(network_root, {"type": "projected_crs", "crs": "EPSG:25832"})
        network_lanes = {lane.get("id"): (edge, lane, LineString(to_reference(_shape(lane))))
                         for edge in network_root.findall("edge") if edge.get("function", "normal") in {"normal", "internal", "crossing"}
                         for lane in edge.findall("lane")}
    official = {lane.lane_id: {"points": [list(transform.transform(p[0], p[1], errcheck=True)) for p in geo_by_id[lane.lane_id]["coordinates"]],
                "mode": "motor" if lane.is_vehicle else "bicycle" if lane.lane_type == "bikeLane" else "pedestrian",
                "direction": (-1 if lane.is_ingress else 1) if lane.is_vehicle or lane.lane_type == "bikeLane"
                and (lane.is_ingress or lane.egress_approach) else None}
                for lane in lanes}
    selected = {key: binding.sumo_lane for key, binding in binding_by_id.items() if binding.sumo_lane}
    conflicts = duplicate_lane_assignments(selected)
    occupied = set()
    for key in sorted(selected, key=lambda key: (key not in confirmed_map_lane_ids, binding_by_id[key].distance_m, key)):
        if selected[key] in occupied and key not in confirmed_map_lane_ids:
            selected.pop(key)
        else:
            occupied.add(selected[key])
    guessed, guessed_diagnostics = assign_nonmotor_lanes(official, network_lanes, selected) if network_lanes else ({}, {})
    selected.update(guessed)
    approaches: dict[str, list] = defaultdict(list)
    for lane in lanes:
        binding = binding_by_id.get(lane.lane_id)
        confirmed = (lane.lane_id in confirmed_map_lane_ids and binding is not None
                     and bool(binding.sumo_lane) and binding.mapping_status == "active")
        status = "confirmed" if confirmed else "unresolved"
        selector = {"lane_id": selected[lane.lane_id]} if lane.lane_id in selected else {}
        row = geo_by_id[lane.lane_id]
        points = official[lane.lane_id]["points"]
        mode = official[lane.lane_id]["mode"]
        direction = official[lane.lane_id]["direction"]
        confidence = "confirmed" if confirmed else binding.mapping_confidence if binding else "none"
        method = "confirmed_binding" if confirmed else "automatic_lane_binding"
        diagnostics = guessed_diagnostics.get(lane.lane_id, {})
        if lane.lane_id in guessed:
            confidence, method = "low", "one_to_one_mode_specific_association"
        if not selector:
            method = f"not_modeled_{mode}" if mode != "motor" else "not_modeled_motor_correspondence"
            if lane.lane_id in conflicts:
                diagnostics["duplicate_seed_lane"] = binding.sumo_lane
        if selector:
            selected_lanes[lane.lane_id] = selector["lane_id"]
        prefix = f"{node_id}:lane:{lane.lane_id}"
        common = {"source_ids": ["map_xml", "map_kml"], "mapping_status": status,
                  "mapping_confidence": confidence, "mapping_method": method,
                  "traffic_mode": mode, "reference_direction": direction, "mapping_diagnostics": diagnostics,
                  "tolerance_m": tolerance_m, "selector": selector}
        line_selector = selector
        if network_root is not None and selector:
            parts, selection = _connected_lane_parts(network_root, selector["lane_id"], points, sample_spacing_m,
                direction=direction, return_diagnostics=True)
            line_selector = {"lane_ids": parts}
            if lane.is_vehicle:
                line_selector = {"lane_ids": [key for key in parts if network_lanes[key][0].get("function", "normal") == "normal"],
                                 "include_internal_paths": True}
            diagnostics["connected_part_selection"] = selection
            if lane.is_vehicle:
                alternatives_paths = []
                for shift in (-1, 1):
                    seed = selector["lane_id"]
                    other = f"{seed.rsplit('_', 1)[0]}_{int(seed.rsplit('_', 1)[1]) + shift}"
                    if other in network_lanes:
                        alternatives_paths.append({"index_shift": shift, "lane_ids": _connected_lane_parts(
                            network_root, other, points, sample_spacing_m, direction=direction)})
                confidence, alternatives, match = diagnose_lane_match(parts=parts, reference=LineString(points),
                    network_lanes=network_lanes, spacing=sample_spacing_m, direction=direction, alternative_paths=alternatives_paths)
                diagnostics.update(match)
                common.update(mapping_confidence="confirmed" if confirmed else confidence, alternatives=alternatives)
        facts.append({"id": f"{prefix}:centerline", "kind": "lane_centerline", "expected": points,
                      "sample_spacing_m": sample_spacing_m, **common, "selector": line_selector})
        if lane.is_ingress:
            endpoint = row["endpoint_b"]
            facts.append({"id": f"{prefix}:stop_line", "kind": "stop_line",
                          "expected": list(transform.transform(endpoint[0], endpoint[1], errcheck=True)), **common})
            if lane.is_vehicle or lane.lane_type == "bikeLane":
                facts.append({"id": f"{prefix}:width_at_stop_line", "kind": "lane_width",
                              **common, "expected": widths.get(lane.lane_id), "source_ids": ["map_xml"],
                              "tolerance_m": None})
        metadata = lane.permission_metadata
        # A MAP sharing bit is not a complete SUMO allow list.
        facts.append({"id": f"{prefix}:permissions", "kind": "lane_permissions",
                      "expected": metadata["allowed_vehicle_classes"] if metadata["permission_status"] == "restricted" else None,
                      "source_ids": ["map_xml"], "mapping_status": status, "selector": selector})
        if lane.is_vehicle and lane.is_ingress:
            approaches[lane.ingress_approach].append(lane)
    for approach, group in sorted(approaches.items()):
        bindings = [binding_by_id.get(lane.lane_id) for lane in group]
        selector = ({"edge_ids": sorted({b.sumo_edge for b in bindings if b and b.sumo_edge}),
                     "vehicle_class": "passenger"}
                    if all(b and b.sumo_edge for b in bindings) else {})
        facts.append({"id": f"{node_id}:approach:{approach}:lanes", "kind": "lane_count", "expected": len(group),
                      "source_ids": ["map_xml"], "mapping_status": "unresolved", "selector": selector})
    facts.extend([
        {"id": f"{node_id}:surface", "kind": "junction_surface", "expected": None, "selector": {}, "source_ids": []},
        {"id": f"{node_id}:signal_groups", "kind": "signal_group", "expected": None, "selector": {}, "source_ids": []},
    ])
    by_mode = defaultdict(list)
    for connection in connections:
        modes = {official[key]["mode"] for key in (connection.ingress_lane_id, connection.egress_lane_id)}
        mode = "pedestrian" if "pedestrian" in modes else "bicycle" if "bicycle" in modes else "motor"
        by_mode[mode].append(connection)
    for mode in ("motor", "bicycle", "pedestrian"):
        movements = by_mode[mode]
        if not movements and mode != "motor":
            continue
        complete = all(c.ingress_lane_id in selected_lanes and c.egress_lane_id in selected_lanes for c in movements)
        pairs = sorted({(selected_lanes[c.ingress_lane_id], selected_lanes[c.egress_lane_id]) for c in movements}) if complete else []
        selector = {"incoming_lane_ids": sorted({pair[0] for pair in pairs})} if pairs else {}
        if network_root is not None and selector:
            members = {network_lanes[selected_lanes[lane.lane_id]][0].get("to" if lane.is_ingress else "from")
                       for lane in lanes if official[lane.lane_id]["mode"] == mode and lane.lane_id in selected_lanes}
            members.discard(None)
            outgoing = {key for key, (edge, _, _) in network_lanes.items()
                        if edge.get("from") in members and edge.get("to") not in members} | {pair[1] for pair in pairs}
            if members:
                selector.update(junction_ids=sorted(members), outgoing_lane_ids=sorted(outgoing),
                                vehicle_class={"motor": "passenger", "bicycle": "bicycle", "pedestrian": "pedestrian"}[mode])
        facts.append({"id": f"{node_id}:movement_inventory" if mode == "motor" else f"{node_id}:{mode}:movement_inventory",
            "kind": "movement_inventory", "selector": selector, "mapping_status": "unresolved", "mapping_confidence": "low",
            "mapping_method": ("automatic_mode_specific_scoped_paths" if "junction_ids" in selector
                               else "automatic_mode_specific_lane_pairs" if selector else f"not_modeled_{mode}_movements"),
            "traffic_mode": mode, "source_ids": ["map_xml"],
            "expected": [list(pair) for pair in pairs] if selector else [[c.ingress_lane_id, c.egress_lane_id] for c in movements]})
    table = {"schema": "astra.network-facts/v1", "site_id": f"hamburg-{node_id}",
             "target_date": date.fromisoformat(target_date).isoformat(),
             "coordinate_system": {"type": "projected_crs", "crs": "EPSG:25832"},
             "sources": sources, "facts": facts}
    NetworkFactTable.model_validate(table)
    write_json_atomic(output_file, table, sort_keys=True)
    return table


def _connected_lane_parts(root, seed, points, spacing, *, direction=None, return_diagnostics=False):
    """Extend an automatic association only through saved lane connections with added coverage."""
    transform = _coordinate_transform(root, {"type": "projected_crs", "crs": "EPSG:25832"})
    lanes = {lane.get("id"): transform(_shape(lane)) for edge in root.findall("edge") if edge.get("function", "normal") in {"normal", "internal", "crossing"}
             for lane in edge.findall("lane")}
    if seed not in lanes:
        return ([seed], []) if return_diagnostics else [seed]
    from .source_movement_support import _candidate_modes, _index
    index = _index(root)
    successors, predecessors = defaultdict(list), defaultdict(list)
    for _, a, b, connection in index["movements"]:
        _, path = _candidate_modes(index, a, b, connection)
        if path and all(key in lanes for key in path):
            successors[a].append(path)
            predecessors[b].append(path)
    reference = LineString(points)
    chosen = [seed]
    first, last = seed, seed
    score = centerline_overlap_metrics(reference, [lanes[seed]], spacing, direction=direction)
    seed_mean = score["mean_lateral_offset_m"]
    if seed_mean is None:
        seed_mean = LineString(lanes[seed]).distance(reference)
    seed_lane = next(lane for edge in root.findall("edge") for lane in edge.findall("lane") if lane.get("id") == seed)
    offset_change_limit = float(seed_lane.get("width", "3.2")) / 2
    selection = []
    while True:
        options = []
        extensions = [(path[:-1], True, path[0]) for path in predecessors[first]] + [
                      (path[1:], False, path[-1]) for path in successors[last]]
        for added, prepend, candidate in extensions:
            if any(key in chosen for key in added):
                continue
            own = centerline_overlap_metrics(reference, [lanes[key] for key in added], spacing, direction=direction)
            if own["mean_lateral_offset_m"] is None or abs(own["mean_lateral_offset_m"] - seed_mean) > offset_change_limit:
                continue
            combined = centerline_overlap_metrics(reference, [lanes[key] for key in chosen + added], spacing, direction=direction)
            if combined["reference_coverage"] > score["reference_coverage"]:
                options.append((own["mean_lateral_offset_m"], candidate, combined, added, prepend))
        if not options:
            return (chosen, selection) if return_diagnostics else chosen
        _, candidate, score, added, prepend = min(options, key=lambda row: (row[0], row[1]))
        selection.append({"lane_id": candidate, "reference_coverage": score["reference_coverage"],
                          "saved_lane_path": added,
                          "maximum_mean_offset_change_m": offset_change_limit,
                          "basis": "saved_directed_connection_and_consistent_offset"})
        if prepend:
            chosen = added + chosen
            first = candidate
        else:
            chosen.extend(added)
            last = candidate
