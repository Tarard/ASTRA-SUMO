from __future__ import annotations

import shutil
import subprocess
import xml.etree.ElementTree as ET
from copy import deepcopy

import pytest
from pyproj import Transformer

from torii_sumo.core.candidate_contracts import file_sha256
from torii_sumo.core import hamburg_aerial_approach as approach_module
from torii_sumo.core.hamburg_aerial_approach import (
    _approach_proposal,
    _boundary_adjustment_scope,
    _preserved_boundary_edge,
    build_hamburg_aerial_approach_candidate,
)


def _source_and_plan(tmp_path, distant_lane=False, reuse_boundary=False, right_pocket=False, through_count=2, full_length_bus=False):
    nodes = tmp_path / "nodes.xml"
    edges = tmp_path / "edges.xml"
    nodes.write_text('<nodes><node id="w" x="-160" y="0"/><node id="s" x="-100" y="0"/><node id="J" x="0" y="0"/><node id="e" x="100" y="0"/></nodes>')
    edges.write_text('<edges><edge id="before" from="w" to="s" numLanes="2" spreadType="center"/><edge id="approach" from="s" to="J" numLanes="2" spreadType="center" speed="11" allow="passenger bus"/><edge id="after" from="J" to="e" numLanes="2" spreadType="center"/></edges>')
    if through_count == 1:
        edges.write_text(edges.read_text().replace('id="approach" from="s" to="J" numLanes="2"', 'id="approach" from="s" to="J" numLanes="1"'))
    if reuse_boundary:
        nodes.write_text(nodes.read_text().replace('id="s" x="-100" y="0"', 'id="s" x="-100" y="0" shape="-102,-5 -98,-5 -98,5 -102,5"'))
        edges.write_text(edges.read_text().replace('id="before" from="w" to="s" numLanes="2"', 'id="before" from="w" to="s" numLanes="1"'))
    source = tmp_path / "source.net.xml"
    subprocess.run([shutil.which("netconvert"), "-n", str(nodes), "-e", str(edges), "-o", str(source), "--offset.disable-normalization"], check=True, capture_output=True, timeout=30)
    tree = ET.parse(source)
    tree.getroot().find("location").set("projParameter", "+proj=utm +zone=32 +ellps=GRS80 +units=m +no_defs")
    tree.write(source, encoding="utf-8")
    source_lanes = tree.getroot().find("edge[@id='approach']").findall("lane")
    lane_shapes = {str(index + 1): [tuple(map(float, value.split(","))) for value in lane.get("shape").split()] for index, lane in enumerate(source_lanes)}
    if reuse_boundary:
        for shape in lane_shapes.values():
            shape.insert(0, (-120, shape[0][1]))
    carrier_id = "1" if right_pocket or through_count == 1 else "2"
    pocket_id = str(through_count + 1)
    merge = (-100 if reuse_boundary else -40, lane_shapes[carrier_id][0][1])
    pocket_y = merge[1] + (-3.2 if right_pocket else 3.2)
    lane_shapes[pocket_id] = [merge, (-25, pocket_y), (lane_shapes[carrier_id][-1][0], pocket_y)]
    if full_length_bus:
        lane_shapes[pocket_id] = [(lane_shapes[carrier_id][0][0], pocket_y), (lane_shapes[carrier_id][-1][0], pocket_y)]
    if distant_lane:
        lane_shapes["4"] = [(30, 1.6), (60, 1.6)]
    inverse = Transformer.from_crs("EPSG:25832", "EPSG:4326", always_xy=True)
    root = ET.Element("kml")
    folder = ET.SubElement(root, "Folder")
    ET.SubElement(folder, "name").text = "MAP"
    folders = {}
    for name in ("Base Points", "Lanes", "Crosswalks", "Connections", "Drive lines", "Points", "Merge points"):
        folders[name] = ET.SubElement(folder, "Folder")
        ET.SubElement(folders[name], "name").text = name
    def mark(folder_name, name, coordinates, *, point=False):
        item = ET.SubElement(folders[folder_name], "Placemark")
        ET.SubElement(item, "name").text = name
        if folder_name == "Lanes":
            ET.SubElement(item, "styleUrl").text = "#laneStyleIn"
        geometry = ET.SubElement(item, "Point" if point else "LineString")
        ET.SubElement(geometry, "coordinates").text = " ".join(f"{lon:.12f},{lat:.12f},0" for lon, lat in [inverse.transform(*xy) for xy in coordinates])
    mark("Base Points", "Base Point", [(0, 0)], point=True)
    for lane_id, shape in lane_shapes.items():
        mark("Lanes", f"Lane {lane_id}", shape)
        mark("Points", f"Lane {lane_id} A", [shape[0]], point=True)
        mark("Points", f"Lane {lane_id} B", [shape[-1]], point=True)
    if not full_length_bus:
        for lane_id in (carrier_id, pocket_id):
            mark("Merge points", f"Lane {lane_id} Merge", [merge], point=True)
    kml = tmp_path / "map.kml"
    ET.ElementTree(root).write(kml, encoding="utf-8")
    plan = {"node_id": "7", "inputs": {"map_kml": {"path": str(kml), "sha256": file_sha256(kml)}},
            "lanes": [{"lane_id": lane_id, "lane_type": "vehicle", "direction_role": "ingress", "ingress_approach": "1"} for lane_id in lane_shapes],
            "movements": [{"ingress_lane_id": lane_id, "intersection_part": "0"} for lane_id in lane_shapes]}
    return source, {"7": plan}


