import xml.etree.ElementTree as ET
import shutil
import subprocess

import pytest

from astra_sumo.core.junction_rebuild.boundary_restore import _restore_existing_edge_geometry
from astra_sumo.core.junction_rebuild.geometry import _blend_geometry_anchor_at_endpoint, _polyline_length, _set_edge_lengths_from_shapes
from astra_sumo.core.official_movement_composition import _original_lane_bindings
from astra_sumo.core.source_movement_support import _index
from astra_sumo.core.hamburg_junctions.boundaries import _resolved_boundary_polygon


def test_restored_normal_lanes_share_the_mean_spatial_length():
    target = ET.fromstring('<edge id="a"><lane id="a_0" index="0" length="99"/><lane id="a_1" index="1" length="99"/></edge>')
    source = ET.fromstring('''<edge id="a"><lane id="a_0" index="0" shape="0,0 3,0,4"/>
        <lane id="a_1" index="1" shape="0,3 6,3,8"/></edge>''')
    _restore_existing_edge_geometry(target, source, ET.Element("net"))
    assert [lane.get("length") for lane in target.findall("lane")] == ["7.50", "7.50"]
    assert _polyline_length("0,0 3,0,4") == 5


@pytest.mark.skipif(shutil.which("netconvert") is None, reason="SUMO required")
@pytest.mark.parametrize("offset", [0, 5, 10])
def test_native_explicit_edge_length_survives_geometry_length_refresh(tmp_path, offset):
    nodes, edges, network = [tmp_path / name for name in ("nodes.xml", "edges.xml", "network.net.xml")]
    nodes.write_text('<nodes><node id="a" x="0" y="0"/><node id="b" x="100" y="0"/></nodes>', encoding="utf-8")
    edges.write_text(f'<edges><edge id="road" from="a" to="b" numLanes="2" length="500"><lane index="0" endOffset="{offset}"/></edge></edges>', encoding="utf-8")
    result = subprocess.run(["netconvert", "-n", str(nodes), "-e", str(edges), "-o", str(network)], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    edge = ET.parse(network).getroot().find("edge[@id='road']")
    assert float(edge.get("length")) == 500
    native = [float(lane.get("length")) for lane in edge.findall("lane")]
    _set_edge_lengths_from_shapes(edge)
    assert [float(lane.get("length")) for lane in edge.findall("lane")] == native == [500 - offset / 2] * 2


def test_geometry_refresh_keeps_native_coupled_bidirectional_lengths():
    edge = ET.fromstring('''<edge id="a" bidi="b"><lane length="90" shape="0,0 10,0"/>
        <lane length="90" shape="0,3 12,3"/></edge>''')
    _set_edge_lengths_from_shapes(edge)
    assert [lane.get("length") for lane in edge.findall("lane")] == ["90", "90"]


def test_endpoint_blending_preserves_unchanged_local_port_and_explicit_length_precision():
    anchor = ET.fromstring('''<edge id="a"><lane id="a_0" index="0" length="35.351140"
        shape="904.690817,771.137424 893.601710,737.570554"/></edge>''')
    replay = ET.fromstring('''<edge id="a" length="35.351140"><lane id="a_0" index="0" length="35.351140"
        shape="904.690817,771.137425 893.601710,737.570554"/></edge>''')
    assert _blend_geometry_anchor_at_endpoint(replay, anchor, target_at_start=False)
    assert replay.find("lane").get("shape") == anchor.find("lane").get("shape")
    assert replay.find("lane").get("length") == "35.351140"


def test_endpoint_blending_recomputes_one_normal_edge_length():
    anchor = ET.fromstring('''<edge id="a" shape="0,0 10,0"><lane id="a_0" index="0" length="10" shape="0,0 10,0"/>
        <lane id="a_1" index="1" length="10" shape="0,3 10,3"/></edge>''')
    replay = ET.fromstring('''<edge id="a" shape="0,0 10,0"><lane id="a_0" index="0" length="99" shape="0,0 10,0"/>
        <lane id="a_1" index="1" length="99" shape="0,3 14,3"/></edge>''')
    assert _blend_geometry_anchor_at_endpoint(replay, anchor, target_at_start=False)
    assert [lane.get("length") for lane in replay.findall("lane")] == ["12.00", "12.00"]
    assert replay.findall("lane")[1].get("shape").endswith("14.00,3.00")


def test_parallel_official_lanes_use_distinct_source_lanes_despite_a_common_lateral_offset():
    root = ET.fromstring('''<net><junction id="w"/><junction id="j"/>
      <edge id="road" from="w" to="j"><lane id="road_0" index="0" allow="passenger" shape="0,0 20,0"/>
        <lane id="road_1" index="1" allow="passenger" shape="0,3.2 20,3.2"/></edge></net>''')
    lanes = {key: {"lane_id": key, "lane_type": "vehicle", "direction_role": "ingress", "ingress_approach": "1",
                   "shape_network": [(0, y), (20, y)], "junction_endpoint_network": (20, y)}
             for key, y in (("2", 2.8), ("3", 6.0))}
    bindings, reviews = _original_lane_bindings(_index(root), lanes, {"j"}, 10, .5)
    assert bindings == {"2": "road_0", "3": "road_1"}
    assert all(row["binding_method"] == "equal_lane_count_and_consistent_common_section_order" for row in reviews)
    first = next(row for row in reviews if row["lane_id"] == "2")
    assert first["binding_before_ordered_fit"] == "road_1"
    assert {source_id for _, source_id in first["candidates"]} == {"road_0", "road_1"}


def test_turning_internal_lengths_are_not_averaged_by_normal_road_restoration():
    target = ET.fromstring('''<edge id=":j_0" function="internal"><lane index="0" length="99" shape="0,0 5,0"/>
        <lane index="1" length="99" shape="0,3 8,3"/></edge>''')
    source = ET.fromstring('''<edge id=":j_0" function="internal"><lane index="0" shape="0,0 5,0"/>
        <lane index="1" shape="0,3 8,3"/></edge>''')
    _restore_existing_edge_geometry(target, source, ET.Element("net"))
    assert [float(lane.get("length")) for lane in target.findall("lane")] == [5, 8]


def test_official_boundary_covers_laterally_displaced_source_lane_strips():
    root = ET.fromstring('''<net><junction id="j" shape="-5,-5 10,-5 10,15 -5,15"/>
      <edge id="entry" from="w" to="j"><lane id="entry_0" width="3.2" shape="-20,2.8 -5,2.8"/>
        <lane id="entry_1" width="3.2" shape="-20,6 -5,6"/></edge>
      <edge id="exit" from="j" to="e"><lane id="exit_0" width="3.2" shape="10,0 30,0"/></edge>
      <edge id="north" from="j" to="n"><lane id="north_0" width="3.2" shape="5,10 5,30"/></edge></net>''')
    points = {"entry_0": (0, 0), "entry_1": (0, 3.2), "exit_0": (10, 0), "north_0": (5, 10)}
    ports = []
    for lane_id, center in points.items():
        vertical = lane_id == "north_0"
        section = [(center[0] - 1.6, center[1]), (center[0] + 1.6, center[1])] if vertical else [(center[0], center[1] - 1.6), (center[0], center[1] + 1.6)]
        ports.append({"lane_id": lane_id, "source_lane_id": lane_id, "source_edge_id": lane_id.rsplit("_", 1)[0],
                      "role": "ingress" if lane_id.startswith("entry") else "egress", "width_m": 3.2,
                      "junction_endpoint_network": center, "shape": section, "basis": "official_external_B"})
    before = ET.tostring(root)
    polygon, report = _resolved_boundary_polygon(root, {"join_id": "j", "source_node_ids": ["j"],
        "boundary_port_classification": {"official_external_ports": ports}})
    assert polygon
    for port in report["ports"]:
        assert port["junction_endpoint_network"] == points[port["source_lane_id"]]
        if port["role"] == "ingress":
            assert abs(port["source_lateral_offset_m"]) > 2.7
            assert max(point[1] for point in port["shape"]) >= port["native_center_at_official_section"][1] + 1.59
    entry_profile = next(row for row in report["portal_profiles"] if row["source_edge_id"] == "entry")
    positions = [point[1] for point in entry_profile["points"]]
    assert positions == sorted(positions) or positions == sorted(positions, reverse=True)
    assert ET.tostring(root) == before
