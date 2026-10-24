import xml.etree.ElementTree as ET
import gzip
import json
from pathlib import Path
import pytest

from astra_sumo.core.hamburg_junctions.workflow import _remap_lane_origins


def test_sequential_lane_edits_retain_source_identity_and_drop_removed_bindings(tmp_path):
    def network(name, count):
        path = tmp_path / name
        root = ET.Element("net")
        edge = ET.SubElement(root, "edge", id="road")
        for index in range(count):
            ET.SubElement(edge, "lane", id=f"road_{index}", index=str(index))
        ET.ElementTree(root).write(path, encoding="utf-8")
        return path

    before, inserted, removed = network("before.xml", 2), network("inserted.xml", 3), network("removed.xml", 2)
    origins = {"road_0": "source_0", "road_1": "source_1", "other_0": None}
    constructed = {("site", "0"): {"official": ("road", 1)}}
    plan = {"operation": {"params": {"edge_id": "road"}}, "lane_index_mapping": {"0": 0, "1": 2}}
    assert _remap_lane_origins(before, inserted, plan, origins, constructed) == "road"
    assert origins == {"road_0": "source_0", "road_1": None, "road_2": "source_1", "other_0": None}
    assert constructed[("site", "0")]["official"] == ("road", 2)
    plan["lane_index_mapping"] = {"0": 0, "1": 1, "2": None}
    _remap_lane_origins(inserted, removed, plan, origins, constructed)
    assert origins == {"road_0": "source_0", "road_1": None, "other_0": None}
    assert constructed[("site", "0")] == {}


def test_ring1_added_motor_lane_inherits_bus_access_from_its_source(tmp_path):
    from astra_sumo.core.network_edit_operations import write_scoped_edit_plan
    from astra_sumo.core.osm_access import _permission_set

    root = Path(__file__).parents[1]
    source = tmp_path / "source.net.xml"
    source.write_bytes(gzip.decompress((root / "benchmarks/hamburg_ring1_road_scope_20261020_v1/lane-count-input.net.xml.gz").read_bytes()))
    operation = json.loads((root / "examples/06_hamburg_ring1_demo/junction-edits.json").read_text(encoding="utf-8"))["operations"][0]
    (tmp_path / "edit").mkdir()
    plan = write_scoped_edit_plan(root=ET.parse(source).getroot(), operation=operation, source_file=source,
        candidate_file=tmp_path / "candidate.net.xml", output_dir=tmp_path / "edit", prefix="lane",
        netconvert_binary="netconvert", mode="explore")
    original = ET.parse(source).getroot().find("edge[@id='37413814#0']").findall("lane")[plan["lane_template_index"]]
    patch = ET.parse(plan["patches"]["edges"]["path"]).getroot().find("edge[@id='37413814#0']").findall("lane")[-1]
    assert {"passenger", "bus"} <= _permission_set(patch.attrib)
    assert _permission_set(patch.attrib) == _permission_set(original.attrib, network_version="1.20")


def test_sumo_content_hash_ignores_presentation_and_keeps_model_values(tmp_path):
    from astra_sumo.core.hamburg_junctions.workflow import sumo_xml_content_sha256

    first, second, changed = (tmp_path / name for name in ("first.xml", "second.xml", "changed.xml"))
    first.write_text('<!--generated at Windows path--><net>\n <lane id="road_0" speed="10"/>\n</net>', encoding="utf-8")
    second.write_text('<!--generated at Linux path--><net><lane speed="10" id="road_0"/></net>', encoding="utf-8")
    changed.write_text('<net><lane speed="11" id="road_0"/></net>', encoding="utf-8")
    assert sumo_xml_content_sha256(first) == sumo_xml_content_sha256(second)
    assert sumo_xml_content_sha256(first) != sumo_xml_content_sha256(changed)


@pytest.mark.parametrize("protected", [True, False])
def test_connection_exclusions_protect_official_pairs_and_nonmotor_access(tmp_path, protected):
    from astra_sumo.core.hamburg_junctions.workflow import _apply_connection_edits

    source = tmp_path / "source.xml"
    source.write_text('''<net><edge id="in" from="a" to="j"><lane id="in_0" index="0" allow="passenger bicycle"/></edge>
      <edge id="out" from="j" to="b"><lane id="out_0" index="0" allow="passenger bicycle"/></edge>
      <connection from="in" fromLane="0" to="out" toLane="0"/></net>''', encoding="utf-8")
    before = source.read_bytes()
    pair = ["in", 0, "out", 0]
    audit = {"boundary_connections": [{"sumo_connection": pair}] if protected else [], "unexplained_extra": [pair]}
    operation = {"operation": "delete_lane_connection", "params": dict(zip(("from", "fromLane", "to", "toLane"), pair))}
    with pytest.raises(ValueError, match="official boundary inventory" if protected else "nonmotor access"):
        _apply_connection_edits(source, [operation], original_net=source, audit=audit, groups=[], origins={},
            changed_edges=[], output=tmp_path, netconvert="unneeded", timeout=30)
    assert source.read_bytes() == before
    assert not (tmp_path / "connection-edit-0").exists()
