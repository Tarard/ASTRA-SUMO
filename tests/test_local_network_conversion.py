from collections import Counter
from copy import deepcopy
import json
from pathlib import Path
import shutil
import subprocess
import xml.etree.ElementTree as ET

import pytest
import traci

from astra_sumo.core.candidate_contracts import file_sha256
from astra_sumo.core.command_runner import run_command
from astra_sumo.core.local_network_conversion import local_conversion_input, validate_local_conversion, validate_join_boundary_conversion
from astra_sumo.core.network_edit_operations import write_scoped_edit_plan, check_scoped_edit


@pytest.mark.parametrize("candidate_length", ["999", "35.35114"])
def test_off_scope_restore_keeps_explicit_edge_length_and_its_precision(tmp_path, candidate_length):
    from astra_sumo.core.junction_rebuild.restoration import restore_off_scope_netconvert_artifacts
    source, candidate = tmp_path / "source.xml", tmp_path / "candidate.xml"
    root = ET.fromstring('''<net version="1.27"><location netOffset="0,0" projParameter="!" convBoundary="0,0,50,0"/>
      <edge id="road" from="a" to="b" length="35.351140000"><lane id="road_0" index="0" length="35.351140" shape="0,0 50,0"/></edge>
      <junction id="a" type="dead_end" x="0" y="0" incLanes="" intLanes="" shape="0,0"/>
      <junction id="b" type="dead_end" x="50" y="0" incLanes="road_0" intLanes="" shape="50,0"/></net>''')
    ET.ElementTree(root).write(source, encoding="utf-8", xml_declaration=True)
    changed = deepcopy(root)
    changed.find("edge").set("length", candidate_length)
    ET.ElementTree(changed).write(candidate, encoding="utf-8", xml_declaration=True)
    source_bytes = source.read_bytes()
    result = restore_off_scope_netconvert_artifacts(source_file=source, target_file=candidate,
        mutable_junction_ids=set(), mutable_edge_ids=set())
    assert result["status"] == "pass", result
    assert ET.parse(candidate).getroot().find("edge").get("length") == "35.351140000"
    assert result["restored_external_edge_ids"] == ["road"] and source.read_bytes() == source_bytes


@pytest.mark.parametrize("precision", [[], ["--precision", "2"], ["--precision=8"]])
def test_shared_geometry_precision_keeps_explicit_recipe_values_and_caller_command(precision):
    from astra_sumo.core.local_network_conversion import with_geometry_precision
    command = ["netconvert", "--sumo-net-file", "source.net.xml", *precision, "--output-file", "candidate.net.xml"]
    before = list(command)
    actual = with_geometry_precision(command)
    assert command == before and actual[-2:] == ["--output-file", "candidate.net.xml"]
    assert actual == (command if precision else [*command[:-2], "--precision", "6", *command[-2:]])


@pytest.mark.parametrize("output", [["--plain-output-prefix", "export"], ["-o", "candidate.net.xml"], []])
@pytest.mark.parametrize("precision", [[], ["--precision", "2"], ["--precision=8"]])
def test_shared_geometry_precision_supports_exports_short_options_and_partial_commands(output, precision):
    from astra_sumo.core.local_network_conversion import with_geometry_precision
    command = ["netconvert", "-s", "source.net.xml", *precision, *output]
    before = list(command)
    actual = with_geometry_precision(command)
    assert command == before
    assert actual == (command if precision else [command[0], "--precision", "6", *command[1:]])
    assert with_geometry_precision(actual) == actual


@pytest.mark.skipif(not shutil.which("netconvert"), reason="SUMO required")
@pytest.mark.parametrize("full_input", [False, True])
def test_native_geometry_conversion_retains_subcentimetre_contour_points(tmp_path, monkeypatch, full_input):
    from astra_sumo.core import local_network_conversion as local
    source = _source(tmp_path)
    patch = tmp_path / "precise.nod.xml"
    shape = "-3.003,-3.003 3.003,-3.003 3.003,3.003 -3.003,3.003"
    patch.write_text(f'<nodes><node id="A" shape="{shape}"/></nodes>', encoding="utf-8")
    if full_input:
        monkeypatch.setattr(local, "local_conversion_input", lambda *args, **kwargs: (None, {"status": "full_network"}))
    output = tmp_path / "precise.net.xml"
    root = ET.parse(source).getroot()
    edges = {edge.get("id") for edge in root.findall("edge") if edge.get("from") == "A" or edge.get("to") == "A"}
    before = file_sha256(source)
    result, record = local.run_scoped_native_conversion(command=[shutil.which("netconvert"), "--sumo-net-file", str(source),
        "--node-files", str(patch), "--offset.disable-normalization", "true", "--output-file", str(output)],
        mutable_nodes={"A"}, mutable_edges=edges, output_dir=tmp_path, prefix="precision", timeout_seconds=30)
    assert result.returncode == 0, result.stderr
    assert result.command[result.command.index("--precision") + 1] == "6"
    actual = ET.parse(record["native_output"]["path"]).getroot().find("junction[@id='A']").get("shape")
    points = [[float(value) for value in point.split(",")[:2]] for point in actual.split()]
    assert len(points) == 4 and all(abs(abs(value) - 3.003) < 1e-6 for point in points for value in point)
    assert file_sha256(source) == before