@pytest.mark.skipif(not shutil.which("netconvert") or not shutil.which("sumo"), reason="SUMO binaries required")
@pytest.mark.parametrize("full_length_bus", [False, True])
def test_official_bus_lane_permissions_and_real_vehicle_legality(tmp_path, full_length_bus):
    source, plans = _source_and_plan(tmp_path, through_count=1, full_length_bus=full_length_bus)
    plans["7"]["lanes"][1].update({"vehicle_attribute_bits": "10010000", "shared_with_bits": "0000100000", "allowed_vehicle_classes": ["bus"], "revocable": True, "permission_status": "restricted"})
    result = build_hamburg_aerial_approach_candidate(source_net=source, plans=plans, junction_bindings={("7", "0"): "J"}, output_dir=tmp_path / "out")
    assert result["status"] == "pass", result
    event = result["approaches"][0]
    if full_length_bus:
        assert event["mode"] == "official_bounded_restricted_lane"
        assert event["official_merge_xy"] is None
    network = ET.parse(result["candidate_network"]["path"]).getroot()
    bus_lane = network.find("edge[@id='approach']/lane[@index='1']")
    normal_lane = network.find("edge[@id='approach']/lane[@index='0']")
    assert bus_lane.get("allow") == "bus" and bus_lane.get("disallow") is None
    assert normal_lane.get("allow") == "passenger bus"
    assert event["revocable_lane_policy"] == "enabled_for_topology_test;activation_not_replayed"
    first = event["upstream_edge_id"] or "before"
    routes = tmp_path / "legal.xml"
    routes.write_text('<routes><vType id="bus" vClass="bus"/><vehicle id="bus" type="bus" depart="0"><route edges="' + first + ' approach"/><stop lane="approach_1" endPos="' + str(float(bus_lane.get("length")) - 2) + '" duration="1"/></vehicle><vehicle id="car" depart="100"><route edges="' + first + ' approach"/></vehicle></routes>')
    command = [shutil.which("sumo"), "-n", result["candidate_network"]["path"], "-r", str(routes), "--end", "200", "--no-step-log", "--tripinfo-output", str(tmp_path / "trips.xml")]
    run = subprocess.run(command, capture_output=True, text=True, timeout=30)
    assert run.returncode == 0, run.stderr
    assert {row.get("id") for row in ET.parse(tmp_path / "trips.xml").getroot()} == {"bus", "car"}
    routes.write_text(routes.read_text().replace('id="bus" type="bus" depart="0"', 'id="bus" depart="0"'))
    rejected = subprocess.run(command, capture_output=True, text=True, timeout=30)
    assert rejected.returncode != 0, "A passenger vehicle must not use the official bus-only stop lane"


@pytest.mark.skipif(not shutil.which("netconvert"), reason="SUMO binaries required")
@pytest.mark.parametrize("distant_lane", [False, True])
def test_explicit_official_pocket_rebuild_preserves_upstream_and_compiles(tmp_path, distant_lane):
    source, plans = _source_and_plan(tmp_path, distant_lane)
    old_hash = file_sha256(source)
    result = build_hamburg_aerial_approach_candidate(source_net=source, plans=plans, junction_bindings={("7", "0"): "J"}, output_dir=tmp_path / "out")
    assert result["status"] == "pass", result
    assert file_sha256(source) == old_hash
    assert result["gates"]["external_connection_set"] == "pass"
    assert result["gates"]["outside_geometry_and_attributes"] == "pass"
    assert result["gates"]["internal_path_continuity"] == "pass"
    event = result["approaches"][0]
    assert event["through_lane_ids"] == ["1", "2"]
    assert event["pocket_lane_ids"] == ["3"]
    assert event["same_approach_lanes_outside_section"] == (["4"] if distant_lane else [])
    net = ET.parse(result["candidate_network"]["path"]).getroot()
    near = net.find("edge[@id='approach']")
    upstream = net.find(f"edge[@id='{event['upstream_edge_id']}']")
    assert len(near.findall("lane")) == 3 and len(upstream.findall("lane")) == 2
    bridges = [row for row in net.findall("connection") if row.get("from") == event["upstream_edge_id"]]
    assert {(row.get("fromLane"), row.get("toLane")) for row in bridges} == {("0", "0"), ("1", "1")}
    assert all(lane.get("speed") == "11.00" and lane.get("allow") == "passenger bus" for lane in near.findall("lane"))
    if shutil.which("sumo"):
        loaded = subprocess.run([shutil.which("sumo"), "-n", result["candidate_network"]["path"], "--end", "1", "--no-step-log"], capture_output=True, text=True, timeout=30)
        assert loaded.returncode == 0, loaded.stderr


