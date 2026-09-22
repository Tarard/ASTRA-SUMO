"""Select physical junction groups and prepare declared context joins."""

from __future__ import annotations

import math
import xml.etree.ElementTree as ET
from collections import defaultdict, deque
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any
from ..candidate_contracts import file_sha256
from ..command_runner import run_command
from ..connection_mode_audit import lane_supports_motorized
from ..hamburg_official_intersection_plainxml import _angular_endpoint_polygon
from ..surface_overlap_audit import _lane_face_primitives
from .boundaries import (
    _boundary_distance,
    _classify_boundary_ports,
    _official_boundary_sections,
    _resolved_boundary_polygon,
    _section_touches_polygon,
)
from .geometry import _distance_to_lines, _movement_centroid, _parse_shape
from .source import _official_internal_paths, _official_lane_boundary_points


def _select_join_groups(
    root: ET.Element,
    binding: Mapping[str, Any],
    plans: Mapping[str, Mapping[str, Any]],
    *,
    short_edge_m: float = 20.0,
    maximum_node_to_movement_m: float = 15.0,
    maximum_lane_projection_error_m: float = 10.0,
    minimum_lane_match_margin_m: float = 0.5,
) -> list[dict[str, Any]]:
    nodes = {
        junction.attrib["id"]: (
            float(junction.attrib.get("x", "0")),
            float(junction.attrib.get("y", "0")),
        )
        for junction in root.findall("junction")
        if not junction.attrib["id"].startswith(":")
    }
    node_shapes = {junction.attrib["id"]: _parse_shape(junction.get("shape", "")) for junction in root.findall("junction") if junction.attrib["id"] in nodes}
    node_faces = {node_id: [shape] if len(shape) >= 3 else [] for node_id, shape in node_shapes.items()}
    for edge in root.findall("edge"):
        if edge.get("function") != "internal":
            continue
        owner = edge.get("id", "")[1:].rsplit("_", 1)[0]
        if owner not in node_faces:
            continue
        for lane in edge.findall("lane"):
            shape = _parse_shape(lane.get("shape", ""))
            shape = [point for index, point in enumerate(shape) if index == 0 or math.dist(point, shape[index - 1]) > 1e-9]
            if lane_supports_motorized(lane) and len(shape) >= 2:
                node_faces[owner].extend(_lane_face_primitives(shape, float(lane.get("width", "3.2"))))
    edge_to = {}
    neighbors: dict[str, set[str]] = defaultdict(set)
    for edge in root.findall("edge"):
        if edge.attrib.get("function") == "internal" or "from" not in edge.attrib:
            continue
        source, target = edge.attrib["from"], edge.attrib["to"]
        edge_to[edge.attrib["id"]] = target
        length = min(
            (float(lane.attrib.get("length", "inf")) for lane in edge.findall("lane")),
            default=math.inf,
        )
        if length <= short_edge_m:
            neighbors[source].add(target)
            neighbors[target].add(source)
    binding_by_node = {}
    owners: dict[tuple[str, str], str] = {}
    for row in binding.get("bindings", []):
        if not isinstance(row, Mapping) or not row.get("node_id"):
            raise ValueError("each cluster binding requires a node id")
        node_id = str(row["node_id"])
        if node_id in binding_by_node:
            raise ValueError(f"duplicate node binding: {node_id}")
        binding_by_node[node_id] = row
        if node_id not in plans:
            continue
        tls_ids = row.get("tls_ids")
        if not isinstance(tls_ids, list) or not tls_ids or any(not str(value).strip() for value in tls_ids):
            raise ValueError(f"planned node {node_id} requires source TLS ids")
        identities = [("TLS", str(value)) for value in tls_ids]
        if row.get("cluster_id"):
            identities.append(("cluster", str(row["cluster_id"])))
        for identity in identities:
            previous = owners.setdefault(identity, node_id)
            if previous != node_id:
                raise ValueError(f"source {identity[0]} {identity[1]} belongs to multiple planned nodes: {previous}, {node_id}")
    if any(node_id not in binding_by_node for node_id in plans):
        raise ValueError("every planned intersection requires a source junction binding")
    all_connections = root.findall("connection")
    declared_tls_owners: dict[str, set[str]] = defaultdict(set)
    declared_node_owners: dict[str, set[str]] = defaultdict(set)
    for declared_id, row in binding_by_node.items():
        for tls_id in row.get("tls_ids", []) if isinstance(row.get("tls_ids"), list) else []:
            declared_tls_owners[str(tls_id)].add(declared_id)
            if str(tls_id) in nodes:
                declared_node_owners[str(tls_id)].add(declared_id)
    for connection in all_connections:
        owner = edge_to.get(connection.get("from", ""))
        if owner is not None:
            declared_node_owners[owner].update(declared_tls_owners.get(connection.get("tl", ""), set()))
    used: set[str] = set()
    groups = []
    for node_id in plans:
        movements_by_part = _movements_by_part(plans[node_id])
        official_points = _official_lane_boundary_points(plans[node_id], root)
        official_sections = _official_boundary_sections(plans[node_id], official_points)
        core_paths = _official_internal_paths(plans[node_id], root, official_points)
        boundaries = {}
        boundary_owners = {}
        anchor_owners = {}
        part_sections = {}
        movement_owners = {}
        for part, movements in movements_by_part.items():
            lane_ids = {str(row[key]) for row in movements for key in ("ingress_lane_id", "egress_lane_id")}
            boundary_points = [official_points[lane_id] for lane_id in sorted(lane_ids) if lane_id in official_points]
            boundaries[part] = []
            part_sections[part] = [official_sections[lane_id] for lane_id in sorted(lane_ids) if lane_id in official_sections]
            if len(boundary_points) >= 3 and len(boundary_points) == len(lane_ids):
                center = tuple(sum(point[i] for point in boundary_points) / len(boundary_points) for i in (0, 1))
                try:
                    boundaries[part] = _angular_endpoint_polygon(boundary_points, center=center, tolerance_m=0.1)
                except ValueError:
                    pass
            boundary_owners[part] = {
                candidate for candidate, faces in node_faces.items()
                if any(
                    any(_boundary_distance(point, shape) <= 1e-6 for point in boundaries[part])
                    or any(_section_touches_polygon(section["shape"], shape) for section in part_sections[part])
                    for shape in faces
                )
            }
            part_paths = [core_paths["paths"][key] for row in movements for key in [(str(row["ingress_lane_id"]), str(row["egress_lane_id"]))] if key in core_paths["paths"]]
            movement_owners[part] = {
                candidate for candidate, faces in node_faces.items()
                if any(_section_touches_polygon((a, b), face) for path in part_paths for a, b in zip(path, path[1:]) for face in faces)
            }
            anchor_owners[part] = set(boundary_owners[part])
            boundary_owners[part] |= movement_owners[part]
        centroids = {
            part: tuple(sum(point[i] for point in boundaries[part]) / len(boundaries[part]) for i in (0, 1)) if boundaries[part] else _movement_centroid(movements, (0.0, 0.0))
            for part, movements in movements_by_part.items()
        }
        tls_ids = set(str(value) for value in binding_by_node[node_id]["tls_ids"])
        tls_nodes = {value for value in tls_ids if value in nodes}
        tls_nodes.update(
            edge_to.get(connection.attrib.get("from", ""), "")
            for connection in all_connections
            if connection.attrib.get("tl") in tls_ids
        )
        tls_nodes.discard("")
        assigned: dict[str, set[str]] = defaultdict(set)
        for tls_node in tls_nodes:
            assigned[
                min(
                    centroids,
                    key=lambda part: (
                        0.0 if tls_node in boundary_owners[part] else _boundary_distance(nodes[tls_node], boundaries[part]) if boundaries[part] else math.dist(nodes[tls_node], centroids[part]),
                        math.dist(nodes[tls_node], centroids[part]),
                    ),
                )
            ].add(tls_node)
        for part, movements in sorted(movements_by_part.items()):
            boundary = boundaries[part]
            local_lines = [
                movement["selected_shape_network"]
                for movement in movements
            ]
            eligible = {
                candidate
                for candidate, point in nodes.items()
                if _distance_to_lines(point, local_lines) <= maximum_node_to_movement_m
            }
            initial = set(assigned.get(part, set()))
            excluded = []
            active_neighbors = neighbors
            if boundary:
                margin = max((float(lane.get("width", "3.2")) / 2 for edge in root.findall("edge") if edge.get("from") in initial or edge.get("to") in initial for lane in edge.findall("lane") if lane_supports_motorized(lane)), default=1.6)
                eligible = {candidate for candidate in nodes if _boundary_distance(nodes[candidate], boundary) <= margin or candidate in boundary_owners[part]}
                excluded = sorted(initial - eligible)
                initial &= eligible
                active_neighbors = defaultdict(set)
                for edge in root.findall("edge"):
                    source, target = edge.get("from"), edge.get("to")
                    if source not in eligible or target not in eligible:
                        continue
                    lane_shapes = [_parse_shape(lane.get("shape", "")) for lane in edge.findall("lane") if lane_supports_motorized(lane)]
                    lane_shapes = [shape for shape in lane_shapes if len(shape) >= 2]
                    if not lane_shapes:
                        continue
                    samples = [
                        (a[0] + (b[0] - a[0]) * t / steps, a[1] + (b[1] - a[1]) * t / steps)
                        for shape in lane_shapes
                        for a, b in zip(shape, shape[1:])
                        for steps in [max(1, math.ceil(math.dist(a, b) / max(margin, 0.1)))]
                        for t in range(steps + 1)
                    ]
                    endpoint_surfaces = [shape for value in (source, target) if value in boundary_owners[part] for shape in node_faces[value]]
                    if all(_boundary_distance(point, boundary) <= margin or any(_boundary_distance(point, shape) <= 1e-6 for shape in endpoint_surfaces) for point in samples):
                        active_neighbors[source].add(target)
                        active_neighbors[target].add(source)
            group = set(initial)
            if not group and eligible:
                group.add(min(eligible, key=lambda value: math.dist(nodes[value], centroids[part])))
            queue = deque(group)
            while queue:
                current = queue.popleft()
                for neighbor in active_neighbors[current]:
                    if (
                        neighbor in eligible
                        and neighbor not in group
                        and neighbor not in used
                        and (neighbor not in tls_nodes or neighbor in initial)
                    ):
                        group.add(neighbor)
                        queue.append(neighbor)
            # Parallel carriageways can be disconnected in the motor graph.
            # Supplement the original seed component only with nodes directly
            # witnessed by this part's official B sections or B-to-B paths.
            # Do not expand these additions through unproved nearby branches.
            evidence_components, component_reviews = [], []
            pending = eligible & boundary_owners[part]
            while pending:
                seed = min(pending)
                component, queue = {seed}, deque([seed])
                pending.remove(seed)
                while queue:
                    for neighbor in active_neighbors[queue.popleft()]:
                        if neighbor in pending:
                            pending.remove(neighbor)
                            component.add(neighbor)
                            queue.append(neighbor)
                additions = component - group
                conflicting_parts = sorted(other for other in boundary_owners if other != part and component & boundary_owners[other])
                conflicting_intersections = sorted({owner for value in component for owner in declared_node_owners[value]} - {node_id})
                reserved = component & used
                other_part_tls = component & (tls_nodes - initial)
                record = {"source_node_ids": sorted(component), "added_source_node_ids": sorted(additions),
                          "official_B_owner_node_ids": sorted(component & anchor_owners[part]),
                          "official_drive_line_owner_node_ids": sorted(component & movement_owners[part]),
                          "basis": "same_part_official_geometry_witnessed_component"}
                if additions and (conflicting_parts or conflicting_intersections or reserved or other_part_tls):
                    component_reviews.append({**record, "reason": "component_has_conflicting_junction_ownership",
                                              "conflicting_parts": conflicting_parts, "conflicting_intersections": conflicting_intersections,
                                              "already_used_node_ids": sorted(reserved), "other_part_tls_node_ids": sorted(other_part_tls)})
                else:
                    group.update(component)
                    evidence_components.append(record)
            group -= used
            used.update(group)
            if group:
                record = {
                        "node_id": node_id,
                        "intersection_part": part,
                        "join_id": f"LSA{node_id}_part{part}" if len(group) > 1 else next(iter(group)),
                        "source_node_ids": sorted(group),
                        "assigned_tls_node_count": len(initial),
                        "selected_movement_count": len(movements),
                        "official_boundary_shape": boundary,
                        "boundary_excluded_node_ids": excluded,
                        "boundary_anchor_owner_node_ids": sorted(group & anchor_owners[part]),
                        "boundary_port_sections": part_sections[part],
                        "official_movement_owner_node_ids": sorted(group & movement_owners[part]),
                        "evidence_components": evidence_components,
                        "component_selection_reviews": component_reviews,
                        "unusable_official_core_curves": core_paths["unusable"],
                    }
                classification = _classify_boundary_ports(
                    root, plans[node_id], record, official_points,
                    maximum_lane_projection_error_m=maximum_lane_projection_error_m,
                    minimum_lane_match_margin_m=minimum_lane_match_margin_m,
                )
                if classification is not None:
                    record["selection_boundary_shape"] = boundary
                    record["boundary_port_classification"] = classification
                    record["official_control_sections"] = record["boundary_port_sections"]
                    record["boundary_port_sections"] = classification["official_external_ports"]
                    record["official_boundary_shape"], _ = _resolved_boundary_polygon(root, record)
                groups.append(record)
    return groups


