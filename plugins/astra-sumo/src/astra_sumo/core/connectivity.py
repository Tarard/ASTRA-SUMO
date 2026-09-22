from __future__ import annotations

import csv
import json
from collections import deque
from pathlib import Path
from typing import Any, Mapping
import xml.etree.ElementTree as ET

from .command_runner import run_command
from .osm_network import read_net_edges


def _failure(error_message: str) -> dict[str, Any]:
    return {
        "status": "fail",
        "claim_status": "construction-invalid",
        "connectivity_status": "fail",
        "error": error_message,
    }


def _weak_components(edge_ids: set[str], connections: dict[str, set[str]]) -> list[set[str]]:
    neighbors = {edge_id: set() for edge_id in edge_ids}
    for from_edge, to_edges in connections.items():
        if from_edge not in edge_ids:
            continue
        for to_edge in to_edges:
            if to_edge not in edge_ids:
                continue
            neighbors[from_edge].add(to_edge)
            neighbors[to_edge].add(from_edge)

    remaining = set(edge_ids)
    components = []
    while remaining:
        start = remaining.pop()
        component = {start}
        queue: deque[str] = deque([start])
        while queue:
            current = queue.popleft()
            for neighbor in neighbors[current]:
                if neighbor in remaining:
                    remaining.remove(neighbor)
                    component.add(neighbor)
                    queue.append(neighbor)
        components.append(component)
    return components


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


def _passenger_components(net_file: Path) -> tuple[dict[str, Any], list[set[str]]]:
    edges, connections = read_net_edges(net_file)
    passenger_edges = {
        edge_id
        for edge_id, edge in edges.items()
        if edge.allows_passenger
    }
    passenger_connections = {
        from_edge: {to_edge for to_edge in to_edges if to_edge in passenger_edges}
        for from_edge, to_edges in connections.items()
        if from_edge in passenger_edges
    }
    components = _weak_components(passenger_edges, passenger_connections)
    components.sort(key=lambda component: (-len(component), sorted(component)[0] if component else ""))
    return {
        "passenger_edge_count": len(passenger_edges),
        "passenger_component_count": len(components),
    }, components


def _write_component_review_file(
    net_file: Path,
    output_file: Path,
    components: list[set[str]],
) -> None:
    """Write stable, map-addressable review records for discarded components."""

    root = ET.parse(net_file).getroot()
    node_xy: dict[str, tuple[float, float]] = {}
    for node in root.findall("junction"):
        node_id = node.attrib.get("id", "")
        try:
            node_xy[node_id] = (float(node.attrib["x"]), float(node.attrib["y"]))
        except (KeyError, TypeError, ValueError):
            continue

    edge_endpoints: dict[str, tuple[str, str]] = {}
    edge_shape_points: dict[str, list[tuple[float, float]]] = {}
    for edge in root.findall("edge"):
        edge_id = edge.attrib.get("id", "")
        if not edge_id:
            continue
        edge_endpoints[edge_id] = (
            edge.attrib.get("from", ""),
            edge.attrib.get("to", ""),
        )
        points: list[tuple[float, float]] = []
        lane = edge.find("lane")
        if lane is not None:
            for raw_point in lane.attrib.get("shape", "").split():
                try:
                    x_text, y_text = raw_point.split(",", 1)
                    points.append((float(x_text), float(y_text)))
                except (ValueError, TypeError):
                    continue
        edge_shape_points[edge_id] = points

    records: list[dict[str, Any]] = []
    for rank, component in enumerate(components, start=2):
        points: list[tuple[float, float]] = []
        for edge_id in sorted(component):
            from_node, to_node = edge_endpoints.get(edge_id, ("", ""))
            endpoint_points = [
                node_xy[node_id]
                for node_id in (from_node, to_node)
                if node_id in node_xy
            ]
            points.extend(endpoint_points or edge_shape_points.get(edge_id, []))
        if points:
            x_values = [point[0] for point in points]
            y_values = [point[1] for point in points]
            centroid_x = sum(x_values) / len(x_values)
            centroid_y = sum(y_values) / len(y_values)
            bounds = {
                "min_x": min(x_values),
                "min_y": min(y_values),
                "max_x": max(x_values),
                "max_y": max(y_values),
            }
        else:
            centroid_x = None
            centroid_y = None
            bounds = {}
        records.append(
            {
                "location_id": f"disconnected_component_{rank:03d}",
                "component_rank": rank,
                "component_size": len(component),
                "edge_ids": sorted(component),
                "discard_reason": "outside_largest_passenger_component",
                "centroid_x": centroid_x,
                "centroid_y": centroid_y,
                "bounds": bounds,
                "decision": "pending",
                "evidence": "",
                "reviewer": "",
            }
        )

    output_file.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "status": "pending_review",
                "source_net_file": str(net_file),
                "decision_policy": (
                    "Each discarded component must be repaired/reintegrated or "
                    "explicitly rejected_with_evidence before a clean-network claim."
                ),
                "component_count": len(records),
                "records": records,
            },
            indent=2,
            ensure_ascii=False,
            sort_keys=True,
        ),
        encoding="utf-8",
    )


