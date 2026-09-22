"""Equal-width approaches preserve physical lane order under a lateral map offset."""

from copy import deepcopy
import math

import pytest

from torii_sumo.core.hamburg_aerial_corridor_candidate import _bind_official_lanes
from torii_sumo.core import hamburg_aerial_corridor_candidate as candidate_module


def _case(role="ingress"):
    lanes = [
        {"lane_id": name, "lane_type": "vehicle", f"{role}_approach": "1", "shape_network": [(-20, y), (0, y)]}
        for name, y in (("right", -2.4), ("left", 0.8))
    ]
    movements = [
        {f"{role}_lane_id": lane["lane_id"], "selected_shape_network": [(-5, 0), (5, 0)]}
        for lane in lanes
    ]
    candidates = {("road", 0): [(-20, 0), (0, 0)], ("road", 1): [(-20, 3.2), (0, 3.2)]}
    return lanes, movements, candidates


def _bind(lanes, movements, candidates, role="ingress"):
    return _bind_official_lanes(lanes, movements, candidates, role=role, max_error_m=10, margin_m=0.5)


@pytest.mark.parametrize("role", ["ingress", "egress"])
def test_shifted_equal_lane_counts_keep_physical_order(role):
    result = _bind(*_case(role), role)
    assert result["bindings"] == {"right": ("road", 0), "left": ("road", 1)}
    assert not result["ambiguous"]
    assert all(row["reason"] == "ordered_same_approach_lane_fit" for row in result["records"])


@pytest.mark.parametrize("fault", ["no_approach", "different_approaches", "different_counts", "different_roads", "competing_road", "reverse", "crossing", "too_far"])
def test_ordered_matching_does_not_force_an_unproven_approach(fault):
    lanes, movements, candidates = deepcopy(_case())
    if fault == "no_approach":
        for lane in lanes:
            lane.pop("ingress_approach")
    elif fault == "different_approaches":
        lanes[1]["ingress_approach"] = "2"
    elif fault == "different_counts":
        candidates[("road", 2)] = [(-20, 6.4), (0, 6.4)]
    elif fault == "different_roads":
        candidates[("other", 1)] = candidates.pop(("road", 1))
    elif fault == "competing_road":
        candidates.update({("other", index): shape for (_, index), shape in list(candidates.items())})
    elif fault == "reverse":
        candidates = {key: list(reversed(shape)) for key, shape in candidates.items()}
    elif fault == "crossing":
        lanes[0]["shape_network"] = [(-20, -2.4), (0, 0.8)]
        lanes[1]["shape_network"] = [(-20, 0.8), (0, -2.4)]
    elif fault == "too_far":
        for lane in lanes:
            lane["shape_network"] = [(x, y - 20) for x, y in lane["shape_network"]]
    result = _bind(lanes, movements, candidates)
    assert len(result["bindings"]) < 2
    assert result["ambiguous"]


def test_joint_matching_rejects_competing_official_approaches():
    lanes, movements, candidates = _case()
    for lane in deepcopy(lanes):
        lane["lane_id"] += "-other"
        lane["ingress_approach"] = "2"
        lanes.append(lane)
        movements.append({"ingress_lane_id": lane["lane_id"], "selected_shape_network": [(-5, 0), (5, 0)]})
    result = _bind(lanes, movements, candidates)
    assert result["bindings"] == {}
    assert not result["ordered_groups"]


@pytest.mark.parametrize("role", ["ingress", "egress"])
@pytest.mark.parametrize("competing_offset", [-3.85, -0.9])
def test_missing_third_lane_does_not_discard_a_mutually_unique_existing_lane(role, competing_offset):
    # LSA118 has three official exits and two OSM exits. Two official lanes
    # select the same OSM lane, but one fits 3 m better. Keep that clear match,
    # not both claims; a near tie remains unresolved without a new tolerance.
    lanes = [{"lane_id": name, "lane_type": "vehicle", f"{role}_approach": "1",
              "shape_network": [(0, y), (20, y)]}
             for name, y in (("28", 4.067), ("29", 0.866), ("30", competing_offset))]
    movements = [{f"{role}_lane_id": row["lane_id"], "selected_shape_network": [(0, 0), (20, 0)]} for row in lanes]
    candidates = {("road", 0): [(0, 0), (20, 0)], ("road", 1): [(0, 3.2), (20, 3.2)]}
    result = _bind(lanes, movements, candidates, role)
    expected = {"28": ("road", 1)}
    if competing_offset == -3.85:
        expected["29"] = ("road", 0)
    assert result["bindings"] == expected
    assert "30" not in result["bindings"]
    assert result["ambiguous"]