@pytest.mark.skipif(not shutil.which("netconvert"), reason="SUMO required")
@pytest.mark.parametrize("step", ["normalize", "export"])
def test_legacy_native_export_and_normalization_retain_supplied_contours(tmp_path, step):
    from astra_sumo.core.osm_workflow_tail import _normalize_sumo_net, export_plain_net_for_teacher_guided_repair
    source = _source(tmp_path)
    tree = ET.parse(source)
    node = tree.getroot().find("junction[@id='A']")
    node.set("shape", "-3.003,-3.003 3.003,-3.003 3.003,3.003 -3.003,3.003")
    node.set("customShape", "1")
    tree.write(source, encoding="utf-8", xml_declaration=True)
    before = file_sha256(source)
    folder = tmp_path / step
    folder.mkdir()
    if step == "export":
        report = export_plain_net_for_teacher_guided_repair(net_file=source, output_dir=folder, prefix="precise")
        output, tag, command = report["raw_node_file"], "node", report["netconvert"]["command"]
    else:
        output = folder / "normalized.net.xml"
        report = _normalize_sumo_net(net_file=source, output_file=output, output_dir=folder,
            netconvert_binary=shutil.which("netconvert"), timeout_seconds=30, command_runner=run_command)
        tag, command = "junction", report["command"]
    assert report["status"] == "pass", report
    assert command[command.index("--precision") + 1] == "6"
    shape = ET.parse(output).getroot().find(f"{tag}[@id='A']").get("shape")
    assert all(abs(abs(float(value)) - 3.003) < 1e-9 for point in shape.split() for value in point.split(","))
    assert file_sha256(source) == before


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


@pytest.mark.parametrize("duplicate_tag", [None, "junction", "tlLogic", "connection"])
def test_replacements_keep_road_order_and_native_request_order(duplicate_tag):
    from astra_sumo.core.local_network_conversion import merge_local_conversion
    source = ET.fromstring('''<net><location netOffset="0,0" projParameter="!"/>
      <edge id="a" from="A" to="B"/><edge id="middle" from="B" to="C"/>
      <edge id="z" from="X" to="Y"/>
      <tlLogic id="B" programID="0"/><tlLogic id="B" programID="1"/>
      <tlLogic id="Y" programID="0"/>
      <junction id="A"/><junction id="B"/><junction id="C"/><junction id="X"/><junction id="Y"/>
      <connection from="a" to="middle" fromLane="0" toLane="0" tl="B"/>
      <connection from="a" to="middle" fromLane="1" toLane="0" tl="B"/>
      <connection from="z" to="z" fromLane="0" toLane="0" tl="Y"/></net>''')
    endpoints = {"new": {"from": "B", "to": "C"}}
    subset, record = local_conversion_input(source, {"B"}, {"a", "middle", "new"}, edge_endpoints=endpoints)
    native = deepcopy(subset)
    native[:] = list(reversed(native[:]))
    native.find("edge[@id='a']").set("priority", "7")
    native.find("tlLogic[@programID='1']").set("offset", "13")
    ET.SubElement(native, "edge", id="new", attrib={"from": "B", "to": "C"})
    ET.SubElement(native, "connection", attrib={"from": "a", "to": "new", "fromLane": "0", "toLane": "0"})
    if duplicate_tag:
        row = native.find("junction[@id='B']") if duplicate_tag == "junction" else native.find(duplicate_tag)
        native.append(deepcopy(row))
        with pytest.raises(ValueError, match="unique .* identities"):
            merge_local_conversion(source, native, record)
        return
    merged = merge_local_conversion(source, native, record)
    assert [row.get("id") for row in merged.findall("edge")] == ["a", "middle", "z", "new"]
    assert [row.get("id") for row in merged.findall("junction")] == ["A", "B", "C", "X", "Y"]
    assert [(row.get("id"), row.get("programID")) for row in merged.findall("tlLogic")] == [("B", "0"), ("B", "1"), ("Y", "0")]
    assert [(row.get("to"), row.get("fromLane")) for row in merged.findall("connection")] == [
        ("z", "0"), ("middle", "1"), ("middle", "0"), ("new", "0")]
    assert merged.find("edge[@id='a']").get("priority") == "7"
    assert merged.find("tlLogic[@programID='1']").get("offset") == "13"
    assert source.find("edge[@id='a']").get("priority") is None