@pytest.mark.skipif(not shutil.which("netconvert"), reason="SUMO binaries required")
def test_official_single_lane_plus_pocket_has_no_extra_upstream_fanout(tmp_path):
    source, plans = _source_and_plan(tmp_path, through_count=1)
    result = build_hamburg_aerial_approach_candidate(source_net=source, plans=plans, junction_bindings={("7", "0"): "J"}, output_dir=tmp_path / "out")
    assert result["status"] == "pass", result
    event = result["approaches"][0]
    assert (event["lane_count_before"], event["lane_count_after"]) == (1, 2)
    assert event["source_lane_to_downstream_lane"] == {"0": 0}
    net = ET.parse(result["candidate_network"]["path"]).getroot()
    assert [(row.get("fromLane"), row.get("toLane")) for row in net.findall("connection") if row.get("from") == event["upstream_edge_id"]] == [("0", "0")]


def test_closer_road_with_wrong_count_is_not_replaced_by_parallel_match():
    root = ET.fromstring('<net><junction id="s" x="-100" y="0" shape="-101,-5 -99,-5 -99,5 -101,5"/><junction id="J" x="0" y="0"/><edge id="near" from="s" to="J"><lane index="0" shape="-100,1.6 0,1.6"/></edge><edge id="far" from="s" to="J"><lane index="0" shape="-100,5 0,5"/><lane index="1" shape="-100,8.2 0,8.2"/></edge></net>')
    members = [
        {"lane_id": "1", "direction": "ingress", "endpoint_a": (-100, 0), "endpoint_b": (0, 0), "shape": [(-100, 0), (0, 0)], "merge_points": []},
        {"lane_id": "2", "direction": "ingress", "endpoint_a": (-100, 3.2), "endpoint_b": (0, 3.2), "shape": [(-100, 3.2), (0, 3.2)], "merge_points": [(-40, 3.2)]},
        {"lane_id": "3", "direction": "ingress", "endpoint_a": (-40, 3.2), "endpoint_b": (0, 6.4), "shape": [(-40, 3.2), (-20, 6.4), (0, 6.4)], "merge_points": [(-40, 3.2)]},
    ]
    proposal, reason = _approach_proposal(root, members, "J", 10, 0.5)
    assert proposal is None
    assert reason == "nearest_source_arm_lane_count_mismatch"


@pytest.mark.skipif(not shutil.which("netconvert"), reason="SUMO binaries required")
@pytest.mark.parametrize("fault", [None, "short_lane", "source_count", "permissions"])
def test_no_addition_requires_matching_full_length_official_lanes(tmp_path, monkeypatch, fault):
    source, plans = _source_and_plan(tmp_path)
    plan = plans["7"]
    plan["lanes"] = [lane for lane in plan["lanes"] if lane["lane_id"] != "3"]
    plan["movements"] = [row for row in plan["movements"] if row["ingress_lane_id"] != "3"]
    kml_file = tmp_path / "map.kml"
    kml = ET.parse(kml_file)
    for folder in kml.getroot().iter("Folder"):
        for item in list(folder.findall("Placemark")):
            if "Merge" in item.findtext("name", ""):
                folder.remove(item)
    kml.write(kml_file, encoding="utf-8")
    plan["inputs"]["map_kml"]["sha256"] = file_sha256(kml_file)
    if fault == "short_lane":
        inverse = Transformer.from_crs("EPSG:25832", "EPSG:4326", always_xy=True)
        shape = kml.find(".//Placemark[name='Lane 2']/LineString/coordinates")
        points = shape.text.split()
        lon, lat = inverse.transform(-40, 1.6)
        points[0] = f"{lon:.12f},{lat:.12f},0"
        shape.text = " ".join(points)
        kml.find(".//Placemark[name='Lane 2 A']/Point/coordinates").text = points[0]
        kml.write(kml_file, encoding="utf-8")
        plan["inputs"]["map_kml"]["sha256"] = file_sha256(kml_file)
    elif fault == "source_count":
        tree = ET.parse(source)
        edge = tree.find("edge[@id='approach']")
        edge.remove(edge.findall("lane")[-1])
        tree.write(source, encoding="utf-8")
    elif fault == "permissions":
        plan["lanes"][0]["permission_status"] = "review_required"
    old_hash = file_sha256(source)
    monkeypatch.setattr(approach_module, "run_command", lambda *args, **kwargs: pytest.fail("No lane addition should compile a network"))
    result = build_hamburg_aerial_approach_candidate(source_net=source, plans=plans, junction_bindings={("7", "0"): "J"}, output_dir=tmp_path / "out")
    assert result["candidate_network"] is None
    assert file_sha256(source) == old_hash
    if fault:
        assert result["status"] == "review_required"
        assert result["reviews"]
        assert result["not_applicable"] == []
    else:
        assert result["status"] == "not_applicable"
        assert result["reviews"] == []
        unchanged = result["not_applicable"][0]
        assert unchanged["reason"] == "no_documented_addition_with_matching_source_lanes"
        assert unchanged["source_edge_id"] == "approach"
        assert unchanged["official_lane_indices"] == {"1": 0, "2": 1}