def test_other_intersection_part_does_not_inflate_current_approach_lane_count():
    lanes, movements, candidates = _case()
    lanes.append({"lane_id": "other-part", "lane_type": "vehicle", "ingress_approach": "1", "shape_network": [(80, 40), (100, 40)]})
    result = _bind(lanes, movements, candidates)
    assert result["bindings"] == {"right": ("road", 0), "left": ("road", 1)}


@pytest.mark.parametrize("role", ["ingress", "egress"])
def test_lane_direction_uses_official_boundary_not_first_movement_trace(role):
    # KML lane centerlines may be stored B-to-A regardless of driving direction.
    lanes = [{"lane_id": "official", "lane_type": "vehicle", "shape_network": [(0, 0), (20, 0)], "junction_endpoint_network": (0, 0)}]
    bad = {f"{role}_lane_id": "official", "selected_shape_network": [(0, 10), (0, 0)]}
    good = {f"{role}_lane_id": "official", "selected_shape_network": [(10, 0), (0, 0)] if role == "ingress" else [(0, 0), (10, 0)]}
    candidates = {("road", 0): [(20, 0), (0, 0)] if role == "ingress" else [(0, 0), (20, 0)]}
    for movements in ([bad, good], [good, bad]):
        assert _bind(lanes, movements, candidates, role)["bindings"] == {"official": ("road", 0)}


def test_constructed_official_lane_identity_survives_a_taper_distance_tie():
    result = {"bindings": {}, "records": [{"lane_id": "14", "reason": "lane_match_ambiguous"}],
              "ambiguous": [{"lane_id": "14", "reason": "lane_match_ambiguous"}]}
    candidate_module._keep_constructed_lane_identity(result, {"14": ("road", 2)}, {("road", 2): [(0, 0), (10, 0)]})
    assert result["bindings"] == {"14": ("road", 2)}
    assert result["ambiguous"] == []
    assert result["records"][0]["reason"] == "constructed_official_lane_identity"
    with pytest.raises(ValueError, match="constructed lane"):
        candidate_module._keep_constructed_lane_identity(result, {"14": ("unknown", 2)}, {})


@pytest.mark.parametrize("fault", [None, "duplicate_source", "duplicate_official", "new_pocket", "changed_without_origin", "duplicate_origin", "occupied", "incompatible", "too_far", "internal", "other_role", "already_bound"])
def test_preserved_cut_lane_identity_does_not_override_unproved_or_conflicting_lanes(fault):
    result = {"bindings": {}, "records": [{"lane_id": "15", "reason": "lane_match_ambiguous", "candidates": [
        {"edge": "road", "lane": 0, "mean_error_m": 1.45}, {"edge": "road", "lane": 1, "mean_error_m": 1.75}]}],
        "ambiguous": [{"lane_id": "15", "reason": "lane_match_ambiguous"}]}
    ports = [{"lane_id": "15", "role": "egress", "source_lane_id": "road_0"}]
    group = {"boundary_port_classification": {"official_external_ports": ports}}
    available = {("road", i): [(0, 3.2 * i), (20, 3.2 * i)] for i in (0, 1)}
    origins, changed = {}, []
    if fault == "duplicate_source":
        ports.append({**ports[0], "lane_id": "16"})
    elif fault == "duplicate_official":
        ports.append({**ports[0], "source_lane_id": "road_1"})
    elif fault == "new_pocket":
        origins["road_0"] = None
    elif fault == "changed_without_origin":
        changed.append("road")
    elif fault == "duplicate_origin":
        origins["road_1"] = "road_0"
    elif fault == "occupied":
        result["bindings"]["other"] = ("road", 0)
    elif fault == "incompatible":
        result["records"][0]["candidates"].pop(0)
    elif fault == "too_far":
        result["records"][0]["candidates"][0]["mean_error_m"] = 11
    elif fault == "internal":
        group["boundary_port_classification"] = {"official_internal_anchors": ports}
    elif fault == "other_role":
        ports[0]["role"] = "ingress"
    elif fault == "already_bound":
        result["bindings"]["15"] = ("road", 1)
    before = dict(result["bindings"])
    candidate_module._keep_boundary_lane_identity(result, group, available, role="egress", lane_origins=origins,
        changed_edge_ids=changed, maximum_error_m=10)
    assert result["bindings"] == ({"15": ("road", 0)} if fault is None else before)
    if fault is None:
        assert result["ambiguous"] == []
        assert result["records"][0]["reason"] == "preserved_source_boundary_lane_identity"


