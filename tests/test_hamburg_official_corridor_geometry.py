from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from torii_sumo.core.hamburg_official_corridor_geometry import (
    HamburgOfficialCorridorGeometryError,
    _build_connections,
    _build_nodes,
    _derived_edges,
    _resolve_sources,
    _anchor_local_junction_edge_shapes,
    _convex_hull,
    _ensure_network_projection_metadata,
    _official_lsa_control_boundary_evidence,
    _shape,
    _synthesize_signal_node_shapes,
    materialize_hamburg_official_corridor_geometry,
)


def _local_connections(*connections: ET.Element) -> dict[str, ET.Element]:
    root = ET.Element("connections")
    for connection in connections:
        root.append(connection)
    return {"2349": {"con": root}, "2394": {"con": ET.Element("connections")}}


def test_derived_boundary_edges_have_stable_ids_and_lane_shapes() -> None:
    edges = _derived_edges({})

    assert {edge.attrib["id"] for edge in edges} == {
        "corridor_2349_2394_east_upstream",
        "corridor_2349_2394_east_downstream",
        "corridor_2349_2394_west_upstream",
        "corridor_2349_2394_west_downstream",
        "bridge_2349_grasbrook_out",
        "bridge_2349_grasbrook_in",
        "bridge_2394_sandtorpark_out",
        "bridge_2394_sandtorpark_in",
        "corridor_2394_2403_east",
        "corridor_2394_2403_west",
    }
    for edge in edges:
        lanes = edge.findall("lane")
        assert len(lanes) == int(edge.attrib["numLanes"])
        assert all(lane.attrib.get("shape", "").count(" ") >= 1 for lane in lanes)
        assert edge.find("param[@key='torii:automatic_promotion']") is not None


def test_local_map_connections_are_rebound_to_derived_edges_and_keep_map_shapes() -> None:
    connection = ET.Element(
        "connection",
        {
            "from": "hh-map-2349-a1-in",
            "to": "hh-map-2349-a1-out",
            "fromLane": "99",
            "toLane": "99",
            "shape": "1,1 2,2",
        },
    )

    rewritten = next(
        item
        for item in _build_connections(_local_connections(connection))
        if item.attrib.get("shape") == "1,1 2,2"
        and item.attrib.get("from") == "corridor_2349_2394_west_downstream"
    )

    assert rewritten.attrib["from"] == "corridor_2349_2394_west_downstream"
    assert rewritten.attrib["to"] == "corridor_2349_2394_east_upstream"
    assert rewritten.attrib["fromLane"] == "1"
    assert rewritten.attrib["toLane"] == "1"
    assert rewritten.attrib["shape"] == "1,1 2,2"


def test_rebound_map_curve_is_reanchored_to_new_lane_endpoints() -> None:
    connection = ET.Element(
        "connection",
        {
            "from": "hh-map-2349-a1-in",
            "to": "hh-map-2349-a1-out",
            "fromLane": "99",
            "toLane": "99",
            "shape": "565840.515,5933176.193 565822.639,5933173.007 565780.587,5933167.758",
        },
    )

    rewritten = _build_connections(_local_connections(connection), edges=_derived_edges({}))[0]
    points = rewritten.attrib["shape"].split()

    assert points[0] == "565820.981,5933161.338"
    assert points[-1] == "565820.199,5933164.442"


def test_local_map_edge_endpoint_is_anchored_without_moving_boundary() -> None:
    local_nodes = ET.fromstring(
        '<nodes><node id="core" type="traffic_light" x="10" y="20"/>'
        '<node id="boundary" type="priority" x="0" y="20"/></nodes>'
    )
    edge = ET.fromstring(
        '<edge id="arm" from="core" to="boundary"><lane index="0" '
        'shape="0,0 0,10"/></edge>'
    )
    _anchor_local_junction_edge_shapes(
        [edge],
        {"2349": {"nod": local_nodes}, "2394": {"nod": ET.Element("nodes")}},
    )

    points = edge.find("lane").attrib["shape"].split()
    assert points[0] == "10.000,20.000"
    assert points[-1] == "0.000,10.000"


def test_local_signal_node_drops_stale_custom_shape() -> None:
    hh_nodes = ET.Element("nodes")
    hh_edges = ET.Element("edges")
    local_node = ET.fromstring(
        '<node id="hh-map-2349-core" type="traffic_light" x="10" y="20" '
        'shape="0,0 50,50"/>'
    )
    local = {
        "2349": {"nod": ET.Element("nodes"), "edg": ET.Element("edges")},
        "2394": {"nod": ET.Element("nodes"), "edg": ET.Element("edges")},
    }
    local["2349"]["nod"].append(local_node)

    nodes = _build_nodes(hh_nodes, hh_edges, local)
    rebuilt = next(node for node in nodes if node.attrib.get("id") == "hh-map-2349-core")
    assert "shape" not in rebuilt.attrib


def test_signal_node_shape_is_a_simple_endpoint_hull() -> None:
    node = ET.Element("node", {"id": "core", "type": "traffic_light"})
    edges = [
        ET.fromstring(
            '<edge id="west" from="west" to="core"><lane index="0" '
            'shape="0,0 9,0"/></edge>'
        ),
        ET.fromstring(
            '<edge id="east" from="core" to="east"><lane index="0" '
            'shape="11,0 20,0"/></edge>'
        ),
        ET.fromstring(
            '<edge id="south" from="south" to="core"><lane index="0" '
            'shape="10,-9 10,-1"/></edge>'
        ),
    ]
    _synthesize_signal_node_shapes([node], edges)

    hull = _convex_hull([(9, 0), (11, 0), (10, -1)])
    assert node.attrib["shape"] == _shape(hull)


