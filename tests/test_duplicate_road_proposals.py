from copy import deepcopy
import xml.etree.ElementTree as ET
import shutil

import pytest

from astra_sumo.core.corridor_edit_ledger import propose_corridor_edits
from astra_sumo.core.command_runner import run_command


@pytest.mark.parametrize(
    "difference",
    [None, "shape", "length", "lane_rule", "lane_parameter", "edge_priority", "connection", "same_connections"],
)
def test_duplicate_deletion_requires_equal_geometry_rules_and_movements(tmp_path, difference):
    root = ET.fromstring(
        '<net><junction id="a" type="priority"/><junction id="b" type="priority"/>'
        '<junction id="q" type="priority"/><junction id="c" type="priority"/>'
        '<edge id="first" from="a" to="b" priority="1" type="residential"><lane id="first_0" index="0" '
        'allow="passenger" speed="10" width="3.2" length="10" shape="0,0,0 10,0,0"/></edge></net>'
    )
    for name, start, end in (("in", "q", "a"), ("out", "b", "c")):
        edge = ET.SubElement(root, "edge", id=name, attrib={"from": start, "to": end})
        ET.SubElement(
            edge, "lane", id=f"{name}_0", index="0", allow="passenger", speed="10", length="10", shape="0,0 10,0"
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


@pytest.mark.skipif(not shutil.which("netconvert"), reason="netconvert required")
@pytest.mark.parametrize("difference", [None, "internal_shape", "internal_permission", "unresolved"])
def test_native_duplicate_ignores_generated_ids_but_keeps_actual_internal_paths(tmp_path, difference):
    nodes, edges, turns = [tmp_path / name for name in ("nodes.xml", "edges.xml", "connections.xml")]
    nodes.write_text(
        '<nodes><node id="inlet" x="-200" y="0"/><node id="w" x="-100" y="0"/><node id="j" x="0" y="0" type="priority"/>'
        '<node id="e" x="100" y="0"/></nodes>',
        encoding="utf-8",
    )
    edges.write_text(
        '<edges><edge id="in" from="inlet" to="w" allow="passenger" speed="10"/>'
        '<edge id="first" from="w" to="j" allow="passenger" speed="10"/>'
        '<edge id="second" from="w" to="j" allow="passenger" speed="10"/>'
        '<edge id="out" from="j" to="e" allow="passenger" speed="10"/></edges>',
        encoding="utf-8",
    )
    turns.write_text(
        '<connections><connection from="in" to="first" fromLane="0" toLane="0"/>'
        '<connection from="in" to="second" fromLane="0" toLane="0"/>'
        '<connection from="first" to="out" fromLane="0" toLane="0"/>'
        '<connection from="second" to="out" fromLane="0" toLane="0"/></connections>',
        encoding="utf-8",
    )
    source = tmp_path / "native.net.xml"
    result = run_command(
        [
            shutil.which("netconvert"),
            "-n",
            str(nodes),
            "-e",
            str(edges),
            "-x",
            str(turns),
            "--offset.disable-normalization",
            "true",
            "-o",
            str(source),
        ],
        cwd=tmp_path,
        timeout_seconds=30,
    )
    assert result.returncode == 0, result
    tree = ET.parse(source)
    root = tree.getroot()
    movement = root.find("connection[@from='second']")
    first = root.find("connection[@from='first']")
    assert first.get("via") != movement.get("via")
    assert first.get("state") != movement.get("state")
    lane = root.find(f"edge/lane[@id='{movement.get('via')}']")
    if difference == "internal_shape":
        lane.set("shape", "-1.5,-1.6 0,1 1.5,-1.6")
    elif difference == "internal_permission":
        lane.set("allow", "bus")
    elif difference == "unresolved":
        movement.set("via", ":missing_0")
    tree.write(source, encoding="utf-8")
    original = source.read_bytes()
    proposals = propose_corridor_edits(source, include_isolated=False, include_micro_merges=False)
    assert len(proposals) == (1 if difference is None else 0), proposals
    assert source.read_bytes() == original
