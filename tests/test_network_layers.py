import json
import os
from pathlib import Path
import subprocess
import xml.etree.ElementTree as ET

import pytest

from astra_sumo.core import network_layers
from astra_sumo.core.candidate_contracts import file_sha256
from astra_sumo.core.network_layers import (
    derive_network_layer,
    master_network_layer,
    measure_mode_components,
    measure_tls_cycles,
    select_layer_edges,
)
from astra_sumo.core.osm_network import build_osm_network


FIXTURE = Path(__file__).parent / "fixtures/osm_multimodal_layers.osm.xml"
FOOTWAY_AND_CYCLEWAYS = {"200", "201", "-201", "202", "-202"}


def _binaries() -> tuple[str, str]:
    sumo = pytest.importorskip("sumo")
    directory = Path(sumo.__file__).parent / "bin"
    suffix = ".exe" if os.name == "nt" else ""
    netconvert, simulator = directory / f"netconvert{suffix}", directory / f"sumo{suffix}"
    if not netconvert.is_file() or not simulator.is_file():
        pytest.skip("The SUMO package binaries are required.")
    return str(netconvert), str(simulator)


@pytest.fixture(scope="module")
def master(tmp_path_factory) -> Path:
    netconvert, _ = _binaries()
    build = build_osm_network(bbox="10.9985,47.9990,11.0025,48.0020", output_dir=tmp_path_factory.mktemp("master"),
        source_osm_path=FIXTURE, allowed_highways={"secondary", "tertiary", "residential", "footway", "cycleway"},
        clip_source_ways_to_bbox=False, netconvert_binary=netconvert, netconvert_profile="multimodal",
        multimodal_modes=["bicycle", "pedestrian"])
    assert build["status"] == "pass", build
    return Path(build["net_file"])


def _ordinary_edges(root: ET.Element) -> dict[str, ET.Element]:
    return {edge.get("id"): edge for edge in root.findall("edge") if not edge.get("function")}


def _connections(root: ET.Element, edges) -> dict[tuple, tuple]:
    return {(row.get("from"), row.get("fromLane"), row.get("to"), row.get("toLane")): (row.get("tl"), row.get("linkIndex"))
            for row in root.findall("connection") if row.get("from") in edges and row.get("to") in edges}


