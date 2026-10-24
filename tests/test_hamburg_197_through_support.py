from copy import deepcopy
import gzip
from pathlib import Path
import xml.etree.ElementTree as ET

import pytest

from astra_sumo.core import hamburg_197_through_support as repair


@pytest.fixture
def source(tmp_path):
    case = Path(__file__).resolve().parents[1] / "benchmarks/hamburg_ring1_classical_od_response_20261023_v1/prepared"
    paths = []
    for name in ("network.net.xml", "control.add.xml"):
        path = tmp_path / name
        path.write_bytes(gzip.decompress((case / (name + ".gz")).read_bytes()))
        paths.append(path)
    return paths


def fake_native(monkeypatch, change=None):
    def compile(**kwargs):
        before = ET.parse(kwargs["merge_source_file"]).getroot()
        if kwargs["prefix"] == "priority":
            command = kwargs["command"]
            after = ET.parse(command[command.index("--sumo-net-file") + 1]).getroot()
            out = Path(kwargs["output_dir"])
            raw = out / "priority-fake-native-output.net.xml"
            ET.ElementTree(after).write(raw, encoding="utf-8", xml_declaration=True)
            Path(command[command.index("--output-file") + 1]).write_bytes(raw.read_bytes())
            from astra_sumo.core.local_network_conversion import local_conversion_input
            _, record = local_conversion_input(before, {repair.NODE}, repair._target_roads(before))
            return {"returncode": 0, "status": "pass"}, {**record, "native_output": {"path": str(raw)}}
        after = deepcopy(before)
        lanes = {lane.get("id"): lane for e in after.findall("edge") for lane in e.findall("lane")}
        target = after.find("junction[@id='1975693623']")
        for index, key in enumerate(repair.ADDITIONS):
            first, last = lanes[f"{key[0]}_{key[1]}"], lanes[f"{key[2]}_{key[3]}"]
            shape = first.get("shape").split()[-1] + " " + last.get("shape").split()[0]
            edge_id = f":1975693623_added{index}"
            edge = ET.SubElement(after, "edge", id=edge_id, function="internal")
            ET.SubElement(edge, "lane", id=edge_id + "_0", index="0", speed="13.888889", length="10", shape=shape,
                          disallow=first.get("disallow"))
            ET.SubElement(after, "connection", {"from": key[0], "fromLane": str(key[1]), "to": key[2], "toLane": str(key[3]),
                          "via": edge_id + "_0", "tl": "1975693623", "linkIndex": str(14 + index), "dir": "s", "state": "o"})
            ET.SubElement(after, "connection", {"from": edge_id, "fromLane": "0", "to": key[2], "toLane": str(key[3]), "dir": "s", "state": "M"})
            target.set("intLanes", target.get("intLanes") + " " + edge_id + "_0")
        for index, c in enumerate(c for c in after.findall("connection") if c.get("tl") == "1975693623"):
            c.set("linkIndex", str(15 - index))
        for row in list(target.findall("request")):
            target.remove(row)
        for index in range(16):
            ET.SubElement(target, "request", index=str(index), response="0" * 16, foes="0" * 16, cont="0")
        for index, other, response in ((15, 9, False), (9, 15, True), (15, 2, False), (2, 15, True)):
            request = target.find(f"request[@index='{index}']")
            bits = list(request.get("foes"))
            bits[-1 - other] = "1"
            request.set("foes", "".join(bits))
            if response:
                request.set("response", "".join(bits))
        after.find("tlLogic[@id='1975693623']/phase").set("state", "r" * 16)
        if change:
            change(after)
        out = Path(kwargs["output_dir"])
        raw = out / "fake-native-output.net.xml"
        ET.ElementTree(after).write(raw, encoding="utf-8", xml_declaration=True)
        candidate = Path(kwargs["command"][kwargs["command"].index("--output-file") + 1])
        candidate.write_bytes(raw.read_bytes())
        from astra_sumo.core.local_network_conversion import local_conversion_input
        _, record = local_conversion_input(before, {repair.NODE}, repair._target_roads(before))
        return {"returncode": 0, "status": "pass"}, {**record, "native_output": {"path": str(raw)}}
    monkeypatch.setattr(repair, "run_scoped_native_conversion", compile)


