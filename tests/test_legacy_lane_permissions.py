import shutil
import xml.etree.ElementTree as ET

import pytest
import traci

from astra_sumo.core.osm_access import _permission_set
from astra_sumo.core.network_permissions import _add_passenger_to_lane


@pytest.mark.skipif(shutil.which("sumo") is None, reason="SUMO required")
@pytest.mark.parametrize("version,attributes", [
    ("1.3", {"disallow": "rail_urban"}),
    ("1.20", {"disallow": "rail_urban"}),
    ("1.2", {"disallow": "passenger"}),
    ("1.20", {"allow": "public_transport"}),
    ("1.20", {"allow": "bus", "disallow": "bus"}),
    ("1.20", {}),
])
def test_permission_sets_match_native_lane_loading_including_legacy_versions(tmp_path, version, attributes):
    root = ET.Element("net", version=version)
    for name, start, end, y, permissions in (("road", "a", "b", 0, attributes), ("control", "c", "d", 10, {"allow": "bus"})):
        edge = ET.SubElement(root, "edge", id=name, **{"from": start, "to": end, "priority": "1"})
        ET.SubElement(edge, "lane", id=f"{name}_0", index="0", speed="13.89", length="100", shape=f"0,{y} 100,{y}", **permissions)
        for identity, x, incoming in ((start, 0, ""), (end, 100, f"{name}_0")):
            ET.SubElement(root, "junction", id=identity, x=str(x), y=str(y), type="dead_end", incLanes=incoming,
                          intLanes="", shape=f"{x},{y-1} {x},{y+1}")
    # Native networks group all edges before junctions.
    ordered = ET.Element("net", version=version)
    ordered.extend(root.findall("edge"))
    ordered.extend(root.findall("junction"))
    network = tmp_path / "source.net.xml"
    ET.ElementTree(ordered).write(network, encoding="utf-8", xml_declaration=True)
    label = f"permissions-{tmp_path.name}"
    traci.start([shutil.which("sumo"), "-n", str(network), "--end", "1", "--no-step-log", "true"], label=label)
    connection = traci.getConnection(label)
    try:
        universe = set(connection.lane.getDisallowed("control_0")) | {"bus"}
        native = universe - set(connection.lane.getDisallowed("road_0"))
    finally:
        connection.close()
    assert _permission_set(attributes, network_version=version) == native


def test_service_passenger_addition_keeps_the_other_legacy_restrictions():
    lane = ET.fromstring('<lane id="service_0" disallow="passenger rail_urban"/>')
    assert _add_passenger_to_lane(lane, network_version="1.3")
    modes = set(lane.get("allow").split())
    assert "passenger" in modes
    assert not modes & {"rail_urban", "subway", "cable_car"}