def test_vehicle_layer_removes_only_edges_and_keeps_master_identity(master, tmp_path):
    netconvert, simulator = _binaries()
    master_hash = file_sha256(master)
    selection = select_layer_edges(master, "vehicle")
    assert set(selection["removed_edge_ids"]) == FOOTWAY_AND_CYCLEWAYS
    assert selection["removed_by_permission_signature"] == {"bicycle": 4, "pedestrian": 1}

    report = derive_network_layer(master, tmp_path / "vehicle", "vehicle", netconvert_binary=netconvert,
                                  sumo_binary=simulator)

    assert report["status"] == "measured", report.get("identity")
    assert report["master_unchanged"] and file_sha256(master) == master_hash == report["derived_from_sha256"]
    assert all(report["identity"]["checks"].values())
    assert report["sumo_load"]["status"] == "pass"
    assert (report["edge_count"], report["removed_edge_count"]) == (10, 5)
    assert file_sha256(Path(report["net_file"])) == report["sha256"]
    assert json.loads(Path(report["manifest_file"]).read_text(encoding="utf-8"))["status"] == "measured"
    # The crossing over the removed cycleway and the links into it go with it; the controller stays.
    assert ["crossing", "1", "-202 202"] in report["identity"]["removed_controlled_links"]
    assert report["identity"]["removed_tls_ids"] == []

    before, after = ET.parse(master).getroot(), ET.parse(report["net_file"]).getroot()
    old_edges, new_edges = _ordinary_edges(before), _ordinary_edges(after)
    assert set(new_edges) == set(old_edges) - FOOTWAY_AND_CYCLEWAYS
    assert {row.get("id") for row in after.findall("junction")} - {row.get("id") for row in before.findall("junction")} <= {
        row.get("id") for row in after.findall("junction") if row.get("type") == "internal"}
    for edge_id, edge in new_edges.items():
        assert [(lane.get("id"), lane.get("allow"), lane.get("disallow")) for lane in edge.findall("lane")] == [
            (lane.get("id"), lane.get("allow"), lane.get("disallow")) for lane in old_edges[edge_id].findall("lane")]
    assert after.find("location").get("netOffset") == before.find("location").get("netOffset")
    old_connections = _connections(before, new_edges)
    assert _connections(after, new_edges) == old_connections
    assert any(tl for tl, _ in old_connections.values())
    programs = {(row.get("id"), row.get("type"), row.get("offset"),
                 sum(float(phase.get("duration")) for phase in row.findall("phase"))) for row in before.findall("tlLogic")}
    assert {(row.get("id"), row.get("type"), row.get("offset"),
             sum(float(phase.get("duration")) for phase in row.findall("phase"))) for row in after.findall("tlLogic")} <= programs

    # netconvert recomputes geometry and right of way around the removed edges; the report counts what changed.
    def points(shape):
        return [tuple(map(float, point.split(","))) for point in shape.split()]

    def moved(old, new):
        old_points, new_points = points(old.get("shape")), points(new.get("shape"))
        return (len(old_points) != len(new_points) or abs(float(old.get("length")) - float(new.get("length"))) > 0.01
                or any(abs(a - b) > 0.01 for p, q in zip(old_points, new_points) for a, b in zip(p, q)))

    old_lanes = {lane.get("id"): lane for edge in old_edges.values() for lane in edge.findall("lane")}
    changed_lanes = {lane.get("id") for edge in new_edges.values() for lane in edge.findall("lane")
                     if moved(old_lanes[lane.get("id")], lane)}
    geometry = report["identity"]["geometry"]
    assert changed_lanes and geometry["changed_lane_count"] == len(changed_lanes)
    assert {row["lane"] for row in geometry["changed_lanes"]} <= changed_lanes
    assert geometry["max_lane_endpoint_shift_m"] > 1.0
    assert all(row["at_junction_that_lost_an_edge"] for row in geometry["changed_lanes"])

    def logic(root):
        return {key: (row.get("dir"), row.get("state")) for row in root.findall("connection")
                if (key := (row.get("from"), row.get("fromLane"), row.get("to"), row.get("toLane"))) in old_connections}

    old_logic, new_logic = logic(before), logic(after)
    right_of_way = report["identity"]["right_of_way"]
    assert right_of_way["changed_connection_count"] == sum(old_logic[key] != value for key, value in new_logic.items())
    assert sum(right_of_way["connection_dir_changes"].values()) == sum(
        old_logic[key][0] != value[0] for key, value in new_logic.items())


_CROSSING_NODES = """<nodes>
  <node id="C" x="0" y="0" type="traffic_light"/>
  <node id="N" x="0" y="100"/><node id="E" x="100" y="0"/><node id="S" x="0" y="-100"/><node id="W" x="-100" y="0"/>
  <node id="WC" x="-100" y="-4"/><node id="F1" x="-100" y="10"/><node id="F2" x="-100" y="-25"/>
</nodes>"""
_CROSSING_EDGES = """<edges>
  <edge id="rn_in" from="N" to="C" numLanes="2"><lane index="0" allow="pedestrian"/><lane index="1" allow="passenger"/></edge>
  <edge id="rn_out" from="C" to="N" numLanes="2"><lane index="0" allow="pedestrian"/><lane index="1" allow="passenger"/></edge>
  <edge id="re_in" from="E" to="C" numLanes="2"><lane index="0" allow="pedestrian"/><lane index="1" allow="passenger"/></edge>
  <edge id="re_out" from="C" to="E" numLanes="2"><lane index="0" allow="pedestrian"/><lane index="1" allow="passenger"/></edge>
  <edge id="rs_in" from="S" to="C" numLanes="2"><lane index="0" allow="pedestrian"/><lane index="1" allow="passenger"/></edge>
  <edge id="rs_out" from="C" to="S" numLanes="2"><lane index="0" allow="pedestrian"/><lane index="1" allow="passenger"/></edge>
  <edge id="rw_in" from="W" to="C" numLanes="1" allow="passenger"/>
  <edge id="rw_out" from="C" to="W" numLanes="1" allow="passenger"/>
  <edge id="cw_in" from="WC" to="C" numLanes="1" allow="bicycle"/>
  <edge id="cw_out" from="C" to="WC" numLanes="1" allow="bicycle"/>
  <edge id="fw1" from="F1" to="C" numLanes="1" allow="pedestrian"/>
  <edge id="fw2" from="F2" to="C" numLanes="1" allow="pedestrian"/>
</edges>"""


