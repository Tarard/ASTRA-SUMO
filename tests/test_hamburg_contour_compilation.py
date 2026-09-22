from copy import deepcopy
from xml.etree import ElementTree as ET

import pytest

from torii_sumo.core.hamburg_aerial_corridor_candidate import _compare_contour_compilation


def _network():
    return ET.fromstring('''<net lefthand="false"><location netOffset="0,0" projParameter="!"/>
      <edge id="in" from="a" to="j"><lane id="in_0" index="0" speed="13" length="10" shape="0,0 10,0" allow="passenger"/></edge>
      <edge id="out" from="j" to="b"><lane id="out_0" index="0" speed="13" length="10" shape="12,0 22,0" allow="passenger"/></edge>
      <edge id=":j_0" function="internal"><lane id=":j_0_0" index="0" speed="8" length="2" shape="10,0 12,0" allow="passenger"/></edge>
      <junction id="a" type="dead_end" shape="0,0"/>
      <junction id="j" type="traffic_light" shape="10,-2 12,-2 12,2 10,2">
        <request index="0" response="0" foes="0" cont="0"/>
      </junction><junction id="b" type="dead_end" shape="22,0"/>
      <connection from="in" fromLane="0" to="out" toLane="0" via=":j_0_0" tl="j" linkIndex="0" state="O" shape="10,0 12,0">
        <conflict foeIndex="0" response="0"/>
      </connection><tlLogic id="j" type="static" programID="0" offset="0"><phase duration="10" state="G"/></tlLogic>
    </net>''')


def test_same_curve_resampling_passes_without_mutating_either_tree():
    before = _network()
    after = deepcopy(before)
    after.find("edge[@id='in']/lane").set("shape", "0,0 3,0 7,0 10,0")
    after.find("edge[@id=':j_0']/lane").set("shape", "10,0 11,0 12,0")
    after.find("connection").set("shape", "10,0 10.5,0 11,0 12,0")
    snapshots = [ET.tostring(tree) for tree in (before, after)]
    result = _compare_contour_compilation(before, after, ["j"])
    assert result["status"] == "pass", result
    assert result["changes"]
    assert [ET.tostring(tree) for tree in (before, after)] == snapshots


def test_only_target_boundary_shape_and_marker_are_allowed():
    before = _network()
    after = deepcopy(before)
    after.find("junction[@id='j']").set("shape", "9,-3 13,-3 13,3 9,3")
    after.find("junction[@id='j']").set("customShape", "1")
    result = _compare_contour_compilation(before, after, ["j"])
    assert result["status"] == "pass", result
    assert result["target_shape_changes"][0]["junction_id"] == "j"


@pytest.mark.parametrize("xpath, attribute, value", [
    ("edge[@id='in']/lane", "shape", "10,0 0,0"),
    ("edge[@id='in']/lane", "shape", "0,1 10,1"),
    ("edge[@id='in']/lane", "shape", "0,0 5,2 10,0"),
    ("edge[@id='in']/lane", "length", "8"),
    ("edge[@id='in']/lane", "allow", "bus"),
    ("edge[@id='in']/lane", "speed", "12"),
    ("edge[@id=':j_0']/lane", "speed", "7"),
    ("edge[@id='in']/lane", "id", "in_9"),
    ("connection", "shape", None),
    ("connection", "via", ":j_99_0"),
    ("connection", "tl", "different"),
    ("connection", "linkIndex", "1"),
    ("connection", "state", "o"),
    ("connection/conflict", "response", "1"),
    ("junction[@id='j']/request", "foes", "1"),
    ("tlLogic/phase", "state", "r"),
    ("tlLogic/phase", "duration", "11"),
    ("junction[@id='a']", "shape", "1,0"),
])
def test_geometry_or_semantic_regression_is_rejected(xpath, attribute, value):
    before = _network()
    after = deepcopy(before)
    element = after.find(xpath)
    if value is None:
        element.attrib.pop(attribute)
    else:
        element.set(attribute, value)
    result = _compare_contour_compilation(before, after, ["j"])
    assert result["status"] == "blocked", result
    assert any(row["status"] == "blocked" for row in result["changes"]), result
