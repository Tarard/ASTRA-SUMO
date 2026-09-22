"""Replay reviewed Hamburg road geometry when its source still matches."""

from __future__ import annotations

from copy import deepcopy
import math
from pathlib import Path
import xml.etree.ElementTree as ET

from shapely.geometry import LineString

from .artifact_io import write_json_atomic
from .candidate_contracts import file_sha256
from .command_runner import run_command
from .connection_mode_audit import audit_network_connection_mode
from .hamburg_lane_connection_repair import HAMBURG_DIVERGE_LANE_SHAPE

_JUNCTION = "266199070"
_BRANCH = "234421319"
_THROUGH = "24483344#0"
_INGRESS = "37693933#1"
# Source lane positions relative to the branch's downstream endpoint. These
# guards bound the existing reviewed correction; they are not a general fit.
_SOURCE_SHAPES = {
    (_BRANCH, 0): ((-105.02, 27.68), (-82.40, 18.09), (0.0, 0.0)),
    (_THROUGH, 0): ((-105.39, 26.76), (-79.72, 22.90), (-25.0, 10.32)),
    (_THROUGH, 1): ((-104.15, 29.81), (-79.12, 26.04), (-24.28, 13.44)),
}


def _points(value):
    points = [tuple(map(float, token.split(","))) for token in value.split()]
    if len(points) < 2 or any(len(p) != 2 or not all(map(math.isfinite, p)) for p in points):
        raise ValueError("Road geometry requires at least two finite 2D positions.")
    return points


def _shape(points):
    return " ".join(f"{x:.2f},{y:.2f}" for x, y in points)


def _overlap(root, points, opposing_edge=_THROUGH):
    surface = LineString(points).buffer(1.6, cap_style=2)
    return sum(surface.intersection(LineString(_points(lane.get("shape", ""))).buffer(
        float(lane.get("width", "3.2")) / 2, cap_style=2)).area
        for lane in root.findall(f"edge[@id='{opposing_edge}']/lane"))


def _plan_san_francisco(root):
    """Remove the source dogleg, keeping both end cuts and the remote tangent."""
    primary, opposing, junction_id = "9702439#0", "307556940#2", "3127482712"
    record = {"profile": "hamburg_reviewed_san_francisco_clearance/v1", "junction_id": junction_id,
              "edge_id": primary, "opposing_edge_id": opposing, "status": "not_applicable", "applied": False}
    edges = {e.get("id"): e for e in root.findall("edge")}
    scope = {primary, opposing, "307556942", "-307556942"}
    if not scope <= edges.keys():
        return {**record, "reason": "reviewed_road_set_absent"}
    record.update(status="review_required", reason="reviewed_source_geometry_or_lane_connections_changed")
    node = root.find(f"junction[@id='{junction_id}']")
    pairs = {(c.get("from"), c.get("fromLane"), c.get("to"), c.get("toLane"))
             for c in root.findall("connection") if c.get("from") in {opposing, "307556942"}}
    if (node is None or node.get("type") != "priority"
            or {k for k, e in edges.items() if junction_id in (e.get("from"), e.get("to"))} != scope
            or any(edges[key].get("from") != junction_id for key in (primary, "-307556942"))
            or any(edges[key].get("to") != junction_id for key in (opposing, "307556942"))
            or pairs != {(opposing, "0", "-307556942", "0"), ("307556942", "0", primary, "0"), ("307556942", "1", primary, "1")}
            or len(edges[primary].findall("lane")) != 2 or len(edges[opposing].findall("lane")) != 1):
        return record
    references = {(opposing, 0): ((-14.42, 28.24), (-9.56, 19.21), (0, 0)),
                  (primary, 0): ((5.44, 2.70), (2.21, 4.72), (-2.34, 23.09)),
                  (primary, 1): ((3.74, -0.02), (-0.59, 2.70), (-5.44, 22.33))}
    try:
        anchor = _points(edges[opposing].find("lane").get("shape", ""))[-1]
        lanes = edges[primary].findall("lane")
        original = [_points(lane.get("shape", "")) for lane in lanes]
        if any(not math.isfinite(float(lane.get("width", "3.2"))) or abs(float(lane.get("width", "3.2")) - 3.2) > 1e-8
               for key in (primary, opposing) for lane in edges[key].findall("lane")):
            return record
        before = sum(_overlap(root, points, opposing) for points in original)
        if before <= 0.01:
            return {**record, "status": "not_applicable", "reason": "no_exit_lane_overlap"}
        for (edge_id, index), reference in references.items():
            points = _points(edges[edge_id].findall("lane")[index].get("shape", ""))
            if len(points) != len(reference) or any(math.dist((p[0] - anchor[0], p[1] - anchor[1]), ref) > 0.03
                                                   for p, ref in zip(points, reference)):
                return record
        # Reviewed local bend relocation. Both boundary positions and the last
        # segment direction remain fixed; no lane count or permission changes.
        corrected = {str(i): [start, tuple(round(a + 0.7 * (b - a), 2) for a, b in zip(mid, end)), end]
                     for i, (start, mid, end) in enumerate(original)}
        after = sum(_overlap(root, points, opposing) for points in corrected.values())
    except (ValueError, TypeError, OverflowError):
        return record
    if after > 0.01:
        return {**record, "reason": "reviewed_correction_does_not_separate_current_lanes"}
    return {**record, "status": "pass", "reason": "reviewed_source_and_lane_connections_match",
            "lane_shapes": corrected, "before_lane_shapes": original, "before_overlap_m2": before,
            "after_proposal_overlap_m2": after, "translation_anchor": anchor,
            "basis": "Source-matched local clearance correction; 2024 orthophoto reviewed. Historical construction layout and exact field lane boundaries remain unverified."}


