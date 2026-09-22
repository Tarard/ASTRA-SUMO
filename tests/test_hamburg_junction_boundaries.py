from __future__ import annotations

import shutil
import hashlib
import subprocess
import xml.etree.ElementTree as ET

import pytest

from astra_sumo.core.hamburg_aerial_corridor_candidate import _select_join_groups
from astra_sumo.core import hamburg_aerial_corridor_candidate as candidate_module
from astra_sumo.core.hamburg_junctions import boundaries as boundary_module, groups as group_module


def test_join_does_not_absorb_a_road_beyond_official_lane_boundary(monkeypatch) -> None:
    root = ET.fromstring('''<net>
      <junction id="J" x="0" y="0"/><junction id="R" x="15" y="0"/>
      <edge id="short_exit" from="J" to="R"><lane id="short_exit_0" length="15" shape="0,0 15,0"/></edge>
    </net>''')
    plan = {
        "_official_lane_b_points_network": {"i0": (-4, -1.6), "i1": (-4, 1.6), "o0": (4, -1.6), "o1": (4, 1.6)},
        "movements": [
            {"ingress_lane_id": "i0", "egress_lane_id": "o0", "intersection_part": "0", "selected_shape_network": [(-20, -1.6), (20, -1.6)]},
            {"ingress_lane_id": "i1", "egress_lane_id": "o1", "intersection_part": "0", "selected_shape_network": [(-20, 1.6), (20, 1.6)]},
        ],
    }
    binding = {"bindings": [{"node_id": "999", "tls_ids": ["J", "R"]}]}
    monkeypatch.setattr(group_module, "_official_lane_boundary_points", lambda *_: plan["_official_lane_b_points_network"])
    groups = _select_join_groups(root, binding, {"999": plan})
    assert groups[0]["source_node_ids"] == ["J"]
    assert groups[0]["boundary_excluded_node_ids"] == ["R"]


def test_official_core_keeps_connected_nodes_even_when_internal_edge_exceeds_20m(monkeypatch) -> None:
    root = ET.fromstring('<net><junction id="J" x="0" y="0"/><junction id="R" x="30" y="0"/><edge id="inside" from="J" to="R"><lane id="inside_0" length="30" shape="0,0 30,0"/></edge></net>')
    points = {"i0": (-40, -5), "i1": (-40, 5), "o0": (40, -5), "o1": (40, 5)}
    monkeypatch.setattr(group_module, "_official_lane_boundary_points", lambda *_: points)
    plan = {"movements": [{"ingress_lane_id": f"i{n}", "egress_lane_id": f"o{n}", "intersection_part": "0", "selected_shape_network": [(-50, 0), (50, 0)]} for n in (0, 1)]}
    groups = _select_join_groups(root, {"bindings": [{"node_id": "999", "tls_ids": ["J"]}]}, {"999": plan})
    assert groups[0]["source_node_ids"] == ["J", "R"]


def test_boundary_node_ownership_uses_road_surface_not_only_node_center(monkeypatch) -> None:
    root = ET.fromstring('<net><junction id="J" x="0" y="0"/><junction id="R" x="12" y="0" shape="3,-3 14,-3 14,3 3,3"/><junction id="outside" x="25" y="0" shape="22,-3 28,-3 28,3 22,3"/><edge id="inside" from="J" to="R"><lane length="12" shape="0,0 12,0"/></edge><edge id="outside_road" from="R" to="outside"><lane length="13" shape="12,0 25,0"/></edge></net>')
    points = {"i0": (-4, -1.6), "i1": (-4, 1.6), "o0": (4, -1.6), "o1": (4, 1.6)}
    monkeypatch.setattr(group_module, "_official_lane_boundary_points", lambda *_: points)
    plan = {"movements": [{"ingress_lane_id": f"i{n}", "egress_lane_id": f"o{n}", "intersection_part": "0", "selected_shape_network": [(-20, 0), (30, 0)]} for n in (0, 1)]}
    groups = _select_join_groups(root, {"bindings": [{"node_id": "999", "tls_ids": ["J"]}]}, {"999": plan})
    assert groups[0]["source_node_ids"] == ["J", "R"]
    assert groups[0]["boundary_anchor_owner_node_ids"] == ["R"]


