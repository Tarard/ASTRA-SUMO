"""Compile one761 lane fan-out hypothesis without changing the one-lane road."""

from pathlib import Path
import xml.etree.ElementTree as ET

from shapely.geometry import LineString

from .artifact_io import write_json_atomic
from .candidate_contracts import file_sha256
from .hamburg_209_connection_support import _boundary_attributes_match, _programs_match
from .junction_rebuild.restoration import restore_off_scope_netconvert_artifacts
from .junction_split_fanout import _canonical, _curve, _internal_path, _key
from .local_network_conversion import loaded_turn_permissions, local_conversion_input, merge_local_conversion, run_scoped_native_conversion
from .network_geometry_freeze import prepare_geometry_frozen_input
from .osm_access import _permission_set


NODE = "7115619106"
ORIGINAL = ("761590046", 0, "199500674#0", 0)
ADDED = {("761590046", 0, "199500674#0", 1), ("761590046", 0, "199500674#0", 2)}
SOURCE_SHA = "6c52382e7664eca33fceff6fd02ae700d93139a316a3f9f5139d0e139d7a18ce"
CONTROL_SHA = "27220b430aa68c24f0ad360c57ee3bd0f4b8dcdc88aead5344bcff00e4f43e3b"


def _patch(root):
    roads = {row.get("id"): row for row in root.findall("edge") if not row.get("function")}
    node = root.find(f"junction[@id='{NODE}']")
    incoming = {key for key, row in roads.items() if row.get("to") == NODE}
    outgoing = {key for key, row in roads.items() if row.get("from") == NODE}
    rows = [row for row in root.findall("connection") if row.get("from") in incoming]
    if (node is None or node.get("type") != "priority" or node.get("incLanes") != "761590046_0"
            or incoming != {ORIGINAL[0]} or outgoing != {ORIGINAL[2]} or len(rows) != 1 or _key(rows[0]) != ORIGINAL
            or any(name in rows[0].attrib for name in ("tl", "linkIndex", "linkIndex2"))
            or len(roads[ORIGINAL[0]].findall("lane")) != 1 or len(roads[ORIGINAL[2]].findall("lane")) != 3):
        raise ValueError("Require the original one-to-three priority boundary with only lane0 connected.")
    access = loaded_turn_permissions(root)[ORIGINAL]
    lanes = {int(row.get("index")): row for row in roads[ORIGINAL[2]].findall("lane")}
    patch, allowed = ET.Element("connections"), {}
    original_path = _internal_path(root, rows[0])
    original = {key: value for key, value in rows[0].attrib.items() if key not in {"via", "dir", "state", "shape", "length", "speed"}}
    original.update(shape=" ".join(row.get("shape") for row in original_path), speed=original_path[0].get("speed"))
    ET.SubElement(patch, "connection", original)
    for key in sorted(ADDED):
        modes = access & _permission_set(lanes[key[3]].attrib, network_version=root.get("version"))
        if "passenger" not in modes:
            raise ValueError("Require source passenger access at each existing target lane.")
        allowed[key] = modes
        ET.SubElement(patch, "connection", {"from": key[0], "fromLane": "0", "to": key[2], "toLane": str(key[3]),
            "allow": " ".join(sorted(modes))})
    return patch, allowed