def plan_hamburg_road_geometry(root):
    """Translate the existing local correction, without changing the source."""
    # ponytail: one reviewed profile per crop; sequence candidates if a crop contains both sites.
    edges = {edge.get("id"): edge for edge in root.findall("edge")}
    record = {"profile": "hamburg_reviewed_266199070_diverge/v1", "junction_id": _JUNCTION,
              "edge_id": _BRANCH, "status": "not_applicable", "applied": False}
    if not {_BRANCH, _THROUGH, _INGRESS}.issubset(edges):
        return _plan_san_francisco(root)
    record.update(status="review_required", reason="reviewed_source_geometry_or_lane_connections_changed")
    junction = root.find(f"junction[@id='{_JUNCTION}']")
    expected = {(_INGRESS, "0", _BRANCH, "0"), (_INGRESS, "1", _THROUGH, "0"),
                (_INGRESS, "2", _THROUGH, "1")}
    actual = {(c.get("from"), c.get("fromLane"), c.get("to"), c.get("toLane"))
              for c in root.findall("connection") if c.get("from") == _INGRESS}
    incident = {key for key, e in edges.items() if _JUNCTION in (e.get("from"), e.get("to"))}
    if (junction is None or junction.get("type") != "priority" or actual != expected
            or incident != {_INGRESS, _BRANCH, _THROUGH, "31106387"}
            or edges[_INGRESS].get("to") != _JUNCTION
            or any(edges[k].get("from") != _JUNCTION for k in (_BRANCH, _THROUGH))
            or [len(edges[k].findall("lane")) for k in (_INGRESS, _BRANCH, _THROUGH)] != [3, 1, 2]):
        return record
    try:
        source = _points(edges[_BRANCH].find("lane").get("shape", ""))
        anchor = source[-1]
        widths = [float(lane.get("width", "3.2")) for key in (_BRANCH, _THROUGH) for lane in edges[key].findall("lane")]
        if any(not math.isfinite(width) or abs(width - 3.2) > 1e-8 for width in widths):
            return record
        overlap = _overlap(root, source)
        if overlap <= 0.01:
            return {**record, "status": "not_applicable", "reason": "no_exit_lane_overlap"}
        for (edge_id, index), reference in _SOURCE_SHAPES.items():
            lane = edges[edge_id].findall("lane")[index]
            points = _points(lane.get("shape", ""))
            if len(points) != len(reference) or any(math.dist(
                    (p[0] - anchor[0], p[1] - anchor[1]), ref) > 0.03 for p, ref in zip(points, reference)):
                return record
        corrected = [(round(x - HAMBURG_DIVERGE_LANE_SHAPE[-1][0] + anchor[0], 2),
                      round(y - HAMBURG_DIVERGE_LANE_SHAPE[-1][1] + anchor[1], 2))
                     for x, y in HAMBURG_DIVERGE_LANE_SHAPE]
        corrected[1:] = source[1:]
        if _overlap(root, corrected) > 0.01:
            return {**record, "reason": "reviewed_correction_does_not_separate_current_lanes"}
    except (ValueError, TypeError, OverflowError):
        return record
    return {**record, "status": "pass", "reason": "reviewed_source_and_lane_connections_match",
            "lane_shape": corrected, "lane_shapes": {"0": corrected}, "opposing_edge_id": _THROUGH,
            "before_lane_shape": source, "before_overlap_m2": overlap,
            "after_proposal_overlap_m2": _overlap(root, corrected), "translation_anchor": anchor,
            "basis": "existing write_hamburg_diverge_geometry_patch; translated after source-shape checks"}