def test_empty_official_scope_is_not_evidence_that_no_addition_is_needed(tmp_path):
    source = tmp_path / "source.net.xml"
    source.write_text('<net><location projParameter="+proj=utm +zone=32 +ellps=GRS80 +units=m +no_defs"/></net>', encoding="utf-8")
    result = build_hamburg_aerial_approach_candidate(source_net=source, plans={}, junction_bindings={}, output_dir=tmp_path / "out")
    assert result["status"] == "review_required"
    assert result["not_applicable"] == []
    assert result["candidate_network"] is None


@pytest.mark.parametrize("fault", [None, "permissions", "grade", "ambiguous", "missing_control"])
def test_official_merge_selects_its_unique_upstream_road_without_removing_side_links(fault):
    root = ET.fromstring('''<net>
      <junction id="west" x="-100" y="0"/><junction id="cross" x="-20" y="0"/>
      <junction id="J" x="0" y="0"/><junction id="north" x="-20" y="50"/><junction id="south" x="-20" y="-50"/>
      <edge id="main" from="west" to="cross"><lane index="0" shape="-100,0 -20,0" allow="passenger bus"/></edge>
      <edge id="tail" from="cross" to="J"><lane index="0" shape="-20,0 0,0" allow="passenger bus"/></edge>
      <edge id="left" from="north" to="cross"><lane index="0" shape="-20,50 -20,0" allow="bus"/></edge>
      <edge id="right" from="south" to="cross"><lane index="0" shape="-20,-50 -20,0" allow="delivery"/></edge>
      <connection from="main" to="tail" fromLane="0" toLane="0" dir="s"/>
      <connection from="left" to="tail" fromLane="0" toLane="0" dir="r"/>
      <connection from="right" to="tail" fromLane="0" toLane="0" dir="l"/>
    </net>''')
    for node in root.findall("junction"):
        x, y = float(node.get("x")), float(node.get("y"))
        node.set("shape", f"{x-.1},{y-.1} {x+.1},{y-.1} {x+.1},{y+.1} {x-.1},{y+.1}")
    if fault == "permissions":
        root.find("edge[@id='main']/lane").set("allow", "bus")
    elif fault == "grade":
        ET.SubElement(root.find("edge[@id='main']"), "param", key="layer", value="-1")
    elif fault == "ambiguous":
        edge = ET.SubElement(root, "edge", id="parallel", **{"from": "west", "to": "cross"})
        ET.SubElement(edge, "lane", index="0", shape="-100,0.2 -20,0.2", allow="passenger bus")
        ET.SubElement(root, "connection", **{"from": "parallel", "to": "tail", "fromLane": "0", "toLane": "0", "dir": "s"})
    elif fault == "missing_control":
        root.find("connection[@from='left']").set("tl", "unresolved-side-controller")
    unchanged = ET.tostring(root)
    chain, reason = approach_module._source_chain_to_merge(root, root.find("edge[@id='tail']"), (-40, 0), 10)
    if fault:
        assert chain is None
        assert ET.tostring(root) == unchanged
        return
    assert chain is not None, reason
    assert [edge.get("id") for edge in chain] == ["main", "tail"]
    assert ET.tostring(root) == unchanged