@pytest.mark.parametrize("fault", [None, "missing_created", "extra_road", "wrong_endpoint"])
def test_split_merge_requires_the_declared_road_inventory_and_endpoints(fault):
    from astra_sumo.core.local_network_conversion import merge_local_conversion
    source = ET.fromstring('''<net><location netOffset="0,0" projParameter="!"/>
      <edge id="road" from="A" to="B"><lane id="road_0"/></edge>
      <junction id="A"/><junction id="B"/></net>''')
    endpoints = {"road": {"from": "N", "to": "B"}, "upstream": {"from": "A", "to": "N"}}
    subset, record = local_conversion_input(source, {"A", "B", "N"}, {"road", "upstream"}, edge_endpoints=endpoints)
    assert record["converted_normal_edge_count"] == 1
    assert subset.find("edge[@id='upstream']") is None
    native = ET.fromstring('''<net><location netOffset="0,0" projParameter="!"/>
      <edge id="road" from="N" to="B"><lane id="road_0"/></edge>
      <edge id="upstream" from="A" to="N"><lane id="upstream_0"/></edge>
      <junction id="A"/><junction id="B"/><junction id="N"/></net>''')
    if fault == "missing_created":
        native.remove(native.find("edge[@id='upstream']"))
    elif fault == "extra_road":
        ET.SubElement(native, "edge", id="unplanned", attrib={"from": "A", "to": "B"})
    elif fault == "wrong_endpoint":
        native.find("edge[@id='road']").set("from", "A")
    if fault:
        with pytest.raises(ValueError, match="inventory|endpoint"):
            merge_local_conversion(source, native, record)
    else:
        result = merge_local_conversion(source, native, record)
        assert result.find("edge[@id='road']").get("from") == "N"
        assert result.find("edge[@id='upstream']").get("to") == "N"
        assert result.find("junction[@id='N']") is not None


def test_empty_local_context_keeps_the_full_native_input():
    root = ET.fromstring('<net><location netOffset="0,0" projParameter="!"/><junction id="isolated"/></net>')
    subset, record = local_conversion_input(root, {"isolated"}, set())
    assert subset is None
    assert record == {"status": "full_network", "reason": "no_incident_ordinary_roads"}


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


def test_join_replaces_affected_roundabout_membership_and_keeps_absorbed_prohibitions_on_full_conversion():
    from astra_sumo.core.local_network_conversion import merge_local_conversion
    root = ET.fromstring('''<net><location netOffset="0,0" projParameter="!"/>
      <edge id="a" from="A" to="B"/><edge id="b" from="B" to="C"/><edge id="c" from="C" to="A"/>
      <junction id="A"/><junction id="B"/><junction id="C"/>
      <roundabout nodes="A B C" edges="a b c"/></net>''')
    _, record = local_conversion_input(root, {"A", "B", "J"}, {"a", "b", "c"}, join_groups={"J": ["A", "B"]})
    native = ET.fromstring('''<net><location netOffset="0,0" projParameter="!"/>
      <edge id="b" from="J" to="C"/><edge id="c" from="C" to="J"/>
      <junction id="J"/><junction id="C"/><roundabout nodes="J C" edges="b c"/></net>''')
    merged = merge_local_conversion(root, native, record)
    assert merged.find("roundabout").get("edges") == "b c"
    assert merged.find("roundabout").get("nodes") == "J C"
    ET.SubElement(root, "prohibition", prohibitor="a->b", prohibited="c->a")
    subset, record = local_conversion_input(root, {"A", "B", "J"}, {"a", "b", "c"}, join_groups={"J": ["A", "B"]})
    assert subset is None and record["reason"] == "prohibition_references_absorbed_road"


def _source(tmp_path, *, signalized=False):
    nodes, edges, source = [tmp_path / name for name in ("nodes.xml", "edges.xml", "source.net.xml")]
    node_root, edge_root = ET.Element("nodes"), ET.Element("edges")
    for name, x, y in (("W", -100, 0), ("A", 0, 0), ("B", 50, 0), ("E", 150, 0), ("N", 0, 100), ("S", 50, -100)):
        ET.SubElement(node_root, "node", id=name, x=str(x), y=str(y), **({"type": "traffic_light"} if signalized and name == "A" else {}))
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
    source = _source(tmp_path, signalized=(operation or {}).get("params", {}).get("signal_policy") == "rebuild_diagnostic")
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


