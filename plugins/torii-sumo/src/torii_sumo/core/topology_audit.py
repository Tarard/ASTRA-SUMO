from __future__ import annotations

import csv
import hashlib
import json
import math
from collections import Counter, deque
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path
from typing import Any
import xml.etree.ElementTree as ET

from .modal_aggregation_policy import classify_cluster_modal_policy, classify_edge_modal_role
from .candidate_contracts import file_sha256
from .artifact_io import write_json_atomic
from .osm_network import _net_xy_to_latlon
from .road_corridor import (
    enrich_clusters_with_corridor_audit,
    graph_from_topology_inputs,
    parse_osm_way_context,
)


def audit_topology_fragmentation(
    *,
    net_file: Path,
    output_dir: Path,
    prefix: str = "topology_audit",
    cluster_radius_m: float = 30.0,
    min_cluster_nodes: int = 3,
    osm_file: Path | None = None,
) -> dict[str, Any]:
    output_dir = output_dir.resolve()
    try:
        output_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return _failure(f"could not create topology output directory: {type(exc).__name__}: {exc}")
    report_file = _short_output_path(output_dir, prefix, "topology_audit.json")
    manifest_file = _short_output_path(output_dir, prefix, "topology_audit.manifest.json")

    def finish(report: Mapping[str, Any]) -> dict[str, Any]:
        return _write_topology_outcome(
            report=report,
            report_file=report_file,
            manifest_file=manifest_file,
            net_file=net_file,
            osm_file=osm_file,
        )

    if cluster_radius_m <= 0:
        return finish(_failure("cluster_radius_m must be positive"))
    if min_cluster_nodes <= 1:
        return finish(_failure("min_cluster_nodes must be greater than 1"))
    if not net_file.exists():
        return finish(_failure(f"net file does not exist: {net_file}"))

    try:
        junctions, edges = _read_network_graph(net_file)
    except (OSError, ET.ParseError, KeyError, ValueError) as exc:
        return finish(_failure(f"{type(exc).__name__}: {exc}"))

    xy_to_latlon = _coordinate_converter(net_file)
    clusters = _dense_clusters(
        junctions,
        radius_m=cluster_radius_m,
        min_cluster_nodes=min_cluster_nodes,
        xy_to_latlon=xy_to_latlon,
        edges=edges,
    )
    connection_cell_candidates = _connection_cell_candidates(junctions, edges)
    warnings = []
    corridor_audit_status = "not_run"
    corridor_error = ""
    if osm_file is not None:
        try:
            osm_context = parse_osm_way_context(osm_file)
            clusters = enrich_clusters_with_corridor_audit(
                clusters,
                graph=graph_from_topology_inputs(junctions, edges),
                osm_context=osm_context,
            )
            corridor_audit_status = "pass"
        except (OSError, ET.ParseError, KeyError, ValueError) as exc:
            corridor_audit_status = "failed"
            corridor_error = f"{type(exc).__name__}: {exc}"
            warnings.append(f"corridor audit failed: {corridor_error}")

    canonical_cells = _canonical_cell_records(clusters)
    osm_context_sha256 = _sha256_file(osm_file) if osm_file is not None else ""

    clusters_file = _short_output_path(output_dir, prefix, "dense_junction_clusters.csv")
    connection_cells_file = _short_output_path(output_dir, prefix, "topology_connection_candidates.csv")
    try:
        _write_clusters_csv(clusters_file, clusters)
        _write_connection_cells_csv(connection_cells_file, connection_cell_candidates)
    except OSError as exc:
        return finish(_failure(f"could not write topology CSV artifacts: {type(exc).__name__}: {exc}"))

    status = "blocked" if clusters else "pass"
    if clusters:
        warnings.append(f"topology audit found {len(clusters)} suspicious dense junction cluster(s)")

    physical_shape_counts = dict(Counter(cluster["physical_intersection_shape"] for cluster in clusters))
    modal_decision_counts = dict(
        Counter(cluster.get("modal_aggregation_decision", "review_required") for cluster in clusters)
    )
    modal_review_action_counts = dict(
        Counter(cluster.get("modal_review_action", "review_vehicle_core_boundary") for cluster in clusters)
    )
    corridor_decision_counts = dict(
        Counter(
            str(cluster.get("corridor_decision", "not_available"))
            for cluster in clusters
        )
    )
    physical_intersection_candidate_count = sum(
        1
        for cluster in clusters
        if cluster["physical_intersection_shape"] in {"cross", "t_or_y"}
        and float(cluster["physical_intersection_score"]) >= 0.6
    )
    report = {
        "schema": "torii.topology_audit.v2",
        "status": status,
        "claim_status": "blocked" if clusters else "diagnostic-demo",
        "topology_fragmentation_status": "needs_review" if clusters else "pass",
        "net_file": str(net_file.resolve()),
        "net_sha256": file_sha256(net_file),
        "osm_file": str(osm_file.resolve()) if osm_file is not None else "",
        "topology_osm_context_sha256": osm_context_sha256,
        "output_dir": str(output_dir),
        "cluster_radius_m": cluster_radius_m,
        "min_cluster_nodes": min_cluster_nodes,
        "junction_count": len(junctions),
        "suspicious_cluster_count": len(clusters),
        "topology_canonical_cell_count": len(canonical_cells),
        "topology_canonical_cell_records": canonical_cells,
        "topology_canonical_unidentified_cell_count": sum(
            1 for cell in canonical_cells if not cell["corridor_signatures"] and not cell["identity_node_ids"]
        ),
        "topology_connection_cell_candidate_count": len(connection_cell_candidates),
        "topology_connection_cell_candidates_file": str(connection_cells_file),
        "topology_connection_cell_candidates": connection_cell_candidates,
        "max_cluster_node_count": max((cluster["node_count"] for cluster in clusters), default=0),
        "aggregation_decision_counts": dict(Counter(cluster["aggregation_decision"] for cluster in clusters)),
        "physical_intersection_shape_counts": physical_shape_counts,
        "physical_intersection_candidate_count": physical_intersection_candidate_count,
        "modal_policy_status": "pass",
        "modal_decision_counts": modal_decision_counts,
        "modal_review_action_counts": modal_review_action_counts,
        "corridor_audit_status": corridor_audit_status,
        "corridor_audit_error": corridor_error,
        "corridor_decision_counts": corridor_decision_counts,
        "max_corridor_partition_count": max((cluster.get("corridor_partition_count", 0) for cluster in clusters), default=0),
        "max_intersection_cell_count": max(
            (cluster.get("corridor_intersection_cell_count", 0) for cluster in clusters),
            default=0,
        ),
        "junction_aggregation_candidate_count": sum(
            1
            for cluster in clusters
            if cluster["aggregation_decision"] in {"join", "needs_map_review"}
            and cluster.get("corridor_decision") != "reject"
            and cluster.get("modal_aggregation_decision") not in {"never_join", "protected_terminal", "shape_support"}
        ),
        "junction_aggregation_blocked_by_corridor_count": sum(
            1
            for cluster in clusters
            if cluster["aggregation_decision"] in {"join", "needs_map_review"}
            and cluster.get("corridor_decision") == "reject"
        ),
        "junction_aggregation_blocked_by_modal_count": sum(
            1
            for cluster in clusters
            if cluster["aggregation_decision"] in {"join", "needs_map_review"}
            and cluster.get("modal_aggregation_decision") in {"never_join", "protected_terminal", "shape_support"}
        ),
        "clusters_file": str(clusters_file),
        "report_file": str(report_file),
        "manifest_file": str(manifest_file),
        "suspicious_clusters": clusters,
        "warnings": warnings,
    }
    return finish(report)


