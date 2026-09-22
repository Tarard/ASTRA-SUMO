import shutil
import subprocess
import xml.etree.ElementTree as ET

import pytest

from astra_sumo.core.candidate_contracts import file_sha256
from astra_sumo.core.junction_boundary_rebuild import (
    _boundary_geometry,
    _source_polygon_preserves_cuts,
    collect_join_boundary_paths,
    restore_joined_boundary_connections,
)
from astra_sumo.core.source_movement_support import _index


@pytest.mark.skipif(not shutil.which("netconvert") or not shutil.which("sumo"), reason="SUMO binaries required")
@pytest.mark.parametrize("lane_change_allowed", [True, False])
def test_explicit_interior_lane_change_policy_requires_source_permission(tmp_path, lane_change_allowed):
    nodes, edges, connections = [tmp_path / name for name in ("nodes.xml", "edges.xml", "connections.xml")]
    nodes.write_text('<nodes><node id="w" x="-100" y="0"/><node id="a" x="0" y="0"/><node id="b" x="20" y="0"/><node id="e" x="120" y="0"/><node id="n" x="20" y="100"/></nodes>', encoding="utf-8")
    restriction = '' if lane_change_allowed else '<lane index="1" changeRight="bus"/>'
    edges.write_text('<edges><edge id="in" from="w" to="a" allow="passenger"/><edge id="middle" from="a" to="b" allow="passenger" numLanes="2">' + restriction + '</edge><edge id="out" from="b" to="e" allow="passenger"/><edge id="side" from="b" to="n" allow="passenger"/></edges>', encoding="utf-8")
    connections.write_text('<connections><connection from="in" to="middle" fromLane="0" toLane="1"/><connection from="middle" to="out" fromLane="0" toLane="0"/><connection from="middle" to="side" fromLane="1" toLane="0"/></connections>', encoding="utf-8")
    source, joined = tmp_path / "source.net.xml", tmp_path / "joined.net.xml"
    subprocess.run(["netconvert", "-n", str(nodes), "-e", str(edges), "-x", str(connections), "-o", str(source)], check=True, capture_output=True, timeout=30)
    nodes.write_text('<nodes><join id="joined" nodes="a b" reset="false" type="priority"/></nodes>', encoding="utf-8")
    connections.write_text('<connections><connection from="in" to="out" fromLane="0" toLane="0"/><connection from="in" to="side" fromLane="0" toLane="0"/></connections>', encoding="utf-8")
    subprocess.run(["netconvert", "-s", str(source), "-n", str(nodes), "-x", str(connections), "--offset.disable-normalization", "true", "-o", str(joined)], check=True, capture_output=True, timeout=30)
    args = dict(source_net=source, joined_net=joined, groups={"joined": ["a", "b"]},
                expected_source_sha256=file_sha256(source), expected_joined_sha256=file_sha256(joined))
    strict = restore_joined_boundary_connections(**args, output_dir=tmp_path / "strict")
    assert strict["status"] == "blocked"
    result = restore_joined_boundary_connections(**args, output_dir=tmp_path / "permitted",
                                                interior_lane_change_policy="permitted_interior")
    assert result["status"] == ("pass" if lane_change_allowed else "blocked")
    additions = [row for row in result["plan"]["movements"] if row.get("permitted_interior_lane_change_paths")]
    assert len(additions) == int(lane_change_allowed)
    if additions:
        assert additions[0]["permitted_interior_lane_change_paths"][0]["lane_changes"][0]["edge_id"] == "middle"


def _source():
    root = ET.Element("net")
    for node in ("w", "a", "b", "e", "outside"):
        ET.SubElement(root, "junction", id=node, type="priority")
    for edge_id, a, b, allow in (
        ("in", "w", "a", "passenger bus"),
        ("middle", "a", "b", "bus"),
        ("out", "b", "e", "passenger bus"),
        ("detour", "a", "outside", "passenger"),
        ("return", "outside", "b", "passenger"),
    ):
        edge = ET.SubElement(root, "edge", id=edge_id, **{"from": a, "to": b})
        ET.SubElement(edge, "lane", id=f"{edge_id}_0", index="0", allow=allow)
    for a, b in (("in", "middle"), ("middle", "out"), ("in", "detour"), ("detour", "return"), ("return", "out")):
        ET.SubElement(root, "connection", **{"from": a, "to": b, "fromLane": "0", "toLane": "0"})
    return root


def test_source_polygon_is_not_frozen_when_it_would_recut_an_existing_lane():
    node = ET.fromstring('<junction shape="-2,-2 2,-2 2,2 -2,2"/>')
    edge = ET.fromstring('<edge><lane shape="0,0 10,0"/></edge>')
    assert not _source_polygon_preserves_cuts(node, [edge])
    edge.find('lane').set('shape', '2,0 10,0')
    assert _source_polygon_preserves_cuts(node, [edge])
    node.set('shape', '2,-2 2,2 2,-2')
    assert _source_polygon_preserves_cuts(node, [edge])