def test_crossing_that_keeps_a_road_edge_is_the_same_signal_link_in_a_master_of_any_precision(tmp_path):
    # A signalised crossing spans a road and a cycle track beside it, as at a contraflow cycle track. The vehicle
    # layer removes the cycle track; netconvert keeps the crossing over the road with its id and link index.
    netconvert, simulator = _binaries()
    (tmp_path / "n.nod.xml").write_text(_CROSSING_NODES, encoding="utf-8")
    (tmp_path / "n.edg.xml").write_text(_CROSSING_EDGES, encoding="utf-8")
    master = tmp_path / "master.net.xml"
    # netconvert's default precision of 2, unlike the layer's 6.
    subprocess.run([netconvert, "--node-files", "n.nod.xml", "--edge-files", "n.edg.xml", "--no-turnarounds",
                    "--crossings.guess", "--walkingareas", "--output-file", str(master)],
                   cwd=tmp_path, check=True, capture_output=True)
    crossings = {edge.get("id"): edge.get("crossingEdges") for edge in ET.parse(master).getroot().findall("edge")
                 if edge.get("function") == "crossing"}
    assert sorted(crossings.values()).count("cw_out rw_in cw_in") == 1

    report = derive_network_layer(master, tmp_path / "vehicle", "vehicle", netconvert_binary=netconvert,
                                  sumo_binary=simulator)

    identity = report["identity"]
    assert report["status"] == "measured", identity
    assert all(identity["checks"].values()) and identity["changed_signal_timelines"] == []
    assert identity["reduced_crossings"] == [
        {"junction": "C", "master_crossing_edges": "cw_in cw_out rw_in", "layer_crossing_edges": "rw_in"}]
    assert "review_reason" not in identity
    layer_crossings = {edge.get("id"): edge.get("crossingEdges") for edge in ET.parse(report["net_file"]).getroot().findall(
        "edge") if edge.get("function") == "crossing"}
    reduced = next(crossing for crossing, edges in crossings.items() if edges == "cw_out rw_in cw_in")
    assert layer_crossings[reduced] == "rw_in"
    assert ET.parse(master).getroot().find("location").get("netOffset") != ET.parse(report["net_file"]).getroot().find(
        "location").get("netOffset")


def test_layer_without_removed_edges_is_no_effect_and_output_directories_are_new(master, tmp_path):
    netconvert, simulator = _binaries()
    vehicle = derive_network_layer(master, tmp_path / "vehicle", "vehicle", netconvert_binary=netconvert,
                                   sumo_binary=simulator)
    again = derive_network_layer(vehicle["net_file"], tmp_path / "again", "vehicle", netconvert_binary=netconvert)
    assert (again["status"], again["reason"], again["removed_edge_count"]) == ("no_effect", "layer_keeps_every_master_edge", 0)
    assert again["net_file"] == str(Path(vehicle["net_file"]).resolve()) and again["native_conversion_count"] == 0
    assert derive_network_layer(master, tmp_path / "all", "multimodal")["status"] == "no_effect"
    with pytest.raises(ValueError, match="new output directory"):
        derive_network_layer(master, tmp_path / "vehicle", "vehicle", netconvert_binary=netconvert)
    with pytest.raises(ValueError, match="network layer"):
        derive_network_layer(master, tmp_path / "unknown", "bicycle")
    described = master_network_layer(master, "multimodal")
    assert (described["status"], described["reason"], described["sha256"]) == ("no_effect", "layer_is_master", file_sha256(master))


def test_layer_whose_signal_timing_changed_is_blocked(master, tmp_path, monkeypatch):
    netconvert, simulator = _binaries()
    native = network_layers.keep_selected_edges_natively

    def retimed(source, output, keep_file, candidate, **kwargs):
        result = native(source, output, keep_file, candidate, **kwargs)
        text = candidate.read_text(encoding="utf-8")
        candidate.write_text(text.replace('<phase duration="15"', '<phase duration="25"', 1), encoding="utf-8")
        return result

    monkeypatch.setattr(network_layers, "keep_selected_edges_natively", retimed)
    report = derive_network_layer(master, tmp_path / "vehicle", "vehicle", netconvert_binary=netconvert,
                                  sumo_binary=simulator)
    assert (report["status"], report["reason"]) == ("blocked", "layer_identity_check_failed")
    assert {"controlled_link_timelines_equal", "controller_cycle_offset_type_equal"} <= set(
        report["identity"]["failed_checks"])
    assert report["master_unchanged"]