def _verify(before, after, native):
    roads = [{row.get("id"): row for row in root.findall("edge") if not row.get("function")} for root in (before, after)]
    access_before, access_after = loaded_turn_permissions(before), loaded_turn_permissions(after)
    _, expected_access = _patch(before)
    rows = [{_key(row): row for row in root.findall("connection") if row.get("from") in roads[0]} for root in (before, after)]
    first, last = [_internal_path(root, group[ORIGINAL]) for root, group in zip((before, after), rows, strict=True)]
    curve_before, curve_after = _curve(before, rows[0][ORIGINAL]), _curve(after, rows[1][ORIGINAL])
    ignored = {"via", "shape", "length", "speed", "allow", "disallow"}
    old_attributes = [{key: value for key, value in group[ORIGINAL].attrib.items() if key not in ignored} for group in rows]
    lane_ignored = {"id", "index", "shape", "length", "speed", "allow", "disallow"}
    lane_attributes_exact = len(first) == len(last) and all(
        {k: float(v) if k == "width" else v for k, v in a.attrib.items() if k not in lane_ignored}
        == {k: float(v) if k == "width" else v for k, v in b.attrib.items() if k not in lane_ignored}
        and _permission_set(a.attrib, network_version=before.get("version")) == _permission_set(b.attrib, network_version=after.get("version"))
        and [_canonical(row) for row in a] == [_canonical(row) for row in b] for a, b in zip(first, last, strict=False))

    def exterior(root):
        return [_canonical(row) for row in root if not (
            row.tag in {"edge", "junction"} and (row.get("id") == NODE or row.get("id", "").startswith(":" + NODE + "_"))
            or row.tag == "connection" and (row.get("from", "").startswith(":" + NODE + "_") or row.get("from") == ORIGINAL[0]))]

    nodes = [root.find(f"junction[@id='{NODE}']") for root in (after, native)]
    new_paths = {key: _internal_path(after, rows[1][key]) for key in ADDED if key in rows[1]}
    paths = {ORIGINAL: last, **new_paths}
    geometric_lengths = {key: _curve(after, rows[1][key]).length for key in paths}
    native_mean = sum(geometric_lengths.values()) / 3
    native_rows = {_key(row): row for row in native.findall("connection")}

    def original_internal_attributes(root, path):
        result = []
        for lane in path:
            rows = [row for row in root.findall("connection") if row.get("from") == lane.get("id").rsplit("_", 1)[0]
                and row.get("fromLane") == lane.get("index") and row.get("to") == ORIGINAL[2] and row.get("toLane") == "0"]
            if len(rows) != 1:
                raise ValueError("Require one original internal continuation per source path lane.")
            result.append(({key: value for key, value in rows[0].attrib.items() if key not in {"from", "fromLane", "via"}},
                [_canonical(row) for row in rows[0]]))
        return result
    checks = {
        "ordinary_roads_xml_exact": roads[0].keys() == roads[1].keys() and all(_canonical(row) == _canonical(roads[1][key]) for key, row in roads[0].items()),
        "only_two_turns_added_with_loaded_access": access_after.keys() == access_before.keys() | ADDED
            and all(access_after.get(key) == modes for key, modes in access_before.items())
            and all(access_after.get(key) == modes for key, modes in expected_access.items()),
        "outside_elements_and_order_exact": exterior(before) == exterior(after),
        "all_programs_exact": _programs_match(before, after),
        "target_attributes_exact": _boundary_attributes_match(before.find(f"junction[@id='{NODE}']"), nodes[0]),
        "original_internal_curve_and_speed_preserved": curve_before.equals_exact(curve_after, 1e-6)
            and [float(row.get("speed")) for row in first] == [float(row.get("speed")) for row in last],
        "original_connection_runtime_attributes_exact": old_attributes[0] == old_attributes[1]
            and [_canonical(row) for row in rows[0][ORIGINAL]] == [_canonical(row) for row in rows[1][ORIGINAL]],
        "original_internal_lane_width_access_and_LC_exact": lane_attributes_exact,
        "original_internal_connection_attributes_exact": original_internal_attributes(before, first) == original_internal_attributes(after, last),
        "all_target_connection_attributes_are_native": all(_canonical(rows[1][key]) == _canonical(native_rows[key]) for key in paths),
        "new_paths_are_native_and_nonempty": new_paths.keys() == ADDED and all(path and all(float(row.get("length")) > 0 for row in path) for path in new_paths.values())
            and all([_canonical(row) for row in path] == [_canonical(row) for row in _internal_path(native,
                next(row for row in native.findall("connection") if _key(row) == key))] for key, path in new_paths.items()),
        "only_native_group_length_normalization": paths.keys() == ADDED | {ORIGINAL}
            and all(len(path) == 1 and abs(float(path[0].get("length")) - native_mean) <= 1e-9 for path in paths.values())
            and all([_canonical(row) for row in path] == [_canonical(row) for row in _internal_path(native,
                next(row for row in native.findall("connection") if _key(row) == key))] for key, path in paths.items()),
        "target_movements_remain_unsignalized": all(not any(name in row.attrib for name in ("tl", "linkIndex", "linkIndex2"))
            for key, row in rows[1].items() if key in ADDED | {ORIGINAL}),
        "native_requests_not_overlaid": nodes[0].get("intLanes") == nodes[1].get("intLanes")
            and [_canonical(row) for row in nodes[0].findall("request")] == [_canonical(row) for row in nodes[1].findall("request")]
            and len(nodes[0].findall("request")) == 3,
    }
    geometry = {"original_curve_offset_m": curve_before.hausdorff_distance(curve_after),
        "source_original_length_m": sum(float(row.get("length")) for row in first),
        "candidate_original_length_m": sum(float(row.get("length")) for row in last),
        "native_expected_group_length_m": native_mean,
        "native_group_formula": "(original_native_arc_length + added_lane1_native_arc_length + added_lane2_native_arc_length) / 3; no custom length",
        "native_group_mean_geometric_length_m": sum(geometric_lengths.values()) / 3,
        "native_group_geometric_lengths": [{"movement": key, "arc_length_m": value} for key, value in sorted(geometric_lengths.items())],
        "added_paths": [{"movement": key, "internal_lanes": [row.get("id") for row in path],
            "length_m": sum(float(row.get("length")) for row in path)} for key, path in sorted(new_paths.items())]}
    return checks, geometry


