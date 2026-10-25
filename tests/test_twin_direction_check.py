"""Direction consistency against the edge angles netconvert derives SUMO dir from (M3)."""

from __future__ import annotations

import hashlib
from pathlib import Path
import xml.etree.ElementTree as ET

import pytest

from astra_sumo.corridor.enums import TrafficSide
from astra_sumo.corridor.synthetic_networks import FIXTURES, apply_synthetic_mutation, build_synthetic_fixture
from astra_sumo.twin.direction_check import (RULE, check_connection_directions, direction_baseline,
                                             new_direction_findings)

RING1 = (Path(__file__).resolve().parents[1]
         / "benchmarks/hamburg_ring1_119_control_diagnosis_20261022_v1/nominal-inputs/network.net.xml")


def _net(connections: str, *, extra: str = "", edge_shapes: dict[str, str] | None = None) -> ET.Element:
    """Node C at the origin; W, E, S and N arms 100 m away; lanes cut 5 m before C."""
    shapes = edge_shapes or {}
    points = {"W": (-100, 0), "E": (100, 0), "S": (0, -100), "N": (0, 100)}
    edges = []
    for arm, (x, y) in points.items():
        ux, uy = x / 100, y / 100
        for name, start, end in ((f"{arm}_in", (x, y), (5 * ux, 5 * uy)), (f"{arm}_out", (5 * ux, 5 * uy), (x, y))):
            shape = f' shape="{shapes[name]}"' if name in shapes else ""
            ends = (arm, "C") if name.endswith("_in") else ("C", arm)
            edges.append(f'<edge id="{name}" from="{ends[0]}" to="{ends[1]}"{shape}><lane id="{name}_0" index="0" '
                         f'length="95" shape="{start[0]},{start[1]} {end[0]},{end[1]}"/></edge>')
    junctions = '<junction id="C" type="priority" x="0" y="0"/>' + "".join(
        f'<junction id="{arm}" type="dead_end" x="{x}" y="{y}"/>' for arm, (x, y) in points.items())
    return ET.fromstring(f"<net>{''.join(edges)}{junctions}{connections}{extra}</net>")


def _connection(start: str, end: str, label: str) -> str:
    return f'<connection from="{start}" to="{end}" fromLane="0" toLane="0" dir="{label}"/>'


@pytest.mark.parametrize("fixture", sorted(FIXTURES))
@pytest.mark.parametrize("side", [TrafficSide.RIGHT, TrafficSide.LEFT])
def test_clean_synthetic_fixtures_have_no_direction_findings(fixture, side):
    report = check_connection_directions(build_synthetic_fixture(fixture, traffic_side=side))
    assert report["rule"] == RULE and report["checked_count"] > 0
    assert report["findings"] == []


def test_x4_wrong_turn_is_found_at_its_junction():
    clean = build_synthetic_fixture("standard-x4", traffic_side=TrafficSide.RIGHT)
    report = check_connection_directions(apply_synthetic_mutation(clean, "wrong-turn"))
    assert [(row["id"], row["junction_id"], row["dir"], row["geometry_turn"], row["turn_angle_deg"])
            for row in report["findings"]] == [("dir:W_in_0>S_out_0", "J0", "l", "r", -90.0)]
    baseline = direction_baseline(check_connection_directions(clean), "0" * 64)
    assert [row["id"] for row in new_direction_findings(baseline, report)] == ["dir:W_in_0>S_out_0"]


def test_labels_compare_by_side_and_ambiguous_geometry_is_never_flagged():
    root = _net(_connection("W_in", "E_out", "R") + _connection("W_in", "S_out", "r")
                + _connection("W_in", "N_out", "L") + _connection("S_in", "N_out", "l")
                + _connection("E_in", "W_out", "s"),
                edge_shapes={"E_in": "100,0 30,0 0,0", "W_out": "0,0 -40,-35 -100,-35"})
    report = check_connection_directions(root)
    # W->E straight: a partial right is legal; S->N straight labelled l contradicts; E->W bends 41 deg: ambiguous.
    assert [row["id"] for row in report["findings"]] == ["dir:S_in_0>N_out_0"]
    assert [(row["id"], row["geometry_turn"]) for row in report["ambiguous"]] == [("dir:E_in_0>W_out_0", "ambiguous")]
    assert report["checked_count"] == 5


def test_edge_geometry_not_lane_terminal_segments_decides():
    # The lane cut runs backwards on a short edge, but netconvert's edge geometry still points into the node.
    root = _net(_connection("W_in", "E_out", "s"))
    lane = root.find("edge[@id='W_in']/lane")
    lane.set("shape", "-100,0 -4,0 -6,0.1")
    report = check_connection_directions(root)
    assert report["findings"] == [] and report["ambiguous"] == []