def test_preserved_cut_identity_can_follow_an_explicit_reindexed_lane_origin():
    available = {("new-road", 1): [(0, 0), (20, 0)]}
    result = {"bindings": {}, "records": [{"lane_id": "15", "reason": "lane_match_ambiguous",
        "candidates": [{"edge": "new-road", "lane": 1, "mean_error_m": 1.45}]}], "ambiguous": [{"lane_id": "15"}]}
    group = {"boundary_port_classification": {"official_external_ports": [{"lane_id": "15", "role": "egress", "source_lane_id": "old-road_0"}]}}
    candidate_module._keep_boundary_lane_identity(result, group, available, role="egress",
        lane_origins={"new-road_1": "old-road_0"}, changed_edge_ids=["new-road"], maximum_error_m=10)
    assert result["bindings"] == {"15": ("new-road", 1)}
    assert result["records"][0]["source_boundary_identity"]["basis"] == "explicit_lane_origin"


def test_bus_movement_does_not_match_a_nearer_delivery_only_exit():
    lanes = [
        {"lane_id": "bus-in", "lane_type": "vehicle", "allowed_vehicle_classes": ["bus"], "shape_network": [(-10, 0), (0, 0)]},
        {"lane_id": "out", "lane_type": "vehicle", "shape_network": [(0, 0), (10, 0)]},
    ]
    movements = [{"ingress_lane_id": "bus-in", "egress_lane_id": "out", "selected_shape_network": [(-10, 0), (10, 0)]}]
    candidates = {("service", 0): [(0, 0), (10, 0)], ("road", 0): [(0, 3.2), (10, 3.2)]}
    result = _bind_official_lanes(lanes, movements, candidates, role="egress", max_error_m=10, margin_m=.5,
        candidate_modes={("service", 0): {"delivery"}, ("road", 0): {"bus", "passenger"}})
    assert result["bindings"] == {"out": ("road", 0)}


