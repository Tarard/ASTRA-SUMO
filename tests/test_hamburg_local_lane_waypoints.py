import json
import xml.etree.ElementTree as ET

import pytest

from astra_sumo.core.hamburg_readme_pipeline import localize_hamburg_lane_waypoints


def test_local_waypoints_preserve_demand_and_release_unobserved_lanes(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    files = {
        "network.net.xml": '<net><edge id="a"><lane id="a_0" length="30" speed="10"/></edge><edge id="b"><lane id="b_0" length="4" speed="10"/></edge></net>',
        "demand.rou.xml": '<routes><vType id="car" lcKeepRight="0" lcSpeedGain="0"/><vehicle id="v1" depart="10"><route edges="a b"/><stop lane="b_0" endPos="3.99" speed="10"/></vehicle><vehicle id="v2" depart="20"><route edges="b"/></vehicle></routes>',
        "field-loops.add.xml": '<additional><inductionLoop id="f1" lane="a_0" pos="29.9"/><inductionLoop id="f2" lane="b_0" pos="3.9"/></additional>',
        "expected-counts.csv": "detector_id,begin,end,expected_total\nf1,0,900,1\n",
        "control.add.xml": "<additional/>",
        "scenario.json": json.dumps({"vehicles": 2}),
        "lane-intentions.json": json.dumps({"vehicles": [{"vehicle_id": "v1", "field_id": "f1"}, {"vehicle_id": "v2", "field_id": "f2"}]}),
    }
    for name, text in files.items():
        (source / name).write_text(text, encoding="utf-8")
    output = tmp_path / "candidate"
    result = localize_hamburg_lane_waypoints(prepared_dir=source, output_dir=output)
    assert result["removed_waypoints"] == result["added_waypoints"] == 1
    root = ET.parse(output / "demand.rou.xml").getroot()
    assert root.find("vType").attrib == {"id": "car"}
    first, second = root.findall("vehicle")
    assert first.attrib == {"id": "v1", "depart": "10"}
    assert first.find("route").get("edges") == "a b"
    assert first.find("stop").attrib == {"lane": "a_0", "endPos": "22.500000", "speed": "10"}
    assert second.find("stop") is None
    for name in result["unchanged"]:
        assert (output / name).read_bytes() == (source / name).read_bytes()
    assert (source / "demand.rou.xml").read_text(encoding="utf-8") == files["demand.rou.xml"]
    with pytest.raises(ValueError, match="new preparation"):
        localize_hamburg_lane_waypoints(prepared_dir=source, output_dir=output)