@pytest.mark.parametrize("alter_permissions", [False, True])
def test_legacy_lane_count_checks_inherited_lane_and_connection_permissions(tmp_path, alter_permissions):
    if not shutil.which("netconvert") or not shutil.which("sumo"):
        pytest.skip("SUMO required")
    from astra_sumo.core.osm_access import _permission_set
    source = _source(tmp_path)
    tree = ET.parse(source)
    root = tree.getroot()
    root.set("version", "1.3")
    root.find("edge[@id='middle']/lane").set("disallow", "rail_urban")
    root.find("connection[@from='in'][@to='middle']").set("disallow", "rail_urban")
    tree.write(source, encoding="utf-8", xml_declaration=True)
    output = tmp_path / "legacy-count"
    output.mkdir()
    candidate = output / "candidate.net.xml"
    plan = write_scoped_edit_plan(root=root, operation={"id": "count", "operation": "set_lane_count",
        "params": {"edge_id": "middle", "num_lanes": 2, "add_side": "right"}}, source_file=source,
        candidate_file=candidate, output_dir=output, prefix="test", netconvert_binary=shutil.which("netconvert"), mode="explore")
    result = run_command(plan["command"], cwd=output, timeout_seconds=30)
    assert result.returncode == 0, result.stderr
    if alter_permissions:
        changed = ET.parse(candidate)
        lane = changed.getroot().find("edge[@id='middle']/lane")
        lane.attrib.pop("disallow", None)
        lane.set("allow", "all")
        changed.write(candidate, encoding="utf-8", xml_declaration=True)
    checks = check_scoped_edit(plan=plan, candidate_file=candidate, output_dir=output,
        netconvert_binary=shutil.which("netconvert"), timeout_seconds=30, command_runner=run_command, mode="explore")
    assert checks["requested_effect_applied"], checks
    assert checks["construction_checks_passed"] is not alter_permissions, checks
    assert checks["release_checks_passed"] is not alter_permissions
    assert checks["new_lane_service"]["status"] == "pass"
    if alter_permissions:
        assert not checks["other_lane_permissions_preserved"]
        return
    actual = ET.parse(candidate).getroot()
    for lane in actual.find("edge[@id='middle']").findall("lane"):
        assert not _permission_set(lane.attrib, network_version=actual.get("version")) & {"rail_urban", "subway", "cable_car"}
    connection = actual.find("connection[@from='in'][@to='middle']")
    assert not _permission_set(connection.attrib, network_version=actual.get("version")) & {"rail_urban", "subway", "cable_car"}


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


@pytest.mark.parametrize("signal_policy", ["preserve", "rebuild_diagnostic"])
def test_join_uses_local_inputs_for_both_native_conversions(tmp_path, signal_policy):
    join_id = "joined"
    operation = {"id": "join", "operation": "join_junctions", "params": {
        "node_ids": ["A", "B"], "join_id": join_id, "signal_policy": signal_policy}}
    source, candidate, plan = _native_case(tmp_path, operation)
    assert plan["local_conversion"]["junction_aliases"] == {"A": join_id, "B": join_id}
    assert plan["local_conversion"]["absorbed_edge_ids"] == ["middle", "reverse"]
    checked = check_scoped_edit(plan=plan, candidate_file=candidate, output_dir=candidate.parent,
        netconvert_binary=shutil.which("netconvert"), timeout_seconds=30, command_runner=run_command, mode="explore")
    assert checked["release_checks_passed"], checked
    assert checked["local_conversion"]["converted_normal_edge_count"] == 8
    assert checked["local_conversion"]["source_normal_edge_count"] == 48
    second = checked["restoration"]["local_conversion"]
    assert second["status"] == "local"
    assert second["source_normal_edge_count"] == 46
    assert second["converted_normal_edge_count"] == 6
    assert validate_local_conversion(checked["local_conversion"], source).is_file()
    joined = checked["restoration"]["joined_network"]
    assert file_sha256(Path(joined["path"])) == joined["sha256"]
    assert validate_join_boundary_conversion(checked["restoration"], source) == Path(second["input"]["path"])
    forged = deepcopy(checked["restoration"])
    fake = tmp_path / "forged-boundary-input.net.xml"
    tree = ET.parse(second["input"]["path"])
    tree.getroot().find("edge/lane").set("speed", "999")
    tree.write(fake, encoding="utf-8", xml_declaration=True)
    forged["local_conversion"]["input"] = {"path": str(fake), "sha256": file_sha256(fake)}
    with pytest.raises(ValueError, match="source-derived subset"):
        validate_join_boundary_conversion(forged, source)
    forged = deepcopy(checked["restoration"])
    command = forged["commands"][0]["command"]
    command[command.index("--output-file") + 1] = str(candidate)
    with pytest.raises(ValueError, match="retained native output"):
        validate_join_boundary_conversion(forged, source)
    before, after = ET.parse(source).getroot(), ET.parse(candidate).getroot()
    assert after.find(f"junction[@id='{join_id}']") is not None
    assert all(after.find(f"junction[@id='{identity}']") is None for identity in {"A", "B"} - {join_id})
    for identity in ("far1", "far10", "back20"):
        assert ET.tostring(before.find(f"edge[@id='{identity}']")) == ET.tostring(after.find(f"edge[@id='{identity}']"))