@pytest.mark.parametrize("node_id,lane_ids,official,candidates,direction", [
    ("2394", ("6", "7"), [
        [(474.430843, 313.888358), (484.382598, 288.001667), (500.113327, 248.865778), (501.246320, 246.044263), (498.420167, 239.606804), (499.414786, 236.727719)],
        [(471.299567, 312.687409), (481.244526, 286.811744), (488.046735, 269.571137), (499.428039, 236.727904), (514.374814, 209.598127), (528.308699, 182.398595), (547.540767, 138.438248)],
    ], {("381540198#0", 0): [(487.16, 274.09), (472.47, 312.48)], ("381540198#0", 1): [(484.17, 272.95), (469.48, 311.33)]}, (-0.36, 0.93)),
    ("2349", ("2", "3"), [
        [(360.984842, 315.553525), (404.406895, 323.236132), (423.174864, 327.581710), (433.514478, 330.374286)],
        [(361.462720, 312.177623), (389.306010, 316.838826), (426.624004, 325.003928), (428.758201, 325.467678), (431.394934, 329.810598), (433.507696, 330.385319)],
    ], {("554713076#0", 0): [(392.24, 323.65), (392.04, 323.61)], ("554713076#0", 1): [(392.74, 320.49), (392.54, 320.45)]}, ((-0.654552, 0.238808), (-57.663677, -9.650235))),
])
def test_real_sandtorkai_same_count_lane_offsets(node_id, lane_ids, official, candidates, direction):
    # Coordinates transcribed from the frozen Hamburg MAP plans and combined-v2 joined network.
    lanes = [{"lane_id": lane_id, "lane_type": "vehicle", "ingress_approach": node_id, "shape_network": shape} for lane_id, shape in zip(lane_ids, official)]
    directions = direction if isinstance(direction[0], tuple) else (direction, direction)
    movements = [{"ingress_lane_id": lane_id, "selected_shape_network": [(0, 0), tangent]} for lane_id, tangent in zip(lane_ids, directions)]
    result = _bind(lanes, movements, candidates)
    edge_id = next(iter(candidates))[0]
    assert result["bindings"] == {lane_ids[0]: (edge_id, 0), lane_ids[1]: (edge_id, 1)}
    assert not result["ambiguous"]


def test_real_lsa431_full_exit_shape_rejects_the_near_parking_aisle_without_mode_filter():
    # Frozen official MAP lane 9 and iteration-01 SUMO lane shapes. Both
    # candidates start near B, but the parking aisle then leaves the road arm.
    official = [(423.060074, 221.494472), (431.210831, 221.267090), (439.613557, 221.020759)]
    parking = [(423.07, 221.72), (423.93, 221.15), (427.87, 217.75),
               (430.83, 214.24), (434.08, 212.02), (437.23, 210.73), (438.39, 210.65)]
    road = [(421.57, 220.41), (445.48, 219.92)]
    lanes = [{"lane_id": "9", "lane_type": "vehicle", "shape_network": official,
              "junction_endpoint_network": official[0]}]
    movements = [{"egress_lane_id": "9", "selected_shape_network": official}]
    result = _bind(lanes, movements, {("32130731", 0): parking, ("1359821920", 0): road}, "egress")
    assert result["bindings"] == {"9": ("1359821920", 0)}
    assert candidate_module._lane_overlap_error(official, parking) > candidate_module._lane_overlap_error(official, road) + 0.5


def test_lane_error_covers_a_diverging_tail_and_is_invariant_to_added_vertices():
    official = [(0, 0), (10, 0)]
    candidate = [(0, 0), (1, 0), (10, 10)]
    subdivided = [(0, 0), (0.01, 0), (1, 0), (1.9, 1), (5.5, 5), (10, 10)]
    assert candidate_module._lane_overlap_error(official, candidate) == pytest.approx(4.5)
    assert candidate_module._lane_overlap_error(official, subdivided) == pytest.approx(4.5)


@pytest.mark.parametrize("reverse_official,reverse_candidate", [(False, False), (True, False), (False, True), (True, True)])
def test_lane_error_ignores_geometry_outside_the_shared_longitudinal_interval(reverse_official, reverse_candidate):
    official = [(0, 0), (20, 0)]
    longer = [(-100, 60), (-5, 2), (25, 2), (100, -60)]
    if reverse_official:
        official.reverse()
    if reverse_candidate:
        longer.reverse()
    assert candidate_module._lane_overlap_error(official, longer) == pytest.approx(2)
    assert candidate_module._lane_overlap_error(longer, official) < 3


@pytest.mark.parametrize("candidate", [
    [(10, 0), (20, 0)],  # Only one common endpoint, not a shared interval.
    [(20, 0), (30, 0)],
    [(0, 0), (8, 0), (2, 3), (10, 3)],  # Two branches in a common section.
    [(0, 0), (5, 0), (5, 3), (10, 3)],  # Perpendicular segment in the shared interval.
])
def test_lane_error_rejects_missing_or_nonunique_shared_sections(candidate):
    assert math.isinf(candidate_module._lane_overlap_error([(0, 0), (10, 0)], candidate))