def _signature(element):
    return element.tag, dict(element.attrib), [_signature(c) for c in element]


def _local(element, edge_ids, junction_id=_JUNCTION):
    identifier = element.get("id", "")
    if element.tag == "edge":
        return identifier in edge_ids or identifier.startswith(f":{junction_id}_")
    if element.tag == "junction":
        return identifier == junction_id or identifier.startswith(f":{junction_id}_")
    if element.tag == "connection":
        return any(element.get(key, "").startswith(f":{junction_id}_") for key in ("from", "to", "via"))
    return False


def build_hamburg_road_geometry_candidate(*, source_net, output_dir,
        netconvert_binary="netconvert", timeout_seconds=240.0):
    """Compile a separate local candidate and retain every outside element."""
    source = Path(source_net).resolve(strict=True)
    source_hash = file_sha256(source)
    root = ET.parse(source).getroot()
    plan = plan_hamburg_road_geometry(root)
    report = {"schema": "torii.hamburg-road-geometry/v1", **plan,
              "source_network": {"path": str(source), "sha256": source_hash}, "candidate_network": None,
              "claim_status": "diagnostic-demo", "claim_boundary":
              "Reuses a source-matched Hamburg road correction. Other roads and changed source geometry require review."}
    if plan["status"] != "pass":
        return report
    junction_id, primary = plan["junction_id"], plan["edge_id"]
    destination = Path(output_dir).resolve()
    if destination.exists():
        raise ValueError("output_dir must not already exist")
    destination.mkdir(parents=True)
    edges = {e.get("id"): e for e in root.findall("edge") if e.get("function") != "internal"}
    scope = {key for key, e in edges.items() if junction_id in (e.get("from"), e.get("to"))}
    nodes = ET.Element("nodes")
    remote_ids = {e.get(k) for key, e in edges.items() if key in scope for k in ("from", "to")} - {junction_id}
    for node in root.findall("junction"):
        if node.get("id") in remote_ids:
            ET.SubElement(nodes, "node", {k: v for k, v in node.attrib.items() if k in ("id", "x", "y", "z", "type", "shape")})
    patch = ET.Element("edges")
    edge = ET.SubElement(patch, "edge", id=primary)
    for index, shape in plan["lane_shapes"].items():
        ET.SubElement(edge, "lane", index=index, shape=_shape(shape))
    node_file, edge_file = destination / "fixed-boundaries.nod.xml", destination / "road-geometry.edg.xml"
    ET.ElementTree(nodes).write(node_file, encoding="utf-8", xml_declaration=True)
    ET.ElementTree(patch).write(edge_file, encoding="utf-8", xml_declaration=True)
    compiled = destination / "native.net.xml"
    command = [str(netconvert_binary), "--sumo-net-file", str(source), "--node-files", str(node_file),
               "--edge-files", str(edge_file), "--offset.disable-normalization", "true",
               "--junctions.internal-link-detail", "25", "--output-file", str(compiled)]
    result = run_command(command, cwd=destination, timeout_seconds=timeout_seconds)
    (destination / "netconvert.log").write_text(result.stdout + result.stderr, encoding="utf-8")
    if result.returncode != 0 or not compiled.is_file():
        raise ValueError("Netconvert could not compile the reviewed road correction.")
    native = ET.parse(compiled).getroot()
    candidate = deepcopy(root)
    for tag in ("edge", "junction", "connection"):
        old = candidate.findall(tag)
        insertion = min(candidate[:].index(e) for e in old)
        kept = [e for e in old if not _local(e, scope, junction_id)]
        rebuilt = [deepcopy(e) for e in native.findall(tag) if _local(e, scope, junction_id)]
        for element in old:
            candidate.remove(element)
        for offset, element in enumerate([*kept, *rebuilt]):
            candidate.insert(insertion + offset, element)
    rebuilt_edges = {e.get("id"): e for e in candidate.findall("edge")}
    semantics_preserved = True
    remote_endpoints_preserved = True
    for edge_id in scope:
        before, after = edges[edge_id], rebuilt_edges.get(edge_id)
        if after is None:
            semantics_preserved = False
            continue
        comparable = deepcopy(after)
        for old, new in zip(before.findall("lane"), comparable.findall("lane")):
            for key in ("shape", "length", "customShape"):
                new.attrib.pop(key, None)
                if key in old.attrib:
                    new.set(key, old.get(key))
        semantics_preserved &= _signature(before) == _signature(comparable)
        endpoint = -1 if before.get("from") == junction_id else 0
        remote_endpoints_preserved &= all(math.dist(_points(old.get("shape"))[endpoint],
            _points(new.get("shape"))[endpoint]) <= 0.03 for old, new in zip(before.findall("lane"), after.findall("lane")))
    def external_pairs(r):
        return {(c.get("from"), c.get("fromLane"), c.get("to"), c.get("toLane"),
                 c.get("tl"), c.get("linkIndex")) for c in r.findall("connection")
                if not c.get("from", "").startswith(":")}
    checked_junctions = sorted({junction_id, *remote_ids})
    audit = audit_network_connection_mode(candidate, junction_ids=checked_junctions, endpoint_tolerance_m=0.1)
    before_audit = audit_network_connection_mode(root, junction_ids=checked_junctions, endpoint_tolerance_m=0.1)
    after_overlap = sum(_overlap(candidate, _points(lane.get("shape")), plan["opposing_edge_id"])
                        for lane in rebuilt_edges[primary].findall("lane"))
    gates = {"source_immutable": file_sha256(source) == source_hash,
             "external_lane_semantics": semantics_preserved, "remote_endpoints": remote_endpoints_preserved,
             "lane_connections_and_controls": external_pairs(root) == external_pairs(candidate),
             "outside_elements": [_signature(e) for e in root if not _local(e, scope, junction_id)] ==
                                 [_signature(e) for e in candidate if not _local(e, scope, junction_id)],
             "exit_lane_overlap_removed": after_overlap <= 0.01,
             "connection_continuity": audit["structural_failure_count"] == 0 and not audit["configuration_failures"]}
    target = destination / "road-geometry.net.xml"
    # Persist every cut used by the native compile, including the unchanged
    # remote ends of other incident roads. Otherwise reload can recut them.
    fixed_junctions = [junction_id, *sorted(remote_ids)]
    for junction_id in fixed_junctions:
        candidate.find(f"junction[@id='{junction_id}']").set("customShape", "1")
    gates["outside_elements_except_declared_boundary_flag"] = gates.pop("outside_elements")
    ET.indent(candidate)
    ET.ElementTree(candidate).write(target, encoding="utf-8", xml_declaration=True)
    reloaded_file = destination / "reload.net.xml"
    reload_command = [str(netconvert_binary), "--sumo-net-file", str(target),
                      "--offset.disable-normalization", "true", "--junctions.internal-link-detail", "25",
                      "--output-file", str(reloaded_file)]
    reloaded = run_command(reload_command, cwd=destination, timeout_seconds=timeout_seconds)
    (destination / "reload.log").write_text(reloaded.stdout + reloaded.stderr, encoding="utf-8")
    stable = reloaded.returncode == 0 and reloaded_file.is_file()
    length_deltas = []
    shape_deltas = []
    if stable:
        reloaded_root = ET.parse(reloaded_file).getroot()
        for edge_id in scope:
            old = rebuilt_edges[edge_id].findall("lane")
            new = reloaded_root.findall(f"edge[@id='{edge_id}']/lane")
            stable &= len(old) == len(new)
            for before, after in zip(old, new):
                a, b = _points(before.get("shape")), _points(after.get("shape"))
                stable &= len(a) == len(b)
                shape_deltas.extend(math.dist(p, q) for p, q in zip(a, b))
            length_deltas.extend(abs(float(a.get("length")) - float(b.get("length"))) for a, b in zip(old, new))
    # Native cuts use unrounded points, then stored centimetre coordinates.
    # Bound that rounding effect in both the curves and their lengths.
    stable &= max(length_deltas, default=0.0) <= 0.03 and max(shape_deltas, default=0.0) <= 0.02
    gates["native_reload_geometry"] = stable
    report.update(status="pass" if all(gates.values()) else "blocked", applied=all(gates.values()),
                  gates=gates, after_overlap_m2=after_overlap, edited_edge_ids=sorted(scope), command=command,
                  fixed_boundary_junctions=fixed_junctions, reload_command=reload_command,
                  reload_max_length_delta_m=max(length_deltas, default=0.0),
                  reload_max_shape_delta_m=max(shape_deltas, default=0.0),
                  patch={"path": str(edge_file), "sha256": file_sha256(edge_file)},
                  connection_audit=audit, source_connection_audit=before_audit)
    if report["status"] == "pass":
        report["candidate_network"] = {"path": str(target), "sha256": file_sha256(target)}
    write_json_atomic(destination / "manifest.json", report)
    return report
