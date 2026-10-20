"""Prevent distant, opposite-direction, and wrong-mode lanes from looking calibrated."""

import xml.etree.ElementTree as ET

import pytest
from shapely.geometry import LineString

from astra_sumo.core import network_calibration as calibration
from astra_sumo.core.lane_correspondence import assign_nonmotor_lanes, diagnose_lane_match
from test_network_calibration import _case


def _lane(key, points, *, allow="passenger", function="", width="3.2"):
    edge = ET.Element("edge", id=key.rsplit("_", 1)[0])
    if function:
        edge.set("function", function)
    lane = ET.SubElement(edge, "lane", id=key, allow=allow, width=width)
    return edge, lane, LineString(points)


def test_distance_bands_do_not_call_a_distant_parallel_line_covered():
    metrics = calibration.centerline_overlap_metrics(LineString([[0, 0], [10, 0]]), [[[0, 5], [10, 5]]], 0.25, direction=1)
    assert metrics["reference_coverage"] == 1
    assert metrics["mean_lateral_offset_m"] == 5
    assert metrics["coverage_within_m"] == {"1.0": 0, "2.0": 0}


def test_reverse_direction_is_visible_and_excluded_from_coverage():
    metrics = calibration.centerline_overlap_metrics(LineString([[0, 0], [10, 0]]), [[[10, 0], [0, 0]]], 0.25, direction=1)
    assert metrics["reference_coverage"] == 0
    assert metrics["mean_lateral_offset_m"] is None
    assert metrics["mean_direction_cosine"] == -1


def test_stop_line_splits_signed_longitudinal_and_lateral_errors(tmp_path):
    net, facts, output = _case(tmp_path, [{"kind": "stop_line", "selector": {"lane_id": "in_0"}, "expected": [6, -3]}])
    report = calibration.evaluate_network_calibration(net_file=net, facts_file=facts, output_file=output)
    metrics = report["facts"][0]["metrics"]
    assert metrics == {"offset_m": 5, "longitudinal_offset_m": 4, "lateral_offset_m": 3}


def test_missing_lateral_value_does_not_increment_measured_facts(tmp_path):
    net, facts, output = _case(tmp_path, [{"kind": "lane_centerline", "selector": {"lane_id": "in_0"},
        "expected": [[0, 0], [0, 10]], "sample_spacing_m": 0.5}])
    report = calibration.evaluate_network_calibration(net_file=net, facts_file=facts, output_file=output)
    assert report["status"] == "measured"
    assert report["layers"]["geometry"]["measured_facts"] == 0
    assert report["facts"][0]["reason"] == "no_directed_longitudinal_overlap"


def test_programming_errors_do_not_become_unknown_reference_facts(tmp_path, monkeypatch):
    net, facts, output = _case(tmp_path, [{"kind": "lane_count", "selector": {}, "expected": 1}])
    def broken(*args):
        raise TypeError("programming defect")
    monkeypatch.setattr(calibration, "_measure", broken)
    with pytest.raises(TypeError, match="programming defect"):
        calibration.evaluate_network_calibration(net_file=net, facts_file=facts, output_file=output)
    assert not output.exists()


def test_nonmotor_assignment_uses_distinct_crossings_and_excludes_sidewalks_and_shared_motor_lanes():
    official = {"p1": {"mode": "pedestrian", "points": [[0, 0], [1, 0]]},
                "p2": {"mode": "pedestrian", "points": [[0, 0], [1, 0]]},
                "b": {"mode": "bicycle", "points": [[0, 0], [10, 0]], "direction": 1}}
    lanes = {"walk_0": _lane("walk_0", [[0, 0], [1, 0]], allow="pedestrian"),
             "cross_0": _lane("cross_0", [[0, 0.2], [1, 0.2]], allow="pedestrian", function="crossing"),
             "mixed_0": _lane("mixed_0", [[0, 0], [10, 0]], allow="passenger bicycle"),
             "bike_0": _lane("bike_0", [[0, 1], [10, 1]], allow="bicycle")}
    assigned, diagnostics = assign_nonmotor_lanes(official, lanes, {})
    assert len(assigned) == 2
    assert set(assigned.values()) == {"cross_0", "bike_0"}
    assert diagnostics["p1"]["candidate_class"] == "crossing"