def extract_largest_passenger_component_core(
    net_file: Path,
    *,
    output_dir: Path,
    prefix: str = "sumo_osm_network",
    timeout_seconds: float = 240.0,
    command_runner=run_command,
) -> dict[str, Any]:
    """Build a valid SUMO network containing only the largest passenger component."""
    try:
        raw_summary, components = _passenger_components(net_file)
    except (OSError, ET.ParseError, KeyError, ValueError) as exc:
        return _failure(f"{type(exc).__name__}: {exc}")

    if not components:
        return {
            "status": "fail",
            "claim_status": "construction-invalid",
            "network_quality": "construction-invalid",
            "raw_net_file": str(net_file),
            "raw_passenger_edge_count": raw_summary["passenger_edge_count"],
            "raw_passenger_component_count": raw_summary["passenger_component_count"],
            "core_passenger_edge_count": 0,
            "discarded_passenger_edge_count": 0,
            "warnings": ["network has no passenger component to extract"],
        }

    output_dir.mkdir(parents=True, exist_ok=True)
    core_edges = sorted(components[0])
    discarded_components = components[1:]
    core_file = output_dir / f"{prefix}_connected_core.net.xml"
    keep_edges_file = output_dir / f"{prefix}_connected_core.keep_edges.txt"
    discarded_components_file = output_dir / f"{prefix}_discarded_components.csv"
    discarded_components_review_file = output_dir / f"{prefix}_discarded_components_review.json"
    command_record = output_dir / f"{prefix}_connected_core_command.txt"

    keep_edges_file.write_text("\n".join(core_edges) + "\n", encoding="utf-8")
    with discarded_components_file.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["component_rank", "component_size", "edge_id", "discard_reason"],
        )
        writer.writeheader()
        for rank, component in enumerate(discarded_components, start=2):
            for edge_id in sorted(component):
                writer.writerow(
                    {
                        "component_rank": rank,
                        "component_size": len(component),
                        "edge_id": edge_id,
                        "discard_reason": "outside_largest_passenger_component",
                    }
                )

    command = [
        "netconvert",
        "--sumo-net-file",
        str(net_file.resolve()),
        "--keep-edges.input-file",
        keep_edges_file.name,
        "--keep-edges.postload",
        "--output-file",
        core_file.name,
    ]
    command_record.write_text(" ".join(command) + "\n", encoding="utf-8")
    command_attempts = [list(command)]
    try:
        result = _result_to_dict(command_runner(command, cwd=output_dir, timeout_seconds=timeout_seconds))
    except OSError as exc:
        return _failure(f"{type(exc).__name__}: {exc}")

    fallback_used = False
    if result.get("status") != "pass" or not core_file.exists():
        # Some visual-detail .net.xml files contain manually preserved internal
        # geometry that makes --keep-edges.postload fail inside netconvert.  A
        # plain keep-edges rebuild is still safe here because the keep list is
        # restricted to the selected passenger component.  Keep the failed
        # attempt in the report instead of silently replacing its result.
        fallback_command = [
            value
            for value in command
            if value != "--keep-edges.postload"
        ]
        command_attempts.append(list(fallback_command))
        command_record.write_text(
            "\n".join(" ".join(item) for item in command_attempts) + "\n",
            encoding="utf-8",
        )
        try:
            fallback_result = _result_to_dict(
                command_runner(
                    fallback_command,
                    cwd=output_dir,
                    timeout_seconds=timeout_seconds,
                )
            )
        except OSError as exc:
            fallback_result = _result_to_dict(
                {
                    "status": "fail",
                    "returncode": None,
                    "stdout": "",
                    "stderr": "",
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
        if fallback_result.get("status") == "pass" and core_file.exists():
            result = fallback_result
            fallback_used = True

    try:
        _write_component_review_file(net_file, discarded_components_review_file, discarded_components)
    except (OSError, ET.ParseError, KeyError, ValueError) as exc:
        return _failure(f"{type(exc).__name__}: {exc}")

    status = "pass" if result.get("status") == "pass" and core_file.exists() else "fail"
    discarded_edge_count = sum(len(component) for component in discarded_components)
    warnings = []
    if status != "pass":
        warnings.append(f"connected-core network was not created: {core_file}")
    if discarded_edge_count:
        warnings.append(
            f"extracted largest passenger component as connected simulation core; "
            f"discarded {discarded_edge_count} passenger edges outside the core"
        )
    return {
        "status": status,
        "claim_status": "diagnostic-demo" if status == "pass" else "construction-invalid",
        "network_quality": "connected-core" if status == "pass" else "construction-invalid",
        "raw_net_file": str(net_file),
        "connected_core_file": str(core_file),
        "keep_edges_file": str(keep_edges_file),
        "discarded_components_file": str(discarded_components_file),
        "discarded_components_review_file": str(discarded_components_review_file),
        "command_record": str(command_record),
        "command_attempts": command_attempts,
        "fallback_used": fallback_used,
        "raw_passenger_edge_count": raw_summary["passenger_edge_count"],
        "raw_passenger_component_count": raw_summary["passenger_component_count"],
        "core_passenger_edge_count": len(core_edges),
        "discarded_passenger_edge_count": discarded_edge_count,
        "netconvert": result,
        "warnings": warnings,
    }


def summarize_passenger_connectivity(
    net_file: Path,
    *,
    small_component_threshold: int = 3,
) -> dict[str, Any]:
    try:
        edges, connections = read_net_edges(net_file)
    except (OSError, ET.ParseError, KeyError, ValueError) as exc:
        return _failure(f"{type(exc).__name__}: {exc}")

    passenger_edges = {
        edge_id
        for edge_id, edge in edges.items()
        if edge.allows_passenger
    }
    if not passenger_edges:
        return {
            "status": "fail",
            "claim_status": "construction-invalid",
            "connectivity_status": "fail",
            "net_file": str(net_file),
            "passenger_edge_count": 0,
            "passenger_component_count": 0,
            "largest_component_edge_count": 0,
            "small_component_count": 0,
            "isolated_passenger_edge_count": 0,
            "warnings": ["network has no passenger-allowed edges"],
        }

    passenger_connections = {
        from_edge: {to_edge for to_edge in to_edges if to_edge in passenger_edges}
        for from_edge, to_edges in connections.items()
        if from_edge in passenger_edges
    }
    components = _weak_components(passenger_edges, passenger_connections)
    component_sizes = sorted((len(component) for component in components), reverse=True)
    connected_edges = set(passenger_connections)
    for to_edges in passenger_connections.values():
        connected_edges.update(to_edges)
    isolated_edges = passenger_edges - connected_edges
    small_components = []
    if len(components) != 1:
        small_components = [
            component
            for component in components
            if len(component) <= small_component_threshold
        ]

    warnings = []
    if len(components) != 1:
        warnings.append(f"passenger network has {len(components)} disconnected components")
    if isolated_edges:
        warnings.append(f"passenger network has {len(isolated_edges)} isolated passenger edges")

    ok = len(components) == 1 and not isolated_edges
    return {
        "status": "pass" if ok else "fail",
        "claim_status": "diagnostic-demo" if ok else "construction-invalid",
        "connectivity_status": "pass" if ok else "fail",
        "net_file": str(net_file),
        "passenger_edge_count": len(passenger_edges),
        "passenger_component_count": len(components),
        "largest_component_edge_count": component_sizes[0] if component_sizes else 0,
        "small_component_count": len(small_components),
        "isolated_passenger_edge_count": len(isolated_edges),
        "component_sizes": component_sizes,
        "warnings": warnings,
    }
