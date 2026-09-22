"""Write teacher lane, type and endpoint patches for netconvert."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path
from .artifacts import _failure
from .geometry import _format_xy, _translate_shape
from .scope import _endpoint_rewrites


def write_teacher_lane_patch_edges(
    *,
    raw_edge_file: Path,
    teacher_edge_file: Path,
    output_file: Path,
    edge_map: dict[str, str],
    junction_id: str | None = None,
    teacher_junction_id: str | None = None,
    boundary_node_ids: set[str] | None = None,
    prune_unmapped_boundary_edges: bool = False,
    approach_endpoint_rebuild_plan: object | None = None,
    lane_shape_delta: tuple[float, float] | None = None,
    preserve_lane_shapes: bool = True,
) -> dict[str, object]:
    output_file.parent.mkdir(parents=True, exist_ok=True)
    teacher_edges = {
        edge.attrib["id"]: edge
        for edge in ET.parse(teacher_edge_file).getroot().findall("edge")
        if edge.attrib.get("id")
    }
    teacher_by_candidate = {candidate_id: teacher_edges[teacher_id] for teacher_id, candidate_id in edge_map.items() if teacher_id in teacher_edges}

    tree = ET.parse(raw_edge_file)
    root = tree.getroot()
    patched = []
    added_missing_mapped_edges = []
    pruned_boundary_edges = []
    retained_unmapped_boundary_edges = []
    remapped_teacher_edges = set(edge_map)
    teacher_same_junction_edges = {
        edge_id
        for edge_id, edge in teacher_edges.items()
        if junction_id and junction_id in (edge.attrib.get("from"), edge.attrib.get("to")) and edge_id not in remapped_teacher_edges
    }
    allowed_boundary_edges = set(edge_map.values()) | teacher_same_junction_edges
    boundary_node_ids = boundary_node_ids or set()
    join_source_node_id = (
        sorted(boundary_node_ids)[0]
        if teacher_junction_id and junction_id and teacher_junction_id != junction_id and boundary_node_ids
        else ""
    )
    rebased_missing_mapped_edges = []
    rebased_existing_mapped_edges = []
    endpoint_rewritten_existing_mapped_edges = []
    endpoint_rewritten_missing_mapped_edges = []
    skipped_rebased_self_loop_edges = []
    endpoint_rewrites = _endpoint_rewrites(approach_endpoint_rebuild_plan)
    for edge in root.findall("edge"):
        edge_id = edge.attrib.get("id", "")
        touches_target = (
            edge.attrib.get("from") == junction_id
            or edge.attrib.get("to") == junction_id
            or edge.attrib.get("from") in boundary_node_ids
            or edge.attrib.get("to") in boundary_node_ids
        )
        if junction_id and touches_target and edge_id not in allowed_boundary_edges:
            if prune_unmapped_boundary_edges:
                root.remove(edge)
                pruned_boundary_edges.append(edge_id)
                continue
            retained_unmapped_boundary_edges.append(edge_id)
        teacher_edge = teacher_by_candidate.get(edge.attrib.get("id", ""))
        if teacher_edge is None:
            continue
        teacher_lanes = teacher_edge.findall("lane")
        if not teacher_lanes:
            continue
        rebased_endpoints = {}
        endpoint_rewritten = {}
        endpoint_rewrite = endpoint_rewrites.get(edge_id)
        if endpoint_rewrite is not None:
            for attr, desired_endpoint in (("from", endpoint_rewrite[0]), ("to", endpoint_rewrite[1])):
                current_endpoint = edge.attrib.get(attr, "")
                if current_endpoint != desired_endpoint:
                    edge.set(attr, desired_endpoint)
                    endpoint_rewritten[attr] = {"old": current_endpoint, "new": desired_endpoint}
        elif join_source_node_id:
            for attr in ("from", "to"):
                teacher_endpoint = teacher_edge.attrib.get(attr, "")
                if teacher_endpoint == teacher_junction_id:
                    edge.set(attr, join_source_node_id)
                    rebased_endpoints[attr] = {"teacher": teacher_endpoint, "candidate": join_source_node_id}
        if (rebased_endpoints or endpoint_rewritten) and edge.attrib.get("from") and edge.attrib.get("from") == edge.attrib.get("to"):
            skipped_rebased_self_loop_edges.append(
                {
                    "candidate_edge_id": edge.attrib.get("id", ""),
                    "teacher_edge_id": teacher_edge.attrib.get("id", ""),
                    "node": edge.attrib["from"],
                }
            )
            root.remove(edge)
            continue
        existing_lane_shapes = (
            {}
            if endpoint_rewrite is not None
            else {
                lane.attrib.get("index", ""): lane.attrib["shape"]
                for lane in edge.findall("lane")
                if lane.attrib.get("index", "") and lane.attrib.get("shape")
            }
        )
        for lane in list(edge.findall("lane")):
            edge.remove(lane)
        edge.attrib.pop("allow", None)
        edge.attrib.pop("disallow", None)
        edge.attrib.pop("width", None)
        edge.set("numLanes", str(len(teacher_lanes)))
        for attr in ("allow", "disallow", "width"):
            if teacher_edge.attrib.get(attr):
                edge.set(attr, teacher_edge.attrib[attr])
        if not preserve_lane_shapes:
            edge.attrib.pop("shape", None)
        for lane in teacher_lanes:
            lane_attrs = {"index": lane.attrib.get("index", "0")}
            for attr in ("allow", "disallow", "width", "speed"):
                if lane.attrib.get(attr):
                    lane_attrs[attr] = lane.attrib[attr]
            if preserve_lane_shapes:
                if lane.attrib.get("index", "") in existing_lane_shapes:
                    lane_attrs["shape"] = existing_lane_shapes[lane.attrib.get("index", "")]
                elif lane.attrib.get("shape"):
                    lane_attrs["shape"] = (
                        _translate_shape(lane.attrib["shape"], lane_shape_delta[0], lane_shape_delta[1])
                        if lane_shape_delta is not None
                        else lane.attrib["shape"]
                    )
            ET.SubElement(edge, "lane", lane_attrs)
        patched.append({"candidate_edge_id": edge.attrib.get("id", ""), "teacher_edge_id": teacher_edge.attrib.get("id", ""), "lane_count": len(teacher_lanes)})
        if endpoint_rewritten:
            endpoint_rewritten_existing_mapped_edges.append(
                {
                    "candidate_edge_id": edge.attrib.get("id", ""),
                    "teacher_edge_id": teacher_edge.attrib.get("id", ""),
                    **endpoint_rewritten,
                }
            )
        if rebased_endpoints:
            rebased_existing_mapped_edges.append(
                {
                    "candidate_edge_id": edge.attrib.get("id", ""),
                    "teacher_edge_id": teacher_edge.attrib.get("id", ""),
                    **rebased_endpoints,
                }
            )

    existing_edge_ids = {edge.attrib.get("id", "") for edge in root.findall("edge")}
    for teacher_id, candidate_id in sorted(edge_map.items()):
        if candidate_id in existing_edge_ids:
            continue
        teacher_edge = teacher_edges.get(teacher_id)
        if teacher_edge is None:
            continue
        teacher_lanes = teacher_edge.findall("lane")
        edge_attrs = dict(teacher_edge.attrib)
        edge_attrs["id"] = candidate_id
        edge_attrs["numLanes"] = str(len(teacher_lanes))
        rebased_endpoints = {}
        endpoint_rewritten = {}
        endpoint_rewrite = endpoint_rewrites.get(candidate_id)
        if endpoint_rewrite is not None:
            for attr, desired_endpoint in (("from", endpoint_rewrite[0]), ("to", endpoint_rewrite[1])):
                current_endpoint = edge_attrs.get(attr, "")
                if current_endpoint != desired_endpoint:
                    edge_attrs[attr] = desired_endpoint
                    endpoint_rewritten[attr] = {"old": current_endpoint, "new": desired_endpoint}
        elif join_source_node_id:
            for attr in ("from", "to"):
                teacher_endpoint = edge_attrs.get(attr, "")
                if teacher_endpoint == teacher_junction_id:
                    edge_attrs[attr] = join_source_node_id
                    rebased_endpoints[attr] = {"teacher": teacher_endpoint, "candidate": join_source_node_id}
        if (rebased_endpoints or endpoint_rewritten) and edge_attrs.get("from") and edge_attrs.get("from") == edge_attrs.get("to"):
            skipped_rebased_self_loop_edges.append(
                {"candidate_edge_id": candidate_id, "teacher_edge_id": teacher_id, "node": edge_attrs["from"]}
            )
            continue
        if lane_shape_delta is not None and edge_attrs.get("shape"):
            edge_attrs["shape"] = _translate_shape(edge_attrs["shape"], lane_shape_delta[0], lane_shape_delta[1])
        edge = ET.SubElement(root, "edge", edge_attrs)
        for lane in teacher_lanes:
            lane_attrs = {"index": lane.attrib.get("index", "0")}
            for attr in ("allow", "disallow", "width", "speed"):
                if lane.attrib.get(attr):
                    lane_attrs[attr] = lane.attrib[attr]
            if preserve_lane_shapes and lane.attrib.get("shape"):
                lane_attrs["shape"] = (
                    _translate_shape(lane.attrib["shape"], lane_shape_delta[0], lane_shape_delta[1])
                    if lane_shape_delta is not None
                    else lane.attrib["shape"]
                )
            ET.SubElement(edge, "lane", lane_attrs)
        added_missing_mapped_edges.append(
            {"candidate_edge_id": candidate_id, "teacher_edge_id": teacher_id, "lane_count": len(teacher_lanes)}
        )
        if rebased_endpoints:
            rebased_missing_mapped_edges.append(
                {"candidate_edge_id": candidate_id, "teacher_edge_id": teacher_id, **rebased_endpoints}
            )
        if endpoint_rewritten:
            endpoint_rewritten_missing_mapped_edges.append(
                {"candidate_edge_id": candidate_id, "teacher_edge_id": teacher_id, **endpoint_rewritten}
            )
        patched.append(added_missing_mapped_edges[-1])
        existing_edge_ids.add(candidate_id)

    ET.indent(root, space="    ")
    tree.write(output_file, encoding="utf-8", xml_declaration=True)
    return {
        "status": "pass",
        "claim_status": "diagnostic-demo",
        "edge_file": str(output_file),
        "patched_edge_count": len(patched),
        "patched_edges": patched,
        "added_missing_mapped_edge_count": len(added_missing_mapped_edges),
        "added_missing_mapped_edges": added_missing_mapped_edges,
        "rebased_existing_mapped_edge_count": len(rebased_existing_mapped_edges),
        "rebased_existing_mapped_edges": rebased_existing_mapped_edges,
        "rebased_missing_mapped_edge_count": len(rebased_missing_mapped_edges),
        "rebased_missing_mapped_edges": rebased_missing_mapped_edges,
        "endpoint_rewritten_existing_mapped_edge_count": len(endpoint_rewritten_existing_mapped_edges),
        "endpoint_rewritten_existing_mapped_edges": endpoint_rewritten_existing_mapped_edges,
        "endpoint_rewritten_missing_mapped_edge_count": len(endpoint_rewritten_missing_mapped_edges),
        "endpoint_rewritten_missing_mapped_edges": endpoint_rewritten_missing_mapped_edges,
        "skipped_rebased_self_loop_edge_count": len(skipped_rebased_self_loop_edges),
        "skipped_rebased_self_loop_edges": skipped_rebased_self_loop_edges,
        "pruned_boundary_edge_count": len(pruned_boundary_edges),
        "pruned_boundary_edges": pruned_boundary_edges,
        "retained_unmapped_boundary_edge_count": len(retained_unmapped_boundary_edges),
        "retained_unmapped_boundary_edges": retained_unmapped_boundary_edges,
        "lane_shape_translation_applied": lane_shape_delta is not None,
        "preserve_lane_shapes": preserve_lane_shapes,
    }


def write_missing_edge_type_patch(
    *,
    raw_type_file: Path | None,
    edge_file: Path,
    output_file: Path,
) -> dict[str, object]:
    try:
        edge_root = ET.parse(edge_file).getroot()
        if raw_type_file is not None and raw_type_file.exists():
            type_tree = ET.parse(raw_type_file)
            type_root = type_tree.getroot()
        else:
            type_root = ET.Element("types")
            type_tree = ET.ElementTree(type_root)
    except (ET.ParseError, OSError) as exc:
        return _failure(f"could not patch edge types: {exc}")

    known_type_ids = {edge_type.attrib["id"] for edge_type in type_root.findall("type") if edge_type.attrib.get("id")}
    synthesized = []
    for edge in edge_root.findall("edge"):
        type_id = edge.attrib.get("type", "")
        if not type_id or type_id in known_type_ids:
            continue
        attrs = {"id": type_id}
        for attr in ("priority", "numLanes", "speed", "allow", "disallow", "oneway", "width"):
            if edge.attrib.get(attr):
                attrs[attr] = edge.attrib[attr]
        if "numLanes" not in attrs:
            lane_count = len(edge.findall("lane"))
            if lane_count:
                attrs["numLanes"] = str(lane_count)
        if "speed" not in attrs:
            first_lane = edge.find("lane")
            if first_lane is not None and first_lane.attrib.get("speed"):
                attrs["speed"] = first_lane.attrib["speed"]
        ET.SubElement(type_root, "type", attrs)
        known_type_ids.add(type_id)
        synthesized.append(type_id)

    removed_lane_synthesis_attributes = []
    for edge_type in type_root.findall("type"):
        type_id = str(edge_type.attrib.get("id", ""))
        for attribute in ("sidewalkWidth", "bikeLaneWidth"):
            value = edge_type.attrib.pop(attribute, None)
            if value is not None:
                removed_lane_synthesis_attributes.append(
                    {"type_id": type_id, "attribute": attribute, "value": value}
                )

    patched = bool(synthesized or removed_lane_synthesis_attributes)
    if patched:
        output_file.parent.mkdir(parents=True, exist_ok=True)
        ET.indent(type_root, space="    ")
        type_tree.write(output_file, encoding="utf-8", xml_declaration=True)
        type_file = output_file
    else:
        type_file = raw_type_file if raw_type_file is not None and raw_type_file.exists() else None
    return {
        "status": "pass",
        "claim_status": "diagnostic-demo",
        "raw_type_file": str(raw_type_file) if raw_type_file is not None else "",
        "type_file": str(type_file) if type_file is not None else "",
        "patched_type_file": str(output_file) if patched else "",
        "synthesized_edge_type_count": len(synthesized),
        "synthesized_edge_type_ids": synthesized,
        "roundtrip_lane_synthesis_attribute_removal_count": len(
            removed_lane_synthesis_attributes
        ),
        "roundtrip_lane_synthesis_attribute_removals": removed_lane_synthesis_attributes,
    }


def write_teacher_endpoint_patch_nodes(
    *,
    raw_node_file: Path,
    teacher_net_file: Path,
    edge_file: Path,
    output_file: Path,
    lane_shape_delta: tuple[float, float] | None = None,
) -> dict[str, object]:
    output_file.parent.mkdir(parents=True, exist_ok=True)
    tree = ET.parse(raw_node_file)
    root = tree.getroot()
    existing_node_ids = {node.attrib.get("id", "") for node in root.findall("node") if node.attrib.get("id")}
    needed_node_ids = {
        endpoint
        for edge in ET.parse(edge_file).getroot().findall("edge")
        for endpoint in (edge.attrib.get("from", ""), edge.attrib.get("to", ""))
        if endpoint and not endpoint.startswith(":")
    }
    missing_node_ids = sorted(needed_node_ids - existing_node_ids)
    teacher_nodes = {
        junction.attrib["id"]: junction
        for junction in ET.parse(teacher_net_file).getroot().findall("junction")
        if junction.attrib.get("id") and junction.attrib.get("type") != "internal"
    }
    added_node_ids = []
    unresolved_node_ids = []
    safe_attrs = ("id", "x", "y", "type", "shape", "radius", "keepClear", "rightOfWay", "fringe", "tl")
    dx, dy = lane_shape_delta if lane_shape_delta is not None else (0.0, 0.0)

    for node_id in missing_node_ids:
        teacher_node = teacher_nodes.get(node_id)
        if teacher_node is None:
            unresolved_node_ids.append(node_id)
            continue
        attrs = {attr: teacher_node.attrib[attr] for attr in safe_attrs if teacher_node.attrib.get(attr)}
        attrs["id"] = node_id
        if lane_shape_delta is not None:
            if attrs.get("x"):
                attrs["x"] = _format_xy(float(attrs["x"]) + dx)
            if attrs.get("y"):
                attrs["y"] = _format_xy(float(attrs["y"]) + dy)
            if attrs.get("shape"):
                attrs["shape"] = _translate_shape(attrs["shape"], dx, dy)
        ET.SubElement(root, "node", attrs)
        added_node_ids.append(node_id)

    ET.indent(root, space="    ")
    tree.write(output_file, encoding="utf-8", xml_declaration=True)
    return {
        "status": "pass" if not unresolved_node_ids else "review",
        "claim_status": "diagnostic-demo",
        "node_file": str(output_file),
        "added_missing_endpoint_node_count": len(added_node_ids),
        "added_missing_endpoint_node_ids": added_node_ids,
        "unresolved_missing_endpoint_node_ids": unresolved_node_ids,
        "node_shape_translation_applied": lane_shape_delta is not None,
    }
