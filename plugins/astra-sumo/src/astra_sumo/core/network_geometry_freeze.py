"""Prepare explicit geometry inputs and audit native SUMO rebuild changes."""

from __future__ import annotations

from pathlib import Path
import math
import xml.etree.ElementTree as ET
from shapely.geometry import LineString

from .candidate_contracts import file_sha256


def _lane_count_automatic_pedestrian_nodes(root):
    """Keep automatic pedestrian junctions available for walking-area rebuilding."""
    from .osm_access import _permission_set

    pedestrian_nodes = {edge.get(end) for edge in root.findall("edge") if not edge.get("function")
        for lane in edge.findall("lane") if "pedestrian" in _permission_set(lane.attrib, network_version=root.get("version", "0.0"))
        for end in ("from", "to")}
    return frozenset(j.get("id") for j in root.findall("junction")
        if j.get("type") != "internal" and j.get("customShape", "false") not in {"true", "1"}
        and j.get("id") in pedestrian_nodes)


def prepare_geometry_frozen_input(*, source_file: Path, output_file: Path,
                                  mutable_edge_ids: frozenset[str] = frozenset(),
                                  mutable_junction_ids: frozenset[str] = frozenset()) -> dict:
    """Write geometry hints separately. Validate the rebuilt output before use."""
    source, target = source_file.resolve(), output_file.resolve()
    if source == target or target.exists():
        raise ValueError("Geometry freeze requires a new input file separate from its source.")
    before = file_sha256(source)
    tree = ET.parse(source)
    root = tree.getroot()
    hints = _freeze_geometry_hints(root, mutable_edge_ids, mutable_junction_ids)
    if file_sha256(source) != before:
        raise ValueError("Source changed during geometry freeze.")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(ET.tostring(root, encoding="utf-8", xml_declaration=True))
    return {"status": "pass", "source": {"path": str(source), "sha256": before},
            "frozen_input": {"path": str(target), "sha256": file_sha256(target)}, **hints,
            "claim_boundary": "Frozen import geometry still requires a post-rebuild comparison. Internal paths, pedestrian facilities and control are not certified here."}


def _freeze_geometry_hints(root, mutable_edge_ids, mutable_junction_ids):
    """Apply the same source-derived hints for writing and independent reconstruction."""
    edge_ids = {e.get("id") for e in root.findall("edge") if not e.get("function")}
    junction_ids = {j.get("id") for j in root.findall("junction") if j.get("type") != "internal"}
    if mutable_edge_ids - edge_ids or mutable_junction_ids - junction_ids:
        raise ValueError("Geometry scope references an unknown external edge or junction.")
    frozen_lanes, frozen_junctions, removed_defaults, pinned_lengths = [], [], [], []
    for edge in root.findall("edge"):
        if edge.get("function") or edge.get("id") in mutable_edge_ids:
            continue
        lanes = edge.findall("lane")
        lengths = {float(lane.get("length", "nan")) for lane in lanes}
        if "length" not in edge.attrib and len(lengths) == 1:
            # SUMO subtracts the mean lane endOffset from an explicitly loaded edge length.
            loaded = next(iter(lengths)) + sum(float(lane.get("endOffset", "0")) for lane in lanes) / len(lanes)
            edge.set("length", str(loaded))
            pinned_lengths.append(edge.get("id"))
        for lane in edge.findall("lane"):
            if not lane.get("shape"):
                raise ValueError(f"Lane has no geometry to freeze: {lane.get('id')}")
            lane.set("customShape", "true")
            frozen_lanes.append(lane.get("id"))
    for junction in root.findall("junction"):
        if junction.get("type") != "internal" and junction.get("id") not in mutable_junction_ids and junction.get("shape"):
            junction.set("customShape", "true")
            frozen_junctions.append(junction.get("id"))
    # These defaults synthesize extra lanes on reimport. Existing lanes already carry their geometry.
    for road_type in root.findall("type"):
        for attribute in ("sidewalkWidth", "bikeLaneWidth"):
            if attribute in road_type.attrib:
                removed_defaults.append({"type_id": road_type.get("id"), "attribute": attribute,
                                         "value": road_type.attrib.pop(attribute)})
    return {"frozen_lane_ids": sorted(frozen_lanes), "frozen_junction_ids": sorted(frozen_junctions),
            "mutable_edge_ids": sorted(mutable_edge_ids), "mutable_junction_ids": sorted(mutable_junction_ids),
            "pinned_operational_length_edge_ids": sorted(pinned_lengths),
            "removed_lane_synthesis_defaults": removed_defaults}