def test_partial_cycle_segment_does_not_turn_missing_length_into_lateral_error():
    official = {"bike": {"mode": "bicycle", "points": [[0, 0], [100, 0]], "direction": 1}}
    lanes = {"part_0": _lane("part_0", [[0, 1], [10, 1]], allow="bicycle")}
    assigned, diagnostics = assign_nonmotor_lanes(official, lanes, {})
    assert assigned == {"bike": "part_0"}
    assert diagnostics["bike"]["nearest_candidate_distance_m"] == 1
    assert diagnostics["bike"]["candidate_reference_coverages"]["part_0"] == pytest.approx(.1)


def test_nonmotor_fit_prefers_coverage_and_excludes_opposite_or_crossing_directions():
    official = {"bike": {"mode": "bicycle", "points": [[0, 0], [100, 0]], "direction": 1}}
    lanes = {"short_0": _lane("short_0", [[0, 1], [2, 1]], allow="bicycle"),
             "full_0": _lane("full_0", [[0, 1], [100, 1]], allow="bicycle"),
             "reverse_0": _lane("reverse_0", [[100, 0], [0, 0]], allow="bicycle"),
             "cross_0": _lane("cross_0", [[50, -50], [50, 50]], allow="bicycle")}
    assigned, _ = assign_nonmotor_lanes(official, lanes, {})
    assert assigned == {"bike": "full_0"}
    assigned, _ = assign_nonmotor_lanes(official, {key: value for key, value in lanes.items() if key in {"reverse_0", "cross_0"}}, {})
    assert assigned == {}


def test_better_neighbor_is_a_conflict_without_silent_rebinding():
    network = {"road_1": _lane("road_1", [[0, 0.3], [10, 0.3]]),
               "road_2": _lane("road_2", [[0, 3], [10, 3]])}
    confidence, alternatives, diagnostics = diagnose_lane_match(parts=["road_2"], reference=LineString([[0, 0], [10, 0]]),
        network_lanes=network, spacing=0.25, direction=1)
    assert confidence == "conflict" and diagnostics["conflicting_alternative"]
    assert alternatives[0]["lane_ids"] == ["road_1"]
    assert alternatives[0]["metrics"]["mean_lateral_offset_m"] == pytest.approx(0.3)


@pytest.mark.parametrize("inside", [True, False])
def test_compound_movement_uses_the_internal_road_and_never_an_outside_detour(tmp_path, inside):
    net, facts, output = _case(tmp_path, [{"kind": "movement_inventory",
        "selector": {"incoming_lane_ids": ["in_0"], "outgoing_lane_ids": ["out_0"],
                     "junction_ids": ["j1", "j2"], "vehicle_class": "passenger"},
        "expected": [["in_0", "out_0"]]}])
    root = ET.Element("net")
    for key, start, end in (("in", "a", "j1"), ("middle", "j1", "j2"), ("out", "j2", "b"), ("detour", "j1", "outside")):
        edge = ET.SubElement(root, "edge", id=key, **{"from": start, "to": end})
        ET.SubElement(edge, "lane", id=f"{key}_0", index="0", allow="passenger")
    for start, end in [("in", "middle"), ("in", "detour"), ("detour", "out")] + ([("middle", "out")] if inside else []):
        ET.SubElement(root, "connection", **{"from": start, "to": end, "fromLane": "0", "toLane": "0"})
    ET.ElementTree(root).write(net, encoding="utf-8")
    report = calibration.evaluate_network_calibration(net_file=net, facts_file=facts, output_file=output)
    row = report["facts"][0]
    assert row["status"] == ("matched" if inside else "mismatched")
    assert row["metrics"]["false_negative"] == (0 if inside else 1)