def test_lane_port_width_can_overlap_junction_when_its_center_does_not(monkeypatch) -> None:
    root = ET.fromstring('<net><junction id="J" x="0" y="0"/><junction id="R" x="12" y="2" shape="3,1 14,1 14,4 3,4"/><edge id="inside" from="J" to="R"><lane length="12" shape="0,0 12,2"/></edge></net>')
    points = {"in": (-4, 0), "out": (4, 0), "north": (0, 4)}
    monkeypatch.setattr(group_module, "_official_lane_boundary_points", lambda *_: points)
    monkeypatch.setattr(boundary_module, "_official_boundary_widths", lambda *_: {key: 3.25 for key in points}, raising=False)
    plan = {"lanes": [{"lane_id": key, "shape_network": [point, (point[0] * 4, point[1] * 4)]} for key, point in points.items()], "movements": [{"ingress_lane_id": key, "egress_lane_id": "out", "intersection_part": "0", "selected_shape_network": [points[key], (20, 0)]} for key in ("in", "north")]}
    groups = _select_join_groups(root, {"bindings": [{"node_id": "999", "tls_ids": ["J"]}]}, {"999": plan})
    assert groups[0]["source_node_ids"] == ["J", "R"]


def test_internal_road_face_owns_stopline_even_outside_legacy_junction_polygon(monkeypatch) -> None:
    root = ET.fromstring('<net><junction id="J" x="0" y="0"/><junction id="R" x="12" y="0" shape="10,3 14,3 14,5 10,5"/><edge id="inside" from="J" to="R"><lane length="12" shape="0,0 3,0"/></edge><edge id=":R_0" function="internal"><lane id=":R_0_0" width="3.2" shape="3,0 14,0"/></edge></net>')
    points = {"in": (-4, 0), "out": (4, 0), "north": (0, 4)}
    monkeypatch.setattr(group_module, "_official_lane_boundary_points", lambda *_: points)
    plan = {"movements": [{"ingress_lane_id": key, "egress_lane_id": "out", "intersection_part": "0", "selected_shape_network": [points[key], (20, 0)]} for key in ("in", "north")]}
    groups = _select_join_groups(root, {"bindings": [{"node_id": "999", "tls_ids": ["J"]}]}, {"999": plan})
    assert groups[0]["source_node_ids"] == ["J", "R"]


def test_official_internal_curve_identifies_fragment_outside_endpoint_polygon(monkeypatch) -> None:
    root = ET.fromstring('<net><junction id="J" x="0" y="0"/><junction id="R" x="2" y="8" shape="1,6 3,6 3,10 1,10"/><edge id="inside" from="J" to="R"><lane length="6" shape="0,0 1,6"/></edge></net>')
    points = {"in": (-4, 0), "out": (4, 0), "north": (0, 4)}
    monkeypatch.setattr(group_module, "_official_lane_boundary_points", lambda *_: points)
    monkeypatch.setattr(group_module, "_official_internal_paths", lambda *_: {"paths": {("in", "out"): [(-4, 0), (2, 8), (4, 0)]}, "unusable": []}, raising=False)
    plan = {"movements": [{"ingress_lane_id": key, "egress_lane_id": "out", "intersection_part": "0", "selected_shape_network": [points[key], (20, 0)]} for key in ("in", "north")]}
    groups = _select_join_groups(root, {"bindings": [{"node_id": "999", "tls_ids": ["J"]}]}, {"999": plan})
    assert groups[0]["source_node_ids"] == ["J", "R"]
    assert groups[0]["official_movement_owner_node_ids"] == ["R"]


def test_core_connectivity_uses_actual_lane_geometry_not_edge_reference_axis(monkeypatch) -> None:
    root = ET.fromstring('<net><junction id="J" x="0" y="0"/><junction id="R" x="20" y="0"/><edge id="inside" from="J" to="R" shape="0,100 20,100"><lane length="20" shape="0,0 20,0"/></edge><edge id=":R_0" function="internal"><lane shape="20,0 20,0"/></edge></net>')
    points = {"i0": (-30, -5), "i1": (-30, 5), "o0": (30, -5), "o1": (30, 5)}
    monkeypatch.setattr(group_module, "_official_lane_boundary_points", lambda *_: points)
    plan = {"movements": [{"ingress_lane_id": f"i{n}", "egress_lane_id": f"o{n}", "intersection_part": "0", "selected_shape_network": [(-30, 0), (30, 0)]} for n in (0, 1)]}
    groups = _select_join_groups(root, {"bindings": [{"node_id": "999", "tls_ids": ["J"]}]}, {"999": plan})
    assert groups[0]["source_node_ids"] == ["J", "R"]