@pytest.mark.parametrize("ambiguous_other_road", [False, True])
def test_one_official_approach_keeps_a_separate_parallel_road_out_of_the_pocket(ambiguous_other_road):
    root = ET.fromstring('''<net><junction id="s" x="-100" y="1.6"/><junction id="p" x="-100" y="14.4"/><junction id="J" x="0" y="0"/>
      <edge id="main" from="s" to="J"><lane index="0" shape="-100,0 0,0"/><lane index="1" shape="-100,3.2 0,3.2"/></edge>
      <edge id="parallel" from="p" to="J"><lane index="0" shape="-100,12.8 0,12.8"/><lane index="1" shape="-100,16 0,16"/></edge></net>''')
    for node in root.findall("junction"):
        x, y = float(node.get("x")), float(node.get("y"))
        node.set("shape", f"{x-.1},{y-.1} {x+.1},{y-.1} {x+.1},{y+.1} {x-.1},{y+.1}")
    members = [{"lane_id": str(index), "direction": "ingress", "endpoint_a": (-100, y), "endpoint_b": (0, y),
                "shape": [(-100, y), (0, y)], "merge_points": [(-40, 3.2)] if index == 2 else []}
               for index, y in ((1, 0), (2, 3.2), (4, 12.8), (5, 16))]
    members.append({"lane_id": "3", "direction": "ingress", "endpoint_a": (-40, 3.2), "endpoint_b": (0, 6.4),
                    "shape": [(-40, 3.2), (-20, 6.4), (0, 6.4)], "merge_points": [(-40, 3.2)]})
    if ambiguous_other_road:
        members[2].update(endpoint_a=(-100, 8), endpoint_b=(0, 8), shape=[(-100, 8), (0, 8)])
    unchanged = ET.tostring(root)
    proposal, reason = _approach_proposal(root, members, "J", 10, 0.5)
    if ambiguous_other_road:
        assert proposal is None
        assert ET.tostring(root) == unchanged
        return
    assert proposal is not None, reason
    assert proposal["source_edge_id"] == "main"
    assert proposal["through_lane_ids"] == ["1", "2"]
    assert proposal["pocket_lane_ids"] == ["3"]
    assert proposal["lane_count_after"] == 3
    assert set(proposal["same_approach_lanes_outside_section"]) == {"4", "5"}
    assert ET.tostring(root) == unchanged


@pytest.mark.skipif(not shutil.which("netconvert"), reason="SUMO binaries required")
def test_branch_merge_rebuild_preserves_side_permissions_and_controller_links(tmp_path):
    source, plans = _source_and_plan(tmp_path, through_count=1)
    split = tmp_path / "split.xml"
    split.write_text('<edges><edge id="approach"><split pos="70" id="middle" idBefore="approach" idAfter="tail" type="traffic_light"/></edge></edges>', encoding="utf-8")
    chain = tmp_path / "chain.net.xml"
    subprocess.run(["netconvert", "-s", str(source), "-e", str(split), "-o", str(chain), "--offset.disable-normalization"], check=True, capture_output=True, timeout=30)
    nodes, edges = tmp_path / "side.nodes.xml", tmp_path / "side.edges.xml"
    nodes.write_text('<nodes><node id="north" x="-30" y="70"/><node id="south" x="-30" y="-70"/></nodes>', encoding="utf-8")
    edges.write_text('<edges><edge id="left" from="north" to="middle" allow="bus"/><edge id="right" from="south" to="middle" allow="passenger"/></edges>', encoding="utf-8")
    branched = tmp_path / "branched.net.xml"
    subprocess.run(["netconvert", "-s", str(chain), "-n", str(nodes), "-e", str(edges), "-o", str(branched), "--offset.disable-normalization", "--tls.rebuild", "true"], check=True, capture_output=True, timeout=30)
    before = ET.parse(branched).getroot()
    report = build_hamburg_aerial_approach_candidate(source_net=branched, plans=plans, junction_bindings={("7", "0"): "J"}, output_dir=tmp_path / "out")
    assert report["status"] == "pass", report
    after = ET.parse(report["candidate_network"]["path"]).getroot()
    remap = next(row["source_lane_to_downstream_lane"] for row in report["approaches"] if row["source_edge_id"] == "tail")
    for side in ("left", "right"):
        assert approach_module._preserved_edge(before.find(f"edge[@id='{side}']"), after.find(f"edge[@id='{side}']"))
        old = before.find(f"connection[@from='{side}'][@to='tail']")
        new = after.find(f"connection[@from='{side}'][@to='tail']")
        assert new.get("toLane") == str(remap[old.get("toLane")])
        assert (new.get("tl"), new.get("linkIndex")) == (old.get("tl"), old.get("linkIndex"))
    assert [phase.attrib for phase in before.find("tlLogic[@id='middle']")] == [phase.attrib for phase in after.find("tlLogic[@id='middle']")]