def compare_frozen_geometry(*, source_file: Path, candidate_file: Path,
                            mutable_edge_ids: frozenset[str] = frozenset(),
                            mutable_junction_ids: frozenset[str] = frozenset(),
                            boundary_edge_map: dict[str, str] | None = None) -> dict:
    """Reject exact geometric changes outside scope without restoring the candidate."""
    def shape(element):
        return tuple(tuple(float(v) for v in token.split(",")) for token in element.get("shape", "").split())

    def inventory(root):
        lanes = {lane.get("id"): (shape(lane), float(lane.get("length", "nan")), float(lane.get("width", "3.2")))
                 for edge in root.findall("edge") if not edge.get("function") and edge.get("id") not in mutable_edge_ids
                 for lane in edge.findall("lane")}
        junctions = {j.get("id"): (shape(j), tuple(j.get(key) for key in ("x", "y", "z"))) for j in root.findall("junction")
                     if j.get("type") != "internal" and j.get("id") not in mutable_junction_ids}
        edges = {e.get("id"): (shape(e), e.get("from"), e.get("to"), e.get("spreadType"))
                 for e in root.findall("edge") if not e.get("function") and e.get("id") not in mutable_edge_ids}
        location = root.find("location")
        frame = (location.get("projParameter"), tuple(float(v) for v in location.get("netOffset", "0,0").split(","))) if location is not None else None
        return lanes, junctions, edges, frame

    source_root, candidate_root = ET.parse(source_file).getroot(), ET.parse(candidate_file).getroot()
    original, candidate = inventory(source_root), inventory(candidate_root)
    differences = {}
    for name, before, after in zip(("lanes", "junctions", "edges"), original[:3], candidate[:3]):
        differences[name] = {"missing": sorted(before.keys() - after.keys()),
                             "added": sorted(after.keys() - before.keys()),
                             "changed": sorted(key for key in before.keys() & after.keys() if before[key] != after[key])}
    frame_preserved = original[3] == candidate[3]
    boundary_failures = []
    boundary_edge_map = dict(boundary_edge_map or {})
    source_edge_ids = {edge.get("id") for edge in source_root.findall("edge") if not edge.get("function")}
    candidate_edges = {e.get("id"): e for e in candidate_root.findall("edge") if not e.get("function")}
    if (len(set(boundary_edge_map.values())) != len(boundary_edge_map)
            or any(a not in source_edge_ids or b not in candidate_edges
                   or a not in mutable_edge_ids or b not in mutable_edge_ids
                   for a, b in boundary_edge_map.items())):
        boundary_failures.append({"reason": "invalid_boundary_correspondence"})
    for edge_id, edge in candidate_edges.items():
        if (edge_id in mutable_edge_ids and edge_id not in source_edge_ids and edge_id not in boundary_edge_map.values()
                and any(edge.get(key) not in mutable_junction_ids for key in ("from", "to"))):
            boundary_failures.append({"edge_id": edge_id, "reason": "new_boundary_edge_has_no_correspondence"})
    for edge in source_root.findall("edge"):
        if edge.get("function") or edge.get("id") not in mutable_edge_ids:
            continue
        fixed_ends = [(key, index) for key, index in (("from", 0), ("to", -1))
                      if edge.get(key) not in mutable_junction_ids]
        if not fixed_ends:
            continue
        mapped = edge.get("id") in boundary_edge_map
        after = candidate_edges.get(boundary_edge_map.get(edge.get("id"), edge.get("id")))
        before_lanes = {lane.get("index") if mapped else lane.get("id"): lane for lane in edge.findall("lane")}
        after_lanes = {lane.get("index") if mapped else lane.get("id"): lane for lane in after.findall("lane")} if after is not None else {}
        if after is None or before_lanes.keys() != after_lanes.keys():
            boundary_failures.append({"edge_id": edge.get("id"), "reason": "boundary_lane_identity_changed"})
            continue
        for key, index in fixed_ends:
            for lane_id, lane in before_lanes.items():
                before_shape, after_shape = shape(lane), shape(after_lanes[lane_id])
                if (edge.get(key) != after.get(key) or not before_shape or not after_shape
                        or before_shape[index] != after_shape[index]):
                    boundary_failures.append({"edge_id": edge.get("id"), "lane_id": lane_id,
                                              "end": key, "reason": "fixed_boundary_endpoint_changed"})
    passed = (frame_preserved and not boundary_failures
              and not any(values for layer in differences.values() for values in layer.values()))
    shared_lanes = original[0].keys() & candidate[0].keys()
    attributes = {name: sum(original[0][lane][index] != candidate[0][lane][index] for lane in shared_lanes)
                  for index, name in enumerate(("shape", "length", "width"))}
    endpoint_offsets, geometric_length_changes = [], []
    endpoint_offsets_xyz, geometric_length_changes_xyz = [], []
    shape_displacements = []
    for lane in shared_lanes:
        before, after = original[0][lane][0], candidate[0][lane][0]
        if len(before) < 2 or len(after) < 2:
            continue
        if before == after:
            endpoint_offsets.extend((0.0, 0.0))
            endpoint_offsets_xyz.extend((0.0, 0.0))
            geometric_length_changes.append(0.0)
            geometric_length_changes_xyz.append(0.0)
            shape_displacements.append(0.0)
            continue
        endpoint_offsets.extend(math.hypot(before[i][0] - after[i][0], before[i][1] - after[i][1]) for i in (0, -1))
        lengths = [sum(math.hypot(b[0] - a[0], b[1] - a[1]) for a, b in zip(points, points[1:]))
                   for points in (before, after)]
        geometric_length_changes.append(abs(lengths[0] - lengths[1]))
        spatial = [[point + (0.0,) * (3 - len(point)) for point in points] for points in (before, after)]
        endpoint_offsets_xyz.extend(math.dist(spatial[0][i], spatial[1][i]) for i in (0, -1))
        lengths_xyz = [sum(math.dist(a, b) for a, b in zip(points, points[1:])) for points in spatial]
        geometric_length_changes_xyz.append(abs(lengths_xyz[0] - lengths_xyz[1]))
        shape_displacements.append(LineString([point[:2] for point in before]).hausdorff_distance(
            LineString([point[:2] for point in after])))
    return {"status": "pass" if passed else "blocked", "coordinate_frame_preserved": frame_preserved,
            "mutable_junction_ids": sorted(mutable_junction_ids), "mutable_edge_ids": sorted(mutable_edge_ids),
            "ordinary_lane_attribute_changes": attributes,
            "max_lane_endpoint_offset_xy_m": max(endpoint_offsets, default=None),
            "max_geometric_lane_length_change_xy_m": max(geometric_length_changes, default=None),
            "max_lane_endpoint_offset_xyz_m": max(endpoint_offsets_xyz, default=None),
            "max_geometric_lane_length_change_xyz_m": max(geometric_length_changes_xyz, default=None),
            "outside_lane_count_with_shape_changes": attributes["shape"],
            "shape_displacement_metric": "xy_hausdorff",
            "outside_lane_count_moved_over_0_01_m": sum(value > 0.01 for value in shape_displacements) if frame_preserved else None,
            "max_outside_lane_shape_displacement_xy_m": max(shape_displacements, default=None) if frame_preserved else None,
            "fixed_boundary_failures": boundary_failures,
            "boundary_edge_map": boundary_edge_map,
            "outside_scope_geometry": differences}