def test_sharp_rights_lefts_and_turnaround_geometry_follow_sumo_labels():
    root = _net(_connection("W_in", "W_out", "r") + _connection("S_in", "S_out", "l")
                + _connection("W_in", "E_out", "t") + _connection("W_in", "N_out", "t"),
                edge_shapes={"W_out": "0,0 -86.6,-50 -100,0", "S_out": "0,0 -50,-86.6 0,-100"})
    report = check_connection_directions(root)
    # A 150-degree right is still SUMO's r, a 150-degree left still l and a turnaround may look like a left turn;
    # only a turnaround label on straight geometry is a definite contradiction.
    assert {row["id"]: row["geometry_turn"] for row in report["ambiguous"] + report["findings"]} == {
        "dir:W_in_0>E_out_0": "s"}
    assert [row["reason"] for row in report["findings"]] == ["dir_t_contradicts_edge_angle_s"]
    assert report["checked_count"] == 4


def test_pedestrian_roundabout_and_unknown_labels_are_out_of_scope():
    walking = ('<edge id=":C_w0" function="walkingarea"><lane id=":C_w0_0" index="0" length="1" '
               'shape="5,5 6,6 5,7 5,5"/></edge>')
    root = _net(_connection("W_in", ":C_w0", "s") + _connection("W_in", "E_out", "invalid")
                + _connection("S_in", "E_out", "s"),
                extra=walking + '<roundabout nodes="C" edges="S_in E_out"/>')
    report = check_connection_directions(root)
    assert report["checked_count"] == 0 and report["findings"] == []
    assert sorted(row["reason"] for row in report["unsupported"]) == ["dir_label_not_compared",
                                                                     "roundabout_links_are_straight_by_definition"]


def test_new_findings_are_differential_and_follow_declared_lane_remaps():
    source = check_connection_directions(_net(_connection("S_in", "N_out", "l")))
    baseline = direction_baseline(source, "a" * 64)
    assert baseline["finding_ids"] == ["dir:S_in_0>N_out_0"] and len(baseline["sha256"]) == 64
    same = check_connection_directions(_net(_connection("S_in", "N_out", "l")))
    assert new_direction_findings(baseline, same) == []
    moved = _net(_connection("S_in", "N_out", "l").replace('fromLane="0"', 'fromLane="1"'))
    moved.find("edge[@id='S_in']").append(
        ET.fromstring('<lane id="S_in_1" index="1" length="95" shape="1,-100 1,-5"/>'))
    candidate = check_connection_directions(moved)
    assert [row["id"] for row in new_direction_findings(baseline, candidate)] == ["dir:S_in_1>N_out_0"]
    assert new_direction_findings(baseline, candidate, {"S_in": {"0": "1"}}) == []
    with pytest.raises(ValueError, match="same rule"):
        new_direction_findings({**baseline, "rule": "lane_terminal_segment"}, candidate)


def test_a_changed_contradicting_label_on_an_already_flagged_connection_is_new():
    # Review finding: the identity was the connection alone, so relabelling a flagged connection passed INV-DIR.
    source = check_connection_directions(_net(_connection("S_in", "N_out", "l")))
    baseline = direction_baseline(source, "a" * 64)
    assert baseline["finding_labels"] == {"dir:S_in_0>N_out_0": ["l", "s"]}
    relabelled = check_connection_directions(_net(_connection("S_in", "N_out", "r")))
    assert [(row["id"], row["dir"]) for row in new_direction_findings(baseline, relabelled)] == [
        ("dir:S_in_0>N_out_0", "r")]
    assert new_direction_findings(baseline, source) == []


def test_clean_ring1_nominal_baseline_is_frozen_with_zero_findings():
    raw = RING1.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    assert digest == "cc04537494708721e1ad87409ab47cef249c3774dbaf9ed3ec7a7b01710672a4"
    report = check_connection_directions(ET.fromstring(raw))
    assert (report["checked_count"], report["finding_count"], report["ambiguous_count"]) == (963, 0, 25)
    baseline = direction_baseline(report, digest)
    # The frozen payload holds the rule, the net sha256, the finding ids and their [dir, geometry turn] labels.
    assert baseline["sha256"] == "0319978d4c02f9c75a07afc6c3890110c867214a289e6faedc632d29cafabc87"
    # The former terminal-segment example (network.net.xml:14471) agrees with SUMO's edge angle.
    assert not any(row["id"] == "dir:-1332144622_0>51214742#1_0" for row in report["ambiguous"] + report["findings"])
