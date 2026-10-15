"""Build road facts from official Hamburg MAP/KML and a fixed lane association."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict
from datetime import date
import hashlib
import json
from pathlib import Path
from typing import Sequence

from pyproj import Transformer

from .artifact_io import relative_or_absolute_path, write_json_atomic
from .candidate_contracts import file_sha256
from .digital_twin import parse_mapem
from .digital_twin_mapping import MapLaneBinding, bind_map_lanes_to_network
from .hamburg_map_kml import bind_hamburg_map_kml_to_mapem, parse_hamburg_map_kml
from .network_calibration import NetworkFactTable, evaluate_network_calibration


def evaluate_hamburg_calibration_snapshot(*, network: Path, official_sources: Path, output_dir: Path,
                                          target_date: str, sample_spacing_m: float) -> dict:
    """Run the same reference comparison for every downloaded official site."""
    manifest_bytes = official_sources.read_bytes()
    manifest = json.loads(manifest_bytes)
    if not isinstance(manifest.get("paths"), dict) or not manifest["paths"]:
        raise ValueError("The official source manifest must contain at least one site.")
    manifest_digest = hashlib.sha256(manifest_bytes).hexdigest()
    network_digest = file_sha256(network)
    verified = {}
    for record in manifest["downloads"]:
        if file_sha256(Path(record["path"])) != record["sha256"]:
            raise ValueError(f"Official source hash mismatch: {record['path']}")
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
        write_hamburg_network_facts(map_xml_file=map_file, map_kml_file=kml_file, output_file=facts_file,
                                   target_date=target_date, lane_bindings=bindings, sample_spacing_m=sample_spacing_m)
        report = evaluate_network_calibration(net_file=network, facts_file=facts_file,
                                             output_file=output_dir / f"{node}-scorecard.json")
        measured = [f for f in report["facts"] if f["kind"] == "lane_centerline" and f["metrics"]]
        reports[node] = {"status": report["status"], "layers": report["layers"],
                         "scorecard": report["output_file"], "measured_centerlines": len(measured),
                         "largest_sampled_reference_offset_m": max((f["metrics"]["max_sampled_reference_offset_m"]
                                                                    for f in measured), default=None)}
    if (file_sha256(network) != network_digest or file_sha256(official_sources) != manifest_digest
            or any(file_sha256(Path(path)) != digest for (_, _, path), digest in verified.items())):
        raise ValueError("A frozen source changed during calibration.")
    summary = {"schema": "astra.hamburg-network-calibration-run/v1", "status": "review_required",
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
                               tolerance_m: float | None = None) -> dict:
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
    facts = []
    approaches: dict[str, list] = defaultdict(list)
    for lane in lanes:
        binding = binding_by_id.get(lane.lane_id)
        confirmed = (lane.lane_id in confirmed_map_lane_ids and binding is not None
                     and bool(binding.sumo_lane) and binding.mapping_status == "active")
        status = "confirmed" if confirmed else "unresolved"
        selector = {"lane_id": binding.sumo_lane} if binding and binding.sumo_lane else {}
        row = geo_by_id[lane.lane_id]
        points = [list(transform.transform(p[0], p[1], errcheck=True)) for p in row["coordinates"]]
        prefix = f"{node_id}:lane:{lane.lane_id}"
        common = {"source_ids": ["map_xml", "map_kml"], "mapping_status": status,
                  "tolerance_m": tolerance_m, "selector": selector}
        facts.append({"id": f"{prefix}:centerline", "kind": "lane_centerline", "expected": points,
                      "sample_spacing_m": sample_spacing_m, **common})
        if lane.is_ingress:
            endpoint = row["endpoint_b"]
            facts.append({"id": f"{prefix}:stop_line", "kind": "stop_line",
                          "expected": list(transform.transform(endpoint[0], endpoint[1], errcheck=True)), **common})
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
        {"id": f"{node_id}:movement_inventory", "kind": "movement_inventory", "selector": {},
         "mapping_status": "unresolved", "source_ids": ["map_xml"],
         "expected": [[c.ingress_lane_id, c.egress_lane_id] for c in connections]},
    ])
    table = {"schema": "astra.network-facts/v1", "site_id": f"hamburg-{node_id}",
             "target_date": date.fromisoformat(target_date).isoformat(),
             "coordinate_system": {"type": "projected_crs", "crs": "EPSG:25832"},
             "sources": sources, "facts": facts}
    NetworkFactTable.model_validate(table)
    write_json_atomic(output_file, table, sort_keys=True)
    return table
