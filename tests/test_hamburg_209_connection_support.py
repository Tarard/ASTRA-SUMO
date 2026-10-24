from copy import deepcopy
import gzip
from pathlib import Path
import xml.etree.ElementTree as ET

import pytest

from astra_sumo.core import hamburg_209_connection_support as repair

project_program = repair.project_program


@pytest.fixture
def source(tmp_path):
    folder = Path(__file__).resolve().parents[1] / "benchmarks/hamburg_ring1_119_receiving_priority_20261023_v1/full-priority/prepared"
    paths = []
    for name in ("network.net.xml", "control.add.xml"):
        path = tmp_path / name
        path.write_bytes(gzip.decompress((folder / (name + ".gz")).read_bytes()))
        paths.append(path)
    return paths


@pytest.mark.parametrize("identity,width,donor", [("266197536", 6, 0), ("266197525", 8, 6)])
def test_projection_keeps_all_old_states_and_parameters(identity, width, donor):
    program = ET.Element("tlLogic", id=identity, type="actuated", offset="7", programID="test")
    ET.SubElement(program, "phase", duration="42", minDur="5", maxDur="50", state="GrygGrGG"[:width])
    before = deepcopy(program)
    projected = project_program(program)
    assert projected.attrib == before.attrib
    assert {k: v for k, v in projected[0].attrib.items() if k != "state"} == {k: v for k, v in before[0].attrib.items() if k != "state"}
    assert projected[0].get("state") == before[0].get("state") + before[0].get("state")[donor]
    assert ET.tostring(program) == ET.tostring(before)


def test_projection_rejects_already_extended_or_ambiguous_program():
    for identity, state in [("266197536", "G" * 7), ("266197525", "G" * 9), ("266197525", "rrrrrrGr"), ("other", "G" * 6)]:
        program = ET.Element("tlLogic", id=identity)
        ET.SubElement(program, "phase", state=state)
        with pytest.raises(ValueError):
            project_program(program)


def test_program_inventory_rejects_missing_extra_duplicate_or_changed_program():
    expected = ET.fromstring('<net><tlLogic id="266197536" programID="0"><phase state="GG" duration="5"/></tlLogic><tlLogic id="other" programID="A"/></net>')
    assert repair._programs_match(expected, deepcopy(expected))
    missing = deepcopy(expected)
    missing.remove(missing[0])
    assert not repair._programs_match(expected, missing)
    extra = deepcopy(expected)
    ET.SubElement(extra, "tlLogic", id="added", programID="0")
    assert not repair._programs_match(expected, extra)
    duplicate = deepcopy(expected)
    duplicate.append(deepcopy(duplicate[0]))
    assert not repair._programs_match(expected, duplicate)
    changed = deepcopy(expected)
    changed[0][0].set("duration", "6")
    assert not repair._programs_match(expected, changed)


def test_boundary_guard_allows_only_recomputed_internal_lane_inventory():
    source = ET.Element("junction", id="266197536", type="traffic_light", x="1", y="2", shape="1,2 3,4",
        incLanes="603_0 603_1", intLanes=":old_0", customShape="true")
    rebuilt = deepcopy(source)
    rebuilt.set("intLanes", ":new_0 :new_1")
    assert repair._boundary_attributes_match(source, rebuilt)
    for key, value in [("type", "priority"), ("incLanes", "603_0"), ("customShape", "false"), ("x", "9"), ("unexplained", "new")]:
        changed = deepcopy(rebuilt)
        changed.set(key, value)
        assert not repair._boundary_attributes_match(source, changed)


def test_patch_replaces_the_misaligned_lane_ordinals_and_keeps_residential(source):
    root = ET.parse(source[0]).getroot()
    before = ET.tostring(root)
    patch, bindings, access = repair._patch(root)
    deletes = {repair._key(row) for row in patch.findall("delete")}
    assert deletes == {("603103444", 1, "73474895", 0), ("603103444", 2, "73474895", 1), ("603103444", 3, "73474895", 2)}
    expected = {(0, "381750336", 0), (1, "381750336", 1), (2, "73474895", 0), (3, "73474895", 1), (4, "73474895", 2)}
    assert {(int(row.get("fromLane")), row.get("to"), int(row.get("toLane"))) for row in patch.findall("connection") if row.get("from") == "603103444"} == expected
    assert ("73474895", 0, "27064186#0", 0) in bindings
    assert bindings[("73474895", 0, "1108564798#0", 0)] == ("266197525", "8", None)
    assert all("passenger" in modes for modes in access.values())
    assert ET.tostring(root) == before


def test_no_new_turn_can_widen_a_restricted_endpoint(source):
    root = ET.parse(source[0]).getroot()
    root.find("edge[@id='603103444']/lane[@index='4']").set("disallow", "passenger")
    with pytest.raises(ValueError, match="passenger access"):
        repair._patch(root)


def test_external_projection_preserves_non_target_bytes_and_all_old_phase_attributes(source):
    raw = source[1].read_bytes()
    result = repair._control_bytes(raw)
    before, after = ET.fromstring(raw), ET.fromstring(result)
    for first, last in zip(before.findall("tlLogic"), after.findall("tlLogic"), strict=True):
        if first.get("id") not in repair.NODES:
            assert repair._canonical(first) == repair._canonical(last)
        else:
            assert repair._canonical(project_program(first)) == repair._canonical(last)
    for identity in repair.NODES:
        for first, last in zip(before.find(f"tlLogic[@id='{identity}']"), after.find(f"tlLogic[@id='{identity}']"), strict=True):
            assert first.get("state") == last.get("state")[:-1]


def test_native_scoped_compile_preserves_roads_exterior_and_native_row(source, tmp_path):
    repo = Path(__file__).resolve().parents[1]
    binary = repo / ".venv/Lib/site-packages/sumo/bin/netconvert.exe"
    if not binary.is_file():
        pytest.skip("The project netconvert binary is unavailable.")
    report = repair.materialize_hamburg_209_connection_support(source_file=source[0], control_file=source[1],
        output_dir=tmp_path / "candidate", netconvert_binary=binary)
    assert report["status"] == "review_required" and all(report["checks"].values()), report
    candidate = ET.parse(report["candidate_file"]).getroot()
    raw = ET.parse(tmp_path / "candidate/ordinal.native-local-output.net.xml").getroot()
    for node, expected_requests in [("266197536", 7), ("266197525", 6)]:
        first = raw.find(f"junction[@id='{node}']")
        last = candidate.find(f"junction[@id='{node}']")
        assert [row.attrib for row in first.findall("request")] == [row.attrib for row in last.findall("request")]
        assert len(last.findall("request")) == expected_requests
        assert all(len(row.get(attribute)) == expected_requests for row in last.findall("request") for attribute in ("response", "foes"))
    assert not report["traffic_simulation_run"]