def test_official_port_width_uses_centimeter_deltas_at_stopline(tmp_path) -> None:
    from astra_sumo.core.hamburg_aerial_corridor_candidate import _official_boundary_widths

    path = tmp_path / "map.xml"
    path.write_text('<MapData><IntersectionGeometry><laneWidth>325</laneWidth><laneSet><GenericLane><laneID>1</laneID><nodeList><nodes><NodeXY><attributes><dWidth>-25</dWidth><localNode><stopLine/></localNode></attributes></NodeXY></nodes></nodeList></GenericLane><GenericLane><laneID>2</laneID><nodeList><nodes><NodeXY/></nodes></nodeList></GenericLane></laneSet></IntersectionGeometry></MapData>', encoding="utf-8")
    plan = {"inputs": {"map_xml": {"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}}}
    assert _official_boundary_widths(plan) == {"1": 3.0, "2": 3.25}


def test_part_assignment_uses_stopline_core_instead_of_full_drive_line_tail(monkeypatch) -> None:
    root = ET.fromstring('<net><junction id="J" x="0" y="0"/><junction id="K" x="100" y="0"/></net>')
    points = {f"{part}{role}{n}": (center + (-5 if role == "i" else 5), -3 if n == 0 else 3) for part, center in (("0", 0), ("1", 100)) for role in ("i", "o") for n in (0, 1)}
    monkeypatch.setattr(group_module, "_official_lane_boundary_points", lambda *_: points)
    plan = {"movements": [{"ingress_lane_id": f"{part}i{n}", "egress_lane_id": f"{part}o{n}", "intersection_part": part, "selected_shape_network": [(0, 0), (300, 0)] if part == "0" else [(90, 0), (110, 0)]} for part in ("0", "1") for n in (0, 1)]}
    groups = _select_join_groups(root, {"bindings": [{"node_id": "999", "tls_ids": ["J", "K"]}]}, {"999": plan})
    assert {group["intersection_part"]: group["source_node_ids"] for group in groups} == {"0": ["J"], "1": ["K"]}
    assert all(group["assigned_tls_node_count"] == 1 for group in groups)


def test_internal_official_stopline_cannot_replace_an_actual_external_cut_port():
    root = ET.fromstring('''<net>
      <junction id="U" x="-12" y="0"/><junction id="J" x="0" y="0"/>
      <junction id="F" x="8" y="0"/><junction id="E" x="40" y="0"/><junction id="N" x="0" y="40"/>
      <edge id="entry" from="U" to="J"><lane id="entry_0" index="0" width="4" length="1" shape="-10,0 -9,0"/></edge>
      <edge id="inside" from="J" to="F"><lane id="inside_0" index="0" length="8" shape="0,0 8,0"/></edge>
      <edge id="out" from="F" to="E"><lane id="out_0" index="0" length="28" shape="12,0 40,0"/></edge>
      <edge id="north" from="F" to="N"><lane id="north_0" index="0" length="31" shape="8,10 0,40"/></edge>
      <connection from="entry" to="inside" fromLane="0" toLane="0"/>
      <connection from="inside" to="out" fromLane="0" toLane="0"/>
      <connection from="inside" to="north" fromLane="0" toLane="0"/>
    </net>''')
    points = {"mid": (8.0, 0.0), "exit": (12.0, 0.0), "north": (8.0, 10.0)}
    plan = {"lanes": [
        {"lane_id": "mid", "lane_type": "vehicle", "direction_role": "ingress", "shape_network": [(-12, 0), points["mid"]], "junction_endpoint_network": points["mid"]},
        {"lane_id": "exit", "lane_type": "vehicle", "direction_role": "egress", "shape_network": [points["exit"], (40, 0)], "junction_endpoint_network": points["exit"]},
        {"lane_id": "north", "lane_type": "vehicle", "direction_role": "egress", "shape_network": [points["north"], (0, 40)], "junction_endpoint_network": points["north"]},
    ], "movements": [{"movement_id": str(index), "intersection_part": "0", "ingress_lane_id": "mid", "egress_lane_id": target} for index, target in enumerate(("exit", "north"))]}
    group = {"node_id": "999", "intersection_part": "0", "join_id": "joined", "source_node_ids": ["J", "F"], "official_boundary_shape": list(points.values())}
    classification = candidate_module._classify_boundary_ports(root, plan, group, points)
    assert [row["lane_id"] for row in classification["official_internal_anchors"]] == ["mid"]
    assert {row["lane_id"] for row in classification["official_external_ports"]} == {"exit", "north"}
    group["boundary_port_classification"] = classification
    polygon, report = candidate_module._resolved_boundary_polygon(root, group)
    fallback = next(row for row in report["ports"] if row["source_lane_id"] == "entry_0")
    assert fallback["basis"] == "native_joined_cut_lane"
    assert fallback["width_m"] == 4.0
    assert fallback["shape"] == [(-9.0, 2.0), (-9.0, -2.0)]
    assert not any(point == points["mid"] for point in polygon)
    assert any(row["lane_id"] == "exit" and row["basis"] == "official_external_B" for row in report["ports"])
    assert min(point[0] for point in polygon) <= -9
    shifted_points = {**points, "mid": (8.0, 0.4)}
    shifted_plan = {**plan, "lanes": [{**row, "shape_network": [(-12, 0.4), (8, 0.4)]} if row["lane_id"] == "mid" else row for row in plan["lanes"]]}
    strict = candidate_module._classify_boundary_ports(root, shifted_plan, group, shifted_points, maximum_lane_projection_error_m=0.2)
    assert any(row["lane_id"] == "mid" for row in strict["official_unresolved_anchors"])
    parallel = ET.SubElement(root, "edge", id="parallel", **{"from": "J", "to": "F"})
    ET.SubElement(parallel, "lane", id="parallel_0", index="0", length="8", shape="0,0.4 8,0.4")
    for target in ("out", "north"):
        ET.SubElement(root, "connection", **{"from": "parallel", "to": target, "fromLane": "0", "toLane": "0"})
    uncertain = candidate_module._classify_boundary_ports(root, plan, group, points, minimum_lane_match_margin_m=1.0)
    assert any(row["lane_id"] == "mid" for row in uncertain["official_unresolved_anchors"])


@pytest.mark.skipif(shutil.which("netconvert") is None, reason="netconvert is not installed")
def test_rebuilt_boundary_keeps_native_external_port_and_official_external_exits(tmp_path):
    nodes, edges, source, joined = [tmp_path / name for name in ("nodes.xml", "edges.xml", "source.net.xml", "joined.net.xml")]
    nodes.write_text('<nodes><node id="W" x="-60" y="0"/><node id="U" x="-15" y="0"/><node id="J" x="0" y="0"/><node id="F" x="20" y="0"/><node id="E" x="70" y="0"/><node id="N" x="20" y="60"/><node id="S" x="0" y="-60"/></nodes>', encoding="utf-8")
    edges.write_text('<edges><edge id="upstream" from="W" to="U"/><edge id="entry" from="U" to="J" width="4"/><edge id="inside" from="J" to="F"/><edge id="out" from="F" to="E"/><edge id="north" from="F" to="N"/><edge id="branch" from="J" to="S"/></edges>', encoding="utf-8")
    subprocess.run(["netconvert", "-n", str(nodes), "-e", str(edges), "-o", str(source), "--offset.disable-normalization"], check=True, capture_output=True, timeout=30)
    joins = tmp_path / "joins.xml"
    joins.write_text('<nodes><join nodes="J F" id="joined"/></nodes>', encoding="utf-8")
    subprocess.run(["netconvert", "-s", str(source), "-n", str(joins), "-o", str(joined), "--offset.disable-normalization"], check=True, capture_output=True, timeout=30)
    original, native = ET.parse(source).getroot(), ET.parse(joined).getroot()
    lanes, points = [], {}
    for lane_id, edge_id, role in (("mid", "inside", "ingress"), ("exit", "out", "egress"), ("north", "north", "egress")):
        shape = candidate_module._parse_shape(original.find(f"edge[@id='{edge_id}']/lane").get("shape"))
        point = shape[-1] if role == "ingress" else shape[0]
        points[lane_id] = point
        lanes.append({"lane_id": lane_id, "lane_type": "vehicle", "direction_role": role, "shape_network": shape, "junction_endpoint_network": point})
    plan = {"lanes": lanes, "movements": [{"movement_id": str(index), "ingress_lane_id": "mid", "egress_lane_id": target} for index, target in enumerate(("exit", "north"))]}
    group = {"node_id": "999", "intersection_part": "0", "join_id": "joined", "source_node_ids": ["J", "F"], "official_boundary_shape": list(points.values())}
    group["boundary_port_classification"] = candidate_module._classify_boundary_ports(original, plan, group, points)
    output = tmp_path / "bounded.net.xml"
    report = candidate_module._rebuild_join_boundaries(joined, output, [group], netconvert_binary="netconvert", timeout_seconds=30)
    result = ET.parse(output).getroot()
    before = candidate_module._parse_shape(native.find("edge[@id='entry']/lane").get("shape"))[-1]
    after = candidate_module._parse_shape(result.find("edge[@id='entry']/lane").get("shape"))[-1]
    assert abs(before[0] - after[0]) <= 0.1
    assert abs(before[1] - after[1]) <= 0.1
    assert float(result.find("edge[@id='entry']/lane").get("width")) == 4
    ports = report["boundaries"][0]["ports"]
    assert not any(port["lane_id"] == "mid" for port in ports)
    assert {port["lane_id"] for port in ports if port["basis"] == "official_external_B"} == {"exit", "north"}
    assert {edge.get("id") for edge in native.findall("edge") if edge.get("function") != "internal"} == {edge.get("id") for edge in result.findall("edge") if edge.get("function") != "internal"}
    assert candidate_module.audit_network_connection_mode(result, endpoint_tolerance_m=0.1)["structural_failure_count"] == 0
    roundtrip = tmp_path / "roundtrip.net.xml"
    subprocess.run(["netconvert", "-s", str(output), "-o", str(roundtrip), "--offset.disable-normalization"], check=True, capture_output=True, timeout=30)
    reloaded = ET.parse(roundtrip).getroot()
    for edge_id in ("entry", "out", "north", "branch"):
        before_lane = result.find(f"edge[@id='{edge_id}']/lane")
        after_lane = reloaded.find(f"edge[@id='{edge_id}']/lane")
        assert before_lane.attrib == after_lane.attrib
    assert report["cut_geometry"]["roundtrip_stable"] is True


def test_road_port_profiles_keep_endpoint_rays_out_of_interleaved_spikes():
    from astra_sumo.road_network.official_splice_materializer import _segment_intersection
    import math

    def port(edge, index, center, section, direction, role="ingress"):
        return {"source_edge_id": edge, "source_lane_index": index, "lane_id": f"{edge}{index}",
                "junction_endpoint_network": center, "native_cut_endpoint": center, "shape": section,
                "native_lane_direction": direction, "role": role}
    ports = [
        port("A", 1, (6.322, 19.581), [(7.778, 18.859), (4.866, 20.302)], (0.45, 0.89)),
        port("A", 0, (8.827, 17.212), [(10.275, 16.476), (7.378, 17.949)], (0.45, 0.89)),
        port("B", 1, (6.4, 6.08), [(5.531, 7.423), (7.231, 4.713)], (0.85, 0.53)),
        port("B", 0, (8.1, 3.37), [(7.231, 4.713), (8.969, 2.027)], (0.85, 0.53)),
        port("E", 0, (55, 80), [(55, 82), (55, 78)], (1, 0), "egress"),
        port("N", 0, (20, 100), [(18, 100), (22, 100)], (0, 1), "egress"),
    ]
    polygon, profiles = candidate_module._portal_boundary_polygon(ports, [(0, 0), (55, 0), (55, 110), (0, 110)])
    assert len(profiles) == 4
    b = ports[1]["junction_endpoint_network"]
    direction = (0.45, 0.89)
    a = tuple(b[i] - 30 * direction[i] for i in (0, 1))
    end = tuple(b[i] + 150 * direction[i] for i in (0, 1))
    intersections = [p for left, right in zip(polygon, [*polygon[1:], polygon[0]]) if (p := _segment_intersection(a, end, left, right)) is not None]
    unique = []
    for point in intersections:
        if not any(math.dist(point, old) < 0.01 for old in unique):
            unique.append(point)
    assert len(unique) == 2
    assert min(math.dist(b, point) for point in unique) < 0.01
    assert b in polygon


def test_historical_lsa200_road_order_uses_current_full_width_mouths():
    # Three real southwest mouths from the frozen 2020 LSA200 source. The
    # old native contour is a positioning reference, not topology ground truth.
    native = [(1401.29, 502.02), (1422.61, 499.93), (1423.09, 496.44), (1425.17, 495.58),
              (1428.32, 496.78), (1432.03, 499.49), (1435.78, 503.17), (1439.05, 507.26),
              (1446.93, 501.78), (1445.41, 499.77), (1444.14, 498.27), (1443.14, 496.98),
              (1442.42, 495.61), (1441.98, 493.86), (1441.85, 491.41), (1438.64, 492.17),
              (1450.32, 459.94), (1431.81, 438.86), (1427.08, 442.06), (1421.83, 443.81),
              (1416.07, 444.11), (1409.78, 442.95), (1402.97, 440.35), (1395.64, 436.30),
              (1392.24, 441.72), (1394.81, 442.82), (1395.42, 442.55), (1395.58, 441.73),
              (1395.29, 440.38), (1394.56, 438.47), (1378.25, 446.38), (1379.61, 448.95),
              (1379.88, 449.96), (1379.97, 451.01), (1379.93, 452.29), (1379.83, 453.97),
              (1383.03, 454.11), (1380.27, 482.30), (1372.92, 521.00), (1375.54, 519.52),
              (1378.01, 518.80), (1380.34, 518.85), (1382.53, 519.66), (1384.57, 521.24),
              (1386.46, 523.57), (1391.78, 520.01)]
    rows = [
        ("350033476", 0, "egress", (1381.509, 449.657), (1379.70, 445.68), (-1.86, -3.01), [(1382.938, 448.937), (1380.081, 450.378)]),
        ("350033476", 1, "egress", (1383.971, 447.555), (1382.58, 444.28), (-0.05, -0.10), [(1385.421, 446.823), (1382.520, 448.287)]),
        ("186713403#0", 0, "ingress", (1398.827, 453.572), (1395.47, 444.33), (0.07, 0.18), [(1400.275, 452.836), (1397.378, 454.309)]),
        ("186713403#0", 1, "ingress", (1396.322, 455.941), (1392.56, 445.68), (0.07, 0.18), [(1397.778, 455.219), (1394.866, 456.662)]),
        ("551288834", 0, "ingress", (1406.003, 447.375), (1398.11, 439.74), (0.17, 0.11), [(1407.205, 446.282), (1404.801, 448.469)]),
        ("551288834", 1, "ingress", (1403.600, 449.578), (1396.40, 442.45), (0.17, 0.11), [(1404.802, 448.484), (1402.398, 450.672)]),
        ("north-fixture", 0, "egress", (1395, 480), (1395, 520), (0, 1), [(1405, 480), (1385, 480)]),
    ]
    ports = [{"source_edge_id": edge, "source_lane_index": index, "lane_id": f"{edge}_{index}", "role": role,
              "junction_endpoint_network": current, "native_cut_endpoint": old, "native_lane_direction": direction, "shape": shape}
             for edge, index, role, current, old, direction, shape in rows]
    polygon, profiles = candidate_module._portal_boundary_polygon(ports, native)
    order = [row["source_edge_id"] for row in profiles]
    start = order.index("350033476")
    assert (order[start:] + order[:start])[:3] == ["350033476", "186713403#0", "551288834"]
    assert all(port["junction_endpoint_network"] in polygon for port in ports)
    assert all(row["basis"] == "current_port_position_on_native_boundary" for row in profiles)


def test_crossing_physical_port_profiles_are_reported_not_reordered_away():
    ports = [{"source_edge_id": "a", "source_lane_index": 0, "lane_id": "a0", "role": "ingress", "junction_endpoint_network": (0, 0), "native_cut_endpoint": (0, 0), "native_lane_direction": (0, 1), "shape": [(-2, 0), (2, 0)]},
             {"source_edge_id": "b", "source_lane_index": 0, "lane_id": "b0", "role": "egress", "junction_endpoint_network": (0, 0), "native_cut_endpoint": (0, 0), "native_lane_direction": (1, 0), "shape": [(0, -2), (0, 2)]}]
    with pytest.raises(ValueError, match="overlap"):
        candidate_module._portal_boundary_polygon(ports, [(-5, -5), (5, -5), (5, 5), (-5, 5)])
    ports[1]["shape"] = [(-1, 0), (3, 0)]
    with pytest.raises(ValueError, match="overlap"):
        candidate_module._portal_boundary_polygon(ports, [(-5, -5), (5, -5), (5, 5), (-5, 5)])


def test_unknown_cut_port_must_follow_the_remote_owner_exit_with_full_lane_width():
    root = ET.fromstring('''<net><junction id="U" shape="-5,-2 0,-2 0,2 -5,2"/>
      <junction id="J"/><edge id="short" from="U" to="J" shape="-5,0 -1,0">
      <lane id="short_0" index="0" width="2" shape="-1.2,0 -1,0"/>
      </edge></net>''')
    edge = root.find("edge")
    port = candidate_module._safe_native_cut_port(root, edge, edge.find("lane"), "ingress", maximum_movement_m=10)
    assert port["junction_endpoint_network"] == pytest.approx((0.2, 0))
    assert port["shape"][0] == pytest.approx((0.2, 1))
    assert port["shape"][1] == pytest.approx((0.2, -1))
    assert port["adjustment"]["remote_owner"] == "U"
    assert port["adjustment"]["shift_m"] == pytest.approx(1.2)
    root.find("junction[@id='U']").set("shape", "-5,-2 0,-2 0.5,0.5 0,2 -5,2")
    wide = candidate_module._safe_native_cut_port(root, edge, edge.find("lane"), "ingress", maximum_movement_m=10)
    assert wide["junction_endpoint_network"][0] == pytest.approx(0.7)
    root.find("junction[@id='U']").set("shape", "-5,-2 -2,-2 -2,2 -5,2")
    edge.set("shape", "-5,0 1,0 1,5")
    edge.find("lane").set("shape", "0,0 1,0")
    unchanged = candidate_module._safe_native_cut_port(root, edge, edge.find("lane"), "ingress", maximum_movement_m=10)
    assert unchanged["shape"] == [(1.0, 1.0), (1.0, -1.0)]


def test_unlocated_driveway_can_move_upstream_without_moving_official_ports():
    root = ET.fromstring('''<net><junction id="U" shape="-2,11 2,11 2,13 -2,13"/><junction id="J"/>
      <edge id="drive" from="U" to="J" shape="0,10 0,0"><lane id="drive_0" index="0" width="2" shape="0,10 0,0" allow="delivery"/></edge></net>''')
    ports = [
        {"source_edge_id": "drive", "source_lane_id": "drive_0", "lane_id": None, "role": "ingress", "width_m": 2,
         "junction_endpoint_network": (0, 0), "shape": [(-1, 0), (1, 0)], "basis": "native_joined_cut_lane"},
        {"source_edge_id": "main", "source_lane_id": "main_0", "lane_id": "10", "role": "ingress", "width_m": 4,
         "junction_endpoint_network": (0.5, 0), "shape": [(0.5, -2), (0.5, 2)], "basis": "official_external_B"},
    ]
    before = ET.tostring(root)
    adjusted, reviews = candidate_module._adjust_native_port_conflicts(root, ports, maximum_movement_m=10)
    assert not reviews
    assert adjusted[0]["junction_endpoint_network"] == pytest.approx((0, 2.1), abs=0.001)
    assert adjusted[0]["width_m"] == 2
    assert adjusted[0]["adjustment"]["upstream_distance_m"] == pytest.approx(2.1, abs=0.001)
    assert adjusted[1] == ports[1]
    assert ET.tostring(root) == before
    unchanged, limited = candidate_module._adjust_native_port_conflicts(root, ports, maximum_movement_m=1)
    assert limited
    assert unchanged[0]["junction_endpoint_network"] == (0, 0)


@pytest.mark.skipif(shutil.which("netconvert") is None, reason="netconvert is not installed")
def test_custom_boundary_restores_approach_without_removing_edges(tmp_path) -> None:
    from astra_sumo.core.hamburg_aerial_corridor_candidate import _rebuild_join_boundaries

    nodes = tmp_path / "nodes.xml"
    edges = tmp_path / "edges.xml"
    nodes.write_text('<nodes><node id="west" x="-40" y="0"/><node id="J" x="0" y="0" type="priority" shape="-39,-10 39,-10 39,10 -39,10"/><node id="east" x="40" y="0"/><node id="north" x="0" y="40"/></nodes>', encoding="utf-8")
    edges.write_text('<edges><edge id="in" from="west" to="J" numLanes="1"/><edge id="out" from="J" to="east" numLanes="1"/><edge id="branch" from="J" to="north" numLanes="1"/></edges>', encoding="utf-8")
    source = tmp_path / "raw.net.xml"
    run = subprocess.run(["netconvert", "-n", str(nodes), "-e", str(edges), "-o", str(source), "--offset.disable-normalization"], capture_output=True, text=True)
    assert run.returncode == 0, run.stderr
    before = ET.parse(source).getroot()
    groups = [{"join_id": "J", "official_boundary_shape": [(-4, -3.2), (4, -3.2), (4, 3.2), (-4, 3.2)]}]
    target = tmp_path / "bounded.net.xml"
    report = _rebuild_join_boundaries(source, target, groups, netconvert_binary="netconvert", timeout_seconds=60)
    after = ET.parse(target).getroot()
    assert report["status"] == "pass"
    assert float(before.find("edge[@id='in']/lane").get("length")) < 2
    assert float(after.find("edge[@id='in']/lane").get("length")) > 30
    assert {edge.get("id") for edge in before.findall("edge") if edge.get("function") != "internal"} == {edge.get("id") for edge in after.findall("edge") if edge.get("function") != "internal"}
def _bidirectional_cut_fixture():
    root = ET.fromstring('''<net><junction id="U" shape="-5,10 5,10 5,15 -5,15"/><junction id="J"/>
      <edge id="in" from="U" to="J" shape="0,10 0,0"><lane id="in_0" width="3.2" shape="-1.6,10 -1.6,0"/></edge>
      <edge id="out" from="J" to="U" shape="0,0 0,10"><lane id="out_0" width="3.2" shape="1.6,0 1.6,10"/></edge></net>''')
    ports = []
    for name, role, x, dy in (("in", "ingress", -1.6, -1), ("out", "egress", 1.6, 1)):
        ports.append({"source_edge_id": name, "source_lane_id": name + "_0", "source_lane_index": 0,
                      "role": role, "width_m": 3.2, "junction_endpoint_network": (x, 0),
                      "native_cut_endpoint": (x, 0), "native_lane_direction": (0, dy),
                      "shape": [(x - 1.6, 0), (x + 1.6, 0)], "basis": "native_joined_cut_lane"})
    ports.append({"source_edge_id": "main", "shape": [(-1, -0.5), (-1, 2)], "basis": "official_external_B"})
    return root, ports


def test_unlocated_bidirectional_road_uses_one_full_width_cut():
    root, ports = _bidirectional_cut_fixture()
    adjusted, reviews = candidate_module._adjust_native_port_conflicts(root, ports, maximum_movement_m=10)
    incoming, outgoing = adjusted[:2]
    assert incoming["junction_endpoint_network"][1] > 2
    assert outgoing["junction_endpoint_network"][1] == pytest.approx(incoming["junction_endpoint_network"][1])
    assert incoming["road_profile_id"] == outgoing["road_profile_id"]
    assert incoming["width_m"] == outgoing["width_m"] == 3.2
    assert not reviews


def test_common_cut_rejects_same_crossing_point_at_different_lane_positions():
    root, ports = _bidirectional_cut_fixture()
    root.find("edge[@id='out']/lane").set("shape", "1.6,0 1.6,3 1.6,0 1.6,10")
    adjusted, reviews = candidate_module._adjust_native_port_conflicts(root, ports, maximum_movement_m=10)
    assert reviews
    assert adjusted[:2] == ports[:2]


def test_oblique_common_cut_checks_its_full_section_against_remote_owner():
    import math

    root, ports = _bidirectional_cut_fixture()
    root.find("edge[@id='out']/lane").set("shape", f"1.6,0 {1.6 + 10 * math.tan(math.radians(40))},10")
    root.find("junction[@id='U']").set("shape", "5.35,2.05 5.55,2.05 5.55,2.15 5.35,2.15")
    adjusted, reviews = candidate_module._adjust_native_port_conflicts(root, ports, maximum_movement_m=10)
    assert reviews
    assert adjusted[:2] == ports[:2]
