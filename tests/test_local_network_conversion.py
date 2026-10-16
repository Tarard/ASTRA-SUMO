from collections import Counter
from copy import deepcopy
import json
from pathlib import Path
import shutil
import subprocess
import xml.etree.ElementTree as ET

import pytest

from astra_sumo.core.candidate_contracts import file_sha256
from astra_sumo.core.command_runner import run_command
from astra_sumo.core.local_network_conversion import local_conversion_input, validate_local_conversion
from astra_sumo.core.network_edit_operations import write_scoped_edit_plan, check_scoped_edit


def test_local_context_closes_roundabouts_and_retains_source_immutability():
    root = ET.fromstring('''<net><location netOffset="0,0" projParameter="!"/>
      <edge id="a" from="A" to="B"><lane id="a_0"/></edge>
      <edge id="b" from="B" to="C"><lane id="b_0"/></edge>
      <edge id="c" from="C" to="A"><lane id="c_0"/></edge>
      <junction id="A"/><junction id="B"/><junction id="C"/>
      <connection from="a" to="b"/><connection from="b" to="c"/><connection from="c" to="a"/>
      <roundabout nodes="A B C" edges="a b c"/></net>''')
    before = ET.tostring(root)
    subset, record = local_conversion_input(root, {"A"}, {"a", "c"})
    assert record["context_edge_ids"] == ["a", "b", "c"]
    assert subset.find("roundabout").get("edges") == "a b c"
    assert ET.tostring(root) == before
    for connection in root.findall("connection"):
        connection.set("tl", "shared")
    subset, record = local_conversion_input(root, {"A"}, {"a", "c"})
    assert subset is None
    assert record == {"status": "full_network", "reason": "controller_spans_edit_boundary", "controller_ids": ["shared"]}


def test_missing_frame_retains_the_full_native_path():
    subset, record = local_conversion_input(ET.fromstring("<net/>"), set(), set())
    assert subset is None
    assert record == {"status": "full_network", "reason": "coordinate_frame_not_declared"}


def _source(tmp_path):
    nodes, edges, source = [tmp_path / name for name in ("nodes.xml", "edges.xml", "source.net.xml")]
    node_root, edge_root = ET.Element("nodes"), ET.Element("edges")
    for name, x, y in (("W", -100, 0), ("A", 0, 0), ("B", 50, 0), ("E", 150, 0), ("N", 0, 100), ("S", 50, -100)):
        ET.SubElement(node_root, "node", id=name, x=str(x), y=str(y))
    for name, start, end in (("in", "W", "A"), ("middle", "A", "B"), ("out", "B", "E"), ("east", "E", "B"),
                             ("reverse", "B", "A"), ("west", "A", "W"), ("north", "N", "A"), ("south", "B", "S")):
        ET.SubElement(edge_root, "edge", id=name, **{"from": start, "to": end})
    for i in range(21):
        ET.SubElement(node_root, "node", id=f"R{i}", x=str(500 + i * 30), y="400",
                      **({"type": "traffic_light"} if i == 10 else {}))
        if i:
            ET.SubElement(edge_root, "edge", id=f"far{i}", **{"from": f"R{i-1}", "to": f"R{i}"})
            ET.SubElement(edge_root, "edge", id=f"back{i}", **{"from": f"R{i}", "to": f"R{i-1}"})
    ET.ElementTree(node_root).write(nodes)
    ET.ElementTree(edge_root).write(edges)
    result = subprocess.run([shutil.which("netconvert"), "-n", str(nodes), "-e", str(edges),
        "--offset.disable-normalization", "true", "-o", str(source)], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    return source


@pytest.fixture
def local_edit(tmp_path):
    if not shutil.which("netconvert") or not shutil.which("sumo"):
        pytest.skip("SUMO required")
    source = _source(tmp_path)
    output = tmp_path / "local"
    output.mkdir()
    candidate = output / "candidate.net.xml"
    operation = {"id": "width", "operation": "set_lane_width", "status": "candidate",
                 "params": {"lane_id": "middle_0", "width_m": 3.2 + .1}}
    plan = write_scoped_edit_plan(root=ET.parse(source).getroot(), operation=operation, source_file=source,
        candidate_file=candidate, output_dir=output, prefix="test", netconvert_binary=shutil.which("netconvert"), mode="explore")
    result = run_command(plan["command"], cwd=output, timeout_seconds=30)
    assert result.returncode == 0, result.stderr
    checked = check_scoped_edit(plan=plan, candidate_file=candidate, output_dir=output,
        netconvert_binary=shutil.which("netconvert"), timeout_seconds=30, command_runner=run_command, mode="explore")
    assert checked["status"] == "measured", checked
    return source, candidate, plan, checked


def test_native_local_conversion_keeps_remote_roads_and_matches_the_full_proposal(tmp_path, local_edit):
    source, candidate, plan, checked = local_edit
    local = checked["local_conversion"]
    assert local["converted_normal_edge_count"] == 8
    assert local["source_normal_edge_count"] == 48
    subset = ET.parse(local["input"]["path"]).getroot()
    assert subset.find("tlLogic[@id='R10']") is None
    assert checked["geometry_preservation"]["status"] == "pass"
    assert validate_local_conversion(local, source) == Path(local["input"]["path"])
    before, after = ET.parse(source).getroot(), ET.parse(candidate).getroot()
    for name in ("far1", "far10", "back20"):
        assert ET.tostring(before.find(f"edge[@id='{name}']")) == ET.tostring(after.find(f"edge[@id='{name}']"))
    assert ET.canonicalize(ET.tostring(before.find("tlLogic[@id='R10']"), encoding="unicode"), strip_text=True) == (
        ET.canonicalize(ET.tostring(after.find("tlLogic[@id='R10']"), encoding="unicode"), strip_text=True))

    output = tmp_path / "full"
    output.mkdir()
    full_candidate = output / "candidate.net.xml"
    command = list(plan["command"])
    command[command.index("--sumo-net-file") + 1] = str(source)
    command[command.index("--output-file") + 1] = str(full_candidate)
    result = run_command(command, cwd=output, timeout_seconds=30)
    assert result.returncode == 0, result.stderr
    full_plan = {key: value for key, value in plan.items() if key != "local_conversion"}
    checked = check_scoped_edit(plan=full_plan, candidate_file=full_candidate, output_dir=output,
        netconvert_binary=shutil.which("netconvert"), timeout_seconds=30, command_runner=run_command, mode="explore")
    assert checked["status"] == "measured"
    def elements(path):
        return Counter(ET.canonicalize(ET.tostring(row, encoding="unicode"), strip_text=True)
                       for row in ET.parse(path).getroot() if row.tag != "location")
    assert elements(candidate) == elements(full_candidate)


@pytest.mark.parametrize("artifact", ["input", "merged_proposal"])
def test_rehashed_forged_local_inputs_or_exterior_do_not_pass_source_reconstruction(tmp_path, local_edit, artifact):
    source, _, _, checked = local_edit
    record = deepcopy(checked["local_conversion"])
    tree = ET.parse(record[artifact]["path"])
    tree.getroot().find("edge/lane").set("speed", "999")
    forged = tmp_path / "forged.net.xml"
    tree.write(forged, encoding="utf-8", xml_declaration=True)
    record[artifact] = {"path": str(forged), "sha256": file_sha256(forged)}
    with pytest.raises(ValueError, match="differs"):
        validate_local_conversion(record, source)
    json.dumps(record)
