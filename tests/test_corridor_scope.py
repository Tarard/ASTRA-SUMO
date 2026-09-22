from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from torii_sumo.core.corridor_scope import (
    CorridorScopeError,
    select_compact_corridor_edges,
)


def _write_network(path: Path) -> None:
    root = ET.Element("net")
    ET.SubElement(
        root,
        "location",
        {
            "netOffset": "0,0",
            "convBoundary": "0,0,500,500",
            "origBoundary": "0,0,0.01,0.01",
            "projParameter": "+proj=utm +zone=31 +datum=WGS84 +units=m +no_defs",
        },
    )
    definitions = [
        ("west", "n0", "n1", "166021.4,0 166051.4,0", 30),
        ("left", "n1", "n2", "166051.4,0 166081.4,0", 30),
        ("bridge", "n2", "n3", "166081.4,70 166111.4,70", 30),
        ("right", "n3", "n4", "166111.4,0 166141.4,0", 30),
        ("east", "n4", "n5", "166141.4,0 166171.4,0", 30),
        ("remote", "x0", "x1", "166031.4,100 166061.4,100", 30),
    ]
    for edge_id, from_node, to_node, shape, length in definitions:
        edge = ET.SubElement(
            root,
            "edge",
            {"id": edge_id, "from": from_node, "to": to_node},
        )
        ET.SubElement(
            edge,
            "lane",
            {
                "id": f"{edge_id}_0",
                "index": "0",
                "speed": "13.89",
                "length": str(length),
                "shape": shape,
            },
        )
    for from_edge, to_edge in (
        ("west", "left"),
        ("left", "bridge"),
        ("bridge", "right"),
        ("right", "east"),
    ):
        ET.SubElement(
            root,
            "connection",
            {"from": from_edge, "to": to_edge, "fromLane": "0", "toLane": "0"},
        )
    ET.indent(root, space="    ")
    ET.ElementTree(root).write(path, encoding="utf-8", xml_declaration=True)


def _write_directional_bridge_network(
    path: Path,
    *,
    reverse_connected: bool = True,
    reverse_oneway: str = "",
    reverse_allows_passenger: bool = True,
    ambiguous_reverse: bool = False,
) -> None:
    root = ET.Element("net")
    ET.SubElement(
        root,
        "location",
        {
            "netOffset": "0,0",
            "convBoundary": "0,0,500,500",
            "origBoundary": "0,0,0.01,0.01",
            "projParameter": "+proj=utm +zone=31 +datum=WGS84 +units=m +no_defs",
        },
    )

    def add_edge(
        edge_id: str,
        from_node: str,
        to_node: str,
        shape: str,
        *,
        allows_passenger: bool = True,
        oneway: str = "",
    ) -> None:
        edge = ET.SubElement(
            root,
            "edge",
            {"id": edge_id, "from": from_node, "to": to_node},
        )
        lane_attributes = {
            "id": f"{edge_id}_0",
            "index": "0",
            "speed": "13.89",
            "length": "30",
            "shape": shape,
        }
        if not allows_passenger:
            lane_attributes["allow"] = "bicycle"
        ET.SubElement(edge, "lane", lane_attributes)
        if oneway:
            ET.SubElement(edge, "param", {"key": "oneway", "value": oneway})

    add_edge("10", "n0", "n1", "166021.4,0 166051.4,0")
    add_edge("-200", "n1", "nx", "166051.4,0 166081.4,0")
    add_edge("100#0", "n1", "n2", "166051.4,70 166111.4,70")
    add_edge(
        "-100#0",
        "n2",
        "n1",
        "166111.4,70 166051.4,70",
        allows_passenger=reverse_allows_passenger,
        oneway=reverse_oneway,
    )
    if ambiguous_reverse:
        add_edge("-100#1", "n2", "n1", "166111.4,72 166051.4,72")
    add_edge("20", "n2", "n3", "166111.4,0 166141.4,0")
    connections = [("10", "-200"), ("10", "100#0"), ("100#0", "20")]
    if reverse_connected:
        connections.append(("-100#0", "-200"))
    for from_edge, to_edge in connections:
        ET.SubElement(
            root,
            "connection",
            {"from": from_edge, "to": to_edge, "fromLane": "0", "toLane": "0"},
        )
    ET.indent(root, space="    ")
    ET.ElementTree(root).write(path, encoding="utf-8", xml_declaration=True)


def test_selection_bridges_only_required_geometry_components_and_drops_remote_seed(
    tmp_path: Path,
) -> None:
    net = tmp_path / "source.net.xml"
    _write_network(net)

    selection = select_compact_corridor_edges(
        net,
        centers_lonlat=((0.00027, 0.0), (0.00135, 0.0)),
        required_lane_ids=("left_0", "right_0"),
        corridor_buffer_m=15,
        intersection_stub_radius_m=20,
        max_bridge_length_m=100,
    )

    assert {"left", "right", "bridge"}.issubset(selection.selected_edge_ids)
    assert selection.bridge_edge_ids == ("bridge",)
    assert "remote" not in selection.selected_edge_ids
    assert selection.selected_component_count == 1