def test_collects_only_scoped_mode_consistent_source_paths():
    root = _source()
    snapshot = ET.tostring(root)
    result = collect_join_boundary_paths(root, groups={"joined": ["a", "b"]})
    movement = next(row for row in result["movements"] if row["connection"] == ["in", 0, "out", 0])
    assert movement["vehicle_classes"] == ["bus"]
    assert movement["source_paths"] == [{"lane_ids": ["in_0", "middle_0", "out_0"], "vehicle_classes": ["bus"]}]
    assert ET.tostring(root) == snapshot


def test_explicit_diagnostic_signal_rebuild_keeps_source_paths_and_input():
    root = _source()
    root.find("junction[@id='a']").set("type", "traffic_light")
    root.find("connection[@from='in']").set("tl", "local-control")
    root.find("connection[@from='in']").set("linkIndex", "0")
    snapshot = ET.tostring(root)
    result = collect_join_boundary_paths(root, groups={"joined": ["a", "b"]}, signal_policy="rebuild_diagnostic")
    assert result["retired_tls_ids"] == ["local-control"]
    path = next(row for row in result["movements"] if row["connection"] == ["in", 0, "out", 0])
    assert path["vehicle_classes"] == ["bus"]
    assert ET.tostring(root) == snapshot


def test_diagnostic_rebuild_rejects_a_controller_shared_outside_the_group():
    root = _source()
    root.find("junction[@id='a']").set("type", "traffic_light")
    for edge in ["in", "detour"]:
        root.find(f"connection[@from='{edge}']").set("tl", "shared")
    with pytest.raises(ValueError, match="outside"):
        collect_join_boundary_paths(root, groups={"joined": ["a", "b"]}, signal_policy="rebuild_diagnostic")


@pytest.mark.parametrize("groups", [{"joined": ["a", "missing"]}, {"joined": ["a", "b"], "second": ["b", "e"]}, {"joined": ["a", "e"]}, {"outside": ["a", "b"]}])
def test_rejects_unknown_or_overlapping_join_members(groups):
    with pytest.raises(ValueError):
        collect_join_boundary_paths(_source(), groups=groups)


def test_rejects_source_signal_control_even_when_a_join_was_requested():
    root = _source()
    root.find("junction[@id='b']").set("type", "traffic_light")
    with pytest.raises(ValueError, match="control"):
        collect_join_boundary_paths(root, groups={"joined": ["a", "b"]})


def test_clipped_lane_reference_never_keeps_old_points_beyond_its_endpoints(tmp_path):
    root = _source()
    incoming, outgoing = root.find("edge[@id='in']"), root.find("edge[@id='out']")
    incoming.set("shape", "0,0 6,0 10,0")
    incoming.find("lane").set("shape", "0,0 5,0")
    outgoing.set("shape", "10,0 13,0 20,0")
    outgoing.find("lane").set("shape", "15,0 20,0")
    plan = {"groups": [{"join_id": "joined", "source_node_ids": ["a", "b"], "incoming_edge_ids": ["in"], "outgoing_edge_ids": ["out"]}]}
    files, _ = _boundary_geometry(root, _index(root), plan, tmp_path)
    edges = ET.parse(files["edges"]).getroot()
    for edge_id, lower, upper in (("in", 0, 5), ("out", 15, 20)):
        points = [tuple(map(float, point.split(","))) for point in edges.find(f"edge[@id='{edge_id}']").get("shape").split()]
        assert all(lower <= point[0] <= upper for point in points)
        assert all(a[0] <= b[0] for a, b in zip(points, points[1:]))


