import shutil
import subprocess
import xml.etree.ElementTree as ET

import pytest
import traci

from astra_sumo.core.osm_access import _permission_set, _permission_attributes_for_version
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
    # Small fixtures must fail quickly if the native server cannot start.
    traci.start([shutil.which("sumo"), "-n", str(network), "--end", "1", "--no-step-log", "true"], label=label, numRetries=3)
    connection = traci.getConnection(label)
    try:
        universe = set(connection.lane.getDisallowed("control_0")) | {"bus"}
        native = universe - set(connection.lane.getDisallowed("road_0"))
    finally:
        connection.close()
    assert _permission_set(attributes, network_version=version) == native
    for target_version in ("1.2", "1.3", "1.20"):
        ordered.set("version", target_version)
        lane = ordered.find("edge[@id='road']/lane")
        lane.attrib.pop("allow", None)
        lane.attrib.pop("disallow", None)
        lane.attrib.update(_permission_attributes_for_version(attributes,
            source_version=version, target_version=target_version))
        translated = tmp_path / f"translated-{target_version}.net.xml"
        ET.ElementTree(ordered).write(translated, encoding="utf-8", xml_declaration=True)
        translated_label = f"{label}-{target_version}"
        traci.start([shutil.which("sumo"), "-n", str(translated), "--end", "1", "--no-step-log", "true"], label=translated_label, numRetries=3)
        connection = traci.getConnection(translated_label)
        try:
            actual = universe - set(connection.lane.getDisallowed("road_0"))
        finally:
            connection.close()
        assert actual == native


def test_service_passenger_addition_keeps_the_other_legacy_restrictions():
    lane = ET.fromstring('<lane id="service_0" disallow="passenger rail_urban"/>')
    assert _add_passenger_to_lane(lane, network_version="1.3")
    modes = set(lane.get("allow").split())
    assert "passenger" in modes
    assert not modes & {"rail_urban", "subway", "cable_car"}


@pytest.mark.skipif(not shutil.which("sumo") or not shutil.which("netconvert"), reason="SUMO required")
def test_replayed_permissions_use_the_receiver_format_without_replacing_its_header(tmp_path):
    from astra_sumo.core.junction_rebuild.restoration import _restore_replayed_geometry_attrs
    nodes, edges, source, target = [tmp_path / name for name in ("nodes.xml", "edges.xml", "source.net.xml", "target.net.xml")]
    nodes.write_text('<nodes><node id="w" x="0" y="0"/><node id="j" x="50" y="0"/>'
                     '<node id="e" x="100" y="0"/></nodes>', encoding="utf-8")
    edges.write_text('<edges><edge id="in" from="w" to="j"/><edge id="out" from="j" to="e"/></edges>', encoding="utf-8")
    result = subprocess.run([shutil.which("netconvert"), "-n", str(nodes), "-e", str(edges), "-o", str(source)],
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    tree = ET.parse(source)
    root = tree.getroot()
    root.set("version", "1.3")
    lane = root.find("edge[@function='internal']/lane")
    assert lane is not None
    lane.set("disallow", "rail_urban")
    lane_id = lane.get("id")
    tree.write(source, encoding="utf-8", xml_declaration=True)
    original = source.read_bytes()
    root.set("version", "1.20")
    tree.write(target, encoding="utf-8", xml_declaration=True)
    restored = _restore_replayed_geometry_attrs(source_file=source, target_file=target, junction_id="j")
    assert restored["status"] == "pass", restored
    assert ET.parse(target).getroot().get("version") == "1.20"
    assert source.read_bytes() == original
    label = f"replayed-version-{tmp_path.name}"
    traci.start([shutil.which("sumo"), "-n", str(target), "--end", "1", "--no-step-log", "true"], label=label, numRetries=3)
    connection = traci.getConnection(label)
    try:
        denied = set(connection.lane.getDisallowed(lane_id))
    finally:
        connection.close()
    assert {"rail_urban", "subway", "cable_car"} <= denied