def test_selection_fails_closed_for_missing_required_lane(tmp_path: Path) -> None:
    net = tmp_path / "source.net.xml"
    _write_network(net)

    with pytest.raises(CorridorScopeError, match="absent"):
        select_compact_corridor_edges(
            net,
            centers_lonlat=((0.00027, 0.0), (0.00135, 0.0)),
            required_lane_ids=("does_not_exist_0",),
        )


def test_selection_rejects_excessive_component_bridge(tmp_path: Path) -> None:
    net = tmp_path / "source.net.xml"
    _write_network(net)

    with pytest.raises(CorridorScopeError, match="excessive bridge"):
        select_compact_corridor_edges(
            net,
            centers_lonlat=((0.00027, 0.0), (0.00135, 0.0)),
            required_lane_ids=("left_0", "right_0"),
            corridor_buffer_m=15,
            intersection_stub_radius_m=20,
            max_bridge_length_m=10,
        )


def test_selection_adds_source_proven_connected_reverse_osm_sibling(tmp_path: Path) -> None:
    net = tmp_path / "source.net.xml"
    _write_directional_bridge_network(net)

    selection = select_compact_corridor_edges(
        net,
        centers_lonlat=((0.00027, 0.0), (0.00135, 0.0)),
        required_lane_ids=("10_0", "20_0"),
        corridor_buffer_m=15,
        intersection_stub_radius_m=20,
        max_bridge_length_m=100,
    )

    assert selection.bridge_edge_ids == ("100#0",)
    assert selection.added_directional_sibling_edge_ids == ("-100#0",)
    assert "-100#0" in selection.selected_edge_ids
    assert selection.excluded_directional_sibling_edge_ids == ()
    assert len(selection.directional_sibling_evidence_sha256) == 64
    evidence = selection.directional_sibling_evidence[0]
    assert evidence.osm_way_id == "100"
    assert evidence.reciprocal_endpoints is True
    assert evidence.connected_to_retained_scope is True
    assert evidence.decision == "included"


def test_selection_excludes_reverse_sibling_outside_retained_component(tmp_path: Path) -> None:
    net = tmp_path / "source.net.xml"
    _write_directional_bridge_network(net, reverse_connected=False)

    selection = select_compact_corridor_edges(
        net,
        centers_lonlat=((0.00027, 0.0), (0.00135, 0.0)),
        required_lane_ids=("10_0", "20_0"),
        corridor_buffer_m=15,
        intersection_stub_radius_m=20,
        max_bridge_length_m=100,
    )

    assert selection.added_directional_sibling_edge_ids == ()
    assert selection.excluded_directional_sibling_edge_ids == ("-100#0",)
    assert "-100#0" not in selection.selected_edge_ids
    evidence = selection.directional_sibling_evidence[0]
    assert evidence.reason == "reverse_span_disconnected_from_retained_scope"
    assert selection.selected_component_count == 1


@pytest.mark.parametrize(
    ("kwargs", "reason"),
    [
        ({"reverse_oneway": "yes"}, "oneway_evidence"),
        ({"reverse_allows_passenger": False}, "passenger_permission"),
    ],
)
def test_selection_does_not_add_reverse_sibling_without_operational_evidence(
    tmp_path: Path,
    kwargs: dict[str, object],
    reason: str,
) -> None:
    net = tmp_path / "source.net.xml"
    _write_directional_bridge_network(net, **kwargs)

    selection = select_compact_corridor_edges(
        net,
        centers_lonlat=((0.00027, 0.0), (0.00135, 0.0)),
        required_lane_ids=("10_0", "20_0"),
        corridor_buffer_m=15,
        intersection_stub_radius_m=20,
        max_bridge_length_m=100,
    )

    assert selection.added_directional_sibling_edge_ids == ()
    assert selection.directional_sibling_evidence[0].reason == reason


def test_selection_fails_closed_for_ambiguous_reverse_osm_sibling(tmp_path: Path) -> None:
    net = tmp_path / "source.net.xml"
    _write_directional_bridge_network(net, ambiguous_reverse=True)

    with pytest.raises(CorridorScopeError, match="ambiguous reverse OSM sibling"):
        select_compact_corridor_edges(
            net,
            centers_lonlat=((0.00027, 0.0), (0.00135, 0.0)),
            required_lane_ids=("10_0", "20_0"),
            corridor_buffer_m=15,
            intersection_stub_radius_m=20,
            max_bridge_length_m=100,
        )
