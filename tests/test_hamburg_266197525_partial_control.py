"""Partial control preserves the original six movements and yielding relation."""

from copy import deepcopy
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import xml.etree.ElementTree as ET

import pytest

from astra_sumo.core.hamburg_266197525_partial_control import (
    COUNTER, MAIN, NODE, _connection_attributes, _movement_requests, _project_partial_control, _verify_candidate,
)
from astra_sumo.core.junction_split_fanout import _internal_path, _key
from astra_sumo.core.local_network_conversion import loaded_turn_permissions


@pytest.fixture
def source():
    repo = Path(__file__).resolve().parents[1]
    spec = spec_from_file_location("partial_control_fixture", repo / "benchmarks/hamburg_ring1_119_common_offset_20261024_v1/freeze_evidence.py")
    frozen = module_from_spec(spec)
    spec.loader.exec_module(frozen)
    packet = repo / "benchmarks/hamburg_ring1_209_ordinal_connection_20261024_v1"
    return ET.fromstring(frozen.read(packet, "full/prepared/network.net.xml"))


def candidate(source):
    root = _project_partial_control(source)
    for row in root.findall("connection"):
        key = row.get("from"), int(row.get("fromLane")), row.get("to"), int(row.get("toLane"))
        if key in MAIN:
            row.set("state", "m")
    return root


def test_only_four_movements_are_unbound(source):
    before = ET.tostring(source)
    projected = _project_partial_control(source)
    for row in projected.findall("connection"):
        key = row.get("from"), int(row.get("fromLane")), row.get("to"), int(row.get("toLane"))
        if key in MAIN:
            assert row.get("uncontrolled") == "true" and not any(a in row.attrib for a in ("tl", "linkIndex", "linkIndex2"))
        elif key in COUNTER:
            assert row.get("tl") == NODE and row.get("linkIndex") == str(COUNTER[key])
    assert ET.tostring(source) == before
    assert [ET.tostring(p) for p in projected.findall("tlLogic")] == [ET.tostring(p) for p in source.findall("tlLogic")]


def test_original_six_movement_request_map(source):
    mapping = _movement_requests(source)
    assert mapping.keys() == MAIN | COUNTER.keys()
    for key, relation in mapping.items():
        assert set(relation["response"]) == (set(COUNTER) if key in MAIN else set())
        assert set(relation["foes"]) == (set(COUNTER) if key in MAIN else MAIN)
        assert relation["cont"] == "0"


@pytest.mark.parametrize("kind", ["counter_unbound", "missing_main", "unknown_main"])
def test_projection_rejects_wrong_source_scope(source, kind):
    rows = source.findall("connection")
    if kind == "counter_unbound":
        next(row for row in rows if row.get("tl") == NODE and row.get("linkIndex") == "0").attrib.pop("tl")
    elif kind == "missing_main":
        source.remove(next(row for row in rows if row.get("tl") == NODE and row.get("linkIndex") == "8"))
    else:
        row = deepcopy(next(row for row in rows if row.get("tl") == NODE and row.get("linkIndex") == "8"))
        row.set("toLane", "1")
        source.append(row)
    with pytest.raises(ValueError):
        _project_partial_control(source)


def test_uncontrolled_candidate_exact_scope(source):
    proposed = candidate(source)
    checks, _ = _verify_candidate(source, proposed, proposed)
    assert all(checks.values())


@pytest.mark.parametrize("kind,gate", [
    ("yield_flip", "requests_and_foes_same_by_movement"),
    ("major_state", "four_main_minor_uncontrolled"),
    ("missing_program", "all_programs_exact"),
    ("boundary", "target_attributes_exact"),
    ("curve_length", "all_six_internal_curves_lengths_speeds"),
])
def test_materialization_guards_reject_tampering(source, kind, gate):
    proposed = candidate(source)
    if kind == "yield_flip":
        proposed.find(f"junction[@id='{NODE}']/request[@index='3']").set("response", "000000")
    elif kind == "major_state":
        next(r for r in proposed.findall("connection") if r.get("from") == "73474895").set("state", "M")
    elif kind == "missing_program":
        proposed.remove(proposed.find(f"tlLogic[@id='{NODE}']"))
    elif kind == "boundary":
        proposed.find(f"junction[@id='{NODE}']").set("type", "priority")
    else:
        proposed.find(f"edge[@id=':{NODE}_3']/lane").set("length", "99")
    checks, _ = _verify_candidate(source, proposed, proposed)
    assert not checks[gate]


def test_contpos_zero_hints_only_protect_three_original_primary_single_paths(source):
    access = loaded_turn_permissions(source)
    for row in source.findall("connection"):
        key = _key(row)
        if key in MAIN | COUNTER.keys():
            attrs = _connection_attributes(source, key, row, access)
            assert (attrs.get("contPos") == "0") == (key in MAIN and key[2] == "1108564798#0")
            assert attrs.get("pass") == row.get("pass") and attrs.get("keepClear") == row.get("keepClear")
    with pytest.raises(ValueError):
        _connection_attributes(source, ("other", 0, "other", 0), row, access)


@pytest.mark.parametrize("kind,gate", [
    ("keepClear", "all_six_connection_runtime_attributes_exact"),
    ("pass", "all_six_connection_runtime_attributes_exact"),
    ("LC", "all_internal_lane_width_LC_permissions_exact"),
    ("width", "all_internal_lane_width_LC_permissions_exact"),
    ("internal_access", "all_internal_lane_width_LC_permissions_exact"),
    ("internal_keepClear", "all_internal_connection_runtime_attributes_exact"),
])
def test_runtime_scope_guards_reject_silent_changes(source, kind, gate):
    proposed = candidate(source)
    row = next(r for r in proposed.findall("connection") if r.get("from") == "73474895")
    if kind == "internal_keepClear":
        lane = _internal_path(proposed, row)[0]
        edge = lane.get("id").rsplit("_", 1)[0]
        next(r for r in proposed.findall("connection") if r.get("from") == edge and r.get("fromLane") == lane.get("index")).set("keepClear", "false")
    elif kind in {"keepClear", "pass"}:
        row.set(kind, "false" if kind == "keepClear" else "true")
    else:
        lane = _internal_path(proposed, row)[0]
        if kind == "internal_access":
            lane.attrib.pop("disallow", None)
        else:
            lane.set("changeLeft" if kind == "LC" else "width", "none" if kind == "LC" else ".1")
    checks, _ = _verify_candidate(source, proposed, proposed)
    assert not checks[gate]
