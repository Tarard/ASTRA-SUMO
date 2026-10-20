import xml.etree.ElementTree as ET
import gzip
import json
from pathlib import Path

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
