from copy import deepcopy
import xml.etree.ElementTree as ET

import pytest

from astra_sumo.core.corridor_edit_ledger import propose_corridor_edits


@pytest.mark.parametrize(
    "difference",
    [None, "shape", "length", "lane_rule", "lane_parameter", "edge_priority", "connection", "same_connections"],
)
def test_duplicate_deletion_requires_equal_geometry_rules_and_movements(tmp_path, difference):
    root = ET.fromstring(
        '<net><junction id="a" type="priority"/><junction id="b" type="priority"/>'
        '<edge id="first" from="a" to="b" priority="1" type="residential"><lane id="first_0" index="0" '
        'allow="passenger" speed="10" width="3.2" length="10" shape="0,0,0 10,0,0"/></edge></net>'
    )
    second = deepcopy(root.find("edge"))
    second.set("id", "second")
    lane = second.find("lane")
    lane.set("id", "second_0")
    if difference == "shape":
        lane.set("shape", "0,0,0 5,1,2 10,0,0")
    elif difference == "length":
        lane.set("length", "11")
    elif difference == "lane_rule":
        lane.set("changeLeft", "bus")
    elif difference == "lane_parameter":
        ET.SubElement(lane, "param", key="purpose", value="retained-separate-path")
    elif difference == "edge_priority":
        second.set("priority", "3")
    elif difference == "connection":
        ET.SubElement(root, "connection", **{"from": "first", "to": "out", "fromLane": "0", "toLane": "0"})
    elif difference == "same_connections":
        for name in ("first", "second"):
            ET.SubElement(root, "connection", **{"from": name, "to": "out", "fromLane": "0", "toLane": "0"})
            ET.SubElement(root, "connection", **{"from": "in", "to": name, "fromLane": "0", "toLane": "0"})
    root.append(second)
    source = tmp_path / "source.net.xml"
    ET.ElementTree(root).write(source)
    original = source.read_bytes()
    proposals = propose_corridor_edits(source, include_isolated=False, include_micro_merges=False)
    assert len(proposals) == (1 if difference in {None, "same_connections"} else 0), proposals
    if proposals:
        assert proposals[0]["operation"] == "delete_edge"
        assert proposals[0]["target_ids"] == ["second"]
    assert source.read_bytes() == original