def test_compiled_metric_network_declares_hamburg_projection(tmp_path: Path) -> None:
    network = tmp_path / "candidate.net.xml"
    network.write_text(
        '<net><location netOffset="0,0" convBoundary="565000,5933000,566000,5934000" '
        'origBoundary="565000,5933000,566000,5934000" projParameter="!"/></net>\n',
        encoding="utf-8",
    )

    report = _ensure_network_projection_metadata(network)

    assert report["status"] == "pass"
    assert report["projection"] == "EPSG:25832"
    assert "+zone=32" in ET.parse(network).getroot().find("location").attrib["projParameter"]


def test_official_lsa_point_is_compared_without_snapping_hh_sib_boundary(tmp_path: Path) -> None:
    nodes = [
        ET.Element(
            "node",
            {"id": "hh_sib.n.242500071", "x": "566117.41", "y": "5933264.86"},
        )
    ]
    identity = tmp_path / "lsa-identity.json"
    identity.write_text(
        json.dumps(
            {
                "schema": "torii.hamburg-lsa-node-identity-evidence/v1",
                "decision": "pass",
                "selections": [
                    {
                        "expected_node_id": "2403",
                        "selected_node": {
                            "node_id": "2403",
                            "official_name": "Am Sandtorkai/Osakaallee",
                            "point_geometry": {
                                "type": "Point",
                                "coordinates": [9.997839159986002, 53.54409500839008],
                            },
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    evidence = _official_lsa_control_boundary_evidence(nodes, lsa_identity_manifest=identity)

    assert evidence["status"] == "pass"
    assert evidence["geometry_action"] == "retain_hh_sib_road_boundary_without_signal_point_snap"
    assert evidence["official_lsa_point_projected"]["x"] == pytest.approx(566119.5)
    assert evidence["official_lsa_point_projected"]["y"] == pytest.approx(5933262.5)
    assert evidence["distance_m"] == pytest.approx(3.152411775, rel=1e-8)


def test_official_lsa_boundary_evidence_is_explicit_when_identity_is_not_supplied() -> None:
    evidence = _official_lsa_control_boundary_evidence(
        [ET.Element("node", {"id": "hh_sib.n.242500071", "x": "1", "y": "2"})],
        lsa_identity_manifest=None,
    )

    assert evidence == {
        "status": "not_provided",
        "node_id": "2403",
        "official_name": "Am Sandtorkai/Osakaallee",
        "hh_sib_boundary_node_id": "hh_sib.n.242500071",
        "hh_sib_boundary_point": {"crs": "EPSG:25832", "x": 1.0, "y": 2.0},
        "geometry_action": "retain_hh_sib_road_boundary_without_signal_point_snap",
        "reason": "official_lsa_identity_manifest_not_supplied",
    }


def test_source_resolution_requires_exact_local_cells(tmp_path: Path) -> None:
    hh_nodes = tmp_path / "hh.nod.xml"
    hh_edges = tmp_path / "hh.edg.xml"
    hh_types = tmp_path / "hh.typ.xml"
    for path in (hh_nodes, hh_edges, hh_types):
        path.write_text("<root/>\n", encoding="utf-8")
    cells: dict[str, Path] = {}
    for node_id in ("2349", "2394"):
        cell = tmp_path / node_id
        cell.mkdir()
        for suffix in ("nod.xml", "edg.xml", "con.xml", "tll.xml", "typ.xml"):
            path = cell / f"hamburg-official-{node_id}.{suffix}"
            path.write_text("<root/>\n", encoding="utf-8")
        cells[node_id] = cell

    resolved = _resolve_sources(hh_nodes, hh_edges, hh_types, cells)

    assert resolved["2349_nod"].name == "hamburg-official-2349.nod.xml"
    assert resolved["2394_tll"].name == "hamburg-official-2394.tll.xml"
    with pytest.raises(HamburgOfficialCorridorGeometryError, match="exactly 2349 and 2394"):
        _resolve_sources(hh_nodes, hh_edges, hh_types, {"2349": cells["2349"]})


def test_netconvert_uses_endpoint_based_junction_shapes(tmp_path: Path) -> None:
    """The compiled candidate must use SUMO's endpoint-derived junction shape."""
    source_root = tmp_path / "sources"
    source_root.mkdir()
    hh_nodes = source_root / "hh.nod.xml"
    hh_edges = source_root / "hh.edg.xml"
    hh_types = source_root / "hh.typ.xml"
    for path in (hh_nodes, hh_edges, hh_types):
        path.write_text("<root/>\n", encoding="utf-8")
    cells: dict[str, Path] = {}
    for node_id in ("2349", "2394"):
        cell = source_root / node_id
        cell.mkdir()
        for suffix in ("nod.xml", "edg.xml", "con.xml", "tll.xml", "typ.xml"):
            (cell / f"hamburg-official-{node_id}.{suffix}").write_text("<root/>\n", encoding="utf-8")
        cells[node_id] = cell

    commands: list[list[str]] = []

    def runner(command: list[str], **_: object) -> dict[str, object]:
        commands.append(command)
        return {"status": "blocked", "returncode": 1, "stderr": "", "stdout": ""}

    report = materialize_hamburg_official_corridor_geometry(
        hh_sib_nodes_file=hh_nodes,
        hh_sib_edges_file=hh_edges,
        hh_sib_types_file=hh_types,
        intersection_sources=cells,
        output_dir=tmp_path / "output",
        command_runner=runner,
    )

    assert report["status"] == "blocked"
    assert commands
    command = commands[0]
    assert "--junctions.endpoint-shape" in command
    assert command[command.index("--junctions.endpoint-shape") + 1] == "true"
