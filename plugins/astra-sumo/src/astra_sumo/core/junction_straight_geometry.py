"""Order explicitly selected straight-movement vertices without inventing coordinates."""

import math
from pathlib import Path
import xml.etree.ElementTree as ET

from shapely.geometry import LineString, Point

from .artifact_io import write_json_atomic
from .junction_split_fanout import _compile_connection_patch, _curve, _key


def ordered_straight_vertices(points, *, lane_width_m=3.2):
    """Order forward vertices within one lane width of the straight endpoint chord."""
    points = [tuple(map(float, point)) for point in points]
    if (len(points) < 2 or any(len(p) != 2 or not all(map(math.isfinite, p)) for p in points)
            or not math.isfinite(lane_width_m) or lane_width_m <= 0):
        raise ValueError("Use finite two-dimensional points and a positive lane width.")
    first, last = points[0], points[-1]
    dx, dy = last[0] - first[0], last[1] - first[1]
    length = math.hypot(dx, dy)
    if length == 0:
        raise ValueError("Straight movement anchors must be distinct.")
    progress = [((x - first[0]) * dx + (y - first[1]) * dy) / length for x, y in points]
    if any(p < 0 or p > length + 1e-8 for p in progress):
        raise ValueError("Straight vertices must stay between their anchors.")
    if any(abs((x - first[0]) * dy - (y - first[1]) * dx) / length > lane_width_m for x, y in points):
        raise ValueError("Vertices leave the straight movement's lane-width envelope.")
    return [first, *[point for _, point in sorted(zip(progress[1:-1], points[1:-1]), key=lambda row: row[0])], last]


def materialize_ordered_straights(*, source_file, junction_id, connection_keys, output_dir,
                                  netconvert_binary="netconvert"):
    """Create a scoped review candidate for selected backtracking straight curves."""
    source, out = Path(source_file).resolve(strict=True), Path(output_dir).resolve()
    before = ET.parse(source).getroot()
    ordinary = {e.get("id"): e for e in before.findall("edge") if not e.get("function")}
    connections = {_key(c): c for c in before.findall("connection") if c.get("from") in ordinary}
    keys = [tuple(key) for key in connection_keys]
    if not keys or len(set(keys)) != len(keys) or any(key not in connections for key in keys):
        raise ValueError("Select distinct existing straight movements.")
    patch = ET.Element("connections")
    changes = []
    for key in keys:
        row = connections[key]
        if row.get("dir") != "s" or ordinary[key[0]].get("to") != junction_id:
            raise ValueError("Select straight movements at the declared junction.")
        old = list(_curve(before, row).coords)
        lane = before.find(f"edge/lane[@id='{row.get('via')}']")
        if lane is None:
            raise ValueError("The selected straight movement has no loaded internal lane.")
        ordered = ordered_straight_vertices(old, lane_width_m=float(lane.get("width", "3.2")))
        if ordered == old:
            raise ValueError("The selected straight movement has no longitudinal reversal.")
        attrs = {name: value for name, value in row.attrib.items() if name not in {"via", "dir", "state", "shape"}}
        attrs["shape"] = " ".join(f"{x:.9f},{y:.9f}" for x, y in ordered)
        ET.SubElement(patch, "connection", attrs)
        changes.append({"connection_key": key, "before": old, "ordered": ordered})
    report = _compile_connection_patch(source, junction_id, patch, out, netconvert_binary)
    after = ET.parse(report["candidate_file"]).getroot()
    new_connections = {_key(c): c for c in after.findall("connection") if c.get("from") in ordinary}
    unselected = {}
    for key, row in connections.items():
        if key in keys or ordinary[key[0]].get("to") != junction_id:
            continue
        old, new = _curve(before, row), _curve(after, new_connections[key])
        if not old.equals_exact(new, 1e-4):
            raise ValueError("Compilation changed an unselected movement's ordered vertices.")
        unselected[key] = old.hausdorff_distance(new)
    for change in changes:
        key = tuple(change["connection_key"])
        expected = change["ordered"]
        actual = list(_curve(after, new_connections[key]).coords)
        remaining = iter(enumerate(expected))
        retained = []
        for point in actual:
            match = next((i for i, old in remaining if math.dist(point, old) < 1e-4), None)
            if match is None:
                raise ValueError("Compilation introduced or reordered selected vertices.")
            retained.append(match)
        removed = [point for i, point in enumerate(expected) if i not in retained]
        # SUMO's native geometry cleanup can drop vertices less than 0.1 m apart.
        if (math.dist(actual[0], expected[0]) > 1e-4 or math.dist(actual[-1], expected[-1]) > 1e-4
                or any(Point(point).distance(LineString(actual)) > .1 for point in removed)):
            raise ValueError("Compilation did not retain the selected forward vertex order.")
        change["native_nearby_vertices_removed"] = removed
    if max(unselected.values(), default=0) > 1e-4:
        raise ValueError("Compilation changed an unselected movement curve.")
    report.update(connection_keys=keys, vertex_order_changes=changes,
        unselected_ordered_vertices_unchanged=True,
        maximum_unselected_curve_offset_m=max(unselected.values(), default=0),
        claim_boundary="Explicit straight-curve vertex order candidate. Ordinary lengths, access, signals and unselected curves are protected. Physical review and paired native driving remain required.")
    write_json_atomic(out / "manifest.json", report)
    return report
