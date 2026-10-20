from pathlib import Path
import shutil
import xml.etree.ElementTree as ET

import pytest

from astra_sumo.core.corridor_simplification import (
    audit_alias_normalized_connections,
    find_removable_corridor_geometry_nodes,
    build_corridor_geometry_simplification_variant,
)


def _write_net(path: Path, *, second_name: str = "Ringstrasse") -> None:
    path.write_text(
        f"""<net>
  <junction id="west" type="priority"/>
  <junction id="micro" type="priority"/>
  <junction id="east" type="priority"/>
  <edge id="f0" from="west" to="micro" type="highway.secondary"><lane id="f0_0" index="0" speed="13.9" length="0.2"/><param key="name" value="Ringstrasse"/></edge>
  <edge id="f1" from="micro" to="east" type="highway.secondary"><lane id="f1_0" index="0" speed="13.9" length="20"/><param key="name" value="{second_name}"/></edge>
  <edge id="r0" from="east" to="micro" type="highway.secondary"><lane id="r0_0" index="0" speed="13.9" length="20"/><param key="name" value="{second_name}"/></edge>
  <edge id="r1" from="micro" to="west" type="highway.secondary"><lane id="r1_0" index="0" speed="13.9" length="0.2"/><param key="name" value="Ringstrasse"/></edge>
  <connection from="f0" to="f1" fromLane="0" toLane="0" dir="s"/>
  <connection from="r0" to="r1" fromLane="0" toLane="0" dir="s"/>
</net>""",
        encoding="utf-8",
    )


def test_corridor_detector_accepts_reference_absent_micro_geometry_node(tmp_path: Path) -> None:
    candidate = tmp_path / "candidate.net.xml"
    reference = tmp_path / "reference.net.xml"
    _write_net(candidate)
    reference.write_text("<net><junction id='west'/><junction id='east'/></net>", encoding="utf-8")

    candidates = find_removable_corridor_geometry_nodes(candidate, reference_net_file=reference)

    assert [candidate["node_id"] for candidate in candidates] == ["micro"]
    assert candidates[0]["minimum_incident_edge_length_m"] == 0.2
    assert candidates[0]["proof"] == "same_corridor_same_semantics_lane_preserving_micro_segment"


def test_corridor_detector_rejects_different_road_names(tmp_path: Path) -> None:
    candidate = tmp_path / "candidate.net.xml"
    _write_net(candidate, second_name="Other Road")

    assert find_removable_corridor_geometry_nodes(candidate) == []


def test_corridor_detector_rejects_controlled_local_connection(tmp_path: Path) -> None:
    candidate = tmp_path / "candidate.net.xml"
    _write_net(candidate)
    text = candidate.read_text(encoding="utf-8").replace(
        'from="f0" to="f1" fromLane="0" toLane="0" dir="s"',
        'from="f0" to="f1" fromLane="0" toLane="0" dir="s" tl="tls" linkIndex="0"',
    )
    candidate.write_text(text, encoding="utf-8")

    assert find_removable_corridor_geometry_nodes(candidate) == []


def test_corridor_detector_rejects_node_with_pedestrian_internal_ring(tmp_path: Path) -> None:
    candidate = tmp_path / "candidate.net.xml"
    _write_net(candidate)
    text = candidate.read_text(encoding="utf-8").replace(
        "</net>",
        '<edge id=":micro_c0" function="crossing"><lane id=":micro_c0_0" index="0"/></edge></net>',
    )
    candidate.write_text(text, encoding="utf-8")

    assert find_removable_corridor_geometry_nodes(candidate) == []


def test_corridor_detector_rejects_edge_used_by_neighbor_crossing(tmp_path: Path) -> None:
    candidate = tmp_path / "candidate.net.xml"
    _write_net(candidate)
    text = candidate.read_text(encoding="utf-8").replace(
        "</net>",
        '<edge id=":other_c0" function="crossing" crossingEdges="f0 r1"><lane id=":other_c0_0" index="0"/></edge></net>',
    )
    candidate.write_text(text, encoding="utf-8")

    assert find_removable_corridor_geometry_nodes(candidate) == []