def compare_topology_canonical_cells(
    candidate_report: Mapping[str, Any],
    reference_report: Mapping[str, Any],
    *,
    max_match_distance_m: float = 100.0,
) -> dict[str, Any]:
    """Compare physical topology cells using shared OSM identity evidence.

    The legacy aggregate counts mix distance-chain clusters, modal filtering,
    and corridor evidence. This comparison is intentionally cell-level: a
    candidate cluster may contain more SUMO subdivision nodes, but it must map
    to exactly one reference cell with shared OSM node/corridor evidence and
    compatible physical/modal semantics.
    """

    candidate_cells = list(candidate_report.get("topology_canonical_cell_records", []) or [])
    reference_cells = list(reference_report.get("topology_canonical_cell_records", []) or [])
    candidate_context = str(candidate_report.get("topology_osm_context_sha256", ""))
    reference_context = str(reference_report.get("topology_osm_context_sha256", ""))
    if not candidate_context or not reference_context:
        return {
            "status": "blocked",
            "reason": "canonical_topology_requires_shared_osm_context",
            "matched_cell_count": 0,
            "unmatched_candidate_cells": [str(cell.get("cluster_id", "")) for cell in candidate_cells],
            "unmatched_reference_cells": [str(cell.get("cluster_id", "")) for cell in reference_cells],
            "approach_mismatches": [],
            "modal_mismatches": [],
            "graph_mismatches": [],
        }
    if candidate_context != reference_context:
        return {
            "status": "blocked",
            "reason": "candidate_reference_osm_context_mismatch",
            "candidate_osm_context_sha256": candidate_context,
            "reference_osm_context_sha256": reference_context,
            "matched_cell_count": 0,
            "unmatched_candidate_cells": [str(cell.get("cluster_id", "")) for cell in candidate_cells],
            "unmatched_reference_cells": [str(cell.get("cluster_id", "")) for cell in reference_cells],
            "approach_mismatches": [],
            "modal_mismatches": [],
            "graph_mismatches": [],
        }

    def distance(left: Mapping[str, Any], right: Mapping[str, Any]) -> float:
        return math.hypot(
            (float(left.get("centroid_lat", 0.0)) - float(right.get("centroid_lat", 0.0))) * 111_000.0,
            (float(left.get("centroid_lon", 0.0)) - float(right.get("centroid_lon", 0.0))) * 74_000.0,
        )

    pair_candidates: list[tuple[tuple[int, int, int, int, float], int, int]] = []
    for candidate_index, candidate in enumerate(candidate_cells):
        candidate_nodes = set(str(item) for item in candidate.get("identity_node_ids", []) or [])
        candidate_signatures = set(str(item) for item in candidate.get("corridor_signatures", []) or [])
        for reference_index, reference in enumerate(reference_cells):
            reference_nodes = set(str(item) for item in reference.get("identity_node_ids", []) or [])
            reference_signatures = set(str(item) for item in reference.get("corridor_signatures", []) or [])
            node_overlap = len(candidate_nodes & reference_nodes)
            signature_overlap = len(candidate_signatures & reference_signatures)
            if not node_overlap and not signature_overlap:
                continue
            separation = distance(candidate, reference)
            if separation > max_match_distance_m:
                continue
            shape_match = int(
                str(candidate.get("physical_intersection_shape", ""))
                == str(reference.get("physical_intersection_shape", ""))
            )
            pair_candidates.append(
                (
                    (int(bool(node_overlap)), node_overlap, signature_overlap, shape_match, -separation),
                    candidate_index,
                    reference_index,
                )
            )
    pair_candidates.sort(reverse=True)
    matched_candidate_indices: set[int] = set()
    matched_reference_indices: set[int] = set()
    matched_pairs: list[dict[str, Any]] = []
    for score, candidate_index, reference_index in pair_candidates:
        if candidate_index in matched_candidate_indices or reference_index in matched_reference_indices:
            continue
        candidate = candidate_cells[candidate_index]
        reference = reference_cells[reference_index]
        matched_candidate_indices.add(candidate_index)
        matched_reference_indices.add(reference_index)
        candidate_signatures = set(str(item) for item in candidate.get("corridor_signatures", []) or [])
        reference_signatures = set(str(item) for item in reference.get("corridor_signatures", []) or [])
        matched_pairs.append(
            {
                "candidate_cluster_id": str(candidate.get("cluster_id", "")),
                "reference_cluster_id": str(reference.get("cluster_id", "")),
                "distance_m": round(distance(candidate, reference), 3),
                "node_overlap_count": len(
                    set(str(item) for item in candidate.get("identity_node_ids", []) or [])
                    & set(str(item) for item in reference.get("identity_node_ids", []) or [])
                ),
                "corridor_signature_overlap_count": len(candidate_signatures & reference_signatures),
                "physical_shape_match": str(candidate.get("physical_intersection_shape", ""))
                == str(reference.get("physical_intersection_shape", "")),
            }
        )

    approach_mismatches = []
    modal_mismatches = []
    graph_mismatches = []
    for pair in matched_pairs:
        candidate = next(
            cell for cell in candidate_cells if str(cell.get("cluster_id", "")) == pair["candidate_cluster_id"]
        )
        reference = next(
            cell for cell in reference_cells if str(cell.get("cluster_id", "")) == pair["reference_cluster_id"]
        )
        if abs(int(candidate.get("approach_count", 0)) - int(reference.get("approach_count", 0))) > 1:
            approach_mismatches.append(
                {
                    **pair,
                    "candidate_approach_count": int(candidate.get("approach_count", 0)),
                    "reference_approach_count": int(reference.get("approach_count", 0)),
                }
            )
        if str(candidate.get("modal_primary_role", "")) != str(reference.get("modal_primary_role", "")):
            modal_mismatches.append(
                {
                    **pair,
                    "candidate_modal_primary_role": str(candidate.get("modal_primary_role", "")),
                    "reference_modal_primary_role": str(reference.get("modal_primary_role", "")),
                }
            )
        if not pair["physical_shape_match"]:
            graph_mismatches.append(
                {
                    **pair,
                    "candidate_physical_intersection_shape": str(candidate.get("physical_intersection_shape", "")),
                    "reference_physical_intersection_shape": str(reference.get("physical_intersection_shape", "")),
                }
            )

    unmatched_candidate = [
        str(cell.get("cluster_id", ""))
        for index, cell in enumerate(candidate_cells)
        if index not in matched_candidate_indices
    ]
    unmatched_reference = [
        str(cell.get("cluster_id", ""))
        for index, cell in enumerate(reference_cells)
        if index not in matched_reference_indices
    ]
    status = "pass" if not unmatched_candidate and not unmatched_reference and not approach_mismatches and not modal_mismatches and not graph_mismatches else "blocked"
    return {
        "status": status,
        "reason": "canonical_topology_cells_are_one_to_one" if status == "pass" else "canonical_topology_cell_mismatch",
        "candidate_osm_context_sha256": candidate_context,
        "reference_osm_context_sha256": reference_context,
        "candidate_cell_count": len(candidate_cells),
        "reference_cell_count": len(reference_cells),
        "matched_cell_count": len(matched_pairs),
        "matched_pairs": matched_pairs,
        "unmatched_candidate_cells": unmatched_candidate,
        "unmatched_reference_cells": unmatched_reference,
        "approach_mismatches": approach_mismatches,
        "modal_mismatches": modal_mismatches,
        "graph_mismatches": graph_mismatches,
    }