def test_materializer_restores_every_signal_binding_including_pedestrians_and_native_requests(source, tmp_path, monkeypatch):
    fake_native(monkeypatch)
    before = [p.read_bytes() for p in source]
    report = repair.materialize_hamburg_197_through_support(source_file=source[0], control_file=source[1],
        output_dir=tmp_path / "out", netconvert_binary="unused-test-converter")
    assert report["status"] == "review_required" and all(report["checks"].values()), report
    output = ET.parse(report["candidate_file"]).getroot()
    original = ET.fromstring(before[0])
    by_key = {repair._key(c): c for c in output.findall("connection")}
    for row in original.findall("connection"):
        if row.get("tl") == "1975693623":
            actual = by_key[repair._key(row)]
            assert actual.get("tl") == row.get("tl") and actual.get("linkIndex") == row.get("linkIndex")
    assert [by_key[k].get("linkIndex") for k in repair.ADDITIONS] == ["14", "15"]
    assert len(output.find("junction[@id='1975693623']").findall("request")) == 16
    expected = repair._extend_program(original.find("tlLogic[@id='1975693623']"))
    assert repair._canonical(output.find("tlLogic[@id='1975693623']")) == repair._canonical(expected)
    assert Path(report["control_file"]).read_bytes() == repair._extend_control_bytes(before[1])
    assert [p.read_bytes() for p in source] == before
    tll_connections = ET.parse(tmp_path / "out/final-program.tll.xml").getroot().findall("connection")
    assert len(tll_connections) == 12
    assert all(not row.get("from").startswith(":") and not row.get("to").startswith(":") for row in tll_connections)
    assert [(r["phase_index"], r["signal_index"]) for r in report["priority_case_changes"]] == [(0, 9), (1, 9), (3, 2)]


@pytest.mark.parametrize("through,opposite,expected_old9", [("G", "G", "g"), ("G", "g", "g"), ("G", "y", "y"),
                                                           ("G", "r", "r"), ("y", "G", "G"), ("r", "G", "G")])
@pytest.mark.parametrize("turn_index", [2, 9])
def test_old_turns_yield_without_changing_old_signal_colors(through, opposite, expected_old9, turn_index):
    state = list("r" * 14)
    state[1], state[turn_index] = through, opposite
    original = "".join(state)
    projected = repair._extend_state(original)
    assert projected[:14].lower() == original.lower() and projected[14:] == through * 2
    assert projected[turn_index] == expected_old9
    assert all(projected[i] == original[i] for i in range(14) if i != turn_index)


@pytest.mark.parametrize("delta", [1.8e-10, 2e-6])
def test_boundary_rounding_restore_never_replaces_native_request_structure(source, delta):
    before = ET.parse(source[0]).getroot()
    after = deepcopy(before)
    original = before.find("junction[@id='1975693623']")
    node = after.find("junction[@id='1975693623']")
    node.set("x", str(float(node.get("x")) + delta))
    node.set("customShape", "1")
    node.set("intLanes", " ".join(reversed(node.get("intLanes").split())))
    node.find("request").set("response", "1" + "0" * 13)
    native = (node.get("incLanes"), node.get("intLanes"), node.get("type"), [repair._canonical(r) for r in node.findall("request")])
    if delta > 1e-6:
        with pytest.raises(ValueError, match="boundary.*beyond rounding"):
            repair._restore_local_geometry(before, after)
    else:
        repair._restore_local_geometry(before, after)
        assert all(node.get(a) == original.get(a) for a in ("x", "y", "z", "shape", "customShape"))
        assert native == (node.get("incLanes"), node.get("intLanes"), node.get("type"), [repair._canonical(r) for r in node.findall("request")])


@pytest.mark.parametrize("attribute,default", [("speed", "0"), ("width", "3.2")])
@pytest.mark.parametrize("delta", [1.8e-10, 2e-6])
def test_old_uniform_internal_speed_width_restores_only_rounding(source, attribute, default, delta):
    before = ET.parse(source[0]).getroot()
    after = deepcopy(before)
    source_lane = repair._internal_path(before, repair._connections(before)[repair.THROUGH])[0]
    lane = repair._internal_path(after, repair._connections(after)[repair.THROUGH])[0]
    original = source_lane.get(attribute)
    lane.set(attribute, str(float(source_lane.get(attribute, default)) + delta))
    if delta > 1e-6:
        with pytest.raises(ValueError, match="beyond rounding"):
            repair._restore_local_geometry(before, after)
    else:
        corrections = repair._restore_local_geometry(before, after)
        assert lane.get(attribute) == original
        assert any(row.get("attribute") == attribute for row in corrections)


def test_walking_hints_pin_source_shapes_without_service_road_sidewalk(source):
    before = ET.parse(source[0]).getroot()
    patch = repair._patch(before, repair._connections(before), repair.loaded_turn_permissions(before))
    hints = patch.findall("walkingArea")
    assert len(hints) == 4
    assert {row.get("edges") for row in hints} == {"-186821028 186821027#0", "186821028", "186821029#1", "186821029#1 186821027#0"}
    for ordinal, row in enumerate(hints):
        lane = before.find(f"edge[@id=':1975693623_w{ordinal}']/lane")
        assert row.get("shape") == lane.get("shape") and row.get("width") == lane.get("width")
    crossings = [row for row in patch.findall("crossing") if row.get("discard") != "true"]
    assert [row.get("linkIndex") for row in crossings] == ["10", "11", "12", "13"]