@pytest.mark.parametrize("target", ["A", "legacy_control"])
def test_native_join_preconditions_reject_existing_node_or_controller_names(tmp_path, target):
    if not shutil.which("netconvert"):
        pytest.skip("SUMO required")
    from astra_sumo.core.network_edit_operations import validate_scoped_operation
    source = _source(tmp_path, signalized=True)
    root = ET.parse(source).getroot()
    root.find("tlLogic[@id='A']").set("id", "legacy_control")
    for connection in root.findall("connection"):
        if connection.get("tl") == "A":
            connection.set("tl", "legacy_control")
    operation = {"id": "join", "operation": "join_junctions", "params": {
        "node_ids": ["A", "B"], "join_id": target, "signal_policy": "rebuild_diagnostic"}}
    assert validate_scoped_operation(operation, root)


def test_shared_conversion_runs_multiple_join_groups_and_retains_the_remote_program(tmp_path):
    if not shutil.which("netconvert"):
        pytest.skip("SUMO required")
    from astra_sumo.core.local_network_conversion import run_scoped_native_conversion
    source = _source(tmp_path)
    before = file_sha256(source)
    nodes = tmp_path / "joins.xml"
    nodes.write_text('<nodes><join id="J_AB" nodes="A B" type="priority" reset="false"/>'
                     '<join id="J_remote" nodes="R5 R6" type="priority" reset="false"/></nodes>', encoding="utf-8")
    groups = {"J_AB": ["A", "B"], "J_remote": ["R5", "R6"]}
    mutable_nodes = {"A", "B", "J_AB", "R5", "R6", "J_remote"}
    root = ET.parse(source).getroot()
    mutable_edges = {row.get("id") for row in root.findall("edge")
                     if any(row.get(end) in mutable_nodes for end in ("from", "to"))}
    output = tmp_path / "joined.net.xml"
    result, record = run_scoped_native_conversion(command=[shutil.which("netconvert"), "--sumo-net-file", str(source),
        "--node-files", str(nodes), "--offset.disable-normalization", "true", "--output-file", str(output)],
        mutable_nodes=mutable_nodes, mutable_edges=mutable_edges, join_groups=groups,
        output_dir=tmp_path, prefix="multiple", timeout_seconds=30)
    assert result.returncode == 0, result.stderr
    assert record["status"] == "local" and record["source_unchanged"]
    assert record["converted_normal_edge_count"] == 14
    candidate = ET.parse(output).getroot()
    assert sum(not row.get("id", "").startswith(":") for row in candidate.findall("edge")) == 44
    assert candidate.find("junction[@id='J_AB']") is not None
    assert candidate.find("junction[@id='J_remote']") is not None
    assert ET.canonicalize(ET.tostring(root.find("tlLogic[@id='R10']"), encoding="unicode"), strip_text=True) == ET.canonicalize(
        ET.tostring(candidate.find("tlLogic[@id='R10']"), encoding="unicode"), strip_text=True)
    assert file_sha256(source) == before

    proposal = Path(record["merged_proposal"]["path"])
    assert proposal != output
    digest = file_sha256(proposal)
    output.write_text("later restoration", encoding="utf-8")
    assert file_sha256(proposal) == digest == record["merged_proposal"]["sha256"]


def test_shared_conversion_failure_keeps_partial_output_and_protects_existing_files(tmp_path, monkeypatch):
    from astra_sumo.core.local_network_conversion import run_scoped_native_conversion
    from astra_sumo.core.command_runner import CommandResult
    source, output = tmp_path / "source.net.xml", tmp_path / "candidate.net.xml"
    source.write_text('<net><location netOffset="0,0" projParameter="!"/></net>', encoding="utf-8")
    original = source.read_bytes()
    def fail(command, **kwargs):
        Path(command[command.index("--output-file") + 1]).write_text("partial native output", encoding="utf-8")
        return CommandResult(command=command, cwd=str(kwargs["cwd"]), status="fail", returncode=1)
    monkeypatch.setattr("astra_sumo.core.command_runner.run_command", fail)
    args = dict(command=["netconvert", "--sumo-net-file", str(source), "--output-file", str(output)],
                mutable_nodes=set(), mutable_edges=set(), output_dir=tmp_path, prefix="failed", timeout_seconds=1)
    result, record = run_scoped_native_conversion(**args)
    assert result.returncode == 1 and "merged_proposal" not in record
    assert not output.exists()
    assert Path(record["native_output"]["path"]).read_text() == "partial native output"
    assert record["native_output"]["status"] == "incomplete"
    failure = json.loads((tmp_path / "failed.conversion-record.json").read_text())
    assert failure["command"]["returncode"] == 1
    assert failure["local_conversion"]["native_output"] == record["native_output"]
    assert source.read_bytes() == original
    with pytest.raises(ValueError, match="new conversion output"):
        run_scoped_native_conversion(**args)