def test_replanned_crossing_phases_of_a_controller_that_lost_a_crossing_need_review(master, tmp_path, monkeypatch):
    netconvert, simulator = _binaries()
    native = network_layers.keep_selected_edges_natively

    def replanned(source, output, keep_file, candidate, **kwargs):
        # Stand-in for netconvert re-planning the pedestrian phases of a controller whose crossing count changed.
        result = native(source, output, keep_file, candidate, **kwargs)
        tree = ET.parse(candidate)
        root = tree.getroot()
        crossings = {edge.get("id") for edge in root.findall("edge") if edge.get("function") == "crossing"}
        index = next(int(row.get("linkIndex")) for row in root.findall("connection")
                     if row.get("to") in crossings and row.get("tl"))
        phase = root.find("tlLogic/phase")
        state = phase.get("state")
        phase.set("state", state[:index] + ("r" if state[index] in "Gg" else "G") + state[index + 1:])
        tree.write(candidate, encoding="utf-8", xml_declaration=True)
        return result

    monkeypatch.setattr(network_layers, "keep_selected_edges_natively", replanned)
    report = derive_network_layer(master, tmp_path / "vehicle", "vehicle", netconvert_binary=netconvert,
                                  sumo_binary=simulator)

    identity = report["identity"]
    assert (report["status"], report["reason"]) == ("measured", "crossing_signal_timelines_replanned")
    assert (identity["status"], identity["review_reason"]) == ("measured", "crossing_signal_timelines_replanned")
    assert all(identity["checks"].values()) and identity["changed_signal_timelines"] == []
    assert identity["replanned_crossing_link_count"] >= 1
    assert identity["replanned_crossing_controllers"] == [ET.parse(master).getroot().find("tlLogic").get("id")]


def _inventory(phases, crossing_links, edges=()):
    return {"edges": {edge: {"lanes": [], "permissions": [], "orig_ids": []} for edge in edges},
            "junctions": {"J"}, "location": {}, "connections": {},
            "programs": {("t", "0"): {"type": "static", "offset": "0", "phases": phases}},
            "controlled_links": {("crossing", "J", edge): (("t", index),) for edge, index in crossing_links.items()}}


def test_a_crossing_that_lost_some_crossed_edges_is_compared_as_the_same_link():
    master = _inventory([(10.0, "Gr"), (10.0, "rG")], {"a b": "0", "c": "1"}, edges="abc")

    reduced = network_layers._layer_identity(
        master, _inventory([(10.0, "Gr"), (10.0, "rG")], {"b": "0", "c": "1"}, edges="bc"), set("bc"))
    assert (reduced["status"], reduced["changed_signal_timelines"]) == ("measured", [])
    assert reduced["reduced_crossings"] == [{"junction": "J", "master_crossing_edges": "a b", "layer_crossing_edges": "b"}]
    assert reduced["removed_controlled_link_count"] == 0 and "review_reason" not in reduced

    # Same crossing count, so netconvert keeps the program: a changed timeline on it is not explained.
    retimed = network_layers._layer_identity(
        master, _inventory([(10.0, "rr"), (10.0, "GG")], {"b": "0", "c": "1"}, edges="bc"), set("bc"))
    assert (retimed["status"], retimed["failed_checks"]) == ("blocked", ["controlled_link_timelines_equal"])

    # A crossing that keeps none of its edges is removed, and the controller's crossing phases are re-planned.
    replanned = network_layers._layer_identity(
        master, _inventory([(10.0, "r"), (10.0, "G")], {"a b": "0"}, edges="ab"), set("ab"))
    assert (replanned["status"], replanned["review_reason"]) == ("measured", "crossing_signal_timelines_replanned")
    assert replanned["removed_controlled_links"] == [["crossing", "J", "c"]]


def test_location_identity_compares_the_offset_as_numbers_and_the_projection_as_text():
    frame = {"netOffset": "100.00,-5.50", "projParameter": "+proj=utm +zone=33 +ellps=WGS84 +datum=WGS84 +units=m +no_defs"}
    assert network_layers._same_frame(frame, {**frame, "netOffset": "100.000000,-5.500000"})
    assert not network_layers._same_frame(frame, {**frame, "netOffset": "100.010000,-5.500000"})
    assert not network_layers._same_frame(frame, {**frame, "projParameter": "!"})
    assert network_layers._same_frame({}, {})


