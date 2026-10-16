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


def test_prohibition_inputs_keep_complete_references_and_fall_back_for_unresolved_references():
    root = ET.fromstring('''<net><location netOffset="0,0" projParameter="!"/>
      <edge id="in" from="W" to="A"/><edge id="out" from="A" to="E"/>
      <edge id="other" from="N" to="A"/><edge id="far_in" from="X" to="R"/>
      <edge id="far_out" from="R" to="Y"/>
      <junction id="W"/><junction id="A"/><junction id="E"/><junction id="N"/>
      <junction id="X"/><junction id="R"/><junction id="Y"/>
      <prohibition prohibitor="other->out" prohibited="in->out"/>
      <prohibition prohibitor="far_in->far_out" prohibited="far_in->far_out"/></net>''')
    subset, _ = local_conversion_input(root, {"A"}, {"in", "out", "other"})
    assert [row.get("prohibitor") for row in subset.findall("prohibition")] == ["other->out"]
    root.find("prohibition").set("prohibitor", "missing->out")
    subset, record = local_conversion_input(root, {"A"}, {"in", "out", "other"})
    assert subset is None
    assert record["reason"] == "unresolved_prohibition_references"


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


def _native_case(tmp_path, operation=None, *, prohibition=False):
    if not shutil.which("netconvert") or not shutil.which("sumo"):
        pytest.skip("SUMO required")
    source = _source(tmp_path)
    if prohibition:
        tree = ET.parse(source)
        ET.SubElement(tree.getroot(), "prohibition", prohibitor="north->middle", prohibited="in->middle")
        tree.write(source, encoding="utf-8", xml_declaration=True)
    output = tmp_path / "local"
    output.mkdir()
    candidate = output / "candidate.net.xml"
    operation = operation or {"id": "width", "operation": "set_lane_width", "status": "candidate",
                             "params": {"lane_id": "middle_0", "width_m": 3.2 + .1}}
    plan = write_scoped_edit_plan(root=ET.parse(source).getroot(), operation=operation, source_file=source,
        candidate_file=candidate, output_dir=output, prefix="test", netconvert_binary=shutil.which("netconvert"), mode="explore")
    result = run_command(plan["command"], cwd=output, timeout_seconds=30)
    assert result.returncode == 0, result.stderr
    return source, candidate, plan


@pytest.fixture
def local_edit(tmp_path, request):
    source, candidate, plan = _native_case(tmp_path, prohibition=getattr(request, "param", False))
    output = candidate.parent
    checked = check_scoped_edit(plan=plan, candidate_file=candidate, output_dir=output,
        netconvert_binary=shutil.which("netconvert"), timeout_seconds=30, command_runner=run_command, mode="explore")
    assert checked["status"] == "measured", checked
    return source, candidate, plan, checked


def test_local_raw_remote_recut_is_corrected_before_a_candidate_is_accepted(tmp_path):
    source, candidate, plan = _native_case(tmp_path)
    tree = ET.parse(candidate)
    lane = tree.getroot().find("edge[@id='in']/lane")
    points = lane.get("shape").split()
    coordinates = list(map(float, points[0].split(",")))
    coordinates[0] += 25
    points[0] = ",".join(map(str, coordinates))
    lane.set("shape", " ".join(points))
    tree.write(candidate, encoding="utf-8", xml_declaration=True)
    checked = check_scoped_edit(plan=plan, candidate_file=candidate, output_dir=candidate.parent,
        netconvert_binary=shutil.which("netconvert"), timeout_seconds=30, command_runner=run_command, mode="explore")
    assert checked["geometry_preservation"]["status"] == "pass"
    original = ET.parse(source).getroot().find("edge[@id='in']/lane").get("shape").split()[0]
    restored = ET.parse(candidate).getroot().find("edge[@id='in']/lane").get("shape").split()[0]
    raw = ET.parse(checked["local_conversion"]["merged_proposal"]["path"]).getroot().find("edge[@id='in']/lane").get("shape").split()[0]
    assert restored == original
    assert raw != original


@pytest.mark.parametrize("kind", ["set_lane_width", "set_lane_count"])
def test_native_opposite_lane_references_survive_width_and_right_lane_insertion(tmp_path, kind):
    if not shutil.which("netconvert") or not shutil.which("sumo"):
        pytest.skip("SUMO required")
    source = _source(tmp_path)
    tree = ET.parse(source)
    for name, other in (("middle", "reverse"), ("reverse", "middle")):
        ET.SubElement(tree.getroot().find(f"edge[@id='{name}']/lane"), "neigh", lane=f"{other}_0")
    tree.write(source, encoding="utf-8", xml_declaration=True)
    output = tmp_path / "opposites"
    output.mkdir()
    candidate = output / "candidate.net.xml"
    params = {"lane_id": "middle_0", "width_m": 3.4} if kind == "set_lane_width" else {
        "edge_id": "middle", "num_lanes": 2, "add_side": "right"}
    plan = write_scoped_edit_plan(root=ET.parse(source).getroot(), operation={"id": "opposite", "operation": kind, "params": params},
        source_file=source, candidate_file=candidate, output_dir=output, prefix="test", netconvert_binary=shutil.which("netconvert"), mode="explore")
    result = run_command(plan["command"], cwd=output, timeout_seconds=30)
    assert result.returncode == 0, result.stderr
    checked = check_scoped_edit(plan=plan, candidate_file=candidate, output_dir=output,
        netconvert_binary=shutil.which("netconvert"), timeout_seconds=30, command_runner=run_command, mode="explore")
    assert checked["status"] == "measured", checked
    root = ET.parse(candidate).getroot()
    index = 0 if kind == "set_lane_width" else 1
    assert root.find(f"edge/lane[@id='middle_{index}']/neigh").get("lane") == "reverse_0"
    assert root.find("edge/lane[@id='reverse_0']/neigh").get("lane") == f"middle_{index}"


@pytest.mark.parametrize("local_edit", [False, True], indirect=True)
def test_native_local_conversion_keeps_remote_roads_and_matches_the_full_proposal(tmp_path, local_edit):
    source, candidate, plan, checked = local_edit
    local = checked["local_conversion"]
    assert plan["junction_link_detail"] == 5
    assert local["merged_proposal"]["stage"] == "before_exterior_restoration"
    assert local["converted_normal_edge_count"] == 8
    assert local["source_normal_edge_count"] == 48
    subset = ET.parse(local["input"]["path"]).getroot()
    assert len(subset.findall("prohibition")) == len(ET.parse(source).getroot().findall("prohibition"))
    assert subset.find("tlLogic[@id='R10']") is None
    assert checked["geometry_preservation"]["status"] == "pass"
    assert validate_local_conversion(local, source) == Path(local["input"]["path"])
    before, after = ET.parse(source).getroot(), ET.parse(candidate).getroot()
    if before.find("prohibition") is not None:
        assert [row.attrib for row in before.find("junction[@id='A']").findall("request")] != [
            row.attrib for row in after.find("junction[@id='A']").findall("request")]
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