def _prepare_context_joins(*, source_net, context_groups, official_groups, output_dir, netconvert_binary, sumo_binary, timeout_seconds, seed, adjacent_geometry_junction_ids=(), signal_policy="preserve", junction_contours="preserve", junction_corner_radius_m=8.0, interior_lane_change_policy="fixed_paths"):
    """Rebuild declared context under its explicit signal policy."""
    from ..junction_boundary_rebuild import collect_join_boundary_paths, restore_joined_boundary_connections

    root = ET.parse(source_net).getroot()
    nodes = {node.get("id"): node for node in root.findall("junction")}
    protected = {str(node) for group in official_groups for node in group["source_node_ids"]}
    members = {node for group in context_groups.values() for node in group}
    if members & protected:
        raise ValueError("context_joins must not overlap official junction groups")
    allowed_types = {"priority", "priority_stop", "right_before_left", "left_before_right", "allway_stop", "zipper", "unregulated"}
    if signal_policy == "rebuild_diagnostic":
        allowed_types.update({"traffic_light", "traffic_light_unregulated", "traffic_light_right_on_red"})
        collect_join_boundary_paths(root, groups=context_groups, signal_policy=signal_policy)
    elif signal_policy != "preserve":
        raise ValueError("unsupported context signal_policy")
    if not members <= set(nodes) or any(nodes[node].get("type") not in allowed_types for node in members):
        raise ValueError("context_joins requires existing junctions without signals or rail control")
    if any(join_id in nodes and join_id not in group for join_id, group in context_groups.items()):
        raise ValueError("context_joins target id collides with a retained junction")
    edge_targets = {edge.get("id"): edge.get("to") for edge in root.findall("edge")}
    if signal_policy == "preserve" and any(connection.get("tl") and edge_targets.get(connection.get("from")) in members for connection in root.findall("connection")):
        raise ValueError("context_joins cannot contain a controlled connection")
    output_dir.mkdir(parents=True)
    joins = output_dir / "context-joins.nod.xml"
    _write_join_file(joins, [{"join_id": key, "source_node_ids": values} for key, values in context_groups.items()], traffic_light=signal_policy == "rebuild_diagnostic")
    joined = output_dir / "joined.net.xml"
    command = run_command([netconvert_binary, "--sumo-net-file", str(source_net), "--node-files", str(joins), "--offset.disable-normalization", "true", "--junctions.internal-link-detail", "25", "--seed", str(seed), "--output-file", str(joined)], cwd=output_dir, timeout_seconds=timeout_seconds)
    (output_dir / "netconvert.log").write_text(command.stdout + command.stderr, encoding="utf-8")
    if command.returncode != 0 or not joined.is_file():
        raise ValueError("netconvert could not build the declared context joins")
    report = restore_joined_boundary_connections(
        source_net=source_net, joined_net=joined, groups=context_groups,
        output_dir=output_dir / "boundary-restoration", expected_source_sha256=file_sha256(source_net),
        expected_joined_sha256=file_sha256(joined), netconvert_binary=netconvert_binary,
        sumo_binary=sumo_binary, timeout_seconds=timeout_seconds,
        adjacent_geometry_junction_ids=adjacent_geometry_junction_ids, signal_policy=signal_policy,
        junction_contours=junction_contours, junction_corner_radius_m=junction_corner_radius_m,
        interior_lane_change_policy=interior_lane_change_policy)
    if report["status"] != "pass":
        raise ValueError("context joins did not preserve boundary paths or outside junctions")
    return {**report, "groups": context_groups, "join_command": command.to_dict(), "join_file": {"path": str(joins), "sha256": file_sha256(joins)}}


def _write_join_file(path: Path, groups: Sequence[Mapping[str, Any]], *, traffic_light: bool = True) -> None:
    root = ET.Element("nodes")
    for group in groups:
        if len(group["source_node_ids"]) < 2:
            continue
        ET.SubElement(
            root,
            "join",
            {
                "id": str(group["join_id"]),
                "type": "traffic_light" if traffic_light else "priority",
                **({"tl": str(group["join_id"])} if traffic_light else {}),
                "reset": "false",
                "nodes": " ".join(str(value) for value in group["source_node_ids"]),
            },
        )
    ET.indent(root, space="  ")
    ET.ElementTree(root).write(path, encoding="utf-8", xml_declaration=True)


def _movements_by_part(plan: Mapping[str, Any]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for movement in plan.get("movements", []):
        if isinstance(movement, Mapping):
            grouped[str(movement.get("intersection_part", "0"))].append(
                dict(movement)
            )
    return dict(grouped)