def test_crossing_timeline_change_needs_review_only_where_the_controller_lost_a_crossing():
    master = _inventory([(10.0, "Gr"), (10.0, "rG")], {"a": "0", "b": "1"})

    unchanged = network_layers._layer_identity(master, _inventory([(10.0, "G"), (10.0, "r")], {"a": "0"}), set())
    assert unchanged["status"] == "measured" and "review_reason" not in unchanged

    replanned = network_layers._layer_identity(master, _inventory([(10.0, "r"), (10.0, "r")], {"a": "0"}), set())
    assert (replanned["status"], replanned["review_reason"]) == ("measured", "crossing_signal_timelines_replanned")
    assert (replanned["replanned_crossing_link_count"], replanned["replanned_crossing_controllers"]) == (1, ["t"])

    # With every crossing kept, nothing explains the change, so the layer stays blocked.
    kept = network_layers._layer_identity(master, _inventory([(10.0, "GG"), (10.0, "rG")], {"a": "0", "b": "1"}), set())
    assert (kept["status"], kept["failed_checks"]) == ("blocked", ["controlled_link_timelines_equal"])
    assert [row["reason"] for row in kept["changed_signal_timelines"]] == ["signal_timeline_changed"]
    assert "review_reason" not in kept


def test_mode_components_are_measured_without_pruning(master, tmp_path):
    measured = measure_mode_components(master, ["bicycle", "pedestrian"])
    assert measured["gate"] == "pass" and measured["network_has_walkingareas"]
    assert {mode: row["largest_component_share"] for mode, row in measured["modes"].items()} == {
        "bicycle": 1.0, "pedestrian": 1.0}

    split = tmp_path / "split.net.xml"
    split.write_text(
        '<net version="1.20">'
        '<edge id="a" from="1" to="2"><lane id="a_0" allow="bicycle" length="10"/></edge>'
        '<edge id="b" from="2" to="3"><lane id="b_0" allow="bicycle" length="10"/></edge>'
        '<edge id="c" from="7" to="8"><lane id="c_0" allow="bicycle pedestrian" length="10"/></edge>'
        '<edge id="d" from="8" to="9"><lane id="d_0" allow="pedestrian" length="10"/></edge>'
        '<connection from="a" to="b" fromLane="0" toLane="0"/>'
        '</net>', encoding="utf-8")
    before = split.read_bytes()
    report = measure_mode_components(split, ["bicycle", "pedestrian"])
    bicycle, pedestrian = report["modes"]["bicycle"], report["modes"]["pedestrian"]
    assert (bicycle["edge_count"], bicycle["component_count"], bicycle["largest_component_share"]) == (3, 2, 0.666667)
    assert (bicycle["gate"], bicycle["reason"]) == ("review_required", "largest_component_share_below_threshold")
    # Without walking areas, pedestrians change edges at a shared junction.
    assert (pedestrian["component_count"], pedestrian["gate"]) == (1, "pass")
    assert (report["gate"], report["review_modes"]) == ("review_required", ["bicycle"])
    assert split.read_bytes() == before


def test_plain_cycleway_tags_without_a_bike_lane_are_counted(tmp_path):
    netconvert, _ = _binaries()
    # Way 102 maps its track with the plain key; way 100 keeps the side key the multimodal profile reads.
    osm = tmp_path / "plain.osm.xml"
    osm.write_text(FIXTURE.read_text(encoding="utf-8").replace(
        '<tag k="highway" v="tertiary"/>', '<tag k="highway" v="tertiary"/>\n    <tag k="cycleway" v="track"/>'),
        encoding="utf-8")
    build = build_osm_network(bbox="10.9985,47.9990,11.0025,48.0020", output_dir=tmp_path / "master", source_osm_path=osm,
        allowed_highways={"secondary", "tertiary", "residential", "footway", "cycleway"}, clip_source_ways_to_bbox=False,
        netconvert_binary=netconvert, netconvert_profile="multimodal", multimodal_modes=["bicycle", "pedestrian"])
    before = (osm.read_bytes(), Path(build["net_file"]).read_bytes())

    report = network_layers.measure_plain_cycleway_tags(osm, build["net_file"])

    assert (report["plain_cycleway_way_count"], report["without_bicycle_lane_way_ids"]) == (1, ["102"])
    assert (report["without_bicycle_lane_by_value"], report["reason"]) == (
        {"track": 1}, "plain_cycleway_tag_without_bicycle_lane")
    assert (osm.read_bytes(), Path(build["net_file"]).read_bytes()) == before


