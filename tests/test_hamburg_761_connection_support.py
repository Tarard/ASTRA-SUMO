"""The761 review candidate adds two lane continuations without widening roads."""

from copy import deepcopy
import gzip
from pathlib import Path
import xml.etree.ElementTree as ET

import pytest

from astra_sumo.core import hamburg_761_connection_support as repair
from astra_sumo.core.junction_split_fanout import _internal_path, _key
from astra_sumo.core.local_network_conversion import loaded_turn_permissions


@pytest.fixture(scope="module")
def sources(tmp_path_factory):
    repo = Path(__file__).resolve().parents[1]
    folder = tmp_path_factory.mktemp("source761")
    files = [("network.net.xml", "hamburg_ring1_266197525_partial_control_20261024_v1/candidate-v2/candidate.net.xml.gz"),
        ("control.add.xml", "hamburg_ring1_209_ordinal_connection_20261024_v1/construction/candidate-v3/control.add.xml.gz")]
    for name, frozen in files:
        (folder / name).write_bytes(gzip.decompress((repo / "benchmarks" / frozen).read_bytes()))
    return folder


def test_patch_adds_only_two_native_lane_choices_and_does_not_mutate_source(sources):
    root = ET.parse(sources / "network.net.xml").getroot()
    before = ET.tostring(root)
    patch, access = repair._patch(root)
    assert {_key(row) for row in patch} == repair.ADDED | {repair.ORIGINAL}
    frozen = next(row for row in patch if _key(row) == repair.ORIGINAL)
    assert frozen.get("shape") and frozen.get("speed")
    assert all(row.tag == "connection" and row.get("fromLane") == "0" for row in patch)
    assert all(not any(name in row.attrib for name in ("pass", "uncontrolled", "shape", "keepClear")) for row in patch if _key(row) in repair.ADDED)
    assert all("length" not in row.attrib for row in patch)
    assert all("passenger" in modes and modes == loaded_turn_permissions(root)[repair.ORIGINAL] for modes in access.values())
    assert ET.tostring(root) == before


@pytest.mark.parametrize("change", ["node_type", "extra_road", "extra_turn", "restricted_lane"])
def test_source_scope_rejects_another_topology_or_access(sources, change):
    root = ET.parse(sources / "network.net.xml").getroot()
    if change == "node_type":
        root.find(f"junction[@id='{repair.NODE}']").set("type", "traffic_light")
    elif change == "extra_road":
        root.find("edge[@id='44532531']").set("to", repair.NODE)
    elif change == "extra_turn":
        row = deepcopy(next(row for row in root.findall("connection") if _key(row) == repair.ORIGINAL))
        row.set("toLane", "1")
        root.append(row)
    else:
        root.find("edge[@id='199500674#0']/lane[@index='2']").set("disallow", "passenger")
    with pytest.raises(ValueError):
        repair._patch(root)


@pytest.fixture(scope="module")
def compiled(sources, tmp_path_factory):
    repo = Path(__file__).resolve().parents[1]
    binary = repo / ".venv/Lib/site-packages/sumo/bin/netconvert.exe"
    if not binary.is_file():
        pytest.skip("Project netconvert is unavailable.")
    out = tmp_path_factory.mktemp("compile761") / "candidate"
    result = repair.materialize_hamburg_761_connection_support(source_file=sources / "network.net.xml", control_file=sources / "control.add.xml",
        output_dir=out, netconvert_binary=binary)
    assert result["status"] == "review_required" and all(result["checks"].values()), result
    return result


def test_native_compile_preserves_source_roads_old_curve_and_native_requests(sources, compiled):
    before = ET.parse(sources / "network.net.xml").getroot()
    after = ET.parse(compiled["candidate_file"]).getroot()
    raw = ET.parse(compiled["conversion"]["native_output"]["path"]).getroot()
    old, new = loaded_turn_permissions(before), loaded_turn_permissions(after)
    assert new.keys() - old.keys() == repair.ADDED and all(new[key] == modes for key, modes in old.items())
    assert all(new[key] == old[repair.ORIGINAL] for key in repair.ADDED)
    assert [row.attrib for row in after.find(f"junction[@id='{repair.NODE}']")] == [row.attrib for row in raw.find(f"junction[@id='{repair.NODE}']")]
    assert len(after.find(f"junction[@id='{repair.NODE}']").findall("request")) == 3
    geometry = compiled["geometry"]
    assert geometry["native_expected_group_length_m"] == sum(row["arc_length_m"] for row in geometry["native_group_geometric_lengths"]) / 3
    assert abs(geometry["candidate_original_length_m"] - geometry["native_expected_group_length_m"]) < 1e-9
    assert not compiled["traffic_simulation_run"]


@pytest.mark.parametrize("change,gate", [
    ("road", "ordinary_roads_xml_exact"), ("external", "outside_elements_and_order_exact"),
    ("old_length", "only_native_group_length_normalization"), ("old_shape", "original_internal_curve_and_speed_preserved"),
    ("old_keep_clear", "original_connection_runtime_attributes_exact"),
    ("old_internal_keep_clear", "original_internal_connection_attributes_exact"),
    ("new_pass", "all_target_connection_attributes_are_native"),
    ("old_lane_change", "original_internal_lane_width_access_and_LC_exact"), ("boundary", "target_attributes_exact"),
    ("program", "all_programs_exact"), ("request", "native_requests_not_overlaid"),
])
def test_candidate_guards_reject_protected_changes(sources, compiled, change, gate):
    before = ET.parse(sources / "network.net.xml").getroot()
    after = ET.parse(compiled["candidate_file"]).getroot()
    raw = ET.parse(compiled["conversion"]["native_output"]["path"]).getroot()
    old = next(row for row in after.findall("connection") if _key(row) == repair.ORIGINAL)
    if change == "road":
        after.find("edge[@id='761590046']/lane").set("length", "20")
    elif change == "external":
        next(row for row in after.findall("connection") if row.get("tl") == "LSA119_part0").set("keepClear", "false")
    elif change == "old_length":
        _internal_path(after, old)[0].set("length", "9")
    elif change == "old_shape":
        _internal_path(after, old)[0].set("shape", "850,817 861,810")
    elif change == "old_keep_clear":
        old.set("keepClear", "false")
    elif change == "old_internal_keep_clear":
        next(row for row in after.findall("connection") if row.get("from") == _internal_path(after, old)[0].get("id").rsplit("_", 1)[0]
            and row.get("toLane") == "0").set("keepClear", "false")
    elif change == "new_pass":
        next(row for row in after.findall("connection") if _key(row) in repair.ADDED).set("pass", "true")
    elif change == "old_lane_change":
        _internal_path(after, old)[0].set("changeLeft", "none")
    elif change == "boundary":
        after.find(f"junction[@id='{repair.NODE}']").set("customShape", "false")
    elif change == "program":
        after.remove(after.find("tlLogic"))
    else:
        after.find(f"junction[@id='{repair.NODE}']/request").set("response", "111")
    checks, _ = repair._verify(before, after, raw)
    assert not checks[gate]