def _canonical_cell_records(clusters: list[dict[str, Any]]) -> list[dict[str, Any]]:
    records = []
    for cluster in clusters:
        identity_nodes: set[str] = set()
        for raw_node_id in cluster.get("node_ids", []) or []:
            node_id = str(raw_node_id)
            if node_id.startswith("cluster_"):
                identity_nodes.update(token for token in node_id.removeprefix("cluster_").split("_") if token.isdigit())
            elif node_id.isdigit():
                identity_nodes.add(node_id)
        records.append(
            {
                "cluster_id": str(cluster.get("cluster_id", "")),
                "identity_node_ids": sorted(identity_nodes),
                "corridor_signatures": sorted(
                    str(item)
                    for item in cluster.get("corridor_intersection_cell_signatures", []) or []
                    if str(item)
                ),
                "centroid_lat": float(cluster.get("centroid_lat", 0.0) or 0.0),
                "centroid_lon": float(cluster.get("centroid_lon", 0.0) or 0.0),
                "physical_intersection_shape": str(cluster.get("physical_intersection_shape", "")),
                "approach_count": int(cluster.get("approach_count", 0) or 0),
                "modal_primary_role": str(cluster.get("modal_primary_role", "")),
                "traffic_light_node_count": int(cluster.get("traffic_light_node_count", 0) or 0),
            }
        )
    return records


def _sha256_file(path: Path | None) -> str:
    if path is None or not path.exists():
        return ""
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError:
        return ""
    return digest.hexdigest()


def _failure(error: str) -> dict[str, Any]:
    return {
        "status": "fail",
        "claim_status": "construction-invalid",
        "topology_fragmentation_status": "construction-invalid",
        "error": error,
    }