def test_routes_through_controllers_names_the_signals_each_route_passes(tmp_path):
    net, routes = tmp_path / "n.net.xml", tmp_path / "r.rou.xml"
    net.write_text('<net version="1.20"><connection from="a" to="b" fromLane="0" toLane="0" tl="long" linkIndex="0"/>'
                   '<connection from="b" to="c" fromLane="0" toLane="0" tl="short" linkIndex="0"/></net>', encoding="utf-8")
    routes.write_text('<routes><route id="shared" edges="a b c"/><vehicle id="0" route="shared"/>'
                      '<vehicle id="1"><route edges="b c"/></vehicle><vehicle id="2"><route edges="a b"/></vehicle>'
                      '</routes>', encoding="utf-8")
    report = network_layers.routes_through_controllers(net, routes, ["0", "1"], ["long"])
    assert (report["vehicle_count"], report["routed_vehicle_count"], report["vehicles_through_controllers_count"]) == (2, 2, 1)
    assert (report["controllers"], report["vehicles"]) == ({"long": 1}, [{"vehicle": "0", "controllers": ["long"]}])


def test_tls_cycles_list_programs_above_the_threshold(master, tmp_path):
    assert measure_tls_cycles(master)["long_cycle_controllers"] == []
    net = tmp_path / "cycles.net.xml"
    net.write_text('<net version="1.20">'
                   '<tlLogic id="short" type="static" programID="0" offset="0">'
                   '<phase duration="40" state="G"/><phase duration="50" state="r"/></tlLogic>'
                   '<tlLogic id="long" type="static" programID="0" offset="0">'
                   '<phase duration="100" state="G"/><phase duration="30" state="r"/></tlLogic>'
                   '</net>', encoding="utf-8")
    report = measure_tls_cycles(net)
    assert (report["controller_count"], report["max_cycle_s"]) == (2, 130.0)
    assert [row["tl_id"] for row in report["long_cycle_controllers"]] == ["long"]


def _controller_nodes(net: Path) -> dict[str, set[str]]:
    root = ET.parse(net).getroot()
    to_node = {edge.get("id"): edge.get("to") for edge in root.findall("edge") if not edge.get("function")}
    nodes: dict[str, set[str]] = {logic.get("id"): set() for logic in root.findall("tlLogic")}
    for row in root.findall("connection"):
        if row.get("tl") and row.get("from") in to_node:
            nodes[row.get("tl")].add(to_node[row.get("from")])
    return nodes


def test_multimodal_master_keeps_the_signal_groups_of_the_vehicle_roads(tmp_path):
    netconvert, _ = _binaries()
    osm = Path(__file__).parent / "fixtures/osm_multimodal_signal_groups.osm.xml"
    common = {"bbox": "10.9975,47.9990,11.0030,48.0010", "source_osm_path": osm, "clip_source_ways_to_bbox": False,
              "netconvert_binary": netconvert}
    vehicle = build_osm_network(output_dir=tmp_path / "vehicle", allowed_highways={"secondary", "residential"}, **common)
    master = build_osm_network(output_dir=tmp_path / "master", allowed_highways={"secondary", "residential", "cycleway"},
                               netconvert_profile="multimodal", multimodal_modes=["bicycle"], **common)
    # netconvert's own join treats the crossing signals 4 and 5 as an intersection once cycleways touch them.
    subprocess.run([netconvert, "--osm-files", str(osm), "--proj.utm", "--osm.bike-access", "--tls.join",
                    "--tls.join-dist", "35", "--output-file", str(tmp_path / "joined.net.xml")],
                   check=True, capture_output=True)

    assert (vehicle["status"], master["status"]) == ("pass", "pass")
    assert _controller_nodes(tmp_path / "joined.net.xml")["joinedS_4_5"] == {"4", "5"}
    expected = {"4": {"4"}, "5": {"5"}, "joinedS_2_3": {"2", "3"}}
    assert _controller_nodes(Path(vehicle["net_file"])) == _controller_nodes(Path(master["net_file"])) == expected
    grouping = master["netconvert_signal_grouping"]
    assert (grouping["status"], grouping["joined_controller_count"], grouping["patched_node_count"]) == ("measured", 1, 2)
    assert grouping["skipped_controllers"] == []
