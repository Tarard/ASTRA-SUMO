from __future__ import annotations

import xml.etree.ElementTree as ET
import pytest

from astra_sumo.core.network_structure_review import review_network_structures


def _network() -> ET.Element:
    return ET.fromstring('''<net>
<edge id="a" from="A" to="J"><lane id="a_0" index="0"/>
<param key="tunnel" value="building_passage"/><param key="maxheight" value="1.95"/></edge>
<edge id="back" from="J" to="A"><lane id="back_0" index="0"/>
<param key="tunnel" value="building_passage"/></edge>
<edge id="exit" from="J" to="B"><lane id="exit_0" index="0"/></edge>
<edge id=":J_0" function="internal"><lane id=":J_0_0" index="0"/></edge>
<edge id=":J_1" function="internal"><lane id=":J_1_0" index="0"/></edge>
<edge id=":J_2" function="internal"><lane id=":J_2_0" index="0"/></edge>
<junction id="A" incLanes="back_0"/><junction id="J" incLanes="a_0"/>
<connection from="a" to="back" fromLane="0" toLane="0" via=":J_0_0" dir="t"/>
<connection from=":J_0" to="back" fromLane="0" toLane="0" via=":J_1_0"/>
<connection from=":J_1" to="back" fromLane="0" toLane="0"/>
<connection from="a" to="exit" fromLane="0" toLane="0" via=":J_2_0" dir="s"/>
<connection from=":J_2" to="exit" fromLane="0" toLane="0"/>
</net>''')


def test_structure_follows_connections_and_preserves_turnaround_for_review() -> None:
    root = _network()
    before = ET.tostring(root)
    report = review_network_structures(root)
    assert ET.tostring(root) == before
    assert report["lane_structures"][":J_0_0"] == "building_passage"
    assert report["lane_structures"][":J_1_0"] == "building_passage"
    assert report["lane_structures"][":J_2_0"] == "transition"
    assert report["junction_structures"] == {"A": "building_passage", "J": "transition"}
    assert report["lane_structures"]["exit_0"] == "unspecified"
    assert report["untraced_connections"] == []
    turns = report["turnarounds_requiring_review"]
    assert len(turns) == 1 and turns[0]["status"] == "review_required"
    assert turns[0]["from_annotations"]["maxheight"] == "1.95"
    root[:] = list(reversed(list(root)))
    assert review_network_structures(root) == report


def test_unrelated_internal_lane_does_not_inherit_a_nearby_structure() -> None:
    root = _network()
    ET.SubElement(ET.SubElement(root, "edge", id=":unrelated", function="internal"),
                  "lane", id=":unrelated_0", index="0")
    assert ":unrelated_0" not in review_network_structures(root)["lane_structures"]


def test_incomplete_internal_chain_remains_unresolved() -> None:
    root = _network()
    connection = next(c for c in root.findall("connection") if c.get("from") == ":J_0")
    connection.set("via", ":missing_0")
    report = review_network_structures(root)
    assert len(report["untraced_connections"]) == 1
    assert report["untraced_connections"][0]["reason"] == "missing_or_cyclic_internal_lane"
    assert report["turnarounds_requiring_review"][0]["status"] == "review_required"


def test_unknown_metadata_does_not_establish_a_ground_road() -> None:
    root = _network()
    edge = next(e for e in root.findall("edge") if e.get("id") == "exit")
    ET.SubElement(edge, "param", key="bridge", value="yes")
    ET.SubElement(edge, "param", key="layer", value="1")
    report = review_network_structures(root)
    assert report["lane_structures"]["exit_0"] == "bridge"
    assert report["lane_structures"][":J_2_0"] == "transition"


def test_connection_lane_sequence_survives_inconsistent_index_attributes() -> None:
    root = _network()
    edge = next(e for e in root.findall("edge") if e.get("id") == ":J_1")
    ET.SubElement(edge, "lane", id=":J_1_1", index="0")
    ET.SubElement(root, "connection", {"from": "a", "to": "back", "fromLane": "0", "toLane": "0",
                                       "via": ":J_1_1", "dir": "t"})
    ET.SubElement(root, "connection", {"from": ":J_1", "to": "back", "fromLane": "1", "toLane": "0"})
    report = review_network_structures(root)
    assert report["untraced_connections"] == []
    assert report["lane_structures"][":J_1_1"] == "building_passage"
    assert report["lane_index_mismatches"] == [{"lane_id": ":J_1_1", "declared_index": "0", "sequence_index": 1}]


def test_duplicate_edge_identity_is_rejected() -> None:
    root = _network()
    ET.SubElement(root, "edge", id="a")
    with pytest.raises(ValueError, match="identifiers"):
        review_network_structures(root)