def _write_topology_outcome(
    *,
    report: Mapping[str, Any],
    report_file: Path,
    manifest_file: Path,
    net_file: Path,
    osm_file: Path | None,
) -> dict[str, Any]:
    persisted = dict(report)
    persisted.setdefault("schema", "torii.topology_audit.v2")
    persisted.setdefault("net_file", str(net_file.resolve()))
    if net_file.is_file():
        persisted.setdefault("net_sha256", file_sha256(net_file))
    persisted.setdefault("osm_file", str(osm_file.resolve()) if osm_file is not None else "")
    persisted["report_file"] = str(report_file)
    persisted["manifest_file"] = str(manifest_file)
    write_json_atomic(report_file, persisted)

    artifact_candidates: list[tuple[Path, str]] = []
    if net_file.is_file():
        artifact_candidates.append((net_file, "topology_net"))
    if osm_file is not None and osm_file.is_file():
        artifact_candidates.append((osm_file, "topology_osm_context"))
    for key, kind in (
        ("clusters_file", "dense_junction_clusters"),
        ("topology_connection_cell_candidates_file", "topology_connection_candidates"),
    ):
        value = persisted.get(key)
        if value:
            artifact_candidates.append((Path(str(value)), kind))
    artifact_candidates.append((report_file, "topology_report"))

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
        "schema": "torii.topology_manifest.v2",
        "status": persisted.get("status", "fail"),
        "claim_status": persisted.get("claim_status", "construction-invalid"),
        "topology_fragmentation_status": persisted.get(
            "topology_fragmentation_status",
            "construction-invalid",
        ),
        "net_file": persisted.get("net_file", ""),
        "net_sha256": persisted.get("net_sha256", ""),
        "osm_file": persisted.get("osm_file", ""),
        "topology_osm_context_sha256": persisted.get("topology_osm_context_sha256", ""),
        "artifacts": artifacts,
    }
    write_json_atomic(manifest_file, manifest)
    return persisted


def _short_output_path(output_dir: Path, prefix: str, suffix: str) -> Path:
    """Keep generated audit artifacts writable on Windows' legacy path limits."""
    candidate = output_dir / f"{prefix}_{suffix}"
    if len(str(candidate.resolve())) < 239:
        return candidate
    digest = hashlib.sha1(str(candidate).encode("utf-8")).hexdigest()[:10]
    return output_dir / f"p_{digest}_{suffix}"