def test_failed_local_merge_keeps_native_output_separate_from_candidate(tmp_path, monkeypatch):
    if not shutil.which("netconvert"):
        pytest.skip("SUMO required")
    from astra_sumo.core.local_network_conversion import run_scoped_native_conversion
    source = _source(tmp_path)
    def reject(*args):
        raise ValueError("test merge failure")
    monkeypatch.setattr("astra_sumo.core.local_network_conversion.merge_local_conversion", reject)
    output = tmp_path / "candidate.net.xml"
    with pytest.raises(ValueError, match="test merge failure"):
        run_scoped_native_conversion(command=[shutil.which("netconvert"), "--sumo-net-file", str(source),
            "--output-file", str(output)], mutable_nodes={"A", "B"},
            mutable_edges={"in", "middle", "out", "reverse", "east", "west", "north", "south"},
            output_dir=tmp_path, prefix="merge-failure", timeout_seconds=30)
    assert not output.exists()
    assert ET.parse(tmp_path / "merge-failure.native-local-output.net.xml").getroot().tag == "net"
    failure = json.loads((tmp_path / "merge-failure.conversion-record.json").read_text())
    assert failure["exception"] == {"type": "ValueError", "message": "test merge failure"}
    assert failure["local_conversion"]["source_unchanged"]
    assert Path(failure["local_conversion"]["native_output"]["path"]).is_file()


def test_native_error_without_output_still_has_a_durable_conversion_record(tmp_path):
    if not shutil.which("netconvert"):
        pytest.skip("SUMO required")
    from astra_sumo.core.local_network_conversion import run_scoped_native_conversion
    source = _source(tmp_path)
    patch = tmp_path / "invalid.con.xml"
    patch.write_text('<connections><connection from="in" to="middle" fromLane="999" toLane="0"/></connections>', encoding="utf-8")
    output = tmp_path / "candidate.net.xml"
    result, record = run_scoped_native_conversion(command=[shutil.which("netconvert"), "--sumo-net-file", str(source),
        "--connection-files", str(patch), "--output-file", str(output)], mutable_nodes={"A", "B"},
        mutable_edges={"in", "middle", "out", "reverse", "east", "west", "north", "south"},
        output_dir=tmp_path, prefix="native-error", timeout_seconds=30)
    assert result.returncode != 0 and result.stderr
    assert not output.exists() and "native_output" not in record
    failure = json.loads((tmp_path / "native-error.conversion-record.json").read_text())
    assert failure["status"] == "failed"
    assert failure["command"]["stderr"] == result.stderr
    assert failure["local_conversion"]["input"] == record["input"]
    assert failure["local_conversion"]["source_unchanged"]


def test_source_drift_returns_measurements_and_retains_a_warning_record(tmp_path, monkeypatch):
    from astra_sumo.core.local_network_conversion import run_scoped_native_conversion
    from astra_sumo.core.command_runner import CommandResult
    source, output = tmp_path / "source.net.xml", tmp_path / "candidate.net.xml"
    original = b'<net><location netOffset="0,0" projParameter="!"/></net>'
    source.write_bytes(original)
    def convert(command, **kwargs):
        source.write_bytes(original.replace(b"<net>", b'<net changed="true">'))
        frozen = Path(command[command.index("--sumo-net-file") + 1]).read_bytes()
        Path(command[command.index("--output-file") + 1]).write_bytes(frozen)
        return CommandResult(command=command, cwd=str(kwargs["cwd"]), status="pass", returncode=0)
    monkeypatch.setattr("astra_sumo.core.command_runner.run_command", convert)
    result, record = run_scoped_native_conversion(command=["netconvert", "--sumo-net-file", str(source),
        "--output-file", str(output)], mutable_nodes=set(), mutable_edges=set(), output_dir=tmp_path,
        prefix="source-drift", timeout_seconds=1)
    assert result.returncode == 0 and not record["source_unchanged"] and not record["merge_source_unchanged"]
    assert output.read_bytes() == original
    assert Path(record["input"]["path"]).read_bytes() == original
    assert record["input"]["sha256"] == record["source"]["sha256"]
    warning = json.loads((tmp_path / "source-drift.conversion-record.json").read_text())
    assert warning["status"] == "source_changed" and warning["exception"] is None