def materialize_hamburg_761_connection_support(*, source_file, control_file, output_dir, netconvert_binary):
    """Run scoped netconvert only, keeping source geometry and all failed artifacts."""
    source, control, out = Path(source_file).resolve(strict=True), Path(control_file).resolve(strict=True), Path(output_dir).resolve()
    if out.exists() or any(path.is_relative_to(out) for path in (source, control)):
        raise ValueError("Use a new candidate directory separate from both immutable inputs.")
    if file_sha256(source) != SOURCE_SHA or file_sha256(control) != CONTROL_SHA:
        raise ValueError("Require the frozen remove43 network and external control bytes.")
    before = ET.parse(source).getroot()
    patch, _ = _patch(before)
    out.mkdir(parents=True)
    copied_control = out / "control.add.xml"
    copied_control.write_bytes(control.read_bytes())
    hashes = {str(path): file_sha256(path) for path in (source, control, Path(__file__))}
    report = {"schema": "astra.hamburg.761-connection-support/v1", "status": "blocked", "formal_result": False,
        "source_hashes": hashes, "traffic_simulation_run": False, "checks": {}, "added_movements": sorted(ADDED),
        "control_file": str(copied_control), "control_sha256": file_sha256(copied_control),
        "native_internal_length_policy": "No .con custom length is supplied. SUMO1.27.1 NBEdge::assignInternalLaneLength averages the three native geometric lengths sharing one straight internal edge. The original curve is preserved, but its derived logical length changes to that native group mean. The width/access/LC guard excludes that declared length change. This is not an ordinary-road length edit or surveyed path-length claim.",
        "declared_mutable_internal_attributes": ["lane.length on the three native continuations at7115619106 only"],
        "claim_boundary": "One source-supported lane fan-out hypothesis at a priority node. The one-lane761 road, all ordinary roads, access, control and demand are unchanged. MAP ordinals do not supply SUMO curves or independent field-turn proof. Native curves and ROW require later driving review; no field correction, safety or count-fit claim."}
    try:
        frozen, patch_file, candidate = out / "geometry-input.net.xml", out / "connections.con.xml", out / "candidate.net.xml"
        report["geometry_input"] = prepare_geometry_frozen_input(source_file=source, output_file=frozen)
        ET.ElementTree(patch).write(patch_file, encoding="utf-8", xml_declaration=True)
        incident = {ORIGINAL[0], ORIGINAL[2]}
        execution, conversion = run_scoped_native_conversion(command=[str(netconvert_binary), "--sumo-net-file", str(frozen),
            "--connection-files", str(patch_file), "--offset.disable-normalization", "true", "--precision", "12", "--output-file", str(candidate)],
            mutable_nodes={NODE}, mutable_edges=incident, output_dir=out, prefix="fanout", timeout_seconds=120, merge_source_file=source)
        report["execution"], report["conversion"] = execution.to_dict(), conversion
        if execution.status != "pass" or conversion["status"] != "local":
            raise ValueError("Require a successful single-node local conversion.")
        report["restoration"] = restore_off_scope_netconvert_artifacts(source_file=source, target_file=candidate,
            mutable_junction_ids={NODE}, mutable_edge_ids=set(), expand_mutable_edge_endpoints=False)
        if report["restoration"]["status"] != "pass":
            raise ValueError("Outside-scope restoration failed.")
        after = ET.parse(candidate).getroot()
        first_node, last_node = [root.find(f"junction[@id='{NODE}']") for root in (before, after)]
        outlines = [LineString([tuple(map(float, point.split(",")[:2])) for point in row.get("shape").split()]) for row in (first_node, last_node)]
        if (any(abs(float(first_node.get(name, "0")) - float(last_node.get(name, "0"))) > 1e-6 for name in ("x", "y", "z"))
                or outlines[0].hausdorff_distance(outlines[1]) > 1e-6):
            raise ValueError("Native conversion moved the target boundary.")
        for name in ("x", "y", "z", "shape", "customShape"):
            last_node.attrib.pop(name, None)
            if first_node.get(name) is not None:
                last_node.set(name, first_node.get(name))
        subset, _ = local_conversion_input(after, {NODE}, incident)
        after = merge_local_conversion(before, subset, conversion)
        ET.ElementTree(after).write(candidate, encoding="utf-8", xml_declaration=True)
        native = ET.parse(conversion["native_output"]["path"]).getroot()
        report["checks"], report["geometry"] = _verify(before, after, native)
        report["checks"]["external_control_bytes_exact"] = copied_control.read_bytes() == control.read_bytes()
        report["checks"]["source_files_immutable"] = all(file_sha256(Path(path)) == digest for path, digest in hashes.items())
        if all(report["checks"].values()):
            final = out / "network.net.xml"
            final.write_bytes(candidate.read_bytes())
            report.update(status="review_required", candidate_file=str(final), candidate_sha256=file_sha256(final))
    except Exception as error:
        report["error"] = {"type": type(error).__name__, "message": str(error)}
    report["source_unchanged"] = all(file_sha256(Path(path)) == digest for path, digest in hashes.items())
    write_json_atomic(out / "manifest.json", report)
    return report