@pytest.mark.skipif(not shutil.which("netconvert"), reason="SUMO binaries required")
@pytest.mark.parametrize("obstruction", [None, "traffic_light", "missing_control", "branch", "permissions", "grade"])
def test_merge_in_a_predecessor_chain_preserves_sections_and_stops_at_protected_changes(tmp_path, obstruction):
    source, plans = _source_and_plan(tmp_path, through_count=1)
    patch = tmp_path / "split.xml"
    patch.write_text('<edges><edge id="approach"><split pos="70" id="middle" idBefore="approach" idAfter="tail" type="' + ("traffic_light" if obstruction in {"traffic_light", "missing_control"} else "priority") + '"/></edge></edges>')
    chain = tmp_path / "chain.net.xml"
    subprocess.run([shutil.which("netconvert"), "-s", str(source), "-e", str(patch), "-o", str(chain), "--offset.disable-normalization"], check=True, capture_output=True, timeout=30)
    changed = ET.parse(chain)
    if obstruction == "branch":
        ET.SubElement(changed.getroot(), "junction", {"id": "side", "x": "-30", "y": "30"})
        edge = ET.SubElement(changed.getroot(), "edge", {"id": "side", "from": "middle", "to": "side"})
        ET.SubElement(edge, "lane", {"index": "0", "shape": "-30,0 -30,30"})
    elif obstruction == "permissions":
        changed.getroot().find("edge[@id='approach']/lane").set("allow", "bus")
    elif obstruction == "grade":
        ET.SubElement(changed.getroot().find("edge[@id='approach']"), "param", {"key": "layer", "value": "-1"})
    elif obstruction == "missing_control":
        connection = changed.getroot().find("connection[@from='approach'][@to='tail']")
        connection.attrib.pop("tl")
        connection.attrib.pop("linkIndex")
    if obstruction in {"branch", "permissions", "grade", "missing_control"}:
        changed.write(chain, encoding="utf-8")
    result = build_hamburg_aerial_approach_candidate(source_net=chain, plans=plans, junction_bindings={("7", "0"): "J"}, output_dir=tmp_path / "out")
    if obstruction and obstruction != "traffic_light":
        assert result["candidate_network"] is None
        assert any(obstruction in row["reason"] for row in result["reviews"])
        return
    assert result["status"] == "pass", result
    assert [row["source_edge_id"] for row in result["approaches"]] == ["approach", "tail"]
    assert result["approaches"][1]["mode"] == "existing_chain_section"
    assert result["approaches"][1]["upstream_edge_id"] is None
    net = ET.parse(result["candidate_network"]["path"]).getroot()
    assert net.find("junction[@id='middle']") is not None
    continuation = {(row.get("fromLane"), row.get("toLane")) for row in net.findall("connection") if row.get("from") == "approach" and row.get("to") == "tail"}
    assert continuation == {("0", "0"), ("1", "1")}
    if obstruction == "traffic_light":
        old = ET.parse(chain).getroot().find("connection[@from='approach'][@to='tail']")
        added = net.find("connection[@from='approach'][@to='tail'][@fromLane='1']")
        assert net.find("junction[@id='middle']").get("type") == "traffic_light"
        assert added.get("tl") == old.get("tl") == "middle"
        assert added.get("linkIndex") == old.get("linkIndex")
        assert result["affected_tls"][0]["tls_id"] == "middle"
        assert result["gates"]["controlled_continuations"] == "pass"


def test_boundary_adjustment_stays_inside_original_owner_polygons():
    root = ET.fromstring('<net><junction id="one" shape="0,0 2,0 2,2 0,2"/><junction id="two" shape="1,0 3,0 3,2 1,2"/><edge id="short" from="one" to="two"><lane index="0" speed="11.00" allow="passenger" shape="1.1,1 1.3,1"/></edge><edge id="far" from="one" to="two"><lane index="0" speed="11.00" shape="-10,1 1.3,1"/></edge></net>')
    scope = _boundary_adjustment_scope(root, {"one"}, set())
    assert set(scope) == {"short"}
    old = root.find("edge[@id='short']")
    new = ET.fromstring(ET.tostring(old))
    new.find("lane").set("shape", "1.8,1 2.5,1")
    assert _preserved_boundary_edge(old, new, scope["short"]["polygons"])
    new.find("lane").set("shape", "1.8,1 3.5,1")
    assert not _preserved_boundary_edge(old, new, scope["short"]["polygons"])
    new.find("lane").set("shape", "1.8,1 2.5,1")
    new.find("lane").set("allow", "bus")
    assert not _preserved_boundary_edge(old, new, scope["short"]["polygons"])


@pytest.mark.skipif(not shutil.which("netconvert"), reason="SUMO binaries required")
def test_conflicting_chain_does_not_apply_its_nonconflicting_prefix(tmp_path, monkeypatch):
    source, plans = _source_and_plan(tmp_path)
    plans["8"] = deepcopy(plans["7"])
    plans["8"]["node_id"] = "8"
    choices = iter([
        ({"source_edge_id": "approach"}, "matched"),
        ({"_chain_sections": [{"source_edge_id": "before"}, {"source_edge_id": "approach"}]}, "matched"),
    ])
    monkeypatch.setattr(approach_module, "_approach_proposal", lambda *args: next(choices))
    result = build_hamburg_aerial_approach_candidate(source_net=source, plans=plans, junction_bindings={("7", "0"): "J", ("8", "0"): "J"}, output_dir=tmp_path / "out")
    assert result["approaches"] == []
    assert result["candidate_network"] is None