def test_full_input_snapshot_cannot_also_be_the_requested_output(tmp_path, monkeypatch):
    from astra_sumo.core.local_network_conversion import run_scoped_native_conversion
    source, output = tmp_path / "source.net.xml", tmp_path / "guard.full-input.net.xml"
    source.write_text("<net/>", encoding="utf-8")
    monkeypatch.setattr("astra_sumo.core.command_runner.run_command", lambda *args, **kwargs: pytest.fail("The native input must stay separate."))
    with pytest.raises(ValueError, match="new conversion output"):
        run_scoped_native_conversion(command=["netconvert", "--sumo-net-file", str(source), "--output-file", str(output)],
            mutable_nodes=set(), mutable_edges=set(), output_dir=tmp_path, prefix="guard", timeout_seconds=1)
    assert not output.exists() and source.read_text() == "<net/>"


@pytest.mark.parametrize("element", ["junction", "tlLogic", "connection"])
def test_shared_join_rejects_occupied_target_ids_before_conversion(tmp_path, monkeypatch, element):
    from astra_sumo.core.local_network_conversion import run_scoped_native_conversion
    root = ET.Element("net")
    ET.SubElement(root, element, **({"tl": "LSA2349_part0"} if element == "connection" else {"id": "LSA2349_part0"}))
    source = tmp_path / "source.net.xml"
    ET.ElementTree(root).write(source)
    def unexpected(*args, **kwargs):
        raise AssertionError("Native conversion must not run with occupied join IDs.")
    monkeypatch.setattr("astra_sumo.core.command_runner.run_command", unexpected)
    with pytest.raises(ValueError, match="new joined junction and controller IDs"):
        run_scoped_native_conversion(command=["netconvert", "--sumo-net-file", str(source), "--output-file", str(tmp_path / "candidate.net.xml")],
            mutable_nodes=set(), mutable_edges=set(), join_groups={"LSA2349_part0": ["A", "B"]},
            output_dir=tmp_path, prefix="test", timeout_seconds=1)


def test_shared_full_conversion_retains_its_native_snapshot(tmp_path, monkeypatch):
    if not shutil.which("netconvert"):
        pytest.skip("SUMO required")
    from astra_sumo.core.local_network_conversion import run_scoped_native_conversion
    source = _source(tmp_path)
    output = tmp_path / "full.net.xml"
    monkeypatch.setattr("astra_sumo.core.local_network_conversion.local_conversion_input",
                        lambda *args, **kwargs: (None, {"status": "full_network", "reason": "test_control"}))
    result, record = run_scoped_native_conversion(command=[shutil.which("netconvert"), "--sumo-net-file", str(source),
        "--output-file", str(output)], mutable_nodes=set(), mutable_edges=set(),
        output_dir=tmp_path, prefix="full", timeout_seconds=30)
    assert result.returncode == 0, result.stderr
    native = record["native_output"]
    assert Path(record["input"]["path"]) != source
    assert Path(record["input"]["path"]).read_bytes() == source.read_bytes()
    assert Path(native["path"]) != output and "merged_proposal" not in record
    output.write_text("later restoration", encoding="utf-8")
    assert file_sha256(Path(native["path"])) == native["sha256"]


def test_conversion_can_keep_the_saved_baseline_exterior(tmp_path):
    if not shutil.which("netconvert"):
        pytest.skip("SUMO required")
    from astra_sumo.core.local_network_conversion import run_scoped_native_conversion
    source = _source(tmp_path)
    tree = ET.parse(source)
    tree.getroot().find("edge[@id='far10']/lane").set("speed", "4.2")
    baseline = tmp_path / "baseline.net.xml"
    tree.write(baseline, encoding="utf-8", xml_declaration=True)
    root = ET.parse(source).getroot()
    nodes = {"A", "B"}
    edges = {row.get("id") for row in root.findall("edge") if any(row.get(end) in nodes for end in ("from", "to"))}
    output = tmp_path / "kept.net.xml"
    result, record = run_scoped_native_conversion(command=[shutil.which("netconvert"), "--sumo-net-file", str(source),
        "--offset.disable-normalization", "true", "--output-file", str(output)], mutable_nodes=nodes, mutable_edges=edges,
        output_dir=tmp_path, prefix="baseline", timeout_seconds=30, merge_source_file=baseline)
    assert result.returncode == 0, result.stderr
    assert ET.parse(output).getroot().find("edge[@id='far10']/lane").get("speed") == "4.2"
    assert record["merge_source_unchanged"] and record["source_unchanged"]
    assert record["merge_source"]["sha256"] == file_sha256(baseline)


