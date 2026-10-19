import json
import shutil
import xml.etree.ElementTree as ET

import pytest

from astra_sumo.core.network_calibration import evaluate_network_calibration
from astra_sumo.core.network_edit_applicability import _boundary_access
from test_network_calibration import _case
from test_traffic_effect_review import _native_network, _native_access


native = pytest.mark.skipif(not shutil.which("sumo") or not shutil.which("netconvert"), reason="SUMO required")


@native
@pytest.mark.parametrize("version,attributes,selector,canonical,connection_attributes", [
    ("1.20", {"allow": "public_transport"}, "bus", "bus", {}),
    ("1.20", {"allow": "bus"}, "public_transport", "bus", {}),
    ("1.3", {"disallow": "rail_urban"}, "subway", "subway", {}),
    ("1.2", {"disallow": "bus"}, "rail_fast", "rail_fast", {}),
    ("1.20", {"disallow": "bus"}, "rail_fast", "rail_fast", {}),
    ("1.20", {"allow": "all", "disallow": "bus"}, "bus", "bus", {}),
    ("1.20", {}, "passenger", "passenger", {"allow": "bus"}),
])
def test_calibration_and_local_boundary_checks_match_native_permissions(
        tmp_path, version, attributes, selector, canonical, connection_attributes):
    root = _native_network(tmp_path)
    root.set("version", version)
    root.find("edge[@id='in']/lane").attrib.update(attributes)
    root.find("connection[@from='in'][@to='out']").attrib.update(connection_attributes)
    paths = _case(tmp_path, [{"kind": "lane_count", "selector": {"edge_ids": ["in"], "vehicle_class": selector},
                              "expected": 0}])
    ET.ElementTree(root).write(paths[0], encoding="utf-8", xml_declaration=True)
    # Only the incoming lane is restricted; the native movement tests its permission too.
    observed = _native_access(paths[0], canonical)["admitted"]
    table = json.loads(paths[1].read_text(encoding="utf-8"))
    table["coordinate_system"]["location"] = dict(root.find("location").attrib)
    table["facts"][0]["expected"] = int(observed)
    paths[1].write_text(json.dumps(table), encoding="utf-8")
    score = evaluate_network_calibration(net_file=paths[0], facts_file=paths[1], output_file=paths[2], mode="explore")
    assert score["facts"][0]["observed"] == int(observed), score["facts"][0]
    assert score["facts"][0]["status"] == "matched"
    access = _boundary_access(root, {"j"})[2].get(("in_0", "out_0"), set())
    assert (canonical in access) == observed


@native
@pytest.mark.parametrize("declared,expected", [("public_transport", "bus"), ("bus", "public_transport")])
def test_permission_facts_resolve_aliases_on_both_sides(tmp_path, declared, expected):
    root = _native_network(tmp_path)
    root.set("version", "1.20")
    root.find("edge[@id='in']/lane").set("allow", declared)
    paths = _case(tmp_path, [{"kind": "lane_permissions", "selector": {"lane_id": "in_0"}, "expected": [expected]}])
    ET.ElementTree(root).write(paths[0], encoding="utf-8", xml_declaration=True)
    table = json.loads(paths[1].read_text(encoding="utf-8"))
    table["coordinate_system"]["location"] = dict(root.find("location").attrib)
    paths[1].write_text(json.dumps(table), encoding="utf-8")
    assert _native_access(paths[0], "bus")["admitted"]
    assert not _native_access(paths[0], "passenger")["admitted"]
    score = evaluate_network_calibration(net_file=paths[0], facts_file=paths[1], output_file=paths[2], mode="explore")
    assert score["facts"][0]["observed"] == ["bus"]
    assert score["facts"][0]["status"] == "matched", score["facts"][0]