def test_alias_normalized_connection_audit_preserves_boundary_movement(tmp_path: Path) -> None:
    source = tmp_path / "source.net.xml"
    candidate = tmp_path / "candidate.net.xml"
    source.write_text(
        """<net>
  <edge id="a" from="west" to="micro" type="highway.secondary"><lane id="a_0" index="0"/></edge>
  <edge id="b" from="micro" to="east" type="highway.secondary"><lane id="b_0" index="0"/></edge>
  <edge id="out" from="east" to="far" type="highway.secondary"><lane id="out_0" index="0"/></edge>
  <connection from="a" to="b" fromLane="0" toLane="0" dir="s"/>
  <connection from="b" to="out" fromLane="0" toLane="0" dir="s" tl="tls" linkIndex="2"/>
</net>""",
        encoding="utf-8",
    )
    candidate.write_text(
        """<net>
  <edge id="a" from="west" to="east" type="highway.secondary"><lane id="a_0" index="0"/><param key="removedNodeIds" value="micro"/></edge>
  <edge id="out" from="east" to="far" type="highway.secondary"><lane id="out_0" index="0"/></edge>
  <connection from="a" to="out" fromLane="0" toLane="0" dir="s" tl="tls" linkIndex="2"/>
</net>""",
        encoding="utf-8",
    )

    report = audit_alias_normalized_connections(source, candidate)

    assert report["status"] == "pass"
    assert report["edge_aliases"] == {"a": "a", "b": "a"}
    assert report["collapsed_source_connection_count"] == 1
    assert report["normal_missing_count"] == 0
    assert report["normal_extra_count"] == 0
    assert report["controlled_missing_count"] == 0


@pytest.mark.parametrize("variant", ["unchanged", "merged", "changed_access"])
def test_standalone_simplification_requires_realized_and_access_preserving_merges(tmp_path, variant):
    source = tmp_path / "source.net.xml"
    _write_net(source)
    if variant == "changed_access":
        tree = ET.parse(source)
        for lane in tree.getroot().findall("edge/lane"):
            lane.set("allow", "bus")
        tree.write(source, encoding="utf-8")
    original = source.read_bytes()

    def runner(command, **kwargs):
        output = Path(command[command.index("--output-file") + 1])
        if variant == "unchanged":
            shutil.copyfile(source, output)
        else:
            root = ET.parse(source).getroot()
            root.remove(root.find("junction[@id='micro']"))
            for first, second, end in (("f0", "f1", "east"), ("r0", "r1", "west")):
                root.remove(root.find(f"edge[@id='{second}']"))
                edge = root.find(f"edge[@id='{first}']")
                edge.set("to", end)
                ET.SubElement(edge, "param", key="removedNodeIds", value="micro")
                if variant == "changed_access":
                    edge.find("lane").set("allow", "passenger bus")
            for connection in list(root.findall("connection")):
                root.remove(connection)
            ET.ElementTree(root).write(output, encoding="utf-8")
        return {"status": "pass", "returncode": 0}

    result = build_corridor_geometry_simplification_variant(
        net_file=source, output_dir=tmp_path / "candidate", command_runner=runner
    )
    assert result["status"] == ("pass" if variant == "merged" else "fail")
    assert result["requested_merge_realized"] is (variant != "unchanged")
    if variant == "unchanged":
        assert result["missed_candidate_node_ids"] == ["micro"]
    elif variant == "changed_access":
        assert result["alias_normalized_connection_audit"]["merged_road_access_preservation"]["status"] == "fail"
    assert source.read_bytes() == original


@pytest.mark.parametrize("bad_prefix", [False, True])
def test_standalone_plan_outputs_do_not_replace_a_source(tmp_path, bad_prefix):
    source = tmp_path / "corridor_geometry_simplification_plan.json"
    _write_net(source)
    original = source.read_bytes()
    result = build_corridor_geometry_simplification_variant(
        net_file=source,
        output_dir=tmp_path / "new" if bad_prefix else tmp_path,
        prefix="../corridor_geometry_simplification" if bad_prefix else "corridor_geometry_simplification",
    )
    assert result["status"] == "fail"
    assert source.read_bytes() == original