@pytest.mark.skipif(not shutil.which("netconvert"), reason="SUMO binaries required")
def test_approach_rebuild_rejects_changed_official_kml_before_output(tmp_path):
    source, plans = _source_and_plan(tmp_path)
    plans["7"]["inputs"]["map_kml"]["sha256"] = "0" * 64
    with pytest.raises(ValueError, match="SHA-256"):
        build_hamburg_aerial_approach_candidate(source_net=source, plans=plans, junction_bindings={("7", "0"): "J"}, output_dir=tmp_path / "out")
    assert not (tmp_path / "out").exists()


@pytest.mark.skipif(not shutil.which("netconvert"), reason="SUMO binaries required")
@pytest.mark.parametrize("right_pocket", [False, True])
def test_reuses_an_existing_junction_containing_the_official_merge(tmp_path, right_pocket):
    source, plans = _source_and_plan(tmp_path, reuse_boundary=True, right_pocket=right_pocket)
    result = build_hamburg_aerial_approach_candidate(source_net=source, plans=plans, junction_bindings={("7", "0"): "J"}, output_dir=tmp_path / "out")
    assert result["status"] == "pass", result
    event = result["approaches"][0]
    assert event["mode"] == "reuse_existing_merge_junction"
    assert event["upstream_edge_id"] is None
    assert event["split_node_id"] == "s"
    assert event["upstream_interval_m"] is None
    net = ET.parse(result["candidate_network"]["path"]).getroot()
    assert {edge.get("id") for edge in net.findall("edge") if not edge.get("function")} == {"before", "approach", "after"}
    assert {(row.get("fromLane"), row.get("toLane")) for row in net.findall("connection") if row.get("from") == "before"} == ({("0", "1"), ("0", "2")} if right_pocket else {("0", "0"), ("0", "1")})
    assert result["gates"]["outside_geometry_and_attributes"] == "pass"


def test_same_approach_parallel_roads_have_separate_official_merge_events():
    # LSA119's two pairs share approach1 but not their physical road or Merge.
    members = [
        {"lane_id": "1", "merge_points": [(100, 200)]},
        {"lane_id": "2", "merge_points": [(100, 200.01)]},
        {"lane_id": "3", "merge_points": [(89.25, 178.83)]},
        {"lane_id": "4", "merge_points": [(89.26, 178.82)]},
    ]
    groups = approach_module._separate_official_merge_events(members)
    assert [{row["lane_id"] for row in group} for group in groups] == [{"1", "2"}, {"3", "4"}]
    # A lane assigned to both Merge events or an unassigned lane is not split
    # by arbitrary proximity. Existing review behavior remains available.
    members[0]["merge_points"].append((89.25, 178.83))
    assert approach_module._separate_official_merge_events(members) == [members]
    members[0]["merge_points"] = []
    assert approach_module._separate_official_merge_events(members) == [members]


def test_merge_owner_is_reached_when_its_short_departure_lane_was_clipped_past_the_next_node():
    # Mirrors LSA119: a valid Merge lies in the preceding junction polygon,
    # while netconvert has clipped the intervening edge to a 0.2m remnant.
    root = ET.fromstring('<net><junction id="s" type="priority" x="-40" y="0" shape="-42,-3 -38,-3 -38,3 -42,3"/><junction id="middle" type="priority" x="-30" y="0" shape="-31,-1 -29,-1 -29,1 -31,1"/><junction id="J" x="0" y="0"/><edge id="remnant" from="s" to="middle"><lane id="remnant_0" index="0" shape="-28,0 -27.8,0"/></edge><edge id="tail" from="middle" to="J"><lane id="tail_0" index="0" shape="-30,0 0,0"/></edge><connection from="remnant" to="tail" fromLane="0" toLane="0"/></net>')
    chain, reason = approach_module._source_chain_to_merge(root, root.find("edge[@id='tail']"), (-40, 0), 10)
    assert chain is not None, reason
    assert [edge.get("id") for edge in chain] == ["remnant", "tail"]


