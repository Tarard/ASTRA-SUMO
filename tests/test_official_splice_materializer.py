from __future__ import annotations

import xml.etree.ElementTree as ET

from torii_sumo.road_network.official_splice_materializer import (
    _axis_node_at_station,
    _build_local_components,
    _derive_merge_through_indices,
    _forbidden_intervals,
    _source_polygon_station_interval,
)


def test_two_direction_events_keep_both_splice_boundaries() -> None:
    corridor = "hh-sib-axis-example"
    events = [
        {
            "node_id": "2150",
            "axis_station_m": 58.555434,
            "junction_station_m": 320.638660,
        },
        {
            "node_id": "2150",
            "axis_station_m": 357.614654,
            "junction_station_m": 339.308953,
        },
    ]

    intervals = _forbidden_intervals({corridor: events})[corridor]

    # The old min/max shortcut expanded past both events and removed the
    # boundary fragments needed by the two directional bridge connections.
    assert intervals == [(58.555434, 357.614654)]


def test_event_at_source_endpoint_resolves_to_splice_node() -> None:
    source = ET.fromstring(
        '<edge id="axis" from="old-from" to="old-to">'
        '<param key="torii:station_direction" value="with_stationing"/>'
        '<param key="torii:station_from_m" value="0"/>'
        '<param key="torii:station_to_m" value="10"/>'
        '<lane id="axis_0" shape="0,0 10,0"/>'
        '</edge>'
    )

    node = _axis_node_at_station(
        source,
        0.0,
        corridor_id="hh-sib-axis-example",
        events=[{"axis_station_m": 0.0, "splice_node_id": "splice"}],
        cut_nodes={},
    )

    assert node == "splice"


def test_core_polygon_intersection_returns_axis_station_interval() -> None:
    source = ET.fromstring(
        '<edge id="axis" shape="0,0 10,0">'
        '<param key="torii:station_direction" value="with_stationing"/>'
        '<param key="torii:station_from_m" value="0"/>'
        '<param key="torii:station_to_m" value="10"/>'
        '</edge>'
    )

    interval = _source_polygon_station_interval(
        source,
        [(4.0, -1.0), (6.0, -1.0), (6.0, 1.0), (4.0, 1.0)],
    )

    assert interval == (4.0, 6.0)


def test_local_core_shape_is_preserved_for_minimal_junction(tmp_path) -> None:
    xml_by_key = {
        "nod": '<nodes><node id="hh-map-1-core" type="traffic_light" shape="0,0 2,0 2,2 0,2"/><node id="boundary" type="priority"/></nodes>',
        "edg": '<edges><edge id="map-edge" from="boundary" to="hh-map-1-core" numLanes="1"><lane index="0" shape="-1,1 1,1"/></edge></edges>',
        "con": "<connections />",
        "tll": "<tlLogics />",
        "typ": "<types />",
    }
    files = {}
    for key, content in xml_by_key.items():
        path = tmp_path / f"{key}.xml"
        path.write_text(content, encoding="utf-8")
        files[key] = path

    nodes, *_ = _build_local_components(
        {"1": files},
        [
            {
                "map_edge_id": "map-edge",
                "map_role": "ingress",
                "splice_node_id": "splice",
                "map_event_xy": [0.0, 1.0],
                "axis_xy": [0.0, 1.0],
            }
        ],
    )

    core = next(node for node in nodes if node.attrib["id"] == "hh-map-1-core")
    assert core.attrib["shape"] == "0,0 2,0 2,2 0,2"


def test_merge_through_group_uses_map_destination_provenance() -> None:
    connections = [
        ET.Element("connection", {"from": "map-edge", "fromLane": "0", "to": "through"}),
        ET.Element("connection", {"from": "map-edge", "fromLane": "1", "to": "through"}),
        ET.Element("connection", {"from": "map-edge", "fromLane": "2", "to": "turn"}),
    ]

    assert _derive_merge_through_indices(
        {"map_edge_id": "map-edge"},
        ["8", "10", "11"],
        connections,
        axis_count=2,
        known_indices=[0],
    ) == [0, 1]