def test_walking_hints_reject_unexpected_source_sidewalk_adjacency(source):
    before = ET.parse(source[0]).getroot()
    ET.SubElement(before, "connection", {"from": ":1975693623_w0", "fromLane": "0", "to": "28665128#0", "toLane": "0"})
    with pytest.raises(ValueError, match="walking-area adjacency"):
        repair._patch(before, repair._connections(before), repair.loaded_turn_permissions(before))


def test_connection_hints_pin_old_native_speeds_but_not_the_two_additions(source):
    before = ET.parse(source[0]).getroot()
    rows = repair._connections(before)
    patch = repair._patch(before, rows, repair.loaded_turn_permissions(before))
    for row in patch.findall("connection"):
        key = repair._key(row)
        if key in repair.ADDITIONS:
            assert row.get("speed") is None
        else:
            expected = {float(lane.get("speed")) for lane in repair._internal_path(before, rows[key])}
            assert expected == {float(row.get("speed"))}


@pytest.mark.parametrize("speed", ["0", "nan", "nonuniform"])
def test_connection_hints_reject_speed_profiles_they_cannot_preserve(source, speed):
    before = ET.parse(source[0]).getroot()
    key = ("-186821028", 3, "28665128#0", 0)
    path = repair._internal_path(before, repair._connections(before)[key])
    assert len(path) == 2
    if speed == "nonuniform":
        path[-1].set("speed", str(float(path[0].get("speed")) + 1))
    else:
        for lane in path:
            lane.set("speed", speed)
    with pytest.raises(ValueError, match="uniform finite positive"):
        repair._patch(before, repair._connections(before), repair.loaded_turn_permissions(before))


@pytest.mark.parametrize("mutation", ["missing_pedestrian", "unexpected_turn", "permission_drift", "curve_drift", "priority_response", "parking_priority_response", "unsafe_protected_foe", "unsafe_new_new_foe"])
def test_materializer_blocks_drift_and_keeps_native_failure_artifacts(source, tmp_path, monkeypatch, mutation):
    def change(root):
        if mutation == "missing_pedestrian":
            root.remove(next(c for c in root.findall("connection") if c.get("tl") == "1975693623" and c.get("from", "").endswith("_w0")))
        elif mutation == "unexpected_turn":
            c = deepcopy(next(c for c in root.findall("connection") if repair._key(c) == repair.ADDITIONS[0]))
            c.set("toLane", "3")
            root.append(c)
        elif mutation in {"priority_response", "parking_priority_response", "unsafe_protected_foe", "unsafe_new_new_foe"}:
            node = root.find("junction[@id='1975693623']")
            if mutation in {"priority_response", "parking_priority_response"}:
                index = 2 if mutation == "parking_priority_response" else 9
                node.find(f"request[@index='{index}']").set("response", "0" * 16)
            else:
                bits = list("0" * 16)
                bits[-1 - (15 if mutation == "unsafe_new_new_foe" else 1)] = "1"
                node.find("request[@index='14']").set("foes", "".join(bits))
        else:
            lane = root.find("edge[@id=':1975693623_0']/lane[@index='1']")
            if mutation == "permission_drift":
                lane.set("allow", "bicycle")
            else:
                lane.set("shape", "0,0 1,1")
    fake_native(monkeypatch, change)
    report = repair.materialize_hamburg_197_through_support(source_file=source[0], control_file=source[1],
        output_dir=tmp_path / "out", netconvert_binary="unused-test-converter")
    assert report["status"] == "blocked"
    assert (tmp_path / "out/fake-native-output.net.xml").is_file()
    assert (tmp_path / "out/manifest.json").is_file()
    assert not (tmp_path / "out/network.net.xml").exists()


@pytest.mark.parametrize("invalid", ["wrong_node_type", "control_width", "control_parameter_drift", "already_added", "existing_output"])
def test_invalid_inputs_fail_before_conversion(source, tmp_path, monkeypatch, invalid):
    out = tmp_path / "out"
    if invalid == "existing_output":
        out.mkdir()
    else:
        path = source[1] if invalid in {"control_width", "control_parameter_drift"} else source[0]
        tree = ET.parse(path)
        if invalid == "wrong_node_type":
            tree.getroot().find("junction[@id='1975693623']").set("type", "priority")
        elif invalid == "control_width":
            tree.getroot().find("tlLogic[@id='1975693623']/phase").set("state", "G")
        elif invalid == "control_parameter_drift":
            tree.getroot().find("tlLogic[@id='1975693623']/phase").set("maxDur", "999")
        else:
            ET.SubElement(tree.getroot(), "connection", {"from": "-186821028", "fromLane": "3", "to": "186821027#0", "toLane": "4"})
        tree.write(path, encoding="utf-8", xml_declaration=True)
    monkeypatch.setattr(repair, "run_scoped_native_conversion", lambda **kw: pytest.fail("invalid input reached native converter"))
    with pytest.raises(ValueError):
        repair.materialize_hamburg_197_through_support(source_file=source[0], control_file=source[1],
            output_dir=out, netconvert_binary="unused-test-converter")