@pytest.mark.skipif(not shutil.which("netconvert") or not shutil.which("sumo"), reason="SUMO binaries required")
def test_native_restore_keeps_source_signals_geometry_and_original_od_evidence(tmp_path):
    nodes, edges = tmp_path / "nodes.xml", tmp_path / "edges.xml"
    nodes.write_text('<nodes><node id="f" x="-160" y="0"/><node id="w" x="-100" y="0" type="traffic_light"/><node id="a" x="0" y="0"/><node id="b" x="20" y="0"/><node id="e" x="120" y="0"/><node id="p" x="400" y="0"/><node id="r" x="500" y="0" type="traffic_light"><param key="source-note" value="keep"/></node><node id="q" x="600" y="0"/></nodes>', encoding="utf-8")
    edges.write_text('<edges><edge id="upstream" from="f" to="w" allow="passenger bus"/><edge id="in" from="w" to="a" allow="passenger bus"/><edge id="middle" from="a" to="b" allow="passenger bus"/><edge id="out" from="b" to="e" allow="passenger bus"/><edge id="remote-in" from="p" to="r"/><edge id="remote-out" from="r" to="q"/></edges>', encoding="utf-8")
    source, joined = tmp_path / "source.net.xml", tmp_path / "joined.net.xml"
    subprocess.run(["netconvert", "-n", str(nodes), "-e", str(edges), "-o", str(source)], check=True, capture_output=True, timeout=30)
    join = tmp_path / "join.xml"
    join.write_text('<nodes><join id="joined" nodes="a b" reset="false" type="priority"/></nodes>', encoding="utf-8")
    subprocess.run(["netconvert", "-s", str(source), "-n", str(join), "--offset.disable-normalization", "true", "-o", str(joined)], check=True, capture_output=True, timeout=30)
    tree = ET.parse(joined)
    root = tree.getroot()
    # Deliberately remove one boundary movement and reproduce an unrelated
    # round-trip side effect. Neither mutation touches the immutable source.
    for connection in list(root.findall("connection")):
        if connection.get("from") == "in" or connection.get("from", "").startswith(":joined_"):
            root.remove(connection)
    root.find("edge[@id='remote-in']/lane").set("speed", "1")
    # netconvert may recompute displayed bounds without changing the actual
    # projected coordinate frame. Keep that metadata delta visible, not a
    # false projection mismatch.
    root.find("location").set("convBoundary", "-0.59,0,700,10")
    tree.write(joined, encoding="utf-8", xml_declaration=True)
    trips = tmp_path / "original-trips.xml"
    trips.write_text('<routes><trip id="inside" depart="3" from="middle" to="out"/><trip id="outside" depart="7" from="in" to="out"/></routes>', encoding="utf-8")
    identities = [file_sha256(path) for path in (source, joined, trips)]
    result = restore_joined_boundary_connections(
        source_net=source, joined_net=joined, groups={"joined": ["a", "b"]},
        output_dir=tmp_path / "restored", expected_source_sha256=identities[0],
        expected_joined_sha256=identities[1], original_trip_files=[trips],
        adjacent_geometry_junction_ids=["w"],
    )
    assert result["status"] == "pass"
    assert result["decision"] == "review_required"
    assert result["boundary_before"]["missing"] == [["in", 0, "out", 0]]
    assert result["boundary_after"]["missing"] == []
    assert result["outside_preservation"]["status"] == "pass"
    assert result["outside_before_restore"]["changed_external_edges"] == ["remote-in"]
    assert result["input_bounds_metadata_changed"] is True
    assert result["adjacent_geometry"]["connections"][0]["connection_before"]["tl"] == "w"
    assert result["original_demand"][0]["affected_od"][0]["id"] == "inside"
    assert result["original_demand"][0]["affected_od"][0]["depart"] == "3"
    assert result["original_demand"][0]["same_demand_runnable"] is False
    assert identities == [file_sha256(path) for path in (source, joined, trips)]
    candidate = ET.parse(result["candidate_network"]["path"]).getroot()
    assert candidate.find("edge[@id='remote-in']/lane").get("speed") == ET.parse(source).getroot().find("edge[@id='remote-in']/lane").get("speed")
    source_root = ET.parse(source).getroot()
    assert candidate.find("junction[@id='w']").get("customShape") == "1"
    assert candidate.find("junction[@id='w']").get("shape") == source_root.find("junction[@id='w']").get("shape")
    assert candidate.find("junction[@id='r']").attrib == source_root.find("junction[@id='r']").attrib
    assert "w" in result["outside_preservation"]["fixed_shape_marker_junction_ids"]
    reloaded = tmp_path / "reloaded.net.xml"
    subprocess.run(["netconvert", "-s", result["candidate_network"]["path"], "--offset.disable-normalization", "true", "-o", str(reloaded)], check=True, capture_output=True, timeout=30)
    assert ET.parse(reloaded).getroot().find("junction[@id='w']").get("shape") == source_root.find("junction[@id='w']").get("shape")
    with pytest.raises(ValueError, match="SHA-256"):
        restore_joined_boundary_connections(source_net=source, joined_net=joined, groups={"joined": ["a", "b"]}, output_dir=tmp_path / "bad", expected_source_sha256="0" * 64, expected_joined_sha256=identities[1])
    with pytest.raises(ValueError, match="directly meet"):
        restore_joined_boundary_connections(source_net=source, joined_net=joined, groups={"joined": ["a", "b"]}, output_dir=tmp_path / "unrelated-neighbor", expected_source_sha256=identities[0], expected_joined_sha256=identities[1], adjacent_geometry_junction_ids=["r"])