@pytest.mark.skipif(not shutil.which("netconvert"), reason="SUMO binaries required")
def test_public_builder_keeps_an_unmatched_parallel_event_separate(tmp_path):
    source, plans = _source_and_plan(tmp_path, through_count=1)
    plan = plans["7"]
    kml_path = tmp_path / "map.kml"
    tree = ET.parse(kml_path)
    forward = Transformer.from_crs(4326, 25832, always_xy=True)
    inverse = Transformer.from_crs(25832, 4326, always_xy=True)
    for folder in tree.getroot().iter("Folder"):
        for item in list(folder.findall("Placemark")):
            name = item.findtext("name", "")
            if not name.startswith(("Lane 1", "Lane 2")):
                continue
            copied = deepcopy(item)
            copied.find("name").text = name.replace("Lane 1", "Lane 3").replace("Lane 2", "Lane 4")
            coords = copied.find(".//coordinates")
            shifted = []
            for point in coords.text.split():
                x, y = forward.transform(*map(float, point.split(",")[:2]))
                lon, lat = inverse.transform(x, y + 1000)
                shifted.append(f"{lon:.12f},{lat:.12f},0")
            coords.text = " ".join(shifted)
            folder.append(copied)
    tree.write(kml_path, encoding="utf-8")
    plan["inputs"]["map_kml"]["sha256"] = file_sha256(kml_path)
    for lane_id in ("3", "4"):
        plan["lanes"].append({"lane_id": lane_id, "lane_type": "vehicle", "direction_role": "ingress", "ingress_approach": "1"})
        plan["movements"].append({"ingress_lane_id": lane_id, "intersection_part": "0"})
    report = build_hamburg_aerial_approach_candidate(source_net=source, plans=plans, junction_bindings={("7", "0"): "J"}, output_dir=tmp_path / "out")
    assert report["status"] == "pass", report
    assert len(report["approaches"]) == 1
    built = report["approaches"][0]
    assert built["lane_event_id"] == "merge-1-2"
    assert built["split_node_id"] == "torii-merge-7-0-1-merge-1-2"
    assert built["same_approach_lanes_outside_event"] == ["3", "4"]
    assert any(row.get("lane_event_id") == "merge-3-4" and row["reason"] == "source_edge_not_uniquely_bound" for row in report["reviews"])


def test_merge_over_an_already_clipped_source_junction_does_not_create_a_single_point_prefix():
    root = ET.fromstring('<net><junction id="s" x="-100" y="0" type="priority" shape="-101,-1 -20,-1 -20,1 -101,1"/><junction id="J" x="0" y="0"/><edge id="approach" from="s" to="J" shape="-100,0 0,0"><lane id="approach_0" index="0" shape="-20,0 0,0"/></edge></net>')
    members = [
        {"lane_id": "1", "direction": "ingress", "endpoint_a": (-100, 6), "endpoint_b": (0, 6), "shape": [(-100, 6), (0, 6)], "merge_points": [(-40, 6)]},
        {"lane_id": "2", "direction": "ingress", "endpoint_a": (-40, 6), "endpoint_b": (0, 9.2), "shape": [(-40, 6), (-30, 9.2), (0, 9.2)], "merge_points": [(-40, 6)]},
    ]
    proposal, reason = _approach_proposal(root, members, "J", 10, .5)
    assert proposal is not None, reason
    assert proposal["mode"] == "reuse_existing_merge_junction"
    assert proposal["merge_attachment_basis"] == "source_junction_covers_longitudinal_transition"
    assert proposal["official_merge_inside_source_junction"] is False
    assert proposal["official_merge_projection_error_m"] == pytest.approx(6)
    # Without polygon ownership of the projected transition, fail explicitly.
    root.find("junction[@id='s']").set("shape", "-101,-1 -99,-1 -99,1 -101,1")
    rejected, reason = _approach_proposal(root, members, "J", 10, .5)
    assert rejected is None
    assert reason == "source_split_has_no_nonzero_visible_prefix"


@pytest.mark.skipif(not shutil.which("netconvert"), reason="SUMO binaries required")
def test_existing_terminal_tls_program_and_link_indices_are_explicitly_retained(tmp_path):
    source, plans = _source_and_plan(tmp_path, right_pocket=True)
    nodes = tmp_path / "tls.xml"
    nodes.write_text('<nodes><node id="J" type="traffic_light"/></nodes>', encoding="utf-8")
    signal_source = tmp_path / "signal-source.net.xml"
    subprocess.run(["netconvert", "-s", str(source), "-n", str(nodes), "-o", str(signal_source), "--offset.disable-normalization"], check=True, capture_output=True, timeout=30)
    report = build_hamburg_aerial_approach_candidate(source_net=signal_source, plans=plans, junction_bindings={("7", "0"): "J"}, output_dir=tmp_path / "out")
    assert "traffic_lights" in report["artifacts"]
    assert report["gates"]["controlled_continuations"] == "pass"
    written = ET.parse(report["artifacts"]["traffic_lights"]["path"]).getroot()
    assert written.find("tlLogic[@id='J']") is not None
    old = ET.parse(signal_source).getroot()
    rebuilt = ET.parse(report["candidate_network"]["path"]).getroot()
    remap = report["approaches"][0]["source_lane_to_downstream_lane"]
    actual = {(c.get("fromLane"), c.get("toLane")): c.get("linkIndex") for c in rebuilt.findall("connection") if c.get("from") == "approach" and c.get("tl") == "J"}
    assert actual == {(str(remap[c.get("fromLane")]), c.get("toLane")): c.get("linkIndex") for c in old.findall("connection") if c.get("from") == "approach" and c.get("tl") == "J"}
