from copy import deepcopy
import xml.etree.ElementTree as ET

from astra_sumo.core.internal_ownership import resolve_internal_ownership
from astra_sumo.core.source_movement_support import _index
from astra_sumo.core.local_network_conversion import local_conversion_input
from astra_sumo.core.junction_rebuild.restoration import _restore_non_target_internal_artifacts


def _network():
    return ET.fromstring('''<net><location netOffset="0,0" projParameter="!"/>
      <edge id="in" from="W" to="A"><lane id="in_0" index="0"/></edge>
      <edge id="out" from="A" to="E"><lane id="out_0" index="0"/></edge>
      <edge id="remote_in" from="R" to="A_10"><lane id="remote_in_0" index="0"/></edge>
      <edge id="remote_out" from="A_10" to="S"><lane id="remote_out_0" index="0"/></edge>
      <edge id=":A_10" function="internal"><lane id=":A_10_0" index="0" speed="11"/></edge>
      <edge id=":A_20" function="internal"><lane id=":A_20_0" index="0" speed="11"/></edge>
      <edge id=":A_10_0" function="internal"><lane id=":A_10_0_0" index="0" speed="12"/></edge>
      <junction id="W"/><junction id="E"/><junction id="R"/><junction id="S"/>
      <junction id="A" type="priority" incLanes="in_0" intLanes=":A_20_0"/>
      <junction id="A_10" type="priority" incLanes="remote_in_0" intLanes=":A_10_0_0"/>
      <junction id=":A_10_0" type="internal" x="1" incLanes=":A_10_0" intLanes=":A_20_0"/>
      <connection from="in" to="out" fromLane="0" toLane="0" via=":A_10_0"/>
      <connection from=":A_10" to="out" fromLane="0" toLane="0" via=":A_20_0"/>
      <connection from=":A_20" to="out" fromLane="0" toLane="0"/>
      <connection from="remote_in" to="remote_out" fromLane="0" toLane="0" via=":A_10_0_0"/>
      <connection from=":A_10_0" to="remote_out" fromLane="0" toLane="0"/></net>''')


def test_reference_ownership_separates_colliding_edge_lane_and_junction_names():
    root = _network()
    owners = resolve_internal_ownership(root)
    assert owners["lane"][":A_10_0"] == "A"
    assert owners["edge"][":A_10_0"] == "A_10"
    assert owners["junction"][":A_10_0"] == "A"
    assert owners["lane"][":A_20_0"] == "A"
    assert _index(root)["owners"][":A_10_0"] == "A"
    subset, record = local_conversion_input(root, {"A"}, {"in", "out"})
    assert record["status"] == "local"
    assert subset.find("edge[@id=':A_10']") is not None
    assert subset.find("edge[@id=':A_10_0']") is None
    assert subset.find("junction[@id=':A_10_0']") is not None


def test_reference_ownership_preserves_the_edited_internal_node_and_restores_the_other_edge(tmp_path):
    root = _network()
    source, target = tmp_path / "source.net.xml", tmp_path / "candidate.net.xml"
    ET.ElementTree(root).write(source, encoding="utf-8")
    candidate = deepcopy(root)
    candidate.find("junction[@id=':A_10_0']").set("x", "99")
    candidate.find("edge[@id=':A_10_0']/lane").set("speed", "99")
    ET.ElementTree(candidate).write(target, encoding="utf-8")
    result = _restore_non_target_internal_artifacts(source_file=source, target_file=target, exclude_junction_ids={"A"})
    assert result["status"] == "pass"
    restored = ET.parse(target).getroot()
    assert restored.find("junction[@id=':A_10_0']").get("x") == "99"
    assert restored.find("edge[@id=':A_10_0']/lane").get("speed") == "12"
    assert len(restored.findall("junction[@id=':A_10_0']")) == 1


def test_ambiguous_names_without_topological_references_remain_unknown():
    root = ET.fromstring('''<net><junction id="A"/><junction id="A_10"/>
      <edge id=":A_10" function="internal"><lane id=":A_10_0"/></edge></net>''')
    assert resolve_internal_ownership(root)["lane"][":A_10_0"] is None