def test_frozen_ordinary_lane_correspondence_resolves_new_internal_ids(tmp_path):
    net, facts, output = _case(tmp_path, [{"kind": "lane_centerline",
        "selector": {"lane_ids": ["a_0", "b_0"], "include_internal_paths": True},
        "expected": [[0, 0], [10, 0]], "sample_spacing_m": 0.25, "tolerance_m": 0}])
    root = ET.fromstring('''<net><edge id="a" from="w" to="j"><lane id="a_0" index="0" shape="0,0 1,0"/></edge>
      <edge id=":j_99" function="internal"><lane id=":j_99_0" index="0" shape="1,0 9,0"/></edge>
      <edge id="b" from="j" to="e"><lane id="b_0" index="0" shape="9,0 10,0"/></edge>
      <junction id="j"/><connection from="a" fromLane="0" to="b" toLane="0" via=":j_99_0"/>
      <connection from=":j_99" fromLane="0" to="b" toLane="0"/></net>''')
    ET.ElementTree(root).write(net, encoding="utf-8")
    report = calibration.evaluate_network_calibration(net_file=net, facts_file=facts, output_file=output)
    row = report["facts"][0]
    assert row["status"] == "matched"
    assert row["resolved_lane_ids"] == ["a_0", ":j_99_0", "b_0"]
    assert row["metrics"]["coverage_within_m"]["1.0"] == 1


@pytest.mark.parametrize("mode", ["explore", "release"])
def test_missing_reference_part_does_not_hide_retained_geometry_or_bridge_the_gap(tmp_path, mode):
    net, facts, output = _case(tmp_path, [{"kind": "lane_centerline",
        "selector": {"lane_ids": ["a_0", "removed_0", "b_0"], "include_internal_paths": True},
        "expected": [[0, 0], [10, 0]], "sample_spacing_m": 0.25}])
    root = ET.fromstring('''<net><edge id="a" from="w" to="j"><lane id="a_0" index="0" shape="0,0 1,0"/></edge>
      <edge id=":j_99" function="internal"><lane id=":j_99_0" index="0" shape="1,0 9,0"/></edge>
      <edge id="b" from="j" to="e"><lane id="b_0" index="0" shape="9,0 10,0"/></edge>
      <junction id="j"/><connection from="a" fromLane="0" to="b" toLane="0" via=":j_99_0"/>
      <connection from=":j_99" fromLane="0" to="b" toLane="0"/></net>''')
    ET.ElementTree(root).write(net, encoding="utf-8")
    report = calibration.evaluate_network_calibration(net_file=net, facts_file=facts, output_file=output, mode=mode)
    row = report["facts"][0]
    assert row["missing_lane_ids"] == ["removed_0"]
    if mode == "release":
        assert row["status"] == "mismatched" and row["metrics"] == {}
    else:
        assert row["status"] == "measured" and row["reason"] == "network_lane_parts_missing"
        assert row["resolved_lane_ids"] == ["a_0", "b_0"]
        assert row["metrics"]["reference_coverage"] == pytest.approx(.2)
        assert row["metrics"]["mean_lateral_offset_m"] == 0


def test_all_missing_tentative_parts_report_zero_coverage_without_inventing_lateral_error(tmp_path):
    net, facts, output = _case(tmp_path, [{"kind": "lane_centerline", "selector": {"lane_ids": ["removed_0"]},
        "mapping_status": "unresolved", "expected": [[0, 0], [10, 0]], "sample_spacing_m": .25}])
    report = calibration.evaluate_network_calibration(net_file=net, facts_file=facts, output_file=output, mode="explore")
    row = report["facts"][0]
    assert row["metrics"]["reference_coverage"] == 0
    assert row["metrics"]["uncovered_reference_length_m"] == 10
    assert row["metrics"]["mean_lateral_offset_m"] is None
    assert report["layers"]["geometry"]["coverage_measured_facts"] == 1
    assert report["layers"]["geometry"]["measured_facts"] == 0