def _read_network_graph(net_file: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    root = ET.parse(net_file).getroot()
    junctions = []
    for junction in root.findall("junction"):
        junction_id = junction.attrib["id"]
        junction_type = junction.attrib.get("type", "")
        if junction_id.startswith(":") or junction_type == "internal":
            continue
        junctions.append(
            {
                "id": junction_id,
                "type": junction_type,
                "x": float(junction.attrib["x"]),
                "y": float(junction.attrib["y"]),
            }
        )
    edges = []
    for edge in root.findall("edge"):
        edge_id = edge.attrib.get("id", "")
        if edge_id.startswith(":") or edge.attrib.get("function") == "internal":
            continue
        from_node = edge.attrib.get("from")
        to_node = edge.attrib.get("to")
        if not from_node or not to_node:
            continue
        lanes = edge.findall("lane")
        shape = _edge_shape(edge, lanes)
        edges.append(
            {
                "id": edge_id,
                "from": from_node,
                "to": to_node,
                "type": edge.attrib.get("type", ""),
                "function": edge.attrib.get("function", ""),
                "allow": edge.attrib.get("allow", ""),
                "disallow": edge.attrib.get("disallow", ""),
                "name": edge.attrib.get("name", ""),
                "lane_count": len(lanes),
                "length": _edge_length(shape, lanes),
                "shape": shape,
            }
        )
    return junctions, edges


def _dense_clusters(
    junctions: list[dict[str, Any]],
    *,
    radius_m: float,
    min_cluster_nodes: int,
    xy_to_latlon: Callable[[float, float], tuple[float, float]] | None = None,
    edges: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    neighbors = {index: set() for index in range(len(junctions))}
    for left in range(len(junctions)):
        for right in range(left + 1, len(junctions)):
            if _distance(junctions[left], junctions[right]) <= radius_m:
                neighbors[left].add(right)
                neighbors[right].add(left)

    remaining = set(range(len(junctions)))
    clusters = []
    while remaining:
        start = remaining.pop()
        component = {start}
        queue: deque[int] = deque([start])
        while queue:
            current = queue.popleft()
            for neighbor in neighbors[current]:
                if neighbor in remaining:
                    remaining.remove(neighbor)
                    component.add(neighbor)
                    queue.append(neighbor)
        if len(component) >= min_cluster_nodes:
            clusters.append(
                _cluster_summary(junctions, component, radius_m, xy_to_latlon=xy_to_latlon, edges=edges or [])
            )
    clusters.sort(key=lambda cluster: (-cluster["node_count"], cluster["centroid_x"], cluster["centroid_y"]))
    for index, cluster in enumerate(clusters, start=1):
        cluster["cluster_id"] = f"C{index:03d}"
    return clusters


def _connection_cell_candidates(
    junctions: list[dict[str, Any]],
    edges: list[dict[str, Any]],
    *,
    short_edge_max_length_m: float = 12.0,
    min_external_vehicle_approaches: int = 3,
) -> list[dict[str, Any]]:
    junction_by_id = {str(junction["id"]): junction for junction in junctions}
    short_vehicle_edges = [
        edge
        for edge in edges
        if float(edge.get("length") or 0.0) <= short_edge_max_length_m
        and classify_edge_modal_role(edge)["modal_primary_role"] == "vehicle_core"
    ]
    graph: dict[str, set[str]] = {}
    for edge in short_vehicle_edges:
        left = str(edge["from"])
        right = str(edge["to"])
        graph.setdefault(left, set()).add(right)
        graph.setdefault(right, set()).add(left)

    candidates = []
    for component in _connected_node_components(graph):
        if len(component) < 2:
            continue
        boundary_edges = [
            edge
            for edge in edges
            if (str(edge["from"]) in component) ^ (str(edge["to"]) in component)
            and classify_edge_modal_role(edge)["modal_primary_role"] == "vehicle_core"
        ]
        if len(boundary_edges) < min_external_vehicle_approaches:
            continue
        internal_edges = [
            edge
            for edge in edges
            if str(edge["from"]) in component and str(edge["to"]) in component
        ]
        centroid_x = _mean(float(junction_by_id[node]["x"]) for node in component if node in junction_by_id)
        centroid_y = _mean(float(junction_by_id[node]["y"]) for node in component if node in junction_by_id)
        candidates.append(
            {
                "cell_id": f"TC{len(candidates) + 1:03d}",
                "node_ids": sorted(component),
                "centroid_x": round(centroid_x, 3),
                "centroid_y": round(centroid_y, 3),
                "internal_edge_ids": sorted(str(edge["id"]) for edge in internal_edges),
                "boundary_edge_ids": sorted(str(edge["id"]) for edge in boundary_edges),
                "external_vehicle_approach_count": len(boundary_edges),
                "connection_cell_decision": "needs_review",
                "reason": "short connected vehicle-core topology cell has multiple external approaches",
            }
        )
    return candidates


def _connected_node_components(graph: dict[str, set[str]]) -> list[set[str]]:
    remaining = set(graph)
    components = []
    while remaining:
        start = remaining.pop()
        component = {start}
        stack = [start]
        while stack:
            node = stack.pop()
            for neighbor in graph.get(node, set()):
                if neighbor in remaining:
                    remaining.remove(neighbor)
                    component.add(neighbor)
                    stack.append(neighbor)
        components.append(component)
    return components


def _mean(values: Iterable[float]) -> float:
    items = list(values)
    return sum(items) / len(items) if items else 0.0


def _cluster_summary(
    junctions: list[dict[str, Any]],
    component: set[int],
    radius_m: float,
    *,
    xy_to_latlon: Callable[[float, float], tuple[float, float]] | None,
    edges: list[dict[str, Any]],
) -> dict[str, Any]:
    nodes = [junctions[index] for index in sorted(component, key=lambda item: junctions[item]["id"])]
    centroid_x = sum(node["x"] for node in nodes) / len(nodes)
    centroid_y = sum(node["y"] for node in nodes) / len(nodes)
    max_pair_distance = 0.0
    for left in range(len(nodes)):
        for right in range(left + 1, len(nodes)):
            max_pair_distance = max(max_pair_distance, _distance(nodes[left], nodes[right]))
    lat, lon, coordinate_status = _cluster_latlon(centroid_x, centroid_y, xy_to_latlon)
    junction_by_id = {str(junction["id"]): junction for junction in junctions}
    graph = _cluster_graph_summary(nodes, edges, junction_by_id)
    return {
        "cluster_id": "",
        "node_count": len(nodes),
        "node_ids": [str(node["id"]) for node in nodes],
        "node_types": [str(node["type"]) for node in nodes],
        "centroid_x": round(centroid_x, 3),
        "centroid_y": round(centroid_y, 3),
        "centroid_lat": round(lat, 7),
        "centroid_lon": round(lon, 7),
        "coordinate_status": coordinate_status,
        "map_review_source": "Google Maps default map",
        "google_maps_url": _google_maps_default_url(lat, lon),
        "optional_google_maps_satellite_url": _google_maps_satellite_url(lat, lon),
        "manual_correction_status": "needs_map_review",
        "suggested_correction_action": "compare the Google Maps road/intersection footprint before joining SUMO junctions",
        **graph,
        "max_pair_distance_m": round(max_pair_distance, 3),
        "cluster_radius_m": radius_m,
    }


def _distance(left: dict[str, Any], right: dict[str, Any]) -> float:
    return math.hypot(float(left["x"]) - float(right["x"]), float(left["y"]) - float(right["y"]))


def _cluster_graph_summary(
    nodes: list[dict[str, Any]],
    edges: list[dict[str, Any]],
    junction_by_id: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    node_ids = {str(node["id"]) for node in nodes}
    traffic_light_count = sum(1 for node in nodes if str(node["type"]) == "traffic_light")
    internal_edges = []
    boundary_edges = []
    external_junction_ids = set()
    connected_pairs = set()
    endpoint_pair_counts: dict[tuple[str, str], int] = {}
    internal_length_total = 0.0
    internal_length_max = 0.0

    for edge in edges:
        from_node = str(edge["from"])
        to_node = str(edge["to"])
        from_inside = from_node in node_ids
        to_inside = to_node in node_ids
        if from_inside and to_inside:
            internal_edges.append(edge)
            connected_pairs.add(_node_pair_label(from_node, to_node))
            endpoint_pair = tuple(sorted((from_node, to_node)))
            endpoint_pair_counts[endpoint_pair] = endpoint_pair_counts.get(endpoint_pair, 0) + 1
            length = float(edge.get("length") or 0.0)
            internal_length_total += length
            internal_length_max = max(internal_length_max, length)
        elif from_inside or to_inside:
            boundary_edges.append(edge)
            external_junction_ids.add(to_node if from_inside else from_node)

    overlap_pair_count = sum((count * (count - 1)) // 2 for count in endpoint_pair_counts.values() if count > 1)
    physical_shape = _physical_intersection_shape_score(
        nodes=nodes,
        internal_edges=internal_edges,
        boundary_edges=boundary_edges,
        junction_by_id=junction_by_id,
    )
    risk_flags = _cluster_risk_flags(
        node_count=len(nodes),
        traffic_light_count=traffic_light_count,
        internal_edge_count=len(internal_edges),
        boundary_edge_count=len(boundary_edges),
        approach_count=len(external_junction_ids),
        overlap_pair_count=overlap_pair_count,
    )
    aggregation_score = _reference_free_aggregation_score(
        node_count=len(nodes),
        traffic_light_count=traffic_light_count,
        internal_edges=internal_edges,
        boundary_edges=boundary_edges,
        approach_count=len(external_junction_ids),
        internal_edge_max_length=internal_length_max,
        overlap_pair_count=overlap_pair_count,
        physical_shape=physical_shape,
    )
    modal_policy = classify_cluster_modal_policy(
        internal_edges=internal_edges,
        boundary_edges=boundary_edges,
    )
    return {
        "internal_edge_ids": sorted(str(edge["id"]) for edge in internal_edges),
        "boundary_edge_ids": sorted(str(edge["id"]) for edge in boundary_edges),
        "external_junction_ids": sorted(external_junction_ids),
        "connected_node_pairs": sorted(connected_pairs),
        "internal_edge_count": len(internal_edges),
        "boundary_edge_count": len(boundary_edges),
        "approach_count": len(external_junction_ids),
        "direct_connected_node_pair_count": len(connected_pairs),
        "traffic_light_node_count": traffic_light_count,
        "internal_edge_total_length_m": round(internal_length_total, 3),
        "internal_edge_max_length_m": round(internal_length_max, 3),
        "internal_edge_overlap_pair_count": overlap_pair_count,
        "aggregation_recommendation": _aggregation_recommendation(
            internal_edge_count=len(internal_edges),
            boundary_edge_count=len(boundary_edges),
            approach_count=len(external_junction_ids),
        ),
        **aggregation_score,
        **modal_policy,
        "risk_flags": sorted(set(risk_flags) | set(modal_policy["modal_risk_flags"])),
    }


def _node_pair_label(from_node: str, to_node: str) -> str:
    left, right = sorted((from_node, to_node))
    return f"{left}<->{right}"


def _cluster_risk_flags(
    *,
    node_count: int,
    traffic_light_count: int,
    internal_edge_count: int,
    boundary_edge_count: int,
    approach_count: int,
    overlap_pair_count: int,
) -> list[str]:
    flags = ["map_review_required"]
    if internal_edge_count == 0:
        flags.append("no_internal_edges")
    if boundary_edge_count == 0:
        flags.append("no_boundary_edges")
    if traffic_light_count > 0 and approach_count < 3:
        flags.append("few_approaches_for_signalized_cluster")
    if overlap_pair_count > 0:
        flags.append("overlapping_internal_edges")
    if internal_edge_count >= max(node_count, 1):
        flags.append("many_internal_edges")
    if node_count >= 10:
        flags.append("large_cluster")
    return flags


def _aggregation_recommendation(*, internal_edge_count: int, boundary_edge_count: int, approach_count: int) -> str:
    if internal_edge_count > 0 and boundary_edge_count > 0 and approach_count >= 2:
        return "map_review_join_candidate"
    if internal_edge_count > 0:
        return "inspect_cluster_graph"
    return "map_review_required"


def _reference_free_aggregation_score(
    *,
    node_count: int,
    traffic_light_count: int,
    internal_edges: list[dict[str, Any]],
    boundary_edges: list[dict[str, Any]],
    approach_count: int,
    internal_edge_max_length: float,
    overlap_pair_count: int,
    physical_shape: dict[str, Any],
) -> dict[str, Any]:
    internal_edge_count = len(internal_edges)
    boundary_edge_count = len(boundary_edges)
    short_internal_edge_score = _short_internal_edge_score(internal_edge_count, internal_edge_max_length)
    same_road_name_score = _same_road_name_score(internal_edges + boundary_edges)
    traffic_signal_density = round(traffic_light_count / node_count, 3) if node_count else 0.0
    service_or_parking_risk = _edge_text_has(
        internal_edges + boundary_edges,
        {"service", "parking", "private", "driveway"},
    )
    bridge_tunnel_layer_risk = _edge_text_has(
        internal_edges + boundary_edges,
        {"bridge", "tunnel", "layer"},
    )
    roundabout_or_slip_lane_risk = _edge_text_has(
        internal_edges + boundary_edges,
        {"roundabout", "slip"},
    )
    physical_intersection_shape = str(physical_shape.get("physical_intersection_shape", "none"))
    physical_intersection_score = float(physical_shape.get("physical_intersection_score", 0.0) or 0.0)
    has_stable_cross_or_t_shape = (
        physical_intersection_shape in {"cross", "t_or_y"} and physical_intersection_score >= 0.6
    )

    decision = "join"
    confidence = "medium"
    reason_parts = []
    if internal_edge_count == 0:
        decision = "do_not_join"
        confidence = "high"
        reason_parts.append("no internal edges connect the dense nodes")
    if boundary_edge_count == 0:
        decision = "do_not_join"
        confidence = "high"
        reason_parts.append("no boundary approaches leave the cluster")
    if approach_count < 2:
        decision = "do_not_join"
        confidence = "high"
        reason_parts.append("too few external approaches for a physical intersection")
    if decision != "do_not_join" and not has_stable_cross_or_t_shape:
        decision = "needs_map_review"
        confidence = "low"
        reason_parts.append("no stable cross/T intersection shape from approach axes")

    review_risks = []
    if traffic_light_count > 0:
        review_risks.append("traffic-signal semantics require map review")
    if overlap_pair_count > 0:
        review_risks.append("overlapping internal edges require map review")
    if node_count >= 10:
        review_risks.append("large cluster requires map review")
    if approach_count >= 6:
        review_risks.append("many approaches require map review")
    if service_or_parking_risk:
        review_risks.append("service or parking access risk requires map review")
    if bridge_tunnel_layer_risk:
        review_risks.append("bridge, tunnel, or layer risk requires map review")
    if roundabout_or_slip_lane_risk:
        review_risks.append("roundabout or slip-lane risk requires map review")
    if decision != "do_not_join" and review_risks:
        decision = "needs_map_review"
        confidence = "low"
        reason_parts.extend(review_risks)

    if decision == "join":
        reason_parts.append(
            f"short internal edges and {physical_intersection_shape} approach-axis geometry indicate one physical junction candidate"
        )
    elif not reason_parts:
        reason_parts.append("insufficient topology evidence for automatic joining")

    return {
        "reference_free_scorer": "topology_heuristic_v1",
        "aggregation_decision": decision,
        "aggregation_confidence": confidence,
        "aggregation_reason": "; ".join(reason_parts),
        "short_internal_edge_score": short_internal_edge_score,
        "same_road_name_score": same_road_name_score,
        **physical_shape,
        "traffic_signal_density": traffic_signal_density,
        "service_or_parking_risk": service_or_parking_risk,
        "bridge_tunnel_layer_risk": bridge_tunnel_layer_risk,
        "roundabout_or_slip_lane_risk": roundabout_or_slip_lane_risk,
    }


def _physical_intersection_shape_score(
    *,
    nodes: list[dict[str, Any]],
    internal_edges: list[dict[str, Any]],
    boundary_edges: list[dict[str, Any]],
    junction_by_id: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    if not nodes:
        return _empty_physical_shape()

    node_ids = {str(node["id"]) for node in nodes}
    centroid_x = sum(float(node["x"]) for node in nodes) / len(nodes)
    centroid_y = sum(float(node["y"]) for node in nodes) / len(nodes)
    external_nodes: dict[str, dict[str, Any]] = {}
    for edge in boundary_edges:
        from_node = str(edge.get("from", ""))
        to_node = str(edge.get("to", ""))
        if from_node in node_ids and to_node in junction_by_id:
            external_nodes[to_node] = junction_by_id[to_node]
        elif to_node in node_ids and from_node in junction_by_id:
            external_nodes[from_node] = junction_by_id[from_node]

    arms = []
    for external_node in external_nodes.values():
        dx = float(external_node["x"]) - centroid_x
        dy = float(external_node["y"]) - centroid_y
        if math.hypot(dx, dy) <= 1e-6:
            continue
        arms.append(_axis_angle_deg(dx, dy))

    axis_groups = _group_approach_axes(arms)
    axis_angles = [round(group["mean_angle_deg"], 1) for group in axis_groups]
    axis_arm_counts = [len(group["angles"]) for group in axis_groups]
    dominant_axis_separation = 0.0
    angle_continuity_score = 0.0
    physical_shape = "none"
    shape_score = 0.0

    if len(axis_groups) >= 2:
        dominant_axis_separation = _axis_angle_distance(
            axis_groups[0]["mean_angle_deg"],
            axis_groups[1]["mean_angle_deg"],
        )
        angle_continuity_score = max(0.0, 1.0 - abs(90.0 - dominant_axis_separation) / 45.0)
        if len(arms) >= 4 and axis_arm_counts[0] >= 2 and axis_arm_counts[1] >= 2 and 65.0 <= dominant_axis_separation <= 115.0:
            physical_shape = "cross"
            shape_score = 0.72 + 0.28 * angle_continuity_score
        elif len(arms) >= 3 and axis_arm_counts[0] >= 2 and 45.0 <= dominant_axis_separation <= 135.0:
            physical_shape = "t_or_y"
            shape_score = 0.55 + 0.30 * angle_continuity_score
        elif len(arms) >= 5:
            physical_shape = "multi_arm"
            shape_score = 0.45

    short_internal_edge_score = _short_internal_edge_score(
        len(internal_edges),
        max((float(edge.get("length") or 0.0) for edge in internal_edges), default=0.0),
    )
    physical_intersection_score = min(1.0, shape_score * 0.72 + short_internal_edge_score * 0.28)
    return {
        "physical_intersection_shape": physical_shape,
        "physical_intersection_score": round(physical_intersection_score, 3),
        "approach_axis_count": len(axis_groups),
        "approach_axis_angles_deg": axis_angles,
        "approach_axis_arm_counts": axis_arm_counts,
        "dominant_axis_separation_deg": round(dominant_axis_separation, 1),
        "angle_continuity_score": round(angle_continuity_score, 3),
    }


def _empty_physical_shape() -> dict[str, Any]:
    return {
        "physical_intersection_shape": "none",
        "physical_intersection_score": 0.0,
        "approach_axis_count": 0,
        "approach_axis_angles_deg": [],
        "approach_axis_arm_counts": [],
        "dominant_axis_separation_deg": 0.0,
        "angle_continuity_score": 0.0,
    }


def _axis_angle_deg(dx: float, dy: float) -> float:
    return math.degrees(math.atan2(dy, dx)) % 180.0


def _axis_angle_distance(left: float, right: float) -> float:
    distance = abs(left - right) % 180.0
    return min(distance, 180.0 - distance)


def _group_approach_axes(angles: list[float], *, tolerance_deg: float = 18.0) -> list[dict[str, Any]]:
    groups: list[dict[str, Any]] = []
    for angle in sorted(angles):
        placed = False
        for group in groups:
            if _axis_angle_distance(float(group["mean_angle_deg"]), angle) <= tolerance_deg:
                group["angles"].append(angle)
                group["mean_angle_deg"] = _mean_axis_angle(group["angles"])
                placed = True
                break
        if not placed:
            groups.append({"mean_angle_deg": angle, "angles": [angle]})
    groups.sort(key=lambda group: (-len(group["angles"]), float(group["mean_angle_deg"])))
    return groups


def _mean_axis_angle(angles: list[float]) -> float:
    if not angles:
        return 0.0
    x = sum(math.cos(math.radians(2.0 * angle)) for angle in angles)
    y = sum(math.sin(math.radians(2.0 * angle)) for angle in angles)
    return (math.degrees(math.atan2(y, x)) / 2.0) % 180.0


def _short_internal_edge_score(internal_edge_count: int, internal_edge_max_length: float) -> float:
    if internal_edge_count == 0:
        return 0.0
    if internal_edge_max_length <= 10.0:
        return 1.0
    if internal_edge_max_length >= 30.0:
        return 0.0
    return round((30.0 - internal_edge_max_length) / 20.0, 3)


def _same_road_name_score(edges: list[dict[str, Any]]) -> float:
    names = [str(edge.get("name", "")).strip().lower() for edge in edges if str(edge.get("name", "")).strip()]
    if not names:
        return 0.0
    most_common = Counter(names).most_common(1)[0][1]
    return round(most_common / len(names), 3)


def _edge_text_has(edges: list[dict[str, Any]], tokens: set[str]) -> bool:
    for edge in edges:
        text = " ".join(
            [
                str(edge.get("id", "")),
                str(edge.get("type", "")),
                str(edge.get("name", "")),
            ]
        ).lower()
        if any(token in text for token in tokens):
            return True
    return False


def _edge_shape(edge: ET.Element, lanes: list[ET.Element]) -> list[tuple[float, float]]:
    shape_text = edge.attrib.get("shape", "")
    if not shape_text and lanes:
        shape_text = lanes[0].attrib.get("shape", "")
    return _parse_shape(shape_text)


def _parse_shape(shape_text: str) -> list[tuple[float, float]]:
    points = []
    for raw_point in shape_text.split():
        parts = raw_point.split(",")
        if len(parts) < 2:
            continue
        points.append((float(parts[0]), float(parts[1])))
    return points


def _edge_length(shape: list[tuple[float, float]], lanes: list[ET.Element]) -> float:
    if lanes and lanes[0].attrib.get("length"):
        return float(lanes[0].attrib["length"])
    if len(shape) < 2:
        return 0.0
    return sum(math.hypot(right[0] - left[0], right[1] - left[1]) for left, right in zip(shape, shape[1:]))


def _coordinate_converter(net_file: Path) -> Callable[[float, float], tuple[float, float]] | None:
    try:
        import sumolib  # type: ignore

        net = sumolib.net.readNet(str(net_file))
        return lambda x, y: _net_xy_to_latlon(net, x, y)
    except Exception:  # noqa: BLE001 - optional sumolib projection has an explicit XY fallback.
        return None


def _cluster_latlon(
    centroid_x: float,
    centroid_y: float,
    xy_to_latlon: Callable[[float, float], tuple[float, float]] | None,
) -> tuple[float, float, str]:
    if xy_to_latlon is None:
        return centroid_y, centroid_x, "xy_fallback_no_geo_projection"
    try:
        lat, lon = xy_to_latlon(centroid_x, centroid_y)
    except Exception:  # noqa: BLE001 - converter may be supplied by sumolib/pyproj and is best-effort.
        return centroid_y, centroid_x, "xy_fallback_geo_projection_failed"
    return lat, lon, "wgs84_from_sumo_projection"


def _google_maps_default_url(lat: float, lon: float) -> str:
    return f"https://www.google.com/maps/@{lat:.7f},{lon:.7f},50m"


def _google_maps_satellite_url(lat: float, lon: float) -> str:
    return f"https://www.google.com/maps/@{lat:.7f},{lon:.7f},50m/data=!3m1!1e3"


def _write_clusters_csv(path: Path, clusters: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "cluster_id",
                "node_count",
                "node_ids",
                "node_types",
                "centroid_x",
                "centroid_y",
                "centroid_lat",
                "centroid_lon",
                "coordinate_status",
                "map_review_source",
                "google_maps_url",
                "optional_google_maps_satellite_url",
                "manual_correction_status",
                "suggested_correction_action",
                "internal_edge_count",
                "boundary_edge_count",
                "approach_count",
                "direct_connected_node_pair_count",
                "traffic_light_node_count",
                "internal_edge_total_length_m",
                "internal_edge_max_length_m",
                "internal_edge_overlap_pair_count",
                "aggregation_recommendation",
                "reference_free_scorer",
                "aggregation_decision",
                "aggregation_confidence",
                "aggregation_reason",
                "modal_aggregation_decision",
                "modal_primary_role",
                "modal_review_action",
                "modal_reason",
                "modal_risk_flags",
                "modal_decision_counts",
                "modal_role_counts",
                "short_internal_edge_score",
                "same_road_name_score",
                "physical_intersection_shape",
                "physical_intersection_score",
                "approach_axis_count",
                "approach_axis_angles_deg",
                "approach_axis_arm_counts",
                "dominant_axis_separation_deg",
                "angle_continuity_score",
                "traffic_signal_density",
                "service_or_parking_risk",
                "bridge_tunnel_layer_risk",
                "roundabout_or_slip_lane_risk",
                "corridor_decision",
                "corridor_reason",
                "corridor_named_corridor_count",
                "corridor_unnamed_corridor_count",
                "corridor_intersection_cell_count",
                "corridor_intersection_cell_signatures",
                "corridor_partition_count",
                "corridor_max_partition_node_count",
                "corridor_named_corridors",
                "corridor_unnamed_corridors",
                "corridor_top_partitions",
                "risk_flags",
                "internal_edge_ids",
                "boundary_edge_ids",
                "external_junction_ids",
                "connected_node_pairs",
                "max_pair_distance_m",
                "cluster_radius_m",
            ],
        )
        writer.writeheader()
        for cluster in clusters:
            row = dict(cluster)
            for field in (
                "node_ids",
                "node_types",
                "risk_flags",
                "internal_edge_ids",
                "boundary_edge_ids",
                "external_junction_ids",
                "connected_node_pairs",
                "approach_axis_angles_deg",
                "approach_axis_arm_counts",
                "corridor_intersection_cell_signatures",
                "corridor_named_corridors",
                "corridor_unnamed_corridors",
                "corridor_top_partitions",
            ):
                row[field] = ";".join(str(item) for item in row.get(field, []) or [])
            for field in ("modal_decision_counts", "modal_role_counts"):
                row[field] = json.dumps(row.get(field, {}) or {}, sort_keys=True)
            writer.writerow(row)


def _write_connection_cells_csv(path: Path, cells: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "cell_id",
                "node_ids",
                "centroid_x",
                "centroid_y",
                "internal_edge_ids",
                "boundary_edge_ids",
                "external_vehicle_approach_count",
                "connection_cell_decision",
                "reason",
            ],
        )
        writer.writeheader()
        for cell in cells:
            row = dict(cell)
            for field in ("node_ids", "internal_edge_ids", "boundary_edge_ids"):
                row[field] = ";".join(str(item) for item in row.get(field, []) or [])
            writer.writerow(row)