@pytest.mark.parametrize("mode", ["guarded", "fused"])
@pytest.mark.parametrize("include_context", [False, True])
def test_native_contour_selection_matches_a_full_input_control(tmp_path, monkeypatch, mode, include_context):
    if not shutil.which("netconvert"):
        pytest.skip("SUMO required")
    from unittest.mock import patch
    from astra_sumo.core.hamburg_junctions.movements import _compile_junction_contours
    source = _source(tmp_path)
    root = ET.parse(source).getroot()
    def proposal(network, identifier, **kwargs):
        shape = [tuple(map(float, point.split(","))) for point in network.find(f"junction[@id='{identifier}']").get("shape").split()]
        return {"status": "pass", "changed": True, "proposed_shape": shape, "native_boundary_refit": mode == "fused"}
    monkeypatch.setattr("astra_sumo.core.hamburg_junction_contour.propose_junction_contour", proposal)
    monkeypatch.setattr("astra_sumo.core.hamburg_junction_contour.propose_fused_junction_contour", proposal)
    def compile_case(name):
        folder = tmp_path / name
        folder.mkdir()
        output = folder / "candidate.net.xml"
        return _compile_junction_contours(deepcopy(root), command=[shutil.which("netconvert"), "--sumo-net-file", str(source),
            "--offset.disable-normalization", "true", "--output-file", str(output)], groups=[{"join_id": "A"}],
            output_file=output, timeout_seconds=30, mode=mode, include_context=include_context)
    _, local = compile_case("local")
    with patch("astra_sumo.core.local_network_conversion.local_conversion_input",
               return_value=(None, {"status": "full_network", "reason": "explicit_full_control"})):
        _, full = compile_case("full")
    assert local["parts"][0]["local_conversion"]["status"] == "local"
    assert local["parts"][0]["local_conversion"]["converted_normal_edge_count"] < 48
    assert local["accepted_junction_ids"] == full["accepted_junction_ids"]
    assert local["rejected_junction_ids"] == full["rejected_junction_ids"]
    if mode == "fused":
        assert full["accepted_junction_ids"] == full["target_junction_ids"]


def test_new_connection_keeps_modern_permissions_in_a_legacy_format_candidate(tmp_path):
    if not shutil.which("netconvert") or not shutil.which("sumo"):
        pytest.skip("SUMO required")
    initial = _source(tmp_path)
    patch_file, source = tmp_path / "remove.con.xml", tmp_path / "without-turn.net.xml"
    patch_file.write_text('<connections><delete from="in" to="west" fromLane="0" toLane="0"/></connections>', encoding="utf-8")
    converted = run_command([shutil.which("netconvert"), "-s", str(initial), "-x", str(patch_file), "-o", str(source)],
                            cwd=tmp_path, timeout_seconds=30)
    assert converted.returncode == 0, converted.stderr
    tree = ET.parse(source)
    tree.getroot().set("version", "1.3")
    tree.write(source, encoding="utf-8", xml_declaration=True)
    output = tmp_path / "add"
    output.mkdir()
    candidate = output / "candidate.net.xml"
    operation = {"id": "new-turn", "operation": "add_lane_connection", "params": {
        "from": "in", "fromLane": 0, "to": "west", "toLane": 0, "disallow": "rail_urban"}}
    plan = write_scoped_edit_plan(root=tree.getroot(), operation=operation, source_file=source,
        candidate_file=candidate, output_dir=output, prefix="test", netconvert_binary=shutil.which("netconvert"), mode="explore")
    result = run_command(plan["command"], cwd=output, timeout_seconds=30)
    assert result.returncode == 0, result.stderr
    checks = check_scoped_edit(plan=plan, candidate_file=candidate, output_dir=output,
        netconvert_binary=shutil.which("netconvert"), timeout_seconds=30, command_runner=run_command, mode="explore")
    assert checks["release_checks_passed"], checks
    root = ET.parse(candidate).getroot()
    assert root.get("version") == "1.3"
    via = root.find("connection[@from='in'][@to='west']").get("via")
    label = f"new-legacy-turn-{tmp_path.name}"
    traci.start([shutil.which("sumo"), "-n", str(candidate), "--end", "1", "--no-step-log", "true"], label=label, numRetries=3)
    connection = traci.getConnection(label)
    try:
        denied = set(connection.lane.getDisallowed(via))
    finally:
        connection.close()
    assert "rail_urban" in denied and not denied & {"subway", "cable_car"}


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
